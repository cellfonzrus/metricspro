"""Integration: the P&L's salary line says when its figure is a STALE COPY of the measurement.

Owner report 2026-11-12 (Sanjot): *"in the accoutn module i diod the compute but the expenses are
not  showing correctly , they shoudl include the salaries"*. LuxeLink's October 2026 statement
booked $54,044.69 of salary against $105,109.69 the platform could measure from the same month's
hours — the Expenses sheet had COPIED a payroll figure into an `Employee Salaries` row per store,
and a payroll row confers authority, which SUPPRESSES the measurement. The copy then froze.

These tests drive the REAL `coa.build_inputs` over seeded rows (the house database double, no
network), because the unit tests prove the comparison and this proves the WIRING: that the figure
reaches the line the owner reads, in the key space the P&L books in, for every tenant, and that
wiring it moves NO money.
"""
import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
PERIOD = "October 2026"

# The month's own expense rows must exist or `expenses_effective` carries another month's and the
# comparison correctly refuses to run (§COMMCALC-23 owns that case; `test_a_carried_month…` below).
RENT = {"expense_name": "Rent / Lease", "expense_type": "Fixed", "amount": 3000.00,
        "source_key": None}


def _shifts(org, code, employee="E1", days=20, hours=8.0):
    return [{"id": f"{org}-{code}-{i}", "org_id": org, "employee_id": employee,
             "store_code": code, "scheduled_hours": hours, "actual_hours": hours,
             "shift_date": "2026-10-%02d" % (i + 1), "is_deleted": False}
            for i in range(days)]


@pytest.fixture
def books(monkeypatch, fake_supabase):
    """Two tenants, each with its OWN payroll vocabulary — the multi-tenant rule (§COMMCALC-49.10).
    Both have one store whose entered salary is a fraction of the hours it worked."""
    from app.modules.account import coa
    for mod, attr in (("app.modules.account.coa", "sb"),):
        if hasattr(coa, attr):
            monkeypatch.setattr(f"{mod}.{attr}", lambda: fake_supabase, raising=False)
    fake_supabase.seed("storeops.stores", [
        {"id": "s1", "org_id": HOUSE, "store_code": "HOUSE-1",
         "store_address": "1 House St", "address": "1 House St", "is_active": True},
        {"id": "s2", "org_id": OTHER, "store_code": "Belmont",
         "store_address": "5601 W Belmont Ave", "address": "5601 W Belmont Ave",
         "is_active": True}])
    fake_supabase.seed("storeops.employees", [
        {"id": "e1", "org_id": HOUSE, "employee_id": "E1", "name": "House Rep",
         "pay_rate": 20.0, "home_store": "HOUSE-1", "is_active": True},
        {"id": "e2", "org_id": OTHER, "employee_id": "E1", "name": "Other Rep",
         "pay_rate": 25.0, "home_store": "Belmont", "is_active": True}])
    fake_supabase.seed("storeops.shifts",
                       _shifts(HOUSE, "HOUSE-1") + _shifts(OTHER, "Belmont"))
    fake_supabase.seed("commcalc.store_expenses", [
        # The house tenant calls its payroll row one thing; the other tenant calls it another.
        {"id": "x1", "org_id": HOUSE, "period": PERIOD, "store_code": "HOUSE-1",
         "expense_name": "Staff Wages", "expense_type": "Fixed", "amount": 100.00,
         "source_key": None},
        {"id": "x2", "org_id": HOUSE, "period": PERIOD, "store_code": "HOUSE-1", **RENT},
        {"id": "x3", "org_id": OTHER, "period": PERIOD, "store_code": "Belmont",
         "expense_name": "Employee Salaries", "expense_type": "Fixed", "amount": 166.50,
         "source_key": None},
        {"id": "x4", "org_id": OTHER, "period": PERIOD, "store_code": "Belmont", **RENT}])
    fake_supabase.seed("commcalc.account_config", [
        {"id": "c1", "org_id": HOUSE, "payroll_expense_names": ["Staff Wages"],
         "payroll_authority_grain": "store"},
        {"id": "c2", "org_id": OTHER, "payroll_expense_names": ["Employee Salaries"],
         "payroll_authority_grain": "store"}])
    return fake_supabase


def _wages(db, org):
    from app.modules.account import coa
    return coa.build_inputs(db, org, PERIOD)["wages"]


def _booked(line):
    return round(sum(line["by_store"].values()) + (line.get("company_wide") or 0.0), 2)


def test_the_line_says_the_entered_figure_is_below_the_measurement(books):
    """20 days x 8h x $25.00 = $4,000.00 measured against $166.50 entered."""
    note = _wages(books, OTHER).get("note") or ""
    assert "LESS than the hours and punches measure" in note
    assert "$3,833.50" in note


def test_it_works_for_the_other_tenants_own_vocabulary_too(books):
    """A feature is not finished until it works for EVERY tenant, measured against that tenant's
    own declarations (owner directive 2026-10-30). 20 x 8h x $20.00 = $3,200.00 vs $100.00."""
    note = _wages(books, HOUSE).get("note") or ""
    assert "LESS than the hours and punches measure" in note
    assert "$3,100.00" in note


def test_one_tenants_payroll_words_never_answer_for_another(books):
    """`Employee Salaries` is not in the house org's vocabulary and must not be read as payroll
    there — and the house default must not be able to answer in its place."""
    books.seed("commcalc.store_expenses", books.rows("commcalc.store_expenses") + [
        {"id": "x5", "org_id": HOUSE, "period": PERIOD, "store_code": "HOUSE-1",
         "expense_name": "Employee Salaries", "expense_type": "Fixed", "amount": 9999.00,
         "source_key": None}])
    note = _wages(books, HOUSE).get("note") or ""
    assert "$9,999.00" not in note
    assert "$3,100.00" in note          # still measured against ITS OWN row


def test_wiring_the_check_in_moves_no_money(books):
    """DISPLAY-ONLY. The $166.50 entered row still books, by itself, exactly as before — the
    $4,000.00 measurement it suppresses is still NOT added. Which route should win is the owner's
    call (it moves money) and is deliberately NOT taken here."""
    line = _wages(books, OTHER)
    assert _booked(line) == 166.50
    assert line["by_store"] == {"Belmont": 166.50}


def test_a_store_with_no_measurement_is_not_reported_as_overstated(books):
    """Belmont keeps its entered figure and loses its hours: the fault is missing clock data, and
    an owner must not be sent hunting a code defect by a $166.50 "overstated"."""
    books.seed("storeops.shifts", [s for s in books.rows("storeops.shifts")
                                   if s["org_id"] != OTHER])
    note = _wages(books, OTHER).get("note") or ""
    assert "MORE than the hours and punches measure" not in note


def test_a_carried_month_is_not_reported_as_a_stale_entry(books):
    """November has no rows of its own, so it shows October's — which `coverage_note` already says
    in full. A second sentence calling each carried row stale would send the owner to re-enter a
    figure the carry rule replaces anyway (§COMMCALC-23)."""
    from app.modules.account import coa
    note = coa.build_inputs(books, OTHER, "November 2026")["wages"].get("note") or ""
    assert "carried forward" in note
    assert "LESS than the hours and punches measure" not in note
