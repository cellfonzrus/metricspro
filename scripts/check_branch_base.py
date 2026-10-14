#!/usr/bin/env python3
"""BRANCH-BASE GUARD — a pull request may not be cut from a stale starting point (index §CORE-10).

OWNER DIRECTIVE 2026-10-14, after being told about two near-misses on PR #475/#477: *"this is very
creitical finding , dont jsut write down make a gate so nothing can passs thru unkowlingly , you were
smart may be the other might not be as asmart"*. So this is a gate, not a note.

THE INSTANCE. `git checkout -b claude/new-branch` was run WITHOUT a start point, so the branch was cut
from whatever HEAD happened to be — this session's already-merged branch — instead of `origin/main`.
The pull request then carried 5 already-squash-merged commits and its two-dot diff against `main`
DELETED 103,263 lines across 498 files. It was caught by counting the files on the PR, by hand. Worse,
the conflict it created meant GitHub ran no `pull_request` workflow at all, so the only green tick on
it came from the preview deployment and proved nothing (see also the 2026-10-10 case on PR #440, where
a branch cut before `main` gained a CI job simply never ran that job, and 48 green checks looked
finished next to main's 51).

THE CLASS, NOT THE INSTANCE. **A branch is only as trustworthy as the base it was cut from, and every
symptom of a stale base is silent.** Three of them are mechanical, so all three are failed here:

  A2  MASS DELETION — the two-dot diff `base..head` removes more than --max-deletions lines. A real
      change that large is allowed, but it must SAY SO: put `BASE-DELETIONS-ACKNOWLEDGED` in the pull
      request body and the check passes with the count named. Silence is the failure.
  A3  RE-PROPOSED MERGED WORK — a commit on the branch whose subject already exists in the base
      branch's history. Because this repo squash-merges, a merged commit is not an ancestor of `main`,
      so ancestry cannot answer this and `git diff main...branch` (three dots) actively lies: the
      merge base lands BEFORE the squash, and already-merged content renders as new. Subjects are
      compared instead.
  A4  A LOST CI JOB — a `pull_request` run executes the workflow files from the HEAD BRANCH, so a
      branch cut before `main` added a job never runs that job, forever, however many times CI is
      re-run. Any job name declared on the base and absent from the head fails here, by name.

WHY THIS RUNS ON `pull_request_target`. A gate that lives in the branch's own workflow file is exactly
what a stale branch drops — the failure mode A4 describes would delete this guard itself. The workflow
`.github/workflows/branch-base-guard.yml` is therefore triggered from the BASE branch. It never checks
out and never executes any code from the pull request: this script reads git metadata and parses
workflow text, nothing more, and the job is granted `contents: read` and no secrets.

Stdlib only, no DB, no network.
  Run:        python3 scripts/check_branch_base.py --base refs/base-main --head refs/pr-head \
                  [--body-file body.txt] [--max-deletions 2000]
  Arm it:     python3 scripts/check_branch_base.py --self-test
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# The declaration that makes a genuinely large removal legal. It goes in the PR body, so a human wrote
# it and a reviewer reads it in the same place.
DECLARATION = "BASE-DELETIONS-ACKNOWLEDGED"
DEFAULT_MAX_DELETIONS = 2000

_PASS: list[str] = []
_FAIL: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> bool:
    (_PASS if cond else _FAIL).append(label + (("  [%s]" % detail) if detail else ""))
    return bool(cond)


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(("git",) + args, cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


# ---------------------------------------------------------------------------- workflow job names
_JOBS_RE = re.compile(r"^jobs:\s*(#.*)?$")


def workflow_job_names(text: str) -> set[str]:
    """The display names of the jobs a workflow file declares.

    A job's check-run name is its `name:` when it has one and its key otherwise. Only a `name:`
    indented exactly two spaces inside the job is a job name — a step's `name:` sits deeper, and
    reading one as the job's would silently rename the subject and defeat the comparison.
    """
    names: set[str] = set()
    in_jobs = False
    job_indent: int | None = None
    cur_key: str | None = None
    cur_name: str | None = None

    def flush() -> None:
        if cur_key is not None:
            names.add(cur_name or cur_key)

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if not in_jobs:
            if indent == 0 and _JOBS_RE.match(line):
                in_jobs = True
            continue
        if indent == 0:  # a top-level key after jobs: ends the block
            flush()
            cur_key = cur_name = None
            in_jobs = False
            continue
        if job_indent is None:
            job_indent = indent
        if indent == job_indent:
            m = re.match(r"^\s*([A-Za-z0-9_.-]+):\s*(#.*)?$", line)
            if m:
                flush()
                cur_key, cur_name = m.group(1), None
            continue
        if cur_key is not None and cur_name is None and indent == job_indent + 2:
            m = re.match(r"^\s*name:\s*(.+?)\s*(#.*)?$", line)
            if m:
                val = m.group(1).strip()
                if val in (">", "|", ">-", "|-"):      # a folded scalar: the name is on the next
                    cur_name = None                     # line(s); fall back to the key rather than
                    cur_key = cur_key                   # inventing one.
                else:
                    cur_name = val.strip("'\"")
    flush()
    return names


def _job_names_at(rev: str, cwd: str | None = None) -> dict[str, set[str]]:
    """Every workflow job name declared at `rev`, keyed by workflow path."""
    out: dict[str, set[str]] = {}
    listing = _git("ls-tree", "-r", "--name-only", rev, ".github/workflows", cwd=cwd)
    for path in sorted(p for p in listing.splitlines() if p.endswith((".yml", ".yaml"))):
        out[path] = workflow_job_names(_git("show", "%s:%s" % (rev, path), cwd=cwd))
    return out


# ---------------------------------------------------------------------------- the three checks
def deletions_between(base: str, head: str, cwd: str | None = None) -> tuple[int, int]:
    """(lines deleted, files touched) on the TWO-DOT diff base..head — never three dots."""
    dels = files = 0
    for row in _git("diff", "--numstat", base, head, cwd=cwd).splitlines():
        parts = row.split("\t")
        if len(parts) >= 3:
            files += 1
            if parts[1].isdigit():
                dels += int(parts[1])
    return dels, files


def reproposed_subjects(base: str, head: str, cwd: str | None = None) -> list[str]:
    """Branch commit subjects that already exist in the base branch's history."""
    base_subjects = set(_git("log", "--format=%s", base, cwd=cwd).splitlines())
    branch = _git("log", "--format=%s", "%s..%s" % (base, head), cwd=cwd).splitlines()
    return [s for s in branch if s in base_subjects]


def lost_jobs(base: str, head: str, cwd: str | None = None) -> list[str]:
    """Job names the base branch runs that the head branch's workflow files do not declare."""
    b, h = _job_names_at(base, cwd=cwd), _job_names_at(head, cwd=cwd)
    head_all = set().union(*h.values()) if h else set()
    missing = []
    for path, names in sorted(b.items()):
        for name in sorted(names - head_all):
            missing.append("%s :: %s" % (path, name))
    return missing


def _first(items: list[str], empty: str, keep: int = 6) -> str:
    """Name enough of the subjects to act on, and say how many more there are. A stale base loses
    FIFTY jobs at once, and a wall of them is read as noise instead of as the finding."""
    if not items:
        return empty
    shown = "; ".join(items[:keep])
    return shown + ("  (+%d more)" % (len(items) - keep) if len(items) > keep else "")


def run(base: str, head: str, body: str, max_deletions: int, cwd: str | None = None) -> None:
    ok(bool(_git("rev-parse", base, cwd=cwd).strip()), "A1 the base revision resolves", base)
    ok(bool(_git("rev-parse", head, cwd=cwd).strip()), "A1 the head revision resolves", head)

    dels, files = deletions_between(base, head, cwd=cwd)
    declared = DECLARATION in (body or "")
    ok(dels <= max_deletions or declared,
       "A2 the branch does not delete the repository out from under itself",
       "%d line(s) deleted over %d file(s); limit %d%s"
       % (dels, files, max_deletions, "; declared in the PR body" if declared else
          "; add %s to the PR body if this removal is intended" % DECLARATION))

    dup = reproposed_subjects(base, head, cwd=cwd)
    ok(not dup, "A3 no commit on the branch is already in the base branch's history",
       _first(dup, "none re-proposed"))

    missing = lost_jobs(base, head, cwd=cwd)
    ok(not missing, "A4 every CI job the base branch runs is also declared on this branch",
       _first(missing, "no job lost"))


# ---------------------------------------------------------------------------- armed controls
_WF_OK = """name: x
on: [pull_request]
jobs:
  guards:
    name: Run the guards
    runs-on: ubuntu-latest
    steps:
      - name: a step name that is NOT a job name
        run: true
  plain:
    runs-on: ubuntu-latest
    steps:
      - run: true
"""


def self_test() -> None:
    """Every check is planted with the defect it exists to catch. A check that cannot fail is not a
    check (index 24) — and the whole reason this gate exists is that an unarmed check went blind."""
    names = workflow_job_names(_WF_OK)
    ok(names == {"Run the guards", "plain"},
       "Z1 a job's display name is its name:, or its key when it has none", str(sorted(names)))
    ok("a step name that is NOT a job name" not in names,
       "Z2 control: a STEP's name: is not mistaken for a job's")
    ok(workflow_job_names(_WF_OK.replace("    name: Run the guards\n", "")) == {"guards", "plain"},
       "Z3 control: dropping the job's name: falls back to the key, it does not drop the job")
    ok(workflow_job_names("name: x\non: [push]\n") == set(),
       "Z4 control: a file with no jobs: block yields no names, it does not throw")
    planted = _WF_OK.replace("  plain:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n", "")
    ok("plain" in (workflow_job_names(_WF_OK) - workflow_job_names(planted)),
       "Z5 ARMED: deleting a job from the head file makes it show up as lost")

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        def g(*a: str) -> str:
            return _git(*a, cwd=d)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t"); g("config", "user.name", "t")
        os.makedirs(os.path.join(d, ".github", "workflows"))
        open(os.path.join(d, ".github", "workflows", "w.yml"), "w").write(_WF_OK)
        open(os.path.join(d, "big.txt"), "w").write("\n".join(str(i) for i in range(50)) + "\n")
        g("add", "-A"); g("commit", "-qm", "a merged change that squashed onto main (#1)")
        g("checkout", "-q", "-b", "stale")
        # The exact shape of the instance: the already-merged subject is re-proposed, the file the
        # base added is gone, and the head's workflow file lost a job.
        g("commit", "-q", "--allow-empty", "-m", "a merged change that squashed onto main (#1)")
        os.remove(os.path.join(d, "big.txt"))
        open(os.path.join(d, ".github", "workflows", "w.yml"), "w").write(planted)
        g("add", "-A"); g("commit", "-qm", "my own work")

        dels, _files = deletions_between("main", "stale", cwd=d)
        ok(dels >= 50, "Z6 ARMED: the stale branch's deletions are counted", str(dels))
        ok(reproposed_subjects("main", "stale", cwd=d) == ["a merged change that squashed onto main (#1)"],
           "Z7 ARMED: a re-proposed merged subject is named")
        ok(reproposed_subjects("main", "main", cwd=d) == [],
           "Z8 control: a branch with no commits of its own re-proposes nothing")
        ok(lost_jobs("main", "stale", cwd=d) == [".github/workflows/w.yml :: plain"],
           "Z9 ARMED: the job the head branch no longer declares is named",
           str(lost_jobs("main", "stale", cwd=d)))
        g("checkout", "-q", "main")
        g("checkout", "-q", "-b", "clean")
        open(os.path.join(d, "mine.txt"), "w").write("one line\n")
        g("add", "-A"); g("commit", "-qm", "work cut from the right base")
        ok(deletions_between("main", "clean", cwd=d)[0] == 0
           and reproposed_subjects("main", "clean", cwd=d) == []
           and lost_jobs("main", "clean", cwd=d) == [],
           "Z10 control: a branch cut from main passes all three checks")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="refs/base-main")
    ap.add_argument("--head", default="refs/pr-head")
    ap.add_argument("--body-file", default="")
    ap.add_argument("--max-deletions", type=int, default=DEFAULT_MAX_DELETIONS)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        self_test()
    else:
        body = ""
        if a.body_file and os.path.exists(a.body_file):
            body = open(a.body_file, encoding="utf-8", errors="replace").read()
        run(a.base, a.head, body, a.max_deletions)

    for line in _PASS:
        print("PASS  " + line)
    for line in _FAIL:
        print("FAIL  " + line)
    print("\n%d passed, %d failed" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("\nA stale starting point is the usual cause. The fix is a NEW branch cut explicitly from\n"
              "the base — `git fetch origin main && git checkout -B <branch> origin/main` — then replay\n"
              "your own commits onto it. Never a force push (CLAUDE.md, branch lifecycle).")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
