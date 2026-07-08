"""What-If / Scenario Analysis — reuses the LIVE commission, residual and sales model so
projections match the real engine. Three tools:
  1. activation_baseline  → per-period commission rates (payout_config) + baseline actuals
                            (rep_commissions). The projector math (Σ count×rate × tier) runs
                            client-side against these, mirroring calculator.py exactly.
  2. byod_residual        → residual (MI+ATU) trend + avg residual/sub + BYOD activation counts,
                            to model BYOD's contribution to recurring residual.
  3. accessory_byod_corr  → per store/period BYOD activations vs accessory revenue vs total
                            revenue, with Pearson correlations.
All read-only. No new SQL required (only existing tables/RPCs).
"""
import calendar
from app.modules.commcalc.calculator import classify_contract_type, safe_float
from app.modules.account import residual_subs

_MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}


def _pvariants(period: str):
    """Match both period spellings: 'June 2026' <-> '2026-06'."""
    p = (period or "").strip()
    out = {p}
    parts = p.split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        out.add(f"{parts[1]}-{_MONTHS[parts[0]]:02d}")
    elif len(p) >= 7 and p[:4].isdigit() and p[4] == "-":
        try:
            out.add(f"{calendar.month_name[int(p[5:7])]} {p[:4]}")
        except Exception:
            pass
    return list(out)


def _list_periods(client, org_id, limit=18):
    """Distinct month-name periods that actually have sales rows, newest first."""
    seen = {}
    for tbl in ("raw_sales", "daily_sales_feed"):
        try:
            rows = (client.schema("commcalc").table(tbl).select("period,period_year,period_month")
                    .eq("org_id", org_id).limit(200000).execute().data) or []
        except Exception:
            rows = []
        for r in rows:
            p = r.get("period")
            if not p:
                continue
            y, m = r.get("period_year"), r.get("period_month")
            key = (y or 0, m or 0, p)
            seen[p] = key
    ordered = sorted(seen.values(), reverse=True)
    return [p for (_y, _m, p) in ordered][:limit]


# ─── 1. Activation-mix commission projector ────────────────────────────────────────────────
_RATE_DEFAULTS = {
    "premium_flat": 5, "byod_flat": 3, "byod_extra_spiff": 0, "upgrade_flat": 20,
    "acc_rate": 0.10, "setup_fee_rate": 0.10, "trade_in_spiff": 20, "acima_spiff": 25,
    "tier_100_min_kpis": 7, "tier_75_min_kpis": 5, "tier_75_pct": 0.75, "tier_50_pct": 0.50,
    "straight_line": False,
}


def _rates(client, org_id, period):
    row = {}
    try:
        r = (client.schema("commcalc").table("payout_config").select("*")
             .eq("org_id", org_id).in_("period", _pvariants(period)).limit(1).execute().data) or []
        if r:
            row = r[0]
    except Exception:
        pass
    return {k: (dv if row.get(k) is None else row.get(k)) for k, dv in _RATE_DEFAULTS.items()}


def activation_baseline(client, org_id, period):
    rates = _rates(client, org_id, period)
    rc = (client.schema("commcalc").table("rep_commissions")
          .select("premium_acts,byod_acts,upgrade_acts,acc_comm,setup_fee_comm,trade_in_comm,"
                  "acima_comm,subtotal,total_payout,tier")
          .eq("org_id", org_id).in_("period", _pvariants(period)).limit(100000).execute().data) or []
    agg = {k: 0.0 for k in ("acc_comm", "setup_fee_comm", "trade_in_comm", "acima_comm", "subtotal", "total_payout")}
    cnt = {k: 0 for k in ("premium_acts", "byod_acts", "upgrade_acts")}
    tiers = []
    for r in rc:
        for k in cnt:
            cnt[k] += int(r.get(k) or 0)
        for k in agg:
            agg[k] += safe_float(r.get(k))
        if r.get("tier") is not None:
            tiers.append(safe_float(r.get("tier")))

    def _div(a, b):
        return round(a / b, 2) if b else 0.0

    actuals = {
        "premium_acts": cnt["premium_acts"], "byod_acts": cnt["byod_acts"], "upgrade_acts": cnt["upgrade_acts"],
        # inputs the projector edits — derived from paid comm ÷ its rate so "current" pre-fills to reality
        "acc_sales": _div(agg["acc_comm"], rates["acc_rate"]),
        "setup_sales": _div(agg["setup_fee_comm"], rates["setup_fee_rate"]),
        "trade_ins": round(_div(agg["trade_in_comm"], rates["trade_in_spiff"])),
        "acima_count": round(_div(agg["acima_comm"], rates["acima_spiff"])),
        "subtotal": round(agg["subtotal"], 2), "total_payout": round(agg["total_payout"], 2),
        "avg_tier": round(sum(tiers) / len(tiers), 3) if tiers else 1.0,
        "reps": len(rc),
    }
    return {"period": period, "rates": rates, "actuals": actuals, "periods": _list_periods(client, org_id)}


# ─── 2. BYOD → residual (MI + ATU) ─────────────────────────────────────────────────────────
def byod_residual(client, org_id, months=6):
    res = residual_subs.compute(client, org_id, months=months)
    company = res.get("company") or []
    byod_by_period = {}
    try:
        rc = (client.schema("commcalc").table("rep_commissions").select("period,byod_acts")
              .eq("org_id", org_id).limit(300000).execute().data) or []
    except Exception:
        rc = []
    for r in rc:
        p = r.get("period")
        if p:
            byod_by_period[p] = byod_by_period.get(p, 0) + int(r.get("byod_acts") or 0)

    def _byod_for(period):
        for v in _pvariants(period):
            if v in byod_by_period:
                return byod_by_period[v]
        return 0

    series = [{**c, "byod_acts": _byod_for(c["period"])} for c in company]
    tot_res = sum(c["residual"] for c in company)
    tot_subs = sum(c["subs"] for c in company)
    return {
        "months": res.get("months"), "series": series,
        "avg_residual_per_sub": round(tot_res / tot_subs, 2) if tot_subs else 0.0,
        "total_residual": round(tot_res, 2), "total_subs": tot_subs,
        "latest": series[-1] if series else None,
        # BYOD-specific: residual actually earned by subscribers whose activation was BYOD (MDN -> raw_mi).
        # Best-effort + bounded; None if the join can't run. Falls back to the blended avg in the UI.
        "byod_specific": _byod_specific_residual(client, org_id, company),
        "note": res.get("note"),
    }


def _norm_mdn(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _byod_mdns(client, org_id, periods):
    """Distinct normalized MDNs whose activation classified as BYOD, across the given sales periods."""
    out = set()
    for period in periods:
        for tbl in ("raw_sales", "daily_sales_feed"):
            page, got = 0, False
            while True:
                try:
                    chunk = (client.schema("commcalc").table(tbl)
                             .select("mdn,contract_type,voided,trans_type")
                             .eq("org_id", org_id).in_("period", _pvariants(period))
                             .range(page * 1000, page * 1000 + 999).execute().data) or []
                except Exception:
                    chunk = []
                if chunk:
                    got = True
                for r in chunk:
                    if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                        continue
                    if str(r.get("trans_type") or "").strip() == "Return":
                        continue
                    if classify_contract_type(r.get("contract_type")) == "byod":
                        m = _norm_mdn(r.get("mdn"))
                        if m:
                            out.add(m)
                if len(chunk) < 1000 or page > 60:
                    break
                page += 1
            if got:
                break  # raw_sales preferred; if it had rows, don't double from the feed
    return out


def _residual_by_mdn(client, org_id, period):
    """Sum (MI + ATU) per normalized MDN for ONE raw_mi period (bounded)."""
    per, page = {}, 0
    while True:
        try:
            chunk = (client.schema("commcalc").table("raw_mi")
                     .select("phone_number,actual_mi_payout,actual_atu_payout")
                     .eq("org_id", org_id).in_("period", _pvariants(period))
                     .range(page * 1000, page * 1000 + 999).execute().data) or []
        except Exception:
            chunk = []
        for r in chunk:
            m = _norm_mdn(r.get("phone_number"))
            if not m:
                continue
            per[m] = per.get(m, 0.0) + safe_float(r.get("actual_mi_payout")) + safe_float(r.get("actual_atu_payout"))
        if len(chunk) < 1000 or page > 60:
            break
        page += 1
    return per


def _byod_specific_residual(client, org_id, company):
    """Attribute a monthly residual/sub to BYOD subscribers: take the most complete recent residual
    month, and of the subscribers earning residual that month, isolate those whose activation was BYOD
    (any recent month) via the MDN join. Bounded to one residual month to stay fast; None on any failure."""
    try:
        if not company:
            return None
        base = max(company, key=lambda c: c["residual"])  # most complete (highest-$) recent month
        res_by_mdn = _residual_by_mdn(client, org_id, base["period"])
        if not res_by_mdn:
            return None
        byod_mdns = _byod_mdns(client, org_id, _list_periods(client, org_id)[:6])
        matched = {m: v for m, v in res_by_mdn.items() if m in byod_mdns and v}
        others = {m: v for m, v in res_by_mdn.items() if m not in byod_mdns and v}
        byod_res = sum(matched.values())
        return {
            "period": base["period"],
            "byod_activation_mdns": len(byod_mdns),
            "byod_subs_with_residual": len(matched),
            "byod_residual_month": round(byod_res, 2),
            "avg_residual_per_byod_sub": round(byod_res / len(matched), 2) if matched else 0.0,
            "avg_residual_per_other_sub": round(sum(others.values()) / len(others), 2) if others else 0.0,
            "match_rate": round(len(matched) / len(byod_mdns), 3) if byod_mdns else 0.0,
        }
    except Exception:
        return None


# ─── 3. Accessory sales ↔ BYOD activations ↔ total revenue ──────────────────────────────────
def _acc_cfg(client, org_id):
    depts, cats, kws = [], [], []
    try:
        rows = (client.schema("commcalc").table("flag_rules")
                .select("accessory_departments,accessory_categories,accessory_product_keywords")
                .eq("org_id", org_id).eq("id", 1).limit(1).execute().data) or []
        if rows:
            depts = [d for d in (rows[0].get("accessory_departments") or []) if d]
            cats = [c for c in (rows[0].get("accessory_categories") or []) if c]
            kws = [k for k in (rows[0].get("accessory_product_keywords") or []) if k]
    except Exception:
        pass
    if not depts and not cats and not kws:
        depts = ["Ondigo"]
    return {"d": {x.strip().lower() for x in depts}, "c": {x.strip().lower() for x in cats},
            "p": {x.strip().lower() for x in kws}}


def _is_acc(dept, cat, product, acc):
    if (dept or "").strip().lower() in acc["d"]:
        return True
    c = (cat or "").strip().lower()
    if c and c in acc["c"]:
        return True
    if acc["p"]:
        p = (product or "").strip().lower()
        if p and any(k in p for k in acc["p"]):
            return True
    return False


def _fetch_period_sales(client, org_id, period):
    cols = "store,contract_type,department,category,product_desc,ext_price,trans_id,voided,trans_type"
    for tbl in ("raw_sales", "daily_sales_feed"):
        out, page = [], 0
        while True:
            start = page * 1000
            try:
                chunk = (client.schema("commcalc").table(tbl).select(cols)
                         .eq("org_id", org_id).in_("period", _pvariants(period))
                         .range(start, start + 999).execute().data) or []
            except Exception:
                chunk = []
            out.extend(chunk)
            if len(chunk) < 1000 or page > 60:
                break
            page += 1
        if out:
            return out
    return []


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / ((sxx * syy) ** 0.5), 3)


def accessory_byod_correlation(client, org_id, months=4):
    acc = _acc_cfg(client, org_id)
    periods = _list_periods(client, org_id)[:max(1, min(months, 12))]
    points = []
    for period in periods:
        rows = _fetch_period_sales(client, org_id, period)
        per_store = {}
        for r in rows:
            if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                continue
            if str(r.get("trans_type") or "").strip() == "Return":
                continue
            store = (r.get("store") or "").strip() or "(unknown)"
            s = per_store.setdefault(store, {"byod": set(), "acc_rev": 0.0, "revenue": 0.0})
            ext = safe_float(r.get("ext_price"))
            s["revenue"] += ext
            if _is_acc(r.get("department"), r.get("category"), r.get("product_desc"), acc):
                s["acc_rev"] += ext
            tid = str(r.get("trans_id") or "").strip()
            if tid and classify_contract_type(r.get("contract_type")) == "byod":
                s["byod"].add(tid)
        for store, s in per_store.items():
            if s["revenue"] <= 0 and not s["byod"]:
                continue
            points.append({"store": store, "period": period, "byod": len(s["byod"]),
                           "accessory_rev": round(s["acc_rev"], 2), "revenue": round(s["revenue"], 2)})

    byod = [p["byod"] for p in points]
    accr = [p["accessory_rev"] for p in points]
    rev = [p["revenue"] for p in points]
    return {
        "periods": periods, "points": points, "n": len(points),
        "correlation": {
            "byod_vs_accessory": _pearson(byod, accr),
            "byod_vs_revenue": _pearson(byod, rev),
            "accessory_vs_revenue": _pearson(accr, rev),
        },
        "totals": {
            "byod": sum(byod), "accessory_rev": round(sum(accr), 2), "revenue": round(sum(rev), 2),
        },
    }
