"""Busy hours — turning raw camera events into a curve that can be read honestly.

WHY THIS FILE EXISTS, and it is not for the arithmetic
    The first version of busy hours summed person events per local hour and drew bars. Then the
    owner ran the numbers against a real estate — 21 cameras, 18 stores, 48 hours — and the summed
    curve turned out to be measuring something other than what it said.

    From that data:
      * event volume per camera ranged from 0 to 52 over the same two days, a 26x spread;
      * the five busiest cameras produced 45% of every event in the company;
      * one camera alone produced 11% of them, and 23% of ITS events were between midnight and 6am,
        which is the signature of a lens pointed at a street rather than a shop floor.

    So a summed "all stores" curve is largely the shape of a few chatty cameras' days. A store with
    two cameras outweighs a store with one; a store whose camera faces the pavement outweighs a
    store twice as busy whose camera faces the counter. Nothing about that is visible in a bar chart
    labelled "sightings", which is what makes it worth a module rather than a comment.

THE TWO MEASURES, and when each is honest
    events   raw count. Comparable HOUR TO HOUR WITHIN ONE STORE, because that store's camera set is
             fixed across the day — whatever bias its cameras carry, they carry it at 9am and at
             5pm alike. Meaningless ACROSS stores, for the reasons above.
    index    each store's own day normalised to sum to 100, then averaged over stores with EQUAL
             WEIGHT. Answers "what shape is a typical store's day", which is the estate-level
             question a staffing decision actually turns on, and which no amount of summing can
             answer.

    Both are always computed. The caller picks by scope rather than the aggregator guessing: one
    store gets counts, several get the index. Returning only one would make the page's honesty
    depend on a mode flag that a future caller could forget to set.

WHAT NEITHER MEASURE BECOMES
    A customer count. Google reports that a person was SEEN — never a direction, never an identity,
    and staff trip the same detector as shoppers. Migration 907's header sets that out; this module
    keeps it true by never emitting a field that could be read as footfall, and by surfacing the
    per-camera concentration so a dominant camera is visible on the page instead of only in a SQL
    session.

PURE. No database, no network, no clock — (rows) -> (curve), so harness_vision_busy.py can prove
the weighting offline. The weighting is the whole point of the module, and a weighting bug looks
exactly like a quiet Tuesday.
"""

# A single camera contributing more than this share of a store's events IS that store's curve.
#
# Set high on purpose. An earlier draft used a third, on the reasoning that three cameras split
# evenly at 33% — which flags every EVEN two-camera store, since an even split of two is 50%. A
# warning that fires on the healthy case teaches people to ignore it, so the bar is the point at
# which the other cameras stop contributing anything: above 80%, whatever else is mounted in that
# store is rounding error. The real case that prompted this sat at 96%.
DOMINANT_SHARE = 0.80

# Below this, a store's normalised shape is noise rather than a day — a handful of events spread
# over 24 hours says nothing about when anybody shops. Such stores are counted and named, but kept
# out of the index so they cannot swing the estate curve with three sightings.
MIN_EVENTS_FOR_INDEX = 10


def _hour_of(row) -> int:
    """The stored local hour, or -1 when it is absent or out of range.

    Rows come from a table with a CHECK constraint on this column, so -1 should be unreachable —
    but a NULL from an older row or a widened schema would otherwise become hour 0 and pile
    midnight with everything unparseable."""
    try:
        h = int(row.get("local_hour"))
    except (TypeError, ValueError):
        return -1
    return h if 0 <= h <= 23 else -1


def aggregate(rows, cameras=None):
    """Rows of person events -> the busy-hours payload.

    `rows`    : [{store_code, local_date, local_hour, device_name}], already tenant- and
                scope-filtered by the caller. This function trusts the filtering and does none.
    `cameras` : optional [{device_name, display_name, store_code}] so contributions can be named.
                A camera that has been deleted since its events landed simply keeps its device id.
    """
    rows = rows or []
    by_store = {}          # store -> hour -> count
    by_camera = {}         # device_name -> count
    dates = set()
    hours = [0] * 24
    total = 0

    for r in rows:
        h = _hour_of(r)
        if h < 0:
            continue
        store = (r.get("store_code") or "").strip() or "—"
        by_store.setdefault(store, [0] * 24)[h] += 1
        dev = (r.get("device_name") or "").strip()
        if dev:
            by_camera[dev] = by_camera.get(dev, 0) + 1
        if r.get("local_date"):
            dates.add(r["local_date"])
        hours[h] += 1
        total += 1

    days_seen = len(dates) or 1

    # ── THE INDEX ────────────────────────────────────────────────────────────────────────────────
    # Each store's day normalised to sum to 100, then averaged with EQUAL WEIGHT. A store with two
    # chatty cameras and a store with one quiet one count the same, which is the entire correction:
    # summing lets camera count and camera chattiness masquerade as customer volume.
    scored = {s: hrs for s, hrs in by_store.items() if sum(hrs) >= MIN_EVENTS_FOR_INDEX}
    index = [0.0] * 24
    if scored:
        for hrs in scored.values():
            t = sum(hrs)
            for h in range(24):
                index[h] += (hrs[h] / t) * 100.0
        index = [round(v / len(scored), 2) for v in index]

    # ── PER-CAMERA CONCENTRATION ─────────────────────────────────────────────────────────────────
    # Surfaced on the page so a street-facing camera is visible where the numbers are read, rather
    # than only to somebody who thinks to write the query.
    names = {}
    stores_of = {}
    for c in (cameras or []):
        dev = (c.get("device_name") or "").strip()
        if dev:
            names[dev] = (c.get("display_name") or "").strip() or dev
            stores_of[dev] = (c.get("store_code") or "").strip()
    contributions = sorted(
        ({"device_name": d, "name": names.get(d, d), "store_code": stores_of.get(d, ""),
          "events": n, "share": round(n / total, 4) if total else 0.0}
         for d, n in by_camera.items()),
        key=lambda c: -c["events"])

    # A store whose curve is really one camera's curve. Reported PER STORE, not company-wide: one
    # camera being 11% of a 21-camera company is unremarkable, while the same camera being 96% of
    # its own store means that store's bars are that lens and nothing else.
    dominant = []
    for store, hrs in by_store.items():
        t = sum(hrs)
        if t < MIN_EVENTS_FOR_INDEX:
            continue
        here = [c for c in contributions if c["store_code"] == store]
        if not here:
            continue
        top = here[0]
        s = top["events"] / t
        if s >= DOMINANT_SHARE and len(here) > 1:
            dominant.append({"store_code": store, "name": top["name"],
                             "share": round(s, 3), "events": top["events"], "of": t})
    dominant.sort(key=lambda d: -d["share"])

    return {
        "days_with_data": days_seen,
        "events": total,
        "stores": len(by_store),
        "stores_scored": len(scored),
        "stores_too_quiet": len(by_store) - len(scored),
        "by_hour": [{"hour": h,
                     "events": hours[h],
                     "per_day": round(hours[h] / days_seen, 1),
                     "index": index[h]} for h in range(24)],
        "cameras": contributions[:12],
        "dominant": dominant[:5],
    }


def peak(by_hour, measure: str):
    """The busiest hour under `measure`, or None when nothing was seen.

    Ties go to the earlier hour — a manager told two hours tie still has to pick one, and the
    earlier is where the day's staffing decision gets made. Tie-broken on the HOUR rather than on
    list order so a reordered payload cannot move the headline."""
    key = "index" if measure == "index" else "events"
    best = None
    for row in (by_hour or []):
        v = row.get(key) or 0
        if v <= 0:
            continue
        if best is None or v > (best.get(key) or 0) or (
                v == (best.get(key) or 0) and row.get("hour", 99) < best.get("hour", 99)):
            best = row
    return best


def measure_for(store_code, stores: int) -> str:
    """Which measure the page should draw.

    ONE store — named, or the only one in the caller's reach — gets raw counts: its camera set is
    constant across the day, so hour-to-hour comparison is fair. SEVERAL stores get the index,
    because summing across stores lets camera count and camera chattiness impersonate customer
    volume. Deciding here rather than in the page means the two cannot disagree."""
    if str(store_code or "").strip():
        return "events"
    return "events" if int(stores or 0) <= 1 else "index"


def caveat(measure: str, stores: int) -> str:
    """What the page must say about the number it is showing, in the operator's terms.

    Not a disclaimer — a description. Someone who reads only this sentence and the chart must come
    away with the right idea of what they are looking at, which is why it names the specific way
    each measure misleads rather than a general warning about accuracy."""
    if measure == "index":
        return ("Each store's day counts equally here, whatever its camera count — so this is the "
                "shape of a typical store's day, not a total. Summing raw sightings would let a "
                "store with more cameras, or a chattier camera, outrank one that is genuinely "
                "busier.")
    if int(stores or 0) > 1:
        return ("Raw sightings for the stores you can see. Comparable hour to hour, but not "
                "between stores — a store with more cameras reports more sightings for the same "
                "number of people.")
    return ("Raw sightings from this store's cameras. Comparable hour to hour, because the same "
            "cameras are watching all day.")
