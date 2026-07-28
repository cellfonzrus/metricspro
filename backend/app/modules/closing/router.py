"""Daily Closing API Router — /api/v1/closing/*  (DM store-visit Phase 3).

Upload the closing sheet (one row per rep per day), DM evening verification (per-store totals +
missing-rep check vs the schedule), and reconciliation against B2B actual daily sales. Tables live
in commcalc.* (migration 029).
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks, Header
from app.core.database import get_supabase
from app.core.config import settings
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as dateparser
import pandas as pd
import io
import base64
from . import gsheet
from . import ops_chargebacks

router = APIRouter(prefix="/closing", tags=["Daily Closing"])

ORG_ID = "00000000-0000-0000-0000-000000000001"


def require_org(org_id: str):
    """Guard: reject a blank org_id (module-local, matches commcalc/account routers).
    The part-qq tender / attempt endpoints call this; it was never defined here, so they
    raised NameError -> bare 500. (No behaviour change for the valid-org default.)"""
    if not org_id:
        raise HTTPException(400, "org_id required")


def _period_label(date_str):
    """'2026-06-15' → 'June 2026'. raw_sales / the daily_sales_actuals RPC store the month-NAME
    period spelling, so passing 'YYYY-MM' silently matched nothing — both closing reconciliations
    (count + money) returned empty for every store. Convert to the spelling the data is stored under."""
    try:
        return dateparser.parse(str(date_str)).strftime("%B %Y")
    except Exception:
        return str(date_str)[:7]


def sb():
    return get_supabase()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Parsing helpers ──────────────────────────────────────────────────────────────────────
def _money(v) -> float:
    """'$ 1,234.00' / '$ -' / '' → float."""
    s = str(v or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if s in ("", "-", "—"):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _int(v) -> int:
    s = str(v or "").strip().replace(",", "")
    if s in ("", "-"):
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def _date(v) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return dateparser.parse(s).date().isoformat()
    except Exception:
        return None


def _ts(v) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return dateparser.parse(s).isoformat()
    except Exception:
        return None


def _norm(c) -> str:
    return "".join(ch for ch in str(c).lower() if ch.isalnum())


# ── DM-Verify / dashboard filter helpers (retail-ops-14, RULE FIVE parity + de-hard-coded market
#    bucketing). SAP-configurable: no hard-coded market/store list anywhere — these just normalize
#    whatever query-string filter the caller sent against whatever market/store/rep strings the org's
#    OWN data actually has. ──────────────────────────────────────────────────────────────────────
def _market_bucket(m) -> str:
    """A store's resolved market, or the explicit '(no market)' bucket for a blank/unresolved one —
    NEVER an empty string. Filtering must compare against this bucketed value, never the raw string,
    so an unresolved/blank-market store can only be excluded by an EXPLICIT '(no market)' deselection,
    never silently dropped by an exact '' != 'Some Market' mismatch (the root cause of DM Verify
    looking empty for a market-scoped caller — see docs/handoffs/retail-ops.md)."""
    m = (m or "").strip()
    return m if m else "(no market)"


def _csv_set(s) -> set[str]:
    return {x.strip() for x in str(s or "").split(",") if x.strip()}


def _resolve_market_filter(market, markets):
    """Combine the legacy singular `market=` param with the new multi-select `markets=` (comma list) —
    both additive/backward-compatible. Returns None (no filter, nothing dropped) or a CASEFOLDED set to
    compare against `_market_bucket(...).casefold()`."""
    s = _csv_set(markets)
    if not s and market:
        s = {market}
    return {m.casefold() for m in s} if s else None


def _resolve_store_filter(stores):
    """None (no filter) or an UPPERCASED set of store_codes. Never applied to an unresolved row (no
    store_code at all) — that row has no store identity to filter by, and hiding a 'no closing
    submitted' alert behind an unrelated store pick would recreate the exact silent-drop bug."""
    s = _csv_set(stores)
    return {x.upper() for x in s} if s else None


def _resolve_rep_filter(reps):
    """None (no filter) or a CASEFOLDED set of employee names."""
    s = _csv_set(reps)
    return {x.casefold() for x in s} if s else None


def _date_range_list(d_from: str, d_to: str) -> list[str]:
    """Every CALENDAR date in [d_from, d_to] inclusive, ascending — not just dates that already have a
    daily_closing row, because part of the point of the summary view is to surface a store that worked
    but submitted NOTHING that day."""
    try:
        a = dateparser.parse(str(d_from)).date()
        b = dateparser.parse(str(d_to)).date()
    except Exception:
        return [str(d_from)]
    if a > b:
        a, b = b, a
    out, d = [], a
    while d <= b:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# Sheet header → our field. Keyed by normalized header; several aliases per field.
_FIELD_ALIASES = {
    "submitted_at": ["timestamp"],
    "close_date": ["date"],
    "sfid": ["sfid", "salesforceid"],
    "store_name": ["storename", "store"],
    "employee_name": ["employeename", "employee", "salesrep", "rep"],
    "store_cash": ["storecash", "storecash$"],
    "store_cc": ["storecc", "storecc$"],
    "epay_cash": ["epaycash", "epaycash$"],
    "epay_cc": ["epaycc", "epaycc$"],
    "acc_sale": ["accsale", "accsale$", "accessorysale", "accessorysales"],
    "other_account": ["zellcashappotheraccount", "zellecashappother", "otheraccount", "zellecashapp"],
    "upgrade_count": ["upgrade", "upgrade#", "upgrades"],
    "new_line_count": ["newline", "newline#", "newlines"],
    "postpaid_count": ["postpaid", "postpaid#"],
    "envelope_picture": ["envelopepicture", "envelope", "envelopephoto"],
    "remarks": ["remarks", "remark", "notes"],
}


def _build_colmap(df) -> dict:
    norm_to_actual = {_norm(c): c for c in df.columns}
    out = {}
    for field, aliases in _FIELD_ALIASES.items():
        for a in aliases:
            if _norm(a) in norm_to_actual:
                out[field] = norm_to_actual[_norm(a)]
                break
    return out


def _store_resolver(client, org_id):
    sm = (client.schema("commcalc").table("store_mapping")
          .select("salesforce_id,store_code,store_address").eq("org_id", org_id).execute().data) or []
    by_sfid = {}
    for r in sm:
        sid = (r.get("salesforce_id") or "").strip()
        if sid:
            by_sfid[sid] = r
    return by_sfid


# ── Upload the closing sheet ──────────────────────────────────────────────────────────────
@router.post("/upload")
async def upload_closing(file: UploadFile = File(...), org_id: str = ORG_ID):
    contents = await file.read()
    try:
        if (file.filename or "").lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(contents), dtype=str)
    except Exception as e:
        raise HTTPException(400, f"Could not read the file: {e}")
    return _ingest_dataframe(sb(), org_id, df)


def _ingest_dataframe(client, org_id: str, df) -> dict:
    """Parse a closing DataFrame (from the sheet upload OR the Google auto-sweep) into
    daily_closing rows, then idempotently replace the sheet-sourced rows for the covered dates
    (manual in-app rows are preserved)."""
    df = df.fillna("")
    cm = _build_colmap(df)
    if "close_date" not in cm or "sfid" not in cm:
        raise HTTPException(400, "This doesn't look like the closing sheet — need at least a Date "
                                 f"and SFID column. Found: {list(df.columns)}")
    by_sfid = _store_resolver(client, org_id)

    def g(row, field):
        col = cm.get(field)
        return row[col] if col else ""

    rows, dates = [], set()
    for _, r in df.iterrows():
        d = _date(g(r, "close_date"))
        if not d:
            continue
        sfid = str(g(r, "sfid")).strip()
        sm_row = by_sfid.get(sfid, {})
        rows.append({
            "org_id": org_id, "period": d[:7], "close_date": d,
            "submitted_at": _ts(g(r, "submitted_at")),
            "sfid": sfid, "store_name": str(g(r, "store_name")).strip(),
            "store_code": sm_row.get("store_code"), "store_address": sm_row.get("store_address"),
            "employee_name": str(g(r, "employee_name")).strip(),
            "store_cash": _money(g(r, "store_cash")), "store_cc": _money(g(r, "store_cc")),
            "epay_cash": _money(g(r, "epay_cash")), "epay_cc": _money(g(r, "epay_cc")),
            "acc_sale": _money(g(r, "acc_sale")), "other_account": _money(g(r, "other_account")),
            "upgrade_count": _int(g(r, "upgrade_count")), "new_line_count": _int(g(r, "new_line_count")),
            "postpaid_count": _int(g(r, "postpaid_count")),
            "envelope_picture": str(g(r, "envelope_picture")).strip(),
            "remarks": str(g(r, "remarks")).strip(), "source": "sheet_upload",
        })
        dates.add(d)

    if not rows:
        raise HTTPException(400, "No rows with a valid Date found.")

    # Idempotent: wipe sheet-sourced rows for the covered dates, keep manual rows, then insert.
    (client.schema("commcalc").table("daily_closing").delete()
     .eq("org_id", org_id).eq("source", "sheet_upload").in_("close_date", sorted(dates)).execute())
    for i in range(0, len(rows), 500):
        client.schema("commcalc").table("daily_closing").insert(rows[i:i + 500]).execute()

    unresolved = sum(1 for r in rows if not r["store_code"])
    return {"rows_saved": len(rows), "dates": sorted(dates), "unresolved_stores": unresolved}


# ── Available dates (for the picker) ──────────────────────────────────────────────────────
@router.get("/dates")
def closing_dates(period: str = None, org_id: str = ORG_ID):
    q = sb().schema("commcalc").table("daily_closing").select("close_date").eq("org_id", org_id)
    if period:
        q = q.eq("period", period)
    rows = q.execute().data or []
    counts = {}
    for r in rows:
        d = r.get("close_date")
        if d:
            counts[d] = counts.get(d, 0) + 1
    return [{"date": d, "rows": counts[d]} for d in sorted(counts, reverse=True)]


# ── Raw rows (in-app entry / browsing) ────────────────────────────────────────────────────
@router.get("/days")
def closing_rows(date: str = None, store_code: str = None, date_from: str = None,
                 date_to: str = None, org_id: str = ORG_ID):
    q = sb().schema("commcalc").table("daily_closing").select("*").eq("org_id", org_id)
    if date:      q = q.eq("close_date", date)
    if store_code: q = q.eq("store_code", store_code)
    if date_from: q = q.gte("close_date", date_from)
    if date_to:   q = q.lte("close_date", date_to)
    return q.order("close_date", desc=True).limit(2000).execute().data or []


# ── Dashboard detail (retail-ops-13, OWNER DIRECTIVE 2026-07-27): EVERY submitted column, one row per
#    rep-submission, with the standard filter bar (date range / store / market / rep) + export. READ
#    ONLY — reuses the exact existing gate/recon helpers (_b2b_day / _rep_b2b / _money_issues) rather
#    than re-implementing them, so this view can never diverge from the real close-gate math and never
#    writes anything. Secrecy boundary is preserved: the coarse gate_status (ok/flagged/blocked/
#    recon_pending) is always included, but the DOLLAR reasons/B2B figures (gate_reasons/b2b_cash/
#    b2b_card) are populated only for a caller who passes the existing _can_mgmt_review gate — the same
#    boundary /closing/management and /closing/attempts already enforce, so a rep or DM viewing the main
#    dashboard still never sees the true system figure, exactly like the 3-try submit flow already keeps
#    secret today.
_SUBMISSIONS_MAX_ROWS = 8000
_SUBMISSIONS_MAX_STATUS_DATES = 45   # bound _b2b_day replays on a very wide date range (mirrors the
                                     # existing closing_stale_stores provider's "bounded to at most 14" pattern


def _row_display_tenders(r: dict) -> dict:
    """Best-effort per-tender breakdown for ONE submitted row. A row with any t_* column populated
    (mig103+, both manual and sheet-sourced once the tenant is on the new form) reads those directly.
    A pre-mig103 sheet_upload row (no t_* at all) falls back to the legacy store_cash/store_cc/
    epay_cash/epay_cc/other_account split — the EXACT SAME fallback create_row already applies at
    write time (see POST /row above) — so this is a pure read-time re-derivation, not new math."""
    has_t = any(r.get(k) is not None for k in
                ("t_cash", "t_credit", "t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima"))
    if has_t:
        return {"cash": _f(r.get("t_cash")), "credit": _f(r.get("t_credit")), "ext_cc": _f(r.get("t_ext_cc")),
                "gift": _f(r.get("t_gift")), "store_acct": _f(r.get("t_store_acct")),
                "zelle": _f(r.get("t_zelle")), "acima": _f(r.get("t_acima"))}
    return {"cash": round(_f(r.get("store_cash")) + _f(r.get("epay_cash")), 2),
            "credit": round(_f(r.get("store_cc")) + _f(r.get("epay_cc")), 2),
            "ext_cc": 0.0, "gift": 0.0, "store_acct": 0.0,
            "zelle": _f(r.get("other_account")), "acima": 0.0}


@router.get("/submissions")
def closing_submissions(date_from: str = None, date_to: str = None,
                        authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Every daily_closing row (one per rep-submission) in [date_from, date_to] (inclusive; defaults to
    the current month), with ALL submitted columns + resolved market + DM-verify status + a re-derived
    close-gate status badge. Powers the Daily Closing dashboard's detail table."""
    client = sb()
    today = _biz_today_iso()
    if not date_from and not date_to:
        date_from, date_to = today[:8] + "01", today
    else:
        date_to = date_to or today
        date_from = date_from or (date_to[:8] + "01")
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    rows = (client.schema("commcalc").table("daily_closing").select("*").eq("org_id", org_id)
            .gte("close_date", date_from).lte("close_date", date_to)
            .order("close_date", desc=True).limit(_SUBMISSIONS_MAX_ROWS).execute().data) or []
    truncated = len(rows) >= _SUBMISSIONS_MAX_ROWS

    # Market resolution (store_code -> market), same union source as GET /closing/stores.
    try:
        store_rows = (client.schema("storeops").table("stores").select("store_code,market")
                      .eq("org_id", org_id).execute().data) or []
    except Exception:
        store_rows = []
    market_by_code = {s.get("store_code"): (s.get("market") or "").strip()
                      for s in store_rows if s.get("store_code")}

    dates = sorted({r.get("close_date") for r in rows if r.get("close_date")})

    # DM verification, per (store_code, close_date).
    vers = {}
    if dates:
        try:
            vrows = (client.schema("commcalc").table("daily_closing_verification").select("*")
                     .eq("org_id", org_id).in_("close_date", dates).execute().data) or []
        except Exception:
            vrows = []
        for v in vrows:
            vers[(v.get("store_code"), v.get("close_date"))] = v

    # Custom tender/count-field labels (best-effort, ONE extra read each — not per row).
    from . import count_config, tender_config
    try:
        clabels = {d.get("field_key"): (d.get("label") or d.get("field_key"))
                   for d in count_config.load_count_config(client, org_id)}
    except Exception:
        clabels = {}
    try:
        _tdefs, _tmaps = tender_config.load_tender_config(client, org_id)
        tlabels = {d.get("tender_key"): (d.get("label") or d.get("tender_key")) for d in _tdefs}
    except Exception:
        tlabels = {}

    can_review = _can_mgmt_review(_caller_perms(client, authorization))

    # Gate-status re-derivation: replay each distinct close_date's B2B day ONCE (cached), capped so a
    # huge date range can't fire dozens of heavy sales-day queries per row.
    # Prioritize the MOST RECENT distinct dates when capped (an admin widening the range still gets
    # today's/this-week's rows computed first; older ones degrade to 'not_computed' rather than guessing).
    status_dates = sorted(dates, reverse=True)[:_SUBMISSIONS_MAX_STATUS_DATES]
    status_capped = len(dates) > _SUBMISSIONS_MAX_STATUS_DATES
    day_cache = {}
    for d in status_dates:
        try:
            day_cache[d] = _b2b_day(client, org_id, d)
        except Exception:
            day_cache[d] = None

    out = []
    for r in rows:
        code = r.get("store_code")
        d = r.get("close_date")
        mkt = market_by_code.get(code) or ""
        tenders = _row_display_tenders(r)
        declared_cash = tenders["cash"]
        declared_credit = round(tenders["credit"] + tenders["ext_cc"], 2)

        gate_status, reasons, b2b_cash, b2b_card = "not_computed", [], None, None
        day = day_cache.get(d)
        if day is not None:
            if not day.get("has_data"):
                gate_status = "recon_pending"
            else:
                repb = _rep_b2b(day, code, r.get("employee_name") or "") if code else None
                if repb is None or not repb.get("tenders_available", True):
                    gate_status = "recon_pending"
                else:
                    issues = _money_issues(declared_cash, declared_credit, repb["cash"], repb["card"])
                    blocks = [i["reason"] for i in issues if i["severity"] == "block"]
                    flags = [i["reason"] for i in issues if i["severity"] == "flag"]
                    gate_status = "blocked" if blocks else ("flagged" if flags else "ok")
                    reasons = blocks + flags
                    b2b_cash, b2b_card = repb["cash"], repb["card"]

        custom_t = r.get("tenders") if isinstance(r.get("tenders"), dict) else {}
        custom_tenders_display = ", ".join(f"{tlabels.get(k, k)}: {_usd(_f(v))}" for k, v in custom_t.items() if _f(v))
        row_counts = r.get("counts") if isinstance(r.get("counts"), dict) else {}
        custom_counts_display = ", ".join(f"{clabels.get(k, k)}: {v}" for k, v in row_counts.items()
                                          if k not in count_config.STD_FIELD_KEYS)

        ver = vers.get((code, d)) or {}
        out.append({
            "id": r.get("id"), "close_date": d, "submitted_at": r.get("submitted_at"),
            "store_code": code, "store_address": r.get("store_address") or r.get("store_name") or code or "—",
            "market": mkt or "(no market)",
            "employee_name": r.get("employee_name"), "source": r.get("source"),
            "t_cash": tenders["cash"], "t_credit": tenders["credit"], "t_ext_cc": tenders["ext_cc"],
            "t_gift": tenders["gift"], "t_store_acct": tenders["store_acct"], "t_zelle": tenders["zelle"],
            "t_acima": tenders["acima"], "custom_tenders": custom_tenders_display,
            "total_collected": round(sum(tenders.values()), 2),
            "acc_sale": _f(r.get("acc_sale")),
            "epay_on_cash": _f(r.get("epay_on_cash")), "epay_on_credit": _f(r.get("epay_on_credit")),
            "epay_on_acima": _f(r.get("epay_on_acima")),
            "upgrade_count": _int(r.get("upgrade_count")), "new_line_count": _int(r.get("new_line_count")),
            "postpaid_count": _int(r.get("postpaid_count")), "custom_counts": custom_counts_display,
            "expense_amount": _f(r.get("expense_amount")), "expense_description": r.get("expense_description"),
            "expense_approved": bool(r.get("expense_approved")),
            "gate_status": gate_status,
            "gate_reasons": (reasons if can_review else []),
            "b2b_cash": (b2b_cash if can_review else None), "b2b_card": (b2b_card if can_review else None),
            "attempts": _int(r.get("attempts")) or 1, "auto_accepted": bool(r.get("auto_accepted")),
            "mgmt_flag": bool(r.get("mgmt_flag")),
            "released_at": r.get("released_at"), "released_by": r.get("released_by"),
            "correction_count": _int(r.get("correction_count")),
            "dm_verified": bool(ver.get("verified")), "dm_verified_by": ver.get("verified_by"),
            "dm_verified_at": ver.get("verified_at"),
            # Reference only — the raw storage path, never a signed URL (no per-row network round trip
            # to Storage on a list endpoint that can return thousands of rows; nothing in the dashboard
            # or its exports renders it as a clickable image). In-app photo viewing for a specific
            # store-day continues to live on /closing/verify and /closing/management, unchanged.
            "envelope_picture": r.get("envelope_picture"),
            "remarks": r.get("remarks"),
        })

    return {"rows": out, "date_from": date_from, "date_to": date_to, "truncated": truncated,
            "status_capped": status_capped, "status_dates_computed": len(status_dates),
            "status_dates_total": len(dates), "can_review": can_review}


# ── Stores (for the rep submission form's store picker) ────────────────────────────────────
@router.get("/stores")
def closing_stores(org_id: str = ORG_ID):
    """Store options for the in-app closing form — SFID + canonical store + market."""
    client = sb()
    sm = (client.schema("commcalc").table("store_mapping")
          .select("salesforce_id,store_code,store_address").eq("org_id", org_id).execute().data) or []
    stores = (client.schema("storeops").table("stores").select("store_code,address,market").eq("org_id", org_id).execute().data) or []
    mkt = {s.get("store_code"): s.get("market") for s in stores if s.get("store_code")}
    addr = {s.get("store_code"): s.get("address") for s in stores if s.get("store_code")}
    by_code = {}
    for r in sm:
        code = (r.get("store_code") or "").strip()
        if not code:
            continue
        by_code[code] = {"sfid": (r.get("salesforce_id") or "").strip(), "store_code": code,
                         "store_address": r.get("store_address") or addr.get(code) or code,
                         "market": mkt.get(code) or ""}
    # A store created under StoreOps Admin (storeops.stores) but not yet in the commcalc store_mapping
    # must STILL appear in Daily Closing — union it in so new stores propagate here immediately.
    for s in stores:
        code = (s.get("store_code") or "").strip()
        if code and code not in by_code:
            by_code[code] = {"sfid": "", "store_code": code,
                             "store_address": s.get("address") or code, "market": s.get("market") or ""}
    out = list(by_code.values())
    out.sort(key=lambda s: str(s.get("store_address") or ""))
    return out


# ── Monthly rollup (dashboard summaries: per-store + per-rep over a period) — retail-ops-14
#    (OWNER DIRECTIVE 2026-07-28): gained date_from/date_to (backward-compatible with period=YYYY-MM,
#    which still wins if both are sent), stores=/reps= (comma lists, additive to market=/markets=), and
#    bucket-aware market matching so an unresolved/blank-market store can never be silently dropped by a
#    market filter (mirrors the /closing/summary fix — same root cause, same "(no market)" bucket). ──
@router.get("/rollup")
def closing_rollup(period: str = None, date_from: str = None, date_to: str = None,
                   market: str = None, markets: str = None, stores: str = None, reps: str = None,
                   authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Aggregate daily_closing for a YYYY-MM period OR an explicit [date_from, date_to] range into
    per-store and per-rep money + counts + days-submitted, plus DM verification coverage. Powers the
    Daily Closing dashboard (tiles + By-store/By-rep tabs)."""
    if not period and not date_from and not date_to:
        raise HTTPException(400, "period (YYYY-MM) or date_from/date_to required")
    client = sb()
    q = client.schema("commcalc").table("daily_closing").select("*").eq("org_id", org_id)
    if period:
        q = q.eq("period", period)
    else:
        d_from = date_from or date_to
        d_to = date_to or date_from
        # Defensive parse (Gate-1 NIT-3, 2026-07-28) — mirrors _date_range_list's use of dateparser
        # before ANY value reaches PostgREST: an un-validated garbage string in gte()/lte() against a
        # date column raises inside the Supabase client (an uncaught 500), not a clean 400. Validate +
        # normalize to YYYY-MM-DD here so a bad date_from/date_to fails loudly with a real 4xx instead.
        try:
            d_from = dateparser.parse(str(d_from)).date().isoformat()
            d_to = dateparser.parse(str(d_to)).date().isoformat()
        except Exception:
            raise HTTPException(400, "date_from/date_to must be valid dates (YYYY-MM-DD)")
        if d_from > d_to:
            d_from, d_to = d_to, d_from
        q = q.gte("close_date", d_from).lte("close_date", d_to)
        date_from, date_to = d_from, d_to
    rows = (q.limit(50000).execute().data) or []
    store_rows = (client.schema("storeops").table("stores").select("store_code,address,market").eq("org_id", org_id).execute().data) or []
    store_meta = {s.get("store_code"): s for s in store_rows if s.get("store_code")}

    market_set = _resolve_market_filter(market, markets)
    store_set = _resolve_store_filter(stores)
    rep_set = _resolve_rep_filter(reps)

    # Verification coverage — filter by period prefix (period mode) or the explicit range (range mode).
    # "period + '-31'" would make an invalid date (e.g. 2026-06-31) that Postgres rejects on the date
    # cast, so the range compare stays in Python too; the verification table is small either way.
    vers = (client.schema("commcalc").table("daily_closing_verification")
            .select("store_code,close_date,verified").eq("org_id", org_id).execute().data) or []
    if period:
        verified_keys = {(v.get("store_code"), str(v.get("close_date"))) for v in vers
                         if v.get("verified") and str(v.get("close_date") or "").startswith(period)}
    else:
        verified_keys = {(v.get("store_code"), str(v.get("close_date"))) for v in vers
                         if v.get("verified") and date_from <= str(v.get("close_date") or "") <= date_to}

    MONEY = ("store_cash", "store_cc", "epay_cash", "epay_cc", "acc_sale", "other_account")
    COUNT = ("upgrade_count", "new_line_count", "postpaid_count")

    def blank():
        d = {k: 0.0 for k in MONEY}
        d.update({k: 0 for k in COUNT})
        d.update({"rows": 0, "_days": set()})
        return d

    by_store, by_rep, grand = {}, {}, blank()
    kept_rows = []
    for r in rows:
        raw_code = r.get("store_code")
        key = raw_code or f"name:{r.get('store_name') or '—'}"
        meta = store_meta.get(raw_code, {}) if raw_code else {}
        mk_bucket = _market_bucket(meta.get("market"))
        if market_set is not None and mk_bucket.casefold() not in market_set:
            continue
        # A store filter never touches a row whose store didn't resolve to a real storeops.stores
        # record (no store_code at all, OR a code no longer/not yet in the roster) — it has no store
        # identity a picker could ever offer, and hiding it behind an unrelated store pick would
        # recreate the exact silent-drop bug this package fixes.
        if store_set is not None and meta and raw_code.upper() not in store_set:
            continue
        if rep_set is not None and (r.get("employee_name") or "").strip().casefold() not in rep_set:
            continue
        kept_rows.append(r)
        s_ = by_store.setdefault(key, {**blank(), "store_code": raw_code,
                                       "store_address": meta.get("address") or r.get("store_address"),
                                       "store_name": r.get("store_name"), "market": mk_bucket})
        rep_key = f"{(r.get('employee_name') or '—').strip()}||{key}"
        r_ = by_rep.setdefault(rep_key, {**blank(), "employee_name": (r.get("employee_name") or "—").strip(),
                                         "store_code": raw_code,
                                         "store_address": meta.get("address") or r.get("store_address"), "market": mk_bucket})
        for agg in (s_, r_, grand):
            for k in MONEY:
                agg[k] = round(agg[k] + _f(r.get(k)), 2)
            for k in COUNT:
                agg[k] += int(r.get(k) or 0)
            agg["rows"] += 1
            if r.get("close_date"):
                agg["_days"].add(str(r.get("close_date")))

    def finalize(d):
        d = {k: v for k, v in d.items() if k != "_days"} | {"days": len(d["_days"])}
        return d

    submitted_keys = {(r.get("store_code"), str(r.get("close_date"))) for r in kept_rows if r.get("store_code")}
    bs = sorted((finalize(v) for v in by_store.values()),
                key=lambda s: str(s.get("store_address") or s.get("store_name") or ""))
    br = sorted((finalize(v) for v in by_rep.values()), key=lambda s: -s.get("rows", 0))
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        bs = [s for s in bs if in_keyset(ks, s.get("store_code"), s.get("store_address"))]
        br = [s for s in br if in_keyset(ks, s.get("store_code"), s.get("store_address"))]
    return {
        "period": period, "date_from": date_from, "date_to": date_to,
        "by_store": bs, "by_rep": br, "totals": finalize(grand),
        "verified_keys": len(verified_keys & submitted_keys), "submitted_keys": len(submitted_keys),
    }


# ── DM evening verification view: per-store totals + missing reps + B2B recon ─────────────
# retail-ops-14 (OWNER DIRECTIVE 2026-07-28): the actual per-date computation is now
# `_closing_summary_for_date` so `/closing/summary` can call it once per date for an optional
# date_from/date_to range (the single-`date=` call path is UNCHANGED math, just parameterized —
# every dollar figure below is byte-identical to before this package). Gained bucket-aware
# market/store/rep filtering (never silently drops an unresolved/blank-market store — see
# `_market_bucket`) and additive per-tender detail (ACIMA + the 3 tenders previously invisible here,
# custom tenders/counts, and a re-derived close-gate status per rep — REUSES `_b2b_day`/`_rep_b2b`/
# `_money_issues` verbatim, the exact same functions the real close gate and `/closing/submissions`
# already use, never re-implemented) so the DM Verify page can show everything the dashboard's detail
# tab shows for the same store/day. The money-secrecy boundary is unchanged: dollar reasons/B2B
# figures only populate for a `_can_mgmt_review` caller (company-wide/super-admin/explicit grant);
# a DM/store-scope viewer sees the same coarse status with an empty reasons list, same as
# /closing/submissions and /closing/management already do.
_SUMMARY_MAX_RANGE_DATES = 14   # bounded like closing_stale_stores' "at most 14" pattern — this
                                # endpoint does much heavier per-day work (schedules, timelog, B2B
                                # money+counts, X-report, verification, + now the gate replay) than
                                # closing_submissions' single _b2b_day call.
_GATE_RANK = {"blocked": 4, "flagged": 3, "recon_pending": 2, "not_computed": 1, "ok": 0}


def _closing_summary_for_date(client, org_id, date, market_set, store_set, rep_set, tolerance, can_review):
    """One day's DM-Verify per-store summary. Returns a list of store dicts, each tagged
    close_date=date. market_set/store_set/rep_set (see _resolve_*_filter) are None (unrestricted) or
    a normalized set narrowing which store CARDS come back — filtering never touches the money math,
    it only decides which already-computed cards are included."""
    rows = (client.schema("commcalc").table("daily_closing").select("*")
            .eq("org_id", org_id).eq("close_date", date).execute().data) or []

    # Configurable activation-count fields (mig 501): empty config -> the hardcoded 3 fields
    # (upgrade_count/new_line_count/postpaid_count), so an un-opted tenant is byte-identical.
    from . import count_config, tender_config
    _cdefs = count_config.load_count_config(client, org_id)
    _ckeys, _clabels, _crclass = count_config.count_axis(_cdefs)
    # Configurable tender labels (mig 111) — for the custom-tenders display string only (same source
    # /closing/submissions already uses); never affects the close gate.
    try:
        _tdefs, _tmaps = tender_config.load_tender_config(client, org_id)
        tlabels = {d.get("tender_key"): (d.get("label") or d.get("tender_key")) for d in _tdefs}
    except Exception:
        tlabels = {}

    # Store + market context.
    stores = (client.schema("storeops").table("stores")
              .select("store_code,address,market").eq("org_id", org_id).execute().data) or []
    store_meta = {s.get("store_code"): s for s in stores if s.get("store_code")}

    # Scheduled reps that day (to flag who didn't submit).
    shifts = (client.schema("storeops").table("shifts").select("store_code,employee_name")
              .eq("org_id", org_id).eq("is_deleted", False).eq("shift_date", date).execute().data) or []
    sched_by_store = {}
    for s in shifts:
        sc = s.get("store_code")
        nm = (s.get("employee_name") or "").strip()
        if sc and nm:
            sched_by_store.setdefault(sc, set()).add(nm)

    # Who ACTUALLY worked each store (clock-in ∪ B2B sales-by-rep) — the closing checks reality, not
    # the roster. Plus the tenant closing_mode (per_rep = every worker owes a closing; one_closing =
    # only the assigned closer does, and tallies the store's cash) + the assigned closers.
    who = _who_worked_by_store(client, org_id, date)
    tcfg = (client.schema("storeops").table("tenants").select("closing_mode")
            .eq("org_id", org_id).limit(1).execute().data or [{}])
    closing_mode = (tcfg[0].get("closing_mode") if tcfg else None) or "per_rep"
    try:
        closer_rows = (client.schema("storeops").table("store_closer")
                       .select("store_code,employee_name").eq("org_id", org_id).execute().data) or []
    except Exception:
        closer_rows = []
    closer_by_store = {c.get("store_code"): (c.get("employee_name") or "").strip()
                       for c in closer_rows if c.get("store_code")}

    # B2B actual daily sales per store — from the UNIFIED source (feed-first for the open month) with
    # the shared contract-type classifier + configurable accessory, so July populates and it agrees with
    # the Sales Report / Action Plan (no rigid daily_sales_actuals RPC).
    b2b = {}
    try:
        b2b = _b2b_counts_by_store(client, org_id, date)
    except Exception as e:
        print("closing B2B recon count failed:", e)

    # B2B MONEY actuals for that day (accessory gross, cash vs card by tender) → store money-recon.
    try:
        b2b_money = _b2b_money_by_store(client, org_id, date)
    except Exception as e:
        print("closing B2B money recon failed:", e)
        b2b_money = {}
    # Authoritative cash/card split comes from the POS X-REPORT (pos_tender_summary), NOT the sales
    # feed (which omits Tender Type). When the X-report is imported for the day, it overrides the
    # feed tenders in the money recon below.
    try:
        xreport_tenders = _xreport_tenders_by_store(client, org_id, date)
    except Exception as e:
        print("closing X-report tender load failed:", e)
        xreport_tenders = {}
    # Distinguish "no X-report EVER for this tenant" (config/delivery — mailbox rule or the b2bsoft
    # schedule never set up) from "just today's file hasn't landed yet" (2026-07-15 luxelink diagnosis)
    # -> a sharper, more actionable honest-empty message on the money_recon note below.
    x_report_ever = bool(xreport_tenders)
    if not x_report_ever:
        try:
            x_report_ever = bool((client.schema("commcalc").table("pos_tender_summary").select("close_date")
                                  .eq("org_id", org_id).limit(1).execute().data) or [])
        except Exception:
            x_report_ever = False

    # Verifications for that day.
    vers = (client.schema("commcalc").table("daily_closing_verification").select("*")
            .eq("org_id", org_id).eq("close_date", date).execute().data) or []
    ver_by_store = {v.get("store_code"): v for v in vers}

    # Close-gate replay (READ ONLY, reuses the exact existing helpers — never redefined): lets each rep
    # row carry the SAME block/flag/ok/recon_pending status the real 3-try close gate and
    # /closing/submissions already compute, instead of DM Verify's own separate (and less precise, no
    # block-vs-flag distinction) money_recon tolerance check below. A failure here degrades to
    # "not_computed" per rep, never breaks the rest of the page.
    try:
        day_data = _b2b_day(client, org_id, date)
    except Exception as e:
        print("closing summary gate-status _b2b_day failed:", e)
        day_data = None

    def _tender_and_gate(code, rp):
        dt = _row_display_tenders(rp)
        d_cash, d_credit = dt["cash"], round(dt["credit"] + dt["ext_cc"], 2)
        gate_status, gate_reasons, g_cash, g_card = "not_computed", [], None, None
        if day_data is not None:
            if not day_data.get("has_data"):
                gate_status = "recon_pending"
            else:
                repb = _rep_b2b(day_data, code, (rp.get("employee_name") or ""))
                if repb is None or not repb.get("tenders_available", True):
                    gate_status = "recon_pending"
                else:
                    issues = _money_issues(d_cash, d_credit, repb["cash"], repb["card"], tolerance)
                    blocks = [i["reason"] for i in issues if i["severity"] == "block"]
                    flags = [i["reason"] for i in issues if i["severity"] == "flag"]
                    gate_status = "blocked" if blocks else ("flagged" if flags else "ok")
                    gate_reasons = blocks + flags
                    g_cash, g_card = repb["cash"], repb["card"]
        return dt, {"status": gate_status, "reasons": (gate_reasons if can_review else []),
                    "b2b_cash": (g_cash if can_review else None), "b2b_card": (g_card if can_review else None)}

    def _rep_custom_displays(rp):
        ct = rp.get("tenders") if isinstance(rp.get("tenders"), dict) else {}
        ct_display = ", ".join(f"{tlabels.get(k, k)}: {_usd(_f(v))}" for k, v in ct.items() if _f(v))
        rc = rp.get("counts") if isinstance(rp.get("counts"), dict) else {}
        rc_display = ", ".join(f"{_clabels.get(k, k)}: {v}" for k, v in rc.items() if k not in count_config.STD_FIELD_KEYS)
        return ct_display, rc_display

    # Group rows by store (code if resolved, else name).
    groups = {}
    for r in rows:
        key = r.get("store_code") or f"name:{r.get('store_name') or '—'}"
        groups.setdefault(key, []).append(r)

    out = []
    for key, reps in groups.items():
        code = None if key.startswith("name:") else key
        meta = store_meta.get(code, {}) if code else {}
        mkt = meta.get("market") or ""
        mkt_bucket = _market_bucket(mkt)
        # Bucket-aware: an unresolved/blank-market store is NEVER silently dropped by a market filter —
        # it can only be excluded if the caller explicitly deselects the "(no market)" bucket.
        if market_set is not None and mkt_bucket.casefold() not in market_set:
            continue
        # A store filter never touches a row whose store didn't resolve to a real storeops.stores
        # record (no store_code at all, OR a code no longer/not yet in the roster) — it has no store
        # identity a picker could ever offer, and hiding a "did this even close" alert behind an
        # unrelated store pick would recreate the exact silent-drop bug this package fixes.
        if store_set is not None and meta and code.upper() not in store_set:
            continue
        totals = {
            "store_cash": round(sum(_f(r["store_cash"]) for r in reps), 2),
            "store_cc": round(sum(_f(r["store_cc"]) for r in reps), 2),
            "epay_cash": round(sum(_f(r["epay_cash"]) for r in reps), 2),
            "epay_cc": round(sum(_f(r["epay_cc"]) for r in reps), 2),
            "acc_sale": round(sum(_f(r["acc_sale"]) for r in reps), 2),
            "other_account": round(sum(_f(r["other_account"]) for r in reps), 2),
            "upgrade_count": sum(int(r.get("upgrade_count") or 0) for r in reps),
            "new_line_count": sum(int(r.get("new_line_count") or 0) for r in reps),
            "postpaid_count": sum(int(r.get("postpaid_count") or 0) for r in reps),
            "rep_count": len(reps),
            # Config-driven count fields (mig 501) — the DM verify view + submit form render off this
            # list instead of the 3 hardcoded keys above (which stay populated for backward-compat).
            "counts": [{"field_key": k, "label": _clabels.get(k, k),
                        "value": sum(count_config.row_value(r, k) for r in reps)} for k in _ckeys],
        }
        # Per-tender breakdown (mig 111/103) — ADDITIVE, byte-identical legacy fields above untouched.
        # Includes ACIMA, which was previously invisible on this page entirely (it's excluded from
        # store_cash/store_cc/other_account on write — see create_row — because it's financing, not
        # cash/card collected; it still needs to be VISIBLE to a DM verifying the night's totals).
        _rep_computed = [_tender_and_gate(code, rp) for rp in reps]
        _tender_rows = [dt for dt, _g in _rep_computed]
        totals["t_cash"] = round(sum(t["cash"] for t in _tender_rows), 2)
        totals["t_credit"] = round(sum(t["credit"] for t in _tender_rows), 2)
        totals["t_ext_cc"] = round(sum(t["ext_cc"] for t in _tender_rows), 2)
        totals["t_gift"] = round(sum(t["gift"] for t in _tender_rows), 2)
        totals["t_store_acct"] = round(sum(t["store_acct"] for t in _tender_rows), 2)
        totals["t_zelle"] = round(sum(t["zelle"] for t in _tender_rows), 2)
        totals["t_acima"] = round(sum(t["acima"] for t in _tender_rows), 2)
        totals["total_collected"] = round(sum(sum(t.values()) for t in _tender_rows), 2)
        _custom_sum = {}
        for r in reps:
            ct = r.get("tenders") if isinstance(r.get("tenders"), dict) else {}
            for k, v in ct.items():
                _custom_sum[k] = _custom_sum.get(k, 0.0) + _f(v)
        totals["custom_tenders"] = [{"key": k, "label": tlabels.get(k, k), "value": round(v, 2)}
                                    for k, v in _custom_sum.items() if v]
        rep_gate_statuses = [g["status"] for _dt, g in _rep_computed]
        store_gate_status = max(rep_gate_statuses, key=lambda s: _GATE_RANK.get(s, 0)) if rep_gate_statuses else None

        submitted_names = {(r.get("employee_name") or "").strip().lower() for r in reps}
        submitted_set = {sn for sn in submitted_names if sn}
        scheduled = sched_by_store.get(code, set()) if code else set()

        # Who actually worked = clocked-in ∪ sold-in-B2B ∪ submitted-a-closing (submitting implies work).
        ww = who.get(code, {}) if code else {}
        clocked = set(ww.get("clocked_in", set()))
        sold = set(ww.get("sold", set()))
        submitted_display = {(r.get("employee_name") or "").strip() for r in reps if (r.get("employee_name") or "").strip()}
        worked = {n for n in (clocked | sold | submitted_display) if n}

        def _submitted(nm):
            return any(_name_match(nm, sn) for sn in submitted_set)

        # missing_reps = who OWES a closing but hasn't submitted. In per_rep mode that's every worker;
        # in one_closing mode only the assigned closer owes it (they tally the whole store's cash).
        if closing_mode == "one_closing":
            closer = closer_by_store.get(code) if code else None
            owes = {closer} if closer else worked
        else:
            owes = worked
        missing = sorted({nm for nm in owes if nm and not _submitted(nm)})

        # Scheduled but didn't work (roster no-show, e.g. someone marked on the schedule who never
        # showed) — surfaced separately so it's visible WITHOUT dunning them for a missing closing.
        scheduled_no_show = sorted({nm for nm in scheduled
                                    if not any(_name_match(nm, w) for w in worked) and not _submitted(nm)})
        # Worked but not on the roster.
        worked_unscheduled = sorted({nm for nm in worked
                                     if not any(_name_match(nm, s) for s in scheduled)})
        # Cross-login: rang B2B sales but never clocked in here → likely worked under another login.
        # Attach the login(s) each such name transacted under (from the B2B report) for the recon.
        logins = ww.get("logins", {})
        cross_login = []
        for nm in sold:
            if not any(_name_match(nm, c) for c in clocked):
                cross_login.append({"salesperson": nm, "logins": sorted(logins.get(nm, set()))})
        cross_login.sort(key=lambda x: x["salesperson"])

        # A rep filter narrows which STORE CARDS show (any selected rep submitted, worked, or is
        # missing here) — it never changes which rep ROWS contribute to the store totals above.
        if rep_set is not None:
            involved_cf = {n.casefold() for n in submitted_display} | {n.casefold() for n in worked} | {n.casefold() for n in missing}
            if not (involved_cf & rep_set):
                continue

        bb = b2b.get(code, {}) if code else {}
        # Sum by recon_class (config-driven, mig 501) instead of the 2 hardcoded field names — with an
        # empty config _ckeys/_crclass fall back to the hardcoded 3, so this is unchanged by default.
        closing_acts = sum(sum(count_config.row_value(r, k) for r in reps) for k in _ckeys if _crclass.get(k) == "activation")
        closing_upg = sum(sum(count_config.row_value(r, k) for r in reps) for k in _ckeys if _crclass.get(k) == "upgrade")
        recon = None
        if bb:
            act_var = closing_acts - int(bb.get("activations", 0))
            upg_var = closing_upg - int(bb.get("upgrades", 0))
            recon = {
                "b2b_activations": int(bb.get("activations", 0)),
                "b2b_upgrades": int(bb.get("upgrades", 0)),
                "b2b_acc_gp": round(float(bb.get("acc_gp", 0)), 2),
                "closing_activations": closing_acts, "closing_upgrades": closing_upg,
                "act_var": act_var, "upg_var": upg_var,
                "discrepancy": (act_var != 0 or upg_var != 0),
            }

        # MONEY recon: store-declared closing $ vs B2B actuals (accessory gross, cash, credit).
        # Shortage = declared LESS than B2B (money unaccounted). epay-vs-portal is wired but
        # pending the ePay Daily Transactions Report sweep.
        bm = b2b_money.get(code) if code else None
        money_recon = None
        if bm is not None:
            closing_cash = round(totals["epay_cash"] + totals["store_cash"], 2)   # cash collected
            closing_credit = round(totals["store_cc"] + totals["epay_cc"], 2)      # credit declared
            closing_epay = round(totals["epay_cash"] + totals["epay_cc"], 2)       # total epay declared

            def _cmp(closing_v, b2b_v, available=True):
                if not available:
                    # Source can't supply this split → recon-pending, NOT a flag (don't compare vs $0).
                    return {"closing": round(closing_v, 2), "b2b": None, "var": None,
                            "shortage": False, "overage": False, "flag": False, "pending": True}
                var = round(closing_v - b2b_v, 2)
                return {"closing": round(closing_v, 2), "b2b": round(b2b_v, 2), "var": var,
                        "shortage": var < -tolerance, "overage": var > tolerance,
                        "flag": abs(var) > tolerance}

            # Cash/card come from the X-REPORT when available (authoritative), else the sales feed
            # (which usually lacks a tender split → pending, not flagged).
            xt = xreport_tenders.get(code) if code else None
            if xt:
                tender_cash, tender_card, tender_src, tenders_ok = xt["cash"], xt["card"], "x_report", True
            else:
                tender_cash, tender_card, tender_src = bm["cash"], bm["card"], "sales_feed"
                tenders_ok = bm.get("tenders_available", True)
            dept_ok = bm.get("dept_available", True)
            money_recon = {
                "tolerance": tolerance,
                "accessory": _cmp(totals["acc_sale"], bm["acc_gross"], dept_ok),  # gross vs gross
                "tax_collected": round(bm.get("tax", 0.0), 2),  # sales tax on the day (ext_price is pre-tax; tenders include this)
                "cash": _cmp(closing_cash, tender_cash, tenders_ok),
                "credit": _cmp(closing_credit, tender_card, tenders_ok),
                "epay": {"declared": closing_epay, "portal": None, "portal_pending": True,
                         "fee": None, "other": None, "var": None,
                         "note": "ePay Daily Transactions Report sweep not yet wired"},
                "b2b_total": bm["total"], "b2b_tenders": bm["tenders"],
                "tender_source": tender_src, "tenders_available": tenders_ok, "dept_available": dept_ok,
            }
            if not tenders_ok:
                if x_report_ever:
                    money_recon["note"] = ("No POS X-report tender data for THIS DAY (and the sales feed has "
                                           "no Tender Type), so cash & credit can't be reconciled yet — this "
                                           "tenant has had X-reports before, so check today's file specifically. "
                                           "Shown as pending, not flagged.")
                else:
                    money_recon["note"] = ("This tenant has NEVER had a POS X-report imported (and the sales "
                                           "feed has no Tender Type), so cash & credit can't be reconciled — "
                                           "check (1) the mailbox has an *X-Report* -> x_report rule and "
                                           "(2) b2bsoft is actually scheduled to email an X-Report for this "
                                           "tenant. Shown as pending, not flagged.")
            money_recon["any_flag"] = any(money_recon[k].get("flag") for k in ("accessory", "cash", "credit"))

        out_reps = []
        for rp, (dt, g) in zip(reps, _rep_computed):
            ct_display, rc_display = _rep_custom_displays(rp)
            out_reps.append({**rp, "envelope_url": _signed_envelope(rp.get("envelope_picture")),
                             "_tenders": dt, "_gate": g,
                             "_custom_tenders_display": ct_display, "_custom_counts_display": rc_display})

        out.append({
            "store_code": code, "store_name": (reps[0].get("store_name") or code or "—"),
            "store_address": meta.get("address") or reps[0].get("store_address"),
            "market": mkt_bucket, "close_date": date, "reps": out_reps,
            "totals": totals, "gate_status": store_gate_status,
            "scheduled_count": len(scheduled), "missing_reps": missing,
            "worked_reps": sorted(worked), "worked_count": len(worked),
            "scheduled_no_show": scheduled_no_show, "worked_unscheduled": worked_unscheduled,
            "cross_login": cross_login, "closing_mode": closing_mode,
            "closer": closer_by_store.get(code) if code else None,
            "verification": ver_by_store.get(code), "recon": recon, "money_recon": money_recon,
        })

    # Stores where reps WORKED (clocked in / sold) but NOBODY submitted a closing never appear in
    # `groups` (which is built from submitted rows) — surface them so a store that failed to close is
    # visible instead of silently absent. Everyone who worked (or the closer, in one_closing mode) owes it.
    closed_codes = {s["store_code"] for s in out if s.get("store_code")}
    for code, ww in who.items():
        if not code or code in closed_codes:
            continue
        meta = store_meta.get(code, {})
        mkt = meta.get("market") or ""
        mkt_bucket = _market_bucket(mkt)
        if market_set is not None and mkt_bucket.casefold() not in market_set:
            continue
        if store_set is not None and meta and code.upper() not in store_set:
            continue
        clocked = set(ww.get("clocked_in", set()))
        sold = set(ww.get("sold", set()))
        worked = {n for n in (clocked | sold) if n}
        if not worked:
            continue
        scheduled = sched_by_store.get(code, set())
        closer = closer_by_store.get(code)
        owes = ({closer} if closer else worked) if closing_mode == "one_closing" else worked
        missing_here = sorted({n for n in owes if n})
        if rep_set is not None:
            involved_cf = {n.casefold() for n in worked} | {n.casefold() for n in missing_here}
            if not (involved_cf & rep_set):
                continue
        logins = ww.get("logins", {})
        cross_login = sorted(
            [{"salesperson": nm, "logins": sorted(logins.get(nm, set()))}
             for nm in sold if not any(_name_match(nm, c) for c in clocked)],
            key=lambda x: x["salesperson"])
        out.append({
            "store_code": code, "store_name": meta.get("address") or code, "store_address": meta.get("address"),
            "market": mkt_bucket, "close_date": date, "reps": [], "totals": None, "gate_status": None,
            "scheduled_count": len(scheduled), "missing_reps": missing_here,
            "worked_reps": sorted(worked), "worked_count": len(worked),
            "scheduled_no_show": sorted({nm for nm in scheduled if not any(_name_match(nm, w) for w in worked)}),
            "worked_unscheduled": sorted({nm for nm in worked if not any(_name_match(nm, s) for s in scheduled)}),
            "cross_login": cross_login, "closing_mode": closing_mode, "closer": closer,
            "no_closing_submitted": True,
            "verification": ver_by_store.get(code), "recon": None, "money_recon": None,
        })

    return out


@router.get("/summary")
def closing_summary(date: str = None, date_from: str = None, date_to: str = None,
                    market: str = None, markets: str = None, stores: str = None, reps: str = None,
                    tolerance: float = 1.0, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """DM evening verification view. `date=YYYY-MM-DD` (the historical single-day call, UNCHANGED
    shape/math) OR `date_from`/`date_to` for an optional multi-day range (retail-ops-14, OWNER
    DIRECTIVE 2026-07-28 — the DM's evening workflow stays single-day by default; range mode is an
    additive capability for catching up on several nights at once, bounded to
    _SUMMARY_MAX_RANGE_DATES). `markets=`/`stores=`/`reps=` are comma-separated multi-selects
    (additive to the legacy singular `market=`); market matching is bucket-aware — see
    `_market_bucket` — so an unresolved/blank-market store is never silently dropped. No date at all
    (e.g. a "Clear filters" reset on the frontend's standard filter bar) degrades to TODAY rather than
    a 400 — matches the doctrine every other RULE-FIVE page follows (a cleared filter falls back to a
    sane default, never an error)."""
    if not date and not date_from and not date_to:
        date = _biz_today_iso()
    client = sb()
    is_range = bool(date_from or date_to)
    if is_range:
        d_from = date_from or date_to
        d_to = date_to or date_from
        all_dates = _date_range_list(d_from, d_to)
        range_capped = len(all_dates) > _SUMMARY_MAX_RANGE_DATES
        dates = all_dates[-_SUMMARY_MAX_RANGE_DATES:] if range_capped else all_dates
    else:
        dates = [date]
        all_dates = dates
        range_capped = False

    market_set = _resolve_market_filter(market, markets)
    store_set = _resolve_store_filter(stores)
    rep_set = _resolve_rep_filter(reps)
    can_review = _can_mgmt_review(_caller_perms(client, authorization))

    out = []
    for d in dates:
        out.extend(_closing_summary_for_date(client, org_id, d, market_set, store_set, rep_set, tolerance, can_review))

    # Stable two-pass sort: store_address ascending WITHIN each date, dates descending overall — for a
    # single-day call (all rows share one close_date) this is byte-identical to the historical
    # store_address-only sort.
    out.sort(key=lambda s: str(s.get("store_address") or s.get("store_name") or ""))
    out.sort(key=lambda s: str(s.get("close_date") or ""), reverse=True)
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        out = [s for s in out if in_keyset(ks, s.get("store_code"), s.get("store_address"))]
    return {"date": date, "dates": dates, "range": is_range, "stores": out,
           "dates_requested": len(all_dates), "dates_computed": len(dates), "range_capped": range_capped,
           "can_review": can_review}


# ── DM verification upsert ────────────────────────────────────────────────────────────────
@router.post("/verify")
def verify_store(payload: dict, org_id: str = ORG_ID):
    code = payload.get("store_code")
    date = payload.get("close_date")
    if not code or not date:
        raise HTTPException(400, "store_code and close_date required")
    body = {
        "org_id": org_id, "close_date": date, "store_code": code,
        "store_name": payload.get("store_name"),
        "verified": bool(payload.get("verified", True)),
        "verified_by": payload.get("verified_by"),
        "verified_at": _now() if payload.get("verified", True) else None,
        "dm_store_cash": payload.get("dm_store_cash"), "dm_store_cc": payload.get("dm_store_cc"),
        "dm_epay_cash": payload.get("dm_epay_cash"), "dm_epay_cc": payload.get("dm_epay_cc"),
        "dm_acc_sale": payload.get("dm_acc_sale"), "dm_other": payload.get("dm_other"),
        "note": payload.get("note"), "updated_at": _now(),
    }
    (sb().schema("commcalc").table("daily_closing_verification")
     .upsert(body, on_conflict="org_id,close_date,store_code").execute())
    return {"ok": True, "store_code": code, "close_date": date}


@router.post("/expense/approve")
def approve_expense(payload: dict, org_id: str = ORG_ID):
    """DM approval of a rep's daily-closing expense — a single toggle. Body: {row_id, approved(bool),
    approved_by?}. Sets expense_approved(+by/at) on that daily_closing row; unchecking clears them."""
    row_id = (payload.get("row_id") or "").strip()
    if not row_id:
        raise HTTPException(400, "row_id required")
    approved = bool(payload.get("approved", True))
    upd = {
        "expense_approved": approved,
        "expense_approved_by": (payload.get("approved_by") or "DM") if approved else None,
        "expense_approved_at": _now() if approved else None,
        "updated_at": _now(),
    }
    (sb().schema("commcalc").table("daily_closing").update(upd)
     .eq("org_id", org_id).eq("id", row_id).execute())
    return {"ok": True, "row_id": row_id, "expense_approved": approved,
            "expense_approved_by": upd["expense_approved_by"], "expense_approved_at": upd["expense_approved_at"]}


# ── Manual in-app row entry (the eventual switch off the Google sheet) ─────────────────────
# ── Envelope photo: real capture/upload + OCR mismatch (Theme 3) ──────────────────────────────
ENVELOPE_BUCKET = "closing-envelopes"


def _ensure_envelope_bucket():
    c = get_supabase()
    try:
        c.storage.get_bucket(ENVELOPE_BUCKET)
    except Exception:
        try:
            c.storage.create_bucket(ENVELOPE_BUCKET)
        except Exception:
            pass
    return c


def _upload_envelope(org_id, data_url):
    if not data_url or "," not in str(data_url):
        return None
    try:
        header, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
        ext = "png" if "png" in header else "jpg"
        path = f"{org_id}/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.{ext}"
        _ensure_envelope_bucket().storage.from_(ENVELOPE_BUCKET).upload(
            path, raw, {"content-type": f"image/{'png' if ext == 'png' else 'jpeg'}", "upsert": "true"})
        return path
    except Exception as e:
        print(f"WARN envelope upload failed: {e}")
        return None


def _signed_envelope(path):
    """Sign a storage path; pass through legacy http links / plain text unchanged."""
    p = str(path or "")
    if not p or p.startswith("http") or "/" not in p:
        return path
    try:
        res = get_supabase().storage.from_(ENVELOPE_BUCKET).create_signed_url(p, 3600)
        return (res.get("signedURL") or res.get("signed_url")) if isinstance(res, dict) else res
    except Exception:
        return path


@router.post("/envelope-photo")
def upload_envelope_photo(body: dict, org_id: str = ORG_ID):
    """Store a captured envelope photo (base64) → return its path + a signed URL. The path goes into
    daily_closing.envelope_picture on submit."""
    path = _upload_envelope(org_id, body.get("image"))
    if not path:
        raise HTTPException(400, "no image provided")
    return {"path": path, "url": _signed_envelope(path)}


async def _notify_envelope_mismatch(client, org_id, summary):
    """Best-effort email + WhatsApp of an envelope OCR mismatch to the designated closing recipient."""
    try:
        rows = (client.schema("commcalc").table("cash_pickup_config").select("*").eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    cfg = rows[0] if rows else {}
    email = (cfg.get("recipient_email") or "").strip()
    wa = (cfg.get("recipient_whatsapp") or "").strip()
    if email and cfg.get("notify_email", True):
        try:
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                await email_resend.send_email(email, "⚠️ Envelope mismatch", "<p>" + summary.replace("\n", "<br>") + "</p>")
        except Exception:
            pass
    if wa and cfg.get("notify_whatsapp", True):
        try:
            from app.modules.notify.channels import whatsapp_meta
            if whatsapp_meta.is_configured():
                await whatsapp_meta.send_document(wa, b"", "text/plain", "mismatch.txt", summary)
        except Exception:
            pass


@router.post("/row")
async def create_row(payload: dict, org_id: str = ORG_ID):
    client = sb()
    d = _date(payload.get("close_date"))
    if not d:
        raise HTTPException(400, "valid close_date required")
    sfid = (payload.get("sfid") or "").strip()
    sm = _store_resolver(client, org_id).get(sfid, {}) if sfid else {}
    body = {
        "org_id": org_id, "period": d[:7], "close_date": d, "submitted_at": _now(),
        "sfid": sfid or None, "store_name": payload.get("store_name"),
        "store_code": payload.get("store_code") or sm.get("store_code"),
        "store_address": sm.get("store_address"),
        "employee_name": payload.get("employee_name"),
        "acc_sale": _money(payload.get("acc_sale")),
        "envelope_picture": (payload.get("envelope_picture") or "").strip() or None,
        "remarks": payload.get("remarks"), "source": "manual",
    }

    # ── Duplicate-submission guard (mig 502): ONE ACTIVE row per (org, store_code, employee_name,
    #    close_date). A rep double-submitting used to create a SECOND daily_closing row that
    #    closing_summary/recon summed straight into the store's totals, silently DOUBLING declared
    #    cash/credit — this is the fix. A second submit for the same combo is refused with a clear
    #    message unless a manager has RELEASED the existing row (POST /closing/row/{id}/release,
    #    Management Review page) — a release unlocks it for exactly ONE corrected resubmit, which
    #    UPDATES that same row (never inserts a second) and re-locks it, fully audited
    #    (released_by/released_at/release_note + corrected_at/correction_count on the row).
    _dupe_store, _dupe_emp = body.get("store_code"), (body.get("employee_name") or "").strip()
    _update_id = None
    if _dupe_store and _dupe_emp:
        try:
            _existing = (client.schema("commcalc").table("daily_closing")
                         .select("id,released_at,correction_count")
                         .eq("org_id", org_id).eq("close_date", d).eq("store_code", _dupe_store)
                         .ilike("employee_name", _dupe_emp)
                         .order("submitted_at").execute().data) or []
        except Exception:
            _existing = []
        if len(_existing) > 1 and not any(r.get("released_at") for r in _existing):
            raise HTTPException(409, f"Multiple existing submissions found for {_dupe_emp} at this store "
                                 f"on {d} (likely from the double-submit bug) — ask a manager to review "
                                 f"/closing/duplicates and release the correct row before resubmitting.")
        if _existing:
            _row0 = _existing[0]
            if not _row0.get("released_at"):
                raise HTTPException(409, f"Already submitted for {d} — ask a manager to release it before resubmitting.")
            _update_id = _row0.get("id")
            body["correction_count"] = int(_row0.get("correction_count") or 0) + 1
            body["corrected_at"] = _now()
            body["released_at"] = None
            body["released_by"] = None
            body.pop("submitted_at", None)   # keep the ORIGINAL submitted_at on a corrected resubmit
        body["dedup_key"] = f"{org_id}|{_dupe_store}|{_dupe_emp.lower()}|{d}"
    # ── Activation-count fields (mig 501). Configured tenants send `counts: {field_key: value}` for
    #    every field on their axis; standard field_keys still write the physical column (backward-compat
    #    with the rollup dashboard + sheet-upload ingestion), custom ones go to daily_closing.counts
    #    jsonb. No config sent (or no tenant config at all) -> the legacy upgrade_count/new_line_count/
    #    postpaid_count keys, byte-identical to today. ──
    from . import count_config
    _payload_counts = payload.get("counts")
    if isinstance(_payload_counts, dict) and _payload_counts:
        _cdefs = count_config.load_count_config(client, org_id)
        _ckeys, _clabels, _crclass = count_config.count_axis(_cdefs)
        _custom_counts = {}
        for _k in _ckeys:
            _v = _int(_payload_counts.get(_k))
            if _k in count_config.STD_FIELD_KEYS:
                body[_k] = _v
            else:
                _custom_counts[_k] = _v
        for _k in count_config.STD_FIELD_KEYS:
            body.setdefault(_k, 0)
        if _custom_counts:
            body["counts"] = _custom_counts
    else:
        body["upgrade_count"] = _int(payload.get("upgrade_count"))
        body["new_line_count"] = _int(payload.get("new_line_count"))
        body["postpaid_count"] = _int(payload.get("postpaid_count"))
    # Rep expense (reimbursement) — the rep enters an amount + a REQUIRED description; a positive
    # amount with no description is rejected. Starts unapproved; the DM approves it on the verify screen.
    exp_amt = _money(payload.get("expense_amount"))
    exp_desc = (payload.get("expense_description") or "").strip()
    if exp_amt > 0 and not exp_desc:
        raise HTTPException(400, "A description is required for the expense.")
    body["expense_amount"] = exp_amt
    body["expense_description"] = exp_desc or None
    body["expense_approved"] = False
    # ── Six tender types (mirror the POS X-report). Accept the new t_* fields; fall back to the legacy
    #    store/epay/other fields for any caller (old kiosk) that hasn't sent them yet. ──
    def _pt(k):
        return _money(payload.get(k))
    if any(payload.get(k) not in (None, "") for k in ("t_cash", "t_credit", "t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima")):
        tenders = {"cash": _pt("t_cash"), "credit": _pt("t_credit"), "ext_cc": _pt("t_ext_cc"),
                   "gift": _pt("t_gift"), "store_acct": _pt("t_store_acct"), "zelle": _pt("t_zelle"),
                   "acima": _pt("t_acima")}
    else:
        tenders = {"cash": _money(payload.get("store_cash")) + _money(payload.get("epay_cash")),
                   "credit": _money(payload.get("store_cc")) + _money(payload.get("epay_cc")),
                   "ext_cc": 0.0, "gift": 0.0, "store_acct": 0.0,
                   "zelle": _money(payload.get("other_account")), "acima": 0.0}
    body.update({"t_cash": tenders["cash"], "t_credit": tenders["credit"], "t_ext_cc": tenders["ext_cc"],
                 "t_gift": tenders["gift"], "t_store_acct": tenders["store_acct"], "t_zelle": tenders["zelle"],
                 "t_acima": tenders["acima"]})
    # Custom tenders (mig 111) — amounts for tender_keys beyond the standard 7 go to daily_closing.tenders
    # JSONB. Only sent by a tenant that configured custom tenders (so the column exists post-mig-111).
    _custom = payload.get("custom_tenders")
    if isinstance(_custom, dict) and _custom:
        body["tenders"] = {k: _money(v) for k, v in _custom.items() if k and k not in _TCOL}
    # Keep the legacy columns populated so existing dashboards / recon keep reconciling unchanged.
    body["store_cash"] = tenders["cash"]
    body["epay_cash"] = 0.0
    body["store_cc"] = round(tenders["credit"] + tenders["ext_cc"], 2)
    body["epay_cc"] = 0.0
    body["other_account"] = round(tenders["zelle"] + tenders["store_acct"] + tenders["gift"], 2)
    # ePay (bill-payment) breakdown — how much of the cash / credit / ACIMA collected was ePay bill
    # payments. INFORMATIONAL: a subset of those tenders, NOT added to the total and NOT part of the
    # legacy cash/credit recon (epay_cash/epay_cc stay 0). Feeds the ePay bank-deposit reconciliation.
    body["epay_on_cash"] = _money(payload.get("epay_on_cash") or payload.get("epay_cash"))
    body["epay_on_credit"] = _money(payload.get("epay_on_credit") or payload.get("epay_credit"))
    body["epay_on_acima"] = _money(payload.get("epay_on_acima") or payload.get("epay_acima"))

    # ── Close gate + 3-TRY flow: cash SHORT or credit OVER vs B2B is a "blocker". The rep is told only
    #    the DIRECTION (never the amount) and may recount up to 3 times; the 3rd try is auto-accepted and
    #    flagged for management review. Every try is logged to closing_attempt. Cash-over / credit-under
    #    stay non-blocking flags. No B2B / rep-not-matched → recon-pending, never blocks. ──
    declared_cash = tenders["cash"]
    declared_credit = round(tenders["credit"] + tenders["ext_cc"], 2)
    tol = float(payload.get("tolerance") or 1.0)
    gate = _gate_row(client, org_id, body.get("store_code"), d, body.get("employee_name") or "",
                     declared_cash, declared_credit, tol)
    b2b = gate.get("b2b")
    issues = _money_issues(declared_cash, declared_credit, b2b["cash"], b2b["card"], tol) if b2b else []
    dirs = _variance_dirs(issues)
    is_blocking = any(i["severity"] == "block" for i in issues)

    prior = (client.schema("commcalc").table("closing_attempt").select("id")
             .eq("org_id", org_id).eq("close_date", d)
             .eq("store_code", body.get("store_code") or "")
             .eq("employee_name", body.get("employee_name") or "").execute().data) or []
    attempt_no = len(prior) + 1
    accept = (not is_blocking) or attempt_no >= 3
    auto_accepted = bool(is_blocking and attempt_no >= 3)

    _log_attempt(client, org_id, d, body, tenders, declared_cash, declared_credit, b2b, dirs,
                 attempt_no, blocked=(is_blocking and not accept), accepted=accept, auto_accepted=auto_accepted)

    if not accept:
        # Rep sees NOTHING specific — not the amount, the over/short direction, or the attempt count.
        return {"accepted": False, "retry": {"message": _REP_MISMATCH_RETRY}}

    body["attempts"] = attempt_no
    body["auto_accepted"] = auto_accepted
    body["mgmt_flag"] = auto_accepted

    def _write(b):
        # A release→corrected-resubmit UPDATEs the SAME row (mig 502) — never a second INSERT.
        if _update_id:
            return (client.schema("commcalc").table("daily_closing").update(b)
                    .eq("org_id", org_id).eq("id", _update_id).execute())
        return client.schema("commcalc").table("daily_closing").insert(b).execute()
    try:
        r = _write(body)
    except Exception as e:
        if "daily_closing_one_active_per_rep_day" in str(e) or "duplicate key" in str(e).lower():
            # A race: two near-simultaneous submits both passed the pre-check above. The DB-level
            # partial unique index (mig 502) is the safety net — same refusal message either way.
            raise HTTPException(409, f"Already submitted for {d} — ask a manager to release it before resubmitting.")
        # Tolerate not-yet-run additive migrations (t_acima=mig104, epay_on_*=mig106,
        # expense_*=mig109, dedup_key/corrected_at/correction_count/released_*=mig502): drop the new
        # keys + retry. (mig 502 not run -> dedup guard above already no-op'd via empty `_existing`.)
        for _k in ("t_acima", "epay_on_cash", "epay_on_credit", "epay_on_acima",
                   "expense_amount", "expense_description", "expense_approved", "counts",
                   "dedup_key", "corrected_at", "correction_count", "released_at", "released_by"):
            body.pop(_k, None)
        r = _write(body)
    saved = r.data[0] if r.data else body

    # 3-way envelope recon: OCR'd cash (the rep's OWN photo) vs entered cash — this is the rep's own
    # data, so it's fine to show. It does NOT reveal the B2B system figure.
    ocr_mismatch = None
    if payload.get("ocr_cash") not in (None, ""):
        ocr_cash = _money(payload.get("ocr_cash"))
        diff = round(ocr_cash - declared_cash, 2)
        if abs(diff) > tol:
            ocr_mismatch = {"ocr_cash": ocr_cash, "declared_cash": declared_cash, "diff": diff}
            summary = (f"Envelope OCR mismatch — {body.get('store_name') or body.get('store_code') or '—'}, "
                       f"{body.get('employee_name') or '—'} on {d}: photo reads {_usd(ocr_cash)} but "
                       f"{_usd(declared_cash)} was entered (off by {_usd(diff)}). "
                       f"B2B cash: {_usd((b2b or {}).get('cash'))}.")
            try:
                await _notify_envelope_mismatch(client, org_id, summary)
            except Exception:
                pass

    # Rep-facing recon: reveals NOTHING — a mismatch (auto-accepted or a non-blocking over/under) just
    # says the report doesn't match and is going to management review. NO amount, direction, or count.
    rep_flags = []
    mismatch = auto_accepted or dirs.get("cash") not in (None, "ok") or dirs.get("credit") not in (None, "ok")
    if mismatch:
        rep_flags.append(_REP_MISMATCH_REVIEW)
    recon = {"status": ("auto_accepted" if auto_accepted else gate["status"]),
             "flags": rep_flags, "auto_accepted": auto_accepted, "attempts": attempt_no}
    if ocr_mismatch:
        recon["envelope_mismatch"] = ocr_mismatch
        recon["flags"] = rep_flags + [f"Envelope photo reads {_usd(ocr_mismatch['ocr_cash'])} vs {_usd(declared_cash)} entered"]
    return {**saved, "accepted": True, "recon": recon, "envelope_url": _signed_envelope(saved.get("envelope_picture"))}


@router.patch("/row/{row_id}")
def update_row(row_id: str, updates: dict, org_id: str = ORG_ID):
    allowed = ("store_cash", "store_cc", "epay_cash", "epay_cc", "acc_sale", "other_account",
               "upgrade_count", "new_line_count", "postpaid_count", "remarks", "employee_name",
               "store_name", "envelope_picture")
    body = {}
    for k in allowed:
        if k in updates:
            if k.endswith("_count"):
                body[k] = _int(updates[k])
            elif k in ("store_cash", "store_cc", "epay_cash", "epay_cc", "acc_sale", "other_account"):
                body[k] = _money(updates[k])
            else:
                body[k] = updates[k]
    body["updated_at"] = _now()
    r = (sb().schema("commcalc").table("daily_closing").update(body)
         .eq("id", row_id).eq("org_id", org_id).execute())
    return r.data[0] if r.data else body


@router.delete("/row/{row_id}")
def delete_row(row_id: str, org_id: str = ORG_ID):
    sb().schema("commcalc").table("daily_closing").delete().eq("id", row_id).eq("org_id", org_id).execute()
    return {"deleted": row_id}


@router.post("/row/{row_id}/release")
def release_closing_row(row_id: str, payload: dict = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """MANAGEMENT OVERRIDE (mig 502): unlock a submitted daily_closing row for exactly ONE corrected
    resubmit. Gated to the same management-review permission as /closing/attempts (DMs excluded) — a
    DM cannot self-release a duplicate. Body: {released: bool (default true), released_by?, note?}.
    Toggling released=false re-locks a row without requiring a resubmit (undo a mistaken release).
    Never deletes/merges rows — the next POST /closing/row for that store+employee+day UPDATES this
    exact row (see create_row); releasing never creates a second row."""
    require_org(org_id)
    client = sb()
    if not _can_mgmt_review(_caller_perms(client, authorization)):
        raise HTTPException(403, "Releasing a closing is permission-restricted (not available to DMs).")
    payload = payload or {}
    released = bool(payload.get("released", True))
    by = (payload.get("released_by") or "").strip() or "management"
    upd = ({"released_at": _now(), "released_by": by, "release_note": (payload.get("note") or "").strip() or None}
           if released else {"released_at": None, "released_by": None, "release_note": None})
    try:
        r = (client.schema("commcalc").table("daily_closing").update(upd)
             .eq("org_id", org_id).eq("id", row_id).execute())
    except Exception:
        raise HTTPException(400, "run migration 502 first (commcalc.daily_closing release columns)")
    if not r.data:
        raise HTTPException(404, "row not found")
    return {"ok": True, "row_id": row_id, "released": released, **upd}


@router.get("/duplicates")
def closing_duplicates(period: str = None, date: str = None, store: str = None,
                       authorization: str = Header(default=""), org_id: str = ORG_ID):
    """READ-ONLY report of suspected duplicate daily_closing submissions — 2+ rows sharing the same
    (org, store, employee, close_date), the exact fingerprint of the double-submit bug mig 502 fixes.
    NEVER auto-deletes or auto-merges; the owner/management reviews each group and uses
    POST /row/{id}/release + a corrected resubmit (or DELETE /row/{id} for a true throwaway dupe) to
    resolve. Gated the same as /closing/attempts (management only, DMs excluded)."""
    require_org(org_id)
    client = sb()
    if not _can_mgmt_review(_caller_perms(client, authorization)):
        raise HTTPException(403, "The duplicates report is permission-restricted (not available to DMs).")
    q = (client.schema("commcalc").table("daily_closing")
         .select("id,store_code,store_address,store_name,employee_name,close_date,submitted_at,"
                 "t_cash,t_credit,store_cash,store_cc,released_at,released_by,source"))
    q = q.eq("org_id", org_id)
    if period:
        q = q.eq("period", period)
    if date:
        q = q.eq("close_date", _date(date) or date)
    if store:
        q = q.eq("store_code", store)
    rows = q.limit(50000).execute().data or []
    groups = {}
    for r in rows:
        k = (r.get("store_code") or "", (r.get("employee_name") or "").strip().lower(), str(r.get("close_date") or ""))
        groups.setdefault(k, []).append(r)
    out = []
    for (code, emp_key, d), rs in groups.items():
        if len(rs) < 2:
            continue
        rs.sort(key=lambda x: str(x.get("submitted_at") or ""))
        out.append({
            "store_code": code or None, "store_address": rs[0].get("store_address") or rs[0].get("store_name"),
            "employee_name": rs[0].get("employee_name"), "close_date": d, "row_count": len(rs),
            "any_released": any(r.get("released_at") for r in rs),
            "rows": [{"id": r.get("id"), "submitted_at": r.get("submitted_at"),
                      "cash": _f(r.get("t_cash")) or _f(r.get("store_cash")),
                      "credit": _f(r.get("t_credit")) or _f(r.get("store_cc")),
                      "source": r.get("source"), "released_at": r.get("released_at"),
                      "released_by": r.get("released_by")} for r in rs],
        })
    out.sort(key=lambda g: (g["close_date"] or "", g["store_address"] or ""), reverse=True)
    return {"period": period, "date": date, "groups": out, "total_groups": len(out),
            "total_duplicate_rows": sum(g["row_count"] for g in out)}


# ── Management review (permission-gated, DMs excluded): the 3-try close-attempt log ──────────────
@router.get("/attempts")
def closing_attempts(period: str = None, date: str = None, store: str = None, only_review: bool = False,
                     authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Every value a rep entered before a close was accepted, grouped by (date, store, rep), WITH the
    true B2B variance the rep never saw. Restricted to management (super-admin / company-wide scope /
    explicit /closing/management grant) — a DM cannot see it. only_review=true → just the groups that
    took >1 try or were auto-accepted (the ones worth reviewing)."""
    require_org(org_id)
    client = sb()
    if not _can_mgmt_review(_caller_perms(client, authorization)):
        raise HTTPException(403, "Management review is permission-restricted (not available to DMs).")
    q = client.schema("commcalc").table("closing_attempt").select("*").eq("org_id", org_id)
    if period:
        q = q.eq("period", period)
    if date:
        q = q.eq("close_date", _date(date) or date)
    if store:
        q = q.eq("store_code", store)
    rows = q.limit(50000).execute().data or []
    groups = {}
    for r in rows:
        k = (r.get("close_date"), r.get("store_code"), r.get("employee_name"))
        groups.setdefault(k, []).append(r)

    # Match each group to its daily_closing row (mig 502 release/dedup state), so Management Review can
    # RELEASE a row for correction right from this list — without a separate lookup. Best-effort: if
    # mig 502 hasn't run, released_at/correction_count just come back null and the release button on the
    # frontend degrades to "not available yet".
    dc_by_key = {}
    try:
        dcq = client.schema("commcalc").table("daily_closing").select(
            "id,close_date,store_code,employee_name,released_at,released_by,correction_count").eq("org_id", org_id)
        if period:
            dcq = dcq.eq("period", period)
        if date:
            dcq = dcq.eq("close_date", _date(date) or date)
        if store:
            dcq = dcq.eq("store_code", store)
        for dcr in (dcq.limit(50000).execute().data or []):
            dk = (dcr.get("close_date"), dcr.get("store_code"), dcr.get("employee_name"))
            dc_by_key[dk] = dcr   # last one wins if duplicates exist — the report/duplicates endpoint is
                                  # the tool for reviewing a duplicate group in full
    except Exception:
        pass

    out = []
    for (dt, sc, emp), tries in groups.items():
        tries.sort(key=lambda x: x.get("attempt_no") or 0)
        last = tries[-1]
        auto = any(t.get("auto_accepted") for t in tries)
        if only_review and not (len(tries) > 1 or auto):
            continue
        dc = dc_by_key.get((dt, sc, emp)) or {}
        out.append({
            "close_date": dt, "store_code": sc, "store_address": last.get("store_address"),
            "employee_name": emp, "attempts": len(tries), "auto_accepted": auto,
            "final_dir": {"cash": last.get("cash_dir"), "credit": last.get("credit_dir")},
            "b2b": {"cash": last.get("b2b_cash"), "credit": last.get("b2b_credit")},
            "row_id": dc.get("id"), "released_at": dc.get("released_at"),
            "released_by": dc.get("released_by"), "correction_count": dc.get("correction_count") or 0,
            "tries": [{"attempt_no": t.get("attempt_no"), "entered_cash": t.get("entered_cash"),
                       "entered_credit": t.get("entered_credit"), "cash_dir": t.get("cash_dir"),
                       "credit_dir": t.get("credit_dir"), "blocked": t.get("blocked"),
                       "accepted": t.get("accepted"), "auto_accepted": t.get("auto_accepted"),
                       "t_cash": t.get("t_cash"), "t_credit": t.get("t_credit"), "t_ext_cc": t.get("t_ext_cc"),
                       "t_gift": t.get("t_gift"), "t_store_acct": t.get("t_store_acct"), "t_zelle": t.get("t_zelle"),
                       "t_acima": t.get("t_acima"),
                       "created_at": t.get("created_at")} for t in tries],
        })
    out.sort(key=lambda x: (x["close_date"] or "", x["store_address"] or ""), reverse=True)
    return {"groups": out, "total": len(out)}


# ── 3-way tender recon: DAILY CLOSING vs POS X-REPORT vs SALES TRANSACTIONS, per store, per tender ──
def _sales_tenders_by_store(client, org_id: str, date: str, tresolve=None, keys=None,
                             unmapped_out: dict = None) -> dict:
    """The day's sales-transaction $ bucketed per tender, per store_code (from the same unified B2B
    source the money recon uses). Sums ext_price per tender — merchandise by tender, so it tracks the
    X-report's tender split (which also includes tax, hence small deltas are expected). `tresolve`/`keys`
    let the caller pass a tenant-configured tender resolver + axis; default = the hardcoded
    _canon_tender + CANON_TENDERS, so behaviour is unchanged when a tenant hasn't opted in.

    `unmapped_out`, if given, is a dict this function POPULATES (per store_code: {"amount", "labels"})
    with sales rows whose tender resolved to nothing bucketable — either no resolver match at all, OR a
    truthy tender_key an explicit tenant map rule returned that isn't actually on this tenant's active
    axis (2026-07-28 Gate-1 N1/N2: a map rule's `tender_key` is never validated against `closing_tender_
    def` at save time, so a deactivated/typo'd def leaves a rule pointing at a dead key — previously that
    silently dropped the dollars with NO signal, the exact bug class this whole package exists to fix).
    Mirrors the X-report leg's `xrep_unmapped` treatment. OPTIONAL/backward-compatible: omitting it (any
    other/future caller) is BYTE-IDENTICAL to before this fix — an unmapped row is just not tallied into
    `out`, same as today."""
    addr = _addr_resolver(client, org_id)
    tresolve = tresolve or _canon_tender
    keys = keys or CANON_TENDERS
    rows = _b2b_sales_rows(client, org_id, date, "store,tender_type,ext_price,voided,trans_type")
    out = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        code = addr(r.get("store")) or (r.get("store") or "?")
        amt = _f(r.get("ext_price"))
        canon = tresolve(r.get("tender_type"))
        if canon and canon in keys:
            agg = out.setdefault(code, {t: 0.0 for t in keys})
            agg[canon] += amt
        elif unmapped_out is not None:
            u = unmapped_out.setdefault(code, {"amount": 0.0, "labels": set()})
            u["amount"] += amt
            lbl = str(r.get("tender_type") or "").strip()
            if lbl:
                u["labels"].add(lbl)
    return out


@router.get("/tender-recon-3way")
def tender_recon_3way(date: str, store: str = None, org_id: str = ORG_ID):
    """One day, per store: the SAME tenders captured three independent ways — (1) DAILY CLOSING (what the
    rep entered), (2) POS X-REPORT (pos_tender_summary), (3) SALES TRANSACTIONS (raw_sales / feed). All
    bucketed to cash / credit / external CC / gift card / store account / zelle. The X-report is generated
    from the sales transactions, so those two should agree; the closing is the human cross-check."""
    require_org(org_id)
    client = sb()
    d = _date(date)
    if not d:
        raise HTTPException(400, "valid date required (YYYY-MM-DD)")
    # Tenant tender config (mig 111): the axis (keys+labels) and per-report raw→tender resolvers.
    # No config → the hardcoded CANON_TENDERS + _canon_tender, so an un-opted tenant is byte-identical.
    from .tender_config import load_tender_config, tender_axis, make_resolver
    _defs, _maps = load_tender_config(client, org_id)
    keys, tlabel, _rclass, _intotal = tender_axis(_defs, CANON_TENDERS, CANON_TENDER_LABEL)
    resolve_x = make_resolver(_maps, "x_report", _canon_tender, keys)
    resolve_s = make_resolver(_maps, "sales", _canon_tender, keys)
    # (1) closing — rep t_* (+ custom tenders JSONB) per store_code. Resilient to columns not existing
    # yet (mig 104/111 not run): fall back so the recon never 500s on a not-yet-run migration.
    closing = {}
    def _closing_rows(cols):
        # No DB-level store_code filter here (2026-07-28 Gate-1 N3): `.eq("store_code", store)` never
        # matches a NULL store_code row, so it silently dropped an unresolved closing row the instant a
        # store filter was active — the same value-space bug already fixed for the x_report/sales legs.
        # Always fetch the full day; the store filter is applied POST-HOC below via the shared `_keep`
        # rule, uniformly with the other 3 legs (latent — the current frontend never sends `store=`).
        return (client.schema("commcalc").table("daily_closing").select(cols)
                .eq("org_id", org_id).eq("close_date", d).limit(50000).execute().data) or []
    try:
        _crows = _closing_rows("store_code,store_address,t_cash,t_credit,t_ext_cc,t_gift,t_store_acct,t_zelle,t_acima,tenders")
    except Exception:
        try:
            _crows = _closing_rows("store_code,store_address,t_cash,t_credit,t_ext_cc,t_gift,t_store_acct,t_zelle,t_acima")
        except Exception:
            _crows = _closing_rows("store_code,store_address,t_cash,t_credit,t_ext_cc,t_gift,t_store_acct,t_zelle")
    addr_by_code = {}
    for r in _crows:
        code = r.get("store_code") or "?"
        if r.get("store_address"):
            addr_by_code[code] = r.get("store_address")
        agg = closing.setdefault(code, {t: 0.0 for t in keys})
        for t in keys:
            agg[t] += _closing_amt(r, t)
    # store names / resolved-code set — fetched BEFORE the X-report/sales legs so both can tell a
    # genuinely-resolved store_code apart from a raw/unmatched store string (needed for the "never drop
    # an unresolved store under a filter" rule below — same value-space class fixed in retail-ops-14 B1).
    sm = (client.schema("commcalc").table("store_mapping").select("store_code,store_address")
          .eq("org_id", org_id).execute().data) or []
    name_by_code = {s.get("store_code"): s.get("store_address") for s in sm if s.get("store_code")}
    resolved_codes = set(name_by_code)
    # (2) X-report — pos_tender_summary raw tender_type → tenant tender (fallback _canon_tender). A raw
    # label that resolves to NO tender (no tenant-map rule matched AND the hardcoded fallback either
    # doesn't recognize it or isn't on this tenant's axis) is NEVER dropped — its dollars land in
    # `xrep_unmapped` (surfaced in the response + UI below) instead of silently vanishing. This closes
    # the "3-way tender is not pulling in data from x-report" bug class: pos_tender_summary can hold rows
    # whose raw tender_type isn't in _canon_tender's substring vocabulary (e.g. a bare 'CC'/'Chip'/'Check'
    # label, or a custom-tender-axis tenant with no explicit map rule yet) — those dollars used to just
    # disappear from this leg while the dashboard's `_xreport_tenders_by_store` (which reads the
    # pre-classified tender_class column, never a resolver) kept showing them, exactly the asymmetry that
    # made this page look broken while pos_tender_summary actually had rows.
    resolve = _addr_resolver(client, org_id)
    xrep, xrep_unmapped = {}, {}
    xrows = (client.schema("commcalc").table("pos_tender_summary")
             .select("store,tender_type,amount").eq("org_id", org_id).eq("close_date", d).execute().data) or []
    for r in xrows:
        code = resolve(r.get("store")) or (r.get("store") or "?")
        amt = _f(r.get("amount"))
        canon = resolve_x(r.get("tender_type"))
        # 2026-07-28 Gate-1 N1: `canon` can be truthy-but-not-on-axis — an explicit tenant map rule
        # (closing_tender_map) returns its `tender_key` verbatim with NO validation against the active
        # `closing_tender_def` axis at save time, so a deactivated/typo'd def leaves a live rule pointing
        # at a dead key. That used to take the `if canon:` branch, fail the `canon in agg` check, and
        # drop the dollars with NO signal at all (neither bucketed NOR in xrep_unmapped) — the same
        # silent-disappearance bug this whole package exists to fix, just reached via a different code
        # path. Checking `canon in keys` (the tenant's actual axis) up front closes both paths at once.
        if canon and canon in keys:
            agg = xrep.setdefault(code, {t: 0.0 for t in keys})
            agg[canon] += amt
        else:
            u = xrep_unmapped.setdefault(code, {"amount": 0.0, "labels": set()})
            u["amount"] += amt
            lbl = str(r.get("tender_type") or "").strip()
            if lbl:
                u["labels"].add(lbl)
    # (3) sales transactions (same tenant axis + resolver). `sales_unmapped` mirrors `xrep_unmapped`
    # (2026-07-28 Gate-1 N2) — the sales leg had the identical silent-drop pattern (both the no-match
    # case and the truthy-but-off-axis case from N1), now surfaced the same way instead of just quietly
    # producing an unexplained sales-vs-x_report delta.
    sales_unmapped = {}
    sales = _sales_tenders_by_store(client, org_id, d, resolve_s, keys, unmapped_out=sales_unmapped)
    if store:
        # An unresolved store (its pos_tender_summary/raw_sales/daily_closing `store` string never
        # matched store_mapping, so the dict key here is the raw address/"?" placeholder, not a real
        # store_code) is NEVER dropped by this filter — a real store_code selection can only ever equal
        # another REAL store_code, so a raw/unmatched key can't be "the wrong store" the way a resolved
        # code can. Mirrors the analogous "unresolved row bypasses a filter" rule already applied to
        # /closing/summary + /closing/rollup (retail-ops-14 B1/NIT-4a). Applied uniformly to all four
        # legs (2026-07-28 Gate-1 N3 folded the closing leg in — it used to filter at the DB level with
        # `.eq("store_code", store)`, which silently drops a NULL-store_code row for ANY filter value).
        def _keep(k):
            return k == store or k not in resolved_codes
        closing = {k: v for k, v in closing.items() if _keep(k)}
        xrep = {k: v for k, v in xrep.items() if _keep(k)}
        xrep_unmapped = {k: v for k, v in xrep_unmapped.items() if _keep(k)}
        sales = {k: v for k, v in sales.items() if _keep(k)}
        sales_unmapped = {k: v for k, v in sales_unmapped.items() if _keep(k)}
    codes = sorted(set(closing) | set(xrep) | set(sales) | set(xrep_unmapped) | set(sales_unmapped))
    stores_out = []
    for code in codes:
        c, x, s = closing.get(code, {}), xrep.get(code, {}), sales.get(code, {})
        per = []
        for t in keys:
            cv, xv, sv = round(c.get(t, 0), 2), round(x.get(t, 0), 2), round(s.get(t, 0), 2)
            per.append({"tender": t, "label": tlabel.get(t, t), "closing": cv, "x_report": xv, "sales": sv,
                        "match": abs(cv - xv) <= 1 and abs(xv - sv) <= 1 and abs(cv - sv) <= 1})
        um, sm_u = xrep_unmapped.get(code), sales_unmapped.get(code)
        stores_out.append({
            "store_code": code, "store_address": name_by_code.get(code) or addr_by_code.get(code) or code,
            "tenders": per,
            # Additive-only, never touches the byte-identical `tenders`/`totals` comparison above — the
            # X-report/sales dollars this store had that no rule/fallback could bucket into a known
            # tender, so recon math for a fully-mapped tenant/day is untouched, but a partial/no-map gap
            # is now VISIBLE instead of silently reading as "no data at all".
            "x_report_unmapped": ({"amount": round(um["amount"], 2), "raw_labels": sorted(um["labels"])}
                                   if um else None),
            "sales_unmapped": ({"amount": round(sm_u["amount"], 2), "raw_labels": sorted(sm_u["labels"])}
                                if sm_u else None),
            "totals": {"closing": round(sum(c.values()), 2), "x_report": round(sum(x.values()), 2),
                       "sales": round(sum(s.values()), 2)}})
    # x_report_ever: has this tenant EVER had ANY X-report imported (vs just missing for THIS day)?
    # Sharpens the honest-empty message below — a tenant that's never had one needs its mailbox rule /
    # b2bsoft schedule checked (config/delivery); a tenant that normally has one just needs today's file.
    # Truth for TODAY is the raw `xrows` fetch (pos_tender_summary actually has rows), never the
    # post-resolver `xrep` — a day where every raw label happened to be unmapped must still count as
    # "X-report data exists", or this signal would falsely claim "never imported" for a real delivery.
    x_report_ever = bool(xrows)
    if not x_report_ever:
        try:
            x_report_ever = bool((client.schema("commcalc").table("pos_tender_summary").select("close_date")
                                  .eq("org_id", org_id).limit(1).execute().data) or [])
        except Exception:
            x_report_ever = False

    # ── 4TH LEG (mig 502, retail-ops-7 item 4): BANK DEPOSIT, additive-only. The 3 legs above
    #    (closing/x_report/sales — `tenders`, `totals`) are computed EXACTLY as before this change;
    #    this block only APPENDS a new `bank_deposit` key per store, nothing upstream is re-touched. ──
    dep_cfg = _deposit_config(client, org_id)
    bank_by_store = {}
    def _bank_rows_3way(cols):
        bq = client.schema("commcalc").table("bank_deposit").select(cols).eq("org_id", org_id).eq("close_date", d)
        return (bq.eq("store_code", store) if store else bq).limit(5000).execute().data or []
    try:
        _brows3 = _bank_rows_3way("store_code,amount,ocr_amount,ocr_match,receipt_path")
    except Exception:
        try:
            _brows3 = _bank_rows_3way("store_code,amount,receipt_path")   # OCR columns (mig 502) not run yet
        except Exception:
            _brows3 = []   # bank_deposit table (mig 107) not run yet -> no 4th leg, 3-way unaffected
    for r in _brows3:
        code = r.get("store_code") or "?"
        b = bank_by_store.setdefault(code, {"amount": 0.0, "n": 0, "any_mismatch": False, "any_receipt": False})
        b["amount"] += _f(r.get("amount"))
        b["n"] += 1
        if r.get("ocr_match") == "mismatch":
            b["any_mismatch"] = True
        if r.get("receipt_path"):
            b["any_receipt"] = True
    for s in stores_out:
        code = s.get("store_code")
        declared_basis = None
        if code and code != "?":
            declared_basis, _n = _bank_deposit_declared(client, org_id, code, d, dep_cfg["match_target"])
        bk = bank_by_store.get(code)
        deposited = round(bk["amount"], 2) if bk else None
        var = round((declared_basis or 0) - deposited, 2) if (bk and declared_basis is not None) else None
        s["bank_deposit"] = {
            "match_target": dep_cfg["match_target"], "declared": declared_basis, "deposited": deposited,
            "var": var, "flag": bool(var is not None and abs(var) > 1),
            "has_deposit": bool(bk), "deposit_count": bk["n"] if bk else 0,
            "any_mismatch_flag": bool(bk and bk.get("any_mismatch")),
        }

    x_report_unmapped_total = round(sum(u["amount"] for u in xrep_unmapped.values()), 2)
    sales_unmapped_total = round(sum(u["amount"] for u in sales_unmapped.values()), 2)
    note = ("X-report tender amounts include tax; sales-transaction figures are merchandise "
            "(ext price), so small deltas between those two are expected. Bank Deposit is compared "
            f"against the tenant's configured basis ({dep_cfg['match_target'].replace('_', ' ')}).")
    if not x_report_ever:
        note += (" This tenant has NEVER had a POS X-report imported — check (1) the mailbox has an "
                 "*X-Report* -> x_report rule and (2) b2bsoft is actually scheduled to email an "
                 "X-Report for this tenant.")
    if x_report_unmapped_total:
        note += (f" ⚠ ${x_report_unmapped_total:,.2f} of X-report tenders used a raw label this "
                 "tenant's mapping doesn't recognize (see the ⚠ marker per store below) — map it on "
                 "/closing/tender-config or it will keep showing outside the tender breakdown.")
    if sales_unmapped_total:
        note += (f" ⚠ ${sales_unmapped_total:,.2f} of sales-transaction tenders used a raw label this "
                 "tenant's mapping doesn't recognize.")
    return {"date": d, "tenders": [{"key": t, "label": tlabel.get(t, t)} for t in keys],
            "stores": stores_out,
            # x_report here is RAW presence (any pos_tender_summary row today), not "every row mapped" —
            # a resolver drop must never flip this badge to "not loaded" when the X-report import itself
            # actually succeeded (see x_report_unmapped for the drop signal instead).
            "sources_present": {"closing": bool(closing), "x_report": bool(xrows), "sales": bool(sales),
                                "bank_deposit": bool(bank_by_store)},
            "x_report_ever": x_report_ever,
            "x_report_unmapped_total": x_report_unmapped_total,
            "sales_unmapped_total": sales_unmapped_total,
            "note": note}


@router.get("/tender-drilldown")
def tender_drilldown(date: str, store: str = None, tender: str = None, org_id: str = ORG_ID):
    """Every sales-transaction line for a day (optionally one store / one canonical tender) — so a manager
    can see exactly which transactions fell under External CC / Gift Card / Store Account / Zelle / etc."""
    require_org(org_id)
    client = sb()
    d = _date(date)
    if not d:
        raise HTTPException(400, "valid date required (YYYY-MM-DD)")
    resolve = _addr_resolver(client, org_id)
    rows = _b2b_sales_rows(client, org_id, d,
                           "store,trans_id,salesperson,tender_type,product_desc,ext_price,mdn,voided,trans_type")
    out = []
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        canon = _canon_tender(r.get("tender_type"))
        code = resolve(r.get("store")) or (r.get("store") or "?")
        if store and code != store:
            continue
        if tender and canon != tender:
            continue
        out.append({"store_code": code, "trans_id": r.get("trans_id"), "salesperson": r.get("salesperson"),
                    "tender_type": r.get("tender_type"), "canon": canon,
                    "canon_label": CANON_TENDER_LABEL.get(canon, "(unmapped)"),
                    "product_desc": r.get("product_desc"), "amount": round(_f(r.get("ext_price")), 2),
                    "mdn": r.get("mdn"), "is_return": str(r.get("trans_type") or "").strip() == "Return"})
    out.sort(key=lambda x: (x["store_code"] or "", str(x["trans_id"] or "")))
    return {"date": d, "rows": out, "count": len(out), "total": round(sum(x["amount"] for x in out), 2)}


# ── Configurable tenders (mig 111): standard-or-custom tender fields + smart raw-label→tender map ─────
@router.get("/tender-config")
def get_tender_config(org_id: str = ORG_ID):
    """The tenant's tender field definitions + raw-label→tender maps + the built-in standard template +
    recon mode. Empty defs → the app uses the built-in 7 (CANON_TENDERS); the wizard shows those as the
    starting point."""
    require_org(org_id)
    client = sb()
    from .tender_config import load_tender_config, STANDARD_DEFS
    defs, maps = load_tender_config(client, org_id)
    mode, custom = "3way", False
    try:
        t = (client.schema("storeops").table("tenants").select("closing_recon_mode,closing_tenders_custom")
             .eq("org_id", org_id).limit(1).execute().data or [])
        if t:
            mode = t[0].get("closing_recon_mode") or "3way"
            custom = bool(t[0].get("closing_tenders_custom"))
    except Exception:
        pass
    standard = [{"tender_key": k, "label": lbl, "recon_class": rc, "is_standard": True, "include_in_total": intot}
                for (k, lbl, rc, intot) in STANDARD_DEFS]
    return {"defs": defs, "maps": maps, "standard": standard, "recon_mode": mode, "custom": custom}


@router.put("/tender-config")
def put_tender_config(payload: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Save the tenant's tender defs + maps + recon mode. Body {defs:[...], maps:[...], recon_mode, custom}.
    Full replace (delete-then-insert) — the wizard always sends the complete set. Gated to the 'closing'
    settings area (2026-07-26 settings audit: /closing/tender-config is already nav-restricted to
    company-wide scope, but the backend had no matching check — a market-scoped caller who knew the
    endpoint could still write it)."""
    require_org(org_id)
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing tender configuration is permission-restricted.")
    defs = payload.get("defs") or []
    maps = payload.get("maps") or []
    client.schema("commcalc").table("closing_tender_def").delete().eq("org_id", org_id).execute()
    rows = []
    for i, dd in enumerate(defs):
        key = (dd.get("tender_key") or "").strip()
        if not key:
            continue
        rows.append({"org_id": org_id, "tender_key": key, "label": dd.get("label") or key,
                     "sort_order": dd.get("sort_order", i), "is_standard": bool(dd.get("is_standard")),
                     "is_active": dd.get("is_active", True) is not False,
                     "recon_class": dd.get("recon_class") or "other",
                     "include_in_total": dd.get("include_in_total", True) is not False})
    if rows:
        client.schema("commcalc").table("closing_tender_def").insert(rows).execute()
    client.schema("commcalc").table("closing_tender_map").delete().eq("org_id", org_id).execute()
    mrows = []
    for m in maps:
        key = (m.get("tender_key") or "").strip()
        labels = [str(x).strip() for x in (m.get("source_labels") or []) if str(x).strip()]
        if not key or not labels:
            continue
        mrows.append({"org_id": org_id, "tender_key": key, "report": m.get("report") or "both",
                      "source_labels": labels, "match_mode": m.get("match_mode") or "substring",
                      "priority": m.get("priority", 100)})
    if mrows:
        client.schema("commcalc").table("closing_tender_map").insert(mrows).execute()
    try:
        upd = {}
        if "recon_mode" in payload:
            upd["closing_recon_mode"] = payload.get("recon_mode") or "3way"
        if "custom" in payload:
            upd["closing_tenders_custom"] = bool(payload.get("custom"))
        if upd:
            client.schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    except Exception:
        pass
    return {"ok": True, "defs": len(rows), "maps": len(mrows)}


@router.post("/tender-config/seed-standard")
def seed_standard_tenders(org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Seed the 7 built-in tenders as editable defs — the starting point for a 'standard' tenant."""
    require_org(org_id)
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing tender configuration is permission-restricted.")
    from .tender_config import STANDARD_DEFS
    client.schema("commcalc").table("closing_tender_def").delete().eq("org_id", org_id).execute()
    rows = [{"org_id": org_id, "tender_key": k, "label": lbl, "sort_order": i,
             "is_standard": True, "is_active": True, "recon_class": rc, "include_in_total": intot}
            for i, (k, lbl, rc, intot) in enumerate(STANDARD_DEFS)]
    client.schema("commcalc").table("closing_tender_def").insert(rows).execute()
    return {"ok": True, "seeded": len(rows)}


@router.post("/tender-config/detect")
async def detect_tenders(file: UploadFile = File(None), leg: str = Form("auto"), org_id: str = ORG_ID):
    """Smart mapping helper: gather the distinct raw Tender Type values in the tenant's data and SUGGEST
    the best tender per value with a confidence. Sources: an uploaded sample, ELSE the already-ingested
    Sales report (raw_sales + daily_sales_feed) + X-report (pos_tender_summary). The wizard pre-fills
    each dropdown with the suggestion. On the Total side both reports are b2bsoft.

    2026-07-15 fix: an uploaded sample used to ALWAYS land in the sales leg — a tenant with no
    ingested X-report data (e.g. Luxelink) had no way to upload an X-Report sample to map that leg at
    all. `leg` ('sales'|'x_report'|'auto', default 'auto') lets the Step-2 wizard force a leg via its
    explicit upload buttons; 'auto' classifies the file by its own column shape (see
    tender_config.classify_sample_file) and the response's `detected_leg`/`detect_detail` tell the UI
    which leg it landed in and why. `org_id` stays a query param (never a Form field) per the
    multi-tenant rule. Backward compatible: an omitted `leg` behaves as 'auto', and a plain
    tender-column file with no X-report signature still lands in `sales` exactly as before."""
    require_org(org_id)
    client = sb()
    from .tender_config import (load_tender_config, tender_axis, suggest_for_labels,
                                 classify_sample_file, db_sales_tender_labels)
    defs, _maps = load_tender_config(client, org_id)
    keys, labels, _rc, _it = tender_axis(defs, CANON_TENDERS, CANON_TENDER_LABEL)
    sales_labels, x_labels = set(), set()
    detected_leg, detect_detail = None, None
    if file is not None:
        try:
            content = await file.read()
            detected_leg, found, detect_detail = classify_sample_file(content, file.filename or "", leg)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(400, f"could not read sample file: {e}")
        if detected_leg == "x_report":
            x_labels |= found
        else:
            sales_labels |= found
    else:
        sales_labels |= db_sales_tender_labels(client, org_id)
        try:
            xrows = (client.schema("commcalc").table("pos_tender_summary").select("tender_type")
                     .eq("org_id", org_id).limit(20000).execute().data) or []
            x_labels |= {str(r.get("tender_type")).strip() for r in xrows if str(r.get("tender_type") or "").strip()}
        except Exception:
            pass
    return {"tenders": [{"key": k, "label": labels.get(k, k)} for k in keys],
            "sales": suggest_for_labels(sorted(sales_labels), keys, labels, _canon_tender),
            "x_report": suggest_for_labels(sorted(x_labels), keys, labels, _canon_tender),
            "detected_leg": detected_leg, "detect_detail": detect_detail}


# ── Configurable activation-count fields (mig 501): standard-or-custom count fields on the closing ──
# sheet, mirroring the tender-config endpoints above. Empty defs -> the app uses the built-in 3
# (upgrade_count/new_line_count/postpaid_count); the wizard shows those as the starting point.
@router.get("/count-config")
def get_count_config(org_id: str = ORG_ID):
    """The tenant's activation-count field definitions + the built-in standard template. Empty defs
    means the rep form / DM verify view / recon all use the hardcoded 3 fields."""
    require_org(org_id)
    client = sb()
    from . import count_config
    defs = count_config.load_count_config(client, org_id)
    standard = [{"field_key": k, "label": lbl, "recon_class": rc, "sort_order": so, "is_standard": True}
                for (k, lbl, rc, so) in count_config.STANDARD_DEFS]
    return {"defs": defs, "standard": standard}


@router.put("/count-config")
def put_count_config(payload: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Save the tenant's count-field defs. Body {defs:[...]}. Full replace (delete-then-insert) — the
    wizard always sends the complete set. Saving an EMPTY list reverts the tenant to the hardcoded 3.
    Gated to the 'closing' settings area (2026-07-26 settings audit — same rationale as tender-config)."""
    require_org(org_id)
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing count-field configuration is permission-restricted.")
    defs = payload.get("defs") or []
    try:
        client.schema("commcalc").table("closing_count_field_def").delete().eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — run migration 501_closing_count_field_registry.sql first. [{e}]")
    rows = []
    for i, dd in enumerate(defs):
        key = (dd.get("field_key") or "").strip()
        if not key:
            continue
        rows.append({"org_id": org_id, "field_key": key, "label": dd.get("label") or key,
                     "sort_order": dd.get("sort_order", i), "is_standard": bool(dd.get("is_standard")),
                     "is_active": dd.get("is_active", True) is not False,
                     "recon_class": dd.get("recon_class") or "other"})
    if rows:
        client.schema("commcalc").table("closing_count_field_def").insert(rows).execute()
    return {"ok": True, "defs": len(rows)}


@router.post("/count-config/seed-standard")
def seed_standard_counts(org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Seed the 3 built-in count fields as editable defs — the starting point for a customized tenant."""
    require_org(org_id)
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing count-field configuration is permission-restricted.")
    from . import count_config
    client.schema("commcalc").table("closing_count_field_def").delete().eq("org_id", org_id).execute()
    rows = [{"org_id": org_id, "field_key": k, "label": lbl, "sort_order": so,
             "is_standard": True, "is_active": True, "recon_class": rc}
            for (k, lbl, rc, so) in count_config.STANDARD_DEFS]
    client.schema("commcalc").table("closing_count_field_def").insert(rows).execute()
    return {"ok": True, "seeded": len(rows)}


# ── Reconciliation sheet: every day's closing-vs-B2B errors over a period ────────────────────
# ── ePay bill-payment reconciliation: declared (closing) vs sales (by tender) vs bank-deposited ──────
_EPAY_CATS = {"bill payments", "other carr. payments", "other carr payments", "bill payment"}
_EPAY_KWS = ("epay", "rtr", "refill", "recharge", "wallet funding", "access charge",
             "top up", "topup", "airtime", "bill pay")


def _is_epay(dept, cat, product):
    if (cat or "").strip().lower() in _EPAY_CATS:
        return True
    p = (product or "").strip().lower()
    return bool(p) and any(k in p for k in _EPAY_KWS)


def _epay_sales_by_store(client, org_id, date, store=None):
    """Bill-payment (ePay) sales $ per store, SPLIT BY TENDER (cash / credit / acima). Best-effort:
    identifies bill-payment lines by category or product keyword, then buckets ext_price by tender."""
    resolve = _addr_resolver(client, org_id)
    try:
        rows = _b2b_sales_rows(client, org_id, date,
                               "store,department,category,product_desc,tender_type,ext_price,voided,trans_type")
    except Exception:
        rows = []
    out = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        if not _is_epay(r.get("department"), r.get("category"), r.get("product_desc")):
            continue
        canon = _canon_tender(r.get("tender_type"))
        bucket = "cash" if canon == "cash" else ("credit" if canon in ("credit", "ext_cc") else ("acima" if canon == "acima" else None))
        if not bucket:
            continue
        code = resolve(r.get("store")) or (r.get("store") or "?")
        if store and code != store:
            continue
        out.setdefault(code, {"cash": 0.0, "credit": 0.0, "acima": 0.0})[bucket] += _f(r.get("ext_price"))
    return out


# ── Bank-deposit slip OCR + configurable match target (mig 502, retail-ops-7 item 3) ─────────────
_DEPOSIT_MATCH_TARGETS = ("bill_payment_cash", "store_cash", "total_cash")
_DEFAULT_OCR_MODEL = "claude-haiku-4-5-20251001"   # cheap vision model; tenant-overridable (never hard-wired
                                                    # into a payout/money-COMPUTATION path — display/verify only)


def _deposit_config(client, org_id: str) -> dict:
    """Per-tenant bank-deposit OCR settings (mig 502). Missing table/row -> the documented default
    (total_cash / the coded default model), so an un-configured tenant still gets OCR verification."""
    try:
        rows = (client.schema("commcalc").table("closing_deposit_config").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        rows = []
    c = rows[0] if rows else {}
    target = (c.get("match_target") or "total_cash").strip()
    if target not in _DEPOSIT_MATCH_TARGETS:
        target = "total_cash"
    return {"match_target": target, "ocr_model": (c.get("ocr_model") or "").strip() or _DEFAULT_OCR_MODEL}


def _bank_deposit_declared(client, org_id: str, store_code: str, close_date: str, target: str):
    """The declared-cash basis a bank deposit is checked against, per the tenant's configured
    match_target — computed from the SAME daily_closing tender figures every other closing recon
    surface already uses (never re-derives/adjusts a dollar amount, only picks WHICH already-computed
    figure to compare):
      bill_payment_cash = sum(epay_on_cash)          — ePay/bill-payment cash only (a subset)
      store_cash        = sum(t_cash) - sum(epay_on_cash) — register cash, excluding the epay portion
      total_cash        = sum(t_cash)                — everything declared as cash (the full envelope; DEFAULT)
    Returns (amount, rep_row_count)."""
    try:
        rows = (client.schema("commcalc").table("daily_closing")
                .select("t_cash,store_cash,epay_on_cash").eq("org_id", org_id)
                .eq("store_code", store_code).eq("close_date", close_date).limit(5000).execute().data) or []
    except Exception:
        rows = []
    total = 0.0
    for r in rows:
        cash = _f(r.get("t_cash"))
        if not cash:
            cash = _f(r.get("store_cash"))   # pre-mig-103 tenants never populated t_cash
        epay = _f(r.get("epay_on_cash"))
        if target == "bill_payment_cash":
            total += epay
        elif target == "store_cash":
            total += max(cash - epay, 0.0)
        else:
            total += cash
    return round(total, 2), len(rows)


def _ocr_bank_deposit_slip(raw: bytes, ext: str, model: str):
    """Read {amount, date, bank_name} off a bank DEPOSIT SLIP with Claude vision (reuses the account
    module's Anthropic client pattern — see account/engine.py). Returns (amount_or_None, detail_dict,
    status) where status is one of: 'ocr_unavailable' (no key / lib missing — degrade to manual entry),
    'unreadable' (ran but couldn't extract an amount), or None (amount extracted; caller classifies
    matched/mismatch/pending once it knows the declared basis)."""
    if not settings.ANTHROPIC_API_KEY or not raw:
        return None, {"skipped": "ANTHROPIC_API_KEY not set — enter the deposit amount manually"}, "ocr_unavailable"
    try:
        from anthropic import Anthropic
    except Exception as e:
        return None, {"skipped": f"anthropic library not installed: {e}"}, "ocr_unavailable"
    try:
        import json as _json
        cli = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        media = "image/png" if ext == "png" else "image/jpeg"
        b64 = base64.b64encode(raw).decode("ascii")
        msg = cli.messages.create(
            model=model, max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                {"type": "text", "text": "This is a bank DEPOSIT SLIP. Return ONLY compact JSON: "
                 '{"amount": <number|null>, "date": "<YYYY-MM-DD|null>", "bank_name": "<string|null>"}. '
                 "amount is the TOTAL amount deposited (no $ or commas). If any field is unreadable, use null."}]}])
        text = "".join(getattr(b, "text", "") for b in msg.content) if msg.content else ""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        data = _json.loads(text[text.find("{"): text.rfind("}") + 1])
        amt = data.get("amount")
        return (float(amt) if amt is not None else None), data, (None if amt is not None else "unreadable")
    except Exception as e:
        return None, {"error": str(e)[:200]}, "unreadable"


@router.get("/deposit-config")
def get_deposit_config(org_id: str = ORG_ID):
    cfg = _deposit_config(sb(), org_id)
    cfg["anthropic_configured"] = bool(settings.ANTHROPIC_API_KEY)
    cfg["match_targets"] = list(_DEPOSIT_MATCH_TARGETS)
    return cfg


@router.put("/deposit-config")
def put_deposit_config(body: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """2026-07-26 settings audit: this was the ONE gap already flagged by a prior Gate-1 review
    ('deposit-config PUT ungated = SETTING_AREAS doctrine gap') — closed the same way as the other
    closing settings writes."""
    if not _can_edit_closing_setting(_caller_perms(sb(), authorization)):
        raise HTTPException(403, "Editing deposit-recon configuration is permission-restricted.")
    target = (body.get("match_target") or "total_cash").strip()
    if target not in _DEPOSIT_MATCH_TARGETS:
        raise HTTPException(400, f"match_target must be one of {_DEPOSIT_MATCH_TARGETS}")
    row = {"org_id": org_id, "match_target": target, "updated_at": _now()}
    model = (body.get("ocr_model") or "").strip()
    if model:
        row["ocr_model"] = model
    try:
        sb().schema("commcalc").table("closing_deposit_config").upsert(row, on_conflict="org_id").execute()
    except Exception:
        raise HTTPException(400, "run migration 502 first (commcalc.closing_deposit_config)")
    return get_deposit_config(org_id)


@router.post("/bank-deposit")
async def bank_deposit(body: dict, org_id: str = ORG_ID):
    """Record a bank deposit (ePay cash / store cash reps collected and deposited). One row per
    deposit; reconciled vs the tenant's CONFIGURED declared-cash basis (bill_payment_cash | store_cash |
    total_cash — see /closing/deposit-config). Accepts EITHER an already-uploaded `receipt_path` OR an
    inline `slip` (data URL) to upload now — if a slip is provided and no `amount`, Claude vision OCRs
    it (mig 502). OCR NEVER blocks the deposit from saving and NEVER silently swallows a mismatch — a
    mismatch is stored honestly on the row (`ocr_match`) and alerted (scope 'deposit_mismatch'), for
    management review, never auto-corrected. Missing key/lib -> 'ocr_unavailable', with `manual_confirmed`
    as the degrade path (a human checks the box after eyeballing the slip themselves)."""
    require_org(org_id)
    client = sb()
    d = _date(body.get("close_date")) or body.get("close_date")
    store = (body.get("store_code") or "").strip() or None
    row = {"org_id": org_id, "close_date": d, "period": (str(d)[:7] if d else None),
           "store_code": store,
           "store_address": body.get("store_name") or body.get("store_address"),
           "employee_name": (body.get("employee_name") or "").strip() or None,
           "amount": _money(body.get("amount")),
           "receipt_path": body.get("receipt_path") or body.get("receipt") or None,
           "handed_to": (body.get("handed_to") or "").strip() or None,
           "note": (body.get("note") or "").strip() or None}

    cfg = _deposit_config(client, org_id)
    ocr_amount, ocr_detail, ocr_status = None, None, "pending"
    slip = body.get("slip")
    if slip and "," in str(slip):
        try:
            header, b64 = str(slip).split(",", 1)
            raw = base64.b64decode(b64)
            ext = "png" if "png" in header else "jpg"
            path = _upload_envelope(org_id, slip)   # reuse the private closing-envelopes bucket
            if path:
                row["receipt_path"] = path
            ocr_amount, ocr_detail, ocr_status = _ocr_bank_deposit_slip(raw, ext, cfg["ocr_model"])
        except Exception as e:
            ocr_detail, ocr_status = {"error": str(e)[:200]}, "unreadable"
    elif body.get("manual_confirmed"):
        ocr_status = "manual_confirmed"

    declared_amount, _rep_n = (None, 0)
    if store and d:
        declared_amount, _rep_n = _bank_deposit_declared(client, org_id, store, d, cfg["match_target"])

    if ocr_amount is not None:
        if row["amount"] <= 0:
            row["amount"] = round(ocr_amount, 2)   # OCR fills the amount when the caller didn't type one
        ocr_status = ("matched" if abs(ocr_amount - declared_amount) <= 1.0 else "mismatch") \
            if declared_amount is not None else "pending"   # nothing to compare against yet -> honest pending, not a verdict

    row.update({
        "ocr_amount": ocr_amount,
        "ocr_date": (ocr_detail or {}).get("date") if isinstance(ocr_detail, dict) else None,
        "ocr_bank_name": (ocr_detail or {}).get("bank_name") if isinstance(ocr_detail, dict) else None,
        "ocr_match": ocr_status, "ocr_detail": ocr_detail, "match_target": cfg["match_target"],
        "declared_amount": declared_amount, "manual_confirmed": bool(body.get("manual_confirmed")),
    })
    try:
        r = client.schema("commcalc").table("bank_deposit").insert(row).execute()
    except Exception:
        # mig 502 not yet run -> the new OCR/config columns don't exist on bank_deposit. Degrade to the
        # pre-502 row shape (receipt_path / amount / handed_to / note still save).
        for k in ("ocr_amount", "ocr_date", "ocr_bank_name", "ocr_match", "ocr_detail",
                  "match_target", "declared_amount", "manual_confirmed"):
            row.pop(k, None)
        r = client.schema("commcalc").table("bank_deposit").insert(row).execute()
    saved = (r.data or [row])[0]

    if ocr_status == "mismatch":
        summary = (f"Bank deposit MISMATCH — {row.get('store_address') or store or '—'} on {d}: the slip "
                   f"reads {_usd(ocr_amount)} but {cfg['match_target'].replace('_', ' ')} was "
                   f"{_usd(declared_amount)} (off by {_usd(round((ocr_amount or 0) - (declared_amount or 0), 2))}). "
                   f"Needs management review — never auto-corrected.")
        try:
            await _send_alert(client, org_id, "deposit_mismatch", "⚠️ Bank deposit mismatch", summary,
                              ref_key=f"bankdep|{store}|{d}|{saved.get('id')}", store_code=store)
        except Exception:
            pass
    return {"ok": True, "row": saved, "ocr": ocr_detail, "ocr_match": ocr_status,
            "declared_amount": declared_amount, "match_target": cfg["match_target"]}


@router.get("/epay-recon")
def epay_recon(date: str, store: str = None, tolerance: float = 1.0, org_id: str = ORG_ID):
    """ePay bill-payment reconciliation for a day, per store: DECLARED ePay (closing epay_on_* fields) vs
    ACTUAL bill-payments from sales (by tender) vs BANK-DEPOSITED (bank_deposit receipts). The headline
    variance is declared ePay CASH vs what was deposited in the bank."""
    require_org(org_id)
    client = sb()
    d = _date(date)
    if not d:
        raise HTTPException(400, "valid date required (YYYY-MM-DD)")

    def _dc(cols):
        q = client.schema("commcalc").table("daily_closing").select(cols).eq("org_id", org_id).eq("close_date", d)
        if store:
            q = q.eq("store_code", store)
        return q.limit(50000).execute().data or []
    try:
        drows = _dc("store_code,store_address,employee_name,epay_on_cash,epay_on_credit,epay_on_acima")
    except Exception:
        drows = []   # mig 106 not run yet
    declared = {}
    for r in drows:
        code = r.get("store_code") or "?"
        s = declared.setdefault(code, {"store_address": r.get("store_address"), "cash": 0.0, "credit": 0.0, "acima": 0.0, "reps": []})
        c, cr, a = _f(r.get("epay_on_cash")), _f(r.get("epay_on_credit")), _f(r.get("epay_on_acima"))
        s["cash"] += c; s["credit"] += cr; s["acima"] += a
        s["reps"].append({"employee_name": r.get("employee_name"), "cash": round(c, 2), "credit": round(cr, 2), "acima": round(a, 2)})

    bank = {}
    _BANK_COLS_BASE = "store_code,amount,receipt_path,employee_name,handed_to,note"
    _BANK_COLS_OCR = _BANK_COLS_BASE + ",ocr_amount,ocr_match,ocr_date,ocr_bank_name,match_target,declared_amount"

    def _bq(cols):
        q = client.schema("commcalc").table("bank_deposit").select(cols).eq("org_id", org_id).eq("close_date", d)
        return q.eq("store_code", store) if store else q
    try:
        _brows = _bq(_BANK_COLS_OCR).limit(50000).execute().data or []
    except Exception:
        try:
            _brows = _bq(_BANK_COLS_BASE).limit(50000).execute().data or []   # mig 502 OCR columns not run yet
        except Exception:
            _brows = []   # mig 107 not run yet
    for r in _brows:
        code = r.get("store_code") or "?"
        b = bank.setdefault(code, {"amount": 0.0, "deposits": []})
        b["amount"] += _f(r.get("amount"))
        b["deposits"].append({"amount": round(_f(r.get("amount")), 2), "receipt_path": r.get("receipt_path"),
                              "employee_name": r.get("employee_name"), "handed_to": r.get("handed_to"), "note": r.get("note"),
                              "ocr_amount": r.get("ocr_amount"), "ocr_match": r.get("ocr_match"),
                              "ocr_date": r.get("ocr_date"), "ocr_bank_name": r.get("ocr_bank_name"),
                              "match_target": r.get("match_target"), "declared_amount": r.get("declared_amount")})

    sales = _epay_sales_by_store(client, org_id, d, store)
    codes = [store] if store else sorted(set(declared) | set(bank) | set(sales))
    out = []
    for code in codes:
        dec = declared.get(code, {"store_address": None, "cash": 0.0, "credit": 0.0, "acima": 0.0, "reps": []})
        bk = bank.get(code, {"amount": 0.0, "deposits": []})
        sl = sales.get(code, {"cash": 0.0, "credit": 0.0, "acima": 0.0})
        declared_cash, deposited = round(dec["cash"], 2), round(bk["amount"], 2)
        var = round(declared_cash - deposited, 2)
        out.append({
            "store_code": code, "store_address": dec["store_address"] or code,
            "declared": {"cash": declared_cash, "credit": round(dec["credit"], 2), "acima": round(dec["acima"], 2)},
            "sales": {"cash": round(sl["cash"], 2), "credit": round(sl["credit"], 2), "acima": round(sl["acima"], 2)},
            "bank_deposited": deposited, "cash_variance": var, "flag": abs(var) > tolerance,
            "direction": ("short" if var > tolerance else "over" if var < -tolerance else "ok"),
            "reps": dec["reps"], "deposits": bk["deposits"],
        })
    out.sort(key=lambda x: -abs(x["cash_variance"]))
    return {"date": d, "tolerance": tolerance, "rows": out,
            "totals": {"declared_cash": round(sum(r["declared"]["cash"] for r in out), 2),
                       "bank_deposited": round(sum(r["bank_deposited"] for r in out), 2),
                       "sales_cash": round(sum(r["sales"]["cash"] for r in out), 2),
                       "flagged": sum(1 for r in out if r["flag"]), "stores": len(out)}}


@router.get("/accessory-recon")
def accessory_recon(date: str, store: str = None, tolerance: float = 1.0, org_id: str = ORG_ID):
    """Accessory DECLARED (daily-closing acc_sale, per rep) vs ACTUAL accessory sales (B2B ext_price on
    accessory lines, per store) for a day — so management catches reps entering wrong accessory numbers.
    Accessory is NOT a tender, so this is its own tally (the tender total excludes it)."""
    require_org(org_id)
    client = sb()
    d = _date(date)
    if not d:
        raise HTTPException(400, "valid date required (YYYY-MM-DD)")
    cq = (client.schema("commcalc").table("daily_closing")
          .select("store_code,store_address,employee_name,acc_sale")
          .eq("org_id", org_id).eq("close_date", d))
    if store:
        cq = cq.eq("store_code", store)
    declared = {}
    for r in (cq.limit(50000).execute().data or []):
        code = r.get("store_code") or "?"
        s = declared.setdefault(code, {"store_address": r.get("store_address"), "declared": 0.0, "reps": []})
        v = _f(r.get("acc_sale"))
        s["declared"] += v
        s["reps"].append({"employee_name": r.get("employee_name"), "acc_sale": round(v, 2)})
    b2b = _b2b_money_by_store(client, org_id, d)
    codes = [store] if store else sorted(set(declared) | set(b2b))
    out = []
    for code in codes:
        dec = declared.get(code, {"store_address": None, "declared": 0.0, "reps": []})
        actual = round(_f((b2b.get(code) or {}).get("acc_gross")), 2)
        declared_v = round(dec["declared"], 2)
        var = round(declared_v - actual, 2)
        out.append({
            "store_code": code, "store_address": dec["store_address"] or code,
            "declared": declared_v, "actual": actual, "variance": var,
            "flag": abs(var) > tolerance,
            "direction": ("over" if var > tolerance else "under" if var < -tolerance else "ok"),
            "reps": sorted(dec["reps"], key=lambda x: -_f(x.get("acc_sale"))),
        })
    out.sort(key=lambda x: -abs(x["variance"]))
    return {"date": d, "tolerance": tolerance, "rows": out,
            "totals": {"declared": round(sum(r["declared"] for r in out), 2),
                       "actual": round(sum(r["actual"] for r in out), 2),
                       "flagged": sum(1 for r in out if r["flag"]), "stores": len(out)}}


@router.get("/recon")
def closing_recon(period: str, market: str = None, tolerance: float = 1.0, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-rep (money) + per-store (counts) reconciliation of declared closing vs B2B actuals for
    a YYYY-MM period. Returns one error row per discrepancy with severity block | flag, plus
    recon-pending rows where B2B isn't loaded / the rep didn't match B2B sales."""
    if not period:
        raise HTTPException(400, "period required (YYYY-MM)")
    client = sb()
    closing = (client.schema("commcalc").table("daily_closing").select("*")
               .eq("org_id", org_id).eq("period", period).limit(50000).execute().data) or []
    stores = (client.schema("storeops").table("stores").select("store_code,address,market").eq("org_id", org_id).execute().data) or []
    store_meta = {s.get("store_code"): s for s in stores if s.get("store_code")}
    # Configurable activation-count fields (mig 501) — same fallback-to-hardcoded-3 as closing_summary.
    from . import count_config
    _cdefs = count_config.load_count_config(client, org_id)
    _ckeys, _clabels, _crclass = count_config.count_axis(_cdefs)

    by_date = {}
    for r in closing:
        by_date.setdefault(r.get("close_date"), []).append(r)

    errors = []
    blocks = flags = pending = 0
    for date in sorted((d for d in by_date if d), reverse=True):
        day = _b2b_day(client, org_id, date)
        store_groups = {}
        for r in by_date[date]:
            store_groups.setdefault(r.get("store_code") or f"name:{r.get('store_name') or '—'}", []).append(r)
        for key, reps in store_groups.items():
            code = None if str(key).startswith("name:") else key
            meta = store_meta.get(code, {}) if code else {}
            if market and (meta.get("market") or "") != market:
                continue
            addr = meta.get("address") or (reps[0].get("store_address") if reps else None) or (reps[0].get("store_name") if reps else None)
            for r in reps:
                emp = (r.get("employee_name") or "").strip()
                dcash = _f(r.get("store_cash")) + _f(r.get("epay_cash"))
                dcred = _f(r.get("store_cc")) + _f(r.get("epay_cc"))
                repb = _rep_b2b(day, code, emp) if (code and day["has_data"]) else None
                if repb is None:
                    pending += 1
                    errors.append({"date": date, "store_code": code, "store_address": addr, "rep": emp or "—",
                                   "metric": "recon", "severity": "pending", "status": "recon_pending",
                                   "reason": "B2B not loaded / rep not matched yet", "declared": round(dcash + dcred, 2),
                                   "b2b": None, "variance": None})
                    continue
                for it in _money_issues(dcash, dcred, repb["cash"], repb["card"], tolerance):
                    blocks += it["severity"] == "block"
                    flags += it["severity"] == "flag"
                    errors.append({"date": date, "store_code": code, "store_address": addr, "rep": emp or "—",
                                   "status": it["severity"], **it})
            # store-level count recon
            if code and day["has_data"] and code in day["counts"]:
                cnt = day["counts"][code]
                cl_act = sum(sum(count_config.row_value(x, k) for x in reps) for k in _ckeys if _crclass.get(k) == "activation")
                cl_upg = sum(sum(count_config.row_value(x, k) for x in reps) for k in _ckeys if _crclass.get(k) == "upgrade")
                for metric, dv, bv in (("activations", cl_act, cnt["activations"]), ("upgrades", cl_upg, cnt["upgrades"])):
                    if dv != bv:
                        flags += 1
                        errors.append({"date": date, "store_code": code, "store_address": addr, "rep": None,
                                       "metric": metric, "declared": dv, "b2b": bv, "variance": dv - bv,
                                       "severity": "flag", "status": "flag", "reason": f"{metric.title()} count mismatch (closing {dv} vs B2B {bv})"})

    errors.sort(key=lambda e: (str(e.get("date")), 0 if e["severity"] == "block" else 1 if e["severity"] == "flag" else 2), reverse=True)
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        errors = [e for e in errors if in_keyset(ks, e.get("store_code"), e.get("store_address"))]
        blocks = sum(1 for e in errors if e["severity"] == "block")
        flags = sum(1 for e in errors if e["severity"] == "flag")
        pending = sum(1 for e in errors if e["severity"] == "pending")
    return {"period": period, "errors": errors,
            "summary": {"blocks": blocks, "flags": flags, "pending": pending, "total": len(errors)}}


# ── DM cash-envelope pickup + notify the assigned recipient ─────────────────────────────────
def _email_configured() -> bool:
    try:
        from app.modules.notify.channels import email_resend
        return email_resend.is_configured()
    except Exception:
        return False


def _wa_configured() -> bool:
    try:
        from app.modules.notify.channels import whatsapp_meta
        return whatsapp_meta.is_configured()
    except Exception:
        return False


async def _notify_pickup(client, org_id, dm_name, date, items, total) -> list:
    """Best-effort email + WhatsApp to the assigned recipient after a pickup. Never raises."""
    try:
        rows = (client.schema("commcalc").table("cash_pickup_config").select("*").eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    cfg = rows[0] if rows else {}
    lines = "\n".join(
        f"• {it.get('store_name') or it.get('store_code') or '—'} — {it.get('employee_name') or '—'}: {_usd(it.get('amount') or it.get('cash'))}"
        + (f"  ({it['note']})" if it.get("note") else "")
        for it in items)
    summary = (f"Cash pickup confirmed by {dm_name or 'DM'} on {date}: "
               f"{len(items)} envelope(s), {_usd(total)} total.\n{lines}")
    results = []
    email = (cfg.get("recipient_email") or "").strip()
    if cfg.get("notify_email", True) and email:
        try:
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                html = "<p>" + summary.replace("\n", "<br>") + "</p>"
                mid = await email_resend.send_email(email, f"Cash pickup — {date} ({_usd(total)})", html)
                results.append({"channel": "email", "to": email, "ok": True, "id": mid or "sent"})
            else:
                results.append({"channel": "email", "ok": False, "detail": "Resend not configured on the server"})
        except Exception as e:
            results.append({"channel": "email", "ok": False, "detail": str(e)[:200]})
    wa = (cfg.get("recipient_whatsapp") or "").strip()
    if cfg.get("notify_whatsapp", True) and wa:
        try:
            from app.modules.notify.channels import whatsapp_meta
            if whatsapp_meta.is_configured():
                mid = await whatsapp_meta.send_document(wa, b"", "text/plain", "pickup.txt", summary)
                results.append({"channel": "whatsapp", "to": wa, "ok": True, "id": mid or "sent"})
            else:
                results.append({"channel": "whatsapp", "ok": False, "detail": "WhatsApp not configured on the server"})
        except Exception as e:
            results.append({"channel": "whatsapp", "ok": False, "detail": str(e)[:200]})
    if not email and not wa:
        results.append({"channel": "none", "ok": False, "detail": "No pickup recipient set — configure one on the Cash Pickup page."})
    return results


# ════════════════════════════════════════════════════════════════════════════════════════════════
# CASH-MANAGEMENT ALERT FOUNDATION (migration 089) — auto-DM + named extras, email/WhatsApp, deduped
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _alert_recipients(client, org_id, scope, store_code=None):
    """Resolve recipients for an alert scope: the configured NAMED EXTRAS (storeops.alert_recipient
    for this scope or 'all') + the store's auto-resolved District Manager when include_dm is set.
    Returns a list of {name, email, whatsapp, via_email, via_whatsapp}."""
    out = []
    try:
        rows = (client.schema("storeops").table("alert_recipient").select("*")
                .eq("org_id", org_id).in_("scope", [scope, "all"]).execute().data) or []
    except Exception:
        rows = []
    want_dm = False
    for r in rows:
        want_dm = want_dm or bool(r.get("include_dm"))
        if (r.get("email") or r.get("whatsapp")):
            out.append({"name": r.get("name"), "email": (r.get("email") or "").strip(),
                        "whatsapp": (r.get("whatsapp") or "").strip(),
                        "via_email": r.get("via_email", True), "via_whatsapp": bool(r.get("via_whatsapp"))})
    if (want_dm or not rows) and store_code:
        try:
            from app.modules.storeops.router import _dm_for_store
            deid, demail, dname = _dm_for_store(org_id, store_code)
            if demail or deid:
                out.append({"name": dname or "District Manager", "email": (demail or "").strip(),
                            "whatsapp": "", "via_email": True, "via_whatsapp": False, "is_dm": True})
        except Exception:
            pass
    return out


async def _send_alert(client, org_id, scope, subject, text, ref_key, store_code=None, force=False):
    """Send an alert to the scope's recipients via email + WhatsApp, DEDUPED by (scope, ref_key) via
    storeops.alert_log so a cron doesn't re-alert every tick. Best-effort; returns a summary dict."""
    if not force:
        try:
            seen = (client.schema("storeops").table("alert_log").select("id")
                    .eq("org_id", org_id).eq("scope", scope).eq("ref_key", ref_key).limit(1).execute().data) or []
            if seen:
                return {"skipped": "already alerted", "ref_key": ref_key}
        except Exception:
            pass
    recips = _alert_recipients(client, org_id, scope, store_code)
    if not recips:
        return {"sent": 0, "detail": "no recipients configured for scope " + scope}
    html = "<p>" + text.replace("\n", "<br>") + "</p>"
    sent, tos = 0, []
    for r in recips:
        em = (r.get("email") or "").strip()
        if r.get("via_email", True) and em:
            try:
                from app.modules.notify.channels import email_resend
                if email_resend.is_configured():
                    await email_resend.send_email(em, subject, html)
                    sent += 1; tos.append(em)
            except Exception:
                pass
        wa = (r.get("whatsapp") or "").strip()
        if r.get("via_whatsapp") and wa:
            try:
                from app.modules.notify.channels import whatsapp_meta
                if whatsapp_meta.is_configured():
                    await whatsapp_meta.send_document(wa, b"", "text/plain", "alert.txt", text)
                    sent += 1; tos.append(wa)
            except Exception:
                pass
    try:
        client.schema("storeops").table("alert_log").insert(
            {"org_id": org_id, "scope": scope, "ref_key": ref_key,
             "recipients": ", ".join(tos), "detail": {"subject": subject, "count": sent}}).execute()
    except Exception:
        pass
    return {"sent": sent, "recipients": tos, "ref_key": ref_key}


# ── Cash-management config: closing gate + assigned closers + alert recipients ──────────────────
@router.get("/cash-config")
def get_cash_config(org_id: str = ORG_ID):
    """Closing-gate + cash-aging settings (from storeops.tenants) + assigned closers + alert recipients."""
    c = sb()
    t = (c.schema("storeops").table("tenants").select("closing_deadline,closing_gate_enabled,cash_alert_after_days,closing_mode")
         .eq("org_id", org_id).limit(1).execute().data or [{}])
    tenant = t[0] if t else {}
    try:
        closers = (c.schema("storeops").table("store_closer").select("*").eq("org_id", org_id).execute().data) or []
    except Exception:
        closers = []
    try:
        recips = (c.schema("storeops").table("alert_recipient").select("*").eq("org_id", org_id).execute().data) or []
    except Exception:
        recips = []
    # closing_stale_alert_days (mig 505) — read SEPARATELY from the tenant select above so a database
    # that hasn't run mig 505 yet never breaks the rest of this (already-working) endpoint; missing
    # column/table → the same default (3) the attention provider falls back to.
    try:
        _sd = (c.schema("storeops").table("tenants").select("closing_stale_alert_days")
               .eq("org_id", org_id).limit(1).execute().data) or []
        stale_days = _sd[0].get("closing_stale_alert_days") if _sd else None
    except Exception:
        stale_days = None
    return {"closing_deadline": tenant.get("closing_deadline"),
            "closing_gate_enabled": bool(tenant.get("closing_gate_enabled")),
            "cash_alert_after_days": tenant.get("cash_alert_after_days"),
            "closing_mode": (tenant.get("closing_mode") or "per_rep"),
            "closing_stale_alert_days": stale_days if stale_days is not None else 3,
            "closers": closers, "recipients": recips}


@router.put("/cash-config")
def put_cash_config(body: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Update the closing-gate + cash-aging settings (defined at onboarding). Gated to the 'closing'
    settings area (2026-07-26 settings audit — this page is already nav-restricted to company-wide
    scope; the backend had no matching check)."""
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing cash-management settings is permission-restricted.")
    upd = {}
    for k in ("closing_deadline", "closing_gate_enabled", "cash_alert_after_days", "closing_mode"):
        if k in body:
            v = body[k]
            if k == "closing_gate_enabled":
                v = bool(v)
            elif k == "cash_alert_after_days":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = None
            elif k == "closing_mode":
                v = "one_closing" if str(v) == "one_closing" else "per_rep"
            upd[k] = v
    if upd:
        client.schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    if "closing_stale_alert_days" in body:
        try:
            _n = max(0, int(body["closing_stale_alert_days"]))
        except (TypeError, ValueError):
            _n = 3
        try:
            client.schema("storeops").table("tenants").update(
                {"closing_stale_alert_days": _n}).eq("org_id", org_id).execute()
        except Exception:
            pass   # migration 505 not run yet on this database — degrades silently, nothing else breaks
    return get_cash_config(org_id)


@router.put("/cash-config/closer")
def set_store_closer(body: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Assign (or clear) the closer for a store. Body: {store_code, employee_id?, employee_name?}.
    Gated to the 'closing' settings area (2026-07-26 settings audit — same rationale as cash-config)."""
    if not _can_edit_closing_setting(_caller_perms(sb(), authorization)):
        raise HTTPException(403, "Assigning store closers is permission-restricted.")
    store = (body.get("store_code") or "").strip()
    if not store:
        raise HTTPException(400, "store_code required")
    if not (body.get("employee_id") or body.get("employee_name")):
        sb().schema("storeops").table("store_closer").delete().eq("org_id", org_id).eq("store_code", store).execute()
        return {"ok": True, "cleared": True}
    row = {"org_id": org_id, "store_code": store, "employee_id": body.get("employee_id"),
           "employee_name": body.get("employee_name"), "updated_at": _now()}
    sb().schema("storeops").table("store_closer").upsert(row, on_conflict="org_id,store_code").execute()
    return {"ok": True, "store_code": store}


@router.put("/cash-config/recipient")
def upsert_alert_recipient(body: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Add/update an alert recipient. Body: {id?, scope, name?, email?, whatsapp?, via_email?, via_whatsapp?, include_dm?}.
    Gated to the 'closing' settings area (2026-07-26 settings audit — same rationale as cash-config)."""
    if not _can_edit_closing_setting(_caller_perms(sb(), authorization)):
        raise HTTPException(403, "Editing alert recipients is permission-restricted.")
    scope = (body.get("scope") or "all").strip()
    row = {"org_id": org_id, "scope": scope, "name": body.get("name"),
           "email": (body.get("email") or "").strip() or None, "whatsapp": (body.get("whatsapp") or "").strip() or None,
           "via_email": body.get("via_email", True), "via_whatsapp": bool(body.get("via_whatsapp")),
           "include_dm": body.get("include_dm", True)}
    c = sb()
    if body.get("id"):
        c.schema("storeops").table("alert_recipient").update(row).eq("id", body["id"]).eq("org_id", org_id).execute()
        return {"ok": True, "id": body["id"]}
    r = c.schema("storeops").table("alert_recipient").insert(row).execute()
    return {"ok": True, "id": (r.data or [{}])[0].get("id")}


@router.delete("/cash-config/recipient/{rid}")
def delete_alert_recipient(rid: str, org_id: str = ORG_ID, authorization: str = Header(default="")):
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing alert recipients is permission-restricted.")
    client.schema("storeops").table("alert_recipient").delete().eq("id", rid).eq("org_id", org_id).execute()
    return {"ok": True}


# ── Ops-accountability chargebacks (OWNER DIRECTIVE 2026-07-22, mig 504) ───────────────────────
# Two reasons: missed_closing (charged to the effective closer, decided at payroll — mod-people
# owns that decide UI, writing straight to commcalc.ops_chargeback per the shared contract) and
# missed_dm_verify (charged to the store's DM's commission, decided right here on the DM Verify
# page). Everything below degrades to an honest empty/no-op state if migration 504 hasn't run.
@router.get("/ops-chargebacks/policy")
def get_ops_chargeback_policy(org_id: str = ORG_ID):
    """Per-reason chargeback amount + enabled toggle. Always returns one row per known reason
    (registry defaults when unsaved), so the admin UI renders a full form even at zero DB rows."""
    require_org(org_id)
    return {"policy": ops_chargebacks.get_policy(sb(), org_id)}


@router.put("/ops-chargebacks/policy")
def put_ops_chargeback_policy(payload: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Save {policy:[{reason, amount, enabled}, ...]}. Gated the same way as this module's other
    money-config surfaces (an explicit settings.closing role grant/deny wins; else the management-
    review gate — company-wide/super-admin, DMs excluded)."""
    require_org(org_id)
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing ops-chargeback amounts is permission-restricted.")
    rows = ops_chargebacks.put_policy(client, org_id, payload.get("policy") or [])
    return {"policy": rows}


@router.get("/ops-chargebacks/dm-verify")
def get_missed_dm_verifies(lookback_days: int = 14, date_from: str = None, date_to: str = None,
                           market: str = None, markets: str = None, stores: str = None, reps: str = None,
                           authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Runs the missed-DM-verification sweep, then returns every missed_dm_verify chargeback
    (pending/posted/waived) scoped to the caller's store span, plus cumulative totals. Powers the
    'Missed verifications & chargebacks' panel at the top of the DM Verify page. `date_from`/`date_to`/
    `markets`/`stores`/`reps` (retail-ops-14, OWNER DIRECTIVE 2026-07-28) let the panel honor the SAME
    active filter bar as the rest of the page — bucket-aware market matching, same as /closing/summary,
    so an unresolved/blank-market store's missed-verify row is never silently dropped."""
    require_org(org_id)
    client = sb()
    rows = ops_chargebacks.detect_missed_dm_verifies(org_id, lookback_days=lookback_days)
    # PARENT rows only — a settlement-created overflow child (mod-commission's cascade-deduction
    # engine, parent_id set) is bookkeeping for how an already-decided chargeback was allocated
    # across commission cycles, not a second missed-verification incident; counting both would
    # double the totals on this per-incident panel.
    rows = [r for r in rows if not r.get("parent_id")]
    if date_from:
        rows = [r for r in rows if str(r.get("incident_date") or "") >= date_from]
    if date_to:
        rows = [r for r in rows if str(r.get("incident_date") or "") <= date_to]
    store_set = _resolve_store_filter(stores)
    rep_set = _resolve_rep_filter(reps)
    market_set = _resolve_market_filter(market, markets)
    if store_set is not None:
        # Gate-1 NIT-4a (2026-07-28): never drop a row with no store_code at all — same "an unresolved
        # row has no identity a picker could offer" rule /closing/summary and /closing/rollup apply.
        rows = [r for r in rows if not r.get("store_code") or (r.get("store_code") or "").upper() in store_set]
    if rep_set is not None:
        rows = [r for r in rows if (r.get("employee_name") or "").strip().casefold() in rep_set]
    if market_set is not None:
        try:
            _store_rows = (client.schema("storeops").table("stores").select("store_code,market")
                          .eq("org_id", org_id).execute().data) or []
            _mkt_by_code = {s.get("store_code"): _market_bucket(s.get("market")) for s in _store_rows if s.get("store_code")}
            rows = [r for r in rows if _mkt_by_code.get(r.get("store_code"), "(no market)").casefold() in market_set]
        except Exception:
            # Gate-1 NIT-4b (2026-07-28): a failed storeops.stores fetch used to leave _mkt_by_code={},
            # which buckets EVERY row into "(no market)" via the .get(...) default — a market filter for
            # any REAL market then silently emptied the whole panel even though the true markets were
            # simply unresolvable. Prefer NOT applying the market filter in this degraded path over a
            # silent, misleading drop.
            pass
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]
    return {"rows": rows, "totals": ops_chargebacks.totals(rows),
            "can_decide": _can_mgmt_review(_caller_perms(client, authorization))}


@router.post("/ops-chargebacks/decide")
def decide_missed_dm_verify(payload: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Post (deduct from the DM's commission) or waive one pending missed_dm_verify chargeback.
    Body: {id, decision: 'posted'|'waived', notes?}. Management-review gated — same as this
    module's other override actions (release, duplicates); DMs cannot decide their own chargeback."""
    require_org(org_id)
    client = sb()
    perms = _caller_perms(client, authorization)
    if not _can_mgmt_review(perms):
        raise HTTPException(403, "Deciding a chargeback is permission-restricted (not available to DMs).")
    cb_id = (payload.get("id") or "").strip()
    decision = (payload.get("decision") or "").strip()
    if not cb_id:
        raise HTTPException(400, "id required")
    try:
        row = ops_chargebacks.decide_chargeback(
            client, org_id, cb_id, decision, decided_by=_caller_email(client, authorization),
            notes=payload.get("notes"), reason_filter="missed_dm_verify")
    except LookupError:
        raise HTTPException(404, "chargeback not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "chargeback": row}


# ── Alert crons (pg_cron → run-due, NOTIFY_RUN_SECRET-guarded) ──────────────────────────────────
def _biz_today_iso():
    from datetime import datetime as _dt, timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        return _dt.now(_tz.utc).astimezone(ZoneInfo(settings.BUSINESS_TZ or "America/New_York")).date().isoformat()
    except Exception:
        return _dt.now(_tz.utc).date().isoformat()


async def _run_closing_missing_alerts(org_id=None):
    """For each tenant with the gate on + a deadline that has passed today, alert (DM + extras) about
    every store that has NO daily closing submitted for today. Deduped per store+date."""
    from datetime import datetime as _dt, timezone as _tz
    c = sb()
    tens = (c.schema("storeops").table("tenants")
            .select("org_id,closing_deadline,closing_gate_enabled").eq("closing_gate_enabled", True).execute().data) or []
    if org_id:
        tens = [t for t in tens if t.get("org_id") == org_id]
    today = _biz_today_iso()
    results = []
    for t in tens:
        oid = t.get("org_id")
        dl = (t.get("closing_deadline") or "").strip()
        if not dl:
            continue
        # only after the deadline (business-local)
        try:
            now_biz = _biz_now_hhmm()
            if now_biz < dl:
                continue
        except Exception:
            pass
        stores = (c.schema("storeops").table("stores").select("store_code,address").eq("org_id", oid).execute().data) or []
        closed = {(r.get("store_code") or "") for r in
                  ((c.schema("commcalc").table("daily_closing").select("store_code")
                    .eq("org_id", oid).eq("close_date", today).execute().data) or [])}
        for s in stores:
            sc = s.get("store_code")
            if not sc or sc in closed:
                continue
            res = await _send_alert(
                c, oid, "closing_missing",
                subject=f"Daily closing NOT submitted — {sc} ({today})",
                text=(f"The daily closing for {s.get('address') or sc} was not submitted by the "
                      f"{dl} deadline on {today}. The closing must be submitted before the store closes."),
                ref_key=f"{sc}|{today}", store_code=sc)
            results.append({"org_id": oid, "store": sc, **res})
    return {"checked": len(tens), "alerts": [r for r in results if r.get("sent")]}


async def _run_cash_unpicked_alerts(org_id=None):
    """Alert when store cash hasn't been picked up within cash_alert_after_days — one alert per store+date."""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    c = sb()
    tens = (c.schema("storeops").table("tenants").select("org_id,cash_alert_after_days").execute().data) or []
    if org_id:
        tens = [t for t in tens if t.get("org_id") == org_id]
    today = _dt.fromisoformat(_biz_today_iso())
    results = []
    for t in tens:
        oid = t.get("org_id")
        days = t.get("cash_alert_after_days")
        if days in (None, "", 0):
            continue
        try:
            days = int(days)
        except (TypeError, ValueError):
            continue
        cutoff = (today - _td(days=days)).isoformat()
        # closings older than the cutoff whose cash hasn't been marked picked up
        closings = (c.schema("commcalc").table("daily_closing").select("store_code,close_date,store_cash,epay_cash")
                    .eq("org_id", oid).lte("close_date", cutoff).gte("close_date", (today - _td(days=days + 14)).isoformat())
                    .execute().data) or []
        picks = {(p.get("store_code") or "", str(p.get("close_date"))) for p in
                 ((c.schema("commcalc").table("cash_pickup").select("store_code,close_date,picked_up")
                   .eq("org_id", oid).eq("picked_up", True).execute().data) or [])}
        by_store = {}
        for cl in closings:
            sc, cd = cl.get("store_code") or "", str(cl.get("close_date"))
            if not sc or (sc, cd) in picks:
                continue
            by_store.setdefault(sc, {"n": 0, "oldest": cd})
            by_store[sc]["n"] += 1
            by_store[sc]["oldest"] = min(by_store[sc]["oldest"], cd)
        for sc, info in by_store.items():
            res = await _send_alert(
                c, oid, "cash_unpicked",
                subject=f"Cash not picked up — {sc} ({info['n']} day(s), oldest {info['oldest']})",
                text=(f"Store {sc} has cash that hasn't been picked up for {days}+ days "
                      f"(oldest uncollected: {info['oldest']}). Please arrange collection."),
                ref_key=f"{sc}|{info['oldest']}", store_code=sc)
            results.append({"org_id": oid, "store": sc, **res})
    return {"checked": len(tens), "alerts": [r for r in results if r.get("sent")]}


def _biz_now_hhmm():
    from datetime import datetime as _dt, timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        return _dt.now(_tz.utc).astimezone(ZoneInfo(settings.BUSINESS_TZ or "America/New_York")).strftime("%H:%M")
    except Exception:
        return _dt.now(_tz.utc).strftime("%H:%M")


@router.post("/alerts/run-due")
async def cash_alerts_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint (NOTIFY_RUN_SECRET) — run both the missing-closing and cash-unpicked alert
    sweeps across all tenants. Schedule hourly. Deduped, so re-running is safe."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    missing = await _run_closing_missing_alerts()
    unpicked = await _run_cash_unpicked_alerts()
    return {"closing_missing": missing, "cash_unpicked": unpicked}


@router.post("/alerts/run")
async def cash_alerts_run_now(org_id: str = ORG_ID):
    """Manual trigger for one tenant (testing)."""
    missing = await _run_closing_missing_alerts(org_id)
    unpicked = await _run_cash_unpicked_alerts(org_id)
    return {"closing_missing": missing, "cash_unpicked": unpicked}


@router.get("/pickups")
def closing_pickups(date: str = "", start: str = "", end: str = "", market: str = None,
                    store: str = "", employee: str = "", dm: str = "",
                    stores: str = "", employees: str = "", org_id: str = ORG_ID):
    """Cash envelopes + their pickup/deposit status, FILTERABLE by date (or start..end range), store(s),
    sales rep/employee(s), and the DM who collected. An envelope = a rep's closing row with cash to
    collect (store_cash + epay_cash > 0) or an envelope photo. `stores`/`employees` (mig-502-era,
    retail-ops-7 item 2) are comma-separated MULTI-select lists, additive to the original singular
    `store`/`employee` (kept for backward compat — a lone `store`/`employee` still narrows exactly as
    before). `stores` is exact-match (store_code) like the original `store`; `employees` is EXACT match
    (the picker on the frontend supplies real roster names) while the legacy `employee` stays a
    substring match, unchanged."""
    if not (date or (start and end)):
        raise HTTPException(400, "date, or start+end, required (YYYY-MM-DD)")
    client = sb()
    q = client.schema("commcalc").table("daily_closing").select("*").eq("org_id", org_id)
    q = q.eq("close_date", date) if date else q.gte("close_date", start).lte("close_date", end)
    rows = q.execute().data or []
    # NOTE: local named store_rows (NOT `stores`) — `stores` is the multi-select query param below;
    # this endpoint's 500 (2026-07-16 hotfix) was exactly this roster fetch shadowing that param, so
    # `stores.split(",")` a few lines down blew up with AttributeError on every call. Keep them distinct.
    store_rows = (client.schema("storeops").table("stores").select("store_code,address,market,is_active").eq("org_id", org_id).execute().data) or []
    smeta = {s.get("store_code"): s for s in store_rows if s.get("store_code")}
    # storeops.stores.market is only partly populated; fall back to commcalc.store_mapping.market (much
    # fuller) so a market-scoped DM still sees every store in their market (was: stores with a blank
    # storeops market silently dropped out of the DM's market filter).
    sm_market = {}
    try:
        for s in (client.schema("commcalc").table("store_mapping").select("store_code,market")
                  .eq("org_id", org_id).execute().data or []):
            if s.get("store_code") and (s.get("market") or "").strip():
                sm_market[s.get("store_code")] = s["market"].strip()
    except Exception:
        pass
    pq = client.schema("commcalc").table("cash_pickup").select("*").eq("org_id", org_id)
    pq = pq.eq("close_date", date) if date else pq.gte("close_date", start).lte("close_date", end)
    try:
        picks = pq.execute().data or []
    except Exception:
        picks = []
    pick_by = {((p.get("store_code") or ""), (p.get("employee_name") or ""), str(p.get("close_date"))): p for p in picks}
    store_f, emp_f, dm_f = store.strip().upper(), employee.strip().lower(), dm.strip().lower()
    store_set = {s.strip().upper() for s in stores.split(",") if s.strip()}
    if store_f:
        store_set.add(store_f)
    emp_set = {e.strip().lower() for e in employees.split(",") if e.strip()}

    out = []
    for r in rows:
        cash = _f(r.get("store_cash")) + _f(r.get("epay_cash"))
        if cash <= 0 and not r.get("envelope_picture"):
            continue
        code = r.get("store_code") or ""
        meta = smeta.get(code, {})
        mk = (meta.get("market") or "").strip() or sm_market.get(code, "")
        if market and mk.strip().lower() != market.strip().lower():
            continue
        if store_set and (code or "").upper() not in store_set:
            continue
        _rname = (r.get("employee_name") or "").lower()
        if emp_f and emp_f not in _rname:
            continue
        if emp_set and _rname not in emp_set:
            continue
        p = pick_by.get((code, (r.get("employee_name") or ""), str(r.get("close_date"))))
        if dm_f and dm_f not in ((p or {}).get("picked_up_by") or "").lower():
            continue
        out.append({
            "close_date": str(r.get("close_date")),
            "store_code": r.get("store_code"),
            "store_name": meta.get("address") or r.get("store_address") or r.get("store_name"),
            "market": mk, "employee_name": r.get("employee_name"), "cash": round(cash, 2),
            "envelope_picture": r.get("envelope_picture"),
            # Sign the private-bucket paths at request time (raw path 404s as a relative href).
            "envelope_url": _signed_envelope(r.get("envelope_picture")),
            "picked_up": bool(p and p.get("picked_up")),
            "picked_up_by": p.get("picked_up_by") if p else None,
            "picked_up_at": p.get("picked_up_at") if p else None, "note": p.get("note") if p else None,
            # deposit tracking (mig 089)
            "disposition": p.get("disposition") if p else None,
            "deposit_amount": p.get("deposit_amount") if p else None,
            "deposit_matched": p.get("deposit_matched") if p else None,
            "deposit_flagged": bool(p.get("deposit_flagged")) if p else False,
            "deposit_url": _signed_envelope(p.get("deposit_slip_path")) if p and p.get("deposit_slip_path") else None,
            "pickup_id": p.get("id") if p else None,
        })
    out.sort(key=lambda e: (e["picked_up"], str(e.get("close_date") or ""), str(e.get("store_name") or "")))

    # Stores that did NOT submit a daily closing (single-date only — ambiguous over a range). Same
    # "closed = has any daily_closing row" definition the missing-closing alert uses. Respects the
    # market scope so a market-scoped DM only sees their own market's stragglers.
    not_closed = []
    if date:
        closed = {(r.get("store_code") or "") for r in rows if r.get("store_code")}
        for s in store_rows:
            code = s.get("store_code") or ""
            if not code or code in closed or s.get("is_active") is False:
                continue
            mk = (s.get("market") or "").strip() or sm_market.get(code, "")
            if market and mk.strip().lower() != market.strip().lower():
                continue
            not_closed.append({"store_code": code, "store_name": s.get("address") or code, "market": mk})
        not_closed.sort(key=lambda s: str(s.get("store_name") or ""))

    return {"date": date, "start": start, "end": end, "envelopes": out,
            "ready": sum(1 for e in out if not e["picked_up"]),
            "collected": sum(1 for e in out if e["picked_up"]),
            "flagged": sum(1 for e in out if e["deposit_flagged"]),
            # Cash-dollar totals (the count fields above are envelope counts).
            "total_cash": round(sum(e["cash"] for e in out), 2),
            "collected_cash": round(sum(e["cash"] for e in out if e["picked_up"]), 2),
            "ready_cash": round(sum(e["cash"] for e in out if not e["picked_up"]), 2),
            "not_closed": not_closed}


# ── Cash-position report (retail-ops-7 item 5): per-store cash on hand, as of a chosen day or over a
#    range, as a running ledger (declared cash accumulated MINUS cash actually picked up). Never
#    re-derives a dollar figure — reads the SAME t_cash/store_cash (daily_closing) and amount
#    (cash_pickup, picked_up=true) values every other closing surface already uses. ──
@router.get("/cash-position")
def cash_position(date: str = "", start: str = "", end: str = "",
                  stores: str = "", employees: str = "", org_id: str = ORG_ID):
    """Per store: cash on hand AS OF a chosen day (all declared cash to date minus all cash actually
    picked up to date — a store not swept in a few days shows its TRUE uncollected balance, not just
    today's own figure), last pickup at, last deposited at (cash_pickup.deposited_at ∪
    bank_deposit.created_at). `date` alone -> one row per store, the running balance as of that day.
    `start`+`end` -> one row per (store, day-in-range) that had activity, with a CUMULATIVE column
    carried from an opening balance computed from all history before `start` (so day 1 of the range
    never falsely resets to zero). `stores`/`employees` are comma-separated multi-select filters
    (store_code exact / employee_name exact — same convention as GET /closing/pickups)."""
    require_org(org_id)
    if not date and not (start and end):
        raise HTTPException(400, "date, or start+end, required (YYYY-MM-DD)")
    client = sb()
    store_list = [s.strip().upper() for s in stores.split(",") if s.strip()]
    emp_list = [e.strip().lower() for e in employees.split(",") if e.strip()]
    as_of = _date(date) if date else _date(end)
    range_start = _date(start) if start else None
    if not as_of:
        raise HTTPException(400, "valid date required (YYYY-MM-DD)")

    smeta_rows = (client.schema("storeops").table("stores").select("store_code,address,market")
                  .eq("org_id", org_id).execute().data) or []
    smeta = {s.get("store_code"): s for s in smeta_rows if s.get("store_code")}

    dq = (client.schema("commcalc").table("daily_closing")
          .select("store_code,employee_name,close_date,t_cash,store_cash")
          .eq("org_id", org_id).lte("close_date", as_of))
    if store_list:
        dq = dq.in_("store_code", store_list)
    drows = dq.limit(200000).execute().data or []
    if emp_list:
        drows = [r for r in drows if (r.get("employee_name") or "").strip().lower() in emp_list]

    pq = (client.schema("commcalc").table("cash_pickup")
          .select("store_code,employee_name,close_date,amount,picked_up,picked_up_at,deposited_at")
          .eq("org_id", org_id).lte("close_date", as_of))
    if store_list:
        pq = pq.in_("store_code", store_list)
    try:
        prows = pq.limit(200000).execute().data or []
    except Exception:
        prows = []
    if emp_list:
        prows = [r for r in prows if (r.get("employee_name") or "").strip().lower() in emp_list]

    bq = (client.schema("commcalc").table("bank_deposit").select("store_code,close_date,created_at")
          .eq("org_id", org_id).lte("close_date", as_of))
    if store_list:
        bq = bq.in_("store_code", store_list)
    try:
        brows = bq.limit(200000).execute().data or []
    except Exception:
        brows = []

    def _cash_amt(r):
        v = _f(r.get("t_cash"))
        return v if v else _f(r.get("store_cash"))

    decl_by_store_day, pick_by_store_day = {}, {}
    last_pickup_at, last_deposited_at = {}, {}
    for r in drows:
        code = r.get("store_code") or "?"
        dday = str(r.get("close_date") or "")
        decl_by_store_day.setdefault(code, {}).setdefault(dday, 0.0)
        decl_by_store_day[code][dday] += _cash_amt(r)
    for r in prows:
        code = r.get("store_code") or "?"
        dday = str(r.get("close_date") or "")
        if r.get("picked_up"):
            pick_by_store_day.setdefault(code, {}).setdefault(dday, 0.0)
            pick_by_store_day[code][dday] += _f(r.get("amount"))
            pu = r.get("picked_up_at")
            if pu and str(pu) > str(last_pickup_at.get(code) or ""):
                last_pickup_at[code] = pu
        dep = r.get("deposited_at")
        if dep and str(dep) > str(last_deposited_at.get(code) or ""):
            last_deposited_at[code] = dep
    for r in brows:
        code = r.get("store_code") or "?"
        c = r.get("created_at")
        if c and str(c) > str(last_deposited_at.get(code) or ""):
            last_deposited_at[code] = c

    codes = sorted({c for c in (set(decl_by_store_day) | set(pick_by_store_day) | set(store_list)) if c and c != "?"})

    def _label(code):
        m = smeta.get(code, {})
        return m.get("address") or code

    if not range_start:
        out = []
        for code in codes:
            declared_total = round(sum(decl_by_store_day.get(code, {}).values()), 2)
            picked_total = round(sum(pick_by_store_day.get(code, {}).values()), 2)
            out.append({
                "store_code": code, "store_name": _label(code), "market": smeta.get(code, {}).get("market"),
                "as_of": as_of, "declared_cumulative": declared_total, "picked_up_cumulative": picked_total,
                "cash_on_hand": round(declared_total - picked_total, 2),
                "cumulative_cash_on_hand": round(declared_total - picked_total, 2),
                "last_pickup_at": last_pickup_at.get(code), "last_deposited_at": last_deposited_at.get(code),
            })
        out.sort(key=lambda r: -r["cash_on_hand"])
        return {"mode": "single_day", "date": as_of, "rows": out}

    day_before_start = (dateparser.parse(range_start) - timedelta(days=1)).date().isoformat()
    out = []
    for code in codes:
        opening = round(
            sum(v for dd, v in decl_by_store_day.get(code, {}).items() if dd <= day_before_start) -
            sum(v for dd, v in pick_by_store_day.get(code, {}).items() if dd <= day_before_start), 2)
        store_days = sorted({dd for dd in decl_by_store_day.get(code, {}) if range_start <= dd <= as_of} |
                            {dd for dd in pick_by_store_day.get(code, {}) if range_start <= dd <= as_of})
        if not store_days:
            continue
        running = opening
        for dd in store_days:
            day_declared = round(decl_by_store_day.get(code, {}).get(dd, 0.0), 2)
            day_picked = round(pick_by_store_day.get(code, {}).get(dd, 0.0), 2)
            running = round(running + day_declared - day_picked, 2)
            out.append({
                "store_code": code, "store_name": _label(code), "market": smeta.get(code, {}).get("market"),
                "close_date": dd, "day_declared": day_declared, "day_picked_up": day_picked,
                "day_net": round(day_declared - day_picked, 2), "opening_balance": opening,
                "cumulative_cash_on_hand": running,
                "last_pickup_at": last_pickup_at.get(code), "last_deposited_at": last_deposited_at.get(code),
            })
    out.sort(key=lambda r: (r["store_name"], r["close_date"]))
    return {"mode": "range", "start": range_start, "end": as_of,
            "opening_note": "cumulative carries an opening balance computed from all history before the range start",
            "rows": out}


def _ocr_deposit_amount(raw: bytes, ext: str):
    """Read the deposited amount off a bank deposit-slip image with Claude vision. Returns
    (amount_float_or_None, raw_json). Graceful no-op ({} , None) when ANTHROPIC_API_KEY is unset."""
    if not settings.ANTHROPIC_API_KEY or not raw:
        return None, {"skipped": "ANTHROPIC_API_KEY not set — enter the deposit amount manually"}
    try:
        import json as _json
        from anthropic import Anthropic
        cli = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        media = "image/png" if ext == "png" else "image/jpeg"
        b64 = base64.b64encode(raw).decode("ascii")
        msg = cli.messages.create(
            model=settings.ACCOUNT_ENGINE_MODEL, max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                {"type": "text", "text": "This is a bank deposit slip. Return ONLY compact JSON: "
                 '{"total_deposit": <number>, "cash": <number|null>, "date": "<YYYY-MM-DD|null>"}. '
                 "total_deposit is the total amount deposited (no $ or commas). If unreadable, use null."}]}])
        text = "".join(getattr(b, "text", "") for b in msg.content) if msg.content else ""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        data = _json.loads(text[text.find("{"): text.rfind("}") + 1])
        amt = data.get("total_deposit")
        return (float(amt) if amt is not None else None), data
    except Exception as e:
        return None, {"error": str(e)[:200]}


@router.post("/pickup/deposit")
def record_deposit(payload: dict, org_id: str = ORG_ID):
    """Record what happened to picked-up cash: DEPOSITED (upload the slip → OCR the amount → match
    against the system's declared cash → flag any mismatch for review) or HANDED to management.
    Body: {store_code, close_date, employee_name, disposition:'deposited'|'handed_to_mgmt',
    deposit_slip?(data_url), deposit_amount?(manual override), declared_amount?, handed_to?, note?}."""
    client = sb()
    store = (payload.get("store_code") or "").strip()
    cdate = _date(payload.get("close_date") or payload.get("date"))
    emp = (payload.get("employee_name") or "").strip()
    disp = (payload.get("disposition") or "").strip().lower()
    if not (store and cdate) or disp not in ("deposited", "handed_to_mgmt"):
        raise HTTPException(400, "store_code, close_date and disposition (deposited|handed_to_mgmt) required")
    upd = {"org_id": org_id, "close_date": cdate, "store_code": store, "employee_name": emp,
           "disposition": disp, "deposit_note": payload.get("note"), "deposited_at": _now()}
    ocr = None
    if disp == "handed_to_mgmt":
        upd["handed_to"] = payload.get("handed_to")
    else:
        # declared = the system's cash for this envelope (epay cash + store cash)
        declared = payload.get("declared_amount")
        if declared is None:
            dc = (client.schema("commcalc").table("daily_closing").select("store_cash,epay_cash")
                  .eq("org_id", org_id).eq("close_date", cdate).eq("store_code", store)
                  .eq("employee_name", emp).limit(1).execute().data) or []
            declared = (_f(dc[0].get("store_cash")) + _f(dc[0].get("epay_cash"))) if dc else None
        slip = payload.get("deposit_slip")
        amount = payload.get("deposit_amount")
        if slip and "," in str(slip):
            path = _upload_envelope(org_id, slip)   # reuse the private closing-envelopes bucket
            upd["deposit_slip_path"] = path
            if amount in (None, ""):
                try:
                    header, b64 = str(slip).split(",", 1)
                    amount, ocr = _ocr_deposit_amount(base64.b64decode(b64), "png" if "png" in header else "jpg")
                except Exception:
                    amount = None
        upd["deposit_amount"] = (float(amount) if amount not in (None, "") else None)
        upd["declared_amount"] = (round(_f(declared), 2) if declared is not None else None)
        upd["deposit_ocr"] = ocr
        if upd["deposit_amount"] is not None and upd["declared_amount"] is not None:
            matched = abs(upd["deposit_amount"] - upd["declared_amount"]) <= 1.0
            upd["deposit_matched"] = matched
            upd["deposit_flagged"] = not matched
        else:
            upd["deposit_matched"] = None
            upd["deposit_flagged"] = False
    client.schema("commcalc").table("cash_pickup").upsert(
        upd, on_conflict="org_id,close_date,store_code,employee_name").execute()
    return {"ok": True, "disposition": disp, "deposit_amount": upd.get("deposit_amount"),
            "declared_amount": upd.get("declared_amount"), "matched": upd.get("deposit_matched"),
            "flagged": upd.get("deposit_flagged"), "ocr": ocr}


@router.post("/pickup")
async def confirm_pickup(payload: dict, org_id: str = ORG_ID):
    """Confirm the DM picked up the selected cash envelopes, then notify the assigned recipient.
    `date` is the single-day-page's date (kept for backward compat — every item defaults to it when it
    doesn't carry its own `close_date`). Since the pickup page now supports a DATE RANGE (retail-ops-7
    item 2), a batch can span multiple days — each item's OWN `close_date` (if sent) wins, so a
    multi-day selection is never mis-stamped with one shared date."""
    client = sb()
    top_date = _date(payload.get("date") or payload.get("close_date"))
    items = payload.get("items") or []
    if not items:
        raise HTTPException(400, "Select at least one envelope.")
    dm = (payload.get("picked_up_by") or "DM").strip()
    total = 0.0
    for it in items:
        item_date = _date(it.get("close_date")) or top_date
        if not item_date:
            raise HTTPException(400, "valid date required (on the request or each item)")
        amt = _f(it.get("amount") or it.get("cash"))
        total += amt
        row = {"org_id": org_id, "close_date": item_date, "store_code": it.get("store_code") or "",
               "store_name": it.get("store_name"), "employee_name": (it.get("employee_name") or ""),
               "amount": amt, "picked_up": True, "picked_up_by": dm, "picked_up_at": _now(),
               "note": (it.get("note") or "").strip() or None}
        client.schema("commcalc").table("cash_pickup").upsert(
            row, on_conflict="org_id,close_date,store_code,employee_name").execute()
    item_dates = sorted({_date(it.get("close_date")) or top_date for it in items} - {None})
    notify_label = top_date or (item_dates[0] if len(item_dates) == 1 else
                                f"{item_dates[0]}..{item_dates[-1]}" if item_dates else "—")
    notify = await _notify_pickup(client, org_id, dm, notify_label, items, round(total, 2))
    return {"ok": True, "count": len(items), "total": round(total, 2), "notify": notify}


@router.get("/pickup-config")
def get_pickup_config(org_id: str = ORG_ID):
    try:
        rows = (sb().schema("commcalc").table("cash_pickup_config").select("*").eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    c = rows[0] if rows else {}
    return {"recipient_name": c.get("recipient_name") or "", "recipient_email": c.get("recipient_email") or "",
            "recipient_whatsapp": c.get("recipient_whatsapp") or "",
            "notify_email": c.get("notify_email", True) if c else True,
            "notify_whatsapp": c.get("notify_whatsapp", True) if c else True,
            "email_configured": _email_configured(), "whatsapp_configured": _wa_configured()}


@router.put("/pickup-config")
def put_pickup_config(body: dict, org_id: str = ORG_ID):
    row = {"org_id": org_id, "updated_at": _now()}
    for k in ("recipient_name", "recipient_email", "recipient_whatsapp", "notify_email", "notify_whatsapp"):
        if k in body:
            row[k] = body[k]
    sb().schema("commcalc").table("cash_pickup_config").upsert(row, on_conflict="org_id").execute()
    return get_pickup_config(org_id)


# ── Google service-account auto-import of the closing responses sheet ───────────────────────
_CLOSING_CFG_DEFAULTS = {"frequency": "daily", "day_of_week": 1, "day_of_month": 1, "hour": 22,
                         "timezone": "America/New_York", "enabled": False, "tab": ""}


def _closing_cfg(client, org_id: str) -> dict:
    rows = (client.schema("commcalc").table("closing_sweep_config").select("*")
            .eq("org_id", org_id).execute().data) or []
    return rows[0] if rows else {}


def _closing_public_cfg(cfg: dict) -> dict:
    cfg = cfg or {}
    return {
        "sheet_id": cfg.get("sheet_id") or "", "tab": cfg.get("tab") or "",
        "enabled": bool(cfg.get("enabled")), "frequency": cfg.get("frequency") or "daily",
        "day_of_week": cfg.get("day_of_week"), "day_of_month": cfg.get("day_of_month"),
        "hour": cfg.get("hour"), "timezone": cfg.get("timezone") or "America/New_York",
        "next_run_at": cfg.get("next_run_at"), "last_run_at": cfg.get("last_run_at"),
        "last_status": cfg.get("last_status"), "last_detail": cfg.get("last_detail"),
        # the SA key lives in a server env var, never the DB — surface only whether it's set + its email
        "service_account_email": gsheet.sa_email(),
        "service_account_configured": gsheet.sa_info() is not None,
    }


def _next_run(frequency, dow, dom, hour, tzname) -> str:
    tz = ZoneInfo(tzname or "America/New_York")
    now = datetime.now(tz)
    hour = int(hour if hour is not None else 22)
    cand = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    freq = (frequency or "daily").lower()
    if freq == "weekly":
        dow = int(dow if dow is not None else 1)
        cand += timedelta(days=(dow - cand.weekday()) % 7)
        if cand <= now:
            cand += timedelta(days=7)
    elif freq == "monthly":
        import calendar
        dom = int(dom if dom is not None else 1)
        def at(yy, mm):
            d = min(dom, calendar.monthrange(yy, mm)[1])
            return now.replace(year=yy, month=mm, day=d, hour=hour, minute=0, second=0, microsecond=0)
        cand = at(now.year, now.month)
        if cand <= now:
            ny, nm = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
            cand = at(ny, nm)
    else:  # daily
        if cand <= now:
            cand += timedelta(days=1)
    return cand.astimezone(timezone.utc).isoformat()


def _closing_sweep_status(client, org_id, status, detail, mark_run=False):
    body = {"org_id": org_id, "last_status": status, "last_detail": (detail or "")[:500], "updated_at": _now()}
    if mark_run:
        body["last_run_at"] = _now()
    try:
        client.schema("commcalc").table("closing_sweep_config").upsert(body, on_conflict="org_id").execute()
    except Exception:
        pass


def _do_closing_sweep(org_id: str):
    client = sb()
    try:
        cfg = _closing_cfg(client, org_id)
        sheet_id = (cfg.get("sheet_id") or "").strip()
        if not sheet_id:
            _closing_sweep_status(client, org_id, "error", "No Google sheet id configured.", mark_run=True)
            return
        values, tab = gsheet.fetch_values(sheet_id, cfg.get("tab"))
        if not values or len(values) < 2:
            _closing_sweep_status(client, org_id, "error",
                                  "Sheet empty or unreadable — is it shared with the service account?", mark_run=True)
            return
        header = [str(h) for h in values[0]]
        body = [list(r) + [""] * (len(header) - len(r)) for r in values[1:]]
        df = pd.DataFrame(body, columns=header).astype(str)
        res = _ingest_dataframe(client, org_id, df)
        detail = f"OK — {res['rows_saved']} rows across {len(res['dates'])} day(s) from tab '{tab}'"
        if res["unresolved_stores"]:
            detail += f"; {res['unresolved_stores']} rows had an unrecognized SFID"
        _closing_sweep_status(client, org_id, "ok", detail, mark_run=True)
    except HTTPException as he:
        _closing_sweep_status(client, org_id, "error", str(he.detail), mark_run=True)
    except Exception as e:
        _closing_sweep_status(client, org_id, "error", f"Sweep failed: {e}", mark_run=True)


@router.get("/sweep/config")
def closing_sweep_get_config(org_id: str = ORG_ID):
    return _closing_public_cfg(_closing_cfg(sb(), org_id))


@router.put("/sweep/config")
def closing_sweep_put_config(body: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Gated to the 'closing' settings area (2026-07-26 settings audit — /closing/imports is already
    nav-restricted to company-wide scope; the backend had no matching check)."""
    client = sb()
    if not _can_edit_closing_setting(_caller_perms(client, authorization)):
        raise HTTPException(403, "Editing the auto-import schedule is permission-restricted.")
    cur = _closing_cfg(client, org_id) or {}
    row = {"org_id": org_id}
    for k in ("sheet_id", "tab", "enabled", "frequency", "day_of_week", "day_of_month", "hour", "timezone"):
        if k in body and body[k] is not None:
            row[k] = body[k]
    merged = {**_CLOSING_CFG_DEFAULTS, **cur, **row}
    row["next_run_at"] = _next_run(merged.get("frequency"), merged.get("day_of_week"),
                                   merged.get("day_of_month"), merged.get("hour"), merged.get("timezone"))
    row["updated_at"] = _now()
    client.schema("commcalc").table("closing_sweep_config").upsert(row, on_conflict="org_id").execute()
    return _closing_public_cfg(_closing_cfg(client, org_id))


@router.post("/sweep/run-now")
def closing_sweep_run_now(background_tasks: BackgroundTasks, org_id: str = ORG_ID):
    cfg = _closing_cfg(sb(), org_id)
    if not (cfg.get("sheet_id") or "").strip():
        raise HTTPException(400, "Set the Google sheet id first.")
    if gsheet.sa_info() is None:
        raise HTTPException(400, "GOOGLE_SERVICE_ACCOUNT_JSON is not set on the server.")
    background_tasks.add_task(_do_closing_sweep, org_id)
    return {"status": "started"}


@router.post("/sweep/run-due")
def closing_sweep_run_due(background_tasks: BackgroundTasks, x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint — run every enabled config whose next_run_at has passed."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _now()
    due = (client.schema("commcalc").table("closing_sweep_config").select("*")
           .eq("enabled", True).lte("next_run_at", now_iso).execute().data) or []
    for cfg in due:
        oid = cfg.get("org_id") or ORG_ID
        nxt = _next_run(cfg.get("frequency"), cfg.get("day_of_week"), cfg.get("day_of_month"),
                        cfg.get("hour"), cfg.get("timezone"))
        client.schema("commcalc").table("closing_sweep_config").update({"next_run_at": nxt}).eq("org_id", oid).execute()
        background_tasks.add_task(_do_closing_sweep, oid)
    return {"triggered": len(due)}


# ── Closing readiness (self-diagnostic — 2026-07-16 luxelink audit) ───────────────────────
def _rc_count(client, schema, table, org_id):
    """Row count for (schema.table, org_id). None = table/migration unreachable (unknown, NOT zero —
    a readiness check must never cry wolf over an unrun migration in some environment)."""
    try:
        r = (client.schema(schema).table(table).select("org_id", count="exact")
             .eq("org_id", org_id).limit(1).execute())
        return r.count or 0
    except Exception:
        return None


@router.get("/readiness")
def closing_readiness(org_id: str = ORG_ID):
    """Self-diagnostic: 'is Daily Closing actually wired for this tenant', surfaced explicitly instead
    of the module quietly degrading to empty/pending everywhere (the pattern behind the 2026-07-16
    "not wired properly on the luxelink side" report — every one of those gaps was a real config/data
    hole this module already degrades around SAFELY, but silently). Universal: the exact same checks run
    for every tenant, house included — Boost simply passes all of them today, so this endpoint changes
    NOTHING about how closing/recon behave, it only makes the existing degrade states visible. Read-only,
    no gate/money-math touched."""
    require_org(org_id)
    client = sb()
    issues = []

    module_on = True
    try:
        from app.modules.core.entitlements import module_enabled
        module_on = module_enabled(org_id, "closing", client)
    except Exception:
        pass
    if not module_on:
        issues.append({"code": "module_disabled", "severity": "critical",
                       "message": "Daily Closing is not enabled for this tenant's billing plan — enable "
                                  "it under Admin \u2192 Billing / Modules."})

    # NOTE: every "is this a real gap" check below fires ONLY on a CONFIRMED count of 0 (never on
    # None = the table/migration was unreachable) \u2014 an unrun migration in some environment must
    # never be misreported as "no stores" / "no sales" for a tenant that's actually fine.
    sm_n = _rc_count(client, "commcalc", "store_mapping", org_id)
    so_n = _rc_count(client, "storeops", "stores", org_id)
    if sm_n == 0 and so_n == 0:
        issues.append({"code": "no_stores", "severity": "critical",
                       "message": "No stores found in StoreOps or commcalc.store_mapping \u2014 create "
                                  "stores under StoreOps \u2192 Admin \u2192 Stores first; nothing else "
                                  "in Daily Closing can resolve a store until this exists."})
    elif sm_n == 0 and (so_n or 0) > 0:
        issues.append({"code": "no_store_mapping", "severity": "warning",
                       "message": f"{so_n} store(s) in StoreOps but none yet mirrored into "
                                  "commcalc.store_mapping (the table B2B/X-report recon resolves stores "
                                  "against) \u2014 this self-heals the next time each store is saved in "
                                  "StoreOps Admin; re-save a store if this persists."})

    raw_n = _rc_count(client, "commcalc", "raw_sales", org_id)
    feed_n = _rc_count(client, "commcalc", "daily_sales_feed", org_id)
    if raw_n == 0 and feed_n == 0:
        issues.append({"code": "no_sales_source", "severity": "critical",
                       "message": "No B2B sales data has ever landed in raw_sales or daily_sales_feed \u2014 "
                                  "money/count recon and the \u2018who worked\u2019 check will stay "
                                  "recon-pending for every day. Check the daily email-import mapping (a "
                                  "*Sales* \u2192 sales rule on this tenant's mailbox under Email Imports) "
                                  "or upload a Sales Transaction Details file."})

    xr_n = _rc_count(client, "commcalc", "pos_tender_summary", org_id)
    if xr_n == 0:
        issues.append({"code": "no_xreport_ever", "severity": "warning",
                       "message": "No POS X-report has ever been imported \u2014 cash & credit recon will "
                                  "stay \u2018pending\u2019 (never falsely flagged/blocked, but never "
                                  "verified either). Check (1) the mailbox has an *X-Report* \u2192 "
                                  "x_report rule under Email Imports and (2) b2bsoft is actually scheduled "
                                  "to email an X-Report for this tenant (a separate subscription from the "
                                  "mailbox rule)."})

    dc_n = _rc_count(client, "commcalc", "daily_closing", org_id)
    if dc_n == 0:
        issues.append({"code": "no_closings_yet", "severity": "info",
                       "message": "No daily_closing rows yet \u2014 reps haven't submitted via "
                                  "/closing/submit, and/or the Google-sheet auto-import (Closing \u2192 "
                                  "Auto-Import) isn't configured for this tenant yet."})

    from . import tender_config, count_config
    try:
        _tdefs, _tmaps = tender_config.load_tender_config(client, org_id)
    except Exception:
        _tdefs = []
    try:
        _cdefs = count_config.load_count_config(client, org_id)
    except Exception:
        _cdefs = []
    # INFO only (2026-07-16 platform-core cross-module sweep flagged mig 089/111 as "house-org-only
    # seeded" -> re-verified: BOTH are pure additive DDL with NO seed rows, house or otherwise; the
    # documented doctrine is an empty config falls back to the hardcoded standard set BYTE-IDENTICAL
    # (proven, retail-ops-8's 13/13 harness) and /closing/tender-config + /closing/count-config already
    # render that standard set as the pre-filled editable starting point even with zero DB rows -> not
    # a functional gap. Still surfaced here (info, not critical) purely for DISCOVERABILITY, since a
    # tenant that never visits either wizard is silently on the generic keyword-matching fallback,
    # which may not fit every POS vendor's raw labels as well as an explicit mapping would.
    if not _tdefs:
        issues.append({"code": "tender_config_default", "severity": "info",
                       "message": "Using the built-in 7 tenders + generic keyword matching (no tenant-"
                                  "specific tender mapping saved yet) — fine to leave as-is, or "
                                  "visit Closing → Tender Setup to map this tenant's own POS "
                                  "labels for a tighter match."})
    if not _cdefs:
        issues.append({"code": "count_config_default", "severity": "info",
                       "message": "Using the built-in 3 activation-count fields (Upgrades/New Lines/"
                                  "Postpaid) — fine to leave as-is, or visit Closing → Count "
                                  "Fields to define this tenant's own activation taxonomy."})

    return {
        "org_id": org_id, "module_enabled": module_on,
        "counts": {"store_mapping": sm_n, "storeops_stores": so_n, "raw_sales": raw_n,
                  "daily_sales_feed": feed_n, "pos_tender_summary": xr_n, "daily_closing": dc_n},
        "tender_config": "custom" if _tdefs else "standard (built-in 7 tenders)",
        "count_config": "custom" if _cdefs else "standard (built-in 3 fields)",
        "issues": issues,
        "ok": not any(i["severity"] == "critical" for i in issues),
    }


@router.get("/health")
def health():
    return {"status": "ok", "module": "closing"}


# ── small utils ───────────────────────────────────────────────────────────────────────────
def _f(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _name_match(a: str, b: str) -> bool:
    """Loose match between a scheduled name and a submitted name (first-token / contains)."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return a.split()[0] == b.split()[0]


# ── B2B money actuals for the closing money-recon (accessory gross, cash vs card) ────────────
import re as _re

_CASH_HINTS = ("cash",)
_CARD_HINTS = ("credit", "card", "debit", "visa", "master", "amex", "discover")


def _tender_class(t: str) -> str:
    """Bucket a B2B Tender Type string into cash / card / other (best-effort; the raw breakdown
    is also returned so the mapping can be validated against a real day)."""
    s = (t or "").strip().lower()
    if not s:
        return "other"
    if any(h in s for h in _CASH_HINTS):
        return "cash"
    if any(h in s for h in _CARD_HINTS):
        return "card"
    return "other"


# ── Canonical 7 tender types (the axis of the 3-way recon: closing vs X-report vs sales-transactions) ──
CANON_TENDERS = ["cash", "credit", "ext_cc", "gift", "store_acct", "zelle", "acima"]
CANON_TENDER_LABEL = {
    "cash": "Cash", "credit": "Credit", "ext_cc": "External Credit Card",
    "gift": "Gift Card", "store_acct": "Store Account", "zelle": "Zelle / CashApp",
    "acima": "ACIMA (lease)",
}


def _canon_tender(raw: str):
    """Map any source's raw tender string to one of the 6 canonical tenders (or None = unmapped).
    ORDER MATTERS: 'gift card' contains 'card', 'cash app' contains 'cash', 'external credit card'
    contains 'credit' — so the specific buckets are tested before the generic cash/credit ones."""
    t = (raw or "").strip().lower()
    if not t:
        return None
    if "acima" in t:
        return "acima"
    if "gift" in t:
        return "gift"
    if "zelle" in t or "cashapp" in t or "cash app" in t or "venmo" in t:
        return "zelle"
    if ("store" in t and ("acct" in t or "account" in t)) or t in ("account", "on account", "store credit"):
        return "store_acct"
    if "ext" in t or "external" in t:
        return "ext_cc"
    if "cash" in t:
        return "cash"
    if any(h in t for h in ("credit", "debit", "card", "visa", "master", "amex", "discover")):
        return "credit"
    return None


# Standard tender_key → physical daily_closing column. A standard tender reads its t_* column; a custom
# tender (mig 111 config) reads the `tenders` JSONB instead — so the two never double-count.
_TCOL = {"cash": "t_cash", "credit": "t_credit", "ext_cc": "t_ext_cc", "gift": "t_gift",
         "store_acct": "t_store_acct", "zelle": "t_zelle", "acima": "t_acima"}


def _closing_amt(row: dict, key: str) -> float:
    """Amount for one tender on a closing row — standard tender → its t_* column, custom → tenders JSONB."""
    col = _TCOL.get(key)
    if col:
        return _f(row.get(col))
    j = row.get("tenders")
    return _f(j.get(key)) if isinstance(j, dict) else 0.0


def _variance_dirs(issues: list) -> dict:
    """From _money_issues, the DIRECTION of each variance (no amount): cash short/over, credit over/under."""
    d = {"cash": "ok", "credit": "ok"}
    for i in issues:
        if i.get("metric") == "cash":
            d["cash"] = "short" if _f(i.get("variance")) < 0 else "over"
        elif i.get("metric") == "credit":
            d["credit"] = "over" if _f(i.get("variance")) > 0 else "under"
    return d


# Rep-facing wording — deliberately reveals NOTHING (not the amount, the over/short direction, or the
# attempt count). The rep is only told the report doesn't match; the detail lives in management review.
_REP_MISMATCH_RETRY = ("Your report does not match the system. Please recount and re-enter — if it still "
                       "doesn't match it will be reviewed by management.")
_REP_MISMATCH_REVIEW = "Your report does not match the system — sent for management review."


def _log_attempt(client, org_id, d, body, tenders, declared_cash, declared_credit, b2b, dirs,
                 attempt_no, blocked, accepted, auto_accepted):
    """Record ONE submission try. Management review reads these (with amounts + the true B2B variance);
    the rep never sees the amounts. Best-effort — a logging failure must not break the close."""
    try:
        client.schema("commcalc").table("closing_attempt").insert({
            "org_id": org_id, "close_date": d, "period": d[:7],
            "store_code": body.get("store_code"), "store_address": body.get("store_address"),
            "sfid": body.get("sfid"), "employee_name": body.get("employee_name"),
            "attempt_no": attempt_no,
            "entered_cash": round(_f(declared_cash), 2), "entered_credit": round(_f(declared_credit), 2),
            "t_cash": tenders["cash"], "t_credit": tenders["credit"], "t_ext_cc": tenders["ext_cc"],
            "t_gift": tenders["gift"], "t_store_acct": tenders["store_acct"], "t_zelle": tenders["zelle"],
            "t_acima": tenders["acima"],
            "b2b_cash": (b2b or {}).get("cash"), "b2b_credit": (b2b or {}).get("card"),
            "cash_dir": dirs.get("cash"), "credit_dir": dirs.get("credit"),
            "blocked": bool(blocked), "accepted": bool(accepted), "auto_accepted": bool(auto_accepted),
        }).execute()
    except Exception as e:
        print("closing attempt log failed:", e)


def _caller_perms(client, authorization: str) -> dict:
    """Resolve the logged-in user's role permissions (same source as /core/me) for a backend gate.
    `__resolved` (2026-07-26 settings audit) marks "we found a real, logged-in caller" — see
    `_can_mgmt_review` for why this matters. Every early-return (no/invalid token, no matching
    app_users row, any exception) returns `{}` — no `__resolved` key, i.e. falsy — never a dict that
    could accidentally satisfy a later `.get(...)` check."""
    try:
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if not uid:
            return {}
        rows = (client.schema("storeops").table("app_users").select("org_id,role,super_admin")
                .eq("auth_id", uid).limit(1).execute().data) or []
        if not rows:
            return {}
        u = rows[0]
        perms = {}
        if u.get("role"):
            rr = (client.schema("storeops").table("roles").select("permissions")
                  .eq("org_id", u.get("org_id") or ORG_ID).eq("name", u["role"]).limit(1).execute().data) or []
            if rr:
                perms = dict(rr[0].get("permissions") or {})
        perms["__super_admin"] = bool(u.get("super_admin"))
        perms["__resolved"] = True
        return perms
    except Exception:
        return {}


def _can_mgmt_review(perms: dict) -> bool:
    """Management-review gate: super-admin, an explicit page grant, or company-wide ('all') scope.
    DMs (market/store scope) are excluded unless an admin grants /closing/management to their role.

    FIX (2026-07-26 settings audit): the final fallback `(perms.get("scope") or "all") == "all"` used
    to run even when `perms == {}` — which is exactly what every caller gets for a MISSING or INVALID
    Authorization header (no exception, just an honest "couldn't resolve anyone") — so an entirely
    unauthenticated request was being treated as company-wide/all-scope and PASSED this gate. That
    silently made every endpoint gated by this function (or by `_can_edit_closing_setting`, which
    falls back to this) effectively ungated for a caller with no valid token at all. Now requires
    `__resolved` (a REAL caller was found) before that fallback applies; a super-admin or an explicit
    per-page override still short-circuit first, exactly as before, since those can only be set on a
    resolved caller's perms dict anyway."""
    if perms.get("__super_admin"):
        return True
    ov = (perms.get("pages") or {}).get("/closing/management")
    if isinstance(ov, bool):
        return ov
    if not perms.get("__resolved"):
        return False
    return (perms.get("scope") or "all") == "all"


def _caller_email(client, authorization: str) -> str | None:
    """The logged-in caller's email (storeops.app_users), for audit fields like decided_by. None
    when the token doesn't resolve (never raises — audit fields are best-effort)."""
    try:
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        rows = (client.schema("storeops").table("app_users").select("email")
                .eq("auth_id", uid).limit(1).execute().data) or []
        return rows[0].get("email") if rows else None
    except Exception:
        return None


def _can_edit_closing_setting(perms: dict) -> bool:
    """Per-setting rights for closing money-config (ops-chargeback amounts): an explicit
    settings.closing grant/deny on the caller's role wins (same key core's SETTING_AREAS registry
    uses for 'Daily Closing / Tender Fields' — read locally off the already-fetched `perms` dict so
    this doesn't need core's x-active-org multi-tenant membership resolution), else fall back to
    the existing management-review gate."""
    if perms.get("__super_admin"):
        return True
    s = perms.get("settings") or {}
    if "closing" in s:
        return bool(s["closing"])
    return _can_mgmt_review(perms)


def _num_key(s: str) -> str:
    """Leading store-number, digits only ('116-36 Springfield Blvd' → '11636'). The project's
    standard cross-source store join (calculator.py / coa.store_resolver)."""
    m = _re.match(r"\s*([0-9][0-9-]*)", str(s or ""))
    return _re.sub(r"\D", "", m.group(1)) if m else ""


def _b2b_sales_rows(client, org_id: str, date: str, cols: str) -> list:
    """The day's B2B sales rows for recon — the SAME source the Sales Report / Daily Targets use, so every
    B2B consumer agrees. For a day in the CURRENT (open) month it reads the daily email feed first (the
    freshest + complete source — the monthly raw_sales lags / promotes late even with 'auto' on); for a
    closed month it reads the authoritative raw_sales first; each falls back to the other. This is why the
    closing / X-tender recon was showing "b2b sales not loaded" for July — raw_sales was empty/partial and
    it didn't fall to the feed which HAD the data.

    UNION at (day, STORE) grain, not just day grain (2026-07-15 luxelink fix): if the primary source has
    rows for this exact day but is missing a particular STORE the other source DOES have (e.g. a manually-
    uploaded raw_sales month covers stores the automated daily feed's connector doesn't push for that
    day), the other source's rows for that missing store are pulled in too — a "primary has ANY row today"
    check used to make the entire OTHER source invisible for the day, even for stores primary never had
    anything for. A read failing outright on ONE table (schema hiccup, connectivity) now degrades to the
    other table instead of raising — previously an uncaught primary-query exception (only the fallback
    branch was wrapped) silently blanked the recon for EVERY store that day, not just the affected one.
    Byte-identical to before whenever the primary source already has every relevant store's data for the
    day (the common/house case: fill=[] and this returns exactly `prows`, unchanged)."""
    def _q(table):
        try:
            return (client.schema("commcalc").table(table).select(cols)
                    .eq("org_id", org_id).in_("period", [_period_label(date), date[:7]])
                    .eq("trans_date", date).limit(100000).execute().data) or []
        except Exception as e:
            print(f"WARN _b2b_sales_rows read of {table} for {date} failed: {e}")
            return []
    open_month = str(date)[:7] == _biz_today_iso()[:7]
    primary, other = ("daily_sales_feed", "raw_sales") if open_month else ("raw_sales", "daily_sales_feed")
    prows = _q(primary)
    if not prows:
        return _q(other)
    if "store" not in [c.strip() for c in cols.split(",")]:
        return prows   # can't key a safe per-store union without the store column — primary-only (unchanged)
    orows = _q(other)
    if not orows:
        return prows
    pstores = {(r.get("store") or "").strip().lower() for r in prows}
    fill = [r for r in orows if (r.get("store") or "").strip().lower() not in pstores]
    return prows + fill


def _acc_cfg(client, org_id):
    """The org's configurable accessory departments/categories/product-keywords (mig 092/093, shared with
    the commcalc Sales Report). Empty → default department 'Ondigo'. Read directly (no cross-module import)."""
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


def _is_acc(dept, cat, acc, product=""):
    d = (dept or "").strip().lower()
    c = (cat or "").strip().lower()
    if d in acc["d"]:
        return True
    if c and c in acc["c"]:
        return True
    if acc["p"]:
        p = (product or "").strip().lower()
        if p and any(k in p for k in acc["p"]):
            return True
    return False


def _b2b_counts_by_store(client, org_id: str, date: str) -> dict:
    """Per store_code: activations / upgrades (distinct trans_id, the SHARED contract-type classifier)
    + accessory GP — from the SAME unified B2B source as the money recon (_b2b_sales_rows, feed-first
    for the open month). Replaces the rigid daily_sales_actuals RPC so the recon counts populate for
    July too, and stay consistent with the Sales Report / Action Plan."""
    from app.modules.commcalc.calculator import classify_contract_type
    resolve = _addr_resolver(client, org_id)
    acc = _acc_cfg(client, org_id)
    rows = _b2b_sales_rows(client, org_id, date,
                           "store,department,category,product_desc,contract_type,trans_id,gp,voided,trans_type")
    out, seen = {}, {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        code = resolve(r.get("store"))
        if not code:
            continue
        o = out.setdefault(code, {"activations": 0, "upgrades": 0, "acc_gp": 0.0})
        s = seen.setdefault(code, {"act": set(), "upg": set()})
        tid = str(r.get("trans_id") or "").strip()
        cls = classify_contract_type(r.get("contract_type"))
        if tid and cls in ("premium", "byod"):
            s["act"].add(tid)
        elif tid and cls == "upgrade":
            s["upg"].add(tid)
        if _is_acc(r.get("department"), r.get("category"), acc, r.get("product_desc")):
            o["acc_gp"] += _f(r.get("gp"))
    for code, o in out.items():
        o["activations"] = len(seen[code]["act"])
        o["upgrades"] = len(seen[code]["upg"])
    return out


def _addr_resolver(client, org_id):
    """A store-name/address → store_code resolver (exact lowercased address, then unambiguous leading
    street-number), shared by the B2B and X-report tender aggregations."""
    addr_to_code, num_to_code, num_counts = {}, {}, {}
    sm = (client.schema("commcalc").table("store_mapping")
          .select("store_code,store_address").eq("org_id", org_id).execute().data) or []
    for r in sm:
        code = (r.get("store_code") or "").strip()
        addr = (r.get("store_address") or "").strip()
        if not (code and addr):
            continue
        addr_to_code[addr.lower()] = code
        nk = _num_key(addr)
        if nk:
            num_counts[nk] = num_counts.get(nk, 0) + 1
            num_to_code.setdefault(nk, code)

    def resolve(store_str):
        s = (store_str or "").strip()
        if not s:
            return None
        c = addr_to_code.get(s.lower())
        if c:
            return c
        nk = _num_key(s)
        if nk and num_counts.get(nk, 0) == 1:
            return num_to_code.get(nk)
        return None
    return resolve


def _xreport_tenders_by_store(client, org_id: str, date: str) -> dict:
    """Cash/card/other per store_code from the POS X-REPORT (commcalc.pos_tender_summary) for `date`
    — the AUTHORITATIVE tender split (the daily sales feed omits Tender Type). Returns {} when no
    X-report has been imported for that day (then the recon falls back to the feed / shows pending)."""
    try:
        rows = (client.schema("commcalc").table("pos_tender_summary")
                .select("store,tender_class,amount").eq("org_id", org_id)
                .eq("close_date", date).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        return {}
    resolve = _addr_resolver(client, org_id)
    out = {}
    for r in rows:
        code = resolve(r.get("store"))
        if not code:
            continue
        cls = (r.get("tender_class") or "other").lower()
        if cls not in ("cash", "card", "other"):
            cls = "other"
        agg = out.setdefault(code, {"cash": 0.0, "card": 0.0, "other": 0.0, "total": 0.0})
        amt = _f(r.get("amount"))
        agg[cls] += amt
        agg["total"] += amt
    for a in out.values():
        for k in list(a):
            a[k] = round(a[k], 2)
    return out


def _b2b_money_by_store(client, org_id: str, date: str) -> dict:
    """Aggregate that day's B2B sales per store_code: accessory GROSS (ext_price, dept Ondigo),
    cash vs card totals (by tender_type), plus the raw tender breakdown for transparency. Source is
    raw_sales, falling back to the daily feed (see _b2b_sales_rows). store is matched to store_code
    by exact address then by an unambiguous leading street-number."""
    addr_to_code, num_to_code, num_counts = {}, {}, {}
    sm = (client.schema("commcalc").table("store_mapping")
          .select("store_code,store_address").eq("org_id", org_id).execute().data) or []
    for r in sm:
        code = (r.get("store_code") or "").strip()
        addr = (r.get("store_address") or "").strip()
        if not (code and addr):
            continue
        addr_to_code[addr.lower()] = code
        nk = _num_key(addr)
        if nk:
            num_counts[nk] = num_counts.get(nk, 0) + 1
            num_to_code.setdefault(nk, code)

    def resolve(store_str):
        s = (store_str or "").strip()
        if not s:
            return None
        c = addr_to_code.get(s.lower())
        if c:
            return c
        nk = _num_key(s)
        if nk and num_counts.get(nk, 0) == 1:    # ambiguous numbers stay unmatched, never mis-merge
            return num_to_code.get(nk)
        return None

    acc = _acc_cfg(client, org_id)
    rows = _b2b_sales_rows(client, org_id, date, "store,department,category,product_desc,tender_type,ext_price,tax,voided")

    out = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        code = resolve(r.get("store"))
        if not code:
            continue
        ext = _f(r.get("ext_price"))
        agg = out.setdefault(code, {"acc_gross": 0.0, "cash": 0.0, "card": 0.0,
                                    "other": 0.0, "total": 0.0, "tax": 0.0, "tenders": {}, "_dept_seen": False})
        agg["total"] += ext
        agg["tax"] += _f(r.get("tax"))
        agg[_tender_class(r.get("tender_type"))] += ext
        dept = (r.get("department") or "").strip()
        if dept:
            agg["_dept_seen"] = True
        if _is_acc(dept, r.get("category"), acc, r.get("product_desc")):
            agg["acc_gross"] += ext
        tname = (r.get("tender_type") or "—").strip() or "—"
        agg["tenders"][tname] = round(agg["tenders"].get(tname, 0.0) + ext, 2)

    for a in out.values():
        # tenders_available: the source actually split cash vs card. The Legacy daily feed omits the
        # Tender Type column, so everything lands in 'other' (cash=card=0) — then declared money must
        # NOT be compared against a fabricated $0 (that flags every rep). total<=0 = nothing to recon.
        a["tenders_available"] = bool(a["total"] <= 0 or (a["cash"] + a["card"]) > 0)
        a["dept_available"] = bool(a.pop("_dept_seen", False))  # Department present → accessory recon valid
        for k in ("acc_gross", "cash", "card", "other", "total", "tax"):
            a[k] = round(a[k], 2)
    return out


# ── Reconciliation: rep-declared closing vs B2B actuals (cash/credit), + the close gate ──────
def _usd(n) -> str:
    return f"${_f(n):,.2f}"


def _b2b_day(client, org_id: str, date: str) -> dict:
    """One day's B2B raw_sales actuals: money per store_code and per (store_code, salesperson),
    plus per-store activation/upgrade counts. has_data=False ⇒ B2B not loaded for that day yet
    (the close gate then treats the rep as recon-pending rather than blocking)."""
    addr_to_code, num_to_code, num_counts = {}, {}, {}
    sm = (client.schema("commcalc").table("store_mapping")
          .select("store_code,store_address").eq("org_id", org_id).execute().data) or []
    for r in sm:
        code = (r.get("store_code") or "").strip()
        addr = (r.get("store_address") or "").strip()
        if not (code and addr):
            continue
        addr_to_code[addr.lower()] = code
        nk = _num_key(addr)
        if nk:
            num_counts[nk] = num_counts.get(nk, 0) + 1
            num_to_code.setdefault(nk, code)

    def resolve(store_str):
        s = (store_str or "").strip()
        if not s:
            return None
        c = addr_to_code.get(s.lower())
        if c:
            return c
        nk = _num_key(s)
        if nk and num_counts.get(nk, 0) == 1:
            return num_to_code.get(nk)
        return None

    from app.modules.commcalc.calculator import classify_contract_type
    acc = _acc_cfg(client, org_id)
    rows = _b2b_sales_rows(client, org_id, date,
                           "store,salesperson,department,category,product_desc,contract_type,trans_id,tender_type,ext_price,voided,trans_type")
    by_store, by_rep, counts, seen = {}, {}, {}, {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        code = resolve(r.get("store"))
        if not code:
            continue
        ext = _f(r.get("ext_price"))
        cls = _tender_class(r.get("tender_type"))
        is_acc = _is_acc(r.get("department"), r.get("category"), acc, r.get("product_desc"))
        st = by_store.setdefault(code, {"cash": 0.0, "card": 0.0, "other": 0.0, "acc_gross": 0.0, "total": 0.0})
        st[cls] += ext
        st["total"] += ext
        if is_acc:
            st["acc_gross"] += ext
        sp = (r.get("salesperson") or "").strip()
        rp = by_rep.setdefault((code, sp.lower()),
                               {"cash": 0.0, "card": 0.0, "other": 0.0, "acc_gross": 0.0, "total": 0.0, "salesperson": sp})
        rp[cls] += ext
        rp["total"] += ext
        if is_acc:
            rp["acc_gross"] += ext
        # Activation/upgrade counts from the SAME source + shared classifier (no rigid RPC).
        if str(r.get("trans_type") or "").strip() != "Return":
            tid = str(r.get("trans_id") or "").strip()
            ct_cls = classify_contract_type(r.get("contract_type"))
            s = seen.setdefault(code, {"act": set(), "upg": set()})
            if tid and ct_cls in ("premium", "byod"):
                s["act"].add(tid)
            elif tid and ct_cls == "upgrade":
                s["upg"].add(tid)

    # Flag stores/reps whose feed rows carry NO tender split (all in 'other') so the gate treats
    # them as recon-pending instead of blocking on a fabricated $0 cash/card.
    for d in (by_store, by_rep):
        for v in d.values():
            v["tenders_available"] = bool(v["total"] <= 0 or (v["cash"] + v["card"]) > 0)

    for code, s in seen.items():
        counts[code] = {"activations": len(s["act"]), "upgrades": len(s["upg"])}
    return {"has_data": len(rows) > 0, "by_store": by_store, "by_rep": by_rep, "counts": counts}


def _rep_b2b(day: dict, store_code: str, emp_name: str):
    """Best B2B money match for a rep at a store (loose name match; sums if several aliases)."""
    matches = [v for (c, _rl), v in day["by_rep"].items()
               if c == store_code and _name_match(emp_name, v.get("salesperson", ""))]
    if not matches:
        return None
    agg = {"cash": 0.0, "card": 0.0, "acc_gross": 0.0, "total": 0.0}
    for m in matches:
        for k in agg:
            agg[k] += m.get(k, 0.0)
    return agg


def _who_worked_by_store(client, org_id: str, date: str) -> dict:
    """Who ACTUALLY worked each store on `date`, so the closing checks reality instead of the roster
    (a scheduled rep who never showed shouldn't be dunned for a closing; a rep who sold but wasn't
    scheduled should be). Two independent signals, unioned per store_code:
      • clocked_in — storeops.timelog punches for the day (store_code straight off the punch).
      • sold        — distinct salespeople on that store's B2B sales rows (raw_sales / daily feed),
                      plus the set of user_logins each name transacted under, so a rep who rang
                      sales under a DIFFERENT login than they clocked in on is surfaced for recon.
    Returns {store_code: {"clocked_in": set, "sold": set, "logins": {salesperson: set(user_login)}}}."""
    out = {}

    def _bucket(code):
        return out.setdefault(code, {"clocked_in": set(), "sold": set(), "logins": {}})

    # 1) Clock-ins — authoritative store_code, independent of whether B2B is loaded.
    try:
        tl = (client.schema("storeops").table("timelog")
              .select("employee_name,store_code").eq("org_id", org_id)
              .eq("work_date", date).execute().data) or []
        for t in tl:
            code = (t.get("store_code") or "").strip()
            nm = (t.get("employee_name") or "").strip()
            if code and nm:
                _bucket(code)["clocked_in"].add(nm)
    except Exception as e:
        print("who-worked timelog load failed:", e)

    # 2) B2B sales-by-rep — resolve the raw store string → store_code the same way the money recon does.
    try:
        resolve = _addr_resolver(client, org_id)
        rows = _b2b_sales_rows(client, org_id, date, "store,salesperson,user_login,voided")
        for r in rows:
            if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                continue
            code = resolve(r.get("store"))
            sp = (r.get("salesperson") or "").strip()
            if not (code and sp):
                continue
            b = _bucket(code)
            b["sold"].add(sp)
            login = (r.get("user_login") or "").strip()
            if login:
                b["logins"].setdefault(sp, set()).add(login)
    except Exception as e:
        print("who-worked B2B load failed:", e)

    return out


def _money_issues(declared_cash, declared_credit, b2b_cash, b2b_card, tol=1.0) -> list:
    """Apply the close rules: CASH short → block / over → flag; CREDIT over → block / under → flag."""
    out = []
    cv = round(_f(declared_cash) - _f(b2b_cash), 2)
    if cv < -tol:
        out.append({"metric": "cash", "declared": round(_f(declared_cash), 2), "b2b": round(_f(b2b_cash), 2),
                    "variance": cv, "severity": "block", "reason": f"Cash short {_usd(-cv)} vs B2B sales"})
    elif cv > tol:
        out.append({"metric": "cash", "declared": round(_f(declared_cash), 2), "b2b": round(_f(b2b_cash), 2),
                    "variance": cv, "severity": "flag", "reason": f"Cash over {_usd(cv)} vs B2B — investigate"})
    rv = round(_f(declared_credit) - _f(b2b_card), 2)
    if rv > tol:
        out.append({"metric": "credit", "declared": round(_f(declared_credit), 2), "b2b": round(_f(b2b_card), 2),
                    "variance": rv, "severity": "block", "reason": f"Credit {_usd(rv)} OVER B2B card sales"})
    elif rv < -tol:
        out.append({"metric": "credit", "declared": round(_f(declared_credit), 2), "b2b": round(_f(b2b_card), 2),
                    "variance": rv, "severity": "flag", "reason": f"Credit under {_usd(-rv)} vs B2B"})
    return out


def _gate_row(client, org_id, store_code, date, emp_name, declared_cash, declared_credit, tol=1.0) -> dict:
    """Recon a single rep's close vs B2B. Returns {status, block_reasons[], flags[], b2b}. status:
    ok | flagged | blocked | recon_pending (B2B not loaded / rep not matched → never blocks)."""
    if not store_code:
        return {"status": "recon_pending", "block_reasons": [], "flags": [], "b2b": None}
    day = _b2b_day(client, org_id, date)
    if not day["has_data"]:
        return {"status": "recon_pending", "block_reasons": [], "flags": [], "b2b": None}
    repb = _rep_b2b(day, store_code, emp_name)
    if repb is None:
        return {"status": "recon_pending", "block_reasons": [], "flags": [], "b2b": None}
    if not repb.get("tenders_available", True):
        # The daily feed has no cash/card split for this rep → don't block on a fabricated $0.
        return {"status": "recon_pending", "block_reasons": [], "flags": [], "b2b": None,
                "note": "B2B tender split not in the daily feed"}
    issues = _money_issues(declared_cash, declared_credit, repb["cash"], repb["card"], tol)
    blocks = [i["reason"] for i in issues if i["severity"] == "block"]
    flags = [i["reason"] for i in issues if i["severity"] == "flag"]
    return {"status": "blocked" if blocks else ("flagged" if flags else "ok"),
            "block_reasons": blocks, "flags": flags, "b2b": {"cash": repb["cash"], "card": repb["card"]}}


# ── Universal admin-attention contributions (2026-07-26 settings audit) ─────────────────────────────
# Registers this module's checks with the CENTRAL attention system (core/import_health.py) — see
# closing/attention_providers.py for what's registered and why. Import-time side effect only (each
# provider function itself lazily imports back into this module at CALL time, never at import time,
# so there is no closing<->attention_providers circular import); guarded so a deploy that hasn't run
# migration 717 (core.import_feed) yet — or is simply missing the file for some other reason — never
# breaks this module's own endpoints.
try:
    from . import attention_providers  # noqa: F401
except Exception as _e:
    print("closing.attention_providers registration skipped:", _e)
