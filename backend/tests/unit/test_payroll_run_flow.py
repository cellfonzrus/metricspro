"""Unit: running one pay period as a flow somebody can leave halfway (index §STOREOPS-16).

Seven checks over ONE store in ONE pay period — the third of the three flows the owner asked for in
one sentence, and the third caller of the shared home rather than a third copy of it.

WHAT THESE TESTS GUARD, beyond "the lamps are right":
  · ★ `period_start` / `period_end` are REQUIRED — which dates a pay period covers is the tenant's
    own declared cycle, and a house fortnight answering for a weekly tenant would ask somebody to
    approve half a pay period (the defect `payroll_approval` §② already records);
  · ★ the preflight step ASKS `clock_variance.payroll_run_gate` and repeats its words verbatim, so
    this surface can never say "ready" while the run itself refuses;
  · ★ the run record is MONTH-grained and the step says so rather than reading a per-period run
    that nothing writes (the `shifts.actual_hours` defect, §STOREOPS-07);
  · "priced at $0.00" is the one home's answer (`employees_without_rate`), never a second rule;
  · what counts as "standing on a punch" is `payroll_hours_basis`' own vocabulary (§14y);
  · absence is never zero: no punches at all is an OPEN step, not a clean one.
"""
import pytest

from app.modules.core import guided_workflow as gw
from app.modules.storeops import clock_variance as cv
from app.modules.storeops import payroll_hours_basis as hb
from app.modules.storeops import payroll_run_flow as flow

# Two tenants with different cycles: A pays fortnightly, B pays weekly.
WEEKLY = {"period_start": "2026-11-16", "period_end": "2026-11-22"}
FORTNIGHT = {"period_start": "2026-11-09", "period_end": "2026-11-22"}
STRADDLES = {"period_start": "2026-10-26", "period_end": "2026-11-08"}

HOURLY = {"employee_id": "E1", "name": "Ada", "home_store": "B-117", "is_active": True,
          "pay_basis": "hourly", "pay_rate": 17.0}
HOURLY_NO_RATE = {"employee_id": "E2", "name": "Ben", "home_store": "B-117", "is_active": True,
                  "pay_basis": "hourly", "pay_rate": None}
SALARIED = {"employee_id": "E3", "name": "Cleo", "home_store": "B-117", "is_active": True,
            "pay_basis": "salary", "pay_amount": 5000, "pay_rate": None}


def _facts(**over):
    f = {"store_code": "B-117",
         "punches": [{"employee_id": "E1", "clock_in": "2026-11-17T09:00:00Z",
                      "clock_out": "2026-11-17T17:00:00Z"}],
         "worked_without_punch": [],
         "paid_rows": [{"employee_id": "E1", "hours_by_basis": {hb.CLOCKED: 40}}],
         "blocking": [], "manual_uncounted": [],
         "approvals": [{"employee_id": "E1", "dm_status": "approved", "hr_status": "approved"}],
         "employees": [HOURLY], "paid_employee_ids": ["E1"], "salary_rows": [],
         "run_rows": [{"period": "2026-11", "store": "B-117", "wages": 4000,
                       "run_at": "2026-11-23T02:00:00Z"}],
         "tax_rows": [{"employee_id": "E1", "employer_total": 320}],
         "expense_rows": [{"amount": 100}]}
    f.update(over)
    return f


# ── the shape of the flow ────────────────────────────────────────────────────────────────────────
def test_the_seven_steps_are_in_the_order_the_work_happens():
    assert [s["key"] for s in flow.steps(WEEKLY)] == list(flow.STEP_ORDER)
    assert list(flow.STEP_ORDER) == ["punches", "basis", "exceptions", "approval", "rates",
                                     "preflight", "run"]


def test_every_step_is_required_because_a_payroll_has_no_optional_half():
    assert all(s["required"] for s in flow.steps(WEEKLY))


def test_the_dependencies_put_the_gate_after_what_it_judges_and_the_run_last():
    dep = {s["key"]: s["depends_on"] for s in flow.steps(WEEKLY)}
    assert dep["exceptions"] == ("punches",)
    assert dep["approval"] == ("exceptions",)
    assert set(dep["preflight"]) == {"exceptions", "rates"}
    assert set(dep["run"]) == {"approval", "preflight"}


def test_each_step_names_a_screen_that_already_does_that_work():
    for s in flow.steps(WEEKLY):
        assert s["screen"].startswith("/storeops/"), s


# ── ★ the tenant's own pay period ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [{}, None, {"period_start": "2026-11-01"},
                                 {"period_end": "2026-11-15"},
                                 {"period_start": "nope", "period_end": "2026-11-15"}])
def test_a_caller_that_has_not_asked_the_org_its_pay_period_cannot_answer(bad):
    with pytest.raises(ValueError):
        flow.steps(bad)


def test_a_backwards_period_is_read_in_the_order_the_calendar_has_it():
    d = flow.describe({"period_start": "2026-11-22", "period_end": "2026-11-16"}, _facts())
    assert (d["period_start"], d["period_end"]) == ("2026-11-16", "2026-11-22")


def test_a_weekly_and_a_fortnightly_tenant_are_each_measured_over_their_own_period():
    assert flow.describe(WEEKLY, _facts())["period_start"] == "2026-11-16"
    assert flow.describe(FORTNIGHT, _facts())["period_start"] == "2026-11-09"


def test_a_period_inside_one_month_names_one_month_and_a_straddling_one_names_both():
    assert flow.months_of(WEEKLY) == ("2026-11",)
    assert flow.months_of(STRADDLES) == ("2026-10", "2026-11")


# ── step 1 · the punches ─────────────────────────────────────────────────────────────────────────
def test_no_punches_at_all_is_an_open_step_not_a_clean_one():
    v = flow._punches_verdict([], [])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "No punches at all" in v["needs_input"][0]


def test_somebody_still_clocked_in_is_named():
    v = flow._punches_verdict([{"employee_id": "E1", "clock_in": "x", "clock_out": None}], [])
    assert gw.status_of(v) == gw.NEEDS_INPUT and "still open" in v["needs_input"][0]
    assert v["detail"]["open_punches"] == 1


def test_somebody_on_the_rota_who_never_clocked_in_is_named():
    v = flow._punches_verdict([{"employee_id": "E1", "clock_out": "x"}], ["E9"])
    assert "never clocked in" in " ".join(v["needs_input"])
    assert v["detail"]["worked_without_punch"] == ["E9"]


def test_closed_punches_with_nobody_missing_settle_the_step():
    assert gw.status_of(flow._punches_verdict([{"employee_id": "E1", "clock_out": "x"}],
                                              [])) == gw.DONE


# ── step 2 · what is paying them ─────────────────────────────────────────────────────────────────
def test_hours_on_a_punch_settle_the_basis_step():
    v = flow._basis_verdict([{"hours_by_basis": {hb.CLOCKED: 40}}], WEEKLY)
    assert gw.status_of(v) == gw.DONE and v["detail"]["clock_coverage"] == 1.0


def test_mostly_schedule_hours_are_named_with_the_percentage():
    v = flow._basis_verdict([{"hours_by_basis": {hb.CLOCKED: 10}},
                             {"hours_by_basis": {hb.SCHEDULE_OPT_IN: 90}}], WEEKLY)
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "Only 10% of the paid hours" in v["needs_input"][0]


def test_what_stands_on_a_punch_is_the_basis_homes_own_vocabulary():
    for basis in hb.MEASURED_BASES:
        v = flow._basis_verdict([{"hours_by_basis": {basis: 8}}], WEEKLY)
        assert v["detail"]["clock_coverage"] == 1.0, basis
    v = flow._basis_verdict([{"hours_by_basis": {hb.SCHEDULE_OPT_IN: 8}}], WEEKLY)
    assert v["detail"]["clock_coverage"] == 0.0


def test_hours_on_a_basis_that_does_not_pay_are_reported_as_paying_nobody():
    v = flow._basis_verdict([{"hours_by_basis": {hb.NOT_MEASURED_UNPAID: 8}}], WEEKLY)
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "would pay nobody here" in v["needs_input"][0]


def test_each_basis_on_the_period_is_explained_in_words_a_person_can_read():
    v = flow._basis_verdict([{"hours_by_basis": {hb.CLOCKED_CORRECTED: 8}}], WEEKLY)
    assert v["detail"]["bases_explained"][hb.CLOCKED_CORRECTED] == hb.basis_reason(
        hb.CLOCKED_CORRECTED)


def test_an_org_that_wants_more_punch_evidence_can_declare_it_and_nothing_else_changes():
    rows = [{"hours_by_basis": {hb.CLOCKED: 70}}, {"hours_by_basis": {hb.SCHEDULE_OPT_IN: 30}}]
    assert gw.status_of(flow._basis_verdict(rows, WEEKLY)) == gw.DONE
    strict = dict(WEEKLY, clock_coverage_floor=0.9)
    assert gw.status_of(flow._basis_verdict(rows, strict)) == gw.NEEDS_INPUT


# ── step 3 · the exceptions ──────────────────────────────────────────────────────────────────────
def test_an_uncorrected_clock_variance_is_named_with_the_person_and_the_day():
    v = flow._exceptions_verdict([{"store_code": "B-117", "employee_id": "E1",
                                   "date": "2026-11-17"}], [])
    assert gw.status_of(v) == gw.NEEDS_INPUT and "B-117/E1 2026-11-17" in v["needs_input"][0]


def test_hand_typed_hours_nothing_has_counted_are_named():
    v = flow._exceptions_verdict([], [{"employee_id": "E1"}])
    assert "not counted by anything yet" in v["needs_input"][0]


def test_a_clean_period_settles_the_exceptions_step():
    assert gw.status_of(flow._exceptions_verdict([], [])) == gw.DONE


# ── step 4 · hours approved ──────────────────────────────────────────────────────────────────────
def test_nothing_raised_for_approval_is_said_rather_than_read_as_approved():
    v = flow._approval_verdict([])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "No hours have been raised for approval" in v["needs_input"][0]


def test_waiting_on_the_manager_and_sent_back_by_the_manager_are_different_sentences():
    waiting = flow._approval_verdict([{"dm_status": "pending", "hr_status": "pending"}])
    back = flow._approval_verdict([{"dm_status": "sent_back", "hr_status": "pending"}])
    assert "waiting on the manager" in " ".join(waiting["needs_input"])
    assert "sent back by the manager" in " ".join(back["needs_input"])
    assert waiting["needs_input"] != back["needs_input"]


def test_hr_is_only_waiting_on_what_the_manager_has_already_approved():
    v = flow._approval_verdict([{"dm_status": "pending", "hr_status": "pending"}])
    assert v["detail"]["hr_pending"] == 0 and v["detail"]["dm_pending"] == 1


def test_both_sign_offs_settle_the_step():
    v = flow._approval_verdict([{"dm_status": "approved", "hr_status": "approved"}])
    assert gw.status_of(v) == gw.DONE and v["detail"]["approved"] == 1


def test_an_admin_override_is_recorded_rather_than_hidden():
    v = flow._approval_verdict([{"dm_status": "approved", "hr_status": "approved",
                                 "override_by": "sanjot", "override_reason": "cheque cut"}])
    assert gw.status_of(v) == gw.DONE
    assert "override" in v["detail"]["override_note"]


# ── ★ step 5 · nobody is priced at $0.00 ─────────────────────────────────────────────────────────
def test_somebody_with_no_rate_is_named_through_the_one_home_that_answers_that():
    v = flow._rates_verdict([HOURLY, HOURLY_NO_RATE], ["E1", "E2"], [])
    assert gw.status_of(v) == gw.NEEDS_INPUT and "$0.00" in v["needs_input"][0]
    assert v["detail"]["employees_without_rate"] == [
        r["employee_id"] for r in cv.employees_without_rate([HOURLY, HOURLY_NO_RATE],
                                                            ["E1", "E2"])]


def test_a_dormant_record_this_run_would_not_pay_does_not_block_it():
    v = flow._rates_verdict([HOURLY, HOURLY_NO_RATE], ["E1"], [])
    assert gw.status_of(v) == gw.DONE


def test_a_salaried_person_without_an_hourly_rate_is_not_a_defect():
    v = flow._rates_verdict([SALARIED], ["E3"], [{"employee_id": "E3", "amount": 2500}])
    assert gw.status_of(v) == gw.DONE


def test_a_salaried_person_with_no_figure_for_the_period_is_named():
    v = flow._rates_verdict([SALARIED], ["E3"], [])
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "no salary figure" in v["needs_input"][0]
    assert v["detail"]["salaried_without_figure"] == ["E3"]


# ── ★ step 6 · the preflight gate is ASKED, never re-decided ─────────────────────────────────────
def test_the_preflight_step_repeats_the_runs_own_refusal_word_for_word():
    blocking = [{"store_code": "B-117", "employee_id": "E1", "date": "2026-11-17"}]
    gate = cv.payroll_run_gate(blocking=blocking, cfg={"payroll_run_gate_enabled": True},
                               employee_rows=[HOURLY], paid_employee_ids=["E1"])
    v = flow._preflight_verdict(blocking, [HOURLY], ["E1"], WEEKLY)
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert v["needs_input"][0] == gate["reason"]


def test_the_gate_passing_settles_the_step_and_keeps_its_wording():
    gate = cv.payroll_run_gate(blocking=(), cfg={"payroll_run_gate_enabled": True},
                               employee_rows=[HOURLY], paid_employee_ids=["E1"])
    v = flow._preflight_verdict([], [HOURLY], ["E1"], WEEKLY)
    assert gw.status_of(v) == gw.DONE and v["detail"]["gate_reason"] == gate["reason"]


def test_an_org_with_the_variance_gate_off_still_hears_about_a_missing_rate():
    off = dict(WEEKLY, gate_enabled=False)
    v = flow._preflight_verdict([{"store_code": "B-117", "employee_id": "E1"}],
                                [HOURLY_NO_RATE], ["E2"], off)
    assert gw.status_of(v) == gw.NEEDS_INPUT and "NO pay rate" in v["needs_input"][0]
    assert v["detail"]["gate_enabled"] is False


# ── ★ step 7 · the run record is month-grained, and says so ──────────────────────────────────────
def test_a_month_that_has_not_been_run_is_named():
    v = flow._run_verdict([], [], [], WEEKLY)
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert "has not been run for the month 2026-11" in v["needs_input"][0]


def test_a_period_straddling_a_month_end_needs_both_months_and_names_the_missing_one():
    v = flow._run_verdict([{"period": "2026-10", "wages": 100}], [{"employer_total": 1}], [],
                          STRADDLES)
    assert gw.status_of(v) == gw.NEEDS_INPUT and "2026-11" in v["needs_input"][0]
    assert v["detail"]["months"] == ["2026-10", "2026-11"]


def test_the_step_says_in_words_that_the_record_is_by_month_and_not_by_pay_period():
    v = flow._run_verdict([{"period": "2026-11", "wages": 10}], [{"employer_total": 1}], [],
                          FORTNIGHT)
    assert "recorded by MONTH, not by pay period" in v["detail"]["grain_note"]


def test_a_run_that_pushed_nothing_for_this_store_is_named_rather_than_counted_as_done():
    v = flow._run_verdict([{"period": "2026-11", "wages": 0}], [{"employer_total": 1}], [], WEEKLY)
    assert gw.status_of(v) == gw.NEEDS_INPUT and "$0.00 of wages" in v["needs_input"][0]


def test_employer_tax_not_recorded_is_named():
    v = flow._run_verdict([{"period": "2026-11", "wages": 100}], [], [], WEEKLY)
    assert gw.status_of(v) == gw.NEEDS_INPUT and "Employer tax" in v["needs_input"][0]


def test_no_expenses_is_a_note_not_a_refusal_because_a_tenant_may_book_none():
    v = flow._run_verdict([{"period": "2026-11", "wages": 100}], [{"employer_total": 10}], [],
                          WEEKLY)
    assert gw.status_of(v) == gw.DONE
    assert "correct for a tenant that books none" in v["detail"]["expenses_note"]


def test_a_recorded_run_reports_the_money_it_pushed():
    v = flow._run_verdict([{"period": "2026-11", "wages": 4000, "run_at": "2026-11-23T02:00:00Z"}],
                          [{"employer_total": 320}], [{"amount": 100}], WEEKLY)
    assert gw.status_of(v) == gw.DONE
    assert (v["detail"]["wages"], v["detail"]["employer_tax"], v["detail"]["expenses"]) == (
        4000.0, 320.0, 100.0)
    assert v["detail"]["last_run_at"] == "2026-11-23T02:00:00Z"


# ── the whole flow: resume, progress, the board ──────────────────────────────────────────────────
def test_a_finished_period_has_nothing_to_pick_up():
    d = flow.describe(WEEKLY, _facts())
    assert d["status"] == gw.DONE and d["resume"] is None
    assert d["progress"]["required_done"] == d["progress"]["required_total"] == 7


def test_a_period_nobody_has_started_picks_up_at_the_punches():
    d = flow.describe(WEEKLY, {"store_code": "B-1"})
    assert d["resume"] == flow.STEP_PUNCHES


def test_the_gate_is_not_offered_before_the_exceptions_and_the_rates_are_settled():
    d = flow.describe(WEEKLY, _facts(blocking=[{"store_code": "B-117", "employee_id": "E1"}]))
    rail = {s["key"]: tuple(s["blocked_by"]) for s in d["steps"]}
    assert flow.STEP_EXCEPTIONS in rail[flow.STEP_PREFLIGHT]
    assert d["resume"] == flow.STEP_EXCEPTIONS


def test_the_run_is_not_offered_before_the_hours_are_approved():
    d = flow.describe(WEEKLY, _facts(approvals=[{"dm_status": "pending", "hr_status": "pending"}]))
    rail = {s["key"]: tuple(s["blocked_by"]) for s in d["steps"]}
    assert flow.STEP_APPROVAL in rail[flow.STEP_RUN]
    assert d["resume"] == flow.STEP_APPROVAL


def test_the_flow_reports_the_store_and_the_period_it_measured():
    d = flow.describe(FORTNIGHT, _facts())
    assert d["store_code"] == "B-117"
    assert (d["period_start"], d["period_end"]) == ("2026-11-09", "2026-11-22")
    assert d["months"] == ["2026-11"]


def test_the_board_counts_the_store_periods_and_says_where_they_are_stuck():
    rows = [flow.describe(WEEKLY, _facts()),
            flow.describe(WEEKLY, _facts(store_code="B-200", punches=[], run_rows=[])),
            flow.describe(WEEKLY, _facts(store_code="B-300", run_rows=[]))]
    s = flow.summarize(rows)
    assert s["total"] == 3 and s["open"] == 2
    stuck = {r["key"]: r["count"] for r in s["stuck_at"]}
    assert stuck == {flow.STEP_PUNCHES: 1, flow.STEP_RUN: 1}


def test_every_step_is_answered_for_every_tenant_cycle():
    for declared in (WEEKLY, FORTNIGHT, STRADDLES, dict(WEEKLY, gate_enabled=False)):
        d = flow.describe(declared, _facts())
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
    for word in ("boost", "luxelink", "cellfonz", "verizon", "total wireless"):
        assert word not in src, word


def test_the_flow_spells_no_status_word_and_no_basis_word_of_its_own():
    src = open(flow.__file__).read()
    for word in ("not_started", "in_progress", "needs_input"):
        assert f'"{word}"' not in src, word
    for basis in hb.ALL_BASES:
        assert f'"{basis}"' not in src, basis
    assert "guided_workflow as gw" in src
