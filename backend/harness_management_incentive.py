"""Offline proof (no DB/network) for the Management Incentive computation (migration 852).

Builds the owner's Total Wireless DEFAULT plan as a dict and proves:
  1. FULL ATTAINMENT — hitting every target + passing every qualifier pays exactly $2,090
     (Accessory $1,120 + VHI/FIOS $120 + Edge $300 + Consolidated $300 + Inventory $250).
  2. COMPONENT MATH — rate × actual, capped at the target opportunity.
  3. QUALIFIER GATE — the $300 consolidated bonus is all-or-nothing: one failed metric (Zulu, 3MR,
     TWP, Address Checks, Cash Deposit) drops it to $0 while the component payouts still pay.
  4. FAIL-CLOSED — a missing qualifier value fails that gate.
  5. INVENTORY BONUS — the $250 is gated on the inventory-aging derived flag, independently.
  6. OVER-TARGET CAP — production beyond target does not overpay a capped component.
  7. RESOLUTION — an employee-scope assignment beats a role-scope one (same basis as commissions).

Run: `python3 harness_management_incentive.py` from backend/.
"""
import sys
sys.path.insert(0, ".")

from app.modules.commcalc import management_incentive as M  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  ({detail})"))


# ── The owner's Total Wireless DEFAULT plan (the shape mig 852 stores) ────────────────────────────
PLAN = {
    "id": "tw-default", "name": "Total Wireless — District Manager", "is_active": True,
    "consolidated_bonus_amount": 300,
    "components": [
        {"label": "Accessory Sales", "kind": "percent", "rate": 0.02, "metric_source": "accessory_gp",
         "target_per_store": 8000, "store_count": 7, "cap_at_target": True, "sort": 1},
        {"label": "VHI / FIOS Sales", "kind": "per_unit", "rate": 2, "metric_source": "vhi_fios_count",
         "target_per_store": 10, "store_count": 6, "cap_at_target": True, "sort": 2},
        {"label": "Edge Activations", "kind": "per_unit", "rate": 5, "metric_source": "edge_count",
         "target_per_store": 10, "store_count": 6, "cap_at_target": True, "sort": 3},
    ],
    "bonuses": [
        {"label": "Consolidated Bonus", "kind": "consolidated", "gated_by": "qualifiers", "sort": 1},
        {"label": "Inventory Control Bonus", "kind": "inventory_selloff", "amount": 250,
         "gated_by": "inventory_aging", "config": {"max_days": 10}, "sort": 2},
    ],
    "qualifiers": [
        {"metric_key": "zulu", "label": "Zulu", "source": "kpi", "op": "lt", "threshold": 5, "unit": "percent"},
        {"metric_key": "tmr3", "label": "3MR", "source": "kpi", "op": "gt", "threshold": 75, "unit": "percent"},
        {"metric_key": "cash_deposit", "label": "Cash Deposit", "source": "cash_deposit", "op": "lte", "threshold": 0, "unit": "usd"},
        {"metric_key": "twp", "label": "TWP", "source": "kpi", "op": "gt", "threshold": 80, "unit": "percent"},
        {"metric_key": "address_checks", "label": "Address Checks", "source": "kpi", "op": "gt", "threshold": 50, "unit": "percent"},
    ],
}

# Actuals exactly at target: accessory $56,000 (8000×7); VHI 60 (10×6); Edge 60 (10×6).
AT_TARGET = {"accessory_gp": 56000, "vhi_fios_count": 60, "edge_count": 60}
# Qualifiers all passing: Zulu 4.2<5, 3MR 78>75, cash 0<=0, TWP 82>80, AddrChk 55>50.
PASS_METRICS = {"zulu": 4.2, "tmr3": 78, "cash_deposit": 0, "twp": 82, "address_checks": 55}

print("\n(1) full attainment pays exactly $2,090")
r = M.compute_payout(PLAN, actuals=AT_TARGET, qualifier_values=PASS_METRICS,
                     manager_store_count=7, derived={"inventory_aging": True})
comp = {c["label"]: c["payout"] for c in r["components"]}
bon = {b["label"]: b["payout"] for b in r["bonuses"]}
check("Accessory Sales pays $1,120", comp["Accessory Sales"] == 1120.0, comp)
check("VHI / FIOS pays $120", comp["VHI / FIOS Sales"] == 120.0, comp)
check("Edge Activations pays $300", comp["Edge Activations"] == 300.0, comp)
check("Consolidated bonus pays $300", bon["Consolidated Bonus"] == 300.0, bon)
check("Inventory bonus pays $250", bon["Inventory Control Bonus"] == 250.0, bon)
check("component_total == $1,540", r["component_total"] == 1540.0, r["component_total"])
check("bonus_total == $550", r["bonus_total"] == 550.0, r["bonus_total"])
check("GRAND TOTAL == $2,090", r["total"] == 2090.0, r["total"])
check("consolidated_qualified is True", r["consolidated_qualified"] is True)

print("\n(3) one failed qualifier drops the $300 but components still pay")
fail_metrics = dict(PASS_METRICS, zulu=6.1)   # Zulu 6.1 is NOT under 5
r2 = M.compute_payout(PLAN, actuals=AT_TARGET, qualifier_values=fail_metrics,
                      manager_store_count=7, derived={"inventory_aging": True})
b2 = {b["label"]: b["payout"] for b in r2["bonuses"]}
check("consolidated gate fails", r2["consolidated_qualified"] is False)
check("Consolidated bonus now $0", b2["Consolidated Bonus"] == 0.0, b2)
check("Inventory bonus still $250 (independent gate)", b2["Inventory Control Bonus"] == 250.0, b2)
check("components still pay in full ($1,540)", r2["component_total"] == 1540.0, r2["component_total"])
check("total drops to $1,790", r2["total"] == 1790.0, r2["total"])

print("\n(4) a missing qualifier value fails closed")
miss = dict(PASS_METRICS); miss.pop("address_checks")
r3 = M.compute_payout(PLAN, actuals=AT_TARGET, qualifier_values=miss,
                      manager_store_count=7, derived={"inventory_aging": True})
check("missing Address Checks → consolidated fails", r3["consolidated_qualified"] is False)
check("Consolidated bonus $0 on missing metric",
      [b for b in r3["bonuses"] if b["label"] == "Consolidated Bonus"][0]["payout"] == 0.0)

print("\n(5) inventory bonus is gated independently on the aging flag")
r4 = M.compute_payout(PLAN, actuals=AT_TARGET, qualifier_values=PASS_METRICS,
                      manager_store_count=7, derived={"inventory_aging": False})
b4 = {b["label"]: b["payout"] for b in r4["bonuses"]}
check("inventory >10 days → $250 forfeited", b4["Inventory Control Bonus"] == 0.0, b4)
check("consolidated still paid ($300)", b4["Consolidated Bonus"] == 300.0, b4)
check("total = $1,540 + $300 = $1,840", r4["total"] == 1840.0, r4["total"])

print("\n(6) production beyond target does not overpay a capped component")
over = {"accessory_gp": 90000, "vhi_fios_count": 200, "edge_count": 200}
r5 = M.compute_payout(PLAN, actuals=over, qualifier_values=PASS_METRICS,
                      manager_store_count=7, derived={"inventory_aging": True})
c5 = {c["label"]: c["payout"] for c in r5["components"]}
check("Accessory capped at $1,120 despite $90k", c5["Accessory Sales"] == 1120.0, c5)
check("VHI capped at $120 despite 200 sales", c5["VHI / FIOS Sales"] == 120.0, c5)
check("Edge capped at $300 despite 200 acts", c5["Edge Activations"] == 300.0, c5)
check("capped grand total still $2,090", r5["total"] == 2090.0, r5["total"])

print("\n(2b) under-target pays pro-rata (rate × actual)")
half = {"accessory_gp": 28000, "vhi_fios_count": 30, "edge_count": 30}
r6 = M.compute_payout(PLAN, actuals=half, qualifier_values=PASS_METRICS,
                      manager_store_count=7, derived={"inventory_aging": True})
c6 = {c["label"]: c["payout"] for c in r6["components"]}
check("Accessory at half target pays $560", c6["Accessory Sales"] == 560.0, c6)
check("VHI at half target pays $60", c6["VHI / FIOS Sales"] == 60.0, c6)
check("Edge at half target pays $150", c6["Edge Activations"] == 150.0, c6)

print("\n(7) resolution: an employee assignment beats a role assignment")
role_plan = {"id": "role", "name": "Role default", "is_active": True,
             "assignments": [{"scope": "role", "scope_value": "district_manager", "priority": 0}]}
emp_plan = {"id": "emp", "name": "Jane's plan", "is_active": True,
            "assignments": [{"scope": "employee", "scope_value": "Jane Doe", "priority": 0}]}
won = M.resolve_plan([role_plan, emp_plan], employee_name="Doe, Jane", role="district_manager")
check("employee-scope plan wins over role-scope", won and won["id"] == "emp", won)
won2 = M.resolve_plan([role_plan], employee_name="Someone Else", role="district_manager")
check("role-scope plan still matches a DM with no personal plan", won2 and won2["id"] == "role", won2)
won3 = M.resolve_plan([emp_plan], employee_name="Someone Else", role="district_manager")
check("no matching assignment → no plan", won3 is None, won3)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
