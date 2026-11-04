"""Integration: the four writes that asked nothing now ask, through the REAL app, for EVERY tenant.

OWNER (2026-11-04): *"on ny logistics Manan was able to go past it and also see the adjustment hours,
this should not be allowed for any sales reps, fix the permissions accordingly … this is universal
across the board."*

The real FastAPI app over the in-memory double, with the REAL scope code deciding whose people are
whose (`app.core.scope.visible_people_keyset` → `storeops.router.schedule_emp_ids`) — nothing about
the span is stubbed here, which is the point: the unit proof (`backend/harness_rep_surface.py`) pins
the declaration, and this pins that a real request through a real role row lands on the real answer.

TWO TENANTS throughout, each with its own roles rows, because the owner's rule is that a feature
works for every tenant and never leaks between them (CLAUDE.md, 2026-10-30). Tenant B is deliberately
the SAME role NAMES with DIFFERENT configuration, which is how a one-tenant answer would show.
"""
import pytest

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
UID = {"repA": "uid-rep-a", "mgrA": "uid-mgr-a", "repB": "uid-rep-b", "mgrB": "uid-mgr-b"}
HDR = {"repA": "Bearer rep-a", "mgrA": "Bearer mgr-a", "repB": "Bearer rep-b", "mgrB": "Bearer mgr-b"}
_BY_TOKEN = {v: UID[k] for k, v in HDR.items()}


def _rows(org, suffix, store):
    """One tenant's people: a rep, a teammate at the same store, a manager of that store, and one
    employee at ANOTHER store who is nobody's business here."""
    return {
        "app_users": [
            {"id": f"au-rep-{suffix}", "org_id": org, "auth_id": UID[f"rep{suffix}"],
             "role": "sales_rep", "employee_id": f"E100{suffix}", "is_active": True},
            {"id": f"au-mgr-{suffix}", "org_id": org, "auth_id": UID[f"mgr{suffix}"],
             "role": "store_manager", "employee_id": f"E200{suffix}", "store_code": store,
             "is_active": True},
        ],
        "employees": [
            {"id": f"1{suffix}", "org_id": org, "employee_id": f"E100{suffix}", "name": "The Rep",
             "home_store": store, "is_active": True},
            {"id": f"2{suffix}", "org_id": org, "employee_id": f"E101{suffix}", "name": "A Teammate",
             "home_store": store, "is_active": True},
            {"id": f"3{suffix}", "org_id": org, "employee_id": f"E200{suffix}", "name": "The Manager",
             "home_store": store, "is_active": True},
            {"id": f"9{suffix}", "org_id": org, "employee_id": f"E999{suffix}", "name": "Another Store",
             "home_store": f"{store}-OTHER", "is_active": True},
        ],
        "roles": [
            # A rep is an individual contributor by DECLARATION, which is the one answer the whole
            # platform asks (§14w) — tenant A declares it through `scope`, tenant B through
            # `people_visibility` over a store scope, because both shapes exist live.
            {"id": f"r1{suffix}", "org_id": org, "name": "sales_rep",
             "permissions": ({"scope": "self"} if suffix == "A"
                             else {"scope": "store", "people_visibility": "self"})},
            {"id": f"r2{suffix}", "org_id": org, "name": "store_manager",
             "permissions": {"scope": "store", "people_visibility": "span"}},
        ],
    }


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """RBAC on, two tenants, the real routers reading the double."""
    for mod in ("app.modules.storeops.router", "app.modules.hr.router"):
        monkeypatch.setattr(f"{mod}.sb", lambda: fake_supabase.schema("storeops"), raising=False)
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.modules.hr.router._so", lambda: fake_supabase.schema("storeops"),
                        raising=False)
    monkeypatch.setattr("app.modules.core.router._uid_from_token",
                        lambda a: _BY_TOKEN.get((a or "").strip()))
    # The template read is not what is under test; an un-ready template short-circuits the body and
    # leaves the GATE as the only thing the request can fail on.
    monkeypatch.setattr("app.modules.hr.router.onboarding_template",
                        lambda **kw: {"ready": False, "categories": []}, raising=False)
    fake_supabase.seed("storeops.app_config", [{"id": 1, "rbac_enabled": True}])
    a, b = _rows(ORG_A, "A", "S1"), _rows(ORG_B, "B", "T1")
    for table in ("app_users", "employees", "roles"):
        fake_supabase.seed(f"storeops.{table}", a[table] + b[table])
    fake_supabase.seed("storeops.stores", [
        {"id": "s1", "org_id": ORG_A, "store_code": "S1", "is_active": True},
        {"id": "s2", "org_id": ORG_A, "store_code": "S1-OTHER", "is_active": True},
        {"id": "t1", "org_id": ORG_B, "store_code": "T1", "is_active": True},
        {"id": "t2", "org_id": ORG_B, "store_code": "T1-OTHER", "is_active": True}])
    fake_supabase.seed("storeops.manual_hours", [
        {"id": "mhA", "org_id": ORG_A, "employee_id": "E101A", "work_date": "2026-11-03",
         "hours": 2.0, "reason": "forgot to clock out"}])
    fake_supabase.seed("storeops.shifts", [])
    fake_supabase.seed("storeops.timelog", [])
    fake_supabase.seed("storeops.time_off_requests", [])
    fake_supabase.seed("storeops.shift_swap_requests", [])
    fake_supabase.seed("storeops.employee_onboarding", [])
    fake_supabase.seed("storeops.employee_onboarding_profile", [])
    return fake_supabase


def _hours(client, who, org, eid):
    return client.post(f"/api/v1/storeops/manual-hours?org_id={org}",
                       headers={"Authorization": HDR[who]},
                       json={"employee_id": eid, "hours": 3, "reason": "test", "work_date": "2026-11-04"})


# ── THE OWNER'S REPORT ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("who,org,eid", [("repA", ORG_A, "E101A"), ("repA", ORG_A, "E100A"),
                                         ("repB", ORG_B, "E101B"), ("repB", ORG_B, "E100B")])
def test_a_rep_cannot_adjust_hours_in_either_tenant(api_client, wired, who, org, eid):
    """Not for a teammate, and not even for themselves — adjusting hours is a manager's act."""
    r = _hours(api_client, who, org, eid)
    assert r.status_code == 403, r.text
    assert "manager action" in r.json()["detail"]


@pytest.mark.parametrize("who,org,eid", [("mgrA", ORG_A, "E101A"), ("mgrB", ORG_B, "E101B")])
def test_the_manager_of_that_store_can(api_client, wired, who, org, eid):
    assert _hours(api_client, who, org, eid).status_code == 200


@pytest.mark.parametrize("who,org,eid", [("mgrA", ORG_A, "E999A"), ("mgrB", ORG_B, "E999B")])
def test_a_manager_cannot_adjust_for_someone_who_is_not_theirs(api_client, wired, who, org, eid):
    r = _hours(api_client, who, org, eid)
    assert r.status_code == 403 and "not in your team" in r.json()["detail"], r.text


def test_a_rep_cannot_delete_an_hours_adjustment(api_client, wired):
    r = api_client.delete(f"/api/v1/storeops/manual-hours/mhA?org_id={ORG_A}",
                          headers={"Authorization": HDR["repA"]})
    assert r.status_code == 403, r.text
    rows = wired.schema("storeops").table("manual_hours").select("*").eq("id", "mhA").execute().data
    assert rows, "the row must still be there"


def test_the_manager_can_delete_it(api_client, wired):
    r = api_client.delete(f"/api/v1/storeops/manual-hours/mhA?org_id={ORG_A}",
                          headers={"Authorization": HDR["mgrA"]})
    assert r.status_code == 200, r.text


# ── TIME OFF: an employee files their own, a manager files for their own employee ─────────────────
def _timeoff(client, who, org, eid):
    return client.post(f"/api/v1/storeops/time-off?org_id={org}", headers={"Authorization": HDR[who]},
                       json={"employee_id": eid, "start_date": "2026-11-10", "end_date": "2026-11-11"})


@pytest.mark.parametrize("who,org,eid", [("repA", ORG_A, "E100A"), ("repB", ORG_B, "E100B")])
def test_an_employee_files_their_own_time_off(api_client, wired, who, org, eid):
    assert _timeoff(api_client, who, org, eid).status_code == 200


@pytest.mark.parametrize("who,org,eid", [("mgrA", ORG_A, "E101A"), ("mgrB", ORG_B, "E101B")])
def test_a_manager_files_time_off_for_their_own_employee(api_client, wired, who, org, eid):
    """The owner's rule, 2026-11-04: *"the manager can file for time off for the employee"*."""
    assert _timeoff(api_client, who, org, eid).status_code == 200


@pytest.mark.parametrize("who,org,eid", [("repA", ORG_A, "E101A"), ("mgrA", ORG_A, "E999A")])
def test_nobody_files_time_off_in_a_stranger_s_name(api_client, wired, who, org, eid):
    assert _timeoff(api_client, who, org, eid).status_code == 403


def test_a_shift_swap_cannot_be_raised_in_a_stranger_s_name(api_client, wired):
    ok = api_client.post(f"/api/v1/storeops/shift-swaps?org_id={ORG_A}",
                         headers={"Authorization": HDR["repA"]}, json={"requester_id": "E100A"})
    no = api_client.post(f"/api/v1/storeops/shift-swaps?org_id={ORG_A}",
                         headers={"Authorization": HDR["repA"]}, json={"requester_id": "E999A"})
    assert ok.status_code == 200 and no.status_code == 403, (ok.text, no.text)


# ── ONBOARDING PAPERWORK ─────────────────────────────────────────────────────────────────────────
def _paperwork(client, who, org, eid):
    return client.get(f"/api/v1/hr/onboarding/employee/{eid}?org_id={org}",
                      headers={"Authorization": HDR[who]})


@pytest.mark.parametrize("who,org,eid", [("repA", ORG_A, "E101A"), ("repA", ORG_A, "E999A"),
                                         ("repB", ORG_B, "E101B")])
def test_nobody_reads_another_person_s_onboarding_paperwork(api_client, wired, who, org, eid):
    """Owner 2026-11-04: *"I moneyed can see anybody else's onboarding paperwork that is a big
    problem"*. Reported unfixed on 2026-11-01; fixed here."""
    assert _paperwork(api_client, who, org, eid).status_code == 403


@pytest.mark.parametrize("who,org,eid", [("repA", ORG_A, "E100A"), ("mgrA", ORG_A, "E101A"),
                                         ("repB", ORG_B, "E100B")])
def test_your_own_record_and_your_own_team_s_are_readable(api_client, wired, who, org, eid):
    assert _paperwork(api_client, who, org, eid).status_code == 200


def test_the_paperwork_cannot_be_CHANGED_for_a_stranger(api_client, wired):
    r = api_client.patch(f"/api/v1/hr/onboarding/employee/E999A?org_id={ORG_A}",
                         headers={"Authorization": HDR["repA"]}, json={"work_state": "NY"})
    assert r.status_code == 403, r.text


# ── NO TENANT'S ANSWER REACHES ANOTHER ───────────────────────────────────────────────────────────
def test_one_tenant_s_rep_cannot_act_on_another_tenant_s_employee(api_client, wired):
    """Tenant A's rep, naming tenant B's employee. The org rewrite is not what is being proved —
    whatever org the request claims, the person is not in this caller's team."""
    assert _hours(api_client, "repA", ORG_A, "E101B").status_code == 403
    assert _timeoff(api_client, "repA", ORG_A, "E100B").status_code == 403
    assert _paperwork(api_client, "repA", ORG_A, "E100B").status_code == 403


def test_a_manager_of_one_tenant_is_not_a_manager_in_the_other(api_client, wired):
    assert _hours(api_client, "mgrA", ORG_B, "E101B").status_code == 403
    assert _hours(api_client, "mgrB", ORG_A, "E101A").status_code == 403


# ── THE ALWAYS-ON API GATE (the middleware, over the real app) ───────────────────────────────────
# `RepSurfaceMiddleware` is installed unconditionally, which is the whole point: it must not depend
# on MULTI_TENANT_ENFORCE (off by default). Identity is stubbed at the ONE seam the middleware uses
# (`_resolve_identity` — a Supabase token verification, which no test may perform), never the gate.
_IDENT = {
    HDR["repA"].split(" ", 1)[1]: (True, False, (ORG_A,), ORG_A, {ORG_A: {"role": "sales_rep"}}, UID["repA"]),
    HDR["mgrA"].split(" ", 1)[1]: (True, False, (ORG_A,), ORG_A, {ORG_A: {"role": "store_manager"}}, UID["mgrA"]),
    HDR["repB"].split(" ", 1)[1]: (True, False, (ORG_B,), ORG_B, {ORG_B: {"role": "sales_rep"}}, UID["repB"]),
}


@pytest.fixture
def gate(monkeypatch, wired):
    monkeypatch.delenv("MULTI_TENANT_ENFORCE", raising=False)   # the tenant middleware stays OFF
    monkeypatch.setattr("app.core.tenant_middleware._resolve_identity",
                        lambda tok: _IDENT.get(tok, (False, False, (), None, {}, None)))
    monkeypatch.setattr("app.core.tenant_middleware._rep_cache",
                        type(__import__("app.core.tenant_middleware", fromlist=["x"])._rep_cache)(
                            ttl=0.001, cap=16))
    return wired


MANAGER_API = ["/api/v1/storeops/payroll", "/api/v1/storeops/manual-hours",
               "/api/v1/storeops/employees", "/api/v1/storeops/hours-budgets"]


@pytest.mark.parametrize("who", ["repA", "repB"])
@pytest.mark.parametrize("api", MANAGER_API)
def test_the_api_itself_refuses_a_rep_a_manager_path(api_client, gate, who, api):
    """Not the menu, not the page — the API. This is the half that made the browser gate irrelevant."""
    r = api_client.get(f"{api}?org_id={ORG_A}", headers={"Authorization": HDR[who]})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "not_your_access"


@pytest.mark.parametrize("api", ["/api/v1/core/me", "/api/v1/storeops/timeclock/status",
                                 "/api/v1/commcalc/nav-config"])
def test_a_declared_path_is_never_refused(api_client, gate, api):
    assert api_client.get(api, headers={"Authorization": HDR["repA"]}).status_code != 403


def test_a_store_manager_is_not_confined_by_the_api_gate(api_client, gate):
    r = api_client.get(f"/api/v1/storeops/employees?org_id={ORG_A}", headers={"Authorization": HDR["mgrA"]})
    assert r.status_code != 403, r.text


def test_the_break_glass_lifts_the_api_gate(api_client, gate, monkeypatch):
    monkeypatch.setenv("REP_CONFINE", "0")
    r = api_client.get(f"/api/v1/storeops/payroll?org_id={ORG_A}", headers={"Authorization": HDR["repA"]})
    assert r.status_code != 403, r.text


def test_an_anonymous_request_is_not_confined_by_this_gate(api_client, gate):
    """Nothing to confine: whatever refuses it, it is not this (the auth gates own that question)."""
    r = api_client.get(f"/api/v1/storeops/payroll?org_id={ORG_A}")
    assert r.status_code != 403 or r.json().get("code") != "not_your_access"
