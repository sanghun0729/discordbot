'use strict';

const OpenAI = require('openai');

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

/**
 * GPT 로 텍스트를 목표 언어로 번역한다.
 *
 * @param {string} text       원문
 * @param {string} targetLang 목표 언어 (예: "English", "Korean", "Japanese")
 * @returns {Promise<string>} 번역문
 */
async function translate(text, targetLang) {
  const res = await openai.chat.completions.create({
    model: process.env.TRANSLATE_MODEL || 'gpt-4o-mini',
    temperature: 0.2,
    messages: [
      {
        role: 'system',
        content:
          `You are a professional real-time translation engine. ` +
          `Translate the user's message into ${targetLang}. ` +
          `Preserve the tone and meaning. ` +
          `If the text is already in ${targetLang}, return it unchanged. ` +
          `Output ONLY the translation with no quotes, no explanations, no extra text.`,
      },
      { role: 'user', content: text },
    ],
  });

  return (res.choices[0]?.message?.content || '').trim();
}

module.exports = { translate };
