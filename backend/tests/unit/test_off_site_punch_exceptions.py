"""Unit: an off-site punch reaches management through the board they already review.

OWNER ASK 2026-11-01: a clock-out from somewhere that is not the store *"should be reported as flag
to the management"*.

WHY THIS IS NOT A NEW REPORT (CLAUDE.md duplicate-check build gate). Management already reviews
`GET /storeops/timeclock/attendance-exceptions` — the board that answers "who was scheduled and did
not clock in, who was late, who left early, who covered for whom". "This punch was not made at the
store" is the same question about the same punches for the same reviewer, so it becomes another
`exception_type` on that board, with the filters and the Send bar it already has. A second report
would be a sibling derivation that drifts, and nobody would open it.

WHAT THE ENGINE IS NOT ALLOWED TO DO HERE. It does not judge a distance and it does not know what a
radius is: the verdict was decided and STORED when the punch was made (`core/store_geofence`), and
this pass only READS it, through the same `PUNCH_COLUMNS` table the writer used. A board that
re-judged punches on read would restate who was flagged last month every time somebody moved a pin.
"""
from datetime import datetime, timezone

import pytest

from app.modules.core import geo
from app.modules.core import store_geofence as sg
from app.modules.storeops import attendance_exceptions as ae

TZ = timezone.utc
NOW = datetime(2026, 11, 1, 23, 0, tzinfo=TZ)
DAY = "2026-11-01"
CFG = dict(ae.DEFAULT_CONFIG)

SHIFT = {"id": "sh1", "employee_id": "E029", "employee_name": "A Rep", "store_code": "B-1115",
         "shift_date": DAY, "start_time": "10:00", "end_time": "18:00", "is_deleted": False}


def _punch(**over):
    row = {"id": "p1", "employee_id": "E029", "employee_name": "A Rep", "store_code": "B-1115",
           "work_date": DAY, "clock_in": f"{DAY}T10:00:00+00:00",
           "clock_out": f"{DAY}T18:00:00+00:00"}
    row.update(over)
    return row


def _rows(punches, shifts=(SHIFT,)):
    return ae.compute_attendance_exceptions(list(shifts), punches, [], CFG, NOW, TZ)


def _of(rows):
    return [r for r in rows if r["exception_type"].startswith("off_site")]


# ── §A. the flag reaches the board ───────────────────────────────────────────────────────────────
def test_A1_a_clock_out_judged_off_site_becomes_an_exception_row():
    rows = _of(_rows([_punch(clock_out_geo_decision=geo.OUTSIDE, clock_out_geo_distance_m=5600)]))
    assert len(rows) == 1
    row = rows[0]
    assert row["exception_type"] == "off_site_clock_out"
    assert row["punch_id"] == "p1" and row["employee_id"] == "E029"
    assert row["store_code"] == "B-1115" and row["work_date"] == DAY
    assert row["distance_m"] == 5600
    assert row["punch_kind"] == "clock_out"


def test_A2_a_clock_in_judged_off_site_becomes_its_own_row():
    rows = _of(_rows([_punch(clock_in_geo_decision=geo.OUTSIDE, clock_in_geo_distance_m=900)]))
    assert [r["exception_type"] for r in rows] == ["off_site_clock_in"]
    assert rows[0]["distance_m"] == 900


def test_A3_both_ends_off_site_are_two_rows_not_one_averaged_verdict():
    """"Arrived at the store and left from home" and "never came near the store" are different
    things for a manager to say to somebody, so they stay separable."""
    rows = _of(_rows([_punch(clock_in_geo_decision=geo.OUTSIDE, clock_in_geo_distance_m=4000,
                             clock_out_geo_decision=geo.OUTSIDE, clock_out_geo_distance_m=5600)]))
    assert [r["exception_type"] for r in rows] == ["off_site_clock_in", "off_site_clock_out"]
    assert [r["distance_m"] for r in rows] == [4000, 5600]


def test_A4_the_row_carries_the_times_a_manager_needs_to_act_on_it():
    row = _of(_rows([_punch(clock_out_geo_decision=geo.OUTSIDE)]))[0]
    assert row["clock_in"] and row["clock_out"]
    assert row["clock_in_local"] and row["clock_out_local"]
    assert row["note"], "a flag with no sentence is a number somebody has to interpret"


def test_A5_an_off_site_punch_is_flagged_whether_or_not_it_was_scheduled():
    """The two passes are independent: a scheduled punch made from home is the owner's case, and an
    unscheduled one made from home is worse. Neither may hide the other."""
    punch = _punch(clock_out_geo_decision=geo.OUTSIDE)
    assert len(_of(_rows([punch], shifts=(SHIFT,)))) == 1
    assert len(_of(_rows([punch], shifts=()))) == 1
    kinds = {r["exception_type"] for r in _rows([punch], shifts=())}
    assert "unscheduled" in kinds and "off_site_clock_out" in kinds


def test_A6_an_off_site_punch_does_not_suppress_the_boards_existing_exceptions():
    """THE REGRESSION GUARD. The board's own classes must come back exactly as before — a new pass
    that swallowed a no_show would trade one blind spot for another."""
    rows = _rows([], shifts=(SHIFT,))
    assert [r["exception_type"] for r in rows] == ["no_show"]
    late = _punch(clock_in=f"{DAY}T10:45:00+00:00", clock_out_geo_decision=geo.OUTSIDE)
    types = [r["exception_type"] for r in _rows([late])]
    assert "late" in types and "off_site_clock_out" in types


# ── §B. what was never measured accuses nobody ───────────────────────────────────────────────────
def test_B1_a_punch_with_no_verdict_at_all_is_not_a_flag():
    """Every punch in the platform looks like this until migration 1112 is applied and a store is
    pinned. If this emitted a row, the board's first morning would be unusable."""
    assert _of(_rows([_punch()])) == []


@pytest.mark.parametrize("decision", [geo.INSIDE, geo.UNVERIFIED_NO_FIX, geo.UNVERIFIED_NO_TARGET,
                                      geo.UNVERIFIED_ACCURACY, "", None, "outside_ish"])
def test_B2_only_a_decision_on_the_allowlist_is_ever_a_flag(decision):
    assert _of(_rows([_punch(clock_in_geo_decision=decision,
                             clock_out_geo_decision=decision)])) == []


def test_B3_an_open_punch_has_no_clock_out_to_judge():
    rows = _of(_rows([_punch(clock_out=None, clock_in_geo_decision=geo.OUTSIDE)]))
    assert [r["exception_type"] for r in rows] == ["off_site_clock_in"]
    assert rows[0]["clock_out"] is None


def test_B4_a_punch_that_cannot_be_placed_on_the_timeline_is_skipped_not_crashed_on():
    assert _of(_rows([_punch(clock_in=None, clock_out_geo_decision=geo.OUTSIDE)])) == []
    assert _of(_rows([_punch(work_date="", clock_out_geo_decision=geo.OUTSIDE)])) == []
    assert _of(_rows([{"clock_out_geo_decision": geo.OUTSIDE}])) == []


# ── §C. the engine reads the verdict and never re-decides it ─────────────────────────────────────
def test_C1_the_engine_reads_the_shared_column_table_and_no_name_of_its_own():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "app/modules/storeops/attendance_exceptions.py").read_text()
    assert "store_geofence" in src, "the shared verdict home is not dereferenced"
    # CODE only — a comment may name what the module deliberately does NOT do, and a lock that
    # could not tell the two apart would make the explanation unwritable.
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    for banned in ("geo_lat", "gps_lat", "clock_out_geo_decision", "radius", "haversine"):
        assert banned not in code, f"{banned} is spelled here instead of being dereferenced"


def test_C2_a_moved_pin_does_not_restate_an_old_verdict():
    """Pinning a store today must not change who was flagged last month. The proof is that the
    engine is handed no pin and no config and still produces the flag — there is nothing it could
    re-judge against."""
    rows = _of(_rows([_punch(clock_out_geo_decision=geo.OUTSIDE, clock_out_geo_distance_m=5600)]))
    assert rows[0]["distance_m"] == 5600


def test_C3_the_exception_type_names_are_declared_once_for_the_filters_to_read():
    assert sg.KIND_CLOCK_IN in ae.OFF_SITE_EXCEPTION_TYPES
    assert ae.OFF_SITE_EXCEPTION_TYPES[sg.KIND_CLOCK_OUT] == "off_site_clock_out"


# ── §T. THE CROSS-TENANT PASS (project rule 2026-10-30) ─────────────────────────────────────────
#
# The engine is pure and tenant-agnostic — the caller hands it one tenant's already-scoped rows —
# so the cross-tenant risk here is not a radius but a LEAK: a punch belonging to another tenant
# appearing on this tenant's board. The row set is what the engine is given, so what is pinned here
# is that no row is invented for an employee or store the engine was not handed.
THREE_TENANT_PUNCHES = [
    _punch(id="h1", employee_id="E029", store_code="B-1115", clock_out_geo_decision=geo.OUTSIDE),
    _punch(id="s1", employee_id="L001", store_code="L-01", clock_out_geo_decision=geo.OUTSIDE),
    _punch(id="v1", employee_id="V001", store_code="V-01", clock_in_geo_decision=geo.OUTSIDE),
]


@pytest.mark.parametrize("punch", THREE_TENANT_PUNCHES)
def test_T1_every_tenants_shape_produces_its_own_flag(punch):
    rows = _of(_rows([punch], shifts=()))
    assert len(rows) == 1
    assert rows[0]["punch_id"] == punch["id"]
    assert rows[0]["store_code"] == punch["store_code"]


@pytest.mark.parametrize("punch", THREE_TENANT_PUNCHES)
def test_T2_no_tenants_board_gains_a_row_for_a_punch_it_was_not_handed(punch):
    rows = _of(_rows([punch], shifts=()))
    assert {r["employee_id"] for r in rows} == {punch["employee_id"]}
    assert {r["punch_id"] for r in rows} == {punch["id"]}
