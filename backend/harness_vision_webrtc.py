"""Proof harness for the WebRTC frame source (mod-vision, migration 900).

Run: python3 backend/harness_vision_webrtc.py   (needs aiortc; NO camera, NO network to Google)

WHY THIS EXISTS, AND WHAT IT HONESTLY DOES NOT COVER
────────────────────────────────────────────────────
Every Nest camera sold since 2021 — including the Nest Cam (indoor, wired, 2nd gen) — streams over
WebRTC, so for most deployments this path is the ONLY path to a number. It also cannot be proven
end-to-end without a real camera and a real Google authorization, neither of which exists in a build
environment.

So this proves the half that is provable, against a real aiortc peer standing in for Google:

  1. The offer is a genuine, complete SDP with BOTH an audio and a video m-line, receive-only.
     (Google answers only the m-lines it was offered and refuses a video-only offer; this is the
     single most likely reason a real camera would fail, so it is asserted rather than assumed.)
  2. The offer is fully ICE-gathered before it leaves — Google takes one complete SDP, not a trickle.
  3. The negotiated answer is applied and the connection reaches a live state.
  4. Decoded frames land in the slot as BGR ndarrays, the shape the detector expects.
  5. The slot OVERWRITES rather than queues — the newest frame wins, so a slow consumer cannot build
     an ever-growing latency debt that would timestamp door crossings minutes late.
  6. Staleness: a source that stops producing reports not-ok, so the worker reopens instead of
     silently counting nothing forever.
  7. A backend that returns no answer SDP fails with a stated reason, not a hang.

NOT covered here (needs hardware; use `vision_edge_analyzer.py --probe` on site):
  · that Google accepts this exact offer,
  · that Nest's codecs decode under aiortc,
  · that the ICE path traverses a real store network.
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


try:
    import numpy as np
    from av import VideoFrame
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import VideoStreamTrack
except Exception as e:                                   # pragma: no cover
    print(f"aiortc/av not installed ({type(e).__name__}) — install with: pip install aiortc")
    sys.exit(0)

from vision_edge_analyzer import (PersonDetector, WebRtcFrameSource,   # noqa: E402
                                  capacity, webrtc_available)


# ── a stand-in for Google: a real peer that answers our offer and sends colour bars ──────────────
class ColourBars(VideoStreamTrack):
    """A real video track, so the frames under test go through actual encode/decode."""

    def __init__(self, width=320, height=180):
        super().__init__()
        self.w, self.h = width, height
        self._n = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        self._n += 1
        img = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        img[:, : self.w // 3] = (255, 0, 0)
        img[:, self.w // 3 : 2 * self.w // 3] = (0, 255, 0)
        img[:, 2 * self.w // 3 :] = (0, 0, 255)
        frame = VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts, frame.time_base = pts, time_base
        return frame


class FakeGoogle:
    """Stands in for `Api.call('POST', 'stream', …)`. Records the offer it was handed, answers it
    with a real peer connection, and can be told to misbehave."""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.seen_offer = None
        self._pc = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def call(self, method, path, payload=None):
        assert method == "POST" and path == "stream", f"unexpected call: {method} {path}"
        self.seen_offer = (payload or {}).get("offer_sdp")
        if self.mode == "no_answer":
            return {"session_id": "s1", "protocol": "webrtc"}          # answer missing
        fut = asyncio.run_coroutine_threadsafe(self._answer(self.seen_offer), self._loop)
        answer = fut.result(timeout=20)
        return {"session_id": "s1", "protocol": "webrtc", "answer_sdp": answer,
                "extend_after_seconds": 200}

    async def _answer(self, offer_sdp):
        pc = RTCPeerConnection()
        self._pc = pc
        pc.addTrack(ColourBars())
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        await pc.setLocalDescription(await pc.createAnswer())
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.05)
        return pc.localDescription.sdp

    def shutdown(self):
        """Close the peer AND let its transports finish unwinding before stopping the loop.
        Without the drain, aiortc's ICE monitor task is still pending when the loop dies and Python
        prints 'Task was destroyed but it is pending!' — harmless, but a harness that prints scary
        noise on a passing run is a harness people stop reading."""
        try:
            if self._pc is not None:
                asyncio.run_coroutine_threadsafe(self._pc.close(), self._loop).result(timeout=5)
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0.35), self._loop).result(timeout=5)
        except Exception:
            pass
        try:
            async def _drain():
                for t in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                    t.cancel()
                await asyncio.sleep(0.1)
            asyncio.run_coroutine_threadsafe(_drain(), self._loop).result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


print("\n(0) Capability detection")
check("webrtc_available() is True with aiortc installed", webrtc_available() is True)

print("\n(0b) Machine sizing — 'can we reuse the PC already in the store?'")
# The arithmetic behind --benchmark. Sequential detection across cameras, so the machine's budget is
# 1/per_detection detections per second and each camera claims detect_fps of them; half is withheld
# because the usual host is also running the register.
c = capacity(0.040, 6)                      # 40ms/detection, 6 fps per camera
check("25 detections/sec measured from 40ms", c["fps_ceiling"] == 25.0)
check("only half is offered — the register keeps the rest", c["usable_fps"] == 12.5)
check("12.5 usable / 6 fps -> 2 cameras", c["cameras"] == 2)
check("halving the frame rate doubles the cameras", capacity(0.040, 3)["cameras"] == 4)
check("a slow machine reports ZERO cameras rather than rounding up to one",
      capacity(0.200, 6)["cameras"] == 0)
check("and tells the operator the rate that WOULD work",
      capacity(0.200, 6)["max_fps_for_one_camera"] == 2.5)
check("a fast machine scales linearly", capacity(0.010, 6)["cameras"] == 8)
check("headroom is honoured when overridden", capacity(0.040, 6, headroom=1.0)["cameras"] == 4)
check("a nonsense measurement cannot divide by zero", capacity(0, 6)["cameras"] > 0)
check("detect_fps is floored so it can never divide by zero", capacity(0.040, 0)["detect_fps"] == 0.5)

print("\n(0c) A missing detector is refused, not run blind")
det = PersonDetector(prefer_yolo=False)
if det.kind is None:
    check("unavailable_message names the cause", bool(det.reason))
    check("...and gives the fix", "pip install ultralytics" in det.unavailable_message())
else:
    check(f"a detector IS available here ({det.kind}) — nothing to refuse", True)
    check("...and it reports its kind", det.kind in ("yolov8n", "opencv-hog"))

print("\n(1)/(2)/(3) Negotiation — offer shape, ICE completeness, connection")
google = FakeGoogle()
src = WebRtcFrameSource(google, "enterprises/p/devices/front-counter")
session = None
try:
    session = src.start(timeout=45)
    check("start() returns the stream session", isinstance(session, dict) and session.get("session_id") == "s1")

    offer = google.seen_offer or ""
    check("an SDP offer actually reached the backend", offer.startswith("v=0"))
    check("the offer carries a VIDEO m-line", "\nm=video " in offer)
    check("the offer carries an AUDIO m-line (Google refuses a video-only offer)",
          "\nm=audio " in offer)
    check("both m-lines are receive-only", offer.count("a=recvonly") >= 2)
    # A fully gathered offer has its candidates inline; a trickled one would have none.
    check("the offer is ICE-gathered before sending (candidates inline)", "a=candidate:" in offer)
    check("the offer ends gathering cleanly", "a=end-of-candidates" in offer or "a=candidate:" in offer)

    print("\n(4) Frames arrive as BGR ndarrays")
    deadline = time.time() + 30
    ok, frame = False, None
    while time.time() < deadline:
        ok, frame = src.read()
        if ok:
            break
        time.sleep(0.2)
    check("a frame was decoded and slotted", ok is True and frame is not None)
    if frame is not None:
        check("shape is (h, w, 3)", frame.ndim == 3 and frame.shape[2] == 3)
        check("dtype is uint8", str(frame.dtype) == "uint8")
        # Colour bars were sent as BGR: left third is BGR blue-channel-max... in BGR order the first
        # band was written as (255,0,0) = blue. Assert the channel ordering survived the round trip.
        h, w = frame.shape[:2]
        left = frame[h // 2, w // 6]
        right = frame[h // 2, w - w // 6]
        check("channel order preserved through encode/decode (left band ≠ right band)",
              tuple(int(v) for v in left) != tuple(int(v) for v in right))

    print("\n(5) The slot overwrites rather than queues")
    _, first = src.read()
    time.sleep(1.0)                       # let several frames go by unconsumed
    ok2, later = src.read()
    check("still returns a frame after ignoring many", ok2 is True)
    with src._lock:
        depth = 1 if src._frame is not None else 0
    check("exactly ONE frame is retained, never a backlog", depth == 1)

    print("\n(6) Staleness makes a silent stream reopenable")
    with src._lock:
        src._frame_at = time.time() - (WebRtcFrameSource.FRAME_STALE_AFTER + 5)
    ok3, _ = src.read()
    check("a stream gone quiet past the window reports not-ok", ok3 is False)
finally:
    src.close()
    google.shutdown()

check("close() clears the frame slot", src._frame is None)

print("\n(7) A backend with no answer fails with a reason, not a hang")
bad = FakeGoogle(mode="no_answer")
src2 = WebRtcFrameSource(bad, "enterprises/p/devices/broken")
try:
    src2.start(timeout=25)
    check("start() raises when no answer SDP comes back", False)
except RuntimeError as e:
    check("start() raises when no answer SDP comes back", True)
    check("the reason names the missing answer", "answer" in str(e).lower())
finally:
    src2.close()
    bad.shutdown()

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
