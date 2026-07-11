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

# 한↔영 전용 파인튜닝 모델 (우선 사용) + 그 외 언어용 범용 모델(선택).
NLLB_EN2KO_DIR = os.environ.get("NLLB_EN2KO_DIR", os.path.join(HERE, "models", "nllb-finetuned-en2ko-ct2"))
NLLB_KO2EN_DIR = os.environ.get("NLLB_KO2EN_DIR", os.path.join(HERE, "models", "nllb-finetuned-ko2en-ct2"))
NLLB_MODEL_DIR = os.environ.get("NLLB_MODEL_DIR", os.path.join(HERE, "models", "nllb-200-distilled-1.3B-ct2"))
# NLLB 토크나이저는 모든 distilled 변형이 동일 → HF id 하나로 공유(최초 1회 다운로드 후 캐시).
NLLB_TOKENIZER = os.environ.get("NLLB_TOKENIZER", "facebook/nllb-200-distilled-600M")
NLLB_BEAM = int(os.environ.get("NLLB_BEAM", "4"))

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
print(f"[ml] Whisper 로딩: {WHISPER_MODEL} ({DEVICE}/{COMPUTE}) ...")
_whisper = WhisperModel(WHISPER_MODEL, device=DEVICE, compute_type=COMPUTE)
print("[ml] Whisper 준비 완료.")


def _load_translator(path, label):
    if path and os.path.isdir(path):
        print(f"[ml] 번역모델 로딩({label}): {path}")
        return ctranslate2.Translator(path, device=DEVICE, compute_type=COMPUTE)
    print(f"[ml] 번역모델 없음({label}): {path} — 건너뜀")
    return None


_en2ko = _load_translator(NLLB_EN2KO_DIR, "en→ko (finetuned)")
_ko2en = _load_translator(NLLB_KO2EN_DIR, "ko→en (finetuned)")
_general = _load_translator(NLLB_MODEL_DIR, "general")
_tokenizer = AutoTokenizer.from_pretrained(NLLB_TOKENIZER)
print("[ml] 번역 모델 준비 완료.")


print(f"[ml] TTS(edge-tts) 목소리: {EDGE_VOICES} | 속도: {TTS_RATE}")


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
        [source], target_prefix=[[tgt]], beam_size=NLLB_BEAM
    )
    tokens = results[0].hypotheses[0]
    if tokens and tokens[0] == tgt:
        tokens = tokens[1:]  # 선두 언어 토큰 제거
    return _tokenizer.decode(
        _tokenizer.convert_tokens_to_ids(tokens), skip_special_tokens=True
    )


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
# STT
# ---------------------------------------------------------------------------
def _run_whisper(wav_bytes, use_vad, language=None):
    return _whisper.transcribe(
        io.BytesIO(wav_bytes),
        beam_size=5,
        temperature=0.0,
        language=language,  # None이면 자동 감지, 지정 시 그 언어로 고정
        vad_filter=use_vad,
        vad_parameters=dict(min_silence_duration_ms=500) if use_vad else None,
        condition_on_previous_text=False,  # 직전 텍스트 반복(루프) 방지
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,  # 반복 텍스트(환각) 억제
    )


def _transcribe(wav_bytes, source_lang=None):
    # 입력 언어 고정 시 자동감지의 오탐(예: 한국어→중국어)을 방지.
    lang = source_lang if source_lang in NLLB_CODES else None
    # VAD로 무음/잡음 구간 제거 + 환각 억제. VAD(onnxruntime) 미설치 시 폴백.
    try:
        segments, info = _run_whisper(wav_bytes, use_vad=True, language=lang)
        segments = list(segments)
    except Exception as e:
        print(f"[ml] VAD 사용 불가, 미적용으로 재시도: {e}")
        segments, info = _run_whisper(wav_bytes, use_vad=False, language=lang)
        segments = list(segments)
    parts = []
    for seg in segments:
        # 비음성/저신뢰/반복(환각) 구간은 버린다.
        if getattr(seg, "no_speech_prob", 0.0) > 0.6:
            continue
        if getattr(seg, "avg_logprob", 0.0) < -1.0:
            continue
        if getattr(seg, "compression_ratio", 0.0) > 2.4:
            continue
        parts.append(seg.text)
    text = "".join(parts).strip()

    # 반복 환각이면 버린다. (언어 고정 시엔 감지신뢰도 필터를 건너뜀)
    if _is_hallucination(text):
        return "", info.language
    if not lang:
        prob = getattr(info, "language_probability", 1.0) or 1.0
        if prob < 0.5:
            return "", info.language
    return text, info.language


@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "translators": {
            "en2ko": _en2ko is not None,
            "ko2en": _ko2en is not None,
            "general": _general is not None,
        },
        "tts": EDGE_VOICES,
        "tts_rate": TTS_RATE,
    }


@app.post("/process")
async def process(
    file: UploadFile = File(...),
    target: str = Form(...),
    source: str = Form(""),
):
    wav_bytes = await file.read()

    source_text, source_lang = _transcribe(wav_bytes, source or None)
    # 빈 텍스트 / 너무 짧거나 자명한 표현(HI/YES/NO 등)은 번역 생략.
    if not source_text or _should_skip(source_text):
        return JSONResponse(
            {"source_text": source_text, "source_lang": source_lang, "translated_text": "", "target_lang": target, "audio_b64": None}
        )

    translated = _translate(source_text, source_lang, target)
    audio = await _synthesize(translated, target)

    return JSONResponse(
        {
            "source_text": source_text,
            "source_lang": source_lang,
            "translated_text": translated,
            "target_lang": target,
            "audio_b64": base64.b64encode(audio).decode("ascii") if audio else None,
        }
    )
