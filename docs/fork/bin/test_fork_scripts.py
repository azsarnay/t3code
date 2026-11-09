"""Tests for fork_sync.py and carry_check.py against throwaway git repos.

Run: python3 -m unittest discover -s docs/fork/bin
"""

import contextlib
import fcntl
import io
import os
import re
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# keep the machine's global hooks (commit-msg, pre-push) out of the fixtures
os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import carry_check  # noqa: E402
import fork_sync  # noqa: E402

TASKBOARD_STUB = """\
import json
import sys
from pathlib import Path
args = sys.argv[1:]
assert args[0] == "taskboard.py", args
if args[1] == "add":
    queue = Path(args[args.index("--dir") + 1])
    title, _, body = sys.stdin.read().partition("\\n")
    n = len(list(queue.glob("*.md"))) + len(list((queue / "Archive").glob("*.md"))) + 1
    card = queue / f"card-{n}.md"
    card.write_text(f"---\\nstatus: queued\\n---\\n\\n# {title}\\n\\n## Task\\n{body}\\n")
    print(f"queued: {card}")
    sys.exit()
card = Path(args[2])
text = card.read_text()
if args[1] == "status":
    assert args[3:] == ["started"] and "status: queued" in text, args
    card.write_text(text.replace("status: queued", "status: started"))
else:
    assert args[1:] == ["ask", str(card), "--stdin"] and "status: started" in text, args
    [q] = json.loads(sys.stdin.read())["questions"]
    assert len(q["options"]) >= 2, q
    card.write_text(text.replace("status: started", "status: needs-input")
                    + f"\\n## Needs from you\\n### ? {q['id']} - {q['text']}\\n")
"""

GH_STUB = """\
import sys
from pathlib import Path
Path(sys.argv[1]).open("a").write(" ".join(sys.argv[2:]) + "\\n")
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo, name, text, message):
    (Path(repo) / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def refs(repo):
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def init(path):
    git(path.parent, "init", "-q", "-b", "main", str(path))
    git(path, "config", "user.email", "you@example.com")
    git(path, "config", "user.name", "you")
    return path


class Fixture:
    """An upstream with two nightly tags, a fork clone whose `carry` sits on the first."""

    def __init__(self, root, conflict=False):
        """conflict: False, True (both edit app.txt) or "rename" (both rename it)."""
        self.root = Path(root)
        work = init(self.root / "upstream-work")
        commit(work, "app.txt", "one\n", "base")
        git(work, "tag", "v0.1.0-nightly.1")
        self.base = "v0.1.0-nightly.1"
        if conflict == "rename":
            git(work, "mv", "app.txt", "up-app.txt")
            git(work, "commit", "-qm", "upstream rename")
        else:
            commit(work, "app.txt" if conflict else "up.txt", "two\n", "upstream change")
        commit(work, "other.txt", "x\n", "upstream other")
        git(work, "tag", "v0.1.0-nightly.2")
        self.upstream = self.root / "upstream.git"
        git(self.root, "clone", "-q", "--bare", str(work), str(self.upstream))

        self.repo = self.root / "sro"
        git(self.root, "clone", "-q", "-o", "upstream", str(self.upstream), str(self.repo))
        git(self.repo, "config", "user.email", "you@example.com")
        git(self.repo, "config", "user.name", "you")
        git(self.repo, "remote", "set-url", "--push", "upstream", "DISABLED-fork-only")
        git(self.repo, "switch", "-q", "-c", "carry", self.base)
        git(self.repo, "branch", "-q", "-D", "main")
        if conflict == "rename":
            git(self.repo, "mv", "app.txt", "our-app.txt")
            git(self.repo, "commit", "-qm", "fix")
        else:
            commit(self.repo, "app.txt" if conflict else "fix.txt", "ours\n", "fix")
        commit(self.repo, "carry.md", "notes\n", "notes")
        self.queue = self.root / "queue"
        (self.queue / "Archive").mkdir(parents=True)
        self.stub = self.root / "taskboard_stub.py"
        self.stub.write_text(TASKBOARD_STUB)

    def run(self, step3=("true",), env_sh=""):
        carry_check.STEP3 = list(step3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = carry_check.main([
                "--repo", str(self.repo), "--work-root", str(self.root / "work"),
                "--state", str(self.root / "state"), "--queue", str(self.queue),
                "--env-sh", env_sh, "--taskboard", f"{sys.executable} {self.stub}"])
        return code, out.getvalue()

    def body(self):
        return (self.root / "state" / "v0.1.0-nightly.2" / "card.md").read_text()

    def cards(self):
        return sorted(self.queue.glob("*.md")) + sorted((self.queue / "Archive").glob("*.md"))


class CarryCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, carry_check, "STEP3", carry_check.STEP3)
        self.addCleanup(setattr, carry_check, "STEP_TIMEOUT", carry_check.STEP_TIMEOUT)

    def assert_untouched(self, f, carry_before, upstream_before):
        self.assertEqual(git(f.repo, "rev-parse", "carry"), carry_before)
        self.assertEqual(git(f.repo, "branch", "--show-current"), "carry")
        self.assertEqual(refs(f.upstream), upstream_before)
        self.assertEqual(git(f.repo, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertEqual(git(f.repo, "branch", "--format=%(refname:short)"), "carry")

    def assert_report(self, f, card):
        """Filed so no watcher dispatches it, and the body reports without instructing."""
        text = card.read_text()
        self.assertIn("status: needs-input", text)
        self.assertIn("## Needs from you", text)
        self.assertNotRegex(f.body(), r"(?i)ready for|resolve|by hand|steps? [2456]|build|swap"
                                      r"|push")

    def test_passing_rebase_files_one_card_and_never_refiles(self):
        f = Fixture(self.tmp.name)
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        code, _ = f.run()
        self.assertEqual(code, 0)
        [card] = f.cards()
        text = card.read_text()
        self.assertIn("# t3code nightly v0.1.0-nightly.2 rebases and passes with the carry commits", text)
        self.assertIn("range-diff", text)
        self.assertTrue((f.root / "state" / "v0.1.0-nightly.2" / "range-diff.txt").read_text())
        self.assert_report(f, card)
        self.assert_untouched(f, carry, upstream)

        f.run()
        self.assertEqual(len(f.cards()), 1)
        card.rename(f.queue / "Archive" / card.name)
        code, out = f.run()
        self.assertEqual((code, len(f.cards())), (0, 1))
        self.assertIn("already filed", out)

    def test_conflict_lists_files_and_leaves_carry(self):
        f = Fixture(self.tmp.name, conflict=True)
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        f.run(step3=("false",))
        [card] = f.cards()
        text = card.read_text()
        self.assertIn("# t3code nightly v0.1.0-nightly.2: carry rebase conflicts", text)
        self.assertIn("app.txt", text)
        self.assert_report(f, card)
        self.assert_untouched(f, carry, upstream)

    def test_conflict_on_a_path_with_no_file_lists_it(self):
        f = Fixture(self.tmp.name, conflict="rename")
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        code, _ = f.run()
        self.assertEqual(code, 0)
        [card] = f.cards()
        text = card.read_text()
        self.assertIn("carry rebase conflicts", text)
        self.assertIn("`app.txt`", text)
        self.assert_untouched(f, carry, upstream)

    def test_conflict_rerere_already_resolved_rebases(self):
        f = Fixture(self.tmp.name, conflict=True)
        git(f.repo, "config", "rerere.enabled", "true")
        git(f.repo, "switch", "-q", "-c", "teach", "carry")
        subprocess.run(["git", "-C", str(f.repo), "rebase", "-q", "v0.1.0-nightly.2"],
                       capture_output=True)
        (f.repo / "app.txt").write_text("resolved\n")
        git(f.repo, "add", "app.txt")
        subprocess.run(["git", "-C", str(f.repo), "rebase", "--continue"], check=True,
                       capture_output=True, env={**os.environ, "GIT_EDITOR": "true"})
        git(f.repo, "switch", "-q", "carry")
        git(f.repo, "branch", "-q", "-D", "teach")
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        f.run()
        [card] = f.cards()
        self.assertIn("rebases and passes with the carry commits", card.read_text())
        self.assert_untouched(f, carry, upstream)

    def test_failing_checks_name_the_failing_command(self):
        f = Fixture(self.tmp.name)
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        f.run(step3=("true", "echo boom-output; false"))
        [card] = f.cards()
        text = card.read_text()
        self.assertIn("# t3code nightly v0.1.0-nightly.2: carry checks fail", text)
        self.assertIn("echo boom-output; false", text)
        self.assertIn("boom-output", text)
        self.assert_report(f, card)
        self.assert_untouched(f, carry, upstream)

    def test_checks_run_under_nice_15(self):
        f = Fixture(self.tmp.name)
        f.run(step3=("sh -c 'echo niceness=$(nice); false'",))
        self.assertIn(f"niceness={min(os.nice(0) + 15, 19)}", f.cards()[0].read_text())

    def test_worktree_tools_win_over_env_sh(self):
        f = Fixture(self.tmp.name)
        old = f.root / "old-bin"
        old.mkdir()
        (old / "vp").write_text("#!/bin/sh\necho old-vp\nexit 1\n")
        (old / "vp").chmod(0o755)
        env_sh = f.root / "env.sh"
        env_sh.write_text(f'export PATH="{old}:$PATH"\n')
        install = ("mkdir -p node_modules/.bin && printf '#!/bin/sh\\necho worktree-vp\\nexit 1\\n'"
                   " > node_modules/.bin/vp && chmod +x node_modules/.bin/vp")
        f.run(step3=(install, "vp"), env_sh=str(env_sh))
        text = f.cards()[0].read_text()
        self.assertIn("worktree-vp", text)
        self.assertNotIn("old-vp", text)

    def test_hung_check_is_killed_with_its_children_and_cleaned_up(self):
        f = Fixture(self.tmp.name)
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        carry_check.STEP_TIMEOUT = 1
        pid_file = f.root / "sleep.pid"
        code, _ = f.run(step3=(f"sleep 600 & echo $! > {pid_file}; wait", "true"))
        self.assertEqual(code, 0)
        [card] = f.cards()
        self.assertRegex(card.read_text(), r"sleep 600 .*\(timed out after 1 s\)")
        try:
            fd = os.pidfd_open(int(pid_file.read_text()))
        except ProcessLookupError:
            pass
        else:
            with os.fdopen(fd) as pidfd:
                self.assertTrue(select.select([pidfd], [], [], 10)[0], "the check's child survived")
        self.assertFalse((f.root / "work" / "v0.1.0-nightly.2").exists())
        self.assert_untouched(f, carry, upstream)

    def test_worktree_left_by_a_killed_run_is_removed(self):
        f = Fixture(self.tmp.name)
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        stale = f.root / "work" / "v0.0.9-nightly.1"
        git(f.repo, "worktree", "add", "-q", "--detach", str(stale), "carry")
        f.run()
        self.assertEqual(len(f.cards()), 1)
        self.assertFalse(stale.exists())
        self.assert_untouched(f, carry, upstream)

    def test_overlapping_run_does_nothing(self):
        f = Fixture(self.tmp.name)
        carry, upstream = git(f.repo, "rev-parse", "carry"), refs(f.upstream)
        (f.root / "state").mkdir()
        with open(f.root / "state" / ".lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            code, out = f.run()
        self.assertEqual((code, f.cards()), (0, []))
        self.assertIn("already running", out)
        self.assert_untouched(f, carry, upstream)

    def test_carry_already_on_newest_nightly_files_nothing(self):
        f = Fixture(self.tmp.name)
        git(f.repo, "rebase", "-q", "v0.1.0-nightly.2")
        code, out = f.run()
        self.assertEqual((code, f.cards()), (0, []))
        self.assertIn("already on", out)


class UnitFileTest(unittest.TestCase):
    UNITS = Path(__file__).resolve().parent.parent / "systemd"
    CHECKOUT = "%h/scratch/t3code-sro/"

    def test_services_are_bounded_and_run_scripts_on_this_branch(self):
        services = sorted(self.UNITS.glob("*.service"))
        self.assertEqual(len(services), 2)
        tree = Path(__file__).resolve().parents[3]
        self.assertTrue((tree / "docs/fork/CARRY.md").is_file(), "the scripts belong on `carry`")
        for unit in services:
            text = unit.read_text()
            self.assertRegex(text, r"(?m)^TimeoutStartSec=\S", unit.name)
            [script] = re.findall(r"(?m)^ExecStart=\S*python3 (\S+)", text)
            self.assertTrue(script.startswith(self.CHECKOUT), script)
            self.assertTrue((tree / script.removeprefix(self.CHECKOUT)).is_file(), script)

    def test_carry_check_timeout_outlasts_its_checks(self):
        text = (self.UNITS / "t3code-carry-check.service").read_text()
        hours = int(re.search(r"(?m)^TimeoutStartSec=(\d+)h$", text)[1])
        self.assertGreater(hours * 3600, len(carry_check.STEP3) * carry_check.STEP_TIMEOUT)


class ForkSyncTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        work = init(self.root / "fork-work")
        self.old = commit(work, "a.txt", "1\n", "one")
        self.origin = self.root / "origin.git"
        git(self.root, "clone", "-q", "--bare", str(work), str(self.origin))
        self.checkout = self.root / "t3code"
        git(self.root, "clone", "-q", str(self.origin), str(self.checkout))
        git(self.checkout, "config", "user.email", "you@example.com")
        git(self.checkout, "config", "user.name", "you")
        # what `gh repo sync` does to the fork: origin/main moves on upstream's behalf
        self.new = commit(work, "a.txt", "2\n", "two")
        git(work, "push", "-q", str(self.origin), "main")
        self.calls = self.root / "gh-calls"
        stub = self.root / "gh_stub.py"
        stub.write_text(GH_STUB)
        self.gh = f"{sys.executable} {stub} {self.calls}"

    def run_sync(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fork_sync.main(["--checkout", str(self.checkout), "--gh", self.gh, *extra])
        return code, out.getvalue()

    def gh_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_clean_main_syncs_fork_then_fast_forwards(self):
        origin = refs(self.origin)
        code, _ = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(self.gh_calls(), ["repo sync azsarnay/t3code"])
        self.assertEqual(git(self.checkout, "rev-parse", "main"), self.new)
        self.assertEqual(refs(self.origin), origin)

    def test_dirty_main_is_not_moved(self):
        (self.checkout / "a.txt").write_text("local edit\n")
        code, out = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(git(self.checkout, "rev-parse", "main"), self.old)
        self.assertEqual((self.checkout / "a.txt").read_text(), "local edit\n")
        self.assertIn("not clean", out)

    def test_untracked_file_counts_as_dirty(self):
        (self.checkout / "scratch.txt").write_text("x\n")
        self.run_sync()
        self.assertEqual(git(self.checkout, "rev-parse", "main"), self.old)

    def test_other_branch_is_not_moved(self):
        git(self.checkout, "switch", "-q", "-c", "work")
        code, out = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(git(self.checkout, "rev-parse", "main"), self.old)
        self.assertEqual(git(self.checkout, "branch", "--show-current"), "work")
        self.assertIn("not on main", out)

    def test_diverged_main_fails_without_moving(self):
        local = commit(self.checkout, "b.txt", "x\n", "local commit on main")
        code, _ = self.run_sync()
        self.assertNotEqual(code, 0)
        self.assertEqual(git(self.checkout, "rev-parse", "main"), local)

    def test_dry_run_neither_syncs_nor_moves_main(self):
        code, out = self.run_sync("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(git(self.checkout, "rev-parse", "main"), self.old)
        self.assertRegex(out, r"would run: .* repo sync azsarnay/t3code")

    def test_failed_sync_leaves_main(self):
        self.gh = "false"
        code, _ = self.run_sync()
        self.assertNotEqual(code, 0)
        self.assertEqual(git(self.checkout, "rev-parse", "main"), self.old)


if __name__ == "__main__":
    unittest.main()
