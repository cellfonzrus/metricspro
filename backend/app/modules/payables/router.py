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
DEV_DEPTS_EXACT = ["Android - XP", "IPHONE - XP", "TABLET - XP"]   # exact-case for server-side .in_()


def _is_device_line(dept, category):
    """A physical-phone sale line. The custom 'for Metrics pro' report marks it category='CellPhone'
    (department is blank on that feed); the older format used the 'Android/IPHONE/TABLET - XP' depts.
    Accept EITHER so the forecast counts device units regardless of which report format is flowing."""
    return (str(category or "").strip().lower() == "cellphone") or ((dept or "").strip().lower() in DEV_DEPTS)


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
def forecast(lookback: int = 7, horizon: int = 7, days: int = 0, store: str = "", carrier: str = "", org_id: str = ORG_ID):
    """Phones-only ordering forecast, SEPARATE PER CARRIER. Velocity = units sold in the last `lookback`
    days; projected demand = velocity × the next `horizon` days; recommend_order = max(0, projected −
    on_hand). Both windows are user-defined. Carrier + canonical model come from the PHONE MAPPING table
    (commcalc.device_model_alias, mig 096); an unmapped model falls back to its source carrier + raw name
    and is flagged so it can be curated (the onboarding to-do). Sales sources: raw_sales (Boost) +
    raw_ma_commission (Total). On-hand: asset_ledger unsold (Boost consignment)."""
    client = sb()
    if days:                                     # back-compat: a single `days` sets both windows
        lookback = horizon = days
    lookback = max(1, min(lookback, 365))
    horizon = max(1, min(horizon, 365))
    pmap = engine.load_phone_map(client, org_id)
    carriers = client.schema("commcalc").table("carrier").select("id,name,code").eq("org_id", org_id).execute().data or []
    cby = {c["id"]: c.get("name") for c in carriers}

    def _find(sub):
        for c in carriers:
            if sub in (c.get("name") or "").lower() or sub in (c.get("code") or "").lower():
                return c["id"], c.get("name")
        return None, sub.capitalize()
    boost_id, boost_name = _find("boost")
    total_id, total_name = _find("total")
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=lookback)).isoformat()

    agg = {}   # (carrier, store, canonical) -> row

    def _bucket(raw, default_cid, default_cname, st):
        base = str(raw or "").split(" - ")[0].strip()
        m = pmap.get(base.lower())
        canonical = (m["canonical"] if m and m.get("canonical") else base) or base
        cid = (m["carrier_id"] if m and m.get("carrier_id") else default_cid)
        cname = (cby.get(cid) or default_cname) if cid else default_cname
        key = (cname, st, canonical)
        return agg.setdefault(key, {"carrier": cname, "store": st, "device_model": canonical,
                                    "units": 0, "on_hand": 0, "mapped": bool(m)})

    # velocity — Boost device sales (raw_sales device lines)
    def sq():
        q = (client.schema("commcalc").table("raw_sales")
             .select("store,product_desc,department,category,voided,trans_type,salesperson")
             .eq("org_id", org_id).gte("trans_date", cutoff))
        return q.eq("store", store) if store else q
    for r in _fetch_all(sq):
        if (r.get("voided") or "").upper() == "YES" or (r.get("trans_type") or "") == "Return":
            continue
        if not _is_device_line(r.get("department"), r.get("category")):
            continue
        sp = (r.get("salesperson") or "").strip().lower()
        if not sp or sp == "admin":
            continue
        _bucket(r.get("product_desc"), boost_id, boost_name, r.get("store"))["units"] += 1

    # velocity — Total device sales (raw_ma_commission), if the table has rows.
    # A marketplace/MA activation is booked against the DEALER account and carries no store, so it used
    # to bucket under store=None and the whole Total forecast read "—" in the Store column — you could
    # not tell which store the recommended order was FOR (owner report 2026-08-10). Resolve it through
    # the device: IMEI -> the POS line that sold it -> that line's store. Unresolved stays None and
    # renders as "(unassigned)" rather than being attached to a store it wasn't ordered for.
    ma_store = {}
    try:
        for r in _fetch_all(lambda: client.schema("commcalc").table("raw_sales")
                            .select("serial_1,store").eq("org_id", org_id).gte("trans_date", cutoff)):
            sn = str(r.get("serial_1") or "").strip()
            st = str(r.get("store") or "").strip()
            if sn and st:
                ma_store.setdefault(sn, st)
    except Exception:
        pass
    try:
        for r in _fetch_all(lambda: client.schema("commcalc").table("raw_ma_commission")
                            .select("sku,imei,tx_date").eq("org_id", org_id).gte("tx_date", cutoff)):
            st = ma_store.get(str(r.get("imei") or "").strip())
            if store and st != store:      # honor an explicit ?store= the same way the Boost leg does
                continue
            _bucket(r.get("sku"), total_id, total_name, st)["units"] += 1
    except Exception:
        pass

    # on-hand — asset_ledger unsold On-Inventory (Boost consignment; the only per-model on-hand we have)
    def oq():
        q = (client.schema("commcalc").table("asset_ledger")
             .select("store,device_model,date_sold,category").eq("org_id", org_id))
        return q.eq("store", store) if store else q
    for r in _fetch_all(oq):
        if r.get("date_sold") or "on inventory" not in (r.get("category") or "").lower():
            continue
        _bucket(r.get("device_model"), boost_id, boost_name, r.get("store"))["on_hand"] += 1

    out = []
    for e in agg.values():
        rate = e["units"] / lookback
        e["avg_daily_velocity"] = round(rate, 2)
        e["projected_demand"] = int(round(rate * horizon))
        e["recommend_order"] = max(0, e["projected_demand"] - e["on_hand"])
        out.append(e)
    if carrier:
        out = [r for r in out if (r["carrier"] or "").lower() == carrier.lower()]
    out.sort(key=lambda x: (x["recommend_order"], x["units"]), reverse=True)
    return {"lookback": lookback, "horizon": horizon, "store": store or None,
            "carriers": sorted({r["carrier"] for r in out if r["carrier"]}),
            "unmapped": sum(1 for r in out if not r["mapped"]),
            # How many rows still have no store, so the page can SAY "n rows unassigned" instead of
            # showing a silent '—' the reader has to guess at.
            "unassigned_store": sum(1 for r in out if not r.get("store")),
            "rows": out, "total": len(out)}


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


_VENDOR_FEED_TABLE = "raw_ma_daily_tx"


def _owed_from_vendor_feed(client, org_id, start, end):
    """Daily owed straight from the PROCESSOR'S OWN feed — `raw_ma_daily_tx.due_date`.

    OWNER 2026-08-10: "if we have the actual due date build that and ship it."  We do. This is the
    fallback for a tenant whose per-IMEI ledger carries no amounts, which is every Total/MA tenant:
    their source map has no owed_field because the MA reports record what was ACTIVATED, never what was
    INVOICED, so Daily Owed grouped nothing at all.

    The earlier plan was to ESTIMATE the due date as ship + 20 days off the handset fulfillment report.
    That is not needed and would have been less accurate: the daily-tx feed already carries the
    vendor's own `due_date` on every row (all 45,525 luxelink rows, 2026-02-02..2026-09-24). A positive
    `retail_cost` is money the DEALER OWES (negative = paid to the dealer — the same sign convention
    the canonical commission ledger books from), so the owed side is the positive rows.

    GRAIN, stated out loud: this is per ORDER LINE, not per device. The feed carries no IMEI, and
    neither does the fulfillment report — there is no device identity on this path at all, which is
    exactly why the per-IMEI ledger cannot be priced from it. So these rows answer "how much is due on
    what date", not "which handset". They are NOT mixed into the ledger.

    Returns (rows, meta). Never raises — a tenant without the table just gets no fallback."""
    rows, page, start_at = [], 1000, 0
    by_date, entities = {}, set()
    try:
        while True:
            q = (client.schema("commcalc").table(_VENDOR_FEED_TABLE)
                 .select("due_date,retail_cost,account_name")
                 .eq("org_id", org_id).not_.is_("due_date", "null"))
            if start:
                q = q.gte("due_date", start)
            if end:
                q = q.lte("due_date", end)
            chunk = q.range(start_at, start_at + page - 1).execute().data or []
            for r in chunk:
                amt = r.get("retail_cost")
                try:
                    amt = float(amt)
                except (TypeError, ValueError):
                    continue
                if amt <= 0:                     # negative / zero = paid TO the dealer, not owed BY it
                    continue
                d = str(r.get("due_date") or "")[:10]
                if not d:
                    continue
                e = by_date.setdefault(d, {"due_date": d, "count": 0, "owed": 0.0})
                e["count"] += 1
                e["owed"] += amt
                nm = (r.get("account_name") or "").strip()
                if nm:
                    entities.add(nm)
            if len(chunk) < page:
                break
            start_at += page
    except Exception as e:
        print(f"WARN owed-by-date vendor feed unavailable: {e}")
        return [], {}
    rows = [dict(v, owed=round(v["owed"], 2)) for v in by_date.values()]
    rows.sort(key=lambda x: x["due_date"])
    return rows, {"entities": sorted(entities)}


@router.get("/owed-by-date")
def owed_by_date(start: str = "", end: str = "", store: str = "", org_id: str = ORG_ID):
    """Consolidated daily owed: net_owed grouped by due_date.

    Two possible sources, and the response always names which one answered:
      • `device_ledger` — the per-IMEI payable ledger (Boost/VIP: owed_to_vip + the VIP invoice dates);
      • `vendor_feed`  — the processor's own `raw_ma_daily_tx.due_date` + amounts, used when the ledger
        carries no amounts at all (every Total/MA tenant). Per ORDER LINE, not per device.
    """
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
    if out:
        return {"rows": out, "total_owed": round(sum(x["owed"] for x in out), 2),
                "source": "device_ledger", "grain": "device"}

    # The ledger produced nothing — which for a Total/MA tenant is not "you owe nothing", it is "no
    # device carries an amount". Fall back to the processor's own dated amounts and SAY so, including
    # the grain change, rather than showing an empty table.
    vrows, vmeta = _owed_from_vendor_feed(client, org_id, start, end)
    if not vrows:
        return {"rows": [], "total_owed": 0.0, "source": None, "grain": None}
    note = ("Dated from the processor's own feed (MA Daily Tx `due_date`), not from the per-IMEI "
            "payable ledger — this tenant's carrier source map carries no owed field, because the MA "
            "reports record what was activated, never what was invoiced. These are ORDER LINES, not "
            "devices, so there is no IMEI to drill into"
            + (" · entities: " + ", ".join(vmeta["entities"]) if vmeta.get("entities") else "") + ".")
    if store:
        note += (" The store filter does not apply here: the feed carries no store — an order is placed "
                 "against the dealer account.")
    return {"rows": vrows, "total_owed": round(sum(x["owed"] for x in vrows), 2),
            "source": "vendor_feed", "grain": "order_line", "note": note,
            "entities": vmeta.get("entities") or []}


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


# ── phone mapping table (raw model → canonical + carrier) — the forecast alignment + onboarding to-do ──
def _carriers(client, org_id):
    return client.schema("commcalc").table("carrier").select("id,name,code").eq("org_id", org_id).execute().data or []


def _find_carrier(carriers, sub):
    for c in carriers:
        if sub in (c.get("name") or "").lower() or sub in (c.get("code") or "").lower():
            return c["id"], c.get("name")
    return None, sub.capitalize()


@router.get("/phone-map")
def list_phone_map(org_id: str = ORG_ID):
    client = sb()
    rows = (client.schema("commcalc").table("device_model_alias").select("*")
            .eq("org_id", org_id).order("raw_model").execute().data) or []
    carriers = _carriers(client, org_id)
    cby = {c["id"]: c.get("name") for c in carriers}
    for r in rows:
        r["carrier_name"] = cby.get(r.get("carrier_id"))
    return {"rows": rows, "carriers": carriers}


@router.get("/phone-map/candidates")
def phone_map_candidates(limit: int = 300, days: int = 180, org_id: str = ORG_ID):
    """Distinct raw model strings seen in sales + inventory that are NOT yet in the phone map — the
    onboarding to-do list. Each carries a suggested side + carrier and a frequency (map the big ones first)."""
    client = sb()
    mapped = {(r.get("raw_model") or "").strip().lower()
              for r in (client.schema("commcalc").table("device_model_alias").select("raw_model")
                        .eq("org_id", org_id).execute().data or [])}
    carriers = _carriers(client, org_id)
    boost_id, boost_name = _find_carrier(carriers, "boost")
    total_id, total_name = _find_carrier(carriers, "total")
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=max(1, days))).isoformat()
    cand = {}

    def _add(raw, side, cid, cname):
        base = str(raw or "").split(" - ")[0].strip()
        if not base or base.lower() in mapped:
            return
        e = cand.setdefault(base.lower(), {"raw_model": base, "side": side, "carrier_id": cid,
                                           "carrier": cname, "count": 0})
        e["count"] += 1

    for r in _fetch_all(lambda: client.schema("commcalc").table("raw_sales").select("product_desc")
                        .eq("org_id", org_id).eq("category", "CellPhone").gte("trans_date", cutoff)):
        _add(r.get("product_desc"), "sales", boost_id, boost_name)          # custom report phone lines
    for r in _fetch_all(lambda: client.schema("commcalc").table("raw_sales").select("product_desc")
                        .eq("org_id", org_id).in_("department", DEV_DEPTS_EXACT).gte("trans_date", cutoff)):
        _add(r.get("product_desc"), "sales", boost_id, boost_name)          # legacy-format phone lines
    try:
        for r in _fetch_all(lambda: client.schema("commcalc").table("raw_ma_commission").select("sku")
                            .eq("org_id", org_id).gte("tx_date", cutoff)):
            _add(r.get("sku"), "sales", total_id, total_name)
    except Exception:
        pass
    for r in _fetch_all(lambda: client.schema("commcalc").table("asset_ledger").select("device_model")
                        .eq("org_id", org_id).ilike("category", "%On Inventory%").is_("date_sold", "null")):
        _add(r.get("device_model"), "inventory", boost_id, boost_name)

    out = sorted(cand.values(), key=lambda x: x["count"], reverse=True)[:limit]
    return {"rows": out, "total_unmapped": len(cand), "carriers": carriers}


@router.post("/phone-map")
def upsert_phone_map(body: dict, org_id: str = ORG_ID):
    client = sb()
    raw = (body.get("raw_model") or "").strip()
    if not raw:
        raise HTTPException(400, "raw_model required")
    row = {"org_id": org_id, "raw_model": raw,
           "canonical_model": (body.get("canonical_model") or raw).strip(),
           "carrier_id": body.get("carrier_id") or None, "side": body.get("side"), "source": "manual"}
    r = (client.schema("commcalc").table("device_model_alias")
         .upsert(row, on_conflict="org_id,raw_model").execute())
    return {"saved": True, "row": (r.data or [None])[0]}


@router.delete("/phone-map/{map_id}")
def delete_phone_map(map_id: str, org_id: str = ORG_ID):
    client = sb()
    client.schema("commcalc").table("device_model_alias").delete() \
        .eq("org_id", org_id).eq("id", map_id).execute()
    return {"deleted": True}
