'use strict';

const prism = require('prism-media');
const { EndBehaviorType } = require('@discordjs/voice');
const { pcmToWav } = require('./wav');
const { transcribe } = require('./transcribe');
const { translate } = require('./translate');

// 너무 짧은 발화(잡음/기침 등)는 버린다. 48000Hz * 2ch * 2byte = 192000 byte/sec.
// 약 0.6초 미만이면 무시.
const MIN_PCM_BYTES = Math.floor(192000 * 0.6);

/**
 * 음성 연결의 수신기를 구독해, 각 화자의 발화 단위로
 * STT → 번역 → 텍스트 채팅 전송 파이프라인을 돌린다.
 *
 * @param {import('@discordjs/voice').VoiceConnection} connection
 * @param {object} ctx
 * @param {import('discord.js').Client} ctx.client
 * @param {() => {targetLang: string, textChannelId: string, allowedUserId: string|null}} ctx.getSession
 */
function startListening(connection, { client, getSession }) {
  const receiver = connection.receiver;

  // 동일 화자가 말하는 도중 speaking 'start' 가 여러 번 발생할 수 있으므로
  // 처리 중인 화자는 중복 구독하지 않도록 추적한다.
  const active = new Set();

  receiver.speaking.on('start', (userId) => {
    const session = getSession();
    if (!session) return;

    // 특정 사용자만 번역하도록 설정된 경우 그 외 화자는 무시.
    if (session.allowedUserId && session.allowedUserId !== userId) return;

    if (active.has(userId)) return;
    active.add(userId);

    const opusStream = receiver.subscribe(userId, {
      end: {
        behavior: EndBehaviorType.AfterSilence,
        duration: 800, // 0.8초 무음이면 한 발화의 끝으로 간주
      },
    });

    const decoder = new prism.opus.Decoder({
      rate: 48000,
      channels: 2,
      frameSize: 960,
    });

    const chunks = [];
    const pcmStream = opusStream.pipe(decoder);

    pcmStream.on('data', (chunk) => chunks.push(chunk));

    pcmStream.on('error', (err) => {
      console.error(`[voice] decode error (${userId}):`, err.message);
      active.delete(userId);
    });

    pcmStream.on('end', async () => {
      active.delete(userId);
      const pcm = Buffer.concat(chunks);
      if (pcm.length < MIN_PCM_BYTES) return; // 너무 짧으면 스킵

      try {
        const wav = pcmToWav(pcm, 48000, 2);
        const text = await transcribe(wav);
        if (!text) return;

        const current = getSession();
        if (!current) return;

        const translated = await translate(text, current.targetLang);
        if (!translated) return;

        const channel = await client.channels.fetch(current.textChannelId);
        const user = await client.users.fetch(userId);
        const name = user.globalName || user.username;

        await channel.send(`🗣️ **${name}** → ${translated}\n> _${text}_`);
      } catch (err) {
        console.error(`[voice] pipeline error (${userId}):`, err.message);
      }
    });
  });
}

module.exports = { startListening };
