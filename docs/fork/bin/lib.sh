# Shared by carry_auto.sh, carry_swap.sh and cos_t3_renew.sh. Sourced, not run.
export PATH=$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin
DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")   # docs/fork/bin on carry
STATE=$HOME/.local/state/t3code-fork
REPO=$HOME/repos/personal/t3code
CARRY_WT=$HOME/repos/personal/t3code-carry
SERVE=$HOME/scratch/release-serve
PENDING=$STATE/pending            # the build waiting for a quiet night hour: V=, SHA=, TAG=, SRC= lines
SERVICE_STATE=$HOME/.t3/runtime/service-state.json
mkdir -p "$STATE"

running() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["activeVersion"])' "$SERVICE_STATE"; }

# card "<title>" "<body>": a queue card parked as needs-input, so Abby sees it and no agent picks it up.
card() {
  local q path
  q=$(zplug taskboard zconfig.py load taskboard \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["vaultRoot"] + "/" + d["tasksSubdir"])') || return 1
  path=$(printf '%s\n%s\n' "$1" "$2" | zplug taskboard taskboard.py add --dir "$q" --priority high --stdin) || return 1
  path=${path#queued: }
  zplug taskboard taskboard.py status "$path" started </dev/null || return 1
  printf '%s' '{"questions":[{"id":"close","text":"This is a report from the T3 automation on dev-abby. Close it?","options":[{"label":"Close it","recommended":true},{"label":"Keep it open"}]}]}' \
    | zplug taskboard taskboard.py ask "$path" --stdin
}
