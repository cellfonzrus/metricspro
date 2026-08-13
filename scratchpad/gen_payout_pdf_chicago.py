"""Render the employee-facing Commission Payout Structure PDF for the REAL
"Total Employee Comp Chicago" plan (the plan Silvia Nava was switched to),
reading the actual plan config from scratchpad/lux_plans.json and rendering it
with the SHIPPED renderer (commcalc/payout_structure.render_pdf). No DB."""
import sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from app.modules.commcalc import payout_structure as ps

plans = json.load(open(os.path.join(HERE, "lux_plans.json")))["plans"]
chi = next(p for p in plans if str(p.get("name", "")).lower().strip()
           == "total employee comp chicago")

doc = ps.build_doc([chi], tenant_name="Luxelink Wireless LLC",
                   generated_at="August 12, 2026", plan_id=chi["id"])
pdf = ps.render_pdf(doc)
out = os.path.join(HERE, "payout_structure_chicago.pdf")
open(out, "wb").write(pdf)

p = doc["plans"][0]
print("filename_for():", ps.filename_for(doc))
print("bytes:", len(pdf), "| starts:", pdf[:5])
print("plan:", p["name"], "| active:", p["active"])
print("applies:", p["applies"]["lines"])
print("\nPAYS:")
for i in p["pay_items"]:
    print(f"   - {i['what']:<12} | {i['condition']:<62} | {i['rate']:<26} | {i['frequency']}")
print("\nDOES NOT PAY:")
for i in p["no_pay_items"]:
    print(f"   - {i['what']:<12} | {i['condition']:<62} | {i['why']}")
print("\ntiers:", p["tiers"])
print("warnings:", p["warnings"])
if doc["footnotes"]:
    print("footnotes:", doc["footnotes"])
print("\nwrote:", out)
