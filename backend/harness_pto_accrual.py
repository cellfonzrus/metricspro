"""Pure-logic proof harness for the PTO accrual engine (mod-people, migration 403).

Runs the ACTUAL shipped functions from app.modules.storeops.pto_accrual against synthetic data —
no DB, no network. Run: `python3 harness_pto_accrual.py` from backend/.

Proves:
  1. Config layering: employee override > role override > org default > code default, with None on
     an override row meaning "inherit".
  2. Basic accrual across 2 stores in 'accrue' mode: cost follows worked-hours share per store.
  3. 'on_use' mode: no cost until taken, cost lands at the employee's home store when taken.
  4. max_accrual_hours cap: excess accrual is forfeited, payable_balance never exceeds the cap.
  5. enabled=False fully excludes an employee (no accrual, no cost, no ledger row).
  6. taken_hours_from_time_off prorates a month-spanning PTO block correctly across two periods.
  7. month_bounds is correct for a 28/29/30/31-day month.
  8. Idempotent ledger re-run: simulated delete-by-(org,period)-then-insert leaves no duplicates.
  9. expense_cells_from_stores includes a zero-cost store (so a stale nonzero system-line gets
     cleared on re-run, not left stale).
"""
import sys
from datetime import date

sys.path.insert(0, ".")

from app.modules.storeops.pto_accrual import (   # noqa: E402
    DEFAULT_CONFIG, resolve_effective_config, month_bounds, taken_hours_from_time_off,
    hours_worked_from_shifts, compute_pto, ledger_rows, expense_cells_from_stores,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── 1. config layering ──────────────────────────────────────────────────────────────────────────
org_row = {"enabled": True, "accrual_rate": 0.0385, "mode": "accrue", "cost_basis": "payscale_rate",
           "max_accrual_hours": None, "hours_per_pto_day": 8, "counts_as_pto_types": ["PTO"]}
role_row = {"accrual_rate": 0.05, "max_accrual_hours": None}       # only overrides accrual_rate
emp_row = {"max_accrual_hours": 40}                                # only overrides the cap

eff0 = resolve_effective_config(None, None, None)
check("t1a: no rows at all -> pure code defaults (types normalized to lowercase by design)",
      eff0["accrual_rate"] == DEFAULT_CONFIG["accrual_rate"] and eff0["mode"] == DEFAULT_CONFIG["mode"]
      and eff0["cost_basis"] == DEFAULT_CONFIG["cost_basis"]
      and eff0["max_accrual_hours"] == DEFAULT_CONFIG["max_accrual_hours"]
      and eff0["hours_per_pto_day"] == DEFAULT_CONFIG["hours_per_pto_day"]
      and eff0["counts_as_pto_types"] == ["pto"], eff0)

eff1 = resolve_effective_config(org_row, None, None)
check("t1b: org row alone applies", eff1["accrual_rate"] == 0.0385 and eff1["mode"] == "accrue")

eff2 = resolve_effective_config(org_row, role_row, None)
check("t1c: role override wins over org for accrual_rate", eff2["accrual_rate"] == 0.05)
check("t1d: role row's None max_accrual_hours does NOT clobber org's None (both None -> still None)",
      eff2["max_accrual_hours"] is None)

eff3 = resolve_effective_config(org_row, role_row, emp_row)
check("t1e: employee override wins over role for max_accrual_hours", eff3["max_accrual_hours"] == 40)
check("t1f: employee row's absence of accrual_rate leaves the role's value in place (inherit)",
      eff3["accrual_rate"] == 0.05)
check("t1g: cost_basis inherited all the way from org since neither override sets it",
      eff3["cost_basis"] == "payscale_rate")


# ── 2 & 3. compute_pto — 2 stores, 2 employees, 2 modes ────────────────────────────────────────────
hours = {
    "E1": {"Store1": 60.0, "Store2": 20.0},   # accrue mode, no PTO taken
    "E2": {"Store1": 40.0},                   # on_use mode, took 8 hrs this period
}
taken = {"E2": 8.0}
rates = {"E1": 20.0, "E2": 15.0}
cfg_accrue = dict(DEFAULT_CONFIG)              # accrual_rate 0.0385, mode 'accrue'
cfg_on_use = {**DEFAULT_CONFIG, "mode": "on_use"}
cfg_by_emp = {"E1": cfg_accrue, "E2": cfg_on_use}
home_store = {"E1": "Store1", "E2": "Store1"}

result = compute_pto(hours, taken, rates, cfg_by_emp, home_store_by_employee=home_store)
e1, e2 = result["employees"]["E1"], result["employees"]["E2"]

check("t2a: E1 accrued_hours = 80 * 0.0385", abs(e1["accrued_hours"] - 80 * 0.0385) < 1e-6, e1)
check("t2b: E1 cost (accrue mode) = accrued_hours * rate", abs(e1["cost"] - round(80 * 0.0385 * 20, 2)) < 0.01, e1)
check("t2c: E1's cost splits 60/20 across Store1/Store2 proportional to hours worked",
      abs(e1["by_store"]["Store1"]["cost"] - e1["cost"] * 0.75) < 0.02 and
      abs(e1["by_store"]["Store2"]["cost"] - e1["cost"] * 0.25) < 0.02, e1["by_store"])

check("t3a: E2 (on_use) cost = taken_hours * rate, NOT accrued_hours * rate",
      abs(e2["cost"] - 8 * 15) < 1e-6, e2)
check("t3b: E2's on_use cost lands entirely at the home store", e2["by_store"]["Store1"]["cost"] == e2["cost"], e2)
check("t3c: E2 still accrues hours for balance purposes even though cost is 0 until taken",
      abs(e2["accrued_hours"] - 40 * 0.0385) < 1e-6, e2)

store_rollup = result["stores"]
check("t2d: store rollup sums both employees at Store1 (E1's accrue share + E2's on_use taken cost)",
      abs(store_rollup["Store1"]["cost"] - (e1["by_store"]["Store1"]["cost"] + e2["cost"])) < 0.02,
      store_rollup)
check("t2e: Store2 only has E1's accrue-mode share (E2 never worked there)",
      abs(store_rollup["Store2"]["cost"] - e1["by_store"]["Store2"]["cost"]) < 0.02, store_rollup)


# ── 4. cap ───────────────────────────────────────────────────────────────────────────────────────
cfg_capped = {**DEFAULT_CONFIG, "max_accrual_hours": 80}
r_cap = compute_pto({"E3": {"Store3": 500.0}}, {}, {"E3": 10.0}, {"E3": cfg_capped},
                     home_store_by_employee={"E3": "Store3"}, prior_balance_by_employee={"E3": 75.0})
e3 = r_cap["employees"]["E3"]
raw = 500 * 0.0385   # 19.25
check("t4a: raw accrual exceeds available room (19.25 > 5) -> capped True", e3["capped"] is True, e3)
check("t4b: accrued_hours capped to exactly the remaining room (80 - 75 = 5)", abs(e3["accrued_hours"] - 5.0) < 1e-6, e3)
check("t4c: payable_balance never exceeds the cap", e3["payable_balance"] == 80.0, e3)
check("t4d: cost reflects only the capped (not raw) accrued hours", abs(e3["cost"] - 5.0 * 10.0) < 1e-6, e3)
check("t4e: accrued_hours_precap still reports the uncapped raw value for transparency", abs(e3["accrued_hours_precap"] - raw) < 1e-6, e3)

# cap with taken usage drawing the balance down first, freeing room
r_cap2 = compute_pto({"E4": {"Store3": 260.0}}, {"E4": 20.0}, {"E4": 10.0}, {"E4": cfg_capped},
                      home_store_by_employee={"E4": "Store3"}, prior_balance_by_employee={"E4": 78.0})
e4 = r_cap2["employees"]["E4"]
# balance_after_taken = 78 - 20 = 58; room = 80-58=22; raw = 260*0.0385=10.01 -> not capped
check("t4f: usage frees room under the cap so a normal accrual isn't blocked", e4["capped"] is False, e4)
check("t4g: payable_balance = (prior - taken) + accrued", abs(e4["payable_balance"] - (78 - 20 + e4["accrued_hours"])) < 1e-6, e4)


# ── 5. enabled=False excludes the employee entirely ─────────────────────────────────────────────
cfg_off = {**DEFAULT_CONFIG, "enabled": False}
r_off = compute_pto({"E5": {"Store1": 100.0}}, {}, {"E5": 20.0}, {"E5": cfg_off}, home_store_by_employee={"E5": "Store1"})
check("t5a: a disabled employee produces NO employee entry", "E5" not in r_off["employees"], r_off)
check("t5b: a disabled employee contributes NOTHING to the store rollup", r_off["stores"] == {}, r_off)


# ── 6. taken_hours_from_time_off proration across a month boundary ─────────────────────────────────
rows = [
    {"employee_id": "E6", "start_date": "2026-06-28", "end_date": "2026-07-02", "type": "PTO", "status": "approved"},
    {"employee_id": "E7", "start_date": "2026-07-05", "end_date": "2026-07-06", "type": "Sick", "status": "approved"},  # not PTO type
    {"employee_id": "E8", "start_date": "2026-07-10", "end_date": "2026-07-11", "type": "PTO", "status": "pending"},   # not approved
]
june_start, june_end = date(2026, 6, 1), date(2026, 6, 30)
july_start, july_end = date(2026, 7, 1), date(2026, 7, 31)
june_taken = taken_hours_from_time_off(rows, june_start, june_end, ["PTO"], 8.0)
july_taken = taken_hours_from_time_off(rows, july_start, july_end, ["PTO"], 8.0)
check("t6a: June gets the 3 days (28,29,30) of the spanning block = 24 hrs", june_taken.get("E6") == 24.0, june_taken)
check("t6b: July gets the remaining 2 days (1,2) = 16 hrs", july_taken.get("E6") == 16.0, july_taken)
check("t6c: June + July for E6 = the full 5-day block (40 hrs) — no double count, no loss",
      june_taken.get("E6", 0) + july_taken.get("E6", 0) == 40.0)
check("t6d: a non-PTO type (Sick) is excluded when pto_types=['PTO']", "E7" not in july_taken, july_taken)
check("t6e: a non-approved (pending) request never counts as taken", "E8" not in july_taken, july_taken)


# ── 7. month_bounds ──────────────────────────────────────────────────────────────────────────────
check("t7a: February non-leap 2026", month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28)))
check("t7b: February leap 2028", month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29)))
check("t7c: 31-day month", month_bounds("2026-07") == (date(2026, 7, 1), date(2026, 7, 31)))
check("t7d: December year-end", month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31)))


# ── 8. idempotent ledger re-run (simulated delete-by-(org,period)-then-insert) ─────────────────────
class FakeLedgerTable:
    def __init__(self):
        self.rows = []

    def run(self, org_id, period, rows):
        self.rows = [r for r in self.rows if not (r["org_id"] == org_id and r["period"] == period)]
        self.rows.extend(rows)


ledger_db = FakeLedgerTable()
rows_run1 = ledger_rows("ORG1", "2026-07", result, run_by="test")
ledger_db.run("ORG1", "2026-07", rows_run1)
count_after_1 = len(ledger_db.rows)
# Re-run the SAME period again (e.g. payroll numbers changed and got recomputed) — must not double up.
rows_run2 = ledger_rows("ORG1", "2026-07", result, run_by="test")
ledger_db.run("ORG1", "2026-07", rows_run2)
count_after_2 = len(ledger_db.rows)
check("t8a: re-running the SAME period produces the SAME row count (no duplication)",
      count_after_1 == count_after_2 and count_after_1 > 0, (count_after_1, count_after_2))
check("t8b: ledger rows exist for both employees x their stores (E1 x 2 stores + E2 x 1 store)",
      len(rows_run1) == 3, rows_run1)

# A DIFFERENT period must not touch the first period's rows.
rows_aug = ledger_rows("ORG1", "2026-08", result, run_by="test")
ledger_db.run("ORG1", "2026-08", rows_aug)
check("t8c: a different period's rows coexist (delete is scoped to org+period, not org alone)",
      len(ledger_db.rows) == count_after_2 + len(rows_aug), (len(ledger_db.rows), count_after_2, len(rows_aug)))

# An employee that drops to ZERO activity on a re-run (e.g. their shifts were deleted) must have
# their stale ledger rows actually gone after the re-run — not left behind as a ghost cost.
result_dropped = {"employees": {"E1": {**e1, "by_store": {}}}}   # E1 now has no store activity
rows_dropped = ledger_rows("ORG1", "2026-07", result_dropped, run_by="test")
ledger_db.run("ORG1", "2026-07", rows_dropped)
check("t8d: an employee with zero activity on re-run leaves NO ledger rows for that period (stale cost cleared)",
      not any(r["employee_id"] == "E1" and r["period"] == "2026-07" for r in ledger_db.rows), ledger_db.rows)


# ── 9. expense_cells_from_stores includes zero-cost stores (clears a stale nonzero on re-run) ──────
stores_next_run = {"Store1": {"store": "Store1", "accrued_hours": 0.0, "taken_hours": 0.0, "cost": 0.0},
                    "Store2": {"store": "Store2", "accrued_hours": 1.0, "taken_hours": 0, "cost": 15.4}}
cells = expense_cells_from_stores(stores_next_run)
check("t9a: a store with cost 0 still gets an explicit cell (clears a stale prior nonzero on upsert)",
      any(c["store"] == "Store1" and c["amount"] == 0 for c in cells), cells)
check("t9b: cells sorted by store, amounts match", cells == [{"store": "Store1", "amount": 0.0}, {"store": "Store2", "amount": 15.4}], cells)


# ── worked-hours-from-shifts sanity (used to build hours_by_employee_store from real shift rows) ──
shifts = [
    {"employee_id": "E9", "store_code": "Store1", "scheduled_hours": 8, "actual_hours": 0},   # not clocked -> falls back to scheduled
    {"employee_id": "E9", "store_code": "Store1", "scheduled_hours": 8, "actual_hours": 7.5},  # clocked -> actual wins
    {"employee_id": "E9", "store_code": "Store2", "scheduled_hours": 4, "actual_hours": 0},
]
hbs = hours_worked_from_shifts(shifts)
check("t10a: unclocked shift falls back to scheduled, clocked shift uses actual (matches /payroll-by-store)",
      abs(hbs["E9"]["Store1"] - 15.5) < 1e-9, hbs)
check("t10b: a second store for the same employee is tracked separately", hbs["E9"]["Store2"] == 4.0, hbs)


# ── Report ─────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
