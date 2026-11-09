#!/usr/bin/env python3
"""Rebase a copy of `carry` onto the newest upstream nightly, test it, and file one card.

Fetches upstream tags. When a nightly newer than carry's base exists and no card for it is
in the taskboard queue or its Archive, rebases a detached copy of carry in a throwaway
worktree, runs step 3 of docs/fork/CARRY.md under `nice -n 15`, records `git range-diff`,
and files a card with the result. It never moves `carry`, pushes, builds or swaps T3.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

NIGHTLY = "v*-nightly.*"
# Step 3 of docs/fork/CARRY.md on `carry`; change the two together.
STEP3 = [
    "corepack pnpm@12.14.0 install --frozen-lockfile",
    "vp test run --maxWorkers=2 apps/server/src/persistence/Migrations.test.ts"
    " apps/server/src/scheduledTasks apps/server/src/orchestration-v2/ThreadLaunchService.test.ts"
    " apps/server/src/orchestration-v2/ClaudeAutomaticDelivery.integration.test.ts"
    " apps/server/src/mcp/OrchestratorMcpService.test.ts"
    " apps/server/src/mcp/OrchestratorMcpService.activity.test.ts"
    " packages/contracts/src/scheduledTask.test.ts",
    "vp run --filter t3 --filter @t3tools/contracts --filter @t3tools/web"
    " --concurrency-limit 2 typecheck",
]
TAIL = 60


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, "GIT_EDITOR": "true"})
    if not check:
        return r
    if r.returncode:
        raise SystemExit(f"git {shlex.join(args)} failed in {repo}:\n{r.stderr.strip()}")
    return r.stdout.strip()


def title_prefix(tag):
    return f"t3code nightly {tag}"


def filed(queue, tag):
    """The card already filed for TAG in QUEUE or its Archive, else None."""
    heading = re.compile(rf"^# {re.escape(title_prefix(tag))}[: ]", re.M)
    for p in [*queue.glob("*.md"), *(queue / "Archive").glob("*.md")]:
        if heading.search(p.read_text(encoding="utf-8", errors="replace")):
            return p
    return None


def rebase(wt, tag):
    """Rebase WT's detached HEAD onto TAG, taking rerere's resolutions; the conflicted
    files on failure (the rebase is aborted), [] on success."""
    r = git(wt, "rebase", tag, check=False)
    while r.returncode:
        unmerged = git(wt, "diff", "--name-only", "--diff-filter=U").splitlines()
        markers = [f for f in unmerged
                   if re.search(r"^<{7} ", (wt / f).read_text(errors="replace"), re.M)]
        if not unmerged or markers or git(wt, "rerere", "remaining"):
            git(wt, "rebase", "--abort", check=False)
            return unmerged or ["(the rebase stopped with no conflicted file)"]
        git(wt, "add", "--", *unmerged)
        r = git(wt, "rebase", "--continue", check=False)
    return []


def step3(wt, tag, env_sh, logs):
    """Each failing check as (command, log tail)."""
    failures = []
    for i, cmd in enumerate(STEP3):
        log = logs / f"step3-{i}.log"
        script = f". {shlex.quote(env_sh)} && nice -n 15 {cmd}" if env_sh else f"nice -n 15 {cmd}"
        with log.open("w") as out:
            code = subprocess.run(["bash", "-c", script], cwd=wt, stdout=out,
                                  stderr=subprocess.STDOUT).returncode
        if code:
            tail = log.read_text(errors="replace").splitlines()[-TAIL:]
            failures.append((cmd, "\n".join(tail)))
    persistence = git(wt, "diff", "--stat", tag, "HEAD", "--", "apps/server/src/persistence")
    if persistence:
        failures.append(("git diff <tag> HEAD -- apps/server/src/persistence (must be empty)",
                         persistence))
    return failures


def fence(text):
    return f"```\n{text}\n```"


def card(tag, base, carry, conflicts, failures, range_diff, logs):
    prefix = title_prefix(tag)
    head = (f"Upstream nightly `{tag}` is out. `carry` ({carry[:10]}) is on `{base}`. "
            f"A detached copy of carry was rebased onto it in a throwaway worktree; "
            f"`carry` itself did not move. Logs: {logs}")
    if conflicts:
        files = "\n".join(f"- `{f}`" for f in conflicts)
        return (f"{prefix}: carry rebase conflicts\n{head}\n\n"
                f"The rebase stopped on these files (rerere had no resolution):\n{files}\n\n"
                f"Resolve them by hand with CARRY.md step 2.")
    rd = f"`git range-diff {base}..carry {tag}..<rebased>`:\n{fence(range_diff)}"
    if failures:
        parts = "\n\n".join(f"`{cmd}` failed:\n{fence(tail)}" for cmd, tail in failures)
        return f"{prefix}: carry checks fail\n{head}\n\n{rd}\n\nCARRY.md step 3:\n\n{parts}"
    return (f"{prefix} rebases and passes with the carry commits\n{head}\n\n{rd}\n\n"
            f"CARRY.md step 3 passed. Ready for a manual build (CARRY.md steps 2 and 4 to 6).")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "scratch/t3code-sro")
    ap.add_argument("--work-root", type=Path, default=Path.home() / "scratch/carry-check")
    ap.add_argument("--state", type=Path,
                    default=Path.home() / ".local/state/t3code-fork/carry-check")
    ap.add_argument("--queue", type=Path, help="the taskboard queue dir (default: taskboard's)")
    ap.add_argument("--env-sh", default=str(Path.home() / "scratch/tools/env.sh"),
                    help="sourced before step 3; empty to skip")
    ap.add_argument("--taskboard", default="zplug taskboard",
                    help="command that runs taskboard's scripts (tests pass a stub)")
    a = ap.parse_args(argv)
    try:
        return check(a)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 1


def default_queue(taskboard):
    r = subprocess.run([*shlex.split(taskboard), "zconfig.py", "load", "taskboard"],
                       capture_output=True, text=True)
    cfg = json.loads(r.stdout) if r.returncode == 0 else {}
    if not cfg.get("vaultRoot") or not cfg.get("tasksSubdir"):
        raise SystemExit("taskboard config has no vaultRoot/tasksSubdir: pass --queue")
    return Path(cfg["vaultRoot"]) / cfg["tasksSubdir"]


def check(a):
    a.queue = a.queue or default_queue(a.taskboard)
    git(a.repo, "fetch", "--quiet", "--tags", "upstream")
    tags = git(a.repo, "tag", "-l", NIGHTLY, "--sort=-v:refname").splitlines()
    if not tags:
        raise SystemExit(f"no {NIGHTLY} tag in {a.repo}")
    tag, carry = tags[0], git(a.repo, "rev-parse", "carry")
    base = git(a.repo, "describe", "--tags", "--abbrev=0", "--match", NIGHTLY, carry)
    if git(a.repo, "merge-base", "--is-ancestor", tag, carry, check=False).returncode == 0:
        print(f"carry is already on {tag}: nothing to check")
        return 0
    if (held := filed(a.queue, tag)):
        print(f"{tag} already filed: {held}")
        return 0

    logs, wt = a.state / tag, a.work_root / tag
    logs.mkdir(parents=True, exist_ok=True)
    if wt.exists():
        git(a.repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(wt, ignore_errors=True)
    git(a.repo, "worktree", "prune")
    git(a.repo, "worktree", "add", "--quiet", "--detach", str(wt), carry)
    try:
        conflicts = rebase(wt, tag)
        range_diff = failures = None
        if not conflicts:
            range_diff = git(wt, "range-diff", f"{base}..{carry}", f"{tag}..HEAD")
            (logs / "range-diff.txt").write_text(range_diff + "\n")
            failures = step3(wt, tag, a.env_sh, logs)
    finally:
        git(a.repo, "worktree", "remove", "--force", str(wt), check=False)
        git(a.repo, "worktree", "prune")

    body = card(tag, base, carry, conflicts, failures, range_diff, logs)
    (logs / "card.md").write_text(body + "\n")
    r = subprocess.run([*shlex.split(a.taskboard), "taskboard.py", "add", "--dir", str(a.queue),
                        "--priority", "medium", "--stdin"], input=body, capture_output=True,
                       text=True)
    if r.returncode:
        raise SystemExit(f"could not file the card for {tag}:\n{r.stderr.strip()}")
    print(r.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
