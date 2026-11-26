"""Unit: Employee Setup as a flow somebody can leave halfway (index §STOREOPS-13).

Six steps, in the order the work can actually be done in, ending at the onboarding pack that already
exists. Every step is a READ of state the platform already holds — this flow is a projection and a
set of doors, never a second place to type an employee's details.

WHAT THESE TESTS GUARD, beyond "the lamps are right":
  · ★ the ROLE vocabulary is a tenant's own words, so it is a REQUIRED argument with no house
    default behind it (owner 2026-10-30). A tenant that has declared no roles is TOLD so — it never
    silently accepts another tenant's role names;
  · a person a payroll run would price at $0.00 is named here, through the ONE home that already
    answers that question (`clock_variance.employees_without_rate`), never a second derivation;
  · login and app access are SKIPPABLE WITH A WARNING (owner's answer, 2026-11-25) — and the
    warning is attached to the onboarding step, so a person provisioned without a login is named
    rather than quietly finished.
"""
import pytest

from app.modules.core import guided_workflow as gw
from app.modules.storeops import employee_setup_flow as flow

ROLES_A = ("Sales Rep", "Store Manager", "District Manager")
ROLES_B = ("Associate", "Shift Lead")

DECL_A = {"roles": ROLES_A, "pays_incentive_plans": True, "hours_from_clock": True}
DECL_B = {"roles": ROLES_B, "pays_incentive_plans": False, "hours_from_clock": False}

FULL_EMP = {"employee_id": "E100", "name": "Ada Okafor", "home_store": "B-117",
            "role": "Sales Rep", "hire_date": "2026-11-01", "is_active": True,
            "pay_basis": "hourly", "pay_rate": 17.0}

FULL_FACTS = {
    "login": {"employee_id": "E100", "is_active": True, "role": "Sales Rep",
              "store_code": "B-117", "store_codes": None, "market": None,
              "must_reset_password": False, "last_login": "2026-11-20T10:00:00Z"},
    "plan_assignments": [{"plan_id": "p1", "plan_name": "Rep Tier 2026"}],
    "onboarding": {"workflow_status": "active", "mandatory_total": 4, "mandatory_done": 4,
                   "invited_at": "2026-11-02T00:00:00Z"},
}


def _blank_facts():
    return {"login": None, "plan_assignments": [], "onboarding": {}}


def _describe(emp=None, declared=None, facts=None):
    return flow.describe(emp if emp is not None else FULL_EMP,
                         declared or DECL_A,
                         facts if facts is not None else FULL_FACTS)


def _by_key(d):
    return {s["key"]: s for s in d["steps"]}


# ── the step list ────────────────────────────────────────────────────────────────────────────────
def test_the_six_steps_in_the_order_the_owner_confirmed():
    assert [s["key"] for s in flow.steps(DECL_A)] == [
        flow.STEP_IDENTITY, flow.STEP_PAY, flow.STEP_LOGIN,
        flow.STEP_ACCESS, flow.STEP_PLAN, flow.STEP_ONBOARDING]


def test_identity_pay_and_onboarding_are_required_login_access_and_plan_are_not():
    req = {s["key"]: s["required"] for s in flow.steps(DECL_A)}
    assert req[flow.STEP_IDENTITY] and req[flow.STEP_PAY] and req[flow.STEP_ONBOARDING]
    assert not req[flow.STEP_LOGIN] and not req[flow.STEP_ACCESS] and not req[flow.STEP_PLAN]


def test_every_step_points_at_the_screen_where_the_work_is_actually_done():
    for s in flow.steps(DECL_A):
        assert s["screen"].startswith("/"), f"{s['key']} must name an existing door"


def test_the_declared_steps_pass_the_shared_homes_own_validation():
    # declare() refuses a cycle, a duplicate and a forward dependency. Calling it IS the proof.
    assert len(flow.steps(DECL_A)) == 6
    assert len(flow.steps(DECL_B)) == 6


# ── the tenant's own words are a required argument (owner 2026-10-30) ───────────────────────────
def test_a_caller_that_has_not_asked_the_org_for_its_roles_cannot_answer():
    with pytest.raises(ValueError, match="roles"):
        flow.steps({"pays_incentive_plans": True, "hours_from_clock": True})
    with pytest.raises(ValueError, match="declar"):
        flow.steps(None)
    with pytest.raises(ValueError, match="roles"):
        flow.describe(FULL_EMP, {"pays_incentive_plans": False, "hours_from_clock": False},
                      FULL_FACTS)


def test_a_tenant_with_no_declared_roles_is_told_so_and_never_given_another_tenants_words():
    d = _describe(declared={"roles": (), "pays_incentive_plans": True, "hours_from_clock": True})
    ident = _by_key(d)[flow.STEP_IDENTITY]
    assert ident["status"] == gw.NEEDS_INPUT
    assert any("no roles" in r.lower() for r in ident["needs_input"]), ident["needs_input"]
    for reason in ident["needs_input"]:
        for word in ("sales rep", "district manager", "associate"):
            assert word not in reason.lower(), "no tenant's role words may appear by default"


def test_one_tenants_role_is_not_a_valid_role_in_another_tenant():
    emp = dict(FULL_EMP, role="Sales Rep")            # tenant A's word
    good = _by_key(_describe(emp, DECL_A))[flow.STEP_IDENTITY]
    leaked = _by_key(_describe(emp, DECL_B))[flow.STEP_IDENTITY]
    assert good["status"] == gw.DONE
    assert leaked["status"] == gw.NEEDS_INPUT
    assert any("Sales Rep" in r for r in leaked["needs_input"])


def test_a_role_is_matched_without_case_or_padding_tripping_it_up():
    emp = dict(FULL_EMP, role="  sales rep ")
    assert _by_key(_describe(emp, DECL_A))[flow.STEP_IDENTITY]["status"] == gw.DONE


# ── step 1 · who and where ──────────────────────────────────────────────────────────────────────
def test_a_brand_new_record_names_every_missing_piece_of_identity():
    emp = {"employee_id": "", "name": "", "home_store": "", "role": "", "is_active": True}
    ident = _by_key(_describe(emp, DECL_A, _blank_facts()))[flow.STEP_IDENTITY]
    assert ident["status"] == gw.NEEDS_INPUT
    joined = " ".join(ident["needs_input"]).lower()
    for want in ("name", "employee id", "home store", "role"):
        assert want in joined, f"{want!r} is not named: {ident['needs_input']}"


def test_a_name_alone_reads_as_started_not_as_nothing():
    emp = {"employee_id": "", "name": "Ada Okafor", "home_store": "", "role": "", "is_active": True}
    d = _describe(emp, DECL_A, _blank_facts())
    assert _by_key(d)[flow.STEP_IDENTITY]["status"] == gw.NEEDS_INPUT
    assert d["status"] == gw.NEEDS_INPUT
    assert d["resume"] == flow.STEP_IDENTITY


def test_a_hire_date_is_asked_for_but_does_not_hold_the_step_open():
    ident = _by_key(_describe(dict(FULL_EMP, hire_date=None)))[flow.STEP_IDENTITY]
    assert ident["status"] == gw.DONE
    assert any("hire date" in n.lower() for n in ident["detail"]["missing_optional"])


# ── step 2 · how they are paid ──────────────────────────────────────────────────────────────────
def test_an_hourly_person_with_no_rate_is_named_as_a_zero_dollar_payroll_risk():
    pay = _by_key(_describe(dict(FULL_EMP, pay_rate=None)))[flow.STEP_PAY]
    assert pay["status"] == gw.NEEDS_INPUT
    assert any("$0.00" in r for r in pay["needs_input"]), pay["needs_input"]


def test_a_zero_rate_counts_as_no_rate():
    pay = _by_key(_describe(dict(FULL_EMP, pay_rate=0)))[flow.STEP_PAY]
    assert pay["status"] == gw.NEEDS_INPUT


def test_that_verdict_comes_from_the_one_home_that_already_answers_it():
    # The $0.00 question has a home (clock_variance.employees_without_rate, index §14z). This flow
    # must ASK it, never re-derive it — so the two can never disagree.
    from app.modules.storeops import clock_variance as cv
    emp = dict(FULL_EMP, pay_rate=None)
    assert cv.employees_without_rate([emp]), "the home must agree there is a gap"
    assert _by_key(_describe(emp))[flow.STEP_PAY]["status"] == gw.NEEDS_INPUT
    assert cv.employees_without_rate([FULL_EMP]) == []
    assert _by_key(_describe(FULL_EMP))[flow.STEP_PAY]["status"] == gw.DONE


def test_a_salaried_person_needs_an_amount_and_not_an_hourly_rate():
    salaried = dict(FULL_EMP, pay_basis="salary", pay_rate=None, pay_amount=None)
    pay = _by_key(_describe(salaried))[flow.STEP_PAY]
    assert pay["status"] == gw.NEEDS_INPUT
    assert any("salary" in r.lower() for r in pay["needs_input"])
    ok = _by_key(_describe(dict(salaried, pay_amount=78000)))[flow.STEP_PAY]
    assert ok["status"] == gw.DONE
    assert "$0.00" not in " ".join(ok["needs_input"])


def test_a_blank_pay_basis_is_read_as_hourly_the_way_the_rest_of_the_platform_reads_it():
    pay = _by_key(_describe(dict(FULL_EMP, pay_basis="", pay_rate=17.0)))[flow.STEP_PAY]
    assert pay["status"] == gw.DONE
    assert pay["detail"]["pay_basis"] == "hourly"


def test_pay_cannot_be_reached_before_identity():
    emp = {"employee_id": "", "name": "", "home_store": "", "role": "", "is_active": True,
           "pay_basis": "hourly", "pay_rate": 17.0}
    d = _describe(emp, DECL_A, _blank_facts())
    assert _by_key(d)[flow.STEP_PAY]["blocked_by"] == (flow.STEP_IDENTITY,)
    assert d["resume"] == flow.STEP_IDENTITY


# ── step 3 · login and the time clock ───────────────────────────────────────────────────────────
def test_no_login_means_they_cannot_clock_in_and_the_step_says_exactly_that():
    d = _describe(FULL_EMP, DECL_A, dict(FULL_FACTS, login=None))
    login = _by_key(d)[flow.STEP_LOGIN]
    assert login["status"] == gw.NEEDS_INPUT
    assert any("no login" in r.lower() for r in login["needs_input"])
    assert any("clock in" in r.lower() for r in login["needs_input"])


def test_when_the_tenant_pays_from_the_schedule_the_clock_sentence_is_not_used():
    d = _describe(FULL_EMP, DECL_B, dict(FULL_FACTS, login=None))
    login = _by_key(d)[flow.STEP_LOGIN]
    assert not any("clock in" in r.lower() for r in login["needs_input"])
    assert login["detail"]["hours_from_clock"] is False


def test_a_deactivated_login_is_not_a_login():
    d = _describe(FULL_EMP, DECL_A, dict(FULL_FACTS, login=dict(FULL_FACTS["login"], is_active=False)))
    assert _by_key(d)[flow.STEP_LOGIN]["status"] == gw.NEEDS_INPUT


def test_a_login_that_has_never_been_used_reads_in_progress_and_says_the_invite_is_unopened():
    login_row = dict(FULL_FACTS["login"], last_login=None, must_reset_password=True)
    d = _describe(FULL_EMP, DECL_A, dict(FULL_FACTS, login=login_row))
    login = _by_key(d)[flow.STEP_LOGIN]
    # It is waiting on the EMPLOYEE, so it is not "needs your input" — the reason is still stated.
    assert login["status"] == gw.IN_PROGRESS
    assert login["needs_input"] == ()
    assert any("not signed in" in r.lower() for r in login["detail"]["waiting_on"])


# ── step 4 · what they can open ─────────────────────────────────────────────────────────────────
def test_a_login_with_no_store_and_no_market_would_see_nothing_and_is_named():
    login_row = dict(FULL_FACTS["login"], store_code=None, store_codes=None, market=None)
    d = _describe(FULL_EMP, DECL_A, dict(FULL_FACTS, login=login_row))
    access = _by_key(d)[flow.STEP_ACCESS]
    assert access["status"] == gw.NEEDS_INPUT
    assert any("store" in r.lower() for r in access["needs_input"])


def test_access_cannot_be_judged_before_there_is_a_login_at_all():
    d = _describe(FULL_EMP, DECL_A, dict(FULL_FACTS, login=None))
    assert _by_key(d)[flow.STEP_ACCESS]["blocked_by"] == (flow.STEP_LOGIN,)


def test_a_store_pin_through_the_list_column_counts_as_a_pin():
    login_row = dict(FULL_FACTS["login"], store_code=None, store_codes=["B-117", "B-118"])
    d = _describe(FULL_EMP, DECL_A, dict(FULL_FACTS, login=login_row))
    assert _by_key(d)[flow.STEP_ACCESS]["status"] == gw.DONE


def test_the_login_role_disagreeing_with_the_employee_role_is_reported_not_silently_accepted():
    login_row = dict(FULL_FACTS["login"], role="District Manager")
    d = _describe(FULL_EMP, DECL_A, dict(FULL_FACTS, login=login_row))
    access = _by_key(d)[flow.STEP_ACCESS]
    assert access["status"] == gw.NEEDS_INPUT
    assert any("District Manager" in r and "Sales Rep" in r for r in access["needs_input"])


# ── step 5 · incentive plan ─────────────────────────────────────────────────────────────────────
def test_a_tenant_that_pays_no_incentive_plans_has_the_step_skipped_with_the_reason():
    plan = _by_key(_describe(FULL_EMP, DECL_B, FULL_FACTS))[flow.STEP_PLAN]
    assert plan["status"] == gw.SKIPPED
    assert plan["detail"]["why"]
    assert plan["status"] != gw.DONE, "skipped is never rendered as done"


def test_a_tenant_that_pays_plans_but_has_assigned_none_to_this_person_reads_not_started():
    plan = _by_key(_describe(FULL_EMP, DECL_A, dict(FULL_FACTS, plan_assignments=[])))[flow.STEP_PLAN]
    assert plan["status"] == gw.NOT_STARTED


def test_an_assigned_plan_is_named_on_the_step():
    plan = _by_key(_describe())[flow.STEP_PLAN]
    assert plan["status"] == gw.DONE
    assert plan["detail"]["plans"] == ["Rep Tier 2026"]


# ── step 6 · the onboarding pack (the flow this one leads up to) ────────────────────────────────
def test_nothing_sent_yet_reads_not_started():
    ob = _by_key(_describe(FULL_EMP, DECL_A, _blank_facts()))[flow.STEP_ONBOARDING]
    assert ob["status"] == gw.NOT_STARTED


def test_documents_outstanding_name_how_many():
    facts = dict(FULL_FACTS, onboarding={"workflow_status": "in_progress", "mandatory_total": 4,
                                         "mandatory_done": 1, "invited_at": "2026-11-02T00:00:00Z"})
    ob = _by_key(_describe(FULL_EMP, DECL_A, facts))[flow.STEP_ONBOARDING]
    assert ob["status"] == gw.NEEDS_INPUT
    assert any("3 of 4" in r for r in ob["needs_input"]), ob["needs_input"]


def test_only_provisioned_or_active_closes_the_onboarding_step():
    for st in ("invited", "in_progress", "docs_submitted", "docs_verified"):
        facts = dict(FULL_FACTS, onboarding={"workflow_status": st, "mandatory_total": 4,
                                             "mandatory_done": 4})
        ob = _by_key(_describe(FULL_EMP, DECL_A, facts))[flow.STEP_ONBOARDING]
        assert ob["status"] != gw.DONE, st
    for st in ("provisioned", "active"):
        facts = dict(FULL_FACTS, onboarding={"workflow_status": st, "mandatory_total": 4,
                                             "mandatory_done": 4})
        assert _by_key(_describe(FULL_EMP, DECL_A, facts))[flow.STEP_ONBOARDING]["status"] == gw.DONE


def test_an_unknown_onboarding_word_is_reported_rather_than_guessed():
    facts = dict(FULL_FACTS, onboarding={"workflow_status": "halfway", "mandatory_total": 0,
                                         "mandatory_done": 0})
    ob = _by_key(_describe(FULL_EMP, DECL_A, facts))[flow.STEP_ONBOARDING]
    assert ob["status"] == gw.NEEDS_INPUT
    assert any("halfway" in r for r in ob["needs_input"])


def test_the_onboarding_words_come_from_their_own_home_and_are_not_a_second_copy():
    # The six stage words have ONE definition (hr/onboarding_stages, pure). That hr/router
    # dereferences the same objects rather than keeping a copy is proved at the HTTP boundary, in
    # tests/integration/test_employee_setup_endpoint.py, where the framework is importable.
    from app.modules.hr import onboarding_stages as stages
    assert flow.ONBOARDING_STATUSES == tuple(stages.WORKFLOW_STATUSES)
    assert flow.ONBOARDING_SETTLED == tuple(stages.SETTLED_STATUSES)


# ── skippable WITH A WARNING — the owner's answer, proved ───────────────────────────────────────
def test_a_person_provisioned_with_no_login_is_finished_but_the_warning_is_named():
    facts = dict(FULL_FACTS, login=None,
                 onboarding={"workflow_status": "provisioned", "mandatory_total": 4,
                             "mandatory_done": 4})
    d = _describe(FULL_EMP, DECL_A, facts)
    assert d["status"] == gw.DONE, "login is skippable — it does not block finishing"
    warn = _by_key(d)[flow.STEP_ONBOARDING]["detail"]["warnings"]
    assert any("login" in w.lower() for w in warn), warn


def test_the_warning_is_absent_once_the_login_exists():
    d = _describe()
    assert _by_key(d)[flow.STEP_ONBOARDING]["detail"]["warnings"] == []


# ── the whole flow ──────────────────────────────────────────────────────────────────────────────
def test_a_fully_set_up_person_reads_done_with_no_resume():
    d = _describe()
    assert d["status"] == gw.DONE and d["resume"] is None
    assert d["progress"] == {"total": 6, "done": 6, "required_total": 3, "required_done": 3}


def test_a_blank_record_resumes_at_step_one():
    emp = {"employee_id": "E999", "name": "", "home_store": "", "role": "", "is_active": True}
    d = _describe(emp, DECL_A, _blank_facts())
    assert d["resume"] == flow.STEP_IDENTITY
    assert d["progress"]["done"] == 0


def test_the_flow_is_a_projection_and_a_set_of_doors_never_a_second_writer():
    import inspect
    src = inspect.getsource(flow)
    for banned in ("insert(", "update(", "upsert(", "delete(", "get_supabase", "fastapi"):
        assert banned not in src, f"{banned!r} must not appear — this flow only reads"


def test_the_flow_names_no_tenant_no_carrier_and_no_role_of_its_own():
    import inspect
    src = inspect.getsource(flow).lower()
    for banned in ("cellfonz", "luxelink", "vzone", "ny logistics", "boost", "sales rep",
                   "district manager", "store manager"):
        assert banned not in src, f"{banned!r} is a tenant's own word (RULE TWO)"


def test_an_inactive_employee_is_still_describable_and_says_so():
    d = _describe(dict(FULL_EMP, is_active=False))
    assert d["status"] in gw.STATUSES
    assert _by_key(d)[flow.STEP_IDENTITY]["detail"]["is_active"] is False


# ── every tenant shape, not just the house one (owner 2026-10-30) ───────────────────────────────
@pytest.mark.parametrize("declared", [DECL_A, DECL_B,
                                      {"roles": ("Operator",), "pays_incentive_plans": True,
                                       "hours_from_clock": False},
                                      {"roles": ("Rep", "Lead", "GM"),
                                       "pays_incentive_plans": False, "hours_from_clock": True}])
def test_every_tenant_shape_produces_a_complete_answer(declared):
    emp = dict(FULL_EMP, role=declared["roles"][0])
    d = flow.describe(emp, declared, FULL_FACTS)
    assert len(d["steps"]) == 6
    assert all(s["status"] in gw.STATUSES for s in d["steps"])
    assert d["status"] in gw.STATUSES
    blank = flow.describe({"employee_id": "X", "name": "", "home_store": "", "role": "",
                           "is_active": True}, declared, _blank_facts())
    assert blank["resume"] == flow.STEP_IDENTITY
