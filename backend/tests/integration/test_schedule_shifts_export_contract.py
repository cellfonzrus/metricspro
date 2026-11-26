"""Integration: the week the schedule EXPORT is printed from (index §STOREOPS-13).

Real routing, the real router body, over the in-memory database double, with conftest blocking every
outbound socket. There is no staging Supabase, so "integration" here means the app, not a real
database (docs/TESTING.md).

WHY THIS TEST EXISTS. The printed schedule was redesigned on 2026-11-26 to one LINE per person —
owner: *"the schdule show show line by line which employee is working and thier houre next to their
name instaed of in this illegible form"*. The new line builder
(`frontend/.../storeops/lib/scheduleExport.ts`) is pure and proved offline by its own vitest suite,
but its proof can only assume the rows it is handed. This is the other half: that `GET
/storeops/shifts` — the ONE read behind the schedule screen and therefore behind the export, the
emailed PDF and the scheduled send — really does return each of the six fields a line is built
from, per shift row rather than pre-grouped per store-day.

The defect it forecloses is not hypothetical in shape: a response that grouped, flattened or dropped
`employee_name` would put the export straight back to a store-day cell holding a list of names,
which is exactly the illegible form that was reported.

MULTI-TENANT (project rule, 2026-10-30). Every case runs for THREE orgs with different shapes: a
tenant whose people work several stores, a tenant with one store, and a tenant with employees on the
roster and NO shifts that week. The last is not an edge case — it is a tenant's first week, and it
is where a "nobody scheduled" line has to come from an empty list rather than from another tenant's
rows.
"""
import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"     # several stores, people who move between them
SOLO = "11111111-2222-3333-4444-555555555555"      # one store
BARE = "99999999-8888-7777-6666-555555555555"      # no shifts at all this week
SHIFTS_PATH = "/api/v1/storeops/shifts"

WEEK_START, WEEK_END = "2026-11-23", "2026-11-29"

# The six fields a printed line is built from. Named here, once, so a route that stops returning one
# of them fails with the reason rather than with a KeyError somewhere downstream.
LINE_FIELDS = ("employee_name", "store_code", "shift_date", "start_time", "end_time",
               "scheduled_hours")

SHIFTS = [
    # HOUSE — two people at one store on the same day (the cell that used to hold both names), one
    # person who works TWO stores in the week, and a split shift (two rows, one person, one day).
    {"id": "h1", "org_id": HOUSE, "employee_id": "E1", "employee_name": "Rep One",
     "store_code": "B-1598", "shift_date": "2026-11-23", "start_time": "16:00",
     "end_time": "19:00", "scheduled_hours": 3, "is_deleted": False},
    {"id": "h2", "org_id": HOUSE, "employee_id": "E2", "employee_name": "Rep Two",
     "store_code": "B-1598", "shift_date": "2026-11-23", "start_time": "09:00",
     "end_time": "19:00", "scheduled_hours": 10, "is_deleted": False},
    {"id": "h3", "org_id": HOUSE, "employee_id": "E1", "employee_name": "Rep One",
     "store_code": "B-117", "shift_date": "2026-11-25", "start_time": "10:00",
     "end_time": "16:00", "scheduled_hours": 6, "is_deleted": False},
    {"id": "h4", "org_id": HOUSE, "employee_id": "E2", "employee_name": "Rep Two",
     "store_code": "B-1598", "shift_date": "2026-11-26", "start_time": "09:00",
     "end_time": "12:00", "scheduled_hours": 3, "is_deleted": False},
    {"id": "h5", "org_id": HOUSE, "employee_id": "E2", "employee_name": "Rep Two",
     "store_code": "B-1598", "shift_date": "2026-11-26", "start_time": "15:00",
     "end_time": "19:00", "scheduled_hours": 4, "is_deleted": False},
    # A deleted row, and a row OUTSIDE the week: neither may reach a printed line.
    {"id": "h6", "org_id": HOUSE, "employee_id": "E3", "employee_name": "Deleted Rep",
     "store_code": "B-1598", "shift_date": "2026-11-24", "start_time": "09:00",
     "end_time": "17:00", "scheduled_hours": 8, "is_deleted": True},
    {"id": "h7", "org_id": HOUSE, "employee_id": "E1", "employee_name": "Rep One",
     "store_code": "B-1598", "shift_date": "2026-12-07", "start_time": "09:00",
     "end_time": "17:00", "scheduled_hours": 8, "is_deleted": False},
    # SOLO — one store, an OVERNIGHT shift whose stored hours are wrong. The line builder recomputes
    # from the times, which it can only do because both times come back.
    {"id": "s1", "org_id": SOLO, "employee_id": "L1", "employee_name": "Night Rep",
     "store_code": "L-01", "shift_date": "2026-11-23", "start_time": "21:00",
     "end_time": "05:00", "scheduled_hours": -16, "is_deleted": False},
]


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The schedule read pointed at the double, with the person gate lifted. WHO may read a week is
    §14w's job and has its own tests; what is under test here is the SHAPE of what comes back."""
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.modules.storeops.router.gate_person_rows",
                        lambda rows, *a, **k: rows)
    fake_supabase.seed("storeops.shifts", SHIFTS)
    yield fake_supabase
    app_module.dependency_overrides.clear()


def _week(api_client, org, **extra):
    return api_client.get(SHIFTS_PATH, params={"org_id": org, "week_start": WEEK_START,
                                               "week_end": WEEK_END, **extra}).json()


# ── the route the export is printed from exists where it is documented ───────────────────────────
def test_the_shifts_route_is_registered(app_module):
    from app.core.path_redact import route_templates
    assert SHIFTS_PATH in set(route_templates(app_module))


# ── §A. a row per SHIFT, carrying every field a printed line needs ───────────────────────────────
def test_A1_every_row_carries_all_six_fields_a_printed_line_is_built_from(wired, api_client):
    rows = _week(api_client, HOUSE)
    assert rows, "the week came back empty — a printed schedule would have nothing to print"
    for r in rows:
        missing = [f for f in LINE_FIELDS if f not in r]
        assert not missing, f"the export cannot build a line without {missing}"


def test_A2_the_response_is_one_row_per_shift_never_pre_grouped_per_store_day(wired, api_client):
    rows = _week(api_client, HOUSE)
    # Two people at B-1598 on 11/23 come back as TWO rows. One grouped row holding both names is the
    # illegible form the redesign removed, and a route that grouped would restore it.
    monday = [r for r in rows if r["shift_date"] == "2026-11-23" and r["store_code"] == "B-1598"]
    assert len(monday) == 2
    assert sorted(r["employee_name"] for r in monday) == ["Rep One", "Rep Two"]
    for r in monday:
        assert ";" not in str(r["employee_name"])
        assert ";" not in str(r["start_time"])


def test_A3_a_split_shift_stays_two_rows_so_the_day_cell_can_stack_them(wired, api_client):
    rows = [r for r in _week(api_client, HOUSE)
            if r["employee_name"] == "Rep Two" and r["shift_date"] == "2026-11-26"]
    assert sorted((r["start_time"], r["end_time"]) for r in rows) == [("09:00", "12:00"),
                                                                      ("15:00", "19:00")]


def test_A4_a_person_at_two_stores_comes_back_keyed_to_each_one(wired, api_client):
    rows = [r for r in _week(api_client, HOUSE) if r["employee_name"] == "Rep One"]
    assert sorted(r["store_code"] for r in rows) == ["B-117", "B-1598"]


def test_A5_both_times_come_back_so_hours_are_recomputed_not_trusted(wired, api_client):
    """The stored `scheduled_hours` on this row is -16. The line builder prints 8 because it reads
    the times; it can only do that if the route returns them."""
    row = _week(api_client, SOLO)[0]
    assert (row["start_time"], row["end_time"]) == ("21:00", "05:00")
    assert row["scheduled_hours"] == -16          # the stored value really is wrong, and unused


# ── §B. nothing reaches a line that does not belong on it ────────────────────────────────────────
def test_B1_a_deleted_shift_never_reaches_a_printed_line(wired, api_client):
    assert "Deleted Rep" not in {r["employee_name"] for r in _week(api_client, HOUSE)}


def test_B2_a_shift_outside_the_printed_week_never_reaches_it(wired, api_client):
    assert {r["shift_date"] for r in _week(api_client, HOUSE)} <= set(
        ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26",
         "2026-11-27", "2026-11-28", "2026-11-29"])


def test_B3_the_store_filter_narrows_the_week_the_way_the_screen_does(wired, api_client):
    rows = _week(api_client, HOUSE, store_code="B-117")
    assert {r["store_code"] for r in rows} == {"B-117"}


# ── §C. every tenant, and never another tenant's week ────────────────────────────────────────────
@pytest.mark.parametrize("org,expected", [
    (HOUSE, {"Rep One", "Rep Two"}),
    (SOLO, {"Night Rep"}),
    (BARE, set()),
])
def test_C1_each_tenant_gets_its_own_week_and_only_its_own(wired, api_client, org, expected):
    assert {r["employee_name"] for r in _week(api_client, org)} == expected


def test_C2_a_tenant_with_no_shifts_gets_an_empty_week_not_another_tenants(wired, api_client):
    """A first-week tenant. The export turns this into a 'nobody scheduled' line per store; what it
    must never be handed is somebody else's rows, which is how a report tells one tenant another
    tenant's business."""
    rows = _week(api_client, BARE)
    assert rows == []


def test_C3_one_tenants_store_codes_never_appear_in_anothers_week(wired, api_client):
    assert {r["store_code"] for r in _week(api_client, SOLO)} == {"L-01"}
    assert "L-01" not in {r["store_code"] for r in _week(api_client, HOUSE)}
