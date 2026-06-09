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
        raise HTTPException(status_code=400, detail=f"Parse error: {e}")

    if not rows:
        raise HTTPException(status_code=400, detail="No rows parsed from file")

    client = sb()
    # Clear existing
    client.schema("commcalc").table("asset_ledger").delete().eq("org_id", org_id).execute()

    # Insert in chunks of 500
    for i in range(0, len(rows), 500):
        chunk = rows[i:i+500]
        client.schema("commcalc").table("asset_ledger").insert(chunk).execute()

    return {"status": "ok", "rows_imported": len(rows)}


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
    total_open = sum(float(r.get("owed_to_vip") or 0) for r in rows)
    total_reimbursed = sum(float(r.get("total_reimbursed") or 0) for r in rows)
    total_owed = sum(float(r.get("total_owed") or 0) for r in rows)
    on_inventory = sum(float(r.get("on_inventory") or 0) for r in rows)

    # By status
    by_status: dict = {}
    for r in rows:
        s = r.get("status") or "Unknown"
        if s not in by_status:
            by_status[s] = {"count": 0, "owed": 0, "reimbursed": 0, "fees": 0}
        by_status[s]["count"] += 1
        by_status[s]["owed"] += float(r.get("owed_to_vip") or 0)
        by_status[s]["reimbursed"] += float(r.get("total_reimbursed") or 0)
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
