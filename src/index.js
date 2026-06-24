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

client.once(Events.ClientReady, (c) => {
  console.log(`✅ 로그인됨: ${c.user.tag}`);
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  try {
    if (interaction.commandName === 'join') return handleJoin(interaction);
    if (interaction.commandName === 'leave') return handleLeave(interaction);
    if (interaction.commandName === 'setlang') return handleSetLang(interaction);
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
  const speak = interaction.options.getBoolean('speak') ?? true;

  const voiceChannel = interaction.member?.voice?.channel;
  if (!voiceChannel || voiceChannel.type !== ChannelType.GuildVoice) {
    return interaction.reply({
      content: '먼저 음성 채널에 입장한 뒤 명령을 사용하세요.',
      flags: MessageFlags.Ephemeral,
    });
  }

  await interaction.deferReply();

  const connection = joinVoiceChannel({
    channelId: voiceChannel.id,
    guildId: voiceChannel.guild.id,
    adapterCreator: voiceChannel.guild.voiceAdapterCreator,
    selfDeaf: false, // 수신하려면 반드시 false
    selfMute: false, // TTS 재생을 위해 false
  });

  await entersState(connection, VoiceConnectionStatus.Ready, 20_000);

  const guildId = voiceChannel.guild.id;
  sessions.set(guildId, {
    targetCode,
    targetName: LANGUAGES[targetCode] || targetCode,
    textChannelId: interaction.channelId,
    allowedUserId: targetUser ? targetUser.id : null,
    speak,
  });

  if (speak) attachPlayer(guildId, connection);
  startListening(connection, {
    client,
    guildId,
    getSession: () => sessions.get(guildId),
  });

  const who = targetUser ? `**${targetUser.username}** 님의 발화만` : '모든 발화를';
  const mode = speak ? '텍스트 + 음성(TTS)' : '텍스트';
  await interaction.editReply(
    `🎧 **${voiceChannel.name}** 입장 완료.\n` +
      `${who} **${LANGUAGES[targetCode] || targetCode}** (으)로 번역해 ${mode}(으)로 출력합니다.`
  );
}

async function handleSetLang(interaction) {
  const targetCode = interaction.options.getString('language', true);
  const session = sessions.get(interaction.guildId);
  if (!session) {
    return interaction.reply({
      content: '활성화된 번역 세션이 없습니다. 먼저 `/join` 으로 시작하세요.',
      flags: MessageFlags.Ephemeral,
    });
  }
  session.targetCode = targetCode;
  session.targetName = LANGUAGES[targetCode] || targetCode;
  await interaction.reply(`🌐 번역 목표 언어를 **${session.targetName}** (으)로 변경했습니다.`);
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
