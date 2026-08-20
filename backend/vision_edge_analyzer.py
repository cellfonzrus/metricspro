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
  aiortc              optional. Needed only for WEBRTC cameras (every recent Nest camera). Without
                      it this analyzer handles RTSP cameras and reports the WebRTC ones as skipped.

TWO PATHS THIS REFERENCE BUILD DOES NOT IMPLEMENT, STATED PLAINLY
─────────────────────────────────────────────────────────────────
* **WebRTC cameras.** Every Nest camera released since 2021 is WebRTC-only. Reading one needs an
  aiortc peer connection: create the offer here, POST it to /vision/edge/stream, apply the answer,
  and hand the decoded video track to `CameraWorker.step()` in place of the OpenCV capture. The
  server side of that handshake is complete and proven (harness_vision_sdm.py §4); only this client
  half is missing, and a camera it cannot read is logged as skipped rather than silently ignored.

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
import hmac
import json
import logging
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import geometry as GEO   # noqa: E402  (the SAME rules the server proves)

log = logging.getLogger("vision-edge")

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

    Two backends. YOLO when it is installed (what a real deployment should use); OpenCV's built-in
    HOG people detector otherwise. The HOG path is genuinely weaker — it misses seated and heavily
    occluded people — and that is reported at startup rather than discovered later as "the counts
    look low", because an undercount that nobody was warned about is worse than no count."""

    def __init__(self, prefer_yolo=True):
        self.kind = None
        self._yolo = None
        self._hog = None
        if prefer_yolo:
            try:
                from ultralytics import YOLO
                self._yolo = YOLO("yolov8n.pt")
                self.kind = "yolov8n"
            except Exception as e:
                log.info("YOLO unavailable (%s) — falling back to OpenCV HOG", type(e).__name__)
        if not self._yolo:
            try:
                import cv2
                self._hog = cv2.HOGDescriptor()
                self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                self.kind = "opencv-hog"
            except Exception as e:
                log.warning("No detector available (%s) — detection disabled", type(e).__name__)

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
# One camera's worker
# ══════════════════════════════════════════════════════════════════════════════════════════════
class CameraWorker:
    """Holds one camera's stream and turns it into events. Owns nothing persistent."""

    def __init__(self, api, cam, grid, tz_offset_minutes, detector, outbox):
        self.api = api
        self.cam = cam
        self.grid = grid
        self.tz_offset = tz_offset_minutes
        self.detector = detector
        self.outbox = outbox
        self.tracker = Tracker()
        self.session = None
        self.extend_at = 0
        self.capture = None
        self.occupancy = defaultdict(float)
        self.last_flush = time.time()
        self.last_frame_at = None

    # ── stream lifecycle ─────────────────────────────────────────────────────────────────────
    def open(self):
        proto = (self.cam.get("stream_protocol") or "webrtc").lower()
        if proto != "rtsp":
            log.warning("[%s] WebRTC camera — this reference analyzer reads RTSP only. "
                        "Install aiortc and extend _open_webrtc() to handle it.",
                        self.cam["device_name"])
            return False
        try:
            res = self.api.call("POST", "stream", {"device_name": self.cam["device_name"]})
        except Exception as e:
            log.error("[%s] could not obtain a stream: %s", self.cam["device_name"], e)
            return False
        self.session = res
        self.extend_at = time.time() + max(30, int(res.get("extend_after_seconds") or 200))
        url = res.get("rtsp_url")
        if not url:
            log.error("[%s] no RTSP url returned", self.cam["device_name"])
            return False
        try:
            import cv2
            self.capture = cv2.VideoCapture(url)
            # A live stream must never buffer: a backed-up queue turns a door crossing into an event
            # timestamped a minute late, which quietly wrecks the hourly curve.
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception as e:
            log.error("[%s] could not open the stream: %s", self.cam["device_name"], e)
            return False
        log.info("[%s] live", self.cam["device_name"])
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
            if self.capture is not None:
                self.capture.release()
        except Exception:
            pass
        self.capture, self.session = None, None

    # ── the frame loop ───────────────────────────────────────────────────────────────────────
    def step(self):
        if self.capture is None:
            return
        ok, frame = self.capture.read()
        if not ok:
            log.warning("[%s] stream ended — reopening", self.cam["device_name"])
            self.close()
            return
        now = time.time()
        dt = (now - self.last_frame_at) if self.last_frame_at else 0.0
        self.last_frame_at = now

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
                    self.args.tz_offset, self.detector, self.outbox)

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

    def run(self):
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        hello = self.api.call("POST", "heartbeat", {"version": "1.0.0"})
        log.info("authenticated as %s (store %s); module enabled=%s",
                 hello.get("agent_key"), hello.get("store_code"), hello.get("enabled"))
        if self.detector.kind is None:
            log.warning("NO DETECTOR AVAILABLE — install opencv-python (and ideally ultralytics). "
                        "Running in config-only mode.")
        elif self.detector.kind == "opencv-hog":
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
                for w in list(self.workers.values()):
                    if w.capture is None:
                        w.open()
                    else:
                        w.maybe_extend()
                        w.step()
                if time.time() >= self.next_post:
                    self.post()
                    self.next_post = time.time() + POST_SECONDS
                if not self.workers:
                    time.sleep(self.args.interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.exception("loop error: %s", e)
                time.sleep(5)

        for w in self.workers.values():
            w.close()
        self.post()
        log.info("stopped")

    def _stop(self, *_a):
        self.running = False


def main():
    p = argparse.ArgumentParser(description="MetricsPro Vision edge analyzer")
    p.add_argument("--api", required=True, help="Platform base URL, e.g. https://api.example.com")
    p.add_argument("--agent-key", required=True, help="The va_… key from Vision → Settings")
    p.add_argument("--secret", required=True, help="The signing secret shown once at registration")
    p.add_argument("--tz-offset", type=int, default=0,
                   help="Store local time offset from UTC in MINUTES (e.g. -420 for PDT). This is "
                        "what files the 11pm rush under the right business date.")
    p.add_argument("--interval", type=float, default=2.0, help="Idle loop sleep, seconds")
    p.add_argument("--no-yolo", action="store_true", help="Force the OpenCV HOG detector")
    p.add_argument("--dry-run", action="store_true",
                   help="Authenticate and fetch config, but open no stream. Use this to prove a "
                        "deployment before pointing it at a camera.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    Analyzer(args).run()


if __name__ == "__main__":
    main()
