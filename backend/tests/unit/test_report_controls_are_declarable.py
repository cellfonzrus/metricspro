"""THE CONTROLS THE PULL PRESSES MUST BE DECLARABLE, OR A COMPLETE DECLARATION IMPORTS NOTHING.

Owner, 2026-11-04: *"How to get the filters right"*, four days after the capture was fixed to fold its
suggestions in (§COMMCALC-48.9) and still with nothing captured.

WHAT WAS ACTUALLY WRONG. `portal_report_pull` reads FOUR facts per report off `portal_calibration`:
the filters (`report_params`), the Run control (`run_report`), the Export control
(`download_report`), and an address or a menu selector (`navigate_to_report`). The Reports editor
could write exactly ONE of them — `params`. So:

  * `suggest_params` has always returned `run_selector` and `export_selector` under their own keys,
    and `merge_suggestion` read only `params`: BOTH were discarded on every capture;
  * with no Export control declared, `download_report` hunts for a control whose words happen to be
    Export / Download / CSV / Excel. A portal spelling its button anything else imports zero rows
    while the declaration reads complete;
  * with no address declared, `navigate_to_report` can only CLICK the menu words — and on a portal
    whose reports all render on one page there is nothing to click.

Measured on the owner's own fourteen declared reports, 2026-11-04 — every one of them
`params 0 | run None | export None | address None`, against a login whose portal_url is a single
`.aspx` page.

THE FIX. `CONTROL_FIELDS` declares the three a human can answer, in one place; the editor row carries
them, `columns` writes them, `merge_suggestion` folds a capture's into an EMPTY one only, and the
capture now also suggests the address it arrived at — the one caller standing on the page.
`nav_selector` is deliberately not here (nothing suggests it, no human would hand-write it) and is
still folded through untouched.
"""
import inspect

import pytest

from app.modules.commcalc import pos_report_sweep as sweep
from app.modules.commcalc import portal_report_pull as prp
from app.modules.commcalc import report_declaration as rd
from app.modules.commcalc import router as r

HOUSE = "00000000-0000-0000-0000-000000000001"

# His own fourteen, as the live row carries them — three is enough to prove the shape, and the
# vocabulary is his (RULE TWO).
SRC = {
    "portal_reports": [
        {"key": "item_sales", "label": "Item Sales", "nav": ["Reporting", "POS", "Item Sales"],
         "export": "csv"},
        {"key": "tender_summary", "label": "Tender Summary",
         "nav": ["Reporting", "POS", "Tender Summary"], "export": "csv"},
    ],
    "portal_calibration": {
        "item_sales": {
            "params": [{"selector": "#dtFrom", "kind": "fill", "value": "{date_from}",
                        "label": "From"}],
            "run_selector": "#btnRun",
            # A fact this editor does not edit. It must survive a save untouched.
            "nav_selector": "#ctl00_menu_ItemSales",
        },
    },
}


# ── the three are declared once ─────────────────────────────────────────────────────────────────
def test_the_controls_are_declared_in_one_place():
    assert tuple(rd.CONTROL_FIELDS) == ("report_url", "run_selector", "export_selector")
    for f in rd.CONTROL_FIELDS:
        assert f in rd.ROW_FIELDS, f


def test_each_one_is_a_fact_the_pull_actually_reads():
    """ARMED: a field here that the pull never reads would be a screen collecting dead config."""
    src = (inspect.getsource(prp.run_report) + inspect.getsource(prp.download_report)
           + inspect.getsource(prp.navigate_to_report))
    for f in rd.CONTROL_FIELDS:
        assert 'calibrated(' in src and '"%s"' % f in src, \
            "%s is declared but no pull step reads it off the calibration" % f


def test_nav_selector_is_deliberately_not_offered():
    assert "nav_selector" not in rd.CONTROL_FIELDS
    assert "nav_selector" not in rd.ROW_FIELDS


# ── the row carries them, and the round trip is still the identity ──────────────────────────────
def test_the_row_shows_what_is_declared_and_what_is_blank():
    rows = {x["key"]: x for x in rd.rows(SRC)}
    assert rows["item_sales"]["run_selector"] == "#btnRun"
    assert rows["item_sales"]["export_selector"] == ""
    assert rows["item_sales"]["report_url"] == ""
    assert rows["tender_summary"]["run_selector"] == ""


def test_opening_the_editor_and_saving_still_changes_nothing():
    """The property that makes an editor safe. It must survive three new fields."""
    rows = rd.rows(SRC)
    reports, cal = rd.columns(rows, SRC["portal_calibration"])
    assert rd.rows({"portal_reports": reports, "portal_calibration": cal}) == rows
    assert cal["item_sales"]["run_selector"] == "#btnRun"
    assert cal["item_sales"]["nav_selector"] == "#ctl00_menu_ItemSales", \
        "a calibration fact this editor does not edit was dropped by a save"


def test_a_control_typed_on_the_screen_is_stored():
    rows = rd.rows(SRC)
    rows[0]["export_selector"] = "#ctl00_btnExportCsv"
    rows[0]["report_url"] = "https://portal.example/UI/Reporting/POS/POSReports.aspx"
    _, cal = rd.columns(rows, SRC["portal_calibration"])
    assert cal["item_sales"]["export_selector"] == "#ctl00_btnExportCsv"
    assert cal["item_sales"]["report_url"].endswith("POSReports.aspx")


@pytest.mark.parametrize("cleared", ["", "   ", None])
def test_a_control_cleared_on_the_screen_is_cleared_in_the_declaration(cleared):
    """Not stored empty — the KEY goes, so `calibrated()` reads "nothing declared" exactly as it did
    before this editor could write it."""
    rows = rd.rows(SRC)
    rows[0]["run_selector"] = cleared
    _, cal = rd.columns(rows, SRC["portal_calibration"])
    assert "run_selector" not in cal["item_sales"]
    assert prp.calibrated({"portal_calibration": cal}, "item_sales", "run_selector") in (None, "", {})


def test_a_selector_is_taken_AS_WRITTEN():
    """A CSS selector is case-sensitive and a portal's generated id depends on it, so the cleaner
    trims and never normalises — unlike a report key, which `spec_key` folds."""
    rows = rd.rows(SRC)
    rows[0]["export_selector"] = "  #ctl00_ContentPlaceHolder1_btnExportCSV  "
    _, cal = rd.columns(rows, SRC["portal_calibration"])
    assert cal["item_sales"]["export_selector"] == "#ctl00_ContentPlaceHolder1_btnExportCSV"


# ── a capture's controls fold the same way its filters do ───────────────────────────────────────
CAPTURED = {
    "report": "tender_summary", "label": "Tender Summary", "reached": True, "controls_seen": 9,
    "params": [{"selector": "#dtFrom", "kind": "fill", "value": "{date_from}", "label": "From"}],
    "run_selector": "#btnRun", "export_selector": "#btnCsv",
    "report_url": "https://portal.example/UI/Reporting/POS/POSReports.aspx",
}


def test_the_capture_suggests_the_address_it_arrived_at():
    src = inspect.getsource(prp.capture_report_calibration)
    assert 'out["report_url"] = here' in src
    assert "page.url" in src


def test_a_capture_fills_the_controls_that_are_blank():
    out = rd.merge_suggestion(rd.rows(SRC), CAPTURED["report"], CAPTURED)
    row = next(x for x in out if x["key"] == "tender_summary")
    assert row["run_selector"] == "#btnRun"
    assert row["export_selector"] == "#btnCsv"
    assert row["report_url"].endswith("POSReports.aspx")
    assert [p["selector"] for p in row["params"]] == ["#dtFrom"]


def test_a_capture_never_overwrites_a_control_a_human_typed():
    """ARMED: the human was looking at the page. Same rule as a corrected filter selector."""
    rows = rd.rows(SRC)
    rows[0]["export_selector"] = "#mine"            # item_sales, typed by hand
    sug = dict(CAPTURED, report="item_sales")
    out = rd.merge_suggestion(rows, "item_sales", sug)
    assert out[0]["export_selector"] == "#mine"
    assert out[0]["run_selector"] == "#btnRun", "the one that was ALREADY declared also stands"
    assert out[0]["report_url"].endswith("POSReports.aspx"), "...and a blank one is still filled"


def test_the_old_merge_dropped_both_which_is_the_defect_reproduced():
    """ARMED. `suggest_params` returns these under their own keys and always has; a merge reading
    only `params` discards them, which is why fourteen captures left `run None | export None`."""
    assert "run_selector" in CAPTURED and "export_selector" in CAPTURED
    assert all(p.get("selector") != CAPTURED["run_selector"] for p in CAPTURED["params"]), \
        "the Run control is deliberately NOT a param — a param pressing Run exports the previous run"


# ── the operator is told which ones the row now has ─────────────────────────────────────────────
def test_the_capture_panel_names_each_control_it_did_and_did_not_settle():
    out = r._calibration_answer(client=None, org_id=HOUSE, sid="ds-1",
                                res={"reports": [CAPTURED]}, editing=rd.rows(SRC))
    c = out["capture"][0]
    assert c["controls"] == {"report_url": True, "run_selector": True, "export_selector": True}
    bare = dict(CAPTURED)
    for f in rd.CONTROL_FIELDS:
        bare.pop(f, None)
    c2 = r._calibration_answer(client=None, org_id=HOUSE, sid="ds-1",
                               res={"reports": [bare]}, editing=rd.rows(SRC))["capture"][0]
    assert c2["controls"] == {"report_url": False, "run_selector": False, "export_selector": False}


def test_a_row_with_no_export_control_says_so_in_the_operators_words():
    """The one omission that reads as working and is not."""
    probs = " · ".join(rd.rows(SRC)[0]["problems"])
    assert "no export control is declared" in probs
    rows = rd.rows(SRC)
    rows[0]["export_selector"] = "#btnCsv"
    assert all("export control" not in p for p in rd.row_problems(rows[0]))


def test_either_the_menu_words_or_an_address_satisfies_reaching_the_page():
    """`navigate_to_report` tries the declared address BEFORE the menu words, so on a one-page portal
    the address is the only one of the two that identifies anything."""
    bare = {"key": "x", "label": "X", "nav": [], "params": [], "export_selector": "#e"}
    assert any("menu words" in p for p in rd.row_problems(bare))
    assert all("menu words" not in p
               for p in rd.row_problems(dict(bare, report_url="https://portal.example/r.aspx")))


# ── the screen offers all three, and keeps no list of its own ───────────────────────────────────
EDITOR = "frontend/src/components/commcalc/PortalReportEditor.tsx"


def _editor():
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    return open(os.path.join(root, EDITOR), encoding="utf-8").read()


def test_the_editor_offers_every_control_the_backend_declares():
    src = _editor()
    for f in rd.CONTROL_FIELDS:
        assert "patch(i, { %s: e.target.value })" % f in src, \
            "%s is declared and the editor offers no way to type it" % f


def test_the_backend_publishes_the_field_names_rather_than_the_browser_keeping_them():
    assert '"control_fields": list(rd.CONTROL_FIELDS)' in inspect.getsource(r.data_source_reports)


def test_a_new_row_carries_the_same_fields_as_a_stored_one():
    """A fresh row missing them would save as a row that cannot be round-tripped."""
    src = _editor()
    new = src[src.index("setRows(rs => [...rs, {"):]
    new = new[:new.index("}")]
    for f in rd.CONTROL_FIELDS:
        assert f in new, f
