"""Pure access-decision for the employee self-service dashboard (GET /core/employee-dashboard).

Kept dependency-free (no FastAPI / no DB) so the rule is unit-testable in isolation
(backend/harness_employee_dashboard_access.py) — the router wraps this with the actual
employees_visible() lookup and turns a False into a 403.

The employee-dashboard bundle carries a rep's private compensation (commission $, pay rate, KPIs,
chargebacks). The endpoint historically trusted the `employee_id` QUERY PARAM, so any signed-in rep
could read a colleague's numbers by changing the id (an IDOR). This rule restores the intended
boundary and deliberately MATCHES the employee-picker visibility (/storeops/employees/visible) so the
server allows exactly what the dropdown offers.
"""
from typing import Any, Mapping


def dashboard_access_allowed(visible: Mapping[str, Any] | None, employee_id: str) -> bool:
    """True iff the caller (described by `visible`, the /storeops/employees/visible result) may read
    `employee_id`'s dashboard.

    `visible` shape (only these keys are read):
        {"employee_id": <caller's own emp id>, "scope": <role scope>, "employees": [{"employee_id": ...}, ...]}

    Rules, in order:
      1. Own dashboard (target == caller's own employee_id)                → allow
      2. scope == 'all' (admin / RBAC master-switch off / open-app mode)   → allow (behaviour unchanged)
      3. target is inside the caller's visible roster (manager's span)     → allow
      4. otherwise                                                          → deny
    """
    v = visible or {}
    my_eid = str(v.get("employee_id") or "").strip()
    target = str(employee_id or "").strip()

    if target and target == my_eid:
        return True
    if v.get("scope") == "all":
        return True
    allowed = {
        str(e.get("employee_id")).strip()
        for e in (v.get("employees") or [])
        if isinstance(e, Mapping) and e.get("employee_id")
    }
    return target in allowed
