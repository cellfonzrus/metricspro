"""HARNESS — Chicago 3-tier manager incentive computes end-to-end from CONFIG + the sales roll-up.

The rep tier is the existing rep engine. This proves the DM / market-manager tier: the store_manager map
(mig 305) resolves a manager's stores, the Executive-MTD by-store roll-up becomes the component actuals
(the accessory OVERRIDE on the reps under them), and compute_payout applies goals + qualifier gates
(cash deposit on time, scheduled hours under target). All PURE — no DB, no network.

  python3 backend/harness_management_incentive_chicago.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import management_incentive as mi  # noqa: E402

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


# ── the store→manager map (mig 305 rows) ─────────────────────────────────────────────────────────────
SM_ROWS = [
    {"store_code": "CHI-01", "role": "district_manager", "manager_name": "Rivera, Sam", "is_active": True},
    {"store_code": "CHI-02", "role": "district_manager", "manager_name": "Sam Rivera", "is_active": True},
    {"store_code": "CHI-03", "role": "district_manager", "manager_name": "Rivera, Sam", "is_active": True},
    {"store_code": "CHI-09", "role": "district_manager", "manager_name": "Rivera, Sam", "is_active": False},  # inactive
    {"store_code": "CHI-04", "role": "district_manager", "manager_name": "Other DM", "is_active": True},      # other mgr
    {"store_code": "CHI-01", "role": "market_manager", "manager_name": "Rivera, Sam", "is_active": True},     # other role
]

print("── A. stores_for_manager — name-order-insensitive, role-scoped, skips inactive ──")
stores = mi.stores_for_manager(SM_ROWS, manager_name="Sam Rivera", role="district_manager")
check("Sam's DM stores = CHI-01/02/03 (both name spellings, no inactive, no other mgr/role)",
      stores == ["CHI-01", "CHI-02", "CHI-03"], stores)
check("a different role resolves its own set",
      mi.stores_for_manager(SM_ROWS, manager_name="Rivera, Sam", role="market_manager") == ["CHI-01"])
check("unknown manager -> empty", mi.stores_for_manager(SM_ROWS, manager_name="Nobody", role="district_manager") == [])

print("── B. rollup_store_sales — sums the manager's stores, skips TOTAL + out-of-set ──")
BY_STORE = [
    {"store": "CHI-01", "acc_sales": 5000.0, "activation": 20, "port": 5, "byod": 3, "tablet": 2,
     "home_internet": 4, "edge": 6, "upgrade": 1, "total_activation": 30},
    {"store": "CHI-02", "acc_sales": 3000.0, "activation": 10, "port": 2, "byod": 1, "tablet": 0,
     "home_internet": 2, "edge": 3, "upgrade": 0, "total_activation": 15},
    {"store": "CHI-03", "acc_sales": 2000.0, "activation": 8, "port": 1, "byod": 0, "tablet": 1,
     "home_internet": 0, "edge": 1, "upgrade": 2, "total_activation": 10},
    {"store": "CHI-04", "acc_sales": 9999.0, "activation": 99, "home_internet": 9, "edge": 9,
     "total_activation": 99},   # NOT Sam's store -> excluded
    {"store": "TOTAL", "acc_sales": 99999.0, "total_activation": 999},  # the total row -> skipped
]
roll = mi.rollup_store_sales(BY_STORE, stores)
check("accessory$ rolled up across Sam's 3 stores = 5000+3000+2000 = 10000", roll["acc_sales"] == 10000.0, roll)
check("home_internet (VHI/FIOS) = 4+2+0 = 6", roll["home_internet"] == 6.0, roll)
check("edge = 6+3+1 = 10", roll["edge"] == 10.0, roll)
check("CHI-04 (not Sam's) excluded — acc_sales did not include 9999", roll["acc_sales"] == 10000.0, roll)
check("TOTAL row skipped", roll["total_activation"] == 55.0, roll)   # 30+15+10, not 999

print("── C. actuals_from_rollup — component metric_source ← roll-up field via alias ──")
COMPONENTS = [
    {"label": "Accessory override", "kind": "percent", "rate": 0.02, "metric_source": "accessory_gp",
     "target_per_store": 8000, "store_count": None, "cap_at_target": True},
    {"label": "VHI/FIOS", "kind": "per_unit", "rate": 2, "metric_source": "vhi_fios_count",
     "target_per_store": 10, "store_count": None, "cap_at_target": True},
    {"label": "Edge", "kind": "per_unit", "rate": 5, "metric_source": "edge_count",
     "target_per_store": 10, "store_count": None, "cap_at_target": True},
    {"label": "Mystery", "kind": "percent", "rate": 1, "metric_source": "not_a_sales_metric",
     "target_per_store": 0},
]
actuals = mi.actuals_from_rollup(COMPONENTS, roll)
check("accessory_gp actual = $10,000 (the reps' accessory sales under Sam)", actuals.get("accessory_gp") == 10000.0, actuals)
check("vhi_fios_count actual = 6", actuals.get("vhi_fios_count") == 6.0, actuals)
check("edge_count actual = 10", actuals.get("edge_count") == 10.0, actuals)
check("unrecognized metric_source left for the body to supply", "not_a_sales_metric" not in actuals, actuals)

print("── D. full compute — goals + accessory override + cash-deposit & schedule-hours gates ──")
PLAN = {
    "id": "chi-dm", "name": "Chicago — District Manager", "consolidated_bonus_amount": 300,
    "components": COMPONENTS[:3],
    "bonuses": [{"label": "Consolidated", "kind": "consolidated", "gated_by": "qualifiers"}],
    "qualifiers": [
        {"metric_key": "cash_deposit", "source": "cash_deposit", "op": "lte", "threshold": 0,
         "applies_to": "consolidated"},                       # deposited on time => variance <= 0
        {"metric_key": "schedule_hours", "source": "schedule_hours", "op": "lte", "threshold": 0,
         "applies_to": "consolidated"},                       # hours at/under target => over-target <= 0
    ],
}
# manager owns 3 stores; component store_count None -> uses manager_store_count for the target math
res_pass = mi.compute_payout(PLAN, actuals=actuals, manager_store_count=3,
                             qualifier_values={"cash_deposit": 0, "schedule_hours": 0})
# accessory: 2% × 10000 = 200 ; opportunity = 2% × (8000×3) = 480 -> not capped -> 200
acc = next(c for c in res_pass["components"] if c["metric_source"] == "accessory_gp")
check("accessory override pays 2% × $10,000 = $200", acc["payout"] == 200.0, acc)
# VHI: $2 × 6 = 12 ; Edge: $5 × 10 = 50
check("VHI/FIOS pays $2 × 6 = $12", next(c for c in res_pass["components"] if c["metric_source"] == "vhi_fios_count")["payout"] == 12.0)
check("Edge pays $5 × 10 = $50", next(c for c in res_pass["components"] if c["metric_source"] == "edge_count")["payout"] == 50.0)
check("component_total = 200 + 12 + 50 = 262", res_pass["component_total"] == 262.0, res_pass["component_total"])
check("both gates pass -> consolidated $300 earned", res_pass["consolidated_qualified"] is True, res_pass)
check("total = 262 + 300 = 562", res_pass["total"] == 562.0, res_pass["total"])

# gate FAILS: scheduled hours OVER target (variance > 0) -> consolidated bonus withheld
res_fail = mi.compute_payout(PLAN, actuals=actuals, manager_store_count=3,
                             qualifier_values={"cash_deposit": 0, "schedule_hours": 12})
check("schedule-hours over target -> consolidated gate fails", res_fail["consolidated_qualified"] is False, res_fail)
check("bonus withheld -> total = component_total only ($262)", res_fail["total"] == 262.0, res_fail["total"])

# a missing gate value FAILS CLOSED (engine invariant) — a gate you can't measure is not passed
res_missing = mi.compute_payout(PLAN, actuals=actuals, manager_store_count=3,
                                qualifier_values={"cash_deposit": 0})  # schedule_hours absent
check("missing schedule_hours value fails the gate closed (no unearned $300)",
      res_missing["consolidated_qualified"] is False and res_missing["total"] == 262.0, res_missing["total"])

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
