"""HARNESS — approved hours reach the payroll report (_apply_approved_hours).

Owner 2026-08-11: "apply the fixes and the payroll approved as Gina runs payroll which will eventually
touch the p&l."

This is the money path: the figure this function produces is what payroll is run from and what
flows into the P&L. The dangerous failures are (a) absorbing approvals into a period they do not
belong to, and (b) restating hours silently so nobody can see it happened.

  A. The approved figure wins, and pay follows it.
  B. EXACT-PERIOD rule — a wider or shifted window must not pick approvals up.
  C. The overlay is visible and auditable, never silent.
  D. It agrees with the approval board's own definition of effective hours.
  E. Degradation — no approvals, no matching employee, salaried rows, bad values.
  F. ARMED negative control.

Run: python3 harness_approved_hours.py     (pure — the function is fed rows directly)
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


# The function reads the DB through sb(); a fake client returns whatever the test stages, and RECORDS
# the filters so an over-broad query (one that ignores the period) fails instead of quietly passing.
CALLS = []


class _Q:
    def __init__(self, rows):
        self._rows, self._eq = rows, {}

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def execute(self):
        CALLS.append(dict(self._eq))
        rows = [r for r in self._rows
                if all(str(r.get(k)) == str(v) for k, v in self._eq.items() if k in r)]
        return type("R", (), {"data": rows})()


class _Client:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _n):
        return _Q(self._rows)


ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
P_START, P_END = "2026-07-23", "2026-08-05"
HI = (date.fromisoformat(P_END) + timedelta(days=1)).isoformat()   # get_payroll's exclusive upper


def approval(eid, approved=None, adj=None, dm="approved", hr="pending"):
    return {"org_id": ORG, "employee_id": eid, "period_start": P_START, "period_end": P_END,
            "hours_approved": approved, "adjustment_hours": adj, "dm_status": dm, "hr_status": hr}


def run(rows, approvals, lo=P_START, hi=HI):
    """Call _apply_approved_hours with a staged client."""
    import app.modules.storeops.router as R
    orig = R.sb
    R.sb = lambda: _Client(approvals)
    try:
        return R._apply_approved_hours(ORG, lo, hi, [dict(r) for r in rows])
    finally:
        R.sb = orig


def row(eid="E142", hours=25.0, rate=18.0, name="Nallely Espinoza", basis="hourly"):
    return {"employee_id": eid, "name": name, "actual_hours": hours, "pay_rate": rate,
            "actual_pay": round(hours * rate, 2), "pay_basis": basis}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the approved figure wins, and pay follows it")

out = run([row(hours=25.0, rate=18.0)], [approval("E142", approved=68.5)])[0]
check("A1 hours become the approved figure", out["actual_hours"], 68.5)
check("A2 pay is re-derived from them", out["actual_pay"], round(68.5 * 18.0, 2))
check("A3 the computed figure is preserved for audit", out["hours_computed"], 25.0)
check("A4 ... and the change is flagged", out["hours_from_approval"], True)

# With no explicit approval, an ADJUSTMENT still applies: computed + adjustment.
out = run([row(hours=25.0)], [approval("E142", approved=None, adj=3.5)])[0]
check("A5 an adjustment alone moves the hours", out["actual_hours"], 28.5)
check("A6 an explicit approval OUTRANKS the adjustment",
      run([row(hours=25.0)], [approval("E142", approved=40.0, adj=3.5)])[0]["actual_hours"], 40.0)

# An approval equal to the computed value is not a "change".
out = run([row(hours=25.0)], [approval("E142", approved=25.0)])[0]
check("A7 an approval matching the computation is not flagged as a change",
      out["hours_from_approval"], False)
check("A8 ... but the hours are still the approved ones", out["actual_hours"], 25.0)

check("A9 zero approved hours is honoured, not treated as 'unset'",
      run([row(hours=25.0)], [approval("E142", approved=0)])[0]["actual_hours"], 0.0)

section("B. EXACT-PERIOD rule — the money-critical guard")

CALLS.clear()
run([row()], [approval("E142", approved=68.5)])
check("B1 the query is pinned to the exact period",
      (CALLS[0].get("period_start"), CALLS[0].get("period_end")), (P_START, P_END))
check("B2 ... and to the org", CALLS[0].get("org_id"), ORG)

# A MONTH view must not absorb a fortnight's approvals.
out = run([row(hours=160.0)], [approval("E142", approved=68.5)], lo="2026-08-01", hi="2026-09-01")[0]
check("B3 a month window does not pick up a fortnight's approval", out["actual_hours"], 160.0)
check("B4 ... and is not annotated at all", "hours_from_approval" in out, False)

# A window off by ONE DAY is a different period.
out = run([row(hours=25.0)], [approval("E142", approved=68.5)],
          lo="2026-07-24", hi=HI)[0]
check("B5 a shifted start is a different period", out["actual_hours"], 25.0)
out = run([row(hours=25.0)], [approval("E142", approved=68.5)],
          lo=P_START, hi="2026-08-06")[0]
check("B6 the correct end boundary DOES match (hi is exclusive)", out["actual_hours"], 68.5)

# The old buggy week default must not collect the fortnight's approvals.
out = run([row(hours=25.0)], [approval("E142", approved=68.5)],
          lo="2026-07-29", hi="2026-08-05")[0]
check("B7 the old 07/29-08/04 week window picks up nothing", out["actual_hours"], 25.0)

section("C. visible, never silent")

out = run([row(hours=25.0)], [approval("E142", approved=68.5, dm="approved", hr="approved")])[0]
check("C1 the DM stage is reported", out["dm_status"], "approved")
check("C2 the HR stage is reported", out["hr_status"], "approved")
check("C3 a not-yet-HR-approved row still says so",
      run([row()], [approval("E142", approved=68.5)])[0]["hr_status"], "pending")
check("C4 the adjustment is reported", run([row()], [approval("E142", adj=2.0)])[0]["adjustment_hours"], 2.0)
check("C5 every overlaid row carries the pre-approval figure",
      all("hours_computed" in r for r in run([row()], [approval("E142", approved=1.0)])), True)

section("D. agrees with the approval board")

# The board's rule (payroll_approval.list_approvals): effective = approved if set else src + adj.
for approved, adj, want in ((68.5, 0.0, 68.5), (None, 3.5, 28.5), (None, None, 25.0), (0.0, 5.0, 0.0)):
    got = run([row(hours=25.0)], [approval("E142", approved=approved, adj=adj)])[0]["actual_hours"]
    check(f"D1 approved={approved} adj={adj} -> {want} (same rule as the board)", got, want)

section("E. degradation")

check("E1 no approvals at all leaves rows untouched",
      run([row(hours=25.0)], [])[0]["actual_hours"], 25.0)
check("E2 ... with no annotations", "hours_from_approval" in run([row()], [])[0], False)

two = run([row("E142", 25.0), row("E141", 30.0, name="Nancy Espinoza")],
          [approval("E142", approved=68.5)])
check("E3 an employee with no approval is untouched", two[1]["actual_hours"], 30.0)
check("E4 ... and the approved one still applies", two[0]["actual_hours"], 68.5)

sal = run([row(basis="salary", hours=80.0, rate=0.0)], [approval("E142", approved=70.0)])[0]
check("E5 a salaried row's HOURS still reflect the approval", sal["actual_hours"], 70.0)
check("E6 ... but its pay is NOT recomputed from hours x rate", "actual_pay" in sal
      and sal["actual_pay"] == round(80.0 * 0.0, 2), True)

check("E7 a non-numeric approved value degrades to 0 rather than raising",
      run([row(hours=25.0)], [approval("E142", approved="abc")])[0]["actual_hours"], 0.0)
check("E8 an empty-string approval is treated as UNSET, not zero",
      run([row(hours=25.0)], [approval("E142", approved="")])[0]["actual_hours"], 25.0)

section("F. ARMED negative control")

_f0 = len(FAIL)
check("F-armed month", run([row(hours=160.0)], [approval("E142", approved=68.5)],
                           lo="2026-08-01", hi="2026-09-01")[0]["actual_hours"], 68.5)
check("F-armed pay", run([row(hours=25.0, rate=18.0)],
                         [approval("E142", approved=68.5)])[0]["actual_pay"], 450.0)
fired = len(FAIL) - _f0
if fired == 2:
    FAIL[:] = FAIL[:_f0]
    PASS.append("F1 negative control fired on both wrong expectations (checks are live)")
else:
    FAIL.append(f"F1 NEGATIVE CONTROL DID NOT FIRE — {fired}/2 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
