"""ADMIN "VIEW AS EMPLOYEE" — the server-side impersonation primitive (SHARED, platform-core steward).

OWNER DIRECTIVE 2026-08-06:
    "the admin should have the option to login the app with an employees login directly from the
     roles and config menu incase needed to replicate an issue they are facing but not able to clock
     in or clock out without password, rest all functions should work"

WHAT THIS MODULE IS
───────────────────
The ONE place that decides "is this request being made by an admin acting AS someone else, and if so,
as whom". Everything else in the codebase asks this module; nothing else parses the header, and no
module ever trusts a client-supplied "I am impersonating" flag.

Three consumers, in order of blast radius:
  1. `app/core/tenant_middleware.py` — mints nothing, but VERIFIES the grant on every request, pins
     the acting org, and publishes the context (see `resolve_request` / `set_current`).
  2. `app/modules/core/router.py::_uid_from_token` — swaps the EFFECTIVE auth id to the target
     (`effective_uid`). This is what makes the whole product render as the employee: every role,
     permission, span and org decision in every module already flows from that one uid.
  3. Any endpoint that must NOT be forgeable by an admin wearing someone else's face — today exactly
     two, `POST /storeops/timeclock/clock-in` and `/clock-out` — calls `require_target_reauth()`.

THE TOKEN / CLAIM MECHANISM (read this before changing anything)
───────────────────────────────────────────────────────────────
An impersonation claim is a **server-minted, HMAC-signed, DB-anchored grant**, carried in the
`x-impersonate` request header. It is NOT a client flag and NOT a Supabase token.

  grant = base64url(json payload) "." base64url(HMAC-SHA256(key, body))
  payload = {"v":1, "p":"imp", "s":<impersonation session uuid>, "a":<ACTOR auth id>,
             "t":<TARGET auth id>, "o":<org_id>, "e":<expiry epoch>}

Verification is a FIVE-part AND, all server-side, on every single request:
  (1) the HMAC verifies under a key only the backend holds  → the payload was minted by us;
  (2) `e` is in the future                                  → hard expiry, no revocation needed;
  (3) the request ALSO carries a valid Supabase bearer token whose auth id == `a`
                                                            → the grant is bound to the real human
                                                              and is worthless when stolen alone;
  (4) `core.impersonation_session` row `s` exists, is not ended, and has not expired
                                                            → "Exit" and the sweep really do revoke;
  (5) the TARGET still holds an ACTIVE `storeops.app_users` row in org `o`
                                                            → a revoked/moved employee ends it.
Any failure ⇒ the request is REJECTED (401/403). There is no "fall back to being the admin" path:
a session that shows the impersonation banner can never quietly act with admin rights.

Domain separation: the signing key is HKDF-ish derived from the platform secret with the literal
`metricspro.impersonation.v1`, so a 2FA marker (`x-2fa-token`, same base secret) can never be
replayed as an impersonation grant and vice-versa.

THREAT MODEL — what this stops, and what it does not
────────────────────────────────────────────────────
STOPS
  • A hand-forged header. No HMAC key ⇒ no valid grant; the payload is not parsed at all when the
    signature fails (constant-time compare).
  • A stolen grant used from another login. Bound to the actor's auth id (3).
  • Privilege escalation. The effective identity is the TARGET's — never a union. `assert_not_impersonating`
    blocks the escalation surface (start another impersonation, edit roles/users/tenants, change the
    target's own password / 2FA / phone), and `tenant_middleware` enforces the same list at the edge.
  • Cross-tenant wandering. The grant pins `org_id` AND `x-active-org`; the super-admin "no rewrite"
    bypass is deliberately NOT taken while impersonating.
  • An abandoned session. Hard `expires_at` on the row + `e` in the grant (tenant-configurable,
    default 45 min).
  • Manufacturing a punch. `require_target_reauth` demands a fresh, single-use, server-verified
    re-authentication AS THE EMPLOYEE (below).
NOT STOPPED (accepted, documented)
  • Anyone who can read the backend's secret + the database can mint anything. That is true of every
    server-side auth decision in this codebase.
  • An admin who learns the employee's password can punch for them. That is the owner's chosen line:
    the password IS the consent. What the design guarantees is that a punch cannot happen without it.
  • The single-use re-auth marker is bound to the Supabase auth SESSION the employee's password
    created (claim `session_id`), and that session may only ever mint ONE marker. So an admin who
    keeps the refresh token from a password entry still cannot mint a second marker; they would need
    the employee to type the password again. (Where a JWT carries no `session_id` claim we fall back
    to a hash of the token, and a refresh WOULD produce a new value — noted as the weakest link.)
  • Per-request write journalling is fail-CLOSED on the pre-write (below), but the status back-fill
    is best-effort; a crashed process can leave a journal row with a NULL status.

ATTRIBUTION OF WRITES (audit requirement)
─────────────────────────────────────────
We do NOT add an `acted_by` column to every module's tables — that would be a cross-module schema
change with an enormous blast radius and would still miss anything written through an RPC. Instead
the middleware writes ONE append-only `core.impersonation_action` row for EVERY mutating request
(POST/PUT/PATCH/DELETE) made inside an impersonated session, BEFORE the handler runs, carrying the
real human, the target, the org, the method+path+query and (best-effort, after) the status code.
The pre-write is FAIL-CLOSED: if the journal cannot be written, the mutating request is refused
(503). Reads are not journalled (volume), but the session row itself records the whole window.

DEGRADES GRACEFULLY (migration 730 un-run)
──────────────────────────────────────────
Every DB touch here is try/except-guarded. With mig 730 un-run, `POST /core/impersonation/start`
cannot write its audit row and therefore FAILS CLOSED — no grant is ever minted, so no request can
carry a valid `x-impersonate` header and the entire feature is simply absent. Nothing else in the
product changes: with no header present, `resolve_request` is never called.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

# ── Wire protocol ───────────────────────────────────────────────────────────────────────────────
IMPERSONATE_HEADER = "x-impersonate"          # the signed grant (minted by /core/impersonation/start)
REAUTH_HEADER = "x-impersonate-reauth"        # the single-use "the employee just typed their password"

_PURPOSE_GRANT = "imp"
_PURPOSE_REAUTH = "impra"
_KEY_INFO = b"metricspro.impersonation.v1"    # domain separation vs the 2FA marker (same base secret)

# ── Tenant-configurable policy (RULE TWO — no constants a human would want to tune) ─────────────
# Stored on storeops.tenants.impersonation_policy (jsonb, mig 730). Absent column / absent row /
# garbage ⇒ these defaults, so the feature behaves identically before and after the migration.
DEFAULT_POLICY = {
    "enabled": True,               # a tenant may switch impersonation off entirely
    "max_minutes": 45,             # hard server-side session expiry
    "reauth_minutes": 5,           # how long a "the employee typed their password" marker lives
    "reauth_token_max_age_s": 120,  # how FRESH the employee's Supabase token must be to mint one
}
_POLICY_BOUNDS = {
    "max_minutes": (5, 240),
    "reauth_minutes": (1, 30),
    "reauth_token_max_age_s": (30, 600),
}
_POLICY_TTL_S = 30.0
_policy_cache: dict = {}


def normalize_policy(raw) -> dict:
    """PURE. Merge a (possibly partial / possibly hostile) tenant override over the defaults and
    clamp every bound. Always returns a complete, safe policy. Never raises."""
    p = dict(DEFAULT_POLICY)
    if isinstance(raw, dict):
        if "enabled" in raw:
            p["enabled"] = bool(raw.get("enabled"))
        for k, (lo, hi) in _POLICY_BOUNDS.items():
            v = raw.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            p[k] = max(lo, min(int(v), hi))
    return p


def load_policy(client, org_id: str) -> dict:
    """Cached, best-effort read of a tenant's impersonation policy. Any error / un-run mig 730 ⇒
    DEFAULT_POLICY (the feature keeps working with sane bounds). Never raises."""
    now = time.time()
    hit = _policy_cache.get(org_id)
    if hit and hit[1] > now:
        return hit[0]
    raw = None
    try:
        rows = (client.schema("storeops").table("tenants").select("impersonation_policy")
                .eq("org_id", org_id).limit(1).execute().data) or []
        raw = rows[0].get("impersonation_policy") if rows else None
    except Exception:
        raw = None
    pol = normalize_policy(raw)
    _policy_cache[org_id] = (pol, now + _POLICY_TTL_S)
    return pol


def invalidate_policy(org_id: str = None) -> None:
    if org_id is None:
        _policy_cache.clear()
    else:
        _policy_cache.pop(org_id, None)


# ── Signed grant / re-auth marker (PURE — no DB, unit-provable) ─────────────────────────────────
def _base_secret() -> bytes:
    """Base platform secret. Explicit IMPERSONATION_SECRET wins (rotate independently); otherwise the
    same fallback ladder every other signed marker here uses. Never a blank key in prod because
    SUPABASE_SERVICE_KEY is always set."""
    env = os.environ.get("IMPERSONATION_SECRET", "").strip()
    if env:
        return env.encode()
    try:
        from app.core.config import settings
        return ((settings.AUTH_2FA_SECRET or settings.SUPABASE_SERVICE_KEY or "mp-imp-secret")).encode()
    except Exception:                                            # pragma: no cover - config import guard
        return b"mp-imp-secret"


def _key() -> bytes:
    """Domain-separated signing key. A 2FA marker and an impersonation grant can never cross-verify."""
    return hmac.new(_base_secret(), _KEY_INFO, hashlib.sha256).digest()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: dict) -> str:
    body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64u(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _open(token: str, purpose: str, now: float):
    """Return the payload iff `token` is well-formed, correctly signed, of the right PURPOSE and
    unexpired; else None. Constant-time on the signature. Never raises."""
    try:
        body, _, sig = (token or "").partition(".")
        if not body or not sig:
            return None
        expect = _b64u(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        p = json.loads(_b64u_dec(body).decode())
        if not isinstance(p, dict):
            return None
        if p.get("p") != purpose:            # purpose confusion (grant vs re-auth) is a forgery
            return None
        if float(p.get("e") or 0) <= now:
            return None
        return p
    except Exception:
        return None


def mint_grant(*, session_id: str, actor_uid: str, target_uid: str, org_id: str, exp_ts: float) -> str:
    return _sign({"v": 1, "p": _PURPOSE_GRANT, "s": str(session_id), "a": str(actor_uid),
                  "t": str(target_uid), "o": str(org_id), "e": int(exp_ts)})


def verify_grant(token: str, now: float = None):
    return _open(token, _PURPOSE_GRANT, now if now is not None else time.time())


def mint_reauth(*, session_id: str, target_uid: str, nonce: str, exp_ts: float) -> str:
    return _sign({"v": 1, "p": _PURPOSE_REAUTH, "s": str(session_id), "t": str(target_uid),
                  "n": str(nonce), "e": int(exp_ts)})


def verify_reauth(token: str, now: float = None):
    return _open(token, _PURPOSE_REAUTH, now if now is not None else time.time())


# ── Per-request context ─────────────────────────────────────────────────────────────────────────
# Set by tenant_middleware BEFORE `await self.app(...)` and reset in its finally. uvicorn runs one
# asyncio Task per request and Starlette/anyio COPY the context into the sync-handler threadpool, so
# this is per-request state, not global state. Belt-and-braces: `effective_uid` additionally requires
# the context's actor to equal the auth id the request's own bearer token just resolved to, so even a
# hypothetical leaked context can never swap identity for a different caller.
_CTX: contextvars.ContextVar = contextvars.ContextVar("mp_impersonation", default=None)


def current() -> dict:
    """The active impersonation context, or None. Keys: session_id, actor_uid, target_uid, org_id,
    expires_ts, reauth (raw header), actor_email, target_email, target_name."""
    return _CTX.get()


def set_current(ctx: dict):
    return _CTX.set(ctx)


def reset_current(token) -> None:
    try:
        _CTX.reset(token)
    except Exception:                                             # pragma: no cover - defensive
        _CTX.set(None)


def is_impersonating() -> bool:
    return _CTX.get() is not None


def actor_uid():
    c = _CTX.get()
    return c.get("actor_uid") if c else None


def target_uid():
    c = _CTX.get()
    return c.get("target_uid") if c else None


def effective_uid(real_uid: str):
    """THE identity swap. Returns the TARGET's auth id when this request carries a verified grant
    minted for `real_uid`; otherwise returns `real_uid` unchanged.

    The `actor_uid == real_uid` check is the safety interlock: the swap only ever applies to the
    exact login the grant was issued to."""
    c = _CTX.get()
    if not c or not real_uid:
        return real_uid
    if c.get("actor_uid") != real_uid:
        return real_uid
    return c.get("target_uid") or real_uid


# ── Session validity (DB-anchored, fail-closed) ─────────────────────────────────────────────────
_SESSION_TTL_S = 10.0        # short: "Exit" must take effect fast (same-process invalidate is instant)
_session_cache: dict = {}


def invalidate_session(session_id: str = None) -> None:
    if session_id is None:
        _session_cache.clear()
    else:
        _session_cache.pop(str(session_id), None)


def _iso_to_ts(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def session_state(client, session_id: str, target_uid_: str, org_id: str, now: float = None) -> str:
    """Is impersonation session `session_id` still live AND still legitimate?

    Returns 'ok' | 'ended' | 'expired' | 'unknown' | 'target_revoked' | 'unavailable'.
    'unavailable' means the store could not be read — the caller must FAIL CLOSED (we never guess).
    Cached for _SESSION_TTL_S; POSITIVE results only, so a revocation is picked up promptly and a
    transient error is retried on the next request."""
    now = now if now is not None else time.time()
    key = str(session_id)
    hit = _session_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        rows = (client.schema("core").table("impersonation_session")
                .select("id,org_id,actor_auth_id,target_auth_id,ended_at,expires_at")
                .eq("id", key).limit(1).execute().data) or []
    except Exception:
        return "unavailable"
    if not rows:
        return "unknown"
    row = rows[0]
    if row.get("ended_at"):
        return "ended"
    if _iso_to_ts(row.get("expires_at")) <= now:
        return "expired"
    if str(row.get("target_auth_id") or "") != str(target_uid_ or ""):
        return "unknown"
    if str(row.get("org_id") or "") != str(org_id or ""):
        return "unknown"
    # The target must STILL be an active member of that org — a revoked / moved / deactivated
    # employee ends the session immediately rather than leaving an admin wearing a stale face.
    try:
        urows = (client.schema("storeops").table("app_users").select("id,is_active")
                 .eq("auth_id", str(target_uid_)).eq("org_id", str(org_id)).limit(1).execute().data) or []
    except Exception:
        return "unavailable"
    if not urows or urows[0].get("is_active") is False:
        return "target_revoked"
    _session_cache[key] = ("ok", now + _SESSION_TTL_S)
    return "ok"


_BRIEF_TTL_S = 30.0
_brief_cache: dict = {}


def session_brief(client, session_id: str) -> dict:
    """Small, display-only summary of a live session for `/core/me` — what the banner needs to name
    the employee and count down. Cached; best-effort (a read failure degrades to {'active': True} so
    the banner still shows). NEVER returns a token."""
    key = str(session_id or "")
    if not key:
        return {"active": True}
    now = time.time()
    hit = _brief_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    out = {"active": True, "session_id": key}
    try:
        rows = (client.schema("core").table("impersonation_session")
                .select("id,org_id,target_name,target_email,target_role,actor_email,actor_name,"
                        "started_at,expires_at,reason")
                .eq("id", key).limit(1).execute().data) or []
        if rows:
            r = rows[0]
            out.update({"org_id": r.get("org_id"), "target_name": r.get("target_name"),
                        "target_email": r.get("target_email"), "target_role": r.get("target_role"),
                        "actor_email": r.get("actor_email"), "actor_name": r.get("actor_name"),
                        "started_at": r.get("started_at"), "expires_at": r.get("expires_at"),
                        "reason": r.get("reason")})
    except Exception:
        return out
    _brief_cache[key] = (out, now + _BRIEF_TTL_S)
    return out


def resolve_request(grant_token: str, bearer_uid: str, reauth_token: str = "", now: float = None):
    """Turn the raw `x-impersonate` header into a verified context. Called ONLY by tenant_middleware.

    Returns (ctx | None, reason). reason ∈ {'ok','invalid','not_actor','ended','expired','unknown',
    'target_revoked','unavailable','disabled'}. Anything but 'ok' ⇒ the middleware rejects; there is
    deliberately no "continue as the admin" outcome."""
    now = now if now is not None else time.time()
    p = verify_grant(grant_token, now)
    if not p:
        return (None, "invalid")
    if not bearer_uid or str(p.get("a")) != str(bearer_uid):
        return (None, "not_actor")
    sid, tgt, org = str(p.get("s") or ""), str(p.get("t") or ""), str(p.get("o") or "")
    if not (sid and tgt and org):
        return (None, "invalid")
    try:
        from app.core.database import get_supabase
        client = get_supabase()
    except Exception:
        return (None, "unavailable")
    state = session_state(client, sid, tgt, org, now)
    if state != "ok":
        return (None, state)
    if not load_policy(client, org).get("enabled", True):
        return (None, "disabled")
    return ({"session_id": sid, "actor_uid": str(p.get("a")), "target_uid": tgt, "org_id": org,
             "expires_ts": float(p.get("e") or 0), "reauth": (reauth_token or "").strip()}, "ok")


# ── Privilege-escalation guard ──────────────────────────────────────────────────────────────────
# Paths an impersonated session may never WRITE to (defence in depth — the target's own permissions
# already gate most of these, but an admin impersonating another ADMIN would otherwise inherit them).
# Boundary-matched prefixes. Enforced at the edge by tenant_middleware AND, for start-impersonation,
# by an explicit assert in the endpoint.
FORBIDDEN_WRITE_PREFIXES = (
    "/api/v1/core/impersonation",     # no nesting, no self-extension, no stopping someone else's
    "/api/v1/core/roles",             # role / permission editing
    "/api/v1/core/users",             # role assignment, login creation, admin-set-password, delete
    "/api/v1/core/super-admins",      # super-admin grant/revoke
    "/api/v1/core/tenants",           # tenant provisioning / settings
    "/api/v1/core/auth-config",       # the global enforce-login switch
    "/api/v1/core/security-settings",  # password policy / 2FA policy
    "/api/v1/core/me/set-password",   # would change the EMPLOYEE's password
    "/api/v1/core/me/2fa",            # would change the EMPLOYEE's second factor
    "/api/v1/core/me/phone",          # would change the EMPLOYEE's recovery phone
    "/api/v1/core/me/password-changed",
)
_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def is_forbidden_while_impersonating(path: str, method: str) -> bool:
    """PURE. True when this (path, method) must be refused for an impersonated session.
    `/api/v1/core/impersonation` is refused for EVERY method (an impersonated session has no
    business reading or writing the impersonation console at all)."""
    path = str(path or "")
    for p in FORBIDDEN_WRITE_PREFIXES:
        if path == p or path.startswith(p + "/"):
            if p == "/api/v1/core/impersonation":
                return True
            return (method or "").upper() in _WRITE_METHODS
    return False


def assert_not_impersonating(what: str = "this action") -> None:
    """Refuse an operation outright while impersonating. Use for anything that would let a borrowed
    identity widen its own rights (starting another impersonation, editing roles)."""
    if is_impersonating():
        raise HTTPException(403, f"Not available while viewing the app as another user: {what}. "
                                 "Exit the impersonated session first.")


# ── THE CLOCK-IN / CLOCK-OUT GATE (the owner's carve-out) ───────────────────────────────────────
def require_target_reauth(action: str) -> dict:
    """Gate an action that must never be manufacturable by an admin wearing someone else's face.

    CONTRACT FOR OTHER MODULES (this is the whole integration surface):

        from app.core.impersonation import require_target_reauth
        require_target_reauth("timeclock.clock_in")

    * NOT impersonating  → returns {"impersonating": False} immediately. ZERO behaviour change, no
      DB call, no network. A normal employee punching normally never notices this exists.
    * Impersonating, and the employee has JUST re-authenticated (a valid, unexpired, UNUSED marker
      minted by POST /core/impersonation/reauth for THIS session) → the marker is CONSUMED (single
      use — one password entry buys exactly one punch) and it returns
      {"impersonating": True, "actor_uid": ..., "target_uid": ..., "session_id": ..., "reauth_id": ...}.
    * Impersonating without one → raises HTTPException(403) with code `reauth_required`.
    * Anything unexpected (marker store unreadable, race lost) → raises 403. FAIL CLOSED, always.

    Call it as the FIRST statement of the handler, before any state is touched."""
    ctx = current()
    if not ctx:
        return {"impersonating": False}
    now = time.time()
    marker = (ctx.get("reauth") or "").strip()
    p = verify_reauth(marker, now) if marker else None
    if not p or str(p.get("s")) != str(ctx.get("session_id")) or str(p.get("t")) != str(ctx.get("target_uid")):
        raise HTTPException(403, _REAUTH_MSG)
    nonce = str(p.get("n") or "")
    if not nonce:
        raise HTTPException(403, _REAUTH_MSG)
    # Single use: claim the nonce atomically. PostgREST returns the affected rows, so an empty
    # result means somebody (or a double-submit) already consumed it.
    try:
        from app.core.database import get_supabase
        claimed = (get_supabase().schema("core").table("impersonation_reauth")
                   .update({"consumed_at": datetime.now(timezone.utc).isoformat(),
                            "consumed_action": str(action)[:80]})
                   .eq("nonce", nonce).eq("imp_session_id", str(ctx.get("session_id")))
                   .is_("consumed_at", "null").execute().data) or []
    except Exception:
        raise HTTPException(403, _REAUTH_MSG)
    if not claimed:
        raise HTTPException(403, _REAUTH_MSG)
    log_event(kind="reauth_used", ctx=ctx, detail={"action": str(action)[:80], "nonce": nonce[:12]})
    return {"impersonating": True, "actor_uid": ctx.get("actor_uid"),
            "target_uid": ctx.get("target_uid"), "session_id": ctx.get("session_id"),
            "reauth_id": nonce}


_REAUTH_MSG = ("You are viewing the app as another employee. Clocking in or out on their behalf "
               "requires THEIR password — open the impersonation banner and choose "
               "\"Unlock clock in/out\". Each unlock is good for one punch.")


# ── Append-only audit ───────────────────────────────────────────────────────────────────────────
def new_id() -> str:
    return str(uuid.uuid4())


def log_event(*, kind: str, ctx: dict = None, org_id: str = None, session_id: str = None,
              actor_uid_: str = None, target_uid_: str = None, method: str = None, path: str = None,
              query: str = None, status: int = None, detail: dict = None, ip: str = None,
              user_agent: str = None, client=None, fail_closed: bool = False):
    """Write one `core.impersonation_action` row. Returns the row id (or None).

    `fail_closed=True` re-raises so the caller can refuse the request — used for the pre-write of
    every MUTATING request (a write we cannot attribute must not happen). Everything else is
    best-effort: a missing mig 730 must never break an unrelated page (contract §5)."""
    ctx = ctx or {}
    row = {
        "id": new_id(),
        "org_id": org_id or ctx.get("org_id"),
        "session_id": session_id or ctx.get("session_id"),
        "actor_auth_id": actor_uid_ or ctx.get("actor_uid"),
        "target_auth_id": target_uid_ or ctx.get("target_uid"),
        "kind": str(kind)[:32],
        "method": (method or "")[:10] or None,
        "path": (path or "")[:400] or None,
        "query": (query or "")[:600] or None,
        "status": status,
        "detail": detail or None,
        "ip": (ip or "")[:64] or None,
        "user_agent": (user_agent or "")[:300] or None,
    }
    try:
        from app.core.database import get_supabase
        (client or get_supabase()).schema("core").table("impersonation_action").insert(row).execute()
        return row["id"]
    except Exception:
        if fail_closed:
            raise
        return None


def finish_event(row_id: str, status: int) -> None:
    """Best-effort status back-fill on a pre-written journal row. Never raises."""
    if not row_id:
        return
    try:
        from app.core.database import get_supabase
        (get_supabase().schema("core").table("impersonation_action")
         .update({"status": int(status)}).eq("id", str(row_id)).execute())
    except Exception:
        pass


def should_journal(method: str) -> bool:
    """PURE. Mutating requests are journalled; reads are not (volume)."""
    return (method or "").upper() in _WRITE_METHODS
