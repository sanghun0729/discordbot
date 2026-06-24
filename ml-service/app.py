"""
로컬 ML 사이드카 — STT(faster-whisper) + 번역(argostranslate) + TTS(piper).

전부 로컬에서 무료로 동작한다. Node 봇이 WAV 오디오를 POST /process 로 보내면
{ source_text, source_lang, translated_text, target_lang, audio_b64 } 를 돌려준다.

실행:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

import base64
import io
import json
import os
import subprocess
import tempfile
import wave

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse

from faster_whisper import WhisperModel
import argostranslate.package
import argostranslate.translate

app = FastAPI(title="Discord 번역 봇 ML 서비스")

# ---------------------------------------------------------------------------
# 설정 (환경변수로 조정 가능)
# ---------------------------------------------------------------------------
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")  # tiny/base/small/medium...
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")  # cpu 또는 cuda
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")  # cpu면 int8 권장
PIPER_BIN = os.environ.get("PIPER_BIN", "piper")
# 언어코드 -> piper voice(.onnx) 경로 매핑. voices.json 또는 PIPER_VOICES(env, JSON)로 지정.
VOICES_FILE = os.environ.get("PIPER_VOICES_FILE", os.path.join(os.path.dirname(__file__), "voices.json"))

print(f"[ml] Whisper 모델 로딩: {WHISPER_MODEL} ({WHISPER_DEVICE}/{WHISPER_COMPUTE}) ...")
_whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
print("[ml] Whisper 준비 완료.")


def _load_voices():
    if os.environ.get("PIPER_VOICES"):
        try:
            return json.loads(os.environ["PIPER_VOICES"])
        except json.JSONDecodeError:
            pass
    if os.path.exists(VOICES_FILE):
        with open(VOICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


VOICES = _load_voices()
print(f"[ml] TTS voices: {list(VOICES.keys()) or '(없음 — 텍스트만 출력)'}")


# ---------------------------------------------------------------------------
# 번역: 필요한 argostranslate 언어팩을 지연 설치
# ---------------------------------------------------------------------------
_installed_pairs = None


def _refresh_installed():
    global _installed_pairs
    _installed_pairs = {
        (p.from_code, p.to_code) for p in argostranslate.package.get_installed_packages()
    }


def _ensure_pair(from_code, to_code):
    """from->to 직접 팩이 없으면 from->en, en->to 를 설치해 영어 경유 번역을 가능케 한다."""
    if _installed_pairs is None:
        _refresh_installed()
    needed = []
    if (from_code, to_code) in _installed_pairs:
        return
    # 직접 팩 우선, 없으면 영어 피벗.
    candidates = [(from_code, to_code)]
    if from_code != "en" and to_code != "en":
        candidates = [(from_code, "en"), ("en", to_code)]

    available = argostranslate.package.get_available_packages()
    for fc, tc in candidates:
        if (fc, tc) in _installed_pairs:
            continue
        match = next((p for p in available if p.from_code == fc and p.to_code == tc), None)
        if match:
            needed.append(match)

    if needed:
        for pkg in needed:
            print(f"[ml] argos 언어팩 설치: {pkg.from_code}->{pkg.to_code}")
            argostranslate.package.install_from_path(pkg.download())
        _refresh_installed()


def _translate(text, from_code, to_code):
    if not text.strip() or from_code == to_code:
        return text
    try:
        argostranslate.package.update_package_index()
    except Exception as e:  # 인덱스 갱신 실패해도 이미 설치된 팩으로 시도
        print(f"[ml] 패키지 인덱스 갱신 실패(무시): {e}")
    try:
        _ensure_pair(from_code, to_code)
        return argostranslate.translate.translate(text, from_code, to_code)
    except Exception as e:
        print(f"[ml] 번역 실패({from_code}->{to_code}), 원문 반환: {e}")
        return text


# ---------------------------------------------------------------------------
# TTS: piper CLI 로 합성 (없는 언어는 None 반환 → 텍스트만 출력)
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
    return {"status": "ok", "voices": list(VOICES.keys())}


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
