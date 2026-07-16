"""Proof for live-login-lowlat ROUND 2 (human-driven pre-auth: CDP screencast fast frames + instant input
+ captcha→human_action).  branch agent/commission/live-login-lowlat.

WHAT THIS COVERS (the round-2 additions on top of the diag-persist/pull-session work, which have their own
proofs — live_login_diag_persist_proof.py 27/27 and vidapay_pull_session_proof.py 30/30):

  1. frame_since / _store_frame — the monotonic seq contract the ~300ms /frame?since= poll relies on
     (a new JPEG ONLY when seq advanced; phase/message always fresh; empty/None frames ignored).
  2. input_event — the HIGH-priority queue (_hi_q drained before _cmd_q) + first input sets _human_driving
     (pauses auto-drive), and the back-compat click()/type_text() delegate to it.
  3. _do_input / _do_click — NORMALIZED (0..1) coords → live-viewport pixels (DPR-proof), clamped; type/key
     dispatch real key events; scroll uses the wheel.
  4. CDP screencast plumbing — _start_screencast (start + on-frame → buffer + pending ack), _flush_ack
     (acks on the worker thread), _stop_screencast, and the graceful fallback when CDP is unavailable.
  5. _pump — liveness guarantee (force a screenshot if the stream stalled > _FRAME_GUARANTEE_S; always
     capture in the no-CDP fallback).
  6. vidapay_sweep._looks_like_captcha — visible-widget AND human-text detection, never raises; and
     live_login._captcha_present swallows a driver that lacks the helper.
  7. _preauth_detect — DETECTS (never drives): proxy_error→error, authenticated→_on_authenticated (but NOT
     while still on the trust page), and the human_action ↔ awaiting_code/login captcha status upkeep.
  8. get_session tenant isolation (org mismatch → None) + router source-level wiring of the two new
     endpoints (/input validation + /frame?since= long-poll-lite; org_id a QUERY PARAM, not a constant).

Both live_login.py and vidapay_sweep.py are loaded BY FILE PATH (their top-level imports are stdlib only;
app + playwright imports are lazy), with _vp() monkeypatched to a FakeVP and playwright.sync_api shadowed,
so this runs with ZERO app/playwright/portal deps.  Router wiring is checked at the SOURCE level (importing
router.py would pull the whole app).

Run: python3 backend/scratchpad/live_login_lowlat_proof.py
"""
import importlib.util
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMM = os.path.join(_HERE, "..", "app", "modules", "commcalc")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_COMM, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mod = _load("ll_lowlat_target", "live_login.py")       # live_login.py
vp_mod = _load("vp_lowlat_target", "vidapay_sweep.py")  # vidapay_sweep.py (for the REAL _looks_like_captcha)

_ok = 0
_fail = 0


def check(name, cond):
    global _ok, _fail
    if cond:
        _ok += 1
        print("  ok   %s" % name)
    else:
        _fail += 1
        print("  FAIL %s" % name)


ROW = {"portal_url": "https://portal", "processor": "vidapay", "account_id": "A1",
       "username": "u", "password": "p", "proxy_url": None}


def new_session(persist=None, persist_shot=None):
    return mod.LiveLoginSession("sid1", "orgA", ROW, persist, persist_shot)


class Rec:
    def __init__(self):
        self.updates = []
        self.shots = []

    def persist(self, u):
        self.updates.append(dict(u))

    def persist_shot(self, s):
        self.shots.append(s)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[1] frame_since / _store_frame — the seq contract the ~300ms /frame?since= poll relies on")
s = new_session()
check("1a: fresh session seq is 0", s.snapshot_seq() == 0)
s._store_frame("FR1")
check("1b: _store_frame bumps seq to 1 + records the frame", s.snapshot_seq() == 1 and s._shot == "FR1")
f = s.frame_since(0)
check("1c: frame_since(0) after a new frame → changed True + data-uri JPEG",
      f["changed"] is True and f["seq"] == 1 and f["shot"] == "data:image/jpeg;base64,FR1")
f = s.frame_since(1)
check("1d: frame_since(seq) with nothing new → changed False, shot None (no JPEG shipped)",
      f["changed"] is False and f["shot"] is None and f["seq"] == 1)
check("1d: …but phase + message are ALWAYS present (status stays fresh without a frame)",
      "phase" in f and "message" in f)
before = s.snapshot_seq()
s._store_frame("")
s._store_frame(None)
check("1e: empty/None frames are ignored (seq unchanged — never ship a blank frame)",
      s.snapshot_seq() == before and s._shot == "FR1")
f = s.frame_since("not-an-int")
check("1f: a non-int `since` is tolerated (treated as 0) → still returns the newest frame",
      f["changed"] is True and f["shot"].startswith("data:image/jpeg;base64,"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[2] input_event — HIGH-priority queue (drained before SUBMIT_CODE) + first input = _human_driving")
s = new_session()
check("2a: a fresh session is NOT human-driving (auto-drive fast-path allowed)", s._human_driving is False)
s.submit("123456")                       # a normal command on the LOW-priority queue
s.input_event({"type": "click", "x": 0.5, "y": 0.5})
check("2b: the first human input flips _human_driving True (pauses auto-drive for the rest of pre-auth)",
      s._human_driving is True)
c1 = s._next_cmd()
c2 = s._next_cmd()
check("2c: _next_cmd drains the human INPUT before the queued SUBMIT_CODE (a click never waits behind a code)",
      c1[0] == "INPUT" and c2[0] == "SUBMIT_CODE" and c2[1] == "123456")
check("2c: the INPUT carries the forwarded event", c1[1].get("type") == "click" and c1[1].get("x") == 0.5)
s2 = new_session()
s2.click(0.1, 0.2)
q = s2._hi_q.get_nowait()
check("2d: back-compat click() delegates to input_event (→ _hi_q as a click)",
      q[0] == "INPUT" and q[1]["type"] == "click" and q[1]["x"] == 0.1 and q[1]["y"] == 0.2)
s2.type_text("hi")
q = s2._hi_q.get_nowait()
check("2e: back-compat type_text() delegates to input_event (→ _hi_q as a type)",
      q[0] == "INPUT" and q[1]["type"] == "type" and q[1]["text"] == "hi")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[3] _do_input / _do_click — normalized (0..1) coords → live-viewport pixels (DPR-proof) + input kinds")


class RecMouse:
    def __init__(self):
        self.moves = []
        self.clicks = []
        self.dblclicks = []
        self.wheels = []

    def move(self, x, y):
        self.moves.append((x, y))

    def click(self, x, y, **k):
        self.clicks.append((x, y))

    def dblclick(self, x, y, **k):
        self.dblclicks.append((x, y))

    def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class RecKeyboard:
    def __init__(self):
        self.typed = []
        self.pressed = []

    def type(self, t, delay=None):
        self.typed.append(t)

    def press(self, k):
        self.pressed.append(k)


class InputPage:
    def __init__(self, vw=1366, vh=900):
        self.viewport_size = {"width": vw, "height": vh}
        self.mouse = RecMouse()
        self.keyboard = RecKeyboard()
        self.url = "https://portal/x"

    def wait_for_timeout(self, *a, **k):
        pass

    def query_selector(self, *a, **k):
        return None


class InputVP:
    """A driver stand-in for the input path: screenshots succeed, page never classifies authenticated."""
    _TRUST_PAGE_WORDS = ("trust this device",)

    @staticmethod
    def _shot_b64(page):
        return "SHOT"

    @staticmethod
    def _wait_settle(page):
        pass

    @staticmethod
    def _page_text(page):
        return ""

    @staticmethod
    def _code_field(page):
        return None

    @staticmethod
    def _classify(page):
        return "login"


_saved_vp = mod._vp
mod._vp = lambda: InputVP        # _capture() uses _vp(); the vp ARG below is the same fake

pg = InputPage(1366, 900)
s = new_session()
s._do_input(pg, None, InputVP, {"type": "click", "x": 0.5, "y": 0.25})
check("3a: a click at (0.5,0.25) of the image → (683,225) on a 1366x900 live viewport",
      pg.mouse.clicks and pg.mouse.clicks[-1] == (683.0, 225.0))

pg2 = InputPage(1000, 800)
s._do_input(pg2, None, InputVP, {"type": "click", "x": 1.5, "y": -0.3})   # out of range
check("3b: out-of-range coords are CLAMPED to the viewport edges (1.5→1.0*W, -0.3→0.0)",
      pg2.mouse.clicks[-1] == (1000.0, 0.0))

pg3 = InputPage()
s._do_input(pg3, None, InputVP, {"type": "dblclick", "x": 0.5, "y": 0.5})
check("3c: type 'dblclick' routes to a real double-click (not a single click)",
      pg3.mouse.dblclicks and not pg3.mouse.clicks)

pg4 = InputPage()
s._do_input(pg4, None, InputVP, {"type": "type", "text": "Abc123"})
check("3d: type 'type' dispatches real keystrokes via keyboard.type", pg4.keyboard.typed == ["Abc123"])

pg5 = InputPage()
s._do_input(pg5, None, InputVP, {"type": "key", "key": "Enter"})
check("3e: type 'key' presses the named key via keyboard.press", pg5.keyboard.pressed == ["Enter"])

pg6 = InputPage()
s._do_input(pg6, None, InputVP, {"type": "scroll", "deltaY": 240})
check("3f: type 'scroll' forwards the wheel delta (0, deltaY)", pg6.mouse.wheels == [(0, 240.0)])

pg7 = InputPage()
before = s.snapshot_seq()
s._do_input(pg7, None, InputVP, {"type": "key", "key": "Tab"})
check("3g: every input takes a fresh frame afterwards (instant visual feedback → seq advances)",
      s.snapshot_seq() > before)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[4] CDP screencast plumbing — start / on-frame → buffer + ack / stop, and the no-CDP fallback")


class RecCDP:
    def __init__(self):
        self.sent = []
        self.handlers = {}
        self.detached = False

    def on(self, evt, fn):
        self.handlers[evt] = fn

    def send(self, method, params=None):
        self.sent.append((method, params))

    def detach(self):
        self.detached = True


class CtxCDP:
    def __init__(self, cdp, raise_new=False):
        self._cdp = cdp
        self._raise = raise_new

    def new_cdp_session(self, page):
        if self._raise:
            raise RuntimeError("CDP unavailable on this host")
        return self._cdp


class CDPPage:
    def __init__(self, ctx):
        self.context = ctx


cdp = RecCDP()
s = new_session()
s._start_screencast(CDPPage(CtxCDP(cdp)))
check("4a: _start_screencast turns the screencast ON when CDP is available", s._screencast_on is True and s._cdp is cdp)
check("4a: it sends Page.startScreencast as viewport-sized JPEG (format+quality set)",
      any(m == "Page.startScreencast" and (p or {}).get("format") == "jpeg" for m, p in cdp.sent))
check("4a: it subscribes to Page.screencastFrame", "Page.screencastFrame" in cdp.handlers)

cdp.handlers["Page.screencastFrame"]({"data": "CDPFRAME", "sessionId": 7})
check("4b: an incoming screencast frame lands in the shot buffer + bumps seq",
      s._shot == "CDPFRAME" and s.snapshot_seq() >= 1)
check("4b: the frame's sessionId is stashed for a deferred (worker-thread) ack", s._pending_ack == 7)

s._flush_ack()
check("4c: _flush_ack sends Page.screencastFrameAck for the newest frame + clears pending",
      any(m == "Page.screencastFrameAck" and (p or {}).get("sessionId") == 7 for m, p in cdp.sent)
      and s._pending_ack is None)

s._stop_screencast()
check("4d: _stop_screencast stops the cast + detaches the CDP session",
      any(m == "Page.stopScreencast" for m, p in cdp.sent) and cdp.detached is True
      and s._cdp is None and s._screencast_on is False)

s2 = new_session()
s2._start_screencast(CDPPage(CtxCDP(RecCDP(), raise_new=True)))
check("4e: CDP unavailable → screencast stays OFF (worker falls back to the screenshot loop)",
      s2._screencast_on is False and s2._cdp is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[5] _pump — liveness guarantee (force a screenshot if the stream stalled) + always-capture fallback")
# no-CDP fallback: _pump always takes a screenshot
s = new_session()
before = s.snapshot_seq()
s._screencast_on = False
s._pump(InputPage())
check("5a: no-CDP mode → _pump captures a frame every tick (fallback screenshot loop)",
      s.snapshot_seq() > before)

# CDP mode, fresh frame: _pump does NOT force a screenshot (screencast is carrying it)
s = new_session()
s._screencast_on = True
s._cdp = RecCDP()
s._last_frame_at = time.time()          # a frame just arrived
before = s.snapshot_seq()
s._pump(InputPage())
check("5b: CDP mode with a FRESH frame → _pump does NOT force an extra screenshot (seq steady)",
      s.snapshot_seq() == before)

# CDP mode, stalled: _pump forces a screenshot so the view never freezes
s = new_session()
s._screencast_on = True
s._cdp = RecCDP()
s._last_frame_at = time.time() - (mod._FRAME_GUARANTEE_S + 1.0)   # stream stalled
before = s.snapshot_seq()
s._pump(InputPage())
check("5c: CDP mode STALLED > _FRAME_GUARANTEE_S → _pump forces a screenshot (liveness guarantee)",
      s.snapshot_seq() > before)

mod._vp = _saved_vp                       # restore for the detection tests (they pass vp explicitly)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[6] captcha detection — vidapay_sweep._looks_like_captcha (real) + live_login._captcha_present guard")


class CapEl:
    def __init__(self, vis=True, raise_vis=False):
        self._vis = vis
        self._raise = raise_vis

    def is_visible(self):
        if self._raise:
            raise RuntimeError("detached")
        return self._vis


class CapFrame:
    def __init__(self, el=None):
        self._el = el

    def query_selector(self, sel):
        return self._el


class CapPage:
    def __init__(self, frames=None, content="", raise_frames=False):
        self._frames = frames or []
        self._content = content
        self._raise_frames = raise_frames

    @property
    def frames(self):
        if self._raise_frames:
            raise RuntimeError("no frames api")
        return self._frames

    def title(self):
        return ""

    def content(self):
        return self._content

    def query_selector(self, sel):
        return None


check("6a: a VISIBLE reCAPTCHA/hCaptcha/turnstile widget → captcha present",
      vp_mod._looks_like_captcha(CapPage(frames=[CapFrame(CapEl(vis=True))])) is True)
check("6b: a widget present but visibility unresolvable (is_visible raises) → treated as present",
      vp_mod._looks_like_captcha(CapPage(frames=[CapFrame(CapEl(raise_vis=True))])) is True)
check("6c: no widget but human-facing text ('I'm not a robot') → captcha present",
      vp_mod._looks_like_captcha(CapPage(frames=[CapFrame(None)], content="Please I'm not a robot check")) is True)
check("6d: an INVISIBLE widget + no human text → NOT flagged (reCAPTCHA v3 script-only must not false-positive)",
      vp_mod._looks_like_captcha(CapPage(frames=[CapFrame(CapEl(vis=False))], content="normal login")) is False)
check("6e: a clean login page → no captcha",
      vp_mod._looks_like_captcha(CapPage(frames=[CapFrame(None)], content="username password sign in")) is False)
check("6f: never raises even if page.frames blows up (falls back to the main frame)",
      vp_mod._looks_like_captcha(CapPage(raise_frames=True, content="clean")) is False)


class BoomVP:
    @staticmethod
    def _looks_like_captcha(page):
        raise RuntimeError("driver lacks the helper (old fake)")


check("6g: live_login._captcha_present swallows a driver missing/raising the helper → False (never crashes)",
      mod._captcha_present(BoomVP, object()) is False)
check("6h: live_login._captcha_present passes through a real True",
      mod._captcha_present(type("V", (), {"_looks_like_captcha": staticmethod(lambda p: True)}), object()) is True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[7] _preauth_detect — DETECTS state (never drives): proxy/auth/captcha status upkeep")


class DetectVP:
    _TRUST_PAGE_WORDS = ("trust this device",)
    SESSION_TTL_HOURS = 8

    def __init__(self):
        self.state = "login"
        self.captcha = False
        self.has_code = False
        self.text = ""

    def _classify(self, page):
        return self.state

    def _looks_like_captcha(self, page):
        return self.captcha

    def _code_field(self, page):
        return object() if self.has_code else None

    def _page_text(self, page):
        return self.text

    def _proxy_error_message(self, url, proxy):
        return "died at the egress proxy"

    def _shot_b64(self, page):
        return "SHOT"

    def capture_session_state(self, page, ctx):
        return {"cookies": [], "origins": []}


# proxy_error → error
vp = DetectVP()
vp.state = "proxy_error"
s = new_session()
s._set(phase="human_action")
s._preauth_detect(InputPage(), None, vp)
check("7a: _classify proxy_error → phase 'error' with the egress-proxy message",
      s.snapshot_phase() == "error" and "egress proxy" in s.state()["message"])

# authenticated (not on trust page, no code) → concludes auth + persists
vp = DetectVP()
vp.state = "authenticated"
vp.text = "welcome dashboard"
rec = Rec()
s = mod.LiveLoginSession("sid1", "orgA", ROW, rec.persist, rec.persist_shot)
s._set(phase="human_action")
s._preauth_detect(InputPage(), object(), vp)
check("7b: _classify authenticated (no trust page, no code) → _on_authenticated → phase 'authenticated'",
      s.snapshot_phase() == "authenticated")
check("7b: …and the durable session is persisted (auth_status='authenticated' + session_state)",
      any(u.get("auth_status") == "authenticated" and u.get("session_state") for u in rec.updates))

# authenticated-looking but STILL on the trust page → do NOT conclude auth (the 'Sign Out' link false-read)
vp = DetectVP()
vp.state = "authenticated"
vp.text = "please trust this device to continue"
s = new_session()
s._set(phase="verifying")
s._preauth_detect(InputPage(), object(), vp)
check("7c: authenticated classify but on the TRUST page → auth NOT concluded (stays non-authenticated)",
      s.snapshot_phase() != "authenticated")

# captcha appears while in 'login' → flip to human_action
vp = DetectVP()
vp.state = "login"
vp.captcha = True
s = new_session()
s._set(phase="login")
s._preauth_detect(InputPage(), None, vp)
check("7d: a captcha appearing during login → phase flips to 'human_action' (waits for the human)",
      s.snapshot_phase() == "human_action")

# captcha clears with a code box present → awaiting_code
vp = DetectVP()
vp.state = "login"
vp.captcha = False
vp.has_code = True
s = new_session()
s._set(phase="human_action")
s._preauth_detect(InputPage(), None, vp)
check("7e: captcha clears + a code box is present → phase 'awaiting_code'",
      s.snapshot_phase() == "awaiting_code")

# captcha clears, no code box → back to login
vp = DetectVP()
vp.state = "login"
vp.captcha = False
vp.has_code = False
s = new_session()
s._set(phase="human_action")
s._preauth_detect(InputPage(), None, vp)
check("7f: captcha clears + no code box → phase back to 'login' (finish the sign-in on screen)",
      s.snapshot_phase() == "login")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[8] tenant isolation + router source-level wiring of the two new endpoints")
s = new_session()      # org 'orgA'
with mod._SESSIONS_LOCK:
    mod._SESSIONS["sidX"] = s
    s.sid = "sidX"
check("8a: get_session returns the session for its OWN org", mod.get_session("sidX", "orgA") is s)
check("8b: get_session refuses a MISMATCHED org (cross-tenant isolation) → None",
      mod.get_session("sidX", "orgB") is None)
with mod._SESSIONS_LOCK:
    mod._SESSIONS.pop("sidX", None)

with open(os.path.join(_COMM, "router.py"), "r", encoding="utf-8") as fh:
    rsrc = fh.read()
check("8c: router defines POST /live-login/input", '@router.post("/data-sources/{sid}/live-login/input")' in rsrc)
check("8d: /input validates the type against click|dblclick|type|key|scroll",
      'if et not in ("click", "dblclick", "type", "key", "scroll")' in rsrc)
check("8e: /input is org-scoped via get_session(sid, org_id) (not the module constant)",
      "live_login.get_session(sid, org_id)" in rsrc and "sess.input_event(norm)" in rsrc)
check("8f: /input takes org_id as a QUERY PARAM (multi-tenant rule), not a Form/constant",
      "def live_login_input(sid: str, body: dict, org_id: str = ORG_ID)" in rsrc)
check("8g: router defines GET /live-login/frame with a `since` long-poll-lite param",
      '@router.get("/data-sources/{sid}/live-login/frame")' in rsrc
      and "def live_login_frame(sid: str, since: int = 0, org_id: str = ORG_ID)" in rsrc)
check("8h: /frame delegates to sess.frame_since(since) (returns a JPEG only when seq advanced)",
      "return sess.frame_since(since)" in rsrc)
check("8i: /frame with no live session → an idle payload (panel stops polling), not a 500",
      '{"seq": 0, "phase": "idle"' in rsrc)


print("\n==== %d ok, %d fail ====" % (_ok, _fail))
raise SystemExit(1 if _fail else 0)
