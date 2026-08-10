"""StoreOps API Router — /api/v1/storeops/*"""
import base64
import os
import requests
import time
from datetime import datetime, timezone, timedelta, date as _date
from fastapi import APIRouter, HTTPException, Header, BackgroundTasks, Response
from app.core.database import get_supabase
from app.core.config import settings
from app.core import scope as _cscope
from app.modules.storeops import google_reviews as _gr
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
from app.modules.storeops.payroll_identity import (
    business_id_alias_map as _business_id_alias_map,
    reconcile_employee_identity as _reconcile_employee_identity,
)
from app.modules.storeops import payroll_salary
from app.modules.storeops.lunch_deduction import (
    get_lunch_config as _lunch_get_config,
    get_tenant_lunch_config as _lunch_get_tenant_config,
    compute_lunch_deduction_from_rows as _lunch_compute_from_rows,
    period_lunch_deduction as _lunch_period_deduction,
)
from app.modules.storeops import face_recognition as _face
from app.modules.storeops import face_retention as _fret
from app.modules.storeops import salary_owed as _owed
from app.modules.storeops import target_attribution as _dmta
from app.modules.storeops import attendance_exceptions as _attn

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


# ── Disabled-store leak fix (2026-08-06 owner report, verbatim: "t-902 / 531 etc all t-stores have
# been disabled but they still show in time clock, targets etc reports - check and remove") ────────
# ROOT CAUSE: storeops.stores.is_active was correctly set to false for the 6 disabled T-stores (T-531,
# T-7812, T-902, T-957, T21880, T3560 — confirmed live via the read-only prod probe), but GET /stores
# (which feeds pickers/dropdowns across the app) and GET /timeclock/stores (the kiosk clock-in picker,
# whose own docstring falsely claimed "active store list") never filtered the flag at all.
def _store_is_active(s: dict) -> bool:
    """NULL-SAFE 'is this store active' check. `storeops.stores.is_active` is a NULLABLE column
    (`DEFAULT true`, no NOT NULL) — the SAME trap `_inactive_ids_from` (below, ~line 554) was already
    hard-won on the employee side: NULL/missing MUST read as ACTIVE, matching the column's own default
    and every frontend picker's established `s.is_active !== false` convention. Only an EXPLICIT
    `is_active=false` counts as inactive.

    Deliberately a PYTHON-side post-fetch check, not a PostgREST `is_active=not.is.false` query filter.
    That filter is real (PostgREST's `is` operator negated by a `not.` prefix does mean exactly
    'IS NOT FALSE' server-side) and was evaluated — but postgrest-py's own `.is_()` only special-cases
    Python `None` (-> the string "null"); a raw Python `False` passed to `.not_.is_("is_active", False)`
    f-strings straight into the query as `not.is.False` (CAPITAL F, verified by constructing the actual
    installed client), which is not one of PostgREST's recognized `true|false|null|unknown` literals —
    a real footgun in the naive form, avoidable only by passing the literal string `"false"` instead.
    Given no way to verify the raw wire behavior against live Postgres from this environment (read-only
    prod probe, no write access) and the blast radius a wrong exclusionary DB filter would have (every
    picker in the app), filtering here in Python — already fetched, already proven, harness-provable —
    is the lower-risk choice. See docs/handoffs/people.md for the fuller write-up."""
    return s.get("is_active") is not False


def _active_stores_only(stores_rows):
    return [s for s in (stores_rows or []) if _store_is_active(s)]


@router.get("/stores")
def get_stores(include_inactive: bool = False, authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    """Every store in the caller's org (+ RBAC span). Active-only BY DEFAULT — the SAME convention
    GET /employees already establishes (`include_inactive: bool = False` opt-in), not a second one.
    Admin/config surfaces that must still SEE (and re-enable) a disabled store — e.g. StoreOps Admin —
    pass `include_inactive=true` explicitly."""
    r = sb().table("stores").select("*").eq("org_id", org_id).order("address").execute()
    rows = r.data or []
    if not include_inactive:
        rows = _active_stores_only(rows)
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / enforcement off)
    if ks is not None:
        rows = [s for s in rows if in_keyset(ks, s.get("store_code"), s.get("address"))]
    return rows

@router.get("/timeclock/stores")
def timeclock_stores(org_id: str = ORG_ID):
    """FULL active store list for the kiosk clock-in picker — deliberately UNSCOPED (no RBAC span
    filter, unlike GET /stores) so a visiting/floater rep can pick the store they're physically at
    and reach the manager-override path, instead of being silently forced into their home store.
    ALWAYS active-only (no include_inactive escape hatch here — offering a closed store as a clock-in
    location is never correct) — this docstring claimed "active" long before the filter actually did
    it; now it does. Response SHAPE is unchanged (store_code/address/market only, is_active fetched
    only to filter, never returned) — a pure bugfix, not a contract change for existing callers."""
    rows = (sb().table("stores").select("store_code,address,market,is_active")
            .eq("org_id", org_id).order("address").execute().data or [])
    rows = _active_stores_only(rows)
    return [{"store_code": s.get("store_code"), "address": s.get("address"), "market": s.get("market")}
            for s in rows if s.get("store_code")]

@router.get("/employees")
def get_employees(include_inactive: bool = False, all_company: bool = False, authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    """Employees in the caller's span. all_company=true is a SCHEDULING REACH question ("whom may
    this login put on a shift"), never a reporting question — see app.core.scope's module docstring.
    Role permission `scheduling_reach` defaults to 'org', which is byte-identical to today's live,
    UNCONDITIONAL org-wide exemption; only a role explicitly configured scheduling_reach='span'
    narrows the roster back to the caller's reporting span even when all_company=true is asked for."""
    q = sb().table("employees").select("*").eq("org_id", org_id)
    if not include_inactive:
        q = q.eq("is_active", True)
    rows = q.order("name").execute().data or []
    if all_company:
        au = _caller_app_user(authorization, org_id)
        perms = _role_permissions(org_id, (au.get("role") or "").strip()) if au else {}
        if _cscope.roster_span_exempt(perms):
            return rows
        # scheduling_reach='span' — fall through and apply the reporting span below.
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

# ── Scheduling-over-approved-time-off policy (owner directive 2026-07-26, ALL tenants) ─────────
# Managers reported they could NOT reschedule an employee with approved/requested time off — the
# old create_shift hard-blocked with a 409 and no override. Default policy is now WARN (schedule
# succeeds, response carries `timeoff_warning`); a tenant may opt back into the original hard
# BLOCK via PUT /timeoff-conflict-mode. Config lives on storeops.tenants.timeoff_conflict_mode
# (migration 409) — a missing column/row/migration always degrades to 'warn', never a 500 and
# never a silent revert to blocking a tenant never asked for.
def _timeoff_conflict_mode(org_id):
    try:
        t = (sb().table("tenants").select("timeoff_conflict_mode").eq("org_id", org_id)
             .limit(1).execute().data) or []
        mode = (t[0].get("timeoff_conflict_mode") if t else None) or "warn"
    except Exception:
        mode = "warn"
    mode = str(mode).strip().lower()
    return mode if mode in ("warn", "block") else "warn"


@router.get("/timeoff-conflict-mode")
def get_timeoff_conflict_mode(org_id: str = ORG_ID):
    """Current org policy for scheduling over approved time off — 'warn' (default) or 'block'.
    Any signed-in caller may read (the schedule page uses this to decide whether to show its own
    confirm() before POSTing, vs. relying on the backend's 409 for a 'block' tenant)."""
    return {"mode": _timeoff_conflict_mode(org_id)}


@router.put("/timeoff-conflict-mode")
def set_timeoff_conflict_mode(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager/admin only. mode must be 'warn' or 'block'."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in ("warn", "block"):
        raise HTTPException(400, "mode must be 'warn' or 'block'")
    try:
        sb().table("tenants").update({"timeoff_conflict_mode": mode}).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't save the setting — is migration 409 applied?")
    return {"ok": True, "mode": mode}


def _canonical_shift_employee_id(org_id, raw_id, employees=None):
    """Resolve a raw employee_id value to the canonical BUSINESS employees.employee_id BEFORE any
    new/updated storeops row that carries an employee_id foreign-key-by-string is written — the
    2026-07-27 owner-approved money fix (see payroll_identity.py's module docstring + migration 415
    for the full root-cause writeup). Table-agnostic (despite the name, kept for history/grep
    continuity): called from every shift-writing path (POST /shifts, the bulk shift-template
    save/apply, the employee-merge reassignment) AND, since the Gate-1 REDO N1 fix, `POST /time-off`
    too (storeops/timeoff/page.tsx's admin picker had the identical numeric-id bug, poisoning
    time_off_requests instead of shifts) — so no writer, this one, a future one, or a stale saved
    template, can create a NEW numeric-id row regardless of which code path produced it.

    Reuses `business_id_alias_map`'s own ambiguity guard: a raw numeric value that collides with a
    DIFFERENT employee's own real business employee_id is left UNCHANGED rather than guessed at —
    the same rule the payroll aggregation (`payroll_identity.reconcile_employee_identity`) and the
    shift-extension gate (`_emp_id_variants`) already apply. A blank/None id, or one that doesn't
    resolve to any employee's numeric primary key (already a business id, or simply invalid), passes
    through unchanged — nothing to reconcile, and never a 500 on a bad/foreign id."""
    raw = str(raw_id).strip() if raw_id not in (None, "") else ""
    if not raw:
        return raw_id
    if employees is None:
        try:
            employees = (sb().table("employees").select("id,employee_id")
                         .eq("org_id", org_id).execute().data) or []
        except Exception:
            return raw_id
    alias = _business_id_alias_map(employees)
    return alias.get(raw, raw_id)


@router.post("/shifts")
def create_shift(shift: dict, org_id: str = ORG_ID):
    # 2026-07-27 money fix (owner-approved): canonicalize the incoming employee_id to the BUSINESS id
    # before anything below reads it (the time-off check, the hours-budget guard, and the insert
    # itself) — closes this endpoint as a source of NEW numeric-id shifts, whichever caller hits it.
    eid_raw = shift.get("employee_id")
    if eid_raw not in (None, ""):
        shift = {**shift, "employee_id":
                 _canonical_shift_employee_id(shift.get("org_id") or org_id, eid_raw)}
    # Check for an APPROVED time-off conflict. Default policy is WARN, not block (see above) — a
    # manager can still schedule over approved time off; the response carries `timeoff_warning` so
    # the caller can surface it non-blockingly. A tenant opted into 'block' keeps the original
    # hard-409 behavior.
    eid = shift.get("employee_id")
    sdate = shift.get("shift_date")
    timeoff_warning = None
    if eid and sdate:
        # Gate-1 REDO N1 fix (2026-07-27, MUST): the admin Time Off page still writes
        # time_off_requests keyed by the employee's NUMERIC id (storeops/timeoff/page.tsx — a
        # separate, not-yet-backfilled table; migration 415 now also backfills it, but an admin can
        # create a new numeric-keyed row again at any time until that page's own write is fixed).
        # Canonicalizing `eid` above (correct for the SHIFT's own identity) would otherwise silently
        # STOP matching those numeric-keyed rows — before this fix, the shift side's OWN numeric-id
        # bug accidentally kept this lookup "working" by symmetry; fixing the shift side alone would
        # have broken it. Check BOTH id forms via the same ambiguity-guarded variant lookup the
        # shift-extension gate already uses, so a block-mode tenant never silently loses enforcement.
        lookup_ids, _ = _emp_id_variants(shift.get("org_id") or org_id, eid)
        conflict = (sb().table("time_off_requests").select("id").eq("org_id", org_id)
                    .in_("employee_id", list(lookup_ids)).eq("status", "approved")
                    .lte("start_date", sdate).gte("end_date", sdate)
                    .limit(1).execute().data)
        if conflict:
            who = shift.get("employee_name") or "This employee"
            if _timeoff_conflict_mode(org_id) == "block":
                raise HTTPException(409, f"{who} has approved time off on {sdate} — cannot schedule.")
            timeoff_warning = f"{who} has approved time off on {sdate}."
    # Hours-budget guard (mig 087): block scheduling past the store's weekly budget unless a DM
    # approved an override for that store+week. Only enforced when a budget is set for the store;
    # any lookup failure degrades to "allow" so scheduling never breaks on a config/migration gap.
    _enforce_hours_budget(shift.get("org_id") or org_id, shift, exclude_id=None)
    # Stamp org_id so the row survives the org-scoped read filter on GET /shifts.
    # (shifts.org_id has NO column default → an unstamped insert lands NULL and vanishes.)
    shift = {**shift, "org_id": shift.get("org_id") or org_id}
    r = sb().table("shifts").insert(shift).execute()
    out = r.data[0] if r.data else shift
    if timeoff_warning:
        out = {**out, "timeoff_warning": timeoff_warning}
    return out

@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: int, updates: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Update a shift. org_id-scoped so a foreign (guessable BIGSERIAL) shift_id is a no-op instead
    of a cross-tenant write — this previously took NO org filter at all.

    2026-07-27 (Deliverable 4, Payroll Change Log): a "before" fetch + field diff is logged for every
    hour-relevant field a DM correction touches (scheduled_hours/actual_hours/times/store/date/status).
    Logging is best-effort (`_log_payroll_change`/`_who_for_log` never raise) — a logging failure or a
    pre-migration-414 table NEVER blocks the shift update itself."""
    updates = {k: v for k, v in updates.items() if k not in ("org_id", "id")}
    # dict(...) snapshot: a fake/mocked client's select() can return the SAME row object the update
    # below mutates in place (a real PostgREST response never aliases like this, but the diff logic
    # must be correct regardless of the client implementation underneath it) — take an independent
    # copy of "before" so the change-log diff is never accidentally comparing a row to itself.
    before = dict((sb().table("shifts").select("*").eq("id", shift_id).eq("org_id", org_id)
                   .limit(1).execute().data or [{}])[0])
    r = sb().table("shifts").update(updates).eq("id", shift_id).eq("org_id", org_id).execute()
    if not r.data:
        raise HTTPException(404, "shift not found")
    after = r.data[0]
    try:
        _log_shift_edit(org_id, before, after, _who_for_log(authorization, org_id))
    except Exception:
        pass
    return after

@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """org_id-scoped so a foreign shift_id is a no-op instead of a cross-tenant delete.

    Gate-1 N3 (2026-07-27): deleting a shift IS a manual hours fix (the sharpest gap the reviewer
    found — a DM removing a scheduled shift entirely is at least as consequential as editing its
    hours) — logs the deletion's full before-state (best-effort, never blocks the delete itself).

    2026-07-30 failure-log fix (fix_request 9b342c54, real-world repro: shift_id 4535): soft-deletes
    via an app-level UPDATE instead of a real DELETE. storeops.shifts carries a BEFORE DELETE
    trigger (storeops.soft_delete_shift(), migration 003) whose body runs
    `UPDATE storeops.shifts SET is_deleted = true, deleted_at = NOW() WHERE id = OLD.id` against the
    SAME row the outer DELETE is currently processing — a documented Postgres anti-pattern that
    raises "tuple to be updated was already modified by an operation triggered by the current
    command" on EVERY invocation (not intermittent), which postgrest-py surfaces as an unhandled
    APIError -> unhandled 500. Every reader of storeops.shifts in this file already filters
    `.eq("is_deleted", False)`, so flipping that flag here (exactly what the trigger was trying,
    and failing, to do) produces the identical observable result the feature always intended,
    without ever exercising the broken trigger. `.update()` is a plain UPDATE — it does not fire a
    DELETE trigger at all. The whole write is wrapped in try/except so any OTHER DB error becomes a
    clean 400 instead of an unhandled 500 (org-scoping unchanged: still `.eq(id).eq(org_id)`, a
    foreign/nonexistent shift_id still zero-matches and no-ops with a 200, same as before)."""
    before = dict((sb().table("shifts").select("*").eq("id", shift_id).eq("org_id", org_id)
                   .limit(1).execute().data or [{}])[0])
    who = _who_for_log(authorization, org_id)
    try:
        sb().table("shifts").update({
            "is_deleted": True,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": (who or {}).get("email"),
        }).eq("id", shift_id).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't delete this shift — please retry, or contact support if it keeps happening.")
    if before:
        try:
            _log_payroll_change(org_id, field="shift_deleted", entry_point="shift_edit",
                                 employee_id=before.get("employee_id"), employee_name=before.get("employee_name"),
                                 store_code=before.get("store_code"), work_date=before.get("shift_date"),
                                 before=f"{before.get('scheduled_hours')}h scheduled ({before.get('start_time')}-{before.get('end_time')})",
                                 after=None, source_table="shifts", source_id=shift_id,
                                 who=who)
        except Exception:
            pass
    return {"deleted": shift_id}

@router.get("/time-off")
def get_time_off(employee_id: str = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    q = sb().table("time_off_requests").select("*").eq("org_id", org_id)
    if employee_id: q = q.eq("employee_id", employee_id)
    rows = q.order("start_date", desc=True).execute().data or []
    since, until = _emp_ids_window_from_rows(rows, "start_date", "end_date")
    eids = scope_emp_ids(authorization, org_id, since=since, until=until)   # None = unrestricted
    if eids is not None:
        rows = [r for r in rows if str(r.get("employee_id")) in eids]
    return rows

@router.post("/time-off")
def create_time_off(request: dict, org_id: str = ORG_ID):
    if not (request.get("employee_id") and request.get("start_date") and request.get("end_date")):
        raise HTTPException(400, "employee_id, start_date and end_date are required")
    # Gate-1 REDO N1 fix (2026-07-27, defense in depth): canonicalize to the BUSINESS employee_id
    # server-side too, so no caller (the fixed timeoff page, a future one, or a bad client) can
    # create a NEW numeric-id time_off_requests row — same posture as _canonical_shift_employee_id
    # on the shift-writing paths. Reuses the identical helper/ambiguity guard (the function itself is
    # table-agnostic — it just resolves a raw id to the canonical business id for an org's roster).
    request = {**request, "employee_id":
               _canonical_shift_employee_id(request.get("org_id") or org_id, request.get("employee_id"))}
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

# ── P0 payroll performance (mig 407, 2026-07-22): Postgres-side month aggregation ───────────────
# /payroll + /payroll-by-store used to pull EVERY month shift row (select *) plus up to 20,000
# timelog rows over PostgREST into Python and aggregate there — seconds of transfer for kiosk-heavy
# tenants (luxelink: kiosk punches with no formal schedule ride the timelog-fallback path).
# storeops.payroll_month_rows() (migration 407) computes per-(employee_id, store_code) aggregates
# in Postgres with the exact row-level semantics of the legacy loops (actual==0 → scheduled
# fallback, closed-punch-only timelog, the no-double-count shift-day rule), so the handlers merge
# a handful of group rows instead. If the RPC is missing (mig 407 not yet run) or fails in ANY
# way, both handlers fall back to the legacy Python aggregation unchanged — an un-run migration
# never breaks payroll (AGENT_CONTRACT §5). Equivalence proof (money-adjacent):
# backend/harness_payroll_rpc_equivalence.py asserts BYTE-IDENTICAL output between the two paths.
# ── Arbitrary pay-period ranges (2026-07-25, owner: "need time range to create payroll for the
# employees universally") ───────────────────────────────────────────────────────────────────────
# /payroll + /payroll-by-store originally only understood `month` ('YYYY-MM'). Real payroll runs on
# biweekly/semimonthly/custom periods that don't line up with calendar months. storeops.payroll_month_rows
# (mig 407) already takes arbitrary (p_lo, p_hi) dates with no month assumption baked into its SQL — it
# was named for its ORIGINAL caller, not its actual bound semantics — so an explicit start/end range reuses
# the SAME RPC/legacy paths with NO new migration. `month` stays supported byte-identically (still the
# only param the harness's month-mode assertions ever pass); start/end are additive and take precedence
# when both are given, so a caller can never end up with an ambiguous half-range.
# ── Salary pay-basis (owner directive 2026-07-27, migrations 416/417) ───────────────────────────────
# See payroll_salary.py's module docstring for the full design. These two helpers are the ONLY I/O
# this feature needs beyond widening an existing `employees` SELECT: reading the tenant's own
# pay-period config row (mirrors `_work_week_bounds`'s existing direct-table-read pattern, just below)
# and widening a base employees SELECT to also carry pay_basis/pay_amount/hire_date/termination_date,
# degrading to the caller's original field list on a pre-migration database so every salary code path
# is a silent no-op until both 416 and 417 have run (see payroll_salary.py PAY_FIELDS).
def _tenant_pp_settings(org_id):
    try:
        rows = (sb().table("tenants").select(
            "work_week_start_dow,pay_period_type,payday_dow,payday_weeks_after,biweekly_anchor,timezone"
        ).eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        rows = []
    return payroll_salary.tenant_pay_period_settings(rows[0] if rows else {})


def _employees_with_pay_fields(org_id, base_fields, eq: dict = None):
    """`eq` applies additional `.eq(k, v)` filters SERVER-SIDE (Gate-1 N3 — house perf doctrine:
    filter in the query, not fetch-all-then-filter in Python) — e.g. `{"employee_id": employee_id}`
    for a single-employee lookup, same as any other org-scoped query in this file."""
    def _q(fields):
        q = sb().table("employees").select(fields).eq("org_id", org_id)
        for k, v in (eq or {}).items():
            q = q.eq(k, v)
        return q
    try:
        return _q(f"{base_fields},{payroll_salary.PAY_FIELDS}").execute().data or []
    except Exception:
        return _q(base_fields).execute().data or []


def _warn_salary_override_failed(response, org_id, endpoint, exc):
    """Gate-1 N5 fix (2026-07-27): the salary-override call sites (GET /payroll, /payroll-by-store,
    hr's /compensation) wrap the whole override in a try/except so a bug there can NEVER break the
    base hourly report — but a bare `except Exception: pass` makes a real failure silently revert a
    salaried employee's row to $0-hourly with no signal anywhere, which is the exact failure mode the
    house doctrine calls out as unacceptable. This (a) always prints a WARN (visible in Railway logs,
    matching `_log_payroll_change`'s own WARN-on-failure convention) and (b) sets a response header
    (`X-Salary-Override-Warning`) — never a response BODY field, since GET /payroll returns a bare
    JSON array and changing that shape would break every existing consumer; a header is the one
    channel that's genuinely additive regardless of whether the endpoint returns a list or a dict."""
    print(f"WARN salary pay-basis override failed for org {org_id} on {endpoint}: {exc}")
    if response is not None:
        try:
            response.headers["X-Salary-Override-Warning"] = f"salary override failed on {endpoint}: {str(exc)[:180]}"
        except Exception:
            pass


def _resolve_range(month, start, end):
    """(lo, hi) exclusive-upper-bound date strings for payroll aggregation. Precedence: explicit
    start/end (INCLUSIVE on both ends, per RULE ONE-style caller ergonomics — a "Jan 1 to Jan 31" range
    should include the 31st) > legacy `month` ('YYYY-MM', exclusive-next-month-1st, unchanged) > open
    range (None, None — the original no-filter behavior). Raises HTTPException(400) on a malformed date
    or start > end so a bad picker/URL never silently returns the wrong period's money."""
    if start or end:
        if not (start and end):
            raise HTTPException(400, "start and end must both be provided together")
        try:
            d_start = _date.fromisoformat(str(start)[:10])
            d_end = _date.fromisoformat(str(end)[:10])
        except ValueError:
            raise HTTPException(400, "start/end must be ISO dates (YYYY-MM-DD)")
        if d_start > d_end:
            raise HTTPException(400, "start must be on or before end")
        return d_start.isoformat(), (d_end + timedelta(days=1)).isoformat()
    if month:
        parts = str(month).split("-")
        y, m = int(parts[0]), int(parts[1])
        return f"{month}-01", (f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01")
    return None, None


def _payroll_month_groups(org_id, lo, hi):
    """(shift_groups, timelog_groups) from storeops.payroll_month_rows (mig 407), each sorted by
    first_ord (min shifts.id / epoch of min timelog.created_at) so the callers rebuild the SAME
    first-seen insertion order the legacy row loops had — first-row-wins name resolution,
    dominant-store tie-breaks, and stable same-name sort order all reproduce exactly.
    Returns None when the range is open (no month given) or the RPC isn't available — callers
    must then use the legacy full-row Python path."""
    if not (lo and hi):
        return None
    try:
        data = (sb().rpc("payroll_month_rows", {"p_org_id": org_id, "p_lo": lo, "p_hi": hi})
                .execute().data) or []

        def _ord(g):
            v = g.get("first_ord")
            return (v is None, v if v is not None else 0.0)
        shift_groups = sorted((g for g in data if g.get("kind") == "shift"), key=_ord)
        tl_groups = sorted((g for g in data if g.get("kind") == "timelog"), key=_ord)
        return shift_groups, tl_groups
    except Exception:
        return None   # RPC missing/unreachable -> degrade gracefully to the legacy Python path


# ── Inactive-employee phantom-schedule fix (2026-07-25, owner: "the employees assigned to [inactive
# stores] are inactive but they still appear in the payroll and the storeops report") ──────────────
# Money-adjacent rule (owner-confirmed): an employee row must appear iff they have REAL activity in
# the range (a closed timelog punch, or a shift with GENUINELY recorded actual_hours) — a
# terminated-mid-period employee with real worked hours must still appear AND be paid their real rate
# (never blanket-filtered by is_active). What must drop is a schedule-only PHANTOM shift for an
# INACTIVE employee (actual_hours==0, so the normal actual==0->scheduled fallback would otherwise
# fabricate paid hours for a shift that was never actually worked, left over from before they were
# deactivated). Applied IDENTICALLY to both /payroll and /payroll-by-store, and to BOTH the mig-407
# RPC fast path and the legacy Python path, via ONE shared code path below (not reimplemented per
# path) — an inactive employee's contribution is ALWAYS computed here, never through the RPC groups
# (which can't be phantom-filtered after their SQL-side aggregation) or the legacy per-row loops
# (which are told to skip inactive employees entirely and let this path handle them).
def _inactive_ids_from(employees_rows):
    """employee_id set of employees explicitly marked inactive. `is_active` is a NULLABLE column —
    Gate-1 LOW-B2 (2026-07-26): NULL/missing must be treated as ACTIVE (matching every frontend
    picker's `s.is_active !== false` convention, and the column's own `DEFAULT true`), never folded
    into the inactive/phantom-filtering path on an absent flag. Only an EXPLICIT `is_active=false`
    counts."""
    return {e.get("employee_id") for e in employees_rows
            if e.get("employee_id") and e.get("is_active") is False}


def _inactive_activity_rows(org_id, lo, hi, inactive_ids):
    """(real_shifts, timelog_rows) for INACTIVE employees only. real_shifts = storeops.shifts rows
    (is_deleted=false, in [lo,hi) when given) with actual_hours GENUINELY > 0 — a schedule-only row
    (actual_hours 0/blank) is dropped right here, at the source, for every caller. timelog_rows =
    CLOSED punches only (never phantom by definition, always real activity) MINUS any punch whose
    (employee_id, work_date) is already covered by a SURVIVING real shift (Gate-1 MAJOR-B1,
    2026-07-26: without this, a real shift AND its own same-day punch both counted — the active
    path's "already represented by a shift that day -> never double-count" invariant had been
    dropped for inactive employees, e.g. 6h shift + 6h punch on the same day summed to a fabricated
    12h/$240 instead of 6h/$120). A PHANTOM shift (actual_hours==0, already excluded from
    real_shifts above) must NOT block its day — that's the point of this whole feature: the
    phantom-dropped + punch-counted cell is the CORRECT behavior and must keep working exactly as
    before this fix."""
    if not inactive_ids:
        return [], []
    try:
        q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
        if lo and hi:
            q = q.gte("shift_date", lo).lt("shift_date", hi)
        shifts = q.in_("employee_id", list(inactive_ids)).execute().data or []
    except Exception:
        shifts = []
    real_shifts = [s for s in shifts if float(s.get("actual_hours") or 0) > 0]
    # (employee_id, work_date) pairs already covered by a REAL (surviving) shift — mirrors the
    # active-path `shift_days` invariant, but scoped to real_shifts only so a phantom shift's day
    # stays open for its punch to count (see harness checks for both cases explicitly).
    real_shift_days = {(s.get("employee_id"), str(s.get("shift_date") or "")[:10]) for s in real_shifts}
    try:
        q2 = (sb().table("timelog").select("employee_id,employee_name,hours,clock_out,work_date,store_code")
              .eq("org_id", org_id))
        if lo and hi:
            q2 = q2.gte("work_date", lo).lt("work_date", hi)
        tl = q2.in_("employee_id", list(inactive_ids)).limit(20000).execute().data or []
    except Exception:
        tl = []
    timelog_rows = [t for t in tl if t.get("clock_out") and t.get("hours") is not None
                     and (t.get("employee_id"), str(t.get("work_date") or "")[:10]) not in real_shift_days]
    return real_shifts, timelog_rows


def _merge_inactive_into_payroll(summary, store_hours, emp_map, real_shifts, timelog_rows):
    """Fold an INACTIVE employee's real-activity-only rows into /payroll's summary + store_hours —
    real_shifts already guarantee actual_hours>0, so the act==0->scheduled fallback never applies."""
    for s in real_shifts:
        eid = s.get("employee_id")
        emp = emp_map.get(eid, {})
        if eid not in summary:
            summary[eid] = {"employee_id": eid, "name": s.get("employee_name") or emp.get("name", ""),
                             "store": "", "pay_rate": float(emp.get("pay_rate") or 0),
                             "scheduled_hours": 0, "actual_hours": 0, "shifts": 0}
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        summary[eid]["scheduled_hours"] += sched
        summary[eid]["actual_hours"] += act
        summary[eid]["shifts"] += 1
        st = (s.get("store_code") or "").strip()
        if st:
            sh = store_hours.setdefault(eid, {})
            sh[st] = sh.get(st, 0.0) + sched + act
    for t in timelog_rows:
        eid = t.get("employee_id")
        if not eid:
            continue
        emp = emp_map.get(eid, {})
        if eid not in summary:
            summary[eid] = {"employee_id": eid, "name": t.get("employee_name") or emp.get("name", ""),
                             "store": "", "pay_rate": float(emp.get("pay_rate") or 0),
                             "scheduled_hours": 0, "actual_hours": 0, "shifts": 0}
        hrs = float(t.get("hours") or 0)
        summary[eid]["actual_hours"] += hrs
        st = (t.get("store_code") or "").strip()
        if st:
            sh = store_hours.setdefault(eid, {})
            sh[st] = sh.get(st, 0.0) + hrs


def _merge_inactive_into_by_store(by_store, rate_map, real_shifts, timelog_rows):
    """Fold an INACTIVE employee's real-activity-only rows into /payroll-by-store's by_store map."""
    for s in real_shifts:
        store = (s.get("store_code") or "").strip()
        if not store:
            continue
        eid = s.get("employee_id")
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        hrs = act if act > 0 else sched   # act is always >0 here (real_shifts is pre-filtered)
        rate = rate_map.get(eid, 0.0)
        d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
        d["hours"] += hrs
        d["amount"] += hrs * rate
    for t in timelog_rows:
        eid = t.get("employee_id")
        store = (t.get("store_code") or "").strip()
        if not eid or not store:
            continue
        hrs = float(t.get("hours") or 0)
        rate = rate_map.get(eid, 0.0)
        d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
        d["hours"] += hrs
        d["amount"] += hrs * rate


# ════════════════════════════════════════════════════════════════════════════════════════════════
# PAYROLL CHANGE LOG (owner directive 2026-07-27, Deliverable 4): "track and highlight any changes
# done by the DM to fix the hours manually" — an append-only audit trail (migration 414,
# storeops.payroll_change_log) covering every write path that alters punches/hours: shift edits
# (PATCH /shifts/{id}), manager clock-in overrides (POST /timeclock/override), manual hours
# adjustments (POST/DELETE /manual-hours), and the force-clockout sweep (both the manager "run now"
# button and the pg_cron auto-sweep). Best-effort throughout: a missing table (pre-migration-414)
# never blocks the underlying write — the log call always happens AFTER the real write succeeds and
# is wrapped in try/except.
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _who_for_log(authorization, org_id=ORG_ID):
    """Best-effort {email, role, employee_id} for audit attribution — NEVER raises (a shift edit or
    manual-hours entry must still succeed even if identity resolution fails), unlike
    `_require_manager` which is a hard gate. Returns {} when the caller can't be resolved."""
    try:
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if not uid:
            return {}
        from app.core.tenant_middleware import caller_app_user
        return caller_app_user(uid, "org_id,email,role,employee_id") or {}
    except Exception:
        return {}


def _log_payroll_change(org_id, *, field, entry_point, employee_id=None, employee_name=None,
                         store_code=None, work_date=None, before=None, after=None,
                         source_table=None, source_id=None, who=None, reason=None):
    """Append ONE row to storeops.payroll_change_log. `who` = a dict from `_who_for_log` (or
    `_require_manager`'s return, which carries the same email/role keys) — pass {} or None for a
    system-triggered change (e.g. the pg_cron force-clockout sweep). Never raises: a missing
    migration/table degrades to "no log row written", never a 500 on the real payroll write."""
    try:
        row = {
            "org_id": org_id, "employee_id": employee_id, "employee_name": employee_name,
            "store_code": store_code, "work_date": (str(work_date)[:10] if work_date else None),
            "field": field, "before_value": (None if before is None else str(before)),
            "after_value": (None if after is None else str(after)),
            "entry_point": entry_point, "source_table": source_table, "source_id": (str(source_id) if source_id else None),
            "changed_by_email": (who or {}).get("email") or "system",
            "changed_by_role": (who or {}).get("role") or ("system" if not who else None),
            "reason": reason,
        }
        sb().table("payroll_change_log").insert(row).execute()
    except Exception as e:
        print(f"WARN payroll_change_log insert failed (is migration 414 applied?): {e}")


_SHIFT_LOGGED_FIELDS = ("scheduled_hours", "actual_hours", "start_time", "end_time",
                        "store_code", "shift_date", "status", "employee_id")


def _log_shift_edit(org_id, before, after, who):
    """Diff a shift PATCH's hour-relevant fields and log ONE row per changed field. `before`/`after`
    are the full shift rows (pre- and post-update)."""
    if not before or not after:
        return
    for f in _SHIFT_LOGGED_FIELDS:
        bv, av = before.get(f), after.get(f)
        if str(bv or "") == str(av or ""):
            continue
        _log_payroll_change(
            org_id, field=f, entry_point="shift_edit",
            employee_id=after.get("employee_id") or before.get("employee_id"),
            employee_name=after.get("employee_name") or before.get("employee_name"),
            store_code=after.get("store_code") or before.get("store_code"),
            work_date=after.get("shift_date") or before.get("shift_date"),
            before=bv, after=av, source_table="shifts", source_id=after.get("id") or before.get("id"),
            who=who)


@router.get("/payroll-change-log")
def payroll_change_log(start: str = "", end: str = "", employee_id: str = "", store_code: str = "",
                        entry_point: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The Payroll Change Log page's data source — every manual hours/punch correction in range,
    newest first. RULE FIVE core filters (period/store/rep) all supported; degrades to an empty list
    pre-migration-414 (never a 500)."""
    try:
        q = sb().table("payroll_change_log").select("*").eq("org_id", org_id)
        if start:
            q = q.gte("work_date", start)
        if end:
            q = q.lte("work_date", end)
        if employee_id:
            q = q.eq("employee_id", employee_id)
        if store_code:
            q = q.eq("store_code", store_code)
        if entry_point:
            q = q.eq("entry_point", entry_point)
        rows = q.order("created_at", desc=True).limit(5000).execute().data or []
    except Exception:
        return {"items": [], "available": False}
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]
    return {"items": rows, "available": True}


@router.get("/payroll")
def get_payroll(month: str = None, start: str = None, end: str = None,
                 authorization: str = Header(default=""), org_id: str = ORG_ID, response: Response = None):
    """Returns scheduled vs actual hours per employee for payroll.

    Accepts EITHER the legacy `month` ('YYYY-MM', unchanged, still byte-identical) OR an explicit
    `start`/`end` ISO date range (both inclusive) for an arbitrary pay period — biweekly, semimonthly,
    custom — for ANY tenant (org_id stays the query-param scope, RULE ONE). start/end win if both given."""
    lo, hi = _resolve_range(month, start, end)
    # ALL employees (active OR not), not active-only: a terminated employee with REAL worked hours
    # must still be paid their real rate (2026-07-25 fix) — matches /payroll-by-store's existing
    # all-employees rate_map. Row EXISTENCE for an inactive employee is still gated on real activity
    # below (_inactive_activity_rows), so this alone does not resurrect a schedule-only phantom.
    employees = _employees_with_pay_fields(org_id, "id,name,employee_id,pay_rate,home_store,is_active")

    emp_map = {e["employee_id"]: e for e in employees}
    inactive_ids = _inactive_ids_from(employees)
    summary = {}
    # employee_id -> {store_code: hours-weight}. RULE FIVE (§3d) store filter: a floater's row must
    # attribute to the store they actually WORKED THE MOST this month, not just whichever shift the
    # DB happened to return first (the old behavior) or their static home_store.
    store_hours: dict = {}

    groups = _payroll_month_groups(org_id, lo, hi)
    if groups is not None:
        # FAST PATH (mig 407): merge Postgres-side per-(employee, store) aggregates. actual_eff_sum
        # already carries the per-ROW actual==0->scheduled fallback; timelog groups already exclude
        # open punches and shift-covered days (no-double-count). Group order = first-row order.
        shift_groups, tl_groups = groups
        # Inactive employees' RPC groups can't be phantom-filtered post-aggregation (no per-row
        # granularity survives the SQL GROUP BY) — excluded here and recomputed below via
        # _inactive_activity_rows, the SAME shared path the legacy branch also uses.
        if inactive_ids:
            shift_groups = [g for g in shift_groups if g.get("employee_id") not in inactive_ids]
            tl_groups = [g for g in tl_groups if g.get("employee_id") not in inactive_ids]
        for g in shift_groups:
            eid = g.get("employee_id")
            emp = emp_map.get(eid, {})
            if eid not in summary:
                summary[eid] = {
                    "employee_id": eid,
                    "name": g.get("employee_name") or emp.get("name", ""),
                    "store": "",  # filled below from store_hours (dominant store), home_store fallback
                    "pay_rate": float(emp.get("pay_rate") or 0),
                    "scheduled_hours": 0,
                    "actual_hours": 0,
                    "shifts": 0,
                }
            sched = float(g.get("scheduled_sum") or 0)
            act = float(g.get("actual_eff_sum") or 0)
            summary[eid]["scheduled_hours"] += sched
            summary[eid]["actual_hours"]    += act
            summary[eid]["shifts"] += int(g.get("shift_count") or 0)
            st = (g.get("store_code") or "").strip()
            if st:
                sh = store_hours.setdefault(eid, {})
                sh[st] = sh.get(st, 0.0) + sched + act
        for g in tl_groups:
            eid = g.get("employee_id")
            if not eid:
                continue
            emp = emp_map.get(eid, {})
            if eid not in summary:
                summary[eid] = {
                    "employee_id": eid,
                    "name": g.get("employee_name") or emp.get("name", ""),
                    "store": "",
                    "pay_rate": float(emp.get("pay_rate") or 0),
                    "scheduled_hours": 0,
                    "actual_hours": 0,
                    "shifts": 0,
                }
            hrs = float(g.get("timelog_hours_sum") or 0)
            summary[eid]["actual_hours"] += hrs
            st = (g.get("store_code") or "").strip()
            if st:
                sh = store_hours.setdefault(eid, {})
                sh[st] = sh.get(st, 0.0) + hrs
    else:
        # LEGACY PATH (pre-mig-407 fallback): full row fetch + Python aggregation — UNCHANGED for
        # active/unknown employees. Inactive employees are excluded from this loop (below) and
        # recomputed via the SAME shared _inactive_activity_rows path the fast branch uses.
        q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
        if lo and hi:
            q = q.gte("shift_date", lo).lt("shift_date", hi)
        shifts = q.execute().data or []
        # employee_id -> {shift_date already represented by a shift row}, so the timelog fallback
        # below never double-counts a day that's already schedule-tracked.
        shift_days: dict = {}
        for s in shifts:
            eid = s.get("employee_id")
            if eid in inactive_ids:
                continue   # handled by _inactive_activity_rows below (phantom-schedule-only excluded there)
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
                if eid in inactive_ids:
                    continue   # handled by _inactive_activity_rows below (always real activity, no fallback needed)
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

    # Inactive employees: ALWAYS computed via this ONE shared, phantom-aware path (never via the fast
    # RPC groups or the legacy per-row loops above, which both explicitly skip them) — money-adjacent
    # rule: real activity (a genuinely-clocked/worked hour) still appears and is paid at their real
    # rate; a leftover schedule-only shift for someone since deactivated does not.
    real_shifts, tl_rows = _inactive_activity_rows(org_id, lo, hi, inactive_ids)
    _merge_inactive_into_payroll(summary, store_hours, emp_map, real_shifts, tl_rows)

    # LUNCH-BREAK AUTO-DEDUCTION (owner directive 2026-07-27, Deliverable 3) — subtracts from ACTUAL
    # hours only (scheduled_hours/scheduled_pay untouched), keyed off storeops.timelog.employee_id
    # (ALWAYS the business id, per payroll_identity.py's root-cause writeup) so it always lands on the
    # correct `summary` bucket here, BEFORE the numeric-id/business-id merge below sums it forward.
    # `actual_pay` below is computed FROM the netted actual_hours, so hourly pay = (hours - deduction) x
    # rate automatically. EQUIVALENCE (byte-identical to base until migration 418 runs): `summary` rows
    # gain a `lunch_deduction_hours` key ONLY inside the `available` branch below — never added at all
    # (not even as a 0.0) when the feature is unavailable, so an existing exact-JSON-shape assertion
    # elsewhere in the test suite (and any caller diffing the raw response) sees a byte-identical
    # payload pre-migration. Never lets a failure here break payroll itself.
    try:
        _lunch = _lunch_period_deduction(org_id, lo, hi, sb())
        if _lunch.get("available"):
            for r in summary.values():
                r.setdefault("lunch_deduction_hours", 0.0)
                # HONESTY (no-silent-caps doctrine): a pathological org+range with more than
                # lunch_deduction.LUNCH_TIMELOG_FETCH_LIMIT closed punches would otherwise silently
                # under-deduct (fail-safe direction: pays MORE, never less) — flag it explicitly
                # instead, same additive-only-when-relevant convention as lunch_deduction_hours itself.
                if _lunch.get("limit_hit"):
                    r["lunch_deduction_data_capped"] = True
            for _eid, _ded in _lunch["by_employee"].items():
                if _eid in summary:
                    _applied = min(_ded, summary[_eid]["actual_hours"])   # negative-hours guard
                    summary[_eid]["actual_hours"] = round(summary[_eid]["actual_hours"] - _applied, 2)
                    summary[_eid]["lunch_deduction_hours"] = round(summary[_eid]["lunch_deduction_hours"] + _applied, 2)
    except Exception:
        pass

    rows = list(summary.values())
    for r in rows:
        eid = r["employee_id"]
        sh = store_hours.get(eid)
        r["store"] = (max(sh.items(), key=lambda kv: kv[1])[0] if sh
                      else (emp_map.get(eid, {}).get("home_store") or ""))
        r["scheduled_pay"] = round(r["scheduled_hours"] * r["pay_rate"], 2)
        r["actual_pay"]    = round(r["actual_hours"] * r["pay_rate"], 2)
    # ONE ROW PER REP (owner directive 2026-07-27) — collapse the numeric-id-vs-business-id duplicate
    # rows a Schedule-created shift + a kiosk punch otherwise produce for the SAME employee. Pure
    # regrouping of already-computed numbers, never a hours×rate recompute — see
    # payroll_identity.py's module docstring for the full root-cause + presentation-only proof.
    rows = _reconcile_employee_identity(rows, employees)
    # SALARY PAY-BASIS OVERRIDE (owner directive 2026-07-27, Deliverable 3) — see payroll_salary.py's
    # module docstring. Runs AFTER the RPC-fast-path/legacy-path convergence above, so it is the ONE
    # shared point that applies regardless of which hours-aggregation branch ran. try/except: a bad
    # tenant-settings row or unexpected data must never break the base hourly report — but Gate-1 N5:
    # never SILENTLY (see _warn_salary_override_failed).
    #
    # Gate-1 F1 fix (MAJOR, 2026-07-27): apply_to_payroll_rows only overrides an EXISTING row — a
    # salaried employee with ZERO activity this period (no shift, no punch) never gets a row from the
    # activity-driven aggregation above, so they were silently MISSING from this report while still
    # correctly appearing in GET /payroll-by-store and GET /compensation (which iterate the full
    # roster, not activity). synthesize_zero_activity_rows appends a 0-hours row for exactly that case
    # — see its own docstring in payroll_salary.py.
    if lo and hi:
        try:
            pp_settings = _tenant_pp_settings(org_id)
            lo_d, hi_d = _date.fromisoformat(lo), _date.fromisoformat(hi) - timedelta(days=1)
            rows = payroll_salary.apply_to_payroll_rows(rows, employees, pp_settings, lo_d, hi_d)
            rows = payroll_salary.synthesize_zero_activity_rows(rows, employees, pp_settings, lo_d, hi_d)
        except Exception as e:
            _warn_salary_override_failed(response, org_id, "GET /payroll", e)
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store"))]
    return sorted(rows, key=lambda x: x["name"])


@router.get("/payroll-by-store")
def get_payroll_by_store(month: str = None, start: str = None, end: str = None,
                          authorization: str = Header(default=""), org_id: str = ORG_ID, response: Response = None):
    """Per-STORE payroll for a month OR an arbitrary start/end range (same precedence as /payroll —
    see _resolve_range), for the Store Expenses 'Employee Salaries' auto-fill.

    For each shift in range, hours = actual_hours where clocked else scheduled_hours (SAME basis
    as /payroll, so the numbers reconcile), pay = hours * the employee's pay_rate, attributed to the
    shift's own store_code (a floater's hours land at the store they worked). Returns one row per store:
    {store_code, hours, amount}."""
    lo, hi = _resolve_range(month, start, end)
    # All employees (active OR not) — a terminated rep who worked this month still earns; rate=0 if unknown.
    employees = _employees_with_pay_fields(org_id, "employee_id,pay_rate,is_active,home_store")
    rate_map = {e.get("employee_id"): float(e.get("pay_rate") or 0) for e in employees}
    inactive_ids = _inactive_ids_from(employees)
    # Salary pay-basis (Deliverable 4) — per-(employee, store) hours/$ bookkeeping, gathered ONLY for
    # employees who are actually salaried (empty set -> zero extra cost for an all-hourly tenant). See
    # payroll_salary.apply_to_by_store for how these are consumed.
    salaried_ids = {e.get("employee_id") for e in employees
                    if e.get("employee_id") and "pay_basis" in e
                    and payroll_salary.resolve_pay_basis(e)[0] != "hourly"}
    emp_store_hours: dict = {}
    emp_store_dollars: dict = {}

    by_store = {}
    groups = _payroll_month_groups(org_id, lo, hi)
    if groups is not None:
        # FAST PATH (mig 407): hours_eff_sum already carries the per-ROW hrs = actual if >0 else
        # scheduled basis; timelog groups already exclude open punches + shift-covered days.
        # Inactive employees' groups are excluded here (2026-07-25) — recomputed below via
        # _inactive_activity_rows, the SAME shared path the legacy branch also uses.
        shift_groups, tl_groups = groups
        if inactive_ids:
            shift_groups = [g for g in shift_groups if g.get("employee_id") not in inactive_ids]
            tl_groups = [g for g in tl_groups if g.get("employee_id") not in inactive_ids]
        for g in shift_groups:
            store = (g.get("store_code") or "").strip()
            if not store:
                continue
            eid = g.get("employee_id")
            hrs = float(g.get("hours_eff_sum") or 0)
            rate = rate_map.get(eid, 0.0)
            d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
            d["hours"] += hrs
            d["amount"] += hrs * rate
            if eid in salaried_ids:
                payroll_salary.accumulate(emp_store_hours, eid, store, hrs)
                payroll_salary.accumulate(emp_store_dollars, eid, store, hrs * rate)
        for g in tl_groups:
            eid = g.get("employee_id")
            store = (g.get("store_code") or "").strip()
            if not eid or not store:
                continue
            hrs = float(g.get("timelog_hours_sum") or 0)
            rate = rate_map.get(eid, 0.0)
            d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
            d["hours"] += hrs
            d["amount"] += hrs * rate
            if eid in salaried_ids:
                payroll_salary.accumulate(emp_store_hours, eid, store, hrs)
                payroll_salary.accumulate(emp_store_dollars, eid, store, hrs * rate)
    else:
        # LEGACY PATH (pre-mig-407 fallback): full row fetch + Python aggregation — UNCHANGED.
        q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
        if lo and hi:
            q = q.gte("shift_date", lo).lt("shift_date", hi)
        shifts = q.execute().data or []
        shift_days: dict = {}   # employee_id -> {shift_date} already represented by a shift row
        for s in shifts:
            eid = s.get("employee_id")
            if eid in inactive_ids:
                continue   # handled by _inactive_activity_rows below (phantom-schedule-only excluded there)
            store = (s.get("store_code") or "").strip()
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
            if eid in salaried_ids:
                payroll_salary.accumulate(emp_store_hours, eid, store, hrs)
                payroll_salary.accumulate(emp_store_dollars, eid, store, hrs * rate)

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
                if eid in inactive_ids:
                    continue   # handled by _inactive_activity_rows below (always real activity, no fallback needed)
                wd = str(t.get("work_date") or "")[:10]
                store = (t.get("store_code") or "").strip()
                if not eid or not wd or not store or wd in shift_days.get(eid, set()):
                    continue
                hrs = float(t.get("hours") or 0)
                rate = rate_map.get(eid, 0.0)
                d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
                d["hours"] += hrs
                d["amount"] += hrs * rate
                if eid in salaried_ids:
                    payroll_salary.accumulate(emp_store_hours, eid, store, hrs)
                    payroll_salary.accumulate(emp_store_dollars, eid, store, hrs * rate)

    # Inactive employees: ALWAYS computed via this ONE shared, phantom-aware path (2026-07-25) — same
    # function /payroll uses, so both endpoints agree byte-for-byte on which of an inactive employee's
    # hours are "real" vs a leftover schedule-only phantom.
    real_shifts, tl_rows = _inactive_activity_rows(org_id, lo, hi, inactive_ids)
    _merge_inactive_into_by_store(by_store, rate_map, real_shifts, tl_rows)

    # Gate-1 D1 fix (2026-07-28, MODERATE money): _merge_inactive_into_by_store above writes an
    # INACTIVE employee's real-activity hourly $ straight into by_store, but never into
    # emp_store_hours/emp_store_dollars (those two dicts are only fed by the active-path loops
    # earlier, which explicitly SKIP inactive employee ids). For an inactive employee who is ALSO
    # salaried, that left apply_to_by_store below with NOTHING to subtract for them — so it ADDED
    # their full derived salary ON TOP of the hourly figure just written above, overstating the store
    # total by hours × their (possibly stale) pay_rate (repro: $52k salaried, is_active=false, one 8h
    # punch at a leftover $20/hr rate -> by-store showed $1,160 against /payroll's correct $1,000, a
    # +$160 = 8h×$20 overstatement). Mirrors the SAME hrs computation _merge_inactive_into_by_store
    # itself uses (real_shifts is pre-filtered to actual_hours>0; tl_rows is always real, never
    # phantom) so the two stay in exact agreement — this is bookkeeping only, not a second source of
    # truth for the hours themselves.
    for s in real_shifts:
        eid = s.get("employee_id")
        if eid not in salaried_ids:
            continue
        store = (s.get("store_code") or "").strip()
        if not store:
            continue
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        hrs = act if act > 0 else sched   # act is always >0 here (real_shifts is pre-filtered)
        rate = rate_map.get(eid, 0.0)
        payroll_salary.accumulate(emp_store_hours, eid, store, hrs)
        payroll_salary.accumulate(emp_store_dollars, eid, store, hrs * rate)
    for t in tl_rows:
        eid = t.get("employee_id")
        if eid not in salaried_ids:
            continue
        store = (t.get("store_code") or "").strip()
        if not store:
            continue
        hrs = float(t.get("hours") or 0)
        rate = rate_map.get(eid, 0.0)
        payroll_salary.accumulate(emp_store_hours, eid, store, hrs)
        payroll_salary.accumulate(emp_store_dollars, eid, store, hrs * rate)

    # SALARY PAY-BASIS OVERRIDE (owner directive 2026-07-27, Deliverable 4) — see
    # payroll_salary.apply_to_by_store's own docstring for the subtract-hourly/add-derived mechanics.
    # With the D1 fix immediately above, an inactive-AND-salaried employee's real-activity hourly $ is
    # now tracked in emp_store_hours/emp_store_dollars exactly like the active path, so this call nets
    # them correctly (subtract the real hourly $, add the derived salary allocated proportional to
    # those same hours) instead of adding the derived salary on top of an untouched hourly figure.
    if lo and hi and salaried_ids:
        try:
            by_store = payroll_salary.apply_to_by_store(
                by_store, employees, emp_store_hours, emp_store_dollars, _tenant_pp_settings(org_id),
                _date.fromisoformat(lo), _date.fromisoformat(hi) - timedelta(days=1))
        except Exception as e:
            _warn_salary_override_failed(response, org_id, "GET /payroll-by-store", e)

    # LUNCH-BREAK AUTO-DEDUCTION (2026-07-27, Deliverable 3) — the SAME guarded per-(employee, day)
    # result /payroll uses, attributed to the store of that day's marked punch, so the Store Expenses
    # "Employee Salaries" auto-fill stays in step with /payroll's own netted total instead of silently
    # overstating labor cost by the deducted amount. Clamped against the STORE's total hours (a valid,
    # if loose, upper bound — one employee's own deduction can never exceed their own contribution to
    # that store's total, which is <= the store total). Byte-identical no-op until migration 418 runs.
    #
    # Gate-1 merge hand-fix (2026-07-27): a SALARIED employee's `by_store[store]["amount"]` no longer
    # means "hours × pay_rate" once the salary override above has run — it's their derived-salary
    # allocation. Subtracting an HOURS-based lunch deduction dollar figure (`_applied * _rate`) from
    # that would either double-subtract (the salary override already fully replaced their hourly
    # contribution) or corrupt a salary figure with an hourly concept. Salaried employees are
    # completely skipped here — their pay is never hours-derived, so lunch deduction never touches it
    # in the by-store view (their DISPLAYED hours reduction still happens correctly on GET /payroll,
    # which computes lunch BEFORE the salary override does a full overwrite of actual_pay — see that
    # endpoint's own lunch-deduction comment).
    try:
        _lunch = _lunch_period_deduction(org_id, lo, hi, sb())
        if _lunch.get("available"):
            for (_eid, _store), _ded in _lunch["by_employee_store"].items():
                if _eid in salaried_ids:
                    continue
                if not _store or _store not in by_store:
                    continue
                _rate = rate_map.get(_eid, 0.0)
                _applied = min(_ded, by_store[_store]["hours"])   # negative-hours guard
                by_store[_store]["hours"] -= _applied
                by_store[_store]["amount"] -= _applied * _rate
            # HONESTY (no-silent-caps doctrine) — see get_payroll's identical comment.
            if _lunch.get("limit_hit"):
                for r in by_store.values():
                    r["lunch_deduction_data_capped"] = True
    except Exception:
        pass

    rows = list(by_store.values())
    for r in rows:
        r["hours"] = round(r["hours"], 2)
        r["amount"] = round(r["amount"], 2)
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]
    return {"month": month, "stores": sorted(rows, key=lambda x: x["store_code"])}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# ACTUAL-HOURS DRILL-DOWN (owner directive 2026-07-27, Deliverable 2): "need a drill down for the
# payroll hours showing as actual" — clicking a rep's Actual Hrs on the report opens this.
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/payroll/actual-hours-detail")
def payroll_actual_hours_detail(employee_id: str, start: str, end: str,
                                 authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Day-by-day composition behind a rep's Actual Hrs figure: each day's shift + punch pairs (in/
    out, store, source), which were manually edited/overridden (cross-referenced against
    storeops.payroll_change_log, migration 414), and day subtotals that reconcile EXACTLY to
    `/payroll`'s own total for this employee/range — including faithfully reproducing (never
    silently "fixing") the SAME per-source no-double-count rule `/payroll` itself applies, so this
    is a genuine explanation of the displayed number, not a different, prettier one. See
    payroll_identity.py's module docstring + docs/handoffs/people.md Deliverable 3 for the root
    cause: a Schedule-created shift stores the employee's NUMERIC id while a kiosk punch stores their
    BUSINESS id, so `/payroll`'s own no-double-count dedup (which compares those raw ids) silently
    never fires for most employees — a day with both a shift AND a punch counts BOTH, additively.
    This endpoint surfaces that explicitly per day (`double_counted: true` + a plain-language note)
    instead of hiding it, which is exactly what lets a manager tell "real long shift" apart from
    "counted twice" at a glance.

    `employee_id` is the CANONICAL business id (what a merged /payroll row carries) — resolved to
    every id VARIANT a shift might carry via the SAME `_emp_id_variants()` helper the shift-
    extension/force-clockout gate already uses (org_id from the query param, RULE ONE)."""
    if not (employee_id and start and end):
        raise HTTPException(400, "employee_id, start and end are required")
    ids, variant_name = _emp_id_variants(org_id, employee_id)
    # Gate-1 N3 fix (2026-07-27): filter server-side (eq=) instead of fetching the whole org roster
    # and filtering in Python — this endpoint is called once per row-click, but there's no reason to
    # pay the fetch-all cost when the query can do it (house perf doctrine).
    emp_rows = _employees_with_pay_fields(org_id, "employee_id,name,pay_rate,is_active",
                                           eq={"employee_id": employee_id})
    emp = emp_rows[0] if emp_rows else {}
    name = emp.get("name") or variant_name or employee_id
    pay_rate = float(emp.get("pay_rate") or 0)
    is_inactive = emp.get("is_active") is False

    shifts = (sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
              .gte("shift_date", start).lte("shift_date", end)
              .in_("employee_id", list(ids)).execute().data) or []
    timelog = (sb().table("timelog").select("*").eq("org_id", org_id)
               .gte("work_date", start).lte("work_date", end)
               .in_("employee_id", list(ids)).execute().data) or []
    manual = (sb().table("manual_hours").select("*").eq("org_id", org_id)
              .gte("work_date", start).lte("work_date", end)
              .in_("employee_id", list(ids)).execute().data) or []
    try:
        edits = (sb().table("payroll_change_log").select("source_table,source_id,work_date").eq("org_id", org_id)
                 .gte("work_date", start).lte("work_date", end)
                 .in_("employee_id", list(ids)).limit(2000).execute().data) or []
    except Exception:
        edits = []
    edited_source_ids = {(e.get("source_table"), str(e.get("source_id"))) for e in edits if e.get("source_id")}

    # LUNCH-BREAK AUTO-DEDUCTION (owner directive 2026-07-27, Deliverable 3) — the SAME
    # compute_lunch_deduction_from_rows() /payroll uses, fed the SAME `timelog` rows this endpoint
    # already fetched (no second query) so the two reconcile EXACTLY. Merges across id variants (in
    # practice always just one — timelog.employee_id is always the business id) keyed by work_date.
    lunch_by_day: dict = {}
    lunch_available = False
    try:
        _tenant_cfg, _overrides, lunch_available = _lunch_get_config(org_id, sb())
        if lunch_available:
            _lunch_result = _lunch_compute_from_rows(timelog, _tenant_cfg, _overrides)
            for _d in _lunch_result["days"]:
                if _d.get("employee_id") not in ids:
                    continue
                _wd = _d["work_date"]
                _acc = lunch_by_day.setdefault(_wd, {"deduct_hours": 0.0, "applied": False,
                                                      "skip_reason": None, "minutes_configured": _d.get("minutes_configured", 0)})
                if _d.get("applied"):
                    _acc["deduct_hours"] = round(_acc["deduct_hours"] + _d["deduct_hours"], 2)
                    _acc["applied"] = True
                elif not _acc["applied"] and _acc["skip_reason"] is None:
                    _acc["skip_reason"] = _d.get("skip_reason")
    except Exception:
        lunch_by_day = {}
        lunch_available = False

    days: dict = {}

    def day(d, store=None):
        row = days.setdefault(d, {"work_date": d, "store_code": None, "shift": None, "punches": [],
                                  "manual": [], "actual_hours": 0.0, "scheduled_hours": 0.0,
                                  "edited": False, "double_counted": False, "note": None})
        if store and not row["store_code"]:
            row["store_code"] = store
        return row

    # Shift contribution — a day covered by a shift ALWAYS gets the shift's own eff value added to
    # `/payroll`'s bucket for whichever raw id that shift row carries. `matches_business_id` is
    # exactly the test that determines whether THIS shift lands in the SAME aggregation bucket as
    # the employee's timelog rows (both `/payroll`'s legacy Python `shift_days` set and mig-407's SQL
    # anti-join key off raw employee_id equality) — reproducing it here is what makes the dedup below
    # match `/payroll` instead of silently being "more correct" than the number it's explaining.
    shift_days_same_bucket = set()
    for s in shifts:
        d = str(s.get("shift_date") or "")[:10]
        if not d:
            continue
        row = day(d, s.get("store_code"))
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        matches_business_id = str(s.get("employee_id")) == str(employee_id)
        if is_inactive:
            eff = act if act > 0 else 0.0       # phantom (act==0) shift never counts for an inactive rep
            counted = act > 0
        else:
            eff = act if act > 0 else sched      # active-path act==0->scheduled fallback
            counted = True
        row["scheduled_hours"] += sched if (not is_inactive or act > 0) else 0.0
        row["actual_hours"] += eff
        row["shift"] = {"id": s.get("id"), "start_time": s.get("start_time"), "end_time": s.get("end_time"),
                        "scheduled_hours": sched, "actual_hours": act, "effective_hours": eff,
                        "status": s.get("status"), "store_code": s.get("store_code"), "counted": counted,
                        "edited": ("shifts", str(s.get("id"))) in edited_source_ids}
        if row["shift"]["edited"]:
            row["edited"] = True
        if matches_business_id and counted:
            shift_days_same_bucket.add(d)

    for t in timelog:
        d = str(t.get("work_date") or "")[:10]
        if not d:
            continue
        row = day(d, t.get("store_code"))
        closed = bool(t.get("clock_out") and t.get("hours") is not None)
        hrs = float(t.get("hours") or 0) if closed else 0.0
        counted = closed and d not in shift_days_same_bucket
        edited = ("timelog", str(t.get("id"))) in edited_source_ids
        row["punches"].append({"id": t.get("id"), "clock_in": t.get("clock_in"), "clock_out": t.get("clock_out"),
                               "hours": t.get("hours"), "store_code": t.get("store_code"),
                               "device": t.get("device"), "face_match_pct": t.get("face_match_pct"),
                               "counted": counted, "edited": edited})
        if edited:
            row["edited"] = True
        if counted:
            row["actual_hours"] += hrs
        if closed and row.get("shift") is not None and row["shift"].get("counted") and d not in shift_days_same_bucket:
            # the shift exists but landed in a DIFFERENT raw-id bucket than this punch (the common,
            # buggy case) -> both counted -> flag it plainly.
            row["double_counted"] = True
            row["note"] = (f"{row['shift']['effective_hours']:.1f}h from the schedule AND "
                            f"{hrs:.1f}h from a separate clock punch both counted this day — see "
                            f"Deliverable 3 (payroll investigation) in docs/handoffs/people.md.")

    # Gate-1 N2 fix (2026-07-27, MEDIUM, honesty): GET /payroll never reads storeops.manual_hours on
    # ANY path (grep-verified — neither the legacy Python aggregation nor the mig-407
    # payroll_month_rows SQL touches that table at all). Folding a manual_hours row into
    # `actual_hours` here would make this endpoint's total DIVERGE from the /payroll report row it
    # exists to explain, for any employee with a manual-hours entry in range — exactly what the
    # harness (Section C) now proves does NOT happen. Shown for transparency (a real correction
    # happened, worth seeing) but marked `counted: false` and EXCLUDED from the reconciling total —
    # consistent with the SAME `counted` language already used for shift/punch line items. Do NOT
    # fold manual_hours into /payroll itself to "fix" this instead — that changes a pay figure
    # (propose-first, see docs/handoffs/people.md Deliverable 3).
    total_manual_not_in_payroll = 0.0
    for m in manual:
        d = str(m.get("work_date") or "")[:10]
        if not d:
            continue
        row = day(d)
        hrs = float(m.get("hours") or 0)
        edited = ("manual_hours", str(m.get("id"))) in edited_source_ids
        row["manual"].append({"id": m.get("id"), "hours": hrs, "reason": m.get("reason"), "edited": edited,
                              "counted": False})
        row["edited"] = True   # a manual_hours row is a manual adjustment by definition — worth flagging
        total_manual_not_in_payroll += hrs

    out_days = [days[d] for d in sorted(days)]
    total_lunch_deduction = 0.0
    for r in out_days:
        r["actual_hours"] = round(r["actual_hours"], 2)
        r["scheduled_hours"] = round(r["scheduled_hours"], 2)
        # Lunch deduction is applied HERE — after the shift/punch/(informational)manual composition
        # above, so `actual_hours` below reconciles EXACTLY to /payroll's own row (which nets the SAME
        # per-day amount off the SAME basis — see get_payroll's own lunch-deduction block). HONESTY:
        # never a silent subtraction — `lunch_deduction_hours`/`applied`/`skip_reason` are their own
        # explicit fields, the frontend renders them as a visible "− 0:30 lunch (auto)" line.
        # EQUIVALENCE: these three keys are only added to a day row when the feature is genuinely
        # available (migration 418 ran) — never fabricated as a bare 0.0/False/None triplet, so the
        # response stays byte-identical to a pre-lunch-deduction caller until then.
        if lunch_available:
            ld = lunch_by_day.get(r["work_date"])
            applied_hours = min(ld["deduct_hours"], r["actual_hours"]) if (ld and ld["applied"]) else 0.0  # negative-hours guard
            r["actual_hours"] = round(max(0.0, r["actual_hours"] - applied_hours), 2)
            r["lunch_deduction_hours"] = round(applied_hours, 2)
            r["lunch_deduction_applied"] = applied_hours > 0
            r["lunch_deduction_skip_reason"] = None if applied_hours > 0 else (ld.get("skip_reason") if ld else None)
            total_lunch_deduction += applied_hours
    total_actual = round(sum(r["actual_hours"] for r in out_days), 2)
    total_scheduled = round(sum(r["scheduled_hours"] for r in out_days), 2)
    total_lunch_deduction = round(total_lunch_deduction, 2)

    ks = scope_keyset(authorization, org_id)
    if ks is not None and out_days:
        if not any(in_keyset(ks, d.get("store_code")) for d in out_days):
            raise HTTPException(403, "not in your scope")

    # Salary pay-basis note (Deliverable 3 UX requirement: "salaried — pay not hours-derived" note in
    # the drill-down). Read-only/display-only here — this endpoint never computes a pay figure other
    # than the informational salary_derived_pay shown alongside the hours breakdown; the AUTHORITATIVE
    # figure is always GET /payroll's own row (payroll_salary.apply_to_payroll_rows).
    salary_meta = {}
    if "pay_basis" in emp:
        basis, amount = payroll_salary.resolve_pay_basis(emp)
        if basis != "hourly":
            derived = None
            if amount and amount > 0:
                try:
                    derived = payroll_salary.derive_salary_pay(
                        basis, amount, _tenant_pp_settings(org_id),
                        _date.fromisoformat(start[:10]), _date.fromisoformat(end[:10]),
                        payroll_salary.parse_date(emp.get("hire_date")),
                        payroll_salary.parse_date(emp.get("termination_date")))
                except Exception:
                    derived = None
            salary_meta = {
                "pay_basis": basis, "pay_amount": amount,
                "salary_period_pay": (derived or {}).get("period_pay"),
                "salary_derived_pay": (derived or {}).get("amount"),
                "salary_prorated": (derived or {}).get("prorated", False),
                "salary_note": ("Salaried — pay is not derived from these hours; shown for reference only."
                                 if derived else
                                 f"pay_basis is '{basis}' but no pay_amount is configured — pay is not derived."),
            }

    out = {"employee_id": employee_id, "name": name, "pay_rate": pay_rate, "start": start, "end": end,
           "days": out_days, "total_actual_hours": total_actual, "total_scheduled_hours": total_scheduled,
           "total_manual_hours_not_in_payroll": round(total_manual_not_in_payroll, 2)}
    if lunch_available:
        out["total_lunch_deduction_hours"] = total_lunch_deduction
    out.update(salary_meta)
    return out


# ════════════════════════════════════════════════════════════════════════════════════════════════
# WEEKLY HOURS OVER-LIMIT HIGHLIGHTING (owner directive 2026-07-27, Deliverable 3): "need to track...
# a lot of employees ... are showing over 80 hours but their limit has been set for 78 per store per
# week" — the limit ALREADY EXISTS as storeops.hours_budget.weekly_hours (migration 087, the SAME
# config `/hours-budgets` + the Schedule page's create-shift guard already read — RULE TWO: reuse,
# don't invent a parallel setting), but until now it was read ONLY at schedule-CREATE time
# (`_enforce_hours_budget`, above) to block a new SCHEDULED shift from exceeding it — nothing in the
# payroll/report code path ever compared it to ACTUAL clocked hours. This endpoint is read-only,
# DISPLAY-ONLY: it flags a (store, work-week) whose ACTUAL hours exceed the budget; it never blocks
# scheduling or changes any pay figure.
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/payroll/over-hours")
def payroll_over_hours(start: str, end: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per (store, work-week) actual hours vs. the store's configured weekly hours budget, for every
    week that overlaps [start, end]. Also flags any INDIVIDUAL employee whose own actual hours that
    week already exceed the store's whole-team budget (the sharpest anomaly signal — one person
    alone consuming the entire store's weekly allowance). Read-only; org-scoped; degrades to `budget:
    null` (never flagged) for a store with no budget configured."""
    if not (start and end):
        raise HTTPException(400, "start and end are required")
    budgets = {b["store_code"]: float(b.get("weekly_hours") or 0)
               for b in (sb().table("hours_budget").select("store_code,weekly_hours").eq("org_id", org_id).execute().data or [])
               if b.get("weekly_hours") is not None}
    weeks = []
    cur = start
    guard = 0
    while cur <= end and guard < 60:
        ws, we = _work_week_bounds(org_id, cur)
        if not weeks or weeks[-1][0] != ws:
            weeks.append((ws, we))
        cur = (_date.fromisoformat(we) + timedelta(days=1)).isoformat()
        guard += 1
    ks = scope_keyset(authorization, org_id)
    out = []
    for ws, we in weeks:
        rows = get_payroll(start=ws, end=we, authorization=authorization, org_id=org_id)
        by_store: dict = {}
        for r in rows:
            st = r.get("store") or ""
            if not st:
                continue
            d = by_store.setdefault(st, {"store_code": st, "actual_hours": 0.0, "employees": []})
            ah = round(float(r.get("actual_hours") or 0), 2)
            d["actual_hours"] += ah
            d["employees"].append({"employee_id": r.get("employee_id"), "name": r.get("name"), "actual_hours": ah})
        for st, d in by_store.items():
            budget = budgets.get(st)
            if ks is not None and not in_keyset(ks, st):
                continue
            d["actual_hours"] = round(d["actual_hours"], 2)
            d["weekly_hours_limit"] = budget
            d["over"] = budget is not None and d["actual_hours"] > budget + 1e-6
            for e in d["employees"]:
                e["over_alone"] = budget is not None and e["actual_hours"] > budget + 1e-6
            d["employees"].sort(key=lambda e: -e["actual_hours"])
            out.append({"week_start": ws, "week_end": we, **d})
    return {"weeks": out}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# PAYROLL-TIME CHARGEBACKS (2026-07-22, owner-directed, MONEY-ADJACENT — shows/decides deductions on
# the Payroll Report). `commcalc.ops_chargeback` + `commcalc.ops_chargeback_policy` are OWNED and
# CREATED by mod-retail-ops in its own migration band; this router only READS rows and UPDATES their
# status/decision fields — it never inserts a chargeback (detection/creation is
# closing/ops_chargebacks.py's detect_missed_closings, called from the time-clock punch handlers
# above). Every lookup degrades to an empty list / a clear 400 if that table isn't there yet, so a
# pending migration on ANOTHER agent's branch never breaks this page.
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _chargeback_policy_labels(org_id):
    """org-scoped {reason: label} override map from commcalc.ops_chargeback_policy.label (retail-ops
    schema v2, 2026-07-22 owner follow-up) — ONE lookup per request, never per-row. A blank/absent
    label for a reason falls through to the code default. Degrades to {} if the table/column isn't
    there yet (pre-migration on retail-ops' branch)."""
    try:
        rows = (get_supabase().schema("commcalc").table("ops_chargeback_policy").select("reason,label")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {}
    return {r.get("reason"): (r.get("label") or "").strip() for r in rows if (r.get("label") or "").strip()}


@router.get("/payroll-chargebacks")
def payroll_chargebacks(month: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """This pay period's commcalc.ops_chargeback rows with applied_to='payroll' (missed-closing
    chargebacks against an employee's pay, PLUS any commission-settlement OVERFLOW child rows the
    settlement engine upserts with parent_id set — those arrive already status='posted') —
    read-only, org + store-RBAC scoped like /payroll. `select("*")` so parent_id/covered_amount
    pass through automatically once retail-ops' v2 columns exist; absent otherwise (degrade-safe)."""
    lo = hi = None
    if month:
        parts = str(month).split("-")
        y, m = int(parts[0]), int(parts[1])
        lo, hi = f"{month}-01", (f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01")
    try:
        q = (get_supabase().schema("commcalc").table("ops_chargeback").select("*")
             .eq("org_id", org_id).eq("applied_to", "payroll"))
        if lo and hi:
            q = q.gte("incident_date", lo).lt("incident_date", hi)
        rows = q.order("incident_date", desc=True).limit(2000).execute().data or []
    except Exception:
        return {"items": []}
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]
    policy_labels = _chargeback_policy_labels(org_id)
    for r in rows:
        r["reason_label"] = _chargeback_reason_label(r.get("reason"), policy_labels)
    return {"items": rows}


@router.post("/payroll-chargebacks/{cb_id}/decision")
def decide_payroll_chargeback(cb_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """MANAGEMENT-GATED (same _require_manager tier as the shift-extension/DM-approval endpoints
    above): POST a chargeback (status='posted' — becomes a visible deduction on that employee's
    payroll row) or WAIVE it (status='waived' — never deducts). Body: {decision: 'post'|'waive',
    period?}. Only ever UPDATEs an existing, org+applied_to-scoped row — never inserts one.

    2026-07-22 owner follow-up rule (CASCADE settlement overflow): a commission-settlement engine
    (mod-commission) may UPSERT overflow CHILD rows here (parent_id set, applied_to='payroll',
    status already 'posted', decided_by='settlement') — the remainder a person's commission couldn't
    fully absorb. Owner default: POST is only ever valid on a row still 'pending' (a settlement
    child never is — it arrives already posted, so this also structurally blocks re-posting one);
    WAIVE is allowed on ANY row regardless of status/parent_id — management can still cancel/reverse
    a posted row, including a settlement-created overflow child, at any time."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    decision = (body.get("decision") or "").strip().lower()
    if decision not in ("post", "waive"):
        raise HTTPException(400, "decision must be 'post' or 'waive'")
    try:
        rows = (get_supabase().schema("commcalc").table("ops_chargeback").select("*")
                .eq("id", cb_id).eq("org_id", org_id).eq("applied_to", "payroll")
                .limit(1).execute().data) or []
    except Exception:
        raise HTTPException(400, "Chargebacks aren't available yet — pending a migration on another module.")
    if not rows:
        raise HTTPException(404, "unknown chargeback")
    row = rows[0]
    if decision == "post":
        if str(row.get("status") or "").lower() != "pending":
            raise HTTPException(409, f"Already {row.get('status')} — cannot post again.")
        if row.get("parent_id"):
            raise HTTPException(409, "This is a commission-settlement overflow row — it arrives "
                                      "already posted and can only be waived, never posted.")
    # decision == "waive": no restriction (any status, any parent_id) — see owner-rule docstring above.
    upd = {"status": "posted" if decision == "post" else "waived",
           "decided_by": mgr.get("email"), "decided_at": datetime.now(timezone.utc).isoformat()}
    if decision == "post":
        upd["posted_ref"] = (body.get("period") or "").strip() or None
    (get_supabase().schema("commcalc").table("ops_chargeback").update(upd)
     .eq("id", cb_id).eq("org_id", org_id).execute())
    return {"ok": True, "status": upd["status"], "decided_by": mgr.get("email")}


ORG_ID = "00000000-0000-0000-0000-000000000001"

EMP_FIELDS = ("name", "home_store", "role", "pay_rate", "is_active", "email",
              "phone", "notes", "epay_login", "epay_salesperson", "employee_id",
              "pay_basis", "pay_amount", "termination_date")
STORE_FIELDS = ("store_code", "address", "market", "monthly_target", "is_active", "phone", "notes")

# Pay-adjacent fields on `employees` (2026-07-27 Deliverable 6): PATCH /employees/{id} previously took
# NO role gate at all for pay_rate — only org_id scoping. Per the owner dispatch's explicit rule ("if
# ungated, gate BOTH + note"): editing any of these now requires `_require_manager`. A non-pay field
# edit in the SAME PATCH body (name/email/home_store/...) is unaffected, so this can't break an
# existing non-pay-editing caller.
_PAY_GATED_FIELDS = {"pay_rate", "pay_basis", "pay_amount", "termination_date"}
# The SAME set, also logged to storeops.payroll_change_log on change (Gate-1 F2, 2026-07-27 — every
# gated field IS a pay field, so every gated field is logged; a tuple, not a set, for a deterministic
# select-column-list/diff-loop order).
_PAY_LOGGED_FIELDS = ("pay_rate", "pay_basis", "pay_amount", "termination_date")


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
def update_employee(emp_id: str, updates: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Update an employee (name/role/home_store/pay_rate/active/contact/pay_basis/pay_amount).
    StoreOps Admin + HR (hr_update_employee delegates here).
    emp_id is str (not int) so a UUID or numeric id both work — a typed int rejected UUID ids
    with a 422, which read as 'cannot edit' in the UI.

    org_id-scoped so a foreign (guessable BIGSERIAL) emp_id is a no-op instead of a cross-tenant
    write — this previously took NO org filter at all, and it's the pay_rate write path.

    MANAGER-GATED for pay fields (see _PAY_GATED_FIELDS) — a genuine security hardening added
    2026-07-27, not just posture-matching (docs/handoffs/people.md). Every field in
    `_PAY_LOGGED_FIELDS` (pay_rate/pay_basis/pay_amount/termination_date — ALL of the gated fields,
    since all four are pay-relevant) is best-effort logged to storeops.payroll_change_log
    (entry_point='pay_basis_change', migration 414) so it shows the same ✎ manual-edit trail as an
    hours correction. Gate-1 F2 fix (2026-07-27): termination_date and pay_rate were gated but NOT
    logged before this — both are now in the logged set, same one-liner as pay_basis/pay_amount."""
    row = {k: updates[k] for k in EMP_FIELDS if k in updates}
    if not row:
        raise HTTPException(400, "no valid fields to update")
    if _PAY_GATED_FIELDS & set(row):
        _require_manager(authorization, org_id)
    # Clearing the Emp ID must store NULL, not '' (TEXT UNIQUE → '' collides across people).
    if "employee_id" in row and not (row.get("employee_id") or "").strip():
        row["employee_id"] = None
    if "pay_basis" in row:
        b = str(row["pay_basis"] or "hourly").strip().lower()
        row["pay_basis"] = b if b in payroll_salary.PAY_BASES else "hourly"
    if "pay_amount" in row:
        try:
            row["pay_amount"] = float(row["pay_amount"]) if row["pay_amount"] not in (None, "") else None
        except (TypeError, ValueError):
            row["pay_amount"] = None
    before = None
    if set(_PAY_LOGGED_FIELDS) & set(row):
        try:
            before_rows = (sb().table("employees").select(
                "id,employee_id,name," + ",".join(_PAY_LOGGED_FIELDS))
                .eq("id", emp_id).eq("org_id", org_id).limit(1).execute().data) or []
        except Exception:
            # Gate-1 NIT-A fix (2026-07-28, MUST): the widened select above names ALL of
            # _PAY_LOGGED_FIELDS unconditionally, including pay_basis/pay_amount/termination_date
            # (migrations 416/417) — PostgREST fails the WHOLE select if any named column doesn't
            # exist yet. Pre-migration, that meant an ORDINARY pay_rate edit (a field that predates
            # this feature entirely) 500'd in the deploy-before-SQL window. Degrade to a select of
            # just the fields that already existed (pay_rate always did) — same widened-select-with-
            # fallback convention _employees_with_pay_fields already uses for every READ path; this
            # is the matching guard for the EDIT path.
            try:
                before_rows = (sb().table("employees").select("id,employee_id,name,pay_rate")
                               .eq("id", emp_id).eq("org_id", org_id).limit(1).execute().data) or []
            except Exception:
                before_rows = []
        before = before_rows[0] if before_rows else None
    r = sb().table("employees").update(row).eq("id", emp_id).eq("org_id", org_id).execute()
    if not r.data:
        raise HTTPException(404, "employee not found")
    after = r.data[0]
    if before is not None:
        who = _who_for_log(authorization, org_id)
        for f in _PAY_LOGGED_FIELDS:
            if f in row and str(before.get(f) or "") != str(after.get(f) or ""):
                _log_payroll_change(org_id, field=f, entry_point="pay_basis_change",
                                     employee_id=after.get("employee_id"), employee_name=after.get("name"),
                                     before=before.get(f), after=after.get(f),
                                     source_table="employees", source_id=after.get("id"), who=who)
    return _ensure_employee_id(after)


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
    # 2026-07-27 money fix: reassign to the target's BUSINESS employee_id (same identity every other
    # payroll source uses), not their numeric primary key — the old `str(tgt["id"])` here created a
    # brand-new numeric-id shift on every merge, the identical bug this whole package fixes elsewhere.
    # Falls back to the numeric id only if the target somehow has no business id yet (pre-existing,
    # safe no-op — matches the pre-fix behavior for that edge case only).
    reassign = {"employee_id": tgt.get("employee_id") or str(tgt["id"]), "employee_name": tgt.get("name")}
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
def bulk_payscale(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Bulk set pay rates from a list. Body: {rows:[{employee_id|name, pay_rate}]}.
    Matches by employee_id, else exact name (case-insensitive). Reports unmatched/bad rows.
    MANAGER-GATED (2026-07-27) — same posture as the single-row PATCH's pay-field gate
    (_PAY_GATED_FIELDS, Deliverable 6). Gate-1 D2 fix (2026-07-28, MODERATE audit): a bulk upload
    previously rewrote every rate with ZERO change-log trail (unlike the single-row PATCH, which logs
    via _log_payroll_change) — a DM could silently mass-edit pay with no ✎ audit marker anywhere.
    Each successfully-updated row now logs the SAME way, entry_point='bulk_payscale', best-effort
    (a log-write failure never blocks the actual rate update, matching every other hook's posture)."""
    _require_manager(authorization, org_id)
    rows = body.get("rows") or body.get("employees") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows[] required")
    emps = sb().table("employees").select("id,employee_id,name,pay_rate").eq("org_id", org_id).execute().data or []
    by_eid = {str(e.get("employee_id")): e for e in emps if e.get("employee_id")}
    by_name = {(e.get("name") or "").strip().lower(): e for e in emps}
    who = _who_for_log(authorization, org_id)
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
        before_rate = match.get("pay_rate")
        sb().table("employees").update({"pay_rate": rate}).eq("id", match["id"]).eq("org_id", org_id).execute()
        updated += 1
        if str(before_rate or "") != str(rate):
            _log_payroll_change(org_id, field="pay_rate", entry_point="bulk_payscale",
                                 employee_id=match.get("employee_id"), employee_name=match.get("name"),
                                 before=before_rate, after=rate,
                                 source_table="employees", source_id=match.get("id"), who=who)
    return {"updated": updated, "errors": errors, "total": len(rows)}


def _collect_markets(org_id: str):
    """Distinct markets for the org's pick-don't-type dropdown (RULE THREE), sourced from BOTH
    storeops.stores.market AND commcalc.store_mapping.market — the two vocabularies this page's
    data flows to/from (stores propagate into store_mapping via _sync_store_mapping /
    _sync_store_mapping_update) — so they can't silently diverge. Delegates to
    app.core.scope.canonical_markets, which folds the IDENTICAL union + most-common/alphabetical-tie
    canonicalisation rule through ONE cached (30s TTL, org-keyed) index — the same index
    `_market_store_codes` now resolves grants against, so the picker and the resolver can never
    disagree again (this was the root cause of "3 markets selected but sees nothing")."""
    return _cscope.canonical_markets(get_supabase(), org_id)


def _canonicalize_market(value, canonical_markets):
    """btrim + case-insensitive match to an existing market -> saves the canonical casing.
    A genuinely new (non-matching) value is kept as-typed (btrimmed) — that's the "create new"
    path. Empty stays empty (Unassigned is explicit and allowed)."""
    s = str(value or "").strip()
    if not s:
        return ""
    for m in canonical_markets:
        if m.lower() == s.lower():
            return m
    return s


@router.get("/markets")
def list_markets(org_id: str = ORG_ID):
    """Distinct market options for the StoreOps Admin Stores editor dropdown (RULE THREE:
    pick-don't-type). Org-scoped; see _collect_markets for sourcing/dedupe rules."""
    return {"markets": _collect_markets(org_id)}


def _norm_addr(x) -> str:
    """Same normalization bar `_norm_store` already uses for store_code matching in this file
    (`.strip().upper()`) — deliberately simple, not a fuzzy/punctuation-stripping matcher (that risk
    trade-off belongs to a dedicated store-matching feature, not a dedupe guard); see
    _sync_store_mapping's docstring for why this specific bar was chosen here."""
    return str(x or "").strip().upper()


def _sync_store_mapping(org_id, stores):
    """Mirror StoreOps-created stores into commcalc.store_mapping so a new store PROPAGATES everywhere
    that reads the mapping (Daily Closing, Assets, Targets, recons, …). Insert-if-absent.
    Best-effort — a mapping failure must never break store creation.

    2026-08-06 dedupe-widening fix (mod-commission escalation, live Luxelink defect: 19 of 20 stores
    DUPLICATED in commcalc.store_mapping — two bulk syncs 47 minutes apart on 2026-08-05 under two
    DIFFERENT store_code naming schemes for the SAME physical stores, e.g. a `LUX-<CITY>-<NAME>` set
    and the plain storeops roster codes). ROOT CAUSE: the old "already have it?" check was
    `.in_("store_code", ...)` — every commcalc.store_mapping CONSUMER actually keys on
    `store_address` (the real identity), so a second sync under a different code for the SAME address
    was invisible to that check and inserted a second row — actively splitting attribution (one code
    scheme answers commission-by-store, a different one is what store_expenses was keyed under).

    FIX: "already have it?" is now the UNION of a store_code match OR a normalized store_address
    match — a second sync under ANY new code for an address that already has a mapping row is
    correctly recognized as a duplicate and skipped, regardless of which naming scheme either sync
    used. Deliberately NOT a DB-level `.upsert(on_conflict=...)` — commcalc.store_mapping's own
    identity columns are NULLABLE (the exact trap that already bit prod once, 2026-08-04: an
    ON CONFLICT target against a nullable unique column silently seeds duplicates; PostgREST upsert
    also cannot target an expression/COALESCE index). This keeps the original SELECT-existing-then-
    INSERT-only-new shape (an app-level 'WHERE NOT EXISTS'), just widened to check the address too —
    no new migration, no constraint change, no upsert.

    Does NOT touch the 19 already-duplicated Luxelink rows — that cleanup is mod-commission's own
    SELECT-first/quarantine/DELETE Block B, owner-approved separately. This is the code fix so a
    THIRD sync (under yet another naming scheme) cannot create a 20th."""
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
        # Fetch existing mapping rows for the org ONCE (both identity columns — code AND address —
        # not a per-store_code-only lookup) and dedupe against BOTH, union-style.
        existing_rows = (c.schema("commcalc").table("store_mapping").select("store_code,store_address")
                         .eq("org_id", org_id).execute().data or [])
        have_codes = {str(m.get("store_code") or "").strip() for m in existing_rows}
        have_addrs = {_norm_addr(m.get("store_address")) for m in existing_rows if m.get("store_address")}
        new = []
        for code, v in want.items():
            if code in have_codes:
                continue                                   # exact code already mapped (unchanged rule)
            addr_key = _norm_addr(v.get("store_address"))
            if addr_key and addr_key in have_addrs:
                continue                                   # THE FIX — same address, different code
            new.append(v)
            have_codes.add(code)                            # guard within THIS batch too (two new rows
            if addr_key:                                    # for the same address in one call never
                have_addrs.add(addr_key)                    # both slip through)
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
    canonical_markets = _collect_markets(org_id)   # RULE THREE: normalize once for the whole batch
    to_insert, skipped = [], 0
    for s in rows_in:
        row = {k: s[k] for k in STORE_FIELDS if k in s}
        code = str(row.get("store_code") or "").strip()
        if not code or code.upper() in existing:
            skipped += 1; continue
        if "market" in row:
            row["market"] = _canonicalize_market(row["market"], canonical_markets)
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
    if to_insert:
        _cscope.invalidate_market_index(org_id)   # new store/market visible in the picker instantly
    return {"inserted": inserted, "skipped": skipped}


@router.post("/stores")
def create_store(store: dict, org_id: str = ORG_ID):
    """Create a store (StoreOps Admin)."""
    row = {k: store[k] for k in STORE_FIELDS if k in store}
    if not (row.get("store_code") or "").strip():
        raise HTTPException(400, "store_code required")
    if "market" in row:
        row["market"] = _canonicalize_market(row["market"], _collect_markets(org_id))
    row["org_id"] = org_id
    if row.get("is_active") is None:
        row["is_active"] = True
    r = sb().table("stores").insert(row).execute()
    _sync_store_mapping(org_id, [row])   # propagate the new store to commcalc.store_mapping
    _cscope.invalidate_market_index(org_id)   # new store/market visible in the picker instantly
    return r.data[0] if r.data else row


def _sync_store_mapping_update(org_id, store_code, patch):
    """Propagate an EXISTING store's is_active/address/market change into commcalc.store_mapping —
    UPDATE only (creation-time propagation stays _sync_store_mapping's job, insert-if-absent).

    2026-07-25 fix: `commcalc.store_mapping` has its OWN `is_active` column, and migration 003
    defines a `storeops.sync_to_commcalc()` trigger FUNCTION meant to keep it in sync — but that
    function is never actually ATTACHED to storeops.stores anywhere in the migration history (no
    `CREATE TRIGGER ... ON storeops.stores` exists for it), and the app-side `_sync_store_mapping`
    only INSERTS a mapping row for a brand-new store, never updates an existing one. So toggling a
    store inactive in StoreOps Admin correctly saved `storeops.stores.is_active` but never reached
    `commcalc.store_mapping.is_active` at all — plausibly part of "stores not going inactive"
    wherever a downstream surface reads store_mapping's own flag instead of storeops.stores'. This
    closes that gap going forward from the PATCH path (the toggle's actual write path) without
    touching the dormant SQL trigger. Best-effort: a sync failure must never break the store update
    itself (same posture as _sync_store_mapping)."""
    upd = {}
    if "is_active" in patch:
        upd["is_active"] = bool(patch["is_active"])
    if "address" in patch:
        upd["store_address"] = patch["address"]
    if "market" in patch:
        upd["market"] = patch["market"]
    if not upd or not store_code:
        return
    try:
        (get_supabase().schema("commcalc").table("store_mapping").update(upd)
         .eq("org_id", org_id).eq("store_code", store_code).execute())
    except Exception as e:
        print(f"WARN store_mapping update-sync failed: {e}")


@router.patch("/stores/{store_id}")
def update_store(store_id: int, updates: dict, org_id: str = ORG_ID):
    """Update a store (StoreOps Admin). org_id-scoped so a foreign (guessable BIGSERIAL) store_id
    is a no-op instead of a cross-tenant write — this previously took NO org filter at all."""
    row = {k: updates[k] for k in STORE_FIELDS if k in updates}
    if not row:
        raise HTTPException(400, "no valid fields to update")
    if "market" in row:
        row["market"] = _canonicalize_market(row["market"], _collect_markets(org_id))
    r = sb().table("stores").update(row).eq("id", store_id).eq("org_id", org_id).execute()
    if not r.data:
        raise HTTPException(404, "store not found")
    _sync_store_mapping_update(org_id, r.data[0].get("store_code"), row)
    _cscope.invalidate_market_index(org_id)   # new store/market visible in the picker instantly
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
    # 2026-07-27 money fix: canonicalize each shift's employee_id to the business id BEFORE it's
    # captured into the template, so a leftover numeric-id shift (pre-fix, or pre-migration-415
    # backfill) never propagates the bug forward every time the template is later applied. Builds a
    # NEW list of shallow copies rather than mutating the fetched rows in place — this function only
    # ever READS storeops.shifts here (never writes it back), so the source rows must stay untouched;
    # a live PostgREST response is a fresh dict per call anyway, but copying keeps that explicit
    # rather than relying on it (same discipline as update_shift's own "before" snapshot copy).
    _save_tmpl_employees = sb().table("employees").select("id,employee_id").eq("org_id", org_id).execute().data or []
    canon_shifts = []
    for s in shifts:
        s = dict(s)
        raw = s.get("employee_id")
        if raw not in (None, ""):
            s["employee_id"] = _canonical_shift_employee_id(org_id, raw, employees=_save_tmpl_employees)
        canon_shifts.append(s)
    shifts = canon_shifts
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
    # 2026-07-27 money fix: canonicalize once for the whole batch (not re-fetched per template row).
    _apply_tmpl_employees = sb().table("employees").select("id,employee_id").eq("org_id", org_id).execute().data or []
    for t in templates:
        target = (ws + timedelta(days=int(t.get("weekday") or 0))).isoformat()
        if (t.get("employee_name"), target, t.get("start_time"), t.get("store_code")) in seen:
            continue
        eid = t.get("employee_id")
        if eid not in (None, ""):
            # Guards against a STALE template saved before this fix (or before migration 415's
            # backfill) still carrying a numeric id — applying it must never create a new
            # numeric-id shift either.
            eid = _canonical_shift_employee_id(org_id, eid, employees=_apply_tmpl_employees)
        if eid:
            # Gate-1 REDO N1 fix — same reasoning as create_shift above: check BOTH id forms so a
            # numeric-keyed admin Time Off row (still possible until that page's own write is fixed)
            # is never silently missed just because `eid` here is now correctly canonicalized.
            lookup_ids, _ = _emp_id_variants(org_id, eid)
            conflict = (sb().table("time_off_requests").select("id").eq("org_id", org_id)
                        .in_("employee_id", list(lookup_ids))
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
    since, until = _emp_ids_window_from_rows(reqs, "created_at")
    eids = scope_emp_ids(authorization, org_id, since=since, until=until)   # None = unrestricted
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


def _apply_swap(swap, org_id: str = ORG_ID, who=None):
    """On approval, reassign the shift(s). If both shifts present it's a true swap;
    otherwise the single shift is handed to the target employee.

    Gate-1 N3 (2026-07-27): a swap approval REWRITES shifts.employee_id — exactly the kind of manual
    hours/assignment fix the Payroll Change Log exists for. Logs each reassigned shift's before/after
    employee (best-effort, never blocks the swap itself). `who` = the approver's identity dict (or
    None if unresolved) for attribution."""
    names = _emp_name_map(org_id)
    tgt, reqr = swap.get("target_id"), swap.get("requester_id")
    if swap.get("shift_id") and tgt:
        before = dict((sb().table("shifts").select("*").eq("id", swap["shift_id"])
                       .eq("org_id", org_id).limit(1).execute().data or [{}])[0])
        sb().table("shifts").update({"employee_id": tgt, "employee_name": names.get(str(tgt))}) \
            .eq("id", swap["shift_id"]).eq("org_id", org_id).execute()
        try:
            _log_payroll_change(org_id, field="employee_id", entry_point="shift_swap",
                                 employee_id=tgt, employee_name=names.get(str(tgt)),
                                 store_code=before.get("store_code"), work_date=before.get("shift_date"),
                                 before=before.get("employee_name") or before.get("employee_id"),
                                 after=names.get(str(tgt)) or tgt, source_table="shifts",
                                 source_id=swap["shift_id"], who=who, reason="shift swap approved")
        except Exception:
            pass
    if swap.get("target_shift_id") and reqr:
        before2 = dict((sb().table("shifts").select("*").eq("id", swap["target_shift_id"])
                        .eq("org_id", org_id).limit(1).execute().data or [{}])[0])
        sb().table("shifts").update({"employee_id": reqr, "employee_name": names.get(str(reqr))}) \
            .eq("id", swap["target_shift_id"]).eq("org_id", org_id).execute()
        try:
            _log_payroll_change(org_id, field="employee_id", entry_point="shift_swap",
                                 employee_id=reqr, employee_name=names.get(str(reqr)),
                                 store_code=before2.get("store_code"), work_date=before2.get("shift_date"),
                                 before=before2.get("employee_name") or before2.get("employee_id"),
                                 after=names.get(str(reqr)) or reqr, source_table="shifts",
                                 source_id=swap["target_shift_id"], who=who, reason="shift swap approved")
        except Exception:
            pass


@router.patch("/shift-swaps/{swap_id}")
def update_shift_swap(swap_id: int, updates: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Approve/deny/cancel a swap. Approving reassigns the shift(s)."""
    status = updates.get("status")
    if status not in ("approved", "denied", "pending", "cancelled"):
        raise HTTPException(400, "invalid status")
    cur = sb().table("shift_swap_requests").select("*").eq("org_id", org_id).eq("id", swap_id).limit(1).execute().data or []
    if not cur:
        raise HTTPException(404, "swap not found")
    if status == "approved":
        _apply_swap(cur[0], org_id, who=_who_for_log(authorization, org_id))
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
    from app.core.tenant_middleware import caller_app_user_http
    u = caller_app_user_http(uid, "org_id,email,role,employee_id")
    if not u:
        raise HTTPException(403, "That login isn't recognized for the company you are working in.")
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


_FACE_OFF_MSG = ("Face recognition is turned off for your account — use the photo-only clock-in "
                 "button. (An admin can turn it back on under Time Clock \u2192 \u2699 Face Recognition.)")


def _face_gate(org_id, employee_id):
    """{enabled, reason} for ONE employee — the single place the kiosk config, the enroll endpoint and
    the descriptor read all get the same answer (owner directive 2026-08-09, migration 420). See
    face_recognition.py for the precedence rules and why the tenant flag is a MASTER switch rather
    than migration 418's per-field override."""
    client = sb()
    tenant_cfg, available = _face.get_tenant_face_config(org_id, client)
    row = _face.get_employee_face_row(org_id, employee_id, client) if employee_id else {}
    return _face.resolve_employee_face(tenant_cfg, row, available)


def _face_flags(gate):
    """The two keys every face endpoint echoes back, so the kiosk always knows WHY it's off."""
    return {"face_recognition_enabled": gate["enabled"], "face_recognition_reason": gate["reason"]}


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
    from app.core.tenant_middleware import caller_app_user_http
    row = caller_app_user_http(uid, "org_id,employee_id") or {}
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
    resp = {"success": True, "data": {"time": _fmt_time(saved.get("clock_in"), org_id), "entry_id": saved.get("id"),
                                      "store_code": req_store}}
    notice = _missed_closing_notice(org_id, employee_id)
    if notice:
        resp["missed_closing_notice"] = notice
    return resp


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
            # Deliverable 4 (Payroll Change Log): this override ADDS an unscheduled shift on the
            # employee's behalf — a manual change to their payroll, log it (best-effort).
            try:
                _log_payroll_change(org_id, field="shift_added", entry_point="timeclock_override",
                                     employee_id=employee_id, employee_name=name, store_code=store_code,
                                     work_date=work_date, before=None, after=store_code,
                                     source_table="shifts", who=mgr)
            except Exception:
                pass
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
    try:
        _log_payroll_change(org_id, field="clock_in", entry_point="timeclock_override",
                             employee_id=employee_id, employee_name=name, store_code=store_code,
                             work_date=work_date, before=None, after=_fmt_time(saved.get("clock_in"), org_id),
                             source_table="timelog", source_id=saved.get("id"), who=mgr)
    except Exception:
        pass
    return {"success": True, "override_by": mgr.get("email"),
            "data": {"time": _fmt_time(saved.get("clock_in"), org_id), "entry_id": saved.get("id"), "store_code": store_code}}


def _missed_closing_notice(org_id, employee_id):
    """Best-effort, NON-blocking: surface this employee's OPEN pending missed-closing chargeback
    items as a punch-time notice (never a block). Built in parallel by mod-retail-ops
    (closing/ops_chargebacks.py, commcalc.ops_chargeback) — degrade to no notice at all if that
    module/table isn't there yet (parallel parked branches must stay independently deployable)."""
    try:
        from app.modules.closing.ops_chargebacks import detect_missed_closings
        items = detect_missed_closings(org_id, employee_id=employee_id) or []
        if not items:
            return None
        dates = sorted({str(i.get("incident_date"))[:10] for i in items if i.get("incident_date")})
        stores = sorted({str(i.get("store_code")) for i in items if i.get("store_code")})
        when = ", ".join(dates) if dates else "a recent shift"
        where = f" at {', '.join(stores)}" if stores else ""
        plural = "s" if len(items) != 1 else ""
        return {"message": f"⚠ You have {len(items)} missed store closing{plural}{where} "
                            f"({when}) still to complete.",
                "items": items}
    except Exception:
        return None


def _closing_gate_block(org_id, employee_id, store_code, work_date=None):
    """Return a block message if this employee is the EFFECTIVE CLOSER for `store_code`, the tenant's
    closing gate is ON, and the store's daily closing for today is NOT yet submitted — else None.
    Cross-module: closings live in commcalc.daily_closing. Any lookup gap → no block (never trap a
    rep on a config/migration miss). NEVER gates a stale punch (work_date before today): the gate
    exists to hold the closer until TODAY's closing is in — applying it to an older open punch
    deadlocks the employee (can't clock out = gated; can't clock in = 409 already-open).

    EFFECTIVE CLOSER (2026-07-22, shared definition with mod-retail-ops' missed-closing detection):
    workers = employees with a timelog punch at this store today.
      (a) if the STATIC store_closer is among today's workers → only they are gated (unchanged from
          the original mig-089 behavior: everyone else clocks out normally).
      (b) else (the static closer is unconfigured OR simply didn't work here today) → the gate falls
          to the LAST worker still clocked in at this store (no other employee has an open punch
          here). Anyone else still clocked in passes — the gate will catch the true last-to-leave
          when THEY try to clock out.

    2026-07-25 UNIVERSAL FIX (multi-session days): (a)/(b) above only ever fire from this employee's
    TRUE final clock-out of the day, never a mid-day break. Scheduled shift today -> gate only once its
    end has passed (precise). No schedule at all (pure-kiosk, e.g. luxelink) -> gate only from this
    employee's SECOND clock-out of the day at this store onward (their first gets the benefit of the
    doubt as a likely break) — see the sched_end / had_prior_close_here checks below."""
    try:
        store = (store_code or "").strip()
        if not store:
            return None
        today = datetime.now(timezone.utc).astimezone(_biz_tz_for(org_id)).date().isoformat()
        if work_date and str(work_date)[:10] != today:
            return None  # stale punch from a previous business day — always closable
        t = (sb().table("tenants").select("closing_gate_enabled").eq("org_id", org_id).limit(1).execute().data) or []
        if not (t and t[0].get("closing_gate_enabled")):
            return None
        # match the submitted closing by NORMALIZED store code — a spelling variant between the
        # punch's store_code and the closing's must not leave the closer blocked after submitting
        done = (get_supabase().schema("commcalc").table("daily_closing").select("store_code")
                .eq("org_id", org_id).eq("close_date", today).execute().data) or []
        if any(_norm_store(r.get("store_code")) == _norm_store(store) for r in done):
            return None

        # 2026-07-25 UNIVERSAL FIX (luxelink: "clocks in the morning, leaves for lunch, comes back and
        # clock-in errors" — root cause traced to THIS gate, not clock-in itself): multiple CLOSED
        # punch sessions per business day are legal (owner rule) — a lunch-break clock-out is NOT the
        # closer's final departure, so it must never be gated; only their TRUE end-of-day clock-out
        # should be held for the closing. Two signals, in priority order:
        #  1. A scheduled shift today (reusing the SAME helper the force-clockout sweep uses, honoring
        #     an approved shift_extension too — no duplicated logic): still before its end -> DEFINITELY
        #     mid-shift, never gate, however many sessions/breaks they take. At/after its end -> fall
        #     through to the existing closer logic below exactly as before (precise, no regression).
        #  2. No schedule at all for this employee (pure-kiosk tenant/employee, the luxelink case) ->
        #     no precise "shift over" signal exists, so use the best available proxy: THIS EMPLOYEE'S
        #     OWN clock-out history at this store today. Their FIRST clock-out of the day gets the
        #     benefit of the doubt (most likely a break) and is never gated; from their SECOND clock-out
        #     of the day onward, gate exactly as before — still holds a genuinely-departing closer
        #     accountable, just no longer deadlocks them on an ordinary lunch break.
        sched_end = _scheduled_end_for_punch(
            org_id, {"employee_id": employee_id, "work_date": work_date or today, "store_code": store})
        if sched_end is not None:
            if datetime.now(timezone.utc) < sched_end:
                return None   # scheduled and still mid-shift
        else:
            try:
                todays_own = (sb().table("timelog").select("store_code,clock_out")
                              .eq("org_id", org_id).eq("employee_id", employee_id)
                              .eq("work_date", work_date or today).execute().data) or []
            except Exception:
                todays_own = []
            had_prior_close_here = any(p.get("clock_out") is not None
                                        and _norm_store(p.get("store_code")) == _norm_store(store)
                                        for p in todays_own)
            if not had_prior_close_here:
                return None   # first clock-out of the day here, no schedule signal -> benefit of the doubt

        # Today's punches at THIS store (any employee, open or closed) — decides both whether the
        # static closer worked today and, if not, who's the last one still clocked in.
        todays_all = (sb().table("timelog").select("employee_id,clock_out,store_code")
                      .eq("org_id", org_id).eq("work_date", today).execute().data) or []
        store_punches = [p for p in todays_all if _norm_store(p.get("store_code")) == _norm_store(store)]

        closer_rows = (sb().table("store_closer").select("employee_id")
                       .eq("org_id", org_id).eq("store_code", store).limit(1).execute().data) or []
        closer_id = str((closer_rows[0].get("employee_id") if closer_rows else "") or "").strip()

        ids, _ = _emp_id_variants(org_id, employee_id)  # the CALLER's own id variants

        closer_worked_today = False
        if closer_id:
            closer_ids, _ = _emp_id_variants(org_id, closer_id)
            closer_worked_today = any(str(p.get("employee_id")) in closer_ids for p in store_punches)

        if closer_id and closer_id in ids:
            # (a) caller IS the static closer — gated iff they actually have a punch here today
            # (they always do at this point: this very entry is one), matching the original rule.
            if closer_worked_today:
                return (f"The daily closing for {store} must be submitted before you clock out. "
                        f"Complete the store closing, then clock out.")
            return None
        if closer_worked_today:
            # the static closer worked here today and isn't this caller — THEY are gated, not this
            # employee. Not our concern here; pass.
            return None

        # (b) effective-closer fallback: no static closer worked here today (unconfigured or
        # absent) — gate only the LAST person still clocked in at this store.
        others_open = [p for p in store_punches
                       if p.get("clock_out") is None and str(p.get("employee_id")) not in ids]
        if others_open:
            return None  # someone else is still clocked in — they may end up the true last-to-leave
        return (f"The daily closing for {store} must be submitted before you clock out. "
                f"As the last one clocked in today, please complete the store closing, then clock out.")
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
    block = _closing_gate_block(org_id, employee_id, entry.get("store_code"), entry.get("work_date"))
    if block and not body.get("override"):
        return {"success": False, "needs_closing": True, "message": block}
    now = datetime.now(timezone.utc)
    out_at = now
    note_add = None
    # Stale punch (opened on a previous business day, e.g. the force-clockout sweep had no scheduled
    # shift to close it against): stamp the clock-out at the SCHEDULED shift end when one exists —
    # the same paid-=-scheduled semantics as _do_force_clockout — instead of inflating hours to
    # now-minus-clock-in across days.
    today = now.astimezone(_biz_tz_for(org_id)).date().isoformat()
    wdate = str(entry.get("work_date") or "")[:10]
    auto_stamped = False
    if wdate and wdate != today:
        end_dt = _scheduled_end_for_punch(org_id, entry)
        if end_dt and end_dt < now:
            out_at = end_dt
            note_add = "auto clock-out at scheduled end (stale punch closed from kiosk)"
            auto_stamped = True
        else:
            note_add = f"stale punch (opened {wdate}) closed from kiosk — review hours"
    try:
        ci = datetime.fromisoformat(str(entry["clock_in"]).replace("Z", "+00:00"))
        hours = round((out_at - ci).total_seconds() / 3600.0, 2)
        if hours < 0:
            hours = 0.0
    except Exception:
        hours = None
    upd = {"clock_out": out_at.isoformat(), "hours": hours}
    if note_add:
        upd["notes"] = ((entry.get("notes") or "") + " | " + note_add).strip(" |")
    sb().table("timelog").update(upd).eq("id", entry["id"]).execute()
    # Gate-1 N3 (2026-07-27): this branch auto-stamps hours away from a raw clock-in/out diff exactly
    # like _do_force_clockout (which IS logged) — log it here too, system-attributed (the employee's
    # own self-service clock-out triggered it, but the HOURS value itself is a system computation,
    # not something they typed in).
    if auto_stamped:
        try:
            _log_payroll_change(org_id, field="clock_out", entry_point="clock_out_stale_auto",
                                 employee_id=employee_id, employee_name=entry.get("employee_name"),
                                 store_code=entry.get("store_code"), work_date=wdate,
                                 before=None, after=f"{out_at.isoformat()} ({hours}h, auto at scheduled end)",
                                 source_table="timelog", source_id=entry.get("id"))
        except Exception:
            pass
    resp = {"success": True, "data": {"time": _fmt_time(out_at.isoformat(), org_id), "hours": hours,
                                      "clock_in": _fmt_time(entry.get("clock_in"), org_id)}}
    notice = _missed_closing_notice(org_id, employee_id)
    if notice:
        resp["missed_closing_notice"] = notice
    return resp


@router.get("/timeclock/list")
def timeclock_list(start: str = "", end: str = "", employee_id: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Timelog entries for a date range (+ optional employee). Newest first.

    RULE ONE: date bounds are INCLUSIVE both ends (`.gte`/`.lte` on `work_date`, which is stored per
    BUSINESS_TZ at punch time — see docs/handoffs/people.md's timeclock filter-bug fix) — a caller
    passing start=07-09&end=07-22 gets exactly those 14 calendar days, never a punch outside the range.

    Owner directive 2026-07-27 (Deliverable 3, lunch-break auto-deduction): each punch that belongs to
    a day where the double-deduction-guarded auto lunch deduction applies (lunch_deduction.py) carries
    `lunch_deduction_hours` — attached to exactly ONE punch per qualifying day (the day's last, by
    clock_in) so summing it across the visible rows never double-counts. This NEVER mutates the punch's
    own `hours` field (HONESTY: an explicit line, never a silent subtraction). EQUIVALENCE: the field
    is added to every row ONLY when migration 418 has actually run (see lunch_deduction.py's DEGRADE
    docstring) — never fabricated as a bare 0.0 on a tenant that's never heard of this feature, so the
    response is byte-identical (same KEYS, not just the same values) to before this feature existed."""
    q = sb().table("timelog").select("*").eq("org_id", org_id)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    if start:
        q = q.gte("work_date", start)
    if end:
        q = q.lte("work_date", end)
    rows = q.order("clock_in", desc=True).limit(5000).execute().data or []
    eids = scope_emp_ids(authorization, org_id, since=start or None, until=end or None)   # None = unrestricted
    if eids is not None:
        rows = [e for e in rows if str(e.get("employee_id")) in eids]
    for e in rows:
        e["selfie_url"] = _signed_selfie(e.get("selfie_path"))
    if start and end:
        try:
            hi = (_date.fromisoformat(str(end)[:10]) + timedelta(days=1)).isoformat()
            ded = _lunch_period_deduction(org_id, str(start)[:10], hi, sb())
            if ded.get("available"):
                for e in rows:
                    e["lunch_deduction_hours"] = 0.0
                    # HONESTY (no-silent-caps doctrine) — see get_payroll's identical comment.
                    if ded.get("limit_hit"):
                        e["lunch_deduction_data_capped"] = True
                marked = {(d["employee_id"], d["work_date"]): d for d in ded["days"] if d.get("applied")}
                for e in rows:
                    d = marked.get((e.get("employee_id"), str(e.get("work_date") or "")[:10]))
                    if d and d.get("marked_punch_id") == e.get("id"):
                        e["lunch_deduction_hours"] = d["deduct_hours"]
        except Exception:
            pass   # never let the lunch-deduction overlay break the punches list itself
    return rows


# ════════════════════════════════════════════════════════════════════════════════════════════════
# ATTENDANCE EXCEPTIONS (owner directive 2026-08-06, verbatim): "time clock should show who were
# scheduled and didn't clock in and also if somebody else clocked in instead of the scheduled".
# All the classification logic (no_show/covered_by_other/unscheduled/late/left_early + the
# approved-time-off EXCUSED label) is PURE and lives in attendance_exceptions.py — see that module's
# docstring for the full correctness writeup (timezone, multi-session, don't-flag-the-future, store
# matching). This handler is I/O only: fetch shifts/timelog/time_off_requests for the range,
# canonicalize employee_id to the BUSINESS id across all three (the exact same numeric-vs-business
# mismatch payroll_identity.py documents for /payroll — a Schedule-page shift's numeric employee_id
# would otherwise never match a punch's business employee_id, producing a false no-show for EVERY
# scheduled employee), resolve the tenant's config (RULE TWO, migration 421, graceful pre-migration
# default), call the pure engine, then apply the SAME RBAC store-span narrowing every sibling
# timeclock endpoint already applies.
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/timeclock/attendance-exceptions")
def attendance_exceptions(start: str = "", end: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Attendance Exceptions for [start, end] (inclusive both ends, matching /timeclock/list's own
    convention — `shift_date`/`work_date` are already business-local, per BUSINESS_TZ at write time).

    RULE FIVE: only the date range triggers this fetch — store/market/rep/exception-type filtering is
    client-side over this already org+span-scoped response, the SAME established pattern the Time
    Clock page itself already uses (see that page's own 2026-07-27 race-fix writeup)."""
    if not (start and end):
        raise HTTPException(400, "start and end are required")
    client = sb()
    FETCH_LIMIT = 20000
    shifts = (client.table("shifts").select(
        "id,employee_id,employee_name,store_code,shift_date,start_time,end_time,is_deleted")
        .eq("org_id", org_id).eq("is_deleted", False)
        .gte("shift_date", start).lte("shift_date", end).limit(FETCH_LIMIT).execute().data) or []
    punches = (client.table("timelog").select(
        "id,employee_id,employee_name,store_code,work_date,clock_in,clock_out")
        .eq("org_id", org_id).gte("work_date", start).lte("work_date", end).limit(FETCH_LIMIT).execute().data) or []
    # HONESTY (no-silent-caps doctrine, same convention as lunch_deduction.period_lunch_deduction):
    # hitting the cap is a strong signal (not proof — PostgREST gives no total count without a
    # separate query) that the range/tenant is too big for this window to be a complete picture.
    # Surfaced to the frontend rather than silently under-reporting exceptions for a huge range.
    limit_hit = len(shifts) >= FETCH_LIMIT or len(punches) >= FETCH_LIMIT
    try:
        timeoff = (client.table("time_off_requests").select(
            "employee_id,start_date,end_date,status,type,notes")
            .eq("org_id", org_id).eq("status", "approved")
            .lte("start_date", end).gte("end_date", start).limit(5000).execute().data) or []
    except Exception:
        timeoff = []

    # Canonicalize employee_id -> the BUSINESS id across all three sources before joining (see banner
    # comment above). `employees` is fetched once; a lookup failure just means no aliasing happens
    # (rows pass through with their raw ids) rather than a 500 — the classifier still runs, it just
    # may under-match a numeric-vs-business mismatch until the employees read succeeds again.
    try:
        employees = (client.table("employees").select("id,employee_id").eq("org_id", org_id).execute().data) or []
    except Exception:
        employees = []
    alias = _business_id_alias_map(employees)

    def _canon(rows):
        out = []
        for r in rows:
            raw = r.get("employee_id")
            if raw in (None, ""):
                out.append(r)
                continue
            canon = alias.get(str(raw), raw)
            out.append({**r, "employee_id": canon} if canon != raw else r)
        return out

    shifts, punches, timeoff = _canon(shifts), _canon(punches), _canon(timeoff)

    cfg, available = _attn.get_tenant_attendance_config(org_id, client)
    tz = _biz_tz_for(org_id)
    now = datetime.now(timezone.utc)
    rows = _attn.compute_attendance_exceptions(shifts, punches, timeoff, cfg, now, tz)

    # RBAC store-span narrowing — same posture/keyset as GET /shifts, GET /stores, GET /timeclock/list.
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]

    counts: dict = {}
    for r in rows:
        counts[r["exception_type"]] = counts.get(r["exception_type"], 0) + 1
    return {"available": available, "config": cfg, "rows": rows, "counts": counts, "limit_hit": limit_hit}


# ── tenant-level thresholds for the report above (RULE TWO admin UI) ──────────────────────────────
@router.get("/timeclock/attendance-config")
def get_attendance_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Current org thresholds for Attendance Exceptions, always a full usable config (defaults when
    migration 421 hasn't run yet — see attendance_exceptions.get_tenant_attendance_config)."""
    cfg, available = _attn.get_tenant_attendance_config(org_id, sb())
    return {"config": cfg, "available": available}


@router.put("/timeclock/attendance-config")
def set_attendance_config(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager/admin only. Persists the 5 attendance-exception thresholds (RULE TWO). Values are
    clamped the same way `attendance_exceptions.resolve_config` clamps them (never negative, mode
    restricted to label/suppress) before being written, so a bad payload can't corrupt the config row."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    cfg = _attn.resolve_config(body)
    try:
        sb().table("tenants").update({
            "attendance_late_grace_min": cfg["late_grace_min"],
            "attendance_early_leave_grace_min": cfg["early_leave_grace_min"],
            "attendance_noshow_grace_min": cfg["noshow_grace_min"],
            "attendance_coverage_overlap_min": cfg["coverage_overlap_min"],
            "attendance_timeoff_mode": cfg["timeoff_mode"],
        }).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't save the setting — is migration 421 applied?")
    return {"ok": True, "config": cfg}


_CHARGEBACK_REASON_LABELS = {
    "missed_closing": "Missed store closing",
    "missed_dm_verify": "Missed DM store-visit verification",
}


def _chargeback_reason_label(reason, policy_labels=None):
    """Plain-language label for a chargeback reason. Prefers a management-set
    commcalc.ops_chargeback_policy.label override for the org (2026-07-22 owner follow-up — pass the
    ONE-per-request map from _chargeback_policy_labels, never re-fetched per row), falling back to
    the code default map when no override is configured for that reason."""
    if policy_labels and policy_labels.get(reason):
        return policy_labels[reason]
    return _CHARGEBACK_REASON_LABELS.get(str(reason or ""), str(reason or "Chargeback").replace("_", " ").title())


@router.get("/my-chargebacks")
def my_chargebacks(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The SIGNED-IN employee's own commcalc.ops_chargeback rows — identity comes from the auth
    token (SAME rule as every other self-view endpoint here: /timeclock/status, /timeclock/face…),
    NEVER a client-supplied employee_id. Every reason (payroll or commission-side), for the
    Employee Dashboard's "My Chargebacks" card. Degrades to an empty list if the table isn't there
    yet (mod-retail-ops' migration, parallel parked branch). Passes through the CASCADE-settlement
    fields (parent_id, covered_amount — retail-ops schema v2, 2026-07-22) when present so the card
    can show "$X from commission" / overflow-origin context; both are simply absent pre-migration."""
    org_id, employee_id = _caller_identity(authorization)
    ids, _name = _emp_id_variants(org_id, employee_id)
    try:
        rows = (get_supabase().schema("commcalc").table("ops_chargeback").select("*")
                .eq("org_id", org_id).in_("employee_id", list(ids))
                .order("incident_date", desc=True).limit(500).execute().data) or []
    except Exception:
        return {"items": []}
    policy_labels = _chargeback_policy_labels(org_id)
    items = [{
        "id": r.get("id"),
        "reason": r.get("reason"),
        "reason_label": _chargeback_reason_label(r.get("reason"), policy_labels),
        "store_code": r.get("store_code"),
        "incident_date": r.get("incident_date"),
        "amount": r.get("amount"),
        "status": r.get("status"),
        "applied_to": r.get("applied_to"),
        "parent_id": r.get("parent_id"),
        "covered_amount": r.get("covered_amount"),
    } for r in rows]
    return {"items": items}


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
    # FACE RECOGNITION ENABLEMENT (owner directive 2026-08-09, migration 420) — resolved for THIS
    # caller: tenant master switch, then their own consent/assignment. The kiosk reads this and skips
    # the entire face-api path (photo-only clock-in, the non-biometric alternative the security plan's
    # Phase 9.3 asks for) when it is false. The backend enforces the SAME answer on the enroll and
    # descriptor endpoints, so a stale kiosk bundle can never keep capturing biometrics after the
    # switch goes off. Identity is best-effort: an unresolvable caller gets the TENANT-level answer
    # with no per-employee assignment applied (still fail-closed when the tenant is off).
    try:
        face_org, face_emp = _caller_identity(authorization)
    except Exception:
        face_org, face_emp = org_id, None
    gate = _face_gate(face_org, face_emp)
    return {"face_match_threshold": thr, **_face_flags(gate)}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# FACE-RECOGNITION SETTINGS (owner directive 2026-08-09, migration 420): OFF for every tenant, with a
# master switch to turn it back on later, a per-employee assignment, and a consent record stamped
# across the whole roster at the moment the switch goes ON ("as if the consent has been signed by all
# employees" — recorded as a dated per-employee row, never a silent assumption).
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/timeclock/face-config")
def get_face_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Tenant switch + the per-employee assignment/consent roster, for the Time Clock page's
    ⚙ Face Recognition panel and the HR “Employees & Pay” tab. Any signed-in caller may read (same
    posture as GET /timeclock/config and /timeclock/lunch-config — it exposes no biometric data, only
    whether the feature is on). `available=False` whenever migration 420 hasn't run on this tenant."""
    client = sb()
    tenant_cfg, available = _face.get_tenant_face_config(org_id, client)
    rows, roster_available = _face.get_employee_face_rows(org_id, client)
    # How many biometric templates are currently STORED (they are kept, not deleted, while the feature
    # is off — that is what makes re-enabling instant instead of a re-enrollment campaign). Shown in
    # the panel so the retention question stays visible rather than invisible.
    try:
        enrolled = len((client.table("face_descriptors").select("employee_id")
                        .eq("org_id", org_id).limit(10000).execute().data) or [])
    except Exception:
        enrolled = 0
    return {"available": bool(available and roster_available), "tenant": tenant_cfg,
            "summary": _face.consent_summary(rows), "enrolled_templates": enrolled,
            "employees": [{"employee_id": eid,
                           "face_recognition_enabled": r.get("face_recognition_enabled"),
                           "face_consent_status": r.get("face_consent_status"),
                           "face_consent_at": r.get("face_consent_at"),
                           "face_consent_source": r.get("face_consent_source")}
                          for eid, r in rows.items()]}


@router.put("/timeclock/face-config")
def set_face_config(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager/admin only (same gate as PUT /timeclock/lunch-config — a tenant-wide toggle, not a
    single-row edit). Body: {enabled?, default_for_employees?}.

    Turning `enabled` ON is what stamps the owner's “consent signed by all employees” across the
    roster: every employee with NO consent record gets status='signed', a real timestamp, and
    source='assumed_on_enable'. An employee already recorded as 'declined' is deliberately never
    overwritten — a refusal has to survive the switch being toggled off and on again."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    client = sb()
    before, available = _face.get_tenant_face_config(org_id, client)
    if not available:
        raise HTTPException(400, "Face-recognition settings aren't set up on this tenant yet (migration 420).")
    upd, turning_on = {}, False
    if "enabled" in body:
        want = bool(body["enabled"])
        upd["face_recognition_enabled"] = want
        turning_on = want and not before.get("enabled")
        if turning_on:
            upd["face_recognition_enabled_at"] = datetime.now(timezone.utc).isoformat()
            upd["face_recognition_enabled_by"] = ((mgr.get("email") or "").strip() or None)
    if "default_for_employees" in body:
        upd["face_recognition_default_for_employees"] = bool(body["default_for_employees"])
    if not upd:
        raise HTTPException(400, "no valid fields to update")
    try:
        client.table("tenants").update(upd).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't save the setting — is migration 420 applied?")
    # Best-effort by design: the switch is already flipped, and a stamping failure must not undo that
    # or 500 the request — it comes back as consent_stamped=None so the panel can say so out loud.
    stamped = _face.stamp_assumed_consent_for_all(org_id, client, who=mgr.get("email")) if turning_on else None
    after, _ = _face.get_tenant_face_config(org_id, client)
    return {"ok": True, "tenant": after, "consent_stamped": stamped}


@router.put("/employees/{emp_id}/face-config")
def set_employee_face_config(emp_id: str, body: dict, authorization: str = Header(default=""),
                             org_id: str = ORG_ID):
    """Per-employee ASSIGNMENT + consent record (“it should be assigned per employee”). Isolated from
    the generic PATCH /employees/{emp_id} for exactly the reason /lunch-config is: a tenant that hasn't
    run migration 420 can then never have an unrelated name/pay_rate save fail because of it.

    Body: {enabled: bool|null, consent: 'signed'|'declined'|null}. `enabled: null` = inherit the tenant
    default. `consent: null` clears the record back to “nothing recorded”. A 'declined' consent turns
    face recognition off for that person regardless of the assignment (face_recognition.py precedence)."""
    row = {}
    if "enabled" in body:
        row["face_recognition_enabled"] = None if body["enabled"] is None else bool(body["enabled"])
    if "consent" in body:
        c = body["consent"]
        if c is None:
            row.update({"face_consent_status": None, "face_consent_at": None, "face_consent_source": None})
        else:
            c = str(c).strip().lower()
            if c not in _face.CONSENT_STATUSES:
                raise HTTPException(400, f"consent must be one of {list(_face.CONSENT_STATUSES)} or null")
            who = (_who_for_log(authorization, org_id) or {}).get("email")
            src = _face.CONSENT_SOURCE_MANUAL if c == _face.CONSENT_SIGNED else _face.CONSENT_DECLINED
            # `consent_at` lets an admin record the date the employee ACTUALLY signed, rather than the
            # date somebody typed it in. This matters more than it looks: back-filling a real paper
            # release with today's timestamp would make the record show consent obtained AFTER the
            # template was collected — manufacturing the appearance of the exact 15(b) violation the
            # paperwork disproves. A future date is refused for the same reason, from the other side.
            _at = body.get("consent_at")
            if _at:
                try:
                    _parsed = datetime.fromisoformat(str(_at).replace("Z", "+00:00"))
                    if _parsed.tzinfo is None:
                        _parsed = _parsed.replace(tzinfo=timezone.utc)
                except Exception:
                    raise HTTPException(400, "consent_at must be a date or timestamp (e.g. 2026-07-01)")
                if _parsed > datetime.now(timezone.utc):
                    raise HTTPException(400, "consent_at cannot be in the future - consent has to "
                                             "precede the collection it authorizes")
                _when = _parsed.isoformat()
            else:
                _when = datetime.now(timezone.utc).isoformat()
            row.update({"face_consent_status": c,
                        "face_consent_at": _when,
                        "face_consent_source": (f"{src}:{who}"[:120] if who else src)})
    if not row:
        raise HTTPException(400, "no valid fields to update")
    try:
        r = sb().table("employees").update(row).eq("id", emp_id).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't save the setting — is migration 420 applied?")
    if not r.data:
        raise HTTPException(404, "employee not found")
    saved = r.data[0]
    return {"ok": True, "employee_id": saved.get("employee_id"),
            "face_recognition_enabled": saved.get("face_recognition_enabled"),
            "face_consent_status": saved.get("face_consent_status"),
            "face_consent_at": saved.get("face_consent_at"),
            "face_consent_source": saved.get("face_consent_source")}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# FACE-DESCRIPTOR RETENTION SCHEDULE + DELETION JOB (owner decision 2026-08-09, migration 422) —
# closes security-plan Phase 9.2. See docs/BIOMETRIC_RETENTION_POLICY.md (the written policy) and
# face_retention.py (the rule + degrade in full). "whichever is first" of: 90 days (tenant-configurable,
# ceilinged at 1095) after termination_date, or 1095 days (statutory, NEVER configurable) since the
# employee's last interaction with their own descriptor — plus an immediate employee-request path and
# an opt-in "purge everything while the tenant's face recognition is OFF" tenant setting.
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/timeclock/face-retention/config")
def get_face_retention_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Any signed-in caller may read (same posture as the other face-recognition config GETs — it
    exposes no biometric data, only the tenant's retention settings)."""
    cfg, available = _fret.get_tenant_retention_config(org_id, sb())
    return {"available": available, "tenant": cfg,
            "retention_days_default": _fret.FACE_RETENTION_DAYS_DEFAULT,
            "retention_days_max": _fret.STATUTORY_BACKSTOP_DAYS,
            "statutory_backstop_days": _fret.STATUTORY_BACKSTOP_DAYS}


@router.put("/timeclock/face-retention/config")
def set_face_retention_config(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager/admin only (same gate as PUT /timeclock/face-config — a tenant-wide compliance setting,
    not a single-row edit). Body: {retention_days?, purge_on_disable?}. `retention_days` is clamped to
    [1, 1095] server-side — RULE TWO's config table can never be pushed past the statutory ceiling.

    Flipping `purge_on_disable` ON while the tenant's face recognition is ALREADY off (or turning face
    recognition off while `purge_on_disable` is already on, via PUT /timeclock/face-config) fires the
    purge IMMEDIATELY — the owner's "opts to purge" is an action, not merely a future-sweep setting."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    client = sb()
    before, available = _fret.get_tenant_retention_config(org_id, client)
    if not available:
        raise HTTPException(400, "Face-retention settings aren't set up on this tenant yet (migration 422).")
    upd = {}
    if "retention_days" in body:
        upd["face_retention_days"] = _fret.clamp_retention_days(body["retention_days"])
    if "purge_on_disable" in body:
        upd["face_recognition_purge_on_disable"] = bool(body["purge_on_disable"])
    if not upd:
        raise HTTPException(400, "no valid fields to update")
    try:
        client.table("tenants").update(upd).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't save the setting — is migration 422 applied?")
    after, _ = _fret.get_tenant_retention_config(org_id, client)
    purge_result = None
    if after.get("purge_on_disable") and after.get("face_recognition_enabled") is False:
        purge_result = _fret.destroy(org_id, client, dry_run=False, destroyed_by=(mgr.get("email") or "system"))
    return {"ok": True, "tenant": after, "purge_result": purge_result}


@router.post("/timeclock/face-retention/run")
def run_face_retention_now(body: dict = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager-triggered run for the caller's own tenant. `dry_run` defaults TRUE — report only,
    nothing destroyed (matches this codebase's dry-run-before-apply convention, e.g.
    POST /hr/onboarding/reconcile). Pass {"dry_run": false} to actually destroy what's due."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    dry_run = (body or {}).get("dry_run", True) is not False
    client = sb()
    computed = _fret.compute_due(org_id, client)
    return _fret.destroy(org_id, client, computed=computed, dry_run=dry_run,
                          destroyed_by=(mgr.get("email") or "system"))


@router.post("/timeclock/face-retention/run-due")
def face_retention_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint (guarded by NOTIFY_RUN_SECRET, same convention as
    /timeclock/force-clockout/run-due and /google-reviews/sweep/run-due) — runs the REAL (non-dry-run)
    destruction sweep across EVERY tenant. Schedule daily (see docs/handoffs/people.md OPERATOR
    ACTIONS — an operator must add the pg_cron schedule; this endpoint is inert until something calls
    it). Idempotent: a descriptor is destroyed at most once (the row is gone after)."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    try:
        orgs = sorted({r.get("org_id") for r in
                       (client.table("tenants").select("org_id").execute().data or [])
                       if r.get("org_id")})
    except Exception:
        orgs = []
    results = []
    for oid in orgs:
        computed = _fret.compute_due(oid, client)
        r = _fret.destroy(oid, client, computed=computed, dry_run=False, destroyed_by="system:pg_cron")
        results.append({"org_id": oid, **{k: v for k, v in r.items() if k != "items"},
                        "employees_affected": len(r.get("items") or [])})
    return {"orgs_processed": len(orgs), "results": results}


@router.get("/timeclock/face-retention/log")
def get_face_retention_log(authorization: str = Header(default=""), org_id: str = ORG_ID, limit: int = 100):
    """The evidence view for the admin panel — org-scoped, most recent first."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    return {"rows": _fret.recent_log(org_id, sb(), limit=limit)}


@router.post("/employees/{emp_id}/face-retention/request-deletion")
def request_face_deletion(emp_id: str, body: dict = None, authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    """BIPA gives an employee the right to demand destruction of their biometric data — this is that
    path: immediate, single-employee, logged with trigger='employee_request'. `emp_id` is the internal
    numeric id (matches PATCH /employees/{id}). Body: {note?} — record how/when the request was made;
    it becomes the audit row's `notes`."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    client = sb()
    row = (client.table("employees").select("employee_id,name").eq("id", emp_id).eq("org_id", org_id)
           .limit(1).execute().data or [None])[0]
    if not row:
        raise HTTPException(404, "employee not found")
    note = ((body or {}).get("note") or "").strip()
    return _fret.destroy_one_employee_request(
        org_id, row.get("employee_id"), row.get("name"), client,
        destroyed_by=(mgr.get("email") or "system"), note=(note or None))


# ════════════════════════════════════════════════════════════════════════════════════════════════
# LUNCH-BREAK AUTO-DEDUCTION CONFIG (owner directive 2026-07-27, Deliverable 3): a tenant-wide default
# {enabled, minutes, min_shift_hours} + a per-employee override {enabled, minutes} — see
# lunch_deduction.py for the full design (guard, precedence, degrade). RULE TWO: config table + admin
# UI, universal for every tenant, nothing hard-coded.
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/timeclock/lunch-config")
def get_lunch_config_endpoint(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The tenant's lunch-deduction default + roster of per-employee overrides, for the Time Clock
    page's settings panel and the HR Employees & Pay tab. Any signed-in caller may read (matches
    GET /timeclock/config's posture). `available=False` (owner's stated default shown for reference
    only) whenever migration 418 hasn't run — see lunch_deduction.py DEGRADE."""
    tenant_cfg, overrides, available = _lunch_get_config(org_id, sb())
    return {"available": available, "tenant": tenant_cfg,
            "employee_overrides": [{"employee_id": eid, **v} for eid, v in overrides.items()]}


@router.put("/timeclock/lunch-config")
def set_lunch_config_endpoint(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager/admin only (matches PUT /timeoff-conflict-mode's posture — a tenant-wide toggle, not a
    single row edit). Body: {enabled?, minutes?, min_shift_hours?} — only provided keys change."""
    mgr = _require_manager(authorization, org_id)
    org_id = mgr.get("org_id") or org_id
    before, _ = _lunch_get_tenant_config(org_id, sb())
    upd = {}
    if "enabled" in body:
        upd["lunch_deduction_enabled"] = bool(body["enabled"])
    if "minutes" in body:
        try:
            upd["lunch_deduction_minutes"] = max(0, int(body["minutes"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "minutes must be a non-negative integer")
    if "min_shift_hours" in body:
        try:
            upd["lunch_deduction_min_shift_hours"] = max(0.0, float(body["min_shift_hours"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "min_shift_hours must be a non-negative number")
    if not upd:
        raise HTTPException(400, "no valid fields to update")
    try:
        sb().table("tenants").update(upd).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't save the setting — is migration 418 applied?")
    who = _who_for_log(authorization, org_id)
    field_map = {"lunch_deduction_enabled": "enabled", "lunch_deduction_minutes": "minutes",
                 "lunch_deduction_min_shift_hours": "min_shift_hours"}
    for col, key in field_map.items():
        if col in upd and str(before.get(key)) != str(upd[col]):
            _log_payroll_change(org_id, field=col, entry_point="lunch_deduction_config",
                                 before=before.get(key), after=upd[col], source_table="tenants",
                                 who=who, reason="tenant default")
    after, _ = _lunch_get_tenant_config(org_id, sb())
    return {"ok": True, "tenant": after}


@router.put("/employees/{emp_id}/lunch-config")
def set_employee_lunch_config(emp_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-employee lunch-deduction override. Isolated from the generic PATCH /employees/{emp_id}
    (EMP_FIELDS) so a tenant that hasn't run migration 418 yet can NEVER 500 an unrelated name/pay_rate/
    home_store edit — this endpoint's own failure is caught and reported on its own. Same permission
    posture as pay_rate edits on that same endpoint (org-scoped only, no extra manager gate — pay is
    already editable from the same HR page this control lives on). Body: {enabled: bool|null, minutes:
    int|null} — null explicitly clears the override back to "inherit the tenant default"."""
    row = {}
    if "enabled" in body:
        row["lunch_deduction_enabled"] = None if body["enabled"] is None else bool(body["enabled"])
    if "minutes" in body:
        if body["minutes"] is None:
            row["lunch_deduction_minutes"] = None
        else:
            try:
                row["lunch_deduction_minutes"] = max(0, int(body["minutes"]))
            except (TypeError, ValueError):
                raise HTTPException(400, "minutes must be a non-negative integer or null")
    if not row:
        raise HTTPException(400, "no valid fields to update")
    try:
        _before_raw = (sb().table("employees").select("employee_id,name,lunch_deduction_enabled,lunch_deduction_minutes")
                       .eq("id", emp_id).eq("org_id", org_id).limit(1).execute().data or [None])[0]
        # Explicit copy BEFORE the update — a real PostgREST round-trip always returns a fresh dict, but
        # a test double (or any future client implementation) that aliases select() results by
        # reference would otherwise have this "before" snapshot silently mutated by the .update() call
        # below, making every diff compare a row to itself (the exact class of bug already caught once
        # in update_shift/save_week_as_template — see docs/handoffs/people.md).
        before = dict(_before_raw) if _before_raw is not None else None
        r = sb().table("employees").update(row).eq("id", emp_id).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(400, "Couldn't save the setting — is migration 418 applied?")
    if not r.data:
        raise HTTPException(404, "employee not found")
    saved = r.data[0]
    if before is not None:
        who = _who_for_log(authorization, org_id)
        for col in ("lunch_deduction_enabled", "lunch_deduction_minutes"):
            if col in row and str(before.get(col)) != str(saved.get(col)):
                _log_payroll_change(org_id, field=col, entry_point="lunch_deduction_config",
                                     employee_id=before.get("employee_id"), employee_name=before.get("name"),
                                     before=before.get(col), after=saved.get(col), source_table="employees",
                                     source_id=emp_id, who=who, reason="per-employee override")
    return {"ok": True, "employee_id": saved.get("employee_id"),
            "lunch_deduction_enabled": saved.get("lunch_deduction_enabled"),
            "lunch_deduction_minutes": saved.get("lunch_deduction_minutes")}


# ── face recognition (face-api.js 128-float descriptors) ──────────────────────────────────────
@router.get("/timeclock/face")
def get_face(authorization: str = Header(default=""), action: str = "", org_id: str = ORG_ID):
    """Registration status (and the descriptor itself when action=descriptor, for verify) for the
    SIGNED-IN employee — identity comes from the auth token."""
    org_id, employee_id = _caller_identity(authorization)
    # Migration 420: while face recognition is off for this employee the stored descriptor is NOT
    # handed out (biometric data leaves the table only for a purpose the feature is switched on for).
    # The registration state itself is still reported truthfully, so the admin panel can show that a
    # template exists and re-enabling won't need a re-enrollment.
    gate = _face_gate(org_id, employee_id)
    if action == "descriptor" and not gate["enabled"]:
        raise HTTPException(403, _FACE_OFF_MSG)
    rows = (sb().table("face_descriptors").select("*").eq("org_id", org_id)
            .eq("employee_id", employee_id).limit(1).execute().data) or []
    if not rows:
        return {"registered": False, **_face_flags(gate)}
    if action == "descriptor":
        return {"registered": True, "descriptor": rows[0].get("descriptor"), **_face_flags(gate)}
    return {"registered": True, "register_count": rows[0].get("register_count"), **_face_flags(gate)}


def _face_consent_ok(org_id: str, employee_id: str):
    """(ok, message). A face template may be stored only for an employee whose written release is on
    file and DATED AT OR BEFORE this moment — "we have consent now" is not evidence that consent
    preceded collection, and preceding is the whole of what 15(b) requires.

    Fails CLOSED on a read error or an un-run migration 420: if we cannot prove consent exists, we do
    not collect. That is the opposite of this codebase's usual degrade-open posture and it is
    deliberate — see the note at the call site."""
    try:
        rows = (sb().table("employees")
                .select("face_consent_status,face_consent_at")
                .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
    except Exception:
        return (False, "Face enrollment is unavailable right now because your consent record could "
                       "not be read. Nothing was saved - please try again in a moment.")
    row = rows[0] if rows else {}
    if row.get("face_consent_status") != _face.CONSENT_SIGNED:
        return (False, "A signed biometric consent form must be on file before a face template can be "
                       "stored. Ask your manager to record your written release first.")
    at = row.get("face_consent_at")
    if not at:
        return (False, "Your consent record has no date on file, so it cannot show that consent came "
                       "before enrollment. Ask your manager to record the date you signed.")
    try:
        signed_at = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        if signed_at.tzinfo is None:
            signed_at = signed_at.replace(tzinfo=timezone.utc)
        if signed_at > datetime.now(timezone.utc):
            return (False, "Your consent record is dated in the future, so it cannot show that consent "
                           "came before enrollment. Ask your manager to correct the date.")
    except Exception:
        return (False, "Your consent record has an unreadable date. Ask your manager to re-record it.")
    return (True, "")


@router.post("/timeclock/face")
def save_face(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Save (or re-register) an averaged 128-float descriptor for the SIGNED-IN employee — identity
    comes from the auth token, so you can only enroll your own face."""
    org_id, employee_id = _caller_identity(authorization)
    # Migration 420 / owner directive 2026-08-09: NO new biometric template is captured while the
    # feature is off for this employee. Server-side, so a stale kiosk bundle still running the old
    # enroll-on-first-clock-in flow cannot create one after the switch was thrown.
    gate = _face_gate(org_id, employee_id)
    if not gate["enabled"]:
        raise HTTPException(403, _FACE_OFF_MSG)
    # CONSENT BEFORE COLLECTION (migration 424, BIPA 740 ILCS 14/15(b)). The feature being ON for this
    # employee is NOT consent. Until now the only consent-shaped check was `declined` (via
    # face_recognition.resolve_employee_face); an employee with NO consent record at all enrolled
    # freely — which is how all 77 descriptors on file as of 2026-08-09 came to have none.
    #
    # This is the readable half of the guarantee; migration 424's trigger is the half that actually
    # holds, because it binds every write path rather than this one endpoint. Both fail CLOSED: the
    # cost of a wrong refusal is one retry at a kiosk, the cost of a wrong acceptance is a per-person
    # statutory exposure that deleting the row afterwards does not undo.
    _consent_ok, _consent_why = _face_consent_ok(org_id, employee_id)
    if not _consent_ok:
        raise HTTPException(403, _consent_why)
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
    gate reconciles). Returns ({id-strings}, name).

    Gate-1 N1 fix (2026-07-27, MEDIUM, same class as payroll_identity.business_id_alias_map): this
    employee's own numeric id can collide with a DIFFERENT employee's own all-digit business
    employee_id (e.g. this employee's numeric id is 42, while some OTHER employee's business
    employee_id literally is "42"). Adding "42" as a variant unconditionally would pull that OTHER
    employee's shifts/timelog into THIS employee's result set. Guarded: the numeric id is added only
    when no OTHER employee in the org claims it as their own business employee_id."""
    ids = {str(employee_id)}
    name = None
    try:
        er = (sb().table("employees").select("id,name").eq("org_id", org_id)
              .eq("employee_id", employee_id).limit(1).execute().data) or []
        if er:
            numeric_s = str(er[0]["id"]) if er[0].get("id") is not None else None
            if numeric_s and numeric_s != str(employee_id):
                collision = (sb().table("employees").select("id").eq("org_id", org_id)
                             .eq("employee_id", numeric_s).limit(1).execute().data) or []
                if not collision:
                    ids.add(numeric_s)
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


def _do_force_clockout(org_id=None, grace_min=FORCE_CLOCKOUT_GRACE_MIN, actor=None):
    """Close every open punch whose scheduled shift end (+ grace) has passed, stamping the clock-out
    at the SCHEDULED END (paid hours = scheduled). Punches with no scheduled shift are left open.

    `actor` = a manager dict (from `_require_manager`) when a DM clicked "run now", or None for the
    unattended pg_cron sweep — either way every punch this closes stamps hours away from a raw
    clock-in/out diff, so it's logged to the Payroll Change Log (Deliverable 4, 2026-07-27) with the
    appropriate entry_point so the two triggers stay distinguishable on that page."""
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
            try:
                _log_payroll_change(
                    oid, field="clock_out", entry_point=("force_clockout_manual" if actor else "force_clockout_cron"),
                    employee_id=p.get("employee_id"), employee_name=p.get("employee_name"),
                    store_code=p.get("store_code"), work_date=p.get("work_date"),
                    before=None, after=f"{end_dt.isoformat()} ({hours}h, auto at scheduled end)",
                    source_table="timelog", source_id=p.get("id"), who=actor)
            except Exception:
                pass
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
    return _do_force_clockout(org_id=(mgr.get("org_id") or org_id), actor=mgr)


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
    eids = scope_emp_ids(authorization, org_id, since=start or None, until=end or None)   # None = unrestricted
    if eids is not None:
        rows = [r for r in rows if str(r.get("employee_id")) in eids]
    return rows


@router.post("/manual-hours")
def add_manual_hours(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
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
    saved = r.data[0] if r.data else row
    # Deliverable 4 (Payroll Change Log): a manual-hours adjustment IS a manual change to payroll
    # hours by definition — log it (best-effort, never blocks the write above).
    try:
        who = _who_for_log(authorization, org_id)
        _log_payroll_change(org_id, field="manual_hours", entry_point="manual_hours_add",
                             employee_id=employee_id, work_date=row["work_date"],
                             before=None, after=row["hours"], source_table="manual_hours",
                             source_id=saved.get("id"), who=who, reason=reason)
    except Exception:
        pass
    return saved


@router.delete("/manual-hours/{mid}")
def delete_manual_hours(mid: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    before = (sb().table("manual_hours").select("*").eq("org_id", org_id).eq("id", mid)
              .limit(1).execute().data or [{}])[0]
    sb().table("manual_hours").delete().eq("org_id", org_id).eq("id", mid).execute()
    try:
        who = _who_for_log(authorization, org_id)
        _log_payroll_change(org_id, field="manual_hours", entry_point="manual_hours_delete",
                             employee_id=before.get("employee_id"), work_date=before.get("work_date"),
                             before=before.get("hours"), after=None, source_table="manual_hours",
                             source_id=mid, who=who, reason=before.get("reason"))
    except Exception:
        pass
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
    """store_codes in a market — resolved off the CANONICAL union of storeops.stores.market AND
    commcalc.store_mapping.market (app.core.scope.market_store_codes). Was: storeops.stores.market
    ALONE, which made any market that lives — or is only spelled — in store_mapping resolve to the
    EMPTY set (the "3 markets selected, sees nothing" bug: GET /storeops/markets already offered the
    union for the picker, so the picker could offer a market this resolver could not bind). Same
    signature/contract, org-tree-independent."""
    return _cscope.market_store_codes(get_supabase(), org_id, market)


def _login_extra_codes(au: dict, org_id: str) -> set:
    """store_codes implied by an app_user's market + pinned store(s) — the org-tree-independent span,
    so a market/store manager scopes correctly even before the org units/managers are wired.
    Delegates to app.core.scope.login_grant_codes — same comma-split-markets + store_code +
    store_codes contract, one table read total instead of one per market, and markets resolve
    through the canonical union (see _market_store_codes)."""
    return _cscope.login_grant_codes(get_supabase(), org_id, au)


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


def _role_permissions(org_id: str, role: str) -> dict:
    """Full roles.permissions jsonb for a role name (scope, scheduling_reach, page/report
    overrides, …). Empty dict on missing role / read failure — every consumer here treats a blank
    dict as the safe default (app.core.scope.scheduling_reach -> 'org')."""
    if not role:
        return {}
    try:
        rr = sb().table("roles").select("permissions").eq("org_id", org_id).eq("name", role).limit(1).execute().data or []
        return (rr[0].get("permissions") or {}) if rr else {}
    except Exception:
        return {}


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
    may see — so rows whose store field is EITHER a code or an address still match. caller_scope()
    is UNCHANGED; only the widening (code -> code+address) now runs off app.core.scope's cached,
    unioned market index instead of a fresh storeops.stores scan per request — same contract, and it
    additionally picks up the address for a store_code that exists only in commcalc.store_mapping."""
    codes = caller_scope(authorization, org_id)
    if codes is None:
        return None
    return _cscope.widen_codes_to_keys(get_supabase(), org_id, codes)


def in_keyset(keyset, *vals) -> bool:
    """True when unrestricted (keyset None) or any of vals matches an allowed store key."""
    if keyset is None:
        return True
    return any(str(v or "").strip().upper() in keyset for v in vals)


def _emp_ids_window_from_rows(rows, start_key, end_key=None):
    """Derive a bounded (since, until) date window for scope_emp_ids' 'worked at' resolution from
    ROWS THE CALLER ALREADY FETCHED — for the call sites that take no start/end query params of
    their own (time-off, shift-swaps). Falls back to a single-day window anchored on today when
    there's nothing to bound from; that's safe because an empty rows list stays empty after the
    eids filter regardless — this only exists to keep reporting_employee_ids from scanning full
    shift/timelog history on every call."""
    end_key = end_key or start_key
    dates = [str(r.get(start_key))[:10] for r in (rows or []) if r.get(start_key)]
    dates += [str(r.get(end_key))[:10] for r in (rows or []) if r.get(end_key)]
    if not dates:
        today = _date.today().isoformat()
        return today, today
    return min(dates), max(dates)


def scope_emp_ids(authorization: str, org_id: str = ORG_ID, *, since: str = None, until: str = None):
    """employee_ids in the caller's span (None = UNRESTRICTED). For employee-keyed tables
    (time-off, swaps, manual-hours, timeclock) that carry no store column — resolves by HOME STORE
    **union WHERE THEY ACTUALLY WORKED** (a non-deleted shift or a time-log at a store inside the
    span), via app.core.scope.reporting_employee_ids. This is the "employees move around" fix: a
    borrowed rep working a manager's store used to be invisible to that manager on these surfaces
    (home_store-only resolution). It WIDENS visibility versus before — see docs/handoffs/people.md
    for the before/after proof. `since`/`until` bound the "worked at" half so this never turns into
    a full shifts/timelog history scan; every call site passes a window derived from its own query
    (its own start/end params, or _emp_ids_window_from_rows over what it already fetched)."""
    ks = scope_keyset(authorization, org_id)
    if ks is None:
        return None
    return _cscope.reporting_employee_ids(get_supabase(), org_id, ks, since=since, until=until)


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
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# DM ACCESSORY-TARGET ATTRIBUTION — migration band 400-499 (no new table; read + rollup only), owner
# directive 2026-08-04 (ledger Q7 answer): "my team accessory numbers are the accessory target for the
# [stores] calculated by the schedule and for the dm it is the total of employees which run under him
# for the stores they worked in, if an employee works under 2 dms then their target for that store
# goes under the dm for that market."
#
# The SCHEDULE-DRIVEN per-rep target is mod-commission's Daily Targets engine
# (`commcalc/targets_engine.py`) — NOT reimplemented here (money-adjacent, cross-file-owned). Per the
# 2026-08-04 "plan for a bigger tenant" directive this section makes exactly ONE internal HTTP call —
# `GET /commcalc/targets/{period}/summary` (the org's existing BULK/all-stores shape) — per rollup,
# instead of one call per (employee, store) pair; the one number that call doesn't carry (a rep's
# SHARE of a store's target) is computed locally, off this module's OWN `storeops.shifts` schedule
# data, by a small pure function that mirrors mod-commission's own proration ratio (see
# `target_attribution.py`'s module docstring for the full "why one call, why local" reasoning).
#
# READ-ONLY / NOT MONEY: nothing here writes a payout, a target, or a schedule row. It only re-groups
# numbers mod-commission already computed (the store's target $ + each rep's achieved $) and this
# module's own schedule already describes, by market → DM. Achieved-$ is read VERBATIM off that ONE
# bulk payload — never recomputed here.
#
# SPAN-SCOPING (Gate-1 rework 2026-08-04): a market-scope caller (District Manager) sees ONLY the DM
# card(s) for market(s) granted to them — see `target_attribution.py`'s span-scoping section for the
# full reasoning (why the FULL org-wide attribution is still computed once, then redacted for
# presentation, rather than narrowing the underlying data fetch).
# ═══════════════════════════════════════════════════════════════════════════════════════════════

_DM_TARGET_CACHE_TTL_S = 120.0
_dm_target_cache: dict = {}   # (org_id, period) -> (fetched_at, summary_json | None)


def _norm_upper(v) -> str:
    return str(v or "").strip().upper()


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _requote(period: str) -> str:
    """URL-safe period path segment ('August 2026' has a space)."""
    from urllib.parse import quote
    return quote(str(period), safe="")


def _dm_targets_summary_bulk(org_id: str, period: str) -> dict:
    """THE one bulk internal-HTTP call this whole package makes: mod-commission's OWN all-stores
    endpoint (`GET /commcalc/targets/{period}/summary?include_untargeted=true`), same
    `INTERNAL_API_BASE_URL` convention the PTO/payroll-tax packages established. TTL-cached per
    (org_id, period) — belt-and-braces on top of already being a single call. Degrades to `{}` on any
    failure (never raises) — the caller turns an empty summary into an honest `warnings` entry, not a
    500 for the whole rollup."""
    key = (str(org_id), str(period))
    now = time.time()
    hit = _dm_target_cache.get(key)
    if hit and (now - hit[0]) < _DM_TARGET_CACHE_TTL_S:
        return hit[1] or {}
    url = f"{PTO_INTERNAL_API_BASE}/api/v1/commcalc/targets/{_requote(period)}/summary"
    data = {}
    try:
        resp = requests.get(url, params={"org_id": org_id, "include_untargeted": "true"}, timeout=30)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as e:
        print(f"WARN _dm_targets_summary_bulk failed ({org_id}/{period}): {e}")
        data = None
    _dm_target_cache[key] = (now, data)
    return data or {}


def _dm_target_rows(org_id: str, period: str, *, max_pairs: int = 20000) -> tuple:
    """Build the (employee, store) attribution rows for the whole org this period: every distinct
    pair with at least one positive-hour, non-deleted shift ("the stores they worked in") this
    module's OWN `storeops.shifts` describes, priced off the ONE bulk `/targets/.../summary` call
    (store monthly $ + each rep's own achieved $) plus a LOCAL hours-share computation (no HTTP per
    pair — see the section banner). Labeled with the store's CANONICAL market (app.core.scope's
    unioned market index — the same source the scope-wiring package used, so a store known only to
    `commcalc.store_mapping` still resolves).

    Returns (rows, warnings, truncated). `max_pairs` is a safety backstop only (not a performance
    cap — the bulk call is O(1) regardless of pair count); it is generous enough that no real tenant
    should ever hit it, and a hit is reported via `truncated`, never silent."""
    client = sb()
    ym = _dmta.parse_period_to_ym(period)
    start, end = pto_month_bounds(ym)
    try:
        shifts = (client.table("shifts")
                  .select("employee_id,employee_name,store_code,scheduled_hours,shift_date,is_deleted")
                  .eq("org_id", org_id).gte("shift_date", start.isoformat())
                  .lte("shift_date", end.isoformat()).limit(50000).execute().data) or []
    except Exception as e:
        print(f"WARN _dm_target_rows shifts read failed: {e}")
        shifts = []
    pairs = _dmta.worked_pairs_from_shifts(shifts)
    truncated = len(pairs) > max_pairs
    if truncated:
        pairs = pairs[:max_pairs]

    idx = _cscope.market_index(get_supabase(), org_id)
    market_by_code, address_by_code = {}, {}
    for s in idx.get("stores") or []:
        code = _norm_upper(s.get("store_code"))
        if code:
            market_by_code[code] = s.get("market") or ""
            address_by_code[code] = s.get("address") or ""

    summary = _dm_targets_summary_bulk(org_id, period)
    warnings = []
    if not summary:
        warnings.append({"employee_name": "", "store_code": "",
                         "note": "bulk targets summary unavailable this load — every row shows $0 "
                                 "until it can be reached again"})
    store_target_by_code, achieved_by_pair = {}, {}
    for s in (summary.get("stores") or []):
        code = _norm_upper(s.get("store_code"))
        if not code:
            continue
        store_target_by_code[code] = _safe_float((s.get("categories") or {}).get("accessories", {}).get("monthly"))
        for r in (s.get("reps") or []):
            rn = _norm_upper(r.get("rep"))
            if rn:
                achieved_by_pair[(code, rn)] = _safe_float(r.get("accessories"))

    today = _date.today()
    rows = []
    for pr in pairs:
        code = pr["store_code"]
        rep_up = _norm_upper(pr["employee_name"])
        share = _dmta.rep_share_from_shifts(shifts, code, pr["employee_name"], today, end)
        target = round(store_target_by_code.get(code, 0.0) * share, 2)
        achieved = achieved_by_pair.get((code, rep_up), 0.0)
        rows.append({
            "employee_name": pr["employee_name"], "employee_id": pr.get("employee_id"),
            "store_code": code, "address": address_by_code.get(code, ""),
            "market": market_by_code.get(code, ""), "target": target, "achieved": achieved,
            "rep_share": round(share, 4), "ok": bool(summary),
        })
    return rows, warnings, truncated


def _dm_roster(org_id: str) -> dict:
    """{dm_key: {'label','markets','role'}} — every app_user whose role's reporting scope is
    'market' (the shipped DM convention — "set the DM role's reporting grants to the 3 markets",
    ledger Q9/11) with at least one market granted."""
    client = sb()
    try:
        roles = client.table("roles").select("name,permissions").eq("org_id", org_id).execute().data or []
    except Exception:
        roles = []
    scope_by_name = {(r.get("name") or ""): ((r.get("permissions") or {}).get("scope") or "all") for r in roles}
    try:
        emps = client.table("employees").select("employee_id,name").eq("org_id", org_id).execute().data or []
    except Exception:
        emps = []
    name_by_id = {e.get("employee_id"): e.get("name") for e in emps if e.get("employee_id")}
    try:
        aus = (client.table("app_users").select("id,email,full_name,employee_id,role,market")
               .eq("org_id", org_id).execute().data) or []
    except Exception:
        aus = []
    return _dmta.dm_roster_from_app_users(aus, scope_by_name, name_by_id)


@router.get("/dm-accessory-attribution/{period}")
def dm_accessory_attribution(period: str, authorization: str = Header(default=""),
                             dm_id: str = "", org_id: str = ORG_ID):
    """DM accessory-target ATTRIBUTION rollup (owner directive 2026-08-04, ledger Q7) — see the
    section banner above for the rule. Returns `by_dm` (one entry per DM the caller may see, even at
    $0), `unassigned` (rows whose store has no market or no DM grant — never silently dropped),
    `ambiguous_markets` (a market granted to >1 DM — a config collision, flagged not guessed at), and
    `cross_dm_employees` (the "verify a 2-DM split at a glance" view).

    SPAN-SCOPING (Gate-1 rework 2026-08-04): under RBAC, this endpoint reads the CALLER's own role
    scope via the same `_role_scope`/`_caller_app_user` machinery every other storeops read uses:
      - scope 'all' (admin / RBAC off / unresolvable caller — same "unrestricted" default the rest of
        this module uses)  -> every DM card, org-wide totals, full cross_dm_employees detail.
      - scope 'market' (a District Manager) -> `by_dm` narrowed to DM(s) whose granted market(s)
        intersect the caller's OWN granted market(s) (`app.core.scope`'s market-grant machinery,
        resolved via this endpoint's OWN `_dm_roster` — the same resolver every DM card already comes
        from, not a new one). `unassigned` / `ambiguous_markets` narrowed the same way. Grand totals
        recomputed over ONLY the caller's visible DM(s) (never the whole org's). `cross_dm_employees`
        keeps full detail for the caller's own DM(s) on a split row but reduces any OTHER dm on that
        row to a bare identity label (`redacted: true`, no rows, no total) — enough to explain the
        split exists without exposing that other DM's numbers or roster.
      - scope 'self' / 'store' -> 403 (not a manager-level report).

    `dm_id` optionally narrows `by_dm` further to one key — for a market-scope caller it can only ever
    select a key already inside their own visible set (never a way to reach another DM's card)."""
    au = None
    caller_scope_kind = "all"
    if _rbac_enabled(org_id):
        au = _caller_app_user(authorization, org_id)
        if au:
            caller_scope_kind = _role_scope(org_id, (au.get("role") or "").strip())
    if caller_scope_kind in ("self", "store"):
        raise HTTPException(403, "This report is not available to your role.")
    try:
        ym = _dmta.parse_period_to_ym(period)
    except ValueError as e:
        raise HTTPException(400, str(e))

    rows, warnings, truncated = _dm_target_rows(org_id, period)
    dm_markets = _dm_roster(org_id)
    attributed = _dmta.attribute_rows_to_dms(rows, dm_markets)
    cross_dm = _dmta.cross_dm_employees(attributed)

    by_dm = attributed["by_dm"]
    unassigned = attributed["unassigned"]
    ambiguous = attributed["ambiguous_markets"]
    total_target = attributed["total_target_all_rows"]
    total_achieved = attributed["total_achieved_all_rows"]
    pairs_considered = len(rows)

    if caller_scope_kind == "market":
        caller_key = str((au or {}).get("id") or "")
        caller_markets = set((dm_markets.get(caller_key) or {}).get("markets") or ())
        visible_keys = _dmta.visible_dm_keys_for_markets(dm_markets, caller_markets)
        by_dm = {k: v for k, v in by_dm.items() if k in visible_keys}
        unassigned = _dmta.visible_unassigned(unassigned, caller_markets)
        ambiguous = _dmta.visible_ambiguous_markets(ambiguous, caller_markets)
        cross_dm = _dmta.redact_cross_dm_employees(cross_dm, visible_keys)
        total_target = round(sum(d["total_target"] for d in by_dm.values()), 2)
        total_achieved = round(sum(d["total_achieved"] for d in by_dm.values()), 2)
        pairs_considered = sum(len(d["rows"]) for d in by_dm.values())
        folded_markets = {m.strip().lower() for m in caller_markets}
        market_by_store = {r["store_code"]: r["market"] for r in rows}
        warnings = [w for w in warnings
                   if not w.get("store_code")
                   or str(market_by_store.get(w["store_code"]) or "").strip().lower() in folded_markets]

    if dm_id:
        by_dm = {dm_id: by_dm[dm_id]} if dm_id in by_dm else {}

    return {"period": period, "period_ym": ym, "by_dm": by_dm,
            "unassigned": unassigned, "ambiguous_markets": ambiguous,
            "cross_dm_employees": cross_dm,
            "total_target_all_rows": total_target,
            "total_achieved_all_rows": total_achieved,
            "pairs_considered": pairs_considered, "truncated": truncated, "warnings": warnings,
            "caller_scope": caller_scope_kind}


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


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# DAILY SALARY OWED + CASH ADVANCES + ADDITIONAL PAYROLL (EEP package) — owner directive 2026-08-04,
# migration 419, cross-module spec docs/specs/envelope-expense-payout.md. Pure math lives in
# salary_owed.py (imported as `_owed` above; unit-tested in harness_salary_owed.py) — everything here
# is I/O: fetch shift/timelog/employee/ledger rows, call the engine, and push the SEPARATE
# 'Additional Payroll' system line via the SAME contract PTO/payroll-expenses/payroll-gross already use.
#
# MONEY DOCTRINE (owner rule, verbatim intent): salary paid in cash from the daily-closing envelope
# NEVER changes what payroll counts — GET /storeops/payroll / the 'payroll_gross' P&L line stay
# EXACTLY as they are today (this section only READS shifts/timelog, never writes them, and never
# touches payroll_gross_ledger/payroll_tax_ledger/payroll_expense_ledger). Cash payments recorded here
# are ADVANCES; only the EXCESS of cumulative cash paid over cumulative earned posts to the P&L, as its
# OWN 'additional_payroll' line — never folded into payroll_gross.
# ═══════════════════════════════════════════════════════════════════════════════════════════════

def _salary_owed_group_by_employee(rows):
    out = {}
    for r in rows or ():
        eid = r.get("employee_id")
        if eid:
            out.setdefault(eid, []).append(r)
    return out


def _salary_owed_lunch_by_emp_day(org_id, timelog_rows):
    """{(employee_id, work_date): deduct_hours} for APPLIED days only, off an already-fetched timelog
    set — ONE lunch-config fetch regardless of how many employees are in `timelog_rows` (mirrors
    payroll_actual_hours_detail's own reuse of compute_lunch_deduction_from_rows over rows it already
    has, never a second per-employee query). Degrades to {} pre-migration-418 / on any failure — never
    blocks the owed computation."""
    try:
        tenant_cfg, overrides, available = _lunch_get_config(org_id, sb())
        if not available:
            return {}
        result = _lunch_compute_from_rows(timelog_rows, tenant_cfg, overrides)
        return {(d["employee_id"], d["work_date"]): d["deduct_hours"] for d in result["days"] if d.get("applied")}
    except Exception:
        return {}


def _salary_owed_for_employees(emp_rows, lo_by_emp, hi: _date, shifts_by_emp: dict,
                                timelog_by_emp: dict, lunch_by_emp_day: dict, pp_settings: dict):
    """{employee_id: salary_owed.build_employee_salary_owed(...) result} for a set of employees whose
    shift/timelog rows have ALREADY been fetched + grouped by the caller. `lo_by_emp` is either a
    single `date` (same report-window start for every employee — GET /salary-owed) or a
    {employee_id: date} dict (a hire-date-aware per-employee lookback start — the Additional-Payroll
    gather)."""
    out = {}
    for emp in emp_rows:
        eid = emp.get("employee_id")
        if not eid:
            continue
        lo = lo_by_emp.get(eid) if isinstance(lo_by_emp, dict) else lo_by_emp
        if lo is None or lo > hi:
            continue
        is_inactive = emp.get("is_active") is False
        day_hours = _owed.daily_hours_for_employee(shifts_by_emp.get(eid, []), timelog_by_emp.get(eid, []), is_inactive)
        ded = {d: lunch_by_emp_day.get((eid, d), 0.0) for d in day_hours}
        day_hours = _owed.apply_lunch_deduction(day_hours, ded)
        out[eid] = _owed.build_employee_salary_owed(emp, pp_settings, lo, hi, day_hours)
    return out


@router.get("/salary-owed")
def get_salary_owed(start: str, end: str, store_code: str = "", employee_id: str = "",
                     authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-employee daily salary owed vs cash paid vs balance for [start, end] (both inclusive) — the
    Salary Advances page's data source. Hours basis REUSES /storeops/payroll's exact rules (see
    salary_owed.daily_hours_for_employee's docstring) — this can never diverge from what /payroll shows
    for the SAME employee/range, including its open-punch exclusion and shift-covered no-double-count
    guards. `store_code` narrows to shift/timelog activity actually AT that store (a floater's other-
    store hours don't count toward what's owed from THIS store's envelope, and cash paid is likewise
    narrowed to advances recorded from that store); `employee_id` narrows to one employee (an unknown
    id returns an empty list rather than a 500 — same convention as every other filtered GET here)."""
    try:
        d_lo, d_hi = _date.fromisoformat(str(start)[:10]), _date.fromisoformat(str(end)[:10])
    except ValueError:
        raise HTTPException(400, "start/end must be ISO dates (YYYY-MM-DD)")
    if d_lo > d_hi:
        raise HTTPException(400, "start must be on or before end")
    lo, hi = d_lo.isoformat(), (d_hi + timedelta(days=1)).isoformat()   # half-open bounds for the fetch

    eq = {"employee_id": employee_id} if employee_id else None
    employees = _employees_with_pay_fields(org_id, "id,name,employee_id,pay_rate,home_store,is_active", eq=eq)
    if not employees:
        return {"start": start, "end": end, "employees": []}
    emp_map = {e["employee_id"]: e for e in employees if e.get("employee_id")}

    shift_q = sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False) \
        .gte("shift_date", lo).lt("shift_date", hi)
    tl_q = sb().table("timelog").select("*").eq("org_id", org_id).gte("work_date", lo).lt("work_date", hi)
    if employee_id:
        shift_q = shift_q.eq("employee_id", employee_id)
        tl_q = tl_q.eq("employee_id", employee_id)
    if store_code:
        shift_q = shift_q.eq("store_code", store_code)
        tl_q = tl_q.eq("store_code", store_code)
    shifts = shift_q.execute().data or []
    timelog = tl_q.limit(20000).execute().data or []

    shifts_by_emp = _salary_owed_group_by_employee(shifts)
    timelog_by_emp = _salary_owed_group_by_employee(timelog)
    lunch_by_emp_day = _salary_owed_lunch_by_emp_day(org_id, timelog)

    # Employee set: whoever has activity in the (possibly store-filtered) window, UNION every salaried
    # employee with a usable pay_amount whose home_store matches (mirrors /payroll's
    # synthesize_zero_activity_rows treatment — a salaried market manager who never clocks in still
    # earns/owes a figure and must not silently vanish from this report).
    if employee_id:
        target_ids = {employee_id} if employee_id in emp_map else set()
    else:
        target_ids = set(shifts_by_emp) | set(timelog_by_emp)
        for e in employees:
            eid = e.get("employee_id")
            if not eid or eid in target_ids or "pay_basis" not in e:
                continue
            basis, amount = payroll_salary.resolve_pay_basis(e)
            if basis == "hourly" or amount is None or amount <= 0:
                continue
            if store_code and (e.get("home_store") or "").strip() != store_code:
                continue
            target_ids.add(eid)

    pp_settings = _tenant_pp_settings(org_id)
    owed_by_emp = _salary_owed_for_employees([emp_map[e] for e in target_ids if e in emp_map],
                                              d_lo, d_hi, shifts_by_emp, timelog_by_emp, lunch_by_emp_day, pp_settings)

    # cash paid IN THIS WINDOW (paid_date within [start,end]) — pairs with the windowed owed_total
    # above. Narrowed by store_code too when given (documented default — "what have I paid this
    # employee from THIS store's envelopes", not their company-wide cash total).
    cash_by_emp = {}
    try:
        adv_q = (sb().table("salary_advance_ledger").select("employee_id,amount")
                 .eq("org_id", org_id).gte("paid_date", start).lte("paid_date", end)
                 .in_("employee_id", list(target_ids) or ["__none__"]))
        if store_code:
            adv_q = adv_q.eq("store_code", store_code)
        for a in adv_q.execute().data or []:
            eid = a.get("employee_id")
            cash_by_emp[eid] = round(cash_by_emp.get(eid, 0.0) + float(a.get("amount") or 0), 2)
    except Exception:
        pass   # migration 419 not applied yet -> cash_paid_total stays 0 for every employee

    # store attribution (dominant store this window, home_store fallback) — informational + drives scope.
    store_hours: dict = {}
    for eid, rows_ in shifts_by_emp.items():
        for s in rows_:
            st = (s.get("store_code") or "").strip()
            if st:
                d = store_hours.setdefault(eid, {})
                d[st] = d.get(st, 0.0) + float(s.get("actual_hours") or 0) + float(s.get("scheduled_hours") or 0)
    for eid, rows_ in timelog_by_emp.items():
        for t in rows_:
            st = (t.get("store_code") or "").strip()
            if st:
                d = store_hours.setdefault(eid, {})
                d[st] = d.get(st, 0.0) + float(t.get("hours") or 0)

    out = []
    for eid, res in owed_by_emp.items():
        emp = emp_map.get(eid, {})
        sh = store_hours.get(eid)
        store = (max(sh.items(), key=lambda kv: kv[1])[0] if sh else (emp.get("home_store") or ""))
        cash_paid = cash_by_emp.get(eid, 0.0)
        out.append({
            "employee_id": eid, "name": emp.get("name") or eid, "store": store,
            "pay_basis": res["basis"], "days": res["days"], "owed_total": res["owed_total"],
            "cash_paid_total": cash_paid, "balance": round(res["owed_total"] - cash_paid, 2),
        })
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        out = [r for r in out if in_keyset(ks, r.get("store"))]
    return {"start": start, "end": end, "employees": sorted(out, key=lambda r: r["name"])}


def _additional_payroll_store_for(org_id, employee_id, period_end_iso, home_store):
    """The store attribution for an employee's Additional-Payroll excess this period: the store_code
    of their MOST RECENT salary_advance_ledger row on/before period_end (documented default — "the
    store where the advance was paid", per docs/specs/envelope-expense-payout.md), falling back to
    home_store when that row has no store_code, or to 'Unassigned' when even that is blank (never
    silently vanish — same convention as payroll_salary.allocate_across_stores)."""
    try:
        rows = (sb().table("salary_advance_ledger").select("store_code,paid_date")
                .eq("org_id", org_id).eq("employee_id", employee_id).lte("paid_date", period_end_iso)
                .order("paid_date", desc=True).limit(1).execute().data) or []
    except Exception:
        rows = []
    st = (rows[0].get("store_code") or "").strip() if rows else ""
    return st or (home_store or "").strip() or "Unassigned"


def _additional_payroll_gather(org_id, period):
    """Every employee with a cash advance recorded on/before this period's end, their cumulative
    'earned to date' (salary_owed basis, bounded lookback — see salary_owed.EARNED_LOOKBACK_DAYS) and
    cumulative 'cash paid to date' (unbounded — every advance ever recorded through period end), the
    excess (salary_owed.additional_payroll_excess), and the per-store rollup for the P&L push.
    Read-only; NEVER writes shifts/timelog/employees/payroll_gross_ledger — the only side effect any
    caller of this can ever cause is the additive 'additional_payroll' Store Expenses system line."""
    period_start, period_end = pto_month_bounds(period)
    try:
        adv_all = (sb().table("salary_advance_ledger").select("employee_id,amount,paid_date")
                   .eq("org_id", org_id).lte("paid_date", period_end.isoformat()).execute().data) or []
    except Exception:
        return {"employees": [], "stores": {}, "cells": [], "available": False}
    eids = sorted({a.get("employee_id") for a in adv_all if a.get("employee_id")})
    if not eids:
        return {"employees": [], "stores": {}, "cells": [], "available": True}

    employees = _employees_with_pay_fields(org_id, "id,name,employee_id,pay_rate,home_store,is_active")
    emp_map = {e["employee_id"]: e for e in employees if e.get("employee_id")}

    cash_to_date = {}
    for a in adv_all:
        eid = a.get("employee_id")
        cash_to_date[eid] = round(cash_to_date.get(eid, 0.0) + float(a.get("amount") or 0), 2)

    global_lo = _owed.earned_lookback_start(None, period_end)   # widest window any employee could need
    shifts = (sb().table("shifts").select("*").eq("org_id", org_id).eq("is_deleted", False)
              .in_("employee_id", eids).gte("shift_date", global_lo.isoformat())
              .lte("shift_date", period_end.isoformat()).execute().data) or []
    timelog = (sb().table("timelog").select("*").eq("org_id", org_id).in_("employee_id", eids)
               .gte("work_date", global_lo.isoformat()).lte("work_date", period_end.isoformat())
               .limit(20000).execute().data) or []
    shifts_by_emp = _salary_owed_group_by_employee(shifts)
    timelog_by_emp = _salary_owed_group_by_employee(timelog)
    lunch_by_emp_day = _salary_owed_lunch_by_emp_day(org_id, timelog)
    pp_settings = _tenant_pp_settings(org_id)

    lo_by_emp = {}
    for eid in eids:
        emp = emp_map.get(eid, {})
        lo_by_emp[eid] = _owed.earned_lookback_start(payroll_salary.parse_date(emp.get("hire_date")), period_end)

    owed_by_emp = _salary_owed_for_employees([emp_map[e] for e in eids if e in emp_map],
                                              lo_by_emp, period_end, shifts_by_emp, timelog_by_emp,
                                              lunch_by_emp_day, pp_settings)

    out_employees, stores = [], {}
    for eid in eids:
        emp = emp_map.get(eid)
        res = owed_by_emp.get(eid)
        if not emp or res is None:
            continue   # unknown/deleted employee_id on an old ledger row -> can't attribute pay, skip
        earned = res["owed_total"]
        paid = cash_to_date.get(eid, 0.0)
        excess = _owed.additional_payroll_excess(paid, earned)
        store = _additional_payroll_store_for(org_id, eid, period_end.isoformat(), emp.get("home_store"))
        out_employees.append({"employee_id": eid, "name": emp.get("name") or eid, "store": store,
                              "earned_to_date": earned, "cash_paid_to_date": paid, "excess": excess,
                              "lookback_start": lo_by_emp[eid].isoformat()})
        if excess > 0:
            stores[store] = round(stores.get(store, 0.0) + excess, 2)

    cells = [{"store": s, "amount": amt} for s, amt in sorted(stores.items())]
    return {"employees": out_employees, "stores": stores, "cells": cells, "available": True,
            "period_start": period_start.isoformat(), "period_end": period_end.isoformat()}


def _additional_payroll_push_line(org_id, period, cells):
    """POST the per-store Additional Payroll excess to mod-commission's Store Expenses system-line
    endpoint (source_key='additional_payroll', label='Additional Payroll') — a DIFFERENT source_key
    than 'payroll_gross'/'payroll_expenses'/'pto_accrual' so it coexists as its OWN, non-double-
    counting P&L line (mod-finance routes it to the payroll_expenses P&L bucket, NOT wages — see
    docs/specs/envelope-expense-payout.md). Same best-effort contract as every sibling push in this
    file: any failure is caught and reported, NEVER raised."""
    url = f"{PTO_INTERNAL_API_BASE}/api/v1/commcalc/expenses/{period}/system-line"
    body = {"source_key": "additional_payroll", "label": "Additional Payroll", "cells": cells}
    try:
        resp = requests.post(url, params={"org_id": org_id}, json=body, timeout=10)
        if resp.status_code == 404:
            return {"pushed": False, "status": 404, "note": "system-line endpoint not deployed yet — recomputed live, pull via GET /salary-advance/additional-payroll/{period} instead"}
        resp.raise_for_status()
        return {"pushed": True, "status": resp.status_code}
    except Exception as e:
        return {"pushed": False, "status": None, "note": f"push failed ({type(e).__name__}: {e}) — recomputed live, pull via GET /salary-advance/additional-payroll/{{period}} instead"}


@router.get("/salary-advance/additional-payroll/{period}")
def get_additional_payroll(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Read-only preview (no push) of this period's Additional Payroll — the Salary Advances page's
    'Additional Payroll preview' card. Always computed LIVE (see _additional_payroll_gather); degrades
    to available:false pre-migration-419."""
    g = _additional_payroll_gather(org_id, period)
    ks = scope_keyset(authorization, org_id)
    employees = [e for e in g["employees"] if in_keyset(ks, e.get("store"))] if ks is not None else g["employees"]
    cells = [c for c in g["cells"] if ks is None or in_keyset(ks, c["store"])]
    return {"period": period, "employees": sorted(employees, key=lambda r: r["name"]),
            "cells": cells, "total": round(sum(c["amount"] for c in cells), 2), "available": g["available"]}


@router.post("/salary-advance/additional-payroll/run/{period}")
def run_additional_payroll(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manager-triggered explicit recompute + push for one period (mirrors /pto-accrual/run/{period} /
    /payroll-expenses/run/{period}). NEVER writes shifts/timelog/employees/payroll_gross_ledger — the
    ONLY write this can ever cause is the additive 'additional_payroll' Store Expenses system line."""
    _require_manager(authorization, org_id)
    g = _additional_payroll_gather(org_id, period)
    push = _additional_payroll_push_line(org_id, period, g["cells"]) if g["cells"] else \
        {"pushed": False, "status": None, "note": "no excess this period — nothing to push"}
    return {"period": period, "employees": g["employees"], "cells": g["cells"], "push": push}


@router.post("/salary-advance/additional-payroll/run-due")
def additional_payroll_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint (secret-gated, same NOTIFY_RUN_SECRET convention as
    /google-reviews/sweep/run-due) — recomputes + pushes the CURRENT calendar-month period's
    Additional Payroll for every org that has EVER recorded a salary advance. An operator must add the
    pg_cron schedule (see docs/handoffs/people.md OPERATOR ACTIONS) — this endpoint is inert (never
    called) until something invokes it; NEVER an unauthenticated trigger."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    period = datetime.now(_BIZ_TZ).strftime("%Y-%m")
    try:
        orgs = sorted({r.get("org_id") for r in
                       (sb().table("salary_advance_ledger").select("org_id").execute().data or [])
                       if r.get("org_id")})
    except Exception:
        orgs = []
    results = []
    for oid in orgs:
        g = _additional_payroll_gather(oid, period)
        push = _additional_payroll_push_line(oid, period, g["cells"]) if g["cells"] else \
            {"pushed": False, "status": None, "note": "no excess this period"}
        results.append({"org_id": oid, "stores_written": len(g["cells"]), "push": push})
    return {"period": period, "orgs_processed": len(orgs), "results": results}


@router.post("/salary-advance/record")
def record_salary_advance(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Records a cash salary advance from the daily-closing envelope, then recomputes + pushes THIS
    employee's period's Additional Payroll. OWNER RULE (verbatim intent): this NEVER touches
    payroll_gross / GET /storeops/payroll — it only appends to storeops.salary_advance_ledger and, if
    cumulative cash now exceeds cumulative earned, updates the SEPARATE 'additional_payroll' P&L line.
    Body: {employee_id, amount, paid_date, store_code, withdrawal_ref, recorded_by}. org_id is the
    QUERY param (RULE ONE). employee_id is validated against the real roster — pick-don't-type, never
    a free-text id (RULE THREE)."""
    u = _require_manager(authorization, org_id)
    body = body or {}
    employee_id = str(body.get("employee_id") or "").strip()
    if not employee_id:
        raise HTTPException(400, "employee_id is required")
    emp_rows = _employees_with_pay_fields(org_id, "id,name,employee_id,home_store", eq={"employee_id": employee_id})
    if not emp_rows:
        raise HTTPException(400, "Unknown employee_id — pick an existing employee (no free-text ids).")
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a number")
    if amount <= 0:
        raise HTTPException(400, "amount must be greater than 0")
    paid_date_raw = str(body.get("paid_date") or "").strip()
    try:
        paid_date = _date.fromisoformat(paid_date_raw[:10])
    except ValueError:
        raise HTTPException(400, "paid_date must be an ISO date (YYYY-MM-DD)")

    row = {
        "org_id": org_id, "employee_id": employee_id, "amount": amount, "paid_date": paid_date.isoformat(),
        "method": "envelope_cash", "store_code": body.get("store_code") or None,
        "withdrawal_ref": body.get("withdrawal_ref") or None,
        "recorded_by": body.get("recorded_by") or u.get("email") or u.get("employee_id") or "manager",
    }
    try:
        ins = sb().table("salary_advance_ledger").insert(row).execute()
    except Exception as e:
        raise HTTPException(503, f"Could not record the advance — is migration 419 applied? ({type(e).__name__}: {e})")

    period = paid_date.strftime("%Y-%m")
    g = _additional_payroll_gather(org_id, period)
    push = _additional_payroll_push_line(org_id, period, g["cells"]) if g["cells"] else \
        {"pushed": False, "status": None, "note": "no excess this period — nothing to push"}
    return {"ok": True, "id": (ins.data or [{}])[0].get("id"), "employee_id": employee_id,
            "amount": amount, "paid_date": row["paid_date"],
            "additional_payroll": {"period": period, "cells": g["cells"], "push": push}}


@router.get("/salary-advance/history")
def salary_advance_history(start: str = "", end: str = "", employee_id: str = "", store_code: str = "",
                            authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Advance history list for the Salary Advances page — RULE FIVE core filters (period/store/rep).
    Degrades to an empty, available:false list pre-migration-419 (never a 500)."""
    try:
        q = sb().table("salary_advance_ledger").select("*").eq("org_id", org_id)
        if start:
            q = q.gte("paid_date", start)
        if end:
            q = q.lte("paid_date", end)
        if employee_id:
            q = q.eq("employee_id", employee_id)
        if store_code:
            q = q.eq("store_code", store_code)
        rows = q.order("paid_date", desc=True).limit(2000).execute().data or []
    except Exception:
        return {"items": [], "available": False}
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]
    names = {}
    if rows:
        eids = sorted({r.get("employee_id") for r in rows if r.get("employee_id")})
        try:
            emps = (sb().table("employees").select("employee_id,name").eq("org_id", org_id)
                    .in_("employee_id", eids).execute().data) or []
            names = {e["employee_id"]: e.get("name") for e in emps}
        except Exception:
            pass
    for r in rows:
        r["employee_name"] = names.get(r.get("employee_id")) or r.get("employee_id")
    return {"items": rows, "available": True}



# ═══════════════════════════════════════════════════════════════════════════════════════════════
# GOOGLE REVIEWS (Phase 1) — owner directive 2026-07-27. Pure logic + Google Places HTTP calls live
# in google_reviews.py (imported as `_gr` above); everything here is auth/scoping glue using this
# file's OWN caller-identity/span helpers (see the module docstring in google_reviews.py for why the
# split is this way, not a sub-router like hr/letters.py).
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _require_google_reviews_admin(authorization, x_active_org, org_id):
    """Per-setting edit-permission gate (SETTING_AREAS 'google_reviews' pattern) for the Google
    Reviews CONFIG (api key / target / sweep schedule / place overrides). `google_reviews` is NOT
    yet in core's SETTING_AREAS registry (filed NEEDS CORE — see the people handoff) — that's fine,
    `_can_edit_setting` already degrades correctly for an unregistered key (super_admin always yes;
    a full-scope/'admin' role yes; anyone else no) per its own documented precedence, it just isn't
    yet toggleable per-ROLE in the Roles UI. Falls back to `_require_manager` when the settings-area
    path can't resolve the caller at all (RBAC off / no token), so this never blocks a legitimate
    manager on a resolution hiccup — same posture as commcalc's `_require_import_admin` /
    hr/letters.py's `_require_letters_admin`."""
    try:
        from app.modules.core.router import _can_edit_setting, _resolve_caller, _uid_from_token
        uid = _uid_from_token(authorization)
        if uid:
            caller = _resolve_caller(get_supabase(), uid, x_active_org)
            if caller and caller.get("org_id"):
                if not (caller.get("super_admin") or _can_edit_setting(caller, "google_reviews")):
                    raise HTTPException(403, "You don't have permission to edit Google Reviews settings.")
                return caller.get("org_id")
    except HTTPException:
        raise
    except Exception:
        pass
    u = _require_manager(authorization, org_id)
    return u.get("org_id") or org_id


def _gr_manager_span(authorization, org_id):
    """None = UNRESTRICTED (admin/'all'-scope role, or an unresolvable caller) — same fallback
    posture /employees/visible uses. Otherwise the (possibly empty) list of store_codes the caller's
    role/org-tree position grants them."""
    au = _caller_app_user(authorization, org_id)
    if not au:
        return None
    scope = _role_scope(org_id, (au.get("role") or "").strip())
    if scope == "all":
        return None
    return _caller_span_codes(authorization, org_id)


def _gr_store_card(client, org_id, store_code, store_row, cfg, employee_id=None):
    """One store's rating/target/status + recent reviews (+ the caller's own open action plan, when
    `employee_id` is given). Shared by /google-reviews/my, /google-reviews/dm-dashboard and
    /google-reviews/store/{store_code}. Never raises — every read degrades to an empty/None default."""
    try:
        ov = (client.table("google_review_store").select("*").eq("org_id", org_id)
              .eq("store_code", store_code).limit(1).execute().data) or []
    except Exception:
        ov = []
    ov0 = ov[0] if ov else {}
    target = _gr.effective_target(ov0, cfg.get("target_default"))
    try:
        snaps = (client.table("google_review_snapshot").select("*").eq("org_id", org_id)
                 .eq("store_code", store_code).order("fetched_at", desc=True)
                 .limit(1).execute().data) or []
    except Exception:
        snaps = []
    snap = snaps[0] if snaps else {}
    rating, review_count = snap.get("rating"), snap.get("review_count")
    status = _gr.rating_status(rating, target)
    try:
        reviews = (client.table("google_review_item").select("*").eq("org_id", org_id)
                   .eq("store_code", store_code).order("first_seen_at", desc=True)
                   .limit(10).execute().data) or []
    except Exception:
        reviews = []
    for r in reviews:
        r["possible_mention"] = bool(r.get("matched_employee_id"))
    my_plan = None
    if employee_id:
        try:
            plans = (client.table("action_plan").select("*").eq("org_id", org_id)
                     .eq("store_code", store_code).eq("employee_id", str(employee_id))
                     .neq("status", "completed").order("created_at", desc=True)
                     .limit(1).execute().data) or []
            my_plan = plans[0] if plans else None
        except Exception:
            my_plan = None
    return {"store_code": store_code, "address": store_row.get("address"),
            "market": store_row.get("market"), "place_id": ov0.get("place_id"),
            "rating": rating, "review_count": review_count, "target": target, "status": status,
            "reviews_needed": _gr.reviews_needed_for_target(rating, review_count, target),
            "reviews": reviews, "action_plan": my_plan,
            "fetched_at": snap.get("fetched_at")}


def _gr_set_sweep_status(client, org_id, status, detail, mark_run=False):
    now_iso = datetime.now(timezone.utc).isoformat()
    row = {"last_attempt_at": now_iso, "last_status": status, "last_detail": (detail or "")[:500]}
    if mark_run:
        row["last_run_at"] = now_iso
    try:
        client.table("google_review_sweep_config").update(row).eq("org_id", org_id).execute()
    except Exception:
        pass


def _do_google_reviews_sweep(org_id, store_codes=None):
    """Background-task body for both /sweep/run-now and /sweep/run-due. Never raises — every failure
    is recorded on google_review_sweep_config.last_status/last_detail instead."""
    client = sb()
    _gr_set_sweep_status(client, org_id, "running", "Sweep in progress…")
    try:
        res = _gr.sweep_org(client, org_id, only_store_codes=store_codes)
    except Exception as e:
        _gr_set_sweep_status(client, org_id, "error", f"Sweep failed: {e}", mark_run=True)
        return {"ok": False, "error": str(e)}
    if res.get("skipped"):
        _gr_set_sweep_status(client, org_id, "idle", res.get("reason") or "not enabled", mark_run=True)
        return res
    stores_res = res.get("stores") or []
    # Gate-1 N5: a FATAL failure (ok=False, e.g. no place_id resolvable) is a real 'error'; a
    # non-fatal per-row write failure (ok=True but status='partial' — see sweep_store) is reported
    # separately so a transient write hiccup never reads as an outright sweep failure.
    fatal_errs = [s.get("error") for s in stores_res if s.get("error") and not s.get("ok")]
    partials = [s for s in stores_res if s.get("status") == "partial"]
    ok_count = len([s for s in stores_res if s.get("ok")])
    detail = f"OK — {ok_count}/{len(stores_res)} store(s)"
    if partials:
        detail += f" · {len(partials)} partial ({'; '.join(p.get('partial_detail') or '' for p in partials)[:200]})"
    if fatal_errs:
        detail += f" · {len(fatal_errs)} error(s)"
    status = "ok"
    if fatal_errs and ok_count == 0:
        status = "error"
    elif fatal_errs or partials:
        status = "partial"
    _gr_set_sweep_status(client, org_id, status, detail, mark_run=True)
    all_notes = [n for s in stores_res for n in (s.get("notifications") or [])]
    if all_notes:
        try:
            from app.modules.notify.channels import email_resend
            if email_resend.is_configured():
                import asyncio

                async def _send_all():
                    for n in all_notes:
                        try:
                            await email_resend.send_email(to=n["email"], subject=n["subject"],
                                                          html=f"<p>{n['body']}</p>")
                        except Exception:
                            pass
                asyncio.run(_send_all())
        except Exception:
            pass
    return res


# ── config ───────────────────────────────────────────────────────────────────────────────────────
@router.get("/google-reviews/config")
def get_google_reviews_config(authorization: str = Header(default=""),
                              x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Masked org config for the admin page — the api_key is NEVER returned raw (has_api_key +
    a trailing-4-char hint only). Any manager may view; `can_edit` tells the page whether THIS
    caller may Save (see _require_google_reviews_admin)."""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    cfg = _gr.get_config(sb(), org_id)
    out = _gr.public_config(cfg)
    try:
        _require_google_reviews_admin(authorization, x_active_org, org_id)
        out["can_edit"] = True
    except HTTPException:
        out["can_edit"] = False
    return out


@router.put("/google-reviews/config")
def put_google_reviews_config(body: dict, authorization: str = Header(default=""),
                              x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """api_key is WRITE-ONLY: send it to (re)set it, omit/blank to keep the existing one — same
    posture as every other credential config (VIP/DLAR/epay sweep configs)."""
    org_id = _require_google_reviews_admin(authorization, x_active_org, org_id)
    row = {"org_id": org_id, "updated_at": datetime.now(timezone.utc).isoformat()}
    if "enabled" in body:
        row["enabled"] = bool(body["enabled"])
    if "target_default" in body:
        row["target_default"] = _gr.clamp_target(body["target_default"])
    if "notify_on_new_reviews" in body:
        row["notify_on_new_reviews"] = bool(body["notify_on_new_reviews"])
    if "lookback_days" in body:
        # Phase 1.5: how far back an employee's store-set lookup looks for a shift (migration 420).
        # Pre-migration this key simply doesn't exist on the table yet — the upsert then fails and
        # the generic except below turns it into a clear 400 naming the migration (same posture as
        # every other not-yet-run-migration write here); GET always still returns the code default
        # (30) regardless via google_reviews.get_config's degrade-gracefully shape.
        row["lookback_days"] = _gr.clamp_lookback_days(body["lookback_days"])
    if "search_brand" in body:
        # mig 430 — the business token prepended to the Places text search. Empty string clears it back
        # to address-only (which resolves to the postal address and yields no rating — see the migration).
        row["search_brand"] = (str(body["search_brand"] or "").strip() or None)
    key = (body.get("api_key") or "").strip()
    if key:
        row["api_key"] = key
    try:
        sb().table("google_review_config").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save (run migration 411 first? migration 420 for "
                                 f"lookback_days?): {str(e)[:160]}")
    return _gr.public_config(_gr.get_config(sb(), org_id))


@router.get("/google-reviews/sweep-config")
def get_google_reviews_sweep_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    cfg = _gr.get_sweep_config(sb(), org_id)
    return {k: cfg.get(k) for k in ("enabled", "frequency", "day_of_week", "hour", "timezone",
                                    "next_run_at", "last_run_at", "last_attempt_at",
                                    "last_status", "last_detail")}


@router.put("/google-reviews/sweep-config")
def put_google_reviews_sweep_config(body: dict, authorization: str = Header(default=""),
                                    x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    org_id = _require_google_reviews_admin(authorization, x_active_org, org_id)
    cur = _gr.get_sweep_config(sb(), org_id)
    row = {"org_id": org_id}
    for k in ("enabled", "frequency", "day_of_week", "hour", "timezone"):
        if k in body and body[k] is not None:
            row[k] = body[k]
    merged = {**cur, **row}
    row["next_run_at"] = _gr.next_run_at(merged.get("frequency") or "daily", merged.get("day_of_week"),
                                         merged.get("hour"), merged.get("timezone"))
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        sb().table("google_review_sweep_config").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save (run migration 411 first?): {str(e)[:160]}")
    return get_google_reviews_sweep_config(authorization=authorization, org_id=org_id)


@router.post("/google-reviews/sweep/run-now")
def post_google_reviews_run_now(background_tasks: BackgroundTasks, body: dict = None,
                                authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manual 'Refresh now' from the admin/DM page."""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    cfg = _gr.get_config(sb(), org_id)
    if not cfg.get("api_key"):
        raise HTTPException(400, "Set the Google Places API key first.")
    store_codes = (body or {}).get("store_codes") if body else None
    background_tasks.add_task(_do_google_reviews_sweep, org_id, store_codes)
    return {"status": "started"}


@router.post("/google-reviews/sweep/run-due")
def post_google_reviews_run_due(background_tasks: BackgroundTasks,
                                x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint: run every enabled config whose next_run_at has passed. Secret-gated —
    NEVER an unauthenticated trigger. Reuses NOTIFY_RUN_SECRET so no new env var is needed."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        due = (client.table("google_review_sweep_config").select("*").eq("enabled", True)
               .lte("next_run_at", now_iso).execute().data) or []
    except Exception:
        due = []
    for cfgrow in due:
        oid = cfgrow.get("org_id")
        if not oid:
            continue
        nxt = _gr.next_run_at(cfgrow.get("frequency") or "daily", cfgrow.get("day_of_week"),
                              cfgrow.get("hour"), cfgrow.get("timezone"))
        try:
            client.table("google_review_sweep_config").update({"next_run_at": nxt}).eq("org_id", oid).execute()
        except Exception:
            pass
        background_tasks.add_task(_do_google_reviews_sweep, oid, None)
    return {"triggered": len(due)}


# ── per-store admin overlay (place_id override + target override + auto-resolve) ──────────────────
@router.get("/google-reviews/stores")
def list_google_review_stores(authorization: str = Header(default=""), org_id: str = ORG_ID):
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    client = sb()
    cfg = _gr.get_config(client, org_id)
    try:
        stores = (client.table("stores").select("store_code,address,market,is_active")
                  .eq("org_id", org_id).execute().data) or []
    except Exception:
        stores = []
    try:
        overlay_rows = (client.table("google_review_store").select("*")
                        .eq("org_id", org_id).execute().data) or []
    except Exception:
        overlay_rows = []
    overlay = {r["store_code"]: r for r in overlay_rows if r.get("store_code")}
    try:
        snaps = (client.table("google_review_snapshot")
                 .select("store_code,rating,review_count,fetched_at")
                 .eq("org_id", org_id).order("fetched_at", desc=True).limit(3000).execute().data) or []
    except Exception:
        snaps = []
    latest = {}
    for s in snaps:
        sc = s.get("store_code")
        if sc and sc not in latest:
            latest[sc] = s
    out = []
    for s in stores:
        sc = s.get("store_code")
        ov = overlay.get(sc) or {}
        snap = latest.get(sc) or {}
        target = _gr.effective_target(ov, cfg.get("target_default"))
        rating = snap.get("rating")
        out.append({"store_code": sc, "address": s.get("address"), "market": s.get("market"),
                    "is_active": s.get("is_active"), "place_id": ov.get("place_id"),
                    "place_id_source": ov.get("place_id_source"),
                    "resolved_address": ov.get("resolved_address"),
                    "resolved_display_name": ov.get("resolved_display_name"),
                    "target_override": ov.get("target_override"), "target": target,
                    "rating": rating, "review_count": snap.get("review_count"),
                    "status": _gr.rating_status(rating, target), "fetched_at": snap.get("fetched_at"),
                    "reviews_needed": _gr.reviews_needed_for_target(
                        rating, snap.get("review_count"), target)})
    return {"stores": out, "target_default": cfg.get("target_default", _gr.DEFAULT_TARGET)}


@router.put("/google-reviews/store-config/{store_code}")
def put_google_review_store_config(store_code: str, body: dict, authorization: str = Header(default=""),
                                   x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Manual place_id / target overrides (pick-don't-type: store_code comes from the existing store
    roster the page already renders, never free-typed)."""
    org_id = _require_google_reviews_admin(authorization, x_active_org, org_id)
    store_code = (store_code or "").strip()
    if not store_code:
        raise HTTPException(400, "store_code is required")
    row = {"org_id": org_id, "store_code": store_code, "updated_at": datetime.now(timezone.utc).isoformat()}
    if body.get("clear_target_override"):
        row["target_override"] = None
    elif "target_override" in body and body["target_override"] not in (None, ""):
        row["target_override"] = _gr.clamp_target(body["target_override"])
    if body.get("clear_place_id"):
        row["place_id"] = None
        row["place_id_source"] = "manual"
    elif (body.get("place_id") or "").strip():
        row["place_id"] = body["place_id"].strip()
        row["place_id_source"] = "manual"
        if body.get("resolved_address"):
            row["resolved_address"] = body["resolved_address"]
        if body.get("resolved_display_name"):
            row["resolved_display_name"] = body["resolved_display_name"]
    try:
        sb().table("google_review_store").upsert(row, on_conflict="org_id,store_code").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save (run migration 411 first?): {str(e)[:160]}")
    # Return what actually PERSISTED, read back from the table — not an unconditional {"ok": True}
    # (2026-08-10). The old shape could not distinguish a real write from a no-op, so the settings
    # page had no way to show the operator whether a manually-pasted Place ID had landed: it kept
    # rendering the string still sitting in local component state either way. Read-back also catches
    # the case where the upsert reports success but a filtered/again-empty row comes back.
    saved = {}
    try:
        got = (sb().table("google_review_store").select("*").eq("org_id", org_id)
               .eq("store_code", store_code).limit(1).execute().data) or []
        saved = got[0] if got else {}
    except Exception:
        saved = {}
    if not saved:
        raise HTTPException(500, "The save reported success but nothing was stored for "
                                 f"{store_code}. Nothing has been changed — please report this.")
    return {"ok": True, "store_code": store_code, "place_id": saved.get("place_id"),
            "place_id_source": saved.get("place_id_source"),
            "target_override": saved.get("target_override"),
            "resolved_address": saved.get("resolved_address"),
            "resolved_display_name": saved.get("resolved_display_name")}


@router.post("/google-reviews/resolve-place")
def post_resolve_place(body: dict, authorization: str = Header(default=""),
                       x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Google Places Text Search on the store's OWN address (from the existing store registry — no
    free-typed address here). Costs a real Places API call, so it's admin-gated."""
    org_id = _require_google_reviews_admin(authorization, x_active_org, org_id)
    store_code = (body.get("store_code") or "").strip()
    if not store_code:
        raise HTTPException(400, "store_code is required")
    client = sb()
    cfg = _gr.get_config(client, org_id)
    if not cfg.get("api_key"):
        raise HTTPException(400, "Set the Google Places API key first.")
    try:
        st = (client.table("stores").select("address").eq("org_id", org_id)
              .eq("store_code", store_code).limit(1).execute().data) or []
    except Exception:
        st = []
    address = (st[0].get("address") if st else None) or (body.get("address") or "").strip()
    if not address:
        raise HTTPException(400, "This store has no address on file — add one, or set the place_id manually.")
    try:
        row = _gr.resolve_place_for_store(client, org_id, store_code, address, cfg["api_key"])
    except Exception as e:
        raise HTTPException(400, f"Google Places lookup failed: {e}")
    return {"ok": True, **row}


# ── read surfaces: employee ('my') + DM/manager dashboard + one-store detail ───────────────────────
@router.get("/google-reviews/my")
def my_google_reviews(authorization: str = Header(default="")):
    """The SIGNED-IN employee's own highlighted rating card(s), one per store they are scheduled at
    (next 14 days, minus a 2-day lookback) UNION their home store — identity from the token, same
    self-view rule as every other self-service endpoint here."""
    org_id, employee_id = _caller_identity(authorization)
    client = sb()
    cfg = _gr.get_config(client, org_id)
    ids, _name = _emp_id_variants(org_id, employee_id)
    try:
        emp_row = (client.table("employees").select("home_store").eq("org_id", org_id)
                   .eq("employee_id", employee_id).limit(1).execute().data) or []
    except Exception:
        emp_row = []
    home_store = ((emp_row[0].get("home_store") if emp_row else "") or "").strip()
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=2)).date().isoformat()
    upto = (now + timedelta(days=14)).date().isoformat()
    try:
        shifts = (client.table("shifts").select("store_code").eq("org_id", org_id)
                  .in_("employee_id", list(ids)).eq("is_deleted", False)
                  .gte("shift_date", since).lte("shift_date", upto).execute().data) or []
    except Exception:
        shifts = []
    store_codes = {s.get("store_code") for s in shifts if s.get("store_code")}
    try:
        all_stores = (client.table("stores").select("store_code,address,market")
                      .eq("org_id", org_id).execute().data) or []
    except Exception:
        all_stores = []
    if home_store:
        hs_upper = home_store.upper()
        matched = next((s["store_code"] for s in all_stores
                        if (s.get("store_code") or "").upper() == hs_upper
                        or (s.get("address") or "").upper() == hs_upper), None)
        if matched:
            store_codes.add(matched)
    store_by_code = {s["store_code"]: s for s in all_stores if s.get("store_code")}
    out = [_gr_store_card(client, org_id, sc, store_by_code.get(sc) or {"store_code": sc}, cfg,
                          employee_id=employee_id)
           for sc in sorted(c for c in store_codes if c)]
    return {"employee_id": employee_id, "stores": out,
           "note": ("Showing Google's highlighted reviews — Google Places returns a curated subset "
                    "(typically ~5), not every review ever left.")}


@router.get("/google-reviews/dm-dashboard")
def google_reviews_dm_dashboard(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Every store under the caller's span (org-tree/market/store manager scope; unrestricted for a
    full admin — see _gr_manager_span), rating vs target highlighted, with each store's action plans
    (open + history) for the DM review queue."""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    client = sb()
    cfg = _gr.get_config(client, org_id)
    span = _gr_manager_span(authorization, org_id)
    try:
        all_stores = (client.table("stores").select("store_code,address,market,is_active")
                      .eq("org_id", org_id).execute().data) or []
    except Exception:
        all_stores = []
    if span is not None:
        keyset = {c.upper() for c in span}
        stores = [s for s in all_stores if (s.get("store_code") or "").upper() in keyset]
    else:
        stores = all_stores
    out = []
    for s in stores:
        card = _gr_store_card(client, org_id, s["store_code"], s, cfg)
        try:
            plans = (client.table("action_plan").select("*").eq("org_id", org_id)
                     .eq("store_code", s["store_code"]).order("created_at", desc=True)
                     .limit(50).execute().data) or []
        except Exception:
            plans = []
        card["action_plans"] = plans
        card["open_action_plan_count"] = len([p for p in plans if p.get("status") != "completed"])
        card["is_active"] = s.get("is_active")
        out.append(card)
    return {"stores": out, "target_default": cfg.get("target_default", _gr.DEFAULT_TARGET)}


@router.get("/google-reviews/store/{store_code}")
def google_review_store_detail(store_code: str, authorization: str = Header(default=""),
                               org_id: str = ORG_ID):
    """One store's card — a manager in span, or an employee scheduled/home there, may view."""
    client = sb()
    au = _caller_app_user(authorization, org_id)
    org_id = (au.get("org_id") if au else None) or org_id
    allowed = False
    employee_id = None
    if au:
        role = (au.get("role") or "").strip()
        if role in {"admin", "market_manager", "store_manager", "district_manager",
                    "regional_manager", "director", "executive"} or _role_scope(org_id, role) != "self":
            span = _gr_manager_span(authorization, org_id)
            allowed = span is None or store_code.upper() in {c.upper() for c in span}
    if not allowed:
        try:
            org_id2, eid = _caller_identity(authorization)
            org_id = org_id2 or org_id
            employee_id = eid
            emps = _gr.employees_for_store(client, org_id, store_code)
            allowed = any(str(e.get("employee_id")) == str(eid) for e in emps)
        except HTTPException:
            allowed = False
    if not allowed:
        raise HTTPException(403, "You don't have access to this store's reviews.")
    cfg = _gr.get_config(client, org_id)
    try:
        st = (client.table("stores").select("store_code,address,market").eq("org_id", org_id)
              .eq("store_code", store_code).limit(1).execute().data) or []
    except Exception:
        st = []
    store_row = st[0] if st else {"store_code": store_code}
    return _gr_store_card(client, org_id, store_code, store_row, cfg, employee_id=employee_id)


# ── Phase 1.5 (owner directive 2026-08-06): "google reviews everywhere" — one employee's card(s),
# and a light batched summary for table columns, so a rating can be surfaced next to that employee
# wherever they're shown (action plans, Performance Management, the commission dashboard, their own
# dashboard). Response shapes here are an EXACT contract other agents (mod-commission) code against
# — do not change field names/nesting without updating both sides. ────────────────────────────────
@router.get("/google-reviews/employee/{employee_id}")
def google_review_employee_detail(employee_id: str, authorization: str = Header(default=""),
                                  org_id: str = ORG_ID):
    """One employee's rating card(s) — home_store UNION any store they're scheduled at within the
    tenant's configurable lookback window (google_review_config.lookback_days, default 30) through
    +14 days ahead (same forward window as /google-reviews/my). A manager whose span covers AT LEAST
    ONE of the employee's stores may view it (unrestricted for an admin/'all'-scope role — same
    posture as /google-reviews/store/{code}); the employee may always view their OWN card (same
    self-rule as /google-reviews/my).

    Response (EXACT):
      {"employee_id", "employee_name", "stores": [<same card shape _gr_store_card returns, with
      action_plan scoped to THIS employee>], "note"}"""
    client = sb()
    employee_id = (employee_id or "").strip()
    if not employee_id:
        raise HTTPException(400, "employee_id is required")
    au = _caller_app_user(authorization, org_id)
    try:
        emp_rows = (client.table("employees").select("employee_id,name,home_store")
                    .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
    except Exception:
        emp_rows = []
    if not emp_rows:
        raise HTTPException(404, "Unknown employee.")
    emp = emp_rows[0]

    cfg = _gr.get_config(client, org_id)
    lookback = _gr.clamp_lookback_days(cfg.get("lookback_days"))
    store_map = _gr.stores_for_employees(client, org_id, [employee_id], lookback_days=lookback)
    store_codes = store_map.get(employee_id) or []

    allowed = False
    if au:
        role = (au.get("role") or "").strip()
        if role in {"admin", "market_manager", "store_manager", "district_manager",
                    "regional_manager", "director", "executive"} or _role_scope(org_id, role) != "self":
            span = _gr_manager_span(authorization, org_id)
            if span is None:
                allowed = True
            else:
                keyset = {c.upper() for c in span}
                allowed = any(c.upper() in keyset for c in store_codes)
    if not allowed:
        try:
            self_org, self_eid = _caller_identity(authorization)
            if self_org and str(self_eid) == str(employee_id):
                allowed = True
                org_id = self_org or org_id
        except HTTPException:
            allowed = False
    if not allowed:
        raise HTTPException(403, "You don't have access to this employee's reviews.")

    try:
        all_stores = (client.table("stores").select("store_code,address,market")
                      .eq("org_id", org_id).execute().data) or []
    except Exception:
        all_stores = []
    store_by_code = {s["store_code"]: s for s in all_stores if s.get("store_code")}
    cards = [_gr_store_card(client, org_id, sc, store_by_code.get(sc) or {"store_code": sc}, cfg,
                            employee_id=employee_id)
             for sc in sorted(c for c in store_codes if c)]
    return {"employee_id": employee_id, "employee_name": emp.get("name"), "stores": cards,
           "note": ("Showing Google's highlighted reviews — Google Places returns a curated subset "
                    "(typically ~5), not every review ever left.")}


@router.get("/google-reviews/employee-summary")
def google_reviews_employee_summary(employee_ids: str = "", authorization: str = Header(default=""),
                                    org_id: str = ORG_ID):
    """Batched, LIGHT per-employee rating rows for a ranking/commission TABLE column — one call, not
    N (never a per-employee round trip). No review text. Same span gating as the rest of this file's
    manager reads, but an employee OUTSIDE the caller's span is silently DROPPED from the result
    (a mixed roster on a ranking table is normal) rather than 403-ing the whole call.

    Response (EXACT):
      {"summaries": {"<employee_id>": [{"store_code","rating","review_count","target","status"}, ...]}}"""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    ids = sorted({e.strip() for e in (employee_ids or "").split(",") if e.strip()})
    if not ids:
        return {"summaries": {}}
    client = sb()
    span = _gr_manager_span(authorization, org_id)
    cfg = _gr.get_config(client, org_id)
    lookback = _gr.clamp_lookback_days(cfg.get("lookback_days"))
    try:
        store_rows = (client.table("stores").select("store_code,address,market")
                      .eq("org_id", org_id).execute().data) or []
    except Exception:
        store_rows = []
    store_map = _gr.stores_for_employees(client, org_id, ids, lookback_days=lookback,
                                         store_rows=store_rows)
    if span is not None:
        keyset = {c.upper() for c in span}
        store_map = {eid: [c for c in codes if c.upper() in keyset] for eid, codes in store_map.items()}
    all_codes = sorted({c for codes in store_map.values() for c in codes})
    overlay: dict = {}
    latest: dict = {}
    if all_codes:
        try:
            overlay_rows = (client.table("google_review_store").select("store_code,target_override")
                            .eq("org_id", org_id).in_("store_code", all_codes).execute().data) or []
        except Exception:
            overlay_rows = []
        overlay = {r["store_code"]: r for r in overlay_rows if r.get("store_code")}
        try:
            snaps = (client.table("google_review_snapshot")
                     .select("store_code,rating,review_count,fetched_at")
                     .eq("org_id", org_id).in_("store_code", all_codes)
                     .order("fetched_at", desc=True).limit(3000).execute().data) or []
        except Exception:
            snaps = []
        for s in snaps:
            sc = s.get("store_code")
            if sc and sc not in latest:
                latest[sc] = s
    summaries: dict = {}
    for eid, codes in store_map.items():
        if not codes:
            continue
        rows = []
        for sc in codes:
            ov = overlay.get(sc) or {}
            target = _gr.effective_target(ov, cfg.get("target_default"))
            snap = latest.get(sc) or {}
            rating = snap.get("rating")
            rows.append({"store_code": sc, "rating": rating, "review_count": snap.get("review_count"),
                        "target": target, "status": _gr.rating_status(rating, target),
                        "reviews_needed": _gr.reviews_needed_for_target(
                            rating, snap.get("review_count"), target)})
        summaries[eid] = rows
    return {"summaries": summaries}


# ── action plans ─────────────────────────────────────────────────────────────────────────────────
@router.get("/action-plan-areas")
def list_action_plan_areas(authorization: str = Header(default=""), org_id: str = ORG_ID):
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    try:
        rows = (sb().table("action_plan_area").select("*").eq("org_id", org_id)
                .order("area_key").execute().data) or []
    except Exception:
        rows = []
    if not rows:
        rows = [{"org_id": org_id, "area_key": _gr.DEFAULT_AREA_KEY, "label": "Google Reviews",
                "enabled": True}]
    return {"areas": rows}


@router.get("/action-plans/mine")
def my_action_plans(authorization: str = Header(default="")):
    org_id, employee_id = _caller_identity(authorization)
    try:
        rows = (sb().table("action_plan").select("*").eq("org_id", org_id)
                .eq("employee_id", str(employee_id)).order("created_at", desc=True)
                .limit(200).execute().data) or []
    except Exception:
        rows = []
    return {"items": rows}


@router.get("/action-plans")
def list_action_plans(status: str = "", store_code: str = "", authorization: str = Header(default=""),
                      org_id: str = ORG_ID):
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    span = _gr_manager_span(authorization, org_id)
    q = sb().table("action_plan").select("*").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    if store_code:
        q = q.eq("store_code", store_code)
    try:
        rows = q.order("created_at", desc=True).limit(500).execute().data or []
    except Exception:
        rows = []
    if span is not None:
        keyset = {c.upper() for c in span}
        rows = [r for r in rows if (r.get("store_code") or "").upper() in keyset]
    return {"items": rows}


@router.post("/action-plans/{plan_id}/submit")
def submit_action_plan(plan_id: str, body: dict, authorization: str = Header(default="")):
    """Self-service: an employee submits their OWN required action plan. identity from token."""
    org_id, employee_id = _caller_identity(authorization)
    plan_text = (body.get("plan_text") or "").strip()
    if not plan_text:
        raise HTTPException(400, "plan_text is required")
    try:
        rows = (sb().table("action_plan").select("*").eq("id", plan_id).eq("org_id", org_id)
                .limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(404, "Unknown action plan.")
    row = rows[0]
    if str(row.get("employee_id")) != str(employee_id):
        raise HTTPException(403, "You can only submit your own action plan.")
    if not _gr.can_submit(row.get("status")):
        raise HTTPException(400, f"This plan is already '{row.get('status')}' — it can't be (re)submitted.")
    now_iso = datetime.now(timezone.utc).isoformat()
    upd = {"status": "submitted", "plan_text": plan_text, "submitted_at": now_iso, "updated_at": now_iso}
    (sb().table("action_plan").update(upd).eq("id", plan_id).eq("org_id", org_id).execute())
    return {"ok": True, **row, **upd}


@router.post("/action-plans/{plan_id}/push-back")
def push_back_action_plan(plan_id: str, body: dict, authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    """DM/manager review: send a submitted plan back with comments + a due date."""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    due_date = (body.get("due_date") or "").strip()[:10]
    dm_comments = (body.get("dm_comments") or "").strip()
    if not due_date:
        raise HTTPException(400, "due_date is required")
    try:
        rows = (sb().table("action_plan").select("*").eq("id", plan_id).eq("org_id", org_id)
                .limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(404, "Unknown action plan.")
    row = rows[0]
    if not _gr.can_push_back(row.get("status")):
        raise HTTPException(400, f"This plan is '{row.get('status')}' — it isn't awaiting review.")
    now_iso = datetime.now(timezone.utc).isoformat()
    upd = {"status": "pushed_back", "dm_comments": dm_comments, "due_date": due_date,
          "reviewed_at": now_iso, "reviewed_by": u.get("email") or u.get("employee_id"),
          "employee_marked_done_at": None, "updated_at": now_iso}
    (sb().table("action_plan").update(upd).eq("id", plan_id).eq("org_id", org_id).execute())
    return {"ok": True, **row, **upd}


@router.post("/action-plans/{plan_id}/approve")
def approve_action_plan(plan_id: str, body: dict = None, authorization: str = Header(default=""),
                        org_id: str = ORG_ID):
    """DM/manager accepts a submitted plan as-is (optionally with a due date/comments) — moves it
    straight to in_progress without a 'needs revision' round trip."""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    try:
        rows = (sb().table("action_plan").select("*").eq("id", plan_id).eq("org_id", org_id)
                .limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(404, "Unknown action plan.")
    row = rows[0]
    if row.get("status") != "submitted":
        raise HTTPException(400, f"This plan is '{row.get('status')}' — only a submitted plan can be approved.")
    body = body or {}
    due_date = (body.get("due_date") or "").strip()[:10] or None
    now_iso = datetime.now(timezone.utc).isoformat()
    upd = {"status": "in_progress", "reviewed_at": now_iso,
          "reviewed_by": u.get("email") or u.get("employee_id"), "updated_at": now_iso}
    if due_date:
        upd["due_date"] = due_date
    if body.get("dm_comments"):
        upd["dm_comments"] = body["dm_comments"]
    (sb().table("action_plan").update(upd).eq("id", plan_id).eq("org_id", org_id).execute())
    return {"ok": True, **row, **upd}


@router.post("/action-plans/{plan_id}/employee-mark-done")
def employee_mark_action_plan_done(plan_id: str, authorization: str = Header(default="")):
    """Self-service: the employee says the work is done. Status only advances to in_progress here —
    it stays there until the DM confirms (dm-confirm-complete), so nothing is silently closed out."""
    org_id, employee_id = _caller_identity(authorization)
    try:
        rows = (sb().table("action_plan").select("*").eq("id", plan_id).eq("org_id", org_id)
                .limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(404, "Unknown action plan.")
    row = rows[0]
    if str(row.get("employee_id")) != str(employee_id):
        raise HTTPException(403, "You can only update your own action plan.")
    if not _gr.can_employee_mark_done(row.get("status")):
        raise HTTPException(400, f"This plan is '{row.get('status')}' — nothing to mark done yet.")
    now_iso = datetime.now(timezone.utc).isoformat()
    upd = {"employee_marked_done_at": now_iso, "updated_at": now_iso}
    if row.get("status") == "pushed_back":
        upd["status"] = "in_progress"
    (sb().table("action_plan").update(upd).eq("id", plan_id).eq("org_id", org_id).execute())
    return {"ok": True, **row, **upd}


@router.post("/action-plans/{plan_id}/dm-confirm-complete")
def dm_confirm_action_plan(plan_id: str, body: dict = None, authorization: str = Header(default=""),
                           org_id: str = ORG_ID):
    """DM/manager confirms the employee's completed work — the ONLY path to 'completed' (terminal)."""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    try:
        rows = (sb().table("action_plan").select("*").eq("id", plan_id).eq("org_id", org_id)
                .limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(404, "Unknown action plan.")
    row = rows[0]
    if not _gr.can_dm_confirm(row.get("status"), row.get("employee_marked_done_at")):
        raise HTTPException(400, "This plan hasn't been marked done by the employee yet.")
    now_iso = datetime.now(timezone.utc).isoformat()
    upd = {"status": "completed", "completed_at": now_iso, "reviewed_at": now_iso,
          "reviewed_by": u.get("email") or u.get("employee_id"), "updated_at": now_iso}
    if body and body.get("dm_comments"):
        upd["dm_comments"] = body["dm_comments"]
    (sb().table("action_plan").update(upd).eq("id", plan_id).eq("org_id", org_id).execute())
    return {"ok": True, **row, **upd}



# ── Admin-attention providers (settings-audit package, 2026-07-26) ────────────────────────────────
# Contribute StoreOps findings to the cross-module attention feed WITHOUT editing the shared
# core/import_health.py (AGENT_CONTRACT §1). Guarded: a missing/renamed core module (e.g. migration
# 717 not applied, or the module simply absent in an older deploy) must never break StoreOps itself.
try:
    from app.modules.core.import_health import register_provider as _register_attention_provider
    from app.modules.storeops import attention as _storeops_attention
    _storeops_attention.register(_register_attention_provider)
except Exception as _attn_e:
    print(f"WARN storeops attention providers not registered: {_attn_e}")


# ── Weekly payroll-hours approval (migration 431, owner directive 2026-08-10) ──────────────────────
# DM approves last week's hours -> HR approves -> dispatch to whoever pays that employee. The routes
# live in their own module (this file is already large) and are mounted onto THIS router, so the
# shared app/main.py needs no edit (AGENT_CONTRACT §1: no agent touches main.py).
#
# Imported LAST, and lazily on the other side: payroll_approval calls back into this module for
# get_payroll / _require_manager / _log_payroll_change / _dm_for_store, so its imports of `router.py`
# all sit INSIDE functions. By the time this line runs, everything it needs is defined.
from app.modules.storeops.payroll_approval import router as _payroll_approval_router  # noqa: E402
router.include_router(_payroll_approval_router)
