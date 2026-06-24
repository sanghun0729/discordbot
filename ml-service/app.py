"""
로컬 ML 사이드카 — STT(faster-whisper) + 번역(CTranslate2 + NLLB) + TTS(piper).

전부 로컬에서 무료로 동작한다. Node 봇이 WAV 오디오를 POST /process 로 보내면
{ source_text, source_lang, translated_text, target_lang, audio_b64 } 를 돌려준다.

번역 엔진은 CTranslate2("ctrans"), 모델은 Meta NLLB-200 (distilled).
→ 모델은 먼저 setup_translation.sh 로 CTranslate2 포맷으로 변환해 두어야 한다.

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
# GPU 서버 기본값(cuda/float16). CPU만 있으면 DEVICE=cpu, COMPUTE=int8 로 덮어쓸 것.
DEVICE = os.environ.get("ML_DEVICE", "cuda")
COMPUTE = os.environ.get("ML_COMPUTE", "float16")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")

NLLB_MODEL_DIR = os.environ.get(
    "NLLB_MODEL_DIR", os.path.join(HERE, "models", "nllb-200-distilled-1.3B-ct2")
)
# 토크나이저: 변환 시 모델 디렉터리에 함께 복사되므로 기본값은 모델 디렉터리.
NLLB_TOKENIZER = os.environ.get("NLLB_TOKENIZER", NLLB_MODEL_DIR)
NLLB_BEAM = int(os.environ.get("NLLB_BEAM", "4"))

PIPER_BIN = os.environ.get("PIPER_BIN", "piper")
VOICES_FILE = os.environ.get("PIPER_VOICES_FILE", os.path.join(HERE, "voices.json"))

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

print(f"[ml] NLLB(CTranslate2) 로딩: {NLLB_MODEL_DIR} ({DEVICE}/{COMPUTE}) ...")
_translator = ctranslate2.Translator(NLLB_MODEL_DIR, device=DEVICE, compute_type=COMPUTE)
_tokenizer = AutoTokenizer.from_pretrained(NLLB_TOKENIZER)
print("[ml] 번역 모델 준비 완료.")


def _load_voices():
    if os.environ.get("PIPER_VOICES"):
        try:
            return json.loads(os.environ["PIPER_VOICES"])
        except json.JSONDecodeError:
            pass
    if os.path.exists(VOICES_FILE):
        with open(VOICES_FILE, "r", encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    return {}


VOICES = _load_voices()
print(f"[ml] TTS voices: {list(VOICES.keys()) or '(없음 — 텍스트만 출력)'}")


# ---------------------------------------------------------------------------
# 번역 (CTranslate2 + NLLB)
# ---------------------------------------------------------------------------
def _translate(text, src_iso, tgt_iso):
    if not text.strip():
        return text
    tgt = NLLB_CODES.get(tgt_iso)
    if not tgt:
        return text  # 목표 언어 미지원 → 원문 반환
    src = NLLB_CODES.get(src_iso, "eng_Latn")  # 감지 실패 시 영어로 가정
    if src == tgt:
        return text

    _tokenizer.src_lang = src
    source = _tokenizer.convert_ids_to_tokens(_tokenizer.encode(text))
    results = _translator.translate_batch(
        [source], target_prefix=[[tgt]], beam_size=NLLB_BEAM
    )
    tokens = results[0].hypotheses[0]
    if tokens and tokens[0] == tgt:
        tokens = tokens[1:]  # 선두의 언어 토큰 제거
    return _tokenizer.decode(
        _tokenizer.convert_tokens_to_ids(tokens), skip_special_tokens=True
    )


# ---------------------------------------------------------------------------
# TTS (piper) — 없는 언어는 None 반환 → 텍스트만 출력
# ---------------------------------------------------------------------------
def _synthesize(text, lang_code):
    voice = VOICES.get(lang_code)
    if not voice or not text.strip():
        return None
    if not os.path.exists(voice):
        print(f"[ml] voice 파일 없음: {voice}")
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out_path = tmp.name
    try:
        subprocess.run(
            [PIPER_BIN, "--model", voice, "--output_file", out_path],
            input=text.encode("utf-8"),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        with open(out_path, "rb") as f:
            return f.read()
    except subprocess.CalledProcessError as e:
        print(f"[ml] piper 합성 실패: {e.stderr.decode('utf-8', 'ignore')[:200]}")
        return None
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------
def _transcribe(wav_bytes):
    segments, info = _whisper.transcribe(io.BytesIO(wav_bytes), beam_size=5)
    text = "".join(seg.text for seg in segments).strip()
    return text, info.language


@app.get("/health")
def health():
    return {"status": "ok", "device": DEVICE, "voices": list(VOICES.keys())}


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
