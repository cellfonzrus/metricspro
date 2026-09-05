"""PLATFORM OPERATOR CONSOLE — the PURE decision layer (stdlib only, no I/O, no fastapi).

OWNER DIRECTIVE 2026-09-05 (sanjot@):
    "Need to separate the super admin access of Sanjot@cellfonzrus.com from Cellfonz r us tenant,
     make a separate view for the super admin but the option for the super admin to log in to any
     tenant from it is list of tenants dashboard an option to log in from there, Tennat billing
     dashboard will be another module on the super admin side, what other industry wide super admin
     controls are missing yet very import do a thorough research and add those also."

WHAT WAS ALREADY THERE (duplicate check, CLAUDE.md build gate — searched the index for
super-admin / tenant / impersonat / switch / audit before writing a line):

| Existing mechanism | Where | What this module does with it |
|---|---|---|
| `core.router._require_super_admin` — THE one super-admin gate | `core/router.py:553` | EXTENDED, never duplicated. `resolve_authority` is the pure decision it delegates to; the union it computes can only ever be a SUPERSET of today's answer (see NO-LOCKOUT below). |
| `GET /core/tenants` (the tenant list + per-tenant user/login counts) | `core/router.py:807` | REUSED VERBATIM as the console's tenant directory. No second tenant list is built. |
| the cross-tenant switcher (`x-active-org` + the middleware's super-admin no-rewrite bypass) | `frontend/src/lib/client.ts`, `core/tenant_middleware.py:928` | REUSED as the ENTRY MECHANISM. `entry_decision` wraps it with the reason / time-box / audit / banner it never had. Nothing new can reach a tenant that the switcher could not already reach. |
| `core.impersonation` "view as employee" (mig 730) | `app/core/impersonation.py` | UNTOUCHED and deliberately NOT extended. Entering a tenant and wearing an employee's face are different acts; see NOT AN ESCALATION below. |
| `core.access_log` (per-request who/path/status/IP) | `app/core/access_log.py` | Stays the per-request trail. The operator log here records INTENT (entered tenant X for reason Y until Z), which no per-request log can express. |
| `core.control_box` LAMPS / `redact` | `core/control_box.py` | IMPORTED, not re-implemented — the console's health surface and secret masking are the board's. |
| `app_users.super_admin` "cannot remove the LAST super-admin" refusal | `core/router.py:944` | The SAME safety idea, applied to the policy flip (`policy_change_decision`). |

THE SEPARATION MODEL — "operator" is an IDENTITY, not a membership flag
──────────────────────────────────────────────────────────────────────
Today platform authority is `storeops.app_users.super_admin`: a boolean on a row that also says
"this login is an employee of tenant T". The owner's platform power is therefore literally a column
on their CellfonzRUs employment record. That is the coupling to break.

The separated model is a row in `core.platform_operator`, keyed by AUTH ID and belonging to NO org.
It carries a SCOPED operator role (`owner` / `support` / `billing` / `engineering` / `readonly`) with
a capability set, and an optional `expires_at` (just-in-time, time-boxed elevation).

`resolve_authority` computes authority from BOTH sources and takes the UNION:

      authority = (legacy membership flag, when policy still honors it)  ∪  (registry row, when active)

so a login that is authorized today is authorized after every migration, and a login that is in the
registry is authorized even if the legacy flag is later cleared.

★ NO-LOCKOUT — the single most important property in this file ★
────────────────────────────────────────────────────────────────
This is the owner's own account on a live platform. Every one of these states MUST authorize the
existing super-admin, and `harness_operator_console.py` §A proves each one:

  1. PRE-MIGRATION       — `core.platform_operator` does not exist. The I/O layer catches, passes
                           `operator_row=None, policy=None`. Legacy honored (it is the DEFAULT) ⇒ IN.
  2. HALF-APPLIED        — table exists, EMPTY, no policy row. Same as (1) ⇒ IN.
  3. APPLIED + SEEDED    — the migration seeds one `owner` row per existing `app_users.super_admin`
                           (derived from DATA, never from a literal email — RULE TWO) ⇒ IN twice over.
  4. POLICY ROW GARBAGE  — unparseable / partial policy ⇒ every key falls back to its DEFAULT, and
                           the default honors legacy ⇒ IN.
  5. REGISTRY ROW EXPIRED/INACTIVE, legacy still on ⇒ IN via legacy.
  6. AFTER THE CUTOVER   — `legacy_membership_flag_honored=false` ⇒ IN via the seeded registry row.

  And the cutover itself cannot be the thing that locks anyone out: `policy_change_decision`
  REFUSES to stop honoring the legacy flag while ZERO active registry operators exist — the exact
  discipline `revoke_super_admin` already applies to the last super-admin. The flip is an explicit,
  reversible row UPDATE the owner performs; it is COMMENTED OUT in migration 980 and is never a
  consequence of deploying code.

CAPABILITIES ARE NEVER NARROWED BY THIS SHIPPING
────────────────────────────────────────────────
A login authorized ONLY by the legacy flag gets `ALL_CAPABILITIES` — byte-identical to the
all-powerful super-admin it is today. Scoping only ever applies to a login that has a registry row,
and the union with legacy means the registry can only ADD. Therefore no existing endpoint's answer
changes on the day this ships. ENFORCING scoped roles for existing surfaces (i.e. making `support`
genuinely unable to touch billing) is an authorization-semantics change and is PROPOSED, not shipped:
only the NEW console endpoints — which have no legacy behaviour to preserve — gate on capabilities.

TENANT ENTRY IS NOT AN ESCALATION
─────────────────────────────────
`entry_decision` returns permission to do exactly what the switcher already does: set the acting org.
It grants nothing else. Specifically it does NOT grant, and must never be read as granting, the
DEFAULT-DENY `impersonate` permission (`core/impersonation_api.py` — not implied by `scope:'all'`,
not by the `admin` module, and NOT by super-admin; there is no bypass). `ENTRY_GRANTS` is the
explicit, asserted list of what an entry session confers, and the harness fails if `impersonate`
ever appears in it. Entering LuxeLink as an operator lets you SEE LuxeLink as yourself, attributed to
your own identity in every log; it does not let you become one of their employees.

TAMPER-EVIDENT AUDIT
────────────────────
`core.operator_action` is append-only AND hash-chained: each row's `hash` covers its own canonical
payload plus the previous row's hash, so deleting or editing any row breaks the chain from that point
forward and `verify_chain` reports exactly where. This is what "immutable, tamper-evident admin-action
audit" means in practice on a database the operator also administers — it cannot PREVENT a service-role
edit, but it makes one undeniable. The migration additionally revokes UPDATE/DELETE.

Every helper here is PURE: same inputs ⇒ same outputs, no clock unless passed, no client, no raise on
malformed input (a bad shape degrades to the fail-CLOSED answer, never to an authorizing one).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

# `redact` and the lamp ladder are the control box's; importing keeps ONE definition of each
# (CLAUDE.md duplicate gate). control_box.py is pure stdlib too, so this stays harness-importable.
from app.modules.core.control_box import redact, worst_lamp  # noqa: F401  (re-exported for the API layer)

# ── Capabilities ────────────────────────────────────────────────────────────────────────────────
# One flat, stable vocabulary. A capability names a CONSOLE ABILITY, never a tenant or a person
# (RULE TWO). Adding one here and to a role below is the whole change; no branch anywhere says
# "if cellfonz" or "if sanjot@".
CAP_TENANT_READ = "tenant.read"           # see the tenant directory
CAP_TENANT_ENTER = "tenant.enter"         # open an audited entry session into a tenant
CAP_TENANT_LIFECYCLE = "tenant.lifecycle"  # provision / suspend / offboard (PROPOSED surfaces)
CAP_BILLING_READ = "billing.read"
CAP_BILLING_WRITE = "billing.write"
CAP_OPERATOR_READ = "operator.read"       # see the operator roster
CAP_OPERATOR_WRITE = "operator.write"     # grant / revoke operator identities
CAP_AUDIT_READ = "audit.read"
CAP_NOTICE_WRITE = "notice.write"         # publish a platform status notice
CAP_CONTROL_BOX = "control_box.read"
CAP_SECURITY = "security.write"           # IP blocks, session revocation, MFA policy
CAP_POLICY_WRITE = "policy.write"         # change the operator policy itself (incl. the cutover)

ALL_CAPABILITIES = frozenset({
    CAP_TENANT_READ, CAP_TENANT_ENTER, CAP_TENANT_LIFECYCLE, CAP_BILLING_READ, CAP_BILLING_WRITE,
    CAP_OPERATOR_READ, CAP_OPERATOR_WRITE, CAP_AUDIT_READ, CAP_NOTICE_WRITE, CAP_CONTROL_BOX,
    CAP_SECURITY, CAP_POLICY_WRITE,
})

# ── Scoped operator roles (industry standard: support ≠ billing ≠ engineering ≠ owner) ───────────
# `owner` is deliberately ALL_CAPABILITIES so that seeding today's super-admins as `owner` is a
# perfect no-op on their authority. The narrower roles exist so the owner can hire without handing
# out the keys to the platform.
OPERATOR_ROLES = {
    "owner": ALL_CAPABILITIES,
    "support": frozenset({CAP_TENANT_READ, CAP_TENANT_ENTER, CAP_AUDIT_READ, CAP_CONTROL_BOX}),
    "billing": frozenset({CAP_TENANT_READ, CAP_BILLING_READ, CAP_BILLING_WRITE, CAP_AUDIT_READ}),
    "engineering": frozenset({CAP_TENANT_READ, CAP_CONTROL_BOX, CAP_AUDIT_READ, CAP_NOTICE_WRITE}),
    "readonly": frozenset({CAP_TENANT_READ, CAP_AUDIT_READ, CAP_CONTROL_BOX}),
}
DEFAULT_OPERATOR_ROLE = "owner"

# What an entry session confers. ASSERTED by the harness — `impersonate` must never appear here.
ENTRY_GRANTS = ("acting_org",)

# ── Policy (RULE TWO: config with house defaults; an absent/garbage row behaves as TODAY) ────────
POLICY_DEFAULTS = {
    # THE CUTOVER SWITCH. True (default) = `app_users.super_admin` still grants platform authority,
    # exactly as it does today. The owner flips it to False, explicitly and reversibly, once the
    # registry is populated — and `policy_change_decision` refuses the flip if it would leave nobody.
    "legacy_membership_flag_honored": True,
    # False (default) = the cross-tenant switcher keeps working untouched. True = a super-admin must
    # open an entry session before acting as another tenant. ACCESS-CUTTING ⇒ default OFF, PROPOSED.
    "require_entry_session": False,
    "entry_reason_required": True,
    "entry_min_minutes": 5,
    "entry_max_minutes": 60,
    "entry_default_minutes": 30,
    # Anomaly thresholds (config, not constants in a branch).
    "anomaly_burst_actions": 25,      # actions by one operator …
    "anomaly_burst_minutes": 10,      # … inside this window
    "anomaly_fanout_tenants": 5,      # distinct tenants entered in a rolling day
    "anomaly_denied_streak": 5,       # consecutive refused attempts
}
_BOOL_KEYS = ("legacy_membership_flag_honored", "require_entry_session", "entry_reason_required")


def effective_policy(row):
    """POLICY_DEFAULTS overlaid by a stored row. NEVER raises: a None row, a non-dict, an unknown key,
    or a value of the wrong type falls back to the DEFAULT for that key.

    This is the first half of the no-lockout proof — a database that has never heard of this feature
    produces exactly `POLICY_DEFAULTS`, and `POLICY_DEFAULTS` describes today's behaviour."""
    out = dict(POLICY_DEFAULTS)
    if not isinstance(row, dict):
        return out
    for k, default in POLICY_DEFAULTS.items():
        if k not in row or row[k] is None:
            continue
        v = row[k]
        if k in _BOOL_KEYS:
            if isinstance(v, bool):
                out[k] = v
            elif isinstance(v, str) and v.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
                out[k] = v.strip().lower() in ("true", "1", "yes")
            # anything else → keep the default
        else:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                out[k] = n
    # A nonsensical window can never invert: clamp max ≥ min, default inside the band.
    if out["entry_max_minutes"] < out["entry_min_minutes"]:
        out["entry_max_minutes"] = out["entry_min_minutes"]
    out["entry_default_minutes"] = _clamp(out["entry_default_minutes"],
                                          out["entry_min_minutes"], out["entry_max_minutes"])
    return out


def _clamp(n, lo, hi):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = lo
    return max(lo, min(hi, n))


# ── Time helpers (pure; `now` is always injectable so the harness owns the clock) ────────────────
def _now(now=None):
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if isinstance(now, (int, float)):
        return datetime.fromtimestamp(float(now), timezone.utc)
    return _parse_ts(now) or datetime.now(timezone.utc)


def _parse_ts(v):
    """Lenient ISO-8601 → aware datetime. Returns None on anything unparseable (never raises)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    # Postgres microseconds can exceed 6 digits through some drivers; trim so fromisoformat copes.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d):
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def is_org_id(v) -> bool:
    """A tenant id must LOOK like a uuid. Caller-supplied org ids reach queries, so this is the
    'pick, don't type' backstop — never a tenant NAME test (RULE TWO: no tenant literals)."""
    return bool(_UUID_RE.match(str(v or "").strip()))


# ── Registry row validity ───────────────────────────────────────────────────────────────────────
def operator_row_active(row, now=None) -> bool:
    """Is a `core.platform_operator` row currently conferring authority? FAIL-CLOSED: a non-dict, a
    missing auth_id, `is_active` false, or an `expires_at` in the past ⇒ False. An ABSENT expires_at
    means 'no expiry' (a standing operator), which is how the seeded owner rows are written."""
    if not isinstance(row, dict):
        return False
    if not str(row.get("auth_id") or "").strip():
        return False
    if row.get("is_active") is False:
        return False
    exp = _parse_ts(row.get("expires_at"))
    if exp is not None and exp <= _now(now):
        return False
    return True


def role_capabilities(role, overrides=None):
    """Capabilities for a scoped role, plus optional per-row grants/denies.

    `overrides` is a {capability: bool} map on the row — a grant adds, a deny removes, and a deny
    always wins (the `_can_edit_setting` precedence this codebase already uses for settings areas).
    An UNKNOWN role yields the EMPTY set, not a default-allow: a typo in the registry must not
    manufacture authority."""
    caps = set(OPERATOR_ROLES.get(str(role or "").strip().lower(), frozenset()))
    if isinstance(overrides, dict):
        for cap, want in overrides.items():
            if cap not in ALL_CAPABILITIES:
                continue          # unknown capability name: ignored, never invented
            if want is True:
                caps.add(cap)
            elif want is False:
                caps.discard(cap)
    return frozenset(caps)


# ── THE AUTHORITY DECISION (what `_require_super_admin` delegates to) ────────────────────────────
def resolve_authority(*, legacy_super_admin=False, operator_row=None, policy=None,
                      house_admin=False, now=None):
    """THE union decision. Returns a dict — never raises, never partially answers.

        {"is_operator": bool,
         "sources": ("legacy"|"registry"|"house_bootstrap", …),   # why they are in
         "operator_role": str|None,
         "capabilities": frozenset,
         "legacy_honored": bool,
         "denied_reason": str|None}

    PRECEDENCE — union, not override. Both sources are evaluated and the capability sets are UNIONed.
    That single choice is what makes this additive: shipping the registry can only ever ADD authority
    to a login that has it today, never subtract. `legacy_super_admin` alone ⇒ ALL_CAPABILITIES,
    i.e. byte-identical to the super-admin this platform has right now.

    `house_admin` is the EXISTING bootstrap rung from `_require_super_admin` ("a house-org admin, so
    the very first operator is never locked out before the flag is seeded"). It is carried through
    unchanged and is NOT gated on the policy — it is the floor under the floor.
    """
    pol = effective_policy(policy)
    honored = bool(pol["legacy_membership_flag_honored"])
    sources, caps, role = [], set(), None

    if legacy_super_admin and honored:
        sources.append("legacy")
        caps |= set(ALL_CAPABILITIES)      # unchanged authority — no narrowing on ship day

    if operator_row_active(operator_row, now=now):
        sources.append("registry")
        role = str((operator_row or {}).get("operator_role") or DEFAULT_OPERATOR_ROLE).strip().lower()
        caps |= set(role_capabilities(role, (operator_row or {}).get("capabilities")))

    if house_admin:
        # The pre-existing bootstrap. Kept whole so a database with no super_admin flag set anywhere
        # (a fresh env, a restored backup mid-seed) still has a way in, exactly as today.
        sources.append("house_bootstrap")
        caps |= set(ALL_CAPABILITIES)

    if not sources:
        why = ("the platform no longer honors the tenant super-admin flag and this login has no "
               "platform-operator record") if (legacy_super_admin and not honored) else \
              "not a platform operator"
        return {"is_operator": False, "sources": (), "operator_role": None,
                "capabilities": frozenset(), "legacy_honored": honored, "denied_reason": why}

    return {"is_operator": True, "sources": tuple(sources), "operator_role": role,
            "capabilities": frozenset(caps), "legacy_honored": honored, "denied_reason": None}


def has_capability(authority, capability) -> bool:
    """FAIL-CLOSED capability test. A non-dict authority, a missing/false `is_operator`, or an
    unrecognised capability name ⇒ False. There is no super-admin bypass BELOW this function: the
    bypass is expressed ABOVE it, by legacy authority carrying ALL_CAPABILITIES."""
    if not isinstance(authority, dict) or not authority.get("is_operator"):
        return False
    if capability not in ALL_CAPABILITIES:
        return False
    return capability in (authority.get("capabilities") or frozenset())


def policy_change_decision(*, current_policy, requested, active_registry_operators, now=None):
    """May this policy change be applied? PURE. The ONE refusal that matters:

        turning OFF `legacy_membership_flag_honored` while ZERO active registry operators exist
        would remove every operator's authority at once — the lockout the owner explicitly ruled out.

    This mirrors `revoke_super_admin`'s existing "cannot remove the last platform super-admin".
    `active_registry_operators` is the COUNT of rows for which `operator_row_active` is true, computed
    by the caller against the database; passing 0 (or a garbage value) is always the safe direction."""
    cur = effective_policy(current_policy)
    req = effective_policy({**cur, **(requested if isinstance(requested, dict) else {})})
    try:
        n = int(active_registry_operators)
    except (TypeError, ValueError):
        n = 0
    turning_off = cur["legacy_membership_flag_honored"] and not req["legacy_membership_flag_honored"]
    if turning_off and n < 1:
        return {"allowed": False, "policy": cur, "code": "would_lock_out",
                "message": ("Refused: no active platform-operator record exists yet, so switching off "
                            "the legacy tenant super-admin flag would lock every operator out. Add at "
                            "least one operator on the Operators page first — then flip this.")}
    if turning_off and n == 1:
        # Allowed, but say it out loud: one row is now the only thing standing between the owner and
        # a support call. The API surfaces this verbatim.
        return {"allowed": True, "policy": req, "code": "single_point_of_failure",
                "message": ("Applied. NOTE: exactly ONE active platform operator remains. If that "
                            "record is deactivated or expires, nobody can administer the platform. "
                            "Add a second operator before relying on this.")}
    return {"allowed": True, "policy": req, "code": "ok", "message": ""}


# ── TENANT ENTRY — the audited wrapper around the switcher the platform already has ──────────────
def entry_decision(*, authority, target_org_id, reason, minutes=None, policy=None,
                   known_org_ids=None, now=None):
    """May this operator open an entry session into `target_org_id`, and until when? PURE.

    Returns {"allowed", "code", "message", "minutes", "expires_at", "grants", "reason"}.

    WHAT THIS IS NOT. It is not a new door into a tenant — a platform super-admin can already act as
    any tenant through the cross-tenant switcher, with no record kept. This decision produces the
    RECORD, the EXPIRY and the BANNER that act has never had. Its `grants` is `ENTRY_GRANTS`, which
    is exactly `("acting_org",)`: the same acting-tenant switch, nothing more. It never confers the
    default-deny `impersonate` permission, and the harness fails if it ever does.

    Fail-closed on every axis: no capability, a target that is not a uuid, a target not in the
    caller's known tenant set, a missing reason when the policy requires one, or a non-positive
    duration all refuse."""
    pol = effective_policy(policy)
    grants = tuple(ENTRY_GRANTS)
    reason_s = str(reason or "").strip()
    org = str(target_org_id or "").strip()

    def no(code, msg):
        return {"allowed": False, "code": code, "message": msg, "minutes": 0,
                "expires_at": None, "grants": (), "reason": reason_s}

    if not has_capability(authority, CAP_TENANT_ENTER):
        return no("forbidden", "Your operator role cannot enter tenants.")
    if not is_org_id(org):
        return no("bad_target", "Pick a company from the directory.")
    if known_org_ids is not None and org not in set(known_org_ids):
        # `known_org_ids` is the list the caller just read from storeops.tenants — so a typed or
        # stale org id can never open a session against a tenant that does not exist.
        return no("unknown_target", "That company is not in the tenant directory.")
    if pol["entry_reason_required"] and len(reason_s) < 6:
        return no("reason_required",
                  "Give a short reason (it is written to the tenant-entry log and shown to you "
                  "while the session is open).")

    mins = _clamp(pol["entry_default_minutes"] if minutes in (None, "", 0) else minutes,
                  pol["entry_min_minutes"], pol["entry_max_minutes"])
    exp = _now(now) + _minutes(mins)
    return {"allowed": True, "code": "ok", "message": "", "minutes": mins,
            "expires_at": _iso(exp), "grants": grants, "reason": reason_s[:500]}


def _minutes(n):
    from datetime import timedelta
    return timedelta(minutes=int(n))


def session_state(session, now=None):
    """'active' | 'ended' | 'expired' | 'none' for an entry-session row. PURE, fail-closed: an
    unparseable expiry is treated as EXPIRED, never as active (an unreadable time-box is no time-box)."""
    if not isinstance(session, dict) or not session:
        return "none"
    if session.get("ended_at"):
        return "ended"
    exp = _parse_ts(session.get("expires_at"))
    if exp is None or exp <= _now(now):
        return "expired"
    return "active"


def banner_payload(session, tenant_name=None, now=None):
    """What the persistent in-tenant banner shows, or None when no session is active.

    VISIBILITY IS A SAFETY PROPERTY, not decoration: the impersonation banner exists for the same
    reason. Carries the operator's OWN email — an operator inside a tenant is never anonymised
    behind that tenant."""
    if session_state(session, now=now) != "active":
        return None
    exp = _parse_ts(session.get("expires_at"))
    remaining = max(0, int((exp - _now(now)).total_seconds())) if exp else 0
    return {
        "session_id": session.get("id"),
        "org_id": session.get("org_id"),
        "tenant_name": tenant_name or session.get("tenant_name") or "",
        "actor_email": session.get("actor_email") or "",
        "reason": redact(session.get("reason") or ""),
        "expires_at": session.get("expires_at"),
        "seconds_remaining": remaining,
        "grants": list(ENTRY_GRANTS),
        # Said explicitly in the UI so nobody mistakes an entry session for "view as employee".
        "note": ("You are viewing this company as yourself, as a platform operator. Every action is "
                 "logged under your own account. This is not 'view as employee'."),
    }


# ── TAMPER-EVIDENT AUDIT CHAIN ──────────────────────────────────────────────────────────────────
GENESIS_HASH = "0" * 64

# Fields covered by the hash. Anything outside this tuple (e.g. a display-only join) can be added to
# a row without invalidating history; anything inside it is sealed.
_CHAIN_FIELDS = ("seq", "actor_auth_id", "actor_email", "action", "target_org_id", "target_ref",
                 "detail", "created_at")


def _canonical(payload):
    """Deterministic bytes for hashing: sorted keys, no whitespace, stable separators. Values that
    do not survive JSON are stringified rather than dropped, so nothing silently escapes the seal."""
    d = {k: payload.get(k) for k in _CHAIN_FIELDS}
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode("utf-8")


def chain_hash(prev_hash, payload):
    """sha256(prev_hash ‖ canonical(payload)) — the link. PURE."""
    h = hashlib.sha256()
    h.update(str(prev_hash or GENESIS_HASH).encode("ascii", "ignore"))
    h.update(b"\x1f")
    h.update(_canonical(payload if isinstance(payload, dict) else {}))
    return h.hexdigest()


def audit_row(*, seq, actor_auth_id, actor_email, action, target_org_id=None, target_ref=None,
              detail=None, prev_hash=None, now=None):
    """Build one sealed operator-action row. The operator's OWN identity is mandatory and is never
    replaced by the tenant's — that is the audit requirement in the owner's directive.

    `detail` is passed through `redact` field-by-field so a reason or an error string that happens to
    contain a token never lands in the permanent log."""
    payload = {
        "seq": int(seq),
        "actor_auth_id": str(actor_auth_id or ""),
        "actor_email": str(actor_email or ""),
        "action": str(action or "")[:80],
        "target_org_id": (str(target_org_id) if target_org_id else None),
        "target_ref": (redact(target_ref)[:200] if target_ref else None),
        "detail": _redact_detail(detail),
        "created_at": _iso(_now(now)),
    }
    payload["prev_hash"] = str(prev_hash or GENESIS_HASH)
    payload["hash"] = chain_hash(payload["prev_hash"], payload)
    return payload


def _redact_detail(detail):
    if not isinstance(detail, dict):
        return {} if detail is None else {"value": redact(detail)[:500]}
    return {str(k)[:60]: (redact(v)[:500] if isinstance(v, str) else v) for k, v in detail.items()}


def verify_chain(rows):
    """Walk the chain oldest-first. Returns {"ok", "length", "broken_at", "reason"}.

    `broken_at` is the `seq` of the FIRST row whose stored hash does not match a recomputation over
    its own sealed fields and its predecessor's hash — i.e. the first row that was edited, or the
    first row after one that was deleted. An empty log is `ok` with length 0: nothing to contradict."""
    rows = sorted([r for r in (rows or []) if isinstance(r, dict)],
                  key=lambda r: _seq(r))
    prev = GENESIS_HASH
    for i, r in enumerate(rows):
        if i and _seq(r) == _seq(rows[i - 1]):
            return {"ok": False, "length": len(rows), "broken_at": _seq(r), "reason": "duplicate_seq"}
        if str(r.get("prev_hash") or GENESIS_HASH) != prev:
            return {"ok": False, "length": len(rows), "broken_at": _seq(r), "reason": "prev_hash_mismatch"}
        if chain_hash(prev, r) != str(r.get("hash") or ""):
            return {"ok": False, "length": len(rows), "broken_at": _seq(r), "reason": "hash_mismatch"}
        prev = str(r.get("hash"))
    return {"ok": True, "length": len(rows), "broken_at": None, "reason": ""}


def _seq(r):
    try:
        return int(r.get("seq") or 0)
    except (TypeError, ValueError):
        return 0


# ── ANOMALY DETECTION over the operator's own trail ──────────────────────────────────────────────
def anomalies(rows, policy=None, now=None):
    """Findings over `core.operator_action` rows. PURE, read-only, thresholds from POLICY.

    Deliberately boring and explainable — three shapes an operator abusing (or losing) their account
    actually makes, each stated with the evidence that triggered it:
      · BURST     — many actions from one operator in a short window (a script, or a stolen session);
      · FAN-OUT   — one operator entering an unusual number of DISTINCT tenants in a rolling day;
      · DENIALS   — a run of refused attempts (probing for what they can reach).
    No machine learning, no baseline it could silently mis-learn. Never raises; a malformed row is
    skipped rather than failing the whole scan."""
    pol = effective_policy(policy)
    now_d = _now(now)
    by_actor = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ts = _parse_ts(r.get("created_at"))
        if ts is None:
            continue
        a = by_actor.setdefault(str(r.get("actor_auth_id") or r.get("actor_email") or "?"),
                                {"email": r.get("actor_email") or "", "rows": []})
        a["rows"].append((ts, r))
    out = []
    for actor, blob in by_actor.items():
        rs = sorted(blob["rows"], key=lambda t: t[0])
        # BURST — sliding window, no sampling.
        w = pol["anomaly_burst_minutes"] * 60
        n = pol["anomaly_burst_actions"]
        lo = 0
        for hi in range(len(rs)):
            while (rs[hi][0] - rs[lo][0]).total_seconds() > w:
                lo += 1
            if hi - lo + 1 >= n:
                out.append({"kind": "burst", "actor_auth_id": actor, "actor_email": blob["email"],
                            "count": hi - lo + 1, "window_minutes": pol["anomaly_burst_minutes"],
                            "from": _iso(rs[lo][0]), "to": _iso(rs[hi][0]), "severity": "amber",
                            "message": "%d operator actions in %d minutes." % (
                                hi - lo + 1, pol["anomaly_burst_minutes"])})
                break
        # FAN-OUT — distinct tenants ENTERED in the last 24h.
        day = [r for (ts, r) in rs
               if (now_d - ts).total_seconds() <= 86400 and str(r.get("action") or "") == "tenant.enter"]
        orgs = {str(r.get("target_org_id")) for r in day if r.get("target_org_id")}
        if len(orgs) >= pol["anomaly_fanout_tenants"]:
            out.append({"kind": "tenant_fanout", "actor_auth_id": actor, "actor_email": blob["email"],
                        "count": len(orgs), "severity": "amber",
                        "message": "Entered %d different companies in 24 hours." % len(orgs)})
        # DENIALS — a trailing run of refusals.
        streak = 0
        for (_ts, r) in reversed(rs):
            if str(r.get("action") or "").endswith(".denied"):
                streak += 1
            else:
                break
        if streak >= pol["anomaly_denied_streak"]:
            out.append({"kind": "denied_streak", "actor_auth_id": actor, "actor_email": blob["email"],
                        "count": streak, "severity": "red",
                        "message": "%d refused operator actions in a row." % streak})
    out.sort(key=lambda f: (0 if f["severity"] == "red" else 1, -f.get("count", 0)))
    return out


# ── PLATFORM STATUS NOTICE (the operator → every tenant broadcast) ───────────────────────────────
NOTICE_SEVERITIES = ("info", "maintenance", "degraded", "outage")


def notice_visible(notice, org_id=None, now=None):
    """Is this platform notice live for `org_id` right now? PURE, fail-CLOSED on shape.

    Audience is either every tenant (`org_ids` empty/absent) or an explicit list. A notice with no
    `starts_at` is live immediately; with no `ends_at` it stays up until withdrawn. An unparseable
    window hides the notice rather than pinning it up forever."""
    if not isinstance(notice, dict) or notice.get("is_active") is False:
        return False
    # A notice with nothing to say is not a notice. `title` is NOT NULL in the table, so this only
    # ever fires on a malformed/partial object — and the fail-CLOSED answer is to show nothing
    # rather than to pin a blank banner across every tenant's screen.
    if not str(notice.get("title") or "").strip():
        return False
    n = _now(now)
    s = notice.get("starts_at")
    if s not in (None, ""):
        sd = _parse_ts(s)
        if sd is None or sd > n:
            return False
    e = notice.get("ends_at")
    if e not in (None, ""):
        ed = _parse_ts(e)
        if ed is None or ed <= n:
            return False
    orgs = notice.get("org_ids") or []
    if orgs:
        return str(org_id or "") in {str(o) for o in orgs}
    return True


def notice_lamp(notices, org_id=None, now=None):
    """The single worst lamp across the live notices, on the CONTROL BOX's ladder (imported, not
    re-invented). `info` is green-with-a-message, `maintenance` amber, `degraded` amber, `outage` red."""
    m = {"info": "green", "maintenance": "amber", "degraded": "amber", "outage": "red"}
    live = [n for n in (notices or []) if notice_visible(n, org_id=org_id, now=now)]
    return worst_lamp(*[m.get(str(n.get("severity") or "info"), "amber") for n in live]) if live else "green"


# ── BACKUP / RESTORE-DRILL ATTESTATION (the control box's declared UNMONITORED gap) ──────────────
# §20 of the index: "KNOWN, DECLARED GAPS … Supabase backup/restore drills … none is observable from
# the backend today." It is not observable — but it IS attestable: a human performs the drill and
# records the outcome, and staleness of that record is then a perfectly ordinary heartbeat. The
# control box needs NO code change to consume this: a `core.system_check` ROW of kind `heartbeat`
# pointed at `core.restore_drill.verified_at` turns the grey lamp into a real one (mig 981, and the
# row is COMMENTED OUT there because switching it on makes the board honestly RED until the owner
# records their first drill).
DRILL_OUTCOMES = ("passed", "failed", "partial")


def drill_record_valid(rec):
    """Is a submitted drill attestation well-formed enough to be evidence? An attestation that does
    not say WHAT was restored and whether it worked is not evidence, and is refused rather than
    stored — a bogus green is the one thing §20's honesty rules forbid."""
    if not isinstance(rec, dict):
        return False, "not a record"
    if str(rec.get("outcome") or "").strip().lower() not in DRILL_OUTCOMES:
        return False, "outcome must be one of: %s" % ", ".join(DRILL_OUTCOMES)
    if len(str(rec.get("scope") or "").strip()) < 3:
        return False, "say what was restored (e.g. 'full cluster to staging')"
    if _parse_ts(rec.get("performed_at")) is None:
        return False, "performed_at must be a timestamp"
    return True, ""


def drill_lamp(latest, cadence_days=90, grace_days=30, now=None):
    """Lamp for the most recent restore drill. PURE, and honest in the §20 sense: never GREEN without
    a passing, in-cadence drill, and RED (not amber) when none has ever been recorded — a backup that
    has never been restored is an untested backup."""
    if not isinstance(latest, dict) or not latest:
        return "red", "no restore drill has ever been recorded"
    outcome = str(latest.get("outcome") or "").strip().lower()
    ts = _parse_ts(latest.get("verified_at") or latest.get("performed_at"))
    if ts is None:
        return "unknown", "the recorded drill has no usable timestamp"
    age_days = (_now(now) - ts).total_seconds() / 86400.0
    if age_days < 0:
        return "unknown", "the recorded drill is dated in the future"
    if outcome == "failed":
        return "red", "the last restore drill FAILED (%.0f days ago)" % age_days
    if age_days > cadence_days + grace_days:
        return "red", "the last restore drill was %.0f days ago" % age_days
    if outcome == "partial" or age_days > cadence_days:
        return "amber", "the last restore drill was %.0f days ago (%s)" % (age_days, outcome or "?")
    return "green", "restore drill %s %.0f days ago" % (outcome, age_days)


# ── CONSOLE NAV — derived from capabilities, so it can never advertise what you cannot do ────────
CONSOLE_SECTIONS = (
    # (href, label, icon, capability, description)
    ("/operator", "Console Home", "🛰️", None,
     "Platform health, the operator trail and anything that needs you."),
    ("/operator/tenants", "Companies", "🏢", CAP_TENANT_READ,
     "Every tenant on the platform — and the way in to any of them."),
    ("/operator/billing", "Tenant Billing", "💳", CAP_BILLING_READ,
     "Plans, invoices, usage and margin, per tenant."),
    ("/operator/operators", "Operators", "🛡️", CAP_OPERATOR_READ,
     "Who holds platform authority, with what scope, until when."),
    ("/operator/audit", "Operator Trail", "🧾", CAP_AUDIT_READ,
     "Every operator action, hash-chained and verifiable."),
    ("/operator/notices", "Status Notices", "📣", CAP_NOTICE_WRITE,
     "Broadcast maintenance or an incident to tenants."),
    ("/admin/control-box", "System Control Box", "🛎️", CAP_CONTROL_BOX,
     "The platform red/green board (§20)."),
)


def console_sections(authority):
    """The console nav for this operator. A section with `capability=None` always shows; every other
    is filtered by `has_capability`. Nav is CONVENIENCE — every endpoint gates independently."""
    out = []
    for href, label, icon, cap, desc in CONSOLE_SECTIONS:
        if cap is None or has_capability(authority, cap):
            out.append({"href": href, "label": label, "icon": icon, "capability": cap,
                        "description": desc})
    return out
