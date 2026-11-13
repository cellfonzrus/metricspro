"""WHAT IS THIS MONTH TRENDING TO — one home, dereferenced by every surface that asks.

THE DUPLICATE CHECK THAT PRODUCED THIS FILE. The arithmetic was already here, written inline TWICE
in `router.py`'s Executive MTD report — `trend_factor = (dim / elapsed)`, once for the whole month
and once for a date range inside it — and its answer is already on screen as "Trending Acc. $" on
the Daily Targets page and "Trending Acc. Sales" on Executive MTD. A third copy for the action plan
would have been a third answer to one question, and the three would have parted company the first
time anybody changed what "elapsed" means. So the fact was FACTORED and both older callers rewired
in the same change (§19.18: writing the registry without wiring the callers to it is not a fix).

THE RULE, which those callers established and this keeps unchanged:

    projected month-end = (month-to-date ÷ days COMPLETED) × days the month holds

"Completed" means strictly before today, because today is still in progress. Counting today would
divide a part-day's sales by a whole day and read every store as collapsing each morning. For a
month that has finished there is nothing to project: elapsed is the whole month and the factor is 1.

THE DIVISION ITSELF is not written here either — it is `demand_projection.velocity`, the house's one
answer to "how many of something per window-unit" (§COMMCALC-49.2). This module's job is only to say
what the window IS for a calendar month, and to say plainly when there is no window worth dividing
by. PURE: no client, no clock of its own, no config; `today` is always injected.

Also here, for the same one-home reason, is what makes a store an OUTLIER (`outliers`) — because
"highlight the outliers in a different colour" has to mean the same thing on every surface that
highlights, and a rule written per screen is a rule nobody can argue with.

Index: §COMMCALC-72.
"""
from __future__ import annotations

import datetime as _dt

# THE DIVISION HAS ONE HOME (§COMMCALC-49.2). This module imports it rather than writing `a / b`:
# the window and the horizon are in the caller's unit there, and days are this caller's unit.
from app.modules.commcalc import demand_projection as _dp
# AND SO DOES THE CALENDAR. Three facts this module needs were already answered in `feed_period`,
# and the first draft of this file re-derived all three before the duplicate check caught it:
#   * how to read a period's month and year, in EITHER spelling a commcalc table stores
#   * how many days that month holds
#   * whether the month is still in progress
# `router._is_open_month` is the cautionary tale: its own copy mapped '2026-07' to January, so July
# read as CLOSED and the report took the wrong source. Read these; never re-derive them.
from app.modules.commcalc import feed_period as _fp

# How far from the middle of the pack a figure has to sit before it is called out, as a multiple of
# the pack's own spread. A HOUSE POLICY (a number, not a tenant's vocabulary), so a default is right
# here — see CLAUDE.md: house defaults remain right for a policy and wrong for a tenant's own words.
OUTLIER_SPREAD_MULTIPLE = 2.5
# Below this many measured figures there is no pack to be outside of, and the arithmetic would be
# calling the lower of two stores an outlier.
OUTLIER_MIN_COUNT = 4


def _month_year(period) -> tuple[int, int]:
    """(month, year), dereferenced from `feed_period.period_month_year`. (0, 0) when unparseable."""
    mo, yr = _fp.period_month_year(period)
    return (int(mo), int(yr)) if (mo and yr) else (0, 0)


def days_in_month(period) -> int:
    """How many days this month holds — the length of `feed_period.month_days`, which is the set a
    month's completeness is already judged against. 30 for a period nobody can parse, the same
    fallback the inline callers used, so no figure moves."""
    return len(_fp.month_days(period)) or 30


def is_open_month(period, today: _dt.date | None = None) -> bool:
    """Is this month still in progress? `feed_period.month_state` is the one home for the question
    and the only reader of it here. A FUTURE month is open there — nothing about it has finished —
    and that is the right answer for a projection too: there is no run rate to carry forward."""
    return _fp.month_state(period, today=today) == _fp.OPEN


def month_window(period, today: _dt.date | None = None,
                 span_from: _dt.date | None = None,
                 span_to: _dt.date | None = None) -> dict:
    """The window a month-to-date figure was summed over, and the horizon to project it across.

    Returns `{days_in_month, elapsed, complete, open_month, no_overlap, basis}`.

    * `elapsed` is the number of COMPLETED days, clamped to at least 1 — the clamp the inline
      callers already had, kept so their figures do not move.
    * `complete` is the honest flag beside it: False says the clamp was used and there is no real
      window, so a surface can refuse to project instead of printing a run rate off a part-day.
    * `basis` is 'mtd' (an open month so far), 'month' (a finished month), 'range' (a span inside
      the month) or 'unknown' (an unparseable period).
    """
    today = today or _dt.date.today()
    dim = days_in_month(period)
    mo, yr = _month_year(period)
    parsed = bool(1 <= mo <= 12 and yr)
    open_m = is_open_month(period, today)

    if span_from is not None or span_to is not None:
        m_start = _dt.date(yr, mo, 1) if parsed else None
        m_end = _dt.date(yr, mo, dim) if parsed else None
        eff_from = span_from or m_start
        eff_to = span_to or m_end
        if eff_from is None or eff_to is None:
            return {'days_in_month': dim, 'elapsed': 1, 'complete': False, 'open_month': open_m,
                    'no_overlap': False, 'basis': 'unknown'}
        eff_from, eff_to = max(eff_from, m_start), min(eff_to, m_end)
        # A backwards or fully-outside window selects nothing, and says so rather than falling back
        # to the whole month — a filter that silently ignores itself is worse than an empty answer.
        no_overlap = eff_from > eff_to
        last_complete = min(eff_to, today - _dt.timedelta(days=1)) if open_m else eff_to
        raw = 0 if no_overlap else max(0, (last_complete - eff_from).days + 1)
        return {'days_in_month': dim, 'elapsed': max(1, raw), 'complete': raw > 0,
                'open_month': open_m, 'no_overlap': no_overlap, 'basis': 'range'}

    if not parsed:
        return {'days_in_month': dim, 'elapsed': 1, 'complete': False, 'open_month': False,
                'no_overlap': False, 'basis': 'unknown'}
    if open_m and (yr, mo) != (today.year, today.month):
        # A FUTURE month. `feed_period` calls it open, correctly — nothing about it has finished —
        # and that is exactly why there is no run rate to carry across it. Without this branch
        # `today.day - 1` would divide next month's target by THIS month's elapsed days.
        return {'days_in_month': dim, 'elapsed': 1, 'complete': False, 'open_month': True,
                'no_overlap': False, 'basis': 'future'}
    if not open_m:
        return {'days_in_month': dim, 'elapsed': dim, 'complete': True, 'open_month': False,
                'no_overlap': False, 'basis': 'month'}
    raw = today.day - 1          # strictly before today: today is in progress
    return {'days_in_month': dim, 'elapsed': max(1, raw), 'complete': raw > 0,
            'open_month': True, 'no_overlap': False, 'basis': 'mtd'}


def factor(window: dict) -> float:
    """What a month-to-date figure is multiplied by to reach month end. 1.0 for a finished month."""
    w = window or {}
    # `dim / elapsed` IS "how many per window-unit" — the same question `demand_projection.velocity`
    # answers, so it is asked there rather than divided again here. That leaves exactly ONE division
    # behind every projected figure in the codebase, which is the whole point of this module.
    v = _dp.velocity(w.get('days_in_month'), w.get('elapsed'))
    return 1.0 if v is None else v


def project(mtd, window: dict, unit: str = 'dollars'):
    """`mtd` carried across the rest of the month at the rate it was earned. PURE.

    **None when the window is not complete** — on the first of the month there is no finished day to
    divide by, and a run rate off a part-day is not a trend. Zero achieved over a real window is 0,
    not None: nothing sold IS a measurement, and the difference matters because a surface prints the
    first as "not measured" and the second as a store heading for nothing.
    """
    w = window or {}
    if not w.get('complete'):
        return None
    # The division is `demand_projection.velocity` (units per day), not a second `/` written here.
    v = _dp.velocity(mtd, w.get('elapsed'))
    if v is None:
        return None
    out = v * float(int(w.get('days_in_month') or 0))
    return int(round(out)) if str(unit) == 'count' else round(out, 2)


def verdict(projected, target) -> dict:
    """Is that projection going to reach the target? `{kind, gap}` and nothing else.

    `kind` is one of:
      * `no_target`      — nothing to be on track FOR. Never 'on_track': "target met" printed for a
                           scope with no target is the owner-reported defect of 2026-09-10.
      * `not_measured`   — nothing could be projected. NOT 'short': printing a shortfall nobody
                           measured invents one.
      * `on_track` / `short` — the projection reaches the target, or falls `gap` under it.

    The words and the colour are the surface's; this is the fact, which is why the mailed copy and
    the screen can agree without sharing a stylesheet.
    """
    try:
        tgt = float(target or 0)
    except (TypeError, ValueError):
        tgt = 0.0
    if tgt <= 0:
        return {'kind': 'no_target', 'gap': None}
    if projected is None:
        return {'kind': 'not_measured', 'gap': None}
    try:
        proj = float(projected)
    except (TypeError, ValueError):
        return {'kind': 'not_measured', 'gap': None}
    if proj >= tgt:
        return {'kind': 'on_track', 'gap': 0}
    return {'kind': 'short', 'gap': tgt - proj}


def trend_of(mtd, target, period, today: _dt.date | None = None, unit: str = 'dollars',
             span_from: _dt.date | None = None, span_to: _dt.date | None = None) -> dict:
    """The whole answer a surface asks for: the projection, the verdict, and every term behind them.

    Every term is returned beside the answer for the same reason `demand_projection.projection`
    returns its own — a figure a manager is going to act on has to be arguable with.
    """
    w = month_window(period, today=today, span_from=span_from, span_to=span_to)
    projected = project(mtd, w, unit=unit)
    v = verdict(projected, target)
    return {'projected': projected, 'kind': v['kind'], 'gap': v['gap'],
            'mtd': mtd, 'target': target, 'elapsed': w['elapsed'],
            'days_in_month': w['days_in_month'], 'complete': w['complete'], 'basis': w['basis']}


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def outliers(values, multiple: float = OUTLIER_SPREAD_MULTIPLE,
             min_count: int = OUTLIER_MIN_COUNT) -> dict:
    """Which figures sit outside the pack — ONE rule, so "highlighted" means one thing everywhere.

    Median-based, deliberately: a mean is dragged by the very figure being looked for, so the one
    store having an extraordinary month would raise the bar it is measured against and hide itself.
    The spread is the median absolute deviation from the median, and a figure is an outlier when it
    is more than `multiple` spreads away.

    * A figure nobody measured (`None`) is **absent from the distribution**, never at the bottom of
      it — a store whose feed landed nothing is not a store that sold nothing.
    * Fewer than `min_count` measured figures returns nobody (`basis: 'too_few'`): two stores are
      not a distribution, and calling the lower one an outlier would be an accusation dressed up as
      arithmetic.
    * An identical pack has zero spread, which would make every unequal figure an outlier. The rule
      falls back to the gap from the median itself rather than multiplying by nothing.

    Returns `{median, spread, high, low, counted, basis}`; `high`/`low` are the OUTLYING VALUES, so
    a caller keyed by store or rep matches its own rows against them.
    """
    vals: list[float] = []
    for v in (values or []):
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(vals) < int(min_count):
        return {'median': _median(vals) if vals else None, 'spread': None,
                'high': [], 'low': [], 'counted': len(vals), 'basis': 'too_few'}
    med = _median(vals)
    spread = _median([abs(v - med) for v in vals])
    if spread <= 0:
        # Zero spread: the pack agrees exactly. Anything unequal to it is the stray.
        return {'median': med, 'spread': 0.0,
                'high': sorted({v for v in vals if v > med}),
                'low': sorted({v for v in vals if v < med}),
                'counted': len(vals), 'basis': 'equal_pack'}
    limit = float(multiple) * spread
    return {'median': med, 'spread': spread,
            'high': sorted({v for v in vals if v - med > limit}),
            'low': sorted({v for v in vals if med - v > limit}),
            'counted': len(vals), 'basis': 'median_spread'}
