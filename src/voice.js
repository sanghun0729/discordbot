'use strict';

const prism = require('prism-media');
const { EndBehaviorType } = require('@discordjs/voice');
const { pcmToWav } = require('./wav');
const mlClient = require('./mlClient');
const { enqueueAudio } = require('./playback');

// 48000Hz * 2ch * 2byte = 192000 byte/sec. 약 0.6초 미만 발화는 잡음으로 보고 버린다.
const MIN_PCM_BYTES = Math.floor(192000 * 0.6);

// 길드별 최근 전송 번역문(중복 억제용). guildId -> { text, t }
const lastSent = new Map();

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
      // 발화 종료(무음) 감지 시간. 짧을수록 반응 빠름(기본 600ms, SILENCE_MS로 조정).
      end: {
        behavior: EndBehaviorType.AfterSilence,
        duration: Number(process.env.SILENCE_MS) || 600,
      },
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

    // 부분 원문 표시: 발화가 INTERIM_MS 넘게 이어지면 그때까지 원문을 미리 한 번 표시.
    // 발화 종료 시 이 메시지를 최종 번역으로 편집한다. (경량 스트리밍)
    const INTERIM_MS = Number(process.env.INTERIM_MS ?? 1000);
    let ended = false;
    let interimMsg = null;
    let interimTimer = null;
    if (INTERIM_MS > 0) {
      interimTimer = setTimeout(async () => {
        if (ended) return;
        const partial = Buffer.concat(chunks);
        if (partial.length < MIN_PCM_BYTES) return;
        try {
          const cur = getSession();
          if (!cur) return;
          const { sourceText } = await mlClient.transcribeOnly(
            pcmToWav(partial, 48000, 2)
          );
          if (ended || !sourceText) return;
          const ch =
            client.channels.cache.get(cur.textChannelId) ||
            (await client.channels.fetch(cur.textChannelId));
          interimMsg = await ch.send(`🎤 _${sourceText}…_`);
        } catch (_) {
          /* 부분표시 실패는 최종 결과에 영향 없음 — 무시 */
        }
      }, INTERIM_MS);
    }

    const cleanupInterim = async () => {
      if (interimMsg) {
        try {
          await interimMsg.delete();
        } catch (_) {
          /* noop */
        }
        interimMsg = null;
      }
    };

    pcmStream.on('end', async () => {
      ended = true;
      if (interimTimer) clearTimeout(interimTimer);
      active.delete(userId);
      const pcm = Buffer.concat(chunks);
      if (pcm.length < MIN_PCM_BYTES) return cleanupInterim();

      try {
        const current = getSession();
        if (!current) return cleanupInterim();

        const secs = (pcm.length / 192000).toFixed(1);
        const wav = pcmToWav(pcm, 48000, 2);
        const tMl0 = Date.now();
        // A1: STT+번역만(빠름). TTS는 텍스트 전송 후 비동기로 분리.
        const result = await mlClient.processAudio(
          wav,
          current.targetCode,
          current.sourceCode,
          false
        );
        const mlMs = Date.now() - tMl0;
        if (!result.translated) {
          console.log(`[voice] ${secs}s 음성 → ml=${mlMs}ms (번역없음/스킵)`);
          return cleanupInterim();
        }

        // 짧은 시간 내 동일 번역 반복 억제(환각/중복 발화 방지).
        const prev = lastSent.get(guildId);
        const nowMs = Date.now();
        if (prev && prev.text === result.translated && nowMs - prev.t < 8000) {
          return cleanupInterim();
        }
        lastSent.set(guildId, { text: result.translated, t: nowMs });

        const user =
          client.users.cache.get(userId) || (await client.users.fetch(userId));
        const name = user.globalName || user.username;
        const finalText = `🗣️ **${name}** → ${result.translated}\n> _${result.sourceText}_`;
        // 부분표시 메시지가 있으면 편집, 없으면 새로 전송.
        if (interimMsg) {
          try {
            await interimMsg.edit(finalText);
          } catch (_) {
            const ch =
              client.channels.cache.get(current.textChannelId) ||
              (await client.channels.fetch(current.textChannelId));
            await ch.send(finalText);
          }
        } else {
          const channel =
            client.channels.cache.get(current.textChannelId) ||
            (await client.channels.fetch(current.textChannelId));
          await channel.send(finalText);
        }
        console.log(`[voice] ${secs}s 음성 → ml=${mlMs}ms interim=${interimMsg ? 'Y' : 'N'}`);

        // A1: 음성 출력이 켜져 있으면 TTS를 비동기로 합성·재생(텍스트를 막지 않음).
        if (current.speak) {
          mlClient
            .synthesizeTts(result.translated, current.targetCode)
            .then((audio) => {
              if (audio) enqueueAudio(guildId, audio);
            })
            .catch((e) => console.error(`[voice] TTS 실패:`, e.message));
        }
      } catch (err) {
        await cleanupInterim();
        const cause = err.cause
          ? ` | cause: ${err.cause.code || err.cause.message || err.cause}`
          : '';
        console.error(`[voice] pipeline error (${userId}): ${err.message}${cause}`);
      }
    });
  });
}

module.exports = { startListening };
