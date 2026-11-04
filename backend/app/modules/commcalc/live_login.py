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

try:
    from app.core.service_role import (assert_browser_allowed,      # SERVICE_ROLE=api guard
                                       browser_allowed, require_browser_service)
except ImportError:                                     # loaded by path, not as app.modules.commcalc.*
    def assert_browser_allowed():                        # no-op fallback for path-loaded proof scripts
        return None

    def browser_allowed():
        return True

    def require_browser_service():
        return None

# Registry of live sessions, keyed by data_source id. Guarded by _SESSIONS_LOCK.
_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()


# ── WHERE THE SESSIONS LIVE (§COMMCALC-24) ───────────────────────────────────────────────────────
# `_SESSIONS` is a dict in THIS process's memory. Asking it a question therefore answers for this
# process ONLY, and on a split deploy (`SERVICE_ROLE=api` + `BROWSER_SERVICE_URL`, §41) the API host
# holds no sessions at all: `get_session` there returns None for a session that is alive and running
# on the sweeps worker.
#
# That is how the Team Portal live login was dead for the owner from 2026-10-15 to 2026-10-20.
# `/live-login/start` carried `require_browser_service()`, so it proxied to the worker and a real
# Chromium really did open, fill his username and password and park on the portal's Sign In button.
# `/live-login/state` and `/live-login/frame` did NOT carry it, so they answered from the API host —
# `{"phase": "idle", "message": "No live session running…", "shot": None}` — and the panel that IS
# his only view of that browser showed him nothing to press. Eight minutes later the worker closed
# the session for operator inactivity. He had been looking at the wrong process the whole time.
#
# THE CLASS: gating "does this endpoint LAUNCH a browser?" is the wrong question. `/frame` launches
# nothing; it reads a dict. The right question is **"does this endpoint touch `_SESSIONS`?"** — and
# if it does, it must run in the process that HAS them. The three endpoints that obviously drive a
# browser got the gate; the six that merely read or steer the session did not, and the read half is
# exactly what the operator's screen is made of.
#
# So the requirement gets its own NAME here, in the module that owns the registry, rather than being
# spelled as `require_browser_service()` at each call site. The old spelling reads as "this launches
# Chromium", which is precisely why a reviewer adding a read endpoint left it off.
def sessions_live_here():
    """True when THIS process can hold live sessions, i.e. it is the one that runs browsers.

    PURE and never raises — an unintrospectable role reads as True, which keeps the single-service
    behaviour (sessions are local) rather than declaring a working deploy blind."""
    try:
        return bool(browser_allowed())
    except Exception:
        return True


def require_session_host():
    """THE gate for an endpoint that reads, drives or creates a live session.

    Dereferences `require_browser_service()` — on a split deploy that raises `BrowserWorkProxy` and
    `main._proxy_browser_work` forwards the whole request to the worker, so the endpoint runs where
    `_SESSIONS` actually is; with no worker configured it is the same honest 503 as before. Call it
    EARLY, before `require_org`, exactly as `/live-login/start` always has."""
    require_browser_service()

_SHOT_INTERVAL = 0.7           # legacy idle capture cadence (kept for the post-auth slow path)
_IDLE_CLOSE_SECONDS = 20 * 60  # auto-close a session left idle this long (long enough to fetch a code)
_HUMAN_ACTION_IDLE_SECONDS = 8 * 60  # a human_action (captcha) phase left untouched this long closes cleanly
# §COMMCALC-30 — "nobody is there" is not the same fact as "the person there is not typing". Only
# operator COMMANDS call `_touch()`; the frame and state polls never did, so an operator READING the
# page or working through a captcha was indistinguishable from an abandoned tab, and the SHORTENED
# budget above closed the session out from under them (owner, 2026-10-23: his 00:48 session died at
# ~00:56 while he was in it). A surface still polling IS a human watching, so the shortened budget
# does not apply to them — they get the ordinary `_IDLE_CLOSE_SECONDS` instead. No budget here gets
# LONGER than one that already existed: an abandoned tab stops polling and closes on the old
# schedule, and `in_flight_stale_after_s` (§COMMCALC-13) keeps the window it already had.
_VIEWER_PRESENT_SECONDS = 30      # a frame/state poll within this long means a human is watching now
# 'authenticated' is deliberately NOT terminal: after sign-in the browser stays OPEN (see _post_auth_loop)
# so a '▶ Pull now' can run on the SAME trusted page. The session becomes terminal only on error/cancel
# (incl. the post-auth idle timeout, which flips it to 'cancelled').
_TERMINAL = ("error", "cancelled")
# THE phases in which this session has passed the portal's sign-in. Declared once because THREE
# things asked it and one of them got it wrong: `can_pull` and `can_calibrate` both carried this
# tuple inline, and the live window in the browser compared the phase to the single word
# 'authenticated' — so closing the window during the post-login pull (phase 'pulling') CANCELLED a
# session the operator had just signed in to, which is how the owner lost his 21:02 UPS session two
# minutes after making it. 'pulling' is post-authentication: the browser is open and trusted.
_SIGNED_IN_PHASES = ("authenticated", "pulling")

# ── HOW LONG MAY THIS SESSION STAY IN THIS PHASE? ONE HOME, two different questions ─────────────
# Owner report 2026-10-15, on a brand-new Team Portal login: *"it has been over 5 minutes it is
# startying teh love login and does not move freom tehre"*. He was right, and waiting would never
# have helped: the session was in phase 'starting' with NO limit on it at all.
#
# THE CLASS. "How long may this phase go without progress" WAS answered — by one inline conditional
# inside `_preauth_loop`. But that loop is only reached once the login is already WORKING, so every
# phase before it (browser launch, the first navigation, the sign-in submit) had no limit of any
# kind. A stall there sat in "Starting the live login session…" forever: no error written, the
# data_source row left reading `auth_status='authenticating'` / "Live login in progress…", and the
# operator staring at a frozen word with nothing ever arriving. A status that cannot say a session
# died is not a record of what the session did.
#
# Two DIFFERENT questions, deliberately not merged — merging them would silently shorten the human's
# window for fetching a 2FA code, which is the one place a long wait is CORRECT:
#   · INACTIVITY (below): the session is healthy and waiting on the OPERATOR. Unchanged.
#   · NO PROGRESS (this map): the session is waiting on ITSELF, before the command loop is entered,
#     with no human expected to act. These are the phases that had no limit.
#
# The pre-loop budgets are generous against the real worst cases so a slow-but-working login is
# never killed: `_launch` tries real Chrome then bundled Chromium (~30s Playwright launch timeout
# each) before giving up, and `vidapay_sweep._goto_login` can spend two ladders of
# 3 x 60s `_goto_with_retry` plus settles. Each phase change RESTARTS its own clock, so reaching
# 'login' grants it the full navigation budget.
PRELOOP_PROGRESS_LIMIT_S = {
    "starting": 180.0,        # launch Chrome, fall back to Chromium, open a context + page: ~60s real
}
PRELOOP_PROGRESS_DEFAULT_S = 600.0   # navigating / signing in — covers the full _goto_login retry ladder
_PROGRESS_POLL_S = 5.0               # how often the watchdog looks
# The phase slug is ours, not the operator's. This is what each pre-loop phase was DOING, in words
# they can act on — the stall message names the step, never 'phase=starting'.
_PRELOOP_DOING = {
    "starting": "opening a browser on the server",
    "login": "loading the portal's sign-in page",
    "verifying": "waiting for the portal to accept the sign-in",
}


def preloop_limit(phase):
    """THE reader of the no-progress budget for a phase, used BEFORE the command loop is entered.
    A phase nobody declared gets the default rather than no limit at all — "unlisted" must never
    mean "may hang forever", which is the defect this map exists to close."""
    return float(PRELOOP_PROGRESS_LIMIT_S.get(str(phase or ""), PRELOOP_PROGRESS_DEFAULT_S))


def inactivity_limit(phase, watched=False):
    """THE reader of the OPERATOR-inactivity budget, used INSIDE the command loop. Factored out of
    `_preauth_loop`'s inline conditional so the two questions above have one home each.

    `watched` is the §COMMCALC-30 fact: a surface is still polling this session, so a human IS there
    and simply has not typed yet. The ONLY thing it changes is that the SHORTENED `human_action`
    budget — shortened on the assumption that nobody is acting — does not apply to a human who is
    demonstrably there; they get the ordinary budget, never a longer one. Default False, which gives
    exactly the pre-2026-10-23 answers."""
    if str(phase or "") == "human_action" and not watched:
        return float(_HUMAN_ACTION_IDLE_SECONDS)
    return float(_IDLE_CLOSE_SECONDS)


def max_inactivity_limit():
    """The LONGEST an operator may legitimately hold a PRE-AUTH session without an input, over every
    budget above. Anything deriving "how long may this take" dereferences THIS rather than naming one
    constant, so a budget added later widens every derivation with it instead of silently invalidating
    it — which is exactly what a longer watched budget would have done to `in_flight_stale_after_s`."""
    return float(max(_IDLE_CLOSE_SECONDS, _HUMAN_ACTION_IDLE_SECONDS))
_TERMINAL_TTL = 15 * 60       # keep a finished session's final state this long, then prune
_POST_AUTH_IDLE_SECONDS = 15 * 60  # keep the authenticated browser open this long for a reuse-the-session pull

# Low-latency frame streaming.
_SCREENCAST_QUALITY = 55       # JPEG quality for the CDP screencast (viewport-sized)
_SCREENCAST_MAX_W = 1366
_SCREENCAST_MAX_H = 900
_FRAME_PUMP_MS = 80            # CDP mode: pump the event loop this often so screencast frames dispatch (~12/s)
_FALLBACK_PUMP_MS = 300        # no-CDP mode: tightened screenshot loop (~3.3/s vs the old ~0.67/s)
_FRAME_GUARANTEE_S = 1.5       # even in CDP mode, force a screenshot if no frame advanced this long (never freeze)
_INPUT_DRAIN_MAX = 32          # service at most this many queued human inputs per tick (bounded: the idle
                               # check and the frame pump must still run even under a flood)
_CLASSIFY_POLL_S = 1.2         # pre-auth: how often to re-DETECT state (captcha / auth / proxy) for the status
# §COMMCALC-31 — detection is the SAME work §COMMCALC-27 took off the operator's latency path, in a
# second place. `_preauth_detect` walks every frame of the page (`_classify` reads the text of each,
# `_captcha_present` queries selectors), which on a heavy portal costs far more than a frame tick, and
# it ran on a fixed timer AHEAD of the input drain — so a click could wait a pump plus a whole page
# re-read before it was dequeued. It is now deferred while the operator is still acting, BOUNDED: once
# this long has passed since the last detect it runs regardless, so deferral can never become
# starvation (a captcha appearing mid-burst is still noticed within one ceiling).
_DETECT_DEFER_MAX_S = 4.0      # the longest detection may be deferred by a run of operator input

_B2B_PROCESSORS = ("b2bsoft", "b2b")
# The master-agent portal under both the names its connector registry row carries. Spelled HERE and
# read by _preauth_login_url, so the bot-wall fallback belongs to the portal it is for.
_VIDAPAY_PROCESSORS = ("vidapay", "total_access")


def detect_due(now, last_detect, input_pending,
               poll_s=_CLASSIFY_POLL_S, defer_max_s=_DETECT_DEFER_MAX_S):
    """THE one answer to "may state detection run on this tick" (§COMMCALC-31). Pure, and it never
    raises: unreadable inputs read as DUE, so the failure mode is the old always-detect behaviour
    rather than a session that silently stops noticing a captcha.

    Due when `poll_s` has passed since the last detect AND the operator is not mid-action — except
    once `defer_max_s` has passed, when it is due regardless, so a flood cannot starve detection."""
    try:
        since = float(now) - float(last_detect)
    except (TypeError, ValueError):
        return True
    try:
        if since >= float(defer_max_s):
            return True
    except (TypeError, ValueError):
        return True
    try:
        if since < float(poll_s):
            return False
    except (TypeError, ValueError):
        return True
    return not bool(input_pending)


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


def _lf():
    """Lazy import of the frame-geometry home (§COMMCALC-47) — where a click on the streamed image
    lands on the live page. THE one translation; this module does not do that arithmetic itself."""
    from app.modules.commcalc import live_frame as lf
    return lf


# What the operator is told when no password field could be found. The page itself usually SAYS why,
# and until §COMMCALC-50 this threw that away: a portal that had plainly refused the request was
# reported as "the login form may render differently than expected", which sends somebody to read
# markup on a page that has no form on it at all (owner's UPS billing login, 2026-10-30).
#
# So the page's own verdict leads, and the guess is only what is left when the page said nothing.
def _page_verdict(vp, page, refusal=None, challenge=None):
    """The portal's own refusal / challenge, or None. Dereferences the ONE vocabulary."""
    try:
        return vp.page_refusal(page, refusal=refusal, challenge=challenge)
    except Exception:
        return None


def _wall_note(vp, page, refusal=None, challenge=None):
    v = _page_verdict(vp, page, refusal=refusal, challenge=challenge)
    if v:
        return v["note"]
    return "The login form may render differently than expected — check the screenshot."


# What to tell the operator for each KIND of human check. One home, because the two need genuinely
# different instructions and the wrong one sends them hunting for a box that is not on the page yet
# (owner report 2026-10-15 — the UPS reporting portal arms an INVISIBLE reCAPTCHA, index §COMMCALC-11).
_HUMAN_CHECK_MESSAGE = {
    "visible": ("Human check detected. Complete the 'I'm not a robot' box on the screen, then click "
                "Sign in — you're driving this login directly."),
    "invisible": ("This portal runs a human check when you sign in. Click Sign in on the screen "
                  "yourself and complete the check if one appears. Signing in for you would be "
                  "rejected as a bad password."),
}

# WHAT IS ACTUALLY IN THE FORM — a different fact from what GATES the submit, and that is why it is
# a different home. The invisible message above used to assert "Your details are filled in" without
# anything having looked: `vidapay_sweep.prefill_login` swallowed every failure and returned nothing,
# so a blank saved credential or a control it could not find left the operator staring at an empty
# form that the status line told him was filled (owner report 2026-10-31, the second half of the UPS
# reporting-portal login). The fill now reports; these are the only words for that report.
_PREFILL_NOTE = {
    "ok": "Your saved sign-in details are filled in for you.",
    # The two causes of an empty box, which send the operator to two different places.
    "no_value": ("%s is not saved on this login, so the form is not complete — add it on the login's "
                 "settings, or type it on the screen now."),
    "no_control": ("%s could not be put into this page's form — type it on the screen now, and the "
                   "screenshot shows the form this login was given."),
}


def _and_list(names):
    """"a", "a and b", "a, b and c" — so a sentence reads like a sentence. PURE."""
    names = [str(n) for n in (names or []) if str(n or "").strip()]
    if len(names) <= 1:
        return names[0] if names else ""
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def _prefill_note(report):
    """The ONE sentence about what the pre-fill put in the form, or "" when there is nothing to say.

    Reads `vidapay_sweep.prefill_login`'s report. A missing / empty report says NOTHING rather than
    claiming a fill, because this sentence exists precisely to stop the surface asserting something
    nobody checked. Never raises."""
    try:
        if not report:
            return ""
        if report.get("ok"):
            return _PREFILL_NOTE["ok"] if report.get("filled") else ""
        parts = []
        for key in ("no_value", "no_control"):
            names = report.get(key) or []
            if names:
                parts.append(_PREFILL_NOTE[key] % _and_list(names).capitalize())
        return " ".join(parts)
    except Exception:
        return ""


def _human_check_kind(vp, page):
    """THE kind of human check gating this page: "visible", "invisible" or "". Never raises — a driver
    (or a test fake) without the helper reads as no check, which is the pre-2026-10-15 behaviour.
    Falls back to the older boolean helper so a fake that only provides THAT still answers."""
    try:
        kind = vp.human_check(page)
    except Exception:
        try:
            kind = "visible" if vp._looks_like_captcha(page) else ""
        except Exception:
            return ""
    kind = str(kind or "")
    return kind if kind in _HUMAN_CHECK_MESSAGE else ""


def _captcha_present(vp, page):
    """Back-compat boolean over `_human_check_kind` — kept because the proof scripts in
    backend/scratchpad call it directly. Reads the SAME answer, so there is no second detector."""
    return bool(_human_check_kind(vp, page))


# WHAT STATUS DOES A FINISHED SESSION LEAVE BEHIND (owner-visible defect found 2026-10-15,
# index §COMMCALC-13)?
#
# `live_login_start` stamps `auth_status='authenticating'` / "Live login in progress…" on the row
# before the worker starts, because that IS true while the worker runs. §COMMCALC-10 taught a session
# that DIES to write 'error'. But a session that simply ENDS — the inactivity timeout, or the operator
# pressing Close — wrote only `auth_message` and left the status saying 'authenticating'. The chip on
# commcalc/email-imports maps that to "⏳ Logging in…", so the owner's screen claimed a login was in
# progress an hour after the browser had closed. Same class as §COMMCALC-10 and §19.41: **a status is
# a record of what IS, so a session that ends without signing in must leave the row saying what was
# true BEFORE it started — never "in progress".**
#
# `_STATUS_IN_FLIGHT` is the status that means "a login is running right now". It is the one value
# that may never be restored: a row already carrying it (a PREVIOUS session that ended before this
# fix, or any future path that stamps it and dies un-persisted) would otherwise have the same lie
# handed straight back to it. It becomes None, which the chip renders as "○ Not connected" — the
# honest reading of a login that has credentials but no session.
_STATUS_IN_FLIGHT = "authenticating"


def terminal_status(phase, status_before):
    """THE status a terminal live session leaves on the data_source row, or None to clear it.

    `"error"` is the session's own verdict and owns the row. Every other terminal phase did not
    change whether we are signed in, so the row goes back to what it said before — except
    `_STATUS_IN_FLIGHT`, which is never restored (see above). A phase that is not terminal returns
    None and writes nothing; `_persist_diag` is what decides to call this at all."""
    if str(phase or "") == "error":
        return "error"
    before = str(status_before or "").strip().lower()
    if not before or before == _STATUS_IN_FLIGHT:
        return None
    return before


# THE READ SIDE OF THE SAME FACT. `terminal_status` can only speak for a session that reaches its own
# end. A worker that is KILLED mid-login — and every merge to main redeploys both hosts, so this is
# routine here, not exotic — never runs `_persist_diag` at all, and the row is left claiming a login
# is in flight with no process anywhere running it. Nothing on the row can distinguish that from a
# login genuinely still going, so the only honest discriminator is AGE: past the longest a live
# session may legitimately stay in flight, an in-flight status is a leftover.
#
# The budget is DEREFERENCED, never a new number: the worst honest case is a session that reaches the
# command loop and is held there by an operator for the full inactivity window, plus one pre-loop
# budget before it. A margin is added because a clock here and a clock in Postgres are not the same
# clock, and reading a LIVE session as stale would be the one regression this must not cause.
_IN_FLIGHT_STALE_MARGIN_S = 5 * 60


def in_flight_stale_after_s():
    """How long an in-flight status may stand before it is a leftover. One expression over the two
    homes that already answer "how long may this take", so a change to either moves this too.

    It dereferences `max_inactivity_limit()` rather than one budget constant: §COMMCALC-30 added a
    LONGER budget for a watched session, and reading a live watched session as stale would be exactly
    the regression the comment above forbids."""
    return max_inactivity_limit() + PRELOOP_PROGRESS_DEFAULT_S + float(_IN_FLIGHT_STALE_MARGIN_S)


def in_flight_is_stale(auth_status, pending_started_at, now=None):
    """True when this row's status claims a login is running but no session can still be running it.

    PURE and never raises — an unparseable or missing timestamp reads as NOT stale, so an unknown
    shape keeps the pre-2026-10-15 answer rather than clearing a status that might be live. Only
    `_STATUS_IN_FLIGHT` can be stale; every other status is a settled verdict and is left alone."""
    if str(auth_status or "").strip().lower() != _STATUS_IN_FLIGHT:
        return False
    try:
        started = str(pending_started_at or "").strip()
        if not started:
            return False
        t = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        return (ref - t).total_seconds() > in_flight_stale_after_s()
    except Exception:
        return False


def present_auth_status(row, now=None):
    """The `auth_status` a SURFACE should show for this row — the stored one, or None once an in-flight
    claim has gone stale. The chip on commcalc/email-imports renders None as "○ Not connected", which
    is the honest reading of a login that holds credentials and no session."""
    try:
        stored = row.get("auth_status")
    except Exception:
        return None          # not a row at all — there is no status to present
    try:
        return None if in_flight_is_stale(stored, row.get("pending_started_at"), now=now) else stored
    except Exception:
        return stored        # anything unexpected keeps the STORED answer, never a cleared one


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
                 persist_pull=None, auto_pull_gate=None, calibrate_fn=None):
        self.sid = sid
        self.org_id = org_id
        self.url = row.get("portal_url")
        self.account_id = row.get("account_id")
        self.user = row.get("username")
        self.pw = row.get("password")
        self.proxy_url = row.get("proxy_url")
        self.processor = (row.get("processor") or "").strip().lower()
        self._is_b2b = self.processor in _B2B_PROCESSORS
        # The source's declared calibration. Its "login" key is what `login_progressive` reads when a
        # page's own words do not say which control takes which value (§COMMCALC-46) — config, never a
        # branch in the login code.
        self.portal_calibration = row.get("portal_calibration") or {}
        self.persist = persist                    # callable(updates: dict) -> persists to data_source
        self.persist_shot = persist_shot          # callable(shot_b64) -> writes login_shot/login_shot_at
        self.pull_fn = pull_fn                     # callable(page) -> pull result; runs on THIS live page
        self.persist_pull = persist_pull           # callable(result) -> stamps the pull's outcome honestly
        self.pull_result = None                    # last pull result (auto-pull or '▶ Pull now')
        # READ a report page's own controls and SUGGEST its declaration (§COMMCALC-39). Separate from
        # pull_fn and never called by the auto-pull: a capture is something an operator asks for, and
        # a session that signed in to fetch reports must not start reading pages nobody asked about.
        self.calibrate_fn = calibrate_fn           # callable(page[, should_stop]) -> suggestion dict
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
        # The phase's OWN clock, restarted by every phase change — what the no-progress watchdog
        # measures. Distinct from `_last_activity`, which the inactivity rule measures and which a
        # frame or a human keystroke also touches.
        self._phase_since = time.time()
        # True once _preauth_loop / _post_auth_loop is running. From that moment the INACTIVITY rule
        # owns the session and the watchdog stands down — so no human window is ever shortened.
        self._loop_entered = False
        self._shot = None                         # latest base64 JPEG (no data-uri prefix)
        # What that frame SHOWS, in CSS px of the live page (§COMMCALC-47). A click is a fraction of
        # the image, so it is translated through this and not through the window.
        self._frame_geom = None
        self._viewport = None                     # the context's window, once the page exists
        self._seq = 0                             # monotonic frame sequence — bumped on every new frame
        self._last_frame_at = 0.0                 # wall time of the last frame stored (liveness guarantee)
        self._human_driving = False               # True once the human sends ANY input → pauses auto-drive
        self._cdp = None                          # the CDP screencast session (None if unavailable)
        self._screencast_on = False
        self._pending_ack = None                  # sessionId of the newest screencast frame awaiting ack
        self._updated_at = datetime.now(timezone.utc).isoformat()
        self._last_activity = time.time()
        self._last_seen_at = 0.0                  # wall time an operator surface last asked (§COMMCALC-30)
        # §COMMCALC-31 — what the session is DOING right now, written by the one setter `_note_act`
        # and read by the one reader `activity()`. The owner's 2026-10-26 report was "it should show
        # what is going on, it seems nothing is moving": a phase ('login') and a standing instruction
        # ('complete the login on the screen') do not move, so a stalled session and a working one
        # looked identical. This is NOT a second copy of `phase`/`message` — it is the step in flight.
        self._act = "Starting up"
        self._act_at = time.time()
        self._finished_at = None
        self._thread = None
        self._watchdog = None                     # the no-progress watchdog thread (see _watch)
        # What the row said BEFORE `live_login_start` stamped 'authenticating' over it — the row is
        # read before that stamp, so this is the pre-login truth. `terminal_status` restores it when
        # this session ends without signing in (§COMMCALC-13).
        self._status_before = row.get("auth_status")

    # ── thread-safe shared-state access ──────────────────────────────────────────────────────────
    def _set(self, phase=None, message=None, touch=True):
        with self._lock:
            if phase is not None:
                if phase != self.phase:
                    self._phase_since = time.time()   # a new phase gets its own full budget
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

    def _note_act(self, doing):
        """THE setter for "what is this session doing right now" (§COMMCALC-31). Cheap and lock-only —
        it is called on the hot path, so it never touches the page or the row."""
        with self._lock:
            self._act = str(doing)[:80]
            self._act_at = time.time()

    def activity(self):
        """THE reader of the same fact, for every operator surface: the step in flight, how long it has
        been in flight, and the age of the newest frame. Dereferenced by `state()` and `frame_since()`
        so no surface composes its own answer. Ages are seconds, rounded, and None before the first
        frame — never a fabricated 0 that would read as "a frame just arrived"."""
        now = time.time()
        with self._lock:
            doing, since, frame_at = self._act, self._act_at, self._last_frame_at
        return {"doing": doing,
                "doing_age_s": round(max(0.0, now - since), 1) if since else None,
                "frame_age_s": round(max(0.0, now - frame_at), 1) if frame_at else None}

    def _note_viewer(self):
        """Record that an operator surface just asked about this session (§COMMCALC-30). Called by the
        two methods that ANSWER such a question — `state` and `frame_since` — so presence is recorded
        wherever the question is answered rather than at each endpoint that might forget to. It is
        deliberately NOT `_touch`: watching is not acting, and conflating them would let an abandoned
        tab hold a browser open forever."""
        with self._lock:
            self._last_seen_at = time.time()

    def viewer_present(self, now=None):
        """THE one reader of "is a human watching this session right now" (§COMMCALC-30). PURE and never
        raises: a session nobody has polled reads as NOT watched, which keeps the pre-2026-10-23
        budgets exactly, so the failure mode is the old behaviour rather than a browser held open."""
        try:
            with self._lock:
                seen = self._last_seen_at
            if not seen:
                return False
            return ((time.time() if now is None else now) - seen) <= _VIEWER_PRESENT_SECONDS
        except Exception:
            return False

    def _store_frame(self, shot, metadata=None):
        """Record a new frame (from the CDP screencast OR an explicit screenshot) + bump the sequence.

        The frame's own GEOMETRY is stored beside it (§COMMCALC-47). A click arrives as a fraction of
        the image the operator was looking at, so it must be translated through THAT image's account of
        itself; before this, every click was translated through the configured viewport, which is a
        different thing on any page whose visible size is not the window."""
        if not shot:
            return
        geom = _lf().frame_geometry(metadata, self._viewport)
        with self._lock:
            self._shot = shot
            self._frame_geom = geom
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
        the status `terminal_status` answers — 'error' on a hard stop, mirroring _do_portal_login's
        failure shape, and otherwise the row's PRE-LOGIN status restored, never left in flight). Runs at
        EVERY stop of the session — proxy_error, auth failure, idle timeout, operator Close, or a crash —
        so the panel reflects THIS live session's final screen instead of a stale earlier attempt's frame.
        Best-effort / never raises, like _store_login_shot and the other diag writes. 'authenticated' owns
        its own status via _on_authenticated, so only the frame is refreshed there (not the status line)."""
        try:
            with self._lock:
                phase, message, shot = self.phase, self.message, self._shot
            if self.persist and phase in ("error", "cancelled"):
                upd = {"auth_message": (message or "")[:400],
                       "last_run_at": datetime.now(timezone.utc).isoformat(),
                       # Nothing is in flight any more, on either terminal phase. 'authenticated'
                       # clears the same two fields in _on_authenticated, so a finished session
                       # leaves one shape whichever way it finished.
                       "auth_status": terminal_status(phase, self._status_before),
                       "pending_started_at": None}
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
        self._note_viewer()                       # answering about the session IS a sighting (§COMMCALC-30)
        act = self.activity()                     # §COMMCALC-31 — taken BEFORE the lock below (it locks too)
        # Taken before the lock for the same reason `act` is: `signed_in` takes it too.
        signed = self.signed_in()
        with self._lock:
            pr = self.pull_result
            return {
                "phase": self.phase,
                # PUBLISHED so no surface has to re-derive it from the phase word. The live window
                # decides from this whether leaving may cancel the session; `pulling` is signed in
                # and a string compare against 'authenticated' there cost the owner a session he had
                # just made (2026-10-31).
                "signed_in": signed,
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
                "activity": act,
            }

    def frame_since(self, since):
        """Lightweight frame poll: return the newest frame ONLY if `_seq` advanced past `since`, else a tiny
        unchanged payload (phase/message always included so the panel stays fresh without shipping a JPEG)."""
        self._note_viewer()                       # answering about the session IS a sighting (§COMMCALC-30)
        try:
            since = int(since)
        except Exception:
            since = 0
        act = self.activity()                     # §COMMCALC-31 — every tick, whether or not a frame moved
        with self._lock:
            seq, phase, message, shot = self._seq, self.phase, self.message, self._shot
        if shot and seq > since:
            return {"seq": seq, "phase": phase, "message": message, "changed": True, "activity": act,
                    "shot": "data:image/jpeg;base64," + shot}
        return {"seq": seq, "phase": phase, "message": message, "changed": False, "activity": act,
                "shot": None}

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

    def calibrate(self, result_q=None):
        """Enqueue a CALIBRATION CAPTURE on THIS live authenticated browser.

        The sibling of `pull`, deliberately: a capture reaches a report the same way a pull does, on
        the same authenticated page, through the same single-threaded loop. It READS the page's own
        controls and SUGGESTS a declaration — it writes nothing, neither to the portal nor to the
        source row, because a capture is evidence about a page, not a decision about a tenant's
        config (§COMMCALC-39)."""
        self._touch()
        self._cmd_q.put(("CALIBRATE", result_q))

    def signed_in(self):
        """Has this session passed the portal's sign-in? THE one answer, over `_SIGNED_IN_PHASES`.

        Deliberately says nothing about whether the worker is still alive or what the session can be
        asked to DO — `can_pull` / `can_calibrate` add those, because "signed in" is the fact a
        surface needs to decide whether leaving the window may cancel the session, and a liveness
        check would make a momentary thread hiccup read as "never signed in" and cancel it."""
        with self._lock:
            return self.phase in _SIGNED_IN_PHASES

    def can_calibrate(self):
        """True only when the worker is alive AND we are post-authentication AND a capture is
        configured. Same shape and the same honest fallback as `can_pull`: a dead or aged-out session
        returns False and the caller says so rather than hanging."""
        ok_phase = self.signed_in()
        alive = bool(self._thread and self._thread.is_alive())
        return ok_phase and alive and self.calibrate_fn is not None

    def run_calibrate_blocking(self, timeout=300):
        """Called from the request thread: enqueue a capture and block for its result. Returns None if
        the session cannot capture or times out — never a half-filled declaration, because a partial
        capture confirmed by an operator is a filter that silently will not be set."""
        if not self.can_calibrate():
            return None
        rq = queue.Queue(maxsize=1)
        self.calibrate(rq)
        try:
            return rq.get(timeout=timeout)
        except queue.Empty:
            return None

    def can_pull(self):
        """True only when the worker is alive AND we're post-authentication (browser still open) AND a
        pull_fn is configured — so the router can decide to reuse the live session instead of a cold
        storage_state restore. A dead/aged-out session returns False → the router falls back cleanly."""
        ok_phase = self.signed_in()
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

    def _next_input(self):
        """Pop the next HUMAN INPUT only (never a SUBMIT_CODE / RESEND / CANCEL / PULL), so the command
        loop can drain a burst of clicks and keystrokes without reordering the other commands."""
        try:
            return self._hi_q.get_nowait()[1]
        except queue.Empty:
            return None

    def operator_input_pending(self):
        """THE one reader of "is the operator still acting" (§COMMCALC-27). Work that only makes sense
        once they have STOPPED — settling the page, re-classifying for auth — is skipped while this is
        true, because it would otherwise be charged to the latency of their next click. Never raises:
        an unreadable queue reads as "not pending", which keeps the old always-settle behaviour."""
        try:
            return self._hi_q.qsize() > 0
        except Exception:
            return False

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
                # Chrome says what this frame SHOWS — the visible page size, the page scale and the
                # non-page strip at the top. It used to be dropped here, which is what made a click
                # land off target on any page whose visible size is not the window (§COMMCALC-47).
                meta = params.get("metadata") if isinstance(params, dict) else None
            except Exception:
                data, fsid, meta = None, None, None
            if data:
                self._store_frame(data, metadata=meta)
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
        # A SECOND thread, because the first one is exactly what can be stuck. The worker cannot time
        # itself out: a blocked `p.chromium.launch()` or `page.goto()` never returns to check a clock,
        # which is why phase 'starting' could sit forever. Playwright gives no way to interrupt that
        # call, so the watchdog does not try to kill it — it ENDS THE SESSION, writes the error to the
        # row, and frees the operator to start a new one. The stuck call dies with the daemon thread.
        self._watchdog = threading.Thread(target=self._watch, name="live-login-watch-%s" % self.sid,
                                          daemon=True)
        self._watchdog.start()

    def _watch(self):
        """Fail a session that stops making progress BEFORE the command loop is entered.

        Stands down the moment `_loop_entered` is set, so the operator's own window (20 minutes to
        fetch a code, 8 on a human check) is owned by `inactivity_limit` alone and is never shortened
        by this. Best-effort and silent on its own errors — a watchdog that raises would be one more
        way to hang."""
        while True:
            time.sleep(_PROGRESS_POLL_S)
            try:
                with self._lock:
                    phase, entered, since = self.phase, self._loop_entered, self._phase_since
                if phase in _TERMINAL or phase == "authenticated":
                    return
                if entered:
                    return
                stalled = time.time() - float(since or 0)
                limit = preloop_limit(phase)
                if stalled <= limit:
                    continue
                self._set(phase="error",
                          message=("The live login stopped responding while %s — no progress for %d "
                                   "seconds, so the session was ended rather than left waiting. Start "
                                   "it again; if it stops at the same point, the server could not open "
                                   "a browser or the portal did not answer it."
                                   % (_PRELOOP_DOING.get(phase, "starting up"), int(stalled))))
                self._persist_diag()
                return
            except Exception:
                return

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
                assert_browser_allowed()   # SERVICE_ROLE=api → no Chromium on the user-facing API service
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
            self._note_act("Opening the browser")
            browser = vp._launch(p)
            ctx = vp._new_context(browser, proxy=vp._proxy_arg(self.proxy_url))
            page = ctx.new_page()
            # The window the context was given, so a frame that carries no metadata of its own still
            # has a stated fallback rather than a number in the click handler (§COMMCALC-47).
            try:
                self._viewport = dict(page.viewport_size or {})
            except Exception:
                self._viewport = None
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
                self._note_act("Loading the portal login page")
                vp._goto_login(page, base, login_url=self._preauth_login_url(vp))
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

            # A progressive step may precede the password field: a page that asks for an account or
            # company id, or just the user id, and reveals the password only once that is done. Which
            # control is which is read off the page by `login_progressive` (index §COMMCALC-46) — this
            # used to be one vendor's element ids spelled here, which is why every OTHER portal with
            # the same shape reported a broken login form.
            if not vp._password_frame(page)[1]:
                self._progressive_first_step(page)

            login_fr, pw_el = vp._password_frame(page)
            if not pw_el:
                # LEAD WITH WHAT THE PAGE SAID. "Could not find the login form" is true of a refusal
                # page too, and saying it first makes the real answer read like a footnote — which is
                # exactly how an operator ends up checking form markup on a page that refused him.
                _v = _page_verdict(vp, page)
                self._set(phase="error",
                          message=(_v["note"] if _v
                                   else "Could not find the login form. " + _wall_note(vp, page)))
                self._capture(page)
                return

            # HUMAN-DRIVEN-UNTIL-AUTH decision. If a human check gates the sign-in, or the human has
            # already taken control, do NOT auto-submit — hand the login to the human on the live page.
            # `_human_check_kind` answers "visible" / "invisible" / "", and the two kinds get DIFFERENT
            # instructions: a visible challenge is already on screen, an invisible one appears only
            # once Sign in is pressed, so telling the operator to "complete the box on the screen"
            # would send them looking for something that is not there yet.
            kind = _human_check_kind(vp, page)
            with self._lock:
                human = self._human_driving
            if kind or human:
                if kind:
                    try:
                        _rep = vp.prefill_login(page, login_fr, pw_el, self.account_id,
                                                self.user, self.pw)
                    except Exception:
                        _rep = None
                    # Two homes, two facts: what GATES the submit, and what is in the form. The
                    # second is read from the fill's own report — never asserted (owner 2026-10-31).
                    self._set(phase="human_action",
                              message=(_HUMAN_CHECK_MESSAGE[kind] + " "
                                       + _prefill_note(_rep)).strip())
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

    def _progressive_first_step(self, page):
        """The page's own first step, when the password is not on it yet: fill what it asks for, press
        its continue control, wait for the password field.

        ONE home for this, shared with the unattended login (`login_progressive.advance_past_first_step`,
        index §COMMCALC-46). It used to be this method, spelling `#companyId` / `#btnSubmit` /
        `#password` — one portal's markup, which made the step work for that portal and for nothing
        else. Best-effort: a portal without a first step is untouched, and the note is kept for the
        operator's screen rather than raised."""
        from app.modules.commcalc import login_progressive as lp
        try:
            note = lp.advance_past_first_step(
                page,
                {"account_id": self.account_id, "username": self.user},
                declared=(self.portal_calibration or {}).get("login"))
        except Exception:
            return ""
        if note:
            self._note_act(note)
        return note

    def _preauth_login_url(self, vp):
        """THIS connector's own pre-auth sign-in page, or None when it has not declared one.

        The bot-wall fallback inside `vp._goto_login` navigates here. Passing nothing used to inherit
        the master-agent portal's own id-server, so EVERY other connector — the POS-reports kind, the
        supply-vendor kind — retried at a different vendor's login server, spending up to three
        60-second attempts on an address that has nothing to do with the portal the operator
        configured (owner's Team Portal live login, 2026-10-15, index §COMMCALC-10).

        None for a connector KIND whose address is per-tenant config: `connector_registry` carries no
        host for those rows BY DESIGN (RULE TWO), so there is no house default to fall back to and
        inventing one is worse than not retrying. The caller then reports "could not find the login
        form" against the page the operator actually configured, which is the honest answer."""
        if self.processor in _VIDAPAY_PROCESSORS:
            return getattr(vp, "LOGIN_URL", None)
        if self._is_b2b:
            return getattr(vp, "B2BSOFT_URL", None)
        return None

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
        with self._lock:
            self._loop_entered = True     # the inactivity rule owns the session from here
        last_poll = 0.0
        while True:
            # Service EVERY queued human input FIRST (§COMMCALC-27, ordering §COMMCALC-31). Servicing one
            # per iteration charged each click a full `_pump` (80ms on CDP, 300ms without) plus the
            # previous click's settle, so a burst drained at a few events a second and the window felt
            # frozen. Draining here — ahead of the pump and the page re-read, not after them — is what
            # makes that true of the FIRST click of a burst as well as the rest of it. Bounded by
            # `_INPUT_DRAIN_MAX` so the idle check and the pump still run.
            drained = 0
            while drained < _INPUT_DRAIN_MAX:
                ev = self._next_input()
                if ev is None:
                    break
                drained += 1
                self._do_input(page, ctx, vp, ev)
                if self.snapshot_phase() == "authenticated":
                    return self._post_auth_loop(page, ctx, vp)   # a human click finished sign-in
            self._note_act("Streaming the live screen")
            self._pump(page)
            if detect_due(time.time(), last_poll, self.operator_input_pending()):
                last_poll = time.time()
                self._note_act("Checking the page")
                self._preauth_detect(page, ctx, vp)
                ph = self.snapshot_phase()
                if ph == "authenticated":
                    return self._post_auth_loop(page, ctx, vp)
                if ph in _TERMINAL:
                    return
            with self._lock:
                idle = time.time() - self._last_activity
                ph = self.phase
            limit = inactivity_limit(ph, self.viewer_present())
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
        with self._lock:
            self._loop_entered = True     # reachable straight from _drive on the fast path
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
            # The same drain as pre-auth, in the same place in the tick (§COMMCALC-27 + §COMMCALC-31) —
            # "take control" on an authenticated session is the same operator typing into the same
            # single-threaded loop, so their input leads the tick here too.
            drained = 0
            while drained < _INPUT_DRAIN_MAX:
                ev = self._next_input()
                if ev is None:
                    break
                drained += 1
                self._do_input(page, ctx, vp, ev)
            self._note_act("Streaming the live screen")
            self._pump(page)
            with self._lock:
                idle = time.time() - self._last_activity
            # NOT a §COMMCALC-30 site, and excused with its reason rather than left silent: this
            # budget is the hold-open window for a signed-in browser, not a budget SHORTENED on the
            # assumption nobody is acting, so a watching human is not being cut short by it.
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
            if kind == "CALIBRATE":
                self._handle_calibrate(page, vp, cmd[1] if len(cmd) > 1 else None)
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

    def _handle_calibrate(self, page, vp, result_q):
        """Read this login's report pages and hand back the SUGGESTED declarations. Writes nothing.

        The page is left where the capture ended, and the phase goes back to `authenticated`, so the
        operator can look at the same screen the suggestion was read from. Best-effort throughout —
        a capture failure is reported, never crashes the session, because the session is also how the
        operator stays signed in."""
        self._set(phase="pulling", message="Signed in — reading this login's report pages…")
        self._note_act("Reading the report pages")
        self._capture(page)
        res = None
        try:
            if self.calibrate_fn is not None:
                res = (self.calibrate_fn(page, self._stop_requested)
                       if _takes_stop(self.calibrate_fn) else self.calibrate_fn(page))
            else:
                res = {"ok": False,
                       "error": "No calibration capture is configured for this live session."}
        except Exception as e:
            res = {"ok": False,
                   "error": ("Could not read the report pages: " + str(e))[:300]}
        if result_q is not None:
            try:
                result_q.put(res)
            except Exception:
                pass
        self._capture(page)
        self._set(phase="authenticated",
                  message="Signed in — the report pages were read; nothing was changed.")
        self._note_act("Waiting")

    def _handle_pull(self, page, vp, result_q):
        """Run the report pull on THIS live authenticated browser (never a cold restore) and hand the
        result back to the waiting request thread via `result_q`. Keeps the page open afterwards so the
        operator can pull again. Best-effort — a pull failure is reported, never crashes the session."""
        self._set(phase="pulling",
                  message="Signed in — fetching this login's reports on the live session…")
        self._note_act("Fetching the reports")
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
            self._note_act("Delivering your click")
            self._do_click(page, ctx, vp, ev.get("x"), ev.get("y"), double=(et == "dblclick"))
            return
        self._note_act("Delivering your typing" if et in ("type", "key") else "Delivering your input")
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
            # THE one translation (§COMMCALC-47): the geometry of the frame the operator was looking at,
            # falling back to the window only when that frame carried no account of itself.
            with self._lock:
                geom = self._frame_geom
            x, y = _lf().click_point(nx, ny, geom or _lf().frame_geometry(None, page.viewport_size))
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
        # …but ONLY when the operator has stopped (§COMMCALC-27). The burst below costs ~750ms of waits
        # plus a settle, and this loop is single-threaded, so running it with their next click already
        # queued charges that click for the previous one's verification — which is the "it freezes, I
        # have to press hard" the owner reported on 2026-10-22. The screencast keeps streaming either
        # way, and `_preauth_detect` re-classifies every `_CLASSIFY_POLL_S` regardless, so nothing is
        # lost: the LAST click of a burst has nothing pending behind it and still settles in full.
        if self.operator_input_pending():
            return
        self._note_act("Waiting for the page to respond to your click")
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
        self._note_act("Submitting the code")
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
                  auto_pull_gate=None, calibrate_fn=None):
    """Spawn (or REPLACE) the live session for `sid`. Non-blocking — the worker thread drives it.
    `pull_fn` (callable(page) -> pull result) runs the report pull on THIS live browser — automatically
    the moment the login authenticates, and again on every '▶ Pull now'. `persist_pull(result)` records
    that pull's outcome on the data_source row (honest last_run_at vs last_attempt_at).
    `calibrate_fn` (callable(page) -> suggestion) READS this login's report pages and suggests their
    declarations (§COMMCALC-39). It is never called automatically — only when an operator asks."""
    with _SESSIONS_LOCK:
        _prune_locked()
        old = _SESSIONS.get(sid)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass
        sess = LiveLoginSession(sid, org_id, row, persist, persist_shot, pull_fn, persist_pull,
                                auto_pull_gate, calibrate_fn)
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
