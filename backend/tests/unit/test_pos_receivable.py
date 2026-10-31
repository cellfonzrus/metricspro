"""Unit: what a customer still owes us on a sale, and how late it is (§POS-19).

TDD for the owner's 2026-10-31 *"build them"*. These exercise the pure home directly; the wiring —
which sales the report asks about, which org's settings answer, which store a caller may see — is
`tests/integration/test_pos_receivable_endpoint.py`.

The properties worth stating once, because each is a defect somebody would otherwise ship:

  · a sale with no deferred leg is NOT a receivable of $0.00; it is not a receivable;
  · not yet due is owed and is not late;
  · an age that cannot be measured is never the newest band;
  · a terms leg is not money the till has — the regression for the defect building this found.
"""
import datetime as dt

import pytest

from app.modules.commcalc import manager_followup as age
from app.modules.pos import receivable as R
from app.modules.pos import sale_settlement as S

TODAY = dt.date(2026, 10, 31)


def sale(**kw):
    return {"id": "s1", "total": 100.0, "balance": 0.0, "status": "completed", **kw}


def terms(amount=100.0, days=30, due="2026-11-30"):
    return {"payment_method": "terms", "amount": amount, "terms_days": days, "due_date": due}


def cash(amount=100.0):
    return {"payment_method": "cash", "amount": amount}


# ── a sale with no deferred leg is not a receivable ──────────────────────────────────────────────
def test_a_sale_paid_at_the_till_is_not_a_receivable():
    a = R.assess(sale(), [cash()], as_of=TODAY)
    assert a["receivable"] is False
    assert a["state"] == R.NOT_RECEIVABLE
    assert a["outstanding"] == 0.0
    assert a["reason"] == R.NOT_RECEIVABLE_REASONS["no_deferred_leg"]


def test_a_sale_with_no_payment_row_is_not_a_debt():
    """4,603 of the 4,605 live sales are imported receipt history with no payment row. Reading that
    absence as "owes the whole total, due today" would be a collections queue over the ledger."""
    a = R.assess(sale(), [], as_of=TODAY)
    assert a["receivable"] is False
    assert a["reason"] == R.NOT_RECEIVABLE_REASONS["tender_unrecorded"]
    assert a["outstanding"] == 0.0


def test_a_part_tendered_cash_sale_carrying_a_balance_is_not_a_terms_debt():
    """REGRESSION for the filter this report deliberately does not use. `balance > 0` would have
    admitted a cash sale somebody part-paid at the till, which nobody agreed terms on."""
    a = R.assess(sale(balance=60.0), [cash(40.0)], as_of=TODAY)
    assert a["receivable"] is False


def test_the_rollup_adds_nothing_for_sales_that_are_not_receivables():
    rows = [R.assess(sale(), [cash()], as_of=TODAY), R.assess(sale(), [], as_of=TODAY)]
    s = R.summarise(rows)
    assert s["open_total"] == 0.0
    assert s["receivables"] == 0
    assert s["not_receivable"] == 2          # counted honestly, never dropped


# ── not yet due is owed, and is not late ─────────────────────────────────────────────────────────
def test_a_sale_inside_its_terms_is_owed_but_not_overdue():
    a = R.assess(sale(balance=100.0), [terms(due="2026-11-30")], as_of=TODAY)
    assert a["receivable"] is True
    assert a["state"] == R.OPEN_NOT_DUE
    assert a["band"] is None                 # it has no age, so it has no band
    s = R.summarise([a])
    assert (s["open_total"], s["not_due_total"], s["past_due_total"]) == (100.0, 100.0, 0.0)
    assert s["past_due_count"] == 0


def test_due_today_is_not_yet_late():
    """A customer has the day they were given."""
    a = R.assess(sale(balance=100.0), [terms(due=TODAY.isoformat())], as_of=TODAY)
    assert a["state"] == R.OPEN_NOT_DUE
    assert a["days_past_due"] == 0


@pytest.mark.parametrize("due,days,band", [
    ("2026-10-30", 1, "0-7"),
    ("2026-10-01", 30, "8-30"),
    ("2026-09-01", 60, "31-60"),
    ("2026-06-01", 152, "90+"),
])
def test_how_late_and_which_band(due, days, band):
    a = R.assess(sale(balance=100.0), [terms(due=due)], as_of=TODAY)
    assert a["state"] == R.OPEN_PAST_DUE
    assert a["days_past_due"] == days
    assert a["band"] == band == age.age_band(days)   # the house ladder's answer, not a second one


def test_a_cleared_balance_is_not_chased():
    a = R.assess(sale(balance=0.0), [terms()], as_of=TODAY)
    assert a["state"] == R.CLEARED
    assert a["receivable"] is False


def test_a_voided_terms_sale_is_shown_but_never_owed():
    a = R.assess(sale(balance=100.0, status="voided"), [terms()], as_of=TODAY)
    assert a["state"] == R.VOIDED
    assert a["receivable"] is False
    assert R.summarise([a])["open_total"] == 0.0


# ── an age that cannot be measured ───────────────────────────────────────────────────────────────
def test_a_deferred_leg_with_no_due_date_is_unknown_not_fresh():
    a = R.assess(sale(balance=100.0), [{"payment_method": "terms", "amount": 100.0}], as_of=TODAY)
    assert a["receivable"] is True
    assert a["band"] == R.BAND_UNKNOWN == age.BAND_UNKNOWN
    assert a["days_past_due"] is None        # never 0, which would read as "due today"
    assert "cannot be measured" in a["reason"]
    assert R.summarise([a])["bands"][R.BAND_UNKNOWN] == 100.0


def test_the_earliest_due_date_wins_so_one_late_leg_is_not_hidden():
    assert R.due_date([terms(due="2026-12-01"), terms(due="2026-11-01")]) == dt.date(2026, 11, 1)


def test_a_settling_legs_date_is_never_read_as_a_term():
    assert R.due_date([dict(cash(), due_date="2026-11-01")]) is None
    assert R.terms_days([dict(cash(), due_date="2026-11-01", terms_days=30)]) is None


# ── the sibling §POS-17 shares ───────────────────────────────────────────────────────────────────
def test_a_terms_leg_is_not_money_the_till_has():
    """REGRESSION. §POS-15 and §POS-17 landed the same evening in different branches, and
    `tendered` summed every leg: a $100 sale wholly on terms stores `balance = 100`, so `expected`
    was $0.00 against a $100 tender — a $100 OVER-tender telling a manager to refund a customer who
    has not paid. Found before a single terms sale existed."""
    t = S.tendered([terms()])
    assert t["present"] is True               # the row exists; this is not the absence case
    assert t["total"] == 0.0                  # ...and nothing settled
    assert t["deferred_total"] == 100.0       # ...carried beside it, not dropped
    out = S.outstanding(sale(balance=100.0), [terms()])
    assert out["measured"] is True
    assert out["variance"] == 0.0
    assert out["mismatch"] is False
    assert out["direction"] == S.SETTLED


def test_a_shortfall_on_the_till_half_of_a_terms_sale_is_still_found():
    out = S.outstanding(sale(balance=60.0), [cash(30.0), terms(40.0)])
    assert out["mismatch"] is True
    assert out["variance"] == -10.0
    assert out["attribution"]["method"] == "cash"    # never the leg nobody has paid


def test_an_unknown_method_is_money_taken():
    """Imported history predates the tender vocabulary, and reading an unknown word as "not yet
    paid" would turn settled history into a ledger of debts."""
    assert S.tendered([{"payment_method": "zz_no_vocabulary", "amount": 50.0}])["total"] == 50.0


# ── the rollup, and the sentence ─────────────────────────────────────────────────────────────────
def test_the_oldest_age_reported_is_the_largest_not_the_last():
    rows = [R.assess(sale(balance=1.0), [terms(due=d)], as_of=TODAY)
            for d in ("2026-10-01", "2026-09-01", "2026-10-20")]
    assert R.summarise(rows)["oldest_days_past_due"] == 60


def test_every_house_band_is_a_key_so_an_empty_band_is_a_zero():
    assert set(R.BAND_LABELS) <= set(R.summarise([])["bands"])
    assert R.BANDS is age.AGE_BANDS            # dereferenced by identity, never respelled


def test_the_sentence_claims_only_what_the_row_carries():
    late = R.assess(sale(balance=100.0), [terms(due="2026-10-01")], as_of=TODAY)
    assert R.sentence(late) == "$100.00 is owed on this sale and is 30 days past due."
    one = R.assess(sale(balance=5.0), [terms(due="2026-10-30")], as_of=TODAY)
    assert R.sentence(one).endswith("is 1 day past due.")        # not "1 days"
    nodue = R.assess(sale(balance=9.0), [{"payment_method": "terms", "amount": 9.0}], as_of=TODAY)
    assert "cannot be measured" in R.sentence(nodue)
    assert "$" not in R.sentence(R.assess(sale(), [cash()], as_of=TODAY))


def test_the_module_reads_no_clock_so_a_test_and_a_report_see_one_day():
    a = R.assess(sale(balance=5.0), [terms(due="2026-10-01")], as_of=dt.date(2026, 10, 2))
    assert a["days_past_due"] == 1
