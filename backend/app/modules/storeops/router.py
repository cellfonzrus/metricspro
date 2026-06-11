"""StoreOps API Router — /api/v1/storeops/*"""
from fastapi import APIRouter, HTTPException
from app.core.database import get_supabase

router = APIRouter(prefix="/storeops", tags=["StoreOps"])

def sb():
    # StoreOps tables live in the storeops.* schema (see migration 003).
    return get_supabase().schema("storeops")


@router.get("/stores")
def get_stores(org_id: str = "00000000-0000-0000-0000-000000000001"):
    r = sb().table("stores").select("*").order("address").execute()
    return r.data or []

@router.get("/employees")
def get_employees(org_id: str = "00000000-0000-0000-0000-000000000001"):
    r = sb().table("employees").select("*").eq("is_active", True).order("name").execute()
    return r.data or []

@router.get("/shifts")
def get_shifts(store_code: str = None, week_start: str = None, week_end: str = None):
    q = sb().table("shifts").select("*").eq("is_deleted", False)
    if store_code: q = q.eq("store_code", store_code)
    if week_start: q = q.gte("shift_date", week_start)
    if week_end:   q = q.lte("shift_date", week_end)
    return q.order("shift_date").execute().data or []

@router.post("/shifts")
def create_shift(shift: dict):
    r = sb().table("shifts").insert(shift).execute()
    return r.data[0] if r.data else shift

@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: int, updates: dict):
    r = sb().table("shifts").update(updates).eq("id", shift_id).execute()
    return r.data[0] if r.data else updates

@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int):
    sb().table("shifts").delete().eq("id", shift_id).execute()
    return {"deleted": shift_id}

@router.get("/time-off")
def get_time_off(employee_id: str = None):
    q = sb().table("time_off_requests").select("*")
    if employee_id: q = q.eq("employee_id", employee_id)
    return q.order("start_date", desc=True).execute().data or []

@router.post("/time-off")
def create_time_off(request: dict):
    r = sb().table("time_off_requests").insert({**request, "status": "pending"}).execute()
    return r.data[0] if r.data else request

@router.patch("/time-off/{request_id}")
def update_time_off(request_id: int, updates: dict):
    r = sb().table("time_off_requests").update(updates).eq("id", request_id).execute()
    return r.data[0] if r.data else updates

@router.get("/payroll")
def get_payroll(month: str = None):
    """Returns scheduled vs actual hours per employee for payroll"""
    q = sb().table("shifts").select("*").eq("is_deleted", False)
    if month:
        q = q.gte("shift_date", f"{month}-01").lt("shift_date", f"{month}-32")
    shifts = q.execute().data or []
    employees = sb().table("employees").select("id,name,employee_id,pay_rate,home_store").eq("is_active", True).execute().data or []
    
    emp_map = {e["employee_id"]: e for e in employees}
    summary = {}
    for s in shifts:
        eid = s.get("employee_id")
        if eid not in summary:
            emp = emp_map.get(eid, {})
            summary[eid] = {
                "employee_id": eid,
                "name": s.get("employee_name") or emp.get("name", ""),
                "store": s.get("store_code") or emp.get("home_store", ""),
                "pay_rate": float(emp.get("pay_rate") or 0),
                "scheduled_hours": 0,
                "actual_hours": 0,
                "shifts": 0,
            }
        summary[eid]["scheduled_hours"] += float(s.get("scheduled_hours") or 0)
        summary[eid]["actual_hours"]    += float(s.get("actual_hours") or 0)
        summary[eid]["shifts"] += 1

    rows = list(summary.values())
    for r in rows:
        r["scheduled_pay"] = round(r["scheduled_hours"] * r["pay_rate"], 2)
        r["actual_pay"]    = round(r["actual_hours"] * r["pay_rate"], 2)
    return sorted(rows, key=lambda x: x["name"])
