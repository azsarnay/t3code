#!/usr/bin/env bash
# Test and build one fork commit into an installable archive (CARRY.md steps 3 and 4).
# Usage: carry_build.sh <commit> <version>   e.g. carry_build.sh carry 0.1.12-nightly.20261124.5191.scheduler.3
# Builds in its own worktree, so the `carry` worktree the timers run from stays untouched.
# Result: ~/scratch/release-serve/v<version>/ holding the archive and SHA256SUMS. Exit 0 only when that exists.
set -euo pipefail
REF=$1 V=$2
TAG=v${V%.scheduler.*}
REPO=$HOME/repos/personal/t3code
B=$HOME/repos/personal/t3code-build
OUT=$HOME/scratch/release-cli-$V
SERVE=$HOME/scratch/release-serve/v$V
STOCK=$HOME/scratch/stock-$TAG
N="nice -n 15"
. "$HOME/scratch/tools/env.sh"

step() { echo; echo "=== $(date '+%F %T') $*"; }

SHA=$(git -C "$REPO" rev-parse --verify "$REF^{commit}")
git -C "$REPO" merge-base --is-ancestor "$TAG" "$SHA" || { echo "$REF is not based on $TAG"; exit 2; }

step "worktree at $SHA"
[ -d "$B" ] || git -C "$REPO" worktree add --detach "$B" "$SHA"
cd "$B"
git reset -q --hard
git checkout -q --detach "$SHA"
[ -f .env ] || cp .env.example .env
# The repo pins the Node it builds with (package.json engines.node); fetch that one when it is new.
NODE_V=$(node -p 'require("./package.json").engines.node')
NODE_DIR=$HOME/scratch/tools/node-v$NODE_V-linux-x64
if [ ! -x "$NODE_DIR/bin/node" ]; then
  step "download Node $NODE_V"
  (cd "$HOME/scratch/tools" && f=node-v$NODE_V-linux-x64.tar.xz \
    && curl -fsSLO "https://nodejs.org/dist/v$NODE_V/$f" \
    && curl -fsSL "https://nodejs.org/dist/v$NODE_V/SHASUMS256.txt" | grep " $f\$" | sha256sum -c - \
    && tar -xJf "$f" && rm "$f")
fi
# env.sh puts another checkout's node_modules/.bin on PATH; this worktree's own must win
export PATH="$NODE_DIR/bin:$B/node_modules/.bin:$PATH"

step "install"
$N corepack pnpm@12.14.0 install --frozen-lockfile

step "tests"
$N vp test run --maxWorkers=2 apps/server/src/persistence/Migrations.test.ts apps/server/src/scheduledTasks \
  apps/server/src/orchestration-v2/ThreadLaunchService.test.ts \
  apps/server/src/orchestration-v2/ClaudeAutomaticDelivery.integration.test.ts \
  apps/server/src/mcp/OrchestratorMcpService.test.ts apps/server/src/mcp/OrchestratorMcpService.activity.test.ts \
  packages/contracts/src/scheduledTask.test.ts packages/contracts/src/settings.test.ts \
  packages/shared/src/usageLimits.test.ts apps/server/src/usage/UsageLimitSources.test.ts

step "typecheck"
$N vp run --filter t3 --filter @t3tools/contracts --filter @t3tools/web --concurrency-limit 2 typecheck

step "no migration changes"
[ -z "$(git diff "$TAG" HEAD -- apps/server/src/persistence)" ] || { echo "fork changes touch persistence"; exit 3; }

step "build $V"
node scripts/update-release-package-versions.ts "$V"
$N vp run --filter t3 build
node scripts/apply-web-brand-assets.ts nightly apps/server/dist/client
$N node apps/server/scripts/cli.ts build-exe

step "resource monitor from upstream $TAG"
if [ ! -d "$STOCK/resource-monitor" ]; then
  rm -rf "$STOCK"; mkdir -p "$STOCK"
  (cd "$STOCK" && gh release download "$TAG" -R pingdotgg/t3code \
     -p "t3-${V%.scheduler.*}-linux-x64.tar.gz" -p SHA256SUMS \
   && grep linux-x64 SHA256SUMS | sha256sum -c - \
   && tar -xzf t3-*.tar.gz --strip-components=1 --wildcards '*/resource-monitor/*' && rm t3-*.tar.gz)
fi

step "archive and smoke test"
rm -rf "$OUT"
$N node scripts/build-cli-archive.ts --platform linux --arch x64 --version "$V" \
  --resource-monitor-dir "$STOCK/resource-monitor" --output-dir "$OUT"
node scripts/smoke-cli-archive.ts --archive "$OUT/t3-$V-linux-x64.tar.gz" --expect-version "$V"
mkdir -p "$SERVE" && ln -f "$OUT/t3-$V-linux-x64.tar.gz" "$SERVE/"
(cd "$SERVE" && sha256sum "t3-$V-linux-x64.tar.gz" > SHA256SUMS)
git reset -q --hard

step "BUILT $V from $SHA"
