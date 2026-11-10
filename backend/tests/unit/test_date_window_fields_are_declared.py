"""THE FIELDS A REPORT'S DATE WINDOW IS TYPED INTO ARE DECLARED, NOT ASSUMED (§COMMCALC-67).

Measured 2026-11-10 22:00, the first tick on §COMMCALC-66's wording:

    could not set this report's own date filter — this report's date filter has not been declared …
    the panel's controls are Run Report: empty; … Username: empty; Date of most recent transaction
    to include (mandatory): empty; Retailer Account ID: empty; Terminal ID: empty;
    the page's date fields are StartDate, EndDate

So Daily Transaction Detail has no "Summarize by" and types its window into **StartDate / EndDate**,
while the routine in this module has always typed into **BeginDate / EndDate**. Two facts a report
declares, not one word in the code: whether its window needs an interval set first, and which two
fields carry the dates.

The comp report keeps `BeginDate`/`EndDate` because that is what it has always used — and its own
page has still never been measured, so when the 23:30 leg reports, correcting it is a row in this
declaration rather than an edit to a JavaScript string.
"""
import pytest

from app.modules.commcalc import epay_sweep as es


def test_each_routine_declares_the_two_fields_it_types_into():
    for name, plan in es.DATE_FILTERS.items():
        if name == es.DATE_FILTER_UNDECLARED:
            continue
        begin, end = es.date_window_fields(name)
        assert begin and end and begin != end, name


def test_the_comp_report_still_types_into_the_fields_it_always_did():
    assert es.date_window_fields(es.SUMMARIZE_BY_DAILY) == ("BeginDate", "EndDate")


def test_daily_transaction_detail_types_into_the_fields_its_page_actually_has():
    assert es.date_window_fields(es.START_END_DATES) == ("StartDate", "EndDate")
    assert es.date_filter_of(es.REPORTS["epay_daily_tx"]) == es.START_END_DATES


def test_only_the_comp_report_s_routine_sets_an_interval_first():
    assert es.date_window_interval(es.SUMMARIZE_BY_DAILY) == es.DAILY_INTERVAL
    assert es.date_window_interval(es.START_END_DATES) is None


def test_the_field_names_are_passed_into_the_page_rather_than_written_into_it():
    # A name baked into the JavaScript is a name no report can declare.
    assert "BeginDate" not in es._SET_DATES_JS
    assert "StartDate" not in es._SET_DATES_JS
    assert "beginName" in es._SET_DATES_JS and "endName" in es._SET_DATES_JS


def test_an_undeclared_routine_still_refuses_rather_than_guessing_fields():
    with pytest.raises(es.EpayPortalError):
        es.date_window_fields(es.DATE_FILTER_UNDECLARED)
    with pytest.raises(es.EpayPortalError):
        es.date_window_fields("whatever_the_next_page_wants")


def test_there_is_one_routine_for_setting_a_window_and_both_reports_go_through_it():
    import inspect
    src = inspect.getsource(es._open_and_download)
    assert "_set_date_window(" in src
    assert not hasattr(es, "_set_daily_range"), "two date setters is the duplicate the house forbids"


def test_a_report_with_no_interval_never_asks_the_page_for_one():
    calls = []

    class _Page:
        def evaluate(self, js, arg=None):
            calls.append(js)
            if js is es._SET_DATES_JS:
                return {"ok": True, "begin": arg[0], "end": arg[1]}
            return None

        def wait_for_timeout(self, _ms):
            return None

    ok, detail, confirmed = es._set_date_window(_Page(), "2026-11-01", "2026-11-02",
                                                es.START_END_DATES)
    assert ok is True, detail
    assert es._PICK_JQX_ITEM_JS not in calls, "it must not hunt for a control this page lacks"
    assert "StartDate" in detail or "2026-11-01" in detail


def test_the_fields_that_did_not_take_are_named_back_with_the_report_s_own_names():
    class _Page:
        def evaluate(self, js, arg=None):
            if js is es._SET_DATES_JS:
                return {"ok": False, "begin": None, "end": "2026-11-02"}
            return None

        def wait_for_timeout(self, _ms):
            return None

    ok, detail, _ = es._set_date_window(_Page(), "2026-11-01", "2026-11-02", es.START_END_DATES)
    assert ok is False
    assert "StartDate" in detail, detail
