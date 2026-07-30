"""Supabase client access — SINGLETON (P0 perf, 2026-07-22).

get_supabase()/get_supabase_admin() used to call create_client() on EVERY invocation, and every
router does `sb().table(...)` per query — so EVERY DB query paid full client construction plus a
fresh TCP/TLS handshake. Measured live in prod: /health ≈ 100ms vs /core/auth-config (exactly one
indexed read) ≈ 210–230ms → ~110ms pure overhead per query. Both functions now return one shared,
lazily-built, process-wide client. Signatures and return type are unchanged: zero call sites change.

THREAD-SAFETY — verified by reading the INSTALLED pinned stack (supabase 2.31.0 / postgrest 2.31.0 /
supabase-auth 2.31.0 / httpx 0.28.1; requirements.txt floors `supabase>=2.9.0` resolve to this line).
The app runs sync `def` endpoints on uvicorn's threadpool, so ONE client is used from many threads:

  • `Client.schema(name)` (supabase/_sync/client.py) delegates to `SyncPostgrestClient.schema(name)`
    (postgrest/_sync/client.py) which returns a **NEW** SyncPostgrestClient — it never mutates the
    parent, so concurrent `.schema()` calls cannot cross-contaminate schemas.
  • BUT each new SyncPostgrestClient constructs its OWN `httpx.Client` — an un-cached `.schema()`
    would still pay a fresh TLS handshake per query (and leak an unclosed pool). So we memoize ONE
    SyncPostgrestClient per schema name (`_SchemaCachingClient` below = the "per-schema cached
    clients" design), giving each schema a single reused httpx connection pool.
  • Request builders don't touch shared state: `.table()/.from_()/.rpc()` create a fresh builder
    whose per-request Headers object is NEW and merges the client-level headers INTO it
    (`headers.update(self.headers)` in postgrest request_builder); later mutations (Prefer/Accept…)
    hit only that per-request copy. Client-level headers are written only by postgrest's `.auth()`
    and supabase's `_listen_to_auth_events` — neither ever fires here: we use the service key, the
    app never calls postgrest `.auth()` nor `auth.sign_in*/set_session/sign_out` (grep-verified),
    and `auth.get_user(jwt)` / `auth.admin.*` build per-request header dicts (stateless).
  • `httpx.Client` is documented thread-safe for concurrent requests (shared connection pool).

Proven by backend/harness_db_singleton.py: concurrent `.schema()` usage from many threads keeps
Accept-Profile/Content-Profile disjoint per schema and per-request header mutations isolated.

CONNECTION RESILIENCE (2026-07-30) — the singleton STAYS a singleton
-------------------------------------------------------------------
The singleton above is correct and is NOT being reverted (client-per-call was the original latency
bug). What it lacked was a healthy connection pool: postgrest builds each pool with `http2=True`,
and httpcore's HTTP/2 pool — unlike its HTTP/1.1 pool — never probes an idle socket before reusing
it, and multiplexes every concurrent request onto that one socket. A Supabase-side idle close
therefore came back as `httpx.RemoteProtocolError: Server disconnected` (or an h2 GOAWAY,
`<ConnectionTerminated error_code:1, last_stream_id:237>`) for EVERY request in flight at once —
~50+ failure_log rows across every module, 7/17→7/29. postgrest's own `send_with_retry` cannot help:
it only inspects a returned 503/520 Response and never catches a transport exception.

So every per-schema postgrest client is now built with an INJECTED httpx client from
`app.core.db_resilience` (postgrest's supported `http_client=` parameter): HTTP/1.1 (so httpcore's
idle-socket readable probe applies and a dead socket is pruned instead of handed out), pinned +
env-tunable pool limits/timeouts identical to today's effective values, and a transport wrapper
that replays a GET/HEAD exactly once on a fresh connection while NEVER replaying a write (a
disconnect mid-write may have committed) — an unrecoverable request raises a clean 503
`DatabaseUnavailable` instead of a masked 500. Full mechanics + version evidence live in
`app/core/db_resilience.py`; proofs in `backend/harness_db_resilience.py`.

Because the injected client must reach EVERY pool, `.schema()` builds its `SyncPostgrestClient`
directly here (postgrest's own `.schema()` does not forward `http_client=`), and the default/public
pool (`client.postgrest`, `client.table()`, `client.rpc()`) is routed through the same memo.
"""
import threading
from typing import Dict, Optional

from supabase import create_client, Client  # create_client kept importable for legacy importers
from postgrest import SyncPostgrestClient
from postgrest.constants import DEFAULT_POSTGREST_CLIENT_TIMEOUT

from app.core.config import settings
from app.core.db_resilience import build_pool_client

_lock = threading.Lock()
_client: Optional[Client] = None


class _SchemaCachingClient(Client):
    """supabase Client whose .schema(name) memoizes one RESILIENT SyncPostgrestClient per schema.

    Stock `.schema()` builds a brand-new postgrest client + httpx pool on every call (see module
    docstring); memoizing per schema reuses one pooled connection per schema for the process life.
    Returns the exact same SyncPostgrestClient type call sites already chain `.table()/.rpc()` on.
    """

    def __init__(self, *args, **kwargs):
        # set before super().__init__ so a hypothetical .schema() during init can't hit an unset attr
        self._schema_clients: Dict[str, SyncPostgrestClient] = {}
        # RE-ENTRANT: building a non-default schema client derives its headers from the DEFAULT
        # schema client (exact stock parity, see _new_schema_client), which re-enters schema().
        self._schema_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def _new_schema_client(self, schema: str) -> SyncPostgrestClient:
        """Build one postgrest client for `schema` on a resilient, injected httpx pool.

        Produces requests BYTE-IDENTICAL to the stock supabase/postgrest construction path — the
        injected httpx client is the only difference. Verified against the installed 2.31.0 sources
        and proven on the wire by harness_db_resilience.py section C:
          • base_url — supabase passes `str(self.rest_url)`; postgrest's own `.schema()` passes
            `str(parent.base_url)`, the same URL round-tripped through yarl.
          • headers — stock builds the DEFAULT-schema client from `self.options.headers` (a plain
            dict, so `{"X-Client-Info": <postgrest>, **headers}` collapses onto supabase's value)
            and every OTHER schema from `dict(parent.headers)` — which, because `parent.headers` is
            an `httpx.Headers` (lower-cased keys), does NOT collapse and legitimately emits two
            `X-Client-Info` headers plus lower-cased `apikey`/`authorization`. Reproduced exactly
            here rather than "tidied", so this stays a drop-in replacement: header casing and
            duplicate telemetry headers are precisely what Supabase receives today.
          • timeout/verify/proxy — omitted on purpose: passing them only triggers postgrest's
            deprecation warnings, and with `http_client=` provided they never reach the session.
            The injected client carries the same effective values (see db_resilience).
          • the session's CLIENT-LEVEL default headers are then set to `pg.headers`, which is what
            stock's `Client(headers=self.headers)` does. (Requests do not depend on them —
            postgrest merges `pg.headers` into every request — but parity keeps this drop-in.)
        """
        default_schema = self.options.schema
        headers = (dict(self.options.headers) if schema == default_schema
                   else dict(self.schema(default_schema).headers))
        http_client = build_pool_client(str(self.rest_url))
        # http_client is None only under the DB_RESILIENCE_DISABLE kill switch: postgrest then
        # builds its own stock pool, and `timeout=` must be supplied so the fallback is TRULY stock
        # (postgrest passes the raw arg to httpx, and `Client(timeout=None)` means NO timeout).
        extra = {} if http_client is not None else {"timeout": DEFAULT_POSTGREST_CLIENT_TIMEOUT}
        pg = SyncPostgrestClient(
            str(self.rest_url),
            schema=schema,
            headers=headers,
            http_client=http_client,
            **extra,
        )
        pg.session.headers.update(pg.headers)   # no-op on the stock path (same headers already set)
        return pg

    def schema(self, schema: str) -> SyncPostgrestClient:
        pg = self._schema_clients.get(schema)
        if pg is None:
            with self._schema_lock:
                pg = self._schema_clients.get(schema)
                if pg is None:
                    pg = self._new_schema_client(schema)
                    self._schema_clients[schema] = pg
        return pg

    @property
    def postgrest(self) -> SyncPostgrestClient:
        """The default-schema pool, routed through the same memo so it is resilient too.

        `client.table()/.from_()/.rpc()` all delegate here (4 live call sites in commcalc use the
        default schema). Stock supabase would lazily build this one with `http_client=None`, i.e.
        with the stock http2 pool this package exists to replace.
        """
        return self.schema(self.options.schema)

    def _listen_to_auth_events(self, event, session) -> None:   # pragma: no cover - never fires
        """Drop the memoized pools when supabase rotates the Authorization header.

        Stock does `self._postgrest = None` so the next access rebuilds with the new token. Since
        `postgrest` is now a memo lookup, the memo must be the thing that is cleared — otherwise a
        rotated token would never reach the pools. This path is DEAD in this app (service key; the
        app never calls auth.sign_in*/set_session/sign_out — grep-verified) and is kept purely so
        the subclass cannot become less correct than the class it overrides. In-flight callers
        holding an old client keep working; only the next `.schema()` rebuilds.
        """
        super()._listen_to_auth_events(event, session)
        if event in ("SIGNED_IN", "TOKEN_REFRESHED", "SIGNED_OUT"):
            with self._schema_lock:
                self._schema_clients.clear()


def _build() -> Client:
    key = settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_KEY
    # .create() (what create_client() calls) rather than bare __init__, so the one-time auth-header
    # bootstrap behaves exactly like every per-call construction did before.
    return _SchemaCachingClient.create(settings.SUPABASE_URL, key)


def get_supabase() -> Client:
    global _client
    c = _client
    if c is None:
        with _lock:                 # double-checked locking: build exactly once under concurrency
            c = _client
            if c is None:
                c = _client = _build()
    return c


def get_supabase_admin() -> Client:
    # Historically an identical body (same key resolution: SERVICE_KEY, falling back to KEY), so it
    # shares the SAME singleton — one connection pool set for the whole process.
    return get_supabase()
