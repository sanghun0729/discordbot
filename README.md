# Discord 음성 번역 봇 (로컬·무료)

음성 채널의 **발화를 인식(STT)** 하고 **번역**하여 **텍스트 + 음성(TTS)** 으로 출력하는 Discord 봇입니다.
STT/번역/TTS를 **전부 로컬에서 무료**로 처리합니다 (유료 API 불필요).

```
사용자 음성(마이크)
  → Discord 음성 채널
    → Node 봇이 수신 (@discordjs/voice) → Opus 디코딩 → WAV
      → [로컬 Python ML 사이드카]
          STT  : faster-whisper          (음성→텍스트, 언어 자동 감지)
          번역 : CTranslate2 + NLLB-200  (오프라인, 고품질)
          TTS  : piper                   (텍스트→음성)
      → 텍스트 채널에 번역문 전송  +  음성 채널에 TTS 재생
```

## 구성 요소

| 구성 | 역할 | 기술 |
|---|---|---|
| **Node 봇** (`src/`) | Discord 입출력 — 음성 수신, 텍스트 전송, TTS 재생 | discord.js, @discordjs/voice |
| **ML 사이드카** (`ml-service/`) | STT + 번역 + TTS (로컬·무료) | FastAPI, faster-whisper, CTranslate2+NLLB, piper |

두 프로세스는 같은 서버에서 HTTP(`http://127.0.0.1:8000`)로 통신합니다.

## 명령어

- `/join language:<언어> [user:<사용자>] [speak:<true/false>]` — 음성 채널 입장, 번역 시작
  - `user`: 그 사용자의 발화만 번역 (요청하신 "특정 사용자")
  - `speak`: 번역문 음성(TTS) 재생 여부 (기본 켜짐)
- `/setlang language:<언어>` — 번역 목표 언어 변경 (명령어로 언어 설정)
- `/leave` — 종료

출력 예시(텍스트):
```
🗣️ Alice → 안녕하세요, 잘 지내세요?
> Hello, how are you?
```

---

## 설치 (Ubuntu 24.04 기준)

### 0) 시스템 패키지
```bash
sudo apt-get update
sudo apt-get install -y nodejs npm python3 python3-venv python3-pip build-essential
# ffmpeg 는 npm 의 ffmpeg-static 으로 자동 제공되므로 별도 설치 불필요
```

### 1) ML 사이드카 (Python)
```bash
cd ml-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 번역 모델을 CTranslate2 포맷으로 변환 (1회). 한↔영 전용 파인튜닝 우선.
bash setup_translation.sh NHNDQ/nllb-finetuned-en2ko models/nllb-finetuned-en2ko-ct2 float16
bash setup_translation.sh NHNDQ/nllb-finetuned-ko2en models/nllb-finetuned-ko2en-ct2 float16
#   (선택) 한↔영 외 다른 언어도 쓰려면 범용 모델도 변환:
#   bash setup_translation.sh facebook/nllb-200-distilled-1.3B models/nllb-200-distilled-1.3B-ct2 float16
#   CPU면 quant 를 int8 로, 모델은 600M 권장. 경로는 NLLB_*_DIR 환경변수로 지정.

# (선택) TTS 음성 모델 다운로드 — 음성 출력을 원할 때만
bash download_voices.sh            # 기본 /opt/piper/voices 에 저장
#   다운로드 후 voices.json 의 경로가 맞는지 확인 (없는 언어는 텍스트만 출력)

# 서비스 실행 (최초 실행 시 Whisper 모델 자동 다운로드)
uvicorn app:app --host 127.0.0.1 --port 8000
```
> **GPU(기본)**: `ML_DEVICE=cuda ML_COMPUTE=float16`, Whisper `large-v3`, NLLB `1.3B`.
> **CPU**: `ML_DEVICE=cpu ML_COMPUTE=int8`, Whisper `small`, NLLB `600M` 권장.
> NVIDIA 드라이버 + CUDA 런타임이 필요합니다(ctranslate2/faster-whisper GPU).

### 2) Node 봇
```bash
# 프로젝트 루트에서
npm install

cp .env.example .env
nano .env   # DISCORD_TOKEN, CLIENT_ID, GUILD_ID, ML_SERVICE_URL 입력

npm run deploy   # 슬래시 명령 등록
npm start        # 봇 실행
```

### 3) Discord 봇 등록
1. https://discord.com/developers/applications → **New Application**
2. **Bot** → **Reset Token** → `.env` 의 `DISCORD_TOKEN`
3. **General Information** 의 **Application ID** → `.env` 의 `CLIENT_ID`
4. **OAuth2 > URL Generator** → scopes: `bot`, `applications.commands` /
   permissions: `Connect`, `Speak`, `Send Messages`, `View Channels` → 생성된 URL로 서버 초대

---

## 사용법
1. 음성 채널 입장
2. `/join language:한국어` (특정인만: `/join language:한국어 user:@상대`, 음성 끄기: `speak:false`)
3. 말하면 약 2~5초 뒤 번역문이 텍스트로 올라오고, 음성으로도 재생됨
4. 언어 바꾸기: `/setlang language:English`
5. 종료: `/leave`

## 상시 구동 (선택)
```bash
# ML 서비스
sudo tee /etc/systemd/system/transl-ml.service >/dev/null <<'EOF'
[Unit]
Description=Discord Translate ML
After=network.target
[Service]
WorkingDirectory=%h/discordbot/ml-service
ExecStart=%h/discordbot/ml-service/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=default.target
EOF

# Node 봇 (pm2)
npm i -g pm2 && pm2 start src/index.js --name transl-bot && pm2 save
```

## 설계 / 한계
- **발화 단위 처리**: 0.8초 무음 기준으로 문장 종료를 판단 → 완전 실시간이 아니라 2~5초 지연.
- **다중 화자**: 화자별 독립 스트림으로 동시 번역. 단, **TTS 음성 재생은 길드당 1개 오디오만 가능**하므로 큐로 직렬화되어 순서대로 재생됩니다.
- **언어 자동 감지**: Whisper가 입력 언어를 감지하고, NLLB-200이 목표 언어로 직접 번역.
- **번역 품질/엔진**: 번역은 **CTranslate2(엔진) + NLLB(모델)** 조합입니다. 모델 라우팅:
  - `en→ko` → `NHNDQ/nllb-finetuned-en2ko` (한영 전용 파인튜닝)
  - `ko→en` → `NHNDQ/nllb-finetuned-ko2en` (한영 전용 파인튜닝)
  - 그 외 언어쌍 → 범용 `NLLB-200` (설치한 경우, 없으면 원문 유지)

  이 앱은 주로 한↔영으로 쓰이므로 **한영 전용 파인튜닝본을 우선 적용**해 품질을 높였습니다. 모델 경로/디바이스는 `NLLB_EN2KO_DIR`, `NLLB_KO2EN_DIR`, `NLLB_MODEL_DIR`, `ML_DEVICE`, `ML_COMPUTE` 로 조정합니다. `GET /health` 로 어떤 번역기가 로드됐는지 확인할 수 있습니다.
- **TTS 언어**: piper 음성 모델이 있는 언어만 음성 출력. 모델이 없으면 자동으로 텍스트만 출력합니다.

## 비용
- STT/번역/TTS 전부 로컬 오픈소스 → **추가 API 비용 없음**. (서버 CPU/디스크만 사용)
