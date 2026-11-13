"""WHAT IS THIS MONTH TRENDING TO — the one home, proved before it had callers.

The arithmetic was already in the codebase TWICE, inline in `router.py`'s Executive MTD report
(`trend_factor = dim / elapsed`, once for the whole month and once for a date range inside it). The
Daily Targets screen already prints its answer as "Trending Acc. $". So this module is a FACTORING,
not a new derivation, and the first thing proved here is that it reproduces that arithmetic exactly
— a projection that disagreed with the one already on screen would be the two-numbers-hoping-to-agree
defect the house forbids.

The rule those callers established and this keeps: a month-to-date figure is divided by the days that
have COMPLETED (strictly before today, because today is still in progress) and multiplied by the days
the month holds. A morning would otherwise read every store as collapsing.
"""
import datetime as dt

import pytest

from app.modules.commcalc import month_trend

pytestmark = pytest.mark.unit


# ── THE WINDOW ────────────────────────────────────────────────────────────────────────────────────

def test_an_open_month_divides_by_the_days_that_have_completed():
    # 12 November: the 1st-11th are complete, the 12th is in progress.
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 12))
    assert w['days_in_month'] == 30
    assert w['elapsed'] == 11
    assert w['basis'] == 'mtd'
    assert w['open_month'] is True


def test_the_first_of_an_open_month_has_no_completed_day_and_says_so():
    # The existing callers clamp to 1 rather than divide by zero. The clamp is kept so no figure
    # moves, and `complete` reports that the window is not really measured — a surface that wants to
    # refuse to project has to be able to tell.
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 1))
    assert w['elapsed'] == 1
    assert w['complete'] is False
    assert w['basis'] == 'mtd'


def test_a_finished_month_is_not_projected_at_all():
    w = month_trend.month_window('2026-09', today=dt.date(2026, 11, 12))
    assert w['days_in_month'] == 30
    assert w['elapsed'] == 30
    assert w['basis'] == 'month'
    assert w['open_month'] is False
    assert month_trend.factor(w) == 1.0


def test_february_in_a_leap_year_is_the_calendar_s_answer_not_a_constant():
    assert month_trend.month_window('2028-02', today=dt.date(2028, 3, 1))['days_in_month'] == 29
    assert month_trend.month_window('2026-02', today=dt.date(2026, 3, 1))['days_in_month'] == 28


def test_a_malformed_period_does_not_raise():
    w = month_trend.month_window('not-a-month', today=dt.date(2026, 11, 12))
    assert w['days_in_month'] == 30
    assert w['basis'] == 'unknown'


# ── THE FACTOR IS THE ONE THE EXECUTIVE MTD REPORT ALREADY USED ───────────────────────────────────

@pytest.mark.parametrize('day,expected_elapsed', [(2, 1), (12, 11), (30, 29)])
def test_the_factor_reproduces_the_inline_arithmetic_exactly(day, expected_elapsed):
    today = dt.date(2026, 11, day)
    dim = 30
    elapsed = max(1, today.day - 1)          # the literal that was inline in router.py
    assert elapsed == expected_elapsed
    w = month_trend.month_window('2026-11', today=today)
    assert w['elapsed'] == elapsed
    assert month_trend.factor(w) == pytest.approx(dim / elapsed)


def test_a_range_inside_the_month_counts_its_own_complete_days():
    # The second inline site: an [from, to] window inside the month. "Complete" still means strictly
    # before today for an open month.
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 12),
                                 span_from=dt.date(2026, 11, 5), span_to=dt.date(2026, 11, 30))
    assert w['basis'] == 'range'
    assert w['elapsed'] == 7          # the 5th through the 11th
    assert w['days_in_month'] == 30


def test_a_backwards_range_selects_nothing_and_does_not_divide_by_zero():
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 12),
                                 span_from=dt.date(2026, 11, 20), span_to=dt.date(2026, 11, 5))
    assert w['no_overlap'] is True
    assert w['elapsed'] == 1
    assert w['complete'] is False


# ── THE PROJECTION DEREFERENCES THE VELOCITY HOME ─────────────────────────────────────────────────

def test_the_projection_is_the_run_rate_over_the_whole_month():
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 12))   # 11 of 30 days
    # $24,820 in 11 days is $2,256.36/day, which over 30 days is $67,690.91.
    assert month_trend.project(24820, w) == pytest.approx(67690.91, abs=0.01)


def test_a_count_projects_to_a_whole_number_and_money_to_cents():
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 12))
    assert month_trend.project(100, w, unit='count') == 273
    assert isinstance(month_trend.project(100, w, unit='count'), int)
    assert month_trend.project(100, w, unit='dollars') == pytest.approx(272.73, abs=0.01)


def test_nothing_achieved_projects_to_nothing_rather_than_to_None():
    # Zero sold IS a measurement. Only an unmeasurable WINDOW gives None (below).
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 12))
    assert month_trend.project(0, w) == 0


def test_an_incomplete_window_refuses_to_project():
    # Day one. A run rate off a part-day is not a trend, and printing one would read as a collapse.
    w = month_trend.month_window('2026-11', today=dt.date(2026, 11, 1))
    assert month_trend.project(900, w) is None


def test_a_finished_month_projects_to_what_it_actually_did():
    w = month_trend.month_window('2026-09', today=dt.date(2026, 11, 12))
    assert month_trend.project(140000, w) == pytest.approx(140000.0)


# ── THE VERDICT ───────────────────────────────────────────────────────────────────────────────────

def test_trending_past_the_target_is_on_track():
    v = month_trend.verdict(170000, 163000)
    assert v['kind'] == 'on_track'
    assert v['gap'] == 0


def test_trending_short_names_the_gap():
    v = month_trend.verdict(67691, 163000)
    assert v['kind'] == 'short'
    assert v['gap'] == pytest.approx(95309)


def test_no_target_is_never_on_track():
    # §CORE "target met" printed for a scope with NO target is the 2026-09-10 defect. A trend must
    # not reintroduce it by comparing against zero.
    assert month_trend.verdict(5000, 0)['kind'] == 'no_target'
    assert month_trend.verdict(0, 0)['kind'] == 'no_target'


def test_an_unmeasurable_projection_is_not_measured_not_short():
    # A metric nothing could project is NOT behind. Printing "short" would invent a shortfall.
    v = month_trend.verdict(None, 163000)
    assert v['kind'] == 'not_measured'
    assert v['gap'] is None


def test_the_verdict_carries_no_colour_or_wording():
    # The words and the colour belong to the surface; the verdict is the fact. Keeps the mailed copy
    # and the screen reading the same kind without sharing a stylesheet.
    v = month_trend.verdict(1, 2)
    assert set(v) == {'kind', 'gap'}


# ── THE WHOLE ANSWER, AS A SURFACE ASKS FOR IT ────────────────────────────────────────────────────

def test_trend_of_answers_the_figure_and_the_verdict_together():
    t = month_trend.trend_of(24820, 163000, '2026-11', today=dt.date(2026, 11, 12), unit='dollars')
    assert t['projected'] == pytest.approx(67690.91, abs=0.01)
    assert t['kind'] == 'short'
    assert t['gap'] == pytest.approx(95309.09, abs=0.01)
    assert t['elapsed'] == 11
    assert t['days_in_month'] == 30
    assert t['basis'] == 'mtd'


def test_trend_of_on_the_first_of_the_month_is_not_measured():
    t = month_trend.trend_of(900, 163000, '2026-11', today=dt.date(2026, 11, 1), unit='dollars')
    assert t['projected'] is None
    assert t['kind'] == 'not_measured'


def test_trend_of_with_no_target_reports_the_projection_but_no_verdict():
    # A tenant that declared a category but set no monthly figure still gets to see its run rate.
    t = month_trend.trend_of(24820, 0, '2026-11', today=dt.date(2026, 11, 12), unit='dollars')
    assert t['projected'] == pytest.approx(67690.91, abs=0.01)
    assert t['kind'] == 'no_target'


# ── OUTLIERS ──────────────────────────────────────────────────────────────────────────────────────
# One stated rule, in one place, so "highlighted in a different colour" means the same thing on every
# surface that highlights. Median-based, because a mean is dragged by the very store being looked for.

def test_an_outlier_is_far_from_the_MEDIAN_by_the_spread_of_the_pack():
    vals = [50.0, 52.0, 48.0, 51.0, 49.0, 120.0, 5.0]
    out = month_trend.outliers(vals)
    assert out['median'] == 50.0
    assert out['high'] == [120.0]
    assert out['low'] == [5.0]


def test_a_tight_pack_has_no_outliers():
    assert month_trend.outliers([50.0, 51.0, 49.0, 50.5])['high'] == []
    assert month_trend.outliers([50.0, 51.0, 49.0, 50.5])['low'] == []


def test_too_few_stores_to_have_a_pack_names_nobody():
    # Two stores are not a distribution. Calling the lower one an outlier would be an accusation
    # dressed as arithmetic.
    for vals in ([], [10.0], [10.0, 90.0], [10.0, 50.0, 90.0]):
        o = month_trend.outliers(vals)
        assert o['high'] == [] and o['low'] == [], vals
        assert o['basis'] == 'too_few'


def test_an_identical_pack_with_one_stray_still_finds_it():
    # Zero spread would make any threshold zero, so every non-equal value becomes an "outlier". The
    # rule falls back to the gap from the median rather than dividing by a zero spread.
    o = month_trend.outliers([50.0, 50.0, 50.0, 50.0, 50.0, 90.0])
    assert o['high'] == [90.0]
    assert o['low'] == []


def test_nothing_measured_is_not_an_outlier():
    # A store nothing fed is absent from the distribution, not at the bottom of it.
    o = month_trend.outliers([50.0, 52.0, 48.0, None, 51.0, 49.0, None, 120.0])
    assert o['high'] == [120.0]
    assert o['counted'] == 6


def test_a_future_month_has_nothing_to_project_across():
    # `feed_period` calls a future month OPEN (nothing about it has finished), which is right — and
    # is exactly why there is no run rate for it. The naive reading would divide December's target
    # by November's elapsed days.
    w = month_trend.month_window('2026-12', today=dt.date(2026, 11, 12))
    assert w['basis'] == 'future'
    assert w['complete'] is False
    assert month_trend.project(900, w) is None


def test_the_month_spellings_a_commcalc_table_stores_all_parse():
    # `feed_period.period_month_year` is the one home and handles all of these; the cautionary tale
    # is `router._is_open_month`'s own copy, which read '2026-07' as January.
    for spelling in ('2026-11', 'November 2026', 'nov 2026', '2026-11-12'):
        assert month_trend.month_window(spelling, today=dt.date(2026, 11, 12))['days_in_month'] == 30, spelling
