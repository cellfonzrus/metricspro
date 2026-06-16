"""#10 reconciliation — VIP credit-memo residual vs MI + ATU earned.

VIP pays the MI + ATU residual COMBINED as one "Weekly Incentive Credit" memo (Xfinity is a
separate report, excluded). The core, unambiguous check is COMPANY-WIDE for a period:
    Σ credit-memo GrandTotal (non-Xfinity)  ==  Σ raw_mi (actual_mi_payout + actual_atu_payout)
We also break it down PER STORE: memos carry a store (CompanyName line 2); MI/ATU rows carry a
rep, which we attribute to a store best-effort via rep_commissions (rep name → store). Per-store
attribution is labeled best-effort because raw_mi has no native store column.

Discrepancies beyond a configurable tolerance write to commcalc.flags (under-payment = critical,
over-payment = warning). When ANTHROPIC_API_KEY is set, flagged stores get a Claude "missed-days"
note that buckets MI/ATU by day (mi_activation_date) against the memo's date range.
"""
from datetime import datetime, timezone

from app.core.config import settings
from app.modules.commcalc.calculator import safe_float
from app.modules.account import coa

DEFAULT_TOLERANCE = 1.0
DEFAULT_DATE_COL = "mi_activation_date"


def _rep_to_store(client, org_id, period):
    mp = {}
    for r in coa._fetch_all(client, "rep_commissions", "epay_salesperson,storeops_name,store",
                            {"org_id": org_id, "period": period}):
        st = (r.get("store") or "").strip()
        if not st:
            continue
        for nm in (r.get("epay_salesperson"), r.get("storeops_name")):
            if nm:
                mp[str(nm).strip().lower()] = st
    return mp


def reconcile(client, org_id, period, tolerance=DEFAULT_TOLERANCE, date_col=DEFAULT_DATE_COL):
    tol = abs(safe_float(tolerance)) or DEFAULT_TOLERANCE

    # credit memos for the period (exclude Xfinity)
    memos = coa._fetch_all(client, "vip_credit_memos",
                           "credit_memo_number,memo,company_name,store_address,grand_total,is_xfinity,"
                           "memo_start,memo_end,created_on,period", {"org_id": org_id, "period": period})
    memo_by_store, excluded, memo_total_all = {}, 0, 0.0
    for m in memos:
        if m.get("is_xfinity"):
            excluded += 1
            continue
        store = (m.get("store_address") or "").strip() or "(unattributed)"
        amt = safe_float(m.get("grand_total"))
        s = memo_by_store.setdefault(store, {"memo_total": 0.0, "memos": []})
        s["memo_total"] = round(s["memo_total"] + amt, 2)
        s["memos"].append({"number": m.get("credit_memo_number"), "memo": m.get("memo"),
                           "amount": round(amt, 2), "start": m.get("memo_start"), "end": m.get("memo_end")})
        memo_total_all = round(memo_total_all + amt, 2)

    # MI + ATU for the period, attributed to a store via rep name (best-effort).
    # Effective MI accrual date = mi_activation_date (new activations) OR
    # residual_transfer_in_date (upgrades) — both drive total MI received. ATU is the
    # auto-pay initiation and is paid combined with MI in the credit memo.
    rep2store = _rep_to_store(client, org_id, period)
    sel = "actual_mi_payout,actual_atu_payout,rep_username,mi_activation_date,residual_transfer_in_date"
    try:
        mi_rows = coa._fetch_all(client, "raw_mi", sel, {"org_id": org_id, "period": period})
    except Exception:
        mi_rows = coa._fetch_all(client, "raw_mi", "actual_mi_payout,actual_atu_payout,rep_username",
                                 {"org_id": org_id, "period": period})

    def _accrual(r):
        d = r.get(date_col) or r.get("mi_activation_date") or r.get("residual_transfer_in_date")
        return str(d or "")[:10] or None

    mi_by_store, mi_by_store_day, mi_total_all, mi_unattributed = {}, {}, 0.0, 0.0
    for r in mi_rows:
        mi, atu = safe_float(r.get("actual_mi_payout")), safe_float(r.get("actual_atu_payout"))
        amt = mi + atu
        mi_total_all = round(mi_total_all + amt, 2)
        st = rep2store.get(str(r.get("rep_username") or "").strip().lower())
        if st:
            mi_by_store[st] = round(mi_by_store.get(st, 0.0) + amt, 2)
            day = _accrual(r)
            if day:
                d = mi_by_store_day.setdefault(st, {})
                d[day] = round(d.get(day, 0.0) + amt, 2)
        else:
            mi_unattributed = round(mi_unattributed + amt, 2)

    # per-store rows (union of stores seen in memos + attributed MI)
    stores = sorted(set(memo_by_store) | set(mi_by_store))
    rows = []
    for st in stores:
        memo_t = round(memo_by_store.get(st, {}).get("memo_total", 0.0), 2)
        mi_t = round(mi_by_store.get(st, 0.0), 2)
        diff = round(memo_t - mi_t, 2)  # +ve = VIP paid more than earned
        if abs(diff) <= tol:
            status = "ok"
        elif diff < 0:
            status = "under"   # VIP paid LESS than MI+ATU earned (critical)
        else:
            status = "over"    # VIP paid MORE (warning)
        row = {"store": st, "memo_total": memo_t, "mi_atu_total": mi_t, "diff": diff,
               "status": status, "memos": memo_by_store.get(st, {}).get("memos", [])}
        if status != "ok":
            # by-day MI/ATU accrual (activation + residual transfer-in) for missed-days analysis
            row["by_day"] = dict(sorted(mi_by_store_day.get(st, {}).items()))
        rows.append(row)

    cw_diff = round(memo_total_all - mi_total_all, 2)
    company_wide = {
        "memo_total": memo_total_all, "mi_atu_total": mi_total_all, "diff": cw_diff,
        "status": "ok" if abs(cw_diff) <= tol else ("under" if cw_diff < 0 else "over"),
        "mi_unattributed": mi_unattributed,
    }
    return {
        "period": period, "tolerance": tol, "date_col": date_col,
        "company_wide": company_wide, "stores": rows,
        "xfinity_excluded": excluded, "memos_loaded": len(memos),
        "has_memos": len(memos) > 0,
        "notes": [
            "Company-wide is the authoritative check (VIP pays MI+ATU combined). Per-store MI/ATU "
            "is attributed best-effort by rep name (raw_mi has no native store column).",
            "MI accrual date = activation (new activations) or residual transfer-in (upgrades); "
            "ATU = auto-pay initiation, paid combined. Flagged stores carry a by-day MI/ATU breakdown "
            "for missed-days analysis. Xfinity memos excluded.",
        ],
    }


def sync_flags(client, org_id, period, tolerance=DEFAULT_TOLERANCE, date_col=DEFAULT_DATE_COL):
    """Write recon discrepancies to commcalc.flags (delete-by-source then insert)."""
    rec = reconcile(client, org_id, period, tolerance, date_col)
    pm, py = coa.parse_period(period)
    flags = []
    cw = rec["company_wide"]
    if cw["status"] != "ok":
        sev = "critical" if cw["status"] == "under" else "warning"
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "VIP credit-memo recon (company-wide)", "source": "account_recon",
            "severity": sev, "amount": abs(cw["diff"]),
            "description": (f"Company-wide: VIP credit memos {cw['memo_total']:.2f} vs MI+ATU earned "
                            f"{cw['mi_atu_total']:.2f} ({'short' if cw['status'] == 'under' else 'over'} "
                            f"by {abs(cw['diff']):.2f})."),
        })
    for r in rec["stores"]:
        if r["status"] == "ok":
            continue
        sev = "critical" if r["status"] == "under" else "warning"
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "VIP credit-memo recon (store)", "source": "account_recon",
            "severity": sev, "store_address": r["store"], "amount": abs(r["diff"]),
            "description": (f"{r['store']}: memos {r['memo_total']:.2f} vs MI+ATU {r['mi_atu_total']:.2f} "
                            f"({'short' if r['status'] == 'under' else 'over'} by {abs(r['diff']):.2f})."),
        })

    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "account_recon").eq("period", period).execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i + 500]).execute()
    return {"period": period, "flags_written": len(flags),
            "company_wide_status": cw["status"], "stores_flagged": sum(1 for r in rec["stores"] if r["status"] != "ok")}
