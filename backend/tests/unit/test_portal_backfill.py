"""A portal pull that owes HISTORY must know which span it owes. Written RED first, 2026-11-10.

THE CLASS, not the instance. `portal_report_pull.date_range` answers exactly one question — "the last
N days, ending yesterday" — clamped to `MAX_WINDOW_DAYS` on purpose, because a daily pull that
re-fetches months is how a portal starts rate-limiting. So there was NO way for any connector on that
loop to be asked for "every day since the first of the year": the only knob refused the question.

The instance that surfaced it: the owner asked for every ePay Backoffice report "first time from the
beginning of the year 2026 till date then update very day autonomously". The general fact missing is
not ePay's and not this tenant's — it is **which span does this source still owe, and which spans has
it already answered**. One home: `commcalc/portal_backfill.py`. Every adapter on the loop inherits it.

These are the PURE rules. The table round trip is in tests/integration.
"""
import pytest

pytestmark = pytest.mark.unit

ORG = "00000000-0000-0000-0000-000000000001"
SRC = "11111111-1111-1111-1111-111111111111"


def test_a_source_with_no_declared_history_owes_nothing():
    """The default posture. A connector that nobody asked to backfill keeps the loop's own recent
    window — this mechanism may never widen a pull that was not asked to be wide."""
    from app.modules.commcalc import portal_backfill as bf
    assert bf.plan_windows(None, today="2026-11-10") == ()
    assert bf.plan_windows("", today="2026-11-10") == ()


def test_history_is_chunked_oldest_first_and_stops_at_yesterday():
    from app.modules.commcalc import portal_backfill as bf
    wins = bf.plan_windows("2026-01-01", today="2026-01-20", chunk_days=7)
    assert wins == (("2026-01-01", "2026-01-07"),
                    ("2026-01-08", "2026-01-14"),
                    ("2026-01-15", "2026-01-19"))
    # yesterday, never today: a business day is not final until it closes (the loop's own rule).
    assert wins[-1][1] == "2026-01-19"


def test_a_history_start_in_the_future_owes_nothing():
    from app.modules.commcalc import portal_backfill as bf
    assert bf.plan_windows("2026-12-01", today="2026-11-10", chunk_days=7) == ()


def test_an_unreadable_history_start_owes_nothing_rather_than_guessing():
    from app.modules.commcalc import portal_backfill as bf
    for bad in ("last january", "2026-13-40", 7, {}):
        assert bf.plan_windows(bad, today="2026-11-10") == ()


def test_the_chunk_is_clamped_to_the_loops_own_ceiling_not_a_second_number():
    """The ceiling is DEREFERENCED from `portal_report_pull.MAX_WINDOW_DAYS`. A second copy here is
    the divergence the house rules forbid — raise that constant and this must follow."""
    from app.modules.commcalc import portal_backfill as bf
    from app.modules.commcalc import portal_report_pull as prp
    wins = bf.plan_windows("2026-01-01", today="2026-11-10", chunk_days=9999)
    span = [w for w in wins][0]
    from datetime import date
    length = (date.fromisoformat(span[1]) - date.fromisoformat(span[0])).days + 1
    assert length == prp.MAX_WINDOW_DAYS
    assert bf.window_width(None) == bf.DEFAULT_CHUNK_DAYS
    assert bf.window_width(10 ** 6) == prp.MAX_WINDOW_DAYS
    assert bf.window_width(0) == bf.DEFAULT_CHUNK_DAYS


def test_a_window_already_answered_is_not_owed_again():
    from app.modules.commcalc import portal_backfill as bf
    done = [{"window_from": "2026-01-01", "window_to": "2026-01-07", "state": "done", "attempts": 1}]
    owed = bf.owed_windows(bf.plan_windows("2026-01-01", today="2026-01-20", chunk_days=7), done)
    assert owed and owed[0] == ("2026-01-08", "2026-01-14")


def test_a_window_that_landed_no_rows_is_answered_too():
    """`empty` is an ANSWER, not a failure: a portal with nothing for those days will have nothing
    for them tomorrow either, and re-asking forever is how a backfill never finishes."""
    from app.modules.commcalc import portal_backfill as bf
    rows = [{"window_from": "2026-01-01", "window_to": "2026-01-07", "state": "empty", "attempts": 1}]
    owed = bf.owed_windows(bf.plan_windows("2026-01-01", today="2026-01-20", chunk_days=7), rows)
    assert owed[0] == ("2026-01-08", "2026-01-14")


def test_a_failed_window_is_retried_until_the_ceiling_then_stays_visible():
    from app.modules.commcalc import portal_backfill as bf
    plan = bf.plan_windows("2026-01-01", today="2026-01-20", chunk_days=7)
    failed_once = [{"window_from": "2026-01-01", "window_to": "2026-01-07",
                    "state": "failed", "attempts": 1}]
    assert bf.owed_windows(plan, failed_once)[0] == ("2026-01-01", "2026-01-07")
    exhausted = [{"window_from": "2026-01-01", "window_to": "2026-01-07",
                  "state": "failed", "attempts": bf.MAX_ATTEMPTS}]
    owed = bf.owed_windows(plan, exhausted)
    assert owed[0] == ("2026-01-08", "2026-01-14"), "an exhausted window must not block the rest"
    assert bf.stuck_windows(plan, exhausted) == (("2026-01-01", "2026-01-07"),), \
        "and it must still be reported — a gap in the data is visible as a gap"


def test_the_pull_takes_the_oldest_owed_window_and_otherwise_the_loops_own_range():
    from app.modules.commcalc import portal_backfill as bf
    default = ("2026-11-03", "2026-11-09")
    assert bf.choose((("2026-01-01", "2026-01-07"),), default) == ("2026-01-01", "2026-01-07")
    assert bf.choose((), default) == default
    assert bf.choose(None, default) == default


def test_one_spelling_of_which_window_this_is():
    """Read twice — once to plan, once to record — and two spellings is how a recorded window never
    matches the planned one and a backfill repeats forever."""
    from app.modules.commcalc import portal_backfill as bf
    assert bf.window_key(("2026-01-01", "2026-01-07")) == "2026-01-01..2026-01-07"
    assert bf.window_key({"window_from": "2026-01-01", "window_to": "2026-01-07"}) \
        == "2026-01-01..2026-01-07"
    assert bf.window_key(None) == ""


def test_the_outcome_word_is_derived_from_what_the_pull_actually_did():
    """`delivered` + rows are what the loop already reports. The state must be DERIVED from them, so
    a pull cannot be recorded green while having landed nothing (the one rule the whole loop keeps)."""
    from app.modules.commcalc import portal_backfill as bf
    assert bf.state_of({"delivered": True, "rows_ingested": 12}) == "done"
    assert bf.state_of({"delivered": False, "rows_ingested": 0}) == "empty"
    assert bf.state_of({"delivered": False, "rows_ingested": 0, "error": "login rejected"}) == "failed"
    assert bf.state_of({"ok": False, "error": "no reports declared"}) == "failed"
    assert bf.state_of(None) == "failed"


def test_progress_counts_what_is_left_for_an_operator_to_read():
    from app.modules.commcalc import portal_backfill as bf
    plan = bf.plan_windows("2026-01-01", today="2026-01-20", chunk_days=7)
    rows = [{"window_from": "2026-01-01", "window_to": "2026-01-07", "state": "done", "attempts": 1}]
    p = bf.progress(plan, rows)
    assert p["windows"] == 3 and p["answered"] == 1 and p["owed"] == 2 and p["stuck"] == 0
    assert "2026-01-08" in p["next_window"]


def test_what_an_operator_typed_is_cleaned_before_it_reaches_a_date_column():
    """A cleared form field arrives as "" — into a DATE column that is a 500, which is how a settings
    screen starts refusing to save. One home for "what this login declared", used by the save path."""
    from app.modules.commcalc import portal_backfill as bf
    assert bf.clean_declaration({"history_from": ""}) == {"history_from": None}
    assert bf.clean_declaration({"history_from": "2026-01-01"}) == {"history_from": "2026-01-01"}
    assert bf.clean_declaration({"history_from": "not a date"}) == {"history_from": None}
    assert bf.clean_declaration({"history_from": "2026-01-01T00:00:00Z"}) == {"history_from": "2026-01-01"}
    assert bf.clean_declaration({"store_code": "  b-1115  "}) == {"store_code": "b-1115"}
    assert bf.clean_declaration({"store_code": ""}) == {"store_code": None}
    assert bf.clean_declaration({"history_chunk_days": "14"}) == {"history_chunk_days": 14}
    assert bf.clean_declaration({"history_chunk_days": ""}) == {"history_chunk_days": None}
    assert bf.clean_declaration({"history_chunk_days": "999"})["history_chunk_days"] <= 45
    # a key nobody sent is not invented
    assert bf.clean_declaration({"label": "x"}) == {}
