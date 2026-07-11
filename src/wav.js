'use strict';

/**
 * 원시(raw) PCM 버퍼를 WAV 파일 버퍼로 감싼다.
 * Discord 음성은 48kHz / 2채널 / 16bit(s16le) PCM 으로 디코딩되며,
 * Whisper API 는 WAV 컨테이너를 그대로 받아들인다.
 *
 * @param {Buffer} pcm        s16le 원시 PCM 데이터
 * @param {number} sampleRate 샘플레이트 (기본 48000)
 * @param {number} channels   채널 수 (기본 2)
 * @param {number} bitDepth   비트 심도 (기본 16)
 * @returns {Buffer}          44바이트 헤더가 붙은 WAV 버퍼
 */
function pcmToWav(pcm, sampleRate = 48000, channels = 2, bitDepth = 16) {
  const byteRate = (sampleRate * channels * bitDepth) / 8;
  const blockAlign = (channels * bitDepth) / 8;

  const header = Buffer.alloc(44);
  header.write('RIFF', 0); // ChunkID
  header.writeUInt32LE(36 + pcm.length, 4); // ChunkSize
  header.write('WAVE', 8); // Format
  header.write('fmt ', 12); // Subchunk1ID
  header.writeUInt32LE(16, 16); // Subchunk1Size (PCM)
  header.writeUInt16LE(1, 20); // AudioFormat (1 = PCM)
  header.writeUInt16LE(channels, 22); // NumChannels
  header.writeUInt32LE(sampleRate, 24); // SampleRate
  header.writeUInt32LE(byteRate, 28); // ByteRate
  header.writeUInt16LE(blockAlign, 32); // BlockAlign
  header.writeUInt16LE(bitDepth, 34); // BitsPerSample
  header.write('data', 36); // Subchunk2ID
  header.writeUInt32LE(pcm.length, 40); // Subchunk2Size

  return Buffer.concat([header, pcm]);
}

module.exports = { pcmToWav };
