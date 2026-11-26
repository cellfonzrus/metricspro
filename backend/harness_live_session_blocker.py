"""Proof harness: A LIVE SESSION THAT CANNOT ACT SAYS WHICH REASON APPLIES (index §COMMCALC-11.8).

DB-free. Builds real `LiveLoginSession` objects — never started, so no browser, no thread, no
portal — and drives them through the phase changes an operator actually produces.

OWNER, 2026-11-26 03:29 UTC, minutes after a sign-in the row recorded as `authenticated` with the
saved session good for five more hours: *"finally logged in again but when i hit read report it
shows cannot read as not loggedin"*. One minute later: *"seems like the live login is logging in and
out of the portal, i don't want to get shut down."*

He was told he was not signed in AT THE MOMENT HE WAS. The only move that sentence leaves an
operator is to sign in again — repeatedly, against a franchise portal, in the middle of the night.
A wrong reason does not merely fail to help; it aims the operator at the one action that can get the
account locked. That is why this is a lock and not a wording tidy-up.

THE DEFECT. `can_calibrate()` and `can_pull()` each folded THREE facts into one boolean — passed the
portal's sign-in, worker thread still alive, function configured — and the two routers, holding only
the boolean, each spelled a sentence that GUESSED which fact it was. Both guessed "not signed in".
The true reason was the third. `signed_in()` reads the CURRENT phase, so a session that authenticated
and was later cancelled by the post-login inactivity timeout reads exactly like one that never got in.

THE CLASS, named twice already in this repo: an absence needs the RIGHT reason, not just a reason
(§COMMCALC-44); and a fact every caller needs has ONE home that callers dereference, with the
registry WIRED and not merely written (§19.18). So the REASON is the fact with a home, the sentence
is keyed by (action, reason), and the two booleans are DERIVED from the reason — a caller that can
reach the verdict can always reach the reason.

WHAT THIS PROVES
  §A  the four reasons are distinguished, on real session objects.
  §B  the owner's own case, reproduced end to end: authenticated, then cancelled, thread never
      started → `window_closed`, never `not_signed_in`.
  §C  every (action, reason) has its own sentence, and the closed-window one does not tell an
      operator he is signed out.
  §D  the booleans are DERIVED, and `act_blocker` is the only place weighing all three facts.
  §E  both operator-facing routers dereference the one home and spell no reason of their own.
  §F  ARMED: the pre-change rule, reproduced, gets the owner's case wrong.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import live_login as ll          # noqa: E402
from harnesslib import py_code_only                        # noqa: E402

PASS = FAIL = 0


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % label)
    else:
        FAIL += 1
        print("  FAIL %s" % label)


def session(calibrate=True, pull=True):
    """A real session object, never started."""
    return ll.LiveLoginSession("sid", "org", {"username": "u", "password": "p"},
                               pull_fn=(lambda page: {}) if pull else None,
                               calibrate_fn=(lambda page: {}) if calibrate else None)


ACTIONS = ("calibrate", "pull")
REASONS = ("no_session", "not_signed_in", "window_closed", "not_configured")

print("\n§A  the four reasons, on real sessions")
ok(ll.act_blocker(None, "calibrate") == "no_session", "no session at all is its own reason")
s = session()
s._set(phase="awaiting_code")
ok(ll.act_blocker(s, "calibrate") == "not_signed_in", "a session that never signed in says so")
s = session()
s._set(phase="authenticated")
ok(ll.act_blocker(s, "calibrate") == "window_closed",
   "signed in with no worker thread is a closed window, not a failed sign-in")
s = session(calibrate=False)
s._set(phase="authenticated")
s.worker_alive = lambda: True
ok(ll.act_blocker(s, "calibrate") == "not_configured", "no capture configured is its own reason")
ok(ll.act_blocker(s, "pull") == "", "…and it does not block the other action")
s = session()
s._set(phase="pulling")
s.worker_alive = lambda: True
ok(ll.act_blocker(s, "calibrate") == "",
   "the auto-pull phase still counts as signed in — an operator pressing Read this page during it "
   "is not refused")
try:
    ll.act_blocker(session(), "sign_the_lease")
    ok(False, "an unknown action is refused")
except ValueError:
    ok(True, "an unknown action is refused rather than silently allowed")

print("\n§B  the owner's case, reproduced end to end")
his = session()
his._set(phase="authenticated")
his._set(phase="cancelled", message="Live session closed after inactivity (post-login).")
ok(his.signed_in() is False, "the phase alone still reads 'not signed in'…")
ok(his.ever_signed_in() is True, "…but the session remembers that it did sign in")
ok(ll.act_blocker(his, "calibrate") == "window_closed", "so the reason is the closed window")
ok(ll.act_blocker(his, "pull") == "window_closed", "the same for a pull")
ok(his.can_calibrate() is False and his.can_pull() is False, "the verdicts are unchanged")

print("\n§C  one sentence per (action, reason)")
for action in ACTIONS:
    notes = [ll.cannot_act_note(action, r) for r in REASONS]
    ok(all(n and len(n) > 20 for n in notes), "%s: every reason has a sentence" % action)
    ok(len(set(notes)) == 4, "%s: four reasons, four different sentences" % action)
    closed = ll.cannot_act_note(action, "window_closed").lower()
    ok("not signed in" not in closed and "saved" in closed,
       "%s: the closed-window sentence does not tell him he is signed out" % action)
ok(ll.cannot_act_note("calibrate", "no_such_reason") == ""
   and ll.cannot_act_note("no_such_action", "window_closed") == ""
   and ll.cannot_act_note("calibrate", "") == "",
   "an unknown (action, reason) is empty rather than a guess dressed as an explanation")

print("\n§D  the booleans are derived, and the decision has one home")
import inspect  # noqa: E402
for name in ("can_calibrate", "can_pull"):
    src = inspect.getsource(getattr(ll.LiveLoginSession, name))
    ok("act_blocker" in src and "is_alive()" not in src,
       "%s dereferences act_blocker instead of re-weighing the facts" % name)
ok('"authenticated"' not in inspect.getsource(ll.act_blocker)
   and "signed_in()" in inspect.getsource(ll.act_blocker),
   "act_blocker asks signed_in() rather than keeping its own phase list")


def _weighing_all_three(path):
    tree = ast.parse(open(path).read())
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        used = ({n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
                | {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)})
        if (used & {"signed_in", "ever_signed_in", "_SIGNED_IN_PHASES"}) \
           and (used & {"worker_alive", "is_alive"}) \
           and (used & {"calibrate_fn", "pull_fn", "_ACT_FN"}):
            out.append(fn.name)
    return out


ok(_weighing_all_three(ll.__file__) == ["act_blocker"],
   "act_blocker is the ONLY function weighing all three facts (the lock)")

print("\n§E  both routers dereference it and word nothing themselves")
from app.modules.commcalc import router as _cr          # noqa: E402
from app.modules.supply import router as _sr            # noqa: E402
for who, mod in (("commcalc", _cr), ("supply", _sr)):
    code = py_code_only(open(mod.__file__).read())
    ok("act_blocker" in code and "cannot_act_note" in code,
       "%s router reads the one home" % who)
    ok("is not signed in yet" not in code.lower(),
       "%s router no longer spells the sentence that lied to him" % who)

print("\n§F  ARMED: the pre-change rule, reproduced")


def _old_rule(sess):
    """What `can_calibrate` used to compute, and what the router then had to guess a reason from."""
    return sess.signed_in() and sess.worker_alive() and sess.calibrate_fn is not None


ok(_old_rule(his) is False, "ARMED: the old boolean is False for the owner's session…")
ok(his.signed_in() is False,
   "…and the only fact it left the router was a phase that reads 'not signed in' — so the router "
   "told him to sign in again. That is the defect, reproduced.")
ok(ll.act_blocker(his, "calibrate") != "not_signed_in", "the fixed rule does not say that")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — a live session could not say WHICH reason stopped it, or a caller worded a reason "
          "of its own. The one home is live_login.act_blocker (the reason) and "
          "live_login.cannot_act_note (the sentence, keyed by action and reason); can_calibrate and "
          "can_pull are DERIVED from the first, and no router keeps a sentence.")
    sys.exit(1)
print("OK — four reasons, four sentences, one home, and a closed window is never reported as a "
      "failed sign-in.")
sys.exit(0)
