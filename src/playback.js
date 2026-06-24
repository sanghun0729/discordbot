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
const managers = new Map(); // guildId -> { player, queue: Buffer[], playing: boolean }

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
  manager.queue.push(wavBuffer);
  if (!manager.playing) playNext(guildId);
}

function playNext(guildId) {
  const manager = managers.get(guildId);
  if (!manager || manager.playing) return;
  const next = manager.queue.shift();
  if (!next) return;

  manager.playing = true;
  // WAV(임의 포맷)는 prism-media를 통해 ffmpeg로 디코딩된다 → 서버에 ffmpeg 필요.
  const resource = createAudioResource(Readable.from(next), {
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
