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


def _fetch(client, table, plabel, org_id=ORG_ID):
    """Pull the trans-level columns for a period from a sales-shaped table (paginated)."""
    out, start, PAGE = [], 0, 1000
    while True:
        resp = (client.schema("commcalc").table(table)
                .select("trans_id,ext_price,store,salesperson,trans_date,voided")
                .eq("org_id", org_id).eq("period", plabel)
                .range(start, start + PAGE - 1).execute())
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
        start += PAGE
    return out


def run_sales_recon(period: str, org_id: str = ORG_ID):
    client = get_supabase()
    plabel = _period_label(period)

    monthly_rows = _fetch(client, "raw_sales", plabel, org_id)
    feed_rows = _fetch(client, "daily_sales_feed", plabel, org_id)

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


def transaction_detail(period: str, trans_id: str, org_id: str = ORG_ID):
    """Line-item drill-down for ONE transaction: every raw_sales (monthly) line vs every
    daily_sales_feed line for that trans_id, so you can see exactly WHAT differs (a missing line, a
    price change, a void). Powers the Sales Feed Recon row click-through."""
    client = get_supabase()
    plabel = _period_label(period)
    cols = ("trans_id,trans_date,store,salesperson,product_desc,department,category,"
            "ext_price,gp,mdn,serial_1,voided,contract_type,tender_type")

    def fetch(table):
        rows = (client.schema("commcalc").table(table).select(cols)
                .eq("org_id", org_id).eq("period", plabel).eq("trans_id", str(trans_id))
                .limit(2000).execute().data) or []
        lines, total = [], 0.0
        for r in rows:
            voided = _is_void(r.get("voided"))
            try:
                amt = float(r.get("ext_price") or 0)
            except Exception:
                amt = 0.0
            if not voided:
                total += amt
            lines.append({
                "product_desc": r.get("product_desc"), "department": r.get("department"),
                "category": r.get("category"), "contract_type": r.get("contract_type"),
                "tender_type": r.get("tender_type"), "ext_price": round(amt, 2),
                "voided": voided, "mdn": r.get("mdn"), "serial_1": r.get("serial_1"),
                "trans_date": str(r.get("trans_date") or "")[:10],
            })
        lines.sort(key=lambda x: -(x["ext_price"] or 0))
        return lines, round(total, 2)

    monthly, m_tot = fetch("raw_sales")
    daily, d_tot = fetch("daily_sales_feed")
    hdr = (daily or monthly or [{}])[0]
    return {"trans_id": str(trans_id), "period": plabel,
            "store": hdr.get("store") if isinstance(hdr, dict) else None,
            "monthly": monthly, "daily": daily,
            "monthly_total": m_tot, "daily_total": d_tot, "delta": round(d_tot - m_tot, 2),
            "in_monthly": len(monthly) > 0, "in_daily": len(daily) > 0}


def _period_my_any(p):
    """(month, year) from EITHER spelling — 'June 2026' or '2026-06'; (None, None) otherwise.

    mig 287: the retire step bounds itself on `period = any(...)`, so it must be handed every spelling
    the table might hold or a row stored the other way is silently never retired. `_period_my` below
    only parses the month-name form, which is the recurring period-spelling bug class in this module."""
    s = (p or "").strip()
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-" and s[5:7].isdigit():
        m, y = int(s[5:7]), int(s[:4])
        return (m, y) if 1 <= m <= 12 else (None, None)
    return _period_my(s)


def _pvariants_recon(p):
    """Both spellings of a month-period, deduped; [p] when it is not a month-period."""
    import calendar as _c
    m, y = _period_my_any(p)
    if not m or not y:
        return [x for x in [str(p or "").strip()] if x]
    out = []
    for v in (str(p or "").strip(), f"{y:04d}-{m:02d}", f"{_c.month_name[m]} {y}"):
        if v and v not in out:
            out.append(v)
    return out


def _period_my(plabel: str):
    """('June 2026') -> (6, 2026); best-effort, (None, None) on failure."""
    parts = (plabel or "").strip().split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        return _MONTHS.index(parts[0]) + 1, int(parts[1])
    return None, None


def sync_recon_flags(period: str, include_mismatch: bool = True, org_id: str = ORG_ID):
    """Persist sales-feed recon findings into commcalc.flags so leaks show on the Flags page and can be
    routed/notified like any other flag. Writes:
      • missing_in_monthly → flag_type 'sales_leak' (severity 'critical') — money in the daily B2B feed
        that never reached the authoritative monthly file: a real revenue/commission leak or an
        unrecorded void.
      • amount_mismatch    → flag_type 'sales_amount_mismatch' (severity 'warning'), when include_mismatch.
    (missing_in_daily is a feed-coverage gap, not a money leak — intentionally not flagged.)
    Delete-first by (org_id, period, source='sales_recon') then insert, so re-running is idempotent and
    never touches other flag sources (matches the asset _sync_*_flags pattern). Returns counts."""
    res = run_sales_recon(period, org_id)
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
                # mig 287 identity — the leaking transaction. The description carries dollar totals
                # that move between runs, so it can never be the key.
                "source_ref": str(r.get("trans_id") or "").strip(),
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
                "source_ref": str(r.get("trans_id") or "").strip(),
                "amount": r.get("delta"),
                "description": (f"Trans {r['trans_id']} totals differ: monthly "
                                f"${(r.get('monthly_total') or 0):,.2f} vs daily "
                                f"${(r.get('daily_total') or 0):,.2f} (Δ ${(r.get('delta') or 0):,.2f})."),
            })

    # Resolve each flag's store into `store_code` (mig 285) so it can reach the district manager whose
    # span covers it — a recon leak written with a POS spelling the span keyset doesn't know matches no
    # manager at all. Visibility only; nothing here changes an amount or a store_address.
    try:
        from app.modules.commcalc import flag_store_resolver
        flag_store_resolver.stamp_flags(client, org_id, flags)
    except Exception as e:                                  # never fail a recon on a routing lookup
        print(f"WARN sync_recon_flags store_code stamping skipped: {e}")

    # ADDITIVE (mig 287, owner 2026-08-08 "DM review should not be erased and teh new data should only
    # add the missing data if any"). The delete-first-by-source this replaces re-created every recon
    # flag on each sweep, so a district manager's review of a leak lasted until the next sweep. Now a
    # leak that is still leaking keeps its row (and its review) with a refreshed dollar delta, a leak
    # that has since been reconciled is RETIRED with a reason instead of vanishing, and a new one is
    # inserted. Both period spellings are passed because raw_sales stores 'June 2026' while the caller
    # may pass '2026-06'.
    _periods = _pvariants_recon(plabel)
    try:
        from app.modules.commcalc import flag_persist
        _fp = flag_persist.sync(client, org_id, flags,
                                periods=_periods, sources=["sales_recon"],
                                reason=f"the {plabel} transaction reconciles in the latest sweep")
        print(f"INFO sales_recon flags additive org={org_id} period={plabel} "
              f"{ {k: v for k, v in _fp.items() if k != 'run_id'} }")
    except Exception as e:                          # incl. FlagPersistUnavailable before migration 287
        print(f"WARN sales_recon additive flag write unavailable, using legacy path: {e}")
        (client.schema("commcalc").table("flags").delete()
         .eq("org_id", org_id).eq("period", plabel).eq("source", "sales_recon").execute())
        if flags:
            for f in flags:
                f["org_id"] = org_id
            for i in range(0, len(flags), 500):
                client.schema("commcalc").table("flags").insert(flags[i:i + 500]).execute()

    return {
        "period": plabel, "has_feed": res["has_feed"], "flagged": len(flags),
        "missing_in_monthly": res["summary"]["missing_in_monthly"],
        "amount_mismatch": res["summary"]["amount_mismatch"] if include_mismatch else 0,
        "leak_total": res["summary"]["missing_in_monthly_total"],
    }


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# DERIVE GAP — the COUNT-ONLY feed-vs-basis divergence (mod-commission, 2026-08-01)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
# run_sales_recon() above is the full report: every row, every store, both directions. The month-boundary
# work needs the same question answered CHEAPLY and repeatedly (an attention provider that must not make
# a login slow, and a status strip on the derive console), so this pulls TWO columns instead of six and
# returns counts instead of rows. Same aggregation semantics (voided lines excluded, trans_id grain).
#
# It also queries BOTH period spellings. raw_sales normally stores 'July 2026', but the derivation
# deletes with `.in_('period', _pvariants(period))` precisely because the other spelling turns up — and a
# gap report that missed half the rows because of the spelling would be worse than no report at all.

DERIVE_GAP_ROW_CAP = 120000       # per table; a month that exceeds this is reported as `capped`


def _period_variants(period: str):
    """['July 2026', '2026-07'] — both spellings the database uses, deduped. PURE."""
    plabel = _period_label(period)
    out = [plabel]
    parts = plabel.split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        out.append("%s-%02d" % (parts[1], _MONTHS.index(parts[0]) + 1))
    return list(dict.fromkeys(out))


def _trans_ids(client, table, org_id, variants, cap=DERIVE_GAP_ROW_CAP):
    """The set of non-void trans_ids for one org+period from a sales-shaped table. Bounded + org-scoped.
    Returns (set, capped, lines). A missing table contributes an EMPTY set and capped=False — never a
    false alarm (the caller checks 'is there a feed at all' before it says anything)."""
    ids, start, PAGE, capped, lines = set(), 0, 1000, False, 0
    while True:
        try:
            chunk = (client.schema("commcalc").table(table).select("trans_id,voided")
                     .eq("org_id", org_id).in_("period", variants)
                     .range(start, start + PAGE - 1).execute().data) or []
        except Exception:
            return set(), False, 0
        for r in chunk:
            lines += 1
            if _is_void(r.get("voided")):
                continue
            tid = (r.get("trans_id") or "").strip()
            if tid:
                ids.add(tid)
        if len(chunk) < PAGE:
            break
        start += PAGE
        if start >= cap:
            capped = True
            break
    return ids, capped, lines


def derive_gap(period: str, org_id: str = ORG_ID, client=None):
    """Count-only divergence between the daily feed and the monthly basis for one period, org-scoped.

    `missing_in_monthly` is THE number the month-boundary defect produces: transactions the B2B feed
    delivered that the authoritative raw_sales basis never received, which are invisible to every report
    for that period and would be UNPAID in a recompute of it. Read-only; writes nothing; recomputes
    nothing."""
    client = client or get_supabase()
    variants = _period_variants(period)
    feed, fcap, flines = _trans_ids(client, "daily_sales_feed", org_id, variants)
    monthly, mcap, mlines = _trans_ids(client, "raw_sales", org_id, variants)
    missing = feed - monthly
    return {
        "period": _period_label(period), "org_id": org_id,
        "feed_trans": len(feed), "monthly_trans": len(monthly),
        "feed_lines": flines, "monthly_lines": mlines,
        "has_feed": len(feed) > 0,
        "missing_in_monthly": len(missing),
        "missing_in_daily": len(monthly - feed),
        "sample_missing": sorted(missing)[:25],
        "capped": bool(fcap or mcap),
    }
