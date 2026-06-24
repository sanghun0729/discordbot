#!/usr/bin/env bash
# Piper 음성(TTS) 모델 다운로드.
# 일부가 실패(404 등)해도 설치를 멈추지 않고 계속 진행한다(해당 언어만 TTS 생략).
# Piper voices: https://huggingface.co/rhasspy/piper-voices
# 한국어는 공식 repo에 없어 커뮤니티 모델(neurlang/piper-onnx-kss-korean)을 사용.
#
# 사용: bash download_voices.sh [저장경로]   (기본 /opt/piper/voices)
set -uo pipefail   # ※ -e 제거: 한 개 실패가 전체를 중단시키지 않도록

DEST="${1:-/opt/piper/voices}"
mkdir -p "$DEST"

get() {  # get <저장파일명.onnx> <onnx 전체 URL>
  local out="$1" url="$2"
  echo "▶ ${out}"
  if curl -fL "$url" -o "${DEST}/${out}"; then
    curl -fsL "${url}.json" -o "${DEST}/${out}.json" || echo "  (설정 json 없음 — 무시)"
  else
    echo "  ↷ 다운로드 실패(스킵): ${out}"
    rm -f "${DEST}/${out}"
  fi
}

RH="https://huggingface.co/rhasspy/piper-voices/resolve/main"

# 영어 (공식)
get en_US-amy-medium.onnx     "${RH}/en/en_US/amy/medium/en_US-amy-medium.onnx"
# 일본어 (공식)
get ja_JP-hfc_female-medium.onnx "${RH}/ja/ja_JP/hfc_female/medium/ja_JP-hfc_female-medium.onnx"
# 한국어 (커뮤니티 — 공식 repo엔 한국어 없음)
get ko_KR-kss.onnx            "https://huggingface.co/neurlang/piper-onnx-kss-korean/resolve/main/piper-kss-korean.onnx"

echo "✅ 완료. 받은 음성: $(ls "$DEST"/*.onnx 2>/dev/null | wc -l)개  (경로: $DEST)"
