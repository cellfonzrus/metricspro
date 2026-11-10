"""A CONTROL IS FOUND BY WHAT IT ANSWERS TO, NOT BY HOW IT LOOKS (index §COMMCALC-57).

THE DEFECT, settled by a photograph on 2026-11-10. §COMMCALC-56 had just made the comp report's
failure say which of three things went wrong, and it said this, on every run:

    no 'Summarize by' dropdown offering Daily was found on the report's filter panel

The owner photographed that panel. "Summarize by" was right there, and opening it offered Daily,
Weekly and Monthly. The portal had re-skinned that one control to the SEARCHABLE jqWidgets flavour
— its popup carries a "Looking for" box — which wears a different class and answers to a different
API. The scan knew one styling class, `.jqx-dropdownlist-state-normal`, so it reported a control it
was standing in front of as ABSENT.

THE CLASS: a scan keyed on appearance goes blind the day appearance changes, and it goes blind
SILENTLY — "not found" is indistinguishable from "not there", and only "not there" is actionable.
Ask the page what the control ANSWERS TO instead: offer each candidate every widget API we know,
and whichever one returns a list of items is what that control really is.

AND THE SIBLING PROVES IT IS A CLASS, NOT AN INSTANCE. Two functions in this file scanned for the
same control family with near-identical code; one had a click-the-control fallback behind it and
the other did not, so only one of them went blind. That is the duplicate the house forbids, with
one copy fixed and the other not. There is now one routine and both callers read it.

WHERE THE REAL REGRESSION LIVES. The defect was in browser JavaScript that no test executed, so it
could not be caught here. `backend/prove_jqx_picker.mjs` runs the REAL scan, read out of the module,
against a fake DOM shaped like the photograph. These tests cover the Python half: the shape the
wrapper guarantees, and that both callers dereference it.
"""
import pytest

from app.modules.commcalc import epay_sweep as es


class FakePicker:
    """A page whose only job is to answer the picker scan."""

    def __init__(self, result=None, raises=False):
        self._result = result
        self._raises = raises
        self.asked = []

    def evaluate(self, script, arg=None):
        self.asked.append(arg)
        if self._raises:
            raise RuntimeError("page closed")
        return self._result

    def wait_for_timeout(self, ms):
        return None


def _scan(ok=True, selected="Daily", widget="jqxComboBox", offered=("Daily", "Weekly"), pickers=1,
          why=None):
    return {"ok": ok,
            "hit": ({"id": "x", "widget": widget, "selected": selected} if selected else None),
            "offered": list(offered), "pickers": pickers, "why": why}


# ── the shape the one home guarantees ────────────────────────────────────────────────────────────

def test_the_live_shape_a_searchable_combobox_is_driven_and_reported():
    p = FakePicker(_scan(selected="Daily", widget="jqxComboBox"))
    r = es.pick_jqx_item(p, ["Daily"])
    assert r["ok"] is True
    assert r["selected"] == "Daily"
    assert r["widget"] == "jqxComboBox"


def test_the_page_is_asked_with_every_declared_selector_and_family():
    """Adding a flavour must be a declaration, not an edit buried in a string of JavaScript."""
    p = FakePicker(_scan())
    es.pick_jqx_item(p, ["Daily"])
    wants, selectors, widgets = p.asked[0]
    assert wants == ["Daily"]
    assert selectors == list(es.JQX_PICKER_SELECTORS)
    assert widgets == list(es.JQX_PICKER_WIDGETS)


def test_both_widget_families_are_declared():
    """The plain list AND the searchable one. Dropping either re-opens this defect."""
    assert "jqxDropDownList" in es.JQX_PICKER_WIDGETS
    assert "jqxComboBox" in es.JQX_PICKER_WIDGETS


def test_no_selector_is_keyed_on_a_widget_STATE():
    """`-state-normal` is a MOMENT in a control's life (normal / hover / focus), not its identity.
    Keying the scan on one was half of why a present control read as absent."""
    for sel in es.JQX_PICKER_SELECTORS:
        assert "-state-" not in sel, sel


def test_a_scan_that_finds_nothing_is_a_failure_with_a_reason_not_an_exception():
    p = FakePicker(_scan(ok=False, selected=None, offered=(), pickers=0,
                         why="no picker was found on the panel at all"))
    r = es.pick_jqx_item(p, ["Daily"])
    assert r["ok"] is False
    assert r["selected"] is None
    assert "no picker" in r["why"]


def test_a_dead_page_is_reported_as_itself():
    r = es.pick_jqx_item(FakePicker(raises=True), ["Daily"])
    assert r["ok"] is False
    assert "RuntimeError" in r["why"]
    assert r["offered"] == [] and r["pickers"] == 0


def test_a_scan_that_returns_rubbish_still_returns_the_promised_keys():
    """Every caller unpacks the same keys; a page that answers oddly must not make them absent."""
    r = es.pick_jqx_item(FakePicker("not a dict"), ["Daily"])
    assert set(r) == {"ok", "selected", "widget", "offered", "pickers", "why"}
    assert r["ok"] is False


def test_ok_is_never_true_without_the_widgets_own_answer():
    """§COMMCALC-56 again: the verification is what the thing we set says afterwards. A scan
    claiming success with nothing selected is not success."""
    r = es.pick_jqx_item(FakePicker({"ok": True, "hit": {"id": "x", "selected": None}}), ["Daily"])
    assert r["ok"] is False


# ── what the operator is told ────────────────────────────────────────────────────────────────────

def test_a_failure_names_what_the_panel_ACTUALLY_offers():
    """The whole cost of this defect was a sentence that said only what the panel did not have.
    One that lists what it does have answers the question without a photograph."""
    p = FakePicker(_scan(ok=False, selected=None, offered=("Hourly", "Weekly"), pickers=1,
                         why="the panel has 1 picker(s), none offering it"))
    ok, detail, confirmed = es._set_daily_range(p, "2026-11-01", "2026-11-08")
    assert (ok, confirmed) == (False, False)
    assert "Hourly" in detail and "Weekly" in detail


def test_a_panel_with_no_picker_at_all_reads_differently_from_one_with_the_wrong_options():
    """Opposite fixes: rebuild the leg, or widen the scan. They must not share a sentence."""
    none_at_all = es.pick_jqx_item(
        FakePicker(_scan(ok=False, selected=None, offered=(), pickers=0,
                         why="no picker was found on the panel at all")), ["Daily"])
    wrong_options = es.pick_jqx_item(
        FakePicker(_scan(ok=False, selected=None, offered=("Weekly",), pickers=2,
                         why="the panel has 2 picker(s), none offering it")), ["Daily"])
    assert none_at_all["why"] != wrong_options["why"]
    assert none_at_all["pickers"] == 0 and wrong_options["pickers"] == 2


# ── matching on meaning ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("portal_spelling", ["Daily", "daily", " Daily ", "Daily:", "DAILY"])
def test_the_portals_own_spelling_is_not_ours_to_predict(portal_spelling):
    """A control reading 'daily ' must not be failed as if it read 'Weekly'."""
    ok, _d, _c = es._set_daily_range(
        _PageFor(selected=portal_spelling), "2026-11-01", "2026-11-08")
    assert ok is True


def test_a_control_reading_something_ELSE_is_still_fatal():
    ok, detail, _c = es._set_daily_range(_PageFor(selected="Weekly"), "2026-11-01", "2026-11-08")
    assert ok is False and "Weekly" in detail


class _PageFor:
    """A whole `_set_daily_range` page: pick, then the corroborating field, then the dates."""

    def __init__(self, selected="Daily", field=None):
        self._selected, self._field = selected, field

    def evaluate(self, script, arg=None):
        if "jqxDateTimeInput" in script:
            return {"ok": True, "begin": "2026-11-01", "end": "2026-11-08"}
        if "selectItem" in script:
            return _scan(selected=self._selected)
        return self._field

    def wait_for_timeout(self, ms):
        return None


# ── one home, both callers ───────────────────────────────────────────────────────────────────────

def test_the_month_picker_reads_the_same_routine(monkeypatch):
    """The sibling that did NOT go blind must not keep its own scan, or the two drift again."""
    seen = {}

    def fake(page, wants):
        seen["wants"] = list(wants)
        return {"ok": True, "selected": "June- 26", "widget": "jqxDropDownList",
                "offered": [], "pickers": 1, "why": None}

    monkeypatch.setattr(es, "pick_jqx_item", fake)

    class P:
        def wait_for_timeout(self, ms):
            return None

    assert es._set_report_month(P(), "June", 2026) is True
    assert any("June" in w and "2026" in w for w in seen["wants"])
    assert any("June" in w and "26" in w for w in seen["wants"])


def test_neither_home_learns_which_tenant_it_is_acting_for():
    """Owner directive 2026-10-30. A control's required value is a property of the REPORT; an org
    argument appearing here is how a per-tenant divergence would start."""
    import inspect
    for fn in (es.pick_jqx_item, es._set_daily_range, es._set_report_month):
        assert "org" not in inspect.signature(fn).parameters
