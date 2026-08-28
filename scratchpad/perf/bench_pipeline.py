#!/usr/bin/env python3
"""END-TO-END CPU BUDGET FOR ONE CAMERA, in CPU-milliseconds per second of video.

CPU-ms per second of wall clock is the only currency that adds up across cameras. A machine
has 1000 CPU-ms per second per core; a camera costs what it costs; capacity is division.

Two configurations of the SAME pipeline, on the same clip:
  CURRENT  what vision_edge_analyzer.py does today — PyTorch yolov8n at imgsz 640 with all
           cores, and aiortc converting every arriving frame to BGR whether the detector
           wants it or not.
  FAST     fast_detector.FastPersonDetector (OpenVINO fp32, 1 thread, fixed 640x384), and
           BGR conversion done lazily, only for frames a detection actually consumes.

usage: bench_pipeline.py [clip] [detect_fps] [stream_fps]
"""
import os, sys, time, resource, statistics, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
sys.path.insert(0, HERE)

CLIP = sys.argv[1] if len(sys.argv) > 1 else HERE + "/store_1080p.mp4"
DETECT_FPS = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
STREAM_FPS = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np, cv2, av


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


class Stage:
    def __init__(self):
        self.t = 0.0
        self.n = 0

    def __enter__(self):
        self._c = cpu()
        return self

    def __exit__(self, *a):
        self.t += cpu() - self._c
        self.n += 1

    def ms_per_call(self):
        return self.t / self.n * 1000 if self.n else 0.0


def open_single_threaded(path):
    """A decoder pinned to ONE thread, so its cost lands in this process's rusage as the
    per-core cost it really is. Frame threading would hide the same work in helper threads."""
    cont = av.open(path)
    st = cont.streams.video[0]
    st.thread_count = 1
    st.thread_type = "NONE"
    return cont, st


def run(mode, nframes=300):
    from vision_edge_analyzer import Tracker
    from app.modules.vision import geometry as GEO

    cont, vstream = open_single_threaded(CLIP)
    pk = [p for p in cont.demux(vstream) if p.size][:nframes]

    if mode == "current":
        import torch
        torch.set_num_threads(int(os.environ.get("CUR_THREADS", "4")))
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt")

        def detect(im):
            r = model(im, verbose=False, classes=[0])
            h, w = im.shape[:2]
            return [{"x": float(b.xyxy[0][0]) / w, "y": float(b.xyxy[0][1]) / h,
                     "w": float(b.xyxy[0][2] - b.xyxy[0][0]) / w,
                     "h": float(b.xyxy[0][3] - b.xyxy[0][1]) / h,
                     "conf": float(b.conf[0])} for b in r[0].boxes]
        lazy_bgr = False
    else:
        from fast_detector import FastPersonDetector, export_once
        onnx = export_once(HERE + "/eng_384x640.onnx")
        det = FastPersonDetector(onnx, threads=1,
                                 motion_gate=(mode == "fast_gated"))
        detect = det
        lazy_bgr = True

    S = {k: Stage() for k in ("decode", "to_bgr", "detect", "track", "geom")}
    tracker = Tracker()
    zones = [{"kind": "line", "is_active": True, "inward": "left",
              "geometry": {"x1": 0.1, "y1": 0.6, "x2": 0.9, "y2": 0.55}}]
    interval = 1.0 / DETECT_FPS
    vtime = 0.0
    last_detect = -1e9
    ndet, nboxes = 0, []
    frames_seen = 0
    warm = 0

    for p in pk:
        with S["decode"]:
            try:
                fr = p.decode()
            except Exception:
                continue
        for f in fr:
            frames_seen += 1
            vtime += 1.0 / STREAM_FPS
            want = (vtime - last_detect) >= interval
            if not lazy_bgr:
                with S["to_bgr"]:
                    im = f.to_ndarray(format="bgr24")     # aiortc converts EVERY frame
            if not want:
                continue
            if lazy_bgr:
                with S["to_bgr"]:
                    im = f.to_ndarray(format="bgr24")     # only frames we will look at
            last_detect = vtime
            if warm < 3:
                warm += 1
                detect(im)
                for k in S.values():
                    k.t = 0.0; k.n = 0
                continue
            with S["detect"]:
                dets = detect(im)
            ndet += 1
            nboxes.append(len(dets))
            with S["track"]:
                tracks = tracker.update(dets, vtime)
            with S["geom"]:
                feet = {t["key"]: GEO.foot_point(t["box"]) for t in tracks}
                for t in tracks:
                    foot = feet[t["key"]]
                    if GEO.excluded(zones, foot):
                        continue
                    if t.get("prev_foot"):
                        GEO.crossing_direction(zones[0], t["prev_foot"], foot)
                    t["prev_foot"] = foot
                    GEO.grid_cell(foot, 24, 16)

    secs = vtime
    per_sec = {k: v.t / secs * 1000 for k, v in S.items()}
    return {
        "mode": mode, "video_seconds": round(secs, 2), "frames": frames_seen,
        "detections": ndet, "mean_boxes": round(statistics.mean(nboxes), 2) if nboxes else 0,
        "per_call_ms": {k: round(v.ms_per_call(), 3) for k, v in S.items()},
        "cpu_ms_per_video_second": {k: round(v, 1) for k, v in per_sec.items()},
        "total_cpu_ms_per_video_second": round(sum(per_sec.values()), 1),
    }


if __name__ == "__main__":
    mode = os.environ.get("MODE", "current")
    r = run(mode)
    print(json.dumps(r, indent=1))
