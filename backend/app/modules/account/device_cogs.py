"""Device / handset COGS — INVOICE-FIRST recognition (owner policy 2026-07-30 §9, ruling K3 2026-08-10).

WHY THIS MODULE EXISTS
----------------------
`coa.build_inputs` derived device cost from the POS only, as sale-line `ext_price − gp`. On a
subsidised handset that is not merely imprecise, it is the wrong sign: measured on luxelink July 2026,
`raw_sales` dept `BrandedHandset` / cat `KittedBranded` gives ext 23,289.18 − gp 25,625.51 =
**−$2,336.33**, because B2B Soft records the POST-SUBSIDY cost. So the phones a dealer buys were
absent from the books entirely (luxelink July `device_cost` = $234.00, all of it SIM kits) while the
distributor's rebate sat in income with nothing to offset.

The policy of record already resolved how to fix it and the flip was HELD as "Option C". Owner ruling
K3 released it. This module is that flip, isolated so the recognition rule is testable on its own.

THE RULE (policy §9 C1/C2/C3)
----------------------------
1. **Invoice-first.** Cost comes from the distributor invoice whenever one is in the system.
2. **Sale-time fallback.** POS `ext − gp` ONLY when no invoice covers the period.
3. **Dedup by IMEI.** One physical handset is charged to COGS exactly once.
4. **Consignment (VIP).** The amount VIP billed IS the consignment COGS; `owed_to_vip` stays a
   liability until the unit sells (that treatment is untouched here).
5. **Periodic inventory.** Period-end unsold units are a balance-sheet asset, not COGS.

CARRIER-AGNOSTIC BY DATA, NEVER BY TENANT NAME
---------------------------------------------
Same discipline as `coa`'s residual fallback: each source is tried, and whichever the org actually HAS
is the one that answers. A VidaPay tenant has `raw_ma_*`; a VIP/Boost tenant has
`asset_ledger`/`vip_invoices`. No branch anywhere reads a tenant name, and the whole module is gated by
`account_config.device_cogs_mode` (mig 621) which DEFAULTS TO 'off' — so every org is byte-identical
until a tenant is explicitly switched on.

⚠️ THE UN-LINKABLE REMAINDER IS COUNTED, NEVER ASSUMED ZERO
-----------------------------------------------------------
Policy §9 C1 carries an explicit caveat: the owner's "IMEI means never duplicate" premise fails for
rows that cannot be linked. `raw_ma_fulfillment` has **no IMEI column** — it prices by product name and
reaches an IMEI only through an activation. So every return carries `meta` reporting exactly what could
NOT be priced. luxelink July 2026, measured: 787 activation rows → **746 distinct IMEIs** (41 duplicate
rows dropped — verified same SKU, same date, i.e. a line/AAL pair that would otherwise charge one
handset twice) → **599 priced = $137,185.10**, with **147 IMEIs unpriced** (mostly
`sku='Product Not Available'`, which are SIM-only/BYOD activations carrying $0 rebate, not handsets).

📌 $137,185.10 is the IMEI-DEDUPED figure and is the policy-correct one. The $142,033.93 quoted in the
formula book §C was measured WITHOUT dedup (all 787 rows) and is $4,848.83 too high. Dedup is a policy
requirement, so the lower figure governs; see the handoff for the net-income consequence.

GRAIN — READ THIS BEFORE TRUSTING A PER-STORE DEVICE MARGIN
-----------------------------------------------------------
`raw_ma_commission` carries `merchant_account_id`, **not a store address**. MA device COGS was
therefore booked **company-wide**, exactly like PayGo and the MA residual — resolving an account id
as a store by GUESSING is the phantom-store failure PayGo taught us. The condition this paragraph
set ("until account_id → store_address is mapped") is now MET where the data provides it: the
dealer's own `raw_ma_fulfillment` sheet carries both `tspid` (the account) and `business_address`
on every order row, and mig 314 adds an owner-pinned override table
(`ma_account_store_map`). When the caller passes that index (`ma_acct_index`, built by
`ma_store_pnl.load_store_index` and gated by `commission_org_config.pl_ma_store_attribution`),
MA device cost books per store; any account the index cannot name STAYS company-wide — mapped
beats guessed, honest beats mis-attributed. No index passed ⇒ company-wide, byte-identical to
the pre-314 behaviour. (Owner spec 2026-09-02: "rebates and phone cost are not being captured
per store".)
"""
import logging

from app.modules.commcalc.calculator import safe_float

_log = logging.getLogger(__name__)

# Which `raw_ma_fulfillment.order_type` values are HANDSETS/devices whose price is a device cost.
# Measured on luxelink: 'Branded Handset' is 401 of 404 rows / $290,854.59 of $290,940.21; the other
# three rows are a SIM kit and two Alphacomm lines, which are not devices. Kept as a module constant
# rather than a config knob because it is a VENDOR vocabulary (the distributor's own order-type
# taxonomy), not a tenant policy — the same reasoning that keeps `source_key` a code table in `coa`.
_MA_DEVICE_ORDER_TYPES = {"branded handset"}

# A `sku` the distributor could not name. These rows are SIM-only / BYOD activations, not handsets:
# on luxelink July all 160 carry $0 rebate. Excluded from the device count AND reported in `meta`.
_MA_UNKNOWN_SKU = "product not available"


def _page(client, table, select, eqs=None, page=1000, cap=200000):
    """Paginated org-scoped select. Mirrors `coa._fetch_all` (supabase caps a query at 1000 rows).
    A list/tuple/set filter value becomes an IN (...) clause — used for the period-spelling duality."""
    out, start = [], 0
    while start < cap:
        q = client.schema("commcalc").table(table).select(select)
        for k, v in (eqs or {}).items():
            q = q.in_(k, list(v)) if isinstance(v, (list, tuple, set)) else q.eq(k, v)
        rows = (q.range(start, start + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def ma_unit_price_map(client, org_id):
    """{product_name(lower) -> unit cost} from the distributor's handset fulfillment sheet.

    ALL dates, deliberately: a device ordered in June and activated in July must still be priced, and
    the sheet is a price list as much as a purchase log. Unit = Σ(price × qty) / Σ(qty), which is a
    weighted average when the same model was bought at two prices. Measured spot-checks on luxelink:
    iPhone 16e $599.99, Galaxy Tab A11+ $279.99, A16 $129.99, TCL Tab 10 $219.99 — and the VidaPay
    wallet charges (`raw_ma_daily_tx` 'Postpaid Branded MarketPlace') are at EXACTLY those prices, so
    the two independent sources agree and either could have sourced this.
    """
    qty, ext = {}, {}
    for r in _page(client, "raw_ma_fulfillment",
                   "product_name,number_ordered,price,order_type", {"org_id": org_id}):
        if (r.get("order_type") or "").strip().lower() not in _MA_DEVICE_ORDER_TYPES:
            continue
        name = (r.get("product_name") or "").strip().lower()
        if not name:
            continue
        n = safe_float(r.get("number_ordered")) or 0.0
        p = safe_float(r.get("price")) or 0.0
        if n <= 0 or p <= 0:
            continue
        qty[name] = qty.get(name, 0.0) + n
        ext[name] = ext.get(name, 0.0) + (p * n)
    return {k: round(ext[k] / qty[k], 2) for k in qty if qty[k]}


def _ma_sold_cost(client, org_id, period_keys, ma_acct_index=None):
    """Cost of the devices ACTIVATED (= sold) in the period, from the distributor's own records.

    `raw_ma_commission` is one row per activated line and carries the `imei` and the `sku`. Dedup by
    IMEI first (a line/AAL pair repeats the same handset), then price each distinct IMEI off the
    fulfillment sheet. Grain: per store where `ma_acct_index` names the row's merchant account
    (mig 314 — see the module docstring), company-wide for everything else. No index ⇒ all
    company-wide, byte-identical to pre-314.
    """
    price = ma_unit_price_map(client, org_id)
    rows = _page(client, "raw_ma_commission", "imei,sku,activation_type,merchant_account_id",
                 {"org_id": org_id, "period": period_keys})
    idx = ma_acct_index or {}
    by_imei = {}
    no_imei = 0
    for r in rows:
        imei = (r.get("imei") or "").strip()
        if not imei:
            no_imei += 1
            continue
        # first row wins; a repeat of the same IMEI is the SAME physical handset
        by_imei.setdefault(imei, ((r.get("sku") or "").strip(),
                                  str(r.get("merchant_account_id") or "").strip()))
    cost, priced, unknown_sku, unpriced = 0.0, 0, 0, 0
    detail, by_store = {}, {}
    for imei, (sku, acct) in by_imei.items():
        low = sku.lower()
        if not sku or low == _MA_UNKNOWN_SKU:
            unknown_sku += 1
            continue
        unit = price.get(low)
        if unit is None:
            unpriced += 1
            continue
        cost += unit
        priced += 1
        detail[sku] = round(detail.get(sku, 0.0) + unit, 2)
        addr = idx.get(acct)
        if addr:
            by_store[addr] = round(by_store.get(addr, 0.0) + unit, 2)
    return {
        "cost": round(cost, 2),
        "detail": detail,
        # per-store slice of `cost` (the remainder is company-wide); {} without an index
        "by_store": by_store,
        "meta": {"source": "raw_ma_commission x raw_ma_fulfillment",
                 "rows": len(rows), "distinct_imei": len(by_imei), "dedup_dropped": len(rows) - len(by_imei),
                 "priced": priced, "unknown_sku": unknown_sku, "unpriced_sku": unpriced,
                 "rows_without_imei": no_imei, "price_list_skus": len(price),
                 "store_mapped_accounts": len(idx)},
    }


def _vip_sold_cost(client, org_id, pm, py, in_period, resolve_store):
    """Consignment COGS for VIP/Boost: the amount VIP BILLED for each unit, recognised when the unit
    SOLD (policy §9 C2). Per-store, because `asset_ledger` carries a store.

    Dedup by `esn_imei` for the same reason as the MA path. Fee-only categories are excluded — those
    are already booked on `vip_fees` by `coa` and are not device cost.
    """
    _FEE_CATS = {"PROCESSING FEE", "SHIPPING", "SIM KIT"}
    rows = _page(client, "asset_ledger",
                 "esn_imei,store,category,owed_to_vip,date_sold", {"org_id": org_id})
    seen, by_store, detail = set(), {}, {}
    sold, dup = 0, 0
    for r in rows:
        cat = (r.get("category") or "").strip().upper()
        if cat in _FEE_CATS:
            continue
        if not in_period(r.get("date_sold"), pm, py):
            continue
        imei = (r.get("esn_imei") or "").strip()
        if imei:
            if imei in seen:
                dup += 1
                continue
            seen.add(imei)
        owed = safe_float(r.get("owed_to_vip"))
        if not owed:
            continue
        st = resolve_store(r.get("store")) if r.get("store") else None
        key = st or "(company-wide)"
        by_store[key] = round(by_store.get(key, 0.0) + owed, 2)
        label = (r.get("category") or "Consignment device").strip()
        detail[label] = round(detail.get(label, 0.0) + owed, 2)
        sold += 1
    total = round(sum(by_store.values()), 2)
    return {
        "cost": total,
        "by_store": by_store,
        "detail": detail,
        "meta": {"source": "asset_ledger (consignment, billed=COGS)",
                 "rows": len(rows), "sold_in_period": sold, "dedup_dropped": dup},
    }


def resolve(client, org_id, period_keys, pm, py, in_period, resolve_store, mode,
            ma_acct_index=None):
    """The one entry point `coa.build_inputs` calls.

    Returns a dict:
      {"active": bool,          # did an INVOICE source answer? (False ⇒ caller keeps POS cost)
       "company_wide": float,   # MA device cost the account→store index could not place
       "by_store": {store: amt},# VIP consignment cost + mig-314 store-mapped MA cost
       "detail": {label: amt},
       "meta": {...}}           # always populated, including the un-linkable remainder

    `mode` comes from `account_config.device_cogs_mode` (mig 621):
      'off' / 'pos'  → returns active=False immediately; caller books POS cost. PRE-621 BEHAVIOUR.
      'auto'         → invoice-first; caller falls back to POS only if no invoice source answered.
      'invoice'      → invoice only; caller NEVER books POS cost (correct where POS cost is negative).

    NEVER RAISES. Any failure degrades to active=False, i.e. exactly the pre-621 POS behaviour, with
    the reason recorded in `meta` so a silent $0 is never mistaken for a real zero.
    """
    out = {"active": False, "company_wide": 0.0, "by_store": {}, "detail": {},
           "meta": {"mode": mode}}
    if mode not in ("auto", "invoice"):
        out["meta"]["skipped"] = "mode=%s (POS recognition)" % mode
        return out

    ma, vip = None, None
    try:
        ma = _ma_sold_cost(client, org_id, period_keys, ma_acct_index=ma_acct_index)
    except Exception as e:
        _log.warning("device_cogs: MA/VidaPay invoice source unavailable — %s: %s", type(e).__name__, e)
        out["meta"]["ma_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        vip = _vip_sold_cost(client, org_id, pm, py, in_period, resolve_store)
    except Exception as e:
        _log.warning("device_cogs: VIP consignment source unavailable — %s: %s", type(e).__name__, e)
        out["meta"]["vip_error"] = "%s: %s" % (type(e).__name__, e)

    if ma and ma["cost"]:
        # per-store slice first (mig 314, only when an account→store index was passed);
        # whatever the index could not name stays company-wide — total unchanged either way.
        _ma_store = ma.get("by_store") or {}
        _store_total = round(sum(_ma_store.values()), 2)
        for st, amt in _ma_store.items():
            out["by_store"][st] = round(out["by_store"].get(st, 0.0) + amt, 2)
        out["company_wide"] = round(ma["cost"] - _store_total, 2)
        for k, v in ma["detail"].items():
            out["detail"][k] = round(out["detail"].get(k, 0.0) + v, 2)
        out["meta"]["ma"] = ma["meta"]
        out["active"] = True
    elif ma:
        out["meta"]["ma"] = ma["meta"]

    if vip and vip["cost"]:
        for st, amt in vip["by_store"].items():
            if st == "(company-wide)":
                out["company_wide"] = round(out["company_wide"] + amt, 2)
            else:
                out["by_store"][st] = round(out["by_store"].get(st, 0.0) + amt, 2)
        for k, v in vip["detail"].items():
            out["detail"][k] = round(out["detail"].get(k, 0.0) + v, 2)
        out["meta"]["vip"] = vip["meta"]
        out["active"] = True
    elif vip:
        out["meta"]["vip"] = vip["meta"]

    # 'invoice' mode suppresses the POS fallback even when no invoice answered — the caller must not
    # book a NEGATIVE POS device cost just because the distributor sheet is missing for a month. That
    # is the honest-zero-with-reason ruling K3(b) requires for the pre-`raw_ma_commission` months.
    if mode == "invoice" and not out["active"]:
        out["active"] = True
        out["meta"]["honest_zero"] = (
            "mode=invoice and no invoice source produced a cost for this period — device COGS is $0.00 "
            "BY DECLARATION, not by measurement. The POS fallback is deliberately NOT used because POS "
            "cost on a subsidised handset is negative.")
    return out
