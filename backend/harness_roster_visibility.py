#!/usr/bin/env python3
"""THE LOCK for "an inactive store or person does not show unless Show inactive is ticked".

OWNER DIRECTIVE 2026-11-14: *"once the store or employee is made inactive their data should [not] be
showing on any module unless the checkox is clicked for show inactive stores"*, and, asked whether
that should take their past money with it: *"yes the past earning will show in that months totals and
the year to date also"* — so hiding is a ROW rule, never a MONEY rule.

WHY A LOCK AND NOT JUST A FIX (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix"). The fact that
was wrong is not "one report lists a closed store"; it is that every caller carried its own copy of
"is this active", and the copies DISAGREED — `storeops.stores` readers took NULL as active (right:
the column is `BOOLEAN DEFAULT true`), while the employee roster narrowed with
`.eq("is_active", True)`, which drops NULL and so deleted live people. A fix without a lock lets the
next change quietly restore the patchwork. This program FAILS THE BUILD when:

  A. the one home stops existing, or stops answering NULL / `'false'` / `0` the way it must;
  B. a shared surface stops dereferencing it (the option source behind every filter bar, the store
     and employee rosters, the commission roster, the HR employee database);
  C. a roster-reading backend module hand-rolls the predicate MORE than its pinned count — the
     pending ledger may only SHRINK, and a file not in the ledger may not appear at all;
  D. the frontend home or the shared bar's control goes missing;
  E. the frontend pending ledger grows the same way.

THE LEDGERS ARE DEBT, STATED OUT LOUD: `backend/roster_visibility_pending.txt` and
`frontend/roster_visibility_pending.txt`. Each line is `<count> <path>`. Wire a caller to the home,
lower its number (delete the line at zero) in the SAME PR, and the lock stays green. Raising a number
or adding a path is how this goes red.

Run: `python backend/harness_roster_visibility.py` (DB-free, no network; exit 0 = green).
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BACKEND_APP = os.path.join(HERE, "app")
FRONTEND_SRC = os.path.join(REPO, "frontend", "src")
HOME_PY = os.path.join(BACKEND_APP, "modules", "core", "roster_visibility.py")
HOME_TS = os.path.join(FRONTEND_SRC, "lib", "roster-visibility.ts")
BAR_TSX = os.path.join(FRONTEND_SRC, "components", "StandardFilterBar.tsx")
FILTERS_TS = os.path.join(FRONTEND_SRC, "lib", "standard-filters.ts")
PENDING_PY = os.path.join(HERE, "roster_visibility_pending.txt")
PENDING_TS = os.path.join(REPO, "frontend", "roster_visibility_pending.txt")

# A module is IN SCOPE when it reads one of the three roster relations. Only there does a hand-rolled
# active test answer the owner's question; the same words over `connector_registry` or
# `closing_tender_def` are a different registry's own flag and none of this lock's business.
ROSTER_READ = re.compile(r"""table\(["'](?:stores|employees|app_users)["']\)""")

# Every way this platform has spelled the predicate by hand. `.eq("is_active", True)` is included
# because it is not merely a copy — it is the WRONG copy: it drops NULL.
PRED_PY = re.compile(
    r"""is_active["']?\s*\)\s*is\s+not\s+False"""
    r"""|is_active["']?\]\s*is\s+not\s+False"""
    r"""|\.eq\(\s*["']is_active["']\s*,\s*True\s*\)"""
    r"""|is_active["']?\s*\)\s*is\s+True""")
PRED_TS = re.compile(r"""is_active\s*(?:!==|===|==|!=)\s*(?:false|true)""")

# Each surface here is one the directive reaches EVERY report through; a line of evidence per entry.
WIRED_PY = {
    os.path.join("app", "modules", "core", "router.py"): [
        "roster_visibility", "include_inactive"],
    os.path.join("app", "modules", "storeops", "router.py"): [
        "roster_visibility", "_rv.is_active", "_rv.visible"],
    os.path.join("app", "modules", "commcalc", "router.py"): ["roster_visibility"],
    os.path.join("app", "modules", "hr", "router.py"): ["roster_visibility", "_rv.visible"],
}

FAILURES: list[str] = []


def bad(section: str, msg: str) -> None:
    FAILURES.append(f"[{section}] {msg}")


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def strip_comments_py(src: str) -> str:
    """A module can satisfy a source check by TALKING about the thing (the 2026-10-30 lesson from
    harness_report_toolbar_coverage: a page's explanatory comment mentioning `<ReportExportBar>` kept
    a gate green after the real element was deleted). Comments and docstrings are not code."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    return re.sub(r"(?m)^\s*#.*$|(?<!['\"])#[^\n'\"]*$", "", src)


def strip_comments_ts(src: str) -> str:
    src = re.sub(r"/\*(?:.|\n)*?\*/", "", src)
    return re.sub(r"(?m)^\s*//.*$|(?<![:'\"])//[^\n'\"]*$", "", src)


def load_pending(path: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in read(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        count, _, rel = line.partition(" ")
        try:
            out[rel.strip()] = int(count)
        except ValueError:
            bad("ledger", f"{os.path.basename(path)}: cannot read line {line!r} (want '<count> <path>')")
    return out


# ── A. the home exists and answers the two traps correctly ───────────────────────────────────────

def section_a() -> None:
    if not os.path.exists(HOME_PY):
        return bad("A", "the backend one home is GONE: app/modules/core/roster_visibility.py")
    sys.path.insert(0, HERE)
    try:
        from app.modules.core import roster_visibility as rv
    except Exception as e:                                      # pragma: no cover - import guard
        return bad("A", f"the one home does not import: {e}")

    if rv.is_active({}) is not True or rv.is_active({"is_active": None}) is not True:
        bad("A", "NULL / missing no longer reads as ACTIVE — the column is BOOLEAN DEFAULT true, so "
                 "this silently deletes every row whose flag was never set")
    if rv.is_active({"is_active": False}) is not False:
        bad("A", "an explicitly inactive row reads as active")
    for falsey in ("false", "False", "f", "0", "no", 0):
        if rv.is_active({"is_active": falsey}) is not False:
            bad("A", f"{falsey!r} off the wire reads as ACTIVE — the string 'false' is truthy in "
                     "Python, which is how exactly one caller gets this wrong")
    if rv.visible([{"is_active": False}]) != []:
        bad("A", "visible() no longer hides inactive rows BY DEFAULT — the default IS the directive")
    if len(rv.visible([{"is_active": False}], include_inactive=True)) != 1:
        bad("A", "the include_inactive opt-in no longer shows them")
    if rv.hidden_count([{"is_active": False}, {}]) != 1:
        bad("A", "hidden_count is gone/wrong — a surface that hides without being able to SAY what it "
                 "hid is how a list stops agreeing with its own total")
    if rv.INCLUDE_INACTIVE_PARAM != "include_inactive":
        bad("A", "the opt-in parameter has been renamed; twelve endpoints spell it include_inactive")
    if tuple(rv.POSTGREST_ACTIVE_FILTER) != ("is_active", "not.is.false"):
        bad("A", "the stated server-side filter changed: eq.true DROPS NULL, which was the live "
                 "employees-roster defect")


# ── B. the shared surfaces dereference it ────────────────────────────────────────────────────────

def section_b() -> None:
    for rel, needles in WIRED_PY.items():
        src = strip_comments_py(read(os.path.join(HERE, rel)))
        if not src:
            bad("B", f"{rel} is missing")
            continue
        for needle in needles:
            if needle not in src:
                bad("B", f"{rel} no longer carries {needle!r} — it has stopped dereferencing the one "
                         f"home (or stopped offering the opt-in), so this surface answers the "
                         f"directive with a copy again")

    # The option source behind every adopting report's pickers must narrow by DEFAULT.
    core = strip_comments_py(read(os.path.join(HERE, "app", "modules", "core", "router.py")))
    m = re.search(r"def filter_options\(([^)]*)\)", core)
    if not m:
        bad("B", "GET /core/filter-options is gone — that endpoint IS the Stores/People dropdown on "
                 "most reports")
    elif "include_inactive: bool = False" not in m.group(1):
        bad("B", "filter_options must default include_inactive to False: it is the shared option "
                 "source, and offering a closed store to filter by is the leak the directive names")

    # The store roster's named predicate must be a dereference, not a re-derivation.
    so = read(os.path.join(HERE, "app", "modules", "storeops", "router.py"))
    body = re.search(r"def _store_is_active\(.*?\n(.*?)\ndef ", so, re.S)
    if not body:
        bad("B", "storeops/router._store_is_active is gone; ~30 call sites read it")
    elif "_rv.is_active" not in strip_comments_py(body.group(1)):
        bad("B", "storeops/router._store_is_active has grown its own predicate again instead of "
                 "dereferencing core.roster_visibility")


# ── C/E. the two ledgers, shrink-only ────────────────────────────────────────────────────────────

def measure(root: str, exts: tuple[str, ...], pred: re.Pattern, scope: re.Pattern | None,
            skip: tuple[str, ...]) -> dict[str, int]:
    found: dict[str, int] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(exts):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, os.path.dirname(root) if root.endswith("app") else
                                  os.path.dirname(root))
            if any(s in path for s in skip):
                continue
            src = read(path)
            if scope and not scope.search(src):
                continue
            hits = len(pred.findall(src))
            if hits:
                found[rel.replace(os.sep, "/")] = hits
    return found


def ledger(section: str, label: str, found: dict[str, int], pending_path: str, wiring_hint: str) -> None:
    pinned = load_pending(pending_path)
    for rel, hits in sorted(found.items()):
        if rel not in pinned:
            bad(section, f"NEW hand-rolled active test in {rel} ({hits}). {wiring_hint} "
                         f"If it is genuinely about another registry's own flag, say so in "
                         f"{os.path.basename(pending_path)} with a comment and pin it.")
        elif hits > pinned[rel]:
            bad(section, f"{rel} went from {pinned[rel]} to {hits} hand-rolled active tests. "
                         f"The ledger may only SHRINK.")
    for rel, count in sorted(pinned.items()):
        now = found.get(rel, 0)
        if now < count:
            bad(section, f"{rel} is now down to {now} (pinned {count}) — re-pin it DOWNWARD in this "
                         f"PR, which is the only way the debt stays honest.")
    total_now, total_pin = sum(found.values()), sum(pinned.values())
    print(f"  {label}: {total_now} hand-rolled tests across {len(found)} files "
          f"(pinned {total_pin} across {len(pinned)})")


def section_c() -> None:
    found = measure(BACKEND_APP, (".py",), PRED_PY, ROSTER_READ, ("roster_visibility.py",))
    ledger("C", "backend", found, PENDING_PY,
           "Read core.roster_visibility.is_active instead.")


def section_d() -> None:
    home = read(HOME_TS)
    if not home:
        return bad("D", "the frontend one home is GONE: frontend/src/lib/roster-visibility.ts")
    for needed in ("rosterIsActive", "visibleRoster", "inactiveCount", "INACTIVE_LABEL",
                   "withIncludeInactive"):
        if f"export function {needed}" not in home and f"export const {needed}" not in home:
            bad("D", f"frontend home no longer exports {needed}")
    if "FALSE_WORDS" not in home:
        bad("D", "the frontend home no longer handles the wire spellings of false — the STRING "
                 "'false' is truthy in JavaScript")

    filters = strip_comments_ts(read(FILTERS_TS))
    if "rosterIsActive" not in filters:
        bad("D", "lib/standard-filters no longer dereferences the roster home, so the shared filters "
                 "have stopped honouring the directive for every adopting report at once")
    if "showInactive" not in filters:
        bad("D", "StandardFilterValue.showInactive is gone — that field IS the checkbox's state")
    if "standardInactiveHidden" not in filters:
        bad("D", "standardInactiveHidden is gone; a report that hides rows must be able to say so")

    bar = strip_comments_ts(read(BAR_TSX))
    if "INACTIVE_LABEL" not in bar or "inactiveHidden" not in bar:
        bad("D", "the shared filter bar no longer renders the Show inactive control")
    if 'type="checkbox"' not in bar:
        bad("D", "the Show inactive control is no longer a checkbox — the owner asked for a checkbox")
    if "showInactive: false" not in bar:
        bad("D", "Clear no longer resets Show inactive, so a reload is the only way back to the "
                 "default the directive asks for")


def section_e() -> None:
    found = measure(FRONTEND_SRC, (".ts", ".tsx"), PRED_TS, None, ("roster-visibility",))
    ledger("E", "frontend", found, PENDING_TS,
           "Read lib/roster-visibility.rosterIsActive instead.")


def main() -> int:
    print("harness_roster_visibility — one answer to 'is this store / person active' (§CORE-44)")
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    if FAILURES:
        print(f"\nRED — {len(FAILURES)} problem(s):")
        for f in FAILURES:
            print(f"  ✗ {f}")
        return 1
    print("\nGREEN — the one home holds, the shared surfaces dereference it, both ledgers are at or "
          "below their pins.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
