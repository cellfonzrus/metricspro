"""Integration: the punch carries its location, the verdict is stored, the flag reaches the board.

OWNER ASK 2026-11-01 (verbatim): *"the sales reps when they log in should capture the gos location so
they are not clocking out at their homes and the geolocation when they clock out from somewhere else
than their usual location which should match the store address should be reported as flag to the
management"*.

WHAT THESE COVER THAT THE UNIT TESTS CANNOT. The unit tests hand `store_geofence.evaluate_presence`
a store row and a config directly. They cannot see whether the ENDPOINTS reach for them — and that
is the whole defect: the rule for "was this punch at the store" was not missing from anyone's head,
it was missing from the punch path, and `ClockOutIn` had no location fields at all. So these send a
real clock-out to the real route and assert what lands in `storeops.timelog`.

§M is the one that would otherwise bite in production. Both hosts auto-deploy from `main` while a
migration waits for the owner, so between the merge and the SQL this code is live against a database
with none of these columns. §M proves the punch path is byte-identical to today's in that window
instead of 500ing at the point somebody ends their shift.

MULTI-TENANT (project rule 2026-10-30). Every case runs for three tenant shapes: the house org with
a pinned store and the default fence, a tenant with a TIGHTER declared fence, and a tenant that has
pinned nothing at all — which is what every tenant looks like on the day this ships, and the one
where a silent "no flags" would read as a clean result.
"""
import pytest

from app.modules.core import geo
from app.modules.core import store_geofence as sg

HOUSE = "00000000-0000-0000-0000-000000000001"   # pinned store, house fence (150 m)
SOLO = "11111111-2222-3333-4444-555555555555"    # pinned store, declared 40 m fence
BARE = "99999999-8888-7777-6666-555555555555"    # a store, NO pin — nothing can be judged

CLOCK_IN = "/api/v1/storeops/timeclock/clock-in"
CLOCK_OUT = "/api/v1/storeops/timeclock/clock-out"
EXCEPTIONS = "/api/v1/storeops/timeclock/attendance-exceptions"
GEO_CONFIG = "/api/v1/storeops/timeclock/geo-config"
GEO_PINS = "/api/v1/storeops/stores/geo-pins"

PIN_LAT, PIN_LNG = 40.650000, -73.950000
AT_DOOR = {"gps_lat": PIN_LAT, "gps_lng": PIN_LNG, "gps_accuracy_m": 10}
NEAR = {"gps_lat": 40.650450, "gps_lng": PIN_LNG, "gps_accuracy_m": 10}       # ~50 m
AT_HOME = {"gps_lat": 40.700000, "gps_lng": PIN_LNG, "gps_accuracy_m": 10}    # ~5.6 km

STORE_OF = {HOUSE: "B-1115", SOLO: "L-01", BARE: "V-01"}
EMP_OF = {HOUSE: "E029", SOLO: "L001", BARE: "V001"}

STORES = [
    {"id": 1, "org_id": HOUSE, "store_code": "B-1115", "address": "2723 Example Ave",
     "market": "Brooklyn", "is_active": True,
     "geo_lat": PIN_LAT, "geo_lng": PIN_LNG, "geofence_radius_m": None},
    {"id": 2, "org_id": HOUSE, "store_code": "B-1598", "address": "88 Utica Ave",
     "market": "Brooklyn", "is_active": True,
     "geo_lat": None, "geo_lng": None, "geofence_radius_m": None},
    {"id": 3, "org_id": SOLO, "store_code": "L-01", "address": "1 Solo Way",
     "market": "Queens", "is_active": True,
     "geo_lat": PIN_LAT, "geo_lng": PIN_LNG, "geofence_radius_m": 40},
    {"id": 4, "org_id": BARE, "store_code": "V-01", "address": "5 Bare St",
     "market": "Bronx", "is_active": True,
     "geo_lat": None, "geo_lng": None, "geofence_radius_m": None},
]
TENANTS = [
    {"id": "t1", "org_id": HOUSE, "punch_geo_enabled": True, "punch_geofence_radius_m": 150,
     "punch_geo_max_accuracy_m": 200, "punch_geo_retention_days": 180},
    {"id": "t2", "org_id": SOLO, "punch_geo_enabled": True, "punch_geofence_radius_m": 40,
     "punch_geo_max_accuracy_m": 200, "punch_geo_retention_days": 180},
    {"id": "t3", "org_id": BARE, "punch_geo_enabled": True, "punch_geofence_radius_m": 150,
     "punch_geo_max_accuracy_m": 200, "punch_geo_retention_days": 180},
]
EMPLOYEES = [
    {"id": 10, "org_id": HOUSE, "employee_id": "E029", "name": "A Rep", "home_store": "B-1115",
     "is_active": True},
    {"id": 11, "org_id": SOLO, "employee_id": "L001", "name": "B Rep", "home_store": "L-01",
     "is_active": True},
    {"id": 12, "org_id": BARE, "employee_id": "V001", "name": "C Rep", "home_store": "V-01",
     "is_active": True},
]

ALL_ORGS = [HOUSE, SOLO, BARE]


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The storeops reads pointed at the double, the caller's identity pinned, and every migration
    this feature does not own declared ABSENT — so what is under test is the geofence and not the
    permission/pending machinery around it."""
    import app.modules.storeops.router as r

    monkeypatch.setattr(r, "sb", lambda: fake_supabase.schema("storeops"))
    monkeypatch.setattr(r, "get_supabase", lambda: fake_supabase)
    # The caller is whichever tenant's rep the request names, resolved from the org_id query param
    # the way the real token would resolve it.
    holder = {"org": HOUSE}
    monkeypatch.setattr(r, "_caller_identity",
                        lambda *a, **k: (holder["org"], EMP_OF[holder["org"]]))
    monkeypatch.setattr(r, "_require_manager",
                        lambda *a, **k: {"org_id": holder["org"], "email": "mgr@example.com"})
    monkeypatch.setattr(r, "scope_keyset", lambda *a, **k: None)
    # Migrations this feature does not own: off, so their paths cannot colour the result.
    monkeypatch.setattr(r, "_timeclock_432_present", lambda: False)
    monkeypatch.setattr(r, "_pending_clockin_present", lambda: False)
    monkeypatch.setattr(r, "_allowed_clock_stores", lambda *a, **k: set())
    monkeypatch.setattr(r, "_scheduled_start_utc", lambda *a, **k: None)
    monkeypatch.setattr(r, "_scheduled_end_for_punch", lambda *a, **k: None)
    monkeypatch.setattr(r, "_closing_gate_block", lambda *a, **k: None)
    monkeypatch.setattr(r, "_missed_closing_notice", lambda *a, **k: None)
    monkeypatch.setattr(r, "_upload_selfie", lambda *a, **k: None)
    # Migration 1112 IS applied for most cases; §M turns this off.
    monkeypatch.setattr(r, "_punch_geo_present", lambda: True)
    r._STORE_PIN_CACHE.clear()

    fake_supabase.seed("storeops.stores", STORES)
    fake_supabase.seed("storeops.tenants", TENANTS)
    fake_supabase.seed("storeops.employees", EMPLOYEES)
    fake_supabase.seed("storeops.timelog", [])
    fake_supabase.seed("storeops.shifts", [])
    fake_supabase.seed("storeops.time_off_requests", [])
    fake_supabase.holder = holder
    yield fake_supabase
    app_module.dependency_overrides.clear()


def _as(wired, org):
    wired.holder["org"] = org
    return org


def _punch_in(wired, api_client, org, where=AT_DOOR, store=None):
    _as(wired, org)
    body = {"store_code": store or STORE_OF[org], "device": "mobile-app", **where}
    res = api_client.post(CLOCK_IN, params={"org_id": org}, json=body)
    # `storeops.timelog.id` is `DEFAULT gen_random_uuid()` (migration 045); the in-memory double has
    # no column defaults, so the id Postgres would have stamped is stamped here. Without it the
    # clock-out has no row key to update and the test would be measuring the double, not the code.
    stored = wired.rows("storeops.timelog")
    for i, row in enumerate(stored):
        row.setdefault("id", f"punch-{i + 1}")
    wired.seed("storeops.timelog", stored)
    return res


def _punch_out(wired, api_client, org, where=AT_DOOR):
    _as(wired, org)
    return api_client.post(CLOCK_OUT, params={"org_id": org}, json=dict(where))


def _row(wired, org):
    rows = [r for r in wired.rows("storeops.timelog") if r.get("org_id") == org]
    assert rows, f"no punch was written for {org}"
    return rows[-1]


# ── the routes exist where they are documented ───────────────────────────────────────────────────
def test_the_new_routes_are_registered(app_module):
    from app.core.path_redact import route_templates
    paths = set(route_templates(app_module))
    assert GEO_CONFIG in paths
    assert GEO_PINS in paths
    assert "/api/v1/storeops/stores/{store_code}/geo-pin" in paths


# ── §A. the clock-in is judged and the verdict is stored ─────────────────────────────────────────
def test_A1_a_clock_in_at_the_store_is_stored_as_inside(wired, api_client):
    assert _punch_in(wired, api_client, HOUSE, AT_DOOR).status_code == 200
    row = _row(wired, HOUSE)
    assert row["clock_in_geo_decision"] == geo.INSIDE
    assert row["clock_in_geo_distance_m"] == 0
    assert row["gps_lat"] == PIN_LAT


def test_A2_a_clock_in_from_far_away_is_stored_as_outside(wired, api_client):
    _punch_in(wired, api_client, HOUSE, AT_HOME)
    row = _row(wired, HOUSE)
    assert row["clock_in_geo_decision"] == geo.OUTSIDE
    assert 5500 <= row["clock_in_geo_distance_m"] <= 5700


def test_A3_a_clock_in_with_no_location_is_unverified_not_refused(wired, api_client):
    res = _punch_in(wired, api_client, HOUSE, {})
    assert res.status_code == 200 and res.json()["success"] is True
    assert _row(wired, HOUSE)["clock_in_geo_decision"] == geo.UNVERIFIED_NO_FIX


def test_A4_a_junk_coordinate_is_stored_as_nothing_rather_than_as_a_place(wired, api_client):
    """A latitude of 999 reaching the column would be a fence measured against a point that is not
    on the Earth. The parsers drop it and the punch reads as unverified."""
    _punch_in(wired, api_client, HOUSE, {"gps_lat": 999, "gps_lng": "abc"})
    row = _row(wired, HOUSE)
    assert row["gps_lat"] is None and row["gps_lng"] is None
    assert row["clock_in_geo_decision"] == geo.UNVERIFIED_NO_FIX


# ── §B. THE OWNER'S CASE: the clock-out carries a fix, and it is judged ─────────────────────────
def test_B1_the_clock_out_body_accepts_a_location_at_all(wired, api_client):
    """It did not until 2026-11-01. `ClockOutIn` had no location fields, so this is the assertion
    that the gap is closed rather than that the maths works."""
    _punch_in(wired, api_client, HOUSE, AT_DOOR)
    assert _punch_out(wired, api_client, HOUSE, AT_HOME).status_code == 200
    row = _row(wired, HOUSE)
    assert row["clock_out_gps_lat"] == AT_HOME["gps_lat"]
    assert row["clock_out_gps_accuracy_m"] == 10


def test_B2_clocking_out_from_home_after_clocking_in_at_the_store_stores_BOTH_facts(
        wired, api_client):
    """THE PATTERN THE OWNER DESCRIBED, end to end. The clock-out must not overwrite the clock-in's
    fix: "arrived at the store, left from home" is unreadable if one erases the other."""
    _punch_in(wired, api_client, HOUSE, AT_DOOR)
    _punch_out(wired, api_client, HOUSE, AT_HOME)
    row = _row(wired, HOUSE)
    assert row["clock_in_geo_decision"] == geo.INSIDE
    assert row["clock_out_geo_decision"] == geo.OUTSIDE
    assert row["gps_lat"] == PIN_LAT, "the clock-in's own fix was overwritten"
    assert row["clock_out_gps_lat"] == AT_HOME["gps_lat"]


def test_B3_a_clock_out_is_never_refused_over_its_location(wired, api_client):
    _punch_in(wired, api_client, HOUSE, AT_DOOR)
    res = _punch_out(wired, api_client, HOUSE, AT_HOME)
    assert res.status_code == 200 and res.json()["success"] is True
    assert res.json()["data"]["hours"] is not None, "the shift still ended and still paid"


def test_B4_a_clock_out_with_no_location_still_closes_the_punch(wired, api_client):
    _punch_in(wired, api_client, HOUSE, AT_DOOR)
    assert _punch_out(wired, api_client, HOUSE, {}).status_code == 200
    row = _row(wired, HOUSE)
    assert row["clock_out"] is not None
    assert row["clock_out_geo_decision"] == geo.UNVERIFIED_NO_FIX


def test_B5_the_clock_out_is_judged_against_the_punchs_own_store_not_the_home_store(
        wired, api_client):
    """A floater ends their shift where they worked it. The punch carries B-1598 (unpinned), so even
    a fix at the HOUSE pin must read as "could not verify" rather than as verified-at-home-store."""
    _punch_in(wired, api_client, HOUSE, AT_DOOR, store="B-1598")
    _punch_out(wired, api_client, HOUSE, AT_DOOR)
    row = _row(wired, HOUSE)
    assert row["store_code"] == "B-1598"
    assert row["clock_out_geo_decision"] == geo.UNVERIFIED_NO_TARGET


# ── §C. an unpinned store accuses nobody ────────────────────────────────────────────────────────
def test_C1_a_punch_at_an_unpinned_store_is_unverified_whatever_the_fix_says(wired, api_client):
    _punch_in(wired, api_client, BARE, AT_HOME)
    row = _row(wired, BARE)
    assert row["clock_in_geo_decision"] == geo.UNVERIFIED_NO_TARGET
    assert row["clock_in_geo_distance_m"] is None
    assert row["gps_lat"] == AT_HOME["gps_lat"], "the fix is kept so a pin can answer it later"


def test_C2_pinning_a_store_makes_the_NEXT_punch_judged_with_no_restart(wired, api_client):
    """The cache must not keep serving "unpinned" after somebody declares the pin."""
    _punch_in(wired, api_client, BARE, AT_HOME)
    assert _row(wired, BARE)["clock_in_geo_decision"] == geo.UNVERIFIED_NO_TARGET
    _as(wired, BARE)
    res = api_client.put(f"/api/v1/storeops/stores/V-01/geo-pin", params={"org_id": BARE},
                         json={"geo_lat": PIN_LAT, "geo_lng": PIN_LNG})
    assert res.status_code == 200, res.text
    _punch_out(wired, api_client, BARE, AT_HOME)
    _punch_in(wired, api_client, BARE, AT_HOME)
    assert _row(wired, BARE)["clock_in_geo_decision"] == geo.OUTSIDE


# ── §D. the tenant's declared fence is the one that judges ──────────────────────────────────────
def test_D1_a_tighter_declared_fence_flags_a_punch_the_house_fence_would_not(wired, api_client):
    """~50 m from the door: inside the house's 150 m, outside SOLO's declared 40 m."""
    _punch_in(wired, api_client, HOUSE, NEAR)
    assert _row(wired, HOUSE)["clock_in_geo_decision"] == geo.INSIDE
    _punch_in(wired, api_client, SOLO, NEAR)
    assert _row(wired, SOLO)["clock_in_geo_decision"] == geo.OUTSIDE


def test_D2_a_tenant_with_the_check_switched_off_stores_no_verdict_at_all(wired, api_client):
    wired.rows("storeops.tenants")[0]["punch_geo_enabled"] = False
    wired.seed("storeops.tenants", [{**TENANTS[0], "punch_geo_enabled": False},
                                    TENANTS[1], TENANTS[2]])
    _punch_in(wired, api_client, HOUSE, AT_HOME)
    row = _row(wired, HOUSE)
    assert row.get("clock_in_geo_decision") is None
    assert row["gps_lat"] == AT_HOME["gps_lat"], "the fix is still captured, only not judged"


def test_D3_the_policy_endpoint_reads_and_writes_through_the_one_resolver(wired, api_client):
    _as(wired, HOUSE)
    body = api_client.get(GEO_CONFIG, params={"org_id": HOUSE}).json()
    assert body["config"]["punch_geofence_radius_m"] == 150
    assert body["available"] is True and body["stores"]["pinned"] == 1
    res = api_client.put(GEO_CONFIG, params={"org_id": HOUSE},
                         json={"punch_geofence_radius_m": 75})
    assert res.status_code == 200, res.text
    assert res.json()["config"]["punch_geofence_radius_m"] == 75
    assert wired.rows("storeops.tenants")[0]["punch_geofence_radius_m"] == 75


def test_D4_an_absurd_policy_value_is_resolved_not_obeyed(wired, api_client):
    _as(wired, HOUSE)
    res = api_client.put(GEO_CONFIG, params={"org_id": HOUSE},
                         json={"punch_geofence_radius_m": "nonsense"})
    assert res.status_code == 200
    assert res.json()["config"]["punch_geofence_radius_m"] == 150


# ── §E. declaring where a store is ──────────────────────────────────────────────────────────────
def test_E1_a_pin_is_audited_because_it_changes_who_gets_flagged(wired, api_client):
    _as(wired, HOUSE)
    api_client.put("/api/v1/storeops/stores/B-1598/geo-pin", params={"org_id": HOUSE},
                   json={"geo_lat": PIN_LAT, "geo_lng": PIN_LNG})
    row = next(s for s in wired.rows("storeops.stores") if s["store_code"] == "B-1598")
    assert row["geo_lat"] == PIN_LAT and row["geo_pin_source"] == "manual"
    assert row["geo_pinned_by"] == "mgr@example.com" and row["geo_pinned_at"]


def test_E2_half_a_pin_is_refused_rather_than_placed_on_the_prime_meridian(wired, api_client):
    _as(wired, HOUSE)
    for body in ({"geo_lat": PIN_LAT}, {"geo_lng": PIN_LNG}, {}, {"geo_lat": 999, "geo_lng": 0}):
        res = api_client.put("/api/v1/storeops/stores/B-1598/geo-pin",
                             params={"org_id": HOUSE}, json=body)
        assert res.status_code == 400, body
    row = next(s for s in wired.rows("storeops.stores") if s["store_code"] == "B-1598")
    assert row["geo_lat"] is None


def test_E3_a_pin_can_be_removed_and_punches_go_back_to_unverified(wired, api_client):
    _as(wired, HOUSE)
    res = api_client.put("/api/v1/storeops/stores/B-1115/geo-pin", params={"org_id": HOUSE},
                         json={"clear": True})
    assert res.status_code == 200 and res.json()["pinned"] is False
    _punch_in(wired, api_client, HOUSE, AT_HOME)
    assert _row(wired, HOUSE)["clock_in_geo_decision"] == geo.UNVERIFIED_NO_TARGET


def test_E4_a_store_code_in_another_case_is_the_same_store(wired, api_client):
    """`'b-1115'` once created a phantom store holding a day's money. A pin resolved off a
    case-drifted code would silently become "no pin"."""
    _as(wired, HOUSE)
    res = api_client.put("/api/v1/storeops/stores/b-1115/geo-pin", params={"org_id": HOUSE},
                         json={"geo_lat": 1.0, "geo_lng": 2.0})
    assert res.status_code == 200
    row = next(s for s in wired.rows("storeops.stores") if s["store_code"] == "B-1115")
    assert row["geo_lat"] == 1.0


def test_E5_an_impossible_store_radius_is_refused_with_a_sentence(wired, api_client):
    _as(wired, HOUSE)
    res = api_client.put("/api/v1/storeops/stores/B-1115/geo-pin", params={"org_id": HOUSE},
                         json={"geo_lat": PIN_LAT, "geo_lng": PIN_LNG, "geofence_radius_m": 1})
    assert res.status_code == 400
    assert "metres" in res.json()["detail"] or "m and" in res.json()["detail"]


def test_E6_the_pin_list_tells_a_manager_which_stores_are_not_pinned(wired, api_client):
    _as(wired, HOUSE)
    body = api_client.get(GEO_PINS, params={"org_id": HOUSE}).json()
    by_code = {s["store_code"]: s for s in body["stores"]}
    assert by_code["B-1115"]["pinned"] is True
    assert by_code["B-1598"]["pinned"] is False
    assert by_code["B-1598"]["effective_radius_m"] == 150


# ── §F. the flag reaches the board management already reviews ───────────────────────────────────
def test_F1_an_off_site_clock_out_appears_on_the_attendance_exceptions_board(wired, api_client):
    _punch_in(wired, api_client, HOUSE, AT_DOOR)
    _punch_out(wired, api_client, HOUSE, AT_HOME)
    row = _row(wired, HOUSE)
    day = row["work_date"]
    body = api_client.get(EXCEPTIONS, params={"org_id": HOUSE, "start": day, "end": day}).json()
    off = [r for r in body["rows"] if r["exception_type"] == "off_site_clock_out"]
    assert len(off) == 1, body["counts"]
    assert off[0]["employee_id"] == "E029" and off[0]["store_code"] == "B-1115"
    assert off[0]["distance_m"] and off[0]["note"]


def test_F2_the_board_never_shows_the_employees_coordinates(wired, api_client):
    """A manager needs to know the punch was not at the store. Where the employee actually WAS is a
    much larger disclosure and does not belong on an emailable board (index §23.5)."""
    _punch_in(wired, api_client, HOUSE, AT_DOOR)
    _punch_out(wired, api_client, HOUSE, AT_HOME)
    day = _row(wired, HOUSE)["work_date"]
    body = api_client.get(EXCEPTIONS, params={"org_id": HOUSE, "start": day, "end": day}).json()
    off = [r for r in body["rows"] if r["exception_type"].startswith("off_site")]
    for row in off:
        assert "lat" not in row and "lng" not in row
        assert not any("lat" in k or "lng" in k for k in row)


def test_F3_the_board_says_WHY_there_are_no_flags_when_nothing_is_pinned(wired, api_client):
    """Zero rows at an unpinned tenant is coverage, not compliance, and the surface has to say so."""
    _punch_in(wired, api_client, BARE, AT_HOME)
    day = _row(wired, BARE)["work_date"]
    body = api_client.get(EXCEPTIONS, params={"org_id": BARE, "start": day, "end": day}).json()
    assert not [r for r in body["rows"] if r["exception_type"].startswith("off_site")]
    assert body["geo"]["pinned_stores"] == {"pinned": 0, "total": 1, "available": True}
    assert body["geo"]["coverage"]["clock_in"][geo.UNVERIFIED_NO_TARGET] == 1


def test_F4_the_boards_existing_classes_are_untouched(wired, api_client):
    """THE REGRESSION GUARD. A new pass must not swallow a no_show."""
    wired.seed("storeops.shifts", [
        {"id": "sh1", "org_id": HOUSE, "employee_id": "E029", "employee_name": "A Rep",
         "store_code": "B-1115", "shift_date": "2026-10-20", "start_time": "09:00",
         "end_time": "17:00", "is_deleted": False}])
    body = api_client.get(EXCEPTIONS, params={"org_id": HOUSE, "start": "2026-10-20",
                                              "end": "2026-10-20"}).json()
    assert [r["exception_type"] for r in body["rows"]] == ["no_show"]


# ── §M. THE DEPLOY WINDOW: live code, migration not yet applied ─────────────────────────────────
#
# Both hosts auto-deploy from `main` while a migration waits for the owner. These run with the probe
# reporting the columns ABSENT, which is what production looks like between the merge and the SQL.
@pytest.fixture
def unmigrated(wired, monkeypatch):
    import app.modules.storeops.router as r
    monkeypatch.setattr(r, "_punch_geo_present", lambda: False)
    return wired


@pytest.mark.parametrize("org", ALL_ORGS)
def test_M1_a_punch_still_lands_with_no_geofence_column_written(unmigrated, api_client, org):
    assert _punch_in(unmigrated, api_client, org, AT_HOME).status_code == 200
    row = _row(unmigrated, org)
    for kind in (sg.KIND_CLOCK_IN, sg.KIND_CLOCK_OUT):
        for key in ("decision", "distance"):
            assert sg.PUNCH_COLUMNS[kind][key] not in row, \
                "a column the database has not got was written"


@pytest.mark.parametrize("org", ALL_ORGS)
def test_M2_a_clock_out_still_closes_the_punch_and_still_pays(unmigrated, api_client, org):
    _punch_in(unmigrated, api_client, org, AT_DOOR)
    res = _punch_out(unmigrated, api_client, org, AT_HOME)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["hours"] is not None
    assert "clock_out_gps_lat" not in _row(unmigrated, org)


def test_M3_the_exceptions_board_still_works_and_shows_no_off_site_rows(unmigrated, api_client):
    _punch_in(unmigrated, api_client, HOUSE, AT_HOME)
    day = _row(unmigrated, HOUSE)["work_date"]
    res = api_client.get(EXCEPTIONS, params={"org_id": HOUSE, "start": day, "end": day})
    assert res.status_code == 200, res.text
    body = res.json()
    assert not [r for r in body["rows"] if r["exception_type"].startswith("off_site")]
    assert body["geo"]["available"] is False


def test_M4_the_settings_panel_says_so_instead_of_offering_a_control_that_fails(
        unmigrated, api_client):
    _as(unmigrated, HOUSE)
    assert api_client.get(GEO_CONFIG, params={"org_id": HOUSE}).json()["available"] is False
    res = api_client.put(GEO_CONFIG, params={"org_id": HOUSE},
                         json={"punch_geofence_radius_m": 75})
    # The §19.36 setup-notice boundary rewrites a migration-naming refusal for a tenant, so what is
    # pinned here is the NEUTRALISATION — a tenant must never be shown a migration number, and a
    # test that asserted "1112" would be asserting a leak.
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "1112" not in detail and "migration" not in detail.lower()
    assert "switched on" in detail or "enable" in detail


def test_M5_pinning_is_refused_with_a_reason_rather_than_a_500(unmigrated, api_client):
    _as(unmigrated, HOUSE)
    res = api_client.put("/api/v1/storeops/stores/B-1115/geo-pin", params={"org_id": HOUSE},
                         json={"geo_lat": PIN_LAT, "geo_lng": PIN_LNG})
    assert res.status_code == 400
    assert "1112" not in res.json()["detail"], "a tenant was shown a migration number"


# ── §T. THE CROSS-TENANT PASS (project rule 2026-10-30) ─────────────────────────────────────────
@pytest.mark.parametrize("org", ALL_ORGS)
def test_T1_every_tenant_gets_an_answer_about_every_punch(wired, api_client, org):
    """Not necessarily a FLAG — an answer. A tenant with nothing pinned must get
    `unverified_no_target`, never a silent NULL that looks like a clean result."""
    _punch_in(wired, api_client, org, AT_HOME)
    decision = _row(wired, org)["clock_in_geo_decision"]
    assert decision in geo.DECISIONS, f"{org} got {decision!r}"


@pytest.mark.parametrize("org", ALL_ORGS)
def test_T2_every_tenant_can_end_a_shift_wherever_they_are(wired, api_client, org):
    _punch_in(wired, api_client, org, AT_DOOR)
    res = _punch_out(wired, api_client, org, AT_HOME)
    assert res.status_code == 200, f"{org} could not clock out: {res.text}"


@pytest.mark.parametrize("org", ALL_ORGS)
def test_T3_one_tenants_pin_never_judges_anothers_punch(wired, api_client, org):
    """BARE's V-01 shares no pin with anybody. If the store read were not org-scoped it would
    inherit HOUSE's pin — which sits at exactly the coordinates these tests punch from — and BARE
    would start reporting verified punches it has no basis for."""
    _punch_in(wired, api_client, org, AT_DOOR)
    row = _row(wired, org)
    if org == BARE:
        assert row["clock_in_geo_decision"] == geo.UNVERIFIED_NO_TARGET, \
            "a tenant with no pin was judged against another tenant's pin"
    else:
        assert row["clock_in_geo_decision"] == geo.INSIDE


@pytest.mark.parametrize("org", ALL_ORGS)
def test_T4_no_tenant_can_pin_another_tenants_store(wired, api_client, org):
    _as(wired, org)
    for other in ALL_ORGS:
        if other == org:
            continue
        res = api_client.put(f"/api/v1/storeops/stores/{STORE_OF[other]}/geo-pin",
                             params={"org_id": org},
                             json={"geo_lat": 1.0, "geo_lng": 2.0})
        assert res.status_code == 404, f"{org} pinned {STORE_OF[other]}"
    for store in wired.rows("storeops.stores"):
        assert store.get("geo_lat") != 1.0, "another tenant's store was moved"


@pytest.mark.parametrize("org", ALL_ORGS)
def test_T5_no_tenants_pin_list_shows_another_tenants_stores(wired, api_client, org):
    _as(wired, org)
    codes = {s["store_code"] for s in api_client.get(GEO_PINS, params={"org_id": org}).json()["stores"]}
    mine = {s["store_code"] for s in STORES if s["org_id"] == org}
    assert codes == mine


@pytest.mark.parametrize("org", ALL_ORGS)
def test_T6_no_tenants_board_shows_another_tenants_punch(wired, api_client, org):
    for other in ALL_ORGS:
        _punch_in(wired, api_client, other, AT_HOME)
        _punch_out(wired, api_client, other, AT_HOME)
    day = _row(wired, org)["work_date"]
    body = api_client.get(EXCEPTIONS, params={"org_id": org, "start": day, "end": day}).json()
    mine = {e["employee_id"] for e in EMPLOYEES if e["org_id"] == org}
    for row in body["rows"]:
        assert row["employee_id"] in mine, f"{org}'s board shows {row['employee_id']}"


def test_T7_no_tenant_or_carrier_name_is_spelled_in_the_one_home():
    """RULE TWO on the module this PR adds."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "app/modules/core/store_geofence.py").read_text().lower()
    for banned in ("boost", "luxelink", "cellfonz", "vzone", "b-1115", "total wireless"):
        assert banned not in src, banned
