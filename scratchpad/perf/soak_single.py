#!/usr/bin/env python3
"""The SHIPPED topology: one process, one loop, N cameras taken in turn.

vision_edge_analyzer.Analyzer.run() walks self.workers and calls step() on each, so every
camera's inference is serialised through one thread while PyTorch spreads that single
inference over all cores. This reproduces that shape exactly — including that each camera's
decoder still has to keep up with its own live 30 fps stream, which here runs in a decoder
thread per camera the way aiortc's does.

usage: soak_single.py N [clip] [detect_fps] [seconds] [engine: torch|ov]
"""
import os, sys, time, threading, resource
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
sys.path.insert(0, HERE)

N = int(sys.argv[1])
CLIP = sys.argv[2] if len(sys.argv) > 2 else HERE + "/store_1080p.mp4"
DF = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0
SECS = float(sys.argv[4]) if len(sys.argv) > 4 else 22.0
ENGINE = sys.argv[5] if len(sys.argv) > 5 else "torch"

import av
from vision_edge_analyzer import Tracker
from app.modules.vision import geometry as GEO

stop = threading.Event()


class Source(threading.Thread):
    """One decoder thread per camera, dropping frames into a slot — WebRtcFrameSource's shape.
    Paced to the source rate: a live stream arrives whether or not the loop is ready."""

    def __init__(self, clip):
        super().__init__(daemon=True)
        self.clip = clip
        self.frame = None
        self.lock = threading.Lock()
        self.n = 0
        self.eager_bgr = (ENGINE == "torch")   # what WebRtcFrameSource does today

    def run(self):
        t0 = time.perf_counter()
        while not stop.is_set():
            cont = av.open(self.clip)
            st = cont.streams.video[0]
            st.thread_count = 1
            st.thread_type = "NONE"
            for f in cont.decode(st):
                if stop.is_set():
                    break
                due = t0 + self.n / 30.0
                d = due - time.perf_counter()
                if d > 0:
                    time.sleep(d)
                self.n += 1
                img = f.to_ndarray(format="bgr24") if self.eager_bgr else f
                with self.lock:
                    self.frame = img
            cont.close()

    def read(self):
        with self.lock:
            return self.frame


def main():
    if ENGINE == "torch":
        import torch
        torch.set_num_threads(os.cpu_count())
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt")

        def detect(im):
            r = model(im, verbose=False, classes=[0])
            h, w = im.shape[:2]
            return [{"x": float(b.xyxy[0][0]) / w, "y": float(b.xyxy[0][1]) / h,
                     "w": float(b.xyxy[0][2] - b.xyxy[0][0]) / w,
                     "h": float(b.xyxy[0][3] - b.xyxy[0][1]) / h,
                     "conf": float(b.conf[0])} for b in r[0].boxes]
    else:
        from fast_detector import FastPersonDetector
        d = FastPersonDetector(HERE + "/eng_384x640.onnx", threads=os.cpu_count())
        detect = d

    srcs = [Source(CLIP) for _ in range(N)]
    trackers = [Tracker() for _ in range(N)]
    last = [-1e9] * N
    ndet = [0] * N
    zones = [{"kind": "line", "is_active": True, "inward": "left",
              "geometry": {"x1": 0.1, "y1": 0.6, "x2": 0.9, "y2": 0.55}}]
    [s.start() for s in srcs]
    time.sleep(1.0)
    r0 = resource.getrusage(resource.RUSAGE_SELF)
    c0 = r0.ru_utime + r0.ru_stime
    base = [s.n for s in srcs]
    t0 = time.perf_counter()
    interval = 1.0 / DF
    while time.perf_counter() - t0 < SECS:
        worked = False
        for i, s in enumerate(srcs):
            now = time.perf_counter()
            if now - last[i] < interval:
                continue
            fr = s.read()
            if fr is None:
                continue
            im = fr if ENGINE == "torch" else fr.to_ndarray(format="bgr24")
            last[i] = now
            dets = detect(im)
            ndet[i] += 1
            worked = True
            tracks = trackers[i].update(dets, now)
            feet = {t["key"]: GEO.foot_point(t["box"]) for t in tracks}
            for t in tracks:
                foot = feet[t["key"]]
                if GEO.excluded(zones, foot):
                    continue
                if t.get("prev_foot"):
                    GEO.crossing_direction(zones[0], t["prev_foot"], foot)
                t["prev_foot"] = foot
                GEO.grid_cell(foot, 24, 16)
        if not worked:
            time.sleep(0.02)
    wall = time.perf_counter() - t0
    stop.set()
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (r1.ru_utime + r1.ru_stime) - c0
    dec = sum(s.n - base[i] for i, s in enumerate(srcs)) / N / wall
    det = sum(ndet) / N / wall
    ok = dec > 29 and det > 0.88 * DF
    print("single-proc %-6s %-18s N=%-3d df=%-4.1f  decode %5.1f/30 fps  detect %5.2f/%.1f fps"
          "  cores %4.2f/%d  %s"
          % (ENGINE, os.path.basename(CLIP), N, DF, dec, det, DF, cpu / wall,
             os.cpu_count(), "OK" if ok else "BEHIND"))


if __name__ == "__main__":
    main()
