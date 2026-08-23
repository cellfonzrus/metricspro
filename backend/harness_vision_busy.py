"""Proves the busy-hours weighting offline — no network, no DB, no clock.

WHY THIS EXISTS
    The first version of busy hours summed person events per hour and drew bars. It was wrong, and
    it was wrong in the way that does not announce itself: the chart rendered, the numbers were
    real, and the curve was mostly the shape of a few chatty cameras rather than of anybody's
    trading day. It took running the query against a live estate to see it.

    So the fixtures below are that estate, not invented ones — 21 cameras over 18 stores, the 26x
    spread between the busiest and quietest camera, the one camera carrying 96% of its store, and
    the street-facing lens with a quarter of its events after midnight. If the weighting regresses,
    these fail.

Run:  python3 backend/harness_vision_busy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.vision import busy as B   # noqa: E402

_pass = _fail = 0


def ck(label, cond, extra=None):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f"  XX  {label}" + (f"  {extra!r}" if extra is not None else ""))


def eq(label, got, want):
    ck(label, got == want, None if got == want else f"got {got!r} want {want!r}")


def ev(store, hour, device, date="2026-08-22"):
    return {"store_code": store, "local_hour": hour, "device_name": device, "local_date": date}


def spread(store, device, per_hour, date="2026-08-22"):
    """per_hour: {hour: count} -> a list of event rows."""
    out = []
    for h, n in per_hour.items():
        out += [ev(store, h, device, date) for _ in range(n)]
    return out


print("── an empty estate says nothing rather than something wrong ─────────────────────────────")
z = B.aggregate([])
eq("no events", z["events"], 0)
eq("no stores", z["stores"], 0)
eq("24 hours are still returned, so the chart has an axis", len(z["by_hour"]), 24)
ck("every hour is zero", all(r["events"] == 0 and r["index"] == 0 for r in z["by_hour"]))
eq("days_with_data never divides by zero", z["days_with_data"], 1)
eq("no camera contributions", z["cameras"], [])
eq("no dominant camera", z["dominant"], [])
eq("peak of nothing is nothing", B.peak(z["by_hour"], "events"), None)
eq("...under either measure", B.peak(z["by_hour"], "index"), None)
z2 = B.aggregate(None)
eq("None rows behave like no rows", z2["events"], 0)

print("── THE BUG THIS MODULE EXISTS FOR ───────────────────────────────────────────────────────")
# Two stores. QUIET has one camera and a genuine afternoon peak. CHATTY has one camera that fires
# constantly all day — the real one produced 52 events in 48h against a sibling's 2, and 23% of them
# after midnight. Summed, CHATTY's flat noise buries QUIET's real curve. That is the defect: the
# estate chart showed the chatty camera's day and called it everyone's.
# QUIET: one camera, a real trading day peaking at 4pm.
quiet = spread("QUIET", "cam-quiet", {9: 1, 10: 2, 11: 2, 15: 8, 16: 9, 17: 3})      # 25 events
# CHATTY: one camera facing the pavement. Fires round the clock and hardest at 8pm, when the street
# is busy and the shop is shut. 163 events to QUIET's 25 — the real pair was 52 against 2.
street = {h: 6 for h in range(24)}
street[20] = 25
chatty = spread("CHATTY", "cam-street", street)                                      # 163 events
both = B.aggregate(quiet + chatty)

summed_peak = max(range(24), key=lambda h: both["by_hour"][h]["events"])
eq("summing puts the estate peak at the STREET camera's hour, which is the bug", summed_peak, 20)
ck("...and 8pm is not when the shop was busy", both["by_hour"][20]["events"] > both["by_hour"][16]["events"],
   (both["by_hour"][20]["events"], both["by_hour"][16]["events"]))
index_peak = B.peak(both["by_hour"], "index")
eq("the INDEX finds the hour a real store is actually busy", index_peak["hour"], 16)
ck("...and demotes the street camera's hour below it",
   both["by_hour"][16]["index"] > both["by_hour"][20]["index"],
   (both["by_hour"][16]["index"], both["by_hour"][20]["index"]))
ck("the raw count still reports honestly for whoever wants it",
   both["events"] == 25 + 163, both["events"])

print("── each store counts once, whatever its camera count ────────────────────────────────────")
# The real estate had three stores with two cameras and fifteen with one. Two cameras must not make
# a store count twice.
one_cam = spread("A", "a1", {10: 5, 15: 5})
two_cam = spread("B", "b1", {10: 5, 15: 5}) + spread("B", "b2", {10: 5, 15: 5})
r = B.aggregate(one_cam + two_cam)
eq("two stores", r["stores"], 2)
# Both stores have an identical SHAPE (half at 10, half at 15), so the index must be identical too
# — B having twice the events changes nothing.
eq("identical shapes give identical index at 10", r["by_hour"][10]["index"], 50.0)
eq("identical shapes give identical index at 15", r["by_hour"][15]["index"], 50.0)
ck("...while the raw counts do differ, as they should",
   r["by_hour"][10]["events"] == 15, r["by_hour"][10]["events"])
# Now give B a different shape and check it moves the index by exactly half.
skew = B.aggregate(one_cam + spread("B", "b1", {10: 10}))
eq("one store peaking at 10, one split -> index at 10 is (50+100)/2", skew["by_hour"][10]["index"], 75.0)
eq("...and 15 is (50+0)/2", skew["by_hour"][15]["index"], 25.0)

print("── the index describes a day, so it sums to 100 ─────────────────────────────────────────")
for name, payload in (("two stores", r), ("skewed", skew), ("quiet+chatty", both)):
    total = round(sum(x["index"] for x in payload["by_hour"]), 1)
    ck(f"{name}: the index sums to 100", abs(total - 100.0) < 0.5, total)
single = B.aggregate(spread("A", "a1", {9: 3, 17: 7}))
eq("a single store's index is just its own distribution at 9", single["by_hour"][9]["index"], 30.0)
eq("...and at 17", single["by_hour"][17]["index"], 70.0)

print("── a store with three sightings cannot swing the estate ─────────────────────────────────")
# MIN_EVENTS_FOR_INDEX. A store seen twice all week has no day shape; without this, its two events
# would each be worth 50% of a store's curve and would move the whole estate.
noisy = B.aggregate(spread("REAL", "r1", {10: 20, 16: 20}) + spread("TINY", "t1", {3: 2}))
eq("both stores are counted", noisy["stores"], 2)
eq("only the one with enough data is scored", noisy["stores_scored"], 1)
eq("the quiet one is reported, not hidden", noisy["stores_too_quiet"], 1)
eq("3am does not become a peak because of two events", noisy["by_hour"][3]["index"], 0.0)
ck("...but its events are still in the raw total", noisy["by_hour"][3]["events"] == 2)
eq("the real store's shape is undiluted", noisy["by_hour"][10]["index"], 50.0)
# Exactly at the threshold the store IS scored — an off-by-one here silently drops small stores.
at_min = B.aggregate(spread("BIG", "b", {12: 50}) + spread("EDGE", "e", {8: B.MIN_EVENTS_FOR_INDEX}))
eq(f"a store with exactly {B.MIN_EVENTS_FOR_INDEX} events is scored", at_min["stores_scored"], 2)
below = B.aggregate(spread("BIG", "b", {12: 50})
                    + spread("EDGE", "e", {8: B.MIN_EVENTS_FOR_INDEX - 1}))
eq("one below the threshold is not", below["stores_scored"], 1)

print("── the dominant camera is named where the numbers are read ──────────────────────────────")
# The real case: two cameras at one store, 52 events against 2. Whoever reads that store's chart is
# reading one lens. It was only visible after somebody wrote a query; now it is on the page.
CAMS = [{"device_name": "big", "display_name": "5135 Bergenline Ave", "store_code": "S"},
        {"device_name": "small", "display_name": "5135 camera", "store_code": "S"}]
lop = B.aggregate(spread("S", "big", {h: 4 for h in range(13)}) + spread("S", "small", {12: 2}),
                  cameras=CAMS)
eq("the dominant camera is reported", len(lop["dominant"]), 1)
eq("...named as the operator knows it", lop["dominant"][0]["name"], "5135 Bergenline Ave")
eq("...against its own store", lop["dominant"][0]["store_code"], "S")
ck("...with the share that makes it dominant", lop["dominant"][0]["share"] > 0.9,
   lop["dominant"][0]["share"])
# An even split is NOT dominance — flagging it would train people to ignore the warning.
even = B.aggregate(spread("S", "big", {10: 20}) + spread("S", "small", {10: 20}), cameras=CAMS)
eq("an even two-camera split is not flagged", even["dominant"], [])
# Nor is 70/30 — both cameras are contributing, and a warning that fires on a healthy store is a
# warning people learn to skip.
lean = B.aggregate(spread("S", "big", {10: 70}) + spread("S", "small", {10: 30}), cameras=CAMS)
eq("a 70/30 split is not flagged", lean["dominant"], [])
# A single-camera store is 100% one camera by definition and must never be flagged, or every
# one-camera store in the estate would carry a warning that means nothing.
solo = B.aggregate(spread("S", "big", {10: 40}),
                   cameras=[{"device_name": "big", "display_name": "only", "store_code": "S"}])
eq("a single-camera store is never 'dominated'", solo["dominant"], [])
# Dominance is judged per store, not company-wide.
multi = B.aggregate(
    spread("S1", "big", {10: 50}) + spread("S1", "small", {10: 2})
    + spread("S2", "x", {10: 20}) + spread("S2", "y", {10: 20}),
    cameras=[{"device_name": "big", "display_name": "Big", "store_code": "S1"},
             {"device_name": "small", "display_name": "Small", "store_code": "S1"},
             {"device_name": "x", "display_name": "X", "store_code": "S2"},
             {"device_name": "y", "display_name": "Y", "store_code": "S2"}])
eq("only the lopsided store is flagged", [d["store_code"] for d in multi["dominant"]], ["S1"])

print("── camera contributions ─────────────────────────────────────────────────────────────────")
c = lop["cameras"]
ck("cameras are listed busiest first", all(c[i]["events"] >= c[i + 1]["events"]
                                           for i in range(len(c) - 1)), c)
ck("shares are a fraction of the total", abs(sum(x["share"] for x in c) - 1.0) < 0.01)
eq("a camera we have no record of keeps its device id",
   B.aggregate(spread("S", "ghost", {10: 3}))["cameras"][0]["name"], "ghost")
eq("...and reports no store rather than guessing one",
   B.aggregate(spread("S", "ghost", {10: 3}))["cameras"][0]["store_code"], "")
big = B.aggregate([ev("S", 10, f"cam{i}") for i in range(40)])
ck("the contribution list is capped so the payload stays small", len(big["cameras"]) <= 12)

print("── which measure the page draws ─────────────────────────────────────────────────────────")
eq("a named store gets raw counts", B.measure_for("B-103", 5), "events")
eq("...even when only one store is in reach", B.measure_for("B-103", 1), "events")
eq("one store in reach, none named, gets raw counts", B.measure_for("", 1), "events")
eq("several stores get the index", B.measure_for("", 18), "index")
eq("whitespace is not a store name", B.measure_for("   ", 18), "index")
eq("no stores at all does not crash", B.measure_for("", 0), "events")
eq("None behaves like absent", B.measure_for(None, 3), "index")

print("── the page must say what it is showing ─────────────────────────────────────────────────")
idx_note = B.caveat("index", 18)
ck("the index caveat explains equal weighting", "equally" in idx_note, idx_note)
ck("...and names the specific way summing misleads", "more cameras" in idx_note, idx_note)
multi_raw = B.caveat("events", 18)
ck("raw across several stores warns against comparing stores",
   "not" in multi_raw and "between stores" in multi_raw, multi_raw)
one_raw = B.caveat("events", 1)
ck("one store is told its hours ARE comparable", "hour to hour" in one_raw, one_raw)
for note in (idx_note, multi_raw, one_raw):
    ck("no caveat calls these customers", "customer" not in note.lower(), note)
    ck("no caveat calls these footfall", "footfall" not in note.lower(), note)

print("── the peak, and its tie rule ───────────────────────────────────────────────────────────")
tied = [{"hour": h, "events": 0, "index": 0.0} for h in range(24)]
tied[15]["events"] = tied[19]["events"] = 50
eq("a tie goes to the earlier hour", B.peak(tied, "events")["hour"], 15)
eq("...and reordering the rows does not change that",
   B.peak(list(reversed(tied)), "events")["hour"], 15)
eq("the peak follows the measure asked for", B.peak(both["by_hour"], "index")["hour"], 16)
ck("an all-zero day has no peak", B.peak([{"hour": h, "events": 0, "index": 0.0}
                                          for h in range(24)], "events") is None)

print("── bad rows are dropped, never silently bucketed ────────────────────────────────────────")
# A NULL hour becoming int 0 would pile every unparseable row onto midnight and invent a night shift.
bad = B.aggregate([ev("S", 10, "a"), {"store_code": "S", "local_hour": None, "device_name": "a"},
                   {"store_code": "S", "local_hour": 99, "device_name": "a"},
                   {"store_code": "S", "local_hour": -1, "device_name": "a"},
                   {"store_code": "S", "local_hour": "noon", "device_name": "a"}])
eq("only the valid row is counted", bad["events"], 1)
eq("midnight did not collect the unparseable ones", bad["by_hour"][0]["events"], 0)
eq("a row with no store is still counted, under a placeholder",
   B.aggregate([{"local_hour": 10, "device_name": "a"}])["events"], 1)
eq("a row with no device does not create a phantom camera",
   B.aggregate([{"store_code": "S", "local_hour": 10}])["cameras"], [])

print("── nothing here reaches for the network, a clock, or a database ─────────────────────────")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app/modules/vision/busy.py")).read()
for bad_import in ("requests.", "httpx.", "get_supabase", "datetime.now", "time.time", "import os"):
    ck(f"busy.py does not use {bad_import}", bad_import not in src)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
