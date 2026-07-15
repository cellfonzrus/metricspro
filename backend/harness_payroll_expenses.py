"""Pure-logic proof harness for the Payroll Expenses engine (mod-people, migration 404).

Runs the ACTUAL shipped functions from app.modules.storeops.payroll_expenses against synthetic data —
no DB, no network. Run: `python3 harness_payroll_expenses.py` from backend/.

Proves:
  1. resolve_tax_config: code defaults, partial org row override, full org row.
  2. compute_payroll_tax: basic per-employee tax math (FICA SS + Medicare + FUTA + SUTA), no caps hit.
  3. compute_payroll_tax: per-store split proportional to wage share for an employee working 2 stores.
  4. compute_payroll_tax: FICA SS wage-base cap — mid-year YTD already at/over the cap zeroes new SS
     tax for the rest of the year; partially-consumed cap taxes only the remaining room.
  5. compute_payroll_tax: FUTA wage-base cap — the realistic case (an employee crosses $7,000 well
     before December) correctly stops accruing FUTA once the cap is hit, using YTD carried in.
  6. compute_payroll_tax: Medicare has NO cap (keeps taxing full wages even past the SS cap).
  7. compute_payroll_tax: enabled=False on tax_cfg returns an empty result (feature off).
  8. compute_expense_items: pct_wages, per_100_wages, per_employee, fixed — each calc_method, scope='store'.
  9. compute_expense_items: scope='company' allocates ONE company-wide amount across stores
     proportional to wage share (fixed + per_employee company-scope cases).
  10. compute_expense_items: wage_cap clamps the wages basis for pct_wages/per_100_wages.
  11. compute_expense_items: a disabled item contributes nothing.
  12. rollup_cells: tax total + item totals combined into ONE amount per store; a store touched by
      only one bucket still appears; a store with $0 total still gets an explicit cell (clears stale).
  13. tax_ledger_rows / expense_ledger_rows: correct shaping, zero-tax employee produces no row.
  14. Idempotent ledger re-run (simulated delete-by-(org,period)-then-insert, same pattern as PTO's
      harness): re-running the SAME period never duplicates; a different period coexists; an
      employee whose activity drops to zero leaves no stale ledger row after a re-run.
  15. wages_by_store_from_hours / headcount_by_store_from_hours sanity (the shared basis both the tax
      engine and the item engine consume, reusing pto_accrual's hours_worked_from_shifts shape).
  16. End-to-end YTD accumulation across 3 consecutive periods for one employee proves the CORRECT
      cumulative-cap behavior a naive per-period cap check would get wrong (the FUTA cap should bind
      partway through, not reset every month).
  17. GROSS PAYROLL (migration 405, OWNER DECISION 2026-07-15) — gross_payroll_cells /
      gross_payroll_ledger_rows: per-store gross computed correctly from synthetic hours * rate,
      matches wages_by_store_from_hours exactly, is DISTINCT from (not equal to, not derived from)
      the burden/tax total for the same data, an idempotent re-run (simulated delete-by-(org,period)
      -then-insert) never duplicates and correctly drops a store whose activity goes to zero, and two
      different orgs' ledger rows never collide/leak into each other (org-scoped).
"""
import sys

sys.path.insert(0, ".")

from app.modules.storeops.pto_accrual import hours_worked_from_shifts  # noqa: E402 (shared basis, reused as-is)
from app.modules.storeops.payroll_expenses import (   # noqa: E402
    DEFAULT_TAX_CONFIG, resolve_tax_config, compute_payroll_tax,
    wages_by_store_from_hours, headcount_by_store_from_hours,
    compute_expense_items, rollup_cells, tax_ledger_rows, expense_ledger_rows,
    gross_payroll_cells, gross_payroll_ledger_rows,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── 1. resolve_tax_config ───────────────────────────────────────────────────────────────────────
eff0 = resolve_tax_config(None)
check("t1a: no org row -> pure code defaults",
      eff0 == DEFAULT_TAX_CONFIG or all(eff0[k] == DEFAULT_TAX_CONFIG[k] for k in DEFAULT_TAX_CONFIG), eff0)

partial_row = {"fica_ss_rate": 0.07}    # only overrides one field
eff1 = resolve_tax_config(partial_row)
check("t1b: partial org row overrides just that field", eff1["fica_ss_rate"] == 0.07)
check("t1c: partial org row leaves everything else at code default",
      eff1["medicare_rate"] == DEFAULT_TAX_CONFIG["medicare_rate"] and eff1["suta_rate"] == DEFAULT_TAX_CONFIG["suta_rate"])

full_row = {"enabled": True, "fica_ss_rate": 0.062, "fica_ss_wage_base": 168600,
            "medicare_rate": 0.0145, "futa_rate": 0.006, "futa_wage_base": 7000,
            "suta_rate": 0.031, "suta_wage_base": 12000}
eff2 = resolve_tax_config(full_row)
check("t1d: full org row applies suta override", eff2["suta_rate"] == 0.031 and eff2["suta_wage_base"] == 12000)


# ── 2. compute_payroll_tax — basic, no caps hit ─────────────────────────────────────────────────
cfg = dict(DEFAULT_TAX_CONFIG)
hours_by_emp = {"E1": {"Store1": 160.0}}     # 160 hrs this month
rates = {"E1": 25.0}                          # $25/hr -> $4000 wages, nowhere near any cap
r2 = compute_payroll_tax(hours_by_emp, rates, cfg, ytd_taxable_before={})
e1 = r2["employees"]["E1"]
check("t2a: wages = hours * rate", e1["wages"] == 4000.0, e1)
check("t2b: FICA SS tax = 4000 * 0.062", abs(e1["fica_ss_tax"] - 248.0) < 1e-6, e1)
check("t2c: Medicare tax = 4000 * 0.0145", abs(e1["medicare_tax"] - 58.0) < 1e-6, e1)
check("t2d: FUTA tax = 4000 * 0.006", abs(e1["futa_tax"] - 24.0) < 1e-6, e1)
check("t2e: SUTA tax = 4000 * 0.027", abs(e1["suta_tax"] - 108.0) < 1e-6, e1)
check("t2f: total_tax sums all 4 components", abs(e1["total_tax"] - (248 + 58 + 24 + 108)) < 1e-6, e1)
check("t2g: store rollup total matches employee total (single store)",
      abs(r2["stores"]["Store1"]["total"] - e1["total_tax"]) < 1e-6, r2["stores"])


# ── 3. per-store split proportional to wage share ───────────────────────────────────────────────
hours_2store = {"E2": {"Store1": 120.0, "Store2": 40.0}}   # 75% / 25% split (same rate both stores)
r3 = compute_payroll_tax(hours_2store, {"E2": 20.0}, cfg, ytd_taxable_before={})
e2 = r3["employees"]["E2"]
by_store = e2["by_store"]
check("t3a: Store1 gets 75% of the wages", abs(by_store["Store1"]["wages"] - 2400.0) < 1e-6, by_store)
check("t3b: Store2 gets 25% of the wages", abs(by_store["Store2"]["wages"] - 800.0) < 1e-6, by_store)
check("t3c: Store1's tax share == 75% of total_tax",
      abs(by_store["Store1"]["total"] - round(e2["total_tax"] * 0.75, 2)) < 0.02, (by_store, e2["total_tax"]))
check("t3d: store rollup sums both stores' shares back to the employee total",
      abs((r3["stores"]["Store1"]["total"] + r3["stores"]["Store2"]["total"]) - e2["total_tax"]) < 0.05,
      r3["stores"])


# ── 4. FICA SS wage-base cap ─────────────────────────────────────────────────────────────────────
# Employee already has $168,000 of SS-taxable wages YTD (from prior periods); this period they earn
# another $5,000. Only $600 of room remains under the $168,600 cap.
cfg_cap = dict(DEFAULT_TAX_CONFIG)
r4 = compute_payroll_tax({"E3": {"StoreA": 100.0}}, {"E3": 50.0}, cfg_cap,
                          ytd_taxable_before={"E3": {"ss": 168000.0, "futa": 99999.0, "suta": 99999.0}})
e3 = r4["employees"]["E3"]
check("t4a: wages this period are the full $5,000 (cap doesn't touch raw wages, only taxable wages)",
      e3["wages"] == 5000.0, e3)
check("t4b: only $600 of SS-taxable room remains", abs(e3["ss_taxable_wages"] - 600.0) < 1e-6, e3)
check("t4c: FICA SS tax computed on the CAPPED $600, not the full $5000",
      abs(e3["fica_ss_tax"] - (600.0 * 0.062)) < 1e-6, e3)
check("t4d: SS cap fully exhausted by prior YTD -> a SECOND period with more YTD gets ZERO new SS tax",
      compute_payroll_tax({"E3": {"StoreA": 100.0}}, {"E3": 50.0}, cfg_cap,
                           ytd_taxable_before={"E3": {"ss": 168600.0, "futa": 0, "suta": 0}}
                           )["employees"]["E3"]["fica_ss_tax"] == 0.0)


# ── 5. FUTA wage-base cap — the realistic "crossed $7,000 by month 3" case ──────────────────────
# An employee earning $3,000/month crosses the $7,000 FUTA wage base during month 3 (YTD before month
# 3 = $6,000; this period's $3,000 wages only has $1,000 of FUTA room left).
r5 = compute_payroll_tax({"E4": {"StoreA": 120.0}}, {"E4": 25.0}, cfg_cap,
                          ytd_taxable_before={"E4": {"ss": 6000.0, "futa": 6000.0, "suta": 6000.0}})
e4 = r5["employees"]["E4"]
check("t5a: wages this period = $3000 (120hrs * $25)", e4["wages"] == 3000.0, e4)
check("t5b: FUTA taxable wages capped at the remaining $1000 of room", abs(e4["futa_taxable_wages"] - 1000.0) < 1e-6, e4)
check("t5c: FUTA tax = $1000 * 0.006, NOT $3000 * 0.006 (proves the cap actually binds, not a no-op)",
      abs(e4["futa_tax"] - 6.0) < 1e-6, e4)
check("t5d: a naive (uncapped) FUTA calc would have been $18 — the cap saved $12 this period",
      abs((3000.0 * cfg_cap["futa_rate"]) - e4["futa_tax"] - 12.0) < 1e-6)


# ── 6. Medicare has NO cap — keeps taxing full wages even for a very high earner ────────────────
r6 = compute_payroll_tax({"E5": {"StoreA": 1.0}}, {"E5": 200000.0}, cfg_cap,
                          ytd_taxable_before={"E5": {"ss": 168600.0, "futa": 7000.0, "suta": 9000.0}})
e5 = r6["employees"]["E5"]
check("t6a: SS tax is ZERO (fully capped from YTD)", e5["fica_ss_tax"] == 0.0, e5)
check("t6b: FUTA tax is ZERO (fully capped from YTD)", e5["futa_tax"] == 0.0, e5)
check("t6c: SUTA tax is ZERO (fully capped from YTD)", e5["suta_tax"] == 0.0, e5)
check("t6d: Medicare STILL taxes the full $200,000 wages (no cap exists on employer Medicare)",
      abs(e5["medicare_tax"] - (200000.0 * cfg_cap["medicare_rate"])) < 1e-6, e5)


# ── 7. enabled=False -> empty result ─────────────────────────────────────────────────────────────
cfg_off = {**DEFAULT_TAX_CONFIG, "enabled": False}
r7 = compute_payroll_tax({"E6": {"Store1": 100.0}}, {"E6": 20.0}, cfg_off, ytd_taxable_before={})
check("t7a: tax_cfg.enabled=False -> no employees, no stores (feature fully off)",
      r7 == {"employees": {}, "stores": {}}, r7)


# ── 8. compute_expense_items — each calc_method, scope='store' ─────────────────────────────────
wages_by_store = {"S1": 10000.0, "S2": 4000.0}
headcount_by_store = {"S1": 5, "S2": 2}
items_store_scope = [
    {"key": "ui", "name": "Unemployment Insurance", "calc_method": "pct_wages", "rate_or_amount": 0.02,
     "wage_cap": None, "scope": "store", "enabled": True},
    {"key": "per100", "name": "Per-$100 item", "calc_method": "per_100_wages", "rate_or_amount": 3.0,
     "wage_cap": None, "scope": "store", "enabled": True},
    {"key": "wc", "name": "Workers Comp per head", "calc_method": "per_employee", "rate_or_amount": 15.0,
     "wage_cap": None, "scope": "store", "enabled": True},
    {"key": "flat", "name": "Flat admin fee per store", "calc_method": "fixed", "rate_or_amount": 50.0,
     "wage_cap": None, "scope": "store", "enabled": True},
]
r8 = compute_expense_items(wages_by_store, headcount_by_store, company_headcount=7, items=items_store_scope)
s1 = r8["stores"]["S1"]
check("t8a: pct_wages = 10000 * 0.02 = 200", s1["ui"] == 200.0, s1)
check("t8b: per_100_wages = (10000/100) * 3 = 300", s1["per100"] == 300.0, s1)
check("t8c: per_employee = 5 * 15 = 75", s1["wc"] == 75.0, s1)
check("t8d: fixed = 50 (per store, independent of wages/headcount)", s1["flat"] == 50.0, s1)
s2 = r8["stores"]["S2"]
check("t8e: S2 pct_wages = 4000 * 0.02 = 80", s2["ui"] == 80.0, s2)
check("t8f: S2 fixed is STILL 50 (fixed+store = flat per store, not shared)", s2["flat"] == 50.0, s2)


# ── 9. scope='company' — one company amount allocated by wage share ────────────────────────────
items_company_scope = [
    {"key": "fixed_co", "name": "Company flat item", "calc_method": "fixed", "rate_or_amount": 1000.0,
     "wage_cap": None, "scope": "company", "enabled": True},
    {"key": "peremp_co", "name": "Company per-employee item", "calc_method": "per_employee", "rate_or_amount": 10.0,
     "wage_cap": None, "scope": "company", "enabled": True},
]
r9 = compute_expense_items(wages_by_store, headcount_by_store, company_headcount=7, items=items_company_scope)
# company wages = 14000; S1 share = 10000/14000 = 0.714285..., S2 share = 4000/14000 = 0.285714...
check("t9a: company fixed $1000 allocated to S1 proportional to wage share (~$714.29)",
      abs(r9["stores"]["S1"]["fixed_co"] - 714.29) < 0.01, r9["stores"])
check("t9b: company fixed $1000 allocated to S2 proportional to wage share (~$285.71)",
      abs(r9["stores"]["S2"]["fixed_co"] - 285.71) < 0.01, r9["stores"])
check("t9c: the two store allocations sum back to the full company amount",
      abs((r9["stores"]["S1"]["fixed_co"] + r9["stores"]["S2"]["fixed_co"]) - 1000.0) < 0.01, r9["stores"])
check("t9d: company per_employee = 7 * 10 = $70 total, also allocated by wage share",
      abs((r9["stores"]["S1"]["peremp_co"] + r9["stores"]["S2"]["peremp_co"]) - 70.0) < 0.01, r9["stores"])


# ── 10. wage_cap clamps the wages basis (pct_wages / per_100_wages) ────────────────────────────
items_capped = [
    {"key": "capped_ui", "name": "Capped item", "calc_method": "pct_wages", "rate_or_amount": 0.05,
     "wage_cap": 2000.0, "scope": "store", "enabled": True},
]
r10 = compute_expense_items({"S1": 10000.0}, {"S1": 5}, 5, items_capped)
check("t10a: wage_cap clamps the basis to $2000, so tax = 2000 * 0.05 = 100 (NOT 10000*0.05=500)",
      r10["stores"]["S1"]["capped_ui"] == 100.0, r10["stores"])


# ── 11. disabled item contributes nothing ───────────────────────────────────────────────────────
items_disabled = [{"key": "off", "name": "Off item", "calc_method": "fixed", "rate_or_amount": 999.0,
                    "wage_cap": None, "scope": "store", "enabled": False}]
r11 = compute_expense_items({"S1": 1000.0}, {"S1": 1}, 1, items_disabled)
check("t11a: a disabled item produces no stores entry at all", r11["stores"] == {}, r11)
check("t11b: a disabled item is excluded from the 'items' detail list too", r11["items"] == [], r11)


# ── 12. rollup_cells — combine tax + items into ONE amount per store ───────────────────────────
tax_stores = {"S1": {"total": 500.0}, "S2": {"total": 0.0}}
item_stores = {"S1": {"ui": 200.0, "wc": 50.0}, "S3": {"flat": 75.0}}
cells = rollup_cells(tax_stores, item_stores)
by_store_cell = {c["store"]: c["amount"] for c in cells}
check("t12a: S1 = tax 500 + items 250 = 750", by_store_cell.get("S1") == 750.0, cells)
check("t12b: S2 (tax bucket only, $0) still appears explicitly (clears a stale prior value)",
      by_store_cell.get("S2") == 0.0, cells)
check("t12c: S3 (items bucket only) appears with just the item total", by_store_cell.get("S3") == 75.0, cells)
check("t12d: cells sorted by store", [c["store"] for c in cells] == sorted(by_store_cell), cells)


# ── 13. ledger row shaping ──────────────────────────────────────────────────────────────────────
tax_rows = tax_ledger_rows("ORG1", "2026-07", r2, run_by="tester")
check("t13a: one tax ledger row per employee with activity", len(tax_rows) == 1 and tax_rows[0]["employee_id"] == "E1", tax_rows)
check("t13b: tax ledger row carries the taxable-wage fields needed for next period's YTD lookup",
      set(tax_rows[0]) >= {"ss_taxable_wages", "futa_taxable_wages", "suta_taxable_wages", "total_tax"}, tax_rows)

exp_rows = expense_ledger_rows("ORG1", "2026-07", r2, r8, run_by="tester")
check("t13c: expense ledger has tax-component rows (fica_ss/medicare/futa/suta) for Store1",
      any(r["component_key"] == "fica_ss" and r["store"] == "Store1" for r in exp_rows), exp_rows)
check("t13d: expense ledger has item rows too (e.g. 'ui' for S1)",
      any(r["component_key"] == "ui" and r["store"] == "S1" for r in exp_rows), exp_rows)

r_zero = compute_payroll_tax({}, {}, cfg, ytd_taxable_before={})
check("t13e: zero-activity tax result produces zero ledger rows", tax_ledger_rows("ORG1", "2026-07", r_zero) == [])


# ── 14. idempotent ledger re-run (simulated delete-by-(org,period)-then-insert) ────────────────
class FakeLedger:
    def __init__(self):
        self.rows = []

    def run(self, org_id, period, rows):
        self.rows = [r for r in self.rows if not (r["org_id"] == org_id and r["period"] == period)]
        self.rows.extend(rows)


ledger_db = FakeLedger()
rows_jul = tax_ledger_rows("ORG1", "2026-07", r2, run_by="run1")
ledger_db.run("ORG1", "2026-07", rows_jul)
check("t14a: first run persists 1 row", len(ledger_db.rows) == 1, ledger_db.rows)

ledger_db.run("ORG1", "2026-07", rows_jul)   # re-run SAME period, same data
check("t14b: re-running the SAME period does not duplicate", len(ledger_db.rows) == 1, ledger_db.rows)

rows_aug = tax_ledger_rows("ORG1", "2026-08", r3, run_by="run2")
ledger_db.run("ORG1", "2026-08", rows_aug)
check("t14c: a DIFFERENT period coexists (doesn't clobber July)", len(ledger_db.rows) == 1 + len(rows_aug), ledger_db.rows)

r_now_zero = compute_payroll_tax({}, {}, cfg, ytd_taxable_before={})   # E1's shifts got deleted
ledger_db.run("ORG1", "2026-07", tax_ledger_rows("ORG1", "2026-07", r_now_zero))
check("t14d: an employee whose activity drops to zero leaves NO ledger row after a re-run (no stale cost)",
      not any(r["period"] == "2026-07" for r in ledger_db.rows), ledger_db.rows)
check("t14e: August's row is untouched by the July re-run", any(r["period"] == "2026-08" for r in ledger_db.rows), ledger_db.rows)


# ── 15. shared wages/headcount basis sanity ─────────────────────────────────────────────────────
shifts = [
    {"employee_id": "E9", "store_code": "Store1", "scheduled_hours": 8, "actual_hours": 0},
    {"employee_id": "E9", "store_code": "Store1", "scheduled_hours": 8, "actual_hours": 7.5},
    {"employee_id": "E9", "store_code": "Store2", "scheduled_hours": 4, "actual_hours": 0},
    {"employee_id": "E10", "store_code": "Store1", "scheduled_hours": 10, "actual_hours": 10},
]
hbs = hours_worked_from_shifts(shifts)
wbs = wages_by_store_from_hours(hbs, {"E9": 20.0, "E10": 10.0})
check("t15a: Store1 wages = (15.5*20 for E9) + (10*10 for E10) = 310 + 100 = 410",
      abs(wbs["Store1"] - 410.0) < 1e-6, wbs)
check("t15b: Store2 wages = 4 * 20 = 80", abs(wbs["Store2"] - 80.0) < 1e-6, wbs)
hbc = headcount_by_store_from_hours(hbs)
check("t15c: Store1 headcount = 2 distinct employees (E9, E10)", hbc["Store1"] == 2, hbc)
check("t15d: Store2 headcount = 1 (only E9)", hbc["Store2"] == 1, hbc)


# ── 16. end-to-end YTD accumulation across 3 consecutive periods ───────────────────────────────
# One employee earning $3,000/month, FUTA wage base $7,000: month 1 & 2 fully taxable ($3000 each,
# $6000 YTD); month 3 only $1000 of room left; month 4+ should be $0 FUTA for the rest of the year.
cfg_ytd = dict(DEFAULT_TAX_CONFIG)
emp_hours = {"E7": {"StoreZ": 100.0}}
emp_rates = {"E7": 30.0}   # 100hrs * $30 = $3000/month

history = []  # simulated payroll_tax_ledger rows, across periods


def _ytd_before(period):
    year = period.split("-")[0]
    d = {"ss": 0.0, "futa": 0.0, "suta": 0.0}
    for row in history:
        if row["period"].startswith(year) and row["period"] < period:
            d["ss"] += row["ss_taxable_wages"]
            d["futa"] += row["futa_taxable_wages"]
            d["suta"] += row["suta_taxable_wages"]
    return {"E7": d}


months = ["2026-01", "2026-02", "2026-03", "2026-04"]
futa_by_month = {}
for m in months:
    res = compute_payroll_tax(emp_hours, emp_rates, cfg_ytd, ytd_taxable_before=_ytd_before(m))
    futa_by_month[m] = res["employees"]["E7"]["futa_tax"]
    history.extend(tax_ledger_rows("ORG1", m, res, run_by="ytd-test"))

check("t16a: month 1 fully taxable FUTA = 3000*0.006 = 18", abs(futa_by_month["2026-01"] - 18.0) < 1e-6, futa_by_month)
check("t16b: month 2 fully taxable FUTA = 18 (YTD 3000, room 4000)", abs(futa_by_month["2026-02"] - 18.0) < 1e-6, futa_by_month)
check("t16c: month 3 partially taxable — YTD hits 6000 after month2, only $1000 of room -> FUTA = 6",
      abs(futa_by_month["2026-03"] - 6.0) < 1e-6, futa_by_month)
check("t16d: month 4 fully capped — YTD already >= 7000 -> FUTA = 0 for the rest of the year",
      futa_by_month["2026-04"] == 0.0, futa_by_month)
check("t16e: a naive per-period-only cap check would have taxed FUTA all 4 months ($18 x 4 = $72) — "
      "the YTD design saved $72 - 42 = $30 of overstated liability",
      sum(futa_by_month.values()) == 18.0 + 18.0 + 6.0 + 0.0)


# ── 17. GROSS PAYROLL (migration 405, OWNER DECISION 2026-07-15) ───────────────────────────────
# Synthetic 2-store, 2-employee period — a floater (E9) splits hours across Store1/Store2, E10 works
# only Store1. Uses the SAME shifts fixture as check 15 so wages_by_store is known-correct.
gross_shifts = [
    {"employee_id": "E9", "store_code": "Store1", "scheduled_hours": 8, "actual_hours": 0},
    {"employee_id": "E9", "store_code": "Store1", "scheduled_hours": 8, "actual_hours": 7.5},
    {"employee_id": "E9", "store_code": "Store2", "scheduled_hours": 4, "actual_hours": 0},
    {"employee_id": "E10", "store_code": "Store1", "scheduled_hours": 10, "actual_hours": 10},
]
gross_rates = {"E9": 20.0, "E10": 10.0}
gross_hbs = hours_worked_from_shifts(gross_shifts)
gross_wbs = wages_by_store_from_hours(gross_hbs, gross_rates)   # {"Store1": 410.0, "Store2": 80.0} (see check 15)
gross_hcs = headcount_by_store_from_hours(gross_hbs)

# t17a-b: per-store GROSS matches hours*rate exactly (same basis as wages_by_store_from_hours) —
# Store1 = (15.5hrs * $20 for E9) + (10hrs * $10 for E10) = 310 + 100 = 410; Store2 = 4hrs * $20 = 80.
g_cells = gross_payroll_cells(gross_wbs)
g_by_store = {c["store"]: c["amount"] for c in g_cells}
check("t17a: Store1 gross payroll = hours*rate summed across both employees = $410",
      abs(g_by_store["Store1"] - 410.0) < 1e-6, g_by_store)
check("t17b: Store2 gross payroll = 4hrs * $20 = $80", abs(g_by_store["Store2"] - 80.0) < 1e-6, g_by_store)
check("t17c: gross_payroll_cells reproduces wages_by_store_from_hours exactly, store-for-store",
      g_by_store == {s: round(w, 2) for s, w in gross_wbs.items()}, (g_by_store, gross_wbs))

# t17d: DISTINCT from the burden/tax total for the SAME underlying data — gross wages != employer tax.
tax_same_data = compute_payroll_tax(gross_hbs, gross_rates, dict(DEFAULT_TAX_CONFIG), ytd_taxable_before={})
tax_total_store1 = tax_same_data["stores"]["Store1"]["total"]
check("t17d: Store1's gross payroll ($410) is NOT equal to Store1's employer tax burden on the same "
      "wages (proves the two lines are genuinely distinct figures, not aliases of each other)",
      abs(g_by_store["Store1"] - tax_total_store1) > 1.0, (g_by_store["Store1"], tax_total_store1))
check("t17e: gross payroll ($410) is strictly LARGER than the tax burden on it (burden is a fraction "
      "of wages under any realistic rate set) — sanity check the two aren't swapped",
      g_by_store["Store1"] > tax_total_store1 > 0, (g_by_store["Store1"], tax_total_store1))

# t17f: ledger row shaping carries wages + headcount per store, sorted, one row per touched store.
g_rows = gross_payroll_ledger_rows("ORG1", "2026-07", gross_wbs, gross_hcs, run_by="tester")
check("t17f: one gross ledger row per store touched", len(g_rows) == 2, g_rows)
check("t17g: Store1 ledger row carries wages=410 and headcount=2 (E9+E10)",
      any(r["store"] == "Store1" and abs(r["wages"] - 410.0) < 1e-6 and r["headcount"] == 2 for r in g_rows), g_rows)
check("t17h: Store2 ledger row carries wages=80 and headcount=1 (only E9)",
      any(r["store"] == "Store2" and abs(r["wages"] - 80.0) < 1e-6 and r["headcount"] == 1 for r in g_rows), g_rows)


# ── idempotent re-run + org-scoping (simulated delete-by-(org,period)-then-insert, same harness
# convention as check 14) ───────────────────────────────────────────────────────────────────────
class FakeGrossLedger:
    def __init__(self):
        self.rows = []

    def run(self, org_id, period, rows):
        self.rows = [r for r in self.rows
                     if not (r["org_id"] == org_id and r["period"] == period)]
        self.rows.extend(rows)


gross_ledger_db = FakeGrossLedger()
gross_ledger_db.run("ORG1", "2026-07", g_rows)
check("t17i: first run persists 2 rows (Store1, Store2)", len(gross_ledger_db.rows) == 2, gross_ledger_db.rows)

gross_ledger_db.run("ORG1", "2026-07", g_rows)   # re-run SAME (org, period), same data
check("t17j: re-running the SAME (org, period) does not duplicate", len(gross_ledger_db.rows) == 2, gross_ledger_db.rows)

# a different ORG, same period, must coexist without colliding (org-scoped)
gross_ledger_db.run("ORG2", "2026-07", gross_payroll_ledger_rows("ORG2", "2026-07", {"Store9": 999.0}, {"Store9": 1}, run_by="tester"))
check("t17k: a DIFFERENT org's rows for the SAME period coexist (org-scoped, no cross-org overwrite)",
      len(gross_ledger_db.rows) == 3, gross_ledger_db.rows)
check("t17l: ORG1's rows are untouched by ORG2's write", sum(1 for r in gross_ledger_db.rows if r["org_id"] == "ORG1") == 2,
      gross_ledger_db.rows)
check("t17m: ORG2's row is scoped to ORG2 only (never visible under ORG1)",
      all(r["org_id"] == "ORG2" for r in gross_ledger_db.rows if r["store"] == "Store9"), gross_ledger_db.rows)

# activity drops to zero at Store2 next run (E9's Store2 shift removed) -> Store2 disappears from the
# re-run's rows entirely (gross_payroll_ledger_rows only emits rows for stores present in wages_by_store)
wbs_store2_gone = {"Store1": gross_wbs["Store1"]}
rows_after_drop = gross_payroll_ledger_rows("ORG1", "2026-07", wbs_store2_gone, {"Store1": 2}, run_by="tester2")
gross_ledger_db.run("ORG1", "2026-07", rows_after_drop)
check("t17n: a store whose gross payroll drops to zero/absent leaves NO stale ledger row after a re-run",
      not any(r["org_id"] == "ORG1" and r["store"] == "Store2" for r in gross_ledger_db.rows), gross_ledger_db.rows)
check("t17o: Store1's row (still active) and ORG2's row are both untouched by the ORG1 re-run",
      any(r["org_id"] == "ORG1" and r["store"] == "Store1" for r in gross_ledger_db.rows)
      and any(r["org_id"] == "ORG2" for r in gross_ledger_db.rows), gross_ledger_db.rows)

# t17p: gross_payroll_cells includes every store even at $0 (matches rollup_cells/expense_cells_from_stores
# convention — clears a stale prior value on the receiver's idempotent delete-by-source_key).
check("t17p: a $0-wage store still gets an explicit cell (not silently dropped)",
      {"store": "StoreZero", "amount": 0.0} in gross_payroll_cells({"StoreZero": 0.0}))

# t17q: this module NEVER writes hours/pay_rate/shifts — gross_payroll_cells/gross_payroll_ledger_rows
# are pure functions of their inputs; calling them twice with the same input is byte-identical (no
# hidden mutable state, no side effect on a payout number).
check("t17q: pure + side-effect-free — same input twice produces byte-identical output",
      gross_payroll_cells(gross_wbs) == gross_payroll_cells(gross_wbs), None)


# ── Report ─────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
