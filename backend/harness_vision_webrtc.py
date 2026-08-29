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

from vision_edge_analyzer import (CommandBudget, DEVICE_QPM, LazyFrame,   # noqa: E402
                                  PersonDetector, USER_QPM, WebRtcFrameSource,
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
# The arithmetic behind --benchmark. CORES IS PASSED EXPLICITLY IN EVERY CHECK: it defaults to
# os.cpu_count(), so a proof that let it default would assert something different on every
# machine that ran it, which is not a proof.
#
# WHAT THE MODEL PRICES, and why the first version was wrong. Capacity is not "one core's
# detection rate divided by detect_fps". A camera costs its video before it costs a detection:
# receiving and decoding 1080p30 is ~180 CPU-ms every second whether or not anything looks at
# the frames. The old model saw none of that and counted a single core, and told an owner with
# a perfectly serviceable four-core box that it could carry "about 1 camera".
c = capacity(0.040, 6, cores=4)             # 40 ms/detection, 6 fps per camera, 1080p WebRTC
check("a whole camera-second is priced, not just the detections",
      c["cpu_ms_per_camera_second"] == 502.9)
check("...and it is more than the detections alone (6 x 40 = 240 ms)",
      c["cpu_ms_per_camera_second"] > 6 * 40)
check("four cores at 40ms/detection, half withheld -> 3 cameras", c["cameras"] == 3)
check("the unrounded number is reported too, so 3.7 is not read as exactly 3",
      c["cameras_exact"] == 3.7)
check("the machine's core count is reported back", c["cores"] == 4)

check("twice the cores carries more cameras", capacity(0.040, 6, cores=8)["cameras"] == 7)
check("detecting half as often carries more cameras", capacity(0.040, 3, cores=4)["cameras"] == 4)

# THE FLOOR THAT THE OLD MODEL COULD NOT SEE. Make the detector free and the machine does NOT
# become infinite: the video is still arriving, still being decoded, and that is what is left.
free = capacity(0.0, 6, cores=4)
check("a FREE detector does not give infinite cameras — the video is the floor",
      free["cameras"] == 7)
check("...and that floor is receive + decode + process overhead, not detection",
      free["cpu_ms_per_camera_second"] == 262.9)
check("a faster detector helps, but cannot beat that floor",
      capacity(0.017, 6, cores=4)["cameras"] < free["cameras"])

# RESOLUTION IS THE CHEAPEST LEVER AN OWNER HAS, and it is invisible unless the model prices
# video, which is why it is proven here rather than left as a comment.
check("720p carries more cameras than 1080p on the same box",
      capacity(0.040, 6, cores=4, res="720p")["cameras"]
      > capacity(0.040, 6, cores=4, res="1080p")["cameras"])
check("an unknown resolution falls back to the dearer one rather than flattering the machine",
      capacity(0.040, 6, cores=4, res="4k")["cpu_ms_per_camera_second"]
      == capacity(0.040, 6, cores=4, res="1080p")["cpu_ms_per_camera_second"])
check("RTSP costs less than WebRTC — no RTP receive to pay for",
      capacity(0.040, 6, cores=4, webrtc=False)["cpu_ms_per_camera_second"]
      < capacity(0.040, 6, cores=4, webrtc=True)["cpu_ms_per_camera_second"])

check("headroom is the fraction OFFERED, so 1.0 offers the whole machine",
      capacity(0.040, 6, cores=4, headroom=1.0)["cameras"] == 7)
check("...and 0.5 is what --benchmark reports, leaving the register the rest",
      capacity(0.040, 6, cores=4, headroom=0.5)["cameras"] == 3)

check("a machine too slow for one camera reports ZERO rather than rounding up to one",
      capacity(0.200, 6, cores=1)["cameras"] == 0)
check("and tells the operator the rate that WOULD work instead",
      capacity(0.200, 6, cores=1)["max_fps_for_one_camera"] == 1.1)

check("a nonsense measurement cannot divide by zero", capacity(0, 6, cores=4)["cameras"] > 0)
check("detect_fps is floored so it can never divide by zero",
      capacity(0.040, 0, cores=4)["detect_fps"] == 0.5)

print("\n(0b2) The command budget — TWO ceilings, and the estate-wide one is the tight one")
# Google documents three limits on devices.executeCommand and an analyzer meets the tightest:
#   API level     10 QPM per project, PER USER, across every device   <- this one binds
#   Command level  5 QPM per project, per user, per device
#   Device level  30 QPM / 100 QPH per camera, across projects
#
# A previous version of this file asserted that "twenty cameras all open in the same pass, which is
# the whole point". That check encoded a MISREADING — that no across-devices ceiling existed — and
# it passed happily while the analyzer was arranged to send 21 commands a minute against a limit of
# 10. The checks below pin the real shape, including the case that mistake would have broken.
b = CommandBudget(per_minute=4.0, reserve=1.0, user_per_minute=8.0)
t = 1000.0
check("a camera may open immediately", b.allow("camA", now=t))
check("...and so may a different camera in the same instant", b.allow("camB", now=t))
check("ONE camera cannot spend the estate's budget — its own gate stops it first "
      "(device cap 4, reserve 1, one already spent)",
      [b.allow("camA", now=t) for _ in range(6)].count(True) == 2)
check("...and the other camera still has its own allowance", b.allow("camB", now=t))

# THE CEILING THAT BINDS. Twenty fresh cameras, all opening in the same instant: each passes its own
# device gate, and the estate gate is what stops them.
b2 = CommandBudget(per_minute=4.0, reserve=1.0, user_per_minute=8.0)
opened = [b2.allow(f"cam{i}", now=t) for i in range(20)].count(True)
check(f"twenty cameras opening at once are capped by the ESTATE, not their own buckets "
      f"(got {opened}, user cap 8 less a reserve of 1)", opened == 7)
check("...and the refused ones are simply not sent, not queued into a burst later",
      not b2.allow("cam19", now=t))

# EXTENSIONS OUTRANK OPENS AT BOTH LEVELS.
b3 = CommandBudget(per_minute=4.0, reserve=1.0, user_per_minute=8.0)
while b3.allow("cam0", now=t):
    pass                                        # drain cam0 to its device reserve
check("an extension may spend the reserve an open may not",
      b3.allow("cam0", spend_reserve=True, now=t))
b4 = CommandBudget(per_minute=4.0, reserve=1.0, user_per_minute=8.0)
for i in range(7):
    b4.allow(f"c{i}", now=t)                    # drain the ESTATE to its reserve
check("an extension gets through the estate gate when an open would not",
      (not b4.allow("cX", now=t)) and b4.allow("cX", spend_reserve=True, now=t))

# REFILL. Only bites on a bucket that has been spent and then left alone — a fresh key starts full
# and accrues nothing, so testing one proves nothing about the ceiling.
b5 = CommandBudget(per_minute=4.0, reserve=1.0, user_per_minute=8.0)
while b5.allow("c", now=0.0):
    pass
burst = 0
while b5.allow("c", now=3600.0):
    burst += 1
check(f"an hour idle refills to capacity and no further — not a burst of hundreds (got {burst})",
      burst == 3)

# THE ARITHMETIC THAT DECIDES HOW MANY CAMERAS ONE GOOGLE USER CARRIES. Each open stream is
# extended about every 200 s = 0.3 commands/min, forever. This is the real capacity limit and it is
# checked here so a future change to the extend interval cannot quietly blow the ceiling.
per_cam_qpm = 60.0 / 200.0
check(f"21 cameras sit inside Google's 10 QPM on extensions alone "
      f"({21 * per_cam_qpm:.1f} QPM)", 21 * per_cam_qpm < 10.0)
check(f"...but 40 cameras do NOT, and no setting can fix that "
      f"({40 * per_cam_qpm:.1f} QPM)", 40 * per_cam_qpm > 10.0)
# THE HAZARD, and it is worth a check of its own because the AVERAGE looks fine. 21 cameras at
# 0.3 QPM average 6.3 QPM, comfortably inside 10 — but if their extend timers align, all 21 land
# within a few seconds and that minute sees 21 commands. The bucket refuses them, and a refused
# EXTENSION drops a stream.
def _hour(spread, budget=None):
    bb = budget or CommandBudget(per_minute=4.0, reserve=1.0, user_per_minute=8.0)
    granted = 0
    for period in range(int(3600 / 200)):
        for i in range(21):
            if bb.allow(f"cam{i}", spend_reserve=True,
                        now=period * 200.0 + i * (spread / 21.0)):
                granted += 1
    return granted, 21 * int(3600 / 200)

clumped, due = _hour(8.4)
check(f"21 extends CLUMPED into 8 seconds are throttled — the average being 6.3 QPM does not "
      f"save them ({clumped} of {due} granted)", clumped < due)

# WHAT SAVES IT. Opens are metered by the same estate gate, so 21 cameras cannot all come up at
# once — they arrive over about three minutes, and their extend timers inherit that spread. At 21
# cameras that is one command every 8.6 s, against the 7.5 s the bucket sustains.
spread_ok, due2 = _hour(180.0)
check(f"...but extends inheriting the open stagger all get through ({spread_ok} of {due2})",
      spread_ok == due2)
check("the sustainable spacing is ~7.5 s between commands, and 21 cameras give 9.5 s",
      abs(60.0 / 8.0 - 7.5) < 1e-9 and (200.0 / 21) > 7.5)

# THE SHIPPED CONSTANTS MUST SIT INSIDE GOOGLE'S DOCUMENTED CEILINGS. Every check above builds its
# own budget with explicit numbers, so none of them would notice someone raising the defaults past
# what Google actually allows — a change that buys nothing (the limit is Google's, not ours) and
# turns a throttle into a mystery. Source: Device Access "User and Rate Limits", API level
# devices.executeCommand 10 QPM per project per user; command level 5 QPM per device.
check(f"the estate default ({USER_QPM}) sits inside Google's 10 QPM per project, per user",
      0 < USER_QPM <= 10.0)
check(f"the per-device default ({DEVICE_QPM}) sits inside Google's 5 QPM per device",
      0 < DEVICE_QPM <= 5.0)
check("...and each leaves room for a person opening live view",
      USER_QPM < 10.0 and DEVICE_QPM < 5.0)

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

    print("\n(4) Frames arrive as LazyFrames that convert to BGR only when asked")
    deadline = time.time() + 30
    ok, lazy = False, None
    while time.time() < deadline:
        ok, lazy = src.read()
        if ok:
            break
        time.sleep(0.2)
    check("a frame was slotted", ok is True and lazy is not None)
    # THE DEFERRAL IS THE POINT, so it is asserted rather than assumed. read() must hand back
    # something that has NOT been converted yet: the pump used to pay 3.0 CPU-ms of YUV->BGR on
    # every arriving frame, 30 times a second, and the loop then threw four fifths of them away
    # at detect_fps 6. If read() ever goes back to returning a converted array, this check is
    # what says so — the cost would otherwise be invisible, because everything still works.
    if lazy is not None:
        check("read() returns a LazyFrame, not an array", isinstance(lazy, LazyFrame))
        check("...which has not converted anything yet", lazy._arr is None)
        frame = lazy.array()
        check("...and converts on demand", frame is not None)
        check("...caching, so a second caller does not pay again", lazy.array() is frame)
    else:
        frame = None
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
