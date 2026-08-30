"""HARNESS — commission computed DIRECTLY from the Executive MTD per-employee numbers (owner 2026-08-30).

Proves the pure math of the /commission-mtd endpoint: for each rep, commission =
    Total Activation × the plan's flat activation rate  +  Acc. Sales × the plan's accessory %.
So the payout basis is byte-identical to what the owner already sees on Executive MTD — the accessory
number visible on that report is the SAME one that pays, closing the "shows in Exec MTD but not in the
payout" gap (the two accessory classifiers had drifted).

  python3 backend/harness_commission_from_mtd.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc.router import (  # noqa: E402
    _plan_mtd_rates, _commission_from_mtd_rows, _default_mtd_rate_map, _parse_mtd_rate_map,
    _override_plan_by_rep_with_mtd)

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── the NY plan shape: $10/activation (activation_bucket) + 10% accessories ─────────────────────────
NY_PLAN = {"rules": [
    {"match_field": "activation_bucket", "match_op": "in", "match_value": "premium,byod",
     "payout_kind": "flat_per_unit", "amount": 10, "pct": 0, "qualifies": True},
    {"match_field": "accessory", "match_op": "equals", "match_value": "yes",
     "payout_kind": "pct_price", "amount": 0, "pct": 0.10, "qualifies": True},
]}

print("── A. rate extraction from the plan rules ───────────────────────────────────────")
act, acc = _plan_mtd_rates(NY_PLAN)
check("activation flat rate = $10", act == 10.0, act)
check("accessory pct = 0.10", acc == 0.10, acc)

# a department-keyed activation rule (the service-plan variant) is also picked up
act2, _ = _plan_mtd_rates({"rules": [
    {"match_field": "department", "payout_kind": "flat_per_unit", "amount": 12, "qualifies": True}]})
check("department flat_per_unit is read as the activation rate ($12)", act2 == 12.0, act2)

# a disabled rule (qualifies=False) is ignored
act3, acc3 = _plan_mtd_rates({"rules": [
    {"match_field": "activation_bucket", "payout_kind": "flat_per_unit", "amount": 99, "qualifies": False}]})
check("a non-qualifying rule contributes no rate", act3 == 0.0 and acc3 == 0.0, (act3, acc3))

print("── B. per-rep commission — DEFAULT rate map ($10 all activation cats, upgrade $0) ─")
# The default map applies the plan's flat rate to every activation category EXCEPT upgrade. Exec MTD rows
# break the count out per category; here they sum to Total Activation (excl. upgrade).
DEF = _default_mtd_rate_map(act)   # {activation:10, port:10, byod:10, tablet:10, home_internet:10, edge:10, upgrade:0}
check("default map: byod = $10", DEF["byod"] == 10.0, DEF)
check("default map: upgrade = $0 (separate, not paid by default)", DEF["upgrade"] == 0.0, DEF)
emp_rows = [
    # 10 new + 5 port + 6 byod + 2 tablet + 1 home_internet + 1 edge = 25 payable; 3 upgrades NOT paid.
    {"employee": "Fozilova, Shakhnoza", "total_activation": 25, "activation": 10, "port": 5, "byod": 6,
     "tablet": 2, "home_internet": 1, "edge": 1, "upgrade": 3, "acc_sales": 400.0},   # 25*10 + 40 = 290
    {"employee": "Navarro, Alondra", "total_activation": 3, "activation": 3, "port": 0, "byod": 0,
     "tablet": 0, "home_internet": 0, "edge": 0, "upgrade": 0, "acc_sales": 0.0},      # 30 + 0 = 30
    {"employee": "Jacobo, Liset", "total_activation": 0, "activation": 0, "port": 0, "byod": 0,
     "tablet": 0, "home_internet": 0, "edge": 0, "upgrade": 0, "acc_sales": 150.55},   # 0 + 15.06
]
rows = _commission_from_mtd_rows(emp_rows, DEF, acc)
by = {r["employee"]: r for r in rows}
check("rep A: 25 payable × $10 + $400 × 10% = $290 (3 upgrades excluded)",
      by["Fozilova, Shakhnoza"]["commission"] == 290.0, by["Fozilova, Shakhnoza"])
check("rep A activation_pay = $250", by["Fozilova, Shakhnoza"]["activation_pay"] == 250.0, by["Fozilova, Shakhnoza"])
check("rep A byod category paid 6 × $10 = $60", by["Fozilova, Shakhnoza"]["by_category"]["byod"]["pay"] == 60.0,
      by["Fozilova, Shakhnoza"]["by_category"]["byod"])
check("rep A upgrade category paid $0 (rate 0)", by["Fozilova, Shakhnoza"]["by_category"]["upgrade"]["pay"] == 0.0,
      by["Fozilova, Shakhnoza"]["by_category"]["upgrade"])
check("rep B: 3 new × $10 = $30", by["Navarro, Alondra"]["commission"] == 30.0, by["Navarro, Alondra"])
check("rep C: 0 act + $150.55 × 10% = $15.06 (rounded)", by["Jacobo, Liset"]["commission"] == 15.06,
      by["Jacobo, Liset"])
check("rows sorted by commission desc", [r["employee"] for r in rows][0] == "Fozilova, Shakhnoza",
      [r["employee"] for r in rows])

print("── C. PER-CATEGORY override — pay Upgrade $5, BYOD $12, everything else $10 ──────")
custom = _parse_mtd_rate_map("upgrade:5,byod:12", DEF)
check("override kept new=$10", custom["activation"] == 10.0, custom)
check("override set byod=$12", custom["byod"] == 12.0, custom)
check("override set upgrade=$5 (now a paid option)", custom["upgrade"] == 5.0, custom)
rows2 = _commission_from_mtd_rows(emp_rows, custom, acc)
a2 = {r["employee"]: r for r in rows2}["Fozilova, Shakhnoza"]
# new10*10 + port5*10 + byod6*12 + tablet2*10 + hi1*10 + edge1*10 + upg3*5 = 100+50+72+20+10+10+15 = 277 ; +40 acc = 317
check("rep A with overrides: $277 activation + $40 acc = $317", a2["commission"] == 317.0, a2)
check("rep A upgrade now pays 3 × $5 = $15", a2["by_category"]["upgrade"]["pay"] == 15.0, a2["by_category"]["upgrade"])

print("── D. accessory number on Exec MTD == the number that pays (the whole point) ─────")
only_acc = _commission_from_mtd_rows(
    [{"employee": "X", "total_activation": 0, "acc_sales": 1000.0}], _default_mtd_rate_map(10.0), 0.10)
check("an Exec-MTD accessory number always pays (1000 × 10% = $100)", only_acc[0]["commission"] == 100.0,
      only_acc[0])

print("── E. LIVE-PAY override: exec_mtd plan reps paid from Exec MTD, rules plans untouched ──")
# plan_by_rep as the calc builds it from the rules preview (REP UPPER -> {amount, plan_name, setup_fee_comm}).
pbr = {
    "KELLIE, MARK": {"amount": 21.40, "plan_name": "NY / Luxelink Comp", "setup_fee_comm": 0.0},   # rules basis, to be replaced
    "STALE, REP": {"amount": 15.00, "plan_name": "NY / Luxelink Comp", "setup_fee_comm": 0.0},      # exec_mtd plan, but NOT in exec MTD -> dropped
    "CHICAGO, DM": {"amount": 99.00, "plan_name": "Total Comp DM", "setup_fee_comm": 0.0},          # a RULES plan -> untouched
}
mtd_by_plan = [("NY / Luxelink Comp", [
    {"employee": "Kellie, Mark", "commission": 1000.93},
    {"employee": "Fatima, Syeda Zainab", "commission": 385.08},   # new rep, added
])]
_override_plan_by_rep_with_mtd(pbr, {"NY / Luxelink Comp"}, mtd_by_plan)
check("exec_mtd rep's amount is REPLACED with the Exec-MTD commission (not the rules $21.40)",
      pbr.get("KELLIE, MARK", {}).get("amount") == 1000.93, pbr.get("KELLIE, MARK"))
check("a new exec_mtd rep is ADDED", pbr.get("FATIMA, SYEDA ZAINAB", {}).get("amount") == 385.08,
      pbr.get("FATIMA, SYEDA ZAINAB"))
check("an exec_mtd-plan rep with NO Exec MTD row is DROPPED (not paid by rules)",
      "STALE, REP" not in pbr, list(pbr.keys()))
check("a RULES plan's rep is UNTOUCHED (byte-identical)", pbr.get("CHICAGO, DM", {}).get("amount") == 99.00,
      pbr.get("CHICAGO, DM"))
check("no double-count: exactly one entry per paid rep",
      len(pbr) == 3 and set(pbr) == {"KELLIE, MARK", "FATIMA, SYEDA ZAINAB", "CHICAGO, DM"}, list(pbr.keys()))
# with NO exec_mtd plans, the map is byte-identical
pbr2 = {"A": {"amount": 5.0, "plan_name": "X"}}
_override_plan_by_rep_with_mtd(pbr2, set(), [])
check("no exec_mtd plans -> plan_by_rep unchanged", pbr2 == {"A": {"amount": 5.0, "plan_name": "X"}}, pbr2)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
