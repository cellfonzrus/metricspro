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
import re

import pytest

from app.core import rep_surface

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
    # `_require_manager` resolves the caller through this one home (it is org-independent on
    # purpose: a manager in ANY tenant may answer). Point it at the double so a manager-gated
    # route is testable rather than 500ing on a real client.
    monkeypatch.setattr(
        "app.core.tenant_middleware.caller_app_user_http",
        lambda uid, cols="*": next(
            (u for u in (fake_supabase.schema("storeops").table("app_users").select("*")
                         .execute().data or []) if u.get("auth_id") == uid), None))
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
    # `/me` is part of this gate's surface — it is the payload the landing turns into links — so the
    # core router reads the double here rather than a live client.
    monkeypatch.setattr("app.modules.core.router.sb", lambda: wired, raising=False)
    monkeypatch.setattr("app.core.tenant_middleware._rep_cache",
                        type(__import__("app.core.tenant_middleware", fromlist=["x"])._rep_cache)(
                            ttl=0.001, cap=16))
    # The ROLE's own switches are cached beside the self-scope answer, for the same reason; a test
    # that changes a roles row must see the change, so this copy expires immediately too.
    monkeypatch.setattr("app.core.tenant_middleware._rep_perms_cache",
                        type(__import__("app.core.tenant_middleware", fromlist=["x"])._rep_perms_cache)(
                            ttl=0.001, cap=16))
    monkeypatch.setattr("app.modules.storeops.router.sb", lambda: wired.schema("storeops"),
                        raising=False)
    return wired


def _switch_off(wired, org, role, page):
    """Turn one page off for one role, exactly as the Roles screen does — a `pages` entry of False
    on that role's permissions row. Nothing else in the tenant changes."""
    rows = wired.schema("storeops").table("roles").select("*").eq("org_id", org).execute().data or []
    row = next(r for r in rows if r.get("name") == role)
    perms = dict(row.get("permissions") or {})
    perms["pages"] = {**(perms.get("pages") or {}), page: False}
    (wired.schema("storeops").table("roles").update({"permissions": perms})
     .eq("org_id", org).eq("name", role).execute())


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


# ── THE TILE A CONFINED EMPLOYEE TAPS MUST BE AN ADDRESS ─────────────────────────────────────────
# OWNER, 2026-11-27: *"when the employee clicks on my commisison in Ny logistics it leads to this
# 404"*, then *"when you clock on clock in clock out from the app.metricspro.tech it shows the same
# 404 … if it broken platform wide fix that aswell"*.
#
# A confined employee has NO MENU: the landing's tiles are the only way they reach anything. The
# tiles were built from the surface's permission PATTERN (`"/employee/**"`), which is not an address,
# so the browser navigated to the literal `/employee/**` and the app answered 404 — for every
# tenant. Proved here through the REAL `/me`, for BOTH tenants, because the payload is what the
# browser turns into a link.
_PATTERN = re.compile(r"[*?]")


@pytest.mark.parametrize("who,org", [("repA", ORG_A), ("repB", ORG_B)])
def test_every_tile_the_landing_is_given_is_a_navigable_address(api_client, gate, who, org):
    r = api_client.get(f"/api/v1/core/me?org_id={org}", headers={"Authorization": HDR[who]})
    assert r.status_code == 200, r.text
    surface = (r.json().get("permissions") or {}).get("rep_surface") or {}
    tiles = surface.get("tiles") or []
    assert tiles, "a confined employee with no tiles has no way to reach anything"
    for t in tiles:
        page = t.get("page") or ""
        assert page.startswith("/"), f"{t.get('key')} published no address: {page!r}"
        # THE DEFECT: a permission pattern handed to the browser as a link.
        assert not _PATTERN.search(page), f"{t.get('key')} published a pattern as its link: {page!r}"
        # …and the address is one the same declaration admits, so the tile cannot be a dead end.
        assert rep_surface.page_allowed(page), f"{t.get('key')} links somewhere it may not open"


@pytest.mark.parametrize("who,org", [("repA", ORG_A), ("repB", ORG_B)])
def test_the_two_reported_tiles_land_where_the_owner_expects(api_client, gate, who, org):
    """My commissions opens the employee portal; Clock in / out opens the punch kiosk — not the
    landing the tile sits on, which is what guessing the surface's FIRST grant produced."""
    r = api_client.get(f"/api/v1/core/me?org_id={org}", headers={"Authorization": HDR[who]})
    by_key = {t["key"]: t.get("page") for t in
              ((r.json().get("permissions") or {}).get("rep_surface") or {}).get("tiles") or []}
    assert by_key.get("commissions") == "/employee"
    assert by_key.get("clock") == "/portal"
    # OWNER 2026-11-27: *"daily closing tile is missing from their landing page but on teh portal is
    # seen"* — the form existed only as a panel inside the kiosk (§CORE-33.4).
    assert by_key.get("closing") == "/closing/submit"
    # OWNER 2026-11-27: *"my targets are shown on the portal and not on the employee dashboad, a tile
    # for that shoudl be there too"* — target-vs-achieved lived only in the kiosk's dashboard tab.
    assert by_key.get("targets") == "/commcalc/targets/my"


def test_both_tenants_are_given_the_same_tiles(api_client, gate):
    """House policy, not a tenant's vocabulary: the tiles are a declaration with no org in it, so
    one tenant's landing can never differ from another's (CLAUDE.md, 2026-10-30)."""
    def tiles(who, org):
        r = api_client.get(f"/api/v1/core/me?org_id={org}", headers={"Authorization": HDR[who]})
        return [(t["key"], t.get("page")) for t in
                ((r.json().get("permissions") or {}).get("rep_surface") or {}).get("tiles") or []]
    assert tiles("repA", ORG_A) == tiles("repB", ORG_B)


@pytest.mark.parametrize("who", ["repA", "repB"])
def test_the_closing_tile_opened_one_page_and_widened_no_api(api_client, gate, who):
    """The Daily closing grant is that ONE page: `/closing` is a 30-screen subsystem and none of it
    moved. Asked of the API, because the API is what enforces it (§CORE-33) — the kiosk's own calls
    are allowed and the tender CONFIG write, which the first draft of this module admitted, is not."""
    allowed = api_client.get(f"/api/v1/closing/days?org_id={ORG_A}", headers={"Authorization": HDR[who]})
    assert allowed.status_code != 403, allowed.text
    for path, method in (("/api/v1/closing/tender-config", "put"),
                         ("/api/v1/closing/count-config", "post")):
        r = getattr(api_client, method)(f"{path}?org_id={ORG_A}", json={},
                                        headers={"Authorization": HDR[who]})
        assert r.status_code == 403, f"{method.upper()} {path}: {r.status_code}"
        assert r.json()["code"] == "not_your_access"
    assert rep_surface.page_allowed("/closing/submit")
    assert not any(rep_surface.page_allowed(p) for p in
                   ("/closing", "/closing/recon", "/closing/verify", "/closing/management"))


@pytest.mark.parametrize("who", ["repA", "repB"])
def test_the_targets_tile_opened_one_page_and_widened_no_api(api_client, gate, who):
    """The My targets grant is that ONE page. The reads behind it were ALREADY granted (the
    action-plan surface grants read on `/commcalc/targets`), so nothing on the API moved — asserted
    both ways: the read is allowed and the write that would change a target is not."""
    r = api_client.get(f"/api/v1/commcalc/targets/November%202026/summary?org_id={ORG_A}",
                       headers={"Authorization": HDR[who]})
    assert r.status_code != 403, r.text
    w = api_client.post(f"/api/v1/commcalc/targets?org_id={ORG_A}", json={},
                        headers={"Authorization": HDR[who]})
    assert w.status_code == 403 and w.json()["code"] == "not_your_access", w.text
    assert rep_surface.page_allowed("/commcalc/targets/my")
    assert not any(rep_surface.page_allowed(p) for p in
                   ("/commcalc/targets", "/commcalc/targets/settings",
                    "/commcalc/targets/report-cards", "/commcalc/targets/rep-map"))


@pytest.mark.parametrize("who,org", [("repA", ORG_A), ("repB", ORG_B)])
def test_nothing_is_granted_a_page_and_left_without_a_tile(api_client, gate, who, org):
    """A confined employee has no menu, so a granted page with no tile can only be reached by typing
    its address. Asked of the payload the browser actually gets: every page the gate admits is either
    a tile's destination or below one."""
    r = api_client.get(f"/api/v1/core/me?org_id={org}", headers={"Authorization": HDR[who]})
    surface = (r.json().get("permissions") or {}).get("rep_surface") or {}
    destinations = [t.get("page") or "" for t in surface.get("tiles") or []]
    landing = surface.get("landing") or "/my"
    # DECLARED, not counted. Pages reached by a LINK from a tile's destination rather than by a tile
    # of their own: the two REQUEST forms behind My schedule. `/my/schedule` (what the tile opens)
    # links to both — "Ask for time off" and "Ask for a shift swap" — and the Time Off page carries a
    # "Shift swaps" button as well. All three links are pinned by rep-surface-nav-proof.mjs §E.
    LINKED_FROM_A_TILE = {"/storeops/timeoff", "/storeops/swaps"}
    for entry in surface.get("pages") or []:
        base = rep_surface.tile_page(entry)
        if base == landing or base in LINKED_FROM_A_TILE:
            continue  # the landing is where the tiles ARE, not somewhere a tile leads
        assert any(d == base or d.startswith(base + "/") or base.startswith(d) for d in destinations), \
            f"{entry} is granted but no tile leads to it"


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


# ── THE ASK IS NOT THE ANSWER ────────────────────────────────────────────────────────────────────
# Found by the security pass on this change (2026-11-04). The declaration granted the time-off and
# shift-swap COLLECTIONS because an employee ASKS there with a POST, and the grant carried every
# path below them — including the manager's PATCH, whose handler had no authority check at all. A
# rep could approve their own leave, and approve a swap that moves someone else's shift. Both halves
# are proved here: the DECLARATION refuses the path, and the HANDLER refuses the write even when the
# middleware is not what stops it.

def _seed_timeoff(wired, rid, org, eid, status="pending"):
    """Seeded with an explicit INTEGER id, because the route's path parameter is an int — the
    double's generated UUID would 422 before the gate is ever reached."""
    wired.schema("storeops").table("time_off_requests").insert(
        {"id": rid, "org_id": org, "employee_id": eid, "start_date": "2026-12-01",
         "end_date": "2026-12-02", "status": status}).execute()
    return rid


@pytest.mark.parametrize("who,org,eid,rid", [("repA", ORG_A, "E100A", 41), ("repB", ORG_B, "E100B", 42)])
def test_a_rep_cannot_approve_their_own_time_off(api_client, wired, who, org, eid, rid):
    """The reported shape: it is their OWN request, whose id they hold — and they still cannot
    answer it. Answering is the manager's act."""
    _seed_timeoff(wired, rid, org, eid)
    r = api_client.patch(f"/api/v1/storeops/time-off/{rid}?org_id={org}",
                         headers={"Authorization": HDR[who]}, json={"status": "approved"})
    assert r.status_code == 403, r.text
    rows = (wired.schema("storeops").table("time_off_requests").select("status")
            .eq("id", rid).execute().data)
    assert rows and rows[0]["status"] != "approved", "the approval must not have landed"


@pytest.mark.parametrize("org,mgr,eid,rid", [(ORG_A, "mgrA", "E101A", 43), (ORG_B, "mgrB", "E101B", 44)])
def test_the_manager_answers_it_for_their_own_employee(api_client, wired, org, mgr, eid, rid):
    _seed_timeoff(wired, rid, org, eid)
    r = api_client.patch(f"/api/v1/storeops/time-off/{rid}?org_id={org}",
                         headers={"Authorization": HDR[mgr]}, json={"status": "approved"})
    assert r.status_code == 200, r.text


def test_a_manager_cannot_answer_for_someone_who_is_not_theirs(api_client, wired):
    _seed_timeoff(wired, 45, ORG_A, "E999A")
    r = api_client.patch(f"/api/v1/storeops/time-off/45?org_id={ORG_A}",
                         headers={"Authorization": HDR["mgrA"]}, json={"status": "approved"})
    assert r.status_code == 403 and "not in your team" in r.json()["detail"], r.text


def test_one_tenant_s_manager_cannot_answer_the_other_tenant_s_request(api_client, wired):
    _seed_timeoff(wired, 46, ORG_B, "E101B")
    r = api_client.patch(f"/api/v1/storeops/time-off/46?org_id={ORG_B}",
                         headers={"Authorization": HDR["mgrA"]}, json={"status": "approved"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("who,org,sid,expect", [("repA", ORG_A, 51, 403), ("mgrA", ORG_A, 52, 200)])
def test_only_a_manager_answers_a_shift_swap(api_client, wired, who, org, sid, expect):
    """Approving reassigns another person's shift, so it is a manager's act whoever raised it."""
    wired.schema("storeops").table("shift_swap_requests").insert(
        {"id": sid, "org_id": org, "requester_id": "E101A", "status": "pending"}).execute()
    r = api_client.patch(f"/api/v1/storeops/shift-swaps/{sid}?org_id={org}",
                         headers={"Authorization": HDR[who]}, json={"status": "denied"})
    assert r.status_code == expect, r.text


def test_a_rep_cannot_run_the_org_wide_time_off_cleanup(api_client, wired):
    r = api_client.post(f"/api/v1/storeops/time-off/reconcile-duplicates?org_id={ORG_A}",
                        headers={"Authorization": HDR["repA"]})
    assert r.status_code == 403, r.text


def test_the_manager_can_run_it(api_client, wired):
    r = api_client.post(f"/api/v1/storeops/time-off/reconcile-duplicates?org_id={ORG_A}",
                        headers={"Authorization": HDR["mgrA"]})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path,method", [
    ("/api/v1/storeops/time-off/41", "PATCH"),
    ("/api/v1/storeops/time-off/reconcile-duplicates", "POST"),
    ("/api/v1/storeops/shift-swaps/7", "PATCH"),
    ("/api/v1/storeops/shift-extensions/9/decision", "POST"),
    ("/api/v1/helpdesk/config/teams", "POST"),
    ("/api/v1/helpdesk/support/sla-policy", "PUT"),
    ("/api/v1/closing/tender-config", "PUT"),
    ("/api/v1/closing/row/9", "DELETE"),
    ("/api/v1/storeops/timeclock/geo-config", "PUT"),
    ("/api/v1/chat/admin/retention", "POST"),
])
def test_the_always_on_gate_refuses_every_carved_route(api_client, gate, path, method):
    """The middleware's own refusal, with MULTI_TENANT_ENFORCE absent — the declaration is what
    answers, before any handler is reached."""
    r = api_client.request(method, path, headers={"Authorization": HDR["repA"]}, json={})
    assert r.status_code == 403, r.text
    assert r.json().get("code") == "not_your_access", r.text


@pytest.mark.parametrize("path,method", [
    ("/api/v1/storeops/time-off", "POST"),
    ("/api/v1/storeops/shift-swaps", "POST"),
    ("/api/v1/storeops/shift-extensions", "POST"),
    ("/api/v1/closing/row", "POST"),
    ("/api/v1/closing/tender-config", "GET"),
    ("/api/v1/helpdesk/tickets", "POST"),
    ("/api/v1/storeops/timeclock/clock-in", "POST"),
    ("/api/v1/storeops/timeclock/override", "POST"),
])
def test_the_ask_is_never_refused_by_the_gate(api_client, gate, path, method):
    """★ NO LOCKOUT ★ — carving the answer must not refuse the ask, nor the kiosk's own calls."""
    r = api_client.request(method, path, headers={"Authorization": HDR["repA"]}, json={})
    assert not (r.status_code == 403 and r.json().get("code") == "not_your_access"), r.text


# ── THE CARD THAT COULD NOT LOAD (owner 2026-11-27, §CORE-33.6) ──────────────────────────────────
@pytest.mark.parametrize("who", ["repA", "repB"])
@pytest.mark.parametrize("api", ["/api/v1/commcalc/my-shelf",
                                 "/api/v1/helpdesk/config/bootstrap",
                                 "/api/v1/storeops/timeoff-conflict-mode",
                                 "/api/v1/storeops/employees/visible"])
def test_the_reads_a_reps_own_screens_make_are_not_refused(api_client, gate, who, api):
    """Every one of these is a card or a drop-down ON a page the rep is granted. The gate refused
    four of them, which is what the owner saw in a red box on his own dashboard."""
    r = api_client.get(api, headers={"Authorization": HDR[who]})
    assert not (r.status_code == 403 and r.json().get("code") == "not_your_access"), r.text


@pytest.mark.parametrize("who", ["repA", "repB"])
@pytest.mark.parametrize("path,method", [("/api/v1/commcalc/my-shelf", "POST"),
                                         ("/api/v1/helpdesk/config/settings", "PUT"),
                                         ("/api/v1/helpdesk/config/categories", "POST"),
                                         ("/api/v1/helpdesk/stats/dashboard", "GET"),
                                         ("/api/v1/storeops/timeoff-conflict-mode", "PUT"),
                                         ("/api/v1/storeops/employees", "GET")])
def test_the_reads_did_not_widen_into_writes_or_rosters(api_client, gate, who, path, method):
    """A read granted for a card must not carry the write beside it, nor the personnel roster."""
    r = api_client.request(method, path, headers={"Authorization": HDR[who]}, json={})
    assert r.status_code == 403, r.text
    assert r.json().get("code") == "not_your_access", r.text


# ── A GRANT IS A MAXIMUM, NOT A MANDATE (owner 2026-11-27, §CORE-33.6) ───────────────────────────
@pytest.mark.parametrize("who,org", [("repA", ORG_A), ("repB", ORG_B)])
def test_the_roles_screen_switch_closes_a_tile_for_that_tenant(api_client, gate, who, org):
    """*"i disaables my targets from ny logistics sales reps but it still shows up"*. Switched off on
    the Roles screen, the tile must not be published, the page must not be admitted, and the card's
    API must refuse — for the tenant that switched it and NOT for the other one."""
    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR[who]},
                        params={"org_id": org})
    assert me.status_code == 200, me.text
    before = me.json()["permissions"]["rep_surface"]
    assert "targets" in [t["key"] for t in before["tiles"]]
    assert "/commcalc/targets/my" in before["pages"]

    _switch_off(gate, org, "sales_rep", "/commcalc/targets/my")

    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR[who]},
                        params={"org_id": org})
    after = me.json()["permissions"]["rep_surface"]
    assert "targets" not in [t["key"] for t in after["tiles"]], after["tiles"]
    assert "/commcalc/targets/my" not in after["pages"]
    assert len(after["tiles"]) == len(before["tiles"]) - 1
    # The shelf card's read is declared by the shelf's OWN page too (§CORE-33.7), so closing the
    # targets page alone must NOT close it — and closing both must.
    r = api_client.get("/api/v1/commcalc/my-shelf", headers={"Authorization": HDR[who]})
    assert not (r.status_code == 403 and r.json().get("code") == "not_your_access"), r.text
    _switch_off(gate, org, "sales_rep", "/commcalc/asset/my-shelf")
    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR[who]},
                        params={"org_id": org})
    assert "shelf" not in [t["key"] for t in me.json()["permissions"]["rep_surface"]["tiles"]]
    r = api_client.get("/api/v1/commcalc/my-shelf", headers={"Authorization": HDR[who]})
    assert r.status_code == 403 and r.json().get("code") == "not_your_access", r.text


def test_one_tenants_switch_never_reaches_the_other(api_client, gate):
    """Tenant A switches My targets off; tenant B's reps still have it, tile, page and card."""
    _switch_off(gate, ORG_A, "sales_rep", "/commcalc/targets/my")
    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR["repB"]},
                        params={"org_id": ORG_B})
    rs = me.json()["permissions"]["rep_surface"]
    assert "targets" in [t["key"] for t in rs["tiles"]]
    assert "/commcalc/targets/my" in rs["pages"]
    r = api_client.get("/api/v1/commcalc/my-shelf", headers={"Authorization": HDR["repB"]})
    assert not (r.status_code == 403 and r.json().get("code") == "not_your_access"), r.text


def test_a_switch_only_subtracts_and_never_opens_a_manager_screen(api_client, gate):
    """Ticking a page ON for a confined role grants nothing — widening by configuration is the
    defect §CORE-33 exists to prevent, and the API would refuse what the menu offered."""
    rows = (gate.schema("storeops").table("roles").select("*").eq("org_id", ORG_A)
            .execute().data or [])
    row = next(r for r in rows if r.get("name") == "sales_rep")
    perms = {**(row.get("permissions") or {}),
             "pages": {"/storeops/payroll": True, "/commcalc/targets/settings": True}}
    (gate.schema("storeops").table("roles").update({"permissions": perms})
     .eq("org_id", ORG_A).eq("name", "sales_rep").execute())
    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR["repA"]},
                        params={"org_id": ORG_A})
    rs = me.json()["permissions"]["rep_surface"]
    assert "/storeops/payroll" not in rs["pages"]
    assert "/commcalc/targets/settings" not in rs["pages"]
    r = api_client.get("/api/v1/storeops/payroll", headers={"Authorization": HDR["repA"]})
    assert r.status_code == 403 and r.json().get("code") == "not_your_access", r.text


def test_the_shell_survives_every_switch(api_client, gate):
    """★ NO LOCKOUT ★ — with every tiled page switched off, the person can still sign in, read who
    they are and change their own password."""
    for page in [rep_surface.tile_page(p) for p in rep_surface.pages()]:
        _switch_off(gate, ORG_A, "sales_rep", page)
    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR["repA"]},
                        params={"org_id": ORG_A})
    assert me.status_code == 200, me.text
    rs = me.json()["permissions"]["rep_surface"]
    assert "/account/password/**" in rs["pages"], rs["pages"]
    assert "password" in [t["key"] for t in rs["tiles"]]


# ── A REP'S TILES FOLLOW THE TENANT'S BUSINESS TYPE (owner 2026-11-27, §CORE-33.7) ───────────────
# *"barcode resue does not belong to the wireless stors it is only for the ups store, also the device
# history is also missing from alis dashbpard"*. Tenant B is given a business type that hides two
# pages; tenant A is left on the default. Nothing in this file names a vertical the platform ships —
# the point is that the TENANT'S OWN ROW decides, so the test declares its own.
HIDES = ["/commcalc/asset/my-shelf", "/commcalc/device-history"]


@pytest.fixture
def business_types(monkeypatch, gate):
    gate.seed("core.tenant_vertical", [
        {"key": "sells_phones", "label": "Sells phones", "is_default": True, "uses_carriers": True,
         "nav_hidden": [], "sort_order": 10},
        {"key": "sells_no_phones", "label": "Sells no phones", "is_default": False,
         "uses_carriers": False, "nav_hidden": HIDES, "sort_order": 20}])
    gate.seed("storeops.tenants", [{"org_id": ORG_A, "name": "A", "vertical": None},
                                   {"org_id": ORG_B, "name": "B", "vertical": "sells_no_phones"}])
    monkeypatch.setattr("app.core.database.get_supabase", lambda: gate, raising=False)
    monkeypatch.setattr("app.modules.core.verticals.closing_sections", lambda: [], raising=False)
    return gate


def _tiles(api_client, who, org):
    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR[who]}, params={"org_id": org})
    assert me.status_code == 200, me.text
    return [t["key"] for t in me.json()["permissions"]["rep_surface"]["tiles"]]


def test_a_business_type_that_hides_a_page_has_no_tile_for_it(api_client, business_types):
    assert "shelf" in _tiles(api_client, "repA", ORG_A)
    assert "device_history" in _tiles(api_client, "repA", ORG_A)
    assert "shelf" not in _tiles(api_client, "repB", ORG_B)
    assert "device_history" not in _tiles(api_client, "repB", ORG_B)


def test_and_the_api_follows_the_tile_for_each_tenant(api_client, business_types):
    """The half that matters: hiding the screen must also refuse its read, or the browser and the
    server disagree again — in the direction that leaves a page nobody can see still answering."""
    r = api_client.get("/api/v1/commcalc/device-history?q=1", headers={"Authorization": HDR["repB"]})
    assert r.status_code == 403 and r.json().get("code") == "not_your_access", r.text
    r = api_client.get("/api/v1/commcalc/device-history?q=1", headers={"Authorization": HDR["repA"]})
    assert not (r.status_code == 403 and r.json().get("code") == "not_your_access"), r.text


def test_the_business_type_never_takes_the_shell_away(api_client, business_types):
    """★ NO LOCKOUT ★ — whatever a business type hides, a rep of that tenant still signs in."""
    me = api_client.get("/api/v1/core/me", headers={"Authorization": HDR["repB"]},
                        params={"org_id": ORG_B})
    assert me.status_code == 200, me.text
    rs = me.json()["permissions"]["rep_surface"]
    assert "password" in [t["key"] for t in rs["tiles"]]
    assert "clock" in [t["key"] for t in rs["tiles"]]
