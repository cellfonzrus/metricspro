"""Unit: scheduling one store-week as a flow somebody can leave halfway (index §STOREOPS-15).

Five steps over ONE store in ONE week — the scheduling analogue of the closing module's store-day.
Every step is a READ of state the platform already holds, so there is nothing half-saved to go
stale and the flow is never a second writer of a shift.

WHAT THESE TESTS GUARD, beyond "the lamps are right":
  · ★ `week_start_dow` is a REQUIRED argument with no house Monday behind it (owner 2026-10-30).
    Which seven days a week IS, is the tenant's own declaration, and a wrong week start mis-buckets
    every shift at the boundary — so a caller that has not asked the org cannot answer;
  · ★ there is NO "published" shift state in this platform and this flow does not invent one. The
    last step reads what IS recorded: a send of the week's rota. (The `shifts.actual_hours` defect,
    §STOREOPS-07, is what reading a column nothing writes looks like.);
  · ★ an uncovered day is REPORTED, not judged, until a tenant declares `open_dows` — nothing
    declared means nothing identified, and the step says so in words;
  · a week's hours come from the ONE home that already answers that (`schedule_hours.total_hours`),
    never a second derivation;
  · "approved leave" and "still awaiting a decision" are one spelling, read by both the
    availability step and the coverage step, so the two can never disagree about who is away.
"""
import pytest

from app.modules.core import guided_workflow as gw
from app.modules.storeops import schedule_hours as sh
from app.modules.storeops import schedule_week_flow as flow

# Two tenants with DIFFERENT weeks. A is Monday-start, B is Sunday-start.
DECL_A = {"week_start_dow": 0}
DECL_B = {"week_start_dow": 6}
DECL_A_OPEN = {"week_start_dow": 0, "open_dows": (0, 1, 2, 3, 4, 5)}

WEEK_A = "2026-11-23"   # a Monday
WEEK_B = "2026-11-22"   # a Sunday


def _shift(eid, day, start="09:00", end="17:00", store="B-117", name=""):
    return {"employee_id": eid, "employee_name": name or eid, "shift_date": day,
            "start_time": start, "end_time": end, "store_code": store}


def _facts(**over):
    f = {"store_code": "B-117", "week_start": WEEK_A,
         "budget": {"weekly_hours": 120}, "time_off": [], "swaps": [],
         "shifts": [_shift("E1", "2026-11-23"), _shift("E2", "2026-11-24")],
         "other_shifts": [], "override": None,
         "sends": [{"status": "sent", "target": "team@example.test",
                    "created_at": "2026-11-22T18:00:00Z"}]}
    f.update(over)
    return f


# ── the shape of the flow ────────────────────────────────────────────────────────────────────────
def test_the_five_steps_are_in_the_order_the_work_happens():
    assert [s["key"] for s in flow.steps(DECL_A)] == list(flow.STEP_ORDER)
    assert list(flow.STEP_ORDER) == ["budget", "availability", "shifts", "coverage", "tell"]


def test_telling_the_team_is_the_only_optional_step():
    req = {s["key"]: s["required"] for s in flow.steps(DECL_A)}
    assert req == {"budget": True, "availability": True, "shifts": True,
                   "coverage": True, "tell": False}


def test_each_step_names_the_screen_that_already_does_that_work():
    for s in flow.steps(DECL_A):
        assert s["screen"].startswith("/storeops/"), s


def test_laying_out_shifts_waits_on_availability_and_the_checks_wait_on_the_shifts():
    dep = {s["key"]: s["depends_on"] for s in flow.steps(DECL_A)}
    assert dep["shifts"] == ("availability",)
    assert dep["coverage"] == ("shifts",)
    assert dep["tell"] == ("shifts",)
    assert dep["budget"] == () and dep["availability"] == ()


# ── ★ the tenant's own week ──────────────────────────────────────────────────────────────────────
def test_the_week_start_is_required_and_there_is_no_house_monday_behind_it():
    with pytest.raises(ValueError) as e:
        flow.steps({})
    assert "week_start_dow" in str(e.value)
    with pytest.raises(ValueError):
        flow.steps(None)


@pytest.mark.parametrize("bad", [7, -1, "monday", None, ""])
def test_a_week_start_that_is_not_a_weekday_number_is_refused(bad):
    with pytest.raises(ValueError):
        flow.steps({"week_start_dow": bad})


def test_one_tenants_week_is_not_another_tenants_week():
    # The SAME Wednesday falls in two different weeks for two tenants.
    wed = "2026-11-25"
    assert flow.week_days(wed, DECL_A)[0].isoformat() == "2026-11-23"   # Monday-start
    assert flow.week_days(wed, DECL_B)[0].isoformat() == "2026-11-22"   # Sunday-start


def test_a_date_anywhere_inside_the_week_names_the_same_week():
    for day in ("2026-11-23", "2026-11-25", "2026-11-29"):
        assert flow.week_days(day, DECL_A)[0].isoformat() == "2026-11-23"


def test_the_week_is_seven_consecutive_days_and_the_flow_reports_both_ends():
    days = flow.week_days(WEEK_A, DECL_A)
    assert len(days) == 7 and (days[-1] - days[0]).days == 6
    d = flow.describe(DECL_A, _facts())
    assert d["week_start"] == "2026-11-23" and d["week_end"] == "2026-11-29"


def test_the_week_start_is_derived_through_the_one_home_that_already_answers_it():
    for dow in range(7):
        got = flow.week_days("2026-11-25", {"week_start_dow": dow})[0]
        assert got == sh.week_start_of(flow._day("2026-11-25"), dow)


# ── step 1 · the hours budget ────────────────────────────────────────────────────────────────────
def test_no_budget_set_asks_for_one_by_name():
    v = flow._budget_verdict(None)
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "No weekly hours budget is set for this store" in v["needs_input"][0]


def test_a_zero_budget_is_named_rather_than_read_as_no_budget():
    v = flow._budget_verdict({"weekly_hours": 0})
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "zero" in v["needs_input"][0]


def test_a_declared_budget_settles_step_one():
    v = flow._budget_verdict({"weekly_hours": "120.5"})
    assert gw.status_of(v) == gw.DONE and v["detail"]["weekly_hours"] == 120.5


# ── step 2 · who is available ────────────────────────────────────────────────────────────────────
def test_a_pending_time_off_request_holds_the_week_up_and_is_counted():
    v = flow._availability_verdict([{"employee_id": "E1", "status": "pending"}], [])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert v["needs_input"][0] == ("1 time-off request for this week is still awaiting a decision")
    assert v["detail"]["pending_time_off"] == 1


def test_pending_requests_are_counted_in_the_plural():
    v = flow._availability_verdict([{"status": "pending"}, {"status": "pending"}], [])
    assert "2 time-off requests" in v["needs_input"][0] and "are still" in v["needs_input"][0]


def test_a_pending_cover_swap_holds_the_week_up_too():
    v = flow._availability_verdict([], [{"status": "pending"}])
    assert gw.status_of(v) == gw.NEEDS_INPUT and "cover swap" in v["needs_input"][0]


def test_decided_requests_settle_the_step_and_approved_leave_is_recorded():
    v = flow._availability_verdict(
        [{"employee_id": "E9", "status": "approved"}, {"status": "denied"}], [])
    assert gw.status_of(v) == gw.DONE
    assert v["detail"]["away"] == ["E9"] and v["detail"]["approved_time_off"] == 1


def test_the_two_steps_read_one_spelling_of_approved_and_pending():
    off = [{"employee_id": "E1", "status": flow.APPROVED,
            "start_date": "2026-11-24", "end_date": "2026-11-24"}]
    assert flow._availability_verdict(off, [])["detail"]["away"] == ["E1"]
    assert list(flow._away_on(off)) == ["E1"]


# ── step 3 · lay out the shifts ──────────────────────────────────────────────────────────────────
def test_an_empty_rota_asks_for_somebody_on_it():
    v = flow._shifts_verdict([])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "Nobody is on the rota" in v["needs_input"][0]


def test_a_weeks_hours_come_from_the_one_home_that_already_answers_that():
    shifts = [_shift("E1", "2026-11-23"), _shift("E1", "2026-11-24", "09:00", "13:30")]
    v = flow._shifts_verdict(shifts)
    assert gw.status_of(v) == gw.DONE
    assert v["detail"]["scheduled_hours"] == sh.total_hours(shifts)
    assert v["detail"]["shifts"] == 2 and v["detail"]["people"] == 1


def test_a_deleted_shift_is_not_on_the_rota():
    gone = dict(_shift("E1", "2026-11-23"), is_deleted=True)
    assert gw.status_of(flow._shifts_verdict([gone])) == gw.NEEDS_INPUT


# ── step 4 · the careful checks ──────────────────────────────────────────────────────────────────
def _cov(**over):
    f = _facts(**over)
    return flow._coverage_verdict(f["shifts"], f["other_shifts"], f["time_off"], f["budget"],
                                  f["override"], over.get("declared", DECL_A),
                                  flow.week_days(f["week_start"], over.get("declared", DECL_A)))


def test_a_clean_week_passes_the_careful_checks():
    assert gw.status_of(_cov()) == gw.DONE


def test_over_budget_is_named_with_the_hours_and_the_ceiling():
    v = _cov(budget={"weekly_hours": 8})
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "16h are scheduled against a 8h budget" in v["needs_input"][0]
    assert v["detail"]["over_by"] == 8.0


def test_an_approved_overage_stops_the_refusal_without_hiding_the_number():
    v = _cov(budget={"weekly_hours": 8}, override={"status": "approved", "approved_hours": 20})
    assert gw.status_of(v) == gw.DONE
    assert v["detail"]["effective_ceiling"] == 20.0 and v["detail"]["over_by"] == 0.0


def test_an_overage_approval_still_awaiting_a_decision_does_not_count_as_one():
    v = _cov(budget={"weekly_hours": 8}, override={"status": "pending", "approved_hours": 20})
    assert gw.status_of(v) == gw.NEEDS_INPUT and v["detail"]["effective_ceiling"] == 8.0


def test_somebody_scheduled_while_on_approved_leave_is_named_with_the_day():
    v = _cov(time_off=[{"employee_id": "E1", "status": "approved",
                        "start_date": "2026-11-23", "end_date": "2026-11-24"}])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert v["detail"]["on_approved_leave"] == ["E1 on 2026-11-23"]


def test_a_pending_request_is_not_leave_so_it_is_not_a_clash_here():
    v = _cov(time_off=[{"employee_id": "E1", "status": "pending",
                        "start_date": "2026-11-23", "end_date": "2026-11-23"}])
    assert v["detail"]["on_approved_leave"] == []


def test_somebody_booked_at_two_stores_the_same_day_is_named():
    v = _cov(other_shifts=[_shift("E1", "2026-11-23", store="B-200")])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert v["detail"]["double_booked"] == ["E1 is also at B-200 on 2026-11-23"]


def test_the_same_person_elsewhere_on_a_different_day_is_not_a_clash():
    v = _cov(other_shifts=[_shift("E1", "2026-11-26", store="B-200")])
    assert v["detail"]["double_booked"] == [] and gw.status_of(v) == gw.DONE


# ── ★ an uncovered day is reported, not judged ───────────────────────────────────────────────────
def test_days_with_nobody_on_are_listed_and_the_step_says_why_it_is_not_judging():
    v = _cov()
    assert gw.status_of(v) == gw.DONE
    assert v["detail"]["days_with_nobody"] == ["Wednesday", "Thursday", "Friday",
                                               "Saturday", "Sunday"]
    assert v["detail"]["open_days_declared"] is False
    assert "No opening days are declared" in v["detail"]["days_with_nobody_note"]


def test_once_a_tenant_declares_its_opening_days_the_same_step_starts_refusing():
    v = _cov(declared=DECL_A_OPEN)
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "4 declared opening days have nobody on" in v["needs_input"][0]
    assert v["detail"]["open_days_declared"] is True
    assert "days_with_nobody_note" not in v["detail"]


def test_a_week_with_every_declared_opening_day_covered_passes():
    shifts = [_shift("E1", f"2026-11-{d}") for d in (23, 24, 25, 26, 27, 28)]
    v = _cov(declared=DECL_A_OPEN, shifts=shifts, budget={"weekly_hours": 200})
    assert gw.status_of(v) == gw.DONE
    assert v["detail"]["days_with_nobody"] == ["Sunday"]


# ── ★ step 5 · tell the team, which is a SEND and not a "publish" ────────────────────────────────
def test_an_unsent_rota_asks_to_be_sent():
    v = flow._tell_verdict([], [_shift("E1", "2026-11-23")])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "has not been sent to anybody yet" in v["needs_input"][0]


def test_nothing_to_send_is_not_something_to_chase():
    v = flow._tell_verdict([], [])
    assert gw.status_of(v) == gw.NOT_STARTED


def test_a_recorded_send_settles_the_step_and_names_who_it_went_to():
    v = flow._tell_verdict([{"status": "sent", "target": "a@b.test",
                             "created_at": "2026-11-22T18:00:00Z"}], [])
    assert gw.status_of(v) == gw.DONE and v["detail"]["targets"] == ["a@b.test"]
    assert v["detail"]["last_sent_at"] == "2026-11-22T18:00:00Z"


def test_a_send_that_did_not_arrive_is_named_rather_than_counted_as_sent():
    v = flow._tell_verdict([{"status": "failed", "target": "a@b.test"}], [])
    assert gw.status_of(v) == gw.NEEDS_INPUT and "did not arrive" in v["needs_input"][0]


def test_one_failed_address_does_not_reopen_a_step_a_real_send_settled():
    v = flow._tell_verdict([{"status": "sent", "target": "a@b.test"},
                            {"status": "failed", "target": "c@d.test"}], [])
    assert gw.status_of(v) == gw.DONE and v["detail"]["failed"] == 1


def test_the_flow_invents_no_published_state_and_reads_the_send_log_it_does_have():
    src = open(flow.__file__).read().lower()
    assert "published" not in src.replace("there is no \"published\"", "")
    assert flow.SCHEDULE_REPORT_KEY == "storeops_schedule"


# ── the whole flow: resume, progress, the board ──────────────────────────────────────────────────
def test_a_finished_week_has_nothing_to_pick_up():
    shifts = [_shift("E1", f"2026-11-{d}") for d in (23, 24, 25, 26, 27)]
    d = flow.describe(DECL_A, _facts(shifts=shifts))
    assert d["status"] == gw.DONE and d["resume"] is None
    assert d["progress"]["required_done"] == d["progress"]["required_total"] == 4


def test_a_week_nobody_has_started_resumes_at_the_budget():
    d = flow.describe(DECL_A, {"store_code": "B-1", "week_start": WEEK_A})
    assert d["resume"] == flow.STEP_BUDGET and d["status"] == gw.NEEDS_INPUT


def test_the_checks_cannot_be_picked_up_before_there_are_shifts_to_check():
    d = flow.describe(DECL_A, _facts(shifts=[]))
    assert d["resume"] == flow.STEP_SHIFTS
    rail = {s["key"]: s for s in d["steps"]}
    assert tuple(rail[flow.STEP_COVERAGE]["blocked_by"]) == (flow.STEP_SHIFTS,)


def test_a_week_held_up_by_an_undecided_request_picks_up_where_the_decision_is():
    d = flow.describe(DECL_A, _facts(time_off=[{"status": "pending"}]))
    assert d["resume"] == flow.STEP_AVAILABILITY
    rail = {s["key"]: s for s in d["steps"]}
    assert rail[flow.STEP_AVAILABILITY]["screen"] == "/storeops/timeoff"


def test_the_flow_reports_the_store_and_the_hours_it_scheduled():
    d = flow.describe(DECL_A, _facts())
    assert d["store_code"] == "B-117" and d["scheduled_hours"] == 16.0


def test_the_board_counts_the_store_weeks_and_says_where_they_are_stuck():
    weeks = [flow.describe(DECL_A, _facts()),
             flow.describe(DECL_A, _facts(shifts=[], store_code="B-200")),
             flow.describe(DECL_A, _facts(budget=None, store_code="B-300"))]
    s = flow.summarize(weeks)
    assert s["total"] == 3 and s["open"] == 2
    stuck = {row["key"]: row["count"] for row in s["stuck_at"]}
    assert stuck == {flow.STEP_SHIFTS: 1, flow.STEP_BUDGET: 1}


def test_every_step_is_answered_for_every_tenant_shape():
    for declared in (DECL_A, DECL_B, DECL_A_OPEN, {"week_start_dow": 3}):
        d = flow.describe(declared, _facts(week_start=WEEK_B))
        assert [s["key"] for s in d["steps"]] == list(flow.STEP_ORDER)
        for s in d["steps"]:
            assert s["status"] in gw.STATUSES and s["label"]


# ── the design, locked in a test as well as in the harness ───────────────────────────────────────
def test_the_flow_is_a_projection_and_a_set_of_doors_never_a_second_writer():
    src = open(flow.__file__).read()
    for writer in (".insert(", ".update(", ".upsert(", ".delete(", "supabase", "fastapi"):
        assert writer not in src, writer


def test_the_flow_names_no_tenant_and_no_carrier_of_its_own():
    src = open(flow.__file__).read().lower()
    for word in ("boost", "luxelink", "cellfonz", "total wireless", "verizon"):
        assert word not in src, word


def test_the_flow_spells_no_status_word_of_its_own_but_dereferences_the_shared_home():
    src = open(flow.__file__).read()
    for word in ("not_started", "in_progress", "needs_input"):
        assert f'"{word}"' not in src, word
    assert "guided_workflow as gw" in src
