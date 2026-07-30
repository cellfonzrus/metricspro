"""On-Inventory 3-Way Rebate Recon (mod-asset, migration band 300-399, mig 310).

OWNER DIRECTIVE 2026-07-28 (verbatim): "the on inventory per store report seems to be off than the
actual inventory in each store, the imei which show up in the on inventory must be check with the
imei rebate report under assest landing and the commission report where it shows the rebate got
paid, a 3 way recon needs to be done to find the missing phones and the non activated phones"

THE THREE LEGS — identified precisely, not assumed (see also 310_asset_oninv_3way_recon_rpc.sql,
which does the actual join):

  1. ON-INVENTORY. backend/app/modules/asset/router.py — the SAME predicate
     `GET /asset/on-inventory-by-store` (router.py, function get_on_inventory_by_store) and
     `GET /asset/aging` already use:
         asset_ledger.org_id = <org> AND date_sold IS NULL
         AND category ILIKE '%On Inventory%'
     This module reuses that identical predicate (duplicated into the RPC's `oninv` CTE, not
     re-derived) so this recon reconciles the SAME set the owner already sees on those two pages —
     never a set that could silently disagree with them.

  2. "THE IMEI REBATE REPORT UNDER ASSET LANDING." Turned out to be data already sitting on the
     on-inventory row itself: `asset_ledger.reimbursement` / `asset_ledger.reimbursement_date`
     (parsed straight from VIP's own "Reimbursement" / "Reimbursement Date" columns —
     asset_parser.py `COLUMN_MAP`). The closest existing dedicated REPORT for this is
     `GET /asset/aging-rebate` ("💵 Aging — Rebate Received", frontend
     commcalc/asset/aging-rebate/page.tsx) — it already defines "a rebate was received but the
     device is still in inventory" as exactly `reimbursement > 0` on an on-inventory row, which is
     leg 2 here. (Note for the operator: `/aging-rebate` is a real, working page but is not
     currently linked from the Asset Ledger landing nav bar — see this module's handoff.) Because
     `asset_ledger` is a full wipe-and-replace snapshot (one row per device as of the LAST upload —
     asset-2 / mig 300), there is no separate "leg 2 table" to join against; the on-inventory row
     IS the leg-2 evidence.

  3. "THE COMMISSION REPORT WHERE IT SHOWS THE REBATE GOT PAID." commcalc.raw_payment_detail (the
     ePay Payment Detail Report), joined by IMEI. This reuses the EXACT join shape router.py's
     `_epay_payments_map` already uses for the Appeals charge-group report (denial_reason /
     "what Boost actually paid" per device) — leg-3 evidence is ANY raw_payment_detail row for that
     IMEI, returned with its type/date/amount so a reader can see exactly what it was. This is
     deliberately NOT pre-filtered to a narrower "device-rebate-only" payment_type subset (e.g. the
     DFB/ISDFB/DEVICE_REIMB buckets `commcalc/discrepancy_engine.parse_payment_type` classifies)
     because `_epay_payments_map` itself doesn't pre-filter either — this recon must not silently
     diverge from what the rest of the module already shows for the same IMEI. The report displays
     the raw payment_type(s) so a reader can judge relevance themselves.

MATCHING: normalize both sides identically before comparing — strip, uppercase, drop a trailing
".0" (mirrors router.py's `_norm_imei` exactly). No fuzzy/invented matching beyond that. Normalizing
BOTH sides of the join is equivalent to `_epay_payments_map`'s raw+normalized+normalized-with-.0
candidate-set approach (same collisions caught), just expressed as one join predicate in the RPC.

CLASSIFICATION — see the migration file header for the full decision tree with rationale. Summary:
  - unmatchable            — esn_imei blank/null on the on-inventory row (leg 3 uncheckable at all)
  - missing_phone_candidate — leg 2 AND leg 3 agree the device was reimbursed/paid for, OR leg 2
    alone shows it while leg 3's data source (raw_payment_detail) has ZERO rows for the WHOLE org
    (not loaded, so it can't disagree)
  - conflict                — leg 2 and leg 3 actively DISAGREE (one says paid, the other — checked
    and confirmed unavailable, not just "not loaded" — says nothing). Never collapsed into either
    of the other buckets.
  - non_activated            — no rebate evidence in any leg that could actually be checked (true
    stock)

DEVICE $ VALUE: `owed_to_vip` — the same column On-Inventory-by-Store, Aging, Charges Dashboard,
RMA, and Owed-Weekly all already treat as a device's $ exposure.

MOUNTING: own file (same precedent as purchase_orders.py) so asset/router.py's diff stays a
2-line `include_router` append — no main.py change (same /api/v1/asset prefix). Self-registers its
own admin-attention provider at import time (guarded try/except, degrades to "contributes nothing"
if core/import_health.py is ever refactored) — deliberately NOT added to attention.py, to avoid a
merge collision with the concurrently in-flight `agent/asset/market-filter-dropdown` package, which
is actively editing that file for unrelated reasons.

MARKET/STORE FILTER CONVENTIONS: this package originally (2026-07-28) duplicated the
market-filter-dropdown package's `NO_MARKET_SENTINEL` / multi-store-CSV conventions locally,
since that package was still uncommitted-in-flight in a parallel worktree at build time, and
flagged the duplication for a merge-time dedupe pass. Both packages have since landed on main
(`2ed44ba`/`cff89b0` and `358876f`/`48406d0`); **2026-07-29: dedupe done** — `NO_MARKET_SENTINEL`,
`_store_list`, and the market-filter-vs-RPC-param translation `_call_recon_rpc` used are now
imported from `app.modules.asset.market_filter` (the new shared helper module both this file and
router.py use) instead of being locally redefined. See that file's module docstring for the full
history. This module's own read-only classification/join logic (the 3-way recon RPC call shape,
row shaping, admin-attention provider) is unchanged.

NO WRITES: every endpoint in this file is read-only. It never inserts/updates/deletes any ledger,
flag, or investigation row.
"""
from datetime import date

from fastapi import APIRouter

from app.core.database import get_supabase
from app.modules.asset.market_filter import NO_MARKET_SENTINEL, _store_list, resolve_market_for_rpc

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"

_MIGRATION_MSG = ("On-Inventory 3-Way Recon migration pending — ask the operator to run "
                   "database/migrations/310_asset_oninv_3way_recon_rpc.sql in the Supabase SQL Editor.")
_MISSING_SCHEMA_MARKERS = ("PGRST202", "PGRST205", "PGRST203", "schema cache", "does not exist")

# NO_MARKET_SENTINEL is imported above from market_filter.py (2026-07-29 dedupe — see module
# docstring "MARKET/STORE FILTER CONVENTIONS"); re-exported as a module attribute unchanged so
# `oninv_recon.NO_MARKET_SENTINEL` (used by harness_asset_oninv_3way_recon.py) keeps working.

# Tenant-configurable-in-spirit, hardcoded-for-now (documented constant, same posture as
# attention.py's `_STALE_DAYS`) — a store with this many or more MISSING-PHONE CANDIDATE devices is
# worth an admin's attention. Small/obvious enough not to warrant a new settings table + admin UI in
# this package (RULE TWO finding, not fixed here — flagged for a future config-table pass alongside
# the other asset settings-audit findings in docs/handoffs/asset.md, using `po_settings` as the
# template for how to do it right).
MISSING_PHONE_STORE_THRESHOLD = 3


def sb():
    return get_supabase()


def _is_missing_schema_error(exc: Exception) -> bool:
    s = str(exc)
    return any(m in s for m in _MISSING_SCHEMA_MARKERS)


def _fl(v):
    try:
        return round(float(v), 2)
    except Exception:
        return None


def _call_recon_rpc(client, org_id: str, store: str, market: str, date_from: str, date_to: str):
    """Calls commcalc.asset_oninv_3way_recon (mig 310). Returns (rows, migrated:bool)."""
    stores = _store_list(store)
    p_market, p_no_market_only = resolve_market_for_rpc(market)
    params = {
        "p_org_id": org_id,
        "p_stores": stores or None,
        "p_market": p_market,
        "p_no_market_only": p_no_market_only,
        "p_date_from": date_from or None,
        "p_date_to": date_to or None,
    }
    try:
        res = client.schema("commcalc").rpc("asset_oninv_3way_recon", params).execute()
        return (res.data or []), True
    except Exception as e:
        if _is_missing_schema_error(e):
            return [], False
        raise


_CLASSIFICATIONS = ("missing_phone_candidate", "non_activated", "conflict", "unmatchable")


def _blank_class_counts():
    return {c: {"count": 0, "exposure": 0.0} for c in _CLASSIFICATIONS}


def _shape_row(r: dict) -> dict:
    return {
        "store": r.get("store"),
        "market": r.get("market"),
        "esn_imei": r.get("esn_imei"),
        "device_model": r.get("device_model"),
        "acquired_date": r.get("acquired_date"),
        "aging_days": r.get("aging_days"),
        "device_value": _fl(r.get("device_value")) or 0.0,
        "classification": r.get("classification"),
        "leg2": {
            "paid": bool(r.get("leg2_paid")),
            "amount": _fl(r.get("leg2_amount")),
            "date": r.get("leg2_date"),
        },
        "leg3": {
            "status": r.get("leg3_status"),   # 'paid' | 'not_paid' | 'na'
            "amount": _fl(r.get("leg3_amount")),
            "last_date": r.get("leg3_last_date"),
            "payment_count": int(r.get("leg3_payment_count") or 0),
            "payment_types": r.get("leg3_payment_types"),
        },
    }


@router.get("/oninv-3way-recon")
async def get_oninv_3way_recon(
    org_id: str = ORG_ID,
    store: str = "",          # comma-separated multi-select
    market: str = "",         # single value; NO_MARKET_SENTINEL selects the "(no market)" bucket
    date_from: str = "",      # acquired_date >=
    date_to: str = "",        # acquired_date <=
):
    """The 3-way On-Inventory x IMEI-Rebate x Commission-Paid recon (OWNER DIRECTIVE 2026-07-28).
    Per-store summary counts + $ exposure per classification, per-IMEI rows, and grand totals. See
    this module's docstring for exactly what each of the three legs is and how classification works.
    Read-only — never writes to asset_ledger, flags, or investigation rows."""
    client = sb()
    raw_rows, migrated = _call_recon_rpc(client, org_id, store, market, date_from, date_to)
    if not migrated:
        return {"migrated": False, "message": _MIGRATION_MSG, "rows": [], "stores": [],
                "totals": _blank_class_counts(), "as_of": date.today().isoformat()}

    rows = [_shape_row(r) for r in raw_rows]

    by_store: dict = {}
    totals = _blank_class_counts()
    for r in rows:
        s = r["store"] or "(unknown)"
        d = by_store.setdefault(s, {
            "store": s, "market": r["market"],
            "classes": _blank_class_counts(), "device_count": 0, "total_exposure": 0.0,
        })
        if not d["market"] and r["market"]:
            d["market"] = r["market"]
        c = r["classification"] if r["classification"] in _CLASSIFICATIONS else "unmatchable"
        d["classes"][c]["count"] += 1
        d["classes"][c]["exposure"] = round(d["classes"][c]["exposure"] + r["device_value"], 2)
        d["device_count"] += 1
        d["total_exposure"] = round(d["total_exposure"] + r["device_value"], 2)
        totals[c]["count"] += 1
        totals[c]["exposure"] = round(totals[c]["exposure"] + r["device_value"], 2)

    stores_out = sorted(by_store.values(), key=lambda x: -x["classes"]["missing_phone_candidate"]["exposure"])
    grand_total_devices = sum(v["count"] for v in totals.values())
    grand_total_exposure = round(sum(v["exposure"] for v in totals.values()), 2)

    return {
        "migrated": True,
        "as_of": date.today().isoformat(),
        "rows": rows,
        "stores": stores_out,
        "totals": totals,
        "grand_total_devices": grand_total_devices,
        "grand_total_exposure": grand_total_exposure,
        "device_value_column": "owed_to_vip",
    }


def _p_asset_oninv_missing_phones(client, org_id, ctx):
    """Admin-attention provider (self-registered below, group='other' per dispatch, cost='heavy' —
    it runs the real 3-way join, so it's deferred unless the caller passes deep=1, same as the
    other join-heavy checks in core/import_health.py e.g. product_mrc/plan_coverage). Fires ONE
    item per store whose MISSING-PHONE CANDIDATE count meets MISSING_PHONE_STORE_THRESHOLD."""
    try:
        raw_rows, migrated = _call_recon_rpc(client, org_id, "", "", "", "")
    except Exception:
        return []
    if not migrated or not raw_rows:
        return []
    by_store: dict = {}
    for r in raw_rows:
        if r.get("classification") != "missing_phone_candidate":
            continue
        s = r.get("store") or "(unknown)"
        d = by_store.setdefault(s, {"count": 0, "exposure": 0.0})
        d["count"] += 1
        d["exposure"] += float(r.get("device_value") or 0)
    hot = {s: d for s, d in by_store.items() if d["count"] >= MISSING_PHONE_STORE_THRESHOLD}
    if not hot:
        return []
    top = sorted(hot.items(), key=lambda kv: -kv[1]["count"])[:5]
    total_count = sum(d["count"] for d in hot.values())
    total_exposure = round(sum(d["exposure"] for d in hot.values()), 2)
    examples = ", ".join(f"{s} ({d['count']})" for s, d in top)
    return [{
        "group": "other", "key": "asset_oninv_missing_phone_candidates", "severity": "warning",
        "label": "On-Inventory devices with a rebate paid but still showing in inventory",
        "detail": (f"{total_count} device(s) across {len(hot)} store(s) are still marked On-Inventory "
                   f"but a rebate/commission was paid on them (${total_exposure:,.2f} exposure) — "
                   f"the inventory record may be wrong or the phone left the store. Stores at or over "
                   f"{MISSING_PHONE_STORE_THRESHOLD}: {examples}. Open the On-Inventory 3-Way Recon "
                   f"report to investigate."),
        "count": total_count, "deep_link": "/commcalc/asset/oninv-3way-recon",
        "deep_link_label": "Open On-Inventory 3-Way Recon",
    }]


try:
    from app.modules.core.import_health import register_provider
except Exception:
    register_provider = None

if register_provider:
    register_provider("asset_oninv_missing_phone_candidates",
                      label="On-Inventory devices with a rebate paid elsewhere",
                      group="other", cost="heavy")(_p_asset_oninv_missing_phones)
