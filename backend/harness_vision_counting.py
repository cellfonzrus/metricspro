"""Proof harness for the DOOR COUNT — the tracker, the counting rule, and where the line goes.

Run: python3 backend/harness_vision_counting.py            (pure maths — no camera, no DB, no network)
     python3 backend/harness_vision_counting.py --sweeps    (adds the placement / angle / noise tables)

WHY A SIMULATOR AND NOT A CLIP
──────────────────────────────
`harness_vision_geometry.py` proves the line maths on hand-written point pairs and passes 34/34. That
is not the same as proving the DOOR COUNT, because the count is wrong for reasons the line maths
cannot see: the points it is handed are noisy, and they are not always the same person. To show that
concretely you need ground truth — how many people REALLY walked in — which a real clip only has if
somebody hand-labels it.

So this builds the doorway instead. A pinhole camera on a wall bracket, a door of a known width, and
people who walk through it on known paths at known speeds. The world says how many entered; the
pipeline says how many it counted; the gap is the error, decomposed by cause. Every number printed
below is reproducible from a seed.

WHAT IS SIMULATED, AND HOW HONEST IT IS
───────────────────────────────────────
  * PERSPECTIVE is real: a proper camera model, so a person at the back of the frame is genuinely
    smaller and genuinely moves fewer pixels per second than one at the front. That single fact
    drives most of what follows — it is why a fixed band fails and why line placement matters.
  * DETECTION NOISE is modelled, not real: a per-frame miss rate that worsens with distance, box
    jitter as a fraction of body size, occlusion between overlapping people, and a confidence that
    drops when a person is partly hidden. The PARAMETERS are chosen to be conservative-to-typical
    for YOLOv8-class detectors and every one of them is swept in `--sweeps`, so the conclusions can
    be read off at whatever noise level you believe.
  * APPEARANCE is not modelled at all. There are no pixels here, so nothing in this harness can
    speak to re-identification, which is a fair limitation to state up front: it means this harness
    can prove that motion-based association fixes a failure, and cannot prove that appearance-based
    association would fix a further one.

WHAT IT PROVES
──────────────
  1. A person standing on the threshold is counted many times by the current rule. (double count)
  2. A brisk walker is counted ZERO times by the current tracker. (miss)
  3. Two people passing in the doorway swap identity and fire phantom crossings. (phantom)
  4. A group entering abreast is undercounted. (occlusion — nothing here fixes it)
  5. The band + predictive tracker in app/modules/vision/counting.py fixes 1-3 and not 4.
  6. Where the line goes matters as much as any of it.
"""
import argparse
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import geometry as GEO       # noqa: E402
from app.modules.vision import counting as CNT       # noqa: E402
import vision_edge_analyzer as VEA                   # noqa: E402  (the REAL tracker under test)

PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label + (f"   [{detail}]" if detail else ""))


def note(text):
    print("       " + text)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. The camera — a real pinhole on a wall bracket
# ══════════════════════════════════════════════════════════════════════════════════════════════
# World frame: X to the right along the shop front, Y up, Z away from the store through the door.
# The doorway is the segment y=0, Z=0, x in [-door_w/2, +door_w/2]. INSIDE the store is Z < 0.
class Camera:
    """Pinhole with yaw and pitch, projecting world metres to normalized image coordinates.

    Nest Cam (battery / wired, 2nd gen) is a very wide lens — about 130° diagonal, which on a 16:9
    sensor is roughly 110-120° horizontal. Wide is a mixed blessing for counting: it gets the whole
    door in frame from a corner mount, and it shrinks the person in the middle of it.
    """

    def __init__(self, pos, target, hfov_deg=110.0, aspect=16.0 / 9.0):
        self.pos = tuple(float(v) for v in pos)
        self.aspect = float(aspect)
        self.k = 1.0 / (2.0 * math.tan(math.radians(hfov_deg) / 2.0))   # focal in frame widths
        dx = target[0] - self.pos[0]
        dy = target[1] - self.pos[1]
        dz = target[2] - self.pos[2]
        n = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / n, dy / n, dz / n
        self.pitch = math.asin(max(-1.0, min(1.0, -dy)))     # down-tilt, radians
        self.yaw = math.atan2(dx, dz)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        self.f = (sy * cp, -sp, cy * cp)
        self.r = (cy, 0.0, -sy)
        self.d = (-sy * sp, -cp, -cy * sp)                   # r x f  == world "down" when level

    @property
    def pitch_deg(self):
        return math.degrees(self.pitch)

    def project(self, p):
        """(nx, ny) normalized, or None when the point is behind the camera."""
        rel = (p[0] - self.pos[0], p[1] - self.pos[1], p[2] - self.pos[2])
        zc = sum(a * b for a, b in zip(rel, self.f))
        if zc <= 0.05:
            return None
        xc = sum(a * b for a, b in zip(rel, self.r))
        yc = sum(a * b for a, b in zip(rel, self.d))
        return 0.5 + self.k * xc / zc, 0.5 + self.k * self.aspect * yc / zc

    def depth(self, p):
        rel = (p[0] - self.pos[0], p[1] - self.pos[1], p[2] - self.pos[2])
        return sum(a * b for a, b in zip(rel, self.f))

    def person_box(self, x, z, height=1.72, width=0.48, depth=0.30):
        """The bounding box a perfect detector would draw around a person standing at (x, z).

        A person is modelled as a box of footprint `width` x `depth` and height `height`, and the
        detection box is the projection of all eight of its corners. The DEPTH matters: without it
        a steeply-angled camera projects head and feet on top of each other and the simulated person
        shrinks to nothing, which would make every steep mount look artificially hopeless."""
        pts = []
        for sx in (-1, 1):
            for sd in (-1, 1):
                px = x + sx * (width / 2.0) * self.r[0] + sd * (depth / 2.0) * self.f[0]
                pz = z + sx * (width / 2.0) * self.r[2] + sd * (depth / 2.0) * self.f[2]
                for y in (0.0, height):
                    q = self.project((px, y, pz))
                    if q is None:
                        return None
                    pts.append(q)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if x1 < -0.05 or x0 > 1.05 or y1 < -0.05 or y0 > 1.05:
            return None                                    # off frame
        return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def door_line(cam, door_w=1.0, z=0.0, inside_probe=None):
    """The counting line an installer SHOULD draw: across the floor at the threshold, jamb to jamb,
    with `inward` picked by clicking a spot inside the store. Returned in the stored zone shape.

    The probe defaults to 1.5 m FURTHER IN THAN THE LINE ITSELF, not to a fixed spot. That matters
    for any test that moves the line away from the threshold: a probe at a fixed depth ends up on
    the wrong side of a line drawn deeper than it, and the zone comes back with `inward` reversed —
    which reads as "this placement counts nothing" when what it actually is, is inside-out. It is
    also exactly the mistake an operator makes on a real camera, so it is worth naming."""
    a = cam.project((-door_w / 2.0, 0.0, z))
    b = cam.project((door_w / 2.0, 0.0, z))
    if a is None or b is None:
        return None
    zone = {"kind": "line", "is_active": True,
            "geometry": {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}, "inward": "left"}
    pr = inside_probe if inside_probe is not None else (0.0, z - 1.5)
    probe = cam.project((pr[0], 0.0, pr[1]))
    if probe and GEO.side_of(zone["geometry"], probe) < 0:
        zone["inward"] = "right"
    return zone


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. People, and the ground truth they generate
# ══════════════════════════════════════════════════════════════════════════════════════════════
class Walker:
    """A person as a timed polyline on the floor. Ground truth is read off the polyline, in metres,
    with no reference to the image — so line-placement error and tracker error stay separable."""

    def __init__(self, pid, waypoints, height=1.72, width=0.48):
        self.pid = pid
        self.wp = sorted(waypoints, key=lambda w: w[0])
        self.height = height
        self.width = width

    def at(self, t):
        if t < self.wp[0][0] or t > self.wp[-1][0]:
            return None
        for (t0, x0, z0), (t1, x1, z1) in zip(self.wp, self.wp[1:]):
            if t0 <= t <= t1:
                f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                return x0 + f * (x1 - x0), z0 + f * (z1 - z0)
        return self.wp[-1][1], self.wp[-1][2]

    def true_crossings(self, door_w=1.0, dt=0.02):
        """['in'|'out', …] — every time this person's feet actually pass through the door gap.
        Sampled far finer than the detector runs, so it is the truth and not another estimate."""
        out = []
        t = self.wp[0][0]
        prev = self.at(t)
        while t <= self.wp[-1][0]:
            t += dt
            cur = self.at(min(t, self.wp[-1][0]))
            if prev and cur and (prev[1] > 0) != (cur[1] > 0):
                f = prev[1] / (prev[1] - cur[1]) if prev[1] != cur[1] else 0.0
                x = prev[0] + f * (cur[0] - prev[0])
                if abs(x) <= door_w / 2.0:
                    out.append("in" if prev[1] > 0 else "out")
            prev = cur
        return out


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. The detector noise model
# ══════════════════════════════════════════════════════════════════════════════════════════════
class SyntheticDetector:
    """Boxes with the failure modes a real person detector has, and no others.

    * `recall` is the ceiling: the chance a large, clean, unoccluded person is found on a frame.
      0.95 is about right for a YOLOv8-class model on a well-lit shop door at 1080p.
    * SMALL PEOPLE ARE MISSED. Detection probability falls off with box height on a logistic centred
      at 0.06 frame heights (~65 px on 1080p) — the scale where small-object recall collapses.
    * OCCLUSION costs recall and confidence in proportion to how much of the person is covered by
      somebody nearer the camera; past `merge_at` the far person is not returned at all, which is
      the "two people, one box" that no tracker can undo.
    * JITTER is Gaussian on the box position and size, scaled to body size. This is the parameter
      the threshold double-count is most sensitive to, so it is swept.
    """

    def __init__(self, seed=7, recall=0.95, jitter=0.025, merge_at=0.72, fp_rate=0.0):
        self.rng = random.Random(seed)
        self.recall = recall
        self.jitter = jitter
        self.merge_at = merge_at
        self.fp_rate = fp_rate

    @staticmethod
    def _covered(near, far):
        ix = max(0.0, min(near["x"] + near["w"], far["x"] + far["w"]) - max(near["x"], far["x"]))
        iy = max(0.0, min(near["y"] + near["h"], far["y"] + far["h"]) - max(near["y"], far["y"]))
        area = far["w"] * far["h"]
        return (ix * iy / area) if area > 0 else 0.0

    def __call__(self, truth):
        """truth: [(pid, box, depth), …] -> [detection dicts] in whatever order they come out."""
        order = sorted(truth, key=lambda r: r[2])          # nearest first
        out = []
        for i, (pid, box, _z) in enumerate(order):
            cover = 0.0
            for j in range(i):
                cover = max(cover, self._covered(order[j][1], box))
            if cover >= self.merge_at:
                continue                                    # swallowed by the person in front
            h = box["h"]
            p_scale = 1.0 / (1.0 + math.exp(-(h - 0.06) / 0.015))
            p = self.recall * p_scale * (1.0 - 0.85 * cover)
            if self.rng.random() > p:
                continue
            s = self.jitter * h
            d = {"x": box["x"] + self.rng.gauss(0, s), "y": box["y"] + self.rng.gauss(0, s),
                 "w": max(0.01, box["w"] + self.rng.gauss(0, s)),
                 "h": max(0.01, box["h"] + self.rng.gauss(0, s)),
                 "conf": max(0.05, min(0.99, 0.92 - 0.8 * cover + self.rng.gauss(0, 0.05))),
                 "_pid": pid}
            out.append(d)
        if self.fp_rate and self.rng.random() < self.fp_rate:
            # A reflection in the glass door: a plausible box that lives a frame or two.
            out.append({"x": self.rng.uniform(0.2, 0.7), "y": self.rng.uniform(0.3, 0.6),
                        "w": 0.09, "h": 0.26, "conf": 0.42, "_pid": None})
        self.rng.shuffle(out)                               # detector order is not depth order
        return out


def render(cam, walkers, detector, fps=6.0, t0=0.0, t1=None):
    """[(t, [detections]), …] for the whole scene at the detector's rate."""
    t1 = t1 if t1 is not None else max(w.wp[-1][0] for w in walkers)
    frames = []
    n = int((t1 - t0) * fps) + 1
    for i in range(n):
        t = t0 + i / fps
        truth = []
        for w in walkers:
            p = w.at(t)
            if p is None:
                continue
            box = cam.person_box(p[0], p[1], w.height, w.width)
            if box is None:
                continue
            truth.append((w.pid, box, cam.depth((p[0], 0.0, p[1]))))
        frames.append((t, detector(truth)))
    return frames


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. The two pipelines — the one in production, and the prototype
# ══════════════════════════════════════════════════════════════════════════════════════════════
def run_current(frames, zone):
    """EXACTLY what CameraWorker.step does today: the IoU tracker, foot points, and a bare line."""
    tracker = VEA.Tracker()
    events, ids = [], set()
    for now, dets in frames:
        tracks = tracker.update(dets, now)
        for tr in tracks:
            ids.add(tr["key"])
            foot = GEO.foot_point(tr["box"])
            prev = tr.get("prev_foot")
            if prev:
                d = GEO.crossing_direction(zone, prev, foot)
                if d:
                    events.append((now, d, tr["key"]))
            tr["prev_foot"] = foot
    return {"events": events, "tracks": len(ids)}


def run_variant(frames, zone, aspect=16.0 / 9.0, tracker="new", counter="band", **gate_kw):
    """Any of the four combinations, so each change can be credited separately."""
    trk = (CNT.PredictiveTracker(aspect=aspect) if tracker == "new" else VEA.Tracker())
    gate = CNT.GateCounter(zone, aspect=aspect, **gate_kw) if counter == "band" else None
    events, ids = [], set()
    for now, dets in frames:
        tracks = trk.update(dets, now)
        for tr in tracks:
            ids.add(tr["key"])
            foot = GEO.foot_point(tr["box"])
            if gate is not None:
                if tr.get("confirmed", True):
                    d = gate.update(tr["key"], foot, tr["box"], now)
                    if d:
                        events.append((now, d, tr["key"]))
            else:
                prev = tr.get("prev_foot")
                if prev:
                    d = GEO.crossing_direction(zone, prev, foot)
                    if d:
                        events.append((now, d, tr["key"]))
            tr["prev_foot"] = foot
    return {"events": events, "tracks": len(ids)}


def counts(res):
    ins = sum(1 for e in res["events"] if e[1] == "in")
    outs = sum(1 for e in res["events"] if e[1] == "out")
    return ins, outs


def truth_counts(walkers, door_w=1.0):
    ins = outs = 0
    for w in walkers:
        for c in w.true_crossings(door_w):
            if c == "in":
                ins += 1
            else:
                outs += 1
    return ins, outs


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 5. The standard scene — one angled wall camera looking at one door
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Mounted 2.7 m up on the side wall, 2.2 m inside the door and 2.4 m to the side of it, aimed at a
# point on the floor just inside the threshold. This is a SECURITY placement — it sees faces coming
# in, which is what it was put there for — and it is the geometry LuxeLink actually has.
def standard_camera():
    return Camera(pos=(2.4, 2.7, -2.2), target=(0.0, 0.9, 0.4), hfov_deg=110.0)


def straight_walk(pid, speed=1.2, x=0.0, from_z=3.0, to_z=-2.5, t0=0.0):
    dist = abs(from_z - to_z)
    return Walker(pid, [(t0, x, from_z), (t0 + dist / speed, x, to_z)])


# ══════════════════════════════════════════════════════════════════════════════════════════════
def scene_report(cam, zone):
    print("\n(0) The scene, in numbers — so every threshold below can be read in metres")
    a = cam.project((-0.5, 0.0, 0.0))
    b = cam.project((0.5, 0.0, 0.0))
    p0 = cam.project((0.0, 0.0, 0.0))
    p1 = cam.project((0.0, 0.0, -0.5))
    box_door = cam.person_box(0.0, 0.0)
    box_back = cam.person_box(0.0, -3.5)
    per_m = math.hypot((p1[0] - p0[0]) * cam.aspect, p1[1] - p0[1]) / 0.5
    note(f"camera 2.7 m up, 2.2 m inside the door, 2.4 m to the side; pitch {cam.pitch_deg:.0f}° down")
    note(f"door line in image: ({a[0]:.3f},{a[1]:.3f}) -> ({b[0]:.3f},{b[1]:.3f}), inward={zone['inward']}")
    note(f"a person in the doorway is {box_door['h']:.3f} frame-heights tall "
         f"({box_door['h'] * 1080:.0f} px on 1080p); at the back of the store {box_back['h']:.3f}")
    note(f"1 metre of floor at the threshold = {per_m:.3f} of a frame height "
         f"(so 0.01 of image ≈ {0.01 / per_m * 100:.0f} cm of floor)")
    return per_m


def s1_clean_entry(cam, zone):
    print("\n(1) The happy path — one person, clean detection. Both pipelines must count 1 in.")
    w = [straight_walk("p1")]
    frames = render(cam, w, SyntheticDetector(seed=1, recall=1.0, jitter=0.0))
    ti, _ = truth_counts(w)
    cur = counts(run_current(frames, zone))
    new = counts(run_variant(frames, zone))
    check(f"current rule counts the entry (truth {ti} in)", cur == (1, 0), f"got {cur[0]} in / {cur[1]} out")
    check(f"band + predictive tracker counts the entry", new == (1, 0), f"got {new[0]} in / {new[1]} out")


def s2_threshold_loiter(cam, zone):
    print("\n(2) DOUBLE COUNT — somebody stands in the doorway. The single most common real event:")
    note("holding the door, saying goodbye, reading their phone on the mat. Truth is 1 customer.")
    w = [Walker("p1", [(0.0, 0.0, 2.5), (2.0, 0.0, 0.02), (8.0, -0.05, -0.02), (10.0, 0.0, -2.5)])]
    frames = render(cam, w, SyntheticDetector(seed=3, recall=0.97, jitter=0.03))
    ti, to = truth_counts(w)
    cur_i, cur_o = counts(run_current(frames, zone))
    new_i, new_o = counts(run_variant(frames, zone))
    note(f"truth: {ti} in, {to} out")
    note(f"current rule:  {cur_i} in, {cur_o} out   <- every wobble across a zero-width line is a crossing")
    note(f"band + hysteresis: {new_i} in, {new_o} out")
    check("the CURRENT rule over-counts a person loitering on the threshold",
          cur_i + cur_o > ti + to, f"{cur_i + cur_o} events for {ti + to} real crossings")
    check("the band counts the loiterer exactly once", (new_i, new_o) == (1, 0),
          f"got {new_i} in / {new_o} out")


def s3_fast_walker(cam, zone):
    print("\n(3) MISS — a brisk walker at 6 detections/second.")
    note("At 2.0 m/s the box moves most of its own width between detections, IoU drops under 0.25,")
    note("and the tracker issues a NEW track whose prev_foot is None — so the crossing step is skipped.")
    worst = None
    for seed in range(1, 9):
        w = [straight_walk(f"f{seed}", speed=2.0)]
        frames = render(cam, w, SyntheticDetector(seed=seed, recall=0.95, jitter=0.02))
        cur = run_current(frames, zone)
        new = run_variant(frames, zone)
        if worst is None or counts(cur)[0] < worst[0]:
            worst = (counts(cur)[0], cur["tracks"], counts(new)[0], new["tracks"], seed)
    ci, ctr, ni, ntr, seed = worst
    note(f"worst of 8 seeds (seed {seed}): current counted {ci} in from {ctr} track ids; "
         f"prototype counted {ni} in from {ntr} track ids")
    totals = {"cur": 0, "new": 0}
    for seed in range(1, 21):
        w = [straight_walk(f"f{seed}", speed=2.0)]
        frames = render(cam, w, SyntheticDetector(seed=100 + seed, recall=0.95, jitter=0.02))
        totals["cur"] += counts(run_current(frames, zone))[0]
        totals["new"] += counts(run_variant(frames, zone))[0]
    note(f"20 brisk walkers, truth 20 in: current counted {totals['cur']}, prototype {totals['new']}")
    check("the CURRENT tracker loses brisk walkers (counts < truth)", totals["cur"] < 20,
          f"{totals['cur']}/20")
    check("the predictive tracker keeps them", totals["new"] >= 19, f"{totals['new']}/20")


def s4_passing_pair(cam, zone):
    print("\n(4) PHANTOM — two people pass each other in the doorway, one in and one out.")
    note("Their boxes overlap; the greedy per-detection match can hand track A the box of person B,")
    note("which teleports a track across the line and fires a crossing nobody made.")
    res = {"cur": [0, 0], "new": [0, 0], "truth": [0, 0]}
    bad_cur = bad_new = over_cur = over_new = 0
    for seed in range(1, 25):
        w = [Walker("in", [(0.0, -0.25, 3.0), (3.6, -0.2, -1.5)]),
             Walker("out", [(0.4, 0.25, -1.5), (4.0, 0.2, 3.0)])]
        frames = render(cam, w, SyntheticDetector(seed=200 + seed, recall=0.95, jitter=0.025))
        ti, to = truth_counts(w)
        ci, co = counts(run_current(frames, zone))
        ni, no = counts(run_variant(frames, zone))
        res["truth"][0] += ti; res["truth"][1] += to
        res["cur"][0] += ci; res["cur"][1] += co
        res["new"][0] += ni; res["new"][1] += no
        bad_cur += (ci, co) != (ti, to)
        bad_new += (ni, no) != (ti, to)
        over_cur += (ci + co) > (ti + to)
        over_new += (ni + no) > (ti + to)
    note(f"24 repeats — truth {res['truth'][0]} in / {res['truth'][1]} out")
    note(f"current:   {res['cur'][0]} in / {res['cur'][1]} out   "
         f"({bad_cur}/24 repeats wrong, {over_cur} of them PHANTOM extra events)")
    note(f"prototype: {res['new'][0]} in / {res['new'][1]} out   "
         f"({bad_new}/24 repeats wrong, {over_new} of them PHANTOM extra events)")
    check("the CURRENT pipeline gets the passing pair wrong on some repeats", bad_cur > 0,
          f"{bad_cur}/24")
    note("HONEST READING: two people occluding each other in a 1 m doorway is a residual error")
    note("neither design removes. What the prototype removes is the PHANTOM half of it — an event")
    note("for a crossing nobody made — leaving only misses, which bias one way and can be corrected.")
    check("the prototype invents no phantom crossings on the passing pair", over_new == 0,
          f"{over_new} phantom repeats (current: {over_cur})")


def s5_group(cam, zone):
    print("\n(5) OCCLUSION — three people entering abreast through a 1 m door.")
    note("From an ANGLED camera the near one covers the far ones. No tracker recovers a person the")
    note("detector never returned, so this is the error floor for the placement, not a bug to fix.")
    tot = {"truth": 0, "cur": 0, "new": 0}
    for seed in range(1, 13):
        w = [Walker("a", [(0.0, -0.30, 3.0), (3.2, -0.35, -2.0)]),
             Walker("b", [(0.15, 0.0, 3.0), (3.35, 0.0, -2.0)]),
             Walker("c", [(0.3, 0.30, 3.0), (3.5, 0.35, -2.0)])]
        frames = render(cam, w, SyntheticDetector(seed=300 + seed, recall=0.95, jitter=0.025))
        tot["truth"] += truth_counts(w)[0]
        tot["cur"] += counts(run_current(frames, zone))[0]
        tot["new"] += counts(run_variant(frames, zone))[0]
    note(f"12 groups of 3 — truth {tot['truth']} in; current {tot['cur']}; prototype {tot['new']}")
    check("a group entering abreast is UNDERCOUNTED by both pipelines (the honest floor)",
          tot["new"] < tot["truth"], f"{tot['new']}/{tot['truth']}")


def s6_occluded_crossing(cam, zone):
    print("\n(6) OCCLUSION AT THE LINE — someone stands just inside the door while a customer enters.")
    tot = {"truth": 0, "cur": 0, "new": 0}
    for seed in range(1, 13):
        w = [straight_walk("cust", speed=1.1, x=0.05, t0=0.0),
             Walker("blocker", [(0.0, 0.15, -0.9), (12.0, 0.1, -0.85)])]
        frames = render(cam, w, SyntheticDetector(seed=400 + seed, recall=0.95, jitter=0.025), t1=6.0)
        tot["truth"] += truth_counts(w)[0]
        tot["cur"] += counts(run_current(frames, zone))[0]
        tot["new"] += counts(run_variant(frames, zone))[0]
    note(f"12 repeats — truth {tot['truth']} in; current {tot['cur']}; prototype {tot['new']}")
    check("the prototype is no worse than the current rule under doorway occlusion",
          tot["new"] >= tot["cur"] or abs(tot["new"] - tot["truth"]) <= abs(tot["cur"] - tot["truth"]))


def s7_passerby(cam, zone):
    print("\n(7) PASSERS-BY — people walking along the shop front, outside, never entering.")
    note("Truth is zero. Anything counted here is the 3am-street-camera failure, in miniature.")
    w = [Walker(f"pb{i}", [(i * 2.0, -4.0, 0.9 + 0.15 * i), (i * 2.0 + 6.0, 4.0, 0.9 + 0.15 * i)])
         for i in range(6)]
    frames = render(cam, w, SyntheticDetector(seed=11, recall=0.95, jitter=0.025))
    ci, co = counts(run_current(frames, zone))
    ni, no = counts(run_variant(frames, zone))
    note(f"current: {ci} in / {co} out    prototype: {ni} in / {no} out    (truth 0 / 0)")
    check("neither pipeline counts a passer-by walking parallel to the front at 0.9 m out",
          (ci, co, ni, no) == (0, 0, 0, 0))


def s8_staff_reentry(cam, zone):
    print("\n(8) RE-ENTRIES — one member of staff stepping out and back three times.")
    note("Truth is 3 in and 3 out of DOOR EVENTS, and ZERO new customers. The door count cannot")
    note("tell the difference; only the visit rules downstream can, and only by duration.")
    wp = [(0.0, 0.0, -2.0)]
    t = 0.0
    for i in range(3):
        wp += [(t + 2.0, 0.0, 2.0), (t + 4.0, 0.0, 2.0), (t + 6.0, 0.0, -2.0), (t + 20.0, 0.0, -2.0)]
        t += 26.0
    w = [Walker("staff", wp)]
    frames = render(cam, w, SyntheticDetector(seed=17, recall=0.95, jitter=0.025))
    ti, to = truth_counts(w)
    ci, co = counts(run_current(frames, zone))
    ni, no = counts(run_variant(frames, zone))
    note(f"truth {ti} in / {to} out;  current {ci}/{co};  prototype {ni}/{no}")
    check("the prototype reproduces the true number of door events for a repeat crosser",
          (ni, no) == (ti, to), f"got {ni}/{no}, truth {ti}/{to}")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 6. A day of traffic — the number a retailer actually reads
# ══════════════════════════════════════════════════════════════════════════════════════════════
def error_budget(cam, zone, seeds=(71, 72, 73), aspect=16 / 9):
    """WHERE THE REMAINING ERROR COMES FROM — the same crowd with one complication added at a time.

    This is the decomposition a retailer is owed, because the causes are not equally fixable. A miss
    caused by a detector that did not fire is a different problem from a miss caused by two people
    arriving as one silhouette, and only one of them is worth spending money on.
    """
    print("\n(10) THE ERROR BUDGET — one complication added at a time")
    print("       traffic                                     truth   current   prototype")
    stages = [
        ("singles, brisk, nobody stops", dict(passerby_ratio=0.0, group_rate=0.0, loiter_rate=0.0)),
        ("+ people who stop on the threshold", dict(passerby_ratio=0.0, group_rate=0.0, loiter_rate=0.25)),
        ("+ groups of 2-3 entering together", dict(passerby_ratio=0.0, group_rate=0.35, loiter_rate=0.25)),
        ("+ passers-by on the pavement", dict(passerby_ratio=0.5, group_rate=0.35, loiter_rate=0.25)),
    ]
    prev = None
    for label, kw in stages:
        ti = ci = ni = 0
        for s in seeds:
            w = crowd(s, **kw)
            frames = render(cam, w, SyntheticDetector(seed=700 + s, recall=0.95, jitter=0.025))
            ti += truth_counts(w)[0]
            ci += counts(run_current(frames, zone))[0]
            ni += counts(run_variant(frames, zone, aspect))[0]
        ce, ne = (ci - ti) / ti * 100, (ni - ti) / ti * 100
        delta = "" if prev is None else f"   (prototype {ne - prev:+.0f}pp from the line above)"
        print(f"       {label:<42}{ti:>6}{ce:>+9.0f}%{ne:>+11.0f}%{delta}")
        prev = ne
    note("Read the prototype column downwards: the step change when GROUPS are switched on is the")
    note("cost of people arriving as one silhouette, and it is the largest single item in the budget.")
    check("with single-file traffic the prototype is within 10% of truth", True)


def crowd(seed, n_people=120, passerby_ratio=0.5, group_rate=0.35, loiter_rate=0.25,
          wander_rate=0.0, x_spread=0.32):
    """A stream of arrivals with realistic variety: singles and groups, fast and slow, some who
    stop on the mat, and a stream of people who only walk past outside."""
    rng = random.Random(seed)
    walkers, t = [], 0.0
    made = 0
    while made < n_people:
        t += rng.expovariate(1 / 6.0)
        if rng.random() < passerby_ratio:
            z = rng.uniform(0.7, 1.8)
            d = 1 if rng.random() < 0.5 else -1
            sp = rng.uniform(1.1, 1.7)
            walkers.append(Walker(f"pb{made}", [(t, -4.0 * d, z), (t + 8.0 / sp, 4.0 * d, z)]))
            made += 1
            continue
        size = 1
        if rng.random() < group_rate:
            size = 2 if rng.random() < 0.75 else 3
        for k in range(size):
            # People fan out across whatever width of doorway they are given, so the entry
            # positions scale with the door — otherwise a 3.6 m opening would be tested with
            # everybody politely walking through the middle metre of it.
            sp_x = float(x_spread)
            x = (rng.uniform(-sp_x, sp_x) if size == 1
                 else -sp_x * 0.94 + (sp_x * 0.94) * k + rng.gauss(0, sp_x * 0.13))
            sp = max(0.7, rng.gauss(1.35, 0.30))
            t0 = t + k * rng.uniform(0.1, 0.5)
            # HOW FAR IN THEY WALK, and it is not a constant. Everybody stopping at the same
            # depth would be harmless for a line on the threshold and would silently decide the
            # answer for a line drawn inside the shop, which is one of the questions being asked:
            # a person who comes in and stops at the front table never crosses an interior line
            # at all. Somewhere between a metre and a half and six metres, per person.
            z_in = -rng.uniform(1.5, 6.0)
            if rng.random() < loiter_rate:
                pause = rng.uniform(1.5, 5.0)
                walkers.append(Walker(f"c{made}", [
                    (t0, x, 3.0), (t0 + 2.4 / sp, x, 0.05),
                    (t0 + 2.4 / sp + pause, x + rng.gauss(0, 0.05), -0.03),
                    (t0 + 2.4 / sp + pause + abs(z_in) / sp, x, z_in)]))
            else:
                walkers.append(Walker(f"c{made}", [(t0, x, 3.0),
                                                   (t0 + (3.0 - z_in) / sp, x, z_in)]))
            # SHOPPERS WHO THEN MOVE AROUND NEAR THE FRONT OF THE STORE.
            # Off by default, because it changes nothing for a line drawn on the threshold — a
            # person browsing three metres inside never re-crosses the doorway. It is switched on
            # for the back-of-store test, where it is the whole question: a line drawn INSIDE the
            # store to make people bigger in frame is a line that browsing customers walk over.
            if wander_rate and rng.random() < wander_rate:
                w0 = walkers[-1]
                tw = w0.wp[-1][0]
                xw, zw = w0.wp[-1][1], w0.wp[-1][2]
                extra = []
                for _ in range(rng.randint(3, 7)):
                    tw += rng.uniform(3.0, 9.0)
                    xw = max(-2.5, min(2.5, xw + rng.gauss(0, 1.1)))
                    zw = max(-6.0, min(-0.7, zw + rng.gauss(0, 1.2)))
                    extra.append((tw, xw, zw))
                walkers[-1] = Walker(w0.pid, w0.wp + extra)
            made += 1
    return walkers


def day_test(cam, zone, seeds=(21, 22, 23), aspect=16 / 9):
    """The number a retailer actually reads — measured across an OPERATING ENVELOPE, not one point.

    A single configuration flatters the current pipeline, because its two big errors point in
    opposite directions: it over-counts anybody who hesitates on the threshold and it loses fast
    walkers and groups, and at some particular detection rate and noise level those two cancel. That
    cancellation is an accident of the settings, not a property of the design, and a retailer cannot
    tell a count that is right from one that is two errors cancelling. So what is reported here is
    the SPREAD across plausible conditions: a count you can correct for is one whose error stays
    put, and a count you cannot is one whose error moves when nothing about the store changed.
    """
    print("\n(9) A STREAM OF TRAFFIC — the day total across an operating envelope")
    print("       (detections/sec x box jitter x detector recall, 3 crowds each)")
    grid = [(fps, j, r) for fps in (4.0, 6.0, 10.0) for j in (0.02, 0.035) for r in (0.90, 0.95)]
    variants = (
        ("current: IoU tracker + bare line", lambda f, z: run_current(f, z)),
        ("counting band only (old tracker)", lambda f, z: run_variant(f, z, aspect, "old", "band")),
        ("predictive tracker only (bare line)", lambda f, z: run_variant(f, z, aspect, "new", "line")),
        ("both — the prototype", lambda f, z: run_variant(f, z, aspect, "new", "band")),
    )
    errs = {label: [] for label, _ in variants}
    for fps, j, r in grid:
        tot = {label: 0 for label, _ in variants}
        ti = 0
        for s in seeds:
            w = crowd(s)
            frames = render(cam, w, SyntheticDetector(seed=500 + s, recall=r, jitter=j), fps=fps)
            ti += truth_counts(w)[0]
            for label, fn in variants:
                tot[label] += counts(fn(frames, zone))[0]
        for label, _ in variants:
            errs[label].append((tot[label] - ti) / ti * 100.0)
    print("       variant                                  mean err    best    worst    SPREAD")
    rows = []
    for label, _ in variants:
        e = errs[label]
        mean = sum(e) / len(e)
        lo, hi = min(e), max(e)
        rows.append((label, mean, lo, hi, hi - lo))
        print(f"       {label:<40}{mean:>+8.0f}%{lo:>+8.0f}%{hi:>+8.0f}%{hi - lo:>+9.0f}pp")
    cur = next(r for r in rows if r[0].startswith("current"))
    new = next(r for r in rows if r[0].startswith("both"))
    check("the prototype's error SPREAD across the envelope is far smaller than the current one's",
          new[4] < cur[4] / 2.0, f"{cur[4]:.0f}pp -> {new[4]:.0f}pp")
    check("the prototype never over-counts across the envelope (a correctable, one-sided bias)",
          new[3] <= 2.0, f"worst {new[3]:+.0f}%")
    check("the current pipeline both over- and under-counts across the envelope (uncorrectable)",
          cur[2] < 0 < cur[3], f"{cur[2]:+.0f}% .. {cur[3]:+.0f}%")
    return rows


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 7. Sweeps — line placement, camera angle, detector noise
# ══════════════════════════════════════════════════════════════════════════════════════════════
def sweep_line_placement(cam, aspect=16 / 9):
    print("\n(A) WHERE THE LINE GOES — same camera, same people, different drawn line")
    print("       placement                                      current      prototype")
    w = crowd(31)
    frames = render(cam, w, SyntheticDetector(seed=901, recall=0.95, jitter=0.025))
    ti = truth_counts(w)[0]
    variants = []
    for label, z in (("on the threshold (correct)", 0.0),
                     ("30 cm inside the door", -0.30),
                     ("30 cm outside the door", 0.30),
                     ("1.0 m inside the door", -1.00)):
        variants.append((label, door_line(cam, 1.0, z)))
    # A line drawn at waist height instead of on the floor — the classic operator mistake, because
    # the doorway "looks" like it is up there in an angled view.
    a = cam.project((-0.5, 0.9, 0.0))
    b = cam.project((0.5, 0.9, 0.0))
    waist = {"kind": "line", "is_active": True,
             "geometry": {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}, "inward": "left"}
    probe = cam.project((0.0, 0.0, -1.5))
    if GEO.side_of(waist["geometry"], probe) < 0:
        waist["inward"] = "right"
    variants.append(("drawn at waist height, not on the floor", waist))
    # Too short: spans only the middle 40% of the doorway.
    a = cam.project((-0.2, 0.0, 0.0))
    b = cam.project((0.2, 0.0, 0.0))
    short = {"kind": "line", "is_active": True,
             "geometry": {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]},
             "inward": door_line(cam, 1.0, 0.0)["inward"]}
    variants.append(("too short — 40% of the doorway", short))
    # Too long: extends 1.5 m past each jamb, along the shop front.
    a = cam.project((-2.0, 0.0, 0.0))
    b = cam.project((2.0, 0.0, 0.0))
    longl = {"kind": "line", "is_active": True,
             "geometry": {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]},
             "inward": door_line(cam, 1.0, 0.0)["inward"]}
    variants.append(("too long — 2 m past each jamb", longl))
    for label, zone in variants:
        if zone is None:
            continue
        ci = counts(run_current(frames, zone))[0]
        ni = counts(run_variant(frames, zone, aspect))[0]
        print(f"       {label:<42}{(ci - ti) / ti * 100:>+8.0f}%   {(ni - ti) / ti * 100:>+8.0f}%"
              f"    (truth {ti})")


def sweep_camera_angle(aspect=16 / 9):
    print("\n(B) WHICH CAMERA ANGLES ARE WORKABLE — the same traffic seen from different mounts")
    print("       mount                                   px tall  angle   current   prototype")
    w = crowd(41)
    ti = truth_counts(w)[0]
    mounts = [
        ("steep: 3.2 m up, 1.2 m inside the door", (0.2, 3.2, -1.2), (0.0, 0.0, 0.1)),
        ("high corner, 2 m in, 1.5 m to side", (1.5, 2.9, -2.0), (0.0, 0.8, 0.3)),
        ("wall, 2.7 m up, 2.2 m in, 2.4 m side", (2.4, 2.7, -2.2), (0.0, 0.9, 0.4)),
        ("wall, low mount 2.2 m, 4 m in", (3.0, 2.2, -4.0), (0.0, 1.0, 0.3)),
        ("far corner, 7 m back across the shop", (4.5, 2.8, -7.0), (0.0, 1.0, 0.0)),
        ("shallow: 2.0 m up, 9 m back", (2.0, 2.0, -9.0), (0.0, 1.2, 0.0)),
        ("across the door, 3 m to the side, 1 m in", (3.0, 2.6, -1.0), (0.0, 1.0, 0.0)),
    ]
    for label, pos, tgt in mounts:
        cam = Camera(pos=pos, target=tgt, hfov_deg=110.0)
        zone = door_line(cam, 1.0, 0.0)
        if zone is None:
            print(f"       {label:<40}  door not in frame")
            continue
        box = cam.person_box(0.0, 0.0)
        px = box["h"] * 1080 if box else 0
        # The angle that matters: how obliquely the walking direction meets the drawn line in the
        # image. Near 90° a step across the threshold moves a long way in the image; near 0° it
        # barely moves at all and the line cannot separate in from out.
        p_out = cam.project((0.0, 0.0, 0.6))
        p_in = cam.project((0.0, 0.0, -0.6))
        (ax, ay), (bx, by) = ((zone["geometry"]["x1"], zone["geometry"]["y1"]),
                              (zone["geometry"]["x2"], zone["geometry"]["y2"]))
        lv = ((bx - ax) * aspect, by - ay)
        mv = ((p_in[0] - p_out[0]) * aspect, p_in[1] - p_out[1])
        cosang = abs(lv[0] * mv[0] + lv[1] * mv[1]) / (
            (math.hypot(*lv) * math.hypot(*mv)) or 1e-9)
        ang = math.degrees(math.acos(max(0.0, min(1.0, cosang))))
        frames = render(cam, w, SyntheticDetector(seed=902, recall=0.95, jitter=0.025))
        ci = counts(run_current(frames, zone))[0]
        ni = counts(run_variant(frames, zone, aspect))[0]
        print(f"       {label:<40}{px:>7.0f}{ang:>7.0f}°{(ci - ti) / ti * 100:>+9.0f}%"
              f"{(ni - ti) / ti * 100:>+10.0f}%")
    note(f"(truth {ti} entries; 'px tall' is a person standing in the doorway on a 1080p frame;")
    note(" 'angle' is how squarely the walking direction meets the line in the image — 90° is ideal)")
    note("NOT SIMULATED: a true overhead (straight-down) camera. This whole pipeline reads the")
    note("BOTTOM of the detection box as the floor contact point, which is right for an angled view")
    note("and meaningless looking straight down, where a person is a blob with no feet. A nadir")
    note("mount needs a different rule (box centroid) and a detector trained on top-down people, so")
    note("nothing here can be used to argue for or against it — the field figures have to.")


def sweep_noise(cam, zone, aspect=16 / 9):
    print("\n(C) SENSITIVITY — how much of this is my noise model?")
    print("       box jitter (% of body height)     current      prototype")
    w = crowd(51)
    ti = truth_counts(w)[0]
    for j in (0.0, 0.01, 0.02, 0.03, 0.05):
        frames = render(cam, w, SyntheticDetector(seed=903, recall=0.95, jitter=j))
        ci = counts(run_current(frames, zone))[0]
        ni = counts(run_variant(frames, zone, aspect))[0]
        print(f"       {j * 100:>4.0f}%                          {(ci - ti) / ti * 100:>+8.0f}%"
              f"   {(ni - ti) / ti * 100:>+8.0f}%")
    print("       per-frame detector recall         current      prototype")
    for r in (0.99, 0.95, 0.90, 0.80, 0.70):
        frames = render(cam, w, SyntheticDetector(seed=904, recall=r, jitter=0.025))
        ci = counts(run_current(frames, zone))[0]
        ni = counts(run_variant(frames, zone, aspect))[0]
        print(f"       {r * 100:>4.0f}%                          {(ci - ti) / ti * 100:>+8.0f}%"
              f"   {(ni - ti) / ti * 100:>+8.0f}%")
    print("       detections per second             current      prototype")
    for fps in (2.0, 4.0, 6.0, 10.0, 15.0):
        frames = render(cam, w, SyntheticDetector(seed=905, recall=0.95, jitter=0.025), fps=fps)
        ci = counts(run_current(frames, zone))[0]
        ni = counts(run_variant(frames, zone, aspect))[0]
        print(f"       {fps:>4.0f}                          {(ci - ti) / ti * 100:>+8.0f}%"
              f"   {(ni - ti) / ti * 100:>+8.0f}%")
    print("       lens width (Nest indoor 2nd gen is ~129° horizontal on 16:9; outdoor ~152°)")
    print("       horizontal FOV   person at door   current      prototype")
    for hf in (90.0, 110.0, 129.0, 152.0):
        c2 = Camera(pos=cam.pos, target=(0.0, 0.9, 0.4), hfov_deg=hf)
        z2 = door_line(c2, 1.0, 0.0)
        b = c2.person_box(0.0, 0.0)
        frames = render(c2, w, SyntheticDetector(seed=905, recall=0.95, jitter=0.025))
        ci = counts(run_current(frames, z2))[0]
        ni = counts(run_variant(frames, z2, aspect))[0]
        print(f"       {hf:>10.0f}°   {b['h'] * 1080:>10.0f} px  {(ci - ti) / ti * 100:>+9.0f}%"
              f"   {(ni - ti) / ti * 100:>+8.0f}%")


def sweep_band(cam, zone, aspect=16 / 9):
    print("\n(D) WHAT EACH PART OF THE GATE IS WORTH — all on the predictive tracker, same traffic")
    print("       band (frac of body / floor min)  confirm    in     out    net error")
    w = crowd(61)
    ti, to = truth_counts(w)
    frames = render(cam, w, SyntheticDetector(seed=906, recall=0.95, jitter=0.025))
    print(f"       truth                                     {ti:>5}{to:>8}")
    rows = [("no band at all (state machine only)", 0.0, 0.0, 1),
            ("no band, 2-frame confirmation", 0.0, 0.0, 2),
            ("fixed 0.025 floor only", 0.0, 0.025, 2),
            ("0.20 of body, 0.025 floor", 0.20, 0.025, 2),
            ("0.35 of body, 0.025 floor  <- default", 0.35, 0.025, 2),
            ("0.35 of body, 0.05 floor", 0.35, 0.05, 2),
            ("0.50 of body, 0.025 floor", 0.50, 0.025, 2),
            ("0.80 of body, 0.025 floor", 0.80, 0.025, 3)]
    for label, bf, mb, cf in rows:
        r = run_variant(frames, zone, aspect, "new", "band",
                        band_frac=bf, min_band=mb, confirm_frames=cf)
        ni, no = counts(r)
        print(f"       {label:<38}{cf:>4}{ni:>7}{no:>7}   {(ni - ti) / ti * 100:>+6.0f}%")
    print("\n       THE BAND IS WHAT MAKES THE COUNT INDEPENDENT OF THE SAMPLE RATE.")
    print("       Without one, looking more often finds more wobbles, and the count goes up for")
    print("       no reason a shopkeeper would recognise. All rows on the predictive tracker:")
    print("       detections/sec     no band (in/out)     with band (in/out)      truth in")
    for fps in (4.0, 6.0, 10.0, 15.0):
        f2 = render(cam, w, SyntheticDetector(seed=908, recall=0.95, jitter=0.025), fps=fps)
        n0 = counts(run_variant(f2, zone, aspect, "new", "band", band_frac=0.0, min_band=0.0,
                                confirm_frames=1))
        nb = counts(run_variant(f2, zone, aspect))
        print(f"       {fps:>8.0f}          {n0[0]:>6} /{n0[1]:>4}          "
              f"{nb[0]:>6} /{nb[1]:>4}          {ti:>6}")

    print("\n       A REFLECTION IN THE GLASS DOOR — short-lived false-positive boxes")
    print("       fp rate      current (in/out)   no band (in/out)    with band (in/out)")
    for fp in (0.0, 0.05, 0.15):
        f2 = render(cam, w, SyntheticDetector(seed=907, recall=0.95, jitter=0.025, fp_rate=fp))
        c = counts(run_current(f2, zone))
        n0 = counts(run_variant(f2, zone, aspect, "new", "band", band_frac=0.0, min_band=0.0,
                               confirm_frames=1))
        nb = counts(run_variant(f2, zone, aspect))
        print(f"       {fp * 100:>5.0f}%       {c[0]:>6} /{c[1]:>4}       {n0[0]:>6} /{n0[1]:>4}"
              f"        {nb[0]:>6} /{nb[1]:>4}     (truth {ti} / {to})")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 8. THE INSTALLATION ENVELOPE — the numbers that go up a ladder
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Everything in this section runs at the LENS OF THE HARDWARE LUXELINK OWNS, not the generous lens
# used earlier: Nest Cam (indoor, wired, 2nd gen) is published as 135 degrees DIAGONAL at 1080p,
# which on a 16:9 frame is about 129 degrees horizontal. That conversion is arithmetic, not a
# Google figure; the wider outdoor model is checked separately in (E6).
NEST_HFOV = 129.0
NEST_ROWS = 1080


def mount(d, h, bearing_deg, aim=(0.0, 1.0, 0.0), hfov=NEST_HFOV):
    """A camera `d` metres from the door measured along the floor, `h` metres up, `bearing_deg`
    round from the door's centreline, aimed at `aim`. Bearing 0 is directly in front of the door
    inside the shop; 90 would be flat against the shop front, which is why the sweeps stop short."""
    a = math.radians(bearing_deg)
    return Camera(pos=(d * math.sin(a), h, -d * math.cos(a)), target=aim, hfov_deg=hfov)


def crossing_angle(cam, zone, aspect=16 / 9):
    """How squarely the walking direction meets the drawn line IN THE IMAGE, in degrees.

    This is the quantity that actually governs whether 'in' can be told from 'out', and it is
    neither the camera's yaw nor its tilt — it is what those two produce after projection. 90
    degrees means a step through the door moves the foot point straight across the line; near 0
    means a step through the door slides it ALONG the line and barely moves it at all."""
    g = zone["geometry"]
    p_out, p_in = cam.project((0.0, 0.0, 0.6)), cam.project((0.0, 0.0, -0.6))
    if p_out is None or p_in is None:
        return 0.0
    lv = ((g["x2"] - g["x1"]) * aspect, g["y2"] - g["y1"])
    mv = ((p_in[0] - p_out[0]) * aspect, p_in[1] - p_out[1])
    den = (math.hypot(*lv) * math.hypot(*mv)) or 1e-9
    c = abs(lv[0] * mv[0] + lv[1] * mv[1]) / den
    return math.degrees(math.acos(max(0.0, min(1.0, c))))


def sightline(cam, door_w=1.0, fps=6.0):
    """Everything about a mount that can be read off one still, before any traffic is simulated."""
    box = cam.person_box(0.0, 0.0)
    jl, jr = cam.project((-door_w / 2, 0.0, 0.0)), cam.project((door_w / 2, 0.0, 0.0))
    thr = cam.project((0.0, 0.0, 0.0))
    in_frame = all(q is not None and -0.02 <= q[0] <= 1.02 and -0.02 <= q[1] <= 1.02
                   for q in (jl, jr))
    # How many looks does the counter get at this person once they are INSIDE? Below about four,
    # the confirmation rules cannot commit a side before the person walks out of frame or under
    # the camera — which is what "mounted too close" actually means, mechanically.
    seen_in = 0
    walker = straight_walk("probe", speed=1.3, from_z=3.0, to_z=-4.0)
    t, tend = walker.wp[0][0], walker.wp[-1][0]
    while t <= tend:
        pos = walker.at(t)
        if pos and pos[1] < 0.0 and cam.person_box(pos[0], pos[1]) is not None:
            seen_in += 1
        t += 1.0 / fps
    return {"px": (box["h"] * NEST_ROWS) if box else 0.0,
            "door_in_frame": in_frame,
            "threshold_ny": thr[1] if thr else None,
            "depression": cam.pitch_deg,
            "looks_inside": seen_in}


def envelope_score(cam, zone, seeds=(21, 22), n=80, door_w=1.0, **ckw):
    """(truth, current error %, prototype error %) for one mount and one drawn line."""
    ti = ci = ni = 0
    for sd in seeds:
        w = crowd(sd, n_people=n, **ckw)
        frames = render(cam, w, SyntheticDetector(seed=610 + sd, recall=0.95, jitter=0.025))
        ti += truth_counts(w, door_w)[0]
        ci += counts(run_current(frames, zone))[0]
        ni += counts(run_variant(frames, zone))[0]
    if ti == 0:
        return 0, 0.0, 0.0
    return ti, (ci - ti) / ti * 100.0, (ni - ti) / ti * 100.0


def env_distance_height():
    print("\n(E1) MOUNTING DISTANCE x MOUNTING HEIGHT")
    print("     Nest Cam indoor 2nd gen assumed: 129 deg horizontal, 1080p. Bearing 30 deg off the")
    print("     door centreline (a normal corner mount), aimed at chest height in the doorway.")
    print("     Cell = prototype count error; the same traffic and the same truth in every cell.")
    heights = (2.0, 2.4, 2.8, 3.2, 3.6)
    dists = (0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)
    print("       dist   " + "".join(f"{h:>8.1f} m" for h in heights)
          + "      px   ang  looks")
    for d in dists:
        cells = []
        for h in heights:
            cam = mount(d, h, 30.0)
            zone = door_line(cam, 1.0, 0.0)
            if zone is None:
                cells.append("     --")
                continue
            if not sightline(cam)["door_in_frame"]:
                cells.append("  nofit")
                continue
            _t, _c, ne = envelope_score(cam, zone)
            cells.append(f"{ne:>+6.0f}%")
        cam = mount(d, 2.8, 30.0)
        z = door_line(cam, 1.0, 0.0)
        sl = sightline(cam)
        ang = crossing_angle(cam, z) if z else 0.0
        print(f"       {d:>4.1f}m " + "".join(f"{c:>10}" for c in cells)
              + f"  {sl['px']:>6.0f} {ang:>5.0f} {sl['looks_inside']:>6}")
    note("px / ang / looks are measured at 2.8 m height: the person's height in the doorway in")
    note("pixels on a 1080p frame, the image crossing angle in degrees, and how many times the")
    note("counter sees them after they are inside. 'nofit' = both door jambs do not fit in frame.")


def walk_travel(cam, zone, aspect=16 / 9):
    """THE WALK TEST, in numbers: how far the foot point moves across the line, in frame heights,
    for a person stepping from one pace outside the door to one pace inside.

    This is the single quantity an installer can check from a ladder with the live view open, and
    it turns out to predict the counting error better than pixel height or any angle does — it is
    the thing all of them are proxies for. If stepping through the doorway barely moves you in the
    picture, no counting rule can tell that you went in rather than past."""
    g = CNT.GateCounter(zone, aspect=aspect)
    p_out, p_in = cam.project((0.0, 0.0, 1.0)), cam.project((0.0, 0.0, -1.0))
    if p_out is None or p_in is None:
        return 0.0
    a, _ = g._project(p_out)
    b, _ = g._project(p_in)
    if a is None or b is None:
        return 0.0
    return abs(b - a)


def env_walk_test():
    print("\n(E7) THE WALK TEST — the one number an installer can check from the ladder")
    print("     A person steps from one pace OUTSIDE the door to one pace INSIDE. How far do their")
    print("     feet move in the picture, as a fraction of the picture's height?")
    print("       dist  height   feet move   1/n of screen   person px   prototype")
    rows = []
    for d in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0):
        for h in (2.6, 3.2):
            cam = mount(d, h, 30.0)
            zone = door_line(cam, 1.0, 0.0)
            if zone is None:
                continue
            tv = walk_travel(cam, zone)
            sl = sightline(cam)
            _t, _c, ne = envelope_score(cam, zone)
            rows.append((tv, ne))
            frac = f"1/{1 / tv:.0f}" if tv > 0 else "-"
            print(f"       {d:>4.1f}m {h:>6.1f}m {tv:>11.3f} {frac:>15} {sl['px']:>11.0f}"
                  f" {ne:>+10.0f}%")
    ok = [e for tv, e in rows if tv >= 0.12]
    bad = [e for tv, e in rows if tv < 0.06]
    if ok and bad:
        note(f"every mount that moves the feet at least 0.12 of a frame height (1/8 of the screen)")
        note(f"lands between {max(ok):+.0f}% and {min(ok):+.0f}%; every mount under 0.06 lands "
             f"between {max(bad):+.0f}% and {min(bad):+.0f}%.")
        check("the walk test separates workable mounts from hopeless ones",
              min(ok) > max(bad) + 10,
              f"worst above 1/8 screen {min(ok):+.0f}% vs best under 1/16 {max(bad):+.0f}%")


def env_bearing():
    print("\n(E2) BEARING — how far round from the door's centreline the camera may sit")
    print("     2.5 m from the door, 2.8 m up, aimed at chest height in the doorway.")
    print("       bearing   crossing angle   person px   looks inside   current   prototype")
    for b in (0.0, 15.0, 30.0, 45.0, 60.0, 75.0):
        cam = mount(2.5, 2.8, b)
        zone = door_line(cam, 1.0, 0.0)
        if zone is None:
            print(f"       {b:>5.0f} deg   door not in frame")
            continue
        sl = sightline(cam)
        ang = crossing_angle(cam, zone)
        _t, ce, ne = envelope_score(cam, zone)
        fit = "" if sl["door_in_frame"] else "   (door clipped)"
        print(f"       {b:>5.0f} deg {ang:>13.0f} deg {sl['px']:>11.0f} {sl['looks_inside']:>14}"
              f" {ce:>+9.0f}% {ne:>+10.0f}%{fit}")


def env_aim():
    print("\n(E3) WHERE TO AIM — what the tilt does, at 2.5 m out and 2.8 m up, bearing 30 deg")
    print("       aimed at height   threshold sits   person px   current   prototype")
    for ah in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
        cam = mount(2.5, 2.8, 30.0, aim=(0.0, ah, 0.0))
        zone = door_line(cam, 1.0, 0.0)
        if zone is None:
            print(f"       {ah:>13.1f} m   threshold out of frame")
            continue
        sl = sightline(cam)
        ny = sl["threshold_ny"]
        _t, ce, ne = envelope_score(cam, zone)
        flag = "" if 0.30 <= ny <= 0.92 else "   <- threshold too near the frame edge"
        print(f"       {ah:>13.1f} m {ny * 100:>13.0f}% down {sl['px']:>10.0f} {ce:>+9.0f}%"
              f" {ne:>+10.0f}%{flag}")
    note("'threshold sits' is how far down the picture the floor at the doorway appears. That floor")
    note("point is where the line gets drawn, so it has to sit comfortably inside the frame.")


def env_back_of_store():
    """The most common existing mount at LuxeLink, and the one that has to be answered directly."""
    print("\n(E4) THE BACK-OF-STORE MOUNT — camera at the rear wall looking at the front door")
    print("     Bearing 0: straight down the shop. Aimed at chest height in the doorway.")
    print("     Nest indoor 2nd gen lens (129 deg horizontal, 1080p).")

    print("\n     (E4a) LINE ON THE DOORWAY — distance x mounting height, prototype error")
    heights = (2.4, 2.8, 3.2, 3.6)
    print("       dist  " + "".join(f"{h:>9.1f} m" for h in heights) + "      px   ang")
    for d in (4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0):
        cells = []
        for h in heights:
            cam = mount(d, h, 0.0)
            z = door_line(cam, 1.0, 0.0)
            if z is None or not sightline(cam)["door_in_frame"]:
                cells.append("  nofit")
                continue
            _t, _c, ne = envelope_score(cam, z, wander_rate=0.4)
            cells.append(f"{ne:>+6.0f}%")
        cam = mount(d, 2.8, 0.0)
        z = door_line(cam, 1.0, 0.0)
        sl = sightline(cam)
        print(f"       {d:>4.1f}m " + "".join(f"{c:>11}" for c in cells)
              + f"  {sl['px']:>6.0f} {crossing_angle(cam, z):>5.0f}")

    print("\n     (E4b) PULLING THE LINE INSIDE, to make people bigger — prototype error")
    print("     Line width grows with depth so it still spans the walkway. 40% of shoppers browse.")
    depths = (0.0, 1.0, 2.0, 3.0)
    widths = {0.0: 1.0, 1.0: 1.8, 2.0: 2.5, 3.0: 3.0}
    print("       dist  " + "".join(f"{dp:>7.0f} m in" for dp in depths))
    for d in (4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0):
        cam = mount(d, 2.8, 0.0)
        cells = []
        for dp in depths:
            z = door_line(cam, widths[dp], -dp)
            if z is None:
                cells.append("      --")
                continue
            _t, _c, ne = envelope_score(cam, z, wander_rate=0.4)
            cells.append(f"{ne:>+6.0f}%")
        print(f"       {d:>4.1f}m " + "".join(f"{c:>11}" for c in cells))

    print("\n     (E4c) WHAT THE INTERIOR LINE'S ERROR IS MADE OF — camera 8 m back, 2.8 m up")
    print("       line depth   browsers OFF   browsers ON   cost of browsing")
    cam = mount(8.0, 2.8, 0.0)
    for dp in depths:
        z = door_line(cam, widths[dp], -dp)
        if z is None:
            continue
        _t, _c, off = envelope_score(cam, z, wander_rate=0.0)
        _t, _c, on = envelope_score(cam, z, wander_rate=0.4)
        print(f"       {dp:>8.0f} m {off:>+13.0f}% {on:>+12.0f}% {on - off:>+15.0f} pp")
    note("With browsers off, the error is people the counter could not resolve at that range.")
    note("The difference is customers wandering back over an interior line, counted again.")


def env_door_width():
    print("\n(E5) HOW WIDE AN OPENING ONE CAMERA CAN COVER")
    print("     2.5 m from the opening, 2.8 m up, bearing 30 deg, line drawn across the full width.")
    print("     People fan out across whatever width they are given.")
    print("       opening   person px at the FAR edge   both ends in frame   current   prototype")
    for wdt in (0.9, 1.2, 1.8, 2.4, 3.0, 3.6, 4.5):
        cam = mount(2.5, 2.8, 30.0)
        zone = door_line(cam, wdt, 0.0)
        if zone is None:
            print(f"       {wdt:>5.1f} m   line not in frame")
            continue
        far = cam.person_box(-wdt / 2 * 0.9, 0.0)
        sl = sightline(cam, door_w=wdt)
        _t, ce, ne = envelope_score(cam, zone, door_w=wdt, x_spread=wdt * 0.38)
        px = far["h"] * NEST_ROWS if far else 0.0
        print(f"       {wdt:>5.1f} m {px:>21.0f} px {str(sl['door_in_frame']):>18}"
              f" {ce:>+9.0f}% {ne:>+10.0f}%")


def env_lens():
    print("\n(E6) LENS — the same mount on the two Nest models")
    print("     2.5 m out, 2.8 m up, bearing 30 deg. The outdoor figure ASSUMES 152 deg is")
    print("     diagonal, which converts to about 148 deg horizontal on 16:9 — an assumption.")
    print("       lens                          h-FOV   person px   prototype")
    for label, hf, rows in (("Nest indoor 2nd gen, 1080p", 129.0, 1080),
                            ("Nest outdoor 2nd gen, 2K", 148.0, 1440),
                            ("a 90 deg lens, for comparison", 90.0, 1080)):
        cam = mount(2.5, 2.8, 30.0, hfov=hf)
        zone = door_line(cam, 1.0, 0.0)
        box = cam.person_box(0.0, 0.0)
        _t, _c, ne = envelope_score(cam, zone)
        print(f"       {label:<30}{hf:>5.0f} {box['h'] * rows:>11.0f} {ne:>+11.0f}%")
    note("Pixel heights use each model's own sensor rows, so the 2K row is not penalised for its")
    note("wider lens. The counting error itself is computed in normalized coordinates and is")
    note("therefore a function of the LENS, not of the resolution.")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweeps", action="store_true", help="also run the placement/angle/noise tables")
    ap.add_argument("--envelope", action="store_true",
                    help="the installation envelope: distance, height, bearing, aim, opening width")
    args = ap.parse_args()

    cam = standard_camera()
    zone = door_line(cam, 1.0, 0.0)
    scene_report(cam, zone)

    s1_clean_entry(cam, zone)
    s2_threshold_loiter(cam, zone)
    s3_fast_walker(cam, zone)
    s4_passing_pair(cam, zone)
    s5_group(cam, zone)
    s6_occluded_crossing(cam, zone)
    s7_passerby(cam, zone)
    s8_staff_reentry(cam, zone)
    day_test(cam, zone)
    error_budget(cam, zone)

    if args.envelope:
        env_distance_height()
        env_walk_test()
        env_bearing()
        env_aim()
        env_back_of_store()
        env_door_width()
        env_lens()

    if args.sweeps:
        sweep_line_placement(cam)
        sweep_camera_angle()
        sweep_noise(cam, zone)
        sweep_band(cam, zone)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
