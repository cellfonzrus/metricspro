"""Offline proof (no DB/network) for THE TARGET VERDICT — owner bug report 2026-09-10:

    "on the employe dashboad under my tagrets it shows 0 achieved with on target, that needs to be
     fixed"

THE DEFECT. Six screens decided the verdict with the same one-liner:

    need > 0 ? `${need} to go` : 'target met' / '✓ on track'

`need` is `max(0, monthly - achieved)` (targets_engine.compute_scope). A category with NO TARGET SET
has monthly 0 — which is also exactly what a MISSING target row collapses to, `float(get(cat, 0) or 0)`
— so `need` is 0 and the screen rendered a green pass. A rep with no goals and no sales was told they
were on track. §A below reproduces that input against the REAL engine and pins that `need` alone
genuinely cannot tell the two apart, which is why the shared helper takes `monthly` as well.

WHY A NEW HARNESS. No existing harness covered `compute_scope`'s per-category output or the render
vocabulary: harness_area_targets.py proves `aggregate_stores` (Σ per-store == aggregate),
harness_dm_target_attribution.py the DM attribution, harness_targets_rep_name_join.py the rep-name
join. None of them can see a verdict rendered in TSX.

§B/§C/§D are STATIC assertions over the shipped frontend source, on purpose: every value involved is a
legal string and all six pages render perfectly, so neither tsc nor a build can see a screen that says
"on track" to somebody with no target (index §24, the proof-harness audit).

Run: `cd backend && python3 harness_target_state.py`
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import targets_engine as te   # noqa: E402
from harnesslib import js_code_only   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


FE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "src")


def src(rel):
    """The file's CODE, comments stripped (harnesslib.js_code_only).

    Required, not tidiness: the comment above each fixed line quotes the defect verbatim
    ("this read `need > 0 ? 'to go' : 'on track'`"), so a raw read matches the explanation and fails
    a file that is correct. That is exactly how §C2/§D2 failed on their first run here."""
    with open(os.path.join(FE, rel), encoding="utf-8") as fh:
        return js_code_only(fh.read())


# ══ A. THE ENGINE — the input that produced the lie, against the real code ═══════════════════════
TODAY = date(2026, 9, 10)
HOURS = {date(2026, 9, d): 8.0 for d in range(1, 21)}


def actuals(prem=0.0, byod=0.0, upg=0.0, acc=0.0, day=TODAY):
    return {day: {"prem": prem, "byod": byod, "upg": upg, "acc": acc}}


no_target = te.compute_scope({}, HOURS, {}, TODAY)
check("A1 a category with NO target set computes monthly 0",
      no_target["categories"]["activations"]["monthly"] == 0, no_target["categories"]["activations"])
check("A2 ... and achieved 0, because nothing was sold either",
      no_target["categories"]["activations"]["achieved_mtd"] == 0, no_target["categories"]["activations"])
check("A3 ... and THEREFORE need 0 — the exact input every screen was reading as 'target met'",
      no_target["categories"]["activations"]["need"] == 0, no_target["categories"]["activations"])

met = te.compute_scope({"activations": 10}, HOURS, actuals(prem=12), TODAY)
short = te.compute_scope({"activations": 10}, HOURS, actuals(prem=4), TODAY)
check("A4 a REAL target that was reached also reports need 0", met["categories"]["activations"]["need"] == 0,
      met["categories"]["activations"])
check("A5 a real target not yet reached reports the gap", short["categories"]["activations"]["need"] == 6,
      short["categories"]["activations"])
check("A6 SO `need` ALONE CANNOT TELL 'no goal' FROM 'goal met' — both are 0. This is the whole "
      "defect, and it is why targetState() takes `monthly` as well as `need`",
      no_target["categories"]["activations"]["need"] == met["categories"]["activations"]["need"] == 0
      and no_target["categories"]["activations"]["monthly"] != met["categories"]["activations"]["monthly"])

# ══ B. THE SHARED VOCABULARY ═════════════════════════════════════════════════════════════════════
ts = src("lib/target-state.ts")
check("B1 there is ONE shared verdict helper", "export function targetState(" in ts)
for kind in ("unknown", "unset", "met", "short"):
    check(f"B2 it names the '{kind}' state", f"'{kind}'" in ts)
check("B3 a MISSING figure is 'unknown' — not zero, not fine",
      re.search(r"if \(m == null \|\| n == null\)[\s\S]{0,120}kind: 'unknown'", ts) is not None)
check("B4 a computed scope with NO goal is 'unset' — checked BEFORE any pass can be returned",
      re.search(r"if \(m <= 0\)[\s\S]{0,120}kind: 'unset'", ts) is not None
      and ts.index("kind: 'unset'") < ts.index("kind: 'met'"))
check("B5 'unset' and 'unknown' are NOT passing — the absence of a goal is never a pass",
      ts.count("passing: false") == 3 and ts.count("passing: true") == 1)
check("B6 the helper takes need, NOT achieved, so a caller cannot hand in a pair that disagrees "
      "(a second way to compute the same answer is how this drifted)",
      "targetState(monthly: unknown, need: unknown)" in ts and "achieved" not in
      ts.split("export function targetState")[1])

# ══ C. EVERY RENDER SITE — the class, not just the reported instance ══════════════════════════════
SITES = [
    ("components/EmployeeWidgets.impl.tsx", "the employee dashboard 'My Targets' (REPORTED)"),
    ("app/(platform)/commcalc/targets/action-plan/page.tsx", "Action Plan (team box + per-store box)"),
    ("app/(platform)/commcalc/targets/page.tsx", "Targets table + detail card"),
    ("app/(platform)/commcalc/targets/my/page.tsx", "My Targets"),
]
OLD = re.compile(r"need\s*>\s*0\s*\?[\s\S]{0,200}?(target met|on track)")
for rel, label in SITES:
    body = src(rel)
    check(f"C1 {label} uses the shared verdict", "targetState(" in body
          and "@/lib/target-state" in body)
    check(f"C2 {label} no longer decides it from `need` alone", OLD.search(body) is None,
          (OLD.search(body).group(0)[:80] if OLD.search(body) else ""))

# The colour-only sites: green on a 0 `need` said "met" just as loudly as the words did.
tg = src("app/(platform)/commcalc/targets/page.tsx")
check("C3 the Targets table colours `need` from the verdict, not from `need > 0`",
      "color: targetState(a?.monthly, a?.need).color" in tg)
check("C4 ... and so does its detail card",
      "color: targetState(m.monthly, m.need).color" in tg)
my = src("app/(platform)/commcalc/targets/my/page.tsx")
check("C5 ... and My Targets", "color: targetState(m.monthly, m.need).color" in my)

# ══ D. THE REPORTED INSTANCE ═════════════════════════════════════════════════════════════════════
ew = src("components/EmployeeWidgets.impl.tsx")
check("D1 the widget's verdict comes from the helper", "const ts = targetState(m.monthly, m.need)" in ew)
check("D2 the phrase the owner saw is gone from the targets widget",
      "on track" not in ew.split("My Targets")[1].split("</Card>")[0])
check("D3 the gap is still shown when there IS a real gap",
      "ts.kind === 'short' ? `${v(m.need)} to go`" in ew)
check("D4 the progress bar is drawn FLAT and neutral with no target, instead of '0% of the way "
      "there' toward a goal that does not exist",
      "ts.kind === 'met' || ts.kind === 'short' ? pct : 0" in ew and "'var(--border)'" in ew)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
