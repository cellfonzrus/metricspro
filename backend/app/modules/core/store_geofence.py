"""THE one home for "was this person at the store this record is for?" — PURE, leaf, no second rule.

OWNER ASK 2026-11-01 (verbatim): *"the sales reps when they log in should capture the gos location so
they are not clocking out at their homes and the geolocation when they clock out from somewhere else
than their usual location which should match the store address should be reported as flag to the
management"*.

WHAT ALREADY EXISTED, AND WHAT DID NOT (CLAUDE.md duplicate-check build gate, owner 2026-09-02)
───────────────────────────────────────────────────────────────────────────────────────────────
Searched before a line was written:

  · `storeops.timelog` has CAPTURED a clock-in fix since migration 045 — `gps_lat` / `gps_lng` /
    `gps_accuracy_m` — and both the kiosk and the phone app send one. **The clock-OUT captured
    nothing at all**: `ClockOutIn` had no location fields, so the punch the owner is actually
    worried about was the one punch nobody could see.
  · `core/geo.evaluate_checkin` has been THE geofence DECISION in this platform since the marketing
    event check-in (index §23). It takes the reported accuracy seriously and already has the
    vocabulary for "we could not tell".
  · `storeops.stores` holds an `address` and **no coordinates**, so nothing could be measured
    against a store at all. That was the missing INPUT, not a missing rule.
  · `storevisit` (mig 027) captures a DM's check-in fix and has never judged one either — the
    sibling path asking this identical question (CLAUDE.md: find the siblings before you ship).

So this module adds the inputs a punch has and an event did not (which store, whose pin, whose
radius), and DEREFERENCES the existing decision. There is no haversine here, no radius comparison
here and no second verdict vocabulary here; `tests/unit/test_store_geofence.py` §Z fails the build
if one appears, and `harness_store_geofence_lock.py` fails it if a caller stops dereferencing this.

THE DESIGN FIX, NOT THE INSTANCE (owner directive 2026-09-20)
─────────────────────────────────────────────────────────────
The class is not "clock-out can be done from home". It is **"a punch's location was captured and
never judged"** — true of the clock-in, true of the store visit, and newly true of the clock-out.
One function answers it for all three `KINDS`, one table (`PUNCH_COLUMNS`) says which column holds
which fact so the writer and the review board can never read past each other, and an unruled kind
raises rather than being judged by a rule nobody wrote for it.

WHAT IS NEVER DECIDED HERE
──────────────────────────
**A punch is never refused.** The owner asked for a flag to management, not a lock on the door: a
rep whose phone puts them across the street must still be able to end their shift, and the row is
what a manager reviews. `accepted` is therefore always True — the `block_outside` branch of
`evaluate_checkin` is deliberately never reached from a punch.

**"Could not verify" is never "they were not there."** No pin, no fix, or a fix too coarse to settle
a borderline all return an `unverified_*` decision with `within_geofence = None` and
`off_site = False`. Zero stores are pinned on the day this ships; a module that read an unpinned
store as a violation would flag every punch in the company on its first morning.

PRIVACY POSTURE — read before adding a function here
────────────────────────────────────────────────────
Single-shot, like the decision it wraps: ONE position, ONE place, ONE verdict. Nothing here knows a
previous position, a path, a dwell time or a heading, and nothing here should ever gain one — those
are the primitives of tracking and no requirement in this platform needs them. Employee coordinates
are SENSITIVE personal data (index §23.5): they are captured at the two moments the employee
themselves initiates, and they are retained under the tenant's declared retention.

MULTI-TENANT (project rule 2026-10-30). The radius, the accuracy limit and the switch are POLICY, so
a house default is right for them. The PIN is the tenant's own declaration and has NO default — an
unpinned store answers "could not verify", never another tenant's coordinates.

LEAF MODULE: stdlib only, plus `core.geo` (itself stdlib-only). No fastapi, no supabase, no app
imports, so it is provable in a bare container. The tenant config READ lives in the router that owns
its I/O, exactly as `attendance_exceptions` does.
"""
from app.modules.core import geo

# ── the kinds of record that ask this question ──────────────────────────────────────────────────
# An unruled kind RAISES. A new caller must add itself here and say which columns hold its fix, so a
# record can never be judged by a rule nobody wrote for it (and so `PUNCH_COLUMNS` cannot fall
# behind `KINDS` unnoticed — the unit test pins them together).
KIND_CLOCK_IN = "clock_in"
KIND_CLOCK_OUT = "clock_out"
KIND_STORE_VISIT = "store_visit"
KINDS = (KIND_CLOCK_IN, KIND_CLOCK_OUT, KIND_STORE_VISIT)

# ── the house POLICY defaults (never a tenant's own vocabulary) ─────────────────────────────────
DEFAULT_CONFIG = {
    # ON for every tenant from the first day (project rule 2026-10-30: a feature is for every
    # tenant). It is safely inert until a store is pinned — an unpinned store cannot produce a
    # verdict — so switching it on by default accuses nobody.
    "punch_geo_enabled": True,
    "punch_geofence_radius_m": geo.DEFAULT_RADIUS_M,        # 150 m
    "punch_geo_max_accuracy_m": geo.DEFAULT_MAX_ACCURACY_M,  # 200 m
}
CONFIG_COLUMNS = tuple(DEFAULT_CONFIG)

# ── which column holds which fact, declared ONCE ────────────────────────────────────────────────
# The punch endpoints WRITE these and the exceptions board READS them. A second spelling anywhere
# would mean the board reads NULL forever while the write path looks perfectly correct.
#
# The clock-in keeps the three columns migration 045 already created, so no stored history moves.
# The clock-out gets its OWN three: a shift's two ends are two different facts, and a clock-out fix
# that overwrote the clock-in's would destroy the only evidence of where the shift began.
PUNCH_COLUMNS = {
    KIND_CLOCK_IN: {
        "lat": "gps_lat", "lng": "gps_lng", "accuracy": "gps_accuracy_m",
        "decision": "clock_in_geo_decision", "distance": "clock_in_geo_distance_m",
    },
    KIND_CLOCK_OUT: {
        "lat": "clock_out_gps_lat", "lng": "clock_out_gps_lng",
        "accuracy": "clock_out_gps_accuracy_m",
        "decision": "clock_out_geo_decision", "distance": "clock_out_geo_distance_m",
    },
    KIND_STORE_VISIT: {
        "lat": "check_in_lat", "lng": "check_in_lng", "accuracy": "check_in_accuracy",
        "decision": "check_in_geo_decision", "distance": "check_in_geo_distance_m",
    },
}

# The ALLOWLIST of decisions that accuse somebody. Same posture as
# `carrier_dollar_class.component_is_evidence` (index §66): a decision this version has never heard
# of reads as NOT a violation. An allowlist can only ever fail to flag; a denylist would one day
# flag a word that means "unknown".
OFF_SITE_DECISIONS = (geo.OUTSIDE,)


def _require_kind(kind):
    if kind not in KINDS:
        raise ValueError(
            "unknown geofence record kind %r — add it to store_geofence.KINDS and give it a row in "
            "PUNCH_COLUMNS rather than judging it by another kind's rule" % (kind,))
    return kind


def store_pin(store_row):
    """THE one read of "where is this store on the map?" → `{lat, lng, radius_m}` or **None**.

    None means nobody has pinned it. That is a planner's omission, never an employee's fault, and
    every caller must carry it through as "could not verify". A junk pin (out of range, unparseable,
    half-present) is the same as no pin — a stray `0.0` would place every store off the coast of
    Africa, so the coordinate parsers in `core.geo` are what decide, not a truthiness test."""
    row = store_row or {}
    lat = geo.parse_lat(row.get("geo_lat"))
    lng = geo.parse_lng(row.get("geo_lng"))
    if lat is None or lng is None:
        return None
    raw_radius = row.get("geofence_radius_m")
    radius = None
    if raw_radius not in (None, ""):
        radius = geo.clamp_radius(raw_radius)
    return {"lat": lat, "lng": lng, "radius_m": radius}


def resolve_config(row, available=True):
    """A tenant row (or anything dict-shaped) → the three policy values, house defaults behind each.

    Used by the routers that read `storeops.tenants`; kept here so one tenant's policy can never be
    resolved two ways. `available=False` is passed through untouched — it describes the migration,
    not the policy."""
    row = row or {}
    cfg = dict(DEFAULT_CONFIG)
    enabled = row.get("punch_geo_enabled")
    if enabled is not None:
        cfg["punch_geo_enabled"] = bool(enabled)
    for key in ("punch_geofence_radius_m", "punch_geo_max_accuracy_m"):
        val = row.get(key)
        if val in (None, ""):
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num > 0:
            cfg[key] = int(num)
    return cfg, bool(available)


def evaluate_presence(kind, *, fix_lat, fix_lng, fix_accuracy_m, store_row, config):
    """Judge ONE reported position against ONE store. Pure; returns a dict, or **None** when the
    tenant has the check switched off.

      {kind, decision, within_geofence, off_site, distance_m, radius_m, accuracy_m, accepted,
       needs_store_pin, note}

    `off_site` is the only field a flag may be built from, and it is True ONLY for a decision on the
    `OFF_SITE_DECISIONS` allowlist. `within_geofence` stays None for every `unverified_*`, so no
    caller can mistake "we could not tell" for "they were not there".

    None (the switch off) is deliberately different from a passing verdict: a tenant that has turned
    this off stores NOTHING, so turning it on later cannot find a column full of verdicts nobody
    measured."""
    _require_kind(kind)
    cfg, _ = resolve_config(config if isinstance(config, dict) else None)
    if not cfg["punch_geo_enabled"]:
        return None

    pin = store_pin(store_row)
    radius = cfg["punch_geofence_radius_m"]
    if pin is not None and pin["radius_m"] is not None:
        # A store's own fence wins over its tenant's: a mall kiosk and a standalone shop are not the
        # same fence, and that is a per-STORE fact, not a per-tenant one.
        radius = pin["radius_m"]

    verdict = geo.evaluate_checkin(
        fix_lat, fix_lng, fix_accuracy_m,
        (pin or {}).get("lat"), (pin or {}).get("lng"),
        radius_m=radius, max_accuracy_m=cfg["punch_geo_max_accuracy_m"],
        # NEVER blocks. The owner asked for a flag to management, not a lock on the door.
        block_outside=False,
    )
    decision = verdict["decision"]
    return {
        "kind": kind,
        "decision": decision,
        "within_geofence": verdict["within_geofence"],
        "off_site": is_off_site(decision),
        "distance_m": verdict["distance_m"],
        "radius_m": verdict["radius_m"],
        "accuracy_m": verdict["accuracy_m"],
        "accepted": True,
        "needs_store_pin": decision == geo.UNVERIFIED_NO_TARGET,
        "note": _note(kind, verdict),
    }


def is_off_site(decision):
    """Does this stored decision accuse somebody? ALLOWLIST — see `OFF_SITE_DECISIONS`."""
    return str(decision or "") in OFF_SITE_DECISIONS


def stored_columns(kind, verdict, *, fix_lat=None, fix_lng=None, fix_accuracy_m=None):
    """The columns to write for this record, keyed by `PUNCH_COLUMNS[kind]` and nothing else.

    The COORDINATES are stored whether or not there is a verdict to store with them: the pin arrives
    later, and the coordinates are what make a retrospective answer possible at all. A `verdict` of
    None (the check switched off) stores the fix and no verdict."""
    _require_kind(kind)
    cols = PUNCH_COLUMNS[kind]
    row = {
        cols["lat"]: geo.parse_lat(fix_lat),
        cols["lng"]: geo.parse_lng(fix_lng),
        cols["accuracy"]: _int_or_none(geo.parse_accuracy(fix_accuracy_m)),
    }
    if verdict:
        row[cols["decision"]] = verdict.get("decision")
        row[cols["distance"]] = verdict.get("distance_m")
    return row


def punch_off_site_flags(punch_row):
    """A stored punch → the list of its OFF-SITE ends, in `KINDS` order. `[]` for everything else.

    This is what the review board builds its rows from, so it reads the SAME `PUNCH_COLUMNS` the
    punch endpoints wrote and never a column name of its own."""
    row = punch_row or {}
    flags = []
    for kind in KINDS:
        cols = PUNCH_COLUMNS[kind]
        decision = row.get(cols["decision"])
        if not is_off_site(decision):
            continue
        flags.append({
            "kind": kind,
            "decision": decision,
            "distance_m": row.get(cols["distance"]),
            "lat": row.get(cols["lat"]),
            "lng": row.get(cols["lng"]),
            "accuracy_m": row.get(cols["accuracy"]),
        })
    return flags


def punch_geo_coverage(punch_rows):
    """How much of a window could be answered at all — per end, counted by decision.

    Honesty surface, not a flag: a board showing "3 off-site punches" while 900 punches could not be
    verified is telling a manager something false by omission. Reported beside the flags so the
    reason a number is small is visible (index §23's `no_target` posture)."""
    out = {}
    for kind in KINDS:
        col = PUNCH_COLUMNS[kind]["decision"]
        counts = {}
        for row in punch_rows or []:
            decision = str((row or {}).get(col) or "") or "not_measured"
            counts[decision] = counts.get(decision, 0) + 1
        out[kind] = counts
    return out


# ── the sentences a manager reads ───────────────────────────────────────────────────────────────
_WHERE = {
    KIND_CLOCK_IN: "clocked in",
    KIND_CLOCK_OUT: "clocked out",
    KIND_STORE_VISIT: "checked in",
}


def _note(kind, verdict):
    """Re-word the shared decision's note for a punch. `core.geo`'s own notes talk about an EVENT and
    an event LEAD, which would read as nonsense on a payroll board — the DECISION is shared, the
    wording belongs to the surface, and nothing here re-decides anything."""
    decision, distance, radius = verdict["decision"], verdict["distance_m"], verdict["radius_m"]
    did = _WHERE.get(kind, "punched")
    if decision == geo.INSIDE:
        return "Verified at the store (%d m from its pin, fence %d m)." % (distance or 0, radius)
    if decision == geo.OUTSIDE:
        return ("The employee %s %s from the store's pin (fence %d m). Recorded and flagged for "
                "management to review." % (did, _metres(distance), radius))
    if decision == geo.UNVERIFIED_NO_TARGET:
        return ("This store has no map pin, so the location could not be checked. Pin the store to "
                "turn verification on — nothing about this punch is wrong.")
    if decision == geo.UNVERIFIED_NO_FIX:
        return ("The device reported no location when the employee %s, so this punch is recorded as "
                "unverified." % did)
    if decision == geo.UNVERIFIED_ACCURACY:
        return ("The device's location was accurate to about %s, too coarse to tell whether the "
                "employee was inside the %d m fence. Recorded as unverified rather than counted "
                "against anyone." % (_metres(verdict.get("accuracy_m")), radius))
    # An unruled decision: say so plainly rather than inventing a meaning for it.
    return "This punch's location could not be interpreted (%s)." % (decision,)


def _metres(value):
    if value is None:
        return "an unknown distance"
    try:
        metres = int(round(float(value)))
    except (TypeError, ValueError):
        return "an unknown distance"
    if metres >= 1000:
        return "%.1f km" % (metres / 1000.0)
    return "%d m" % metres


def _int_or_none(value):
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None
