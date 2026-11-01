"""The sweep, against the in-memory database double: coordinates go, verdicts stay, every tenant.

WHAT THESE PIN that the unit tests cannot: that the registry's relation and column names are the
ones the writers actually use, that the sweep is bounded and idempotent against a real-ish client,
that a tenant's own declared window is what stamps its rows, and that the whole thing degrades
quietly on a tenant where migration 1113 has not run.
"""
from datetime import datetime, timezone

import pytest

from app.modules.core import geo_retention as R
from app.modules.core import store_geofence as _sgeo

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
PUNCH = R.RELATIONS_BY_NAME["storeops.timelog"]
VISIT = R.RELATIONS_BY_NAME["storeops.store_visits"]
CHECKIN = R.RELATIONS_BY_NAME["marketing.marketing_event_checkin"]

CI = _sgeo.PUNCH_COLUMNS[_sgeo.KIND_CLOCK_IN]
CO = _sgeo.PUNCH_COLUMNS[_sgeo.KIND_CLOCK_OUT]
SV = _sgeo.PUNCH_COLUMNS[_sgeo.KIND_STORE_VISIT]

HOUSE = "00000000-0000-0000-0000-000000000001"
SOLO = "11111111-1111-1111-1111-111111111111"
BARE = "22222222-2222-2222-2222-222222222222"


# ── a tiny client the sweep can drive, backed by the shared fake ─────────────────────────────────
@pytest.fixture
def db():
    from tests.fakes.supabase import FakeSupabase
    return FakeSupabase()


def _client_for(db):
    def inner(entry):
        return db.schema(entry["schema"])
    return inner


def _punch(org, pid, stamp, *, decision="outside", distance=4200.0, cleared=False):
    row = {"id": pid, "org_id": org, PUNCH["stamp"]: stamp,
           CI["decision"]: "inside", CI["distance"]: 12.0,
           CO["decision"]: decision, CO["distance"]: distance}
    coords = {CI["lat"]: 40.6, CI["lng"]: -73.9, CI["accuracy"]: 10,
              CO["lat"]: 40.1, CO["lng"]: -74.9, CO["accuracy"]: 14}
    row.update({k: None for k in coords} if cleared else coords)
    return row


def _seed(db, rows, entry=PUNCH):
    db.seed(f'{entry["schema"]}.{entry["table"]}', rows)


def _rows(db, entry=PUNCH):
    return db.rows(f'{entry["schema"]}.{entry["table"]}')


# ── A. the sweep clears what is due ──────────────────────────────────────────────────────────────
def test_A1_a_punch_past_its_date_loses_its_COORDINATES(db):
    _seed(db, [_punch(HOUSE, "p1", "2026-05-01")])
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert rep["ok"] and rep["cleared"] == 1
    row = _rows(db)[0]
    for col in R.fix_columns_for(PUNCH):
        assert row[col] is None, f"{col} survived the purge"


def test_A2_and_KEEPS_the_verdict_and_the_distance(db):
    _seed(db, [_punch(HOUSE, "p1", "2026-05-01")])
    R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    row = _rows(db)[0]
    assert row[CO["decision"]] == "outside"
    assert row[CO["distance"]] == 4200.0
    assert row[CI["decision"]] == "inside"


def test_A3_so_the_off_site_flag_still_reads_off_site(db):
    _seed(db, [_punch(HOUSE, "p1", "2026-05-01")])
    R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert _sgeo.punch_off_site_flags(_rows(db)[0]), "a purged punch stopped being flagged"


def test_A4_a_punch_not_yet_due_is_untouched(db):
    _seed(db, [_punch(HOUSE, "p1", "2027-05-01")])
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert rep["cleared"] == 0
    assert _rows(db)[0][CO["lat"]] == 40.1


def test_A5_an_UNSTAMPED_punch_is_never_purged(db):
    _seed(db, [_punch(HOUSE, "p1", None)])
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert rep["cleared"] == 0
    assert _rows(db)[0][CI["lat"]] == 40.6


def test_A6_the_stamp_itself_is_not_erased(db):
    """The row must keep saying what was promised about it, after the promise is kept."""
    _seed(db, [_punch(HOUSE, "p1", "2026-05-01")])
    R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert _rows(db)[0][PUNCH["stamp"]] == "2026-05-01"


# ── B. idempotent and bounded ────────────────────────────────────────────────────────────────────
def test_B1_running_it_twice_clears_nothing_the_second_time(db):
    _seed(db, [_punch(HOUSE, "p1", "2026-05-01")])
    assert R.sweep_relation(_client_for(db), PUNCH, now=NOW)["cleared"] == 1
    assert R.sweep_relation(_client_for(db), PUNCH, now=NOW)["cleared"] == 0


def test_B2_an_already_clear_row_is_not_rewritten(db):
    _seed(db, [_punch(HOUSE, "p1", "2026-05-01", cleared=True)])
    assert R.sweep_relation(_client_for(db), PUNCH, now=NOW)["cleared"] == 0


def test_B3_a_backlog_bigger_than_the_cap_is_REPORTED_not_silently_half_done(db):
    _seed(db, [_punch(HOUSE, f"p{i}", "2026-05-01") for i in range(7)])
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW, page=2, max_pages=2)
    assert rep["more_due"] is True
    assert rep["cleared"] == 4


def test_B4_a_backlog_inside_the_cap_finishes_and_says_nothing_is_left(db):
    _seed(db, [_punch(HOUSE, f"p{i}", "2026-05-01") for i in range(5)])
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW, page=2, max_pages=10)
    assert rep["more_due"] is False and rep["cleared"] == 5


def test_B5_an_empty_relation_is_an_empty_report_never_a_raise(db):
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert rep == {"relation": "storeops.timelog", "cleared": 0, "scanned": 0,
                   "more_due": False, "ok": True}


# ── C. it degrades instead of breaking a scheduler ───────────────────────────────────────────────
def test_C1_a_relation_whose_migration_has_not_run_is_reported_with_the_number(db):
    def broken(entry):
        raise RuntimeError('column "geo_purge_after_date" does not exist')
    rep = R.sweep_relation(broken, PUNCH, now=NOW)
    assert rep["ok"] is False
    assert "1113" in rep["error"]


def test_C2_one_broken_relation_never_stops_the_others(db):
    _seed(db, [_punch(HOUSE, "p1", "2026-05-01")])

    def half_broken(entry):
        if entry["relation"] == CHECKIN["relation"]:
            raise RuntimeError("relation does not exist")
        return db.schema(entry["schema"])

    out = R.sweep_all(half_broken, now=NOW)
    assert out["ok"] is False
    assert out["cleared"] == 1
    by = {r["relation"]: r for r in out["relations"]}
    assert by["storeops.timelog"]["ok"] is True
    assert by[CHECKIN["relation"]]["ok"] is False


def test_C3_sweep_all_covers_every_registered_relation(db):
    out = R.sweep_all(_client_for(db), now=NOW)
    assert {r["relation"] for r in out["relations"]} == {e["relation"] for e in R.LOCATION_RELATIONS}


# ── S. the sibling relations, not just the one that surfaced the bug ─────────────────────────────
def test_S1_a_DM_store_visit_is_swept_too(db):
    _seed(db, [{"id": "v1", "org_id": HOUSE, VISIT["stamp"]: "2026-05-01",
                SV["lat"]: 40.6, SV["lng"]: -73.9, SV["accuracy"]: 8,
                SV["decision"]: "outside", SV["distance"]: 900.0}], VISIT)
    rep = R.sweep_relation(_client_for(db), VISIT, now=NOW)
    assert rep["cleared"] == 1
    row = _rows(db, VISIT)[0]
    assert row[SV["lat"]] is None and row[SV["decision"]] == "outside"


def test_S2_an_event_checkin_is_swept_too_under_its_OWN_stamp_column(db):
    _seed(db, [{"id": "c1", "org_id": HOUSE, CHECKIN["stamp"]: "2026-05-01",
                SV["lat"]: 40.6, SV["lng"]: -73.9, SV["accuracy"]: 8,
                SV["decision"]: "inside", SV["distance"]: 20.0}], CHECKIN)
    rep = R.sweep_relation(_client_for(db), CHECKIN, now=NOW)
    assert rep["cleared"] == 1
    row = _rows(db, CHECKIN)[0]
    assert row[SV["lat"]] is None and row[SV["decision"]] == "inside"


def test_S3_the_event_checkin_was_the_PRE_EXISTING_gap_and_is_now_in_the_registry():
    """It was declared `unmonitored` ("no purge job runs") before this change. It is covered now."""
    assert CHECKIN["relation"] in R.RELATIONS_BY_NAME
    assert CHECKIN["migration"] == "986"


# ── T. every tenant, its own window, no leakage ──────────────────────────────────────────────────
@pytest.mark.parametrize("org,days,expected", [
    (HOUSE, 180, "2027-04-30"), (SOLO, 30, "2026-12-01"), (BARE, None, "2027-04-30")])
def test_T1_each_tenants_row_is_stamped_from_its_OWN_declared_window(org, days, expected):
    assert R.purge_after(now=NOW, days=days) == expected


def test_T2_the_sweep_honours_each_rows_own_date_not_one_tenants(db):
    _seed(db, [_punch(HOUSE, "h1", "2026-05-01"),      # due
               _punch(SOLO, "s1", "2027-05-01"),       # not due
               _punch(BARE, "b1", "2026-10-31")])      # due
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert rep["cleared"] == 2
    by = {r["id"]: r for r in _rows(db)}
    assert by["h1"][CO["lat"]] is None
    assert by["b1"][CO["lat"]] is None
    assert by["s1"][CO["lat"]] == 40.1, "another tenant's row was purged early"


def test_T3_a_tenant_with_nothing_due_is_told_zero_rather_than_an_error(db):
    _seed(db, [_punch(SOLO, "s1", "2027-05-01")])
    rep = R.sweep_relation(_client_for(db), PUNCH, now=NOW)
    assert rep["ok"] is True and rep["cleared"] == 0


# ── W. the WRITERS stamp it — a coordinate with no purge date is one nothing will ever delete ────
def test_W1_the_punch_writer_stamps_the_commitment(monkeypatch):
    from app.modules.storeops import router as r
    monkeypatch.setattr(r, "_punch_geo_present", lambda: True)
    monkeypatch.setattr(r, "_geo_stamp_present", lambda rel: True)
    monkeypatch.setattr(r, "_punch_policy_row", lambda org: ({PUNCH["policy_column"]: 30}, True))
    monkeypatch.setattr(r, "_store_pin_row", lambda org, code: {})
    cols = r._punch_geo_columns(HOUSE, _sgeo.KIND_CLOCK_OUT, "B-1", 40.1, -74.9, 10)
    assert cols[PUNCH["stamp"]] == R.purge_after(days=30)


def test_W2_the_punch_writer_stamps_NOTHING_before_migration_1113(monkeypatch):
    from app.modules.storeops import router as r
    monkeypatch.setattr(r, "_punch_geo_present", lambda: True)
    monkeypatch.setattr(r, "_geo_stamp_present", lambda rel: False)
    monkeypatch.setattr(r, "_punch_policy_row", lambda org: ({}, True))
    monkeypatch.setattr(r, "_store_pin_row", lambda org, code: {})
    cols = r._punch_geo_columns(HOUSE, _sgeo.KIND_CLOCK_OUT, "B-1", 40.1, -74.9, 10)
    assert PUNCH["stamp"] not in cols
    assert cols[CO["lat"]] == 40.1, "the fix itself must still be stored"


def test_W3_an_unreadable_window_stamps_the_HOUSE_default_never_today(monkeypatch):
    from app.modules.storeops import router as r
    monkeypatch.setattr(r, "_punch_geo_present", lambda: True)
    monkeypatch.setattr(r, "_geo_stamp_present", lambda rel: True)
    monkeypatch.setattr(r, "_punch_policy_row",
                        lambda org: ({PUNCH["policy_column"]: "nonsense"}, True))
    monkeypatch.setattr(r, "_store_pin_row", lambda org, code: {})
    cols = r._punch_geo_columns(HOUSE, _sgeo.KIND_CLOCK_OUT, "B-1", 40.1, -74.9, 10)
    assert cols[PUNCH["stamp"]] == R.purge_after(days=R.DEFAULT_RETENTION_DAYS)


def test_W4_the_store_visit_writer_stamps_it_too(monkeypatch):
    from app.modules.storeops import router as sr
    from app.modules.storevisit import router as vr
    monkeypatch.setattr(sr, "_punch_geo_present", lambda: True)
    monkeypatch.setattr(sr, "_geo_stamp_present", lambda rel: True)
    monkeypatch.setattr(sr, "_punch_policy_row", lambda org: ({PUNCH["policy_column"]: 45}, True))
    monkeypatch.setattr(sr, "_store_pin_row", lambda org, code: {})
    cols = vr._visit_geo_columns(HOUSE, "B-1", 40.1, -74.9, 10)
    assert cols[VISIT["stamp"]] == R.purge_after(days=45)


def test_W5_the_marketing_checkin_writer_already_stamped_and_now_shares_the_arithmetic():
    from app.modules.marketing import event_logic as L
    assert L.purge_after_date(NOW, 45) == R.purge_after(now=NOW, days=45)
