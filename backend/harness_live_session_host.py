"""Proof harness: A LIVE SESSION IS READ WHERE IT LIVES (index §COMMCALC-22).

FOUND 2026-10-20, owner: *"ups portla live login does not work i hvae tried again today"*. He was
right, and the row proved he had been right for five days. His 17:47 attempt reached the real UPS
Franchise Reporting System sign-in page, filled his username and password, parked on Sign In — the
server-side `login_shot` shows exactly that — and closed at 17:55 for OPERATOR INACTIVITY, having
received not one click in eight minutes. He never saw the page he was supposed to click.

THE CAUSE. `_SESSIONS` is a dict in ONE process's memory. On this deploy (`SERVICE_ROLE=api` +
`BROWSER_SERVICE_URL`, §41) `/live-login/start` carried `require_browser_service()`, so it proxied to
the sweeps worker and the session was created THERE. `/live-login/state` and `/live-login/frame` did
not carry it, so they answered from the API host, where the registry is empty — `{"phase": "idle",
"message": "No live session running…", "shot": None}`. His hands were pointed at the worker and his
eyes at the API host.

THE CLASS, which is why this is not a one-line patch: the gate was asking **"does this endpoint
launch a browser?"**. `/frame` launches nothing — it reads a dict — so a reviewer adding it left the
gate off, correctly by that question. The question that matters is **"does this endpoint touch
`_SESSIONS`?"**, and if it does it must run in the process that HAS them. Three endpoints that
obviously drive a browser were gated; six that read or steer the session were not, and the read half
is the entire operator-facing surface. So the requirement is RENAMED at its one home
(`live_login.require_session_host`), because the old spelling is what misled the eye.

THE SIBLINGS, checked here rather than left to drift:
  · `supply/router` `/orders/{po_id}` reports vendor-session liveness as one field of a whole page.
    Gating it would make an order unreadable whenever the worker is down, so it is NOT gated — it
    dereferences `sessions_live_here()` and reports `observable`, never presenting this process's
    blindness as "no session is open".
  · `commcalc/router`'s VidaPay pull reuses a live session when one happens to be in THIS worker's
    memory and falls back to the cold restore otherwise. That is a legitimate best-effort read of a
    per-process registry — it degrades, it does not lie — and is allowlisted BY NAME below.

WHAT THIS PROVES
  §A  one home for "can this process hold live sessions", and the gate dereferences the role module
      rather than keeping a second copy of it.
  §B  `sessions_live_here` is pure and never raises — an unintrospectable role reads as True, which
      keeps the single-service answer rather than declaring a working deploy blind.
  §C  THE LOCK: every endpoint that touches the session registry calls the gate, with the two
      exceptions named and justified above — nothing else may opt out.
  §D  the gate runs EARLY, before the request does DB work it would only throw away.
  §E  the supply sibling reports liveness only when it is observable.
  §F  controls, including the exact pre-change shape going RED.

No DB, no network, no browser. Run:  cd backend && python3 harness_live_session_host.py
"""
import importlib.util
import io
import os
import re
import sys

PASS = FAIL = 0
LL_PATH = os.path.join("app", "modules", "commcalc", "live_login.py")
RT_PATH = os.path.join("app", "modules", "commcalc", "router.py")
SUP_PATH = os.path.join("app", "modules", "supply", "router.py")
MAIN_PATH = os.path.join("app", "main.py")
LL_SRC = io.open(LL_PATH, encoding="utf-8").read()
RT_SRC = io.open(RT_PATH, encoding="utf-8").read()
SUP_SRC = io.open(SUP_PATH, encoding="utf-8").read()

# The ONLY endpoints allowed to touch the registry without the gate, each with the reason it is safe.
# A new name may be added here only with a reason of the same kind: "reads liveness as one field of a
# bigger answer AND dereferences sessions_live_here", or "best-effort reuse with a real fallback".
GATE_EXEMPT = {
    "/orders/{po_id}": "whole-PO read; dereferences sessions_live_here and reports `observable`",
}


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s%s" % (msg, ("\n      %s" % detail) if detail else ""))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def endpoints(src):
    """Every @router endpoint in `src` as (verb, path, body). Body runs to the next decorator."""
    pat = re.compile(r'@router\.(get|post|put|patch|delete)\("([^"]+)"\)(.*?)(?=@router\.|\Z)', re.S)
    return [(m.group(1), m.group(2), m.group(3)) for m in pat.finditer(src)]


def touches_registry(body):
    """True when this handler asks the per-process session registry a question."""
    return bool(re.search(r'\b(live_login|_ll)\.(get_session|start_session)\s*\(', body))


ll = _load("ll_session_host", LL_PATH)

print("\n§A  ONE HOME for where the sessions live")
ok(hasattr(ll, "sessions_live_here") and hasattr(ll, "require_session_host"),
   "live_login owns both halves: sessions_live_here() (the fact) and require_session_host() (the gate)")
ok("def require_session_host" in LL_SRC and "require_browser_service()" in LL_SRC,
   "the gate DEREFERENCES the role module rather than re-deciding the role itself")
# The gate must not grow its own copy of the role logic — that is the divergence this forbids.
gate_body = LL_SRC.split("def require_session_host", 1)[1].split("\ndef ", 1)[0]
ok("SERVICE_ROLE" not in gate_body and "environ" not in gate_body,
   "and it reads no environment of its own — one reader of the role, not two")
ok(LL_SRC.count("def sessions_live_here") == 1 and LL_SRC.count("def require_session_host") == 1,
   "each is defined exactly once")

print("\n§B  the fact is PURE and never raises")
ok(ll.sessions_live_here() is True,
   "path-loaded (no app.core on the path) it reads True — the single-service answer, never 'blind'")


class _Boom:
    def __call__(self):
        raise RuntimeError("role exploded")


_saved = ll.browser_allowed
try:
    ll.browser_allowed = _Boom()
    ok(ll.sessions_live_here() is True,
       "a role that RAISES still answers True rather than propagating — reading a working deploy as "
       "blind is the one regression this must not cause")
    ll.browser_allowed = lambda: False
    ok(ll.sessions_live_here() is False, "a service that may not run browsers holds no sessions")
    ll.browser_allowed = lambda: True
    ok(ll.sessions_live_here() is True, "a service that may run browsers does")
finally:
    ll.browser_allowed = _saved

print("\n§C  THE LOCK — every endpoint that touches the registry runs where the registry is")
checked = 0
for src, label in ((RT_SRC, "commcalc"), (SUP_SRC, "supply")):
    for verb, path, body in endpoints(src):
        if not touches_registry(body):
            continue
        checked += 1
        if path in GATE_EXEMPT:
            ok("sessions_live_here()" in body,
               "%s %s %s is exempt and EARNS it: %s" % (label, verb.upper(), path, GATE_EXEMPT[path]))
            continue
        ok("require_session_host()" in body,
           "%s %s %s calls the gate" % (label, verb.upper(), path),
           "this endpoint reads live_login's per-process registry. On a split deploy it runs on the "
           "API host, where that registry is EMPTY, and will answer 'no live session running' about "
           "a session that is alive on the worker — which is exactly how the Team Portal live login "
           "was dead for five days. Add live_login.require_session_host() as its first statement.")
ok(checked >= 9, "and the scan actually found the endpoint family (%d handlers)" % checked)
# The vidapay pull is deliberately NOT an endpoint-level exemption: it is an inner best-effort read.
ok("Falls back to the cold restore" in RT_SRC,
   "the allowlisted inner reader (VidaPay pull reuse) still documents its real fallback")

print("\n§D  the gate runs EARLY")
for verb, path, body in endpoints(RT_SRC):
    if "live-login" not in path or not touches_registry(body):
        continue
    if "require_session_host()" not in body:
        continue            # already reported RED in §C
    i_gate = body.index("require_session_host()")
    i_org = body.find("require_org(")
    i_db = body.find("sb()")
    ok(i_org == -1 or i_gate < i_org,
       "%s is gated before require_org" % path)
    if i_db != -1:
        ok(i_gate < i_db, "%s is gated before it opens a DB client" % path)

print("\n§E  the supply sibling reports liveness only when it can SEE it")
po = [b for v, p, b in endpoints(SUP_SRC) if p == "/orders/{po_id}"]
ok(len(po) == 1, "the PO read endpoint is there to check")
if po:
    ok('"observable"' in po[0],
       "it reports `observable` so a blind process is never read as 'no session is open'")
    ok("if observable else None" in po[0],
       "and `for_this_order` is None — not False — when this process cannot observe the session")
for verb, path, body in endpoints(SUP_SRC):
    if "_live_pull(" in body:
        ok("require_browser_service()" in body or "require_session_host()" in body,
           "supply %s drives the session and is gated" % path)

print("\n§F  controls — the pre-change shape cannot come back")
ok("live_login.require_session_host()" in RT_SRC, "the gate is actually wired in commcalc/router")
state_body = [b for v, p, b in endpoints(RT_SRC) if p.endswith("live-login/state")]
frame_body = [b for v, p, b in endpoints(RT_SRC) if p.endswith("live-login/frame")]
ok(state_body and "require_session_host()" in state_body[0],
   "ARMED: /live-login/state — the operator's status line — can never again answer from the API host")
ok(frame_body and "require_session_host()" in frame_body[0],
   "ARMED: /live-login/frame — the operator's EYES — likewise")
# The exact pre-change text of the two endpoints that blinded him, armed so a revert reads as RED.
ok('"""The live session\'s phase + human message + the LATEST screenshot (data-uri JPEG). Polled ~1s by\n'
   '    the UI. Screenshot is served ONLY here (never in the polled data-sources list)."""\n'
   '    require_org(org_id)' not in RT_SRC,
   "ARMED: the exact pre-change /state body (require_org first, no gate) is gone")
ok("_browser_client()" in io.open(MAIN_PATH, encoding="utf-8").read(),
   "and the forwarder pools its connection, so a ~3/s frame poll is not a handshake per frame")

print("\n§G  THE GATE HAS ONE NAME-LIST, AND EVERY LOCK READS IT")
# WHY THIS SECTION EXISTS. The rename in this change broke three CI locks, one after another, because
# each carried its own literal "require_browser_service". Two failed a correctly gated endpoint — loud
# and harmless. The third went BLIND: harness_one_domain_lock's AST scan stopped recognising the gate,
# eleven endpoints silently left the set whose DIRECT_ROUTES coverage it asserts, and only a
# non-vacuity bound (len >= 10) turned it red. A scan that cannot see its subject does not fail; its
# assertion ceases to exist. So the vocabulary lives beside the gate and the locks dereference it.
ROLE_SRC = io.open(os.path.join("app", "core", "service_role.py"), encoding="utf-8").read()
ok("GATE_FUNCTION_NAMES" in ROLE_SRC,
   "the role module declares the gate's names — one home for what the gate is CALLED")
_names = [l for l in ROLE_SRC.splitlines() if l.startswith("GATE_FUNCTION_NAMES")]
ok(len(_names) == 1 and "require_browser_service" in _names[0] and "require_session_host" in _names[0],
   "and it names both spellings exactly once")
# Every lock that SCANS source for the gate must read that list rather than spell a name itself.
SCANNERS = {"harness_browser_sweep_kinds.py": "walks helper calls to a browser launch",
            "harness_one_domain_lock.py": "checks each gated endpoint is DIRECT, not proxied"}
for fname in sorted(SCANNERS):
    src = io.open(fname, encoding="utf-8").read()
    ok("GATE_FUNCTION_NAMES" in src,
       "%s dereferences the name-list (%s)" % (fname, SCANNERS[fname]),
       "this lock scans for the gate. Spelling a gate name as a literal here is what made the rename "
       "break three locks at once and sent one of them blind. Read "
       "app/core/service_role.GATE_FUNCTION_NAMES instead.")
    # and it must not keep a second copy of the vocabulary beside the dereference
    body = src.replace("GATE_FUNCTION_NAMES", "")
    ok(body.count('"require_browser_service"') + body.count("'require_browser_service'") == 0,
       "%s keeps no second copy of a gate NAME beside it" % fname)

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — an endpoint can answer about a live session from a process that holds none. The one "
          "home is live_login.sessions_live_here (the fact) + require_session_host (the gate); every "
          "endpoint that touches _SESSIONS dereferences it, and the only exemptions are the named "
          "readers that report their own blindness instead of hiding it.")
    sys.exit(1)
print("OK — the session is read where it lives, and a process that holds none says so.")
