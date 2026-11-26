"""Integration: the Employee Setup workflow endpoints through the REAL app (index §STOREOPS-14).

Real routing, real middleware, the real endpoint bodies — over the in-memory database double, with
conftest blocking every outbound socket. "Integration" here means the app, not a real database
(docs/TESTING.md): there is no staging Supabase.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT:
  · the two routes are reachable at their registered paths, and neither writes anything;
  · ★ an organisation is measured against ITS OWN declared roles, and one tenant's role words never
    reach another tenant's board — at the HTTP boundary, where cross-tenant leakage actually lives
    (owner's 2026-10-30 rule, and the security pass it asks for);
  · an organisation that has declared NO roles is told so, rather than shown a clean board;
  · active-only is the default and the number hidden is STATED (§CORE-45);
  · the standard filters narrow the board (the 2026-10-30 report rule);
  · `hr/router` dereferences the stage words rather than keeping a copy — provable only where the
    framework is importable;
  · a database missing a table degrades by name in `blockers` instead of 500-ing.
"""
import pytest

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
BOARD = "/api/v1/storeops/employee-setup"

ROLES_A = [{"org_id": ORG_A, "name": "sales_rep", "display_name": "Sales Rep", "permissions": {}},
           {"org_id": ORG_A, "name": "dm", "display_name": "District Manager", "permissions": {}}]
ROLES_B = [{"org_id": ORG_B, "name": "associate", "display_name": "Associate", "permissions": {}}]

EMP_A = {"id": 1, "org_id": ORG_A, "employee_id": "E100", "name": "Ada Okafor",
         "home_store": "B-117", "role": "Sales Rep", "is_active": True, "email": "ada@x.test",
         "phone": "555-0100", "hire_date": "2026-11-01", "pay_rate": 17.0,
         "pay_basis": "hourly", "pay_amount": None, "epay_salesperson": "AOKAFOR",
         "pay_from_schedule": None}
EMP_A_BARE = {"id": 2, "org_id": ORG_A, "employee_id": "E200", "name": "Ben Ruiz",
              "home_store": "", "role": "", "is_active": True, "email": None, "phone": None,
              "hire_date": None, "pay_rate": None, "pay_basis": "hourly", "pay_amount": None,
              "epay_salesperson": None, "pay_from_schedule": None}
EMP_A_GONE = {"id": 3, "org_id": ORG_A, "employee_id": "E300", "name": "Cleo Vance",
              "home_store": "B-118", "role": "Sales Rep", "is_active": False, "email": None,
              "phone": None, "hire_date": None, "pay_rate": 18.0, "pay_basis": "hourly",
              "pay_amount": None, "epay_salesperson": None, "pay_from_schedule": None}
EMP_B = {"id": 4, "org_id": ORG_B, "employee_id": "L900", "name": "Dara Lin",
         "home_store": "L-1", "role": "Associate", "is_active": True, "email": None, "phone": None,
         "hire_date": None, "pay_rate": 16.0, "pay_basis": "hourly", "pay_amount": None,
         "epay_salesperson": None, "pay_from_schedule": None}

LOGIN_A = {"org_id": ORG_A, "employee_id": "E100", "email": "ada@x.test", "role": "Sales Rep",
           "market": None, "store_code": "B-117", "store_codes": None, "is_active": True,
           "must_reset_password": False, "last_login": "2026-11-20T10:00:00Z"}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.storeops.employee_setup_api.get_supabase",
                        lambda: fake_supabase)
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase)
    fake_supabase.seed("storeops.roles", ROLES_A + ROLES_B)
    fake_supabase.seed("storeops.employees", [EMP_A, EMP_A_BARE, EMP_A_GONE, EMP_B])
    fake_supabase.seed("storeops.app_users", [LOGIN_A])
    fake_supabase.seed("storeops.employee_onboarding_profile", [
        {"org_id": ORG_A, "employee_id": "E100", "workflow_status": "active",
         "started_at": None, "completed_at": None}])
    fake_supabase.seed("storeops.onboarding_task", [
        {"id": "t1", "org_id": ORG_A, "is_mandatory": True, "is_active": True},
        {"id": "t2", "org_id": ORG_A, "is_mandatory": True, "is_active": True}])
    fake_supabase.seed("storeops.employee_onboarding", [
        {"org_id": ORG_A, "employee_id": "E100", "task_id": "t1", "status": "verified"},
        {"org_id": ORG_A, "employee_id": "E100", "task_id": "t2", "status": "verified"}])
    fake_supabase.seed("commcalc.commission_plan", [])
    return fake_supabase


def _board(api_client, org=ORG_A, **params):
    q = "&".join([f"org_id={org}"] + [f"{k}={v}" for k, v in params.items()])
    r = api_client.get(f"{BOARD}?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def _row(board, employee_id):
    for r in board["rows"]:
        if r["employee_id"] == employee_id:
            return r
    raise AssertionError(f"{employee_id} not on the board: "
                         f"{[r['employee_id'] for r in board['rows']]}")


# ── the board is reachable and shaped ───────────────────────────────────────────────────────────
def test_the_board_lists_this_organisations_people_with_a_resume_step_each(wired, api_client):
    b = _board(api_client)
    assert [s["key"] for s in b["steps"]] == ["identity", "pay", "login", "access", "plan",
                                              "onboarding"]
    assert all(s["screen"].startswith("/") for s in b["steps"])
    ada, ben = _row(b, "E100"), _row(b, "E200")
    assert ada["status"] == "done" and ada["resume"] is None
    assert ben["status"] == "needs_input" and ben["resume"] == "identity"


def test_the_board_summarises_where_people_are_getting_stuck(wired, api_client):
    s = _board(api_client)["summary"]
    assert s["total"] == 2 and s["finished"] == 1 and s["open"] == 1
    assert s["by_status"]["needs_input"] == 1
    assert s["stuck_at"][0]["key"] == "identity"


def test_every_status_word_is_published_so_the_screen_invents_none(wired, api_client):
    b = _board(api_client)
    assert [s["key"] for s in b["statuses"]] == ["not_started", "in_progress", "needs_input",
                                                 "skipped", "done"]
    assert all(s["label"] for s in b["statuses"])


# ── active-only by default, and hiding is never silent (§CORE-45) ───────────────────────────────
def test_an_inactive_person_is_hidden_by_default_and_the_count_is_stated(wired, api_client):
    b = _board(api_client)
    assert {r["employee_id"] for r in b["rows"]} == {"E100", "E200"}
    assert b["inactive_hidden"] == 1


def test_show_inactive_brings_them_back_under_the_one_spelling(wired, api_client):
    b = _board(api_client, include_inactive="true")
    assert "E300" in {r["employee_id"] for r in b["rows"]}
    assert _row(b, "E300")["is_active"] is False


# ── ★ the tenant rule: one organisation's words never reach another ─────────────────────────────
def test_each_organisation_is_measured_against_its_own_declared_roles(wired, api_client):
    a, b = _board(api_client, org=ORG_A), _board(api_client, org=ORG_B)
    assert a["declared"]["roles"] == ["Sales Rep", "District Manager"]
    assert b["declared"]["roles"] == ["Associate"]
    assert _row(a, "E100")["status"] == "done"
    dara = _row(b, "L900")
    ident = {s["key"]: s for s in dara["steps"]}["identity"]
    assert ident["status"] == "done", "its own declared role is accepted"


def test_one_tenants_people_never_appear_on_anothers_board(wired, api_client):
    assert {r["employee_id"] for r in _board(api_client, org=ORG_A)["rows"]} == {"E100", "E200"}
    assert {r["employee_id"] for r in _board(api_client, org=ORG_B)["rows"]} == {"L900"}


def test_a_role_declared_by_one_tenant_is_not_valid_in_the_other(wired, api_client, fake_supabase):
    # Give tenant B a person carrying tenant A's role word. It must be REPORTED, not accepted.
    fake_supabase.seed("storeops.employees", [EMP_A, EMP_A_BARE, EMP_A_GONE, EMP_B,
                                              dict(EMP_B, id=5, employee_id="L901",
                                                   name="Eli Shah", role="Sales Rep")])
    b = _board(api_client, org=ORG_B)
    ident = {s["key"]: s for s in _row(b, "L901")["steps"]}["identity"]
    assert ident["status"] == "needs_input"
    assert any("Sales Rep" in r for r in ident["needs_input"])


def test_an_organisation_that_has_declared_no_roles_is_told_so(wired, api_client, fake_supabase):
    fake_supabase.seed("storeops.roles", ROLES_A)          # tenant B declares none
    b = _board(api_client, org=ORG_B)
    assert b["declared"]["roles"] == []
    ident = {s["key"]: s for s in _row(b, "L900")["steps"]}["identity"]
    assert ident["status"] == "needs_input"
    assert any("no roles" in r.lower() for r in ident["needs_input"])
    joined = " ".join(ident["needs_input"]).lower()
    assert "sales rep" not in joined and "district manager" not in joined


# ── the standard filters (report rule, 2026-10-30) ──────────────────────────────────────────────
def test_the_store_filter_narrows_the_board(wired, api_client):
    assert {r["employee_id"] for r in _board(api_client, stores="B-117")["rows"]} == {"E100"}


def test_the_person_filter_narrows_the_board(wired, api_client):
    assert {r["employee_id"] for r in _board(api_client, reps="E200")["rows"]} == {"E200"}


def test_the_status_filter_narrows_the_board(wired, api_client):
    assert {r["employee_id"] for r in _board(api_client, status="done")["rows"]} == {"E100"}


# ── one person's rail ───────────────────────────────────────────────────────────────────────────
def test_one_person_returns_the_six_steps_with_their_doors(wired, api_client):
    r = api_client.get(f"{BOARD}/E100?org_id={ORG_A}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["employee_name"] == "Ada Okafor" and d["status"] == "done"
    assert [s["key"] for s in d["steps"]] == ["identity", "pay", "login", "access", "plan",
                                              "onboarding"]
    assert all(s["screen"].startswith("/") for s in d["steps"])


def test_a_half_set_up_person_says_what_is_missing_and_where_to_pick_up(wired, api_client):
    d = api_client.get(f"{BOARD}/E200?org_id={ORG_A}").json()
    assert d["resume"] == "identity" and d["resume_title"] == "Who and where"
    ident = {s["key"]: s for s in d["steps"]}["identity"]
    joined = " ".join(ident["needs_input"]).lower()
    for want in ("home store", "role"):
        assert want in joined, joined
    pay = {s["key"]: s for s in d["steps"]}["pay"]
    assert pay["blocked_by"] == ["identity"]


def test_an_hourly_person_with_no_rate_is_named_as_a_zero_dollar_payroll_risk(wired, api_client,
                                                                              fake_supabase):
    fake_supabase.seed("storeops.employees", [dict(EMP_A, pay_rate=None)])
    d = api_client.get(f"{BOARD}/E100?org_id={ORG_A}").json()
    pay = {s["key"]: s for s in d["steps"]}["pay"]
    assert pay["status"] == "needs_input"
    assert any("$0.00" in r for r in pay["needs_input"])


def test_an_inactive_person_is_readable_and_says_the_record_is_switched_off(wired, api_client):
    d = api_client.get(f"{BOARD}/E300?org_id={ORG_A}").json()
    assert d["is_active"] is False


def test_an_unknown_person_is_a_404_not_an_empty_rail(wired, api_client):
    assert api_client.get(f"{BOARD}/NOPE?org_id={ORG_A}").status_code == 404


def test_one_tenant_cannot_read_anothers_person_by_id(wired, api_client):
    assert api_client.get(f"{BOARD}/L900?org_id={ORG_A}").status_code == 404
    assert api_client.get(f"{BOARD}/E100?org_id={ORG_B}").status_code == 404


# ── the incentive-plan step follows the tenant's own declaration ────────────────────────────────
def test_a_tenant_with_no_commission_plan_has_the_plan_step_skipped_with_a_reason(wired, api_client):
    d = api_client.get(f"{BOARD}/E100?org_id={ORG_A}").json()
    plan = {s["key"]: s for s in d["steps"]}["plan"]
    assert plan["status"] == "skipped" and plan["detail"]["why"]
    assert d["declared"]["pays_incentive_plans"] is False


def test_a_tenant_that_has_plans_gets_the_step_back(wired, api_client, fake_supabase):
    fake_supabase.seed("commcalc.commission_plan", [{"id": "p1", "org_id": ORG_A,
                                                     "name": "Rep Tier", "is_active": True}])
    d = api_client.get(f"{BOARD}/E100?org_id={ORG_A}").json()
    assert d["declared"]["pays_incentive_plans"] is True
    assert {s["key"]: s for s in d["steps"]}["plan"]["status"] != "skipped"


# ── degrade by name, never 500 ──────────────────────────────────────────────────────────────────
class _RaisingOn:
    """The double, except that one table raises the way PostgREST does for a missing relation."""

    def __init__(self, inner, table):
        self._inner, self._table = inner, table

    def schema(self, name):
        return _RaisingSchema(self._inner.schema(name), self._table)

    def table(self, name):
        return self.schema("public").table(name)

    def __getattr__(self, item):
        return getattr(self._inner, item)


class _RaisingSchema:
    def __init__(self, inner, table):
        self._inner, self._table = inner, table

    def table(self, name):
        if name == self._table:
            raise RuntimeError("relation does not exist")
        return self._inner.table(name)

    def __getattr__(self, item):
        return getattr(self._inner, item)


def test_a_missing_table_is_reported_in_blockers_rather_than_500ing(monkeypatch, fake_supabase,
                                                                    api_client, wired):
    monkeypatch.setattr("app.modules.storeops.employee_setup_api.get_supabase",
                        lambda: _RaisingOn(fake_supabase, "app_users"))
    b = _board(api_client)
    assert b["ready"] is False
    assert any(x["table"] == "app_users" for x in b["blockers"]), b["blockers"]
    assert _row(b, "E100")["status"] in ("not_started", "in_progress", "needs_input", "done")


# ── nothing here writes ─────────────────────────────────────────────────────────────────────────
def test_reading_the_board_leaves_every_seeded_row_exactly_as_it_was(wired, api_client):
    before = {k: wired.rows(k) for k in ("storeops.employees", "storeops.app_users",
                                         "storeops.employee_onboarding",
                                         "storeops.employee_onboarding_profile")}
    _board(api_client)
    api_client.get(f"{BOARD}/E100?org_id={ORG_A}")
    assert {k: wired.rows(k) for k in before} == before

    # …and the surface carries no writer at all, so a later change cannot quietly add one.
    import inspect

    from app.modules.storeops import employee_setup_api as mod
    src = inspect.getsource(mod)
    for banned in (".insert(", ".update(", ".upsert(", ".delete("):
        assert banned not in src, f"{banned} must not appear on a read-only surface"


# ── the stage words have ONE definition, and hr/router dereferences it ──────────────────────────
def test_hr_router_dereferences_the_stage_words_rather_than_keeping_a_copy():
    from app.modules.hr import onboarding_stages as stages
    from app.modules.hr import router as hr
    assert hr.WORKFLOW_STATUSES is stages.WORKFLOW_STATUSES
    assert hr.STATUS_ORDER is stages.STATUS_ORDER
    assert hr.STATUS_LABELS is stages.STATUS_LABELS
