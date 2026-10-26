"""ONE home for "which DAYS is this surface measuring" — the day-window fact, dereferenced.

WHY THIS MODULE EXISTS. Three separate surfaces already answered "is this row inside the chosen
date range?" and a fourth was about to: `vip_invoice_filter.in_date_window` (§15v, the Distributor
Invoices selector), the `/payables/owed-by-date` endpoint's inline `gte`/`lte` pair, and the
`/payables/forecast` velocity cutoff, which stated its window as a NUMBER OF DAYS and so could not
be compared with any of them. A window is a single fact — where it starts, where it ends, whether
both ends are inclusive, and what a row with no date does — and a second copy of it is a future
divergence (CLAUDE.md: "one fact, one home, dereferenced — never copied"). So the predicate lives
here and every caller READS it.

THE SEMANTICS, stated once:

  • Both ends are INCLUSIVE. A one-day window (from == to) selects that day.
  • An empty end is OPEN. '' / '' selects everything.
  • Comparison is made on DAYS, never on a timestamp against a bare date — otherwise a row booked
    at 08:49 on the `to` day is silently dropped because '2026-06-15T08:49:34+00:00' > '2026-06-15'.
  • A row with NO date is FAIL-CLOSED: excluded once a window is set, in scope when none is. This
    is the convention `frontend/src/lib/standard-filters.ts periodOk` already uses on the client,
    so the screen and the server drop the same rows instead of each dropping a different set.

AND the other half of the same fact: a window stated as "the last N days" is the same window with
its ends left implicit. `resolve_window` makes them explicit, so a surface with a look-back box and
a surface with a date range are the same mechanism with two front ends, and the day COUNT a rate is
divided by comes from the window itself rather than from whichever number the caller happened to
pass. `basis` names which front end answered, so a response never implies a range it was not given.

PURE. No DB, no framework, no app imports — provable by `harness_payables_date_window.py`.
"""
import re
from datetime import date, timedelta

#: The widest window any surface here may ask for, in days. A look-back is a scan bound, not a
#: preference, so it is clamped rather than trusted.
MAX_DAYS = 365

BASIS_RANGE = "explicit_range"      # the caller gave at least one real date
BASIS_LOOKBACK = "lookback_days"    # the caller gave only "the last N days"


#: A leading calendar day, dashes or slashes, month/day optionally unpadded — the same shape the
#: client's `standard-filters.ymd` accepts, so a value normalizes identically on both sides.
_DAY_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def day(v) -> str:
    """A date / timestamp / 'YYYY-MM-DD…' string reduced to its YYYY-MM-DD day; '' when there is
    none. The one place a value becomes comparable with a window bound.

    IT VALIDATES rather than truncating. `str(v)[:10]` turns any 10-character string into a bound —
    so a malformed `?start=` compares as the nonsense it is and silently empties the report, and a
    row carrying junk in its date column compares as a date. Anything that is not a leading calendar
    day is NO DAY: as a row value that is fail-closed (excluded from a window), and as a bound it is
    no bound at all, which leaves the window open instead of closing it on garbage."""
    s = str(v or "").strip()
    m = _DAY_RE.match(s)
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"


def in_window(value, date_from: str = "", date_to: str = "") -> bool:
    """PURE: is this DATE VALUE inside [date_from, date_to], both ends inclusive?

    Fail-closed on a value with no date once a window is set (see the module docstring)."""
    f, t = day(date_from), day(date_to)
    if not f and not t:
        return True
    d = day(value)
    if not d:
        return False
    if f and d < f:
        return False
    if t and d > t:
        return False
    return True


def row_in_window(row, date_from: str = "", date_to: str = "", field: str = "") -> bool:
    """`in_window` over a row's own date FIELD. A row that is not a mapping, or a field that is not
    on it, reads as dateless — which is fail-closed, not in-scope, so a column list that forgets to
    fetch the date field empties the window instead of quietly ignoring it."""
    v = row.get(field) if (field and hasattr(row, "get")) else None
    return in_window(v, date_from, date_to)


def clamp_days(n, default: int = 7) -> int:
    """A day count forced into [1, MAX_DAYS]. Anything unreadable becomes `default`."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        v = default
    if v < 1:
        v = default if default >= 1 else 1
    return min(v, MAX_DAYS)


def resolve_window(date_from: str = "", date_to: str = "", lookback_days: int = 7, today=None) -> dict:
    """The window a surface is measuring, with BOTH ends explicit and its day COUNT named.

    Returns {'start', 'end', 'days', 'basis', 'days_measured'}:

      • a real `date_from` and/or `date_to` -> `basis` is `explicit_range`. A window given a start
        and no end is CLOSED AT TODAY ("from the 1st" means "from the 1st until now"), so its day
        count is a measured fact rather than a guess;
      • neither -> `basis` is `lookback_days`, `end` is today and `start` is `lookback_days - 1`
        days before it, so a 1-day look-back is today and a 7-day look-back is seven DAYS including
        today — never eight, which is what `today - 7` quietly measures.

    `days` is the INCLUSIVE day count of the window, which is what a per-day rate must be divided
    by, clamped like any look-back: a caller cannot ask for a 20-year scan. The ONE case where the
    window cannot supply it is an open START (`date_to` alone — "everything up to the 9th"): there
    is no first day to count from, so `days` falls back to the look-back and `days_measured` is
    False. A caller dividing by `days` must say which it got rather than present a rate the window
    does not support.
    """
    f, t = day(date_from), day(date_to)
    back = clamp_days(lookback_days)
    ref = today or date.today()
    if isinstance(ref, str):
        ref = date.fromisoformat(day(ref))
    if f or t:
        if f and not t:
            t = ref.isoformat()                 # "from the 1st" = from the 1st until today
        days, measured = back, False
        if f and t:
            try:
                days = clamp_days((date.fromisoformat(t) - date.fromisoformat(f)).days + 1, back)
                measured = True
            except ValueError:
                days, measured = back, False
        return {"start": f, "end": t, "days": days, "basis": BASIS_RANGE, "days_measured": measured}
    end = ref
    start = ref - timedelta(days=back - 1)
    return {"start": start.isoformat(), "end": end.isoformat(), "days": back,
            "basis": BASIS_LOOKBACK, "days_measured": True}


def apply_window(q, column: str, w: dict):
    """Add the window's bounds to a PostgREST query as `gte`/`lte` on `column`, skipping an open
    end. The server-side twin of `in_window`, so a windowed endpoint and a windowed client filter
    cannot disagree about which end is inclusive."""
    if w.get("start"):
        q = q.gte(column, w["start"])
    if w.get("end"):
        q = q.lte(column, w["end"])
    return q


def undated(rows, field: str) -> int:
    """How many rows carry no date in `field` — the count a windowed surface must REPORT, because
    fail-closed means those rows vanish from every range and a reader would otherwise read their
    absence as "nothing is owed" rather than "nothing is dated"."""
    return sum(1 for r in rows if not day(r.get(field) if hasattr(r, "get") else None))

# ── THE DEFAULT WINDOW A PAYMENT SCREEN OPENS ON (owner report 2026-10-26) ────────────────────────
# Owner, verbatim: *"should auaomatically pick teh dates from today and 3 days ahead ... the month and
# date shoudl not be entered manually it should picked for otehr dates"*. A screen whose job is "who
# do I pay in the next few days" should not need a date typed into it before it says anything.
#
# IT LIVES HERE, not in the page, because the page and the endpoint would otherwise each hold a
# default and drift — which is the whole reason this module exists (§COMMCALC-25). The endpoint
# computes it and RETURNS it, and the pickers render what came back, so there is exactly one answer
# to "which days am I looking at".
DEFAULT_AHEAD_DAYS = 3
BASIS_AHEAD_DEFAULT = "default_ahead"


def ahead_window(today, days=DEFAULT_AHEAD_DAYS):
    """PURE: today through `days` days ahead, both ends INCLUSIVE — the same inclusivity every other
    window here uses, so a payment due on the last day is in the window rather than one day out of it.

    `days=0` is honoured and means today alone. A negative `days` is clamped to 0 rather than
    returning a backwards window that silently matches nothing.
    """
    d = day(today)
    try:
        n = max(0, int(days))
    except (TypeError, ValueError):
        n = DEFAULT_AHEAD_DAYS
    end = date.fromisoformat(d) + timedelta(days=n)
    w = resolve_window(d, end.isoformat(), 0)
    w["basis"] = BASIS_AHEAD_DEFAULT
    return w
