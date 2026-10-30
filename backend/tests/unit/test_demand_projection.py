"""Unit: `commcalc/demand_projection.py` — THE one home for "how many will sell next".

WHY THIS FILE EXISTS BEFORE THE MODULE DOES (TDD, project standard 2026-10-28). The arithmetic it
holds was written TWICE on this platform before today — `payables/router.forecast` and
`asset/purchase_orders.po_recommendations` each kept their own `units / window * horizon` and their
own `_is_device_line`, and the second one's docstring says out loud that it is a copy. Two paths
answering one question is the duplicate defect CLAUDE.md forbids, so the extraction has to be
provably BYTE-IDENTICAL to what both callers did, or rewiring them would move numbers nobody asked
to move. These tests state that identity as the contract.

The honesty rules are the other half: a window of no days cannot divide, and "we could not measure
it" must never come back as a projection of zero.
"""
import pytest

from app.modules.commcalc import demand_projection as dp


# ── the device-line test (the fact both callers kept their own copy of) ──────────────────────────
@pytest.mark.parametrize("dept,category,expected", [
    ("", "CellPhone", True),            # the custom report: category only, department blank
    ("", "cellphone", True),            # case is not a fact about the line
    ("Android - XP", "", True),         # the older format: department only
    ("IPHONE - XP", None, True),
    ("TABLET - XP", "", True),
    ("Plans", "Service", False),
    ("", "", False),
    (None, None, False),
])
def test_the_device_line_test_matches_both_feed_formats(dept, category, expected):
    assert dp.is_device_line(dept, category) is expected


def test_the_device_vocabulary_is_config_and_an_empty_declaration_is_honoured():
    """RULE TWO: the words live in config with house defaults. A tenant whose POS spells its device
    department differently declares it; an EXPLICITLY empty declaration means "this half does not
    identify a device here" and is obeyed rather than silently replaced by the house list."""
    assert dp.is_device_line("Handsets", "", depts=["handsets"]) is True
    assert dp.is_device_line("Android - XP", "", depts=["handsets"]) is False
    assert dp.is_device_line("", "CellPhone", categories=[]) is False
    assert dp.is_device_line("Android - XP", "CellPhone", depts=[], categories=[]) is False


# ── the projection ───────────────────────────────────────────────────────────────────────────────
def test_the_projection_is_the_arithmetic_both_callers_already_ran():
    """`/payables/forecast` and `/asset/po/recommendations` both compute
    `int(round(units / window * horizon))`. Stated once here, including the rounding, because
    `int(round(...))` is banker's rounding and a "tidier" `math.floor(x + 0.5)` would move live
    quantities on every .5."""
    p = dp.projection(14, 7, 14)
    assert p["velocity"] == 2.0
    assert p["projected"] == 28
    assert p["basis"] == "measured"
    # 2.5 -> 2 under banker's rounding, which is what both live callers do today.
    assert dp.projection(5, 2, 1)["projected"] == 2
    assert dp.projection(7, 2, 1)["projected"] == 4          # 3.5 -> 4


def test_nothing_sold_over_a_real_window_is_a_measured_zero():
    """We looked and nothing sold. That IS a number, and the row must say so rather than go blank —
    the opposite error from the one below."""
    p = dp.projection(0, 30, 60)
    assert p["projected"] == 0
    assert p["basis"] == "measured"


def test_a_window_of_no_days_cannot_be_measured_and_never_reads_as_zero():
    """A divisor of zero is the absence of a measurement. Returning 0 would put an order quantity of
    zero on the screen and call it planning (§19.26, the house rule this platform keeps re-learning)."""
    for window in (0, -3, None):
        p = dp.projection(9, window, 14)
        assert p["projected"] is None, window
        assert p["velocity"] is None, window
        assert p["basis"] == "no_window", window


def test_a_horizon_of_nothing_is_a_zero_order_not_an_unmeasurable_one():
    """Asking "what do I need for the next 0 months" has an answer, and it is none."""
    p = dp.projection(30, 30, 0)
    assert p["projected"] == 0 and p["basis"] == "measured"


def test_the_recommendation_nets_off_what_is_already_on_the_shelf():
    assert dp.recommend_qty(28, 10) == 18
    assert dp.recommend_qty(5, 40) == 0            # never a negative order
    assert dp.recommend_qty(None, 10) is None      # unmeasured in, unmeasured out


def test_the_projection_states_every_input_it_was_given():
    """A quantity on an order screen has to be arguable. Every term of the arithmetic travels with
    the answer so a reader never has to guess which window produced it."""
    p = dp.projection(14, 7, 14)
    assert p["units"] == 14 and p["window"] == 7 and p["horizon"] == 14


def test_junk_counts_read_as_nothing_sold_rather_than_raising():
    """The unit counts are this platform's own tallies, so junk here means a caller passed something
    unexpected. A planning screen must not 500 over it, and must not invent a quantity either."""
    for units in ("", None, "abc", {}):
        p = dp.projection(units, 7, 7)
        assert p["projected"] == 0, units
        assert p["basis"] == "measured", units
