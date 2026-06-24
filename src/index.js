'use strict';

require('dotenv').config();

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

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.GuildMessages,
  ],
});

// 길드별 번역 세션 상태.
// guildId -> { targetLang, textChannelId, allowedUserId }
const sessions = new Map();

client.once(Events.ClientReady, (c) => {
  console.log(`✅ 로그인됨: ${c.user.tag}`);
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;

  try {
    if (interaction.commandName === 'join') {
      await handleJoin(interaction);
    } else if (interaction.commandName === 'leave') {
      await handleLeave(interaction);
    }
  } catch (err) {
    console.error('[interaction] error:', err);
    if (interaction.deferred || interaction.replied) {
      await interaction.followUp({
        content: `오류가 발생했습니다: ${err.message}`,
        flags: MessageFlags.Ephemeral,
      });
    } else {
      await interaction.reply({
        content: `오류가 발생했습니다: ${err.message}`,
        flags: MessageFlags.Ephemeral,
      });
    }
  }
});

async function handleJoin(interaction) {
  const targetLang = interaction.options.getString('language', true);
  const targetUser = interaction.options.getUser('user'); // 선택: 특정 사용자만 번역

  const member = interaction.member;
  const voiceChannel = member?.voice?.channel;

  if (!voiceChannel || voiceChannel.type !== ChannelType.GuildVoice) {
    await interaction.reply({
      content: '먼저 음성 채널에 입장한 뒤 명령을 사용하세요.',
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await interaction.deferReply();

  const connection = joinVoiceChannel({
    channelId: voiceChannel.id,
    guildId: voiceChannel.guild.id,
    adapterCreator: voiceChannel.guild.voiceAdapterCreator,
    selfDeaf: false, // 음성을 수신하려면 반드시 false
    selfMute: true,
  });

  await entersState(connection, VoiceConnectionStatus.Ready, 20_000);

  sessions.set(voiceChannel.guild.id, {
    targetLang,
    textChannelId: interaction.channelId,
    allowedUserId: targetUser ? targetUser.id : null,
  });

  startListening(connection, {
    client,
    getSession: () => sessions.get(voiceChannel.guild.id),
  });

  const who = targetUser ? `**${targetUser.username}** 님의 발화만` : '모든 발화를';
  await interaction.editReply(
    `🎧 **${voiceChannel.name}** 에 입장했습니다.\n` +
      `${who} **${targetLang}** (으)로 번역해 이 채널에 올립니다.`
  );
}

async function handleLeave(interaction) {
  const connection = getVoiceConnection(interaction.guildId);
  if (!connection) {
    await interaction.reply({
      content: '현재 음성 채널에 접속해 있지 않습니다.',
      flags: MessageFlags.Ephemeral,
    });
    return;
  }
  connection.destroy();
  sessions.delete(interaction.guildId);
  await interaction.reply('👋 음성 채널에서 나갔습니다. 번역을 종료합니다.');
}

client.login(process.env.DISCORD_TOKEN);
