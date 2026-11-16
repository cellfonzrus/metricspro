"""Unit: a CLOSED store keeps its identity, so the months it was open cannot vanish.

Owner ruling 2026-11-16 (Sanjot, verbatim): *"902 is closed for good , it set to inactive"*.

THE DEFECT THAT RULING EXPOSED, measured live on production the same day. `902 Ave U` is a LuxeLink
store that shut. Its October 2026 shifts are still in the roster and its labour still measured at
$6,352.09 — real money, really spent, in a month that really happened. But `store_identity_index`,
the ONE home for "which store is this string?", SKIPPED an explicitly inactive mapping row. With no
identity, the Gross Profit report could not key that store at all: the $6,352.09 was not hidden from
the report, it was *absent* from it, reported only as `labour_measured_fill.unkeyed` in the payload
where nobody reads it. The P&L, which keys by canonical address and never consulted this index, had
it right all along at $57,727.81 — so this was the SAME class of defect as §ACCOUNT-03: one question
answered two ways, the reports disagreeing, and the owner told again that the numbers conflict.

THE CLASS. **Identity is not visibility.** Hiding a closed store is a ROW rule — the owner's own
earlier directive, *"once the store or employee is made inactive their data should [not] be shwoing
on any module unless the checkox is clicked"* (§CORE-45) — and `core/roster_visibility` already
states the other half of it: *"A report that quietly drops a closed store's September money is a
report that lies about September."* An index that refuses to NAME a store cannot implement either
rule; it just loses the money and says nothing.

So: every mapping row is indexed, each identity carries `is_active` read from the §CORE-45 home
(never a second copy of the predicate), a caller wanting a LIST narrows it explicitly, and the
Gross Profit report drops a closed store only when that store has nothing in the period.
"""
import pytest

from app.modules.account import store_identity as sid
from app.modules.commcalc.gp_report import calc_gp_report

PERIOD = "October 2026"
LIVE = {"store_code": "LIVE-1", "store_address": "1 Open St", "market": "NY",
        "salesforce_id": "SF-1", "is_active": True}
CLOSED = {"store_code": "AVE-U", "store_address": "902 Ave U", "market": "NY",
          "salesforce_id": None, "is_active": False}
MAPPING = [LIVE, CLOSED]


class TestIdentityIsNotVisibility:
    def test_a_closed_store_still_has_an_identity(self):
        """THE REGRESSION. Before the fix this key was simply absent, and with it the store."""
        idx = sid.store_identity_index(MAPPING)
        assert "902 Ave U" in idx
        assert idx["902 Ave U"]["store_code"] == "AVE-U"

    def test_and_the_index_says_it_is_closed(self):
        idx = sid.store_identity_index(MAPPING)
        assert idx["902 Ave U"]["is_active"] is False
        assert idx["1 Open St"]["is_active"] is True

    def test_a_caller_that_wants_a_list_narrows_it_explicitly(self):
        idx = sid.store_identity_index(MAPPING, include_inactive=False)
        assert "902 Ave U" not in idx and "1 Open St" in idx

    @pytest.mark.parametrize("flag", [None, True, "true", 1, "TRUE"])
    def test_null_or_true_means_active(self, flag):
        """NULL means ACTIVE — the trap that made live employees vanish from every roster."""
        row = dict(CLOSED, is_active=flag)
        assert sid.store_identity_index([row], include_inactive=False)
        assert sid.store_identity_index([row])["902 Ave U"]["is_active"] is True

    @pytest.mark.parametrize("flag", [False, "false", "FALSE", 0])
    def test_the_string_false_is_not_active_though_python_calls_it_truthy(self, flag):
        row = dict(CLOSED, is_active=flag)
        assert sid.store_identity_index([row], include_inactive=False) == {}
        assert sid.store_identity_index([row])["902 Ave U"]["is_active"] is False

    def test_one_live_row_anywhere_makes_the_store_live_whatever_the_order(self):
        """`902` has TWO mapping rows live today, one active and one not, and the shifts carry the
        inactive one. A store is not closed because one of its spellings is."""
        both = [dict(CLOSED), dict(CLOSED, store_code="AVE-U-NEW", is_active=True)]
        for rows in (both, list(reversed(both))):
            ent = sid.store_identity_index(rows)["902 Ave U"]
            assert ent["is_active"] is True
            assert ent["store_code"] == "AVE-U-NEW", "the LIVE row is folded first"

    def test_the_withholding_relation_still_narrows_to_live_rows(self):
        """`shared_identity_pairs` is a different question — "could these two addresses be ONE
        store" — and a caller reading it SUPPRESSES a finding about real dollars. Broadening it to
        closed rows is a money-visible change, measured on its own, not carried in here."""
        assert sid.shared_identity_pairs([dict(LIVE, store_code="AVE-U"), CLOSED]) == set()


class TestTheGrossProfitRowRule:
    def _report(self, expenses):
        return {r["store"]: r for r in calc_gp_report(
            [], [], [], [], expenses, [], MAPPING, PERIOD)["store_rows"]}

    def test_the_closed_stores_labour_is_booked_not_lost(self):
        """THE $6,352.09. It is the number the report was missing, to the cent."""
        rows = self._report([{"store_code": "AVE-U", "amount": 6352.09,
                              "category": "Employee Salaries"}])
        assert round(rows["902 Ave U"]["exp_total"], 2) == 6352.09
        assert round(rows["902 Ave U"]["net_profit"], 2) == -6352.09

    def test_a_closed_store_with_nothing_this_period_is_not_a_line(self):
        """The owner's hide-inactive directive, now implementable because the store HAS a name."""
        assert "902 Ave U" not in self._report([])

    def test_a_live_store_with_nothing_is_still_listed(self):
        """So the drop is about closure, not about emptiness — a live store that sold nothing in a
        month must still show its rent."""
        assert "1 Open St" in self._report([])


class TestTheRosterAnswersWhetherAStoreIsClosed:
    """Owner, shown three of the four as "still open": *"All these 4 stores are closed"*.

    He was right and the mapping flag was not. `storeops.stores` is the home for the store roster
    and §CORE-45 for the predicate; `commcalc.store_mapping.is_active` is a flag about a MAPPING
    ROW. Live they disagree — all four closed LuxeLink stores are `is_active=false` in the roster
    while a second, stale `LUX-*` mapping row still says active — so a report reading the mapping
    flag calls a closed store open. Two copies of one fact, which is the §19.18 defect itself.
    """
    def _report(self, expenses, roster):
        return {r["store"] for r in calc_gp_report(
            [], [], [], [], expenses, [], MAPPING, PERIOD,
            store_active_by_code=roster)["store_rows"]}

    def test_the_roster_overrides_a_stale_active_mapping_row(self):
        assert "1 Open St" not in self._report([], {"LIVE-1": False})

    def test_but_the_closed_store_is_still_listed_for_a_period_it_has_data_in(self):
        assert "1 Open St" in self._report(
            [{"store_code": "LIVE-1", "amount": 1.0}], {"LIVE-1": False})

    def test_the_roster_wins_both_ways(self):
        """Not a one-directional hide: a store the mapping calls closed and the roster calls open is
        listed, so this cannot become a quiet way to drop stores."""
        assert "902 Ave U" in self._report([], {"AVE-U": True})

    def test_a_store_the_roster_has_never_heard_of_keeps_the_mapping_answer(self):
        """UNREADABLE IS NOT CLOSED. A partial or missing roster must never take a store off the
        report, which would take its money with it."""
        assert "1 Open St" in self._report([], {"SOMETHING-ELSE": False})

    def test_an_empty_roster_changes_nothing(self):
        assert "1 Open St" in self._report([], {})
