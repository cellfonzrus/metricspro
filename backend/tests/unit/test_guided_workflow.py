"""Unit: the one home for "a multi-step flow somebody can leave halfway" (index §CORE-51).

Owner 2026-11-25, verbatim: *"The way we have set up teh closing workflow , it is very easy for a
new person to start and then pick up where tehy left off, need similar for the Employee set up in
logical steps leading up to onboaring and then simlar workflow for scheduling andn simlar workflow
for runnign the payroll with paying attention to details"*

THE CLASS. Three flows were asked for. Writing the rail, the lamps and the "resume here" three
times is three homes for one question, and the third one would drift. `core/guided_workflow` is the
one home; a flow declares its STEPS and answers a VERDICT per step, and everything a screen shows
is derived here.

These tests are pure: no app, no database, no tenant. The flow-shaped tests live beside each flow.
"""
import pytest

from app.modules.core import guided_workflow as gw


# ── the vocabulary ───────────────────────────────────────────────────────────────────────────────
def test_the_five_statuses_and_nothing_else():
    assert gw.STATUSES == (gw.NOT_STARTED, gw.IN_PROGRESS, gw.NEEDS_INPUT, gw.SKIPPED, gw.DONE)
    assert set(gw.LABELS) == set(gw.STATUSES), "every status is worded for a person"
    assert all(gw.LABELS[s] and gw.LABELS[s][0].isupper() for s in gw.STATUSES)


def test_open_statuses_are_the_ones_still_needing_somebody():
    assert gw.OPEN_STATUSES == (gw.NOT_STARTED, gw.IN_PROGRESS, gw.NEEDS_INPUT)
    assert gw.DONE not in gw.OPEN_STATUSES
    assert gw.SKIPPED not in gw.OPEN_STATUSES


def test_no_label_names_a_table_a_schema_or_a_host():
    # index §19.38 — these strings reach the screen.
    banned = ("storeops.", "commcalc.", "supabase", "postgres", "vercel", "select ")
    for text in gw.LABELS.values():
        assert not any(b in text.lower() for b in banned), text


# ── declaring steps ──────────────────────────────────────────────────────────────────────────────
def _three():
    return gw.declare([
        gw.step("who", "Who and where", why="Name, store, role"),
        gw.step("pay", "How they are paid", depends_on=("who",)),
        gw.step("plan", "Incentive plan", required=False, depends_on=("who",)),
    ])


def test_declare_returns_the_steps_in_order_with_defaults_filled():
    steps = _three()
    assert [s["key"] for s in steps] == ["who", "pay", "plan"]
    assert steps[0]["required"] is True and steps[0]["depends_on"] == ()
    assert steps[2]["required"] is False
    assert steps[1]["depends_on"] == ("who",)


def test_a_duplicate_step_key_is_refused():
    with pytest.raises(ValueError, match="duplicate"):
        gw.declare([gw.step("a", "A"), gw.step("a", "Again")])


def test_a_step_may_only_depend_on_a_step_declared_BEFORE_it():
    # This is what makes a cycle impossible and the rail's order the real order.
    with pytest.raises(ValueError, match="depends_on"):
        gw.declare([gw.step("a", "A", depends_on=("b",)), gw.step("b", "B")])
    with pytest.raises(ValueError, match="depends_on"):
        gw.declare([gw.step("a", "A", depends_on=("a",))])


def test_a_step_with_no_title_is_refused():
    with pytest.raises(ValueError, match="title"):
        gw.declare([gw.step("a", "")])


# ── one step's status ────────────────────────────────────────────────────────────────────────────
def test_nothing_entered_reads_not_started():
    assert gw.status_of(gw.verdict()) == gw.NOT_STARTED


def test_something_entered_but_unfinished_reads_in_progress():
    assert gw.status_of(gw.verdict(started=True)) == gw.IN_PROGRESS


def test_a_named_gap_reads_needs_input_and_beats_in_progress():
    v = gw.verdict(started=True, needs_input=("No pay rate on file",))
    assert gw.status_of(v) == gw.NEEDS_INPUT
    assert v["needs_input"] == ("No pay rate on file",)


def test_done_beats_everything_else():
    assert gw.status_of(gw.verdict(done=True, started=True, needs_input=("x",))) == gw.DONE


def test_skipped_is_its_own_answer_and_never_pretends_to_be_done():
    v = gw.verdict(skipped=True, detail={"why": "this tenant runs no time clock"})
    assert gw.status_of(v) == gw.SKIPPED
    assert gw.status_of(v) != gw.DONE


def test_a_required_step_cannot_be_skipped():
    steps = gw.declare([gw.step("who", "Who and where")])
    with pytest.raises(ValueError, match="required"):
        gw.rail(steps, {"who": gw.verdict(skipped=True)})


def test_needs_input_must_carry_a_sentence_a_person_can_act_on():
    with pytest.raises(ValueError, match="needs_input"):
        gw.verdict(needs_input=("",))
    with pytest.raises(ValueError, match="needs_input"):
        gw.verdict(needs_input="No pay rate")      # a bare string is not a list of reasons


# ── the rail ─────────────────────────────────────────────────────────────────────────────────────
def test_the_rail_is_one_row_per_step_carrying_its_own_status_and_label():
    steps = _three()
    rail = gw.rail(steps, {"who": gw.verdict(done=True),
                           "pay": gw.verdict(started=True, needs_input=("No pay rate on file",)),
                           "plan": gw.verdict()})
    assert [r["key"] for r in rail] == ["who", "pay", "plan"]
    assert [r["status"] for r in rail] == [gw.DONE, gw.NEEDS_INPUT, gw.NOT_STARTED]
    assert rail[1]["label"] == gw.LABELS[gw.NEEDS_INPUT]
    assert rail[1]["needs_input"] == ("No pay rate on file",)
    assert rail[0]["why"] == "Name, store, role"


def test_a_step_whose_dependency_is_not_finished_says_what_blocks_it():
    steps = _three()
    rail = gw.rail(steps, {"who": gw.verdict(), "pay": gw.verdict(), "plan": gw.verdict()})
    by = {r["key"]: r for r in rail}
    assert by["pay"]["blocked_by"] == ("who",)
    assert by["who"]["blocked_by"] == ()


def test_a_skipped_dependency_does_not_block_what_comes_after_it():
    steps = gw.declare([gw.step("clock", "Time clock", required=False),
                        gw.step("access", "App access", depends_on=("clock",))])
    rail = gw.rail(steps, {"clock": gw.verdict(skipped=True), "access": gw.verdict()})
    assert {r["key"]: r["blocked_by"] for r in rail}["access"] == ()


def test_a_missing_verdict_is_a_refusal_never_a_silent_not_started():
    # "Nothing declared means nothing identified, and the surface says so" — a flow that forgot to
    # answer a step must not render as a step nobody has started.
    steps = _three()
    with pytest.raises(ValueError, match="plan"):
        gw.rail(steps, {"who": gw.verdict(), "pay": gw.verdict()})


def test_a_verdict_for_a_step_that_does_not_exist_is_a_refusal():
    steps = _three()
    with pytest.raises(ValueError, match="ghost"):
        gw.rail(steps, {"who": gw.verdict(), "pay": gw.verdict(), "plan": gw.verdict(),
                        "ghost": gw.verdict()})


# ── where do I pick up ───────────────────────────────────────────────────────────────────────────
def test_resume_is_the_first_open_step_that_is_not_blocked():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(), "plan": gw.verdict()}
    assert gw.resume_key(steps, v) == "pay"


def test_resume_skips_what_is_already_done_or_skipped():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(skipped=False, done=True),
         "plan": gw.verdict()}
    assert gw.resume_key(steps, v) == "plan"


def test_resume_never_sends_somebody_to_a_blocked_step():
    steps = _three()
    v = {"who": gw.verdict(), "pay": gw.verdict(started=True), "plan": gw.verdict()}
    assert gw.resume_key(steps, v) == "who", "the dependency comes first, not the started step"


def test_resume_comes_back_to_a_step_that_needs_input():
    steps = _three()
    v = {"who": gw.verdict(done=True, ), "pay": gw.verdict(needs_input=("No pay rate",)),
         "plan": gw.verdict(done=True)}
    assert gw.resume_key(steps, v) == "pay"


def test_nothing_left_means_no_resume():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(done=True), "plan": gw.verdict(done=True)}
    assert gw.resume_key(steps, v) is None


# ── progress, blocking, finished ─────────────────────────────────────────────────────────────────
def test_progress_counts_the_required_steps_separately():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(), "plan": gw.verdict(skipped=True)}
    p = gw.progress(steps, v)
    assert p == {"total": 3, "done": 2, "required_total": 2, "required_done": 1}


def test_blocking_names_the_required_steps_that_are_not_finished():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(needs_input=("No pay rate",)),
         "plan": gw.verdict()}
    assert gw.blocking(steps, v) == ("pay",)


def test_finished_means_every_required_step_is_done_an_optional_one_need_not_be():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(done=True), "plan": gw.verdict()}
    assert gw.is_finished(steps, v) is True
    assert gw.blocking(steps, v) == ()


def test_not_finished_while_one_required_step_is_open():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(started=True), "plan": gw.verdict(done=True)}
    assert gw.is_finished(steps, v) is False


# ── describe: what one subject's whole flow reads as ─────────────────────────────────────────────
def test_describe_carries_the_status_the_resume_and_the_rail():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(), "plan": gw.verdict()}
    d = gw.describe(steps, v)
    assert d["status"] == gw.IN_PROGRESS
    assert d["label"] == gw.LABELS[gw.IN_PROGRESS]
    assert d["resume"] == "pay" and d["resume_title"] == "How they are paid"
    assert d["progress"]["done"] == 1
    assert d["blocking"] == ("pay",)
    assert [s["key"] for s in d["steps"]] == ["who", "pay", "plan"]


def test_a_flow_nobody_has_touched_reads_not_started():
    steps = _three()
    v = {k: gw.verdict() for k in ("who", "pay", "plan")}
    assert gw.describe(steps, v)["status"] == gw.NOT_STARTED


def test_a_flow_with_one_named_gap_reads_needs_input_not_in_progress():
    steps = _three()
    v = {"who": gw.verdict(done=True), "pay": gw.verdict(needs_input=("No pay rate",)),
         "plan": gw.verdict(done=True)}
    assert gw.describe(steps, v)["status"] == gw.NEEDS_INPUT


def test_a_finished_flow_reads_done_and_has_no_resume():
    steps = _three()
    v = {k: gw.verdict(done=True) for k in ("who", "pay", "plan")}
    d = gw.describe(steps, v)
    assert d["status"] == gw.DONE and d["resume"] is None and d["resume_title"] == ""


# ── summarize: the board over many subjects ──────────────────────────────────────────────────────
def test_summarize_counts_the_subjects_by_status_and_names_the_commonest_gap():
    steps = _three()
    done = gw.describe(steps, {k: gw.verdict(done=True) for k in ("who", "pay", "plan")})
    gap = gw.describe(steps, {"who": gw.verdict(done=True),
                              "pay": gw.verdict(needs_input=("No pay rate on file",)),
                              "plan": gw.verdict()})
    fresh = gw.describe(steps, {k: gw.verdict() for k in ("who", "pay", "plan")})
    s = gw.summarize([done, gap, gap, fresh])
    assert s["total"] == 4
    assert s["by_status"][gw.DONE] == 1
    assert s["by_status"][gw.NEEDS_INPUT] == 2
    assert s["by_status"][gw.NOT_STARTED] == 1
    assert s["by_status"][gw.IN_PROGRESS] == 0
    assert s["finished"] == 1 and s["open"] == 3
    assert s["stuck_at"][0] == {"key": "pay", "title": "How they are paid", "count": 2}


def test_summarize_of_nothing_is_zero_not_an_error():
    s = gw.summarize([])
    assert s["total"] == 0 and s["open"] == 0 and s["stuck_at"] == ()
    assert set(s["by_status"]) == set(gw.STATUSES)


# ── purity (the design rule, proved rather than promised) ────────────────────────────────────────
def test_the_home_holds_no_tenant_word_no_io_and_no_framework_import():
    import inspect
    src = inspect.getsource(gw)
    for banned in ("import requests", "supabase", "fastapi", "psycopg", "httpx",
                   "cellfonz", "luxelink", "vzone", "boost", "total wireless"):
        assert banned not in src.lower(), f"{banned!r} must not appear in the shared home"
