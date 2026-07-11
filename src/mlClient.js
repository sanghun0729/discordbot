'use strict';

const ML_SERVICE_URL = process.env.ML_SERVICE_URL || 'http://127.0.0.1:8000';

/**
 * 로컬 Python ML 사이드카(/process)에 WAV 오디오를 보내
 * STT + 번역 + (선택)TTS 결과를 받는다.
 *
 * @param {Buffer} wavBuffer  s16le WAV 오디오
 * @param {string} targetCode 목표 언어 코드 (예: 'ko', 'en')
 * @returns {Promise<{sourceText: string, sourceLang: string, translated: string, audio: Buffer|null}>}
 */
async function processAudio(wavBuffer, targetCode, sourceCode) {
  const form = new FormData();
  form.append('target', targetCode);
  if (sourceCode) form.append('source', sourceCode); // 입력 언어 고정(생략 시 자동 감지)
  form.append(
    'file',
    new Blob([wavBuffer], { type: 'audio/wav' }),
    'audio.wav'
  );

  const res = await fetch(`${ML_SERVICE_URL}/process`, {
    method: 'POST',
    body: form,
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`ML 서비스 오류 ${res.status}: ${detail.slice(0, 200)}`);
  }

  const json = await res.json();
  return {
    sourceText: json.source_text || '',
    sourceLang: json.source_lang || '',
    translated: json.translated_text || '',
    audio: json.audio_b64 ? Buffer.from(json.audio_b64, 'base64') : null,
  };
}

module.exports = { processAudio, ML_SERVICE_URL };
