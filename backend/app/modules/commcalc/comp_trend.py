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


def _mi_atu_by_period(client, org_id, periods, store_q=""):
    """TRUE RESIDUAL per period = Σ(actual_mi_payout + actual_atu_payout) from raw_mi.

    The Comprehensive Comp report this module trends is ~95% one-time promo/bounty COMPENSATION, not
    residual (see docs/SAAS_FRAMEWORK.md canonical model). Residual — recurring per-subscriber income
    — is MI + ATU. We surface it alongside the comp total so the report stops mislabeling comp.

    Aggregated in Postgres via the `mi_atu_by_period` RPC — raw_mi is ~38k rows/MONTH, so summing in
    Python (paginated) made this endpoint take 30s. Returns {} if the RPC isn't present yet (the page
    stays fast; residual_mi_atu shows 0 until commcalc.mi_atu_by_period is created — see migration).

    Returns ({period: residual}, basis) where `basis` is 'company' or 'store_filtered' — stated,
    never implied, so the screen can say which it is reading."""
    if not periods:
        return {}, "company"
    store_q = str(store_q or "").strip().lower()
    if store_q:
        per_store, basis = _mi_atu_store_filtered(client, org_id, periods, store_q)
        if per_store is not None:
            return per_store, basis
    try:
        rows = client.schema("commcalc").rpc(
            "mi_atu_by_period", {"p_org_id": org_id, "p_periods": periods}).execute().data or []
        return {r["period"]: safe_float(r.get("residual_mi_atu"))
                for r in rows if r.get("period")}, "company"
    except Exception:
        return {}, "company"  # RPC not created yet — trend stays fast; residual lights up after it


def _mi_atu_store_filtered(client, org_id, periods, store_q):
    """The MI/ATU residual of the stores matching `store_q` — or (None, _) to fall back to the
    company figure, which is what happens whenever the org has NOT turned residual store attribution
    on (`commission_org_config.pl_mi_store_attribution`, mig 1033) or the door index is unreadable.
    Falling back is deliberate: a HALF-attributed per-store figure would understate a store's
    residual silently, where the company figure at least states what it is. NEVER raises.

    Both facts come from their one home — the door→store index and the per-store aggregation both
    live in `account.residual_subs`; nothing about raw_mi is re-derived here. The store test is the
    SAME case-insensitive substring this function's caller applies to the comp rows' address, so one
    filter string means one thing on both columns of the response."""
    try:
        from app.modules.account import ma_store_pnl as _msp
        from app.modules.account import residual_subs as _rs
        if not bool((_msp.load_config(client, org_id) or {}).get("mi_store_attribution")):
            return None, "company"
        index = _rs.canonical_salesforce_store_index(client, org_id) or {}
        if not index:
            return None, "company"
        rows = _rs.mi_atu_by_period_store(client, org_id, list(periods)) or []
        if not rows:
            return None, "company"
        out = {p: 0.0 for p in periods}
        for r in rows:
            addr = index.get(str(r.get("salesforce_id") or "").strip())
            if not addr or store_q not in addr.lower():
                continue
            p = str(r.get("period") or "").strip()
            if p in out:
                out[p] += safe_float(r.get("mi")) + safe_float(r.get("atu"))
        return out, "store_filtered"
    except Exception as e:                          # pragma: no cover - I/O guard
        print(f"WARN comp_trend store-filtered residual unavailable: {e}")
        return None, "company"


def compute_rep_pay_trend(client, org_id, months=6, store=""):
    """Per-REP commission trend = Σ rep_commissions.total_payout per (rep, period).

    This is the commission WE ACTUALLY PAY the rep (the spiff stack × KPI tier from the calculator),
    NOT the account-level carrier compensation in compute_residual_trend. It answers "the commission
    being paid to the sales rep" — the page's account-level comp view never had a rep dimension.
    Reps are keyed by storeops_name (fallback epay_salesperson)."""
    sel = "period,storeops_name,epay_salesperson,store,total_payout"
    rows, start, page = [], 0, 1000
    while True:
        resp = (client.schema("commcalc").table("rep_commissions").select(sel)
                .eq("org_id", org_id).range(start, start + page - 1).execute())
        chunk = resp.data or []
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page

    store_q = (store or "").strip().lower()
    periods = sorted({(r.get("period") or "").strip() for r in rows if r.get("period")}, key=_pkey)
    kept = periods[-months:] if months and months > 0 else periods
    kept_set = set(kept)

    reps = {}                                  # rep -> {"rep","store","by_period":{p:pay},"total","_ps"}
    totals_by_month = {p: 0.0 for p in kept}
    for r in rows:
        p = (r.get("period") or "").strip()
        if p not in kept_set:
            continue
        st = (r.get("store") or "").strip()
        if store_q and store_q not in st.lower():
            continue
        rep = (r.get("storeops_name") or r.get("epay_salesperson") or "").strip() or "(unknown)"
        pay = safe_float(r.get("total_payout"))
        d = reps.setdefault(rep, {"rep": rep, "store": st, "by_period": {}, "total": 0.0, "_ps": {}})
        if st and not d["store"]:
            d["store"] = st
        d["by_period"][p] = d["by_period"].get(p, 0.0) + pay
        d["total"] += pay
        # Per-(period, store) pay so store/market attribution stays PER-MONTH — never collapsed to one
        # store for the whole window. A rep floats between stores (luxelink has 2-3 reps per store), so
        # a single top-level store misattributes every month's dollars under a store/market filter (FIX 4).
        d["_ps"][(p, st)] = d["_ps"].get((p, st), 0.0) + pay
        totals_by_month[p] += pay

    rep_list = sorted(reps.values(), key=lambda x: -x["total"])
    for d in rep_list:
        d["by_period"] = {p: round(v, 2) for p, v in d["by_period"].items()}
        d["total"] = round(d["total"], 2)
        d["latest"] = d["by_period"].get(kept[-1], 0.0) if kept else 0.0
        # Per-month attribution points (period × store × pay), chronological. The page filters + re-sums
        # THESE so a store/market filter counts only the months worked at that store/market (market is
        # stamped per point in router.py). Top-level `store`/`market` stay for backward-compat display.
        d["points"] = [{"period": pp, "store": ss, "pay": round(v, 2)}
                       for (pp, ss), v in sorted(d.pop("_ps").items(),
                                                 key=lambda kv: (_pkey(kv[0][0]), kv[0][1]))]
    return {
        "months": kept,
        "reps": rep_list,
        "totals_by_month": [{"period": p, "total_payout": round(totals_by_month[p], 2)} for p in kept],
        "note": (None if kept else
                 "No commission data yet — run a commission calculation for a period first."),
    }


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
    # TRUE residual (MI+ATU) per period. SIBLING FIX, mig 1033 (owner 2026-10-01 "assign the
    # residual at the store level in the p&l and all reports"): this figure was ALWAYS the whole
    # company's, even with a store filter applied — so the column sat beside store-filtered comp
    # totals on a different basis, the same "residual has no store" defect the P&L had. It now
    # resolves each dealer door through THE one home (`residual_subs.canonical_salesforce_store_index`)
    # and the SAME per-store aggregation the §7a report reads, under the SAME per-org config switch
    # (`pl_mi_store_attribution`) — so with the switch off, or with no filter applied, the figure is
    # byte-identical to before. `residual_mi_atu_basis` states which it is, on every response.
    mi_atu, mi_atu_basis = _mi_atu_by_period(client, org_id, kept, store_q)

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
            "residual_mi_atu_basis": mi_atu_basis,
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
