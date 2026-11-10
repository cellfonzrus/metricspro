"""A CONTROL IS FOUND BY WHAT IT ANSWERS TO, NOT BY HOW IT LOOKS (index §COMMCALC-58).

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
    wants, selectors, widgets, sample = p.asked[0]
    assert wants == ["Daily"]
    assert selectors == list(es.JQX_PICKER_SELECTORS)
    assert widgets == list(es.JQX_PICKER_WIDGETS)
    assert sample == es.PICKER_SAMPLE, "the per-control sample size is DECLARED, not inlined"


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
    assert set(r) == {"ok", "selected", "widget", "offered", "panel", "candidates", "pickers",
                      "ready", "empty", "looked", "waited_ms", "why"}
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


# ── a picker that exists but has not filled in is NOT absent (§COMMCALC-61) ──────────────────────

def _panel(candidates=1, pickers=1, ready=0, empty=1, offered=(), selected=None, why=None):
    return {"ok": bool(selected),
            "hit": ({"id": "x", "widget": "jqxComboBox", "selected": selected} if selected else None),
            "offered": list(offered), "candidates": candidates, "pickers": pickers,
            "ready": ready, "empty": empty, "why": why}


class _Stages:
    """A page that answers differently each time it is asked — a panel filling itself in."""

    def __init__(self, *stages):
        self._stages = list(stages)
        self.asked = 0
        self.waited = []

    def evaluate(self, script, arg=None):
        self.asked += 1
        return self._stages[min(self.asked, len(self._stages)) - 1]

    def wait_for_timeout(self, ms):
        self.waited.append(ms)


def test_a_panel_still_filling_in_is_waited_for_and_then_found():
    """THE LIVE SHAPE, 2026-11-10 16:30. The dealer list had loaded and the filter control had not,
    so the only picker carrying anything was the account selector."""
    page = _Stages(_panel(empty=1, ready=1, pickers=2,
                          offered=["474517 - Passaic wireless 2022 LLC(230)"]),
                   _panel(empty=0, ready=2, pickers=2, selected="Daily",
                          offered=["Daily", "Weekly", "Monthly"]))
    r = es.pick_jqx_item(page, ["Daily"])
    assert r["ok"] is True
    assert r["selected"] == "Daily"
    assert page.asked == 2 and page.waited, "it must actually have waited"


def test_a_ready_panel_that_simply_does_not_offer_it_is_NOT_waited_on():
    """Another look cannot make a loaded control start offering a value it does not have. Spending
    the budget proving that four times only delays the honest answer."""
    page = _Stages(_panel(pickers=1, ready=1, empty=0, offered=["Weekly", "Monthly"],
                          why="the panel has 1 picker(s) with options, none offering it"))
    r = es.pick_jqx_item(page, ["Daily"])
    assert r["ok"] is False
    assert page.asked == 1 and page.waited == []


def test_an_empty_picker_is_counted_and_reported_as_not_ready():
    """Before this it was invisible: skipped, uncounted, and the operator was told the panel had
    one fewer control than it really had."""
    page = _Stages(_panel(pickers=2, ready=1, empty=1,
                          why="the panel has 1 picker(s) with options, none offering it "
                              "(and 1 still empty)"))
    r = es.pick_jqx_item(page, ["Daily"])
    assert r["pickers"] == 2 and r["ready"] == 1 and r["empty"] == 1
    assert "still empty" in r["why"]


def test_the_failure_says_how_long_it_waited():
    """A slow panel and an absent control read identically without it."""
    page = _Stages(_panel(candidates=0, pickers=0, ready=0, empty=0,
                          why="no picker was found on the panel at all"))
    r = es.pick_jqx_item(page, ["Daily"], settle_ms=1000)
    assert r["looked"] == es.PICKER_PATIENCE + 1
    assert f"looked {r['looked']} times" in r["why"]
    assert f"{r['waited_ms'] / 1000:g}s" in r["why"]


def test_the_wait_is_bounded_and_cannot_be_switched_off_into_an_infinite_one():
    """Since §COMMCALC-62 the bound is PATIENCE (progress) and a DEADLINE, not a look count, so
    there is no caller-supplied number that can turn the wait into an unbounded one."""
    page = _Stages(_panel(candidates=0, pickers=0, empty=0, why="none"))
    r = es.pick_jqx_item(page, ["x"], settle_ms=1)
    assert r["looked"] <= es.PICKER_PATIENCE + 1
    assert es.PICKER_SETTLE_MS > 0 and es.PICKER_PATIENCE >= 1 and es.PICKER_DEADLINE_MS > 0
    import inspect
    assert "looks" not in inspect.signature(es.pick_jqx_item).parameters, \
        "a parameter that bounds nothing misleads the next reader"


def test_a_success_on_the_first_look_never_waits():
    page = _Stages(_panel(ready=1, pickers=1, empty=0, selected="Daily", offered=["Daily"]))
    assert es.pick_jqx_item(page, ["Daily"])["ok"] is True
    assert page.asked == 1 and page.waited == []


# ── the wait follows PROGRESS, and no one control may drown the report (§COMMCALC-62) ────────────
#
# Measured live at 2026-11-10 18:30, on the §COMMCALC-61 code:
#
#     the panel has 3 picker(s) with options, none offering it (and 10 still empty)
#     (looked 4 times over 4.5s while the panel settled)
#
# TWO facts in one sentence, and both were new. The panel has THIRTEEN controls, not the one the
# 16:30 run reported. And ten of them were STILL empty when the fixed 4.5s budget ran out, which
# says the budget expired while the page was still working — a fixed number guessed at once cannot
# know how slow the portal is today.
#
# The second fact is why this took three rounds to see. `offered` was one merged list, and one of
# those controls is the DEALER list carrying hundreds of accounts, so it filled the whole sentence
# and the recorder truncated the rest. Both other loaded controls were in that message and neither
# could be read. A report in which the biggest control wins is a report that hides the panel.


class _Filling:
    """A panel that fills one control in per look, then stops — and counts what it was asked."""

    def __init__(self, *, empty, ready=0, fills=True, selected_at=None, pickers=None):
        self._empty, self._ready, self._fills = empty, ready, fills
        self._selected_at, self._pickers = selected_at, pickers or (empty + ready)
        self.asked, self.waited = 0, []

    def evaluate(self, script, arg=None):
        self.asked += 1
        if self.asked > 1 and self._fills and self._empty > 0:
            self._empty -= 1
            self._ready += 1
        sel = "Daily" if (self._selected_at and self.asked >= self._selected_at) else None
        return _panel(candidates=self._pickers, pickers=self._pickers, ready=self._ready,
                      empty=self._empty, selected=sel,
                      offered=(["Daily"] if sel else []),
                      why="the panel has pickers, none offering it")

    def wait_for_timeout(self, ms):
        self.waited.append(ms)


def test_the_wait_keeps_going_while_the_panel_is_STILL_FILLING_past_the_old_cap():
    """THE 18:30 SHAPE. Ten controls still unfilled when the budget ran out means the budget, not
    the portal, decided the answer. While controls keep arriving, keep looking."""
    page = _Filling(empty=10, ready=3, selected_at=9)
    r = es.pick_jqx_item(page, ["Daily"])
    assert r["ok"] is True, r["why"]
    assert page.asked >= 9, f"gave up after {page.asked} looks while the panel was still filling"


def test_a_panel_that_STOPS_filling_is_not_waited_on_for_ever():
    """Progress is the licence to wait. When nothing new fills in, waiting has become guessing."""
    page = _Filling(empty=6, ready=1, fills=False)
    r = es.pick_jqx_item(page, ["Daily"])
    assert r["ok"] is False
    assert page.asked <= es.PICKER_PATIENCE + 2, f"kept looking {page.asked} times at a dead panel"


def test_and_a_stalled_panel_SAYS_it_stopped_filling_with_controls_still_empty():
    """Its own fact, and the actionable one: the controls exist, the page gave up on them."""
    page = _Filling(empty=6, ready=1, fills=False)
    why = es.pick_jqx_item(page, ["Daily"])["why"]
    assert "6" in why and "stopped filling" in why


def test_the_whole_wait_is_capped_by_a_DEADLINE_however_much_progress_there_is():
    """A panel that fills one control per look for ever must not hold the sweep open for ever."""
    page = _Filling(empty=10_000, ready=1)
    es.pick_jqx_item(page, ["Daily"])
    assert sum(page.waited) <= es.PICKER_DEADLINE_MS
    assert 0 < es.PICKER_DEADLINE_MS <= 60_000, "a deadline nobody can read is not a deadline"


# ── no single control may drown the report ───────────────────────────────────────────────────────

def _with_panel(entries, selected=None, why="none offering it"):
    """The scan's per-picker report: one entry per control, as the page describes itself."""
    p = _panel(candidates=len(entries), pickers=len(entries),
               ready=sum(1 for e in entries if e.get("options")),
               empty=sum(1 for e in entries if not e.get("options")),
               selected=selected, why=why)
    p["panel"] = entries
    p["offered"] = [lab for e in entries for lab in e.get("sample", [])]
    return p


def test_the_scan_describes_each_control_separately_not_as_one_merged_list():
    page = _Stages(_with_panel([
        {"id": "DealerList", "widget": "jqxComboBox", "options": 480,
         "sample": [f"4745{n} - Wireless LLC" for n in range(8)]},
        {"id": "PaymentCategory", "widget": "jqxDropDownList", "options": 3,
         "sample": ["All", "Commission", "Residual"]},
        {"id": "SummarizeBy", "widget": "jqxComboBox", "options": 0, "sample": []},
    ]))
    r = es.pick_jqx_item(page, ["Daily"])
    assert [e["id"] for e in r["panel"]] == ["DealerList", "PaymentCategory", "SummarizeBy"]
    assert r["panel"][0]["options"] == 480, "the real size must still be reported"


def test_a_control_with_hundreds_of_options_contributes_only_a_SAMPLE():
    """The whole defect in one assertion: 480 dealer names must not be able to fill the sentence."""
    page = _Stages(_with_panel([
        {"id": "DealerList", "widget": "jqxComboBox", "options": 480,
         "sample": [f"4745{n} - Wireless LLC" for n in range(es.PICKER_SAMPLE)]},
        {"id": "PaymentCategory", "widget": "jqxDropDownList", "options": 2,
         "sample": ["All", "Commission"]},
    ]))
    r = es.pick_jqx_item(page, ["Daily"])
    assert len(r["offered"]) <= 2 * es.PICKER_SAMPLE
    assert "All" in r["offered"], "the SMALL control's options must survive the big one"
    assert 0 < es.PICKER_SAMPLE <= 20


def test_the_failure_sentence_names_every_control_and_stays_readable():
    """What the operator reads. Before this, one control's contents WERE the whole sentence."""
    page = _Stages(_with_panel([
        {"id": "DealerList", "widget": "jqxComboBox", "options": 480,
         "sample": [f"4745{n} - Wireless LLC" for n in range(es.PICKER_SAMPLE)]},
        {"id": "PaymentCategory", "widget": "jqxDropDownList", "options": 2,
         "sample": ["All", "Commission"]},
        {"id": "SummarizeBy", "widget": "jqxComboBox", "options": 0, "sample": []},
    ]))
    ok, detail, confirmed = es._set_daily_range(page, "2026-11-01", "2026-11-08")
    assert (ok, confirmed) == (False, False)
    for name in ("DealerList", "PaymentCategory", "SummarizeBy"):
        assert name in detail, f"{name} is not named in the failure"
    assert "480" in detail, "the dealer list's real size must be stated, not its contents"
    assert "empty" in detail, "the unfilled control must be marked as unfilled"
    assert len(detail) < 1200, f"the sentence is {len(detail)} chars - it will be truncated again"


# ── a thing inside a thing is not a second control, and a generated id is not a name (§C-63) ─────
#
# Measured live at 2026-11-10 19:30, on the §COMMCALC-62 code — the first tick that named the
# controls:
#
#     the panel has 2 picker(s) with options, none offering it (and 11 still empty) … (
#     innerListBoxjqxWidgetaa329b45: empty; innerListBoxjqxWidget5ea7ca38: empty;
#     listBoxContentjqxWidget0206f1e1: empty; jqxWidget349f357e: empty;
#     dropDownButtonContentjqxWidget349f357e: empty)
#
# THOSE ARE NOT THIRTEEN CONTROLS. `innerListBox…`, `listBoxContent…` and `dropDownButtonContent…`
# are jqWidgets' own internal scaffolding, and the hashes repeat: `jqxWidget0206f1e1` appears twice
# and `jqxWidget349f357e` appears twice. Our selectors match a widget's host AND its inner list box
# AND its button content, so ONE control was counted several times.
#
# Two consequences, both of which hid the panel further:
#   * the inner parts can NEVER carry items, so `empty > 0` was permanently true — the wait ran its
#     whole budget on every tick waiting for scaffolding, and always reported a stall; and
#   * every "name" in that sentence is a jqWidgets-generated id. It tells the operator nothing, and
#     it is not stable across page loads, so it cannot even be compared between ticks.


def _entry(id=None, label=None, widget="jqxComboBox", options=0, sample=()):
    return {"id": id, "label": label, "widget": widget, "options": options, "sample": list(sample)}


def test_the_on_screen_label_is_what_the_operator_is_shown():
    d = es.describe_panel({"panel": [_entry(id="jqxWidget349f357e", label="Summarize by")]})
    assert "Summarize by" in d
    assert "jqxWidget349f357e" not in d, "a generated id must not be shown when a real name exists"


def test_a_GENERATED_id_is_not_a_name():
    """It changes every page load, so it cannot identify a control even to its next tick."""
    d = es.describe_panel({"panel": [_entry(id="innerListBoxjqxWidgetaa329b45")]})
    assert "jqxWidgetaa329b45" not in d
    assert "unnamed" in d.lower() or "jqxComboBox" in d


def test_a_REAL_id_is_still_a_usable_name_when_there_is_no_label():
    d = es.describe_panel({"panel": [_entry(id="SummarizeByList")]})
    assert "SummarizeByList" in d


def test_the_generated_id_pattern_is_declared_not_inlined():
    assert es.GENERATED_ID_RE.search("innerListBoxjqxWidgetaa329b45")
    assert es.GENERATED_ID_RE.search("jqxWidget0206f1e1")
    assert not es.GENERATED_ID_RE.search("SummarizeBy")
    assert not es.GENERATED_ID_RE.search("DealerList")


def test_scaffolding_that_can_never_fill_does_not_keep_the_wait_alive():
    """THE COST OF THE DEFECT. `innerListBox` can never carry items, so `empty > 0` never cleared
    and every tick spent its whole budget before reporting. With nesting dropped at the source the
    scan sees one control, and a ready panel that simply lacks the value is not waited on at all."""
    page = _Stages(_with_panel([
        {"id": None, "label": "Summarize by", "widget": "jqxComboBox", "options": 3,
         "sample": ["Hourly", "Weekly", "Monthly"]},
    ], why="the panel has 1 picker(s) with options, none offering it"))
    r = es.pick_jqx_item(page, ["Daily"])
    assert r["ok"] is False
    assert page.asked == 1 and page.waited == [], "it waited on a panel with nothing left to fill"
