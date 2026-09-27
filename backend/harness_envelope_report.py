"""Proof harness — Envelope report + envelope_short chargeback wiring (owner 2026-09-02, item 2).

Proves, stdlib-only and DB-free:
  A. expected_cash: t_cash canonical, store_cash legacy fallback (the _cash_position_core rule).
  B. count_fields: variance sign (counted − expected; negative = short), tolerance band, cent
     rounding.
  C. shortage_amount: the chargeback dollar is the ACTUAL missing cash, positive, and 0 for
     over/match (an overage is never a chargeback).
  D. chargeback_parent_row: rides the EXISTING mig-504 ops_chargeback contract — parent row
     (no parent_id), reason 'envelope_short', applied_to 'commission', status 'pending',
     incident_date = the envelope's close_date; never built for a non-positive amount.
  E. report_row + status_filter + totals: assembly, the owner's filterables (comments,
     chargebacks, over/short discrepancies), and the tile math.

Run: python3 backend/harness_envelope_report.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

from app.modules.closing.envelope_report import (  # noqa: E402
    expected_cash, count_fields, shortage_amount, chargeback_parent_row,
    report_row, status_filter, totals, by_employee, ENVELOPE_SHORT_REASON)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("A. expected_cash")
check("t_cash wins", expected_cash({"t_cash": 786.0, "store_cash": 700.0}) == 786.0)
check("legacy store_cash fallback", expected_cash({"t_cash": 0, "store_cash": 450.0}) == 450.0)
check("garbage → 0", expected_cash({"t_cash": "abc"}) == 0.0)

print("B. count_fields")
cf = count_fields(500.0, 460.0)
check("short: variance = counted − expected", cf["variance"] == -40.0 and cf["status"] == "short")
cf = count_fields(500.0, 512.34)
check("over detected", cf["variance"] == 12.34 and cf["status"] == "over")
check("exact match", count_fields(500.0, 500.0)["status"] == "match")
check("tolerance band → match", count_fields(500.0, 499.5, tolerance=1.0)["status"] == "match")
check("outside tolerance → short", count_fields(500.0, 498.0, tolerance=1.0)["status"] == "short")
check("cent rounding", count_fields(100.004, 100.0)["variance"] == 0.0)

print("C. shortage_amount")
check("short → positive missing cash", shortage_amount(-40.0) == 40.0)
check("over → 0", shortage_amount(12.0) == 0.0)
check("match → 0", shortage_amount(0.0) == 0.0)

print("D. chargeback_parent_row (mig-504 contract)")
crow = {"id": "row1", "close_date": "2026-09-01", "store_code": "Diversey",
        "employee_name": "Diana Antunez"}
p = chargeback_parent_row("org1", crow, "E123", "Diana Antunez", 40.0)
check("reason envelope_short", p["reason"] == ENVELOPE_SHORT_REASON == "envelope_short")
check("applied_to commission (rep pay; cascade handles overflow)", p["applied_to"] == "commission")
check("pending until management decides", p["status"] == "pending")
check("amount = the actual shortage", p["amount"] == 40.0)
check("incident_date = envelope close_date", p["incident_date"] == "2026-09-01")
check("PARENT row: no parent_id/covered_amount keys (mig-504: this side only creates parents)",
      "parent_id" not in p and "covered_amount" not in p)
check("idempotency key fields present (org,employee,store,reason,incident_date)",
      p["org_id"] == "org1" and p["employee_id"] == "E123" and p["store_code"] == "Diversey")
check("non-positive amount builds nothing",
      chargeback_parent_row("org1", crow, "E123", "D", 0) is None
      and chargeback_parent_row("org1", crow, "E123", "D", -5) is None)

print("E. report assembly + filters + totals")
count = {"counted_amount": 460.0, "expected_amount": 500.0, "variance": -40.0, "status": "short",
         "comment": "recount at pickup", "counted_by": "mgr", "chargeback_id": "cb1"}
cb = {"id": "cb1", "status": "pending", "amount": 40.0}
r1 = report_row({**crow, "t_cash": 500.0, "envelope_picture": "p.jpg"}, count, cb,
                {"verified": True}, "Chicago")
check("row carries declared + counted + variance + comment + chargeback",
      r1["declared_cash"] == 500.0 and r1["counted_amount"] == 460.0 and r1["variance"] == -40.0
      and r1["status"] == "short" and r1["comment"] == "recount at pickup"
      and r1["chargeback_status"] == "pending" and r1["chargeback_amount"] == 40.0)
r2 = report_row({**crow, "id": "row2", "t_cash": 300.0}, None, None, None, None)
check("uncounted row honest", r2["status"] == "uncounted" and r2["counted"] is False
      and r2["market"] == "(no market)" and r2["dm_verified"] is False)
r3 = report_row({**crow, "id": "row3", "t_cash": 200.0},
                {"counted_amount": 210.0, "expected_amount": 200.0, "variance": 10.0,
                 "status": "over"}, None, None, "Chicago")
rows = [r1, r2, r3]
check("status filter: short", [r["closing_row_id"] for r in status_filter(rows, "short")] == ["row1"])
check("status filter: discrepancy = short|over",
      {r["closing_row_id"] for r in status_filter(rows, "discrepancy")} == {"row1", "row3"})
check("status filter: commented", [r["closing_row_id"] for r in status_filter(rows, "commented")] == ["row1"])
check("status filter: chargeback", [r["closing_row_id"] for r in status_filter(rows, "chargeback")] == ["row1"])
check("status filter: uncounted", [r["closing_row_id"] for r in status_filter(rows, "uncounted")] == ["row2"])
check("unknown filter drops nothing", status_filter(rows, "bogus") == rows and status_filter(rows, "") == rows)
t = totals(rows)
check("totals tiles", t["envelopes"] == 3 and t["counted"] == 2 and t["short"] == 1 and t["over"] == 1
      and t["short_total"] == 40.0 and t["over_total"] == 10.0
      and t["chargebacks"] == 1 and t["chargeback_total"] == 40.0, str(t))

# ── BY EMPLOYEE (owner 2026-09-26, "report by user") ──────────────────────────────────────────────
# The rollup answers "who is short, how often, by how much" across a range. Its whole correctness
# claim is that it does not RE-DERIVE anything: each line is `totals()` over that employee's rows.
# So the test that matters is the INVARIANT — the rollup must add back up to the tiles, for every
# figure, or one of the two is lying to the owner.
be = by_employee(rows)
# The name comes from the fixture rather than a literal, so renaming the fixture cannot make this
# check silently vacuous — all three rows are the same person, so the rollup must be ONE line.
check("by_employee: one line per employee",
      [e["employee_name"] for e in be] == [crow["employee_name"]], str(be))
e0 = be[0]
check("by_employee: counts and dollars carry over",
      e0["envelopes"] == 3 and e0["counted"] == 2 and e0["short"] == 1 and e0["over"] == 1
      and e0["short_total"] == 40.0 and e0["over_total"] == 10.0
      and e0["chargebacks"] == 1 and e0["chargeback_total"] == 40.0, str(e0))
check("by_employee: uncounted is envelopes minus counted", e0["uncounted"] == 1, str(e0))
check("by_employee: net_variance is over MINUS short", e0["net_variance"] == -30.0, str(e0))
check("by_employee: first/last close date span the group",
      e0["first_close"] == "2026-09-01" and e0["last_close"] == "2026-09-01", str(e0))
for k in ("envelopes", "counted", "short", "over", "match", "chargebacks"):
    check(f"INVARIANT: rollup sums to the tiles — {k}", sum(e[k] for e in be) == t[k],
          f"{sum(e[k] for e in be)} vs {t[k]}")
for k in ("short_total", "over_total", "chargeback_total"):
    check(f"INVARIANT: rollup sums to the tiles — {k}",
          round(sum(e[k] for e in be), 2) == t[k], f"{sum(e[k] for e in be)} vs {t[k]}")

# Two employees, worst first. $60 short must outrank $5 short however the rows arrive.
many = [
    {"employee_name": "Small Miss", "close_date": "2026-09-03", "store_address": "S1",
     "status": "short", "variance": -5.0, "counted": True},
    {"employee_name": "Big Miss", "close_date": "2026-09-02", "store_address": "S2",
     "status": "short", "variance": -60.0, "counted": True},
    {"employee_name": "Big Miss", "close_date": "2026-09-04", "store_address": "S3",
     "status": "match", "variance": 0.0, "counted": True},
]
mb = by_employee(many)
check("by_employee: ordered worst-first by dollars short",
      [e["employee_name"] for e in mb] == ["Big Miss", "Small Miss"], str([e["employee_name"] for e in mb]))
check("by_employee: stores the person touched are listed, deduped and sorted",
      mb[0]["stores"] == ["S2", "S3"], str(mb[0]["stores"]))
check("by_employee: date span across several days",
      mb[0]["first_close"] == "2026-09-02" and mb[0]["last_close"] == "2026-09-04", str(mb[0]))

# An employee whose envelopes were NEVER counted has nothing measured. They must not sort to the
# top as if they were the worst, and must not read as a clean sheet either — `uncounted` says so.
never = by_employee([
    {"employee_name": "Never Counted", "close_date": "2026-09-05", "store_address": "S4",
     "status": "uncounted", "variance": None, "counted": False},
    {"employee_name": "Was Short", "close_date": "2026-09-05", "store_address": "S4",
     "status": "short", "variance": -1.0, "counted": True},
])
check("by_employee: an uncounted-only employee is not ranked worst",
      [e["employee_name"] for e in never] == ["Was Short", "Never Counted"], str(never))
check("by_employee: uncounted-only reads 0 short / 1 uncounted, never a clean match",
      never[1]["short"] == 0 and never[1]["uncounted"] == 1 and never[1]["match"] == 0, str(never[1]))

# A blank name is a row that still has to be accounted for, not a row that vanishes.
blank = by_employee([{"employee_name": "", "close_date": "2026-09-06", "status": "short",
                      "variance": -2.0, "counted": True}])
check("by_employee: a blank employee name is surfaced, not dropped",
      len(blank) == 1 and blank[0]["employee_name"] == "(unnamed)", str(blank))
check("by_employee: empty input is an empty rollup",
      by_employee([]) == [] and by_employee(None) == [])

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_envelope_report: ALL PASS")
