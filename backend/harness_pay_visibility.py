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

    def execute(self):
        if self._fail:
            raise RuntimeError("column does not exist (pre-434)")
        return _Resp(self._rows)


class FakeClient:
    def __init__(self, tenants=None, app_config=None, fail_tables=()):
        self.tenants, self.app_config, self.fail = tenants or [], app_config or [], set(fail_tables)

    def schema(self, name):
        return self

    def table(self, name):
        rows = {"tenants": self.tenants, "app_config": self.app_config}.get(name, [])
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
print(f"\n{'='*100}")
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
for f in FAIL:
    print(f"  ✗ {f}")
if FAIL:
    sys.exit(1)
print("ALL CHECKS PASSED")
