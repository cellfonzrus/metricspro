"""Integration: the scheduling workflow endpoints through the REAL app (index §STOREOPS-15).

Real routing, real middleware, the real endpoint bodies — over the in-memory database double, with
conftest blocking every outbound socket. "Integration" here means the app, not a real database
(docs/TESTING.md): there is no staging Supabase.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT:
  · the two routes are reachable at their registered paths, and neither writes anything;
  · ★ each organisation's week is ITS OWN — the SAME requested date returns a Monday week for one
    tenant and a Thursday week for another, at the HTTP boundary where the mis-bucketing would
    actually happen (owner's 2026-10-30 rule, and the security pass it asks for);
  · ★ one tenant's stores, shifts and sends never reach another tenant's board, both directions;
  · open-stores-only is the default and the number hidden is STATED (§CORE-45);
  · the standard filters narrow the board (the 2026-10-30 report rule);
  · the double-booking check sees the SAME person at another store, which only the whole-week read
    can answer;
  · a database missing a table degrades by name in `blockers` instead of 500-ing.
"""
import pytest

ORG_A = "00000000-0000-0000-0000-000000000001"   # week starts MONDAY (dow 0)
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"   # week starts THURSDAY (dow 3)
BOARD = "/api/v1/storeops/schedule-workflow"

WED = "2026-11-25"          # the date both tenants ask for
MON_WEEK = "2026-11-23"     # org A's week containing it
THU_WEEK = "2026-11-19"     # org B's week containing it

TENANTS = [{"org_id": ORG_A, "work_week_start_dow": 0},
           {"org_id": ORG_B, "work_week_start_dow": 3}]

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

BUDGETS = [{"org_id": ORG_A, "store_code": "B-117", "weekly_hours": 120},
           {"org_id": ORG_A, "store_code": "B-200", "weekly_hours": 40},
           {"org_id": ORG_B, "store_code": "L-1", "weekly_hours": 60}]

EMPLOYEES = [{"org_id": ORG_A, "employee_id": "E1", "home_store": "B-117"},
             {"org_id": ORG_A, "employee_id": "E2", "home_store": "B-200"},
             {"org_id": ORG_B, "employee_id": "L9", "home_store": "L-1"}]


def _shift(i, org, store, eid, day, start="09:00", end="17:00"):
    return {"id": i, "org_id": org, "store_code": store, "employee_id": eid,
            "employee_name": eid, "shift_date": day, "start_time": start, "end_time": end,
            "is_deleted": False}


SHIFTS = [
    _shift(1, ORG_A, "B-117", "E1", "2026-11-23"),
    _shift(2, ORG_A, "B-117", "E1", "2026-11-24"),
    # The SAME person at another store on a day they are already on at B-117 — the double booking.
    _shift(3, ORG_A, "B-200", "E1", "2026-11-24"),
    # Org B's week (Thu 19 Nov – Wed 25 Nov) — invisible to org A's board.
    _shift(4, ORG_B, "L-1", "L9", "2026-11-19"),
]

SENDS = [{"org_id": ORG_A, "report_key": "storeops_schedule", "channel": "email",
          "target": "team@x.test", "status": "sent",
          "filters": {"week_start": MON_WEEK, "store_code": "B-117"},
          "created_at": "2026-11-22T18:00:00Z"}]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.storeops.schedule_week_api.get_supabase",
                        lambda: fake_supabase)
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.modules.storeops.payroll_approval.get_supabase",
                        lambda: fake_supabase)
    fake_supabase.seed("storeops.tenants", TENANTS)
    fake_supabase.seed("storeops.stores", STORES)
    fake_supabase.seed("storeops.hours_budget", BUDGETS)
    fake_supabase.seed("storeops.employees", EMPLOYEES)
    fake_supabase.seed("storeops.shifts", SHIFTS)
    fake_supabase.seed("storeops.time_off_requests", [])
    fake_supabase.seed("storeops.shift_swap_requests", [])
    fake_supabase.seed("storeops.budget_override", [])
    fake_supabase.seed("notify.send_log", SENDS)
    return fake_supabase


def _board(api_client, org=ORG_A, week=WED, **params):
    q = "&".join([f"org_id={org}", f"week_start={week}"]
                 + [f"{k}={v}" for k, v in params.items()])
    r = api_client.get(f"{BOARD}?{q}")
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
    b = _board(wired and api_client)
    assert {r["store_code"] for r in b["rows"]} == {"B-117", "B-200"}
    for r in b["rows"]:
        assert r["status"] in {s["key"] for s in b["statuses"]}
        assert "resume" in r and len(r["steps"]) == 5


def test_the_board_publishes_the_five_steps_and_every_status_word(wired, api_client):
    b = _board(api_client)
    assert [s["key"] for s in b["steps"]] == ["budget", "availability", "shifts", "coverage",
                                              "tell"]
    assert [s["key"] for s in b["statuses"]] == ["not_started", "in_progress", "needs_input",
                                                 "skipped", "done"]
    assert all(s["label"] for s in b["statuses"])


def test_the_board_counts_the_weeks_and_names_the_step_stores_stop_at(wired, api_client):
    b = _board(api_client)
    assert b["summary"]["total"] == 2
    assert isinstance(b["summary"]["stuck_at"], list)


# ── ★ each organisation's own week ───────────────────────────────────────────────────────────────
def test_the_same_requested_date_returns_each_tenants_own_week(wired, api_client):
    a = _board(api_client, org=ORG_A)
    b = _board(api_client, org=ORG_B)
    assert (a["week_start"], a["week_end"]) == (MON_WEEK, "2026-11-29")
    assert (b["week_start"], b["week_end"]) == (THU_WEEK, "2026-11-25")
    assert a["declared"]["week_start_day"] == "Monday"
    assert b["declared"]["week_start_day"] == "Thursday"


def test_the_week_the_board_reports_is_the_week_its_days_cover(wired, api_client):
    b = _board(api_client, org=ORG_B)
    assert [d["date"] for d in b["days"]][0] == b["week_start"]
    assert [d["name"] for d in b["days"]][0] == "Thursday" and len(b["days"]) == 7


def test_a_tenant_that_has_declared_no_week_is_told_the_column_default_is_in_use(wired, api_client):
    wired.seed("storeops.tenants", [{"org_id": ORG_A, "work_week_start_dow": 0}])
    b = _board(api_client, org=ORG_B)
    assert b["declared"]["week_start_declared"] is False
    assert b["declared"]["week_start_day"] == "Monday"


# ── ★ one tenant's week never reaches another's board ────────────────────────────────────────────
def test_org_bs_stores_shifts_and_sends_are_absent_from_org_as_board(wired, api_client):
    a = _board(api_client, org=ORG_A)
    assert "L-1" not in {r["store_code"] for r in a["rows"]}
    hours = sum(r["scheduled_hours"] for r in a["rows"])
    assert hours == 24.0   # three 8h shifts, all org A's


def test_org_as_stores_shifts_and_sends_are_absent_from_org_bs_board(wired, api_client):
    b = _board(api_client, org=ORG_B)
    assert {r["store_code"] for r in b["rows"]} == {"L-1"}
    assert _row(b, "L-1")["scheduled_hours"] == 8.0
    # Org A's send of its own rota must not settle org B's "tell the team" step.
    assert _step(_row(b, "L-1"), "tell")["status"] != "done"


# ── the standard filters, and the inactive rule ──────────────────────────────────────────────────
def test_a_closed_store_is_hidden_by_default_and_the_number_hidden_is_stated(wired, api_client):
    b = _board(api_client)
    assert "B-999" not in {r["store_code"] for r in b["rows"]}
    assert b["inactive_hidden"] == 1


def test_the_closed_store_comes_back_on_the_explicit_opt_in_and_says_it_is_closed(wired,
                                                                                 api_client):
    b = _board(api_client, include_inactive="true")
    assert _row(b, "B-999")["is_active"] is False


def test_the_store_filter_narrows_the_board(wired, api_client):
    b = _board(api_client, stores="B-117")
    assert {r["store_code"] for r in b["rows"]} == {"B-117"}


def test_the_market_filter_narrows_the_board(wired, api_client):
    b = _board(api_client, markets="NYC")
    assert {r["store_code"] for r in b["rows"]} == {"B-200"}


def test_the_status_filter_narrows_the_board_to_the_words_it_published(wired, api_client):
    b = _board(api_client)
    want = _row(b, "B-117")["status"]
    only = _board(api_client, status=want)
    assert only["rows"] and all(r["status"] == want for r in only["rows"])


# ── the checks that need the WHOLE week, not one store's ─────────────────────────────────────────
def test_somebody_at_two_stores_on_one_day_is_named_on_both_stores_weeks(wired, api_client):
    b = _board(api_client)
    for code, other in (("B-117", "B-200"), ("B-200", "B-117")):
        clash = _step(_row(b, code), "coverage")["detail"]["double_booked"]
        assert any(other in c for c in clash), (code, clash)


def test_over_budget_is_named_against_this_stores_own_ceiling(wired, api_client):
    wired.seed("storeops.hours_budget", [{"org_id": ORG_A, "store_code": "B-117",
                                          "weekly_hours": 4}])
    cov = _step(_row(_board(api_client), "B-117"), "coverage")
    assert cov["status"] == "needs_input"
    assert any("budget" in n for n in cov["needs_input"])


def test_a_pending_time_off_request_holds_the_home_stores_week_up(wired, api_client):
    wired.seed("storeops.time_off_requests", [
        {"org_id": ORG_A, "employee_id": "E2", "start_date": "2026-11-25",
         "end_date": "2026-11-25", "status": "pending", "type": "pto"}])
    b = _board(api_client)
    assert _step(_row(b, "B-200"), "availability")["status"] == "needs_input"
    assert _step(_row(b, "B-117"), "availability")["status"] == "done"


def test_a_recorded_send_of_this_weeks_rota_settles_the_last_step_for_that_store_only(wired,
                                                                                     api_client):
    b = _board(api_client)
    assert _step(_row(b, "B-117"), "tell")["status"] == "done"
    assert _step(_row(b, "B-200"), "tell")["status"] != "done"


def test_a_send_of_a_different_week_does_not_settle_this_week(wired, api_client):
    wired.seed("notify.send_log", [dict(SENDS[0], filters={"week_start": "2026-11-16",
                                                           "store_code": "B-117"})])
    assert _step(_row(_board(api_client), "B-117"), "tell")["status"] != "done"


def test_uncovered_days_are_listed_and_the_payload_says_opening_days_are_not_declared(wired,
                                                                                      api_client):
    b = _board(api_client)
    assert b["declared"]["open_days_declared"] is False
    cov = _step(_row(b, "B-117"), "coverage")["detail"]
    assert cov["days_with_nobody"] and "days_with_nobody_note" in cov


# ── one store-week ───────────────────────────────────────────────────────────────────────────────
def test_one_store_weeks_rail_is_reachable_and_carries_the_doors(wired, api_client):
    r = api_client.get(f"{BOARD}/B-117?org_id={ORG_A}&week_start={WED}")
    assert r.status_code == 200, r.text
    one = r.json()
    assert one["store_code"] == "B-117" and one["week_start"] == MON_WEEK
    assert [s["key"] for s in one["steps"]] == ["budget", "availability", "shifts", "coverage",
                                                "tell"]
    assert all(s["screen"].startswith("/storeops/") for s in one["steps"])


def test_a_store_in_another_organisation_is_not_found_here(wired, api_client):
    r = api_client.get(f"{BOARD}/L-1?org_id={ORG_A}&week_start={WED}")
    assert r.status_code == 404, r.text


def test_a_closed_store_is_still_readable_and_says_it_is_closed(wired, api_client):
    r = api_client.get(f"{BOARD}/B-999?org_id={ORG_A}&week_start={WED}")
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False


def test_a_date_that_is_not_a_date_is_refused_rather_than_guessed(wired, api_client):
    r = api_client.get(f"{BOARD}?org_id={ORG_A}&week_start=not-a-date")
    assert r.status_code == 400, r.text


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


def test_an_unreadable_table_is_named_in_blockers_rather_than_500ing(wired, monkeypatch,
                                                                    api_client):
    monkeypatch.setattr("app.modules.storeops.schedule_week_api.get_supabase",
                        lambda: _RaisingOn(wired, "hours_budget"))
    b = _board(api_client)
    assert b["ready"] is False
    assert any(x["table"] == "hours_budget" for x in b["blockers"]), b["blockers"]
    assert b["rows"], "the rest of the board still answers"


def test_an_unreadable_shift_table_is_named_rather_than_read_as_an_empty_rota(wired, monkeypatch,
                                                                             api_client):
    monkeypatch.setattr("app.modules.storeops.schedule_week_api.get_supabase",
                        lambda: _RaisingOn(wired, "shifts"))
    b = _board(api_client)
    assert any(x["table"] == "shifts" for x in b["blockers"]), b["blockers"]


# ── nothing here writes ──────────────────────────────────────────────────────────────────────────
def test_reading_the_board_leaves_every_seeded_row_exactly_as_it_was(wired, api_client):
    keys = ("storeops.shifts", "storeops.hours_budget", "storeops.time_off_requests",
            "storeops.budget_override", "notify.send_log", "storeops.stores")
    before = {k: wired.rows(k) for k in keys}
    _board(api_client)
    api_client.get(f"{BOARD}/B-117?org_id={ORG_A}&week_start={WED}")
    assert {k: wired.rows(k) for k in keys} == before

    # …and the surface carries no writer at all, so a later change cannot quietly add one.
    import inspect

    from app.modules.storeops import schedule_week_api as api_mod
    src = inspect.getsource(api_mod)
    for writer in (".insert(", ".update(", ".upsert(", ".delete("):
        assert writer not in src, writer


def test_the_emailed_rota_uses_the_tenants_week_and_not_a_house_monday(wired):
    """The SIBLING of this flow's week rule: the schedule report snapped to Monday until 2026-11-26,
    so a Thursday-week tenant was emailed a Mon–Sun grid. One fact, one home — both read the
    tenant's declaration."""
    import inspect

    from app.modules.notify import report_registry
    src = inspect.getsource(report_registry._storeops_schedule)
    assert "start.weekday()" not in src
    assert "week_start_of" in src and "_week_start_dow" in src
