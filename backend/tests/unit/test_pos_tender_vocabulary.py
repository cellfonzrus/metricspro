"""Unit: THE register's tender vocabulary (index §POS-14).

WHAT IS UNDER TEST. Before this module the answer to "which tenders may a register accept" was
written down twice — a hardcoded array in `pos/sales/page.tsx` and a `CHECK` constraint on
`pos.sale_payments` (mig 725) — and the rule for hiding the integrated card buttons when a tenant
suspends card processing lived only in the browser. Two copies of one fact is the duplicate defect
CLAUDE.md forbids, and it is why adding a tender type touched a stylesheet, a constraint and a
screen with nothing checking that the three agreed.

So this file pins the one home: what the list IS, what a Terms sale needs before it may be rung,
and the one thing a terms tender must never do, which is settle at the till.

EVERY ASSERTION IS TENANT-NEUTRAL. The vocabulary is the PLATFORM's (RULE TWO: no tenant, carrier
or product is named), while which of it an org OFFERS, and on what day counts, is that org's own
declaration — so the tests drive each case from a config dict, and assert that an org which
declared nothing still gets the house answer rather than another tenant's.
"""
import datetime as dt

import pytest

from app.modules.pos import tender_vocabulary as tv


# ── the vocabulary itself ────────────────────────────────────────────────────────────────────────
def test_every_tender_the_old_hardcoded_screen_offered_is_still_offered():
    """The five methods mig 725's CHECK allows must all survive, or this change breaks the till."""
    keys = [t["key"] for t in tv.all_tenders()]
    for pre_existing in ("cash", "credit_card", "debit_card", "check", "card_external"):
        assert pre_existing in keys
    assert "terms" in keys


def test_keys_are_unique_and_labels_are_present():
    keys = [t["key"] for t in tv.all_tenders()]
    assert len(keys) == len(set(keys))
    assert all(t["label"].strip() for t in tv.all_tenders())


def test_only_cash_asks_for_an_amount_tendered():
    asks = [t["key"] for t in tv.all_tenders() if t["needs_amount_tendered"]]
    assert asks == ["cash"]


def test_terms_is_the_only_tender_that_does_not_settle_at_the_till():
    unsettled = [t["key"] for t in tv.all_tenders() if not t["settles_now"]]
    assert unsettled == ["terms"]


def test_the_recon_class_is_dereferenced_from_the_house_tender_vocabulary():
    """Not restated here. A terms sale is money owed on an account, so it reconciles as the house
    vocabulary's `store_acct`; a check has no class on the closing axis and says so honestly."""
    by_key = {t["key"]: t for t in tv.all_tenders()}
    assert by_key["terms"]["recon_class"] == "store_acct"
    assert by_key["cash"]["recon_class"] == "cash"
    assert by_key["credit_card"]["recon_class"] == "credit"
    assert by_key["debit_card"]["recon_class"] == "debit"
    assert by_key["card_external"]["recon_class"] == "ext_cc"
    assert by_key["check"]["recon_class"] is None


def test_no_tender_class_is_invented_that_the_house_vocabulary_does_not_know():
    from app.modules.closing import router as closing_router
    known = set(closing_router.TENDER_CLASSES)
    for t in tv.all_tenders():
        assert t["recon_class"] is None or t["recon_class"] in known


# ── what an org OFFERS (its own declaration, never another tenant's) ─────────────────────────────
def test_an_org_that_declared_nothing_gets_the_house_list_with_integrated_cards():
    keys = [t["key"] for t in tv.offered({})]
    assert keys == ["cash", "credit_card", "debit_card", "check", "terms"]


def test_suspending_card_processing_swaps_the_integrated_buttons_for_the_external_one():
    keys = [t["key"] for t in tv.offered({"suspend_card_processing": True})]
    assert "credit_card" not in keys and "debit_card" not in keys
    assert "card_external" in keys


def test_an_org_may_switch_the_terms_tender_off_without_touching_code():
    keys = [t["key"] for t in tv.offered({"tender_terms_enabled": False})]
    assert "terms" not in keys
    assert "cash" in keys


# ── the day counts ──────────────────────────────────────────────────────────────────────────────
def test_the_house_day_counts_are_the_three_the_owner_asked_for():
    assert tv.terms_day_options({}) == (15, 30, 60)


def test_an_org_declares_its_own_day_counts_and_they_are_sorted_and_de_duplicated():
    assert tv.terms_day_options({"tender_terms_day_options": "30, 7,30 ,90"}) == (7, 30, 90)


def test_a_declaration_that_is_not_day_counts_falls_back_to_the_house_rather_than_to_zero():
    assert tv.terms_day_options({"tender_terms_day_options": "soon, later"}) == (15, 30, 60)
    assert tv.terms_day_options({"tender_terms_day_options": ""}) == (15, 30, 60)


def test_a_day_count_that_cannot_be_a_day_count_is_dropped_not_rounded():
    assert tv.terms_day_options({"tender_terms_day_options": "15,-3,0,30,99999"}) == (15, 30)


def test_a_due_date_is_the_sale_date_plus_the_declared_days():
    assert tv.due_date(dt.date(2026, 10, 30), 30) == dt.date(2026, 11, 29)
    assert tv.due_date("2026-10-30", 15) == dt.date(2026, 11, 14)


# ── the refusals: every one a sentence, written once ─────────────────────────────────────────────
def test_a_settled_tender_needs_nothing_declared_and_is_accepted():
    plan = tv.plan_payment("cash", {}, customer_id=None, terms_days=None,
                           sale_date=dt.date(2026, 10, 30), total=100)
    assert plan["refusal"] is None
    assert plan["terms_days"] is None and plan["due_date"] is None
    assert plan["balance"] == 0


def test_an_unknown_tender_is_refused_by_name():
    plan = tv.plan_payment("bitcoin", {}, customer_id=None, terms_days=None,
                           sale_date=dt.date(2026, 10, 30), total=100)
    assert plan["refusal"] and "bitcoin" in plan["refusal"]


def test_a_tender_the_org_has_not_offered_is_refused_even_though_the_platform_knows_it():
    plan = tv.plan_payment("terms", {"tender_terms_enabled": False}, customer_id="c-1",
                           terms_days=30, sale_date=dt.date(2026, 10, 30), total=100)
    assert plan["refusal"] and "not offered" in plan["refusal"].lower()


def test_a_terms_sale_with_nobody_to_owe_it_is_refused():
    plan = tv.plan_payment("terms", {}, customer_id=None, terms_days=30,
                           sale_date=dt.date(2026, 10, 30), total=100)
    assert plan["refusal"] and "customer" in plan["refusal"].lower()


def test_a_terms_sale_on_a_day_count_the_org_never_declared_is_refused():
    plan = tv.plan_payment("terms", {"tender_terms_day_options": "15,30"}, customer_id="c-1",
                           terms_days=45, sale_date=dt.date(2026, 10, 30), total=100)
    assert plan["refusal"] and "45" in plan["refusal"]


def test_an_accepted_terms_sale_carries_the_whole_total_as_a_balance_and_a_due_date():
    plan = tv.plan_payment("terms", {}, customer_id="c-1", terms_days=30,
                           sale_date=dt.date(2026, 10, 30), total=217.45)
    assert plan["refusal"] is None
    assert plan["terms_days"] == 30
    assert plan["due_date"] == dt.date(2026, 11, 29)
    assert plan["balance"] == pytest.approx(217.45)
    assert plan["settles_now"] is False


def test_the_refusals_are_sentences_a_cashier_can_read():
    for plan in (tv.plan_payment("terms", {}, customer_id=None, terms_days=30,
                                 sale_date=dt.date(2026, 10, 30), total=1),
                 tv.plan_payment("nope", {}, customer_id=None, terms_days=None,
                                 sale_date=dt.date(2026, 10, 30), total=1)):
        assert plan["refusal"][0].isupper() and plan["refusal"].rstrip().endswith((".", "?"))
