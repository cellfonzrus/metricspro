"""Proof for live-login-nav-retry — TWO 2026-07-17 live incidents, one package.
branch agent/commission/live-login-nav-retry.

INCIDENT 1: 🔴 Live login crashed with the RAW message
  `Page.goto: net::ERR_CONNECTION_CLOSED at https://www.vidapaycrm.com/Main%20Panel.aspx
   Call log: - navigating to ... waiting until "domcontentloaded"`.
Two defects: (1) _goto_login did ONE goto with no retry — a single bad Decodo exit / severed TLS gives
ERR_CONNECTION_CLOSED; (2) live_login._drive only caught vp.VidaPayLoginError around vp._goto_login, so a
RAW Playwright error escaped to _run's crash handler and the "Call log:" block reached the UI.

INCIDENT 2 (on retry): the session died with the FRIENDLY egress message (detection works) for URL
  `http://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx`.
ROOT CAUSE: _new_context's http→https 307-route can't fire on a SERVER-SIDE 302 hop (Chromium follows it at
network level, the route never sees it) → the plain-http absolute-form request reaches Decodo's squid raw.
FIX: a bounded, GET-only http→https-twin RECOVERY (_recover_from_proxy_error) wired into every proxy_error
site, pre- AND post-auth (recovery never re-submits a form / resends a code).

WHAT THIS COVERS (pure fakes only — no portal, no egress, no real Chromium):
  [1] vidapay_sweep._is_connection_error / _first_conn_marker / _strip_call_log — the connection-class
      match set + call-log stripping.
  [2] vidapay_sweep._goto_with_retry — fail N-then-succeed (attempt count + growing backoff), always-fail
      (→ friendly VidaPayLoginError, NO 'Call log:', names the ERR + attempts), non-connection error
      (selector timeout) NOT retried (raised on the FIRST attempt).
  [3] vidapay_sweep._goto_login — retries through _goto_with_retry (fail-twice-then-succeed → login
      proceeds), and an exhausted drop surfaces as a clean VidaPayLoginError (no 'Call log:').
  [4] live_login._clean_err — strips 'Call log:' onward; no-op otherwise.
  [5] live_login._drive — a goto failure (friendly VidaPayLoginError, a RAW connection error WITH a Call
      log, AND a NON-connection raw error) ALL land in phase="error" with a cleaned message + egress hint
      and NO 'Call log:' (the broadened except is the crux fix); session is never left in phase="login".
  [6] live_login._run — the outer crash handler sets phase="error" with a cleaned message (no 'Call log:')
      and _persist_diag fires (no worker-thread death leaving the session stuck).
  [7] SOURCE-level: which goto sites got the bounded retry (_goto_login base+fallback, run_vidapay_sweep,
      run_b2bsoft_sweep) and which were deliberately LEFT single-attempt (complete_2fa / complete_2fa_b2bsoft
      — post-login/post-code 2FA navigations, resend risk).

Run: python3 backend/scratchpad/live_login_nav_retry_proof.py
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMM = os.path.join(_HERE, "..", "app", "modules", "commcalc")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_COMM, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


vp = _load("vp_navretry_target", "vidapay_sweep.py")
mod = _load("ll_navretry_target", "live_login.py")

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


# A crafted Playwright-style error string (marker + a raw Call log block, exactly like the incident).
def _conn_err(marker="ERR_CONNECTION_CLOSED", url="https://www.vidapaycrm.com/Main%20Panel.aspx"):
    return Exception(
        "Page.goto: net::%s at %s\nCall log:\n  - navigating to \"%s\", waiting until "
        "\"domcontentloaded\"" % (marker, url, url))


def _timeout_err():
    return Exception(
        "Page.goto: Timeout 30000ms exceeded.\nCall log:\n  - navigating to \"https://x\", waiting until "
        "\"domcontentloaded\"")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[1] connection-class match set + call-log stripping")
for m in ("ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED", "ERR_EMPTY_RESPONSE",
          "ERR_TIMED_OUT", "ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED",
          "ERR_SOCKET_NOT_CONNECTED", "ERR_NAME_NOT_RESOLVED"):
    check("1a: %s is treated as connection-class (retryable)" % m, vp._is_connection_error(_conn_err(m)) is True)
    check("1a: _first_conn_marker(%s) == %s" % (m, m), vp._first_conn_marker(_conn_err(m)) == m)
check("1b: a selector Timeout is NOT connection-class (must not retry)", vp._is_connection_error(_timeout_err()) is False)
check("1b: a bare WAF/bot-wall message is NOT connection-class",
      vp._is_connection_error(Exception("Something doesn't look right — access denied")) is False)
check("1c: _first_conn_marker falls back to a generic marker when none present",
      vp._first_conn_marker(_timeout_err()) == "ERR_CONNECTION")
check("1d: _strip_call_log drops the 'Call log:' block (and after)",
      "Call log:" not in vp._strip_call_log(str(_conn_err()))
      and vp._strip_call_log(str(_conn_err())).startswith("Page.goto: net::ERR_CONNECTION_CLOSED"))
check("1e: _strip_call_log is a no-op when there is no call log",
      vp._strip_call_log("plain message") == "plain message")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[2] _goto_with_retry — retry on connection-class only, bounded, friendly final error")


class RetryPage:
    """Minimal page: goto fails `fail_n` times (connection-class or a supplied error) then succeeds."""
    def __init__(self, fail_n=0, err_factory=_conn_err, fail_forever=False):
        self.goto_calls = 0
        self.backoffs = []
        self._fail_n = fail_n
        self._err_factory = err_factory
        self._forever = fail_forever

    def goto(self, url, timeout=None, wait_until=None):
        self.goto_calls += 1
        if self._forever or self.goto_calls <= self._fail_n:
            raise self._err_factory()
        return None

    def wait_for_timeout(self, ms):
        self.backoffs.append(ms)


# fail twice then succeed
p = RetryPage(fail_n=2)
vp._goto_with_retry(p, "https://portal", attempts=3, backoffs=(1.5, 3.0))
check("2a: fail-twice-then-succeed → 3 goto attempts total, returns (no raise)", p.goto_calls == 3)
check("2b: growing backoff between attempts (1.5s, 3s) → 2 sleeps of 1500ms then 3000ms",
      p.backoffs == [1500, 3000])

# always fails → friendly VidaPayLoginError, no Call log, names ERR + attempts
p = RetryPage(fail_forever=True)
raised = None
try:
    vp._goto_with_retry(p, "https://portal", attempts=3, backoffs=(1.5, 3.0))
except Exception as e:
    raised = e
check("2c: always-fail → exactly `attempts` goto calls (3), no unbounded loop", p.goto_calls == 3)
check("2d: always-fail raises VidaPayLoginError (a friendly, UI-safe type)", isinstance(raised, vp.VidaPayLoginError))
_msg = str(raised)
check("2e: the message NEVER contains 'Call log:'", "Call log:" not in _msg)
check("2f: it names the connection failure (net::ERR_CONNECTION_CLOSED)", "net::ERR_CONNECTION_CLOSED" in _msg)
check("2g: it names the egress proxy + says it's NOT a credentials/2FA problem",
      "egress proxy" in _msg.lower() and "2fa" in _msg.lower())
check("2h: it includes the attempts count (3)", "3 " in _msg or "3 attempts" in _msg)

# a NON-connection error is NOT retried — raised on the FIRST attempt, unchanged type
p = RetryPage(fail_forever=True, err_factory=_timeout_err)
raised = None
try:
    vp._goto_with_retry(p, "https://portal", attempts=3)
except Exception as e:
    raised = e
check("2i: a selector Timeout (non-connection) is NOT retried → only 1 goto attempt", p.goto_calls == 1)
check("2j: …and it re-raises the ORIGINAL error (not converted to VidaPayLoginError)",
      raised is not None and not isinstance(raised, vp.VidaPayLoginError) and "Timeout" in str(raised))
check("2k: no backoff sleeps happen on a non-retried error", p.backoffs == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[3] _goto_login — flows through _goto_with_retry (both the base + the fallback goto)")


class LoginPage:
    """A fuller page: satisfies _wait_settle / _wait_for_password / _looks_like_bot_wall so a SUCCESSFUL
    goto lets _goto_login finish. goto fails `fail_n` times (connection-class) first."""
    def __init__(self, fail_n=0, fail_forever=False, has_password=True):
        self.goto_calls = 0
        self.backoffs = []
        self._fail_n = fail_n
        self._forever = fail_forever
        self._has_pw = has_password
        self.url = "https://portal/login"

    def goto(self, url, timeout=None, wait_until=None):
        self.goto_calls += 1
        if self._forever or self.goto_calls <= self._fail_n:
            raise _conn_err()
        return None

    def wait_for_timeout(self, ms):
        self.backoffs.append(ms)

    def title(self):
        return "Sign In"

    def content(self):
        return "<html><body>username password sign in</body></html>"

    @property
    def frames(self):
        return [self]

    def query_selector(self, sel):
        if sel == "input[type=password]" and self._has_pw:
            return object()
        return None


# fail twice then succeed → login proceeds to the password field and returns cleanly
lp = LoginPage(fail_n=2)
vp._goto_login(lp, "https://portal/login")
check("3a: _goto_login retries the base entry navigation (2 fails then success = 3 attempts)", lp.goto_calls == 3)
check("3b: it applied the growing backoff via the shared helper (retry sleeps 1500ms, 3000ms)",
      lp.backoffs[:2] == [1500, 3000])

# always fails → the exhausted friendly VidaPayLoginError propagates out of _goto_login (no Call log)
lp = LoginPage(fail_forever=True)
raised = None
try:
    vp._goto_login(lp, "https://portal/login")
except Exception as e:
    raised = e
check("3c: an exhausted drop surfaces from _goto_login as VidaPayLoginError", isinstance(raised, vp.VidaPayLoginError))
check("3d: …with NO 'Call log:' in the message", "Call log:" not in str(raised))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[4] live_login._clean_err")
check("4a: strips the raw Playwright 'Call log:' block",
      "Call log:" not in mod._clean_err(str(_conn_err()))
      and mod._clean_err(str(_conn_err())).startswith("Page.goto: net::ERR_CONNECTION_CLOSED"))
check("4b: no-op when there is no call log", mod._clean_err("clean message") == "clean message")
check("4c: tolerates None", mod._clean_err(None) == "")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[5] live_login._drive — a goto failure lands in phase=error, cleaned + egress hint, NO Call log")

ROW = {"portal_url": "https://portal/main", "processor": "vidapay", "account_id": "A1",
       "username": "u", "password": "p", "proxy_url": "http://user:pass@isp.decodo.com:10001"}


class FakeCtxCap:
    def new_page(self):
        return DrivePage()

    def new_cdp_session(self, page):        # via page.context in _start_screencast
        raise RuntimeError("no CDP in the fake (screencast stays off — fine)")


class DrivePage:
    def __init__(self):
        self.context = FakeCtxCap()
        self.url = "https://portal/main"

    def wait_for_timeout(self, *a, **k):
        pass


class FakeBrowser:
    def close(self):
        pass


class DriveVP:
    """A driver stand-in for _drive: launch/new_context/norm/shot succeed; _goto_login raises the
    configured error; egress_hint appends a recognizable marker."""
    DEFAULT_URL = "https://portal/main"
    B2BSOFT_URL = "https://b2b/main"

    class VidaPayLoginError(Exception):
        pass

    def __init__(self, goto_exc):
        self._goto_exc = goto_exc

    def _launch(self, p):
        return FakeBrowser()

    def _new_context(self, browser, proxy=None):
        return FakeCtxCap()

    def _proxy_arg(self, url):
        return {"server": "http://isp.decodo.com:10001"} if url else None

    def _norm_url(self, url, fallback):
        return url or fallback

    def _goto_login(self, page, base):
        raise self._goto_exc

    def egress_hint(self, proxy_url):
        return " |EGRESS-HINT|"

    def _shot_b64(self, page):
        return "SHOT"


def _drive_once(goto_exc):
    saved = mod._vp
    mod._vp = lambda: DriveVP(goto_exc)
    try:
        s = mod.LiveLoginSession("sid1", "orgA", ROW, None, None)
        fake_p = object()
        s._drive(fake_p)
        return s
    finally:
        mod._vp = saved


# (a) the friendly VidaPayLoginError _goto_login now raises (connection-class, no Call log)
friendly = DriveVP.VidaPayLoginError(
    "The portal connection dropped at/behind the egress proxy (net::ERR_CONNECTION_CLOSED) — it kept "
    "dropping across 3 attempts before the portal page could load. NOT your credentials and NOT a 2FA problem.")
s = _drive_once(friendly)
st = s.state()
check("5a: friendly VidaPayLoginError → phase 'error' (not stuck on 'login')", st["phase"] == "error")
check("5a: …message carries the friendly text, NO 'Call log:'",
      "connection dropped" in st["message"] and "Call log:" not in st["message"])
check("5a: …and the egress hint is appended (proxy_url available at the call site)", "|EGRESS-HINT|" in st["message"])

# (b) a RAW connection error WITH a Call log (simulating a raw net::ERR_ that reached _drive un-converted)
s = _drive_once(_conn_err())
st = s.state()
check("5b: RAW ERR_CONNECTION_CLOSED (with a Call log) → phase 'error', Call log STRIPPED",
      st["phase"] == "error" and "Call log:" not in st["message"] and "ERR_CONNECTION_CLOSED" in st["message"])
check("5b: …egress hint appended", "|EGRESS-HINT|" in st["message"])

# (c) THE CRUX: a NON-VidaPayLoginError, NON-connection raw error (selector timeout) — the OLD narrow
# `except vp.VidaPayLoginError` would have let this escape to _run and leak its Call log. The broadened
# `except Exception` must catch it, clean it, and land in phase=error.
s = _drive_once(_timeout_err())
st = s.state()
check("5c: a generic (non-VidaPayLoginError) Playwright error is now CAUGHT by _drive → phase 'error'",
      st["phase"] == "error")
check("5c: …its Call log is stripped (never reaches the UI)", "Call log:" not in st["message"])
check("5c: …and it is NEVER left in phase 'login' (no stuck session)", st["phase"] != "login")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[6] live_login._run — outer crash handler cleans the message + sets error + persists (no stuck session)")


class Rec:
    def __init__(self):
        self.updates = []
        self.shots = []

    def persist(self, u):
        self.updates.append(dict(u))

    def persist_shot(self, s):
        self.shots.append(s)


# Shadow playwright.sync_api so _run's `with sync_playwright() as p:` works with no real Playwright.
class _FakeSP:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


_fake_pw = types.ModuleType("playwright")
_fake_pw_sync = types.ModuleType("playwright.sync_api")
_fake_pw_sync.sync_playwright = lambda: _FakeSP()
_saved_pw = sys.modules.get("playwright")
_saved_pw_sync = sys.modules.get("playwright.sync_api")
sys.modules["playwright"] = _fake_pw
sys.modules["playwright.sync_api"] = _fake_pw_sync
try:
    rec = Rec()
    s = mod.LiveLoginSession("sid2", "orgA", ROW, rec.persist, rec.persist_shot)
    # Make _drive raise a RAW error carrying a Call log (simulate an escape past _drive's own handler).
    s._drive = lambda p: (_ for _ in ()).throw(_conn_err())
    s._run()
    st = s.state()
    check("6a: an escaped raw error → _run sets phase 'error' (session never stuck in 'login')",
          st["phase"] == "error")
    check("6b: _run's crash message is cleaned — NO 'Call log:'", "Call log:" not in st["message"])
    check("6c: the message keeps the 'Live login crashed:' prefix (still informative)",
          st["message"].startswith("Live login crashed:"))
    check("6d: _persist_diag fired in the finally → an error status was persisted",
          any(u.get("auth_status") == "error" for u in rec.updates))
finally:
    if _saved_pw is not None:
        sys.modules["playwright"] = _saved_pw
    else:
        sys.modules.pop("playwright", None)
    if _saved_pw_sync is not None:
        sys.modules["playwright.sync_api"] = _saved_pw_sync
    else:
        sys.modules.pop("playwright.sync_api", None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[7] SOURCE-level — which goto sites got the retry, which were deliberately left single-attempt")
with open(os.path.join(_COMM, "vidapay_sweep.py"), "r", encoding="utf-8") as fh:
    vsrc = fh.read()

# _goto_login: BOTH navigations go through _goto_with_retry, and the RAW page.goto is gone from it.
_gl = vsrc[vsrc.index("def _goto_login("):vsrc.index("def drive_typed_login(")]
check("7a: _goto_login uses _goto_with_retry for the base entry navigation",
      "_goto_with_retry(page, base_url" in _gl)
check("7b: _goto_login uses _goto_with_retry for the bot-wall LOGIN_URL fallback",
      "_goto_with_retry(page, LOGIN_URL" in _gl)
check("7c: _goto_login no longer does a bare single-attempt page.goto(", "page.goto(" not in _gl)

# run_vidapay_sweep + run_b2bsoft_sweep: fresh ENTRY navigations of a cold-restore browser → retried.
_rv = vsrc[vsrc.index("def run_vidapay_sweep("):vsrc.index("def run_b2bsoft_sweep(")]
_rb = vsrc[vsrc.index("def run_b2bsoft_sweep("):]
check("7d: run_vidapay_sweep entry navigation uses _goto_with_retry (fresh entry, nothing submitted)",
      "_goto_with_retry(page, base_url" in _rv and "page.goto(" not in _rv)
check("7e: run_b2bsoft_sweep entry navigation uses _goto_with_retry (fresh entry, nothing submitted)",
      "_goto_with_retry(page, base_url" in _rb and "page.goto(" not in _rb)

# complete_2fa + complete_2fa_b2bsoft: POST-LOGIN / POST-CODE (2FA) navigations → DELIBERATELY single-attempt
# (retrying risks a 2FA resend / mid-code state; task forbids it).
_c2 = vsrc[vsrc.index("def complete_2fa("):vsrc.index("def complete_2fa_b2bsoft(")]
_c2b = vsrc[vsrc.index("def complete_2fa_b2bsoft("):vsrc.index("def _open_reports_page(")]
check("7f: complete_2fa 2FA navigation is LEFT single-attempt (no retry — resend risk)",
      "page.goto(nav_url or base_url" in _c2 and "_goto_with_retry" not in _c2)
check("7g: complete_2fa_b2bsoft 2FA navigation is LEFT single-attempt (no retry — resend risk)",
      "page.goto(nav_url or base_url" in _c2b and "_goto_with_retry" not in _c2b)

# live_login._drive: the except around vp._goto_login is BROAD (Exception), not just VidaPayLoginError.
with open(os.path.join(_COMM, "live_login.py"), "r", encoding="utf-8") as fh:
    lsrc = fh.read()
_dr = lsrc[lsrc.index("vp._goto_login(page, base)"):]
_dr = _dr[:_dr.index("self._capture(page)\n                return") + 40]
check("7h: _drive catches a BROAD Exception around vp._goto_login (not just VidaPayLoginError)",
      "except Exception as e:" in _dr and "except vp.VidaPayLoginError" not in _dr)
check("7i: _drive cleans the message via _clean_err + appends egress_hint",
      "_clean_err(str(e))" in _dr and "vp.egress_hint(self.proxy_url)" in _dr)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  INCIDENT 2 — the http-302→squid hop recovery (GET-only https-twin re-navigation)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[8] vidapay_sweep._recover_from_proxy_error — bounded GET-only https-twin recovery")

_SQUID = "ERROR The requested URL could not be retrieved (squid) your cache administrator"
_HTTP_HOP = ("http://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com"
             "%2fMain+Panel.aspx")


class SquidPage:
    """Renders the egress squid error page until `clears_after` https re-gotos have happened (or never, if
    `persist`). Records goto targets + would-be form submits (to PROVE recovery never submits)."""
    def __init__(self, url=_HTTP_HOP, clears_after=1, persist=False):
        self._url = url
        self.goto_calls = []
        self.submits = []
        self.backoffs = []
        self._clears_after = clears_after
        self._https = 0
        self._persist = persist
        self._cleared = False

    @property
    def url(self):
        return self._url

    def goto(self, u, timeout=None, wait_until=None):
        self.goto_calls.append(u)
        if u.lower().startswith("https://"):
            self._https += 1
            if self._persist:
                # The real T-CETRA hop is permanent: navigating the https twin 302s BACK to the http
                # returnto squid page (url goes http again) — so recovery would keep finding an http URL.
                self._url = _HTTP_HOP
            else:
                self._url = u
                if self._https >= self._clears_after:
                    self._cleared = True
        else:
            self._url = u

    def content(self):
        return "welcome dashboard username password" if self._cleared else _SQUID

    def title(self):
        return ""

    def wait_for_timeout(self, ms):
        self.backoffs.append(ms)

    def click(self, *a, **k):
        self.submits.append(("click", a, k))

    def fill(self, *a, **k):
        self.submits.append(("fill", a, k))

    @property
    def frames(self):
        return [self]

    def query_selector(self, sel):
        return None


# (a) http squid page → re-goto the https twin → clears → True, exactly ONE recovery goto, NO form submit
sp = SquidPage(url=_HTTP_HOP, clears_after=1)
ok = vp._recover_from_proxy_error(sp, attempts=2)
check("8a: an http squid hop recovers → returns True", ok is True)
check("8a: exactly ONE recovery goto happened (bounded)", len(sp.goto_calls) == 1)
check("8a: the recovery goto targets the HTTPS twin of the http hop",
      sp.goto_calls[0] == "https://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx")
check("8a: recovery NEVER submitted a form (no click/fill) — it can't resend a 2FA code", sp.submits == [])

# (b) squid persists after 2 https re-gotos → False (friendly error), bounded to 2 gotos (no infinite loop)
sp = SquidPage(url=_HTTP_HOP, persist=True)
ok = vp._recover_from_proxy_error(sp, attempts=2)
check("8b: a persistent squid page → returns False (caller shows the friendly egress error)", ok is False)
check("8b: bounded — exactly 2 https re-gotos, never an infinite loop", len(sp.goto_calls) == 2)
check("8b: still no form submission during recovery", sp.submits == [])

# (c) proxy_error but the CURRENT url is https (NOT the http-hop case) → NO recovery goto at all
sp = SquidPage(url="https://www.vidapaycrm.com/Main%20Panel.aspx", persist=True)
ok = vp._recover_from_proxy_error(sp, attempts=2)
check("8c: an https squid url → NO recovery attempt (nothing to upgrade)", ok is False and sp.goto_calls == [])

# (d) not actually a proxy error → returns True immediately, no goto
class CleanPage(SquidPage):
    def content(self):
        return "username password sign in"


cp = CleanPage()
check("8d: a clean (non-squid) page → True immediately, no recovery goto",
      vp._recover_from_proxy_error(cp) is True and cp.goto_calls == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[9] _goto_login — an http-302→squid landing recovers to the https twin, then finds the form")


class GotoLoginSquidPage:
    """The base goto 'succeeds' but the server 302s to the http squid hop; _goto_login's recovery re-gotos
    the https twin, the squid clears, and the login form appears."""
    def __init__(self):
        self.goto_calls = []
        self.backoffs = []
        self._url = "https://start"
        self._squid = False
        self._has_pw = False

    @property
    def url(self):
        return self._url

    def goto(self, u, timeout=None, wait_until=None):
        self.goto_calls.append(u)
        if u.lower().startswith("https://") and "default.aspx" in u.lower():
            self._url = u; self._squid = False; self._has_pw = True          # https twin → recovered + form
        elif u.lower().startswith("http://"):
            self._url = u; self._squid = True; self._has_pw = False
        else:                                                                 # base https Main Panel → 302→squid
            self._url = _HTTP_HOP; self._squid = True; self._has_pw = False

    def content(self):
        return _SQUID if self._squid else ("username password sign in" + ("<input type=password>" if self._has_pw else ""))

    def title(self):
        return ""

    def wait_for_timeout(self, ms):
        self.backoffs.append(ms)

    @property
    def frames(self):
        return [self]

    def query_selector(self, sel):
        return object() if (sel == "input[type=password]" and self._has_pw) else None


glp = GotoLoginSquidPage()
vp._goto_login(glp, "https://www.vidapaycrm.com/Main%20Panel.aspx")     # must NOT raise
check("9a: _goto_login recovered the http-302→squid hop by going to the HTTPS twin",
      any("default.aspx" in u.lower() and u.lower().startswith("https://") for u in glp.goto_calls))
check("9b: after recovery the form is reachable → _goto_login returns cleanly (no proxy error surfaced)",
      glp.query_selector("input[type=password]") is not None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[10] live_login._preauth_detect — proxy_error branch recovers first, only errors if it can't clear")


class RecoverVP:
    _TRUST_PAGE_WORDS = ("trust this device",)

    def __init__(self, recover_ok):
        self.state = "proxy_error"
        self._recover_ok = recover_ok
        self.recover_calls = 0

    def _classify(self, page):
        return self.state

    def _recover_from_proxy_error(self, page):
        self.recover_calls += 1
        if self._recover_ok:
            self.state = "login"          # squid cleared → the real login page is back
            return True
        return False

    def _proxy_error_message(self, url, proxy):
        return "died at the egress proxy"

    def _shot_b64(self, page):
        return "SHOT"

    def _looks_like_captcha(self, page):
        return False

    def _code_field(self, page):
        return None

    def _page_text(self, page):
        return ""

    def capture_session_state(self, page, ctx):
        return {"cookies": [], "origins": []}


_saved_vp = mod._vp
mod._vp = lambda: RecoverVP(True)     # _capture uses _vp()._shot_b64
try:
    # recovery SUCCEEDS → phase returns to the login flow, NOT error
    vpr = RecoverVP(True)
    s = mod.LiveLoginSession("sidR", "orgA", ROW, None, None)
    s._set(phase="login")
    s._preauth_detect(type("P", (), {"url": _HTTP_HOP})(), None, vpr)
    check("10a: proxy_error + recovery OK → _preauth_detect does NOT error (back to login flow)",
          s.snapshot_phase() != "error" and s.snapshot_phase() == "login")
    check("10a: …and recovery was attempted exactly once", vpr.recover_calls == 1)

    # recovery FAILS (e.g. https url / squid persists) → the friendly egress error, as before
    mod._vp = lambda: RecoverVP(False)
    vpf = RecoverVP(False)
    s = mod.LiveLoginSession("sidR2", "orgA", ROW, None, None)
    s._set(phase="human_action")
    s._preauth_detect(type("P", (), {"url": "https://x"})(), None, vpf)
    check("10b: proxy_error + recovery FAILS → phase 'error' with the egress-proxy message",
          s.snapshot_phase() == "error" and "egress proxy" in s.state()["message"])
    check("10b: …recovery was still attempted once", vpf.recover_calls == 1)
finally:
    mod._vp = _saved_vp


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[11] SOURCE-level — the http→https recovery is wired into every proxy_error site (pre + post auth)")

# vidapay_sweep: _goto_login (base + fallback), begin_login post-submit, complete_2fa, complete_2fa_b2bsoft,
# run_vidapay_sweep, run_b2bsoft_sweep all call _recover_from_proxy_error before surfacing a proxy error.
check("11a: _goto_login runs _recover_from_proxy_error after the base goto", "_recover_from_proxy_error(page)" in _gl)
check("11b: _goto_login's fallback also triggers on a squid page (not just the bot-wall)",
      "_looks_like_proxy_error(page)" in _gl and "or _looks_like_proxy_error" in _gl)
_bl = vsrc[vsrc.index("def begin_login("):vsrc.index("def begin_login_b2bsoft(")]
check("11c: begin_login post-submit recovers before erroring",
      "_recover_from_proxy_error(page)" in _bl)
check("11d: complete_2fa recovers before erroring (GET-only, no code resend)",
      "not _recover_from_proxy_error(page)" in _c2)
check("11e: complete_2fa_b2bsoft recovers before erroring", "not _recover_from_proxy_error(page)" in _c2b)
check("11f: run_vidapay_sweep recovers before erroring", "_recover_from_proxy_error(page)" in _rv)
check("11g: run_b2bsoft_sweep recovers before erroring", "not _recover_from_proxy_error(page)" in _rb)

# live_login: _preauth_detect + _drive post-login + _do_submit post-code all attempt _recover_proxy first.
check("11h: live_login._preauth_detect attempts _recover_proxy on proxy_error", "_recover_proxy(vp, page)" in lsrc)
check("11i: live_login has a _recover_proxy wrapper delegating to vp._recover_from_proxy_error",
      "def _recover_proxy(vp, page):" in lsrc and "vp._recover_from_proxy_error(page)" in lsrc)
_dosub = lsrc[lsrc.index("def _do_submit("):]
check("11j: _do_submit (post-code) recovers before erroring (GET-only, never resends the code)",
      "_recover_proxy(vp, page)" in _dosub)


print("\n==== %d ok, %d fail ====" % (_ok, _fail))
raise SystemExit(1 if _fail else 0)
