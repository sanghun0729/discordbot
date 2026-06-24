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

# TTS: gTTS (Google Translate TTS, 무료·키 불필요). ISO-639-1 -> gTTS 언어코드.
# 번역문 텍스트만 전송하며 MP3 오디오를 받는다(로컬 아님, 무료).
GTTS_LANG = {
    "ko": "ko",
    "en": "en",
    "ja": "ja",
    "zh": "zh-CN",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "vi": "vi",
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


print(f"[ml] TTS(gTTS) 지원 언어: {list(GTTS_LANG.keys())}")


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
# TTS (piper) — 없는 언어는 None 반환 → 텍스트만 출력
# ---------------------------------------------------------------------------
def _synthesize(text, lang_code):
    """gTTS로 MP3 오디오 생성. 미지원 언어/실패 시 None(텍스트만 출력)."""
    code = GTTS_LANG.get(lang_code)
    if not code or not text.strip():
        return None
    try:
        from gtts import gTTS

        buf = io.BytesIO()
        gTTS(text=text, lang=code).write_to_fp(buf)  # MP3 (Node가 ffmpeg로 재생)
        return buf.getvalue()
    except Exception as e:
        print(f"[ml] gTTS 합성 실패: {e}")
        return None


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------
def _transcribe(wav_bytes):
    segments, info = _whisper.transcribe(io.BytesIO(wav_bytes), beam_size=5)
    text = "".join(seg.text for seg in segments).strip()
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
        "tts": list(GTTS_LANG.keys()),
    }


@app.post("/process")
async def process(file: UploadFile = File(...), target: str = Form(...)):
    wav_bytes = await file.read()

    source_text, source_lang = _transcribe(wav_bytes)
    if not source_text:
        return JSONResponse(
            {"source_text": "", "source_lang": source_lang, "translated_text": "", "target_lang": target, "audio_b64": None}
        )

    translated = _translate(source_text, source_lang, target)
    audio = _synthesize(translated, target)

    return JSONResponse(
        {
            "source_text": source_text,
            "source_lang": source_lang,
            "translated_text": translated,
            "target_lang": target,
            "audio_b64": base64.b64encode(audio).decode("ascii") if audio else None,
        }
    )
