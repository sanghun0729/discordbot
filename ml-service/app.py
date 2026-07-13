"""
로컬 ML 사이드카 — STT(faster-whisper) + 번역(CTranslate2 + NLLB) + TTS(piper).

전부 로컬에서 무료로 동작한다. Node 봇이 WAV 오디오를 POST /process 로 보내면
{ source_text, source_lang, translated_text, target_lang, audio_b64 } 를 돌려준다.

번역 엔진은 CTranslate2("ctrans"). 모델 라우팅:
  - en→ko : NHNDQ/nllb-finetuned-en2ko  (한영 전용 파인튜닝, 고품질)
  - ko→en : NHNDQ/nllb-finetuned-ko2en  (한영 전용 파인튜닝, 고품질)
  - 그 외 : NLLB-200 distilled (범용, 선택 설치)
모델은 먼저 setup_translation.sh 로 CTranslate2 포맷으로 변환해 두어야 한다.

실행:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

import base64
import io
import json
import os
import re
import subprocess
import tempfile
import time

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse

from faster_whisper import WhisperModel
import ctranslate2
from transformers import AutoTokenizer

app = FastAPI(title="Discord 번역 봇 ML 서비스")
HERE = os.path.dirname(__file__)

# ---------------------------------------------------------------------------
# 설정 (환경변수로 조정)
# ---------------------------------------------------------------------------
# GPU 기본값(cuda/float16). CPU만 있으면 ML_DEVICE=cpu, ML_COMPUTE=int8 로 덮어쓸 것.
DEVICE = os.environ.get("ML_DEVICE", "cuda")
COMPUTE = os.environ.get("ML_COMPUTE", "float16")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")

# STT 백엔드: "whisper"(정확) | "sensevoice"(빠름/CPU) | "hybrid"(SenseVoice 기본 + Whisper 폴백).
STT_BACKEND = os.environ.get("STT_BACKEND", "whisper")
SENSEVOICE_DIR = os.environ.get(
    "SENSEVOICE_DIR",
    os.path.join(HERE, "models", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"),
)
SENSEVOICE_THREADS = int(os.environ.get("SENSEVOICE_THREADS", "8"))
# hybrid에서 한국어 발화는 SenseVoice가 약하므로 Whisper로 재확인(정확도↑). 1=켜짐.
STT_KO_VERIFY = os.environ.get("STT_KO_VERIFY", "1") == "1"

# 한↔영 전용 파인튜닝 모델 (우선 사용) + 그 외 언어용 범용 모델(선택).
NLLB_EN2KO_DIR = os.environ.get("NLLB_EN2KO_DIR", os.path.join(HERE, "models", "nllb-finetuned-en2ko-ct2"))
NLLB_KO2EN_DIR = os.environ.get("NLLB_KO2EN_DIR", os.path.join(HERE, "models", "nllb-finetuned-ko2en-ct2"))
NLLB_MODEL_DIR = os.environ.get("NLLB_MODEL_DIR", os.path.join(HERE, "models", "nllb-200-distilled-1.3B-ct2"))
# NLLB 토크나이저는 모든 distilled 변형이 동일 → HF id 하나로 공유(최초 1회 다운로드 후 캐시).
NLLB_TOKENIZER = os.environ.get("NLLB_TOKENIZER", "facebook/nllb-200-distilled-600M")
NLLB_BEAM = int(os.environ.get("NLLB_BEAM", "4"))

# 번역 백엔드: "nllb"(기본, 빠름) 또는 "llm"(Ollama 로컬 LLM, 구어체·슬랭 강함).
# LLM 실패 시 자동으로 NLLB로 폴백한다.
TRANSLATE_BACKEND = os.environ.get("TRANSLATE_BACKEND", "nllb")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
# 모델을 VRAM에 상주시켜 콜드스타트 방지("-1"=무기한, "60m"=60분 유휴 후 해제).
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "60m")
LANG_NAME = {
    "ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese",
    "es": "Spanish", "fr": "French", "de": "German", "vi": "Vietnamese",
}

# TTS: edge-tts (Microsoft Edge 온라인 TTS, 무료·키 불필요).
# 언어별 목소리 선택 + 속도(rate) 조절 지원. 번역문 텍스트만 전송, MP3 수신.
TTS_RATE = os.environ.get("TTS_RATE", "+20%")  # 1.2배 ≈ +20%
EDGE_VOICES = {
    "ko": os.environ.get("TTS_VOICE_KO", "ko-KR-InJoonNeural"),
    "en": os.environ.get("TTS_VOICE_EN", "en-US-AriaNeural"),
    "ja": os.environ.get("TTS_VOICE_JA", "ja-JP-NanamiNeural"),
    "zh": os.environ.get("TTS_VOICE_ZH", "zh-CN-XiaoxiaoNeural"),
    "es": os.environ.get("TTS_VOICE_ES", "es-ES-AlvaroNeural"),
    "fr": os.environ.get("TTS_VOICE_FR", "fr-FR-DeniseNeural"),
    "de": os.environ.get("TTS_VOICE_DE", "de-DE-KillianNeural"),
    "vi": os.environ.get("TTS_VOICE_VI", "vi-VN-NamMinhNeural"),
}

# Whisper(ISO-639-1) -> NLLB(FLORES-200) 언어코드 매핑.
NLLB_CODES = {
    "ko": "kor_Hang",
    "en": "eng_Latn",
    "ja": "jpn_Jpan",
    "zh": "zho_Hans",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "vi": "vie_Latn",
}

# ---------------------------------------------------------------------------
# 모델 로딩
# ---------------------------------------------------------------------------
# CUDA를 요청했지만 실제로 사용 불가하면(드라이버 미초기화 등) CPU로 자동 폴백.
# → 재부팅 전에도 봇이 죽지 않고 CPU로 동작하고, GPU가 살아나면 자동으로 GPU 사용.
if DEVICE == "cuda":
    try:
        _cuda_ok = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        _cuda_ok = False
    if not _cuda_ok:
        _fb = os.environ.get("WHISPER_CPU_FALLBACK", "small")
        print(f"[ml] ⚠ CUDA 사용 불가 → CPU 폴백 (compute=int8, whisper={_fb})")
        DEVICE, COMPUTE, WHISPER_MODEL = "cpu", "int8", _fb

# CPU 추론 스레드 수 (0=자동/물리코어). CPU 모드에서 속도에 크게 영향.
CPU_THREADS = int(os.environ.get("CPU_THREADS", "0"))

print(f"[ml] Whisper 로딩: {WHISPER_MODEL} ({DEVICE}/{COMPUTE}, threads={CPU_THREADS or 'auto'}) ...")
_whisper = WhisperModel(
    WHISPER_MODEL, device=DEVICE, compute_type=COMPUTE, cpu_threads=CPU_THREADS
)
print("[ml] Whisper 준비 완료.")

# SenseVoice(CPU) — hybrid/sensevoice 모드에서 기본 STT로 사용.
_sv = None
if STT_BACKEND in ("sensevoice", "hybrid"):
    try:
        import sherpa_onnx

        _sv = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=os.path.join(SENSEVOICE_DIR, "model.int8.onnx"),
            tokens=os.path.join(SENSEVOICE_DIR, "tokens.txt"),
            num_threads=SENSEVOICE_THREADS,
            use_itn=True,
            debug=False,
        )
        print(f"[ml] SenseVoice(CPU) 로드 완료 (STT_BACKEND={STT_BACKEND})")
    except Exception as e:
        print(f"[ml] SenseVoice 로드 실패 → Whisper 단독 사용: {e}")
        _sv = None


def _load_translator(path, label):
    if path and os.path.isdir(path):
        print(f"[ml] 번역모델 로딩({label}): {path}")
        kwargs = {}
        if DEVICE == "cpu" and CPU_THREADS:
            kwargs["intra_threads"] = CPU_THREADS
        return ctranslate2.Translator(path, device=DEVICE, compute_type=COMPUTE, **kwargs)
    print(f"[ml] 번역모델 없음({label}): {path} — 건너뜀")
    return None


_en2ko = _load_translator(NLLB_EN2KO_DIR, "en→ko (finetuned)")
_ko2en = _load_translator(NLLB_KO2EN_DIR, "ko→en (finetuned)")
_general = _load_translator(NLLB_MODEL_DIR, "general")
_tokenizer = AutoTokenizer.from_pretrained(NLLB_TOKENIZER)
print("[ml] 번역 모델 준비 완료.")


print(f"[ml] TTS(edge-tts) 목소리: {EDGE_VOICES} | 속도: {TTS_RATE}")

# LLM 번역 백엔드면 시작 시 모델을 미리 VRAM에 올려(예열) 첫 요청 콜드스타트 방지.
if TRANSLATE_BACKEND == "llm":
    try:
        import urllib.request

        print(f"[ml] LLM({OLLAMA_MODEL}) 예열 중...")
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps(
                {"model": OLLAMA_MODEL, "prompt": "hi", "stream": False,
                 "keep_alive": OLLAMA_KEEP_ALIVE, "options": {"num_predict": 1}}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=120).read()
        print("[ml] LLM 예열 완료.")
    except Exception as e:
        print(f"[ml] LLM 예열 실패(무시): {e}")


# ---------------------------------------------------------------------------
# 번역 (CTranslate2 + NLLB, 한↔영 전용 모델 우선 라우팅)
# ---------------------------------------------------------------------------
def _select_translator(src_iso, tgt_iso):
    if src_iso == "en" and tgt_iso == "ko" and _en2ko:
        return _en2ko
    if src_iso == "ko" and tgt_iso == "en" and _ko2en:
        return _ko2en
    return _general  # 그 외 언어쌍 (미설치 시 None)


def _translate(text, src_iso, tgt_iso):
    if not text.strip():
        return text
    tgt = NLLB_CODES.get(tgt_iso)
    if not tgt:
        return text  # 목표 언어 미지원
    src = NLLB_CODES.get(src_iso, "eng_Latn")  # 감지 실패 시 영어로 가정
    if src == tgt:
        return text

    translator = _select_translator(src_iso, tgt_iso)
    if translator is None:
        return text  # 해당 언어쌍 모델 미설치 → 원문 반환

    _tokenizer.src_lang = src
    source = _tokenizer.convert_ids_to_tokens(_tokenizer.encode(text))
    results = translator.translate_batch(
        [source],
        target_prefix=[[tgt]],
        beam_size=NLLB_BEAM,
        repetition_penalty=1.3,      # 같은 표현 반복(루프) 억제
        no_repeat_ngram_size=3,      # 3-gram 반복 금지
        max_decoding_length=256,     # 폭주 방지
    )
    tokens = results[0].hypotheses[0]
    if tokens and tokens[0] == tgt:
        tokens = tokens[1:]  # 선두 언어 토큰 제거
    return _tokenizer.decode(
        _tokenizer.convert_tokens_to_ids(tokens), skip_special_tokens=True
    )


# ---------------------------------------------------------------------------
# 용어사전(glossary) — 자주 틀리는 단어/슬랭을 지정 번역으로 강제.
#   · 아래 기본값 + (있으면) ml-service/glossary.json 을 병합해서 사용.
#   · glossary.json 예: { "ko": { "my guys": "우리 애들", "commander": "지휘관" } }
#     → 코드 수정 없이 이 파일만 편집/재시작하면 새 용어가 반영된다.
#   · 바깥 키는 "번역 목표 언어" 코드(ko/en/…), 안쪽은 원문 표현→지정 번역.
# ---------------------------------------------------------------------------
def _load_glossary():
    base = {
        "ko": {
            "my guys": "우리 애들",
            "my guy": "우리 애",
            "commander": "지휘관",
            "command": "지휘관",
        },
    }
    path = os.path.join(os.path.dirname(__file__), "glossary.json")
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for lang, terms in (user or {}).items():
            base.setdefault(lang, {}).update(terms)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[ml] glossary.json 로드 실패(기본값 사용): {e}")
    # 원문 표현 키는 소문자로 정규화(대소문자 무시 매칭).
    return {
        lang: {k.lower(): v for k, v in terms.items()}
        for lang, terms in base.items()
    }


GLOSSARY = _load_glossary()


async def _translate_llm(text, src_iso, tgt_iso):
    """Ollama 로컬 LLM으로 번역(구어체·슬랭에 강함). 실패 시 빈 문자열 → NLLB 폴백."""
    import aiohttp  # edge-tts 의존성으로 이미 설치됨

    tgt_name = LANG_NAME.get(tgt_iso)
    if not tgt_name:
        return ""
    src_name = LANG_NAME.get(src_iso, "the source language")

    # 용어사전 적용
    terms = GLOSSARY.get(tgt_iso, {})
    low = text.strip().lower()
    # (1) 발화 전체가 사전 용어와 정확히 일치하면 LLM 없이 즉시 반환(가장 확실·빠름).
    if low in terms:
        return terms[low]
    # (2) 문장 속에 포함된 사전 용어는 프롬프트에 "고정 번역 규칙"으로 주입.
    hits = [(k, v) for k, v in terms.items() if k in low]
    glossary_note = ""
    if hits:
        lines = "\n".join(f'- "{k}" → "{v}"' for k, v in hits)
        glossary_note = (
            "\nAlways translate these terms EXACTLY as specified:\n" + lines + "\n"
        )

    prompt = (
        f"You are a translator for a live team game voice chat. Speakers use casual "
        f"speech, gaming slang, and short tactical callouts. Translate the following "
        f"{src_name} speech into natural, colloquial {tgt_name}. Prefer gaming/team "
        f'meanings over literal ones (e.g. "my guys" = teammates not a boyfriend; '
        f'"commander" = the commander role not an order). Keep it short and natural. '
        f"Output ONLY the translation, no notes, no quotes."
        f"{glossary_note}\n\n{text}"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.3,
            "num_predict": 128,   # 번역 1문장엔 충분, 과생성 꼬리지연 방지
            "num_ctx": 1024,      # 짧은 번역용 컨텍스트 축소 → TTFT 안정
            "top_p": 0.9,
            "repeat_penalty": 1.05,
        },
    }
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(f"{OLLAMA_URL}/api/generate", json=payload) as r:
                data = await r.json()
                return (data.get("response") or "").strip()
    except Exception as e:
        print(f"[ml] LLM 번역 실패(→NLLB 폴백): {e}")
        return ""


# ---------------------------------------------------------------------------
# TTS (edge-tts) — 없는 언어는 None 반환 → 텍스트만 출력
# ---------------------------------------------------------------------------
async def _synthesize(text, lang_code):
    """edge-tts로 MP3 오디오 생성(목소리/속도 적용). 미지원/실패 시 None."""
    voice = EDGE_VOICES.get(lang_code)
    if not voice or not text.strip():
        return None
    try:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=TTS_RATE)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf += chunk["data"]
        return bytes(buf) if buf else None
    except Exception as e:
        print(f"[ml] edge-tts 합성 실패: {e}")
        return None


# ---------------------------------------------------------------------------
# 필터: 번역 생략(짧은 말) + 환각(반복 문자) 차단
# ---------------------------------------------------------------------------
# 2글자 이하 ASCII 단어(hi, no, ok...)는 자동 생략 + 아래 목록은 길어도 생략.
SKIP_MAX_ASCII = int(os.environ.get("SKIP_MAX_ASCII", "2"))
SKIP_PHRASES = {
    "hi", "hello", "hey", "yes", "yeah", "yep", "no", "nope", "ok", "okay",
    "bye", "thanks", "thank you", "hmm", "uh", "um", "oh", "wow", "haha",
    "lol", "huh", "yo",
    "네", "넵", "예", "응", "음", "어", "아니", "아니요", "안녕",
}


def _should_skip(text):
    """너무 짧거나 자명한 표현(HI/YES/NO 등)이면 True → 번역 생략."""
    norm = re.sub(r"[^\w가-힣 ]", "", text).strip().lower()
    if not norm:
        return True
    if norm in SKIP_PHRASES:
        return True
    compact = norm.replace(" ", "")
    # 매우 짧은 영문 단어만 길이로 생략(한글은 정보량이 커서 목록으로만 판단).
    if compact.isascii() and len(compact) <= SKIP_MAX_ASCII:
        return True
    return False


def _is_hallucination(text):
    """무음에서 흔한 반복 환각(예: 'HHHH...', 'h-h-h-h')을 감지."""
    compact = re.sub(r"[\s\-]+", "", text)
    if len(compact) >= 8 and len(set(compact.lower())) <= 2:
        return True  # 같은 문자만 길게 반복
    return False


# ---------------------------------------------------------------------------
# STT — SenseVoice(CPU, 빠름) 기본 + Whisper(GPU, 정확) 폴백
# ---------------------------------------------------------------------------
WHISPER_BEAM = int(os.environ.get("WHISPER_BEAM", "5"))
from faster_whisper.audio import decode_audio  # 어떤 포맷이든 16k 모노 float32로 디코드


def _run_whisper(samples, use_vad, language=None):
    return _whisper.transcribe(
        samples,
        beam_size=WHISPER_BEAM,
        temperature=0.0,
        language=language,  # None이면 자동 감지, 지정 시 그 언어로 고정
        vad_filter=use_vad,
        vad_parameters=dict(min_silence_duration_ms=500) if use_vad else None,
        condition_on_previous_text=False,  # 직전 텍스트 반복(루프) 방지
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,  # 반복 텍스트(환각) 억제
    )


def _whisper_transcribe(samples, language=None):
    try:
        segments, info = _run_whisper(samples, use_vad=True, language=language)
        segments = list(segments)
    except Exception as e:
        print(f"[ml] VAD 사용 불가, 미적용으로 재시도: {e}")
        segments, info = _run_whisper(samples, use_vad=False, language=language)
        segments = list(segments)
    parts = []
    for seg in segments:
        if getattr(seg, "no_speech_prob", 0.0) > 0.6:
            continue
        if getattr(seg, "avg_logprob", 0.0) < -1.0:
            continue
        if getattr(seg, "compression_ratio", 0.0) > 2.4:
            continue
        parts.append(seg.text)
    text = "".join(parts).strip()
    # 언어 고정 시엔 감지 신뢰도 필터를 건너뛴다(강제했으므로 prob 무의미).
    prob = getattr(info, "language_probability", 1.0) or 1.0
    if (language is None and prob < 0.5) or _is_hallucination(text):
        return "", info.language
    return text, info.language


def _sensevoice_transcribe(samples):
    s = _sv.create_stream()
    s.accept_waveform(16000, samples)
    _sv.decode_stream(s)
    r = s.result
    lang = (r.lang or "").replace("<|", "").replace("|>", "").strip() or "en"
    return (r.text or "").strip(), lang


# 입력 언어를 고정할 수 있는 언어 코드 (Whisper가 인식하는 ISO-639-1).
FORCE_LANGS = {"ko", "en", "ja", "zh", "es", "fr", "de", "vi"}


def _transcribe(wav_bytes, source_lang=None):
    # 어떤 포맷(48k 스테레오 WAV 등)이든 16k 모노로 1회 디코드 후 두 엔진이 공유.
    samples = decode_audio(io.BytesIO(wav_bytes), sampling_rate=16000)

    # 입력 언어를 지정하면 SenseVoice 자동감지(오인 원인)를 건너뛰고
    # Whisper에 언어를 강제해 정확히 전사한다(예: 한국어→중국어 오인 방지).
    if source_lang in FORCE_LANGS:
        return _whisper_transcribe(samples, language=source_lang)

    if _sv is not None and STT_BACKEND in ("sensevoice", "hybrid"):
        text, lang = _sensevoice_transcribe(samples)
        if STT_BACKEND == "sensevoice":
            return ("" if _is_hallucination(text) else text), lang
        # hybrid: 실패(빈값/환각)면 Whisper 폴백. 한국어 발화는 정확도 위해 Whisper 재확인.
        need_whisper = (not text) or _is_hallucination(text)
        if STT_KO_VERIFY and lang == "ko":
            need_whisper = True
        if not need_whisper:
            return text, lang
        print(f"[ml] Whisper 재확인 (lang={lang})")

    return _whisper_transcribe(samples)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "compute": COMPUTE,
        "stt_backend": STT_BACKEND,
        "sensevoice": _sv is not None,
        "whisper_model": WHISPER_MODEL,
        "translators": {
            "en2ko": _en2ko is not None,
            "ko2en": _ko2en is not None,
            "general": _general is not None,
        },
        "tts": EDGE_VOICES,
        "tts_rate": TTS_RATE,
    }


@app.post("/stt")
async def stt(file: UploadFile = File(...)):
    """전사만(번역·TTS·KO재확인 없이) 빠르게 — 발화 중 부분 원문 표시용."""
    wav_bytes = await file.read()
    samples = decode_audio(io.BytesIO(wav_bytes), sampling_rate=16000)
    if _sv is not None:
        text, lang = _sensevoice_transcribe(samples)
    else:
        text, lang = _whisper_transcribe(samples)
    if _is_hallucination(text) or _should_skip(text):
        text = ""
    return JSONResponse({"source_text": text, "source_lang": lang})


@app.post("/tts")
async def tts(text: str = Form(...), target: str = Form(...)):
    """번역문 → 음성(MP3). 봇이 텍스트 전송 후 비동기로 호출(A1: TTS 분리)."""
    audio = await _synthesize(text, target)
    return JSONResponse(
        {"audio_b64": base64.b64encode(audio).decode("ascii") if audio else None}
    )


@app.post("/process")
async def process(file: UploadFile = File(...), target: str = Form(...), speak: str = Form("0"), source: str = Form("")):
    _t0 = time.time()
    wav_bytes = await file.read()

    source_text, source_lang = _transcribe(wav_bytes, source or None)
    _t1 = time.time()
    # 빈 텍스트 / 너무 짧거나 자명한 표현(HI/YES/NO 등)은 번역 생략.
    if not source_text or _should_skip(source_text):
        print(f"[ml] timing STT={_t1-_t0:.2f}s (skip) audio={len(wav_bytes)}B")
        return JSONResponse(
            {"source_text": source_text, "source_lang": source_lang, "translated_text": "", "target_lang": target, "audio_b64": None}
        )

    # 번역: LLM 백엔드면 먼저 시도, 실패/빈값이면 NLLB로 폴백.
    translated = ""
    if TRANSLATE_BACKEND == "llm" and source_lang != target:
        translated = await _translate_llm(source_text, source_lang, target)
    if not translated:
        translated = _translate(source_text, source_lang, target)
    _t2 = time.time()
    # 음성 출력이 필요할 때만 TTS 생성(텍스트 모드에선 생략 → 속도 향상).
    audio = await _synthesize(translated, target) if speak in ("1", "true", "True") else None
    _t3 = time.time()
    print(f"[ml] timing STT={_t1-_t0:.2f}s translate={_t2-_t1:.2f}s tts={_t3-_t2:.2f}s "
          f"audio_in={len(wav_bytes)}B lang={source_lang}")

    return JSONResponse(
        {
            "source_text": source_text,
            "source_lang": source_lang,
            "translated_text": translated,
            "target_lang": target,
            "audio_b64": base64.b64encode(audio).decode("ascii") if audio else None,
        }
    )
