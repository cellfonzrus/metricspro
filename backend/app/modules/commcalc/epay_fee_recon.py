"""ePay (Boost) FEE reconciliation (owner directive 2026-08-20).

The Boost "ePay service charge" is a per-transaction fee the store collects. It is captured in TWO places
that must agree, and a third-party (the owner's portal) is the authority:

  • OUR SYSTEM  — raw_sales lines whose product_desc is "epay service charge" (what the register rang and
                  our sales feed captured). Summed per store-day.
  • THE PORTAL  — the Boost Daily Transaction Detail FEE lines (raw_epay_daily_tx, is_fee=true). The
                  authoritative fee count. (epay_ingest.per_store_day → 'fee'.)

This module reconciles the two per store-day. A discrepancy means fees rang on the register aren't
reaching the portal (or vice-versa) — surfaced on the fee-recon report and, hourly, alerted to DM+ (P4).

Store keys: raw_sales carries a raw `store` string; we resolve it to OUR store_code via
commcalc.store_mapping (the same Store Matching every other raw_sales consumer uses), so both sides key
by store_code. Pure aggregation is factored out for the harness; DB reads degrade to empty, never raise.
"""

FEE_DESC = "epay service charge"   # raw_sales.product_desc marker for the Boost fee line


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm(s):
    return str(s or "").strip().upper()


def is_fee_desc(product_desc):
    return FEE_DESC in str(product_desc or "").strip().lower()


def aggregate_system_fee(sales_rows, store_resolver):
    """{(store_code, trans_date): fee$} from raw_sales rows, fee lines only, store resolved to store_code.
    `store_resolver(raw_store_string) -> store_code`. Voided lines are excluded. Pure."""
    out = {}
    for r in sales_rows or []:
        if not is_fee_desc(r.get("product_desc")):
            continue
        if str(r.get("voided") or "").strip().lower() in ("true", "1", "yes", "voided", "y"):
            continue
        d = str(r.get("trans_date") or "")[:10]
        code = store_resolver(r.get("store"))
        if not (code and d):
            continue
        out[(code, d)] = round(out.get((code, d), 0.0) + _f(r.get("ext_price")), 2)
    return out


def build_recon(system_by_sd, portal_by_sd, tolerance=1.0):
    """Join system fee (per store-day) with portal fee (per store-day) into recon rows. `portal_by_sd` is
    epay_ingest.per_store_day's map ({(store,date): {'fee':...}}). Returns rows sorted by |variance| desc —
    the biggest discrepancies first. Pure."""
    keys = set(system_by_sd) | set(portal_by_sd)
    rows = []
    for (code, d) in keys:
        sysf = _f(system_by_sd.get((code, d)))
        por = portal_by_sd.get((code, d))
        porf = _f(por.get("fee")) if isinstance(por, dict) else _f(por)
        var = round(sysf - porf, 2)
        rows.append({
            "store_code": code, "close_date": d,
            "system_fee": sysf, "portal_fee": porf, "var": var,
            "in_system": (code, d) in system_by_sd, "in_portal": (code, d) in portal_by_sd,
            "shortage": var < -tolerance,   # portal has MORE fee than our system captured
            "overage": var > tolerance,     # our system rang MORE fee than the portal shows
            "flag": abs(var) > tolerance,
        })
    rows.sort(key=lambda x: -abs(x["var"]))
    return rows


# ── DB-backed orchestration ────────────────────────────────────────────────────────────────────────
def _store_resolver(client, org_id):
    """raw_store_string -> store_code via commcalc.store_mapping (address/name -> code). Falls back to the
    normalized raw string when unmapped, so an unmatched store still reconciles against itself rather than
    vanishing. Returns a closure."""
    amap = {}
    try:
        rows = (client.schema("commcalc").table("store_mapping")
                .select("store_address,location_name,store_code").eq("org_id", org_id)
                .execute().data) or []
    except Exception:
        rows = []
    for r in rows:
        code = r.get("store_code")
        if not code:
            continue
        for k in (r.get("store_address"), r.get("location_name"), code):
            if k:
                amap[_norm(k)] = code

    def resolve(raw_store):
        n = _norm(raw_store)
        return amap.get(n, n or None)
    return resolve


def system_fee_per_store_day(client, org_id, date_from, date_to, store_codes=None):
    """{(store_code, date): fee$} from raw_sales 'epay service charge' lines in range."""
    try:
        rows = (client.schema("commcalc").table("raw_sales")
                .select("store,trans_date,product_desc,ext_price,voided")
                .eq("org_id", org_id).gte("trans_date", date_from).lte("trans_date", date_to)
                .limit(500000).execute().data) or []
    except Exception:
        return {}
    agg = aggregate_system_fee(rows, _store_resolver(client, org_id))
    if store_codes:
        keep = set(store_codes)
        agg = {k: v for k, v in agg.items() if k[0] in keep}
    return agg


def fee_recon(client, org_id, date_from, date_to, store_codes=None, tolerance=1.0):
    """The full fee reconciliation over a range: system (raw_sales) vs portal (DTD) fee per store-day."""
    from app.modules.commcalc import epay_ingest as _epay
    system = system_fee_per_store_day(client, org_id, date_from, date_to, store_codes=store_codes)
    portal = _epay.per_store_day(client, org_id, date_from, date_to, store_codes=store_codes)
    rows = build_recon(system, portal, tolerance=tolerance)
    return {
        "rows": rows,
        "totals": {"system_fee": round(sum(r["system_fee"] for r in rows), 2),
                   "portal_fee": round(sum(r["portal_fee"] for r in rows), 2),
                   "var": round(sum(r["var"] for r in rows), 2),
                   "flagged": sum(1 for r in rows if r["flag"]), "store_days": len(rows)},
    }
