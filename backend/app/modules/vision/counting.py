"""Door counting that survives a real doorway — a counting BAND with hysteresis, and a tracker that
predicts instead of hoping.

WHY THIS EXISTS ALONGSIDE geometry.py
─────────────────────────────────────
`geometry.crossing_direction()` is correct, and is not what is wrong. It answers one question
perfectly: did this two-point step cross that segment, and which way. The door count still comes out
wrong, because the two inputs it is handed are wrong in ways a line test cannot see:

  1. THE STEP IS NOISY. A detection box wobbles by a few per cent of its own size every frame even
     when the person is standing still. A person who stops ON the threshold — holding the door,
     saying goodbye, checking their phone — has a foot point that flips sides several times a second.
     Every flip is a real, correct line crossing, and the door count gains an in/out pair for each.
  2. THE STEP IS NOT ALWAYS THE SAME PERSON. A simple IoU tracker loses a fast walker between frames
     and re-issues them as a new track whose `prev_foot` is None, so the one step that mattered is
     skipped and the entry is never counted at all.

So this module fixes the inputs rather than the maths.

WHAT A BAND BUYS, IN ONE SENTENCE
─────────────────────────────────
A line has zero width, so noise of any size crosses it; a BAND has to be walked all the way through,
in one direction, before anything is counted — which is what "somebody came in" actually means.

Everything here is pure, dependency-free and in normalized coordinates, exactly like geometry.py, so
it can be proven offline (`backend/harness_vision_counting.py`) and imported unchanged by the edge
analyzer and by the server.

ASPECT RATIO
────────────
Normalized coordinates are anisotropic: 0.01 of x on a 1920x1080 frame is 19.2 px, 0.01 of y is 10.8.
A band measured in raw normalized units would therefore be nearly twice as wide against a vertical
line as against a horizontal one. Distances here are computed in ASPECT SPACE (x multiplied by the
frame's width/height), so a band of 0.05 means the same real distance whichever way the line runs.
"""

EPS = 1e-9
DEFAULT_ASPECT = 16.0 / 9.0


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The gate — a band with hysteresis, per track
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _pt(p):
    if isinstance(p, dict):
        return float(p.get("x") or 0.0), float(p.get("y") or 0.0)
    return float(p[0]), float(p[1])


class GateCounter:
    """Directional counting for ONE line zone, with a band, hysteresis and span checking.

    Feed it every observation of every track: `update(track_key, foot, box, now)`. It returns
    'in', 'out' or None. One person walking through the door returns exactly one 'in', however
    many frames they take, however much the box wobbles, and however long they stand on the mat.

    THE RULES, AND WHY EACH ONE IS THERE
    ────────────────────────────────────
    * BAND. The band is a corridor of half-width `band` either side of the drawn line. Inside it,
      nothing is decided. A crossing needs the track to have been committed OUTSIDE the band on one
      side and then to become committed OUTSIDE it on the other. Box jitter is a fraction of a body
      width and never spans the corridor, so it counts nothing.
    * THE BAND SCALES WITH THE PERSON. `band_frac` of the detection box's HEIGHT, clamped. On an
      angled camera someone at the far end of the frame is a third the size of someone at the near
      end, and their jitter is a third the size too; a fixed band is either useless up close or
      impassable far away. Tying it to the body means one setting works across the whole frame.
    * CONFIRMATION. A side has to hold for `confirm_frames` consecutive observations before it is
      committed. One outlier box — the classic half-body detection when a shoulder is occluded —
      cannot arm or fire the gate on its own.
    * SPAN. The crossing has to happen ALONG THE DRAWN SEGMENT, not past its end. The place to test
      that is where the track actually passed through the line — the observation pair whose signed
      distance changed sign — not where the gate happened to commit, which on an oblique camera can
      be a long way up the line's extension. `geometry.crossing_direction()` makes the same test for
      a single step; here it is remembered across the several steps a band takes to traverse. A
      track that reaches the far side without ever having passed through the segment (walked round
      the end of the line, or had its identity swapped with somebody already inside) counts nothing.
    * ARMING IS SILENT. The first committed side only arms the gate. A track that is first detected
      already inside the store has not been seen to enter and is not counted as having entered.
    * RE-ARM. After firing, the committed side is the new side, so the same track walking back out
      counts 'out' — and a person hovering has to traverse the whole corridor again to count again.
    """

    def __init__(self, zone, aspect=DEFAULT_ASPECT, band_frac=0.35, min_band=0.025,
                 max_band=0.20, confirm_frames=2, span_margin=0.15, ttl_seconds=30.0):
        self.zone = zone or {}
        self.aspect = float(aspect or DEFAULT_ASPECT)
        self.band_frac = float(band_frac)
        self.min_band = float(min_band)
        self.max_band = float(max_band)
        self.confirm_frames = max(1, int(confirm_frames))
        self.span_margin = float(span_margin)
        self.ttl = float(ttl_seconds)
        self._state = {}
        geom = (self.zone.get("geometry") or {})
        self._line = self._line_points(geom)
        inward = (self.zone.get("inward") or "left").strip().lower()
        self._inward_side = 1 if inward == "left" else -1

    # ── line helpers, in aspect space ──────────────────────────────────────────────────────
    def _line_points(self, geom):
        if not isinstance(geom, dict):
            return None
        if all(k in geom for k in ("x1", "y1", "x2", "y2")):
            a = (float(geom["x1"]), float(geom["y1"]))
            b = (float(geom["x2"]), float(geom["y2"]))
        else:
            pts = geom.get("points") or []
            if len(pts) < 2:
                return None
            a, b = _pt(pts[0]), _pt(pts[1])
        if abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) < EPS:
            return None
        return a, b

    def _project(self, point):
        """(signed_distance, t) in aspect space. signed_distance > 0 is LEFT of A->B, matching
        geometry.side_of(); t is the position along the segment, 0 at A and 1 at B."""
        if not self._line:
            return None, None
        (ax, ay), (bx, by) = self._line
        k = self.aspect
        ax, bx = ax * k, bx * k
        px, py = _pt(point)
        px *= k
        dx, dy = bx - ax, by - ay
        length = (dx * dx + dy * dy) ** 0.5
        if length < EPS:
            return None, None
        cross = dx * (py - ay) - dy * (px - ax)
        t = ((px - ax) * dx + (py - ay) * dy) / (length * length)
        return cross / length, t

    def _band_for(self, box):
        h = float((box or {}).get("h") or 0.0)
        return max(self.min_band, min(self.max_band, self.band_frac * h))

    # ── the state machine ──────────────────────────────────────────────────────────────────
    def update(self, track_key, foot, box=None, now=0.0):
        """One observation of one track. Returns 'in' | 'out' | None."""
        if not self._line:
            return None
        self._expire(now)
        sd, t = self._project(foot)
        if sd is None:
            return None
        st = self._state.setdefault(track_key, {"side": 0, "cand": 0, "n": 0, "t_cross": None,
                                                "prev_sd": None, "prev_t": None, "last": now})
        st["last"] = now

        # Where did this track pass THROUGH the line? Interpolate the point at which the signed
        # distance changed sign, between this observation and the previous one — so a gap in the
        # track (an occlusion mid-doorway) is bridged the same way a single step would be.
        psd, pt = st["prev_sd"], st["prev_t"]
        if psd is not None and ((psd > 0) != (sd > 0)) and abs(sd - psd) > EPS:
            f = psd / (psd - sd)
            st["t_cross"] = pt + f * (t - pt)
        st["prev_sd"], st["prev_t"] = sd, t

        band = self._band_for(box)
        side = 1 if sd > band else (-1 if sd < -band else 0)

        if side == 0:                       # inside the corridor: decide nothing, forget nothing
            st["cand"], st["n"] = 0, 0
            return None
        if side != st["cand"]:
            st["cand"], st["n"] = side, 1
        else:
            st["n"] += 1
        if st["n"] < self.confirm_frames:
            return None

        prev_side = st["side"]
        if prev_side == side:
            return None
        st["side"] = side
        if prev_side == 0:                  # first commitment only ARMS the gate
            st["t_cross"] = None
            return None
        tc = st["t_cross"]
        st["t_cross"] = None                # consumed: the next count needs its own crossing
        if tc is None or not ((-self.span_margin) <= tc <= (1.0 + self.span_margin)):
            return None                     # crossed the line's extension, not the doorway
        return "in" if side == self._inward_side else "out"

    def drop(self, track_key):
        self._state.pop(track_key, None)

    def _expire(self, now):
        if self.ttl <= 0:
            return
        for k in [k for k, v in self._state.items() if now - v.get("last", now) > self.ttl]:
            self._state.pop(k, None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The tracker — prediction, a distance fallback, global assignment, and coasting
# ══════════════════════════════════════════════════════════════════════════════════════════════
class PredictiveTracker:
    """Drop-in replacement for the analyzer's IoU tracker, same `update(detections, now) -> tracks`.

    FOUR CHANGES, EACH FIXING A MEASURED FAILURE
    ────────────────────────────────────────────
    1. PREDICT BEFORE MATCHING. Each track carries a velocity and its box is moved forward by
       velocity x elapsed before overlap is computed. Six detections a second and a brisk walker is
       a box that has moved most of its own width between frames, so raw IoU against the STALE box
       falls under the threshold and the walker is re-issued as a new person. Matching the PREDICTED
       box restores the overlap. This is the cheap half of what SORT does — no filter, no matrix.
    2. A DISTANCE FALLBACK. Overlap is a bad similarity when the box is small or the step is large:
       two boxes a body-width apart have zero IoU whether they are a metre apart or ten. So a pair
       that fails IoU can still match if the foot points are within a fraction of a body height,
       which degrades gracefully instead of falling off a cliff.
    3. GLOBAL BEST-FIRST ASSIGNMENT. The original loops over detections and lets each take the best
       track still free, so the answer depends on the order the detector happened to emit boxes —
       the first detection can take a track that fits the second one twice as well. Scoring every
       pair and assigning in descending order of score removes that, and removes with it the
       identity swap that fires two phantom crossings when two people pass in the doorway.
    4. COASTING. An unmatched track is kept (and keeps its `prev_foot`) for `max_age` seconds
       instead of being retired at the first miss, so an occlusion in the doorway does not restart
       the person. Coasted tracks are NOT returned — nothing is counted from a guess — they are only
       held so the identity survives to be reclaimed.

    Plus `min_hits`: a track must be seen this many times before `confirmed` is set, so a
    single-frame reflection in the glass door never becomes a track the counter will listen to. It
    is deliberately LOW (2), because the counting band behind it already refuses anything that has
    not been observed on both sides of a corridor — raising it to 3 was measured to cost real
    entries (a person first seen mid-doorway) without buying any extra rejection.
    """

    def __init__(self, iou_match=0.20, dist_gate=0.75, max_age=2.0, min_hits=2,
                 aspect=DEFAULT_ASPECT, vel_smooth=0.5, max_predict=0.75):
        self._tracks = {}
        self._next = 1
        self.iou_match = float(iou_match)
        self.dist_gate = float(dist_gate)
        self.max_age = float(max_age)
        self.min_hits = int(min_hits)
        self.aspect = float(aspect)
        self.vel_smooth = float(vel_smooth)
        self.max_predict = float(max_predict)

    @staticmethod
    def _iou(a, b):
        ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
        bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
        ix = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
        iy = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
        inter = ix * iy
        union = a["w"] * a["h"] + b["w"] * b["h"] - inter
        return inter / union if union > 0 else 0.0

    def _predicted(self, t, now):
        dt = min(self.max_predict, max(0.0, now - t.get("last_seen", now)))
        b = t["box"]
        vx, vy = t.get("vx", 0.0), t.get("vy", 0.0)
        return {"x": b["x"] + vx * dt, "y": b["y"] + vy * dt, "w": b["w"], "h": b["h"]}

    def _score(self, t, d, now):
        """Similarity in [0, 2]; None when the pair is not admissible at all."""
        p = self._predicted(t, now)
        iou = self._iou(p, d)
        if iou >= self.iou_match:
            return 1.0 + iou                      # overlapping pairs always beat distance-only ones
        pcx = (p["x"] + p["w"] / 2.0) * self.aspect
        pcy = p["y"] + p["h"]
        dcx = (d["x"] + d["w"] / 2.0) * self.aspect
        dcy = d["y"] + d["h"]
        dist = ((pcx - dcx) ** 2 + (pcy - dcy) ** 2) ** 0.5
        scale = max(0.05, max(p["h"], d["h"]))
        if dist > self.dist_gate * scale:
            return None
        return 1.0 - dist / (self.dist_gate * scale)

    def update(self, detections, now):
        for t in self._tracks.values():
            t["matched"] = False
        dets = list(detections or [])
        high = [d for d in dets if float(d.get("conf") or 0.0) >= 0.50]
        low = [d for d in dets if float(d.get("conf") or 0.0) < 0.50]

        # ByteTrack's one big idea, for free: associate the confident boxes first, then give the
        # leftover tracks a second chance against the boxes a confidence threshold would have
        # thrown away. A half-occluded person in a doorway is exactly a low-confidence box, and
        # discarding it is what breaks the track precisely where the counting happens.
        used = set()
        for pool in (high, low):
            self._assign(pool, used, now)

        for d in dets:
            if id(d) in used:
                continue
            key = f"tk{self._next}"
            self._next += 1
            self._tracks[key] = {"key": key, "first_seen": now, "prev_foot": None, "box": d,
                                 "conf": float(d.get("conf") or 0.0), "last_seen": now,
                                 "matched": True, "hits": 1, "vx": 0.0, "vy": 0.0,
                                 "confirmed": self.min_hits <= 1}
            used.add(id(d))

        for key in [k for k, t in self._tracks.items()
                    if now - t.get("last_seen", now) > self.max_age]:
            self._tracks.pop(key, None)
        return [t for t in self._tracks.values() if t.get("matched")]

    def _assign(self, pool, used, now):
        pairs = []
        for d in pool:
            if id(d) in used:
                continue
            for key, t in self._tracks.items():
                if t["matched"]:
                    continue
                s = self._score(t, d, now)
                if s is not None:
                    pairs.append((s, key, d))
        pairs.sort(key=lambda p: -p[0])
        taken_d = set()
        for _s, key, d in pairs:
            t = self._tracks.get(key)
            if t is None or t["matched"] or id(d) in taken_d:
                continue
            dt = max(1e-3, now - t.get("last_seen", now))
            ncx, ncy = d["x"] + d["w"] / 2.0, d["y"] + d["h"] / 2.0
            ocx, ocy = t["box"]["x"] + t["box"]["w"] / 2.0, t["box"]["y"] + t["box"]["h"] / 2.0
            a = self.vel_smooth
            t["vx"] = (1 - a) * t.get("vx", 0.0) + a * (ncx - ocx) / dt
            t["vy"] = (1 - a) * t.get("vy", 0.0) + a * (ncy - ocy) / dt
            t["box"] = d
            t["conf"] = float(d.get("conf") or 0.0)
            t["last_seen"] = now
            t["matched"] = True
            t["hits"] = t.get("hits", 0) + 1
            t["confirmed"] = t["hits"] >= self.min_hits
            taken_d.add(id(d))
            used.add(id(d))


def gates_for(zones, aspect=DEFAULT_ASPECT, **kw):
    """A GateCounter per active line zone, keyed the way the analyzer iterates them."""
    return [GateCounter(z, aspect=aspect, **kw)
            for z in (zones or [])
            if (z.get("kind") == "line") and z.get("is_active", True)]
