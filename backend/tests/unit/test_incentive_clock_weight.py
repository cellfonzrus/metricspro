"""AN INCENTIVE POOL IS SPLIT ON CLOSED PUNCHES, AND A HAND-EDITED DAY WEIGHS NOTHING.

Owner Sanjot, 2026-11-27 14:35 ET, verbatim: *"let the daily cincentive as itonly affects ny
logistics bee ffeceive going forward based on eh acual clock in, if te hours are udpated manuay then
we cannot oay for thsose hours"*.

WHAT WAS WRONG. `commcalc/router._incentive_hours_for_day` weighted a `threshold_pool` rule whose
`payout.split == "hours"` with `actual_hours` else `scheduled_hours`. `shifts.actual_hours` carries a
figure on 7 of 7,740 live rows (index §14y), so in practice **the pool was split by the SCHEDULE** —
a rep rostered 9h who punched 4h drew a 9/Σ share. The pool total never moved; who got paid what
did.

★ THIS IS A NARROWER QUESTION THAN PAY ASKS, DELIBERATELY ★ — and that is the whole reason this
rule has its own home. Payroll's `corrections_apply` / `corrections_effective_from` (PR #679,
§STOREOPS-07) **do** fold a `manual_hours` correction into paid hours, on purpose: a day somebody
wrote down is a day somebody wrote down, and the business owes it. The INCENTIVE is a reward for
measured work, and the owner's sentence is explicit that a hand-edited day may not earn one. So:

  | question | manual correction | the schedule |
  | --- | --- | --- |
  | what are this person's PAID hours? (`resolve_day`) | folded, on and after the org's date | paid when the tick is on |
  | what WEIGHT does this person's day carry in an incentive pool? | **ZERO WEIGHT FOR THAT DAY** | **never any weight** |

The two must never be merged into one helper, and this file exists partly to pin that they have not
been.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.commcalc import incentive_clock_floor as icf       # noqa: E402
from app.modules.storeops import payroll_hours_basis as hb          # noqa: E402

CFG = hb.resolve_config(None)


def _punch(hours, *, closed=True, auto=None):
    p = {"hours": hours, "clock_in": "2026-11-20T09:00:00Z"}
    p["clock_out"] = "2026-11-20T17:00:00Z" if closed else None
    if auto is not None:
        p["auto_clocked_out"] = auto
    return p


# ══════════════════════════════════════════════════════════════════════════════════════════════
# A. the weight rule
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_a_closed_punch_is_the_weight():
    h, reason = hb.incentive_weight_hours([_punch(7.5)], CFG)
    assert h == 7.5
    assert reason == hb.INCENTIVE_WEIGHT_CLOCKED


def test_several_closed_punches_in_a_day_add_up():
    h, _ = hb.incentive_weight_hours([_punch(4.0), _punch(3.25)], CFG)
    assert h == 7.25


def test_no_punch_at_all_weighs_zero_and_says_why():
    h, reason = hb.incentive_weight_hours([], CFG)
    assert h == 0.0
    assert reason == hb.INCENTIVE_WEIGHT_NO_PUNCH


def test_an_open_punch_is_not_a_measurement_and_carries_no_weight():
    """Somebody is still clocked in. That is not evidence of a finished day's work."""
    h, reason = hb.incentive_weight_hours([_punch(None, closed=False)], CFG)
    assert h == 0.0
    assert reason == hb.INCENTIVE_WEIGHT_NO_PUNCH


def test_an_open_punch_that_already_carries_hours_still_weighs_nothing():
    """★ THE ONE THAT PROVES `punch_usability` IS LOAD-BEARING. The plain open punch above has
    `hours: None`, so it weighs zero even if the usability test were bypassed — which makes it a
    weak guard. This one is clocked IN with hours already on the row: only the CLOSED test stops it
    counting, so bypassing `punch_usability` shows up here and nowhere else.
    """
    open_with_hours = {"hours": 8.0, "clock_in": "2026-11-20T09:00:00Z", "clock_out": None}
    h, reason = hb.incentive_weight_hours([open_with_hours], CFG)
    assert h == 0.0, "an unfinished day weighted an incentive"
    assert reason == hb.INCENTIVE_WEIGHT_NO_PUNCH


def test_the_closed_punch_rule_is_the_one_home_not_a_second_copy():
    """`punch_usability` decides CLOSED, including the org's auto-clockout declaration. This rule
    must not re-spell that test — so an org that counts auto-clockouts weights them, and one that
    does not, does not, without this function knowing what an auto-clockout is."""
    auto = [_punch(8.0, auto=True)]
    off = hb.resolve_config({"count_auto_clockout_punches": False})
    on = hb.resolve_config({"count_auto_clockout_punches": True})
    assert hb.incentive_weight_hours(auto, off)[0] == 0.0
    assert hb.incentive_weight_hours(auto, on)[0] == 8.0


# ══════════════════════════════════════════════════════════════════════════════════════════════
# B. ★ a hand-edited day weighs NOTHING — the owner's second sentence
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_a_manual_correction_removes_the_whole_days_weight():
    """*"if te hours are udpated manuay then we cannot oay for thsose hours"*. Not "the punch minus
    the correction", and not "the correction instead of the punch" — the DAY carries no weight."""
    h, reason = hb.incentive_weight_hours([_punch(7.5)], CFG, has_manual_correction=True)
    assert h == 0.0
    assert reason == hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED


def test_the_manual_block_outranks_a_perfectly_good_punch():
    assert hb.incentive_weight_hours([_punch(8.0)], CFG)[0] == 8.0
    assert hb.incentive_weight_hours([_punch(8.0)], CFG, has_manual_correction=True)[0] == 0.0


def test_a_corrected_day_with_no_punch_is_still_the_manual_reason_not_the_no_punch_one():
    """The reader is told the day was hand-edited, which is actionable, rather than "nobody
    clocked", which is not what happened."""
    h, reason = hb.incentive_weight_hours([], CFG, has_manual_correction=True)
    assert h == 0.0 and reason == hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED


def test_the_incentive_rule_ignores_the_orgs_PAY_correction_switches_entirely():
    """★ THE ANTI-COUPLING ASSERTION. `count_manual_hours` and `corrections_effective_from` are PAY
    controls (PR #679). An org that folds corrections into pay must still carry no incentive weight
    on a corrected day — otherwise the owner's sentence would be silently overridden by a switch he
    set for a different question."""
    folds = hb.resolve_config({"count_manual_hours": True,
                               "corrections_effective_from": "2026-01-01"})
    h, reason = hb.incentive_weight_hours([_punch(8.0)], folds, has_manual_correction=True)
    assert h == 0.0 and reason == hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED


def test_pay_still_folds_a_correction_so_this_change_did_not_leak_into_payroll():
    """The other half of the same guard: PR #679's posture is UNTOUCHED. A correction on an org that
    has declared the date still reaches PAID hours."""
    folds = hb.resolve_config({"count_manual_hours": True,
                               "corrections_effective_from": "2026-01-01"})
    applies, _why = hb.corrections_apply("2026-11-20", folds)
    assert applies is True, "payroll's correction fold was broken by an incentive change"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# C. the floor — derived, stored, read; nothing already paid restates
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_the_floor_is_derived_from_the_first_CLOSED_punch_per_store():
    rows = [
        {"org_id": "O1", "store_code": "S1", "work_date": "2026-11-10",
         "clock_out": "x", "hours": 8},
        {"org_id": "O1", "store_code": "S1", "work_date": "2026-11-04",
         "clock_out": "x", "hours": 8},
        {"org_id": "O1", "store_code": "S2", "work_date": "2026-11-20",
         "clock_out": "x", "hours": 8},
    ]
    assert icf.derive(rows) == {("O1", "S1"): "2026-11-04", ("O1", "S2"): "2026-11-20"}


def test_an_open_punch_cannot_set_a_floor():
    """An open punch measured nothing, so it is not the day they started clocking in."""
    rows = [{"org_id": "O1", "store_code": "S1", "work_date": "2026-11-01",
             "clock_out": None, "hours": None},
            {"org_id": "O1", "store_code": "S1", "work_date": "2026-11-09",
             "clock_out": "x", "hours": 8}]
    assert icf.derive(rows) == {("O1", "S1"): "2026-11-09"}


def test_each_store_gets_its_own_floor_and_no_store_inherits_anothers():
    rows = [{"org_id": "O1", "store_code": "EARLY", "work_date": "2026-01-01",
             "clock_out": "x", "hours": 8},
            {"org_id": "O1", "store_code": "LATE", "work_date": "2026-11-20",
             "clock_out": "x", "hours": 8}]
    d = icf.derive(rows)
    assert d[("O1", "EARLY")] == "2026-01-01" and d[("O1", "LATE")] == "2026-11-20"


def test_a_tenant_with_no_closed_punch_gets_NO_floor_and_is_unmeasurable():
    """★ NEVER handed another tenant's date (§COMMCALC-49.10). No punches ⇒ no entry at all."""
    assert icf.derive([{"org_id": "O2", "store_code": "S9", "work_date": "2026-11-01",
                        "clock_out": None, "hours": None}]) == {}
    assert icf.derive([]) == {}


def test_no_stored_floor_means_the_rule_is_NOT_in_effect_so_nothing_changes_on_apply():
    """★ THE SAFETY POSTURE. The stored floor is the switch. Absent ⇒ today's behaviour, so
    deploying this code moves no money until Sanjot accepts a floor per store."""
    assert icf.in_effect(None, "2026-11-27") is False
    assert icf.in_effect({}, "2026-11-27") is False
    assert icf.in_effect({"effective_from": None}, "2026-11-27") is False


def test_a_day_before_the_floor_keeps_todays_behaviour():
    row = {"effective_from": "2026-11-20"}
    assert icf.in_effect(row, "2026-11-19") is False
    assert icf.in_effect(row, "2026-11-20") is True      # inclusive — "going forward FROM" that day
    assert icf.in_effect(row, "2026-12-01") is True


def test_an_undated_day_is_not_pushed_to_either_side_of_the_boundary():
    assert icf.in_effect({"effective_from": "2026-11-20"}, None) is False
    assert icf.in_effect({"effective_from": "2026-11-20"}, "") is False


def test_a_stored_floor_is_read_and_a_later_backfilled_punch_cannot_walk_it_backwards():
    """★ derived → STORED → read. `derive` only ever PROPOSES. A punch backfilled into an earlier
    day would move `derive`'s answer, and if the money read the derivation it would restate a closed
    period. `in_effect` reads the STORED row and never recomputes."""
    stored = {"effective_from": "2026-11-20"}
    backfilled = [{"org_id": "O1", "store_code": "S1", "work_date": "2026-10-01",
                   "clock_out": "x", "hours": 8}]
    assert icf.derive(backfilled)[("O1", "S1")] == "2026-10-01"   # the proposal moved
    assert icf.in_effect(stored, "2026-10-15") is False           # the money did not
