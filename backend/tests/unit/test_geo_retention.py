"""A retention promise that deletes nothing is worse than no promise — proved, DB-free.

THE DEFECT UNDER TEST. `punch_geo_retention_days` (mig 1112) and `checkin_geo_retention_days`
(mig 986) both declared a window and nothing ever deleted anything. The security pass on the punch
geofence found the first; the index already declared the second as `unmonitored`. Two instances, one
class, so these tests judge the MECHANISM: one registry, one date arithmetic, one sweep, and the
verdict surviving every purge.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.core import geo_retention as R
from app.modules.core import store_geofence as _sgeo

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
PUNCH = R.RELATIONS_BY_NAME["storeops.timelog"]
VISIT = R.RELATIONS_BY_NAME["storeops.store_visits"]
CHECKIN = R.RELATIONS_BY_NAME["marketing.marketing_event_checkin"]
ALL = pytest.mark.parametrize("entry", R.LOCATION_RELATIONS, ids=lambda e: e["relation"])


# ── A. the window ────────────────────────────────────────────────────────────────────────────────
def test_A1_a_declared_window_is_honoured():
    assert R.purge_after(now=NOW, days=30) == "2026-12-01"


def test_A2_the_house_default_applies_when_a_tenant_declared_nothing():
    assert R.purge_after(now=NOW, days=None) == (
        (NOW + timedelta(days=R.DEFAULT_RETENTION_DAYS)).date().isoformat())


@pytest.mark.parametrize("junk", [None, "", "nonsense", {}, [], "  "])
def test_A3_an_unreadable_window_NEVER_resolves_to_delete_everything(junk):
    assert R.retention_days(junk) == R.DEFAULT_RETENTION_DAYS


@pytest.mark.parametrize("tiny", [0, -1, -9999, "0"])
def test_A4_a_zero_or_negative_window_is_clamped_to_the_floor_not_obeyed(tiny):
    assert R.retention_days(tiny) == R.MIN_RETENTION_DAYS


def test_A5_a_tenants_own_longer_window_is_obeyed_not_capped_to_the_house_number():
    assert R.retention_days(730) == 730


# ── B. the registry covers every relation that stores where somebody was ─────────────────────────
def test_B1_the_three_known_location_relations_are_all_registered():
    assert {e["relation"] for e in R.LOCATION_RELATIONS} == {
        "storeops.timelog", "storeops.store_visits", "marketing.marketing_event_checkin"}


@ALL
def test_B2_every_entry_names_a_stamp_a_policy_and_what_it_holds(entry):
    for key in ("relation", "schema", "table", "captures", "stamp",
                "policy_table", "policy_column", "migration", "what"):
        assert entry.get(key), f"{entry.get('relation')} is missing {key}"


@ALL
def test_B3_every_entrys_fix_columns_come_from_the_ONE_home_never_a_copy(entry):
    """The coordinate names must be exactly what `store_geofence.PUNCH_COLUMNS` says they are."""
    expected = set()
    for kind in entry["captures"]:
        c = _sgeo.PUNCH_COLUMNS[kind]
        expected |= {c["lat"], c["lng"], c["accuracy"]}
    assert set(R.fix_columns_for(entry)) == expected


def test_B4_the_punch_relation_covers_BOTH_ends_of_a_shift():
    cols = set(R.fix_columns_for(PUNCH))
    assert _sgeo.PUNCH_COLUMNS[_sgeo.KIND_CLOCK_IN]["lat"] in cols
    assert _sgeo.PUNCH_COLUMNS[_sgeo.KIND_CLOCK_OUT]["lat"] in cols


def test_B5_the_event_checkin_keeps_its_OWN_stamp_column_spelling():
    """It shipped first and the column is already in production data; renaming buys nothing."""
    assert CHECKIN["stamp"] == R.CHECKIN_STAMP_COLUMN
    assert PUNCH["stamp"] == VISIT["stamp"] == R.PUNCH_STAMP_COLUMN


# ── C. what a purge clears, and what it must never touch ─────────────────────────────────────────
@ALL
def test_C1_a_purge_clears_every_coordinate_column(entry):
    assert set(R.cleared_columns(entry)) == set(R.fix_columns_for(entry))
    assert all(v is None for v in R.cleared_columns(entry).values())


@ALL
def test_C2_a_purge_NEVER_touches_a_verdict_or_a_distance(entry):
    cleared = set(R.cleared_columns(entry))
    for col in R.verdict_columns_for(entry):
        assert col not in cleared, f"{col} would be wiped — the flag must survive the purge"


@ALL
def test_C3_a_purge_NEVER_touches_the_stamp_itself(entry):
    assert entry["stamp"] not in R.cleared_columns(entry)


def test_C4_the_off_site_flag_still_reads_off_site_after_a_purge():
    """The business question survives: management reviews the verdict and the distance, not the fix."""
    cols = _sgeo.PUNCH_COLUMNS[_sgeo.KIND_CLOCK_OUT]
    row = {cols["lat"]: 40.1, cols["lng"]: -73.9, cols["accuracy"]: 10,
           cols["decision"]: "outside", cols["distance"]: 4200.0,
           PUNCH["stamp"]: "2026-01-01"}
    purged = {**row, **R.cleared_columns(PUNCH)}
    assert _sgeo.punch_off_site_flags(purged), "a purged punch stopped being flagged"
    assert purged[cols["distance"]] == 4200.0
    assert purged[cols["lat"]] is None


# ── D. which rows are due ────────────────────────────────────────────────────────────────────────
def test_D1_a_row_past_its_date_is_due():
    assert R.is_due({PUNCH["stamp"]: "2026-10-31"}, PUNCH, now=NOW) is True


def test_D2_a_row_due_TODAY_is_due():
    assert R.is_due({PUNCH["stamp"]: "2026-11-01"}, PUNCH, now=NOW) is True


def test_D3_a_row_not_yet_due_is_left_alone():
    assert R.is_due({PUNCH["stamp"]: "2026-11-02"}, PUNCH, now=NOW) is False


@pytest.mark.parametrize("stamp", [None, "", 0])
def test_D4_an_UNSTAMPED_row_is_never_purged_on_a_guess(stamp):
    """It predates the stamp. Guessing its age invents the commitment this design makes explicit."""
    assert R.is_due({PUNCH["stamp"]: stamp}, PUNCH, now=NOW) is False


def test_D5_a_full_timestamp_stamp_is_read_by_its_DATE_part():
    assert R.is_due({PUNCH["stamp"]: "2026-10-30T23:59:59+00:00"}, PUNCH, now=NOW) is True


# ── E. the sweep is idempotent and honest about what it cannot see ────────────────────────────────
def _due_row(entry, **extra):
    cols = {c: 1.0 for c in R.fix_columns_for(entry)}
    return {**cols, entry["stamp"]: "2026-01-01", **extra}


@ALL
def test_E1_a_due_row_is_planned_for_clearing(entry):
    plan = R.plan_purge([_due_row(entry)], entry, now=NOW)
    assert len(plan) == 1
    assert plan[0][1] == R.cleared_columns(entry)


@ALL
def test_E2_an_already_cleared_row_is_NOT_rewritten(entry):
    cleared = {**_due_row(entry), **R.cleared_columns(entry)}
    assert R.plan_purge([cleared], entry, now=NOW) == []


@ALL
def test_E3_running_the_sweep_twice_changes_nothing_the_second_time(entry):
    row = _due_row(entry)
    first = R.plan_purge([row], entry, now=NOW)
    assert first
    row.update(first[0][1])
    assert R.plan_purge([row], entry, now=NOW) == []


@ALL
def test_E4_a_row_not_yet_due_is_not_planned(entry):
    assert R.plan_purge([_due_row(entry, **{entry["stamp"]: "2027-01-01"})], entry, now=NOW) == []


@ALL
def test_E5_the_summary_counts_the_backlog_that_carries_no_promise(entry):
    rows = [_due_row(entry),
            _due_row(entry, **{entry["stamp"]: None}),
            {**_due_row(entry), **R.cleared_columns(entry)}]
    s = R.retention_summary(rows, entry, now=NOW)
    assert s["rows"] == 3
    assert s["due_for_purge"] == 1
    assert s["already_clear"] == 1
    assert s["no_retention_stamp"] == 1
    assert s["relation"] == entry["relation"]


@ALL
def test_E6_an_empty_relation_is_an_empty_answer_never_a_raise(entry):
    assert R.plan_purge([], entry, now=NOW) == []
    assert R.plan_purge(None, entry, now=NOW) == []
    assert R.retention_summary(None, entry, now=NOW)["rows"] == 0


# ── T. every tenant, measured against its OWN declaration ────────────────────────────────────────
@pytest.mark.parametrize("days,expected", [(30, "2026-12-01"), (90, "2027-01-30"), (365, "2027-11-01")])
def test_T1_each_tenants_own_window_produces_its_own_date(days, expected):
    assert R.purge_after(now=NOW, days=days) == expected


def test_T2_one_tenants_window_never_decides_anothers():
    assert R.purge_after(now=NOW, days=30) != R.purge_after(now=NOW, days=90)


def test_T3_the_default_is_a_POLICY_and_names_no_tenant_or_carrier():
    import inspect
    src = inspect.getsource(R)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    for word in ("cellfonz", "luxelink", "vzone", "boost", "total wireless", "metro"):
        assert word not in code.lower(), f"{word} is a tenant/carrier name in the one home"


# ── Z. source locks — the one home stays the one home ────────────────────────────────────────────
def test_Z1_the_one_home_spells_NO_coordinate_column_of_its_own():
    import inspect
    src = inspect.getsource(R)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    for col in ("gps_lat", "gps_lng", "clock_out_gps_lat", "check_in_lat", "gps_accuracy_m"):
        assert f'"{col}"' not in code, (
            f"{col} is spelled in geo_retention.py — it must be dereferenced from "
            "store_geofence.PUNCH_COLUMNS")


def test_Z2_marketings_date_arithmetic_delegates_here_rather_than_keeping_a_copy():
    from app.modules.marketing import event_logic as L
    assert L.purge_after_date(NOW, 30) == R.purge_after(now=NOW, days=30)
    import inspect
    body = inspect.getsource(L.purge_after_date)
    assert "timedelta" not in body, (
        "event_logic.purge_after_date still computes its own date — it must dereference "
        "geo_retention.purge_after")
