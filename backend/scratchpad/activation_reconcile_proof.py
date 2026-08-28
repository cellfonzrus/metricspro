"""Proof: /activation-counts reconciles to the b2b "Month To Date Location Sales Report" (owner 2026-08-26).

That report's Total Activation = Activation + Port + BYOD + Tablet + Home Internet + Edge and EXCLUDES
Upgrade (verified against its totals row: 148+195+119+162+62+1 = 687). So the endpoint's `total_activation`
sums every type bucket except Upgrade, and offers an `as_of` cutoff so the count can be pinned to "as of
last night" and compared against a b2b snapshot at the same cutoff.

Proves: the b2b Total-Activation definition (excludes Upgrade), per-store + grand-total shape, the derived
type breakdown, date normalization (M/D/YYYY), and the as-of cutoff.

Run:  cd backend && python3 scratchpad/activation_reconcile_proof.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app.modules.commcalc.router as R

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  \033[92mPASS\033[0m {name}")
    else:
        FAIL += 1; print(f"  \033[91mFAIL\033[0m {name}  {extra}")

def rec(store, tid, ci, ct, sp, prod, cat, act, tt="Sale", date="8/1/2026", status=""):
    return {"data": {"Store": store, "Trans ID": tid, "Commission Item": ci, "Contract Type": ct,
                     "Trans Type": tt, "SP/PO Name": sp, "Product Desc": prod, "Category": cat,
                     "Activation#": act, "Trans Date": date, "Salesperson": "Kellie, Mark",
                     "Carrier": "Total Wireless", "MRC": "$30", "Activation Status": status},
            "period": "August 2026", "source_filename": "AD.xlsx"}

PA = "4640-A W Diversey Ave"
# 5 activations at PA: New, Port, BYOD, Tablet, Home Internet, Edge, + an Upgrade; one dated late for as-of.
ROWS = [
    rec(PA, "1", "Service Plan", "Activation", "MAX Plan $55", "Moto G", "KittedBranded", "101"),          # New
    rec(PA, "2", "Service Plan", "Activation With IDV", "MAX Plan $55", "Galaxy A17", "KittedBranded", "102"),  # Port
    rec(PA, "3", "Service Plan", "BYOD Activation", "BYO Plan $30", "Customer Phone", "Customer Phone", "103"),  # BYOD
    rec(PA, "4", "Service Plan", "Activation", "Tablet 6-Month Plan", "Galaxy Tab A11", "KittedBranded", "104"),  # Tablet
    rec(PA, "5", "Service Plan", "Activation", "Wireless Home Internet Router", "FWA Gateway", "KittedBranded", "105"),  # Home Internet
    rec(PA, "6", "Service Plan", "Upgrade", "Edge Lease Plan", "Galaxy S25 Edge", "KittedBranded", "106"),   # Edge (device wins over Upgrade)
    rec(PA, "7", "Service Plan", "Upgrade", "Device Upgrade", "Galaxy A17", "KittedBranded", "107"),         # Upgrade (excluded)
    rec(PA, "8", "Service Plan", "Activation", "MAX Plan $55", "Moto G", "KittedBranded", "108", date="8/26/2026"),  # late New
    rec(PA, "9", "Plan Option", "BYOD Activation", "Total Protect Insurance", "Customer Phone", "Insurance", "103"),  # feature -> excluded
    rec(PA, "10", "Service Plan", "Activation", "MAX Plan", "Moto G", "KittedBranded", "110", tt="Return"),  # return -> excluded
]


class _Res:
    def __init__(s, d): s.data = d
class _Q:
    def __init__(s, r): s._r = r
    def select(s, *a, **k): return s
    def eq(s, *a, **k): return s
    def in_(s, *a, **k): return s
    def limit(s, *a, **k): return s
    def execute(s): return _Res(s._r)
class _S:
    def __init__(s, r): s._r = r
    def table(s, *a, **k): return _Q(s._r)
class _C:
    def __init__(s, r): s._r = r
    def schema(s, *a, **k): return _S(s._r)

R.sb = lambda: _C(ROWS)
R.require_org = lambda o: None

print("(1) Total Activation EXCLUDES Upgrade (b2b definition), Edge device beats the Upgrade contract type")
o = R.activation_counts("August 2026", org_id="x")
t = o["total"]
# 8 Service-Plan activations (insurance + Return excluded). Of them 1 is a pure Upgrade -> excluded.
# So total_activation = 7 (New, Port, BYOD, Tablet, Home Internet, Edge, late New).
check("total_activation = 7 (8 activations − 1 upgrade)", t["total_activation"] == 7, t["total_activation"])
check("upgrade = 1", t["upgrade"] == 1, t["upgrade"])
check("edge = 1 (device type wins over the Upgrade contract type)", t["edge"] == 1, t["edge"])
check("breakdown: new 2, port 1, byod 1, tablet 1, home_internet 1",
      (t["activation"], t["port"], t["byod"], t["tablet"], t["home_internet"]) == (2, 1, 1, 1, 1),
      (t["activation"], t["port"], t["byod"], t["tablet"], t["home_internet"]))
check("Diversey store row present with total_activation 7",
      any(s["store"] == PA and s["total_activation"] == 7 for s in o["stores"]),
      [(s["store"], s["total_activation"]) for s in o["stores"]])

print("(2) as-of cutoff pins the count to a day (M/D/YYYY dates normalized)")
o2 = R.activation_counts("August 2026", org_id="x", as_of="2026-08-25")
check("the 8/26 activation is excluded", o2["excluded_after_cutoff"] == 1, o2["excluded_after_cutoff"])
check("total_activation drops to 6 as of 8/25", o2["total"]["total_activation"] == 6, o2["total"]["total_activation"])
check("as_of echoed as ISO", o2["as_of"] == "2026-08-25", o2["as_of"])

print("(3) empty capture → note, not a silent 0")
R.sb = lambda: _C([])
o3 = R.activation_counts("August 2026", org_id="x")
check("note set when nothing captured", bool(o3["note"]) and o3["counted"] == 0, o3)

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
