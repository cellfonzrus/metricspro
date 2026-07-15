"""Persistent LIVE portal-login session manager (VidaPay / T-CETRA new-device 2FA).

THE PROBLEM this fixes. The two-call state machine in vidapay_sweep.py (begin_login → complete_2fa)
opens a FRESH browser for each call. On VidaPay/T-CETRA's NEW-DEVICE 2FA, begin_login logs in and
clicks "New Sign In → Next", which DISPATCHES code #1, then saves storage_state; complete_2fa restores
that state in a SECOND browser and re-navigates, so the portal re-issues code #2 and invalidates the
code the operator just typed ("it sent another code twice"). The challenge is stateful — you cannot fix
it by replaying storage_state.

THE FIX (this module). ONE browser stays ALIVE from login through code entry. A dedicated worker THREAD
runs a single `sync_playwright()` for the session's whole life (NOT the per-call `with` that closes it),
drives the login to the code-entry screen — clicking "New Sign In → Next" EXACTLY ONCE so the code is
sent ONCE — then idles, capturing a screenshot every ~1.5s into a shared buffer. The operator watches
that live screenshot stream and submits the code / resends / cancels against the SAME live page: no
re-navigation, no resend, so the code the operator types goes into the very page that requested it.

DESIGN.
  - `_SESSIONS[sid] -> LiveLoginSession`, keyed by data_source id. Org-scoped: a session is bound to
    its source's org_id and `get_session(sid, org_id)` refuses a mismatched tenant.
  - The worker thread consumes a command queue: (implicit START) → SUBMIT_CODE(code) → RESEND → CANCEL.
  - All Playwright calls happen ON the worker thread only. The request thread just enqueues commands and
    reads shared state (phase / message / latest screenshot) under a lock.
  - Phases surfaced to the UI: starting | login | awaiting_code | verifying | authenticated | error |
    cancelled — each with a human message + the latest JPEG.

OPERATOR CAVEAT (documented in the handoff): the live session lives in ONE worker PROCESS's memory, so
the backend must run a SINGLE uvicorn worker (Railway's FastAPI default is 1) or start + submit could hit
different workers. This is best-effort headless-in-a-thread; if the host kills long-lived threads, the
fallback is the existing begin_login/complete_2fa path (left intact).
"""
import threading
import queue
import time
from datetime import datetime, timezone, timedelta

# Registry of live sessions, keyed by data_source id. Guarded by _SESSIONS_LOCK.
_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()

_SHOT_INTERVAL = 1.5           # seconds between idle screenshot captures
_IDLE_CLOSE_SECONDS = 20 * 60  # auto-close a session left idle this long (long enough to fetch a code)
_TERMINAL = ("authenticated", "error", "cancelled")
_TERMINAL_TTL = 15 * 60       # keep a finished session's final state this long, then prune

_B2B_PROCESSORS = ("b2bsoft", "b2b")


def _vp():
    """Lazy import of the driver library (keeps Playwright import cost off module load)."""
    from app.modules.commcalc import vidapay_sweep as vp
    return vp


def _wall_note(vp, page):
    try:
        if vp._looks_like_bot_wall(page):
            return "The portal served an anti-automation page — route this source through a residential proxy."
    except Exception:
        pass
    return "The login form may render differently than expected — check the screenshot."


class LiveLoginSession:
    """One persistent browser-backed login session. Owns a single worker thread for its whole life."""

    def __init__(self, sid, org_id, row, persist=None):
        self.sid = sid
        self.org_id = org_id
        self.url = row.get("portal_url")
        self.account_id = row.get("account_id")
        self.user = row.get("username")
        self.pw = row.get("password")
        self.proxy_url = row.get("proxy_url")
        self.processor = (row.get("processor") or "").strip().lower()
        self._is_b2b = self.processor in _B2B_PROCESSORS
        self.persist = persist                    # callable(updates: dict) -> persists to data_source
        self.session_state = None                 # durable storage_state once authenticated

        self._cmd_q = queue.Queue()
        self._lock = threading.Lock()
        self.phase = "starting"
        self.message = "Starting the live login session…"
        self._shot = None                         # latest base64 JPEG (no data-uri prefix)
        self._updated_at = datetime.now(timezone.utc).isoformat()
        self._last_activity = time.time()
        self._finished_at = None
        self._thread = None

    # ── thread-safe shared-state access ──────────────────────────────────────────────────────────
    def _set(self, phase=None, message=None, touch=True):
        with self._lock:
            if phase is not None:
                self.phase = phase
                if phase in _TERMINAL:
                    self._finished_at = time.time()
            if message is not None:
                self.message = message
            self._updated_at = datetime.now(timezone.utc).isoformat()
            if touch:
                self._last_activity = time.time()

    def _touch(self):
        with self._lock:
            self._last_activity = time.time()

    def _capture(self, page):
        try:
            shot = _vp()._shot_b64(page)
        except Exception:
            shot = None
        if shot:
            with self._lock:
                self._shot = shot
                self._updated_at = datetime.now(timezone.utc).isoformat()

    def snapshot_phase(self):
        with self._lock:
            return self.phase

    def state(self):
        with self._lock:
            return {
                "phase": self.phase,
                "message": self.message,
                "shot": ("data:image/jpeg;base64," + self._shot) if self._shot else None,
                "updated_at": self._updated_at,
            }

    def is_terminal(self):
        with self._lock:
            return self.phase in _TERMINAL

    def finished_age(self):
        with self._lock:
            return (time.time() - self._finished_at) if self._finished_at else None

    # ── operator commands (called from the request thread; only enqueue) ─────────────────────────
    def submit(self, code):
        self._touch()
        self._cmd_q.put(("SUBMIT_CODE", str(code)))

    def resend(self):
        self._touch()
        self._cmd_q.put(("RESEND",))

    def click(self, nx, ny):
        """Forward an operator click at NORMALIZED coords (0..1 of the streamed image) to the live page —
        this is the 'take control' path so the operator can press a button (e.g. Next) the auto-clicker
        missed."""
        self._touch()
        self._cmd_q.put(("CLICK", float(nx), float(ny)))

    def type_text(self, text):
        self._touch()
        self._cmd_q.put(("TYPE", str(text)))

    def cancel(self):
        self._touch()
        self._cmd_q.put(("CANCEL",))

    # ── worker thread ────────────────────────────────────────────────────────────────────────────
    def start(self):
        self._thread = threading.Thread(target=self._run, name="live-login-%s" % self.sid, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            self._set(phase="error",
                      message="Playwright/Chromium is not available in the backend image: " + str(e)[:200])
            return
        try:
            # ONE sync_playwright for the whole session life — the browser/context/page stay OPEN across
            # the command loop (do NOT use a per-call `with` that closes it between calls).
            with sync_playwright() as p:
                self._drive(p)
        except Exception as e:
            self._set(phase="error", message="Live login crashed: " + str(e)[:300])

    def _drive(self, p):
        vp = _vp()
        browser = None
        try:
            browser = vp._launch(p)
            ctx = vp._new_context(browser, proxy=vp._proxy_arg(self.proxy_url))
            page = ctx.new_page()
            self._set(phase="login", message="Opening the portal…")
            self._capture(page)
            base = vp._norm_url(self.url, vp.B2BSOFT_URL if self._is_b2b else vp.DEFAULT_URL)
            try:
                vp._goto_login(page, base)
            except vp.VidaPayLoginError as e:
                msg = str(e)
                if "egress" in msg.lower() or "waf" in msg.lower():
                    try:
                        msg += vp.egress_hint(self.proxy_url)
                    except Exception:
                        pass
                self._set(phase="error", message=msg[:400])
                self._capture(page)
                return

            # A progressive access-code (#companyId) step may precede the password field (b2bsoft / kin).
            if not vp._password_frame(page)[1]:
                self._companyid_step(page, vp)

            login_fr, pw_el = vp._password_frame(page)
            if not pw_el:
                self._set(phase="error", message="Could not find the login form. " + _wall_note(vp, page))
                self._capture(page)
                return

            self._set(phase="login", message="Signing in…")
            self._capture(page)
            # Fill + submit using the SHARED pinned/heuristic driver (identical to begin_login).
            vp.drive_typed_login(page, login_fr, pw_el, self.account_id, self.user, self.pw)
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            try:
                page.wait_for_timeout(3500)
            except Exception:
                pass
            try:
                vp._wait_settle(page)
            except Exception:
                pass
            self._capture(page)

            state = vp._classify(page)
            if state == "authenticated":
                self._on_authenticated(page, ctx, vp)
                return
            if state == "botwall":
                self._set(phase="error",
                          message="The portal served an anti-automation page after login — route through a residential proxy.")
                self._capture(page)
                return
            if state == "login":
                self._set(phase="error",
                          message="Login was rejected — Account ID / User ID / Password not accepted.")
                self._capture(page)
                return

            # 2FA: click THROUGH the pre-code steps (device interstitial → method chooser) EXACTLY ONCE,
            # dispatching the code a SINGLE time. The page then stays OPEN on the code screen; the code
            # the operator types goes into THIS page (no second dispatch → no "code sent twice").
            if not vp._code_field(page):
                try:
                    vp._advance_2fa(page)
                except Exception:
                    pass
            self._capture(page)
            if vp._code_field(page):
                self._set(phase="awaiting_code",
                          message="Enter the code the portal just sent to the registered phone/email.")
            else:
                self._set(phase="awaiting_code",
                          message="Reached the verification step — if no code box appears, use the screenshot "
                                  "to see what the portal is waiting on, then Resend or Cancel.")
            self._capture(page)
            self._command_loop(page, ctx, vp)
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    def _companyid_step(self, page, vp):
        """b2bsoft/progressive first page: fill the access code (#companyId) and continue to reveal the
        password field. Best-effort — a portal without it is untouched."""
        try:
            cid = page.query_selector("#companyId")
        except Exception:
            cid = None
        if not (cid and self.account_id):
            return
        try:
            cid.click()
            cid.fill("")
            cid.type(str(self.account_id), delay=15)
        except Exception:
            try:
                cid.fill(str(self.account_id))
            except Exception:
                pass
        try:
            b = page.query_selector("#btnSubmit")
            if b:
                b.click()
        except Exception:
            pass
        try:
            page.wait_for_selector("#password", state="visible", timeout=15000)
        except Exception:
            pass
        try:
            vp._wait_settle(page)
        except Exception:
            pass

    def _command_loop(self, page, ctx, vp):
        """Idle on the code screen: refresh the screenshot every ~1.5s and service operator commands.
        Auto-close after ~8 min idle. All Playwright work stays on this (the worker) thread."""
        last_shot = 0.0
        while True:
            now = time.time()
            if now - last_shot >= _SHOT_INTERVAL:
                self._capture(page)
                last_shot = now
            with self._lock:
                idle = time.time() - self._last_activity
            if idle > _IDLE_CLOSE_SECONDS:
                self._set(phase="cancelled", message="Live session closed after inactivity.")
                return
            try:
                cmd = self._cmd_q.get(timeout=0.5)
            except queue.Empty:
                continue
            kind = cmd[0]
            if kind == "CANCEL":
                self._set(phase="cancelled", message="Cancelled by the operator.")
                return
            if kind == "RESEND":
                self._do_resend(page, vp)
                last_shot = 0.0
                continue
            if kind == "CLICK":
                self._do_click(page, ctx, vp, cmd[1], cmd[2])
                if self.snapshot_phase() == "authenticated":
                    return           # the operator's click finished sign-in
                last_shot = 0.0
                continue
            if kind == "TYPE":
                try:
                    page.keyboard.type(cmd[1], delay=20)
                except Exception:
                    pass
                self._capture(page)
                last_shot = 0.0
                continue
            if kind == "SUBMIT_CODE":
                if self._do_submit(page, ctx, vp, cmd[1]):
                    return           # authenticated → session done
                last_shot = 0.0

    def _do_click(self, page, ctx, vp, nx, ny):
        """'Take control': translate a normalized (0..1) click on the streamed image to a real click on
        the live page at the matching viewport pixel, then re-check whether we've landed authenticated
        (so an operator clicking the portal's Next / Trust button completes + saves the session)."""
        try:
            vp_size = page.viewport_size or {"width": 1366, "height": 900}
            x = max(0.0, min(1.0, nx)) * vp_size["width"]
            y = max(0.0, min(1.0, ny)) * vp_size["height"]
            page.mouse.click(x, y)
        except Exception as e:
            self._set(message="Click didn't register (%s) — try again." % str(e)[:80])
            self._capture(page)
            return
        try:
            page.wait_for_timeout(1200)
            vp._wait_settle(page)
        except Exception:
            pass
        self._capture(page)
        # A click on Next/Trust may finish sign-in — but only conclude auth once the trust page is gone.
        try:
            on_trust = any(w in vp._page_text(page) for w in vp._TRUST_PAGE_WORDS)
            if not on_trust and not vp._code_field(page) and vp._classify(page) == "authenticated":
                self._on_authenticated(page, ctx, vp)
        except Exception:
            pass

    def _do_resend(self, page, vp):
        """Click the LIVE page's resend / send-code control — NO re-login, NO re-navigation."""
        self._set(phase="awaiting_code", message="Requesting a new code…")
        try:
            clicked = vp._trigger_2fa_send(page)
        except Exception:
            clicked = None
        try:
            page.wait_for_timeout(1500)
            vp._wait_settle(page)
        except Exception:
            pass
        self._capture(page)
        if clicked:
            self._set(phase="awaiting_code",
                      message="A new code was requested — enter the LATEST code (older ones are now void).")
        else:
            self._set(phase="awaiting_code",
                      message="No resend control was found on the page — enter the code already sent, or Cancel and start over.")
        self._capture(page)

    def _do_submit(self, page, ctx, vp, code):
        """Fill the code into the LIVE code field, select the trust radio, click Verify. On success,
        persist the durable session and stop. On failure, KEEP the page open on the verification screen
        (phase back to awaiting_code) so a fresh code can be entered — never re-run the login."""
        self._set(phase="verifying", message="Verifying the code…")
        self._capture(page)
        code_el, code_fr = None, None
        for fr in vp._frames(page):
            el = vp._find_input(fr, kinds=("text", "tel", "number", "password"),
                                want=("code", "otp", "pin", "verif", "token", "authenticat",
                                      "2fa", "one-time", "onetime", "passcode"))
            if el:
                code_el, code_fr = el, fr
                break
        if not code_el:                                  # lone-field fallback
            for fr in vp._frames(page):
                el = vp._find_input(fr, kinds=("text", "tel", "number"))
                if el:
                    code_el, code_fr = el, fr
                    break
        if not code_el:
            self._set(phase="awaiting_code",
                      message="Could not find the code box — check the screenshot, or Cancel and restart.")
            self._capture(page)
            return False
        try:
            code_el.fill(str(code).strip())
        except Exception:
            pass
        try:
            vp._tick_remember(code_fr)                   # select the REQUIRED "trust this device" radio
        except Exception:
            pass
        if not vp._click_submit(code_fr, ("verify", "submit", "continue", "confirm", "log in", "sign in")):
            try:
                code_el.press("Enter")
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(3000)
        except Exception:
            pass
        try:
            vp._wait_settle(page)
        except Exception:
            pass
        self._capture(page)
        # Click through the post-code "Trust This Device" page (nickname + Next) — that Next both
        # finalizes the 90-day trust AND is what leads to the dashboard; without it the accepted code
        # looks rejected. on_step keeps the live screenshot refreshing so the viewer doesn't freeze.
        self._set(phase="verifying", message="Finishing sign-in (trusting this device)…")
        state = vp.finalize_after_code(page, on_step=lambda: self._capture(page))
        self._capture(page)
        if state == "authenticated":
            self._on_authenticated(page, ctx, vp)
            return True
        # If the auto-clicker couldn't finish the "Trust This Device" page, tell the operator to click
        # the Next button themselves in the live view (click-forwarding is enabled) — the code WAS
        # accepted; only the trust step remains. Otherwise it's a genuine code rejection.
        try:
            on_trust = any(w in vp._page_text(page) for w in vp._TRUST_PAGE_WORDS)
        except Exception:
            on_trust = False
        if on_trust:
            self._set(phase="action_needed",
                      message="Code accepted! One step left — click the blue Next button in the view "
                              "above to finish trusting this device.")
        else:
            self._set(phase="awaiting_code",
                      message="That code was not accepted (it may have expired). Enter the LATEST code, or ↻ Resend.")
        self._capture(page)
        return False

    def _on_authenticated(self, page, ctx, vp):
        try:
            # capture_session_state ALSO stashes sessionStorage — VidaPay/T-CETRA keeps its OIDC token
            # there, and a session saved without it dies the moment the report Pull restores it.
            st = vp.capture_session_state(page, ctx)
        except Exception:
            st = None
        now = datetime.now(timezone.utc)
        exp = now + timedelta(hours=getattr(vp, "SESSION_TTL_HOURS", 8))
        self.session_state = st
        if st is not None and self.persist:
            try:
                self.persist({
                    "auth_status": "authenticated",
                    "auth_message": "Signed in via the live session — session saved.",
                    "session_state": st, "pending_state": None, "pending_started_at": None,
                    "session_expires_at": exp.isoformat(), "last_run_at": now.isoformat(),
                })
            except Exception:
                pass
        self._set(phase="authenticated",
                  message="Signed in — the session is saved and will be reused until it expires.")
        self._capture(page)


# ── registry API (used by router.py; org-scoped) ─────────────────────────────────────────────────
def _prune_locked():
    """Drop finished sessions whose final state has aged out. Caller holds _SESSIONS_LOCK."""
    dead = [k for k, s in _SESSIONS.items()
            if s.is_terminal() and (s.finished_age() or 0) > _TERMINAL_TTL]
    for k in dead:
        _SESSIONS.pop(k, None)


def start_session(sid, org_id, row, persist=None):
    """Spawn (or REPLACE) the live session for `sid`. Non-blocking — the worker thread drives it."""
    with _SESSIONS_LOCK:
        _prune_locked()
        old = _SESSIONS.get(sid)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass
        sess = LiveLoginSession(sid, org_id, row, persist)
        _SESSIONS[sid] = sess
    sess.start()
    return sess


def get_session(sid, org_id):
    """The live session for `sid`, but ONLY if it belongs to `org_id` (tenant isolation)."""
    with _SESSIONS_LOCK:
        _prune_locked()
        sess = _SESSIONS.get(sid)
        if sess is None or sess.org_id != org_id:
            return None
        return sess
