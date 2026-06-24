#!/usr/bin/env bash
# Piper 음성(TTS) 모델 다운로드 스크립트.
# Piper voices: https://huggingface.co/rhasspy/piper-voices
# 사용: bash download_voices.sh  (기본 /opt/piper/voices 에 저장)
set -euo pipefail

DEST="${1:-/opt/piper/voices}"
mkdir -p "$DEST"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"

# 언어코드:HF경로(.onnx) — 필요 언어만 남기거나 추가하세요.
download() {
  local code="$1" path="$2" name
  name="$(basename "$path")"
  echo "▶ ${code}: ${name}"
  curl -fL "${BASE}/${path}" -o "${DEST}/${name}"
  curl -fL "${BASE}/${path}.json" -o "${DEST}/${name}.json"
}

download ko "ko/ko_KR/glow/medium/ko_KR-glow-medium.onnx"
download en "en/en_US/amy/medium/en_US-amy-medium.onnx"
download ja "ja/ja_JP/hfc_female/medium/ja_JP-hfc_female-medium.onnx"

echo "✅ 완료. voices.json 의 경로를 ${DEST} 기준으로 맞추세요."
