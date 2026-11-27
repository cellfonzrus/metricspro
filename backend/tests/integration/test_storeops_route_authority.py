"""Integration: the storeops writes ASK WHO IS CALLING, through the REAL app (index §STOREOPS-07.8).

Real routing, real middleware, the real endpoint bodies, over the in-memory database double
(docs/TESTING.md — there is no staging Supabase).

WHAT THIS COVERS THAT THE LOCK CANNOT. `harness_storeops_route_authority.py` reads the SOURCE and
proves the call is present. That is a text fact. These tests drive the real route and prove the
BEHAVIOUR: an individual contributor is refused, NOTHING is written, a manager is allowed, and — the
half that matters just as much (★ NO LOCKOUT ★, index §22) — an unrestricted/in-process caller is
completely unaffected.

THE ONE HOME'S ANSWER IS STUBBED, NOT RE-SPELLED. Each test patches the input the gate reads
(`role_is_self_scoped`, `schedule_emp_ids`) rather than asserting a role name here. So what is
proved is that THE ROUTE ASKS — not that a second copy of the rule lives in a test. Same shape as
§STOREOPS-07.7 §D10.

NEGATIVE CONTROL. Every refusal is paired with the pre-fix world: the gate monkeypatched to a
pass-through, the SAME request, and the write landing. That is what makes these tests evidence that
the gate is the thing stopping the attack, rather than something else refusing for its own reasons.
"""
import pytest

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"

R = "app.modules.storeops.router"


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr(f"{R}.get_supabase", lambda: fake_supabase)
    fake_supabase.seed("storeops.org_levels", [{"id": 1, "org_id": ORG_A, "name": "Market",
                                                "rank": 1}])
    fake_supabase.seed("storeops.org_units", [])
    fake_supabase.seed("storeops.org_managers", [])
    fake_supabase.seed("storeops.stores", [{"store_code": "7710", "org_id": ORG_A,
                                            "address": "1 A St", "org_unit_id": None}])
    fake_supabase.seed("storeops.employees", [
        {"id": 1, "org_id": ORG_A, "employee_id": "E100", "name": "Ada", "home_store": "7710",
         "is_active": True},
        {"id": 2, "org_id": ORG_A, "employee_id": "E200", "name": "Ben", "home_store": "7710",
         "is_active": True}])
    fake_supabase.seed("storeops.payroll_settings", [
        {"org_id": ORG_A, "employee_id": "E200", "filing_status": "Married", "allowances": 3,
         "state": "NY", "extra_withholding": 25.0, "skipped": False}])
    return fake_supabase


def _as_individual_contributor(monkeypatch):
    """The Manan shape: a login the org resolves, whose role is scope `self`."""
    monkeypatch.setattr(f"{R}._caller_app_user",
                        lambda *a, **k: {"org_id": ORG_A, "role": "sales_rep",
                                         "employee_id": "E100", "email": "ada@x.test"})
    monkeypatch.setattr(f"{R}.role_is_self_scoped", lambda org, role: True)


def _as_manager(monkeypatch):
    monkeypatch.setattr(f"{R}._caller_app_user",
                        lambda *a, **k: {"org_id": ORG_A, "role": "store_manager",
                                         "employee_id": "E900", "email": "mgr@x.test"})
    monkeypatch.setattr(f"{R}.role_is_self_scoped", lambda org, role: False)


def _ungate(monkeypatch):
    """The PRE-FIX world — the gate as a pass-through."""
    monkeypatch.setattr(f"{R}.require_not_individual_contributor", lambda *a, **k: None)


H = {"Authorization": "Bearer probe-token"}


# ══ the company structure ════════════════════════════════════════════════════════════════════════
def test_an_individual_contributor_cannot_create_an_org_unit_and_nothing_is_written(
        wired, api_client, monkeypatch):
    _as_individual_contributor(monkeypatch)
    before = len(wired.rows("storeops.org_units"))
    r = api_client.post(f"/api/v1/storeops/org/units?org_id={ORG_A}",
                        json={"name": "Chicago", "level_id": 1}, headers=H)
    assert r.status_code == 403, r.text
    assert "manager action" in r.text
    assert len(wired.rows("storeops.org_units")) == before, "a refused write still wrote"


def test_negative_control_without_the_gate_the_same_request_writes(
        wired, api_client, monkeypatch):
    _as_individual_contributor(monkeypatch)
    _ungate(monkeypatch)
    before = len(wired.rows("storeops.org_units"))
    r = api_client.post(f"/api/v1/storeops/org/units?org_id={ORG_A}",
                        json={"name": "Chicago", "level_id": 1}, headers=H)
    assert r.status_code < 400, r.text
    assert len(wired.rows("storeops.org_units")) == before + 1, (
        "the gate is not what stopped the attack — this test proves nothing without this")


def test_a_manager_may_still_create_an_org_unit(wired, api_client, monkeypatch):
    _as_manager(monkeypatch)
    r = api_client.post(f"/api/v1/storeops/org/units?org_id={ORG_A}",
                        json={"name": "Chicago", "level_id": 1}, headers=H)
    assert r.status_code < 400, r.text


def test_an_individual_contributor_cannot_assign_themselves_as_a_units_manager(
        wired, api_client, monkeypatch):
    """org_managers is what the escalation chain and the overhead rollup read, so a self-granted
    row is not cosmetic."""
    _as_individual_contributor(monkeypatch)
    before = len(wired.rows("storeops.org_managers"))
    r = api_client.post(f"/api/v1/storeops/org/units/u1/managers?org_id={ORG_A}",
                        json={"employee_id": "E100"}, headers=H)
    assert r.status_code == 403, r.text
    assert len(wired.rows("storeops.org_managers")) == before


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/v1/storeops/org/seed", None),
    ("post", "/api/v1/storeops/org/build-standard", None),
    ("post", "/api/v1/storeops/org/levels", {"name": "Region"}),
    ("delete", "/api/v1/storeops/org/units/u1", None),
    ("put", "/api/v1/storeops/org/stores/7710/unit", {"unit_id": "u1"}),
    ("put", "/api/v1/storeops/org/employees/1/unit", {"unit_id": "u1"}),
    ("post", "/api/v1/storeops/stores", {"store_code": "9999"}),
    ("post", "/api/v1/storeops/stores/bulk", {"stores": [{"store_code": "9999"}]}),
])
def test_every_structure_write_refuses_an_individual_contributor(
        wired, api_client, monkeypatch, method, path, body):
    """The siblings, in one place — one gated and its twin left open is the same defect in a hat."""
    _as_individual_contributor(monkeypatch)
    kw = {"headers": H}
    if body is not None:
        kw["json"] = body
    r = getattr(api_client, method)(f"{path}?org_id={ORG_A}", **kw)
    assert r.status_code == 403, f"{method.upper()} {path} did not refuse: {r.status_code} {r.text[:200]}"


# ══ one named person's W-4 ═══════════════════════════════════════════════════════════════════════
def test_a_caller_cannot_read_another_persons_payroll_settings(wired, api_client, monkeypatch):
    monkeypatch.setattr(f"{R}.schedule_emp_ids", lambda *a, **k: {"E100"})
    r = api_client.get(f"/api/v1/storeops/payroll-settings/E200?org_id={ORG_A}", headers=H)
    assert r.status_code == 403, r.text
    assert "E200" not in r.text or "not in your team" in r.text
    assert "Married" not in r.text, "the refused answer still carried the W-4"


def test_the_same_caller_still_reads_their_OWN_payroll_settings(wired, api_client, monkeypatch):
    """★ NO LOCKOUT ★ — gating a read must never take a person's own screen away."""
    monkeypatch.setattr(f"{R}.schedule_emp_ids", lambda *a, **k: {"E200"})
    r = api_client.get(f"/api/v1/storeops/payroll-settings/E200?org_id={ORG_A}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["filing_status"] == "Married"


def test_an_unrestricted_caller_reads_anybodys_payroll_settings(wired, api_client, monkeypatch):
    """★ NO LOCKOUT ★ — None is UNRESTRICTED (a sweep, a cron, logins off) and stays so."""
    monkeypatch.setattr(f"{R}.schedule_emp_ids", lambda *a, **k: None)
    r = api_client.get(f"/api/v1/storeops/payroll-settings/E200?org_id={ORG_A}", headers=H)
    assert r.status_code == 200, r.text


def test_an_individual_contributor_cannot_rewrite_a_W4_even_their_own(
        wired, api_client, monkeypatch):
    """Setting withholding is a manager's act (`manager_only=True`) — a rep adjusting their own pay
    inputs is the very thing the owner stopped."""
    _as_individual_contributor(monkeypatch)
    monkeypatch.setattr(f"{R}.schedule_emp_ids", lambda *a, **k: {"E100"})
    r = api_client.put(f"/api/v1/storeops/payroll-settings/E100?org_id={ORG_A}",
                       json={"filing_status": "Single", "allowances": 9}, headers=H)
    assert r.status_code == 403, r.text
    rows = [x for x in wired.rows("storeops.payroll_settings") if x.get("employee_id") == "E100"]
    assert not rows, "a refused W-4 write still landed"


def test_a_manager_may_still_set_a_W4(wired, api_client, monkeypatch):
    _as_manager(monkeypatch)
    monkeypatch.setattr(f"{R}.schedule_emp_ids", lambda *a, **k: None)
    r = api_client.put(f"/api/v1/storeops/payroll-settings/E200?org_id={ORG_A}",
                       json={"filing_status": "Single", "allowances": 1}, headers=H)
    assert r.status_code < 400, r.text


# ══ the stranger, at the HTTP boundary ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("path", [
    "/api/v1/storeops/payroll-settings/E200",
    "/api/v1/storeops/org/tree",
])
def test_these_paths_are_not_declared_for_a_confined_employee(path):
    """The confinement declaration is asked for every path (§CORE-52); none of the surfaces this
    change touches is declared, so a scope-`self` employee never reaches the handler at all. The
    gate above is the SECOND door, not the only one."""
    from app.core import rep_surface
    for m in ("GET", "PUT", "POST", "DELETE"):
        assert rep_surface.api_allowed(path, m) is False, f"{m} {path} is declared rep-reachable"
