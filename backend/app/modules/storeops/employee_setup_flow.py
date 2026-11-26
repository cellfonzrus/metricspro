"""EMPLOYEE SETUP AS A FLOW SOMEBODY CAN LEAVE HALFWAY (index §STOREOPS-14).

Owner 2026-11-25, verbatim: *"The way we have set up teh closing workflow , it is very easy for a
new person to start and then pick up where tehy left off, need similar for the Employee set up in
logical steps leading up to onboaring"*. Step list confirmed by the owner the same evening; asked
whether the login, access and incentive-plan steps should block the onboarding pack, the answer was
**skippable with a warning**, and that is what ships — those three are optional steps, and the
warning is attached to the onboarding step so a person provisioned without a login is NAMED.

SIX STEPS, in the order the work can actually be done in:

    1 identity   Who and where            name · employee ID · home store · role · hire date
    2 pay        How they are paid        hourly rate, or salary amount
    3 login      Login and time clock     an app login linked to this employee
    4 access     What they can open       their role and which stores they can see
    5 plan       Incentive plan           which commission plan pays them
    6 onboarding Onboarding pack          the invite, the documents, HR's verification

THIS FLOW WRITES NOTHING, AND THAT IS THE DESIGN. Every step is a READ of state the platform
already holds, plus the address of the screen that already does that work. Employee details have
one writer each today; a wizard that typed them again would be a second writer for every one of
them — the sibling derivation the index rules forbid — and "resume where you left off" needs no
saved draft when the state IS the data. Nothing here can be half-saved, because nothing here saves.

WHAT IS ALREADY TRUE, AND SO IS NOT REBUILT HERE (the duplicate-check build gate)
  · the rail, the lamps, the resume and the progress count → `core/guided_workflow` (§CORE-51),
    dereferenced. This module decides no status word and no ordering rule of its own;
  · "which active hourly employees would a payroll run price at $0.00" → ONE home,
    `storeops/clock_variance.employees_without_rate` (§14z), ASKED here rather than re-derived, so
    this flow and the payroll preflight gate can never disagree about the same person;
  · the onboarding stage words → HR's own `WORKFLOW_STATUSES`, read from there. A second copy of
    those six words is how the screen and the server would drift apart;
  · "is this person active" → `core/roster_visibility` is the caller's business (§CORE-45): this
    module describes whoever it is handed, and reports `is_active` rather than deciding visibility.

THE TENANT'S OWN WORDS ARE A REQUIRED ARGUMENT (owner 2026-10-30). `declared["roles"]` has NO house
default: a caller that has not asked the organisation for its role names cannot answer, and a
tenant that has declared none is told so rather than silently measured against another tenant's
vocabulary. `pays_incentive_plans` and `hours_from_clock` are POLICY, resolved by the caller from
the existing config homes, and they change which steps apply — never which words appear.

PURE: no I/O, no database client, no framework import. The caller does the reads and hands facts in.
"""
from __future__ import annotations

from app.modules.core import guided_workflow as gw
from app.modules.hr import onboarding_stages as _stages  # the onboarding stage words' own home
from app.modules.storeops import clock_variance as _cv   # §14z — the $0.00 question's one home

# ── The step keys. Nothing outside this module spells one. ───────────────────────────────────────
STEP_IDENTITY = "identity"
STEP_PAY = "pay"
STEP_LOGIN = "login"
STEP_ACCESS = "access"
STEP_PLAN = "plan"
STEP_ONBOARDING = "onboarding"

STEP_ORDER = (STEP_IDENTITY, STEP_PAY, STEP_LOGIN, STEP_ACCESS, STEP_PLAN, STEP_ONBOARDING)

#: The onboarding stage vocabulary, DEREFERENCED from HR rather than copied (see the header).
ONBOARDING_STATUSES = tuple(_stages.WORKFLOW_STATUSES)
#: The two stages that mean the pack is finished. `provisioned` counts: HR has done its part.
ONBOARDING_SETTLED = tuple(_stages.SETTLED_STATUSES)
#: The stage a profile that exists but has never moved sits at.
ONBOARDING_FIRST = _stages.FIRST_STATUS

#: Which identity fields hold the step open, and which are merely asked for. A hire date missing
#: does not stop somebody being set up; a home store missing means no report can place them.
IDENTITY_REQUIRED = (("name", "Name"), ("employee_id", "Employee ID"),
                     ("home_store", "Home store"), ("role", "Role"))
IDENTITY_OPTIONAL = (("hire_date", "Hire date"), ("email", "Email"), ("phone", "Phone"))

#: The doors. Each is a screen that already exists and already does that step's work.
SCREENS = {
    STEP_IDENTITY: "/storeops/setup/employees",
    STEP_PAY: "/storeops/setup/employees",
    STEP_LOGIN: "/admin/roles",
    STEP_ACCESS: "/admin/roles",
    STEP_PLAN: "/commcalc/commission-plans",
    STEP_ONBOARDING: "/hr/onboarding",
}


def _s(v) -> str:
    return str(v or "").strip()


def _norm(v) -> str:
    return _s(v).casefold()


def _declared(declared) -> dict:
    """Validate the organisation's declarations. `roles` is REQUIRED and has no house default."""
    if not isinstance(declared, dict):
        raise ValueError("employee setup needs the organisation's declarations (roles, policy)")
    if "roles" not in declared:
        raise ValueError(
            "declared['roles'] is required — a caller that has not asked this organisation for its "
            "role names cannot answer whether an employee's role is one of them")
    roles = tuple(_s(r) for r in (declared.get("roles") or ()) if _s(r))
    return {"roles": roles,
            "role_index": {_norm(r): r for r in roles},
            "pays_incentive_plans": bool(declared.get("pays_incentive_plans")),
            "hours_from_clock": bool(declared.get("hours_from_clock"))}


# ── The steps ────────────────────────────────────────────────────────────────────────────────────
def steps(declared) -> tuple:
    """The six steps, validated and frozen by the shared home.

    `declared` is validated here too, so a caller cannot reach the rail without having asked the
    organisation for its vocabulary.
    """
    _declared(declared)
    return gw.declare([
        gw.step(STEP_IDENTITY, "Who and where",
                why="Their name, employee ID, home store and role.",
                screen=SCREENS[STEP_IDENTITY]),
        gw.step(STEP_PAY, "How they are paid",
                why="Hourly rate, or a salary amount.",
                depends_on=(STEP_IDENTITY,), screen=SCREENS[STEP_PAY]),
        gw.step(STEP_LOGIN, "Login and time clock",
                why="A login lets them sign in — and clock in, where hours come from the clock.",
                required=False, depends_on=(STEP_IDENTITY,), screen=SCREENS[STEP_LOGIN]),
        gw.step(STEP_ACCESS, "What they can open",
                why="Their role, and which stores they can see.",
                required=False, depends_on=(STEP_LOGIN,), screen=SCREENS[STEP_ACCESS]),
        gw.step(STEP_PLAN, "Incentive plan",
                why="Which commission plan pays them.",
                required=False, depends_on=(STEP_IDENTITY,), screen=SCREENS[STEP_PLAN]),
        gw.step(STEP_ONBOARDING, "Onboarding pack",
                why="Send the invite, collect the documents, HR verifies them.",
                depends_on=(STEP_IDENTITY,), screen=SCREENS[STEP_ONBOARDING]),
    ])


# ── Step 1 · who and where ───────────────────────────────────────────────────────────────────────
def _identity_verdict(emp, decl) -> dict:
    missing = [label for field, label in IDENTITY_REQUIRED if not _s(emp.get(field))]
    missing_optional = [label for field, label in IDENTITY_OPTIONAL if not _s(emp.get(field))]
    reasons = [f"{label} is not filled in" for label in missing]

    role = _s(emp.get("role"))
    role_known = None
    if role:
        if not decl["roles"]:
            reasons.append("No roles are declared for this organisation yet, so this role cannot "
                           "be checked — declare the roles first")
            role_known = False
        elif _norm(role) not in decl["role_index"]:
            reasons.append(f"Role {role!r} is not one of the roles this organisation has declared")
            role_known = False
        else:
            role_known = True
    elif not decl["roles"]:
        reasons.append("No roles are declared for this organisation yet")

    detail = {"missing": missing, "missing_optional": missing_optional,
              "role": role, "role_is_declared": role_known,
              "is_active": emp.get("is_active") is not False,
              "home_store": _s(emp.get("home_store"))}
    started = any(_s(emp.get(f)) for f, _ in IDENTITY_REQUIRED)
    return gw.verdict(done=not reasons, started=started, needs_input=tuple(reasons), detail=detail)


# ── Step 2 · how they are paid ───────────────────────────────────────────────────────────────────
def _pay_verdict(emp) -> dict:
    """The $0.00 verdict is ASKED of `clock_variance.employees_without_rate`, never re-derived."""
    basis = _norm(emp.get("pay_basis")) or "hourly"      # a blank basis is hourly platform-wide
    reasons, detail = [], {"pay_basis": basis}
    if basis == "hourly":
        gaps = _cv.employees_without_rate([emp])
        detail["pay_rate"] = emp.get("pay_rate")
        if gaps:
            reasons.append("No pay rate on file — a payroll run would price this person at $0.00")
        started = bool(_s(emp.get("pay_rate")))
    else:
        amount = emp.get("pay_amount")
        detail["pay_amount"] = amount
        try:
            ok = amount is not None and float(amount) > 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            reasons.append("Paid by salary, but no salary amount is on file")
        started = amount is not None
    return gw.verdict(done=not reasons, started=started or bool(reasons),
                      needs_input=tuple(reasons), detail=detail)


# ── Step 3 · login and the time clock ────────────────────────────────────────────────────────────
def _login_verdict(login, decl) -> dict:
    from_clock = decl["hours_from_clock"]
    detail = {"hours_from_clock": from_clock, "has_login": bool(login)}
    if not login:
        reasons = ["No login for this person yet"]
        if from_clock:
            reasons.append("Without a login they cannot clock in, and their hours come from the "
                           "clock here")
        return gw.verdict(needs_input=tuple(reasons), detail=detail)
    detail["email"] = _s(login.get("email"))
    detail["role"] = _s(login.get("role"))
    detail["last_login"] = login.get("last_login")
    if login.get("is_active") is False:
        return gw.verdict(started=True, detail=detail,
                          needs_input=("Their login is switched off",))
    if not login.get("last_login"):
        # Waiting on the EMPLOYEE, not on the person doing the setup. So it is in progress with the
        # reason stated, not "needs your input" — a lamp that asks somebody for something they
        # cannot give is the thing this flow exists to stop.
        detail["waiting_on"] = ("They have not signed in yet",)
        detail["must_reset_password"] = bool(login.get("must_reset_password"))
        return gw.verdict(started=True, detail=detail)
    return gw.verdict(done=True, started=True, detail=detail)


# ── Step 4 · what they can open ──────────────────────────────────────────────────────────────────
def _access_verdict(emp, login) -> dict:
    if not login:
        return gw.verdict(detail={"has_login": False})
    pins = [p for p in ([_s(login.get("store_code"))] + [_s(x) for x in (login.get("store_codes") or ())])
            if p]
    market = _s(login.get("market"))
    login_role, emp_role = _s(login.get("role")), _s(emp.get("role"))
    reasons = []
    if not pins and not market:
        reasons.append("Their login is pinned to no store and no market, so they would open the "
                       "app and see nothing")
    if not login_role:
        reasons.append("Their login carries no role, so nothing decides what they can open")
    elif emp_role and _norm(login_role) != _norm(emp_role):
        reasons.append(f"Their login says {login_role!r} and their employee record says "
                       f"{emp_role!r} — one of the two is wrong")
    detail = {"has_login": True, "store_pins": pins, "market": market, "login_role": login_role,
              "employee_role": emp_role}
    return gw.verdict(done=not reasons, started=True, needs_input=tuple(reasons), detail=detail)


# ── Step 5 · incentive plan ──────────────────────────────────────────────────────────────────────
def _plan_verdict(assignments, decl) -> dict:
    if not decl["pays_incentive_plans"]:
        return gw.verdict(skipped=True,
                          detail={"why": "This organisation pays no incentive plans, so there is "
                                         "nothing to assign."})
    names = [_s(a.get("plan_name")) or _s(a.get("plan_id")) for a in (assignments or ())]
    names = [n for n in names if n]
    return gw.verdict(done=bool(names), started=bool(names), detail={"plans": names})


# ── Step 6 · the onboarding pack ─────────────────────────────────────────────────────────────────
def _onboarding_verdict(onboarding, login) -> dict:
    ob = onboarding or {}
    stage = _s(ob.get("workflow_status"))
    total = int(ob.get("mandatory_total") or 0)
    done_n = int(ob.get("mandatory_done") or 0)
    # Skippable WITH A WARNING (owner 2026-11-25): the optional steps do not block the pack, so
    # the thing nobody would otherwise notice is said here instead.
    warnings = [] if login else ["This person has no login yet, so they cannot sign in or clock in"]
    detail = {"workflow_status": stage, "mandatory_total": total, "mandatory_done": done_n,
              "warnings": warnings}

    if not stage:
        return gw.verdict(detail=detail)
    if stage not in ONBOARDING_STATUSES:
        return gw.verdict(started=True, detail=detail,
                          needs_input=(f"Their onboarding stage reads {stage!r}, which is not one "
                                       "of the stages this platform knows",))
    reasons = []
    outstanding = max(total - done_n, 0)
    if outstanding:
        reasons.append(f"{outstanding} of {total} required documents are still outstanding")
    if stage not in ONBOARDING_SETTLED and not reasons:
        reasons.append(f"Their onboarding is at {stage.replace('_', ' ')} and not finished yet")
    if stage in ONBOARDING_SETTLED and not outstanding:
        return gw.verdict(done=True, started=True, detail=detail)
    return gw.verdict(started=True, needs_input=tuple(reasons), detail=detail)


# ── The whole flow ───────────────────────────────────────────────────────────────────────────────
def verdicts(employee, declared, facts) -> dict:
    """One verdict per step, from facts the caller has already read."""
    decl = _declared(declared)
    emp = employee or {}
    f = facts or {}
    login = f.get("login") or None
    return {
        STEP_IDENTITY: _identity_verdict(emp, decl),
        STEP_PAY: _pay_verdict(emp),
        STEP_LOGIN: _login_verdict(login, decl),
        STEP_ACCESS: _access_verdict(emp, login),
        STEP_PLAN: _plan_verdict(f.get("plan_assignments"), decl),
        STEP_ONBOARDING: _onboarding_verdict(f.get("onboarding"), login),
    }


def describe(employee, declared, facts) -> dict:
    """One person's whole setup: status, where to pick up, progress and the rail."""
    flow_steps = steps(declared)
    out = gw.describe(flow_steps, verdicts(employee, declared, facts))
    emp = employee or {}
    out["employee_id"] = _s(emp.get("employee_id"))
    out["employee_name"] = _s(emp.get("name"))
    out["home_store"] = _s(emp.get("home_store"))
    out["is_active"] = emp.get("is_active") is not False
    return out


def summarize(describes) -> dict:
    """The board: how many people at each status, and which step they are getting stuck at."""
    return gw.summarize(describes)
