"""LOCK: a shift's employee NAME is not an employee identity — and it cannot become one again.

Owner report 2026-10-12 (*"keep one hajra schdule is the same for both"*) and the standing design
rule *"if one thing is fixed for one tenant it should be a design fix not a temporary fix"*.
Index §STOREOPS-01.

THE CLASS. `storeops.shifts.employee_name` is a COPY of `storeops.employees.name`, written once at
insert time; `PATCH /storeops/employees/{id}` updates the roster row and nothing else. So RENAMING
AN EMPLOYEE ORPHANS EVERY SHIFT THEY ALREADY HAVE. Any surface that groups shifts by that copy loses
them; any surface that groups by `store_code` keeps them. Measured live 2026-10-12 at UPS centre
7710: 208.0h by store against 164.0h by employee for one week, one person, ONE employee record, two
spellings — every shift already carrying `employee_id = 'E274'`.

THE DESIGN FIX. TWO homes, one question each, and every caller dereferences them:
  · WHO is this row about  → `storeops/payroll_identity.build_shift_attributor` (an extension of the
    module that already owned "which employee id is which" and "which spelling is which employee").
  · WHAT do the hours add up to → `storeops/schedule_hours.scheduled_axes`, whose contract is the
    INVARIANT `sum(by_store) == sum(by_employee) == total`, with an explicit `__unattributed__`
    bucket so a row that names nobody is still counted and still visible.
  · the client mirror → `frontend/.../storeops/lib/scheduleAxes.ts`, read by the schedule page, its
    footer, its row totals and its export.

THIS GUARD is what stops the patchwork returning. It FAILS THE BUILD when
  A. a NEW site decides WHO a shift is about from a name (every existing one is pinned below with a
     reviewed classification, and a STALE pin fails too, so a pin cannot cover a future site),
  B. a caller that must dereference a home stops doing so,
  C. a SECOND derivation of either axis appears,
  D. the invariant stops being asserted, or `hours_trend` goes back to defaulting its roster away.

Classifications (the pinned inventory):
  DISPLAY-ONLY     — the name is rendered or attached to an output row. It labels, it does not join.
  NAME-IS-THE-FACT — the question genuinely IS about a spelling (a clock FEED's own name column, a
                     login proposal, a letter's addressee). Owned by `payroll_identity`'s matcher.
  ID-FIRST         — the key is an employee id and the name is only the fallback label for a row
                     that has no id. Reversing that order would be the defect; this way round is not.
  REPORTED-SIBLING — a SECOND name-canonicalisation home, for a different question, whose output is
                     money. MEASURED and reported, left for the owner's word because unifying it
                     moves computed figures. Pinned so it cannot be forgotten and cannot grow.
  FALLBACK-ONLY    — a name match kept for a caller that predates §STOREOPS-01 and may not have the
                     pre-grouped axes. Never used by an in-app caller; pinned so it cannot spread.
  SAME-COPY-BOTH-SIDES — numerator and denominator both read the SAME shift-name copy, so a rename
                     moves them together and the ratio is unharmed. It still mis-merges two people
                     who share a name; that is REPORTED (see §E) and is a different question.

To add a site: dereference a home (preferred), or justify one of the classifications IN THE CODE
COMMENT and pin it here in the same PR.

No DB, no network, no app imports.  Run:  cd backend && python3 harness_schedule_identity_lock.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "app")
_FE = os.path.normpath(os.path.join(_HERE, "..", "frontend", "src"))
_SCHED_PAGE = os.path.join(_FE, "app", "(platform)", "storeops", "schedule", "page.tsx")
_AXES_TS = os.path.join(_FE, "app", "(platform)", "storeops", "lib", "scheduleAxes.ts")
_EXPORT_TS = os.path.join(_FE, "app", "(platform)", "storeops", "lib", "scheduleExport.ts")

# A "name-as-identity site" = using an `employee_name` to decide WHO a row is about: comparing it,
# testing membership of it, or keying a dict/set on it. Deliberately broad — a reviewer cannot tell
# from the regex whether the next edit turns a label into a join, so every hit must be classified.
_NAME_RE = re.compile(
    r"""employee_name\s*(?:===|!==|==|!=)"""
    r"""|(?:===|!==|==|!=)\s*[^\n]{0,60}?employee_name"""
    r"""|setdefault\([^\n]{0,80}employee_name"""
    r"""|\.has\([^\n]{0,80}employee_name"""
    r"""|employee_name[^\n]{0,40}\bin\s+(?:emps|names|seen|keys|set\()"""
    r"""|\[\s*[^\]\n]{0,80}employee_name[^\]\n]{0,80}\]\s*(?:\|\|=|=[^=]|\+=)""")

# {relative path: {enclosing def/function: classification}}
PINNED = {
    # ── backend ────────────────────────────────────────────────────────────────────────────────
    "modules/storeops/accountability.py": {
        # `_key(name, emp_id)` groups the attendance lens. Both its numerator (exception rows) and
        # its denominator (shift_counts) are built from the SAME shift rows and therefore the same
        # name copy, so a rename moves them together and no rate is distorted. Two DIFFERENT people
        # who share a roster name would merge into one coaching row — reported, not fixed here,
        # because changing this key changes what a manager has already been shown.
        "aggregate": "SAME-COPY-BOTH-SIDES",
    },
    "modules/storeops/attention.py": {
        "_p_kiosk_unenrolled": "ID-FIRST",   # names.setdefault(eid, …) — keyed by id, name is the value
    },
    "modules/storeops/clock_variance_api.py": {
        "clock_variances": "DISPLAY-ONLY",   # writes a display name onto an output row
    },
    "modules/storeops/clockin_evidence.py": {
        "analyze": "NAME-IS-THE-FACT",     # a punch feed's own spelling; payroll_identity's domain
    },
    "modules/storeops/router.py": {
        "salary_advance_history": "DISPLAY-ONLY",
    },
    "modules/commcalc/payout_accrual.py": {
        "mtd_allocate": "ID-FIRST",
    },
    "modules/commcalc/router.py": {
        # Targets' own read of `storeops.shifts` canonicalises the rep SPELLING through commcalc's
        # `_rep_canon_map`/`_canon` — a second name-canonicalisation home, for sales-feed rep names,
        # whose output is commission money. It is not the scheduled-hours axis (these rows feed
        # Targets' open-days), and unifying it with payroll_identity would move computed rep pay, so
        # it is REPORTED to the owner and the commission agent rather than changed here.
        "_fetch_shifts": "REPORTED-SIBLING",
        "_keep": "DISPLAY-ONLY",
    },
    "modules/hr/letters.py": {
        "_cash_shortage_lookup": "NAME-IS-THE-FACT",   # the addressee of a letter IS a spelling
    },
    # ── frontend ───────────────────────────────────────────────────────────────────────────────
    "FE:schedule/page.tsx": {
        "addShift": "NAME-IS-THE-FACT",           # the add-shift form's own picker value
        "Cell": "DISPLAY-ONLY",                   # the chip label, resolved through the attributor
    },
    "FE:payroll-change-log/page.tsx": {
        "visibleItems": "ID-FIRST",               # matches employee_id; the name is only a label
    },
    "FE:lib/scheduleExport.ts": {
        "buildScheduleExport": "FALLBACK-ONLY",
    },
}

#: path → (needle, why). A caller that stops dereferencing a home fails the build.
MUST_DEREFERENCE = {
    "modules/storeops/schedule_hours.py": [
        ("build_shift_attributor", "the employee axis must come from the identity home"),
        ("def scheduled_axes(shifts, employees", "the one home for both axes must still be here, "
                                                "under its own name"),
        ("axes = scheduled_axes(live, employees)", "hours_trend must emit the SAME axes it reports"),
        ('"agrees"', "the invariant must still be computed and emitted"),
    ],
    "modules/storeops/router.py": [
        ("employees=_roster", "GET /schedule/hours-trend must hand hours_trend the roster"),
        ("_pid.build_shift_attributor(_roster)", "the hours-trend employee filter must resolve "
                                                 "through the identity home"),
        ("_pid.build_shift_attributor(_tmpl_roster)", "shift-template apply must dedupe on the "
                                                      "person, not the name copy"),
    ],
    "modules/storeops/payroll_identity.py": [
        ("def build_shift_attributor", "the identity home for our own rows"),
        ("UNATTRIBUTED_KEY", "a row that names nobody must still have a bucket"),
        ("def name_divergences", "a stale spelling must be reportable"),
    ],
}

FE_MUST_DEREFERENCE = {
    _SCHED_PAGE: [
        ("from '../lib/scheduleAxes'", "the schedule page must read the one axes home"),
        ("scheduledAxes(scopedShifts, attrRoster)", "both axes must come from one derivation"),
        ("axes.byStoreShifts", "the store axis rows must come from the one home"),
        ("employeeRows", "the employee axis rows must be the union that cannot hide hours"),
        ("parityGap", "the page must state it when the two totals disagree"),
        ("axes.unattributed", "hours that name nobody must be shown, not dropped"),
        ("dedupeWho", "the copy-week dedupes must key on the person"),
    ],
    _AXES_TS: [
        ("export function scheduledAxes", "the client axes home"),
        ("UNATTRIBUTED_KEY", "the visible bucket for hours that name nobody"),
        ("agrees:", "the invariant must be emitted client-side too"),
        ("normalize('NFD')", "the accent fold — 'í' is alnum and every plain fold kept it"),
    ],
    _EXPORT_TS: [
        ("employeeBuckets", "the export must take the pre-grouped axes, not re-group by name"),
    ],
}

PASS = 0
FAIL = 0


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}" + (f"\n      {detail}" if detail else ""))


def enclosing_def(src, pos):
    """The name of the def/function the match belongs to (Python `def`, or TS `function`/`const fn`).
    A match ON the declaring line is that function itself."""
    line_start = src.rfind("\n", 0, pos) + 1
    line_end = src.find("\n", pos)
    own = re.match(r"\s*(?:export\s+)?(?:async\s+)?(?:def|function)\s+(\w+)", src[line_start:line_end])
    if own:
        return own.group(1)
    best = "<module>"
    for m in re.finditer(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|function)\s+(\w+)"
                         r"|^\s*const\s+(\w+)\s*=\s*(?:\(|async|useMemo|function)", src[:pos], re.M):
        best = m.group(1) or m.group(2)
    return best


def _scan(path, rel):
    src = open(path, encoding="utf-8").read()
    hits = {}
    for m in _NAME_RE.finditer(src):
        fn = enclosing_def(src, m.start())
        hits.setdefault(fn, []).append(src.count("\n", 0, m.start()) + 1)
    return hits, src


def main():
    print("=" * 78)
    print("A. No unpinned site decides WHO a shift is about from a name")
    print("=" * 78)
    seen = {}
    targets = []
    for sub in ("modules/storeops", "modules/notify", "modules/commcalc", "modules/hr"):
        for dp, _d, fns in os.walk(os.path.join(_APP, sub)):
            for n in sorted(fns):
                if n.endswith(".py"):
                    p = os.path.join(dp, n)
                    targets.append((p, os.path.relpath(p, _APP).replace(os.sep, "/")))
    for dp, _d, fns in os.walk(os.path.join(_FE, "app", "(platform)", "storeops")):
        for n in sorted(fns):
            if n.endswith((".ts", ".tsx")):
                p = os.path.join(dp, n)
                rel = "FE:" + os.path.relpath(
                    p, os.path.join(_FE, "app", "(platform)", "storeops")).replace(os.sep, "/")
                targets.append((p, rel))

    for path, rel in targets:
        hits, _src = _scan(path, rel)
        for fn, lines in hits.items():
            cls = (PINNED.get(rel) or {}).get(fn)
            seen.setdefault(rel, set()).add(fn)
            ok(bool(cls),
               f"{rel}:{lines[0]} `{fn}` decides identity from an employee NAME and is NOT pinned",
               "Dereference payroll_identity.build_shift_attributor (or, client-side, "
               "scheduleAxes.buildShiftAttributor), or pin it in "
               "harness_schedule_identity_lock.py with a reviewed classification.")
    for rel, fns in PINNED.items():
        for fn in fns:
            ok(fn in (seen.get(rel) or set()),
               f"STALE PIN {rel} `{fn}` matches nothing — remove it so it cannot cover a new site")
    print(f"  … {sum(len(v) for v in seen.values())} name sites, all pinned")

    print()
    print("=" * 78)
    print("B. Every caller still dereferences the homes")
    print("=" * 78)
    for rel, needles in MUST_DEREFERENCE.items():
        path = os.path.join(_APP, rel)
        src = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        for needle, why in needles:
            ok(needle in src, f"{rel} no longer dereferences the home: {why}",
               f"missing: {needle!r}")
    for path, needles in FE_MUST_DEREFERENCE.items():
        src = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        rel = os.path.basename(path)
        for needle, why in needles:
            ok(needle in src, f"{rel} no longer dereferences the home: {why}",
               f"missing: {needle!r}")

    print()
    print("=" * 78)
    print("C. No SECOND derivation of either axis")
    print("=" * 78)
    page = open(_SCHED_PAGE, encoding="utf-8").read() if os.path.exists(_SCHED_PAGE) else ""
    # the exact shapes the defect lived in
    ok("filteredEmps.map(e => e.name)" not in page,
       "the schedule page builds an employee-name key set again — that set IS the inner join that "
       "dropped a renamed employee's hours")
    ok(re.search(r"employee_name\s*===", page) is None,
       "the schedule page compares employee_name to decide identity again",
       "group through `axes` / `attribute`, never on the name copy")
    ok("shifts.filter(s => keys.has" not in page,
       "the schedule page re-filters shifts through a name/store key set again")
    ok("function shiftHrs" not in page and "function hoursBetween(start: string, end: string) {" not in page,
       "the schedule page defines its own hours math again — it must read scheduleAxes")
    ok("_emp_name" not in open(os.path.join(_APP, "modules/storeops/schedule_hours.py"),
                               encoding="utf-8").read(),
       "schedule_hours defines the old name-keyed employee label again")
    axes_ts = open(_AXES_TS, encoding="utf-8").read() if os.path.exists(_AXES_TS) else ""
    ok(axes_ts.count("export function scheduledAxes") == 1, "two client-side axes derivations")
    sh_py = open(os.path.join(_APP, "modules/storeops/schedule_hours.py"), encoding="utf-8").read()
    ok(sh_py.count("def scheduled_axes(") == 1, "two server-side axes derivations")

    print()
    print("=" * 78)
    print("D. The invariant and the required roster cannot be defaulted away")
    print("=" * 78)
    ok("def hours_trend(shifts, *, anchor, employees," in sh_py,
       "hours_trend's `employees` roster is no longer REQUIRED — a default would let a caller fall "
       "silently back to the stale name copy, which is the whole defect")
    ok('"agrees": abs(store_total - emp_total) < 0.01' in sh_py,
       "scheduled_axes no longer asserts that the two axes agree")
    ok("agrees: Math.abs(storeTotal - employeeTotal) < 0.01" in axes_ts,
       "the client axes home no longer asserts that the two axes agree")

    print()
    print("=" * 78)
    print("E. REPORTED, not fixed — stated here so it cannot be forgotten")
    print("=" * 78)
    print("  · storeops/accountability.aggregate keys the attendance lens on a NAME. Its numerator "
          "and\n    denominator read the same shift-name copy so a rename cannot distort a rate, but "
          "TWO\n    DIFFERENT PEOPLE SHARING A ROSTER NAME still merge into one coaching row. Fixing "
          "it\n    changes a figure a manager has already been shown, so it waits for the owner's "
          "word.")
    print("  · `storeops.shifts.employee_name` itself is still written on insert (from the roster) "
          "and\n    still never updated on rename. Nothing now DEPENDS on it, and the divergence is "
          "shown on\n    screen; whether the stored copies get rewritten is an owner-run data "
          "decision, not code.")

    print()
    print("=" * 78)
    print(f"{'ALL GREEN' if not FAIL else 'RED'}    PASS {PASS} / {PASS + FAIL}")
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
