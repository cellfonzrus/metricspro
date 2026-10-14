"""LOCK — a guard that DERIVES its subjects from the code must not accept a smaller set (index §CORE-10).

OWNER DIRECTIVE 2026-10-14: *"this is very creitical finding , dont jsut write down make a gate so
nothing can passs thru unkowlingly , you were smart may be the other might not be as asmart"*. The
lesson from PR #477 was written into a note; the owner's answer was that a note is not a gate.

THE INSTANCE. `harness_browser_sweep_kinds.py` locks "an endpoint that can reach a live browser calls
`require_browser_service()` first". It DERIVED that set of endpoints by scanning `supply/router.py`,
then asserted one thing about the set itself: `ok(len(reach) >= 4, …)`. PR #477 moved the browser call
and its guard out of `read_vendor_catalog` into a helper. The endpoint left the derived set entirely,
and with it the per-endpoint assertion about its guard — **the check did not fail, it stopped
existing.** The only reason anything went red is that the count happened to sit exactly on its bound.
One more endpoint in the set and the hole would have shipped green.

THE CLASS, NOT THE INSTANCE. **A guard whose subjects are derived from the code decides for itself
what it is guarding, so the set shrinking is indistinguishable from the set being clean.** A lower
bound is not a measurement of that: it tolerates substitution (one subject in, one out), it tolerates
the scan's own axis going blind, and it never names what left. The strong form is to compare the
derived set with a DECLARED one, BOTH WAYS — `harness_browser_sweep_kinds.py` already does exactly
that with `eq(derived, BROWSER_MODULES_ELSEWHERE, …)` ten lines above the weak site, so the right
shape was in the same file as the wrong one.

WHAT THIS FAILS ON
  B1  a scanning harness asserts `len(<derived>) >= N` and the site is not on the debt list.
  B2  the debt list names a site that no longer exists — a stale pin, which silently re-permits the
      weak form if the variable ever comes back.
  B3  the debt list is longer than PINNED_MAX. **The list may only SHRINK.**

A RATCHET, NOT A SWEEP, and deliberately so. 51 sites were measured across 41 harnesses on 2026-10-14;
most are harmless fixture sanity ("there is something lagging in the fixture to build an item from")
and rewriting all of them in one change would be a 41-file diff nobody can review. So the existing
sites are pinned with their reason, a NEW one fails the build, and each one that is converted to the
declared-set form comes off the list and lowers the ceiling.

HOW TO SATISFY IT when it fires on new code — declare the set, do not pad the number:

    SUPPLY_BROWSER_ENDPOINTS = {"live_login_start", "read_vendor_catalog", …}   # module level
    eq(set(derived), SUPPLY_BROWSER_ENDPOINTS, "…are exactly the declared set")

Then a subject that leaves NAMES ITSELF, and a subject that arrives has to be declared by a human.

WHY THIS FILE IS NOT ITS OWN SUBJECT. It holds the weak form as example TEXT, so scanning itself would
report its own documentation. It is excluded by name, and `Z6` proves the exclusion is the only one.

Stdlib only, no DB, no network: `python3 harness_subject_set_lock.py`
"""
from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, "harness_subject_set_pending.txt")

# The length of the debt list, pinned. It may only go DOWN. Raising it is the one change this lock
# exists to make impossible to do quietly.
PINNED_MAX = 50

# This file documents the weak form, so it cannot be its own subject (see the docstring).
EXCLUDED = {"harness_subject_set_lock.py"}

# An assertion statement — the house's four spellings.
ASSERT_START = re.compile(r"^\s*(ok|check|ck|assert)\b")
# The weak form: a lower bound on the SIZE of something. The name is captured as a plain dotted
# identifier so `s_junk["cannot_answer"]` pins as `s_junk` — the variable is the subject, not the key.
WEAK = re.compile(r"len\(\s*([A-Za-z_][A-Za-z0-9_.]*)[^()]*\)\s*>=\s*(\d+)")
# A file that reads repo SOURCE, which is what makes its subject set derived rather than a fixture.
READS_SOURCE = re.compile(r"\.py[\"']|\.tsx?[\"']")

_PASS: list[str] = []
_FAIL: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> bool:
    (_PASS if cond else _FAIL).append(label + (("  [%s]" % detail) if detail else ""))
    return bool(cond)


def mask(text: str) -> str:
    """Blank the CONTENTS of every string literal and comment, keeping length and line breaks.

    Not cosmetic — it is what makes the two reads below honest. Unmasked, a `"(%d)"` in a label
    desynchronises the paren depth and swallows the next twenty lines into one statement that no
    longer starts with `ok(`, and prose in a docstring matches the weak pattern. The first draft of
    this file skipped the masking and found 35 of the 51 real sites — it would have pinned a short
    list and then passed the sites it could not see. Same class as the defect it guards.
    """
    out: list[str] = []
    i, n, q = 0, len(text), None
    while i < n:
        c = text[i]
        if q is None:
            if c in "\"'":
                if text[i:i + 3] in ('"""', "'''"):
                    q = text[i:i + 3]
                    out.append(q)
                    i += 3
                    continue
                q = c
                out.append(c)
                i += 1
                continue
            if c == "#":
                j = text.find("\n", i)
                j = n if j < 0 else j
                out.append(" " * (j - i))
                i = j
                continue
            out.append(c)
            i += 1
        else:
            if text.startswith(q, i) and (i == 0 or text[i - 1] != "\\"):
                out.append(q)
                i += len(q)
                q = None
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
    return "".join(out)


def statements(masked: str) -> list[str]:
    """Logical statements, so an assertion wrapped over four lines is read as one."""
    out, buf, depth = [], [], 0
    for line in masked.splitlines(True):
        buf.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            out.append("".join(buf))
            buf, depth = [], 0
    if buf:
        out.append("".join(buf))
    return out


def weak_sites(text: str) -> set[str]:
    """The names a file asserts a bare lower bound on, inside an assertion. '' when it does not scan
    repo source: a fixture's size is the author's own number, not a fact derived from the code."""
    masked = mask(text)
    if not (("os.walk(" in masked or ".read_text(" in masked or "open(" in masked)
            and READS_SOURCE.search(text)):
        return set()
    found = set()
    for st in statements(masked):
        if not ASSERT_START.match(st):
            continue
        for m in WEAK.finditer(st):
            found.add(m.group(1))
    return found


def read_pending(path: str = PENDING) -> list[str]:
    rows = []
    for raw in open(path, encoding="utf-8").read().splitlines():
        line = raw.split("#")[0].strip()
        if line:
            rows.append(line.split()[0])
    return rows


def derive(files: dict[str, str]) -> set[str]:
    """{filename: source} → the set of `file::name` weak sites."""
    out = set()
    for name in sorted(files):
        if name in EXCLUDED:
            continue
        for var in weak_sites(files[name]):
            out.add("%s::%s" % (name, var))
    return out


def main() -> int:
    files = {os.path.basename(p): open(p, encoding="utf-8").read()
             for p in sorted(glob.glob(os.path.join(HERE, "harness_*.py")))}
    ok(len(files) > 300, "the harnesses were found and read", "%d files" % len(files))

    sites = derive(files)
    pinned = read_pending()
    ok(len(pinned) == len(set(pinned)), "B0 the debt list names each site once",
       "%d rows" % len(pinned))

    new = sorted(sites - set(pinned))
    ok(not new,
       "B1 no harness asserts a bare lower bound on a set it derived from the code",
       "; ".join(new) if new else "%d known site(s), none new" % len(sites))

    stale = sorted(set(pinned) - sites)
    ok(not stale,
       "B2 every pinned site still exists (a stale pin re-permits the weak form silently)",
       "; ".join(stale) if stale else "none stale")

    ok(len(pinned) <= PINNED_MAX,
       "B3 the debt list is no longer than its pin — it may only shrink",
       "%d pinned, ceiling %d" % (len(pinned), PINNED_MAX))

    # ── Z negative controls — each check planted with its own defect (index 24) ────────────────
    _strong = ('import os\nREACH = {"a"}\nfor r, d, f in os.walk("x"):\n    pass\n'
               'src = open("app/x.py").read()\n'
               'eq(set(derived), REACH, "exactly the declared set")\n')
    _weak = _strong.replace('eq(set(derived), REACH, "exactly the declared set")',
                            'ok(len(derived) >= 4, "the endpoints were found")')
    ok(weak_sites(_strong) == set(),
       "Z1 control: the declared-set form (eq against a constant) is not a weak site")
    ok(weak_sites(_weak) == {"derived"},
       "Z2 ARMED: the same file with a bare lower bound IS a weak site", str(weak_sites(_weak)))
    ok(weak_sites('ok(len(rows) >= 4, "fixture")\n') == set(),
       "Z3 control: a harness that reads no repo source is not a subject (its sizes are its own)")
    _wrapped = ('import os\nos.walk("x")\nsrc = open("app/x.py").read()\n'
                'ok(\n    "a label with a stray ( paren",\n    len(reach) >= 4,\n)\n')
    ok(weak_sites(_wrapped) == {"reach"},
       "Z4 ARMED: a weak site wrapped over several lines, past a paren inside a label, is still seen",
       str(weak_sites(_wrapped)))
    ok(weak_sites('import os\nos.walk("x")\nopen("app/x.py")\n'
                  '"""prose: ok(len(x) >= 9) is the form we are warning about"""\n') == set(),
       "Z5 control: the weak form written in PROSE is not a site (masking did the work)")
    ok(derive({"harness_subject_set_lock.py": _weak}) == set()
       and derive({"harness_other.py": _weak}) == {"harness_other.py::derived"},
       "Z6 control: this file is excluded by name, and nothing else is")
    ok(sorted(derive({"harness_z.py": _weak, "harness_y.py": _strong})) == ["harness_z.py::derived"],
       "Z7 ARMED: a NEW unpinned site is exactly what B1 reports")

    for line in _PASS:
        print("  PASS  " + line)
    for line in _FAIL:
        print("  ✗ " + line)
    print("\n%d passed, %d failed" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("\nDeclare the set instead of bounding its size:\n"
              "  EXPECTED = {…}   # module level, a human wrote it\n"
              "  eq(set(derived), EXPECTED, \"…are exactly the declared set\")\n"
              "Then a subject that leaves names itself. Padding the number, or adding a row to\n"
              "harness_subject_set_pending.txt, is not a fix — the list may only shrink.")
    else:
        print("OK — no guard quietly decides for itself which subjects it is still guarding.")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
