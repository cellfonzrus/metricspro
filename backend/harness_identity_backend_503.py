"""Proof harness — tenant_middleware identity honesty (auth-ux hardening, 2026-08-03).

THIS IS THE HIGHEST-BLAST-RADIUS FILE IN THE APP. `_resolve_identity` used to wrap token
verification AND the membership fetch in ONE broad `except Exception -> ok=False`, so an
infrastructure fault (stale pool / DatabaseUnavailable / PostgREST down) was reported to the user as
401 "authentication required" and never logged. This harness proves the narrowing:

  A. BYTE-IDENTICAL 401.  Absent token, garbage token, unverifiable token, auth service throwing —
     every one still produces status 401 with the EXACT bytes b'{"detail":"authentication required"}'
     and the exact same headers. Additionally the `_reject_401` function's SOURCE is asserted
     byte-identical to origin/main, so the response cannot have drifted at all.
  B. VALID TOKEN STILL PASSES, with the org_id query param rewritten to the verified membership;
     super-admin still bypasses the rewrite (cross-tenant admin intact).
  C. MEMBERSHIP-FETCH OUTAGE -> 503 (not 401), distinct body, Retry-After, plus exactly ONE
     core.failure_log row and ZERO other DB writes. Proven with the real db_resilience
     DatabaseUnavailable, and with a plain RuntimeError.
  D. ISOLATION FIX. Under the old code a membership-read failure returned [] -> "verified user with
     no app_users row" -> NO org_id rewrite -> the CLIENT-SUPPLIED org_id was honored. Proven that
     the request no longer reaches the app at all in that state.
  E. SCHEMA TOLERANCE UNCHANGED. An un-run mig 706/711 (missing role / twofa_enabled / is_default_org
     columns) still falls down the ladder and resolves normally — it must NOT become a 503.
  F. BREAK-GLASS. IDENTITY_BACKEND_503=0 restores the old behaviour exactly.
  G. IDENTITY CACHE. Positive results cached for the TTL (one auth call for N requests); negative
     results and outages are NOT cached (a hiccup can never pin a good user out).
  H. THROTTLE. An outage storm writes at most one failure_log row per _OUTAGE_LOG_MIN_GAP.

Offline: no network, no DB, no real Supabase. Run:
    cd backend && python3 harness_identity_backend_503.py
"""
import asyncio
import importlib
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.core.database as DB                     # noqa: E402
import app.core.tenant_middleware as M             # noqa: E402
from app.core.db_resilience import DatabaseUnavailable   # noqa: E402

P = F = 0


def ck(name, cond):
    global P, F
    if cond:
        P += 1
        print("  ok  %s" % name)
    else:
        F += 1
        print("  XX  %s" % name)


# ── fakes ────────────────────────────────────────────────────────────────────────────────────────
class FakeAuth:
    """Supabase auth shim. `tokens` maps token -> uid; anything else raises like GoTrue does."""

    def __init__(self, tokens, calls, raise_on=None):
        self.tokens, self.calls, self.raise_on = tokens, calls, (raise_on or set())

    def get_user(self, token):
        self.calls.append(token)
        if token in self.raise_on:
            raise RuntimeError("auth service refused")
        uid = self.tokens.get(token)
        if uid is None:
            raise RuntimeError("invalid JWT")
        return type("R", (), {"user": type("U", (), {"id": uid})()})()


class FakeAdmin:
    def __init__(self, auth):
        self.auth = auth


class FakeQuery:
    """Minimal PostgREST chain. `fail_cols` = column-lists whose select() raises (models an un-run
    migration); `boom` = an exception raised by EVERY select (models the store being unreachable)."""

    def __init__(self, table, rows, writes, fail_cols=None, boom=None):
        self.table, self.rows, self.writes = table, rows, writes
        self.fail_cols, self.boom = (fail_cols or set()), boom
        self._payload = None

    def select(self, cols):
        if self.boom is not None:
            raise self.boom
        if cols in self.fail_cols:
            raise RuntimeError("column does not exist (migration un-run): %s" % cols)
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self._payload is not None:
            self.writes.append((self.table, self._payload))
            return type("R", (), {"data": [self._payload]})()
        return type("R", (), {"data": list(self.rows)})()


class FakeClient:
    def __init__(self, rows, writes, fail_cols=None, boom=None, boom_on_schema=False):
        self.rows, self.writes = rows, writes
        self.fail_cols, self.boom, self.boom_on_schema = fail_cols, boom, boom_on_schema
        self._schema = None

    def schema(self, name):
        if self.boom_on_schema:
            raise RuntimeError("connection is closed")
        self._schema = name
        return self

    def table(self, name):
        # failure_log writes must always be allowed through (they are the thing under test in C/H)
        if name == "failure_log":
            return FakeQuery(name, [], self.writes)
        return FakeQuery(name, self.rows, self.writes, self.fail_cols, self.boom)


class Captured:
    """Collects the ASGI response and whatever scope the downstream app saw."""

    def __init__(self):
        self.status = None
        self.headers = None
        self.body = b""
        self.reached_app = False
        self.app_scope = None


def run_request(*, token, rows=None, writes=None, fail_cols=None, boom=None, boom_on_schema=False,
                auth_tokens=None, auth_raise=None, auth_calls=None, path="/api/v1/commcalc/summary",
                query=b"org_id=OTHER-TENANT", headers=None):
    """Drive the REAL TenantScopeMiddleware end to end and capture what came back."""
    cap = Captured()
    writes = writes if writes is not None else []
    auth_calls = auth_calls if auth_calls is not None else []
    auth = FakeAuth(auth_tokens if auth_tokens is not None else {"GOOD": "uid-1"},
                    auth_calls, auth_raise)
    client = FakeClient(rows or [], writes, fail_cols, boom, boom_on_schema)

    orig_admin, orig_sb = DB.get_supabase_admin, DB.get_supabase
    DB.get_supabase_admin = lambda: FakeAdmin(auth)
    DB.get_supabase = lambda: client

    async def app(scope, receive, send):
        cap.reached_app = True
        cap.app_scope = scope

    async def send(msg):
        if msg["type"] == "http.response.start":
            cap.status = msg["status"]
            cap.headers = msg.get("headers")
        else:
            cap.body += msg.get("body", b"")

    hdrs = [(b"host", b"x")]
    if token is not None:
        hdrs.append((b"authorization", ("Bearer %s" % token).encode()))
    for k, v in (headers or {}).items():
        hdrs.append((k.encode(), v.encode()))
    scope = {"type": "http", "path": path, "query_string": query, "headers": hdrs}
    try:
        asyncio.run(M.TenantScopeMiddleware(app)(scope, receive=None, send=send))
    finally:
        DB.get_supabase_admin, DB.get_supabase = orig_admin, orig_sb
    cap.writes = writes
    cap.auth_calls = auth_calls
    return cap


def fresh(**env):
    """Reload the middleware with a clean cache + the given env, so no test leaks into another."""
    for k in ("MULTI_TENANT_ENFORCE", "REQUIRE_AUTH", "TWOFA_ENFORCE", "IDENTITY_BACKEND_503",
              "STRICT_MEMBERSHIP"):
        os.environ.pop(k, None)
    os.environ["MULTI_TENANT_ENFORCE"] = "1"
    os.environ["TWOFA_ENFORCE"] = "0"        # 2FA is a separate gate, proven in prove_twofa_gate.py
    os.environ.update(env)
    importlib.reload(M)
    M._cache.clear()
    M._last_outage_log[0] = 0.0
    return M


GOLDEN_401_BODY = b'{"detail":"authentication required"}'
GOLDEN_401_HEADERS = [(b"content-type", b"application/json"),
                      (b"content-length", str(len(GOLDEN_401_BODY)).encode())]
MEMBER = [{"org_id": "ORG-A", "super_admin": False, "is_default_org": True,
           "role": "district_manager", "twofa_enabled": False}]

print("A. 401 IS BYTE-IDENTICAL — the security-critical invariant")
fresh()
for label, kwargs in [
    ("no bearer token at all", dict(token=None)),
    ("garbage token", dict(token="GARBAGE")),
    ("empty bearer token", dict(token="")),
    ("expired/unverifiable token", dict(token="EXPIRED", auth_tokens={})),
    ("auth service itself throwing", dict(token="GOOD", auth_raise={"GOOD"})),
    ("token resolves to no uid", dict(token="NOUID", auth_tokens={"NOUID": None})),
]:
    fresh()
    c = run_request(**kwargs)
    ck("%s → 401" % label, c.status == 401)
    ck("%s → exact body bytes" % label, c.body == GOLDEN_401_BODY)
    ck("%s → exact headers" % label, list(c.headers) == GOLDEN_401_HEADERS)
    ck("%s → never reached the app" % label, c.reached_app is False)
    ck("%s → zero DB writes" % label, c.writes == [])

# Source-level proof: the responder itself cannot have drifted from main.
try:
    MAIN = subprocess.check_output(
        ["git", "show", "origin/main:backend/app/core/tenant_middleware.py"],
        cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), text=True)
except Exception:
    MAIN = None
if MAIN is None:
    print("  --  skipped source diff (origin/main unavailable)")
else:
    NOW = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "app/core/tenant_middleware.py")).read()

    def fn_src(src, header, stop):
        i = src.index(header)
        j = src.index(stop, i)
        return src[i:j]

    old = fn_src(MAIN, "async def _reject_401(send):", "\nclass TenantScopeMiddleware")
    new = fn_src(NOW, "async def _reject_401(send):", "\n\nasync def _reject_503(send):")
    ck("_reject_401 source is byte-identical to origin/main", old.strip() == new.strip())
    ck("the 401 literal appears exactly once (no second copy to drift)",
       NOW.count('b\'{"detail":"authentication required"}\'') == 1)
    # The public allowlist must not silently open routes. 2026-08-05 (whatsapp-delivery-truth) makes ONE
    # intentional, strictly-TIGHTENING change: /api/v1/remediation/whatsapp-webhook moves from the PREFIX
    # list to the EXACT list and is method-scoped to {GET, POST}. So instead of byte-identity we assert
    # the SET of public paths is unchanged and the move is exactly that one path.
    def paths_in(block):
        return {m.group(1) for m in re.finditer(r'"(/[^"]+)"', block)}

    main_exact = paths_in(fn_src(MAIN, "_PUBLIC_EXACT = frozenset({", "# Public path PREFIXES"))
    now_exact = paths_in(fn_src(NOW, "_PUBLIC_EXACT = frozenset({", "# Public path PREFIXES"))
    main_pre = paths_in(fn_src(MAIN, "_PUBLIC_PREFIXES = (", "# Self-authenticating background sweeps"))
    now_pre = paths_in(fn_src(NOW, "_PUBLIC_PREFIXES = (", "# Self-authenticating background sweeps"))
    WH = "/api/v1/remediation/whatsapp-webhook"
    ck("no public path ADDED vs main (the union is unchanged)",
       (now_exact | now_pre) == (main_exact | main_pre))
    ck("the ONLY exact-list delta is the webhook (moved in)", now_exact - main_exact == {WH})
    ck("the ONLY prefix-list delta is the webhook (moved out)", main_pre - now_pre == {WH})
    ck("nothing else left the exact list", main_exact - now_exact == set())
    ck("nothing else joined the prefix list", now_pre - main_pre == set())
    ck("_is_public body byte-identical to main (only the trailing marker differs)",
       fn_src(MAIN, "def _is_public(path: str) -> bool:", "\ndef _fetch_memberships").strip()
       == fn_src(NOW, "def _is_public(path: str) -> bool:", "\n\n# METHOD SCOPING").strip())

    # ── method scoping (the auth-config lesson, applied to the webhook) ──
    ck("auth-config is public for GET only",
       M._public_method_ok("/api/v1/core/auth-config", "GET") is True
       and M._public_method_ok("/api/v1/core/auth-config", "PUT") is False
       and M._public_method_ok("/api/v1/core/auth-config", "POST") is False)
    ck("the Meta webhook is public for GET+POST only",
       M._public_method_ok(WH, "GET") is True and M._public_method_ok(WH, "POST") is True
       and all(M._public_method_ok(WH, m) is False for m in ("PUT", "DELETE", "PATCH")))
    ck("an UNSCOPED allowlisted path stays method-agnostic (no behaviour change)",
       all(M._public_method_ok("/api/v1/core/me", m) is True
           for m in ("GET", "POST", "PUT", "DELETE")))
    ck("the webhook is now an EXACT match — a sub-path is NOT public",
       M._is_public(WH) is True and M._is_public(WH + "/anything") is False)

print("B. a VALID token still passes, and the org_id rewrite is unchanged")
fresh()
c = run_request(token="GOOD", rows=MEMBER)
ck("reached the app", c.reached_app is True)
ck("org_id rewritten to the verified membership (client value discarded)",
   c.app_scope["query_string"] == b"org_id=ORG-A")
ck("no failure_log row on the happy path", c.writes == [])

fresh()
c = run_request(token="GOOD", rows=[{"org_id": "ORG-A", "super_admin": True, "is_default_org": True}],
                query=b"org_id=OTHER-TENANT")
ck("super-admin still BYPASSES the rewrite (cross-tenant admin intact)",
   c.reached_app is True and c.app_scope["query_string"] == b"org_id=OTHER-TENANT")

print("C. membership-store OUTAGE → 503 (not 401) + exactly one failure_log row")
for label, boom in [
    ("db_resilience.DatabaseUnavailable", DatabaseUnavailable("GET", "/app_users",
                                                              RuntimeError("socket closed"), retried=True)),
    ("plain RuntimeError (stale pool)", RuntimeError("Server disconnected without sending a response")),
]:
    fresh()
    c = run_request(token="GOOD", boom=boom)
    ck("%s → 503" % label, c.status == 503)
    ck("%s → NOT the 401 body" % label, c.body != GOLDEN_401_BODY)
    ck("%s → distinct machine code" % label, b'"identity_backend_unavailable"' in c.body)
    ck("%s → says it is temporary, not an auth problem" % label,
       b"temporarily unavailable" in c.body and b"authentication required" not in c.body)
    ck("%s → Retry-After header" % label, (b"retry-after", b"1") in list(c.headers))
    ck("%s → never reached the app" % label, c.reached_app is False)
    ck("%s → exactly ONE DB write" % label, len(c.writes) == 1)
    ck("%s → and it is a core.failure_log insert" % label, c.writes[0][0] == "failure_log")
    row = c.writes[0][1]
    ck("%s → row is org-stamped" % label, bool(row.get("org_id")))
    ck("%s → category/severity follow the core pattern" % label,
       row.get("category") == "system_error" and row.get("severity") == "error")
    ck("%s → source points at the real code site" % label,
       row.get("source") == "core/tenant_middleware:_resolve_identity")
    ck("%s → detail carries the path + error type" % label,
       row["detail"].get("path") == "/api/v1/commcalc/summary" and row["detail"].get("error_type"))
    ck("%s → remediation names the break-glass" % label,
       "IDENTITY_BACKEND_503=0" in (row.get("remediation") or ""))

# The client object itself being dead (schema() raising) takes the same path.
fresh()
c = run_request(token="GOOD", boom_on_schema=True)
ck("dead client (schema() raises) → 503 too", c.status == 503 and c.reached_app is False)

print("D. ISOLATION — an outage can no longer downgrade tenant scoping")
fresh()
c = run_request(token="GOOD", boom=RuntimeError("down"), query=b"org_id=SOMEONE-ELSES-TENANT")
ck("client-declared org_id never reaches a handler during an outage", c.reached_app is False)
ck("...it is refused, not silently honoured", c.status == 503)

print("E. SCHEMA TOLERANCE unchanged — an un-run migration must NOT become a 503")
fresh()
c = run_request(token="GOOD",
                rows=[{"org_id": "ORG-A", "super_admin": False}],
                fail_cols={"org_id,super_admin,is_default_org,role,twofa_enabled",
                           "org_id,super_admin,is_default_org,role",
                           "org_id,super_admin,is_default_org"})
ck("mig 706/711 un-run → still resolves (ladder falls to the pre-706 select)", c.reached_app is True)
ck("...and rewrites org_id normally", c.app_scope["query_string"] == b"org_id=ORG-A")
ck("...with no failure_log noise", c.writes == [])

fresh()
c = run_request(token="GOOD", rows=MEMBER,
                fail_cols={"org_id,super_admin,is_default_org,role,twofa_enabled"})
ck("only the newest column missing → still resolves", c.reached_app is True)

print("F. BREAK-GLASS — IDENTITY_BACKEND_503=0 restores the pre-2026-08-03 behaviour")
# NOTE (2026-08-05, H2): fully restoring the pre-honesty *pass-through* for an EMPTY membership now
# also requires STRICT_MEMBERSHIP=0 — H2 added an independent fail-closed on the empty-membership case
# (a verified login with no app_users row → 401 instead of honoring the client org_id). The 503
# break-glass and the H2 break-glass are separate switches; this test exercises the 503 one, so it sets
# both OFF to isolate it. (Section J below proves H2 is ON by default with its own switch.)
fresh(IDENTITY_BACKEND_503="0", STRICT_MEMBERSHIP="0")
c = run_request(token="GOOD", boom=RuntimeError("down"), query=b"org_id=OTHER-TENANT")
ck("switch off → no 503", c.status != 503)
ck("switch off → old pass-through restored (empty membership ⇒ no rewrite)",
   c.reached_app is True and c.app_scope["query_string"] == b"org_id=OTHER-TENANT")
ck("switch off → no failure_log write either", c.writes == [])
fresh(IDENTITY_BACKEND_503="0")
c = run_request(token="GARBAGE")
ck("switch off → a bad token is STILL the byte-identical 401",
   c.status == 401 and c.body == GOLDEN_401_BODY)
fresh()   # back on for the rest

print("G. IDENTITY CACHE still behaves")
fresh()
calls = []
for _ in range(5):
    c = run_request(token="GOOD", rows=MEMBER, auth_calls=calls)
ck("positive result cached — 5 requests, 1 auth verification", len(calls) == 1)
ck("cache entry present", "GOOD" in M._cache)

fresh()
calls = []
for _ in range(3):
    run_request(token="GARBAGE", auth_calls=calls)
ck("NEGATIVE results are not cached — every bad token re-verifies", len(calls) == 3)
ck("nothing cached for a bad token", "GARBAGE" not in M._cache)

fresh()
calls = []
for _ in range(3):
    run_request(token="GOOD", boom=RuntimeError("down"), auth_calls=calls)
ck("an OUTAGE is not cached — recovery is automatic on the next request", len(calls) == 3)
ck("nothing cached during the outage", "GOOD" not in M._cache)

fresh()
c = run_request(token="GOOD", rows=MEMBER)
M._cache["GOOD"] = (M._cache["GOOD"][0], 0.0)          # expire it
calls = []
c = run_request(token="GOOD", rows=MEMBER, auth_calls=calls)
ck("an expired cache entry re-verifies", len(calls) == 1 and c.reached_app is True)

print("H. failure_log THROTTLE — an outage storm cannot hammer the dead database")
fresh()
writes = []
for _ in range(25):
    run_request(token="GOOD", boom=RuntimeError("down"), writes=writes)
ck("25 failing requests → exactly 1 failure_log row", len(writes) == 1)
M._last_outage_log[0] = 0.0                             # simulate the gap elapsing
run_request(token="GOOD", boom=RuntimeError("down"), writes=writes)
ck("after the gap elapses, it logs again", len(writes) == 2)
ck("throttle window is a whole minute", M._OUTAGE_LOG_MIN_GAP == 60.0)

print("I. a failing failure_log write can never become a second failure")


class ExplodingClient(FakeClient):
    def table(self, name):
        raise RuntimeError("failure_log unreachable too (mig 112 un-run)")


fresh()
cap = Captured()


async def _app(scope, receive, send):
    cap.reached_app = True


async def _send(msg):
    if msg["type"] == "http.response.start":
        cap.status = msg["status"]
    else:
        cap.body += msg.get("body", b"")


_oa, _os = DB.get_supabase_admin, DB.get_supabase
DB.get_supabase_admin = lambda: FakeAdmin(FakeAuth({"GOOD": "uid-1"}, []))
DB.get_supabase = lambda: ExplodingClient([], [], boom=RuntimeError("down"))
try:
    asyncio.run(M.TenantScopeMiddleware(_app)(
        {"type": "http", "path": "/api/v1/x", "query_string": b"",
         "headers": [(b"authorization", b"Bearer GOOD")]}, receive=None, send=_send))
finally:
    DB.get_supabase_admin, DB.get_supabase = _oa, _os
ck("logging failure swallowed → still a clean 503", cap.status == 503 and cap.reached_app is False)

print("J. public routes and the disabled switch are untouched")
fresh()
c = run_request(token=None, path="/health")
ck("/health still passes with no token", c.reached_app is True)
c = run_request(token=None, path="/api/v1/notify/run-due")
ck("*/run-due sweeps still pass with no token", c.reached_app is True)
c = run_request(token="GOOD", boom=RuntimeError("down"), path="/api/v1/core/me")
ck("an allowlisted path never even reaches identity resolution", c.reached_app is True)

os.environ["MULTI_TENANT_ENFORCE"] = "0"
importlib.reload(M)
c = run_request(token="GOOD", boom=RuntimeError("down"))
ck("MULTI_TENANT_ENFORCE=0 → pure pass-through, no 503", c.reached_app is True and c.status is None)

print("K. H2 — verified login with NO membership fails CLOSED (default) / passes with break-glass")
# A GOOD token that verifies to a uid but whose app_users fetch returns [] = "no membership". The old
# code skipped the org rewrite and honored the CLIENT-SUPPLIED org_id (query=b"org_id=OTHER-TENANT").
fresh()                                   # STRICT_MEMBERSHIP defaults ON
c = run_request(token="GOOD", rows=[], query=b"org_id=OTHER-TENANT")
ck("H2 default ON → no-membership verified user is 401", c.status == 401)
ck("H2 default ON → byte-identical 401 body", c.body == GOLDEN_401_BODY)
ck("H2 default ON → never reached the app", c.reached_app is False)
ck("H2 default ON → zero DB writes", c.writes == [])
# NEGATIVE CONTROL: STRICT_MEMBERSHIP=0 restores the pre-H2 pass-through (the attack works on base).
fresh(STRICT_MEMBERSHIP="0")
c = run_request(token="GOOD", rows=[], query=b"org_id=OTHER-TENANT")
ck("H2 break-glass OFF → old pass-through reaches the app (client org honored) = base behaviour",
   c.reached_app is True and c.app_scope["query_string"] == b"org_id=OTHER-TENANT")
# A provisioned member is unaffected either way.
fresh()
c = run_request(token="GOOD", rows=MEMBER, query=b"org_id=SOMETHING-ELSE")
ck("H2 default ON → a provisioned member still passes and org_id is rewritten to their tenant",
   c.reached_app is True and c.app_scope["query_string"] == b"org_id=ORG-A")

print("\n%s: %d passed, %d failed" % ("PASS" if F == 0 else "FAIL", P, F))
sys.exit(1 if F else 0)
