"""PLATFORM OPERATOR CONSOLE — the endpoints (I/O only; every decision lives in `operator.py`).

Owner directive 2026-09-05. Mounted ONTO core/router.py's router (which already carries "/core"), the
same way `impersonation_api` is, so `main.py` needs no change and paths resolve to
`/api/v1/core/operator/*`. core.router is imported LAZILY inside functions — no import cycle.

DUPLICATE CHECK (CLAUDE.md build gate). Nothing here re-derives anything that already exists:
  · the tenant DIRECTORY is `GET /core/tenants` — the console calls it, it is not reimplemented;
  · the super-admin GATE is `core.router._require_super_admin` — `_authority()` calls it FIRST and
    only then enriches the answer with the operator registry. There is still exactly ONE gate;
  · tenant ENTRY reuses the cross-tenant switcher (`x-active-org` + the middleware's super-admin
    no-rewrite bypass). This module adds the record/expiry/banner around it, not a second bypass;
  · health lamps are the CONTROL BOX's (`/core/control-box`), linked to, never recomputed;
  · billing is the BILLING module's (`/admin/billing`, `/admin/pricing` and the per-tenant AI usage
    metering being built alongside). The console places and links it; it owns none of it.

DEGRADES GRACEFULLY (migrations 980/981 un-run). Every DB touch is try/except-guarded and every
read falls back to the value that reproduces TODAY's behaviour:
  · `core.platform_operator` missing        ⇒ operator_row=None  ⇒ legacy authority, unchanged;
  · `core.platform_operator_policy` missing ⇒ policy=None        ⇒ POLICY_DEFAULTS = today;
  · `core.operator_action` missing          ⇒ the audit write is skipped, and every WRITE endpoint
    that could not be audited REFUSES (fail-closed) — except the read-only ones, which still work.
So the console is simply absent before the migration and never removes anyone's access.

WHY THE AUDIT WRITE IS FAIL-CLOSED ON WRITES. `impersonation`'s journal set the precedent: a mutating
operator action that cannot be recorded does not happen. Reads are not journalled (volume) — the
existing `core.access_log` already carries every request with the operator's own auth id.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.database import get_supabase, get_supabase_admin
from app.core.schemas import LaxModel
from app.modules.core import operator as OP

router = APIRouter(prefix="/operator", tags=["Core / Platform Operator"])

_SCHEMA = "core"
_T_OPERATOR = "platform_operator"
_T_POLICY = "platform_operator_policy"
_T_ACTION = "operator_action"
_T_SESSION = "operator_entry_session"
_T_NOTICE = "platform_notice"
_T_DRILL = "restore_drill"


def sb():
    return get_supabase()


def adm():
    return get_supabase_admin()


# ── Bodies (LaxModel like the rest of core) ─────────────────────────────────────────────────────
class EnterIn(LaxModel):
    org_id: Any = None
    reason: Any = None
    minutes: Any = None


class ExitIn(LaxModel):
    session_id: Any = None


class OperatorIn(LaxModel):
    email: Any = None
    auth_id: Any = None
    operator_role: Any = None
    expires_at: Any = None
    notes: Any = None
    capabilities: Any = None


class PolicyIn(LaxModel):
    legacy_membership_flag_honored: Any = None
    require_entry_session: Any = None
    enforce_scoped_roles: Any = None
    entry_reason_required: Any = None
    entry_min_minutes: Any = None
    entry_max_minutes: Any = None
    entry_default_minutes: Any = None


class NoticeIn(LaxModel):
    severity: Any = None
    title: Any = None
    body: Any = None
    starts_at: Any = None
    ends_at: Any = None
    org_ids: Any = None


class NoticeOffIn(LaxModel):
    id: Any = None


class DrillIn(LaxModel):
    outcome: Any = None
    scope: Any = None
    performed_at: Any = None
    notes: Any = None


# ── Authority resolution — ONE gate, enriched ───────────────────────────────────────────────────
def _policy_row():
    """The singleton policy row, or None. NEVER raises: an un-run migration reads as None, which
    `effective_policy` turns into POLICY_DEFAULTS = today's behaviour."""
    try:
        rows = (adm().schema(_SCHEMA).table(_T_POLICY).select("*").limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _operator_row(auth_id):
    try:
        rows = (adm().schema(_SCHEMA).table(_T_OPERATOR).select("*")
                .eq("auth_id", auth_id).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _active_operator_count():
    """How many registry rows currently confer authority. Used ONLY by the lockout refusal, where
    an error must count as ZERO (the conservative direction: it blocks the cutover, never enables it)."""
    try:
        rows = (adm().schema(_SCHEMA).table(_T_OPERATOR).select("*").execute().data) or []
    except Exception:
        return 0
    return sum(1 for r in rows if OP.operator_row_active(r))


def _active_operator_rows():
    """Every registry row, for the decisions that must count CAPABILITY HOLDERS rather than bodies.
    An error reads as an EMPTY registry — the conservative direction, which refuses an access-cutting
    flip rather than permitting one."""
    try:
        return (adm().schema(_SCHEMA).table(_T_OPERATOR).select("*").execute().data) or []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SCOPED-ROLE ENFORCEMENT ON THE PRE-EXISTING SUPER-ADMIN ENDPOINTS  (mig 984)
# ══════════════════════════════════════════════════════════════════════════════════════════════
# `core.router._require_super_admin` — still THE one gate — calls `scoped_role_verdict` as a LAYER.
# Everything about this is designed so the answer is TODAY's answer until somebody deliberately
# turns it on, and so it can always be turned back off:
#   · OPERATOR_ENFORCE=0 in the environment kills both halves outright (the break-glass shape this
#     codebase already uses for TWOFA_ENFORCE / STRICT_MEMBERSHIP / REQUIRE_AUTH);
#   · a missing migration 984 column, an unreadable policy row, an unreadable registry, or an
#     unknown route each read as NOT ENFORCED;
#   · the reads are TTL-cached, so switching this on does not add a per-request round trip.
_ENFORCE_TTL = 30.0
_enf_cache: dict = {}


def _enforce_env_on() -> bool:
    import os
    return (os.getenv("OPERATOR_ENFORCE", "1") or "1").strip().lower() not in ("0", "false", "no")


def _cached(key, ttl, producer):
    import time
    now = time.time()
    hit = _enf_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    val = producer()
    _enf_cache[key] = (val, now + ttl)
    return val


def _route_overrides():
    """`core.operator_route_capability` rows (mig 984) — the per-platform override of the code
    default map (RULE TWO). Absent table ⇒ () ⇒ the house map alone."""
    def _read():
        try:
            return (adm().schema(_SCHEMA).table("operator_route_capability").select("*")
                    .eq("is_active", True).order("route_prefix").execute().data) or []
        except Exception:
            return []
    return _cached("routes", 300.0, _read)


def scoped_role_verdict(uid, *, legacy_super_admin, house_admin, path=None, method=None):
    """None when enforcement is OFF (the caller then keeps its own, unchanged verdict); otherwise the
    `operator.endpoint_decision` dict. NEVER raises — any failure returns None, i.e. today."""
    try:
        if not _enforce_env_on():
            return None
        pol = _cached("policy", _ENFORCE_TTL, _policy_row)
        if not OP.effective_policy(pol)["enforce_scoped_roles"]:
            return None
        if path is None:
            from app.core.tenant_middleware import current_route
            path, method = current_route()
        if not path:
            # The middleware never saw this request (a test client, a worker, a CLI). An unknown
            # route cannot be gated, so enforcement stands down rather than guessing.
            return None
        row = _cached("op:%s" % uid, _ENFORCE_TTL, lambda: _operator_row(uid))
        return OP.endpoint_decision(path=path, method=method, legacy_super_admin=bool(legacy_super_admin),
                                    operator_row=row, policy=pol, house_admin=bool(house_admin),
                                    route_map=_route_overrides())
    except Exception:
        return None


def _authority(authorization: str, active_org: str = ""):
    """(caller_membership, authority). Calls the EXISTING `_require_super_admin` first — so this
    module can never be a second, weaker way in — then unions the registry on top.

    A login the legacy gate refuses but the REGISTRY authorizes (post-cutover, or an operator who was
    never a tenant employee) still gets in: the 403 from the legacy gate is caught and re-decided by
    `resolve_authority`, which is the only place the two sources are combined. If BOTH refuse, the
    original 403 is what the caller sees."""
    from app.modules.core.router import _require_super_admin, _real_uid_from_token
    uid = _real_uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    legacy_ok, caller = False, {}
    try:
        caller = _require_super_admin(authorization, active_org) or {}
        legacy_ok = True
    except HTTPException:
        caller = {}
    row = _operator_row(uid)
    auth = OP.resolve_authority(legacy_super_admin=legacy_ok, operator_row=row, policy=_policy_row())
    if not auth["is_operator"]:
        raise HTTPException(403, auth["denied_reason"] or "super-admin only")
    caller = dict(caller or {})
    caller.setdefault("auth_id", uid)
    if not caller.get("email"):
        caller["email"] = (row or {}).get("email") or _email_for(uid)
    return caller, auth


def _email_for(uid):
    try:
        from app.modules.core.router import _email_for_uid
        return (_email_for_uid(sb(), uid) or "") or None
    except Exception:
        return None


def _need(auth, cap):
    if not OP.has_capability(auth, cap):
        raise HTTPException(403, "Your operator role does not include '%s'." % cap)


# ── The hash-chained audit write ────────────────────────────────────────────────────────────────
def _write_action(caller, action, *, target_org_id=None, target_ref=None, detail=None,
                  required=True):
    """Append one sealed row to `core.operator_action`. Returns the row, or None when the table is
    absent AND `required` is False.

    FAIL-CLOSED when `required` (every mutating action): a 503 rather than an unrecorded operator
    act. The sequence + previous hash are read immediately before the insert; a concurrent writer
    losing the race gets a unique-violation on `seq` and is retried once, after which it fails
    closed. That is deliberate — a chain with a guessed link is worse than a refused request."""
    for attempt in (0, 1):
        try:
            last = (adm().schema(_SCHEMA).table(_T_ACTION).select("seq,hash")
                    .order("seq", desc=True).limit(1).execute().data) or []
            prev_seq = int(last[0]["seq"]) if last else 0
            prev_hash = (last[0].get("hash") if last else None) or OP.GENESIS_HASH
            row = OP.audit_row(seq=prev_seq + 1,
                               actor_auth_id=caller.get("auth_id"), actor_email=caller.get("email"),
                               action=action, target_org_id=target_org_id, target_ref=target_ref,
                               detail=detail, prev_hash=prev_hash)
            adm().schema(_SCHEMA).table(_T_ACTION).insert(row).execute()
            return row
        except Exception as e:
            if attempt == 0:
                continue
            if required:
                raise HTTPException(503, "operator action could not be recorded, so it was not "
                                         "performed (is migration 980 applied?) — %s" % e)
            return None
    return None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# WHO AM I  ·  the console's own identity endpoint
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/me")
def operator_me(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The operator persona for this login: capabilities, scoped role, WHY they are authorized
    (`sources`), the effective policy, and the console nav derived from the capabilities.

    `sources` is the separation made visible: `legacy` means the authority is still riding on a
    tenant membership flag; `registry` means it is a platform identity of its own. The console shows
    this so the owner can watch the migration happen instead of guessing at it."""
    caller, auth = _authority(authorization, x_active_org)
    return {
        "auth_id": caller.get("auth_id"), "email": caller.get("email"),
        "is_operator": True, "operator_role": auth["operator_role"],
        "sources": list(auth["sources"]), "capabilities": sorted(auth["capabilities"]),
        "legacy_honored": auth["legacy_honored"],
        "policy": OP.effective_policy(_policy_row()),
        "sections": OP.console_sections(auth),
        "active_registry_operators": _active_operator_count(),
        "entry": _current_entry_payload(caller.get("auth_id")),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# TENANT ENTRY  ·  the audited wrapper around the switcher that already exists
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _known_org_ids():
    """Tenant ids from `storeops.tenants` — the SAME source `GET /core/tenants` reads. Used only to
    refuse an entry into a company that does not exist; returns None (= 'do not check') on error so a
    transient read fault cannot block an operator, since the org id is a uuid either way."""
    try:
        rows = (sb().schema("storeops").table("tenants").select("org_id,name")
                .order("org_id").execute().data) or []
        return {str(r["org_id"]): (r.get("name") or "") for r in rows if r.get("org_id")}
    except Exception:
        return None


def _open_session(auth_id):
    try:
        rows = (adm().schema(_SCHEMA).table(_T_SESSION).select("*")
                .eq("actor_auth_id", auth_id).is_("ended_at", "null")
                .order("started_at", desc=True).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _current_entry_payload(auth_id, names=None):
    s = _open_session(auth_id)
    if not s:
        return None
    if names is None:
        names = _known_org_ids() or {}
    return OP.banner_payload(s, tenant_name=names.get(str(s.get("org_id")), ""))


@router.get("/entry")
def current_entry(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The caller's OPEN entry session, shaped for the persistent banner — or `null`.

    Deliberately cheap and unauthenticated beyond the operator gate: the banner polls it, and a
    banner that fails to render is a safety regression (an operator inside a tenant with no visible
    indication is exactly what this feature exists to prevent)."""
    caller, _auth = _authority(authorization, x_active_org)
    return {"entry": _current_entry_payload(caller.get("auth_id"))}


@router.post("/enter")
def enter_tenant(body: EnterIn, request: Request = None, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    """Open an audited, time-boxed entry session into a tenant, then tell the client which org to
    switch to. THE OWNER'S "log in from the tenant list" — with the record it has never had.

    What actually happens: nothing about the caller's authority changes. They could already act as
    this tenant through the switcher. This writes (1) a `core.operator_entry_session` row with the
    reason and a hard expiry, and (2) a hash-chained `core.operator_action` row under the operator's
    OWN auth id and email. The client then sets `x-active-org` exactly as the switcher does, and the
    banner renders for as long as the session is live.

    Refuses, fail-closed, on: no `tenant.enter` capability, a target that is not a uuid, a target
    absent from the tenant directory, a missing reason, or an audit table it cannot write to. A
    refusal is ITSELF audited (`tenant.enter.denied`) — best-effort, so a missing table cannot make a
    denial fail open."""
    caller, auth = _authority(authorization, x_active_org)
    names = _known_org_ids()
    d = OP.entry_decision(authority=auth, target_org_id=body.org_id, reason=body.reason,
                          minutes=body.minutes, policy=_policy_row(),
                          known_org_ids=(set(names) if names is not None else None))
    if not d["allowed"]:
        _write_action(caller, "tenant.enter.denied", target_org_id=(str(body.org_id or "") or None),
                      detail={"code": d["code"], "message": d["message"]}, required=False)
        raise HTTPException(403 if d["code"] in ("forbidden",) else 400, d["message"])

    org = str(body.org_id).strip()
    # One open session per operator: entering a second tenant closes the first, so the banner can
    # never disagree with the acting org and the trail never shows two simultaneous tenancies.
    prior = _open_session(caller.get("auth_id"))
    if prior:
        _end_session(prior, "superseded")

    row = {"actor_auth_id": caller.get("auth_id"), "actor_email": caller.get("email"),
           "org_id": org, "reason": d["reason"], "expires_at": d["expires_at"],
           "source_ip": _ip(request), "user_agent": _ua(request)}
    try:
        ins = (adm().schema(_SCHEMA).table(_T_SESSION).insert(row).execute().data) or []
        session = ins[0] if ins else row
    except Exception as e:
        raise HTTPException(503, "entry session could not be opened (is migration 980 applied?) — %s" % e)

    _bust_entry_cache()
    _write_action(caller, "tenant.enter", target_org_id=org,
                  target_ref=(names or {}).get(org, ""),
                  detail={"reason": d["reason"], "minutes": d["minutes"],
                          "expires_at": d["expires_at"], "grants": list(d["grants"])})
    return {"ok": True, "org_id": org, "tenant_name": (names or {}).get(org, ""),
            "expires_at": d["expires_at"], "minutes": d["minutes"], "grants": list(d["grants"]),
            "session_id": session.get("id"),
            # The client sets this as x-active-org — the SAME header the switcher has always used.
            "active_org": org,
            "banner": OP.banner_payload(session, tenant_name=(names or {}).get(org, ""))}


def _bust_entry_cache():
    """Drop the middleware's short-lived entry-session cache in THIS process, so opening or closing a
    session takes effect on the very next request rather than at the end of a TTL. Best-effort: other
    workers pick it up within seconds anyway (a miss is cached for two seconds, never longer)."""
    try:
        from app.core import tenant_middleware as _tm
        _tm._entry_cache.clear()
    except Exception:
        pass


def _end_session(session, reason):
    from datetime import datetime, timezone
    try:
        (adm().schema(_SCHEMA).table(_T_SESSION)
         .update({"ended_at": datetime.now(timezone.utc).isoformat(), "ended_reason": reason})
         .eq("id", session.get("id")).execute())
        return True
    except Exception:
        return False


@router.post("/exit")
def exit_tenant(body: ExitIn, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Close the entry session and hand the operator back to their own console.

    Idempotent: exiting when nothing is open is `{"ok": true, "ended": false}`, never an error — the
    banner's Exit button must always succeed, exactly as the impersonation banner's does."""
    caller, _auth = _authority(authorization, x_active_org)
    s = _open_session(caller.get("auth_id"))
    if not s:
        return {"ok": True, "ended": False}
    _end_session(s, "operator_exit")
    _bust_entry_cache()
    _write_action(caller, "tenant.exit", target_org_id=str(s.get("org_id") or "") or None,
                  detail={"session_id": str(s.get("id") or "")}, required=False)
    return {"ok": True, "ended": True, "org_id": s.get("org_id")}


def _ip(request):
    try:
        fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        return (fwd or (request.client.host if request.client else ""))[:64]
    except Exception:
        return ""


def _ua(request):
    try:
        return (request.headers.get("user-agent") or "")[:300]
    except Exception:
        return ""


@router.get("/entry-log")
def entry_log(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
              org_id: str = "", limit: int = 200):
    """Every tenant-entry session, newest first — who entered which company, why, and for how long.
    Optionally filtered to one tenant, which is what a tenant's own admins would be shown."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_AUDIT_READ)
    try:
        q = (adm().schema(_SCHEMA).table(_T_SESSION).select("*").order("started_at", desc=True))
        if org_id:
            q = q.eq("org_id", org_id)
        rows = (q.limit(min(max(int(limit or 200), 1), 2000)).execute().data) or []
    except Exception as e:
        return {"rows": [], "ready": False, "error": "%s (is migration 980 applied?)" % e}
    for r in rows:
        r["state"] = OP.session_state(r)
    return {"rows": rows, "count": len(rows)}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE OPERATOR TRAIL  ·  hash-chained, with its own verification
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/audit")
def operator_audit(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                   limit: int = 500, actor: str = "", org_id: str = ""):
    """The operator action log + the CHAIN VERDICT over the whole chain.

    `chain` is computed over EVERY row (not the filtered page) — a tamper check on a filtered subset
    would report a break for every filtered-out row and be useless. The page itself is filtered."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_AUDIT_READ)
    try:
        all_rows = (adm().schema(_SCHEMA).table(_T_ACTION).select("*")
                    .order("seq").limit(20000).execute().data) or []
    except Exception as e:
        return {"rows": [], "chain": None, "ready": False,
                "error": "%s (is migration 980 applied?)" % e}
    chain = OP.verify_chain(all_rows)
    rows = list(reversed(all_rows))
    if actor:
        rows = [r for r in rows if (r.get("actor_email") or "") == actor
                or str(r.get("actor_auth_id") or "") == actor]
    if org_id:
        rows = [r for r in rows if str(r.get("target_org_id") or "") == org_id]
    return {"rows": rows[:min(max(int(limit or 500), 1), 5000)], "chain": chain,
            "count": len(rows), "total": len(all_rows)}


@router.get("/anomalies")
def operator_anomalies(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                       lookback: int = 5000):
    """Findings over the operator's own trail — bursts, tenant fan-out, runs of refusals. PURE
    detection (`operator.anomalies`); this endpoint only fetches and hands over the rows."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_AUDIT_READ)
    try:
        rows = (adm().schema(_SCHEMA).table(_T_ACTION).select("*")
                .order("seq", desc=True).limit(min(max(int(lookback or 5000), 1), 20000))
                .execute().data) or []
    except Exception as e:
        return {"findings": [], "ready": False, "error": "%s (is migration 980 applied?)" % e}
    return {"findings": OP.anomalies(rows, policy=_policy_row()), "scanned": len(rows)}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE OPERATOR ROSTER  ·  platform identities, scoped and time-boxed
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/roster")
def operator_roster(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Who holds platform authority — from BOTH sources, side by side.

    `registry` is the new, separated identity; `legacy` is every `app_users.super_admin` login that
    still gets in through a tenant membership flag. Showing them together is the whole point: the
    owner can see exactly who would lose access at the cutover, and fix it BEFORE flipping."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_OPERATOR_READ)
    reg = []
    try:
        reg = (adm().schema(_SCHEMA).table(_T_OPERATOR).select("*").order("email").execute().data) or []
    except Exception:
        reg = []
    for r in reg:
        r["active"] = OP.operator_row_active(r)
        r["capabilities"] = sorted(OP.role_capabilities(r.get("operator_role"), r.get("capabilities")))
    legacy = []
    try:
        legacy = (sb().schema("storeops").table("app_users")
                  .select("email,full_name,org_id,role,is_active,last_login,auth_id")
                  .eq("super_admin", True).order("email").execute().data) or []
    except Exception:
        legacy = []
    reg_ids = {str(r.get("auth_id")) for r in reg if OP.operator_row_active(r)}
    for l in legacy:
        # THE SEPARATION READOUT: a legacy super-admin with no active registry row is someone the
        # cutover would lock out. The UI flags exactly these.
        l["has_registry_record"] = str(l.get("auth_id")) in reg_ids
    return {"registry": reg, "legacy_super_admins": legacy,
            "roles": {k: sorted(v) for k, v in OP.OPERATOR_ROLES.items()},
            "capabilities": sorted(OP.ALL_CAPABILITIES),
            "policy": OP.effective_policy(_policy_row()),
            "would_lose_access_at_cutover": [l for l in legacy if not l["has_registry_record"]]}


@router.post("/roster")
def upsert_operator(body: OperatorIn, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Grant or update a PLATFORM operator identity (idempotent upsert on auth_id).

    ADDITIVE ONLY — this never creates a login and never touches `app_users.super_admin`. The
    subject must already have a login (resolved by email through the existing `app_users` rows), so
    this endpoint cannot mint an account. `expires_at` is the just-in-time, time-boxed elevation:
    leave it empty for a standing operator, set it for "engineering, until Friday"."""
    caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_OPERATOR_WRITE)
    email = str(body.email or "").strip().lower()
    auth_id = str(body.auth_id or "").strip()
    role = str(body.operator_role or OP.DEFAULT_OPERATOR_ROLE).strip().lower()
    if role not in OP.OPERATOR_ROLES:
        raise HTTPException(400, "operator_role must be one of: %s" % ", ".join(sorted(OP.OPERATOR_ROLES)))
    if not auth_id:
        if not email:
            raise HTTPException(400, "email or auth_id required")
        try:
            rows = (sb().schema("storeops").table("app_users").select("auth_id,email,full_name")
                    .eq("email", email).limit(5).execute().data) or []
        except Exception:
            rows = []
        rows = [r for r in rows if r.get("auth_id")]
        if not rows:
            raise HTTPException(404, "No login with that email. Create the login first — this page "
                                     "grants platform authority, it does not create accounts.")
        auth_id = str(rows[0]["auth_id"])
        email = email or (rows[0].get("email") or "")
    fields = {"auth_id": auth_id, "email": email or None, "operator_role": role,
              "is_active": True, "expires_at": (str(body.expires_at) if body.expires_at else None),
              "notes": (str(body.notes)[:500] if body.notes else None),
              "granted_by_auth_id": caller.get("auth_id"), "granted_by_email": caller.get("email")}
    if isinstance(body.capabilities, dict):
        fields["capabilities"] = {k: bool(v) for k, v in body.capabilities.items()
                                  if k in OP.ALL_CAPABILITIES}
    _write_action(caller, "operator.grant", target_ref=(email or auth_id),
                  detail={"operator_role": role, "expires_at": fields["expires_at"]})
    try:
        existing = (adm().schema(_SCHEMA).table(_T_OPERATOR).select("id")
                    .eq("auth_id", auth_id).limit(1).execute().data) or []
        if existing:
            adm().schema(_SCHEMA).table(_T_OPERATOR).update(fields).eq("auth_id", auth_id).execute()
        else:
            adm().schema(_SCHEMA).table(_T_OPERATOR).insert(fields).execute()
    except Exception as e:
        raise HTTPException(503, "could not write the operator record (is migration 980 applied?) — %s" % e)
    return {"ok": True, "auth_id": auth_id, "email": email, "operator_role": role}


@router.delete("/roster")
def revoke_operator(email: str = "", auth_id: str = "", authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Deactivate a platform operator identity.

    THE LOCKOUT REFUSAL, applied here too: when the platform has already stopped honoring the legacy
    tenant flag, the LAST active operator cannot be revoked — the same rule
    `core/router.py::revoke_super_admin` applies to the last super-admin. While the legacy flag is
    still honored the refusal is unnecessary (legacy authority remains), and is not applied."""
    caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_OPERATOR_WRITE)
    key, val = ("auth_id", auth_id.strip()) if auth_id.strip() else ("email", email.strip().lower())
    if not val:
        raise HTTPException(400, "email or auth_id required")
    pol = OP.effective_policy(_policy_row())
    if not pol["legacy_membership_flag_honored"] and _active_operator_count() <= 1:
        raise HTTPException(400, "cannot revoke the last platform operator while the legacy tenant "
                                 "super-admin flag is switched off — the platform would be locked out")
    _write_action(caller, "operator.revoke", target_ref=val)
    try:
        adm().schema(_SCHEMA).table(_T_OPERATOR).update({"is_active": False}).eq(key, val).execute()
    except Exception as e:
        raise HTTPException(503, "could not update the operator record — %s" % e)
    return {"ok": True, key: val, "revoked": True}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE POLICY  ·  including the one-line, reversible CUTOVER the owner performs themselves
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/policy")
def get_policy(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The effective operator policy + whether the cutover is currently SAFE to perform."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_POLICY_WRITE)
    pol = OP.effective_policy(_policy_row())
    n = _active_operator_count()
    probe = OP.policy_change_decision(current_policy=pol,
                                      requested={"legacy_membership_flag_honored": False},
                                      active_registry_operators=n)
    return {"policy": pol, "active_registry_operators": n,
            "cutover_allowed": probe["allowed"], "cutover_note": probe["message"],
            "defaults": OP.POLICY_DEFAULTS}


@router.post("/policy")
def set_policy(body: PolicyIn, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    """Change the operator policy. The ONLY dangerous field is `legacy_membership_flag_honored`, and
    `policy_change_decision` refuses to switch it off while no active registry operator exists.

    This endpoint is the explicit, reversible cutover step from the directive: deploying this code
    never flips it, and flipping it back is the same call with `true`."""
    caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_POLICY_WRITE)
    requested = {k: v for k, v in {
        "legacy_membership_flag_honored": body.legacy_membership_flag_honored,
        "require_entry_session": body.require_entry_session,
        "enforce_scoped_roles": body.enforce_scoped_roles,
        "entry_reason_required": body.entry_reason_required,
        "entry_min_minutes": body.entry_min_minutes,
        "entry_max_minutes": body.entry_max_minutes,
        "entry_default_minutes": body.entry_default_minutes,
    }.items() if v is not None}
    cur = OP.effective_policy(_policy_row())
    rows = _active_operator_rows()
    d = OP.policy_change_decision(current_policy=cur, requested=requested,
                                  active_registry_operators=_active_operator_count(),
                                  active_rows=rows)
    if not d["allowed"]:
        _write_action(caller, "policy.change.denied", detail={"code": d["code"],
                                                              "requested": requested}, required=False)
        raise HTTPException(400, d["message"])
    _write_action(caller, "policy.change", detail={"from": cur, "to": d["policy"]})
    try:
        _persist_policy(d["policy"])
    except Exception as e:
        raise HTTPException(503, "could not write the policy (is migration 980 applied?) — %s" % e)
    _enf_cache.pop("policy", None)      # the switch takes effect on the next request, not in 30s
    _bust_entry_cache()                 # …and so does `require_entry_session`
    return {"ok": True, "policy": d["policy"], "code": d["code"], "message": d["message"]}


# Columns migration 984 adds. A platform on 980 but not yet 984 must still be able to change its
# policy, so the write retries WITHOUT them rather than failing the whole call (half-applied state).
_POLICY_COLUMNS_984 = ("enforce_scoped_roles",)


def _persist_policy(policy):
    for drop in ((), _POLICY_COLUMNS_984):
        payload = {k: v for k, v in policy.items() if k not in drop}
        try:
            rows = (adm().schema(_SCHEMA).table(_T_POLICY).select("id").limit(1).execute().data) or []
            if rows:
                adm().schema(_SCHEMA).table(_T_POLICY).update(payload).eq("id", rows[0]["id"]).execute()
            else:
                adm().schema(_SCHEMA).table(_T_POLICY).insert(payload).execute()
            return
        except Exception:
            if drop:
                raise


@router.get("/enforcement")
def enforcement_state(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """OWNER PREVIEW for the scoped-role cutover (mig 984): what enforcement would change, per
    operator, BEFORE anything is switched on.

    Read-only. `would_lose` lists the mapped route prefixes each active operator would stop being
    able to reach; `full_reach` marks the operators enforcement cannot bite. `exempt_prefixes` is the
    escape hatch — the console (and therefore the way back off) is in it by construction."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_OPERATOR_READ)
    pol = OP.effective_policy(_policy_row())
    rows = _active_operator_rows()
    preview = OP.enforcement_preview(rows, policy=pol)
    probe = OP.policy_change_decision(current_policy=pol, requested={"enforce_scoped_roles": True},
                                      active_registry_operators=_active_operator_count(),
                                      active_rows=rows)
    entry_probe = OP.policy_change_decision(current_policy=pol,
                                            requested={"require_entry_session": True},
                                            active_registry_operators=_active_operator_count(),
                                            active_rows=rows)
    return {"preview": preview, "policy": pol,
            "enforcement_allowed": probe["allowed"], "enforcement_note": probe["message"],
            "require_entry_allowed": entry_probe["allowed"],
            "require_entry_note": entry_probe["message"],
            "route_overrides": _route_overrides(), "env_kill_switch_on": not _enforce_env_on()}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# PLATFORM STATUS NOTICES  ·  operator → tenants broadcast
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/notices")
def list_notices(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                 include_expired: bool = False):
    """Every notice the operator has published (live and, optionally, past)."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_NOTICE_WRITE)
    try:
        rows = (adm().schema(_SCHEMA).table(_T_NOTICE).select("*")
                .order("created_at", desc=True).limit(500).execute().data) or []
    except Exception as e:
        return {"rows": [], "ready": False, "error": "%s (is migration 981 applied?)" % e}
    for r in rows:
        r["live"] = OP.notice_visible(r)
    return {"rows": [r for r in rows if r["live"] or include_expired],
            "severities": list(OP.NOTICE_SEVERITIES)}


@router.post("/notices")
def publish_notice(body: NoticeIn, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    """Publish a platform status notice to every tenant, or to named tenants only.

    Targeting is by org_id (never by tenant NAME — RULE TWO), and an empty list means everyone."""
    caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_NOTICE_WRITE)
    sev = str(body.severity or "info").strip().lower()
    if sev not in OP.NOTICE_SEVERITIES:
        raise HTTPException(400, "severity must be one of: %s" % ", ".join(OP.NOTICE_SEVERITIES))
    title = str(body.title or "").strip()
    if len(title) < 3:
        raise HTTPException(400, "title required")
    orgs = [str(o) for o in (body.org_ids or []) if OP.is_org_id(o)]
    row = {"severity": sev, "title": title[:200], "body": OP.redact(str(body.body or ""))[:4000],
           "starts_at": (str(body.starts_at) if body.starts_at else None),
           "ends_at": (str(body.ends_at) if body.ends_at else None),
           "org_ids": orgs or None, "is_active": True,
           "created_by_auth_id": caller.get("auth_id"), "created_by_email": caller.get("email")}
    _write_action(caller, "notice.publish", detail={"severity": sev, "title": title[:200],
                                                    "tenants": len(orgs) or "all"})
    try:
        ins = (adm().schema(_SCHEMA).table(_T_NOTICE).insert(row).execute().data) or []
    except Exception as e:
        raise HTTPException(503, "could not publish (is migration 981 applied?) — %s" % e)
    return {"ok": True, "notice": (ins[0] if ins else row)}


@router.post("/notices/withdraw")
def withdraw_notice(body: NoticeOffIn, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Take a notice down immediately (soft — the row and its audit row stay)."""
    caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_NOTICE_WRITE)
    nid = str(body.id or "").strip()
    if not nid:
        raise HTTPException(400, "id required")
    _write_action(caller, "notice.withdraw", target_ref=nid)
    try:
        adm().schema(_SCHEMA).table(_T_NOTICE).update({"is_active": False}).eq("id", nid).execute()
    except Exception as e:
        raise HTTPException(503, "could not withdraw — %s" % e)
    return {"ok": True, "id": nid}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# RESTORE-DRILL ATTESTATION  ·  §20's declared UNMONITORED gap, made attestable
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/restore-drill")
def restore_drills(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Recorded backup/restore drills + the lamp the control box would show for them.

    §20 declares `db_backup_restore` UNMONITORED because backup health is not observable from the
    backend. It is, however, ATTESTABLE — and once attested, staleness is an ordinary heartbeat. The
    control box consumes this with NO code change (a `core.system_check` row of kind `heartbeat`
    pointed at `core.restore_drill.verified_at`); that row ships COMMENTED OUT in migration 981
    because switching it on makes the board honestly RED until the first drill is recorded."""
    _caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_CONTROL_BOX)
    try:
        rows = (adm().schema(_SCHEMA).table(_T_DRILL).select("*")
                .order("performed_at", desc=True).limit(100).execute().data) or []
    except Exception as e:
        return {"rows": [], "ready": False, "lamp": "unknown",
                "error": "%s (is migration 981 applied?)" % e}
    lamp, why = OP.drill_lamp(rows[0] if rows else None)
    return {"rows": rows, "lamp": lamp, "reason": why}


@router.post("/restore-drill")
def record_restore_drill(body: DrillIn, authorization: str = Header(default=""),
                         x_active_org: str = Header(default="")):
    """Record that a backup restore was actually performed and what happened.

    Refuses a record that does not say WHAT was restored and whether it worked (`drill_record_valid`)
    — §20's honesty rule: a green lamp with nothing behind it is worse than an honest grey one."""
    caller, auth = _authority(authorization, x_active_org)
    _need(auth, OP.CAP_CONTROL_BOX)
    rec = {"outcome": body.outcome, "scope": body.scope, "performed_at": body.performed_at,
           "notes": body.notes}
    ok, why = OP.drill_record_valid(rec)
    if not ok:
        raise HTTPException(400, why)
    from app.modules.core.router import ORG_ID
    row = {"org_id": ORG_ID,        # the platform-wide drill lives on the house org (§20 convention)
           "outcome": str(rec["outcome"]).strip().lower(), "scope": str(rec["scope"])[:200],
           "performed_at": str(rec["performed_at"]), "verified_at": str(rec["performed_at"]),
           "notes": OP.redact(str(body.notes or ""))[:2000],
           "recorded_by_auth_id": caller.get("auth_id"), "recorded_by_email": caller.get("email")}
    _write_action(caller, "restore_drill.record", detail={"outcome": row["outcome"],
                                                          "scope": row["scope"]})
    try:
        adm().schema(_SCHEMA).table(_T_DRILL).insert(row).execute()
    except Exception as e:
        raise HTTPException(503, "could not record the drill (is migration 981 applied?) — %s" % e)
    lamp, why2 = OP.drill_lamp(row)
    return {"ok": True, "lamp": lamp, "reason": why2}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# TENANT-FACING READS  ·  mounted WITHOUT the /operator prefix
# ══════════════════════════════════════════════════════════════════════════════════════════════
# These are the only two endpoints in this module a NON-operator may call. They are what makes the
# feature honest from the tenant's side: a tenant can see the platform's status banner, and a
# tenant's own admins can see when a platform operator was inside their company.
public_router = APIRouter(tags=["Core / Platform Operator"])


@public_router.get("/platform-notice")
def platform_notice(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """LIVE platform status notices for the CALLING tenant. Any signed-in user.

    THE ONE CROSS-ORG SURFACE IN THIS MODULE, and it is shaped exactly like the control box's
    `/core/control-box/platform`: it returns only what the operator deliberately BROADCAST, never a
    figure belonging to another tenant. Targeting is applied server-side against the caller's own
    resolved org — a notice aimed at tenant A is invisible to tenant B, and the client never gets to
    say which org it is (§19.15, `harness_cross_tenant_isolation.py`).

    Fails SOFT: an un-run migration, a read error, or no notices all return an empty list, because a
    status banner that 500s the whole application is worse than no status banner."""
    from app.modules.core.router import _uid_from_token, _app_user_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    # The acting org is resolved from the VERIFIED membership, never from the request body.
    try:
        me = _app_user_from_token(authorization, x_active_org) or {}
        org = str(me.get("org_id") or "")
    except Exception:
        org = ""
    try:
        rows = (adm().schema(_SCHEMA).table(_T_NOTICE).select(
            "id,severity,title,body,starts_at,ends_at,org_ids,is_active")
            .eq("is_active", True).order("created_at", desc=True).limit(50).execute().data) or []
    except Exception:
        return {"notices": [], "lamp": "green", "ready": False}
    live = [n for n in rows if OP.notice_visible(n, org_id=org)]
    for n in live:
        n.pop("org_ids", None)      # a tenant never learns WHICH other tenants were targeted
    return {"notices": live, "lamp": OP.notice_lamp(live, org_id=org)}


@public_router.get("/tenant-operator-access")
def tenant_operator_access(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                           limit: int = 50):
    """"Who from the platform has been inside MY company" — for a TENANT's own admins.

    Industry-standard transparency (the notification-to-tenant-admins control): the tenant sees the
    operator's email, the reason they gave, when they entered and when the session ended. Scoped to
    the caller's OWN resolved org and to nothing else — the org is never taken from the client.

    Gated on the tenant's own admin rights (`_require_setting(..., 'security')`), the SAME gate the
    account-security surfaces already use, so this adds no new permission concept."""
    from app.modules.core.router import _require_setting, _app_user_from_token
    caller = _require_setting(authorization, (x_active_org or "").strip(), "security")
    org = str((caller or {}).get("org_id") or "")
    if not org:
        me = _app_user_from_token(authorization, x_active_org) or {}
        org = str(me.get("org_id") or "")
    if not OP.is_org_id(org):
        raise HTTPException(400, "no acting company resolved")
    try:
        rows = (adm().schema(_SCHEMA).table(_T_SESSION)
                .select("actor_email,reason,started_at,ended_at,expires_at,ended_reason,org_id")
                .eq("org_id", org).order("started_at", desc=True)
                .limit(min(max(int(limit or 50), 1), 500)).execute().data) or []
    except Exception as e:
        return {"rows": [], "ready": False, "error": "%s (is migration 980 applied?)" % e}
    for r in rows:
        r["state"] = OP.session_state(r)
        r.pop("org_id", None)
    return {"rows": rows, "count": len(rows)}
