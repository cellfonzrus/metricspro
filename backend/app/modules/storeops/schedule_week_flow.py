"""SCHEDULING ONE STORE-WEEK AS A FLOW SOMEBODY CAN LEAVE HALFWAY (index §STOREOPS-15).

Owner 2026-11-25: *"...and then simlar workflow for scheduling"*, said of the daily-closing workflow
a new person can start and come back to. Step list confirmed the same evening.

**THE SUBJECT IS A STORE-WEEK** — the scheduling analogue of the closing module's store-DAY
(§29.12). "Pick the week and the stores", the first item on the confirmed list, is how a person
CHOOSES the subject rather than a step that can be open or done, so it is the page's filter and the
five steps below are the checks for one store in one week. The board carries one row per store-week.

    1 budget        Hours budget         this store's weekly ceiling is declared
    2 availability  Who is available     no time-off request or cover swap still awaiting a decision
    3 shifts        Lay out the shifts    somebody is actually on the rota
    4 coverage      The careful checks    over budget · somebody in two places · somebody scheduled
                                          while on approved leave · (reported) days with nobody on
    5 tell          Tell the team         the week's rota has been sent

**THERE IS NO "PUBLISHED" STATE IN THIS PLATFORM, AND THIS DOES NOT INVENT ONE.** A shift is
`scheduled` the moment it is created; there is no draft and no publish. A step claiming to check
publication would be reading a column nothing writes — which is the `shifts.actual_hours` defect
(§STOREOPS-07) one more time, where seven screens displayed a column no writer filled. The honest
last step is whether the week's rota was SENT, and that IS recorded: `notify.send_log` carries a row
per send of report `storeops_schedule` with the filters it was sent under.

**AN UNCOVERED DAY IS REPORTED, NOT JUDGED.** Nothing in the platform declares which days a store is
open, so `open_dows` arrives empty and the coverage step NAMES the days with nobody on without
calling them a gap. Treating a seven-day week as the expectation would be a house assumption
standing in for a tenant's own declaration — the one place a default is banned (owner 2026-10-30).
Declare `open_dows` and the same step starts refusing, with no change here.

WHAT IS ALREADY TRUE, AND SO IS NOT REBUILT HERE (the duplicate-check build gate)

    the rail, lamps, resume, progress    `core/guided_workflow`               (index §CORE-51)
    which seven days a week IS           the tenant's declared `work_week_start_dow`, a REQUIRED
                                         argument here (`core/router.pay_period_for`'s settings)
    a shift's hours, a week's total      `storeops/schedule_hours.shift_hours` / `total_hours`
    is this store / person active        `core/roster_visibility`             (index §CORE-45)
    who may read which people            `storeops/router.gate_person_rows`   (index §14w)

PURE: no I/O, no client, no framework import. The caller does the reads and hands facts in.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.modules.core import guided_workflow as gw
from app.modules.storeops import schedule_hours as _sh

# ── The step keys. Nothing outside this module spells one. ───────────────────────────────────────
STEP_BUDGET = "budget"
STEP_AVAILABILITY = "availability"
STEP_SHIFTS = "shifts"
STEP_COVERAGE = "coverage"
STEP_TELL = "tell"

STEP_ORDER = (STEP_BUDGET, STEP_AVAILABILITY, STEP_SHIFTS, STEP_COVERAGE, STEP_TELL)

#: The doors. Each is a screen that already exists and already does that step's work.
SCREENS = {
    STEP_BUDGET: "/storeops/hours-budget",
    STEP_AVAILABILITY: "/storeops/timeoff",
    STEP_SHIFTS: "/storeops/schedule",
    STEP_COVERAGE: "/storeops/schedule",
    STEP_TELL: "/storeops/schedule",
}

#: The decision words a time-off or cover request carries (migs `003`, `087`). One spelling, read by
#: both the availability step and the coverage step — a second copy is how they would disagree about
#: whether somebody is away.
PENDING = "pending"
APPROVED = "approved"

#: The report key a send of the week's rota is recorded under (`notify/report_registry`).
SCHEDULE_REPORT_KEY = "storeops_schedule"

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


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
    """A date out of whatever the row carried. Returns None rather than guessing."""
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(_s(v)[:10])
    except (TypeError, ValueError):
        return None


# ── The organisation's declarations ──────────────────────────────────────────────────────────────
def _declared(declared) -> dict:
    """`week_start_dow` is REQUIRED — which seven days a week IS here is the tenant's own
    declaration (`storeops.payroll_settings.work_week_start_dow`), and a house Monday answering for
    a tenant whose week starts on Sunday mis-buckets every shift at the boundary.

    `open_dows` (the days this store is EXPECTED to have somebody on) arrives empty today because
    nothing declares it; see the module header.
    """
    if not isinstance(declared, dict):
        raise ValueError("scheduling needs the organisation's declarations (week start, open days)")
    if "week_start_dow" not in declared:
        raise ValueError(
            "declared['week_start_dow'] is required — which seven days a week is, is this "
            "organisation's own declaration, never a house Monday")
    try:
        dow = int(declared.get("week_start_dow"))
    except (TypeError, ValueError):
        raise ValueError("declared['week_start_dow'] must be a weekday number, 0 (Mon) to 6 (Sun)")
    if not 0 <= dow <= 6:
        raise ValueError("declared['week_start_dow'] must be 0 (Mon) to 6 (Sun)")
    open_dows = []
    for d in (declared.get("open_dows") or ()):
        try:
            n = int(d)
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 6 and n not in open_dows:
            open_dows.append(n)
    return {"week_start_dow": dow, "open_dows": tuple(sorted(open_dows))}


def week_days(week_start, declared) -> tuple:
    """The seven dates of this store-week, from the tenant's OWN week start.

    `week_start` is normalised through `schedule_hours.week_start_of`, so a date anywhere inside the
    week names the same week — a caller passing Wednesday does not get a week that begins then.
    """
    d = _declared(declared)
    start = _day(week_start)
    if start is None:
        raise ValueError("week_start must be a date")
    start = _sh.week_start_of(start, d["week_start_dow"])
    return tuple(start + timedelta(days=i) for i in range(7))


# ── The steps ────────────────────────────────────────────────────────────────────────────────────
def steps(declared) -> tuple:
    """The five checks a store-week passes through, validated and frozen by the shared home."""
    _declared(declared)
    return gw.declare([
        gw.step(STEP_BUDGET, "Hours budget",
                why="How many hours this store may be scheduled in a week.",
                screen=SCREENS[STEP_BUDGET]),
        gw.step(STEP_AVAILABILITY, "Who is available",
                why="Time off and cover swaps decided, so you are not scheduling around a maybe.",
                screen=SCREENS[STEP_AVAILABILITY]),
        gw.step(STEP_SHIFTS, "Lay out the shifts",
                why="Put somebody on for the days this store is open.",
                depends_on=(STEP_AVAILABILITY,), screen=SCREENS[STEP_SHIFTS]),
        gw.step(STEP_COVERAGE, "The careful checks",
                why="Over budget, somebody in two places, somebody on approved leave.",
                depends_on=(STEP_SHIFTS,), screen=SCREENS[STEP_COVERAGE]),
        gw.step(STEP_TELL, "Tell the team",
                why="Send the week's rota so people know when they are on.",
                required=False, depends_on=(STEP_SHIFTS,), screen=SCREENS[STEP_TELL]),
    ])


# ── Step 1 · the hours budget ────────────────────────────────────────────────────────────────────
def _budget_verdict(budget) -> dict:
    raw = (budget or {}).get("weekly_hours")
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        value = None
    detail = {"weekly_hours": value}
    if value is None:
        return gw.verdict(detail=detail,
                          needs_input=("No weekly hours budget is set for this store",))
    if value <= 0:
        return gw.verdict(started=True, detail=detail,
                          needs_input=("This store's weekly hours budget is zero, so any shift is "
                                       "over it",))
    return gw.verdict(done=True, started=True, detail=detail)


# ── Step 2 · who is available ────────────────────────────────────────────────────────────────────
def _availability_verdict(time_off, swaps) -> dict:
    off = [r for r in (time_off or ()) if r]
    pending_off = [r for r in off if _s(r.get("status")).lower() == PENDING]
    approved_off = [r for r in off if _s(r.get("status")).lower() == APPROVED]
    pending_swaps = [r for r in (swaps or ()) if r and _s(r.get("status")).lower() == PENDING]

    reasons = []
    if pending_off:
        n = len(pending_off)
        reasons.append(f"{n} time-off {_plural(n, 'request')} for this week "
                       f"{_plural(n, 'is', 'are')} still awaiting a decision")
    if pending_swaps:
        n = len(pending_swaps)
        reasons.append(f"{n} cover {_plural(n, 'swap')} for this week "
                       f"{_plural(n, 'is', 'are')} still awaiting a decision")
    detail = {"pending_time_off": len(pending_off), "pending_swaps": len(pending_swaps),
              "approved_time_off": len(approved_off),
              "away": sorted({_s(r.get("employee_id")) for r in approved_off
                              if _s(r.get("employee_id"))})}
    return gw.verdict(done=not reasons, started=True, needs_input=tuple(reasons), detail=detail)


# ── Step 3 · lay out the shifts ──────────────────────────────────────────────────────────────────
def _live(shifts):
    return [s for s in (shifts or ()) if s and not s.get("is_deleted")]


def _shifts_verdict(shifts) -> dict:
    live = _live(shifts)
    hours = _sh.total_hours(live)
    people = sorted({_s(s.get("employee_id")) for s in live if _s(s.get("employee_id"))})
    detail = {"shifts": len(live), "scheduled_hours": hours, "people": len(people)}
    if not live:
        return gw.verdict(detail=detail,
                          needs_input=("Nobody is on the rota for this store this week",))
    return gw.verdict(done=True, started=True, detail=detail)


# ── Step 4 · the careful checks ──────────────────────────────────────────────────────────────────
def _away_on(time_off):
    """{employee_id: set of dates} they are on APPROVED leave. A pending request is not leave."""
    out = {}
    for r in (time_off or ()):
        if not r or _s(r.get("status")).lower() != APPROVED:
            continue
        eid = _s(r.get("employee_id"))
        lo, hi = _day(r.get("start_date")), _day(r.get("end_date"))
        if not eid or lo is None:
            continue
        hi = hi or lo
        if hi < lo:
            lo, hi = hi, lo
        day = lo
        while day <= hi:
            out.setdefault(eid, set()).add(day)
            day += timedelta(days=1)
    return out


def _coverage_verdict(shifts, other_shifts, time_off, budget, override, declared, days) -> dict:
    d = _declared(declared)
    live = _live(shifts)
    hours = _sh.total_hours(live)

    # (a) over budget, and nobody has approved exceeding it for THIS week
    reasons = []
    raw = (budget or {}).get("weekly_hours")
    try:
        ceiling = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        ceiling = None
    ov = override or {}
    override_approved = _s(ov.get("status")).lower() == APPROVED
    try:
        override_hours = float(ov.get("approved_hours")) if ov.get("approved_hours") is not None else None
    except (TypeError, ValueError):
        override_hours = None
    effective = override_hours if (override_approved and override_hours is not None) else ceiling
    over_by = 0.0
    if effective is not None and hours > effective + 0.004:
        over_by = round(hours - effective, 2)
        if not override_approved:
            reasons.append(f"{hours:g}h are scheduled against a {effective:g}h budget — "
                           f"{over_by:g}h over, and no approval to exceed it is on file")

    # (b) somebody scheduled while on APPROVED leave
    away = _away_on(time_off)
    on_leave = sorted({f"{_s(s.get('employee_name')) or _s(s.get('employee_id'))} on "
                       f"{_day(s.get('shift_date')).isoformat()}"
                       for s in live
                       if _day(s.get("shift_date")) is not None
                       and _day(s.get("shift_date")) in away.get(_s(s.get("employee_id")), ())})
    if on_leave:
        n = len(on_leave)
        reasons.append(f"{n} {_plural(n, 'shift')} {_plural(n, 'is', 'are')} on somebody's "
                       f"approved time off: {', '.join(on_leave[:5])}"
                       f"{', …' if n > 5 else ''}")

    # (c) somebody booked at two stores on the same day. `other_shifts` is the SAME people's shifts
    # elsewhere in this week — read by the caller, because this module does no I/O.
    here = {(_s(s.get("employee_id")), _day(s.get("shift_date"))) for s in live
            if _s(s.get("employee_id")) and _day(s.get("shift_date")) is not None}
    clashes = sorted({f"{_s(o.get('employee_name')) or _s(o.get('employee_id'))} is also at "
                      f"{_s(o.get('store_code'))} on {_day(o.get('shift_date')).isoformat()}"
                      for o in _live(other_shifts)
                      if (_s(o.get("employee_id")), _day(o.get("shift_date"))) in here})
    if clashes:
        n = len(clashes)
        reasons.append(f"{n} {_plural(n, 'person is', 'people are')} booked at another store the "
                       f"same day: {', '.join(clashes[:5])}{', …' if n > 5 else ''}")

    # (d) days with nobody on — REPORTED, and only judged where the tenant declared its open days
    covered = {_day(s.get("shift_date")) for s in live if _day(s.get("shift_date")) is not None}
    empty = [dd for dd in days if dd not in covered]
    empty_names = [DAY_NAMES[dd.weekday()] for dd in empty]
    uncovered_open = [dd for dd in empty if dd.weekday() in d["open_dows"]]
    if uncovered_open:
        names = [DAY_NAMES[dd.weekday()] for dd in uncovered_open]
        n = len(names)
        reasons.append(f"{n} declared opening {_plural(n, 'day has', 'days have')} nobody on: "
                       f"{', '.join(names)}")

    detail = {"scheduled_hours": hours, "weekly_hours": ceiling,
              "effective_ceiling": effective, "over_by": over_by,
              "override_status": _s(ov.get("status")), "override_hours": override_hours,
              "days_with_nobody": empty_names,
              "open_days_declared": bool(d["open_dows"]),
              "on_approved_leave": on_leave, "double_booked": clashes}
    if not d["open_dows"] and empty_names:
        # Said rather than implied: an empty day is a FACT here, not a verdict, because nothing
        # declares which days this store opens.
        detail["days_with_nobody_note"] = (
            "No opening days are declared for this store, so days with nobody on are listed "
            "rather than treated as gaps.")
    return gw.verdict(done=not reasons, started=bool(live), needs_input=tuple(reasons),
                      detail=detail)


# ── Step 5 · tell the team ───────────────────────────────────────────────────────────────────────
def _tell_verdict(sends, shifts) -> dict:
    rows = [r for r in (sends or ()) if r]
    sent = [r for r in rows if _s(r.get("status")).lower() == "sent"]
    failed = [r for r in rows if _s(r.get("status")).lower() == "failed"]
    detail = {"sent": len(sent), "failed": len(failed),
              "last_sent_at": max([_s(r.get("created_at")) for r in sent], default=""),
              "targets": sorted({_s(r.get("target")) for r in sent if _s(r.get("target"))})}
    if sent:
        # A send that reached somebody settles the step. A FAILED address alongside it is recorded in
        # `detail` for the screen to show, and does not re-open a step that is done.
        return gw.verdict(done=True, started=True, detail=detail)
    if failed:
        n = len(failed)
        return gw.verdict(started=True, detail=detail,
                          needs_input=(f"The rota was sent to {n} "
                                       f"{_plural(n, 'address')} and did not arrive",))
    if not _live(shifts):
        return gw.verdict(detail=detail)
    return gw.verdict(detail=detail,
                      needs_input=("This week's rota has not been sent to anybody yet",))


# ── The whole flow ───────────────────────────────────────────────────────────────────────────────
def verdicts(declared, facts) -> dict:
    """One verdict per step, from facts the caller has already read for ONE store-week."""
    f = facts or {}
    days = week_days(f.get("week_start"), declared)
    return {
        STEP_BUDGET: _budget_verdict(f.get("budget")),
        STEP_AVAILABILITY: _availability_verdict(f.get("time_off"), f.get("swaps")),
        STEP_SHIFTS: _shifts_verdict(f.get("shifts")),
        STEP_COVERAGE: _coverage_verdict(f.get("shifts"), f.get("other_shifts"), f.get("time_off"),
                                         f.get("budget"), f.get("override"), declared, days),
        STEP_TELL: _tell_verdict(f.get("sends"), f.get("shifts")),
    }


def describe(declared, facts) -> dict:
    """One store-week's whole flow: status, where to pick up, progress and the rail."""
    flow_steps = steps(declared)
    out = gw.describe(flow_steps, verdicts(declared, facts))
    f = facts or {}
    days = week_days(f.get("week_start"), declared)
    out["store_code"] = _s(f.get("store_code"))
    out["week_start"] = days[0].isoformat()
    out["week_end"] = days[-1].isoformat()
    out["scheduled_hours"] = _sh.total_hours(_live(f.get("shifts")))
    return out


def summarize(describes) -> dict:
    """The board: how many store-weeks at each status, and which step they are stuck at."""
    return gw.summarize(describes)
