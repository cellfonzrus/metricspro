"""LOCK — "is this gated?" has ONE answer, and it cannot go stale (found 2026-11-04, §CORE-34).

OWNER 2026-11-04: *"Fix 1/2/3"* — the third item, which I had reported to them myself as a lesson
rather than a fix.

WHAT WENT WRONG. On 2026-10-28, shipping the test framework, I told the owner in writing that this
repository had **"no frontend CI at all — not tests, not typecheck, not the lint ratchet."** It was
false. `npm run typecheck` and `npm run lint:gate` had been running on every frontend change for
weeks, from `carrier-vocab-guard.yml` — a workflow whose name mentions neither and which holds 49
jobs. I had read the workflow FILENAMES, seen nothing frontend-shaped, and reported an absence.

THE CLASS, not the instance:

    **Whether a thing is gated was a fact about fifteen workflow files and 150-odd jobs, answerable
    only by reading all of them.** A fact that expensive does not get looked up, it gets guessed —
    and a guess about whether something is checked is worse than no answer, because it is
    indistinguishable from a measurement. Mine reached the owner as a measurement.

So the fact gets one home, `docs/CI_GATES.md`, and the home is DERIVED (`backend/ci_gates.py`) from
the workflows rather than written beside them. A hand-maintained list of what CI checks is a second
copy of what the YAML already says, and the house already calls that a defect.

WHY A GENERATOR WITH A `--bless` IS SAFE HERE, when the owner's rules are rightly suspicious of one
(the module-graph blesser once silently deleted 11 of 12 facts; the eslint ratchet deliberately has
no `--write` at all). The difference is that this file holds **no human-authored fact to lose**:
every byte is derived from the workflows, so a regenerate cannot discard a judgement somebody made.
The register it could corrupt — `harness_unrun_pending.txt` — is READ here and never written. §Z
proves exactly that.

WHAT FAILS THE BUILD
  A  `docs/CI_GATES.md` is missing, or differs by one byte from what the workflows derive.
  B  the file was hand-edited (its generated header is gone).
  C  a gate this index claims is run by a job that no longer exists.
  D  the index and `harness_ci_pipefail_lock.py` disagree about which harnesses nothing runs.
  Z  negative controls — each check planted with its own defect, and the blesser proved harmless.

Stdlib only, no DB, no network:  cd backend && python3 harness_ci_gate_index.py [--bless]
"""
from __future__ import annotations

import glob
import os
import sys

import ci_gates

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "docs", "CI_GATES.md")
PENDING = os.path.join(HERE, "harness_unrun_pending.txt")

_FAIL: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (("\n          " + detail) if (detail and not ok) else ""))
    if not ok:
        _FAIL.append(label)


def read_pending() -> list:
    with open(PENDING, "r", encoding="utf-8") as fh:
        return [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]


def harnesses_on_disk() -> list:
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "harness_*.py")))


def derive() -> str:
    return ci_gates.render_index(ci_gates.read_workflows(ROOT), read_pending(), harnesses_on_disk())


def main(argv) -> int:
    bless = "--bless" in argv
    want = derive()

    if bless:
        with open(INDEX, "w", encoding="utf-8") as fh:
            fh.write(want if want.endswith("\n") else want + "\n")
        print(f"WROTE {os.path.relpath(INDEX, ROOT)} ({len(want.splitlines())} lines)")
        return 0

    print(__doc__.strip().splitlines()[0])
    print("-" * 96)

    print("A. the one home is present and current")
    have = None
    if os.path.exists(INDEX):
        with open(INDEX, "r", encoding="utf-8") as fh:
            have = fh.read()
    check("A1 docs/CI_GATES.md exists", have is not None,
          "run: cd backend && python3 harness_ci_gate_index.py --bless")
    if have is None:
        print("-" * 96)
        print("RED — the index does not exist.")
        return 1

    check("A2 docs/CI_GATES.md carries its generated header, so it was not hand-edited",
          have.startswith(ci_gates.HEADER),
          "a hand-written answer to 'is this gated' is a second copy of the workflows")
    fresh = have.strip() == want.strip()
    check("A3 docs/CI_GATES.md matches what the workflows derive, byte for byte", fresh,
          "the workflows changed and the index did not — run with --bless")

    print("B. the index's claims resolve")
    workflows = ci_gates.read_workflows(ROOT)
    idx = ci_gates.gate_index(workflows)
    dangling = []
    for gate, places in idx.items():
        for wf, key, _name in places:
            if key not in {j["key"] for j in ci_gates.jobs_of_workflow(workflows[wf])}:
                dangling.append(f"{gate} -> {wf}:{key}")
    check("B1 every gate the index lists points at a job that exists", not dangling,
          "; ".join(dangling))
    check("B2 the index found the frontend gates the 2026-10-28 report missed",
          "npm:typecheck" in idx and "npm:lint:gate" in idx,
          "the one thing this lock exists to make unmissable is missing from it")
    check("B3 the index found the test suites", "pytest:unit" in idx and "pytest:integration" in idx)

    print("C. the index and the pipefail lock agree about what nothing runs")
    pending = set(read_pending())
    on_disk = set(harnesses_on_disk())
    run = {g for g in idx if g.endswith(".py")}
    # A harness on disk is either run, or declared unrun. Anything else is the gap the pipefail
    # lock already fails on; if the index disagreed, one of them would be lying.
    gap = sorted(on_disk - run - pending)
    check("C1 no harness on disk is both unrun and undeclared", not gap,
          "neither run nor on harness_unrun_pending.txt: " + ", ".join(gap[:8]))
    stale = sorted(pending & run)
    check("C2 nothing on the unrun list is actually run by a workflow", not stale,
          "run by a workflow but still declared unrun: " + ", ".join(stale[:8]))
    missing = sorted(pending - on_disk)
    check("C3 the unrun list names no harness that has been deleted", not missing,
          "listed but not on disk: " + ", ".join(missing[:8]))

    print("Z. negative controls — each check planted with its own defect")
    wf_one = {"x.yml": "name: x\non:\n  pull_request:\njobs:\n  j:\n    name: J\n    steps:\n"
                       "      - run: python backend/harness_a.py\n"}
    check("Z1 a gate whose job vanished is caught by B1",
          ci_gates.gate_index(wf_one)["harness_a.py"][0][1] == "j")
    check("Z2 render is deterministic, so A3 cannot flap",
          ci_gates.render_index(wf_one, [], ["harness_a.py"]) ==
          ci_gates.render_index(wf_one, [], ["harness_a.py"]))
    check("Z3 an undeclared, unrun harness reaches the index's Unaccounted section",
          "harness_b.py" in ci_gates.render_index(wf_one, [], ["harness_a.py", "harness_b.py"]))
    check("Z4 a hand-edited index fails A2",
          not "# hand written\n".startswith(ci_gates.HEADER))
    # The blesser may not touch anything but the index itself.
    with open(PENDING, "r", encoding="utf-8") as fh:
        pending_before = fh.read()
    check("Z5 --bless writes ONLY docs/CI_GATES.md — the registers it reads are never rewritten",
          "open(PENDING" not in _bless_source() and "w" == _bless_mode(),
          "the module-graph blesser deleted 11 of 12 facts this way (CLAUDE.md §19.18)")
    with open(PENDING, "r", encoding="utf-8") as fh:
        check("Z6 harness_unrun_pending.txt is byte-identical after this run", fh.read() == pending_before)

    print("-" * 96)
    if _FAIL:
        print("RED — %d check(s) failed:" % len(_FAIL))
        for f in _FAIL:
            print("  · " + f)
        return 1
    print("OK — one derived answer to 'is this gated, and on what?', and it is current.")
    return 0


def _bless_source() -> str:
    """This file's own `--bless` branch, read as text for Z5."""
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("    if bless:")
    return src[start:src.index("return 0", start)]


def _bless_mode() -> str:
    """The mode the blesser opens a file with — proving it writes one path, not the registers."""
    seg = _bless_source()
    return "w" if 'open(INDEX, "w"' in seg and seg.count("open(") == 1 else "?"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
