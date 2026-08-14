"""Pure access-decision for the employee self-service dashboard (GET /core/employee-dashboard).

Kept dependency-free (no FastAPI / no DB) so the rule is unit-testable in isolation
(backend/harness_employee_dashboard_access.py) — the router resolves the inputs (auth token →
caller identity + EXPLICIT role scope + span roster) and turns a False into a 403.

The employee-dashboard bundle carries a rep's private compensation (commission $, pay rate, KPIs,
chargebacks). The endpoint historically trusted the `employee_id` QUERY PARAM, so any signed-in rep
could read a colleague's numbers by changing the id (an IDOR).

IMPORTANT — why this does NOT key off the generic role "scope" string: `_role_scope()` (and the
employee picker built on it) DEFAULTS to "all" whenever a caller's role is blank, missing, or has no
`scope` key. That default is fine for populating a dropdown, but using it for a DATA-AUTHORIZATION
decision would leave the IDOR wide open for any authenticated rep with an unconfigured role. So this
rule only grants org-wide access on an EXPLICIT admin scope (a real roles row whose permissions say
`scope == "all"`), and grants cross-employee access to a manager only for employees actually inside
their resolved span roster. A blank/unknown role → self only.
"""
from typing import Iterable, Optional

# Role scopes that legitimately let a caller view OTHER employees (when positively set on a real role).
_MANAGER_SCOPES = ("store", "market", "region", "dm", "area")


def dashboard_access_allowed(
    *,
    authenticated: bool,
    own_employee_id: str,
    target_employee_id: str,
    explicit_scope: Optional[str],
    visible_roster_ids: Optional[Iterable[str]] = None,
) -> bool:
    """True iff the caller may read `target_employee_id`'s dashboard.

    Args:
        authenticated: whether the request carried a resolvable auth token. When False the caller is
            in the platform's login-enforcement-OFF / open-app mode (an unauthenticated request in an
            ENFORCING tenant is already rejected upstream by TenantScopeMiddleware and never reaches
            here), so behaviour is left unchanged — allowed.
        own_employee_id: the caller's own employee_id (from their app_user row).
        target_employee_id: the requested employee_id.
        explicit_scope: the caller role's EXPLICITLY-SET permissions.scope (None when the role is
            blank / missing / has no scope key — deliberately NOT defaulted to "all" here).
        visible_roster_ids: employee_ids inside the caller's span (only consulted for a manager scope).

    Rules, in order:
      1. Unauthenticated (open-app mode)                          → allow (unchanged behaviour)
      2. Own dashboard (target == own)                            → allow
      3. Explicit admin role (explicit_scope == 'all')           → allow
      4. Explicit manager scope AND target inside span roster    → allow
      5. otherwise                                                → deny
    """
    if not authenticated:
        return True

    target = str(target_employee_id or "").strip()
    own = str(own_employee_id or "").strip()

    if target and target == own:
        return True

    scope = (explicit_scope or "").strip().lower()
    if scope == "all":
        return True

    if scope in _MANAGER_SCOPES:
        roster = {str(x).strip() for x in (visible_roster_ids or []) if str(x).strip()}
        if target in roster:
            return True

    return False
