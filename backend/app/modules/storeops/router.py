"""StoreOps API Router — /api/v1/storeops/*"""
from datetime import datetime, timezone, timedelta
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
    # Block scheduling an employee on a day they have APPROVED time off.
    eid = shift.get("employee_id")
    sdate = shift.get("shift_date")
    if eid and sdate:
        conflict = (sb().table("time_off_requests").select("id")
                    .eq("employee_id", str(eid)).eq("status", "approved")
                    .lte("start_date", sdate).gte("end_date", sdate)
                    .limit(1).execute().data)
        if conflict:
            who = shift.get("employee_name") or "This employee"
            raise HTTPException(409, f"{who} has approved time off on {sdate} — cannot schedule.")
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
    if not (request.get("employee_id") and request.get("start_date") and request.get("end_date")):
        raise HTTPException(400, "employee_id, start_date and end_date are required")
    status = str(request.get("status") or "pending").lower()
    if status not in ("pending", "approved", "denied"):
        status = "pending"
    row = {**request, "status": status}
    # Manager approve-at-submission: stamp approved_at if approved and not already set.
    if status == "approved" and not row.get("approved_at"):
        row["approved_at"] = datetime.now(timezone.utc).isoformat()
    r = sb().table("time_off_requests").insert(row).execute()
    if not r.data:
        # Previously this silently returned the un-inserted request, so a failed save
        # still showed in the UI but never persisted ("time off not being saved").
        raise HTTPException(500, "Failed to save the time-off request.")
    return r.data[0]

@router.patch("/time-off/{request_id}")
def update_time_off(request_id: int, updates: dict):
    r = sb().table("time_off_requests").update(updates).eq("id", request_id).execute()
    return r.data[0] if r.data else updates

@router.get("/payroll")
def get_payroll(month: str = None):
    """Returns scheduled vs actual hours per employee for payroll"""
    q = sb().table("shifts").select("*").eq("is_deleted", False)
    if month:
        # Exclusive upper bound = first day of the next month. (The old "{month}-32"
        # hack 500s on a DATE column because 2026-06-32 isn't a valid date.)
        parts = str(month).split("-")
        y, m = int(parts[0]), int(parts[1])
        nxt = f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01"
        q = q.gte("shift_date", f"{month}-01").lt("shift_date", nxt)
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
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        if act == 0:
            act = sched  # actual not recorded yet -> fall back to scheduled hours
        summary[eid]["scheduled_hours"] += sched
        summary[eid]["actual_hours"]    += act
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
    # employee_id is TEXT UNIQUE: a blank '' collides on the 2nd person with no ID.
    # Drop it so the column is NULL (multiple NULLs are allowed).
    if not (row.get("employee_id") or "").strip():
        row.pop("employee_id", None)
    r = sb().table("employees").insert(row).execute()
    return r.data[0] if r.data else row


@router.patch("/employees/{emp_id}")
def update_employee(emp_id: str, updates: dict):
    """Update an employee (name/role/home_store/pay_rate/active/contact). StoreOps Admin.
    emp_id is str (not int) so a UUID or numeric id both work — a typed int rejected UUID ids
    with a 422, which read as 'cannot edit' in the UI."""
    row = {k: updates[k] for k in EMP_FIELDS if k in updates}
    if not row:
        raise HTTPException(400, "no valid fields to update")
    # Clearing the Emp ID must store NULL, not '' (TEXT UNIQUE → '' collides across people).
    if "employee_id" in row and not (row.get("employee_id") or "").strip():
        row["employee_id"] = None
    r = sb().table("employees").update(row).eq("id", emp_id).execute()
    if not r.data:
        raise HTTPException(404, "employee not found")
    return r.data[0]


@router.delete("/employees/{emp_id}")
def delete_employee(emp_id: str):
    """Delete an employee (StoreOps Admin). 404 if missing; 409 if blocked by linked rows
    (shifts / app_users) — the UI can then deactivate (is_active=false) instead.
    Also cascades to the login (app_users row + Supabase Auth account) so the person doesn't
    resurface as a ghost manual user in Roles & Access — i.e. a StoreOps delete is now reflected
    in Roles & Assignments too."""
    existing = sb().table("employees").select("id,name,email,employee_id").eq("id", emp_id).execute().data
    if not existing:
        raise HTTPException(404, "employee not found")
    e = existing[0]
    try:
        sb().table("employees").delete().eq("id", emp_id).execute()
    except Exception as ex:
        raise HTTPException(409, f"cannot delete (linked records exist — try deactivating): {ex}")
    login = {}
    try:
        from app.modules.core.router import purge_app_user, ORG_ID
        login = purge_app_user(ORG_ID, email=e.get("email"), employee_id=e.get("employee_id"), hard=True)
    except Exception:
        pass
    return {"ok": True, "deleted": emp_id, "name": e.get("name"), "login": login}


@router.post("/employees/merge")
def merge_employees(body: dict):
    """Merge a DUPLICATE employee into a TARGET: reassign the duplicate's shifts + time-off to the
    target (by employee_id and by name), then delete the duplicate (deactivate if delete is blocked)."""
    dup_id = str(body.get("dup_id") or "").strip()
    target_id = str(body.get("target_id") or "").strip()
    if not dup_id or not target_id or dup_id == target_id:
        raise HTTPException(400, "dup_id and target_id (different) are required")
    dup = sb().table("employees").select("*").eq("id", dup_id).execute().data
    tgt = sb().table("employees").select("*").eq("id", target_id).execute().data
    if not dup or not tgt:
        raise HTTPException(404, "employee not found")
    dup, tgt = dup[0], tgt[0]
    moved = {"shifts": 0, "time_off": 0}
    reassign = {"employee_id": str(tgt["id"]), "employee_name": tgt.get("name")}
    for field, val in (("employee_id", str(dup["id"])), ("employee_name", dup.get("name"))):
        if not val:
            continue
        try:
            r = sb().table("shifts").update(reassign).eq(field, val).execute()
            moved["shifts"] += len(r.data or [])
        except Exception:
            pass
    try:
        r = sb().table("time_off_requests").update({"employee_id": str(tgt["id"])}).eq("employee_id", str(dup["id"])).execute()
        moved["time_off"] += len(r.data or [])
    except Exception:
        pass
    deleted = True
    try:
        sb().table("employees").delete().eq("id", dup_id).execute()
    except Exception:
        sb().table("employees").update({"is_active": False}).eq("id", dup_id).execute()
        deleted = False
    # Cascade the duplicate's login too (delete it if the row was deleted, else deactivate) so the
    # merged-away person doesn't linger in Roles & Access.
    login = {}
    try:
        from app.modules.core.router import purge_app_user, ORG_ID
        login = purge_app_user(ORG_ID, email=dup.get("email"), employee_id=dup.get("employee_id"), hard=deleted)
    except Exception:
        pass
    return {"ok": True, "merged_into": tgt.get("name"), "moved": moved, "deleted_duplicate": deleted, "login": login}


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


# ── Shift swaps (storeops.shift_swap_requests) ────────────────────────────────
def _emp_name_map():
    return {str(e["employee_id"]): e.get("name")
            for e in (sb().table("employees").select("employee_id,name").execute().data or [])
            if e.get("employee_id")}


# ── Recurring shift templates: save a week as a per-employee template, apply to any week ─────
@router.get("/shift-templates")
def get_shift_templates():
    return sb().table("shift_templates").select("*").order("weekday").execute().data or []


@router.post("/shift-templates/save-week")
def save_week_as_template(body: dict):
    """Save a week's shifts as the recurring template (replaces existing templates for those employees)."""
    week_start = (body.get("week_start") or "").strip()
    if not week_start:
        raise HTTPException(400, "week_start required")
    we = (datetime.fromisoformat(week_start).date() + timedelta(days=6)).isoformat()
    shifts = (sb().table("shifts").select("*").eq("is_deleted", False)
              .gte("shift_date", week_start).lte("shift_date", we).execute().data) or []
    if not shifts:
        raise HTTPException(400, "No shifts in that week to save as a template.")
    emp_ids = list({str(s.get("employee_id")) for s in shifts if s.get("employee_id")})
    for eid in emp_ids:
        sb().table("shift_templates").delete().eq("employee_id", eid).execute()
    by_key = {}
    for s in shifts:
        try:
            wd = datetime.fromisoformat(str(s.get("shift_date"))).date().weekday()  # Mon=0
        except Exception:
            continue
        eid = str(s.get("employee_id")) if s.get("employee_id") else None
        by_key[(eid, wd, s.get("store_code"))] = {
            "employee_id": eid, "employee_name": s.get("employee_name"), "store_code": s.get("store_code"),
            "weekday": wd, "start_time": s.get("start_time"), "end_time": s.get("end_time"),
            "scheduled_hours": s.get("scheduled_hours") or 0}
    rows = list(by_key.values())
    for i in range(0, len(rows), 500):
        sb().table("shift_templates").upsert(rows[i:i + 500], on_conflict="org_id,employee_id,weekday,store_code").execute()
    return {"saved": len(rows), "employees": len(emp_ids)}


@router.post("/shift-templates/apply")
def apply_templates(body: dict):
    """Create shifts for a week from the saved templates (dedup-safe; skips time-off-blocked days)."""
    week_start = (body.get("week_start") or "").strip()
    if not week_start:
        raise HTTPException(400, "week_start required")
    ws = datetime.fromisoformat(week_start).date()
    we = (ws + timedelta(days=6)).isoformat()
    templates = sb().table("shift_templates").select("*").execute().data or []
    if not templates:
        raise HTTPException(400, "No templates saved yet — save a week first.")
    existing = (sb().table("shifts").select("employee_name,shift_date,start_time,store_code")
                .eq("is_deleted", False).gte("shift_date", week_start).lte("shift_date", we).execute().data) or []
    seen = {(e.get("employee_name"), str(e.get("shift_date")), e.get("start_time"), e.get("store_code")) for e in existing}
    added = skipped_off = 0
    for t in templates:
        target = (ws + timedelta(days=int(t.get("weekday") or 0))).isoformat()
        if (t.get("employee_name"), target, t.get("start_time"), t.get("store_code")) in seen:
            continue
        eid = t.get("employee_id")
        if eid:
            conflict = (sb().table("time_off_requests").select("id").eq("employee_id", str(eid))
                        .eq("status", "approved").lte("start_date", target).gte("end_date", target).limit(1).execute().data)
            if conflict:
                skipped_off += 1
                continue
        try:
            sb().table("shifts").insert({
                "employee_id": eid, "employee_name": t.get("employee_name"), "store_code": t.get("store_code"),
                "shift_date": target, "start_time": t.get("start_time"), "end_time": t.get("end_time"),
                "scheduled_hours": t.get("scheduled_hours") or 0, "status": "scheduled"}).execute()
            added += 1
        except Exception:
            pass
    return {"added": added, "skipped_timeoff": skipped_off, "templates": len(templates)}


@router.get("/shift-swaps")
def get_shift_swaps(status: str = None):
    """List swap requests, enriched with employee names + shift details for display."""
    q = sb().table("shift_swap_requests").select("*")
    if status:
        q = q.eq("status", status)
    reqs = q.order("created_at", desc=True).execute().data or []
    names = _emp_name_map()
    ids = [r["shift_id"] for r in reqs if r.get("shift_id")] + \
          [r["target_shift_id"] for r in reqs if r.get("target_shift_id")]
    shifts = {}
    if ids:
        sh = sb().table("shifts").select(
            "id,employee_name,store_code,shift_date,start_time,end_time").in_("id", ids).execute().data or []
        shifts = {s["id"]: s for s in sh}
    for r in reqs:
        r["requester_name"] = names.get(str(r.get("requester_id")), r.get("requester_id"))
        r["target_name"] = names.get(str(r.get("target_id"))) if r.get("target_id") else None
        r["shift"] = shifts.get(r.get("shift_id"))
        r["target_shift"] = shifts.get(r.get("target_shift_id"))
    return reqs


@router.post("/shift-swaps")
def create_shift_swap(req: dict):
    """Create a swap request. Body: requester_id, target_id?, shift_id?, target_shift_id?, notes?"""
    if not req.get("requester_id"):
        raise HTTPException(400, "requester_id required")
    row = {k: req.get(k) for k in ("requester_id", "target_id", "shift_id", "target_shift_id", "notes")}
    row["status"] = "pending"
    row["org_id"] = ORG_ID
    r = sb().table("shift_swap_requests").insert(row).execute()
    return r.data[0] if r.data else row


def _apply_swap(swap):
    """On approval, reassign the shift(s). If both shifts present it's a true swap;
    otherwise the single shift is handed to the target employee."""
    names = _emp_name_map()
    tgt, reqr = swap.get("target_id"), swap.get("requester_id")
    if swap.get("shift_id") and tgt:
        sb().table("shifts").update({"employee_id": tgt, "employee_name": names.get(str(tgt))}) \
            .eq("id", swap["shift_id"]).execute()
    if swap.get("target_shift_id") and reqr:
        sb().table("shifts").update({"employee_id": reqr, "employee_name": names.get(str(reqr))}) \
            .eq("id", swap["target_shift_id"]).execute()


@router.patch("/shift-swaps/{swap_id}")
def update_shift_swap(swap_id: int, updates: dict):
    """Approve/deny/cancel a swap. Approving reassigns the shift(s)."""
    status = updates.get("status")
    if status not in ("approved", "denied", "pending", "cancelled"):
        raise HTTPException(400, "invalid status")
    cur = sb().table("shift_swap_requests").select("*").eq("id", swap_id).limit(1).execute().data or []
    if not cur:
        raise HTTPException(404, "swap not found")
    if status == "approved":
        _apply_swap(cur[0])
    r = sb().table("shift_swap_requests").update({"status": status}).eq("id", swap_id).execute()
    return r.data[0] if r.data else {"id": swap_id, "status": status}
