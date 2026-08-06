"""Persistent LIVE portal-login session manager (VidaPay / T-CETRA new-device 2FA + reCAPTCHA).

THE PROBLEM this fixes. The two-call state machine in vidapay_sweep.py (begin_login → complete_2fa)
opens a FRESH browser for each call. On VidaPay/T-CETRA's NEW-DEVICE 2FA, begin_login logs in and
clicks "New Sign In → Next", which DISPATCHES code #1, then saves storage_state; complete_2fa restores
that state in a SECOND browser and re-navigates, so the portal re-issues code #2 and invalidates the
code the operator just typed ("it sent another code twice"). The challenge is stateful — you cannot fix
it by replaying storage_state.

THE FIX (this module). ONE browser stays ALIVE from login through code entry. A dedicated worker THREAD
runs a single `sync_playwright()` for the session's whole life (NOT the per-call `with` that closes it).
The operator watches a LOW-LATENCY live stream of that browser and clicks / types / submits against the
SAME live page: no re-navigation, no resend, so the code the operator types goes into the very page that
requested it.

HUMAN-DRIVEN UNTIL AUTHENTICATED (owner directive 2026-07-16 — "let it be alive human interaction till
the login happens"). From session start until authentication succeeds the live view is a continuously
interactive human session. The machine's job pre-auth is to (1) render frames FAST, (2) forward the
human's clicks/typing INSTANTLY, and (3) DETECT state (captcha present, 2FA screen, authenticated, proxy
error) for the status line — but NOT to race the human or classify a human-paced login as a
failure/rejection. The existing auto-drive (fill + submit + advance the 2FA once) remains as a FAST-PATH
when NO captcha is detected and the human hasn't touched anything; the FIRST human input pauses
auto-driving for the rest of the pre-auth phase, and a captcha ("I'm not a robot") is never auto-submitted
past — it flips to a `human_action` phase and waits for the human. Once `_classify` sees authenticated,
behaviour is exactly as before: persist the durable session and enter the post-auth reuse-the-session
PULL loop.

LOW-LATENCY FRAMES. Instead of a ~1.5s `page.screenshot()` poll, the worker starts a CDP screencast
(`page.context.new_cdp_session(page)` → `Page.startScreencast`, JPEG frames pushed at the browser's paint
cadence and acked on the worker thread) and streams them into the session's shot buffer, bumping a
monotonic `_seq`. If the CDP session can't start it falls back to a tightened ~300ms screenshot loop. The
UI polls a lightweight `GET /live-login/frame?since=<seq>` (~300ms) that returns a new JPEG only when the
seq advanced (else a tiny unchanged payload). Explicit screenshots at every phase transition + a ≤1.5s
liveness guarantee mean the view never freezes even if the screencast stalls.

IMMEDIATE INPUT. `POST /live-login/{sid}/input` {type: click|dblclick|type|key|scroll, x, y, text, key,
deltaY} is enqueued on a HIGH-priority queue (drained before SUBMIT_CODE/RESEND/PULL) and executed on the
live page. Click coords are NORMALIZED (0..1 of the streamed image) and multiplied by the live viewport
size server-side (DPR-proof — the img is rendered smaller than the real viewport).

DESIGN.
  - `_SESSIONS[sid] -> LiveLoginSession`, keyed by data_source id. Org-scoped: a session is bound to
    its source's org_id and `get_session(sid, org_id)` refuses a mismatched tenant.
  - Two queues: `_hi_q` (human input — high priority) and `_cmd_q` (SUBMIT_CODE / RESEND / CANCEL / PULL).
  - All Playwright calls happen ON the worker thread only. The request thread just enqueues commands and
    reads shared state (phase / message / latest frame / seq) under a lock.
  - Phases surfaced to the UI: starting | login | human_action | awaiting_code | verifying |
    action_needed | authenticated | pulling | error | cancelled.

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

_SHOT_INTERVAL = 0.7           # legacy idle capture cadence (kept for the post-auth slow path)
_IDLE_CLOSE_SECONDS = 20 * 60  # auto-close a session left idle this long (long enough to fetch a code)
_HUMAN_ACTION_IDLE_SECONDS = 8 * 60  # a human_action (captcha) phase left untouched this long closes cleanly
# 'authenticated' is deliberately NOT terminal: after sign-in the browser stays OPEN (see _post_auth_loop)
# so a '▶ Pull now' can run on the SAME trusted page. The session becomes terminal only on error/cancel
# (incl. the post-auth idle timeout, which flips it to 'cancelled').
_TERMINAL = ("error", "cancelled")
_TERMINAL_TTL = 15 * 60       # keep a finished session's final state this long, then prune
_POST_AUTH_IDLE_SECONDS = 15 * 60  # keep the authenticated browser open this long for a reuse-the-session pull

# Low-latency frame streaming.
_SCREENCAST_QUALITY = 55       # JPEG quality for the CDP screencast (viewport-sized)
_SCREENCAST_MAX_W = 1366
_SCREENCAST_MAX_H = 900
_FRAME_PUMP_MS = 80            # CDP mode: pump the event loop this often so screencast frames dispatch (~12/s)
_FALLBACK_PUMP_MS = 300        # no-CDP mode: tightened screenshot loop (~3.3/s vs the old ~0.67/s)
_FRAME_GUARANTEE_S = 1.5       # even in CDP mode, force a screenshot if no frame advanced this long (never freeze)
_CLASSIFY_POLL_S = 1.2         # pre-auth: how often to re-DETECT state (captcha / auth / proxy) for the status

_B2B_PROCESSORS = ("b2bsoft", "b2b")


def _takes_stop(fn):
    """True when `fn` can accept a second (should_stop) argument. Arity is inspected once per call
    site; anything unintrospectable is treated as one-arg (the safe, pre-existing behaviour)."""
    try:
        import inspect
        params = list(inspect.signature(fn).parameters.values())
        if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params):
            return True
        positional = [p for p in params
                      if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                    inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        return len(positional) >= 2
    except Exception:
        return False


def _delivered(res):
    """Did this pull actually LAND rows? PURE. Mirrors router._pull_delivered so the live session's
    message, the data_source stamp and the UI can never disagree about what 'success' means. An explicit
    `delivered` flag wins; then any row-count key; unknown shape ⇒ True (never regress a driver that
    doesn't report counts)."""
    if not isinstance(res, dict):
        return False
    if res.get("ok") is False:
        return False
    if "delivered" in res:
        return bool(res.get("delivered"))
    for k in ("rows_ingested", "rows_saved", "saved", "rows"):
        if k in res:
            try:
                return float(res[k] or 0) > 0
            except Exception:
                return True
    return True


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


def _captcha_present(vp, page):
    """Best-effort captcha detection that never raises (the driver may lack the helper in a fake)."""
    try:
        return bool(vp._looks_like_captcha(page))
    except Exception:
        return False


def _clean_err(msg):
    """Strip Playwright's raw 'Call log:' block (and everything after) from an exception string so an
    operator NEVER sees the internal trace ("... Call log: - navigating to ... waiting until
    domcontentloaded"). Keeps the text UP TO the marker; a no-op when there is no call log. Module-level
    (no vp dependency) so both _run's crash handler and _drive's goto guard can use it."""
    s = str(msg or "")
    i = s.find("Call log:")
    if i != -1:
        s = s[:i]
    return s.strip()


def _recover_proxy(vp, page, dest_url=None):
    """Attempt the http→https squid recovery via the driver — a fresh GET re-navigation (v2: the https twin,
    then a DIRECT goto of the known-good `dest_url`). It NEVER re-submits a form / resends a 2FA code.
    Best-effort; a fake vp may lack the helper (or the dest_url kwarg). Returns True if the egress squid page
    cleared (the caller proceeds as if it never appeared), else False."""
    try:
        return bool(vp._recover_from_proxy_error(page, dest_url=dest_url))
    except TypeError:
        try:
            return bool(vp._recover_from_proxy_error(page))     # older driver without the dest_url kwarg
        except Exception:
            return False
    except Exception:
        return False


def _squid_reported(vp, page):
    """The URL squid reported (for the friendly message / diag), or None. Best-effort; never raises."""
    try:
        return vp._squid_reported_url(page)
    except Exception:
        return None


class LiveLoginSession:
    """One persistent browser-backed login session. Owns a single worker thread for its whole life."""

    def __init__(self, sid, org_id, row, persist=None, persist_shot=None, pull_fn=None,
                 persist_pull=None, auto_pull_gate=None):
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
        self.persist_shot = persist_shot          # callable(shot_b64) -> writes login_shot/login_shot_at
        self.pull_fn = pull_fn                     # callable(page) -> pull result; runs on THIS live page
        self.persist_pull = persist_pull           # callable(result) -> stamps the pull's outcome honestly
        self.pull_result = None                    # last pull result (auto-pull or '▶ Pull now')
        self.session_state = None                 # durable storage_state once authenticated
        # AUTO-PULL AFTER LOGIN (owner report 2026-07-27 — "shows logged in ... does not import any
        # file"). Signing in used to leave the trusted browser idling until a human clicked a SECOND
        # button (▶ Pull now); if they didn't, the session simply expired and nothing was ever imported.
        # Per-source switch (data_source.auto_pull_after_login, mig 242), default ON; a row without the
        # column (pre-migration) reads None → ON.
        self.auto_pull = (row.get("auto_pull_after_login") is not False)
        # PORTAL COOLDOWN GATE (mig 244). callable() -> portal_backoff.read_state(); when it reports
        # blocked=True the AUTOMATIC post-login pull is skipped. Only the automatic one: a HUMAN who
        # explicitly confirms "yes, try anyway" still reaches ▶ Pull now, which has its own confirm gate.
        # None (every existing caller / test) ⇒ never blocked, i.e. byte-identical behaviour.
        self.auto_pull_gate = auto_pull_gate
        self._auto_pulled = False
        self._cancel_flag = False                 # set by cancel(); a running pull checks it between reports

        self._cmd_q = queue.Queue()               # SUBMIT_CODE / RESEND / CANCEL / PULL
        self._hi_q = queue.Queue()                # HIGH-priority human input (click / type / key / scroll)
        self._lock = threading.Lock()
        self.phase = "starting"
        self.message = "Starting the live login session…"
        self._shot = None                         # latest base64 JPEG (no data-uri prefix)
        self._seq = 0                             # monotonic frame sequence — bumped on every new frame
        self._last_frame_at = 0.0                 # wall time of the last frame stored (liveness guarantee)
        self._human_driving = False               # True once the human sends ANY input → pauses auto-drive
        self._cdp = None                          # the CDP screencast session (None if unavailable)
        self._screencast_on = False
        self._pending_ack = None                  # sessionId of the newest screencast frame awaiting ack
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

    def _store_frame(self, shot):
        """Record a new frame (from the CDP screencast OR an explicit screenshot) + bump the sequence."""
        if not shot:
            return
        with self._lock:
            self._shot = shot
            self._seq += 1
            self._last_frame_at = time.time()
            self._updated_at = datetime.now(timezone.utc).isoformat()

    def _capture(self, page):
        """Take a real screenshot NOW (used at phase transitions + as the fallback / liveness guarantee)."""
        try:
            shot = _vp()._shot_b64(page)
        except Exception:
            shot = None
        if shot:
            self._store_frame(shot)

    def _persist_diag(self):
        """Persist the LAST live frame + a status line to the SAME data_source diag store the two-call
        begin_login/complete_2fa failures write — the frame via persist_shot (→ login_shot/login_shot_at,
        the store '📷 What the browser saw' reads) and the status line via persist (auth_message, plus
        auth_status='error' on a hard stop, mirroring _do_portal_login's failure shape). This runs at
        EVERY stop of the session — proxy_error, auth failure, idle timeout, operator Close, or a crash —
        so the panel reflects THIS live session's final screen instead of a stale earlier attempt's frame.
        Best-effort / never raises, like _store_login_shot and the other diag writes. 'authenticated' owns
        its own status via _on_authenticated, so only the frame is refreshed there (not the status line)."""
        try:
            with self._lock:
                phase, message, shot = self.phase, self.message, self._shot
            if self.persist and phase in ("error", "cancelled"):
                upd = {"auth_message": (message or "")[:400],
                       "last_run_at": datetime.now(timezone.utc).isoformat()}
                if phase == "error":
                    upd["auth_status"] = "error"
                try:
                    self.persist(upd)
                except Exception:
                    pass
            if shot and self.persist_shot:
                try:
                    self.persist_shot(shot)
                except Exception:
                    pass
        except Exception:
            pass

    def snapshot_phase(self):
        with self._lock:
            return self.phase

    def snapshot_seq(self):
        with self._lock:
            return self._seq

    def state(self):
        with self._lock:
            pr = self.pull_result
            return {
                "phase": self.phase,
                "message": self.message,
                "shot": ("data:image/jpeg;base64," + self._shot) if self._shot else None,
                "seq": self._seq,
                "human": self._human_driving,
                "updated_at": self._updated_at,
                # The pull outcome the UI must render HONESTLY (a 0-row import is never a green tick).
                # Trimmed to the summary fields — the full per-report diagnostic is served by
                # GET /data-sources/{sid}/pull-diagnostic, not by this ~1s poll.
                "pull": ({"ran": True,
                          "delivered": bool(_delivered(pr)),
                          "rows": (pr.get("rows_ingested") if isinstance(pr, dict) else None),
                          "status": (pr.get("status") or pr.get("error") or "")[:400]
                                    if isinstance(pr, dict) else "",
                          "reason": (pr.get("reason") if isinstance(pr, dict) else None),
                          "options": ((pr.get("calibration") or {}).get("portal_report_options") or [])[:20]
                                     if isinstance(pr, dict) else []}
                         if pr is not None else
                         {"ran": False, "delivered": None, "rows": None, "status": "", "reason": None,
                          "options": []}),
                "auto_pull": self.auto_pull,
            }

    def frame_since(self, since):
        """Lightweight frame poll: return the newest frame ONLY if `_seq` advanced past `since`, else a tiny
        unchanged payload (phase/message always included so the panel stays fresh without shipping a JPEG)."""
        try:
            since = int(since)
        except Exception:
            since = 0
        with self._lock:
            seq, phase, message, shot = self._seq, self.phase, self.message, self._shot
        if shot and seq > since:
            return {"seq": seq, "phase": phase, "message": message, "changed": True,
                    "shot": "data:image/jpeg;base64," + shot}
        return {"seq": seq, "phase": phase, "message": message, "changed": False, "shot": None}

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

    def input_event(self, ev):
        """Forward a raw human input event (click / dblclick / type / key / scroll) to the live page with
        HIGH priority. The FIRST input pauses auto-driving for the rest of the pre-auth phase (human wins)."""
        with self._lock:
            self._human_driving = True
            self._last_activity = time.time()
        self._hi_q.put(("INPUT", dict(ev or {})))

    def click(self, nx, ny):
        """Back-compat single-click affordance (the legacy /live-login/click endpoint)."""
        self.input_event({"type": "click", "x": nx, "y": ny})

    def type_text(self, text):
        self.input_event({"type": "type", "text": str(text)})

    def cancel(self):
        self._touch()
        with self._lock:
            self._cancel_flag = True   # a pull in flight stops at its next report boundary
        self._cmd_q.put(("CANCEL",))

    def _stop_requested(self):
        with self._lock:
            return self._cancel_flag

    def pull(self, result_q=None):
        """Enqueue a report pull to run on THIS live authenticated browser (not a cold restore)."""
        self._touch()
        self._cmd_q.put(("PULL", result_q))

    def can_pull(self):
        """True only when the worker is alive AND we're post-authentication (browser still open) AND a
        pull_fn is configured — so the router can decide to reuse the live session instead of a cold
        storage_state restore. A dead/aged-out session returns False → the router falls back cleanly."""
        with self._lock:
            ok_phase = self.phase in ("authenticated", "pulling")
        alive = bool(self._thread and self._thread.is_alive())
        return ok_phase and alive and self.pull_fn is not None

    def run_pull_blocking(self, timeout=900):
        """Called from the request thread: enqueue a pull on the live session and block for its result.
        Returns the pull result dict, or None if the session can't pull / times out (caller then falls
        back to the cold-restore path). All Playwright work still happens on the worker thread."""
        if not self.can_pull():
            return None
        rq = queue.Queue(maxsize=1)
        self.pull(rq)
        try:
            return rq.get(timeout=timeout)
        except queue.Empty:
            return None

    def _next_cmd(self):
        """Pop the next command, HIGH-priority input first (so a click never waits behind a SUBMIT_CODE)."""
        try:
            return self._hi_q.get_nowait()
        except queue.Empty:
            pass
        try:
            return self._cmd_q.get_nowait()
        except queue.Empty:
            return None

    # ── low-latency frame plumbing ────────────────────────────────────────────────────────────────
    def _start_screencast(self, page):
        """Start a CDP screencast on the live page (Chromium only, which _launch guarantees). Frames stream
        into `_shot` via the on-frame handler; the ack is deferred to the worker thread (`_flush_ack`) so we
        never call a Playwright method from inside an event callback. Silently no-ops if CDP is unavailable —
        the worker then falls back to a tightened screenshot loop."""
        try:
            cdp = page.context.new_cdp_session(page)
        except Exception:
            self._cdp = None
            self._screencast_on = False
            return

        def _on_frame(params):
            try:
                data = params.get("data") if isinstance(params, dict) else None
                fsid = params.get("sessionId") if isinstance(params, dict) else None
            except Exception:
                data, fsid = None, None
            if data:
                self._store_frame(data)
            if fsid is not None:
                with self._lock:
                    self._pending_ack = fsid

        try:
            cdp.on("Page.screencastFrame", _on_frame)
            cdp.send("Page.startScreencast", {
                "format": "jpeg", "quality": _SCREENCAST_QUALITY,
                "maxWidth": _SCREENCAST_MAX_W, "maxHeight": _SCREENCAST_MAX_H, "everyNthFrame": 1})
            self._cdp = cdp
            self._screencast_on = True
        except Exception:
            self._cdp = None
            self._screencast_on = False

    def _flush_ack(self):
        """Ack the newest screencast frame (on the worker thread) so Chrome keeps sending frames."""
        cdp = self._cdp
        if cdp is None:
            return
        with self._lock:
            fsid = self._pending_ack
            self._pending_ack = None
        if fsid is None:
            return
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": fsid})
        except Exception:
            pass

    def _stop_screencast(self):
        cdp = self._cdp
        self._cdp = None
        self._screencast_on = False
        if cdp is None:
            return
        try:
            cdp.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            cdp.detach()
        except Exception:
            pass

    def _pump(self, page):
        """Drive the live frame stream for one tick. In CDP mode this pumps the event loop (~80ms) so
        screencast frames dispatch, flushes the ack, and takes a guarantee screenshot only if the stream
        stalled. Without CDP it takes a screenshot every ~300ms (tightened fallback). A tiny sleep floor
        keeps a no-op `wait_for_timeout` (e.g. a test fake) from hot-looping."""
        pm = _FRAME_PUMP_MS if self._screencast_on else _FALLBACK_PUMP_MS
        t0 = time.time()
        try:
            page.wait_for_timeout(pm)
        except Exception:
            pass
        if self._screencast_on:
            self._flush_ack()
            if time.time() - self._last_frame_at > _FRAME_GUARANTEE_S:
                self._capture(page)
        else:
            self._capture(page)
        if time.time() - t0 < 0.02:      # wait_for_timeout was a no-op → throttle so we don't spin
            time.sleep(0.03)

    # ── worker thread ────────────────────────────────────────────────────────────────────────────
    def start(self):
        self._thread = threading.Thread(target=self._run, name="live-login-%s" % self.sid, daemon=True)
        self._thread.start()

    def _run(self):
        try:
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
                # Belt-and-suspenders: _drive already lands its own stops in phase="error", but if ANY
                # path escapes it the session must not hang in phase="login" — set error here too, and
                # _clean_err strips a raw Playwright 'Call log:' so the operator never sees the trace.
                self._set(phase="error", message=_clean_err("Live login crashed: " + str(e))[:300])
        finally:
            # Every stop (proxy_error / auth failure / idle timeout / operator Close / crash / import
            # failure) persists the last live frame + status line to the diag store that
            # '📷 What the browser saw' reads — so it is never stale on a live-session failure.
            self._persist_diag()

    def _drive(self, p):
        vp = _vp()
        browser = None
        try:
            browser = vp._launch(p)
            ctx = vp._new_context(browser, proxy=vp._proxy_arg(self.proxy_url))
            page = ctx.new_page()
            self._start_screencast(page)             # low-latency frames from the very first paint
            self._set(phase="login", message="Opening the portal…")
            self._capture(page)
            try:
                # SSRF guard (finding C4, 2026-08-06): vp._norm_url now VALIDATES, and raises
                # VidaPayLoginError with an operator-friendly reason when the stored portal_url points
                # at file://, the cloud metadata service, localhost or any other internal address.
                # Caught here so it lands as a clean phase="error" line the settings page can act on,
                # not as "Live login crashed: …". This is the LIVE SCREENCAST path — the surface that
                # streamed the rendered result of that URL back to the caller as JPEG frames.
                base = vp._norm_url(self.url, vp.B2BSOFT_URL if self._is_b2b else vp.DEFAULT_URL)
            except Exception as e:
                self._set(phase="error", message=_clean_err(str(e))[:400])
                self._capture(page)
                return
            try:
                vp._goto_login(page, base)
            except Exception as e:
                # BROAD by design. _goto_login already converts a CONNECTION-CLASS proxy drop into a
                # friendly VidaPayLoginError (named failure + attempts, NO Playwright 'Call log:'), and
                # retries it. But a NON-connection error (a raw net::ERR_ from another path, a selector
                # timeout) would otherwise escape this narrow handler, hit _run's crash handler, and the
                # RAW 'Call log:' reaches the UI (the 2026-07-17 incident). So catch EVERYTHING here:
                # strip the call log and append the egress hint (proxy_url is available at this call site),
                # then land in phase="error" — never leaking the trace.
                msg = _clean_err(str(e))
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

            # HUMAN-DRIVEN-UNTIL-AUTH decision. If a captcha ('I'm not a robot') is present, or the human
            # has already taken control, do NOT auto-submit — hand the login to the human on the live page.
            captcha = _captcha_present(vp, page)
            with self._lock:
                human = self._human_driving
            if captcha or human:
                if captcha:
                    try:
                        vp.prefill_login(page, login_fr, pw_el, self.account_id, self.user, self.pw)
                    except Exception:
                        pass
                    self._set(phase="human_action",
                              message="Human check detected. Complete the 'I'm not a robot' box on the "
                                      "screen, then click Sign in — you're driving this login directly.")
                else:
                    self._set(phase="login",
                              message="You're driving this login — click and type directly on the screen.")
                self._capture(page)
                self._preauth_loop(page, ctx, vp)
                return

            self._set(phase="login", message="Signing in…")
            self._capture(page)
            # FAST PATH (no captcha, human hasn't interacted): fill + submit using the SHARED pinned/heuristic
            # driver (identical to begin_login). The human can still take over at any moment.
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
            # NEVER sit silent on the egress squid page in the "Signing in…" phase (incident 4): if the
            # post-submit page is the proxy's OWN error page but _classify didn't surface it (a flaky
            # content() read), FORCE the proxy_error branch below (which recovers or errors) — instead of
            # falling through to the 2FA tail and idling in awaiting_code on a squid frame.
            try:
                if state != "proxy_error" and vp._looks_like_proxy_error(page):
                    state = "proxy_error"
            except Exception:
                pass
            if state == "authenticated":
                self._on_authenticated(page, ctx, vp)
                self._post_auth_loop(page, ctx, vp)     # keep the trusted browser open for a reuse pull
                return
            if state == "botwall":
                if _captcha_present(vp, page):
                    self._set(phase="human_action",
                              message="The portal's human check must be completed — solve the 'I'm not a "
                                      "robot' box on the screen, then Sign in.")
                    self._capture(page)
                    self._preauth_loop(page, ctx, vp)
                    return
                self._set(phase="error",
                          message="The portal served an anti-automation page after login — route through a residential proxy.")
                self._capture(page)
                return
            if state == "login":
                # A login rejection with an UNSOLVED captcha present is the human check, NOT bad creds.
                if _captcha_present(vp, page):
                    self._set(phase="human_action",
                              message="The portal's human check must be completed — solve the 'I'm not a "
                                      "robot' box on the screen, then click Sign in. (This is NOT a "
                                      "credentials problem.)")
                    self._capture(page)
                    self._preauth_loop(page, ctx, vp)
                    return
                with self._lock:
                    human = self._human_driving
                if human:
                    self._set(phase="login",
                              message="You're driving this login — complete it on the screen above.")
                    self._capture(page)
                    self._preauth_loop(page, ctx, vp)
                    return
                self._set(phase="error",
                          message="Login was rejected — Account ID / User ID / Password not accepted.")
                self._capture(page)
                return
            if state == "proxy_error":
                # The egress proxy served its OWN squid rejection page (NOT the portal, NOT a 2FA screen) —
                # the T-CETRA http-302 hop can strike here (post-submit) too. Recovery v2 (GET-only: https
                # twin, then a DIRECT goto of the https base — never re-submits the login / resends a code):
                # cookies persist, so if the login completed server-side the direct goto lands authenticated.
                # On success drop into the pre-auth loop (which re-detects auth / 2FA). Only surface the
                # friendly egress message (with squid's reported URL) if recovery can't clear it — then
                # _persist_diag writes THIS squid frame to '📷 What the browser saw'.
                if _recover_proxy(vp, page, base):
                    self._set(phase="login", message="Reconnected — finishing sign-in…")
                    self._capture(page)
                    self._preauth_loop(page, ctx, vp)
                    return
                try:
                    pmsg = vp._proxy_error_message(page.url, self.proxy_url, _squid_reported(vp, page))
                except Exception:
                    pmsg = ("The login request died at the egress proxy — check the proxy route "
                            "(use Test proxy), then retry.")
                self._set(phase="error", message=pmsg[:400])
                self._capture(page)
                return

            # 2FA: unless a captcha blocks it or the human has taken over, click THROUGH the pre-code steps
            # (device interstitial → method chooser) EXACTLY ONCE, dispatching the code a SINGLE time. The
            # page then stays OPEN on the code screen; the code the operator types goes into THIS page.
            if not vp._code_field(page):
                if _captcha_present(vp, page):
                    self._set(phase="human_action",
                              message="A human check appeared before the code — complete the 'I'm not a "
                                      "robot' box on the screen, then continue.")
                    self._capture(page)
                    self._preauth_loop(page, ctx, vp)
                    return
                with self._lock:
                    human = self._human_driving
                if not human:
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
            self._preauth_loop(page, ctx, vp)
        finally:
            self._stop_screencast()
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

    def _base_dest(self, vp):
        """The known-good https destination for squid recovery in a POST-submit context — the source's
        configured base (Main-Panel) URL, normalized (no new hardcode beyond vp's DEFAULT_URL/B2BSOFT_URL).
        Once the login completed server-side, a direct goto here lands authenticated (cookies persist)."""
        try:
            return vp._norm_url(self.url, vp.B2BSOFT_URL if self._is_b2b else vp.DEFAULT_URL)
        except Exception:
            # SSRF guard (C4): the old fallback returned the RAW, UNVALIDATED url here — which would
            # have handed the squid-recovery goto exactly the value the guard just refused. There is
            # no safe fallback for a URL we have rejected, so return None; _recover_proxy treats a
            # missing dest as "no direct-goto phase" and still runs the https-twin recovery.
            return None

    def _preauth_loop(self, page, ctx, vp):
        """The HUMAN-DRIVEN pre-auth loop: stream frames fast, forward the operator's input immediately,
        and periodically DETECT state (authenticated / proxy_error / captcha) for the status line — without
        auto-submitting or racing the human. Services the convenience code box (SUBMIT_CODE / RESEND) too.
        Returns into _post_auth_loop the moment the login reaches 'authenticated'. Auto-closes after idle.
        All Playwright work stays on this (the worker) thread."""
        last_poll = 0.0
        while True:
            self._pump(page)
            now = time.time()
            if now - last_poll >= _CLASSIFY_POLL_S:
                last_poll = now
                self._preauth_detect(page, ctx, vp)
                ph = self.snapshot_phase()
                if ph == "authenticated":
                    return self._post_auth_loop(page, ctx, vp)
                if ph in _TERMINAL:
                    return
            with self._lock:
                idle = time.time() - self._last_activity
                ph = self.phase
            limit = _HUMAN_ACTION_IDLE_SECONDS if ph == "human_action" else _IDLE_CLOSE_SECONDS
            if idle > limit:
                self._set(phase="cancelled", message="Live session closed after inactivity.")
                return
            cmd = self._next_cmd()
            if cmd is None:
                continue
            kind = cmd[0]
            if kind == "CANCEL":
                self._set(phase="cancelled", message="Cancelled by the operator.")
                return
            if kind == "INPUT":
                self._do_input(page, ctx, vp, cmd[1])
                if self.snapshot_phase() == "authenticated":
                    return self._post_auth_loop(page, ctx, vp)   # a human click finished sign-in → keep open
                continue
            if kind == "RESEND":
                self._do_resend(page, vp)
                continue
            if kind == "SUBMIT_CODE":
                if self._do_submit(page, ctx, vp, cmd[1]):
                    if self.snapshot_phase() == "authenticated":
                        return self._post_auth_loop(page, ctx, vp)   # authenticated → keep browser open
                    return           # error/proxy_error → terminate (browser closes in _drive finally)
                continue
            # PULL is meaningless pre-auth — ignore.

    def _preauth_detect(self, page, ctx, vp):
        """Pre-auth state DETECTION (never drives): conclude auth when the human finishes, stop on a proxy
        error, and keep the human_action ↔ awaiting/login status line in sync as a captcha appears/clears."""
        try:
            state = vp._classify(page)
        except Exception:
            state = "unknown"
        if state == "proxy_error":
            # The http-302→squid hop can strike at ANY point (T-CETRA's edge changed). Recovery v2 FIRST
            # (GET-only: https twin, then a DIRECT goto of the https base — never re-submits); only surface
            # the friendly error (with squid's reported URL) if it can't clear. On success, drop back into
            # the normal pre-auth flow below by re-reading state (the page is now the real portal/login/2FA
            # screen, not squid).
            if _recover_proxy(vp, page, self._base_dest(vp)):
                try:
                    state = vp._classify(page)
                except Exception:
                    state = "unknown"
                self._capture(page)
            if state == "proxy_error":
                try:
                    pmsg = vp._proxy_error_message(page.url, self.proxy_url, _squid_reported(vp, page))
                except Exception:
                    pmsg = "The request died at the egress proxy — check the proxy route, then retry."
                self._set(phase="error", message=pmsg[:400])
                self._capture(page)
                return
        if state == "authenticated":
            try:
                on_trust = any(w in vp._page_text(page) for w in vp._TRUST_PAGE_WORDS)
            except Exception:
                on_trust = False
            code = None
            try:
                code = vp._code_field(page)
            except Exception:
                code = None
            if not on_trust and not code:
                self._on_authenticated(page, ctx, vp)
                return
        # Captcha status upkeep — flip TO human_action when a challenge appears, and back when it clears.
        cap = _captcha_present(vp, page)
        ph = self.snapshot_phase()
        if cap and ph in ("login", "awaiting_code", "verifying", "action_needed"):
            self._set(phase="human_action",
                      message="A human check appeared — complete the 'I'm not a robot' box on the screen, "
                              "then continue.")
        elif not cap and ph == "human_action":
            try:
                has_code = bool(vp._code_field(page))
            except Exception:
                has_code = False
            if has_code:
                self._set(phase="awaiting_code",
                          message="Human check cleared — enter the verification code the portal sent.")
            else:
                self._set(phase="login",
                          message="Human check cleared — complete the login on the screen (click Sign in).")

    def _post_auth_loop(self, page, ctx, vp):
        """After authentication, keep the SAME trusted browser OPEN and idle so a '▶ Pull now' runs on
        the very session that just passed 2FA. The cold storage_state restore is re-challenged by
        T-CETRA (a fresh browser / egress IP / server session isn't the trusted device — this is the
        'Pull asks for another code / session expired' the operator hit), so reusing this page is the
        fix. Services PULL + CANCEL (+ input 'take control'); auto-closes after an idle window.
        All Playwright work stays on this (the worker) thread; SUBMIT_CODE/RESEND are ignored post-auth.

        AUTO-PULL: the due reports are fetched IMMEDIATELY on arrival here (once per session, switchable
        per source), because a trusted session that expires before anything is pulled is worth nothing —
        that is the owner's 2026-07-27 "logged in but no file imports" report."""
        self._touch()
        if self.auto_pull and self.pull_fn is not None and not self._auto_pulled:
            self._auto_pulled = True
            blocked = self._cooldown_state()
            if blocked.get("blocked"):
                # The portal has temporarily blocked us (owner report 2026-07-27). Pulling five reports
                # across several month windows into an active block is exactly how a 30-minute throttle
                # becomes a day-long ban. The trusted session stays open; ▶ Pull now still works for a
                # human who confirms.
                self._set(phase="authenticated",
                          message=("Signed in — but the portal has temporarily blocked our report "
                                   "requests, so the automatic pull was skipped. " +
                                   (blocked.get("_human") or ""))[:400])
                self._capture(page)
            else:
                self._handle_pull(page, vp, None)
        while True:
            self._pump(page)
            with self._lock:
                idle = time.time() - self._last_activity
            if idle > _POST_AUTH_IDLE_SECONDS:
                self._set(phase="cancelled", message="Live session closed after inactivity (post-login).")
                return
            cmd = self._next_cmd()
            if cmd is None:
                continue
            kind = cmd[0]
            if kind == "CANCEL":
                self._set(phase="cancelled", message="Cancelled by the operator.")
                return
            if kind == "PULL":
                self._handle_pull(page, vp, cmd[1] if len(cmd) > 1 else None)
                continue
            if kind == "INPUT":
                self._do_input(page, ctx, vp, cmd[1])
                continue
            # SUBMIT_CODE / RESEND are meaningless once authenticated — ignore.

    def _cooldown_state(self):
        """Is this login inside a portal-imposed cooldown right now? Best-effort — a gate that raises,
        or no gate at all (pre-migration-244 / existing callers), means NOT blocked, so this can never
        stop a healthy pull."""
        if self.auto_pull_gate is None:
            return {"blocked": False}
        try:
            st = self.auto_pull_gate() or {}
        except Exception:
            return {"blocked": False}
        try:
            from app.modules.commcalc import portal_backoff as _pb
            st = dict(st)
            st["_human"] = _pb.humanize(st)
        except Exception:
            pass
        return st

    def _handle_pull(self, page, vp, result_q):
        """Run the report pull on THIS live authenticated browser (never a cold restore) and hand the
        result back to the waiting request thread via `result_q`. Keeps the page open afterwards so the
        operator can pull again. Best-effort — a pull failure is reported, never crashes the session."""
        self._set(phase="pulling",
                  message="Signed in — fetching this login's reports on the live session…")
        self._capture(page)
        res = None
        try:
            if self.pull_fn is not None:
                # Hand the pull a stop signal WHEN it accepts one (checked by arity, not by catching
                # TypeError — a TypeError raised *inside* the pull must never be mistaken for a
                # signature mismatch). Older/one-arg pull_fns keep working unchanged.
                res = (self.pull_fn(page, self._stop_requested) if _takes_stop(self.pull_fn)
                       else self.pull_fn(page))
            else:
                res = {"ok": False, "error": "No pull is configured for this live session."}
        except Exception as e:
            res = {"ok": False, "error": ("Pull failed on the live session: " + str(e))[:300]}
        with self._lock:
            self.pull_result = res
        # Route the outcome to the data_source row through the SAME honest split /run uses:
        # delivered ⇒ last_run_at, anything else ⇒ last_attempt_at (mig 241). Best-effort.
        if self.persist_pull is not None:
            try:
                self.persist_pull(res)
            except Exception:
                pass
        if result_q is not None:
            try:
                result_q.put(res)
            except Exception:
                pass
        msg = (res.get("status") if isinstance(res, dict) and res.get("status")
               else (res.get("error") if isinstance(res, dict) and res.get("error") else "Pull finished."))
        # NEVER report a 0-row pull as a success: that green tick over "pulled 0 rows across 0
        # report(s)" is precisely what made a dead connector look healthy.
        prefix = "Imported: " if _delivered(res) else "⚠️ Nothing imported — "
        self._set(phase="authenticated", message=(prefix + str(msg))[:400])
        self._capture(page)

    def _do_input(self, page, ctx, vp, ev):
        """Execute a forwarded human input on the live page. Click coords are NORMALIZED (0..1 of the
        streamed image) → multiplied by the live viewport size (DPR-proof). type/key dispatch real key
        events (so form-validation listeners fire); scroll uses the wheel."""
        et = (ev or {}).get("type")
        if et in ("click", "dblclick"):
            self._do_click(page, ctx, vp, ev.get("x"), ev.get("y"), double=(et == "dblclick"))
            return
        try:
            if et == "type":
                txt = str(ev.get("text") or "")
                if txt:
                    page.keyboard.type(txt, delay=8)
            elif et == "key":
                k = str(ev.get("key") or "")
                if k:
                    page.keyboard.press(k)
            elif et == "scroll":
                try:
                    dy = float(ev.get("deltaY") or 0)
                except (TypeError, ValueError):
                    dy = 0.0
                page.mouse.wheel(0, dy)
        except Exception as e:
            self._set(message="Input didn't register (%s)." % str(e)[:80])
        self._capture(page)

    def _do_click(self, page, ctx, vp, nx, ny, double=False):
        """Translate a normalized (0..1) click on the streamed image to a real click on the live page at
        the matching viewport pixel, then re-check whether we've landed authenticated (so an operator
        clicking the portal's Sign in / Next / Trust button completes + saves the session)."""
        try:
            vp_size = page.viewport_size or {"width": 1366, "height": 900}
            x = max(0.0, min(1.0, float(nx))) * vp_size["width"]
            y = max(0.0, min(1.0, float(ny))) * vp_size["height"]
            page.mouse.move(x, y)      # move first so the very next frame shows the pointer where it'll land
            self._capture(page)        # instant feedback (cursor moved) — no wait
            if double:
                page.mouse.dblclick(x, y)
            else:
                page.mouse.click(x, y)
        except Exception as e:
            self._set(message="Click didn't register (%s) — try again." % str(e)[:80])
            self._capture(page)
            return
        # Rapid burst of captures right after the click so the result appears fast (screencast covers the
        # gaps too, but these guarantee movement even if the stream is idle).
        self._capture(page)
        for _ in range(3):
            try:
                page.wait_for_timeout(250)
            except Exception:
                break
            self._capture(page)
        try:
            vp._wait_settle(page)
        except Exception:
            pass
        self._capture(page)
        # A click on Sign in / Next / Trust may finish sign-in — but only conclude auth once the trust page
        # is gone (its header carries a 'Sign Out' link that _classify would misread as logged-in).
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
        if state == "proxy_error":
            # Code accepted, but the post-code navigation hit the T-CETRA http-302→squid hop. Recovery v2
            # is GET-only (https twin, then a DIRECT goto of the https base) — it CANNOT resend the code, so
            # it's safe. Cookies are set (the code was accepted), so the direct goto should land the app.
            # Try it; if it clears, re-check auth.
            if _recover_proxy(vp, page, self._base_dest(vp)):
                try:
                    recl = vp._classify(page)
                except Exception:
                    recl = "unknown"
                self._capture(page)
                if recl == "authenticated":
                    self._on_authenticated(page, ctx, vp)
                    return True
                # Cleared the squid page but not conclusively authenticated → hand back to the pre-auth
                # loop's detection (it concludes auth / keeps the code screen) rather than falsely erroring.
                self._set(phase="verifying", message="Reconnected — finishing sign-in…")
                self._capture(page)
                return False
            # retrying the code can't help. Stop with a clear proxy message; _persist_diag writes the frame.
            try:
                pmsg = vp._proxy_error_message(page.url, self.proxy_url, _squid_reported(vp, page))
            except Exception:
                pmsg = ("The request died at the egress proxy after the code — check the proxy route "
                        "(use Test proxy), then retry.")
            self._set(phase="error", message=pmsg[:400])
            self._capture(page)
            return True
        # A human check that appears at/after the code step is NOT a code rejection — flip to human_action.
        if _captcha_present(vp, page):
            self._set(phase="human_action",
                      message="A human check appeared — complete the 'I'm not a robot' box on the screen, "
                              "then Submit the code again.")
            self._capture(page)
            return False
        # If the auto-clicker couldn't finish the "Trust This Device" page, tell the operator to click
        # the Next button themselves in the live view (input-forwarding is enabled) — the code WAS
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


def start_session(sid, org_id, row, persist=None, persist_shot=None, pull_fn=None, persist_pull=None,
                  auto_pull_gate=None):
    """Spawn (or REPLACE) the live session for `sid`. Non-blocking — the worker thread drives it.
    `pull_fn` (callable(page) -> pull result) runs the report pull on THIS live browser — automatically
    the moment the login authenticates, and again on every '▶ Pull now'. `persist_pull(result)` records
    that pull's outcome on the data_source row (honest last_run_at vs last_attempt_at)."""
    with _SESSIONS_LOCK:
        _prune_locked()
        old = _SESSIONS.get(sid)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass
        sess = LiveLoginSession(sid, org_id, row, persist, persist_shot, pull_fn, persist_pull,
                                auto_pull_gate)
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
