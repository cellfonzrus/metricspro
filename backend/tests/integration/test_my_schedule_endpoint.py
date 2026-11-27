"""Integration: an employee reads their OWN schedule, through the real app, in EVERY tenant.

OWNER (2026-11-27): *"under employee login it does not show the employee thier own schdule"* … *"it
shows your next shift at the bottom on the main page it shoudl also show in schedule whic his made
for teh next 2 weeks or one week"*.

The real FastAPI app over the in-memory double, with the REAL scope code deciding whose people are
whose (`app.core.scope.visible_people_keyset` → `storeops.router.schedule_emp_ids`) and the REAL
`own_emp_ids` deciding which of those are the caller themselves. The unit proof
(`backend/harness_my_schedule.py`) pins the declaration and the pure id-form logic; this pins that a
real request with a real role row lands on the real answer.

TWO TENANTS, DELIBERATELY ON DIFFERENT ID SHAPES, because that is where this defect lived:

  · tenant A stores a shift's `employee_id` as the BUSINESS id ("E100A") — the roster/punch form;
  · tenant B stores it as the NUMERIC `employees.id` ("1B") — the form the Schedule page writes.

A browser-side `String(row.employee_id) === String(login.employee_id)` answers tenant B with ZERO,
which reads as "nothing is scheduled" rather than as a bug. `mine=1` must answer BOTH, and neither
tenant's rows may ever appear in the other's response.
"""
import pytest

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
UID = {"repA": "uid-rep-a", "mgrA": "uid-mgr-a", "repB": "uid-rep-b", "mgrB": "uid-mgr-b",
       "ghost": "uid-ghost", "repAnum": "uid-rep-a-num"}
HDR = {k: f"Bearer {k}" for k in UID}
_BY_TOKEN = {v: UID[k] for k, v in HDR.items()}

WEEK = {"week_start": "2026-12-01", "week_end": "2026-12-14"}


def _people(org, sfx, store):
    return {
        "app_users": [
            {"id": f"au-rep-{sfx}", "org_id": org, "auth_id": UID[f"rep{sfx}"],
             "role": "sales_rep", "employee_id": f"E100{sfx}", "is_active": True},
            {"id": f"au-mgr-{sfx}", "org_id": org, "auth_id": UID[f"mgr{sfx}"],
             "role": "store_manager", "employee_id": f"E200{sfx}", "store_code": store,
             "is_active": True},
        ] + ([
            # THE THIRD ID SHAPE (added 2026-11-27 after the security pass): a login whose OWN
            # `app_users.employee_id` carries the NUMERIC form ("1A") while the shift rows carry the
            # business form ("E100A"). `own_id_forms` resolved only one way at first, so this login
            # read "Not scheduled" every day while its shifts existed — the silent zero the helper
            # exists to kill, re-created inside it.
            {"id": "au-rep-a-num", "org_id": org, "auth_id": UID["repAnum"],
             "role": "sales_rep", "employee_id": "1A", "is_active": True},
        ] if sfx == "A" else []),
        "employees": [
            {"id": f"1{sfx}", "org_id": org, "employee_id": f"E100{sfx}", "name": "The Rep",
             "home_store": store, "is_active": True},
            {"id": f"2{sfx}", "org_id": org, "employee_id": f"E101{sfx}", "name": "A Teammate",
             "home_store": store, "is_active": True},
            {"id": f"3{sfx}", "org_id": org, "employee_id": f"E200{sfx}", "name": "The Manager",
             "home_store": store, "is_active": True},
        ],
        "roles": [
            # Both shapes of "this person is an individual contributor" that exist live.
            {"id": f"r1{sfx}", "org_id": org, "name": "sales_rep",
             "permissions": ({"scope": "self"} if sfx == "A"
                             else {"scope": "store", "people_visibility": "self"})},
            {"id": f"r2{sfx}", "org_id": org, "name": "store_manager",
             "permissions": {"scope": "store", "people_visibility": "span"}},
        ],
    }


def _shift(sid, org, emp, store, date, start="10:00", end="18:00"):
    return {"id": sid, "org_id": org, "employee_id": emp, "store_code": store,
            "shift_date": date, "start_time": start, "end_time": end,
            "scheduled_hours": 8, "status": "scheduled", "is_deleted": False}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.storeops.router.sb",
                        lambda: fake_supabase.schema("storeops"), raising=False)
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.modules.core.router._uid_from_token",
                        lambda a: _BY_TOKEN.get((a or "").strip()))
    fake_supabase.seed("storeops.app_config", [{"id": 1, "rbac_enabled": True}])
    a, b = _people(ORG_A, "A", "S1"), _people(ORG_B, "B", "T1")
    for table in ("app_users", "employees", "roles"):
        fake_supabase.seed(f"storeops.{table}", a[table] + b[table])
    fake_supabase.seed("storeops.stores", [
        {"id": "s1", "org_id": ORG_A, "store_code": "S1", "is_active": True},
        {"id": "t1", "org_id": ORG_B, "store_code": "T1", "is_active": True}])
    fake_supabase.seed("storeops.shifts", [
        # TENANT A — rows keyed by the BUSINESS id.
        _shift("a1", ORG_A, "E100A", "S1", "2026-12-02"),
        _shift("a2", ORG_A, "E100A", "S1", "2026-12-05", "12:00", "20:00"),
        _shift("a3", ORG_A, "E101A", "S1", "2026-12-02"),            # the teammate's
        _shift("a4", ORG_A, "E100A", "S1", "2026-11-20"),            # outside the window
        _shift("a5", ORG_A, "E100A", "S1", "2026-12-03"),
        # TENANT B — rows keyed by the NUMERIC employees.id, which is what the Schedule page writes.
        _shift("b1", ORG_B, "1B", "T1", "2026-12-02"),
        _shift("b2", ORG_B, "2B", "T1", "2026-12-02"),               # the teammate's
        _shift("b3", ORG_B, "3B", "T1", "2026-12-04"),               # the manager's own
    ])
    fake_supabase.seed("storeops.timelog", [])
    return fake_supabase


def _get(client, who, org, **extra):
    q = {**WEEK, **extra, "org_id": org}
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = client.get(f"/api/v1/storeops/shifts?{qs}", headers={"Authorization": HDR[who]})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(rows):
    return sorted(str(r.get("id")) for r in rows)


# ── THE OWNER'S REPORT ───────────────────────────────────────────────────────────────────────────
def test_a_rep_sees_their_own_shifts_when_the_rows_carry_the_business_id(api_client, wired):
    assert _ids(_get(api_client, "repA", ORG_A, mine=1)) == ["a1", "a2", "a5"]


def test_a_rep_sees_their_own_shifts_when_the_rows_carry_the_numeric_id(api_client, wired):
    """THE DEFECT, as a test. Tenant B's shift rows are keyed "1B" while the login carries "E100B";
    a one-form comparison returns nothing at all here, which is what the employee was seeing."""
    assert _ids(_get(api_client, "repB", ORG_B, mine=1)) == ["b1"]


@pytest.mark.parametrize("who,org", [("repA", ORG_A), ("repB", ORG_B)])
def test_mine_is_never_empty_when_the_person_is_scheduled(api_client, who, org, wired):
    """The regression that reproduces the report itself: a scheduled employee asking for their own
    schedule gets rows, in EVERY tenant, whichever id form that tenant's feed happens to use."""
    assert _get(api_client, who, org, mine=1), "a scheduled employee read an EMPTY schedule"


@pytest.mark.parametrize("who,org,alien", [("repA", ORG_A, "a3"), ("repB", ORG_B, "b2")])
def test_a_teammates_shift_is_not_mine(api_client, who, org, alien, wired):
    assert alien not in _ids(_get(api_client, who, org, mine=1))


def test_the_window_still_bounds_the_answer(api_client, wired):
    """`mine` narrows; it does not reach outside the dates asked for."""
    assert "a4" not in _ids(_get(api_client, "repA", ORG_A, mine=1))


# ── A MANAGER ────────────────────────────────────────────────────────────────────────────────────
def test_a_manager_asking_for_mine_gets_only_their_own(api_client, wired):
    """A store manager lands on the same page and is NOT confined, so the grid still shows the team
    — but the page headed "My schedule" must show the manager their own shifts, not the team's."""
    assert _ids(_get(api_client, "mgrB", ORG_B, mine=1)) == ["b3"]


def test_without_mine_the_manager_still_sees_the_team(api_client, wired):
    """Proof `mine` is a NARROWING and changed nothing about the existing read."""
    team = _ids(_get(api_client, "mgrB", ORG_B))
    assert "b1" in team and "b2" in team and "b3" in team, team


# ── POSTURE ──────────────────────────────────────────────────────────────────────────────────────
def test_a_caller_the_token_cannot_name_gets_nothing_rather_than_everything(api_client, wired):
    """`own_emp_ids` is DENY-ALL, the opposite posture to `schedule_emp_ids`. A login with no
    employee row behind it asked for THEIR OWN shifts; the honest answer is none, and the dangerous
    answer — the one `schedule_emp_ids` gives a tokenless caller on purpose — is everyone's."""
    r = api_client.get(
        f"/api/v1/storeops/shifts?{'&'.join(f'{k}={v}' for k, v in WEEK.items())}"
        f"&org_id={ORG_B}&mine=1", headers={"Authorization": HDR["ghost"]})
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── EVERY TENANT, AND NO LEAK BETWEEN THEM ───────────────────────────────────────────────────────
@pytest.mark.parametrize("who,org,foreign", [("repA", ORG_A, "b"), ("repB", ORG_B, "a")])
def test_one_tenants_schedule_never_appears_in_anothers(api_client, who, org, foreign, wired):
    for row_id in _ids(_get(api_client, who, org, mine=1)):
        assert not row_id.startswith(foreign), f"{row_id} is the other tenant's row"


@pytest.mark.parametrize("who,org", [("repA", ORG_B), ("repB", ORG_A)])
def test_a_login_cannot_read_a_schedule_in_a_tenant_it_does_not_belong_to(api_client, who, org,
                                                                          wired):
    """The same login against the OTHER tenant's org_id: no app_user row there, so `own_emp_ids`
    cannot name them and `mine` is deny-all. This is the cross-tenant half of the security pass."""
    assert _get(api_client, who, org, mine=1) == []


# ── REPORTED, NOT HIDDEN ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("who,org,foreign", [("repA", ORG_B, "b"), ("repB", ORG_A, "a")])
def test_the_plain_read_is_where_the_cross_tenant_hole_is(api_client, who, org, foreign, wired):
    """FOUND 2026-11-27 WHILE PROVING THE ABOVE, and reported rather than quietly patched.

    `GET /shifts` WITHOUT `mine` hands a login the OTHER tenant's shifts. The cause is not this
    change: `schedule_emp_ids` returns None — UNRESTRICTED — for a caller it cannot tie to an
    employee (its documented posture, so an in-process/scheduled caller is not silently blanked),
    and a login presenting another tenant's `org_id` has no `app_users` row THERE, so it lands in
    exactly that branch. The store-keyset fallback is skipped because the people answer already
    returned None.

    HOW FAR IT REACHES depends on one env flag, and this test does NOT claim production is open.
    `TenantScopeMiddleware` rewrites a request's `org_id` to one the login is actually a member of —
    and refuses a membership-less login outright — but its whole body sits behind
    `MULTI_TENANT_ENFORCE`, which is off unless an operator sets it. With it ON, the request never
    reaches this handler with a foreign org. With it OFF — this test's posture, and the pass-through
    `docs/SECURITY_CONTROLS_SPEC.md` already files as **P0** ("Enforced only when
    MULTI_TENANT_ENFORCE=1; off by default in some envs") — the handler's own people gate is the last
    line, and it answers "unrestricted".

    So: the flag is the fix and it already has an owner; what this test adds is the proof that the
    handler underneath does NOT independently refuse, so the flag is load-bearing and not a
    belt-and-braces. Asserted rather than asserted away — if this test FAILS, something closed the
    hole and the index should say what. Fixing `schedule_emp_ids`' posture here instead would blank
    every scheduled job that reads shifts, which is exactly why that posture exists.
    """
    rows = _get(api_client, who, org)
    assert any(str(r.get("id")).startswith(foreign) for r in rows), (
        "if this test FAILS the hole is closed — delete it and say so in the index")


# ── THE THIRD ID SHAPE: A LOGIN CARRYING THE NUMERIC FORM ────────────────────────────────────────
def test_a_login_whose_own_id_is_the_numeric_form_still_finds_its_shifts(api_client, wired):
    """`app_users.employee_id` can itself hold the NUMERIC form ("1A") while the shift rows hold the
    business form ("E100A") — the mirror of tenant B's shape. `own_id_forms` resolved only one way
    at first (business in → both out; numeric in → only itself), so this login read an empty
    schedule while its own shifts existed. Both directions now go through
    `business_id_alias_map`, whose collision guard keeps the resolution honest."""
    assert _ids(_get(api_client, "repAnum", ORG_A, mine=1)) == ["a1", "a2", "a5"]


def test_that_login_still_cannot_see_a_teammate(api_client, wired):
    assert "a3" not in _ids(_get(api_client, "repAnum", ORG_A, mine=1))
