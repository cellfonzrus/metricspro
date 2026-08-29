#!/usr/bin/env python3
"""MetricsPro Vision — the EDGE ANALYZER. Runs beside the store, holds the live camera feed, and
posts derived numbers to the platform.

    python3 backend/vision_edge_analyzer.py --api https://api.example.com \
        --agent-key va_xxxxxxxx --secret <the secret shown once at registration>

WHY THIS IS A SEPARATE PROCESS — AND WHERE IT CAN RUN
─────────────────────────────────────────────────────
A Nest live-stream grant expires in about five minutes and must be re-negotiated; decoding video is
CPU-bound and continuous. A shared FastAPI process on Railway can do neither for a dozen cameras. So
the platform brokers the grant and stores the numbers, and THIS process holds the stream and does the
pixel work.

IT DOES NOT HAVE TO BE IN THE STORE. Nest cameras stream from GOOGLE'S CLOUD, not over the shop LAN
— this process authenticates to Google over the internet and the media flows Google -> here, wherever
"here" is. So one machine in a back office, a rack, or a cloud VM can serve every store, and that is
usually the better deployment: one thing to maintain, under your control, not competing with a
register, and not dependent on someone remembering to leave a PC on.

Two things make the central shape work, and both are handled:
  * an agent with NO store_code is not pinned to a store and receives every camera in every home its
    company has connected (a store-pinned agent still may not write for any other store);
  * each camera carries its STORE's timezone from the server, so one analyzer spanning several zones
    still files every event under the right business date.
The trade is a single point of failure: one box down is every store blind, where one box per store
degrades to one store blind. For two or three stores that is usually worth it; past that, or across
very different network paths, split them.

WHAT IT SENDS, AND WHAT IT DESTROYS
───────────────────────────────────
Sends: "a track crossed the entrance line inward at 14:02", "grid cell (4,7) accumulated 38
person-seconds this minute", and (only when the operator has enabled audio AND the employee has
consented) a redacted transcript segment.

Destroys: every frame, immediately after it is measured. Every audio buffer, immediately after it is
transcribed. Nothing is written to disk by this process. There is no recording, no face descriptor,
and no customer identity anywhere in it — see the migration 900 header for why that is a design
constraint and not an aspiration.

THE SERVER IS THE AUTHORITY ON WHAT IS ALLOWED
──────────────────────────────────────────────
This process re-fetches /vision/edge/config every minute and obeys the three booleans it returns
(traffic / heatmap / audio_analytics) plus the per-camera flags and the consented-employee list. An
operator switching audio off in the UI stops the microphone HERE within a minute — the gate is not
merely "the server refuses to store it".

DEPENDENCIES (degrade individually, and say which one is missing at startup)
───────────────────────────────────────────────────────────────────────────
  requests            required — the only hard dependency
  opencv-python       needed to read an RTSP stream and detect people. Without it the process runs
                      in --dry-run shape: it authenticates, fetches config, and reports what it
                      WOULD do, which is exactly what you want while proving out a deployment.
  ultralytics / onnxruntime  optional. A YOLO person detector is far better than the OpenCV HOG
                      fallback in a real store (occlusion, seated people, oblique angles). The HOG
                      path exists so the module produces numbers on day one on hardware with no
                      accelerator, and its weaker recall is stated rather than hidden.
  aiortc              required for WEBRTC cameras — which is every Nest camera sold since 2021,
                      including the Nest Cam (indoor, wired, 2nd gen), plus any older camera that
                      has been migrated into the Google Home app. Without it those cameras are
                      reported as unreadable with the install command, rather than skipped silently.

FIRST RUN ON SITE: USE --probe
──────────────────────────────
    python3 vision_edge_analyzer.py --api … --agent-key … --secret … --probe

It connects to one camera, saves a single frame, and exits. That proves the entire chain — agent
secret, Google authorization, stream negotiation, decoding — in one command instead of a long run
and a log read. The frame it writes is also exactly the still needed to place the counting line and
the exclusion zones, so the install visit produces that artifact on the spot.

ONE PATH THIS REFERENCE BUILD DOES NOT IMPLEMENT, STATED PLAINLY
────────────────────────────────────────────────────────────────
* **The audio / transcript path.** OpenCV's capture discards the audio track entirely, so producing
  transcripts needs a separate ffmpeg demux of the same RTSP URL, a VAD to cut it into utterances,
  and a local ASR (faster-whisper runs adequately on a small box). That is deliberately not wired up
  here, because the surrounding contract matters more than the transcription and is easy to get
  wrong. The contract, which the server enforces:
    - only send segments when GET /edge/config reports `attribution == "unambiguous"` — exactly one
      CONSENTED employee is clocked in at this store — and stamp that `on_duty[0].employee_id`;
    - `speaker` must be "employee"; anything else is dropped at ingest, so the customer's half of
      the conversation must never be sent in the first place;
    - the audio buffer is destroyed as soon as the utterance is transcribed. Nothing is written to
      disk, and no audio is ever posted to the platform.
  Event shape (see app/modules/vision/ingest.py for the authoritative reader):
    {"kind": "transcript", "device_name": …, "employee_id": …, "speaker": "employee",
     "text": …, "started_at": iso, "ended_at": iso, "duration_s": float, "elapsed_s": float,
     "local_date": "YYYY-MM-DD", "local_hour": int, "asr_confidence": float}
"""
import argparse
import hashlib
import os
import hmac
import json
import logging
import asyncio
import signal
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import activity as ACT
from app.modules.vision import counting as CNT  # noqa: E402  (the SAME rules the server proves)
from app.modules.vision import geometry as GEO   # noqa: E402  (the SAME rules the server proves)

log = logging.getLogger("vision-edge")

def process_cpu_seconds() -> float:
    """CPU time this process has consumed across all its threads, on any OS.

    Capacity is spent in CPU-seconds, not wall-clock seconds, so the benchmark has to be able
    to read them — and the machine most likely to be benchmarked is a Windows till, where
    `resource` does not exist. `time.process_time()` is stdlib and portable, and unlike
    `resource` on Linux it already includes every thread, which is exactly what is wanted when
    the thing being timed spreads itself over four cores."""
    return time.process_time()


DETECT_FPS = 6               # detections per second per camera — see CameraWorker.step()
SAMPLE_SECONDS = 60          # how often occupancy is flushed as presence samples
POST_SECONDS = 15            # how often the outbox is drained
CONFIG_SECONDS = 60          # how often the server's answer about what is allowed is re-read
TRACK_TTL_SECONDS = 5        # a track unseen this long is retired
IOU_MATCH = 0.25             # minimum overlap to consider two boxes the same person


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Transport — HMAC-signed calls to the platform
# ══════════════════════════════════════════════════════════════════════════════════════════════
class Api:
    """Every request is signed with HMAC-SHA256 over `timestamp.body`, exactly as
    app/modules/vision/ingest.py verifies it. A GET signs an empty body."""

    def __init__(self, base_url, agent_key, secret, timeout=30):
        import requests
        self._requests = requests
        self.base = base_url.rstrip("/")
        self.agent_key = agent_key
        self.secret = secret
        self.timeout = timeout

    def _headers(self, body: bytes):
        ts = str(int(time.time()))
        sig = hmac.new(self.secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return {"Content-Type": "application/json", "X-Vision-Agent": self.agent_key,
                "X-Vision-Timestamp": ts, "X-Vision-Signature": sig}

    def call(self, method, path, payload=None):
        body = json.dumps(payload or {}, separators=(",", ":")).encode() if method != "GET" else b""
        url = f"{self.base}/api/v1/vision/edge/{path.lstrip('/')}"
        r = self._requests.request(method, url, headers=self._headers(body), data=body or None,
                                   timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {(r.text or '')[:200]}")
        return r.json() if r.content else {}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Detection + tracking
# ══════════════════════════════════════════════════════════════════════════════════════════════
DETECT_SHAPE = (384, 640)       # (h, w) — 16:9 and stride-32 legal, which every fixed camera is


class _OpenVinoYolo:
    """yolov8n on the OpenVINO runtime: the same weights and the same boxes, several times
    cheaper per detection than the PyTorch path.

    WHY THIS IS WORTH A SECOND BACKEND. Measured on one core at 640x384, per detection:
    PyTorch 102.1 CPU-ms, ONNX Runtime 48.2, OpenVINO 17.1. The gap is not the model, it is
    the kernels — and on a Xeon with AMX, OpenVINO additionally runs the graph in bfloat16 by
    default, which is most of that 17.1. On a machine with only AVX2 the same call measured
    85.1 CPU-ms, so the win is real but hardware-dependent, and `--benchmark` reports what THIS
    box actually does rather than repeating a number from somewhere else.

    ONE THREAD, ON PURPOSE. Multi-threaded inference lowers latency and RAISES cost: the same
    detection costs 20.0 CPU-ms at one thread and 43.0 CPU-ms across four. Cameras are already
    parallel work; spending 2.15x the CPU to make one camera finish sooner than its 6 fps
    budget needs buys nothing and halves the box.

    ENABLE_CPU_PINNING OFF, ALSO ON PURPOSE — see the compile options below. This one is a trap
    rather than a tuning choice.
    """

    # iou=0.7 is ULTRALYTICS' predict default, and it is not a tuning choice — it is the number
    # that makes this detector agree with the PyTorch one it replaces. At 0.45 the two disagreed
    # on 49 of 150 frames of the same clip: every disagreement was a box that ultralytics keeps
    # and cv2.dnn.NMSBoxes suppresses, all of them at the low-confidence end (e.g. a 0.284 box
    # overlapping a 0.545 one at IoU 0.515 — kept under 0.7, dropped under 0.45). Those marginal
    # boxes are exactly what counting.PredictiveTracker's second association pass exists to use,
    # so losing them breaks tracks in the doorway. At 0.7 the fp32 path returns identical
    # detections on all 150 frames. Change this and you change the count.
    def __init__(self, threads=1, shape=DETECT_SHAPE, conf=0.25, iou=0.7, weights="yolov8n.pt",
                 precision=None):
        import numpy as np
        import openvino as ov
        self.np = np
        self.h, self.w = shape
        self.conf, self.iou = conf, iou
        path = self._graph(shape, weights)
        core = ov.Core()
        # ENABLE_CPU_PINNING must be False. OpenVINO pins inference threads to specific cores
        # and chooses them per process, knowing nothing about the other analyzer processes on
        # the box. Run one process per camera with pinning left on and every one of them pins
        # to the same core: measured at 8 workers taking 159 ms of wall clock for 27 ms of CPU
        # each while the machine sat 61% idle. A box that looks bored and still cannot keep up
        # is the most expensive kind of capacity bug to diagnose.
        cfg = {
            "PERFORMANCE_HINT": "LATENCY",
            "INFERENCE_NUM_THREADS": int(threads),
            "NUM_STREAMS": 1,
            "ENABLE_CPU_PINNING": False,
        }
        # PRECISION IS A DECISION, NOT A DETAIL, so it is a flag rather than a default that
        # nobody reads. On a Xeon with AMX, OpenVINO silently runs an fp32 graph in bfloat16 —
        # 17.1 CPU-ms per detection against 45.7 for true fp32, and worth having.
        #
        # MEASURED, over 4 clips x 3 line placements, every backend on the same clock
        # (scratchpad/perf/verify_multi.py):
        #     fp32   agrees with the PyTorch path on 12 of 12 — identical crossings.
        #     bf16   disagrees on 4 of 12, and always the same way: one EXTRA crossing.
        #
        # So fp32 is safe and bf16 is not, for a sharper reason than "less precise". bf16's bias
        # is toward crossings NOBODY MADE — the exact error class counting.GateCounter exists to
        # remove — so it would hand back at the detector what the gate just took away. It moves
        # boxes by up to 0.065 of a frame height, which is enough to carry a marginal track
        # across a band.
        #
        # An earlier draft of this comment blamed "the confidence threshold" and an IoU tracker
        # turning a marginal box into an extra track. That was wrong on both counts: the real
        # causes were this class's own NMS iou (see above) and a benchmark that timed three
        # backends on three different clocks. Both are fixed; the bf16 bias survives them.
        # DEFAULTS TO f32, which is NOT what OpenVINO would choose on its own. Left to itself it
        # runs bf16 on any AMX machine — faster, and the one arithmetic measured to invent
        # crossings. Defaulting to the fast-and-wrong option on precisely the hardware a store
        # would buy, with nothing in the log to say so, is the silent failure this file exists to
        # avoid. `--ov-precision auto` gives OpenVINO the choice back for anyone benchmarking.
        prec = str(precision or "f32")
        if prec != "auto":
            cfg["INFERENCE_PRECISION_HINT"] = ov.Type.f32 if prec == "f32" else ov.Type.bf16
        self._cm = core.compile_model(core.read_model(path), "CPU", cfg)
        self._req = self._cm.create_infer_request()
        self._buf = np.empty((1, 3, self.h, self.w), np.float32)
        try:
            prec = str(self._cm.get_property("INFERENCE_PRECISION_HINT"))
        except Exception:
            prec = "?"
        self.kind = "yolov8n-openvino-%dx%d-%s" % (self.w, self.h, prec.strip("<>").split()[-1]
                                                   if prec != "?" else "?")

    @staticmethod
    def _graph(shape, weights):
        """The fixed-shape ONNX graph OpenVINO compiles, exported once and cached beside the
        weights. Export needs ultralytics; steady-state running does not, which is what lets a
        store box carry the analyzer without a PyTorch install."""
        cache = os.path.join(os.path.expanduser("~/.metricspro"),
                             "yolov8n_%dx%d.onnx" % (shape[1], shape[0]))
        if os.path.exists(cache):
            return cache
        os.makedirs(os.path.dirname(cache), mode=0o700, exist_ok=True)
        from ultralytics import YOLO
        produced = YOLO(weights).export(format="onnx", imgsz=shape, simplify=True,
                                        dynamic=False, verbose=False)
        os.replace(produced, cache)
        log.info("exported the detector graph to %s (once)", cache)
        return cache

    def __call__(self, frame):
        import cv2
        np = self.np
        H, W = frame.shape[:2]
        r = min(self.w / W, self.h / H)
        nw, nh = int(round(W * r)), int(round(H * r))
        top, left = (self.h - nh) // 2, (self.w - nw) // 2
        canvas = np.full((self.h, self.w, 3), 114, np.uint8)
        cv2.resize(frame, (nw, nh), dst=canvas[top:top + nh, left:left + nw],
                   interpolation=cv2.INTER_LINEAR)
        x = canvas.astype(np.float32)
        np.multiply(x, 1.0 / 255.0, out=x)
        self._buf[0, 0], self._buf[0, 1], self._buf[0, 2] = x[:, :, 2], x[:, :, 1], x[:, :, 0]
        self._req.infer({0: self._buf})
        p = self._req.get_output_tensor(0).data[0]      # (84, anchors); row 4 = person score
        keep = p[4, :] > self.conf
        if not keep.any():
            return []
        b, cc = p[:4, keep].T, p[4, keep].astype(np.float32)
        boxes = [[float((bx - bw / 2 - left) / r), float((by - bh / 2 - top) / r),
                  float(bw / r), float(bh / r)] for bx, by, bw, bh in b]
        idx = cv2.dnn.NMSBoxes(boxes, cc.tolist(), self.conf, self.iou)
        if idx is None or len(idx) == 0:
            return []
        out = []
        for i in np.array(idx).reshape(-1):
            bx, by, bw, bh = boxes[int(i)]
            out.append({"x": bx / W, "y": by / H, "w": bw / W, "h": bh / H,
                        "conf": float(cc[int(i)])})
        return out


class PersonDetector:
    """Returns [{"x","y","w","h","conf"}] in NORMALIZED coordinates.

    Two backends, tried in order.

    YOLO (`ultralytics`) is the real one and what a production store should run: it handles seated
    people, partial occlusion and oblique ceiling angles, which is most of what a shop floor is.

    OpenCV's HOG people detector is a legacy fallback for a machine with nothing else. Two warnings
    about it, both learned the hard way: it MISSES seated and heavily occluded people, so counts read
    low; and it does not exist at all in OpenCV 5, which removed `objdetect` from the default wheel.
    So the lookup below tries both module locations and then gives up honestly rather than leaving a
    detector that returns nothing — an analyzer that runs all day and reports zero customers looks
    exactly like a quiet store, which is the most expensive kind of bug to notice.
    """

    def __init__(self, prefer_yolo=True, pose=False, no_openvino=False, ov_threads=1,
                 ov_precision=None):
        """`pose=True` loads the POSE weights instead of the plain detector.

        This is a model SWAP, not a second pass, and that is the whole reason posture is affordable:
        yolov8n-pose returns boxes AND 17 keypoints from one inference, so a store already paying for
        detection gets posture for roughly 20% more time rather than double. Running a separate pose
        model over the same frames would have been the obvious wiring and would have halved the
        cameras a box could carry.

        The HOG fallback has no keypoints at all, so a machine that ends up on it produces detection
        and no posture — reported honestly by `supports_pose` rather than by silently empty columns.
        """
        self.kind = None
        self.reason = ""
        self._yolo = None
        self._hog = None
        self._ov = None
        self.supports_pose = False
        # OPENVINO FIRST, and only for the plain detector. Same yolov8n weights, and at fp32
        # the SAME BOXES — identical detections on all 150 frames of the reference clip once
        # the NMS iou matches ultralytics' 0.7 (see _OpenVinoYolo), which it now does —
        # measured at 17.1 CPU-ms per detection against PyTorch's 102.1 single-threaded and
        # 175.8 across four threads. That is the difference between three cameras on a box and
        # nine. Pose is deliberately not routed here: the pose head's decode is a second piece
        # of work to get right and posture is a per-tenant feature, so it stays on the proven
        # path until somebody needs it to be fast.
        if prefer_yolo and not pose and not no_openvino:
            try:
                self._ov = _OpenVinoYolo(threads=ov_threads, precision=ov_precision)
                self.kind = self._ov.kind
                return
            except Exception as e:
                log.info("OpenVINO detector unavailable (%s: %s) — falling back to PyTorch",
                         type(e).__name__, e)
        if prefer_yolo:
            try:
                from ultralytics import YOLO
                self._yolo = YOLO("yolov8n-pose.pt" if pose else "yolov8n.pt")
                self.kind = "yolov8n-pose" if pose else "yolov8n"
                self.supports_pose = bool(pose)
                return
            except Exception as e:
                self.reason = f"ultralytics unavailable ({type(e).__name__})"
                log.info("YOLO unavailable (%s) — trying the OpenCV fallback", type(e).__name__)
        self._load_hog()

    def _load_hog(self):
        """HOG lives in `cv2` on OpenCV 4 and in `cv2.objdetect` where that module is built; it is
        absent from the default OpenCV 5 wheel. Try each, then say which it was."""
        try:
            import cv2
        except Exception as e:
            self.reason = f"opencv not installed ({type(e).__name__})"
            return
        for holder in (cv2, getattr(cv2, "objdetect", None)):
            if holder is None or not hasattr(holder, "HOGDescriptor"):
                continue
            try:
                hog = holder.HOGDescriptor()
                getter = (getattr(holder, "HOGDescriptor_getDefaultPeopleDetector", None)
                          or getattr(holder.HOGDescriptor, "getDefaultPeopleDetector", None))
                if getter is None:
                    continue
                hog.setSVMDetector(getter())
                self._hog = hog
                self.kind = "opencv-hog"
                return
            except Exception:
                continue
        self.reason = (f"opencv {getattr(cv2, '__version__', '?')} has no HOG people detector "
                       "(removed in OpenCV 5)")

    def unavailable_message(self) -> str:
        return (f"No person detector is available on this machine — {self.reason or 'unknown reason'}. "
                "Install one with:  pip install ultralytics   "
                "(that is also the detector production stores should use).")

    def __call__(self, frame):
        if frame is None or self.kind is None:
            return []
        h, w = frame.shape[:2]
        if self._ov is not None:
            return self._ov(frame)
        if self._yolo is not None:
            out = []
            for r in self._yolo(frame, verbose=False, classes=[0]):     # class 0 = person
                # Keypoints come back parallel to boxes, in the same order, when the pose weights are
                # loaded. Normalized here so everything downstream is resolution-independent — the
                # posture rule compares a thigh to a torso and would otherwise drift with the frame.
                kps = None
                if self.supports_pose and getattr(r, "keypoints", None) is not None:
                    try:
                        kps = r.keypoints.data.tolist()
                    except Exception:
                        kps = None
                for i, b in enumerate(r.boxes):
                    x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                    det = {"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                           "conf": float(b.conf[0])}
                    if kps is not None and i < len(kps):
                        det["keypoints"] = [[float(k[0]) / w, float(k[1]) / h,
                                             float(k[2]) if len(k) > 2 else 1.0]
                                            for k in kps[i]]
                    out.append(det)
            return out
        rects, weights = self._hog.detectMultiScale(frame, winStride=(8, 8), scale=1.05)
        return [{"x": x / w, "y": y / h, "w": bw / w, "h": bh / h,
                 "conf": float(weights[i]) if i < len(weights) else 0.5}
                for i, (x, y, bw, bh) in enumerate(rects)]


class Tracker:
    """A deliberately simple IoU tracker. NO LONGER USED FOR COUNTING — kept as the baseline.

    THE ARGUMENT THIS DOCSTRING USED TO MAKE, AND WHY MEASUREMENT KILLED IT. It said that a track
    lost and re-acquired simply becomes a NEW track, which the server's visit pairing already
    tolerates. That is true of the SERVER. It is not true of the crossing test, which is what
    actually counts people: a new track starts with `prev_foot = None`, so the step that carries a
    person over the line is skipped, and the entry is never emitted at all. The failure this
    docstring called "handled" was a silent undercount — harness_vision_counting.py measures 20
    brisk walkers at 6 detections/second as 11.

    So the reasoning was sound about occlusion and wrong about the doorway, which is the one place
    this module has to be right. CameraWorker now uses counting.PredictiveTracker instead.

    THIS CLASS STAYS because it is the control arm: harness_vision_counting.py runs both pipelines
    over the same synthetic traffic, and every claim the new one makes is a claim RELATIVE to this
    one. Delete it and the proof becomes an assertion. It is not wired to any live camera."""

    def __init__(self):
        self._tracks = {}
        self._next = 1

    @staticmethod
    def _iou(a, b):
        ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
        bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
        ix = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
        iy = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
        inter = ix * iy
        union = a["w"] * a["h"] + b["w"] * b["h"] - inter
        return inter / union if union > 0 else 0.0

    def update(self, detections, now):
        for t in self._tracks.values():
            t["matched"] = False
        for d in detections:
            best, best_iou = None, IOU_MATCH
            for key, t in self._tracks.items():
                if t["matched"]:
                    continue
                v = self._iou(t["box"], d)
                if v > best_iou:
                    best, best_iou = key, v
            if best is None:
                best = f"tk{self._next}"
                self._next += 1
                self._tracks[best] = {"key": best, "first_seen": now, "prev_foot": None}
            t = self._tracks[best]
            t.update({"box": d, "conf": d.get("conf", 0.0), "last_seen": now, "matched": True})
        for key in [k for k, t in self._tracks.items() if now - t.get("last_seen", now) > TRACK_TTL_SECONDS]:
            self._tracks.pop(key, None)
        return [t for t in self._tracks.values() if t.get("matched")]


class FaceState:
    """Mouth-openness on one frame, and NOTHING that could identify the face it came from.

    WHY THIS IS A SEPARATE CLASS FROM EVERYTHING ELSE. The person detector's 17 keypoints include a
    nose and eyes but no mouth, so measuring a mouth needs a face-landmark model. That is the most
    invasive thing this analyzer does, so it is isolated here, loaded only when the server says the
    tenant has face_state switched on, and reduced to a single float per frame before it returns.

    WHAT LEAVES THIS CLASS: one number, the ratio of mouth height to mouth width. What does NOT
    leave it, and is never written to disk anywhere: the landmarks, the face crop, the frame, and
    any embedding or descriptor. A ratio cannot be matched against another face; a geometry template
    can, and we compute none. That distinction is what keeps this on the right side of the module's
    no-biometrics commitment (migration 900) rather than adding to the platform's BIPA exposure.

    Optional dependency, on purpose. Without mediapipe installed there is no face state at all and
    the analyzer says so at startup — a tenant does not get silent zeros in a column they enabled.
    """

    LIPS_TOP, LIPS_BOTTOM = 13, 14          # inner lip centre, MediaPipe FaceMesh topology
    MOUTH_LEFT, MOUTH_RIGHT = 78, 308

    def __init__(self):
        self.available = False
        self.reason = ""
        self._mesh = None
        try:
            import mediapipe as mp
            self._mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False, max_num_faces=4, refine_landmarks=False,
                min_detection_confidence=0.5, min_tracking_confidence=0.5)
            self.available = True
        except Exception as e:
            self.reason = f"mediapipe unavailable ({type(e).__name__})"

    def unavailable_message(self) -> str:
        return ("Face state is switched on for this company but no face-landmark model is installed "
                f"on this machine — {self.reason or 'unknown reason'}. Install it with:  "
                "pip install mediapipe   (nothing else here needs it; posture, movement and "
                "coverage all run without it.)")

    def mouth_ratios(self, frame):
        """[(centre_x, centre_y, ratio)] per visible face, normalized. Never raises."""
        if not self.available or frame is None:
            return []
        try:
            import cv2
            res = self._mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        except Exception:
            return []
        out = []
        for face in (getattr(res, "multi_face_landmarks", None) or []):
            try:
                lm = face.landmark
                top, bot = lm[self.LIPS_TOP], lm[self.LIPS_BOTTOM]
                left, right = lm[self.MOUTH_LEFT], lm[self.MOUTH_RIGHT]
                width = ((right.x - left.x) ** 2 + (right.y - left.y) ** 2) ** 0.5
                if width <= 1e-6:
                    continue
                height = ((bot.x - top.x) ** 2 + (bot.y - top.y) ** 2) ** 0.5
                # Ratio, then the landmarks go out of scope with this frame. Nothing is kept.
                out.append(((left.x + right.x) / 2.0, (top.y + bot.y) / 2.0, height / width))
            except Exception:
                continue
        return out


class ActivityAccumulator:
    """Per-track observations for one camera, rolled into buckets and handed to the outbox.

    Keeps ONLY counters. A track's history here is "how many samples looked like sitting", never a
    frame, a crop or a position trail — so even the in-memory state of a running analyzer holds
    nothing that could reconstruct what somebody did.

    NO NAMES ANYWHERE IN THIS CLASS, and none in what it emits. The analyzer cannot know which
    employee a track is and must not appear to: the server attributes a bucket from the time clock,
    and a name in this payload would be ignored there anyway. See ingest.normalize_batch.
    """

    def __init__(self, bucket_seconds=900, sample_seconds=2.0):
        self.bucket_seconds = max(60, int(bucket_seconds or 900))
        self.sample_seconds = float(sample_seconds or 2.0)
        self.bucket_key = None
        self.tracks = {}                    # track_key -> {"obs": [...], "mar": [...]}
        self.window_started = None
        self.staff_seconds = 0.0
        self.customer_seconds = 0.0
        self.peak_people = 0

    def _bucket_of(self, now_utc):
        """The wall-clock bucket this instant falls in, floored to the bucket size.

        Floored to the HOUR boundary rather than to process start, so two analyzers covering the same
        store agree on where a bucket begins and the server's unique index actually dedupes them."""
        epoch = int(now_utc.timestamp())
        start = epoch - (epoch % self.bucket_seconds)
        return datetime.fromtimestamp(start, tz=timezone.utc)

    def observe(self, track_key, posture, motion, with_person, mar=None):
        t = self.tracks.setdefault(track_key, {"obs": [], "mar": []})
        t["obs"].append({"posture": posture, "motion": motion, "with_person": with_person})
        if mar is not None:
            t["mar"].append(mar)

    def note_floor(self, people, dt):
        """People on the floor over dt seconds. Staff-vs-customer is NOT knowable from the picture —
        the detector has one class — so the convention the server documents is used: any person on
        the floor makes it staffed for coverage purposes, and the caveat travels with the number."""
        if dt <= 0:
            return
        self.peak_people = max(self.peak_people, people)
        if people > 0:
            self.staff_seconds += dt
            if people > 1:
                self.customer_seconds += dt

    def maybe_flush(self, now_utc, device_name, outbox, face_on):
        """Emit the finished bucket, if the clock has moved past it. Returns True when it did."""
        key = self._bucket_of(now_utc)
        if self.bucket_key is None:
            self.bucket_key, self.window_started = key, now_utc
            return False
        if key == self.bucket_key:
            return False
        started, ended = self.bucket_key, key
        window = max(0.0, (ended - started).total_seconds())
        for track_key, t in self.tracks.items():
            if not t["obs"]:
                continue
            ev = {"kind": "activity", "device_name": device_name,
                  "bucket_start": started.isoformat(),
                  "track_key": track_key, "sample_seconds": self.sample_seconds,
                  "observations": t["obs"]}
            if face_on and t["mar"]:
                ev["wide_mouth_episodes"] = ACT.yawn_events(t["mar"], self.sample_seconds)
            outbox.append(ev)
        outbox.append({"kind": "coverage", "device_name": device_name,
                       "bucket_start": started.isoformat(),
                       "window_seconds": round(window, 1),
                       "staff_seconds": round(self.staff_seconds, 1),
                       "customer_seconds": round(self.customer_seconds, 1),
                       "peak_people": self.peak_people})
        self.tracks.clear()
        self.staff_seconds = self.customer_seconds = 0.0
        self.peak_people = 0
        self.bucket_key, self.window_started = key, now_utc
        return True


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Frame sources — where pixels come from
#
# Two transports, one interface. Everything downstream (tracking, line crossing, heat binning) is
# identical for both and stays exactly the code the harnesses already prove; only the way a frame is
# obtained differs. That separation is the point: a new transport must never be able to change what
# a crossing means.
#
#   RtspFrameSource    older wired Nest Cams and Dropcams, and anything still managed in the legacy
#                      Nest app. OpenCV opens the tokenized URL directly.
#   WebRtcFrameSource  every Nest camera sold since 2021, and any older camera that has been migrated
#                      into the Google Home app. Needs a real peer connection — see below.
# ══════════════════════════════════════════════════════════════════════════════════════════════
class LazyFrame:
    """A decoded frame that has NOT been converted to BGR yet.

    WHY THIS EXISTS. The loop reads from every camera on every tick — it has to, because on
    RTSP that is what keeps the decoder drained — but it only DETECTS at detect_fps. Converting
    YUV to a contiguous BGR array costs 3.0 CPU-ms at 1080p (measured, webrtc_loopback.py), and
    the shipped code paid it inside the WebRTC pump for every arriving frame: 30 x 3.0 = 91
    CPU-ms per second per camera, of which detection at 6 fps consumed one frame in five and
    threw the other four away. That is 73 CPU-ms/sec/camera bought and binned — about a fifth
    of a whole camera's budget, per camera.

    So the pump now stores the frame as it arrived and the conversion happens HERE, called only
    after the rate limiter has decided this tick will actually run a detection. Cached, because
    face state and the detector both want the same array.
    """

    __slots__ = ("_src", "_arr")

    def __init__(self, src):
        self._src = src          # an av.VideoFrame, or an already-converted ndarray
        self._arr = src if src is not None and not hasattr(src, "to_ndarray") else None

    def array(self):
        if self._arr is None and self._src is not None:
            self._arr = self._src.to_ndarray(format="bgr24")
        return self._arr


class FrameSource:
    """read() returns (ok, LazyFrame). ok=False means "nothing right now" — the caller decides
    whether that is a hiccup or a dead stream. The frame is deliberately NOT converted to BGR
    until somebody asks for the pixels; see LazyFrame."""

    def read(self):
        raise NotImplementedError

    def close(self):
        pass


class RtspFrameSource(FrameSource):
    def __init__(self, url):
        import cv2
        self._cap = cv2.VideoCapture(url)
        # A live stream must never buffer: a backed-up queue turns a door crossing into an event
        # timestamped a minute late, which quietly wrecks the hourly curve.
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self):
        if self._cap is None or not self._cap.isOpened():
            return False, None
        ok, frame = self._cap.read()
        # OpenCV already hands back BGR, so LazyFrame is a no-op wrapper here — it exists so
        # both transports present one interface to the loop.
        return ok, (LazyFrame(frame) if ok else None)

    def close(self):
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None


class WebRtcFrameSource(FrameSource):
    """Holds a WebRTC peer connection to Google and exposes the latest decoded frame.

    THREADING. aiortc is asyncio; the analyzer loop is a plain synchronous while-loop, and that loop
    is where the proven counting logic lives. Rather than make the whole analyzer async — which would
    mean re-testing every counting rule against a new execution model — the peer connection runs in a
    daemon thread with its own event loop and drops each decoded frame into a slot. The sync loop
    reads the slot. Frames are overwritten rather than queued, deliberately: for a live door count the
    newest frame is the only one worth having, and a queue would build latency under load until events
    landed minutes late.

    NEGOTIATION. Same handshake the browser does on the Live Cameras page, which is already proven
    server-side (harness_vision_sdm.py §4): build a complete offer, hand it to /vision/edge/stream,
    apply Google's answer. Two details are load-bearing and are the first things to check if a real
    camera refuses:
      * both an audio and a video transceiver must be present in the offer, recvonly. Google answers
        the m-lines it was offered, and an offer with video alone is rejected.
      * the offer must be fully ICE-gathered before it is sent. Google's API takes one complete SDP,
        not a trickle, so we wait for gathering to finish (bounded, so a stalled gather cannot hang
        the analyzer forever).

    UNVERIFIED AGAINST REAL HARDWARE. There is no Nest camera in the build environment, so this path
    is proven only against a fake peer (harness_vision_webrtc.py) — the offer shape, the frame slot,
    and the failure handling. Use `--probe` on site to confirm the real handshake in one command
    before running the analyzer for real.
    """

    ICE_GATHER_TIMEOUT = 8.0
    FRAME_STALE_AFTER = 10.0        # no frame for this long ⇒ treat the stream as dead and reopen

    def __init__(self, api, device_name, ice_servers=None):
        self.api = api
        self.device_name = device_name
        self.ice_servers = ice_servers or ["stun:stun.l.google.com:19302"]
        self.session = None
        self._frame = None
        self._frame_at = 0.0
        self._lock = threading.Lock()
        self._loop = None
        self._pc = None
        self._thread = None
        self._error = None
        self._ready = threading.Event()

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    def start(self, timeout=30.0):
        """Negotiate and begin receiving. Returns the stream session dict, or raises."""
        self._thread = threading.Thread(target=self._run, name=f"webrtc:{self.device_name[-12:]}",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.close()
            raise RuntimeError("timed out negotiating the WebRTC stream")
        if self._error:
            self.close()
            raise RuntimeError(self._error)
        return self.session

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._negotiate_and_pump())
        except Exception as e:                      # noqa: BLE001 — surfaced to start() verbatim
            self._error = f"{type(e).__name__}: {e}"
            self._ready.set()
        finally:
            # Cancel and DRAIN before closing. aiortc leaves an ICE-transport monitor running; a loop
            # closed out from under it logs "Task was destroyed but it is pending!" every time. On a
            # long analyzer run that reopens a stream whenever it goes quiet, that is one spurious
            # error line per reconnect, per camera, forever — noise that buries the log lines an
            # installer actually needs.
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass

    async def _negotiate_and_pump(self):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

        config = RTCConfiguration(iceServers=[RTCIceServer(urls=u) for u in self.ice_servers])
        pc = RTCPeerConnection(configuration=config)
        self._pc = pc

        # Both m-lines, receive-only. Google answers what it was offered; video alone is refused.
        # Same requirement as the browser path: Device Access wants an m=application line in the
        # offer. Without it the answer comes back and the media never starts, which looks exactly
        # like a camera that is offline. Nothing is ever sent on this channel.
        # audio, then video, then the data channel — Google requires the m-lines in exactly that
        # order and refuses the offer outright otherwise. Creation order is SDP order.
        pc.addTransceiver("audio", direction="recvonly")
        pc.addTransceiver("video", direction="recvonly")
        pc.createDataChannel("dataSendChannel")

        pumping = asyncio.Event()

        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                asyncio.ensure_future(self._pump(track, pumping))

        await pc.setLocalDescription(await pc.createOffer())
        await self._await_ice(pc)

        # The backend brokers this to Google and hands back the answer. It also records the session,
        # so "which machine watched this camera" stays answerable for an analyzer too.
        res = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self.api.call("POST", "stream", {
                "device_name": self.device_name,
                "offer_sdp": pc.localDescription.sdp,
            }))
        answer = res.get("answer_sdp")
        if not answer:
            raise RuntimeError("the backend returned no answer SDP for this camera")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

        self.session = res
        self._ready.set()
        await pumping.wait()          # held open until close() tears the connection down

    async def _await_ice(self, pc):
        """Block until ICE gathering completes, bounded. An offer sent mid-gather is incomplete and
        Google rejects it; waiting forever on a stalled gather would wedge the analyzer instead."""
        if pc.iceGatheringState == "complete":
            return
        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def _on_state():
            if pc.iceGatheringState == "complete":
                done.set()

        try:
            await asyncio.wait_for(done.wait(), timeout=self.ICE_GATHER_TIMEOUT)
        except asyncio.TimeoutError:
            log.debug("[%s] ICE gathering did not complete in %.0fs — sending what we have",
                      self.device_name, self.ICE_GATHER_TIMEOUT)

    async def _pump(self, track, pumping):
        """One decoded frame at a time into the slot. Overwrites, never queues."""
        try:
            while True:
                frame = await track.recv()
                # NOT converted here. The colour conversion is deferred to LazyFrame.array(),
                # which the loop calls only for the frames a detection will actually consume.
                # Converting in this thread cost 91 CPU-ms/sec/camera at 1080p30 and threw
                # four fifths of it away at detect_fps 6.
                with self._lock:
                    self._frame = frame
                    self._frame_at = time.time()
        except Exception as e:                              # track ended / connection dropped
            log.debug("[%s] video track ended: %s", self.device_name, type(e).__name__)
        finally:
            pumping.set()

    # ── the synchronous side ─────────────────────────────────────────────────────────────────
    def read(self):
        with self._lock:
            frame, at = self._frame, self._frame_at
        if frame is None:
            return False, None
        if time.time() - at > self.FRAME_STALE_AFTER:
            return False, None        # connection alive but silent ⇒ let the worker reopen
        return True, LazyFrame(frame)

    def close(self):
        pc, loop = self._pc, self._loop
        self._pc = None
        if pc is not None and loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(pc.close(), loop).result(timeout=5)
            except Exception:
                pass
        with self._lock:
            self._frame = None


def lower_priority() -> bool:
    """Drop this process below normal scheduling priority.

    THE POINT OF THIS ONE LINE: most stores already have a computer, and the cheapest deployment is
    to reuse it — but that computer is usually running the register. A camera feature that makes a
    cashier wait is worse than no camera feature, and it is the kind of harm that gets a whole system
    ripped out. At below-normal priority the OS hands the CPU to the till first and gives the
    analyzer only what is left over; a few dropped detections cost far less than a slow sale.

    Best-effort by design — an OS that refuses is not a reason to fail to start."""
    try:
        if os.name == "nt":
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            return bool(ctypes.windll.kernel32.SetPriorityClass(handle,
                                                                BELOW_NORMAL_PRIORITY_CLASS))
        os.nice(10)
        return True
    except Exception:
        return False


# Per-camera costs that a detector benchmark alone cannot see, in CPU-milliseconds. Measured
# on an Emerald-Rapids-class Xeon; see the throughput report for the scripts. They are stated
# as constants rather than measured on the candidate box because they are dominated by libav
# and aiortc rather than by the machine, and because a benchmark that needs a live camera is a
# benchmark nobody runs before buying the machine.
FRAME_COSTS_MS = {
    #                    rtp   decode   bgr
    "1080p": {"rtp": 2.8, "decode": 3.14, "bgr": 3.04},
    "720p": {"rtp": 1.8, "decode": 1.47, "bgr": 2.90},
}
PROCESS_OVERHEAD_MS = 65.0      # per camera per second: the Python loop and the frame handoff
USABLE_FRACTION = 0.93          # measured ceiling before cameras slipped in a real-time soak


def capacity(per_detection_s: float, detect_fps: float, headroom: float = 0.5,
             cores: int = None, res: str = "1080p", stream_fps: float = 30.0,
             webrtc: bool = True) -> dict:
    """How many cameras a machine of this measured speed can carry. PURE — proven offline.

    WHAT THIS USED TO GET WRONG, AND WHY IT MATTERED. The first version divided one core's
    detection rate by detect_fps and stopped there. Three things were missing and they pulled
    in opposite directions, so the error was not a safe one:

      * IT COUNTED ONE CORE. A detection timed at 44 ms of WALL CLOCK on four cores is not 44 ms
        of machine; it is up to 176 ms of CPU spread thin. Dividing wall latency by detect_fps
        silently assumed the rest of the machine was free, which on a 4-core box it is not.
      * IT IGNORED THE VIDEO. Receiving and decoding a 1080p30 stream costs ~180 CPU-ms every
        second per camera before a detector runs at all, and the analyzer pays it whether or not
        it looks at the frame. On a fast detector that is now the largest single line.
      * IT MEASURED RANDOM NOISE at 720p. Noise is unrepresentative for a detector's NMS stage
        and says nothing about decode at all.

    The result was a number that told an owner with a perfectly serviceable machine that it
    could carry "about 1 camera". This version prices a whole camera-second and divides by the
    whole machine.

    `headroom` is the fraction of the measured capacity deliberately offered. It exists because
    the common deployment is a computer that is already running the store's register, and
    because a synthetic benchmark on an idle machine always flatters it relative to a real shop
    at 5pm.
    """
    per = max(1e-9, float(per_detection_s))
    cores = cores or (os.cpu_count() or 1)
    target = max(0.5, float(detect_fps))
    f = FRAME_COSTS_MS.get(res, FRAME_COSTS_MS["1080p"])
    per_camera_ms = (
        (stream_fps * f["rtp"] if webrtc else 0.0)
        + stream_fps * f["decode"]
        + target * f["bgr"]                       # lazy conversion: only frames we detect on
        + target * per * 1000.0
        + target * 0.25                           # tracker + counting geometry
        + PROCESS_OVERHEAD_MS
    )
    budget = cores * 1000.0 * USABLE_FRACTION * max(0.0, min(1.0, headroom))
    cams = budget / per_camera_ms
    # If it cannot carry one camera at the requested rate, the honest alternative is a slower
    # rate — reported so the operator has a number to try rather than "buy a better machine".
    fixed = per_camera_ms - target * (per * 1000.0 + f["bgr"] + 0.25)
    per_detect = per * 1000.0 + f["bgr"] + 0.25
    max_fps_one = max(0.0, (budget - fixed) / per_detect) if per_detect > 0 else 0.0
    return {
        "ms_per_detection": round(per * 1000, 1),
        "cores": cores,
        "cpu_ms_per_camera_second": round(per_camera_ms, 1),
        "detect_fps": target,
        "cameras": int(cams),
        "cameras_exact": round(cams, 1),
        "max_fps_for_one_camera": round(max_fps_one, 1),
    }


def benchmark(args) -> int:
    """Measure what THIS machine can actually do, and say how many cameras it can carry.

    "Can we reuse the PC we already have?" has no general answer — it depends on the box, the
    detector, and how many cameras that store has. Rather than guess, this times the real detector on
    real-sized frames and does the arithmetic. It needs no camera, no network and no credentials, so
    it can be run on a candidate machine before anything else is set up."""
    import numpy as np

    det = PersonDetector(prefer_yolo=not args.no_yolo,
                         no_openvino=getattr(args, "no_openvino", False),
                         ov_threads=getattr(args, "ov_threads", 1),
                         ov_precision=getattr(args, "ov_precision", None))
    if det.kind is None:
        log.error(det.unavailable_message())
        return 1

    # A 1080p frame with real structure in it. Random noise is what the first version used and
    # it is wrong twice over: it is unrepresentatively fast for HOG, and it produces almost no
    # detections, so NMS — a real part of the per-frame cost — never runs. A smooth gradient with
    # blocks is not a shop either, but it exercises the same code paths at the right size.
    rng = np.random.default_rng(7)
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:, :, 0] = np.linspace(0, 255, 1920, dtype=np.uint8)[None, :]
    frame[:, :, 1] = np.linspace(0, 255, 1080, dtype=np.uint8)[:, None]
    frame[::7, ::5] = rng.integers(0, 255, frame[::7, ::5].shape, dtype=np.uint8)

    runs = max(5, args.benchmark_runs)

    def time_it(d):
        """CPU-seconds per detection, not wall-clock. A detection that takes 44 ms of wall
        clock on four cores can cost 176 ms of machine, and capacity is spent in the second
        currency. Wall latency is reported too, because it is what a person watching a log
        sees, but it is not what the arithmetic uses."""
        for _ in range(3):                # warm up: the first call pays for lazy graph setup
            d(frame)
        c0, w0 = process_cpu_seconds(), time.perf_counter()
        for _ in range(runs):
            d(frame)
        wall = (time.perf_counter() - w0) / runs
        cpu = (process_cpu_seconds() - c0) / runs
        return cpu, wall

    log.info("detector: %s · warming up…", det.kind)
    per_detection, wall = time_it(det)
    cap = capacity(per_detection, args.detect_fps, res=args.benchmark_res,
                   stream_fps=args.benchmark_stream_fps)

    log.info("")
    log.info("  %.1f CPU-ms per detection  (%.1f ms of wall clock, using %.1f core%s)",
             cap["ms_per_detection"], wall * 1000, per_detection / max(wall, 1e-9),
             "" if per_detection / max(wall, 1e-9) < 1.5 else "s")
    log.info("  %d core%s on this machine", cap["cores"], "" if cap["cores"] == 1 else "s")
    log.info("  a %s camera at %.0f fps costs %.0f CPU-ms per second: receive + decode + "
             "%.1f detections", args.benchmark_res, args.benchmark_stream_fps,
             cap["cpu_ms_per_camera_second"], cap["detect_fps"])
    log.info("  at --detect-fps %.1f, with 50%% headroom left for the register:", cap["detect_fps"])
    log.info("")
    if cap["cameras"] >= 1:
        log.info("  ==> this machine can carry about %d camera%s  (%.1f before rounding)",
                 cap["cameras"], "" if cap["cameras"] == 1 else "s", cap["cameras_exact"])
    else:
        log.warning("  ==> NOT enough for one camera at %.1f fps.", cap["detect_fps"])
        log.warning("      Try --detect-fps %.1f, or use a faster machine. Below about 3 fps a fast "
                    "walker can cross the counting line between samples and go uncounted.",
                    cap["max_fps_for_one_camera"])

    # WHAT EMPLOYEE ACTIVITY COSTS, measured rather than asserted. Posture needs the pose weights,
    # which are a different model on the same frames — so the honest way to answer "can we afford to
    # turn this on" is to time both on this machine and print the two camera counts together. An
    # operator deciding on the feature should see the price next to it, not discover it afterwards
    # as a store box that fell behind.
    pose = PersonDetector(prefer_yolo=not args.no_yolo, pose=True)
    if pose.supports_pose:
        pcap = capacity(time_it(pose)[0], args.detect_fps, res=args.benchmark_res,
                        stream_fps=args.benchmark_stream_fps)
        log.info("")
        log.info("  WITH EMPLOYEE ACTIVITY (posture) switched on:")
        log.info("    %.1f CPU-ms per detection  ==> about %d camera%s  (%.0f%% of the above)",
                 pcap["ms_per_detection"], pcap["cameras"],
                 "" if pcap["cameras"] == 1 else "s",
                 100.0 * pcap["ms_per_detection"] / max(cap["ms_per_detection"], 0.001))
        log.info("    Movement, company and floor coverage cost nothing extra — they are computed "
                 "from tracks this machine already produces. Only POSTURE needs these weights.")
        if det.kind and "openvino" in det.kind and pose.kind and "openvino" not in pose.kind:
            # Say WHY the ratio is so ugly. Most of that multiple is the runtime, not the
            # posture model: the plain detector above is running on OpenVINO and the pose
            # weights are not, because only the detection head has been ported. Reported
            # plainly so nobody concludes that posture is six times the work — it is not, and
            # the gap would mostly close if the pose head were ported too.
            log.info("    NOTE: most of that multiple is the RUNTIME, not posture. The detector "
                     "above runs on OpenVINO (%s); the pose weights fall back to PyTorch, which "
                     "is several times dearer per detection on this machine whatever model it "
                     "is running.", det.kind)
    elif not args.no_yolo:
        log.info("")
        log.info("  Employee activity (posture) could not be timed — the pose weights did not load.")

    log.info("")
    log.info("  Detector in use: %s", det.kind)
    if det.kind == "opencv-hog":
        log.info("  This is the FALLBACK detector. It is slower AND less accurate than YOLO — it "
                 "misses seated and partly hidden people, so counts read low. Install ultralytics "
                 "and re-run this before deciding the machine is too slow.")
    log.info("  Run this on the actual store computer, while it is doing its normal work.")
    return 0


def webrtc_available():
    """True when aiortc is installed. Checked by spec rather than by importing, so a probe for the
    capability never drags a heavy dependency into a process that is not going to use it."""
    import importlib.util
    return importlib.util.find_spec("aiortc") is not None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# One camera's worker
# ══════════════════════════════════════════════════════════════════════════════════════════════
class CameraWorker:
    """Holds one camera's stream and turns it into events. Owns nothing persistent."""

    def __init__(self, api, cam, grid, tz_offset_minutes, detector, outbox, detect_fps=DETECT_FPS,
                 activity=None, face=None):
        self.api = api
        self.cam = cam
        self.grid = grid
        self.tz_offset = tz_offset_minutes
        self.detector = detector
        self.outbox = outbox
        self.face = face
        # Activity is per-camera because the switches are: a store can have one eye-level camera
        # marked posture_capable and three ceiling ones that are not.
        self.activity = (ActivityAccumulator(**activity) if activity else None)
        self.tracker = CNT.PredictiveTracker()
        self._gates = []
        self._gates_key = None
        self.session = None
        self.extend_at = 0
        self.source = None
        self.occupancy = defaultdict(float)
        self.last_flush = time.time()
        self.last_detect_at = None
        self._tz_warned = False
        self.detect_interval = 1.0 / max(0.5, float(detect_fps or DETECT_FPS))

    # ── stream lifecycle ─────────────────────────────────────────────────────────────────────
    def open(self):
        """Acquire a live stream for this camera, by whichever transport it speaks."""
        proto = (self.cam.get("stream_protocol") or "webrtc").lower()
        try:
            if proto == "rtsp":
                res = self.api.call("POST", "stream", {"device_name": self.cam["device_name"]})
                url = res.get("rtsp_url")
                if not url:
                    log.error("[%s] no RTSP url returned", self.cam["device_name"])
                    return False
                self.source = RtspFrameSource(url)
                self.session = res
            else:
                if not webrtc_available():
                    log.error("[%s] this camera streams over WebRTC but aiortc is not installed. "
                              "Run: pip install aiortc", self.cam["device_name"])
                    return False
                src = WebRtcFrameSource(self.api, self.cam["device_name"])
                self.session = src.start()      # negotiates; raises with a real reason on failure
                self.source = src
        except Exception as e:
            log.error("[%s] could not open the stream: %s", self.cam["device_name"], e)
            self.close()
            return False

        self.extend_at = time.time() + max(30, int((self.session or {}).get("extend_after_seconds") or 200))
        self.last_detect_at = None
        log.info("[%s] live (%s)", self.cam["device_name"], proto)
        return True

    def needs_extend(self) -> bool:
        """Whether maybe_extend() would actually send a command.

        Split out so the command budget is charged for a COMMAND rather than for a call: the loop
        asks every camera every pass, and metering the asking would drain a camera's bucket dry
        with requests that were never going to leave the process."""
        return bool(self.session) and time.time() >= self.extend_at

    def maybe_extend(self):
        """Google's grant lapses in ~5 minutes. Extend a minute early; a missed extension is not a
        retry, it drops the stream and costs a full re-negotiation."""
        if not self.needs_extend():
            return
        try:
            res = self.api.call("POST", "stream/extend", {"session_id": self.session.get("session_id")})
            self.extend_at = time.time() + max(30, int(res.get("extend_after_seconds") or 200))
            log.debug("[%s] stream extended", self.cam["device_name"])
        except Exception as e:
            log.warning("[%s] extension failed (%s) — reopening", self.cam["device_name"], e)
            self.close()

    def close(self):
        try:
            if self.source is not None:
                self.source.close()
        except Exception:
            pass
        self.source, self.session = None, None

    # ── the frame loop ───────────────────────────────────────────────────────────────────────
    def step(self):
        """Advance one camera by one tick. Returns True if a detection actually ran.

        DETECTION IS RATE-LIMITED, and that is what makes this runnable on a small store box.

        The naive loop — detect on every frame the source hands over — is wrong in two different
        ways depending on transport. On RTSP, `read()` blocks until the next frame, so detection runs
        at the camera's full 30 fps: five times more inference than the numbers need, on hardware
        that does not have it to spare. On WebRTC it is worse, because `read()` returns instantly
        from the frame slot — so the loop would spin at 100% CPU re-running the detector on the SAME
        frame over and over, producing no new information at all.

        DETECT_FPS caps it. Six per second is well above what the counting rules need: a person
        crossing a doorway is in the line's neighbourhood for roughly half a second to a second, so
        6 fps samples them 3–6 times on each side — plenty to establish a side change — and occupancy
        is accumulated in person-seconds, which is a rate, not a frame count. The frame is still READ
        every tick even when detection is skipped, because on RTSP that is what keeps the decoder
        drained and the picture live rather than minutes behind.
        """
        if self.source is None:
            return False
        ok, frame = self.source.read()
        if not ok:
            # WebRTC reports "no frame yet" the same way it reports a dead track, so a miss is only
            # fatal once the source has gone quiet past its own staleness window — which is exactly
            # what read() already folds in. One miss is a hiccup; a persistent one reopens.
            self._misses = getattr(self, "_misses", 0) + 1
            if self._misses > 300:
                log.warning("[%s] stream went quiet — reopening", self.cam["device_name"])
                self.close()
            return False
        self._misses = 0

        now = time.time()
        if self.last_detect_at and (now - self.last_detect_at) < self.detect_interval:
            return False                      # frame drained, NOT converted, inference skipped

        # Only now, past the rate limiter, is it worth turning this frame into pixels.
        frame = frame.array()
        if frame is None:
            return False

        # dt is time since the last DETECTION, not since the last frame — occupancy is measured in
        # person-seconds, so it must advance by the interval actually observed.
        dt = (now - self.last_detect_at) if self.last_detect_at else 0.0
        self.last_detect_at = now

        zones = self.cam.get("zones") or []
        lines = [z for z in zones if z.get("kind") == "line" and z.get("is_active", True)]

        gkey = tuple((z.get("id"), z.get("updated_at")) for z in lines)
        if gkey != self._gates_key:
            self._gates = CNT.gates_for(lines)
            self._gates_key = gkey

        tracks = self.tracker.update(self.detector(frame), now)

        # Mouth ratios for the whole frame, once, then matched to tracks by position. Computed only
        # when the tenant has face state on AND the model is present — see FaceState.
        ratios = self.face.mouth_ratios(frame) if (self.face and self.activity) else []

        # Every foot point in the frame, so "was this person near anybody" can be answered without
        # an O(n²) re-walk per track. Positions live for this tick only.
        feet = {}
        for track in tracks:
            feet[track["key"]] = GEO.foot_point(track["box"])

        on_floor = 0
        for track in tracks:
            foot = feet[track["key"]]
            if GEO.excluded(zones, foot):
                continue                            # the pavement / the back office is not the store
            on_floor += 1
            prev = track.get("prev_foot")
            # THE COUNT. A band with hysteresis, not a bare line test: a line has zero width, so
            # box jitter of any size crosses it, and a person pausing on the threshold produced an
            # in/out pair per wobble (measured: one person standing in the doorway counted 4 in and
            # 4 out). `confirmed` keeps a single-frame reflection in the glass out of the count.
            if self.cam.get("is_entrance") and track.get("confirmed", True):
                for gate in self._gates:
                    direction = gate.update(track["key"], foot, track.get("box"), now)
                    if direction:
                        self._emit_traffic(direction, track)

            if self.activity is not None and dt > 0:
                self._observe_activity(track, foot, prev, feet, dt, ratios)

            track["prev_foot"] = foot
            if self.cam.get("analytics") and dt > 0:
                cx, cy = GEO.grid_cell(foot, self.grid["cols"], self.grid["rows"])
                self.occupancy[(cx, cy)] += min(dt, 2.0)   # cap a stall so one hiccup is not an hour

        if self.activity is not None and dt > 0:
            self.activity.note_floor(on_floor, min(dt, 2.0))
            self.activity.maybe_flush(datetime.now(timezone.utc), self.cam["device_name"],
                                      self.outbox, bool(self.face and self.face.available))

        if now - self.last_flush >= SAMPLE_SECONDS:
            self._flush_presence()
        return True

    def _observe_activity(self, track, foot, prev, feet, dt, ratios):
        """One sample of what this track was doing. Every rule comes from app/modules/vision/
        activity.py — the same functions the server proves offline, imported rather than
        re-implemented, so the edge and the server can never drift on what "sitting" means."""
        # POSTURE only from a camera the operator marked eye-level. The rule reads standing vs
        # sitting out of image geometry, and an overhead camera foreshortens a standing thigh
        # exactly as sitting does. The server enforces this again on the way in — belt and braces,
        # because a stale analyzer is exactly the case this protects against.
        posture = "unknown"
        if self.cam.get("posture_capable") and track["box"].get("keypoints"):
            posture = ACT.classify_posture(track["box"]["keypoints"])

        motion = "unknown"
        if prev:
            moved = ((foot[0] - prev[0]) ** 2 + (foot[1] - prev[1]) ** 2) ** 0.5
            motion = ACT.classify_motion(moved, dt, self.cam.get("walk_speed") or 0.05)

        others = [p for k, p in feet.items() if k != track["key"]]
        with_person = ACT.near_another_person(foot, others,
                                              self.cam.get("engage_distance") or 0.12)

        # Match a mouth ratio to this track by containment in its box. A face that falls in no box,
        # or in two, is dropped rather than assigned to a guess — an episode counted against the
        # wrong track is worse than one not counted at all.
        mar = None
        if ratios:
            b = track["box"]
            inside = [r for r in ratios
                      if b["x"] <= r[0] <= b["x"] + b["w"] and b["y"] <= r[1] <= b["y"] + b["h"]]
            if len(inside) == 1:
                mar = inside[0][2]

        self.activity.observe(track["key"], posture, motion, with_person, mar)

    def _emit_traffic(self, direction, track):
        d, h = self._local_now()
        self.outbox.append({
            "kind": "traffic", "device_name": self.cam["device_name"], "direction": direction,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "local_date": d, "local_hour": h,
            "track_key": f"{self.cam['camera_id']}:{track['key']}:{int(track['first_seen'])}",
            "confidence": round(float(track.get("conf") or 0), 3)})
        log.info("[%s] customer %s", self.cam["device_name"], direction.upper())

    def _flush_presence(self):
        if self.occupancy:
            d, h = self._local_now()
            self.outbox.append({
                "kind": "presence", "device_name": self.cam["device_name"],
                "sampled_at": datetime.now(timezone.utc).isoformat(),
                "local_date": d, "local_hour": h,
                "cells": [{"x": x, "y": y, "occupancy": round(v, 2)}
                          for (x, y), v in self.occupancy.items() if v > 0]})
            self.occupancy.clear()
        self.last_flush = time.time()

    def _local_now(self):
        """The STORE's local date and hour — resolved from the STORE, not from this machine.

        The server sends each camera its store's IANA zone, which is what makes one analyzer able to
        serve stores in different timezones from a single location. Falling back to the --tz-offset
        flag keeps a lone in-store box working when the server has no zone recorded; falling back to
        UTC would file the 11pm rush under tomorrow for half the country.
        """
        now = datetime.now(timezone.utc)
        tz_name = (self.cam.get("timezone") or "").strip()
        if tz_name:
            try:
                from zoneinfo import ZoneInfo
                local = now.astimezone(ZoneInfo(tz_name))
                return local.date().isoformat(), local.hour
            except Exception:
                if not self._tz_warned:
                    log.warning("[%s] unknown timezone %r from the server — falling back to "
                                "--tz-offset %+d minutes", self.cam["device_name"], tz_name,
                                self.tz_offset)
                    self._tz_warned = True
        local = now + timedelta(minutes=self.tz_offset)
        return local.date().isoformat(), local.hour


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The process
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Google's documented ceilings for devices.executeCommand. Three of them apply at once, and an
# analyzer has to respect the tightest — which is NOT the per-device one.
#
#   API level      10 QPM per project, PER USER, across every device.
#   Command level   5 QPM per project, per user, per device.
#   Device level   30 QPM or 100 QPH per camera, aggregated ACROSS projects (battery protection).
#
# THE ACROSS-DEVICES CEILING IS THE ONE THAT BINDS, and it is worth being blunt about how this file
# got there. A first version metered the whole estate from a single bucket. A second replaced it
# with per-device buckets on the reasoning that Google's limit is per device and no estate-wide
# ceiling exists — which was wrong, and wrong in the dangerous direction: it let twenty-one cameras
# fire twenty-one commands in one pass of the loop against a ceiling of ten. Google's own worked
# example settles it: six devices at 5 QPM each "would result in 15 QPM for each user, when the
# devices.executeCommand API level rate limit for a project's user is 10 QPM."
#
# So BOTH gates are enforced. A command needs a token from its own device AND from the estate.
#
# WHAT THIS COSTS AT SCALE, because it is a real limit on how many cameras one Google account can
# carry rather than a tuning knob. Every open stream is extended roughly every 200 seconds, which is
# 0.3 commands per minute per camera, forever:
#
#     21 cameras -> 6.3 QPM, about 63% of the ceiling      (LuxeLink today)
#     27 cameras -> 8.1 QPM, the practical limit with room for opens and live view
#     33 cameras -> 9.9 QPM, extensions alone consume the entire budget
#
# Past roughly 27 cameras a second Google user — or a second Device Access project — is not an
# optimisation, it is the only way through.
# TWO NOTES ON THE PER-DEVICE NUMBER, so a later reader does not mistake either for a bug.
#
#   Google's 5 QPM is per TRAIT COMMAND, so GenerateWebRtcStream and ExtendWebRtcStream each get
#   their own 5 per device. This bucket lumps them together at 4, which is stricter than required.
#   That costs nothing — a camera holding a stream spends 0.3 commands a minute against it — and it
#   keeps the one case the device gate exists for: a camera stuck in a reopen loop cannot spend the
#   estate's budget on itself.
#
#   Those two commands are also the ONLY executeCommand calls this system makes. CameraEventImage
#   is granted write access in the Device Access console but GenerateImage is never called; if that
#   ever changes, it draws on the SAME 10 QPM as the stream commands and this ceiling must account
#   for it.
USER_QPM = 8.0            # of Google's 10, leaving room for a person pressing "Watch live"
DEVICE_QPM = 4.0          # of Google's 5-per-command, deliberately shared across both commands


class CommandBudget:
    """Token buckets over the commands this analyzer sends to Google — per device AND per user.

    WHY A BUDGET AND NOT A SLEEP. Two different things want to send an executeCommand — a camera
    opening its stream, and a camera extending a grant about to lapse — and they are not equally
    urgent. A missed extension drops the stream, costs a full renegotiation and puts a hole in that
    camera's count; a delayed open costs a few seconds of a camera coming up, which nobody notices.

    So extensions get a RESERVE that opens cannot touch, at both levels. An earlier version
    described exactly this and did not implement it: only opens were metered and extensions bypassed
    the budget entirely, so the priority it claimed to encode existed in neither direction.
    """

    # A tuple, so it can never collide with a device_name (always a string).
    USER_KEY = ("__estate__",)

    def __init__(self, per_minute=DEVICE_QPM, reserve=1.0, user_per_minute=USER_QPM):
        self.device_rate = max(0.5, float(per_minute)) / 60.0
        # A full minute of capacity, so a camera that has been quiet can open at once rather than
        # waiting out a bucket sized for half the rate.
        self.device_cap = max(1.0, float(per_minute))
        self.user_rate = max(0.5, float(user_per_minute)) / 60.0
        self.user_cap = max(1.0, float(user_per_minute))
        self.reserve = max(0.0, float(reserve))
        self._buckets = {}                       # key -> (tokens, last_seen)

    def _accrued(self, key, cap, rate, now):
        tokens, last = self._buckets.get(key, (cap, now))
        return min(cap, tokens + (now - last) * rate)

    def allow(self, key, cost=1.0, spend_reserve=False, now=None):
        """Spend one command against this device AND the estate. `spend_reserve` is the
        extension's privilege, and it applies at both levels — an extension that could pass the
        device gate and not the estate gate is still an extension that must not be dropped."""
        now = time.time() if now is None else float(now)
        floor = 0.0 if spend_reserve else self.reserve
        dev = self._accrued(key, self.device_cap, self.device_rate, now)
        usr = self._accrued(self.USER_KEY, self.user_cap, self.user_rate, now)
        ok = (dev - cost >= floor - 1e-9) and (usr - cost >= floor - 1e-9)
        # Write back either way so `last` advances and time keeps accruing; only spend when both
        # gates opened, because a command refused by one gate is never sent.
        self._buckets[key] = (dev - cost if ok else dev, now)
        self._buckets[self.USER_KEY] = (usr - cost if ok else usr, now)
        return ok


class Analyzer:
    def __init__(self, args):
        self.args = args
        self.api = Api(args.api, args.agent_key, args.secret)
        self.opens = CommandBudget(getattr(args, "open_qpm", DEVICE_QPM),
                                   user_per_minute=getattr(args, "user_qpm", USER_QPM))
        # The detector is built AFTER the first config poll, because whether activity is on decides
        # which weights to load. Until then there is nothing to detect on anyway — no camera has a
        # stream open before the first refresh_config().
        self.detector = None
        self.face = None
        self.pose_loaded = None
        self.outbox = []
        self.workers = {}
        self.config = None
        self.next_config = 0
        self.next_post = 0
        self.running = True

    def _ensure_models(self, want_pose, want_face):
        """Load (or reload) the models the tenant's current switches call for.

        Reloading on a switch change is deliberate. An operator who turns activity on expects posture
        within the poll interval, not at the next restart of a box nobody is standing next to; and
        one who turns it off expects the pose model to STOP, because the cheaper weights are why
        their camera count fits on that hardware.
        """
        if self.detector is None or self.pose_loaded != want_pose:
            self.detector = PersonDetector(prefer_yolo=not self.args.no_yolo, pose=want_pose,
                                           no_openvino=getattr(self.args, "no_openvino", False),
                                           ov_threads=getattr(self.args, "ov_threads", 1),
                                           ov_precision=getattr(self.args, "ov_precision", None))
            self.pose_loaded = want_pose
            if self.detector.kind is None:
                log.error(self.detector.unavailable_message())
            else:
                log.info("detector: %s", self.detector.kind)
            if want_pose and not self.detector.supports_pose:
                log.warning("Activity is switched on for this company but this machine fell back to "
                            "%s, which has no keypoints — movement, company and coverage will be "
                            "reported, posture will not.", self.detector.kind)
            # Existing workers hold a reference to the old detector; hand them the new one rather
            # than tearing their streams down, which would cost a full WebRTC re-negotiation each.
            for w in self.workers.values():
                w.detector = self.detector
        if want_face and self.face is None:
            self.face = FaceState()
            if not self.face.available:
                log.error(self.face.unavailable_message())
        elif not want_face and self.face is not None:
            self.face = None
            for w in self.workers.values():
                w.face = None

    def refresh_config(self):
        cfg = self.api.call("GET", "config")
        changed = (self.config or {}).get("features") != cfg.get("features")
        self.config = cfg
        self.next_config = time.time() + int(cfg.get("poll_seconds") or CONFIG_SECONDS)
        if changed:
            log.info("features now: %s", cfg.get("features"))
        feats = cfg.get("features") or {}
        want_activity = bool(feats.get("activity"))
        # Posture needs the pose weights, and it is the ONLY thing that does — a tenant running
        # activity with no eye-level camera keeps the cheaper detector and still gets movement,
        # company and coverage.
        want_pose = want_activity and any(c.get("posture_capable")
                                          for c in cfg.get("cameras") or [])
        self._ensure_models(want_pose, want_activity and bool(feats.get("face_state")))

        act_cfg = cfg.get("activity") or {}
        allowed = {c["device_name"]: c for c in cfg.get("cameras") or [] if c.get("analytics")}
        for name in list(self.workers):
            if name not in allowed:
                # The operator disabled this camera. Stop reading it HERE, not merely stop storing it.
                log.info("[%s] no longer permitted — releasing the stream", name)
                self.workers.pop(name).close()
        for name, cam in allowed.items():
            if name not in self.workers:
                self.workers[name] = CameraWorker(
                    self.api, cam, {"cols": (cfg.get("grid") or {}).get("cols") or 24,
                                    "rows": (cfg.get("grid") or {}).get("rows") or 16},
                    self.args.tz_offset, self.detector, self.outbox,
                    detect_fps=self.args.detect_fps,
                    activity=({"bucket_seconds": act_cfg.get("bucket_seconds") or 900,
                               "sample_seconds": act_cfg.get("sample_seconds") or 2.0}
                              if want_activity else None),
                    face=self.face)
            else:
                # A live worker follows the switches without dropping its stream.
                w = self.workers[name]
                w.cam = cam
                w.face = self.face
                if want_activity and w.activity is None:
                    w.activity = ActivityAccumulator(
                        bucket_seconds=act_cfg.get("bucket_seconds") or 900,
                        sample_seconds=act_cfg.get("sample_seconds") or 2.0)
                elif not want_activity:
                    w.activity = None

    def post(self):
        if not self.outbox:
            return
        batch, self.outbox = self.outbox[:1000], self.outbox[1000:]
        try:
            res = self.api.call("POST", "ingest", {"events": batch})
            if res.get("rejected"):
                # Never swallowed: the likeliest cause is an operator who turned audio on before
                # collecting consent, and they can only fix what they are told about.
                log.warning("server rejected: %s", res["rejected"])
            log.info("posted %d events (accepted %s)", len(batch), res.get("accepted"))
        except Exception as e:
            log.error("ingest failed (%s) — will retry", e)
            self.outbox = batch + self.outbox        # put it back; nothing is dropped on a blip
            del self.outbox[:-5000]                   # but the queue is bounded, oldest first

    def run(self) -> int:
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        hello = self.api.call("POST", "heartbeat", {"version": "1.0.0"})
        log.info("authenticated as %s (store %s); module enabled=%s",
                 hello.get("agent_key"), hello.get("store_code"), hello.get("enabled"))
        if self.detector.kind is None and not self.args.dry_run:
            # REFUSE rather than run. An analyzer with no detector opens every stream, burns the
            # bandwidth, posts nothing, and reports zero customers — which is indistinguishable from
            # a quiet store. Failing at startup, loudly, costs one minute; failing silently costs
            # however long it takes someone to disbelieve the dashboard.
            log.error(self.detector.unavailable_message())
            return 1
        if self.detector.kind == "opencv-hog":
            log.warning("Using the OpenCV HOG detector. It misses seated and heavily occluded "
                        "people, so counts will read LOW. Install ultralytics for production.")

        while self.running:
            try:
                if time.time() >= self.next_config:
                    self.refresh_config()
                if self.args.dry_run:
                    log.info("dry run — %d camera(s) would be analyzed", len(self.workers))
                    time.sleep(self.args.interval)
                    continue
                worked = False
                for w in list(self.workers.values()):
                    dev = w.cam.get("device_name") or w.cam.get("camera_id")
                    if w.source is None:
                        # METERED PER CAMERA, because that is the shape of Google's limit. A
                        # camera that has been sitting idle opens immediately; one that has just
                        # been retried several times waits for its own bucket, and neither holds
                        # the other twenty up.
                        if not self.opens.allow(dev):
                            continue
                        w.open()
                    else:
                        # An extension may spend the reserve an open may not: losing a grant costs
                        # a full renegotiation and a hole in that camera's count.
                        if w.needs_extend() and not self.opens.allow(dev, spend_reserve=True):
                            continue
                        w.maybe_extend()
                        worked = w.step() or worked
                if time.time() >= self.next_post:
                    self.post()
                    self.next_post = time.time() + POST_SECONDS
                if not self.workers:
                    time.sleep(self.args.interval)
                elif not worked:
                    # Every camera was rate-limited or waiting on a frame. Without this the WebRTC
                    # path busy-waits on its frame slot and pins a core for nothing.
                    time.sleep(0.02)
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.exception("loop error: %s", e)
                time.sleep(5)

        for w in self.workers.values():
            w.close()
        self.post()
        log.info("stopped")
        return 0

    def _stop(self, *_a):
        self.running = False


def probe(args):
    """Connect to ONE camera, grab a single frame, save it, and exit.

    Two jobs in one command. It is the cheapest possible proof that the whole chain works on site —
    agent secret, Google authorization, stream negotiation, decoding — without committing to a long
    analyzer run and reading logs. And the frame it writes is exactly the still needed to place the
    counting line and the exclusion zones, so the install visit produces that artifact instead of
    someone screenshotting a phone afterwards.
    """
    api = Api(args.api, args.agent_key, args.secret)
    cfg = api.call("GET", "config")
    cameras = cfg.get("cameras") or []
    if not cameras:
        log.error("No cameras are enabled for this analyzer. Assign a camera to store %s in "
                  "Vision -> Settings, and turn its Analytics switch on.", cfg.get("store_code"))
        return 1

    target = args.device or cameras[0]["device_name"]
    cam = next((c for c in cameras if c["device_name"] == target), None)
    if cam is None:
        log.error("Camera %s is not one this analyzer may use. Available:", target)
        for c in cameras:
            log.error("  %s", c["device_name"])
        return 1

    proto = (cam.get("stream_protocol") or "webrtc").lower()
    log.info("probing %s over %s", cam["device_name"], proto.upper())
    if proto != "rtsp" and not webrtc_available():
        log.error("This camera streams over WebRTC and aiortc is not installed. "
                  "Run: pip install aiortc")
        return 1

    worker = CameraWorker(api, cam, {"cols": 24, "rows": 16}, args.tz_offset,
                          PersonDetector(prefer_yolo=False), [], detect_fps=args.detect_fps)
    if not worker.open():
        return 1

    try:
        deadline = time.time() + args.probe_seconds
        frame = None
        while time.time() < deadline:
            ok, f = worker.source.read()
            if ok:
                frame = f.array()
                break
            time.sleep(0.2)
        if frame is None:
            log.error("Connected, but no video frame arrived within %ds. The stream negotiated and "
                      "then stayed silent — usually a codec or ICE path problem, not authorization.",
                      args.probe_seconds)
            return 1

        out = args.out or f"vision-probe-{cam['device_name'].rsplit('/', 1)[-1]}.png"
        try:
            import cv2
            cv2.imwrite(out, frame)
            h, w = frame.shape[:2]
            log.info("OK — %dx%d frame saved to %s", w, h, out)
            log.info("Send this image back to have the counting line and exclusion zones placed.")
        except Exception as e:
            log.warning("Frame received but could not be saved (%s). The stream itself works.", e)
        return 0
    finally:
        worker.close()


AGENT_VERSION = "1.0.0"
DEFAULT_CRED_FILE = os.path.expanduser("~/.metricspro/vision-agent.json")


def load_credentials(path):
    """What this machine was given when it enrolled. Absent or unreadable is not an error here —
    the caller reports it, with the enrollment command to run."""
    try:
        with open(path or DEFAULT_CRED_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_credentials(path, data):
    """Write the secret 0600, owner-only, and create the directory the same way.

    This file is the ONLY place the signing secret exists outside the database. It is deliberately
    not printed, not logged and not echoed: the entire point of enrollment is that no human ever
    holds this value, so writing it to a terminal would put us back where we started."""
    path = path or DEFAULT_CRED_FILE
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    # Create with the right mode from the start rather than chmod-ing after: a world-readable
    # instant is still an instant in which the secret was world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def enroll(args):
    """Trade the one-time code from the app for this machine's signing secret."""
    if not args.api:
        log.error("--api is required to enroll")
        return 2
    import requests
    url = args.api.rstrip("/") + "/api/v1/vision/edge/enroll"
    try:
        # Unsigned, and it has to be: the whole purpose of this call is to obtain the key that would
        # sign it. The code is the proof, which is why it is short-lived and single-use.
        res = requests.post(url, json={"code": args.enroll, "version": AGENT_VERSION}, timeout=30)
    except Exception as e:
        log.error("Could not reach %s: %s", url, e)
        return 1
    if res.status_code != 200:
        detail = ""
        try:
            detail = (res.json() or {}).get("detail") or ""
        except ValueError:
            pass
        log.error("Enrollment refused: %s", detail or f"HTTP {res.status_code}")
        return 1
    try:
        got = res.json() or {}
    except ValueError:
        log.error("The server returned something that is not JSON.")
        return 1
    if not got.get("secret") or not got.get("agent_key"):
        log.error("The server did not return credentials. Register the analyzer again.")
        return 1
    where = save_credentials(args.cred_file, {"api": args.api, "agent_key": got["agent_key"],
                                              "secret": got["secret"],
                                              "store_code": got.get("store_code")})
    # The agent key is a public identifier and is safe to show. The secret is not, and is not shown.
    log.info("Enrolled as %s%s. Credentials written to %s (owner-only).",
             got["agent_key"],
             f" for store {got['store_code']}" if got.get("store_code") else "", where)
    log.info("The code you used is now spent. Start the analyzer with:")
    log.info("  %s --api %s", sys.argv[0], args.api)
    return 0


def main():
    p = argparse.ArgumentParser(description="MetricsPro Vision edge analyzer")
    # Not `required` at the parser level: --benchmark deliberately needs NO credentials, so it can
    # be run on a candidate store PC before anyone has registered an analyzer or linked Google. The
    # requirement is enforced below, only for the modes that actually talk to the platform.
    p.add_argument("--api", default="", help="Platform base URL, e.g. https://api.example.com")
    p.add_argument("--enroll", default="",
                   help="One-time enrollment code from Vision → Settings. Trades the code for this "
                        "machine's signing secret, writes it owner-only, and exits. Run once.")
    p.add_argument("--cred-file", default="",
                   help=f"Where the enrolled credentials live (default {DEFAULT_CRED_FILE})")
    p.add_argument("--agent-key", default="",
                   help="Legacy: the va_… key. Normally read from the enrolled credentials file.")
    p.add_argument("--secret", default="",
                   help="Legacy: the signing secret. Prefer --enroll; a secret typed on a command "
                        "line lands in shell history.")
    p.add_argument("--tz-offset", type=int, default=0,
                   help="FALLBACK store offset from UTC in MINUTES (e.g. -420 for PDT), used only "
                        "when the server has no timezone recorded for a camera's store. Normally "
                        "the zone comes per-camera from the server, which is what lets ONE analyzer "
                        "serve stores in different timezones.")
    p.add_argument("--interval", type=float, default=2.0, help="Idle loop sleep, seconds")
    p.add_argument("--detect-fps", type=float, default=DETECT_FPS,
                   help=f"Detections per second per camera (default {DETECT_FPS}). This is the main "
                        "CPU dial: halving it roughly halves the load. Below ~3 a fast walker can "
                        "cross the counting line between samples and go uncounted.")
    p.add_argument("--no-yolo", action="store_true", help="Force the OpenCV HOG detector")
    p.add_argument("--dry-run", action="store_true",
                   help="Authenticate and fetch config, but open no stream. Use this to prove a "
                        "deployment before pointing it at a camera.")
    p.add_argument("--probe", action="store_true",
                   help="Connect to one camera, save a single frame, and exit. Run this FIRST on "
                        "site — it proves the whole chain in one command, and the image it writes "
                        "is the still needed to place the counting line.")
    p.add_argument("--device", default="",
                   help="With --probe: the camera to test. Defaults to the first one available.")
    p.add_argument("--out", default="", help="With --probe: where to write the frame.")
    p.add_argument("--probe-seconds", type=int, default=25,
                   help="With --probe: how long to wait for the first frame.")
    p.add_argument("--benchmark", action="store_true",
                   help="Measure this machine and report how many cameras it can carry. Needs no "
                        "camera, network or credentials — run it on a candidate store PC first.")
    p.add_argument("--benchmark-runs", type=int, default=20,
                   help="With --benchmark: how many detections to time.")
    p.add_argument("--benchmark-res", choices=("1080p", "720p"), default="1080p",
                   help="With --benchmark: the resolution the cameras will stream at. This "
                        "changes the answer a lot — receiving and decoding 1080p costs about "
                        "twice what 720p does, per camera, forever.")
    p.add_argument("--benchmark-stream-fps", type=float, default=30.0,
                   help="With --benchmark: the frame rate the cameras will DELIVER (not the "
                        "detection rate). Receive and decode are paid on every arriving frame.")
    p.add_argument("--no-openvino", action="store_true",
                   help="Do not use the OpenVINO detector even if it is installed. The "
                        "OpenVINO and PyTorch paths run the same yolov8n weights; use this to "
                        "compare them, or if you suspect the fast path of the two.")
    p.add_argument("--ov-threads", type=int, default=1,
                   help="Inference threads for the OpenVINO detector (default 1). More threads "
                        "make ONE detection finish sooner and cost the machine MORE CPU per "
                        "detection, so raise it only when a single camera cannot hold "
                        "--detect-fps on its own.")
    p.add_argument("--ov-precision", choices=("f32", "bf16", "auto"), default="f32",
                   help="The OpenVINO arithmetic (default f32). 'auto' hands the choice back "
                        "to OpenVINO, which on a Xeon with AMX means bfloat16 — roughly 2.7x "
                        "faster than fp32 and NOT the same arithmetic. Measured over 4 clips x 3 line "
                        "placements: fp32 counts identically to the PyTorch path on 12 of 12, "
                        "bf16 differs on 4 of 12 and always by counting one crossing too many. "
                        "Use f32 unless you have validated bf16 against your own footage; its "
                        "error is invented entries, which nothing downstream can undo.")
    p.add_argument("--open-qpm", type=float, default=DEVICE_QPM,
                   help="Commands per minute allowed to ONE camera (default %(default)s of "
                        "Google's documented 5 QPM per project, per user, per device). One camera "
                        "retrying hard cannot starve the others.")
    p.add_argument("--user-qpm", type=float, default=USER_QPM,
                   help="Commands per minute allowed across ALL cameras (default %(default)s of "
                        "Google's documented 10 QPM per project, per user). This is the ceiling "
                        "that actually binds: every open stream is extended about every 200 "
                        "seconds, so each camera costs 0.3 QPM forever — 21 cameras use 63%% of "
                        "the budget and past roughly 27 a second Google user is the only way "
                        "through. Lower this if you see throttling; raising it past 10 will not "
                        "help, because the limit is Google's.")
    p.add_argument("--priority", choices=("low", "normal"), default="low",
                   help="Scheduling priority (default low). Low keeps the register responsive when "
                        "the analyzer shares a machine with the point of sale.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    if args.benchmark:
        sys.exit(benchmark(args))
    if args.enroll:
        sys.exit(enroll(args))
    # Credentials come from the file this machine wrote when it enrolled. Flags still work for a
    # migration from the old copy-the-secret flow, but nobody should be typing a secret any more.
    if not (args.agent_key and args.secret):
        saved = load_credentials(args.cred_file)
        args.agent_key = args.agent_key or saved.get("agent_key", "")
        args.secret = args.secret or saved.get("secret", "")
    missing = [f"--{n.replace('_', '-')}" for n in ("api", "agent_key", "secret")
               if not getattr(args, n)]
    if missing:
        p.error("not enrolled on this machine, and " + ", ".join(missing) + " not given.\n"
                "Register the analyzer in Vision → Settings, then run:\n"
                f"  {sys.argv[0]} --api {args.api or '<api url>'} --enroll <code-from-the-app>")
    if args.priority == "low" and not lower_priority():
        log.debug("could not lower process priority; continuing at normal priority")
    if args.probe:
        sys.exit(probe(args))
    sys.exit(Analyzer(args).run() or 0)


if __name__ == "__main__":
    main()
