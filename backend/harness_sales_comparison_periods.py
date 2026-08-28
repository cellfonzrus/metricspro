"""PROOF — Sales Comparison period-over-period per-item report (owner spec).

Exercises app.modules.commcalc.sales_comparison.build against a hand-built raw_sales fixture spanning two
aligned periods, proving:
  * the 6 owner buckets: Phones/BYOD/Activation/Tablets = UNITS, Accessories = $, Financing = units AND $
  * classification reuses the shared engine (classify_contract_type + installment_category chain +
    injected accessory classifier + financing_registry.matcher_hits)
  * day-of-month alignment (full month / week-1 / as-of-day) cuts BOTH windows identically
  * CARRIER-SCOPED financing: under the Boost lens only Boost's vendor (ACIMA) feeds the single
    Financing line; under the Total lens only Total's vendor (TW/Edge) does — never both.

Run: python scratchpad/sales_comparison_periods_proof.py   (from backend/)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import sales_comparison as sc
from app.modules.commcalc import installment_category as icat


class _RaisingClient:
    """A supabase-shaped stub whose every query raises, so load_category_rules degrades to its built-in
    classification rules — exactly the code path a tenant without migration 245 hits."""
    def schema(self, *_a, **_k): return self
    def table(self, *_a, **_k): return self
    def select(self, *_a, **_k): raise RuntimeError("no DB in harness")


RULES = icat.load_category_rules(_RaisingClient(), "org")

# Injected accessory classifier — the report injects the SAME shared one in prod; here a line is an
# accessory iff its category is 'Accessories'.
def is_acc(row):
    return str(row.get("category") or "").strip().lower() == "accessories"

# Resolved financing vendors (shape produced by financing_registry.resolve_vendors): ACIMA→Boost,
# Edge/TW→Total. Each carries carrier assignments used by vendors_for_carrier.
ACIMA = {"vendor_key": "acima", "label": "ACIMA lease-to-own", "enabled": True,
         "detection_status": "configured", "carriers": [{"carrier_name": "Boost"}],
         "matchers": [{"match_field": "tender_type", "match_op": "contains", "match_value": "acima"}]}
EDGE = {"vendor_key": "edge", "label": "Edge financing", "enabled": True,
        "detection_status": "configured", "carriers": [{"carrier_name": "Total"}],
        "matchers": [{"match_field": "tender_type", "match_op": "word", "match_value": "tw"}]}
VENDORS = [ACIMA, EDGE]


def L(**kw):
    base = {"trans_id": "", "store": "S1", "trans_date": "2026-08-03", "contract_type": "",
            "product_desc": "", "department": "", "category": "", "tender_type": "",
            "ext_price": 0, "gp": 0, "voided": "", "trans_type": "Sale"}
    base.update(kw)
    return base


# ── BASE period 2026-08 ──────────────────────────────────────────────────────────────────────────
BASE = [
    # T1 (S1, day 03): phone premium activation + accessory, ACIMA-financed (Boost)
    L(trans_id="T1", store="S1", trans_date="2026-08-03", department="HANDSET", category="HANDSET",
      product_desc="iPhone 15", contract_type="Activation", tender_type="ACIMA Financing",
      ext_price=800, gp=200),
    L(trans_id="T1", store="S1", trans_date="2026-08-03", category="Accessories",
      product_desc="Case", ext_price=40, gp=20),
    # T2 (S1, day 05): BYOD activation on a SIM (no device unit)
    L(trans_id="T2", store="S1", trans_date="2026-08-05", product_desc="SIM",
      contract_type="BYOD Port-In", ext_price=10, gp=5),
    # T3 (S1, day 22): tablet UPGRADE (not a premium activation)
    L(trans_id="T3", store="S1", trans_date="2026-08-22", department="TABLET", category="TABLET",
      product_desc="Galaxy Tab", contract_type="Upgrade", ext_price=500, gp=100),
    # T4 (S2, day 02): phone premium activation, TW/Edge-financed (Total)
    L(trans_id="T4", store="S2", trans_date="2026-08-02", department="HANDSET", category="HANDSET",
      product_desc="Pixel 8", contract_type="Port-In", tender_type="TW Financing",
      ext_price=700, gp=150),
]
# ── COMPARE period 2026-07 ──────────────────────────────────────────────────────────────────────
CMP = [
    # C1 (S1, day 03): phone premium activation, ACIMA-financed (Boost)
    L(trans_id="C1", store="S1", trans_date="2026-07-03", department="HANDSET", category="HANDSET",
      product_desc="iPhone 14", contract_type="Activation", tender_type="ACIMA Financing",
      ext_price=600, gp=150),
    # C2 (S1, day 04): accessory-only sale
    L(trans_id="C2", store="S1", trans_date="2026-07-04", category="Accessories",
      product_desc="Charger", ext_price=25, gp=12),
]

FAILS = []
def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + f"{name}: got={got} want={want}")
    if not ok:
        FAILS.append(name)


def totals(out):
    return {t["key"]: t for t in out["totals_by_category"]}


def run_window(label, base, cmp, carrier):
    vend = sc.vendors_for_carrier(VENDORS, carrier)
    return sc.build(base, cmp, RULES, is_acc, vend,
                    base_period="2026-08", compare_period="2026-07", mode="mom",
                    window_label=label)


print("=== FULL MONTH · Boost lens ===")
out = run_window("Full month", BASE, CMP, "boost")
t = totals(out)
# category order + metric are exactly the owner spec
check("category order", [c["key"] for c in out["categories"]],
      ["phone", "byod", "activation", "tablet", "accessory", "financing"])
check("metrics", {c["key"]: c["metric"] for c in out["categories"]},
      {"phone": "units", "byod": "units", "activation": "units", "tablet": "units",
       "accessory": "dollars", "financing": "both"})
# Phones: T1,T4 base / C1 compare
check("phone current units", t["phone"]["current"], 2)
check("phone previous units", t["phone"]["previous"], 1)
# BYOD: T2 base / none compare
check("byod current units", t["byod"]["current"], 1)
check("byod previous units", t["byod"]["previous"], 0)
# Activation (premium): T1,T4 base / C1 compare (T3 upgrade is NOT an activation)
check("activation current units", t["activation"]["current"], 2)
check("activation previous units", t["activation"]["previous"], 1)
# Tablets: T3 base / none compare
check("tablet current units", t["tablet"]["current"], 1)
check("tablet previous units", t["tablet"]["previous"], 0)
# Accessories = $ : T1 $40 base / C2 $25 compare
check("accessory current $", t["accessory"]["current_rev"], 40.0)
check("accessory previous $", t["accessory"]["previous_rev"], 25.0)
check("accessory current units", t["accessory"]["current"], 1)
# Financing (Boost) = units AND $ : T1 ($800) base / C1 ($600) compare; T4 (TW=Total) EXCLUDED
check("financing[boost] current units", t["financing"]["current"], 1)
check("financing[boost] current $", t["financing"]["current_rev"], 800.0)
check("financing[boost] previous units", t["financing"]["previous"], 1)
check("financing[boost] previous $", t["financing"]["previous_rev"], 600.0)

print("=== FULL MONTH · Total lens (financing must flip to the TW/Edge vendor) ===")
outT = run_window("Full month", BASE, CMP, "total")
tT = totals(outT)
# Financing (Total): T4 ($700) base only; ACIMA (Boost) EXCLUDED
check("financing[total] current units", tT["financing"]["current"], 1)
check("financing[total] current $", tT["financing"]["current_rev"], 700.0)
check("financing[total] previous units", tT["financing"]["previous"], 0)
# Non-financing buckets identical regardless of lens
check("phone units unchanged by lens", tT["phone"]["current"], 2)

print("=== COMPLIANCE: only ONE financing line, never both carriers ===")
check("single financing row (boost)", len([c for c in out["categories"] if c.get("financing")]), 1)
check("single financing row (total)", len([c for c in outT["categories"] if c.get("financing")]), 1)
check("financing label is neutral (boost)",
      [c["label"] for c in out["categories"] if c.get("financing")], ["Financing"])
check("no vendor-key leak in keys",
      any(str(c["key"]).startswith("fin:") for c in out["categories"]), False)

print("=== DAY ALIGNMENT · Week-1 (days 1-7), Boost ===")
bw = [r for r in BASE if sc.in_day_window(r["trans_date"], 0, 1)]
cw = [r for r in CMP if sc.in_day_window(r["trans_date"], 0, 1)]
outw = run_window("Week 1", bw, cw, "boost")
tw = totals(outw)
# T3 (day 22) drops out → no tablet in base
check("wk1 tablet current", tw.get("tablet", {}).get("current", 0), 0)
check("wk1 phone current (T1,T4 in wk1)", tw["phone"]["current"], 2)
check("wk1 byod current (T2 day5 in wk1)", tw["byod"]["current"], 1)

print("=== DAY ALIGNMENT · as-of day 4 (day<=4), Boost ===")
ba = [r for r in BASE if sc.in_day_window(r["trans_date"], 4, 0)]
ca = [r for r in CMP if sc.in_day_window(r["trans_date"], 4, 0)]
outa = run_window("Through day 4", ba, ca, "boost")
ta = totals(outa)
# base keeps T1(03) + T4(02); drops T2(05) + T3(22)
check("asof4 phone current", ta["phone"]["current"], 2)
check("asof4 byod current (T2 day5 dropped)", ta.get("byod", {}).get("current", 0), 0)
# compare keeps C1(03) + C2(04)
check("asof4 accessory previous $ (C2 kept)", ta["accessory"]["previous_rev"], 25.0)

print("=== vendor_serves_carrier unit checks ===")
check("acima serves boost", sc.vendor_serves_carrier(ACIMA, "boost"), True)
check("acima does NOT serve total", sc.vendor_serves_carrier(ACIMA, "total"), False)
check("edge serves total", sc.vendor_serves_carrier(EDGE, "total"), True)
check("edge does NOT serve boost", sc.vendor_serves_carrier(EDGE, "boost"), False)
check("neutral vendor (no carriers) serves any",
      sc.vendor_serves_carrier({"carriers": []}, "boost"), True)

print()
if FAILS:
    print(f"RESULT: {len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
