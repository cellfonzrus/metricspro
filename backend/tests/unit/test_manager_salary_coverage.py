"""Unit: a manager's salary divides over the stores they COVER (owner 2026-11-16, index §STOREOPS-11).

Owner, verbatim: *"brendas salary is attached to diversey nbut her salary should be divided among the
store she is handling"*. The even split is his own 2026-09-09 directive: *"if th market manager is
78000 and he handles all markets then his salary will be 78000/ the number of stores in each
market"*.

`harness_manager_coverage_allocation.py` locks the rule and the wiring. These tests pin what a
harness is worst at: that coverage comes from an ASSIGNMENT and never from a placement, and that one
tenant's org chart can never answer another tenant's question (owner directive 2026-10-30).
"""
import pytest

from app.modules.storeops import payroll_salary as ps
from app.modules.storeops import overhead_allocation as oh

CHI = ["Armitage", "Belmont", "Cermark", "Cicero", "Diversey",
       "Grand", "lawrence", "Narragansett", "s3352", "s3735"]
NY = ["Ave U", "Lefferts", "Nostrand", "QV", "s7812"]


def ctx(codes, unit, managed, extra_units=()):
    units = {unit: {"id": unit, "name": unit, "code": "mkt:" + unit.lower()}}
    for u in extra_units:
        units[u] = {"id": u, "name": u, "code": "mkt:" + u.lower()}
    return {"ok": True,
            "stores": [{"store_code": c, "org_unit_id": unit} for c in codes],
            "org_codes": list(codes), "market_by_code": {}, "codes_by_market": {},
            "unit_by_id": units, "managed_by_emp": dict(managed),
            "subtree": (lambda uid: {uid} if uid in units else set())}


CFG = oh.resolve_config(None)


class TestTheOwnersArithmetic:
    def test_a_covered_manager_is_divided_not_parked(self):
        alloc = ps.allocate_across_stores({}, 5280.23, "Diversey", covered=CHI)
        assert sorted(alloc) == sorted(CHI)
        assert round(sum(alloc.values()), 2) == 5280.23
        assert alloc["Diversey"] < 600.0

    def test_the_directives_own_example(self):
        alloc = ps.allocate_across_stores({}, 78000.0 / 12, "Grand", covered=CHI)
        assert sorted(alloc.values())[0] == 650.0

    def test_hours_do_not_decide_a_managers_store(self):
        worked = ps.allocate_across_stores({"Grand": 200.0}, 5280.23, "Diversey", covered=CHI)
        idle = ps.allocate_across_stores({}, 5280.23, "Diversey", covered=CHI)
        assert worked == idle

    def test_a_rep_is_still_paid_where_they_worked(self):
        hrs = {"Cicero": 115.04, "Grand": 79.5}
        assert (ps.allocate_across_stores(hrs, 3832.42, "Diversey")
                == ps.allocate_across_stores(hrs, 3832.42, "Diversey", covered=[]))


class TestCoverageIsAnAssignmentNotAPlacement:
    """The defect measured before this shipped: three salaried STORE staff carry the market node's
    `org_unit_id`, so reading placement as coverage spreads ordinary store pay estate-wide."""

    def setup_method(self):
        self.ctx = ctx(CHI, "Chi", {"MGR": ["Chi"]})

    def test_a_placed_store_employee_covers_nothing(self):
        codes, how = oh.coverage_for(self.ctx, {"employee_id": "STAFF", "home_store": "Grand",
                                                "org_unit_id": "Chi"}, CFG,
                                     seed_from_placement=False)
        assert codes == [] and how == oh.COVER_NONE

    def test_an_assigned_manager_covers_the_unit_beneath_them(self):
        codes, how = oh.coverage_for(self.ctx, {"employee_id": "MGR"}, CFG,
                                     seed_from_placement=False)
        assert sorted(codes) == sorted(CHI)
        assert how != oh.COVER_NONE

    def test_the_unattached_path_keeps_reading_placement(self):
        codes, _ = oh.coverage_for(self.ctx, {"employee_id": "STAFF", "org_unit_id": "Chi"}, CFG,
                                   seed_from_placement=True)
        assert sorted(codes) == sorted(CHI)

    def test_an_org_that_assigned_nobody_is_inert(self):
        bare = ctx(CHI, "Chi", {})
        assert oh.coverage_for(bare, {"employee_id": "MGR", "org_unit_id": "Chi"}, CFG,
                               seed_from_placement=False) == ([], oh.COVER_NONE)


class TestOneTenantsChartNeverAnswersAnother:
    """Owner directive 2026-10-30 — every feature for every tenant, with the cross-tenant tests
    shipped beside it."""

    def test_the_same_employee_id_resolves_to_each_orgs_own_stores(self):
        a = ctx(CHI, "Chi", {"E1": ["Chi"]})
        b = ctx(NY, "NY", {"E1": ["NY"]})
        assert sorted(oh.coverage_for(a, {"employee_id": "E1"}, CFG,
                                      seed_from_placement=False)[0]) == sorted(CHI)
        assert sorted(oh.coverage_for(b, {"employee_id": "E1"}, CFG,
                                      seed_from_placement=False)[0]) == sorted(NY)

    def test_reading_one_org_does_not_change_the_next(self):
        a = ctx(CHI, "Chi", {"E1": ["Chi"]})
        b = ctx(NY, "NY", {})
        oh.coverage_for(a, {"employee_id": "E1"}, CFG, seed_from_placement=False)
        assert oh.coverage_for(b, {"employee_id": "E1"}, CFG,
                              seed_from_placement=False) == ([], oh.COVER_NONE)
        assert sorted(oh.coverage_for(a, {"employee_id": "E1"}, CFG,
                                      seed_from_placement=False)[0]) == sorted(CHI)

    def test_a_covered_set_never_holds_a_store_the_org_does_not_have(self):
        a = ctx(CHI, "Chi", {"E1": ["Chi"]})
        codes, _ = oh.coverage_for(a, {"employee_id": "E1"}, CFG, seed_from_placement=False)
        assert all(c in a["org_codes"] for c in codes)
        assert not set(codes) & set(NY)

    @pytest.mark.parametrize("n", [2, 3, 7, 10, 24])
    def test_every_estate_size_foots_to_the_cent(self, n):
        codes = [f"S{i}" for i in range(n)]
        alloc = ps.allocate_across_stores({}, 5280.23, "S0", covered=codes)
        assert len(alloc) == n
        assert round(sum(alloc.values()), 2) == 5280.23


class TestTheTwoSpreadPathsStayDisjoint:
    """A person both the Payroll Report and overhead_allocation would spread is one salary booked
    twice. The populations are disjoint on `home_store`."""

    MEQ = staticmethod(lambda basis, amount: 6500.0)

    def test_the_overhead_population_is_the_pay_that_lands_nowhere(self):
        cfg = oh.resolve_config({"mode": "derive"})
        is_oh, why, _ = oh.classify_employee(
            {"employee_id": "M", "home_store": "", "is_active": True,
             "pay_basis": "annual", "pay_amount": 78000.0}, cfg, self.MEQ)
        assert is_oh and why == oh.WHY_UNATTACHED

    def test_a_store_attached_manager_belongs_to_the_pay_path_alone(self):
        cfg = oh.resolve_config({"mode": "derive"})
        assert oh.classify_employee(
            {"employee_id": "M", "home_store": "Diversey", "is_active": True,
             "pay_basis": "annual", "pay_amount": 62000.0}, cfg, self.MEQ)[0] is False
