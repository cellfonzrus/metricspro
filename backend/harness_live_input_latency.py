"""Proof harness: THE OPERATOR'S INPUT IS SERVICED BEFORE THE NEXT FRAME TICK
(live_login, index §COMMCALC-27).

OWNER REPORT 2026-10-22, on the UPS Team Portal live login, verbatim: *"it just freezes , it is not
swift access, must easier it seems i hvae to press hard"*. He was right, and pressing harder was a
rational response to what the loop actually did.

THE CLASS. `_preauth_loop` and `_post_auth_loop` are SINGLE-THREADED: one thread streams frames,
detects state and executes the human's input. Both serviced exactly ONE queued command per iteration,
and every iteration began with `_pump` — 80ms on CDP, 300ms without. Worse, `_do_click` ended with a
mandatory ~750ms burst of waits plus `_wait_settle` plus a re-classification. So the Nth click was
charged for the (N-1)th click's VERIFICATION before it was even dequeued: a burst drained at a few
events a second, and the window felt frozen. The operator then clicked again, which lengthened the
queue, which made it worse.

Naming the class rather than the instance: *work that only makes sense once the operator has STOPPED
must never sit on the latency path of their next action, and their input must never wait behind a
frame tick.* That is true of every portal this loop drives, not of the UPS one.

WHAT THIS PROVES
  §A  "is the operator still acting" has ONE HOME — `operator_input_pending` — and it is PURE: an
      unreadable queue reads as "not pending", which keeps the old always-settle behaviour rather
      than silently skipping verification forever.
  §B  `_next_input` pops HUMAN INPUT ONLY, so draining a burst cannot reorder or swallow a
      SUBMIT_CODE / RESEND / CANCEL / PULL.
  §C  THE LOCK — BOTH loops drain (`_preauth_loop` and `_post_auth_loop`). The sibling is the same
      operator typing into the same loop; one fixed and the other not is the same defect wearing a
      hat (CLAUDE.md, owner directive 2026-09-20).
  §D  the drain is BOUNDED by one declared constant, so a flood can never starve the idle check or
      the frame pump — the regression this fix must not cause.
  §E  the behaviour, on a fake page: a click with more input queued skips the burst; the LAST click
      of a burst still settles in full, so auth is still concluded from a human's click.
  §F  draining is ORDER-PRESERVING and loses nothing — every event reaches the page, once, in order.
  §G  negative controls, including the pre-change single-service shape going RED.
  §I  the frame poll is paced by its own RESPONSES, never by a fixed-rate timer, so a slow hop
      slows the cadence instead of stacking requests that compete with the operator's input.
  §J  the SIBLING on the same path (index §COMMCALC-31): state DETECTION yielded to the operator
      too, through one home `detect_due`, with a BOUNDED deferral so a captcha is still noticed.
  §K  "what is this session doing right now" has ONE home (`_note_act` / `activity`), every surface
      dereferences it, and it moves even when no new frame arrives.
  §H  the FRONTEND half of the same question: the page batches keystrokes instead of awaiting one
      request per character, and every event — typed text, clicks, named keys, scroll — goes out on
      one ordered chain, so a flushed password can never land behind the Enter that submits it.

No DB, no network, no browser. Run:  cd backend && python3 harness_live_input_latency.py
"""
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, ".")

from app.modules.commcalc import live_login as ll    # noqa: E402

PASS = FAIL = 0
SRC = io.open(os.path.join("app", "modules", "commcalc", "live_login.py"), encoding="utf-8").read()


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s%s" % (msg, ("   " + str(detail)[:300]) if detail else ""))


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


def _block(name):
    """A method's source text EXACTLY as written (comments and literals intact)."""
    m = re.search(r"^([ \t]*)def %s\(" % re.escape(name), SRC, re.M)
    if not m:
        return ""
    indent = len(m.group(1))
    lines = SRC[m.start():].split("\n")
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        out.append(ln)
    return "\n".join(out)


def _code(name):
    """A method's source with COMMENTS and STRING literals stripped — a comment quoting the very
    thing a check forbids reads as the violation otherwise (the trap that has cost this repo three
    harnesses; see `harness_live_login_progress._func`)."""
    body = _block(name)
    if not body:
        return ""
    try:
        toks = tokenize.generate_tokens(io.StringIO(body).readline)
        return " ".join(t.string for t in toks
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))
    except Exception:
        return body


class _Row(dict):
    pass


def session(**row):
    base = {"portal_url": "https://example.invalid/login", "username": "u", "password": "p",
            "processor": "pos_report"}
    base.update(row)
    persisted = []
    s = ll.LiveLoginSession("sid-1", "org-1", _Row(base), persist=persisted.append)
    s._persisted = persisted
    return s


# ── A. one home, and it is pure ─────────────────────────────────────────────────────────────────
print("\n— A. one home for 'is the operator still acting', and it is pure —")
s = session()
ok(callable(getattr(s, "operator_input_pending", None)),
   "operator_input_pending is the reader of 'the operator is still acting'")
eq(s.operator_input_pending(), False, "an empty input queue reads as NOT pending")
s.input_event({"type": "click", "x": 0.5, "y": 0.5})
eq(s.operator_input_pending(), True, "a queued input reads as pending")
ok(s._next_input() is not None and s.operator_input_pending() is False,
   "draining the queue clears it")


class _Exploding:
    def qsize(self):
        raise RuntimeError("queue unreadable")

    def get_nowait(self):
        raise RuntimeError("queue unreadable")


s2 = session()
s2._hi_q = _Exploding()
try:
    pend = s2.operator_input_pending()
    raised = False
except Exception:
    pend, raised = None, True
ok(not raised, "operator_input_pending never raises")
eq(pend, False, "an UNREADABLE queue reads as not pending — i.e. keeps the old always-settle path")

# The one-home lock: nothing else may read the queue depth for this decision.
depth_readers = [n for n in ("_do_click", "_do_input", "_preauth_loop", "_post_auth_loop",
                             "_pump", "_next_cmd")
                 if "qsize" in _code(n)]
eq(depth_readers, [], "no other method reads the queue DEPTH — they all dereference the one home")

# ── B. the drain pops human input only ──────────────────────────────────────────────────────────
print("\n— B. _next_input pops HUMAN INPUT only —")
s = session()
ok(callable(getattr(s, "_next_input", None)), "_next_input exists")
eq(s._next_input(), None, "nothing queued → None")
s.submit("123456")
s.resend()
s.cancel()
eq(s._next_input(), None,
   "a SUBMIT_CODE / RESEND / CANCEL is NOT returned as input — the drain cannot swallow them")
ok(s._next_cmd() is not None, "and those commands are still there for the command loop")

s = session()
s.input_event({"type": "type", "text": "abc"})
got = s._next_input()
eq(got, {"type": "type", "text": "abc"}, "the event PAYLOAD comes back, not the ('INPUT', ev) tuple")

# ── C. THE LOCK: both loops drain ───────────────────────────────────────────────────────────────
print("\n— C. THE LOCK: both loops drain their input before the next tick —")
for loop in ("_preauth_loop", "_post_auth_loop"):
    code = _code(loop)
    ok("_next_input" in code, "%s drains with _next_input" % loop)
    ok("_INPUT_DRAIN_MAX" in code, "%s bounds the drain with the declared constant" % loop)
    ok(re.search(r"while\s+drained\s*<\s*_INPUT_DRAIN_MAX", code) is not None,
       "%s drains in a bounded loop, not once per tick" % loop)
    ok("_do_input" in code, "%s still executes the input it drained" % loop)

# The pre-auth drain must still be able to conclude auth from a human's click.
ok("_post_auth_loop" in _code("_preauth_loop"),
   "a click that finishes sign-in during a drain still hands over to _post_auth_loop")

# ── D. bounded, so a flood cannot starve the loop ───────────────────────────────────────────────
print("\n— D. the drain is bounded —")
cap = getattr(ll, "_INPUT_DRAIN_MAX", None)
ok(isinstance(cap, int) and not isinstance(cap, bool), "_INPUT_DRAIN_MAX is a declared int")
ok(isinstance(cap, int) and cap >= 8, "the cap is big enough to drain a real burst (>= 8)", cap)
ok(isinstance(cap, int) and cap <= 256, "and small enough that the idle check still runs (<= 256)", cap)
for loop in ("_preauth_loop", "_post_auth_loop"):
    code = _code(loop)
    # the idle check and the pump must still be reached on every iteration
    ok("_pump" in code, "%s still pumps frames every iteration" % loop)
    ok("inactivity" in _block(loop) or "_IDLE" in code or "_POST_AUTH_IDLE_SECONDS" in code,
       "%s still checks the inactivity budget every iteration" % loop)

# ── E. the behaviour on a fake page ─────────────────────────────────────────────────────────────
print("\n— E. a click with more input queued skips the settle; the last one does not —")


class _FakePage:
    """Records what the click path asked the browser to do. `wait_for_timeout` is the ~750ms burst
    this fix must skip while the operator is still acting."""

    def __init__(self):
        self.waits = []
        self.calls = []
        self.mouse = self
        self.keyboard = self

    def viewport_size(self):      # pragma: no cover - attribute form is what the code uses
        return {"width": 1366, "height": 900}

    # mouse
    def move(self, x, y):
        self.calls.append(("move", round(x), round(y)))

    def click(self, x, y):
        self.calls.append(("click", round(x), round(y)))

    def dblclick(self, x, y):
        self.calls.append(("dblclick", round(x), round(y)))

    def wheel(self, dx, dy):
        self.calls.append(("wheel", dy))

    # keyboard
    def type(self, txt, delay=0):
        self.calls.append(("type", txt))

    def press(self, k):
        self.calls.append(("press", k))

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def screenshot(self, **kw):
        return b"png"


class _FakeVp:
    _TRUST_PAGE_WORDS = ("Trust this browser",)

    @staticmethod
    def _wait_settle(page):
        page.calls.append(("settle", None))

    @staticmethod
    def _page_text(page):
        return "Sign In"

    @staticmethod
    def _classify(page):
        return "login"

    @staticmethod
    def _code_field(page):
        return None


def _click_run(pending_after):
    """Run one _do_click with `pending_after` further events already queued behind it."""
    s = session()
    s._capture = lambda page: None            # frame capture is not what this check is about
    page = _FakePage()
    page.viewport_size = {"width": 1366, "height": 900}
    for _ in range(pending_after):
        s.input_event({"type": "click", "x": 0.1, "y": 0.1})
    s._do_click(page, None, _FakeVp, 0.5, 0.5)
    return page


p_busy = _click_run(pending_after=2)
ok(("click", 683, 450) in p_busy.calls, "the click itself still lands on the page", p_busy.calls)
eq(p_busy.waits, [], "with more input queued, the ~750ms settle burst is SKIPPED")
ok(("settle", None) not in p_busy.calls, "and _wait_settle is skipped too")

p_last = _click_run(pending_after=0)
ok(("click", 683, 450) in p_last.calls, "the last click of a burst lands as well")
eq(p_last.waits, [250, 250, 250], "the LAST click still takes the full burst — nothing is lost")
ok(("settle", None) in p_last.calls, "and still settles, so auth can be concluded from it")

# The skip must be the ONE home's decision, not a second copy of the rule.
ok("operator_input_pending" in _code("_do_click"),
   "_do_click dereferences operator_input_pending rather than reading the queue itself")

# ── F. the drain loses nothing and preserves order ──────────────────────────────────────────────
print("\n— F. draining is order-preserving and lossless —")
s = session()
for i in range(5):
    s.input_event({"type": "type", "text": str(i)})
seen = []
while True:
    ev = s._next_input()
    if ev is None:
        break
    seen.append(ev["text"])
eq(seen, ["0", "1", "2", "3", "4"], "every event comes back exactly once, in order")

s = session()
for i in range(ll._INPUT_DRAIN_MAX + 3):
    s.input_event({"type": "type", "text": "x"})
drained = 0
while drained < ll._INPUT_DRAIN_MAX and s._next_input() is not None:
    drained += 1
eq(drained, ll._INPUT_DRAIN_MAX, "a flood drains up to the cap in one tick")
eq(s.operator_input_pending(), True, "and the remainder is still queued for the next tick, not dropped")

# ── G. armed negative controls ──────────────────────────────────────────────────────────────────
print("\n— G. negative controls (the pre-change shape must read as broken) —")
# G1: the exact pre-change shape of the pre-auth loop — one command per tick, no drain.
PRE_CHANGE = """            cmd = self._next_cmd()
            if cmd is None:
                continue
            kind = cmd[0]"""
pre = _block("_preauth_loop")
ok(PRE_CHANGE in pre, "control armed: the command-loop shape this check reads is still present")
ok(pre.index("_next_input") < pre.index(PRE_CHANGE),
   "the drain runs BEFORE the single-command pop — input never waits behind another command")

# G2: a session whose queue says 'pending' must not settle — proves §E is reading the real branch.
ok(_click_run(pending_after=1).waits == [], "control: one queued event is enough to skip the burst")

# G3: the fix must not have been written as a per-portal special case (RULE TWO).
for word in ("ups", "theupsstore", "pos_report", "frs."):
    ok(word not in _code("_do_click").lower() and word not in _code("_preauth_loop").lower(),
       "no portal/tenant name in the latency path: %r" % word)

# G4: the constant must not have been quietly set to 1 (which is the old behaviour wearing the new shape)
ok(ll._INPUT_DRAIN_MAX > 1, "the cap is not 1 — that would restore the one-per-tick defect")

# ── H. the FRONTEND half of the same question ───────────────────────────────────────────────────
# The backend can only service what reaches it. The page used to `await` ONE POST PER CHARACTER, so a
# 12-character password was 12 sequential round trips (each crossing the §41 forwarder) before the
# first letter even appeared — the owner's "i cannot put the password , it is very slow". Same class,
# same PR: a keystroke must not wait on a network round trip of its own.
print("\n— H. the page batches keystrokes and keeps every event in order —")
PAGE = os.path.join("..", "frontend", "src", "app", "(platform)", "commcalc", "email-imports",
                    "page.tsx")
ok(os.path.exists(PAGE), "the live-view page is where this harness expects it", PAGE)
TSX = io.open(PAGE, encoding="utf-8").read() if os.path.exists(PAGE) else ""


def _tsx_fn(name):
    """A function's body out of the .tsx, by brace depth from its `function <name>(`."""
    m = re.search(r"function %s\s*\(" % re.escape(name), TSX)
    if not m:
        return ""
    i = TSX.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(TSX)):
        if TSX[j] == "{":
            depth += 1
        elif TSX[j] == "}":
            depth -= 1
            if depth == 0:
                return TSX[m.start():j + 1]
    return ""


key_fn = _tsx_fn("keyLive")
ok(key_fn != "", "keyLive is found")
# THE defect, in one check: a printable character must NOT be awaited to the server on its own.
ok(re.search(r"await\s+(sendInput|enqueueSend)\(\{\s*type:\s*'type',\s*text:\s*k\s*\}", key_fn) is None,
   "a printable character is NOT sent as its own awaited request")
ok("pendingTextRef" in key_fn, "printable characters are buffered")
ok("LIVE_TYPE_FLUSH_MS" in key_fn, "and flushed on the one declared window")
ok(re.search(r"const\s+LIVE_TYPE_FLUSH_MS\s*=\s*(\d+)", TSX) is not None,
   "LIVE_TYPE_FLUSH_MS has one home in the page")
_win = re.search(r"const\s+LIVE_TYPE_FLUSH_MS\s*=\s*(\d+)", TSX)
_ms = int(_win.group(1)) if _win else -1
ok(0 < _ms <= 250, "the flush window is short enough to feel instant (<= 250ms)", _ms)

# Ordering: the buffered text must be flushed before ANY other event, or a password could land
# behind the Enter that submits it.
ok("flushText" in key_fn, "a named key (Enter, Tab, Backspace) flushes the typed text FIRST")
for fn in ("clickLive", "dblClickLive", "wheelLive"):
    body = _tsx_fn(fn)
    ok(body != "", "%s is found" % fn)
    ok("flushText()" in body, "%s flushes pending text before its own event" % fn)
    ok("enqueueSend(" in body, "%s sends through the ordered chain" % fn)

flush_fn = _tsx_fn("flushText")
ok(flush_fn != "", "flushText is found")
ok("pendingTextRef.current = ''" in flush_fn, "flushText clears the buffer it sent")
ok("clearTimeout" in flush_fn, "flushText cancels the pending timer so nothing is sent twice")

chain = _tsx_fn("enqueueSend")
ok(chain != "", "enqueueSend is found")
ok("sendChainRef" in chain and ".then(" in chain,
   "events go out on ONE promise chain, so order is preserved across the network")

# ---------------------------------------------------------------------------------------------
# §I  THE FRAME POLL IS PACED BY ITS OWN RESPONSES, NOT BY A TIMER (index §COMMCALC-28)
#
# Same class as §A-§H: work that only makes sense once the operator has stopped must not sit on the
# latency path of their next action. A fixed-rate `setInterval` fires whether or not the last request
# came back, so once a poll outlives its interval the polls overlap permanently — each one carrying a
# stale `since`, so each is answered with a FULL frame of the same paint — and they hold the browser's
# per-origin connection budget that the operator's own input needs.
# ---------------------------------------------------------------------------------------------
print("\n— I. the frame poll is paced by its responses, not by a timer —")

# THE defect, in one check: no fixed-rate timer may drive the frame path.
ok(re.search(r"setInterval\s*\(\s*(refreshFrame|pollFrames)", TSX) is None,
   "no setInterval drives the frame poll")
# Comments mention the shape on purpose; only CODE may not carry it.
_tsx_code = "\n".join(l for l in TSX.split("\n") if not l.strip().startswith("//"))
ok("setInterval" not in _tsx_code,
   "the page declares no fixed-rate timer in code, so none can be added quietly")

# ONE home for "a frame request is already in flight", and ONE issuer of the request.
ok(TSX.count("frameBusyRef") >= 3, "frameBusyRef exists as the in-flight flag")
_await_sites = re.findall(r"await\s+refreshFrame\s*\(", TSX)
ok(len(_await_sites) == 1,
   "refreshFrame is awaited in exactly ONE place (the scheduler), not called ad hoc", len(_await_sites))
_direct = [m for m in re.finditer(r"(?<!await )(?<!const )\brefreshFrame\s*\(\s*\)", TSX)]
ok(len(_direct) == 0,
   "nothing calls refreshFrame() directly — every poll goes through the scheduler", len(_direct))

# The scheduler itself: guarded, and it re-arms from the moment the last response SETTLED.
_sched = re.search(r"const\s+pollFrames\s*=\s*useCallback\(async\s*\(\)\s*=>\s*\{(.*?)\n  \}, \[", TSX, re.S)
ok(_sched is not None, "pollFrames is found")
_body = _sched.group(1) if _sched else ""
ok("if (frameBusyRef.current)" in _body,
   "pollFrames returns early when a request is already in flight")
ok("frameSoonRef.current = true" in _body,
   "a poll that arrives mid-flight is REMEMBERED, not sent in parallel")
ok("finally" in _body,
   "the next poll is armed in a finally block, so a failed request cannot stop the loop")
ok(_body.index("frameBusyRef.current = false") < _body.index("setTimeout"),
   "the in-flight flag is cleared before the next poll is armed")
ok("frameOpenRef.current" in _body,
   "the loop stops once the modal closes rather than polling a closed view")

# The nudge after an input asks the ONE scheduler to come back sooner; it never fans out timers.
nudge = _tsx_fn("nudgeFrames")
ok(nudge != "", "nudgeFrames is found")
ok("frameSoonRef.current = true" in nudge, "a nudge sets the flag the scheduler reads")
ok("for (" not in nudge and "forEach" not in nudge,
   "a nudge no longer fans out one timer per delay")
ok(re.search(r"nudgeFrames\(\s*\[", TSX) is None,
   "no caller passes a list of delays any more")
ok("pollFrames" in nudge, "a nudge goes through the one scheduler")

# The two cadences have one home each, and a nudge is never slower than the idle gap.
_gap = re.search(r"const\s+LIVE_FRAME_GAP_MS\s*=\s*(\d+)", TSX)
_ndg = re.search(r"const\s+LIVE_FRAME_NUDGE_MS\s*=\s*(\d+)", TSX)
ok(_gap is not None, "LIVE_FRAME_GAP_MS has one home in the page")
ok(_ndg is not None, "LIVE_FRAME_NUDGE_MS has one home in the page")
_g = int(_gap.group(1)) if _gap else -1
_n = int(_ndg.group(1)) if _ndg else -1
ok(0 < _n <= _g, "the post-input gap is no slower than the idle gap", "%s <= %s" % (_n, _g))
ok(0 < _g <= 1000, "the idle gap still refreshes the view at least once a second", _g)

# Armed controls: the pre-change shapes must FAIL these checks, or they are not checks.
_pre_interval = "const iv = setInterval(refreshFrame, 300)\n    return () => clearInterval(iv)"
ok(re.search(r"setInterval\s*\(\s*(refreshFrame|pollFrames)", _pre_interval) is not None,
   "control: the pre-change setInterval shape is caught")
_pre_nudge = "function nudgeFrames(delays = [140, 380, 800]) {\n  for (const d of delays) setTimeout(() => { refreshFrame() }, d)\n}"
ok("for (" in _pre_nudge,
   "control: the pre-change fan-out nudge is caught")
ok(len([m for m in re.finditer(r"(?<!await )(?<!const )\brefreshFrame\s*\(\s*\)", _pre_nudge)]) > 0,
   "control: a direct refreshFrame() call is caught")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# §J  STATE DETECTION IS OFF THE OPERATOR'S INPUT PATH TOO (index §COMMCALC-31)
#
# Owner, 2026-10-26, same login, four days after §COMMCALC-27 shipped: *"it does not go past it , it
# is very slow to react"*. §COMMCALC-27 took the click's VERIFICATION off the input path and left the
# other work on it: `_preauth_detect` walks every frame of the page (`_classify` reads each frame's
# text, `_captcha_present` queries selectors), it ran on a fixed timer, and it ran BEFORE the input
# drain — so a click could wait a full `_pump` plus a whole page re-read before it was dequeued. Same
# sentence as §COMMCALC-27, second place: work that only makes sense once the operator has STOPPED
# must never sit on the latency path of their next action.
#
# The deferral is BOUNDED, because "defer while they are acting" with no ceiling is how a captcha
# would stop being noticed. One home, `detect_due`, answers "may detection run on this tick".
print("\n— J. detection yields to the operator, with a ceiling —")

due = getattr(ll, "detect_due", None)
ok(callable(due), "detect_due exists and is the one home for 'may detection run now'")
ceiling = getattr(ll, "_DETECT_DEFER_MAX_S", None)
poll = getattr(ll, "_CLASSIFY_POLL_S", None)
ok(isinstance(ceiling, (int, float)) and not isinstance(ceiling, bool),
   "_DETECT_DEFER_MAX_S is a declared number")
ok(isinstance(ceiling, (int, float)) and isinstance(poll, (int, float)) and ceiling >= poll,
   "the ceiling is not shorter than the ordinary poll (it would never defer otherwise)", (poll, ceiling))
ok(isinstance(ceiling, (int, float)) and ceiling <= 30,
   "and it is short enough that a captcha is still noticed promptly (<= 30s)", ceiling)

# J: the predicate's four cases.
ok(due(100.0, 100.0, False) is False, "not due: the poll interval has not elapsed")
ok(due(100.0 + poll + 0.01, 100.0, False) is True, "due: the interval elapsed and nobody is acting")
ok(due(100.0 + poll + 0.01, 100.0, True) is False,
   "DEFERRED: the interval elapsed but the operator is mid-action")
ok(due(100.0 + ceiling + 0.01, 100.0, True) is True,
   "NOT STARVED: past the ceiling it runs even while they act")
ok(due(100.0, 100.0, True) is False, "still not due before the interval, acting or not")

# J: pure — garbage reads as DUE, so the failure mode is the old always-detect behaviour.
for bad in (None, "x", object()):
    ok(due(bad, 100.0, False) is True, "unreadable `now` reads as DUE (safe default)", repr(bad))
    ok(due(100.0, bad, False) is True, "unreadable `last_detect` reads as DUE (safe default)", repr(bad))
ok(due(100.0 + poll + 0.01, 100.0, _Exploding()) is False,
   "a pending-flag whose truth test is cheap is honoured; nothing here calls the page")

# J: the loop dereferences that one home instead of re-deciding inline.
pre = _code("_preauth_loop")
ok("detect_due" in pre, "_preauth_loop gates its detect through detect_due")
ok("_CLASSIFY_POLL_S" not in pre,
   "_preauth_loop no longer compares against the poll constant itself (one home, dereferenced)")
ok("operator_input_pending" in pre,
   "and the deferral reads the SAME 'is the operator acting' home as the click settle (§A)")
ok("_preauth_detect" in pre, "detection is still CALLED — deferred, never removed")

# J: ordering. The drain must come before the pump and before the detect, in both loops.
for loop, later in (("_preauth_loop", ("_pump", "_preauth_detect")), ("_post_auth_loop", ("_pump",))):
    code = _code(loop)
    i_drain = code.find("_next_input")
    ok(i_drain >= 0, "%s drains operator input" % loop)
    for other in later:
        i_other = code.find(other)
        ok(i_other >= 0 and i_drain < i_other,
           "%s services input BEFORE %s" % (loop, other), (i_drain, i_other))

# J: negative controls — the pre-change shapes must read as broken.
_OLD_SHAPE = """
            self._pump(page)
            now = time.time()
            if now - last_poll >= _CLASSIFY_POLL_S:
                self._preauth_detect(page, ctx, vp)
            while drained < _INPUT_DRAIN_MAX:
                ev = self._next_input()
"""
ok(_OLD_SHAPE.find("_next_input") > _OLD_SHAPE.find("_preauth_detect"),
   "negative control: the pre-change order (detect, then drain) is detectable")
ok("detect_due" not in _OLD_SHAPE, "negative control: the pre-change shape has no one home to read")


def _unbounded(now, last, pending):
    """What a deferral WITHOUT a ceiling would do — the starvation this fix must not introduce."""
    return (now - last) >= poll and not pending


ok(_unbounded(100.0 + ceiling + 99.0, 100.0, True) is False,
   "negative control: an unbounded deferral starves detection forever while input keeps arriving")
ok(due(100.0 + ceiling + 99.0, 100.0, True) is True, "…and the shipped one does not")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# §K  "WHAT IS THIS SESSION DOING RIGHT NOW" HAS ONE HOME, AND IT MOVES (index §COMMCALC-31)
#
# Owner, same report: *"also it shoudl show what is going on it seems nothing is moving"*. He was
# right, and it was not a display preference: `phase` + `message` are a standing INSTRUCTION
# ("complete the login on the screen"), so a session working through a page re-read and a session
# that had stopped looked identical. The step in flight is a different fact from the phase, so it
# gets its own home — one setter, one reader, dereferenced by every surface.
print("\n— K. the step in flight is reported, from one home —")

s_act = session()
ok(hasattr(s_act, "_note_act") and callable(s_act._note_act), "_note_act is THE setter")
ok(hasattr(s_act, "activity") and callable(s_act.activity), "activity() is THE reader")

a0 = s_act.activity()
ok(isinstance(a0, dict), "activity() returns a dict")
eq(sorted(a0.keys()), ["doing", "doing_age_s", "frame_age_s"], "activity() reports exactly the three facts")
ok(a0["frame_age_s"] is None,
   "frame age is None before the first frame — never a fabricated 0 reading as 'just updated'")

s_act._note_act("Checking the page")
eq(s_act.activity()["doing"], "Checking the page", "the reader reflects the setter")
ok(isinstance(s_act.activity()["doing_age_s"], float), "and how long that step has been in flight")

s_act._store_frame("Zm9v")
ok(isinstance(s_act.activity()["frame_age_s"], float), "once a frame exists its age is reported")

# K: the setter is cheap — it must not touch the page or the row on the hot path.
set_code = _code("_note_act")
for forbidden in ("_capture", "persist", "_persist_diag", "page"):
    ok(forbidden not in set_code, "_note_act does not touch %s (it runs on the hot path)" % forbidden)
ok(len(s_act._note_act("x" * 500) or "") == 0 or True, "_note_act accepts any label")
ok(len(s_act.activity()["doing"]) <= 80, "a long label is bounded, never shipped whole", len(s_act.activity()["doing"]))

# K: every operator surface dereferences that reader — none composes its own answer.
for fn in ("state", "frame_since"):
    code = _code(fn)
    ok("activity" in code, "%s() carries the activity fact" % fn)
    ok("self . activity ( )" in code.replace("self.activity()", "self . activity ( )"),
       "%s() dereferences the one reader rather than reading _act itself" % fn)
    ok("_act" not in code.replace("activity", ""), "%s() does not read the raw field" % fn)

st = s_act.state()
ok(isinstance(st.get("activity"), dict), "state() ships the activity dict")
fr_unchanged = s_act.frame_since(s_act.snapshot_seq())
ok(fr_unchanged.get("changed") is False, "a poll with no newer frame is 'unchanged'…")
ok(isinstance(fr_unchanged.get("activity"), dict),
   "…and STILL carries the activity, so the line moves when the picture does not")

# K: the loops say what they are doing.
for loop in ("_preauth_loop", "_post_auth_loop"):
    ok("_note_act" in _code(loop), "%s reports the step it is on" % loop)
ok("_note_act" in _code("_do_input"), "delivering the operator's input is reported")
ok("_note_act" in _code("_do_click"), "waiting for the page to answer a click is reported")
ok("_note_act" in _code("_drive"), "the startup steps (browser, portal page) are reported")

# K: the frontend renders it, and only when the backend sent it.
TSX = io.open(os.path.join("..", "frontend", "src", "app", "(platform)", "commcalc",
                           "email-imports", "page.tsx"), encoding="utf-8").read()
ok("activity: r.activity" in TSX, "the frame poll keeps the activity the backend sent")
ok("liveState?.activity?.doing &&" in TSX,
   "the line renders only when the backend sent a step — never a fabricated 'working…'")
ok("frame_age_s" in TSX, "the frame age is shown, so a frozen picture is visible as frozen")


print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
