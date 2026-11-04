"""A CAPTURE'S SUGGESTION IS FOLDED IN BY THE FUNCTION DECLARED FOR IT, NOT BY THE BROWSER.

Owner, 2026-10-31. He signed in to the UPS Store reporting portal, pressed *Read this page*, and was
told *"Read 14 pages (, , , , , , , , , , , , , ). Anything new is added below"*. Fourteen blanks,
and nothing was added below: every one of the fourteen reports still read *"no filters are declared,
so it will export whatever the page happens to show"*.

THE DEFECT, and it is one fact with two spellings. `report_declaration.merge_suggestion` has been the
declared home for "fold a capture into a row" since §COMMCALC-48 — and it had NO production caller.
The editor had written its own copy in TypeScript, and the copy asked a different question:

  * a per-report RESULT names its report `report` in this module — `epay_sweep`,
    `merchant_portal_sweep`, `portal_report_pull.pull_one_report` and
    `capture_report_calibration` all spell it that way;
  * an editor ROW names it `key`;
  * the browser's copy did `suggested.find(x => x.key === row.key)`.

`x.key` is `undefined` on every capture entry, so the find never matched — for any tenant, any portal
and any report. That is why the names in his message were blank: the same `s.key` was being printed.
The feature had never worked once.

THE FIX. The suggestions never reach the browser as something to merge. The route folds them in
through `merge_suggestion`, which keys reports by `pos_report_sweep.spec_key` — the one rule for
"which report is this" — and de-duplicates on the selector AND the normalised label, which the
browser's copy did not. The browser displays what came back.
"""
import ast
import inspect
import os

import pytest

from app.modules.commcalc import pos_report_sweep as sweep
from app.modules.commcalc import report_declaration as rd
from app.modules.commcalc import portal_report_pull as prp
from app.modules.commcalc import router as r

FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "frontend", "src")
EDITOR = os.path.join(FRONTEND, "components", "commcalc", "PortalReportEditor.tsx")

# His fourteen, in his own words, as the live row carries them — three of them is enough to prove the
# shape, and the vocabulary is his, never this file's business to know (RULE TWO).
HIS_ROWS = [
    {"key": "item_sales", "label": "Item Sales", "nav": ["Reporting", "POS", "Item Sales"],
     "export": "csv", "params": [], "line_map": {}},
    {"key": "tender_summary", "label": "Tender Summary",
     "nav": ["Reporting", "POS", "Tender Summary"], "export": "csv", "params": [], "line_map": {}},
    {"key": "worker_sales_by_product_category", "label": "Worker Sales By Product/Category",
     "nav": ["Reporting", "POS", "Worker Sales By Product", "Category"], "export": "csv",
     "params": [], "line_map": {}},
]
# What the capture really answers with — `report`, the per-report-result spelling.
CAPTURED = {
    "report": "item_sales", "label": "Item Sales", "reached": True, "controls_seen": 7,
    "params": [{"selector": "#dtFrom", "kind": "fill", "value": "{date_from}", "label": "From"},
               {"selector": "#dtTo", "kind": "fill", "value": "{date_to}", "label": "To"}],
    "run_selector": "#btnRun", "export_selector": "#btnCsv",
    "unrecognised": [{"selector": "#cboTz", "label": "Time zone",
                      "why": "the page's own words do not say what this is for"}],
}


# ── the two spellings, named ─────────────────────────────────────────────────────────────────────
def test_a_capture_names_its_report_the_way_every_per_report_result_does():
    """ARMED. If `capture_report_calibration` ever renamed this field, the merge below would stop
    finding the row and this suite is what says so."""
    src = inspect.getsource(prp.capture_report_calibration)
    assert '"report": spec.get("key")' in src
    assert '"key": spec' not in src, "a capture must not also carry the editor ROW's spelling"


def test_the_row_spelling_and_the_result_spelling_are_not_the_same_word():
    """The defect's whole mechanism in one line: these two differ, so nothing may compare them
    directly — `spec_key` is the only bridge."""
    assert "key" in rd.ROW_FIELDS and CAPTURED["report"]
    assert CAPTURED.get("key") is None
    assert sweep.spec_key(CAPTURED["report"]) == sweep.spec_key(HIS_ROWS[0])


# ── the one home does the fold ───────────────────────────────────────────────────────────────────
def test_the_declared_merge_folds_a_real_capture_into_his_real_rows():
    out = rd.merge_suggestion(HIS_ROWS, CAPTURED["report"], CAPTURED)
    got = {r_["key"]: r_["params"] for r_ in out}
    assert [p["selector"] for p in got["item_sales"]] == ["#dtFrom", "#dtTo"]
    assert [p["value"] for p in got["item_sales"]] == ["{date_from}", "{date_to}"]
    assert got["tender_summary"] == [] and got["worker_sales_by_product_category"] == []


def test_the_browsers_old_rule_reproduced_adds_nothing():
    """ARMED: `x.key === row.key` over the same inputs — the owner's fourteen blanks."""
    assert all(CAPTURED.get("key") != r_["key"] for r_ in HIS_ROWS)


def test_a_filter_a_human_already_declared_by_hand_is_not_added_twice():
    """The browser's copy de-duplicated on the SELECTOR alone, so the same box declared under a
    different selector came back a second time. The declared merge also compares the label."""
    hand = [dict(HIS_ROWS[0], params=[{"selector": "input[name=from]", "kind": "fill",
                                       "value": "{date_from}", "label": "From"}])]
    out = rd.merge_suggestion(hand, CAPTURED["report"], CAPTURED)
    sels = [p["selector"] for p in out[0]["params"]]
    assert "input[name=from]" in sels and "#dtFrom" not in sels, sels
    assert "#dtTo" in sels, "a filter the row genuinely lacks is still added"


# ── the route dereferences it, and says what each page did ───────────────────────────────────────
def _answer(res, editing):
    return r._calibration_answer(client=None, org_id="00000000-0000-0000-0000-000000000001",
                                 sid="ds-1", res=res, editing=editing)


def test_the_route_returns_rows_already_merged_not_suggestions_to_merge():
    src = inspect.getsource(r._calibration_answer)
    assert "rd.merge_suggestion(" in src, "the route must call the declared home"
    out = _answer({"reports": [CAPTURED]}, HIS_ROWS)
    assert out["ok"] is True
    rows = {x["key"]: x for x in out["reports"]}
    assert [p["selector"] for p in rows["item_sales"]["params"]] == ["#dtFrom", "#dtTo"]
    assert out["added"] == 2 and out["reached"] == 1 and out["pages"] == 1


def test_an_unsaved_edit_is_merged_into_and_never_over():
    """`editing` is the rows as they stand on the screen. A merge into the STORED rows would drop
    what the operator had just typed."""
    typed = [dict(HIS_ROWS[0], nav=["Reporting", "POS", "Item Sales Detail"])] + HIS_ROWS[1:]
    out = _answer({"reports": [CAPTURED]}, typed)
    assert out["reports"][0]["nav"] == ["Reporting", "POS", "Item Sales Detail"]
    assert len(out["reports"][0]["params"]) == 2


@pytest.mark.parametrize("entry,reached,suggested", [
    ({"report": "tender_summary", "label": "Tender Summary", "reached": False,
      "error": "Could not reach this report — declare its menu words or its address first."},
     False, 0),
    ({"report": "tender_summary", "label": "Tender Summary", "reached": True, "controls_seen": 0,
      "error": "Reached the page but it offered no readable controls."}, True, 0),
])
def test_zero_filters_says_which_of_the_reasons_it_was(entry, reached, suggested):
    """§COMMCALC-44 — an absence needs the RIGHT reason. "Read 14 pages" was none of them."""
    out = _answer({"reports": [entry]}, HIS_ROWS)
    c = out["capture"][0]
    assert c["reached"] is reached and c["suggested"] == suggested and c["added"] == 0
    assert c["error"] and c["report"] == "tender_summary"
    assert out["reached"] == (1 if reached else 0)
    assert "no new filter was found" in out["note"]


def test_the_controls_nobody_could_name_reach_the_operator():
    out = _answer({"reports": [CAPTURED]}, HIS_ROWS)
    assert out["capture"][0]["unrecognised"] == [{
        "label": "Time zone", "selector": "#cboTz",
        "why": "the page's own words do not say what this is for"}]


# ── the lock: the browser may not fold again ─────────────────────────────────────────────────────
def _editor_code():
    """The editor's code with its COMMENTS removed. The comments SHOULD cite the old rule — that
    citation is why the next reader keeps the fix."""
    src = open(EDITOR, encoding="utf-8").read()
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith(("//", "*", "/*")))


def test_the_editor_keeps_no_second_copy_of_the_fold():
    code = _editor_code()
    read = code[code.index("async function readPage("):]
    read = read[:read.index("\n  }") + 4]
    assert "setRows(r.reports)" in read, "the editor must display the merged rows it was given"
    for banned in ("suggested.find", ".find(x =>", "have.has", "new Set(row.params"):
        assert banned not in read, "the browser is folding the capture in again: %r" % banned
    assert "x.key" not in read, "the per-report-result spelling is `report`, never `key`"


def test_the_editor_sends_the_rows_on_screen_with_the_capture():
    code = _editor_code()
    assert "JSON.stringify({ reports: rows })" in code, \
        "without the rows on screen the merge lands on the stored declaration"


def test_the_editor_shows_what_each_page_said():
    code = _editor_code()
    assert "setCapture(r.capture" in code and "What each report page said" in code
