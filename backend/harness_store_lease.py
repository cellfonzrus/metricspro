"""HARNESS — store lease / landlord / insurance capture (store_lease.py, migration 946).

OWNER SPEC (2026-09-03): store setup captures landlord + site contact, rent payment links / ACH,
current rent, annual escalation as a PERCENTAGE or an EXPLICIT monthly-rent schedule, rent due
(house default FIRST WEEK of the month — defined, never hardcoded — per-store override), insurance
+ premium due, and uploadable lease/COI documents. ACH + documents are money-sensitive: management
gate (mig-434 posture), fail-closed.

  A. rent_for_month — schedule wins over %, % compounds per whole anniversary-year from
     rent_effective_from, boundary days exact, nothing-known -> None (never fake 0), malformed
     schedule entries dropped.
  B. normalize/resolve rent_due + rent_due_window — store > tenant > HOUSE first-week; garbage
     at any layer falls through; week/day windows clamp to the real month end.
  C. resolve_lease_access truth table — default allow-list IS "market manager and above";
     scope-'all' + the store_lease_docs grant always pass; unknown role fails closed.
  D. can_see_lease wrapper end-to-end (fake core + fake DB) — tenant allow-list override,
     open-app parity carve-out, broken verifier / ghost login / pre-946 schema all fail closed.
  E. strip_sensitive (deletes, never zeroes, idempotent) + decode_doc_data_url (type/size caps,
     user-showable ValueErrors).
  F. ARMED negative control.

Run: python3 harness_store_lease.py     (stdlib-only — db/core are stubbed)
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
        FAIL.append(f"{label}: got {got!r}, want {want!r}")


# ── stubs at the LAZY-import seams (same pattern as harness_pay_visibility) ───────────────────────
_db = types.ModuleType("app.core.database")
_db.get_supabase = lambda: (_ for _ in ()).throw(RuntimeError("no live DB in this harness"))
sys.modules["app.core.database"] = _db

CALLERS = {
    "u-admin": {"org_id": "ORG1", "role": "admin", "super_admin": False, "perms": {"scope": "all"}},
    "u-super": {"org_id": "ORG1", "role": "rep", "super_admin": True, "perms": {"scope": "self"}},
    "u-mm": {"org_id": "ORG1", "role": "market_manager", "super_admin": False, "perms": {"scope": "market"}},
    "u-dm": {"org_id": "ORG1", "role": "district_manager", "super_admin": False, "perms": {"scope": "market"}},
    "u-sm": {"org_id": "ORG1", "role": "store_manager", "super_admin": False, "perms": {"scope": "store"}},
    "u-rep": {"org_id": "ORG1", "role": "sales_rep", "super_admin": False, "perms": {"scope": "self"}},
    "u-sm-grant": {"org_id": "ORG1", "role": "store_manager", "super_admin": False,
                   "perms": {"scope": "store", "data": {"store_lease_docs": True}}},
    "u-ops": {"org_id": "ORG1", "role": "Operations Lead", "super_admin": False, "perms": {"scope": "market"}},
    "u-ghost": None,   # verified token whose membership row is gone
}
_core = types.ModuleType("app.modules.core.router")
_core._uid_from_token = lambda auth: (_ for _ in ()).throw(RuntimeError("verifier down")) \
    if auth == "Bearer broken" else {
        "Bearer admin": "u-admin", "Bearer super": "u-super", "Bearer mm": "u-mm",
        "Bearer dm": "u-dm", "Bearer sm": "u-sm", "Bearer rep": "u-rep",
        "Bearer sm-grant": "u-sm-grant", "Bearer ops": "u-ops", "Bearer ghost": "u-ghost"}.get(auth)
_core._resolve_caller = lambda client, uid, active_org=None: CALLERS.get(uid)
sys.modules["app.modules.core.router"] = _core


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
            raise RuntimeError("column does not exist (pre-946)")
        return _Resp(self._rows)


class FakeClient:
    def __init__(self, tenants=None, app_config=None, fail_tables=()):
        self.tenants, self.app_config, self.fail = tenants or [], app_config or [], set(fail_tables)

    def schema(self, name):
        return self

    def table(self, name):
        rows = {"tenants": self.tenants, "app_config": self.app_config}.get(name, [])
        return _Table(rows, name in self.fail)


import app.modules.storeops.store_lease as sl                        # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A. rent_for_month
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 nothing known -> None (never fake 0)", sl.rent_for_month(2026, 9), None)
check("A2 current rent only", sl.rent_for_month(2026, 9, current_rent=4500), 4500.0)
check("A3 current rent as string coerces", sl.rent_for_month(2026, 9, current_rent="4500.50"), 4500.5)
check("A4 pct without effective date = no escalation (rent is simply current)",
      sl.rent_for_month(2030, 1, current_rent=4500, escalation_pct=3), 4500.0)
# 3% from 2025-06-01: first anniversary 2026-06-01
check("A5 pct: before first anniversary -> 0 steps",
      sl.rent_for_month(2026, 5, 4500, "2025-06-01", 3), 4500.0)
check("A6 pct: month OF the first anniversary (June 1 <= June 1) -> 1 step",
      sl.rent_for_month(2026, 6, 4500, "2025-06-01", 3), 4635.0)
check("A7 pct: three whole years compound",
      sl.rent_for_month(2028, 7, 4500, "2025-06-01", 3), round(4500 * 1.03 ** 3, 2))
check("A8 pct: effective date in the future -> 0 steps (clamped, never negative)",
      sl.rent_for_month(2026, 1, 4500, "2027-06-01", 3), 4500.0)
check("A9 pct: mid-month effective date — anniversary after the 1st counts from next month",
      sl.rent_for_month(2026, 6, 4500, "2025-06-15", 3), 4500.0)
check("A9b ... and the following month has stepped",
      sl.rent_for_month(2026, 7, 4500, "2025-06-15", 3), 4635.0)
SCHED = [{"effective_from": "2027-01-01", "monthly_rent": 5000},
         {"effective_from": "2026-01-01", "monthly_rent": 4800}]
check("A10 schedule: latest effective_from <= month wins (order-insensitive)",
      sl.rent_for_month(2026, 9, 4500, "2020-01-01", 3, SCHED), 4800.0)
check("A11 schedule: later entry takes over", sl.rent_for_month(2027, 2, None, None, None, SCHED), 5000.0)
check("A12 schedule: month BEFORE first entry falls back to the pct path",
      sl.rent_for_month(2025, 6, 4000, "2024-06-01", 5, SCHED), 4200.0)
check("A13 malformed schedule entries dropped (falls through to current)",
      sl.rent_for_month(2026, 9, 4500, None, None,
                        [{"effective_from": "garbage", "monthly_rent": 1},
                         {"monthly_rent": 2}, {"effective_from": "2026-01-01"}, "x"]), 4500.0)
check("A14 schedule wins even at 0 explicit rent (an entered number, not a fake)",
      sl.rent_for_month(2026, 9, 4500, None, None,
                        [{"effective_from": "2026-01-01", "monthly_rent": 0}]), 0.0)
check("A15 garbage current_rent -> None", sl.rent_for_month(2026, 9, current_rent="lots"), None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# B. rent-due resolution + window
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("B1 house default IS first week", sl.HOUSE_RENT_DUE, {"kind": "week", "value": 1})
check("B2 nothing anywhere -> house", sl.resolve_rent_due(None, None), {"kind": "week", "value": 1})
check("B3 store override wins", sl.resolve_rent_due({"kind": "day", "value": 5}, {"kind": "week", "value": 2}),
      {"kind": "day", "value": 5})
check("B4 no store override -> tenant default", sl.resolve_rent_due(None, {"kind": "week", "value": 2}),
      {"kind": "week", "value": 2})
check("B5 garbage store override falls to tenant",
      sl.resolve_rent_due({"kind": "week", "value": 9}, {"kind": "day", "value": 3}), {"kind": "day", "value": 3})
check("B6 garbage everywhere -> house", sl.resolve_rent_due({"kind": "x"}, "first"), {"kind": "week", "value": 1})
check("B7 normalize: week 0 / week 6 / day 0 / day 32 all rejected",
      [sl.normalize_rent_due({"kind": "week", "value": v}) for v in (0, 6)] +
      [sl.normalize_rent_due({"kind": "day", "value": v}) for v in (0, 32)], [None] * 4)
check("B8 normalize: string value coerces", sl.normalize_rent_due({"kind": "day", "value": "15"}),
      {"kind": "day", "value": 15})
check("B9 window: week 1 = 1st..7th", sl.rent_due_window(2026, 9, {"kind": "week", "value": 1}),
      ("2026-09-01", "2026-09-07"))
check("B10 window: week 5 of a 30-day month clamps to the tail",
      sl.rent_due_window(2026, 9, {"kind": "week", "value": 5}), ("2026-09-29", "2026-09-30"))
check("B11 window: week 5 of Feb (28 days) clamps to the real tail",
      sl.rent_due_window(2026, 2, {"kind": "week", "value": 5}), ("2026-02-22", "2026-02-28"))
check("B12 window: day 31 clamps in Feb (leap year)",
      sl.rent_due_window(2028, 2, {"kind": "day", "value": 31}), ("2028-02-29", "2028-02-29"))
check("B13 window: day kind is a single date", sl.rent_due_window(2026, 9, {"kind": "day", "value": 5}),
      ("2026-09-05", "2026-09-05"))
check("B14 window: malformed due resolves house-first (week 1)",
      sl.rent_due_window(2026, 9, {"bogus": 1}), ("2026-09-01", "2026-09-07"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# C. resolve_lease_access truth table
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for role, want in [("admin", True), ("master_admin", True), ("market_manager", True), ("market", True),
                   ("Market Manager", True), ("district_manager", False), ("store_manager", False),
                   ("sales_rep", False), ("weird_role", False), ("", False), (None, False)]:
    check(f"C1 default allow-list: {role!r} -> {want}", sl.resolve_lease_access(role, "store"), want)
check("C2 scope 'all' passes regardless of role", sl.resolve_lease_access("janitor", "all"), True)
check("C3 grant passes regardless of role", sl.resolve_lease_access("sales_rep", "self", None, True), True)
check("C4 custom allow-list REPLACES the default (MM out, ops lead in)",
      [sl.resolve_lease_access("market_manager", "market", ["operations_lead"]),
       sl.resolve_lease_access("Operations Lead", "market", ["operations_lead"])], [False, True])
check("C5 empty-string entries in the allow-list never match an empty role",
      sl.resolve_lease_access("", "store", ["", "  "]), False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# D. can_see_lease end-to-end (fake core + fake DB)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
T_DEFAULT = [{"lease_visible_roles": None, "rent_due_default": {"kind": "week", "value": 1}}]
T_CUSTOM = [{"lease_visible_roles": ["operations_lead"], "rent_due_default": {"kind": "day", "value": 3}}]
RBAC_ON = [{"rbac_enabled": True}]
RBAC_OFF = [{"rbac_enabled": False}]

c_def = FakeClient(tenants=T_DEFAULT, app_config=RBAC_ON)
for tok, want in [("Bearer admin", True), ("Bearer super", True), ("Bearer mm", True),
                  ("Bearer dm", False), ("Bearer sm", False), ("Bearer rep", False),
                  ("Bearer sm-grant", True), ("Bearer ghost", False), ("Bearer broken", False)]:
    check(f"D1 default tenant: {tok} -> {want}", sl.can_see_lease(tok, "ORG1", client=c_def), want)
c_cus = FakeClient(tenants=T_CUSTOM, app_config=RBAC_ON)
check("D2 custom allow-list: MM now denied", sl.can_see_lease("Bearer mm", "ORG1", client=c_cus), False)
check("D3 custom allow-list: ops lead allowed", sl.can_see_lease("Bearer ops", "ORG1", client=c_cus), True)
check("D4 custom allow-list: admin still passes on scope", sl.can_see_lease("Bearer admin", "ORG1", client=c_cus), True)
check("D5 open-app parity: NO token + login switch OFF -> allowed",
      sl.can_see_lease("", "ORG1", client=FakeClient(tenants=T_DEFAULT, app_config=RBAC_OFF)), True)
check("D6 NO token + login switch ON -> denied", sl.can_see_lease("", "ORG1", client=c_def), False)
check("D7 NO token + unreadable app_config -> denied (assume enforced)",
      sl.can_see_lease("", "ORG1", client=FakeClient(tenants=T_DEFAULT, fail_tables=("app_config",))), False)
check("D8 pre-946 tenants schema -> built-in allow-list still gates (MM yes, rep no)",
      [sl.can_see_lease("Bearer mm", "ORG1", client=FakeClient(tenants=[], app_config=RBAC_ON, fail_tables=("tenants",))),
       sl.can_see_lease("Bearer rep", "ORG1", client=FakeClient(tenants=[], app_config=RBAC_ON, fail_tables=("tenants",)))],
      [True, False])
check("D9 tenant_lease_config adaptive: failure -> (None, None)",
      sl.tenant_lease_config("ORG1", FakeClient(fail_tables=("tenants",))), (None, None))
check("D10 tenant_lease_config normalizes garbage default to None (house applies downstream)",
      sl.tenant_lease_config("ORG1", FakeClient(tenants=[{"lease_visible_roles": [], "rent_due_default": "first"}])),
      (None, None))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# E. strip_sensitive + decode_doc_data_url
# ══════════════════════════════════════════════════════════════════════════════════════════════════
row = {"landlord_name": "Acme Realty", "ach_bank_name": "B", "ach_routing_number": "021",
       "ach_account_number": "99", "ach_notes": "wire ok", "current_rent": 4500}
sl.strip_sensitive(row)
check("E1 ACH keys DELETED (not zeroed)", [k for k in sl.ACH_FIELDS if k in row], [])
check("E2 non-sensitive fields survive", (row.get("landlord_name"), row.get("current_rent")),
      ("Acme Realty", 4500))
check("E3 strip is idempotent + tolerant", sl.strip_sensitive(sl.strip_sensitive(None)), None)

import base64                                                        # noqa: E402
pdf_url = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()
raw, ext, ctype = sl.decode_doc_data_url(pdf_url)
check("E4 pdf decodes", (raw[:4], ext, ctype), (b"%PDF", "pdf", "application/pdf"))
raw, ext, ctype = sl.decode_doc_data_url("data:image/png;base64," + base64.b64encode(b"\x89PNG").decode())
check("E5 png decodes", (ext, ctype), ("png", "image/png"))
for bad in ["", "no-comma", "data:text/html;base64," + base64.b64encode(b"<x>").decode(),
            "data:application/pdf;base64,@@not-base64@@"]:
    try:
        sl.decode_doc_data_url(bad)
        check(f"E6 rejects {bad[:24]!r}", "no error", "ValueError")
    except ValueError:
        check(f"E6 rejects {bad[:24]!r}", "ValueError", "ValueError")
try:
    sl.decode_doc_data_url("data:application/pdf;base64," +
                           base64.b64encode(b"x" * (sl.MAX_DOC_BYTES + 1)).decode())
    check("E7 oversize rejected", "no error", "ValueError")
except ValueError:
    check("E7 oversize rejected", "ValueError", "ValueError")
check("E8 safe filename", sl._safe_name("../../etc/passwd my lease (final).PDF"),
      "etc_passwd_my_lease_final_.PDF")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# F. ARMED negative control — a wrong expectation must FAIL
# ══════════════════════════════════════════════════════════════════════════════════════════════════
before = len(FAIL)
check("F1 armed control (sales_rep must NOT see lease)", sl.resolve_lease_access("sales_rep", "self"), True)
if len(FAIL) == before + 1 and "F1" in FAIL[-1]:
    FAIL.pop()
    PASS.append("F1 armed negative control fired")
else:
    FAIL.append("F1 armed negative control DID NOT fire — harness cannot detect failures")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL: {f}")
sys.exit(1 if FAIL else 0)
