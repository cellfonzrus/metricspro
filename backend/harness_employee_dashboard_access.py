"""Harness — app/core/dashboard_access.py (the IDOR gate for GET /core/employee-dashboard).

Proves, WITHOUT a database or FastAPI, that dashboard_access_allowed() enforces the intended
boundary on a rep's private compensation bundle (commission $, pay rate, KPIs, chargebacks):

  A. A rep may read their OWN dashboard.
  B. A self-scoped rep may NOT read a colleague's dashboard          (the original IDOR).
  C. An EXPLICIT admin (permissions.scope == 'all') may read anyone.
  D. Unauthenticated / open-app mode is allowed                       (behaviour unchanged).
  E. A store/market manager may read employees INSIDE their span…
  F. …but NOT employees outside it.
  G. THE CLOSED GAP: an authenticated rep whose role does NOT explicitly set scope (blank / missing
     role / role with no scope key → explicit_scope is None) is confined to SELF — even though the
     legacy picker would have defaulted them to scope 'all'. This is the residual IDOR the first
     version of the fix left open.
  H. Type hygiene: numeric vs string employee ids compare equal; whitespace trimmed; scope casing.

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


def allowed(**kw):
    # small helper with sensible defaults
    base = dict(authenticated=True, own_employee_id="E100", target_employee_id="E100",
                explicit_scope=None, visible_roster_ids=None)
    base.update(kw)
    return dashboard_access_allowed(**base)


# A. self
ok("A self can read self", allowed(target_employee_id="E100") is True)

# B. the original IDOR: a rep asking for a colleague (self scope)
ok("B self-scope CANNOT read a colleague",
   allowed(explicit_scope="self", target_employee_id="E999") is False,
   "self-scoped rep read another rep's compensation")

# C. explicit admin
ok("C explicit admin reads anyone",
   allowed(own_employee_id="E001", explicit_scope="all", target_employee_id="E777") is True)

# D. unauthenticated / open-app mode
ok("D unauthenticated allowed (open mode)",
   allowed(authenticated=False, own_employee_id="", explicit_scope=None, target_employee_id="E777") is True)

# E/F. manager span
ok("E manager reads in-span report",
   allowed(own_employee_id="M50", explicit_scope="store", target_employee_id="E200",
           visible_roster_ids=["M50", "E200", "E201"]) is True)
ok("F manager CANNOT read out-of-span",
   allowed(own_employee_id="M50", explicit_scope="store", target_employee_id="E999",
           visible_roster_ids=["M50", "E200", "E201"]) is False,
   "manager read an employee outside their span")

# G. THE CLOSED GAP — blank / unresolved role must be self-only, NOT org-wide.
ok("G blank role (explicit_scope None) CANNOT read a colleague",
   allowed(explicit_scope=None, target_employee_id="E999") is False,
   "blank-role rep read another rep's compensation — IDOR still open!")
ok("G blank role can still read SELF", allowed(explicit_scope=None, target_employee_id="E100") is True)
# A defaulted-'all' must NEVER be passed as explicit; if a manager scope has an EMPTY roster, deny others.
ok("G manager with empty roster denies others",
   allowed(own_employee_id="M1", explicit_scope="store", target_employee_id="E2",
           visible_roster_ids=[]) is False)

# H. type hygiene
ok("H numeric own id matches string target", allowed(own_employee_id=100, target_employee_id="100") is True)
ok("H whitespace trimmed on target", allowed(target_employee_id="  E100 ") is True)
ok("H numeric in-span id matches string target",
   allowed(own_employee_id="M1", explicit_scope="store", target_employee_id="200",
           visible_roster_ids=[200]) is True)
ok("H scope casing normalised ('ALL')",
   allowed(own_employee_id="E1", explicit_scope="ALL", target_employee_id="E9") is True)

# Negative control: empty target never matches an empty own id
ok("neg empty target denied",
   allowed(own_employee_id="", explicit_scope="self", target_employee_id="") is False)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
