"""Proves the employee-activity decisions offline — no camera, no model, no database.

WHY THIS EXISTS. These functions decide what a manager is told about a named person. Two of the
properties below are the difference between a coaching aid and a liability, so they are asserted
rather than assumed:

  1. ATTRIBUTION REFUSES TO GUESS. With more than one employee on shift, no row may carry a name.
     There is no threshold, no "most likely", and no fallback that picks one — the only way an
     employee_id is written is exactly one candidate who has signed consent.
  2. EVERY CLASSIFIER HAS A REAL UNKNOWN. A rule that answers standing-or-sitting for every input
     is a rule that is confidently wrong about occluded, distant and half-turned people, which in a
     phone store is most of them. The ambiguous bands are tested from both sides.

Run:  cd backend && python harness_vision_activity.py
"""
from app.modules.vision import activity as A
from app.modules.vision import config as C
from app.modules.vision import ingest as I

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    if not ok:
        print(f"  FAIL {name}")


def eq(name, got, want):
    ok = got == want
    check(name, ok)
    if not ok:
        print(f"       got {got!r}, want {want!r}")


# ── Pose fixtures. COCO-17, normalized, y grows downward. ────────────────────────────────────────
def pose(shoulder_y, hip_y, knee_y, conf=0.9, x=0.5):
    kp = [[0.0, 0.0, 0.0] for _ in range(17)]
    for i, y in ((A.L_SHOULDER, shoulder_y), (A.R_SHOULDER, shoulder_y)):
        kp[i] = [x - 0.03, y, conf]
    for i, y in ((A.L_HIP, hip_y), (A.R_HIP, hip_y)):
        kp[i] = [x - 0.02, y, conf]
    for i, y in ((A.L_KNEE, knee_y), (A.R_KNEE, knee_y)):
        kp[i] = [x, y, conf]
    return kp


print("── posture ─────────────────────────────────────────────────────────────────")
# Standing: thigh projects to about the length of the torso.
eq("upright body reads as standing",
   A.classify_posture(pose(0.30, 0.55, 0.80)), "standing")
# Seated: the thigh foreshortens towards horizontal, so knees sit close under the hips.
eq("foreshortened thigh reads as sitting",
   A.classify_posture(pose(0.30, 0.55, 0.62)), "sitting")

# THE AMBIGUOUS BAND, from both sides. A leaning or crouching person lands here and must NOT be
# forced into either answer.
eq("mid-band (leaning, crouching, half-turned) is unknown",
   A.classify_posture(pose(0.30, 0.55, 0.69)), "unknown")
eq("just under the standing threshold is still unknown, not standing",
   A.classify_posture(pose(0.0, 0.10, 0.1 + 0.10 * 0.67)), "unknown")
eq("just over the standing threshold is standing",
   A.classify_posture(pose(0.0, 0.10, 0.1 + 0.10 * 0.69)), "standing")
eq("just under the sitting threshold is sitting",
   A.classify_posture(pose(0.0, 0.10, 0.1 + 0.10 * 0.44)), "sitting")
eq("just over the sitting threshold is unknown, not sitting",
   A.classify_posture(pose(0.0, 0.10, 0.1 + 0.10 * 0.46)), "unknown")

# THE PHONE-STORE CASE: a rep behind a counter has no visible knees. That is unreadable, and calling
# it "sitting" would report a whole shift of counter work as sitting down.
hidden = pose(0.30, 0.55, 0.80)
hidden[A.L_KNEE] = [0.5, 0.8, 0.05]
hidden[A.R_KNEE] = [0.5, 0.8, 0.05]
eq("knees hidden behind a counter is unknown, NOT sitting",
   A.classify_posture(hidden), "unknown")
eq("low-confidence hips are unknown",
   A.classify_posture(pose(0.30, 0.55, 0.80, conf=0.2)), "unknown")
eq("no keypoints at all is unknown", A.classify_posture([]), "unknown")
eq("None is unknown", A.classify_posture(None), "unknown")
eq("garbage keypoints are unknown", A.classify_posture([["a", "b", "c"]] * 17), "unknown")

# A person side-on shows one hip and one knee. Refusing that would return unknown for anyone not
# square to the lens — most of a store.
side = [[0.0, 0.0, 0.0] for _ in range(17)]
side[A.L_SHOULDER] = [0.5, 0.30, 0.9]
side[A.L_HIP] = [0.5, 0.55, 0.9]
side[A.L_KNEE] = [0.5, 0.80, 0.9]
eq("one visible side is enough to read posture", A.classify_posture(side), "standing")

# Too small in frame to read anything from.
eq("a person too far away to measure is unknown",
   A.classify_posture(pose(0.500, 0.505, 0.510)), "unknown")
# Knees above hips is not a posture this models.
eq("inverted geometry is unknown, not sitting",
   A.classify_posture(pose(0.30, 0.55, 0.40)), "unknown")

print("── motion ──────────────────────────────────────────────────────────────────")
eq("crossing the frame is walking", A.classify_motion(0.30, 2.0, 0.05), "walking")
eq("barely moving is stationary", A.classify_motion(0.01, 2.0, 0.05), "stationary")
eq("exactly at the threshold counts as walking", A.classify_motion(0.10, 2.0, 0.05), "walking")
eq("zero elapsed time is unknown, not stationary", A.classify_motion(0.30, 0, 0.05), "unknown")
eq("negative time is unknown", A.classify_motion(0.30, -1, 0.05), "unknown")
eq("an unset threshold is unknown, not walking", A.classify_motion(0.30, 2.0, 0), "unknown")
eq("nonsense is unknown", A.classify_motion("x", None, 0.05), "unknown")

print("── proximity ───────────────────────────────────────────────────────────────")
eq("somebody standing next to you counts",
   A.near_another_person((0.5, 0.5), [(0.55, 0.5)], 0.12), True)
eq("somebody across the store does not",
   A.near_another_person((0.5, 0.5), [(0.9, 0.9)], 0.12), False)
eq("alone in frame is not near anyone", A.near_another_person((0.5, 0.5), [], 0.12), False)
eq("exactly at the radius counts", A.near_another_person((0.5, 0.5), [(0.62, 0.5)], 0.12), True)
eq("one bad neighbour does not hide a real one",
   A.near_another_person((0.5, 0.5), [("x", "y"), (0.52, 0.5)], 0.12), True)
eq("a zero radius never matches", A.near_another_person((0.5, 0.5), [(0.5, 0.5)], 0), False)
eq("a broken point is not near anyone", A.near_another_person(None, [(0.5, 0.5)], 0.12), False)

print("── sustained wide-mouth episodes ───────────────────────────────────────────")
# Sampled twice a second, so 1.5s of sustained opening needs 3 consecutive samples.
eq("a sustained opening is one episode",
   A.yawn_events([0.2, 0.7, 0.8, 0.75, 0.2], 0.5), 1)
# THE NOISE CASE: single high samples are detector jitter. Counting them makes the number useless.
eq("a single high sample is not an episode", A.yawn_events([0.2, 0.9, 0.2], 0.5), 0)
eq("two samples still short of 1.5s is not an episode",
   A.yawn_events([0.2, 0.9, 0.9, 0.2], 0.5), 0)
eq("one long opening counts ONCE, not once per sample",
   A.yawn_events([0.9] * 20, 0.5), 1)
eq("two separated openings are two episodes",
   A.yawn_events([0.9, 0.9, 0.9, 0.1, 0.1, 0.9, 0.9, 0.9], 0.5), 2)
eq("an unreadable sample breaks the run rather than extending it",
   A.yawn_events([0.9, 0.9, None, 0.9, 0.9], 0.5), 0)
eq("talking stays below the threshold", A.yawn_events([0.35] * 30, 0.5), 0)
eq("an empty series is zero", A.yawn_events([], 0.5), 0)
eq("no series at all is zero", A.yawn_events(None, 0.5), 0)
eq("a zero sample interval cannot be reasoned about", A.yawn_events([0.9] * 10, 0), 0)

print("── ATTRIBUTION — the rule that refuses to guess ────────────────────────────")
signed = {"e1": {"status": "signed"}, "e2": {"status": "signed"}}

eq("exactly one on shift, consented -> named",
   A.attribute_bucket(["e1"], signed), ("e1", "single_on_shift"))

# THE CORE REFUSAL. Two people on shift means no name, ever. There is no tie-break to get wrong
# because there is no tie-break.
eq("TWO on shift -> nobody is named",
   A.attribute_bucket(["e1", "e2"], signed), (None, "multiple_on_shift"))
eq("three on shift -> nobody is named",
   A.attribute_bucket(["e1", "e2", "e3"], signed), (None, "multiple_on_shift"))
check("a multi-staff bucket NEVER returns an employee id",
      all(A.attribute_bucket(c, signed)[0] is None
          for c in (["e1", "e2"], ["e2", "e1"], ["e1", "e2", "e3"], ["a", "b"])))

# A forgotten punch or a shift extension can duplicate one person. Still one person.
eq("the same employee twice is still one person",
   A.attribute_bucket(["e1", "e1"], signed), ("e1", "single_on_shift"))

eq("nobody on shift -> unattributed with a reason",
   A.attribute_bucket([], signed), (None, "nobody_on_shift"))
eq("None on shift -> unattributed", A.attribute_bucket(None, signed), (None, "nobody_on_shift"))
eq("blank entries do not count as people",
   A.attribute_bucket(["", None], signed), (None, "nobody_on_shift"))

print("── consent gates attribution ───────────────────────────────────────────────")
eq("no consent row -> unattributed", A.attribute_bucket(["e9"], signed), (None, "consent_missing"))
eq("pending consent -> unattributed",
   A.attribute_bucket(["e3"], {"e3": {"status": "pending"}}), (None, "consent_missing"))
eq("withdrawn consent -> unattributed",
   A.attribute_bucket(["e3"], {"e3": {"status": "withdrawn"}}), (None, "consent_withdrawn"))
eq("declined consent -> unattributed",
   A.attribute_bucket(["e3"], {"e3": {"status": "declined"}}), (None, "consent_declined"))

# A WITHDRAWAL OUTRANKS THE TENANT SETTING, exactly as config.consent_ok() has it for audio. If
# consent_mode='off' could override a withdrawal, withdrawing would mean nothing.
eq("consent_mode 'off' does NOT override a withdrawal",
   A.attribute_bucket(["e3"], {"e3": {"status": "withdrawn"}}, consent_mode="off"),
   (None, "consent_withdrawn"))
eq("consent_mode 'off' does NOT override a refusal",
   A.attribute_bucket(["e3"], {"e3": {"status": "declined"}}, consent_mode="off"),
   (None, "consent_declined"))
check("no consent_mode value can name a withdrawn employee",
      all(A.attribute_bucket(["e3"], {"e3": {"status": "withdrawn"}}, consent_mode=m)[0] is None
          for m in ("off", "OFF", "required", "", None, "whatever")))
eq("an empty consent map denies everyone", A.attribute_bucket(["e1"], {}), (None, "consent_missing"))
eq("consent_mode 'off' is the tenant's own release path",
   A.attribute_bucket(["e9"], {}, consent_mode="off"), ("e9", "single_on_shift"))
# An unreadable setting must not be the thing that switches consent off.
eq("an unrecognised consent mode falls back to REQUIRED",
   A.attribute_bucket(["e9"], {}, consent_mode="whatever"), (None, "consent_missing"))
eq("an empty consent mode falls back to REQUIRED",
   A.attribute_bucket(["e9"], {}, consent_mode=""), (None, "consent_missing"))
eq("None consent mode falls back to REQUIRED",
   A.attribute_bucket(["e9"], {}, consent_mode=None), (None, "consent_missing"))
# Consent never rescues a multi-staff bucket: the refusal comes first.
eq("consent cannot unlock a two-person bucket",
   A.attribute_bucket(["e1", "e2"], signed, consent_mode="off"), (None, "multiple_on_shift"))

print("── roll-up carries the unknowns ────────────────────────────────────────────")
obs = ([{"posture": "standing", "motion": "stationary", "with_person": True}] * 10 +
       [{"posture": "sitting", "motion": "stationary", "with_person": False}] * 5 +
       [{"posture": "unknown", "motion": "walking", "with_person": False}] * 5)
b = A.roll_up(obs, 2.0)
eq("observed seconds count every sample", b["seconds_observed"], 40.0)
eq("standing seconds", b["seconds_standing"], 20.0)
eq("sitting seconds", b["seconds_sitting"], 10.0)
eq("unknown posture is REPORTED, not folded into the others",
   b["seconds_posture_unknown"], 10.0)
eq("walking seconds", b["seconds_walking"], 10.0)
eq("with-another-person seconds", b["seconds_with_another_person"], 20.0)
check("posture seconds always add up to observed seconds",
      abs(b["seconds_standing"] + b["seconds_sitting"] + b["seconds_posture_unknown"]
          - b["seconds_observed"]) < 0.05)
check("motion seconds always add up to observed seconds",
      abs(b["seconds_walking"] + b["seconds_stationary"] + b["seconds_motion_unknown"]
          - b["seconds_observed"]) < 0.05)

# A bucket that lost frames reports LESS observed time — it does not stretch what it saw to fill
# the window, which would report a camera outage as a fully-observed idle shift.
short = A.roll_up([{"posture": "standing", "motion": "stationary"}] * 3, 2.0)
eq("a bucket with dropped frames reports only what it saw", short["seconds_observed"], 6.0)
eq("no observations is an empty bucket, not a zero-second shift",
   A.roll_up([], 2.0)["seconds_observed"], 0.0)
eq("None observations does not throw", A.roll_up(None, 2.0)["samples"], 0)
eq("non-dict observations are skipped", A.roll_up(["x", 5, None], 2.0)["samples"], 0)
eq("a zero interval yields zero seconds but keeps the sample count",
   (A.roll_up(obs, 0)["seconds_observed"], A.roll_up(obs, 0)["samples"]), (0.0, 20))

print("── 'alone and stationary' (what the operator called idle) ──────────────────")
eq("stationary time with somebody nearby is NOT alone time",
   A.idle_seconds({"seconds_stationary": 60, "seconds_with_another_person": 60}), 0.0)
eq("stationary and alone counts",
   A.idle_seconds({"seconds_stationary": 60, "seconds_with_another_person": 0}), 60.0)
eq("partly accompanied subtracts",
   A.idle_seconds({"seconds_stationary": 60, "seconds_with_another_person": 20}), 40.0)
eq("more company than stationary time never goes negative",
   A.idle_seconds({"seconds_stationary": 10, "seconds_with_another_person": 60}), 0.0)
eq("walking time is never idle time",
   A.idle_seconds({"seconds_walking": 600, "seconds_stationary": 0}), 0.0)
eq("a floor suppresses brief stationary spells",
   A.idle_seconds({"seconds_stationary": 30, "seconds_with_another_person": 0}, idle_after=120), 0.0)
eq("a spell over the floor is reported in full",
   A.idle_seconds({"seconds_stationary": 300, "seconds_with_another_person": 0}, idle_after=120), 300.0)
eq("a junk bucket is zero", A.idle_seconds(None), 0.0)

print("── who was on shift during the bucket ──────────────────────────────────────")
DAY = "2026-08-22"
def punch(code, ci, co=None):
    return {"employee_id": code,
            "clock_in": f"{DAY}T{ci}:00Z",
            "clock_out": (f"{DAY}T{co}:00Z" if co else None)}

shift = [punch("A", "09:00", "13:00"), punch("B", "13:00")]

eq("a bucket inside A's shift finds only A",
   A.on_shift_for_bucket(shift, f"{DAY}T10:00:00Z", 900), ["A"])
eq("a bucket after the handover finds only B",
   A.on_shift_for_bucket(shift, f"{DAY}T14:00:00Z", 900), ["B"])

# THE HANDOVER BOUNDARY. A's shift ends exactly as the 13:00 bucket begins. If the comparison were
# inclusive, BOTH would be candidates — and two candidates means nobody is named, so every
# shift-change bucket in the estate would silently go unattributed.
eq("the bucket ENDING at the handover sees only the outgoing person",
   A.on_shift_for_bucket(shift, f"{DAY}T12:45:00Z", 900), ["A"])
eq("the bucket STARTING at the handover sees only the incoming person",
   A.on_shift_for_bucket(shift, f"{DAY}T13:00:00Z", 900), ["B"])
check("no bucket in the whole day ever sees both",
      all(len(A.on_shift_for_bucket(shift, f"{DAY}T{h:02d}:{m:02d}:00Z", 900)) <= 1
          for h in range(24) for m in (0, 15, 30, 45)))

# An OVERLAPPING bucket genuinely has two people on the floor, and must say so — that is the case
# that correctly produces an unattributed row.
overlap = [punch("A", "09:00", "17:00"), punch("B", "12:00", "20:00")]
eq("genuinely overlapping shifts both appear",
   sorted(A.on_shift_for_bucket(overlap, f"{DAY}T13:00:00Z", 900)), ["A", "B"])
eq("...and that is exactly what makes the bucket unattributed",
   A.attribute_bucket(A.on_shift_for_bucket(overlap, f"{DAY}T13:00:00Z", 900),
                      {"A": {"status": "signed"}, "B": {"status": "signed"}}),
   (None, "multiple_on_shift"))

# An open punch is somebody who never clocked out. Treating it as closed would lose every bucket
# after their last punch; treating it as running forever is the only safe reading.
eq("an open punch still counts hours later",
   A.on_shift_for_bucket([punch("B", "13:00")], f"{DAY}T23:45:00Z", 900), ["B"])
eq("an open punch does NOT count before it started",
   A.on_shift_for_bucket([punch("B", "13:00")], f"{DAY}T09:00:00Z", 900), [])

eq("a CLOSED shift does not leak into the next day",
   A.on_shift_for_bucket([punch("A", "09:00", "17:00")], "2026-08-23T10:00:00Z", 900), [])
# ...whereas B's punch is still OPEN, so it legitimately does. A forgotten clock-out is a real and
# common state, and the row it produces is attributed to a person who may have gone home — which is
# why the UI shows attribution_reason and a manager can see it came from a single open punch.
eq("an OPEN punch keeps running into the next day, by design",
   A.on_shift_for_bucket(shift, "2026-08-23T10:00:00Z", 900), ["B"])
eq("nobody clocked in is an empty candidate set",
   A.on_shift_for_bucket([], f"{DAY}T10:00:00Z", 900), [])
eq("a punch with no clock_in cannot be placed in time",
   A.on_shift_for_bucket([{"employee_id": "A", "clock_in": None}], f"{DAY}T10:00:00Z", 900), [])
eq("a punch with no employee code is skipped",
   A.on_shift_for_bucket([punch(None, "09:00")], f"{DAY}T10:00:00Z", 900), [])
eq("a junk timestamp is skipped, not thrown",
   A.on_shift_for_bucket([{"employee_id": "A", "clock_in": "not a date"}],
                         f"{DAY}T10:00:00Z", 900), [])
eq("a naive timestamp is read as UTC rather than exploding",
   A.on_shift_for_bucket([{"employee_id": "A", "clock_in": f"{DAY}T09:00:00",
                           "clock_out": f"{DAY}T17:00:00"}], f"{DAY}T10:00:00Z", 900), ["A"])
eq("a bad bucket start yields nobody", A.on_shift_for_bucket(shift, "nope", 900), [])
eq("a zero-length bucket yields nobody",
   A.on_shift_for_bucket(shift, f"{DAY}T10:00:00Z", 0), [])
eq("non-dict punches are skipped", A.on_shift_for_bucket(["x"], f"{DAY}T10:00:00Z", 900), [])

print("── floor coverage ──────────────────────────────────────────────────────────")
c = A.coverage(staff_seconds=600, customer_seconds=300, window_seconds=900)
eq("staffed seconds", c["staffed_seconds"], 600.0)
eq("unstaffed seconds", c["unstaffed_seconds"], 300.0)
eq("customers present while unstaffed is bounded by both",
   c["unstaffed_with_customers"], 300.0)
eq("a fully staffed window has nobody waiting",
   A.coverage(900, 300, 900)["unstaffed_with_customers"], 0.0)
eq("more staff time than window never invents negative unstaffed time",
   A.coverage(5000, 0, 900)["unstaffed_seconds"], 0.0)
eq("an empty store unstaffed has nobody waiting",
   A.coverage(0, 0, 900)["unstaffed_with_customers"], 0.0)
eq("a nonsense window is all zeros", A.coverage(600, 300, 0)["window_seconds"], 0.0)
eq("junk input does not throw", A.coverage("x", None, "y")["window_seconds"], 0.0)
check("staffed + unstaffed always equals the window",
      all(abs(A.coverage(s, 0, 900)["staffed_seconds"] + A.coverage(s, 0, 900)["unstaffed_seconds"]
              - 900) < 0.05 for s in (0, 1, 450, 899, 900, 100000)))

print("── INGEST — what the analyzer is NOT allowed to assert ────────────────────")
BUCKET = "2026-08-22T13:00:00Z"
CAM = {"id": "cam-1", "device_name": "enterprises/p/devices/d1", "store_code": "103",
       "enabled": True, "analytics_enabled": True, "posture_capable": True}
CAMS = {"enterprises/p/devices/d1": CAM}
AGENT = {"store_code": "103"}
VIDEO_OK = {"emp-uuid-1": {"status": "signed"}, "emp-uuid-2": {"status": "signed"}}


def cfg_on(**over):
    c = dict(C.DEFAULT_CONFIG)
    c.update({"enabled": True, "activity_enabled": True, "coverage_enabled": True})
    c.update(over)
    return c


def act_event(**over):
    ev = {"kind": "activity", "device_name": "enterprises/p/devices/d1",
          "track_key": "t-1", "bucket_start": BUCKET, "sample_seconds": 2.0,
          "observations": [{"posture": "sitting", "motion": "stationary"}] * 30}
    ev.update(over)
    return ev


def run(events, cfg=None, cam=None, on_shift=None, video=None, audio=None):
    cams = {"enterprises/p/devices/d1": {**CAM, **(cam or {})}}
    return I.normalize_batch({"events": events}, cams, cfg or cfg_on(), audio or {}, "org-1", AGENT,
                             on_shift_by_bucket=on_shift or {},
                             video_consents=VIDEO_OK if video is None else video)


# THE PRIMARY REFUSAL. The analyzer is a box in a stockroom. If a name in its payload could reach
# the employee_id column, anyone who got onto that box could write "sat down all afternoon" against
# a person of their choosing.
r = run([act_event(employee_id="emp-uuid-1", employee_name="Someone")])
eq("a payload-supplied employee_id NEVER reaches the row",
   r["activity"][0]["employee_id"], None)
eq("...and the row says why it is unattributed",
   r["activity"][0]["attribution_reason"], "nobody_on_shift")

# The name comes from the time clock, and only from there.
r = run([act_event()], on_shift={"103|" + BUCKET: ["emp-uuid-1"]})
eq("the time clock is what names a row", r["activity"][0]["employee_id"], "emp-uuid-1")
eq("...with the reason recorded", r["activity"][0]["attribution_reason"], "single_on_shift")

r = run([act_event()], on_shift={"103|" + BUCKET: ["emp-uuid-1", "emp-uuid-2"]})
eq("two on shift -> the row is stored, unattributed",
   (r["activity"][0]["employee_id"], r["activity"][0]["attribution_reason"]),
   (None, "multiple_on_shift"))

# AUDIO CONSENT IS NOT VIDEO CONSENT. Signing for a transcript is not signing for posture analysis.
r = run([act_event()], on_shift={"103|" + BUCKET: ["emp-uuid-9"]}, video={})
eq("an employee with no VIDEO consent is not named",
   (r["activity"][0]["employee_id"], r["activity"][0]["attribution_reason"]),
   (None, "consent_missing"))

# THE CONSENT-LAUNDERING CASE, and the reason the two maps are separate arguments rather than one
# with a fallback: this employee HAS signed for audio. That must not name them on a posture row.
r = run([act_event()], on_shift={"103|" + BUCKET: ["emp-uuid-9"]},
        video={}, audio={"emp-uuid-9": {"status": "signed"}})
eq("signing for AUDIO does not consent to posture analysis",
   (r["activity"][0]["employee_id"], r["activity"][0]["attribution_reason"]),
   (None, "consent_missing"))
# ...and the converse: video consent alone is enough for a video row.
r = run([act_event()], on_shift={"103|" + BUCKET: ["emp-uuid-9"]},
        video={"emp-uuid-9": {"status": "signed"}}, audio={})
eq("video consent alone names a video row", r["activity"][0]["employee_id"], "emp-uuid-9")

print("── INGEST — posture is enforced server-side ────────────────────────────────")
# An overhead camera foreshortens a standing thigh exactly as sitting does. A box that is
# misconfigured or out of date must not be able to put "sitting" against a name from one.
r = run([act_event()], cam={"posture_capable": False})
row = r["activity"][0]
eq("a camera not marked eye-level contributes NO sitting seconds", row["seconds_sitting"], 0.0)
eq("...no standing seconds either", row["seconds_standing"], 0.0)
eq("...and the time is reported as unknown, not lost",
   row["seconds_posture_unknown"], row["seconds_observed"])
check("the operator is told posture was dropped",
      "posture_dropped_camera_not_eye_level" in r["rejected"])
r = run([act_event()], cam={"posture_capable": True})
eq("an eye-level camera keeps its posture", r["activity"][0]["seconds_sitting"], 60.0)

print("── INGEST — the switches fail closed ──────────────────────────────────────")
eq("activity off -> nothing stored",
   run([act_event()], cfg=cfg_on(activity_enabled=False))["activity"], [])
check("activity off is REPORTED, not silent",
      "activity_not_enabled" in run([act_event()], cfg=cfg_on(activity_enabled=False))["rejected"])

# NULL, not 0: "we did not look" must stay distinguishable from "we looked and saw none", or a
# manager acts on a zero nobody ever measured.
r = run([act_event(wide_mouth_episodes=4)])
eq("face state off -> the column is NULL, not zero",
   r["activity"][0]["wide_mouth_episodes"], None)
check("...and the attempt is reported", "face_state_not_enabled" in r["rejected"])
r = run([act_event(wide_mouth_episodes=4)], cfg=cfg_on(face_state_enabled=True))
eq("face state on -> the count is stored", r["activity"][0]["wide_mouth_episodes"], 4)
r = run([act_event(wide_mouth_episodes=4)],
        cfg=cfg_on(face_state_enabled=True, activity_enabled=False))
eq("face state cannot outlive the activity switch it rides on", r["activity"], [])

print("── INGEST — cross-tenant and malformed input ──────────────────────────────")
eq("a camera this tenant does not own is refused",
   run([act_event(device_name="enterprises/p/devices/OTHER")])["activity"], [])
eq("an agent pinned to one store cannot write another's",
   run([act_event()], cam={"store_code": "999"})["activity"], [])
eq("a bucket with no track key is refused (a retry could not dedupe)",
   run([act_event(track_key="")])["activity"], [])
eq("a bucket with no start time is refused", run([act_event(bucket_start=None)])["activity"], [])
eq("a malformed event does not take the batch with it",
   len(run([{"kind": "activity"}, act_event()])["activity"]), 1)
eq("an unknown kind is counted, not stored",
   run([{"kind": "telepathy", "device_name": "enterprises/p/devices/d1"}])["activity"], [])

print("── INGEST — coverage names nobody ─────────────────────────────────────────")
cov = {"kind": "coverage", "device_name": "enterprises/p/devices/d1", "bucket_start": BUCKET,
       "window_seconds": 900, "staff_seconds": 600, "customer_seconds": 300, "peak_people": 4}
r = run([cov])
eq("coverage is stored", len(r["coverage"]), 1)
eq("staffed seconds", r["coverage"][0]["staffed_seconds"], 600.0)
eq("waiting customers on an unstaffed floor", r["coverage"][0]["unstaffed_with_customers"], 300.0)
check("coverage carries NO employee column at all",
      "employee_id" not in r["coverage"][0])
eq("coverage off -> nothing stored",
   run([cov], cfg=cfg_on(coverage_enabled=False))["coverage"], [])
eq("impossible staff seconds cannot create negative unstaffed time",
   run([{**cov, "staff_seconds": 99999}])["coverage"][0]["unstaffed_seconds"], 0.0)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"  - {f}")
raise SystemExit(1 if FAIL else 0)
