'use strict';

const prism = require('prism-media');
const { EndBehaviorType } = require('@discordjs/voice');
const { pcmToWav } = require('./wav');
const mlClient = require('./mlClient');
const { enqueueAudio } = require('./playback');

// 48000Hz * 2ch * 2byte = 192000 byte/sec. 약 0.6초 미만 발화는 잡음으로 보고 버린다.
const MIN_PCM_BYTES = Math.floor(192000 * 0.6);

/**
 * 음성 연결 수신기를 구독해 화자별 발화 단위로
 * 로컬 STT → 번역 → (텍스트 전송 + TTS 재생) 파이프라인을 돌린다.
 *
 * @param {import('@discordjs/voice').VoiceConnection} connection
 * @param {object} ctx
 * @param {import('discord.js').Client} ctx.client
 * @param {string} ctx.guildId
 * @param {() => ({targetCode: string, targetName: string, textChannelId: string, allowedUserId: string|null, speak: boolean})|undefined} ctx.getSession
 */
function startListening(connection, { client, guildId, getSession }) {
  const receiver = connection.receiver;
  const active = new Set(); // 처리 중 화자 중복 구독 방지

  receiver.speaking.on('start', (userId) => {
    const session = getSession();
    if (!session) return;
    if (session.allowedUserId && session.allowedUserId !== userId) return;
    if (active.has(userId)) return;
    active.add(userId);

    const opusStream = receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: 800 },
    });
    // 수신 스트림 자체의 에러(예: DAVE 복호화 실패로 인한 destroy)를 처리해
    // 처리되지 않은 'error' 이벤트로 프로세스가 죽지 않게 한다. pipe는 error를
    // 전파하지 않으므로 opusStream에 직접 리스너를 단다.
    opusStream.on('error', (err) => {
      console.error(`[voice] receive stream error (${userId}):`, err.message);
      active.delete(userId);
    });
    const decoder = new prism.opus.Decoder({
      rate: 48000,
      channels: 2,
      frameSize: 960,
    });

    const chunks = [];
    const pcmStream = opusStream.pipe(decoder);
    pcmStream.on('data', (c) => chunks.push(c));
    pcmStream.on('error', (err) => {
      console.error(`[voice] decode error (${userId}):`, err.message);
      active.delete(userId);
    });

    pcmStream.on('end', async () => {
      active.delete(userId);
      const pcm = Buffer.concat(chunks);
      if (pcm.length < MIN_PCM_BYTES) return;

      try {
        const current = getSession();
        if (!current) return;

        const wav = pcmToWav(pcm, 48000, 2);
        const result = await mlClient.processAudio(wav, current.targetCode);
        if (!result.translated) return;

        const user = await client.users.fetch(userId);
        const name = user.globalName || user.username;
        const channel = await client.channels.fetch(current.textChannelId);
        await channel.send(
          `🗣️ **${name}** → ${result.translated}\n> _${result.sourceText}_`
        );

        // 음성(TTS) 출력이 켜져 있고 합성 오디오가 있으면 음성 채널에 재생.
        if (current.speak && result.audio) {
          enqueueAudio(guildId, result.audio);
        }
      } catch (err) {
        // fetch 실패의 실제 원인(err.cause)까지 출력해 진단을 돕는다.
        const cause = err.cause
          ? ` | cause: ${err.cause.code || err.cause.message || err.cause}`
          : '';
        console.error(`[voice] pipeline error (${userId}): ${err.message}${cause}`);
      }
    });
  });
}

module.exports = { startListening };
