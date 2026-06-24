#!/usr/bin/env bash
# NLLB-200 모델을 CTranslate2 포맷으로 변환한다 (RealTrans와 동일한 ctrans 엔진).
# ctranslate2 pip 패키지에 포함된 ct2-transformers-converter 를 사용한다.
#
# 사용:
#   bash setup_translation.sh                       # 기본: 1.3B, float16 (GPU 권장)
#   bash setup_translation.sh facebook/nllb-200-distilled-600M models/nllb-600M-ct2 int8   # CPU용
set -euo pipefail

MODEL="${1:-facebook/nllb-200-distilled-1.3B}"
OUT="${2:-models/nllb-200-distilled-1.3B-ct2}"
QUANT="${3:-float16}"   # GPU: float16 / CPU: int8

echo "▶ 변환: ${MODEL} -> ${OUT} (quant=${QUANT})"
ct2-transformers-converter \
  --model "${MODEL}" \
  --output_dir "${OUT}" \
  --quantization "${QUANT}" \
  --copy_files tokenizer.json tokenizer_config.json special_tokens_map.json sentencepiece.bpe.model

echo "✅ 완료 -> ${OUT}"
echo "   app.py 는 NLLB_MODEL_DIR 로 이 경로를 사용합니다 (기본값과 다르면 .env/환경변수로 지정)."
