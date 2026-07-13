'use strict';

// 지원 언어: ISO-639-1 코드 -> 표시 이름.
// faster-whisper(감지) / argostranslate(번역) / piper(TTS) 모두 이 코드 체계를 따른다.
const LANGUAGES = {
  ko: '한국어',
  en: 'English',
  ja: '日本語',
  zh: '中文',
  es: 'Español',
  fr: 'Français',
  de: 'Deutsch',
  vi: 'Tiếng Việt',
};

// 슬래시 명령의 선택지 형태로 변환.
function languageChoices() {
  return Object.entries(LANGUAGES).map(([value, name]) => ({ name, value }));
}

// 입력(화자) 언어 선택지 — 맨 앞에 '자동 감지'(value: 'auto') 추가.
function sourceChoices() {
  return [{ name: '자동 감지', value: 'auto' }, ...languageChoices()];
}

module.exports = { LANGUAGES, languageChoices, sourceChoices };
