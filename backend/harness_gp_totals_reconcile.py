"""Offline proof (no DB/network) that the GROSS PROFIT header RECONCILES.

OWNER BUG 2026-09-12, verbatim: *"check the gross profit report for cellfonz rus the numbers are
totally off"*, with the August 2026 header pasted:

    Total Revenue 820,584.78 · Rep Incentives 10,723.40 · Store Expenses 178,577.58
    Net Profit 541,274.47

Subtract what is shown and you get 631,283.80, not 541,274.47. The report was not computing anything
wrong — `net_profit` is `total_rev - rep_pay - exp_total - net_phone_cost`, and it was right. But
`net_phone_cost` (90,009.33 that month) was summed per STORE and never put in `totals`, so the one
subtraction big enough to move the headline appeared nowhere in the headline. A reader who checks the
arithmetic concludes the report is broken, which is worse than a report that admits it cannot measure
something: incompleteness and wrongness read identically, and cost the same trust.

§A pins the ARITHMETIC IDENTITY. §B is the durable half and the reason this file exists: it fails if
ANY numeric column on a store row is missing from `totals`, so the next column added cannot go
un-totalled the same way. `plan_gp` and `other_gp` were missing for exactly this reason too — nobody
noticed, because nothing forced the two shapes to agree.

Run: `cd backend && python3 harness_gp_totals_reconcile.py`
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(ROOT, "app/modules/commcalc/gp_report.py"), encoding="utf-8").read()

# ══ A. THE IDENTITY, ON THE OWNER'S OWN NUMBERS ══════════════════════════════════════════════════
# Live August 2026, read from the real engine when this was written. Held as a fixture so the rule is
# checked without a database: these six numbers are the ones the owner pasted.
AUG = {"total_rev": 820584.78, "rep_pay": 10723.40, "exp_total": 178577.58,
       "net_phone_cost": 90009.33, "net_profit": 541274.47}
lhs = AUG["total_rev"] - AUG["rep_pay"] - AUG["exp_total"] - AUG["net_phone_cost"]
check("A1 net profit IS revenue less phone cost, rep pay and store expenses",
      abs(lhs - AUG["net_profit"]) < 0.005, f"{lhs} vs {AUG['net_profit']}")
check("A2 ... and the gap the owner saw is EXACTLY the phone cost that had no tile",
      abs((AUG["total_rev"] - AUG["rep_pay"] - AUG["exp_total"]) - AUG["net_profit"]
          - AUG["net_phone_cost"]) < 0.005)

# The formula in the engine must stay the one §A1 pins.
m = re.search(r"net_profit\s*=\s*total_rev\s*-\s*rep_pay\s*-\s*exp_total\s*-\s*net_phone_cost", SRC)
check("A3 the engine still computes net profit from those four terms", bool(m))

# ══ B. EVERY STORE-ROW MONEY COLUMN IS TOTALLED ══════════════════════════════════════════════════
# THE POINT OF THIS FILE. Parse both shapes out of the source and compare them.
row_block = SRC.split("store_rows.append(", 1)[1].split("})", 1)[0] if "store_rows.append(" in SRC else ""
row_keys = set(re.findall(r"'([a-z_0-9]+)':", row_block))

tot_block = SRC.split("totals = {", 1)[1].split("\n    }", 1)[0]
tot_keys = set(re.findall(r"'([a-z_0-9]+)':", tot_block))
# Leg keys (comm_m1, mi_unsplit, …) are added in the loop below the literal, not in it.
tot_keys |= {"comm_m1", "comm_m2_12", "comm_unsplit", "comp_comm_m1", "comp_comm_m2_12",
             "comp_comm_unsplit", "mi_m1", "mi_m2_12", "mi_unsplit",
             "atu_m1", "atu_m2_12", "atu_unsplit"}

# Identity/label columns are not money and are not expected in a totals row.
NOT_MONEY = {"store", "store_code", "address", "market", "unmapped", "rep", "name",
             "net_profit_target", "net_profit_attainment"}
missing = sorted(k for k in row_keys - tot_keys - NOT_MONEY)
check("B1 EVERY money column on a store row is summed into totals — the rule that stops a "
      "subtraction disappearing from the header again", not missing, f"missing from totals: {missing}")

for k in ("net_phone_cost", "plan_gp", "other_gp"):
    check(f"B2 `{k}` is in totals (it was not, and that was the bug)", k in tot_keys)

check("B3 the three terms that SUBTRACT are all totalled, so the header can be checked by eye",
      {"rep_pay", "exp_total", "net_phone_cost"} <= tot_keys)

# ══ C. THE HEADER SHOWS WHAT IT SUBTRACTS ════════════════════════════════════════════════════════
PAGE = open(os.path.join(ROOT, "../frontend/src/app/(platform)/commcalc/gp/page.tsx"),
            encoding="utf-8").read()
from harnesslib import js_code_only   # noqa: E402
page = js_code_only(PAGE)
tiles = page.split("Summary cards", 1)[-1].split(".map(({ label", 1)[0]
for key, label in (("total_rev", "Total Revenue"), ("net_phone_cost", "Phone Cost"),
                   ("rep_pay", "Rep Incentives"), ("exp_total", "Store Expenses"),
                   ("net_profit", "Net Profit")):
    check(f"C1 the header carries a `{key}` tile ({label})", f"totals.{key}" in tiles, tiles[:200])

check("C2 the subtracting tiles are marked red, so the row reads as an arithmetic",
      tiles.count("red: true") >= 3)
check("C3 the tile grid is not pinned to a fixed column count that a new tile would overflow",
      "repeat(6, 1fr)" not in tiles)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
