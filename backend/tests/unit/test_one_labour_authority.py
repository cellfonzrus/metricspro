"""Unit: ONE decision about whose labour figure books, dereferenced by every report.

Owner report 2026-11-14 (Sanjot, verbatim): *"check Gross profit report it shows different numbers,
we have spoken about this a lot of times if the source of data is the same why are th numbers
different , this is conflicting and must be fixed in teh code itself"* — and, the same hour, the
ruling that settles which figure is right: *"the luxelink salary is updated everyweek by the Dms and
that is the approved salary you should be counting towards the expenses"*.

WHAT WAS ON SCREEN, measured live on production 2026-11-14 before a line was changed. The Gross
Profit report's expense column adds up the Expenses sheet — which HOLDS the DM-approved
`Employee Salaries` rows — and then adds the payroll measured from hours ON TOP of it:

| store | DM-approved sheet salary | measured payroll added again | report showed |
| --- | --- | --- | --- |
| `Diversey` | $7,308.26 | $12,555.70 | **$27,789.02** |
| `Belmont` | $1,441.50 | $7,097.48 | **$14,213.98** |

**$86,369.22 of October labour booked twice**, at 15 of the 19 stores the month measured. The P&L never had it: its
own authority rule books the approved row and falls back to hours only for a store with no row.
So one question — "whose labour figure books this store-month?" — had two answers in two files,
which is the duplicate defect the house rules forbid, and the owner has been told twice that the
reports disagree.

THE RULING, applied: the entered (DM-approved) figure wins where it exists; the measurement fills in
only a store that has no approved figure. That rule now lives in ONE pure function and both readers
dereference it.

WHAT THESE TESTS REFUSE TO LET HAPPEN, worst first:
  * labour booked twice for one store-month (the defect above)
  * a store with no approved figure losing its labour cost entirely (the opposite failure — a silent
    $0.00 that reads as "this store paid nobody", §4e)
  * the fill decision being made in a key space where one store answers under two codes (live:
    `Utica` on the sheet, `LUX-NY-UTICA` in the mapping)
  * one tenant's payroll vocabulary deciding another tenant's fill (RULE TWO / §COMMCALC-49.10)
  * an org-grain tenant silently switching to per-store behaviour
"""
import pytest

from app.modules.commcalc import labour_coverage as lc

NAMES = ["Employee Salaries", "DM Salaries"]


def _exp(code, name, amount, source_key=None):
    return {"store_code": code, "expense_name": name, "amount": amount, "source_key": source_key}


# The live October shape, reduced to the four stores that make every case visible.
SHEET = [
    _exp("Diversey", "Employee Salaries", 6033.26), _exp("Diversey", "DM Salaries", 1275.00),
    _exp("Belmont", "Employee Salaries", 166.50), _exp("Belmont", "DM Salaries", 1275.00),
    _exp("Irving Park", "Rent / Lease", 3850.00),          # no salary row at all
    _exp("Narragansett", "Rent / Lease", 3840.00),         # no salary row at all
]
MEASURED = {"Diversey": 12555.70, "Belmont": 7097.48,
            "Irving Park": 1077.04, "Narragansett": 1128.22}


class TestMeasuredFill:
    def test_a_store_with_an_approved_figure_does_not_also_book_its_hours(self):
        """The defect, stated as a test: Diversey and Belmont must contribute NOTHING here."""
        plan = lc.measured_fill(SHEET, MEASURED, NAMES, grain="store")
        assert plan["fill"] == {"Irving Park": 1077.04, "Narragansett": 1128.22}
        assert sorted(plan["suppressed"]) == ["Belmont", "Diversey"]

    def test_the_dollars_it_refuses_to_double_book_are_reported_not_hidden(self):
        plan = lc.measured_fill(SHEET, MEASURED, NAMES, grain="store")
        assert plan["suppressed_amount"] == 19653.18        # 12555.70 + 7097.48
        assert plan["fill_amount"] == 2205.26               # 1077.04 + 1128.22
        assert plan["note"] and "$19,653.18" in plan["note"]

    def test_a_store_with_no_approved_figure_keeps_its_labour_cost(self):
        """The opposite failure. Suppressing everything would read as "these stores paid nobody"."""
        plan = lc.measured_fill(SHEET, MEASURED, NAMES, grain="store")
        assert plan["fill"]["Irving Park"] == 1077.04

    def test_a_zero_placeholder_is_not_an_approved_figure(self):
        rows = SHEET + [_exp("Irving Park", "Employee Salaries", 0.0)]
        plan = lc.measured_fill(rows, MEASURED, NAMES, grain="store")
        assert "Irving Park" in plan["fill"]

    def test_a_producer_row_is_an_approved_figure_without_any_vocabulary(self):
        rows = [_exp("Irving Park", "anything", 9000.0, source_key="payroll_gross")]
        plan = lc.measured_fill(rows, MEASURED, [], grain="store")
        assert "Irving Park" not in plan["fill"]


class TestGrain:
    def test_org_grain_means_one_approved_row_anywhere_suppresses_every_store(self):
        plan = lc.measured_fill(SHEET, MEASURED, NAMES, grain="org")
        assert plan["fill"] == {}
        assert plan["grain"] == "org"
        assert sorted(plan["suppressed"]) == sorted(MEASURED)

    def test_org_grain_with_no_approved_row_anywhere_fills_every_store(self):
        plan = lc.measured_fill([_exp("Diversey", "Rent / Lease", 10.0)], MEASURED, NAMES,
                                 grain="org")
        assert plan["fill"] == MEASURED

    def test_the_house_grain_is_dereferenced_never_respelled(self):
        from app.modules.commcalc import labour_vocabulary as lv
        assert lc.measured_fill(SHEET, MEASURED, NAMES)["grain"] == lv.HOUSE_AUTHORITY_GRAIN


class TestKeySpace:
    def test_the_decision_is_made_in_the_callers_key_space(self):
        """Live: the sheet says `Utica` and the mapping says `LUX-NY-UTICA`. Deciding in two key
        spaces is how a store answered twice — so both sides fold through the caller's key_of."""
        sheet = [_exp("Utica", "Employee Salaries", 1365.00)]
        measured = {"LUX-NY-UTICA": 3888.74}
        fold = {"Utica": "utica", "LUX-NY-UTICA": "utica"}.get
        plan = lc.measured_fill(sheet, measured, NAMES, grain="store", key_of=fold)
        assert plan["fill"] == {}
        assert plan["suppressed"] == ["utica"]

    def test_the_dollars_refused_are_counted_in_the_folded_space_too(self):
        """The first cut looked the suppressed amount back up by its FOLDED key in the caller's RAW
        map, so the GP report told twelve LuxeLink stores that $0.00 was refused while it was
        holding back $19,653.18. Counting it anywhere but in the one pass mixes key spaces again."""
        sheet = [_exp("Utica", "Employee Salaries", 1365.00)]
        fold = {"Utica": "utica", "LUX-NY-UTICA": "utica"}.get
        plan = lc.measured_fill(sheet, {"LUX-NY-UTICA": 3888.74}, NAMES, grain="store", key_of=fold)
        assert plan["suppressed_amount"] == 3888.74
        assert plan["suppressed_by_key"] == {"utica": 3888.74}
        assert "$3,888.74" in plan["note"]

    def test_two_measured_codes_folding_to_one_store_are_summed_not_lost(self):
        fold = {"A": "s", "B": "s"}.get
        plan = lc.measured_fill([], {"A": 100.0, "B": 25.5}, NAMES, grain="store", key_of=fold)
        assert plan["fill"] == {"s": 125.5}

    def test_without_the_fold_the_same_rows_double_book_and_that_is_the_bug(self):
        """Pins the mechanism itself, so a future change cannot quietly drop the fold."""
        sheet = [_exp("Utica", "Employee Salaries", 1365.00)]
        plan = lc.measured_fill(sheet, {"LUX-NY-UTICA": 3888.74}, NAMES, grain="store")
        assert plan["fill"] == {"LUX-NY-UTICA": 3888.74}


class TestTenantVocabulary:
    def test_one_tenants_payroll_words_never_decide_anothers_fill(self):
        sheet = [_exp("Diversey", "Employee Salaries", 6033.26)]
        plan = lc.measured_fill(sheet, MEASURED, ["Staff Wages"], grain="store")
        assert "Diversey" in plan["fill"]            # not this tenant's word -> not approved here

    def test_nothing_declared_means_nothing_approved(self):
        plan = lc.measured_fill(SHEET, MEASURED, [], grain="store")
        assert plan["fill"] == MEASURED

    def test_a_flat_allocation_alone_is_not_an_approved_measurement_of_a_stores_staff(self):
        """`DM Salaries` at one identical amount estate-wide is an allocation (§4e `allocated_names`
        already ruled on this). A store carrying ONLY that has no approved figure of its own."""
        rows = [_exp(c, "DM Salaries", 1275.00) for c in ("a", "b", "c", "d")]
        rows.append(_exp("a", "Employee Salaries", 5000.00))
        measured = {c: 7000.0 for c in ("a", "b", "c", "d")}
        plan = lc.measured_fill(rows, measured, NAMES, grain="store")
        assert plan["fill"] == {"b": 7000.0, "c": 7000.0, "d": 7000.0}
        assert plan["allocated_excluded"] == ["dm salaries"]


class TestItIsTheSameDecisionTheStatementMakes:
    def test_it_reproduces_the_pl_authority_set(self):
        """`coa.build_inputs` used to hold this rule inline. Same rows, same answer, or the two
        reports are back to disagreeing."""
        auth = lc.authoritative_codes(SHEET, [n for n in NAMES
                                              if n.lower() not in lc.allocated_names(SHEET, NAMES)])
        plan = lc.measured_fill(SHEET, MEASURED, NAMES, grain="store")
        assert set(plan["suppressed"]) == {c for c in MEASURED if c in auth}

    def test_no_measurement_at_all_is_not_a_fill_of_zero(self):
        plan = lc.measured_fill(SHEET, None, NAMES, grain="store")
        assert plan["fill"] == {}
        assert plan["measured"] is False
        assert plan["fill_amount"] is None
        assert plan["suppressed_amount"] is None
