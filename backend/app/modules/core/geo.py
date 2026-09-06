"""THE shared geolocation decision for the platform. One rule, one file, no second opinion.

WHY THIS FILE EXISTS AT ALL (CLAUDE.md duplicate-check build gate, owner 2026-09-02)
────────────────────────────────────────────────────────────────────────────────────
Before writing a line of the marketing module's event check-in, the platform was searched for an
existing geolocation path. What was found:

  · storevisit (mig 027) CAPTURES a GPS fix — `check_in_lat` / `check_in_lng` / `check_in_accuracy`
    are written onto the DM's store-visit row at the moment the DM presses check-in.
  · NOTHING in the repository JUDGED one. There was no haversine, no distance, no radius, no
    geofence anywhere in `backend/` — storevisit stores the three numbers and never asks whether
    they mean the person was actually at the store.

So the honest position is: the CAPTURE contract already existed and is reused verbatim (the
marketing check-in table uses those same three column names, deliberately), and the DECISION did
not exist and is created here — ONCE, in core, as a pure function — rather than inside the
marketing module where a second caller would later have to copy it.

storevisit can adopt `evaluate_checkin` without changing anything it stores today: it has no store
geo point yet, so it would evaluate to `unverified_no_target` (accepted, flagged) until someone
pins its stores. That is the whole point of the `no_target` branch — a subsystem that hasn't got
coordinates yet gets an honest "couldn't verify", never a false "verified".

WHAT THIS DECIDES, AND WHAT IT REFUSES TO DECIDE
────────────────────────────────────────────────
It answers one question — "given this single position report, is this person plausibly at this
place?" — and it answers it with the reported ACCURACY taken seriously. A phone that says "I am
90 m away, ±200 m" has not told you the person is outside a 150 m fence; it has told you it does
not know. Treating that as a violation would punish people for standing next to a building. So the
decision works on the interval [distance − accuracy, distance + accuracy]:

    worst case inside the fence            -> inside      (cannot be outside, whatever the error)
    best case still outside the fence      -> outside     (cannot be inside, whatever the error)
    interval straddles the fence           -> the fix is too coarse to settle it:
                                              · accuracy within the org's trust limit -> fall back
                                                to the point estimate (a normal borderline call)
                                              · accuracy beyond it -> `unverified_accuracy`, and the
                                                caller is told nobody can tell from this data

PRIVACY POSTURE — read this before adding a function here
──────────────────────────────────────────────────────────
This module is deliberately stateless and single-shot. It takes ONE position and returns ONE
verdict. It has no notion of a previous position, a path, a duration, a dwell time or a heading,
and nothing here should ever gain one: those are the primitives of tracking, and no requirement in
this platform needs them. If a future requirement seems to ask for continuous location, that is the
moment to stop and raise it, not to add a `positions=[...]` parameter.

LEAF MODULE: stdlib only (`math`). No fastapi, no supabase, no app imports — so it is provable in
the bare container. Proof: `backend/harness_marketing_event.py` section B.
"""
import math

# IUGG mean Earth radius in metres. At the distances a geofence cares about (tens to hundreds of
# metres) the difference between a spherical and an ellipsoidal model is far below the accuracy of
# any consumer GPS fix, so haversine on a sphere is the honest level of precision here.
EARTH_RADIUS_M = 6371008.8

# Decision vocabulary. `inside` / `outside` are verdicts; every `unverified_*` value means "this
# data cannot answer the question", which is NEVER the same as "the person was not there".
INSIDE = "inside"
OUTSIDE = "outside"
UNVERIFIED_NO_FIX = "unverified_no_fix"            # the device reported no usable position
UNVERIFIED_NO_TARGET = "unverified_no_target"      # nobody pinned the place on the map
UNVERIFIED_ACCURACY = "unverified_accuracy"        # the fix is too coarse to settle a borderline
DECISIONS = (INSIDE, OUTSIDE, UNVERIFIED_NO_FIX, UNVERIFIED_NO_TARGET, UNVERIFIED_ACCURACY)

# Sane bounds so a config typo or a garbage payload can't produce an absurd fence.
MIN_RADIUS_M = 10
MAX_RADIUS_M = 20000
DEFAULT_RADIUS_M = 150
DEFAULT_MAX_ACCURACY_M = 200


def parse_coord(value, limit):
    """A latitude/longitude as a float, or None. Accepts strings (every JSON body sends them as
    strings sooner or later) and rejects anything out of range, NaN or infinite — a 0.0 that came
    from `float("nan")` failing quietly would place every event off the coast of Africa."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    if abs(f) > limit:
        return None
    return f


def parse_lat(value):
    return parse_coord(value, 90.0)


def parse_lng(value):
    return parse_coord(value, 180.0)


def parse_accuracy(value):
    """Reported accuracy in metres, or None. A negative accuracy is meaningless and is discarded
    rather than trusted as 0 (which would silently make a bad fix look authoritative)."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f) or f < 0:
        return None
    return f


def clamp_radius(value, default=DEFAULT_RADIUS_M):
    """A geofence radius in metres, clamped to something physically sensible."""
    try:
        r = int(float(value))
    except (TypeError, ValueError):
        r = int(default)
    if r < MIN_RADIUS_M:
        return MIN_RADIUS_M
    if r > MAX_RADIUS_M:
        return MAX_RADIUS_M
    return r


def haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres between two points, or None if either is not a real point."""
    a_lat, a_lng = parse_lat(lat1), parse_lng(lng1)
    b_lat, b_lng = parse_lat(lat2), parse_lng(lng2)
    if a_lat is None or a_lng is None or b_lat is None or b_lng is None:
        return None
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = p2 - p1
    dlam = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def evaluate_checkin(fix_lat, fix_lng, fix_accuracy_m, target_lat, target_lng,
                     radius_m=DEFAULT_RADIUS_M, max_accuracy_m=DEFAULT_MAX_ACCURACY_M,
                     block_outside=False):
    """Judge ONE position report against ONE place. Pure; returns a dict, never raises.

      {decision, within_geofence, distance_m, radius_m, accuracy_m, accepted, note}

    `within_geofence` is True / False / None — None whenever the decision is an `unverified_*`, so a
    caller can never mistake "we could not tell" for "they were not there". `distance_m` is rounded
    to whole metres: sub-metre precision on a consumer GPS fix is decoration, and storing it would
    imply a certainty the reading does not have.

    `accepted` answers a DIFFERENT question — may this check-in be recorded? By default YES, always:
    a real person standing at a real event with a bad phone must be able to say they are there, and
    the flag on the row is what a manager reviews afterwards. Only an org that explicitly sets
    `block_outside` refuses, and even then it never refuses `unverified_no_target` — being unable to
    verify because nobody pinned the venue is the planner's omission, not the employee's.
    """
    radius = clamp_radius(radius_m)
    try:
        max_acc = float(max_accuracy_m)
        if math.isnan(max_acc) or max_acc <= 0:
            max_acc = float(DEFAULT_MAX_ACCURACY_M)
    except (TypeError, ValueError):
        max_acc = float(DEFAULT_MAX_ACCURACY_M)

    acc = parse_accuracy(fix_accuracy_m)
    t_lat, t_lng = parse_lat(target_lat), parse_lng(target_lng)
    f_lat, f_lng = parse_lat(fix_lat), parse_lng(fix_lng)

    def out(decision, within, distance, accepted, note):
        return {"decision": decision, "within_geofence": within,
                "distance_m": (None if distance is None else int(round(distance))),
                "radius_m": radius, "accuracy_m": acc, "accepted": accepted, "note": note}

    # The place was never pinned. Nothing about the employee is wrong here, so this never blocks.
    if t_lat is None or t_lng is None:
        return out(UNVERIFIED_NO_TARGET, None, None, True,
                   "No location pin on this event, so the check-in could not be verified against "
                   "one. Add the venue's map pin to enable geofence verification.")

    # The device gave nothing usable — permission denied, indoors with no fix, or a stale browser.
    if f_lat is None or f_lng is None:
        return out(UNVERIFIED_NO_FIX, None, None, not block_outside,
                   "No location was reported by the device, so this check-in is recorded as "
                   "unverified.")

    distance = haversine_m(f_lat, f_lng, t_lat, t_lng)
    margin = acc if acc is not None else 0.0
    best_case = max(0.0, distance - margin)     # closest they could actually be
    worst_case = distance + margin              # furthest they could actually be

    if worst_case <= radius:
        return out(INSIDE, True, distance, True,
                   "Verified at the event location.")
    if best_case > radius:
        note = ("Recorded %d m from the event location (fence %d m)." % (int(round(distance)), radius))
        if block_outside:
            return out(OUTSIDE, False, distance, False,
                       note + " This organisation requires check-in at the event location.")
        return out(OUTSIDE, False, distance, True,
                   note + " Recorded and flagged for the event lead to review.")

    # The interval straddles the fence: this reading alone cannot settle it.
    if acc is not None and acc > max_acc:
        return out(UNVERIFIED_ACCURACY, None, distance, not block_outside,
                   "The device's location was accurate to about %d m, which is too coarse to tell "
                   "whether the check-in was inside the %d m fence. Recorded as unverified rather "
                   "than counted against anyone." % (int(round(acc)), radius))
    # A normal borderline call with a trustworthy fix — use the point estimate and say so.
    if distance <= radius:
        return out(INSIDE, True, distance, True,
                   "Verified at the event location (borderline: %d m from the pin, fence %d m)."
                   % (int(round(distance)), radius))
    if block_outside:
        return out(OUTSIDE, False, distance, False,
                   "Recorded %d m from the event location (fence %d m). This organisation requires "
                   "check-in at the event location." % (int(round(distance)), radius))
    return out(OUTSIDE, False, distance, True,
               "Recorded %d m from the event location (fence %d m). Recorded and flagged for the "
               "event lead to review." % (int(round(distance)), radius))
