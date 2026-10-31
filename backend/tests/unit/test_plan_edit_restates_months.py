"""Unit: EDITING A COMMISSION PLAN RESTATES THE MONTHS IT PAID (index §COMMCALC-41.9).

Owner, 2026-10-31: *"i upldated the commission for luxelink in ny plan but it did not get auto
updated as we confirmed yesterday that everytime anything is change it will auto update acorss the
board, i changed teh comission from june - oct"*.

The platform already holds the rule this is an instance of — *a build's DECLARATION is one of its
inputs* (§COMMCALC-41.8, shipped 2026-10-29 for the carrier's net payment terms). It was applied to
ONE build. The `commission` build, the oldest and the one that moves rep pay, declared its feeds and
no declaration at all, so a plan edit queued nothing and the figures waited for an unrelated upload.

Two facts are proved here, both pure:
  · the registry says a plan table makes the commission build stale;
  · a declaration edit carries no month of its own, so the build names the months it restates —
    bounded, because restating a 2024 month because somebody fixed a rate today is not what anyone
    asked for.
"""
import pytest

from app.modules.commcalc import auto_calc, derived_builds


COMMISSION = derived_builds.by_key("commission")


def test_the_commission_build_is_registered_and_period_scoped():
    assert COMMISSION is not None
    assert COMMISSION["scope"] == derived_builds.SCOPE_PERIOD


@pytest.mark.parametrize("table", [
    "commission_plan", "commission_rule", "commission_tier", "commission_plan_assignment",
    "payout_schedule", "payout_schedule_line",
    "plan_installment_schedule", "plan_installment_line",
])
def test_every_plan_declaration_table_makes_the_commission_build_stale(table):
    assert derived_builds.matches(COMMISSION, table), (
        f"{table} declares rep pay but editing it makes nothing stale")


def test_a_feed_still_makes_it_stale():
    assert derived_builds.matches(COMMISSION, "raw_payment_detail")


def test_an_unrelated_table_does_not():
    assert not derived_builds.matches(COMMISSION, "asset_ledger")


def test_the_declaration_is_never_also_listed_as_a_feed():
    """One fact, one home — B6's rule, asserted for this build."""
    assert not (set(derived_builds.config_inputs(COMMISSION))
                & set(derived_builds.static_inputs(COMMISSION)))


# ── the months a declaration edit restates ───────────────────────────────────────────────────────
def test_restate_months_keeps_the_recent_months_and_drops_the_ancient_one():
    """PURE. Cellfonz R Us really does hold a 'July 2024' row beside its 2026 months (measured
    2026-10-31) — a rate fixed today must not rewrite it."""
    got = auto_calc.restate_periods(
        ["March 2026", "June 2026", "July 2026", "August 2026", "September 2026", "October 2026",
         "July 2024"],
        now="2026-10-31", months=12)
    assert "July 2024" not in got
    assert got == ["March 2026", "June 2026", "July 2026", "August 2026", "September 2026",
                   "October 2026"]


def test_restate_months_is_oldest_first_and_deduplicates_the_two_spellings():
    got = auto_calc.restate_periods(["2026-10", "October 2026", "September 2026"],
                                    now="2026-10-31", months=12)
    assert got == ["September 2026", "October 2026"]


def test_restate_months_over_no_stored_month_is_empty_not_a_guess():
    assert auto_calc.restate_periods([], now="2026-10-31", months=12) == []


def test_the_bound_is_a_house_default_and_never_a_tenants_word():
    """RULE TWO: the look-back is a POLICY, so a house default is right — and it is a real bound."""
    assert 1 <= auto_calc.DEFAULT_RESTATE_MONTHS <= 36
