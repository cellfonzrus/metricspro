"""Daily Closing API Router — /api/v1/closing/*  (DM store-visit Phase 3).

Upload the closing sheet (one row per rep per day), DM evening verification (per-store totals +
missing-rep check vs the schedule), and reconciliation against B2B actual daily sales. Tables live
in commcalc.* (migration 029).
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from app.core.database import get_supabase
from datetime import datetime, timezone
from dateutil import parser as dateparser
import pandas as pd
import io

router = APIRouter(prefix="/closing", tags=["Daily Closing"])

ORG_ID = "00000000-0000-0000-0000-000000000001"


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
    df = df.fillna("")

    cm = _build_colmap(df)
    if "close_date" not in cm or "sfid" not in cm:
        raise HTTPException(400, f"This doesn't look like the closing sheet — need at least a Date and "
                                 f"SFID column. Found: {list(df.columns)}")

    client = sb()
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
        raise HTTPException(400, "No rows with a valid Date found in the file.")

    # Idempotent re-upload: wipe sheet-uploaded rows for the covered dates, keep manual rows.
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


# ── DM evening verification view: per-store totals + missing reps + B2B recon ─────────────
@router.get("/summary")
def closing_summary(date: str, market: str = None, org_id: str = ORG_ID):
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
                   .rpc("daily_sales_actuals", {"p_org_id": org_id, "p_period": date[:7]})
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

        out.append({
            "store_code": code, "store_name": (reps[0].get("store_name") or code or "—"),
            "store_address": meta.get("address") or reps[0].get("store_address"),
            "market": mkt, "reps": reps, "totals": totals,
            "scheduled_count": len(scheduled), "missing_reps": missing,
            "verification": ver_by_store.get(code), "recon": recon,
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
        "remarks": payload.get("remarks"), "source": "manual",
    }
    r = client.schema("commcalc").table("daily_closing").insert(body).execute()
    return r.data[0] if r.data else body


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
