#!/usr/bin/env python3
"""Sync the fork's main with upstream, then fast-forward the local clone's main.

`gh repo sync` updates main on the fork from its parent through the GitHub API. Then the
local clone fetches origin and fast-forwards main, only when main is checked out and clean.
Nothing here pushes. See docs/fork/CARRY.md on `carry`.
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"failed ({r.returncode}): {shlex.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="azsarnay/t3code", help="the fork on GitHub")
    ap.add_argument("--checkout", type=Path, default=Path.home() / "repos/personal/t3code")
    ap.add_argument("--gh", default="gh", help="gh command (tests pass a stub)")
    ap.add_argument("--dry-run", action="store_true",
                    help="skip the fork sync and the fast-forward; fetch and report only")
    a = ap.parse_args(argv)
    try:
        sync = [*shlex.split(a.gh), "repo", "sync", a.repo]
        if a.dry_run:
            print(f"would run: {shlex.join(sync)}")
        else:
            run(sync)
            print(f"synced {a.repo} main with its parent")

        git = ["git", "-C", str(a.checkout)]
        run([*git, "fetch", "--quiet", "origin"])
        branch = subprocess.run([*git, "symbolic-ref", "--short", "-q", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        if branch != "main":
            print(f"{a.checkout} is not on main ({branch or 'detached HEAD'}): left as is")
            return 0
        if run([*git, "status", "--porcelain"]):
            print(f"{a.checkout} main is not clean: left as is")
            return 0
        behind = run([*git, "rev-list", "--count", "main..origin/main"])
        if a.dry_run:
            print(f"would fast-forward {a.checkout} main by {behind} commit(s)")
            return 0
        run([*git, "merge", "--ff-only", "--quiet", "origin/main"])
        print(f"fast-forwarded {a.checkout} main by {behind} commit(s)")
        return 0
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
