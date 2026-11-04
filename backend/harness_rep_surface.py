"""PROOF — an individual contributor's reach is ONE declaration, the API refuses what is not in it,
and no tenant's answer can leak into another's.

OWNER (2026-11-04): *"All sales reps should log in to the clock in screen, on ny logistics Manan was
able to go past it and also see the adjustment hours, this should not be allowed for any sales reps,
fix the permissions accordingly … this is universal across the board."*

THE DEFECT, reproduced for real in `frontend/tools/rep-surface-nav-proof.mjs` §A over the compiled
`lib/rbac.ts`: the `scope === 'self'` branch admitted any page whose URL began with a menu row the
rep could see, so the `/storeops` row admitted `/storeops/timeclock` — Hours Adjustment — and 32
other manager pages. This harness proves the half that is the real fix: the SERVER's own refusal.

DB-free. The pure declaration, the REAL middleware verdict, and the REAL route handlers over the
in-memory double (`tests/fakes/supabase`) — the same double the integration suite uses, so there is
one semantics for "what does .eq() do" and not two.

  A. THE GRAMMAR. A declared page is EXACT or an explicit `/**` subtree; a page's PARENT prefix
     admits nothing (the defect); a `page_except` carves the manager screens out of a subtree.
  B. DEFAULT DENY on the API: every manager path refused, every declared path allowed, a read-only
     lookup refused for a write method.
  C. THE MIDDLEWARE VERDICT over each real role shape: a self-scoped caller is refused an undeclared
     path and served a declared one; a store manager and a company role are refused nothing; the
     break-glass lifts it; an unreadable roles table refuses nobody.
  D. THE FOUR WRITES that asked nothing before: hours adjustment (add + delete), time off, shift
     swap, onboarding paperwork by id — refused to a rep, allowed to the manager whose team the
     person is on, refused to a manager whose team they are not on.
  E. EVERY TENANT (owner 2026-10-30): the declaration takes no org_id at all, and the gate's answer
     for one tenant's role is never reachable from another tenant's request.
  F. NO LOCKOUT (index §22): the identity, shell and own-password paths can never be refused.

    python3 backend/harness_rep_surface.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("APP_ENV", "test")
for _v in ("SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "DATABASE_URL"):
    os.environ[_v] = ""

from app.core import rep_surface as RS                      # noqa: E402
from tests.fakes.supabase import FakeSupabase               # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. THE PAGE GRAMMAR — a parent prefix admits nothing")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 the landing is reachable", RS.page_allowed("/my"))
check("A2 a declared subtree admits what is below it", RS.page_allowed("/my/anything"))
check("A3 the clock-in screen is reachable", RS.page_allowed("/portal"))
check("A4 THE DEFECT: the Hours Adjustment screen is NOT reachable", not RS.page_allowed("/storeops/timeclock"))
check("A5 …nor Hours Approval", not RS.page_allowed("/storeops/payroll/approvals"))
check("A6 …nor payroll, the employee database or store setup",
      not any(RS.page_allowed(p) for p in ("/storeops/payroll", "/storeops/employees",
                                           "/storeops/setup/stores", "/storeops/reports",
                                           "/storeops/attendance", "/storeops/admin")))
check("A7 a declared EXACT page admits only itself — Time Off does not admit its neighbours",
      RS.page_allowed("/storeops/timeoff") and not RS.page_allowed("/storeops/timeoff/settings"))
check("A8 the helpdesk subtree admits a ticket", RS.page_allowed("/helpdesk") and RS.page_allowed("/helpdesk/41"))
check("A9 …and the carve-out refuses its two manager screens",
      not RS.page_allowed("/helpdesk/settings") and not RS.page_allowed("/helpdesk/dashboard"))
check("A10 the Report Center is not an employee page", not RS.page_allowed("/reports"))
check("A11 a query string never changes the answer",
      RS.page_allowed("/helpdesk?tab=mine") and not RS.page_allowed("/storeops/timeclock?d=1"))
check("A12 a trailing slash never changes the answer",
      RS.page_allowed("/helpdesk/") and not RS.page_allowed("/storeops/timeclock/"))
check("A13 every one of the owner's five has a tile, in his order",
      [t["key"] for t in RS.tiles()] == ["clock", "commissions", "schedule", "action_plan", "helpdesk"],
      [t["key"] for t in RS.tiles()])
check("A14 every tile's page is a page the gate admits",
      all(RS.page_allowed(t["page"]) for t in RS.tiles()), [t["page"] for t in RS.tiles()])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. THE API — default deny")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
REFUSED = [("/api/v1/storeops/manual-hours", "POST"), ("/api/v1/storeops/manual-hours/7", "DELETE"),
           ("/api/v1/storeops/manual-hours", "GET"), ("/api/v1/storeops/payroll", "GET"),
           ("/api/v1/storeops/payroll/approvals", "GET"), ("/api/v1/storeops/employees", "POST"),
           ("/api/v1/storeops/hours-budgets", "PUT"), ("/api/v1/storeops/timeclock-permissions", "GET"),
           ("/api/v1/hr/onboarding/employee/E229", "GET"), ("/api/v1/hr/payroll-expenses", "GET"),
           ("/api/v1/commcalc/commission-ledger", "GET"), ("/api/v1/account/pl-statement", "GET"),
           ("/api/v1/core/tenants", "GET"), ("/api/v1/storeops/roles", "PUT")]
for p, m in REFUSED:
    check(f"B- {m} {p} is refused", not RS.api_allowed(p, m), RS.surface_for_api(p, m))
ALLOWED = [("/api/v1/storeops/timeclock/clock-in", "POST"), ("/api/v1/storeops/timeclock/status", "GET"),
           ("/api/v1/core/employee-dashboard", "GET"), ("/api/v1/storeops/my-chargebacks", "GET"),
           ("/api/v1/storeops/shifts", "GET"), ("/api/v1/storeops/time-off", "POST"),
           ("/api/v1/storeops/shift-swaps", "POST"), ("/api/v1/commcalc/targets/2026-09/action-plan", "GET"),
           ("/api/v1/helpdesk/tickets", "POST"), ("/api/v1/hr/onboarding/me/sign", "POST"),
           ("/api/v1/closing/row", "POST"), ("/api/v1/core/me", "GET"), ("/api/v1/core/bootstrap", "GET")]
for p, m in ALLOWED:
    check(f"B+ {m} {p} is allowed", RS.api_allowed(p, m), "refused")
check("B20 a read-only lookup is refused for a WRITE method",
      RS.api_allowed("/api/v1/storeops/stores", "GET") and not RS.api_allowed("/api/v1/storeops/stores", "POST"))
check("B21 the employee roster is readable (already narrowed server-side) and never writable",
      RS.api_allowed("/api/v1/storeops/employees/visible", "GET")
      and not RS.api_allowed("/api/v1/storeops/employees/visible", "POST"))
check("B22 the shift WRITES are not an employee's",
      not RS.api_allowed("/api/v1/storeops/shifts", "POST")
      and not RS.api_allowed("/api/v1/storeops/shift-templates/save-week", "POST"))
check("B23 a path that merely STARTS LIKE a declared one is refused",
      not RS.api_allowed("/api/v1/storeops/timeclock-permissions/9/decision", "POST")
      and not RS.api_allowed("/api/v1/storeops/time-off-balances", "GET"))
# ── §B2. THE ASK IS NOT THE ANSWER (the security pass on this change, 2026-11-04) ────────────────
# The first draft granted whole prefixes for every method, so the POST that ASKS for time off also
# carried the PATCH that APPROVES it, and those handlers had no authority of their own: a rep could
# approve their own leave. Each pair below REPRODUCES the hole (the ask stays allowed, which is what
# made the grant look reasonable) and then shows the answer refused.
print("\n§B2 a grant on a collection is not a grant on its members")
PAIRS = [
    # (the ask — must stay ALLOWED, or an employee cannot ask),  (the answer — must be REFUSED)
    (("/api/v1/storeops/time-off", "POST"), ("/api/v1/storeops/time-off/41", "PATCH")),
    (("/api/v1/storeops/shift-swaps", "POST"), ("/api/v1/storeops/shift-swaps/7", "PATCH")),
    (("/api/v1/storeops/shift-extensions", "POST"),
     ("/api/v1/storeops/shift-extensions/9/decision", "POST")),
    (("/api/v1/closing/row", "POST"), ("/api/v1/closing/row/9", "DELETE")),
    (("/api/v1/helpdesk/tickets", "POST"), ("/api/v1/helpdesk/config/teams", "POST")),
    (("/api/v1/chat/channels/1/messages", "POST"), ("/api/v1/chat/admin/retention", "POST")),
]
for (ap, am), (rp, rm) in PAIRS:
    check(f"B2+ the ask {am} {ap} is still allowed", RS.api_allowed(ap, am), "an employee cannot ask")
    check(f"B2- the answer {rm} {rp} is refused", not RS.api_allowed(rp, rm), RS.surface_for_api(rp, rm))
check("B2a the org-wide time-off cleanup is not an employee's",
      not RS.api_allowed("/api/v1/storeops/time-off/reconcile-duplicates", "POST"))
check("B2b the help desk's agent console is refused, not just its page",
      not RS.api_allowed("/api/v1/helpdesk/support/sla-policy", "PUT")
      and not RS.api_allowed("/api/v1/helpdesk/support/cases/3/assign", "POST"))
check("B2c the closing CONFIG writes are refused while their reads stay (the kiosk form reads them)",
      not RS.api_allowed("/api/v1/closing/tender-config", "PUT")
      and not RS.api_allowed("/api/v1/closing/cash-config", "PUT")
      and not RS.api_allowed("/api/v1/closing/sweep/run-now", "POST")
      and RS.api_allowed("/api/v1/closing/tender-config", "GET")
      and RS.api_allowed("/api/v1/closing/count-config", "GET"))
check("B2d the timeclock ADMIN writes are refused while the punch and the manager override stay",
      not RS.api_allowed("/api/v1/storeops/timeclock/geo-config", "PUT")
      and not RS.api_allowed("/api/v1/storeops/timeclock/attendance-config", "PUT")
      and not RS.api_allowed("/api/v1/storeops/timeclock/permissions/3/decision", "POST")
      and RS.api_allowed("/api/v1/storeops/timeclock/clock-in", "POST")
      and RS.api_allowed("/api/v1/storeops/timeclock/clock-out", "POST")
      and RS.api_allowed("/api/v1/storeops/timeclock/override", "POST"))
check("B2e a carve-out refuses every method, a READ included — it is not a method narrowing",
      not RS.api_allowed("/api/v1/storeops/time-off/41", "GET"))
check("B2f …and a trace names no surface for a carved route, so nothing can report it as granted",
      RS.surface_for_api("/api/v1/storeops/time-off/41", "PATCH") is None)
check("B2g every carved entry really is below a granted prefix — a carve-out of something never "
      "granted is a comment pretending to be a gate",
      all(any(_g == e.split("/**")[0].rstrip("/") or e.startswith(_g.rstrip("/") + "/")
              for s in RS.SURFACES for _g in s["api"])
          for e in RS.api_exceptions()),
      [e for e in RS.api_exceptions()
       if not any(_g == e.split("/**")[0].rstrip("/") or e.startswith(_g.rstrip("/") + "/")
                  for s in RS.SURFACES for _g in s["api"])])

check("B24 every declared API prefix resolves to a surface (its own, or the universal set it is "
      "also in — the own-password path is deliberately both)",
      all(RS.surface_for_api(a, "POST") in (s["key"], "universal")
          for s in RS.SURFACES for a in s["api"]),
      [(s["key"], a, RS.surface_for_api(a, "POST")) for s in RS.SURFACES for a in s["api"]
       if RS.surface_for_api(a, "POST") not in (s["key"], "universal")])
check("B25 every declared read-only prefix resolves too",
      all(RS.surface_for_api(a, "GET") in (s["key"], "universal")
          for s in RS.SURFACES for a in s["api_read"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. THE MIDDLEWARE VERDICT — the real function, over the real role shapes")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.core import tenant_middleware as TM                # noqa: E402

ORG_A, ORG_B = "org-aaaa", "org-bbbb"
SELF_SCOPED = {(ORG_A, "sales_rep"), (ORG_B, "sales_rep")}


def _stub_self(org, role, rbac_on=None):
    return (str(org), str(role)) in SELF_SCOPED


import app.modules.storeops.router as SR                    # noqa: E402
_real_self = SR.role_is_self_scoped
SR.role_is_self_scoped = _stub_self
TM._rep_cache.clear() if hasattr(TM._rep_cache, "clear") else None


def verdict(org, role, path, method="GET"):
    TM._rep_cache = type(TM._rep_cache)(ttl=0.0001, cap=16)   # fresh cache per question
    return TM._rep_surface_refusal(org, role, path, method)


check("C1 a rep is REFUSED the hours-adjustment write",
      bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST")))
check("C2 the refusal names what they CAN use, not what they cannot",
      "Clock in / out" in (verdict(ORG_A, "sales_rep", "/api/v1/storeops/payroll", "GET") or ""))
check("C3 a rep is SERVED the clock-in write",
      verdict(ORG_A, "sales_rep", "/api/v1/storeops/timeclock/clock-in", "POST") is None)
check("C4 a store manager is refused NOTHING (not an individual contributor)",
      verdict(ORG_A, "store_manager", "/api/v1/storeops/manual-hours", "POST") is None)
check("C5 a market manager is refused nothing",
      verdict(ORG_A, "market_manager", "/api/v1/storeops/payroll", "GET") is None)
check("C6 an admin is refused nothing",
      verdict(ORG_A, "admin", "/api/v1/core/tenants", "GET") is None)
os.environ["REP_CONFINE"] = "0"
check("C7 the break-glass REP_CONFINE=0 lifts it entirely",
      verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST") is None)
os.environ.pop("REP_CONFINE")
check("C8 …and it is back on by default",
      bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST")))


def _boom(org, role, rbac_on=None):
    raise RuntimeError("roles table unreadable")


SR.role_is_self_scoped = _boom
check("C9 an UNREADABLE roles table refuses nobody — unreadable is never the restrictive answer",
      verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST") is None)
SR.role_is_self_scoped = _stub_self
check("C10 a declared path never costs a role read at all",
      verdict(ORG_A, "anything-at-all", "/api/v1/core/me", "GET") is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. EVERY TENANT — the declaration is house policy, not a tenant's vocabulary")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
import inspect                                              # noqa: E402

import ast                                                  # noqa: E402

_src = inspect.getsource(RS)
_tree = ast.parse(_src)
# CODE only: the module docstring quotes the owner (who named a tenant) and the comments explain the
# defect, so the RULE TWO / no-org_id assertions are made against what actually EXECUTES.
_code = ast.unparse(ast.Module(body=[n for n in _tree.body
                                     if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                             and isinstance(n.value.value, str))],
                               type_ignores=[])).lower()
check("E1 nothing that executes takes an org_id — one answer for every tenant by construction",
      "org_id" not in _code and "org-" not in _code, _code[:200])
check("E2 no tenant, carrier or product is named in what executes (RULE TWO)",
      not any(w in _code for w in ("cellfonz", "luxelink", "vzone", "logistics", "boost")),
      [w for w in ("cellfonz", "luxelink", "vzone", "logistics", "boost") if w in _code])
check("E3a no public function of the declaration accepts an org at all",
      all("org" not in p for f in ("page_allowed", "api_allowed", "tiles", "pages", "refusal")
          for p in inspect.signature(getattr(RS, f)).parameters))
check("E3 tenant A's rep is confined and tenant B's company role is not, in the same process",
      bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/payroll", "GET"))
      and verdict(ORG_B, "admin", "/api/v1/storeops/payroll", "GET") is None)
check("E4 the SAME role name is answered per tenant, never once for the house",
      bool(verdict(ORG_B, "sales_rep", "/api/v1/storeops/payroll", "GET")))
SELF_SCOPED.discard((ORG_B, "sales_rep"))
check("E5 …so a tenant whose role is NOT self-scoped is not confined by another tenant's answer",
      verdict(ORG_B, "sales_rep", "/api/v1/storeops/payroll", "GET") is None
      and bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/payroll", "GET")))
SELF_SCOPED.add((ORG_B, "sales_rep"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. NO LOCKOUT (index §22)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for p in ("/api/v1/core/me", "/api/v1/core/bootstrap", "/api/v1/core/auth-config",
          "/api/v1/core/my-tenants", "/api/v1/commcalc/nav-config", "/api/v1/core/me/2fa/start"):
    check(f"F- {p} can never be refused", RS.api_allowed(p, "POST") or RS.api_allowed(p, "GET"))
check("F7 changing their own password is a declared page", RS.page_allowed("/account/password"))
check("F8 a confined viewer is always handed the landing to go to",
      (RS.viewer_payload(True, "self") or {}).get("landing") == RS.LANDING_PAGE)
check("F9 a store manager is landed but NOT handed a page allowlist (they are not confined)",
      (RS.viewer_payload(False, "store") or {}) == {"confined": False, "landing": RS.LANDING_PAGE,
                                                    "tiles": [dict(t) for t in RS.tiles()]})
check("F10 a market/company role is handed nothing at all — byte-identical to before",
      RS.viewer_payload(False, "all") is None and RS.viewer_payload(False, "market") is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. THE FOUR WRITES THAT ASKED NOTHING — the real handlers over the double")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
UID_REP, UID_MGR = "uid-rep", "uid-mgr"
REP_EID, MATE_EID, FOREIGN_EID = "E100", "E101", "E999"
fake = FakeSupabase()
fake.seed("storeops.app_config", [{"id": 1, "rbac_enabled": True}])
fake.seed("storeops.app_users", [
    {"id": "au1", "org_id": ORG_A, "auth_id": UID_REP, "role": "sales_rep", "employee_id": REP_EID},
    {"id": "au2", "org_id": ORG_A, "auth_id": UID_MGR, "role": "store_manager", "employee_id": "E200"}])
fake.seed("storeops.employees", [
    {"id": "1", "org_id": ORG_A, "employee_id": REP_EID, "name": "A Rep"},
    {"id": "2", "org_id": ORG_A, "employee_id": MATE_EID, "name": "A Teammate"},
    {"id": "3", "org_id": ORG_A, "employee_id": FOREIGN_EID, "name": "Another Store"}])
fake.seed("storeops.manual_hours", [{"id": "mh1", "org_id": ORG_A, "employee_id": MATE_EID,
                                     "work_date": "2026-11-03", "hours": 2.0, "reason": "forgot"}])

SR.sb = lambda: fake.schema("storeops")
SR.get_supabase = lambda: fake
# WHOSE PEOPLE THESE ARE is `schedule_emp_ids` — the §14w one home. Stubbed here at that ONE seam
# (not re-implemented) so the proof exercises the real handlers and the real `require_person_write`.
_PEOPLE = {UID_REP: {REP_EID}, UID_MGR: {"E200", REP_EID, MATE_EID}}
_who = {"auth": UID_REP}
SR.schedule_emp_ids = lambda authorization="", org_id=ORG_A, **kw: _PEOPLE.get(authorization)
SR._caller_app_user = lambda authorization="", org_id=ORG_A: next(
    (r for r in fake.schema("storeops").table("app_users").select("*").execute().data
     if r["auth_id"] == authorization and r["org_id"] == org_id), {})
SR.role_is_self_scoped = lambda org, role, rbac_on=None: (str(org), str(role).strip()) in SELF_SCOPED
SR._canonical_shift_employee_id = lambda org, eid: str(eid)


def refused(fn, *a, **kw):
    from fastapi import HTTPException
    try:
        fn(*a, **kw)
        return None
    except HTTPException as e:
        return f"{e.status_code}: {e.detail}"


body = SR.ManualHoursIn(employee_id=MATE_EID, hours=3, reason="x", work_date="2026-11-04")
r = refused(SR.add_manual_hours, body, authorization=UID_REP, org_id=ORG_A)
check("D1 THE OWNER'S REPORT: a rep cannot add an hours adjustment at all", r and r.startswith("403"), r)
check("D2 …and the refusal says it is a manager action", "manager action" in (r or ""))
r = refused(SR.add_manual_hours, SR.ManualHoursIn(employee_id=REP_EID, hours=3, reason="x"),
            authorization=UID_REP, org_id=ORG_A)
check("D3 …not even for themselves", r and r.startswith("403"), r)
r = refused(SR.add_manual_hours, SR.ManualHoursIn(employee_id=MATE_EID, hours=3, reason="x",
                                                 work_date="2026-11-04"),
            authorization=UID_MGR, org_id=ORG_A)
check("D4 the manager whose team they are on CAN", r is None, r)
r = refused(SR.add_manual_hours, SR.ManualHoursIn(employee_id=FOREIGN_EID, hours=3, reason="x"),
            authorization=UID_MGR, org_id=ORG_A)
check("D5 …and cannot for someone who is not on their team", r and r.startswith("403"), r)
check("D6 a manual-hours row did land for the allowed write",
      any(x["employee_id"] == MATE_EID and x.get("hours") == 3.0
          for x in fake.schema("storeops").table("manual_hours").select("*").execute().data))
r = refused(SR.delete_manual_hours, "mh1", authorization=UID_REP, org_id=ORG_A)
check("D7 a rep cannot DELETE an hours adjustment either", r and r.startswith("403"), r)
check("D8 …and the row is still there", any(x["id"] == "mh1" for x in
      fake.schema("storeops").table("manual_hours").select("*").execute().data))
r = refused(SR.delete_manual_hours, "mh1", authorization=UID_MGR, org_id=ORG_A)
check("D9 their manager can", r is None, r)

r = refused(SR.create_time_off, {"employee_id": REP_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization=UID_REP, org_id=ORG_A)
check("D10 a rep CAN file their own time off", r is None, r)
r = refused(SR.create_time_off, {"employee_id": FOREIGN_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization=UID_REP, org_id=ORG_A)
check("D11 …and cannot file it in a stranger's name (reported 2026-11-01)", r and r.startswith("403"), r)
r = refused(SR.create_time_off, {"employee_id": MATE_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization=UID_MGR, org_id=ORG_A)
check("D12 THE OWNER'S RULE: a manager CAN file time off for their own employee", r is None, r)
r = refused(SR.create_shift_swap, {"requester_id": FOREIGN_EID}, authorization=UID_REP, org_id=ORG_A)
check("D13 a swap cannot be raised in a stranger's name", r and r.startswith("403"), r)
r = refused(SR.create_shift_swap, {"requester_id": REP_EID}, authorization=UID_REP, org_id=ORG_A)
check("D14 …and an employee can raise their own", r is None, r)

import app.modules.hr.router as HR                          # noqa: E402

HR._so = lambda: fake.schema("storeops")
HR.onboarding_template = lambda org_id=ORG_A: {"ready": False, "categories": []}
r = refused(HR.onboarding_for_employee, FOREIGN_EID, authorization=UID_REP, org_id=ORG_A)
check("D15 THE OWNER'S REPORT: a rep cannot open anyone else's onboarding paperwork",
      r and r.startswith("403"), r)
r = refused(HR.onboarding_for_employee, REP_EID, authorization=UID_REP, org_id=ORG_A)
check("D16 …and can open their own", r is None, r)
r = refused(HR.onboarding_for_employee, MATE_EID, authorization=UID_MGR, org_id=ORG_A)
check("D17 …and their manager can open theirs", r is None, r)
r = refused(HR.onboarding_set_profile, FOREIGN_EID, HR.OnboardingSetProfileIn(work_state="NY"),
            authorization=UID_REP, org_id=ORG_A)
check("D18 …nor CHANGE a stranger's paperwork", r and r.startswith("403"), r)
r = refused(SR.create_time_off, {"employee_id": REP_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization="", org_id=ORG_A)
check("D19 an in-process / tokenless caller is UNRESTRICTED, exactly as before (a sweep, a cron)",
      r is None, r)

SR.role_is_self_scoped = _real_self
print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
