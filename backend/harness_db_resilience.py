"""Proof harness for the DB connection-resilience layer (agent/platform-core/db-conn-resilience).

Target of the fix: the ~50+ `httpx.RemoteProtocolError: Server disconnected` /
`<ConnectionTerminated …>` rows in core.failure_log (7/17→7/29) across EVERY module — stale idle
connections on the process-wide singleton's per-schema httpx pools.

Run offline from backend/:  python3 harness_db_resilience.py
No Supabase is contacted. Sections C–G run REAL HTTP against a local throwaway socket server, so
the postgrest → httpx → httpcore → socket path is exercised end to end.

  A. VERSION EVIDENCE — pins the root-cause claims to the INSTALLED sources, so a dependency bump
     that invalidates the diagnosis fails loudly here instead of silently in production.
  B. CLIENT SHAPE — HTTP/1.1, pinned+tunable limits/timeout, retry wrapper installed on every pool
     (incl. the default/public one), env overrides honoured.
  C. WIRE PARITY — the bytes a request puts on the wire are IDENTICAL to the stock supabase/
     postgrest construction path (zero behavioural change for successful requests).
  D. PREVENTION — a server that closes an idle keep-alive connection can no longer poison the next
     request (httpcore's HTTP/1.1 readable probe prunes it); and healthy keep-alive REUSE is
     preserved (the a9512c1 latency win is not given back).
  E. READ RETRY — a dead socket on a GET is replayed exactly ONCE, on a fresh connection, and the
     caller gets real parsed data through postgrest `.execute()`. Never twice.
  F. WRITE SAFETY — POST/PATCH/PUT/DELETE (incl. `POST /rpc/*`) are NEVER replayed; the caller gets
     a clean 503 DatabaseUnavailable naming the risk, not a masked 500.
  G. NON-TARGETS — timeouts and fresh-connect failures are not retried; body-read failures are out
     of scope by design; a read whose retry also fails surfaces the same clean 503.
  H. COUNTERS — the ops counters are accurate and thread-safe.
"""
from __future__ import annotations

import inspect
import os
import socket
import sys
import threading
import time
from typing import Dict, List, Optional

sys.path.insert(0, ".")

os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

import httpx                                              # noqa: E402
import httpcore                                           # noqa: E402
import postgrest                                          # noqa: E402
import supabase                                           # noqa: E402
from postgrest import SyncPostgrestClient                 # noqa: E402
from postgrest._sync import request_builder as pg_rb       # noqa: E402
from postgrest import base_request_builder as pg_base      # noqa: E402
from httpcore._sync import http11 as hc_http11            # noqa: E402
from httpcore._sync import http2 as hc_http2              # noqa: E402

from app.core import database as db                        # noqa: E402
from app.core import db_resilience as dbr                  # noqa: E402

PASS: List[str] = []
FAIL: List[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    return bool(cond)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# A. VERSION EVIDENCE — the diagnosis, asserted against the installed code
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── A. version evidence (installed stack) ──")
import importlib.metadata as _md  # noqa: E402

VERS = {p: _md.version(p) for p in ("supabase", "postgrest", "httpx", "httpcore", "h2")}
print("   " + "  ".join(f"{k}={v}" for k, v in VERS.items()))
check("A1. pinned stack is the one the diagnosis was read from",
      VERS == {"supabase": "2.31.0", "postgrest": "2.31.0", "httpx": "0.28.1",
               "httpcore": "1.0.9", "h2": "4.3.0"},
      f"got {VERS} — RE-READ the sources before trusting this package's root cause")

# A2/A3: postgrest's send_with_retry cannot save a transport exception.
swr_src = inspect.getsource(pg_rb.send_with_retry)
check("A2. postgrest send_with_retry has NO try/except around req.send()",
      "except" not in swr_src and "try:" not in swr_src, swr_src)
sr_src = inspect.getsource(pg_base.RequestConfig.should_retry)
check("A3. should_retry only fires on a RETURNED 503/520 response",
      "response.status_code == 503" in sr_src and "520" in sr_src)
check("A4. …and its verb guard says 'HTTP' (typo for HEAD) so HEAD is never retried either",
      'self.http_method == "HTTP"' in sr_src)
send_src = inspect.getsource(pg_base.RequestConfig.send)
check("A5. RequestConfig.send is a bare session.request() — exception escapes .execute()",
      "try" not in send_src and "self.session.request(" in send_src)

# A6/A7: THE root cause — httpcore probes idle HTTP/1.1 sockets, but not HTTP/2 ones.
h11_exp = inspect.getsource(hc_http11.HTTP11Connection.has_expired)
h2_exp = inspect.getsource(hc_http2.HTTP2Connection.has_expired)
check("A6. httpcore HTTP/1.1 has_expired() PROBES the idle socket (is_readable)",
      "is_readable" in h11_exp and "server_disconnected" in h11_exp)
check("A7. httpcore HTTP/2 has_expired() does NOT probe — dead idle h2 sockets get reused",
      "is_readable" not in h2_exp, h2_exp)
h2_avail = inspect.getsource(hc_http2.HTTP2Connection.is_available)
h11_avail = inspect.getsource(hc_http11.HTTP11Connection.is_available)
check("A8. h2 is_available() is True while ACTIVE (multiplex ⇒ one dead socket fails N requests)",
      "IDLE" not in h2_avail and "CLOSED" in h2_avail)
check("A9. h1 is_available() requires IDLE (⇒ one dead socket fails at most 1 request)",
      "self._state == HTTPConnectionState.IDLE" in h11_avail)
h2_read = inspect.getsource(hc_http2.HTTP2Connection._read_incoming_data)
check("A10. h2 stores the first read error and re-raises it for every later read on that socket",
      "_read_exception" in h2_read and 'RemoteProtocolError("Server disconnected")' in h2_read)
pg_client_src = inspect.getsource(SyncPostgrestClient.__init__)
check("A11. stock postgrest builds EVERY pool with http2=True (what we override)",
      "http2=True" in pg_client_src)
check("A12. postgrest .schema() does NOT forward http_client= (so database.py must build pools)",
      "http_client" not in inspect.getsource(SyncPostgrestClient.schema))
check("A13. postgrest honours an injected http_client verbatim",
      "self.session = http_client or Client(" in pg_client_src)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# B. CLIENT SHAPE
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── B. built client shape ──")


def _pool_of(client: httpx.Client):
    t = client._transport
    inner = getattr(t, "_inner", t)
    return getattr(inner, "_pool", None)


c = dbr.build_pool_client("http://127.0.0.1:1/rest/v1")
check("B1. transport is the retry wrapper", isinstance(c._transport, dbr.RetryOnDisconnectTransport))
check("B2. wrapped transport is a real httpx.HTTPTransport",
      isinstance(c._transport.inner, httpx.HTTPTransport))
pool = _pool_of(c)
check("B3. pool is HTTP/1.1 only (h2 off)", pool._http1 is True and pool._http2 is False,
      f"http1={pool._http1} http2={pool._http2}")
check("B4. keepalive_expiry pinned to 5.0s (today's httpx default, no silent inheritance)",
      pool._keepalive_expiry == 5.0, str(pool._keepalive_expiry))
check("B5. max_connections 100 (unchanged)", pool._max_connections == 100)
check("B6. max_keepalive 40 (one socket per concurrent query now h2 no longer multiplexes)",
      pool._max_keepalive_connections == 40)
check("B7. timeout 120s on all four axes == postgrest's DEFAULT_POSTGREST_CLIENT_TIMEOUT",
      c.timeout.connect == 120.0 and c.timeout.read == 120.0
      and c.timeout.write == 120.0 and c.timeout.pool == 120.0, str(c.timeout))
check("B8. follow_redirects=True (postgrest parity)", c.follow_redirects is True)
check("B9. retries=0 on the inner transport (no new connect-retry behaviour)",
      pool._retries == 0)
c.close()

# env overrides
os.environ["SUPABASE_KEEPALIVE_EXPIRY"] = "12.5"
os.environ["SUPABASE_MAX_KEEPALIVE"] = "7"
os.environ["SUPABASE_HTTP_TIMEOUT"] = "31"
os.environ["SUPABASE_HTTP2"] = "1"
c2 = dbr.build_pool_client("http://127.0.0.1:1/rest/v1")
p2 = _pool_of(c2)
check("B10. env override: keepalive_expiry", p2._keepalive_expiry == 12.5)
check("B11. env override: max_keepalive", p2._max_keepalive_connections == 7)
check("B12. env override: timeout", c2.timeout.read == 31.0)
check("B13. env override: SUPABASE_HTTP2=1 re-enables h2 (diagnosis escape hatch, reversible)",
      p2._http2 is True)
c2.close()
os.environ["SUPABASE_KEEPALIVE_EXPIRY"] = "not-a-number"
c3 = dbr.build_pool_client("http://127.0.0.1:1/rest/v1")
check("B14. a garbage env value falls back to the default instead of crashing boot",
      _pool_of(c3)._keepalive_expiry == 5.0)
c3.close()
for k in ("SUPABASE_KEEPALIVE_EXPIRY", "SUPABASE_MAX_KEEPALIVE", "SUPABASE_HTTP_TIMEOUT",
          "SUPABASE_HTTP2"):
    os.environ.pop(k, None)

# every pool of the real singleton, including the default/public one
sing = db.get_supabase()
wrapped = {s: isinstance(sing.schema(s).session._transport, dbr.RetryOnDisconnectTransport)
           for s in ("commcalc", "storeops", "core", "notify")}
check("B15. every schema pool used by the app is resilient", all(wrapped.values()), str(wrapped))
check("B16. the DEFAULT/public pool (client.table/.rpc) is resilient too",
      isinstance(sing.postgrest.session._transport, dbr.RetryOnDisconnectTransport))
check("B17. default pool is the memoized 'public' entry (one pool, not two)",
      sing.postgrest is sing.schema("public"))
check("B18. singleton is still a SINGLETON (a9512c1 not reverted)",
      db.get_supabase() is sing and db.get_supabase_admin() is sing)
h2_pools = [s for s in ("commcalc", "storeops", "core", "notify", "public")
            if _pool_of(sing.schema(s).session)._http2]
check("B19. no app pool is left on HTTP/2", not h2_pools, str(h2_pools))
distinct_sessions = len({id(sing.schema(s).session) for s in
                         ("commcalc", "storeops", "core", "notify", "public")})
check("B20. one independent pool per schema (5 schemas ⇒ 5 sessions)", distinct_sessions == 5,
      str(distinct_sessions))


# ════════════════════════════════════════════════════════════════════════════════════════════════
#     local throwaway HTTP/1.1 server (sections C–G)
# ════════════════════════════════════════════════════════════════════════════════════════════════
class TinyServer:
    """Minimal HTTP/1.1 server with scriptable keep-alive behaviour.

    Records every raw request (bytes) and counts accepted TCP connections, so 'was a NEW connection
    opened?' is answered by the server, not by introspecting the client.
    """

    def __init__(self, *, close_after: Optional[int] = None, status: int = 200,
                 body: bytes = b'[{"id":1,"v":"ok"}]', delay: float = 0.0) -> None:
        self.close_after = close_after     # close the socket after this many responses on it
        self.status = status
        self.body = body
        self.delay = delay                 # slow responses, so concurrent requests really overlap
        self.requests: List[bytes] = []
        self.connections = 0
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._t = threading.Thread(target=self._accept_loop, daemon=True)
        self._t.start()

    @property
    def rest_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/rest/v1"

    def _accept_loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with self._lock:
                self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        served = 0
        try:
            buf = b""
            while not self._stop:
                while b"\r\n\r\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                head, _, rest = buf.partition(b"\r\n\r\n")
                clen = 0
                for line in head.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-length:"):
                        clen = int(line.split(b":", 1)[1].strip())
                while len(rest) < clen:
                    rest += conn.recv(65536)
                with self._lock:
                    self.requests.append(head + b"\r\n\r\n" + rest[:clen])
                buf = rest[clen:]
                if self.delay:
                    time.sleep(self.delay)
                conn.sendall(
                    b"HTTP/1.1 %d OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\nConnection: keep-alive\r\n\r\n%s"
                    % (self.status, len(self.body), self.body)
                )
                served += 1
                if self.close_after is not None and served >= self.close_after:
                    time.sleep(0.03)            # let the client finish reading + pool the socket
                    conn.close()                # server-initiated close of an IDLE keep-alive conn
                    return
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass


def pg_for(server: TinyServer, schema: str = "commcalc") -> SyncPostgrestClient:
    """A postgrest client on our resilient pool, pointed at the local server."""
    pg = SyncPostgrestClient(
        server.rest_url, schema=schema,
        headers={"apiKey": "harness-key", "Authorization": "Bearer harness-key"},
        http_client=dbr.build_pool_client(server.rest_url),
    )
    pg.session.headers.update(pg.headers)
    return pg


# ════════════════════════════════════════════════════════════════════════════════════════════════
# C. WIRE PARITY — stock construction vs ours, byte for byte
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── C. wire parity vs the stock stack ──")
srv_stock, srv_ours = TinyServer(), TinyServer()

stock_client = supabase.Client.create(f"http://127.0.0.1:{srv_stock.port}", "harness-key")
ours_client = db._SchemaCachingClient.create(f"http://127.0.0.1:{srv_ours.port}", "harness-key")


def exercise(client, port_srv: TinyServer) -> None:
    sc = client.schema("commcalc")
    sc.table("rep_commissions").select("id,amount").eq("org_id", "org-1").limit(2).execute()
    sc.table("flags").insert({"a": 1, "b": "x"}).execute()
    client.table("app_users").select("role").eq("email", "a@b.c").execute()
    client.schema("storeops").table("stores").select("*").order("address").execute()


exercise(stock_client, srv_stock)
exercise(ours_client, srv_ours)


def normalise(raw: bytes) -> bytes:
    """Drop only genuinely per-connection/per-run headers, keep everything semantic."""
    out = []
    for line in raw.split(b"\r\n"):
        low = line.lower()
        if low.startswith(b"host:"):        # differs only by the throwaway port
            continue
        out.append(line)
    return b"\r\n".join(out)


stock_reqs = [normalise(r) for r in srv_stock.requests]
ours_reqs = [normalise(r) for r in srv_ours.requests]
check("C1. same number of HTTP requests emitted", len(stock_reqs) == len(ours_reqs) == 4,
      f"stock={len(stock_reqs)} ours={len(ours_reqs)}")
diffs = [i for i, (a, b) in enumerate(zip(stock_reqs, ours_reqs)) if a != b]
if diffs:
    i = diffs[0]
    print("   STOCK:", stock_reqs[i][:600])
    print("   OURS :", ours_reqs[i][:600])
check("C2. every request byte-identical to the stock path (method, URL, ALL headers, body)",
      not diffs, f"differing request indexes {diffs}")
check("C3. stock path also negotiated HTTP/1.1 here (apples-to-apples comparison)",
      all(r.split(b"\r\n")[0].endswith(b"HTTP/1.1") for r in stock_reqs))
check("C4. Accept-Profile still carries the requested schema",
      b"accept-profile: commcalc" in ours_reqs[0].lower()
      and b"accept-profile: storeops" in ours_reqs[3].lower())
check("C5. auth headers still present on every request",
      all(b"authorization: bearer harness-key" in r.lower() for r in ours_reqs))
check("C6. default/public path still says public", b"accept-profile: public" in ours_reqs[2].lower())

# ── C(bis). the DB_RESILIENCE_DISABLE kill switch is a TRUE revert to stock ──────────────────────
os.environ["DB_RESILIENCE_DISABLE"] = "1"
check("C7. kill switch: build_pool_client() returns None (postgrest builds its own pool)",
      dbr.build_pool_client("http://127.0.0.1:1/rest/v1") is None)
srv_off = TinyServer()
off_client = db._SchemaCachingClient.create(f"http://127.0.0.1:{srv_off.port}", "harness-key")
exercise(off_client, srv_off)
off_reqs = [normalise(r) for r in srv_off.requests]
check("C8. kill switch: requests still byte-identical to stock", off_reqs == stock_reqs,
      f"{len(off_reqs)} vs {len(stock_reqs)}")
off_pg = off_client.schema("commcalc")
check("C9. kill switch: the pool is the STOCK one again (h2 on, no retry wrapper)",
      _pool_of(off_pg.session)._http2 is True
      and not isinstance(off_pg.session._transport, dbr.RetryOnDisconnectTransport))
check("C10. kill switch: stock 120s timeout preserved (NOT httpx's no-timeout None)",
      off_pg.session.timeout.read == 120.0, str(off_pg.session.timeout))
srv_off.close()
os.environ.pop("DB_RESILIENCE_DISABLE", None)
check("C11. knob is OFF by default — the fix is live without any env var",
      dbr.resilience_enabled() is True and dbr.http2_enabled() is False)

srv_stock.close()
srv_ours.close()


# ════════════════════════════════════════════════════════════════════════════════════════════════
# D. PREVENTION — a dead idle socket no longer poisons the next request; reuse still works
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── D. prevention (idle-socket probe) + keep-alive reuse ──")
dbr.reset_pool_stats()
srv = TinyServer(close_after=1)                 # server hangs up after every single response
pg = pg_for(srv)
r1 = pg.table("t").select("*").execute()
time.sleep(0.25)                                # the FIN has landed; socket is pooled-and-dead
r2 = pg.table("t").select("*").execute()
after_settled = dbr.pool_stats()
r3 = pg.table("t").select("*").execute()        # re-fired IMMEDIATELY: the FIN may not have landed
st = dbr.pool_stats()
check("D1. request after a server-side idle close SUCCEEDS", r2.data == [{"id": 1, "v": "ok"}])
check("D2. …with ZERO retries — httpcore's HTTP/1.1 readable probe pruned the dead socket, which "
      "is exactly what the HTTP/2 pool could not do (A7)", after_settled["read_retried"] == 0,
      str(after_settled))
check("D3. a request re-fired before the FIN can land ALSO succeeds — the second layer (retry) "
      "covers the race no expiry value can close", r3.data == [{"id": 1, "v": "ok"}],
      f"stats={st}")
check("D4. the server accepted a fresh connection for each of the 3 requests", srv.connections == 3,
      f"connections={srv.connections}")
print(f"   (info: retries needed across the 3 requests = {st['read_retried']}; "
      f"recovered = {st['read_recovered']}, unrecovered = {st['read_retry_failed']})")
srv.close()
pg.session.close()

srv = TinyServer()                              # well-behaved keep-alive server
pg = pg_for(srv)
for _ in range(6):
    pg.table("t").select("*").execute()
check("D5. keep-alive REUSE preserved: 6 sequential queries share ONE connection "
      "(the a9512c1 latency win is intact)", srv.connections == 1, f"connections={srv.connections}")


srv.close()
pg.session.close()

# genuinely OVERLAPPING concurrency (slow server) — the same-second-cluster shape
srv = TinyServer(delay=0.20)
pg = pg_for(srv)
errs: List[BaseException] = []


def go():
    try:
        pg.table("t").select("*").execute()
    except BaseException as e:                   # noqa: BLE001
        errs.append(e)


ts = [threading.Thread(target=go) for _ in range(12)]
[t.start() for t in ts]
[t.join() for t in ts]
check("D6. 12 genuinely overlapping queries all succeed", not errs, repr(errs[:1]))
check("D7. …and they were spread over MORE THAN ONE socket, so a single dead socket can no longer "
      "take out every in-flight request (the same-second multi-endpoint cluster signature)",
      srv.connections > 1, f"connections={srv.connections} — expected ~12")
print(f"   (info: 12 overlapping queries used {srv.connections} sockets; under HTTP/2 all 12 would "
      f"share ONE, and A10 shows one read error there fails all of them)")
srv.close()
pg.session.close()


# ════════════════════════════════════════════════════════════════════════════════════════════════
#     instrumented inner transport (sections E–G)
# ════════════════════════════════════════════════════════════════════════════════════════════════
class Flaky(httpx.BaseTransport):
    """Wraps the REAL inner transport and raises `exc` on the chosen attempts.

    Instruments the layer BELOW the production wrapper, so the wrapper under test is the shipped
    one, unmodified, and a non-failed attempt is a genuine end-to-end HTTP request.
    """

    def __init__(self, inner: httpx.BaseTransport, fail_on: set, exc: BaseException,
                 shared: Optional[dict] = None) -> None:
        self._inner = inner
        self.fail_on = fail_on
        self.exc = exc
        # `shared` lets several pools count attempts on ONE sequence (multi-schema chains).
        self._c = shared if shared is not None else {"n": 0, "seen": []}
        self._lock = threading.Lock()

    @property
    def attempts(self) -> int:
        return self._c["n"]

    @property
    def seen(self) -> List[str]:
        return self._c["seen"]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self._c["n"] += 1
            n = self._c["n"]
            self._c["seen"].append(f"{request.method} {request.url.path}")
        if n in self.fail_on:
            raise self.exc
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def flaky_pg(server: TinyServer, fail_on: set, exc: BaseException,
             schema: str = "commcalc", shared: Optional[dict] = None) -> tuple:
    pg = pg_for(server, schema)
    wrapper = pg.session._transport
    flaky = Flaky(wrapper.inner, fail_on, exc, shared=shared)
    wrapper._inner = flaky
    return pg, flaky


DISCONNECT = httpx.RemoteProtocolError("Server disconnected")
GOAWAY = httpx.RemoteProtocolError("<ConnectionTerminated error_code:1, last_stream_id:237>")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# E. READ RETRY
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── E. read retry (GET/HEAD, exactly once) ──")
for label, exc in (("Server disconnected", DISCONNECT), ("h2 GOAWAY/ConnectionTerminated", GOAWAY)):
    dbr.reset_pool_stats()
    srv = TinyServer()
    pg, flaky = flaky_pg(srv, {1}, exc)
    ok, data, err = True, None, None
    try:
        data = pg.table("employees").select("id,name").eq("org_id", "o1").execute().data
    except BaseException as e:                                     # noqa: BLE001
        ok, err = False, e
    st = dbr.pool_stats()
    check(f"E1[{label}]. the read SUCCEEDS through postgrest .execute()", ok, repr(err))
    check(f"E2[{label}]. and returns real parsed data", data == [{"id": 1, "v": "ok"}], repr(data))
    check(f"E3[{label}]. exactly 2 transport attempts = ONE retry", flaky.attempts == 2,
          str(flaky.attempts))
    check(f"E4[{label}]. counters say retried=1 recovered=1 failed=0 write=0",
          (st["read_retried"], st["read_recovered"], st["read_retry_failed"],
           st["write_not_retried"]) == (1, 1, 0, 0), str(st))
    check(f"E5[{label}]. the replay reused the SAME verb+path", flaky.seen[0] == flaky.seen[1],
          str(flaky.seen))
    srv.close()
    pg.session.close()

# retry happens on a FRESH connection, and puts the SAME bytes on the wire as a normal request
dbr.reset_pool_stats()
srv = TinyServer()
pg, flaky = flaky_pg(srv, {1}, DISCONNECT)
pg.table("t").select("*").eq("org_id", "o1").execute()
check("E6. the retry ran on a live socket (server served the replay)", len(srv.requests) == 1,
      f"server requests={len(srv.requests)}")
replay_bytes = srv.requests[0]
srv.close()
pg.session.close()

srv2 = TinyServer()
pg2 = pg_for(srv2)
pg2.table("t").select("*").eq("org_id", "o1").execute()
control_bytes = srv2.requests[0]


def strip_host(raw: bytes) -> bytes:
    return b"\r\n".join(l for l in raw.split(b"\r\n") if not l.lower().startswith(b"host:"))


check("E13. the replayed request is BYTE-IDENTICAL to a normal one (no marker header, no lost "
      "body, no altered query)", strip_host(replay_bytes) == strip_host(control_bytes),
      f"replay={replay_bytes[:200]!r} control={control_bytes[:200]!r}")
srv2.close()
pg2.session.close()

# never a SECOND retry
dbr.reset_pool_stats()
srv = TinyServer()
pg, flaky = flaky_pg(srv, {1, 2, 3, 4}, DISCONNECT)
raised = None
try:
    pg.table("t").select("*").execute()
except BaseException as e:                                          # noqa: BLE001
    raised = e
st = dbr.pool_stats()
check("E7. a read is retried ONCE, never twice (attempts == 2)", flaky.attempts == 2,
      str(flaky.attempts))
check("E8. an unrecoverable read raises DatabaseUnavailable(503)",
      isinstance(raised, dbr.DatabaseUnavailable) and raised.status_code == 503, repr(raised))
check("E9. …flagged as retried, with the original error chained",
      getattr(raised, "retried", None) is True and isinstance(raised.__cause__,
                                                              httpx.RemoteProtocolError))
check("E10. …and counted as read_retry_failed", st["read_retry_failed"] == 1, str(st))
srv.close()
pg.session.close()

# HEAD is a read too (postgrest's own retry gate misses HEAD — ours does not)
dbr.reset_pool_stats()
srv = TinyServer()
pg, flaky = flaky_pg(srv, {1}, DISCONNECT)
ok = True
try:
    pg.session.head(f"{srv.rest_url}/t")
except BaseException:                                               # noqa: BLE001
    ok = False
check("E11. HEAD is retried too", ok and flaky.attempts == 2, f"ok={ok} attempts={flaky.attempts}")
srv.close()
pg.session.close()

# a read-only RPC opted in with get=True is a real GET ⇒ retryable
dbr.reset_pool_stats()
srv = TinyServer()
pg, flaky = flaky_pg(srv, {1}, DISCONNECT)
ok = True
try:
    pg.rpc("asset_charges_summary", {"p_org": "o1"}, get=True).execute()
except BaseException:                                               # noqa: BLE001
    ok = False
check("E12. `.rpc(..., get=True)` (read-only RPC) IS retried — the documented opt-in",
      ok and flaky.attempts == 2, f"ok={ok} attempts={flaky.attempts}")
srv.close()
pg.session.close()


# ════════════════════════════════════════════════════════════════════════════════════════════════
# F. WRITE SAFETY
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── F. write safety (never auto-retried) ──")
WRITES = [
    ("insert (POST)", lambda p: p.table("shifts").insert({"a": 1}).execute()),
    ("upsert (POST)", lambda p: p.table("shifts").upsert({"a": 1}).execute()),
    ("update (PATCH)", lambda p: p.table("shifts").update({"a": 1}).eq("id", 1).execute()),
    ("delete (DELETE)", lambda p: p.table("shifts").delete().eq("id", 1).execute()),
    ("rpc (POST /rpc/*)", lambda p: p.rpc("seed_tenant_defaults", {"p": 1}).execute()),
]
for label, call in WRITES:
    dbr.reset_pool_stats()
    srv = TinyServer()
    pg, flaky = flaky_pg(srv, {1}, DISCONNECT)
    raised = None
    try:
        call(pg)
    except BaseException as e:                                      # noqa: BLE001
        raised = e
    st = dbr.pool_stats()
    check(f"F1[{label}]. NOT retried — exactly one transport attempt", flaky.attempts == 1,
          str(flaky.attempts))
    check(f"F2[{label}]. nothing reached the server on the failed attempt",
          len(srv.requests) == 0, f"server requests={len(srv.requests)}")
    check(f"F3[{label}]. surfaces DatabaseUnavailable → HTTP 503 (not a masked 500)",
          isinstance(raised, dbr.DatabaseUnavailable) and raised.status_code == 503, repr(raised))
    check(f"F4[{label}]. message says it was NOT retried + may already have applied",
          "NOT retried" in raised.detail and "may already have been applied" in raised.detail,
          getattr(raised, "detail", ""))
    check(f"F5[{label}]. counted as write_not_retried, read counters untouched",
          (st["write_not_retried"], st["read_retried"]) == (1, 0), str(st))
    srv.close()
    pg.session.close()

from fastapi import HTTPException  # noqa: E402

exc = dbr.DatabaseUnavailable("POST", "/rest/v1/shifts",
                              httpx.RemoteProtocolError("x"), retried=False)
check("F6. DatabaseUnavailable IS a fastapi HTTPException (Starlette returns a clean 503 body; "
      "it is NOT reported as an unhandled crash)", isinstance(exc, HTTPException))
check("F7. carries Retry-After", (exc.headers or {}).get("Retry-After") == "1")
check("F8. detail is a plain string (frontend `data.detail` contract unchanged)",
      isinstance(exc.detail, str))
leaky = dbr.DatabaseUnavailable(
    "GET", httpx.URL("http://h/rest/v1/employees?org_id=secret-org&email=a@b.c").path,
    httpx.RemoteProtocolError("x"), retried=True)
check("F9. no query string in the surfaced message (org_id/email never leak into an error or log)",
      "secret-org" not in leaky.detail and "a@b.c" not in leaky.detail
      and "/rest/v1/employees" in leaky.detail, leaky.detail)
check("F10. still swallowed by the app's best-effort `except Exception` guards (no new crash class)",
      isinstance(exc, Exception))


# ════════════════════════════════════════════════════════════════════════════════════════════════
# G. NON-TARGETS + healthy path untouched
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── G. deliberate non-targets ──")
NON_RETRY = [
    ("ReadTimeout (server may still be working)", httpx.ReadTimeout("timeout")),
    ("ConnectTimeout", httpx.ConnectTimeout("timeout")),
    ("PoolTimeout", httpx.PoolTimeout("timeout")),
    ("ConnectError (fresh connect failed = real outage)", httpx.ConnectError("refused")),
    ("LocalProtocolError (our own bug)", httpx.LocalProtocolError("bad request")),
]
for label, exc_obj in NON_RETRY:
    dbr.reset_pool_stats()
    srv = TinyServer()
    pg, flaky = flaky_pg(srv, {1}, exc_obj)
    raised = None
    try:
        pg.table("t").select("*").execute()
    except BaseException as e:                                      # noqa: BLE001
        raised = e
    check(f"G1[{label}]. not retried", flaky.attempts == 1, str(flaky.attempts))
    check(f"G2[{label}]. propagates unchanged (same exception object)", raised is exc_obj,
          repr(raised))
    srv.close()
    pg.session.close()

for label, exc_obj in (("ReadError", httpx.ReadError("reset")), ("WriteError", httpx.WriteError("epipe"))):
    dbr.reset_pool_stats()
    srv = TinyServer()
    pg, flaky = flaky_pg(srv, {1}, exc_obj)
    ok = True
    try:
        pg.table("t").select("*").execute()
    except BaseException:                                           # noqa: BLE001
        ok = False
    check(f"G3[{label}]. dead-socket errors on a READ are retried", ok and flaky.attempts == 2,
          f"ok={ok} attempts={flaky.attempts}")
    srv.close()
    pg.session.close()

# healthy path: zero extra attempts, zero counter movement, identical payload
dbr.reset_pool_stats()
srv = TinyServer()
pg, flaky = flaky_pg(srv, set(), DISCONNECT)
res = pg.table("t").select("*").execute()
st = dbr.pool_stats()
check("G4. healthy request: exactly ONE transport attempt", flaky.attempts == 1, str(flaky.attempts))
check("G5. healthy request: payload unchanged", res.data == [{"id": 1, "v": "ok"}])
check("G6. healthy request: no retry/failure counters move",
      (st["read_retried"], st["read_recovered"], st["read_retry_failed"],
       st["write_not_retried"]) == (0, 0, 0, 0), str(st))
check("G7. healthy request: counted once", st["requests"] == 1, str(st))
srv.close()
pg.session.close()

# a postgrest APIError (a real 4xx/5xx from PostgREST) still behaves exactly as before
srv = TinyServer(status=400, body=b'{"message":"boom","code":"42P01","hint":null,"details":null}')
pg = pg_for(srv)
raised = None
try:
    pg.table("t").select("*").execute()
except BaseException as e:                                          # noqa: BLE001
    raised = e
check("G8. a PostgREST error response still raises postgrest APIError, NOT our 503",
      isinstance(raised, postgrest.exceptions.APIError)
      and not isinstance(raised, dbr.DatabaseUnavailable), repr(raised))
srv.close()
pg.session.close()

check("G9. body-read failures are OUT of scope by design and documented as such",
      "part-way through an already-started response body" in dbr.__doc__)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# H. COUNTERS
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── H. counters ──")
dbr.reset_pool_stats()
srv = TinyServer()
pg = pg_for(srv)
wrapper = pg.session._transport
flaky = Flaky(wrapper.inner, set(), DISCONNECT)
wrapper._inner = flaky
N, PER = 8, 25


def hammer():
    for _ in range(PER):
        pg.table("t").select("*").execute()


ts = [threading.Thread(target=hammer) for _ in range(N)]
[t.start() for t in ts]
[t.join() for t in ts]
st = dbr.pool_stats()
check("H1. request counter exact under 8×25 concurrent requests", st["requests"] == N * PER,
      str(st))
check("H2. pool_stats() returns a copy (callers cannot corrupt the counters)",
      dbr.pool_stats() is not dbr.pool_stats())
snap = dbr.pool_stats()
snap["requests"] = -1
check("H3. …mutating the copy does not affect the real counters",
      dbr.pool_stats()["requests"] == N * PER)
srv.close()
pg.session.close()

# ════════════════════════════════════════════════════════════════════════════════════════════════
# J. THE BIGGEST SIGNATURE — a long sequential read chain (core/employee-dashboard, ×13)
#    ~10 .execute() round trips per request across two schemas; the crash was always at the k-th
#    draw (core/router.py:3481). More draws per request = more chances to be handed a dead socket.
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── J. long sequential read chain (the employee-dashboard shape) ──")
for dead_attempt in (1, 6, 10):
    dbr.reset_pool_stats()
    srv = TinyServer()
    # the chain spans two schemas ⇒ two independent pools; ONE shared attempt counter across both,
    # so "dead on draw 6" really is the 6th draw of the chain
    shared_counter: dict = {"n": 0, "seen": []}
    pg, flaky = flaky_pg(srv, {dead_attempt}, DISCONNECT, "commcalc", shared_counter)
    pg_store, _ = flaky_pg(srv, {dead_attempt}, DISCONNECT, "storeops", shared_counter)
    rows, err = [], None
    try:
        for i in range(10):
            client = pg if i % 2 == 0 else pg_store
            rows.append(client.table(f"t{i}").select("*").execute().data)
    except BaseException as e:                                      # noqa: BLE001
        err = e
    st = dbr.pool_stats()
    check(f"J1[dead on draw {dead_attempt}]. the whole 10-draw chain completes", err is None,
          repr(err))
    check(f"J2[dead on draw {dead_attempt}]. all 10 payloads intact",
          rows == [[{"id": 1, "v": "ok"}]] * 10, str(rows)[:160])
    check(f"J3[dead on draw {dead_attempt}]. exactly one retry for the whole chain",
          st["read_retried"] == 1 and st["read_recovered"] == 1, str(st))
    srv.close()
    pg.session.close()
    pg_store.session.close()


# ════════════════════════════════════════════════════════════════════════════════════════════════
# I. IN-PROCESS HTTP — what a real caller actually receives, through the REAL middleware stack
# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── I. end-to-end through app.main's HardeningMiddleware ──")
from fastapi import FastAPI                        # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402
from app.main import app as real_app, HardeningMiddleware  # noqa: E402

check("I0. app.main still imports and exposes 906 routes (unchanged vs base b54a3f3)",
      len(real_app.routes) == 906, str(len(real_app.routes)))

srv = TinyServer()
probe = FastAPI()
probe.add_middleware(HardeningMiddleware)          # the REAL masking middleware from app/main.py
_pg_read, _flaky_read = flaky_pg(srv, {1}, DISCONNECT)
_pg_write, _flaky_write = flaky_pg(srv, {1}, DISCONNECT)


@probe.get("/read")
def _read():
    return {"rows": _pg_read.table("employees").select("*").execute().data}


@probe.post("/write")
def _write():
    return {"rows": _pg_write.table("shifts").insert({"a": 1}).execute().data}


@probe.get("/boom")
def _boom():
    raise RuntimeError("a genuine unhandled crash")


tc = TestClient(probe, raise_server_exceptions=False)
r = tc.get("/read")
check("I1. a read that hit a dead socket returns 200 with data — the user never sees the incident",
      r.status_code == 200 and r.json() == {"rows": [{"id": 1, "v": "ok"}]},
      f"{r.status_code} {r.text[:200]}")
r = tc.post("/write")
check("I2. a write that hit a dead socket returns 503 (was: masked 500)", r.status_code == 503,
      f"{r.status_code} {r.text[:200]}")
detail = (r.json() or {}).get("detail", "")
check("I3. …with an actionable message, NOT 'A system error occurred. Reference: …'",
      "NOT retried" in detail and "may already have been applied" in detail
      and "system error occurred" not in detail, detail)
check("I4. …and Retry-After is set", r.headers.get("retry-after") == "1", str(dict(r.headers)))
check("I5. …security headers still stamped (middleware chain intact)",
      r.headers.get("x-content-type-options") == "nosniff")
r = tc.get("/boom")
check("I6. a GENUINE unhandled crash is still masked as a 500 with a ref (masking not broken)",
      r.status_code == 500 and "A system error occurred" in (r.json() or {}).get("detail", ""),
      f"{r.status_code} {r.text[:200]}")
srv.close()

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
