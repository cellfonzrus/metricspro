"""A REPORT'S OWN VERDICT BELONGS ON THE REPORT'S OWN ROW (index §COMMCALC-68).

Found 2026-11-10 23:40. `commcalc.report_definitions` carries `sweep_last_status` and
`sweep_last_detail` per report, and the connectors / setup surface already READS them —
`router._setup_documents` sets `urgent` and `auto_error` from exactly those two columns. **Nothing
has ever written them.** Both are NULL on every row, so a report whose automatic pull fails every
night shows as not urgent, with no error, on the surface built to show precisely that.

Every sweep writes instead to ONE shared column on the connector's config row,
`epay_sweep_config.last_detail`, overwritten by whichever leg ran last. The Comprehensive
Compensation leg runs at 23:30 America/New_York; the Daily Transaction Detail leg runs every thirty
minutes. So comp's sentence has been erased within half an hour, every night, which is why three
rounds of diagnosis this evening were reading the transaction report's failures and reasoning about
comp.

The registry written but not wired, for the fourth time in this repo (index §19.18).
"""
import pytest

from app.modules.commcalc import epay_sweep as es
from app.modules.commcalc import report_status as rs


def test_the_columns_a_report_s_verdict_is_written_to_are_named_once():
    assert rs.REPORT_STATUS_COLUMNS == ("sweep_last_status", "sweep_last_detail")


def test_each_sweep_report_key_resolves_to_the_registry_row_it_belongs_to():
    # The mapping already exists in the report's own declaration; nothing restates it.
    assert es.registry_key_of("comp_report") == "comp_report"
    assert es.registry_key_of("mi") == "mi_report"
    assert es.registry_key_of("epay_daily_tx") == "epay_daily_tx"
    for key, spec in es.REPORTS.items():
        assert es.registry_key_of(key) == spec["registry_key"], key


def test_an_unknown_report_key_is_refused_rather_than_written_to_a_guessed_row():
    with pytest.raises(KeyError):
        es.registry_key_of("not_a_report")


def test_each_report_gets_its_own_row_with_its_own_sentence():
    rows = rs.rows_for({"comp_report": ("error", "could not set the date filter"),
                        "epay_daily_tx": ("ok", "376 rows")})
    by = {r["report_key"]: r for r in rows}
    assert by["comp_report"]["sweep_last_status"] == "error"
    assert by["comp_report"]["sweep_last_detail"] == "could not set the date filter"
    assert by["epay_daily_tx"]["sweep_last_status"] == "ok"
    assert len(rows) == 2


def test_one_report_s_failure_never_reaches_another_report_s_row():
    rows = rs.rows_for({"comp_report": ("error", "comp's own failure")})
    assert [r["report_key"] for r in rows] == ["comp_report"]


def test_a_sentence_is_fitted_so_a_cap_cannot_decide_which_half_survives():
    long = "A" * 800 + "THE CONCLUSION IS WRITTEN LAST"
    row = rs.rows_for({"mi": ("error", long)})[0]
    said = row["sweep_last_detail"]
    assert len(said) <= rs.DETAIL_CAP
    # The sweep's conclusion is the LAST thing written, which is exactly what `detail[:cap]`
    # throws away — the defect core.status_detail was written for.
    assert "THE CONCLUSION IS WRITTEN LAST" not in long[:rs.DETAIL_CAP], "sample proves nothing"
    assert "THE CONCLUSION IS WRITTEN LAST" in said, "a head cut throws away the conclusion"
    assert said.startswith("AAA"), "and the opening is still readable"


def test_the_run_reports_a_verdict_per_report_and_the_caller_writes_it():
    import inspect
    run = inspect.getsource(es.run_epay_sweep)
    assert 'summary["per_report"]' in run, "the key is known here and nowhere else"
    # Every leg's outcome is recorded where its key is in scope — success, failure and the
    # unvouched zero that makes a run 'partial'.
    assert run.count("outcomes[key]") >= 2
    assert "per_report=outcomes" in run, \
        "a run that fails entirely must still carry each report's reason"


def test_a_run_that_dies_entirely_still_carries_each_report_s_verdict():
    e = es.EpayPortalError("everything failed", per_report={"comp_report": ("error", "why")})
    assert e.per_report == {"comp_report": ("error", "why")}
    # and the plain form still behaves like an ordinary error
    assert es.EpayPortalError("plain").per_report == {}


def test_nothing_writes_a_verdict_to_the_shared_connector_row_instead():
    import inspect
    from app.modules.commcalc import router
    src = inspect.getsource(router._do_epay_sweep)
    assert "report_status.write" in src or "_write_report_verdicts" in src, \
        "the per-report columns must actually be written, or this is a registry nobody fills"
