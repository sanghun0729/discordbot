#!/usr/bin/env bash
# NLLB 계열 모델을 CTranslate2 포맷으로 변환한다 (RealTrans와 동일한 ctrans 엔진).
# ctranslate2 pip 패키지에 포함된 ct2-transformers-converter 를 사용한다.
# 토크나이저는 런타임에 HF id(facebook/nllb-200-distilled-600M)로 공유 로드하므로 별도 복사 불필요.
#
# 사용:
#   bash setup_translation.sh <HF모델> <출력디렉터리> <quant>
# 예:
#   bash setup_translation.sh NHNDQ/nllb-finetuned-en2ko models/nllb-finetuned-en2ko-ct2 float16
#   bash setup_translation.sh NHNDQ/nllb-finetuned-ko2en models/nllb-finetuned-ko2en-ct2 float16
#   bash setup_translation.sh facebook/nllb-200-distilled-1.3B models/nllb-200-distilled-1.3B-ct2 float16
set -euo pipefail

MODEL="${1:?HF 모델 id 필요}"
OUT="${2:?출력 디렉터리 필요}"
QUANT="${3:-float16}"   # GPU: float16 / CPU: int8

if [ -d "$OUT" ]; then
  echo "↷ 이미 존재: ${OUT} (건너뜀)"
  exit 0
fi

echo "▶ 변환: ${MODEL} -> ${OUT} (quant=${QUANT})"
ct2-transformers-converter \
  --model "${MODEL}" \
  --output_dir "${OUT}" \
  --quantization "${QUANT}"

echo "✅ 완료 -> ${OUT}"
