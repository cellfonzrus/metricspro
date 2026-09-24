"""LOCK — a failing harness fails CI.

THE DEFECT (found 2026-09-24). GitHub Actions runs a step with no `shell:` as `bash -e {0}` — WITHOUT pipefail. Every
gate here was written `python3 harness_x.py | tee -a "$GITHUB_STEP_SUMMARY"`, so the step's status was tee's, and a
harness that FAILED still passed CI. Every lock in carrier-vocab-guard / lineage-guard / org-scope-guard was silently
unenforced (a sweep of all 25 CI harnesses that day found them green, so nothing had slipped through — by luck).
An explicit `shell: bash` runs `bash --noprofile --norc -eo pipefail {0}`.

THE RULE: every workflow that runs a harness (`harness_*.py`) declares the top-level default

    defaults:
      run:
        shell: bash

Stdlib only (the job that runs this installs nothing): run `python backend/harness_ci_pipefail_lock.py`.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = os.path.join(HERE, "..", ".github", "workflows")
RUNS_HARNESS = re.compile(r"^\s*(?:-\s*)?run:.*\bharness_\w+\.py", re.M)
DEFAULT_BASH = re.compile(r"^defaults:\s*\n\s+run:\s*\n\s+shell:\s*bash\s*$", re.M)


def violations(files):
    """files: {name: yaml text} → [plain sentences]; [] = the lock holds."""
    out = []
    for name in sorted(files):
        text = files[name]
        if not RUNS_HARNESS.search(text):
            continue
        if not DEFAULT_BASH.search(text):
            out.append(f"{name}: runs a harness but does not declare `defaults: run: shell: bash` — without pipefail "
                       "`python3 harness_x.py | tee …` passes even when the harness fails")
    return out


def _load():
    return {f: open(os.path.join(WORKFLOWS, f), encoding="utf-8").read()
            for f in os.listdir(WORKFLOWS) if f.endswith((".yml", ".yaml"))}


def main():
    passed = failed = 0

    def check(label, ok):
        nonlocal passed, failed
        print(("  PASS  " if ok else "  FAIL  ") + label)
        passed += ok
        failed += (not ok)

    files = _load()
    real = violations(files)
    for v in real:
        print("  ✗ " + v)
    check("every workflow that runs a harness runs it under pipefail", real == [])
    check("the lock sees at least one harness-running workflow", any(RUNS_HARNESS.search(t) for t in files.values()))

    # negative controls — each must go RED
    gate = "jobs:\n  g:\n    steps:\n      - run: python3 harness_x.py | tee -a out\n"
    check("a harness workflow with no default shell → RED", bool(violations({"x.yml": gate})))
    check("a default shell of sh → RED",
          bool(violations({"x.yml": "defaults:\n  run:\n    shell: sh\n\n" + gate})))
    for name, text in files.items():
        if RUNS_HARNESS.search(text):
            stripped = DEFAULT_BASH.sub("", text)
            check(f"{name} with its default shell removed → RED", bool(violations({name: stripped})))
    # and stays green where it should
    check("the declared default → green", violations({"x.yml": "defaults:\n  run:\n    shell: bash\n\n" + gate}) == [])
    check("a workflow that runs no harness needs nothing → green",
          violations({"d.yml": "jobs:\n  d:\n    steps:\n      - run: echo hi | tee x\n"}) == [])

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("OK — every workflow that runs a harness runs it under pipefail; a failing harness fails CI.")


if __name__ == "__main__":
    main()
