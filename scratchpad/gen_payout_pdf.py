"""Generate the employee-facing Payout Structure PDF for the plan Silvia Nava is on
("Total Employee Comp NY"), using the SHIPPED renderer and the real LuxeLink rule
fixtures (measured 2026-08-11). No DB — same document model the live endpoint builds."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.modules.commcalc import payout_structure as ps

# Real LuxeLink "Total Employee Comp NY" rules (from harness_payout_structure.py fixtures).
R_NY_ACC = {"label": "Accessories", "match_field": "accessory", "match_op": "equals",
            "match_value": "yes", "qualifies": True, "payout_kind": "pct_price",
            "amount": "10.0", "pct": "0.1", "tiered": False, "sort": 1}
R_NY_ACT = {"label": "Activations", "match_field": "category", "match_op": "equals",
            "match_value": "KittedBranded", "qualifies": True, "payout_kind": "flat_per_unit",
            "amount": "10.0", "pct": "0.0", "tiered": False, "sort": 0}

# Real assignments on plan 71b0524c… (from scratchpad/lux_plans.json). Silvia Nava is one of them.
NY_ASSIGNEES = ["Mea Collins", "Silvia Nava", "arif", "Syed", "Nivas"]

plan_ny = {
    "id": "71b0524c-dce2-43fd-bf01-a3cc61a6207b",
    "name": "Total Employee Comp NY",
    "is_active": True,
    "rules": [R_NY_ACT, R_NY_ACC],
    "tiers": [],
    "assignments": [{"scope": "employee", "scope_value": n} for n in NY_ASSIGNEES],
}

doc = ps.build_doc([plan_ny], tenant_name="Luxelink Wireless LLC",
                   generated_at="August 12, 2026", plan_id=plan_ny["id"])
pdf = ps.render_pdf(doc)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payout_structure_ny.pdf")
with open(out, "wb") as f:
    f.write(pdf)

p = doc["plans"][0]
print("filename_for():", ps.filename_for(doc))
print("bytes:", len(pdf), "| starts:", pdf[:5])
print("plan:", p["name"], "| active:", p["active"])
print("applies:", p["applies"]["lines"])
print("pay_items:")
for i in p["pay_items"]:
    print("   -", i["what"], "|", i["condition"], "|", i["rate"], "|", i["frequency"])
print("warnings:", p["warnings"])
print("wrote:", out)
