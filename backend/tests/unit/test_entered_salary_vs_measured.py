"""Unit: an ENTERED salary figure is checked against the salary the platform MEASURES.

Owner report 2026-11-12 (Sanjot, verbatim): *"in the accoutn module i diod the compute but the
expenses are not  showing correctly , they shoudl include the salaries"*.

WHAT WAS ON SCREEN. LuxeLink's October 2026 P&L booked **$54,044.69** of salary against
**$105,109.69** the platform could measure from the same month's hours and punches — $51,065.00 of
labour simply absent from October's expenses. Nothing was miscomputed: the Expenses sheet's
"fill salaries" button had COPIED a payroll figure into an `Employee Salaries` expense row per
store, and a payroll row CONFERS authority, which SUPPRESSES the platform's own live measurement.
So the copy froze: `Belmont` booked $166.50 against $7,339.70 measured, `Utica` $90.00 against
$3,699.24, `3735 26th` $85.25 against $6,138.41 — while three stores happened to still agree to
the cent, which is exactly why nobody could see it by eye.

THE CLASS, not the instance. A fact the platform measures and ALSO stores a copy of has two homes,
and the copy cannot be told from a declaration — so a stale copy outranks a live measurement and no
surface says so. This module can only REPORT that (the routing decision is the owner's, and it moves
money), and reporting it is not optional: a figure that reads like a measurement and is not one is
the silent-$0.00 defect this report already exists to refuse, one step along.

WHAT THESE TESTS REFUSE TO LET HAPPEN, worst first:
  * a stale entered figure presented with no indication the platform measured something else
  * an absence reported as agreement — no measurement available must NEVER read as "$0.00 gap"
    (the tri-state verdict discipline, §19.50 / §CORE-17)
  * a store with no measurement at all treated as a $0.00 measurement, which would report its whole
    entered salary as "overstated" and send an owner hunting a defect that is missing clock data
  * a flat allocation (`DM Salaries` at one identical amount estate-wide) compared against a
    store's own staff hours as though it measured them
  * one tenant's payroll vocabulary deciding another tenant's answer (RULE TWO / §COMMCALC-49.10)
"""
import pytest

from app.modules.commcalc import labour_coverage as lc

PAYROLL_NAMES = ["Employee Salaries", "DM Salaries"]


def _exp(code, name, amount, source_key=None):
    return {"store_code": code, "expense_name": name, "amount": amount,
            "expense_type": "Fixed", "source_key": source_key}


# ── the accumulator: entered payroll dollars, in the CALLER's key space ─────────────────────────

class TestPayrollExpenseByKey:
    def test_sums_the_orgs_own_payroll_names_only(self):
        rows = [_exp("Belmont", "Employee Salaries", 166.50),
                _exp("Belmont", "DM Salaries", 1275.00),
                _exp("Belmont", "Rent / Lease", 3550.00)]
        assert lc.payroll_expense_by_key(rows, PAYROLL_NAMES) == {"Belmont": 1441.50}

    def test_no_vocabulary_claims_nothing(self):
        """RULE TWO: the names are the tenant's. Nothing declared -> nothing identified."""
        rows = [_exp("Belmont", "Employee Salaries", 166.50)]
        assert lc.payroll_expense_by_key(rows, []) == {}
        assert lc.payroll_expense_by_key(rows, None) == {}

    def test_one_tenants_names_never_read_anothers_rows(self):
        rows = [_exp("Belmont", "Employee Salaries", 166.50)]
        assert lc.payroll_expense_by_key(rows, ["Staff Wages"]) == {}

    def test_key_of_maps_into_the_readers_space(self):
        """The P&L books by canonical store ADDRESS; the sheet keys by code. Same pattern as
        `commission_expense_by_key`, so the two sides can never disagree about the key space."""
        rows = [_exp("Belmont", "Employee Salaries", 100.0),
                _exp("BELMONT", "Employee Salaries", 50.0)]
        out = lc.payroll_expense_by_key(rows, PAYROLL_NAMES,
                                        key_of=lambda c: "5601 W Belmont Ave")
        assert out == {"5601 W Belmont Ave": 150.0}

    def test_a_producer_row_counts_without_a_vocabulary(self):
        """An exact gross pushed by the payroll run is authoritative on its source_key alone."""
        rows = [_exp("Belmont", "anything", 9000.0, source_key="payroll_gross")]
        assert lc.payroll_expense_by_key(rows, []) == {"Belmont": 9000.0}

    def test_rows_with_no_store_are_skipped(self):
        rows = [_exp(None, "Employee Salaries", 500.0), _exp("  ", "Employee Salaries", 500.0)]
        assert lc.payroll_expense_by_key(rows, PAYROLL_NAMES) == {}


# ── the comparison ─────────────────────────────────────────────────────────────────────────────

class TestEnteredVsMeasured:
    def test_the_live_defect_is_reported_with_both_figures(self):
        entered = {"Belmont": 166.50, "Utica": 90.00, "957": 4523.53}
        measured = {"Belmont": 7339.70, "Utica": 3699.24, "957": 4523.53}
        out = lc.entered_vs_measured(entered, measured)
        assert out["verdict"] == lc.VERDICT_MEASURED
        keys = {s["store_key"]: s for s in out["stores"]}
        assert set(keys) == {"Belmont", "Utica"}          # 957 agrees to the cent -> not a finding
        assert keys["Belmont"]["entered"] == 166.50
        assert keys["Belmont"]["measured"] == 7339.70
        assert keys["Belmont"]["gap"] == 7173.20
        assert keys["Belmont"]["direction"] == lc.UNDERSTATED
        assert out["total_understated"] == 10782.44        # 7173.20 + 3609.24
        assert out["total_overstated"] == 0.0
        assert "$10,782.44" in out["note"]

    def test_no_measurement_at_all_is_an_absence_not_an_agreement(self):
        """A float cannot carry "I measured nothing" and "I measured zero gap"."""
        out = lc.entered_vs_measured({"Belmont": 166.50}, None)
        assert out["verdict"] == lc.VERDICT_NOT_MEASURED
        assert out["total_understated"] is None
        assert out["total_overstated"] is None
        assert out["stores"] == []
        assert out["note"] and "NOT CHECKED" in out["note"]

    def test_an_unmeasured_store_is_not_a_zero_measurement(self):
        """Belmont has an entered figure and no measurable hours. Reporting it as $166.50
        OVERSTATED would send an owner looking for a code defect; the fault is missing clock data."""
        out = lc.entered_vs_measured({"Belmont": 166.50}, {"Utica": 10.0})
        assert out["stores"] == []
        assert out["total_overstated"] == 0.0
        assert out["unmeasured_keys"] == ["Belmont"]

    def test_an_entered_figure_above_the_measurement_is_reported_too(self):
        out = lc.entered_vs_measured({"Belmont": 9000.0}, {"Belmont": 7339.70})
        s = out["stores"][0]
        assert s["direction"] == lc.OVERSTATED
        assert s["gap"] == 1660.30
        assert out["total_overstated"] == 1660.30
        assert out["total_understated"] == 0.0

    def test_agreement_is_reported_as_measured_with_no_note(self):
        out = lc.entered_vs_measured({"Belmont": 7339.70}, {"Belmont": 7339.70})
        assert out["verdict"] == lc.VERDICT_MEASURED
        assert out["stores"] == []
        assert out["total_understated"] == 0.0
        assert out["note"] is None

    def test_a_rounding_residue_is_not_a_finding(self):
        out = lc.entered_vs_measured({"Belmont": 7339.69}, {"Belmont": 7339.70})
        assert out["stores"] == []

    def test_a_small_percentage_drift_is_not_a_finding(self):
        """Tolerance is a POLICY default (a percentage of the measurement), not a tenant's word."""
        out = lc.entered_vs_measured({"Belmont": 7000.0}, {"Belmont": 7200.0})
        assert out["stores"] == []
        out2 = lc.entered_vs_measured({"Belmont": 7000.0}, {"Belmont": 7200.0}, tolerance_pct=0.0)
        assert out2["stores"] and out2["stores"][0]["gap"] == 200.0

    def test_a_store_measured_but_never_entered_is_not_a_finding_here(self):
        """It has no entered figure to be stale: `labour_coverage`'s own states already own that."""
        out = lc.entered_vs_measured({}, {"Belmont": 7339.70})
        assert out["stores"] == []
        assert out["total_understated"] == 0.0

    def test_findings_are_ordered_by_how_badly_they_mislead(self):
        out = lc.entered_vs_measured({"a": 10.0, "b": 10.0, "c": 10.0},
                                      {"a": 2000.0, "b": 9000.0, "c": 5000.0})
        assert [s["store_key"] for s in out["stores"]] == ["b", "c", "a"]


# ── wired into the report every surface already reads ──────────────────────────────────────────

class TestLabourCoverageCarriesIt:
    def _shifts(self, code, n=20, hours=8.0):
        return [{"store_code": code, "employee_id": "E1", "actual_hours": hours,
                 "scheduled_hours": hours, "shift_date": "2026-10-%02d" % (i + 1)}
                for i in range(n)]

    def test_the_report_carries_the_comparison_and_its_note(self):
        rows = [_exp("Belmont", "Employee Salaries", 166.50)]
        out = lc.labour_coverage(["Belmont"], rows, self._shifts("Belmont"), PAYROLL_NAMES,
                                  "October 2026", measured_by_code={"Belmont": 7339.70})
        evm = out["entered_vs_measured"]
        assert evm["verdict"] == lc.VERDICT_MEASURED
        assert evm["total_understated"] == 7173.20
        assert "$7,173.20" in out["note"]

    def test_a_caller_that_hands_over_no_measurement_gets_the_absence(self):
        """Byte-identical for every existing caller: the key is present, the verdict says it was
        not checked, and no dollar of the report moves."""
        rows = [_exp("Belmont", "Employee Salaries", 166.50)]
        out = lc.labour_coverage(["Belmont"], rows, self._shifts("Belmont"), PAYROLL_NAMES,
                                  "October 2026")
        assert out["entered_vs_measured"]["verdict"] == lc.VERDICT_NOT_MEASURED
        assert out["entered_vs_measured"]["total_understated"] is None

    def test_a_flat_allocation_is_not_compared_against_a_stores_own_hours(self):
        """`DM Salaries` at one identical amount estate-wide is an allocation, not a measurement of
        that store's staff — `allocated_names` already rules on this and the comparison reuses it
        rather than deciding again."""
        rows = []
        for code in ("a", "b", "c", "d"):
            rows.append(_exp(code, "DM Salaries", 1275.00))
        shifts = []
        for code in ("a", "b", "c", "d"):
            shifts += self._shifts(code)
        out = lc.labour_coverage(["a", "b", "c", "d"], rows, shifts, ["DM Salaries"],
                                  "October 2026",
                                  measured_by_code={c: 7000.0 for c in ("a", "b", "c", "d")})
        assert out["entered_vs_measured"]["stores"] == []
        assert out["entered_vs_measured"]["allocated_excluded"] == ["dm salaries"]

    def test_a_carried_month_is_not_reported_as_a_stale_entry(self):
        """A carried month's dollars belong to another month and `coverage_note` already says so
        in full. Reporting each carried row as "stale" on top of that is a second sentence about
        one fact — and the owner would be told to re-enter a figure the carry rule is about to
        replace anyway (§COMMCALC-23)."""
        rows = [_exp("Belmont", "Employee Salaries", 166.50)]
        out = lc.labour_coverage(["Belmont"], rows, self._shifts("Belmont"), PAYROLL_NAMES,
                                  "October 2026", carried_from="September 2026",
                                  measured_by_code={"Belmont": 7339.70})
        assert out["entered_vs_measured"]["verdict"] == lc.VERDICT_NOT_MEASURED
        assert "carried" in (out["entered_vs_measured"]["note"] or "").lower()
