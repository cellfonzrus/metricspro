"""WHICH CONTROLS SET A REPORT'S WINDOW IS A PROPERTY OF THAT REPORT'S PAGE (index §COMMCALC-66).

Measured 2026-11-10 21:00, on the first tick after §COMMCALC-64 made the sweep name a control by
the caption printed beside it instead of by a generated id:

    Login OK · Daily Transaction Detail [2026-11-10]: … no 'Summarize by' picker offering Daily
    was found … the panel's controls are … 644078 - PA Phone Traders LLC(6507), +22 more;
    Username: empty; Date of most recent transaction to include (mandatory): empty;
    Retailer Account ID: empty; Terminal ID: empty

That is the DAILY TRANSACTION DETAIL panel, and it has no "Summarize by" on it. It never did — it
does not summarise, it takes a date. Both `comp_report` and `epay_daily_tx` declared
`filter: "daily_range"`, and that one word was driving the comp report's Summarize-by routine
against a page that has never carried that control. Three rounds of "the control is missing" were
about the wrong page.

So the routine is declared per report, a report that has not declared one is NOT driven through
another report's controls, and it is NOT run unfiltered either — an unfiltered day-grain pull would
land somebody else's window silently.
"""
import inspect

import pytest

from app.modules.commcalc import epay_sweep as es


def test_the_comp_report_declares_the_summarize_by_routine_it_actually_needs():
    assert es.date_filter_of(es.REPORTS["comp_report"]) == es.SUMMARIZE_BY_DAILY


def test_daily_transaction_detail_does_not_borrow_the_comp_report_s_control():
    assert es.date_filter_of(es.REPORTS["epay_daily_tx"]) != es.SUMMARIZE_BY_DAILY


def test_every_day_grain_report_declares_how_its_window_is_set():
    for key, spec in es.REPORTS.items():
        if spec.get("grain") == "day":
            assert es.date_filter_of(spec) in es.DATE_FILTERS, key


def test_a_report_that_declares_nothing_does_not_inherit_another_report_s_routine():
    # The defect in one line: silence must not resolve to the house's first day-grain report.
    assert es.date_filter_of({}) == es.DATE_FILTER_UNDECLARED
    assert es.date_filter_of(None) == es.DATE_FILTER_UNDECLARED


def test_a_routine_nobody_declared_is_refused_rather_than_guessed():
    with pytest.raises(es.EpayPortalError):
        es.date_filter_of({"date_filter": "whatever_the_next_page_wants"})


def test_the_run_s_target_carries_the_declared_routine_and_resolving_it_has_one_home():
    assert es.resolve_date_filter({"kind": "day_range",
                                   "date_filter": es.SUMMARIZE_BY_DAILY}) == es.SUMMARIZE_BY_DAILY
    assert es.resolve_date_filter({"kind": "day_range"}) == es.DATE_FILTER_UNDECLARED
    assert es.resolve_date_filter(None) == es.DATE_FILTER_UNDECLARED


def test_the_undeclared_report_is_not_driven_through_the_comp_report_s_routine():
    src = inspect.getsource(es._open_and_download)
    assert "resolve_date_filter(" in src, "the dispatch must dereference the one home"
    assert "SUMMARIZE_BY_DAILY" in src, "_set_daily_range is reached only through its own routine"


def test_the_two_reports_no_longer_share_one_ledger_control_name():
    # The ledger key is what invalidates OTHER legs' zeroes. While both legs wrote the same key, a
    # control broken on one page condemned the other page's honest zero as well.
    assert es.REPORTS["epay_daily_tx"]["controls"] != es.REPORTS["comp_report"]["controls"]
    assert es.UNDECLARED_DATE_CONTROL != es.DAILY_RANGE_CONTROL


def test_the_date_fields_are_reported_by_NAME_and_never_by_value():
    # A value on this panel can be an account id. The whole point is to learn what to set, so the
    # names are the answer and the contents are nobody's business.
    js = es._DATE_FIELD_NAMES_JS
    assert "getAttribute('name')" in js
    assert ".value" not in js


def test_the_undeclared_verdict_names_the_controls_the_page_actually_has():
    class _Page:
        def evaluate(self, js, arg=None):
            if js is es._DATE_FIELD_NAMES_JS:
                return ["TransactionDate"]
            return {"ok": False, "hit": None, "offered": [], "candidates": 2, "pickers": 2,
                    "ready": 0, "empty": 2, "why": "none offering it",
                    "panel": [
                        {"id": "jqxWidgetaa1", "label": "Terminal ID", "widget": "jqxComboBox",
                         "options": 0, "sample": []},
                        {"id": "jqxWidgetbb2",
                         "label": "Date of most recent transaction to include (mandatory)",
                         "widget": "jqxComboBox", "options": 0, "sample": []}]}

        def wait_for_timeout(self, _ms):
            return None

    said = es.describe_date_controls(_Page())
    assert "Date of most recent transaction to include (mandatory)" in said
    assert "Terminal ID" in said
    assert "TransactionDate" in said
    assert "Summarize by" not in said, "it must not report the control this page does not have"
