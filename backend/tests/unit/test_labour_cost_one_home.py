"""Unit: `storeops/labour_cost` — the one door onto "what did this store's labour cost this month".

Owner report 2026-11-16: the Payroll Report's per-store cost was not reaching the dashboard, the
Gross Profit report or the P&L, for ANY tenant. The cause was a second derivation in the finance
module; the fix is that every non-payroll surface reads the producer the Payroll Report publishes,
through this module. These tests pin the SHAPING — the key spaces, the unattributed bucket and the
fact that this door invents no dollar of its own.

`harness_labour_cost_one_home.py` locks the wiring; `tests/integration/
test_payroll_reaches_store_expenses.py` proves the books and the report agree end to end.
"""
import pytest

from app.modules.storeops import labour_cost as lc
from app.modules.storeops import payroll_salary as ps

ADDR = {"qv": "218-80 Hempstead Avenue", "grand": "3966 W Grand Ave"}
FOLD = lambda s: (str(s or "").strip().lower()) or None      # noqa: E731


def rows(*pairs):
    return [{"store_code": c, "hours": h, "amount": a} for c, h, a in pairs]


class TestFolding:
    def test_two_rows_for_one_store_sum(self):
        cells = lc.fold_rows(rows(("QV", 10.0, 150.0), ("QV", 2.0, 30.0)))
        assert cells == {"QV": {"hours": 12.0, "amount": 180.0}}

    def test_an_empty_answer_is_empty_not_a_zero_cell(self):
        assert lc.fold_rows([]) == {} and lc.fold_rows(None) == {}

    def test_a_non_numeric_amount_does_not_raise(self):
        assert lc.fold_rows([{"store_code": "QV", "amount": None}])["QV"]["amount"] == 0.0


class TestTheUnattributedBucket:
    """Pay that belongs to no store must stay visible and must never become a store."""

    @pytest.mark.parametrize("code", ["", "   ", None, "Unassigned", "unassigned", "UNASSIGNED"])
    def test_every_spelling_of_no_store_is_recognised(self, code):
        assert lc.is_unattributed(code)

    def test_a_real_store_is_not(self):
        assert not lc.is_unattributed("QV") and not lc.is_unattributed("Unassigned Ave")

    def test_the_sentinel_is_dereferenced_from_payroll_not_respelled(self):
        assert lc.is_unattributed(ps.UNASSIGNED_STORE)

    def test_the_two_spellings_fold_together(self):
        cells = lc.fold_rows(rows((ps.UNASSIGNED_STORE, 0.0, 6500.0), ("", 0.0, 100.0)))
        assert cells == {"": {"hours": 0.0, "amount": 6600.0}}

    def test_it_books_company_wide_never_as_a_store(self):
        addr = lc.cells_by_address(lc.fold_rows(rows((ps.UNASSIGNED_STORE, 0.0, 6500.0))),
                                   ADDR, norm=FOLD)
        assert addr == {None: 6500.0}


class TestKeySpaces:
    def test_code_space_keeps_the_shifts_own_code(self):
        assert lc.cells_by_code(lc.fold_rows(rows(("Grand", 8.0, 160.0)))) == {"Grand": 160.0}

    def test_address_space_maps_through_the_callers_own_identity_map(self):
        cells = lc.fold_rows(rows(("QV", 1.0, 180.0), ("Grand", 1.0, 160.0)))
        assert lc.cells_by_address(cells, ADDR, norm=FOLD) == {
            "218-80 Hempstead Avenue": 180.0, "3966 W Grand Ave": 160.0}

    def test_an_unknown_code_keeps_its_own_spelling_rather_than_vanishing(self):
        assert lc.cells_by_address(lc.fold_rows(rows(("Nowhere", 1.0, 42.0))), ADDR,
                                   norm=FOLD) == {"nowhere": 42.0}

    def test_two_codes_sharing_one_address_are_summed_not_overwritten(self):
        cells = lc.fold_rows(rows(("QV", 1.0, 10.0), ("Qv ", 1.0, 5.0)))
        assert lc.cells_by_address(cells, ADDR, norm=FOLD) == {"218-80 Hempstead Avenue": 15.0}

    def test_no_money_is_created_or_lost_across_either_space(self):
        cells = lc.fold_rows(rows(("QV", 1.0, 180.0), ("Grand", 1.0, 160.0),
                                  (ps.UNASSIGNED_STORE, 0.0, 60.0)))
        assert round(sum(lc.cells_by_code(cells).values()), 2) == 400.0
        assert round(sum(lc.cells_by_address(cells, ADDR, norm=FOLD).values()), 2) == 400.0


class TestTheDoor:
    """It calls the producer and shapes the answer. It derives nothing."""

    def test_it_returns_whatever_the_producer_says(self):
        out = lc.per_store("ORG", month="2026-10", producer=lambda **kw: {"stores": rows(("QV", 1.0, 9.0))})
        assert out == {"QV": {"hours": 1.0, "amount": 9.0}}

    def test_it_hands_the_producer_the_period_and_the_callers_database(self):
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return {"stores": []}

        lc.per_store("ORG-7", start="2026-10-01", end="2026-10-31", so="HANDLE", producer=fake)
        assert seen == {"month": None, "start": "2026-10-01", "end": "2026-10-31",
                        "org_id": "ORG-7", "so": "HANDLE"}

    def test_one_tenants_answer_is_never_another_tenants(self):
        """The org id is the producer's only tenant input here — this door adds no scope of its own
        and, just as importantly, strips none."""
        calls = []
        lc.per_store("ORG-A", month="2026-10", producer=lambda **kw: calls.append(kw["org_id"]) or {"stores": []})
        lc.per_store("ORG-B", month="2026-10", producer=lambda **kw: calls.append(kw["org_id"]) or {"stores": []})
        assert calls == ["ORG-A", "ORG-B"]

    def test_a_producer_that_answers_nothing_is_not_a_zero(self):
        assert lc.per_store("ORG", month="2026-10", producer=lambda **kw: None) == {}
