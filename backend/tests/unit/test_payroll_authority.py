"""Unit: which source is a tenant's salary authority (owner ruling 2026-11-16, index §ACCOUNT-06).

Owner, verbatim: *"the typer paytol was only for reference take it out, only use that if the store
is not running payroll"*, and on the decision card: **"Per-tenant switch, Payroll on by default"**.

`harness_labour_coverage.py` §L/§M locks the decision and the wiring. These tests pin the thing a
harness is worst at: that the switch is PER TENANT and that one tenant's answer can never be another
tenant's (owner directive 2026-10-30 — every feature is for every tenant, with the cross-tenant
tests shipped beside it).
"""
import pytest

from app.modules.commcalc import labour_coverage as lc
from app.modules.commcalc import labour_actuals as la
from app.modules.commcalc import labour_vocabulary as lv

NAMES = ["Employee Salaries", "DM Salaries"]


def sheet(*pairs):
    return [{"store_code": c, "expense_name": n, "amount": a} for c, n, a in pairs]


class TestTheHouseDefault:
    """A tenant that declared nothing gets the Payroll Report — that is what makes it a fix for
    every tenant rather than for the one that asked."""

    @pytest.mark.parametrize("declared", [None, "", "   ", "MEASURED", " measured ", "nonsense"])
    def test_everything_but_an_explicit_opt_in_is_the_payroll_report(self, declared):
        assert lc.resolve_payroll_authority(declared) == lc.PAYROLL_AUTHORITY_MEASURED

    @pytest.mark.parametrize("declared", ["entered", "ENTERED", " Entered "])
    def test_a_tenant_can_still_ask_for_its_typed_sheet(self, declared):
        assert lc.resolve_payroll_authority(declared) == lc.PAYROLL_AUTHORITY_ENTERED

    def test_one_predicate_answers_it(self):
        assert lc.entered_outranks(None) is False
        assert lc.entered_outranks("entered") is True


class TestTheSwitchIsPerTenant:
    """The defect class this guards: a setting read once and applied to whoever asks next."""

    def setup_method(self):
        self.rows = sheet(("Diversey", "Employee Salaries", 6033.26))
        self.measured = {"Diversey": 12555.70}

    def _plan(self, authority):
        return lc.measured_fill(self.rows, self.measured, NAMES,
                                grain="store", authority=authority)

    def test_two_tenants_resolved_in_one_process_get_their_own_answers(self):
        a = self._plan(None)                      # house: the Payroll Report
        b = self._plan("entered")                 # opt-in: ruling K2
        assert a["fill"] == {"Diversey": 12555.70} and a["suppressed"] == []
        assert b["fill"] == {} and b["suppressed"] == ["Diversey"]

    def test_the_opt_in_tenant_does_not_change_the_next_tenant_read(self):
        self._plan("entered")
        again = self._plan(None)
        assert again["fill"] == {"Diversey": 12555.70}
        assert again["superseded"] == {"Diversey": 6033.26}

    def test_the_plan_states_the_authority_it_decided_under(self):
        assert self._plan(None)["authority"] == lc.PAYROLL_AUTHORITY_MEASURED
        assert self._plan("entered")["authority"] == lc.PAYROLL_AUTHORITY_ENTERED

    def test_the_vocabulary_publishes_each_tenants_own_setting(self):
        one = lv.resolve(NAMES, [], "store", authority_cfg="entered")
        two = lv.resolve(NAMES, [], "store", authority_cfg=None)
        assert one["payroll_authority_source"] == lc.PAYROLL_AUTHORITY_ENTERED
        assert two["payroll_authority_source"] == lc.PAYROLL_AUTHORITY_MEASURED


class TestNeverASilentDeletion:
    """The owner's ruling is CONDITIONAL — *"only use that if the store is not running payroll"*."""

    def test_an_unmeasured_store_keeps_its_typed_row(self):
        plan = lc.measured_fill(
            sheet(("Diversey", "Employee Salaries", 6033.26),
                  ("Closed Store", "Employee Salaries", 4100.00)),
            {"Diversey": 12555.70}, NAMES, grain="store")
        assert "Closed Store" not in plan["superseded"]
        assert plan["superseded"] == {"Diversey": 6033.26}

    def test_a_measured_zero_is_not_evidence_the_store_was_paid(self):
        plan = lc.measured_fill(sheet(("Diversey", "Employee Salaries", 6033.26)),
                                {"Diversey": 0.0}, NAMES, grain="store")
        assert plan["superseded"] == {}

    def test_nothing_measured_supersedes_nothing_and_says_so(self):
        plan = lc.measured_fill(sheet(("Diversey", "Employee Salaries", 6033.26)),
                                None, NAMES, grain="store")
        assert plan["superseded"] == {} and plan["superseded_amount"] is None


class TestTheDollarsReportedAreTheDollarsDropped:
    """One reader deciding and the other summing the sheet is the §ACCOUNT-03 defect itself."""

    ALLOC = sheet(("Diversey", "Employee Salaries", 6033.26),
                  ("Belmont", "Employee Salaries", 166.50),
                  ("Diversey", "DM Salaries", 1275.00),
                  ("Belmont", "DM Salaries", 1275.00),
                  ("Irving Park", "DM Salaries", 1275.00),
                  ("Narragansett", "DM Salaries", 1275.00))

    def plan(self):
        return lc.measured_fill(self.ALLOC, {"Diversey": 12555.70, "Belmont": 7097.48},
                                NAMES, grain="store")

    def test_a_flat_allocation_measures_no_stores_own_staff_so_it_keeps_booking(self):
        plan = self.plan()
        assert "dm salaries" in plan["allocated_excluded"]
        assert "dm salaries" not in plan["superseded_names"]
        assert plan["superseded_amount"] == 6199.76

    def test_the_sheet_reader_drops_exactly_what_the_plan_reported(self):
        plan = self.plan()
        kept = la.without_superseded(self.ALLOC, plan)
        dropped = round(sum(r["amount"] for r in self.ALLOC)
                        - sum(r["amount"] for r in kept), 2)
        assert dropped == plan["superseded_amount"]

    def test_the_allocation_survives_the_sheet_reader(self):
        kept = la.without_superseded(self.ALLOC, self.plan())
        assert len([r for r in kept if r["expense_name"] == "DM Salaries"]) == 4

    def test_a_store_the_report_never_measured_keeps_its_row(self):
        kept = la.without_superseded(self.ALLOC, self.plan())
        assert any(r["store_code"] == "Irving Park" for r in kept)
