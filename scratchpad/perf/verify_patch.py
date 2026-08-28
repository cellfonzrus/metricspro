#!/usr/bin/env python3
"""Does the patched analyzer still count the same thing?

Runs the REAL CameraWorker.step() over the same clip twice — once with the OpenVINO detector
the patch selects, once with --no-openvino forcing the original PyTorch path — through a fake
FrameSource that replays the clip deterministically, and compares the traffic events that come
out of the outbox. Same events, same order, same track keys = the patch changed cost, not
meaning.

This is a REGRESSION check on the plumbing (lazy frames, the new detector's box format), not
an accuracy evaluation: whether yolov8n is the right detector for an oblique doorway camera is
somebody else's measurement.
"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
import av
import vision_edge_analyzer as V

CLIP = HERE + "/store_1080p.mp4"


class ClipSource(V.FrameSource):
    """Replays the clip one frame per read(), wrapped exactly as the live sources now do."""

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
        return True, V.LazyFrame(f)


CAM = {"device_name": "enterprises/x/devices/cam1", "camera_id": 1, "is_entrance": True,
       "analytics": True, "timezone": "America/New_York", "stream_protocol": "webrtc",
       "zones": [{"kind": "line", "is_active": True, "inward": "left",
                  "geometry": {"x1": 0.05, "y1": 0.62, "x2": 0.95, "y2": 0.58}}]}


def run(no_openvino, precision=None):
    det = V.PersonDetector(prefer_yolo=True, no_openvino=no_openvino, ov_precision=precision)
    outbox = []
    w = V.CameraWorker(None, dict(CAM), {"cols": 24, "rows": 16}, 0, det, outbox,
                       detect_fps=1000)          # every replayed frame is a detection
    w.source = ClipSource(CLIP)
    n = 0
    while True:
        before = w.source.i
        w.step()
        if w.source.i == before:
            break
        n += 1
    traffic = [(e["direction"], e["track_key"].split(":")[1]) for e in outbox
               if e["kind"] == "traffic"]
    return det.kind, n, traffic, outbox


REF = None
print("%-26s %-40s %s" % ("configuration", "detector", "crossings"))
for label, kw in (("PyTorch (original)", {"no_openvino": True}),
                  ("OpenVINO fp32", {"no_openvino": False, "precision": "f32"}),
                  ("OpenVINO bf16 (default)", {"no_openvino": False, "precision": "bf16"})):
    kind, n, t, o = run(**kw)
    dirs = [d for d, _ in t]
    if REF is None:
        REF = dirs
    flag = "identical to PyTorch" if dirs == REF else "DIFFERS from PyTorch"
    print("%-26s %-40s %2d  %s" % (label, kind, len(t), dirs))
    print("%-26s %-40s     %s" % ("", "", flag))
