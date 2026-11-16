"""UNIT: the Weekly Review's drill-down declaration — pure, DB-free.

A composed report is where provenance goes wrong: it quotes figures other reports produced, and the
temptation is to name a table for all of them. These tests pin the opposite posture — a panel only
where the relation behind EVERY row is certain, and a refusal in words, naming the report that does
answer, everywhere else.
"""
from app.modules.commcalc import weekly_review as wr
from app.modules.commcalc import weekly_review_drilldown as dd

_WEEK = {"date_from": "2026-11-09", "date_to": "2026-11-15", "week_ending": "2026-11-15",
         "label": "9–15 November 2026"}
_SCOPE = {"kind": "company", "label": "Company", "store_codes": ["A-1", "A-2"], "note": ""}


def _review(facts):
    return wr.review(_SCOPE, facts=facts, week=_WEEK)


def test_the_rep_payouts_open_and_name_the_relation_they_were_read_from():
    review = _review({"commission": [
        {"rep": "Khan, Abid", "store_code": "A-1", "total_payout": 1200.0},
        {"rep": "Perez, Dalia", "store_code": "A-2", "total_payout": 800.0}]})
    panels, _refused = dd.review_drilldowns(review)
    panel = panels["commission"]
    assert panel["figure"]["amount"] == 2000.0
    assert [r["label"] for r in panel["rows"]] == ["Khan, Abid · A-1", "Perez, Dalia · A-2"]
    assert panel["rows"][0]["sources"] == ["rep_commissions"]
    assert panel["tie_out"]["ties"] is True, "the rows must add up to the figure they explain"


def test_a_store_with_no_accessory_target_is_shown_without_a_figure():
    review = _review({"accessory": [{"store_code": "A-1", "target": 12000.0, "achieved": 9000.0},
                                    {"store_code": "A-2", "target": None, "achieved": 4000.0}]})
    panels, _refused = dd.review_drilldowns(review)
    panel = panels["accessory"]
    assert panel["figure"]["amount"] == 12000.0, "the panel opens the TARGET, not the gap (a subtraction)"
    rows = {r["label"]: r for r in panel["rows"]}
    assert rows["A-2"]["amount"] is None and rows["A-2"]["measured"] is False
    assert rows["A-2"]["reason"], "a store with no target says why it carries no figure"


def test_an_item_nobody_measured_is_offered_no_panel_but_says_why():
    panels, refused = dd.review_drilldowns(_review({}))
    assert "commission" not in panels, "an empty panel would assert there were no rows"
    assert refused["commission"]["reason"], "and it says it was not measured, not that it never drills"


def test_every_refusal_names_the_report_that_does_answer():
    panels, refused = dd.review_drilldowns(_review({}))
    for key in ("profit", "payroll", "aging", "growth", "rep_watch"):
        assert key in refused, f"{key} must say why it has no panel"
        assert refused[key]["reason"]
        assert str(refused[key]["report"] or "").startswith("/"), \
            f"{key}'s refusal must point at the report that carries the rows"


def test_every_declared_item_either_drills_or_states_a_refusal():
    review = _review({})
    panels, refused = dd.review_drilldowns(review)
    for key in wr.ITEM_KEYS:
        assert key in panels or key in refused, f"{key} says nothing about its own provenance"


def test_the_panelled_items_name_relations_the_screen_can_say_in_words():
    """`report_drilldown.origin`'s rule: a table named on a panel is rendered through
    `sourceLabels.ts`, so a relation with no entry there would put a database name in front of a
    manager. Read the file rather than trusting the list."""
    import os
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    root = os.path.dirname(backend)
    labels = open(os.path.join(root, "frontend/src/lib/sourceLabels.ts"), encoding="utf-8").read()
    review = _review({"commission": [{"rep": "A", "store_code": "A-1", "total_payout": 1.0}],
                      "accessory": [{"store_code": "A-1", "target": 1.0, "achieved": 1.0}]})
    panels, _ = dd.review_drilldowns(review)
    named = {s for p in panels.values() for r in p["rows"] for s in r["sources"]}
    assert named, "the panels must name the relations they read"
    for table in named:
        assert f"{table}:" in labels, f"{table} has no words in sourceLabels.ts"
