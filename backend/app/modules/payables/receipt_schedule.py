"""ONE home for "on what DAY does this earned money reach us, and what says so".

Index §PAYABLES-02. Lock: `backend/harness_cash_calendar.py`.

OWNER ASK, verbatim: *"i need a report for the expected payments for teh next payout cycle by the day
as acalender display for all tenants"*.

THE CLASS, and it is the one §COMMCALC-26.1 and §COMMCALC-34 have each already named once. "When does
the carrier pay us" has never been a fact anywhere in this platform. The obvious build puts it in the
report — a day-of-month literal, or a weekday inferred from the last few statements — and that is a
carrier's commercial arrangement written into code instead of declared. It would then be ONE tenant's
arrangement answering for every tenant, which is the defect CLAUDE.md's "Every feature is for EVERY
tenant" rule exists to stop. So the rhythm is DECLARED per carrier and this module only answers which
day a dated dollar lands on.

THERE IS NO HOUSE DEFAULT HERE, AND THAT IS THE DESIGN — NOT AN OMISSION
────────────────────────────────────────────────────────────────────────
`payables/billing_period.py` has a house default (`per_due_date`) and is right to: on the OUTBOUND
side the row already carries the date the money is owed, so "the money is owed on the date it is owed"
asserts nothing. On the INBOUND side there is no payment date on the row at all — only the date the
money was EARNED. Any default would therefore INVENT a payment date out of a sale date, which is the
§COMMCALC-26.3 defect wearing a different hat. So:

    nothing declared  ->  no date, basis `cadence_undeclared`, NOT measured, and the surface says so.

`is_measured(basis)` is an ALLOWLIST (§66 / §COMMCALC-33 shape): a basis this module has never ruled
on reads as NOT a measurement, so a cadence added later cannot quietly become one by appearing.

THE FIVE WAYS A RECEIPT DATE CAN BE KNOWN, kept apart because they are different evidence:

  * **`statement_date`** — the feed itself stamps the day the money was (or will be) paid. This is the
    carrier's own answer and is NEVER recomputed, the same rule the outbound `period_field` obeys. A
    row under this cadence carrying no stamp is UNMEASURED, not estimated by some other rule: the
    tenant declared that the feed answers, so an unstamped row means the feed has not answered yet.
  * **`weekly`** — the carrier pays on one declared weekday. A due dollar rolls forward to the first
    such weekday ON OR AFTER the earning date, through
    `billing_period.next_weekday_on_or_after` — DEREFERENCED, because the forward roll already has a
    home and a second copy of that arithmetic is a future divergence (§19.18).
  * **`semi_monthly`** — the carrier pays on two or more declared days of the month.
  * **`monthly`** — one declared day of the month. Answered by the SAME function as `semi_monthly`,
    because "the next declared day of month on or after this one" is one question, not two.
  * **`net_days_after_period_end`** — the carrier pays a declared number of days after the money is
    earned. A declared lag of 0 is a real same-day arrangement and is measured; an ABSENT lag is not.

A DECLARED CADENCE WITH ITS DAY LEFT BLANK IS ITS OWN STATE (`day_undeclared`) and is not measured.
Defaulting it would be the same defect in a config column instead of in code; dropping the money
would hide a real receivable behind a missing config row, which is §COMMCALC-29. So the money is
reported, placed nowhere, and the screen says which half of the declaration is missing.

PURE: no I/O, no framework import, no carrier/tenant/product name (RULE TWO), and NO CLOCK OF ITS
OWN. `today` is never read here — a receipt date that moved depending on when the report happened to
run would not be a fact about the carrier.
"""
import calendar
import datetime

from app.modules.payables import billing_period as _bp

#: The feed stamps the payment day itself — never recomputed here.
CADENCE_STATEMENT_DATE = "statement_date"
#: One declared weekday.
CADENCE_WEEKLY = "weekly"
#: Two or more declared days of the month.
CADENCE_SEMI_MONTHLY = "semi_monthly"
#: One declared day of the month.
CADENCE_MONTHLY = "monthly"
#: A declared number of days after the money was earned.
CADENCE_NET_DAYS = "net_days_after_period_end"

CADENCES = (CADENCE_STATEMENT_DATE, CADENCE_WEEKLY, CADENCE_SEMI_MONTHLY,
            CADENCE_MONTHLY, CADENCE_NET_DAYS)

#: What answered. Only the bases in MEASURED_BASES are a measurement; see `is_measured`.
BASIS_STATEMENT_FIELD = "statement_field"      # the feed's own stamp — never recomputed
BASIS_WEEKLY = "weekly_declared"
BASIS_SEMI_MONTHLY = "semi_monthly_declared"
BASIS_MONTHLY = "monthly_declared"
BASIS_NET_DAYS = "net_days_declared"
BASIS_UNDECLARED = "cadence_undeclared"        # nobody said when this carrier pays
BASIS_DAY_UNDECLARED = "day_undeclared"        # a cadence was declared; its day was not
BASIS_NO_DATE = "no_date"                      # the row carries no earning date at all

#: THE ALLOWLIST (§66). A basis absent from this tuple is not a measurement.
MEASURED_BASES = (BASIS_STATEMENT_FIELD, BASIS_WEEKLY, BASIS_SEMI_MONTHLY,
                  BASIS_MONTHLY, BASIS_NET_DAYS)

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: The one place each state is put into words, so every surface says it identically.
UNDECLARED_NOTE = ("Nobody has declared when this carrier pays us, so no expected payment can be "
                   "placed on a day. Declare the payout cycle to fill the calendar.")
DAY_UNDECLARED_NOTE = ("A payout cycle is declared for this carrier but the day it pays on is not, "
                       "so the money is shown without a date. Declare the day to place it.")
STATEMENT_UNSTAMPED_NOTE = ("This carrier's payout cycle says its own statement carries the payment "
                            "date. The rows below have no such date yet, so none was derived.")

BASIS_WORDS = {
    BASIS_STATEMENT_FIELD: "the carrier's own statement date",
    BASIS_WEEKLY: "the carrier's declared pay weekday",
    BASIS_SEMI_MONTHLY: "the carrier's declared pay days of the month",
    BASIS_MONTHLY: "the carrier's declared pay day of the month",
    BASIS_NET_DAYS: "the carrier's declared days-after-earning terms",
    BASIS_UNDECLARED: "not measured — no payout cycle declared",
    BASIS_DAY_UNDECLARED: "not measured — the pay day is not declared",
    BASIS_NO_DATE: "not measured — the row carries no date",
}


def can_date(schedule=None) -> bool:
    """Is this declaration COMPLETE enough to place a dollar on a day at all?

    NOT the same question as "did it place any" — and conflating the two is a defect this module's
    own integration tests caught. A tenant whose cycle is fully declared but whose rows a STORE
    FILTER narrowed to none has measured a real zero: the declaration answered and nothing was
    there. A tenant with no cycle declared has measured nothing. A caller that read row count as
    measurement would print "not measured" over an honest $0.00, which is the §COMMCALC-29 defect
    pointing the other way — and a store with genuinely nothing coming in would look like a setup
    fault forever.

    `statement_date` can always date in principle (its answer is on the row), so the question for it
    is only whether the cadence was declared.
    """
    cfg = _cfg(schedule)
    if cfg["cadence"] is None:
        return False
    if cfg["cadence"] == CADENCE_WEEKLY:
        return cfg["weekday"] is not None
    if cfg["cadence"] in (CADENCE_SEMI_MONTHLY, CADENCE_MONTHLY):
        return bool(cfg["days"])
    if cfg["cadence"] == CADENCE_NET_DAYS:
        return cfg["net_days"] is not None
    return cfg["cadence"] == CADENCE_STATEMENT_DATE


def is_measured(basis) -> bool:
    """True only for a basis this module has RULED is a measurement (§66 basis-is-evidence).

    Fail-CLOSED. A receipt date nobody measured must not read as a receipt date, because a screen
    would then promise cash arriving on a day no carrier ever named — and the owner would plan
    against it.
    """
    return str(basis or "") in MEASURED_BASES


def basis_word(basis) -> str:
    """The basis in words, from the one map, so a basis a screen has never seen reads as itself
    rather than rendering as the carrier's own arrangement."""
    return BASIS_WORDS.get(str(basis or ""), str(basis or "unknown"))


def _date(v):
    """A `date`, or None when the value is not one. NEVER today: a missing date is missing."""
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


def _int_in(v, lo, hi):
    """An int inside [lo, hi], or None. A bool is refused explicitly: `True` is an `int` in Python
    and would silently mean the 1st (or Tuesday) — the same trap `billing_period._weekday` names."""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _days_of_month(v):
    """The declared pay days as a sorted tuple of 1..31, or () when nothing usable was declared.

    Accepts a list, a tuple, a single number or a comma-separated string, because a config row may
    have been saved from a form, from SQL or from JSON and the declaration means the same thing in
    all three. Order is irrelevant to the answer, so it is normalised here once rather than relied on.
    """
    if v is None or isinstance(v, bool):
        return ()
    items = v if isinstance(v, (list, tuple, set)) else str(v).split(",")
    out = {d for d in (_int_in(x, 1, 31) for x in items) if d is not None}
    return tuple(sorted(out))


#: The keys `resolve_schedule` always returns. A CONFIG ROW IS ALSO A DICT (the §COMMCALC-34 trap),
#: so "is this already resolved?" cannot be `isinstance(x, dict)`.
_RESOLVED_KEYS = ("cadence", "weekday", "days", "net_days", "declared")


def _cfg(schedule):
    """A RESOLVED schedule, whether the caller passed one or passed the config row it comes from."""
    if isinstance(schedule, dict) and all(k in schedule for k in _RESOLVED_KEYS):
        return schedule
    return resolve_schedule(schedule)


def resolve_schedule(map_row=None):
    """The carrier's DECLARED payout rhythm, or NOTHING.

    Returns {cadence, weekday, days, net_days, declared}. `cadence` is None where nothing usable was
    declared — there is no house default on this side of the money (see the module docstring), so a
    surface can print "not declared" rather than implying the carrier chose a rhythm it never named.

    An unrecognised cadence resolves to None rather than being honoured: a value nobody ruled on is
    not a rhythm, and acting on it would be acting on a typo.
    """
    row = map_row or {}
    want = str(row.get("payout_cadence") or "").strip().lower()
    cadence = want if want in CADENCES else None
    days = _days_of_month(row.get("payout_days"))
    if not days:
        one = _int_in(row.get("payout_day_of_month"), 1, 31)
        days = (one,) if one is not None else ()
    return {"cadence": cadence,
            "weekday": _int_in(row.get("payout_weekday"), 0, 6),
            "days": days,
            "net_days": _int_in(row.get("payout_net_days"), 0, 365),
            "declared": cadence is not None}


def next_day_of_month_on_or_after(d, days):
    """PURE: the first declared day-of-month ON OR AFTER `d`, or None.

    "On or after", not "after": money earned ON the pay day is paid that day, and rolling it forward
    would postpone real cash by a whole cycle. The same rule `next_weekday_on_or_after` obeys, which
    is why this function sits beside it in spirit rather than inventing a different one.

    A DECLARED DAY A MONTH DOES NOT HAVE IS THAT MONTH'S LAST DAY. A carrier that pays on the 31st
    pays on 28 February — it does not skip February and it does not pay on 3 March. Clamping is the
    only reading that keeps one payment per declared day per month, which is what a cycle is.
    """
    day = _date(d)
    want = _days_of_month(days)
    if day is None or not want:
        return None
    for probe_month in (0, 1):
        y, m = day.year, day.month + probe_month
        if m > 12:
            y, m = y + 1, m - 12
        last = calendar.monthrange(y, m)[1]
        for dom in want:
            cand = datetime.date(y, m, min(dom, last))
            if cand >= day:
                return cand
    return None


def receipt_date(earned_on, schedule=None):
    """PURE: the day money earned on `earned_on` is expected to reach us, and WHAT SAID SO.

    Returns {date, basis, measured, label}. `date` is an ISO string or None — and None must be
    rendered as unknown by every caller, never folded onto today, onto the earning date, or onto the
    nearest day with money already on it.

    `statement_date` is absent from this function on purpose: that cadence is answered by the ROW's
    own stamp, which only `receipt_date_of_row` can see.
    """
    cfg = _cfg(schedule)
    day = _date(earned_on)
    if cfg["cadence"] is None:
        return {"date": None, "basis": BASIS_UNDECLARED, "measured": False,
                "label": basis_word(BASIS_UNDECLARED)}
    if day is None:
        return {"date": None, "basis": BASIS_NO_DATE, "measured": False,
                "label": basis_word(BASIS_NO_DATE)}

    if cfg["cadence"] == CADENCE_WEEKLY:
        # DEREFERENCED, never re-derived: the outbound side already owns "the first such weekday on
        # or after this date", and two copies of it would be free to disagree the moment either
        # learned something the other did not (§19.18).
        got = _bp.next_weekday_on_or_after(day, cfg["weekday"])
        if got is None:
            return {"date": None, "basis": BASIS_DAY_UNDECLARED, "measured": False,
                    "label": basis_word(BASIS_DAY_UNDECLARED)}
        return {"date": got.isoformat(), "basis": BASIS_WEEKLY, "measured": True,
                "label": "%s %s" % (WEEKDAYS[cfg["weekday"]], got.isoformat())}

    if cfg["cadence"] in (CADENCE_SEMI_MONTHLY, CADENCE_MONTHLY):
        got = next_day_of_month_on_or_after(day, cfg["days"])
        if got is None:
            return {"date": None, "basis": BASIS_DAY_UNDECLARED, "measured": False,
                    "label": basis_word(BASIS_DAY_UNDECLARED)}
        basis = (BASIS_MONTHLY if cfg["cadence"] == CADENCE_MONTHLY else BASIS_SEMI_MONTHLY)
        return {"date": got.isoformat(), "basis": basis, "measured": True,
                "label": got.isoformat()}

    if cfg["cadence"] == CADENCE_NET_DAYS:
        lag = cfg["net_days"]
        if lag is None:
            return {"date": None, "basis": BASIS_DAY_UNDECLARED, "measured": False,
                    "label": basis_word(BASIS_DAY_UNDECLARED)}
        got = day + datetime.timedelta(days=lag)
        return {"date": got.isoformat(), "basis": BASIS_NET_DAYS, "measured": True,
                "label": "%s (+%d days)" % (got.isoformat(), lag)}

    # CADENCE_STATEMENT_DATE reached without a row: the declaration says the FEED answers, and
    # nothing here may answer in its place.
    return {"date": None, "basis": BASIS_NO_DATE, "measured": False,
            "label": basis_word(BASIS_NO_DATE)}


def receipt_date_of_row(row, schedule=None, date_field="payment_date"):
    """The receipt date for one row, reading the feed's OWN stamp where the carrier declares one.

    THE STAMPED DATE WINS AND IS NEVER RECOMPUTED, the same rule `billing_period.period_of_row`
    obeys for the outbound period. A derived date that disagreed with the carrier's own would move a
    figure the commission surfaces reconcile against.

    `date_field` is the column holding the row's date, and the CALLER supplies it from the lineage
    registry — this module declares no column name of its own, so "which column dates this feed"
    keeps the one home it already has.
    """
    cfg = _cfg(schedule)
    r = row or {}
    if cfg["cadence"] == CADENCE_STATEMENT_DATE:
        stamped = _date(r.get(date_field))
        if stamped is None:
            return {"date": None, "basis": BASIS_NO_DATE, "measured": False,
                    "label": basis_word(BASIS_NO_DATE)}
        return {"date": stamped.isoformat(), "basis": BASIS_STATEMENT_FIELD, "measured": True,
                "label": stamped.isoformat()}
    return receipt_date(r.get(date_field), cfg)


def roll_up(rows, schedule=None, *, date_field="payment_date", amount_of=None, count_of=None):
    """PURE: dated rows grouped onto the DAYS the money is expected, plus what could not be placed.

    Returns {days, total, unplaced_rows, unplaced_amount, bases, note, measured, cadence,
    cadence_declared}. `days` is ordered, each carrying date / count / amount / basis / label.

    NOTHING IS DROPPED AND NOTHING IS GUESSED. A row no cadence could date is counted in
    `unplaced_rows` with its money in `unplaced_amount`, BESIDE the days rather than inside one.
    `measured` is False whenever no day could be dated at all, so a caller cannot read an empty
    calendar as "no money is coming" — the §COMMCALC-29 distinction, kept explicit.
    """
    cfg = _cfg(schedule)
    amount_of = amount_of or (lambda r: (r or {}).get("amount"))
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
        try:
            n = int(count_of(r) or 0)
        except (TypeError, ValueError):
            n = 0
        got = receipt_date_of_row(r, cfg, date_field)
        bases[got["basis"]] = bases.get(got["basis"], 0) + 1
        if not got["date"] or not got["measured"]:
            unplaced_n += n
            unplaced_amt += amt
            continue
        e = buckets.setdefault(got["date"], {"date": got["date"], "basis": got["basis"],
                                             "label": got["label"], "count": 0, "amount": 0.0})
        e["count"] += n
        e["amount"] += amt
    days = [dict(v, amount=round(v["amount"], 2)) for v in buckets.values()]
    days.sort(key=lambda x: x["date"])

    note = None
    if cfg["cadence"] is None:
        note = UNDECLARED_NOTE
    elif BASIS_DAY_UNDECLARED in bases:
        note = DAY_UNDECLARED_NOTE
    elif cfg["cadence"] == CADENCE_STATEMENT_DATE and BASIS_NO_DATE in bases and not days:
        note = STATEMENT_UNSTAMPED_NOTE
    return {"days": days, "total": round(sum(d["amount"] for d in days), 2),
            "unplaced_rows": unplaced_n, "unplaced_amount": round(unplaced_amt, 2),
            "bases": bases, "note": note,
            # A roll-up is a measurement where the DECLARATION could date a dollar at all — not
            # where it happened to date one. An empty result under a complete declaration is a real
            # zero (nothing was there); an empty result under no declaration is an absence. Reading
            # row count as measurement would print "not measured" over an honest $0.00.
            "measured": can_date(cfg),
            "cadence": cfg["cadence"], "cadence_declared": cfg["declared"]}
