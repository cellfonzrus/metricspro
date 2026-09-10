"""HARNESS — server-side pay-visibility RBAC (pay_visibility.py, migration 434).

OWNER SPEC (charter rule 4): pay-per-hour, gross pay and salary hidden by default from every level
below market manager; market manager and above see them; per-org configurable, nothing hardcoded;
enforced SERVER-side so a gated money column never leaks through an export (RULE FOUR).

  A. resolve_pay_access truth table — all three modes x roles (admin / master_admin /
     market_manager / store_manager / sales_rep / district_manager / unknown) x grant on/off;
     unknown role and unknown MODE both fail closed.
  B. The built-in default allow-list IS "market manager and above" — and the per-org config
     (visible_roles / the grant / scope 'all') beats it.
  C. strip_pay — deletes, never zeros; totals; odd shapes; never raises; idempotent.
  D. Adaptive tenant config — missing column/row/table resolves to the restrictive owner default
     ('manager_up'), never open; garbage values are clamped.
  E. can_see_pay wrapper end-to-end (fake core + fake DB) — modes, open-app parity, fail-closed.
  F. payroll_approval still exposes the SAME public names with the SAME (stricter, deny-list,
     byte-identical) behavior — including the one documented divergence: a market manager sees pay
     on the money reports ('manager_up') but NOT on the approvals board.
  G. grant_allowed mirror of account.report_gates.grant_allowed.
  H. ARMED negative control.
  J. THE DM DIRECTIVE (owner 2026-09-10, "the dm should not be able to see the salaries of any
     employees and should be gated and allowed if wanted") — every spelling of the DM role is denied
     under the default mode and by a pre-434 database; the per-org config genuinely opens it again
     (pay_visible_roles in any spelling, or 'permissioned' + the employee_pay_rates grant), and can
     be closed again; _norm_role's folding is pinned; a DM role misconfigured with scope 'all' is
     pinned as the ONE remaining open door (a role-scope fix, not a code one).
  K. THE SURFACES BOUND FOR THAT DIRECTIVE — each route's REAL shipped source (AST-extracted, same
     technique as I) vs. the real gate: GET /storeops/employees, /storeops/payroll-change-log,
     PATCH /storeops/employees/{id}, /storeops/pto-accrual/{period},
     /storeops/salary-advance/additional-payroll/{period}, /storeops/salary-advance/history,
     GET /core/employees (+ its /hr/employees delegate), the /core/employee-dashboard pay guard
     (with its deliberate SELF-PAY carve-out), the /marketing/event-sales/roi event-payroll guard
     against the REAL payroll_from_hours payload, and POST /hr/employees. Each pins BOTH halves:
     the per-employee pay keys are DELETED for a DM, and the STORE-LEVEL aggregates (store PTO cost,
     the additional-payroll cells/total, the event's labour total, every hours figure) are still
     there — plus a reproduce-then-fix regression for the reported defect.
  I. GET /storeops/payroll-raw route gate (§19.12 closure, 2026-09-01) — the REAL shipped
     `payroll_raw_route` source (AST-extracted from storeops/router.py, no full-router import)
     against the real gate: ALL-money feed, so denial FAILS CLOSED (403) like its scheduled twin
     `storeops_payroll_tax`; allowed callers get the payload byte-identical; the shared
     payroll_raw() stays undecorated/ungated for in-process consumers that bring their own gate.

Run: python3 harness_pay_visibility.py     (stdlib-only — fastapi/core/db are stubbed)
"""
import sys
import os
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


# ── stdlib-only stubs (pay_visibility itself needs none of these; payroll_approval's module-level
#    imports do, and can_see_pay's LAZY core import is faked so the wrapper is provable end-to-end) ─
_fastapi = types.ModuleType("fastapi")


class _HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class _APIRouter:
    def __init__(self, *a, **k):
        pass

    def _deco(self, *a, **k):
        return lambda f: f
    get = post = put = patch = delete = _deco


_fastapi.APIRouter, _fastapi.HTTPException = _APIRouter, _HTTPException
_fastapi.Header = lambda default="": default
_fastapi.Response = type("Response", (), {})
sys.modules["fastapi"] = _fastapi

_db = types.ModuleType("app.core.database")


def _no_db():
    raise RuntimeError("no live DB in this harness")


_db.get_supabase = _no_db
sys.modules["app.core.database"] = _db

_schemas = types.ModuleType("app.core.schemas")
_schemas.LaxModel = type("LaxModel", (), {})
sys.modules["app.core.schemas"] = _schemas

# Fake core resolver: token -> uid -> caller. Inserted at the LAZY-import path pay_visibility and
# payroll_approval both use, so the real resolution SEAMS are exercised.
CALLERS = {
    "u-admin": {"org_id": "ORG1", "role": "admin", "super_admin": False, "perms": {"scope": "all"}},
    "u-super": {"org_id": "ORG1", "role": "rep", "super_admin": True, "perms": {"scope": "self"}},
    "u-mm": {"org_id": "ORG1", "role": "market_manager", "super_admin": False, "perms": {"scope": "market"}},
    "u-dm": {"org_id": "ORG1", "role": "district_manager", "super_admin": False, "perms": {"scope": "market"}},
    "u-sm": {"org_id": "ORG1", "role": "store_manager", "super_admin": False, "perms": {"scope": "store"}},
    "u-rep": {"org_id": "ORG1", "role": "sales_rep", "super_admin": False, "perms": {"scope": "self"}},
    "u-sm-grant": {"org_id": "ORG1", "role": "store_manager", "super_admin": False,
                   "perms": {"scope": "store", "data": {"employee_pay_rates": True}}},
    "u-hr": {"org_id": "ORG1", "role": "hr_manager", "super_admin": False, "perms": {"scope": "all"}},
    "u-ghost": None,   # verified token whose membership row is gone
}
_core = types.ModuleType("app.modules.core.router")


def _uid_from_token(auth):
    if auth == "Bearer broken":
        raise RuntimeError("verifier down")
    return {"Bearer admin": "u-admin", "Bearer super": "u-super", "Bearer mm": "u-mm",
            "Bearer dm": "u-dm", "Bearer sm": "u-sm", "Bearer rep": "u-rep",
            "Bearer sm-grant": "u-sm-grant", "Bearer hr": "u-hr", "Bearer ghost": "u-ghost",
            }.get(auth)


def _resolve_caller(client, uid, active_org=None):
    return CALLERS.get(uid)


def _can_edit_setting(caller, area):
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    return ((caller.get("perms") or {}).get("scope") == "all") or \
        ((caller.get("role") or "").lower() == "admin")


_core._uid_from_token, _core._resolve_caller = _uid_from_token, _resolve_caller
_core._can_edit_setting = _can_edit_setting
_core.sb = lambda: FAKE_DEFAULT   # used by the approvals deny-list gate
sys.modules["app.modules.core.router"] = _core


# ── fake supabase client (tenants + app_config only — all the wrapper ever reads) ─────────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, rows, fail):
        self._rows, self._fail = rows, fail

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def __getattr__(self, _name):
        """Any other PostgREST verb (order/gte/lte/in_/ilike/...) is a no-op that keeps the chain —
        these fakes answer WHAT a route hands back, never how it filters (§J/§K exercise the gate,
        the span filters have their own harnesses)."""
        return lambda *a, **k: self

    def execute(self):
        if self._fail:
            raise RuntimeError("column does not exist (pre-434)")
        import copy
        return _Resp(copy.deepcopy(self._rows))


class FakeClient:
    def __init__(self, tenants=None, app_config=None, fail_tables=(), tables=None):
        self.tenants, self.app_config, self.fail = tenants or [], app_config or [], set(fail_tables)
        self.tables = tables or {}          # any other table the route under test reads

    def schema(self, name):
        return self

    def table(self, name):
        rows = {"tenants": self.tenants, "app_config": self.app_config}.get(
            name, self.tables.get(name, []))
        return _Table(rows, name in self.fail)


FAKE_DEFAULT = FakeClient()

import app.modules.storeops.pay_visibility as pv                     # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. resolve_pay_access — full truth table (fails closed on unknowns)")
ROLES = ("admin", "master_admin", "market_manager", "store_manager", "sales_rep", "district_manager")
# mode 'all': EVERYONE, grant or not, known role or not.
for role in ROLES + ("totally_unknown", "", None):
    for grant in (False, True):
        check(f"A-all: mode=all role={role!r} grant={grant} -> True",
              pv.resolve_pay_access("all", role, "store", None, grant), True)
# mode 'manager_up', default allow-list, narrow scope, no grant:
MU_WANT = {"admin": True, "master_admin": True, "market_manager": True,
           "store_manager": False, "sales_rep": False, "district_manager": False}
for role, want in MU_WANT.items():
    check(f"A-mu: manager_up role={role} scope=store grant=False -> {want}",
          pv.resolve_pay_access("manager_up", role, "store", None, False), want)
    check(f"A-mu: manager_up role={role} scope=store grant=True -> True (grant beats the default)",
          pv.resolve_pay_access("manager_up", role, "store", None, True), True)
check("A-mu: unknown role, narrow scope, no grant -> False (fails closed)",
      pv.resolve_pay_access("manager_up", "totally_unknown", "store", None, False), False)
check("A-mu: EMPTY role, narrow scope -> False (unresolvable = hidden)",
      pv.resolve_pay_access("manager_up", "", "store", None, False), False)
check("A-mu: None role -> False", pv.resolve_pay_access("manager_up", None, None, None, False), False)
check("A-mu: scope 'all' passes REGARDLESS of role name (company-wide = above market manager)",
      pv.resolve_pay_access("manager_up", "director_of_ops", "all", None, False), True)
check("A-mu: scope 'market' alone does NOT pass — the role must be market-level per the allow-list",
      pv.resolve_pay_access("manager_up", "district_manager", "market", None, False), False)
check("A-mu: scope 'market' + market_manager role -> True (via the allow-list)",
      pv.resolve_pay_access("manager_up", "market_manager", "market", None, False), True)
# mode 'permissioned': the grant, and ONLY the grant (the wrapper's grant_allowed hands the grant
# to super-admins / scope-'all' / 'admin' implicitly — proven in section G).
for role in ROLES:
    check(f"A-perm: permissioned role={role} grant=False -> False",
          pv.resolve_pay_access("permissioned", role, "store", None, False), False)
    check(f"A-perm: permissioned role={role} grant=True -> True",
          pv.resolve_pay_access("permissioned", role, "store", None, True), True)
# unknown / empty mode -> the restrictive owner default, never open:
check("A-unk: unknown mode behaves as manager_up for admin (True)",
      pv.resolve_pay_access("banana", "admin", "store", None, False), True)
check("A-unk: unknown mode behaves as manager_up for sales_rep (False) — NEVER open",
      pv.resolve_pay_access("banana", "sales_rep", "store", None, False), False)
check("A-unk: None mode = owner default (market_manager passes)",
      pv.resolve_pay_access(None, "market_manager", "market", None, False), True)
check("A-unk: None mode = owner default (store_manager hidden)",
      pv.resolve_pay_access(None, "store_manager", "store", None, False), False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. default allow-list IS 'market manager and above'; per-org config beats it")
check("B1: DEFAULT_VISIBLE_ROLES == {admin, master_admin, market_manager, market}",
      set(pv.DEFAULT_VISIBLE_ROLES), {"admin", "master_admin", "market_manager", "market"})
check("B2: 'market' alias in the default list resolves", pv.resolve_pay_access("manager_up", "market", "market"), True)
check("B3: role-name normalization — 'Market Manager' matches 'market_manager'",
      pv.resolve_pay_access("manager_up", "Market Manager", "market"), True)
check("B4: 'market-manager' matches too", pv.resolve_pay_access("manager_up", "market-manager", "market"), True)
check("B5: explicit pay_visible_roles REPLACES the default (store_manager in, so True)",
      pv.resolve_pay_access("manager_up", "store_manager", "store", ["Store Manager"], False), True)
check("B6: explicit pay_visible_roles REPLACES the default (market_manager NOT listed -> False)",
      pv.resolve_pay_access("manager_up", "market_manager", "market", ["Store Manager"], False), False)
check("B7: ...but scope 'all' still passes even when not listed (company-wide is always above)",
      pv.resolve_pay_access("manager_up", "market_manager", "all", ["Store Manager"], False), True)
check("B8: grant key matches rbac.ts DATA_GRANTS", pv.PAY_GRANT_KEY, "employee_pay_rates")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. strip_pay — deletes (never zeros), totals, odd shapes, never raises, idempotent")
rows = [{"employee_id": "E1", "name": "A", "actual_hours": 40.0, "pay_rate": 20.0,
         "scheduled_pay": 800.0, "actual_pay": 800.0, "gross_pay": 800.0, "pay_per_hour": 20.0,
         "wages": 800.0, "salary_period_pay": 1000.0, "base_salary": 1000.0, "total_comp": 1100.0,
         "annualized": 13200.0, "pay_amount": 52000.0, "net_pay": 700.0, "payable_pay": 800.0,
         "pay_effective": 800.0, "salary_derived_pay": 1000.0}]
totals = {"employees": 1, "hours": 40.0, "pay": 800.0, "payable_pay": 800.0, "actual_pay": 800.0,
          "scheduled_pay": 800.0, "base_salary": 1000.0, "total_comp": 1100.0, "annualized": 13200.0,
          "wages": 800.0, "gross_pay": 800.0}
r2, t2 = pv.strip_pay(rows, totals)
check("C1: every PAY_FIELDS key DELETED from the row (absent, not zeroed)",
      [k for k in pv.PAY_FIELDS if k in r2[0]], [])
check("C2: hours / identity keys untouched",
      (r2[0]["actual_hours"], r2[0]["employee_id"], r2[0]["name"]), (40.0, "E1", "A"))
check("C3: no stripped key re-appears as 0", 0 in r2[0].values(), False)
check("C4: every PAY_TOTALS_FIELDS key deleted from totals", [k for k in pv.PAY_TOTALS_FIELDS if k in t2], [])
check("C5: non-pay totals keys survive", (t2["employees"], t2["hours"]), (1, 40.0))
r3, t3 = pv.strip_pay(r2, t2)
check("C6: idempotent — second strip is a no-op", (r3, t3), (r2, t2))
check("C7: rows=None never raises", pv.strip_pay(None, None), (None, None))
check("C8: a single DICT payload (detail endpoint) is stripped in place",
      "pay_rate" in pv.strip_pay({"pay_rate": 9, "days": []})[0], False)
odd = [None, 5, "x", {"pay_rate": 1, "h": 2}]
pv.strip_pay(odd)
check("C9: odd non-dict entries tolerated; the dict entry still stripped",
      odd[3], {"h": 2})
check("C10: non-iterable rows (int) never raises", pv.strip_pay(42, {"pay": 1})[0], 42)
check("C11: ...and totals are STILL stripped on that path (tolerance is not a leak)",
      pv.strip_pay(42, {"pay": 1, "hours": 2})[1], {"hours": 2})
check("C12: custom fields tuple (endpoint-local keys, e.g. /salary-owed)",
      pv.strip_pay([{"owed": 5, "hours": 8}], fields=("owed",))[0], [{"hours": 8}])
check("C13: totals=None tolerated", pv.strip_pay([], None), ([], None))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. adaptive tenant config — pre-434 / missing / garbage all resolve to the owner default")
check("D1: tenants read RAISES (pre-434 column missing) -> ('manager_up', None)",
      pv.tenant_pay_visibility("ORG1", FakeClient(fail_tables={"tenants"})), ("manager_up", None))
check("D2: no tenant row -> default", pv.tenant_pay_visibility("ORG1", FakeClient()), ("manager_up", None))
check("D3: NULL column values -> default",
      pv.tenant_pay_visibility("ORG1", FakeClient(tenants=[{"pay_visibility": None, "pay_visible_roles": None}])),
      ("manager_up", None))
check("D4: garbage mode clamped to the default, NEVER open",
      pv.tenant_pay_visibility("ORG1", FakeClient(tenants=[{"pay_visibility": "everyone!!"}])),
      ("manager_up", None))
check("D5: mode normalized ('  ALL ' -> 'all')",
      pv.tenant_pay_visibility("ORG1", FakeClient(tenants=[{"pay_visibility": "  ALL "}])), ("all", None))
check("D6: explicit allow-list passes through",
      pv.tenant_pay_visibility("ORG1", FakeClient(tenants=[{"pay_visibility": "manager_up",
                                                            "pay_visible_roles": ["Ops Lead"]}])),
      ("manager_up", ["Ops Lead"]))
check("D7: empty-array allow-list means 'not configured' (None -> built-in default)",
      pv.tenant_pay_visibility("ORG1", FakeClient(tenants=[{"pay_visibility": "manager_up",
                                                            "pay_visible_roles": []}])),
      ("manager_up", None))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. can_see_pay wrapper end-to-end (fake core + fake DB)")
T_MU = FakeClient(tenants=[{"pay_visibility": "manager_up"}])
T_ALL = FakeClient(tenants=[{"pay_visibility": "all"}])
T_PERM = FakeClient(tenants=[{"pay_visibility": "permissioned"}])
T_PRE434 = FakeClient(fail_tables={"tenants"})
check("E1: manager_up — admin token sees pay", pv.can_see_pay("Bearer admin", "ORG1", T_MU), True)
check("E2: manager_up — super_admin (any role) sees pay", pv.can_see_pay("Bearer super", "ORG1", T_MU), True)
check("E3: manager_up — market manager sees pay (the owner line: MM and above)",
      pv.can_see_pay("Bearer mm", "ORG1", T_MU), True)
check("E4: manager_up — district manager below MM: hidden", pv.can_see_pay("Bearer dm", "ORG1", T_MU), False)
check("E5: manager_up — store manager hidden", pv.can_see_pay("Bearer sm", "ORG1", T_MU), False)
check("E6: manager_up — sales rep hidden", pv.can_see_pay("Bearer rep", "ORG1", T_MU), False)
check("E7: manager_up — store manager WITH the employee_pay_rates grant sees pay (per-org config)",
      pv.can_see_pay("Bearer sm-grant", "ORG1", T_MU), True)
check("E8: manager_up — company-wide (scope all) HR role keeps its money view",
      pv.can_see_pay("Bearer hr", "ORG1", T_MU), True)
check("E9: 'all' — everyone, even a sales rep", pv.can_see_pay("Bearer rep", "ORG1", T_ALL), True)
check("E10: 'all' — even unauthenticated", pv.can_see_pay("", "ORG1", T_ALL), True)
check("E11: permissioned — market manager WITHOUT the grant hidden",
      pv.can_see_pay("Bearer mm", "ORG1", T_PERM), False)
check("E12: permissioned — the grant opens it", pv.can_see_pay("Bearer sm-grant", "ORG1", T_PERM), True)
check("E13: permissioned — admin holds the grant implicitly", pv.can_see_pay("Bearer admin", "ORG1", T_PERM), True)
check("E14: BROKEN token verifier on a gated mode -> hidden (fail closed)",
      pv.can_see_pay("Bearer broken", "ORG1", T_MU), False)
check("E15: verified uid whose membership is GONE -> hidden (fail closed)",
      pv.can_see_pay("Bearer ghost", "ORG1", T_MU), False)
check("E16: unauthenticated + login enforcement OFF -> open-app parity (allowed)",
      pv.can_see_pay("", "ORG1", FakeClient(tenants=[{"pay_visibility": "manager_up"}],
                                            app_config=[{"rbac_enabled": False}])), True)
check("E17: unauthenticated + login enforcement ON -> hidden",
      pv.can_see_pay("", "ORG1", FakeClient(tenants=[{"pay_visibility": "manager_up"}],
                                            app_config=[{"rbac_enabled": True}])), False)
check("E18: unauthenticated + the enforcement flag UNREADABLE -> hidden (fail closed)",
      pv.can_see_pay("", "ORG1", FakeClient(tenants=[{"pay_visibility": "manager_up"}],
                                            fail_tables={"app_config"})), False)
check("E19: pre-434 DB (tenants columns missing) + admin -> allowed under the adaptive default",
      pv.can_see_pay("Bearer admin", "ORG1", T_PRE434), True)
check("E20: pre-434 DB + store manager -> hidden (adaptive default is manager_up, never open)",
      pv.can_see_pay("Bearer sm", "ORG1", T_PRE434), False)
check("E21: non-string authorization (in-process Header sentinel) tolerated -> parity path, rbac off",
      pv.can_see_pay(object(), "ORG1", FakeClient(tenants=[{"pay_visibility": "manager_up"}],
                                                  app_config=[{"rbac_enabled": False}])), True)
check("E22: org_id=None (hr surfaces) — caller's own org resolves the mode",
      pv.can_see_pay("Bearer mm", None, T_MU), True)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. payroll_approval aliases — same names, same (stricter) behavior, byte-identical fields")
import app.modules.storeops.payroll_approval as pa                    # noqa: E402
check("F1: PAY_RATE_HIDDEN_ROLES unchanged",
      set(pa.PAY_RATE_HIDDEN_ROLES), {"district_manager", "dm", "market_manager", "market"})
check("F2: approvals PAY_FIELDS stays the NARROW original", pa.PAY_FIELDS, ("pay_rate", "pay_effective"))
check("F3: approvals PAY_TOTALS_FIELDS stays the original", pa.PAY_TOTALS_FIELDS, ("payable_pay",))
b_rows = [{"pay_rate": 20.0, "pay_effective": 800.0, "hours_effective": 40.0, "scheduled_pay": 1.0}]
b_tot = {"pay": 800.0, "payable_pay": 800.0, "hours": 40.0}
pa._strip_pay(b_rows, b_tot)
check("F4: _strip_pay removes exactly the board's two row keys (scheduled_pay NOT its concern — "
      "byte-identical narrowness)", b_rows, [{"hours_effective": 40.0, "scheduled_pay": 1.0}])
check("F5: _strip_pay removes only payable_pay from totals (totals['pay'] untouched, as before)",
      b_tot, {"pay": 800.0, "hours": 40.0})
check("F6: _is_admin — admin True", pa._is_admin("Bearer admin", "ORG1"), True)
check("F7: _is_admin — dm False", pa._is_admin("Bearer dm", "ORG1"), False)
check("F8: _can_see_pay_rates — admin sees", pa._can_see_pay_rates("Bearer admin", "ORG1"), True)
check("F9: _can_see_pay_rates — DM hidden", pa._can_see_pay_rates("Bearer dm", "ORG1"), False)
check("F10: _can_see_pay_rates — MARKET MANAGER hidden on the approvals board (stricter deny-list)",
      pa._can_see_pay_rates("Bearer mm", "ORG1"), False)
check("F11: ...while the SAME market manager sees pay on the money reports ('manager_up') — the one "
      "documented divergence between the two surfaces",
      pv.can_see_pay("Bearer mm", "ORG1", T_MU), True)
check("F12: _can_see_pay_rates — HR keeps the money view (not in the deny-list)",
      pa._can_see_pay_rates("Bearer hr", "ORG1"), True)
check("F13: _can_see_pay_rates — unresolvable caller hidden (fail closed, unchanged)",
      pa._can_see_pay_rates("", "ORG1"), False)
check("F14: _can_see_pay_rates — who-fallback still works when core resolution yields nothing",
      pa._can_see_pay_rates("", "ORG1", who={"role": "hr"}), True)
check("F15: _can_see_pay_rates — who-fallback role in deny-list hidden",
      pa._can_see_pay_rates("", "ORG1", who={"role": "dm"}), False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. grant_allowed mirror (account.report_gates shape)")
check("G1: caller None -> False", pv.grant_allowed(None, "employee_pay_rates"), False)
check("G2: empty key -> False", pv.grant_allowed({"super_admin": True}, ""), False)
check("G3: super_admin -> True", pv.grant_allowed({"super_admin": True}, "employee_pay_rates"), True)
check("G4: scope 'all' -> True", pv.grant_allowed({"perms": {"scope": "all"}}, "employee_pay_rates"), True)
check("G5: role 'admin' -> True", pv.grant_allowed({"role": "Admin"}, "employee_pay_rates"), True)
check("G6: key in perms.modules (list) -> True",
      pv.grant_allowed({"perms": {"modules": ["employee_pay_rates"]}}, "employee_pay_rates"), True)
check("G7: key in perms.modules (dict, Roles-UI shape) -> True",
      pv.grant_allowed({"perms": {"modules": {"employee_pay_rates": True}}}, "employee_pay_rates"), True)
check("G8: truthy perms.data[key] -> True",
      pv.grant_allowed({"perms": {"data": {"employee_pay_rates": True}}}, "employee_pay_rates"), True)
check("G9: falsy perms.data[key] -> False",
      pv.grant_allowed({"perms": {"data": {"employee_pay_rates": False}}}, "employee_pay_rates"), False)
check("G10: plain scoped role, no grant -> False",
      pv.grant_allowed({"role": "store_manager", "perms": {"scope": "store"}}, "employee_pay_rates"), False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. ARMED negative control — the harness itself can fail")
# If strip_pay ever ZEROED instead of deleting, C1/C3 would be the guards; prove the guard bites by
# feeding a payload where the key survives and confirming the same predicate FAILS.
_leaky = [{"pay_rate": 20.0}]                     # deliberately NOT stripped
_armed = [k for k in pv.PAY_FIELDS if k in _leaky[0]] == []
check("H1: the C1 predicate correctly FAILS on an unstripped payload (guard is armed)", _armed, False)
check("H2: the E4 predicate correctly flips when the mode is 'all' (gate genuinely mode-driven)",
      pv.can_see_pay("Bearer dm", "ORG1", T_ALL), True)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. GET /payroll-raw route gate (§19.12 closure) — real route source, fail-closed 403")
# The full storeops router cannot import stdlib-only (requests, app.core.config, ...), so the ROUTE
# function's real shipped source is AST-extracted from router.py and exec'd against this harness's
# fake core/DB — the same technique-in-spirit as exec'ing the module under stubs: what runs below IS
# the code that serves GET /storeops/payroll-raw, not a re-implementation.
import ast

_router_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "app", "modules", "storeops", "router.py")
with open(_router_path, encoding="utf-8") as _fh:
    _router_src = _fh.read()
_router_tree = ast.parse(_router_src)
_route_fns = [n for n in ast.walk(_router_tree)
              if isinstance(n, ast.FunctionDef) and n.name == "payroll_raw_route"]
_shared_fns = [n for n in ast.walk(_router_tree)
               if isinstance(n, ast.FunctionDef) and n.name == "payroll_raw"]


def _is_payroll_raw_get(dec):
    """True for a `@router.get("/payroll-raw")` decorator node."""
    return (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "get" and dec.args
            and isinstance(dec.args[0], ast.Constant) and dec.args[0].value == "/payroll-raw")


check("I1: exactly one payroll_raw_route in router.py", len(_route_fns), 1)
check("I2: the @router.get('/payroll-raw') registration sits on payroll_raw_route — and ONLY there",
      (sum(1 for d in _route_fns[0].decorator_list if _is_payroll_raw_get(d)),
       sum(1 for n in ast.walk(_router_tree) if isinstance(n, ast.FunctionDef)
           for d in n.decorator_list if _is_payroll_raw_get(d))),
      (1, 1))
check("I3: the shared payroll_raw() is UNDECORATED — in-process consumers (W3 scheduled builder "
      "with its own pre-gate, harness_payroll_data_flow) reach the ungated computation unchanged",
      (len(_shared_fns), _shared_fns[0].decorator_list if _shared_fns else None), (1, []))

# exec the real route source with this harness's fakes wired in at its free names
_CLIENT_HOLDER = [FAKE_DEFAULT]
_CANON_ROW = {"employee_id": "E1", "name": "A", "store": "S1", "pay_rate": 20.0,
              "clocked_hours": 40.0, "manual_hours": 0.0, "total_hours": 40.0, "basis": "clocked",
              "settings": {"filing_status": "Single", "allowances": 0, "state": "NY",
                           "extra_withholding": 0.0, "skipped": False}}


def _fake_payroll_raw(start=None, end=None, authorization="", org_id=None):
    import copy
    return {"start": start, "end": end, "rows": [copy.deepcopy(_CANON_ROW)]}


_route_ns = {"router": _APIRouter(), "Header": _fastapi.Header, "HTTPException": _HTTPException,
             "ORG_ID": "ORG1", "_payvis": pv, "get_supabase": lambda: _CLIENT_HOLDER[0],
             "payroll_raw": _fake_payroll_raw}
exec(compile(ast.Module(body=[_route_fns[0]], type_ignores=[]), _router_path, "exec"), _route_ns)
_route = _route_ns["payroll_raw_route"]


def _hit(auth, client, org_id="ORG1"):
    """(payload, None) when allowed; (None, status_code) when the gate denies."""
    _CLIENT_HOLDER[0] = client
    try:
        return _route(start="2026-07-01", end="2026-07-15", authorization=auth, org_id=org_id), None
    except _HTTPException as e:
        return None, e.status_code


_p, _e = _hit("Bearer admin", T_MU)
check("I4: manager_up — admin passes; payload BYTE-IDENTICAL to the shared function (no strip, "
      "pay_rate + W-4 settings intact)", (_e, _p), (None, _fake_payroll_raw("2026-07-01", "2026-07-15")))
check("I5: manager_up — market manager passes (owner line: MM and above)",
      _hit("Bearer mm", T_MU)[1], None)
for _tok, _who in (("Bearer dm", "district manager"), ("Bearer sm", "store manager"),
                   ("Bearer rep", "sales rep")):
    check(f"I6: manager_up — {_who} below MM: 403, whole payload withheld (ALL-money feed — "
          "fail closed like the storeops_payroll_tax scheduled twin, not stripped)",
          _hit(_tok, T_MU), (None, 403))
check("I7: manager_up — store manager WITH the employee_pay_rates grant passes (config beats default)",
      _hit("Bearer sm-grant", T_MU)[1], None)
check("I8: per-org pay_visible_roles honored — 'Store Manager' listed lets a store manager in...",
      _hit("Bearer sm", FakeClient(tenants=[{"pay_visibility": "manager_up",
                                             "pay_visible_roles": ["Store Manager"]}]))[1], None)
check("I9: ...and an UNLISTED market manager is then denied (allow-list replaces the default)",
      _hit("Bearer mm", FakeClient(tenants=[{"pay_visibility": "manager_up",
                                             "pay_visible_roles": ["Store Manager"]}])), (None, 403))
check("I10: permissioned — market manager WITHOUT the grant: 403", _hit("Bearer mm", T_PERM), (None, 403))
check("I11: permissioned — the grant opens it", _hit("Bearer sm-grant", T_PERM)[1], None)
check("I12: 'all' (legacy open) — even a sales rep passes", _hit("Bearer rep", T_ALL)[1], None)
check("I13: pre-434 DB — adaptive owner default: store manager 403, admin passes",
      (_hit("Bearer sm", T_PRE434)[1], _hit("Bearer admin", T_PRE434)[1]), (403, None))
check("I14: broken token verifier on a gated mode -> 403 (fail closed, never a leak)",
      _hit("Bearer broken", T_MU), (None, 403))
check("I15: ARMED — the SAME denied caller (dm) flips to allowed on an 'all' tenant, so the I6 "
      "denials are genuinely the gate, not a route that always 403s",
      _hit("Bearer dm", T_ALL)[1], None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. THE DM DIRECTIVE (owner 2026-09-10) — a district manager sees NO employee salary by "
        "default, and the per-org config genuinely opens it back up")
# Owner, verbatim: "the dm should s not be able to see the salaries of any employees and should be
# agted and allowed if wanted". Two halves, both pinned here: DENIED by default under every spelling
# a tenant might give the role, and ALLOWED the moment the org's own config says so — config, never
# code (RULE TWO): nothing below adds a role name to any module.

# ── J1. Every DM spelling is denied under the default mode, with no tenant config at all ──────────
DM_SPELLINGS = ("district_manager", "dm", "district", "District Manager", "DISTRICT-MANAGER",
                "  District_Manager  ", "DM", "district manager")
for _r in DM_SPELLINGS:
    check(f"J1: manager_up default — role {_r!r} does NOT see pay",
          pv.resolve_pay_access("manager_up", _r, "market", None, False), False)
check("J1b: none of the DM spellings is in the built-in allow-list (the default list IS "
      "'market manager and above' — the DM sits below it)",
      sorted({pv._norm_role(r) for r in DM_SPELLINGS} & set(pv.DEFAULT_VISIBLE_ROLES)), [])

# ── J2. _norm_role really does fold case/spacing/punctuation, so a tenant may WRITE the role in the
#        config however it likes as long as it names the same word ───────────────────────────────
check("J2: _norm_role folds every spelling of the two-word name onto one key",
      sorted({pv._norm_role(x) for x in
              ("district_manager", "District Manager", "district-manager", "DISTRICT_MANAGER",
               "  District  Manager ", "district.manager")}),
      ["district_manager"])
check("J2b: the short name folds to its own key ('DM' / 'dm' / ' dm ')",
      sorted({pv._norm_role(x) for x in ("DM", "dm", " dm ")}), ["dm"])
check("J2c: DOCUMENTED — 'dm' and 'district_manager' are DIFFERENT keys (exactly like the platform's "
      "existing 'market' / 'market_manager' pair), so an org lists the name it actually uses",
      pv._norm_role("dm") == pv._norm_role("district_manager"), False)

# ── J3. "allowed if wanted", route 1: the org lists its DM role in pay_visible_roles ─────────────
for _cfg_name in ("district_manager", "District Manager", "district-manager", "DISTRICT_MANAGER"):
    check(f"J3: pay_visible_roles={[_cfg_name]!r} lets a role='district_manager' caller see pay",
          pv.resolve_pay_access("manager_up", "district_manager", "market", [_cfg_name], False), True)
check("J3b: an org whose DM role is literally named 'DM' opens it by listing 'dm'",
      pv.resolve_pay_access("manager_up", "DM", "market", ["dm"], False), True)
check("J3c: listing BOTH spellings covers a tenant that uses either",
      [pv.resolve_pay_access("manager_up", r, "market", ["dm", "District Manager"], False)
       for r in ("dm", "district_manager")], [True, True])
check("J3d: adding the DM does NOT quietly drop market manager — the org lists what it wants",
      [pv.resolve_pay_access("manager_up", r, "market",
                             ["market_manager", "district_manager"], False)
       for r in ("market_manager", "district_manager", "store_manager")], [True, True, False])

# ── J4. "allowed if wanted", route 2: 'permissioned' + the employee_pay_rates grant ──────────────
check("J4: permissioned — a DM WITHOUT the grant sees nothing",
      pv.resolve_pay_access("permissioned", "district_manager", "market", None, False), False)
check("J4b: permissioned — the employee_pay_rates grant opens it for the DM",
      pv.resolve_pay_access("permissioned", "district_manager", "market", None, True), True)
check("J4c: manager_up — the grant ALSO opens it (granting the permission never does less than it "
      "says), so an org can open pay to one DM without moving the whole role",
      pv.resolve_pay_access("manager_up", "district_manager", "market", None, True), True)
check("J4d: the grant key is the rbac.ts DATA_GRANTS key, unchanged",
      pv.PAY_GRANT_KEY, "employee_pay_rates")
check("J4e: grant_allowed reads the DM's resolved perms.data — this is what feeds J4b",
      (pv.grant_allowed({"role": "district_manager", "perms": {"scope": "market"}},
                        pv.PAY_GRANT_KEY),
       pv.grant_allowed({"role": "district_manager",
                         "perms": {"scope": "market", "data": {"employee_pay_rates": True}}},
                        pv.PAY_GRANT_KEY)),
      (False, True))

# ── J5. End-to-end through can_see_pay, with a REAL tenant row and the real (faked-seam) resolver ─
_TOKENS = {"Bearer admin": "u-admin", "Bearer super": "u-super", "Bearer mm": "u-mm",
           "Bearer dm": "u-dm", "Bearer sm": "u-sm", "Bearer rep": "u-rep",
           "Bearer sm-grant": "u-sm-grant", "Bearer hr": "u-hr", "Bearer ghost": "u-ghost",
           # DM spellings a tenant might actually store on the login, + a granted DM + a DM whose
           # role was (mis)configured company-wide.
           "Bearer dm-short": "u-dm-short", "Bearer dm-title": "u-dm-title",
           "Bearer dm-grant": "u-dm-grant", "Bearer dm-scope-all": "u-dm-scope-all"}
CALLERS.update({
    "u-dm-short": {"org_id": "ORG1", "role": "DM", "super_admin": False, "perms": {"scope": "market"}},
    "u-dm-title": {"org_id": "ORG1", "role": "District Manager", "super_admin": False,
                   "perms": {"scope": "market"}},
    "u-dm-grant": {"org_id": "ORG1", "role": "district_manager", "super_admin": False,
                   "perms": {"scope": "market", "data": {"employee_pay_rates": True}}},
    "u-dm-scope-all": {"org_id": "ORG1", "role": "district_manager", "super_admin": False,
                       "perms": {"scope": "all"}},
})


def _uid_from_token_v2(auth):
    if auth == "Bearer broken":
        raise RuntimeError("verifier down")
    return _TOKENS.get(auth)


_core._uid_from_token = _uid_from_token_v2          # same lazy seam, more fixtures

T_DM_OPEN = FakeClient(tenants=[{"pay_visibility": "manager_up",
                                 "pay_visible_roles": ["district_manager"]}])
T_DM_OPEN_TITLE = FakeClient(tenants=[{"pay_visibility": "manager_up",
                                       "pay_visible_roles": ["District Manager"]}])
T_DM_SHORT = FakeClient(tenants=[{"pay_visibility": "manager_up", "pay_visible_roles": ["dm"]}])
for _tok in ("Bearer dm", "Bearer dm-short", "Bearer dm-title"):
    check(f"J5: default tenant ('manager_up', no list) — {_tok} sees NO pay",
          pv.can_see_pay(_tok, "ORG1", T_MU), False)
check("J5b: pre-434 database (no config columns at all) — the DM is still denied (adaptive default)",
      pv.can_see_pay("Bearer dm", "ORG1", T_PRE434), False)
check("J5c: the org opens it — pay_visible_roles=['district_manager'] — and the DM sees pay",
      pv.can_see_pay("Bearer dm", "ORG1", T_DM_OPEN), True)
check("J5d: ...written as 'District Manager' in the config, same result (normalised both sides)",
      pv.can_see_pay("Bearer dm", "ORG1", T_DM_OPEN_TITLE), True)
check("J5e: ...and a tenant whose role is 'DM' opens it with ['dm']",
      pv.can_see_pay("Bearer dm-short", "ORG1", T_DM_SHORT), True)
check("J5f: permissioned + the employee_pay_rates grant opens it for that ONE DM login",
      (pv.can_see_pay("Bearer dm", "ORG1", T_PERM),
       pv.can_see_pay("Bearer dm-grant", "ORG1", T_PERM)), (False, True))
check("J5g: the same granted DM also passes under the DEFAULT mode",
      pv.can_see_pay("Bearer dm-grant", "ORG1", T_MU), True)
check("J5h: FLAGGED FOR THE OWNER, pinned as behaviour, not endorsed — a DM role configured with "
      "perms.scope='all' is company-wide by definition and DOES see pay; closing the leak for such "
      "a tenant is a ROLE-SCOPE fix in Roles & Access, not a code change here",
      pv.can_see_pay("Bearer dm-scope-all", "ORG1", T_MU), True)
check("J5i: an org that opened pay to the DM can close it again by clearing the list "
      "(config is reversible — nothing about the DM is baked into code)",
      pv.can_see_pay("Bearer dm", "ORG1",
                     FakeClient(tenants=[{"pay_visibility": "manager_up",
                                          "pay_visible_roles": []}])), False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. THE SURFACES BOUND FOR THE DM DIRECTIVE — real shipped route source vs. the real gate")
# Same technique as §I: the routers cannot be imported stdlib-only, so each route function's REAL
# source is AST-extracted from the shipped file and exec'd against this harness's fake core/DB. What
# runs below IS the code that serves the endpoint, not a re-implementation of it.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_CACHE = {}


def _module_src(*parts):
    path = os.path.join(_HERE, "app", "modules", *parts)
    if path not in _SRC_CACHE:
        with open(path, encoding="utf-8") as fh:
            _SRC_CACHE[path] = (fh.read(), path)
    return _SRC_CACHE[path]


def _fn_node(src, name):
    fns = [n for n in ast.walk(ast.parse(src))
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    return fns[0] if len(fns) == 1 else None


def _load(parts, name, ns):
    """exec the REAL shipped source of `name` from app/modules/<parts> into `ns`; return the fn."""
    src, path = _module_src(*parts)
    node = _fn_node(src, name)
    if node is None:
        return None
    node = ast.parse(ast.get_source_segment(src, node)).body[0]
    node.decorator_list = []                 # the @router.get registration is proven separately
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    return ns[name]


_HOLDER = [FAKE_DEFAULT]
_BASE_NS = {"Header": _fastapi.Header, "HTTPException": _HTTPException, "ORG_ID": "ORG1",
            "_payvis": pv, "sb": lambda: _HOLDER[0], "get_supabase": lambda: _HOLDER[0],
            "scope_keyset": lambda *a, **k: None, "in_keyset": lambda ks, code: True}


def _ns(**extra):
    d = dict(_BASE_NS)
    d.update(extra)
    return d


def _run(fn, client, **kw):
    _HOLDER[0] = client
    return fn(**kw)


# ── K1. GET /storeops/employees — the roster feed (a select("*"), i.e. pay_rate + pay_amount) ─────
_ROSTER = [{"employee_id": "E1", "name": "Ann", "home_store": "S1", "is_active": True,
            "pay_rate": 22.5, "pay_basis": "hourly", "pay_amount": None, "phone": "555"},
           {"employee_id": "E2", "name": "Bo", "home_store": "S2", "is_active": True,
            "pay_rate": 0.0, "pay_basis": "salary", "pay_amount": 5200.0, "phone": "556"}]


def _roster_client(tenants):
    return FakeClient(tenants=tenants, tables={"employees": _ROSTER})


_get_employees = _load(("storeops", "router.py"), "get_employees",
                       _ns(_caller_app_user=lambda *a, **k: {},
                           _role_permissions=lambda *a, **k: {},
                           _cscope=types.SimpleNamespace(roster_span_exempt=lambda p: False)))
check("K1-0: get_employees was located in the shipped router", _get_employees is not None, True)
_dm_rows = _run(_get_employees, _roster_client([{"pay_visibility": "manager_up"}]),
                authorization="Bearer dm", org_id="ORG1")
check("K1: DM — every pay key is DELETED from the roster (not zeroed)",
      sorted({k for r in _dm_rows for k in r if k in pv.PAY_FIELDS}), [])
check("K1b: DM — the roster itself is intact: both people, names, stores, phones, and pay_basis "
      "(hourly-vs-salary is not a dollar figure and the scheduling pages branch on it)",
      [(r["employee_id"], r["name"], r["home_store"], r["pay_basis"], r["phone"]) for r in _dm_rows],
      [("E1", "Ann", "S1", "hourly", "555"), ("E2", "Bo", "S2", "salary", "556")])
check("K1c: a SALARIED person's pay_amount is gone too — the salary IS the figure the owner named",
      any("pay_amount" in r for r in _dm_rows), False)
check("K1d: market manager (and above) is unaffected — pay_rate/pay_amount ride as before",
      [(r.get("pay_rate"), r.get("pay_amount")) for r in
       _run(_get_employees, _roster_client([{"pay_visibility": "manager_up"}]),
            authorization="Bearer mm", org_id="ORG1")],
      [(22.5, None), (0.0, 5200.0)])
check("K1e: the org OPENS it to the DM (pay_visible_roles) and the same call returns the pay",
      [(r.get("pay_rate"), r.get("pay_amount")) for r in
       _run(_get_employees, _roster_client([{"pay_visibility": "manager_up",
                                             "pay_visible_roles": ["district_manager"]}]),
            authorization="Bearer dm", org_id="ORG1")],
      [(22.5, None), (0.0, 5200.0)])
check("K1f: ...and under 'permissioned' the granted DM login gets it too",
      [r.get("pay_rate") for r in
       _run(_get_employees, _roster_client([{"pay_visibility": "permissioned"}]),
            authorization="Bearer dm-grant", org_id="ORG1")], [22.5, 0.0])
check("K1g: ARMED — a $0.00 rate stays a REAL 0.0 for an allowed caller, so K1's 'no pay keys' is "
      "genuinely deletion and not a zero being mistaken for absence",
      _run(_get_employees, _roster_client([{"pay_visibility": "all"}]),
           authorization="Bearer rep", org_id="ORG1")[1]["pay_rate"], 0.0)

# ── K2. GET /storeops/payroll-change-log — before/after VALUES of pay_rate / pay_amount edits ────
_LOG = [{"employee_id": "E1", "employee_name": "Ann", "field": "pay_rate", "before_value": "20.00",
         "after_value": "22.50", "entry_point": "pay_basis_change", "changed_by_email": "hr@x",
         "store_code": "S1"},
        {"employee_id": "E2", "employee_name": "Bo", "field": "pay_amount", "before_value": "5000",
         "after_value": "5200", "entry_point": "pay_basis_change", "changed_by_email": "hr@x",
         "store_code": "S2"},
        {"employee_id": "E1", "employee_name": "Ann", "field": "actual_hours", "before_value": "7",
         "after_value": "8", "entry_point": "shift_edit", "changed_by_email": "dm@x",
         "store_code": "S1"},
        {"employee_id": "E2", "employee_name": "Bo", "field": "pay_basis", "before_value": "hourly",
         "after_value": "salary", "entry_point": "pay_basis_change", "changed_by_email": "hr@x",
         "store_code": "S2"}]
_log_src, _ = _module_src("storeops", "router.py")
_money_log_fields = None
for _n in ast.walk(ast.parse(_log_src)):
    if isinstance(_n, ast.Assign) and any(getattr(t, "id", "") == "_PAY_MONEY_LOG_FIELDS"
                                          for t in _n.targets):
        _money_log_fields = ast.literal_eval(_n.value)
check("K2-0: the shipped router declares _PAY_MONEY_LOG_FIELDS as the money subset of the logged "
      "fields (pay_basis/termination_date are not dollars and stay readable)",
      _money_log_fields, ("pay_rate", "pay_amount"))
_change_log = _load(("storeops", "router.py"), "payroll_change_log",
                    _ns(_PAY_MONEY_LOG_FIELDS=_money_log_fields))


def _log_client(tenants):
    return FakeClient(tenants=tenants, tables={"payroll_change_log": _LOG})


_dm_log = _run(_change_log, _log_client([{"pay_visibility": "manager_up"}]),
               authorization="Bearer dm", org_id="ORG1")["items"]
check("K2: DM — the two PAY-VALUE rows lose before_value/after_value (deleted, not blanked)",
      [(r["field"], "before_value" in r, "after_value" in r) for r in _dm_log
       if r["field"] in ("pay_rate", "pay_amount")],
      [("pay_rate", False, False), ("pay_amount", False, False)])
check("K2b: DM — the rows THEMSELVES survive: who changed what, for whom, and when is still audited",
      [(r["field"], r["employee_name"], r["changed_by_email"]) for r in _dm_log
       if r["field"] in ("pay_rate", "pay_amount")],
      [("pay_rate", "Ann", "hr@x"), ("pay_amount", "Bo", "hr@x")])
check("K2c: DM — an HOURS correction is untouched (the DM's own day-to-day audit trail)",
      [(r["before_value"], r["after_value"]) for r in _dm_log if r["field"] == "actual_hours"],
      [("7", "8")])
check("K2d: DM — a pay_BASIS change keeps its values ('hourly'->'salary' is not a dollar figure)",
      [(r["before_value"], r["after_value"]) for r in _dm_log if r["field"] == "pay_basis"],
      [("hourly", "salary")])
check("K2e: market manager sees the dollars",
      [(r["before_value"], r["after_value"]) for r in
       _run(_change_log, _log_client([{"pay_visibility": "manager_up"}]),
            authorization="Bearer mm", org_id="ORG1")["items"] if r["field"] == "pay_rate"],
      [("20.00", "22.50")])
check("K2f: the org opens it to the DM and the dollars come back",
      [(r["before_value"], r["after_value"]) for r in
       _run(_change_log, _log_client([{"pay_visibility": "manager_up",
                                       "pay_visible_roles": ["district_manager"]}]),
            authorization="Bearer dm", org_id="ORG1")["items"] if r["field"] == "pay_rate"],
      [("20.00", "22.50")])

# ── K3. GET /storeops/pto-accrual/{period} — per-employee `rate` IS employees.pay_rate ───────────
def _pto_result():
    return {"employees": {"E1": {"employee_id": "E1", "name": "Ann", "store": "S1",
                                 "accrued_hours": 3.2, "taken_hours": 8.0, "rate": 22.5,
                                 "payable_balance": 11.0, "cost": 72.0, "capped": False,
                                 "by_store": {"S1": {"accrued_hours": 3.2, "taken_hours": 8.0,
                                                     "cost": 72.0}}}},
            "stores": {"S1": {"store": "S1", "accrued_hours": 3.2, "taken_hours": 8.0,
                              "cost": 72.0}}}


_pto = _load(("storeops", "router.py"), "get_pto_accrual",
             _ns(_pto_gather=lambda org_id, period: (
                 _pto_result(), {"org_effective": {"mode": "accrue", "accrual_rate": 0.0385}})))
_dm_pto = _run(_pto, FakeClient(tenants=[{"pay_visibility": "manager_up"}]),
               period="2026-09", authorization="Bearer dm", org_id="ORG1")
check("K3: DM — the per-employee `rate` (= employees.pay_rate) and `cost` are DELETED",
      [("rate" in _dm_pto["employees"][0], "cost" in _dm_pto["employees"][0])], [(False, False)])
check("K3b: DM — the per-employee by_store `cost` is stripped too (a nested dollar is still a dollar)",
      "cost" in _dm_pto["employees"][0]["by_store"]["S1"], False)
check("K3c: DM — every HOURS figure survives (accrued/taken, per person and per store) — this is "
      "the PTO liability a DM runs their stores on",
      (_dm_pto["employees"][0]["accrued_hours"], _dm_pto["employees"][0]["taken_hours"],
       _dm_pto["employees"][0]["by_store"]["S1"]["accrued_hours"],
       _dm_pto["employees"][0]["payable_balance"]), (3.2, 8.0, 3.2, 11.0))
check("K3d: DM — THE STORE-LEVEL AGGREGATE IS LEFT ALONE: stores[].cost still reports the store's "
      "PTO expense (a store cost is not a salary — owner's line is PER-EMPLOYEE pay)",
      _dm_pto["stores"], [{"store": "S1", "accrued_hours": 3.2, "taken_hours": 8.0, "cost": 72.0}])
check("K3e: DM — the org-level accrual RATE (hrs accrued per hour worked, a policy knob, not money) "
      "is untouched", (_dm_pto["rate"], _dm_pto["mode"]), (0.0385, "accrue"))
check("K3f: market manager sees the per-employee rate + cost",
      [(e.get("rate"), e.get("cost")) for e in
       _run(_pto, FakeClient(tenants=[{"pay_visibility": "manager_up"}]), period="2026-09",
            authorization="Bearer mm", org_id="ORG1")["employees"]], [(22.5, 72.0)])
check("K3g: the org opens it to the DM and the per-employee figures return",
      [(e.get("rate"), e.get("cost"), e["by_store"]["S1"]["cost"]) for e in
       _run(_pto, FakeClient(tenants=[{"pay_visibility": "manager_up",
                                       "pay_visible_roles": ["dm", "district_manager"]}]),
            period="2026-09", authorization="Bearer dm", org_id="ORG1")["employees"]],
      [(22.5, 72.0, 72.0)])

# ── K4. GET /storeops/salary-advance/additional-payroll/{period} ─────────────────────────────────
def _addl():
    return {"employees": [{"employee_id": "E1", "name": "Ann", "store": "S1",
                           "earned_to_date": 1800.0, "cash_paid_to_date": 2000.0, "excess": 200.0,
                           "lookback_start": "2026-08-01"}],
            "cells": [{"store": "S1", "amount": 200.0}], "available": True}


_addl_route = _load(("storeops", "router.py"), "get_additional_payroll",
                    _ns(_additional_payroll_gather=lambda org_id, period: _addl()))
_dm_addl = _run(_addl_route, FakeClient(tenants=[{"pay_visibility": "manager_up"}]),
                period="2026-09", authorization="Bearer dm", org_id="ORG1")
check("K4: DM — earned_to_date / cash_paid_to_date / excess are DELETED per employee",
      sorted(k for k in ("earned_to_date", "cash_paid_to_date", "excess")
             if k in _dm_addl["employees"][0]), [])
check("K4b: DM — the person, their store and the lookback window still render (the row is not a lie, "
      "it is a withheld figure)",
      (_dm_addl["employees"][0]["name"], _dm_addl["employees"][0]["store"],
       _dm_addl["employees"][0]["lookback_start"]), ("Ann", "S1", "2026-08-01"))
check("K4c: DM — THE STORE AGGREGATE IS LEFT: the per-store cells and the period total are the P&L "
      "expense a DM legitimately needs",
      (_dm_addl["cells"], _dm_addl["total"]), ([{"store": "S1", "amount": 200.0}], 200.0))
check("K4d: market manager sees the per-employee figures",
      [(e.get("earned_to_date"), e.get("cash_paid_to_date"), e.get("excess")) for e in
       _run(_addl_route, FakeClient(tenants=[{"pay_visibility": "manager_up"}]), period="2026-09",
            authorization="Bearer mm", org_id="ORG1")["employees"]], [(1800.0, 2000.0, 200.0)])
check("K4e: the org opens it to the DM and they return",
      [e.get("excess") for e in
       _run(_addl_route, FakeClient(tenants=[{"pay_visibility": "manager_up",
                                              "pay_visible_roles": ["district_manager"]}]),
            period="2026-09", authorization="Bearer dm", org_id="ORG1")["employees"]], [200.0])

# ── K5. GET /storeops/salary-advance/history — "$X of salary handed to this named person" ────────
_ADV = [{"id": "a1", "employee_id": "E1", "amount": 500.0, "paid_date": "2026-09-02",
         "store_code": "S1", "method": "envelope_cash", "withdrawal_ref": "W7",
         "recorded_by": "mgr@x"}]
_adv_hist = _load(("storeops", "router.py"), "salary_advance_history", _ns())


def _adv_client(tenants):
    return FakeClient(tenants=tenants,
                      tables={"salary_advance_ledger": _ADV,
                              "employees": [{"employee_id": "E1", "name": "Ann"}]})


_dm_adv = _run(_adv_hist, _adv_client([{"pay_visibility": "manager_up"}]),
               authorization="Bearer dm", org_id="ORG1")["items"]
check("K5: DM — the advance AMOUNT is deleted", "amount" in _dm_adv[0], False)
check("K5b: DM — the ledger's existence, person, date, store, method and reference survive, so the "
      "cash-handling audit still works without the salary figure",
      (_dm_adv[0]["employee_name"], _dm_adv[0]["paid_date"], _dm_adv[0]["store_code"],
       _dm_adv[0]["method"], _dm_adv[0]["withdrawal_ref"]),
      ("Ann", "2026-09-02", "S1", "envelope_cash", "W7"))
check("K5c: market manager sees the amount",
      _run(_adv_hist, _adv_client([{"pay_visibility": "manager_up"}]),
           authorization="Bearer mm", org_id="ORG1")["items"][0]["amount"], 500.0)
check("K5d: the org opens it to the DM",
      _run(_adv_hist, _adv_client([{"pay_visibility": "manager_up",
                                    "pay_visible_roles": ["district_manager"]}]),
           authorization="Bearer dm", org_id="ORG1")["items"][0]["amount"], 500.0)


# ── K6. PATCH /storeops/employees/{id} — PostgREST echoes the WHOLE row back ─────────────────────
_EMP_ROW = {"id": "u1", "employee_id": "E1", "name": "Ann", "home_store": "S1", "phone": "999",
            "pay_rate": 22.5, "pay_basis": "hourly", "pay_amount": None, "termination_date": None}
_upd_src, _ = _module_src("storeops", "router.py")
_EMP_FIELDS = None
_GATED = None
for _n in ast.walk(ast.parse(_upd_src)):
    if isinstance(_n, ast.Assign):
        _t = [getattr(t, "id", "") for t in _n.targets]
        if "EMP_FIELDS" in _t:
            _EMP_FIELDS = ast.literal_eval(_n.value)
        if "_PAY_GATED_FIELDS" in _t:
            _GATED = ast.literal_eval(_n.value)
_upd = _load(("storeops", "router.py"), "update_employee",
             _ns(EMP_FIELDS=_EMP_FIELDS, _PAY_GATED_FIELDS=_GATED,
                 _PAY_LOGGED_FIELDS=("pay_rate", "pay_basis", "pay_amount", "termination_date"),
                 _require_manager=lambda *a, **k: {"role": "district_manager"},
                 _log_payroll_change=lambda *a, **k: None,
                 _who_for_log=lambda *a, **k: {},
                 _ensure_employee_id=lambda row: row,
                 payroll_salary=types.SimpleNamespace(PAY_BASES=("hourly", "salary"))))
check("K6-0: update_employee located, and EMP_FIELDS/_PAY_GATED_FIELDS read from the shipped module",
      (_upd is not None, "pay_rate" in (_GATED or ())), (True, True))


class _WritingTable(_Table):
    """A table that actually APPLIES `.update(row)` to its stored rows, so a write test can tell the
    difference between 'the field was dropped from the update' and 'the fake silently ignored it'."""

    def update(self, patch, *a, **k):
        self._patch = dict(patch or {})
        return self

    def execute(self):
        rows = self._rows
        if getattr(self, "_patch", None):
            for r in rows:
                r.update(self._patch)
            self._patch = None
        import copy
        return _Resp(copy.deepcopy(rows))


class _WritingClient(FakeClient):
    def table(self, name):
        if name == "employees":
            return _WritingTable(self.tables["employees"], False)
        return FakeClient.table(self, name)


def _emp_client(tenants):
    return _WritingClient(tenants=tenants, tables={"employees": [dict(_EMP_ROW)]})


_dm_patch = _run(_upd, _emp_client([{"pay_visibility": "manager_up"}]), emp_id="u1",
                 updates={"phone": "111"}, authorization="Bearer dm", org_id="ORG1")
check("K6: DM — an ORDINARY edit (a phone number) no longer hands back that person's pay_rate / "
      "pay_amount in the echoed row",
      sorted(k for k in ("pay_rate", "pay_amount") if k in _dm_patch), [])
check("K6b: DM — the rest of the echo is intact, so the page still updates in place",
      (_dm_patch["employee_id"], _dm_patch["name"], _dm_patch["pay_basis"]), ("E1", "Ann", "hourly"))
# The WRITE half: you may not write a figure you may not see.
_w_client = _emp_client([{"pay_visibility": "manager_up"}])
_w_out = _run(_upd, _w_client, emp_id="u1", updates={"phone": "111", "pay_rate": None},
              authorization="Bearer dm", org_id="ORG1")
check("K6f: THE DESTRUCTION PATH, CLOSED — the roles/HR editors post the WHOLE row back, so a caller "
      "shown NO pay_rate would send `pay_rate: null` on an ordinary phone save and WIPE that "
      "person's rate. The pay field is DROPPED from the write, and the stored row keeps its rate",
      _w_client.tables["employees"][0]["pay_rate"], 22.5)
check("K6g: ...the non-pay part of that same save still LANDS (refusing the whole call would break "
      "editing a name — this is a drop, not a 403)",
      _w_client.tables["employees"][0]["phone"], "111")
check("K6h: ...and the response NAMES what it ignored — silently discarding someone's typed work is "
      "the failure this house has already been burned by",
      _w_out.get("pay_fields_ignored"), ["pay_rate"])
_w_only = None
try:
    _run(_upd, _emp_client([{"pay_visibility": "manager_up"}]), emp_id="u1",
         updates={"pay_rate": 99.0}, authorization="Bearer dm", org_id="ORG1")
except _HTTPException as _e:
    _w_only = _e.status_code
check("K6i: a PAY-ONLY update from such a caller is refused outright (403) rather than silently "
      "doing nothing at all", _w_only, 403)
_w_ok = _emp_client([{"pay_visibility": "manager_up", "pay_visible_roles": ["district_manager"]}])
_run(_upd, _w_ok, emp_id="u1", updates={"pay_rate": 99.0}, authorization="Bearer dm", org_id="ORG1")
check("K6j: the org opens pay to the DM and the SAME write lands — config, never code",
      _w_ok.tables["employees"][0]["pay_rate"], 99.0)
_w_mm = _emp_client([{"pay_visibility": "manager_up"}])
_run(_upd, _w_mm, emp_id="u1", updates={"pay_rate": 30.0}, authorization="Bearer mm", org_id="ORG1")
check("K6k: a market manager's pay write is unaffected", _w_mm.tables["employees"][0]["pay_rate"], 30.0)
check("K6c: market manager still gets the full row back",
      _run(_upd, _emp_client([{"pay_visibility": "manager_up"}]), emp_id="u1",
           updates={"phone": "111"}, authorization="Bearer mm", org_id="ORG1")["pay_rate"], 22.5)
class _AliasClient(FakeClient):
    """A data layer that hands back THE STORED ROW OBJECT ITSELF (no copy) — the sharpest shape for
    the aliasing question. The other fakes copy on execute(), which would hide the bug."""

    def table(self, name):
        if name == "employees":
            t = _Table(self.tables["employees"], False)
            t.execute = lambda: _Resp(self.tables["employees"])      # the live objects, uncopied
            return t
        return FakeClient.table(self, name)


_alias_client = _AliasClient(tenants=[{"pay_visibility": "manager_up"}],
                             tables={"employees": [dict(_EMP_ROW)]})
_before = dict(_alias_client.tables["employees"][0])
_echo = _run(_upd, _alias_client, emp_id="u1", updates={"phone": "111"},
             authorization="Bearer dm", org_id="ORG1")
check("K6e: the strip acts on a COPY of the echo, never on the row object the data layer handed "
      "back — deleting keys out of THAT would reach anything else holding it (an in-process caller, "
      "a cache, a test double's store). What a caller may SEE is a property of the response, not of "
      "the record. [regression: the first cut of this fix stripped in place and broke "
      "harness_payroll_salary_router_integration]",
      (_alias_client.tables["employees"][0], "pay_rate" in _echo), (_before, False))
check("K6d: the org opens it to the DM",
      _run(_upd, _emp_client([{"pay_visibility": "manager_up",
                               "pay_visible_roles": ["district_manager"]}]), emp_id="u1",
           updates={"phone": "111"}, authorization="Bearer dm", org_id="ORG1")["pay_rate"], 22.5)

# ── K7. GET /core/employees (and its /hr/employees delegate) — the Roles & Access grid ───────────
_core_src, _core_path = _module_src("core", "router.py")
_pay_visible = _load(("core", "router.py"), "_pay_visible", _ns())
_list_emps = _load(("core", "router.py"), "list_employees",
                   _ns(_pay_visible=_pay_visible, _ensure_employee=lambda *a, **k: False))
check("K7-0: core.list_employees now takes an `authorization` header (it had NO caller gate at all)",
      "authorization" in [a.arg for a in
                          _fn_node(_core_src, "list_employees").args.args], True)


def _grid_client(tenants):
    return FakeClient(tenants=tenants, tables={
        "employees": [{"id": 1, "employee_id": "E1", "name": "Ann", "home_store": "S1",
                       "role": "Sales", "pay_rate": 22.5, "email": "ann@x", "phone": "555",
                       "is_active": True}],
        "app_users": [], "account_link_invite": []})


_dm_grid = _run(_list_emps, _grid_client([{"pay_visibility": "manager_up"}]),
                org_id="ORG1", authorization="Bearer dm")["employees"]
check("K7: DM — the grid's 'Pay $/hr' column is EMPTY at the source (key deleted server-side), so "
      "its Excel/PDF export cannot carry it either (RULE FOUR)",
      "pay_rate" in _dm_grid[0], False)
check("K7b: DM — the rest of the assignment grid is unchanged (name/email/store/role/login state)",
      (_dm_grid[0]["name"], _dm_grid[0]["email"], _dm_grid[0]["home_store"], _dm_grid[0]["role"]),
      ("Ann", "ann@x", "S1", "Sales"))
check("K7c: market manager sees the column",
      _run(_list_emps, _grid_client([{"pay_visibility": "manager_up"}]),
           org_id="ORG1", authorization="Bearer mm")["employees"][0]["pay_rate"], 22.5)
check("K7d: the org opens it to the DM",
      _run(_list_emps, _grid_client([{"pay_visibility": "manager_up",
                                      "pay_visible_roles": ["District Manager"]}]),
           org_id="ORG1", authorization="Bearer dm")["employees"][0]["pay_rate"], 22.5)
check("K7e: NO token at all + login enforcement ON -> fail closed (an unauthenticated scrape of the "
      "roster gets no pay)",
      "pay_rate" in _run(_list_emps,
                         FakeClient(tenants=[{"pay_visibility": "manager_up"}],
                                    app_config=[{"rbac_enabled": True}],
                                    tables=_grid_client([]).tables),
                         org_id="ORG1", authorization="")["employees"][0], False)
_HOLDER[0] = FakeClient(tenants=[{"pay_visibility": "manager_up"}])
check("K7f: _pay_visible is a thin router to the ONE gate and inherits its fail-closed posture — an "
      "unverifiable token gets no pay, a broken storeops import likewise (it returns False, never "
      "'assume allowed')",
      (_pay_visible("Bearer broken", "ORG1"), _pay_visible("Bearer ghost", "ORG1"),
       _pay_visible("Bearer mm", "ORG1")), (False, False, True))
_hr_src, _ = _module_src("hr", "router.py")
check("K7g: /hr/employees threads the caller's header into the delegate (without it the delegate "
      "would see no token and blank pay for an ADMIN too)",
      ("authorization" in [a.arg for a in _fn_node(_hr_src, "hr_list_employees").args.args]
       and "authorization=authorization" in
       ast.get_source_segment(_hr_src, _fn_node(_hr_src, "hr_list_employees"))), True)

# ── K8. GET /core/employee-dashboard — a manager may open ANYONE in their span ───────────────────
# The bundle is too entangled to exec whole (commcalc/payables/targets reads); the PAY GUARD itself
# is exec'd from the shipped source, statement for statement, against the real payload shape.
_dash_node = _fn_node(_core_src, "employee_dashboard")
_dash_src = ast.get_source_segment(_core_src, _dash_node)
_guard_stmts = [st for st in ast.parse(_dash_src).body[0].body
                if "own_eid" in (ast.get_source_segment(_dash_src, st) or "")
                or "_pay_visible" in (ast.get_source_segment(_dash_src, st) or "")]
check("K8-0: the shipped dashboard carries a pay guard (own_eid resolution + the gated strip)",
      len(_guard_stmts) >= 2, True)
_fake_so_router = types.ModuleType("app.modules.storeops.router")
_fake_so_router._caller_app_user = lambda auth, org_id: {
    "Bearer dm": {"employee_id": "E9", "role": "district_manager"},
    "Bearer rep": {"employee_id": "E1", "role": "sales_rep"},
    "Bearer mm": {"employee_id": "E8", "role": "market_manager"}}.get(auth, {})
sys.modules["app.modules.storeops.router"] = _fake_so_router


def _dash_payload():
    return {"employee": {"employee_id": "E1", "name": "Ann", "store": "S1", "role": "sales_rep",
                         "pay_rate": 22.5, "rep_name": "ann"},
            "hours": {"scheduled_hours": 40.0, "actual_hours": 38.5, "pay_rate": 22.5,
                      "scheduled_pay": 900.0, "actual_pay": 866.25, "shifts": 5},
            "commission": {"total_payout": 300.0}}


def _run_dash_guard(auth, target, client):
    _HOLDER[0] = client
    ns = dict(_BASE_NS)
    ns.update({"_pay_visible": _pay_visible, "out": _dash_payload(), "authorization": auth,
               "employee_id": target, "org_id": "ORG1"})
    exec(compile(ast.Module(body=_guard_stmts, type_ignores=[]), _core_path, "exec"), ns)
    return ns["out"]


_dash_other = _run_dash_guard("Bearer dm", "E1", FakeClient(tenants=[{"pay_visibility": "manager_up"}]))
check("K8: DM opening SOMEONE ELSE's dashboard — pay_rate and scheduled/actual pay are DELETED",
      (sorted(k for k in ("pay_rate",) if k in _dash_other["employee"]),
       sorted(k for k in ("pay_rate", "scheduled_pay", "actual_pay") if k in _dash_other["hours"])),
      ([], []))
check("K8b: DM — the rest of that person's bundle is untouched: hours, shifts and commission still "
      "render (a DM manages attendance and performance; that is not a salary)",
      (_dash_other["hours"]["scheduled_hours"], _dash_other["hours"]["actual_hours"],
       _dash_other["hours"]["shifts"], _dash_other["employee"]["name"],
       _dash_other["commission"]["total_payout"]), (40.0, 38.5, 5, "Ann", 300.0))
_dash_self = _run_dash_guard("Bearer rep", "E1", FakeClient(tenants=[{"pay_visibility": "manager_up"}]))
check("K8c: SELF-PAY IS DELIBERATELY LEFT ALONE (flagged for the owner): a person opening their OWN "
      "dashboard still sees their own rate and pay, even though their role is below the gate",
      (_dash_self["employee"]["pay_rate"], _dash_self["hours"]["pay_rate"],
       _dash_self["hours"]["actual_pay"]), (22.5, 22.5, 866.25))
check("K8d: market manager opening someone else's bundle keeps the pay",
      _run_dash_guard("Bearer mm", "E1",
                      FakeClient(tenants=[{"pay_visibility": "manager_up"}]))["hours"]["pay_rate"],
      22.5)
check("K8e: the org opens it to the DM",
      _run_dash_guard("Bearer dm", "E1",
                      FakeClient(tenants=[{"pay_visibility": "manager_up",
                                           "pay_visible_roles": ["district_manager"]}]
                                 ))["hours"]["actual_pay"], 866.25)

# ── K9. GET /marketing/event-sales/roi — the event payroll names each person AND their rate ──────
import app.modules.marketing.event_sales as _ES                      # stdlib-only, imports clean
_mk_src, _mk_path = _module_src("marketing", "router.py")
_roi_node = _fn_node(_mk_src, "event_sales_roi")
_roi_src = ast.get_source_segment(_mk_src, _roi_node)
_roi_guard = [st for st in ast.walk(ast.parse(_roi_src))
              if isinstance(st, ast.If) and "_pay_allowed" in (ast.get_source_segment(_roi_src, st.test) or "")]
check("K9-0: the shipped ROI route carries exactly one pay guard over the event payroll",
      len(_roi_guard), 1)
check("K9-1: the route takes an `authorization` header (it took none before — nothing to gate on)",
      "authorization" in [a.arg for a in _roi_node.args.args], True)


def _real_event_payroll():
    """The REAL payload shape, from the REAL builder — not a hand-written stand-in."""
    return _ES.payroll_from_hours(
        [{"employee_id": "E1", "employee_name": "Ann", "day": "2026-09-01", "hours": 8,
          "state": "measured"},
         {"employee_id": "E2", "employee_name": "Bo", "day": "2026-09-01", "hours": 6,
          "state": "scheduled"}],                      # E2 is salaried -> UNPRICED, rate None
        {"E1": 22.5})


def _run_roi_guard(allowed):
    payroll = _real_event_payroll()
    ns = dict(_BASE_NS)
    ns.update({"payroll": payroll, "_pay_allowed": lambda: allowed})
    exec(compile(ast.Module(body=_roi_guard, type_ignores=[]), _mk_path, "exec"), ns)
    return payroll


_roi_denied = _run_roi_guard(False)
check("K9: denied — every per-person pay_rate / amount is DELETED from rows and unpriced alike",
      sorted({k for r in _roi_denied["rows"] + _roi_denied["unpriced"]
              for k in ("pay_rate", "amount") if k in r}), [])
check("K9b: denied — who worked, how long, and the provenance of those hours all survive",
      [(r["employee_name"], r["hours"], r["hours_state"]) for r in _roi_denied["rows"]],
      [("Ann", 8.0, "measured"), ("Bo", 6.0, "scheduled")])
check("K9c: denied — THE EVENT-LEVEL LABOUR COST IS LEFT ALONE: `total` and the hours mix are what "
      "the ROI report exists to measure, and neither names one person's pay",
      (_roi_denied["total"], _roi_denied["hours_measured"], _roi_denied["hours_scheduled"]),
      (180.0, 8.0, 6.0))
check("K9d: denied — the UNPRICED person keeps their reason text, so 'no rate on file' still reads "
      "as a data gap to fix rather than as a withheld figure",
      "reason" in _roi_denied["unpriced"][0], True)
_roi_ok = _run_roi_guard(True)
check("K9e: allowed — the per-person figures ride exactly as before",
      [(r["employee_name"], r.get("pay_rate"), r.get("amount")) for r in _roi_ok["rows"]],
      [("Ann", 22.5, 180.0), ("Bo", None, None)])

# ── K10. POST /hr/employees — the dedupe path reads an EXISTING row with select("*") ─────────────
_hr_create = _fn_node(_hr_src, "hr_create_employee")
_hr_create_src = ast.get_source_segment(_hr_src, _hr_create)
check("K10: the shipped hr_create_employee gates its echoed `emp` on can_see_pay before returning",
      ("_payvis.can_see_pay(" in _hr_create_src and "_payvis.strip_pay(emp)" in _hr_create_src), True)
check("K10b: PATCH /hr/employees/{id} needs no gate of its own — it RETURNS storeops.update_employee's "
      "already-gated result (K6), which is the one implementation, not a second one",
      "update_employee(emp_id, body, authorization=authorization" in
      ast.get_source_segment(_hr_src, _fn_node(_hr_src, "hr_update_employee")), True)

# ── K11. ONE GATE, NOT SEVEN — no surface bound today grew its own role list ─────────────────────
_BOUND = [(("storeops", "router.py"), "get_employees"), (("storeops", "router.py"), "payroll_change_log"),
          (("storeops", "router.py"), "update_employee"), (("storeops", "router.py"), "get_pto_accrual"),
          (("storeops", "router.py"), "get_additional_payroll"),
          (("storeops", "router.py"), "salary_advance_history"),
          (("core", "router.py"), "list_employees"), (("core", "router.py"), "employee_dashboard"),
          (("marketing", "router.py"), "event_sales_roi"), (("hr", "router.py"), "hr_create_employee")]
for _parts, _name in _BOUND:
    _s, _ = _module_src(*_parts)
    _body = ast.get_source_segment(_s, _fn_node(_s, _name)) or ""
    check(f"K11: {_parts[0]}.{_name} routes through the ONE module (can_see_pay/_pay_visible + "
          f"strip_pay) — no second gate",
          ("can_see_pay" in _body or "_pay_visible" in _body) and "strip_pay" in _body, True)
    check(f"K11b: {_parts[0]}.{_name} hardcodes NO role name — which roles see pay is config "
          "(RULE TWO)",
          any(_r in _body for _r in ("district_manager", "'dm'", '"dm"', "market_manager")), False)


# ── K12. THE REGRESSION — the reported defect, reproduced, then shown fixed ──────────────────────
# Owner report 2026-09-10: a district manager could see employee salaries. Take the SHIPPED source of
# a bound route, delete just the gate statements from its AST, and run the DM through it: the pay is
# there. That is the code as it stood before this change — and it is what every K check above would
# have caught. Same route, gate intact, one line later: gone.
def _ungated(parts, name, ns):
    src, path = _module_src(*parts)
    node = ast.parse(ast.get_source_segment(src, _fn_node(src, name))).body[0]
    node.decorator_list = []

    def _is_gate(st):
        seg = ast.unparse(st)
        return "can_see_pay" in seg or "strip_pay" in seg
    node.body = [st for st in node.body if not _is_gate(st)]
    ns = dict(ns)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    return ns[name]


_leaky_roster = _ungated(("storeops", "router.py"), "get_employees",
                         _ns(_caller_app_user=lambda *a, **k: {},
                             _role_permissions=lambda *a, **k: {},
                             _cscope=types.SimpleNamespace(roster_span_exempt=lambda p: False)))
_leak_rows = _run(_leaky_roster, _roster_client([{"pay_visibility": "manager_up"}]),
                  authorization="Bearer dm", org_id="ORG1")
check("K12: REPRODUCED — with the gate statements removed, the DM's roster ships every pay_rate and "
      "pay_amount (the reported defect)",
      [(r.get("pay_rate"), r.get("pay_amount")) for r in _leak_rows], [(22.5, None), (0.0, 5200.0)])
check("K12b: FIXED — the SAME call against the SHIPPED source carries neither",
      [(r.get("pay_rate"), r.get("pay_amount")) for r in
       _run(_get_employees, _roster_client([{"pay_visibility": "manager_up"}]),
            authorization="Bearer dm", org_id="ORG1")], [(None, None), (None, None)])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*100}")
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
for f in FAIL:
    print(f"  ✗ {f}")
if FAIL:
    sys.exit(1)
print("ALL CHECKS PASSED")
