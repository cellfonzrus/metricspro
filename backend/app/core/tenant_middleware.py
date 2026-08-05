"""Tenant-scope middleware (SaaS P3 hardening) — GATED OFF by default.

When env MULTI_TENANT_ENFORCE=1, every request's org_id is DERIVED FROM THE VERIFIED TOKEN, not
trusted from the query string: the middleware verifies the Supabase bearer token, resolves the
caller's storeops.app_users.org_id, and REWRITES the org_id query param to that value — so a user
can't reach another tenant by passing a different org_id (the residual risk left by P2's client-side
scoping). Public routes are allowlisted; token→org_id is cached briefly to avoid a Supabase auth
call per request. Default OFF ⇒ pure pass-through, zero behavior change.

2026-07-14 hardening (owner-approved) — CLOSE THE UNAUTHENTICATED CROSS-TENANT HOLE:
Previously, a request with NO bearer token (or a bogus one) fell straight through with the caller's
own `?org_id=` honored — so an unauthenticated caller could read/write ANY tenant by guessing an
org_id. Now, when enforcement is on, a request to a NON-public route that does not carry a VALID
Supabase token is REJECTED (401 `{"detail":"authentication required"}`) instead of falling through.

  • The authenticated path is UNCHANGED: valid normal-user token → org_id rewrite; super-admin
    (or a verified user with no app_users row) → NO rewrite (client org_id honored, cross-tenant
    admin intact).
  • Only genuinely-anonymous / self-authenticating routes are allowlisted (see `_is_public`):
    health + docs; the pre-login core auth endpoints; token-verified /core/me; the self-serve
    signup pair; the dual-auth /core/tenants/sync; the Meta WhatsApp webhook; the HR onboarding
    public token endpoints; the notify no-login report download (/api/v1/notify/dl/{token} — the HMAC
    token IS the capability, scoped to one file); and every `*/run-due` sweep (each verifies its own
    x-notify-secret and is called by pg_cron with no JWT). Each of these authenticates by another
    mechanism (its own token/secret/handshake), so bypassing the JWT check for them is safe.
  • KILL SWITCH: env REQUIRE_AUTH (default ON when unset). Set REQUIRE_AUTH=0 to revert to the old
    pass-through (client org_id honored on tokenless requests) via a single Railway env change — no
    code rollback. The authenticated rewrite still runs regardless.

2026-07-14 multi-tenant login switcher (platform-core-9) — ONE login may belong to MANY tenants:
A login's org is no longer a single app_users.org_id; it is the SET of tenants the auth_id is a
member of (one storeops.app_users row per tenant, mig 706). The caller declares which tenant it is
acting as via the `x-active-org` request header. The middleware VERIFIES that choice against the
membership set (never trusts a bare client value): if the header names a tenant the login belongs to,
org_id is rewritten to it; otherwise it falls back to the login's DEFAULT membership (is_default_org,
else earliest-created). A single-membership login (which includes every mig-088 aliased login) always
resolves to its one org regardless of the header — so nothing changes for them. Super-admins still
bypass (no rewrite, client org_id honored). The token→identity cache holds the whole membership set,
so switching tenants (a new header value on the same token) needs no re-auth and no cache bust.

2026-08-03 auth-ux hardening (filed by tenant-triage after the house-org TEST DM incident) — TELL
THE TRUTH ABOUT *WHY* IDENTITY FAILED:
`_resolve_identity` used to wrap token verification AND the membership fetch in ONE broad
`except Exception -> (False, ...)`, so an infrastructure hiccup (stale singleton pool,
DatabaseUnavailable, PostgREST down) came back to the user as 401 "authentication required" and was
never logged anywhere. Two consequences, both fixed here:

  • HONESTY. Token verification failing is a 401 (byte-identical body, unchanged). The membership
    store being UNREADABLE is now a 503 with its own message + a best-effort core.failure_log row,
    so an outage stops looking like "your password is wrong" / "the app is broken".
  • ISOLATION. `_fetch_memberships` used to swallow its LAST fallback's error and return `[]`. An
    empty membership set means "verified user with no app_users row" ⇒ NO org_id rewrite ⇒ the
    CLIENT-SUPPLIED org_id is honored. So a transient failure to read app_users silently degraded
    tenant scoping to "whatever org_id the caller passed". That fallback now raises.

The column-ladder in `_fetch_memberships` still exists for its original purpose (mig 706/711 columns
un-run); only the FINAL, pre-706 `org_id,super_admin` select — which cannot fail for a schema reason
— is treated as infrastructure.

  • KILL SWITCH: env IDENTITY_BACKEND_503 (default ON when unset). IDENTITY_BACKEND_503=0 restores
    the pre-2026-08-03 behaviour EXACTLY (membership-read failure -> `[]` -> pass-through), via a
    single Railway env change and no code rollback. Same break-glass posture as REQUIRE_AUTH and
    TWOFA_ENFORCE: a deploy must never be able to strand the operator.
"""
import os
import time
import asyncio
from urllib.parse import parse_qs, urlencode

# Exact full paths that are public (matched literally, no prefix semantics).
_PUBLIC_EXACT = frozenset({
    "/health",                            # Railway / uptime health probe (no auth by design)
    "/openapi.json",                      # OpenAPI schema backing the docs UIs (currently open)
    "/api/v1/core/auth-config",           # login-enforcement flag read by the login/layout BEFORE sign-in
    "/api/v1/core/signup",                # self-serve tenant signup (env-gated SIGNUPS_OPEN; anonymous)
    "/api/v1/core/signup-status",         # /signup page checks whether signups are open, pre-login
    "/api/v1/core/tenants/sync",          # dual-auth: NOTIFY_RUN_SECRET header OR super-admin; cron has no JWT
    "/api/v1/core/password-policy/public",  # PUBLIC: owner DEFAULT policy for pre-login strength hints
    "/api/v1/core/auth/forgot-password",  # PUBLIC self-serve reset request (anti-enumeration; anonymous)
    "/api/v1/core/auth/reset-password",   # PUBLIC self-serve reset completion (code-gated; anonymous)
    "/api/v1/remediation/whatsapp-webhook",  # Meta webhook. EXACT path + METHOD-SCOPED below to
                                          # {GET, POST} only (2026-08-05: it was a PREFIX, so any future
                                          # sibling path under it would have been public too, and every
                                          # method was public). GET = the hub.challenge verification
                                          # handshake (self-gates on WHATSAPP_VERIFY_TOKEN, fail-closed
                                          # when unset); POST = Meta's inbound/status callback, whose ONLY
                                          # auth is the X-Hub-Signature-256 HMAC it self-verifies (now
                                          # fail-closed when WHATSAPP_APP_SECRET is unset). Meta carries no
                                          # JWT, so the auth requirement must not fire before the handler.
    "/api/v1/core/bootstrap",             # ONE-call login bootstrap (auth-config + my-tenants +
                                          # pending-connections + me). Same rationale as the
                                          # /api/v1/core/me prefix below: it SELF-GATES on the bearer
                                          # token inside the handler (401 without a valid one), and it
                                          # must be reachable BEFORE the 2FA marker exists so a
                                          # 2FA-required login can learn it needs the OTP step.
})

# Public path PREFIXES, matched at a SEGMENT BOUNDARY only (path == p or path.startswith(p + "/")),
# so "/api/v1/core/me" allows "/api/v1/core/me" and "/api/v1/core/me/password-changed" but NOT a
# hypothetical "/api/v1/core/members" — no sloppy startswith over-matching.
_PUBLIC_PREFIXES = (
    "/docs",                              # Swagger UI + /docs/oauth2-redirect (currently open)
    "/redoc",                             # ReDoc UI (currently open)
    "/api/v1/core/me",                    # token-verified whoami + /me/password-changed (self-gate on token)
    "/api/v1/hr/public/onboarding",       # HR onboarding public token endpoints (the link token IS the auth)
    "/api/v1/notify/dl",                   # no-login report download: the HMAC token IS the auth; reaches
                                           # ONLY the one artifact it signs (uniform 404 on any bad token)
    "/api/v1/core/fix-pipeline",            # Auto-Fix Pipeline (mig 718): DUAL-AUTH, same shape as
                                           # /core/tenants/sync + the */run-due sweeps. The scheduled
                                           # triage routine carries NO JWT — it authenticates with the
                                           # least-privilege x-fix-pipeline-secret header — so the JWT
                                           # requirement must not fire before the handler runs. EVERY
                                           # route under this prefix self-gates in
                                           # core/fix_pipeline.py::_authorize (default DENY: valid
                                           # secret scoped to feed+registry, else a verified
                                           # SUPER-ADMIN JWT; anything else 401/403) and resolves its
                                           # own org, because allowlisting also skips the org_id
                                           # rewrite. Boundary-matched, so ONLY
                                           # /api/v1/core/fix-pipeline[/…] is affected — the
                                           # pre-existing /api/v1/core/fix-requests endpoints (mig 716
                                           # support pipeline) do NOT match and keep full middleware
                                           # protection.
)

# Self-authenticating background sweeps: EVERY route ending in "/run-due" is invoked by pg_cron with
# no JWT and verifies its own `x-notify-secret` header inside the handler. Allowlisting the suffix
# lets the cron reach the handler (which still 403s on a wrong/absent secret) and is robust to new
# sweeps added by other modules. The full current set is enumerated in the platform-core handoff.
_RUN_DUE_SUFFIX = "/run-due"

# token -> (identity, expiry_epoch), positive results only. identity is the 4-tuple returned by
# _resolve_identity: (authenticated, super_admin, member_orgs, default_org). Caching the whole
# membership SET (not a single org) lets a login switch active tenants — a new x-active-org header on
# the SAME token — with no re-auth and no cache bust; the per-request header pick is pure/cheap.
_cache: dict = {}
_TTL = 60.0

# Header by which the client declares which of its tenants it is acting as. Client-supplied and
# therefore UNTRUSTED — always verified against the login's membership set before it is honored.
_ACTIVE_ORG_HEADER = "x-active-org"


def _enabled() -> bool:
    return os.environ.get("MULTI_TENANT_ENFORCE", "").lower() in ("1", "true", "yes")


def _require_auth() -> bool:
    """Kill switch. Default ON when unset; REQUIRE_AUTH=0/false/no/off reverts to old pass-through."""
    return os.environ.get("REQUIRE_AUTH", "1").lower() not in ("0", "false", "no", "off")


def _identity_503() -> bool:
    """Break-glass for the 2026-08-03 honesty change. Default ON when unset; IDENTITY_BACKEND_503=0
    restores the old swallow-and-return-[] behaviour byte-for-byte (see module docstring)."""
    return os.environ.get("IDENTITY_BACKEND_503", "1").lower() not in ("0", "false", "no", "off")


def _strict_membership() -> bool:
    """Break-glass for the 2026-08-05 empty-membership fail-closed (H2). Default ON when unset;
    STRICT_MEMBERSHIP=0 restores the pre-2026-08-05 pass-through in which a verified login with NO
    app_users row skipped the org rewrite and the CLIENT-SUPPLIED org_id was honored. Same
    never-strand-the-operator posture as REQUIRE_AUTH / IDENTITY_BACKEND_503 / TWOFA_ENFORCE."""
    return os.environ.get("STRICT_MEMBERSHIP", "1").lower() not in ("0", "false", "no", "off")


class IdentityBackendUnavailable(Exception):
    """The MEMBERSHIP STORE could not be read — as distinct from the token being bad.

    Raised only after the bearer token has ALREADY verified, so it can never be confused with an
    authentication failure. The middleware turns it into a 503; it must never reach the app (the one
    call site catches it), and it never grants access: like the 401 path, no request carrying it ever
    reaches a handler, so this is strictly fail-closed."""

    def __init__(self, original: BaseException) -> None:
        self.original = original
        super().__init__(str(original))


# ── 2FA enforcement (auth-hardening 2026-07-17) — ADDITIVE, super-admin always bypassed ─────────────
# Break-glass: TWOFA_ENFORCE default ON, but per-tenant policy defaults OFF (NULL twofa_policy → mode
# 'off'), so a deploy locks NObody out. Set TWOFA_ENFORCE=0 to kill enforcement globally (a bad deploy
# can't strand the owner). A missing mig 711 column → the policy read errors → treated 'off' → no-op.
_twofa_cache: dict = {}          # org_id -> (policy_dict, expiry_epoch)
_TWOFA_TTL = 60.0


def _twofa_enforce() -> bool:
    return os.environ.get("TWOFA_ENFORCE", "1").lower() not in ("0", "false", "no", "off")


def _tenant_2fa_policy(org: str) -> dict:
    """Best-effort cached read of a tenant's twofa_policy. {'mode':'off'} on any error / un-run mig 711
    (→ no enforcement). Normalized to a safe shape here (independent of the router's copy)."""
    now = time.time()
    hit = _twofa_cache.get(org)
    if hit and hit[1] > now:
        return hit[0]
    policy = {"mode": "off", "required_roles": []}
    try:
        from app.core.database import get_supabase
        rows = (get_supabase().schema("storeops").table("tenants").select("twofa_policy")
                .eq("org_id", org).limit(1).execute().data) or []
        raw = rows[0].get("twofa_policy") if rows else None
        if isinstance(raw, dict):
            m = str(raw.get("mode") or "off").lower()
            policy["mode"] = m if m in ("off", "optional", "required") else "off"
            rr = raw.get("required_roles")
            if isinstance(rr, list):
                policy["required_roles"] = [str(r) for r in rr if str(r).strip()]
    except Exception:
        policy = {"mode": "off", "required_roles": []}
    _twofa_cache[org] = (policy, now + _TWOFA_TTL)
    return policy


def _tenant_needs_2fa(org: str, role, twofa_enabled) -> bool:
    """Does THIS (org, user) require 2FA? Middleware enforces tenant-wide 'required' (optionally
    role-scoped). 'optional' is user-opt-in (twofa_enabled); 'off' → never."""
    policy = _tenant_2fa_policy(org)
    mode = policy.get("mode")
    if mode == "off":
        return False
    if mode == "required":
        rr = policy.get("required_roles") or []
        return (not rr) or ((role or "") in rr)
    return bool(twofa_enabled)   # optional → only the users who turned it on


def _twofa_marker_ok(token: str, uid: str, org: str) -> bool:
    """Verify the stateless x-2fa-token for this login/org. FAILS OPEN on a verifier import/error so a
    library glitch can never lock anyone out (the safe direction under 'never strand the owner')."""
    try:
        from app.modules.core.auth_security import twofa_token_valid_for, now_ts
        return twofa_token_valid_for(token, uid, org, now_ts())
    except Exception:
        return True


async def _reject_2fa(send):
    body = b'{"detail":"two-factor authentication required","code":"2fa_required"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    for p in _PUBLIC_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    if path.endswith(_RUN_DUE_SUFFIX):
        return True
    return False


# METHOD SCOPING for allowlisted paths. An entry here means "public for THESE methods only"; every other
# method on that same path falls through to the normal auth + org_id-rewrite path (and then the handler's
# own permission gate). A path NOT listed keeps today's method-agnostic behaviour.
#   · /api/v1/core/auth-config — GET is read by the login page BEFORE sign-in; the PUT that FLIPS the
#     global enforce-login flag must never be anonymous (2026-08-05 security hardening).
#   · /api/v1/remediation/whatsapp-webhook — Meta only ever calls GET (verify handshake) and POST
#     (inbound + delivery-status callback); nothing else on that path should skip authentication.
_PUBLIC_METHODS = {
    "/api/v1/core/auth-config": ("GET",),
    "/api/v1/remediation/whatsapp-webhook": ("GET", "POST"),
}


def _public_method_ok(path: str, method: str) -> bool:
    """PURE. For an ALREADY-allowlisted path, is this HTTP method one of the public ones? True for any
    path with no method restriction (today's behaviour for everything not in _PUBLIC_METHODS)."""
    allowed = _PUBLIC_METHODS.get(path)
    return True if allowed is None else (method or "").upper() in allowed


def _fetch_memberships(client, uid):
    """Every app_users row for this auth_id, earliest first. Also selects role + twofa_enabled (used by
    the 2FA gate). Tolerant of post-706/711 columns being un-run: falls back through progressively
    leaner column lists so a missing column never breaks identity resolution (pre-706 there is at most
    one row anyway; a missing twofa_enabled just means 2FA-off).

    2026-08-03: the LADDER still swallows-and-retries (that is what makes an un-run mig 706/711
    harmless), but the FINAL rung — a pre-706 `org_id,super_admin` select that cannot fail for a
    schema reason — now RAISES IdentityBackendUnavailable instead of returning `[]`. Returning `[]`
    there was read upstream as "verified user with no app_users row", which skips the org_id rewrite
    and honors the CLIENT-SUPPLIED org_id: a DB hiccup used to silently downgrade tenant isolation.
    IDENTITY_BACKEND_503=0 restores the old `[]` exactly."""
    try:
        tbl = client.schema("storeops").table("app_users")
    except Exception as exc:               # cannot even build the query → the client itself is dead
        if not _identity_503():
            return []
        raise IdentityBackendUnavailable(exc)
    for cols in ("org_id,super_admin,is_default_org,role,twofa_enabled",
                 "org_id,super_admin,is_default_org,role",
                 "org_id,super_admin,is_default_org"):
        try:
            return (tbl.select(cols).eq("auth_id", uid).order("created_at").execute().data) or []
        except Exception:
            continue
    try:
        return (tbl.select("org_id,super_admin").eq("auth_id", uid).execute().data) or []
    except Exception as exc:
        if not _identity_503():
            return []                      # break-glass: pre-2026-08-03 behaviour, byte-for-byte
        raise IdentityBackendUnavailable(exc)


def _resolve_identity(token: str):
    """Verify the Supabase JWT and resolve the login's MEMBERSHIP set. Cached (positive only).

    Returns (authenticated, super_admin, member_orgs, default_org, org_info, uid):
      • org_info = {org_id: {"role","twofa_enabled"}} for the 2FA gate (additive; empty pre-migration).
      • uid = the auth account id (for verifying the 2FA marker binds to this login).
    The first four elements are UNCHANGED in meaning from before:
      (True,  True,  (...),      _,   …) — verified SUPER-ADMIN → NO rewrite, client org_id honored.
      (True,  False, (orgs...),  org, …) — verified normal user → rewrite org_id to the ACTIVE org.
      (True,  False, (),         None,…) — verified user with NO app_users row → no rewrite.
      (False, False, (),         None,…) — token missing/expired/unverifiable → caller must REJECT.

    RAISES `IdentityBackendUnavailable` (2026-08-03) when the token verified but the membership
    store could not be READ — the caller answers 503, never 401. It is never raised for a
    token problem, and never when there is no token at all (the caller skips this function).

    Blocking — call via to_thread. Negative results are NOT cached, so a transient Supabase hiccup
    never pins a good user out for the TTL, and an expired token re-checks on refresh. Nor is an
    IdentityBackendUnavailable cached: the next request re-tries, so recovery is automatic."""
    now = time.time()
    hit = _cache.get(token)
    if hit and hit[1] > now:
        return hit[0]
    # ── STEP 1 — TOKEN VERIFICATION. Missing / expired / forged / unverifiable, or the auth service
    # itself refusing: ALL of these are genuine authentication failures and return the SAME
    # unauthenticated tuple this function has always returned, so the caller emits the byte-identical
    # 401. This block is deliberately as broad as the old one — nothing about 401 behaviour moves.
    try:
        from app.core.database import get_supabase_admin
        resp = get_supabase_admin().auth.get_user(token)
        user = getattr(resp, "user", None) or resp
        uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    except Exception:
        return (False, False, (), None, {}, None)   # verification error / bad token → unauthenticated
    if not uid:
        return (False, False, (), None, {}, None)   # token did not resolve → not authenticated
    # ── STEP 2 — MEMBERSHIP FETCH. The token is GOOD from here on, so a failure below is NOT an
    # authentication problem: it is the membership store being unreachable. Raise, don't lie.
    try:
        from app.core.database import get_supabase
        client = get_supabase()
    except Exception as exc:
        if not _identity_503():
            return (False, False, (), None, {}, None)
        raise IdentityBackendUnavailable(exc)
    rows = _fetch_memberships(client, uid)          # may raise IdentityBackendUnavailable
    # ── STEP 3 — pure assembly over `rows`. Cannot touch I/O; an unexpected shape here falls back to
    # the historical unauthenticated tuple rather than escaping as a 500.
    try:
        super_admin = any(r.get("super_admin") for r in rows)
        member_orgs = tuple(r.get("org_id") for r in rows if r.get("org_id"))
        # default membership: the row flagged is_default_org, else the earliest (rows are ordered).
        default_org = next((r.get("org_id") for r in rows if r.get("is_default_org")),
                           (member_orgs[0] if member_orgs else None))
        org_info = {r.get("org_id"): {"role": r.get("role"), "twofa_enabled": bool(r.get("twofa_enabled"))}
                    for r in rows if r.get("org_id")}
    except Exception:
        return (False, False, (), None, {}, None)
    result = (True, super_admin, member_orgs, default_org, org_info, uid)
    _cache[token] = (result, now + _TTL)
    return result


def _pick_active_org(member_orgs, default_org, requested):
    """Resolve the tenant this request acts as. `requested` is the UNTRUSTED x-active-org header:
    honored ONLY if it names a tenant the login belongs to; otherwise fall back to the default
    membership. Empty member set (unprovisioned) ⇒ None ⇒ no rewrite (client org honored)."""
    if requested and requested in member_orgs:
        return requested
    return default_org


async def _reject_401(send):
    body = b'{"detail":"authentication required"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _reject_503(send):
    """Identity could not be resolved because the MEMBERSHIP STORE is unreachable — the caller's
    token was fine. Distinct body + code so the UI (and a human reading a log) can tell an outage
    apart from a dead session; Retry-After mirrors db_resilience.DatabaseUnavailable."""
    body = (b'{"detail":"Your sign-in could not be verified right now because a backend service is '
            b'temporarily unavailable. Nothing was changed - please retry in a moment.",'
            b'"code":"identity_backend_unavailable"}')
    await send({"type": "http.response.start", "status": 503,
                "headers": [(b"content-type", b"application/json"),
                            (b"retry-after", b"1"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


# Platform org the middleware files its own outage rows under (super-admins see every org's failures;
# attributing them to the CALLER-DECLARED org_id would let an unauthenticated caller spam another
# tenant's Failure Logs page). Env-overridable rather than hard-pinned.
_PLATFORM_ORG_ID = os.environ.get("PLATFORM_ORG_ID", "00000000-0000-0000-0000-000000000001")
_OUTAGE_LOG_MIN_GAP = 60.0        # seconds; one row per minute, not one per request
_last_outage_log = [0.0]          # list = mutable module state without a `global`


def _log_identity_outage(path: str, exc: BaseException) -> None:
    """Best-effort core.failure_log row for a membership-store outage. THROTTLED: during an outage
    every in-flight request would otherwise try to write, hammering the very database that just
    failed. Follows the existing core write pattern (run_for_tenant._log_failure / router._masked_500)
    and is wrapped end-to-end in try/except — a missing mig 112, or the DB still being down, must
    never turn this into a second failure. Writes NOTHING else, ever."""
    now = time.time()
    if now - _last_outage_log[0] < _OUTAGE_LOG_MIN_GAP:
        return
    _last_outage_log[0] = now
    try:
        from app.core.database import get_supabase
        get_supabase().schema("core").table("failure_log").insert({
            "org_id": _PLATFORM_ORG_ID,
            "category": "system_error",
            "severity": "error",
            "source": "core/tenant_middleware:_resolve_identity",
            "message": ("Sign-in could not be verified: the membership store (storeops.app_users) "
                        "was unreachable. Callers received 503, not 401.")[:1000],
            "detail": {"path": str(path)[:300], "error": str(exc)[:1200],
                       "error_type": type(exc).__name__,
                       "throttle_seconds": _OUTAGE_LOG_MIN_GAP},
            "remediation": ("This is an infrastructure fault, not a bad password. Check the database / "
                            "PostgREST health and the connection pool; requests recover on their own "
                            "once it does. Set IDENTITY_BACKEND_503=0 only as a break-glass - it "
                            "restores the old behaviour, in which this fault silently stopped "
                            "enforcing tenant scoping."),
        }).execute()
    except Exception:
        pass    # the store that just failed is the store we are logging to - never raise from here


class TenantScopeMiddleware:
    """Pure ASGI middleware (reliable scope mutation). Forces org_id=<token's org> on the query
    string, and (when REQUIRE_AUTH is on) rejects unauthenticated hits to non-public routes."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not _enabled():
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()
        if _is_public(path):
            # Allowlisting skips BOTH the auth requirement and the org_id rewrite, so a path that only
            # needs ONE public method must not hand every method away. `_PUBLIC_METHODS` scopes those:
            # /core/auth-config is public for GET only (the PUT flips the global enforce-login flag and
            # must never be anonymous), and the Meta webhook for GET+POST only. Any other method on a
            # scoped path falls through to the normal auth path + the handler's own gate.
            # (2026-08-05 security hardening.)
            if _public_method_ok(path, method):
                return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        auth = headers.get("authorization", "")
        token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
        try:
            ok, super_admin, member_orgs, default_org, org_info, uid = (
                (await asyncio.to_thread(_resolve_identity, token)) if token
                else (False, False, (), None, {}, None))
        except IdentityBackendUnavailable as exc:
            # The token verified; the membership store did not answer. Fail CLOSED with an honest
            # 503 (the request still never reaches a handler) instead of the old, silent 401.
            # NOTE: with no bearer token _resolve_identity is not called at all, so a tokenless
            # request can never take this branch - it still gets the byte-identical 401 below.
            await asyncio.to_thread(_log_identity_outage, path, exc)
            return await _reject_503(send)
        if not ok:
            # No valid identity. Kill switch OFF (REQUIRE_AUTH=0) ⇒ old pass-through (client org_id
            # honored). Kill switch ON (default) ⇒ reject — no request reaches a tenant's data
            # without proving which tenant it is.
            if _require_auth():
                return await _reject_401(send)
            return await self.app(scope, receive, send)
        if super_admin:
            # Super-admin ⇒ no rewrite; the client-supplied org_id is honored (cross-tenant admin).
            return await self.app(scope, receive, send)
        if not member_orgs:
            # H2 (2026-08-05): the token VERIFIED but the login has NO tenant membership (no app_users
            # row). There is no org to rewrite to, so the OLD code fell through and honored the
            # CLIENT-SUPPLIED org_id — a verified-but-unprovisioned account could read/write any tenant
            # it named. Fail CLOSED (401). The pre-login / self-provisioning routes a fresh login needs
            # (/core/me, /core/bootstrap, /core/my-tenants, /core/signup, /core/connect-tenant callers
            # already hold a membership) are on the public allowlist and returned above, so this only
            # blocks membership-less access to PROTECTED tenant data. Break-glass: STRICT_MEMBERSHIP=0.
            if _strict_membership():
                return await _reject_401(send)
            return await self.app(scope, receive, send)
        # Normal login: honor the caller's chosen tenant ONLY if it is one of their memberships,
        # else fall back to their default membership. Empty membership ⇒ no rewrite (unprovisioned).
        requested = (headers.get(_ACTIVE_ORG_HEADER, "") or "").strip()
        org = _pick_active_org(member_orgs, default_org, requested)
        # 2FA gate (auth-hardening) — ADDITIVE, super-admins already returned above. Enforced only when
        # the global break-glass TWOFA_ENFORCE is on (default) AND the active tenant requires 2FA for
        # this user AND a valid x-2fa-token is absent. The OTP start/verify endpoints live under the
        # allowlisted /api/v1/core/me prefix, so a user can always obtain + submit a code. Any error /
        # un-run mig 711 → _tenant_needs_2fa is False → no-op (never a lockout). TWOFA_ENFORCE=0 kills it.
        if org and _twofa_enforce():
            info = org_info.get(org) or {}
            if _tenant_needs_2fa(org, info.get("role"), info.get("twofa_enabled")):
                if not _twofa_marker_ok(headers.get("x-2fa-token", ""), uid, org):
                    return await _reject_2fa(send)
        if org:
            qs = parse_qs(scope.get("query_string", b"").decode(), keep_blank_values=True)
            qs["org_id"] = [org]   # override any client-supplied org_id with the resolved active tenant
            scope = {**scope, "query_string": urlencode(qs, doseq=True).encode()}
        return await self.app(scope, receive, send)
