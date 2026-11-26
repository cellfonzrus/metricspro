"""THE ENDPOINTS for Employee Setup as a guided, resumable flow (index §STOREOPS-13).

Owner 2026-11-25: *"need similar for the Employee set up in logical steps leading up to onboaring"*,
said of the daily-closing workflow a new person can start and come back to.

EVERY DECISION ALREADY LIVES IN A PURE MODULE AND IS DEREFERENCED HERE, never re-derived:

    the rail, lamps, resume, progress        `core/guided_workflow`            (index §CORE-51)
    what each of the six steps means         `storeops/employee_setup_flow`    (index §STOREOPS-13)
    "priced at $0.00 by a payroll run"       `storeops/clock_variance`         (index §14z)
    the six onboarding stage words           `hr/onboarding_stages`            (index §STOREOPS-13)
    which plan is assigned to a person       `commcalc/router.commission_plan_roster`
    is this person still active              `core/roster_visibility`          (index §CORE-45)
    who may read which people                `storeops/router.gate_person_rows`

So this file is I/O and HTTP shape, and NOTHING here writes. A setup wizard that typed an
employee's details again would be a second writer for every field that already has one; the screen
this feeds sends people to the doors that already exist. "Resume where you left off" needs no saved
draft when the state IS the data — there is nothing to half-save.

THE TENANT RULE (CLAUDE.md, owner 2026-10-30). An organisation's ROLE NAMES are its own words, so
they are read per organisation from `storeops.roles` and passed to the flow as a required argument.
An organisation that has declared none is told so (`declared.roles: []` and the step says it) — the
flow can refuse rather than measure one tenant's people against another tenant's vocabulary. The
two POLICY declarations beside it (does this tenant pay incentive plans, do its hours come from the
clock) come from the existing config homes, where a house default is legitimate.

DEGRADES BY NAME, NEVER 500s. Each read reports itself in `blockers` when it fails, because "we
could not look" and "there is nothing to set up" must never print the same (absence is never zero).
"""
from fastapi import APIRouter, Header, HTTPException, Response

from app.core.database import get_supabase
from app.modules.core import guided_workflow as gw
from app.modules.core import roster_visibility as _rv
from app.modules.storeops import employee_setup_flow as _flow
from app.modules.storeops import payroll_hours_basis as _hours_basis

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"

#: The roster columns this surface needs. Pay columns ride the existing ladder reader, so a database
#: missing `pay_basis` / `pay_amount` (pre-mig 1072) narrows the answer instead of failing.
EMP_FIELDS = ("id,employee_id,name,home_store,role,is_active,email,phone,hire_date,pay_rate,epay_salesperson")


def sb():
    """SCHEMA-SCOPED to `storeops`, like `storeops/router.sb()`. An unscoped client reads `public`,
    finds nothing, and renders an empty board — absence showing up as a clean screen."""
    return get_supabase().schema("storeops")


def sb_public():
    """The UNSCOPED client, for the helpers that scope themselves. Named so it is deliberate."""
    return get_supabase()


def _s(v) -> str:
    return str(v or "").strip()


def _rows(table, select, org_id, blockers, **eqs):
    """A failed read is REPORTED, never silently empty."""
    try:
        q = sb().table(table).select(select).eq("org_id", org_id)
        for k, v in eqs.items():
            q = q.eq(k, v)
        return q.execute().data or []
    except Exception as e:
        blockers.append({"table": table, "reason": type(e).__name__})
        return []


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE ORGANISATION'S DECLARATIONS — its own words, and the two policies that change which steps apply
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _declarations(org_id, blockers) -> dict:
    """What this organisation has declared. `roles` is ITS OWN VOCABULARY and has no house default.

    `hours_from_clock` asks whether the clock is what pays somebody here: in `clocked_only` it
    always is, and in `schedule_fallback` it is unless the org pays from the schedule by default
    (mig 1073, §14z). It changes only WHETHER the login step mentions clocking in — never a word.
    """
    role_rows = _rows("roles", "name,display_name", org_id, blockers)
    roles = []
    for r in role_rows:
        name = _s(r.get("display_name")) or _s(r.get("name"))
        if name and name not in roles:
            roles.append(name)

    plans_declared, plan_blocked = False, False
    try:
        plans = (sb_public().schema("commcalc").table("commission_plan")
                 .select("id").eq("org_id", org_id).limit(1).execute().data) or []
        plans_declared = bool(plans)
    except Exception as e:
        plan_blocked = True
        blockers.append({"table": "commission_plan", "reason": type(e).__name__})

    try:
        cfg = _hours_basis.load_config(sb(), org_id)
    except Exception as e:
        blockers.append({"table": "payroll_hours_basis_config", "reason": type(e).__name__})
        cfg = dict(_hours_basis.DEFAULT_CONFIG)
    from_clock = (cfg.get("hours_basis_mode") == _hours_basis.MODE_CLOCKED_ONLY
                  or not cfg.get("pay_from_schedule_default"))

    return {"roles": tuple(roles),
            # A read that FAILED is not a tenant that pays no plans: keep the step in play and let
            # the blocker say we could not look.
            "pays_incentive_plans": bool(plans_declared or plan_blocked),
            "hours_from_clock": bool(from_clock),
            "hours_basis_mode": _s(cfg.get("hours_basis_mode"))}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FACTS, read once for the whole board
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _logins_by_employee(org_id, blockers) -> dict:
    rows = _rows("app_users", "employee_id,email,role,market,store_code,store_codes,is_active,"
                              "must_reset_password,last_login", org_id, blockers)
    out = {}
    for r in rows:
        key = _s(r.get("employee_id"))
        if not key:
            continue
        # An ACTIVE login wins over a switched-off one for the same person: the question the step
        # asks is "can they sign in", and one working login is enough.
        if key not in out or (r.get("is_active") is not False
                              and out[key].get("is_active") is False):
            out[key] = r
    return out


def _plans_by_employee(org_id, blockers) -> dict:
    """ASKED of the roster that already answers "which plans is this person assigned" — never a
    second read of the assignment table, which is keyed on the rep NAME, not the employee id."""
    try:
        from app.modules.commcalc.router import commission_plan_roster
        res = commission_plan_roster(org_id=org_id, include_inactive=True) or {}
    except Exception as e:
        blockers.append({"table": "commission_plan_assignment", "reason": type(e).__name__})
        return {}
    if not res.get("ready"):
        blockers.append({"table": "commission_plan_assignment", "reason": "not_ready"})
    out = {}
    for p in res.get("people") or ():
        key = _s(p.get("value")) or _s(p.get("name"))
        if key:
            out[key] = p.get("current_plans") or []
    return out


def _onboarding_by_employee(org_id, blockers) -> dict:
    profiles = _rows("employee_onboarding_profile", "employee_id,workflow_status,started_at,"
                                                    "completed_at", org_id, blockers)
    tasks = _rows("onboarding_task", "id,is_mandatory,is_active", org_id, blockers)
    mandatory = {_s(t.get("id")) for t in tasks
                 if t.get("is_mandatory") and t.get("is_active") is not False}
    items = _rows("employee_onboarding", "employee_id,task_id,status", org_id, blockers)

    done_by, seen_by = {}, {}
    for it in items:
        emp, task = _s(it.get("employee_id")), _s(it.get("task_id"))
        if not emp or task not in mandatory:
            continue
        seen_by.setdefault(emp, set()).add(task)
        if _s(it.get("status")) in ("verified", "na"):
            done_by.setdefault(emp, set()).add(task)

    out = {}
    for p in profiles:
        emp = _s(p.get("employee_id"))
        if not emp:
            continue
        out[emp] = {"workflow_status": _s(p.get("workflow_status")) or _flow.ONBOARDING_FIRST,
                    "mandatory_total": len(mandatory),
                    "mandatory_done": len(done_by.get(emp, ())),
                    "started_at": p.get("started_at"), "completed_at": p.get("completed_at")}
    return out


def _employees(org_id, blockers, employee_id=None) -> list:
    from app.modules.storeops.router import _employees_with_pay_fields
    try:
        eq = {"employee_id": employee_id} if employee_id else None
        return _employees_with_pay_fields(org_id, EMP_FIELDS, eq=eq, so=sb()) or []
    except Exception as e:
        blockers.append({"table": "employees", "reason": type(e).__name__})
        return []


def _assemble(org_id, *, employee_id=None):
    blockers = []
    declared = _declarations(org_id, blockers)
    emps = _employees(org_id, blockers, employee_id=employee_id)
    logins = _logins_by_employee(org_id, blockers)
    plans = _plans_by_employee(org_id, blockers) if declared["pays_incentive_plans"] else {}
    onboarding = _onboarding_by_employee(org_id, blockers)
    return declared, emps, logins, plans, onboarding, blockers


def _facts_for(emp, logins, plans, onboarding) -> dict:
    key = _s(emp.get("employee_id"))
    plan_key = _s(emp.get("epay_salesperson")) or _s(emp.get("name"))
    return {"login": logins.get(key),
            "plan_assignments": plans.get(plan_key) or plans.get(key) or [],
            "onboarding": onboarding.get(key) or {}}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE STANDARD FILTERS (report rule, 2026-10-30) — resolved through the platform's own resolvers
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _apply_filters(rows, *, markets=None, stores=None, reps=None, org_id=ORG_ID):
    def _set(v):
        return {x.strip() for x in str(v or "").split(",") if x.strip()}

    want_stores, want_reps, want_markets = _set(stores), _set(reps), _set(markets)
    if want_stores:
        rows = [r for r in rows if _s(r.get("home_store")) in want_stores]
    if want_reps:
        rows = [r for r in rows if _s(r.get("employee_id")) in want_reps]
    if want_markets:
        try:
            from app.core import scope as _cscope
            mkt = _cscope.market_by_code(sb_public(), org_id) or {}
        except Exception:
            mkt = {}
        rows = [r for r in rows if (mkt.get(_s(r.get("home_store")).upper()) or "") in want_markets]
    return rows


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE BOARD — everybody, how far their setup got, and which step they are stuck at
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/employee-setup")
def employee_setup_board(include_inactive: bool = False, markets: str = None, stores: str = None,
                         reps: str = None, status: str = None,
                         authorization: str = Header(default=""), org_id: str = ORG_ID,
                         response: Response = None):
    """Every person's setup at a glance, with where each one picks up.

    ACTIVE-ONLY BY DEFAULT and `include_inactive` is the one spelling (§CORE-45): a person made
    inactive is out of every menu unless the box is ticked, and the count of those hidden is stated
    rather than left as a silently shorter list.
    """
    from app.modules.storeops.router import gate_person_rows
    declared, emps, logins, plans, onboarding, blockers = _assemble(org_id)

    hidden = _rv.hidden_count(emps) if emps else 0
    visible = _rv.visible(emps, include_inactive=include_inactive)
    visible = gate_person_rows(visible, authorization, org_id,
                               people_fields=("employee_id",), store_fields=("home_store",))
    visible = _apply_filters(visible, markets=markets, stores=stores, reps=reps, org_id=org_id)

    rows = []
    for emp in visible:
        try:
            rows.append(_flow.describe(emp, declared, _facts_for(emp, logins, plans, onboarding)))
        except ValueError as e:
            # The flow REFUSES rather than guessing (a tenant with no declared roles, say). Report
            # it against the person instead of dropping them off the board.
            rows.append({"employee_id": _s(emp.get("employee_id")),
                         "employee_name": _s(emp.get("name")),
                         "home_store": _s(emp.get("home_store")),
                         "is_active": emp.get("is_active") is not False,
                         "status": gw.NEEDS_INPUT, "label": gw.LABELS[gw.NEEDS_INPUT],
                         "resume": None, "resume_title": "", "blocking": (), "steps": [],
                         "refused": str(e)})

    if status:
        want = {x.strip() for x in str(status).split(",") if x.strip()}
        rows = [r for r in rows if r.get("status") in want]

    rows.sort(key=lambda r: (r.get("status") == gw.DONE, _s(r.get("employee_name")).casefold()))
    described = [r for r in rows if r.get("steps")]
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return {"ready": not blockers,
            "declared": {"roles": list(declared["roles"]),
                         "pays_incentive_plans": declared["pays_incentive_plans"],
                         "hours_from_clock": declared["hours_from_clock"],
                         "hours_basis_mode": declared["hours_basis_mode"]},
            "steps": [{"key": s["key"], "title": s["title"], "why": s["why"],
                       "required": s["required"], "screen": s["screen"]}
                      for s in (_flow.steps(declared) if declared["roles"] else ())],
            "statuses": [{"key": s, "label": gw.LABELS[s]} for s in gw.STATUSES],
            "summary": _flow.summarize(described),
            "rows": rows,
            "inactive_hidden": hidden,
            "blockers": blockers}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ONE PERSON — the rail, and the door for each step
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/employee-setup/{employee_id}")
def employee_setup_one(employee_id: str, authorization: str = Header(default=""),
                       org_id: str = ORG_ID, response: Response = None):
    """One person's six steps, what each one is waiting on, and where to go and do it.

    An inactive person is still readable here — somebody arriving from a link must be told the
    record is switched off, not shown a 404 that reads like "this person does not exist".
    """
    from app.modules.storeops.router import gate_person_rows
    key = _s(employee_id)
    declared, emps, logins, plans, onboarding, blockers = _assemble(org_id, employee_id=key)
    emps = [e for e in emps if _s(e.get("employee_id")) == key]
    if not emps:
        raise HTTPException(404, "No employee with that ID in this organisation.")
    if not gate_person_rows(emps, authorization, org_id, people_fields=("employee_id",),
                            store_fields=("home_store",)):
        raise HTTPException(403, "You cannot read this person.")

    emp = emps[0]
    try:
        out = _flow.describe(emp, declared, _facts_for(emp, logins, plans, onboarding))
    except ValueError as e:
        raise HTTPException(409, str(e))
    out["ready"] = not blockers
    out["blockers"] = blockers
    out["declared"] = {"roles": list(declared["roles"]),
                       "pays_incentive_plans": declared["pays_incentive_plans"],
                       "hours_from_clock": declared["hours_from_clock"]}
    out["statuses"] = [{"key": s, "label": gw.LABELS[s]} for s in gw.STATUSES]
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return out
