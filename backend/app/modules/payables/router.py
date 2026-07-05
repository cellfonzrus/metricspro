"""Device Forecasting & Vendor Payables — HTTP endpoints (module 095).

Mounted at /api/v1/payables (bare router; prefix carried in main.py, like the asset module).
GETs are open for easy curl verification (same convention as /commcalc/sales-diagnostics).
The only mutating endpoint is POST /rebuild.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.core.database import get_supabase
from app.modules.asset.router import _norm_imei, _vip_invoice_map, _epay_payments_map
from app.modules.payables import engine

router = APIRouter()
ORG_ID = "00000000-0000-0000-0000-000000000001"
PAGE = 1000
DEV_DEPTS = {"android - xp", "iphone - xp", "tablet - xp"}   # device box lines (mig 013 daily_sales_actuals)


def sb():
    return get_supabase()


def _canon(s, alias):
    """Canonical model: strip a ' - promo' suffix + apply the device_model_alias map."""
    base = str(s or "").split(" - ")[0].strip()
    return alias.get(base.lower(), base) or base


def _fetch_all(make_query, cap=80):
    out, page = [], 0
    while True:
        chunk = make_query().range(page * PAGE, page * PAGE + PAGE - 1).execute().data or []
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > cap:
            break
    return out


# ── the only mutating endpoint ────────────────────────────────────────────────
@router.post("/rebuild")
def rebuild(carrier_id: str = "", org_id: str = ORG_ID):
    """Rebuild the per-IMEI payable ledger (all carriers, or one) + refresh the discrepancy flags.
    Delete+insert per carrier. May run long on a full Boost rebuild; if the browser 502s it still
    completes server-side (like the commission recalc) — poll /payables/payables afterwards."""
    client = sb()
    built = engine.build_ledger(client, org_id, carrier_id or None)
    try:
        built["flags_written"] = engine.sync_payable_flags(client, org_id)
    except Exception as e:
        built["flags_error"] = str(e)[:200]
    return built


# ── Part A — forecast (phones only) ───────────────────────────────────────────
@router.get("/forecast")
def forecast(days: int = 30, store: str = "", org_id: str = ORG_ID):
    """Phones-only: velocity (raw_sales device lines over the trailing `days`) vs on-hand
    (asset_ledger unsold On-Inventory) → recommend_order per store/model."""
    client = sb()
    days = max(1, min(days, 365))
    alias = engine._load_model_alias(client, org_id)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()

    def vel_q():
        q = (client.schema("commcalc").table("raw_sales")
             .select("store,product_desc,department,voided,trans_type,salesperson")
             .eq("org_id", org_id).gte("trans_date", cutoff))
        return q.eq("store", store) if store else q
    vel = {}
    for r in _fetch_all(vel_q):
        if (r.get("voided") or "").upper() == "YES" or (r.get("trans_type") or "") == "Return":
            continue
        if (r.get("department") or "").strip().lower() not in DEV_DEPTS:
            continue
        sp = (r.get("salesperson") or "").strip().lower()
        if not sp or sp == "admin":
            continue
        key = (r.get("store"), _canon(r.get("product_desc"), alias))
        vel[key] = vel.get(key, 0) + 1

    def onhand_q():
        q = (client.schema("commcalc").table("asset_ledger")
             .select("store,device_model,date_sold,category").eq("org_id", org_id))
        return q.eq("store", store) if store else q
    onhand = {}
    for r in _fetch_all(onhand_q):
        if r.get("date_sold") or "on inventory" not in (r.get("category") or "").lower():
            continue
        key = (r.get("store"), _canon(r.get("device_model"), alias))
        onhand[key] = onhand.get(key, 0) + 1

    out = []
    for k in set(vel) | set(onhand):
        st, model = k
        units = vel.get(k, 0)
        rate = units / days
        projected = int(round(rate * days))
        oh = onhand.get(k, 0)
        out.append({"store": st, "device_model": model, "units_sold_window": units,
                    "avg_daily_velocity": round(rate, 2), "projected_demand": projected,
                    "on_hand": oh, "recommend_order": max(0, projected - oh)})
    out.sort(key=lambda x: (x["recommend_order"], x["units_sold_window"]), reverse=True)
    return {"days": days, "store": store or None, "rows": out, "total": len(out)}


# ── Part B — payables + offsets + due ─────────────────────────────────────────
@router.get("/payables")
def list_payables(store: str = "", carrier_id: str = "", status: str = "", limit: int = 500, org_id: str = ORG_ID):
    client = sb()
    q = (client.schema("commcalc").table("device_payable_ledger").select("*").eq("org_id", org_id))
    if carrier_id:
        q = q.eq("carrier_id", carrier_id)
    if store:
        q = q.eq("store", store)
    if status:
        q = q.eq("status", status)
    rows = q.order("net_owed", desc=True).limit(limit).execute().data or []
    # decorate with the VIP invoice #/date (join by IMEI == serial)
    vip = _vip_invoice_map(client, org_id, [r.get("imei") for r in rows])
    for r in rows:
        v = vip.get(_norm_imei(r.get("imei")))
        r["vip_invoice_number"] = v["vip_invoice_number"] if v else None
    return {"rows": rows, "total": len(rows)}


@router.get("/offsets/{imei}")
def offsets(imei: str, org_id: str = ORG_ID):
    """Drill-down: exactly what offsets the owed amount for one device (owed line + primary rebate +
    each ePay reimbursement) with the cross-check mismatch surfaced."""
    client = sb()
    n = _norm_imei(imei)
    led = (client.schema("commcalc").table("device_payable_ledger").select("*")
           .eq("org_id", org_id).eq("imei", n).limit(1).execute().data) or []
    if not led:
        raise HTTPException(404, f"No payable ledger row for IMEI {n} (rebuild first?)")
    row = led[0]
    reimb_types = engine._reimb_types(client, org_id)
    epay = _epay_payments_map(client, org_id, [n]).get(n, [])
    epay_lines = [e for e in epay if (e.get("type") or "").lower() in reimb_types
                  or "reimb" in (e.get("type") or "").lower()]
    return {
        "imei": n, "store": row.get("store"), "device_model": row.get("device_model"),
        "status": row.get("status"), "owed": row.get("owed"), "owed_source": row.get("owed_source"),
        "primary_rebate": {"amount": row.get("rebate_amount"), "date": row.get("rebate_date"),
                           "source": row.get("rebate_source")},
        "epay_crosscheck": {"amount": row.get("epay_rebate_amount"), "mismatch": row.get("rebate_mismatch"),
                            "lines": epay_lines},
        "net_offset": row.get("net_offset"), "net_owed": row.get("net_owed"),
        "sold": row.get("sold_flag"), "sold_date": row.get("sold_date"),
        "due_date": row.get("due_date"), "due_source": row.get("due_source"),
    }


@router.get("/due")
def due(as_of: str = "", store: str = "", carrier_id: str = "", org_id: str = ORG_ID):
    """DUE report for a billing Friday — grouped by bill_path, mirroring /asset/owed-weekly's aging/sold
    buckets. Because build_ledger copies billing_friday/bill_path/owed verbatim from asset_ledger, the
    aging OWED total here equals get_owed_weekly.due_this_week.aging.owed for the same date."""
    if not as_of:
        raise HTTPException(400, "as_of=<billing friday YYYY-MM-DD> required")
    client = sb()
    q = (client.schema("commcalc").table("device_payable_ledger")
         .select("store,bill_path,owed,imei,device_model,due_date")
         .eq("org_id", org_id).eq("billing_friday", as_of))
    if carrier_id:
        q = q.eq("carrier_id", carrier_id)
    if store:
        q = q.eq("store", store)
    rows = q.limit(20000).execute().data or []
    agg = {"aging": {"count": 0, "owed": 0.0}, "billed": {"count": 0, "owed": 0.0}}
    for r in rows:
        b = "aging" if r.get("bill_path") == "aging" else "billed"
        agg[b]["count"] += 1
        agg[b]["owed"] += float(r.get("owed") or 0)
    for b in agg:
        agg[b]["owed"] = round(agg[b]["owed"], 2)
    agg["total"] = {"count": agg["aging"]["count"] + agg["billed"]["count"],
                    "owed": round(agg["aging"]["owed"] + agg["billed"]["owed"], 2)}
    return {"as_of": as_of, "buckets": agg, "rows": rows,
            "note": "aging.owed matches /api/v1/asset/owed-weekly due_this_week.aging.owed"}


@router.get("/owed-by-date")
def owed_by_date(start: str = "", end: str = "", store: str = "", org_id: str = ORG_ID):
    """Consolidated daily owed: net_owed grouped by due_date."""
    client = sb()
    q = (client.schema("commcalc").table("device_payable_ledger")
         .select("due_date,net_owed,owed,store").eq("org_id", org_id))
    if start:
        q = q.gte("due_date", start)
    if end:
        q = q.lte("due_date", end)
    if store:
        q = q.eq("store", store)
    rows = q.limit(50000).execute().data or []
    by_date = {}
    for r in rows:
        d = r.get("due_date")
        if not d:
            continue
        amt = r.get("net_owed")
        if amt is None:
            amt = r.get("owed") or 0
        e = by_date.setdefault(d, {"due_date": d, "count": 0, "owed": 0.0})
        e["count"] += 1
        e["owed"] += float(amt or 0)
    out = [dict(v, owed=round(v["owed"], 2)) for v in by_date.values()]
    out.sort(key=lambda x: x["due_date"])
    return {"rows": out, "total_owed": round(sum(x["owed"] for x in out), 2)}


# ── Part C — per-store priority list ──────────────────────────────────────────
@router.get("/priority")
def priority(store: str = "", employee_id: str = "", org_id: str = ORG_ID):
    """Per-store priority-sell list (devices in the final pct% of their pay window)."""
    client = sb()
    return {"store": store or None, "rows": engine.priority_for_store(client, org_id, store)}


# ── config: per-carrier payable source mapping (add-a-carrier-by-config) ──────
@router.get("/source-maps")
def list_source_maps(org_id: str = ORG_ID):
    client = sb()
    return {"rows": (client.schema("commcalc").table("payable_source_map").select("*")
                     .eq("org_id", org_id).execute().data) or []}


@router.post("/source-maps")
def upsert_source_map(body: dict, org_id: str = ORG_ID):
    client = sb()
    row = dict(body or {})
    row["org_id"] = org_id
    if not row.get("carrier_id") or not row.get("source_table") or not row.get("imei_field"):
        raise HTTPException(400, "carrier_id, source_table, imei_field are required")
    r = (client.schema("commcalc").table("payable_source_map")
         .upsert(row, on_conflict="org_id,carrier_id").execute())
    return {"saved": True, "row": (r.data or [None])[0]}


@router.delete("/source-maps/{map_id}")
def delete_source_map(map_id: str, org_id: str = ORG_ID):
    client = sb()
    client.schema("commcalc").table("payable_source_map").delete() \
        .eq("org_id", org_id).eq("id", map_id).execute()
    return {"deleted": True}


# ── per-tenant settings: the clock-in priority-ack gate + the priority window % ────────────────────
@router.get("/settings")
def get_settings(org_id: str = ORG_ID):
    client = sb()
    t = (client.schema("storeops").table("tenants")
         .select("priority_ack_enabled,priority_window_pct").eq("org_id", org_id).limit(1).execute().data) or []
    r = t[0] if t else {}
    return {"priority_ack_enabled": bool(r.get("priority_ack_enabled")),
            "priority_window_pct": r.get("priority_window_pct") if r.get("priority_window_pct") is not None else 25}


@router.put("/settings")
def put_settings(body: dict, org_id: str = ORG_ID):
    client = sb()
    upd = {}
    if "priority_ack_enabled" in body:
        upd["priority_ack_enabled"] = bool(body["priority_ack_enabled"])
    if "priority_window_pct" in body:
        try:
            upd["priority_window_pct"] = max(1, min(100, int(body["priority_window_pct"])))
        except Exception:
            pass
    if upd:
        client.schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    return {"saved": True, **upd}
