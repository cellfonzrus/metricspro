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
def get_employees(include_inactive: bool = False, org_id: str = "00000000-0000-0000-0000-000000000001"):
    q = sb().table("employees").select("*")
    if not include_inactive:
        q = q.eq("is_active", True)
    return q.order("name").execute().data or []

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


ORG_ID = "00000000-0000-0000-0000-000000000001"

EMP_FIELDS = ("name", "home_store", "role", "pay_rate", "is_active", "email",
              "phone", "notes", "epay_login", "epay_salesperson", "employee_id")
STORE_FIELDS = ("store_code", "address", "market", "monthly_target", "is_active", "phone", "notes")


@router.post("/employees")
def create_employee(emp: dict):
    """Create an employee (StoreOps Admin)."""
    row = {k: emp[k] for k in EMP_FIELDS if k in emp}
    if not (row.get("name") or "").strip():
        raise HTTPException(400, "name required")
    row["org_id"] = ORG_ID
    if row.get("is_active") is None:
        row["is_active"] = True
    r = sb().table("employees").insert(row).execute()
    return r.data[0] if r.data else row


@router.patch("/employees/{emp_id}")
def update_employee(emp_id: int, updates: dict):
    """Update an employee (name/role/home_store/pay_rate/active/contact). StoreOps Admin."""
    row = {k: updates[k] for k in EMP_FIELDS if k in updates}
    if not row:
        raise HTTPException(400, "no valid fields to update")
    r = sb().table("employees").update(row).eq("id", emp_id).execute()
    if not r.data:
        raise HTTPException(404, "employee not found")
    return r.data[0]


@router.post("/employees/bulk-payscale")
def bulk_payscale(body: dict):
    """Bulk set pay rates from a list. Body: {rows:[{employee_id|name, pay_rate}]}.
    Matches by employee_id, else exact name (case-insensitive). Reports unmatched/bad rows."""
    rows = body.get("rows") or body.get("employees") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows[] required")
    emps = sb().table("employees").select("id,employee_id,name").execute().data or []
    by_eid = {str(e.get("employee_id")): e for e in emps if e.get("employee_id")}
    by_name = {(e.get("name") or "").strip().lower(): e for e in emps}
    updated, errors = 0, []
    for i, rw in enumerate(rows):
        try:
            rate = float(rw.get("pay_rate"))
        except (TypeError, ValueError):
            errors.append({"row": i + 1, "error": "invalid pay_rate"})
            continue
        eid = str(rw.get("employee_id") or "").strip()
        match = (by_eid.get(eid) if eid else None) or by_name.get((rw.get("name") or "").strip().lower())
        if not match:
            errors.append({"row": i + 1, "error": "employee not found", "ref": eid or rw.get("name")})
            continue
        sb().table("employees").update({"pay_rate": rate}).eq("id", match["id"]).execute()
        updated += 1
    return {"updated": updated, "errors": errors, "total": len(rows)}


@router.post("/stores")
def create_store(store: dict):
    """Create a store (StoreOps Admin)."""
    row = {k: store[k] for k in STORE_FIELDS if k in store}
    if not (row.get("store_code") or "").strip():
        raise HTTPException(400, "store_code required")
    row["org_id"] = ORG_ID
    if row.get("is_active") is None:
        row["is_active"] = True
    r = sb().table("stores").insert(row).execute()
    return r.data[0] if r.data else row


@router.patch("/stores/{store_id}")
def update_store(store_id: int, updates: dict):
    """Update a store (StoreOps Admin)."""
    row = {k: updates[k] for k in STORE_FIELDS if k in updates}
    if not row:
        raise HTTPException(400, "no valid fields to update")
    r = sb().table("stores").update(row).eq("id", store_id).execute()
    if not r.data:
        raise HTTPException(404, "store not found")
    return r.data[0]
