# Carrying fork changes

This fork, `azsarnay/t3code`, keeps its own changes on one branch, `carry`, on top of a stock upstream nightly tag. They stay on the fork. They are never sent to `pingdotgg/t3code` as a pull request and never pushed to the `upstream` remote. The `carry` to work on is the one in `~/repos/personal/t3code`, checked out in its worktree `~/repos/personal/t3code-carry`; do every step below there. The installed build came from it. `~/scratch/t3code-sro` has an older `carry`; do not rebase or build from it. In both clones, `gh` defaults to `azsarnay/t3code` and the `upstream` push URL is set to `DISABLED-fork-only`.

- `main` on the fork is a plain copy of upstream `main`. Never commit to it; sync it with `gh repo sync azsarnay/t3code`.
- `carry` holds one commit per change, plus this file. Rebase it onto each new nightly tag.
- Every build that runs on dev-abby gets a tag, `ours/v<version>`, on the commit it was built from, pushed to the fork.

Today `carry` holds two changes: the scheduled-run-outcomes fix, and account names for hub usage sources (`accountNames` on a usage limit source in settings). Upstream `main` had neither on 2026-11-24: it still marks a scheduled run `succeeded` when the message is dispatched. The scheduler fix adds no numbered migration: `apps/server/src/scheduledTasks/scheduledTaskOutcomeSchema.ts` adds its columns at service start, so the database's migration history stays the same as stock.

The build tools live outside the repo. Load them first with `. ~/scratch/tools/env.sh` (Node 26.12.1, pnpm through corepack, patchelf, and the `vp` in `~/scratch/t3code-sro`). The build itself needs the Node that `package.json` pins under `engines.node`; `carry_build.sh` downloads that one into `~/scratch/tools` when it is missing and puts it first on `PATH`. Run heavy steps under `nice -n 15`, since agents share the VM. `git config rerere.enabled true` is set in `~/repos/personal/t3code`, so a conflict resolved once is reapplied on later rebases.

## Automatic updates

Since 2026-11-24 dev-abby follows the nightlies by itself. Three scripts in `docs/fork/bin` do the steps below, and a person is needed only when one of them files a card.

1. `carry_auto.sh` (06:30, after the fork sync) takes the newest nightly tag that is on the fork's `main`. When `carry` is not on it, it rebases a copy of `carry` in a throwaway worktree, taking the conflict resolutions `rerere` has recorded. It then runs `carry_build.sh`, which is steps 3 and 4 below in the worktree `~/repos/personal/t3code-build`, and writes the result to `~/.local/state/t3code-fork/pending`. The rebased commits wait on branch `carry-next`. It also builds when `carry` gained a commit and the nightly did not change.
2. `carry_swap.sh` (every 20 minutes from 01:00 to 05:40) does nothing without a pending build. With one, it waits until the fleet is quiet (no watcher card run, no lander, at most one busy agent), then runs step 5. `t3 update` backs up the database and returns to the previous build by itself when the new one does not start.
3. After a swap that worked, `carry_swap.sh` points `~/.local/bin/t3` at the new build, moves `carry` to the built commit (the old head stays on `carry-prev`), tags it `ours/v<version>` and pushes `carry`, `carry-prev` and the tag to the fork. It moves `carry` only when `carry` is still the commit the build started from.

A card is filed, parked as needs-input, when the rebase hits a conflict with no recorded resolution, when a test, the typecheck or the build fails, when the swap does not end on the new build, or when the push fails. In the first two cases `carry` and T3 are untouched: fix `carry` by hand with the steps below, and the next daily run builds it. Resolving a conflict by hand records it for `rerere`, so the same conflict is taken automatically next time.

State is in `~/.local/state/t3code-fork/`: `pending` (the build waiting for its swap), `installed` (tag and commit of the running build), `last` (what was last tried and whether it built), `swap.log`, and `build/<version>.log` with the `range-diff` of each rebase beside it. Try a run without changing anything with `carry_auto.sh --dry-run` or `carry_swap.sh --dry-run`. Stop the automation with `systemctl --user disable --now t3code-carry-check.timer t3code-carry-swap.timer`.

Not automated: the desktop and phone apps. They stay on upstream's own nightly channel and do not get the fork's changes, which only matter on the server.

## For each new nightly, by hand

1. Fetch and pick the tag:

   ```
   git fetch upstream --tags
   git tag -l "v0.1.*-nightly.*" --sort=-v:refname | head -1
   ```

2. Keep the current `carry` as a fallback, then rebase it onto the tag:

   ```
   git branch -f carry-prev carry
   git switch carry
   git rebase <tag>
   git range-diff <old tag>..carry-prev <tag>..carry
   ```

   `range-diff` should show every commit as unchanged (`=`) or with only the conflict edits you made. Conflicts so far were renames upstream made in code the fix touches (`crypto` became `random`) and new upstream tests that expect status `succeeded` where the fix writes `dispatched`. Change those to match the fix. If upstream changed scheduler behavior that the fix also changes, stop and decide by hand.

3. Install and test:

   ```
   corepack pnpm@12.14.0 install --frozen-lockfile
   vp test run --maxWorkers=2 apps/server/src/persistence/Migrations.test.ts apps/server/src/scheduledTasks \
     apps/server/src/orchestration-v2/ThreadLaunchService.test.ts \
     apps/server/src/orchestration-v2/ClaudeAutomaticDelivery.integration.test.ts \
     apps/server/src/mcp/OrchestratorMcpService.test.ts apps/server/src/mcp/OrchestratorMcpService.activity.test.ts \
     packages/contracts/src/scheduledTask.test.ts
   vp run --filter t3 --filter @t3tools/contracts --filter @t3tools/web --concurrency-limit 2 typecheck
   ```

   Also confirm `git diff <tag> HEAD -- apps/server/src/persistence` prints nothing.

4. Build. `.env` is a copy of `.env.example`, which carries the same public relay and Clerk config as the stock binary. Use `V=<tag without the v>.scheduler.<n>`, counting up from the last build:

   ```
   node scripts/update-release-package-versions.ts "$V"
   vp run --filter t3 build
   node scripts/apply-web-brand-assets.ts nightly apps/server/dist/client
   node apps/server/scripts/cli.ts build-exe
   # No cargo here: take the monitor from upstream's own release of this tag, never the running build.
   S=$(mktemp -d ~/scratch/stock-XXXX) && (cd "$S" && gh release download <tag> -R pingdotgg/t3code \
     -p "t3-${V%.scheduler.*}-linux-x64.tar.gz" -p SHA256SUMS && grep linux-x64 SHA256SUMS | sha256sum -c - \
     && tar -xzf t3-*.tar.gz --strip-components=1 --wildcards '*/resource-monitor/*')
   node scripts/build-cli-archive.ts --platform linux --arch x64 --version "$V" \
     --resource-monitor-dir "$S/resource-monitor" --output-dir ~/scratch/release-cli-$V
   node scripts/smoke-cli-archive.ts --archive ~/scratch/release-cli-$V/t3-$V-linux-x64.tar.gz --expect-version "$V"
   mkdir -p ~/scratch/release-serve/v$V && ln -f ~/scratch/release-cli-$V/t3-$V-linux-x64.tar.gz ~/scratch/release-serve/v$V/
   (cd ~/scratch/release-serve/v$V && sha256sum t3-$V-linux-x64.tar.gz > SHA256SUMS)
   git checkout -- apps/desktop/package.json apps/server/package.json apps/web/package.json packages/contracts/package.json
   ```

5. Swap. This restarts T3 and cuts every live session. `<running>` is the runtime folder named in `~/.t3/runtime/service-state.json`.

   ```
   cd ~/scratch/release-serve && python3 -m http.server 8799 --bind 127.0.0.1 & HTTP=$!
   T3CODE_HOME=~/.t3 T3CODE_RELEASE_BASE_URL=http://127.0.0.1:8799 \
     ~/.t3/runtime/versions/<running>/t3 update "$V" -y
   kill $HTTP
   ```

6. Tag the build and push to the fork. Fetch `origin` first: the commit-message pre-push guard skips only commits already on `origin`'s branches, so a stale `origin/main` makes it check upstream's commits against our message rule and refuse the push. The rebase rewrote `carry`, so its push needs a lease:

   ```
   git fetch origin
   git tag "ours/v$V" HEAD
   git push --force-with-lease=carry origin carry "ours/v$V"
   ```

## Timers

Four systemd user timers on dev-abby run scripts from `docs/fork/bin` in `~/repos/personal/t3code-carry`, so the scripts live on `carry` and that worktree stays on `carry`:

- `t3code-fork-sync.timer` (06:00) runs `fork_sync.py`. It runs `gh repo sync azsarnay/t3code`, then fast-forwards `main` in `~/repos/personal/t3code` when that checkout is on a clean `main`.
- `t3code-carry-check.timer` (06:30) runs `carry_auto.sh`, and `t3code-carry-swap.timer` (01:00 to 05:40) runs `carry_swap.sh`; see Automatic updates above.
- `cos-t3-renew.timer` (05:50) runs `cos_t3_renew.sh`. T3 ends an MCP client token after a fixed 30 days and has no refresh for it, unlike app pairings and environment links, which renew themselves since 0.1.11. The script replaces chief-of-staff's token in `~/.config/chief-of-staff/t3-mcp-auth` once it is 23 days old or T3 refuses it: it mints a pairing code, runs chief-of-staff's `cos_t3_login.py`, checks the new token against `/mcp`, and puts the old one back when that check fails. The previous token stays beside it as `t3-mcp-auth.prev`. A failure files a card.
- Until 2026-11-24 the 06:30 timer ran `carry_check.py`, which is still here and can be run by hand; `carry_auto.sh` uses its `rebase` function. It works on `carry` in `~/repos/personal/t3code`. It exits 1 without filing a card when `carry` does not hold the nightly the running T3 build is on (`activeVersion` in `~/.t3/runtime/service-state.json`), since that `carry` is not the one installed. It logs a warning, and adds it to the card, when `carry` and `origin/carry` differ, which means the last rebase was not pushed to the fork. When a nightly newer than carry's base is out, it rebases a detached copy of `carry` in a throwaway worktree under `~/scratch/carry-check`, runs step 3 with each command capped at 45 minutes, and records `range-diff`. It files one card per nightly, parked as needs-input so no agent picks it up. It never moves `carry`, builds, swaps or pushes. Logs and the card text are in `~/.local/state/t3code-fork/carry-check/<tag>/`.

Install them with `git -C ~/repos/personal/t3code worktree add ~/repos/personal/t3code-carry carry` (once), `cp docs/fork/systemd/* ~/.config/systemd/user/`, `systemctl --user daemon-reload` and `systemctl --user enable --now t3code-fork-sync.timer t3code-carry-check.timer t3code-carry-swap.timer cos-t3-renew.timer`. The fork sync reads `GH_TOKEN` from `~/.config/t3code-fork/env` when that file exists. Read runs with `journalctl --user -u t3code-fork-sync -u t3code-carry-check`. Tests: `python3 -m unittest discover -s docs/fork/bin`.

## Going to stock

Stop T3, run the database script, then install stock:

```
systemctl --user stop t3code.service
/usr/bin/node ~/scratch/dbcheck/rollback-db-startup.mjs ~/.t3/userdata/statev2.sqlite
T3CODE_HOME=~/.t3 ~/.t3/runtime/versions/<running>/t3 update <stock version> --allow-downgrade -y
```

Run the same script before swapping the fix back in after stock has run. Stock never clears the outcome ids, and ids left from an earlier run would make the fix treat stock's runs as its own.
