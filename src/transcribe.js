'use strict';

const OpenAI = require('openai');
const { toFile } = require('openai');

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

/**
 * WAV 버퍼를 Whisper API 로 전사(STT)한다.
 *
 * @param {Buffer} wavBuffer WAV 컨테이너 오디오
 * @returns {Promise<string>} 인식된 텍스트 (실패/무음 시 빈 문자열)
 */
async function transcribe(wavBuffer) {
  const file = await toFile(wavBuffer, 'audio.wav', { type: 'audio/wav' });

  const res = await openai.audio.transcriptions.create({
    file,
    model: process.env.WHISPER_MODEL || 'whisper-1',
    // language 를 지정하지 않으면 Whisper 가 발화 언어를 자동 감지한다.
    // 특정 화자가 항상 한 언어로만 말한다면 SOURCE_LANG 으로 고정해 정확도를 높일 수 있다.
    ...(process.env.SOURCE_LANG ? { language: process.env.SOURCE_LANG } : {}),
  });

  return (res.text || '').trim();
}

module.exports = { transcribe };
