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
async function processAudio(wavBuffer, targetCode, sourceCode = null, speak = false) {
  const form = new FormData();
  form.append('target', targetCode);
  if (sourceCode) form.append('source', sourceCode); // 입력 언어 고정(생략 시 자동 감지)
  form.append('speak', speak ? '1' : '0'); // 음성 필요할 때만 TTS 생성 요청
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

/**
 * 전사만(번역/TTS 없이) 빠르게 — 발화 중 부분 원문 표시용.
 * @returns {Promise<{sourceText: string, sourceLang: string}>}
 */
async function transcribeOnly(wavBuffer) {
  const form = new FormData();
  form.append('file', new Blob([wavBuffer], { type: 'audio/wav' }), 'audio.wav');
  const res = await fetch(`${ML_SERVICE_URL}/stt`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(`STT 서비스 오류 ${res.status}`);
  const json = await res.json();
  return { sourceText: json.source_text || '', sourceLang: json.source_lang || '' };
}

/**
 * 번역문을 TTS로 합성(별도 호출). A1: 텍스트 전송을 막지 않도록 비동기로 사용.
 * @returns {Promise<Buffer|null>}
 */
async function synthesizeTts(text, targetCode) {
  const form = new FormData();
  form.append('text', text);
  form.append('target', targetCode);
  const res = await fetch(`${ML_SERVICE_URL}/tts`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(`TTS 서비스 오류 ${res.status}`);
  const json = await res.json();
  return json.audio_b64 ? Buffer.from(json.audio_b64, 'base64') : null;
}

module.exports = { processAudio, transcribeOnly, synthesizeTts, ML_SERVICE_URL };
