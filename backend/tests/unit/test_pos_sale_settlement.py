"""UNIT: what tender stands against a sale, and what moving its total leaves outstanding (§POS-14).

Owner 2026-10-30: *"i need the pos POS — Cash Register to have the ability to edit the sale from the
prior day and create log of the cash or credit mismatch"*.

THE CLASS THESE TESTS PIN. Editing a rung sale restates its totals (§POS-12) and `pos.void_sale`
stands the sale down (mig 725) — and NEITHER touches `pos.sale_payments`. So after either write the
money taken no longer matches the money owed, and nothing recorded the difference. The class is not
"the edit screen needs a warning": it is that **a sale's tender was never reconciled against the
sale's own figure**, by any caller, on any path.

The three facts that make the answer honest, each pinned here so a later change cannot drop one:

  · an UNRECORDED tender is not a $0.00 tender. 4,603 of the 4,605 live `pos.sales` rows are
    imported history with no payment row at all (measured 2026-10-30). A build that read absence as
    zero would declare every one of them a mismatch.
  · a sale may legitimately be part-tendered, so what the tender is compared against is
    `total − balance`, never the total alone.
  · when a sale was tendered on MORE THAN ONE method, which method is short is not knowable from the
    rows — it is a decision a human makes. The module says so rather than attributing it.
"""
import pytest

from app.modules.pos import sale_settlement as S

ORG = "00000000-0000-0000-0000-000000000001"


def sale(total=210.13, balance=0.0, **kw):
    row = {"id": "s-1", "org_id": ORG, "transaction_id": 4605, "store_code": "Ave U",
           "status": "completed", "total": total, "balance": balance}
    row.update(kw)
    return row


def pay(method, amount):
    return {"payment_method": method, "amount": amount}


# ── A. the tender as recorded ────────────────────────────────────────────────────────────────────
def test_tender_sums_and_splits_by_method():
    t = S.tendered([pay("cash", 100.00), pay("credit_card", 90.53), pay("cash", 10.00)])
    assert t["present"] is True
    assert t["total"] == 200.53
    assert t["by_method"] == {"cash": 110.00, "credit_card": 90.53}
    assert t["methods"] == ["cash", "credit_card"]


def test_no_payment_row_is_unrecorded_not_zero():
    """The 4,603-row finding. Absence must not render as money."""
    t = S.tendered([])
    assert t["present"] is False
    assert t["total"] is None
    assert t["by_method"] == {}


# ── B. what the tender is compared against ───────────────────────────────────────────────────────
def test_expected_is_the_total_net_of_any_balance_carried():
    assert S.expected(sale(total=210.13, balance=0))["amount"] == 210.13
    assert S.expected(sale(total=210.13, balance=50.00))["amount"] == 160.13


def test_a_voided_sale_expects_no_tender_at_all():
    """`pos.void_sale` puts the stock back and leaves the payment rows standing — so the whole
    tender is outstanding as a refund. The sibling path, covered by the same one home."""
    e = S.expected(sale(status="voided"))
    assert e["amount"] == 0.0
    assert e["voided"] is True


def test_a_sale_with_no_total_is_not_measurable():
    assert S.expected(sale(total=None))["present"] is False


# ── C. the comparison ────────────────────────────────────────────────────────────────────────────
def test_an_edit_that_raises_the_total_leaves_cash_to_collect():
    out = S.outstanding(sale(total=210.13), [pay("cash", 190.53)])
    assert out["measured"] is True
    assert out["variance"] == -19.60          # negative is short, as everywhere else
    assert out["verdict"] == "short"
    assert out["direction"] == S.COLLECT
    assert out["attribution"]["method"] == "cash"
    assert out["attribution"]["ambiguous"] is False


def test_an_edit_that_lowers_the_total_leaves_credit_to_refund():
    out = S.outstanding(sale(total=150.00), [pay("credit_card", 190.53)])
    assert out["variance"] == 40.53
    assert out["verdict"] == "over"
    assert out["direction"] == S.REFUND
    assert out["attribution"]["method"] == "credit_card"


def test_a_sale_that_still_adds_up_is_settled_and_logs_nothing():
    out = S.outstanding(sale(total=190.53), [pay("cash", 190.53)])
    assert out["measured"] is True and out["variance"] == 0.0
    assert out["verdict"] == "ok" and out["direction"] == S.SETTLED
    assert out["mismatch"] is False


def test_a_voided_sale_owes_its_whole_tender_back():
    out = S.outstanding(sale(total=190.53, status="voided"), [pay("cash", 190.53)])
    assert out["variance"] == 190.53
    assert out["direction"] == S.REFUND
    assert out["mismatch"] is True


def test_an_unrecorded_tender_refuses_rather_than_reporting_a_shortage():
    out = S.outstanding(sale(total=210.13), [])
    assert out["measured"] is False
    assert out["variance"] is None
    assert out["verdict"] is None
    assert out["reason"] == "tender_unrecorded"
    assert out["reason_label"] in S.UNMEASURED_REASONS.values()
    assert out["mismatch"] is False           # not measurable is not a finding


def test_two_tender_methods_name_both_and_attribute_neither():
    out = S.outstanding(sale(total=250.00), [pay("cash", 100.00), pay("credit_card", 90.53)])
    assert out["variance"] == -59.47
    assert out["attribution"]["ambiguous"] is True
    assert out["attribution"]["method"] is None
    assert out["attribution"]["methods"] == ["cash", "credit_card"]


# ── D. the band is declared, never a literal beside a caller (RULE TWO) ──────────────────────────
def test_the_house_band_is_zero_because_tender_is_arithmetic_not_two_counts():
    assert S.HOUSE_BAND == 0.0
    out = S.outstanding(sale(total=190.54), [pay("cash", 190.53)])
    assert out["mismatch"] is True and out["variance"] == -0.01


def test_a_declared_band_is_honoured_and_a_bad_one_does_not_widen_it():
    assert S.declared_band("2.50") == 2.50
    assert S.declared_band(None) == S.HOUSE_BAND
    assert S.declared_band("") == S.HOUSE_BAND
    assert S.declared_band("nonsense") == S.HOUSE_BAND
    assert S.declared_band(-5) == S.HOUSE_BAND
    out = S.outstanding(sale(total=190.54), [pay("cash", 190.53)], band=2.50)
    assert out["verdict"] == "ok" and out["mismatch"] is False


def test_one_tenants_band_never_decides_another_tenants_answer():
    """The module takes the band as an ARGUMENT — it reads no config and holds no org id, so there
    is no path by which one tenant's declaration could answer for another's sale."""
    lenient = S.outstanding(sale(total=192.00), [pay("cash", 190.53)], band=5.00)
    strict = S.outstanding(sale(total=192.00), [pay("cash", 190.53)], band=None)
    assert lenient["mismatch"] is False and strict["mismatch"] is True
    assert lenient["variance"] == strict["variance"] == -1.47


# ── E. the verdict is the platform's existing truth table, not a fourth one ──────────────────────
def test_the_verdict_words_are_deposit_recons_own():
    from app.modules.closing import deposit_recon
    for total, tender in ((210.13, 190.53), (150.00, 190.53), (190.53, 190.53)):
        out = S.outstanding(sale(total=total), [pay("cash", tender)])
        assert out["verdict"] == deposit_recon.status_for(out["variance"], 0.0)


# ── F. the record that becomes the log row ───────────────────────────────────────────────────────
def test_the_record_carries_every_figure_the_log_needs_and_no_derived_total():
    rec = S.record(sale(total=210.13), [pay("cash", 190.53)])
    assert rec["tender_total"] == 190.53
    assert rec["tender_expected"] == 210.13
    assert rec["tender_variance"] == -19.60
    assert rec["tender_verdict"] == "short"
    assert rec["tender_direction"] == S.COLLECT
    assert rec["tender_measured"] is True
    assert rec["tender_methods"] == ["cash"]
    assert rec["tender_reason"] is None


def test_an_unmeasurable_record_carries_the_reason_and_no_figures():
    rec = S.record(sale(total=210.13), [])
    assert rec["tender_measured"] is False
    assert rec["tender_reason"] == "tender_unrecorded"
    assert rec["tender_variance"] is None
    assert rec["tender_verdict"] is None


# ── G. the sentence the till shows ───────────────────────────────────────────────────────────────
def test_the_sentence_names_the_money_and_the_method_when_there_is_one():
    s = S.sentence(sale(total=210.13), S.outstanding(sale(total=210.13), [pay("cash", 190.53)]))
    assert "19.60" in s and "cash" in s.lower() and "collect" in s.lower()


def test_the_sentence_refuses_to_pick_a_method_when_the_sale_had_two():
    out = S.outstanding(sale(total=250.00), [pay("cash", 100.00), pay("credit_card", 90.53)])
    s = S.sentence(sale(total=250.00), out)
    assert "59.47" in s and "cash" in s.lower() and "credit" in s.lower()


def test_the_sentence_for_an_unrecorded_tender_claims_no_figure():
    out = S.outstanding(sale(total=210.13), [])
    s = S.sentence(sale(total=210.13), out)
    assert "$" not in s


# ── H. purity ────────────────────────────────────────────────────────────────────────────────────
def test_the_module_holds_no_clock_no_client_and_no_tenant_name():
    import pathlib
    src = pathlib.Path(S.__file__).read_text()
    assert "get_supabase" not in src and "datetime.now" not in src
    for word in ("cellfonz", "luxelink", "vzone", "boost", "total wireless"):
        assert word not in src.lower()


# ── I. the report rollup — not measurable is counted, never listed as a finding ──────────────────
def _row(**kw):
    base = {"measured": True, "mismatch": False, "variance": 0.0, "direction": S.SETTLED,
            "reason": None, "was_voided": False, "was_edited": False}
    base.update(kw)
    return base


def test_the_summary_counts_each_state_separately():
    s = S.summarise([
        _row(mismatch=True, variance=-19.60, direction=S.COLLECT),
        _row(mismatch=True, variance=-5.00, direction=S.COLLECT),
        _row(mismatch=True, variance=40.53, direction=S.REFUND),
        _row(),
        _row(measured=False, mismatch=False, variance=None, direction=None,
             reason="no payment was ever recorded against this sale"),
    ])
    assert s["sales"] == 5
    assert s["settled"] == 1
    assert s["not_measurable"] == 1
    assert s["mismatches"] == 3
    assert s["to_collect"] == 24.60       # absolute money owed in
    assert s["to_refund"] == 40.53
    assert s["net_variance"] == round(-19.60 - 5.00 + 40.53, 2)


def test_the_summary_of_nothing_is_zeros_and_not_an_error():
    s = S.summarise([])
    assert s["sales"] == 0 and s["mismatches"] == 0 and s["net_variance"] == 0.0


def test_unmeasurable_money_is_never_added_into_any_total():
    """The 4,603-row finding again, at the rollup. An unmeasured row contributes to the COUNT of
    what could not be measured and to no dollar figure anywhere."""
    s = S.summarise([_row(measured=False, mismatch=False, variance=None, direction=None,
                          reason="no payment was ever recorded against this sale")] * 4603)
    assert s["not_measurable"] == 4603
    assert s["to_collect"] == 0.0 and s["to_refund"] == 0.0 and s["net_variance"] == 0.0
    assert s["mismatches"] == 0


def test_blockers_say_what_could_not_be_measured_and_how_many():
    b = S.blockers([
        _row(measured=False, variance=None, direction=None,
             reason="no payment was ever recorded against this sale"),
        _row(measured=False, variance=None, direction=None,
             reason="no payment was ever recorded against this sale"),
        _row(measured=False, variance=None, direction=None,
             reason="this sale does not carry a total"),
        _row(),
    ])
    assert len(b) == 2
    assert {x["count"] for x in b} == {1, 2}
    assert all(x["reason"] for x in b)
    assert b[0]["count"] == 2                      # commonest first, so the screen leads with it


def test_blockers_are_empty_when_everything_was_measurable():
    assert S.blockers([_row(), _row(mismatch=True, variance=-1.0, direction=S.COLLECT)]) == []
