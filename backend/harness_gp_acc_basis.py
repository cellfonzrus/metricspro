"""Proof harness — GP report accessory-column basis switch (owner directive 2026-09-02: "Acc Gp
should show the price at which the accessories were sold not the Gross profit … renamed to Acc
Sales").

DB-free, pure-stdlib, through the REAL gp_report.calc_gp_report. Proves:

  1. IDENTITY — acc_basis='gp' (the function default every existing pure caller gets) is
     byte-identical to before: acc_gp = Σ gp of accessory lines;
  2. acc_basis='sales' → acc_gp = Σ ext_price of accessory lines (the same sell-price basis
     phone_sales has always used), flowing consistently into total_rev and net_profit;
  3. every OTHER money column is unchanged by the switch (the delta is exactly
     Σ(ext_price − gp) over accessory lines — store rows, rep rows and totals);
  4. the payload labels itself from config: acc_basis + acc_label ('Acc Sales' / 'Acc GP'),
     so no display surface hardcodes the column name;
  5. junk basis values fall back to 'gp' (a typo can never invent a third basis).

Run: python3 backend/harness_gp_acc_basis.py   (exit 0 = all proofs hold)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc.gp_report import calc_gp_report  # noqa: E402

FAIL = 0


def check(name, cond):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1


PERIOD = "August 2026"
STORE_MAP = [{"store_address": "100 Main St", "store_code": "B-100", "market": "NYC",
              "salesforce_id": "SF1"}]
# Two accessory lines (Ondigo = built-in accessory dept), one device, one plan — per store & rep.
SALES = [
    {"store": "100 Main St", "department": "Ondigo", "product_desc": "Case",
     "ext_price": 25.00, "gp": 10.00, "salesperson": "Ana"},
    {"store": "100 Main St", "department": "Ondigo", "product_desc": "Charger",
     "ext_price": 40.00, "gp": -3.50, "salesperson": "Ana"},     # garbage cost → negative GP (the owner's complaint)
    {"store": "100 Main St", "department": "Android - XP", "product_desc": "Phone",
     "ext_price": 199.99, "gp": 0.0, "salesperson": "Ana"},
    {"store": "100 Main St", "department": "", "product_desc": "Plan",
     "ext_price": 50.00, "gp": 50.00, "salesperson": "Ana"},
]
ACC_EXT = 25.00 + 40.00        # 65.00
ACC_GP = 10.00 + (-3.50)       # 6.50
DELTA = round(ACC_EXT - ACC_GP, 2)


def run(**kw):
    return calc_gp_report([dict(r) for r in SALES], [], [], [], [], [], STORE_MAP, PERIOD, **kw)


base = run()                      # function default — the legacy identity
sales = run(acc_basis="sales")

print("1. legacy identity (function default = 'gp')")
r0 = base["store_rows"][0]
check("acc_gp = Σ gp of accessory lines (6.50)", r0["acc_gp"] == ACC_GP)
check("payload says so: acc_basis='gp', acc_label='Acc GP'",
      base["acc_basis"] == "gp" and base["acc_label"] == "Acc GP")

print("2. 'sales' basis = sell price, consistent through the report")
r1 = sales["store_rows"][0]
check("acc_gp = Σ ext_price of accessory lines (65.00)", r1["acc_gp"] == ACC_EXT)
check("total_rev shifts by exactly Σ(ext_price − gp) of accessory lines",
      round(r1["total_rev"] - r0["total_rev"], 2) == DELTA)
check("net_profit shifts by exactly the same delta",
      round(r1["net_profit"] - r0["net_profit"], 2) == DELTA)
check("totals row agrees", round(sales["totals"]["acc_gp"] - base["totals"]["acc_gp"], 2) == DELTA)

print("3. every other money column unchanged by the switch")
others = [k for k in r0 if k not in
          ("acc_gp", "total_rev", "net_profit", "net_excl_mdf") and isinstance(r0[k], (int, float))]
check("store row: %d other numeric columns identical" % len(others),
      all(r0[k] == r1[k] for k in others))
rep0, rep1 = base["rep_rows"][0], sales["rep_rows"][0]
check("rep row: acc column follows the basis, others identical",
      rep0["acc_gp"] == ACC_GP and rep1["acc_gp"] == ACC_EXT
      and all(rep0[k] == rep1[k] for k in rep0 if k != "acc_gp"))

print("4. config-driven label")
check("'sales' labels the column 'Acc Sales'",
      sales["acc_basis"] == "sales" and sales["acc_label"] == "Acc Sales")

print("5. junk basis fails safe to 'gp'")
junk = run(acc_basis="banana")
check("unknown basis behaves as 'gp'",
      junk["acc_basis"] == "gp" and junk["store_rows"][0]["acc_gp"] == ACC_GP)

print()
if FAIL:
    print(f"{FAIL} proof(s) FAILED")
    sys.exit(1)
print("ALL PROOFS HOLD — GP accessory-column basis")
