# Carrying fork changes

This fork, `azsarnay/t3code`, keeps its own changes on one branch, `carry`, on top of a stock upstream nightly tag. They stay on the fork. They are never sent to `pingdotgg/t3code` as a pull request and never pushed to the `upstream` remote. In this clone, `gh` defaults to `azsarnay/t3code` and the `upstream` push URL is set to `DISABLED-fork-only`.

- `main` on the fork is a plain copy of upstream `main`. Never commit to it; sync it with `gh repo sync azsarnay/t3code`.
- `carry` holds one commit per change, plus this file. Rebase it onto each new nightly tag.
- Every build that runs on dev-abby gets a tag, `ours/v<version>`, on the commit it was built from, pushed to the fork.

Today `carry` holds one change, the scheduled-run-outcomes fix. It adds no numbered migration: `apps/server/src/scheduledTasks/scheduledTaskOutcomeSchema.ts` adds its columns at service start, so the database's migration history stays the same as stock.

The build tools live outside the repo. Load them first with `. ~/scratch/tools/env.sh` (Node 26.12.1, pnpm through corepack, patchelf, and this clone's `vp`). Run heavy steps under `nice -n 15`, since agents share the VM. `git config rerere.enabled true` is set in this clone, so a conflict resolved once is reapplied on later rebases.

## For each new nightly

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
   cp <running runtime>/resource-monitor/linux-x64/t3-resource-monitor ~/scratch/rm4151/linux-x64/   # only if native/resource-monitor changed
   node scripts/build-cli-archive.ts --platform linux --arch x64 --version "$V" \
     --resource-monitor-dir ~/scratch/rm4151 --output-dir ~/scratch/release-cli-$V
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

6. Tag the build and push to the fork. The rebase rewrote `carry`, so its push needs a lease:

   ```
   git tag "ours/v$V" HEAD
   git push --force-with-lease=carry origin carry "ours/v$V"
   ```

## Going to stock

Stop T3, run the database script, then install stock:

```
systemctl --user stop t3code.service
/usr/bin/node ~/scratch/dbcheck/rollback-db-startup.mjs ~/.t3/userdata/statev2.sqlite
T3CODE_HOME=~/.t3 ~/.t3/runtime/versions/<running>/t3 update <stock version> --allow-downgrade -y
```

Run the same script before swapping the fix back in after stock has run. Stock never clears the outcome ids, and ids left from an earlier run would make the fix treat stock's runs as its own.
