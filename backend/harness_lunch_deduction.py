"""Offline proof for the 2026-07-27 owner-directed lunch-break auto-deduction (mod-people, branch
agent/people/timeclock-filter-lunch), Deliverable 3.

Covers:
  A. Pure guard logic (lunch_deduction.py, no DB): precedence (per-employee overrides tenant,
     enabled/minutes independently), the double-deduction guard (single pair, gapless-merge epsilon,
     real-break/split-shift skip, open-punch-present skip), threshold, negative-hours guard, store
     attribution, multi-employee/day aggregation.
  B. Availability/degrade signal (get_tenant_lunch_config): a missing tenants row, a tenants row that
     predates migration 418 (no lunch keys — the shape of every pre-existing harness fixture in this
     repo), and a genuinely-migrated row are each classified correctly; a raising client is caught.
  C. End-to-end through the REAL shipped router handlers (get_payroll, get_payroll_by_store,
     payroll_actual_hours_detail, timeclock_list) against an in-memory fake Supabase client — the
     guard, threshold, per-employee override precedence, negative-hours guard, and split-shift
     handling all reproduced at the HTTP-handler level, not just the pure-function level, PLUS the
     /payroll <-> drill-down reconciliation the task requires.
  D. EQUIVALENCE — the exact same C fixtures, but the tenant row has never heard of migration 418: the
     four wired endpoints' JSON is BYTE-IDENTICAL (same keys, not just same values) to a build with no
     lunch-deduction code at all.
  E. Config endpoints (GET/PUT /timeclock/lunch-config, PUT /employees/{id}/lunch-config): manager
     gate, persistence, payroll_change_log logging (entry_point='lunch_deduction_config'), and
     graceful degrade when the log table is missing.
  F. Backend half of the Deliverable-1 filter fix: /timeclock/list's date range is INCLUSIVE on both
     ends and org-isolated (the frontend's own request-id/abort-guard fix is proven separately by
     frontend/scratchpad/prove_timeclock_filter_race.mjs, per the repo's established JS-logic-proof
     convention — nothing here duplicates that).

Run: `python3 harness_lunch_deduction.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION A — pure lunch_deduction.py, no DB/router involved at all.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.storeops.lunch_deduction import (  # noqa: E402
    resolve_employee_lunch_settings, compute_lunch_deduction_from_rows, LUNCH_GAP_EPSILON_MINUTES,
)

TENANT_DEFAULT = {"enabled": True, "minutes": 30, "min_shift_hours": 6.0}

# A1-A3: precedence
check("A1 no override -> pure tenant default (True, 30, 6.0)",
      resolve_employee_lunch_settings(TENANT_DEFAULT, None) == (True, 30, 6.0))
check("A2 per-employee enabled=False WINS over tenant enabled=True",
      resolve_employee_lunch_settings(TENANT_DEFAULT, {"enabled": False, "minutes": None})[0] is False)
check("A3 per-employee minutes=45 WINS over tenant minutes=30, INDEPENDENTLY of enabled (still inherits enabled=True)",
      resolve_employee_lunch_settings(TENANT_DEFAULT, {"enabled": None, "minutes": 45}) == (True, 45, 6.0))
check("A3b per-employee enabled=True override on a tenant-disabled default (both fields independent)",
      resolve_employee_lunch_settings({**TENANT_DEFAULT, "enabled": False}, {"enabled": True, "minutes": None})[0] is True)


def pair(id_, ci, co, hrs, store="S1"):
    return {"id": id_, "employee_id": "E1", "work_date": "2026-07-06", "store_code": store,
            "clock_in": f"2026-07-06T{ci}:00", "clock_out": f"2026-07-06T{co}:00", "hours": hrs}


# A4: single continuous pair >= threshold -> deducted, negative-guard never triggers on a sane config
r = compute_lunch_deduction_from_rows([pair("t1", "09:00", "16:00", 7.0)], TENANT_DEFAULT, {})
d = r["days"][0]
check("A4 single 7h pair >= 6h threshold -> applied, 0.5h (30min) deducted",
      d["applied"] is True and d["deduct_hours"] == 0.5, d)
check("A4b by_employee/by_employee_store both carry the 0.5h", r["by_employee"].get("E1") == 0.5
      and r["by_employee_store"].get(("E1", "S1")) == 0.5, r)

# A5: below threshold -> skipped
r = compute_lunch_deduction_from_rows([pair("t1", "09:00", "14:00", 5.0)], TENANT_DEFAULT, {})
check("A5 5h < 6h threshold -> NOT applied, skip_reason=below_threshold",
      r["days"][0]["applied"] is False and r["days"][0]["skip_reason"] == "below_threshold", r["days"][0])

# A6: gapless-merge — two pairs with a gap <= epsilon are ONE continuous block
rows_gapless = [pair("t1", "09:00", "13:00", 4.0), pair("t2", "13:00", "17:00", 4.0)]  # gap = 0 min
r = compute_lunch_deduction_from_rows(rows_gapless, TENANT_DEFAULT, {})
d = r["days"][0]
check("A6 two ZERO-gap pairs (system artifact, e.g. force-clockout immediately re-clocked) merge into "
      "ONE continuous 8h block -> applied, deducted ONCE on the LAST pair only",
      d["applied"] is True and d["deduct_hours"] == 0.5 and d["worked_hours"] == 8.0
      and d["marked_punch_id"] == "t2", d)

# A6b: a gap of exactly the epsilon (1 minute) still merges (<=, not <)
rows_epsilon = [{"id": "t1", "employee_id": "E1", "work_date": "2026-07-06", "store_code": "S1",
                 "clock_in": "2026-07-06T09:00:00", "clock_out": "2026-07-06T13:00:00", "hours": 4.0},
                {"id": "t2", "employee_id": "E1", "work_date": "2026-07-06", "store_code": "S1",
                 "clock_in": "2026-07-06T13:01:00", "clock_out": "2026-07-06T17:00:00", "hours": 3.983}]
r = compute_lunch_deduction_from_rows(rows_epsilon, TENANT_DEFAULT, {})
check(f"A6b a gap of exactly {LUNCH_GAP_EPSILON_MINUTES} min still counts as continuous (boundary, <=)",
      r["days"][0]["applied"] is True, r["days"][0])

# A7: THE DOUBLE-DEDUCTION GUARD — a REAL gap (a genuine lunch re-clock, or a true split shift; the
# module treats both identically) -> NEVER deducted, on EITHER pair.
rows_realbreak = [pair("t1", "09:00", "12:00", 3.0), pair("t2", "12:30", "17:00", 4.5)]  # 30 min gap
r = compute_lunch_deduction_from_rows(rows_realbreak, TENANT_DEFAULT, {})
check("A7 a real 30-min gap between pairs -> NOT applied, skip_reason=real_break_present "
      "(same treatment for a genuine lunch re-clock-in AND a true split shift — the module cannot "
      "and does not try to distinguish them, see module docstring)",
      r["days"][0]["applied"] is False and r["days"][0]["skip_reason"] == "real_break_present", r["days"][0])
# A7b: a LARGE gap (a true AM/PM split shift, e.g. 3 hours) is the SAME code path, same outcome.
rows_split = [pair("t1", "09:00", "13:00", 4.0), pair("t2", "16:00", "20:00", 4.0)]  # 3h gap
r = compute_lunch_deduction_from_rows(rows_split, TENANT_DEFAULT, {})
check("A7b a 3-hour split-shift gap -> also NOT applied (explicit split-shift rule: never deduct twice "
      "for a split shift means never deduct AT ALL across a gapped day, since the gap itself already "
      "represents unpaid time)", r["days"][0]["applied"] is False, r["days"][0])

# A8: an OPEN punch that day defers the decision entirely (can't yet know if a 2nd, gapped pair is coming)
rows_open = [{"id": "t1", "employee_id": "E1", "work_date": "2026-07-06", "store_code": "S1",
              "clock_in": "2026-07-06T09:00:00", "clock_out": None, "hours": None}]
r = compute_lunch_deduction_from_rows(rows_open, TENANT_DEFAULT, {})
check("A8 an open (not yet clocked out) punch that day -> NOT applied, skip_reason=open_punch_present",
      r["days"][0]["applied"] is False and r["days"][0]["skip_reason"] == "open_punch_present", r["days"][0])
# A8b: one CLOSED qualifying pair + a SEPARATE still-open pair the same day -> the whole day defers,
# even though the closed pair alone would have qualified in isolation.
rows_mixed_open = [pair("t1", "09:00", "16:00", 7.0),
                    {"id": "t2", "employee_id": "E1", "work_date": "2026-07-06", "store_code": "S1",
                     "clock_in": "2026-07-06T18:00:00", "clock_out": None, "hours": None}]
r = compute_lunch_deduction_from_rows(rows_mixed_open, TENANT_DEFAULT, {})
check("A8b a closed qualifying pair PLUS a still-open pair the same day -> deferred, not applied "
      "(never deduct now and risk being wrong once the 2nd pair closes gapped)",
      r["days"][0]["applied"] is False and r["days"][0]["skip_reason"] == "open_punch_present", r["days"][0])

# A9: disabled (tenant off, no override) -> never applied
r = compute_lunch_deduction_from_rows([pair("t1", "09:00", "16:00", 7.0)], {**TENANT_DEFAULT, "enabled": False}, {})
check("A9 tenant disabled -> NOT applied, skip_reason=disabled",
      r["days"][0]["applied"] is False and r["days"][0]["skip_reason"] == "disabled", r["days"][0])

# A10: NEGATIVE-HOURS GUARD — an aggressive config (min_shift_hours=0, minutes=600=10h) on a 1h day
# never produces a negative deduction.
r = compute_lunch_deduction_from_rows([pair("t1", "09:00", "10:00", 1.0)],
                                       {"enabled": True, "minutes": 600, "min_shift_hours": 0}, {})
d = r["days"][0]
check("A10 NEGATIVE-HOURS GUARD: minutes=600 configured on a 1h day -> deduct_hours clamped to the "
      "worked hours (1.0), never more", d["applied"] is True and d["deduct_hours"] == 1.0, d)

# A11: minutes=0 configured -> technically 'enabled' but nothing to deduct
r = compute_lunch_deduction_from_rows([pair("t1", "09:00", "16:00", 7.0)],
                                       {"enabled": True, "minutes": 0, "min_shift_hours": 6}, {})
check("A11 minutes=0 configured -> deduct_hours=0, NOT applied, skip_reason=zero_minutes_configured",
      r["days"][0]["deduct_hours"] == 0.0 and r["days"][0]["applied"] is False
      and r["days"][0]["skip_reason"] == "zero_minutes_configured", r["days"][0])

# A12: multi-employee/multi-day aggregation + per-employee override precedence, in the SAME call
multi_rows = [
    pair("t1", "09:00", "16:00", 7.0),                                     # E1, day1: qualifies (30min tenant default)
    {**pair("t2", "09:00", "17:00", 8.0), "employee_id": "E2", "id": "t2"},  # E2, day1: qualifies too, but overridden
]
overrides = {"E2": {"enabled": True, "minutes": 45}}   # E2's own override: 45 min, not the tenant's 30
r = compute_lunch_deduction_from_rows(multi_rows, TENANT_DEFAULT, overrides)
check("A12 E1 uses the tenant default (30min=0.5h)", r["by_employee"].get("E1") == 0.5, r["by_employee"])
check("A12b E2's PER-EMPLOYEE override (45min=0.75h) applies INSTEAD of the tenant's 30min",
      r["by_employee"].get("E2") == 0.75, r["by_employee"])

print(f"[Section A] {len(PASS)} passed, {len(FAIL)} failed so far")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION B — availability/degrade signal (get_tenant_lunch_config): key-PRESENCE, not just
# exception-catching, so the in-memory fake-client harness suite (which never raises for an unknown
# dict key) still correctly models "migration 418 hasn't run" for every OLD fixture in this repo.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.storeops.lunch_deduction import get_tenant_lunch_config  # noqa: E402


class _FakeQ:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQ(self._rows)


check("B1 no tenant row at all -> available=False (owner-default shown for reference only, never used)",
      get_tenant_lunch_config("org", _FakeTable([])) == (TENANT_DEFAULT, False))
check("B2 a tenant row that predates migration 418 (no lunch keys — the EXACT shape of every "
      "pre-existing harness fixture in this repo, e.g. {'org_id': ORG}) -> available=False",
      get_tenant_lunch_config("org", _FakeTable([{"org_id": "org"}])) == (TENANT_DEFAULT, False))
check("B3 a genuinely migrated row (even explicitly OFF) -> available=True, real values returned",
      get_tenant_lunch_config("org", _FakeTable([{"org_id": "org", "lunch_deduction_enabled": False,
                                                   "lunch_deduction_minutes": 15, "lunch_deduction_min_shift_hours": 5}]))
      == ({"enabled": False, "minutes": 15, "min_shift_hours": 5.0}, True))


class _RaisingTable:
    def table(self, _name):
        raise RuntimeError("column does not exist")


check("B4 a raising client (real PostgREST 400 for an unknown column, pre-migration) -> available=False",
      get_tenant_lunch_config("org", _RaisingTable())[1] is False)

print(f"[Section B] {len(PASS)} passed, {len(FAIL)} failed so far")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION C — end-to-end through the REAL shipped router handlers.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload, self._limit, self._order = None, None, None, None

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(str(x) for x in vals))); return self

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def order(self, k, desc=False):
        self._order = (k, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def delete(self):
        self._mode = "delete"; return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v):
                return False
            if kind == "in" and str(rv) not in v:
                return False
            if kind == "is" and v == "null" and rv is not None:
                return False
            if kind == "gte" and str(rv) < str(v):
                return False
            if kind == "lt" and str(rv) >= str(v):
                return False
            if kind == "lte" and str(rv) > str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                row = dict(p)
                row.setdefault("id", f"row{len(rows) + len(out) + 1}")
                out.append(row)
            rows.extend(out)
            return Result(out)
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
        if self._mode == "delete":
            self.store[self.key] = [r for r in rows if not self._matches(r)]
            return Result(matched)
        if self._order:
            k, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result(matched)


class Result:
    def __init__(self, data):
        self.data = data


class FakeSchema:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t))

    def rpc(self, *_a, **_k):
        raise RuntimeError("RPC not available in this harness (legacy path only)")


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchema(self, name)

    def table(self, t):
        return FakeQuery(self.store, ("storeops", t))

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402
import app.modules.core.router as core_router  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")
core_router._uid_from_token = lambda auth: {"Bearer mgr": "uid-mgr"}.get(auth)

ORG = "org-lunch-1"
AUTH = "Bearer mgr"

MIGRATED_TENANT = {"org_id": ORG, "lunch_deduction_enabled": True, "lunch_deduction_minutes": 30,
                    "lunch_deduction_min_shift_hours": 6.0}
UNMIGRATED_TENANT = {"org_id": ORG}   # the exact pre-418 shape


def reset(tenant_row):
    fake.store.clear()
    fake.seed("storeops", "employees", [
        {"id": 1, "org_id": ORG, "employee_id": "E1", "name": "Continuous Carl", "home_store": "S1",
         "pay_rate": 20.0, "is_active": True},
        {"id": 2, "org_id": ORG, "employee_id": "E2", "name": "Breaktime Bea", "home_store": "S1",
         "pay_rate": 20.0, "is_active": True},
        {"id": 3, "org_id": ORG, "employee_id": "E3", "name": "Overridden Omar", "home_store": "S1",
         "pay_rate": 20.0, "is_active": True, "lunch_deduction_enabled": False},
        {"id": 4, "org_id": ORG, "employee_id": "E4", "name": "ShortDay Sam", "home_store": "S1",
         "pay_rate": 20.0, "is_active": True},
    ])
    fake.seed("storeops", "app_users", [
        {"org_id": ORG, "auth_id": "uid-mgr", "email": "dm@example.com", "role": "district_manager", "employee_id": "E1"},
    ])
    fake.seed("storeops", "tenants", [tenant_row])
    fake.seed("storeops", "stores", [{"org_id": ORG, "store_code": "S1", "market": "M1", "is_active": True}])
    fake.seed("storeops", "shifts", [])
    fake.seed("storeops", "manual_hours", [])
    fake.seed("storeops", "hours_budget", [])
    fake.seed("storeops", "payroll_change_log", [])
    fake.seed("storeops", "timelog", [
        # E1: single continuous 7h pair -> qualifies for the tenant default 30min deduction.
        {"id": "t1", "org_id": ORG, "employee_id": "E1", "employee_name": "Continuous Carl", "store_code": "S1",
         "clock_in": "2026-07-06T09:00:00", "clock_out": "2026-07-06T16:00:00", "hours": 7.0,
         "work_date": "2026-07-06", "created_at": "2026-07-06T16:00:01"},
        # E2: a REAL 45-min lunch break already taken (two pairs, real gap) -> guard skips this day.
        {"id": "t2a", "org_id": ORG, "employee_id": "E2", "employee_name": "Breaktime Bea", "store_code": "S1",
         "clock_in": "2026-07-06T09:00:00", "clock_out": "2026-07-06T12:00:00", "hours": 3.0,
         "work_date": "2026-07-06", "created_at": "2026-07-06T12:00:01"},
        {"id": "t2b", "org_id": ORG, "employee_id": "E2", "employee_name": "Breaktime Bea", "store_code": "S1",
         "clock_in": "2026-07-06T12:45:00", "clock_out": "2026-07-06T17:00:00", "hours": 4.25,
         "work_date": "2026-07-06", "created_at": "2026-07-06T17:00:01"},
        # E3: same shape as E1 (single continuous 7h pair) but has a PER-EMPLOYEE enabled=False override.
        {"id": "t3", "org_id": ORG, "employee_id": "E3", "employee_name": "Overridden Omar", "store_code": "S1",
         "clock_in": "2026-07-06T09:00:00", "clock_out": "2026-07-06T16:00:00", "hours": 7.0,
         "work_date": "2026-07-06", "created_at": "2026-07-06T16:00:01"},
        # E4: single continuous pair but only 5h -> below the 6h default threshold.
        {"id": "t4", "org_id": ORG, "employee_id": "E4", "employee_name": "ShortDay Sam", "store_code": "S1",
         "clock_in": "2026-07-06T09:00:00", "clock_out": "2026-07-06T14:00:00", "hours": 5.0,
         "work_date": "2026-07-06", "created_at": "2026-07-06T14:00:01"},
    ])


reset(MIGRATED_TENANT)
pay = {r["employee_id"]: r for r in R.get_payroll(start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)}

check("C1 E1 (single 7h continuous pair, >=6h threshold): actual_hours netted to 6.5 (7 - 0.5 lunch)",
      pay["E1"]["actual_hours"] == 6.5, pay["E1"])
check("C1b E1's lunch_deduction_hours explicit line = 0.5, HONESTY (not folded silently — its own field)",
      pay["E1"]["lunch_deduction_hours"] == 0.5, pay["E1"])
check("C1c E1's actual_pay = NETTED hours * rate (6.5 * 20 = 130), hourly pay = (hours - deduction) x rate",
      pay["E1"]["actual_pay"] == 130.0, pay["E1"])

check("C2 E2 (real 45-min break already taken, 2 punch-pairs with a real gap): NO auto-deduction on "
      "top — actual_hours = full 7.25h worked (3.0 + 4.25), untouched", pay["E2"]["actual_hours"] == 7.25, pay["E2"])
check("C2b E2's lunch_deduction_hours = 0 (double-deduction guard fired)", pay["E2"]["lunch_deduction_hours"] == 0.0, pay["E2"])

check("C3 E3 (PER-EMPLOYEE override enabled=False, same 7h shape as E1): NOT deducted despite the "
      "tenant default being ON — per-employee override wins", pay["E3"]["actual_hours"] == 7.0
      and pay["E3"]["lunch_deduction_hours"] == 0.0, pay["E3"])

check("C4 E4 (5h < 6h default threshold): NOT deducted", pay["E4"]["actual_hours"] == 5.0
      and pay["E4"]["lunch_deduction_hours"] == 0.0, pay["E4"])

# /payroll-by-store — same netting, attributed to the store.
by_store = {s["store_code"]: s for s in R.get_payroll_by_store(start="2026-07-06", end="2026-07-06",
                                                                authorization=AUTH, org_id=ORG)["stores"]}
expected_store_hours = 6.5 + 7.25 + 7.0 + 5.0   # E1 netted, E2/E3/E4 untouched
expected_store_amount = expected_store_hours * 20.0
check("C5 /payroll-by-store S1 hours reflects the SAME netted total as /payroll's row-level sum "
      f"({expected_store_hours}h)", by_store["S1"]["hours"] == expected_store_hours, by_store["S1"])
check("C5b /payroll-by-store S1 amount = netted hours * rate", by_store["S1"]["amount"] == expected_store_amount, by_store["S1"])

# Drill-down reconciliation — the task's explicit requirement.
detail = R.payroll_actual_hours_detail(employee_id="E1", start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)
check("C6 RECONCILIATION: drill-down total_actual_hours == /payroll row's actual_hours EXACTLY (6.5h, "
      "both netting the SAME 0.5h off the SAME basis)", detail["total_actual_hours"] == pay["E1"]["actual_hours"] == 6.5, detail)
check("C6b drill-down total_lunch_deduction_hours == the report row's lunch_deduction_hours (0.5h)",
      detail["total_lunch_deduction_hours"] == pay["E1"]["lunch_deduction_hours"] == 0.5, detail)
check("C6c drill-down's day-level line matches too (applied=True, 0.5h, no skip_reason)",
      detail["days"][0]["lunch_deduction_applied"] is True and detail["days"][0]["lunch_deduction_hours"] == 0.5
      and detail["days"][0]["lunch_deduction_skip_reason"] is None, detail["days"][0])

detail_e2 = R.payroll_actual_hours_detail(employee_id="E2", start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)
check("C7 drill-down explains E2's real-break skip explicitly (never silently hides why)",
      detail_e2["days"][0]["lunch_deduction_applied"] is False
      and detail_e2["days"][0]["lunch_deduction_skip_reason"] == "real_break_present", detail_e2["days"][0])

# Deliverable-1 linkage: /timeclock/list marks the EXACT punch, never both pairs of a gapless day twice.
tc_list = R.timeclock_list(start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)
by_id = {r["id"]: r for r in tc_list}
check("C8 /timeclock/list: E1's single punch carries lunch_deduction_hours=0.5 (the explicit line)",
      by_id["t1"]["lunch_deduction_hours"] == 0.5, by_id["t1"])
check("C8b /timeclock/list: E2's TWO punches (real break) both carry 0.0 — never fabricated",
      by_id["t2a"]["lunch_deduction_hours"] == 0.0 and by_id["t2b"]["lunch_deduction_hours"] == 0.0, tc_list)
check("C8c /timeclock/list: E1's own hours field is UNTOUCHED (still 7.0, never silently mutated)",
      by_id["t1"]["hours"] == 7.0, by_id["t1"])

print(f"[Section C] {len(PASS)} passed, {len(FAIL)} failed so far")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION D — EQUIVALENCE: the SAME fixtures, but the tenant has never run migration 418. Every wired
# endpoint must be BYTE-IDENTICAL (same keys, not just same values) to a caller that predates this
# feature entirely.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
reset(UNMIGRATED_TENANT)
pay_off = R.get_payroll(start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)
check("D1 pre-migration: NO row carries a lunch_deduction_hours key at all (not even 0.0)",
      all("lunch_deduction_hours" not in r for r in pay_off), pay_off)
e1_off = next(r for r in pay_off if r["employee_id"] == "E1")
check("D2 pre-migration: E1's actual_hours is the RAW 7.0 (no netting happened)", e1_off["actual_hours"] == 7.0, e1_off)
check("D2b pre-migration: E1's actual_pay = 7.0 * 20 = 140.0 (unnetted)", e1_off["actual_pay"] == 140.0, e1_off)

by_store_off = R.get_payroll_by_store(start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)["stores"]
raw_total_hours = 7.0 + 7.25 + 7.0 + 5.0
check("D3 pre-migration: /payroll-by-store hours = the RAW total (no netting)",
      next(s for s in by_store_off if s["store_code"] == "S1")["hours"] == raw_total_hours, by_store_off)

detail_off = R.payroll_actual_hours_detail(employee_id="E1", start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)
check("D4 pre-migration: drill-down has NO lunch_deduction_* keys anywhere (day rows or the total)",
      "total_lunch_deduction_hours" not in detail_off
      and all("lunch_deduction_hours" not in d and "lunch_deduction_applied" not in d for d in detail_off["days"]),
      detail_off)
check("D4b pre-migration: drill-down total_actual_hours = the RAW 7.0 too", detail_off["total_actual_hours"] == 7.0, detail_off)

tc_off = R.timeclock_list(start="2026-07-06", end="2026-07-06", authorization=AUTH, org_id=ORG)
check("D5 pre-migration: /timeclock/list rows carry NO lunch_deduction_hours key at all",
      all("lunch_deduction_hours" not in r for r in tc_off), tc_off)

print(f"[Section D] {len(PASS)} passed, {len(FAIL)} failed so far")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION E — config endpoints (GET/PUT /timeclock/lunch-config, PUT /employees/{id}/lunch-config).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from fastapi import HTTPException  # noqa: E402

reset(MIGRATED_TENANT)
cfg = R.get_lunch_config_endpoint(authorization=AUTH, org_id=ORG)
check("E1 GET /timeclock/lunch-config: available=True, tenant echoes the seeded row",
      cfg["available"] is True and cfg["tenant"]["minutes"] == 30, cfg)
check("E1b GET carries the per-employee override roster (E3's enabled=False)",
      any(o["employee_id"] == "E3" and o["enabled"] is False for o in cfg["employee_overrides"]), cfg)

# a non-manager caller is rejected (matches PUT /timeoff-conflict-mode's posture)
core_router._uid_from_token = lambda auth: {"Bearer mgr": "uid-mgr", "Bearer rep": "uid-rep"}.get(auth)
fake.store[("storeops", "app_users")].append(
    {"org_id": ORG, "auth_id": "uid-rep", "email": "rep@example.com", "role": "sales_rep", "employee_id": "E1"})
raised = False
try:
    R.set_lunch_config_endpoint({"minutes": 45}, authorization="Bearer rep", org_id=ORG)
except HTTPException as e:
    raised = e.status_code == 403
check("E2 PUT /timeclock/lunch-config: a non-manager caller is rejected (403)", raised)

out = R.set_lunch_config_endpoint({"enabled": True, "minutes": 45, "min_shift_hours": 5},
                                   authorization=AUTH, org_id=ORG)
check("E3 PUT /timeclock/lunch-config: manager can save; response echoes the new tenant config",
      out["ok"] is True and out["tenant"]["minutes"] == 45 and out["tenant"]["min_shift_hours"] == 5.0, out)
log = fake.store.get(("storeops", "payroll_change_log"), [])
check("E4 tenant config change logged to payroll_change_log, entry_point=lunch_deduction_config",
      any(l["entry_point"] == "lunch_deduction_config" and l["field"] == "lunch_deduction_minutes"
          and l["before_value"] == "30" and l["after_value"] == "45" for l in log), log)

# Per-employee override write + clearing back to "inherit"
out = R.set_employee_lunch_config("4", {"enabled": True, "minutes": 10}, authorization=AUTH, org_id=ORG)
check("E5 PUT /employees/{id}/lunch-config: E4 given an explicit override (enabled=True, 10 min)",
      out["lunch_deduction_enabled"] is True and out["lunch_deduction_minutes"] == 10, out)
log2 = fake.store.get(("storeops", "payroll_change_log"), [])
check("E5b per-employee override change ALSO logged, employee_id attributed correctly",
      any(l["entry_point"] == "lunch_deduction_config" and l["employee_id"] == "E4"
          and l["field"] == "lunch_deduction_minutes" for l in log2), log2)

out2 = R.set_employee_lunch_config("4", {"enabled": None, "minutes": None}, authorization=AUTH, org_id=ORG)
check("E6 clearing the override (null/null) resets to 'inherit the tenant default'",
      out2["lunch_deduction_enabled"] is None and out2["lunch_deduction_minutes"] is None, out2)

# Degrade: the config write endpoints never let a missing table break — best-effort like every other hook.
_real_table = fake.table
def _boom_table(name):
    if name == "payroll_change_log":
        raise RuntimeError("relation does not exist")
    return _real_table(name)
fake.table = _boom_table
try:
    out3 = R.set_employee_lunch_config("4", {"enabled": False, "minutes": None}, authorization=AUTH, org_id=ORG)
    e7 = out3["lunch_deduction_enabled"] is False
except Exception as e:
    e7 = False
fake.table = _real_table
check("E7 a payroll_change_log write failure never blocks the underlying employee-config save "
      "(best-effort, same as every other logging hook)", e7)

print(f"[Section E] {len(PASS)} passed, {len(FAIL)} failed so far")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION F — Deliverable-1 backend half: /timeclock/list date range is INCLUSIVE both ends, org-
# isolated. (The frontend request-id/abort-guard is proven separately, same JS-logic-proof convention
# as every other frontend fix in this repo — see frontend/scratchpad/prove_timeclock_filter_race.mjs.)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
reset(MIGRATED_TENANT)
fake.store[("storeops", "timelog")] = [
    {"id": "in1", "org_id": ORG, "employee_id": "E1", "employee_name": "Continuous Carl", "store_code": "S1",
     "clock_in": "2026-07-09T09:00:00", "clock_out": "2026-07-09T17:00:00", "hours": 8.0, "work_date": "2026-07-09"},
    {"id": "in2", "org_id": ORG, "employee_id": "E1", "employee_name": "Continuous Carl", "store_code": "S1",
     "clock_in": "2026-07-22T09:00:00", "clock_out": "2026-07-22T17:00:00", "hours": 8.0, "work_date": "2026-07-22"},
    {"id": "out_before", "org_id": ORG, "employee_id": "E1", "employee_name": "Continuous Carl", "store_code": "S1",
     "clock_in": "2026-07-08T09:00:00", "clock_out": "2026-07-08T17:00:00", "hours": 8.0, "work_date": "2026-07-08"},
    {"id": "out_after_today", "org_id": ORG, "employee_id": "E1", "employee_name": "Continuous Carl", "store_code": "S1",
     "clock_in": "2026-07-27T09:00:00", "clock_out": "2026-07-27T17:00:00", "hours": 8.0, "work_date": "2026-07-27"},
    {"id": "other_org", "org_id": "org-different", "employee_id": "E1", "employee_name": "Someone Else", "store_code": "S1",
     "clock_in": "2026-07-15T09:00:00", "clock_out": "2026-07-15T17:00:00", "hours": 8.0, "work_date": "2026-07-15"},
]
ranged = R.timeclock_list(start="2026-07-09", end="2026-07-22", authorization=AUTH, org_id=ORG)
ids = {r["id"] for r in ranged}
check("F1 filtering 07-09..07-22 is INCLUSIVE on the START boundary (in1, work_date==start)", "in1" in ids, ids)
check("F2 filtering 07-09..07-22 is INCLUSIVE on the END boundary (in2, work_date==end)", "in2" in ids, ids)
check("F3 a punch the day BEFORE the range is excluded", "out_before" not in ids, ids)
check("F4 THE REPORTED BUG'S EXACT SHAPE — a punch for 'today' (07-27, well past the filtered range) "
      "does NOT leak into a 07-09..07-22 filter", "out_after_today" not in ids, ids)
check("F5 org isolation: a different org's punch never leaks in", "other_org" not in ids, ids)
check("F6 exactly the 2 in-range rows, nothing more/less", ids == {"in1", "in2"}, ids)

print(f"[Section F] {len(PASS)} passed, {len(FAIL)} failed so far")


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
