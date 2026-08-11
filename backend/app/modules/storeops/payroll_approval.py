"""Two-stage weekly payroll-hours approval + disbursement routing (migration 431).

OWNER DIRECTIVE 2026-08-10, verbatim: "dm needs to approve the hours for the employees who have
worked and then the hr approves it to send to accounting or the related parties to pay it."

THE CHAIN
    computed hours  ->  DM reviews (may CORRECT, then approves)  ->  HR approves  ->  dispatch to
    whoever actually pays that employee (accounting / their stores' DM / a third-party disburser).

OWNER RULINGS taken before building — they are the reason the shapes below look the way they do:
  • The DM may correct hours inline. Every correction writes before/after/who/REASON to
    storeops.payroll_change_log (migration 414's existing trail, the same one shift edits and
    manual-hours use) so a payroll number can never move without a name and a reason attached.
  • A row missing either approval is WARNED and held out of dispatch by default, but an admin may
    override with a recorded reason — nobody misses a paycheque because a DM was on holiday.
  • The payer is a STORE default with a per-employee override, so a normal week only touches
    exceptions.

MONEY POSTURE. This module computes NO pay. It reads the hours GET /storeops/payroll already
produces, records a review decision beside them, and emails a statement to a human. The only number
it can change is `hours_approved`, which is (a) explicit, (b) reason-gated and (c) fully logged.

MULTI-TENANCY. org_id is a query param on every read AND write (AGENT_CONTRACT rule one) and every
statement filters on it. Reads are additionally span-scoped: a DM sees their own stores, an admin
sees everything.

IN-PROCESS CALL SAFETY. `_hours_for_period` calls GET /storeops/payroll's handler as a plain
function and passes `authorization=` EXPLICITLY. Omitting it would bind FastAPI's `Header(default="")`
sentinel object instead of a string — the exact class of bug that made the HR Documents board answer
"401: not authenticated" on 2026-08-10 (fix ec75567) and that broke POST /notify/send on 2026-07-17.
Never call a route handler in-process without passing its header params as real strings.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_supabase

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"

_STAGES = ("dm", "hr")
_ACTIONS = ("approve", "send_back", "reset")


def sb():
    return get_supabase().schema("storeops")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _d(v):
    """Parse an ISO date, or None."""
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return round(float(v), 2)
    except Exception:
        return None


# ── period resolution ─────────────────────────────────────────────────────────────────────────────
def _week_start_dow(org_id):
    """The tenant's work-week start as a Python weekday (0=Mon .. 6=Sun).

    storeops.tenants.work_week_start_dow follows the SQL/JS convention (0=Sunday), so it is converted
    here rather than assumed. A tenant with nothing configured falls back to Sunday, which is what
    every existing tenant row carries today."""
    try:
        rows = (sb().table("tenants").select("work_week_start_dow")
                .eq("org_id", org_id).limit(1).execute().data) or []
        raw = rows[0].get("work_week_start_dow") if rows else None
    except Exception:
        raw = None
    sql_dow = 0 if raw is None else int(raw)          # 0=Sun .. 6=Sat
    return (sql_dow - 1) % 7                           # -> 0=Mon .. 6=Sun


def previous_week(org_id, ref=None):
    """The last COMPLETE work week before `ref` (default: today). Returns (start_date, end_date).

    This is what the Monday notice asks the DM to review: the week that has finished, never the one
    in progress — half a week of hours is not a thing anyone can approve."""
    ref = ref or date.today()
    wsd = _week_start_dow(org_id)
    # Start of the week `ref` falls in, then step back one full week.
    this_start = ref - timedelta(days=(ref.weekday() - wsd) % 7)
    start = this_start - timedelta(days=7)
    return start, start + timedelta(days=6)


def _resolve_period(org_id, start, end):
    """(start, end) from explicit params, else the previous complete week."""
    s, e = _d(start), _d(end)
    if s and e:
        if e < s:
            raise HTTPException(400, "end is before start")
        return s, e
    return previous_week(org_id)


# ── payer resolution ──────────────────────────────────────────────────────────────────────────────
def _payers(org_id):
    try:
        return (sb().table("payroll_payer").select("*").eq("org_id", org_id)
                .order("name").execute().data) or []
    except Exception:
        return []      # migration 431 not applied yet — the endpoint says so rather than 500ing


def _store_payer_map(org_id):
    try:
        rows = (sb().table("payroll_store_payer").select("store_code,payer_id")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {}
    return {r["store_code"]: r["payer_id"] for r in rows if r.get("store_code")}


def _resolve_payer(row_payer_id, store_code, store_map, payers_by_id, default_payer):
    """Effective payer for one employee-period: explicit override -> their store's default -> the
    org default. Resolved at READ time so a config change is picked up without rewriting history."""
    for pid in (row_payer_id, store_map.get(store_code or "")):
        if pid and pid in payers_by_id:
            return payers_by_id[pid], ("employee" if pid == row_payer_id else "store")
    return (default_payer, "org-default") if default_payer else (None, None)


def _is_admin(authorization, org_id, who=None):
    """True for a super-admin, a full-scope role, or the 'admin' role — core's `_can_edit_setting`
    precedence, resolved from the verified JWT. Falls back to the role name from `who` if core's
    resolver is unavailable, so a transient failure denies rather than grants."""
    try:
        from app.modules.core.router import _resolve_caller, _can_edit_setting, sb as _core_sb
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if uid:
            caller = _resolve_caller(_core_sb(), uid, org_id)
            if caller and _can_edit_setting(caller, "security"):
                return True
    except Exception:
        pass
    return (who or {}).get("role", "").lower() == "admin"


def _payer_recipient(org_id, payer, store_code):
    """(email, label) for a resolved payer. A 'dm' payer with no pinned employee resolves the DM of
    the row's OWN store — that is what "send it to the dm for those stores" means."""
    if not payer:
        return None, None
    kind = payer.get("kind")
    if kind == "dm":
        pinned = (payer.get("dm_employee_id") or "").strip()
        if pinned:
            try:
                emp = (sb().table("employees").select("name,email").eq("org_id", org_id)
                       .eq("employee_id", pinned).limit(1).execute().data) or []
                if emp and emp[0].get("email"):
                    return emp[0]["email"], f"{payer.get('name')} · {emp[0].get('name') or pinned}"
            except Exception:
                pass
            return None, payer.get("name")
        from app.modules.storeops.router import _dm_for_store
        _eid, email, name = _dm_for_store(org_id, store_code)
        return email, (f"{payer.get('name')} · {name}" if name else payer.get("name"))
    return (payer.get("email") or None), payer.get("name")


# ── the review list ───────────────────────────────────────────────────────────────────────────────
def _hours_for_period(org_id, start, end, authorization):
    """Rows from GET /storeops/payroll for the period — already span-scoped to the caller.

    `authorization` is passed EXPLICITLY and must be a real string (see the module docstring)."""
    from app.modules.storeops.router import get_payroll
    rows = get_payroll(month=None, start=start.isoformat(), end=end.isoformat(),
                       authorization=(authorization or ""), org_id=org_id, response=None)
    return rows or []


def _existing(org_id, start, end):
    try:
        rows = (sb().table("payroll_approval").select("*").eq("org_id", org_id)
                .eq("period_start", start.isoformat()).eq("period_end", end.isoformat())
                .execute().data) or []
    except Exception:
        return None        # migration 431 not applied
    return {r["employee_id"]: r for r in rows}


@router.get("/payroll/approvals")
def list_approvals(start: str = "", end: str = "", store_code: str = "", market: str = "",
                   employee_id: str = "", status: str = "",
                   authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The review board: everyone who WORKED in the period, their hours, both approval stages, and who
    is set to pay them.

    Filters mirror the standard bar (RULE FIVE): store_code / market / employee_id are comma-separated
    multi-selects; `status` is one of pending_dm | pending_hr | approved | sent_back | held.
    Span-scoped — a DM sees their stores, an admin sees all."""
    s, e = _resolve_period(org_id, start, end)
    hours = _hours_for_period(org_id, s, e, authorization)
    saved = _existing(org_id, s, e)
    if saved is None:
        return {"ready": False, "period_start": s.isoformat(), "period_end": e.isoformat(),
                "rows": [], "payers": [],
                "note": "Run migration 431 to activate payroll approvals."}

    payers = _payers(org_id)
    by_id = {p["id"]: p for p in payers}
    default_payer = next((p for p in payers if p.get("is_default")), None)
    store_map = _store_payer_map(org_id)

    want_stores = {x.strip() for x in store_code.split(",") if x.strip()}
    want_markets = {x.strip().lower() for x in market.split(",") if x.strip()}
    want_emps = {x.strip() for x in employee_id.split(",") if x.strip()}

    # market lookup, only when a market filter is actually in play
    market_of = {}
    if want_markets:
        try:
            for st in (sb().table("stores").select("store_code,market").eq("org_id", org_id)
                       .execute().data) or []:
                market_of[st.get("store_code")] = (st.get("market") or "").lower()
        except Exception:
            market_of = {}

    out = []
    for h in hours:
        eid = h.get("employee_id")
        if not eid:
            continue
        st = h.get("store") or ""
        if want_stores and st not in want_stores:
            continue
        if want_markets and market_of.get(st, "") not in want_markets:
            continue
        if want_emps and eid not in want_emps:
            continue

        row = saved.get(eid) or {}
        # ⚠️ `actual_hours` arrives ALREADY NET of the lunch deduction (router.py subtracts it and
        # reports what it took in `lunch_deduction_hours`). Treating it as gross and subtracting
        # lunch again here would short every shift by the deduction — 30 minutes a day on luxelink.
        src = _num(h.get("actual_hours"))
        lunch = _num(h.get("lunch_deduction_hours")) or 0.0
        worked_gross = round((src or 0.0) + lunch, 2)
        adj = _num(row.get("adjustment_hours")) or 0.0
        payable = round((src or 0.0) + adj, 2)      # ≡ worked_gross − lunch + adj, without the double count
        approved = _num(row.get("hours_approved"))
        # The DM approves the PAYABLE figure; an explicit hours_approved override still wins over it.
        effective = approved if approved is not None else payable
        payer, payer_from = _resolve_payer(row.get("payer_id"), st, store_map, by_id, default_payer)
        rate = _num(h.get("pay_rate")) or 0.0
        dm_s = row.get("dm_status") or "pending"
        hr_s = row.get("hr_status") or "pending"
        blocked = not (dm_s == "approved" and hr_s == "approved")
        overridden = bool(row.get("override_by"))

        out.append({
            "employee_id": eid, "name": h.get("name"), "store": st,
            "scheduled_hours": _num(h.get("scheduled_hours")),
            "hours_source": src, "hours_approved": approved, "hours_effective": effective,
            "hours_corrected": approved is not None and src is not None and approved != src,
            # The four columns the owner asked for, in the order they read on the board.
            "hours_worked": worked_gross,          # gross, before the lunch deduction
            "lunch_hours": round(lunch, 2),        # what the previous screen already took out
            "adjustment_hours": round(adj, 2),
            "adjustment_reason": row.get("adjustment_reason"),
            "hours_payable": payable,              # worked − lunch + adjustment = what gets approved
            # A punch or lunch-config edit AFTER sign-off would otherwise restate an approved week
            # in silence; report the drift instead of hiding it.
            "worked_at_approval": _num(row.get("worked_at_approval")),
            "lunch_at_approval": _num(row.get("lunch_at_approval")),
            "hours_drifted": bool(
                row.get("worked_at_approval") is not None
                and abs((_num(row.get("worked_at_approval")) or 0.0) - worked_gross) > 0.001),
            "pay_rate": rate,
            "pay_effective": round((effective or 0) * rate, 2),
            "dm_status": dm_s, "dm_by": row.get("dm_by"), "dm_at": row.get("dm_at"), "dm_note": row.get("dm_note"),
            "hr_status": hr_s, "hr_by": row.get("hr_by"), "hr_at": row.get("hr_at"), "hr_note": row.get("hr_note"),
            "payer_id": row.get("payer_id"), "payer_name": (payer or {}).get("name"),
            "payer_kind": (payer or {}).get("kind"), "payer_from": payer_from,
            "override_by": row.get("override_by"), "override_reason": row.get("override_reason"),
            "dispatch_status": row.get("dispatch_status") or "none",
            "dispatched_at": row.get("dispatched_at"), "dispatch_to": row.get("dispatch_to"),
            # `held` is the loud warning: not fully approved AND not overridden => will not be sent.
            "held": blocked and not overridden,
            "payable": (not blocked) or overridden,
        })

    if status:
        keep = {
            "pending_dm": lambda r: r["dm_status"] == "pending",
            "pending_hr": lambda r: r["dm_status"] == "approved" and r["hr_status"] == "pending",
            "approved": lambda r: r["dm_status"] == "approved" and r["hr_status"] == "approved",
            "sent_back": lambda r: "sent_back" in (r["dm_status"], r["hr_status"]),
            "held": lambda r: r["held"],
        }.get(status)
        if keep:
            out = [r for r in out if keep(r)]

    out.sort(key=lambda r: (r.get("store") or "", r.get("name") or ""))
    totals = {
        "employees": len(out),
        "hours": round(sum(r["hours_effective"] or 0 for r in out), 2),
        "lunch_hours": round(sum(r["lunch_hours"] or 0 for r in out), 2),
        "adjustment_hours": round(sum(r["adjustment_hours"] or 0 for r in out), 2),
        "pay": round(sum(r["pay_effective"] or 0 for r in out), 2),
        "pending_dm": sum(1 for r in out if r["dm_status"] == "pending"),
        "pending_hr": sum(1 for r in out if r["dm_status"] == "approved" and r["hr_status"] == "pending"),
        "held": sum(1 for r in out if r["held"]),
        "payable_pay": round(sum(r["pay_effective"] or 0 for r in out if r["payable"]), 2),
    }
    return {"ready": True, "period_start": s.isoformat(), "period_end": e.isoformat(),
            "rows": out, "totals": totals,
            "payers": [{"id": p["id"], "name": p["name"], "kind": p["kind"],
                        "email": p.get("email"), "is_default": p.get("is_default")}
                       for p in payers if p.get("is_active") is not False]}


# ── decisions ─────────────────────────────────────────────────────────────────────────────────────
def _base_row(org_id, s, e, eid, store, src):
    return {"org_id": org_id, "period_start": s.isoformat(), "period_end": e.isoformat(),
            "employee_id": eid, "store_code": store, "hours_source": src, "updated_at": _now()}


@router.post("/payroll/approvals/decide")
def decide(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Record DM or HR decisions for one or more employees.

    Body: {stage:'dm'|'hr', period_start, period_end, rows:[{employee_id, action:'approve'|
    'send_back'|'reset', hours_approved?, reason?, note?}]}

    A `hours_approved` that differs from the computed value is a CORRECTION: it requires a reason and
    is written to storeops.payroll_change_log. HR cannot approve a row the DM has not approved — the
    chain is the point of the feature, so it is enforced server-side, not just hidden in the UI.
    Management-gated (the same tier as every other hours-affecting write)."""
    from app.modules.storeops.router import _require_manager, _log_payroll_change

    stage = (body.get("stage") or "").strip().lower()
    if stage not in _STAGES:
        raise HTTPException(400, "stage must be 'dm' or 'hr'")
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows[] required")

    who = _require_manager(authorization, org_id) or {}
    actor = (who.get("email") or "").strip() or "unknown"
    s, e = _resolve_period(org_id, body.get("period_start"), body.get("period_end"))

    hours = {h.get("employee_id"): h for h in _hours_for_period(org_id, s, e, authorization)}
    saved = _existing(org_id, s, e)
    if saved is None:
        raise HTTPException(400, "payroll approvals not installed — run migration 431")

    applied, errors = [], []
    for r in rows:
        eid = str(r.get("employee_id") or "").strip()
        action = (r.get("action") or "approve").strip().lower()
        if not eid:
            errors.append({"employee_id": None, "error": "employee_id required"})
            continue
        if action not in _ACTIONS:
            errors.append({"employee_id": eid, "error": f"unknown action '{action}'"})
            continue
        h = hours.get(eid)
        if not h:
            errors.append({"employee_id": eid, "error": "did not work in this period (or outside your span)"})
            continue

        cur = saved.get(eid) or {}
        if stage == "hr" and action == "approve" and (cur.get("dm_status") != "approved"):
            errors.append({"employee_id": eid,
                           "error": "the DM has not approved these hours yet — HR is the second gate"})
            continue

        src = _num(h.get("actual_hours"))
        lunch = _num(h.get("lunch_deduction_hours")) or 0.0
        worked_gross = round((src or 0.0) + lunch, 2)
        row = _base_row(org_id, s, e, eid, h.get("store"), src)

        # ── adjustment (DM only) ──────────────────────────────────────────────────────────────────
        # Same gate as a correction: it moves a payroll number, so it needs a name and a reason.
        # Carried forward when this call doesn't mention it, so an HR approval cannot quietly drop
        # the DM's adjustment back to zero.
        adj = _num(cur.get("adjustment_hours")) or 0.0
        if stage == "dm" and "adjustment_hours" in r:
            new_adj = _num(r.get("adjustment_hours"))
            if new_adj is None:
                new_adj = 0.0
            a_reason = (r.get("adjustment_reason") or r.get("reason") or "").strip()
            if new_adj != adj and not a_reason:
                errors.append({"employee_id": eid,
                               "error": "an hours adjustment needs a reason — it moves a payroll number"})
                continue
            if (src or 0.0) + new_adj < 0:
                errors.append({"employee_id": eid,
                               "error": "that adjustment would make payable hours negative"})
                continue
            if new_adj != adj:
                _log_payroll_change(
                    org_id, field="adjustment_hours", entry_point="payroll_approval",
                    employee_id=eid, employee_name=h.get("name"), store_code=h.get("store"),
                    work_date=e, before=adj, after=new_adj,
                    source_table="payroll_approval", source_id=cur.get("id"),
                    who=who, reason=a_reason)
            adj = new_adj
            row["adjustment_reason"] = a_reason or cur.get("adjustment_reason")
        row["adjustment_hours"] = adj

        # ── correction (DM only) ──────────────────────────────────────────────────────────────────
        if stage == "dm" and "hours_approved" in r and r.get("hours_approved") not in ("", None):
            new_hours = _num(r.get("hours_approved"))
            if new_hours is None or new_hours < 0:
                errors.append({"employee_id": eid, "error": "hours_approved must be a number >= 0"})
                continue
            reason = (r.get("reason") or "").strip()
            if new_hours != src and not reason:
                errors.append({"employee_id": eid,
                               "error": "changing the hours needs a reason — it moves a payroll number"})
                continue
            row["hours_approved"] = new_hours
            if new_hours != src:
                _log_payroll_change(
                    org_id, field="approved_hours", entry_point="payroll_approval",
                    employee_id=eid, employee_name=h.get("name"), store_code=h.get("store"),
                    work_date=e, before=src, after=new_hours,
                    source_table="payroll_approval", source_id=cur.get("id"),
                    who=who, reason=reason)

        # Freeze the figures behind a DM sign-off. Punches and lunch config both change after the
        # fact; without this an approved week silently restates itself and nobody can tell what the
        # DM actually approved. Cleared on reset so a re-approval snapshots afresh.
        if stage == "dm":
            if action == "approve":
                row["worked_at_approval"] = worked_gross
                row["lunch_at_approval"] = round(lunch, 2)
            elif action == "reset":
                row["worked_at_approval"] = None
                row["lunch_at_approval"] = None

        stamp = {"approve": "approved", "send_back": "sent_back", "reset": "pending"}[action]
        row[f"{stage}_status"] = stamp
        row[f"{stage}_by"] = actor if action != "reset" else None
        row[f"{stage}_at"] = _now() if action != "reset" else None
        if r.get("note"):
            row[f"{stage}_note"] = str(r["note"])[:500]

        # A DM un-approving or bouncing a row invalidates any HR approval sitting on top of it —
        # otherwise HR's sign-off would survive the removal of the thing it was signing off on.
        if stage == "dm" and action != "approve" and (cur.get("hr_status") == "approved"):
            row.update({"hr_status": "pending", "hr_by": None, "hr_at": None,
                        "hr_note": "reset — the DM withdrew their approval"})

        try:
            sb().table("payroll_approval").upsert(
                row, on_conflict="org_id,period_start,period_end,employee_id").execute()
            applied.append({"employee_id": eid, "stage": stage, "status": stamp})
        except Exception as ex:
            errors.append({"employee_id": eid, "error": str(ex)[:200]})

    return {"period_start": s.isoformat(), "period_end": e.isoformat(),
            "applied": len(applied), "results": applied, "errors": errors}


@router.post("/payroll/approvals/payer")
def set_payer(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Route employees to a payer for THIS period. Body: {period_start, period_end, employee_ids[],
    payer_id | null}. null clears the override so the row falls back to its store's default."""
    from app.modules.storeops.router import _require_manager
    _require_manager(authorization, org_id)
    ids = [str(i).strip() for i in (body.get("employee_ids") or []) if str(i).strip()]
    if not ids:
        raise HTTPException(400, "employee_ids[] required")
    payer_id = body.get("payer_id") or None
    if payer_id and not any(p["id"] == payer_id for p in _payers(org_id)):
        raise HTTPException(400, "unknown payer for this company")
    s, e = _resolve_period(org_id, body.get("period_start"), body.get("period_end"))
    hours = {h.get("employee_id"): h for h in _hours_for_period(org_id, s, e, authorization)}
    n = 0
    for eid in ids:
        h = hours.get(eid)
        if not h:
            continue
        row = _base_row(org_id, s, e, eid, h.get("store"), _num(h.get("actual_hours")))
        row["payer_id"] = payer_id
        sb().table("payroll_approval").upsert(
            row, on_conflict="org_id,period_start,period_end,employee_id").execute()
        n += 1
    return {"updated": n, "payer_id": payer_id}


@router.post("/payroll/approvals/override")
def override(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Admin escape hatch: include an employee in the payout despite a missing approval.

    Body: {period_start, period_end, employee_ids[], reason}. The reason is REQUIRED and recorded
    against the admin — this is the documented key to the gate, not a way around it. Pass
    {clear:true} to withdraw an override."""
    from app.modules.storeops.router import _require_manager, _log_payroll_change
    who = _require_manager(authorization, org_id) or {}
    # _require_manager admits every manager tier and returns no super_admin flag, so the admin test is
    # resolved through core (super_admin -> full-scope role -> the 'admin' role), the SAME precedence
    # every other admin-only control in the product uses. Checking `who["super_admin"]` here would
    # have been a silent always-false: that key is not in _require_manager's payload.
    if not _is_admin(authorization, org_id, who):
        raise HTTPException(403, "only an admin can pay hours that were never approved")
    ids = [str(i).strip() for i in (body.get("employee_ids") or []) if str(i).strip()]
    if not ids:
        raise HTTPException(400, "employee_ids[] required")
    clear = bool(body.get("clear"))
    reason = (body.get("reason") or "").strip()
    if not clear and not reason:
        raise HTTPException(400, "a reason is required to pay unapproved hours")
    s, e = _resolve_period(org_id, body.get("period_start"), body.get("period_end"))
    hours = {h.get("employee_id"): h for h in _hours_for_period(org_id, s, e, authorization)}
    actor = (who.get("email") or "").strip() or "unknown"
    n = 0
    for eid in ids:
        h = hours.get(eid)
        if not h:
            continue
        row = _base_row(org_id, s, e, eid, h.get("store"), _num(h.get("actual_hours")))
        row.update({"override_by": None if clear else actor,
                    "override_at": None if clear else _now(),
                    "override_reason": None if clear else reason})
        sb().table("payroll_approval").upsert(
            row, on_conflict="org_id,period_start,period_end,employee_id").execute()
        _log_payroll_change(org_id, field=("approval_override_cleared" if clear else "approval_override"),
                            entry_point="payroll_approval", employee_id=eid,
                            employee_name=h.get("name"), store_code=h.get("store"), work_date=e,
                            before=None, after=("cleared" if clear else "included without approval"),
                            source_table="payroll_approval", who=who, reason=reason or None)
        n += 1
    return {"updated": n, "cleared": clear}


# ── payer registry (RULE TWO: config, not code · RULE THREE: the UI picks from this) ──────────────
@router.get("/payroll/payers")
def list_payers(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Who can be paid BY. Plus the per-store default map, so one call fills the whole config screen."""
    try:
        rows = (sb().table("payroll_payer").select("*").eq("org_id", org_id).order("name")
                .execute().data)
    except Exception:
        return {"ready": False, "payers": [], "stores": {},
                "note": "Run migration 431 to activate payroll approvals."}
    return {"ready": True, "payers": rows or [], "stores": _store_payer_map(org_id)}


@router.post("/payroll/payers")
def create_payer(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Add a payer. Body: {name, kind:'accounting'|'dm'|'third_party', email?, phone?,
    dm_employee_id?, note?, is_default?}.

    An 'accounting' or 'third_party' payer needs an email — it is the address the payout statement is
    sent to, and a payer that cannot be reached is not a payer. A 'dm' payer may omit it: the DM of
    each row's own store is resolved at send time."""
    from app.modules.storeops.router import _require_manager
    who = _require_manager(authorization, org_id) or {}
    if not _is_admin(authorization, org_id, who):
        raise HTTPException(403, "only an admin can change who payroll is paid by")
    name = (body.get("name") or "").strip()
    kind = (body.get("kind") or "").strip().lower()
    if not name:
        raise HTTPException(400, "name required")
    if kind not in ("accounting", "dm", "third_party"):
        raise HTTPException(400, "kind must be accounting, dm or third_party")
    email = (body.get("email") or "").strip().lower() or None
    if kind in ("accounting", "third_party") and not email:
        raise HTTPException(400, f"a '{kind}' payer needs an email — that is where the payout statement goes")
    row = {"org_id": org_id, "name": name, "kind": kind, "email": email,
           "phone": (body.get("phone") or "").strip() or None,
           "dm_employee_id": (body.get("dm_employee_id") or "").strip() or None,
           "note": (body.get("note") or "").strip() or None,
           "created_by": (who.get("email") or "")[:200] or None}
    if body.get("is_default"):
        _clear_default(org_id)
        row["is_default"] = True
    try:
        return (sb().table("payroll_payer").insert(row).execute().data or [{}])[0]
    except Exception as e:
        raise HTTPException(400, f"could not save that payer: {str(e)[:200]}")


def _clear_default(org_id):
    """Only one default per org (a partial unique index enforces it) — clear the incumbent first."""
    try:
        sb().table("payroll_payer").update({"is_default": False}) \
            .eq("org_id", org_id).eq("is_default", True).execute()
    except Exception:
        pass


@router.patch("/payroll/payers/{payer_id}")
def update_payer(payer_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Edit a payer. Only the fields present are touched. Setting is_default demotes the incumbent."""
    from app.modules.storeops.router import _require_manager
    who = _require_manager(authorization, org_id) or {}
    if not _is_admin(authorization, org_id, who):
        raise HTTPException(403, "only an admin can change who payroll is paid by")
    patch = {}
    for k in ("name", "email", "phone", "dm_employee_id", "note"):
        if k in body:
            patch[k] = (str(body[k]).strip() or None)
    if "kind" in body:
        if body["kind"] not in ("accounting", "dm", "third_party"):
            raise HTTPException(400, "kind must be accounting, dm or third_party")
        patch["kind"] = body["kind"]
    if "is_active" in body:
        patch["is_active"] = bool(body["is_active"])
    if body.get("is_default"):
        _clear_default(org_id)
        patch["is_default"] = True
    elif "is_default" in body:
        patch["is_default"] = False
    if not patch:
        raise HTTPException(400, "nothing to update")
    res = (sb().table("payroll_payer").update(patch)
           .eq("org_id", org_id).eq("id", payer_id).execute().data) or []
    if not res:
        raise HTTPException(404, "payer not found for this company")
    return res[0]


@router.delete("/payroll/payers/{payer_id}")
def delete_payer(payer_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Remove a payer. Stores routed to it fall back to the org default (the FK cascades the store
    map); approval rows keep their history and resolve to the default on the next read."""
    from app.modules.storeops.router import _require_manager
    who = _require_manager(authorization, org_id) or {}
    if not _is_admin(authorization, org_id, who):
        raise HTTPException(403, "only an admin can change who payroll is paid by")
    sb().table("payroll_payer").delete().eq("org_id", org_id).eq("id", payer_id).execute()
    return {"deleted": payer_id}


@router.put("/payroll/store-payers")
def set_store_payers(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Set the DEFAULT payer for one or more stores. Body: {stores:{store_code: payer_id|null}}.
    null removes the mapping so the store falls back to the org default."""
    from app.modules.storeops.router import _require_manager
    who = _require_manager(authorization, org_id) or {}
    if not _is_admin(authorization, org_id, who):
        raise HTTPException(403, "only an admin can change who payroll is paid by")
    stores = body.get("stores")
    if not isinstance(stores, dict) or not stores:
        raise HTTPException(400, "stores{} required")
    valid = {p["id"] for p in _payers(org_id)}
    actor = (who.get("email") or "")[:200] or None
    n = 0
    for code, pid in stores.items():
        code = str(code).strip()
        if not code:
            continue
        if pid:
            if pid not in valid:
                raise HTTPException(400, f"unknown payer for store {code}")
            sb().table("payroll_store_payer").upsert(
                {"org_id": org_id, "store_code": code, "payer_id": pid,
                 "updated_at": _now(), "updated_by": actor},
                on_conflict="org_id,store_code").execute()
        else:
            sb().table("payroll_store_payer").delete() \
                .eq("org_id", org_id).eq("store_code", code).execute()
        n += 1
    return {"updated": n}


# ── dispatch: hand each payer their own list ──────────────────────────────────────────────────────
def _statement_html(tenant_name, payer_label, s, e, rows, total_hours, total_pay):
    """The payout statement one payer receives. Deliberately plain: a table someone can act on, with
    every corrected or overridden line called out rather than quietly blended in."""
    tr = []
    for r in rows:
        flags = []
        if r.get("hours_corrected"):
            flags.append(f"corrected from {r.get('hours_source')}")
        if r.get("override_reason"):
            flags.append(f"PAID WITHOUT FULL APPROVAL — {r['override_reason']}")
        note = (f"<div style='font-size:11px;color:#b45309'>{' · '.join(flags)}</div>" if flags else "")
        tr.append(
            f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{r.get('name') or r['employee_id']}"
            f"{note}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{r.get('store') or ''}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right'>{r.get('hours_effective') or 0}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right'>${r.get('pay_effective') or 0:,.2f}</td></tr>")
    return f"""
<div style="font-family:system-ui,-apple-system,sans-serif;color:#0f172a">
  <h2 style="margin:0 0 4px;font-size:18px">Payroll to disburse — {tenant_name}</h2>
  <div style="color:#475569;font-size:13px;margin-bottom:14px">
    Week of <b>{s}</b> to <b>{e}</b> · for <b>{payer_label}</b><br>
    Hours below were approved by the district manager and then by HR.
  </div>
  <table style="border-collapse:collapse;font-size:13px;min-width:520px">
    <thead><tr style="background:#f1f5f9">
      <th style="padding:6px 10px;text-align:left">Employee</th>
      <th style="padding:6px 10px;text-align:left">Store</th>
      <th style="padding:6px 10px;text-align:right">Hours</th>
      <th style="padding:6px 10px;text-align:right">Pay</th>
    </tr></thead>
    <tbody>{''.join(tr)}</tbody>
    <tfoot><tr style="font-weight:700">
      <td style="padding:8px 10px" colspan="2">{len(rows)} employee(s)</td>
      <td style="padding:8px 10px;text-align:right">{total_hours}</td>
      <td style="padding:8px 10px;text-align:right">${total_pay:,.2f}</td>
    </tr></tfoot>
  </table>
</div>"""


@router.post("/payroll/approvals/dispatch")
async def dispatch(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HR's final action: send each payer the employees THEY pay.

    Body: {period_start, period_end, employee_ids?[] (default: every payable row), dry_run?}.
    Only rows that are DM-approved AND HR-approved — or explicitly overridden — are ever sent; a held
    row is reported back, never quietly included. Grouped by payer so each party gets one statement.
    """
    from app.modules.storeops.router import _require_manager
    who = _require_manager(authorization, org_id) or {}
    if not _is_admin(authorization, org_id, who):
        raise HTTPException(403, "only an admin or HR lead can send payroll to the payers")
    s, e = _resolve_period(org_id, body.get("period_start"), body.get("period_end"))
    listing = list_approvals(start=s.isoformat(), end=e.isoformat(),
                             authorization=authorization, org_id=org_id)
    if not listing.get("ready"):
        raise HTTPException(400, listing.get("note") or "payroll approvals not installed")
    only = {str(i).strip() for i in (body.get("employee_ids") or []) if str(i).strip()}
    rows = [r for r in listing["rows"] if (not only or r["employee_id"] in only)]
    held = [r for r in rows if not r["payable"]]
    payable = [r for r in rows if r["payable"]]

    try:
        tname = ((sb().table("tenants").select("name").eq("org_id", org_id).limit(1)
                  .execute().data) or [{}])[0].get("name") or "your company"
    except Exception:
        tname = "your company"

    # group by the RESOLVED recipient, not by payer id: two stores routed to "their own DM" are two
    # different people and must not be blended into one statement.
    groups = {}
    unroutable = []
    payers_by_id = {p["id"]: p for p in _payers(org_id)}
    store_map = _store_payer_map(org_id)
    default_payer = next((p for p in _payers(org_id) if p.get("is_default")), None)
    for r in payable:
        payer, _from = _resolve_payer(r.get("payer_id"), r.get("store"), store_map,
                                      payers_by_id, default_payer)
        email, label = _payer_recipient(org_id, payer, r.get("store"))
        if not email:
            unroutable.append({**r, "why": ("no payer configured for this store — set a default payer"
                                            if not payer else f"{label or payer.get('name')} has no email / no DM resolved")})
            continue
        groups.setdefault(email, {"label": label or email, "rows": []})["rows"].append(r)

    if body.get("dry_run"):
        return {"period_start": s.isoformat(), "period_end": e.isoformat(), "dry_run": True,
                "would_send": [{"to": k, "label": v["label"], "employees": len(v["rows"]),
                                "pay": round(sum(x["pay_effective"] or 0 for x in v["rows"]), 2)}
                               for k, v in groups.items()],
                "held": held, "unroutable": unroutable}

    from app.modules.notify.channels.email_resend import send_email, is_configured
    if not is_configured():
        raise HTTPException(400, "email is not configured (RESEND_API_KEY unset) — cannot send statements")

    sent, failed = [], []
    for email, g in groups.items():
        th = round(sum(x["hours_effective"] or 0 for x in g["rows"]), 2)
        tp = round(sum(x["pay_effective"] or 0 for x in g["rows"]), 2)
        html = _statement_html(tname, g["label"], s.isoformat(), e.isoformat(), g["rows"], th, tp)
        try:
            await send_email(email, f"Payroll to disburse — {tname} — week of {s.isoformat()}", html)
            stamp = {"dispatch_status": "sent", "dispatched_at": _now(),
                     "dispatch_to": email, "dispatch_error": None}
            sent.append({"to": email, "employees": len(g["rows"]), "pay": tp})
        except Exception as ex:
            stamp = {"dispatch_status": "failed", "dispatched_at": _now(),
                     "dispatch_to": email, "dispatch_error": str(ex)[:300]}
            failed.append({"to": email, "error": str(ex)[:200]})
        for r in g["rows"]:
            try:
                sb().table("payroll_approval").upsert(
                    {**_base_row(org_id, s, e, r["employee_id"], r.get("store"), r.get("hours_source")),
                     **stamp}, on_conflict="org_id,period_start,period_end,employee_id").execute()
            except Exception:
                pass
    return {"period_start": s.isoformat(), "period_end": e.isoformat(),
            "sent": sent, "failed": failed, "held": held, "unroutable": unroutable}


# ── Monday morning: tell each DM their week is waiting ────────────────────────────────────────────
@router.post("/payroll/approvals/run-weekly-notice")
async def run_weekly_notice(x_notify_secret: str = Header(default=""), eval_date: str = ""):
    """pg_cron entrypoint — schedule WEEKLY on Monday morning (same NOTIFY_RUN_SECRET guard as the
    notify / closing / asset / hr-letters sweeps).

    For every tenant, resolves LAST week's hours, groups the not-yet-approved employees by the DM of
    their store, and emails each DM only their own list. A DM with nothing pending is not emailed —
    a weekly "you have 0 things to do" trains people to ignore the alert.

    Idempotent: it sends a reminder, it writes no decision, so a retry is harmless."""
    from app.core.config import settings
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    from app.modules.notify.channels.email_resend import send_email, is_configured
    from app.modules.storeops.router import _dm_for_store

    try:
        tens = (sb().table("tenants").select("org_id,name").execute().data) or []
    except Exception as ex:
        return {"tenants_checked": 0, "results": [], "note": f"tenants unreadable: {str(ex)[:200]}"}

    ref = _d(eval_date) or date.today()
    results = []
    for t in tens:
        oid, tname = t.get("org_id"), (t.get("name") or "your company")
        s, e = previous_week(oid, ref)
        try:
            # No caller: "" is a REAL empty string, deliberately — the org-wide path. Passing nothing
            # would bind the Header sentinel (see the module docstring).
            listing = list_approvals(start=s.isoformat(), end=e.isoformat(),
                                     authorization="", org_id=oid)
        except Exception as ex:
            results.append({"org_id": oid, "error": str(ex)[:200]})
            continue
        if not listing.get("ready"):
            results.append({"org_id": oid, "skipped": "migration 431 not applied"})
            continue
        pending = [r for r in listing["rows"] if r["dm_status"] == "pending"]
        if not pending:
            results.append({"org_id": oid, "pending": 0, "emailed": 0})
            continue

        by_dm = {}
        for r in pending:
            _eid, email, name = _dm_for_store(oid, r.get("store"))
            if not email:
                continue
            by_dm.setdefault(email, {"name": name, "rows": []})["rows"].append(r)

        emailed = 0
        if is_configured():
            for email, g in by_dm.items():
                hrs = round(sum(x["hours_effective"] or 0 for x in g["rows"]), 2)
                stores = sorted({x.get("store") or "—" for x in g["rows"]})
                html = f"""
<div style="font-family:system-ui,-apple-system,sans-serif;color:#0f172a">
  <h2 style="margin:0 0 4px;font-size:18px">Approve last week's hours</h2>
  <div style="color:#475569;font-size:13px;line-height:1.6">
    Good morning{(' ' + g['name']) if g.get('name') else ''} — the week of
    <b>{s.isoformat()}</b> to <b>{e.isoformat()}</b> has closed and
    <b>{len(g['rows'])} employee(s)</b> across {', '.join(stores)} are waiting on you
    ({hrs} hours in total).<br><br>
    Check the hours, correct anything that is wrong, and tick them approved. HR cannot send payroll
    on for payment until you do.
  </div>
  <p style="margin:18px 0">
    <a href="{settings.APP_PUBLIC_URL}/storeops/payroll/approvals?start={s.isoformat()}&end={e.isoformat()}"
       style="background:#1e3a5f;color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;
              font-weight:600;font-size:14px">Review last week's hours</a>
  </p>
</div>"""
                try:
                    await send_email(email, f"Approve last week's hours — {tname}", html)
                    emailed += 1
                except Exception:
                    pass
        results.append({"org_id": oid, "period": f"{s.isoformat()}..{e.isoformat()}",
                        "pending": len(pending), "dms": len(by_dm), "emailed": emailed})
    return {"tenants_checked": len(tens), "results": results}
