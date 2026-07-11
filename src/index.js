'use strict';

require('dotenv').config();

// prism-media(ffmpeg)가 번들된 ffmpeg-static 바이너리를 쓰도록 경로 지정.
// → 서버에 ffmpeg 를 따로 설치하지 않아도 TTS 오디오 재생이 동작한다.
if (!process.env.FFMPEG_PATH) {
  try {
    process.env.FFMPEG_PATH = require('ffmpeg-static');
  } catch (_) {
    /* ffmpeg-static 미설치 시 시스템 ffmpeg 사용 */
  }
}

const {
  Client,
  GatewayIntentBits,
  Events,
  ChannelType,
  MessageFlags,
} = require('discord.js');
const {
  joinVoiceChannel,
  getVoiceConnection,
  VoiceConnectionStatus,
  entersState,
} = require('@discordjs/voice');

// DAVE(E2EE) 음성 수신 우회 패치를 voice 사용 전에 적용한다.
require('./davePatch');

const { startListening } = require('./voice');
const { attachPlayer, detachPlayer } = require('./playback');
const { LANGUAGES } = require('./languages');

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.GuildMessages,
  ],
});

// 길드별 번역 세션 상태.
// guildId -> { targetCode, targetName, textChannelId, allowedUserId, speak }
const sessions = new Map();

// BOT_NICKNAME 이 설정돼 있으면 해당 길드에서 봇 닉네임을 그 값으로 맞춘다.
async function applyNickname(guild) {
  const nick = process.env.BOT_NICKNAME;
  if (!nick) return;
  try {
    const me = guild.members.me || (await guild.members.fetchMe());
    if (me.nickname !== nick) await me.setNickname(nick);
  } catch (err) {
    console.error(`[nick] 닉네임 설정 실패 (${guild.id}):`, err.message);
  }
}

client.once(Events.ClientReady, async (c) => {
  console.log(`✅ 로그인됨: ${c.user.tag}`);
  for (const guild of c.guilds.cache.values()) await applyNickname(guild);
});

// 새 서버에 초대됐을 때도 닉네임 적용.
client.on(Events.GuildCreate, (guild) => applyNickname(guild));

// 처리되지 않은 예외/거부가 봇 전체를 종료시키지 않도록 안전망.
process.on('unhandledRejection', (reason) => {
  console.error('[unhandledRejection]', reason);
});
process.on('uncaughtException', (err) => {
  console.error('[uncaughtException]', err);
});

// 음성 채널에 사람(봇 제외)이 모두 나가면 봇도 자동 퇴장한다.
client.on(Events.VoiceStateUpdate, async (oldState, newState) => {
  const guild = newState.guild || oldState.guild;
  if (!guild) return;
  const connection = getVoiceConnection(guild.id);
  if (!connection) return;

  const botChannelId = connection.joinConfig.channelId;
  const channel = guild.channels.cache.get(botChannelId);
  if (!channel) return;

  const humans = channel.members.filter((m) => !m.user.bot).size;
  if (humans > 0) return;

  const session = sessions.get(guild.id);
  detachPlayer(guild.id);
  try {
    connection.destroy();
  } catch (_) {
    /* noop */
  }
  sessions.delete(guild.id);
  console.log(`[voice] 사용자 없음 → 자동 퇴장 (${guild.id})`);

  if (session) {
    try {
      const ch = await client.channels.fetch(session.textChannelId);
      await ch.send('👋 음성 채널에 아무도 없어 자동으로 나갔습니다.');
    } catch (_) {
      /* noop */
    }
  }
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  try {
    if (interaction.commandName === 'just-join') return handleJoin(interaction);
    if (interaction.commandName === 'just-leave') return handleLeave(interaction);
    if (interaction.commandName === 'just-setlang') return handleSetLang(interaction);
    if (interaction.commandName === 'just-setsource') return handleSetSource(interaction);
  } catch (err) {
    console.error('[interaction] error:', err);
    const payload = { content: `오류: ${err.message}`, flags: MessageFlags.Ephemeral };
    if (interaction.deferred || interaction.replied) await interaction.followUp(payload);
    else await interaction.reply(payload);
  }
});

async function handleJoin(interaction) {
  const targetCode = interaction.options.getString('language', true);
  const targetUser = interaction.options.getUser('user');
  const speak = interaction.options.getBoolean('speak') ?? false;
  const sourceOpt = interaction.options.getString('source');
  const sourceCode = !sourceOpt || sourceOpt === 'auto' ? null : sourceOpt;

  const voiceChannel = interaction.member?.voice?.channel;
  if (!voiceChannel || voiceChannel.type !== ChannelType.GuildVoice) {
    return interaction.reply({
      content: '먼저 음성 채널에 입장한 뒤 명령을 사용하세요.',
      flags: MessageFlags.Ephemeral,
    });
  }

  await interaction.deferReply();

  const guildId = voiceChannel.guild.id;
  const connection = joinVoiceChannel({
    channelId: voiceChannel.id,
    guildId: voiceChannel.guild.id,
    adapterCreator: voiceChannel.guild.voiceAdapterCreator,
    selfDeaf: false, // 수신하려면 반드시 false
    selfMute: false, // TTS 재생을 위해 false
    debug: !!process.env.VOICE_DEBUG,
  });

  // 음성 연결에서 나는 에러가 프로세스를 죽이지 않도록 반드시 리스너를 단다.
  connection.on('error', (err) => {
    console.error(`[voice] connection error (${guildId}):`, err.message);
  });

  // VOICE_DEBUG=1 일 때 연결 단계별 상태/디버그 로그 (UDP 탐색 vs 암호화 진단용).
  if (process.env.VOICE_DEBUG) {
    let hookedNet = null;
    connection.on('stateChange', (oldS, newS) => {
      console.log(`[voice] state: ${oldS.status} -> ${newS.status}`);
      const net = newS.networking;
      if (net && net !== hookedNet) {
        hookedNet = net;
        net.on('close', (code) =>
          console.log(`[voice] >>> 음성 WS 종료 코드: ${code}`)
        );
        net.on('error', (e) =>
          console.log(`[voice] >>> networking 에러: ${e && e.message}`)
        );
      }
    });
    connection.on('debug', (msg) => console.log('[voice][debug]', msg));
  }

  try {
    await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
  } catch (err) {
    console.error(`[voice] Ready 도달 실패 (${guildId}):`, err.message);
    try {
      detachPlayer(guildId);
    } catch (_) {
      /* noop */
    }
    connection.destroy();
    sessions.delete(guildId);
    return interaction.editReply(
      '⚠️ 음성 채널 연결에 실패했습니다(타임아웃).\n' +
        '서버 방화벽에서 Discord 음성용 **아웃바운드 UDP(50000–65535)** 가 열려 있는지 확인해주세요.'
    );
  }

  sessions.set(guildId, {
    targetCode,
    targetName: LANGUAGES[targetCode] || targetCode,
    textChannelId: interaction.channelId,
    allowedUserId: targetUser ? targetUser.id : null,
    speak,
    sourceCode,
  });

  if (speak) attachPlayer(guildId, connection);
  startListening(connection, {
    client,
    guildId,
    getSession: () => sessions.get(guildId),
  });

  const who = targetUser ? `**${targetUser.username}** 님의 발화만` : '모든 발화를';
  const mode = speak ? '텍스트 + 음성(TTS)' : '텍스트';
  const src = sourceCode ? `입력 언어 **${LANGUAGES[sourceCode]}** 고정, ` : '';
  await interaction.editReply(
    `🎧 **${voiceChannel.name}** 입장 완료.\n` +
      `${src}${who} **${LANGUAGES[targetCode] || targetCode}** (으)로 번역해 ${mode}(으)로 출력합니다.`
  );
}

async function handleSetLang(interaction) {
  const targetCode = interaction.options.getString('language', true);
  const session = sessions.get(interaction.guildId);
  if (!session) {
    return interaction.reply({
      content: '활성화된 번역 세션이 없습니다. 먼저 `/just-join` 으로 시작하세요.',
      flags: MessageFlags.Ephemeral,
    });
  }
  session.targetCode = targetCode;
  session.targetName = LANGUAGES[targetCode] || targetCode;
  await interaction.reply(`🌐 번역 목표 언어를 **${session.targetName}** (으)로 변경했습니다.`);
}

async function handleSetSource(interaction) {
  const opt = interaction.options.getString('source', true);
  const session = sessions.get(interaction.guildId);
  if (!session) {
    return interaction.reply({
      content: '활성화된 번역 세션이 없습니다. 먼저 `/just-join` 으로 시작하세요.',
      flags: MessageFlags.Ephemeral,
    });
  }
  session.sourceCode = opt === 'auto' ? null : opt;
  const label = session.sourceCode ? `**${LANGUAGES[session.sourceCode]}** 고정` : '**자동 감지**';
  await interaction.reply(`🎙️ 입력(말하는) 언어를 ${label} (으)로 설정했습니다.`);
}

async function handleLeave(interaction) {
  const connection = getVoiceConnection(interaction.guildId);
  if (!connection) {
    return interaction.reply({
      content: '현재 음성 채널에 접속해 있지 않습니다.',
      flags: MessageFlags.Ephemeral,
    });
  }
  detachPlayer(interaction.guildId);
  connection.destroy();
  sessions.delete(interaction.guildId);
  await interaction.reply('👋 음성 채널에서 나갔습니다. 번역을 종료합니다.');
}

client.login(process.env.DISCORD_TOKEN);
