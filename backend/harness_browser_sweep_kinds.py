#!/usr/bin/env python3
"""WHICH SWEEPS ACTUALLY LAUNCH A BROWSER — the declared set vs. the code. DB-free, stdlib only.

THE DEFECT THIS PINS (2026-09-20, found the same day it shipped). `_BROWSER_SWEEP_KINDS` told
`/connectors/run-due` which sweep kinds cannot run in-process on a `SERVICE_ROLE=api` service. It was
written as {vip, dlar, epay, b2b} — every kind that pulls from a vendor PORTAL. But "logs into a
portal" and "launches Chromium" are different facts:

    epay_sweep.py   `from playwright.sync_api import sync_playwright` behind assert_browser_allowed()
    dlar_sweep.py   requests + BeautifulSoup — no playwright anywhere
    vip_sweep.py    requests + BeautifulSoup — no playwright anywhere
    b2b_sweep.py    requests + BeautifulSoup — no playwright anywhere

On the live deployment (SERVICE_ROLE=api, BROWSER_SERVICE_URL unset) the over-declared set would have
refused dlar — which had imported successfully hours earlier — and vip, which ran four days earlier.
A guard written to explain ONE broken connector would have taken out TWO healthy ones. It was caught
before its next tick, so nothing was lost.

THE CLASS, not the instance (CLAUDE.md, "a fix is a DESIGN fix or it is not a fix"): a hand-kept list
of "which sweeps need a browser" is a SECOND COPY of a fact the sweep modules already own. One fact,
one home — the home is the module, and the constant is only its fast form, because dispatch must not
parse source on every tick. So this harness DEREFERENCES the real home and fails the build whenever
the copy drifts, IN EITHER DIRECTION:

    · a kind declared that no longer launches a browser  → it would be refused for nothing
    · a sweep that gained playwright but was not declared → it would fail deep in the sweep again,
      which is exactly the ePay defect §19.23 was written to end

Stdlib only: reads the source, never imports the app, never touches a database or a network.
"""
import ast
import os
import re
import sys

PASS = FAIL = 0
HERE = os.path.dirname(os.path.abspath(__file__))
COMMCALC = os.path.join(HERE, "app", "modules", "commcalc")
ROUTER = os.path.join(COMMCALC, "router.py")

# The markers that mean "this module can launch a browser". `assert_browser_allowed` is included
# deliberately: it is the guard a browser call site is REQUIRED to sit behind, so a module carrying it
# is declaring browser work even if the playwright import is spelled some other way.
BROWSER_MARKERS = ("playwright", "assert_browser_allowed")


def ok(cond, msg, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s%s" % (msg, (" " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  ✗ %s%s" % (msg, (" " + extra) if extra else ""))


def eq(got, want, msg):
    ok(got == want, msg, "" if got == want else "[got %r want %r]" % (got, want))


_SRC_CACHE = {}
_TREE_CACHE = {}
_FNS_CACHE = {}


def _src(path):
    if path not in _SRC_CACHE:
        with open(path, "r", encoding="utf-8") as fh:
            _SRC_CACHE[path] = fh.read()
    return _SRC_CACHE[path]


def _tree(src):
    """router.py is ~35k lines; parsing it once and reusing the tree keeps this harness in seconds."""
    key = id(src)
    if key not in _TREE_CACHE:
        _TREE_CACHE[key] = ast.parse(src)
    return _TREE_CACHE[key]


def _functions(src):
    """name -> source text, for every function in the module. Built once.

    Sliced from pre-split lines rather than via `ast.get_source_segment`, which re-splits the whole
    source on every call — O(n) each, and router.py is ~35k lines, so the obvious version takes
    minutes instead of the second this does."""
    key = id(src)
    if key not in _FNS_CACHE:
        lines = src.splitlines()
        out = {}
        for n in ast.walk(_tree(src)):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(n, "end_lineno", None) or n.lineno
                out[n.name] = "\n".join(lines[n.lineno - 1:end])
        _FNS_CACHE[key] = out
    return _FNS_CACHE[key]


def _literal_set(name, src):
    """The frozenset({...}) / {...} literal assigned to `name` at module level. PURE."""
    tree = _tree(src)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == name:
                v = node.value
                if isinstance(v, ast.Call):          # frozenset({...})
                    v = v.args[0] if v.args else None
                return set(ast.literal_eval(v)) if v is not None else set()
    return None


def _builtin_map(src):
    """_SWEEP_BUILTINS: sweep_kind -> puller function name. PURE."""
    return _dict_literal("_SWEEP_BUILTINS", src)


def _dict_literal(name, src):
    tree = _tree(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return dict(ast.literal_eval(node.value))
    return None


def _fn_source(name, src):
    """The source text of a module-level function, or "" if it has none."""
    return _functions(src).get(name, "")


def _modules_reached(fn_src):
    """The commcalc sweep modules a puller calls into, e.g. {'dlar_sweep'}. PURE-ish (text scan)."""
    return set(re.findall(r"\b([a-z0-9_]+_sweep)\s*\.", fn_src))


def _module_launches_browser(mod):
    """Does app/modules/commcalc/<mod>.py carry a browser marker? The ONE home of this fact."""
    path = os.path.join(COMMCALC, mod + ".py")
    if not os.path.exists(path):
        return None
    low = _src(path).lower()
    return any(m in low for m in BROWSER_MARKERS)


def main():
    global FAIL
    print("BROWSER SWEEP KINDS — the declared set must equal what the code actually does\n")
    rsrc = _src(ROUTER)

    declared = _literal_set("_BROWSER_SWEEP_KINDS", rsrc)
    builtins = _builtin_map(rsrc)
    ok(declared is not None, "_BROWSER_SWEEP_KINDS is a readable literal in router.py")
    ok(isinstance(builtins, dict) and builtins, "_SWEEP_BUILTINS is a readable literal in router.py",
       str(sorted(builtins or {})))
    if declared is None or not builtins:
        print("\n%d passed, %d failed" % (PASS, FAIL + 1))
        return 1

    print("\n1. DERIVE THE TRUTH — for each builtin kind, does its puller reach a browser?")
    derived = set()
    for kind, fname in sorted(builtins.items()):
        fsrc = _fn_source(fname, rsrc)
        ok(bool(fsrc), "kind %-5s resolves to puller %s()" % (kind, fname))
        mods = _modules_reached(fsrc)
        # The puller's own body counts too: a sweep that inlines the browser call needs no module.
        own = any(m in fsrc.lower() for m in BROWSER_MARKERS)
        hits = {m for m in mods if _module_launches_browser(m)}
        launches = bool(hits or own)
        if launches:
            derived.add(kind)
        print("      %-5s -> %s  modules=%s  browser=%s"
              % (kind, fname, sorted(mods) or "-", "YES" if launches else "no"))

    print("\n2. THE SET IS EXACTLY THE DERIVED TRUTH — no more, no less")
    eq(declared, derived,
       "_BROWSER_SWEEP_KINDS equals the set of builtin kinds whose puller can launch a browser")
    over = sorted(declared - derived)
    under = sorted(derived - declared)
    ok(not over,
       "no kind is declared that never launches a browser (it would be refused for nothing)",
       "OVER-DECLARED: %s" % over if over else "")
    ok(not under,
       "no browser sweep is left undeclared (it would fail deep in the sweep — the ePay defect)",
       "UNDER-DECLARED: %s" % under if under else "")

    print("\n3. THE MODULES THEMSELVES — the home this set dereferences")
    eq(_module_launches_browser("epay_sweep"), True,
       "epay_sweep.py carries a browser marker (it calls sync_playwright)")
    for m in ("dlar_sweep", "vip_sweep", "b2b_sweep"):
        eq(_module_launches_browser(m), False,
           "%s.py carries NO browser marker (requests + BeautifulSoup)" % m)

    print("\n4. NO ENDPOINT GUARDS A SWEEP THAT DOES NOT NEED ONE")
    # require_browser_service() on a non-browser run-due would 503 (or pointlessly proxy) a sweep that
    # runs perfectly well on the API service. Checked by parsing each handler, never by slicing a
    # guessed number of characters after the decorator — two assertions were rewritten for exactly
    # that mistake while §19.23 was being built.
    handlers = _functions(rsrc)
    for kind, handler in (("vip", "vip_sweep_run_due"), ("dlar", "dlar_sweep_run_due"),
                          ("b2b", "b2b_sweep_run_due")):
        body = handlers.get(handler, "")
        ok(bool(body), "%s() is present" % handler)
        ok("require_browser_service()" not in body,
           "%s does NOT require a browser service — %s never launches Chromium" % (handler, kind))
    epay = handlers.get("epay_sweep_run_due", "")
    ok(bool(epay), "epay_sweep_run_due() is present")
    ok("require_browser_service()" in epay,
       "epay_sweep_run_due DOES require the browser service — it is the one that launches Chromium")

    print("\n5. THE DISPATCHER STILL ASKS, AND ONLY ABOUT DECLARED KINDS")
    disp = handlers.get("connectors_run_due", "")
    ok("_BROWSER_SWEEP_KINDS" in disp,
       "/connectors/run-due consults the set rather than restating which kinds need a browser")
    ok("_browser_allowed()" in disp,
       "...and asks the service-role gate, so a browser-capable service dispatches normally")
    ok(disp.count("_sweep_registry()") >= 1,
       "...and an externally registered kind is never assumed to need a browser")

    print("\n6. BROWSER WORK OUTSIDE THE SWEEP REGISTRY — every module that launches one is declared + guarded")
    _browser_modules_elsewhere()

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


# Modules OUTSIDE commcalc that can launch Chromium (index §36). Derived from the code below and compared
# both ways, exactly like _BROWSER_SWEEP_KINDS: a module that starts launching a browser without being declared
# here (and guarded) fails the build, and so does a declared module that no longer launches one.
APP = os.path.join(HERE, "app")
BROWSER_MODULES_ELSEWHERE = {os.path.join("modules", "supply", "portal.py")}
_LAUNCH_RE = re.compile(r"from\s+playwright|import\s+playwright|sync_playwright|assert_browser_allowed\(\)")
# The endpoints of such a module's router that reach a live browser must call require_browser_service() (so a
# SERVICE_ROLE=api deploy proxies them to BROWSER_SERVICE_URL or answers a plain 503 — never a fake success);
# the ones that do not reach a browser must NOT (they would 503 for nothing).
_BROWSER_CALLS = ("start_session(", "live_login_start(", "_live_pull(")
SUPPLY_ROUTER = os.path.join(APP, "modules", "supply", "router.py")


def _browser_modules_elsewhere():
    derived = set()
    for root, _dirs, files in os.walk(os.path.join(APP, "modules")):
        if os.path.join("modules", "commcalc") in root:
            continue
        for fn in files:
            if fn.endswith(".py"):
                path = os.path.join(root, fn)
                if _LAUNCH_RE.search(_src(path)):
                    derived.add(os.path.relpath(path, APP))
    eq(derived, BROWSER_MODULES_ELSEWHERE,
       "the modules outside commcalc that can launch a browser are exactly the declared set")
    for rel in sorted(derived):
        fns = _functions(_src(os.path.join(APP, rel)))
        for name, body in sorted(fns.items()):
            if "sync_playwright" in body and "import" in body:
                i_launch = body.find("sync_playwright")
                i_guard = body.find("assert_browser_allowed()")
                ok(0 <= i_guard < i_launch,
                   "%s:%s() calls assert_browser_allowed() before it imports/launches playwright" % (rel, name))
    if not os.path.exists(SUPPLY_ROUTER):
        ok(False, "supply/router.py is present")
        return
    handlers = _functions(_src(SUPPLY_ROUTER))
    reach, plain = [], []
    for name, body in sorted(handlers.items()):
        if not re.search(r"@router\.(get|post|put|patch|delete)", _src(SUPPLY_ROUTER).split("def %s(" % name)[0][-400:]):
            continue
        (reach if any(c in body for c in _BROWSER_CALLS) else plain).append(name)
    ok(len(reach) >= 4, "the supply router's browser endpoints were found", str(reach))
    for name in reach:
        ok("require_browser_service()" in handlers[name],
           "supply %s() reaches a live browser and calls require_browser_service()" % name)
    for name in plain:
        ok("require_browser_service()" not in handlers[name],
           "supply %s() reaches no browser and does NOT require the browser service" % name)


if __name__ == "__main__":
    sys.exit(main())
