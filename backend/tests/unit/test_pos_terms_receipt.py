"""Unit: what a receipt says when the sale was not paid (index §POS-14).

A receipt is the document the customer walks out with. `slip_model` printed one line for the money
— `Paid (Credit Card)` with the amount — because until now every tender settled at the till. A
Terms sale does not, so that line would read **Paid (Terms) $108.88** on an invoice nobody has
paid: not a cosmetic slip but a false statement on a tax document, and the first thing a customer
would quote back.

`settles_now` already answers this in the tender vocabulary (§POS-14), so the renderer DEREFERENCES
it rather than testing for a tender word of its own.
"""
from app.modules.pos import sale_receipt as sr

SALE = {"id": "s-1", "transaction_id": 4605, "status": "completed", "created_at": "2026-10-30"}
ITEM = {"description": "Case", "qty": 1, "price": 100.0, "line_total": 108.88}


def _snap(**over):
    snap = {"transactionId": 4605, "date": "2026-10-30", "customerName": "A Customer",
            "items": [ITEM], "subtotal": 100.0, "taxTotal": 8.88, "total": 108.88,
            "method": "terms", "tendered": None, "change": 0,
            "termsDays": 30, "dueDate": "2026-11-29"}
    snap.update(over)
    return snap


def _model(**over):
    return sr.slip_model(sr.snapshot_view(_snap(**over), SALE), sr.template_view({}))


def test_the_snapshot_carries_the_terms_facts_through_to_the_renderer():
    snap = sr.snapshot_view(_snap(), SALE)
    assert snap["terms_days"] == 30
    assert snap["due_date"] == "2026-11-29"


def test_a_sale_that_settled_at_the_till_carries_no_terms_facts():
    snap = sr.snapshot_view(_snap(method="cash", tendered=120, change=11.12,
                                  termsDays=None, dueDate=None), SALE)
    assert snap["terms_days"] is None and snap["due_date"] == ""


def test_an_unpaid_sale_does_not_print_the_word_paid():
    labels = " ".join(p["label"] for p in _model()["paid"])
    assert "Paid" not in labels


def test_an_unpaid_sale_prints_what_is_owed_and_when_it_falls_due():
    paid = _model()["paid"]
    assert any(p["value"] == 108.88 for p in paid)
    assert any("2026-11-29" in p["label"] for p in paid)
    assert any("Balance due" in p["label"] for p in paid)


def test_a_cash_sale_still_prints_exactly_what_it_printed_before():
    paid = _model(method="cash", tendered=120.0, change=11.12, termsDays=None, dueDate=None)["paid"]
    assert paid[0]["label"] == "Paid (Cash)" and paid[0]["value"] == 120.0
    assert paid[1]["label"] == "Change" and paid[1]["value"] == 11.12


def test_a_terms_sale_whose_due_date_is_missing_says_so_rather_than_printing_paid():
    """An older row, or a feed-rebuilt receipt, may carry the method and no due date. The line must
    still not claim payment — it says the balance is due, without inventing a date."""
    paid = _model(dueDate="")["paid"]
    assert "Paid" not in " ".join(p["label"] for p in paid)
    assert any("Balance due" in p["label"] for p in paid)


def test_the_tenants_own_pos_layout_labels_it_a_balance_too():
    """The same question, asked of the same home. Two layouts wording one fact differently is the
    defect §POS-13 already fixed for the method label, so this asserts the real format module."""
    from app.modules.pos.receipt_formats import rq
    doc = sr.document(sr.snapshot_view(_snap(), SALE), rq, sr.template_view({}))
    assert doc["payments"][0]["label"] == "Balance due 2026-11-29"
    assert doc["payments"][0]["amount"] == 108.88


def test_that_layout_is_unchanged_for_a_sale_that_was_paid():
    from app.modules.pos.receipt_formats import rq
    doc = sr.document(sr.snapshot_view(_snap(method="cash", tendered=120.0, change=11.12,
                                             termsDays=None, dueDate=None), SALE),
                      rq, sr.template_view({}))
    assert doc["payments"][0]["label"] == "Cash" and doc["payments"][0]["amount"] == 120.0
