"""Unit: the ONE home for "how much device-financing money did the carrier pay against this device".

WHY THIS MODULE EXISTS AT ALL. The construction was ~60 lines inside
`commcalc/router.py::_device_line_reimb_run` — the org's §58 declarations and keyword ladder, which
components ARE the device-financing side under this org's own routing, the `basis`-is-evidence
refusal, the symmetric lag window, the paged read of the per-line feed, and finally
`device_reimb_recon.carrier_line_side`. A second report needed the same answer (§COMMCALC-43's
chargeback amount), and copying that block would have been two derivations of one money question,
which the duplicate rule forbids. So it is factored out and BOTH callers dereference it.

These tests cover the decisions this layer makes. The per-device fold itself is
`device_reimb_recon.carrier_line_side` (pure, already locked by `harness_device_reimb_recon.py`),
and the wiring against a stand-in PostgREST client is `harness_carrier_device_money.py`.
"""
import pytest

from app.modules.commcalc import carrier_device_money as M


def test_the_lag_window_is_symmetric_around_the_period():
    # A device paid BEFORE the month it is claimed in is the live case that made this symmetric
    # (two devices paid in August, claimed in September). A forward-only window called them unpaid.
    # The window is enumerated by §27 in ITS spelling; the read expands every spelling afterwards
    # through the platform's one period-spelling home, so this layer states no second one.
    got = M.lag_periods({"lag_months": 2}, "2026-09")
    assert "September 2026" in got
    assert "July 2026" in got and "November 2026" in got
    assert len(got) == 5


def test_no_lag_declared_means_the_period_itself_and_nothing_else():
    assert M.lag_periods({"lag_months": 0}, "2026-09") == ["September 2026"]
    assert M.lag_periods({}, "2026-09") == ["September 2026"]


def test_no_period_asks_for_no_window_rather_than_guessing_one():
    assert M.lag_periods({"lag_months": 6}, "") is None


def test_a_component_the_org_routed_to_the_device_line_is_device_money():
    is_dev = M.device_dollar_predicate_from(
        declarations={"promo upgrade": "Re-imbursement"},
        rules=(), cfg={"component_lines": {"REIMBURSEMENT": "vip_reimb"},
                       "declaration_wins": True,
                       "category_components": {"re-imbursement": "REIMBURSEMENT"}},
        components={"REIMBURSEMENT"})
    assert is_dev("Promo Upgrade") is True


def test_a_commission_dollar_is_not_device_money():
    is_dev = M.device_dollar_predicate_from(
        declarations={"new activation bounty": "Commission"},
        rules=(), cfg={"component_lines": {"REIMBURSEMENT": "vip_reimb"},
                       "declaration_wins": True,
                       "category_components": {"commission": "COMMISSION",
                                               "re-imbursement": "REIMBURSEMENT"}},
        components={"REIMBURSEMENT"})
    assert is_dev("New Activation Bounty") is False


def test_a_last_resort_placement_is_not_evidence_about_a_device():
    # §66: a dollar whose component was reached on a basis the allowlist does not rule as evidence
    # is DECLINED at a per-device grain. The live instance is a per-store lump sum declared into a
    # category with no component row — there is no phone for it to name.
    is_dev = M.device_dollar_predicate_from(
        declarations={"ramp up subsidy": "MDF"},
        rules=({"match_type": "contains", "pattern": "subsidy",
                "component": "REIMBURSEMENT", "priority": 10},),
        cfg={"component_lines": {"REIMBURSEMENT": "vip_reimb"}, "declaration_wins": True,
             "category_components": {"re-imbursement": "REIMBURSEMENT"}},
        components={"REIMBURSEMENT"})
    assert is_dev("Ramp Up Subsidy") is False


def test_no_declared_routing_means_no_predicate_at_all_never_a_guess():
    # With nothing routed, the answer is None — "not configured" — so a caller reports that rather
    # than calling every dollar not-device-money, which would read as a measured zero.
    assert M.device_dollar_predicate_from(declarations={}, rules=(), cfg={}, components=set()) is None


def test_the_amount_for_a_device_is_none_when_the_feed_shows_no_row_for_it():
    idx = {"devices": {"351449743101051": {"total": 249.0, "lines": 1, "by_store": {}, "reps": {}}},
           "unidentified": {"rows": 0, "amount": 0.0}}
    assert M.amount_for(idx, "351449743101051") == 249.0
    # NOT 0.00: a rebate lands months after the sale, so "no row yet" is unmeasured, not nothing.
    assert M.amount_for(idx, "999999999999999") is None
    assert M.amount_for(idx, "") is None
    assert M.amount_for(None, "351449743101051") is None


def test_the_device_key_folds_the_way_the_feed_spells_it():
    idx = {"devices": {"351449743101051": {"total": 10.0, "lines": 1, "by_store": {}, "reps": {}}}}
    assert M.amount_for(idx, " 351449743101051 ") == 10.0
