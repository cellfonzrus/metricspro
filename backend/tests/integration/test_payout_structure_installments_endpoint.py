"""Integration: the Payout Structure endpoint must describe a plan's MULTI-MONTH schedule.

Real routing, the real endpoint body, over the in-memory database double (docs/TESTING.md — there is
no staging Supabase). What this covers that the unit tests cannot: that the ENDPOINT reads
`plan_installment_schedule` at all. The unit tests prove the English; this proves the wiring, which
is the half that was missing — `build_doc` could describe a schedule perfectly and still never be
handed one.

Two tenants of different shapes, because a feature is for every tenant (owner 2026-10-30): one with a
schedule and one without, and neither tenant's schedule may appear in the other's document.
"""
import pytest

ORG_A = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"     # a plan-engine tenant WITH a schedule
ORG_B = "11111111-2222-3333-4444-555555555555"     # a plan-engine tenant with none
PATH = "/api/v1/commcalc/commission-plans/payout-structure"

PLAN_A = {"id": "p-a", "org_id": ORG_A, "name": "Chicago Comp", "is_active": True}
PLAN_B = {"id": "p-b", "org_id": ORG_B, "name": "Other Tenant Comp", "is_active": True}
RULE_A = {"id": "r-a", "org_id": ORG_A, "plan_id": "p-a", "label": "activation",
          "match_field": "activation_bucket", "match_op": "in", "match_value": "premium,byod",
          "qualifies": True, "payout_kind": "flat_per_unit", "amount": 0.0, "pct": 0.0, "sort": 1}
RULE_B = {"id": "r-b", "org_id": ORG_B, "plan_id": "p-b", "label": "accessory",
          "match_field": "accessory", "match_op": "equals", "match_value": "yes",
          "qualifies": True, "payout_kind": "pct_price", "amount": 0.1, "pct": 0.1, "sort": 0}
SCHED_A = {"id": "s-a", "org_id": ORG_A, "plan_id": "p-a", "name": "3MR Commission Payment",
           "num_months": 3, "trigger_match_field": "activation_bucket", "trigger_match_op": "in",
           "trigger_match_value": "premium,byod", "gate_mode": "paid_residual",
           "gate_from_month": 2, "clawback_enabled": False, "effective_from": "2026-07-01",
           "effective_to": None, "eligible_sale_periods": [], "is_active": True}
LINES_A = [
    {"id": "l1", "org_id": ORG_A, "schedule_id": "s-a", "month_index": 1,
     "payout_kind": "pct_mrc", "mrc_pct": 0.05, "flat_amount": 0},
    {"id": "l2", "org_id": ORG_A, "schedule_id": "s-a", "month_index": 2,
     "payout_kind": "flat", "mrc_pct": 0, "flat_amount": 0},
    {"id": "l3", "org_id": ORG_A, "schedule_id": "s-a", "month_index": 3,
     "payout_kind": "pct_mrc", "mrc_pct": 0.13, "flat_amount": 0},
]


#: `engine_mode='plan'` is what puts a tenant on the Commission Plans engine (mig 303) — the
#: document for a Boost tenant comes from `build_boost_doc`, which has no plans and no schedules.
CARRIERS = [{"id": "c-a", "org_id": ORG_A, "name": "Total", "code": "TOTAL",
             "is_default": True, "engine_mode": "plan"},
            {"id": "c-b", "org_id": ORG_B, "name": "Total", "code": "TOTAL",
             "is_default": True, "engine_mode": "plan"}]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase)
    fake_supabase.seed("commcalc.carrier", CARRIERS)
    fake_supabase.seed("commcalc.commission_plan", [PLAN_A, PLAN_B])
    fake_supabase.seed("commcalc.commission_rule", [RULE_A, RULE_B])
    fake_supabase.seed("commcalc.plan_installment_schedule", [SCHED_A])
    fake_supabase.seed("commcalc.plan_installment_line", LINES_A)
    return fake_supabase


def _plan(body, name):
    return next(p for p in body["plans"] if p["name"] == name)


def test_the_route_is_registered(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


def test_the_endpoint_reads_the_schedule_and_puts_it_in_the_document(wired, api_client):
    r = api_client.get(PATH, params={"fmt": "json", "org_id": ORG_A})
    assert r.status_code == 200, r.text
    ins = _plan(r.json(), "Chicago Comp")["installments"]
    assert len(ins) == 1
    assert ins[0]["name"] == "3MR Commission Payment"
    assert [m["rate"] for m in ins[0]["months"]] == [
        "5% of the monthly plan charge", "$0.00", "13% of the monthly plan charge"]


def test_the_zero_activation_rule_no_longer_reads_as_earning_nothing(wired, api_client):
    body = api_client.get(PATH, params={"fmt": "json", "org_id": ORG_A}).json()
    whys = [i["why"] for i in _plan(body, "Chicago Comp")["no_pay_items"]]
    assert any("over several months" in w for w in whys)
    assert not any(w == "Currently set to zero — earns no incentive." for w in whys)


def test_another_tenants_schedule_never_appears_in_this_tenants_document(wired, api_client):
    body = api_client.get(PATH, params={"fmt": "json", "org_id": ORG_B}).json()
    assert _plan(body, "Other Tenant Comp")["installments"] == []
    assert all(p["name"] != "Chicago Comp" for p in body["plans"])


def test_a_database_without_migration_201_still_serves_the_document(monkeypatch, wired, api_client):
    """An unapplied optional migration must degrade to "no schedules", never 500 the handout."""
    def boom(*_a, **_k):
        raise RuntimeError("relation commcalc.plan_installment_schedule does not exist")
    monkeypatch.setattr(
        "app.modules.commcalc.sale_installment_engine._load_schedules", boom)
    r = api_client.get(PATH, params={"fmt": "json", "org_id": ORG_A})
    assert r.status_code == 200, r.text
    assert _plan(r.json(), "Chicago Comp")["installments"] == []
