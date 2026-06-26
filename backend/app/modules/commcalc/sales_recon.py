"""Sales feed reconciliation (Theme 5).

Compares the AUTHORITATIVE monthly Sales-Transaction-Details upload (commcalc.raw_sales) against the
daily B2B feed (commcalc.daily_sales_feed, fed by the FTP sweep / manual daily_sales upload) at
trans_id grain, for one period. Surfaces:

  • missing_in_monthly  — a transaction in the daily feed that never made it into the authoritative
                          monthly file (a real revenue / commission leak, or a same-day void). HIGH.
  • missing_in_daily    — a transaction in the monthly file that the daily feed never captured
                          (usually a feed-coverage gap; lower severity).
  • amount_mismatch     — same trans_id in both, but the summed ext_price differs beyond tolerance.
  • matched             — present in both with matching totals (count/total only, not listed).

raw_sales is multi-line per trans_id (one transaction = many line items), so we aggregate per
trans_id within each source before comparing. Voided lines are excluded from both sides so we compare
net sales. Read-only — no flags/writes (a deliberate v1; flagging is a later increment).
"""
from datetime import date
from app.core.database import get_supabase

ORG_ID = "00000000-0000-0000-0000-000000000001"
TOLERANCE = 0.01          # $ difference per trans_id treated as a match
_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
           'July', 'August', 'September', 'October', 'November', 'December']


def _period_label(period: str) -> str:
    """Normalize 'June 2026' or '2026-06' → the month-name label both tables store ('June 2026')."""
    s = (period or "").strip()
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-":
        try:
            return date(int(s[:4]), int(s[5:7]), 1).strftime("%B %Y")
        except Exception:
            return s
    return s


def _is_void(v) -> bool:
    return str(v or "").strip().lower() in ("true", "yes", "y", "1", "voided", "void")


def _aggregate(rows):
    """Fold raw line items into per-trans_id transactions: total ext_price + line count + identity."""
    by_tid = {}
    total = 0.0
    lines = 0
    for r in rows:
        if _is_void(r.get("voided")):
            continue
        tid = (r.get("trans_id") or "").strip()
        if not tid:
            continue
        amt = float(r.get("ext_price") or 0)
        lines += 1
        total += amt
        t = by_tid.get(tid)
        if t is None:
            by_tid[tid] = {
                "trans_id": tid,
                "total": amt,
                "lines": 1,
                "store": r.get("store") or "Unknown",
                "salesperson": r.get("salesperson") or "",
                "trans_date": str(r.get("trans_date") or "")[:10],
            }
        else:
            t["total"] += amt
            t["lines"] += 1
            if not t["store"] or t["store"] == "Unknown":
                t["store"] = r.get("store") or t["store"]
    return by_tid, round(total, 2), lines


def _fetch(client, table, plabel):
    """Pull the trans-level columns for a period from a sales-shaped table (paginated)."""
    out, start, PAGE = [], 0, 1000
    while True:
        resp = (client.schema("commcalc").table(table)
                .select("trans_id,ext_price,store,salesperson,trans_date,voided")
                .eq("org_id", ORG_ID).eq("period", plabel)
                .range(start, start + PAGE - 1).execute())
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
        start += PAGE
    return out


def run_sales_recon(period: str):
    client = get_supabase()
    plabel = _period_label(period)

    monthly_rows = _fetch(client, "raw_sales", plabel)
    feed_rows = _fetch(client, "daily_sales_feed", plabel)

    monthly, monthly_total, monthly_lines = _aggregate(monthly_rows)
    daily, daily_total, daily_lines = _aggregate(feed_rows)

    rows = []
    matched = 0
    mim_total = mid_total = mismatch_delta = 0.0

    # Union of trans_ids across both sources.
    for tid in set(monthly) | set(daily):
        m = monthly.get(tid)
        d = daily.get(tid)
        if m and d:
            delta = round(d["total"] - m["total"], 2)
            if abs(delta) <= TOLERANCE:
                matched += 1
                continue
            bucket = "amount_mismatch"
            mismatch_delta += delta
            store = m["store"]
            rows.append({
                "bucket": bucket, "trans_id": tid, "store": store,
                "salesperson": m["salesperson"] or d["salesperson"],
                "trans_date": m["trans_date"] or d["trans_date"],
                "monthly_total": round(m["total"], 2), "daily_total": round(d["total"], 2),
                "delta": delta, "monthly_lines": m["lines"], "daily_lines": d["lines"],
            })
        elif d and not m:
            mim_total += d["total"]
            rows.append({
                "bucket": "missing_in_monthly", "trans_id": tid, "store": d["store"],
                "salesperson": d["salesperson"], "trans_date": d["trans_date"],
                "monthly_total": None, "daily_total": round(d["total"], 2),
                "delta": round(d["total"], 2), "monthly_lines": 0, "daily_lines": d["lines"],
            })
        else:  # m and not d
            mid_total += m["total"]
            rows.append({
                "bucket": "missing_in_daily", "trans_id": tid, "store": m["store"],
                "salesperson": m["salesperson"], "trans_date": m["trans_date"],
                "monthly_total": round(m["total"], 2), "daily_total": None,
                "delta": round(-m["total"], 2), "monthly_lines": m["lines"], "daily_lines": 0,
            })

    # Per-store rollup.
    by_store = {}
    for r in rows:
        s = by_store.setdefault(r["store"], {
            "store": r["store"], "missing_in_monthly": 0, "missing_in_daily": 0,
            "amount_mismatch": 0, "delta_total": 0.0,
        })
        s[r["bucket"]] += 1
        s["delta_total"] += (r["delta"] or 0)
    by_store_list = sorted(by_store.values(),
                           key=lambda s: abs(s["delta_total"]), reverse=True)
    for s in by_store_list:
        s["delta_total"] = round(s["delta_total"], 2)

    return {
        "period": plabel,
        "has_feed": daily_lines > 0,
        "summary": {
            "monthly_trans": len(monthly), "daily_trans": len(daily),
            "monthly_lines": monthly_lines, "daily_lines": daily_lines,
            "monthly_total": monthly_total, "daily_total": daily_total,
            "matched": matched,
            "missing_in_monthly": sum(1 for r in rows if r["bucket"] == "missing_in_monthly"),
            "missing_in_daily": sum(1 for r in rows if r["bucket"] == "missing_in_daily"),
            "amount_mismatch": sum(1 for r in rows if r["bucket"] == "amount_mismatch"),
            "missing_in_monthly_total": round(mim_total, 2),
            "missing_in_daily_total": round(mid_total, 2),
            "mismatch_delta_total": round(mismatch_delta, 2),
        },
        "by_store": by_store_list,
        "rows": sorted(rows, key=lambda r: abs(r["delta"] or 0), reverse=True),
    }


def _period_my(plabel: str):
    """('June 2026') -> (6, 2026); best-effort, (None, None) on failure."""
    parts = (plabel or "").strip().split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        return _MONTHS.index(parts[0]) + 1, int(parts[1])
    return None, None


def sync_recon_flags(period: str, include_mismatch: bool = True):
    """Persist sales-feed recon findings into commcalc.flags so leaks show on the Flags page and can be
    routed/notified like any other flag. Writes:
      • missing_in_monthly → flag_type 'sales_leak' (severity 'critical') — money in the daily B2B feed
        that never reached the authoritative monthly file: a real revenue/commission leak or an
        unrecorded void.
      • amount_mismatch    → flag_type 'sales_amount_mismatch' (severity 'warning'), when include_mismatch.
    (missing_in_daily is a feed-coverage gap, not a money leak — intentionally not flagged.)
    Delete-first by (org_id, period, source='sales_recon') then insert, so re-running is idempotent and
    never touches other flag sources (matches the asset _sync_*_flags pattern). Returns counts."""
    res = run_sales_recon(period)
    plabel = res["period"]
    pm, py = _period_my(plabel)
    client = get_supabase()

    flags = []
    for r in res["rows"]:
        b = r["bucket"]
        if b == "missing_in_monthly":
            flags.append({
                "period": plabel, "period_month": pm, "period_year": py,
                "flag_type": "sales_leak", "source": "sales_recon", "severity": "critical",
                "store_address": r["store"], "epay_salesperson": r.get("salesperson") or "",
                "amount": r.get("daily_total"),
                "description": (f"Trans {r['trans_id']} is in the daily B2B feed "
                                f"(${(r.get('daily_total') or 0):,.2f}, {r.get('trans_date') or 'n/a'}) "
                                f"but NOT in the authoritative monthly sales file — revenue/commission "
                                f"leak or an unrecorded void."),
            })
        elif b == "amount_mismatch" and include_mismatch:
            flags.append({
                "period": plabel, "period_month": pm, "period_year": py,
                "flag_type": "sales_amount_mismatch", "source": "sales_recon", "severity": "warning",
                "store_address": r["store"], "epay_salesperson": r.get("salesperson") or "",
                "amount": r.get("delta"),
                "description": (f"Trans {r['trans_id']} totals differ: monthly "
                                f"${(r.get('monthly_total') or 0):,.2f} vs daily "
                                f"${(r.get('daily_total') or 0):,.2f} (Δ ${(r.get('delta') or 0):,.2f})."),
            })

    (client.schema("commcalc").table("flags").delete()
     .eq("org_id", ORG_ID).eq("period", plabel).eq("source", "sales_recon").execute())
    if flags:
        for f in flags:
            f["org_id"] = ORG_ID
        for i in range(0, len(flags), 500):
            client.schema("commcalc").table("flags").insert(flags[i:i + 500]).execute()

    return {
        "period": plabel, "has_feed": res["has_feed"], "flagged": len(flags),
        "missing_in_monthly": res["summary"]["missing_in_monthly"],
        "amount_mismatch": res["summary"]["amount_mismatch"] if include_mismatch else 0,
        "leak_total": res["summary"]["missing_in_monthly_total"],
    }
