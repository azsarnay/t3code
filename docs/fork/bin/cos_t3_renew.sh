#!/usr/bin/env bash
# Daily: renew chief-of-staff's T3 MCP token before its fixed 30 days run out. T3 renews app
# pairings and environment links by itself since 0.1.11, but not MCP client tokens.
# Renews when the token is MAX_AGE_DAYS old or T3 refuses it; otherwise does nothing.
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"
F=$HOME/.config/chief-of-staff/t3-mcp-auth
ORIGIN=https://dev-abby.tail2c01c.ts.net
LOGIN=$HOME/.local/share/zforgez-plugin-sync/current/chief-of-staff/scripts/cos_t3_login.py
MAX_AGE_DAYS=${MAX_AGE_DAYS:-23}

# 200 when T3 accepts the token in file $1 on /mcp
check() {
  curl -s -m 15 -o /dev/null -w '%{http_code}' -H "Authorization: $(cat "$1")" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"cos-t3-renew","version":"1"}}}' \
    "$ORIGIN/mcp" || true
}

age=$(( ($(date +%s) - $(stat -c %Y "$F")) / 86400 ))
code=$(check "$F")
if [ "$age" -lt "$MAX_AGE_DAYS" ] && [ "$code" = 200 ]; then echo "token is $age days old and T3 accepts it"; exit 0; fi
echo "renewing: token is $age days old, T3 answered $code"

renew() {
  local pairing
  pairing=$("$HOME/.t3/runtime/versions/$(running)/t3" auth pairing create --ttl 5m --label "chief-of-staff renew" --json \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["credential"])') || return 1
  cp -p "$F" "$F.prev"
  T3_PAIRING_CODE=$pairing python3 "$LOGIN" --origin "$ORIGIN" "$F" || return 1
  [ "$(check "$F")" = 200 ] || { cp -p "$F.prev" "$F"; echo "T3 refused the new token; the old one is back in place"; return 1; }
}
if out=$(renew 2>&1); then echo "$out"; echo "renewed"; exit 0; fi
echo "$out"
card "Chief-of-staff's T3 token on dev-abby did not renew" "The daily renewal failed. The token in $F is $age days old (T3 ends it at 30) and T3 answered $code to it before the attempt.

\`\`\`
$out
\`\`\`

By hand on dev-abby: \`systemctl --user start cos-t3-renew.service\`, then \`journalctl --user -u cos-t3-renew -n 30\`." || true
exit 1
