"""THE "HOURS WORKED" CARD — one fold, and it never calls the schedule a measurement.

Owner Sanjot, 2026-11-27, verbatim: *"it shows hours worked as the same as the hours scheduled, it
should show the atual hours worked"*, then *"fix the card now across the paltform"*.

WHAT WAS WRONG, and why it was guaranteed rather than unlucky. `GET /core/employee-dashboard` built
the rep's own "Hours Worked (this month)" figure as, literally:

    ah = sum((float(s.get("actual_hours") or 0) or float(s.get("scheduled_hours") or 0)) for s in msh)

`storeops.timelog` was never queried, and `shifts.actual_hours` carries a figure on **7 of 7,740**
live rows (index §14y), so `actual == scheduled` on essentially every row for every tenant. This was
the last live single-line spelling of the `actual_hours or scheduled_hours` defect §14y fixed in six
modules and §14y.1 in four more — and it survived because a *display* surface was treated as exempt
from the pay path's discipline. It is not exempt: it is the one surface a REP reads about their own
pay, and it is the only one that showed the figure with no basis beside it.

THE DIVISION THIS MODULE EXISTS TO HOLD. Three different questions were being answered by one
number, which is why one number could not be right:

  | key                | question                                              |
  | ------------------ | ----------------------------------------------------- |
  | `actual_hours`     | how many hours did a clock actually MEASURE?          |
  | `unmeasured_hours` | how many SCHEDULED hours did nothing measure?         |
  | `paid_hours`       | what will payroll pay, on its declared basis?         |

`actual_hours` is the owner's question and it is now answered by measurement alone. `paid_hours` is
the one home's answer (`payroll_hours_basis.resolve_*` via `payroll_evidence.paid_hours_rows`) and
is kept because it is what the rep is actually owed — under `schedule_fallback` it legitimately
includes scheduled hours, and dropping it would tell such a rep they earn nothing. The two are never
summed and never conflated.

THE HOUSE RULES THIS ENFORCES, none of them invented here:
  · **Nothing measured says so** (owner 2026-10-30: *"nothing declared means nothing identified, and
    the surface says so"*). `actual_hours` is **None**, not 0.0, and the sentence reads
    *"no punch recorded"*. `0h` is the other lie: §14s is explicit that a measured zero and an
    unmeasured day must never print the same, which is why `measured_zero` is a NUMBER here.
  · **A refusal outranks a total.** When `payroll_evidence` refuses the period (the coverage
    interlock — a tenant at 49% measured), every figure is None and the card carries the refusal
    sentence. "We could not look" and "nobody clocked" must never print the same.
  · **The basis words are not ours.** The dominant basis, its sentence and the word *mixed* come
    from `payroll_hours_basis.fold_bases` — the same fold `GET /storeops/payroll` applies — so the
    card and the payroll report cannot disagree.
  · **The allowlist decides what counts as measured**, via `basis_is_measurement` /
    `basis_pays_hours`. A basis invented in 2027 is not a measurement merely by appearing, and its
    hours cannot reach `actual_hours`.
  · **No tenant's vocabulary lives here.** This module holds no mode, no rate and no org default: it
    folds the bases it is HANDED. Two orgs' declarations produce two cards from the same shifts
    (§COMMCALC-49.10), and this module cannot be the reason they agree.

PURE. No I/O, no client, no config read — which is why `tests/unit/test_hours_worked_card.py` and
`harness_honest_hours_card.py` can pin every state without a database.
"""
from app.modules.storeops import payroll_hours_basis as _hb

# ── the states a card can be in. Each one prints differently, which is the whole point. ──────────
MEASURED = "measured"                   # every shift in the window was measured
PARTLY_MEASURED = "partly_measured"     # some measured, some not — BOTH are named
NOTHING_MEASURED = "nothing_measured"   # shifts exist, no clock measured any of them
NO_SHIFTS = "no_shifts"                 # nothing was scheduled; not the same as nothing measured
REFUSED = "refused"                     # the period may not be published at all

ALL_STATES = (MEASURED, PARTLY_MEASURED, NOTHING_MEASURED, NO_SHIFTS, REFUSED)

#: The words the card prints. Here so a reader can see every sentence this surface can say in one
#: place, and so the proof can assert they stay distinct — two states that print the same sentence
#: are two states the reader cannot tell apart.
NO_PUNCH_SENTENCE = ("No punch recorded for this period — these hours were scheduled, not clocked, "
                     "so nothing has measured them yet.")
NO_SHIFTS_SENTENCE = "No shifts scheduled in this period."


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def card(rows, *, refusal=None, coverage_note=None):
    """PURE: the "Hours Worked" card, from one row per shift row.

    `rows` is `payroll_evidence.paid_hours_rows`' output shape — each row carrying at least
    `hours` (the one home's paid answer for that row), `basis` (a `payroll_hours_basis` basis) and
    `scheduled` (that row's own scheduled hours). Extra keys are ignored, so the caller passes its
    rows through unchanged rather than building a parallel shape.

    `refusal` is `payroll_evidence.refusal(res)` — pass it whenever the period is refused and the
    card publishes nothing. `coverage_note` is `coverage_sentence(res)`: what the measurement is
    worth when the period is NOT refused, carried alongside rather than folded into the total.

    Returns, with every hours figure `None` rather than `0.0` when there is nothing honest to put
    there:

        {state, sentence, refusal, coverage_note,
         actual_hours, unmeasured_hours, paid_hours, scheduled_hours,
         shifts, measured_shifts, unmeasured_shifts,
         hours_basis, hours_basis_reason, hours_basis_mixed, hours_by_basis}
    """
    rows = [r for r in (rows or ()) if isinstance(r, dict)]

    measured_hours = 0.0
    unmeasured_hours = 0.0
    paid_hours = 0.0
    scheduled_hours = 0.0
    measured_shifts = 0
    unmeasured_shifts = 0
    hours_by_basis: dict = {}

    for r in rows:
        basis = r.get("basis")
        hrs = _f(r.get("hours"))
        sched = _f(r.get("scheduled"))
        scheduled_hours += sched
        # The PAY figure is the one home's, whatever the basis: a non-paying basis contributes 0.0
        # because the one home already made it 0.0, not because this module re-decided it.
        if _hb.basis_pays_hours(basis):
            paid_hours += hrs
        # WHAT COUNTS AS MEASURED IS THE ALLOWLIST'S ANSWER, never a truthiness test on the hours —
        # `measured_zero` is a measurement whose answer is zero, and `schedule_opt_in` carries real
        # hours that measured nothing at all. Testing `hrs > 0` would get BOTH of them wrong.
        if _hb.basis_is_measurement(basis):
            measured_hours += hrs
            measured_shifts += 1
            hours_by_basis[basis] = round(hours_by_basis.get(basis, 0.0) + hrs, 2)
        else:
            # The honest size of the gap is the SCHEDULE's, not the paid figure's: under a
            # clocked-only org an unmeasured day pays 0.0, and reporting 0.0 as "not clocked" would
            # tell the reader nothing was missing.
            unmeasured_hours += sched
            unmeasured_shifts += 1
            # The basis still goes in the tally: it is why the figure is what it is, and the
            # dominant-basis fold must be able to see it (a wholly unmeasured period's basis word is
            # the reason the card has nothing to show).
            hours_by_basis[basis] = round(hours_by_basis.get(basis, 0.0) + sched, 2)

    fold = _hb.fold_bases(hours_by_basis)
    out = {
        "refusal": refusal or None,
        "coverage_note": coverage_note or None,
        "scheduled_hours": round(scheduled_hours, 2),
        "shifts": len(rows),
        "measured_shifts": measured_shifts,
        "unmeasured_shifts": unmeasured_shifts,
        "hours_by_basis": hours_by_basis,
        **fold,
    }

    # ── the states, in precedence order. A REFUSAL OUTRANKS EVERYTHING ──────────────────────────
    # — including a period that would otherwise look complete. That is the Cellfonz shape: every row
    # a reader can see is clocked while the ORG's coverage sits under its own floor, and publishing
    # the total would look like a finished figure rather than an unfinished measurement.
    if refusal:
        # ★ `scheduled_hours` SURVIVES A REFUSAL, deliberately. The refusal is about CLOCK
        # EVIDENCE: it says "we cannot state what was worked or what is owed". It does NOT say we do
        # not know what was BOOKED — the schedule is a plan this business wrote down, not a
        # measurement of anything, and nulling it would throw away the one figure that is still a
        # fact. It is also what lets the reader see that 18h were rostered and nothing measured
        # them, which is the actionable half. (This returned None until 2026-11-27, which disagreed
        # with the endpoint's own published figure — two answers to one question.)
        out.update({"state": REFUSED, "sentence": refusal, "actual_hours": None,
                    "unmeasured_hours": None, "paid_hours": None})
        return out
    if not rows:
        out.update({"state": NO_SHIFTS, "sentence": NO_SHIFTS_SENTENCE, "actual_hours": None,
                    "unmeasured_hours": None, "paid_hours": None})
        return out
    if measured_shifts == 0:
        # NOT `actual_hours: 0.0`. The owner's rule, and §14s's: a measured zero and an unmeasured
        # day must not print the same, so the unmeasured day declines to print a number at all.
        out.update({"state": NOTHING_MEASURED, "sentence": NO_PUNCH_SENTENCE, "actual_hours": None,
                    "unmeasured_hours": round(unmeasured_hours, 2),
                    "paid_hours": round(paid_hours, 2)})
        return out

    out["actual_hours"] = round(measured_hours, 2)
    out["unmeasured_hours"] = round(unmeasured_hours, 2)
    out["paid_hours"] = round(paid_hours, 2)
    if unmeasured_shifts:
        out["state"] = PARTLY_MEASURED
        out["sentence"] = (f"{out['actual_hours']:g}h measured · "
                           f"{out['unmeasured_hours']:g}h not clocked")
    else:
        out["state"] = MEASURED
        out["sentence"] = f"{out['actual_hours']:g}h measured"
    return out


def _month_of(period, today):
    """PURE: `(year, month)` from a dashboard `period` — `'2026-11'` or `'July 2026'`, the two
    spellings the commission rows are stored under. Unparseable / blank → today's month, which is
    what the endpoint has always done."""
    import calendar
    try:
        names = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
        p = str(period or "").strip()
        if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
            return int(p[:4]), int(p[5:7])
        parts = p.split()
        return int(parts[1]), names[parts[0].lower()]
    except Exception:
        return today.year, today.month


def window_for(period, today, settings=None):
    """PURE: `{start, end, label, basis}` — the window the card covers, both dates INCLUSIVE.

    THE WINDOW IS THE ORG'S PAY PERIOD, NOT A CALENDAR MONTH (owner directive 2026-11-27; charter
    rule 2: the schedule, hours approval, payroll, payroll tax and payroll expenses share ONE period
    resolver, never five copies). The card said *"this month"* while payroll ran on the org's pay
    cycle, so a rep's own figure could not reconcile with their payroll row even once the hours were
    right — a weekly tenant's card spanned four payslips.

    `settings` is `payroll_approval._pay_settings(org_id)`'s row (the ONE read of the cycle). The
    period CONTAINING a reference date is `core/router.pay_period_for` — dereferenced, not
    re-derived, so this cannot drift from the cycle the rest of payroll keeps.

    The reference date is `min(today, last day of the asked-for month)`: the CURRENT pay period on
    the main page, and the pay period that closed a past month when the picker names one. Either way
    `label` states the real window, so the card never has to be believed about its own dates.

    NO SETTINGS ⇒ THE CALENDAR MONTH, exactly as before, with `basis` saying so. A tenant that has
    not configured a pay cycle behaves precisely as it does today rather than being handed the house
    org's cycle — that default would be the per-tenant-code RULE TWO forbids (§COMMCALC-49.10).
    """
    from datetime import date as _date
    import calendar

    year, month = _month_of(period, today)
    last_day = calendar.monthrange(year, month)[1]
    month_lo = _date(year, month, 1)
    month_hi = _date(year, month, last_day)
    if not settings:
        return {"start": month_lo.isoformat(), "end": month_hi.isoformat(),
                "basis": "calendar_month",
                "label": f"{calendar.month_name[month]} {year}"}
    ref = min(today, month_hi)
    try:
        from app.modules.core.router import pay_period_for
        p = pay_period_for(settings, ref)
        return {"start": p["start"], "end": p["end"], "basis": "pay_period",
                "label": f"Pay period {p['start']} – {p['end']}"}
    except Exception:
        # The cycle could not be read. The calendar month is the honest fallback AND it says which
        # basis it is, so a reader is never told a month is a pay period.
        return {"start": month_lo.isoformat(), "end": month_hi.isoformat(),
                "basis": "calendar_month",
                "label": f"{calendar.month_name[month]} {year}"}


def pay_is_publishable(c):
    """PURE: may a MONEY figure be derived from this card?

    No when the period is refused — a refusal is about whether the measurement may be published at
    all, and a dollar figure is the loudest possible publication of it. Yes otherwise, including a
    wholly unmeasured period on a `schedule_fallback` org, because `paid_hours` is then a real
    obligation and blanking it would tell that rep they earn nothing (the strip-not-zero lie, from
    the other direction).
    """
    return bool(c) and c.get("state") != REFUSED and c.get("paid_hours") is not None
