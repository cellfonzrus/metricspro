"""Unit: ONE derivation of "which financed units did this feed capture, for whom, on which day".

WHY THIS EXISTS AS ITS OWN FUNCTION. `financing_report.build` already derived financed units by
vendor x store x rep — but only folded over a whole PERIOD, with the day thrown away. The
declared-vs-captured recon (index §CLOSING-01) needs the same units per DAY. Deriving them a second
time is the duplicate defect CLAUDE.md forbids: two paths answering one question drift, and the one
that pays would drift from the one that accuses a rep.

So the derivation is extracted to `vendor_units`, `build` folds ITS output, and the recon folds the
same output by day. These tests pin that the extraction is faithful — above all the unit collapse
(a financed phone rings as many lines and must count ONCE) and the transaction-basis split (two
phones on one contract must not each report the whole contract).
"""
import pytest

from app.modules.commcalc import financing_report as fr


def _line(trans_id, **kw):
    row = {"trans_id": trans_id, "store": "B-1115", "salesperson": "Arora, Tanish",
           "trans_date": "2026-10-28", "tender_type": "TW Financing", "department": "Handsets",
           "category": "Phones", "product_desc": "moto g play", "contract_type": "New",
           "ext_price": 400.0, "voided": None, "trans_type": "Sale", "serial_1": "356938035643801"}
    row.update(kw)
    return row


# Shaped like a RESOLVED vendor (financing_registry.resolve_vendors output), because that is what
# both callers pass.
EDGE_VENDOR = {"vendor_key": "edge", "label": "Edge financing", "enabled": True,
               "amount_basis": "unit_line", "detection_source": "rules",
               "detection_status": "ok", "detection_note": None, "carriers": [],
               "matchers": [{"match_field": "tender_type", "match_op": "word",
                             "match_value": "financing"}]}


@pytest.fixture
def real_gate():
    """The PAYOUT's own unit collapse, driven for real — one payment per DEVICE, anchored on the line
    carrying a serial. Injected rather than imported inside the module so there is exactly one
    definition of a unit in the platform and this test proves THAT one."""
    from app.modules.commcalc import plan_pay_gate
    return plan_pay_gate


def test_a_financed_sale_rung_as_many_lines_counts_once():
    rows = [_line("T-1"), _line("T-1", department="Autopay", ext_price=0.0, serial_1=""),
            _line("T-1", department="Accessories", ext_price=29.99, serial_1="")]
    units, _notes, _considered = fr.vendor_units(rows, [EDGE_VENDOR])
    assert len(units) == 1
    assert units[0]["vendor_key"] == "edge"


def test_each_unit_carries_the_day_it_happened():
    units, _n, _c = fr.vendor_units([_line("T-1", trans_date="2026-10-27"),
                                     _line("T-2", trans_date="2026-10-28")], [EDGE_VENDOR])
    assert sorted(u["day"] for u in units) == ["2026-10-27", "2026-10-28"]


def test_each_unit_carries_its_store_and_its_rep():
    units, _n, _c = fr.vendor_units([_line("T-1")], [EDGE_VENDOR])
    assert units[0]["store"] == "B-1115" and units[0]["rep"] == "Arora, Tanish"


def test_a_voided_line_and_a_return_are_not_captured_units():
    rows = [_line("T-1", voided="true"), _line("T-2", trans_type="Return")]
    units, _n, _c = fr.vendor_units(rows, [EDGE_VENDOR])
    assert units == []


def test_a_vendor_with_no_matcher_claims_nothing_and_says_why():
    unmapped = dict(EDGE_VENDOR, matchers=[], detection_status="not_configured",
                    detection_note="no tender mapped")
    units, _n, considered = fr.vendor_units([_line("T-1")], [unmapped])
    assert units == []
    c = next(c for c in considered if c["vendor_key"] == "edge")
    assert c["detection_status"] == "not_configured"
    assert c["detection_note"] == "no tender mapped"


def test_two_devices_on_one_contract_are_two_units_under_the_payouts_own_collapse(real_gate):
    rows = [_line("T-1", serial_1="356938035643809", ext_price=400.0),
            _line("T-1", serial_1="356938035643810", ext_price=600.0)]
    units, _n, _c = fr.vendor_units(rows, [EDGE_VENDOR], gate=real_gate)
    assert len(units) == 2
    assert {u["trans_id"] for u in units} == {"T-1"}


def test_without_the_gate_the_fallback_counts_once_per_transaction_and_says_so():
    # The documented degradation. It must be AUDIBLE: a silent one-per-transaction count would
    # understate a two-phone contract with nothing on the page admitting it.
    rows = [_line("T-1", serial_1="356938035643809", ext_price=400.0),
            _line("T-1", serial_1="356938035643810", ext_price=600.0)]
    units, notes, _c = fr.vendor_units(rows, [EDGE_VENDOR])
    assert len(units) == 1
    assert "unit_fallback_per_transaction" in notes


def test_a_transaction_basis_amount_is_split_across_the_devices_it_financed(real_gate):
    # Two phones on ONE contract. Each must report HALF the contract, never the whole of it.
    rows = [_line("T-1", serial_1="356938035643809", ext_price=400.0),
            _line("T-1", serial_1="356938035643810", ext_price=600.0)]
    units, _n, _c = fr.vendor_units(rows, [dict(EDGE_VENDOR, amount_basis="transaction")],
                                    gate=real_gate)
    assert len(units) == 2
    assert sum(u["amount"] for u in units) == pytest.approx(1000.0)
    assert all(u["amount"] == pytest.approx(500.0) for u in units)


def test_the_unit_line_basis_reports_the_devices_own_price(real_gate):
    rows = [_line("T-1", serial_1="356938035643809", ext_price=400.0),
            _line("T-1", serial_1="356938035643810", ext_price=600.0)]
    units, _n, _c = fr.vendor_units(rows, [EDGE_VENDOR], gate=real_gate)
    assert sorted(u["amount"] for u in units) == [400.0, 600.0]


def test_the_report_is_folded_from_the_same_units_it_would_have_derived():
    # The extraction is only safe if `build` DEREFERENCES it. Totals folded from `vendor_units`
    # must equal what the report publishes — this is the equivalence the whole extraction rests on.
    rows = [_line("T-1"), _line("T-1", department="Autopay", ext_price=0.0, serial_1=""),
            _line("T-2", salesperson="Singh, Simarjyot", ext_price=550.0, serial_1="356938035643802")]
    units, _n, _c = fr.vendor_units(rows, [EDGE_VENDOR])
    out = fr.build(rows, [EDGE_VENDOR], {}, {}, None, "2026-10")
    assert out["totals"]["units"] == len(units)
    assert out["totals"]["amount"] == pytest.approx(round(sum(u["amount"] for u in units), 2))
    assert {r["rep"] for r in out["rows"]} == {u["rep"] for u in units}
