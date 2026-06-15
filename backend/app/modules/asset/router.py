from fastapi import APIRouter, UploadFile, File, HTTPException
from app.core.database import get_supabase

router = APIRouter()
ORG_ID = "00000000-0000-0000-0000-000000000001"

def sb():
    return get_supabase()

@router.post("/upload")
async def upload_asset_ledger(file: UploadFile = File(...), org_id: str = ORG_ID):
    """Upload Asset_Lending.xlsx — clears existing rows for org then re-inserts."""
    from app.modules.asset.asset_parser import parse_asset_ledger
    file_bytes = await file.read()
    try:
        rows = parse_asset_ledger(file_bytes, org_id)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=400, detail=f"Parse error: {e}\n{traceback.format_exc()}")

    if not rows:
        raise HTTPException(status_code=400, detail="No rows parsed from file")

    client = sb()
    # Clear existing
    client.schema("commcalc").table("asset_ledger").delete().eq("org_id", org_id).execute()

    # Insert in chunks of 500
    for i in range(0, len(rows), 500):
        chunk = rows[i:i+500]
        client.schema("commcalc").table("asset_ledger").insert(chunk).execute()

    # Backfill market from store_mapping + manual corrections (file has no market)
    _backfill_market(client, org_id)

    # Backfill customer selling price from sales transactions (matched by IMEI)
    try:
        _backfill_selling_price(client, org_id)
    except Exception as _e:
        print(f"selling-price backfill failed (run 009_asset_selling_price.sql?): {_e}")

    # Auto-sync appeal rows into the Flags page (critical Boost non-payment)
    try:
        _sync_appeal_flags(client, org_id)
    except Exception as _e:
        print(f"appeal flag sync failed: {_e}")
    try:
        _sync_rma_flags(client, org_id)
    except Exception as _e:
        print(f"rma flag sync failed: {_e}")
    # Undercharge flags: cost (owed_to_vip) > reimbursement + selling_price
    try:
        _sync_undercharge_flags(client, org_id)
    except Exception as _e:
        print(f"undercharge flag sync failed: {_e}")

    return {"status": "ok", "rows_imported": len(rows)}


# Stores whose asset address differs from store_mapping, plus the two not in it.
MARKET_OVERRIDES = {
    "1 S 60th St": "PA",
    "116-36 Springfield Blvd": "LI",
    "1598 Mt Ephraim Ave": "PA",
    "1710 W 4Th Street": "PA",
    "2778 Mount Ephraim Ave": "PA",
    "2778 Mt Ephraim Ave": "PA",
    "4712 White Plains Road": "NYC",
    "5135 Bergenline Ave": "NJ",
    "5619 N Broad St": "PA",
    "5619 N Broad Street": "PA",
    "586 Main Ave": "NJ",
    "6507 Castor Ave": "PA",
    "652 Communipaw Ave": "NJ",
}


def _backfill_market(client, org_id: str):
    """Populate asset_ledger.market: exact match to store_mapping, then overrides."""
    # Build address(lower) -> market map from store_mapping
    sm = client.schema("commcalc").table("store_mapping") \
        .select("store_address,market").execute().data or []
    addr_to_market = {}
    for m in sm:
        a = (m.get("store_address") or "").strip().lower()
        if a and m.get("market"):
            addr_to_market[a] = m["market"]

    # Distinct asset stores
    stores = set()
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        chunk = client.schema("commcalc").table("asset_ledger") \
            .select("store").eq("org_id", org_id) \
            .range(start, start + PAGE - 1).execute().data or []
        for r in chunk:
            if r.get("store"):
                stores.add(r["store"])
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 100:
            break

    # Resolve each store's market (exact match first, then overrides) and update
    for store in stores:
        market = addr_to_market.get(store.strip().lower()) or MARKET_OVERRIDES.get(store)
        if market:
            client.schema("commcalc").table("asset_ledger") \
                .update({"market": market}).eq("org_id", org_id).eq("store", store).execute()


@router.get("/summary")
async def get_asset_summary(org_id: str = ORG_ID, store: str = "", market: str = "",
                            date_from: str = "", date_to: str = ""):
    """High-level totals + breakdowns for the summary dashboard.
    Optional dashboard filters: store, market, acquired_date range (date_from/date_to)."""
    client = sb()
    q = client.schema("commcalc").table("asset_ledger") \
        .select("status,category,owed_to_vip,on_inventory,reimbursement,commissions,total_owed,total_reimbursed,store,market,acquired_date") \
        .eq("org_id", org_id)
    if store:
        q = q.eq("store", store)
    if market:
        q = q.eq("market", market)
    if date_from:
        q = q.gte("acquired_date", date_from)
    if date_to:
        q = q.lte("acquired_date", date_to)
    resp = q.execute()

    rows = resp.data or []
    if not rows:
        return {"loaded": False}

    total_rows = len(rows)
    total_fees = sum(float(r.get("commissions") or 0) for r in rows)
    # open balance = owed_to_vip for Open status only
    total_open = sum(float(r.get("owed_to_vip") or 0) for r in rows if (r.get("status") or "") == "Open")
    # reimbursed = sum of reimbursement col (actual Boost payments received)
    total_reimbursed = sum(float(r.get("reimbursement") or 0) for r in rows)
    # all-time owed = all owed_to_vip
    total_owed = sum(float(r.get("owed_to_vip") or 0) for r in rows)
    # on inventory = owed_to_vip for On Inventory category
    on_inventory = sum(float(r.get("owed_to_vip") or 0) for r in rows if "On Inventory" in (r.get("category") or ""))

    # By status
    by_status: dict = {}
    for r in rows:
        s = r.get("status") or "Unknown"
        if s not in by_status:
            by_status[s] = {"count": 0, "owed": 0, "reimbursed": 0, "fees": 0}
        by_status[s]["count"] += 1
        by_status[s]["owed"] += float(r.get("owed_to_vip") or 0)
        by_status[s]["reimbursed"] += float(r.get("reimbursement") or 0)
        by_status[s]["fees"] += float(r.get("commissions") or 0)

    # By category
    by_category: dict = {}
    for r in rows:
        c = r.get("category") or "Unknown"
        if c not in by_category:
            by_category[c] = {"count": 0, "owed": 0, "fees": 0}
        by_category[c]["count"] += 1
        by_category[c]["owed"] += float(r.get("owed_to_vip") or 0)
        by_category[c]["fees"] += float(r.get("commissions") or 0)

    return {
        "loaded": True,
        "total_rows": total_rows,
        "total_fees": round(total_fees, 2),
        "total_open_balance": round(total_open, 2),
        "total_reimbursed": round(total_reimbursed, 2),
        "total_owed_alltime": round(total_owed, 2),
        "on_inventory": round(on_inventory, 2),
        "by_status": by_status,
        "by_category": by_category,
    }


@router.get("/category-detail")
async def get_category_detail(
    category: str,
    org_id: str = ORG_ID,
    limit: int = 500,
    offset: int = 0,
    store: str = "",
    market: str = "",
    date_from: str = "",
    date_to: str = "",
):
    """Drill-down for one category: status breakdown (all rows) + paginated device rows.
    Honors the dashboard filters: store, market, acquired_date range."""
    client = sb()

    def _af(q):
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        if date_from:
            q = q.gte("acquired_date", date_from)
        if date_to:
            q = q.lte("acquired_date", date_to)
        return q

    # Pull every row in this category for an accurate status tally.
    # Select only the light columns needed for the breakdown.
    tally_rows = []
    page = 0
    PAGE = 1000
    while True:
        start = page * PAGE
        resp = _af(client.schema("commcalc").table("asset_ledger") \
            .select("status,owed_to_vip,reimbursement,commissions") \
            .eq("org_id", org_id).eq("category", category)) \
            .range(start, start + PAGE - 1).execute()
        chunk = resp.data or []
        tally_rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 100:  # hard safety stop (100k rows)
            break

    by_status: dict = {}
    for r in tally_rows:
        s = r.get("status") or "Unknown"
        if s not in by_status:
            by_status[s] = {"count": 0, "owed": 0, "reimbursed": 0, "fees": 0}
        by_status[s]["count"] += 1
        by_status[s]["owed"] += float(r.get("owed_to_vip") or 0)
        by_status[s]["reimbursed"] += float(r.get("reimbursement") or 0)
        by_status[s]["fees"] += float(r.get("commissions") or 0)
    for s in by_status:
        by_status[s]["owed"] = round(by_status[s]["owed"], 2)
        by_status[s]["reimbursed"] = round(by_status[s]["reimbursed"], 2)
        by_status[s]["fees"] = round(by_status[s]["fees"], 2)

    # Paginated device rows for the table.
    rows_resp = _af(client.schema("commcalc").table("asset_ledger") \
        .select("id,store,esn_imei,phone_number,device_model,contract_type,status,date_sold,sfid,owed_to_vip,reimbursement,commissions,selling_price,notes") \
        .eq("org_id", org_id).eq("category", category)) \
        .order("date_sold", desc=True).range(offset, offset + limit - 1).execute()

    rows = _attach_vip_invoices(client, org_id, rows_resp.data or [])

    return {
        "category": category,
        "total_in_category": len(tally_rows),
        "by_status": by_status,
        "rows": rows,
        "offset": offset,
        "limit": limit,
    }


@router.get("/filter-options")
async def get_filter_options(org_id: str = ORG_ID):
    """Distinct stores + markets for the report dropdowns."""
    client = sb()
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        chunk = client.schema("commcalc").table("asset_ledger") \
            .select("store,market").eq("org_id", org_id) \
            .range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 100:
            break
    markets = set()
    store_to_market = {}
    for r in rows:
        if r.get("market"):
            markets.add(r["market"])
        if r.get("store"):
            store_to_market[r["store"]] = r.get("market")
    stores = [{"store": k, "market": v} for k, v in store_to_market.items()]
    stores.sort(key=lambda x: x["store"])
    return {"markets": sorted(markets), "stores": stores}


@router.get("/owed-weekly")
async def get_owed_weekly(
    thursday: str,   # NOTE: this is the billing FRIDAY date (YYYY-MM-DD), matched against
                     # asset_ledger.billing_friday. The param/field is historically named
                     # `thursday`; kept for backward-compat with stored notify subscriptions.
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    weeks_ahead: int = 8,
    limit: int = 200,
    offset: int = 0,
):
    """VIP weekly collection report for a chosen billing Friday, plus upcoming forecast.
    VIP bills on Friday; `thursday` carries the Friday date (legacy name)."""
    from datetime import datetime, timedelta
    client = sb()

    def base(select_cols):
        q = client.schema("commcalc").table("asset_ledger").select(select_cols).eq("org_id", org_id)
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        return q

    def fetch_all(select_cols, apply_filter):
        out = []
        page = 0; PAGE = 1000
        while True:
            start = page * PAGE
            q = apply_filter(base(select_cols)).range(start, start + PAGE - 1)
            chunk = q.execute().data or []
            out.extend(chunk)
            if len(chunk) < PAGE:
                break
            page += 1
            if page > 50:
                break
        return out

    # Devices billing on the selected Thursday
    due_rows = fetch_all(
        "store,market,bill_path,owed_to_vip",
        lambda q: q.eq("billing_friday", thursday),
    )

    sold_c = sold_o = aging_c = aging_o = 0.0
    store_map = {}
    for r in due_rows:
        o = float(r.get("owed_to_vip") or 0)
        is_aging = r.get("bill_path") == "aging"
        if is_aging:
            aging_c += 1; aging_o += o
        else:
            sold_c += 1; sold_o += o
        s = r.get("store") or "\u2014"
        if s not in store_map:
            store_map[s] = {"store": s, "market": r.get("market"),
                            "sold_count": 0, "sold_owed": 0.0,
                            "aging_count": 0, "aging_owed": 0.0}
        if is_aging:
            store_map[s]["aging_count"] += 1; store_map[s]["aging_owed"] += o
        else:
            store_map[s]["sold_count"] += 1; store_map[s]["sold_owed"] += o

    by_store = []
    for s in store_map.values():
        s["sold_owed"] = round(s["sold_owed"], 2)
        s["aging_owed"] = round(s["aging_owed"], 2)
        s["total_owed"] = round(s["sold_owed"] + s["aging_owed"], 2)
        by_store.append(s)
    by_store.sort(key=lambda x: x["total_owed"], reverse=True)

    due_this_week = {
        "sold":  {"count": int(sold_c),  "owed": round(sold_o, 2)},
        "aging": {"count": int(aging_c), "owed": round(aging_o, 2)},
        "total": {"count": int(sold_c + aging_c), "owed": round(sold_o + aging_o, 2)},
    }

    # Upcoming Thursdays forecast
    th = datetime.strptime(thursday, "%Y-%m-%d").date()
    end = (th + timedelta(weeks=weeks_ahead)).isoformat()
    up_rows = fetch_all(
        "bill_path,owed_to_vip,billing_friday",
        lambda q: q.gt("billing_friday", thursday).lte("billing_friday", end),
    )
    up_map = {}
    for r in up_rows:
        t = r.get("billing_friday")
        if not t:
            continue
        if t not in up_map:
            up_map[t] = {"thursday": t, "sold_owed": 0.0, "aging_owed": 0.0, "count": 0}
        o = float(r.get("owed_to_vip") or 0)
        up_map[t]["count"] += 1
        if r.get("bill_path") == "aging":
            up_map[t]["aging_owed"] += o
        else:
            up_map[t]["sold_owed"] += o
    upcoming = []
    for t in sorted(up_map.keys()):
        e = up_map[t]
        e["sold_owed"] = round(e["sold_owed"], 2)
        e["aging_owed"] = round(e["aging_owed"], 2)
        e["total_owed"] = round(e["sold_owed"] + e["aging_owed"], 2)
        upcoming.append(e)

    # Device rows for the selected Thursday (paginated)
    rows_resp = base("id,store,market,esn_imei,phone_number,device_model,contract_type,status,date_sold,due_date,bill_path,owed_to_vip") \
        .eq("billing_friday", thursday).order("owed_to_vip", desc=True) \
        .range(offset, offset + limit - 1).execute()
    week_rows = _attach_vip_invoices(client, org_id, rows_resp.data or [])

    return {
        "thursday": thursday,
        "filters": {"store": store or None, "market": market or None},
        "due_this_week": due_this_week,
        "by_store": by_store,
        "upcoming": upcoming,
        "rows": week_rows,
        "total_due_rows": len(due_rows),
        "offset": offset,
        "limit": limit,
    }


@router.get("/aging")
async def get_aging(
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    month: int = None,
    year: int = None,
):
    """Unsold On-Inventory aging report. Buckets by days since acquired_date (as of today).
    Optional month/year narrows to devices ACQUIRED in that period."""
    from datetime import date
    client = sb()

    def _acq_in_period(r):
        if month is None and year is None:
            return True
        a = r.get("acquired_date")
        if not a:
            return False
        try:
            py, pm, _ = [int(x) for x in str(a)[:10].split("-")]
        except Exception:
            return False
        if year is not None and int(year) != py:
            return False
        if month is not None and int(month) != pm:
            return False
        return True

    def fetch(extra):
        out = []
        page = 0; PAGE = 1000
        while True:
            start = page * PAGE
            q = client.schema("commcalc").table("asset_ledger") \
                .select("id,store,market,esn_imei,phone_number,device_model,category,status,acquired_date,due_date,date_sold,owed_to_vip,reimbursement,selling_price") \
                .eq("org_id", org_id).is_("date_sold", "null").ilike("category", "%On Inventory%")
            if store:
                q = q.eq("store", store)
            if market:
                q = q.eq("market", market)
            q = extra(q).range(start, start + PAGE - 1)
            chunk = q.execute().data or []
            out.extend(chunk)
            if len(chunk) < PAGE:
                break
            page += 1
            if page > 50:
                break
        return out

    rows = [r for r in fetch(lambda q: q) if _acq_in_period(r)]
    today = date.today()

    def days_aged(r):
        a = r.get("acquired_date")
        if not a:
            return None
        try:
            y, m, d = map(int, str(a)[:10].split("-"))
            return (today - date(y, m, d)).days
        except Exception:
            return None

    buckets = {
        "under45": {"count": 0, "owed": 0.0, "rows": []},
        "warn":    {"count": 0, "owed": 0.0, "rows": []},   # 45-60
        "missed":  {"count": 0, "owed": 0.0, "rows": []},   # >60
    }
    zero_rows = []  # plain On Inventory, $0 owed

    for r in rows:
        owed = float(r.get("owed_to_vip") or 0)
        if owed <= 0:
            zero_rows.append(r)
            continue
        d = days_aged(r)
        r["days_aged"] = d
        if d is None:
            continue
        if d < 45:
            b = "under45"
        elif d <= 60:
            b = "warn"
        else:
            b = "missed"
        buckets[b]["count"] += 1
        buckets[b]["owed"] += owed
        buckets[b]["rows"].append(r)

    for b in buckets.values():
        b["owed"] = round(b["owed"], 2)
        b["rows"].sort(key=lambda x: (x.get("days_aged") or 0), reverse=True)

    # Attach VIP invoice # + date to every device row we return (in place).
    zero_returned = zero_rows[:500]
    _attach_vip_invoices(client, org_id,
                         buckets["under45"]["rows"] + buckets["warn"]["rows"]
                         + buckets["missed"]["rows"] + zero_returned)

    # data freshness: max FileDate from raw_row
    fd = None
    sample = client.schema("commcalc").table("asset_ledger") \
        .select("raw_row").eq("org_id", org_id).limit(1).execute().data or []
    if sample and sample[0].get("raw_row"):
        fd = sample[0]["raw_row"].get("FileDate")
        if fd:
            fd = str(fd)[:10]

    return {
        "today": today.isoformat(),
        "data_as_of": fd,
        "buckets": buckets,
        "zero_inventory": {"count": len(zero_rows), "rows": zero_returned},
        "totals": {
            "flagged_count": sum(b["count"] for b in buckets.values()),
            "flagged_owed": round(sum(b["owed"] for b in buckets.values()), 2),
        },
    }


@router.get("/on-inventory-by-store")
async def get_on_inventory_by_store(
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    month: int = None,
    year: int = None,
):
    """On-Inventory exposure rolled up per store: how many unsold devices each store holds
    and the $ owed to VIP, with the same aging buckets as the Inventory Aging report
    (<45 / 45-60 WARN / >60 MISSED, measured from acquired_date as of today). Optional
    month/year narrows to devices ACQUIRED in that period. Numbers reconcile with /aging."""
    from datetime import date
    client = sb()

    def _acq_in_period(r):
        if month is None and year is None:
            return True
        a = r.get("acquired_date")
        if not a:
            return False
        try:
            py, pm, _ = [int(x) for x in str(a)[:10].split("-")]
        except Exception:
            return False
        if year is not None and int(year) != py:
            return False
        if month is not None and int(month) != pm:
            return False
        return True

    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger") \
            .select("store,market,acquired_date,owed_to_vip") \
            .eq("org_id", org_id).is_("date_sold", "null").ilike("category", "%On Inventory%")
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 50:
            break
    rows = [r for r in rows if _acq_in_period(r)]
    today = date.today()

    def days_aged(a):
        if not a:
            return None
        try:
            y, m, d = map(int, str(a)[:10].split("-"))
            return (today - date(y, m, d)).days
        except Exception:
            return None

    def blank(s, mkt):
        return {"store": s, "market": mkt, "count": 0, "owed": 0.0,
                "under45_count": 0, "under45_owed": 0.0,
                "warn_count": 0, "warn_owed": 0.0,
                "missed_count": 0, "missed_owed": 0.0,
                "zero_count": 0}

    by_store: dict = {}
    for r in rows:
        s = r.get("store") or "(unknown)"
        row = by_store.setdefault(s, blank(s, r.get("market")))
        if not row["market"] and r.get("market"):
            row["market"] = r.get("market")
        owed = float(r.get("owed_to_vip") or 0)
        row["count"] += 1
        if owed <= 0:
            row["zero_count"] += 1
            continue
        row["owed"] += owed
        d = days_aged(r.get("acquired_date"))
        if d is None:
            continue
        if d < 45:
            bk = "under45"
        elif d <= 60:
            bk = "warn"
        else:
            bk = "missed"
        row[f"{bk}_count"] += 1
        row[f"{bk}_owed"] += owed

    stores = []
    for v in by_store.values():
        for k in ("owed", "under45_owed", "warn_owed", "missed_owed"):
            v[k] = round(v[k], 2)
        stores.append(v)
    stores.sort(key=lambda x: x["owed"], reverse=True)

    # data freshness: max FileDate from raw_row (same signal the Aging report uses)
    fd = None
    sample = client.schema("commcalc").table("asset_ledger") \
        .select("raw_row").eq("org_id", org_id).limit(1).execute().data or []
    if sample and sample[0].get("raw_row"):
        fd = sample[0]["raw_row"].get("FileDate")
        if fd:
            fd = str(fd)[:10]

    totals = {
        "store_count": len(stores),
        "device_count": sum(s["count"] for s in stores),
        "owed": round(sum(s["owed"] for s in stores), 2),
        "missed_owed": round(sum(s["missed_owed"] for s in stores), 2),
        "warn_owed": round(sum(s["warn_owed"] for s in stores), 2),
        "zero_count": sum(s["zero_count"] for s in stores),
    }
    return {"today": today.isoformat(), "data_as_of": fd, "stores": stores, "totals": totals}


# ---- Asset charge classification (single source of truth) ----
CHARGE_GROUPS = {
    "vip_fees":      ["PROCESSING FEE", "SHIPPING", "SIM KIT"],
    "stock_balance": ["Stock Balancing"],
    "appeals":       ["Appeal Denied. Details in Boost Appeals Status",
                      "Re-Escalation",
                      "Over 10 Days Missing Reimbursement (CheckElevate/Submit Appeal)",
                      "Missing 1st MRC",
                      "Failed Activation. Check Boost Payment Status"],
    "recon_oddity":  ["Phone Number Paid to Different ESN", "No Elevate Data. Received Commissions",
                      "Non-Promo Elevate Coupon", "Exchange/Return"],
}
GROUP_LABELS = {
    "vip_fees": "VIP Fees", "stock_balance": "Stock Balancing / Returns",
    "appeals": "Appeals & Denied Payments", "recon_oddity": "Reconciliation Oddities",
}

def _cat_to_group(cat: str):
    for g, cats in CHARGE_GROUPS.items():
        if cat in cats:
            return g
    return None


def _fetch_asset_rows(client, org_id, store="", market="", select="*"):
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger").select(select).eq("org_id", org_id)
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 60:
            break
    return rows


def _row_period_date(r):
    """Date that places a charge in a period: PAYG > date_sold > acquired."""
    return r.get("payg_date") or r.get("date_sold") or r.get("acquired_date")


def _in_period(r, month=None, year=None, week_friday=None):
    if week_friday:
        bf = r.get("billing_friday")
        return str(bf)[:10] == week_friday if bf else False
    if year is None and month is None:
        return True
    d = _row_period_date(r)
    if not d:
        return False
    try:
        py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
    except Exception:
        return False
    if year is not None and int(year) != py:
        return False
    if month is not None and int(month) != pm:
        return False
    return True


@router.get("/charges-summary")
async def get_charges_summary(org_id: str = ORG_ID, store: str = "", market: str = "", month: int = None, year: int = None, week_friday: str = ""):
    """Charge groups + Total Loss via Postgres aggregation (fast). Totals only — no row lists."""
    client = sb()
    params = {
        "p_org_id": org_id,
        "p_store": store or None,
        "p_market": market or None,
        "p_month": month,
        "p_year": year,
        "p_week_friday": week_friday or None,
    }
    agg = client.schema("commcalc").rpc("asset_charges_summary", params).execute().data or []

    groups = {}
    for gk in CHARGE_GROUPS:
        groups[gk] = {"key": gk, "label": GROUP_LABELS[gk],
                      "count": 0, "owed": 0.0, "by_category": {}, "by_store": {}}

    for row in agg:
        gk = _cat_to_group(row.get("category") or "")
        if not gk:
            continue
        cnt = int(row.get("cnt") or 0)
        owed = float(row.get("owed") or 0)
        G = groups[gk]
        G["count"] += cnt
        G["owed"] += owed
        c = row.get("category") or "Unknown"
        G["by_category"].setdefault(c, {"category": c, "count": 0, "owed": 0.0})
        G["by_category"][c]["count"] += cnt
        G["by_category"][c]["owed"] += owed
        s = row.get("store") or "—"
        G["by_store"].setdefault(s, {"store": s, "market": row.get("market"), "count": 0, "owed": 0.0})
        G["by_store"][s]["count"] += cnt
        G["by_store"][s]["owed"] += owed

    for G in groups.values():
        G["owed"] = round(G["owed"], 2)
        G["by_category"] = sorted(({**v, "owed": round(v["owed"], 2)} for v in G["by_category"].values()),
                                  key=lambda x: x["owed"], reverse=True)
        G["by_store"] = sorted(({**v, "owed": round(v["owed"], 2)} for v in G["by_store"].values()),
                               key=lambda x: x["owed"], reverse=True)

    # Total Loss = denied appeals owed + RMA net loss (unreimbursed full + shortfall)
    appeals_loss = round(sum(v["owed"] for v in groups["appeals"]["by_category"]), 2)
    rma_loss = 0.0
    for row in agg:
        if (row.get("category") or "") == "RMA":
            owed = float(row.get("owed") or 0)
            reimb = float(row.get("reimb") or 0)
            if reimb <= 0:
                rma_loss += owed
            elif reimb < owed - 0.01:
                rma_loss += (owed - reimb)
    rma_loss = round(rma_loss, 2)

    return {
        "groups": groups,
        "total_loss": {"total": round(appeals_loss + rma_loss, 2), "appeals": appeals_loss, "rma": rma_loss},
        "filters": {"store": store or None, "market": market or None, "month": month, "year": year, "week_friday": week_friday or None},
    }


def _epay_evidence(epay):
    """Compact one-line summary of a device's ePay Payment Detail lines, grouped by
    payment type. e.g. 'ePay paid $123.45 (MI $80.00; ATU $43.45)'. Empty list → ''."""
    if not epay:
        return ""
    sums = {}
    for p in epay:
        t = p.get("type") or "—"
        sums[t] = sums.get(t, 0.0) + float(p.get("amount") or 0)
    total = round(sum(sums.values()), 2)
    parts = [f"{t} ${a:,.2f}" for t, a in sorted(sums.items(), key=lambda x: -x[1])]
    tail = "; ".join(parts[:4]) + (f"; +{len(parts) - 4} more" if len(parts) > 4 else "")
    return f"ePay paid ${total:,.2f} ({tail})"


def _appeal_reason(r, epay=None, epay_loaded=False):
    """Human-readable reason an appeal row is a loss. There is no single denial-reason
    column, so we build it from the category plus the concrete raw_row signals when present
    (notably 'PN paid to ESN', i.e. the phone number's credit was paid against a DIFFERENT
    device), and — when the ePay Payment Detail Report is loaded — the actual payments Boost
    made for this device (joined by IMEI), which is the true per-appeal evidence."""
    cat = (r.get("category") or "").strip()
    raw = r.get("raw_row") or {}
    pn_esn = (raw.get("PN paid to ESN") or "").strip() if raw.get("PN paid to ESN") else ""
    reimb_pn = (raw.get("Reimbursement on PN") or "").strip() if raw.get("Reimbursement on PN") else ""
    pn_note = ""
    if pn_esn:
        pn_note = f"phone number's credit paid to a different ESN ({pn_esn})"
        if reimb_pn:
            pn_note += f", ${reimb_pn} reimbursed there"
    base = {
        "Re-Escalation": "Re-escalation submitted to Boost — awaiting decision",
        "Missing 1st MRC": "Missing 1st month recurring charge (1st MRC) — no Boost payment received",
        "Failed Activation. Check Boost Payment Status": "Failed activation — check Boost payment status",
        "Over 10 Days Missing Reimbursement (CheckElevate/Submit Appeal)":
            "Over 10 days missing reimbursement — check Elevate / submit appeal",
    }.get(cat)
    # Concrete ePay evidence (when the Payment Detail Report is loaded): what Boost actually
    # paid for this device, or that nothing was paid — the true per-appeal denial signal.
    ev = _epay_evidence(epay)
    if ev:
        epay_note = ev
    elif epay_loaded:
        epay_note = "ePay: no payment found for this device"
    else:
        epay_note = ""

    if cat.startswith("Appeal Denied"):
        reason = (f"Appeal denied — {pn_note}." if pn_note else "Appeal denied.")
        reason += f" {epay_note}." if epay_note else " See Boost Payment Detail Report (ePay)."
        return reason
    if base:
        parts = [base]
    else:
        parts = [cat or "—"]
    if pn_note:
        parts.append(pn_note)
    if epay_note:
        parts.append(epay_note)
    return " · ".join(parts)


@router.get("/charge-rows")
async def get_charge_rows(
    group: str,
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    month: int = None,
    year: int = None,
    week_friday: str = "",
    limit: int = 500,
    offset: int = 0,
):
    """Per-device line items for one charge group (appeals / vip_fees / stock_balance / recon_oddity).

    Returns IMEI/ESN, store, market, device and the period date so the charge-group
    report pages can show real line items (the /charges-summary endpoint is totals-only).
    Filtered by store / market / period; period filter mirrors _in_period().
    """
    cats = CHARGE_GROUPS.get(group)
    if not cats:
        raise HTTPException(status_code=400, detail=f"Unknown charge group '{group}'")
    client = sb()

    # Pull every row in this group's categories (bounded subset, not the whole ledger),
    # honoring store/market filters in the query; period is filtered in Python below.
    # For appeals we also need raw_row to derive the denial reason.
    sel = ("id,store,market,esn_imei,phone_number,device_model,category,status,"
           "date_sold,payg_date,acquired_date,billing_friday,owed_to_vip,reimbursement,commissions,selling_price,notes")
    if group == "appeals":
        sel += ",raw_row"
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger") \
            .select(sel) \
            .eq("org_id", org_id).in_("category", cats)
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 60:
            break

    # Period filter (same rules as the summary): PAYG > date_sold > acquired, or billing_friday for week.
    if week_friday or month is not None or year is not None:
        rows = [r for r in rows if _in_period(r, month=month, year=year, week_friday=week_friday or None)]

    # Attach the period date used for placement so the report can show it.
    for r in rows:
        r["period_date"] = _row_period_date(r)

    rows.sort(key=lambda x: float(x.get("owed_to_vip") or 0), reverse=True)

    total = len(rows)
    total_owed = round(sum(float(r.get("owed_to_vip") or 0) for r in rows), 2)
    page_rows = _attach_vip_invoices(client, org_id, rows[offset:offset + limit])

    # Appeals: derive the denial reason — joining the ePay Payment Detail Report (by IMEI)
    # for the true per-appeal evidence (what Boost actually paid) — then drop bulky raw_row.
    epay_loaded = False
    if group == "appeals":
        epay_loaded = _epay_has_data(client, org_id)
        epay_map = _epay_payments_map(client, org_id, [r.get("esn_imei") for r in page_rows])
        for r in page_rows:
            pays = epay_map.get(_norm_imei(r.get("esn_imei")), [])
            r["epay_payments"] = pays
            r["denial_reason"] = _appeal_reason(r, pays, epay_loaded)
            r.pop("raw_row", None)

    return {
        "group": group,
        "label": GROUP_LABELS.get(group, group),
        "rows": page_rows,
        "total": total,
        "total_owed": total_owed,
        "offset": offset,
        "limit": limit,
        "epay_loaded": epay_loaded,
        "filters": {"store": store or None, "market": market or None,
                    "month": month, "year": year, "week_friday": week_friday or None},
    }


def _sync_appeal_flags(client, org_id):
    """Write appeal-group asset rows into commcalc.flags (delete-first + insert, keyed on source)."""
    rows = _fetch_asset_rows(
        client, org_id, select="store,esn_imei,phone_number,device_model,category,owed_to_vip,payg_date,date_sold,acquired_date",
    )
    appeal_cats = set(CHARGE_GROUPS["appeals"])
    flags = []
    for r in rows:
        if (r.get("category") or "") not in appeal_cats:
            continue
        d = r.get("payg_date") or r.get("date_sold") or r.get("acquired_date")
        period = "Unknown"; pm = None; py = None
        if d:
            try:
                py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
                period = __import__("datetime").date(py, pm, 1).strftime("%B %Y")
            except Exception:
                pass
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "Asset Appeal / Denied Payment", "source": "asset_appeal",
            "severity": "critical", "store_address": r.get("store"),
            "imei": r.get("esn_imei"), "mdn": r.get("phone_number"),
            "amount": float(r.get("owed_to_vip") or 0),
            "phone_model": r.get("device_model"),
            "description": f"Boost {r.get('category')} — potential unpaid/denied amount",
        })

    # delete-first then plain insert (dedup pattern)
    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "asset_appeal").execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i+500]).execute()
    return len(flags)


@router.post("/sync-appeal-flags")
async def sync_appeal_flags(org_id: str = ORG_ID):
    """Manual refresh: rewrite appeal flags from current asset data."""
    client = sb()
    n = _sync_appeal_flags(client, org_id)
    return {"status": "ok", "appeal_flags_written": n}


def _classify_rma(r):
    """Return (bucket, owed, reimb) for an RMA row. bucket in full/short/none."""
    try: owed = float(r.get("owed_to_vip") or 0)
    except Exception: owed = 0.0
    try: reimb = float(r.get("reimbursement") or 0)
    except Exception: reimb = 0.0
    rd = r.get("reimbursement_date")
    has_date = rd not in (None, "", "nan", "NaT", "None")
    got = reimb > 0 or has_date
    if not got:
        return "none", owed, reimb
    if reimb < owed - 0.01:
        return "short", owed, reimb
    return "full", owed, reimb


@router.get("/rma")
async def get_rma(org_id: str = ORG_ID, store: str = "", market: str = "", month: int = None, year: int = None):
    """RMA reconciliation via Postgres aggregation. Buckets from per-device rows for accuracy.
    Optional month/year narrows to devices SOLD in that period (date_sold)."""
    client = sb()

    def _sold_in_period(r):
        if month is None and year is None:
            return True
        ds = r.get("date_sold")
        if not ds:
            return False
        try:
            py, pm, _ = [int(x) for x in str(ds)[:10].split("-")]
        except Exception:
            return False
        if year is not None and int(year) != py:
            return False
        if month is not None and int(month) != pm:
            return False
        return True
    # We still need per-device classification (short vs none vs full), so fetch only RMA rows.
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger") \
            .select("id,store,market,esn_imei,phone_number,device_model,category,status,date_sold,owed_to_vip,reimbursement,reimbursement_date,selling_price") \
            .eq("org_id", org_id).eq("category", "RMA")
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 10:
            break

    rows = [r for r in rows if _sold_in_period(r)]

    buckets = {k: {"count": 0, "owed": 0.0, "reimb": 0.0, "rows": []} for k in ("full", "short", "none")}
    for r in rows:
        b, owed, reimb = _classify_rma(r)
        r["_bucket"] = b
        r["_shortfall"] = round(owed - reimb, 2) if b in ("short", "none") else 0.0
        buckets[b]["count"] += 1
        buckets[b]["owed"] += owed
        buckets[b]["reimb"] += reimb
        buckets[b]["rows"].append(r)

    for b in buckets.values():
        b["owed"] = round(b["owed"], 2)
        b["reimb"] = round(b["reimb"], 2)
        b["rows"].sort(key=lambda x: float(x.get("owed_to_vip") or 0), reverse=True)

    net_loss = round(buckets["none"]["owed"] + (buckets["short"]["owed"] - buckets["short"]["reimb"]), 2)

    # Attach VIP invoice # + date to every device row we return (in place).
    _attach_vip_invoices(client, org_id,
                         buckets["full"]["rows"] + buckets["short"]["rows"] + buckets["none"]["rows"])

    return {
        "buckets": buckets,
        "net_loss": net_loss,
        "total_rma": len(rows),
        "filters": {"store": store or None, "market": market or None},
    }


def _sync_rma_flags(client, org_id):
    """Write RMA flags: not-reimbursed=critical, short=warning. Delete-first + insert."""
    rows = _fetch_asset_rows(
        client, org_id,
        select="store,esn_imei,phone_number,device_model,category,status,date_sold,owed_to_vip,reimbursement,reimbursement_date,payg_date,acquired_date",
    )
    flags = []
    for r in rows:
        if (r.get("category") or "") != "RMA":
            continue
        b, owed, reimb = _classify_rma(r)
        if b == "full":
            continue
        sev = "critical" if b == "none" else "warning"
        d = r.get("date_sold") or r.get("payg_date") or r.get("acquired_date")
        period = "Unknown"; pm = None; py = None
        if d:
            try:
                py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
                period = __import__("datetime").date(py, pm, 1).strftime("%B %Y")
            except Exception:
                pass
        shortfall = round(owed - reimb, 2)
        desc = ("RMA not reimbursed — full amount uncredited" if b == "none"
                else f"RMA short-paid — owed {owed}, reimbursed {reimb} (short {shortfall})")
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "RMA Reimbursement Gap", "source": "asset_rma",
            "severity": sev, "store_address": r.get("store"),
            "imei": r.get("esn_imei"), "mdn": r.get("phone_number"),
            "amount": shortfall, "phone_model": r.get("device_model"),
            "description": desc,
        })

    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "asset_rma").execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i+500]).execute()
    return len(flags)


@router.post("/sync-rma-flags")
async def sync_rma_flags(org_id: str = ORG_ID):
    """Manual refresh of RMA flags."""
    client = sb()
    n = _sync_rma_flags(client, org_id)
    return {"status": "ok", "rma_flags_written": n}


# ── Selling price (from sales) + undercharge flag ────────────────────────────
def _norm_imei(v):
    s = str(v or "").strip().upper()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _epay_has_data(client, org_id):
    """True iff the ePay Payment Detail Report has any rows for this org. Lets callers say
    'no payment found' (meaningful) vs stay silent (table simply not loaded)."""
    try:
        d = client.schema("commcalc").table("raw_payment_detail") \
            .select("id").eq("org_id", org_id).limit(1).execute().data or []
        return bool(d)
    except Exception:
        return False


def _epay_payments_map(client, org_id, imeis):
    """For a bounded page of asset IMEIs (asset_ledger.esn_imei), the ePay payments Boost
    made for that device, from commcalc.raw_payment_detail joined on `imei` (across ALL
    periods so the device's full payment history is captured). Returns {norm_imei: [
    {type, amount, date, period}, ...]} sorted by date. Mirrors _vip_invoice_map: queries
    raw + normalized + '.0' variants via chunked .in_() so the page stays bounded and fast."""
    keys = {_norm_imei(i) for i in imeis if i}
    if not keys:
        return {}
    candidates = set()
    for i in imeis:
        if not i:
            continue
        candidates.add(str(i).strip())   # raw
        n = _norm_imei(i)
        candidates.add(n)                # normalized
        candidates.add(n + ".0")         # in case ePay stored a trailing .0
    candidates.discard("")
    cand = list(candidates)
    out = {}
    for j in range(0, len(cand), 200):
        chunk = client.schema("commcalc").table("raw_payment_detail") \
            .select("imei,payment_type,amount,payment_date,period") \
            .eq("org_id", org_id).in_("imei", cand[j:j + 200]).execute().data or []
        for r in chunk:
            k = _norm_imei(r.get("imei"))
            if k not in keys:
                continue
            out.setdefault(k, []).append({
                "type": (r.get("payment_type") or "").strip() or "—",
                "amount": round(float(r.get("amount") or 0), 2),
                "date": (str(r.get("payment_date"))[:10] if r.get("payment_date") else None),
                "period": r.get("period"),
            })
    for k in out:
        out[k].sort(key=lambda p: (p["date"] or ""))
    return out


def _vip_invoice_map(client, org_id, imeis):
    """For a bounded page of asset IMEIs (asset_ledger.esn_imei), the VIP invoice (# + date)
    the device appears on, from commcalc.vip_invoice_devices. The asset "ESN/IMEI" column
    is what VIP stores as the device SERIAL (verified: ~99.6% of asset IMEIs match
    vip_invoice_devices.serial; the VIP `imei` column is a different identifier and matches
    almost nothing), so we join on `serial`. Keyed by normalized value. When a device is on
    more than one invoice, keeps the earliest (the original device-purchase invoice).

    Queries by raw + normalized variants via .in_() (the page is small, so this is bounded
    and fast — no full 46k-row device scan). Mirrors _imei_salesperson_map."""
    keys = {_norm_imei(i) for i in imeis if i}
    if not keys:
        return {}
    candidates = set()
    for i in imeis:
        if not i:
            continue
        candidates.add(str(i).strip())   # raw
        n = _norm_imei(i)
        candidates.add(n)                # normalized (trimmed, upper, .0 stripped)
        candidates.add(n + ".0")         # in case VIP stored the value with a trailing .0
    candidates.discard("")
    cand = list(candidates)
    out = {}  # norm_imei -> (created_on_str, invoice_number)
    for j in range(0, len(cand), 200):  # chunk .in_() to keep request URLs sane
        chunk = client.schema("commcalc").table("vip_invoice_devices") \
            .select("serial,invoice_number,created_on") \
            .eq("org_id", org_id).in_("serial", cand[j:j + 200]).execute().data or []
        for r in chunk:
            k = _norm_imei(r.get("serial"))
            if k not in keys:
                continue
            d = str(r.get("created_on") or "")
            prev = out.get(k)
            # keep the earliest invoice with a date; fall back to filling a missing date
            if prev is None or (d and (not prev[0] or d < prev[0])):
                out[k] = (d, r.get("invoice_number"))
    return {
        k: {"vip_invoice_number": v[1], "vip_invoice_date": (v[0][:10] if v[0] else None)}
        for k, v in out.items()
    }


def _attach_vip_invoices(client, org_id, rows):
    """Decorate asset rows in place with vip_invoice_number / vip_invoice_date (None if no
    matching VIP invoice). `rows` must carry esn_imei. Safe on empty / no-overlap."""
    if not rows:
        return rows
    vip_map = _vip_invoice_map(client, org_id, [r.get("esn_imei") for r in rows])
    for r in rows:
        v = vip_map.get(_norm_imei(r.get("esn_imei")))
        r["vip_invoice_number"] = v["vip_invoice_number"] if v else None
        r["vip_invoice_date"] = v["vip_invoice_date"] if v else None
    return rows


def _backfill_selling_price(client, org_id):
    """Set asset_ledger.selling_price from raw_sales (device-line Ext Price by IMEI),
    via the Postgres RPC (one UPDATE...FROM join — fast). Returns rows updated."""
    res = client.schema("commcalc").rpc(
        "backfill_asset_selling_price", {"p_org_id": org_id}).execute()
    return res.data if isinstance(res.data, int) else (res.data or 0)


def _imei_salesperson_map(client, org_id, imeis):
    """For the (few) flagged IMEIs, the rep on the priciest matching sales line."""
    want = {_norm_imei(i) for i in imeis if i}
    if not want:
        return {}
    out = {}  # imei_key -> (price, salesperson)
    page = 0
    PAGE = 1000
    while True:
        start = page * PAGE
        chunk = client.schema("commcalc").table("raw_sales") \
            .select("serial_1,ext_price,salesperson,voided,trans_type") \
            .eq("org_id", org_id).range(start, start + PAGE - 1).execute().data or []
        for r in chunk:
            if str(r.get("voided") or "").upper() == "YES" or str(r.get("trans_type") or "") == "Return":
                continue
            k = _norm_imei(r.get("serial_1"))
            if k not in want:
                continue
            try:
                p = float(r.get("ext_price") or 0)
            except Exception:
                p = 0.0
            if k not in out or p > out[k][0]:
                out[k] = (p, r.get("salesperson"))
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 200:
            break
    return {k: v[1] for k, v in out.items()}


def _sync_undercharge_flags(client, org_id):
    """Flag sold devices where cost (owed_to_vip) > reimbursement + selling_price.
    Only devices that were actually sold (have a selling_price match). Delete-first + insert."""
    rows = _fetch_asset_rows(
        client, org_id,
        select="store,esn_imei,phone_number,device_model,category,status,date_sold,"
               "owed_to_vip,reimbursement,selling_price,payg_date,acquired_date",
    )
    candidates = []
    for r in rows:
        if not r.get("esn_imei"):
            continue
        sp = r.get("selling_price")
        if sp is None:  # no matching sale → can't judge the charge
            continue
        try:
            cost = float(r.get("owed_to_vip") or 0)
            reimb = float(r.get("reimbursement") or 0)
            sell = float(sp or 0)
        except Exception:
            continue
        if cost <= 0:
            continue
        gap = round(cost - reimb - sell, 2)
        if gap > 0.01:  # undercharge / uncovered cost
            r["_gap"] = gap
            r["_cost"] = cost
            r["_reimb"] = reimb
            r["_sell"] = sell
            candidates.append(r)

    sp_map = _imei_salesperson_map(client, org_id, [r["esn_imei"] for r in candidates])

    flags = []
    for r in candidates:
        gap = r["_gap"]
        d = r.get("date_sold") or r.get("payg_date") or r.get("acquired_date")
        period = "Unknown"; pm = None; py = None
        if d:
            try:
                py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
                period = __import__("datetime").date(py, pm, 1).strftime("%B %Y")
            except Exception:
                pass
        sev = "critical" if gap >= 100 else "warning"
        rep = sp_map.get(_norm_imei(r.get("esn_imei")))
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "Device Undercharge", "source": "asset_undercharge",
            "severity": sev, "store_address": r.get("store"),
            "epay_salesperson": rep,
            "imei": r.get("esn_imei"), "mdn": r.get("phone_number"),
            "amount": gap, "phone_model": r.get("device_model"),
            "description": (f"Cost {r['_cost']:.2f} > reimbursement {r['_reimb']:.2f} + "
                            f"selling price {r['_sell']:.2f} — uncovered {gap:.2f}"),
            "coaching_note": (f"This device cost {r['_cost']:.2f}. After reimbursement "
                              f"({r['_reimb']:.2f}) and the customer price ({r['_sell']:.2f}), "
                              f"{gap:.2f} of the cost was not recovered. Coach "
                              f"{rep or 'the rep'} to charge enough to cover device cost less "
                              f"reimbursement."),
        })

    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "asset_undercharge").execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i+500]).execute()
    return len(flags)


@router.post("/backfill-selling-price")
async def backfill_selling_price(org_id: str = ORG_ID):
    """Manual refresh: re-pull selling prices from sales, then re-sync undercharge flags.
    Run after uploading new sales data without re-uploading the asset file."""
    client = sb()
    updated = _backfill_selling_price(client, org_id)
    flags = _sync_undercharge_flags(client, org_id)
    return {"status": "ok", "rows_priced": updated, "undercharge_flags_written": flags}


@router.post("/sync-undercharge-flags")
async def sync_undercharge_flags(org_id: str = ORG_ID):
    """Manual refresh of undercharge flags from current selling_price values."""
    client = sb()
    n = _sync_undercharge_flags(client, org_id)
    return {"status": "ok", "undercharge_flags_written": n}


@router.get("/ledger")
async def get_asset_ledger(
    org_id: str = ORG_ID,
    status: str = "",
    category: str = "",
    search: str = "",
    limit: int = 200,
    offset: int = 0,
):
    """Paginated ledger with optional filters."""
    client = sb()
    q = client.schema("commcalc").table("asset_ledger") \
        .select("id,esn_imei,phone_number,contract_type,category,status,date_sold,sfid,owed_to_vip,on_inventory,reimbursement,commissions,total_owed,total_reimbursed,selling_price,store,market,notes") \
        .eq("org_id", org_id)

    if status:
        q = q.eq("status", status)
    if category:
        q = q.eq("category", category)
    if search:
        q = q.ilike("esn_imei", f"%{search}%")

    q = q.order("date_sold", desc=True).range(offset, offset + limit - 1)
    resp = q.execute()
    return {"rows": resp.data or [], "offset": offset, "limit": limit}
