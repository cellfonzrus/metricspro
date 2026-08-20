"""Customer traffic and heat-map aggregation — turning edge events into the two numbers a store
manager actually acts on: how many people came in, and where in the store they stood.

THREE LAYERS, ON PURPOSE
────────────────────────
  vision_traffic_event    one row per directional crossing of the entrance line      (raw, 90d)
  vision_visit            one row per customer, entry paired to exit, with dwell     (derived, 90d)
  vision_heat_cell        person-seconds per grid cell per hour                      (rolled up, 400d)

The pairing and the roll-up both live HERE, as pure functions over lists of dicts, rather than in SQL
or in the router — so `backend/harness_vision_heatmap.py` can prove the counting rules against
hand-written event sequences including the ugly ones (an exit with no entry, a track that re-enters,
a staff member who crosses the line thirty times a shift).

THE TWO RULES THAT KEEP THE COUNT HONEST
────────────────────────────────────────
1. **An unpaired entry is still a visit.** A track that entered and was never seen leaving is real —
   the detector lost them, or they left through a second door. Dropping those undercounts traffic on
   exactly the busiest days. They are recorded with `exited_at = NULL` and excluded from dwell
   statistics, never from the door count.
2. **A visit outside the configured duration band is not a customer.** Under `min_visit_seconds` is
   someone walking past the doorway; over `max_visit_seconds` is a staff member, or a paired-up track
   that is actually two different people. Both are classified rather than deleted, so the operator can
   see what was filtered and tune the band instead of wondering where their traffic went.
"""
from datetime import datetime, timezone


def _dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def pair_visits(events, min_visit_seconds: int = 20, max_visit_seconds: int = 5400) -> dict:
    """Pair 'in' crossings with their 'out' crossings into visits.

    `events`: [{occurred_at, direction, track_key, store_code, local_date, camera_id}, …]
    Returns {"visits": [...], "unpaired_exits": n, "filtered_short": n, "filtered_long": n}.

    Pairing is BY TRACK KEY when the analyzer supplied one (it tracked the same person across the
    line both ways), and otherwise FIFO per store — the oldest open entry is closed by the next exit.
    FIFO is wrong for any individual customer and right in aggregate, which is the correct trade for a
    dwell-time average; visits closed by FIFO are marked `paired_by='fifo'` so a report can exclude
    them if the operator would rather have fewer, better numbers.
    """
    rows = sorted([e for e in (events or []) if _dt(e.get("occurred_at"))],
                  key=lambda e: _dt(e["occurred_at"]))
    open_by_track, fifo_open, visits = {}, [], []
    unpaired_exits = 0

    for e in rows:
        direction = (e.get("direction") or "").strip().lower()
        track = e.get("track_key")
        if direction == "in":
            if track:
                open_by_track[track] = e
            else:
                fifo_open.append(e)
            continue
        if direction != "out":
            continue
        entry = None
        if track and track in open_by_track:
            entry = open_by_track.pop(track)
            paired_by = "track"
        elif fifo_open:
            entry = fifo_open.pop(0)
            paired_by = "fifo"
        elif open_by_track:
            oldest = min(open_by_track, key=lambda k: _dt(open_by_track[k]["occurred_at"]))
            entry = open_by_track.pop(oldest)
            paired_by = "fifo"
        if not entry:
            unpaired_exits += 1
            continue
        visits.append(_visit(entry, e, paired_by))

    for e in list(open_by_track.values()) + fifo_open:
        visits.append(_visit(e, None, "unpaired"))     # rule 1: an unpaired entry is still a visit

    short = long = 0
    for v in visits:
        d = v.get("dwell_seconds")
        if d is None:
            v["classification"] = "unpaired"
            continue
        if d < min_visit_seconds:
            v["classification"] = "passerby"
            short += 1
        elif d > max_visit_seconds:
            v["classification"] = "staff_or_merged"
            long += 1
        else:
            v["classification"] = "customer"

    visits.sort(key=lambda v: v["entered_at"])
    return {"visits": visits, "unpaired_exits": unpaired_exits,
            "filtered_short": short, "filtered_long": long}


def _visit(entry, exit_event, paired_by) -> dict:
    a = _dt(entry.get("occurred_at"))
    b = _dt((exit_event or {}).get("occurred_at")) if exit_event else None
    return {
        "store_code": entry.get("store_code"),
        "local_date": entry.get("local_date"),
        "camera_id": entry.get("camera_id"),
        "track_key": entry.get("track_key"),
        "entered_at": a.isoformat() if a else None,
        "exited_at": b.isoformat() if b else None,
        "dwell_seconds": int((b - a).total_seconds()) if (a and b and b >= a) else None,
        "paired_by": paired_by,
    }


def traffic_summary(events, visits=None) -> dict:
    """The door-count block: totals, the hourly in/out curve, the peak hour, and average dwell.

    `net_in_store` is a running in-minus-out and is reported as a CURVE rather than a single number
    because the running value drifts across a day (every missed exit pushes it up by one). A manager
    reading "18 people currently in a store with two chairs" would rightly stop trusting the module,
    so the honest presentation is the shape of the curve, and the drift is surfaced explicitly."""
    hourly = {}
    total_in = total_out = 0
    for e in events or []:
        d = (e.get("direction") or "").strip().lower()
        h = e.get("local_hour")
        try:
            h = int(h)
        except (TypeError, ValueError):
            dt = _dt(e.get("occurred_at"))
            h = dt.hour if dt else 0
        b = hourly.setdefault(h, {"hour": h, "in": 0, "out": 0})
        if d == "in":
            b["in"] += 1
            total_in += 1
        elif d == "out":
            b["out"] += 1
            total_out += 1

    curve, running = [], 0
    for h in range(24):
        b = hourly.get(h, {"hour": h, "in": 0, "out": 0})
        running += b["in"] - b["out"]
        curve.append({**b, "net": running})

    dwells = [v["dwell_seconds"] for v in (visits or [])
              if v.get("dwell_seconds") is not None and v.get("classification") == "customer"]
    dwells.sort()
    peak = max(curve, key=lambda b: b["in"]) if curve else None
    return {
        "total_in": total_in,
        "total_out": total_out,
        "drift": total_in - total_out,          # missed exits, stated rather than hidden
        "hourly": curve,
        "peak_hour": peak["hour"] if peak and peak["in"] else None,
        "peak_hour_in": peak["in"] if peak else 0,
        "customers": len([v for v in (visits or []) if v.get("classification") == "customer"]),
        "avg_dwell_seconds": round(sum(dwells) / len(dwells)) if dwells else None,
        "median_dwell_seconds": dwells[len(dwells) // 2] if dwells else None,
    }


def aggregate_presence(samples, grid_cols: int, grid_rows: int) -> list:
    """Roll per-sample occupancy up into `core.vision_heat_cell` rows, keyed exactly like that
    table's unique index so the write is a clean upsert and re-running the aggregator is idempotent.

    Samples whose cell falls outside the configured grid are CLAMPED into it rather than dropped: an
    off-grid sample means the analyzer and the tenant config disagree about resolution, and losing a
    day of heat data to that is worse than putting the edge column's traffic in the edge column."""
    cols = max(1, int(grid_cols or 1))
    rows = max(1, int(grid_rows or 1))
    out = {}
    for s in samples or []:
        try:
            cx = min(cols - 1, max(0, int(s.get("cell_x"))))
            cy = min(rows - 1, max(0, int(s.get("cell_y"))))
        except (TypeError, ValueError):
            continue
        try:
            hour = int(s.get("local_hour"))
        except (TypeError, ValueError):
            dt = _dt(s.get("sampled_at"))
            hour = dt.hour if dt else 0
        key = (s.get("store_code"), s.get("camera_id"), s.get("local_date"), hour, cx, cy)
        cell = out.setdefault(key, {
            "store_code": key[0], "camera_id": key[1], "local_date": key[2], "local_hour": hour,
            "cell_x": cx, "cell_y": cy, "grid_cols": cols, "grid_rows": rows,
            "occupancy": 0.0, "samples": 0,
        })
        try:
            cell["occupancy"] += float(s.get("occupancy") or 0)
        except (TypeError, ValueError):
            pass
        cell["samples"] += 1
    for c in out.values():
        c["occupancy"] = round(c["occupancy"], 3)
    return sorted(out.values(), key=lambda c: (str(c["local_date"]), c["local_hour"],
                                               c["cell_y"], c["cell_x"]))


def heat_matrix(cells, grid_cols: int, grid_rows: int, hours=None) -> dict:
    """The payload the UI renders: a dense rows x cols matrix of person-seconds, plus the max and the
    percentile bands the colour ramp uses.

    Normalising by the MAX alone makes every store's map look identical (one scorching cell at the
    register, everything else black). p95 is returned alongside it so the frontend can clip the ramp
    at the 95th percentile — the register still reads as the hottest cell, but the difference between
    the accessory wall and the empty corner stays visible, which is the entire point of the map."""
    cols = max(1, int(grid_cols or 1))
    rows = max(1, int(grid_rows or 1))
    want = set(int(h) for h in hours) if hours else None
    matrix = [[0.0] * cols for _ in range(rows)]
    total = 0.0
    for c in cells or []:
        try:
            if want is not None and int(c.get("local_hour")) not in want:
                continue
            cx, cy = int(c.get("cell_x")), int(c.get("cell_y"))
            v = float(c.get("occupancy") or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= cx < cols and 0 <= cy < rows:
            matrix[cy][cx] += v
            total += v
    flat = sorted(v for row in matrix for v in row if v > 0)
    p95 = flat[min(len(flat) - 1, int(len(flat) * 0.95))] if flat else 0.0
    hot = sorted(
        ({"cell_x": x, "cell_y": y, "occupancy": round(matrix[y][x], 2)}
         for y in range(rows) for x in range(cols) if matrix[y][x] > 0),
        key=lambda c: -c["occupancy"])[:5]
    return {
        "grid_cols": cols, "grid_rows": rows,
        "matrix": [[round(v, 2) for v in row] for row in matrix],
        "max": round(max(flat) if flat else 0.0, 2),
        "p95": round(p95, 2),
        "total_person_seconds": round(total, 1),
        "occupied_cells": len(flat),
        "hot_cells": hot,
    }


def dead_zones(matrix_payload: dict, threshold_ratio: float = 0.05) -> list:
    """Cells with essentially no traffic, as [{cell_x, cell_y}]. This is the half of the heat map that
    makes an operator money: a display table nobody walks past is a merchandising decision, and it is
    invisible in a report that only ranks the hottest cells."""
    m = (matrix_payload or {}).get("matrix") or []
    peak = (matrix_payload or {}).get("p95") or (matrix_payload or {}).get("max") or 0
    if peak <= 0:
        return []
    cut = peak * threshold_ratio
    return [{"cell_x": x, "cell_y": y}
            for y, row in enumerate(m) for x, v in enumerate(row) if v <= cut]
