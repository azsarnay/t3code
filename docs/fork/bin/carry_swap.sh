#!/usr/bin/env bash
# Night swap: install the build carry_auto.sh queued, once the fleet is quiet. Restarts T3.
# t3's own updater backs up the database and returns to the previous build when the new one does
# not start; this script reports which build ended up running. On success it repoints
# ~/.local/bin/t3, moves `carry` to the built commit, tags it and pushes both to the fork.
# Exit 75 = fleet not quiet; the timer tries again. Usage: carry_swap.sh [--dry-run]
set -u
. "$(dirname "$(readlink -f "$0")")/lib.sh"
BUSY_CPU_PCT=3         # a T3 claude above this CPU share over the sample counts as busy
RECENT_MIN=10          # a T3 claude whose transcript changed within this many minutes counts as busy
SAMPLE=30
LOG=$STATE/swap.log
DRY=0; [ "${1:-}" = --dry-run ] && DRY=1
[ -f "$PENDING" ] || exit 0
. "$PENDING"           # V, SHA, TAG, SRC

log() { echo "$(date '+%F %T') $*" >> "$LOG"; [ "$DRY" = 0 ] || echo "$*"; }

exec 9> "$STATE/swap.lock"
flock -n 9 || { log "another attempt holds the lock"; exit 75; }
log "attempt start (dry-run=$DRY, V=$V)"

# --- quiet check (from swap_tonight.sh, the 2026-11-13 swap) ---
busy=()
exec_units=$(systemctl --user list-units --no-legend --plain --state=active 'taskboard-execution-*' | awk '{print $1}')
[ -n "$exec_units" ] && busy+=("watcher card run in flight: $(echo $exec_units)")
systemctl --user is-active -q taskboard-land.service && busy+=("lander running")
wcg=/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/app.slice/taskboard-queue-watcher.service/cgroup.procs
for p in $(cat "$wcg" 2>/dev/null); do
  case "$(cat /proc/$p/comm 2>/dev/null)" in claude|codex) busy+=("watcher child $p ($(cat /proc/$p/comm)) running");; esac
done

# claude/codex outside T3 (owner ticks, cos commands, card runs) are busy; T3 ones only when working.
declare -A t0
agents=$(pgrep -x 'claude|codex')
for p in $agents; do t0[$p]=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null); done
sleep "$SAMPLE"
hz=$(getconf CLK_TCK) nbusy=0 who=()
for p in $agents; do
  [ -r /proc/$p/stat ] || continue
  cg=$(cat /proc/$p/cgroup) comm=$(cat /proc/$p/comm)
  pct=$(( ($(awk '{print $14+$15}' /proc/$p/stat) - ${t0[$p]:-0}) * 100 / (hz * SAMPLE) ))
  # A claude waiting on the model or a tool uses no CPU, so also look for a tool shell and a fresh transcript.
  tool=$(ps --ppid "$p" -o args= | grep -c '^/bin/bash -c')
  sid=$(tr '\0' ' ' < /proc/$p/cmdline | grep -oE -- '--(session-id|resume)[= ][0-9a-f-]{36}' | tail -c 37)
  fresh=0; [ -n "$sid" ] && [ -n "$(find "$HOME/.claude/projects" -maxdepth 2 -name "$sid.jsonl" -mmin -$RECENT_MIN)" ] && fresh=1
  log "agent $comm $p cpu=${pct}% tool=$tool fresh=$fresh cgroup=${cg##*/}"
  if [[ $cg != *app-t3code* ]] || [ "$pct" -ge "$BUSY_CPU_PCT" ] || [ "$tool" -gt 0 ] || [ "$fresh" = 1 ]; then
    nbusy=$((nbusy+1)); who+=("$comm/$p/${pct}%/${cg##*/}")
  fi
done
# one busy agent is allowed, as on 2026-11-13: with none allowed the fleet is never quiet
[ "$nbusy" -gt 1 ] && busy+=("$nbusy busy agent processes: ${who[*]}")

if [ ${#busy[@]} -gt 0 ]; then log "not quiet: ${busy[*]}"; exit 75; fi
log "fleet quiet ($nbusy busy agent)"

# --- swap ---
give_up() {   # this build is not tried again; the next daily build replaces it
  mv -f "$PENDING" "$STATE/pending.failed"
  log "FAILED: $1"
  card "t3code swap to $V failed" "$1

T3 on dev-abby now runs \`$(running 2>/dev/null)\` and t3code.service is \`$(systemctl --user is-active t3code.service)\`. Log on dev-abby: $LOG

Try again by hand: \`mv $STATE/pending.failed $PENDING\` and \`systemctl --user start t3code-carry-swap.service\`." || true
  exit 1
}

cur=$(running) || give_up "cannot read $SERVICE_STATE (nothing changed)"
T3=$HOME/.t3/runtime/versions/$cur/t3
[ -x "$T3" ] || give_up "$T3 is missing (nothing changed)"
(cd "$SERVE/v$V" && sha256sum -c --quiet SHA256SUMS) >> "$LOG" 2>&1 || give_up "archive checksum mismatch (nothing changed)"
if [ "$DRY" = 1 ]; then log "quiet; would run $T3 update $V -y"; exit 0; fi

if [ "$cur" != "$V" ]; then
  cd "$SERVE" || give_up "no $SERVE (nothing changed)"
  python3 -m http.server 8799 --bind 127.0.0.1 >> "$LOG" 2>&1 9>&- & HTTP=$!
  for _ in $(seq 20); do curl -fsS -o /dev/null "http://127.0.0.1:8799/v$V/SHA256SUMS" && break; sleep 0.5; done
  log "running $T3 update $V -y (from $cur)"
  T3CODE_HOME=$HOME/.t3 T3CODE_RELEASE_BASE_URL=http://127.0.0.1:8799 "$T3" update "$V" -y >> "$LOG" 2>&1 9>&-
  rc=$?
  log "t3 update exit $rc"
  ok=0
  for _ in $(seq 60); do
    systemctl --user is-active -q t3code.service && [ "$(running 2>/dev/null)" = "$V" ] \
      && curl -fsS -m 5 -o /dev/null http://127.0.0.1:3773/ && { ok=1; break; }
    sleep 3
  done
  kill $HTTP 2>/dev/null
  [ "$ok" = 1 ] || give_up "After \`t3 update $V\` (exit $rc) the new build is not the one serving. t3's updater returns to the previous build on its own when the new one does not start."
fi

# --- the swap worked: record it, then move carry and push ---
log "OK $V (was $cur)"
ln -sfn "$HOME/.t3/runtime/versions/$V/t3" "$HOME/.local/bin/t3"
echo "$TAG $SHA" > "$STATE/installed"
rm -f "$PENDING"

git_step() {
  old=$(git -C "$REPO" rev-parse --verify refs/heads/carry) || return 1
  # Move carry only when it is still the commit this build started from. If someone committed to
  # carry since, leave it: the next daily run builds that carry.
  if [ "$old" != "$SHA" ] && [ "$old" = "${SRC:-}" ]; then
    [ -z "$(git -C "$CARRY_WT" status --porcelain)" ] || { echo "$CARRY_WT has uncommitted changes"; return 1; }
    git -C "$REPO" branch -f carry-prev "$old" || return 1
    git -C "$CARRY_WT" reset -q --hard "$SHA" || return 1
  fi
  git -C "$REPO" tag -f "ours/v$V" "$SHA" || return 1
  git -C "$REPO" fetch --quiet origin || return 1
  git -C "$REPO" push --quiet --force-with-lease=carry origin carry +carry-prev "ours/v$V"
}
if out=$(git_step 2>&1); then
  log "carry is ${SHA:0:10}, tagged ours/v$V, pushed to the fork"
else
  log "git step failed: $out"
  card "t3code $V is running, but carry was not moved or pushed" "The swap to \`$V\` worked. Moving \`carry\` to ${SHA:0:10}, tagging \`ours/v$V\` or pushing to the fork failed:

\`\`\`
$out
\`\`\`

The built commit is on branch \`carry-next\` in ~/repos/personal/t3code. Finish by hand with docs/fork/CARRY.md step 6." || true
fi
exit 0
