#!/usr/bin/env python3
"""★ THE LOCK on "what paths does this app serve" having ONE home. DB-free, network-free.

WHY (access probe run 330, 2026-11-26, index §CORE-52). Ten files answered that question with their
own private walk over `app.routes`. Nine got it right. The tenth was the SECURITY control — the
sweep built on 2026-11-23 so that a declared-public link (the W-9 form a wholesale customer is
emailed) can never again be locked out by a router-level gate — and it was measuring FIVE paths of
an app serving 1,558, with its own `checked >= 1` tripwire satisfied by `/health`.

A fact with nine right copies and one wrong one is the patchwork CLAUDE.md forbids, and the wrong
copy is always the one nobody re-reads. So:

  §A  the home exists and answers the question
  §B  the served path is the FULL path a caller can address (trap 2, the silent one)
  §C  no new private copy of the walk — a ratchet, so the count can fall and never rise
  §D  the security control dereferences the home and keeps its non-vacuity floor
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PASS = FAIL = 0


def ok(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  [{detail}]" if detail else ""))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  the home exists and answers the question")
# ══════════════════════════════════════════════════════════════════════════════════════════════
from app.core import route_table                                        # noqa: E402

# The ruling has ONE home and the locks READ it (index 19.18): which file is allowed to answer
# "what paths does this app serve" is declared in the module graph, never restated here.
from app.modules.core import module_graph as MG                         # noqa: E402

_HOMES = MG.homes_for("served_route_table")
ok("A0 the module graph names this fact's home, and this lock dereferences it",
   _HOMES == ("app/core/route_table.py",), _HOMES)
src = read(_HOMES[0])
ok("A1 route_surface / served_paths / serves all exist",
   all(callable(getattr(route_table, n, None)) for n in ("route_surface", "served_paths", "serves")))
ok("A2 the home asks the framework's PUBLIC contract, not its private structure",
   ".openapi()" in src and "original_router" not in src.split('"""', 2)[-1])


class _FakeApp:
    def __init__(self, paths):
        self._paths = paths

    def openapi(self):
        return {"paths": self._paths}


fake = _FakeApp({"/api/v1/a/b": {"get": {}, "post": {}}, "/api/v1/c": {"GET": {}}})
ok("A3 every (path, METHOD) pair is returned, method upper-cased",
   route_table.route_surface(fake) == {("/api/v1/a/b", "GET"), ("/api/v1/a/b", "POST"),
                                       ("/api/v1/c", "GET")})
ok("A4 served_paths filters by method", route_table.served_paths(fake, "post") == ["/api/v1/a/b"])
ok("A5 serves() is exact, templates matched literally",
   route_table.serves(fake, "/api/v1/a/b", "post") and not route_table.serves(fake, "/api/v1/a", "GET"))
ok("A6 an app with no paths is empty, never an exception",
   route_table.route_surface(_FakeApp({})) == set() and route_table.route_surface(_FakeApp(None)) == set())

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  the served path is the FULL path a caller can address  ★ the silent trap ★")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# This is the half that survives the known `_IncludedRouter` fix: flattening through
# `.original_router` recovers the leaf objects, but each carries its path RELATIVE to its mount.
# Every declaration here is keyed by the SERVED path, and they are DEFAULT-DENY, so a relative path
# gets a confident `False` — "not declared" — and the caller skips the route and reports clean.
from fastapi import APIRouter, FastAPI                                  # noqa: E402

inner = APIRouter()


@inner.get("/widget")
def _widget():
    return {}


_app = FastAPI()
_app.include_router(inner, prefix="/api/v1/demo")
surface = {p for p, _m in route_table.route_surface(_app)}
ok("B1 the home returns the MOUNTED path", "/api/v1/demo/widget" in surface, sorted(surface))
ok("B2 and never the prefix-stripped one", "/widget" not in surface)

# The negative control: the walk this replaces really does lose the prefix, so B1/B2 are measuring
# something. If a future FastAPI starts flattening again this control reports it rather than
# silently becoming vacuous.
def _flatten(routes):
    out = []
    for r in routes:
        innr = getattr(r, "original_router", None)
        if innr is not None:
            out.extend(_flatten(innr.routes))
            continue
        sub = getattr(r, "routes", None)
        out.extend(_flatten(sub)) if sub else out.append(r)
    return out


_leaf = {getattr(r, "path", None) for r in _flatten(_app.routes)}
ok("B3 NEGATIVE CONTROL: the private walk really is prefix-stripped on this FastAPI",
   "/widget" in _leaf and "/api/v1/demo/widget" not in _leaf, sorted(x for x in _leaf if x))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  no NEW private copy of the walk — a ratchet")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# A file may carry a private walk ONLY if it is named here with its reason. The set may shrink; a
# file that is not listed fails the build. Scratchpad smokes are excluded: they are not controls.
EXCUSED = {
    # The OTHER home, and a different question: production logging needs the OBJECT-GRAPH walk
    # (it must work on a bare router, must never raise, and runs on the hot path where building the
    # whole OpenAPI schema is not acceptable). It already resolves the mount prefix correctly.
    "app/core/path_redact.py": "the object-graph home — prefix-resolving, never raises, hot path",
    "harness_path_redact.py": "exercises that walk against real FastAPI objects; the walk IS its subject",
    "harness_db_resilience.py": "counts leaf route OBJECTS and asserts no _IncludedRouter is empty",
    "harness_pos_onboarding.py": "reads path_regex off leaves of a mini-app it assembles itself",
    "harness_fix_pipeline.py": "asserts the 7 fix-pipeline routes resolve on a locally built app",
    "harness_route_table.py": "this lock's own negative control, §B3",
}
PATTERN = re.compile(r"original_router|\.openapi\(\)\s*\[|openapi\(\)\[")
offenders = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames
                   if d not in ("scratchpad", ".git", "__pycache__", "node_modules", ".pytest_cache")]
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
        if rel in _HOMES or rel in EXCUSED:
            continue
        try:
            body = read(rel)
        except Exception:
            continue
        hits = [ln for ln in body.splitlines()
                if PATTERN.search(ln) and not ln.lstrip().startswith("#")]
        if hits:
            offenders.append(f"{rel}: {hits[0].strip()[:70]}")
ok("C1 no file outside the two homes carries a private route-table walk",
   not offenders, "; ".join(offenders[:6]))
ok("C2 the ratchet is non-vacuous (the excuse list names files that really exist)",
   all(os.path.exists(os.path.join(ROOT, f)) for f in EXCUSED), sorted(EXCUSED))
ok("C3 the excuse list has not grown past the 6 recorded on 2026-11-26", len(EXCUSED) <= 6, len(EXCUSED))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  the security control dereferences the home and keeps its floor")
# ══════════════════════════════════════════════════════════════════════════════════════════════
CONTROL = "tests/integration/test_public_path_reaches_its_handler.py"
ctl = read(CONTROL)
ok("D1 the lockout sweep reads the one home", "route_table.route_surface(" in ctl)
ok("D2 it asks the DECLARATION which public paths verify their own caller, never a list of its own",
   "public_path_verifies_its_own_caller" in ctl and "public_surface_allows" in ctl)
ok("D3 ★ the non-vacuity floor is present ★ — its absence is the whole defect of 2026-11-23",
   re.search(r"len\(paths\)\s*>=\s*10", ctl) is not None)
ok("D4 the floor is not the `checked >= 1` ASSERTION that `/health` alone satisfied",
   not re.search(r"assert\s+checked\s*>=\s*1", ctl))
ok("D5 the W-9 link must be reached BY THE DERIVATION, not by a hand-kept case",
   "/api/v1/pos/public/w9/" in ctl and "_declared_public_gets" in ctl)
ok("D6 the sweep spans several modules, so a one-router derivation cannot pass the count",
   ">= 4" in ctl)

# The two paths declared self-verifying on 2026-11-26 must stay declared THERE, not here.
from app.core import tenant_middleware as tm                            # noqa: E402
for p in ("/api/v1/remediation/whatsapp-webhook", "/api/v1/vision/edge/config"):
    ok(f"D7 {p} is excused by the declaration",
       tm.public_path_verifies_its_own_caller(p))
ok("D8 a capability-token public path is NOT excused (a 401 there is still a lockout)",
   not tm.public_path_verifies_its_own_caller("/api/v1/pos/public/w9/abc")
   and not tm.public_path_verifies_its_own_caller("/api/v1/referral/redeem/abc"))

print(f"\n{'='*78}\n  route table one home: {PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
