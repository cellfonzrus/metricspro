"""Integration: the warning on the screen can reach the fix, through the real app.

OWNER ASK 2026-11-09: a "not configured" warning must show HOW to fix it, with the way in when the
viewer's role permits it, and a link they can send to their manager when it does not.

WHAT THESE COVER THAT THE UNIT TESTS CANNOT. `tests/unit/test_setup_fix.py` hands the registry a
permission answer and a list of people. It cannot see whether the ENDPOINT asks the right question —
and that is exactly where this class of defect lives: the registry can be perfect while the route
answers `can_fix` from the wrong caller, or offers a recipient from another tenant, or 500s on a
tenant whose role table cannot be read.

MULTI-TENANT (project rule 2026-10-30). Every case runs for three tenant shapes:
  HOUSE  — a full role ladder; the caller is an administrator
  SOLO   — the one area granted to a store manager and denied to the owner
  BARE   — nobody can edit anything, which is what a tenant looks like on day one
and §D proves one tenant's people can never be offered to another tenant's caller.
"""
import pytest

from app.modules.core import setup_fix as sf

HOUSE = "00000000-0000-0000-0000-000000000001"
SOLO = "11111111-2222-3333-4444-555555555555"
BARE = "99999999-8888-7777-6666-555555555555"
ALL_ORGS = [HOUSE, SOLO, BARE]

GUIDE = "/api/v1/core/fix-guide"

# A company-closable gap behind a GRANTABLE settings area, and one that is ours alone.
AREA_KEY = "store_targets"          # gate: 'targets'
ADMIN_KEY = "org_chart_manager"     # gate: ADMIN (no grantable area)
PLATFORM_KEY = "notify_email_sender"

ROLES = [
    {"id": 1, "org_id": HOUSE, "name": "admin", "permissions": {"scope": "all"}},
    {"id": 2, "org_id": HOUSE, "name": "rep", "permissions": {"scope": "self"}},
    {"id": 3, "org_id": SOLO, "name": "owner", "permissions": {"scope": "all", "settings": {"targets": False}}},
    {"id": 4, "org_id": SOLO, "name": "manager", "permissions": {"scope": "store", "settings": {"targets": True}}},
    {"id": 5, "org_id": SOLO, "name": "rep", "permissions": {"scope": "self"}},
    {"id": 6, "org_id": BARE, "name": "rep", "permissions": {"scope": "self"}},
]
APP_USERS = [
    {"id": "u1", "org_id": HOUSE, "auth_id": "a-admin", "employee_id": "E100", "role": "admin",
     "full_name": "Dana Admin", "email": "dana@example.com", "is_active": True},
    {"id": "u2", "org_id": HOUSE, "auth_id": "a-rep", "employee_id": "E200", "role": "rep",
     "full_name": "Rab Rep", "email": "rab@example.com", "is_active": True},
    {"id": "u3", "org_id": SOLO, "auth_id": "s-owner", "employee_id": "S100", "role": "owner",
     "full_name": "Ola Owner", "email": "ola@example.com", "is_active": True},
    {"id": "u4", "org_id": SOLO, "auth_id": "s-mgr", "employee_id": "S200", "role": "manager",
     "full_name": "Mo Manager", "email": "mo@example.com", "is_active": True},
    {"id": "u5", "org_id": SOLO, "auth_id": "s-rep", "employee_id": "S300", "role": "rep",
     "full_name": "Sal Rep", "email": "sal@example.com", "is_active": True},
    {"id": "u6", "org_id": BARE, "auth_id": "b-rep", "employee_id": "B100", "role": "rep",
     "full_name": "Baz Rep", "email": "baz@example.com", "is_active": True},
]
# The caller on each tenant who may NOT fix the area gap, and the person who may.
ASKER = {HOUSE: ("a-rep", "E200"), SOLO: ("s-rep", "S300"), BARE: ("b-rep", "B100")}
FIXER = {HOUSE: "E100", SOLO: "S200", BARE: None}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """The core router's reads pointed at the double; the caller is whichever login the test names."""
    import app.modules.core.router as r

    monkeypatch.setattr(r, "sb", lambda: fake_supabase)
    monkeypatch.setattr(r, "get_supabase", lambda: fake_supabase)
    holder = {"auth": "a-admin"}
    monkeypatch.setattr(r, "_uid_from_token", lambda *a, **k: holder["auth"])
    fake_supabase.seed("storeops.roles", ROLES)
    fake_supabase.seed("storeops.app_users", APP_USERS)
    fake_supabase.seed("storeops.employees", [])
    fake_supabase.seed("storeops.stores", [])
    fake_supabase.holder = holder
    return fake_supabase


def _get(api_client, wired, *, auth, org, key, origin="Targets report"):
    wired.holder["auth"] = auth
    return api_client.get(GUIDE, params={"key": key, "org_id": org, "origin": origin},
                          headers={"authorization": "Bearer t", "x-active-org": org})


# ── the route is where it is documented ─────────────────────────────────────────────────────────
def test_the_route_is_registered(app_module):
    from app.core.path_redact import route_templates
    assert GUIDE in set(route_templates(app_module))


# ── §A a permitted caller is handed the way in ──────────────────────────────────────────────────
def test_A1_an_administrator_is_told_to_fix_it_now(wired, api_client):
    res = _get(api_client, wired, auth="a-admin", org=HOUSE, key=AREA_KEY)
    assert res.status_code == 200
    g = res.json()
    assert g["can_fix"] is True
    assert g["page"] == sf.fix(AREA_KEY)["page"]
    assert g["ask"] is None and g["support"] is False
    assert g["steps"] == sf.fix(AREA_KEY)["steps"]


def test_A2_a_granted_manager_can_fix_the_one_area_and_the_owner_who_was_denied_cannot(wired, api_client):
    """The tenant's own grant decides, not the role's name — the same precedence the write endpoint
    refuses on. A tenant that deliberately took an area off its owner must not be overridden here."""
    assert _get(api_client, wired, auth="s-mgr", org=SOLO, key=AREA_KEY).json()["can_fix"] is True
    owner = _get(api_client, wired, auth="s-owner", org=SOLO, key=AREA_KEY).json()
    assert owner["can_fix"] is False
    assert [r["employee_id"] for r in owner["ask"]["recipients"]] == ["S200"]


# ── §B a refused caller is handed PEOPLE, and the message carries the fix ───────────────────────
@pytest.mark.parametrize("org", [HOUSE, SOLO])
def test_B1_a_rep_gets_the_steps_and_somebody_who_can_actually_close_it(wired, api_client, org):
    g = _get(api_client, wired, auth=ASKER[org][0], org=org, key=AREA_KEY).json()
    assert g["can_fix"] is False
    assert g["steps"] == sf.fix(AREA_KEY)["steps"], "the steps show either way — the rep can relay them"
    assert [r["employee_id"] for r in g["ask"]["recipients"]] == [FIXER[org]]
    assert g["support"] is False
    msg = g["ask"]["message"]
    assert sf.fix(AREA_KEY)["page"] in msg
    assert "Targets report" in msg, "the message says where they hit it"


def test_B2_a_tenant_where_nobody_can_close_it_is_sent_to_support_not_a_dead_button(wired, api_client):
    g = _get(api_client, wired, auth="b-rep", org=BARE, key=AREA_KEY).json()
    assert g["can_fix"] is False and g["ask"] is None and g["support"] is True
    assert g["steps"], "the steps are still shown — support is told the same thing"


def test_B3_the_admin_gate_is_answered_without_a_grantable_area(wired, api_client):
    assert _get(api_client, wired, auth="a-admin", org=HOUSE, key=ADMIN_KEY).json()["can_fix"] is True
    rep = _get(api_client, wired, auth="a-rep", org=HOUSE, key=ADMIN_KEY).json()
    assert rep["can_fix"] is False
    assert [r["employee_id"] for r in rep["ask"]["recipients"]] == ["E100"]


# ── §C a platform gap offers nobody a button that would be refused ──────────────────────────────
@pytest.mark.parametrize("org,auth", [(HOUSE, "a-admin"), (SOLO, "s-owner"), (BARE, "b-rep")])
def test_C1_nobody_in_any_tenant_is_offered_a_platform_gap(wired, api_client, org, auth):
    g = _get(api_client, wired, auth=auth, org=org, key=PLATFORM_KEY).json()
    assert g["can_fix"] is False and g["page"] is None and g["ask"] is None
    assert g["support"] is True


# ── §D org scoping: a recipient from another tenant is impossible ───────────────────────────────
@pytest.mark.parametrize("org", ALL_ORGS)
def test_D1_every_recipient_belongs_to_the_calling_tenant(wired, api_client, org):
    g = _get(api_client, wired, auth=ASKER[org][0], org=org, key=AREA_KEY).json()
    mine = {u["employee_id"] for u in APP_USERS if u["org_id"] == org}
    for r in (g["ask"] or {}).get("recipients", []):
        assert r["employee_id"] in mine


def test_D2_the_asker_is_never_offered_as_their_own_fixer(wired, api_client):
    """An administrator who somehow reaches the refused branch must not be told to ask themselves."""
    g = _get(api_client, wired, auth="s-owner", org=SOLO, key=AREA_KEY).json()
    assert "S100" not in [r["employee_id"] for r in g["ask"]["recipients"]]


# ── §E it degrades; a warning never becomes an error ───────────────────────────────────────────
def test_E1_an_unknown_key_is_refused_rather_than_invented(wired, api_client):
    assert _get(api_client, wired, auth="a-admin", org=HOUSE, key="no_such_gap").status_code == 404


def test_E2_an_unreadable_role_table_still_serves_the_steps(wired, api_client, monkeypatch):
    """A gap is a warning. If the lookup behind "who may" fails, the person still gets the steps and
    the honest "ask support" — never a 500 on top of the thing that was already not set up."""
    import app.modules.core.router as r
    monkeypatch.setattr(r, "_role_perms_map", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    res = _get(api_client, wired, auth="a-rep", org=HOUSE, key=AREA_KEY)
    assert res.status_code == 200
    g = res.json()
    assert g["steps"] and g["can_fix"] is False and g["support"] is True


def test_E3_a_caller_with_no_token_gets_the_steps_and_no_permission(wired, api_client, monkeypatch):
    import app.modules.core.router as r
    monkeypatch.setattr(r, "_uid_from_token", lambda *a, **k: None)
    g = api_client.get(GUIDE, params={"key": AREA_KEY, "org_id": HOUSE}).json()
    assert g["steps"] and g["can_fix"] is False


# ── §F the whole registry is reachable, so a page can ask what it needs ────────────────────────
def test_F1_the_index_lists_every_gap_with_whether_this_caller_may_close_it(wired, api_client):
    wired.holder["auth"] = "a-admin"
    res = api_client.get(GUIDE, params={"org_id": HOUSE},
                         headers={"authorization": "Bearer t", "x-active-org": HOUSE})
    assert res.status_code == 200
    body = res.json()
    assert set(body["can_fix"]) == set(sf.FIX_KEYS)
    assert body["can_fix"][PLATFORM_KEY] is False
    assert body["can_fix"][AREA_KEY] is True
