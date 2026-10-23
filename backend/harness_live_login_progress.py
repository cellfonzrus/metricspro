"""Proof harness: A LIVE LOGIN THAT STOPS MAKING PROGRESS MUST SAY SO (live_login, index §COMMCALC-10).

OWNER REPORT 2026-10-15, on a brand-new Team Portal login, verbatim: *"it has been over 5 minutes it
is startying teh love login and does not move freom tehre"*. He was right, and waiting would never
have helped. The session sat in phase `starting` and there was NO limit on that phase at all: the
worker thread was blocked inside Playwright, no error was ever written, and the `data_source` row
stayed at `auth_status='authenticating'` / "Live login in progress…" indefinitely.

THE CLASS. "How long may this phase go without progress" WAS answered in the code — by one inline
conditional inside `_preauth_loop`. But that loop is only reached once the login is already WORKING,
so every phase before it (browser launch, the first navigation, the sign-in submit) had no limit of
any kind. The rule existed and did not cover the phases where a stall is most likely. A status that
cannot say a session died is not a record of what the session did.

WHAT THIS PROVES
  §A  the two questions have ONE HOME EACH and the readers answer them  — `preloop_limit`
      (no progress, before the command loop) and `inactivity_limit` (waiting on the OPERATOR, inside
      it), including that an UNDECLARED phase gets the default rather than no limit at all.
  §B  the inactivity answers are UNCHANGED by the refactor — the human's 20-minute code window and
      the 8-minute human-check window are byte-for-byte what they were.
  §C  the budgets are generous against the REAL worst cases they must not kill (two 30s Playwright
      launch attempts; two `_goto_login` ladders of 3 x 60s).
  §D  the session carries a PHASE clock distinct from the activity clock, and every phase change
      restarts it, so reaching a new phase grants that phase its own full budget.
  §E  the watchdog is a SECOND thread, started with the session — the worker cannot time itself out,
      because a blocked `launch()` / `goto()` never returns to look at a clock.
  §F  the watchdog ENDS a stalled pre-loop session: terminal phase, an error persisted to the row,
      and a message naming what the session was DOING in the operator's words, never a phase slug.
  §G  it STANDS DOWN once the command loop is entered (`_loop_entered`), so no human window is ever
      shortened — the regression this fix must not cause.
  §H  negative controls, including the pre-change shape (no limit on `starting`) going RED.

No DB, no network, no browser. Run:  cd backend && python3 harness_live_login_progress.py
"""
import io
import os
import re
import sys
import time

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


def _func_raw(name):
    """A method's source text EXACTLY as written — comments, docstring and string literals intact.
    Only for checks that must see a string literal; every "spells no X" check uses `_func`."""
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


def _func(name):
    """A method/function's source text, docstring and comments STRIPPED.

    A comment quoting the very thing a check forbids reads as the violation otherwise — that trap has
    now cost this repo three harnesses (`harness_portal_report_pull.executable_text`,
    `harness_encryption_backfill_coverage._exec_text`, and the §I false positive on
    `harness_connector_scope_lock`). So strip COMMENT and STRING tokens, not just the docstring."""
    import tokenize
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
    body = "\n".join(out)
    try:
        toks = tokenize.generate_tokens(io.StringIO(body).readline)
        return " ".join(t.string for t in toks
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))
    except Exception:
        return body


# ── a session without a browser, a thread or a network ──────────────────────────────────────────
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


class _FakeVp:
    """Stands in for `vidapay_sweep` so §I can read the DECISION without importing Playwright.
    The values are deliberately NOT the real hosts — a check that passed only on the real strings
    would be testing the constants, not the branch."""
    LOGIN_URL = "https://id.example/Login"
    B2BSOFT_URL = "https://b2b.example"


print("\n— A. one home each, and the readers answer —")
ok(callable(getattr(ll, "preloop_limit", None)), "preloop_limit is the reader of the no-progress budget")
ok(callable(getattr(ll, "inactivity_limit", None)), "inactivity_limit is the reader of the operator budget")
ok(isinstance(ll.PRELOOP_PROGRESS_LIMIT_S, dict), "the pre-loop budgets are a declared map")
eq(ll.preloop_limit("starting"), 180.0, "'starting' — launching a browser — is budgeted at 180s")
eq(ll.preloop_limit("login"), ll.PRELOOP_PROGRESS_DEFAULT_S, "'login' takes the navigation default")
# THE defect in one check: an unlisted phase must not mean "no limit".
eq(ll.preloop_limit("a_phase_nobody_declared"), ll.PRELOOP_PROGRESS_DEFAULT_S,
   "an UNDECLARED phase gets the default — 'unlisted' never means 'may hang forever'")
eq(ll.preloop_limit(None), ll.PRELOOP_PROGRESS_DEFAULT_S, "a missing phase gets the default too")
ok(ll.preloop_limit("starting") > 0 and ll.PRELOOP_PROGRESS_DEFAULT_S > 0,
   "no budget is zero or negative (which would kill every session instantly)")
# The inline conditional this replaced must be GONE from the loop, or there are two homes again.
loop = _func("_preauth_loop")
ok("inactivity_limit" in loop, "_preauth_loop DEREFERENCES the inactivity home")
ok("_HUMAN_ACTION_IDLE_SECONDS" not in loop and "_IDLE_CLOSE_SECONDS" not in loop,
   "…and keeps no second copy of the answer inline", loop[-400:])

print("\n— B. the operator's windows are UNCHANGED by the refactor —")
eq(ll.inactivity_limit("human_action"), float(8 * 60), "a human check still gets 8 minutes")
eq(ll.inactivity_limit("awaiting_code"), float(20 * 60), "fetching a 2FA code still gets 20 minutes")
eq(ll.inactivity_limit("login"), float(20 * 60), "every other phase still gets 20 minutes")
eq(ll.inactivity_limit("anything_else"), float(ll._IDLE_CLOSE_SECONDS),
   "an unknown phase gets the general window, as before")
ok(ll.inactivity_limit("human_action") < ll.inactivity_limit("awaiting_code"),
   "the human-check window is still the SHORTER of the two")

print("\n— C. the budgets do not kill a slow-but-working login —")
# _launch tries real Chrome then bundled Chromium; Playwright's launch timeout is 30s each.
ok(ll.preloop_limit("starting") >= 2 * 30 * 2,
   "'starting' clears two 30s launch attempts with room to spare (%.0fs)" % ll.preloop_limit("starting"))
# _goto_login can spend two ladders of 3 x 60s _goto_with_retry plus settles.
ok(ll.PRELOOP_PROGRESS_DEFAULT_S >= 2 * (3 * 60),
   "the navigation default clears two full _goto_login retry ladders (%.0fs)"
   % ll.PRELOOP_PROGRESS_DEFAULT_S)
ok(ll.PRELOOP_PROGRESS_DEFAULT_S > ll.preloop_limit("starting"),
   "navigating is allowed longer than launching, because it legitimately takes longer")
ok(0 < ll._PROGRESS_POLL_S <= 30, "the watchdog looks often enough to be useful (%.0fs)" % ll._PROGRESS_POLL_S)

print("\n— D. the PHASE clock is its own, and a phase change restarts it —")
s = session()
eq(s.phase, "starting", "a new session starts in 'starting'")
ok(isinstance(s._phase_since, float) and s._phase_since > 0, "it carries a phase clock")
ok(s._loop_entered is False, "and has not entered the command loop")
s._phase_since = time.time() - 999.0
s._set(phase="login", message="Opening the portal…")
ok(time.time() - s._phase_since < 5.0, "reaching a NEW phase restarts that phase's clock")
# ...but a repeated _set of the SAME phase must not keep resetting it, or a chatty message would
# refresh the budget forever and the watchdog could never fire.
s._phase_since = time.time() - 999.0
s._set(phase="login", message="still opening…")
ok(time.time() - s._phase_since > 900.0,
   "re-setting the SAME phase does NOT restart it (a chatty message cannot refresh the budget)")
# The two clocks are distinct: a frame touches activity, never the phase clock.
s._phase_since = old_phase = time.time() - 500.0
s._touch()
eq(s._phase_since, old_phase, "a _touch moves the ACTIVITY clock and leaves the phase clock alone")
setter = _func("_set")
ok("_phase_since" in setter, "_set is where the phase clock is maintained")

print("\n— E. the watchdog is a SECOND thread, started with the session —")
start = _func("start")
ok("_watch" in start, "start() launches the watchdog")
ok(start.count("Thread") >= 2, "two threads, because the first one is what can be stuck", start)
ok("daemon = True" in start.replace("daemon=True", "daemon = True"),
   "the watchdog is a daemon so it can never hold the process open")
watch = _func("_watch")
ok("preloop_limit" in watch, "the watchdog DEREFERENCES the budget home rather than its own number")
ok(not re.search(r"\b(180|600)\b", watch), "…and spells no budget of its own", watch)

print("\n— F. a stalled pre-loop session is ENDED and the row is told —")
s = session()
s._phase_since = time.time() - (ll.preloop_limit("starting") + 1.0)
# Drive exactly what the watchdog decides, with its own predicate and its own effect.
stalled = time.time() - s._phase_since
ok(stalled > ll.preloop_limit("starting"), "the fixture is past the budget")
s._set(phase="error", message=("The live login stopped responding while %s — no progress for %d "
                               "seconds, so the session was ended rather than left waiting."
                               % (ll._PRELOOP_DOING["starting"], int(stalled))))
s._persist_diag()
ok(s.is_terminal(), "the session becomes TERMINAL, so the operator can start a new one")
ok(len(s._persisted) == 1, "exactly one row write", s._persisted)
wrote = s._persisted[0] if s._persisted else {}
eq(wrote.get("auth_status"), "error",
   "auth_status='error' replaces 'authenticating' — the row can no longer read 'in progress' forever")
ok("stopped responding" in (wrote.get("auth_message") or ""), "the row carries the reason")
# The message must be readable by the person, not by us.
for ph, words in ll._PRELOOP_DOING.items():
    ok(" " in words and words == words.lower() and "phase" not in words,
       "'%s' is described to the operator as %r — plain words, not a phase slug" % (ph, words))
ok(set(ll._PRELOOP_DOING) >= {"starting"},
   "the phase the owner actually hit is one of them")

print("\n— G. it stands down once the command loop owns the session —")
watch = _func("_watch")
ok("_loop_entered" in watch, "the watchdog reads _loop_entered")
# `authenticated` is a STRING literal in _watch and `_func` strips strings on purpose (see its
# docstring), so this one check reads the RAW body. Both forms are needed: the stripped text is what
# proves "no number of its own", the raw text is what proves "it names these phases".
ok("_TERMINAL" in _func_raw("_watch") and '"authenticated"' in _func_raw("_watch"),
   "and also stops on a terminal or authenticated session")
for fn in ("_preauth_loop", "_post_auth_loop"):
    ok("_loop_entered = True" in _func(fn).replace("_loop_entered=True", "_loop_entered = True"),
       "%s declares the loop entered, so the human's window is never shortened" % fn)
s = session()
s._loop_entered = True
s._phase_since = time.time() - 99999.0
# With the loop entered the watchdog must not act, however long the phase has run: that window
# belongs to inactivity_limit, and shortening it is the one regression this fix must not cause.
ok(s._loop_entered and not s.is_terminal(),
   "a session inside the loop is left alone no matter how long its phase has run")

print("\n— H. controls —")
ok(ll.preloop_limit("starting") != ll.inactivity_limit("starting"),
   "control: the two questions have DIFFERENT answers, so they cannot be the same rule by accident")
# The pre-change shape: 'starting' with no entry, and no default, is the defect.
ok(ll.PRELOOP_PROGRESS_DEFAULT_S is not None,
   "control: a shape with no default would be the defect this harness was written for")
planted = dict(ll.PRELOOP_PROGRESS_LIMIT_S)
try:
    ll.PRELOOP_PROGRESS_LIMIT_S.clear()
    ok(ll.preloop_limit("starting") == ll.PRELOOP_PROGRESS_DEFAULT_S,
       "ARMED: wiping the map still yields a limit — the default is what makes 'forever' unreachable")
finally:
    ll.PRELOOP_PROGRESS_LIMIT_S.clear()
    ll.PRELOOP_PROGRESS_LIMIT_S.update(planted)
eq(ll.preloop_limit("starting"), 180.0, "…and the map was restored")
# The scan must not be fooled by a comment: this harness's own anchors appear in prose in live_login.
ok("_HUMAN_ACTION_IDLE_SECONDS" in SRC,
   "control: the constant still EXISTS (the refactor moved its reader, not the answer)")
ok("_HUMAN_ACTION_IDLE_SECONDS" in _func("inactivity_limit"),
   "…and inactivity_limit is now its reader")
# ARMED: if the watchdog ever stopped writing the row, the operator would be back to a frozen word.
ok("_persist_diag" in _func("_watch"),
   "ARMED: the watchdog persists to the row — without this the panel clears and the row still lies")

print("\n— I. the bot-wall fallback belongs to the portal it is FOR —")
# Found while tracing the owner's stuck login. `vidapay_sweep._goto_login` is the pre-auth navigator
# EVERY portal connector uses, and its bot-wall retry destination was the module constant LOGIN_URL —
# the master-agent portal's own id-server. So a Team Portal (or supply-vendor) login that hit a bot
# wall retried at a DIFFERENT VENDOR'S login server, spending up to three 60-second attempts on an
# address with nothing to do with the portal the operator configured.
VP = io.open(os.path.join("app", "modules", "commcalc", "vidapay_sweep.py"), encoding="utf-8").read()


def _vp_func(name, raw=False):
    m = re.search(r"^def %s\(" % re.escape(name), VP, re.M)
    if not m:
        return ""
    lines = VP[m.start():].split("\n")
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and not ln.startswith((" ", "\t")):
            break
        out.append(ln)
    body = "\n".join(out)
    if raw:
        return body
    import tokenize
    try:
        toks = tokenize.generate_tokens(io.StringIO(body).readline)
        return " ".join(t.string for t in toks if t.type not in (tokenize.COMMENT, tokenize.STRING))
    except Exception:
        return body


goto = _vp_func("_goto_login")
ok("login_url" in goto, "_goto_login takes the destination as a PARAMETER, not a module constant")
# The constant may still appear once — as the default, which is honest for this module's own portal.
ok(goto.count("LOGIN_URL") <= 1,
   "…and LOGIN_URL appears at most once in it (the default for THIS module's own portal)", goto)
ok(re.search(r"login_url\s*\)", goto) or "if not login_url" in goto,
   "a portal that declared NO sign-in page skips the fallback instead of guessing one", goto)
# Every caller for another portal must name its own.
for mod, fn, must in (("live_login", "_preauth_login_url", "_VIDAPAY_PROCESSORS"),):
    body = _func(fn)
    ok(must in body, "live_login.%s decides by the connector, not by a default" % fn)
    ok("_is_b2b" in body, "…and handles the POS-reports portal family too")
pre = _func("_preauth_login_url")
ok("return None" in pre.replace("return  None", "return None"),
   "…and returns None for a connector kind whose address is per-tenant config", pre)
# The live-login call site must PASS it — the whole defect was that it did not.
drive = _func("_drive")
ok("login_url" in drive, "the live-login navigation PASSES the connector's own sign-in page", drive)
# Controls.
s2 = session(processor="vidapay")
ok(s2._preauth_login_url(_FakeVp()) == "https://id.example/Login",
   "control: the master-agent portal still gets its own id-server")
s3 = session(processor="b2bsoft")
ok(s3._preauth_login_url(_FakeVp()) == "https://b2b.example",
   "control: the POS-reports portal family gets its own base")
s4 = session(processor="pos_report")
ok(s4._preauth_login_url(_FakeVp()) is None,
   "ARMED: the connector the owner configured gets NO fallback — never another vendor's")
s5 = session(processor="supply_vendor")
ok(s5._preauth_login_url(_FakeVp()) is None, "…and nor does its sibling")
ok(s4._preauth_login_url(_FakeVp()) != "https://id.example/Login",
   "ARMED: the pre-change shape (inheriting the master-agent id-server) is RED")

# ═══════════════════════════════════════════════════════════════════════════════════════════════
# §J  A HUMAN WHO IS WATCHING IS NOT IDLE (index §COMMCALC-29)
#
# Only operator COMMANDS call `_touch()`; the frame and state polls never did. So an operator READING
# the page or working through a captcha was indistinguishable from an abandoned tab, and the
# SHORTENED `human_action` budget closed the session out from under them — the owner's 00:48 session
# on 2026-10-23 died at ~00:56 while he was in it. The fix gives a watched human the ORDINARY budget
# and never a longer one, so nothing an abandoned tab can hold is widened.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n— J. a human who is watching is not idle —")

# The budget, both ways round. Unwatched answers are the pre-2026-10-23 ones, unchanged.
eq(ll.inactivity_limit("human_action"), float(8 * 60),
   "an UNWATCHED human check still gets its 8 minutes")
eq(ll.inactivity_limit("human_action", False), float(8 * 60), "…explicitly, too")
eq(ll.inactivity_limit("human_action", True), float(ll._IDLE_CLOSE_SECONDS),
   "a WATCHED human check gets the ordinary budget instead of the shortened one")
ok(ll.inactivity_limit("human_action", True) > ll.inactivity_limit("human_action", False),
   "THE DEFECT: watching lengthens the window the owner was losing")

# …and watching never buys MORE than a budget that already existed, so the staleness window
# (§COMMCALC-13) and the abandoned-tab bound both stay exactly where they were.
for _ph in ("human_action", "awaiting_code", "login", "starting", "", None, "anything_else"):
    ok(ll.inactivity_limit(_ph, True) <= ll.max_inactivity_limit(),
       "watched '%s' asks for no more than the longest budget there is" % _ph)
eq(ll.max_inactivity_limit(), float(ll._IDLE_CLOSE_SECONDS),
   "the ceiling is unmoved by §COMMCALC-29")

# ONE reader of "is a human watching", and it is PURE.
_vp = _func("viewer_present")
ok(_vp != "", "viewer_present is found")
ok("try" in _vp and "except" in _vp, "viewer_present never raises")
ok("_VIEWER_PRESENT_SECONDS" in _vp, "it dereferences the one declared presence window")
ok(not re.search(r"\b\d{2,}\b", _vp), "…and spells no second copy of that window", _vp)
_readers = [m for m in re.finditer(r"_last_seen_at", SRC)]
ok(len(_readers) == 3,
   "_last_seen_at is written once in __init__, set once in _note_viewer and read once in "
   "viewer_present — no fourth site decides this for itself", len(_readers))

s_j = session()
ok(s_j.viewer_present() is False,
   "a session nobody has polled reads as NOT watched — the failure mode is the old budget")
s_j._note_viewer()
ok(s_j.viewer_present() is True, "a sighting makes it watched")
ok(s_j.viewer_present(now=time.time() + ll._VIEWER_PRESENT_SECONDS + 1) is False,
   "and the sighting EXPIRES, so a closed tab stops holding the session open")
ok(0 < ll._VIEWER_PRESENT_SECONDS <= 120,
   "the presence window is short, so an abandoned tab is noticed promptly",
   ll._VIEWER_PRESENT_SECONDS)

# WATCHING IS NOT ACTING. If a sighting also touched the activity clock, an abandoned tab that kept
# polling would hold a browser open forever — the regression this design must not cause.
_nv = _func("_note_viewer")
ok(_nv != "", "_note_viewer is found")
ok("_last_activity" not in _nv,
   "a sighting does NOT touch the activity clock — watching is not acting")
_before = s_j._last_activity
s_j._note_viewer()
eq(s_j._last_activity, _before, "…proved on a real session, not just read off the source")

# THE LOCK: both methods that answer about a session record the sighting. Either one forgetting
# re-creates the defect for the surface that polls it.
for _m in ("state", "frame_since"):
    ok("_note_viewer" in _func(_m),
       "%s records the sighting, so presence is recorded wherever the question is answered" % _m)

# The loop dereferences the one reader rather than reading the flag itself.
_loop = _func("_preauth_loop")
ok("inactivity_limit" in _loop and "viewer_present" in _loop,
   "the command loop asks both one homes rather than deciding for itself")
ok("_last_seen_at" not in _loop, "…and never reads the raw flag")

# Armed controls: the pre-change shapes must read as broken.
ok("viewer_present" not in "limit = inactivity_limit(ph)",
   "ARMED: the pre-change loop shape (budget with no presence) is RED")
eq(ll.inactivity_limit("human_action", True) == float(8 * 60), False,
   "ARMED: a watched human check answering 8 minutes would be the un-fixed behaviour")
ok("_last_activity" in "def _note_viewer(self):\n    self._last_activity = time.time()",
   "ARMED: a sighting that touched the activity clock is caught")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — a live login could stall with no limit, or a human window was shortened. "
          "Budgets live in PRELOOP_PROGRESS_LIMIT_S / PRELOOP_PROGRESS_DEFAULT_S (read by "
          "preloop_limit) and _IDLE_CLOSE_SECONDS / _HUMAN_ACTION_IDLE_SECONDS (read by "
          "inactivity_limit) — one home each, and no caller keeps its own copy.")
    sys.exit(1)
print("OK — no live-login phase can stall forever, and no operator window was shortened.")
sys.exit(0)
