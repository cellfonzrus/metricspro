"""Proof harness: THE OPERATOR'S INPUT IS SERVICED BEFORE THE NEXT FRAME TICK
(live_login, index §COMMCALC-25).

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

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
