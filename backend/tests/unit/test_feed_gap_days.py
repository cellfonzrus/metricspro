"""Unit: a missing trading day is reported, and a store that sold nothing is NOT a missing day.

Owner (2026-10-31): *"this is a big problem for management iff we wil miss trading days, we need to
get this fix right away"* — 957 Pennsylvania Ave lost 22 and 23 September 2026 and nothing said so.

THE ONE ASSERTION THAT MATTERS is `test_a_measured_zero_is_never_a_missing_upload`. `measured_zero`
means the rows ARRIVED and none was an activation — a manager's problem. `not_reported` means the
rows never came — a file's problem. The report has distinguished them since 2026-09-22 (§15z); the
whole defect was that only one of the two ever reached anybody. Collapsing them here would send
managers chasing reps who sold fine, which is precisely what §15z exists to prevent.

The DB-free proof with the full state matrix is `backend/harness_feed_gap_days.py`; these are the
assertions CI runs on every push.
"""
import pytest

from app.modules.commcalc import import_audit as IA
from app.modules.commcalc import zero_sales as Z

WINDOW = ["2026-09-%02d" % d for d in range(1, 31)]


def _store(key, day_states, *, label=None, grain=None, parent=None, calendar="scheduled"):
    """A `zero_sales.build_report` row, carrying only what a feed-gap finding may read."""
    tally = {}
    for st in day_states.values():
        tally[st] = tally.get(st, 0) + 1
    return {"grain": grain or Z.GRAIN_STORE, "scope_key": key, "label": label or key,
            "parent": parent, "day_states": dict(day_states), "trading_calendar": calendar,
            "days_not_reported": tally.get(Z.NOT_REPORTED, 0),
            "days_measured_zero": tally.get(Z.MEASURED_ZERO, 0),
            "days_had_sales": tally.get(Z.HAD_SALES, 0),
            "days_closed": tally.get(Z.CLOSED, 0) + tally.get(Z.OFF, 0)}


def _report(*rows):
    return {"days": list(WINDOW), "rows": list(rows), "config": dict(Z.HOUSE_CONFIG)}


def _all(state):
    return {d: state for d in WINDOW}


def _lost(*dates):
    out = _all(Z.HAD_SALES)
    for d in dates:
        out[d] = Z.NOT_REPORTED
    return out


# ── THE NEGATIVE CONTROL ──────────────────────────────────────────────────────────────────────────
def test_a_measured_zero_is_never_a_missing_upload():
    """Rows landed every day and none was an activation. That is a finding, but not THIS finding."""
    sold_nothing = _store("S-ZERO", _all(Z.MEASURED_ZERO))
    assert sold_nothing["days_measured_zero"] == 30     # the report still sees it
    assert Z.feed_gap_items(_report(sold_nothing)) == []  # and we claim no missing file


def test_the_negative_control_can_actually_fail():
    """The same shape marked not-reported DOES raise — so the assertion above is armed, not vacuous."""
    assert len(Z.feed_gap_items(_report(_store("S-ZERO", _all(Z.NOT_REPORTED))))) == 1


def test_a_store_that_reported_every_day_is_silent():
    assert Z.feed_gap_items(_report(_store("S-OK", _all(Z.HAD_SALES)))) == []


# ── the finding itself ────────────────────────────────────────────────────────────────────────────
def test_the_finding_names_the_dates_so_somebody_can_re_pull_them():
    """The live instance: store 957 lost 22 and 23 September."""
    got = Z.feed_gap_items(_report(_store("S-957", _lost("2026-09-22", "2026-09-23"), label="957")))
    assert len(got) == 1
    assert got[0]["dates"] == ["2026-09-22", "2026-09-23"]
    assert got[0]["missing_days"] == 2
    assert got[0]["first_missing_day"] == "2026-09-22"
    assert got[0]["last_missing_day"] == "2026-09-23"
    assert got[0]["store_code"] == "S-957" and got[0]["label"] == "957"
    assert got[0]["kind"] == Z.FEED_GAP_PARTIAL


def test_a_feed_that_never_arrived_is_its_own_kind():
    got = Z.feed_gap_items(_report(_store("S-DEAD", _all(Z.NOT_REPORTED))))
    assert got[0]["kind"] == Z.FEED_GAP_WHOLE_WINDOW
    assert got[0]["missing_days"] == 30
    assert Z.FEED_GAP_WHOLE_WINDOW != Z.FEED_GAP_PARTIAL


# ── days that are not missing ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("state", [Z.CLOSED, Z.IN_PROGRESS, Z.OFF])
def test_a_day_the_store_was_not_trading_has_lost_no_upload(state):
    days = _all(Z.HAD_SALES)
    days["2026-09-06"] = state
    assert Z.feed_gap_items(_report(_store("S-SUN", days))) == []


def test_whole_window_is_judged_against_evaluated_days_not_the_calendar():
    """A store shut on Sundays whose every TRADING day is missing still has a dead feed."""
    days = {d: (Z.CLOSED if d.endswith(("06", "13", "20", "27")) else Z.NOT_REPORTED)
            for d in WINDOW}
    got = Z.feed_gap_items(_report(_store("S-DEAD2", days)))
    assert got[0]["kind"] == Z.FEED_GAP_WHOLE_WINDOW
    assert got[0]["missing_days"] == 26 and got[0]["days_evaluated"] == 26


# ── a missing feed is ONE store problem ───────────────────────────────────────────────────────────
def test_a_rep_row_adds_no_second_finding():
    """`rep_day_state` makes every rep under a gapped store not-reported by inheritance. Counting
    rep rows would report one store's missing upload once per person on the schedule."""
    lost = _lost("2026-09-22")
    got = Z.feed_gap_items(_report(
        _store("S-R", lost),
        _store(("S-R", "Rep A"), lost, label="Rep A", grain=Z.GRAIN_REP, parent="S-R")))
    assert [i["store_code"] for i in got] == ["S-R"]


# ── the threshold is config and cannot silence the report ─────────────────────────────────────────
def test_one_lost_day_is_a_real_gap_by_default():
    assert Z.FEED_GAP_MIN_DAYS == 1
    assert len(Z.feed_gap_items(_report(_store("S-ONE", _lost("2026-09-11"))))) == 1


def test_a_tenant_may_raise_the_threshold():
    assert Z.feed_gap_items(_report(_store("S-ONE", _lost("2026-09-11"))), min_days=2) == []


@pytest.mark.parametrize("bad", [0, -5, None, "", "nonsense"])
def test_a_threshold_that_would_silence_everything_is_refused(bad):
    """Honouring min_days=0 would report nothing at all — the same silence this check exists to end."""
    assert len(Z.feed_gap_items(_report(_store("S-ONE", _lost("2026-09-11"))), min_days=bad)) == 1


# ── ordering and the estate shape that surfaced this ──────────────────────────────────────────────
def test_worst_first_and_deterministic():
    rows = [_store("S-957", _lost("2026-09-22", "2026-09-23"), label="957"),
            _store("S-DEAD", _all(Z.NOT_REPORTED)),
            _store("S-OK", _all(Z.HAD_SALES)),
            _store("S-ZERO", _all(Z.MEASURED_ZERO))]
    got = Z.feed_gap_items(_report(*rows))
    assert [i["store_code"] for i in got] == ["S-DEAD", "S-957"]
    assert sum(i["missing_days"] for i in got) == 32
    assert Z.feed_gap_items(_report(*reversed(rows))) == got


# ── the wording handed to an admin ────────────────────────────────────────────────────────────────
def _attention(items):
    return IA.feed_gap_attention(items, "2026-09-01", "2026-09-30",
                                 whole_kind=Z.FEED_GAP_WHOLE_WINDOW)


def test_the_two_kinds_become_two_items_because_they_are_two_jobs():
    """A dead feed is a connector to repair; lost days are a file to re-pull. One combined item
    would send both to whichever page the louder half named."""
    got = _attention(Z.feed_gap_items(_report(
        _store("S-DEAD", _all(Z.NOT_REPORTED)),
        _store("S-957", _lost("2026-09-22", "2026-09-23"), label="957"))))
    assert [i["severity"] for i in got] == ["error", "warning"]
    assert {i["key"] for i in got} == {"commcalc:feed_gap:no_feed", "commcalc:feed_gap:short_days"}
    assert all(i["group"] == "import" for i in got)


def test_nothing_missing_means_no_attention_item():
    assert _attention([]) == []


def test_the_item_counts_store_days_and_links_to_the_window():
    got = _attention(Z.feed_gap_items(_report(_store("S-957", _lost("2026-09-22", "2026-09-23")))))
    assert got[0]["count"] == 2
    assert got[0]["deep_link"] == "/commcalc/zero-sales?date_from=2026-09-01&date_to=2026-09-30"


def test_a_long_store_list_is_truncated_rather_than_unreadable():
    rows = [_store(f"S-{n:02d}", _all(Z.NOT_REPORTED)) for n in range(20)]
    detail = _attention(Z.feed_gap_items(_report(*rows)))[0]["detail"]
    assert "and 14 more" in detail
    assert detail.count("S-") == IA.FEED_GAP_NAMED_IN_DETAIL


def test_the_detail_says_a_missing_day_is_not_a_zero():
    """The sentence an admin reads must not leave them thinking the store sold nothing."""
    got = _attention(Z.feed_gap_items(_report(_store("S-957", _lost("2026-09-22")))))
    assert "not a zero" in got[0]["detail"]
