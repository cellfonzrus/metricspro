"""§COMMCALC-48.13 — A REPORT THE TENANT HAS NOT ASKED FOR IS NOT A FAILED REPORT.

Owner, 2026-11-20: *"We need to assign the filters and the reports in team portal to the ones in our
system so the system can map them properly and pull the required reports"*.

Fifteen reports were declared on one login and the pull attempted all fifteen on every run, because
the only way to stop pulling one was to DELETE it — along with its menu words, its column bindings
and its line map. So fourteen uncalibrated reports produced fourteen navigation failures a day, and
the status sentence named four of them and dropped the rest in silence: the one report that was fully
mapped to the tenant's declared categories was among the eleven the sentence left out.

These tests pin the two halves of that:
  • a declared `pull: false` is honoured by the LOOP (not by deleting the report), absence means yes,
    and the switch is DECLARED rather than derived from how complete a report's calibration is;
  • a status sentence states the COUNT of what it did not reach before the names it has room for.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.commcalc import portal_report_pull as prp          # noqa: E402
from app.modules.commcalc import pos_report_sweep as sweep          # noqa: E402
from app.modules.commcalc import report_declaration as rd           # noqa: E402

# The connector these rows belong to. `columns` REQUIRES it since §COMMCALC-85.
PROCESSOR = "pos_report"

A = {"key": "alpha", "label": "Alpha", "nav": ["Reporting", "Alpha"], "export": "csv"}
B = {"key": "bravo", "label": "Bravo", "nav": ["Reporting", "Bravo"], "export": "csv"}


def _src(*items, **cal):
    return {"portal_reports": list(items), "portal_calibration": dict(cal)}


# ── the switch itself ────────────────────────────────────────────────────────────────────────────
def test_absence_means_pull_it():
    """Every declaration written before this field existed must pull exactly what it pulled."""
    assert prp.report_requested(_src(A), "alpha") is True


def test_declared_false_means_do_not_pull_it():
    assert prp.report_requested(_src(dict(A, pull=False)), "alpha") is False


def test_declared_true_means_pull_it():
    assert prp.report_requested(_src(dict(A, pull=True)), "alpha") is True


def test_the_switch_is_read_by_the_declarations_own_key_rule():
    """`Alpha` and `alpha` are one report — the §COMMCALC-39 rule, not a second spelling."""
    assert prp.report_requested(_src({"key": "Al-pha", "pull": False}), "al pha") is False


def test_a_report_nobody_declared_is_not_reported_as_switched_off():
    """There is no item to carry a switch, so the answer is the absence default, not False — a key
    that is not declared never reaches the loop at all and must not read as a deliberate exclusion."""
    assert prp.report_requested(_src(A), "zulu") is True


def test_the_field_name_is_spelled_once():
    assert prp.PULL_FIELD == "pull"
    assert rd.PULL_FIELD is prp.PULL_FIELD


def test_the_switch_is_not_derived_from_how_complete_the_calibration_is():
    """The contrast with `is_per_day` is the design. A report with no filters, no address and no
    export control is still PULLED unless somebody said otherwise — deriving "not wanted" from
    "not finished" would stop pulling a working report the moment a field was cleared."""
    bare = _src({"key": "alpha"})
    assert prp.report_requested(bare, "alpha") is True
    assert prp.is_per_day(bare, "alpha") is False


# ── the key rule has ONE home ────────────────────────────────────────────────────────────────────
def test_the_adapters_key_rule_dereferences_the_pulls():
    assert sweep.spec_key is not prp.report_key
    for shape in ({"key": "Item Sales"}, "Tender-Cash", " WORKER sales ", 3, None, {}):
        assert sweep.spec_key(shape) == prp.report_key(shape)


def test_the_report_key_rule_is_not_the_processor_rule():
    """They need the same spelling rules today and answer different questions; folding them together
    would make a change to one silently change the other."""
    src = open(prp.__file__).read()
    assert src.count("def report_key(") == 1
    assert src.count("def connector(") == 1


# ── the loop honours it ──────────────────────────────────────────────────────────────────────────
def test_a_skipped_report_is_carried_in_the_answer_with_its_own_reason():
    out = prp.summarize(
        [{"report": "bravo", "label": "Bravo", "rows_ingested": 0, "delivered": False,
          "not_requested": True}], 0, "2026-11-01", "2026-11-07", [], ["Bravo"])
    assert out["not_requested"] == ["Bravo"]
    assert "Not asked for: 1 — Bravo." in out["status"]
    assert "Not reached" not in out["status"]


def test_a_skipped_report_is_never_counted_as_unreached():
    out = prp.summarize([], 0, "2026-11-01", "2026-11-07", [], ["Bravo", "Alpha"])
    assert out["unreachable"] == []
    assert "Not reached" not in out["status"]


def test_the_loop_skips_before_it_navigates():
    """The ORDER is the whole point: a report nobody asked for must not cost a page load, and must
    not be able to fail. Pinned on the source text because exercising it needs a browser."""
    src = open(prp.__file__).read()
    loop = src.split("def pull_reports_on_page")[1].split("\ndef ")[0]
    assert loop.index("report_requested(") < loop.index("pull_one_report(")


# ── the status sentence stops understating ───────────────────────────────────────────────────────
def test_the_count_of_unreached_reports_is_always_stated():
    out = prp.summarize([], 0, "2026-11-01", "2026-11-07", ["a", "b", "c", "d", "e", "f"])
    assert "Not reached: 6 — a, b, c, d and 2 more." in out["status"]


def test_a_short_list_says_no_more():
    out = prp.summarize([], 0, "2026-11-01", "2026-11-07", ["a", "b"])
    assert "Not reached: 2 — a, b." in out["status"]
    assert "more" not in out["status"]


def test_every_unreached_report_is_in_the_answer_even_when_the_sentence_cannot_name_it():
    names = ["r%d" % i for i in range(15)]
    out = prp.summarize([], 0, "2026-11-01", "2026-11-07", names)
    assert out["unreachable"] == names


def test_summarize_still_answers_without_the_new_argument():
    """Every existing caller passes five arguments."""
    out = prp.summarize([], 0, "2026-11-01", "2026-11-07", [])
    assert out["not_requested"] == []


# ── the editor ───────────────────────────────────────────────────────────────────────────────────
def test_the_editor_row_shows_the_switch_through_the_pulls_own_reader():
    rows = rd.rows(_src(A, dict(B, pull=False)))
    assert [(r["key"], r["pull"]) for r in rows] == [("alpha", True), ("bravo", False)]


def test_the_editor_offers_the_switch_as_a_field():
    assert "pull" in rd.ROW_FIELDS


def test_ticked_on_is_the_absence_of_a_declaration():
    """Storing `pull: true` on every report would rewrite every declaration written before the field
    existed, and `columns(rows(src))` being the identity is what makes this editor safe to open."""
    reports, _ = rd.columns(rd.rows(_src(A)), processor=PROCESSOR)
    assert "pull" not in reports[0]


def test_switched_off_is_stored():
    reports, _ = rd.columns(rd.rows(_src(dict(A, pull=False))), processor=PROCESSOR)
    assert reports[0]["pull"] is False


def test_the_round_trip_is_still_the_identity_with_the_switch():
    src = _src(A, dict(B, pull=False),
               **{"alpha": {"params": [{"selector": "#d", "kind": "fill",
                                        "value": "{date}", "label": "From"}]}})
    once = rd.columns(rd.rows(src), src["portal_calibration"], processor=PROCESSOR)
    twice = rd.columns(rd.rows({"portal_reports": once[0], "portal_calibration": once[1]}),
                       once[1], processor=PROCESSOR)
    assert once == twice


def test_switching_a_report_off_keeps_everything_mapped_about_it():
    """The reason for a switch rather than a delete: the menu words, the line map and the column
    bindings are the work, and they must survive being switched off."""
    src = _src(dict(A, line_map={"NDA": "overnight"}),
               **{"alpha": {"params": [{"selector": "#d", "kind": "fill", "value": "{date}",
                                        "label": "From"}],
                            "column_bindings": {"amount": "Amount before tax"},
                            "unit_basis": "row", "export_selector": "#x"}})
    rows = rd.rows(src)
    rows[0]["pull"] = False
    reports, cal = rd.columns(rows, src["portal_calibration"], processor=PROCESSOR)
    assert reports[0]["pull"] is False
    assert reports[0]["line_map"] == {"NDA": "overnight"}
    assert reports[0]["nav"] == ["Reporting", "Alpha"]
    assert cal["alpha"]["column_bindings"] == {"amount": "Amount before tax"}
    assert cal["alpha"]["unit_basis"] == "row"
    assert cal["alpha"]["export_selector"] == "#x"
    assert len(cal["alpha"]["params"]) == 1


def test_a_report_that_is_not_pulled_is_not_nagged_about_filters():
    """Every one of those problems is about reaching and running a page. A report mapped so that a
    hand-uploaded file can be read needs no address, no filters and no export button."""
    row = rd.rows(_src(dict(B, pull=False)))[0]
    assert row["problems"] == [
        "not pulled — only files you upload by hand are read through this report"]


def test_a_report_that_is_pulled_is_still_nagged():
    # A two-letter key on purpose: `row()` shows the navigation fallback the pull would derive from
    # the key's own words, and a longer key would supply one and hide the missing-address advice.
    row = rd.rows(_src({"key": "ab", "label": "Ab"}))[0]
    assert any("menu words" in p for p in row["problems"])
    assert any("no filters are declared" in p for p in row["problems"])
    assert any("export control" in p for p in row["problems"])


def test_a_row_with_no_name_is_still_told_so_when_it_is_not_pulled():
    row = rd.row({"key": "x", "pull": False})
    row["key"] = ""
    assert "give this report a name" in rd.row_problems(row)


# ── RULE TWO ─────────────────────────────────────────────────────────────────────────────────────
def test_no_tenant_portal_or_report_is_named_by_the_switch():
    """Scanned over EXECUTABLE text only, through the one home for "this file with its prose
    removed" — a docstring explaining which tenant asked and why is history, and hand-tokenising
    Python to strip it is the mistake `py_code_only` exists to stop."""
    import harnesslib

    for mod in (prp, rd):
        code = harnesslib.py_code_only(open(mod.__file__).read()).lower()
        for word in ("team portal", "boost", "shopify", "sales_transactions",
                     "worker_sales", "item_sales", "cellfonz", "luxelink", "ups store"):
            assert word not in code, (mod.__name__, word)


def test_the_word_scan_is_armed():
    import harnesslib

    assert "shopify" in harnesslib.py_code_only('X = "shopify"').lower()
    assert "shopify" not in harnesslib.py_code_only('"""a shopify note"""\nX = 1').lower()


# ── the outcome lists are DERIVED from the results, not tallied beside them ──────────────────────
def test_the_reasons_are_read_off_the_results():
    """A result already says why it landed nothing. A loop that also kept tallies held the fact
    twice and could file a report under the wrong reason."""
    out = prp.summarize(
        [{"report": "alpha", "label": "Alpha", "rows_ingested": 0, "unreachable": True},
         {"report": "bravo", "label": "Bravo", "rows_ingested": 0, "not_requested": True},
         {"report": "chrly", "label": "Chrly", "rows_ingested": 3, "delivered": True}],
        3, "2026-11-01", "2026-11-07")
    assert out["unreachable"] == ["Alpha"]
    assert out["not_requested"] == ["Bravo"]
    assert "Not reached: 1 — Alpha." in out["status"]
    assert "Not asked for: 1 — Bravo." in out["status"]


def test_the_loop_keeps_no_tally_of_its_own():
    src = open(prp.__file__).read()
    loop = src.split("def pull_reports_on_page")[1].split("\ndef ")[0]
    assert "unreachable.append" not in loop
    assert "skipped.append" not in loop
    assert "summarize(results, total, d_from, d_to)" in loop


# ── THE LOOP ITSELF, with no browser ────────────────────────────────────────────────────────────
# A report nobody asked for is never navigated to, so these run the real loop with `page=None`: if
# the skip ever stopped happening, the loop would touch the page and the test would not merely fail,
# it could not run at all. That is the property worth pinning — the skip is BEFORE the page is used.
_LIVE = {"processor": "pos_report", "portal_url": "https://example.invalid/r",
         "portal_reports": [dict(A, pull=False), dict(B, pull=False)]}


def test_the_loop_skips_every_unasked_report_without_touching_the_page():
    out = prp.pull_reports_on_page(None, "org-1", _LIVE, None)
    assert out["rows_ingested"] == 0
    assert out["delivered"] is False
    assert sorted(out["not_requested"]) == ["Alpha", "Bravo"]
    assert out["unreachable"] == []
    assert "Not asked for: 2 — Alpha, Bravo." in out["status"]
    assert "Not reached" not in out["status"]
    assert all(r.get("not_requested") for r in out["reports"])


def test_the_loop_still_runs_a_report_that_is_asked_for(monkeypatch):
    seen = []

    def _fake(client, org_id, src_row, page, mod, proc, spec, tmp, d_from, d_to, days,
              should_stop=None):
        seen.append(spec["key"])
        return {"report": spec["key"], "label": spec["label"], "rows_ingested": 2,
                "delivered": True}

    monkeypatch.setattr(prp, "pull_one_report", _fake)
    src = dict(_LIVE, portal_reports=[dict(A), dict(B, pull=False)])
    out = prp.pull_reports_on_page(None, "org-1", src, None)
    assert seen == ["alpha"]
    assert out["rows_ingested"] == 2
    assert out["not_requested"] == ["Bravo"]
