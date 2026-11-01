"""Integration: the incentive-plan workflow endpoints, through the real app (§COMMCALC-67).

Owner, 2026-11-01: *"When we click on the plan it should show the one which is active and below it
should give the option to change the plan and when you click on change the plan it should show what
the available options are and then clicking on the available options the user should be able to
create a new plan — all of this should be a work flow than random data seen on the screen"*.

WHAT IS PROVED HERE, over the database double and the real routing:
  · the three steps come back in order, for EVERY tenant, each measured against its OWN rows;
  · the engine is each tenant's own declaration (`carrier.engine_mode`, mig 303) — a Boost-engine
    tenant is told it is paid by the built-in engine instead of being shown a plan it is not paid by;
  · one tenant's plans can never appear in another tenant's options (project directive 2026-10-30);
  · the draft a chosen option implies names an EXISTING door and creates a plan that is switched
    off with nobody assigned, so nothing here can move pay;
  · an unknown option is refused with a 400 rather than silently becoming a blank plan.
"""
import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"
LUXELINK = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
VZONE = "f4f1c16e-2acf-4221-854a-c29a605754a7"

WORKFLOW = "/api/v1/commcalc/incentive-plan/workflow"
DRAFT = "/api/v1/commcalc/incentive-plan/draft"
TENANTS = [HOUSE, LUXELINK, VZONE]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: fake_supabase)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    return fake_supabase


def _append(fake, key, rows):
    fake.seed(key, (fake.rows(key) or []) + rows)


def _seed_plan_tenant(fake, org_id, *, engine="plan", plan_name="Market plan", assigned=True,
                      rules=2, extra_plan=None):
    """A tenant paid by configurable plans: one carrier declaring the engine, one plan, its rules."""
    carrier_id = f"carrier-{org_id}"
    plan_id = f"plan-{org_id}"
    _append(fake, "commcalc.carrier", [{"id": carrier_id, "org_id": org_id, "name": "Carrier One",
                                        "code": "C1", "is_default": True, "engine_mode": engine,
                                        "template_shared": False}])
    _append(fake, "commcalc.commission_plan", [{"id": plan_id, "org_id": org_id, "name": plan_name,
                                                "carrier_id": carrier_id, "is_active": True}])
    _append(fake, "commcalc.commission_rule",
            [{"id": f"{plan_id}-r{i}", "org_id": org_id, "plan_id": plan_id, "label": f"rule {i}",
              "match_field": "any", "match_op": "equals", "payout_kind": "flat_per_unit",
              "amount": 10.0 + i, "pct": 0.0, "qualifies": True, "tiered": False, "sort": i}
             for i in range(rules)])
    if assigned:
        _append(fake, "commcalc.commission_plan_assignment",
                [{"id": f"{plan_id}-a0", "org_id": org_id, "plan_id": plan_id,
                  "scope": "market", "scope_value": "LENS"}])
    if extra_plan:
        _append(fake, "commcalc.commission_plan",
                [{"id": f"{plan_id}-x", "org_id": org_id, "name": extra_plan,
                  "carrier_id": carrier_id, "is_active": False}])
    return carrier_id, plan_id


def _steps(payload):
    return {s["key"]: s for s in payload["steps"]}


# ── the shape, for every tenant ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("org_id", TENANTS)
def test_the_three_steps_come_back_in_order_for_every_tenant(wired, api_client, org_id):
    _seed_plan_tenant(wired, org_id, plan_name=f"Plan of {org_id[:4]}")
    r = api_client.get(WORKFLOW, params={"org_id": org_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] is True
    assert [s["key"] for s in body["steps"]] == ["active", "change", "create"]
    step = _steps(body)
    assert step["active"]["plan"]["name"] == f"Plan of {org_id[:4]}"
    assert step["active"]["engine"] == "plan"
    assert step["change"]["option_count"] >= 2
    assert body["advanced_href"] == "/commcalc/commission-plans"


@pytest.mark.parametrize("org_id", TENANTS)
def test_a_boost_engine_tenant_is_told_its_plan_is_the_built_in_engine(wired, api_client, org_id):
    """Each tenant measured against its OWN declaration — no house vocabulary behind a default."""
    _seed_plan_tenant(wired, org_id, engine="boost")
    body = api_client.get(WORKFLOW, params={"org_id": org_id}).json()
    step = _steps(body)
    assert body["engine"] == "boost"
    assert step["active"]["built_in"] is True
    assert step["active"]["plan"] is None
    # and the honest caveat on every option it offers
    assert all((o.get("caveat") or "") for o in step["change"]["options"])


@pytest.mark.parametrize("org_id", TENANTS)
def test_a_tenant_with_a_plan_nobody_is_assigned_to_is_told_it_pays_nobody(wired, api_client, org_id):
    _seed_plan_tenant(wired, org_id, assigned=False)
    step = _steps(api_client.get(WORKFLOW, params={"org_id": org_id}).json())
    assert step["active"]["plan"] is None
    assert step["active"]["pays_nobody"] is True
    assert "$0" in step["active"]["headline"]


def test_a_tenant_with_no_rows_at_all_still_gets_a_usable_workflow(wired, api_client):
    body = api_client.get(WORKFLOW, params={"org_id": VZONE}).json()
    step = _steps(body)
    assert step["active"]["plan"] is None
    assert [o["kind"] for o in step["change"]["options"]] == ["blank"]


# ── no leakage between tenants ───────────────────────────────────────────────────────────────────
def test_one_tenants_plans_never_appear_in_another_tenants_options(wired, api_client):
    """A tenant-scoped read is where cross-tenant leakage lives (project directive 2026-10-30)."""
    _seed_plan_tenant(wired, HOUSE, plan_name="House only", extra_plan="House spare")
    _seed_plan_tenant(wired, LUXELINK, plan_name="Tenant only")
    step = _steps(api_client.get(WORKFLOW, params={"org_id": LUXELINK}).json())
    blob = str(step["change"]["options"]) + str(step["active"])
    assert "House" not in blob
    assert "Tenant only" in blob


def test_the_carrier_lens_narrows_the_plans_without_hiding_the_catch_all(wired, api_client):
    carrier_id, _ = _seed_plan_tenant(wired, LUXELINK, plan_name="Carrier plan")
    _append(wired, "commcalc.carrier", [{"id": "other-carrier", "org_id": LUXELINK,
                                         "name": "Carrier Two", "code": "C2",
                                         "is_default": False, "engine_mode": "plan"}])
    _append(wired, "commcalc.commission_plan",
            [{"id": "catch-all", "org_id": LUXELINK, "name": "Every carrier",
              "carrier_id": None, "is_active": True},
             {"id": "other-plan", "org_id": LUXELINK, "name": "Only carrier two",
              "carrier_id": "other-carrier", "is_active": True}])
    step = _steps(api_client.get(WORKFLOW, params={"org_id": LUXELINK,
                                                   "carrier_id": carrier_id}).json())
    labels = " ".join(o["label"] for o in step["change"]["options"])
    assert "Every carrier" in labels            # a NULL-carrier plan serves every carrier
    assert "Only carrier two" not in labels     # another carrier's plan is out of this lens


# ── the draft: which door, and what it creates ───────────────────────────────────────────────────
@pytest.mark.parametrize("org_id", TENANTS)
def test_copying_the_active_plan_drafts_an_inactive_unassigned_plan(wired, api_client, org_id):
    _seed_plan_tenant(wired, org_id, rules=3)
    r = api_client.get(DRAFT, params={"org_id": org_id, "option": "copy_active",
                                      "name": "Next year's plan"})
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["endpoint"] == "/api/v1/commcalc/commission-plans"
    body = draft["body"]
    assert body["name"] == "Next year's plan"
    assert body["is_active"] is False and body["assignments"] == []
    assert len(body["rules"]) == 3
    assert all("id" not in rule and "plan_id" not in rule for rule in body["rules"])


def test_the_drafted_plan_actually_saves_through_the_existing_endpoint(wired, api_client):
    """END TO END: the option the screen offers → the draft → the save that already existed. No
    second write path was created, and the saved plan is switched off."""
    _seed_plan_tenant(wired, LUXELINK, plan_name="Base plan", rules=2)
    draft = api_client.get(DRAFT, params={"org_id": LUXELINK, "option": "copy_active",
                                          "name": "Base plan v2"}).json()
    r = api_client.post(draft["endpoint"], params={"org_id": LUXELINK}, json=draft["body"])
    assert r.status_code == 200, r.text
    saved = [p for p in wired.rows("commcalc.commission_plan") if p.get("name") == "Base plan v2"]
    assert len(saved) == 1
    assert saved[0]["is_active"] is False
    assert saved[0]["org_id"] == LUXELINK
    copied = [x for x in wired.rows("commcalc.commission_rule")
              if x.get("plan_id") == saved[0]["id"]]
    assert len(copied) == 2
    # and the plan it was copied from is untouched
    assert any(p.get("name") == "Base plan" for p in wired.rows("commcalc.commission_plan"))


def test_an_unknown_option_is_refused_rather_than_creating_something(wired, api_client):
    _seed_plan_tenant(wired, HOUSE)
    for option in ("copy_plan:not-a-plan", "nonsense", "carrier_template:only-one-part"):
        r = api_client.get(DRAFT, params={"org_id": HOUSE, "option": option, "name": "x"})
        assert r.status_code == 400, (option, r.status_code, r.text)


def test_a_draft_with_no_name_is_refused(wired, api_client):
    _seed_plan_tenant(wired, HOUSE)
    r = api_client.get(DRAFT, params={"org_id": HOUSE, "option": "blank", "name": "  "})
    assert r.status_code == 400, r.text


def test_a_shared_carrier_template_is_offered_and_routes_to_the_cloner(wired, api_client):
    """The template option creates payout SCHEDULES, and `template_clone` is already the one home
    for that — the workflow names that door instead of growing a second importer."""
    _seed_plan_tenant(wired, LUXELINK, plan_name="Tenant plan")
    _append(wired, "commcalc.carrier",
            [{"id": "shared-carrier", "org_id": HOUSE, "name": "Shared Template",
              "code": "ST", "is_default": False, "template_shared": True}])
    _append(wired, "commcalc.payout_schedule",
            [{"id": "sched-1", "org_id": HOUSE, "carrier_id": "shared-carrier",
              "activation_type": "*", "is_active": True}])
    step = _steps(api_client.get(WORKFLOW, params={"org_id": LUXELINK}).json())
    tmpl = [o for o in step["change"]["options"] if o["kind"] == "carrier_template"]
    assert tmpl and tmpl[0]["creates"] == "schedules"
    draft = api_client.get(DRAFT, params={"org_id": LUXELINK, "option": tmpl[0]["key"],
                                          "name": "unused"}).json()
    assert draft["endpoint"] == "/api/v1/commcalc/carrier-template/clone"
    assert draft["body"]["source_carrier_id"] == "shared-carrier"


# ── the predicate the older hub now shares ───────────────────────────────────────────────────────
def test_the_payout_plans_hub_counts_the_same_live_plans_as_the_workflow(wired, api_client):
    """ONE FACT, ONE HOME: "is this plan live and does it serve this carrier" is `plan_workflow`'s,
    and the older hub dereferences it. Two surfaces disagreeing about which plan a tenant is on is
    the drift this wiring exists to prevent."""
    carrier_id, _ = _seed_plan_tenant(wired, LUXELINK, plan_name="Live plan",
                                      extra_plan="Switched off plan")
    hub = api_client.get("/api/v1/commcalc/payout-plans/overview",
                         params={"org_id": LUXELINK}).json()
    card = next(c for c in hub["carriers"] if c["id"] == carrier_id)
    wf = _steps(api_client.get(WORKFLOW, params={"org_id": LUXELINK,
                                                 "carrier_id": carrier_id}).json())
    live_in_workflow = 1 + len([o for o in wf["change"]["options"]
                                if o["kind"] == "copy_plan" and "switched off" not in o["label"]])
    assert card["plan_count"] == live_in_workflow
    assert card["assignment_count"] == wf["active"]["plan"]["assignment_count"]
