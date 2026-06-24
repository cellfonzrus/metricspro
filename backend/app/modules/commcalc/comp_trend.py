"""Residual / Comprehensive-Comp month-over-month trend.

Each month's `raw_comp_report` is a frozen snapshot: the daily sweep REPLACES the open month with
the carrier's cumulative month-to-date pull, and a new month is a new `period` (see epay_sweep.py).
This compares consecutive months' residual ($ payment_amount) per account to surface DIPS — an
account whose residual fell or disappeared (a likely cancellation / deactivation) — so you can see
WHICH MONTH a residual dropped and WHY.

Self-contained (paginated read + Python aggregation). Comp is ~10-14k rows/month today; if it grows
large, push the per-(account,period) aggregation into a Postgres RPC per the perf guidance.
"""
from app.modules.commcalc.calculator import parse_period, safe_float
from app.modules.commcalc import carrier_map

_COMPS = ("RESIDUAL", "COMMISSION", "SPIFF", "REIMBURSEMENT", "UNMAPPED")


def _pkey(period):
    """Sortable (year, month) for a 'June 2026' period label."""
    p = parse_period(period or "")
    return (p["year"], p["month"])


def _fetch_comp(client, org_id):
    """All comp rows (projected to the columns we trend), paginated past the REST 1000-row cap."""
    sel = ("period,account_id,owner_id,terminal_id,business_name,business_address,"
           "compensation_type,brand,payment_amount,quantity,has_payment_detail")
    out, start, page = [], 0, 1000
    while True:
        resp = (client.schema("commcalc").table("raw_comp_report").select(sel)
                .eq("org_id", org_id).range(start, start + page - 1).execute())
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return out


def _acct_key(r):
    """Stable identity for an account across months. Prefer AccountID; fall back to a
    business+terminal composite so rows missing AccountID still trend together."""
    aid = (r.get("account_id") or "").strip()
    if aid:
        return aid
    return "b:" + "|".join((str(r.get(k) or "").strip() for k in
                            ("business_address", "business_name", "terminal_id")))


def _mi_atu_by_period(client, org_id, periods):
    """TRUE RESIDUAL per period = Σ(actual_mi_payout + actual_atu_payout) from raw_mi.

    The Comprehensive Comp report this module trends is ~95% one-time promo/bounty COMPENSATION, not
    residual (see docs/SAAS_FRAMEWORK.md canonical model). Residual — recurring per-subscriber income
    — is MI + ATU. We surface it alongside the comp total so the report stops mislabeling comp.

    Aggregated in Postgres via the `mi_atu_by_period` RPC — raw_mi is ~38k rows/MONTH, so summing in
    Python (paginated) made this endpoint take 30s. Returns {} if the RPC isn't present yet (the page
    stays fast; residual_mi_atu shows 0 until commcalc.mi_atu_by_period is created — see migration)."""
    if not periods:
        return {}
    try:
        rows = client.schema("commcalc").rpc(
            "mi_atu_by_period", {"p_org_id": org_id, "p_periods": periods}).execute().data or []
        return {r["period"]: safe_float(r.get("residual_mi_atu"))
                for r in rows if r.get("period")}
    except Exception:
        return {}  # RPC not created yet — keep the trend fast, residual lights up once it exists


def compute_residual_trend(client, org_id, months=6, store="", market="",
                           min_drop_pct=20.0, min_drop_amt=1.0):
    rows = _fetch_comp(client, org_id)
    store_q = (store or "").strip().lower()
    try:
        rules = carrier_map.load_rules(client, org_id)  # canonical component classification (migration 038)
    except Exception:
        rules = []

    totals = {}      # period -> {"residual","qty","accounts":set(),"components":{...}}
    acct = {}        # acct_key -> {"name","store","addr", periods:{period:{"residual","qty"}}}
    for r in rows:
        addr = (r.get("business_address") or "").strip()
        name = (r.get("business_name") or "").strip()
        if store_q and store_q not in addr.lower() and store_q not in name.lower():
            continue
        period = (r.get("period") or "").strip()
        if not period:
            continue
        amt = safe_float(r.get("payment_amount"))
        qty = safe_float(r.get("quantity"))
        k = _acct_key(r)

        t = totals.setdefault(period, {"residual": 0.0, "qty": 0.0, "accounts": set(),
                                       "components": {c: 0.0 for c in _COMPS}})
        t["residual"] += amt
        t["qty"] += qty
        t["accounts"].add(k)
        if rules:
            m = carrier_map.match_rule(rules, r.get("compensation_type"))
            t["components"][m["component"] if (m and m.get("component") in _COMPS) else "UNMAPPED"] += amt

        a = acct.setdefault(k, {"name": name, "store": addr or name, "periods": {}})
        if name and not a["name"]:
            a["name"] = name
        if addr and (not a["store"]):
            a["store"] = addr
        pp = a["periods"].setdefault(period, {"residual": 0.0, "qty": 0.0})
        pp["residual"] += amt
        pp["qty"] += qty

    # order periods chronologically, keep the most recent `months`
    ordered = sorted(totals.keys(), key=_pkey)
    kept = ordered[-months:] if months and months > 0 else ordered
    kept_set = set(kept)
    mi_atu = _mi_atu_by_period(client, org_id, kept)  # true residual (MI+ATU) per period

    totals_by_month = []
    prev_total = None
    for p in kept:
        t = totals[p]
        delta = None if prev_total is None else round(t["residual"] - prev_total, 2)
        pct = None
        if prev_total not in (None, 0):
            pct = round((t["residual"] - prev_total) / abs(prev_total) * 100, 1)
        comp_total = round(t["residual"], 2)
        totals_by_month.append({
            "period": p,
            # `residual` (legacy key) is actually TOTAL CARRIER COMPENSATION (promo + bounty +
            # reimbursement). `total_comp` is the clear alias; `residual_mi_atu` is the real residual.
            "residual": comp_total,
            "total_comp": comp_total,
            "residual_mi_atu": round(mi_atu.get(p, 0.0), 2),
            "accounts": len(t["accounts"]),
            "qty": round(t["qty"], 1),
            "delta_vs_prev": delta,
            "pct_vs_prev": pct,
            "components": {c: round(t["components"][c], 2) for c in _COMPS},
        })
        prev_total = t["residual"]

    # dips: for every consecutive kept-month pair, flag accounts whose residual fell materially.
    # Labeled by the LATER month (the month the residual dipped), so you can see which month + why.
    dips = []
    for i in range(1, len(kept)):
        prev_p, cur_p = kept[i - 1], kept[i]
        for k, a in acct.items():
            prev = a["periods"].get(prev_p, {}).get("residual", 0.0)
            if prev <= min_drop_amt:
                continue
            cur = a["periods"].get(cur_p, {}).get("residual", 0.0)
            drop = prev - cur
            if drop < min_drop_amt:
                continue
            pct = round(drop / prev * 100, 1) if prev else 0.0
            vanished = cur_p not in a["periods"]
            if not vanished and pct < min_drop_pct:
                continue
            reason = ("Account dropped from the report — likely canceled / deactivated"
                      if vanished else
                      f"Residual reduced {pct:.0f}% — fewer active lines or rate change")
            dips.append({
                "period": cur_p, "prev_period": prev_p,
                "account_id": k if not k.startswith("b:") else "",
                "business_name": a["name"], "store": a["store"],
                "prev_residual": round(prev, 2), "residual": round(cur, 2),
                "delta": round(-drop, 2), "pct": pct, "vanished": vanished,
                "prev_qty": round(a["periods"].get(prev_p, {}).get("qty", 0.0), 1),
                "cur_qty": round(a["periods"].get(cur_p, {}).get("qty", 0.0), 1),
                "reason": reason,
            })
    dips.sort(key=lambda d: d["delta"])  # most negative (biggest drop) first

    return {
        "months": kept,
        "totals_by_month": totals_by_month,
        "dips": dips,
        "dip_count": len(dips),
        "params": {"months": months, "store": store, "market": market,
                   "min_drop_pct": min_drop_pct, "min_drop_amt": min_drop_amt},
        "note": (None if kept else
                 "No Comprehensive Comp data yet — carrier comp posts in arrears; "
                 "the trend populates once two or more months are loaded."),
    }
