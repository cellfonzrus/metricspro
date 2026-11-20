"""WHICH STORE-DAYS OF A PER-STORE FEED DO WE HAVE, AND WHICH ARE STILL TO FETCH.

Written RED before the module existed. The owner's ask is a person downloading one file per store per
day for 29 stores, which is only survivable if the screen says what is still missing — and says
"I could not look" differently from "there is nothing there", because those send the operator to do
opposite things.
"""
import pytest

from app.modules.commcalc import feed_day_coverage as cov

pytestmark = pytest.mark.unit

STORES = ("B-1115", "B-1750")
DAYS = ("2026-11-18", "2026-11-19")


def test_days_between_is_inclusive_and_ordered():
    assert cov.days_in_range("2026-11-18", "2026-11-20") == ("2026-11-18", "2026-11-19", "2026-11-20")


def test_days_between_refuses_a_backwards_or_unreadable_range():
    assert cov.days_in_range("2026-11-20", "2026-11-18") == ()
    assert cov.days_in_range("", "2026-11-20") == ()
    assert cov.days_in_range("last tuesday", "2026-11-20") == ()


def test_days_between_is_bounded_so_a_wide_range_cannot_draw_a_year_of_columns():
    wide = cov.days_in_range("2026-01-01", "2026-12-31")
    assert len(wide) == cov.MAX_DAYS
    assert wide[0] == "2026-01-01"


def test_a_landed_store_day_reads_as_have_and_carries_its_figures():
    landed = {("B-1115", "2026-11-18"): {"payment": 120.5, "fee": 3.0, "lines": 7}}
    g = cov.grid(STORES, DAYS, landed)
    cell = [c for c in g["stores"][0]["days"] if c["day"] == "2026-11-18"][0]
    assert cell["state"] == "have" and cell["lines"] == 7 and cell["payment"] == 120.5


def test_a_store_day_with_no_rows_reads_as_missing_not_as_zero_money():
    g = cov.grid(STORES, DAYS, {})
    cell = g["stores"][0]["days"][0]
    assert cell["state"] == "missing"
    assert "payment" not in cell and "lines" not in cell


def test_an_unreadable_feed_is_UNKNOWN_everywhere_and_never_reads_as_missing():
    """The defect this guards: an unreadable feed rendered as "every store-day missing" would send a
    person to download 29 files a day that are already landed."""
    g = cov.grid(STORES, DAYS, None)
    assert g["measured"] is False
    assert all(c["state"] == "unknown" for s in g["stores"] for c in s["days"])
    assert g["missing"] == 0 and g["have"] == 0
    assert "could not" in g["note"].lower()


def test_a_measured_grid_counts_what_is_had_and_what_is_owed():
    landed = {("B-1115", "2026-11-18"): {"payment": 1.0, "fee": 0.0, "lines": 1},
              ("B-1750", "2026-11-19"): {"payment": 2.0, "fee": 0.0, "lines": 2}}
    g = cov.grid(STORES, DAYS, landed)
    assert g["measured"] is True and g["have"] == 2 and g["missing"] == 2
    assert g["note"] == ""
    per = {s["store"]: (s["have"], s["missing"]) for s in g["stores"]}
    assert per == {"B-1115": (1, 1), "B-1750": (1, 1)}


def test_a_day_a_store_landed_as_empty_is_still_missing_a_file():
    """Zero lines is not an answer here: the operator has not downloaded anything for that day."""
    landed = {("B-1115", "2026-11-18"): {"payment": 0.0, "fee": 0.0, "lines": 0}}
    g = cov.grid(STORES, DAYS, landed)
    assert g["stores"][0]["days"][0]["state"] == "missing"


def test_no_declared_store_is_an_empty_grid_that_says_so_rather_than_a_false_zero():
    g = cov.grid((), DAYS, {})
    assert g["stores"] == [] and g["have"] == 0 and g["missing"] == 0
    assert "declared" in g["note"].lower()


def test_landed_rows_for_a_store_nobody_declared_are_reported_rather_than_dropped():
    """A file uploaded under a store code that is not in the picker is a real condition an operator
    must see — silently ignoring it is how a mis-keyed upload hides."""
    landed = {("B-9999", "2026-11-18"): {"payment": 5.0, "fee": 0.0, "lines": 3}}
    g = cov.grid(STORES, DAYS, landed)
    assert g["undeclared_stores"] == ["B-9999"]


def test_one_tenants_landed_days_can_never_fill_another_tenants_grid():
    """The grid is built from the stores it is GIVEN. A caller that passes one org's declared stores
    cannot be handed another org's coverage, because a store it did not ask for is never a cell."""
    landed = {("OTHER-1", "2026-11-18"): {"payment": 9.0, "fee": 0.0, "lines": 9}}
    g = cov.grid(STORES, DAYS, landed)
    assert [s["store"] for s in g["stores"]] == list(STORES)
    assert g["have"] == 0


def test_the_default_window_is_a_readable_fortnight_ending_today():
    """A screen opened with no range named shows a fortnight, not nothing and not a year."""
    import datetime
    a, b = cov.default_range(datetime.date(2026, 11, 20))
    assert (a, b) == ("2026-11-07", "2026-11-20")
    assert len(cov.days_in_range(a, b)) == cov.DEFAULT_DAYS


def test_the_default_window_is_itself_a_valid_range():
    a, b = cov.default_range()
    assert cov.days_in_range(a, b)[-1] == b
