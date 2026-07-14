'use strict';

const { Readable } = require('node:stream');
const {
  createAudioPlayer,
  createAudioResource,
  StreamType,
  AudioPlayerStatus,
  NoSubscriberBehavior,
} = require('@discordjs/voice');

// 길드별 재생 관리자.
// 봇은 길드당 동시에 1개 오디오만 재생 가능하므로 큐로 직렬화한다.
const managers = new Map(); // guildId -> { player, queue: {buf,t}[], playing: boolean }

// 백로그 제어: 말이 많아 TTS가 밀리면 오디오가 실제 대화보다 뒤처져 흐름이 끊긴다.
// 대기 큐 최대 길이(초과 시 오래된 것 폐기) + 대기 오디오 최대 수명(초과 시 스킵).
const TTS_QUEUE_MAX = Number(process.env.TTS_QUEUE_MAX) || 3;
const TTS_MAX_AGE_MS = Number(process.env.TTS_MAX_AGE_MS) || 8000;

/**
 * 음성 연결에 오디오 플레이어를 붙이고 관리자를 생성한다.
 * @param {string} guildId
 * @param {import('@discordjs/voice').VoiceConnection} connection
 */
function attachPlayer(guildId, connection) {
  const player = createAudioPlayer({
    behaviors: { noSubscriber: NoSubscriberBehavior.Pause },
  });

  const manager = { player, queue: [], playing: false };
  managers.set(guildId, manager);

  player.on(AudioPlayerStatus.Idle, () => {
    manager.playing = false;
    playNext(guildId);
  });
  player.on('error', (err) => {
    console.error(`[playback] player error (${guildId}):`, err.message);
    manager.playing = false;
    playNext(guildId);
  });

  connection.subscribe(player);
  return manager;
}

/**
 * 합성된 WAV 오디오를 재생 큐에 넣는다.
 * @param {string} guildId
 * @param {Buffer} wavBuffer
 */
function enqueueAudio(guildId, wavBuffer) {
  const manager = managers.get(guildId);
  if (!manager) return;
  // 큐가 이미 꽉 찼으면 가장 오래된 대기 오디오를 버리고 최신 발화를 우선한다.
  while (manager.queue.length >= TTS_QUEUE_MAX) {
    manager.queue.shift();
    console.log(`[playback] 큐 초과 → 오래된 TTS 스킵 (${guildId})`);
  }
  manager.queue.push({ buf: wavBuffer, t: Date.now() });
  if (!manager.playing) playNext(guildId);
}

function playNext(guildId) {
  const manager = managers.get(guildId);
  if (!manager || manager.playing) return;
  // 대기 중 너무 오래 묵은 오디오는 건너뛴다(실제 대화와 동기 유지).
  let item = manager.queue.shift();
  while (item && Date.now() - item.t > TTS_MAX_AGE_MS) {
    console.log(
      `[playback] 오래된 TTS(${((Date.now() - item.t) / 1000).toFixed(1)}s) 스킵 (${guildId})`
    );
    item = manager.queue.shift();
  }
  if (!item) return;

  manager.playing = true;
  // WAV(임의 포맷)는 prism-media를 통해 ffmpeg로 디코딩된다 → 서버에 ffmpeg 필요.
  const resource = createAudioResource(Readable.from(item.buf), {
    inputType: StreamType.Arbitrary,
  });
  manager.player.play(resource);
}

function detachPlayer(guildId) {
  const manager = managers.get(guildId);
  if (!manager) return;
  try {
    manager.player.stop(true);
  } catch (_) {
    /* noop */
  }
  managers.delete(guildId);
}

module.exports = { attachPlayer, enqueueAudio, detachPlayer };
