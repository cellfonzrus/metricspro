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

2026-08-06 ADMIN "VIEW AS EMPLOYEE" (impersonation, owner directive) — ONE NEW BRANCH, ENTERED ONLY
WHEN THE REQUEST CARRIES THE `x-impersonate` HEADER:
Every request WITHOUT that header takes exactly the code path it took before this change (the header
is probed with a single cheap scan of the raw header list, before anything else is built), so the
blast radius on normal traffic is nil. WITH the header, the request is handled by
`_handle_impersonated`, which:
  • verifies the server-minted HMAC grant AND binds it to the caller's own verified Supabase token
    (`app/core/impersonation.resolve_request`) — a hand-forged header gets 401, never a fallback to
    "act as the admin";
  • PINS the acting tenant: the grant's org_id overrides both the `org_id` query param AND the
    `x-active-org` header, and the SUPER-ADMIN no-rewrite bypass is deliberately NOT taken. So an
    impersonated session cannot wander to another tenant even when the actor is a super-admin;
  • refuses the privilege-escalation surface outright (`is_forbidden_while_impersonating`);
  • FAIL-CLOSED journals every MUTATING request to core.impersonation_action BEFORE the handler runs,
    so anything written while wearing someone else's face is attributable to the real human;
  • publishes the context that `core.router._uid_from_token` reads to swap the EFFECTIVE identity to
    the target, and that `require_target_reauth()` reads to gate clock-in / clock-out.
The 2FA gate is skipped inside an impersonated session by design: the marker belongs to the ACTOR's
login (already satisfied to obtain the grant), and the target's second factor is not the actor's to
present. Impersonation is authorized by an explicit, default-deny role permission plus a DB session
row, not by the target's 2FA.
"""
import os
import time
import asyncio
import contextvars
from urllib.parse import parse_qs, urlencode

# Exact full paths that are public (matched literally, no prefix semantics).
_PUBLIC_EXACT = frozenset({
    "/health",                            # Railway / uptime health probe (no auth by design)
    "/openapi.json",                      # OpenAPI schema backing the docs UIs (currently open)
    "/api/v1/core/auth-config",           # login-enforcement flag read by the login/layout BEFORE sign-in
    "/api/v1/core/signup",                # self-serve tenant signup (env-gated SIGNUPS_OPEN; anonymous)
    "/api/v1/core/signup-status",         # /signup page checks whether signups are open, pre-login
    "/api/v1/billing/public-pricing",     # PUBLIC price list + trial terms read by the marketing
                                          # site BEFORE any login exists (mig 907). METHOD-SCOPED to
                                          # GET below — every price-EDITING sibling under
                                          # /billing/pricing/* stays super-admin-gated and is not
                                          # matched here (exact path, no prefix semantics).
    "/api/v1/core/tenants/sync",          # dual-auth: NOTIFY_RUN_SECRET header OR super-admin; cron has no JWT
    "/api/v1/core/password-policy/public",  # PUBLIC: owner DEFAULT policy for pre-login strength hints
    "/api/v1/core/auth/forgot-password",  # PUBLIC self-serve reset request (anti-enumeration; anonymous)
    "/api/v1/core/auth/reset-password",   # PUBLIC self-serve reset completion (code-gated; anonymous)
    "/api/v1/core/auth/login-precheck",   # PUBLIC pre-login soft-lockout check (mig 859; anonymous)
    "/api/v1/core/auth/login-record",     # PUBLIC pre-login attempt ledger write (mig 859; anonymous)
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
    "/api/v1/referral/redeem",             # PUBLIC QR redemption (mig 850): the HMAC capability token IS
                                           # the auth — it authorizes redeeming ONLY the one referral it
                                           # signs. Boundary-matched, so ONLY /api/v1/referral/redeem[/…]
                                           # is public; every other /referral/* route keeps full auth +
                                           # org_id rewrite. Any bad/expired/used token → uniform 404
                                           # (no enumeration oracle), exactly like /notify/dl above.
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
    "/api/v1/vision/edge",                  # Vision edge analyzer (mig 900): DUAL-AUTH, same shape as
                                           # /core/fix-pipeline above. The analyzer is a machine on a
                                           # store network with NO login, so the JWT requirement would
                                           # fire before the handler could check the credential it DOES
                                           # carry: a per-agent HMAC-SHA256 over `timestamp.body` with a
                                           # bounded clock skew. EVERY route under this prefix
                                           # self-gates in vision/router.py::_authenticate_agent
                                           # (default DENY; unknown agent / disabled agent / bad
                                           # signature / stale timestamp all return an identical 401 so
                                           # a probe learns nothing) and resolves its own org FROM THE
                                           # AGENT RECORD, because allowlisting also skips the org_id
                                           # rewrite. Boundary-matched, so ONLY /api/v1/vision/edge[/…]
                                           # is affected — every other /vision/* route keeps full auth
                                           # + org_id rewrite.
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

# ── Admin "view as employee" (2026-08-06) ───────────────────────────────────────────────────────
# The impersonation grant + the single-use "the employee just typed their password" marker. Both are
# server-minted and server-verified; see app/core/impersonation.py for the mechanism + threat model.
from app.core import impersonation as _imp                       # noqa: E402  (module-level, no cycle)

_IMPERSONATE_HEADER = _imp.IMPERSONATE_HEADER                     # "x-impersonate"
_REAUTH_HEADER = _imp.REAUTH_HEADER                               # "x-impersonate-reauth"


def _raw_header(scope, name: str) -> str:
    """Cheap single-key probe over the RAW ASGI header list. Used to answer "is this an impersonated
    request?" without building the full headers dict, so the 100% case (no header) stays byte-identical
    in cost as well as in behaviour."""
    want = name.encode()
    for k, v in (scope.get("headers") or ()):
        if k.lower() == want:
            try:
                return v.decode().strip()
            except Exception:
                return ""
    return ""


def _client_ip_from(scope, headers) -> str:
    fwd = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd[:64]
    cl = scope.get("client") or ()
    return (str(cl[0]) if cl else "")[:64]


async def _reject_impersonation(send, reason: str):
    """A present-but-unusable impersonation grant. ALWAYS a rejection — there is deliberately no
    "carry on as the admin" outcome, because the UI would still be showing the impersonation banner
    while the session silently regained admin rights. `code` lets the client exit cleanly."""
    msg = {
        "not_actor": "This impersonation session does not belong to your login.",
        "ended": "That impersonation session has been ended.",
        "expired": "That impersonation session has expired.",
        "target_revoked": "That employee's access has been removed, so the session was ended.",
        "disabled": "Impersonation is turned off for this company.",
        "unavailable": ("Impersonation could not be verified right now because a backend service is "
                        "temporarily unavailable. Nothing was changed - please retry in a moment."),
    }.get(reason, "That impersonation session is no longer valid.")
    code = "impersonation_unavailable" if reason == "unavailable" else "impersonation_invalid"
    status = 503 if reason == "unavailable" else 401
    import json as _json
    body = _json.dumps({"detail": msg, "code": code}).encode()
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if status == 503:
        hdrs.append((b"retry-after", b"1"))
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": body})


async def _reject_impersonation_forbidden(send, path: str):
    body = (b'{"detail":"That action is not available while you are viewing the app as another '
            b'employee. Exit the impersonated session first.","code":"impersonation_forbidden"}')
    await send({"type": "http.response.start", "status": 403,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _reject_unattributable(send):
    """The write journal could not be written. A change made while wearing someone else's face that
    we cannot attribute to the real human must NOT happen — refuse rather than lose the audit."""
    body = (b'{"detail":"This change was blocked because the impersonation audit record could not be '
            b'written. Nothing was changed - please retry, or exit the impersonated session.",'
            b'"code":"impersonation_audit_unavailable"}')
    await send({"type": "http.response.start", "status": 503,
                "headers": [(b"content-type", b"application/json"),
                            (b"retry-after", b"1"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _with_active_org(scope, org: str):
    """Return a scope whose `x-active-org` header is FORCED to `org` (the grant's tenant). The
    client-supplied value is dropped, not merged: while impersonating, the tenant is not the
    caller's to choose."""
    want = _ACTIVE_ORG_HEADER.encode()
    hdrs = [(k, v) for (k, v) in (scope.get("headers") or ()) if k.lower() != want]
    hdrs.append((want, str(org).encode()))
    return {**scope, "headers": hdrs}


def _enabled() -> bool:
    return os.environ.get("MULTI_TENANT_ENFORCE", "").lower() in ("1", "true", "yes")


def _require_auth() -> bool:
    """Kill switch. Default ON when unset; REQUIRE_AUTH=0/false/no/off reverts to old pass-through."""
    return os.environ.get("REQUIRE_AUTH", "1").lower() not in ("0", "false", "no", "off")


def _identity_503() -> bool:
    """Break-glass for the 2026-08-03 honesty change. Default ON when unset; IDENTITY_BACKEND_503=0
    restores the old swallow-and-return-[] behaviour byte-for-byte (see module docstring)."""
    return os.environ.get("IDENTITY_BACKEND_503", "1").lower() not in ("0", "false", "no", "off")


def _ambiguous_tenant_strict() -> bool:
    """Break-glass for the 2026-08-09 ambiguous-tenant fail-closed. Default ON when unset;
    AMBIGUOUS_TENANT_STRICT=0 restores the old behaviour in which a login belonging to SEVERAL tenants,
    sending no `x-active-org` and having no `is_default_org` row, was silently answered as whichever
    membership happened to be created FIRST. Same never-strand-the-operator posture as REQUIRE_AUTH /
    STRICT_MEMBERSHIP / IDENTITY_BACKEND_503."""
    return os.environ.get("AMBIGUOUS_TENANT_STRICT", "1").lower() not in ("0", "false", "no", "off")


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


def _admin_2fa_enforce() -> bool:
    """Require a valid 2FA marker for SUPER-ADMIN requests (Security Controls Spec §1, item 10). Default
    OFF — behaviour-changing (a super-admin who hasn't enrolled 2FA would be prompted to), so an operator
    enables it deliberately after super-admins enroll. The 2FA enroll/verify endpoints live under the
    allowlisted /core/me prefix, so a super-admin can always reach them to obtain a marker. Because the
    marker EXPIRES (12h session / 30d device), this also TIME-BOXES the standing super-admin's elevated
    access — they must re-verify periodically. Break-glass ADMIN_2FA_ENFORCE=0."""
    return os.environ.get("ADMIN_2FA_ENFORCE", "").lower() in ("1", "true", "yes")


def _twofa_marker_uid_ok(token: str, uid: str) -> bool:
    """Valid 2FA marker for THIS login, any org — super-admins are cross-tenant, so the org the marker
    was minted for is not the point; 'did this human do 2FA recently' is. FAILS OPEN on verifier error."""
    try:
        from app.modules.core.auth_security import verify_2fa_token, now_ts
        p = verify_2fa_token(token, now_ts())
        return bool(p and p.get("a") == (uid or ""))
    except Exception:
        return True


async def _reject_2fa(send):
    body = b'{"detail":"two-factor authentication required","code":"2fa_required"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _reject_session(send, verdict: str):
    """Server-side session control (session_guard) ended this session. 401 so the client tears down
    and returns to login; distinct `code` lets the UI show "signed out for inactivity" vs "session
    expired" instead of a generic auth error."""
    if verdict == "absolute":
        detail, code = ("Your session has reached its maximum length. Please sign in again.",
                        "session_expired")
    else:
        detail, code = ("You were signed out due to inactivity. Please sign in again.",
                        "session_idle")
    import json as _json
    body = _json.dumps({"detail": detail, "code": code}).encode()
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _session_guard_verdict(token, uid, org, role, scope, headers):
    """Return 'ok' unless SESSION_ENFORCE is on AND the session has timed out. Best-effort and
    fail-open: any error resolves to 'ok' so a logging fault can never strand a valid login. Impersonated
    requests never reach here (they take `_handle_impersonated`), so they are unaffected by design."""
    try:
        from app.core import session_guard as _sg
        if not _sg.enforce():
            return "ok"
        sid = _sg.session_id_from_token(token, fallback_uid=uid)
        return await asyncio.to_thread(
            _sg.touch, sid, auth_id=uid, org_id=org, role=role,
            ip=_client_ip_from(scope, headers))
    except Exception:
        return "ok"


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
#   · /api/v1/billing/public-pricing — GET is the anonymous price list + trial terms the marketing
#     site renders pre-login. It is READ-ONLY by construction (the handler has no write path), and
#     scoping it here means any future method on that same path authenticates normally.
_PUBLIC_METHODS = {
    "/api/v1/core/auth-config": ("GET",),
    "/api/v1/billing/public-pricing": ("GET",),
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
        # The tenant this login may act as WITHOUT being told which one — i.e. a tenant it has
        # actually declared, not one we inferred. Two sources qualify: an explicit `is_default_org`
        # row, or having exactly ONE membership (then there is nothing to infer).
        #
        # WHAT CHANGED 2026-08-09: this used to fall back to `member_orgs[0]` — the EARLIEST-CREATED
        # membership — for ANY number of memberships. Verified live that same day: no app_users row
        # anywhere has is_default_org set, so in practice every multi-tenant login silently acted as
        # "whichever company you joined first". A login belonging to three companies was served its
        # oldest (26 stores, 138k sales rows) while the human believed they were in a brand-new empty
        # one. Now >1 membership with nothing declared yields None, which the caller reads as
        # AMBIGUOUS and refuses — see `_tenant_is_ambiguous`.
        default_org = next((r.get("org_id") for r in rows if r.get("is_default_org")),
                           (member_orgs[0] if len(member_orgs) == 1 else None))
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


# ── ACTING TENANT, PUBLISHED TO THE HANDLERS (2026-08-09 cross-tenant fix) ──────────────────────
# The middleware already resolves WHICH tenant a request acts as, but it only ever expressed that by
# rewriting the `org_id` QUERY PARAM. An endpoint that takes no org_id param therefore could not see
# the answer, and several token-gated endpoints re-derived the tenant themselves with
#
#     .table("app_users").select(...).eq("auth_id", uid).limit(1)
#
# — no ORDER BY, no tenant preference, and the org read OUT of whichever row came back. For the 4 of
# 96 logins that belong to more than one tenant that is a coin toss, and it leaked: a NON-super-admin
# member of three orgs, acting as one, was served another's full employee roster (and the same gate
# gates the decrypted SSN / bank reveal). These contextvars publish the already-validated answer so a
# handler never has to guess. Set once per request, on every authenticated path.
_ACTING_ORG: contextvars.ContextVar = contextvars.ContextVar("mp_acting_org", default=None)
_ACTING_SUPER_ADMIN: contextvars.ContextVar = contextvars.ContextVar("mp_acting_super_admin", default=False)
# The resolved human for THIS request ({uid, role, super_admin, email?}) — published for the access log.
_ACTING_ACTOR: contextvars.ContextVar = contextvars.ContextVar("mp_acting_actor", default=None)


def _set_actor(actor):
    _ACTING_ACTOR.set(actor or None)


def _get_actor():
    return _ACTING_ACTOR.get()


class TenantChoiceRequired(Exception):
    """The login belongs to several tenants and did not name a valid one — refuse, never guess.
    Mirrors `_tenant_is_ambiguous`, for handlers the middleware's own rejection cannot cover."""


def _set_acting(org, super_admin=False):
    _ACTING_ORG.set(org or None)
    _ACTING_SUPER_ADMIN.set(bool(super_admin))


def acting_org():
    """The tenant THIS request acts as, as already validated by the middleware (None when unknown —
    an unauthenticated/public/enforcement-off request)."""
    return _ACTING_ORG.get()


def caller_app_user(uid: str, columns: str = "org_id,email,role,super_admin"):
    """The caller's storeops.app_users row FOR THE TENANT THIS REQUEST ACTS AS.

    Replaces the unsafe `.eq("auth_id", uid).limit(1)` shape everywhere the org came OUT of the row.
    Here the tenant is an INPUT — taken from the middleware's validated resolution — so a login that
    belongs to several tenants can only ever be handed the one it is actually acting as.

      • acting tenant known  → the row for (auth_id, that org). No row ⇒ None ⇒ the caller 403s,
        EXCEPT for a super-admin, whose cross-tenant administration is intentional
        (see the `super_admin` branch in the dispatcher): they get their own row with org_id pinned
        to the tenant they are administering.
      • acting tenant unknown (enforcement off / no token) → exactly one membership is unambiguous
        and is returned; several memberships raise TenantChoiceRequired rather than picking one.

    Returns a dict or None. Blocking (PostgREST) — call it from a sync handler, as all callers do."""
    from app.core.database import get_supabase
    tbl = get_supabase().schema("storeops").table("app_users")
    acting = _ACTING_ORG.get()
    if acting:
        rows = (tbl.select(columns).eq("auth_id", uid)
                .eq("org_id", acting).limit(1).execute().data) or []
        if rows:
            return rows[0]
        if not _ACTING_SUPER_ADMIN.get():
            return None
        own = (tbl.select(columns).eq("auth_id", uid)
               .order("org_id").limit(1).execute().data) or []
        if not own:
            return None
        row = dict(own[0])
        if "org_id" in row:
            row["org_id"] = acting      # a super-admin acts on the tenant they chose
        return row
    rows = (tbl.select(columns).eq("auth_id", uid).order("org_id").execute().data) or []
    if not rows:
        return None
    if len(rows) > 1:
        raise TenantChoiceRequired()
    return rows[0]


def caller_app_user_http(uid: str, columns: str = "org_id,email,role,super_admin"):
    """`caller_app_user` for FastAPI handlers: an ambiguous tenant becomes a 409 telling the caller
    to choose, never a silent guess. Returns the row, or None so the caller can 403 in its own words."""
    from fastapi import HTTPException
    try:
        return caller_app_user(uid, columns)
    except TenantChoiceRequired:
        raise HTTPException(409, "Your login belongs to more than one company. "
                                 "Choose which one you are working in, then try again.")


def _tenant_is_ambiguous(member_orgs, requested, default_org) -> bool:
    """PURE. Is the acting tenant a GUESS rather than a choice?

    True only when the login belongs to at least one tenant, did NOT name a valid one via
    `x-active-org`, and has no tenant it may act as without being told (`default_org` is None — see
    `_resolve_identity`, which yields None exactly when there are several memberships and none is
    flagged `is_default_org`).

    A SINGLE-membership login is never ambiguous: its one tenant is the only possible answer, so the
    92 of 96 logins that were single-membership when this shipped are completely untouched. A login
    with ZERO memberships is not this function's problem either — the fail-closed 401 for an
    unprovisioned account (H2, 2026-08-05) already ran above.

    A header naming a tenant the login does NOT belong to counts as ambiguous rather than being quietly
    downgraded to some other tenant. Answering a request for company X with company Y's data is the
    precise shape of the bug this exists to prevent.
    """
    if not member_orgs:
        return False
    if requested and requested in member_orgs:
        return False
    return default_org is None


async def _reject_tenant_choice(send):
    """The login belongs to several companies and has not said which one it is acting as.

    409, deliberately NOT 401: the session is perfectly valid and must not be torn down. The client
    keeps its token, shows the company picker it already has, and retries. `code` is what the client
    keys on; the prose is for whoever reads a log."""
    body = (b'{"detail":"This sign-in belongs to more than one company. Choose which company you are '
            b'working in, then try again.","code":"tenant_choice_required"}')
    await send({"type": "http.response.start", "status": 409,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


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

    async def _handle_impersonated(self, scope, receive, send, path, method, imp_raw):
        """The ONLY code path an `x-impersonate` request can take. Every exit is a rejection or a
        fully-pinned, fully-journalled call into the app — never a silent downgrade to admin rights."""
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        auth = headers.get("authorization", "")
        token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
        # The grant is bound to the ACTOR's login, so a valid Supabase token is mandatory even on a
        # path that would otherwise be public.
        try:
            ok, _sa, _orgs, _def, _info, uid = (
                (await asyncio.to_thread(_resolve_identity, token)) if token
                else (False, False, (), None, {}, None))
        except IdentityBackendUnavailable as exc:
            await asyncio.to_thread(_log_identity_outage, path, exc)
            return await _reject_503(send)
        if not ok or not uid:
            return await _reject_401(send)
        ctx, reason = await asyncio.to_thread(
            _imp.resolve_request, imp_raw, uid, headers.get(_REAUTH_HEADER, ""))
        if not ctx:
            return await _reject_impersonation(send, reason)
        # Privilege-escalation surface: refused outright for a borrowed identity.
        if _imp.is_forbidden_while_impersonating(path, method):
            return await _reject_impersonation_forbidden(send, path)
        org = ctx["org_id"]
        # The grant pins the tenant; an impersonated request is never the actor's to redirect. The
        # ACTOR's own super-admin standing is carried through unchanged, so pinning the tenant here
        # cannot newly 403 an admin who was already allowed in — privilege reduction while
        # impersonating stays the job of `is_forbidden_while_impersonating`, above, not of this line.
        _set_acting(org, super_admin=bool(_sa))
        # PIN the tenant on BOTH channels. Note the deliberate omission: the super-admin
        # "no rewrite, client org_id is honored" bypass is NOT taken here.
        qs = parse_qs(scope.get("query_string", b"").decode(), keep_blank_values=True)
        qs["org_id"] = [org]
        scope = _with_active_org({**scope, "query_string": urlencode(qs, doseq=True).encode()}, org)
        # Attribution: pre-write the journal row for every MUTATING request, FAIL CLOSED.
        journal_id = None
        if _imp.should_journal(method):
            try:
                journal_id = await asyncio.to_thread(
                    _imp.log_event, kind="write", ctx=ctx, method=method, path=path,
                    query=scope.get("query_string", b"").decode()[:600],
                    ip=_client_ip_from(scope, headers),
                    user_agent=headers.get("user-agent", ""), fail_closed=True)
            except Exception:
                return await _reject_unattributable(send)
        status_holder = [0]

        async def _send(message):
            if message.get("type") == "http.response.start":
                status_holder[0] = int(message.get("status") or 0)
            await send(message)

        ctx_token = _imp.set_current(ctx)
        try:
            return await self.app(scope, receive, _send)
        finally:
            _imp.reset_current(ctx_token)
            if journal_id:
                try:
                    await asyncio.to_thread(_imp.finish_event, journal_id, status_holder[0])
                except Exception:
                    pass

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not _enabled():
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()
        # ── ADMIN "VIEW AS EMPLOYEE" ─────────────────────────────────────────────────────────────
        # Probed FIRST and with a single cheap raw-header scan: when the header is absent (every
        # request in normal operation) this is one comparison and the rest of this function runs
        # exactly as it did before. When it is present the request is handled entirely by
        # _handle_impersonated — including for allowlisted/public paths such as /core/me, which MUST
        # resolve as the target or the UI would render the admin's own profile behind the banner.
        _imp_raw = _raw_header(scope, _IMPERSONATE_HEADER)
        if _imp_raw:
            return await self._handle_impersonated(scope, receive, send, path, method, _imp_raw)
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
        # ── SESSION CONTROLS (Security Controls Spec §1) ─────────────────────────────────────────
        # Identity is valid from here. When SESSION_ENFORCE is on, apply the idle + absolute-lifetime
        # guard once, before any branch dispatches — so it covers super-admin, membership-less and
        # normal logins alike. Default OFF ⇒ this returns 'ok' with no DB work, zero behaviour change.
        _sess_role = "super_admin" if super_admin else (org_info.get(default_org) or {}).get("role")
        _sv = await _session_guard_verdict(token, uid, default_org, _sess_role, scope, headers)
        if _sv != "ok":
            return await _reject_session(send, _sv)
        if super_admin:
            # Super-admin ⇒ no rewrite; the client-supplied org_id is honored (cross-tenant admin).
            # Publish the tenant they declared so a token-gated handler administers THAT tenant
            # instead of silently falling back to whichever membership row sorted first.
            _set_acting((headers.get(_ACTIVE_ORG_HEADER, "") or "").strip() or None, super_admin=True)
            _set_actor({"uid": uid, "super_admin": True, "role": "super_admin"})
            # ADMIN 2FA (item 10): a standing super-admin no longer bypasses 2FA when the operator turns
            # this on. The marker's expiry time-boxes the elevated access. Enroll/verify are allowlisted,
            # so this never permanently strands an admin. Default OFF ⇒ unchanged bypass.
            if _admin_2fa_enforce() and not _twofa_marker_uid_ok(headers.get("x-2fa-token", ""), uid):
                return await _reject_2fa(send)
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
            _set_actor({"uid": uid})
            return await self.app(scope, receive, send)
        # Normal login: honor the caller's chosen tenant ONLY if it is one of their memberships,
        # else fall back to their default membership. Empty membership ⇒ no rewrite (unprovisioned).
        requested = (headers.get(_ACTIVE_ORG_HEADER, "") or "").strip()
        # AMBIGUOUS TENANT (2026-08-09): refuse to guess which company this request is for. Placed
        # before the 2FA gate and before the rewrite, so an ambiguous request never reaches a handler
        # and never has an org_id stamped on it. The login/bootstrap routes a fresh session needs are
        # on the public allowlist and returned far above, so the picker can always load.
        if _ambiguous_tenant_strict() and _tenant_is_ambiguous(member_orgs, requested, default_org):
            return await _reject_tenant_choice(send)
        org = _pick_active_org(member_orgs, default_org, requested)
        _set_acting(org, super_admin=False)
        _set_actor({"uid": uid, "role": (org_info.get(org) or {}).get("role")})
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
