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
from . import gsheet

router = APIRouter(prefix="/closing", tags=["Daily Closing"])

ORG_ID = "00000000-0000-0000-0000-000000000001"


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
    stores = (client.schema("storeops").table("stores").select("store_code,market").execute().data) or []
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
def closing_rollup(period: str, market: str = None, org_id: str = ORG_ID):
    """Aggregate daily_closing for a YYYY-MM period into per-store and per-rep money + counts +
    days-submitted, plus DM verification coverage. Powers the Daily Closing dashboard."""
    if not period:
        raise HTTPException(400, "period required (YYYY-MM)")
    client = sb()
    rows = (client.schema("commcalc").table("daily_closing").select("*")
            .eq("org_id", org_id).eq("period", period).limit(50000).execute().data) or []
    stores = (client.schema("storeops").table("stores").select("store_code,address,market").execute().data) or []
    store_meta = {s.get("store_code"): s for s in stores if s.get("store_code")}

    vers = (client.schema("commcalc").table("daily_closing_verification")
            .select("store_code,close_date,verified").eq("org_id", org_id)
            .gte("close_date", period + "-01").lte("close_date", period + "-31").execute().data) or []
    verified_keys = {(v.get("store_code"), str(v.get("close_date"))) for v in vers if v.get("verified")}

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
    return {
        "period": period, "by_store": bs, "by_rep": br, "totals": finalize(grand),
        "verified_keys": len(verified_keys & submitted_keys), "submitted_keys": len(submitted_keys),
    }


# ── DM evening verification view: per-store totals + missing reps + B2B recon ─────────────
@router.get("/summary")
def closing_summary(date: str, market: str = None, tolerance: float = 1.0, org_id: str = ORG_ID):
    if not date:
        raise HTTPException(400, "date required (YYYY-MM-DD)")
    client = sb()
    rows = (client.schema("commcalc").table("daily_closing").select("*")
            .eq("org_id", org_id).eq("close_date", date).execute().data) or []

    # Store + market context.
    stores = (client.schema("storeops").table("stores")
              .select("store_code,address,market").execute().data) or []
    store_meta = {s.get("store_code"): s for s in stores if s.get("store_code")}

    # Scheduled reps that day (to flag who didn't submit).
    shifts = (client.schema("storeops").table("shifts").select("store_code,employee_name")
              .eq("is_deleted", False).eq("shift_date", date).execute().data) or []
    sched_by_store = {}
    for s in shifts:
        sc = s.get("store_code")
        nm = (s.get("employee_name") or "").strip()
        if sc and nm:
            sched_by_store.setdefault(sc, set()).add(nm)

    # B2B actual daily sales for that period → that day, per store.
    b2b = {}
    try:
        actuals = (client.schema("commcalc")
                   .rpc("daily_sales_actuals", {"p_org_id": org_id, "p_period": _period_label(date)})
                   .execute().data) or []
        for a in actuals:
            if str(a.get("trans_date"))[:10] != date:
                continue
            sc = a.get("store_code")
            if not sc:
                continue
            agg = b2b.setdefault(sc, {"activations": 0, "upgrades": 0, "acc_gp": 0.0})
            agg["activations"] += int(a.get("prem_count") or 0) + int(a.get("byod_count") or 0)
            agg["upgrades"] += int(a.get("upg_count") or 0)
            agg["acc_gp"] += float(a.get("acc_gp") or 0)
    except Exception as e:
        print("closing B2B recon RPC failed:", e)

    # B2B MONEY actuals for that day (accessory gross, cash vs card by tender) → store money-recon.
    try:
        b2b_money = _b2b_money_by_store(client, org_id, date)
    except Exception as e:
        print("closing B2B money recon failed:", e)
        b2b_money = {}

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
        scheduled = sched_by_store.get(code, set()) if code else set()
        missing = [nm for nm in scheduled
                   if not any(_name_match(nm, sn) for sn in submitted_names if sn)]

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

            def _cmp(closing_v, b2b_v):
                var = round(closing_v - b2b_v, 2)
                return {"closing": round(closing_v, 2), "b2b": round(b2b_v, 2), "var": var,
                        "shortage": var < -tolerance, "overage": var > tolerance,
                        "flag": abs(var) > tolerance}

            money_recon = {
                "tolerance": tolerance,
                "accessory": _cmp(totals["acc_sale"], bm["acc_gross"]),  # gross vs gross
                "cash": _cmp(closing_cash, bm["cash"]),
                "credit": _cmp(closing_credit, bm["card"]),
                "epay": {"declared": closing_epay, "portal": None, "portal_pending": True,
                         "fee": None, "other": None, "var": None,
                         "note": "ePay Daily Transactions Report sweep not yet wired"},
                "b2b_total": bm["total"], "b2b_tenders": bm["tenders"],
            }
            money_recon["any_flag"] = any(money_recon[k]["flag"]
                                          for k in ("accessory", "cash", "credit"))

        out.append({
            "store_code": code, "store_name": (reps[0].get("store_name") or code or "—"),
            "store_address": meta.get("address") or reps[0].get("store_address"),
            "market": mkt, "reps": reps, "totals": totals,
            "scheduled_count": len(scheduled), "missing_reps": missing,
            "verification": ver_by_store.get(code), "recon": recon, "money_recon": money_recon,
        })

    out.sort(key=lambda s: str(s.get("store_address") or s.get("store_name") or ""))
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
@router.post("/row")
def create_row(payload: dict, org_id: str = ORG_ID):
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
        "store_cash": _money(payload.get("store_cash")), "store_cc": _money(payload.get("store_cc")),
        "epay_cash": _money(payload.get("epay_cash")), "epay_cc": _money(payload.get("epay_cc")),
        "acc_sale": _money(payload.get("acc_sale")), "other_account": _money(payload.get("other_account")),
        "upgrade_count": _int(payload.get("upgrade_count")), "new_line_count": _int(payload.get("new_line_count")),
        "postpaid_count": _int(payload.get("postpaid_count")),
        "envelope_picture": (payload.get("envelope_picture") or "").strip() or None,
        "remarks": payload.get("remarks"), "source": "manual",
    }
    # Close gate: rep can't submit if cash is SHORT or credit is OVER vs B2B (when B2B is loaded
    # and the rep matches B2B sales). Cash-over / credit-under are allowed but flagged. If B2B
    # isn't loaded yet, or the rep didn't match B2B, it's recon-pending and never blocks.
    declared_cash = body["store_cash"] + body["epay_cash"]
    declared_credit = body["store_cc"] + body["epay_cc"]
    tol = float(payload.get("tolerance") or 1.0)
    gate = _gate_row(client, org_id, body.get("store_code"), d, body.get("employee_name") or "",
                     declared_cash, declared_credit, tol)
    if gate["block_reasons"]:
        raise HTTPException(409, "Can't close shift — " + "; ".join(gate["block_reasons"])
                            + ". Recount and correct, then resubmit.")
    r = client.schema("commcalc").table("daily_closing").insert(body).execute()
    saved = r.data[0] if r.data else body
    return {**saved, "recon": {"status": gate["status"], "flags": gate["flags"], "b2b": gate["b2b"]}}


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


# ── Reconciliation sheet: every day's closing-vs-B2B errors over a period ────────────────────
@router.get("/recon")
def closing_recon(period: str, market: str = None, tolerance: float = 1.0, org_id: str = ORG_ID):
    """Per-rep (money) + per-store (counts) reconciliation of declared closing vs B2B actuals for
    a YYYY-MM period. Returns one error row per discrepancy with severity block | flag, plus
    recon-pending rows where B2B isn't loaded / the rep didn't match B2B sales."""
    if not period:
        raise HTTPException(400, "period required (YYYY-MM)")
    client = sb()
    closing = (client.schema("commcalc").table("daily_closing").select("*")
               .eq("org_id", org_id).eq("period", period).limit(50000).execute().data) or []
    stores = (client.schema("storeops").table("stores").select("store_code,address,market").execute().data) or []
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


@router.get("/pickups")
def closing_pickups(date: str, market: str = None, org_id: str = ORG_ID):
    """Cash envelopes for a day + their pickup status. An envelope = a rep's closing row with cash
    to collect (store_cash + epay_cash > 0) or an envelope photo."""
    if not date:
        raise HTTPException(400, "date required (YYYY-MM-DD)")
    client = sb()
    rows = (client.schema("commcalc").table("daily_closing").select("*")
            .eq("org_id", org_id).eq("close_date", date).execute().data) or []
    stores = (client.schema("storeops").table("stores").select("store_code,address,market").execute().data) or []
    smeta = {s.get("store_code"): s for s in stores if s.get("store_code")}
    try:
        picks = (client.schema("commcalc").table("cash_pickup").select("*")
                 .eq("org_id", org_id).eq("close_date", date).execute().data) or []
    except Exception:
        picks = []
    pick_by = {((p.get("store_code") or ""), (p.get("employee_name") or "")): p for p in picks}

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
        p = pick_by.get((code, (r.get("employee_name") or "")))
        out.append({
            "store_code": r.get("store_code"),
            "store_name": meta.get("address") or r.get("store_address") or r.get("store_name"),
            "market": mk, "employee_name": r.get("employee_name"), "cash": round(cash, 2),
            "envelope_picture": r.get("envelope_picture"),
            "picked_up": bool(p and p.get("picked_up")),
            "picked_up_by": p.get("picked_up_by") if p else None,
            "picked_up_at": p.get("picked_up_at") if p else None, "note": p.get("note") if p else None,
        })
    out.sort(key=lambda e: (e["picked_up"], str(e.get("store_name") or "")))
    return {"date": date, "envelopes": out,
            "ready": sum(1 for e in out if not e["picked_up"]),
            "collected": sum(1 for e in out if e["picked_up"])}


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


def _num_key(s: str) -> str:
    """Leading store-number, digits only ('116-36 Springfield Blvd' → '11636'). The project's
    standard cross-source store join (calculator.py / coa.store_resolver)."""
    m = _re.match(r"\s*([0-9][0-9-]*)", str(s or ""))
    return _re.sub(r"\D", "", m.group(1)) if m else ""


def _b2b_money_by_store(client, org_id: str, date: str) -> dict:
    """Aggregate that day's B2B sales (raw_sales) per store_code: accessory GROSS (ext_price,
    dept Ondigo), cash vs card totals (by tender_type), plus the raw tender breakdown for
    transparency. raw_sales.store is matched to store_code by exact address then by an
    unambiguous leading street-number."""
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

    rows = (client.schema("commcalc").table("raw_sales")
            .select("store,department,tender_type,ext_price,voided")
            .eq("org_id", org_id).in_("period", [_period_label(date), date[:7]]).eq("trans_date", date)
            .limit(100000).execute().data) or []

    out = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        code = resolve(r.get("store"))
        if not code:
            continue
        ext = _f(r.get("ext_price"))
        agg = out.setdefault(code, {"acc_gross": 0.0, "cash": 0.0, "card": 0.0,
                                    "other": 0.0, "total": 0.0, "tenders": {}})
        agg["total"] += ext
        agg[_tender_class(r.get("tender_type"))] += ext
        if (r.get("department") or "").strip() == "Ondigo":
            agg["acc_gross"] += ext
        tname = (r.get("tender_type") or "—").strip() or "—"
        agg["tenders"][tname] = round(agg["tenders"].get(tname, 0.0) + ext, 2)

    for a in out.values():
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

    rows = (client.schema("commcalc").table("raw_sales")
            .select("store,salesperson,department,tender_type,ext_price,voided")
            .eq("org_id", org_id).in_("period", [_period_label(date), date[:7]]).eq("trans_date", date)
            .limit(100000).execute().data) or []
    by_store, by_rep = {}, {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        code = resolve(r.get("store"))
        if not code:
            continue
        ext = _f(r.get("ext_price"))
        cls = _tender_class(r.get("tender_type"))
        st = by_store.setdefault(code, {"cash": 0.0, "card": 0.0, "other": 0.0, "acc_gross": 0.0, "total": 0.0})
        st[cls] += ext
        st["total"] += ext
        if (r.get("department") or "").strip() == "Ondigo":
            st["acc_gross"] += ext
        sp = (r.get("salesperson") or "").strip()
        rp = by_rep.setdefault((code, sp.lower()),
                               {"cash": 0.0, "card": 0.0, "other": 0.0, "acc_gross": 0.0, "total": 0.0, "salesperson": sp})
        rp[cls] += ext
        rp["total"] += ext
        if (r.get("department") or "").strip() == "Ondigo":
            rp["acc_gross"] += ext

    counts = {}
    try:
        actuals = (client.schema("commcalc")
                   .rpc("daily_sales_actuals", {"p_org_id": org_id, "p_period": _period_label(date)})
                   .execute().data) or []
        for a in actuals:
            if str(a.get("trans_date"))[:10] != date:
                continue
            sc = a.get("store_code")
            if not sc:
                continue
            c = counts.setdefault(sc, {"activations": 0, "upgrades": 0})
            c["activations"] += int(a.get("prem_count") or 0) + int(a.get("byod_count") or 0)
            c["upgrades"] += int(a.get("upg_count") or 0)
    except Exception:
        pass
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
    issues = _money_issues(declared_cash, declared_credit, repb["cash"], repb["card"], tol)
    blocks = [i["reason"] for i in issues if i["severity"] == "block"]
    flags = [i["reason"] for i in issues if i["severity"] == "flag"]
    return {"status": "blocked" if blocks else ("flagged" if flags else "ok"),
            "block_reasons": blocks, "flags": flags, "b2b": {"cash": repb["cash"], "card": repb["card"]}}
