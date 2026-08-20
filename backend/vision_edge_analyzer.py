#!/usr/bin/env python3
"""MetricsPro Vision — the EDGE ANALYZER. Runs beside the store, holds the live camera feed, and
posts derived numbers to the platform.

    python3 backend/vision_edge_analyzer.py --api https://api.example.com \
        --agent-key va_xxxxxxxx --secret <the secret shown once at registration>

WHY THIS RUNS HERE AND NOT ON THE SERVER
────────────────────────────────────────
A Nest live-stream grant expires in about five minutes and must be re-negotiated; decoding video is
CPU-bound and continuous. A shared FastAPI process on Railway can do neither for a dozen cameras.
So the platform brokers the grant and stores the numbers, and THIS process — a small box in the
stockroom, or a container next to one — holds the stream and does the pixel work.

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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import geometry as GEO   # noqa: E402  (the SAME rules the server proves)

log = logging.getLogger("vision-edge")

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

    def __init__(self, prefer_yolo=True):
        self.kind = None
        self.reason = ""
        self._yolo = None
        self._hog = None
        if prefer_yolo:
            try:
                from ultralytics import YOLO
                self._yolo = YOLO("yolov8n.pt")
                self.kind = "yolov8n"
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
        if self._yolo is not None:
            out = []
            for r in self._yolo(frame, verbose=False, classes=[0]):     # class 0 = person
                for b in r.boxes:
                    x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                    out.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                                "conf": float(b.conf[0])})
            return out
        rects, weights = self._hog.detectMultiScale(frame, winStride=(8, 8), scale=1.05)
        return [{"x": x / w, "y": y / h, "w": bw / w, "h": bh / h,
                 "conf": float(weights[i]) if i < len(weights) else 0.5}
                for i, (x, y, bw, bh) in enumerate(rects)]


class Tracker:
    """A deliberately simple IoU tracker.

    Re-identification across occlusion is a hard problem and a solved-badly one at this scale; what
    the counting rules actually need is much weaker — a stable id for the few seconds a person takes
    to walk through a doorway. A track that is lost and re-acquired becomes a NEW track, which the
    server's visit pairing already handles (an unpaired entry is still a visit, an unpaired exit is
    counted as such). Choosing the simple tracker and making the server tolerant of it beats a
    fragile clever one whose failures would be invisible."""

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
class FrameSource:
    """read() returns (ok, frame_bgr). ok=False means "nothing right now" — the caller decides
    whether that is a hiccup or a dead stream."""

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
        return self._cap.read()

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
        pc.addTransceiver("audio", direction="recvonly")
        pc.addTransceiver("video", direction="recvonly")

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
                img = frame.to_ndarray(format="bgr24")     # BGR — what the detector expects
                with self._lock:
                    self._frame = img
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
        return True, frame

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


def capacity(per_detection_s: float, detect_fps: float, headroom: float = 0.5) -> dict:
    """How many cameras a machine of this measured speed can carry. PURE — proven offline.

    The analyzer detects sequentially across cameras in one loop, so the machine's total budget is
    `1 / per_detection` detections per second, and each camera claims `detect_fps` of them.

    `headroom` is the half of the measured capacity deliberately NOT offered. It exists because the
    common deployment is a computer that is already running the store's register, and because a
    synthetic benchmark on an idle machine always flatters it relative to a real shop at 5pm. Give
    away the whole measured number and the first busy Saturday turns into a slow till.
    """
    per = max(1e-9, float(per_detection_s))
    fps_ceiling = 1.0 / per
    usable = fps_ceiling * max(0.0, min(1.0, headroom))
    target = max(0.5, float(detect_fps))
    return {
        "ms_per_detection": round(per * 1000, 1),
        "fps_ceiling": round(fps_ceiling, 1),
        "usable_fps": round(usable, 1),
        "detect_fps": target,
        "cameras": int(usable // target),
        # If it cannot carry one camera at the requested rate, the honest alternative is a slower
        # rate — reported so the operator has a number to try rather than "buy a better machine".
        "max_fps_for_one_camera": round(usable, 1),
    }


def benchmark(args) -> int:
    """Measure what THIS machine can actually do, and say how many cameras it can carry.

    "Can we reuse the PC we already have?" has no general answer — it depends on the box, the
    detector, and how many cameras that store has. Rather than guess, this times the real detector on
    real-sized frames and does the arithmetic. It needs no camera, no network and no credentials, so
    it can be run on a candidate machine before anything else is set up."""
    import numpy as np

    det = PersonDetector(prefer_yolo=not args.no_yolo)
    if det.kind is None:
        log.error(det.unavailable_message())
        return 1

    # A 720p frame with some structure in it — a flat grey image is unrepresentatively fast for HOG,
    # which would produce a benchmark that flatters the machine.
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)

    log.info("detector: %s · warming up…", det.kind)
    for _ in range(3):
        det(frame)

    runs = max(5, args.benchmark_runs)
    started = time.perf_counter()
    for _ in range(runs):
        det(frame)
    per_detection = (time.perf_counter() - started) / runs

    cap = capacity(per_detection, args.detect_fps)

    log.info("")
    log.info("  %.1f ms per detection  (%.1f detections/sec at full tilt)",
             cap["ms_per_detection"], cap["fps_ceiling"])
    log.info("  at --detect-fps %.1f, with 50%% headroom left for the register:", cap["detect_fps"])
    log.info("")
    if cap["cameras"] >= 1:
        log.info("  ==> this machine can carry about %d camera%s", cap["cameras"],
                 "" if cap["cameras"] == 1 else "s")
    else:
        log.warning("  ==> NOT enough for one camera at %.1f fps.", cap["detect_fps"])
        log.warning("      Try --detect-fps %.1f, or use a faster machine. Below about 3 fps a fast "
                    "walker can cross the counting line between samples and go uncounted.",
                    cap["max_fps_for_one_camera"])
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

    def __init__(self, api, cam, grid, tz_offset_minutes, detector, outbox, detect_fps=DETECT_FPS):
        self.api = api
        self.cam = cam
        self.grid = grid
        self.tz_offset = tz_offset_minutes
        self.detector = detector
        self.outbox = outbox
        self.tracker = Tracker()
        self.session = None
        self.extend_at = 0
        self.source = None
        self.occupancy = defaultdict(float)
        self.last_flush = time.time()
        self.last_detect_at = None
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

    def maybe_extend(self):
        """Google's grant lapses in ~5 minutes. Extend a minute early; a missed extension is not a
        retry, it drops the stream and costs a full re-negotiation."""
        if not self.session or time.time() < self.extend_at:
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
            return False                      # frame drained, inference skipped

        # dt is time since the last DETECTION, not since the last frame — occupancy is measured in
        # person-seconds, so it must advance by the interval actually observed.
        dt = (now - self.last_detect_at) if self.last_detect_at else 0.0
        self.last_detect_at = now

        zones = self.cam.get("zones") or []
        lines = [z for z in zones if z.get("kind") == "line" and z.get("is_active", True)]

        for track in self.tracker.update(self.detector(frame), now):
            foot = GEO.foot_point(track["box"])
            if GEO.excluded(zones, foot):
                continue                            # the pavement / the back office is not the store
            prev = track.get("prev_foot")
            if prev and self.cam.get("is_entrance"):
                for line in lines:
                    direction = GEO.crossing_direction(line, prev, foot)
                    if direction:
                        self._emit_traffic(direction, track)
            track["prev_foot"] = foot
            if self.cam.get("analytics") and dt > 0:
                cx, cy = GEO.grid_cell(foot, self.grid["cols"], self.grid["rows"])
                self.occupancy[(cx, cy)] += min(dt, 2.0)   # cap a stall so one hiccup is not an hour

        if now - self.last_flush >= SAMPLE_SECONDS:
            self._flush_presence()
        return True

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
        """The STORE's local date and hour. The server trusts these because only this process knows
        the store's clock — and getting them from UTC would file the 11pm rush under tomorrow."""
        from datetime import timedelta
        local = datetime.now(timezone.utc) + timedelta(minutes=self.tz_offset)
        return local.date().isoformat(), local.hour


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The process
# ══════════════════════════════════════════════════════════════════════════════════════════════
class Analyzer:
    def __init__(self, args):
        self.args = args
        self.api = Api(args.api, args.agent_key, args.secret)
        self.detector = PersonDetector(prefer_yolo=not args.no_yolo)
        self.outbox = []
        self.workers = {}
        self.config = None
        self.next_config = 0
        self.next_post = 0
        self.running = True

    def refresh_config(self):
        cfg = self.api.call("GET", "config")
        changed = (self.config or {}).get("features") != cfg.get("features")
        self.config = cfg
        self.next_config = time.time() + int(cfg.get("poll_seconds") or CONFIG_SECONDS)
        if changed:
            log.info("features now: %s", cfg.get("features"))
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
                    detect_fps=self.args.detect_fps)

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
                    if w.source is None:
                        w.open()
                    else:
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
                frame = f
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


def main():
    p = argparse.ArgumentParser(description="MetricsPro Vision edge analyzer")
    # Not `required` at the parser level: --benchmark deliberately needs NO credentials, so it can
    # be run on a candidate store PC before anyone has registered an analyzer or linked Google. The
    # requirement is enforced below, only for the modes that actually talk to the platform.
    p.add_argument("--api", default="", help="Platform base URL, e.g. https://api.example.com")
    p.add_argument("--agent-key", default="", help="The va_… key from Vision → Settings")
    p.add_argument("--secret", default="", help="The signing secret shown once at registration")
    p.add_argument("--tz-offset", type=int, default=0,
                   help="Store local time offset from UTC in MINUTES (e.g. -420 for PDT). This is "
                        "what files the 11pm rush under the right business date.")
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
    p.add_argument("--priority", choices=("low", "normal"), default="low",
                   help="Scheduling priority (default low). Low keeps the register responsive when "
                        "the analyzer shares a machine with the point of sale.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    if args.benchmark:
        sys.exit(benchmark(args))
    missing = [f"--{n.replace('_', '-')}" for n in ("api", "agent_key", "secret")
               if not getattr(args, n)]
    if missing:
        p.error("required for this mode: " + ", ".join(missing))
    if args.priority == "low" and not lower_priority():
        log.debug("could not lower process priority; continuing at normal priority")
    if args.probe:
        sys.exit(probe(args))
    sys.exit(Analyzer(args).run() or 0)


if __name__ == "__main__":
    main()
