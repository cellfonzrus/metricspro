"""LOCK: there is ONE geofence decision and ONE store pin — and a second cannot appear.

OWNER ASK 2026-11-01 (verbatim): *"the sales reps when they log in should capture the gos location so
they are not clocking out at their homes and the geolocation when they clock out from somewhere else
than their usual location which should match the store address should be reported as flag to the
management"*.

WHY A LOCK AND NOT JUST TESTS. The standing design rule (CLAUDE.md, owner 2026-09-20) is that a fix
ships with a check that FAILS THE BUILD if a caller stops dereferencing the shared fact or a second
copy appears — *"it has happened three times here"*. This feature has exactly the shape that goes
wrong: three callers (clock-in, clock-out, store visit) asking one question against one declared pin.
A fourth caller that measured its own distance would be invisible to every test in the suite, because
its own tests would pass.

WHAT IT FAILS THE BUILD ON

  1. A SECOND DISTANCE CALCULATION. A haversine, an Earth radius, a `math.asin`/`math.radians` pair
     or a hand-rolled degrees-to-metres factor anywhere in `backend/app` outside `core/geo.py`.
  2. A SECOND RADIUS COMPARISON. A module that compares a distance against a fence instead of
     asking `evaluate_presence`.
  3. A CALLER THAT STOPPED DEREFERENCING. Each punch/visit write path must still reach the one home.
  4. A SECOND COLUMN-NAME HOME. A module outside `core/store_geofence.py` spelling the verdict or
     clock-out-fix column names literally instead of reading `PUNCH_COLUMNS` — the exact way a
     writer and a reader drift until the board reads NULL forever.
  5. A SECOND STORE-PIN READ. `geo_lat` / `geo_lng` resolved anywhere but `store_pin`, which is how
     half a pin, a junk coordinate or a stray `0.0` would get through.
  6. A DENYLIST where the off-site decision should be an ALLOWLIST (index §66's class: an unruled
     word must read as "not a violation", never as one).
  7. THE PUNCH BEING BLOCKED. `block_outside=True` reaching the punch path would turn a flag into a
     lock on the door, which is not what was asked for and would strand a rep with a bad phone.

No DB, no network, no app imports.  Run:  cd backend && python3 harness_store_geofence_lock.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "app")

PASS = 0
FAIL = 0


def ok(condition, failure_message, pass_message=None):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {pass_message or failure_message}")
    else:
        FAIL += 1
        print(f"  FAIL  {failure_message}")


def _code(path):
    """A file's source with comments and docstring prose stripped of the words we search for.

    Comments are stripped because a module must be free to NAME what it deliberately does not do —
    `store_geofence` itself explains that nothing there computes a haversine, and a lock that could
    not tell an explanation from an implementation would make the explanation unwritable."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return ""
    return "\n".join(line.split("#")[0] for line in src.splitlines())


def _py_files():
    for root, _dirs, names in os.walk(_APP):
        if "__pycache__" in root:
            continue
        for name in sorted(names):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                yield os.path.relpath(path, _APP).replace(os.sep, "/"), path


# ── 1 + 2. the decision has one home ────────────────────────────────────────────────────────────
GEO_HOME = "modules/core/geo.py"
GEOFENCE_HOME = "modules/core/store_geofence.py"

# A distance between two points on the Earth, however it is spelled.
_DISTANCE_RE = re.compile(
    r"""math\.asin|math\.acos|math\.radians"""        # the haversine's own trigonometry
    r"""|6371|6378137|EARTH_RADIUS"""                  # an Earth radius, metres or km
    r"""|def\s+_?haversine|def\s+_?distance_m\b"""     # a helper whose whole job it is
    r"""|111\s*32|0\.000009""")                        # a degrees-to-metres fudge factor

# Comparing a distance against a fence — the decision itself, not the inputs to it.
_FENCE_RE = re.compile(
    r"""(distance|dist|metres|meters)\w*\s*(<=?|>=?)\s*\w*(radius|fence|geofence)"""
    r"""|(radius|fence|geofence)\w*\s*(<=?|>=?)\s*\w*(distance|dist|metres|meters)""",
    re.I)


def check_one_decision():
    print("§1  ONE distance calculation, ONE fence comparison")
    offenders = []
    fence_offenders = []
    for rel, path in _py_files():
        if rel in (GEO_HOME, GEOFENCE_HOME):
            continue
        code = _code(path)
        if _DISTANCE_RE.search(code):
            offenders.append(rel)
        if _FENCE_RE.search(code):
            fence_offenders.append(rel)
    ok(not offenders,
       f"a second distance calculation appeared outside {GEO_HOME}: {offenders}",
       f"no module outside {GEO_HOME} computes a distance between two points")
    ok(not fence_offenders,
       f"a second fence comparison appeared outside the one home: {fence_offenders}",
       "no module compares a distance against a radius of its own")
    # The home itself must still BE the home.
    home = _code(os.path.join(_APP, GEO_HOME))
    ok("def haversine_m" in home and "def evaluate_checkin" in home,
       f"{GEO_HOME} no longer holds the geofence decision",
       f"{GEO_HOME} still holds the geofence decision")
    fence = _code(os.path.join(_APP, GEOFENCE_HOME))
    ok("evaluate_checkin" in fence,
       f"{GEOFENCE_HOME} stopped dereferencing the shared decision and must be re-wired",
       f"{GEOFENCE_HOME} dereferences the shared decision")
    ok(not _DISTANCE_RE.search(fence),
       f"{GEOFENCE_HOME} grew a distance calculation of its own",
       f"{GEOFENCE_HOME} computes no distance of its own")


# ── 3. every caller still dereferences it ───────────────────────────────────────────────────────
# {relative path: why it must reach the one home}
CALLERS = {
    "modules/storeops/router.py": "the clock-in and clock-out write the punch's verdict",
    "modules/storevisit/router.py": "the DM store-visit check-in is judged by the same rule",
    "modules/storeops/attendance_exceptions.py": "the review board reads the stored verdict back",
}


def check_callers():
    print()
    print("§2  every caller still reaches the one home")
    for rel, why in CALLERS.items():
        code = _code(os.path.join(_APP, rel))
        ok("store_geofence" in code,
           f"{rel} stopped dereferencing core/store_geofence — {why}",
           f"{rel} dereferences the one home ({why})")


# ── 4. the column names have one home ───────────────────────────────────────────────────────────
# The literal column names. A module that spells one instead of reading PUNCH_COLUMNS is how a
# writer and a reader drift apart while both look correct.
VERDICT_COLUMNS = ("clock_in_geo_decision", "clock_out_geo_decision", "clock_in_geo_distance_m",
                   "clock_out_geo_distance_m", "check_in_geo_decision", "check_in_geo_distance_m",
                   "clock_out_gps_lat", "clock_out_gps_lng", "clock_out_gps_accuracy_m")


def check_column_names():
    print()
    print("§3  ONE home for which column holds which fact")
    offenders = {}
    for rel, path in _py_files():
        if rel == GEOFENCE_HOME:
            continue
        code = _code(path)
        hits = [c for c in VERDICT_COLUMNS if c in code]
        if hits:
            offenders[rel] = hits
    ok(not offenders,
       f"a geofence column name is spelled outside {GEOFENCE_HOME}: {offenders}",
       f"every caller reads the column names from {GEOFENCE_HOME}.PUNCH_COLUMNS")
    fence = _code(os.path.join(_APP, GEOFENCE_HOME))
    for column in VERDICT_COLUMNS:
        ok(column in fence, f"{GEOFENCE_HOME} no longer declares {column}",
           f"{GEOFENCE_HOME} declares {column}")


# ── 5. the store pin has one read ───────────────────────────────────────────────────────────────
# A reviewed excuse, pinned so it cannot grow. `marketing_event.geo_lat/lng` is the VENUE of a
# one-off event — a school car park, a community hall — and is not a store at all: it has no store
# code, no roster, no fence of its own and nothing to inherit one from (index §23). It is a
# different fact that happens to be two numbers, so it keeps its own read and judges itself through
# the SAME `core/geo.evaluate_checkin`. A NEW entry here has to be argued for in a PR.
PIN_READ_EXCUSED = {
    "modules/marketing/router.py": "an EVENT venue pin, not a store pin — §23, different fact",
}


def check_store_pin():
    print()
    print("§4  ONE read of where a store is")
    offenders = {}
    for rel, path in _py_files():
        if rel == GEOFENCE_HOME or rel in PIN_READ_EXCUSED:
            continue
        code = _code(path)
        # Reading the column off a row is the thing with one home. SELECTING it (a column list in a
        # PostgREST select, or a settings payload) is just I/O and is not a second resolution.
        if re.search(r"""\.get\(\s*['"]geo_(lat|lng)['"]""", code):
            offenders[rel] = "resolves a store pin from the row itself"
    ok(not offenders,
       f"a second store-pin resolution appeared: {offenders}",
       f"every caller resolves a store's pin through {GEOFENCE_HOME}.store_pin"
       f" ({len(PIN_READ_EXCUSED)} reviewed exception(s): {sorted(PIN_READ_EXCUSED)})")
    # The excuse must stay true: an excused module may read ITS pin, never a store's fence.
    for rel, why in PIN_READ_EXCUSED.items():
        code = _code(os.path.join(_APP, rel))
        ok("geofence_radius_m" not in code,
           f"{rel} is excused as {why} but now reads a STORE's fence — the excuse no longer holds",
           f"{rel}'s excuse still holds ({why})")
    fence = _code(os.path.join(_APP, GEOFENCE_HOME))
    ok("def store_pin" in fence, f"{GEOFENCE_HOME} no longer holds store_pin",
       f"{GEOFENCE_HOME} holds store_pin")
    ok("parse_lat" in fence and "parse_lng" in fence,
       "store_pin stopped parsing its coordinates — a junk or half pin would place a store at 0,0",
       "store_pin parses its coordinates, so half a pin and a junk pin both read as no pin")


# ── 6 + 7. the postures that protect people ─────────────────────────────────────────────────────
def check_postures():
    print()
    print("§5  an unruled verdict accuses nobody, and a punch is never blocked")
    fence = _code(os.path.join(_APP, GEOFENCE_HOME))
    ok("OFF_SITE_DECISIONS" in fence and "def is_off_site" in fence,
       "the off-site allowlist is gone — an unruled decision could start accusing people",
       "the off-site verdict is decided by one named allowlist")
    ok(re.search(r"OFF_SITE_DECISIONS\s*=\s*\(", fence) is not None,
       "OFF_SITE_DECISIONS is no longer a fixed tuple (an allowlist, not a denylist)",
       "OFF_SITE_DECISIONS is a fixed tuple")
    ok("not in OFF_SITE_DECISIONS" not in fence,
       "the allowlist has been inverted into a denylist — an unknown word would read as a violation",
       "the allowlist has not been inverted")
    ok("block_outside=False" in fence,
       "the punch path no longer pins block_outside=False — a flag would become a lock on the door",
       "the punch path pins block_outside=False: a flag, never a lock on the door")
    offenders = [rel for rel, path in _py_files()
                 if rel != GEOFENCE_HOME and "block_outside=True" in _code(path)]
    ok(not offenders, f"a caller blocks a punch on its location: {offenders}",
       "no caller turns the location check into a refusal")
    # The privacy posture: single-shot, never a path.
    ok(not re.search(r"watchPosition|def\s+\w*track\w*position|positions\s*=\s*\[", fence),
       f"{GEOFENCE_HOME} grew a notion of a PATH — single-shot is the whole privacy posture",
       f"{GEOFENCE_HOME} is still single-shot: one position, one place, one verdict")


def main():
    print("=" * 78)
    print("LOCK: one geofence decision, one store pin, one column home")
    print("=" * 78)
    check_one_decision()
    check_callers()
    check_column_names()
    check_store_pin()
    check_postures()
    print()
    print("=" * 78)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
