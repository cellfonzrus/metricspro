#!/usr/bin/env python3
"""LOCK: every router-level auth gate DEREFERENCES the public declaration, and nothing copies it.

DB-free. No network. Reads the source tree and builds the real routers under TestClient.

WHAT IT STOPS COMING BACK (2026-11-23, access probe run 327, index §CORE-48). The `pos` APIRouter
carried `dependencies=[Depends(_require_pos_access)]`. A router-level dependency runs BEFORE the
endpoint body, for every route the router mounts -- including `/api/v1/pos/public/w9/{token}`, the
link a wholesale customer is EMAILED to return their Form W-9, declared public in
`tenant_middleware._PUBLIC_PREFIXES`. Measured on production: `401 {"detail":"sign in to perform
this action"}`, the token never read. Every W-9 return link was dead and no control saw it, because
every control proved the gate REFUSES and none proved it STANDS DOWN.

The three things locked here, each the failure mode that actually happened somewhere in this repo:

  §A  Every router-level `dependencies=[Depends(f)]` names a gate that asks
      `router_gate.stand_down`, or is on EXCUSED with a reason. A gate that cannot ask the
      declaration is the defect, whatever its rule says.
  §B  `router_gate.stand_down` dereferences `tenant_middleware.public_surface_allows` and holds NO
      copy of the allowlist -- no path literal, no prefix tuple. A written-but-unwired registry has
      happened three times here (CLAUDE.md §19.18); so has a second copy that drifted.
  §C  Measured, not asserted: for every route a gated router mounts, the gate stands down exactly
      where the declaration says public and refuses everywhere else. Both directions, because a
      stand-down that over-matches is a hole and one that under-matches is the lockout.

Run: python3 harness_router_gate_dereference.py
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "")

FAILED = []
PASSED = []
APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")

# A router-level gate that is deliberately NOT asked to stand down, with the reason. Empty today:
# every gate asks. An entry here is a decision somebody wrote down, never a silence.
EXCUSED = {}

# ★ THE DECLARED SUBJECT SET (index §CORE-10). Every router-level `APIRouter(dependencies=[...])`
# gate in the tree, as `<path under app/>::<the Depends() expression>`. §A derives the same set from
# the AST and compares BOTH WAYS, because a bare `len(derived) >= N` tolerates a subject LEAVING the
# scan: a gate whose router stopped declaring its dependency, or whose file the walk stopped
# reaching, would take its own assertion with it and the harness would still read green. A gate that
# leaves now NAMES ITSELF; a gate that arrives has to be declared here by a person, who at that
# moment has to decide whether it asks the public declaration.
DECLARED_GATES = {
    "modules/account/royalty_router.py::require_module('royalty')",
    "modules/approvals/router.py::_require_member",
    "modules/chat/router.py::_require_member",
    "modules/marketing/router.py::require_module('marketing')",
    "modules/pos/inventory_integrity_router.py::_pr._require_pos_access",
    "modules/pos/router.py::_require_pos_access",
    "modules/supply/router.py::require_module('supply_ordering')",
}


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def _py_files():
    for root, _dirs, files in os.walk(APP_DIR):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def _router_level_gates():
    """Every function named in an `APIRouter(..., dependencies=[Depends(f)])`, with its file."""
    found = []
    for path in _py_files():
        try:
            tree = ast.parse(open(path).read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "APIRouter"):
                continue
            for kw in node.keywords:
                if kw.arg != "dependencies":
                    continue
                for dep in ast.walk(kw.value):
                    if isinstance(dep, ast.Call) and getattr(dep.func, "id", None) == "Depends":
                        for inner in dep.args:
                            found.append((path, ast.unparse(inner)))
    return found


def _source_of(expr, path):
    """The source of the gate `expr` names, looked up in `path` first, then anywhere in app/.

    A factory (`require_module("x")`) is resolved to the factory's own source, because the gate the
    router installs is the closure it returns."""
    name = re.sub(r"\(.*\)$", "", expr).split(".")[-1].strip()
    for candidate in [path] + [p for p in _py_files() if p != path]:
        try:
            tree = ast.parse(open(candidate).read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.unparse(node), candidate
    return None, None


print("\n§A  every router-level gate ASKS the public declaration\n")
gates = _router_level_gates()
_derived = {f"{os.path.relpath(path, APP_DIR)}::{expr}" for path, expr in gates}
_left = sorted(DECLARED_GATES - _derived)
_arrived = sorted(_derived - DECLARED_GATES)
check("the derived gates are EXACTLY the declared set (a gate that leaves names itself)",
      not _left and not _arrived,
      (("no longer derived: " + ", ".join(_left) + " ") if _left else "")
      + (("undeclared: " + ", ".join(_arrived)) if _arrived else ""))
seen = set()
for path, expr in gates:
    key = (os.path.relpath(path, APP_DIR), expr)
    if key in seen:
        continue
    seen.add(key)
    src, where = _source_of(expr, path)
    rel = os.path.relpath(path, APP_DIR)
    if expr in EXCUSED:
        check(f"{rel}: {expr} is excused — {EXCUSED[expr]}", True)
        continue
    check(f"{rel}: {expr} asks router_gate.stand_down",
          bool(src) and "stand_down(" in src,
          "the gate cannot ask whether the platform declared this path public"
          if src else f"could not find the source of {expr}")

print("\n§B  the stand-down DEREFERENCES the one home and holds no copy of it\n")
gate_src = open(os.path.join(APP_DIR, "core", "router_gate.py")).read()
body = gate_src.split('"""', 2)[-1]       # past the module docstring: the code only
check("router_gate calls tenant_middleware.public_surface_allows",
      "public_surface_allows(" in body)
check("router_gate holds NO second copy of the allowlist",
      not re.search(r"_PUBLIC_(EXACT|PREFIXES|METHODS)", body))
check("router_gate holds NO api path literal of its own",
      not re.search(r'["\']/(api|health|docs)', body))
check("router_gate fails CLOSED with no request",
      "is None" in body and "return False" in body)

mw = open(os.path.join(APP_DIR, "core", "tenant_middleware.py")).read()
check("public_surface_allows is the MIDDLEWARE'S OWN branch, not a parallel answer",
      "if public_surface_allows(path, method," in mw)
_psa, _ = _source_of("public_surface_allows", os.path.join(APP_DIR, "core", "tenant_middleware.py"))
check("public_surface_allows asks both halves (path AND method)",
      bool(_psa) and "_is_public(" in _psa and "_public_method_ok(" in _psa)

print("\n§C  measured: the gate stands down where declared, and refuses everywhere else\n")
from fastapi import FastAPI                                    # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

from app.core import router_gate as rg                         # noqa: E402
from app.core import tenant_middleware as tm                   # noqa: E402

MODULES = (("app.modules.pos.router", "pos"),
           ("app.modules.approvals.router", "approvals"),
           ("app.modules.chat.router", "chat"),
           ("app.modules.marketing.router", "marketing"),
           ("app.modules.supply.router", "supply"))

total_declared = 0
for dotted, label in MODULES:
    try:
        mod = __import__(dotted, fromlist=["router"])
    except Exception as exc:                                   # a module that will not import is its
        check(f"{label}: router imports", False, str(exc)[:120])
        continue
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
    client = TestClient(app, raise_server_exceptions=False)
    probes = []
    for route in mod.router.routes:
        for method in sorted((getattr(route, "methods", None) or set()) & {"GET"}):
            probes.append((method, re.sub(r"\{[^}]+\}", "1", "/api/v1" + getattr(route, "path", ""))))

    armed = {(m, p): client.request(m, p).status_code for m, p in probes}
    # NEGATIVE CONTROL: disarm the stand-down and ask again. A route the platform did NOT declare
    # public must answer IDENTICALLY either way — that is what "the stand-down is exactly as wide as
    # the declaration" means, and it holds for an entitlement gate's 403 as well as an auth gate's
    # 401. A declared-public route is expected to differ: armed it is not refused.
    _real = rg.stand_down
    rg.stand_down = lambda request: False
    try:
        disarmed = {(m, p): client.request(m, p).status_code for m, p in probes}
    finally:
        rg.stand_down = _real

    bad_locked, widened, declared, gated = [], [], 0, 0
    for key in probes:
        m, p = key
        if tm.public_surface_allows(p, m):
            declared += 1
            if armed[key] in (401, 403):
                bad_locked.append(f"{m} {p} -> {armed[key]}")
        else:
            gated += 1
            if armed[key] != disarmed[key]:
                widened.append(f"{m} {p} -> {disarmed[key]} became {armed[key]}")
    total_declared += declared
    check(f"{label}: no declared-public route is refused by the gate ({declared} declared)",
          not bad_locked, "; ".join(bad_locked[:3]))
    check(f"{label}: the stand-down widened NOTHING a gated route could reach ({gated} gated)",
          not widened, "; ".join(widened[:3]))

check("at least one declared-public route sits under a gated router (the lock is ARMED, not vacuous)",
      total_declared >= 1, f"{total_declared} found — if this is 0 the §C checks prove nothing")

print("\n" + "=" * 78)
print(f"router gate dereference lock: {len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    for f in FAILED:
        print("  FAILED:", f)
    sys.exit(1)
