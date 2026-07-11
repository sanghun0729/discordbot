'use strict';

// Discord DAVE(E2EE) 음성 수신 우회 패치.
//
// Discord가 2026-03 음성에 DAVE(종단간 암호화)를 의무화하면서, 봇이 받는
// 오디오 패킷이 평문(미암호)일 때 @discordjs/voice의 DAVESession.decrypt 가
// passthrough(평문 통과)를 끈 채 복호화를 시도하다 예외를 던져 음성 수신이
// 막힌다. 여기서 DAVESession.decrypt 를 감싸 ① 세션의 passthrough 모드를
// 켜고 ② 그래도 실패하면 원본 패킷(평문)을 그대로 반환해 수신을 유지한다.
//
// require 시점에 전역 프로토타입을 1회 패치한다(부작용 모듈).
try {
  const voice = require('@discordjs/voice');
  const DS = voice.DAVESession;
  if (DS && DS.prototype && DS.prototype.decrypt && !DS.prototype.__patchedPassthrough) {
    const origDecrypt = DS.prototype.decrypt;
    DS.prototype.decrypt = function patchedDecrypt(packet, userId) {
      try {
        // 세션이 (재)생성될 때마다 passthrough 모드를 켠다.
        if (
          this.session &&
          this.session !== this.__ptSession &&
          typeof this.session.setPassthroughMode === 'function'
        ) {
          this.session.setPassthroughMode(true, 0x7fffffff);
          this.__ptSession = this.session;
        }
      } catch (_) {
        /* noop */
      }
      try {
        return origDecrypt.call(this, packet, userId);
      } catch (_) {
        // 복호화 불가(평문 등) → 원본 패킷을 그대로 사용해 수신을 유지.
        return packet;
      }
    };
    DS.prototype.__patchedPassthrough = true;
    console.log('[dave] passthrough 패치 적용됨 (E2EE 음성 수신 우회)');
  }
} catch (err) {
  console.error('[dave] 패치 실패:', err.message);
}

module.exports = {};
