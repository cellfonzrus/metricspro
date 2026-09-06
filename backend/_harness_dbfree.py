"""Shared DB-FREE guard for proof harnesses.

House rule (CLAUDE.md): "Pure logic ships with a DB-free proof harness." A harness must never open a
connection to a live tenant's database. This module makes that guarantee real instead of hoped-for.

WHY THIS EXISTS — the hole it closes
────────────────────────────────────
The router-integration harnesses inject their in-memory fake the obvious way:

    import app.modules.storeops.router as router_mod
    router_mod.get_supabase = fake_get_supabase

That binding covers only the ONE name in the ONE module it was written against. Two shipped code
paths route around it entirely and land on the REAL client, built from SUPABASE_SERVICE_KEY:

  1. `app/core/tenant_middleware.py::caller_app_user` imports the factory INSIDE the function body
     (`from app.core.database import get_supabase`), so it re-resolves from the source module on
     every call and never sees a patch applied to a router module. Every `_require_manager`,
     `_require_hr_or_admin` and `_caller_identity` gate reaches production through it.
  2. A handler in module A can call a helper in module B: `app/modules/closing/router.py` reaches
     `app/modules/storeops/router.py::_rbac_enabled`, whose `sb()` uses storeops' OWN unpatched
     `get_supabase`. That one swallows every exception (`except Exception: return False`), so the
     live call is SILENT — the harness keeps running and simply reports the wrong answer.

`install()` patches the single chokepoint every one of those paths ultimately resolves through, and
then sweeps any already-imported module that captured the name at import time. It also replaces the
real client CONSTRUCTOR with a tripwire, so a path this module has not anticipated raises loudly
instead of quietly talking to a live tenant.

USAGE — call it BEFORE importing any `app.modules.*` router:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _harness_dbfree
    _harness_dbfree.install(FAKE_CLIENT)

`install()` is safe to call after the imports too (the sys.modules sweep re-points them), but before
is better: it means a real client is never even reachable.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


class LiveDatabaseReached(RuntimeError):
    """Raised when harness code tries to BUILD a real Supabase client."""


def _tripwire(*_a, **_k):
    raise LiveDatabaseReached(
        "DB-FREE VIOLATION: this harness tried to construct a REAL Supabase client. Proof "
        "harnesses must run entirely against an in-memory fake — seed the fake for whatever "
        "table this code path reads instead of reaching for a live database.")


def install(fake_client):
    """Point EVERY Supabase client acquisition in this process at `fake_client`.

    Returns the client, so it can be used inline. Idempotent."""
    import app.core.database as _db

    def _get(*_a, **_k):
        return fake_client

    _db.get_supabase = _get
    _db.get_supabase_admin = _get
    _db._build = _tripwire          # nothing may construct a real client from here on
    _db._client = None              # drop any singleton built before install()

    # Re-point modules that captured the factory at import time (`from ... import get_supabase`).
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")) or mod is None or mod is _db:
            continue
        for attr in ("get_supabase", "get_supabase_admin"):
            if getattr(mod, attr, None) is not None:
                try:
                    setattr(mod, attr, _get)
                except Exception:
                    pass
    return fake_client
