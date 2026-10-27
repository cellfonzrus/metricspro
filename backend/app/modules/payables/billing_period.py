"""ONE home for "which BILLING PERIOD does this dated money fall in, and what says so".

Index §COMMCALC-34. Lock: `backend/harness_billing_period.py`.

OWNER REPORT, verbatim: *"Weekly Owed to Distributor does not show anything in luxelink"*.

THE CLASS, and it is the one §COMMCALC-26 already named once. `asset/router.get_owed_weekly` read
`commcalc.asset_ledger` filtered on `billing_friday` — and nothing else. That column is ONE carrier's
weekly consignment rhythm, stamped by ONE carrier's portal sweep, on ONE carrier's table. MEASURED
2026-10-26: Luxelink has **0** `asset_ledger` rows, so every figure on that report was Σ over an empty
set, and the screen said so by saying nothing. It is the fourth symptom of §COMMCALC-26.1's class
arriving on a fifth surface: *a carrier's arrangement written into code instead of declared*.

**A WEEKLY FRIDAY IS NOT A UNIVERSAL FACT ABOUT OWING MONEY.** Boost/VIP consigns and bills a batch
each week, so "which Friday does this device bill on" is a real question with a stored answer. Total /
MA raises an invoice per order and it falls due on its own terms — there is no Friday at all, and
inventing one would put a date on real money that no feed and no owner ever stated. So the rhythm is
DECLARED per carrier, and the three ways a period can be known are kept apart:

  * **`period_field`** — the feed already stamps the period on the row (Boost's `billing_friday`).
    This WINS wherever it exists, and nothing here recomputes it. A derived Friday that disagreed
    with the sweep's own would move a figure every other surface reconciles against.
  * **`weekly`** — the carrier bills on one declared weekday, so a due date rolls forward to the
    first such weekday on or after it. An undeclared weekday under this cadence is NOT MEASURED;
    defaulting it to Friday would be the same defect in a config column instead of in code.
  * **`per_due_date`** — the money is owed on the date it is owed. This is the house default,
    because it is the only one that asserts nothing: no rhythm, no roll-forward, no invented deadline.

`is_measured(basis)` is an ALLOWLIST, the §66 / §COMMCALC-33 shape: a basis this module has never
ruled on reads as NOT measured, so a cadence added later cannot quietly become a measurement by
appearing.

WHAT THIS MODULE DELIBERATELY DOES NOT DO: it does not read the money. The amounts and their dates
come from `payables/owed_read.py`, which is the ONE read both the Daily Owed surface and the weekly
roll-up share — so the week cannot total something the day disagrees with. This module only answers
which bucket a dated row belongs in.

PURE: no I/O, no framework import, no clock of its own (`today` is always passed), and no carrier,
tenant, product or store name (RULE TWO).
"""
import datetime

#: The money is owed on the date it is owed — no rhythm asserted. THE HOUSE DEFAULT.
CADENCE_PER_DUE_DATE = "per_due_date"
#: The carrier bills on one declared weekday; a due date rolls forward to the next such day.
CADENCE_WEEKLY = "weekly"

CADENCES = (CADENCE_PER_DUE_DATE, CADENCE_WEEKLY)

#: What answered. Only the bases in MEASURED_BASES are a measurement; see `is_measured`.
BASIS_PERIOD_FIELD = "period_field"     # the feed stamped the period itself — never recomputed here
BASIS_WEEKLY = "weekly_declared"        # derived from the carrier's own declared billing weekday
BASIS_DUE_DATE = "due_date"             # the due date IS the period
BASIS_HOUSE = "house_default"           # nothing declared, so the due date answers (same shape)
BASIS_NO_DATE = "no_date"               # the row carries no date at all — not a period of today
BASIS_WEEKDAY_UNDECLARED = "weekday_undeclared"   # cadence `weekly`, and nobody said which day

#: THE ALLOWLIST (§66). A basis absent from this tuple is not a measurement.
MEASURED_BASES = (BASIS_PERIOD_FIELD, BASIS_WEEKLY, BASIS_DUE_DATE, BASIS_HOUSE)

#: Monday is 0, matching `datetime.date.weekday()`, so the declared number needs no translation and
#: there is no off-by-one to get wrong between the column and the comparison.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: The sentence a surface shows where the rhythm could not be resolved. One wording, one home.
UNDECLARED_WEEKDAY_NOTE = (
    "This carrier is declared as billing on a weekly day, but which day was never declared, so the "
    "money below is grouped by its own due date instead of by a billing day. Declare the billing "
    "weekday to group it by week.")

PER_DUE_DATE_NOTE = (
    "This carrier raises an invoice per order and each one falls due on its own date, so there is no "
    "single weekly billing day. The dates below are the real due dates inside the period you chose — "
    "not a weekly batch.")


def is_measured(basis) -> bool:
    """True only for a basis this module has RULED is a measurement (§66 basis-is-evidence).

    Fail-CLOSED, the same posture as `recovered_amount.is_measured` and the opposite of
    `ledger_status.is_payable`: a period nobody measured must not read as a period, because a screen
    would then present money as billing on a date no carrier ever named.
    """
    return str(basis or "") in MEASURED_BASES


def _date(v):
    """A `date`, or None when the value is not one. Accepts a date, a datetime, or an ISO string —
    which is what a PostgREST read hands back. NEVER today: a missing date is missing."""
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v or "")[:10]
    if len(s) != 10:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _weekday(v):
    """The declared billing weekday as 0..6, or None when nothing usable was declared.

    A bool is refused explicitly: `True` is an `int` in Python and would silently mean Tuesday.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 6 else None


#: The keys `resolve_cadence` always returns. A CONFIG ROW IS ALSO A DICT, so "is this already
#: resolved?" cannot be `isinstance(x, dict)` — handing a `payable_source_map` row straight to
#: `period_of` would then have it read as a resolved cadence with no `cadence` key at all. Caught by
#: the first smoke test of this module; `_cfg` below is the one answer and every entry point uses it.
_RESOLVED_KEYS = ("cadence", "weekday", "period_field", "declared")


def _cfg(cadence):
    """A RESOLVED cadence, whether the caller passed one or passed the config row it comes from."""
    if isinstance(cadence, dict) and all(k in cadence for k in _RESOLVED_KEYS):
        return cadence
    return resolve_cadence(cadence)


def resolve_cadence(map_row=None):
    """The carrier's DECLARED billing rhythm, or the house default.

    Returns {cadence, weekday, period_field, declared}. `declared` is False where the row said
    nothing, so a surface can print "house default" rather than implying the carrier chose this.

    An unrecognised cadence falls to the house default rather than being honoured: a value nobody
    ruled on is not a rhythm, and acting on it would be acting on a typo.
    """
    row = map_row or {}
    want = str(row.get("billing_cadence") or "").strip().lower()
    cadence = want if want in CADENCES else CADENCE_PER_DUE_DATE
    field = str(row.get("billing_period_field") or "").strip() or None
    return {"cadence": cadence, "weekday": _weekday(row.get("billing_weekday")),
            "period_field": field,
            "declared": bool(want in CADENCES or field)}


def next_weekday_on_or_after(d, weekday):
    """PURE: the first `weekday` on or ON OR AFTER `d` — the day this money actually bills.

    "On or after", not "after": money due ON the billing day bills that day, and rolling it to the
    next week would postpone a real liability by seven days. Returns None where either input is
    unusable, because a period cannot be derived from a date nobody recorded.
    """
    day = _date(d)
    w = _weekday(weekday)
    if day is None or w is None:
        return None
    return day + datetime.timedelta(days=(w - day.weekday()) % 7)


def week_window(billing_day):
    """PURE: the (start, end) ISO DATE STRINGS the devices billing on `billing_day` were due inside.

    The period a weekly carrier bills on `billing_day` is the seven days ENDING on it — so a screen
    can state the range it rolled up instead of showing a single date and leaving the reader to guess
    whether the six days before it are in or out.

    STRINGS, NOT `date` OBJECTS, and that is the contract rather than a convenience. Every value this
    module hands out is an ISO string (`period_of`'s key / start / end all are), and the one caller
    outside it feeds the pair straight into `date_window.resolve_window`, which parses strings. An
    earlier draft returned `date` objects here and the window silently arrived unusable — the same
    shape of defect as a field that means two things, one function returning two types.

    IT TAKES NO WEEKDAY, and that is deliberate. The span of a period is seven days ending on its
    billing day whatever weekday that is, so a weekday parameter here would be a validity gate and
    nothing more — and the one caller outside this module had to pass SOMETHING through it, which
    made it pass `4`. A guessed Friday in a caller is the same defect as a guessed Friday in the
    home, one import away; the parameter is gone so there is nothing to guess.
    """
    day = _date(billing_day)
    if day is None:
        return None, None
    return (day - datetime.timedelta(days=6)).isoformat(), day.isoformat()


def period_of(due_date, cadence=None):
    """PURE: which billing period a dated row falls in, and WHAT SAID SO.

    Returns {key, start, end, label, basis, measured}. `key` is the ISO date a screen groups on, or
    None where no period could be resolved — which a surface must render as unknown rather than
    folding into the nearest bucket.

    THE UNDECLARED WEEKDAY DOES NOT VANISH. Under cadence `weekly` with no declared day, the row
    keeps its own due date as the period and carries `weekday_undeclared` — so the money is still
    shown and still counted, and the screen says the grouping is not the carrier's own. Dropping it
    would hide a real liability behind a missing config row, which is the §COMMCALC-29 defect; and
    guessing a day would invent a billing date, which is the §COMMCALC-26.3 one.
    """
    cfg = _cfg(cadence)
    day = _date(due_date)
    if day is None:
        return {"key": None, "start": None, "end": None, "label": "no due date recorded",
                "basis": BASIS_NO_DATE, "measured": False}
    if cfg["cadence"] == CADENCE_WEEKLY:
        bill = next_weekday_on_or_after(day, cfg["weekday"])
        if bill is None:
            return {"key": day.isoformat(), "start": day.isoformat(), "end": day.isoformat(),
                    "label": "due %s" % day.isoformat(),
                    "basis": BASIS_WEEKDAY_UNDECLARED, "measured": False}
        start, end = week_window(bill)
        return {"key": bill.isoformat(), "start": start, "end": end,
                "label": "%s %s" % (WEEKDAYS[cfg["weekday"]], bill.isoformat()),
                "basis": BASIS_WEEKLY, "measured": True}
    return {"key": day.isoformat(), "start": day.isoformat(), "end": day.isoformat(),
            "label": "due %s" % day.isoformat(),
            "basis": (BASIS_DUE_DATE if cfg["declared"] else BASIS_HOUSE), "measured": True}


def period_of_row(row, cadence=None, date_field="due_date"):
    """The period for one row, reading a STAMPED period first where the carrier declares one.

    THE STAMPED PERIOD WINS AND IS NEVER RECOMPUTED. Where a feed already carries the billing day it
    assigned, that value is the carrier's own answer; deriving a competing one would move a figure
    other surfaces reconcile against, and the two would then drift exactly as §19.18 describes.
    """
    cfg = _cfg(cadence)
    r = row or {}
    field = cfg.get("period_field")
    if field:
        stamped = _date(r.get(field))
        if stamped is not None:
            start, end = week_window(stamped)
            return {"key": stamped.isoformat(), "start": start, "end": end,
                    "label": stamped.isoformat(), "basis": BASIS_PERIOD_FIELD, "measured": True}
    return period_of(r.get(date_field), cfg)


def roll_up(rows, cadence=None, *, date_field="due_date", amount_of=None, count_of=None):
    """PURE: the dated rows grouped into billing periods, plus what could not be placed.

    Returns {periods, total, unplaced_rows, unplaced_amount, bases, note}. `periods` is a list,
    ordered by key, each carrying key / start / end / label / basis / count / amount.

    NOTHING IS DROPPED AND NOTHING IS GUESSED. A row whose period could not be resolved at all (no
    date) is counted in `unplaced_rows` with its money in `unplaced_amount`, beside the periods
    rather than inside one — a liability nobody could date is reported, never quietly dated. `bases`
    names every basis that answered, so a screen can say the grouping is the carrier's own or is not.
    """
    cfg = _cfg(cadence)
    amount_of = amount_of or (lambda r: (r or {}).get("owed"))
    count_of = count_of or (lambda _r: 1)
    buckets, bases = {}, {}
    unplaced_n, unplaced_amt = 0, 0.0
    for r in rows or []:
        if not r:
            continue
        try:
            amt = float(amount_of(r) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        n = int(count_of(r) or 0)
        p = period_of_row(r, cfg, date_field)
        bases[p["basis"]] = bases.get(p["basis"], 0) + 1
        if not p["key"]:
            unplaced_n += n
            unplaced_amt += amt
            continue
        e = buckets.setdefault(p["key"], {"key": p["key"], "start": p["start"], "end": p["end"],
                                          "label": p["label"], "basis": p["basis"],
                                          "measured": p["measured"], "count": 0, "amount": 0.0})
        e["count"] += n
        e["amount"] += amt
    periods = [dict(v, amount=round(v["amount"], 2)) for v in buckets.values()]
    periods.sort(key=lambda x: x["key"])
    note = None
    if BASIS_WEEKDAY_UNDECLARED in bases:
        note = UNDECLARED_WEEKDAY_NOTE
    elif cfg["cadence"] == CADENCE_PER_DUE_DATE and periods:
        note = PER_DUE_DATE_NOTE
    return {"periods": periods, "total": round(sum(p["amount"] for p in periods), 2),
            "unplaced_rows": unplaced_n, "unplaced_amount": round(unplaced_amt, 2),
            "bases": bases, "note": note,
            "cadence": cfg["cadence"], "weekday": cfg["weekday"],
            "cadence_declared": cfg["declared"]}
