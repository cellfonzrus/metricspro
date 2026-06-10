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
async def get_asset_summary(org_id: str = ORG_ID):
    """High-level totals + breakdowns for the summary dashboard."""
    client = sb()
    resp = client.schema("commcalc").table("asset_ledger") \
        .select("status,category,owed_to_vip,on_inventory,reimbursement,commissions,total_owed,total_reimbursed") \
        .eq("org_id", org_id).execute()

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
):
    """Drill-down for one category: status breakdown (all rows) + paginated device rows."""
    client = sb()

    # Pull every row in this category for an accurate status tally.
    # Select only the light columns needed for the breakdown.
    tally_rows = []
    page = 0
    PAGE = 1000
    while True:
        start = page * PAGE
        resp = client.schema("commcalc").table("asset_ledger") \
            .select("status,owed_to_vip,reimbursement,commissions") \
            .eq("org_id", org_id).eq("category", category) \
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
    rows_resp = client.schema("commcalc").table("asset_ledger") \
        .select("id,esn_imei,phone_number,contract_type,status,date_sold,sfid,owed_to_vip,reimbursement,commissions,notes") \
        .eq("org_id", org_id).eq("category", category) \
        .order("date_sold", desc=True).range(offset, offset + limit - 1).execute()

    return {
        "category": category,
        "total_in_category": len(tally_rows),
        "by_status": by_status,
        "rows": rows_resp.data or [],
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
    thursday: str,
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    weeks_ahead: int = 8,
    limit: int = 200,
    offset: int = 0,
):
    """VIP weekly collection report for a chosen Thursday, plus upcoming forecast."""
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

    return {
        "thursday": thursday,
        "filters": {"store": store or None, "market": market or None},
        "due_this_week": due_this_week,
        "by_store": by_store,
        "upcoming": upcoming,
        "rows": rows_resp.data or [],
        "total_due_rows": len(due_rows),
        "offset": offset,
        "limit": limit,
    }


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
        .select("id,esn_imei,phone_number,contract_type,category,status,date_sold,sfid,owed_to_vip,on_inventory,reimbursement,commissions,total_owed,total_reimbursed,notes") \
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
