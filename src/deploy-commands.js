'use strict';

require('dotenv').config();

const { REST, Routes, SlashCommandBuilder } = require('discord.js');
const { languageChoices } = require('./languages');

const commands = [
  new SlashCommandBuilder()
    .setName('just-join')
    .setDescription('음성 채널에 입장해 발화를 번역합니다.')
    .addStringOption((opt) =>
      opt
        .setName('language')
        .setDescription('번역할 목표 언어')
        .setRequired(true)
        .addChoices(...languageChoices())
    )
    .addUserOption((opt) =>
      opt
        .setName('user')
        .setDescription('이 사용자의 발화만 번역 (생략 시 전체)')
        .setRequired(false)
    )
    .addBooleanOption((opt) =>
      opt
        .setName('speak')
        .setDescription('번역문을 음성(TTS)으로도 재생 (기본: 꺼짐)')
        .setRequired(false)
    )
    .toJSON(),
  new SlashCommandBuilder()
    .setName('just-setlang')
    .setDescription('번역 목표 언어를 변경합니다.')
    .addStringOption((opt) =>
      opt
        .setName('language')
        .setDescription('새 목표 언어')
        .setRequired(true)
        .addChoices(...languageChoices())
    )
    .toJSON(),
  new SlashCommandBuilder()
    .setName('just-leave')
    .setDescription('음성 채널에서 나가고 번역을 종료합니다.')
    .toJSON(),
];

const rest = new REST({ version: '10' }).setToken(process.env.DISCORD_TOKEN);

(async () => {
  try {
    const clientId = process.env.CLIENT_ID;
    const guildId = process.env.GUILD_ID;

    // DEPLOY_GUILD=1 이면 지정 길드에만(즉시 반영, 개발용). 기본은 전역 등록.
    if (process.env.DEPLOY_GUILD === '1' && guildId) {
      await rest.put(Routes.applicationGuildCommands(clientId, guildId), { body: commands });
      console.log(`✅ 길드(${guildId}) 전용 명령 등록 완료 (그 서버에서만 즉시 사용).`);
      return;
    }

    // 전역 등록: 봇이 들어간 모든 서버에서 사용 가능 (반영까지 최대 1시간, 보통 수 분).
    await rest.put(Routes.applicationCommands(clientId), { body: commands });
    console.log('✅ 전역 슬래시 명령 등록 완료 (모든 서버, 반영까지 시간이 걸릴 수 있음).');

    // 과거 길드 전용 등록이 남아 중복으로 보이지 않게 정리.
    if (guildId) {
      await rest.put(Routes.applicationGuildCommands(clientId, guildId), { body: [] });
      console.log(`🧹 길드(${guildId}) 전용 명령 정리 완료(중복 방지).`);
    }
  } catch (err) {
    console.error('명령 등록 실패:', err);
    process.exit(1);
  }
})();
