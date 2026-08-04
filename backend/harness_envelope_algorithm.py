"""Standalone proof harness for app/modules/closing/envelope.py's PURE functions (select_envelopes
fewest-envelopes algorithm + cadence_due). No DB, no FastAPI — imports the module directly off disk so
it can run without the backend's full dependency set installed. See harness_eep_retail_ops.py for the
DB-backed behavioral proof of the rest of the EEP package (netting, expense lines, payout-due, P&L
push gating, envelope-withdrawal).

Run: `cd backend && python3 harness_envelope_algorithm.py`
"""
import sys
import os
import importlib.util

ENVELOPE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app/modules/closing/envelope.py")
spec = importlib.util.spec_from_file_location("envelope", ENVELOPE_PATH)
envelope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envelope)

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f" FAIL {name}")


print("== select_envelopes ==")

# 1. Single envelope exactly covers -> picks it, no shortfall.
r = envelope.select_envelopes(
    [{"closing_row_id": "a", "close_date": "2026-07-01", "available": 500.0}], 500.0)
check("single exact match picks 1 envelope", len(r["picks"]) == 1 and r["picks"][0]["closing_row_id"] == "a")
check("single exact match: no shortfall", r["shortfall"] == 0.0)
check("single exact match: total_taken == required", r["total_taken"] == 500.0)

# 2. Multiple envelopes could each individually cover -> pick the SMALLEST sufficient one.
envs = [
    {"closing_row_id": "big", "close_date": "2026-07-01", "available": 1000.0},
    {"closing_row_id": "small_sufficient", "close_date": "2026-07-02", "available": 600.0},
    {"closing_row_id": "too_small", "close_date": "2026-07-03", "available": 200.0},
]
r = envelope.select_envelopes(envs, 500.0)
check("picks the SMALLEST sufficient envelope (not the biggest)",
      len(r["picks"]) == 1 and r["picks"][0]["closing_row_id"] == "small_sufficient")
check("take == required (not the envelope's full amount)", r["picks"][0]["take"] == 500.0)

# 3. Tie on the smallest-sufficient amount -> OLDEST close_date wins.
envs = [
    {"closing_row_id": "newer", "close_date": "2026-07-10", "available": 500.0},
    {"closing_row_id": "older", "close_date": "2026-07-01", "available": 500.0},
]
r = envelope.select_envelopes(envs, 500.0)
check("tie on sufficient amount -> oldest date wins", r["picks"][0]["closing_row_id"] == "older")

# 4. No single envelope suffices -> greedy largest-first.
envs = [
    {"closing_row_id": "e1", "close_date": "2026-07-01", "available": 300.0},
    {"closing_row_id": "e2", "close_date": "2026-07-02", "available": 250.0},
    {"closing_row_id": "e3", "close_date": "2026-07-03", "available": 100.0},
]
r = envelope.select_envelopes(envs, 500.0)
check("greedy: fewest envelopes (2, not 3)", len(r["picks"]) == 2)
check("greedy: takes largest first (e1 then e2)",
      [p["closing_row_id"] for p in r["picks"]] == ["e1", "e2"])
check("greedy: e1 fully drained (300)", r["picks"][0]["take"] == 300.0)
check("greedy: e2 partially drained to cover remainder (200)", r["picks"][1]["take"] == 200.0)
check("greedy: total_taken == required", r["total_taken"] == 500.0)
check("greedy: no shortfall", r["shortfall"] == 0.0)

# 5. Greedy tie-break: equal amounts -> oldest date drained first.
envs = [
    {"closing_row_id": "e_new", "close_date": "2026-07-10", "available": 200.0},
    {"closing_row_id": "e_old", "close_date": "2026-07-01", "available": 200.0},
    {"closing_row_id": "e_mid", "close_date": "2026-07-05", "available": 200.0},
]
r = envelope.select_envelopes(envs, 350.0)
check("greedy tie-break: oldest first, then next-oldest",
      [p["closing_row_id"] for p in r["picks"]] == ["e_old", "e_mid"])
check("greedy tie-break: first envelope fully drained", r["picks"][0]["take"] == 200.0)
check("greedy tie-break: second envelope partially drained", r["picks"][1]["take"] == 150.0)

# 6. Insufficient total -> shortfall reported, everything eligible taken.
envs = [
    {"closing_row_id": "e1", "close_date": "2026-07-01", "available": 100.0},
    {"closing_row_id": "e2", "close_date": "2026-07-02", "available": 50.0},
]
r = envelope.select_envelopes(envs, 500.0)
check("insufficient: takes everything available", r["total_taken"] == 150.0)
check("insufficient: shortfall = required - available", r["shortfall"] == 350.0)
check("insufficient: both envelopes picked", len(r["picks"]) == 2)

# 7. Zero/negative-available envelopes are never picked (already netted to <=0 -> excluded from pool).
envs = [
    {"closing_row_id": "zero", "close_date": "2026-07-01", "available": 0.0},
    {"closing_row_id": "neg", "close_date": "2026-07-02", "available": -50.0},
    {"closing_row_id": "ok", "close_date": "2026-07-03", "available": 300.0},
]
r = envelope.select_envelopes(envs, 100.0)
check("zero/negative envelopes excluded from the pool",
      all(p["closing_row_id"] not in ("zero", "neg") for p in r["picks"]))

# 8. required_amount == 0 -> no picks, no shortfall (idempotent no-op).
r = envelope.select_envelopes([{"closing_row_id": "e1", "close_date": "2026-07-01", "available": 500.0}], 0)
check("required=0 -> no picks", r["picks"] == [])
check("required=0 -> no shortfall", r["shortfall"] == 0.0)

# 9. Determinism: same input (even in a different input order) -> same output.
envs_a = [
    {"closing_row_id": "e1", "close_date": "2026-07-01", "available": 300.0},
    {"closing_row_id": "e2", "close_date": "2026-07-02", "available": 250.0},
]
envs_b = list(reversed(envs_a))
ra = envelope.select_envelopes(envs_a, 500.0)
rb = envelope.select_envelopes(envs_b, 500.0)
check("deterministic regardless of input order",
      [p["closing_row_id"] for p in ra["picks"]] == [p["closing_row_id"] for p in rb["picks"]])


print("\n== net_row / net_store_day ==")
exp_by_row = {"row1": 40.0}
wd_by_row = {"row1": 60.0}
check("net_row subtracts approved expenses + withdrawals",
      envelope.net_row(500.0, "row1", exp_by_row, wd_by_row) == 400.0)
check("net_row with no expense/withdrawal history == gross (byte-identical pre-migration)",
      envelope.net_row(500.0, "row_unknown", {}, {}) == 500.0)
check("net_row can go negative (over-withdrawn signal, not floored)",
      envelope.net_row(100.0, "row1", exp_by_row, wd_by_row) == 0.0)

exp_sd = {("S1", "2026-07-01"): 25.0}
wd_sd = {("S1", "2026-07-01"): 10.0}
check("net_store_day nets the (store,date) aggregate",
      envelope.net_store_day(1000.0, "S1", "2026-07-01", exp_sd, wd_sd) == 965.0)
check("net_store_day with empty dicts == gross unchanged (empty-config == today's behaviour)",
      envelope.net_store_day(1000.0, "S1", "2026-07-01", {}, {}) == 1000.0)


print("\n== cadence_due ==")
due, amt = envelope.cadence_due("daily", None, None, "2026-08-04", 123.45)
check("daily always due", due is True and amt == 123.45)

# weekly: anchor=1 (Tuesday). 2026-08-04 is a Tuesday.
due, amt = envelope.cadence_due("weekly", 1, None, "2026-08-04", 500.0)
check("weekly due on the anchor weekday", due is True and amt == 500.0)
due, amt = envelope.cadence_due("weekly", 2, None, "2026-08-04", 500.0)
check("weekly NOT due off the anchor weekday", due is False and amt == 0.0)

# monthly: anchor=31 in Feb (28-day, non-leap 2026) clamps to 28.
due, amt = envelope.cadence_due("monthly", 31, None, "2026-02-28", 900.0)
check("monthly anchor clamps to real month-end (31 -> 28 in Feb 2026)", due is True and amt == 900.0)
due, amt = envelope.cadence_due("monthly", 15, None, "2026-08-15", 900.0)
check("monthly due on exact anchor day", due is True)
due, amt = envelope.cadence_due("monthly", 15, None, "2026-08-16", 900.0)
check("monthly not due off the anchor day", due is False)

# biweekly: anchor_date 2026-07-01, +14 = 2026-07-15, +28 = 2026-07-29.
due, amt = envelope.cadence_due("biweekly", None, "2026-07-01", "2026-07-15", 700.0)
check("biweekly due exactly 14 days after anchor", due is True and amt == 700.0)
due, amt = envelope.cadence_due("biweekly", None, "2026-07-01", "2026-07-10", 700.0)
check("biweekly not due mid-cycle", due is False and amt == 0.0)
due, amt = envelope.cadence_due("biweekly", None, None, "2026-07-15", 700.0)
check("biweekly with no anchor_date configured -> never due (no reference point)", due is False)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
