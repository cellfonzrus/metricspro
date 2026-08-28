"""Employee activity — the decisions, as pure functions, with the uncertainty kept visible.

OWNER DIRECTIVE 2026-08-22 (sanjot@): "build the busy hours view, then the employee behavior,
sitting, yawning, not doing anything walking, talking to the customer etc whatever we can pull
from google."

WHAT GOOGLE GIVES US FOR THIS: NOTHING. The Smart Device Management API exposes CameraPerson,
CameraMotion, CameraSound, DoorbellChime and a per-event image, and that is the entire surface.
There is no posture, no gaze, no activity classification and no identity in it, at any price. So
every signal in this file is derived at the EDGE from frames the analyzer already decodes, and the
frames are discarded there — this module never sees an image, a landmark, or a face descriptor. It
sees numbers that have already been reduced to "this track was sitting for 40 of the last 60
seconds", and it decides what those numbers are allowed to mean.

THE HARD PART IS NOT DETECTION, IT IS ATTRIBUTION
─────────────────────────────────────────────────
A pose model will tell you a person is sitting. It will not tell you WHICH person, and the only
technique that would — matching a face against enrolled staff photos — is a biometric identifier
under Illinois BIPA, which the tenant has stores under, and migration 900's header commits this
module to never storing one. That commitment is kept here.

So attribution comes from the TIME CLOCK instead, and it is deliberately timid: a track is named
only when exactly ONE employee was clocked in at that store for that bucket, and that employee has
signed video consent. Two people on shift means the row is stored unattributed — because "one of
these two was sitting" is not a fact about either of them, and a coaching conversation built on a
coin flip is worse than no conversation. `attribute_bucket` returns the REASON alongside the answer
so the UI can say "unattributed: 3 people on shift" instead of quietly showing a blank.

The practical consequence, which the settings page states out loud: per-employee behaviour works in
single-staff stores and degrades to store-level everywhere else. That is a property of not doing
face recognition, and it is the intended trade.

EVERY CLASSIFIER CAN RETURN "UNKNOWN", AND DOES SO OFTEN
────────────────────────────────────────────────────────
Each rule below has an explicit ambiguous band that resolves to UNKNOWN rather than to a guess, and
unknown time is reported as unknown rather than folded into the larger bucket. This matters because
the failure mode of a behaviour system is not missing a yawn — it is confidently telling a manager
that a rep did nothing for two hours when the rep was in the stockroom, off-camera, or bent behind a
counter. Unknown seconds are the honest majority in most real installs and the UI shows them.
"""

# ── COCO-17 keypoint indices, the layout every mainstream pose model emits ───────────────────────
L_SHOULDER, R_SHOULDER = 5, 6
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14

# Posture bands. `thigh / torso` in IMAGE SPACE: standing, the thigh projects to roughly the length
# of the torso; seated, it foreshortens towards horizontal and collapses towards zero. The gap
# between the two thresholds is the ambiguous band and it resolves to unknown on purpose — a person
# leaning, crouching to a low shelf, or half-turned lands there, and none of those is "sitting".
SIT_RATIO = 0.45
STAND_RATIO = 0.68
MIN_KEYPOINT_CONF = 0.4
MIN_TORSO = 0.02          # normalized; below this the person is too small/far to read posture from

POSTURES = ("standing", "sitting", "unknown")
MOTIONS = ("walking", "stationary", "unknown")


def _pt(kp, i):
    """One keypoint as (x, y) if it is present and confident enough, else None.

    Accepts (x, y, conf) or (x, y) — a model that emits no confidence is treated as confident,
    because a missing confidence channel is not evidence of a bad keypoint."""
    try:
        p = kp[i]
    except (TypeError, IndexError, KeyError):
        return None
    if p is None:
        return None
    try:
        x, y = float(p[0]), float(p[1])
        conf = float(p[2]) if len(p) > 2 else 1.0
    except (TypeError, ValueError, IndexError):
        return None
    if conf < MIN_KEYPOINT_CONF:
        return None
    return (x, y)


def _mid(kp, a, b):
    """Midpoint of a left/right pair, or the one side that is visible.

    A person standing side-on to the camera legitimately shows one hip and one knee, and refusing to
    read posture from that would return unknown for anybody not facing the lens square on."""
    pa, pb = _pt(kp, a), _pt(kp, b)
    if pa and pb:
        return ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
    return pa or pb


def classify_posture(keypoints, sit_ratio=SIT_RATIO, stand_ratio=STAND_RATIO):
    """standing | sitting | unknown, from pose keypoints in normalized image coordinates.

    y grows DOWNWARD, as every image coordinate system does.

    THE ASSUMPTION, stated because it is the thing that breaks: this reads posture out of the
    IMAGE, so it assumes a roughly eye-level camera. An overhead camera foreshortens the standing
    thigh exactly the way sitting does, and would report a whole store as seated. The analyzer
    therefore only runs posture on cameras the operator has marked as eye-level, and a camera left
    unmarked produces no posture at all rather than a wrong one.
    """
    shoulder = _mid(keypoints, L_SHOULDER, R_SHOULDER)
    hip = _mid(keypoints, L_HIP, R_HIP)
    knee = _mid(keypoints, L_KNEE, R_KNEE)
    # Knees hidden behind a counter is the single most common case in a phone store, and it is
    # unreadable rather than seated. Say so.
    if not (shoulder and hip and knee):
        return "unknown"
    torso = hip[1] - shoulder[1]
    thigh = knee[1] - hip[1]
    if torso < MIN_TORSO:
        return "unknown"
    # Knees ABOVE the hips: not a posture this rule models (lying down, a child carried, a bad
    # detection). Unknown beats inventing an answer for it.
    if thigh < 0:
        return "unknown"
    ratio = thigh / torso
    if ratio < sit_ratio:
        return "sitting"
    if ratio >= stand_ratio:
        return "standing"
    return "unknown"


def classify_motion(distance, seconds, walk_speed):
    """walking | stationary | unknown, from how far a track's foot point moved.

    `distance` is in normalized frame widths and `walk_speed` in frame widths per second, so the
    threshold is resolution-independent but NOT distance-independent: a person walking at the back
    of a deep store covers fewer frame widths per second than the same walk by the door. The
    operator tunes walk_speed per camera for that reason.
    """
    try:
        distance, seconds, walk_speed = float(distance), float(seconds), float(walk_speed)
    except (TypeError, ValueError):
        return "unknown"
    if seconds <= 0 or distance < 0 or walk_speed <= 0:
        return "unknown"
    return "walking" if (distance / seconds) >= walk_speed else "stationary"


def near_another_person(point, others, max_distance):
    """Was this track within `max_distance` of any other track?

    DELIBERATELY NOT CALLED "serving a customer". The analyzer cannot tell a member of staff from a
    shopper — it has one class, `person` — so what this measures is two people standing close
    together, which is a rep with a customer, two reps talking, or a couple browsing. The column it
    feeds is named seconds_with_another_person and the UI repeats the caveat rather than burying it.

    The reliable version of "was the rep engaging the customer" is the transcript path, which reads
    what was actually said; this is the crude proxy for cameras with no audio.
    """
    try:
        px, py = float(point[0]), float(point[1])
        max_distance = float(max_distance)
    except (TypeError, ValueError, IndexError):
        return False
    if max_distance <= 0:
        return False
    for o in others or []:
        try:
            ox, oy = float(o[0]), float(o[1])
        except (TypeError, ValueError, IndexError):
            continue
        if ((ox - px) ** 2 + (oy - py) ** 2) ** 0.5 <= max_distance:
            return True
    return False


def yawn_events(mar_series, sample_seconds, threshold=0.6, min_seconds=1.5):
    """How many sustained wide-mouth episodes occurred in a mouth-aspect-ratio series.

    WHAT THIS IS NOT. A wide mouth held for two seconds is a yawn, a laugh, a shout across the
    store, a deep breath, or a rep saying a long vowel to a hard-of-hearing customer. Nothing in
    the signal separates those, and no threshold will. The count this returns is therefore reported
    as "sustained wide-mouth episodes" wherever it is shown, never as a tiredness score, and the
    page says a manager should treat a number here as a prompt to go and look, never as a finding.

    WHAT IT DOES NOT STORE, which is the part that keeps this out of BIPA territory. The ratio is
    computed at the edge from two distances on one frame and thrown away with that frame. No face
    landmarks, no face crop, no descriptor, and nothing that could match this face to another face
    ever leaves the analyzer or reaches a table. A COUNT of episodes is not a biometric identifier;
    a face geometry template is, and this module still has no column for one.

    Requires the mouth to stay open for min_seconds — a single high sample is a blink of the
    detector, and counting it would make the number mostly noise.
    """
    try:
        sample_seconds = float(sample_seconds)
        threshold = float(threshold)
        min_seconds = float(min_seconds)
    except (TypeError, ValueError):
        return 0
    if sample_seconds <= 0:
        return 0
    need = max(1, int(round(min_seconds / sample_seconds)))
    events, run = 0, 0
    for v in mar_series or []:
        try:
            open_now = float(v) >= threshold
        except (TypeError, ValueError):
            open_now = False       # an unreadable sample breaks the run rather than extending it
        if open_now:
            run += 1
            if run == need:        # count once, at the moment it qualifies, not on every sample after
                events += 1
        else:
            run = 0
    return events


# ── Attribution: the decision that decides whether a name may be attached at all ─────────────────
ATTRIBUTION_REASONS = (
    "single_on_shift",      # named
    "nobody_on_shift",      # unattributed
    "multiple_on_shift",    # unattributed
    "consent_missing",      # unattributed
    "consent_declined",     # unattributed — outranks consent_mode='off'
    "consent_withdrawn",    # unattributed — outranks consent_mode='off'
)


def attribute_bucket(on_shift, consent_by_employee, consent_mode="required"):
    """(employee_id, reason) for a bucket of activity — or (None, why not).

    THE RULE: name somebody only when there is exactly ONE candidate. With two people on shift, the
    honest statement about a sitting track is "one of these two was sitting", which is not a fact
    about either of them. Storing it against a guess would put a coin flip into a coaching
    conversation and, eventually, into somebody's file.

    This is what makes the whole feature safe to ship without face recognition, and it is also its
    main limitation. Both halves are stated on the page.

    `consent_mode` mirrors the audio path: 'required' (default) refuses to name an employee who has
    not signed, 'off' exists only for a tenant holding its own recorded release and is audited when
    set. An unrecognised mode is treated as 'required' — an unreadable setting must not be the thing
    that switches consent off.

    A DECLINED OR WITHDRAWN row is never overridable, not even by consent_mode='off'. This mirrors
    config.consent_ok() exactly, and for its reason: consent is revocable by definition, so a
    withdrawal has to outrank a tenant-level setting or withdrawing it would mean nothing.
    """
    ids = [str(e) for e in (on_shift or []) if e]
    # Deduplicate: an employee with two overlapping clock rows (a forgotten punch, a shift
    # extension) is still one person, and counting them twice would wrongly read as "multiple".
    seen, unique = set(), []
    for e in ids:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    if not unique:
        return (None, "nobody_on_shift")
    if len(unique) > 1:
        return (None, "multiple_on_shift")
    emp = unique[0]
    status = str(((consent_by_employee or {}).get(emp) or {}).get("status") or "").strip().lower()
    if status in ("declined", "withdrawn"):
        return (None, f"consent_{status}")
    if status == "signed":
        return (emp, "single_on_shift")
    if str(consent_mode or "required").strip().lower() == "off":
        return (emp, "single_on_shift")
    return (None, "consent_missing")


# ── Rolling per-frame observations up into one stored bucket ─────────────────────────────────────
def roll_up(observations, sample_seconds):
    """Per-frame observations for ONE track in ONE bucket → the row that gets stored.

    An observation is {"posture": ..., "motion": ..., "with_person": bool}. Seconds are counted by
    multiplying sample counts by the interval, so a bucket that lost frames reports less observed
    time rather than stretching what it saw to fill the window.

    UNKNOWN IS CARRIED, NOT DROPPED. seconds_observed is the sum of every category including the
    unknowns, so a reader can always compute what fraction of the time we actually had an opinion.
    A view that quietly divided by known-seconds only would show a rep who was readable for 4
    minutes out of an hour as "62% standing" with nothing to indicate that the hour was mostly
    guesswork.
    """
    try:
        sample_seconds = float(sample_seconds)
    except (TypeError, ValueError):
        sample_seconds = 0.0
    if sample_seconds <= 0:
        sample_seconds = 0.0
    counts = {"standing": 0, "sitting": 0, "unknown_posture": 0,
              "walking": 0, "stationary": 0, "unknown_motion": 0, "with_person": 0}
    n = 0
    for o in observations or []:
        if not isinstance(o, dict):
            continue
        n += 1
        p = o.get("posture")
        counts["standing" if p == "standing" else
               "sitting" if p == "sitting" else "unknown_posture"] += 1
        m = o.get("motion")
        counts["walking" if m == "walking" else
               "stationary" if m == "stationary" else "unknown_motion"] += 1
        if o.get("with_person"):
            counts["with_person"] += 1
    s = lambda k: round(counts[k] * sample_seconds, 1)  # noqa: E731
    return {
        "samples": n,
        "seconds_observed": round(n * sample_seconds, 1),
        "seconds_standing": s("standing"),
        "seconds_sitting": s("sitting"),
        "seconds_posture_unknown": s("unknown_posture"),
        "seconds_walking": s("walking"),
        "seconds_stationary": s("stationary"),
        "seconds_motion_unknown": s("unknown_motion"),
        "seconds_with_another_person": s("with_person"),
    }


def idle_seconds(bucket, idle_after=0):
    """Seconds that count as "not doing anything" — stationary, and not near another person.

    THE WORD THE OPERATOR ASKED FOR IS "IDLE"; THE THING MEASURED IS "ALONE AND NOT MOVING". A rep
    counting stock, reading a planogram on a tablet, on the phone to a carrier, or standing at the
    till processing a return is stationary and alone, and lands in this number. It is a prompt to
    look, not a finding, and it is labelled on the page as "alone and stationary" for exactly that
    reason — a column called "idle" would be read as a verdict.

    `idle_after` suppresses buckets below a floor: brief stationary spells are how anybody stands.
    """
    if not isinstance(bucket, dict):
        return 0.0
    alone = float(bucket.get("seconds_stationary") or 0) - float(
        bucket.get("seconds_with_another_person") or 0)
    alone = max(0.0, alone)
    try:
        floor = float(idle_after or 0)
    except (TypeError, ValueError):
        floor = 0.0
    return round(alone, 1) if alone >= floor else 0.0


def coverage(staff_seconds, customer_seconds, window_seconds):
    """Store-level floor coverage for one window: was anybody there to serve.

    This is the signal that needed no pose model and no face at all — it falls straight out of the
    tracks the analyzer already computes — and it is the one a manager can act on without any of
    the caveats above, because it says nothing about a named person. `unstaffed_with_customers` is
    the number that matters: a floor with nobody serving and somebody waiting.
    """
    try:
        staff_seconds = max(0.0, float(staff_seconds or 0))
        customer_seconds = max(0.0, float(customer_seconds or 0))
        window_seconds = float(window_seconds or 0)
    except (TypeError, ValueError):
        return {"staffed_seconds": 0.0, "unstaffed_seconds": 0.0,
                "unstaffed_with_customers": 0.0, "window_seconds": 0.0}
    if window_seconds <= 0:
        return {"staffed_seconds": 0.0, "unstaffed_seconds": 0.0,
                "unstaffed_with_customers": 0.0, "window_seconds": 0.0}
    staffed = min(staff_seconds, window_seconds)
    unstaffed = round(window_seconds - staffed, 1)
    # Customer time overlapping the unstaffed stretch, bounded by both. Without a per-second
    # timeline this is the tightest bound the bucket supports, and overstating it would invent
    # unattended customers that may never have been there.
    return {
        "staffed_seconds": round(staffed, 1),
        "unstaffed_seconds": unstaffed,
        "unstaffed_with_customers": round(min(customer_seconds, unstaffed), 1),
        "window_seconds": round(window_seconds, 1),
    }


# ── Who was on shift during a bucket ─────────────────────────────────────────────────────────────
def _dt(value):
    """A timestamp from the database or the analyzer, as an aware datetime, or None.

    Everything here is compared against everything else, so a naive value would raise mid-comparison
    on one row and take the whole batch with it. Naive input is read as UTC, which is what every
    producer in this path emits."""
    from datetime import datetime, timezone as _tz
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_tz.utc)
    try:
        s = str(value).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=_tz.utc)


def on_shift_for_bucket(punches, bucket_start, bucket_seconds):
    """Employee codes whose shift OVERLAPS [bucket_start, bucket_start + bucket_seconds).

    An activity bucket is usually posted after the fact, so "who is clocked in right now" is the
    wrong question — it has to be who was on shift THEN. A punch still open (no clock_out) counts as
    running to the end of time, which is the only safe reading of a shift nobody has closed.

    HALF-OPEN AT BOTH ENDS, and this is not a nicety: a shift that ends exactly as a bucket begins
    does not overlap it, and neither does one that starts exactly as it ends. Getting that wrong at
    a shift change would put TWO people in the candidate set for the handover bucket — and since two
    candidates means nobody is named, every shift-change bucket in the estate would silently go
    unattributed. Overlapping punches ARE the point elsewhere; here the boundary is exact.
    """
    start = _dt(bucket_start)
    if start is None:
        return []
    try:
        span = float(bucket_seconds)
    except (TypeError, ValueError):
        return []
    if span <= 0:
        return []
    from datetime import timedelta
    end = start + timedelta(seconds=span)
    out = []
    for p in punches or []:
        if not isinstance(p, dict):
            continue
        code = p.get("employee_id")
        if not code:
            continue
        ci = _dt(p.get("clock_in"))
        if ci is None:
            continue                       # a punch with no start cannot be placed in time
        co = _dt(p.get("clock_out"))       # None = still open = still on shift
        if ci < end and (co is None or co > start):
            out.append(str(code))
    return out
