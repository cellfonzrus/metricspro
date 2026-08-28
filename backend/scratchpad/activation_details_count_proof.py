"""Proof for the Activations dataset (b2b "Activation Details" report) — owner 2026-08-26.

Uses the owner's ACTUAL 5 sample rows (Trans 5505/5507/5508/5525/5527) captured via the self-serve custom
import (raw_custom_import JSONB), plus an added Plan-Option (insurance) line and a Return, to prove:
  • one row per activation = rows where Commission Item == 'Service Plan' (Plan Option/insurance excluded),
  • Returns/cancelled excluded, dedup by Activation#,
  • per-store activation count (the number that must reconcile to the b2b MTD figure),
  • the derived Type breakdown (BYOD / Tablet / Port / Upgrade / New).

Run:  cd backend && python3 scratchpad/activation_details_count_proof.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app.modules.commcalc.router as R
from app.modules.commcalc import custom_report

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  \033[92mPASS\033[0m {name}")
    else:
        FAIL += 1; print(f"  \033[91mFAIL\033[0m {name}  {extra}")

ORG, PERIOD = "org-lux", "August 2026"

# The owner's real columns (subset that matters) → a raw_custom_import `data` dict.
def rec(store, tid, ci, ct, sp, prod, cat, act_no, trans_type="Sale", status="", sales="Kellie, Mark"):
    return {"data": {
        "Store": store, "Trans ID": tid, "Service Date": "8/1/2026", "Salesperson": sales,
        "Commission Item": ci, "Contract Type": ct, "Trans Type": trans_type,
        "SP/PO Name": sp, "Product Desc": prod, "Category": cat, "Activation#": act_no,
        "Carrier": "Total Wireless", "MRC": "$30.00", "Trans Date": "8/1/2026",
        "Activation Status": status, "Action Type": "Activation",
    }, "period": PERIOD, "source_filename": "ActivationDetails.xlsx"}

PA = "957 Pennsylvania Avenue"
ROWS = [
    rec(PA, "5505", "Service Plan", "BYOD Activation", "Total MAX 5G BYO Plan $30", "Customer Phone", "Customer Phone", "3148"),
    rec(PA, "5507", "Service Plan", "Activation", "Total Wireless Base Unlimited Tablet 6-Month Plan $60", "Samsung Galaxy Tab A11+ 5G TO", "KittedBranded", "3149"),
    rec("639 W Lincoln Hwy", "5508", "Service Plan", "Activation With IDV", "Total MAX 5G Plan $55", "Motorola Moto G 5G 2026 TO", "KittedBranded", "3150", sales="Chavez, Antonio"),
    rec(PA, "5525", "Service Plan", "BYOD Activation", "Total MAX 5G BYO Plan $30", "Customer Phone", "Customer Phone", "3151"),
    rec("3735 W 26th St", "5527", "Service Plan", "Upgrade", "Total Wireless Device Upgrade", "Samsung Galaxy A17 5G TO", "KittedBranded", "3152", sales="Jacobo, Liset"),
    # Plan Option (insurance) on the SAME activation as 5505 -> a FEATURE, not an activation -> excluded:
    rec(PA, "5505", "Plan Option", "BYOD Activation", "Total Protect Insurance", "Customer Phone", "Insurance", "3148"),
    # A Return -> excluded:
    rec(PA, "5599", "Service Plan", "Activation", "Total MAX 5G Plan $55", "Some Phone", "KittedBranded", "3160", trans_type="Return"),
    # A duplicate Service-Plan line for an existing activation -> deduped, counted once:
    rec(PA, "5507", "Service Plan", "Activation", "Total Wireless Base Unlimited Tablet 6-Month Plan $60", "Samsung Galaxy Tab A11+ 5G TO", "KittedBranded", "3149"),
]


class _Res:
    def __init__(self, d): self.data = d
class _Q:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): return _Res(self._rows)
class _Schema:
    def __init__(self, rows): self._rows = rows
    def table(self, *a, **k): return _Q(self._rows)
class _Client:
    def __init__(self, rows): self._rows = rows
    def schema(self, *a, **k): return _Schema(self._rows)


ctx = {"market_for": (lambda s: "NY/NJ" if "Pennsylvania" in (s or "") else "Chicago")}
rows = R._cr_resolve_activation_details(_Client(ROWS), ORG, PERIOD, ctx)

print("(1) resolver — Service-Plan rows only, insurance/return excluded, deduped")
check("5 activations counted (PO insurance + Return excluded, dup deduped)", len(rows) == 5, len(rows))
tids = sorted(r["trans_id"] for r in rows)
check("the 5 activation Trans IDs", tids == ["5505", "5507", "5508", "5525", "5527"], tids)
check("every row is one activation (activations=1)", all(r["activations"] == 1 for r in rows))

print("(2) derived Type breakdown matches the sample")
by_bucket = {}
for r in rows:
    by_bucket[r["bucket"]] = by_bucket.get(r["bucket"], 0) + 1
check("BYOD x2", by_bucket.get("BYOD") == 2, by_bucket)
check("Tablet x1", by_bucket.get("Tablet") == 1, by_bucket)
check("Port x1 (Activation With IDV)", by_bucket.get("Port") == 1, by_bucket)
check("Upgrade x1", by_bucket.get("Upgrade") == 1, by_bucket)

print("(3) per-store activation count (the number that must reconcile to b2b MTD)")
ds = custom_report.dataset_by_key("activation_details")
gfield = custom_report.resolve_group_field(ds, "store")
out_rows, _ = custom_report.group_and_aggregate(rows, ds["columns"], gfield)
by_store = {r.get("store"): r for r in out_rows}
check("957 Pennsylvania Avenue = 3 activations", by_store.get(PA, {}).get("activations") == 3,
      by_store.get(PA, {}).get("activations"))
check("639 W Lincoln Hwy = 1", by_store.get("639 W Lincoln Hwy", {}).get("activations") == 1,
      by_store.get("639 W Lincoln Hwy", {}).get("activations"))
check("3735 W 26th St = 1", by_store.get("3735 W 26th St", {}).get("activations") == 1)

print("(4) wired + Trans ID present for drill-down")
check("activation_details in resolver registry", "activation_details" in R._CUSTOM_REPORT_RESOLVERS)
check("Trans ID carried on every activation", all(r["trans_id"] for r in rows))

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
