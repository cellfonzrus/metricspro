"""WHICH MONTH A FEED ROW BELONGS TO — one home, dereferenced (owner report 2026-10-05).

Owner: *"check the commission reports … as it seems very high like we were check the other day"*.
It was high. The commission was counted twice.

THE DEFECT, measured live on the house org 2026-10-05. `commcalc.raw_payment_detail` is stamped with
the period an operator PICKS at upload and replaced per (org, period). The processor portal serves
that report over a rolling in-arrears WINDOW, not a month: the 2026-10-04 pull covered 2026-09-05 …
2026-10-02, so 18,231 rows carrying $415,862.56 of SEPTEMBER's commission were filed a second time
under "October 2026". October then read $431,323.25 where its own rows total $15,460.69 — 28x, and
the carrier's own compensation statement agrees to the penny with the smaller figure. The 2026-08-24
pull did the same to August with July's money ($147,435.59, 61,681 rows).

THE CLASS, not the instance (house rule: *a fix is a DESIGN fix or it is not a fix*). The general fact
that was wrong is not "one tenant's October is double-counted"; it is **"a feed row's month came from
the upload instead of from the row"**. That question already had ONE home —
`data_lineage_registry.DATA_DATE_COLUMN_BY_TABLE` — and `router.DATE_KEYED` kept its own four-entry
copy of it, so the per-day replace that makes a rolling re-pull idempotent, and the row-date period
stamp that keeps a month honest, reached the feeds somebody remembered to list. §19.18's shape for the
fourth time. It is also why `raw_comp_report` was given a multi-month upload guard in 2026-08 while
its SIBLING feed, pulled from the same portal on the same window, never was.

WHAT THIS MODULE IS. The one parser for "what month is this day in", plus the two questions an ingest
has to ask before it replaces anything:

  • `period_of_day(value)`     → ("October 2026", 10, 2026) for one date, in any spelling the feeds use.
  • `month_spread(values)`     → [(period, rows)] newest first — every month a file actually covers.
  • `day_stamp(rows, column)`  → re-stamps each row's period/period_month/period_year from its OWN
                                 data date, or REFUSES (and says why) when any row cannot prove its day.

WHY A REFUSAL AND NOT A BEST EFFORT. A day-grain replace deletes the days the file covers. A row whose
day cannot be read is a row the delete cannot cover, so stamping the rest and leaving that one behind
is how you get a duplicate — the very thing this module exists to stop. `day_stamp` therefore either
stamps EVERY row or stamps NONE and tells the caller to keep its period replace, which is a real
delete either way. Live consequence: `raw_payment_detail` and `raw_comp_report` carry their date on
every row and get the fix; `raw_dlar_rep` / `raw_dlar_store` are 85% blank on `as_of_date` today and
stay byte-identical until they are not.

PURE. stdlib only — no client, no FastAPI, no clock, no org, and no carrier, tenant or product name
(RULE TWO; the carrier-vocabulary guard reads this file). Proof + lock:
`backend/harness_feed_day_grain.py`.
"""
import calendar

# The period spelling every commcalc table stores and every reader's `_pvariants` expands from.
PERIOD_FORMAT = "%B %Y"

__all__ = ["period_of_day", "period_label", "month_spread", "day_values", "day_stamp",
           "DayStampResult", "OPEN", "CLOSED", "UNKNOWN", "period_month_year",
           "month_state", "archive_due", "month_days",
           "END_EXCLUSIVE", "END_INCLUSIVE", "END_BOUNDARY_DEFAULT", "request_window"]


def period_label(month, year):
    """PURE: ("October 2026") for (10, 2026); None when the pair is not a real month."""
    try:
        m, y = int(month), int(year)
    except (TypeError, ValueError):
        return None
    if not (1 <= m <= 12) or y < 1900:
        return None
    return f"{calendar.month_name[m]} {y}"


def period_of_day(value):
    """PURE: (period, month, year) for one date value, or None when no month can be read from it.

    Accepts the spellings the feeds actually arrive in — ISO `YYYY-MM-DD` (what every mapper now
    writes), the US `MM/DD/YYYY` the portal exports spell, and a `datetime`/`date` object. Anything
    else returns None, which is the caller's signal to keep its period replace rather than guess.
    """
    if value is None:
        return None
    # a date/datetime without importing datetime for an isinstance check on a hot path
    mo = getattr(value, "month", None)
    yr = getattr(value, "year", None)
    if mo is not None and yr is not None:
        label = period_label(mo, yr)
        return (label, int(mo), int(yr)) if label else None
    s = str(value).strip()
    if len(s) >= 10 and s[2] == "/" and s[5] == "/":          # MM/DD/YYYY
        try:
            mo, yr = int(s[0:2]), int(s[6:10])
        except ValueError:
            return None
    elif len(s) >= 7 and s[4] == "-":                          # YYYY-MM-DD / YYYY-MM
        try:
            yr, mo = int(s[0:4]), int(s[5:7])
        except ValueError:
            return None
    else:
        return None
    label = period_label(mo, yr)
    return (label, mo, yr) if label else None


def day_values(rows, column):
    """PURE: the distinct non-empty values of `column` across `rows`, sorted."""
    return sorted({str(r.get(column)).strip() for r in (rows or [])
                   if r is not None and r.get(column) not in (None, "")})


def month_spread(values):
    """PURE: [(period, count)] newest month first for a list of date values.

    Every month a file COVERS, not just the dominant one — the dominant-month check cannot see a
    rolling window, which is exactly how September's commission reached October.
    """
    counts = {}
    for v in values or []:
        got = period_of_day(v)
        if got:
            counts[(got[2], got[1])] = counts.get((got[2], got[1]), 0) + 1
    return [(period_label(m, y), n)
            for (y, m), n in sorted(counts.items(), reverse=True)]


class DayStampResult(dict):
    """The outcome of `day_stamp`, as a plain dict so a route can return it verbatim.

    Keys: `stamped` (bool — True only when EVERY row was stamped), `rows`, `unproven`,
    `months` ([(period, rows)] after stamping), `reason` (None when stamped).
    """

    @property
    def stamped(self):
        return bool(self.get("stamped"))


def day_stamp(rows, column):
    """PURE: stamp `period` / `period_month` / `period_year` on each row from its OWN `column` date.

    All or nothing. When any row's date cannot be read the rows are left EXACTLY as the caller mapped
    them and `stamped` is False with a `reason` — the caller then keeps its (org, period) replace,
    which is a real delete, rather than a per-day replace that could not cover the row it could not
    read. Mutates `rows` in place only on success, and returns the same objects either way.
    """
    rows = list(rows or [])
    if not rows:
        return DayStampResult(stamped=False, rows=0, unproven=0, months=[],
                              reason="no rows to stamp")
    if not column:
        return DayStampResult(stamped=False, rows=len(rows), unproven=len(rows), months=[],
                              reason="the feed declares no data-date column")
    resolved = []
    unproven = 0
    for r in rows:
        got = period_of_day((r or {}).get(column))
        if got is None:
            unproven += 1
        resolved.append(got)
    if unproven:
        return DayStampResult(
            stamped=False, rows=len(rows), unproven=unproven, months=[],
            reason=(f"{unproven:,} of {len(rows):,} row(s) carry no readable {column}, so a per-day "
                    f"replace could not cover them — filed under the selected period instead"))
    counts = {}
    for r, got in zip(rows, resolved):
        label, mo, yr = got
        r["period"] = label
        r["period_month"] = mo
        r["period_year"] = yr
        counts[(yr, mo)] = counts.get((yr, mo), 0) + 1
    months = [(period_label(m, y), n) for (y, m), n in sorted(counts.items(), reverse=True)]
    return DayStampResult(stamped=True, rows=len(rows), unproven=0, months=months, reason=None)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# IS THIS MONTH STILL OPEN — one home, dereferenced (owner report 2026-10-06)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
# Owner, on a "revenue/commission leak" alert for a single $35.33 sale: *"b2b is updated daily, that is
# how our sales mtd is reporting the daily numbers"*. He is right, and it makes the alert wrong rather
# than merely noisy. Measured live the same day on the house org: **October 2026 carried 3,001
# `sales_leak` flags at severity critical** — October being the month in progress, whose month-end
# archive cannot exist yet. September carried 11,233, one per transaction, because its archive was never
# built at all. Neither number is a count of leaks; both are a comparison run against a side that had
# not arrived.
#
# THE CLASS, not the instance (house rule: *a fix is a DESIGN fix or it is not a fix*). The general fact
# that was wrong is not "September's basis is missing"; it is **"the month-end archive was treated as the
# authority for every month"**. It is the authority for a CLOSED month and does not exist for an open
# one — so "is this month still open" is a precondition of every feed-vs-archive comparison, and a
# reconciliation must report a difference only when BOTH sides have actually arrived.
#
# THAT QUESTION ALREADY HAD THREE ANSWERS and no home (§19.18's shape, now for the fifth time):
#   • `router._is_open_month(period)`          — `parse_period` + `date.today()`
#   • `router.sales_derive_gap`                — `_canon_period(p) != _canon_period(_ftp_current_period())`
#   • `commcalc/sales_recon.py`                — never asked, which is the defect itself
# `sales_derive.current_period_label` / `prior_period_label` carry the month-boundary GRACE window and
# are a different question (how long a just-closed month keeps finalizing), so they are left alone and
# now derive their spelling from `period_label` here.
#
# PURE, like everything else in this module: no client, no FastAPI, no org, no carrier/tenant/product
# name (RULE TWO). `today` is injected so the proof harness can stand on a fixed date.
# Proof + lock: `backend/harness_sales_recon_basis.py`.

OPEN = "open"          # the month in progress — the daily feed IS the record; no archive is due
CLOSED = "closed"      # the month has ended — its month-end archive is the authority and IS due
UNKNOWN = "unknown"    # not a month-period at all; a caller must not infer either state from it


def period_month_year(period):
    """PURE: (month, year) from EITHER spelling a commcalc table stores — 'October 2026', '2026-10',
    '2026-10-06' — else (None, None).

    The month-name branch is case- and abbreviation-tolerant ('oct 2026', 'Sept 2026') because the
    period-spelling bug class in this module is always a reader that understood one form only: the
    pre-existing `router._is_open_month` silently mapped '2026-07' to January, so July read as a
    CLOSED month and took the wrong source.
    """
    s = str(period or "").strip()
    if not s:
        return (None, None)
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-" and s[5:7].isdigit():
        try:
            yr, mo = int(s[:4]), int(s[5:7])
        except ValueError:
            return (None, None)
        return (mo, yr) if 1 <= mo <= 12 else (None, None)
    parts = s.replace(",", " ").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return (None, None)
    name = parts[0].strip(".").lower()
    for i in range(1, 13):
        full = calendar.month_name[i].lower()
        if name == full or (len(name) >= 3 and full.startswith(name)):
            return (i, int(parts[1]))
    return (None, None)


def month_state(period, today=None):
    """PURE: OPEN / CLOSED / UNKNOWN for a month-period, against `today` (a date; defaults to the real
    one). A FUTURE month is OPEN — nothing about it has finished, so no archive is due for it either.

    This is the ONE home for the question. Never re-derive it from `date.today().month`; read it.
    """
    mo, yr = period_month_year(period)
    if not mo or not yr:
        return UNKNOWN
    if today is None:
        import datetime as _dt
        today = _dt.date.today()
    return CLOSED if (yr, mo) < (today.year, today.month) else OPEN


def archive_due(period, today=None):
    """PURE: True when this period's month-end archive is DUE — i.e. the month has closed.

    A caller comparing a live feed against a month-end archive must gate on this: a difference found
    on a month whose archive is not due yet is a statement about the calendar, never about the money.
    UNKNOWN is NOT due — an unparseable period may not be asserted either way.
    """
    return month_state(period, today) == CLOSED


# ── WHAT WINDOW DO I ASK A SOURCE FOR, SO THE LAST DAY ACTUALLY ARRIVES? ─────────────────────────
# THE CLASS (owner 2026-10-08: *"why is the last day of the month missing and we need to build a fix
# for it"*). A window's END BOUNDARY is an assumption about SOMEBODY ELSE'S system, and a feed pulled
# over a window can be short its final day without anything saying so. The instance was the carrier's
# compensation statement: `commcalc.raw_comp_report` stops exactly one day short of every month, with
# no exception, re-measured live read-only on the house org 2026-10-08 —
#
#   March 03-01…03-30 (03-31 missing, $15,361.59 / 436 rows present in the payment detail) · April
#   04-01…04-29 (04-30, $23,050.60 / 2,746) · May 05-01…05-30 (05-31, $16,101.89 / 627) · June
#   06-01…06-29 (06-30, $12,835.05 / 470) · July 07-01…07-30 (07-31, $17,096.33 / 565) · August
#   08-01…08-30 (08-31, $16,628.09 / 702) · September 09-01…09-29 (09-30, $10,875.67 / 495) —
#   $111,949.22 in all, and October, PULLED on 2026-10-03, stops at 10-02.
#
# Read the shape precisely: 31-day months stop at the 30th, 30-day months at the 29th, and the month
# pulled mid-flight stops the day before the pull. That is not a late posting (August and September
# were pulled five weeks and three days after those days posted and still stopped short), not our
# trailing window (`_recent_days(n)` ends TODAY, inclusive, which cannot produce it), and not our
# sweep at all (the comp leg has never once succeeded — every stored row arrived through the manual
# upload path, a human exporting a Start/End range). It is an END DATE BEING TREATED AS EXCLUSIVE by
# the source, every single time, including when the window end was the pull date rather than a month
# end. For the days both feeds DO cover they agree to the penny: August 1–30 is $523,093.67 on both
# sides, and October's two shared days are $15,460.69 = $15,460.69. Same money; a day in one and not
# the other is a feed that arrived incomplete.
#
# WE CANNOT CHANGE SOMEBODY ELSE'S PORTAL. What we can do is ask for a window that cannot lose the day
# we intend to cover, in ONE place, and say WHY — which is this function. A caller states the days it
# INTENDS (`covers_through`); this returns the boundary to put in the widget. No caller adds a day of
# its own: a `+1` scattered through callers is how the assumption gets made twice and corrected once.
#
# RULE TWO — the boundary is CONFIG, not a branch. `end_boundary` comes from the per-report row
# (`commcalc.report_definitions`, like `arrears_days` / `refresh_days` / `sweep_hour` already do), with
# the HOUSE DEFAULT below, so a tenant whose source honours an inclusive end overrides a row instead of
# anybody editing code. The default is `exclusive` because that is what every month of real data shows.
END_EXCLUSIVE = "exclusive"   # the source returns days STRICTLY BEFORE the end date it was given
END_INCLUSIVE = "inclusive"   # the source returns the end date itself
END_BOUNDARY_DEFAULT = END_EXCLUSIVE


def request_window(begin, covers_through, end_boundary=None):
    """PURE: the window to ASK a source for, so that `begin … covers_through` really arrives.

    Returns {begin, end, covers_through, end_boundary, widened}: `end` is what the caller puts in the
    source's End Date, `covers_through` is the last day it INTENDS to receive. Under an end-exclusive
    source `end` is one day later and `widened` is True; under an inclusive one they are equal and
    nothing moves.

    An unreadable end date is returned untouched with `widened` False — a window we cannot parse is a
    window we must not silently move; guessing here would hide the bigger problem.

    NO CLOCK, deliberately, and not even a `datetime` import: this is date ARITHMETIC on the day the
    caller named. The module's one clock stays confined to `month_state`, where it is injected
    (harness_feed_day_grain.py §H2a), and the next-day step below uses the `calendar` this module
    already reads rather than opening a second door to "today".
    """
    boundary = str(end_boundary or "").strip().lower() or END_BOUNDARY_DEFAULT
    if boundary not in (END_EXCLUSIVE, END_INCLUSIVE):
        boundary = END_BOUNDARY_DEFAULT
    out = {"begin": begin, "end": covers_through, "covers_through": covers_through,
           "end_boundary": boundary, "widened": False}
    if boundary != END_EXCLUSIVE:
        return out
    nxt = _next_day(covers_through)
    if nxt is None:
        return out
    out["end"] = nxt
    out["widened"] = True
    return out


def _next_day(iso):
    """PURE: the ISO day after `iso`, or None when it cannot be read. Calendar arithmetic only."""
    s = str(iso or "")[:10]
    parts = s.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    y, m, d = (int(p) for p in parts)
    if not (1 <= m <= 12) or not (1 <= d <= calendar.monthrange(y, m)[1]):
        return None
    if d < calendar.monthrange(y, m)[1]:
        return f"{y:04d}-{m:02d}-{d + 1:02d}"
    if m < 12:
        return f"{y:04d}-{m + 1:02d}-01"
    return f"{y + 1:04d}-01-01"


def month_days(period):
    """PURE: every calendar day of a month-period as ISO strings, oldest first; [] when the period is
    not a month.

    THE set a CLOSED month's completeness is judged against: a month whose statement does not cover
    every one of these days is not a finished month, however many days it does carry.
    """
    mo, yr = period_month_year(period)
    if not mo or not yr:
        return []
    n = calendar.monthrange(yr, mo)[1]
    return [f"{yr:04d}-{mo:02d}-{d:02d}" for d in range(1, n + 1)]
