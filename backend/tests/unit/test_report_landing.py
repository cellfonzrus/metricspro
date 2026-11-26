"""Unit: where a declared report's rows land (index §COMMCALC-85).

Found 2026-11-26 with 27 per-store back-office logins imported and not one able to accept a file:
the `target` a report declares is read by the pull and by the download console, and could be
WRITTEN by nothing. `report_declaration.columns` — the only writer of a declaration — did not carry
the field, so every save dropped it. Measured in production: not one declared report on the
platform had a target.
"""
from app.modules.commcalc import processor_backoffice_sweep as pbs
from app.modules.commcalc import report_declaration as rd
from app.modules.commcalc import report_landing as rl

CONN = pbs.CONNECTOR
TGT = sorted(pbs.TARGETS)[0]


def src(reports, calibration=None, processor=CONN):
    return {"processor": processor, "portal_reports": reports,
            "portal_calibration": calibration or {}}


# ── the legal landings are the connector's own ────────────────────────────────────────────────────
def test_the_landings_offered_are_the_connectors_own_targets():
    assert rl.targets_for(CONN) == tuple(sorted(pbs.TARGETS))


def test_a_connector_that_lands_nothing_offers_nothing_and_says_so():
    assert rl.targets_for("pos_report") == ()
    assert rl.lands_anything("pos_report") is False


def test_an_unknown_or_missing_connector_lands_nothing_rather_than_raising():
    assert rl.targets_for("no-such-connector") == ()
    assert rl.targets_for(None) == ()
    assert rl.targets_for("") == ()


def test_a_legal_target_is_folded_not_rejected_on_spelling():
    assert rl.clean_target(CONN, "  " + TGT.upper() + " ") == TGT


def test_a_target_the_connector_does_not_land_answers_nothing():
    """Refused where an operator can fix it, rather than stored to be refused hours later by a loop
    nobody is watching."""
    assert rl.clean_target(CONN, "somewhere_else") == ""


def test_one_connectors_landing_is_not_anothers():
    assert rl.clean_target("pos_report", TGT) == ""


# ── the advice ────────────────────────────────────────────────────────────────────────────────────
def test_an_undeclared_landing_is_named_as_the_thing_to_choose():
    said = rl.target_problem(CONN, "")
    assert "No landing is declared" in said and TGT in said


def test_a_connector_that_lands_nothing_says_nothing_about_the_row():
    """Advice must be actionable. There is nothing for an operator to pick, and the fact belongs to
    the connector — the editor reads it from an empty `landing_targets` and says it once."""
    assert rl.target_problem("pos_report", "") == ""


def test_a_stored_landing_the_connector_lost_is_named_back():
    assert "does not land" in rl.target_problem(CONN, "retired_landing")


# ── the round trip: the property that was false ───────────────────────────────────────────────────
def test_the_editor_reads_a_declared_landing_back():
    rows = rd.rows(src([{"key": "daily_tx", "target": TGT}]))
    assert rows[0]["target"] == TGT


def test_a_save_writes_the_landing_again():
    rows = rd.rows(src([{"key": "daily_tx", "target": TGT}]))
    reports, _ = rd.columns(rows, None, processor=CONN)
    assert reports[0]["target"] == TGT


def test_what_is_saved_is_what_the_pull_resolves():
    reports, _ = rd.columns([{"key": "daily_tx", "target": TGT}], None, processor=CONN)
    assert pbs.report_specs(src_row={"portal_reports": reports})[0]["target"] == TGT


def test_an_undeclared_landing_is_not_stored_as_an_empty_string():
    """Absence already means "do not store these rows, and say so" to every reader."""
    reports, _ = rd.columns([{"key": "daily_tx"}], None, processor=CONN)
    assert "target" not in reports[0]


def test_an_illegal_landing_is_not_stored_at_all():
    reports, _ = rd.columns([{"key": "daily_tx", "target": "nope"}], None, processor=CONN)
    assert "target" not in reports[0]


def test_a_declaration_from_before_this_field_round_trips_unchanged():
    """The property that makes the editor safe to open on 27 logins at once."""
    old = [{"key": "daily_tx", "label": "Daily TX"}]
    reports, _ = rd.columns(rd.rows(src(old)), None, processor=CONN)
    assert reports == [{"key": "daily_tx", "label": "Daily TX", "nav": ["daily"], "export": "csv"}]


def test_the_connector_must_be_named_on_every_save():
    """No house default: one connector's vocabulary standing in for every other's is the one place
    the default pattern is banned, and the failure it causes is silent."""
    try:
        rd.columns([{"key": "daily_tx"}], None)
    except TypeError:
        return
    raise AssertionError("columns() accepted a save that never said which connector it was for")
