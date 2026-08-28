#!/usr/bin/env python3
"""verify_patch.py, but with a clock tied to the VIDEO instead of the wall.

WHY THIS EXISTS. verify_patch.py replays a clip through the real CameraWorker.step() as fast as
the machine can go, and step() timestamps each tick with time.time(). So the interval the tracker
sees between two frames is not the clip's frame interval — it is HOW LONG THE DETECTOR TOOK. On
this box that is ~46 ms for PyTorch and ~17 ms for bf16, so the three configurations under test
are fed three different time bases for the same video, and the tracker's velocity estimate (px per
SECOND) scales with the difference. Any crossing difference that comes out the other end is then
unattributable: precision, or the clock?

Here the clock is a pure function of the frame index, identical for every configuration, so a
difference in the output is a difference in the arithmetic.
"""
import os, sys, time as _time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
import av
import vision_edge_analyzer as V

CLIP = HERE + "/store_1080p.mp4"
FPS = 30.0
_clock = {"t": 0.0}


class ClipSource(V.FrameSource):
    def __init__(self, path, limit=150):
        cont = av.open(path)
        st = cont.streams.video[0]
        st.thread_type = "AUTO"
        self.frames = []
        for f in cont.decode(st):
            self.frames.append(f)
            if len(self.frames) >= limit:
                break
        cont.close()
        self.i = 0

    def read(self):
        if self.i >= len(self.frames):
            return False, None
        f = self.frames[self.i]
        self.i += 1
        _clock["t"] = self.i / FPS          # the clip's own time, not the machine's
        return True, V.LazyFrame(f)


CAM = {"device_name": "enterprises/x/devices/cam1", "camera_id": 1, "is_entrance": True,
       "analytics": True, "timezone": "America/New_York", "stream_protocol": "webrtc",
       "zones": [{"kind": "line", "is_active": True, "inward": "left",
                  "geometry": {"x1": 0.05, "y1": 0.62, "x2": 0.95, "y2": 0.58}}]}


def run(no_openvino, precision=None):
    det = V.PersonDetector(prefer_yolo=True, no_openvino=no_openvino, ov_precision=precision)
    outbox = []
    w = V.CameraWorker(None, dict(CAM), {"cols": 24, "rows": 16}, 0, det, outbox, detect_fps=1000)
    w.source = ClipSource(CLIP)
    real = _time.time
    V.time.time = lambda: _clock["t"]        # the whole point
    try:
        while True:
            before = w.source.i
            w.step()
            if w.source.i == before:
                break
    finally:
        V.time.time = real
    return det.kind, [e["direction"] for e in outbox if e["kind"] == "traffic"]


import glob
LINES = [
    {"kind": "line", "is_active": True, "inward": "left",
     "geometry": {"x1": 0.05, "y1": 0.62, "x2": 0.95, "y2": 0.58}},
    {"kind": "line", "is_active": True, "inward": "right",
     "geometry": {"x1": 0.50, "y1": 0.10, "x2": 0.50, "y2": 0.95}},
    {"kind": "line", "is_active": True, "inward": "left",
     "geometry": {"x1": 0.30, "y1": 0.95, "x2": 0.72, "y2": 0.15}},
]
agree = disagree = 0
for clip in sorted(glob.glob(HERE + "/store_*.mp4")):
    for li, ln in enumerate(LINES):
        CAM["zones"] = [ln]
        CLIP = clip
        globals()["CLIP"] = clip
        REF = None; row = []
        for label, kw in (("pytorch", {"no_openvino": True}),
                          ("ov_f32", {"no_openvino": False, "precision": "f32"}),
                          ("ov_bf16", {"no_openvino": False, "precision": "bf16"})):
            _clock["t"] = 0.0
            kind, dirs = run(**kw)
            if REF is None: REF = dirs
            row.append((label, dirs, dirs == REF))
        ok = all(r[2] for r in row)
        agree += ok; disagree += (not ok)
        print("%-26s line%d  %s  %s" % (os.path.basename(clip), li,
              " | ".join("%s %2d" % (l, len(d)) for l, d, _ in row),
              "agree" if ok else "*** DIFFER: " + str([d for _, d, _ in row])))
print("")
print("%d of %d clip/line combinations agree across all three backends"
      % (agree, agree + disagree))
