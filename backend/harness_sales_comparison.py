"""Fixture-driven check of sales_comparison pure math (no DB)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import sales_comparison as sc
from app.modules.commcalc import installment_category as icat

rules = icat.effective_rules([])  # built-in category ladder

# One resolved financing vendor: ACIMA via tender_type contains "acima"
vendors = [{
    "vendor_key": "acima", "label": "ACIMA lease-to-own", "enabled": True,
    "detection_status": "configured",
    "matchers": [{"match_field": "tender_type", "match_op": "contains", "match_value": "acima"}],
}]

def is_acc(r):
    return str(r.get("department") or "").strip().lower() == "ondigo"

def line(tid, store, date, dept="", cat="", prod="", ct="", tender="", ext=0.0, gp=0.0, serial=""):
    return {"trans_id": tid, "store": store, "trans_date": date, "department": dept, "category": cat,
            "product_desc": prod, "contract_type": ct, "tender_type": tender, "ext_price": ext, "gp": gp,
            "serial_1": serial, "voided": "", "trans_type": ""}

# BASE window (2026-08): store A — 2 phones, 1 tablet, 1 byod, accessories, 1 acima financed
base = [
    # txn1: a phone activation with an accessory + acima tender
    line("t1", "Store A", "2026-08-03", dept="BrandedHandset", prod="iPhone 15", ct="New Activation",
         tender="ACIMA Lease", ext=800, gp=150, serial="356789012345678"),
    line("t1", "Store A", "2026-08-03", dept="Ondigo", prod="Case", ext=30, gp=20),
    # txn2: a phone upgrade
    line("t2", "Store A", "2026-08-10", dept="BrandedHandset", prod="Galaxy S24", ct="Upgrade",
         ext=700, gp=120, serial="356789012345679"),
    # txn3: a tablet
    line("t3", "Store A", "2026-08-12", prod="iPad tablet", ct="New Activation", ext=500, gp=90,
         serial="356789012345680"),
    # txn4: BYOD activation
    line("t4", "Store A", "2026-08-15", prod="SIM Kit", ct="BYOD Activation", ext=10, gp=5,
         serial="8901234567890123456"),
    # txn5: accessory-only sale
    line("t5", "Store A", "2026-08-20", dept="Ondigo", prod="Screen Protector", ext=25, gp=18),
    # voided line must be ignored
    line("t6", "Store A", "2026-08-21", dept="BrandedHandset", prod="Pixel", ext=600, gp=100,
         serial="356789012345681"),
]
base[-1]["voided"] = "true"

# COMPARE window (2026-07): store A — 1 phone, 1 tablet, 0 byod, 0 acima
comp = [
    line("u1", "Store A", "2026-07-05", dept="BrandedHandset", prod="iPhone 14", ct="New Activation",
         ext=750, gp=140, serial="356789012345690"),
    line("u2", "Store A", "2026-07-06", prod="Galaxy Tab tablet", ct="New Activation", ext=450, gp=80,
         serial="356789012345691"),
    line("u1", "Store A", "2026-07-05", dept="Ondigo", prod="Case", ext=20, gp=12),
]

def resolve_market(store):
    return "Metro" if store == "Store A" else ""

out = sc.build(base, comp, rules, is_acc, vendors,
               base_period="2026-08", compare_period="2026-07", mode="mom",
               window_label="Full month", resolve_market=resolve_market)

print("=== totals_by_category ===")
for t in out["totals_by_category"]:
    print(f"  {t['label']:20s} cur={t['current']:>3} prev={t['previous']:>3} "
          f"Δ={t['delta']:>3} pct={t['pct']} rev cur={t['current_rev']}")

print("=== overall ===", out["overall"])
print("=== window ===", out["window_label"], "| periods", out["base_period"], "vs", out["compare_period"])
print("=== row count ===", len(out["rows"]))

# ── assertions ──
tbc = {t["key"]: t for t in out["totals_by_category"]}
assert tbc["phone"]["current"] == 2, ("phones base", tbc["phone"]["current"])
assert tbc["phone"]["previous"] == 1, ("phones prev", tbc["phone"]["previous"])
assert tbc["phone"]["pct"] == 100.0, ("phones pct", tbc["phone"]["pct"])
assert tbc["tablet"]["current"] == 1 and tbc["tablet"]["previous"] == 1, "tablet"
assert tbc["tablet"]["pct"] == 0.0, ("tablet pct", tbc["tablet"]["pct"])
assert tbc["byod"]["current"] == 1 and tbc["byod"]["previous"] == 0, "byod"
assert tbc["byod"]["pct"] is None, ("byod pct should be None (new)", tbc["byod"]["pct"])
# accessories: 2 accessory lines in base (case + screen protector), 1 in compare
assert tbc["accessory"]["current"] == 2, ("acc base", tbc["accessory"]["current"])
assert tbc["accessory"]["previous"] == 1, ("acc prev", tbc["accessory"]["previous"])
assert tbc["accessory"]["current_rev"] == 55.0, ("acc rev", tbc["accessory"]["current_rev"])
# Financing: 1 in base, 0 in compare.
# This used to assert a PER-VENDOR key, `tbc["fin:acima"]`. Commit f4ce76c5 deliberately collapsed
# the per-vendor financing rows into ONE neutral "Financing" line, and that collapse is
# COMPLIANCE-CRITICAL, not cosmetic: the report was flagged for a dual-affiliation leak, and the
# fix is that the vendor BRAND (ACIMA / TW / Edge) is never emitted at all — only the neutral
# label. So the old key is not something to restore; asserting it back would re-open the defect.
assert "fin:acima" not in tbc, ("per-vendor financing keys must stay collapsed (f4ce76c5) — a "
                                "fin:<vendor> key is the dual-affiliation leak coming back", sorted(tbc))
assert tbc["financing"]["current"] == 1 and tbc["financing"]["previous"] == 0, (
    "financing", tbc["financing"])
assert tbc["financing"]["label"] == "Financing", ("financing label must stay brand-neutral",
                                                  tbc["financing"]["label"])
# The compliance guarantee itself, asserted over the WHOLE payload rather than one row: the
# configured vendor's brand and key appear nowhere a screen could render them.
_payload = repr(out).lower()
assert "acima" not in _payload, "financing vendor BRAND leaked into the sales-comparison payload"
assert "fin:" not in _payload, "a per-vendor financing key leaked into the sales-comparison payload"
# phone revenue for base = txn1 (800, accessory excluded) + txn2 (700) = 1500
assert tbc["phone"]["current_rev"] == 1500.0, ("phone rev", tbc["phone"]["current_rev"])
# overall txns: base has 5 live txns (t6 voided), compare has 2
assert out["overall"]["current_txns"] == 5, ("cur txns", out["overall"]["current_txns"])
assert out["overall"]["previous_txns"] == 2, ("prev txns", out["overall"]["previous_txns"])

# ── scenario helper assertions ──
assert sc.period_ym("2026-08") == (2026, 8)
assert sc.period_ym("August 2026") == (2026, 8)
assert sc.period_ym("garbage") is None
assert sc.shift_month(2026, 1, 1) == (2025, 12), sc.shift_month(2026, 1, 1)     # MoM across year edge
assert sc.shift_month(2026, 8, 1) == (2026, 7)
assert sc.week_bounds(1) == (1, 7) and sc.week_bounds(5) == (29, 31)
# day windows
assert sc.in_day_window("2026-08-13", 13, 0) is True
assert sc.in_day_window("2026-08-14", 13, 0) is False
assert sc.in_day_window("2026-08-05", 0, 1) is True      # week 1
assert sc.in_day_window("2026-08-10", 0, 1) is False     # day 10 not in week 1
assert sc.in_day_window("2026-08-10", 0, 2) is True      # week 2 (8-14)
assert sc.in_day_window("", 0, 0) is True                # undated line kept only in full-month window
assert sc.in_day_window("", 13, 0) is False              # undated line excluded from a MTD slice

# ── "as of day" alignment: cut both windows to day ≤ 12 → base loses the day-15 BYOD + day-20 accessory
b12 = [r for r in base if sc.in_day_window(r["trans_date"], 12, 0)]
c12 = [r for r in comp if sc.in_day_window(r["trans_date"], 12, 0)]
out12 = sc.build(b12, c12, rules, is_acc, vendors, base_period="2026-08", compare_period="2026-07",
                 mode="mom", window_label="Through day 12", resolve_market=resolve_market)
t12 = {t["key"]: t for t in out12["totals_by_category"]}
assert t12["byod"]["current"] == 0, ("byod should drop out of day<=12 window", t12["byod"]["current"])
assert t12["phone"]["current"] == 2, ("phones still 2 by day 12", t12["phone"]["current"])
assert t12["accessory"]["current"] == 1, ("only the txn1 case remains by day 12", t12["accessory"]["current"])

print("\nALL ASSERTIONS PASSED ✅")
