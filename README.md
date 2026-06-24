# Discord 음성 번역 봇

음성 채널에 들어온 사용자의 **발화를 인식(STT)** 하고 **목표 언어로 번역**하여 **텍스트 채팅**으로 출력하는 Discord 봇입니다.

```
사용자 음성(마이크)
  → Discord 음성 채널
    → 봇이 음성 수신 (@discordjs/voice)
      → Opus 디코딩 → WAV
        → Whisper (STT, 음성→텍스트)
          → GPT (번역)
            → 텍스트 채널에 전송
```

## 기능

- `/join language:<언어> [user:<사용자>]` — 현재 내가 있는 음성 채널에 봇이 입장, 발화를 번역해 명령을 입력한 채널에 전송
  - `user` 를 지정하면 **그 사용자의 발화만** 번역 (요구사항의 "특정 사용자" 대응)
- `/leave` — 음성 채널 퇴장 및 번역 종료

출력 예시:
```
🗣️ Alice → 안녕하세요, 잘 지내세요?
> Hello, how are you?
```

## 사전 준비

### 1) Discord 봇 생성
1. https://discord.com/developers/applications → **New Application**
2. **Bot** 탭 → **Reset Token** 으로 토큰 발급 → `.env` 의 `DISCORD_TOKEN`
3. **Bot** 탭에서 **SERVER MEMBERS INTENT** / **MESSAGE CONTENT INTENT** 는 이 봇엔 불필요(슬래시 명령 사용). 단, 음성 수신을 위해 봇이 채널에서 **스스로 deaf 상태가 아니어야** 함 (코드에서 `selfDeaf: false` 처리됨).
4. **OAuth2 > URL Generator** → scopes: `bot`, `applications.commands` / bot permissions: `Connect`, `Speak`, `Send Messages`, `View Channels` → 생성된 URL 로 서버에 초대
5. **General Information** 의 **Application ID** → `.env` 의 `CLIENT_ID`

### 2) OpenAI API 키
- https://platform.openai.com/api-keys → 키 발급 → `.env` 의 `OPENAI_API_KEY`

### 3) 시스템 요구사항
- Node.js 18+ (권장 20/22)
- 네이티브 Opus 디코딩을 위해 `@discordjs/opus` 가 빌드됩니다. 빌드 도구가 없으면(드물게) `python3`, `make`, `g++` 등이 필요할 수 있습니다.

## 설치 및 실행

```bash
# 1. 의존성 설치
npm install

# 2. 환경변수 설정
cp .env.example .env
#   .env 를 열어 DISCORD_TOKEN / CLIENT_ID / GUILD_ID / OPENAI_API_KEY 입력

# 3. 슬래시 명령 등록 (GUILD_ID 지정 시 즉시 반영)
npm run deploy

# 4. 봇 실행
npm start
```

## 사용법

1. 음성 채널에 입장
2. 텍스트 채널에서 `/join language:Korean` 입력 (특정인만: `/join language:Korean user:@Alice`)
3. 음성 채널에서 말하면 → 잠시 후(약 2~5초) 번역문이 텍스트로 올라옴
4. `/leave` 로 종료

## 동작 / 설계 메모

- **발화 단위 처리**: 0.8초 이상 무음이 감지되면 한 문장이 끝난 것으로 보고 STT→번역을 수행합니다. 완전 실시간 동시통역이 아니라 약간의 지연이 있습니다.
- **여러 화자**: 화자별로 독립 스트림을 구독하므로 동시에 여러 명이 말해도 각각 번역됩니다.
- **언어 자동 감지**: Whisper 가 입력 언어를 자동 감지합니다. 화자가 항상 한 언어만 쓴다면 `.env` 의 `SOURCE_LANG`(예: `en`)을 지정해 정확도를 높일 수 있습니다.

## 비용 / 한계

- Whisper STT, GPT 번역은 **유료 API**입니다 (사용량 과금).
- Discord 음성 수신 API 는 공식적으로 "unsupported" 로 표기되지만 `@discordjs/voice` 로 안정적으로 동작합니다.
- 짧은 잡음(0.6초 미만)은 무시합니다.

## 향후 확장 (선택)

- **음성(TTS) 출력**: 번역문을 TTS(OpenAI tts-1 등)로 합성해 봇이 음성 채널에 재생. 단, 봇은 길드당 1개 오디오만 재생 가능하므로 다중 화자 큐잉이 필요.
- **언어 자동 라우팅**: 화자별로 서로 다른 목표 언어 매핑.
- **DeepL/Google Translate** 로 번역 백엔드 교체.
