"""A CONTROL'S PURPOSE IS READ FROM ITS WORDS, NOT FROM LETTERS INSIDE THEM.

Measured on the owner's own report page, 2026-11-26, after his live-login capture declared the wrong
Run control. `control_purpose` compared its vocabulary with `word in hay` — a plain SUBSTRING test —
over a vocabulary that contains two-letter words (`"go"` under `run`, `"to"` under `date_to`). On a
page whose controls are framework-generated identifiers:

    ProductCategoryDropDown  -> run       (cateGOry)
    GoButton                 -> date_to   (BuTTOn)
    DateToTextBox            -> date      ("date to" kept its space, so only "date" matched)

So the dropdown was declared as the report's Run control and the real Go button was read as the
To-date box — and that is exactly what landed in his stored calibration. It is not his portal's
defect: it hits every portal whose controls are named the way nearly all of them are.

Two halves to the fix, and both are needed. An identifier is a list of words written without the
spaces, so it is SPLIT before it is normalized; and the vocabulary is then matched on WORD
BOUNDARIES. Either alone still gets his page wrong.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.commcalc import portal_report_pull as prp        # noqa: E402


def _by_id(cid):
    return prp.control_purpose({"label": "", "id": cid, "name": cid})


def _by_label(label):
    return prp.control_purpose({"label": label, "id": "", "name": ""})


# ── the regression, control by control, as his page actually names them ──────────────────────────
def test_a_dropdown_is_not_the_run_button():
    assert _by_id("ProductCategoryDropDown") is None


def test_the_run_button_is_the_run_button():
    assert _by_id("GoButton") == "run"


def test_the_to_date_box_is_the_to_date_box():
    assert _by_id("DateToTextBox") == "date_to"
    assert _by_id("DateFromTextBox") == "date_from"


def test_a_control_this_vocabulary_has_no_word_for_is_reported_not_guessed():
    """Unrecognised is the RIGHT answer — the operator sees it and declares it. A closest-looking
    guess is harder to spot than a blank."""
    for cid in ("WorkerDropDown", "CenterNumberDropDown", "TransactionTypeDropDown"):
        assert _by_id(cid) is None, cid


# ── the two halves, each pinned on its own ───────────────────────────────────────────────────────
def test_an_identifier_is_read_as_words():
    assert prp._split_identifier("GoButton") == "Go Button"
    assert prp._split_identifier("DateToTextBox") == "Date To Text Box"
    assert prp._split_identifier("HTMLExportLink") == "HTML Export Link"
    assert prp._split_identifier("ctl00_Go") == "ctl00_Go"      # already separated


def test_splitting_leaves_a_human_label_alone():
    """The column side of §COMMCALC-38 reads the same words it always did."""
    for label in ("Amount before tax", "Date From", "Operator ID", "8.5x11 Color"):
        assert prp._split_identifier(label) == label


def test_a_word_is_said_only_as_a_word():
    assert prp._says("go", "go button") is True
    assert prp._says("go", "product category drop down") is False
    assert prp._says("to", "date to text box") is True
    assert prp._says("to", "go button") is False
    assert prp._says("date to", "date to text box") is True
    assert prp._says("date to", "date from text box") is False


def test_says_is_safe_on_nothing():
    assert prp._says("", "anything") is False
    assert prp._says("go", "") is False
    assert prp._says(None, "go") is False


# ── the properties the fix must not break ────────────────────────────────────────────────────────
def test_labels_still_resolve():
    assert _by_label("Date From") == "date_from"
    assert _by_label("Date To") == "date_to"
    assert _by_label("Go") == "run"
    assert _by_label("Export") == "export"
    assert _by_label("Run report") == "run"
    assert _by_label("Show detail") == "detail"


def test_the_longest_phrase_still_wins():
    """"from date" must beat "date", or every date box is the generic one."""
    assert _by_label("From Date") == "date_from"
    assert _by_label("Business Date") == "date"
    assert prp.control_purpose({"label": "Run Report", "id": "", "name": ""}) == "run"


def test_a_longer_word_of_one_purpose_beats_a_shorter_word_of_another():
    """The ordering has to be by LENGTH, and this is the case that proves it rather than a tie that
    any ordering would get right: "Export to CSV" says both "export" and the date word "to", and
    only the longest-first rule stops a download button being declared as a date box."""
    assert _by_label("Export to CSV") == "export"
    assert _by_label("Download to Excel") == "export"


def test_a_tenants_own_word_still_outranks_nothing_and_is_matched_the_same_way():
    """A declared synonym goes through `_says` too — a tenant whose button says "Vis" must not match
    "Vis" inside "Division"."""
    assert prp.control_purpose({"label": "Afficher", "id": "", "name": ""},
                               words={"run": ("afficher",)}) == "run"
    assert prp.control_purpose({"label": "Division", "id": "", "name": ""},
                               words={"run": ("vis",)}) is None


def test_an_empty_control_is_none():
    assert prp.control_purpose({}) is None
    assert prp.control_purpose(None) is None
    assert prp.control_purpose({"label": "   ", "id": "", "name": ""}) is None


def test_the_haystack_is_built_in_one_place():
    """`control_purpose` and anything else asking "what is this control called" read the same
    words — a second assembly is how the two sides come to disagree about a control."""
    c = {"label": "Date From", "id": "DateFromTextBox", "name": "ctl00$DateFrom"}
    hay = prp.control_haystack(c)
    assert "date from" in hay
    assert "text box" in hay


def test_the_normalizer_is_still_the_shared_one():
    """§COMMCALC-38: a label compared here and a header compared there cannot drift apart."""
    from app.modules.commcalc import report_columns as rc

    src = open(prp.__file__).read()
    body = src[src.index("def _norm_words("):]
    body = body[:body.index("\ndef ")]
    assert "report_columns import norm" in body
    assert prp._norm_words("Date  From!") == rc.norm("Date  From!")
