"""Pure geometry for the vision module — line crossing, zone containment, and heat-grid binning.

Deliberately dependency-free (no numpy, no OpenCV) and side-effect free, so the counting rules that
decide "a customer walked in" can be proven exhaustively without a camera, a database or a network.
See `backend/harness_vision_geometry.py`.

COORDINATE SYSTEM
─────────────────
Everything is in NORMALIZED IMAGE COORDINATES: x and y in [0, 1], origin at the TOP-LEFT of the frame
(the convention every detector already emits). Zones drawn by an operator are stored the same way, so
swapping a 1080p camera for a 4K one — or the analyzer downscaling frames for speed — cannot silently
invalidate a drawing that took someone twenty minutes to place.

WHY A LINE CROSSING AND NOT "COUNT THE BLOBS"
─────────────────────────────────────────────
Counting people visible in frame gives a number that swings with occlusion and re-detection: one
customer standing at the counter for ten minutes can be counted, lost, and re-counted a dozen times.
A DIRECTIONAL LINE CROSSING is a state CHANGE — a track's foot point was on one side of the entrance
line and is now on the other — so a person entering counts exactly once no matter how long they stay
and how many times the detector loses them mid-store. That is the difference between a door count a
manager can trust against the POS ticket count and one they cannot.
"""

EPS = 1e-9


def clamp01(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _pt(p):
    """Accept a point as (x, y), [x, y] or {"x":…, "y":…} — the analyzer, the UI drawing tool and the
    stored JSON all use a different one of these, and normalizing here beats three call-site branches."""
    if isinstance(p, dict):
        return clamp01(p.get("x")), clamp01(p.get("y"))
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return clamp01(p[0]), clamp01(p[1])
    return 0.0, 0.0


def _cross(ax, ay, bx, by, px, py) -> float:
    """Signed area of the triangle (a, b, p) x2. > 0 = p is LEFT of a->b, < 0 = right, 0 = collinear."""
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def line_points(geometry: dict):
    """((x1,y1),(x2,y2)) from a stored line geometry. Returns None if the geometry is unusable, so a
    half-drawn line in the DB degrades to "this camera counts nothing" instead of raising."""
    if not isinstance(geometry, dict):
        return None
    if all(k in geometry for k in ("x1", "y1", "x2", "y2")):
        a = (clamp01(geometry["x1"]), clamp01(geometry["y1"]))
        b = (clamp01(geometry["x2"]), clamp01(geometry["y2"]))
    else:
        pts = geometry.get("points") or []
        if len(pts) < 2:
            return None
        a, b = _pt(pts[0]), _pt(pts[1])
    if abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) < EPS:
        return None          # zero-length line: no side is defined, so no crossing is either
    return a, b


def side_of(geometry: dict, point) -> int:
    """+1 if the point is LEFT of the directed line, -1 if RIGHT, 0 if on it (within EPS)."""
    lp = line_points(geometry)
    if not lp:
        return 0
    (ax, ay), (bx, by) = lp
    px, py = _pt(point)
    c = _cross(ax, ay, bx, by, px, py)
    if abs(c) <= EPS:
        return 0
    return 1 if c > 0 else -1


def _segments_intersect(p1, p2, p3, p4) -> bool:
    """True if segment p1->p2 actually crosses segment p3->p4. Needed on top of the side test: a track
    can move from left of the entrance line to right of it while walking around the END of the line
    (past the doorframe, along the window), which is a side change but not a doorway crossing."""
    d1 = _cross(p3[0], p3[1], p4[0], p4[1], p1[0], p1[1])
    d2 = _cross(p3[0], p3[1], p4[0], p4[1], p2[0], p2[1])
    d3 = _cross(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1])
    d4 = _cross(p1[0], p1[1], p2[0], p2[1], p4[0], p4[1])
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
       ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True
    # Collinear/touching cases: treat "an endpoint lies on the other segment" as a crossing, so a
    # track that stops exactly on the threshold and then continues is not silently dropped.
    for d, (a, b, p) in ((d1, (p3, p4, p1)), (d2, (p3, p4, p2)),
                         (d3, (p1, p2, p3)), (d4, (p1, p2, p4))):
        if abs(d) <= EPS and _on_segment(a, b, p):
            return True
    return False


def _on_segment(a, b, p) -> bool:
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS and
            min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def crossing_direction(zone: dict, prev_point, cur_point):
    """'in' | 'out' | None for one track step against one counting line.

    `zone['inward']` names which side of the directed line A->B is INSIDE the store: 'left' (default)
    or 'right'. An operator draws the line across the doorway and picks the side; nothing here assumes
    a camera orientation, because half of these cameras are mounted facing the door and half facing
    away from it.

    Returns None when the track did not change sides, when it changed sides without crossing the
    drawn segment (walked around the end of the line), or when either sample sits exactly on the line
    — an on-line sample is ambiguous and counting it would double-count the next step that resolves it.
    """
    geometry = (zone or {}).get("geometry") or {}
    lp = line_points(geometry)
    if not lp:
        return None
    a, b = lp
    p_prev, p_cur = _pt(prev_point), _pt(cur_point)
    s_prev, s_cur = side_of(geometry, p_prev), side_of(geometry, p_cur)
    if s_prev == 0 or s_cur == 0 or s_prev == s_cur:
        return None
    if not _segments_intersect(p_prev, p_cur, a, b):
        return None
    inward = ((zone or {}).get("inward") or "left").strip().lower()
    inward_side = 1 if inward == "left" else -1
    return "in" if s_cur == inward_side else "out"


def polygon_points(geometry: dict):
    """[(x,y), …] from a stored polygon geometry, or [] when it is not a usable polygon."""
    if not isinstance(geometry, dict):
        return []
    pts = geometry.get("points") or []
    out = [_pt(p) for p in pts]
    return out if len(out) >= 3 else []


def point_in_polygon(geometry: dict, point) -> bool:
    """Ray-casting containment test. A point exactly on an edge counts as inside — a shopper standing
    on the boundary of the accessory-wall zone is at the accessory wall."""
    pts = polygon_points(geometry)
    if not pts:
        return False
    px, py = _pt(point)
    n = len(pts)
    inside = False
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if abs(_cross(x1, y1, x2, y2, px, py)) <= EPS and _on_segment((x1, y1), (x2, y2), (px, py)):
            return True                                   # on an edge
        if (y1 > py) != (y2 > py):
            xint = x1 + (py - y1) * (x2 - x1) / ((y2 - y1) or EPS)
            if px < xint:
                inside = not inside
    return inside


def zones_containing(zones, point):
    """The zone_keys of every ACTIVE polygon zone containing the point, in sort order. Exclusion zones
    are not returned here — `excluded()` answers that question, because an excluded point must be
    dropped entirely rather than attributed to whatever else also contains it."""
    out = []
    for z in sorted(zones or [], key=lambda z: (z.get("sort_order") or 100)):
        if not z.get("is_active", True) or (z.get("kind") or "polygon") != "polygon":
            continue
        if point_in_polygon(z.get("geometry") or {}, point):
            out.append(z.get("zone_key") or z.get("name"))
    return out


def excluded(zones, point) -> bool:
    """True if the point falls in any active exclusion zone — the back office visible through a
    doorway, the pavement through the front window. Those detections are real people and would
    otherwise inflate every number in the module, so they are dropped before anything else runs."""
    for z in zones or []:
        if not z.get("is_active", True) or (z.get("kind") or "") != "exclude":
            continue
        if point_in_polygon(z.get("geometry") or {}, point):
            return True
    return False


def grid_cell(point, cols: int, rows: int):
    """(cell_x, cell_y) for a normalized point on a cols x rows heat grid. x=1.0 lands in the LAST
    column rather than one past the end — the clamp matters because a detection at the frame edge is
    common (someone leaving) and an off-grid cell would be dropped by the aggregate's unique index."""
    cols = max(1, int(cols or 1))
    rows = max(1, int(rows or 1))
    px, py = _pt(point)
    cx = min(cols - 1, int(px * cols))
    cy = min(rows - 1, int(py * rows))
    return cx, cy


def foot_point(box) -> tuple:
    """The floor contact point of a detection box — the midpoint of its BOTTOM edge, not its centre.

    This is the single most important line in the heat map. A person's box centre sits at chest height
    and therefore lands, in a ceiling-angled view, roughly a metre BEHIND where they are standing; a
    heat map built on box centres shows heat on the wall behind the counter instead of at the counter.
    `box` is {"x":…, "y":…, "w":…, "h":…} in normalized coordinates.
    """
    if not isinstance(box, dict):
        return _pt(box)
    x = clamp01(box.get("x"))
    y = clamp01(box.get("y"))
    w = clamp01(box.get("w"))
    h = clamp01(box.get("h"))
    return clamp01(x + w / 2.0), clamp01(y + h)
