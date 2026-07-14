#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 코드 전용 백업 → GitHub의 code-backup 브랜치로 push.
#   · 대용량 모델(ml-service/models)·비밀(.env)은 자동 제외 (.gitignore 준수)
#   · 커밋하지 않은 변경까지 포함해 "현재 코드 상태 전체"를 백업
#   · 이전 code-backup을 부모로 이어붙여 백업 히스토리를 유지
#   · 자격증명(credential.helper store)이 저장돼 있으면 프롬프트 없이 push
#
# 사용:  ~/discordbot/backup.sh ["백업 메모"]
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

MSG="${1:-code backup $(date '+%F %T')}"
TMPIDX="$(mktemp)"
trap 'rm -f "$TMPIDX"' EXIT

echo "▶ 현재 코드 상태 수집 중..."
# 1) HEAD 기준 임시 인덱스에 워킹트리 전체 반영(.gitignore 준수 → models/.env 자동 제외)
GIT_INDEX_FILE="$TMPIDX" git read-tree HEAD
GIT_INDEX_FILE="$TMPIDX" git add -A
# 안전장치: 혹시 과거에 추적되던 대용량/비밀 파일이 남아 있으면 확실히 제외
GIT_INDEX_FILE="$TMPIDX" git rm -r --cached -q ml-service/models 2>/dev/null || true
GIT_INDEX_FILE="$TMPIDX" git rm --cached -q .env ml-service/.env 2>/dev/null || true
TREE="$(GIT_INDEX_FILE="$TMPIDX" git write-tree)"

# 2) 대용량 파일 안전장치: 50MB 초과 blob이 있으면 push 전에 중단
BIG="$(git ls-tree -r -l "$TREE" | awk '$4>52428800{printf "  %.0fMB  %s\n",$4/1048576,$5}')"
if [ -n "$BIG" ]; then
  echo "❌ 50MB 초과 파일이 포함되어 중단합니다 (GitHub 100MB 제한):"
  echo "$BIG"
  exit 1
fi

# 3) 이전 code-backup을 부모로 커밋(히스토리 유지). 없으면 최초 루트 커밋.
PARENT="$(git rev-parse -q --verify code-backup || true)"
if [ -n "$PARENT" ]; then
  COMMIT="$(git commit-tree "$TREE" -p "$PARENT" -m "$MSG")"
else
  COMMIT="$(git commit-tree "$TREE" -m "$MSG")"
fi
git branch -f code-backup "$COMMIT"

# 4) GitHub로 push (code-backup 전용 백업 브랜치 → --force로 원격을 로컬에 맞춤)
echo "▶ GitHub로 push 중..."
git push --force origin code-backup

FILES="$(git ls-tree -r --name-only "$TREE" | wc -l)"
echo "✅ 백업 완료: \"$MSG\"  (파일 $FILES개)"
git log --oneline -1 code-backup
