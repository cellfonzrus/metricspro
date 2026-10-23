"""Proof harness: A FINISHED LIVE LOGIN LEAVES AN HONEST STATUS (index §COMMCALC-13).

FOUND 2026-10-15 while reporting the live row back to the owner. `commcalc/live_login_start` stamps
`auth_status='authenticating'` / "Live login in progress…" on the data_source row before the worker
starts, which IS true while the worker runs. §COMMCALC-10 taught a session that DIES to write
`'error'`. But a session that merely ENDS — the inactivity timeout, or the operator pressing Close —
wrote only `auth_message` and left the status in flight. The chip on `commcalc/email-imports` maps
`authenticating` to "⏳ Logging in…", so the owner's screen claimed a login was in progress an hour
after the browser had closed, on a row whose own message already said "Live session closed after
inactivity."

THE CLASS. *A status is a record of what IS.* §COMMCALC-10 fixed the dying session; the ending one
told the same lie, and the ONE place that writes a terminal status wrote it for only one of its two
terminal phases. Same shape as §19.41 (`empty_ok` recording a broken filter as a successful zero-row
run) and §COMMCALC-10 itself.

THE SIBLING, fixed here too rather than excused. `terminal_status` can only speak for a session that
reaches its own end. A worker KILLED mid-login never runs `_persist_diag` at all — and every merge to
main redeploys both hosts, so that is routine here — leaving the row claiming a login is in flight
with no process anywhere running it. Nothing ON the row distinguishes that from a login genuinely
still going, so the only honest discriminator is AGE, and the budget is DEREFERENCED from the two
homes that already answer "how long may this take" rather than being a new number.

WHAT THIS PROVES
  §A  one home for "what status does a terminal session leave", and it answers for BOTH terminal
      phases — never only 'error'.
  §B  the in-flight status is never restored, from any direction.
  §C  a session that did not change whether we are signed in leaves the row as it found it.
  §D  the write actually happens: `_persist_diag` sends `auth_status` on every terminal phase, and
      stops claiming anything is pending.
  §E  the pre-login status is captured from the row (which is read BEFORE the stamp), not guessed.
  §F  the read side: an in-flight claim older than the budget is presented as cleared, a fresh one is
      NOT, and the budget is one expression over the existing homes.
  §G  the surface dereferences it — the one place a data_source row reaches a page.
  §H  controls, including the pre-change shape going RED.

No DB, no network, no browser. Run:  cd backend && python3 harness_terminal_status.py
"""
import importlib.util
import io
import os
import re
import sys
import tokenize
from datetime import datetime, timedelta, timezone

PASS = FAIL = 0
LL_PATH = os.path.join("app", "modules", "commcalc", "live_login.py")
RT_PATH = os.path.join("app", "modules", "commcalc", "router.py")
LL_SRC = io.open(LL_PATH, encoding="utf-8").read()
RT_SRC = io.open(RT_PATH, encoding="utf-8").read()


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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ll = _load("ll_terminal_status", LL_PATH)


def _body(src, name, decl="def "):
    """The source of one function/method, COMMENT- and STRING-stripped.

    Stripping both is deliberate and has bitten this repo four times: a comment (or a docstring)
    quoting the thing a check forbids reads as the violation itself. `_body_raw` is the intact form,
    for the two checks that must see a string literal."""
    raw = _body_raw(src, name, decl)
    lines = raw.split("\n")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(raw).readline):
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            (r1, c1), (r2, c2) = tok.start, tok.end
            for r in range(r1, r2 + 1):            # blank IN PLACE, so adjacency survives:
                ln = lines[r - 1]                  # joining the surviving tokens with a separator
                a = c1 if r == r1 else 0           # would split `self._status_before` and
                b = c2 if r == r2 else len(ln)     # `("error", "cancelled")` and read as absent.
                lines[r - 1] = ln[:a] + (" " * (b - a)) + ln[b:]
    except Exception:
        return raw
    return "\n".join(lines)


def _body_raw(src, name, decl="def "):
    i = src.index(decl + name)
    indent = len(src[:i].split("\n")[-1])
    lines = src[i:].split("\n")
    body = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent and not ln.lstrip().startswith(("#", ")")):
            break
        body.append(ln)
    return "\n".join(body)


NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


print("\n— A. one home, and it answers for BOTH terminal phases —")
ok(callable(getattr(ll, "terminal_status", None)),
   "live_login.terminal_status is the one home for a terminal session's status")
eq(ll.terminal_status("error", "authenticating"), "error",
   "a session that DIED still owns the row with 'error' (§COMMCALC-10 unchanged)")
eq(ll.terminal_status("error", None), "error", "…whatever the row said before it")
eq(ll.terminal_status("cancelled", "authenticating"), None,
   "a session that ENDED does not leave the row claiming a login is in flight")
# THE DEFECT, REPRODUCED AS A CHECK: `cancelled` is the phase the inactivity timeout and the
# operator's Close both set, and it was the one terminal phase that wrote no status at all.
pd = _body(LL_SRC, "_persist_diag")
ok('phase == "error"' not in pd or "auth_status" in pd.split('phase == "error"')[0],
   "ARMED: _persist_diag no longer writes a status for 'error' ALONE — that was the defect",
   pd[:200])
# the RAW body here: this check must see the string literals themselves, which `_body` blanks.
terminals = re.findall(r'phase in \(([^)]*)\)', _body_raw(LL_SRC, "_persist_diag"))
ok(terminals and '"cancelled"' in terminals[0] and '"error"' in terminals[0],
   "both terminal phases reach the write", terminals)

print("\n— B. the in-flight status is never handed back —")
ok(getattr(ll, "_STATUS_IN_FLIGHT", None) == "authenticating",
   "the in-flight status is DECLARED, not spelled at the call site")
eq(ll.terminal_status("cancelled", ll._STATUS_IN_FLIGHT), None,
   "a row already carrying it (a session that ended before this fix) is cleared, not re-told")
eq(ll.terminal_status("cancelled", "  AUTHENTICATING  "), None,
   "…case and whitespace cannot smuggle it back")
eq(ll.terminal_status("cancelled", ""), None, "an empty status stays empty")
eq(ll.terminal_status("cancelled", None), None, "…as does a missing one")
ts = _body(LL_SRC, "terminal_status")
ok("_STATUS_IN_FLIGHT" in ts, "terminal_status dereferences the declared constant")
ok(ts.count('"authenticating"') == 0, "…and spells the word nowhere itself", ts[:200])

print("\n— C. a session that changed nothing leaves the row as it found it —")
eq(ll.terminal_status("cancelled", "authenticated"), "authenticated",
   "closing the live browser does not un-sign-in a saved session")
eq(ll.terminal_status("cancelled", "needs_2fa"), "needs_2fa", "…nor discard a pending 2FA prompt")
eq(ll.terminal_status("cancelled", "error"), "error", "…nor hide an earlier error")
eq(ll.terminal_status("cancelled", "AUTHENTICATED"), "authenticated",
   "the restored value is normalised, so the chip never sees a second spelling")

print("\n— D. the write happens, and nothing is left pending —")
ok('"auth_status": terminal_status(' in _body_raw(LL_SRC, "_persist_diag"),
   "_persist_diag sends auth_status through the one home")
ok('"pending_started_at": None' in _body_raw(LL_SRC, "_persist_diag"),
   "…and stops claiming an attempt is pending, as _on_authenticated already does")
ok("self._status_before" in pd, "…reading the pre-login status off the session, not the live row")

print("\n— E. the pre-login status is captured, not guessed —")
init = _body_raw(LL_SRC, "__init__")
ok('self._status_before = row.get("auth_status")' in init,
   "__init__ captures the row's status — the row is read BEFORE live_login_start stamps over it")
start_ep = _body_raw(RT_SRC, "live_login_start")
i_read, i_stamp = start_ep.find("_live_source_row"), start_ep.find('"auth_status": "authenticating"')
ok(0 <= i_read < i_stamp,
   "ARMED: the endpoint still reads the row BEFORE stamping it — the capture depends on that order",
   (i_read, i_stamp))
ok("live_login.start_session" in start_ep and start_ep.find("live_login.start_session") > i_stamp,
   "…and the stamp is what the UI polls while the worker runs, so it must stay")

print("\n— F. the read side: an in-flight claim with no process behind it —")
ok(callable(getattr(ll, "in_flight_is_stale", None)), "in_flight_is_stale is the one reader")
ok(ll.in_flight_is_stale("authenticating", ago(hours=1), now=NOW),
   "THE OWNER'S CASE: a claim an hour old with no worker running reads as stale")
ok(not ll.in_flight_is_stale("authenticating", ago(minutes=2), now=NOW),
   "a login that started two minutes ago is NOT stale")
ok(not ll.in_flight_is_stale("authenticating", ago(minutes=20), now=NOW),
   "nor is one an operator has been holding for the full inactivity window")
ok(not ll.in_flight_is_stale("authenticated", ago(days=30), now=NOW),
   "a settled verdict is never stale, however old")
ok(not ll.in_flight_is_stale("error", ago(days=30), now=NOW), "…including an error")
ok(not ll.in_flight_is_stale("authenticating", None, now=NOW),
   "no timestamp ⇒ NOT stale: an unknown shape keeps the old answer rather than clearing a live one")
ok(not ll.in_flight_is_stale("authenticating", "not a date", now=NOW), "…as does an unparseable one")
ok(ll.in_flight_is_stale("authenticating", ago(hours=1).replace("+00:00", ""), now=NOW),
   "a naive timestamp is read as UTC and still answers, rather than raising or reading as fresh")
# the budget is DEREFERENCED, never a second number
budget = _body(LL_SRC, "in_flight_stale_after_s")
# §COMMCALC-30 put a level of indirection in front of the first home: the budget now dereferences
# `max_inactivity_limit()`, which is itself one expression over every inactivity constant. That is
# strictly stronger than naming one constant — a budget added later widens this with it — so the
# CHAIN is what is locked here, end to end, and still no duration is copied.
ok("max_inactivity_limit" in budget and "PRELOOP_PROGRESS_DEFAULT_S" in budget,
   "the budget is one expression over the two homes that already answer 'how long may this take'")
_ceiling = _body(LL_SRC, "max_inactivity_limit")
ok("_IDLE_CLOSE_SECONDS" in _ceiling and "_HUMAN_ACTION_IDLE_SECONDS" in _ceiling,
   "…and that ceiling is itself one expression over the inactivity budgets, not a number")
ok(not re.search(r"\b\d{3,}\b", _ceiling),
   "…with no duration spelled inside it either", _ceiling)
eq(ll.max_inactivity_limit(), float(ll._IDLE_CLOSE_SECONDS),
   "the ceiling is the longest budget there is, and §COMMCALC-30 did not raise it")
ok(not re.search(r"\b\d{3,}\b", budget),
   "…so no third copy of a duration is spelled inside it", budget)
ok(ll.in_flight_stale_after_s() > float(ll._IDLE_CLOSE_SECONDS),
   "and it clears the longest window an operator may legitimately hold")

print("\n— G. the surface dereferences it —")
ok(callable(getattr(ll, "present_auth_status", None)), "present_auth_status is what a surface reads")
eq(ll.present_auth_status({"auth_status": "authenticating", "pending_started_at": ago(hours=1)},
                          now=NOW), None, "a stale claim is presented as cleared")
eq(ll.present_auth_status({"auth_status": "authenticating", "pending_started_at": ago(minutes=1)},
                          now=NOW), "authenticating", "a live one is presented untouched")
eq(ll.present_auth_status({"auth_status": "authenticated"}, now=NOW), "authenticated",
   "and every other status passes through")
ok("present_auth_status" in RT_SRC,
   "the row decorator (the one place a data_source row reaches a page) dereferences it")
_i_present = RT_SRC.find("present_auth_status")
_i_health = RT_SRC.find('row["session_health"]')
ok(0 <= _i_present < _i_health,
   "…BEFORE session_health, so the chip and the health line cannot disagree",
   (_i_present, _i_health))
ok(RT_SRC.count("present_auth_status") == 1, "…exactly once: no second presenter")
ok("in_flight_is_stale" not in RT_SRC and "_STATUS_IN_FLIGHT" not in RT_SRC,
   "the router holds no copy of the rule, only the call")

print("\n— H. controls —")
ok(ll.terminal_status("login", "authenticating") is None,
   "a NON-terminal phase answers None — _persist_diag decides what is terminal, not this")
ok(ll.present_auth_status({}, now=NOW) is None, "an empty row answers None rather than raising")
ok(ll.present_auth_status(None, now=NOW) is None, "…as does no row at all")


class Boom(dict):
    def get(self, *a, **k):
        raise RuntimeError("row exploded")


ok(ll.present_auth_status(Boom(), now=NOW) is None,
   "a row that raises on read answers None rather than breaking the whole list — the fallback path "
   "must not re-read the row that just exploded, which is how the first draft of this crashed")
# the pre-change shape, armed so it can never silently come back
pre = 'if phase == "error":\n                    upd["auth_status"] = "error"'
ok(pre not in LL_SRC,
   "ARMED: the pre-change shape (a status written for 'error' only) is gone and cannot return")
ok('"auth_status": terminal_status(' in LL_SRC,
   "ARMED: and the write is unconditional across the terminal phases")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — a finished live login can leave the row claiming it is still in progress. The one "
          "home is live_login.terminal_status (what it leaves) + in_flight_is_stale / "
          "present_auth_status (a claim with no process behind it). No caller keeps a copy.")
    sys.exit(1)
print("OK — a session that ends leaves an honest status, and an in-flight claim cannot outlive its worker.")
