"""Unit: the Payout Structure document must describe a plan's MULTI-MONTH schedule.

THE DEFECT, measured live 2026-11-16. `payout_structure.build_doc` read `commission_rule` and
nothing else, so a plan whose activation pays $0.00 at the sale and pays a three-month residual was
described to its own employees as paying NOTHING for an activation — the rule filed under "does not
pay" with the sentence *"Currently set to zero — earns no incentive."* LuxeLink's Chicago plan
(`1e53d090`) carries schedule "3MR Commission Payment": 5% of MRC in month 1, nothing in month 2,
13% in month 3, on every premium/BYOD activation, gated from month 2 on the carrier still paying.

THE CLASS, not the instance: a plan's pay is `commission_rule` + `commission_tier` + any
`plan_installment_schedule` attached to it (index §8). Any surface that describes what a plan pays
must read all three. These tests state that rule, the conservatism that keeps it from over-promising,
and the multi-tenant guarantee that one plan's schedule never leaks into another's document.
"""
from app.modules.commcalc import payout_structure as ps


SCHED = {"id": "s1", "plan_id": "p1", "name": "3MR Commission Payment", "num_months": 3,
         "trigger_match_field": "activation_bucket", "trigger_match_op": "in",
         "trigger_match_value": "premium,byod", "gate_mode": "paid_residual",
         "gate_from_month": 2, "clawback_enabled": False, "effective_from": "2026-07-01",
         "effective_to": None, "eligible_sale_periods": [], "is_active": True}
LINES = {"s1": [
    {"schedule_id": "s1", "month_index": 1, "payout_kind": "pct_mrc", "mrc_pct": "0.05",
     "flat_amount": "0"},
    {"schedule_id": "s1", "month_index": 2, "payout_kind": "flat", "flat_amount": "0",
     "mrc_pct": "0"},
    {"schedule_id": "s1", "month_index": 3, "payout_kind": "pct_mrc", "mrc_pct": "0.13",
     "flat_amount": "0"},
]}
ACT0 = {"label": "activation", "match_field": "activation_bucket", "match_op": "in",
        "match_value": "premium,byod", "qualifies": True, "payout_kind": "flat_per_unit",
        "amount": "0.0", "pct": "0.0", "sort": 1}
UPG0 = {"label": "upgrade", "match_field": "contract_type", "match_op": "equals",
        "match_value": "Upgrade", "qualifies": True, "payout_kind": "flat_per_unit",
        "amount": "0.0", "pct": "0.0", "sort": 2}
ACC = {"label": "accessory", "match_field": "accessory", "match_op": "equals", "match_value": "yes",
       "qualifies": True, "payout_kind": "pct_price", "amount": "0.175", "pct": "0.175", "sort": 0}
PLAN = {"id": "p1", "name": "Total Employee Comp Chicago", "is_active": True,
        "rules": [ACC, ACT0, UPG0], "tiers": [], "assignments": []}


def _why(doc, what):
    return {i["what"]: i["why"] for i in doc["plans"][0]["no_pay_items"]}[what]


def test_the_schedule_is_described_at_all():
    ins = ps.describe_installments([SCHED], LINES, "p1")
    assert len(ins) == 1
    assert ins[0]["name"] == "3MR Commission Payment"
    assert ins[0]["horizon"] == 3
    assert ins[0]["pays"] is True


def test_each_month_prints_the_field_the_engine_reads():
    """`pct_mrc` pays `mrc_pct`; `flat` pays `flat_amount`. The unread field is never printed."""
    months = ps.describe_installments([SCHED], LINES, "p1")[0]["months"]
    assert [m["rate"] for m in months] == ["5% of the monthly plan charge", "$0.00",
                                           "13% of the monthly plan charge"]


def test_a_zero_month_is_shown_not_dropped():
    """Hiding month 2 would make a three-month schedule read as a two-month one."""
    months = ps.describe_installments([SCHED], LINES, "p1")[0]["months"]
    assert [m["month"] for m in months] == ["Month 1", "Month 2", "Month 3"]
    assert months[1]["pays"] is False


def test_the_gate_is_stated_only_on_the_months_it_applies_to():
    """`gate_from_month=2`: month 1 is unconditional. Printing the gate on it invents a condition;
    omitting it from months 2–3 promises money the engine withholds."""
    months = ps.describe_installments([SCHED], LINES, "p1")[0]["months"]
    assert months[0]["condition"] == "Paid regardless."
    assert "still paying us" in months[1]["condition"]
    assert months[1]["condition"] == months[2]["condition"]


def test_an_unknown_gate_mode_does_not_promise_the_money():
    months = ps.describe_installments([dict(SCHED, gate_mode="something_new")], LINES, "p1")[0]["months"]
    assert months[1]["condition"] == ps.GATE_UNKNOWN


def test_a_zero_rule_the_schedule_covers_says_the_pay_arrives_later():
    after = ps.build_doc([PLAN], schedules=[SCHED], installment_lines=LINES)
    assert "pays these sales over several months" in _why(after, "Activation")


def test_a_zero_rule_the_schedule_does_not_cover_keeps_its_honest_sentence():
    """CONSERVATIVE BY DESIGN. Over-promising pay is the dangerous direction, so an unproven
    overlap must not be excused."""
    after = ps.build_doc([PLAN], schedules=[SCHED], installment_lines=LINES)
    assert _why(after, "Upgrade") == "Currently set to zero — earns no incentive."


def test_an_all_zero_schedule_cannot_excuse_a_zero_rule():
    zero_lines = {"s1": [dict(LINES["s1"][1], month_index=i) for i in (1, 2, 3)]}
    after = ps.build_doc([PLAN], schedules=[SCHED], installment_lines=zero_lines)
    assert _why(after, "Activation") == "Currently set to zero — earns no incentive."


def test_another_plans_schedule_never_leaks_into_this_document():
    assert ps.describe_installments([dict(SCHED, plan_id="other")], LINES, "p1") == []


def test_an_inactive_schedule_is_left_out():
    assert ps.describe_installments([dict(SCHED, is_active=False)], LINES, "p1") == []


def test_a_schedule_with_no_months_is_disclosed_rather_than_silently_empty():
    ins = ps.describe_installments([SCHED], {}, "p1")[0]
    assert ins["months"] == []
    assert ins["warnings"] == ["No month is configured on this schedule yet, so it pays nothing today."]


def test_a_short_schedule_is_disclosed():
    ins = ps.describe_installments([SCHED], {"s1": LINES["s1"][:2]}, "p1")[0]
    assert ins["warnings"] == ["This schedule runs for 3 months but only 2 are configured."]


def test_the_effective_window_is_stated_and_sale_periods_win():
    assert ps.describe_installments([SCHED], LINES, "p1")[0]["window"] == \
        "Applies to sales made on or after 2026-07-01"
    both = dict(SCHED, eligible_sale_periods=["September 2026", "October 2026"])
    assert ps.describe_installments([both], LINES, "p1")[0]["window"] == \
        "Applies to sales in: September 2026, October 2026"
    assert ps.describe_installments([dict(SCHED, effective_from=None)], LINES, "p1")[0]["window"] is None


def test_a_tenant_with_no_schedule_gets_the_document_it_got_before():
    """The multi-tenant guarantee: a tenant that configures no schedule is byte-identical."""
    assert ps.build_doc([PLAN], generated_at="x") == \
        ps.build_doc([PLAN], schedules=[], installment_lines={}, generated_at="x")


def test_a_plan_paid_only_through_its_schedule_is_not_warned_as_paying_nothing():
    only = dict(PLAN, rules=[ACT0])
    warned = ps.build_doc([only], schedules=[SCHED], installment_lines=LINES)["plans"][0]["warnings"]
    assert not any("currently pay" in w for w in warned)
    assert any("currently pay" in w for w in ps.build_doc([only])["plans"][0]["warnings"])


def test_the_explainer_mentions_multi_month_pay_only_when_a_plan_has_it():
    with_s = " ".join(ps.build_doc([PLAN], schedules=[SCHED],
                                   installment_lines=LINES)["how_it_works"]).lower()
    without = " ".join(ps.build_doc([PLAN])["how_it_works"]).lower()
    assert "paid over several months" in with_s
    assert "paid over several months" not in without


def test_trigger_covers_is_conservative():
    assert ps.trigger_covers(SCHED, ACT0) is True
    assert ps.trigger_covers(SCHED, UPG0) is False
    assert ps.trigger_covers(dict(SCHED, trigger_match_field="any"), UPG0) is True
    # same field, no overlap
    assert ps.trigger_covers(dict(SCHED, trigger_match_value="byod"),
                             dict(ACT0, match_op="equals", match_value="premium")) is False
    # `contains` is ONE value, semicolons and all — never split into a list (trap ②)
    assert ps.trigger_covers(dict(SCHED, trigger_match_field="tender_type", trigger_match_op="contains",
                                  trigger_match_value="Credit Card; TW Financing"),
                             {"match_field": "tender_type", "match_op": "equals",
                              "match_value": "Credit Card"}) is False


def test_the_boost_document_carries_the_key_empty():
    """One document model, one renderer: the Boost engine has no plans and therefore no schedules."""
    assert ps.build_boost_doc({}, (), tenant_name="House")["plans"][0]["installments"] == []


def test_the_internal_rule_handle_never_reaches_the_model():
    doc = ps.build_doc([PLAN], schedules=[SCHED], installment_lines=LINES)
    for item in doc["plans"][0]["no_pay_items"]:
        assert "_rule" not in item
