"""StoreOps API Router — /api/v1/storeops/*"""
import base64
import os
import requests
from datetime import datetime, timezone, timedelta, date as _date
from fastapi import APIRouter, HTTPException, Header, BackgroundTasks
from app.core.database import get_supabase
from app.core.config import settings
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


@router.post("/shifts")
def create_shift(shift: dict, org_id: str = ORG_ID):
    # Check for an APPROVED time-off conflict. Default policy is WARN, not block (see above) — a
    # manager can still schedule over approved time off; the response carries `timeoff_warning` so
    # the caller can surface it non-blockingly. A tenant opted into 'block' keeps the original
    # hard-409 behavior.
    eid = shift.get("employee_id")
    sdate = shift.get("shift_date")
    timeoff_warning = None
    if eid and sdate:
        conflict = (sb().table("time_off_requests").select("id").eq("org_id", org_id)
                    .eq("employee_id", str(eid)).eq("status", "approved")
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
    hours) — logs the deletion's full before-state (best-effort, never blocks the delete itself)."""
    before = dict((sb().table("shifts").select("*").eq("id", shift_id).eq("org_id", org_id)
                   .limit(1).execute().data or [{}])[0])
    sb().table("shifts").delete().eq("id", shift_id).eq("org_id", org_id).execute()
    if before:
        try:
            _log_payroll_change(org_id, field="shift_deleted", entry_point="shift_edit",
                                 employee_id=before.get("employee_id"), employee_name=before.get("employee_name"),
                                 store_code=before.get("store_code"), work_date=before.get("shift_date"),
                                 before=f"{before.get('scheduled_hours')}h scheduled ({before.get('start_time')}-{before.get('end_time')})",
                                 after=None, source_table="shifts", source_id=shift_id,
                                 who=_who_for_log(authorization, org_id))
        except Exception:
            pass
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
        rows = (sb().table("app_users").select("org_id,email,role,employee_id")
                .eq("auth_id", uid).limit(1).execute().data) or []
        return rows[0] if rows else {}
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
                 authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Returns scheduled vs actual hours per employee for payroll.

    Accepts EITHER the legacy `month` ('YYYY-MM', unchanged, still byte-identical) OR an explicit
    `start`/`end` ISO date range (both inclusive) for an arbitrary pay period — biweekly, semimonthly,
    custom — for ANY tenant (org_id stays the query-param scope, RULE ONE). start/end win if both given."""
    lo, hi = _resolve_range(month, start, end)
    # ALL employees (active OR not), not active-only: a terminated employee with REAL worked hours
    # must still be paid their real rate (2026-07-25 fix) — matches /payroll-by-store's existing
    # all-employees rate_map. Row EXISTENCE for an inactive employee is still gated on real activity
    # below (_inactive_activity_rows), so this alone does not resurrect a schedule-only phantom.
    employees = sb().table("employees").select("id,name,employee_id,pay_rate,home_store,is_active").eq("org_id", org_id).execute().data or []

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
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, r.get("store"))]
    return sorted(rows, key=lambda x: x["name"])


@router.get("/payroll-by-store")
def get_payroll_by_store(month: str = None, start: str = None, end: str = None,
                          authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-STORE payroll for a month OR an arbitrary start/end range (same precedence as /payroll —
    see _resolve_range), for the Store Expenses 'Employee Salaries' auto-fill.

    For each shift in range, hours = actual_hours where clocked else scheduled_hours (SAME basis
    as /payroll, so the numbers reconcile), pay = hours * the employee's pay_rate, attributed to the
    shift's own store_code (a floater's hours land at the store they worked). Returns one row per store:
    {store_code, hours, amount}."""
    lo, hi = _resolve_range(month, start, end)
    # All employees (active OR not) — a terminated rep who worked this month still earns; rate=0 if unknown.
    employees = sb().table("employees").select("employee_id,pay_rate,is_active").eq("org_id", org_id).execute().data or []
    rate_map = {e.get("employee_id"): float(e.get("pay_rate") or 0) for e in employees}
    inactive_ids = _inactive_ids_from(employees)

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
            hrs = float(g.get("hours_eff_sum") or 0)
            rate = rate_map.get(g.get("employee_id"), 0.0)
            d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
            d["hours"] += hrs
            d["amount"] += hrs * rate
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

    # Inactive employees: ALWAYS computed via this ONE shared, phantom-aware path (2026-07-25) — same
    # function /payroll uses, so both endpoints agree byte-for-byte on which of an inactive employee's
    # hours are "real" vs a leftover schedule-only phantom.
    real_shifts, tl_rows = _inactive_activity_rows(org_id, lo, hi, inactive_ids)
    _merge_inactive_into_by_store(by_store, rate_map, real_shifts, tl_rows)

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
    emp_rows = (sb().table("employees").select("employee_id,name,pay_rate,is_active")
                .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
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
    for r in out_days:
        r["actual_hours"] = round(r["actual_hours"], 2)
        r["scheduled_hours"] = round(r["scheduled_hours"], 2)
    total_actual = round(sum(r["actual_hours"] for r in out_days), 2)
    total_scheduled = round(sum(r["scheduled_hours"] for r in out_days), 2)

    ks = scope_keyset(authorization, org_id)
    if ks is not None and out_days:
        if not any(in_keyset(ks, d.get("store_code")) for d in out_days):
            raise HTTPException(403, "not in your scope")

    return {"employee_id": employee_id, "name": name, "pay_rate": pay_rate, "start": start, "end": end,
            "days": out_days, "total_actual_hours": total_actual, "total_scheduled_hours": total_scheduled,
            "total_manual_hours_not_in_payroll": round(total_manual_not_in_payroll, 2)}


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
    r = sb().table("stores").update(row).eq("id", store_id).eq("org_id", org_id).execute()
    if not r.data:
        raise HTTPException(404, "store not found")
    _sync_store_mapping_update(org_id, r.data[0].get("store_code"), row)
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
    eids = scope_emp_ids(authorization, org_id)   # None = unrestricted
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
def get_google_reviews_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Masked org config for the admin page — the api_key is NEVER returned raw (has_api_key +
    a trailing-4-char hint only). Any manager may view; `can_edit` tells the page whether THIS
    caller may Save (see _require_google_reviews_admin)."""
    u = _require_manager(authorization, org_id)
    org_id = u.get("org_id") or org_id
    cfg = _gr.get_config(sb(), org_id)
    out = _gr.public_config(cfg)
    try:
        _require_google_reviews_admin(authorization, "", org_id)
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
    key = (body.get("api_key") or "").strip()
    if key:
        row["api_key"] = key
    try:
        sb().table("google_review_config").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save (run migration 411 first?): {str(e)[:160]}")
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
                    "status": _gr.rating_status(rating, target), "fetched_at": snap.get("fetched_at")})
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
    return {"ok": True}


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
