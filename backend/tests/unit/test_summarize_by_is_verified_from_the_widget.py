"""A CONTROL IS VERIFIED FROM THE THING WE SET, NOT FROM A FIELD BESIDE IT (index §COMMCALC-56).

THE DEFECT, measured live on 2026-11-09. The login fault of §COMMCALC-52/55 is fixed: every run
since says "Login OK", the MI/ATU feed is pulling daily and the payment-detail feed back-filled
October by itself. The comprehensive comp report is still empty, for a different reason, and the
sweep has been reporting it like this on every tick:

    could not set the report's daily date filter — 'Summarize by' could not be set to Daily
    (hidden field = None); the report returns an empty workbook without it

`_set_daily_range` SETS a jqWidgets dropdown and then PROVES it took by reading a different
element — `input[name="DateIntervalOption"]`, the portal's own form plumbing, under a name this
platform does not own. That field now reads None, i.e. it is not on the page at all, so the setter
reports failure whether or not the dropdown was set. One sentence covers three different states and
an operator cannot tell which happened.

The sibling in the same file already does it right: `_set_report_month` reads the widget's own
`getSelectedItem()` — what we set, asked of the thing we set.

THE RULE: the widget's own answer is the verification. A field beside it is CORROBORATION — its
absence is not disagreement, and its disagreement is fatal.

AND THE ZERO STAYS HONEST. Proceeding on the widget alone is weaker evidence than proceeding on
both, so the run records the control as set-but-unconfirmed and a zero-row pull still refuses to be
published as "nothing posted" (`empty_pull_verdict`).
"""
import pytest

from app.modules.commcalc import epay_sweep as es
from app.modules.commcalc import empty_pull_verdict as ev


class FakePage:
    """The three evaluate() calls `_set_daily_range` makes, and nothing else.

    Distinguished by what the script mentions, because the driver passes the script source. Since
    §COMMCALC-57 the picker scan is the SHARED one (`_PICK_JQX_ITEM_JS`), the corroborating form
    field is read on its own, and the dates are last.
    """

    def __init__(self, *, interval=None, field=None, dates=None,
                 interval_raises=False, field_raises=False, dates_raises=False):
        self._interval = interval
        self._field = field
        self._dates = dates if dates is not None else {"ok": True, "begin": "2026-11-01",
                                                       "end": "2026-11-08"}
        self._interval_raises = interval_raises
        self._field_raises = field_raises
        self._dates_raises = dates_raises
        self.calls = []

    def evaluate(self, script, arg=None):
        if "jqxDateTimeInput" in script:
            self.calls.append(("dates", arg))
            if self._dates_raises:
                raise RuntimeError("page closed")
            return self._dates
        if "selectItem" in script:
            self.calls.append(("pick", arg))
            if self._interval_raises:
                raise RuntimeError("page closed")
            return self._interval
        self.calls.append(("field", arg))
        if self._field_raises:
            raise RuntimeError("page closed")
        return self._field

    def wait_for_timeout(self, ms):
        return None


def _iv(selected=None, ok=None, offered=(), pickers=0, why=None):
    """What the shared picker scan hands back for the 'Summarize by' control."""
    return {"ok": (bool(selected) if ok is None else ok),
            "hit": ({"id": "x", "widget": "jqxComboBox", "selected": selected}
                    if selected is not None else None),
            "offered": list(offered), "pickers": pickers, "why": why}


BEGIN, END = "2026-11-01", "2026-11-08"


# ── the regression ───────────────────────────────────────────────────────────────────────────────

def test_the_widget_says_daily_and_the_field_beside_it_is_gone():
    """THE LIVE SHAPE. The dropdown took; the hidden input is simply not on the page any more. The
    pull must go ahead — refusing it is how this report has stayed empty."""
    ok, detail, confirmed = es._set_daily_range(
        FakePage(interval=_iv(selected="Daily"), field=None), BEGIN, END)
    assert ok is True
    assert confirmed is False
    assert "could not be confirmed" in detail


def test_and_the_operator_is_told_which_half_is_missing():
    _, detail, _ = es._set_daily_range(
        FakePage(interval=_iv(selected="Daily"), field=None), BEGIN, END)
    assert "Daily" in detail
    assert "DateIntervalOption" in detail or "hidden" in detail


def test_both_halves_agreeing_is_a_fully_confirmed_set():
    ok, detail, confirmed = es._set_daily_range(
        FakePage(interval=_iv(selected="Daily"), field="Daily"), BEGIN, END)
    assert (ok, confirmed) == (True, True)
    assert BEGIN in detail and END in detail


# ── the failures that must stay failures ─────────────────────────────────────────────────────────

def test_no_dropdown_offering_daily_is_a_failure_and_says_so():
    ok, detail, confirmed = es._set_daily_range(
        FakePage(interval=_iv(selected=None), field=None), BEGIN, END)
    assert ok is False and confirmed is False
    assert "no" in detail.lower() and "Daily" in detail


def test_a_widget_that_selected_something_else_is_a_failure():
    """Weaker than a missing field, not stronger: the thing we set says it is on Weekly."""
    ok, detail, confirmed = es._set_daily_range(
        FakePage(interval=_iv(selected="Weekly"), field=None), BEGIN, END)
    assert ok is False and confirmed is False
    assert "Weekly" in detail


def test_a_field_that_CONTRADICTS_the_widget_is_fatal():
    """Absence is not disagreement; disagreement is. If the form still carries the field and it
    says Monthly, the page has not accepted what we set."""
    ok, detail, confirmed = es._set_daily_range(
        FakePage(interval=_iv(selected="Daily"), field="Monthly"), BEGIN, END)
    assert ok is False and confirmed is False
    assert "Monthly" in detail


def test_an_empty_field_is_absence_not_disagreement():
    """A jqWidgets form often renders the input with no value until its own handler writes one."""
    ok, _d, confirmed = es._set_daily_range(
        FakePage(interval=_iv(selected="Daily"), field=""), BEGIN, END)
    assert ok is True and confirmed is False


def test_the_dates_must_still_take_exactly():
    ok, detail, confirmed = es._set_daily_range(
        FakePage(interval=_iv(selected="Daily"), field="Daily",
                 dates={"ok": False, "begin": "2026-12-01", "end": END}), BEGIN, END)
    assert ok is False and confirmed is False
    assert "2026-12-01" in detail


def test_a_dead_page_is_reported_as_itself_never_as_a_quiet_day():
    ok, detail, _c = es._set_daily_range(FakePage(interval_raises=True), BEGIN, END)
    assert ok is False and "RuntimeError" in detail
    ok2, detail2, _c2 = es._set_daily_range(
        FakePage(interval=_iv(selected="Daily"), field="Daily", dates_raises=True), BEGIN, END)
    assert ok2 is False and "RuntimeError" in detail2


def test_nothing_is_set_without_being_asked_for_daily():
    """The ORDER and the ASK, not the call shape: pick the control first, then read the field
    beside it, then the dates. (Since §COMMCALC-57 the pick goes through the shared routine, so
    what is asserted is that Daily is what we asked for — not how the argument is packed.)"""
    p = FakePage(interval=_iv(selected="Daily"), field="Daily")
    es._set_daily_range(p, BEGIN, END)
    assert [k for k, _a in p.calls] == ["pick", "field", "dates"]
    assert es.DAILY_INTERVAL in p.calls[0][1][0]
    assert p.calls[1][1] == es._INTERVAL_FIELD
    assert p.calls[2][1] == [BEGIN, END]


# ── the zero stays honest (the whole reason this may be relaxed at all) ──────────────────────────

def test_an_unconfirmed_control_still_refuses_to_publish_a_zero():
    v = ev.classify_empty_pull(empty_allowed=True, label="Comprehensive Comp",
                               unconfirmed_controls=[es.DAILY_RANGE_CONTROL],
                               window=(BEGIN, END), ever_landed=True, days_since_last_row=1)
    assert v["trusted"] is False
    assert "nothing posted" not in v["sentence"] or "NOT reported" in v["sentence"]


def test_a_proven_broken_control_still_outranks_an_unconfirmed_one():
    """Two different claims with two different sentences; the stronger one must win so the operator
    reads the real reason."""
    both = ev.classify_empty_pull(empty_allowed=True, label="X",
                                  broken_controls=["the filter"],
                                  unconfirmed_controls=["the filter"], ever_landed=True)
    assert both["reason"] == "control_proven_broken"
    only = ev.classify_empty_pull(empty_allowed=True, label="X",
                                  unconfirmed_controls=["the filter"], ever_landed=True)
    assert only["reason"] == "control_not_confirmed"
    assert only["sentence"] != both["sentence"]


def test_an_unconfirmed_control_outranks_the_sources_own_no_records():
    """Same precedence as a proven-broken control: if we are not sure the filter took, the source
    answered a question we may not have asked, whatever it says about having no records."""
    v = ev.classify_empty_pull(empty_allowed=True, label="X",
                               corroboration=ev.SOURCE_REPORTED_EMPTY,
                               unconfirmed_controls=["the filter"], ever_landed=True)
    assert v["trusted"] is False


def test_a_NONZERO_pull_is_unaffected_by_an_unconfirmed_control():
    """The relaxation may not make real rows suspect — only a zero is in question."""
    led = ev.ControlLedger()
    led.control_unconfirmed(es.DAILY_RANGE_CONTROL, "hidden field absent")
    assert led.deferred == []          # nothing deferred ⇒ nothing reclassified


def test_the_ledger_carries_the_unconfirmed_control_to_every_leg_of_the_run():
    led = ev.ControlLedger()
    res = {"report": "comp_report", "rows": 0}
    led.defer(res, controls=(es.DAILY_RANGE_CONTROL,), empty_allowed=True, label="Comprehensive Comp",
              window=(BEGIN, END), ever_landed=True, days_since_last_row=1)
    led.control_unconfirmed(es.DAILY_RANGE_CONTROL, "hidden field absent")   # a LATER leg proves it
    (_r, v), = led.settle()
    assert v["trusted"] is False
    assert res["mode"] == "unverified_no_data"


def test_a_control_both_broken_and_unconfirmed_reads_as_broken():
    led = ev.ControlLedger()
    res = {"rows": 0}
    led.defer(res, controls=("c",), empty_allowed=True, label="X", ever_landed=True)
    led.control_unconfirmed("c", "absent")
    led.control_failed("c", "unsettable")
    (_r, v), = led.settle()
    assert v["reason"] == "control_proven_broken"
