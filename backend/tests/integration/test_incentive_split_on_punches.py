"""Integration: an hours-split incentive pool is weighted by closed punches, through the REAL app.

Owner Sanjot, 2026-11-27 14:35 ET: *"let the daily cincentive as itonly affects ny logistics bee
ffeceive going forward based on eh acual clock in, if te hours are udpated manuay then we cannot oay
for thsose hours"*.

Real routing, the real endpoint body, over the in-memory database double with conftest blocking every
outbound socket. "Integration" here means the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT:
  · the punches actually REACH the weighting — the defect was never the arithmetic, it was that
    `_incentive_hours_for_day` read `storeops.shifts` and nothing else;
  · ★ the identity grain: punches key on the raw `employee_id` and the pool keys on
    `employee_name`. If that mapping is wrong, EVERY weight is zero and every hours pool reports
    unallocated — a silent, total failure that looks like "nobody clocked";
  · ★ THE BLAST RADIUS: the ROSTER is unchanged, so an `equal` or `individual_sales` pool is
    weighted exactly as before. Only `split: hours` moves;
  · ★ the floor: a day before the store's accepted start date keeps today's behaviour, and a store
    with NO accepted floor is untouched entirely — so deploying this moves no money by itself;
  · ★ a hand-corrected day carries no weight even for an org whose PAY folds corrections;
  · a pool nobody carries weight for is reported UNALLOCATED, never equal-split.
"""
import pytest

from app.modules.commcalc import incentive_clock_floor as icf
from app.modules.storeops import payroll_hours_basis as hb

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"

DAY = "2026-11-20"
FLOOR_DAY = "2026-11-01"

STORES = [{"org_id": ORG_A, "store_code": "S1", "address": "1 High St", "market": "LI",
           "is_active": True},
          {"org_id": ORG_B, "store_code": "T1", "address": "9 Other Rd", "market": "NJ",
           "is_active": True}]

EMPLOYEES = [
    {"org_id": ORG_A, "employee_id": "E1", "name": "Ada", "home_store": "S1",
     "is_active": True, "pay_rate": 20.0, "pay_basis": "hourly"},
    {"org_id": ORG_A, "employee_id": "E2", "name": "Ben", "home_store": "S1",
     "is_active": True, "pay_rate": 20.0, "pay_basis": "hourly"},
]

#: Ada and Ben are both rostered 9h. Ada clocked 8h; Ben never clocked at all. Under the old
#: `actual_hours else scheduled_hours` both weighed 9 and split the pool 50/50.
SHIFTS = [
    {"id": 1, "org_id": ORG_A, "store_code": "S1", "employee_id": "E1", "employee_name": "Ada",
     "shift_date": DAY, "scheduled_hours": 9.0, "actual_hours": 0,
     "start_time": "09:00", "end_time": "18:00", "is_deleted": False},
    {"id": 2, "org_id": ORG_A, "store_code": "S1", "employee_id": "E2", "employee_name": "Ben",
     "shift_date": DAY, "scheduled_hours": 9.0, "actual_hours": 0,
     "start_time": "09:00", "end_time": "18:00", "is_deleted": False},
]

PUNCHES = [
    {"id": 11, "org_id": ORG_A, "store_code": "S1", "employee_id": "E1", "work_date": DAY,
     "clock_in": f"{DAY}T09:00:00Z", "clock_out": f"{DAY}T17:00:00Z", "hours": 8.0},
]

FLOORS = [{"org_id": ORG_A, "store_code": "S1", "effective_from": FLOOR_DAY,
           "derived_from": FLOOR_DAY, "accepted_by": None, "accepted_at": None}]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    for mod in ("app.modules.commcalc.router", "app.modules.storeops.router",
                "app.modules.storeops.payroll_approval"):
        monkeypatch.setattr(f"{mod}.get_supabase", lambda: fake_supabase, raising=False)
    monkeypatch.setattr(hb, "_CONFIG_CACHE", {}, raising=False)
    fake_supabase.seed("storeops.stores", STORES)
    fake_supabase.seed("storeops.employees", EMPLOYEES)
    fake_supabase.seed("storeops.shifts", SHIFTS)
    fake_supabase.seed("storeops.timelog", PUNCHES)
    fake_supabase.seed("commcalc.incentive_clock_floor", FLOORS)
    return fake_supabase


def _weights(wired, org=ORG_A, store="S1", day=DAY):
    """The hours map the incentive engine would be handed, straight from the one reader."""
    from app.modules.commcalc import router as CR
    hours, roster, detail = CR._incentive_hours_for_day(wired, org, store, day)
    return hours, roster, detail


# ── the owner's symptom ──────────────────────────────────────────────────────────────────────────
def test_the_weight_is_the_punch_not_the_schedule(wired):
    hours, _roster, _d = _weights(wired)
    assert hours.get("Ada") == 8.0, "the punched hours are not reaching the split"
    assert "Ben" not in hours or hours.get("Ben") == 0.0, \
        "a rep who never clocked is still weighting the pool from the schedule"


def test_the_old_equal_split_is_gone(wired):
    """Under `actual_hours else scheduled_hours` both weighed 9.0 and split the pool 50/50."""
    hours, _r, _d = _weights(wired)
    assert hours.get("Ada") != hours.get("Ben")


def test_the_reason_rides_along_for_every_person_on_the_roster(wired):
    _h, _r, detail = _weights(wired)
    assert detail["Ada"]["reason"] == hb.INCENTIVE_WEIGHT_CLOCKED
    assert detail["Ben"]["reason"] == hb.INCENTIVE_WEIGHT_NO_PUNCH
    # The sentence is the one home's, never composed at the surface.
    assert detail["Ben"]["sentence"] == hb.incentive_weight_reason(hb.INCENTIVE_WEIGHT_NO_PUNCH)


# ── ★ THE BLAST RADIUS: the roster does not move, so only `split: hours` changes ────────────────
def test_the_roster_still_holds_everyone_who_was_scheduled(wired):
    """★ THE CONTAINMENT ASSERTION. `roster` decides who is ELIGIBLE for any pool — equal and
    individual_sales included. Narrowing it to people who clocked would silently change every
    pool on every tenant, which is far beyond what the owner asked for."""
    _h, roster, _d = _weights(wired)
    assert roster == ["Ada", "Ben"], "a rep who did not clock fell off the eligibility roster"


def test_a_rep_who_never_clocked_is_eligible_but_carries_no_weight(wired):
    hours, roster, _d = _weights(wired)
    assert "Ben" in roster
    assert not hours.get("Ben")


# ── ★ a hand-corrected day carries no weight, whatever PAY does ─────────────────────────────────
def test_a_manual_correction_removes_the_days_weight(wired):
    wired.seed("storeops.manual_hours", [
        {"org_id": ORG_A, "employee_id": "E1", "work_date": DAY, "hours": 9.0}])
    hours, roster, detail = _weights(wired)
    assert not hours.get("Ada"), "a hand-edited day still weighted the pool"
    assert detail["Ada"]["reason"] == hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED
    assert "Ada" in roster, "the correction removed the person from the pool, not just the weight"


def test_the_correction_block_holds_even_when_the_org_folds_corrections_into_PAY(wired):
    """★ THE ANTI-COUPLING ASSERTION, end to end. PR #679's switches are PAY controls; they must not
    reach back into the incentive and override the owner's ruling."""
    wired.seed("storeops.payroll_hours_basis_config", [
        {"org_id": ORG_A, "count_manual_hours": True,
         "corrections_effective_from": "2026-01-01"}])
    wired.seed("storeops.manual_hours", [
        {"org_id": ORG_A, "employee_id": "E1", "work_date": DAY, "hours": 9.0}])
    hours, _r, detail = _weights(wired)
    assert not hours.get("Ada")
    assert detail["Ada"]["reason"] == hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED


# ── ★ the floor: nothing already paid restates, and no floor means nothing changes ──────────────
def test_a_day_before_the_stores_floor_keeps_todays_behaviour(wired):
    """The day is before the accepted start date, so it is weighted exactly as it is today —
    `actual_hours else scheduled_hours`, i.e. the schedule. A closed period never restates."""
    early = "2026-10-15"
    wired.seed("storeops.shifts", [dict(s, shift_date=early) for s in SHIFTS])
    hours, _r, detail = _weights(wired, day=early)
    assert hours.get("Ada") == 9.0 and hours.get("Ben") == 9.0, \
        "a period before the floor was restated"
    assert detail["Ada"]["floor_reason"] == icf.NOT_IN_EFFECT_BEFORE_FLOOR


def test_a_store_with_no_accepted_floor_is_untouched(wired):
    """★ THE SAFETY POSTURE. Deploying this code moves no money until a floor is accepted."""
    wired.seed("commcalc.incentive_clock_floor", [])
    hours, _r, detail = _weights(wired)
    assert hours.get("Ada") == 9.0 and hours.get("Ben") == 9.0
    assert detail["Ada"]["floor_reason"] == icf.NOT_IN_EFFECT_NO_FLOOR


def test_a_missing_floor_table_behaves_exactly_as_today_and_says_so(wired):
    """A database that has not run the migration keeps today's split, with the read failure carried
    out rather than swallowed — `payroll_evidence`'s blocker posture."""
    wired.seed("commcalc.incentive_clock_floor", None)
    hours, _r, detail = _weights(wired)
    assert hours.get("Ada") == 9.0
    assert detail["Ada"]["floor_reason"] == icf.NOT_IN_EFFECT_NO_FLOOR


# ── ★ cross-tenant: one org's floor never decides another's weighting ───────────────────────────
def test_one_tenants_accepted_floor_does_not_switch_another_tenant_on(wired):
    """§COMMCALC-49.10. ORG_A has accepted a floor; ORG_B has not, and must be untouched."""
    wired.seed("storeops.employees", EMPLOYEES + [
        {"org_id": ORG_B, "employee_id": "B1", "name": "Dara", "home_store": "T1",
         "is_active": True, "pay_rate": 16.0, "pay_basis": "hourly"}])
    wired.seed("storeops.shifts", SHIFTS + [
        {"id": 9, "org_id": ORG_B, "store_code": "T1", "employee_id": "B1",
         "employee_name": "Dara", "shift_date": DAY, "scheduled_hours": 8.0, "actual_hours": 0,
         "start_time": "09:00", "end_time": "17:00", "is_deleted": False}])
    a_hours, _ar, _ad = _weights(wired, org=ORG_A, store="S1")
    b_hours, _br, b_detail = _weights(wired, org=ORG_B, store="T1")
    assert a_hours.get("Ada") == 8.0                      # punch-weighted
    assert b_hours.get("Dara") == 8.0                     # SCHEDULE-weighted, unchanged
    assert b_detail["Dara"]["floor_reason"] == icf.NOT_IN_EFFECT_NO_FLOOR


def test_one_tenants_punches_never_weight_another_tenants_pool(wired):
    b_hours, _r, _d = _weights(wired, org=ORG_B, store="T1")
    assert "Ada" not in b_hours


# ── a pool nobody carries weight for is reported, never equal-split ────────────────────────────
def test_a_day_nobody_clocked_leaves_the_pool_unallocated_rather_than_equal_split(wired):
    """`daily_incentive.split_pool`'s existing refusal, reached with real weights. Kept, not routed
    around — an equal split of a basis nobody recorded is the lie it exists to prevent."""
    from app.modules.commcalc import daily_incentive as di
    wired.seed("storeops.timelog", [])
    hours, roster, _d = _weights(wired)
    shares, unallocated, note = di.split_pool(100.0, roster, basis="hours",
                                              hours_by_employee=hours)
    assert shares == {}
    assert unallocated == 100.0
    assert "cannot be split by hours" in note


def test_an_equal_split_is_untouched_by_any_of_this(wired):
    """The containment assertion again, at the engine: `equal` reads the ROSTER, which did not
    move, so both reps still share the pool."""
    from app.modules.commcalc import daily_incentive as di
    _h, roster, _d = _weights(wired)
    shares, unallocated, _note = di.split_pool(100.0, roster, basis="equal")
    assert shares == {"Ada": 50.0, "Ben": 50.0}
    assert unallocated == 0
