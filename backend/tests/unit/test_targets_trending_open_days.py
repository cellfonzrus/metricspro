"""The whole-store trending column reads the per-store open-days window (§COMMCALC-72).

`_targets_trending_by_code` folds Executive MTD's by-location rows onto canonical store codes. Exec
MTD trends the whole org on ONE calendar factor, which is right for the org and wrong for a store
that opened on the 12th. These tests pin that a store with an open-days window is re-projected off
its own trading days, that a store without one keeps Exec MTD's figure byte-for-byte, and that the
restatement is announced in the meta rather than being a silent change of basis.
"""
from datetime import date

import pytest

from app.modules.commcalc import router as R

_NOV = '2026-11'
_TODAY = date(2026, 11, 16)


def _exec_payload():
    # Exec MTD's house factor on 16 Nov is 30 / 15 = 2.0; both stores are trended with it.
    return {
        'trending': {'elapsed_days': 15, 'days_in_month': 30, 'factor': 2.0, 'basis': 'mtd'},
        'by_location': {'rows': [
            {'store': 'Graham Ave', 'total_activation': 20, 'acc_sales': 3000.0,
             'setup_fee': 200.0, 'acc_plus_setup': 3200.0,
             'trending_box': 40, 'trending_acc_sales': 6000.0, 'trending_acc_plus_setup': 6400.0},
            {'store': 'Wyckoff', 'total_activation': 9, 'acc_sales': 900.0,
             'setup_fee': 100.0, 'acc_plus_setup': 1000.0,
             'trending_box': 18, 'trending_acc_sales': 1800.0, 'trending_acc_plus_setup': 2000.0},
        ]},
    }


@pytest.fixture
def exec_mtd(monkeypatch):
    monkeypatch.setattr(R, '_exec_mtd', lambda *a, **k: _exec_payload())
    monkeypatch.setattr(R, '_store_code_resolver',
                        lambda *a, **k: (lambda s: {'Graham Ave': 'GRA', 'Wyckoff': 'WYC'}.get(s, s)))


def test_without_open_days_every_row_is_exec_mtds_own_answer(exec_mtd):
    by_code, meta = R._targets_trending_by_code(None, 'org', _NOV, today=_TODAY)
    assert by_code['GRA']['trending_box'] == 40
    assert by_code['WYC']['trending_acc_sales'] == 1800.0
    assert 'open_days_restated' not in meta


def test_a_store_open_three_days_is_projected_off_three_days(exec_mtd):
    by_code, meta = R._targets_trending_by_code(
        None, 'org', _NOV, today=_TODAY, open_days_by_code={'GRA': (3, 18)})
    g = by_code['GRA']
    assert g['trending_box'] == 120              # 20 / 3 x 18, not 20 x 2
    assert g['trending_acc_sales'] == 18000.0
    assert g['trending_acc_plus_setup'] == 19200.0
    assert g['trend_basis'] == 'open_days'
    assert (g['open_days_elapsed'], g['open_days_total']) == (3, 18)
    assert meta['open_days_restated'] == ['GRA']


def test_a_store_with_no_window_is_untouched_beside_one_that_has_it(exec_mtd):
    by_code, _ = R._targets_trending_by_code(
        None, 'org', _NOV, today=_TODAY, open_days_by_code={'GRA': (3, 18)})
    w = by_code['WYC']
    assert w['trending_box'] == 18 and w['trending_acc_sales'] == 1800.0
    assert 'trend_basis' not in w


def test_a_store_open_every_day_does_not_move(exec_mtd):
    by_code, _ = R._targets_trending_by_code(
        None, 'org', _NOV, today=_TODAY, open_days_by_code={'WYC': (13, 26)})
    assert by_code['WYC']['trending_box'] == 18          # 13 of 26 is the same factor as 15 of 30


def test_an_unmeasurable_window_leaves_the_row_alone(exec_mtd):
    by_code, meta = R._targets_trending_by_code(
        None, 'org', _NOV, today=_TODAY, open_days_by_code={'GRA': (0, 0), 'WYC': None})
    assert by_code['GRA']['trending_box'] == 40
    assert 'open_days_restated' not in meta


def test_a_code_that_is_not_selling_is_ignored(exec_mtd):
    by_code, _ = R._targets_trending_by_code(
        None, 'org', _NOV, today=_TODAY, open_days_by_code={'NOSUCH': (2, 10)})
    assert set(by_code) == {'GRA', 'WYC'}


def test_a_trending_failure_still_does_not_raise(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError('exec mtd is down')
    monkeypatch.setattr(R, '_exec_mtd', _boom)
    assert R._targets_trending_by_code(None, 'org', _NOV, today=_TODAY,
                                       open_days_by_code={'GRA': (3, 18)}) == ({}, {})
