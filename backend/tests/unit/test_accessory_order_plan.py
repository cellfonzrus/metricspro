"""Unit: `commcalc/accessory_order_plan.py` — what to order, from what the stores actually sold.

OWNER ASK 2026-10-29, verbatim: *"for sales forecasting need to add one more report to process
accessory orders based on the previous 3 months of average sales and showing the best sellign models
on the top and asking the user to enter where the spoecific model is end of life or current , once
tehy check the system calculates the following accessory projected to sell in the next 2 months , the
case mix will be decalred by the user but by defaul you can use this mix - Blacks and Blues - 70%,
pinks / red - 30% - 1 per sale / screen protectors - 1 per sale / Cables type c- type c 1 per sale /
Charging cubes - 1 per sale / report should have all out store filters and market filters , look back
months and future planning months"*.

WRITTEN BEFORE THE MODULE (TDD, project standard 2026-10-28). Each test states one rule of the ask or
one honesty rule, so the arithmetic can be argued with before it is wired to a screen.
"""
import pytest

from app.modules.commcalc import accessory_order_plan as aop


MONTHS = ("2026-08", "2026-09", "2026-10")

#: Three months of device units per model, per the shape the endpoint hands in.
UNITS = {
    "IPHONE 16E BLK 128GB": {"2026-08": 40, "2026-09": 50, "2026-10": 60},
    "moto g play - 2026":   {"2026-08": 20, "2026-09": 10, "2026-10": 0},
    "OLD HANDSET 32GB":     {"2026-08": 9,  "2026-09": 0,  "2026-10": 0},
}
CURRENT_AND_EOL = {"IPHONE 16E BLK 128GB": "current", "moto g play - 2026": "current",
                   "OLD HANDSET 32GB": "eol"}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE DECLARATIONS — the mix and the attach plan are the user's, with the owner's defaults
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def test_the_house_case_mix_is_the_one_the_owner_stated():
    mix, basis = aop.resolve_case_mix(None)
    assert basis == "house_default"
    assert [(m["key"], m["share"]) for m in mix] == [("dark", 0.70), ("bright", 0.30)]
    # The LABELS carry the owner's own words so the screen needs no glossary.
    assert [m["label"] for m in mix] == ["Blacks and blues", "Pinks and reds"]


def test_the_house_attach_plan_is_one_of_each_per_device_sold():
    plan = aop.resolve_attach_plan(None)
    assert {p["key"]: p["per_device"] for p in plan} == {
        "case": 1.0, "screen_protector": 1.0, "cable_c_to_c": 1.0, "charging_cube": 1.0}
    # Only the case is split by colour; a screen protector has no colour mix to honour.
    assert [p["key"] for p in plan if p["colour_split"]] == ["case"]


def test_a_declared_mix_replaces_the_default_and_says_it_was_declared():
    mix, basis = aop.resolve_case_mix([{"key": "dark", "label": "Dark", "share": 50},
                                       {"key": "bright", "label": "Bright", "share": 50}])
    assert basis == "declared"
    assert [m["share"] for m in mix] == [0.5, 0.5]


def test_a_declared_mix_that_does_not_add_up_is_normalised_and_reported():
    """A mix of 60/30 is 90% of the cases — the missing tenth is not a colour, it is a typo. The
    shares are normalised so the colour quantities still foot to the case quantity, and the basis
    NAMES the correction rather than quietly rescaling behind the reader."""
    mix, basis = aop.resolve_case_mix([{"key": "dark", "share": 60}, {"key": "bright", "share": 30}])
    assert basis == "declared_normalised"
    assert mix[0]["share"] == pytest.approx(2 / 3, abs=1e-6)
    assert sum(m["share"] for m in mix) == pytest.approx(1.0)


def test_a_mix_declaring_nothing_falls_back_to_the_house_mix():
    for raw in ([], {}, "nonsense", [{"key": "", "share": 0}]):
        _mix, basis = aop.resolve_case_mix(raw)
        assert basis == "house_default", raw


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE MODEL TABLE — best sellers on top, and nothing projected until the user has ruled
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def test_the_best_seller_is_the_first_row():
    rows = aop.model_rows(UNITS, months=MONTHS, lifecycle=CURRENT_AND_EOL, horizon_months=2)
    assert [r["model"] for r in rows][0] == "IPHONE 16E BLK 128GB"
    assert rows[0]["rank"] == 1
    assert [r["units_total"] for r in rows] == [150, 30, 9]


def test_each_row_carries_the_month_by_month_units_in_the_window_order():
    rows = aop.model_rows(UNITS, months=MONTHS, lifecycle=CURRENT_AND_EOL, horizon_months=2)
    top = rows[0]
    assert top["units_by_month"] == [{"period": "2026-08", "units": 40},
                                     {"period": "2026-09", "units": 50},
                                     {"period": "2026-10", "units": 60}]
    assert top["avg_monthly"] == 50.0
    assert top["months_observed"] == 3


def test_a_current_model_is_projected_over_the_planning_months():
    """"the following accessory projected to sell in the next 2 months" — the average monthly units
    carried forward over the horizon the user picked."""
    rows = aop.model_rows(UNITS, months=MONTHS, lifecycle=CURRENT_AND_EOL, horizon_months=2)
    assert rows[0]["projected_units"] == 100           # 50/month x 2
    assert rows[0]["projection_basis"] == "measured"
    assert aop.model_rows(UNITS, months=MONTHS, lifecycle=CURRENT_AND_EOL,
                          horizon_months=3)[0]["projected_units"] == 150


def test_an_end_of_life_model_is_projected_at_zero_and_its_demand_is_named():
    """Zero is the RIGHT answer for a model being retired — but the units it sold are still real, so
    the row keeps them and the payload reports what share of history is being retired. Dropping the
    row would make the retired demand invisible, which is how a store runs out of a case it still
    sells."""
    rows = aop.model_rows(UNITS, months=MONTHS, lifecycle=CURRENT_AND_EOL, horizon_months=2)
    eol = [r for r in rows if r["model"] == "OLD HANDSET 32GB"][0]
    assert eol["lifecycle"] == "eol"
    assert eol["projected_units"] == 0
    assert eol["projection_basis"] == "end_of_life"
    assert eol["units_total"] == 9                      # history is not rewritten by the ruling


def test_a_model_nobody_has_ruled_on_is_not_projected_at_all():
    """The owner's flow is "asking the user to enter where the specific model is end of life or
    current, ONCE THEY CHECK the system calculates". So an undeclared model projects NOTHING — not
    zero (which would read as a decision to retire it) and not its average (which would be the
    report deciding on the user's behalf). The row says it is waiting."""
    rows = aop.model_rows(UNITS, months=MONTHS, lifecycle={}, horizon_months=2)
    assert {r["lifecycle"] for r in rows} == {"undeclared"}
    assert all(r["projected_units"] is None for r in rows)
    assert all(r["projection_basis"] == "awaiting_declaration" for r in rows)


def test_a_model_that_launched_mid_window_averages_over_the_months_it_existed():
    """A phone that first sold last month has not been selling for three months. Dividing its units
    by the whole look-back would understate the order by two thirds, which is the quiet way a new
    best-seller gets under-ordered."""
    rows = aop.model_rows({"NEW 5G 256GB": {"2026-10": 30}}, months=MONTHS,
                          lifecycle={"NEW 5G 256GB": "current"}, horizon_months=2)
    r = rows[0]
    assert r["months_observed"] == 1
    assert r["avg_monthly"] == 30.0
    assert r["projected_units"] == 60
    assert r["history_basis"] == "partial_history"


def test_a_model_selling_through_the_whole_window_says_so():
    rows = aop.model_rows(UNITS, months=MONTHS, lifecycle=CURRENT_AND_EOL, horizon_months=2)
    assert rows[0]["history_basis"] == "full_history"


def test_a_model_with_no_units_in_the_window_is_not_a_row():
    rows = aop.model_rows({"GHOST": {"2026-08": 0, "2026-09": 0}}, months=MONTHS,
                          lifecycle={}, horizon_months=2)
    assert rows == []


def test_the_share_of_history_each_model_holds_is_on_the_row():
    rows = aop.model_rows(UNITS, months=MONTHS, lifecycle=CURRENT_AND_EOL, horizon_months=2)
    assert rows[0]["share"] == pytest.approx(150 / 189, abs=1e-6)
    assert sum(r["share"] for r in rows) == pytest.approx(1.0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE ORDER — one of each per device, and the cases split by the declared colour mix
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def test_one_of_each_accessory_per_projected_device():
    mix, _b = aop.resolve_case_mix(None)
    rows = aop.accessory_rows(100, mix=mix, plan=aop.resolve_attach_plan(None))
    by = {r["key"]: r for r in rows}
    assert {k: by[k]["qty"] for k in by} == {"case": 100, "screen_protector": 100,
                                            "cable_c_to_c": 100, "charging_cube": 100}


def test_the_cases_are_split_by_the_declared_colour_mix():
    mix, _b = aop.resolve_case_mix(None)
    case = [r for r in aop.accessory_rows(100, mix=mix, plan=aop.resolve_attach_plan(None))
            if r["key"] == "case"][0]
    assert [(c["key"], c["qty"]) for c in case["colours"]] == [("dark", 70), ("bright", 30)]


def test_the_colour_quantities_always_foot_to_the_case_quantity():
    """Proportional shares rounded independently miss by units — 70% and 30% of 101 is 70.7 + 30.3,
    which rounds to 71 + 30 = 101 by luck and to 70 + 30 = 100 at other totals. The largest-remainder
    pass puts the residue on the largest share, so the colour column FOOTS. This is the same rounding
    rule §61 uses for the accessory target split, for the same reason."""
    mix, _b = aop.resolve_case_mix(None)
    plan = aop.resolve_attach_plan(None)
    for total in range(0, 400):
        case = [r for r in aop.accessory_rows(total, mix=mix, plan=plan) if r["key"] == "case"][0]
        assert sum(c["qty"] for c in case["colours"]) == case["qty"], total


def test_an_accessory_rate_other_than_one_per_sale_is_honoured():
    """"1 per sale" is a DEFAULT, not a law: a tenant that puts two protectors in a bundle declares
    2.0 and the order follows."""
    mix, _b = aop.resolve_case_mix(None)
    plan = aop.resolve_attach_plan([{"key": "screen_protector", "label": "Screen protector",
                                     "per_device": 2}])
    rows = aop.accessory_rows(50, mix=mix, plan=plan)
    assert [(r["key"], r["qty"]) for r in rows] == [("screen_protector", 100)]


def test_no_projection_means_no_order_quantity_rather_than_zero():
    mix, _b = aop.resolve_case_mix(None)
    rows = aop.accessory_rows(None, mix=mix, plan=aop.resolve_attach_plan(None))
    assert all(r["qty"] is None for r in rows)
    assert all(c["qty"] is None for r in rows if r.get("colours") for c in r["colours"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE WHOLE PLAN — the payload the screen reads
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _plan(**kw):
    kw.setdefault("units_by_model", UNITS)
    kw.setdefault("months", MONTHS)
    kw.setdefault("lifecycle", CURRENT_AND_EOL)
    kw.setdefault("horizon_months", 2)
    return aop.plan(**kw)


def test_the_plan_orders_for_the_current_models_only():
    out = _plan()
    # 50/month iPhone + 10/month moto = 60/month x 2 months = 120 devices.
    assert out["projected_devices"] == 120
    by = {r["key"]: r["qty"] for r in out["accessories"]}
    assert by == {"case": 120, "screen_protector": 120, "cable_c_to_c": 120, "charging_cube": 120}


def test_the_plan_reports_what_it_could_not_plan_for():
    """The headline quantity is only as good as the rulings behind it, so the payload says how much
    of the measured demand is still waiting on a ruling and how much is being retired."""
    out = _plan(lifecycle={"IPHONE 16E BLK 128GB": "current"})
    assert out["models_awaiting_declaration"] == ["OLD HANDSET 32GB", "moto g play - 2026"]
    assert out["units_awaiting_declaration"] == 39
    assert out["declared_share"] == pytest.approx(150 / 189, abs=1e-6)
    assert out["projected_devices"] == 100


def test_a_plan_with_no_rulings_at_all_plans_nothing_and_says_why():
    out = _plan(lifecycle={})
    assert out["projected_devices"] is None
    assert all(r["qty"] is None for r in out["accessories"])
    assert out["reason"] == "awaiting_declaration"
    assert out["declared_share"] == 0.0


def test_every_model_retired_is_a_real_order_of_nothing():
    """Different from the test above: here the user HAS ruled, and ruled everything end of life. The
    order is zero because that is the decision, and the payload must not confuse it with silence."""
    out = _plan(lifecycle={m: "eol" for m in UNITS})
    assert out["projected_devices"] == 0
    assert all(r["qty"] == 0 for r in out["accessories"])
    assert out["reason"] is None
    assert out["declared_share"] == 1.0


def test_the_plan_states_its_window_and_its_horizon_on_every_payload():
    out = _plan()
    assert out["months"] == list(MONTHS)
    assert out["lookback_months"] == 3
    assert out["horizon_months"] == 2
    assert out["case_mix_basis"] == "house_default"


def test_the_plan_states_the_per_month_run_rate_behind_the_order():
    """A planner checking the number wants the monthly rate, not just the total — and the total must
    be the rate times the horizon, never a second derivation."""
    out = _plan()
    assert out["projected_devices_per_month"] == 60
    # DERIVED FROM THE TOTAL, never computed a second way: the order total has to foot to the model
    # rows above it, so the rate is that total divided by the horizon rather than a parallel sum.
    assert out["projected_devices_per_month"] == pytest.approx(
        out["projected_devices"] / out["horizon_months"])


def test_no_tenant_or_carrier_name_appears_in_the_module(tmp_path):
    """RULE TWO, as a test rather than a promise: the colour groups, the accessory keys and the
    lifecycle words are all generic, and the vocabulary is config."""
    import inspect
    src = inspect.getsource(aop).lower()
    for banned in ("boost", "luxelink", "cellfonz", "verizon", "t-mobile", "vzone", "apple",
                   "samsung", "otterbox"):
        assert banned not in src, banned
