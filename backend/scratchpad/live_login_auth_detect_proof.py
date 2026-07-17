"""Proof for live-login-auth-detect — INCIDENT 3 (2026-07-17): the live login reached the REAL
authenticated T-CETRA Main Panel, but the phase stayed STUCK at awaiting_code because _classify kept
returning 'twofa'. branch agent/commission/live-login-auth-detect.

ROOT CAUSE: _classify scanned the raw HTML soup (_page_text = title + page.content()) which includes
hidden / <script> / comment text and any leftover-frame phrasing. The twofa branch ran BEFORE the auth
branch, so ANY lingering 2FA phrase ('we sent', 'enter the code', a stray 'otp' token) permanently pinned
the state at 'twofa' — the authenticated Main Panel (which matches auth_words) could never win.

FIX (precedence, encoded as the matrix below):
  STRUCTURAL first — a VISIBLE strict 2FA-code input (and no pw) → twofa; a password field → login.
  Then the word decision runs on the VISIBLE innerText (what the operator SEES), preferring the TOP frame
  (_main_frame_text) and falling back to ALL frames (_all_frames_text) — NOT the HTML/script soup. Per the
  matrix an AUTHENTICATED marker BEATS a 2FA marker.

MATRIX (one proof case each):
  1. visible strict code field + no pw ............................ twofa   (real code page keeps working)
  2. password field .............................................. login
  3. no code, no pw, auth words present + STALE 2FA words in a
     hidden/second frame ......................................... authenticated  (THE incident row)
  4. no code, no pw, NO auth words, twofa words present .......... twofa   (New Sign In → Next interstitial)
  5. trust-device page (auth words + 'trust this device') ....... _classify may say authenticated BUT
     _preauth_detect must NOT conclude auth while on the trust page (gate lives in _preauth_detect)
  6. squid/proxy page + bot-wall ................................. proxy_error / botwall (unchanged precedence)
  PLUS: the exact incident reproduction through a running _preauth_loop in awaiting_code → flips to
  authenticated and _on_authenticated persists the durable session (same as a normal flip).

Pure fakes only — vidapay_sweep.py + live_login.py loaded by file path (stdlib-only top-level imports),
_vp() monkeypatched. ZERO app/playwright/portal deps.

Run: python3 backend/scratchpad/live_login_auth_detect_proof.py
"""
import importlib.util
import os
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMM = os.path.join(_HERE, "..", "app", "modules", "commcalc")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_COMM, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


vp = _load("vp_authdetect_target", "vidapay_sweep.py")
mod = _load("ll_authdetect_target", "live_login.py")

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


# ── Fakes ────────────────────────────────────────────────────────────────────────────────────────
class FakeInput:
    def __init__(self, attrs, visible=True):
        self._attrs = attrs
        self._visible = visible

    def is_visible(self):
        return self._visible

    def get_attribute(self, k):
        return self._attrs.get(k)


class FakeFrame:
    """A child frame: exposes visible innerText via evaluate() + its own inputs."""
    def __init__(self, inner_text="", inputs=None):
        self._text = inner_text
        self._inputs = inputs or []

    def evaluate(self, js):
        return self._text                              # document.body.innerText

    def query_selector_all(self, sel):
        return list(self._inputs) if sel == "input" else []

    def query_selector(self, sel):
        if sel == "input[type=password]":
            for i in self._inputs:
                if (i.get_attribute("type") or "").lower() == "password":
                    return i
        return None


class FakePage:
    """Main frame + child frames. `main_text` = VISIBLE top-frame innerText; `frames_text` = each child
    frame's visible innerText; `content` = the raw HTML soup (what _page_text/_looks_like_* read)."""
    def __init__(self, main_text="", frames_text=None, inputs=None, title="", content="", url="https://portal/x"):
        self._main_text = main_text
        self._inputs = inputs or []
        self._title = title
        self._content = content
        self.url = url
        self._frames = [self] + [FakeFrame(t) for t in (frames_text or [])]

    # frame-view methods (the page acts as its own main frame)
    def evaluate(self, js):
        # _main_frame_text: title + body.innerText ; _all_frames_text (main): body.innerText
        return self._title + " " + self._main_text

    def query_selector_all(self, sel):
        return list(self._inputs) if sel == "input" else []

    def query_selector(self, sel):
        if sel == "input[type=password]":
            for i in self._inputs:
                if (i.get_attribute("type") or "").lower() == "password":
                    return i
        return None

    @property
    def frames(self):
        return self._frames

    def title(self):
        return self._title

    def content(self):
        return self._content


_MAIN_PANEL = ("Main Panel Account Manager Billing Manager Activation Manager MA Handset Ordering "
               "Welcome Sign Out")
_CODE_INPUT = FakeInput({"type": "text", "id": "otpCode", "name": "VerificationCode"})
_PW_INPUT = FakeInput({"type": "password", "id": "Password", "name": "Password"})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[1] MATRIX — one case per row (the classification constraint the fix is designed to)")

# Row 1: a VISIBLE strict code field + no password → twofa (real 2FA code-entry page keeps working)
p = FakePage(main_text="Enter the verification code we sent you", inputs=[_CODE_INPUT], content="code page")
check("row1: visible strict code field, no pw → twofa", vp._classify(p) == "twofa")

# Row 2: a password field → login
p = FakePage(main_text="Sign in Account ID User ID Password", inputs=[_PW_INPUT], content="login page")
check("row2: password field present → login", vp._classify(p) == "login")

# Row 3 (THE incident): no code field, no pw, auth words in the top frame, STALE '2FA' words only in a
# hidden/second frame → authenticated (auth beats the stale 2FA soup).
p = FakePage(main_text=_MAIN_PANEL, frames_text=["we sent you a code — enter the code"],
             inputs=[], content=_MAIN_PANEL + " <script>var otp='x'</script> we sent you a code")
check("row3: authenticated Main Panel + stale '2FA' words in a hidden frame → authenticated",
      vp._classify(p) == "authenticated")

# Row 4: no code field, no pw, NO auth words, twofa words present → twofa (New Sign In → Next interstitial)
p = FakePage(main_text="New Sign In. For your security, we sent a verification code. Next",
             inputs=[], content="interstitial")
check("row4: interstitial (no code box, no auth words, twofa words) → twofa",
      vp._classify(p) == "twofa")

# Row 6: squid/proxy page → proxy_error (checked FIRST, precedence unchanged)
p = FakePage(main_text="whatever", content="ERROR The requested URL could not be retrieved (squid) your cache administrator")
check("row6a: squid/proxy page → proxy_error (unchanged precedence)", vp._classify(p) == "proxy_error")

# Row 6: bot-wall with no password → botwall (unchanged precedence)
p = FakePage(main_text="attention required", inputs=[],
             content="Something doesn't look right verify you are human")
check("row6b: bot-wall (no pw) → botwall (unchanged precedence)", vp._classify(p) == "botwall")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[2] the fix mechanics — visible-text preference, top-frame vs all-frames fallback, auth-beats-2FA")

# stale 2FA phrase ONLY in the HTML soup (page.content), never in the VISIBLE text → NOT twofa
p = FakePage(main_text=_MAIN_PANEL, inputs=[],
             content=_MAIN_PANEL + " <!-- one-time --> <script>x='security code'</script>")
check("2a: a 2FA phrase buried only in HTML/script/comment soup is IGNORED (visible text wins) → authenticated",
      vp._classify(p) == "authenticated")

# app content rendered inside a CHILD frame (top frame has no auth/2FA marker) → all-frames fallback finds auth
p = FakePage(main_text="loading…", frames_text=[_MAIN_PANEL], inputs=[], content="frameset")
check("2b: Main Panel in a CHILD frame (top frame blank) → all-frames fallback → authenticated",
      vp._classify(p) == "authenticated")

# both auth + twofa VISIBLE in the top frame → auth still wins (matrix row-3 rule: auth beats 2FA)
p = FakePage(main_text=_MAIN_PANEL + " we sent you a code", inputs=[], content="x")
check("2c: auth + 2FA words BOTH visible in the top frame → authenticated (auth beats 2FA)",
      vp._classify(p) == "authenticated")

# nothing recognizable → unknown (unchanged tail)
p = FakePage(main_text="lorem ipsum", inputs=[], content="lorem ipsum")
check("2d: no code/pw/auth/2FA signal → unknown", vp._classify(p) == "unknown")

# _main_frame_text / _all_frames_text never raise
class BoomPage(FakePage):
    def evaluate(self, js):
        raise RuntimeError("no js")


bp = BoomPage(main_text="x", content="clean")
check("2e: _main_frame_text never raises (→ '' on evaluate failure)", vp._main_frame_text(bp) == "")
check("2f: _all_frames_text never raises", isinstance(vp._all_frames_text(bp), str))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[3] Row 5 — the trust-device page: _preauth_detect must NOT conclude auth while on it")

ROW = {"portal_url": "https://portal/main", "processor": "vidapay", "account_id": "A1",
       "username": "u", "password": "p", "proxy_url": None}


class DetectVP:
    """Wraps the REAL vidapay_sweep helpers but lets each test pin _classify's result + the page text the
    trust-gate reads. (We exercise _preauth_detect's gate, using the real _classify separately above.)"""
    _TRUST_PAGE_WORDS = vp._TRUST_PAGE_WORDS
    SESSION_TTL_HOURS = getattr(vp, "SESSION_TTL_HOURS", 8)

    def __init__(self, classify="authenticated", page_text="", code=False):
        self._classify_ret = classify
        self._page_text_ret = page_text
        self._code = code

    def _classify(self, page):
        return self._classify_ret

    def _page_text(self, page):
        return self._page_text_ret

    def _code_field(self, page):
        return object() if self._code else None

    def _looks_like_captcha(self, page):
        return False

    def _proxy_error_message(self, url, proxy):
        return "died at the egress proxy"

    def _shot_b64(self, page):
        return "SHOT"

    def capture_session_state(self, page, ctx):
        return {"cookies": [], "origins": []}


class Rec:
    def __init__(self):
        self.updates = []
        self.shots = []

    def persist(self, u):
        self.updates.append(dict(u))

    def persist_shot(self, s):
        self.shots.append(s)


_saved_vp = mod._vp
mod._vp = lambda: DetectVP()      # _capture uses _vp()._shot_b64
try:
    # On the trust page: _classify says authenticated, but _page_text contains a trust-page word → do NOT conclude.
    trust_text = "please trust this device to continue nickname next"
    tvp = DetectVP(classify="authenticated", page_text=trust_text, code=False)
    rec = Rec()
    s = mod.LiveLoginSession("sidT", "orgA", ROW, rec.persist, rec.persist_shot)
    s._set(phase="verifying")
    s._preauth_detect(object(), object(), tvp)
    check("5a: authenticated classify but ON the trust page → auth NOT concluded (stays non-authenticated)",
          s.snapshot_phase() != "authenticated")
    check("5a: …and NO durable session was persisted while on the trust page",
          not any(u.get("auth_status") == "authenticated" for u in rec.updates))

    # A code field still present also blocks the conclusion (defensive gate, unchanged).
    cvp = DetectVP(classify="authenticated", page_text="dashboard", code=True)
    s = mod.LiveLoginSession("sidC", "orgA", ROW, None, None)
    s._set(phase="awaiting_code")
    s._preauth_detect(object(), object(), cvp)
    check("5b: a lingering code field blocks the auth conclusion (unchanged gate)",
          s.snapshot_phase() != "authenticated")
finally:
    mod._vp = _saved_vp


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[4] LATE flip from awaiting_code → authenticated behaves like a normal flip (persist + message)")
mod._vp = lambda: DetectVP()
try:
    # Off the trust page, no code field, _classify authenticated → _on_authenticated persists + sets phase.
    avp = DetectVP(classify="authenticated", page_text="main panel account manager welcome", code=False)
    rec = Rec()
    s = mod.LiveLoginSession("sidA", "orgA", ROW, rec.persist, rec.persist_shot)
    s._set(phase="awaiting_code")            # the STUCK phase from the incident
    s._preauth_detect(object(), object(), avp)
    check("4a: awaiting_code + authenticated (off trust page) → phase flips to authenticated",
          s.snapshot_phase() == "authenticated")
    check("4b: …the durable session is persisted (auth_status='authenticated' + session_state + expiry)",
          any(u.get("auth_status") == "authenticated" and u.get("session_state")
              and u.get("session_expires_at") for u in rec.updates))
    check("4c: …with the same 'session is saved' message as a normal flip",
          "saved" in (s.state()["message"] or "").lower())
finally:
    mod._vp = _saved_vp


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[5] EXACT incident reproduction — a running _preauth_loop STUCK in awaiting_code flips + persists")
# Build a REAL LiveLoginSession whose _classify (via a fake vp using the REAL _classify over a FakePage that
# reproduces the incident: Main Panel auth text in the top frame + 'we sent you a code' in a second frame,
# NO pw, NO code field) drives the running detection loop to authenticated.


class IncidentVP:
    """Uses the REAL vp._classify / _main_frame_text / _all_frames_text over the incident FakePage, plus the
    minimal surface _preauth_loop / _preauth_detect / _on_authenticated need."""
    _TRUST_PAGE_WORDS = vp._TRUST_PAGE_WORDS
    SESSION_TTL_HOURS = getattr(vp, "SESSION_TTL_HOURS", 8)

    def _classify(self, page):
        return vp._classify(page)                     # THE REAL classifier

    def _page_text(self, page):
        return vp._page_text(page)                    # real (for the trust gate) — no trust words here

    def _code_field(self, page):
        return vp._code_field(page)                   # real — none on this page

    def _looks_like_captcha(self, page):
        return False

    def _proxy_error_message(self, url, proxy):
        return "died at the egress proxy"

    def _shot_b64(self, page):
        return "SHOT"

    def capture_session_state(self, page, ctx):
        return {"cookies": [{"name": "sess"}], "origins": []}


incident_page = FakePage(
    main_text=_MAIN_PANEL,                                  # TOP frame: the authenticated Main Panel
    frames_text=["We sent you a code. Enter the code we sent."],   # a leftover/hidden second frame
    inputs=[],                                             # no code field, no password
    content=_MAIN_PANEL + " <script>var otp='stale'</script> we sent you a code")

check("5-pre: REAL _classify on the exact incident page → authenticated (was permanently 'twofa')",
      vp._classify(incident_page) == "authenticated")

mod._vp = lambda: IncidentVP()
try:
    rec = Rec()
    s = mod.LiveLoginSession("sidINC", "orgA", ROW, rec.persist, rec.persist_shot)
    ivp = IncidentVP()
    # Prime the session as the incident left it: STUCK in awaiting_code.
    s._set(phase="awaiting_code", message="Enter the code")
    # Drive the detection loop in a background thread (as _preauth_loop does every ~1.2s) until it flips,
    # then stop. We call _preauth_detect directly in a bounded spin to avoid depending on loop timing.
    for _ in range(3):
        s._preauth_detect(incident_page, object(), ivp)
        if s.snapshot_phase() == "authenticated":
            break
    check("5a: the STUCK awaiting_code session flips to authenticated on detection",
          s.snapshot_phase() == "authenticated")
    check("5b: _on_authenticated persisted the durable session (Pull can now run)",
          any(u.get("auth_status") == "authenticated" and u.get("session_state") for u in rec.updates))
    check("5c: the saved session carries an expiry (reused until it expires)",
          any(u.get("session_expires_at") for u in rec.updates))

    # And confirm the loop itself would have concluded: run the REAL _preauth_loop briefly in a thread.
    rec2 = Rec()
    s2 = mod.LiveLoginSession("sidINC2", "orgA", ROW, rec2.persist, rec2.persist_shot)
    ivp2 = IncidentVP()
    s2._set(phase="awaiting_code", message="Enter the code")

    def _run_loop():
        try:
            s2._preauth_loop(incident_page, object(), ivp2)
        except Exception:
            pass

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    deadline = time.time() + 6
    while time.time() < deadline and s2.snapshot_phase() != "authenticated":
        time.sleep(0.1)
    check("5d: a live _preauth_loop stuck in awaiting_code concludes authenticated on its own",
          s2.snapshot_phase() == "authenticated")
    t.join(timeout=2)
finally:
    mod._vp = _saved_vp


print("\n==== %d ok, %d fail ====" % (_ok, _fail))
raise SystemExit(1 if _fail else 0)
