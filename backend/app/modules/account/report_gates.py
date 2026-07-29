"""DEFAULT-CLOSED page-level DATA_GRANT gates for two finance REPORT surfaces (owner directive
2026-07-29): **Residual per Subscriber** (`/accounts/residual-per-sub`) and **Trends — all metrics**
(`/accounts/trends`). Neither had any page-level permission gate: every caller who could open the
Accounts module could read them.

SAME SHAPE AS THE EXISTING `device_commission` GRANT — deliberately not a new pattern. Mirrors
`commcalc/device_history.device_commission_allowed` + `commcalc/router._can_view_device_commission`:

    super_admin                                    -> allow
    perms.scope == 'all'   OR  role == 'admin'     -> allow
    key in perms.modules   OR  perms.data[key]     -> allow
    else                                           -> DENY

DEFAULT-CLOSED. Unlike `carrier_residual` — which is only enforced when a tenant sets
`residual_visibility='permissioned'`, i.e. effectively default-OPEN — these two keys restrict the
report until a role is EXPLICITLY granted one. Any resolution failure (missing/!bearer/invalid token,
core unavailable, DB hiccup) **degrades CLOSED**: this gate can only ever hide a report behind the lock
note, never leak one. That is the opposite of `_can_view_carrier_residual`'s open-on-error posture and
is intentional for a default-closed grant.

Caller resolution goes through CORE (`app.modules.core.router._uid_from_token` / `_resolve_caller`) —
IMPORTED, never edited (core/** is on the SHARED list, AGENT_CONTRACT §1).

Frontend mirror: `hasDataGrant(perms, '<key>')` from `frontend/src/lib/rbac.ts` (SHARED — READ, not
edited). The two rows for rbac.ts's `DATA_GRANTS` registry are filed under `## NEEDS CORE` in
`docs/handoffs/finance.md`. **The gate works before that registry edit lands** — it reads the role's own
`permissions` JSONB; the registry only makes the keys tickable in the Roles UI. Until then: admins pass,
everyone else gets the 403 / lock note (ship-safe, no half state).

NOT MONEY-TOUCHING: no chart of accounts, booking rule, COGS rate, recon formula or billing plan is read
or changed here, and no number moves. It only decides WHO MAY READ two already-computed reports.
"""
from fastapi import HTTPException

# ── grant keys (must stay identical to the rbac.ts DATA_GRANTS keys filed under NEEDS CORE) ─────────
RESIDUAL_PER_SUB = "residual_per_sub"
ACCOUNT_TRENDS = "account_trends"

# Human labels used in the 403 message (the message always NAMES the permission the caller needs).
REPORT_LABELS = {
    RESIDUAL_PER_SUB: "Residual per Subscriber",
    ACCOUNT_TRENDS: "Trends (all metrics)",
}


def grant_allowed(caller, key):
    """PURE over an already-resolved caller dict (no I/O, no globals) — the unit-testable core of the
    gate, exactly like `device_history.device_commission_allowed`:
        super_admin / perms.scope=='all' / role=='admin'                 -> True
        `key` in perms.modules, or truthy perms.data[key]                -> True
        anything else (including caller=None)                            -> False
    `perms.modules` may be a list (backend seeds) or a dict (Roles UI writes) — `in` handles both."""
    if not caller or not key:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    if key in (perms.get("modules") or []):
        return True
    if bool((perms.get("data") or {}).get(key)):
        return True
    return False


def any_grant_allowed(caller, keys):
    """True if the caller holds ANY of `keys`. Used where one endpoint feeds two gated reports (the
    residual-per-sub aggregate is charted by the Trends hub too, so `account_trends` must be able to
    read it — a Trends grantee already sees that series on their own page)."""
    return any(grant_allowed(caller, k) for k in (keys or ()))


def _resolve_caller_safe(authorization):
    """Resolve the bearer token to core's caller dict; None on ANY failure (→ deny). Imports core
    lazily so this module never participates in an import cycle and so an absent/renamed core symbol
    degrades closed instead of breaking finance at import time."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        from app.core.database import get_supabase
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        return _resolve_caller(get_supabase(), uid)
    except Exception:
        return None


def can_view_report(authorization, keys):
    """Gate over the raw Authorization header. DEGRADES CLOSED on any error."""
    try:
        ks = (keys,) if isinstance(keys, str) else tuple(keys or ())
        return any_grant_allowed(_resolve_caller_safe(authorization), ks)
    except Exception:
        return False


def require_report_grant(authorization, keys, report=None):
    """Raise 403 (message NAMES the permission) unless the caller holds one of `keys`. Call it at the
    TOP of every endpoint that serves a gated report, right after `require_org`."""
    ks = (keys,) if isinstance(keys, str) else tuple(keys or ())
    if can_view_report(authorization, ks):
        return
    label = report or REPORT_LABELS.get(ks[0] if ks else "", "") or "This report"
    names = " or ".join("'%s'" % k for k in ks) or "the required"
    raise HTTPException(403, f"{label} is restricted — you need the {names} data permission on your "
                             f"role (admin-only by default). Ask an admin to grant it on your role.")
