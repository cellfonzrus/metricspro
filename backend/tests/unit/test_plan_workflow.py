"""Unit: the incentive-plan WORKFLOW — "what do I pay today, and what can replace it?" (§COMMCALC-54).

Owner, 2026-11-01: *"the plan which is active in incentive payout when you click on it it takes you
to the page which has all kinds of Incentive structure listed which becomes confusing even to me ...
When we click on the plan it should show the one which is active and below it should give the option
to change the plan and when you click on change the plan it should show what the available options
are and then clicking on the available options the user should be able to create a new plan — all of
this should be a work flow than random data seen on the screen"*.

THE CLASS, not the instance. The confusing screen is a SYMPTOM: there was no answer anywhere to
"which plan am I on?". Every surface showed the operator the raw table and left the question to them
— `payout_plans_overview` counts plans per carrier, the editor lists them all, the diagnostic lists
them again. `plan_workflow` is the ONE home for that question and for the ordered steps that follow
it; the screen renders the steps it is handed and decides nothing.

PURE: no DB, no client, no org constant. Every tenant's answer comes from that tenant's own rows,
and the engine is a REQUIRED argument — a caller that has not resolved which engine pays this tenant
must not be able to answer (project directive 2026-10-30).
"""
import pytest

from app.modules.commcalc import plan_workflow as pw

CARRIER_A = "11111111-1111-1111-1111-111111111111"
CARRIER_B = "22222222-2222-2222-2222-222222222222"


def _plan(pid, name, *, carrier=CARRIER_A, active=True, rules=0, tiers=0, assigns=()):
    return {
        "id": pid, "name": name, "carrier_id": carrier, "is_active": active,
        "rules": [{"label": f"r{i}", "match_field": "any", "match_op": "equals",
                   "match_value": None, "payout_kind": "flat_per_unit", "amount": 10.0 + i,
                   "pct": 0.0, "qualifies": True, "tiered": False, "sort": i}
                  for i in range(rules)],
        "tiers": [{"min_count": i * 10, "multiplier": 1.0 + i} for i in range(tiers)],
        "assignments": [{"id": f"{pid}-a{i}", "plan_id": pid, "scope": s, "scope_value": v}
                        for i, (s, v) in enumerate(assigns)],
    }


# ── the two predicates the whole platform must share ────────────────────────────────────────────
def test_a_plan_with_no_is_active_column_counts_as_live():
    """`is_active` defaults TRUE on the table, so an absent key is live — not off."""
    assert pw.plan_is_live({"id": "p"}) is True
    assert pw.plan_is_live({"id": "p", "is_active": True}) is True
    assert pw.plan_is_live({"id": "p", "is_active": False}) is False


def test_a_carrier_less_plan_serves_every_carrier():
    """A NULL carrier_id plan is the tenant's catch-all; it must not vanish under a carrier lens."""
    assert pw.plan_serves_carrier({"carrier_id": None}, CARRIER_A) is True
    assert pw.plan_serves_carrier({"carrier_id": CARRIER_A}, CARRIER_A) is True
    assert pw.plan_serves_carrier({"carrier_id": CARRIER_B}, CARRIER_A) is False
    # no lens asked for => every plan is in scope
    assert pw.plan_serves_carrier({"carrier_id": CARRIER_B}, None) is True


# ── step 1: WHICH PLAN AM I ON ───────────────────────────────────────────────────────────────────
def test_the_active_plan_is_the_live_one_that_actually_pays_somebody():
    plans = [
        _plan("p1", "Unassigned draft", rules=3),
        _plan("p2", "NY team", rules=2, assigns=(("market", "NY"), ("store", "B-1"))),
        _plan("p3", "Switched off", active=False, rules=9, assigns=(("market", "NY"),)),
    ]
    got = pw.active_plan("plan", plans, carrier_id=CARRIER_A)
    assert got["id"] == "p2"
    assert got["assignment_count"] == 2
    assert got["rule_count"] == 2
    assert [c["scope"] for c in got["covers"]] == ["market", "store"]


def test_nothing_assigned_means_the_surface_says_so_rather_than_picking_one():
    """Zero is a plausible-looking answer, which is why this class hides. An unassigned plan is NOT
    what the tenant pays — the step reports that nobody is covered instead of guessing."""
    plans = [_plan("p1", "Draft", rules=3)]
    got = pw.active_plan("plan", plans, carrier_id=CARRIER_A)
    assert got is None


def test_a_tenant_with_no_plan_rows_at_all_gets_no_active_plan():
    assert pw.active_plan("plan", [], carrier_id=CARRIER_A) is None


def test_the_engine_is_a_required_argument():
    """A caller that has not asked which engine pays this tenant must not be able to answer."""
    with pytest.raises(TypeError):
        pw.active_plan(plans=[])          # type: ignore[call-arg]


def test_a_boost_engine_tenant_is_told_its_plan_is_the_built_in_one():
    """A Boost-engine tenant holds ZERO commission_plan rows. The honest answer is the built-in
    KPI-tier engine, named — never "no plan", which reads as "nothing pays"."""
    step = pw.active_step("boost", [], carrier_id=CARRIER_A)
    assert step["engine"] == "boost"
    assert step["plan"] is None
    assert step["built_in"] is True
    assert "KPI" in step["headline"]
    assert step["can_change"] is True


def test_the_plan_engine_step_names_the_plan_and_where_to_see_its_rates():
    plans = [_plan("p2", "NY team", rules=2, assigns=(("market", "NY"),))]
    step = pw.active_step("plan", plans, carrier_id=CARRIER_A)
    assert step["engine"] == "plan"
    assert step["built_in"] is False
    assert step["plan"]["name"] == "NY team"
    assert "NY team" in step["headline"]


def test_a_plan_engine_tenant_with_nothing_assigned_is_told_it_pays_nobody():
    step = pw.active_step("plan", [_plan("p1", "Draft", rules=1)], carrier_id=CARRIER_A)
    assert step["plan"] is None
    assert step["pays_nobody"] is True
    assert "no" in step["headline"].lower()


# ── step 2/3: WHAT CAN REPLACE IT ────────────────────────────────────────────────────────────────
def _templates():
    return [{"source_org_id": "org-x", "source_carrier_id": "car-x", "carrier_name": "Shared one",
             "carrier_code": "SH", "is_own": False, "schedule_count": 2, "line_count": 8,
             "product_mrc_count": 4}]


def test_every_option_kind_is_offered_and_each_one_is_declared():
    plans = [
        _plan("p2", "NY team", rules=2, assigns=(("market", "NY"),)),
        _plan("p1", "Chicago draft", rules=4),
    ]
    opts = pw.options("plan", plans, templates=_templates(), carrier_id=CARRIER_A)
    kinds = [o["kind"] for o in opts]
    assert set(kinds) <= set(pw.OPTION_KINDS)
    assert "copy_active" in kinds and "copy_plan" in kinds
    assert "carrier_template" in kinds and "blank" in kinds
    # every option is self-describing: the screen renders these, it invents no labels
    for o in opts:
        assert o["key"] and o["label"] and o["detail"]
        assert o["creates"] in ("plan", "schedules")


def test_the_active_plan_is_not_offered_twice():
    plans = [_plan("p2", "NY team", rules=2, assigns=(("market", "NY"),))]
    opts = pw.options("plan", plans, templates=[], carrier_id=CARRIER_A)
    copies = [o for o in opts if o["kind"] == "copy_plan"]
    assert copies == []
    assert [o["source_plan_id"] for o in opts if o["kind"] == "copy_active"] == ["p2"]


def test_an_inactive_plan_is_still_offered_as_a_starting_point():
    """Switched off is not deleted — its shape is a perfectly good place to start from."""
    plans = [_plan("p9", "Last year's plan", active=False, rules=5)]
    opts = pw.options("plan", plans, templates=[], carrier_id=CARRIER_A)
    assert any(o["kind"] == "copy_plan" and o["source_plan_id"] == "p9" for o in opts)


def test_a_boost_tenant_is_warned_that_a_new_plan_does_not_pay_until_the_engine_changes():
    opts = pw.options("boost", [], templates=[], carrier_id=CARRIER_A)
    assert [o["kind"] for o in opts if o["kind"] == "blank"] == ["blank"]
    assert any("engine" in (o.get("caveat") or "").lower() for o in opts)


def test_no_template_shared_anywhere_simply_omits_that_option():
    opts = pw.options("plan", [], templates=[], carrier_id=CARRIER_A)
    assert [o for o in opts if o["kind"] == "carrier_template"] == []


def test_one_tenants_plans_never_appear_as_another_tenants_option():
    """The module holds no org at all — it can only answer from the rows it was handed, which is the
    structural half of the no-leak rule (project directive 2026-10-30)."""
    import inspect
    src = inspect.getsource(pw)
    assert "00000000-0000-0000-0000-000000000001" not in src
    assert "org_id" not in src.replace("source_org_id", "")


# ── step 4: CREATE IT ────────────────────────────────────────────────────────────────────────────
def test_a_copy_carries_the_rules_and_tiers_over_but_never_the_assignments():
    """A new plan must not start paying the moment it is saved. It is created INACTIVE with nobody
    assigned, so pay moves only when the operator assigns it and recalculates."""
    plans = [_plan("p2", "NY team", rules=3, tiers=2, assigns=(("market", "NY"),))]
    draft = pw.draft_from_option("copy_active", plans=plans, name="NY team v2",
                                 carrier_id=CARRIER_A)
    assert draft["endpoint"] == "/api/v1/commcalc/commission-plans"
    body = draft["body"]
    assert body["name"] == "NY team v2"
    assert body["is_active"] is False
    assert body["assignments"] == []
    assert len(body["rules"]) == 3 and len(body["tiers"]) == 2
    assert body["carrier_id"] == CARRIER_A
    # the copy carries no identity of the plan it came from, or the save would EDIT that plan
    assert "id" not in body
    for r in body["rules"]:
        assert "id" not in r and "plan_id" not in r


def test_copying_a_named_plan_reads_that_plan_and_not_the_active_one():
    plans = [
        _plan("p2", "NY team", rules=3, assigns=(("market", "NY"),)),
        _plan("p1", "Chicago", rules=7),
    ]
    body = pw.draft_from_option("copy_plan:p1", plans=plans, name="Chicago v2",
                                carrier_id=CARRIER_A)["body"]
    assert len(body["rules"]) == 7


def test_a_blank_plan_is_a_plan_with_no_rules():
    body = pw.draft_from_option("blank", plans=[], name="Fresh", carrier_id=CARRIER_A)["body"]
    assert body["rules"] == [] and body["tiers"] == [] and body["assignments"] == []
    assert body["is_active"] is False


def test_a_template_option_routes_to_the_cloner_and_not_to_the_plan_save():
    """The carrier template creates payout SCHEDULES, and the platform already has one home for
    that (`template_clone`). The draft says which door it belongs to so no caller has to decide."""
    draft = pw.draft_from_option("carrier_template:org-x:car-x", plans=[], name="ignored",
                                 carrier_id=CARRIER_A)
    assert draft["endpoint"] == "/api/v1/commcalc/carrier-template/clone"
    assert draft["body"] == {"source_org_id": "org-x", "source_carrier_id": "car-x",
                             "dry_run": False}


def test_an_unknown_option_is_refused_rather_than_silently_becoming_a_blank_plan():
    with pytest.raises(ValueError):
        pw.draft_from_option("copy_plan:does-not-exist", plans=[], name="x", carrier_id=None)
    with pytest.raises(ValueError):
        pw.draft_from_option("something_else", plans=[], name="x", carrier_id=None)


def test_a_draft_needs_a_name():
    with pytest.raises(ValueError):
        pw.draft_from_option("blank", plans=[], name="   ", carrier_id=None)


# ── the whole workflow, assembled ────────────────────────────────────────────────────────────────
def test_the_workflow_is_an_ordered_list_of_steps_the_screen_just_renders():
    plans = [_plan("p2", "NY team", rules=2, assigns=(("market", "NY"),)),
             _plan("p1", "Chicago", rules=4)]
    wf = pw.workflow("plan", plans, templates=_templates(), carrier_id=CARRIER_A)
    assert [s["key"] for s in wf["steps"]] == list(pw.STEP_KEYS)
    assert wf["steps"][0]["plan"]["name"] == "NY team"
    assert len(wf["steps"][1]["options"]) >= 3
    assert wf["engine"] == "plan"
