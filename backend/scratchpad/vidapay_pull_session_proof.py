"""Proof for the VidaPay/T-CETRA reuse-the-live-session Pull fix (agent/commission/vidapay-pull-session).

ROOT CAUSE (see docs/handoffs/inbox/commission-vidapay-pull-session.md). run_vidapay_sweep restores the
persisted storage_state into a BRAND-NEW browser context and navigates cold; T-CETRA does not trust that
cold restore (a fresh browser + a new egress IP + a new server session is not the trusted device) and
re-challenges 2FA — vidapay_sweep.run_vidapay_sweep line ~1806-1811 then raises "The VidaPay session has
expired". That raise fires ONLY when session_state IS present (else the earlier `if not session_state`
raises "Not authenticated yet"), which is exactly the owner's repro: a live login DID persist a session,
yet the cold Pull is asked for another code.

THE FIX. The 🔴 Live login already holds an OPEN, just-passed-2FA browser (LiveLoginSession). Keep it open
after auth and run the operator's ▶ Pull now on THAT trusted page instead of a cold restore:
  - vidapay_sweep._pull_all_reports_on_page(page, ...)   — pull core, given an authenticated page (no
    context build, no login-URL nav). Shared by run_vidapay_sweep (cold) AND the live session.
  - live_login: 'authenticated' is no longer terminal; _post_auth_loop keeps the browser open and
    services PULL (→ run_pull_blocking) + CANCEL; can_pull() gates it.
  - router.run_data_source: for vidapay/total_access, if a live session for this source is alive in this
    worker, run the pull on it; else fall back to the cold restore (unchanged).

This proof is PURE (no real portal, no DB, no real Chromium): a FakeVP drives the REAL LiveLoginSession
to 'authenticated' and we assert the post-auth reuse behavior, plus a monkeypatched pull core, plus
source-level wiring. Run:  cd backend && python3 scratchpad/vidapay_pull_session_proof.py
"""
import os
import sys
import time
import types
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import live_login as mod          # noqa: E402
from app.modules.commcalc import vidapay_sweep as vp         # noqa: E402

PASS = 0
FAIL = 0
LINES = []


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        LINES.append("  ok   " + name)
    else:
        FAIL += 1
        LINES.append("  FAIL " + name)


# ── fake playwright so LiveLoginSession._run's `with sync_playwright()` costs nothing ────────────────
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


# ── fake page/ctx/browser (page identity matters: the pull must run on the SAME page) ───────────────
class FakeEl:
    def click(self, *a, **k):
        pass

    def type(self, *a, **k):
        pass


class FakeFrame:
    pass


class FakePage:
    def __init__(self):
        self.url = "https://portal/Main%20Panel.aspx"
        self.goto_calls = 0

    def goto(self, *a, **k):
        self.goto_calls += 1

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def query_selector(self, *a, **k):
        return None

    def keyboard_type(self, *a, **k):
        pass


class FakeCtx:
    def __init__(self):
        self._page = FakePage()          # ONE page per context → identity is stable

    def new_page(self):
        return self._page


class FakeBrowser:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


_LAST_BROWSER = {}


class FakeVP:
    """Login succeeds immediately (auto-auth path → no 2FA), so _drive concludes 'authenticated'."""
    DEFAULT_URL = "https://portal/Main.aspx"
    B2BSOFT_URL = "https://b2b/"
    SESSION_TTL_HOURS = 8

    class VidaPayLoginError(Exception):
        pass

    @staticmethod
    def _launch(p):
        b = FakeBrowser()
        _LAST_BROWSER["b"] = b
        return b

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
        return (FakeFrame(), FakeEl())      # pw present → proceed to sign-in

    @staticmethod
    def drive_typed_login(page, fr, el, acc, user, pw):
        pass

    @staticmethod
    def _wait_settle(page):
        pass

    @staticmethod
    def _shot_b64(page):
        return "SHOT"

    @staticmethod
    def capture_session_state(page, ctx):
        return {"cookies": [{"name": "trust", "value": "x"}], "origins": []}

    @staticmethod
    def _classify(page):
        return "authenticated"

    @staticmethod
    def _code_field(page):
        return None

    @staticmethod
    def _page_text(page):
        return ""

    _TRUST_PAGE_WORDS = ()


class Rec:
    def __init__(self):
        self.updates = []
        self.shots = []

    def persist(self, upd):
        self.updates.append(upd)

    def persist_shot(self, shot):
        if shot:
            self.shots.append(shot)


ROW = {"portal_url": "https://portal/Main.aspx", "account_id": "a", "username": "u",
       "password": "p", "proxy_url": None, "processor": "vidapay", "carrier_id": "car1",
       "id": "src1", "months_back": 2}


def wait_phase(sess, targets, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if sess.snapshot_phase() in targets:
            return sess.snapshot_phase()
        time.sleep(0.05)
    return sess.snapshot_phase()


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print("[1] LiveLoginSession keeps the trusted browser OPEN post-auth and pulls on the SAME page")

_orig_vp = mod._vp
mod._vp = lambda: FakeVP
saved = _install_fake_playwright()
try:
    rec = Rec()
    pulled_pages = []

    def pull_fn(page):
        pulled_pages.append(page)
        return {"ok": True, "status": "pulled 7 rows across 1 report(s): ma_commission",
                "rows_ingested": 7, "authenticated": True}

    sess = mod.LiveLoginSession("src1", "orgA", ROW, rec.persist, rec.persist_shot, pull_fn)
    sess.start()
    ph = wait_phase(sess, ("authenticated", "error", "cancelled"))
    check("A: auto-auth reaches phase 'authenticated'", ph == "authenticated")
    check("A: durable session persisted on auth (session_state present)",
          any(u.get("auth_status") == "authenticated" and u.get("session_state") for u in rec.updates))

    # The KEY regression that the OLD code had: after auth the worker returned and the browser closed.
    time.sleep(0.4)
    check("B: worker thread is STILL ALIVE after auth (browser kept open for reuse)",
          sess._thread.is_alive())
    check("B: the browser was NOT closed after auth", _LAST_BROWSER["b"].closed is False)
    check("B: can_pull() is True (alive + authenticated + pull_fn set)", sess.can_pull() is True)
    # 2026-07-27 (agent/commission/vidapay-pull-after-login): authenticating now ALSO kicks the pull
    # automatically — the owner's "signs in but imports nothing" was the designed behaviour of leaving
    # the trusted session idle until a human pressed a SECOND button. So one pull has already run here.
    check("B2: authenticating auto-pulled once (no operator click needed)", len(pulled_pages) == 1)

    # ▶ Pull now → runs on the live session, returns the pull result, on the SAME live page object.
    live_page = None
    with mod._SESSIONS_LOCK:
        pass
    res = sess.run_pull_blocking(timeout=6)
    check("C: run_pull_blocking returns the live pull result (not None, not a cold restore)",
          isinstance(res, dict) and res.get("rows_ingested") == 7)
    check("C: the manual ▶ Pull now ran on top of the automatic one", len(pulled_pages) == 2)
    # the page the pull ran on is the same object the session authenticated on (reuse, not fresh ctx)
    check("C: the pull ran on the SAME page the live session holds (no cold restore / no re-nav)",
          pulled_pages and pulled_pages[0].goto_calls == 0)
    check("C: session records the last pull_result", sess.pull_result and sess.pull_result.get("rows_ingested") == 7)
    check("C: phase returns to 'authenticated' after the pull (still reusable)",
          sess.snapshot_phase() == "authenticated")

    # a SECOND pull works on the same still-open session
    res2 = sess.run_pull_blocking(timeout=6)
    check("D: a second ▶ Pull now reuses the same open session", res2 and len(pulled_pages) == 3)

    # CANCEL closes it → browser closes, thread ends, no longer pullable.
    sess.cancel()
    ph = wait_phase(sess, ("cancelled",))
    check("E: cancel → phase 'cancelled'", ph == "cancelled")
    time.sleep(0.3)
    check("E: cancel closed the browser", _LAST_BROWSER["b"].closed is True)
    check("E: a closed session can no longer pull (can_pull False → router falls back to cold restore)",
          sess.can_pull() is False and sess.run_pull_blocking(timeout=1) is None)
finally:
    mod._vp = _orig_vp
    _restore_modules(saved)


print("\n[2] can_pull gating — a session with no pull_fn (or dead) never claims the live path")
_orig_vp = mod._vp
mod._vp = lambda: FakeVP
saved = _install_fake_playwright()
try:
    rec = Rec()
    s2 = mod.LiveLoginSession("src2", "orgA", ROW, rec.persist, rec.persist_shot, None)  # no pull_fn
    s2.start()
    wait_phase(s2, ("authenticated", "error", "cancelled"))
    check("F: authenticated session WITHOUT a pull_fn → can_pull False (router uses cold restore)",
          s2.can_pull() is False and s2.run_pull_blocking(timeout=1) is None)
    s2.cancel()
    wait_phase(s2, ("cancelled",))
finally:
    mod._vp = _orig_vp
    _restore_modules(saved)


print("\n[3] 'authenticated' is NOT terminal (so an alive session isn't pruned mid-life)")
check("G: _TERMINAL excludes 'authenticated'",
      "authenticated" not in mod._TERMINAL and "cancelled" in mod._TERMINAL and "error" in mod._TERMINAL)
check("G: a post-auth idle window constant exists", isinstance(mod._POST_AUTH_IDLE_SECONDS, int)
      and mod._POST_AUTH_IDLE_SECONDS > 0)


print("\n[4] vidapay_sweep._pull_all_reports_on_page pulls per-spec on the GIVEN page (no context/nav)")
from app.modules.commcalc import report_pull as rp   # noqa: E402
_orig_specs = rp.resolve_report_specs
_orig_one = vp._pull_one_report
seen = {"pages": [], "specs": []}
try:
    rp.resolve_report_specs = lambda client, org_id, processor="vidapay", only_enabled=True: [
        {"report_key": "ma_commission", "display_name": "Commission Details"},
        {"report_key": "ma_daily_tx", "display_name": "Daily TX"}]

    # 2026-07-27: _pull_one_report gained `frame` + `options` (the Reports page is resolved ONCE per
    # pull now, and the portal's real report names are passed down for the failure message).
    def _fake_one(page, client, org_id, source_id, carrier_id, source_row, spec, start_dt, end_dt,
                  frame=None, options=None):
        seen["pages"].append(page)
        seen["specs"].append(spec.get("report_key"))
        return {"report_key": spec.get("report_key"), "ok": True, "rows_ingested": 5}
    vp._pull_one_report = _fake_one

    pg = FakePage()
    out = vp._pull_all_reports_on_page(pg, client=None, org_id="orgA", source_id="src1",
                                       carrier_id="car1", months_back=2, source_row={"id": "src1"})
    check("H: helper returns authenticated summary with summed rows",
          out.get("authenticated") is True and out.get("rows_ingested") == 10)
    check("H: helper drove EVERY resolved spec", seen["specs"] == ["ma_commission", "ma_daily_tx"])
    check("H: every report ran on the SAME given page (reuse, not per-report contexts)",
          len(seen["pages"]) == 2 and all(p is pg for p in seen["pages"]))
    check("H: helper NEVER navigated the page (no cold login-URL nav)", pg.goto_calls == 0)
finally:
    rp.resolve_report_specs = _orig_specs
    vp._pull_one_report = _orig_one


print("\n[5] source-level wiring (both paths share the pull core; router prefers the live session)")
VS = open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc", "vidapay_sweep.py")).read()
LL = open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc", "live_login.py")).read()
RT = open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc", "router.py")).read()

check("I: run_vidapay_sweep (cold path) delegates to _pull_all_reports_on_page",
      "def run_vidapay_sweep(" in VS and "return _pull_all_reports_on_page(page," in VS)
check("I: the cold path still raises the expired error on a re-challenged restore (unchanged guard)",
      'The VidaPay session has expired' in VS and 'state in ("login", "twofa", "botwall")' in VS)
check("J: live_login defines _post_auth_loop + _handle_pull + run_pull_blocking + can_pull",
      "def _post_auth_loop(" in LL and "def _handle_pull(" in LL
      and "def run_pull_blocking(" in LL and "def can_pull(" in LL)
check("J: _drive auto-auth path keeps the browser open via _post_auth_loop",
      "self._on_authenticated(page, ctx, vp)\n                self._post_auth_loop(page, ctx, vp)" in LL)
check("J: SUBMIT_CODE success routes into _post_auth_loop (not a return-and-close)",
      "return self._post_auth_loop(page, ctx, vp)" in LL)
check("K: router.run_data_source prefers the live session for vidapay/total_access",
      'proc in ("vidapay", "total_access")' in RT and "sess.run_pull_blocking" in RT
      and '"via": "live-session"' in RT)
check("K: live_login_start wires _live_pull into start_session",
      "_live_pull(client, org_id, s)" in RT and "def _live_pull(" in RT)
check("K: _live_pull runs the shared pull core on the live page",
      "vp._pull_all_reports_on_page(page," in RT)
check("K: the cold-restore fallback path is still present (scheduled pulls / other workers)",
      "res = await handler(org_id, src_row)" in RT)

print("\n".join(LINES))
print("\n=== vidapay_pull_session proof: %d/%d PASS ===" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
