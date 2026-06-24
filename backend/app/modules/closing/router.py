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
