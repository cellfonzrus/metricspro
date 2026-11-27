"""Integration: a rep's own "Hours Worked" card, through the REAL app (index §14y, §CORE-33.3).

Owner Sanjot, 2026-11-27: *"it shows hours worked as the same as the hours scheduled, it should show
the atual hours worked"*, then *"fix the card now across the paltform"*.

Real routing, real middleware, the real endpoint body — over the in-memory database double, with
conftest blocking every outbound socket. "Integration" here means the app, not a real database
(docs/TESTING.md): there is no staging Supabase.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT:
  · `GET /core/employee-dashboard` actually READS `storeops.timelog` now. The unit test proves the
    fold; only this proves the punches reach it — the defect was never in the arithmetic, it was
    that no punch was ever fetched;
  · ★ the schema scoping: `payroll_evidence` wants a `storeops`-scoped client, and handing it this
    endpoint's unscoped one would read a non-existent `public.timelog`, refuse every period and
    look like "nobody clocked". A test over the real client shape is the only thing that catches it;
  · ★ EVERY TENANT: the same shifts and punches under `schedule_fallback` and under `clocked_only`
    produce different cards, from each org's OWN declaration row;
  · ★ the coverage interlock REFUSES a thin period (the Cellfonz shape, 49% measured) and the card
    publishes no figure at all rather than a small one;
  · the window is the org's PAY PERIOD, so a weekly tenant and a fortnightly one get different
    windows from the same request (charter rule 2 — one period resolver);
  · one tenant's punches never reach another tenant's card.
"""
import pytest

from app.modules.storeops import hours_worked_card as hwc
from app.modules.storeops import payroll_hours_basis as hb

ORG_A = "00000000-0000-0000-0000-000000000001"   # weekly, schedule_fallback (the house default)
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"   # fortnightly, clocked_only
DASH = "/api/v1/core/employee-dashboard"

PERIOD = "2026-11"

TENANTS = [
    {"org_id": ORG_A, "work_week_start_dow": 0, "pay_period_type": "weekly", "payday_dow": 4,
     "payday_weeks_after": 1},
    {"org_id": ORG_B, "work_week_start_dow": 0, "pay_period_type": "biweekly", "payday_dow": 4,
     "payday_weeks_after": 1, "biweekly_anchor": "2026-11-09"},
]

# ── THE FIXTURE DATES COME FROM THE RESOLVER UNDER TEST, NOT FROM LITERALS ──────────────────────
# The window is the org's PAY PERIOD containing `min(today, end of PERIOD)`, so a hand-written date
# is inside it only by luck and stops being inside it when the calendar moves. (It did: the first
# draft of this file pinned 2026-11-16..22 and every hours assertion read `no_shifts`, because the
# period containing 2026-11-27 is 11-23..11-29.) Deriving them keeps the test honest about which
# window it is exercising AND deterministic forever — once today is past 2026-11-30 the reference
# date pins to 2026-11-30 and never moves again.
def _window(tenant):
    from datetime import date
    from app.modules.core.router import _pp_settings
    return hwc.window_for(PERIOD, date.today(), _pp_settings(tenant))


def _day(tenant, n):
    """The nth day of that tenant's resolved window, as an ISO date."""
    from datetime import date, timedelta
    return (date.fromisoformat(_window(tenant)["start"]) + timedelta(days=n)).isoformat()


A_DAY0, A_DAY1 = _day(TENANTS[0], 0), _day(TENANTS[0], 1)
B_DAY0 = _day(TENANTS[1], 0)

#: THE ORG'S OWN DECLARATION. Nothing in the card decides these — they are rows (RULE TWO), and the
#: two tenants differ precisely so a leak of one into the other is visible.
HOURS_CFG = [
    {"org_id": ORG_A, "hours_basis_mode": "schedule_fallback", "pay_from_schedule_default": True,
     "min_punch_coverage_pct": 85},
    {"org_id": ORG_B, "hours_basis_mode": "clocked_only", "pay_from_schedule_default": False,
     "min_punch_coverage_pct": 85},
]

EMPLOYEES = [
    {"org_id": ORG_A, "employee_id": "E1", "name": "Ada", "home_store": "B-117",
     "is_active": True, "pay_rate": 20.0, "pay_basis": "hourly"},
    {"org_id": ORG_B, "employee_id": "L9", "name": "Dara", "home_store": "L-1",
     "is_active": True, "pay_rate": 16.0, "pay_basis": "hourly"},
]

# Ada: two 9h shifts. ONE is clocked (7.5h), the other never clocked at all. That is the owner's
# exact shape — the old code answered 18.0 for "hours worked".
SHIFTS = [
    {"id": 1, "org_id": ORG_A, "store_code": "B-117", "employee_id": "E1", "employee_name": "Ada",
     "shift_date": A_DAY0, "scheduled_hours": 9.0, "actual_hours": 0,
     "start_time": "09:00", "end_time": "18:00", "is_deleted": False},
    {"id": 2, "org_id": ORG_A, "store_code": "B-117", "employee_id": "E1", "employee_name": "Ada",
     "shift_date": A_DAY1, "scheduled_hours": 9.0, "actual_hours": 0,
     "start_time": "09:00", "end_time": "18:00", "is_deleted": False},
    {"id": 3, "org_id": ORG_B, "store_code": "L-1", "employee_id": "L9", "employee_name": "Dara",
     "shift_date": B_DAY0, "scheduled_hours": 8.0, "actual_hours": 0,
     "start_time": "09:00", "end_time": "17:00", "is_deleted": False},
]

PUNCHES = [
    {"id": 11, "org_id": ORG_A, "store_code": "B-117", "employee_id": "E1",
     "work_date": A_DAY0, "clock_in": f"{A_DAY0}T09:00:00Z",
     "clock_out": f"{A_DAY0}T16:30:00Z", "hours": 7.5},
]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    for mod in ("app.modules.core.router",
                "app.modules.storeops.router",
                "app.modules.storeops.payroll_approval"):
        monkeypatch.setattr(f"{mod}.get_supabase", lambda: fake_supabase, raising=False)
    # `load_config` memoizes per org, so a seeded declaration from another test file would
    # otherwise decide this one's answer.
    monkeypatch.setattr(hb, "_CONFIG_CACHE", {}, raising=False)
    fake_supabase.seed("storeops.tenants", TENANTS)
    fake_supabase.seed("storeops.employees", EMPLOYEES)
    fake_supabase.seed("storeops.shifts", SHIFTS)
    fake_supabase.seed("storeops.timelog", PUNCHES)
    fake_supabase.seed("storeops.payroll_hours_basis_config", HOURS_CFG)
    return fake_supabase


def _hours(api_client, org=ORG_A, eid="E1", period="2026-11"):
    r = api_client.get(f"{DASH}?employee_id={eid}&org_id={org}&period={period}")
    assert r.status_code == 200, r.text
    return r.json()["hours"]


# ── the owner's symptom ──────────────────────────────────────────────────────────────────────────
def test_worked_hours_are_the_punched_hours_and_not_the_schedule(wired, api_client):
    """THE REGRESSION. Ada was scheduled 18.0h across two days and clocked 7.5h on one of them."""
    h = _hours(wired and api_client)
    assert h["scheduled_hours"] == 18.0
    assert h["actual_hours"] == 7.5, "the schedule is being reported as hours worked again"
    assert h["actual_hours"] != h["scheduled_hours"]


def test_the_unclocked_shift_is_named_rather_than_folded_in(wired, api_client):
    h = _hours(api_client)
    assert h["unmeasured_hours"] == 9.0
    assert h["measured_shifts"] == 1 and h["unmeasured_shifts"] == 1
    assert h["state"] == hwc.PARTLY_MEASURED
    assert "7.5" in h["sentence"] and "9" in h["sentence"]
    assert "not clocked" in h["sentence"]


def test_the_endpoint_really_reads_the_punch_table(wired, api_client):
    """★ THE SCHEMA-SCOPING GUARD. If the evidence read is handed this endpoint's UNSCOPED client it
    looks for `public.timelog`, reports a blocker and measures nothing — which is indistinguishable
    from "nobody clocked" on the surface. Deleting the punch must CHANGE the answer; if it does not,
    the punch was never being read in the first place."""
    before = _hours(api_client)["actual_hours"]
    wired.seed("storeops.timelog", [])
    after = _hours(api_client)["actual_hours"]
    assert before == 7.5
    assert after != before, "the punch table is not reaching this endpoint"
    assert after is None, "nothing measured must say so, never report a number"


def test_nothing_clocked_reads_no_punch_recorded_and_never_zero_hours(wired, api_client):
    wired.seed("storeops.timelog", [])
    h = _hours(api_client)
    assert h["state"] == hwc.NOTHING_MEASURED
    assert h["actual_hours"] is None, "0h would read as a measured zero"
    assert "no punch recorded" in h["sentence"].lower()
    # The rep is still OWED these hours on a schedule_fallback org — the pay figure survives.
    assert h["paid_hours"] == 18.0
    assert h["actual_pay"] == pytest.approx(360.0)


# ── the pay figure comes from the paid basis, never from the measured hours ──────────────────────
def test_pay_is_derived_from_the_paid_basis_not_from_the_measured_hours(wired, api_client):
    """A schedule_fallback org owes the scheduled hours it did not measure. Paying only the
    measured 7.5h would under-state a real obligation by half."""
    h = _hours(api_client)
    assert h["paid_hours"] == 16.5                       # 7.5 punched + 9.0 from the schedule
    assert h["actual_pay"] == pytest.approx(16.5 * 20.0)
    assert h["actual_pay"] != pytest.approx(h["actual_hours"] * 20.0)


def test_the_basis_and_its_sentence_ride_along_so_the_figure_is_never_bare(wired, api_client):
    h = _hours(api_client)
    assert h["hours_basis"] in hb.ALL_BASES
    assert h["hours_basis_reason"] == hb.basis_reason(h["hours_basis"])
    assert h["hours_basis_mixed"] is True


# ── ★ EVERY TENANT: one shape, two declarations ──────────────────────────────────────────────────
def _seed_org_b(wired, days, punched):
    """`days` shifts for Dara across her window, the first `punched` of them clocked at 8h.

    The coverage ratio is the POINT of this helper: a clocked-only org whose period is under its own
    floor is REFUSED (the test below), so a test about what an unmeasured DAY pays must first put
    the PERIOD above the floor. Conflating the two is how "pays nothing" and "we cannot say" get
    read as the same answer — the exact confusion the card exists to end.
    """
    wired.seed("storeops.shifts", [s for s in SHIFTS if s["org_id"] == ORG_A] + [
        dict(SHIFTS[2], id=200 + i, shift_date=_day(TENANTS[1], i)) for i in range(days)])
    wired.seed("storeops.timelog", PUNCHES + [
        {"id": 300 + i, "org_id": ORG_B, "store_code": "L-1", "employee_id": "L9",
         "work_date": _day(TENANTS[1], i), "clock_in": f"{_day(TENANTS[1], i)}T09:00:00Z",
         "clock_out": f"{_day(TENANTS[1], i)}T17:00:00Z", "hours": 8.0} for i in range(punched)])


def test_a_clocked_only_tenant_pays_nothing_for_an_unmeasured_day(wired, api_client):
    """ORG_B declares `clocked_only` with `pay_from_schedule_default: false`. Nine days, eight of
    them clocked — 88.9%, over its declared 85% floor, so the period publishes. The ninth day was
    not measured, so it is not paid, and the card names it rather than pricing it."""
    _seed_org_b(wired, days=9, punched=8)
    h = _hours(api_client, org=ORG_B, eid="L9")
    assert h["state"] == hwc.PARTLY_MEASURED
    assert h["actual_hours"] == 64.0                 # 8 clocked days x 8h
    assert h["unmeasured_hours"] == 8.0              # the ninth day, at its SCHEDULED size
    assert h["paid_hours"] == 64.0, "a clocked-only org must not pay an unmeasured day"
    assert h["actual_pay"] == pytest.approx(64.0 * 16.0)


def test_the_two_tenants_answer_differently_from_the_same_unpunched_shape(wired, api_client):
    """The §COMMCALC-49.10 negative: one org's declaration must never decide another's read.

    Identical shape — shifts, no punches — and the two declarations diverge completely: the
    schedule-fallback org owes every scheduled hour, the clocked-only org cannot publish at all.
    """
    wired.seed("storeops.timelog", [])
    a = _hours(api_client, org=ORG_A, eid="E1")
    b = _hours(api_client, org=ORG_B, eid="L9")
    assert a["state"] == hwc.NOTHING_MEASURED and a["paid_hours"] == 18.0
    assert b["state"] == hwc.REFUSED and b["paid_hours"] is None
    assert a["actual_hours"] is None and b["actual_hours"] is None
    assert "actual_pay" in a and "actual_pay" not in b


def test_one_tenants_punches_never_reach_the_other_tenants_card(wired, api_client):
    """Ada's punch sits in the same physical table as Dara's org. It must not measure Dara's day."""
    h = _hours(api_client, org=ORG_B, eid="L9")
    assert h["actual_hours"] is None and h["measured_shifts"] == 0


# ── ★ the interlock refuses a thin period rather than publishing a small number ──────────────────
def test_a_period_below_the_orgs_coverage_floor_publishes_no_figure(wired, api_client):
    """THE CELLFONZ SHAPE (49% measured against an 85% floor). On a clocked-only org the interlock
    refuses, and the card must carry the refusal INSTEAD of a total — "we could not look" and
    "nobody clocked" must never print the same."""
    # Dara gets ten shifts and one punch: 10% measured, far under the declared 85%.
    many = [dict(SHIFTS[2], id=100 + i, shift_date=_day(TENANTS[1], i)) for i in range(7)]
    wired.seed("storeops.shifts", [s for s in SHIFTS if s["org_id"] == ORG_A] + many)
    wired.seed("storeops.timelog", PUNCHES + [
        {"id": 90, "org_id": ORG_B, "store_code": "L-1", "employee_id": "L9",
         "work_date": B_DAY0, "clock_in": f"{B_DAY0}T09:00:00Z",
         "clock_out": f"{B_DAY0}T17:00:00Z", "hours": 8.0}])
    h = _hours(api_client, org=ORG_B, eid="L9")
    assert h["state"] == hwc.REFUSED, h
    assert h["actual_hours"] is None and h["paid_hours"] is None
    assert h["refusal"] and h["sentence"] == h["refusal"]
    # ★ AND NO MONEY IS PUBLISHED. $0.00 would say "this person earns nothing"; absence is the
    # signal the card already reads for a withheld pay key.
    assert "actual_pay" not in h


# ── the window is the pay period, not the calendar month (charter rule 2) ────────────────────────
def test_the_window_is_the_orgs_pay_period_and_the_card_states_it(wired, api_client):
    h = _hours(api_client)
    assert h["window_basis"] == "pay_period"
    assert h["window_start"] and h["window_end"]
    from datetime import date
    length = (date.fromisoformat(h["window_end"]) - date.fromisoformat(h["window_start"])).days
    assert length == 6, (h["window_start"], h["window_end"])      # a week
    assert h["window_start"] in h["window_label"]


def test_each_tenant_gets_its_own_cycle_length_from_the_same_request(wired, api_client):
    from datetime import date
    def _len(h):
        return (date.fromisoformat(h["window_end"]) - date.fromisoformat(h["window_start"])).days
    assert _len(_hours(api_client, org=ORG_A, eid="E1")) == 6     # weekly
    assert _len(_hours(api_client, org=ORG_B, eid="L9")) == 13    # fortnightly


def test_a_tenant_with_no_pay_cycle_falls_back_to_the_calendar_month_and_says_so(wired, api_client):
    """NO HOUSE CYCLE IS BORROWED. A tenant that has declared no cycle behaves exactly as the card
    did before, and `window_basis` states which basis it is so a month is never shown as a period."""
    wired.seed("storeops.tenants", [])
    h = _hours(api_client)
    assert h["window_basis"] == "calendar_month"
    assert (h["window_start"], h["window_end"]) == ("2026-11-01", "2026-11-30")


def test_no_shifts_in_the_window_is_its_own_state(wired, api_client):
    wired.seed("storeops.shifts", [])
    h = _hours(api_client)
    assert h["state"] == hwc.NO_SHIFTS
    assert h["actual_hours"] is None and h["scheduled_hours"] == 0.0


# ── the pay gate still finds the money among the new keys (mig 434) ─────────────────────────────
def test_the_pay_gate_strips_money_from_the_new_shape_but_leaves_the_hours():
    """★ A SECURITY ASSERTION, not a display one. The card grew five new keys, two of which
    (`paid_hours`, `unmeasured_hours`) are HOURS that look money-adjacent. `strip_pay` works off
    `PAY_FIELDS`, so it must still remove every dollar figure from the new dict and leave every hour
    figure intact — the house rule is that a caller who may not see pay still sees hours, so a gate
    that took the hours too would degrade into "less information" in the wrong direction.

    Pure, so it needs no app: this is the exact dict the endpoint assembles.
    """
    from app.modules.storeops import pay_visibility as pv
    h = {"scheduled_hours": 18.0, "actual_hours": 7.5, "unmeasured_hours": 9.0, "paid_hours": 16.5,
         "pay_rate": 20.0, "scheduled_pay": 360.0, "actual_pay": 330.0,
         "state": hwc.PARTLY_MEASURED, "sentence": "7.5h measured · 9h not clocked",
         "hours_basis": hb.CLOCKED, "window_label": "Pay period"}
    pv.strip_pay(h)
    for money in ("pay_rate", "scheduled_pay", "actual_pay"):
        assert money not in h, f"{money} survived the pay gate"
    assert h["actual_hours"] == 7.5 and h["paid_hours"] == 16.5 and h["unmeasured_hours"] == 9.0
    assert h["sentence"] and h["state"] == hwc.PARTLY_MEASURED
