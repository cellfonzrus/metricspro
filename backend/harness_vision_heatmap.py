"""Proof harness for customer traffic + heat-map aggregation (mod-vision, migration 900).

Run: python3 backend/harness_vision_heatmap.py   (pure functions — no network, no DB)

The counting rules a manager will dispute are here, so they are proven against hand-written event
sequences including the ugly ones:

  1. An in/out pair with the same track key becomes one visit with the right dwell.
  2. An entry that was never seen leaving is STILL A VISIT (exit unknown) — dropping it would
     undercount traffic on exactly the busiest days.
  3. An exit with no entry is counted as unpaired rather than inventing a visit.
  4. Trackless events pair FIFO, and say so, so a report can exclude them.
  5. Visits are CLASSIFIED (passerby / customer / staff_or_merged), never deleted — an operator can
     see what was filtered and retune the band.
  6. The hourly curve, the peak hour, and the in-minus-out DRIFT are reported honestly.
  7. Presence samples roll up into idempotent heat-cell keys, out-of-grid cells are clamped.
  8. heat_matrix returns both max and p95 (the ramp that keeps the map readable) and dead zones.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import heatmap as H   # noqa: E402

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


D = "2026-08-19"


def ev(hhmmss, direction, track=None, hour=None):
    return {"occurred_at": f"{D}T{hhmmss}+00:00", "direction": direction, "track_key": track,
            "store_code": "S1", "local_date": D, "local_hour": hour if hour is not None
            else int(hhmmss[:2]), "camera_id": "cam-1"}


print("\n(1) A tracked in/out pair is one visit with the right dwell")
r = H.pair_visits([ev("10:00:00", "in", "t1"), ev("10:05:00", "out", "t1")])
v = r["visits"][0]
check("one visit produced", len(r["visits"]) == 1)
check("dwell is 300s", v["dwell_seconds"] == 300)
check("paired by track key", v["paired_by"] == "track")
check("classified as a customer", v["classification"] == "customer")

print("\n(2) An entry with no exit is still a visit")
r = H.pair_visits([ev("10:00:00", "in", "t1"), ev("11:00:00", "in", "t2"),
                   ev("11:04:00", "out", "t2")])
check("two visits, not one", len(r["visits"]) == 2)
unp = [x for x in r["visits"] if x["paired_by"] == "unpaired"]
check("the un-exited track is present", len(unp) == 1)
check("its exit is unknown, not fabricated", unp[0]["exited_at"] is None)
check("it has no dwell (so it cannot skew the average)", unp[0]["dwell_seconds"] is None)
check("it is classified 'unpaired', not silently a customer", unp[0]["classification"] == "unpaired")

print("\n(3) An exit with no entry invents nothing")
r = H.pair_visits([ev("10:00:00", "out", "ghost")])
check("no visit invented", len(r["visits"]) == 0)
check("counted as an unpaired exit", r["unpaired_exits"] == 1)

print("\n(4) Trackless events pair FIFO and admit it")
r = H.pair_visits([ev("10:00:00", "in"), ev("10:01:00", "in"),
                   ev("10:06:00", "out"), ev("10:09:00", "out")])
check("two visits", len(r["visits"]) == 2)
check("both marked paired_by='fifo'", all(v["paired_by"] == "fifo" for v in r["visits"]))
check("oldest entry closed first (360s, then 480s)",
      sorted(v["dwell_seconds"] for v in r["visits"]) == [360, 480])

print("\n(5) Classification, not deletion")
r = H.pair_visits([ev("10:00:00", "in", "a"), ev("10:00:05", "out", "a"),      # 5s  passerby
                   ev("11:00:00", "in", "b"), ev("11:10:00", "out", "b"),      # 600s customer
                   ev("08:00:00", "in", "c"), ev("17:00:00", "out", "c")],     # 9h  staff
                  min_visit_seconds=20, max_visit_seconds=5400)
kinds = sorted(v["classification"] for v in r["visits"])
check("all three survive as rows", len(r["visits"]) == 3)
check("one passerby, one customer, one staff_or_merged",
      kinds == ["customer", "passerby", "staff_or_merged"])
check("the filtered counts are reported", r["filtered_short"] == 1 and r["filtered_long"] == 1)

print("\n(6) The hourly curve and the honest drift")
events = [ev("09:10:00", "in"), ev("09:20:00", "in"), ev("09:50:00", "out"),
          ev("14:00:00", "in"), ev("14:05:00", "in"), ev("14:07:00", "in"), ev("14:30:00", "out")]
s = H.traffic_summary(events, H.pair_visits(events)["visits"])
check("total in = 5", s["total_in"] == 5)
check("total out = 2", s["total_out"] == 2)
check("drift (missed exits) is stated, not hidden", s["drift"] == 3)
check("peak hour is 14:00", s["peak_hour"] == 14)
check("peak hour count is 3", s["peak_hour_in"] == 3)
check("the curve covers all 24 hours", len(s["hourly"]) == 24)
check("hour 9 shows 2 in / 1 out",
      s["hourly"][9]["in"] == 2 and s["hourly"][9]["out"] == 1)
check("net is a RUNNING total, not a per-hour one", s["hourly"][23]["net"] == 3)

print("\n(7) Presence roll-up is keyed for an idempotent upsert")
samples = [
    {"store_code": "S1", "camera_id": "cam-1", "local_date": D, "local_hour": 14,
     "cell_x": 3, "cell_y": 4, "occupancy": 12.5},
    {"store_code": "S1", "camera_id": "cam-1", "local_date": D, "local_hour": 14,
     "cell_x": 3, "cell_y": 4, "occupancy": 7.5},
    {"store_code": "S1", "camera_id": "cam-1", "local_date": D, "local_hour": 15,
     "cell_x": 3, "cell_y": 4, "occupancy": 4.0},
    {"store_code": "S1", "camera_id": "cam-1", "local_date": D, "local_hour": 14,
     "cell_x": 99, "cell_y": -5, "occupancy": 1.0},          # out of grid
]
cells = H.aggregate_presence(samples, 24, 16)
same_hour = [c for c in cells if c["local_hour"] == 14 and c["cell_x"] == 3 and c["cell_y"] == 4]
check("same (date,hour,cell) collapses to ONE row", len(same_hour) == 1)
check("occupancy sums to 20.0", same_hour[0]["occupancy"] == 20.0)
check("sample count is carried", same_hour[0]["samples"] == 2)
check("a different hour is a different row", len(cells) == 3)
clamped = [c for c in cells if c["cell_x"] == 23]
check("out-of-grid cell is CLAMPED into the grid, not dropped",
      len(clamped) == 1 and clamped[0]["cell_y"] == 0)
check("the grid the data was recorded on is stamped on every row",
      all(c["grid_cols"] == 24 and c["grid_rows"] == 16 for c in cells))

print("\n(8) The matrix the UI renders")
cells = ([{"cell_x": 1, "cell_y": 1, "local_hour": 14, "occupancy": 100.0}] +
         [{"cell_x": x, "cell_y": 2, "local_hour": 14, "occupancy": 5.0} for x in range(10)] +
         [{"cell_x": 0, "cell_y": 0, "local_hour": 18, "occupancy": 50.0}])
m = H.heat_matrix(cells, 12, 8)
check("matrix is rows x cols", len(m["matrix"]) == 8 and len(m["matrix"][0]) == 12)
check("the register cell is the max", m["max"] == 100.0)
check("p95 is reported so the ramp can be clipped", m["p95"] > 0)
check("hot cells are ranked", m["hot_cells"][0] == {"cell_x": 1, "cell_y": 1, "occupancy": 100.0})
check("total person-seconds add up", m["total_person_seconds"] == 200.0)
m14 = H.heat_matrix(cells, 12, 8, hours=[14])
check("an hour filter excludes the 18:00 cell", m14["matrix"][0][0] == 0.0)
check("and keeps the 14:00 cells", m14["matrix"][1][1] == 100.0)
dz = H.dead_zones(m)
check("dead zones are reported (the merchandising half of the map)", len(dz) > 0)
check("the hottest cell is never a dead zone", {"cell_x": 1, "cell_y": 1} not in dz)
check("an empty map has no dead zones rather than every cell",
      H.dead_zones(H.heat_matrix([], 12, 8)) == [])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
