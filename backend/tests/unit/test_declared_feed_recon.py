"""Unit: does what a rep DECLARED at closing agree with what the sales feed CAPTURED.

Owner ask 2026-10-29 (Sanjot, verbatim): *"Add a field in as no of edge for total and no of
financicing in thr daily closing for all employees to declare along with the $amount of edge and
acima as they get paid for each transcoding they do, this would then be checked with our system if
our system is capturing the sales as declared by the sales rep"*.

THE CLASS THESE TESTS PIN. The declaration half already existed as config (mig 1072: a category
says it is measured in BOTH units and dollars and is declared per EMPLOYEE, and the closing form
renders it). What had no home was the LINK: a declared category could not say what in the sales
feed answers the same question, so the only declared-vs-captured check in the platform was
hard-wired to two figures (activations, upgrades). These tests are written against the mechanism,
not against Edge — Edge and Acima reach it as config rows.

WHAT THEY REFUSE TO LET HAPPEN, in order of how badly it would mislead a manager:
  * a day whose feed was never loaded reported as a $0.00 variance (reads as "we checked and it
    agreed" — the `recon_drilldown` pending doctrine, §CORE-17)
  * a vendor whose detection is not configured reported as "the rep declared sales we did not
    capture" (the fault is ours, and the row must say so)
  * one human reported as two because the punch spells them "Singh, Simarjyot" and the feed spells
    them "Simarjyot Singh" (§CORE-08 — the key is dereferenced, never re-folded here)
  * a category with no counterpart silently reconciled against zero
"""
import pytest

from app.modules.closing import declared_feed_recon as rec

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"
DAY = "2026-10-28"


# ── the catalog, as `metric_catalog.build` hands it over ──────────────────────────────────────────

def _metric(key, label, counterpart, *, value_kind="both", declared_by="employee"):
    return {"key": key, "label": label, "value_kind": value_kind, "declared_by": declared_by,
            "feed_counterpart": counterpart, "unit_price": 0.0, "in_revenue": True,
            "recon_class": "other", "source": "count_field", "storage": "metric_line"}


EDGE = _metric("edge_count", "Edge", "financing:edge")
ACIMA = _metric("acima_count", "Acima", "financing:acima")
MAILBOX = _metric("mailbox_count", "Mailbox rentals", None, value_kind="count")
CATALOG = [EDGE, ACIMA, MAILBOX]


def _declared(rep, metric_key, units, amount, *, day=DAY, store="B-1115"):
    return {"date": day, "store_code": store, "rep": rep, "metric_key": metric_key,
            "units": units, "amount": amount}


def _captured(rep, counterpart, units, amount, *, day=DAY, store="B-1115"):
    return {"date": day, "store_code": store, "rep": rep, "counterpart": counterpart,
            "units": units, "amount": amount}


# ── §A  the counterpart vocabulary: a category NAMES what should agree with it ────────────────────

def test_a_counterpart_names_its_kind_and_its_reference():
    assert rec.parse_counterpart("financing:edge") == {"kind": "financing", "ref": "edge"}
    assert rec.parse_counterpart("recon_class:activation") == {"kind": "recon_class",
                                                               "ref": "activation"}


def test_no_counterpart_is_not_an_error_and_is_not_zero():
    # A category the business tracks but the feed cannot answer (mailbox rentals) must come back
    # unparsed, so the reconciler leaves it out rather than comparing it against nothing.
    for blank in (None, "", "   ", "financing:", ":edge"):
        assert rec.parse_counterpart(blank) is None


def test_an_unknown_counterpart_kind_is_refused_rather_than_guessed():
    assert rec.parse_counterpart("sku:12345") is None
    assert rec.parse_counterpart("edge") is None        # a bare word names no source


def test_the_editor_is_offered_the_tenants_own_vendors_never_a_typed_list():
    opts = rec.counterpart_options(financing_vendors=[
        {"vendor_key": "edge", "label": "Edge financing"},
        {"vendor_key": "acima", "label": "ACIMA"},
    ])
    vals = [o["value"] for o in opts]
    assert "financing:edge" in vals and "financing:acima" in vals
    assert "recon_class:activation" in vals and "recon_class:upgrade" in vals
    # The label a tenant typed for its vendor is the label the admin sees.
    assert next(o["label"] for o in opts if o["value"] == "financing:acima").startswith("ACIMA")


def test_only_the_metrics_that_name_a_counterpart_are_reconciled():
    assert [m["key"] for m in rec.reconciled_metrics(CATALOG)] == ["edge_count", "acima_count"]


# ── §B  the comparison itself ─────────────────────────────────────────────────────────────────────

def test_declared_and_captured_agreeing_reads_ok_with_no_variance():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Simarjyot Singh", "edge_count", 3, 1200.0)],
                        captured=[_captured("Singh, Simarjyot", "financing:edge", 3, 1200.0)],
                        feed_days={DAY})
    row = next(r for r in out["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "ok"
    assert row["units_variance"] == 0 and row["amount_variance"] == 0.0
    assert out["totals"]["ok"] == 1 and out["totals"]["gaps"] == 0


def test_one_human_spelled_two_ways_is_one_row():
    # §CORE-08. The punch/roster spelling and the feed spelling are the SAME person; a union over
    # strings reported two reps on B-1750 on 2026-10-10, which is the defect this pins.
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Singh, Simarjyot", "edge_count", 2, 800.0)],
                        captured=[_captured("Simarjyot Singh", "financing:edge", 2, 800.0)],
                        feed_days={DAY})
    assert len([r for r in out["rows"] if r["metric_key"] == "edge_count"]) == 1


def test_the_rep_declared_more_than_the_system_captured():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Tanish Arora", "edge_count", 4, 1600.0)],
                        captured=[_captured("Tanish Arora", "financing:edge", 2, 800.0)],
                        feed_days={DAY})
    row = next(r for r in out["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "variance"
    assert row["units_declared"] == 4 and row["units_captured"] == 2
    assert row["units_variance"] == 2 and row["amount_variance"] == 800.0
    assert row["severity"] == "flagged"              # informational, never a close block
    assert out["totals"]["gaps"] == 1


def test_declared_with_nothing_captured_is_named_for_what_it_is():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Tanish Arora", "edge_count", 2, 800.0)],
                        captured=[],
                        feed_days={DAY})
    row = next(r for r in out["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "not_captured"
    assert row["units_captured"] == 0


def test_captured_with_nothing_declared_is_also_a_gap():
    # The rep is paid per transaction, so a financing the system captured and the rep never declared
    # is as much a gap as the reverse — and it is a DIFFERENT accusation, so it gets its own verdict.
    out = rec.reconcile(CATALOG, declared=[],
                        captured=[_captured("Tanish Arora", "financing:edge", 1, 400.0)],
                        feed_days={DAY})
    row = next(r for r in out["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "not_declared"
    assert row["units_declared"] == 0 and row["units_captured"] == 1


def test_a_metric_with_no_counterpart_never_appears():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Tanish Arora", "mailbox_count", 2, 0.0)],
                        captured=[], feed_days={DAY})
    assert [r["metric_key"] for r in out["rows"]] == []
    assert "mailbox_count" in out["not_reconciled"]


def test_a_tolerance_the_tenant_set_is_honoured_on_both_axes():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Tanish Arora", "edge_count", 3, 1200.0)],
                        captured=[_captured("Tanish Arora", "financing:edge", 3, 1199.50)],
                        feed_days={DAY}, amount_tolerance=1.0)
    assert next(r for r in out["rows"] if r["metric_key"] == "edge_count")["verdict"] == "ok"


# ── §C  an absence needs the RIGHT reason, never a clean zero ─────────────────────────────────────

def test_a_day_whose_feed_was_never_loaded_is_pending_not_a_zero_variance():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Tanish Arora", "edge_count", 2, 800.0)],
                        captured=[], feed_days=set())
    row = next(r for r in out["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "pending"
    assert row["units_variance"] is None and row["amount_variance"] is None
    assert "feed" in (row["reason"] or "").lower()
    assert out["totals"]["pending"] == 1 and out["totals"]["gaps"] == 0


def test_a_vendor_whose_detection_is_not_configured_blames_us_not_the_rep():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Tanish Arora", "edge_count", 2, 800.0)],
                        captured=[], feed_days={DAY},
                        counterpart_status={"financing:edge": {"status": "not_configured",
                                                               "note": "no tender mapped"}})
    row = next(r for r in out["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "pending"
    assert row["units_variance"] is None
    assert "no tender mapped" in (row["reason"] or "")


def test_a_pending_row_still_shows_what_the_rep_declared():
    # The declaration is a fact even when the counterpart is missing; hiding it would lose the
    # only number anybody has.
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Tanish Arora", "edge_count", 2, 800.0)],
                        captured=[], feed_days=set())
    row = next(r for r in out["rows"] if r["metric_key"] == "edge_count")
    assert row["units_declared"] == 2 and row["amount_declared"] == 800.0


# ── §D  the address of an accusation ──────────────────────────────────────────────────────────────

def test_a_row_is_addressed_by_day_store_rep_and_metric():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("A B", "edge_count", 1, 400.0, store="B-1115"),
                                  _declared("A B", "edge_count", 1, 400.0, store="B-1598")],
                        captured=[], feed_days={DAY})
    assert {r["store_code"] for r in out["rows"]} == {"B-1115", "B-1598"}
    for r in out["rows"]:
        assert set(rec.ROW_KEYS) <= set(r)


def test_two_days_do_not_fold_into_one():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("A B", "edge_count", 1, 400.0, day="2026-10-27"),
                                  _declared("A B", "edge_count", 1, 400.0, day="2026-10-28")],
                        captured=[], feed_days={"2026-10-27", "2026-10-28"})
    assert sorted(r["date"] for r in out["rows"]) == ["2026-10-27", "2026-10-28"]


def test_the_metrics_own_label_travels_with_the_row():
    out = rec.reconcile(CATALOG, declared=[_declared("A B", "edge_count", 1, 400.0)],
                        captured=[], feed_days={DAY})
    assert next(r for r in out["rows"])["label"] == "Edge"


# ── §E  per-rep rollup, which is what gets paid on ────────────────────────────────────────────────

def test_a_rep_rollup_sums_their_gaps_and_keeps_their_roster_spelling():
    out = rec.reconcile(CATALOG,
                        declared=[_declared("Singh, Simarjyot", "edge_count", 4, 1600.0),
                                  _declared("Singh, Simarjyot", "acima_count", 1, 300.0)],
                        captured=[_captured("Simarjyot Singh", "financing:edge", 2, 800.0),
                                  _captured("Simarjyot Singh", "financing:acima", 1, 300.0)],
                        feed_days={DAY})
    by_rep = {r["rep"]: r for r in out["by_rep"]}
    assert len(by_rep) == 1
    only = next(iter(by_rep.values()))
    assert only["gaps"] == 1 and only["ok"] == 1
    assert only["units_declared"] == 5 and only["units_captured"] == 3


# ── §F  the captured side is folded to the grain the category is DECLARED at ──────────────────────
# Found by the integration test on 2026-10-29, and it is the kind of defect that reads as a real
# accusation: a store-declared category is one figure for the whole store-day, while the sales feed
# always knows a salesperson. Folding the feed per rep against a store declaration produced TWO
# rows — the store's "nothing captured" and the rep's "nobody declared it" — accusing a store of
# failing to declare a sale it had declared. The grain is the CATEGORY's, so the rule lives here.

STORE_EDGE = _metric("store_edge", "Edge (store)", "financing:edge", declared_by="store")


def test_a_store_declared_category_folds_every_reps_capture_onto_the_store():
    out = rec.reconcile([STORE_EDGE],
                        declared=[_declared("", "store_edge", 2, 800.0)],
                        captured=[_captured("Tanish Arora", "financing:edge", 1, 400.0),
                                  _captured("Simarjyot Singh", "financing:edge", 1, 400.0)],
                        feed_days={DAY})
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["rep"] == ""
    assert row["units_captured"] == 2 and row["amount_captured"] == 800.0
    assert row["verdict"] == "ok"


def test_an_employee_declared_category_still_keeps_the_rep_grain():
    out = rec.reconcile([EDGE],
                        declared=[_declared("Tanish Arora", "edge_count", 1, 400.0)],
                        captured=[_captured("Tanish Arora", "financing:edge", 1, 400.0),
                                  _captured("Simarjyot Singh", "financing:edge", 1, 400.0)],
                        feed_days={DAY})
    assert len(out["rows"]) == 2
    assert {r["verdict"] for r in out["rows"]} == {"ok", "not_declared"}
