#!/usr/bin/env python3
"""PROOF + LOCK — the public allowlist can never be satisfied by a PARAMETERISED route.

THE DEFECT THIS CLOSES (security review 2026-10-28).
`tenant_middleware._is_public` ended with:

    if path.endswith("/run-due") or path.endswith("/promote-due"):
        return True

The intent is right: `pg_cron` calls the sweeps with no JWT and authenticates with its own
`x-notify-secret`, so the middleware's auth requirement must not fire before the handler. The
EXPRESSION is wrong, because it reads the REQUEST PATH, not the ROUTE. This app has 315 routes whose
last segment is a path PARAMETER — `/commcalc/config/{period}`, `/commcalc/calculate/{period}`,
`/commcalc/upload/{file_type}`, `/core/roles/{role_id}` … — and asking for one of them with the
literal value `run-due` produces a path that ends in `/run-due`. The allowlist then fires, which
skips BOTH the authentication requirement AND the `org_id` rewrite:

    PUT /api/v1/commcalc/config/run-due?org_id=<any tenant>      # no token at all
    POST /api/v1/commcalc/calculate/run-due?org_id=<any tenant>  # no token at all

`save_config` has no permission gate of its own (nor do most of the 315) — it relies entirely on the
middleware — so that is an unauthenticated write into another tenant's `commcalc.payout_config`, and
an unauthenticated trigger of another tenant's commission recalculation.

THE CLASS, NOT THE INSTANCE (CLAUDE.md "a fix is a DESIGN fix"). The general fact that was wrong is
not "two routes happen to collide" but **"a request path is not evidence of which route it reached"**.
So the fix stops guessing from the path and DEREFERENCES the thing that actually declares which paths
are self-authenticating sweeps: the app's own route table. A literal (parameter-free) template ending
in `/run-due` IS a sweep; a parameterised template can never become one, whatever is typed into it.
That is the same move `app/core/path_redact.py` already makes for "which URL segments are secrets",
and this harness reuses its `route_templates()` rather than walking the router a second time.

WHAT THIS PROVES (DB-free; §F builds the real FastAPI app, which needs no database):
  A. the collision is real      — a parameterised template, filled with "run-due"/"promote-due",
                                  must NOT be public
  B. the sweeps still work      — every literal cron route IS public (a sweep that 401s is an outage)
  C. allowlist unchanged        — the exact/prefix/method entries behave exactly as before
  D. fail-safe                  — an unreadable route table falls back to the old suffix match rather
                                  than stranding 37 cron jobs
  E. break-glass                — CRON_PATH_LITERAL_ONLY=0 restores the pre-fix behaviour exactly
  F. the real app               — against the REAL 1805-route table: every literal cron route is
                                  public and NONE of the 315 parameterised routes can be
  G. wiring lock                — the dispatcher passes the derived set, and the bare suffix match is
                                  gone from `_is_public`'s own body
  N. negative controls          — the PRE-FIX logic must FAIL §A, and §F must actually find routes
                                  (a lock that measures nothing passes forever)

Run: python3 backend/harness_public_allowlist_lock.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  -> {detail!r}" if detail is not None else ""))


from app.core import tenant_middleware as tm   # noqa: E402

# The two shapes a cron entrypoint takes today. Read off the module so the harness cannot drift from it.
SUFFIXES = (tm._RUN_DUE_SUFFIX, tm._PROMOTE_DUE_SUFFIX)

# A handful of REAL parameterised templates whose last segment is a parameter, with the verb that makes
# the collision bite. Spelled out (not derived) so this section still asserts something if the route
# table cannot be built.
COLLIDING = (
    ("PUT", "/api/v1/commcalc/config/{period}"),
    ("POST", "/api/v1/commcalc/calculate/{period}"),
    ("POST", "/api/v1/commcalc/upload/{file_type}"),
    ("PUT", "/api/v1/commcalc/targets/{period}"),
    ("PUT", "/api/v1/commcalc/expenses/{period}"),
    ("PUT", "/api/v1/core/roles/{role_id}"),
    ("GET", "/api/v1/commcalc/commissions/{period}"),
)

# Literal cron routes that MUST stay reachable without a JWT (a sweep that 401s is a silent outage).
LITERAL_CRON = (
    "/api/v1/notify/run-due",
    "/api/v1/closing/sweep/run-due",
    "/api/v1/commcalc/sales/promote-due",
)


def fill(template, value="run-due"):
    """A parameterised template with its FINAL parameter replaced by a literal value."""
    return re.sub(r"/\{[A-Za-z_][A-Za-z0-9_]*\}$", "/" + value, template)


def cron_set(paths):
    """The derived 'these literal paths are sweeps' fact, as the dispatcher builds it."""
    return frozenset(paths)


print("A. a parameterised route cannot be made public by typing the sweep's name into it")
for value in ("run-due", "promote-due"):
    for verb, template in COLLIDING:
        concrete = fill(template, value)
        check(f"{verb} {concrete} is NOT public (route is {template})",
              not tm._is_public(concrete, cron_paths=cron_set(LITERAL_CRON)), concrete)

print("B. every literal cron route is still public (the sweeps keep working)")
for p in LITERAL_CRON:
    check(f"{p} is public", tm._is_public(p, cron_paths=cron_set(LITERAL_CRON)), p)
check("a literal cron route NOT in the derived set is not public (the set is the authority)",
      not tm._is_public("/api/v1/made-up/run-due", cron_paths=cron_set(LITERAL_CRON)))

print("C. the rest of the allowlist is byte-identical in behaviour")
cs = cron_set(LITERAL_CRON)
check("/health stays public", tm._is_public("/health", cron_paths=cs))
check("/api/v1/core/auth-config stays public", tm._is_public("/api/v1/core/auth-config", cron_paths=cs))
check("/api/v1/core/me prefix stays public", tm._is_public("/api/v1/core/me/password-changed", cron_paths=cs))
check("/api/v1/notify/dl prefix stays public", tm._is_public("/api/v1/notify/dl/abc.def", cron_paths=cs))
check("a sibling of a prefix entry is NOT over-matched",
      not tm._is_public("/api/v1/core/members", cron_paths=cs))
check("an ordinary protected route stays protected",
      not tm._is_public("/api/v1/commcalc/commissions/July%202026", cron_paths=cs))
check("method scoping untouched: auth-config is GET-only public",
      tm._public_method_ok("/api/v1/core/auth-config", "GET")
      and not tm._public_method_ok("/api/v1/core/auth-config", "PUT"))

print("D. fail-safe — an unreadable route table must not strand 37 cron jobs")
check("cron_paths=None falls back to the suffix match (sweeps reachable)",
      tm._is_public("/api/v1/notify/run-due", cron_paths=None))
check("cron_paths=empty set ALSO falls back (an empty derivation means 'could not read it')",
      tm._is_public("/api/v1/notify/run-due", cron_paths=frozenset()))
check("the fallback is the only path on which a parameterised route is public — and it is named",
      tm._is_public("/api/v1/commcalc/config/run-due", cron_paths=None))

print("E. break-glass")
try:
    os.environ["CRON_PATH_LITERAL_ONLY"] = "0"
    check("CRON_PATH_LITERAL_ONLY=0 restores the pre-fix suffix match exactly",
          tm._is_public("/api/v1/commcalc/config/run-due", cron_paths=cs))
    os.environ["CRON_PATH_LITERAL_ONLY"] = "1"
    check("CRON_PATH_LITERAL_ONLY=1 (and unset) enforces",
          not tm._is_public("/api/v1/commcalc/config/run-due", cron_paths=cs))
finally:
    os.environ.pop("CRON_PATH_LITERAL_ONLY", None)
check("default (unset) enforces",
      not tm._is_public("/api/v1/commcalc/config/run-due", cron_paths=cs))

print("F. against the REAL route table (1805 routes)")
real_literal = real_param = pre_fix_collisions = 0
try:
    from app.main import app as real_app          # no database work at import
    from app.core import path_redact as pr
    templates = pr.route_templates(real_app)
    derived = tm.cron_paths_for_app(real_app)
    literal = sorted({t for t in templates if "{" not in t
                      and any(t.endswith(s) for s in SUFFIXES)})
    real_literal = len(literal)
    check("the derived set IS exactly the literal cron routes the app declares",
          derived == frozenset(literal), (len(derived), real_literal))
    for p in literal:
        check(f"literal cron route public: {p}", tm._is_public(p, cron_paths=derived), p)
    def allowlisted_for_another_reason(p):
        """Public via the EXACT/PREFIX entries — deliberately so (each self-gates in its handler on a
        token, an HMAC or a least-privilege secret). This section is about the SWEEP rule only."""
        if p in tm._PUBLIC_EXACT:
            return True
        return any(p == q or p.startswith(q + "/") for q in tm._PUBLIC_PREFIXES)

    collide, pre_fix = [], set()
    for t in templates:
        if not re.search(r"/\{[A-Za-z_][A-Za-z0-9_]*\}$", t):
            continue
        for value in ("run-due", "promote-due"):
            c = fill(t, value)
            if c in derived or allowlisted_for_another_reason(c):
                continue
            if tm._is_public(c, cron_paths=derived):
                collide.append((t, c))
            if c.endswith(SUFFIXES):
                pre_fix.add(t)      # what the PRE-FIX suffix test would have handed away
    real_param = len({t for t, _ in collide})
    pre_fix_collisions = len(pre_fix)
    print(f"  ..    {pre_fix_collisions} parameterised routes WERE reachable this way before the fix")
    check("NO parameterised route in the whole app can be reached unauthenticated this way",
          collide == [], collide[:5])
except ImportError as exc:                        # fastapi absent in a stdlib-only runner
    print(f"  SKIP  real route table ({exc})")

print("G. wiring lock")
src = open(os.path.join(HERE, "app", "core", "tenant_middleware.py")).read()
body = src.split("def _is_public(", 1)[1].split("\ndef ", 1)[0] if "def _is_public(" in src else ""
check("_is_public takes the derived set", re.search(r"def _is_public\([^)]*cron_paths", src) is not None)
check("_is_public no longer ENDS on a bare suffix test of the request path",
      not re.search(r"if\s+path\.endswith\(_RUN_DUE_SUFFIX\)\s+or\s+path\.endswith\(_PROMOTE_DUE_SUFFIX\)"
                    r"\s*:\s*\n\s+return True\s*\n\s*return False", body))
check("the dispatcher passes the derived set into _is_public",
      re.search(r"_is_public\(\s*path\s*,\s*cron_paths\s*=", src) is not None)
check("the derived set comes from the app's own route table via path_redact.route_templates",
      "route_templates" in src)
check("cron_paths_for_app is public API (the harness and the dispatcher read the same fact)",
      hasattr(tm, "cron_paths_for_app"))

print("N. negative controls")
# The PRE-FIX logic, written out. It MUST fail §A — a harness that passes against the bug it was
# written for is decoration.
def pre_fix_is_public(path):
    if path in tm._PUBLIC_EXACT:
        return True
    for p in tm._PUBLIC_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return path.endswith(tm._RUN_DUE_SUFFIX) or path.endswith(tm._PROMOTE_DUE_SUFFIX)


check("control: the PRE-FIX logic really does make PUT /commcalc/config/run-due public",
      pre_fix_is_public("/api/v1/commcalc/config/run-due") is True)
check("control: the PRE-FIX logic really does make POST /commcalc/calculate/run-due public",
      pre_fix_is_public("/api/v1/commcalc/calculate/run-due") is True)
check("control: §F actually measured something (literal cron routes found)", real_literal >= 20, real_literal)
check("control: §F actually found colliding routes to refuse (the defect was real and widespread)",
      pre_fix_collisions >= 100, pre_fix_collisions)
check("control: fill() replaces only a TRAILING parameter",
      fill("/a/{x}/b") == "/a/{x}/b" and fill("/a/{x}") == "/a/run-due")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
