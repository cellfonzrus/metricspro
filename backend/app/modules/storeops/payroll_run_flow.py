"""RUNNING ONE PAY PERIOD AS A FLOW SOMEBODY CAN LEAVE HALFWAY (index §STOREOPS-16).

Owner 2026-11-25: *"...and simlar workflow for runnign the payroll with paying attention to
details"*, said of the daily-closing workflow a new person can start and come back to. The eight
steps were confirmed the same evening; the first of them — "pick the pay period and the stores" —
is how a person CHOOSES the subject rather than a step that can be open or done, so it is the
page's filter and the seven below are the checks for ONE store in ONE pay period.

    1 punches     The punches             nobody is still clocked in, and somebody who worked
                                          clocked in at all
    2 basis       What is paying them     which basis each paid row stands on, and how much of it
                                          is a punch rather than the schedule
    3 exceptions  Fix the exceptions      uncorrected clock variances, and manual hours nothing
                                          has counted
    4 approval    Hours approved          the DM signed off, then HR
    5 rates       Pay rates and salary    nobody this run would pay is priced at $0.00
    6 preflight   The preflight gate      the existing refusal, asked and repeated verbatim
    7 run         Run it, and the rest    the run is recorded, with its tax and expenses

THE RUN RECORD IS MONTH-GRAINED, AND THIS DOES NOT PRETEND OTHERWISE. `payroll_gross_ledger`,
`payroll_tax_ledger` and `payroll_expense_item` are all keyed on `period = "YYYY-MM"` (migs 404,
405), while an approval row is keyed on `period_start`/`period_end`. A tenant paying fortnightly
therefore has pay periods that do not line up with the record of the push. Step 7 says so in words
and answers the question it CAN answer — whether the month this period falls in has been run —
rather than reading a per-period run that nothing writes. Inventing one would be the
`shifts.actual_hours` defect (§STOREOPS-07) a third time.

WHAT IS ALREADY TRUE, AND SO IS NOT REBUILT HERE (the duplicate-check build gate)

    the rail, lamps, resume, progress     `core/guided_workflow`               (index §CORE-51)
    may the run compute, and why not      `storeops/clock_variance.payroll_run_gate` (§14z) — ASKED
                                          for step 6 and its words repeated, never re-derived
    "priced at $0.00 by this run"          `clock_variance.employees_without_rate`
    is this basis PAYING hours             `storeops/payroll_hours_basis.basis_pays_hours` /
                                          `row_is_paid` / `basis_reason`   (index §14y)
    which dates a pay period covers        the tenant's declared cycle, through
                                          `core/router.pay_period_for` — a REQUIRED argument here
    is this store / person active          `core/roster_visibility`             (index §CORE-45)

PURE: no I/O, no client, no framework import. The caller does the reads and hands facts in.
"""
from __future__ import annotations

from datetime import date

from app.modules.core import guided_workflow as gw
from app.modules.storeops import clock_variance as _cv
from app.modules.storeops import payroll_hours_basis as _hb

# ── The step keys. Nothing outside this module spells one. ───────────────────────────────────────
STEP_PUNCHES = "punches"
STEP_BASIS = "basis"
STEP_EXCEPTIONS = "exceptions"
STEP_APPROVAL = "approval"
STEP_RATES = "rates"
STEP_PREFLIGHT = "preflight"
STEP_RUN = "run"

STEP_ORDER = (STEP_PUNCHES, STEP_BASIS, STEP_EXCEPTIONS, STEP_APPROVAL, STEP_RATES,
              STEP_PREFLIGHT, STEP_RUN)

#: The doors. Each is a screen that already exists and already does that step's work.
SCREENS = {
    STEP_PUNCHES: "/storeops/timeclock",
    STEP_BASIS: "/storeops/payroll/clock-variances",
    STEP_EXCEPTIONS: "/storeops/payroll/clock-variances",
    STEP_APPROVAL: "/storeops/payroll/approvals",
    STEP_RATES: "/storeops/setup/employees",
    STEP_PREFLIGHT: "/storeops/payroll",
    STEP_RUN: "/storeops/payroll",
}

#: The decision words an approval row carries (mig 431). One spelling, read by the approval step.
PENDING = "pending"
APPROVED = "approved"
SENT_BACK = "sent_back"


def _s(v) -> str:
    return str(v or "").strip()


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _plural(n: int, one: str, many: str = "") -> str:
    return one if n == 1 else (many or one + "s")


def _day(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(_s(v)[:10])
    except (TypeError, ValueError):
        return None


# ── The organisation's declarations ──────────────────────────────────────────────────────────────
def _declared(declared) -> dict:
    """`period_start` and `period_end` are REQUIRED. Which dates a pay period covers is the tenant's
    own declared cycle (`storeops.tenants.pay_period_type` / `work_week_start_dow` /
    `biweekly_anchor`, resolved by `core/router.pay_period_for`), and a house fortnight answering
    for a weekly tenant would ask somebody to approve half a pay period — the defect
    `payroll_approval` §② already records.

    `clock_coverage_floor` IS a policy (how much punch evidence is too little to trust), so a house
    default is legitimate and it is one.
    """
    if not isinstance(declared, dict):
        raise ValueError("running payroll needs the organisation's declared pay period")
    lo, hi = _day(declared.get("period_start")), _day(declared.get("period_end"))
    if lo is None or hi is None:
        raise ValueError(
            "declared['period_start'] and declared['period_end'] are required — which dates a pay "
            "period covers is this organisation's own declared cycle, not a house fortnight")
    if hi < lo:
        lo, hi = hi, lo
    floor = declared.get("clock_coverage_floor", 0.5)
    try:
        floor = float(floor)
    except (TypeError, ValueError):
        floor = 0.5
    return {"period_start": lo, "period_end": hi,
            "months": tuple(sorted({lo.strftime("%Y-%m"), hi.strftime("%Y-%m")})),
            "clock_coverage_floor": min(max(floor, 0.0), 1.0),
            "gate_enabled": bool(declared.get("gate_enabled", True))}


def months_of(declared) -> tuple:
    """The `YYYY-MM` keys the run record could carry for this period — one, or two when the period
    straddles a month end. The month grain is the record's, not this flow's choice."""
    return _declared(declared)["months"]


# ── The steps ────────────────────────────────────────────────────────────────────────────────────
def steps(declared) -> tuple:
    """The seven checks a store's pay period passes through, validated by the shared home."""
    _declared(declared)
    return gw.declare([
        gw.step(STEP_PUNCHES, "The punches",
                why="Nobody still clocked in, and anybody who worked clocked in at all.",
                screen=SCREENS[STEP_PUNCHES]),
        gw.step(STEP_BASIS, "What is paying them",
                why="Whether these hours stand on a punch, a correction, or the schedule.",
                screen=SCREENS[STEP_BASIS]),
        gw.step(STEP_EXCEPTIONS, "Fix the exceptions",
                why="Clock variances still uncorrected, and hours somebody typed that nothing has "
                    "counted.",
                depends_on=(STEP_PUNCHES,), screen=SCREENS[STEP_EXCEPTIONS]),
        gw.step(STEP_APPROVAL, "Hours approved",
                why="The DM signs off the hours, then HR.",
                depends_on=(STEP_EXCEPTIONS,), screen=SCREENS[STEP_APPROVAL]),
        gw.step(STEP_RATES, "Pay rates and salary",
                why="Nobody this run would pay is priced at $0.00 because a rate is missing.",
                screen=SCREENS[STEP_RATES]),
        gw.step(STEP_PREFLIGHT, "The preflight gate",
                why="The payroll run's own refusal, and exactly what it names.",
                depends_on=(STEP_EXCEPTIONS, STEP_RATES), screen=SCREENS[STEP_PREFLIGHT]),
        gw.step(STEP_RUN, "Run it, and the rest",
                why="The run is recorded, with its employer tax and its expenses.",
                depends_on=(STEP_APPROVAL, STEP_PREFLIGHT), screen=SCREENS[STEP_RUN]),
    ])


# ── Step 1 · the punches ─────────────────────────────────────────────────────────────────────────
def _punches_verdict(punches, worked_without_punch) -> dict:
    rows = [p for p in (punches or ()) if p]
    still_open = [p for p in rows if not _s(p.get("clock_out"))]
    missing = [_s(x) for x in (worked_without_punch or ()) if _s(x)]
    reasons = []
    if still_open:
        n = len(still_open)
        who = sorted({_s(p.get("employee_id")) for p in still_open if _s(p.get("employee_id"))})
        reasons.append(f"{n} {_plural(n, 'punch is', 'punches are')} still open — somebody is "
                       f"clocked in with no clock-out: {', '.join(who[:5])}"
                       f"{', …' if len(who) > 5 else ''}")
    if missing:
        n = len(missing)
        reasons.append(f"{n} {_plural(n, 'person', 'people')} on the rota never clocked in at all: "
                       f"{', '.join(sorted(missing)[:5])}{', …' if n > 5 else ''}")
    detail = {"punches": len(rows), "open_punches": len(still_open),
              "worked_without_punch": sorted(missing)}
    if not rows and not missing:
        # NOTHING TO GO ON is not a clean punch record: absence is never zero (the house rule), so
        # the step stays open and the person is sent to look rather than told it is done.
        return gw.verdict(detail=detail,
                          needs_input=("No punches at all are recorded for this store this period",))
    return gw.verdict(done=not reasons, started=bool(rows), needs_input=tuple(reasons),
                      detail=detail)


# ── Step 2 · what is paying them ─────────────────────────────────────────────────────────────────
def _basis_verdict(paid_rows, declared) -> dict:
    """How much of the PAID time stands on a punch rather than on the schedule.

    Every part of this is ASKED of `payroll_hours_basis` (§14y): which rows are paid at all
    (`row_is_paid`, which reads the producer's own `hours_by_basis` tally), which bases MEASURED the
    day (`basis_is_measurement`), which may become paid hours (`basis_pays_hours`) and what each one
    means to a person (`basis_reason`). This step keeps no vocabulary of its own, so it and the
    payroll engine can never disagree about what is paying somebody.
    """
    d = _declared(declared)
    rows = [r for r in (paid_rows or ()) if r]
    paid = [r for r in rows if _hb.row_is_paid(r)]

    hours_by_basis, measured_hours, paying_hours = {}, 0.0, 0.0
    for r in paid:
        for basis, hrs in ((r.get("hours_by_basis") or {}).items()):
            h = _f(hrs)
            if not h or not _hb.basis_pays_hours(basis):
                continue
            hours_by_basis[basis] = round(hours_by_basis.get(basis, 0.0) + h, 2)
            paying_hours = round(paying_hours + h, 2)
            if _hb.basis_is_measurement(basis):
                measured_hours = round(measured_hours + h, 2)

    coverage = round(measured_hours / paying_hours, 4) if paying_hours else None
    detail = {"rows": len(rows), "paid_rows": len(paid),
              "hours_by_basis": hours_by_basis,
              "paid_hours": paying_hours, "hours_from_a_punch": measured_hours,
              "clock_coverage": coverage,
              "clock_coverage_floor": d["clock_coverage_floor"],
              "bases_explained": {b: _hb.basis_reason(b) for b in hours_by_basis}}
    if not rows:
        return gw.verdict(detail=detail,
                          needs_input=("No hours are on this period yet for this store",))
    if not paid:
        return gw.verdict(started=True, detail=detail,
                          needs_input=("This store has hours in the period but none of them are on "
                                       "a basis that pays, so this run would pay nobody here",))
    if coverage is not None and coverage < d["clock_coverage_floor"]:
        pct = round(coverage * 100)
        return gw.verdict(started=True, detail=detail,
                          needs_input=(f"Only {pct}% of the paid hours here stand on a punch — the "
                                       f"rest fall back to the schedule, so check the clock before "
                                       f"paying from it",))
    return gw.verdict(done=True, started=True, detail=detail)


# ── Step 3 · fix the exceptions ──────────────────────────────────────────────────────────────────
def _exceptions_verdict(blocking, manual_uncounted) -> dict:
    rows = [r for r in (blocking or ()) if r]
    manual = [m for m in (manual_uncounted or ()) if m]
    reasons = []
    if rows:
        n = len(rows)
        named = sorted({f"{_s(r.get('store_code'))}/{_s(r.get('employee_id'))} {_s(r.get('date'))}"
                        for r in rows})
        reasons.append(f"{n} clock {_plural(n, 'variance is', 'variances are')} still uncorrected: "
                       f"{', '.join(named[:5])}{', …' if len(named) > 5 else ''}")
    if manual:
        n = len(manual)
        reasons.append(f"{n} hand-typed {_plural(n, 'hours row', 'hours rows')} "
                       f"{_plural(n, 'is', 'are')} not counted by anything yet")
    detail = {"uncorrected_variances": len(rows), "manual_uncounted": len(manual)}
    return gw.verdict(done=not reasons, started=True, needs_input=tuple(reasons), detail=detail)


# ── Step 4 · hours approved ──────────────────────────────────────────────────────────────────────
def _approval_verdict(approvals) -> dict:
    """The DM, then HR. A row sent back is NAMED separately from one nobody has looked at, because
    "waiting for the DM" and "the DM pushed it back" are different jobs for the person reading this.
    """
    rows = [r for r in (approvals or ()) if r]
    dm_pending = [r for r in rows if _s(r.get("dm_status")).lower() == PENDING]
    dm_back = [r for r in rows if _s(r.get("dm_status")).lower() == SENT_BACK]
    dm_ok = [r for r in rows if _s(r.get("dm_status")).lower() == APPROVED]
    hr_pending = [r for r in dm_ok if _s(r.get("hr_status")).lower() == PENDING]
    hr_back = [r for r in dm_ok if _s(r.get("hr_status")).lower() == SENT_BACK]
    hr_ok = [r for r in dm_ok if _s(r.get("hr_status")).lower() == APPROVED]
    overridden = [r for r in rows if _s(r.get("override_by"))]

    reasons = []
    if not rows:
        reasons.append("No hours have been raised for approval for this store this period")
    if dm_back:
        n = len(dm_back)
        reasons.append(f"{n} {_plural(n, 'row was', 'rows were')} sent back by the manager and "
                       f"{_plural(n, 'needs', 'need')} fixing")
    if dm_pending:
        n = len(dm_pending)
        reasons.append(f"{n} {_plural(n, 'row is', 'rows are')} waiting on the manager's sign-off")
    if hr_back:
        n = len(hr_back)
        reasons.append(f"{n} {_plural(n, 'row was', 'rows were')} sent back by HR")
    if hr_pending:
        n = len(hr_pending)
        reasons.append(f"{n} {_plural(n, 'row is', 'rows are')} waiting on HR")
    detail = {"rows": len(rows), "dm_pending": len(dm_pending), "dm_sent_back": len(dm_back),
              "hr_pending": len(hr_pending), "hr_sent_back": len(hr_back),
              "approved": len(hr_ok), "overridden": len(overridden)}
    if overridden:
        n = len(overridden)
        detail["override_note"] = (f"{n} {_plural(n, 'row', 'rows')} "
                                   f"{_plural(n, 'was', 'were')} let through by an admin override, "
                                   f"with a reason on file")
    return gw.verdict(done=not reasons, started=bool(rows), needs_input=tuple(reasons),
                      detail=detail)


# ── Step 5 · pay rates and salary ────────────────────────────────────────────────────────────────
def _rates_verdict(employee_rows, paid_employee_ids, salary_rows) -> dict:
    """ASKED of `clock_variance.employees_without_rate` — the one home for "this run would price
    them at $0.00" — and never re-derived. A salaried person legitimately has no hourly rate, which
    is why that home reads `pay_basis` and this step does not second-guess it.
    """
    no_rate = _cv.employees_without_rate(employee_rows, paid_employee_ids)
    salaried = [e for e in (employee_rows or ())
                if _s((e or {}).get("pay_basis")).lower() not in ("", "hourly")
                and (e or {}).get("is_active") is not False]
    have_salary_row = {_s((r or {}).get("employee_id")) for r in (salary_rows or ())
                       if _s((r or {}).get("employee_id"))}
    missing_salary = sorted({_s(e.get("employee_id")) for e in salaried
                             if _s(e.get("employee_id"))
                             and _s(e.get("employee_id")) not in have_salary_row
                             and salary_rows is not None})
    reasons = []
    if no_rate:
        n = len(no_rate)
        reasons.append(f"{n} {_plural(n, 'person', 'people')} this run would pay "
                       f"{_plural(n, 'has', 'have')} no pay rate on file, so "
                       f"{_plural(n, 'they would be', 'they would be')} priced at $0.00: "
                       f"{', '.join(r['employee_id'] for r in no_rate[:5])}"
                       f"{', …' if n > 5 else ''}")
    if missing_salary:
        n = len(missing_salary)
        reasons.append(f"{n} salaried {_plural(n, 'person has', 'people have')} no salary figure "
                       f"for this period: {', '.join(missing_salary[:5])}"
                       f"{', …' if n > 5 else ''}")
    detail = {"employees_without_rate": [r["employee_id"] for r in no_rate],
              "salaried": len(salaried), "salaried_without_figure": missing_salary}
    return gw.verdict(done=not reasons, started=True, needs_input=tuple(reasons), detail=detail)


# ── Step 6 · the preflight gate ──────────────────────────────────────────────────────────────────
def _preflight_verdict(blocking, employee_rows, paid_employee_ids, declared) -> dict:
    """THE EXISTING GATE, ASKED AND REPEATED VERBATIM. `clock_variance.payroll_run_gate` already
    decides whether a run may compute and already names what blocks it; a second opinion here would
    be a surface that says "ready" while the run itself refuses. Its `reason` is carried through to
    the person word for word.
    """
    d = _declared(declared)
    gate = _cv.payroll_run_gate(blocking=blocking or (),
                                cfg={"payroll_run_gate_enabled": d["gate_enabled"]},
                                employee_rows=employee_rows, paid_employee_ids=paid_employee_ids)
    detail = {"blocking_count": gate["blocking_count"],
              "employees_without_rate": [r["employee_id"] for r in gate["employees_without_rate"]],
              "gate_enabled": d["gate_enabled"], "gate_reason": gate["reason"]}
    if gate["refused"]:
        return gw.verdict(started=True, detail=detail, needs_input=(gate["reason"],))
    return gw.verdict(done=True, started=True, detail=detail)


# ── Step 7 · run it, and the rest ────────────────────────────────────────────────────────────────
def _run_verdict(run_rows, tax_rows, expense_rows, declared) -> dict:
    """Whether the run is RECORDED — at the grain the record actually has.

    `payroll_gross_ledger`, `payroll_tax_ledger` and `payroll_expense_item` are keyed on a MONTH
    (`YYYY-MM`), not on a pay period, so for a tenant paying fortnightly this answers "has the month
    this period falls in been run", and `detail['grain_note']` says that in words. Reading a
    per-period run would be reading something nothing writes.
    """
    d = _declared(declared)
    runs = [r for r in (run_rows or ()) if r]
    wages = round(sum(_f((r or {}).get("wages")) for r in runs), 2)
    tax = round(sum(_f((r or {}).get("employer_total") or (r or {}).get("employer_tax_total"))
                    for r in (tax_rows or ()) if r), 2)
    expenses = round(sum(_f((r or {}).get("amount")) for r in (expense_rows or ()) if r), 2)
    months_run = sorted({_s((r or {}).get("period")) for r in runs if _s((r or {}).get("period"))})
    missing_months = [m for m in d["months"] if m not in months_run]
    detail = {"months": list(d["months"]), "months_run": months_run,
              "wages": wages, "employer_tax": tax, "expenses": expenses,
              "last_run_at": max([_s((r or {}).get("run_at")) for r in runs], default=""),
              "grain_note": ("The run, its employer tax and its expenses are recorded by MONTH, not "
                             "by pay period, so this says whether the month this period falls in "
                             "has been run.")}
    reasons = []
    if missing_months:
        n = len(missing_months)
        reasons.append(f"Payroll has not been run for {_plural(n, 'the month', 'the months')} "
                       f"{', '.join(missing_months)}")
    elif not wages:
        reasons.append("The run is recorded for this month but it pushed $0.00 of wages for this "
                       "store")
    if not reasons and not tax:
        reasons.append("Employer tax has not been recorded for this month")
    if not reasons and not expenses:
        detail["expenses_note"] = ("No payroll expenses are recorded for this month, which is "
                                   "correct for a tenant that books none.")
    return gw.verdict(done=not reasons, started=bool(runs), needs_input=tuple(reasons),
                      detail=detail)


# ── The whole flow ───────────────────────────────────────────────────────────────────────────────
def verdicts(declared, facts) -> dict:
    """One verdict per step, from facts the caller has already read for ONE store-pay-period."""
    f = facts or {}
    paid_ids = f.get("paid_employee_ids")
    return {
        STEP_PUNCHES: _punches_verdict(f.get("punches"), f.get("worked_without_punch")),
        STEP_BASIS: _basis_verdict(f.get("paid_rows"), declared),
        STEP_EXCEPTIONS: _exceptions_verdict(f.get("blocking"), f.get("manual_uncounted")),
        STEP_APPROVAL: _approval_verdict(f.get("approvals")),
        STEP_RATES: _rates_verdict(f.get("employees"), paid_ids, f.get("salary_rows")),
        STEP_PREFLIGHT: _preflight_verdict(f.get("blocking"), f.get("employees"), paid_ids,
                                           declared),
        STEP_RUN: _run_verdict(f.get("run_rows"), f.get("tax_rows"), f.get("expense_rows"),
                               declared),
    }


def describe(declared, facts) -> dict:
    """One store's pay period: status, where to pick up, progress and the rail."""
    d = _declared(declared)
    out = gw.describe(steps(declared), verdicts(declared, facts))
    f = facts or {}
    out["store_code"] = _s(f.get("store_code"))
    out["period_start"] = d["period_start"].isoformat()
    out["period_end"] = d["period_end"].isoformat()
    out["months"] = list(d["months"])
    return out


def summarize(describes) -> dict:
    """The board: how many store-periods at each status, and which step they are stuck at."""
    return gw.summarize(describes)
