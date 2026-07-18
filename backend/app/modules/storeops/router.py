"""StoreOps API Router — /api/v1/storeops/*"""
import base64
import os
import requests
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Header
from app.core.database import get_supabase
from app.core.config import settings
from app.modules.storeops.pto_accrual import (
    DEFAULT_CONFIG as PTO_DEFAULT_CONFIG,
    resolve_effective_config as pto_resolve_effective_config,
    month_bounds as pto_month_bounds,
    hours_worked_from_shifts as pto_hours_worked_from_shifts,
    taken_hours_from_time_off as pto_taken_hours_from_time_off,
    compute_pto,
    ledger_rows as pto_ledger_rows,
    expense_cells_from_stores as pto_expense_cells_from_stores,
)
from app.modules.storeops.payroll_expenses import (
    DEFAULT_TAX_CONFIG as PAYEX_DEFAULT_TAX_CONFIG,
    CALC_METHODS as PAYEX_CALC_METHODS,
    ITEM_SCOPES as PAYEX_ITEM_SCOPES,
    resolve_tax_config as payex_resolve_tax_config,
    wages_by_store_from_hours as payex_wages_by_store,
    headcount_by_store_from_hours as payex_headcount_by_store,
    compute_payroll_tax,
    compute_expense_items,
    rollup_cells as payex_rollup_cells,
    tax_ledger_rows as payex_tax_ledger_rows,
    expense_ledger_rows as payex_expense_ledger_rows,
    gross_payroll_cells as payex_gross_payroll_cells,
    gross_payroll_ledger_rows as payex_gross_payroll_ledger_rows,
)

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


def _biz_tz_for(org_id: str):
    """The tenant's OWN business timezone when set (storeops.tenants.timezone, mig 085 — added for
    Luxelink's pay-period setup but never wired to the clock until now), else the house-wide
    settings.BUSINESS_TZ default. Every 'business-local work date' (clock-in/out bucketing, the
    closing-gate's 'today', the force-clockout sweep) should use THIS, not the bare global _BIZ_TZ,
    so a tenant outside America/New_York doesn't get punches bucketed onto the wrong calendar day.
    Safe/additive: no tenant has this column set yet (it's not exposed in any settings UI), so this
    is a no-op today and Boost's behavior is unchanged — it only takes effect once a value is set.
    Any lookup/parse failure falls back to the global default (never breaks the clock over a
    config/migration gap)."""
    try:
        t = (sb().table("tenants").select("timezone").eq("org_id", org_id).limit(1).execute().data) or []
        tz = ((t[0].get("timezone") if t else "") or "").strip()
        if tz:
            return ZoneInfo(tz)
    except Exception:
        pass
    return _BIZ_TZ


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
    # Hours-budget guard (mig 087): block scheduling past the store's weekly budget unless a DM
    # approved an override for that store+week. Only enforced when a budget is set for the store;
    # any lookup failure degrades to "allow" so scheduling never breaks on a config/migration gap.
    _enforce_hours_budget(shift.get("org_id") or org_id, shift, exclude_id=None)
    # Stamp org_id so the row survives the org-scoped read filter on GET /shifts.
    # (shifts.org_id has NO column default → an unstamped insert lands NULL and vanishes.)
    shift = {**shift, "org_id": shift.get("org_id") or org_id}
    r = sb().table("shifts").insert(shift).execute()
    return r.data[0] if r.data else shift

@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: int, updates: dict, org_id: str = ORG_ID):
    """Update a shift. org_id-scoped so a foreign (guessable BIGSERIAL) shift_id is a no-op instead
    of a cross-tenant write — this previously took NO org filter at all."""
    updates = {k: v for k, v in updates.items() if k not in ("org_id", "id")}
    r = sb().table("shifts").update(updates).eq("id", shift_id).eq("org_id", org_id).execute()
    if not r.data:
        raise HTTPException(404, "shift not found")
    return r.data[0]

@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int, org_id: str = ORG_ID):
    """org_id-scoped so a foreign shift_id is a no-op instead of a cross-tenant delete."""
    sb().table("shifts").delete().eq("id", shift_id).eq("org_id", org_id).execute()
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
    oid = request.get("org_id") or org_id
    # Dedupe: a re-submitted request for the SAME employee + exact date range must not leave a stale
    # active copy behind. A surviving duplicate 'approved' row is what caused "voided but still can't
    # be scheduled" (the void denied only one copy). Retire prior active copies before inserting.
    if status in ("approved", "pending"):
        try:
            (sb().table("time_off_requests").update({"status": "denied"})
             .eq("org_id", oid).eq("employee_id", str(request.get("employee_id")))
             .eq("start_date", request.get("start_date")).eq("end_date", request.get("end_date"))
             .in_("status", ["approved", "pending"]).execute())
        except Exception:
            pass
    # Stamp org_id (no column default) so the request survives the org-scoped GET /time-off filter.
    row = {**request, "status": status, "org_id": oid}
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
def update_time_off(request_id: int, updates: dict, org_id: str = ORG_ID):
    """org_id-scoped so a foreign request_id is a no-op. The org_id query param was already present
    but wasn't actually applied to this UPDATE — a cross-tenant write hole."""
    client = sb()
    updates = {k: v for k, v in updates.items() if k not in ("org_id", "id")}
    r = client.table("time_off_requests").update(updates).eq("id", request_id).eq("org_id", org_id).execute()
    row = (r.data or [None])[0]
    new_status = str(updates.get("status") or (row or {}).get("status") or "").lower()
    # Voiding/revoking must clear the WHOLE block: a duplicate 'approved' copy for the same
    # employee+dates would otherwise keep blocking scheduling (the reported bug). Cascade the revoke
    # to every sibling approved/pending copy of the same request.
    if row and new_status in ("denied", "cancelled", "voided", "rejected"):
        try:
            (client.table("time_off_requests").update({"status": "denied"})
             .eq("org_id", row.get("org_id") or org_id)
             .eq("employee_id", str(row.get("employee_id")))
             .eq("start_date", row.get("start_date")).eq("end_date", row.get("end_date"))
             .in_("status", ["approved", "pending"]).execute())
        except Exception:
            pass
    return row or updates


@router.post("/time-off/reconcile-duplicates")
def reconcile_timeoff_duplicates(org_id: str = ORG_ID):
    """Idempotent cleanup for the "voided but still can't be scheduled" backlog: when a time-off was
    voided (a 'denied' row exists) but a duplicate 'approved'/'pending' copy for the SAME employee +
    dates survived and keeps blocking scheduling, deny the surviving copy too. Acts ONLY where a denied
    sibling proves the void intent — never denies a standalone approval (e.g. genuine approved PTO)."""
    client = sb()
    rows = (client.table("time_off_requests")
            .select("id,employee_id,start_date,end_date,status")
            .eq("org_id", org_id).limit(20000).execute().data) or []
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        groups[(str(r.get("employee_id")), str(r.get("start_date")), str(r.get("end_date")))].append(r)
    fixed = []
    for g in groups.values():
        statuses = {str(x.get("status") or "").lower() for x in g}
        if "denied" in statuses:
            for x in g:
                if str(x.get("status") or "").lower() in ("approved", "pending"):
                    client.table("time_off_requests").update({"status": "denied"}).eq("id", x["id"]).execute()
                    fixed.append(x["id"])
    return {"ok": True, "reconciled": len(fixed), "ids": fixed}
    return r.data[0] if r.data else updates

@router.get("/payroll")
def get_payroll(month: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Returns scheduled vs actual hours per employee for payroll"""
    lo = hi = None
    q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
    if month:
        # Exclusive upper bound = first day of the next month. (The old "{month}-32"
        # hack 500s on a DATE column because 2026-06-32 isn't a valid date.)
        parts = str(month).split("-")
        y, m = int(parts[0]), int(parts[1])
        lo, hi = f"{month}-01", (f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01")
        q = q.gte("shift_date", lo).lt("shift_date", hi)
    shifts = q.execute().data or []
    employees = sb().table("employees").select("id,name,employee_id,pay_rate,home_store").eq("org_id", org_id).eq("is_active", True).execute().data or []
    
    emp_map = {e["employee_id"]: e for e in employees}
    summary = {}
    # employee_id -> {store_code: hours-weight}. RULE FIVE (§3d) store filter: a floater's row must
    # attribute to the store they actually WORKED THE MOST this month, not just whichever shift the
    # DB happened to return first (the old behavior) or their static home_store.
    store_hours: dict = {}
    # employee_id -> {shift_date already represented by a shift row}, so the timelog fallback below
    # never double-counts a day that's already schedule-tracked.
    shift_days: dict = {}
    for s in shifts:
        eid = s.get("employee_id")
        emp = emp_map.get(eid, {})
        if eid not in summary:
            summary[eid] = {
                "employee_id": eid,
                "name": s.get("employee_name") or emp.get("name", ""),
                "store": "",  # filled below from store_hours (dominant store), home_store fallback
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
        st = (s.get("store_code") or "").strip()
        if st:
            sh = store_hours.setdefault(eid, {})
            sh[st] = sh.get(st, 0.0) + sched + act
        if eid:
            shift_days.setdefault(eid, set()).add(str(s.get("shift_date") or "")[:10])

    # UNIVERSAL FALLBACK (2026-07-18, payroll data-flow audit — luxelink showed employees+shifts+rates
    # but an empty Payroll Report for periods where reps clock in via the kiosk without a formal
    # schedule entered): a real clock-in/out is "existing platform data" this report was silently
    # dropping whenever no shifts row existed for that employee/day. ADDITIVE ONLY — a day already
    # covered by a shift row is untouched (byte-identical for any tenant whose hours are already
    # schedule-tracked, which is the house/Boost pattern today); only days with a clock punch and NO
    # matching shift gain a row, using clocked hours as actual (no schedule existed, so
    # scheduled_hours/scheduled_pay correctly stay 0 for that portion).
    if lo and hi:
        tl = (sb().table("timelog").select("employee_id,employee_name,hours,clock_out,work_date,store_code")
              .eq("org_id", org_id).gte("work_date", lo).lt("work_date", hi).limit(20000).execute().data) or []
        for t in tl:
            if not (t.get("clock_out") and t.get("hours") is not None):
                continue   # only CLOSED punches count (matches /payroll-raw's own rule)
            eid = t.get("employee_id")
            wd = str(t.get("work_date") or "")[:10]
            if not eid or not wd or wd in shift_days.get(eid, set()):
                continue   # already represented by a shift that day -> never double-count
            emp = emp_map.get(eid, {})
            if eid not in summary:
                summary[eid] = {
                    "employee_id": eid,
                    "name": t.get("employee_name") or emp.get("name", ""),
                    "store": "",
                    "pay_rate": float(emp.get("pay_rate") or 0),
                    "scheduled_hours": 0,
                    "actual_hours": 0,
                    "shifts": 0,
                }
            hrs = float(t.get("hours") or 0)
            summary[eid]["actual_hours"] += hrs
            st = (t.get("store_code") or "").strip()
            if st:
                sh = store_hours.setdefault(eid, {})
                sh[st] = sh.get(st, 0.0) + hrs

    rows = list(summary.values())
    for r in rows:
        eid = r["employee_id"]
        sh = store_hours.get(eid)
        r["store"] = (max(sh.items(), key=lambda kv: kv[1])[0] if sh
                      else (emp_map.get(eid, {}).get("home_store") or ""))
        r["scheduled_pay"] = round(r["scheduled_hours"] * r["pay_rate"], 2)
        r["actual_pay"]    = round(r["actual_hours"] * r["pay_rate"], 2)
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store"))]
    return sorted(rows, key=lambda x: x["name"])


@router.get("/payroll-by-store")
def get_payroll_by_store(month: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-STORE payroll for a month, for the Store Expenses 'Employee Salaries' auto-fill.

    For each shift in the month, hours = actual_hours where clocked else scheduled_hours (SAME basis
    as /payroll, so the numbers reconcile), pay = hours * the employee's pay_rate, attributed to the
    shift's own store_code (a floater's hours land at the store they worked). Returns one row per store:
    {store_code, hours, amount}."""
    lo = hi = None
    q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
    if month:
        # Exclusive upper bound = first day of next month (avoids the invalid "{month}-32" DATE cast).
        parts = str(month).split("-")
        y, m = int(parts[0]), int(parts[1])
        lo, hi = f"{month}-01", (f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01")
        q = q.gte("shift_date", lo).lt("shift_date", hi)
    shifts = q.execute().data or []
    # All employees (active OR not) — a terminated rep who worked this month still earns; rate=0 if unknown.
    employees = sb().table("employees").select("employee_id,pay_rate").eq("org_id", org_id).execute().data or []
    rate_map = {e.get("employee_id"): float(e.get("pay_rate") or 0) for e in employees}

    by_store = {}
    shift_days: dict = {}   # employee_id -> {shift_date} already represented by a shift row
    for s in shifts:
        store = (s.get("store_code") or "").strip()
        eid = s.get("employee_id")
        if eid:
            shift_days.setdefault(eid, set()).add(str(s.get("shift_date") or "")[:10])
        if not store:
            continue
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        hrs = act if act > 0 else sched
        rate = rate_map.get(eid, 0.0)
        d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
        d["hours"] += hrs
        d["amount"] += hrs * rate

    # UNIVERSAL FALLBACK (2026-07-18, same audit as /payroll) — a clock punch with no matching shift
    # row is real existing platform data this store auto-fill was dropping. ADDITIVE ONLY: a day
    # already covered by a shift is skipped (byte-identical for a schedule-tracked tenant).
    if lo and hi:
        tl = (sb().table("timelog").select("employee_id,hours,clock_out,work_date,store_code")
              .eq("org_id", org_id).gte("work_date", lo).lt("work_date", hi).limit(20000).execute().data) or []
        for t in tl:
            if not (t.get("clock_out") and t.get("hours") is not None):
                continue
            eid = t.get("employee_id")
            wd = str(t.get("work_date") or "")[:10]
            store = (t.get("store_code") or "").strip()
            if not eid or not wd or not store or wd in shift_days.get(eid, set()):
                continue
            hrs = float(t.get("hours") or 0)
            rate = rate_map.get(eid, 0.0)
            d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
            d["hours"] += hrs
            d["amount"] += hrs * rate

    rows = list(by_store.values())
    for r in rows:
        r["hours"] = round(r["hours"], 2)
        r["amount"] = round(r["amount"], 2)
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]
    return {"month": month, "stores": sorted(rows, key=lambda x: x["store_code"])}


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
def create_employee(emp: dict, org_id: str = ORG_ID):
    """Create an employee (StoreOps Admin)."""
    row = {k: emp[k] for k in EMP_FIELDS if k in emp}
    if not (row.get("name") or "").strip():
        raise HTTPException(400, "name required")
    row["org_id"] = org_id
    if row.get("is_active") is None:
        row["is_active"] = True
    # employee_id is TEXT UNIQUE: a blank '' collides on the 2nd person with no ID.
    # Drop it so the column is NULL (multiple NULLs are allowed), then auto-assign one below.
    if not (row.get("employee_id") or "").strip():
        row.pop("employee_id", None)
    r = sb().table("employees").insert(row).execute()
    return _ensure_employee_id(r.data[0]) if r.data else row


@router.patch("/employees/{emp_id}")
def update_employee(emp_id: str, updates: dict, org_id: str = ORG_ID):
    """Update an employee (name/role/home_store/pay_rate/active/contact). StoreOps Admin.
    emp_id is str (not int) so a UUID or numeric id both work — a typed int rejected UUID ids
    with a 422, which read as 'cannot edit' in the UI.

    org_id-scoped so a foreign (guessable BIGSERIAL) emp_id is a no-op instead of a cross-tenant
    write — this previously took NO org filter at all, and it's the pay_rate write path."""
    row = {k: updates[k] for k in EMP_FIELDS if k in updates}
    if not row:
        raise HTTPException(400, "no valid fields to update")
    # Clearing the Emp ID must store NULL, not '' (TEXT UNIQUE → '' collides across people).
    if "employee_id" in row and not (row.get("employee_id") or "").strip():
        row["employee_id"] = None
    r = sb().table("employees").update(row).eq("id", emp_id).eq("org_id", org_id).execute()
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
        sb().table("employees").delete().eq("id", emp_id).eq("org_id", org_id).execute()
    except Exception as ex:
        raise HTTPException(409, f"cannot delete (linked records exist — try deactivating): {ex}")
    login = {}
    try:
        # BUG FIX (luxelink-parity audit 2026-07-16): this used to purge under the imported HOUSE
        # `ORG_ID` constant instead of the caller's own `org_id` — so deleting a non-house-tenant
        # employee (e.g. luxelink) purged nothing (found:0, login left dangling as a ghost user)
        # and, worse, scoped the lookup to the WRONG tenant. Use the local org_id (the tenant this
        # employee actually belongs to) — purge_app_user is already org_id-scoped internally.
        from app.modules.core.router import purge_app_user
        login = purge_app_user(org_id, email=e.get("email"), employee_id=e.get("employee_id"), hard=True)
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
            # org_id-scoped: matching by employee_NAME is not globally unique, so an unscoped
            # match could reassign another tenant's shift onto this tenant's target employee.
            r = sb().table("shifts").update(reassign).eq(field, val).eq("org_id", org_id).execute()
            moved["shifts"] += len(r.data or [])
        except Exception:
            pass
    try:
        r = (sb().table("time_off_requests").update({"employee_id": str(tgt["id"])})
             .eq("employee_id", str(dup["id"])).eq("org_id", org_id).execute())
        moved["time_off"] += len(r.data or [])
    except Exception:
        pass
    deleted = True
    try:
        sb().table("employees").delete().eq("id", dup_id).eq("org_id", org_id).execute()
    except Exception:
        sb().table("employees").update({"is_active": False}).eq("id", dup_id).eq("org_id", org_id).execute()
        deleted = False
    # Cascade the duplicate's login too (delete it if the row was deleted, else deactivate) so the
    # merged-away person doesn't linger in Roles & Access.
    login = {}
    try:
        # Same fix as delete_employee: use the caller's own org_id, not the house ORG_ID constant.
        from app.modules.core.router import purge_app_user
        login = purge_app_user(org_id, email=dup.get("email"), employee_id=dup.get("employee_id"), hard=deleted)
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
        sb().table("employees").update({"pay_rate": rate}).eq("id", match["id"]).eq("org_id", org_id).execute()
        updated += 1
    return {"updated": updated, "errors": errors, "total": len(rows)}


def _sync_store_mapping(org_id, stores):
    """Mirror StoreOps-created stores into commcalc.store_mapping so a new store PROPAGATES everywhere
    that reads the mapping (Daily Closing, Assets, Targets, recons, …). Insert-if-absent by store_code.
    Best-effort — a mapping failure must never break store creation."""
    try:
        c = get_supabase()
        want = {}
        for s in stores:
            code = str(s.get("store_code") or "").strip()
            if code:
                want[code] = {"org_id": org_id, "store_code": code,
                              "store_address": s.get("address") or s.get("store_address") or code,
                              "market": s.get("market")}
        if not want:
            return
        have = {str(m.get("store_code") or "").strip() for m in
                (c.schema("commcalc").table("store_mapping").select("store_code")
                 .eq("org_id", org_id).in_("store_code", list(want)).execute().data or [])}
        new = [v for code, v in want.items() if code not in have]
        for i in range(0, len(new), 500):
            c.schema("commcalc").table("store_mapping").insert(new[i:i + 500]).execute()
    except Exception as e:
        print(f"WARN store_mapping sync failed: {e}")


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
    _sync_store_mapping(org_id, to_insert)   # propagate new stores to commcalc.store_mapping
    return {"inserted": inserted, "skipped": skipped}


@router.post("/stores")
def create_store(store: dict, org_id: str = ORG_ID):
    """Create a store (StoreOps Admin)."""
    row = {k: store[k] for k in STORE_FIELDS if k in store}
    if not (row.get("store_code") or "").strip():
        raise HTTPException(400, "store_code required")
    row["org_id"] = org_id
    if row.get("is_active") is None:
        row["is_active"] = True
    r = sb().table("stores").insert(row).execute()
    _sync_store_mapping(org_id, [row])   # propagate the new store to commcalc.store_mapping
    return r.data[0] if r.data else row


@router.patch("/stores/{store_id}")
def update_store(store_id: int, updates: dict, org_id: str = ORG_ID):
    """Update a store (StoreOps Admin). org_id-scoped so a foreign (guessable BIGSERIAL) store_id
    is a no-op instead of a cross-tenant write — this previously took NO org filter at all."""
    row = {k: updates[k] for k in STORE_FIELDS if k in updates}
    if not row:
        raise HTTPException(400, "no valid fields to update")
    r = sb().table("stores").update(row).eq("id", store_id).eq("org_id", org_id).execute()
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
        # org_id-scoped: employee_id here isn't guaranteed globally unique (numeric-vs-business-id
        # ambiguity noted below), so an unscoped delete could wipe another tenant's template.
        sb().table("shift_templates").delete().eq("employee_id", eid).eq("org_id", org_id).execute()
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
def create_shift_swap(req: dict, org_id: str = ORG_ID):
    """Create a swap request. Body: requester_id, target_id?, shift_id?, target_shift_id?, notes?"""
    if not req.get("requester_id"):
        raise HTTPException(400, "requester_id required")
    row = {k: req.get(k) for k in ("requester_id", "target_id", "shift_id", "target_shift_id", "notes")}
    row["status"] = "pending"
    row["org_id"] = org_id
    r = sb().table("shift_swap_requests").insert(row).execute()
    return r.data[0] if r.data else row


def _apply_swap(swap, org_id: str = ORG_ID):
    """On approval, reassign the shift(s). If both shifts present it's a true swap;
    otherwise the single shift is handed to the target employee."""
    names = _emp_name_map(org_id)
    tgt, reqr = swap.get("target_id"), swap.get("requester_id")
    if swap.get("shift_id") and tgt:
        sb().table("shifts").update({"employee_id": tgt, "employee_name": names.get(str(tgt))}) \
            .eq("id", swap["shift_id"]).eq("org_id", org_id).execute()
    if swap.get("target_shift_id") and reqr:
        sb().table("shifts").update({"employee_id": reqr, "employee_name": names.get(str(reqr))}) \
            .eq("id", swap["target_shift_id"]).eq("org_id", org_id).execute()


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
    r = sb().table("shift_swap_requests").update({"status": status}).eq("id", swap_id).eq("org_id", org_id).execute()
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


def _fmt_time(iso, org_id=None):
    # Display in the BUSINESS timezone (not the server's) so the kiosk time matches the reports and
    # doesn't drift with wherever Railway happens to run. org_id (optional) resolves the TENANT's
    # own timezone if one is set (mig 085); omitted callers keep the house-wide default.
    try:
        tz = _biz_tz_for(org_id) if org_id else _BIZ_TZ
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(tz).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return iso


def _norm_store(x):
    return str(x or "").strip().upper()


def _allowed_clock_stores(org_id, employee_id, home_store, work_date_local):
    """The stores this employee may clock in at TODAY, without a manager override:
    their home store + any store they're SCHEDULED at today + any store they float to
    (app_users.store_codes[]). Returns a set of normalized (UPPER) store codes.

    ID RECONCILIATION: the schedule stores shifts.employee_id as the employees NUMERIC row id
    (employees.id, e.g. '42' — see the schedule page's `emp.id.toString()`), while clock-in
    identifies the caller by their BUSINESS id (app_users.employee_id = employees.employee_id,
    e.g. 'E039'). Those never match, so a rep scheduled at a non-home store could not clock in
    there (only their home store). We therefore match a shift by EITHER id — plus the employee
    name as a last-resort fallback — so a scheduled store is honored regardless of which id the
    shift was written with."""
    codes = set()
    if home_store:
        codes.add(_norm_store(home_store))
    # Build the set of identifiers a shift for THIS employee might carry (business id + numeric id).
    ids = {str(employee_id)}
    emp_name = None
    try:
        er = (sb().table("employees").select("id,name").eq("org_id", org_id)
              .eq("employee_id", employee_id).limit(1).execute().data) or []
        if er:
            if er[0].get("id") is not None:
                ids.add(str(er[0]["id"]))
            emp_name = er[0].get("name")
    except Exception:
        pass
    try:
        # Match the scheduled shift by the employee's id(s) WITHOUT an org filter. shifts.employee_id is
        # the globally-unique employees.id (the schedule grid writes emp.id) or the business id, so a
        # legacy/house/NULL-org shift row would otherwise be silently dropped once multi-tenant enforcement
        # binds the rep's REAL tenant — blocking clock-in at a scheduled store (the bug). Matching the
        # rep's own id(s) + today's date IS the isolation. (Name fallback stays org-scoped — name is weak.)
        matched = ((sb().table("shifts").select("store_code")
                    .eq("shift_date", work_date_local).eq("is_deleted", False)
                    .in_("employee_id", list(ids)).execute().data) or [])
        if emp_name:  # last-resort fallback: a shift written with a mismatched/blank id but our name
            matched += ((sb().table("shifts").select("store_code")
                         .eq("org_id", org_id).eq("shift_date", work_date_local)
                         .eq("is_deleted", False).eq("employee_name", emp_name).execute().data) or [])
        for s in matched:
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


def _require_manager(authorization, org_id=ORG_ID):
    """Resolve the signed-in caller and confirm they're a manager (not a plain rep) so they can
    authorize a clock-in override. Resolves the manager's OWN tenant from their token (auth_id is
    globally unique), so a manager in ANY tenant — not just the house org — can approve; the returned
    row carries org_id, which the caller uses for the employee lookup + punch. 401/403 otherwise."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "A manager must sign in to approve this override.")
    rows = (sb().table("app_users").select("org_id,email,role,employee_id")
            .eq("auth_id", uid).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(403, "That login isn't recognized.")
    u = rows[0]
    org_id = (u.get("org_id") or "").strip() or org_id
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


def _caller_identity(authorization: str):
    """Resolve the caller's OWN (org_id, employee_id) from their Supabase JWT ALONE — no org filter.
    auth_id is globally unique, so it maps to exactly ONE app_users row, in whatever tenant that user
    belongs to. That tenant is authoritative for self-service time-clock, so a punch always lands in
    the employee's OWN tenant regardless of any org_id query param (the kiosk sends none). This is
    what lets one person hold a separate login per tenant with fully isolated clock-in/out.

    Fixes the cross-tenant clock-in breakage: the old path pinned the lookup to the house org
    (org_id default = ...0001), so an employee moved to another tenant (their app_users.org_id
    re-pointed) matched nothing here and got a 403 'login isn't linked to an employee record'.
    401 if not signed in; 403 if the login isn't linked to an employee record."""
    from app.modules.core.router import _uid_from_token  # local import avoids a circular import
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "Sign in to use the time clock.")
    rows = (sb().table("app_users").select("org_id,employee_id")
            .eq("auth_id", uid).limit(1).execute().data) or []
    row = rows[0] if rows else {}
    org = (row.get("org_id") or "").strip() or ORG_ID
    eid = ((row.get("employee_id") or "")).strip()
    if not eid:
        raise HTTPException(403, "Your login isn't linked to an employee record. "
                                 "Ask an admin to set your Employee ID in Roles & Access.")
    return org, eid


@router.get("/timeclock/status")
def timeclock_status(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Is the SIGNED-IN employee currently clocked in? Identity AND tenant come from the auth token."""
    org_id, employee_id = _caller_identity(authorization)
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
    org_id, employee_id = _caller_identity(authorization)
    # guard: don't open a second concurrent entry
    open_rows = (sb().table("timelog").select("id").eq("org_id", org_id).eq("employee_id", employee_id)
                 .is_("clock_out", "null").limit(1).execute().data) or []
    if open_rows:
        raise HTTPException(409, "Already clocked in — clock out first.")
    name, home_store = _emp_name(org_id, employee_id)
    now = datetime.now(timezone.utc)
    work_date = now.astimezone(_biz_tz_for(org_id)).date().isoformat()   # business-local date (not UTC)
    # Which store is this punch for? The kiosk sends the selected store; fall back to home store.
    req_store = (body.get("store_code") or "").strip() or home_store
    # Gate: home OR scheduled-today OR floater store. Anything else needs a manager override.
    if req_store:
        allowed = _allowed_clock_stores(org_id, employee_id, home_store, work_date)
        if allowed and _norm_store(req_store) not in allowed:
            return {"success": False, "needs_override": True, "store_code": req_store,
                    "allowed_stores": sorted(allowed), "home_store": home_store,
                    "message": f"You're not scheduled at {req_store} today. A manager can approve it."}
    # Priority-sell acknowledgment gate (module 095): if the tenant enabled it and this store has
    # devices in the final % of their pay window, the rep must acknowledge before clocking in. Any
    # lookup gap → no block (never trap a rep on a config/migration miss — mirrors the closing gate).
    if not body.get("priority_ack"):
        try:
            t = (sb().table("tenants").select("priority_ack_enabled").eq("org_id", org_id).limit(1).execute().data) or []
            if t and t[0].get("priority_ack_enabled"):
                from app.modules.payables.engine import priority_for_store
                prio = priority_for_store(get_supabase(), org_id, req_store)
                if prio:
                    return {"success": False, "needs_priority_ack": True, "store_code": req_store,
                            "priority": prio,
                            "message": "Acknowledge the phones to prioritize selling today, then clock in."}
        except Exception:
            pass
    selfie_path = _upload_selfie(org_id, employee_id, body.get("selfie"))
    row = {"org_id": org_id, "employee_id": employee_id, "employee_name": name,
           "store_code": req_store,
           "clock_in": now.isoformat(), "work_date": work_date,
           "device": body.get("device"), "selfie_path": selfie_path,
           "gps_lat": body.get("gps_lat"), "gps_lng": body.get("gps_lng"),
           "gps_accuracy_m": body.get("gps_accuracy_m"), "face_match_pct": body.get("face_match_pct")}
    r = sb().table("timelog").insert(row).execute()
    saved = r.data[0] if r.data else row
    if body.get("priority_ack"):   # record the "I will prioritize these phones" acknowledgment (module 095)
        try:
            get_supabase().schema("commcalc").table("priority_ack_log").insert({
                "org_id": org_id, "employee_id": employee_id, "store_code": req_store,
                "ack_date": work_date, "imei_count": int(body.get("priority_ack_count") or 0)}).execute()
        except Exception:
            pass
    return {"success": True, "data": {"time": _fmt_time(saved.get("clock_in"), org_id), "entry_id": saved.get("id"),
                                      "store_code": req_store}}


@router.get("/timeclock/allowed-stores")
def timeclock_allowed_stores(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The stores the signed-in employee can clock in at today (home + scheduled + floater), so the
    kiosk can show a picker instead of forcing the home store."""
    org_id, employee_id = _caller_identity(authorization)
    name, home_store = _emp_name(org_id, employee_id)
    work_date = datetime.now(timezone.utc).astimezone(_biz_tz_for(org_id)).date().isoformat()
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
    org_id = (mgr.get("org_id") or org_id)   # the manager's own tenant is authoritative
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
    work_date = now.astimezone(_biz_tz_for(org_id)).date().isoformat()
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
            "data": {"time": _fmt_time(saved.get("clock_in"), org_id), "entry_id": saved.get("id"), "store_code": store_code}}


def _closing_gate_block(org_id, employee_id, store_code):
    """Return a block message if this employee is the ASSIGNED CLOSER for `store_code`, the tenant's
    closing gate is ON, and the store's daily closing for today is NOT yet submitted — else None.
    Cross-module: closings live in commcalc.daily_closing. Any lookup gap → no block (never trap a
    rep on a config/migration miss)."""
    try:
        store = (store_code or "").strip()
        if not store:
            return None
        t = (sb().table("tenants").select("closing_gate_enabled").eq("org_id", org_id).limit(1).execute().data) or []
        if not (t and t[0].get("closing_gate_enabled")):
            return None
        closer = (sb().table("store_closer").select("employee_id")
                  .eq("org_id", org_id).eq("store_code", store).limit(1).execute().data) or []
        if not closer:
            return None
        ids, _ = _emp_id_variants(org_id, employee_id)
        if str(closer[0].get("employee_id") or "") not in ids:
            return None  # not the assigned closer → not gated
        today = datetime.now(timezone.utc).astimezone(_biz_tz_for(org_id)).date().isoformat()
        done = (get_supabase().schema("commcalc").table("daily_closing").select("id")
                .eq("org_id", org_id).eq("store_code", store).eq("close_date", today).limit(1).execute().data) or []
        if done:
            return None
        return (f"The daily closing for {store} must be submitted before you clock out. "
                f"Complete the store closing, then clock out.")
    except Exception:
        return None


@router.post("/timeclock/clock-out")
def clock_out(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Close the SIGNED-IN employee's open entry (updates the SAME row) and compute hours. Always
    scoped to the caller's own employee_id so one employee can't close another's punch."""
    org_id, employee_id = _caller_identity(authorization)
    entry_id = body.get("entry_id")
    q = (sb().table("timelog").select("*").eq("org_id", org_id).is_("clock_out", "null")
         .eq("employee_id", employee_id))
    if entry_id:
        q = q.eq("id", entry_id)
    rows = q.order("clock_in", desc=True).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "No open clock-in found.")
    entry = rows[0]
    # Closing gate (mig 089): the store's ASSIGNED CLOSER can't clock out until the store's daily
    # closing is submitted. Only the assigned closer is gated (per the product decision); other reps
    # clock out normally. Returns needs_closing (no punch change) rather than closing the entry.
    block = _closing_gate_block(org_id, employee_id, entry.get("store_code"))
    if block and not body.get("override"):
        return {"success": False, "needs_closing": True, "message": block}
    now = datetime.now(timezone.utc)
    try:
        ci = datetime.fromisoformat(str(entry["clock_in"]).replace("Z", "+00:00"))
        hours = round((now - ci).total_seconds() / 3600.0, 2)
    except Exception:
        hours = None
    sb().table("timelog").update({"clock_out": now.isoformat(), "hours": hours}).eq("id", entry["id"]).execute()
    return {"success": True, "data": {"time": _fmt_time(now.isoformat(), org_id), "hours": hours,
                                      "clock_in": _fmt_time(entry.get("clock_in"), org_id)}}


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


# ── kiosk clock-in config (configurable face-match sensitivity) ───────────────────────────────
@router.get("/timeclock/config")
def timeclock_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Kiosk clock-in config for the caller's tenant. face_match_threshold = the face-api Euclidean-distance
    cutoff; HIGHER = looser = fewer false rejects for the same rep. Any authed user may read. Default 0.60
    (face-api's own default) when unset or migration 112 hasn't run. Set it on the Failure Logs page."""
    thr = None
    try:
        t = (sb().table("tenants").select("face_match_threshold").eq("org_id", org_id).limit(1).execute().data) or []
        if t:
            thr = t[0].get("face_match_threshold")
    except Exception:
        thr = None
    try:
        thr = float(thr) if thr is not None else 0.60
    except (TypeError, ValueError):
        thr = 0.60
    # clamp to the same safe band the setter enforces
    thr = max(0.45, min(0.72, thr))
    return {"face_match_threshold": thr}


# ── face recognition (face-api.js 128-float descriptors) ──────────────────────────────────────
@router.get("/timeclock/face")
def get_face(authorization: str = Header(default=""), action: str = "", org_id: str = ORG_ID):
    """Registration status (and the descriptor itself when action=descriptor, for verify) for the
    SIGNED-IN employee — identity comes from the auth token."""
    org_id, employee_id = _caller_identity(authorization)
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
    org_id, employee_id = _caller_identity(authorization)
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


# ════════════════════════════════════════════════════════════════════════════════════════════════
# FORCED CLOCK-OUT AT SCHEDULED END + SHIFT-EXTENSION (DM approval) WORKFLOW (migration 086)
# At a shift's scheduled end an open punch is auto-closed (stamped at the scheduled end, so paid
# hours match the schedule) UNLESS an extension was APPROVED ahead of time by the District Manager.
# ════════════════════════════════════════════════════════════════════════════════════════════════
FORCE_CLOCKOUT_GRACE_MIN = 15


def _emp_id_variants(org_id, employee_id):
    """The set of ids a shift/extension for this employee might carry — the schedule stores the
    NUMERIC employees.id while the punch carries the BUSINESS employee_id (same mismatch the clock-in
    gate reconciles). Returns ({id-strings}, name)."""
    ids = {str(employee_id)}
    name = None
    try:
        er = (sb().table("employees").select("id,name").eq("org_id", org_id)
              .eq("employee_id", employee_id).limit(1).execute().data) or []
        if er:
            if er[0].get("id") is not None:
                ids.add(str(er[0]["id"]))
            name = er[0].get("name")
    except Exception:
        pass
    return ids, name


def _biz_dt_utc(date_str, hhmm, org_id=None):
    """Combine a 'YYYY-MM-DD' + 'HH:MM' (business-local) into an aware UTC datetime, or None."""
    try:
        parts = str(hhmm).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        naive = datetime.fromisoformat(str(date_str)[:10] + f"T{h:02d}:{m:02d}:00")
        tz = _biz_tz_for(org_id) if org_id else _BIZ_TZ
        return naive.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None


def _approved_extension_end(org_id, id_variants, work_date):
    """The latest APPROVED extension end ('HH:MM') for this employee/day, or None."""
    try:
        rows = (sb().table("shift_extension").select("requested_end")
                .eq("org_id", org_id).eq("shift_date", str(work_date)[:10]).eq("status", "approved")
                .in_("employee_id", list(id_variants)).execute().data) or []
        ends = [r.get("requested_end") for r in rows if r.get("requested_end")]
        return max(ends) if ends else None
    except Exception:
        return None


def _scheduled_end_for_punch(org_id, punch):
    """The aware-UTC datetime an open punch should be auto-closed at: the matching shift's end_time
    (honoring an approved extension). None when the punch has no scheduled shift (leave it open)."""
    eid = punch.get("employee_id")
    wdate = punch.get("work_date")
    if not (eid and wdate):
        return None
    ids, _ = _emp_id_variants(org_id, eid)
    try:
        shifts = (sb().table("shifts").select("store_code,end_time")
                  .eq("org_id", org_id).eq("shift_date", str(wdate)[:10]).eq("is_deleted", False)
                  .in_("employee_id", list(ids)).execute().data) or []
    except Exception:
        shifts = []
    if not shifts:
        return None
    store = punch.get("store_code")
    same = [s for s in shifts if _norm_store(s.get("store_code")) == _norm_store(store)] or shifts
    s = max(same, key=lambda x: (x.get("end_time") or ""))
    end_hhmm = _approved_extension_end(org_id, ids, wdate) or s.get("end_time")
    if not end_hhmm:
        return None
    return _biz_dt_utc(wdate, end_hhmm, org_id)


def _do_force_clockout(org_id=None, grace_min=FORCE_CLOCKOUT_GRACE_MIN):
    """Close every open punch whose scheduled shift end (+ grace) has passed, stamping the clock-out
    at the SCHEDULED END (paid hours = scheduled). Punches with no scheduled shift are left open."""
    client = sb()
    q = client.table("timelog").select("*").is_("clock_out", "null")
    if org_id:
        q = q.eq("org_id", org_id)
    open_punches = q.execute().data or []
    now = datetime.now(timezone.utc)
    closed = []
    for p in open_punches:
        oid = p.get("org_id") or ORG_ID
        end_dt = _scheduled_end_for_punch(oid, p)
        if not end_dt:
            continue
        if now < end_dt + timedelta(minutes=grace_min):
            continue  # still within the shift + grace
        try:
            ci = datetime.fromisoformat(str(p["clock_in"]).replace("Z", "+00:00"))
            hours = round((end_dt - ci).total_seconds() / 3600.0, 2)
            if hours < 0:
                hours = 0.0
        except Exception:
            hours = None
        note = ((p.get("notes") or "") + " | auto clock-out at scheduled end (system)").strip(" |")
        try:
            client.table("timelog").update(
                {"clock_out": end_dt.isoformat(), "hours": hours, "notes": note}
            ).eq("id", p["id"]).execute()
            closed.append({"employee_id": p.get("employee_id"), "store_code": p.get("store_code"),
                           "clock_out": end_dt.isoformat(), "hours": hours})
        except Exception:
            pass
    return {"closed": len(closed), "detail": closed}


@router.post("/timeclock/force-clockout/run-due")
def force_clockout_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint (guarded by NOTIFY_RUN_SECRET) — auto-close overdue open punches across ALL
    tenants. Schedule it every ~15 min. Idempotent: a punch is closed at most once."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    return _do_force_clockout(org_id=None)


@router.post("/timeclock/force-clockout/run")
def force_clockout_run_now(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manual trigger for the caller's tenant (admin/manager) — same logic as the cron, for testing
    or an ad-hoc sweep."""
    mgr = _require_manager(authorization, org_id)
    return _do_force_clockout(org_id=(mgr.get("org_id") or org_id))


# ── Shift-extension request → DM approval workflow ─────────────────────────────────────────────
def _dm_for_store(org_id, store_code):
    """Resolve the District Manager for a store: walk the org tree up from the store's org_unit to a
    District-level node (fallback: match a District unit by market), then read org_managers. Returns
    (employee_id, email, name) or (None, None, None) when the org tree isn't configured."""
    c = sb()
    unit_id, market = None, None
    try:
        st = (c.table("stores").select("org_unit_id,market").eq("org_id", org_id)
              .eq("store_code", store_code).limit(1).execute().data) or []
        if st:
            unit_id, market = st[0].get("org_unit_id"), st[0].get("market")
    except Exception:
        pass
    try:
        levels = {l["id"]: (l.get("name") or "") for l in
                  (c.table("org_levels").select("id,name").eq("org_id", org_id).execute().data or [])}
        units = {u["id"]: u for u in
                 (c.table("org_units").select("id,name,level_id,parent_id,code").eq("org_id", org_id).execute().data or [])}
    except Exception:
        levels, units = {}, {}
    district = None
    cur = units.get(unit_id) if unit_id else None
    guard = 0
    while cur and guard < 20:
        if "district" in (levels.get(cur.get("level_id")) or "").lower():
            district = cur
            break
        cur = units.get(cur.get("parent_id"))
        guard += 1
    if not district and market:
        mk = str(market).strip().lower()
        for u in units.values():
            if "district" in (levels.get(u.get("level_id")) or "").lower() and (
                    mk and (mk in (u.get("name") or "").lower() or (u.get("code") or "").lower() == f"district:{mk}")):
                district = u
                break
    if not district:
        return (None, None, None)
    try:
        mg = (c.table("org_managers").select("employee_id").eq("org_id", org_id)
              .eq("unit_id", district["id"]).limit(1).execute().data) or []
        if not mg:
            return (None, None, None)
        deid = mg[0]["employee_id"]
        emp = (c.table("employees").select("name,email").eq("org_id", org_id)
               .eq("employee_id", deid).limit(1).execute().data) or []
        return (deid, (emp[0].get("email") if emp else None), (emp[0].get("name") if emp else None))
    except Exception:
        return (None, None, None)


@router.post("/shift-extensions")
async def request_shift_extension(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """A manager files a request to extend an employee's shift past its scheduled end. Resolves the
    District Manager, saves it 'pending', and emails the DM an FYI (the approval itself is the DM's
    in-app tick). Body: {employee_id, employee_name?, store_code, shift_date, requested_end, reason?,
    shift_id?, original_end?}."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    employee_id = (body.get("employee_id") or "").strip()
    store_code = (body.get("store_code") or "").strip()
    shift_date = (body.get("shift_date") or "").strip()[:10]
    requested_end = (body.get("requested_end") or "").strip()
    if not (employee_id and shift_date and requested_end):
        raise HTTPException(400, "employee_id, shift_date and requested_end are required")
    dm_eid, dm_email, dm_name = _dm_for_store(org_id, store_code)
    _ids, emp_name = _emp_id_variants(org_id, employee_id)
    row = {"org_id": org_id, "employee_id": employee_id,
           "employee_name": body.get("employee_name") or emp_name,
           "store_code": store_code, "shift_id": body.get("shift_id"), "shift_date": shift_date,
           "original_end": body.get("original_end"), "requested_end": requested_end,
           "reason": body.get("reason"), "status": "pending",
           "requested_by": mgr.get("email"), "requested_by_name": mgr.get("email"),
           "dm_employee_id": dm_eid, "dm_email": dm_email}
    try:
        r = sb().table("shift_extension").insert(row).execute()
        ext_id = (r.data or [{}])[0].get("id")
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 086 applied? {e}")
    # FYI email to the DM (best-effort; the approval is the in-app tick, not this email)
    emailed = False
    if dm_email:
        try:
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                await email_resend.send_email(
                    to=dm_email,
                    subject=f"Shift-extension approval needed — {row['employee_name'] or employee_id}",
                    html=(f"<p>{mgr.get('email')} requested to extend "
                          f"<b>{row['employee_name'] or employee_id}</b>'s shift at "
                          f"<b>{store_code or '—'}</b> on <b>{shift_date}</b> to <b>{requested_end}</b>.</p>"
                          f"<p>Reason: {body.get('reason') or '—'}</p>"
                          f"<p>Approve or deny it in MetricsPro → Workforce → Shift Extensions.</p>"))
                emailed = True
        except Exception:
            pass
    return {"ok": True, "id": ext_id, "status": "pending",
            "dm": {"employee_id": dm_eid, "name": dm_name, "email": dm_email, "emailed": emailed},
            "note": None if dm_eid else "No District Manager is configured for this store — an admin or DM can still approve it."}


@router.get("/shift-extensions")
def list_shift_extensions(status: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Extension requests for the caller's tenant (optionally filtered by status), newest first —
    powers the DM/admin approval queue and the manager's history."""
    org_id, _eid = _caller_identity(authorization) if authorization else (org_id, None)
    q = sb().table("shift_extension").select("*").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    rows = q.order("requested_at", desc=True).limit(200).execute().data or []
    return {"extensions": rows}


@router.post("/shift-extensions/{ext_id}/decision")
def decide_shift_extension(ext_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The DM (or an admin) approves/denies a request IN-APP — the tick is the approval, recorded with
    who + when. Body: {decision: 'approve'|'deny', note?}. Once approved, the forced-clockout job
    honors the extended end for that employee/day."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    decision = (body.get("decision") or "").strip().lower()
    if decision not in ("approve", "deny"):
        raise HTTPException(400, "decision must be 'approve' or 'deny'")
    rows = (sb().table("shift_extension").select("*").eq("id", ext_id).eq("org_id", org_id)
            .limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "unknown request")
    if rows[0].get("status") != "pending":
        raise HTTPException(409, f"already {rows[0].get('status')}")
    upd = {"status": "approved" if decision == "approve" else "denied",
           "decided_by": mgr.get("email"), "decided_by_name": mgr.get("email"),
           "decided_at": datetime.now(timezone.utc).isoformat(),
           "decision_note": body.get("note")}
    sb().table("shift_extension").update(upd).eq("id", ext_id).eq("org_id", org_id).execute()
    return {"ok": True, "status": upd["status"], "decided_by": mgr.get("email")}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# PER-STORE WEEKLY HOURS BUDGET + DM-approved overrides (migration 087)
# A store manager can't schedule past the store's weekly budget; exceeding it needs a District
# Manager's in-app approval for that store+week (the tick IS the approval, recorded with who+when).
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _hours_between(start, end):
    """Duration in hours between two 'HH:MM' strings (0 if malformed / end<=start)."""
    try:
        sh, sm = [int(x) for x in str(start).split(":")[:2]]
        eh, em = [int(x) for x in str(end).split(":")[:2]]
        return max(0.0, ((eh * 60 + em) - (sh * 60 + sm)) / 60.0)
    except Exception:
        return 0.0


def _work_week_bounds(org_id, date_str):
    """(week_start_iso, week_end_iso) for the 7-day work-week containing date_str, per the tenant's
    work_week_start_dow (mig 085; default Monday=0)."""
    from datetime import date as _d
    dow = 0
    try:
        t = (sb().table("tenants").select("work_week_start_dow").eq("org_id", org_id).limit(1).execute().data) or []
        if t and t[0].get("work_week_start_dow") is not None:
            dow = int(t[0]["work_week_start_dow"]) % 7
    except Exception:
        pass
    d = _d.fromisoformat(str(date_str)[:10])
    ws = d - timedelta(days=(d.weekday() - dow) % 7)
    return ws.isoformat(), (ws + timedelta(days=6)).isoformat()


def _store_week_hours(org_id, store_code, ws, we, exclude_id=None):
    """Total scheduled hours at a store across the work-week [ws, we]."""
    rows = (sb().table("shifts").select("id,scheduled_hours,start_time,end_time")
            .eq("org_id", org_id).eq("store_code", store_code).eq("is_deleted", False)
            .gte("shift_date", ws).lte("shift_date", we).execute().data) or []
    total = 0.0
    for r in rows:
        if exclude_id and str(r.get("id")) == str(exclude_id):
            continue
        h = r.get("scheduled_hours")
        if h is None:
            h = _hours_between(r.get("start_time"), r.get("end_time"))
        total += float(h or 0)
    return total


def _store_budget(org_id, store_code):
    try:
        rows = (sb().table("hours_budget").select("weekly_hours").eq("org_id", org_id)
                .eq("store_code", store_code).limit(1).execute().data) or []
        if rows and rows[0].get("weekly_hours") is not None:
            return float(rows[0]["weekly_hours"])
    except Exception:
        pass
    return None


def _budget_override_ok(org_id, store_code, ws):
    try:
        rows = (sb().table("budget_override").select("id").eq("org_id", org_id)
                .eq("store_code", store_code).eq("week_start", ws).eq("status", "approved")
                .limit(1).execute().data) or []
        return bool(rows)
    except Exception:
        return False


def _enforce_hours_budget(org_id, shift, exclude_id=None):
    """Raise 409 if adding `shift` would push its store's work-week over budget (and no DM override
    exists). No-op when the store has no budget set or on any lookup error (never blocks on a gap)."""
    try:
        store = (shift.get("store_code") or "").strip()
        sdate = (shift.get("shift_date") or "").strip()
        if not (store and sdate):
            return
        budget = _store_budget(org_id, store)
        if budget is None:
            return
        ws, we = _work_week_bounds(org_id, sdate)
        new_h = shift.get("scheduled_hours")
        if new_h is None:
            new_h = _hours_between(shift.get("start_time"), shift.get("end_time"))
        projected = _store_week_hours(org_id, store, ws, we, exclude_id=exclude_id) + float(new_h or 0)
        if projected > budget + 1e-6 and not _budget_override_ok(org_id, store, ws):
            raise HTTPException(409, f"Over the weekly hours budget for {store}: this would schedule "
                                     f"{projected:.1f}h vs the {budget:.0f}h budget for the week of {ws}. "
                                     f"Request District Manager approval to exceed it.")
    except HTTPException:
        raise
    except Exception:
        return  # config/migration gap → don't block scheduling


@router.get("/hours-budgets")
def list_hours_budgets(week: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Every store's weekly budget + this-week usage (usage for the work-week containing `week`, or
    today). Powers the budget admin + the over-budget alert."""
    try:
        org_id, _ = _caller_identity(authorization)
    except Exception:
        pass
    ref = (week or datetime.now(timezone.utc).astimezone(_biz_tz_for(org_id)).date().isoformat())
    ws, we = _work_week_bounds(org_id, ref)
    budgets = {b["store_code"]: float(b.get("weekly_hours") or 0)
               for b in (sb().table("hours_budget").select("store_code,weekly_hours").eq("org_id", org_id).execute().data or [])}
    stores = (sb().table("stores").select("store_code,address,market").eq("org_id", org_id).execute().data) or []
    out = []
    for s in stores:
        sc = s.get("store_code")
        if not sc:
            continue
        used = _store_week_hours(org_id, sc, ws, we)
        bud = budgets.get(sc)
        out.append({"store_code": sc, "address": s.get("address"), "market": s.get("market"),
                    "weekly_hours": bud, "used_hours": round(used, 1),
                    "over": (bud is not None and used > bud + 1e-6),
                    "override": _budget_override_ok(org_id, sc, ws)})
    return {"week_start": ws, "week_end": we, "budgets": out}


@router.put("/hours-budgets")
def set_hours_budget(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Set (or clear) a store's standing weekly hours budget. Manager/admin only."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    store = (body.get("store_code") or "").strip()
    if not store:
        raise HTTPException(400, "store_code required")
    if body.get("weekly_hours") in (None, "", "null"):
        sb().table("hours_budget").delete().eq("org_id", org_id).eq("store_code", store).execute()
        return {"ok": True, "cleared": True}
    row = {"org_id": org_id, "store_code": store, "weekly_hours": float(body.get("weekly_hours") or 0),
           "updated_by": mgr.get("email"), "updated_at": datetime.now(timezone.utc).isoformat()}
    sb().table("hours_budget").upsert(row, on_conflict="org_id,store_code").execute()
    return {"ok": True, "store_code": store, "weekly_hours": row["weekly_hours"]}


@router.post("/budget-overrides")
async def request_budget_override(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """A manager requests DM approval to exceed a store's weekly budget. Resolves the DM, saves it
    'pending', emails the DM an FYI. Body: {store_code, week_start, approved_hours?, reason?}."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    store = (body.get("store_code") or "").strip()
    week_start = (body.get("week_start") or "").strip()[:10]
    if not (store and week_start):
        raise HTTPException(400, "store_code and week_start are required")
    dm_eid, dm_email, dm_name = _dm_for_store(org_id, store)
    row = {"org_id": org_id, "store_code": store, "week_start": week_start,
           "approved_hours": body.get("approved_hours"), "reason": body.get("reason"),
           "status": "pending", "requested_by": mgr.get("email"), "requested_by_name": mgr.get("email"),
           "dm_employee_id": dm_eid, "dm_email": dm_email}
    try:
        r = sb().table("budget_override").insert(row).execute()
        oid = (r.data or [{}])[0].get("id")
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 087 applied? {e}")
    emailed = False
    if dm_email:
        try:
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                await email_resend.send_email(
                    to=dm_email,
                    subject=f"Hours-budget override needed — {store} (week of {week_start})",
                    html=(f"<p>{mgr.get('email')} requested to schedule <b>{store}</b> past its weekly "
                          f"hours budget for the week of <b>{week_start}</b>.</p>"
                          f"<p>Reason: {body.get('reason') or '—'}</p>"
                          f"<p>Approve or deny it in MetricsPro → Workforce → Hours Budget.</p>"))
                emailed = True
        except Exception:
            pass
    return {"ok": True, "id": oid, "status": "pending",
            "dm": {"employee_id": dm_eid, "name": dm_name, "email": dm_email, "emailed": emailed},
            "note": None if dm_eid else "No District Manager configured for this store — an admin or DM can still approve."}


@router.get("/budget-overrides")
def list_budget_overrides(status: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    try:
        org_id, _ = _caller_identity(authorization)
    except Exception:
        pass
    q = sb().table("budget_override").select("*").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    rows = q.order("requested_at", desc=True).limit(200).execute().data or []
    return {"overrides": rows}


@router.post("/budget-overrides/{ov_id}/decision")
def decide_budget_override(ov_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The DM (or admin) approves/denies in-app — the tick is the approval, recorded with who+when.
    Approving unlocks scheduling past budget for that store+week. Body: {decision, note?}."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    decision = (body.get("decision") or "").strip().lower()
    if decision not in ("approve", "deny"):
        raise HTTPException(400, "decision must be 'approve' or 'deny'")
    rows = (sb().table("budget_override").select("status").eq("id", ov_id).eq("org_id", org_id)
            .limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "unknown request")
    if rows[0].get("status") != "pending":
        raise HTTPException(409, f"already {rows[0].get('status')}")
    upd = {"status": "approved" if decision == "approve" else "denied",
           "decided_by": mgr.get("email"), "decided_by_name": mgr.get("email"),
           "decided_at": datetime.now(timezone.utc).isoformat(), "decision_note": body.get("note")}
    sb().table("budget_override").update(upd).eq("id", ov_id).eq("org_id", org_id).execute()
    return {"ok": True, "status": upd["status"], "decided_by": mgr.get("email")}


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
    tl = (sb().table("timelog").select("employee_id,hours,clock_out,work_date,store_code")
          .eq("org_id", org_id).gte("work_date", start).lte("work_date", end).limit(20000).execute().data) or []
    mh = (sb().table("manual_hours").select("employee_id,hours")
          .eq("org_id", org_id).gte("work_date", start).lte("work_date", end).limit(5000).execute().data) or []
    ps = (sb().table("payroll_settings").select("*").eq("org_id", org_id).execute().data) or []
    settings = {s["employee_id"]: s for s in ps}
    clocked, manual = {}, {}
    # employee_id -> {store_code: clocked hours}. RULE FIVE (§3d) store filter: attribute to the store the
    # employee actually CLOCKED IN AT THE MOST this pay period — was unconditionally home_store before,
    # which made a store filter meaningless for any floater and could hide their hours from the store
    # manager who actually worked with them.
    store_hours: dict = {}
    for t in tl:
        if t.get("clock_out") and t.get("hours") is not None:   # only closed punches count
            eid = t["employee_id"]
            hrs = float(t["hours"] or 0)
            clocked[eid] = clocked.get(eid, 0.0) + hrs
            st = (t.get("store_code") or "").strip()
            if st:
                sh = store_hours.setdefault(eid, {})
                sh[st] = sh.get(st, 0.0) + hrs
    for m in mh:
        manual[m["employee_id"]] = manual.get(m["employee_id"], 0.0) + float(m["hours"] or 0)

    # UNIVERSAL FALLBACK (2026-07-18, payroll data-flow audit): an employee with a real SCHEDULE
    # (shifts) but ZERO clock punches/manual hours this range used to vanish from Payroll-with-Tax
    # entirely — real "existing platform data" (the schedule) that /payroll-raw ignored outright,
    # unlike /payroll which already falls back to scheduled hours. ADDITIVE ONLY: only employees who
    # are otherwise completely absent here (ch==0 and mhh==0) gain a row; anyone with real clocked or
    # manual hours is untouched byte-for-byte. Tagged `basis:"scheduled"` (vs the normal "clocked") so
    # the UI can flag it as an estimate, not a substitute for an actual punch.
    zero_ids = [e["employee_id"] for e in emps
                if round(clocked.get(e["employee_id"], 0.0), 2) == 0
                and round(manual.get(e["employee_id"], 0.0), 2) == 0]
    scheduled_hours, scheduled_store = {}, {}
    if zero_ids:
        sh_rows = (sb().table("shifts").select("employee_id,store_code,scheduled_hours,shift_date")
                   .eq("org_id", org_id).eq("is_deleted", False)
                   .gte("shift_date", start).lte("shift_date", end)
                   .in_("employee_id", zero_ids).execute().data) or []
        sched_store_hours: dict = {}
        for s in sh_rows:
            eid = s.get("employee_id")
            hrs = float(s.get("scheduled_hours") or 0)
            scheduled_hours[eid] = scheduled_hours.get(eid, 0.0) + hrs
            st = (s.get("store_code") or "").strip()
            if st:
                d = sched_store_hours.setdefault(eid, {})
                d[st] = d.get(st, 0.0) + hrs
        for eid, d in sched_store_hours.items():
            scheduled_store[eid] = max(d.items(), key=lambda kv: kv[1])[0]

    out = []
    for e in emps:
        eid = e["employee_id"]
        ch = round(clocked.get(eid, 0.0), 2)
        mhh = round(manual.get(eid, 0.0), 2)
        s = settings.get(eid) or {}
        basis = "clocked"
        if ch == 0 and mhh == 0:
            sched_h = round(scheduled_hours.get(eid, 0.0), 2)
            if sched_h == 0:
                continue   # genuinely nothing this period (no punch, no manual entry, no schedule)
            store = scheduled_store.get(eid) or (e.get("home_store") or "")
            total_hours = sched_h
            basis = "scheduled"
        else:
            sh = store_hours.get(eid)
            # manual_hours carries no store_code (mig 045) — those hours have no shift to attribute to,
            # so the row still falls back to home_store only when the employee clocked ZERO shifts.
            store = (max(sh.items(), key=lambda kv: kv[1])[0] if sh else (e.get("home_store") or ""))
            total_hours = round(ch + mhh, 2)
        out.append({"employee_id": eid, "name": e.get("name"), "store": store,
                    "pay_rate": float(e.get("pay_rate") or 0), "clocked_hours": ch, "manual_hours": mhh,
                    "total_hours": total_hours, "basis": basis,
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
            c.table("stores").update({"org_unit_id": None}).eq("org_id", org_id).in_("org_unit_id", chunk).execute()
            c.table("employees").update({"org_unit_id": None}).eq("org_id", org_id).in_("org_unit_id", chunk).execute()
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
        c.table("stores").update({"org_unit_id": store_node}).eq("store_code", code).eq("org_id", org_id).execute()
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
    """org_id-scoped so a foreign level_id is a no-op instead of a cross-tenant write."""
    upd = {}
    if "name" in body:
        upd["name"] = (body.get("name") or "").strip()
    if "rank" in body:
        upd["rank"] = int(body.get("rank"))
    if upd:
        sb().table("org_levels").update(upd).eq("id", level_id).eq("org_id", org_id).execute()
    return {"ok": True}


@router.delete("/org/levels/{level_id}")
def org_level_delete(level_id: int, org_id: str = ORG_ID):
    """org_id-scoped so a foreign level_id is a no-op instead of a cross-tenant delete."""
    used = sb().table("org_units").select("id").eq("org_id", org_id).eq("level_id", level_id).limit(1).execute().data or []
    if used:
        raise HTTPException(409, "This level is in use by one or more units — reassign them first.")
    sb().table("org_levels").delete().eq("id", level_id).eq("org_id", org_id).execute()
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
    sb().table("org_units").update(upd).eq("id", unit_id).eq("org_id", org_id).execute()
    return {"ok": True}


@router.delete("/org/units/{unit_id}")
def org_unit_delete(unit_id: str, org_id: str = ORG_ID):
    """Delete a unit + its descendants (cascade). Stores/employees in the subtree are detached
    (org_unit_id -> NULL) FIRST so they become 'unassigned' rather than violating the FK.
    org_id-scoped throughout so a foreign unit_id is a no-op instead of a cross-tenant delete —
    the RPC already filters by org, but the final delete previously ran unconditionally even when
    the subtree resolved empty (i.e. the id wasn't the caller's)."""
    sub = sb().rpc("org_subtree", {"p_org_id": org_id, "p_unit_id": unit_id}).execute().data or []
    ids = [n["id"] for n in sub if n.get("id")]
    if ids:
        sb().table("stores").update({"org_unit_id": None}).eq("org_id", org_id).in_("org_unit_id", ids).execute()
        sb().table("employees").update({"org_unit_id": None}).eq("org_id", org_id).in_("org_unit_id", ids).execute()
    sb().table("org_units").delete().eq("id", unit_id).eq("org_id", org_id).execute()
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
    """org_id-scoped so a foreign unit_id/employee_id pair is a no-op instead of a cross-tenant delete."""
    sb().table("org_managers").delete().eq("unit_id", unit_id).eq("employee_id", employee_id).eq("org_id", org_id).execute()
    return {"ok": True}


# ── attach a store to a unit (or unassign with unit_id=null) ─────────────────────────────────────
@router.put("/org/stores/{store_code}/unit")
def org_assign_store(store_code: str, body: dict, org_id: str = ORG_ID):
    """org_id-scoped so a foreign store_code is a no-op instead of a cross-tenant write — this
    previously took NO org filter at all."""
    sb().table("stores").update({"org_unit_id": body.get("unit_id")}).eq("store_code", store_code).eq("org_id", org_id).execute()
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


@router.get("/employees/visible")
def employees_visible(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The employee roster the SIGNED-IN caller may pick from on the dashboard, scoped by their role:
    self → just themselves, store → their store('s) employees, market/DM → their markets' stores'
    employees, admin/'all' (or an unidentifiable caller) → everyone. Uses _caller_span_codes (org tree
    UNION the market/store pinned on the login) so it works even before the RBAC master switch is on —
    it only bounds a dropdown, it is NOT the security boundary. Returns the caller's own employee_id so
    the dashboard can default to self."""
    c = sb()
    au = _caller_app_user(authorization, org_id)
    my_eid = (au.get("employee_id") or "").strip()
    scope = _role_scope(org_id, (au.get("role") or "").strip()) if au else "all"
    rows = (c.table("employees").select("employee_id,name,home_store,role,is_active")
            .eq("org_id", org_id).execute().data or [])
    rows = [r for r in rows if r.get("employee_id") and r.get("is_active", True)]
    if au and scope != "all":
        if scope == "self":
            rows = [r for r in rows if str(r.get("employee_id")) == my_eid]
        else:
            codes = {x.strip().upper() for x in _caller_span_codes(authorization, org_id)}
            keys = set(codes)
            if codes:   # widen to each store's address too — home_store may be an address, not a code
                meta = c.table("stores").select("store_code,address").eq("org_id", org_id).execute().data or []
                for s in meta:
                    if str(s.get("store_code") or "").strip().upper() in codes:
                        ad = str(s.get("address") or "").strip().upper()
                        if ad:
                            keys.add(ad)
            rows = [r for r in rows
                    if str(r.get("home_store") or "").strip().upper() in keys
                    or str(r.get("employee_id")) == my_eid]
    rows.sort(key=lambda r: (str(r.get("home_store") or ""), str(r.get("name") or "")))
    return {"employee_id": my_eid, "scope": scope,
            "is_manager": scope not in ("self",), "employees": rows}


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


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# PTO ACCRUAL ("Paid Leave Accumulated") — migration 403, band 400-499. RULE TWO: rate/mode/cap/basis
# are CONFIG (storeops.pto_accrual_config, layered org -> role -> employee override), never hard-coded.
# The pure math lives in pto_accrual.py (unit-tested in harness_pto_accrual.py) — everything here is
# I/O: fetch rows, resolve effective config per employee, call the engine, persist a ledger, and push
# the per-store cost to mod-commission's Store Expenses via the shared "system-line" contract.
#
# MONEY-ADJACENT: this produces a NEW, ADDITIVE expense line. It never touches an existing payroll
# payout number (shifts/pay_rate/timelog are only READ here, never written).
# ═══════════════════════════════════════════════════════════════════════════════════════════════

# The commcalc "system-line" receiver is a SEPARATE mod-commission package (not built as of this
# writing — see docs/handoffs/people.md). Calls degrade gracefully: a 404 just means "not deployed
# yet", logged and swallowed — the ledger is still persisted so the numbers can be pulled later via
# GET /pto-accrual/{period} instead of push. INTERNAL_API_BASE_URL lets an operator point this at a
# non-default loopback if the backend's own bind address ever changes; the Railway container binds
# 0.0.0.0:8000 (Dockerfile), so 127.0.0.1:8000 is the correct same-process default.
PTO_INTERNAL_API_BASE = os.environ.get("INTERNAL_API_BASE_URL") or "http://127.0.0.1:8000"


def _pto_config_rows(org_id: str) -> dict:
    """Fetch every pto_accrual_config row for the org, split by scope. Degrades gracefully (empty
    dict/None) if migration 403 hasn't run yet — a missing table must never break payroll or the
    Store Expenses fill, per contract §5."""
    try:
        rows = sb().table("pto_accrual_config").select("*").eq("org_id", org_id).execute().data or []
    except Exception:
        rows = []
    org_row = next((r for r in rows if r.get("scope") == "org"), None)
    role_rows = {r.get("role"): r for r in rows if r.get("scope") == "role" and r.get("role")}
    emp_rows = {r.get("employee_id"): r for r in rows if r.get("scope") == "employee" and r.get("employee_id")}
    return {"org": org_row, "roles": role_rows, "employees": emp_rows}


def _pto_cfg_for(cfg_rows: dict, employee_id: str, role: str) -> dict:
    return pto_resolve_effective_config(cfg_rows.get("org"), cfg_rows.get("roles", {}).get(role),
                                         cfg_rows.get("employees", {}).get(employee_id))


@router.get("/pto-accrual-config")
def get_pto_accrual_config(org_id: str = ORG_ID):
    """Admin view of the layered PTO accrual config: the org default (falls back to the code default
    if migration 403 hasn't run / no row saved yet), plus every role- and employee-level override row,
    for the admin UI to render and edit."""
    rows_raw = []
    try:
        rows_raw = sb().table("pto_accrual_config").select("*").eq("org_id", org_id).execute().data or []
    except Exception:
        pass
    org_row = next((r for r in rows_raw if r.get("scope") == "org"), None)
    role_rows = [r for r in rows_raw if r.get("scope") == "role"]
    emp_rows = [r for r in rows_raw if r.get("scope") == "employee"]
    effective_org = pto_resolve_effective_config(org_row, None, None)
    return {"org_row": org_row, "effective_org_defaults": effective_org,
            "role_overrides": role_rows, "employee_overrides": emp_rows,
            "code_defaults": PTO_DEFAULT_CONFIG}


_PTO_CFG_FIELDS = ("enabled", "accrual_rate", "mode", "cost_basis", "max_accrual_hours",
                    "hours_per_pto_day", "counts_as_pto_types")


@router.put("/pto-accrual-config")
def put_pto_accrual_config(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Upsert one config row: {scope:'org'|'role'|'employee', role?, employee_id?, ...fields}. Only
    fields present in the body are written — omitted fields on a role/employee override row mean
    "inherit" (see pto_accrual.resolve_effective_config). Manager-gated (this changes a cost the
    business books every payroll run)."""
    _require_manager(authorization, org_id)
    scope = (body.get("scope") or "org").strip().lower()
    if scope not in ("org", "role", "employee"):
        raise HTTPException(400, "scope must be 'org', 'role', or 'employee'")
    role = (body.get("role") or "").strip() if scope == "role" else None
    employee_id = (body.get("employee_id") or "").strip() if scope == "employee" else None
    if scope == "role" and not role:
        raise HTTPException(400, "role is required when scope='role'")
    if scope == "employee" and not employee_id:
        raise HTTPException(400, "employee_id is required when scope='employee'")

    fields = {k: body[k] for k in _PTO_CFG_FIELDS if k in body}
    if "mode" in fields and fields["mode"] not in ("accrue", "on_use"):
        raise HTTPException(400, "mode must be 'accrue' or 'on_use'")

    q = sb().table("pto_accrual_config").select("id").eq("org_id", org_id).eq("scope", scope)
    q = q.eq("role", role) if scope == "role" else q
    q = q.eq("employee_id", employee_id) if scope == "employee" else q
    existing = (q.execute().data or [])

    row = {**fields, "updated_at": datetime.now(timezone.utc).isoformat(),
           "updated_by": body.get("updated_by") or "admin"}
    if existing:
        sb().table("pto_accrual_config").update(row).eq("id", existing[0]["id"]).eq("org_id", org_id).execute()
        return {"ok": True, "id": existing[0]["id"], "scope": scope}
    row.update({"org_id": org_id, "scope": scope, "role": role, "employee_id": employee_id})
    if scope == "org":
        # The org row is the base every other layer inherits from — always fully populated so a
        # partial PUT (e.g. just {"accrual_rate": 0.04}) never leaves an unset field ambiguous.
        row = {**PTO_DEFAULT_CONFIG, **row}
    r = sb().table("pto_accrual_config").insert(row).execute()
    return {"ok": True, "id": (r.data or [{}])[0].get("id"), "scope": scope}


@router.delete("/pto-accrual-config/{config_id}")
def delete_pto_accrual_config(config_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Remove one override row (org-scoped so a foreign id is a no-op). Deleting an override just
    means that role/employee falls back to the next layer down — never destructive to any computed
    ledger data already persisted."""
    _require_manager(authorization, org_id)
    sb().table("pto_accrual_config").delete().eq("id", config_id).eq("org_id", org_id).execute()
    return {"ok": True}


def _pto_gather(org_id: str, period: str):
    """Shared fetch+compute for both the read-only GET and the persisting POST /run. Returns
    (result, meta) where result is compute_pto's {"employees","stores"} and meta carries the
    effective org config + period bounds for the caller to use."""
    period_start, period_end = pto_month_bounds(period)
    ps, pe = period_start.isoformat(), period_end.isoformat()

    employees = (sb().table("employees").select("employee_id,name,home_store,pay_rate,role,is_active")
                 .eq("org_id", org_id).execute().data) or []
    emp_by_id = {e["employee_id"]: e for e in employees if e.get("employee_id")}

    shifts = (sb().table("shifts").select("employee_id,store_code,scheduled_hours,actual_hours,shift_date")
              .eq("org_id", org_id).eq("is_deleted", False)
              .gte("shift_date", ps).lte("shift_date", pe).execute().data) or []
    hours_by_emp_store = pto_hours_worked_from_shifts(shifts)

    time_off_raw = (sb().table("time_off_requests").select("employee_id,start_date,end_date,type,status")
                     .eq("org_id", org_id).eq("status", "approved")
                     .lte("start_date", pe).gte("end_date", ps).execute().data) or []
    time_off_by_emp = {}
    for r in time_off_raw:
        time_off_by_emp.setdefault(r.get("employee_id"), []).append(r)

    cfg_rows = _pto_config_rows(org_id)
    active_eids = set(hours_by_emp_store) | set(time_off_by_emp)
    cfg_by_emp, rates, home_store, names, taken_by_emp = {}, {}, {}, {}, {}
    for eid in active_eids:
        emp = emp_by_id.get(eid, {})
        cfg = _pto_cfg_for(cfg_rows, eid, (emp.get("role") or ""))
        cfg_by_emp[eid] = cfg
        rates[eid] = float(emp.get("pay_rate") or 0)
        home_store[eid] = emp.get("home_store") or ""
        names[eid] = emp.get("name") or ""
        taken_map = pto_taken_hours_from_time_off(time_off_by_emp.get(eid, []), period_start, period_end,
                                                    cfg["counts_as_pto_types"], cfg["hours_per_pto_day"])
        taken_by_emp[eid] = taken_map.get(eid, 0.0)

    prior_balance = {}
    if active_eids:
        try:
            hist = (sb().table("pto_accrual_ledger").select("employee_id,accrued_hours,taken_hours")
                    .eq("org_id", org_id).lt("period", period).execute().data) or []
            for h in hist:
                eid = h.get("employee_id")
                if eid in active_eids:
                    prior_balance[eid] = prior_balance.get(eid, 0.0) + float(h.get("accrued_hours") or 0) - float(h.get("taken_hours") or 0)
        except Exception:
            prior_balance = {}

    result = compute_pto(hours_by_emp_store, taken_by_emp, rates, cfg_by_emp,
                          home_store_by_employee=home_store, prior_balance_by_employee=prior_balance,
                          employee_names=names)
    org_effective = pto_resolve_effective_config(cfg_rows.get("org"), None, None)
    return result, {"period_start": ps, "period_end": pe, "org_effective": org_effective}


@router.get("/pto-accrual/{period}")
def get_pto_accrual(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Read-only PTO accrual view for a period (YYYY-MM) — for display/debug and so the expense side
    (mod-commission) can re-pull the numbers directly if the push-on-run didn't land. Always computed
    LIVE from current shifts/time-off/config (never reads the ledger for the numbers themselves — the
    ledger is only consulted for `last_run_at` and for each employee's PRIOR-period running balance)."""
    result, meta = _pto_gather(org_id, period)
    last_run_at = None
    try:
        rows = (sb().table("pto_accrual_ledger").select("run_at").eq("org_id", org_id).eq("period", period)
                .order("run_at", desc=True).limit(1).execute().data) or []
        last_run_at = rows[0]["run_at"] if rows else None
    except Exception:
        pass
    ks = scope_keyset(authorization, org_id)
    stores = [d for s, d in sorted(result["stores"].items()) if in_keyset(ks, s)]
    employees = [e for e in result["employees"].values() if in_keyset(ks, e.get("store"))]
    return {"period": period, "mode": meta["org_effective"]["mode"], "rate": meta["org_effective"]["accrual_rate"],
            "stores": stores, "employees": sorted(employees, key=lambda r: r.get("name") or ""),
            "last_run_at": last_run_at}


def _pto_push_expense_line(org_id: str, period: str, cells: list) -> dict:
    """POST the per-store cost to mod-commission's Store Expenses system-line endpoint (shared
    contract — see docs/handoffs/people.md). Best-effort: any failure (404 = package not deployed
    yet, timeout, connection error) is caught and reported, NEVER raised — the ledger is already
    persisted by the time this runs, so the numbers aren't lost; they can be pulled later via
    GET /pto-accrual/{period}."""
    url = f"{PTO_INTERNAL_API_BASE}/api/v1/commcalc/expenses/{period}/system-line"
    body = {"source_key": "pto_accrual", "label": "Paid Leave Accumulated", "cells": cells}
    try:
        resp = requests.post(url, params={"org_id": org_id}, json=body, timeout=10)
        if resp.status_code == 404:
            return {"pushed": False, "status": 404, "note": "system-line endpoint not deployed yet (mod-commission package pending) — ledger persisted, pull via GET /pto-accrual/{period} instead"}
        resp.raise_for_status()
        return {"pushed": True, "status": resp.status_code}
    except Exception as e:
        return {"pushed": False, "status": None, "note": f"push failed ({type(e).__name__}: {e}) — ledger persisted, pull via GET /pto-accrual/{{period}} instead"}


@router.post("/pto-accrual/run/{period}")
def run_pto_accrual(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The payroll-run hook: compute this period's PTO accrual, persist an idempotent ledger
    (delete-by-(org,period) then insert — safe to re-run e.g. after a shift correction), and push the
    per-store cost to Store Expenses as an ADDITIVE system line. Manager-gated (this is what finalizes
    a cost the business books). NEVER writes to shifts/timelog/employees — read-only against payroll
    inputs, so it cannot change what anyone is paid."""
    u = _require_manager(authorization, org_id)
    result, meta = _pto_gather(org_id, period)
    run_by = u.get("email") or u.get("employee_id") or "manager"

    rows = pto_ledger_rows(org_id, period, result, run_by=run_by)
    sb().table("pto_accrual_ledger").delete().eq("org_id", org_id).eq("period", period).execute()
    if rows:
        for i in range(0, len(rows), 500):
            sb().table("pto_accrual_ledger").insert(rows[i:i + 500]).execute()

    cells = pto_expense_cells_from_stores(result["stores"])
    push = _pto_push_expense_line(org_id, period, cells) if cells else {"pushed": False, "status": None, "note": "no store activity this period — nothing to push"}

    return {"period": period, "mode": meta["org_effective"]["mode"], "rate": meta["org_effective"]["accrual_rate"],
            "employees": sorted(result["employees"].values(), key=lambda r: r.get("name") or ""),
            "stores": [d for _, d in sorted(result["stores"].items())],
            "ledger_rows_written": len(rows), "push": push}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# PAYROLL EXPENSES (payroll tax + operator-customizable employer-burden items) — migration 404, band
# 400-499. RULE TWO: tax rates/wage-bases and every item's calc_method/rate/scope are CONFIG
# (storeops.payroll_tax_config + storeops.payroll_expense_item), never hard-coded. The pure math lives
# in payroll_expenses.py (unit-tested in harness_payroll_expenses.py) — everything here is I/O: fetch
# rows, resolve config, call the engine, persist 2 ledgers, and push ONE rolled-up "Payroll Expenses"
# line to mod-commission's Store Expenses via the same "system-line" contract the PTO package uses.
#
# MONEY-ADJACENT: this produces a NEW, ADDITIVE expense line. It never touches an existing payroll
# payout number (shifts/pay_rate/timelog are only READ here, never written).
# ═══════════════════════════════════════════════════════════════════════════════════════════════

@router.get("/payroll-tax-config")
def get_payroll_tax_config(org_id: str = ORG_ID):
    """Admin view of the org's payroll tax config (falls back to code defaults if migration 404 hasn't
    run / no row saved yet)."""
    org_row = None
    try:
        rows = sb().table("payroll_tax_config").select("*").eq("org_id", org_id).limit(1).execute().data or []
        org_row = rows[0] if rows else None
    except Exception:
        pass
    return {"row": org_row, "effective": payex_resolve_tax_config(org_row), "code_defaults": PAYEX_DEFAULT_TAX_CONFIG}


_PAYEX_TAX_FIELDS = ("enabled", "fica_ss_rate", "fica_ss_wage_base", "medicare_rate",
                      "futa_rate", "futa_wage_base", "suta_rate", "suta_wage_base")


@router.put("/payroll-tax-config")
def put_payroll_tax_config(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Upsert the org's ONE payroll tax config row. Manager-gated (changes a cost the business books
    every payroll run)."""
    _require_manager(authorization, org_id)
    fields = {k: body[k] for k in _PAYEX_TAX_FIELDS if k in body}
    row = {**fields, "updated_at": datetime.now(timezone.utc).isoformat(),
           "updated_by": body.get("updated_by") or "admin"}
    existing = (sb().table("payroll_tax_config").select("id").eq("org_id", org_id).limit(1).execute().data or [])
    if existing:
        sb().table("payroll_tax_config").update(row).eq("id", existing[0]["id"]).eq("org_id", org_id).execute()
        return {"ok": True, "id": existing[0]["id"]}
    row.update({"org_id": org_id, **{k: v for k, v in PAYEX_DEFAULT_TAX_CONFIG.items() if k not in row}})
    r = sb().table("payroll_tax_config").insert(row).execute()
    return {"ok": True, "id": (r.data or [{}])[0].get("id")}


@router.get("/payroll-expense-items")
def get_payroll_expense_items(org_id: str = ORG_ID):
    """List every payroll expense item (Unemployment Insurance / Workers Comp / custom) for the org,
    in the operator's chosen sort_order."""
    try:
        rows = sb().table("payroll_expense_item").select("*").eq("org_id", org_id).order("sort_order").execute().data or []
    except Exception:
        rows = []
    return {"items": rows, "calc_methods": list(PAYEX_CALC_METHODS), "scopes": list(PAYEX_ITEM_SCOPES)}


_PAYEX_ITEM_FIELDS = ("key", "name", "calc_method", "rate_or_amount", "wage_cap", "scope", "enabled", "sort_order")


@router.post("/payroll-expense-items")
def create_payroll_expense_item(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Add a custom payroll expense item (Unemployment Insurance / Workers Comp are seeded by
    migration 404; this is for any ADDITIONAL operator-defined item). Manager-gated."""
    _require_manager(authorization, org_id)
    key = (body.get("key") or "").strip().lower().replace(" ", "_")
    name = (body.get("name") or "").strip()
    if not key or not name:
        raise HTTPException(400, "key and name are required")
    calc_method = body.get("calc_method") or "pct_wages"
    if calc_method not in PAYEX_CALC_METHODS:
        raise HTTPException(400, f"calc_method must be one of {PAYEX_CALC_METHODS}")
    scope = body.get("scope") or "store"
    if scope not in PAYEX_ITEM_SCOPES:
        raise HTTPException(400, f"scope must be one of {PAYEX_ITEM_SCOPES}")
    row = {"org_id": org_id, "key": key, "name": name, "calc_method": calc_method,
           "rate_or_amount": float(body.get("rate_or_amount") or 0),
           "wage_cap": (None if body.get("wage_cap") in (None, "") else float(body.get("wage_cap"))),
           "scope": scope, "enabled": bool(body.get("enabled", True)),
           "sort_order": int(body.get("sort_order") or 0), "updated_by": body.get("updated_by") or "admin"}
    try:
        r = sb().table("payroll_expense_item").insert(row).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(400, f"an item with key '{key}' already exists")
        raise
    return {"ok": True, "id": (r.data or [{}])[0].get("id")}


@router.patch("/payroll-expense-items/{item_id}")
def update_payroll_expense_item(item_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Edit an existing item (rate, calc_method, scope, enabled, …). Org-scoped so a foreign id is a
    no-op. Manager-gated."""
    _require_manager(authorization, org_id)
    fields = {k: body[k] for k in _PAYEX_ITEM_FIELDS if k in body}
    if "calc_method" in fields and fields["calc_method"] not in PAYEX_CALC_METHODS:
        raise HTTPException(400, f"calc_method must be one of {PAYEX_CALC_METHODS}")
    if "scope" in fields and fields["scope"] not in PAYEX_ITEM_SCOPES:
        raise HTTPException(400, f"scope must be one of {PAYEX_ITEM_SCOPES}")
    if "wage_cap" in fields and fields["wage_cap"] in ("", None):
        fields["wage_cap"] = None
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    fields["updated_by"] = body.get("updated_by") or "admin"
    sb().table("payroll_expense_item").update(fields).eq("id", item_id).eq("org_id", org_id).execute()
    return {"ok": True}


@router.delete("/payroll-expense-items/{item_id}")
def delete_payroll_expense_item(item_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Remove a custom item (org-scoped). Manager-gated. Deleting an item stops it from contributing to
    FUTURE runs; past ledger rows already persisted are untouched (historical audit trail)."""
    _require_manager(authorization, org_id)
    sb().table("payroll_expense_item").delete().eq("id", item_id).eq("org_id", org_id).execute()
    return {"ok": True}


def _payex_gather(org_id: str, period: str):
    """Shared fetch+compute for both the read-only GET and the persisting POST /run. Reuses the SAME
    shifts/rate basis pto_accrual.py + /payroll-by-store use (hours = actual if clocked else
    scheduled, wages = hours * pay_rate, attributed to the shift's own store_code)."""
    employees = (sb().table("employees").select("employee_id,name,home_store,pay_rate")
                 .eq("org_id", org_id).execute().data) or []
    rates = {e["employee_id"]: float(e.get("pay_rate") or 0) for e in employees if e.get("employee_id")}
    names = {e["employee_id"]: e.get("name") or "" for e in employees if e.get("employee_id")}

    period_start, period_end = pto_month_bounds(period)      # reuse the same 'YYYY-MM' bounds helper
    ps, pe = period_start.isoformat(), period_end.isoformat()
    shifts = (sb().table("shifts").select("employee_id,store_code,scheduled_hours,actual_hours,shift_date")
              .eq("org_id", org_id).eq("is_deleted", False)
              .gte("shift_date", ps).lte("shift_date", pe).execute().data) or []
    hours_by_emp_store = pto_hours_worked_from_shifts(shifts)     # reuse the PTO engine's shift reader

    tax_org_row = None
    try:
        rows = sb().table("payroll_tax_config").select("*").eq("org_id", org_id).limit(1).execute().data or []
        tax_org_row = rows[0] if rows else None
    except Exception:
        pass
    tax_cfg = payex_resolve_tax_config(tax_org_row)

    year = str(period).split("-")[0]
    ytd_taxable_before: dict = {}
    try:
        hist = (sb().table("payroll_tax_ledger")
                .select("employee_id,ss_taxable_wages,futa_taxable_wages,suta_taxable_wages")
                .eq("org_id", org_id).gte("period", f"{year}-01").lt("period", period).execute().data) or []
        for h in hist:
            eid = h.get("employee_id")
            if not eid:
                continue
            d = ytd_taxable_before.setdefault(eid, {"ss": 0.0, "futa": 0.0, "suta": 0.0})
            d["ss"] += float(h.get("ss_taxable_wages") or 0)
            d["futa"] += float(h.get("futa_taxable_wages") or 0)
            d["suta"] += float(h.get("suta_taxable_wages") or 0)
    except Exception:
        ytd_taxable_before = {}

    tax_result = compute_payroll_tax(hours_by_emp_store, rates, tax_cfg, ytd_taxable_before)

    items = []
    try:
        items = sb().table("payroll_expense_item").select("*").eq("org_id", org_id).order("sort_order").execute().data or []
    except Exception:
        items = []
    wages_by_store = payex_wages_by_store(hours_by_emp_store, rates)
    headcount_by_store = payex_headcount_by_store(hours_by_emp_store)
    company_headcount = len({eid for eid, by_store in hours_by_emp_store.items()
                              if any(float(h or 0) > 0 for h in by_store.values())})
    item_result = compute_expense_items(wages_by_store, headcount_by_store, company_headcount, items)

    cells = payex_rollup_cells(tax_result["stores"], item_result["stores"])
    return {"tax": tax_result, "items": item_result, "cells": cells, "tax_cfg": tax_cfg,
            "wages_by_store": wages_by_store, "headcount_by_store": headcount_by_store,
            "employee_names": names}


@router.get("/payroll-expenses/{period}")
def get_payroll_expenses(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Read-only Payroll Expenses view for a period (YYYY-MM): the itemized tax + item breakdown per
    store, and the single rolled-up total that would be pushed to Store Expenses. Always computed
    LIVE (never reads the ledger for the numbers — only for `last_run_at`). Also surfaces the
    separate GROSS PAYROLL cells (source_key='payroll_gross') that /run pushes as its own P&L line —
    see gross_cells / gross_last_run_at (migration 405; degrades to [] / None until it's applied)."""
    g = _payex_gather(org_id, period)
    last_run_at = None
    try:
        rows = (sb().table("payroll_expense_ledger").select("run_at").eq("org_id", org_id).eq("period", period)
                .order("run_at", desc=True).limit(1).execute().data) or []
        last_run_at = rows[0]["run_at"] if rows else None
    except Exception:
        pass
    gross_last_run_at = None
    try:
        rows = (sb().table("payroll_gross_ledger").select("run_at").eq("org_id", org_id).eq("period", period)
                .order("run_at", desc=True).limit(1).execute().data) or []
        gross_last_run_at = rows[0]["run_at"] if rows else None
    except Exception:
        pass
    ks = scope_keyset(authorization, org_id)
    stores = sorted(set(g["tax"]["stores"]) | set(g["items"]["stores"]))
    if ks is not None:
        stores = [s for s in stores if in_keyset(ks, s)]
    stores_out = []
    for s in stores:
        tax_d = g["tax"]["stores"].get(s, {})
        item_d = g["items"]["stores"].get(s, {})
        stores_out.append({
            "store": s, "wages": tax_d.get("wages", 0.0),
            "fica_ss": tax_d.get("fica_ss", 0.0), "medicare": tax_d.get("medicare", 0.0),
            "futa": tax_d.get("futa", 0.0), "suta": tax_d.get("suta", 0.0),
            "tax_total": tax_d.get("total", 0.0),
            "items": item_d, "items_total": round(sum(item_d.values()), 2),
            "total": round(tax_d.get("total", 0.0) + sum(item_d.values()), 2),
        })
    cells = [c for c in g["cells"] if ks is None or in_keyset(ks, c["store"])]
    gross_cells_all = payex_gross_payroll_cells(g["wages_by_store"])
    gross_cells = [c for c in gross_cells_all if ks is None or in_keyset(ks, c["store"])]
    return {"period": period, "tax_cfg": g["tax_cfg"], "items": g["items"]["items"],
            "stores": stores_out, "cells": cells, "last_run_at": last_run_at,
            "gross_cells": gross_cells, "gross_last_run_at": gross_last_run_at}


def _payex_push_expense_line(org_id: str, period: str, cells: list) -> dict:
    """POST the single rolled-up per-store cost to mod-commission's Store Expenses system-line
    endpoint (source_key='payroll_expenses', label='Payroll Expenses' — the SAME shared contract the
    PTO package pushes 'pto_accrual' through, a DIFFERENT source_key so the two coexist as separate
    lines). Same best-effort contract as PTO's push: any failure is caught and reported, NEVER raised
    — both ledgers are already persisted by the time this runs."""
    url = f"{PTO_INTERNAL_API_BASE}/api/v1/commcalc/expenses/{period}/system-line"
    body = {"source_key": "payroll_expenses", "label": "Payroll Expenses", "cells": cells}
    try:
        resp = requests.post(url, params={"org_id": org_id}, json=body, timeout=10)
        if resp.status_code == 404:
            return {"pushed": False, "status": 404, "note": "system-line endpoint not deployed yet — ledger persisted, pull via GET /payroll-expenses/{period} instead"}
        resp.raise_for_status()
        return {"pushed": True, "status": resp.status_code}
    except Exception as e:
        return {"pushed": False, "status": None, "note": f"push failed ({type(e).__name__}: {e}) — ledger persisted, pull via GET /payroll-expenses/{{period}} instead"}


def _payex_push_gross_line(org_id: str, period: str, cells: list) -> dict:
    """POST the per-store GROSS PAYROLL — the exact wages basis the burden calc above uses — to
    mod-commission's Store Expenses system-line endpoint as its OWN line: source_key='payroll_gross',
    label='Gross Payroll'. OWNER DECISION 2026-07-15: this is a DIFFERENT source_key than
    'payroll_expenses' (and than PTO's 'pto_accrual') so all three coexist as separate, non-
    double-counting P&L lines — gross wages vs. employer burden vs. accrued PTO are distinct costs.
    Same best-effort contract as the other pushes here: any failure is caught and reported, NEVER
    raised — the gross ledger is already persisted (when migration 405 has run) by the time this
    executes."""
    url = f"{PTO_INTERNAL_API_BASE}/api/v1/commcalc/expenses/{period}/system-line"
    body = {"source_key": "payroll_gross", "label": "Gross Payroll", "cells": cells}
    try:
        resp = requests.post(url, params={"org_id": org_id}, json=body, timeout=10)
        if resp.status_code == 404:
            return {"pushed": False, "status": 404, "note": "system-line endpoint not deployed yet — ledger persisted, pull via GET /payroll-expenses/{period} instead"}
        resp.raise_for_status()
        return {"pushed": True, "status": resp.status_code}
    except Exception as e:
        return {"pushed": False, "status": None, "note": f"push failed ({type(e).__name__}: {e}) — ledger persisted, pull via GET /payroll-expenses/{{period}} instead"}


@router.post("/payroll-expenses/run/{period}")
def run_payroll_expenses(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The payroll-run hook: compute this period's payroll tax + expense items, persist BOTH ledgers
    idempotently (delete-by-(org,period) then insert — safe to re-run e.g. after a shift correction),
    and push ONE rolled-up 'Payroll Expenses' line to Store Expenses. Manager-gated. NEVER writes to
    shifts/timelog/employees — read-only against payroll inputs, so it cannot change what anyone is
    paid.

    Also computes + pushes the SEPARATE 'Gross Payroll' line (source_key='payroll_gross') on the SAME
    run — OWNER DECISION 2026-07-15: the exact $ paid to employees (g['wages_by_store'], the identical
    wage base the tax bucket above already uses), persisted to storeops.payroll_gross_ledger
    (migration 405) and pushed via the identical system-line contract, so the P&L can show Gross
    Payroll and Payroll Expenses as two distinct, non-double-counting lines. Purely ADDITIVE: never
    modifies payroll_tax_ledger / payroll_expense_ledger / the 'payroll_expenses' push above, and
    degrades gracefully (push still attempted, run still succeeds) if migration 405 hasn't run yet."""
    u = _require_manager(authorization, org_id)
    run_by = u.get("email") or u.get("employee_id") or "manager"
    g = _payex_gather(org_id, period)

    tax_rows = payex_tax_ledger_rows(org_id, period, g["tax"], run_by=run_by)
    sb().table("payroll_tax_ledger").delete().eq("org_id", org_id).eq("period", period).execute()
    if tax_rows:
        for i in range(0, len(tax_rows), 500):
            sb().table("payroll_tax_ledger").insert(tax_rows[i:i + 500]).execute()

    exp_rows = payex_expense_ledger_rows(org_id, period, g["tax"], g["items"], run_by=run_by)
    sb().table("payroll_expense_ledger").delete().eq("org_id", org_id).eq("period", period).execute()
    if exp_rows:
        for i in range(0, len(exp_rows), 500):
            sb().table("payroll_expense_ledger").insert(exp_rows[i:i + 500]).execute()

    push = _payex_push_expense_line(org_id, period, g["cells"]) if g["cells"] else {"pushed": False, "status": None, "note": "no store activity this period — nothing to push"}

    # ── Gross Payroll (additive, migration 405) — org-scoped write, wrapped so a not-yet-applied
    # migration degrades gracefully (the push still fires; only the audit-ledger persist is skipped).
    gross_cells = payex_gross_payroll_cells(g["wages_by_store"])
    gross_rows = payex_gross_payroll_ledger_rows(org_id, period, g["wages_by_store"],
                                                  g.get("headcount_by_store"), run_by=run_by)
    gross_ledger_rows_written = 0
    try:
        sb().table("payroll_gross_ledger").delete().eq("org_id", org_id).eq("period", period).execute()
        if gross_rows:
            for i in range(0, len(gross_rows), 500):
                sb().table("payroll_gross_ledger").insert(gross_rows[i:i + 500]).execute()
        gross_ledger_rows_written = len(gross_rows)
    except Exception as e:
        gross_ledger_rows_written = None  # migration 405 likely not applied yet — ledger skipped, push still attempted below
        _gross_ledger_error = f"{type(e).__name__}: {e}"
    else:
        _gross_ledger_error = None

    gross_push = _payex_push_gross_line(org_id, period, gross_cells) if gross_cells else {"pushed": False, "status": None, "note": "no store activity this period — nothing to push"}

    return {"period": period, "tax_cfg": g["tax_cfg"], "items": g["items"]["items"],
            "cells": g["cells"], "tax_ledger_rows_written": len(tax_rows),
            "expense_ledger_rows_written": len(exp_rows), "push": push,
            "gross_cells": gross_cells, "gross_ledger_rows_written": gross_ledger_rows_written,
            "gross_ledger_error": _gross_ledger_error, "gross_push": gross_push}
