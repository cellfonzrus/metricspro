"""Proof for live-login-squid-recovery2 — INCIDENT 4 (2026-07-17): the egress squid wall returned in a NEW
flavor and the shipped v1 recovery (bdb6f68) did NOT conclude. branch agent/commission/live-login-squid-recovery2.

OWNER SCREENSHOT FACTS: phase badge "⏳ Signing in…" (auto-drive), the live view shows Decodo squid's OWN
error page reporting the URL as PATH-ONLY: `/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx`
— NO scheme, NO hostname (squid hints: "Missing or incorrect access protocol", "Missing hostname",
"Illegal double-escape in the URL-Path"). The session sat on the squid page in the sign-in drive path
(phase stayed 'login').

DEFECTS FIXED (see the return note):
  A. the sign-in wait never sat silent on a squid page — _drive now FORCES the proxy_error branch when the
     post-submit page is squid even if _classify missed it, and recovery is robust (below).
  B. _https_upgrade_url hardened: a host-less value → None (never a malformed Location); a raw space → %20;
     existing %xx preserved (no double-escape); the 307 route only emits a GUARANTEED-ABSOLUTE https Location.
  C. RECOVERY v2 — DESTINATION FALLBACK: when the https-twin re-goto is impossible (page.url is about:blank /
     chrome-error:// / a host-less path-only form / already https) or keeps squid'ing, do a DIRECT goto of the
     KNOWN-good https destination (pre-auth → LOGIN_URL; post-submit/post-auth → the https base). Cookies
     persist → a completed login lands authenticated (auth-detect _classify concludes it). Bounded, GET-only,
     never re-submits a form/code.
  D. DIAGNOSTICS: squid's REPORTED url is extracted (_squid_reported_url) into the friendly message + diag.

Pure fakes only — vidapay_sweep.py + live_login.py loaded by file path, _vp() monkeypatched. ZERO deps.
Run: python3 backend/scratchpad/live_login_squid_recovery2_proof.py
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMM = os.path.join(_HERE, "..", "app", "modules", "commcalc")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_COMM, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


vp = _load("vp_squid2_target", "vidapay_sweep.py")
mod = _load("ll_squid2_target", "live_login.py")

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


_HTTP_HOP = ("http://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com"
             "%2fMain+Panel.aspx")
_PATH_ONLY = "/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx"
_MAIN_PANEL = "Main Panel Account Manager Billing Manager Welcome Sign Out"
_SQUID_HTML = ('ERROR The requested URL could not be retrieved (squid). The following error was encountered '
               'while trying to retrieve the URL: <a href="x">%s</a> Invalid URL — Missing hostname. Your '
               'cache administrator is webmaster.' % _PATH_ONLY)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[B] _https_upgrade_url hardening + the 307 route only emits an ABSOLUTE https Location")
check("B1: a host-less http:///path → None (never becomes a malformed host-less Location)",
      vp._https_upgrade_url("http:///Default.aspx?returnto=x") is None)
check("B1: an empty-authority http://  → None", vp._https_upgrade_url("http://") is None)
u = vp._https_upgrade_url("http://www.vidapaycrm.com/Main Panel.aspx")
check("B2: a RAW space in the path is encoded to %20 (squid rejects a raw space)",
      u == "https://www.vidapaycrm.com/Main%20Panel.aspx")
u = vp._https_upgrade_url("http://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fx%2fy")
check("B3: an already-%xx-encoded returnto survives byte-for-byte (NO double-escape %3a→%253a)",
      u == "https://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fx%2fy")
check("B4: every non-None result is an ABSOLUTE https URL with a host",
      u.lower().startswith("https://") and "vidapaycrm.com" in u)
check("B5: a full absolute http hop upgrades to its https twin (incident-2 path still works)",
      vp._https_upgrade_url(_HTTP_HOP) ==
      "https://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx")
check("B6: loopback still excluded", vp._https_upgrade_url("http://127.0.0.1:8080/x") is None)
# SOURCE: the 307 route refuses to fulfill unless the Location is guaranteed-absolute https.
with open(os.path.join(_COMM, "vidapay_sweep.py"), "r", encoding="utf-8") as fh:
    vsrc = fh.read()
check("B7: the 307 route only fulfills a Location that startswith https:// (else passes through)",
      'if https and not https.lower().startswith("https://"):' in vsrc
      and "route.fulfill(status=307" in vsrc)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[D] squid REPORTED-URL extraction into the diag / friendly message")


class ContentPage:
    def __init__(self, content):
        self._content = content

    def content(self):
        return self._content


check("D1: _squid_reported_url pulls squid's reported URL from its error page (case-preserved)",
      vp._squid_reported_url(ContentPage(_SQUID_HTML)) == _PATH_ONLY)
check("D2: _squid_reported_url → None on a normal page (no squid marker)",
      vp._squid_reported_url(ContentPage("<html>welcome dashboard</html>")) is None)
check("D3: _squid_reported_url never raises when content() blows up",
      vp._squid_reported_url(object()) is None)
msg = vp._proxy_error_message("https://x", None, _PATH_ONLY)
check("D4: _proxy_error_message APPENDS squid's reported URL (so the next incident needs no screenshot)",
      "Squid reported the URL as:" in msg and _PATH_ONLY in msg)
check("D5: _proxy_error_message WITHOUT a reported URL omits that clause (back-compat)",
      "Squid reported the URL as:" not in vp._proxy_error_message("https://x", None))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[C] recovery v2 — https-twin THEN destination fallback (immune to URL mangling), GET-only, bounded")


class RecPage:
    """Renders squid until a CLEARING goto happens. `twin_clears`: an https-twin goto of the http hop clears;
    `dest_clears`: a DIRECT dest goto (LOGIN_URL / base) clears (login completed server-side, cookies carry).
    Records goto targets + would-be form submits (recovery must NEVER submit)."""
    def __init__(self, url=_HTTP_HOP, twin_clears=False, dest_clears=True):
        self.url = url
        self._squid = True
        self._twin_clears = twin_clears
        self._dest_clears = dest_clears
        self.gotos = []
        self.submits = []

    def goto(self, u, timeout=None, wait_until=None):
        self.gotos.append(u)
        self.url = u
        low = u.lower()
        is_twin = low.startswith("https://") and "default.aspx" in low and "returnto=" in low
        if is_twin:
            if self._twin_clears:
                self._squid = False
        else:                                   # a DIRECT dest goto (LOGIN_URL or the https base)
            if self._dest_clears:
                self._squid = False

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return ""

    def content(self):
        return _SQUID_HTML if self._squid else ("<html>%s</html>" % _MAIN_PANEL)

    def evaluate(self, js):
        return "requested url could not be retrieved (squid)" if self._squid else _MAIN_PANEL

    @property
    def frames(self):
        return [self]

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []

    # form sinks — must stay empty during recovery
    def click(self, *a, **k):
        self.submits.append("click")

    def fill(self, *a, **k):
        self.submits.append("fill")

    def type(self, *a, **k):
        self.submits.append("type")


BASE = "https://www.vidapaycrm.com/Main%20Panel.aspx"

# C1 — the INCIDENT: page.url is a host-less PATH-ONLY form → https-twin impossible → DEST fallback clears.
p = RecPage(url=_PATH_ONLY, twin_clears=False, dest_clears=True)
ok = vp._recover_from_proxy_error(p, dest_url=BASE)
check("C1: path-only page.url (no usable http twin) → recovered via the DESTINATION fallback", ok is True)
check("C1: the destination goto targeted the known-good https base", p.gotos and p.gotos[-1] == BASE)
check("C1: recovery did NOT re-submit any form/code (GET-only)", p.submits == [])
check("C1: after recovery the page is the authenticated app → _classify concludes authenticated",
      vp._classify(p) == "authenticated")

# C2 — page.url = about:blank (unusable) → dest fallback, pre-auth destination = LOGIN_URL
p = RecPage(url="about:blank", twin_clears=False, dest_clears=True)
ok = vp._recover_from_proxy_error(p, dest_url=vp.LOGIN_URL)
check("C2: about:blank page.url → DEST fallback to LOGIN_URL clears (pre-auth destination)",
      ok is True and p.gotos and p.gotos[-1] == vp.LOGIN_URL)

# C3 — page.url = chrome-error:// (unusable) → dest fallback
p = RecPage(url="chrome-error://chromewebdata/", twin_clears=False, dest_clears=True)
check("C3: chrome-error:// page.url → DEST fallback recovers", vp._recover_from_proxy_error(p, dest_url=BASE) is True)

# C4 — full absolute http hop, twin clears → phase-1 https-twin path still works (incident-2 unchanged)
p = RecPage(url=_HTTP_HOP, twin_clears=True, dest_clears=False)
ok = vp._recover_from_proxy_error(p, dest_url=BASE)
check("C4: a FULL http hop still recovers via the https-twin re-goto (phase 1, incident-2 path)",
      ok is True and any("default.aspx" in g.lower() and g.lower().startswith("https://") for g in p.gotos))

# C5 — everything persists (login NOT completed) → bounded, returns False, no submit
p = RecPage(url=_PATH_ONLY, twin_clears=False, dest_clears=False)
ok = vp._recover_from_proxy_error(p, dest_url=BASE, attempts=2)
check("C5: squid persists everywhere → returns False (caller surfaces the friendly error)", ok is False)
check("C5: BOUNDED — a single dest goto (path-only skips phase 1), never an infinite loop", len(p.gotos) == 1)
check("C5: still no form/code re-submission on the failing path", p.submits == [])

# C6 — no dest_url + unusable url → False immediately (old callers without a dest degrade safely)
p = RecPage(url="about:blank", twin_clears=False, dest_clears=True)
check("C6: unusable url + NO dest_url → False, no goto (safe degrade for a dest-less call)",
      vp._recover_from_proxy_error(p) is False and p.gotos == [])

# C7 — a clean (non-squid) page → True immediately, no goto
pc = RecPage(url=BASE)
pc._squid = False
check("C7: a clean page → True immediately, no recovery goto",
      vp._recover_from_proxy_error(pc, dest_url=BASE) is True and pc.gotos == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[A] the sign-in drive NEVER sits silent on squid — _drive guard + _do_submit recovery (post-code)")

ROW = {"portal_url": "https://portal/Main.aspx", "processor": "vidapay", "account_id": "A1",
       "username": "u", "password": "p", "proxy_url": None}


class Rec:
    def __init__(self):
        self.updates = []
        self.shots = []

    def persist(self, u):
        self.updates.append(dict(u))

    def persist_shot(self, s):
        self.shots.append(s)


class FakeEl:
    def fill(self, *a, **k):
        pass

    def press(self, *a, **k):
        pass

    def click(self, *a, **k):
        pass


class FakeFrame:
    pass


class DrivePage:
    def __init__(self, url=_PATH_ONLY):
        self.url = url

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def query_selector(self, *a, **k):
        return None


class FakeCtx:
    def new_page(self):
        return DrivePage()


class FakeBrowser:
    def close(self):
        pass


# ── A1: _drive guard — post-submit page is squid but _classify MISSED it (returned 'login') → the guard
#        FORCES the proxy_error branch → recovery fails → friendly egress error (NOT 'Login was rejected',
#        NOT a silent awaiting_code). Squid's reported URL is in the message.
class DriveVP:
    DEFAULT_URL = "https://portal/Main.aspx"
    B2BSOFT_URL = "https://b2b/"

    class VidaPayLoginError(Exception):
        pass

    @staticmethod
    def _launch(p):
        return FakeBrowser()

    @staticmethod
    def _new_context(browser, proxy=None):
        return FakeCtx()

    @staticmethod
    def _proxy_arg(url):
        return None

    @staticmethod
    def _norm_url(url, default):
        return url or default

    @staticmethod
    def _goto_login(page, base):
        pass

    @staticmethod
    def _password_frame(page):
        return (FakeFrame(), FakeEl())

    @staticmethod
    def drive_typed_login(page, fr, el, acc, user, pw):
        pass

    @staticmethod
    def prefill_login(page, fr, el, acc, user, pw):
        pass

    @staticmethod
    def _wait_settle(page):
        pass

    @staticmethod
    def _shot_b64(page):
        return "SQUID_FRAME"

    @staticmethod
    def _classify(page):
        return "login"                       # MISS: squid on screen but classify says 'login'

    @staticmethod
    def _looks_like_proxy_error(page):
        return True                          # …but the guard sees the squid page

    @staticmethod
    def _looks_like_captcha(page):
        return False

    @staticmethod
    def _recover_from_proxy_error(page, dest_url=None):
        return False                         # recovery can't clear → friendly error must surface

    @staticmethod
    def _squid_reported_url(page):
        return _PATH_ONLY

    @staticmethod
    def _proxy_error_message(url, proxy, reported=None):
        return "EGRESS PROXY (squid)%s" % ((" reported=%s" % reported) if reported else "")

    @staticmethod
    def egress_hint(proxy):
        return ""

    @staticmethod
    def _code_field(page):
        return None


rec = Rec()
s = mod.LiveLoginSession("sidD", "orgA", ROW, rec.persist, rec.persist_shot)
_saved = mod._vp
mod._vp = lambda: DriveVP
try:
    s._drive(object())
finally:
    mod._vp = _saved
check("A1: _drive guard converts a missed squid ('login' classify) → proxy_error → phase 'error'",
      s.phase == "error")
check("A1: …with the egress-proxy message (NOT 'Login was rejected', NOT silent awaiting_code)",
      "EGRESS PROXY (squid)" in (s.message or "") and "rejected" not in (s.message or "").lower())
check("A1: …and squid's reported URL is carried in the message (diag D)",
      _PATH_ONLY in (s.message or ""))
# SOURCE: the guard exists.
with open(os.path.join(_COMM, "live_login.py"), "r", encoding="utf-8") as fh:
    lsrc = fh.read()
check("A2: _drive has the belt-and-suspenders squid guard after _classify",
      'if state != "proxy_error" and vp._looks_like_proxy_error(page):' in lsrc
      and 'state = "proxy_error"' in lsrc)


# ── A3/A4: _do_submit (post-code) — the proxy hop AFTER the code. Recovery v2 with the base destination.
class SubmitVP:
    DEFAULT_URL = "https://portal/Main.aspx"
    B2BSOFT_URL = "https://b2b/"

    def __init__(self, recover_ok):
        self._recover_ok = recover_ok
        self._recovered = False
        self.recover_calls = 0
        self.dest_seen = "UNSET"

    @staticmethod
    def _frames(page):
        return [FakeFrame()]

    @staticmethod
    def _find_input(fr, kinds=(), want=()):
        return FakeEl()

    @staticmethod
    def _tick_remember(fr):
        pass

    @staticmethod
    def _click_submit(fr, words):
        return True

    @staticmethod
    def finalize_after_code(page, on_step=None):
        if callable(on_step):
            on_step()
        return "proxy_error"

    @staticmethod
    def _wait_settle(page):
        pass

    @staticmethod
    def _shot_b64(page):
        return "FRAME"

    @staticmethod
    def _page_text(page):
        return ""

    @staticmethod
    def _looks_like_captcha(page):
        return False

    def _classify(self, page):
        return "authenticated" if self._recovered else "proxy_error"

    def _recover_from_proxy_error(self, page, dest_url=None):
        self.recover_calls += 1
        self.dest_seen = dest_url
        if self._recover_ok:
            self._recovered = True
            return True
        return False

    @staticmethod
    def _squid_reported_url(page):
        return _PATH_ONLY

    @staticmethod
    def _proxy_error_message(url, proxy, reported=None):
        return "EGRESS PROXY (squid)%s" % ((" reported=%s" % reported) if reported else "")

    def _norm_url(self, url, default):
        return url or default

    def capture_session_state(self, page, ctx):
        return {"cookies": [{"n": "s"}], "origins": []}

    def _code_field(self, page):
        return None


# A3: code accepted but the post-code nav hit squid; recovery v2 (cookies carry) → authenticated + persisted.
rec = Rec()
s = mod.LiveLoginSession("sidS1", "orgA", ROW, rec.persist, rec.persist_shot)
sv = SubmitVP(recover_ok=True)
mod._vp = lambda: sv
try:
    ret = s._do_submit(DrivePage(url="about:blank"), FakeCtx(), sv, "123456")
finally:
    mod._vp = _saved
check("A3: post-code squid + recovery v2 succeeds → _do_submit concludes authenticated", s.phase == "authenticated")
check("A3: …the durable session is persisted (Pull can now run)",
      any(u.get("auth_status") == "authenticated" and u.get("session_state") for u in rec.updates))
check("A3: …recovery used the POST-submit destination = the https base (not LOGIN_URL)",
      sv.dest_seen == "https://portal/Main.aspx" and sv.recover_calls == 1)
check("A3: …never resent the code (GET-only recovery)", ret is True)

# A4: recovery can't clear → phase error with the egress message + squid's reported URL (never silent).
rec = Rec()
s = mod.LiveLoginSession("sidS2", "orgA", ROW, rec.persist, rec.persist_shot)
sv = SubmitVP(recover_ok=False)
mod._vp = lambda: sv
try:
    ret = s._do_submit(DrivePage(url=_PATH_ONLY), FakeCtx(), sv, "123456")
finally:
    mod._vp = _saved
check("A4: recovery fails → phase 'error' with the egress message + squid reported URL (not silent)",
      s.phase == "error" and "EGRESS PROXY (squid)" in (s.message or "") and _PATH_ONLY in (s.message or ""))


print("\n==== %d ok, %d fail ====" % (_ok, _fail))
raise SystemExit(1 if _fail else 0)
