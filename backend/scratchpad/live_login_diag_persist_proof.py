"""Proof for the live-login diag-screenshot persistence fix
(branch agent/commission/vidapay-http-upgrade — NEW commit on top of 5595aa1).

THE BUG. The live-login session (live_login.py) captures screenshots into an IN-MEMORY buffer that it
streams via GET /live-login/state, but it NEVER writes them to data_source.login_shot — the store that
'📷 What the browser saw' (GET /login/screenshot) reads and that begin_login/complete_2fa failures write
via _store_login_shot. So when a LIVE session stops (proxy_error / auth failure / idle timeout / operator
Close), the panel keeps showing a STALE earlier attempt's frame — exactly during the failures where it
matters (owner: tonight's live login died on the squid proxy page at 01:37 GMT but the panel still showed
a 7/15 3:20 PM frame).

THE FIX. LiveLoginSession now takes a `persist_shot` callback (wired in router.py to _store_login_shot,
the SAME store/shape the two-call failures use) and a `_persist_diag()` that, at EVERY stop, writes the
last live frame (login_shot/login_shot_at) + a status line (auth_message, plus auth_status='error' on a
hard stop — mirroring _do_portal_login's failure shape). `_run()` calls `_persist_diag()` in a finally so
it fires on proxy_error / auth failure / idle timeout / operator Close / crash / import-failure alike.
proxy_error is now a recognized STOP in _drive and _do_submit (was silently lingering on the code screen).

This proof loads live_login.py BY FILE PATH (top-level imports are stdlib only; the app + playwright
imports are lazy), monkeypatches `_vp()` with a FakeVP and shadows `playwright.sync_api`, so it runs with
ZERO app/playwright/portal deps. Router wiring is checked at the source level (importing router.py would
pull the whole app).

Run: python3 backend/scratchpad/live_login_diag_persist_proof.py
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_LL = os.path.join(_HERE, "..", "app", "modules", "commcalc", "live_login.py")
_spec = importlib.util.spec_from_file_location("live_login_proof_target", _LL)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_fail = 0
_ok = 0


def check(name, cond):
    global _fail, _ok
    if cond:
        _ok += 1
        print("  ok   %s" % name)
    else:
        _fail += 1
        print("  FAIL %s" % name)


class Rec:
    """Recording callbacks standing in for _live_persist / _live_persist_shot (the router closures)."""
    def __init__(self):
        self.updates = []
        self.shots = []

    def persist(self, updates):
        self.updates.append(dict(updates))

    def persist_shot(self, shot):
        self.shots.append(shot)


ROW = {"portal_url": "https://portal", "processor": "vidapay", "account_id": "A1",
       "username": "u", "password": "p", "proxy_url": None}


def new_session(rec):
    return mod.LiveLoginSession("sid1", "orgA", ROW, rec.persist, rec.persist_shot)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. _persist_diag — the heart: what gets written to the diag store on each terminal phase.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[1] _persist_diag writes to the same diag store begin_login failures use")

# A) hard error (e.g. proxy_error) + a frame → frame persisted AND a status line with auth_status='error'
rec = Rec(); s = new_session(rec)
s.phase, s.message, s._shot = "error", "died at the egress proxy", "SQUID_FRAME"
s._persist_diag()
check("A: error persists the frame to login_shot store (persist_shot got the frame)",
      rec.shots == ["SQUID_FRAME"])
check("A: error writes auth_status='error' + auth_message (the status line)",
      len(rec.updates) == 1 and rec.updates[0].get("auth_status") == "error"
      and rec.updates[0].get("auth_message") == "died at the egress proxy")
check("A: the status write carries a fresh last_run_at timestamp",
      "last_run_at" in rec.updates[0] and "T" in rec.updates[0]["last_run_at"])

# B) operator Close / idle timeout (cancelled) + frame → frame + a status line, but NOT flagged as error
rec = Rec(); s = new_session(rec)
s.phase, s.message, s._shot = "cancelled", "Cancelled by the operator.", "CANCEL_FRAME"
s._persist_diag()
check("B: cancelled persists the last frame", rec.shots == ["CANCEL_FRAME"])
check("B: cancelled writes the status line (auth_message) but NOT auth_status='error'",
      len(rec.updates) == 1 and rec.updates[0].get("auth_message") == "Cancelled by the operator."
      and "auth_status" not in rec.updates[0])

# C) authenticated → frame refreshed (shows the signed-in screen), status OWNED by _on_authenticated
rec = Rec(); s = new_session(rec)
s.phase, s.message, s._shot = "authenticated", "Signed in — session saved.", "DASH_FRAME"
s._persist_diag()
check("C: authenticated refreshes login_shot to the final frame", rec.shots == ["DASH_FRAME"])
check("C: authenticated does NOT overwrite the status (no persist() call from _persist_diag)",
      rec.updates == [])

# D) crash/import-failure with NO frame captured → status line still written, no bogus empty shot
rec = Rec(); s = new_session(rec)
s.phase, s.message, s._shot = "error", "Live login crashed: boom", None
s._persist_diag()
check("D: no frame → persist_shot NOT called (no empty login_shot written)", rec.shots == [])
check("D: no frame → the status line is STILL written (auth_status='error')",
      len(rec.updates) == 1 and rec.updates[0].get("auth_status") == "error")

# E) best-effort: raising callbacks must NOT propagate out of _persist_diag
class Boom:
    def persist(self, u):
        raise RuntimeError("db down")

    def persist_shot(self, s):
        raise RuntimeError("db down")


b = Boom()
s = mod.LiveLoginSession("sid1", "orgA", ROW, b.persist, b.persist_shot)
s.phase, s.message, s._shot = "error", "x", "F"
try:
    s._persist_diag()
    check("E: raising persist/persist_shot are swallowed (never raises)", True)
except Exception:
    check("E: raising persist/persist_shot are swallowed (never raises)", False)

# F) no callbacks wired at all (e.g. constructed without them) → no-op, never raises
s = mod.LiveLoginSession("sid1", "orgA", ROW)
s.phase, s.message, s._shot = "error", "x", "F"
try:
    s._persist_diag()
    check("F: missing callbacks → no-op, never raises", True)
except Exception:
    check("F: missing callbacks → no-op, never raises", False)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. A FakeVP so we can drive the REAL _run / _drive / _do_submit offline (no browser, no portal).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class FakeEl:
    def fill(self, *a, **k):
        pass

    def press(self, *a, **k):
        pass

    def click(self, *a, **k):
        pass


class FakeFrame:
    pass


class FakePage:
    def __init__(self, url="http://www.vidapaycrm.com/Default.aspx?returnto=..."):
        self.url = url

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def query_selector(self, *a, **k):
        return None


class FakeCtx:
    def new_page(self):
        return FakePage()


class FakeBrowser:
    def close(self):
        pass


class FakeVP:
    """Minimal vidapay_sweep stand-in: login succeeds, then _classify lands on the squid proxy page."""
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
        return (FakeFrame(), FakeEl())      # pw element present → skip companyId, proceed to sign-in

    @staticmethod
    def drive_typed_login(page, fr, el, acc, user, pw):
        pass

    @staticmethod
    def _wait_settle(page):
        pass

    @staticmethod
    def _shot_b64(page):
        return "SQUID_FRAME"

    @staticmethod
    def _classify(page):
        return "proxy_error"

    @staticmethod
    def _proxy_error_message(url, proxy_url):
        return "PROXY DIED (squid) for %s" % (url or "?")

    # used only by _do_submit
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

    _TRUST_PAGE_WORDS = ()

    @staticmethod
    def _page_text(page):
        return ""


# ── shadow playwright.sync_api so _run's `with sync_playwright() as p` costs nothing ────────────────
class _FakeP:
    def __enter__(self):
        return "FAKE_P"

    def __exit__(self, *a):
        return False


def _install_fake_playwright():
    saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
    fake_pw = types.ModuleType("playwright")
    fake_sync = types.ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: _FakeP()
    fake_pw.sync_api = fake_sync
    sys.modules["playwright"] = fake_pw
    sys.modules["playwright.sync_api"] = fake_sync
    return saved


def _restore_modules(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


print("\n[2] _run() fires _persist_diag in its finally on EVERY exit path")

# G) full drive: login → _classify == proxy_error → _run's finally persists the squid frame + status.
rec = Rec(); s = new_session(rec)
_orig_vp = mod._vp
mod._vp = lambda: FakeVP
saved = _install_fake_playwright()
try:
    s._run()
finally:
    mod._vp = _orig_vp
    _restore_modules(saved)
check("G: proxy_error is a STOP in _drive → phase ends 'error' (not lingering awaiting_code)",
      s.phase == "error")
check("G: _drive proxy_error surfaces the egress-proxy message (not 'session expired')",
      "PROXY DIED" in (s.message or ""))
check("G: _run finally persisted the live squid frame to the login_shot store",
      rec.shots and rec.shots[-1] == "SQUID_FRAME")
check("G: _run finally persisted the status line (auth_status='error' + the proxy message)",
      any(u.get("auth_status") == "error" and "PROXY DIED" in (u.get("auth_message") or "")
          for u in rec.updates))

# H) playwright import failure → _run's except sets error, finally still persists the status line.
rec = Rec(); s = new_session(rec)
saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
sys.modules["playwright.sync_api"] = None      # a None entry makes `from ... import ...` raise
try:
    s._run()
finally:
    _restore_modules(saved)
check("H: import failure → phase 'error'", s.phase == "error")
check("H: import failure → status line written even with no frame captured",
      any(u.get("auth_status") == "error" and "not available" in (u.get("auth_message") or "")
          for u in rec.updates))
check("H: import failure → no frame, so no empty login_shot written", rec.shots == [])


print("\n[3] _do_submit treats a post-code proxy_error as a STOP (was 'code not accepted')")

# I) code accepted but finalize navigation dies at the proxy → _do_submit stops with the proxy message.
rec = Rec(); s = new_session(rec)
_orig_vp = mod._vp
mod._vp = lambda: FakeVP
try:
    ret = s._do_submit(FakePage(), FakeCtx(), FakeVP, "123456")
finally:
    mod._vp = _orig_vp
check("I: _do_submit returns True (ends the command loop → session terminal)", ret is True)
check("I: _do_submit sets phase 'error' with the egress-proxy message (not 'code not accepted')",
      s.phase == "error" and "PROXY DIED" in (s.message or ""))
s._persist_diag()
check("I: the stop then persists the frame + status via _persist_diag",
      rec.shots == ["SQUID_FRAME"] and any(u.get("auth_status") == "error" for u in rec.updates))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. Router + start_session wiring (source-level — importing router.py pulls the whole app).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[4] router.py wires the shot callback to the SAME store (login_shot/login_shot_at)")

with open(os.path.join(_HERE, "..", "app", "modules", "commcalc", "router.py")) as fh:
    RT = fh.read()
with open(_LL) as fh:
    LL = fh.read()

check("router defines _live_persist_shot", "def _live_persist_shot(" in RT)
check("_live_persist_shot delegates to the existing _store_login_shot (same path/shape)",
      "_store_login_shot(client, sid, org_id, shot)" in RT)
check("_store_login_shot writes login_shot + login_shot_at (no new column invented)",
      '"login_shot": shot' in RT and '"login_shot_at"' in RT)
check("live_login_start passes _live_persist_shot into start_session",
      "_live_persist_shot(client, sid, org_id)" in RT and "live_login.start_session(" in RT)
check("live_login.start_session + __init__ accept persist_shot",
      "def start_session(sid, org_id, row, persist=None, persist_shot=None" in LL
      and "def __init__(self, sid, org_id, row, persist=None, persist_shot=None" in LL)
check("_run persists in a finally (every stop path)",
      "finally:" in LL and "self._persist_diag()" in LL)


print("\n==== %d ok, %d fail ====" % (_ok, _fail))
sys.exit(1 if _fail else 0)
