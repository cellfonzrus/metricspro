"""Integration: ONE labour authority, reached by the statement and by the Gross Profit report.

Owner report 2026-11-14 (Sanjot, verbatim): *"check Gross profit report it shows different numbers,
we have spoken about this a lot of times if the source of data is the same why are th numbers
different , this is conflicting and must be fixed in teh code itself"* — and, the same hour, the
ruling that settles which figure is right: *"the luxelink salary is updated everyweek by the Dms and
that is the approved salary you should be counting towards the expenses"*.

The unit tests and `harness_labour_coverage.py` §K prove the DECISION. These prove the WIRING, over
the real `coa.build_inputs` and the real `labour_actuals` plumbing the GP report calls (house
database double, no network): that the statement no longer carries its own copy of the rule, that
the GP report's plan and the statement's booking are the SAME answer from the same rows, and that
each tenant's own payroll vocabulary — never another's — decides its fill.
"""
import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
PERIOD = "October 2026"

RENT = {"expense_name": "Rent / Lease", "expense_type": "Fixed", "amount": 3000.00,
        "source_key": None}
# LuxeLink charges `DM Salaries` at one identical amount to every store, so it is an ALLOCATION and
# measures nobody's own staff (§4e). Live October 2026: `Nostrand` carried only this.
DM = {"expense_name": "DM Salaries", "expense_type": "Fixed", "amount": 1275.00,
      "source_key": None}


def _shifts(org, code, employee, days=20, hours=8.0):
    return [{"id": f"{org}-{code}-{i}", "org_id": org, "employee_id": employee,
             "store_code": code, "scheduled_hours": hours, "actual_hours": hours,
             "shift_date": "2026-10-%02d" % (i + 1), "is_deleted": False}
            for i in range(days)]


@pytest.fixture
def books(fake_supabase):
    """Three stores that make every case visible, on a tenant whose vocabulary names BOTH an
    employee-salary row and a flat DM allocation — the live LuxeLink shape:

      `Belmont`   an approved `Employee Salaries` figure  -> its hours must NOT be added
      `Nostrand`  ONLY the estate-wide `DM Salaries` row  -> its hours MUST still be added
      `Irving`    no payroll row at all                   -> its hours MUST still be added
    """
    db = fake_supabase
    db.seed("storeops.stores", [
        {"id": "s1", "org_id": OTHER, "store_code": "Belmont",
         "store_address": "5601 W Belmont Ave", "address": "5601 W Belmont Ave",
         "is_active": True},
        {"id": "s2", "org_id": OTHER, "store_code": "Nostrand",
         "store_address": "3560 Nostrand Avenue", "address": "3560 Nostrand Avenue",
         "is_active": True},
        {"id": "s3", "org_id": OTHER, "store_code": "Irving",
         "store_address": "6500 W Irving Park Rd", "address": "6500 W Irving Park Rd",
         "is_active": True},
        {"id": "s4", "org_id": HOUSE, "store_code": "HOUSE-1",
         "store_address": "1 House St", "address": "1 House St", "is_active": True}])
    db.seed("storeops.employees", [
        {"id": "e1", "org_id": OTHER, "employee_id": "E1", "name": "A",
         "pay_rate": 25.0, "home_store": "Belmont", "is_active": True},
        {"id": "e2", "org_id": OTHER, "employee_id": "E2", "name": "B",
         "pay_rate": 20.0, "home_store": "Nostrand", "is_active": True},
        {"id": "e3", "org_id": OTHER, "employee_id": "E3", "name": "C",
         "pay_rate": 15.0, "home_store": "Irving", "is_active": True},
        {"id": "e4", "org_id": HOUSE, "employee_id": "E9", "name": "H",
         "pay_rate": 20.0, "home_store": "HOUSE-1", "is_active": True}])
    db.seed("storeops.shifts",
            _shifts(OTHER, "Belmont", "E1") + _shifts(OTHER, "Nostrand", "E2")
            + _shifts(OTHER, "Irving", "E3") + _shifts(HOUSE, "HOUSE-1", "E9"))
    db.seed("commcalc.store_expenses", [
        {"id": "x1", "org_id": OTHER, "period": PERIOD, "store_code": "Belmont",
         "expense_name": "Employee Salaries", "expense_type": "Fixed", "amount": 166.50,
         "source_key": None},
        {"id": "x2", "org_id": OTHER, "period": PERIOD, "store_code": "Belmont", **DM},
        {"id": "x3", "org_id": OTHER, "period": PERIOD, "store_code": "Nostrand", **DM},
        {"id": "x4", "org_id": OTHER, "period": PERIOD, "store_code": "Irving", **DM},
        {"id": "x5", "org_id": OTHER, "period": PERIOD, "store_code": "Irving", **RENT},
        # The house tenant spells its payroll row differently and carries no DM allocation.
        {"id": "x6", "org_id": HOUSE, "period": PERIOD, "store_code": "HOUSE-1",
         "expense_name": "Staff Wages", "expense_type": "Fixed", "amount": 100.00,
         "source_key": None},
        {"id": "x7", "org_id": HOUSE, "period": PERIOD, "store_code": "HOUSE-1", **RENT}])
    # The P&L books by canonical store ADDRESS, resolved through `commcalc.store_mapping` — the
    # same declaration the GP report keys its codes from. Seeding it is what makes this a wiring
    # test: the fill decision has to survive the fold between the sheet's codes and that space.
    db.seed("commcalc.store_mapping", [
        {"id": "m1", "org_id": OTHER, "store_code": "Belmont",
         "store_address": "5601 W Belmont Ave", "is_active": True},
        {"id": "m2", "org_id": OTHER, "store_code": "Nostrand",
         "store_address": "3560 Nostrand Avenue", "is_active": True},
        {"id": "m3", "org_id": OTHER, "store_code": "Irving",
         "store_address": "6500 W Irving Park Rd", "is_active": True},
        {"id": "m4", "org_id": HOUSE, "store_code": "HOUSE-1",
         "store_address": "1 House St", "is_active": True}])
    db.seed("commcalc.account_config", [
        {"id": "c1", "org_id": OTHER,
         "payroll_expense_names": ["DM Salaries", "Employee Salaries"],
         "payroll_authority_grain": "store"},
        {"id": "c2", "org_id": HOUSE, "payroll_expense_names": ["Staff Wages"],
         "payroll_authority_grain": "store"}])
    return db


def _wages(db, org, period=PERIOD):
    from app.modules.account import coa
    return coa.build_inputs(db, org, period)["wages"]["by_store"]


# 20 days x 8h: Belmont $25 -> $4,000.00 · Nostrand $20 -> $3,200.00 · Irving $15 -> $2,400.00
BELMONT, NOSTRAND, IRVING = "5601 W Belmont Ave", "3560 Nostrand Avenue", "6500 W Irving Park Rd"


class TestTheStatement:
    def test_an_approved_figure_is_what_books_and_the_hours_are_not_added_on_top(self, books):
        """The owner's ruling, as the statement books it: the DM-approved figure wins."""
        w = _wages(books, OTHER)
        assert w[BELMONT] == 1441.50              # 166.50 entered + 1275.00 allocation, no hours

    def test_a_store_with_only_the_flat_allocation_still_books_its_own_labour(self, books):
        """THE MONEY MOVE this change makes, asserted so it can never happen in silence. A $1,275.00
        allocation charged identically to every store is not a measurement of Nostrand's staff, so
        its hours still fill in — otherwise the statement reads as though it paid nobody."""
        assert _wages(books, OTHER)[NOSTRAND] == 1275.00 + 3200.00

    def test_a_store_with_no_payroll_row_at_all_keeps_its_labour_cost(self, books):
        assert _wages(books, OTHER)[IRVING] == 1275.00 + 2400.00

    def test_the_statement_keeps_no_copy_of_the_authority_rule(self):
        """The lock: the inline set this reader used to build (`payroll_auth_stores`) is gone, so
        there is nothing here that can drift from `measured_fill` again."""
        import inspect
        from app.modules.account import coa
        src = inspect.getsource(coa.build_inputs)
        assert "payroll_auth_stores.add" not in src
        assert "measured_fill" in src

    def test_an_unmeasurable_period_fills_nothing_and_is_not_a_zero(self, books):
        """Clock evidence below the org's threshold REFUSES (live: the house org's October is
        62.94% against 85%). The entered figures still book; no store is filled at $0.00."""
        books.seed("storeops.shifts", [s for s in books.rows("storeops.shifts")
                                       if s["org_id"] != OTHER])
        w = _wages(books, OTHER)
        assert w[BELMONT] == 1441.50
        assert w.get(NOSTRAND) == 1275.00
        assert w.get(IRVING) == 1275.00


class TestEveryTenant:
    def test_each_tenant_is_measured_against_its_own_payroll_vocabulary(self, books):
        """Owner directive 2026-10-30. `Staff Wages` is the house org's word for payroll, so its
        store has an approved figure and books no hours."""
        assert _wages(books, HOUSE)["1 House St"] == 100.00

    def test_one_tenants_payroll_word_never_decides_anothers_fill(self, books):
        """`Employee Salaries` is not in the house org's vocabulary. Seeding one there must confer
        no authority — and the house DEFAULT must not be able to answer in its place either."""
        books.seed("commcalc.account_config", [
            r for r in books.rows("commcalc.account_config") if r["org_id"] != HOUSE]
            + [{"id": "c2", "org_id": HOUSE, "payroll_expense_names": ["Staff Wages"],
                "payroll_authority_grain": "store"}])
        books.seed("commcalc.store_expenses", books.rows("commcalc.store_expenses") + [
            {"id": "x8", "org_id": HOUSE, "period": PERIOD, "store_code": "HOUSE-1",
             "expense_name": "Employee Salaries", "expense_type": "Fixed", "amount": 9999.00,
             "source_key": None}])
        # The 9,999.00 row is ordinary opex here, and the $100.00 `Staff Wages` row is still the
        # approved figure — so the store books its entered payroll and no hours, as before.
        assert _wages(books, HOUSE)["1 House St"] == 100.00

    def test_a_tenant_with_no_config_row_is_decided_by_the_HOUSE_vocabulary_only(self, books):
        """NY LOGISTICS has no `account_config` row at all, so `labour_vocabulary` resolves to the
        house names. The decision then uses THOSE and nothing else: `Employee Salaries` is a house
        name and still confers authority, while `DM Salaries` — this tenant's own word, and not
        declared anywhere now — confers none, so those two stores keep their hours. What must never
        happen is a word from ANOTHER tenant's row deciding it."""
        books.seed("commcalc.account_config",
                   [r for r in books.rows("commcalc.account_config") if r["org_id"] != OTHER])
        w = _wages(books, OTHER)
        assert w[BELMONT] == 166.50                 # approved under a house name -> no hours added
        assert [w[NOSTRAND], w[IRVING]] == [3200.00, 2400.00]    # allocation only -> hours fill

    def test_an_org_grain_tenant_suppresses_every_store_from_one_approved_row(self, books):
        """Vzone and the house org are `payroll_authority_grain='org'`. One approved row anywhere
        suppresses every store's measurement — byte-identical to every statement before mig 994."""
        books.seed("commcalc.account_config", [
            r for r in books.rows("commcalc.account_config") if r["org_id"] != OTHER]
            + [{"id": "c1", "org_id": OTHER,
                "payroll_expense_names": ["DM Salaries", "Employee Salaries"],
                "payroll_authority_grain": "org"}])
        w = _wages(books, OTHER)
        assert [w[BELMONT], w[NOSTRAND], w[IRVING]] == [1441.50, 1275.00, 1275.00]


class TestBothReportsAnswerTheSame:
    def test_the_gp_reports_plan_is_the_statements_booking(self, books):
        """The defect, stated as a test. The GP report sums the Expenses sheet — which HOLDS the
        approved rows — and then adds the month's measured labour. The only thing stopping it from
        adding it twice is that it asks the SAME function the statement books from."""
        from app.modules.account import coa
        from app.modules.commcalc import labour_actuals as _lact
        from app.modules.commcalc import labour_coverage as _lcov
        rows = [r for r in books.rows("commcalc.store_expenses") if r["org_id"] == OTHER]
        names = ["DM Salaries", "Employee Salaries"]
        measured = coa.wages_by_store(books, OTHER, PERIOD)
        plan = _lcov.measured_fill(rows, measured, names, grain="store",
                                   key_of=lambda c: {"Belmont": BELMONT, "Nostrand": NOSTRAND,
                                                     "Irving": IRVING}.get(c, c))
        assert plan["fill"] == {NOSTRAND: 3200.00, IRVING: 2400.00}
        assert plan["suppressed"] == [BELMONT]
        # …and the statement booked exactly that, on top of the entered rows.
        w = _wages(books, OTHER)
        assert w[NOSTRAND] - 1275.00 == plan["fill"][NOSTRAND]
        assert w[IRVING] - 1275.00 == plan["fill"][IRVING]
        assert w[BELMONT] == 1441.50
        # The GP report reaches it through `as_expense_rows`, which cannot be called without
        # declaring the rows it is already summing — `booked_rows` is positional and required —
        # and which folds BOTH sides into canonical space (`key_canon`) before deciding, then
        # relabels what survives into the sheet's code space (`key_as`).
        _canon = {"Belmont": BELMONT, "Nostrand": NOSTRAND, "Irving": IRVING}
        _code = {v: k for k, v in _canon.items()}
        emitted = _lact.as_expense_rows({"halves": [_lact.SALARY], "measured": {_lact.SALARY: True},
                                         "by_half_store": {_lact.SALARY: measured}},
                                        {"payroll_names": names, "grain": "store"}, rows,
                                        halves=[_lact.SALARY],
                                        key_canon=lambda c: _canon.get(c, c),
                                        key_as=lambda a: _code.get(a, a))
        assert sorted((r["store_code"], r["amount"]) for r in emitted) == \
            [("Irving", 2400.00), ("Nostrand", 3200.00)]

    def test_without_the_canonical_fold_the_same_rows_double_book(self, books):
        """Pins the MECHANISM, so a future change cannot quietly drop the fold: with the sheet in
        code space and the measurement in canonical space, Belmont's approved figure suppresses
        nothing and its $4,000.00 of hours books on top of it — the live defect exactly."""
        from app.modules.account import coa
        from app.modules.commcalc import labour_actuals as _lact
        rows = [r for r in books.rows("commcalc.store_expenses") if r["org_id"] == OTHER]
        measured = coa.wages_by_store(books, OTHER, PERIOD)
        emitted = _lact.as_expense_rows({"halves": [_lact.SALARY], "measured": {_lact.SALARY: True},
                                         "by_half_store": {_lact.SALARY: measured}},
                                        {"payroll_names": ["DM Salaries", "Employee Salaries"],
                                         "grain": "store"}, rows, halves=[_lact.SALARY])
        assert (BELMONT, 4000.00) in [(r["store_code"], r["amount"]) for r in emitted]

    def test_a_reader_cannot_add_measured_labour_without_saying_what_it_already_sums(self):
        """The structural lock. `booked_rows` is a REQUIRED POSITIONAL argument, so a future caller
        cannot materialise measured salary rows beside an expense sheet it never declared — which is
        precisely how the GP report came to double-book a month of labour."""
        import inspect
        from app.modules.commcalc import labour_actuals as _lact
        sig = inspect.signature(_lact.as_expense_rows)
        p = sig.parameters["booked_rows"]
        assert p.default is inspect.Parameter.empty
        assert p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        with pytest.raises(TypeError):
            _lact.as_expense_rows({}, {})
