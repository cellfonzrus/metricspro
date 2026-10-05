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
           "DayStampResult"]


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
