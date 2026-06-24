#!/usr/bin/env bash
# =============================================================================
# Discord 음성 번역 봇 — Ubuntu 24.04 (GPU) 원클릭 설치 스크립트
#
# 사용법 (서버에서, 저장소 루트에서 실행):
#   git clone <repo-url> discordbot
#   cd discordbot
#   git checkout claude/discord-translation-bot-73tdup
#   bash install.sh
#
# 이 스크립트가 하는 일:
#   1) 시스템 패키지 (Node.js 20, Python venv, 빌드도구) 설치
#   2) Node 봇 의존성 설치
#   3) Python ML 사이드카 가상환경 + 의존성 설치
#   4) NLLB-200 번역 모델을 CTranslate2 포맷으로 변환
#   5) (선택) piper TTS 음성 모델 다운로드
#   6) .env 템플릿 생성
#   7) systemd 서비스 등록 (ML 서비스 + Node 봇)
#
# GPU 전제: NVIDIA 드라이버가 이미 설치되어 `nvidia-smi` 가 동작해야 합니다.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
echo "▶ 설치 디렉터리: $ROOT"

# --- 0. 사전 점검 ----------------------------------------------------------
if ! command -v sudo >/dev/null 2>&1; then
  echo "✗ sudo 가 필요합니다." >&2; exit 1
fi

GPU=1
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  echo "✔ GPU 감지됨:"; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true
else
  echo "⚠ nvidia-smi 가 동작하지 않습니다 → CPU 모드로 설치합니다(느림)."
  GPU=0
fi

# --- 1. 시스템 패키지 ------------------------------------------------------
echo "▶ [1/7] 시스템 패키지 설치..."
sudo apt-get update -y
sudo apt-get install -y curl git build-essential python3 python3-venv python3-pip ffmpeg

# Node.js 20 (apt 기본이 18 미만일 때만 NodeSource 사용)
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
  echo "▶ Node.js 20 설치(NodeSource)..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
echo "✔ node $(node -v) / npm $(npm -v)"

# --- 2. Node 봇 의존성 -----------------------------------------------------
echo "▶ [2/7] Node 봇 의존성 설치..."
npm install

# --- 3. Python ML 사이드카 -------------------------------------------------
echo "▶ [3/7] Python 가상환경 + 의존성 설치..."
cd "$ROOT/ml-service"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ "$GPU" -eq 1 ]; then
  echo "▶ GPU용 CUDA 런타임 라이브러리(cuBLAS/cuDNN) 설치..."
  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
fi

# --- 4. 번역 모델 변환 -----------------------------------------------------
echo "▶ [4/7] 번역 모델 → CTranslate2 변환 (다운로드, 시간 소요)..."
if [ "$GPU" -eq 1 ]; then
  QUANT=float16
  ML_DEVICE_DEFAULT=cuda; ML_COMPUTE_DEFAULT=float16; WHISPER_DEFAULT=large-v3
else
  QUANT=int8
  ML_DEVICE_DEFAULT=cpu; ML_COMPUTE_DEFAULT=int8; WHISPER_DEFAULT=small
fi

# 한↔영 전용 파인튜닝 모델(우선) — 이 앱의 주 용도.
bash setup_translation.sh NHNDQ/nllb-finetuned-en2ko "models/nllb-finetuned-en2ko-ct2" "$QUANT"
bash setup_translation.sh NHNDQ/nllb-finetuned-ko2en "models/nllb-finetuned-ko2en-ct2" "$QUANT"

# (선택) 그 외 언어용 범용 NLLB-200. 한↔영만 쓰면 건너뛰어 VRAM/용량 절약.
GENERAL_DIR=""
read -r -p "▶ 한↔영 외 다른 언어도 쓸까요? (범용 NLLB-200 추가 설치) [y/N] " GEN || GEN=N
if [[ "${GEN:-N}" =~ ^[Yy]$ ]]; then
  if [ "$GPU" -eq 1 ]; then
    bash setup_translation.sh facebook/nllb-200-distilled-1.3B "models/nllb-200-distilled-1.3B-ct2" "$QUANT"
    GENERAL_DIR="$ROOT/ml-service/models/nllb-200-distilled-1.3B-ct2"
  else
    bash setup_translation.sh facebook/nllb-200-distilled-600M "models/nllb-200-distilled-600M-ct2" "$QUANT"
    GENERAL_DIR="$ROOT/ml-service/models/nllb-200-distilled-600M-ct2"
  fi
fi

# --- 5. (선택) TTS 음성 모델 ----------------------------------------------
read -r -p "▶ [5/7] TTS(음성 출력) 모델을 지금 받을까요? [y/N] " ANS || ANS=N
if [[ "${ANS:-N}" =~ ^[Yy]$ ]]; then
  bash download_voices.sh "$ROOT/ml-service/voices"
  echo "  → voices.json 의 경로를 $ROOT/ml-service/voices 기준으로 수정하세요."
fi
deactivate

# --- 6. .env 생성 ----------------------------------------------------------
echo "▶ [6/7] .env 템플릿 생성..."
cd "$ROOT"
if [ ! -f .env ]; then
  cp .env.example .env
fi
cat > ml-service/ml.env <<EOF
ML_DEVICE=${ML_DEVICE_DEFAULT}
ML_COMPUTE=${ML_COMPUTE_DEFAULT}
WHISPER_MODEL=${WHISPER_DEFAULT}
NLLB_EN2KO_DIR=${ROOT}/ml-service/models/nllb-finetuned-en2ko-ct2
NLLB_KO2EN_DIR=${ROOT}/ml-service/models/nllb-finetuned-ko2en-ct2
NLLB_MODEL_DIR=${GENERAL_DIR}
EOF
echo "  → ml-service/ml.env 생성됨 (ML 서비스 환경변수)"

# --- 7. systemd 서비스 -----------------------------------------------------
echo "▶ [7/7] systemd 서비스 등록..."
USER_NAME="$(whoami)"
LDPATH=""
if [ "$GPU" -eq 1 ]; then
  PYV="$(ls "$ROOT/ml-service/.venv/lib" | head -n1)"
  LDPATH="Environment=LD_LIBRARY_PATH=$ROOT/ml-service/.venv/lib/$PYV/site-packages/nvidia/cublas/lib:$ROOT/ml-service/.venv/lib/$PYV/site-packages/nvidia/cudnn/lib"
fi

sudo tee /etc/systemd/system/transl-ml.service >/dev/null <<EOF
[Unit]
Description=Discord Translate ML (STT/번역/TTS)
After=network.target
[Service]
User=${USER_NAME}
WorkingDirectory=${ROOT}/ml-service
EnvironmentFile=${ROOT}/ml-service/ml.env
${LDPATH}
ExecStart=${ROOT}/ml-service/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/transl-bot.service >/dev/null <<EOF
[Unit]
Description=Discord Translate Bot (Node)
After=network.target transl-ml.service
[Service]
User=${USER_NAME}
WorkingDirectory=${ROOT}
EnvironmentFile=${ROOT}/.env
ExecStart=$(command -v node) ${ROOT}/src/index.js
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

cat <<EOF

============================================================
✅ 설치 완료.

남은 단계 (직접 하셔야 합니다):

  1) Discord 토큰 입력:
       nano ${ROOT}/.env
       # DISCORD_TOKEN, CLIENT_ID, GUILD_ID 채우기

  2) 슬래시 명령 등록 (1회):
       cd ${ROOT} && npm run deploy

  3) 서비스 시작:
       sudo systemctl enable --now transl-ml transl-bot

  4) 상태/로그 확인:
       systemctl status transl-ml transl-bot
       journalctl -u transl-ml -f
       journalctl -u transl-bot -f
============================================================
EOF
