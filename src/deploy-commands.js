'use strict';

require('dotenv').config();

const { REST, Routes, SlashCommandBuilder } = require('discord.js');

const commands = [
  new SlashCommandBuilder()
    .setName('join')
    .setDescription('음성 채널에 입장해 발화를 번역합니다.')
    .addStringOption((opt) =>
      opt
        .setName('language')
        .setDescription('번역할 목표 언어 (예: English, Korean, Japanese)')
        .setRequired(true)
        .addChoices(
          { name: '한국어 (Korean)', value: 'Korean' },
          { name: 'English', value: 'English' },
          { name: '日本語 (Japanese)', value: 'Japanese' },
          { name: '中文 (Chinese)', value: 'Chinese' },
          { name: 'Español (Spanish)', value: 'Spanish' },
          { name: 'Français (French)', value: 'French' }
        )
    )
    .addUserOption((opt) =>
      opt
        .setName('user')
        .setDescription('이 사용자의 발화만 번역 (생략 시 전체)')
        .setRequired(false)
    )
    .toJSON(),
  new SlashCommandBuilder()
    .setName('leave')
    .setDescription('음성 채널에서 나가고 번역을 종료합니다.')
    .toJSON(),
];

const rest = new REST({ version: '10' }).setToken(process.env.DISCORD_TOKEN);

(async () => {
  try {
    const clientId = process.env.CLIENT_ID;
    const guildId = process.env.GUILD_ID;

    if (guildId) {
      // 특정 서버에만 등록 — 즉시 반영(개발용 권장)
      await rest.put(Routes.applicationGuildCommands(clientId, guildId), {
        body: commands,
      });
      console.log(`✅ 길드(${guildId}) 슬래시 명령 등록 완료.`);
    } else {
      // 전역 등록 — 반영까지 최대 1시간 소요
      await rest.put(Routes.applicationCommands(clientId), { body: commands });
      console.log('✅ 전역 슬래시 명령 등록 완료 (반영까지 시간이 걸릴 수 있습니다).');
    }
  } catch (err) {
    console.error('명령 등록 실패:', err);
    process.exit(1);
  }
})();
