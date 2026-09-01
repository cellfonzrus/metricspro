"""Server-side PAY-VISIBILITY RBAC — one gate for every payroll/workforce money column (mig 434).

OWNER SPEC (charter rule 4, 2026-09-01): pay-per-hour, gross pay and salary are HIDDEN BY DEFAULT
from every level below market manager; market manager and above see them; WHICH roles see pay is a
per-org CONFIG (nothing hardcoded); those below can see/adjust hours only per granted permission.
Enforced SERVER-SIDE, on the payload, before it leaves the endpoint — hiding a column in the UI
alone still ships the dollars to the browser and straight into the Excel/PDF export (RULE FOUR:
"a gated money column never leaks through an export").

THE CONFIG (storeops.tenants, migration 434 — adaptive: pre-434 behaves as the owner default):
    pay_visibility     'manager_up' (DEFAULT — market manager and above see pay)
                       'permissioned' (only roles holding the `employee_pay_rates` data grant)
                       'all' (legacy open — every caller who can open the page sees pay)
    pay_visible_roles  TEXT[] allow-list for 'manager_up'; NULL = the built-in default
                       DEFAULT_VISIBLE_ROLES below, which IS "market manager and above":
                       admin / master_admin / market_manager (+ its 'market' alias). Anything
                       higher than market manager is a company-wide role and passes via
                       perms.scope == 'all' regardless of its name.

TWO GATES LIVE HERE — deliberately, because two different owner directives shaped them:

  • can_see_pay(...)          — the ALLOW-LIST gate above, used by the six payroll/workforce money
    surfaces (GET /storeops/payroll, /payroll-by-store, /payroll/actual-hours-detail, /salary-owed,
    /hr/compensation, /hr/employee-database). Default posture: market manager and above.
  • can_see_pay_deny_list(...) — the ORIGINAL approvals-board gate (owner 2026-08-11: "DM / market
    manager should be able to see the payroll hours ... but not the actual payscale"), lifted
    VERBATIM from payroll_approval._can_see_pay_rates so that surface stays byte-identical. It is
    STRICTER than 'manager_up' in one spot: it hides pay from MARKET managers too, because a DM/MM
    approving HOURS does not need anyone's pay scale. payroll_approval keeps that posture by
    passing its own deny-list; the general surfaces use the allow-list. The two only differ for a
    market manager: money REPORTS yes (they run their market), the hours-approval board no.

FAIL-CLOSED. A caller who cannot be resolved sees NO pay figures — with one platform-parity
carve-out: an UNAUTHENTICATED caller (no token at all) while the login master switch
(storeops.app_config.rbac_enabled) is OFF is the open app's normal state, and is treated exactly
the way caller_scope / hr._require_hr_or_admin already treat it (allowed). A token that fails to
verify or resolve NEVER opens pay. This is the same degradation direction as the approvals gate:
"less information", never a leak.

STRIP, NEVER ZERO. strip_pay DELETES the keys — a 0.00 rate reads as "this person earns nothing",
a different and worse lie than "you cannot see this" (same rationale as payroll_approval._strip_pay,
which now delegates here).

LEAF MODULE: no fastapi, no heavy imports at top level; every DB/core import is lazy so this can be
unit-proven with the stdlib alone (backend/harness_pay_visibility.py) and so a fault in core
degrades CLOSED instead of breaking payroll at import time.
"""
import re

# ── the grant key (must stay identical to the rbac.ts DATA_GRANTS key) ────────────────────────────
PAY_GRANT_KEY = "employee_pay_rates"

# ── the money keys (rows) and totals keys this platform's payroll surfaces carry ──────────────────
# Widened from payroll_approval's original ("pay_rate", "pay_effective") to every pay-classified key
# the six gated endpoints emit. Endpoint-specific extras (e.g. /payroll-by-store's generic "amount",
# /salary-owed's "owed"/"balance") are passed per call site rather than globalized, so a generic key
# name can never be stripped from an unrelated payload by accident.
PAY_FIELDS = (
    "pay_rate", "pay_effective", "scheduled_pay", "actual_pay", "net_pay", "payable_pay",
    "wages", "salary_period_pay", "salary_derived_pay", "pay_amount", "gross_pay",
    "pay_per_hour", "base_salary", "total_comp", "annualized",
)
PAY_TOTALS_FIELDS = (
    "pay", "payable_pay", "scheduled_pay", "actual_pay", "wages", "gross_pay",
    "base_salary", "total_comp", "annualized",
)

# ── modes + the built-in 'manager_up' allow-list ──────────────────────────────────────────────────
MODES = ("all", "manager_up", "permissioned")
DEFAULT_MODE = "manager_up"
# "Market manager and above" as data (a seeded DEFAULT VALUE, not a branch — the same convention as
# PAY_RATE_HIDDEN_ROLES / plan_pay_gate.DEFAULT_EXCLUSIONS): admin + master_admin + market_manager
# (with its 'market' alias, matching how tenants actually name the role). Roles ABOVE market manager
# are company-wide and pass on perms.scope == 'all' without needing a row here, so a tenant's
# 'director'/'owner'/'vp' role needs no seeding. A tenant that names its roles differently sets
# storeops.tenants.pay_visible_roles — config, never code (RULE TWO).
DEFAULT_VISIBLE_ROLES = frozenset({"admin", "master_admin", "market_manager", "market"})

# The approvals board's ORIGINAL deny-list (owner 2026-08-11) — kept as its own constant so that
# surface's stricter posture (market managers hidden too) survives the refactor byte-identically.
APPROVALS_PAY_HIDDEN_ROLES = frozenset({"district_manager", "dm", "market_manager", "market"})


def _norm_role(s):
    """Role name -> canonical key: 'Market Manager' / 'market-manager' / 'MARKET_MANAGER' all become
    'market_manager' (same tolerance rbac.ts's isMasterAdminRole applies to role names)."""
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PURE CORE (no I/O — the unit-provable truth table)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def resolve_pay_access(mode, caller_role, caller_scope, visible_roles=None, has_grant=False):
    """May a caller with (role, scope, grant) see pay under `mode`? PURE — fails CLOSED on unknowns.

      'all'          -> True (legacy open).
      'permissioned' -> has_grant ONLY. (The grant itself is resolved grant_allowed-style by the
                        caller of this function, so admins/company-wide roles hold it implicitly.)
      'manager_up'   -> scope 'all' (a company-wide role — full-scope admin callers and every role
                        ABOVE market manager) always passes; else the role must be in the allow-list
                        (`visible_roles`, or DEFAULT_VISIBLE_ROLES = market manager and above when
                        the tenant configured none); an explicit `employee_pay_rates` grant ALSO
                        passes — granting the permission must never do less than it says.
      unknown mode   -> treated as 'manager_up' (the restrictive owner default, never open).
      unknown/empty role with a narrow scope -> False (unresolvable = hidden).
    """
    m = str(mode or "").strip().lower()
    if m not in MODES:
        m = DEFAULT_MODE
    if m == "all":
        return True
    if m == "permissioned":
        return bool(has_grant)
    # manager_up
    if str(caller_scope or "").strip().lower() == "all":
        return True
    if has_grant:
        return True
    role = _norm_role(caller_role)
    if not role:
        return False
    allow = {_norm_role(r) for r in (visible_roles or DEFAULT_VISIBLE_ROLES)}
    allow.discard("")
    return role in allow


def strip_pay(rows, totals=None, fields=PAY_FIELDS, totals_fields=None):
    """Remove every pay figure from an outgoing payload. Returns (rows, totals) with the keys
    DELETED rather than zeroed — a 0.00 rate reads as "this person earns nothing", which is a
    different and worse lie than "you cannot see this" (payroll_approval's original rationale).

    Tolerant by design: `rows` may be a list of dicts, a single dict (a detail payload), None, or
    contain odd non-dict entries; `totals` may be None or any shape. NEVER raises — a strip failure
    must never 500 a payroll surface (and dict.pop(k, None) cannot fail, so tolerance never turns
    into a partial leak). Idempotent: stripping twice is a no-op."""
    if totals_fields is None:
        totals_fields = PAY_TOTALS_FIELDS
    try:                    # each phase guarded SEPARATELY: an odd `rows` shape must not leave
        targets = [rows] if isinstance(rows, dict) else list(rows or [])
        for r in targets:   # `totals` unstripped (tolerance must never become a partial leak)
            if isinstance(r, dict):
                for k in fields:
                    r.pop(k, None)
    except Exception:
        pass
    try:
        if isinstance(totals, dict):
            for k in totals_fields:
                totals.pop(k, None)
    except Exception:
        pass
    return rows, totals


def grant_allowed(caller, key):
    """PURE grant check over core's resolved caller dict — a deliberate MIRROR of
    app.modules.account.report_gates.grant_allowed (READ, not imported: account/** is another
    agent's module and this gate must not break if that file is mid-edit; the shape is the platform
    convention — device_history.device_commission_allowed is the original):
        super_admin / perms.scope=='all' / role=='admin'   -> True
        key in perms.modules, or truthy perms.data[key]    -> True
        anything else (including caller=None)              -> False"""
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


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# DB / CALLER WRAPPERS (lazy imports; every failure degrades CLOSED)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _default_client():
    from app.core.database import get_supabase
    return get_supabase()


def _storeops(client):
    """A storeops-schema table handle off either a raw supabase client or an already-schema'd one
    (harness fakes return self from .schema(), the real client re-scopes)."""
    try:
        return client.schema("storeops")
    except Exception:
        return client


def tenant_pay_visibility(org_id, client=None):
    """(mode, visible_roles_or_None) from storeops.tenants — ADAPTIVE: a pre-434 database (missing
    column), a missing tenant row, or any read failure all resolve to ('manager_up', None), the
    owner default. A config problem can only ever make pay MORE hidden, never open it."""
    try:
        client = client or _default_client()
        rows = (_storeops(client).table("tenants")
                .select("pay_visibility,pay_visible_roles")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return DEFAULT_MODE, None
    if not rows:
        return DEFAULT_MODE, None
    mode = str(rows[0].get("pay_visibility") or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        mode = DEFAULT_MODE
    raw = rows[0].get("pay_visible_roles")
    roles = [str(r) for r in raw if str(r or "").strip()] if isinstance(raw, (list, tuple)) else []
    return mode, (roles or None)


def _login_enforced(client):
    """The login master switch (storeops.app_config.rbac_enabled — the SAME flag caller_scope and
    hr._require_hr_or_admin read). Unreadable -> True, i.e. assume enforced, which DENIES the
    unauthenticated parity carve-out below (fail closed)."""
    try:
        rows = (_storeops(client).table("app_config").select("rbac_enabled")
                .eq("id", 1).limit(1).execute().data) or []
        return bool(rows and rows[0].get("rbac_enabled"))
    except Exception:
        return True


def can_see_pay(authorization, org_id=None, client=None):
    """May this caller see pay figures on the payroll/workforce money surfaces? The full gate:
    tenant mode (adaptive default 'manager_up') x resolved caller (role / scope / super_admin /
    `employee_pay_rates` grant) -> resolve_pay_access.

    Caller resolution is EXACTLY payroll_approval._can_see_pay_rates' path — core's _uid_from_token
    + _resolve_caller with org_id as the acting-org hint — and, like it, FAILS CLOSED: a token that
    does not verify, a login that does not resolve, or any resolver fault hides pay (hours still
    render, so the failure degrades to "less information", never a leak). ONE carve-out, for parity
    with the rest of the platform (caller_scope, hr._require_hr_or_admin): NO token at all while the
    login master switch is OFF is the open app's normal state -> allowed. `org_id` may be None (the
    hr surfaces resolve the org from the caller's own membership); the caller's acting org is used.
    Pass the calling module's own client so harness fakes see the same world the route does."""
    try:
        auth = authorization if isinstance(authorization, str) else ""
        client = client or _default_client()
        uid, caller, resolver_broke = None, None, False
        if auth.strip():
            # Import inside the branch: an UNauthenticated call never touches core, so a broken /
            # absent core can still serve the 'all' + open-app-parity answers below; a PRESENT token
            # that cannot be verified or resolved NEVER opens a gated mode (fail closed).
            try:
                from app.modules.core.router import _uid_from_token, _resolve_caller
                uid = _uid_from_token(auth)
                if uid:
                    caller = _resolve_caller(client, uid, org_id or None)
            except Exception:
                resolver_broke = True
        org = org_id or (caller or {}).get("org_id")
        mode, visible = tenant_pay_visibility(org, client) if org else (DEFAULT_MODE, None)
        if mode == "all":
            return True                      # legacy open — the tenant said so explicitly, for everyone
        if resolver_broke:
            return False                     # unverifiable token on a gated mode -> hide (fail closed)
        if caller is None:
            # No resolvable identity. Unauthenticated + login enforcement OFF = open-app parity;
            # anything else (a uid whose membership is gone, enforcement ON) stays hidden.
            return (uid is None) and (not _login_enforced(client))
        if caller.get("super_admin"):
            return True
        perms = caller.get("perms") or {}
        return resolve_pay_access(mode, caller.get("role"), perms.get("scope"), visible,
                                  grant_allowed(caller, PAY_GRANT_KEY))
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE APPROVALS-BOARD GATE — lifted VERBATIM from payroll_approval (owner 2026-08-11) so that
# surface's behavior stays byte-identical through the refactor. See the module docstring for why the
# two gates deliberately differ (this one also hides pay from MARKET managers).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def is_full_admin(authorization, org_id, who=None):
    """True for a super-admin, a full-scope role, or the 'admin' role — core's `_can_edit_setting`
    precedence, resolved from the verified JWT. Falls back to the role name from `who` if core's
    resolver is unavailable, so a transient failure denies rather than grants.
    (Formerly payroll_approval._is_admin — moved, not changed.)"""
    try:
        from app.modules.core.router import _resolve_caller, _can_edit_setting, sb as _core_sb
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if uid:
            caller = _resolve_caller(_core_sb(), uid, org_id)
            if caller and _can_edit_setting(caller, "security"):
                return True
    except Exception:
        pass
    return (who or {}).get("role", "").lower() == "admin"


def can_see_pay_deny_list(authorization, org_id, hidden_roles, who=None):
    """May this caller see per-employee pay RATES and dollar amounts, under a DENY-list posture?
    An admin / full-scope / super-admin always may; a caller acting in one of `hidden_roles` may
    not; anyone else is unchanged. FAIL-CLOSED: an unresolvable caller is HIDDEN.
    (Formerly payroll_approval._can_see_pay_rates — moved, not changed; that module passes its own
    PAY_RATE_HIDDEN_ROLES so the approvals board keeps its stricter market-managers-hidden rule.)"""
    if is_full_admin(authorization, org_id, who):
        return True
    role = ""
    try:
        from app.modules.core.router import _resolve_caller, sb as _core_sb, _uid_from_token
        uid = _uid_from_token(authorization)
        if uid:
            caller = _resolve_caller(_core_sb(), uid, org_id) or {}
            role = str(caller.get("role") or "").strip().lower()
    except Exception:
        role = str((who or {}).get("role") or "").strip().lower()
    if not role:
        role = str((who or {}).get("role") or "").strip().lower()
    if not role:
        return False                      # unresolvable caller -> hide
    return role not in hidden_roles
