"""StoreOps API Router — /api/v1/storeops/*"""
import base64
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Header
from app.core.database import get_supabase
from app.core.config import settings

try:
    from zoneinfo import ZoneInfo
    _BIZ_TZ = ZoneInfo(settings.BUSINESS_TZ or "America/New_York")
except Exception:                       # zoneinfo/tzdata unavailable → fall back to UTC (no crash)
    _BIZ_TZ = timezone.utc

router = APIRouter(prefix="/storeops", tags=["StoreOps"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

def sb():
    # StoreOps tables live in the storeops.* schema (see migration 003).
    return get_supabase().schema("storeops")


@router.get("/stores")
def get_stores(authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    r = sb().table("stores").select("*").eq("org_id", org_id).order("address").execute()
    rows = r.data or []
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / enforcement off)
    if ks is not None:
        rows = [s for s in rows if in_keyset(ks, s.get("store_code"), s.get("address"))]
    return rows

@router.get("/timeclock/stores")
def timeclock_stores(org_id: str = ORG_ID):
    """FULL active store list for the kiosk clock-in picker — deliberately UNSCOPED (no RBAC span
    filter, unlike GET /stores) so a visiting/floater rep can pick the store they're physically at
    and reach the manager-override path, instead of being silently forced into their home store."""
    rows = sb().table("stores").select("store_code,address,market").eq("org_id", org_id).order("address").execute().data or []
    return [s for s in rows if s.get("store_code")]

@router.get("/employees")
def get_employees(include_inactive: bool = False, all_company: bool = False, authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    """Employees in the caller's span. all_company=true returns the WHOLE org roster (still
    org-scoped) — used by the schedule picker so a manager can borrow an employee from another
    store/market onto a shift, even if they're outside the manager's span."""
    q = sb().table("employees").select("*").eq("org_id", org_id)
    if not include_inactive:
        q = q.eq("is_active", True)
    rows = q.order("name").execute().data or []
    if not all_company:
        ks = scope_keyset(authorization, org_id)
        if ks is not None:
            rows = [e for e in rows if in_keyset(ks, e.get("home_store"))]
    return rows

@router.get("/shifts")
def get_shifts(store_code: str = None, week_start: str = None, week_end: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
    if store_code: q = q.eq("store_code", store_code)
    if week_start: q = q.gte("shift_date", week_start)
    if week_end:   q = q.lte("shift_date", week_end)
    rows = q.order("shift_date").execute().data or []
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [s for s in rows if in_keyset(ks, s.get("store_code"))]
    return rows

@router.post("/shifts")
def create_shift(shift: dict, org_id: str = ORG_ID):
    # Block scheduling an employee on a day they have APPROVED time off.
    eid = shift.get("employee_id")
    sdate = shift.get("shift_date")
    if eid and sdate:
        conflict = (sb().table("time_off_requests").select("id").eq("org_id", org_id)
                    .eq("employee_id", str(eid)).eq("status", "approved")
                    .lte("start_date", sdate).gte("end_date", sdate)
                    .limit(1).execute().data)
        if conflict:
            who = shift.get("employee_name") or "This employee"
            raise HTTPException(409, f"{who} has approved time off on {sdate} — cannot schedule.")
    # Stamp org_id so the row survives the org-scoped read filter on GET /shifts.
    # (shifts.org_id has NO column default → an unstamped insert lands NULL and vanishes.)
    shift = {**shift, "org_id": shift.get("org_id") or org_id}
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
def get_time_off(employee_id: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    q = sb().table("time_off_requests").select("*").eq("org_id", org_id)
    if employee_id: q = q.eq("employee_id", employee_id)
    rows = q.order("start_date", desc=True).execute().data or []
    eids = scope_emp_ids(authorization, org_id)   # None = unrestricted
    if eids is not None:
        rows = [r for r in rows if str(r.get("employee_id")) in eids]
    return rows

@router.post("/time-off")
def create_time_off(request: dict, org_id: str = ORG_ID):
    if not (request.get("employee_id") and request.get("start_date") and request.get("end_date")):
        raise HTTPException(400, "employee_id, start_date and end_date are required")
    status = str(request.get("status") or "pending").lower()
    if status not in ("pending", "approved", "denied"):
        status = "pending"
    # Stamp org_id (no column default) so the request survives the org-scoped GET /time-off filter.
    row = {**request, "status": status, "org_id": request.get("org_id") or org_id}
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
def get_payroll(month: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Returns scheduled vs actual hours per employee for payroll"""
    q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
    if month:
        # Exclusive upper bound = first day of the next month. (The old "{month}-32"
        # hack 500s on a DATE column because 2026-06-32 isn't a valid date.)
        parts = str(month).split("-")
        y, m = int(parts[0]), int(parts[1])
        nxt = f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01"
        q = q.gte("shift_date", f"{month}-01").lt("shift_date", nxt)
    shifts = q.execute().data or []
    employees = sb().table("employees").select("id,name,employee_id,pay_rate,home_store").eq("org_id", org_id).eq("is_active", True).execute().data or []
    
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
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store"))]
    return sorted(rows, key=lambda x: x["name"])


ORG_ID = "00000000-0000-0000-0000-000000000001"

EMP_FIELDS = ("name", "home_store", "role", "pay_rate", "is_active", "email",
              "phone", "notes", "epay_login", "epay_salesperson", "employee_id")
STORE_FIELDS = ("store_code", "address", "market", "monthly_target", "is_active", "phone", "notes")


@router.post("/employees/bulk")
def bulk_create_employees(body: dict, org_id: str = ORG_ID):
    """Bulk-create employees from a filled template (new-tenant setup). Body: {employees:[{...}]}.
    Skips blank-name rows and any employee_id that already exists (so re-upload is idempotent)."""
    rows_in = body.get("employees") or body.get("rows") or []
    if not isinstance(rows_in, list):
        raise HTTPException(400, "employees must be a list")
    existing = {str(e.get("employee_id")) for e in
                (sb().table("employees").select("employee_id").eq("org_id", org_id).execute().data or [])
                if e.get("employee_id")}
    to_insert, skipped = [], 0
    for e in rows_in:
        row = {k: e[k] for k in EMP_FIELDS if k in e}
        if not str(row.get("name") or "").strip():
            skipped += 1; continue
        eid = str(row.get("employee_id") or "").strip()
        if eid and eid in existing:
            skipped += 1; continue
        if not eid:
            row.pop("employee_id", None)
        row["org_id"] = org_id
        if row.get("is_active") is None:
            row["is_active"] = True
        to_insert.append(row)
        if eid:
            existing.add(eid)
    inserted = 0
    for i in range(0, len(to_insert), 500):
        chunk = to_insert[i:i + 500]
        r = sb().table("employees").insert(chunk).execute()
        for rec in (r.data or []):
            _ensure_employee_id(rec)   # so bulk-added people are assignable in the org
        inserted += len(r.data or chunk)
    return {"inserted": inserted, "skipped": skipped}


def _ensure_employee_id(rec: dict) -> dict:
    """Every employee needs a stable employee_id to be placed in the org tree or assigned a role /
    manager. Auto-generate one (E<pk>) when it's missing, so no employee is unassignable."""
    if rec and rec.get("id") and not str(rec.get("employee_id") or "").strip():
        gen = f"E{rec['id']}"
        try:
            sb().table("employees").update({"employee_id": gen}).eq("id", rec["id"]).execute()
            rec["employee_id"] = gen
        except Exception:
            pass
    return rec


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
    # Drop it so the column is NULL (multiple NULLs are allowed), then auto-assign one below.
    if not (row.get("employee_id") or "").strip():
        row.pop("employee_id", None)
    r = sb().table("employees").insert(row).execute()
    return _ensure_employee_id(r.data[0]) if r.data else row


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
    return _ensure_employee_id(r.data[0])


@router.delete("/employees/{emp_id}")
def delete_employee(emp_id: str, org_id: str = ORG_ID):
    """Delete an employee (StoreOps Admin). 404 if missing; 409 if blocked by linked rows
    (shifts / app_users) — the UI can then deactivate (is_active=false) instead.
    Also cascades to the login (app_users row + Supabase Auth account) so the person doesn't
    resurface as a ghost manual user in Roles & Access — i.e. a StoreOps delete is now reflected
    in Roles & Assignments too."""
    existing = sb().table("employees").select("id,name,email,employee_id").eq("org_id", org_id).eq("id", emp_id).execute().data
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
def merge_employees(body: dict, org_id: str = ORG_ID):
    """Merge a DUPLICATE employee into a TARGET: reassign the duplicate's shifts + time-off to the
    target (by employee_id and by name), then delete the duplicate (deactivate if delete is blocked)."""
    dup_id = str(body.get("dup_id") or "").strip()
    target_id = str(body.get("target_id") or "").strip()
    if not dup_id or not target_id or dup_id == target_id:
        raise HTTPException(400, "dup_id and target_id (different) are required")
    dup = sb().table("employees").select("*").eq("org_id", org_id).eq("id", dup_id).execute().data
    tgt = sb().table("employees").select("*").eq("org_id", org_id).eq("id", target_id).execute().data
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
def bulk_payscale(body: dict, org_id: str = ORG_ID):
    """Bulk set pay rates from a list. Body: {rows:[{employee_id|name, pay_rate}]}.
    Matches by employee_id, else exact name (case-insensitive). Reports unmatched/bad rows."""
    rows = body.get("rows") or body.get("employees") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows[] required")
    emps = sb().table("employees").select("id,employee_id,name").eq("org_id", org_id).execute().data or []
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


@router.post("/stores/bulk")
def bulk_create_stores(body: dict, org_id: str = ORG_ID):
    """Bulk-create stores from a filled template (new-tenant setup). Body: {stores:[{...}]}.
    Skips blank store_code rows and store_codes that already exist (idempotent re-upload)."""
    rows_in = body.get("stores") or body.get("rows") or []
    if not isinstance(rows_in, list):
        raise HTTPException(400, "stores must be a list")
    existing = {str(s.get("store_code")).strip().upper() for s in
                (sb().table("stores").select("store_code").eq("org_id", org_id).execute().data or [])
                if s.get("store_code")}
    to_insert, skipped = [], 0
    for s in rows_in:
        row = {k: s[k] for k in STORE_FIELDS if k in s}
        code = str(row.get("store_code") or "").strip()
        if not code or code.upper() in existing:
            skipped += 1; continue
        row["org_id"] = org_id
        if row.get("is_active") is None:
            row["is_active"] = True
        to_insert.append(row)
        existing.add(code.upper())
    inserted = 0
    for i in range(0, len(to_insert), 500):
        r = sb().table("stores").insert(to_insert[i:i + 500]).execute()
        inserted += len(r.data or to_insert[i:i + 500])
    return {"inserted": inserted, "skipped": skipped}


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
def _emp_name_map(org_id: str = ORG_ID):
    return {str(e["employee_id"]): e.get("name")
            for e in (sb().table("employees").select("employee_id,name").eq("org_id", org_id).execute().data or [])
            if e.get("employee_id")}


# ── Recurring shift templates: save a week as a per-employee template, apply to any week ─────
@router.get("/shift-templates")
def get_shift_templates(authorization: str = Header(default=""), org_id: str = ORG_ID):
    rows = sb().table("shift_templates").select("*").eq("org_id", org_id).order("weekday").execute().data or []
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [t for t in rows if in_keyset(ks, t.get("store_code"))]
    return rows


@router.post("/shift-templates/save-week")
def save_week_as_template(body: dict, org_id: str = ORG_ID):
    """Save a week's shifts as the recurring template (replaces existing templates for those employees)."""
    week_start = (body.get("week_start") or "").strip()
    if not week_start:
        raise HTTPException(400, "week_start required")
    we = (datetime.fromisoformat(week_start).date() + timedelta(days=6)).isoformat()
    shifts = (sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
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
            "org_id": org_id,
            "employee_id": eid, "employee_name": s.get("employee_name"), "store_code": s.get("store_code"),
            "weekday": wd, "start_time": s.get("start_time"), "end_time": s.get("end_time"),
            "scheduled_hours": s.get("scheduled_hours") or 0}
    rows = list(by_key.values())
    for i in range(0, len(rows), 500):
        sb().table("shift_templates").upsert(rows[i:i + 500], on_conflict="org_id,employee_id,weekday,store_code").execute()
    return {"saved": len(rows), "employees": len(emp_ids)}


@router.post("/shift-templates/apply")
def apply_templates(body: dict, org_id: str = ORG_ID):
    """Create shifts for a week from the saved templates (dedup-safe; skips time-off-blocked days)."""
    week_start = (body.get("week_start") or "").strip()
    if not week_start:
        raise HTTPException(400, "week_start required")
    ws = datetime.fromisoformat(week_start).date()
    we = (ws + timedelta(days=6)).isoformat()
    templates = sb().table("shift_templates").select("*").eq("org_id", org_id).execute().data or []
    if not templates:
        raise HTTPException(400, "No templates saved yet — save a week first.")
    existing = (sb().table("shifts").select("employee_name,shift_date,start_time,store_code")
                .eq("org_id", org_id).eq("is_deleted", False).gte("shift_date", week_start).lte("shift_date", we).execute().data) or []
    seen = {(e.get("employee_name"), str(e.get("shift_date")), e.get("start_time"), e.get("store_code")) for e in existing}
    added = skipped_off = 0
    for t in templates:
        target = (ws + timedelta(days=int(t.get("weekday") or 0))).isoformat()
        if (t.get("employee_name"), target, t.get("start_time"), t.get("store_code")) in seen:
            continue
        eid = t.get("employee_id")
        if eid:
            conflict = (sb().table("time_off_requests").select("id").eq("org_id", org_id).eq("employee_id", str(eid))
                        .eq("status", "approved").lte("start_date", target).gte("end_date", target).limit(1).execute().data)
            if conflict:
                skipped_off += 1
                continue
        try:
            sb().table("shifts").insert({
                "org_id": org_id,
                "employee_id": eid, "employee_name": t.get("employee_name"), "store_code": t.get("store_code"),
                "shift_date": target, "start_time": t.get("start_time"), "end_time": t.get("end_time"),
                "scheduled_hours": t.get("scheduled_hours") or 0, "status": "scheduled"}).execute()
            added += 1
        except Exception:
            pass
    return {"added": added, "skipped_timeoff": skipped_off, "templates": len(templates)}


@router.get("/shift-swaps")
def get_shift_swaps(status: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """List swap requests, enriched with employee names + shift details for display."""
    q = sb().table("shift_swap_requests").select("*").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    reqs = q.order("created_at", desc=True).execute().data or []
    eids = scope_emp_ids(authorization, org_id)   # None = unrestricted
    if eids is not None:
        reqs = [r for r in reqs if str(r.get("requester_id")) in eids
                or (r.get("target_id") and str(r.get("target_id")) in eids)]
    names = _emp_name_map(org_id)
    ids = [r["shift_id"] for r in reqs if r.get("shift_id")] + \
          [r["target_shift_id"] for r in reqs if r.get("target_shift_id")]
    shifts = {}
    if ids:
        sh = sb().table("shifts").select(
            "id,employee_name,store_code,shift_date,start_time,end_time").eq("org_id", org_id).in_("id", ids).execute().data or []
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


def _apply_swap(swap, org_id: str = ORG_ID):
    """On approval, reassign the shift(s). If both shifts present it's a true swap;
    otherwise the single shift is handed to the target employee."""
    names = _emp_name_map(org_id)
    tgt, reqr = swap.get("target_id"), swap.get("requester_id")
    if swap.get("shift_id") and tgt:
        sb().table("shifts").update({"employee_id": tgt, "employee_name": names.get(str(tgt))}) \
            .eq("id", swap["shift_id"]).execute()
    if swap.get("target_shift_id") and reqr:
        sb().table("shifts").update({"employee_id": reqr, "employee_name": names.get(str(reqr))}) \
            .eq("id", swap["target_shift_id"]).execute()


@router.patch("/shift-swaps/{swap_id}")
def update_shift_swap(swap_id: int, updates: dict, org_id: str = ORG_ID):
    """Approve/deny/cancel a swap. Approving reassigns the shift(s)."""
    status = updates.get("status")
    if status not in ("approved", "denied", "pending", "cancelled"):
        raise HTTPException(400, "invalid status")
    cur = sb().table("shift_swap_requests").select("*").eq("org_id", org_id).eq("id", swap_id).limit(1).execute().data or []
    if not cur:
        raise HTTPException(404, "swap not found")
    if status == "approved":
        _apply_swap(cur[0], org_id)
    r = sb().table("shift_swap_requests").update({"status": status}).eq("id", swap_id).execute()
    return r.data[0] if r.data else {"id": swap_id, "status": status}


# ═══════════════════════════════════════════════════════════════════════════════
# TIME CLOCK — clock-in/out, face recognition, manual hours, payroll settings (Part B / mig 045)
# ═══════════════════════════════════════════════════════════════════════════════
TIMECLOCK_BUCKET = "timeclock-selfies"


def _ensure_selfie_bucket():
    client = get_supabase()
    try:
        client.storage.get_bucket(TIMECLOCK_BUCKET)
    except Exception:
        try:
            client.storage.create_bucket(TIMECLOCK_BUCKET)   # private by default
        except Exception:
            pass
    return client


def _upload_selfie(org_id, employee_id, data_url):
    """Decode a 'data:image/jpeg;base64,...' selfie and store it; return the storage path (or None)."""
    if not data_url or "," not in str(data_url):
        return None
    try:
        header, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
        ext = "png" if "png" in header else "jpg"
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        path = f"{org_id}/{employee_id}/{ts}.{ext}"
        _ensure_selfie_bucket().storage.from_(TIMECLOCK_BUCKET).upload(
            path, raw, {"content-type": f"image/{'png' if ext == 'png' else 'jpeg'}", "upsert": "true"})
        return path
    except Exception as e:
        print(f"WARN selfie upload failed: {e}")
        return None


def _signed_selfie(path):
    if not path:
        return None
    try:
        res = get_supabase().storage.from_(TIMECLOCK_BUCKET).create_signed_url(path, 3600)
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
        return res
    except Exception:
        return None


def _emp_name(org_id, employee_id):
    r = sb().table("employees").select("name,home_store").eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data or []
    return (r[0].get("name") if r else None), (r[0].get("home_store") if r else None)


def _fmt_time(iso):
    # Display in the BUSINESS timezone (not the server's) so the kiosk time matches the reports and
    # doesn't drift with wherever Railway happens to run.
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(_BIZ_TZ).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return iso


def _norm_store(x):
    return str(x or "").strip().upper()


def _allowed_clock_stores(org_id, employee_id, home_store, work_date_local):
    """The stores this employee may clock in at TODAY, without a manager override:
    their home store + any store they're SCHEDULED at today + any store they float to
    (app_users.store_codes[]). Returns a set of normalized (UPPER) store codes."""
    codes = set()
    if home_store:
        codes.add(_norm_store(home_store))
    try:
        sh = (sb().table("shifts").select("store_code").eq("org_id", org_id)
              .eq("employee_id", employee_id).eq("shift_date", work_date_local)
              .eq("is_deleted", False).execute().data) or []
        for s in sh:
            if s.get("store_code"):
                codes.add(_norm_store(s["store_code"]))
    except Exception:
        pass
    try:
        au = (sb().table("app_users").select("store_codes").eq("org_id", org_id)
              .eq("employee_id", employee_id).limit(1).execute().data) or []
        for c in ((au[0].get("store_codes") if au else None) or []):
            if c:
                codes.add(_norm_store(c))
    except Exception:
        pass
    return codes


def _require_manager(authorization, org_id):
    """Resolve the signed-in caller and confirm they're a manager (not a plain rep) so they can
    authorize a clock-in override. Returns the manager's app_user row; raises 401/403 otherwise."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "A manager must sign in to approve this override.")
    rows = (sb().table("app_users").select("email,role,employee_id").eq("org_id", org_id)
            .eq("auth_id", uid).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(403, "That login isn't recognized.")
    u = rows[0]
    role = (u.get("role") or "").lower()
    MGR_ROLES = {"admin", "market_manager", "store_manager", "district_manager",
                 "regional_manager", "director", "executive"}
    if role in MGR_ROLES:
        return u
    try:  # otherwise allow any role whose scope isn't 'self' (a configured custom manager role)
        rr = (sb().table("roles").select("permissions").eq("org_id", org_id)
              .eq("name", u.get("role")).limit(1).execute().data) or []
        scope = ((rr[0].get("permissions") if rr else {}) or {}).get("scope")
        if scope and scope != "self":
            return u
    except Exception:
        pass
    raise HTTPException(403, "That login isn't a manager — ask a manager to approve.")


def _caller_employee_id(authorization: str, org_id: str = ORG_ID) -> str:
    """Resolve the signed-in caller's employee_id from their Supabase JWT (the kiosk/portal sends
    `Authorization: Bearer <token>`). Self-service time-clock punches are LOCKED to this id so an
    employee can only ever clock THEMSELVES — picking a name from a list no longer grants a punch
    (closes the buddy-punching hole). 401 if not signed in; 403 if the login isn't linked to an
    employee record."""
    from app.modules.core.router import _uid_from_token  # local import avoids a circular import
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "Sign in to use the time clock.")
    rows = (sb().table("app_users").select("employee_id").eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    eid = ((rows[0].get("employee_id") if rows else "") or "").strip()
    if not eid:
        raise HTTPException(403, "Your login isn't linked to an employee record. "
                                 "Ask an admin to set your Employee ID in Roles & Access.")
    return eid


@router.get("/timeclock/status")
def timeclock_status(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Is the SIGNED-IN employee currently clocked in? Identity comes from the auth token."""
    employee_id = _caller_employee_id(authorization)
    rows = (sb().table("timelog").select("*").eq("org_id", org_id).eq("employee_id", employee_id)
            .is_("clock_out", "null").order("clock_in", desc=True).limit(1).execute().data) or []
    if rows:
        e = rows[0]; e["selfie_url"] = _signed_selfie(e.get("selfie_path"))
        return {"clockedIn": True, "entry": e}
    return {"clockedIn": False, "entry": None}


@router.post("/timeclock/clock-in")
def clock_in(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Record a clock-in (one row). Identity is the SIGNED-IN employee (from the auth token) — a
    body employee_id is ignored, so you can only punch yourself. Selfie (base64) + GPS + face-match%
    are still stored for audit (defense in depth)."""
    employee_id = _caller_employee_id(authorization)
    # guard: don't open a second concurrent entry
    open_rows = (sb().table("timelog").select("id").eq("org_id", org_id).eq("employee_id", employee_id)
                 .is_("clock_out", "null").limit(1).execute().data) or []
    if open_rows:
        raise HTTPException(409, "Already clocked in — clock out first.")
    name, home_store = _emp_name(org_id, employee_id)
    now = datetime.now(timezone.utc)
    work_date = now.astimezone(_BIZ_TZ).date().isoformat()   # business-local date (not UTC)
    # Which store is this punch for? The kiosk sends the selected store; fall back to home store.
    req_store = (body.get("store_code") or "").strip() or home_store
    # Gate: home OR scheduled-today OR floater store. Anything else needs a manager override.
    if req_store:
        allowed = _allowed_clock_stores(org_id, employee_id, home_store, work_date)
        if allowed and _norm_store(req_store) not in allowed:
            return {"success": False, "needs_override": True, "store_code": req_store,
                    "allowed_stores": sorted(allowed), "home_store": home_store,
                    "message": f"You're not scheduled at {req_store} today. A manager can approve it."}
    selfie_path = _upload_selfie(org_id, employee_id, body.get("selfie"))
    row = {"org_id": org_id, "employee_id": employee_id, "employee_name": name,
           "store_code": req_store,
           "clock_in": now.isoformat(), "work_date": work_date,
           "device": body.get("device"), "selfie_path": selfie_path,
           "gps_lat": body.get("gps_lat"), "gps_lng": body.get("gps_lng"),
           "gps_accuracy_m": body.get("gps_accuracy_m"), "face_match_pct": body.get("face_match_pct")}
    r = sb().table("timelog").insert(row).execute()
    saved = r.data[0] if r.data else row
    return {"success": True, "data": {"time": _fmt_time(saved.get("clock_in")), "entry_id": saved.get("id"),
                                      "store_code": req_store}}


@router.get("/timeclock/allowed-stores")
def timeclock_allowed_stores(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The stores the signed-in employee can clock in at today (home + scheduled + floater), so the
    kiosk can show a picker instead of forcing the home store."""
    employee_id = _caller_employee_id(authorization)
    name, home_store = _emp_name(org_id, employee_id)
    work_date = datetime.now(timezone.utc).astimezone(_BIZ_TZ).date().isoformat()
    allowed = sorted(_allowed_clock_stores(org_id, employee_id, home_store, work_date))
    return {"home_store": home_store, "work_date": work_date,
            "stores": allowed or ([_norm_store(home_store)] if home_store else [])}


@router.post("/timeclock/override")
def clock_in_override(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """MANAGER override: a manager (their own token in Authorization) authorizes clocking an employee
    in at a store they're not scheduled for, and ADDS the shift to today's schedule so it's on record
    (exactly what the user asked: 'manager override + update their schedule'). Body: {employee_id,
    store_code, selfie?, gps_lat?, gps_lng?, gps_accuracy_m?, face_match_pct?, device?}."""
    mgr = _require_manager(authorization, org_id)
    employee_id = (body.get("employee_id") or "").strip()
    store_code = (body.get("store_code") or "").strip()
    if not employee_id or not store_code:
        raise HTTPException(400, "employee_id and store_code are required")
    open_rows = (sb().table("timelog").select("id").eq("org_id", org_id).eq("employee_id", employee_id)
                 .is_("clock_out", "null").limit(1).execute().data) or []
    if open_rows:
        raise HTTPException(409, "That employee is already clocked in — clock out first.")
    name, _home = _emp_name(org_id, employee_id)
    now = datetime.now(timezone.utc)
    work_date = now.astimezone(_BIZ_TZ).date().isoformat()
    # update the schedule so the store is on record for today (idempotent-ish: skip if already there)
    try:
        exists = (sb().table("shifts").select("id").eq("org_id", org_id).eq("employee_id", employee_id)
                  .eq("shift_date", work_date).eq("store_code", store_code).eq("is_deleted", False)
                  .limit(1).execute().data) or []
        if not exists:
            sb().table("shifts").insert({"org_id": org_id, "employee_id": employee_id, "employee_name": name,
                "store_code": store_code, "shift_date": work_date, "status": "scheduled", "is_deleted": False,
                "notes": f"added via clock-in override by {mgr.get('email')}"}).execute()
    except Exception:
        pass
    selfie_path = _upload_selfie(org_id, employee_id, body.get("selfie"))
    row = {"org_id": org_id, "employee_id": employee_id, "employee_name": name, "store_code": store_code,
           "clock_in": now.isoformat(), "work_date": work_date, "device": body.get("device") or "kiosk-override",
           "selfie_path": selfie_path, "gps_lat": body.get("gps_lat"), "gps_lng": body.get("gps_lng"),
           "gps_accuracy_m": body.get("gps_accuracy_m"), "face_match_pct": body.get("face_match_pct"),
           "notes": f"manager override: {mgr.get('email')}"}
    r = sb().table("timelog").insert(row).execute()
    saved = r.data[0] if r.data else row
    return {"success": True, "override_by": mgr.get("email"),
            "data": {"time": _fmt_time(saved.get("clock_in")), "entry_id": saved.get("id"), "store_code": store_code}}


@router.post("/timeclock/clock-out")
def clock_out(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Close the SIGNED-IN employee's open entry (updates the SAME row) and compute hours. Always
    scoped to the caller's own employee_id so one employee can't close another's punch."""
    employee_id = _caller_employee_id(authorization)
    entry_id = body.get("entry_id")
    q = (sb().table("timelog").select("*").eq("org_id", org_id).is_("clock_out", "null")
         .eq("employee_id", employee_id))
    if entry_id:
        q = q.eq("id", entry_id)
    rows = q.order("clock_in", desc=True).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "No open clock-in found.")
    entry = rows[0]
    now = datetime.now(timezone.utc)
    try:
        ci = datetime.fromisoformat(str(entry["clock_in"]).replace("Z", "+00:00"))
        hours = round((now - ci).total_seconds() / 3600.0, 2)
    except Exception:
        hours = None
    sb().table("timelog").update({"clock_out": now.isoformat(), "hours": hours}).eq("id", entry["id"]).execute()
    return {"success": True, "data": {"time": _fmt_time(now.isoformat()), "hours": hours,
                                      "clock_in": _fmt_time(entry.get("clock_in"))}}


@router.get("/timeclock/list")
def timeclock_list(start: str = "", end: str = "", employee_id: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Timelog entries for a date range (+ optional employee). Newest first."""
    q = sb().table("timelog").select("*").eq("org_id", org_id)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    if start:
        q = q.gte("work_date", start)
    if end:
        q = q.lte("work_date", end)
    rows = q.order("clock_in", desc=True).limit(5000).execute().data or []
    eids = scope_emp_ids(authorization, org_id)   # None = unrestricted
    if eids is not None:
        rows = [e for e in rows if str(e.get("employee_id")) in eids]
    for e in rows:
        e["selfie_url"] = _signed_selfie(e.get("selfie_path"))
    return rows


# ── face recognition (face-api.js 128-float descriptors) ──────────────────────────────────────
@router.get("/timeclock/face")
def get_face(authorization: str = Header(default=""), action: str = "", org_id: str = ORG_ID):
    """Registration status (and the descriptor itself when action=descriptor, for verify) for the
    SIGNED-IN employee — identity comes from the auth token."""
    employee_id = _caller_employee_id(authorization)
    rows = (sb().table("face_descriptors").select("*").eq("org_id", org_id)
            .eq("employee_id", employee_id).limit(1).execute().data) or []
    if not rows:
        return {"registered": False}
    if action == "descriptor":
        return {"registered": True, "descriptor": rows[0].get("descriptor")}
    return {"registered": True, "register_count": rows[0].get("register_count")}


@router.post("/timeclock/face")
def save_face(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Save (or re-register) an averaged 128-float descriptor for the SIGNED-IN employee — identity
    comes from the auth token, so you can only enroll your own face."""
    employee_id = _caller_employee_id(authorization)
    descriptor = body.get("descriptor")
    if not isinstance(descriptor, list) or len(descriptor) != 128:
        raise HTTPException(400, "a 128-float descriptor is required")
    existing = (sb().table("face_descriptors").select("id,register_count").eq("org_id", org_id)
                .eq("employee_id", employee_id).limit(1).execute().data) or []
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        sb().table("face_descriptors").update(
            {"descriptor": descriptor, "register_count": (existing[0].get("register_count") or 1) + 1,
             "updated_at": now}).eq("id", existing[0]["id"]).execute()
    else:
        sb().table("face_descriptors").insert(
            {"org_id": org_id, "employee_id": employee_id, "descriptor": descriptor,
             "register_count": 1, "registered_at": now, "updated_at": now}).execute()
    return {"ok": True, "employee_id": employee_id}


# ── payroll settings (W-4 / state) + manual hours ──────────────────────────────────────────────
@router.get("/payroll-settings/{employee_id}")
def get_payroll_settings(employee_id: str, org_id: str = ORG_ID):
    rows = (sb().table("payroll_settings").select("*").eq("org_id", org_id)
            .eq("employee_id", employee_id).limit(1).execute().data) or []
    if rows:
        return rows[0]
    return {"employee_id": employee_id, "filing_status": "Single", "allowances": 0,
            "state": "NY", "extra_withholding": 0, "skipped": False}


@router.put("/payroll-settings/{employee_id}")
def put_payroll_settings(employee_id: str, body: dict, org_id: str = ORG_ID):
    row = {"org_id": org_id, "employee_id": employee_id,
           "filing_status": body.get("filing_status") or "Single",
           "allowances": int(body.get("allowances") or 0),
           "state": (body.get("state") or "NY").upper()[:2],
           "extra_withholding": float(body.get("extra_withholding") or 0),
           "skipped": bool(body.get("skipped")),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    sb().table("payroll_settings").upsert(row, on_conflict="org_id,employee_id").execute()
    return {"ok": True, "employee_id": employee_id}


@router.get("/manual-hours")
def list_manual_hours(employee_id: str = "", start: str = "", end: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    q = sb().table("manual_hours").select("*").eq("org_id", org_id)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    if start:
        q = q.gte("work_date", start)
    if end:
        q = q.lte("work_date", end)
    rows = q.order("work_date", desc=True).limit(2000).execute().data or []
    eids = scope_emp_ids(authorization, org_id)   # None = unrestricted
    if eids is not None:
        rows = [r for r in rows if str(r.get("employee_id")) in eids]
    return rows


@router.post("/manual-hours")
def add_manual_hours(body: dict, org_id: str = ORG_ID):
    employee_id = (body.get("employee_id") or "").strip()
    reason = (body.get("reason") or "").strip()
    if not employee_id or not reason:
        raise HTTPException(400, "employee_id and reason are required")
    if body.get("hours") in (None, ""):
        raise HTTPException(400, "hours required")
    row = {"org_id": org_id, "employee_id": employee_id,
           "work_date": body.get("work_date") or datetime.now(timezone.utc).date().isoformat(),
           "hours": float(body.get("hours")), "reason": reason, "added_by": body.get("added_by")}
    r = sb().table("manual_hours").insert(row).execute()
    return r.data[0] if r.data else row


@router.delete("/manual-hours/{mid}")
def delete_manual_hours(mid: str, org_id: str = ORG_ID):
    sb().table("manual_hours").delete().eq("org_id", org_id).eq("id", mid).execute()
    return {"ok": True}


@router.get("/payroll-raw")
def payroll_raw(start: str, end: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Raw payroll inputs for a pay period — actual clocked hours (timelog) + manual adjustments +
    pay rate + W-4 settings per employee. The browser runs the tax calc (so stored figures never go
    stale when rates change), per the StoreOps payroll spec."""
    emps = (sb().table("employees").select("employee_id,name,home_store,pay_rate,is_active")
            .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    tl = (sb().table("timelog").select("employee_id,hours,clock_out,work_date")
          .eq("org_id", org_id).gte("work_date", start).lte("work_date", end).limit(20000).execute().data) or []
    mh = (sb().table("manual_hours").select("employee_id,hours")
          .eq("org_id", org_id).gte("work_date", start).lte("work_date", end).limit(5000).execute().data) or []
    ps = (sb().table("payroll_settings").select("*").eq("org_id", org_id).execute().data) or []
    settings = {s["employee_id"]: s for s in ps}
    clocked, manual = {}, {}
    for t in tl:
        if t.get("clock_out") and t.get("hours") is not None:   # only closed punches count
            clocked[t["employee_id"]] = clocked.get(t["employee_id"], 0.0) + float(t["hours"] or 0)
    for m in mh:
        manual[m["employee_id"]] = manual.get(m["employee_id"], 0.0) + float(m["hours"] or 0)
    out = []
    for e in emps:
        eid = e["employee_id"]
        ch = round(clocked.get(eid, 0.0), 2)
        mhh = round(manual.get(eid, 0.0), 2)
        if ch == 0 and mhh == 0:
            continue
        s = settings.get(eid) or {}
        out.append({"employee_id": eid, "name": e.get("name"), "store": e.get("home_store"),
                    "pay_rate": float(e.get("pay_rate") or 0), "clocked_hours": ch, "manual_hours": mhh,
                    "total_hours": round(ch + mhh, 2),
                    "settings": {"filing_status": s.get("filing_status") or "Single",
                                 "allowances": s.get("allowances") or 0, "state": s.get("state") or "NY",
                                 "extra_withholding": float(s.get("extra_withholding") or 0),
                                 "skipped": bool(s.get("skipped"))}})
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        out = [r for r in out if in_keyset(ks, r.get("store"))]
    out.sort(key=lambda r: r["name"] or "")
    return {"start": start, "end": end, "rows": out}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# ORG HIERARCHY (migration 050) — a configurable tree of org units with user-defined levels.
# Stores/employees attach via org_unit_id; a manager assigned to a node sees that node's subtree.
# Span resolution returns store_codes that drop into the existing per-store rollups. RLS is open_all,
# so this is DEFAULT-SCOPING (not a security boundary) until the Phase 5 backend enforcement lands.
# ═══════════════════════════════════════════════════════════════════════════════════════════════

def _span_codes(rows) -> list:
    """Dedup + clean store_codes returned by the span/subtree RPCs."""
    return sorted({(r.get("store_code") or "").strip() for r in (rows or [])
                   if (r.get("store_code") or "").strip()})


def _market_store_codes(org_id: str, market: str) -> set:
    """store_codes in a market — org-tree-INDEPENDENT (straight off the store list)."""
    m = (market or "").strip().upper()
    if not m:
        return set()
    rows = sb().table("stores").select("store_code,market").eq("org_id", org_id).execute().data or []
    return {str(s.get("store_code")).strip() for s in rows
            if s.get("store_code") and str(s.get("market") or "").strip().upper() == m}


def _login_extra_codes(au: dict, org_id: str) -> set:
    """store_codes implied by an app_user's market + pinned store(s) — the org-tree-independent span,
    so a market/store manager scopes correctly even before the org units/managers are wired."""
    codes: set = set()
    if not au:
        return codes
    for mkt in str(au.get("market") or "").split(","):
        codes |= _market_store_codes(org_id, mkt)
    if au.get("store_code"):
        codes.add(str(au["store_code"]).strip())
    for sc in (au.get("store_codes") or []):
        if sc and str(sc).strip():
            codes.add(str(sc).strip())
    return {c for c in codes if c}


def _caller_app_user(authorization: str, org_id: str = ORG_ID) -> dict:
    """The signed-in caller's app_user row (role/employee_id/market/store_code/store_codes), or {}."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        return {}
    rows = (sb().table("app_users").select("role,employee_id,market,store_code,store_codes")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    return rows[0] if rows else {}


def _role_scope(org_id: str, role: str) -> str:
    if not role:
        return "all"
    try:
        rr = sb().table("roles").select("permissions").eq("org_id", org_id).eq("name", role).limit(1).execute().data or []
        return (((rr[0].get("permissions") or {}).get("scope")) if rr else "all") or "all"
    except Exception:
        return "all"


def _caller_span_codes(authorization: str, org_id: str = ORG_ID) -> list:
    """store_codes the SIGNED-IN manager may see: the org-unit subtree(s) they manage UNION the
    market + store(s) pinned on their login (so a market manager resolves their stores even before
    the org tree is wired). An individual contributor (scope 'self') has no team → []. Empty list =
    no resolvable scope."""
    au = _caller_app_user(authorization, org_id)
    if not au:
        return []
    if _role_scope(org_id, (au.get("role") or "").strip()) == "self":
        return []
    codes: set = set()
    eid = (au.get("employee_id") or "").strip()
    if eid:
        rows = sb().rpc("org_span_for_manager", {"p_org_id": org_id, "p_employee_id": eid}).execute().data
        codes |= set(_span_codes(rows))
    codes |= _login_extra_codes(au, org_id)
    return sorted(codes)


def _unit_store_codes(org_id: str, unit_id: str) -> list:
    """store_codes under a chosen unit's subtree (for a manager/admin who picks a node)."""
    rows = sb().rpc("org_store_codes_for_unit", {"p_org_id": org_id, "p_unit_id": unit_id}).execute().data
    return _span_codes(rows)


@router.get("/org/tree")
def org_tree(org_id: str = ORG_ID):
    """Full org tree for the admin page: levels + units (with parent/level/store_count/managers) +
    the stores not yet placed in any unit."""
    levels = sb().table("org_levels").select("*").eq("org_id", org_id).order("rank").execute().data or []
    units  = sb().table("org_units").select("*").eq("org_id", org_id).order("sort_order").execute().data or []
    mgrs   = sb().table("org_managers").select("*").eq("org_id", org_id).execute().data or []
    stores = sb().table("stores").select("store_code,address,market,org_unit_id").eq("org_id", org_id).execute().data or []
    emps   = sb().table("employees").select("employee_id,name").eq("org_id", org_id).execute().data or []
    name_by = {e.get("employee_id"): e.get("name") for e in emps if e.get("employee_id")}
    store_cnt, mgr_by = {}, {}
    for s in stores:
        u = s.get("org_unit_id")
        if u:
            store_cnt[u] = store_cnt.get(u, 0) + 1
    for m in mgrs:
        mgr_by.setdefault(m.get("unit_id"), []).append(
            {"employee_id": m.get("employee_id"), "name": name_by.get(m.get("employee_id"), m.get("employee_id"))})
    for u in units:
        u["store_count"] = store_cnt.get(u["id"], 0)
        u["managers"] = mgr_by.get(u["id"], [])
    unassigned = sorted([s for s in stores if not s.get("org_unit_id")],
                        key=lambda s: (s.get("market") or "", s.get("address") or ""))
    return {"levels": levels, "units": units, "unassigned_stores": unassigned}


@router.post("/org/seed")
def org_seed(org_id: str = ORG_ID):
    """(Re)build Company -> Market -> stores from storeops.stores. Idempotent; manual placements survive."""
    res = sb().rpc("seed_org_from_stores", {"p_org_id": org_id}).execute()
    return {"ok": True, "result": getattr(res, "data", None)}


# Standard corporate ladder + departments — the one-click scaffold.
_STD_LEVELS = ["Executive", "Director", "Regional Manager", "District Manager", "Store Manager", "Sales Consultant"]
_STD_DEPTS = ["Finance", "Human Resources", "Marketing", "IT", "Inventory", "Operations", "Sales"]


@router.post("/org/build-standard")
def org_build_standard(org_id: str = ORG_ID):
    """⚠️ REPLACES the org structure with a standard corporate org: Company (Executive) → departments
    (Director) → from existing markets/stores under Sales: Region (Regional Manager) → District
    (District Manager) → Store (Store Manager); sales consultants are the employees under each store
    (via home_store). The user then just assigns the people (CEO/COO/CFO on Company, Directors on
    departments, etc.). Wipes the current units/levels/manager-assignments first (store + employee
    placements are detached, not deleted)."""
    c = sb()
    # 1. detach stores + employees from any unit (avoid FK violations on the wipe)
    unit_ids = [u["id"] for u in (c.table("org_units").select("id").eq("org_id", org_id).execute().data or [])]
    if unit_ids:
        for i in range(0, len(unit_ids), 100):
            chunk = unit_ids[i:i + 100]
            c.table("stores").update({"org_unit_id": None}).in_("org_unit_id", chunk).execute()
            c.table("employees").update({"org_unit_id": None}).in_("org_unit_id", chunk).execute()
    # 2. wipe units (cascades managers + children) then levels
    c.table("org_units").delete().eq("org_id", org_id).execute()
    c.table("org_levels").delete().eq("org_id", org_id).execute()

    # 3. the 6-level corporate ladder
    lvl = {}
    for rank, name in enumerate(_STD_LEVELS):
        r = c.table("org_levels").insert({"org_id": org_id, "name": name, "rank": rank}).execute()
        lvl[name] = r.data[0]["id"]

    def mk(name, level, parent_id, code=None):
        r = c.table("org_units").insert({"org_id": org_id, "name": name, "level_id": lvl[level],
                                         "parent_id": parent_id, "code": code}).execute()
        return r.data[0]["id"]

    # 4. Company (Executive) + departments (Director)
    root = mk("Company", "Executive", None, "__ROOT__")
    dept = {d: mk(d, "Director", root, f"dept:{d.lower()}") for d in _STD_DEPTS}

    # 5. retail line under Sales, from the existing markets/stores
    sales = dept["Sales"]
    stores = c.table("stores").select("store_code,address,market").eq("org_id", org_id).execute().data or []
    district_by_market = {}
    placed = 0
    for s in stores:
        code = (s.get("store_code") or "").strip()
        if not code:
            continue
        mkt = (s.get("market") or "").strip() or "Unassigned"
        if mkt not in district_by_market:
            reg = mk(f"{mkt} Region", "Regional Manager", sales, f"region:{mkt.lower()}")
            district_by_market[mkt] = mk(f"{mkt} District", "District Manager", reg, f"district:{mkt.lower()}")
        store_node = mk(s.get("address") or code, "Store Manager", district_by_market[mkt])
        c.table("stores").update({"org_unit_id": store_node}).eq("store_code", code).execute()
        placed += 1

    return {"ok": True, "levels": _STD_LEVELS, "departments": _STD_DEPTS,
            "markets": len(district_by_market), "stores_placed": placed}


# ── levels ───────────────────────────────────────────────────────────────────────────────────────
@router.get("/org/levels")
def org_levels_list(org_id: str = ORG_ID):
    return sb().table("org_levels").select("*").eq("org_id", org_id).order("rank").execute().data or []


@router.post("/org/levels")
def org_level_create(body: dict, org_id: str = ORG_ID):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Level name required.")
    rank = body.get("rank")
    if rank is None:
        top = sb().table("org_levels").select("rank").eq("org_id", org_id).order("rank", desc=True).limit(1).execute().data or []
        rank = (top[0]["rank"] + 1) if top else 0
    row = {"org_id": org_id, "name": name, "rank": int(rank)}
    r = sb().table("org_levels").insert(row).execute()
    return r.data[0] if r.data else row


@router.put("/org/levels/{level_id}")
def org_level_update(level_id: int, body: dict, org_id: str = ORG_ID):
    upd = {}
    if "name" in body:
        upd["name"] = (body.get("name") or "").strip()
    if "rank" in body:
        upd["rank"] = int(body.get("rank"))
    if upd:
        sb().table("org_levels").update(upd).eq("id", level_id).execute()
    return {"ok": True}


@router.delete("/org/levels/{level_id}")
def org_level_delete(level_id: int, org_id: str = ORG_ID):
    used = sb().table("org_units").select("id").eq("org_id", org_id).eq("level_id", level_id).limit(1).execute().data or []
    if used:
        raise HTTPException(409, "This level is in use by one or more units — reassign them first.")
    sb().table("org_levels").delete().eq("id", level_id).execute()
    return {"ok": True}


# ── units ────────────────────────────────────────────────────────────────────────────────────────
@router.post("/org/units")
def org_unit_create(body: dict, org_id: str = ORG_ID):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Unit name required.")
    row = {"org_id": org_id, "name": name, "parent_id": body.get("parent_id"),
           "level_id": body.get("level_id"), "sort_order": body.get("sort_order") or 0}
    r = sb().table("org_units").insert(row).execute()
    return r.data[0] if r.data else row


@router.put("/org/units/{unit_id}")
def org_unit_update(unit_id: str, body: dict, org_id: str = ORG_ID):
    """Rename / re-level / reorder / MOVE (set parent_id). Guards against cycles (can't move a unit
    under itself or a descendant)."""
    upd = {}
    for k in ("name", "parent_id", "level_id", "sort_order", "is_active"):
        if k in body:
            upd[k] = body.get(k)
    if "name" in upd:
        upd["name"] = (upd["name"] or "").strip()
    new_parent = upd.get("parent_id")
    if new_parent:
        if new_parent == unit_id:
            raise HTTPException(400, "A unit can't be its own parent.")
        sub = sb().rpc("org_subtree", {"p_org_id": org_id, "p_unit_id": unit_id}).execute().data or []
        if any(n.get("id") == new_parent for n in sub):
            raise HTTPException(400, "Can't move a unit under its own descendant.")
    upd["updated_at"] = datetime.now(timezone.utc).isoformat()
    sb().table("org_units").update(upd).eq("id", unit_id).execute()
    return {"ok": True}


@router.delete("/org/units/{unit_id}")
def org_unit_delete(unit_id: str, org_id: str = ORG_ID):
    """Delete a unit + its descendants (cascade). Stores/employees in the subtree are detached
    (org_unit_id -> NULL) FIRST so they become 'unassigned' rather than violating the FK."""
    sub = sb().rpc("org_subtree", {"p_org_id": org_id, "p_unit_id": unit_id}).execute().data or []
    ids = [n["id"] for n in sub if n.get("id")]
    if ids:
        sb().table("stores").update({"org_unit_id": None}).in_("org_unit_id", ids).execute()
        sb().table("employees").update({"org_unit_id": None}).in_("org_unit_id", ids).execute()
    sb().table("org_units").delete().eq("id", unit_id).execute()
    return {"ok": True}


# ── managers ───────────────────────────────────────────────────────────────────────────────────
@router.post("/org/units/{unit_id}/managers")
def org_unit_add_manager(unit_id: str, body: dict, org_id: str = ORG_ID):
    eid = (body.get("employee_id") or "").strip()
    if not eid:
        raise HTTPException(400, "employee_id required.")
    existing = (sb().table("org_managers").select("id").eq("org_id", org_id).eq("unit_id", unit_id)
                .eq("employee_id", eid).limit(1).execute().data) or []
    if existing:
        return existing[0]
    row = {"org_id": org_id, "unit_id": unit_id, "employee_id": eid}
    r = sb().table("org_managers").insert(row).execute()
    return r.data[0] if r.data else row


@router.delete("/org/units/{unit_id}/managers/{employee_id}")
def org_unit_remove_manager(unit_id: str, employee_id: str, org_id: str = ORG_ID):
    sb().table("org_managers").delete().eq("unit_id", unit_id).eq("employee_id", employee_id).execute()
    return {"ok": True}


# ── attach a store to a unit (or unassign with unit_id=null) ─────────────────────────────────────
@router.put("/org/stores/{store_code}/unit")
def org_assign_store(store_code: str, body: dict, org_id: str = ORG_ID):
    sb().table("stores").update({"org_unit_id": body.get("unit_id")}).eq("store_code", store_code).execute()
    return {"ok": True}


# ── EMPLOYEE org chart: where each person sits in the tree ────────────────────────────────────────
@router.get("/org/employees")
def org_employees(include_inactive: bool = False, org_id: str = ORG_ID):
    """Every employee with the unit they roll up to — a DIRECT employees.org_unit_id wins, else their
    home_store -> stores.org_unit_id. Plus is_manager (assigned to any node). Powers the org chart."""
    q = sb().table("employees").select("id,employee_id,name,home_store,role,is_active,org_unit_id").eq("org_id", org_id)
    if not include_inactive:
        q = q.eq("is_active", True)
    emps = q.order("name").execute().data or []
    stores = sb().table("stores").select("store_code,address,org_unit_id").eq("org_id", org_id).execute().data or []
    unit_by_store = {}
    for s in stores:
        u = s.get("org_unit_id")
        if not u:
            continue
        for k in (s.get("store_code"), s.get("address")):
            if k:
                unit_by_store[str(k).strip().upper()] = u
    mgr_ids = {m.get("employee_id") for m in (sb().table("org_managers").select("employee_id").eq("org_id", org_id).execute().data or [])
               if m.get("employee_id")}
    out = []
    for e in emps:
        hs = str(e.get("home_store") or "").strip().upper()
        out.append({
            "id": e.get("id"),                                         # stable PK — assign keys on this
            "employee_id": e.get("employee_id"), "name": e.get("name"),
            "home_store": e.get("home_store"), "role": e.get("role"),
            "is_active": e.get("is_active", True),
            "org_unit_id": e.get("org_unit_id"),                       # direct placement (override)
            "resolved_unit_id": e.get("org_unit_id") or unit_by_store.get(hs),  # where they show
            "placed_by": "direct" if e.get("org_unit_id") else ("home_store" if unit_by_store.get(hs) else None),
            "is_manager": e.get("employee_id") in mgr_ids,
        })
    return {"employees": out}


@router.put("/org/employees/{row_id}/unit")
def org_assign_employee(row_id: str, body: dict, org_id: str = ORG_ID):
    """Place an employee directly on a unit (overrides the home-store rollup — for managers / roving /
    overhead staff). unit_id=null clears the override so they fall back to their home store's unit.

    Keys on the employees PRIMARY KEY (id), NOT the optional business employee_id — that field is NULL
    for some staff, so keying on it made unplaced/no-Emp-ID employees impossible to assign (the update
    matched no row and silently no-op'd)."""
    sb().table("employees").update({"org_unit_id": body.get("unit_id")}) \
        .eq("id", row_id).eq("org_id", org_id).execute()
    return {"ok": True}


# ── the signed-in caller's span (powers the portal "My Team" tab + default frontend scoping) ──────
@router.get("/org/my-span")
def org_my_span(authorization: str = Header(default=""), org_id: str = ORG_ID):
    au = _caller_app_user(authorization, org_id)
    codes = _caller_span_codes(authorization, org_id)   # org tree UNION market/store from the login
    eid = (au.get("employee_id") or "").strip()
    uids = []
    if eid:
        mrows = sb().table("org_managers").select("unit_id").eq("org_id", org_id).eq("employee_id", eid).execute().data or []
        uids = [m["unit_id"] for m in mrows if m.get("unit_id")]
    units = (sb().table("org_units").select("id,name,level_id").eq("org_id", org_id).in_("id", uids).execute().data or []) if uids else []
    return {"employee_id": eid, "store_codes": codes, "units": units, "is_manager": bool(codes)}


@router.get("/org/my-team")
def org_my_team(authorization: str = Header(default=""), unit_id: str = "", org_id: str = ORG_ID):
    """The caller's TEAM as the org subtree below the unit(s) they manage: each node = an org unit
    with its manager(s), the employees that roll up to it (via org_unit_id or home_store), and its
    child units (recursive). Drives the hierarchical 'My Team' — regional → market managers → their
    employees → … at any depth. Admins / the /storeops/team picker may pass a unit_id to view a node;
    a manager is restricted to their own assigned units."""
    c = sb()
    try:
        eid = _caller_employee_id(authorization)
    except HTTPException:
        eid = ""
    my_unit_ids = []
    if eid:
        mrows = c.table("org_managers").select("unit_id").eq("org_id", org_id).eq("employee_id", eid).execute().data or []
        my_unit_ids = [m["unit_id"] for m in mrows if m.get("unit_id")]
    unrestricted = caller_scope(authorization, org_id) is None   # admin / 'all' / enforcement off
    if unit_id and (unrestricted or unit_id in my_unit_ids):
        roots = [unit_id]
    else:
        roots = my_unit_ids
    if not roots:
        return {"is_manager": False, "tree": []}
    levels = c.table("org_levels").select("id,name,rank").eq("org_id", org_id).execute().data or []
    lvl_name = {l["id"]: l.get("name") for l in levels}
    lvl_rank = {l["id"]: l.get("rank") for l in levels}
    units = c.table("org_units").select("id,name,parent_id,level_id,sort_order").eq("org_id", org_id).execute().data or []
    unit_by = {u["id"]: u for u in units}
    children = {}
    for u in units:
        children.setdefault(u.get("parent_id"), []).append(u)
    for k in children:
        children[k].sort(key=lambda u: (u.get("sort_order") or 0, u.get("name") or ""))
    mrows_all = c.table("org_managers").select("unit_id,employee_id").eq("org_id", org_id).execute().data or []
    emps = c.table("employees").select("employee_id,name,role,home_store,org_unit_id,is_active").eq("org_id", org_id).execute().data or []
    name_by_eid = {e.get("employee_id"): e.get("name") for e in emps if e.get("employee_id")}
    mgr_eids = {m.get("employee_id") for m in mrows_all}
    mgr_by_unit = {}
    for m in mrows_all:
        mgr_by_unit.setdefault(m.get("unit_id"), []).append(
            {"employee_id": m.get("employee_id"), "name": name_by_eid.get(m.get("employee_id"), m.get("employee_id"))})
    stores = c.table("stores").select("store_code,org_unit_id").eq("org_id", org_id).execute().data or []
    unit_by_storecode = {(s.get("store_code") or "").strip().upper(): s.get("org_unit_id")
                         for s in stores if (s.get("store_code") or "").strip()}
    emp_by_unit = {}
    for e in emps:
        if not e.get("is_active", True):
            continue
        u = e.get("org_unit_id") or unit_by_storecode.get((e.get("home_store") or "").strip().upper())
        if not u:
            continue
        emp_by_unit.setdefault(u, []).append(
            {"employee_id": e.get("employee_id"), "name": e.get("name"), "role": e.get("role"),
             "home_store": e.get("home_store"), "is_manager": e.get("employee_id") in mgr_eids})

    def build(uid, depth=0):
        u = unit_by.get(uid)
        if not u or depth > 12:
            return None
        kids = [build(ch["id"], depth + 1) for ch in children.get(uid, [])]
        return {"unit_id": uid, "name": u.get("name"),
                "level": lvl_name.get(u.get("level_id")), "rank": lvl_rank.get(u.get("level_id")),
                "managers": mgr_by_unit.get(uid, []),
                "employees": sorted(emp_by_unit.get(uid, []), key=lambda x: (x.get("name") or "")),
                "children": [k for k in kids if k]}

    tree = [t for t in (build(r) for r in roots) if t]
    return {"is_manager": True, "tree": tree}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# PHASE 5 — span ENFORCEMENT for scoped reads. GATED on the RBAC master switch (app_config.
# rbac_enabled): when login enforcement is OFF (today's default) this is a strict NO-OP, so the app
# keeps working exactly as before. When ON, a non-admin manager's reads are filtered to the stores in
# their org-unit span; an 'all'-scope (admin) role and any caller we can't identify stay unrestricted.
# This is application-layer scoping; full DB-level lockdown (RLS keyed on auth.uid) is a later step.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _rbac_enabled(org_id: str = ORG_ID) -> bool:
    try:
        rows = sb().table("app_config").select("rbac_enabled").eq("id", 1).limit(1).execute().data or []
        return bool(rows and rows[0].get("rbac_enabled"))
    except Exception:
        return False


def caller_scope(authorization: str, org_id: str = ORG_ID):
    """How to scope a read for the signed-in caller.
    Returns None  -> UNRESTRICTED (enforcement off, no/invalid token, unprovisioned, or 'all' scope).
    Returns a SET of store_codes the caller may see otherwise (a manager's span; possibly empty)."""
    if not _rbac_enabled(org_id):
        return None
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        return None
    rows = sb().table("app_users").select("role,employee_id,market,store_code,store_codes").eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data or []
    if not rows:
        return None
    u = rows[0]
    scope = _role_scope(org_id, (u.get("role") or "").strip())
    if scope == "all":
        return None
    eid = (u.get("employee_id") or "").strip()
    span = set()
    if eid:
        spans = sb().rpc("org_span_for_manager", {"p_org_id": org_id, "p_employee_id": eid}).execute().data
        span = set(_span_codes(spans))
    # Market/store managers not yet wired into the org tree fall back to the market + store(s) pinned
    # on their app_user, so scoping works before the org units/managers are assigned. Reps ('self')
    # are pinned to their own store by the frontend, so their read scope stays empty here.
    if scope != "self":
        span |= _login_extra_codes(u, org_id)
    return span


def scope_keyset(authorization: str, org_id: str = ORG_ID):
    """None = unrestricted; else a set of UPPER store keys (store_codes + their addresses) the caller
    may see — so rows whose store field is EITHER a code or an address still match."""
    codes = caller_scope(authorization, org_id)
    if codes is None:
        return None
    keys = {c.strip().upper() for c in codes}
    if keys:
        meta = sb().table("stores").select("store_code,address").eq("org_id", org_id).execute().data or []
        for s in meta:
            sc = str(s.get("store_code") or "").strip().upper()
            if sc in keys:
                ad = str(s.get("address") or "").strip().upper()
                if ad:
                    keys.add(ad)
    return keys


def in_keyset(keyset, *vals) -> bool:
    """True when unrestricted (keyset None) or any of vals matches an allowed store key."""
    if keyset is None:
        return True
    return any(str(v or "").strip().upper() in keyset for v in vals)


def scope_emp_ids(authorization: str, org_id: str = ORG_ID):
    """employee_ids in the caller's span (None = UNRESTRICTED). For employee-keyed tables
    (time-off, swaps, manual-hours, timeclock) that carry no store column — resolves each
    employee to their home_store and keeps those inside the manager's span keyset."""
    ks = scope_keyset(authorization, org_id)
    if ks is None:
        return None
    emps = sb().table("employees").select("employee_id,home_store").eq("org_id", org_id).execute().data or []
    return {str(e.get("employee_id")) for e in emps
            if e.get("employee_id") and in_keyset(ks, e.get("home_store"))}
