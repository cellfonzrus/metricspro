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
"""
import threading
from typing import Dict, Optional

from supabase import create_client, Client  # create_client kept importable for legacy importers
from postgrest import SyncPostgrestClient

from app.core.config import settings

_lock = threading.Lock()
_client: Optional[Client] = None


class _SchemaCachingClient(Client):
    """supabase Client whose .schema(name) memoizes one SyncPostgrestClient per schema.

    Stock `.schema()` builds a brand-new postgrest client + httpx pool on every call (see module
    docstring); memoizing per schema reuses one pooled connection per schema for the process life.
    Returns the exact same SyncPostgrestClient type call sites already chain `.table()/.rpc()` on.
    """

    def __init__(self, *args, **kwargs):
        # set before super().__init__ so a hypothetical .schema() during init can't hit an unset attr
        self._schema_clients: Dict[str, SyncPostgrestClient] = {}
        self._schema_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def schema(self, schema: str) -> SyncPostgrestClient:
        pg = self._schema_clients.get(schema)
        if pg is None:
            with self._schema_lock:
                pg = self._schema_clients.get(schema)
                if pg is None:
                    pg = super().schema(schema)   # new independent postgrest client (verified above)
                    self._schema_clients[schema] = pg
        return pg


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
