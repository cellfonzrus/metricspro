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
    report_row, status_filter, totals, ENVELOPE_SHORT_REASON)

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

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_envelope_report: ALL PASS")
