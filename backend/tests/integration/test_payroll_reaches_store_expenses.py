"""Integration: the Payroll Report's per-store cost REACHES the store expenses, for every tenant.

Owner report 2026-11-16, verbatim: *"The payroll for luxelink is being calculated properly but it is
not reflecting per store expenses in dashboard, gross profit or p&l, they should all be updated from
here and it is quick fix but hasnt been happening, if the agent cannot do it assign somebody senior
to fix the platform wide issue not for just this tenant"*.

Measured live the same day, October 2026: `GET /storeops/payroll-by-store` said $121,632.66 for
LuxeLink and the books carried $116,826.26; the house org's report said $72,905.30 and its books said
$0.00; Vzone and NY LOGISTICS differed too. EVERY store of EVERY tenant. The cause was a second
derivation inside `account/coa`, now deleted (§ACCOUNT-05): the one home is
`storeops/labour_cost` (§STOREOPS-10), the door onto the producer the report publishes.

These prove the WIRING over the real `coa.wages_by_store` / `wages_by_code` / `build_inputs` against
a database double — that the figure the books carry IS the producer's, store for store and cent for
cent, on TWO tenants of different shapes, and that neither tenant can read the other's.
"""
import pytest

from app.modules.account import coa
from app.modules.storeops import payroll_salary as ps

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
PERIOD = "2026-10"


def _shifts(org, code, eid, days=4, hours=8.0, start=1):
    return [{"id": f"{org}-{code}-{eid}-{i}", "org_id": org, "employee_id": eid,
             "store_code": code, "scheduled_hours": hours, "actual_hours": hours,
             "shift_date": "2026-10-%02d" % (start + i), "is_deleted": False}
            for i in range(days)]


@pytest.fixture
def books(fake_supabase, monkeypatch):
    """Two tenants of deliberately different shapes.

    HOUSE   two stores, hourly only — the plain case.
    OTHER   an hourly rep, a SALARIED rep splitting hours across two stores, and a salaried manager
            with no home store at all (the live LuxeLink shape: $78,000 a year, no store).
    """
    db = fake_supabase
    db.seed("commcalc.store_mapping", [
        {"org_id": HOUSE, "store_code": "H1", "store_address": "1 House St"},
        {"org_id": HOUSE, "store_code": "H2", "store_address": "2 House St"},
        {"org_id": OTHER, "store_code": "Belmont", "store_address": "5601 W Belmont Ave"},
        {"org_id": OTHER, "store_code": "Diversey", "store_address": "4640-A W Diversey Ave"}])
    db.seed("storeops.stores", [
        {"id": "s1", "org_id": HOUSE, "store_code": "H1", "address": "1 House St", "is_active": True},
        {"id": "s2", "org_id": HOUSE, "store_code": "H2", "address": "2 House St", "is_active": True},
        {"id": "s3", "org_id": OTHER, "store_code": "Belmont", "address": "5601 W Belmont Ave", "is_active": True},
        {"id": "s4", "org_id": OTHER, "store_code": "Diversey", "address": "4640-A W Diversey Ave", "is_active": True}])
    db.seed("storeops.employees", [
        {"id": "h1", "org_id": HOUSE, "employee_id": "HE1", "name": "House Rep",
         "pay_rate": 20.0, "home_store": "H1", "is_active": True},
        {"id": "h2", "org_id": HOUSE, "employee_id": "HE2", "name": "House Rep 2",
         "pay_rate": 25.0, "home_store": "H2", "is_active": True},
        {"id": "o1", "org_id": OTHER, "employee_id": "OE1", "name": "Hourly",
         "pay_rate": 15.0, "home_store": "Belmont", "is_active": True},
        {"id": "o2", "org_id": OTHER, "employee_id": "OE2", "name": "Salaried Floater",
         "pay_rate": 3692.3, "home_store": "Belmont", "is_active": True,
         "pay_basis": "annual", "pay_amount": 52000.0},
        {"id": "o3", "org_id": OTHER, "employee_id": "OE3", "name": "Market Manager",
         "pay_rate": 0.0, "home_store": None, "is_active": True,
         "pay_basis": "annual", "pay_amount": 78000.0}])
    db.seed("storeops.shifts",
            _shifts(HOUSE, "H1", "HE1") + _shifts(HOUSE, "H2", "HE2")
            + _shifts(OTHER, "Belmont", "OE1")
            + _shifts(OTHER, "Belmont", "OE2", days=3)
            + _shifts(OTHER, "Diversey", "OE2", days=1, start=10))
    db.seed("storeops.timelog", [])
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: db)
    return db


def reported(db, org):
    """The Payroll Report's own per-store figure, folded into the books' address space."""
    from app.modules.storeops import router as sr
    code2addr = coa.store_code_to_address(db, org)
    out = {}
    for r in sr.get_payroll_by_store(start="2026-10-01", end="2026-10-31", org_id=org,
                                     so=db.schema("storeops"))["stores"]:
        c = str(r["store_code"]).strip()
        key = None if c.casefold() in ("", ps.UNASSIGNED_STORE.casefold()) \
            else code2addr.get(coa._norm_store(c), coa._norm_store(c))
        out[key] = round(out.get(key, 0.0) + float(r["amount"]), 2)
    return out


class TestTheBooksCarryTheReportsFigure:

    @pytest.mark.parametrize("org", [HOUSE, OTHER])
    def test_wages_by_store_equals_the_payroll_report_store_for_store(self, books, org):
        assert coa.wages_by_store(books, org, PERIOD) == reported(books, org)

    @pytest.mark.parametrize("org", [HOUSE, OTHER])
    def test_the_two_key_spaces_carry_the_same_money(self, books, org):
        by_addr = coa.wages_by_store(books, org, PERIOD)
        by_code = coa.wages_by_code(books, org, PERIOD)
        assert round(sum(by_addr.values()), 2) == round(sum(by_code.values()), 2)

    def test_the_hourly_figure_is_hours_times_rate_at_the_store_worked(self, books):
        assert coa.wages_by_store(books, HOUSE, PERIOD) == {
            "1 House St": round(4 * 8 * 20.0, 2), "2 House St": round(4 * 8 * 25.0, 2)}

    def test_a_salaried_rep_books_salary_not_hours_times_rate(self, books):
        """32 hours at a $3,692.30 "rate" would be $118,153.60 — the live LuxeLink defect."""
        cells = coa.wages_by_store(books, OTHER, PERIOD)
        assert sum(cells.values()) < 20000.0

    def test_a_manager_with_no_store_books_company_wide_never_as_a_store(self, books):
        cells = coa.wages_by_store(books, OTHER, PERIOD)
        assert cells.get(None) and cells[None] > 0
        assert ps.UNASSIGNED_STORE not in cells
        assert not any(str(k).strip().casefold() == ps.UNASSIGNED_STORE.casefold()
                       for k in cells if k)

    def test_the_statement_books_what_the_report_says(self, books):
        """The whole ask: the P&L `wages` line, per store, IS the Payroll Report's figure."""
        inputs = coa.build_inputs(books, OTHER, PERIOD)
        booked = {k: v for k, v in inputs["wages"]["by_store"].items()}
        rep = {k: v for k, v in reported(books, OTHER).items() if k is not None}
        assert booked == rep
        assert round(inputs["wages"]["company_wide"], 2) == round(reported(books, OTHER)[None], 2)


class TestEveryTenant:
    """A feature is not finished until it works for every tenant, against that tenant's own rows."""

    def test_neither_tenants_hours_reach_the_other(self, books):
        house, other = coa.wages_by_store(books, HOUSE, PERIOD), coa.wages_by_store(books, OTHER, PERIOD)
        assert set(house) & set(k for k in other if k) == set()
        assert all(k in ("1 House St", "2 House St") for k in house)

    def test_an_org_with_no_roster_measures_nothing_rather_than_borrowing_one(self, books):
        assert coa.wages_by_store(books, "11111111-1111-1111-1111-111111111111", PERIOD) == {}

    def test_each_tenants_total_is_its_own_report(self, books):
        for org in (HOUSE, OTHER):
            assert round(sum(coa.wages_by_store(books, org, PERIOD).values()), 2) \
                == round(sum(reported(books, org).values()), 2)


class TestItIsReproducible:
    """A figure the books carry must answer the same twice — live, LuxeLink October answered
    $6,083.83 and $6,083.84 at `Cicero` on alternate runs of the same question."""

    def test_the_same_month_answers_the_same_figure(self, books):
        first = coa.wages_by_store(books, OTHER, PERIOD)
        for _ in range(4):
            assert coa.wages_by_store(books, OTHER, PERIOD) == first

    def test_the_allocation_does_not_depend_on_row_order(self):
        a = ps.allocate_across_stores({"A": 1.0, "B": 1.0, "C": 1.0}, 100.0, None)
        b = ps.allocate_across_stores({"C": 1.0, "B": 1.0, "A": 1.0}, 100.0, None)
        assert a == b and round(sum(a.values()), 2) == 100.0
