"""Offline proof for the ePay (Boost) FEE reconciliation (commcalc/epay_fee_recon.py).
System fee (raw_sales 'epay service charge') vs portal fee (DTD), per store-day. Pure functions only.

Run: `python3 harness_epay_fee_recon.py` from backend/.
"""
import sys
sys.path.insert(0, ".")

import app.modules.commcalc.epay_fee_recon as FR  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


# ── fee line detection ─────────────────────────────────────────────────────────────────────────────
check("is_fee_desc: 'ePay Service Charge' matches", FR.is_fee_desc("ePay Service Charge") is True)
check("is_fee_desc: case/space tolerant", FR.is_fee_desc("  epay service charge  ") is True)
check("is_fee_desc: a Boost RTR payment line is NOT a fee", FR.is_fee_desc("Boost RTR PayGo $5-$300") is False)

# ── system fee aggregation (raw_sales), store resolved, voids excluded ─────────────────────────────
# Mirrors the real _store_resolver: map known stores, fall back to the normalized raw string (never None).
resolver = lambda s: {"418 UNIONDALE": "418", "117 BURNSIDE": "117"}.get(
    str(s or "").strip().upper(), (str(s or "").strip().upper() or None))
sales = [
    {"store": "418 Uniondale", "trans_date": "2026-08-18", "product_desc": "ePay Service Charge", "ext_price": "4", "voided": ""},
    {"store": "418 Uniondale", "trans_date": "2026-08-18", "product_desc": "ePay Service Charge", "ext_price": "4", "voided": ""},
    {"store": "418 Uniondale", "trans_date": "2026-08-18", "product_desc": "Boost RTR PayGo", "ext_price": "95", "voided": ""},  # not a fee
    {"store": "418 Uniondale", "trans_date": "2026-08-18", "product_desc": "ePay Service Charge", "ext_price": "4", "voided": "true"},  # voided
    {"store": "117 Burnside", "trans_date": "2026-08-19", "product_desc": "ePay Service Charge", "ext_price": "4", "voided": ""},
    {"store": "Ghost Store", "trans_date": "2026-08-18", "product_desc": "ePay Service Charge", "ext_price": "4", "voided": ""},  # unresolved -> keyed by raw
]
sysfee = FR.aggregate_system_fee(sales, resolver)
check("system fee: sums the fee lines for a store-day (2x4=8)", sysfee.get(("418", "2026-08-18")) == 8.0, sysfee)
check("system fee: excludes the non-fee (Boost RTR) line", sysfee.get(("418", "2026-08-18")) == 8.0)
check("system fee: excludes a voided fee line", sysfee.get(("418", "2026-08-18")) == 8.0)
check("system fee: other store-day tallied", sysfee.get(("117", "2026-08-19")) == 4.0, sysfee)
check("system fee: unresolved store still keyed (by raw, never dropped)", ("GHOST STORE", "2026-08-18") in sysfee, list(sysfee))

# ── recon join: system vs portal, sorted by |var| desc, flags ──────────────────────────────────────
system = {("418", "2026-08-18"): 8.0, ("117", "2026-08-19"): 4.0, ("200", "2026-08-18"): 50.0}
portal = {("418", "2026-08-18"): {"fee": 8.0}, ("117", "2026-08-19"): {"fee": 12.0}, ("300", "2026-08-18"): {"fee": 20.0}}
rows = FR.build_recon(system, portal, tolerance=1.0)
by = {(r["store_code"], r["close_date"]): r for r in rows}
check("recon: matching fee -> var 0, no flag", by[("418", "2026-08-18")]["var"] == 0.0 and by[("418", "2026-08-18")]["flag"] is False)
check("recon: portal > system -> shortage flagged", by[("117", "2026-08-19")]["var"] == -8.0 and by[("117", "2026-08-19")]["shortage"] is True)
check("recon: system-only store-day (portal missing) shows portal 0, overage", by[("200", "2026-08-18")]["portal_fee"] == 0.0 and by[("200", "2026-08-18")]["overage"] is True)
check("recon: portal-only store-day appears (system 0)", by[("300", "2026-08-18")]["system_fee"] == 0.0 and by[("300", "2026-08-18")]["in_system"] is False)
check("recon: rows sorted by |var| descending (biggest discrepancy first)",
      abs(rows[0]["var"]) >= abs(rows[-1]["var"]), [r["var"] for r in rows])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
