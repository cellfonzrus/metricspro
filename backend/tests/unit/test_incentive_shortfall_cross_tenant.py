"""§COMMCALC-81 across tenants — each business's own gate, its own words, its own money.

The 2026-10-30 directive is what these exist for: *"all features should be for all tenants"*, said
of a report that defaulted to one tenant's POS labels and told the other it had sold nothing. A
shortfall surface is exactly that shape of risk — it reads a tenant's declared categories, its
declared rules and its declared threshold, and a default anywhere in that chain would coach one
business with another's vocabulary.

So every test here runs TWO tenants with deliberately opposite declarations through ONE engine, and
asserts both halves: that each tenant's own shape produces its own answer, and that neither tenant's
declaration can be reached from the other's read.
"""
import pytest

from app.modules.commcalc import incentive_shortfall as sf
from app.modules.commcalc import daily_incentive as di
from app.modules.closing import metric_catalog as mc


# ── TENANT A: a UNIT gate over two shipment categories, paying per unit ──────────────────────────
CAT_A = mc.build([
    {"field_key": "fast_lane", "label": "Fast Lane", "value_kind": "count",
     "declared_by": "employee", "unit_price": 2.5, "in_revenue": True},
    {"field_key": "slow_lane", "label": "Slow Lane", "value_kind": "count",
     "declared_by": "employee", "unit_price": 2.5, "in_revenue": True},
])
RULE_A = {"id": "a1", "label": "Lane Bonus", "rule_kind": "per_unit", "is_active": True,
          "payout": {"rate": 2.5, "basis": "units", "metric_keys": ["fast_lane", "slow_lane"]},
          "qualifier": {"scope": "store", "basis": "units", "op": "gte", "threshold": 5,
                        "metric_keys": ["fast_lane", "slow_lane"]}}

# ── TENANT B: a MONEY gate over one service category, paying a percentage ────────────────────────
CAT_B = mc.build([
    {"field_key": "studio_time", "label": "Studio Time", "value_kind": "amount",
     "declared_by": "employee", "in_revenue": True},
])
RULE_B = {"id": "b1", "label": "Studio Share", "rule_kind": "percent_of_amount", "is_active": True,
          "payout": {"rate": 0.1, "basis": "amount", "metric_keys": ["studio_time"]},
          "qualifier": {"scope": "store", "basis": "amount", "op": "gte", "threshold": 1000.0,
                        "metric_keys": ["studio_time"]}}


def _day(catalog, rows):
    lines = [{"employee_name": w, "metric_key": k, "units": u, "amount": a} for w, k, u, a in rows]
    return {"declared_map": mc.declared(catalog, None, lines),
            "per_employee": mc.by_employee(catalog, lines)}


DAY_A = _day(CAT_A, [("ANA", "fast_lane", 2, 0.0), ("BEN", "slow_lane", 1, 0.0)])
DAY_B = _day(CAT_B, [("CHRIS", "studio_time", 0, 912.50)])


def _item(catalog, rule, day):
    return sf.rule_shortfall(di.compute_day, catalog, rule, day)


def test_each_tenant_gets_its_own_shortfall_in_its_own_unit():
    a, b = _item(CAT_A, RULE_A, DAY_A), _item(CAT_B, RULE_B, DAY_B)
    assert (a["shortfall"]["short_by"], a["shortfall"]["unit"]) == (2, "units")
    assert (b["shortfall"]["short_by"], b["shortfall"]["unit"]) == (87.5, "money")


def test_the_forgone_money_is_the_pay_on_work_already_done():
    # This is the whole argument of the feature: a gate withholds the pay on everything already
    # sold, not on the part that was missing. Three units at $2.50; 10% of $912.50.
    assert _item(CAT_A, RULE_A, DAY_A)["forgone"] == 7.5
    assert _item(CAT_B, RULE_B, DAY_B)["forgone"] == 91.25


def test_neither_tenants_words_reach_the_others_coaching():
    a_words = sf.coach_sentence(_item(CAT_A, RULE_A, DAY_A))
    b_words = sf.coach_sentence(_item(CAT_B, RULE_B, DAY_B))
    assert "Lane Bonus" in a_words and "Studio" not in a_words
    assert "Studio Share" in b_words and "Lane" not in b_words


def test_one_tenants_rule_against_the_others_catalog_is_blocked_not_answered():
    # Nothing declared means nothing identified, and the surface says so. A silent fall back to the
    # other tenant's categories is the defect the 2026-10-30 directive was written about.
    crossed = _item(CAT_B, RULE_A, DAY_B)
    assert crossed["kind"] == sf.KIND_BLOCKED
    assert crossed["shortfall"] is None
    assert sf.steps(crossed) == []


def test_the_walkthrough_labels_come_from_the_tenants_own_catalog():
    labels = [d["label"] for d in sf.steps(_item(CAT_A, RULE_A, DAY_A))[0]["detail"]]
    assert labels == ["Fast Lane", "Slow Lane"]


def test_a_cleared_gate_coaches_nobody_in_either_tenant():
    assert _item(CAT_A, RULE_A, _day(CAT_A, [("ANA", "fast_lane", 5, 0.0)])) is None
    assert _item(CAT_B, RULE_B, _day(CAT_B, [("CHRIS", "studio_time", 0, 1000.0)])) is None


def test_the_period_fold_keys_each_tenants_days_separately():
    folded = sf.fold_days([
        {"date": "2026-10-20", "store_code": "A-1",
         "items": sf.day_shortfalls(di.compute_day, CAT_A, [RULE_A], DAY_A)},
        {"date": "2026-10-20", "store_code": "B-1",
         "items": sf.day_shortfalls(di.compute_day, CAT_B, [RULE_B], DAY_B)},
    ])
    assert folded["days_missed"] == 2
    assert folded["forgone"] == 98.75
    assert {r["rule"] for r in folded["by_rule"]} == {"Lane Bonus", "Studio Share"}


def test_the_reach_policy_is_a_policy_and_not_a_vocabulary():
    # A house default stays legitimate for a window; what it must never do is carry a tenant's own
    # words. Widening it changes a verdict for whichever tenant declares the wider window, and for
    # nobody else.
    far = {"basis": "units", "short_by": 3, "threshold": 5}
    assert not sf.within_reach(far)
    assert sf.within_reach(far, reach_units=3)
    assert not sf.within_reach(far)      # the override did not become the new default


def test_an_employee_scope_gate_names_the_person_in_either_tenant():
    rule = {**RULE_A, "qualifier": {**RULE_A["qualifier"], "scope": "employee", "threshold": 2}}
    item = _item(CAT_A, rule, DAY_A)
    assert item["employee"] == "BEN"
    assert sf.fold_days([{"date": "d", "store_code": "A-1", "items": [item]}])["by_employee"][0] == {
        "employee": "BEN", "days": 1, "days_within_reach": 1, "forgone": 2.5}


@pytest.mark.parametrize("scope", ["", "   ", None])
def test_an_unqualified_rule_is_never_a_near_miss(scope):
    rule = {**RULE_A, "qualifier": {"scope": scope} if scope is not None else {}}
    assert _item(CAT_A, rule, DAY_A) is None
