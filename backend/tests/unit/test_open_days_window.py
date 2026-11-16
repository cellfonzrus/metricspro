"""A day a scope was not open is not a day it completed (§COMMCALC-72, owner 2026-11-16).

LuxeLink opened four New York stores on 12-13 November. On the 16th the Daily Targets page divided
three or four trading days of sales by the fifteen calendar days of the month, and projected each
store at about half its real rate. These tests pin BOTH halves of the fix: the window itself, in
`month_trend`, and the one answer to "which days was this store open", in `targets_engine`.
"""
from datetime import date

from app.modules.commcalc import month_trend as mt
from app.modules.commcalc import targets_engine as te


# ── the window itself ─────────────────────────────────────────────────────────────────────────────
def test_open_days_window_projects_off_open_days():
    w = mt.open_days_window(3, 18)
    assert w['elapsed'] == 3 and w['days_in_month'] == 18
    assert w['basis'] == 'open_days' and w['complete'] is True
    assert mt.factor(w) == 6.0


def test_a_scope_open_every_day_is_unchanged():
    # 15 of 30 and 13 of 26 are the same factor: a normal store does not move.
    assert mt.factor(mt.open_days_window(15, 30)) == mt.factor(mt.open_days_window(13, 26)) == 2.0


def test_nothing_to_measure_returns_none_rather_than_a_denominator():
    assert mt.open_days_window(0, 0) is None
    assert mt.open_days_window(5, None) is None
    assert mt.open_days_window(5, 'x') is None


def test_completed_is_clamped_into_the_month_it_belongs_to():
    w = mt.open_days_window(40, 18)
    assert w['elapsed'] == 18 and mt.factor(w) == 1.0
    assert mt.open_days_window(-3, 18)['elapsed'] == 1


def test_no_day_completed_is_not_a_trend():
    w = mt.open_days_window(0, 18)
    assert w['complete'] is False
    assert mt.project(1000, w) is None


# ── trend_of reads it ─────────────────────────────────────────────────────────────────────────────
_NOV = '2026-11'
_TODAY = date(2026, 11, 16)


def test_trend_of_on_open_days_beats_the_calendar_for_a_new_store():
    cal = mt.trend_of(9000, 12000, _NOV, today=_TODAY)
    opn = mt.trend_of(9000, 12000, _NOV, today=_TODAY, open_days=(3, 18))
    assert cal['basis'] == 'mtd' and opn['basis'] == 'open_days'
    assert opn['projected'] > cal['projected']
    assert opn['projected'] == 54000.0          # 9000 / 3 open days x 18 open days


def test_trend_of_without_open_days_is_byte_for_byte_the_old_answer():
    assert (mt.trend_of(9000, 12000, _NOV, today=_TODAY)
            == mt.trend_of(9000, 12000, _NOV, today=_TODAY, open_days=None))


def test_an_unmeasurable_window_falls_back_to_the_calendar():
    assert mt.trend_of(9000, 12000, _NOV, today=_TODAY, open_days=(0, 0))['basis'] == 'mtd'


def test_an_explicit_span_is_the_caller_naming_the_window_and_wins():
    t = mt.trend_of(9000, 12000, _NOV, today=_TODAY, open_days=(3, 18),
                    span_from=date(2026, 11, 10), span_to=date(2026, 11, 20))
    assert t['basis'] != 'open_days'


def test_a_closed_month_still_projects_nothing():
    t = mt.trend_of(9000, 12000, '2026-09', today=_TODAY, open_days=(4, 20))
    assert t['projected'] == 9000.0


# ── which days a store was open ───────────────────────────────────────────────────────────────────
def _shift(code, day, hours=8.0, **kw):
    row = {'store_code': code, 'shift_date': f'2026-11-{day:02d}', 'scheduled_hours': hours}
    row.update(kw)
    return row


def test_open_days_by_store_counts_only_the_days_it_was_scheduled():
    shifts = [_shift('NY9', 12), _shift('NY9', 13), _shift('NY9', 14), _shift('NY9', 17)]
    got = te.open_days_by_store(shifts, _TODAY)
    assert got['NY9'] == (3, 4)          # three completed before the 16th, four entered


def test_a_store_with_no_schedule_is_absent_not_zero():
    assert te.open_days_by_store([], _TODAY) == {}
    assert te.open_days_by_store([_shift('NY9', 12, hours=0)], _TODAY) == {}


def test_a_deleted_shift_is_not_an_open_day():
    shifts = [_shift('NY9', 12), _shift('NY9', 13, is_deleted=True)]
    assert te.open_days_by_store(shifts, _TODAY)['NY9'] == (1, 1)


def test_store_codes_are_folded_to_one_spelling():
    shifts = [_shift('ny9', 12), _shift(' NY9 ', 13)]
    got = te.open_days_by_store(shifts, _TODAY)
    assert set(got) == {'NY9'} and got['NY9'] == (2, 2)


def test_the_rest_of_the_month_is_projected_from_the_pattern_when_month_end_is_given():
    # Open every weekday so far; the horizon is the whole month, not just the entered days.
    shifts = [_shift('NY9', d) for d in range(2, 16)]
    done, total = te.open_days_by_store(shifts, _TODAY, date(2026, 11, 30))['NY9']
    assert done == 14 and total > 14


def test_two_stores_are_measured_independently():
    shifts = [_shift('NY9', 12), _shift('NY9', 13)] + [_shift('CHI1', d) for d in range(1, 16)]
    got = te.open_days_by_store(shifts, _TODAY)
    assert got['NY9'] == (2, 2) and got['CHI1'] == (15, 15)
