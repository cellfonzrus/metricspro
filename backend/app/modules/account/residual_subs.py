"""Residual per subscriber (MI+ATU), per store, month over month — with a commission overlay.

Purpose: see the effect of lower commissions on the residual payout over time.

- Residual   = actual_mi_payout + actual_atu_payout per raw_mi row.
- Subscriber = a distinct phone number we are PAID residual on that month (MI+ATU nonzero).
- Residual/sub = Σ residual ÷ distinct paid phones, per store per month.
- Store       = raw_mi.salesforce_id → store_mapping.salesforce_id (the clean join gp_report uses);
                rows whose salesforce_id doesn't resolve go to an "(Unassigned)" bucket so the company
                total stays complete (residual for ALL companies).
- Commission  = Σ rep_commissions.total_payout per month; per-store it's matched by street number.

Aggregation runs in Postgres via commcalc.residual_per_sub_by_store (raw_mi is ~38k rows/month); if
that RPC isn't present yet it falls back to a bounded Python aggregation (last `months` only) so the
page always works — running migration 101 just makes it fast over full history.
"""
import re
from datetime import datetime, timezone

from app.modules.commcalc.calculator import safe_float
from app.modules.account._period import parse_period, recent_period_keys


def _pkey(period):
    # (year, month) sort key. parse_period is now the shared finance helper (returns (month, year),
    # robust across both spellings). Byte-identical to the prior month-name-only parse for the
    # month-name period labels raw_mi actually stores; numeric 'YYYY-MM' now sorts correctly too.
    mo, yr = parse_period(period or "")
    return (yr, mo)


def _street_num(addr):
    m = re.match(r"\s*(\d+)", str(addr or ""))
    return m.group(1) if m else ""


def _recent_labels(latest_y, latest_m, n):
    """The last `n` months ending at (latest_y, latest_m), as both 'Month YYYY' and 'YYYY-MM'
    spellings — delegated to the shared finance helper (single source of truth)."""
    return recent_period_keys(latest_y, latest_m, n)


def _latest_period(client, org_id):
    """(year, month) of the most recent raw_mi period; falls back to today if the columns are empty."""
    try:
        rows = (client.schema("commcalc").table("raw_mi")
                .select("period_year,period_month")
                .eq("org_id", org_id)
                .order("period_year", desc=True).order("period_month", desc=True)
                .limit(1).execute().data) or []
        if rows and rows[0].get("period_year") and rows[0].get("period_month"):
            return int(rows[0]["period_year"]), int(rows[0]["period_month"])
    except Exception:
        pass
    n = datetime.now(timezone.utc)
    return n.year, n.month


def _aggregate(client, org_id, months):
    """Per (period, salesforce_id): sum_mi, sum_atu, subs, lines. Postgres RPC, Python fallback."""
    # Fast path: RPC over ALL history (grouped in Postgres), trim to last `months` after.
    try:
        rows = client.schema("commcalc").rpc(
            "residual_per_sub_by_store", {"p_org_id": org_id, "p_periods": None}).execute().data or []
        if rows:
            return rows
    except Exception:
        pass
    # Fallback: bound to the last `months` periods (avoid a full-history Python scan), paginate + aggregate.
    ly, lm = _latest_period(client, org_id)
    want = _recent_labels(ly, lm, months)
    agg, subs = {}, {}
    start, page = 0, 1000
    while True:
        chunk = (client.schema("commcalc").table("raw_mi")
                 .select("period,salesforce_id,phone_number,actual_mi_payout,actual_atu_payout")
                 .eq("org_id", org_id).in_("period", want)
                 .range(start, start + page - 1).execute().data) or []
        for r in chunk:
            per = (r.get("period") or "").strip()
            if not per:
                continue
            sf = (r.get("salesforce_id") or "").strip()
            mi = safe_float(r.get("actual_mi_payout"))
            atu = safe_float(r.get("actual_atu_payout"))
            k = (per, sf)
            a = agg.setdefault(k, {"period": per, "salesforce_id": sf,
                                   "sum_mi": 0.0, "sum_atu": 0.0, "lines": 0})
            a["sum_mi"] += mi
            a["sum_atu"] += atu
            a["lines"] += 1
            ph = (r.get("phone_number") or "").strip()
            if ph and (mi + atu) != 0:
                subs.setdefault(k, set()).add(ph)
        if len(chunk) < page:
            break
        start += page
    out = []
    for k, a in agg.items():
        a["subs"] = len(subs.get(k, ()))
        out.append(a)
    return out


def compute(client, org_id, months=6):
    """Return the residual-per-subscriber trend: per-store monthly series + an exact company total.
    Filtering by store/market is done client-side (like the GP report), so this returns every store."""
    agg = _aggregate(client, org_id, months)

    # salesforce_id → store metadata
    sm_rows = (client.schema("commcalc").table("store_mapping")
               .select("store_address,market,store_code,salesforce_id,is_active")
               .eq("org_id", org_id).execute().data) or []
    by_sfid = {}
    for s in sm_rows:
        sf = (s.get("salesforce_id") or "").strip()
        if not sf:
            continue
        by_sfid[sf] = {"store": (s.get("store_address") or "").strip(),
                       "market": ((s.get("market") or "Boost").strip() or "Boost"),
                       "store_code": (s.get("store_code") or "").strip(),
                       "num": _street_num(s.get("store_address"))}

    # periods present → keep the last `months` chronologically
    all_periods = sorted({(a.get("period") or "").strip() for a in agg if a.get("period")}, key=_pkey)
    kept = all_periods[-months:] if months and months > 0 else all_periods
    kept_set = set(kept)

    # commission by street-number/period (per-store) + exact company total per period
    comm_by_num, comm_company = {}, {p: 0.0 for p in kept}
    if kept:
        crows = (client.schema("commcalc").table("rep_commissions")
                 .select("store,total_payout,period").eq("org_id", org_id)
                 .in_("period", kept).execute().data) or []
        for r in crows:
            per = (r.get("period") or "").strip()
            if per not in kept_set:
                continue
            pay = safe_float(r.get("total_payout"))
            comm_company[per] = comm_company.get(per, 0.0) + pay
            num = _street_num(r.get("store"))
            if num:
                comm_by_num.setdefault(num, {})
                comm_by_num[num][per] = comm_by_num[num].get(per, 0.0) + pay

    # bucket residual by store
    UNASSIGNED = "(Unassigned)"
    stores = {}
    for a in agg:
        per = (a.get("period") or "").strip()
        if per not in kept_set:
            continue
        meta = by_sfid.get((a.get("salesforce_id") or "").strip())
        if meta and meta["store"]:
            label, market_v, code, num = meta["store"], meta["market"], meta["store_code"], meta["num"]
        else:
            label, market_v, code, num = UNASSIGNED, "(Unassigned)", "", ""
        d = stores.setdefault(label, {"store": label, "market": market_v,
                                      "store_code": code, "num": num, "per": {}})
        pp = d["per"].setdefault(per, {"mi": 0.0, "atu": 0.0, "subs": 0})
        pp["mi"] += safe_float(a.get("sum_mi"))
        pp["atu"] += safe_float(a.get("sum_atu"))
        pp["subs"] += int(a.get("subs") or 0)

    # assemble per-store series
    store_rows = []
    company_res = {p: 0.0 for p in kept}
    company_subs = {p: 0 for p in kept}
    for label, d in stores.items():
        series, t_res, t_subs, t_comm = [], 0.0, 0, 0.0
        for p in kept:
            pp = d["per"].get(p, {"mi": 0.0, "atu": 0.0, "subs": 0})
            res = pp["mi"] + pp["atu"]
            subs = int(pp["subs"])
            comm = round((comm_by_num.get(d["num"], {}) or {}).get(p, 0.0), 2) if d["num"] else 0.0
            series.append({"period": p, "mi": round(pp["mi"], 2), "atu": round(pp["atu"], 2),
                           "residual": round(res, 2), "subs": subs,
                           "per_sub": round(res / subs, 2) if subs else 0.0, "commission": comm})
            t_res += res
            t_subs += subs
            t_comm += comm
            company_res[p] += res
            company_subs[p] += subs
        store_rows.append({
            "store": label, "store_code": d["store_code"], "market": d["market"], "series": series,
            "totals": {"residual": round(t_res, 2), "subs": int(t_subs),
                       "per_sub": round(t_res / t_subs, 2) if t_subs else 0.0,
                       "commission": round(t_comm, 2)}})
    store_rows.sort(key=lambda x: -x["totals"]["residual"])

    # exact company line per period (commission is the true Σ, independent of store matching)
    company = []
    for p in kept:
        subs = int(company_subs[p])
        res = company_res[p]
        company.append({"period": p, "residual": round(res, 2), "subs": subs,
                        "per_sub": round(res / subs, 2) if subs else 0.0,
                        "commission": round(comm_company.get(p, 0.0), 2)})

    markets = sorted({d["market"] for d in store_rows if d["market"]})
    return {
        "months": kept,
        "stores": store_rows,
        "company": company,
        "markets": markets,
        "note": (None if kept else
                 "No residual (MI/ATU) data yet — upload an MI report or run the ePay sweep first."),
    }
