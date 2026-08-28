"""Proof harness for the vision counting geometry (mod-vision, migration 900).

Run: python3 backend/harness_vision_geometry.py   (pure math — no network, no DB, no camera)

The door count is the number a manager will check against their POS ticket count, so the rules that
produce it have to be right in the awkward cases, not just the happy one. This proves:

  1. A track crossing the entrance line inward counts 'in'; the same track leaving counts 'out'.
  2. `inward` flips the answer — the operator, not the code, decides which side is the store.
  3. Walking AROUND the end of the line changes sides but is NOT a crossing.
  4. A sample sitting exactly ON the line is ambiguous and counts nothing (the next step resolves it).
  5. A zero-length or malformed line counts nothing instead of raising.
  6. Polygon containment, including a point exactly on an edge, and exclusion zones.
  7. The heat grid bins correctly, and the frame edge (x=1.0) lands in the LAST cell, not off-grid.
  8. foot_point() uses the BOTTOM of the detection box — the line that decides whether the heat map
     shows heat at the counter or on the wall behind it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import geometry as G   # noqa: E402

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


# A vertical counting line down the middle of the frame, drawn top -> bottom.
# For a top->bottom directed line, "left of the line" is the -x side (smaller x).
LINE = {"kind": "line", "geometry": {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}, "inward": "left"}

print("\n(1)/(2) Directional crossing, and the operator's `inward` choice decides the sign")
check("right -> left  is 'in'  when inward=left",
      G.crossing_direction(LINE, (0.7, 0.5), (0.3, 0.5)) == "in")
check("left  -> right is 'out' when inward=left",
      G.crossing_direction(LINE, (0.3, 0.5), (0.7, 0.5)) == "out")
RIGHT = {**LINE, "inward": "right"}
check("right -> left  is 'out' when inward=right",
      G.crossing_direction(RIGHT, (0.7, 0.5), (0.3, 0.5)) == "out")
check("left  -> right is 'in'  when inward=right",
      G.crossing_direction(RIGHT, (0.3, 0.5), (0.7, 0.5)) == "in")

print("\n(3) A side change that is not a doorway crossing counts nothing")
# The line spans y in [0.0, 1.0] at x=0.5, so give it a SHORT line and walk past its end.
SHORT = {"kind": "line", "geometry": {"x1": 0.5, "y1": 0.4, "x2": 0.5, "y2": 0.6}, "inward": "left"}
check("walking past the END of the line is not a crossing",
      G.crossing_direction(SHORT, (0.7, 0.9), (0.3, 0.9)) is None)
check("walking THROUGH the line is still a crossing",
      G.crossing_direction(SHORT, (0.7, 0.5), (0.3, 0.5)) == "in")
check("no side change at all counts nothing",
      G.crossing_direction(LINE, (0.7, 0.2), (0.8, 0.9)) is None)

print("\n(4) A sample exactly ON the line is ambiguous, not a crossing")
check("prev exactly on the line -> None", G.crossing_direction(LINE, (0.5, 0.5), (0.3, 0.5)) is None)
check("cur  exactly on the line -> None", G.crossing_direction(LINE, (0.7, 0.5), (0.5, 0.5)) is None)
check("the NEXT step then resolves it", G.crossing_direction(LINE, (0.5, 0.5), (0.3, 0.5)) is None
      and G.crossing_direction(LINE, (0.51, 0.5), (0.3, 0.5)) == "in")

print("\n(5) Malformed geometry degrades to 'counts nothing', never a crash")
check("zero-length line -> None",
      G.crossing_direction({"geometry": {"x1": 0.5, "y1": 0.5, "x2": 0.5, "y2": 0.5}},
                           (0.2, 0.5), (0.8, 0.5)) is None)
check("empty geometry   -> None", G.crossing_direction({"geometry": {}}, (0.2, 0.5), (0.8, 0.5)) is None)
check("garbage zone     -> None", G.crossing_direction(None, (0.2, 0.5), (0.8, 0.5)) is None)
check("line_points on a one-point 'line' -> None", G.line_points({"points": [[0.1, 0.1]]}) is None)

print("\n(6) Zones — containment, edges, and exclusions")
SQUARE = {"points": [[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]]}
check("point inside  -> True", G.point_in_polygon(SQUARE, (0.4, 0.4)) is True)
check("point outside -> False", G.point_in_polygon(SQUARE, (0.9, 0.4)) is False)
check("point exactly on an edge counts as inside", G.point_in_polygon(SQUARE, (0.2, 0.4)) is True)
check("point on a vertex counts as inside", G.point_in_polygon(SQUARE, (0.6, 0.6)) is True)
check("a 2-point 'polygon' contains nothing",
      G.point_in_polygon({"points": [[0.1, 0.1], [0.2, 0.2]]}, (0.15, 0.15)) is False)

ZONES = [
    {"kind": "polygon", "zone_key": "counter", "geometry": SQUARE, "is_active": True, "sort_order": 10},
    {"kind": "polygon", "zone_key": "accessories",
     "geometry": {"points": [[0.0, 0.0], [0.9, 0.0], [0.9, 0.9], [0.0, 0.9]]},
     "is_active": True, "sort_order": 20},
    {"kind": "polygon", "zone_key": "retired", "geometry": SQUARE, "is_active": False, "sort_order": 5},
    {"kind": "exclude", "zone_key": "back_office",
     "geometry": {"points": [[0.85, 0.0], [1.0, 0.0], [1.0, 0.3], [0.85, 0.3]]}, "is_active": True},
]
check("overlapping zones both reported, in sort order",
      G.zones_containing(ZONES, (0.4, 0.4)) == ["counter", "accessories"])
check("an INACTIVE zone is never reported", "retired" not in G.zones_containing(ZONES, (0.4, 0.4)))
# (0.95, 0.1) is inside the back-office exclusion and outside every polygon zone. Note the caller
# contract this documents: zones_containing() does NOT subtract exclusions — the analyzer calls
# excluded() FIRST and drops the detection, because an excluded point must vanish entirely rather
# than be attributed to whatever polygon also happens to contain it.
check("an exclusion zone is not reported as a normal zone",
      G.zones_containing(ZONES, (0.95, 0.1)) == [])
check("excluded() catches the back office", G.excluded(ZONES, (0.95, 0.1)) is True)
check("excluded() is False on the shop floor", G.excluded(ZONES, (0.4, 0.4)) is False)

print("\n(7) Heat grid binning, including the frame edge")
check("origin lands in cell (0,0)", G.grid_cell((0.0, 0.0), 24, 16) == (0, 0))
check("mid-frame lands mid-grid", G.grid_cell((0.5, 0.5), 24, 16) == (12, 8))
check("x=1.0 lands in the LAST column, not off-grid", G.grid_cell((1.0, 1.0), 24, 16) == (23, 15))
check("an out-of-range point is clamped, not dropped", G.grid_cell((5.0, -3.0), 24, 16) == (23, 0))
check("a 1x1 grid is legal", G.grid_cell((0.7, 0.3), 1, 1) == (0, 0))
check("a zero-size grid degrades to 1x1 instead of dividing by zero",
      G.grid_cell((0.7, 0.3), 0, 0) == (0, 0))

print("\n(8) foot_point uses the BOTTOM of the box, not its centre")
box = {"x": 0.40, "y": 0.20, "w": 0.10, "h": 0.40}   # a standing person, head at y=0.20, feet at 0.60
fx, fy = G.foot_point(box)
check("x is the horizontal midpoint", abs(fx - 0.45) < 1e-9)
check("y is the BOTTOM edge (feet), not the centre", abs(fy - 0.60) < 1e-9)
check("the centre would have been 0.40 — a whole metre of error in a angled view",
      abs((box["y"] + box["h"] / 2) - 0.40) < 1e-9)
check("a box overflowing the frame is clamped into it",
      G.foot_point({"x": 0.9, "y": 0.8, "w": 0.4, "h": 0.5}) == (1.0, 1.0))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
