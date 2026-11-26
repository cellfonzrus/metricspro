"""Integration: the payroll-run workflow endpoints through the REAL app (index §STOREOPS-16).

Real routing, real middleware, the real endpoint bodies — over the in-memory database double, with
conftest blocking every outbound socket. "Integration" here means the app, not a real database
(docs/TESTING.md): there is no staging Supabase.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT:
  · the two routes are reachable at their registered paths, and neither writes anything — this
    surface cannot run payroll, it can only say whether a run may happen;
  · ★ each organisation's PAY PERIOD is its own: the same request returns a week for a weekly tenant
    and a fortnight for a fortnightly one, resolved through the home that already answers it;
  · ★ one tenant's stores, hours, approvals and run records never reach another tenant's board;
  · ★ step 7's MONEY rides `pay_visibility`: a caller who may not see pay is told whether the month
    ran, with the amounts withheld AND the withholding stated;
  · the hours and their basis are the ONE pay derivation (`get_payroll` in-process), so the board
    cannot disagree with the payroll report;
  · open-stores-only is the default and the number hidden is stated (§CORE-45);
  · a database missing a table degrades by name in `blockers` instead of 500-ing.
"""
import pytest

from app.modules.storeops import payroll_hours_basis as hb

ORG_A = "00000000-0000-0000-0000-000000000001"   # weekly
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"   # fortnightly
BOARD = "/api/v1/storeops/payroll-workflow"

LO, HI = "2026-11-16", "2026-11-22"

TENANTS = [{"org_id": ORG_A, "work_week_start_dow": 0, "pay_period_type": "weekly"},
           {"org_id": ORG_B, "work_week_start_dow": 0, "pay_period_type": "biweekly",
            "biweekly_anchor": "2026-11-09"}]

STORES = [
    {"org_id": ORG_A, "store_code": "B-117", "address": "1 High St", "market": "LI",
     "is_active": True},
    {"org_id": ORG_A, "store_code": "B-200", "address": "2 Low St", "market": "NYC",
     "is_active": True},
    {"org_id": ORG_A, "store_code": "B-999", "address": "3 Shut St", "market": "LI",
     "is_active": False},
    {"org_id": ORG_B, "store_code": "L-1", "address": "9 Other Rd", "market": "NJ",
     "is_active": True},
]

EMPLOYEES = [
    {"org_id": ORG_A, "employee_id": "E1", "name": "Ada", "home_store": "B-117",
     "is_active": True, "pay_rate": 17.0, "pay_basis": "hourly"},
    {"org_id": ORG_A, "employee_id": "E2", "name": "Ben", "home_store": "B-200",
     "is_active": True, "pay_rate": None, "pay_basis": "hourly"},
    {"org_id": ORG_B, "employee_id": "L9", "name": "Dara", "home_store": "L-1",
     "is_active": True, "pay_rate": 16.0, "pay_basis": "hourly"},
]

SHIFTS = [
    {"id": 1, "org_id": ORG_A, "store_code": "B-117", "employee_id": "E1", "employee_name": "Ada",
     "shift_date": "2026-11-17", "start_time": "09:00", "end_time": "17:00", "is_deleted": False},
    {"id": 2, "org_id": ORG_A, "store_code": "B-200", "employee_id": "E2", "employee_name": "Ben",
     "shift_date": "2026-11-18", "start_time": "09:00", "end_time": "17:00", "is_deleted": False},
    {"id": 3, "org_id": ORG_B, "store_code": "L-1", "employee_id": "L9", "employee_name": "Dara",
     "shift_date": "2026-11-17", "start_time": "09:00", "end_time": "17:00", "is_deleted": False},
]

PUNCHES = [
    {"id": 11, "org_id": ORG_A, "store_code": "B-117", "employee_id": "E1",
     "work_date": "2026-11-17", "clock_in": "2026-11-17T09:00:00Z",
     "clock_out": "2026-11-17T17:00:00Z", "hours": 8},
    # B-200's punch is still OPEN — somebody is clocked in with no clock-out.
    {"id": 12, "org_id": ORG_A, "store_code": "B-200", "employee_id": "E2",
     "work_date": "2026-11-18", "clock_in": "2026-11-18T09:00:00Z", "clock_out": None,
     "hours": 0},
]

APPROVALS = [{"org_id": ORG_A, "period_start": LO, "period_end": HI, "employee_id": "E1",
              "store_code": "B-117", "hours_source": 8, "hours_approved": None,
              "dm_status": "approved", "hr_status": "approved"}]

RUNS = [{"org_id": ORG_A, "period": "2026-11", "store": "B-117", "wages": 4000, "headcount": 1,
         "run_at": "2026-11-23T02:00:00Z", "run_by": "cron"}]
TAXES = [{"org_id": ORG_A, "period": "2026-11", "employee_id": "E1", "wages": 4000,
          "employer_total": 320}]
EXPENSES = [{"org_id": ORG_A, "period": "2026-11", "store_code": "B-117", "amount": 100,
             "category": "uniforms"}]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    for mod in ("app.modules.storeops.payroll_run_api",
                "app.modules.storeops.router",
                "app.modules.storeops.payroll_approval",
                "app.modules.storeops.clock_variance_api"):
        monkeypatch.setattr(f"{mod}.get_supabase", lambda: fake_supabase, raising=False)
    fake_supabase.seed("storeops.tenants", TENANTS)
    fake_supabase.seed("storeops.stores", STORES)
    fake_supabase.seed("storeops.employees", EMPLOYEES)
    fake_supabase.seed("storeops.shifts", SHIFTS)
    fake_supabase.seed("storeops.timelog", PUNCHES)
    fake_supabase.seed("storeops.payroll_approval", APPROVALS)
    fake_supabase.seed("storeops.payroll_gross_ledger", RUNS)
    fake_supabase.seed("storeops.payroll_tax_ledger", TAXES)
    fake_supabase.seed("storeops.payroll_expense_item", EXPENSES)
    return fake_supabase


def _board(api_client, org=ORG_A, start=LO, end=HI, **params):
    q = [f"org_id={org}"]
    if start and end:
        q += [f"start={start}", f"end={end}"]
    q += [f"{k}={v}" for k, v in params.items()]
    r = api_client.get(f"{BOARD}?{'&'.join(q)}")
    assert r.status_code == 200, r.text
    return r.json()


def _row(board, store_code):
    for r in board["rows"]:
        if r["store_code"] == store_code:
            return r
    raise AssertionError(f"{store_code} not on the board: "
                         f"{[r['store_code'] for r in board['rows']]}")


def _step(row, key):
    for s in row["steps"]:
        if s["key"] == key:
            return s
    raise AssertionError(f"no step {key} in {[s['key'] for s in row['steps']]}")


# ── the board is reachable and shaped ────────────────────────────────────────────────────────────
def test_the_board_lists_this_organisations_stores_with_a_resume_step_each(wired, api_client):
    b = _board(api_client)
    assert {r["store_code"] for r in b["rows"]} == {"B-117", "B-200"}
    for r in b["rows"]:
        assert r["status"] in {s["key"] for s in b["statuses"]}
        assert len(r["steps"]) == 7 and "resume" in r


def test_the_board_publishes_the_seven_checks_and_every_status_word(wired, api_client):
    b = _board(api_client)
    assert [s["key"] for s in b["steps"]] == ["punches", "basis", "exceptions", "approval",
                                              "rates", "preflight", "run"]
    assert [s["key"] for s in b["statuses"]] == ["not_started", "in_progress", "needs_input",
                                                 "skipped", "done"]


def test_the_board_publishes_every_basis_word_with_what_it_means(wired, api_client):
    b = _board(api_client)
    assert set(b["bases"]) == set(hb.ALL_BASES)
    assert b["bases"][hb.CLOCKED] == hb.basis_reason(hb.CLOCKED)


def test_the_period_the_board_reports_is_the_one_it_was_asked_for(wired, api_client):
    b = _board(api_client)
    assert (b["period_start"], b["period_end"]) == (LO, HI)
    assert b["months"] == ["2026-11"]


# ── ★ each organisation's own pay period ─────────────────────────────────────────────────────────
def test_with_no_dates_each_tenant_gets_its_own_declared_period_length(wired, api_client):
    a = _board(api_client, org=ORG_A, start=None, end=None)
    b = _board(api_client, org=ORG_B, start=None, end=None)
    from datetime import date
    def _len(x):
        return (date.fromisoformat(x["period_end"]) - date.fromisoformat(x["period_start"])).days
    assert _len(a) == 6, a["period_start"]           # a week
    assert _len(b) == 13, b["period_start"]          # a fortnight
    assert a["period_start"] != b["period_start"] or _len(a) != _len(b)


def test_a_period_straddling_a_month_end_names_both_months(wired, api_client):
    b = _board(api_client, start="2026-10-26", end="2026-11-08")
    assert b["months"] == ["2026-10", "2026-11"]


def test_an_end_before_the_start_is_refused_rather_than_guessed(wired, api_client):
    r = api_client.get(f"{BOARD}?org_id={ORG_A}&start=2026-11-22&end=2026-11-16")
    assert r.status_code == 400, r.text


# ── ★ one tenant's payroll never reaches another's board ─────────────────────────────────────────
def test_org_bs_stores_and_hours_are_absent_from_org_as_board(wired, api_client):
    a = _board(api_client, org=ORG_A)
    assert "L-1" not in {r["store_code"] for r in a["rows"]}


def test_org_as_approvals_and_run_records_are_absent_from_org_bs_board(wired, api_client):
    b = _board(api_client, org=ORG_B, start=LO, end=HI)
    assert {r["store_code"] for r in b["rows"]} == {"L-1"}
    row = _row(b, "L-1")
    assert _step(row, "approval")["status"] != "done"
    assert _step(row, "run")["status"] != "done"


# ── the steps answer over real seeded rows ───────────────────────────────────────────────────────
def test_an_open_punch_is_named_on_that_stores_row_only(wired, api_client):
    b = _board(api_client)
    assert _step(_row(b, "B-200"), "punches")["detail"]["open_punches"] == 1
    assert _step(_row(b, "B-117"), "punches")["detail"]["open_punches"] == 0


def test_somebody_with_no_pay_rate_is_named_where_this_run_would_pay_them(wired, api_client):
    b = _board(api_client)
    named = [r["store_code"] for r in b["rows"]
             if _step(r, "rates")["detail"]["employees_without_rate"]]
    assert named == ["B-200"], [(_row(b, c), c) for c in ("B-117", "B-200")][:0] or named


def test_both_sign_offs_settle_the_approval_step_for_the_store_that_has_them(wired, api_client):
    b = _board(api_client)
    assert _step(_row(b, "B-117"), "approval")["status"] == "done"
    assert _step(_row(b, "B-200"), "approval")["status"] == "needs_input"


def test_a_recorded_run_settles_the_last_step_for_that_store_only(wired, api_client):
    b = _board(api_client)
    assert _step(_row(b, "B-117"), "run")["status"] == "done"
    assert _step(_row(b, "B-200"), "run")["status"] == "needs_input"


def test_the_run_step_says_the_record_is_by_month_not_by_pay_period(wired, api_client):
    b = _board(api_client)
    assert "recorded by MONTH" in _step(_row(b, "B-117"), "run")["detail"]["grain_note"]


def test_the_preflight_step_carries_the_runs_own_wording(wired, api_client):
    b = _board(api_client)
    reason = _step(_row(b, "B-200"), "preflight")["detail"]["gate_reason"]
    assert "NO pay rate" in reason


def test_an_org_with_the_variance_gate_off_is_said_so_in_the_payload(wired, api_client):
    assert _board(api_client)["declared"]["gate_enabled"] is False


# ── ★ the money gate ────────────────────────────────────────────────────────────────────────────
def test_a_caller_who_may_see_pay_gets_the_amounts_the_run_pushed(wired, api_client):
    b = _board(api_client)
    assert b["can_see_pay"] is True
    detail = _step(_row(b, "B-117"), "run")["detail"]
    assert detail["wages"] == 4000.0 and detail["employer_tax"] == 320.0


def test_a_caller_who_may_not_see_pay_is_told_whether_it_ran_with_the_amounts_withheld(
        wired, monkeypatch, api_client):
    monkeypatch.setattr("app.modules.storeops.payroll_run_api._can_see_pay",
                        lambda authorization, org_id: False)
    b = _board(api_client)
    assert b["can_see_pay"] is False
    detail = _step(_row(b, "B-117"), "run")["detail"]
    assert "wages" not in detail and "employer_tax" not in detail and "expenses" not in detail
    assert "not shown to your role" in detail["money_withheld"]
    # …and the step's VERDICT is unchanged: whether the month ran is not pay.
    assert _step(_row(b, "B-117"), "run")["status"] == "done"


def test_the_board_states_the_pay_visibility_in_a_header_the_export_can_read(wired, api_client):
    r = api_client.get(f"{BOARD}?org_id={ORG_A}&start={LO}&end={HI}")
    assert r.headers.get("X-Can-See-Pay-Rates") in ("true", "false")
    assert r.headers.get("Cache-Control") == "no-store"


# ── the inactive rule and the standard filters ───────────────────────────────────────────────────
def test_a_closed_store_is_hidden_by_default_and_the_number_hidden_is_stated(wired, api_client):
    b = _board(api_client)
    assert "B-999" not in {r["store_code"] for r in b["rows"]}
    assert b["inactive_hidden"] == 1


def test_the_closed_store_comes_back_on_the_explicit_opt_in(wired, api_client):
    assert _row(_board(api_client, include_inactive="true"), "B-999")["is_active"] is False


def test_the_store_and_market_filters_narrow_the_board(wired, api_client):
    assert {r["store_code"] for r in _board(api_client, stores="B-117")["rows"]} == {"B-117"}
    assert {r["store_code"] for r in _board(api_client, markets="NYC")["rows"]} == {"B-200"}


def test_the_status_filter_narrows_the_board_to_the_words_it_published(wired, api_client):
    b = _board(api_client)
    want = _row(b, "B-117")["status"]
    only = _board(api_client, status=want)
    assert only["rows"] and all(r["status"] == want for r in only["rows"])


# ── one store's pay period ───────────────────────────────────────────────────────────────────────
def test_one_stores_rail_is_reachable_and_carries_the_doors(wired, api_client):
    r = api_client.get(f"{BOARD}/B-117?org_id={ORG_A}&start={LO}&end={HI}")
    assert r.status_code == 200, r.text
    one = r.json()
    assert one["store_code"] == "B-117" and one["period_start"] == LO
    assert [s["key"] for s in one["steps"]] == ["punches", "basis", "exceptions", "approval",
                                                "rates", "preflight", "run"]
    assert all(s["screen"].startswith("/storeops/") for s in one["steps"])


def test_a_store_in_another_organisation_is_not_found_here(wired, api_client):
    r = api_client.get(f"{BOARD}/L-1?org_id={ORG_A}&start={LO}&end={HI}")
    assert r.status_code == 404, r.text


def test_a_closed_store_is_still_readable_and_says_it_is_closed(wired, api_client):
    r = api_client.get(f"{BOARD}/B-999?org_id={ORG_A}&start={LO}&end={HI}")
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False


# ── degrading by name ────────────────────────────────────────────────────────────────────────────
class _RaisingOn:
    """The double, except that one table raises the way PostgREST does for a missing relation."""

    def __init__(self, inner, table):
        self._inner, self._table = inner, table

    def schema(self, name):
        return _RaisingSchema(self._inner.schema(name), self._table)

    def table(self, name):
        return self.schema("public").table(name)

    def __getattr__(self, item):
        return getattr(self._inner, item)


class _RaisingSchema:
    def __init__(self, inner, table):
        self._inner, self._table = inner, table

    def table(self, name):
        if name == self._table:
            raise RuntimeError(f'relation "{name}" does not exist')
        return self._inner.table(name)

    def __getattr__(self, item):
        return getattr(self._inner, item)


def test_an_unreadable_run_ledger_is_named_rather_than_read_as_never_run(wired, monkeypatch,
                                                                        api_client):
    monkeypatch.setattr("app.modules.storeops.payroll_run_api.get_supabase",
                        lambda: _RaisingOn(wired, "payroll_gross_ledger"))
    b = _board(api_client)
    assert b["ready"] is False
    assert any(x["table"] == "payroll_gross_ledger" for x in b["blockers"]), b["blockers"]
    assert b["rows"], "the rest of the board still answers"


def test_an_unreadable_approval_table_is_named(wired, monkeypatch, api_client):
    monkeypatch.setattr("app.modules.storeops.payroll_run_api.get_supabase",
                        lambda: _RaisingOn(wired, "payroll_approval"))
    b = _board(api_client)
    assert any(x["table"] == "payroll_approval" for x in b["blockers"]), b["blockers"]


# ── nothing here writes, and it cannot run payroll ───────────────────────────────────────────────
def test_reading_the_board_leaves_every_seeded_row_exactly_as_it_was(wired, api_client):
    keys = ("storeops.shifts", "storeops.timelog", "storeops.payroll_approval",
            "storeops.payroll_gross_ledger", "storeops.payroll_tax_ledger",
            "storeops.payroll_expense_item", "storeops.employees", "storeops.stores")
    before = {k: wired.rows(k) for k in keys}
    _board(api_client)
    api_client.get(f"{BOARD}/B-117?org_id={ORG_A}&start={LO}&end={HI}")
    assert {k: wired.rows(k) for k in keys} == before


def test_the_surface_carries_no_writer_and_no_route_that_runs_payroll(wired):
    import inspect

    from app.modules.storeops import payroll_run_api as api_mod
    src = inspect.getsource(api_mod)
    for writer in (".insert(", ".update(", ".upsert(", ".delete("):
        assert writer not in src, writer
    assert "@router.post" not in src and "@router.put" not in src
