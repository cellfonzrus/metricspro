"""Daily Closing API Router — /api/v1/closing/*  (DM store-visit Phase 3).

Upload the closing sheet (one row per rep per day), DM evening verification (per-store totals +
missing-rep check vs the schedule), and reconciliation against B2B actual daily sales. Tables live
in commcalc.* (migration 029).
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks, Header
from app.core.database import get_supabase
from app.core.config import settings
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as dateparser
import pandas as pd
import io
import base64
from . import gsheet

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


# ── Stores (for the rep submission form's store picker) ────────────────────────────────────
@router.get("/stores")
def closing_stores(org_id: str = ORG_ID):
    """Store options for the in-app closing form — SFID + canonical store + market."""
    client = sb()
    sm = (client.schema("commcalc").table("store_mapping")
          .select("salesforce_id,store_code,store_address").eq("org_id", org_id).execute().data) or []
    stores = (client.schema("storeops").table("stores").select("store_code,market").eq("org_id", org_id).execute().data) or []
    mkt = {s.get("store_code"): s.get("market") for s in stores if s.get("store_code")}
    out = [{
        "sfid": (r.get("salesforce_id") or "").strip(),
        "store_code": (r.get("store_code") or "").strip(),
        "store_address": r.get("store_address"),
        "market": mkt.get((r.get("store_code") or "").strip()) or "",
    } for r in sm]
    out.sort(key=lambda s: str(s.get("store_address") or ""))
    return out


# ── Monthly rollup (dashboard summaries: per-store + per-rep over a YYYY-MM period) ──────────
@router.get("/rollup")
def closing_rollup(period: str, market: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Aggregate daily_closing for a YYYY-MM period into per-store and per-rep money + counts +
    days-submitted, plus DM verification coverage. Powers the Daily Closing dashboard."""
    if not period:
        raise HTTPException(400, "period required (YYYY-MM)")
    client = sb()
    rows = (client.schema("commcalc").table("daily_closing").select("*")
            .eq("org_id", org_id).eq("period", period).limit(50000).execute().data) or []
    stores = (client.schema("storeops").table("stores").select("store_code,address,market").eq("org_id", org_id).execute().data) or []
    store_meta = {s.get("store_code"): s for s in stores if s.get("store_code")}

    # filter by period prefix in Python — "period + '-31'" makes an invalid date (e.g. 2026-06-31)
    # that Postgres rejects on the date cast. The verification table is small (one row per store/day).
    vers = (client.schema("commcalc").table("daily_closing_verification")
            .select("store_code,close_date,verified").eq("org_id", org_id).execute().data) or []
    verified_keys = {(v.get("store_code"), str(v.get("close_date"))) for v in vers
                     if v.get("verified") and str(v.get("close_date") or "").startswith(period)}

    MONEY = ("store_cash", "store_cc", "epay_cash", "epay_cc", "acc_sale", "other_account")
    COUNT = ("upgrade_count", "new_line_count", "postpaid_count")

    def blank():
        d = {k: 0.0 for k in MONEY}
        d.update({k: 0 for k in COUNT})
        d.update({"rows": 0, "_days": set()})
        return d

    by_store, by_rep, grand = {}, {}, blank()
    for r in rows:
        raw_code = r.get("store_code")
        key = raw_code or f"name:{r.get('store_name') or '—'}"
        meta = store_meta.get(raw_code, {}) if raw_code else {}
        mk = meta.get("market") or ""
        if market and mk != market:
            continue
        s_ = by_store.setdefault(key, {**blank(), "store_code": raw_code,
                                       "store_address": meta.get("address") or r.get("store_address"),
                                       "store_name": r.get("store_name"), "market": mk})
        rep_key = f"{(r.get('employee_name') or '—').strip()}||{key}"
        r_ = by_rep.setdefault(rep_key, {**blank(), "employee_name": (r.get("employee_name") or "—").strip(),
                                         "store_code": raw_code,
                                         "store_address": meta.get("address") or r.get("store_address"), "market": mk})
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

    submitted_keys = {(r.get("store_code"), str(r.get("close_date"))) for r in rows if r.get("store_code")}
    bs = sorted((finalize(v) for v in by_store.values()),
                key=lambda s: str(s.get("store_address") or s.get("store_name") or ""))
    br = sorted((finalize(v) for v in by_rep.values()), key=lambda s: -s.get("rows", 0))
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        bs = [s for s in bs if in_keyset(ks, s.get("store_code"), s.get("store_address"))]
        br = [s for s in br if in_keyset(ks, s.get("store_code"), s.get("store_address"))]
    return {
        "period": period, "by_store": bs, "by_rep": br, "totals": finalize(grand),
        "verified_keys": len(verified_keys & submitted_keys), "submitted_keys": len(submitted_keys),
    }


# ── DM evening verification view: per-store totals + missing reps + B2B recon ─────────────
@router.get("/summary")
def closing_summary(date: str, market: str = None, tolerance: float = 1.0, authorization: str = Header(default=""), org_id: str = ORG_ID):
    if not date:
        raise HTTPException(400, "date required (YYYY-MM-DD)")
    client = sb()
    rows = (client.schema("commcalc").table("daily_closing").select("*")
            .eq("org_id", org_id).eq("close_date", date).execute().data) or []

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

    # Verifications for that day.
    vers = (client.schema("commcalc").table("daily_closing_verification").select("*")
            .eq("org_id", org_id).eq("close_date", date).execute().data) or []
    ver_by_store = {v.get("store_code"): v for v in vers}

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
        if market and mkt != market:
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
        }
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

        bb = b2b.get(code, {}) if code else {}
        closing_acts = totals["new_line_count"] + totals["postpaid_count"]
        closing_upg = totals["upgrade_count"]
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
                "cash": _cmp(closing_cash, tender_cash, tenders_ok),
                "credit": _cmp(closing_credit, tender_card, tenders_ok),
                "epay": {"declared": closing_epay, "portal": None, "portal_pending": True,
                         "fee": None, "other": None, "var": None,
                         "note": "ePay Daily Transactions Report sweep not yet wired"},
                "b2b_total": bm["total"], "b2b_tenders": bm["tenders"],
                "tender_source": tender_src, "tenders_available": tenders_ok, "dept_available": dept_ok,
            }
            if not tenders_ok:
                money_recon["note"] = ("No POS X-report tender data for this day (and the sales feed has no "
                                       "Tender Type), so cash & credit can't be reconciled yet — make sure the "
                                       "daily X-report is emailed to the mailbox and imported. Shown as pending, "
                                       "not flagged.")
            money_recon["any_flag"] = any(money_recon[k].get("flag") for k in ("accessory", "cash", "credit"))

        out.append({
            "store_code": code, "store_name": (reps[0].get("store_name") or code or "—"),
            "store_address": meta.get("address") or reps[0].get("store_address"),
            # Sign each rep's private-bucket envelope path (raw path 404s as a relative href).
            "market": mkt, "reps": [{**rp, "envelope_url": _signed_envelope(rp.get("envelope_picture"))} for rp in reps],
            "totals": totals,
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
        if market and mkt != market:
            continue
        clocked = set(ww.get("clocked_in", set()))
        sold = set(ww.get("sold", set()))
        worked = {n for n in (clocked | sold) if n}
        if not worked:
            continue
        scheduled = sched_by_store.get(code, set())
        closer = closer_by_store.get(code)
        owes = ({closer} if closer else worked) if closing_mode == "one_closing" else worked
        logins = ww.get("logins", {})
        cross_login = sorted(
            [{"salesperson": nm, "logins": sorted(logins.get(nm, set()))}
             for nm in sold if not any(_name_match(nm, c) for c in clocked)],
            key=lambda x: x["salesperson"])
        out.append({
            "store_code": code, "store_name": meta.get("address") or code, "store_address": meta.get("address"),
            "market": mkt, "reps": [], "totals": None,
            "scheduled_count": len(scheduled), "missing_reps": sorted({n for n in owes if n}),
            "worked_reps": sorted(worked), "worked_count": len(worked),
            "scheduled_no_show": sorted({nm for nm in scheduled if not any(_name_match(nm, w) for w in worked)}),
            "worked_unscheduled": sorted({nm for nm in worked if not any(_name_match(nm, s) for s in scheduled)}),
            "cross_login": cross_login, "closing_mode": closing_mode, "closer": closer,
            "no_closing_submitted": True,
            "verification": ver_by_store.get(code), "recon": None, "money_recon": None,
        })

    out.sort(key=lambda s: str(s.get("store_address") or s.get("store_name") or ""))
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        out = [s for s in out if in_keyset(ks, s.get("store_code"), s.get("store_address"))]
    return {"date": date, "stores": out}


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
        "upgrade_count": _int(payload.get("upgrade_count")), "new_line_count": _int(payload.get("new_line_count")),
        "postpaid_count": _int(payload.get("postpaid_count")),
        "envelope_picture": (payload.get("envelope_picture") or "").strip() or None,
        "remarks": payload.get("remarks"), "source": "manual",
    }
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
    # Keep the legacy columns populated so existing dashboards / recon keep reconciling unchanged.
    body["store_cash"] = tenders["cash"]
    body["epay_cash"] = 0.0
    body["store_cc"] = round(tenders["credit"] + tenders["ext_cc"], 2)
    body["epay_cc"] = 0.0
    body["other_account"] = round(tenders["zelle"] + tenders["store_acct"] + tenders["gift"], 2)

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
    r = client.schema("commcalc").table("daily_closing").insert(body).execute()
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
    r = sb().schema("commcalc").table("daily_closing").update(body).eq("id", row_id).execute()
    return r.data[0] if r.data else body


@router.delete("/row/{row_id}")
def delete_row(row_id: str):
    sb().schema("commcalc").table("daily_closing").delete().eq("id", row_id).execute()
    return {"deleted": row_id}


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
    out = []
    for (dt, sc, emp), tries in groups.items():
        tries.sort(key=lambda x: x.get("attempt_no") or 0)
        last = tries[-1]
        auto = any(t.get("auto_accepted") for t in tries)
        if only_review and not (len(tries) > 1 or auto):
            continue
        out.append({
            "close_date": dt, "store_code": sc, "store_address": last.get("store_address"),
            "employee_name": emp, "attempts": len(tries), "auto_accepted": auto,
            "final_dir": {"cash": last.get("cash_dir"), "credit": last.get("credit_dir")},
            "b2b": {"cash": last.get("b2b_cash"), "credit": last.get("b2b_credit")},
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
def _sales_tenders_by_store(client, org_id: str, date: str) -> dict:
    """The day's sales-transaction $ bucketed to the 6 canonical tenders, per store_code (from the same
    unified B2B source the money recon uses). Sums ext_price per tender — merchandise by tender, so it
    tracks the X-report's tender split (which also includes tax, hence small deltas are expected)."""
    resolve = _addr_resolver(client, org_id)
    rows = _b2b_sales_rows(client, org_id, date, "store,tender_type,ext_price,voided,trans_type")
    out = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        canon = _canon_tender(r.get("tender_type"))
        if not canon:
            continue
        code = resolve(r.get("store")) or (r.get("store") or "?")
        agg = out.setdefault(code, {t: 0.0 for t in CANON_TENDERS})
        agg[canon] += _f(r.get("ext_price"))
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
    # (1) closing — rep t_* per store_code
    closing = {}
    cq = (client.schema("commcalc").table("daily_closing")
          .select("store_code,store_address,t_cash,t_credit,t_ext_cc,t_gift,t_store_acct,t_zelle,t_acima")
          .eq("org_id", org_id).eq("close_date", d))
    if store:
        cq = cq.eq("store_code", store)
    addr_by_code = {}
    for r in (cq.limit(50000).execute().data or []):
        code = r.get("store_code") or "?"
        if r.get("store_address"):
            addr_by_code[code] = r.get("store_address")
        agg = closing.setdefault(code, {t: 0.0 for t in CANON_TENDERS})
        for t, col in (("cash", "t_cash"), ("credit", "t_credit"), ("ext_cc", "t_ext_cc"),
                       ("gift", "t_gift"), ("store_acct", "t_store_acct"), ("zelle", "t_zelle"),
                       ("acima", "t_acima")):
            agg[t] += _f(r.get(col))
    # (2) X-report — pos_tender_summary raw tender_type → canon
    resolve = _addr_resolver(client, org_id)
    xrep = {}
    xrows = (client.schema("commcalc").table("pos_tender_summary")
             .select("store,tender_type,amount").eq("org_id", org_id).eq("close_date", d).execute().data) or []
    for r in xrows:
        canon = _canon_tender(r.get("tender_type"))
        if not canon:
            continue
        code = resolve(r.get("store")) or (r.get("store") or "?")
        agg = xrep.setdefault(code, {t: 0.0 for t in CANON_TENDERS})
        agg[canon] += _f(r.get("amount"))
    # (3) sales transactions
    sales = _sales_tenders_by_store(client, org_id, d)
    if store:
        xrep = {k: v for k, v in xrep.items() if k == store}
        sales = {k: v for k, v in sales.items() if k == store}
    # store names
    sm = (client.schema("commcalc").table("store_mapping").select("store_code,store_address")
          .eq("org_id", org_id).execute().data) or []
    name_by_code = {s.get("store_code"): s.get("store_address") for s in sm if s.get("store_code")}
    codes = sorted(set(closing) | set(xrep) | set(sales))
    stores_out = []
    for code in codes:
        c, x, s = closing.get(code, {}), xrep.get(code, {}), sales.get(code, {})
        per = []
        for t in CANON_TENDERS:
            cv, xv, sv = round(c.get(t, 0), 2), round(x.get(t, 0), 2), round(s.get(t, 0), 2)
            per.append({"tender": t, "label": CANON_TENDER_LABEL[t], "closing": cv, "x_report": xv, "sales": sv,
                        "match": abs(cv - xv) <= 1 and abs(xv - sv) <= 1 and abs(cv - sv) <= 1})
        stores_out.append({
            "store_code": code, "store_address": name_by_code.get(code) or addr_by_code.get(code) or code,
            "tenders": per,
            "totals": {"closing": round(sum(c.values()), 2), "x_report": round(sum(x.values()), 2),
                       "sales": round(sum(s.values()), 2)}})
    return {"date": d, "tenders": [{"key": t, "label": CANON_TENDER_LABEL[t]} for t in CANON_TENDERS],
            "stores": stores_out,
            "sources_present": {"closing": bool(closing), "x_report": bool(xrep), "sales": bool(sales)},
            "note": ("X-report tender amounts include tax; sales-transaction figures are merchandise "
                     "(ext price), so small deltas between those two are expected.")}


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


# ── Reconciliation sheet: every day's closing-vs-B2B errors over a period ────────────────────
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
                cl_act = sum(int(x.get("new_line_count") or 0) + int(x.get("postpaid_count") or 0) for x in reps)
                cl_upg = sum(int(x.get("upgrade_count") or 0) for x in reps)
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
    return {"closing_deadline": tenant.get("closing_deadline"),
            "closing_gate_enabled": bool(tenant.get("closing_gate_enabled")),
            "cash_alert_after_days": tenant.get("cash_alert_after_days"),
            "closing_mode": (tenant.get("closing_mode") or "per_rep"),
            "closers": closers, "recipients": recips}


@router.put("/cash-config")
def put_cash_config(body: dict, org_id: str = ORG_ID):
    """Update the closing-gate + cash-aging settings (defined at onboarding). Admin/manager only in the UI."""
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
        sb().schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    return get_cash_config(org_id)


@router.put("/cash-config/closer")
def set_store_closer(body: dict, org_id: str = ORG_ID):
    """Assign (or clear) the closer for a store. Body: {store_code, employee_id?, employee_name?}."""
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
def upsert_alert_recipient(body: dict, org_id: str = ORG_ID):
    """Add/update an alert recipient. Body: {id?, scope, name?, email?, whatsapp?, via_email?, via_whatsapp?, include_dm?}."""
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
def delete_alert_recipient(rid: str, org_id: str = ORG_ID):
    sb().schema("storeops").table("alert_recipient").delete().eq("id", rid).eq("org_id", org_id).execute()
    return {"ok": True}


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
                    store: str = "", employee: str = "", dm: str = "", org_id: str = ORG_ID):
    """Cash envelopes + their pickup/deposit status, FILTERABLE by date (or start..end range), store,
    sales rep (employee), and the DM who collected. An envelope = a rep's closing row with cash to
    collect (store_cash + epay_cash > 0) or an envelope photo."""
    if not (date or (start and end)):
        raise HTTPException(400, "date, or start+end, required (YYYY-MM-DD)")
    client = sb()
    q = client.schema("commcalc").table("daily_closing").select("*").eq("org_id", org_id)
    q = q.eq("close_date", date) if date else q.gte("close_date", start).lte("close_date", end)
    rows = q.execute().data or []
    stores = (client.schema("storeops").table("stores").select("store_code,address,market").eq("org_id", org_id).execute().data) or []
    smeta = {s.get("store_code"): s for s in stores if s.get("store_code")}
    pq = client.schema("commcalc").table("cash_pickup").select("*").eq("org_id", org_id)
    pq = pq.eq("close_date", date) if date else pq.gte("close_date", start).lte("close_date", end)
    try:
        picks = pq.execute().data or []
    except Exception:
        picks = []
    pick_by = {((p.get("store_code") or ""), (p.get("employee_name") or ""), str(p.get("close_date"))): p for p in picks}
    store_f, emp_f, dm_f = store.strip().upper(), employee.strip().lower(), dm.strip().lower()

    out = []
    for r in rows:
        cash = _f(r.get("store_cash")) + _f(r.get("epay_cash"))
        if cash <= 0 and not r.get("envelope_picture"):
            continue
        code = r.get("store_code") or ""
        meta = smeta.get(code, {})
        mk = meta.get("market") or ""
        if market and mk != market:
            continue
        if store_f and (code or "").upper() != store_f:
            continue
        if emp_f and emp_f not in (r.get("employee_name") or "").lower():
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
    return {"date": date, "start": start, "end": end, "envelopes": out,
            "ready": sum(1 for e in out if not e["picked_up"]),
            "collected": sum(1 for e in out if e["picked_up"]),
            "flagged": sum(1 for e in out if e["deposit_flagged"])}


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
    """Confirm the DM picked up the selected cash envelopes, then notify the assigned recipient."""
    client = sb()
    date = _date(payload.get("date") or payload.get("close_date"))
    if not date:
        raise HTTPException(400, "valid date required")
    items = payload.get("items") or []
    if not items:
        raise HTTPException(400, "Select at least one envelope.")
    dm = (payload.get("picked_up_by") or "DM").strip()
    total = 0.0
    for it in items:
        amt = _f(it.get("amount") or it.get("cash"))
        total += amt
        row = {"org_id": org_id, "close_date": date, "store_code": it.get("store_code") or "",
               "store_name": it.get("store_name"), "employee_name": (it.get("employee_name") or ""),
               "amount": amt, "picked_up": True, "picked_up_by": dm, "picked_up_at": _now(),
               "note": (it.get("note") or "").strip() or None}
        client.schema("commcalc").table("cash_pickup").upsert(
            row, on_conflict="org_id,close_date,store_code,employee_name").execute()
    notify = await _notify_pickup(client, org_id, dm, date, items, round(total, 2))
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
def closing_sweep_put_config(body: dict, org_id: str = ORG_ID):
    client = sb()
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
    """Resolve the logged-in user's role permissions (same source as /core/me) for a backend gate."""
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
        return perms
    except Exception:
        return {}


def _can_mgmt_review(perms: dict) -> bool:
    """Management-review gate: super-admin, an explicit page grant, or company-wide ('all') scope.
    DMs (market/store scope) are excluded unless an admin grants /closing/management to their role."""
    if perms.get("__super_admin"):
        return True
    ov = (perms.get("pages") or {}).get("/closing/management")
    if isinstance(ov, bool):
        return ov
    return (perms.get("scope") or "all") == "all"


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
    it didn't fall to the feed which HAD the data. Either/or per day, so no double counting."""
    def _q(table):
        return (client.schema("commcalc").table(table).select(cols)
                .eq("org_id", org_id).in_("period", [_period_label(date), date[:7]])
                .eq("trans_date", date).limit(100000).execute().data) or []
    open_month = str(date)[:7] == _biz_today_iso()[:7]
    primary, other = ("daily_sales_feed", "raw_sales") if open_month else ("raw_sales", "daily_sales_feed")
    rows = _q(primary)
    if not rows:
        try:
            rows = _q(other)
        except Exception:
            pass
    return rows


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
    rows = _b2b_sales_rows(client, org_id, date, "store,department,category,product_desc,tender_type,ext_price,voided")

    out = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        code = resolve(r.get("store"))
        if not code:
            continue
        ext = _f(r.get("ext_price"))
        agg = out.setdefault(code, {"acc_gross": 0.0, "cash": 0.0, "card": 0.0,
                                    "other": 0.0, "total": 0.0, "tenders": {}, "_dept_seen": False})
        agg["total"] += ext
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
        for k in ("acc_gross", "cash", "card", "other", "total"):
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
