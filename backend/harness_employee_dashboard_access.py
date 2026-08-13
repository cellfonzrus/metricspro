"""Harness — app/core/dashboard_access.py (the IDOR gate for GET /core/employee-dashboard).

Proves, WITHOUT a database or FastAPI, that dashboard_access_allowed() enforces the intended
boundary on a rep's private compensation bundle:

  A. A rep may read their OWN dashboard.
  B. A self-scoped rep may NOT read a colleague's dashboard  (the IDOR that existed before the fix).
  C. An admin (scope 'all') may read anyone's dashboard      (behaviour unchanged for admins).
  D. An unidentifiable caller (scope 'all' fallback / open-app mode) is allowed (behaviour unchanged).
  E. A store/market manager may read employees INSIDE their span…
  F. …but NOT employees outside it.
  G. Empty/failed scope lookup ({}) denies everything — fail CLOSED, even for a would-be self view.
  H. Type hygiene: numeric vs string employee ids compare equal; whitespace is trimmed.

Run: python3 backend/harness_employee_dashboard_access.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.dashboard_access import dashboard_access_allowed  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


# ── Scenarios ──────────────────────────────────────────────────────────────────────────────────
self_scope = {"employee_id": "E100", "scope": "self", "employees": [{"employee_id": "E100"}]}
admin_scope = {"employee_id": "E001", "scope": "all", "employees": []}
unresolved = {"employee_id": "", "scope": "all", "employees": []}   # caller not identified → visible() = 'all'
mgr_scope = {
    "employee_id": "M50",
    "scope": "store",
    "employees": [{"employee_id": "M50"}, {"employee_id": "E200"}, {"employee_id": "E201"}],
}

# A. self
ok("A self can read self", dashboard_access_allowed(self_scope, "E100") is True)

# B. the IDOR: self-scope rep asking for a colleague
ok("B self CANNOT read a colleague", dashboard_access_allowed(self_scope, "E999") is False,
   "self-scoped rep read another rep's compensation")

# C. admin
ok("C admin reads anyone", dashboard_access_allowed(admin_scope, "E777") is True)

# D. unidentified caller / open-app mode (unchanged behaviour)
ok("D unresolved caller allowed (open mode)", dashboard_access_allowed(unresolved, "E777") is True)

# E/F. manager span
ok("E manager reads self", dashboard_access_allowed(mgr_scope, "M50") is True)
ok("E manager reads in-span report", dashboard_access_allowed(mgr_scope, "E200") is True)
ok("F manager CANNOT read out-of-span", dashboard_access_allowed(mgr_scope, "E999") is False,
   "manager read an employee outside their span")

# G. fail closed on empty scope
ok("G empty scope denies (fail closed)", dashboard_access_allowed({}, "E100") is False)
ok("G None scope denies (fail closed)", dashboard_access_allowed(None, "E100") is False)

# H. type hygiene — numeric vs string ids, whitespace
num_self = {"employee_id": 100, "scope": "self", "employees": [{"employee_id": 100}]}
ok("H numeric own id matches string query", dashboard_access_allowed(num_self, "100") is True)
ok("H whitespace trimmed on target", dashboard_access_allowed(self_scope, "  E100 ") is True)
num_mgr = {"employee_id": "M1", "scope": "store", "employees": [{"employee_id": 200}]}
ok("H numeric in-span id matches string query", dashboard_access_allowed(num_mgr, "200") is True)

# Negative control: empty target never matches an empty own id (guards the `target and ...` clause)
ok("neg empty target denied", dashboard_access_allowed({"employee_id": "", "scope": "self"}, "") is False)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
