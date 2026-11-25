#!/usr/bin/env bash
# Daily: put `carry` on the newest upstream nightly the fork has, test it, build it, and queue the
# build for the night swap (carry_swap.sh). Does nothing when that build already exists or runs.
# A rebase conflict or a failing check files one card and stops; `carry` and T3 are not touched.
# Never swaps T3, moves `carry` or pushes. Usage: carry_auto.sh [--dry-run]
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"
DRY=0; [ "${1:-}" = --dry-run ] && DRY=1
exec 9> "$STATE/auto.lock"; flock -n 9 || { echo "another run holds the lock"; exit 0; }

git -C "$REPO" fetch --quiet --tags upstream
git -C "$REPO" fetch --quiet --prune origin
# Only nightlies already on the fork's main: the push guard refuses upstream commits the fork lacks.
TAG=$(git -C "$REPO" tag -l 'v*-nightly.*' --merged origin/main --sort=-v:refname | sed -n 1p)   # not head: it closes the pipe and pipefail sees 141
[ -n "$TAG" ] || { echo "no nightly tag on origin/main"; exit 1; }
SRC=$(git -C "$REPO" rev-parse --verify refs/heads/carry)
KEY="$TAG $SRC"
# installed: "<tag> <carry commit>" of the running build. last: the key last tried, then built|failed.
[ "$(cat "$STATE/installed" 2>/dev/null)" = "$KEY" ] && { echo "T3 already runs carry ${SRC:0:10} on $TAG"; exit 0; }
LAST=$(cat "$STATE/last" 2>/dev/null || true)
case "$LAST" in "$KEY "*) echo "carry ${SRC:0:10} on $TAG: already ${LAST##* }"; exit 0;; esac

n=$(( $(ls -d "$SERVE/v${TAG#v}.scheduler."* 2>/dev/null | sed 's/.*\.//' | sort -n | tail -1) + 1 ))
V=${TAG#v}.scheduler.$n
LOG=$STATE/build/$V.log; mkdir -p "$STATE/build"
REBASE=1; git -C "$REPO" merge-base --is-ancestor "$TAG" "$SRC" && REBASE=0
if [ "$DRY" = 1 ]; then echo "would build $V from carry ${SRC:0:10} (rebase onto $TAG needed: $REBASE)"; exit 0; fi
: > "$LOG"

fail() {
  echo "$KEY failed" > "$STATE/last"
  card "t3code nightly $TAG: the automatic carry build failed" "$1

\`carry\` (${SRC:0:10}) did not move and T3 was not touched. Log on dev-abby: $LOG

\`\`\`
$(tail -n 40 "$LOG" | sed 's/\x1b\[[0-9;]*m//g')
\`\`\`

Fix \`carry\` by hand (docs/fork/CARRY.md) and the next daily run builds it. A newer nightly is tried on its own." || true
  echo "FAILED: $1"; exit 1
}

SHA=$SRC
if [ "$REBASE" = 1 ]; then
  WT=$HOME/scratch/carry-auto/$TAG
  git -C "$REPO" worktree remove --force "$WT" 2>/dev/null || true
  rm -rf "$WT"; git -C "$REPO" worktree prune
  git -C "$REPO" worktree add --quiet --detach "$WT" "$SRC"
  # carry_check.rebase takes the conflict resolutions rerere has recorded and aborts on any new conflict
  conflicts=$(python3 - "$CARRY_WT/docs/fork/bin" "$WT" "$TAG" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import carry_check
print("\n".join(carry_check.rebase(Path(sys.argv[2]), sys.argv[3])))
PY
  )
  SHA=$(git -C "$WT" rev-parse HEAD)
  OLD=$(git -C "$REPO" describe --tags --abbrev=0 --match 'v*-nightly.*' "$SRC")
  [ -n "$conflicts" ] || git -C "$REPO" range-diff "$OLD..$SRC" "$TAG..$SHA" > "$STATE/build/$V.range-diff" || true
  git -C "$REPO" worktree remove --force "$WT"
  [ -z "$conflicts" ] || { echo "rebase conflicts: $conflicts" >> "$LOG"; fail "The rebase onto $TAG stopped on a conflict with no recorded resolution."; }
fi
git -C "$REPO" branch -f carry-next "$SHA"      # keeps the rebased commits until the swap moves carry

"$DIR/carry_build.sh" "$SHA" "$V" >> "$LOG" 2>&1 || fail "Tests, typecheck or build failed for $V."
printf 'V=%s\nSHA=%s\nTAG=%s\nSRC=%s\n' "$V" "$SHA" "$TAG" "$SRC" > "$PENDING"
echo "$KEY built" > "$STATE/last"
echo "built $V from ${SHA:0:10}; carry_swap.sh installs it in the next quiet night hour"
