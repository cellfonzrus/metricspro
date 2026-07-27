"""Offline proof for the 2026-07-27 owner-directed payroll rework (mod-people, branch
agent/people/payroll-onerow-audit):

  1) ONE ROW PER REP — reconcile_employee_identity() collapses the numeric-id-vs-business-id
     duplicate /payroll rows the Schedule page's shift creation (`employee_id: emp?.id?.toString()`)
     and the kiosk's timelog punches (`employee_id` = business `employees.employee_id`) otherwise
     produce for the SAME person. PRESENTATION ONLY: every check below proves grand totals and
     per-employee totals are byte-identical to the sum of the pre-merge rows.
  2) ACTUAL-HOURS DRILL-DOWN — GET /storeops/payroll/actual-hours-detail reconciles EXACTLY to the
     merged row's total, including faithfully reproducing (not silently fixing) the double-count
     artifact when the id mismatch defeats /payroll's own no-double-count rule, with an explicit
     `double_counted` flag + note.
  3) WEEKLY OVER-LIMIT HIGHLIGHTING — GET /storeops/payroll/over-hours flags a (store, week) whose
     actual hours exceed storeops.hours_budget.weekly_hours (migration 087, reused as directed by
     RULE TWO — no new config table). Display-only: never touches pay.
  4) MANUAL-EDIT AUDIT LOG — every write path that alters punches/hours (PATCH /shifts/{id},
     POST /timeclock/override, POST/DELETE /manual-hours, the force-clockout sweep) appends to
     storeops.payroll_change_log (migration 414); GET /payroll-change-log lists it, org+store scoped.

Run: `python3 harness_payroll_row_merge.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION A — pure reconcile_employee_identity() unit tests, NO db/router involved at all.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.storeops.payroll_identity import business_id_alias_map, reconcile_employee_identity  # noqa: E402

EMPLOYEES = [
    {"id": 45, "employee_id": "E45", "name": "Priya Rep", "pay_rate": 22.0},
    {"id": 46, "employee_id": "E46", "name": "Solo Rep", "pay_rate": 18.0},   # never mismatched in fixtures
    {"id": 50, "employee_id": "50", "name": "Same Id Rep", "pay_rate": 19.0},  # business id == numeric id (no-op case)
]

check("A1 alias map contains one entry per employee whose numeric/business ids actually differ "
      "(both E45 and E46 qualify; the id==50/employee_id=='50' employee correctly does NOT)",
      business_id_alias_map(EMPLOYEES) == {"45": "E45", "46": "E46"}, business_id_alias_map(EMPLOYEES))

# ── A2: THE REPORTED BUG, reproduced — two rows, same person, numeric-id-keyed scheduled row +
# business-id-keyed actual row (pay_rate wrongly 0 on the numeric-keyed row, exactly like the real
# /payroll aggregation produces before this fix).
dup_rows = [
    {"employee_id": "45", "name": "Priya Rep", "store": "S1", "pay_rate": 0.0,
     "scheduled_hours": 40.0, "actual_hours": 40.0, "shifts": 5, "scheduled_pay": 0.0, "actual_pay": 0.0},
    {"employee_id": "E45", "name": "Priya Rep", "store": "S1", "pay_rate": 22.0,
     "scheduled_hours": 0.0, "actual_hours": 45.0, "shifts": 0, "scheduled_pay": 0.0, "actual_pay": 990.0},
]
merged = reconcile_employee_identity(dup_rows, EMPLOYEES)
check("A2a merge collapses the 2 duplicate rows into exactly 1", len(merged) == 1, merged)
m = merged[0]
check("A2b merged row's employee_id is the CANONICAL business id", m["employee_id"] == "E45", m)
check("A2c merged row's pay_rate is the REAL rate (was 0 on the numeric-keyed row)", m["pay_rate"] == 22.0, m)
check("A2d merged scheduled_hours = SUM of both source rows (40+0)", m["scheduled_hours"] == 40.0, m)
check("A2e merged actual_hours = SUM of both source rows (40+45)", m["actual_hours"] == 85.0, m)
check("A2f merged shifts = SUM (5+0)", m["shifts"] == 5, m)
check("A2g merged scheduled_pay = SUM of already-computed values (0+0) — NOT recomputed as 40*22",
      m["scheduled_pay"] == 0.0, m)
check("A2h merged actual_pay = SUM of already-computed values (0+990) — the historical $ figure, "
      "byte-identical to before merging, never recomputed from the (inflated) merged hours",
      m["actual_pay"] == 990.0, m)

# ── A3: GRAND TOTAL invariance across a whole /payroll-shaped rows list (the harness's own proof
# that this is presentation-only, independent of any one employee's fields) ══════════════════════
mixed_rows = dup_rows + [
    {"employee_id": "E46", "name": "Solo Rep", "store": "S2", "pay_rate": 18.0,
     "scheduled_hours": 20.0, "actual_hours": 20.0, "shifts": 3, "scheduled_pay": 360.0, "actual_pay": 360.0},
    {"employee_id": None, "name": "Ghost Temp", "store": "S3", "pay_rate": 0.0,
     "scheduled_hours": 5.0, "actual_hours": 5.0, "shifts": 1, "scheduled_pay": 0.0, "actual_pay": 0.0},
]


def grand_totals(rows):
    return {
        "scheduled_hours": round(sum(r.get("scheduled_hours") or 0 for r in rows), 2),
        "actual_hours": round(sum(r.get("actual_hours") or 0 for r in rows), 2),
        "shifts": sum(r.get("shifts") or 0 for r in rows),
        "scheduled_pay": round(sum(r.get("scheduled_pay") or 0 for r in rows), 2),
        "actual_pay": round(sum(r.get("actual_pay") or 0 for r in rows), 2),
    }


before_tot = grand_totals(mixed_rows)
after_tot = grand_totals(reconcile_employee_identity(mixed_rows, EMPLOYEES))
check("A3 grand totals byte-identical before/after the row merge (associativity of addition, not "
      "just asserted — actually computed both ways)", before_tot == after_tot, (before_tot, after_tot))
check("A3b row count actually dropped (2 -> 1 for the duplicate person, others untouched: 4 -> 3)",
      len(mixed_rows) == 4 and len(reconcile_employee_identity(mixed_rows, EMPLOYEES)) == 3,
      len(reconcile_employee_identity(mixed_rows, EMPLOYEES)))
check("A3c the None-employee_id row (unassigned shift) is NEVER merged with anything, kept as-is",
      any(r.get("employee_id") is None and r.get("name") == "Ghost Temp"
          for r in reconcile_employee_identity(mixed_rows, EMPLOYEES)),
      reconcile_employee_identity(mixed_rows, EMPLOYEES))

# ── A4: a tenant whose rows already carry CONSISTENT ids (the shape of every fixture in
# harness_payroll_rpc_equivalence.py / harness_payroll_data_flow.py) is a COMPLETE no-op — same
# object VALUES, not just same totals — exactly why this bug was invisible to 100+ pre-existing
# harness checks despite being essentially universal in real Schedule-created shifts. ═══════════════
consistent_rows = [
    {"employee_id": "E46", "name": "Solo Rep", "store": "S2", "pay_rate": 18.0,
     "scheduled_hours": 20.0, "actual_hours": 20.0, "shifts": 3, "scheduled_pay": 360.0, "actual_pay": 360.0},
    {"employee_id": "50", "name": "Same Id Rep", "store": "S2", "pay_rate": 19.0,   # business id=="50"==numeric
     "scheduled_hours": 8.0, "actual_hours": 8.0, "shifts": 1, "scheduled_pay": 152.0, "actual_pay": 152.0},
]
no_op = reconcile_employee_identity(consistent_rows, EMPLOYEES)
check("A4 no-mismatch fixture is a byte-identical no-op (same values, same row count)",
      no_op == consistent_rows, no_op)

# ── A5: single-row shift-only employee (no timelog at all this period) still gets pay_rate CORRECTED
# for display even though there's nothing to merge — the $ columns stay whatever was already computed
# (still $0 here, since that's what the router actually paid — this function never invents money).
solo_shift_only = [{"employee_id": "45", "name": "Priya Rep", "store": "S1", "pay_rate": 0.0,
                    "scheduled_hours": 16.0, "actual_hours": 16.0, "shifts": 2,
                    "scheduled_pay": 0.0, "actual_pay": 0.0}]
solo_out = reconcile_employee_identity(solo_shift_only, EMPLOYEES)
check("A5a single shift-only row still relabels employee_id to the canonical business id",
      solo_out[0]["employee_id"] == "E45", solo_out)
check("A5b ...and shows the REAL pay_rate (was 0)", solo_out[0]["pay_rate"] == 22.0, solo_out)
check("A5c ...but scheduled_pay/actual_pay dollar figures are UNCHANGED (not recomputed from the "
      "corrected rate) — presentation only, even for this label-only relabel",
      solo_out[0]["scheduled_pay"] == 0.0 and solo_out[0]["actual_pay"] == 0.0, solo_out)

print(f"\n[Section A] {len(PASS)} passed, {len(FAIL)} failed so far")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION B — router-level: get_payroll() with a REALISTIC mismatched fixture (numeric-id-keyed
# shift + business-id-keyed timelog for the SAME person), proving the shipped handler now returns
# ONE row where it used to return two, with grand totals unaffected.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class Result:
    def __init__(self, data):
        self.data = data


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
core_router._uid_from_token = lambda auth: {"Bearer mgr": "uid-mgr", "Bearer x": "uid-mgr"}.get(auth, "uid-mgr")

ORG = "org-pm-1"
AUTH = "Bearer mgr"


def reset():
    fake.store.clear()
    fake.seed("storeops", "employees", [
        {"id": 45, "org_id": ORG, "employee_id": "E45", "name": "Priya Rep", "home_store": "S1",
         "pay_rate": 22.0, "is_active": True},
        {"id": 46, "org_id": ORG, "employee_id": "E46", "name": "Solo Kiosk", "home_store": "S1",
         "pay_rate": 18.0, "is_active": True},
    ])
    fake.seed("storeops", "app_users", [
        {"org_id": ORG, "auth_id": "uid-mgr", "email": "dm@luxelink.example", "role": "district_manager",
         "employee_id": "E45"},
    ])
    fake.seed("storeops", "tenants", [{"org_id": ORG}])
    fake.seed("storeops", "stores", [{"org_id": ORG, "store_code": "S1", "is_active": True}])
    fake.seed("storeops", "shifts", [])
    fake.seed("storeops", "timelog", [])
    fake.seed("storeops", "manual_hours", [])
    fake.seed("storeops", "hours_budget", [])
    fake.seed("storeops", "payroll_change_log", [])


reset()
# Priya: a Schedule-created shift (NUMERIC id "45") for 5 weekdays, 8h/day scheduled — the Schedule
# page's OWN payload shape (schedule/page.tsx line 294: employee_id: emp?.id?.toString()).
fake.store[("storeops", "shifts")] = [
    {"id": i + 1, "org_id": ORG, "employee_id": "45", "employee_name": "Priya Rep", "store_code": "S1",
     "shift_date": f"2026-07-{6 + i:02d}", "scheduled_hours": 8.0, "actual_hours": 0.0,
     "start_time": "09:00", "end_time": "17:00", "status": "scheduled", "is_deleted": False}
    for i in range(5)
]
# ...and REAL kiosk punches (BUSINESS id "E45") on those SAME 5 days, ~8.5h each (a little over).
fake.store[("storeops", "timelog")] = [
    {"id": f"t{i+1}", "org_id": ORG, "employee_id": "E45", "employee_name": "Priya Rep", "store_code": "S1",
     "clock_in": f"2026-07-{6 + i:02d}T13:00:00", "clock_out": f"2026-07-{6 + i:02d}T21:30:00",
     "hours": 8.5, "work_date": f"2026-07-{6 + i:02d}", "device": "kiosk", "created_at": f"2026-07-{6+i:02d}T21:30:01"}
    for i in range(5)
]

rows = R.get_payroll(start="2026-07-06", end="2026-07-10", authorization=AUTH, org_id=ORG)
priya_rows = [r for r in rows if r.get("name") == "Priya Rep"]
check("B1 THE REPORTED BUG IS FIXED: exactly ONE row for Priya (was 2 — one scheduled, one actual, "
      "same name) on the SHIPPED /payroll handler", len(priya_rows) == 1, rows)
p = priya_rows[0] if priya_rows else {}
check("B2 merged row employee_id is the canonical business id E45", p.get("employee_id") == "E45", p)
check("B3 merged row shows the REAL pay rate ($22/hr, not $0)", p.get("pay_rate") == 22.0, p)
check("B4 merged scheduled_hours = 40 (5 * 8h, the shift-derived contribution)",
      p.get("scheduled_hours") == 40.0, p)
check("B5 merged actual_hours = 82.5 (40h scheduled-fallback from the shift bucket + 42.5h real "
      "punches from the timelog bucket, additively — the SAME number /payroll's own (bug-preserving) "
      "aggregation already produces today; NOT independently recomputed by the merge)",
      p.get("actual_hours") == 82.5, p)
check("B6 no OTHER Priya row leaked through with a different/stale employee_id",
      not any(r.get("employee_id") == "45" for r in rows), rows)

# ── B7: disabling the merge (monkeypatch back to identity) reproduces the ORIGINAL bug exactly —
# proves the fix is actually doing something, not a no-op that happened to pass.
_real_reconcile = R._reconcile_employee_identity
R._reconcile_employee_identity = lambda rows, employees: rows
rows_unfixed = R.get_payroll(start="2026-07-06", end="2026-07-10", authorization=AUTH, org_id=ORG)
R._reconcile_employee_identity = _real_reconcile
priya_unfixed = [r for r in rows_unfixed if r.get("name") == "Priya Rep"]
check("B7 WITHOUT the fix, the bug reproduces exactly as reported: 2 rows for Priya",
      len(priya_unfixed) == 2, rows_unfixed)
check("B7b ...one shows scheduled hours at $0/hr (the numeric-id-keyed shift bucket)",
      any(r.get("employee_id") == "45" and r.get("pay_rate") == 0.0 and r.get("scheduled_hours") == 40.0
          for r in priya_unfixed), priya_unfixed)
check("B7c ...the other shows real actual hours at the correct rate (the business-id-keyed timelog "
      "bucket)", any(r.get("employee_id") == "E45" and r.get("pay_rate") == 22.0
                     and r.get("actual_hours") == 42.5 for r in priya_unfixed), priya_unfixed)

# ── B8: GRAND TOTALS across the whole response are byte-identical fixed vs unfixed (money proof at
# the router/HTTP-shape level, not just the pure-function level in Section A).
def totals(rr):
    return (round(sum(r.get("scheduled_hours") or 0 for r in rr), 2),
            round(sum(r.get("actual_hours") or 0 for r in rr), 2),
            round(sum(r.get("scheduled_pay") or 0 for r in rr), 2),
            round(sum(r.get("actual_pay") or 0 for r in rr), 2))
check("B8 grand totals (hours AND dollars) byte-identical fixed vs unfixed at the router level",
      totals(rows) == totals(rows_unfixed), (totals(rows), totals(rows_unfixed)))

# ── B9: a genuinely single-source employee (kiosk-only, Solo Kiosk) is completely unaffected.
solo_rows = [r for r in rows if r.get("name") == "Solo Kiosk"]
check("B9 an employee with no shift/timelog id-mismatch is untouched (0 rows here — no data seeded, "
      "confirms the merge didn't fabricate one)", len(solo_rows) == 0, solo_rows)

print(f"[Section B] {len(PASS)} passed, {len(FAIL)} failed so far")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION C — GET /payroll/actual-hours-detail: reconciles EXACTLY to the merged row's total,
# including the double-count artifact (surfaced explicitly, not hidden), plus edited/manual markers.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
detail = R.payroll_actual_hours_detail(employee_id="E45", start="2026-07-06", end="2026-07-10",
                                        authorization=AUTH, org_id=ORG)
check("C1 drill-down resolves the employee via id VARIANTS (numeric shift id 45 -> business E45)",
      detail["employee_id"] == "E45" and detail["name"] == "Priya Rep", detail)
check("C2 drill-down total_actual_hours reconciles EXACTLY to the merged /payroll row (82.5h, "
      "SAME number Section B's row shows — a genuine explanation, not a different total)",
      detail["total_actual_hours"] == 82.5, detail["total_actual_hours"])
check("C3 5 day rows returned (one per shift/punch day)", len(detail["days"]) == 5, detail["days"])
check("C4 EVERY day is flagged double_counted (id-mismatch defeats /payroll's own dedup for all 5 "
      "shift+punch days) with a plain-language note",
      all(d["double_counted"] and d["note"] for d in detail["days"]), detail["days"])
d0 = detail["days"][0]
check("C5 day subtotal = shift's 8h (scheduled fallback) + punch's 8.5h = 16.5h, matching the note",
      d0["actual_hours"] == 16.5, d0)
check("C6 day's own scheduled_hours = 8 (from the shift)", d0["scheduled_hours"] == 8.0, d0)
check("C7 the day's shift sub-object is present with start/end times", d0["shift"]["start_time"] == "09:00", d0)
check("C8 the day's punch is present, marked counted (no live matching-key shift blocked it)",
      len(d0["punches"]) == 1 and d0["punches"][0]["counted"] is True, d0["punches"])
check("C9 sum of ALL day subtotals == the endpoint's own total_actual_hours (internal consistency)",
      round(sum(d["actual_hours"] for d in detail["days"]), 2) == detail["total_actual_hours"],
      (detail["days"], detail["total_actual_hours"]))

# ── C10: an INACTIVE employee's PHANTOM (never-worked) shift correctly contributes ZERO, matching
# /payroll's own inactive-phantom-drop rule — the drill-down must not "invent" hours for a ghost row.
fake.seed("storeops", "employees", fake.store[("storeops", "employees")] + [
    {"id": 99, "org_id": ORG, "employee_id": "E99", "name": "Ghost Former", "home_store": "S1",
     "pay_rate": 15.0, "is_active": False},
])
fake.store[("storeops", "shifts")] = fake.store[("storeops", "shifts")] + [
    {"id": 900, "org_id": ORG, "employee_id": "E99", "employee_name": "Ghost Former", "store_code": "S1",
     "shift_date": "2026-07-07", "scheduled_hours": 6.0, "actual_hours": 0.0,
     "start_time": "09:00", "end_time": "15:00", "status": "scheduled", "is_deleted": False},
]
ghost_detail = R.payroll_actual_hours_detail(employee_id="E99", start="2026-07-06", end="2026-07-10",
                                              authorization=AUTH, org_id=ORG)
check("C10 inactive employee's phantom-only shift contributes 0 actual AND 0 scheduled hours "
      "(matches /payroll's own phantom-drop rule, not the active-path fallback)",
      ghost_detail["total_actual_hours"] == 0.0 and ghost_detail["total_scheduled_hours"] == 0.0,
      ghost_detail)
check("C10b ...but the shift is still SHOWN (transparency), just marked not counted",
      ghost_detail["days"][0]["shift"]["counted"] is False, ghost_detail["days"])

print(f"[Section C] {len(PASS)} passed, {len(FAIL)} failed so far")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION D — GET /payroll/over-hours: per-(store,week) actual-vs-budget, reusing
# storeops.hours_budget (migration 087) — no new config table. Display-only.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
fake.seed("storeops", "hours_budget", [{"org_id": ORG, "store_code": "S1", "weekly_hours": 78.0}])
over = R.payroll_over_hours(start="2026-07-06", end="2026-07-10", authorization=AUTH, org_id=ORG)
check("D1 exactly 1 week bucket returned for a 5-day range fully inside one work-week", len(over["weeks"]) == 1, over)
wk = over["weeks"][0]
check("D2 store S1's actual hours for the week = 82.5 (Priya, only employee with hours this range) "
      "+ Solo Kiosk's 0 = 82.5", wk["actual_hours"] == 82.5, wk)
check("D3 weekly_hours_limit read straight from storeops.hours_budget (78.0, reused config)",
      wk["weekly_hours_limit"] == 78.0, wk)
check("D4 store flagged OVER (82.5 > 78)", wk["over"] is True, wk)
check("D5 Priya individually flagged over_alone (her 82.5h alone already exceeds the 78h store budget)",
      any(e["employee_id"] == "E45" and e["over_alone"] is True for e in wk["employees"]), wk["employees"])

# A store with NO budget configured is never flagged (default NULL = no limit).
fake.seed("storeops", "hours_budget", [])
over_nobudget = R.payroll_over_hours(start="2026-07-06", end="2026-07-10", authorization=AUTH, org_id=ORG)
check("D6 no hours_budget configured -> weekly_hours_limit is None, over is False (never flagged)",
      over_nobudget["weeks"][0]["weekly_hours_limit"] is None and over_nobudget["weeks"][0]["over"] is False,
      over_nobudget)
fake.seed("storeops", "hours_budget", [{"org_id": ORG, "store_code": "S1", "weekly_hours": 78.0}])

print(f"[Section D] {len(PASS)} passed, {len(FAIL)} failed so far")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION E — MANUAL-EDIT AUDIT LOG (migration 414, storeops.payroll_change_log): every write path
# that alters punches/hours logs a row; GET /payroll-change-log lists it, org+store scoped.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def log_rows():
    return fake.store.get(("storeops", "payroll_change_log"), [])


# ── E1: PATCH /shifts/{id} (a DM correction) logs a before/after diff per changed field ═══════════
before_count = len(log_rows())
shift_id = fake.store[("storeops", "shifts")][0]["id"]
R.update_shift(shift_id, {"scheduled_hours": 6.0, "notes": "DM shortened the shift"},
               authorization=AUTH, org_id=ORG)
new_logs = log_rows()[before_count:]
sched_change = [r for r in new_logs if r["field"] == "scheduled_hours"]
check("E1a shift edit logs the scheduled_hours field change (8.0 -> 6.0)",
      len(sched_change) == 1 and sched_change[0]["before_value"] == "8.0" and sched_change[0]["after_value"] == "6.0",
      sched_change)
check("E1b logged entry_point is 'shift_edit', source_table 'shifts', source_id the shift's id",
      sched_change[0]["entry_point"] == "shift_edit" and sched_change[0]["source_table"] == "shifts"
      and sched_change[0]["source_id"] == str(shift_id), sched_change)
check("E1c logged changed_by is the ACTUAL caller (district manager's email/role), not a placeholder",
      sched_change[0]["changed_by_email"] == "dm@luxelink.example"
      and sched_change[0]["changed_by_role"] == "district_manager", sched_change)
check("E1d editing a field NOT in the update body (e.g. store_code) does NOT spuriously log",
      not any(r["field"] == "store_code" for r in new_logs), new_logs)
# untouched field (notes) is intentionally not in the logged-fields allowlist -> no log row for it
check("E1e only HOUR-RELEVANT fields are logged (notes change alone would not appear); exactly 1 "
      "field actually changed here (scheduled_hours)", len(new_logs) == 1, new_logs)

# ── E2: POST/DELETE /manual-hours (DM manual adjustment) ═══════════════════════════════════════════
before_count = len(log_rows())
mh = R.add_manual_hours({"employee_id": "E46", "hours": 3.5, "reason": "forgot to clock in",
                         "work_date": "2026-07-08"}, authorization=AUTH, org_id=ORG)
add_logs = log_rows()[before_count:]
check("E2a manual-hours ADD logs entry_point manual_hours_add, after_value 3.5",
      len(add_logs) == 1 and add_logs[0]["entry_point"] == "manual_hours_add"
      and add_logs[0]["after_value"] == "3.5" and add_logs[0]["before_value"] is None, add_logs)

before_count = len(log_rows())
R.delete_manual_hours(mh["id"], authorization=AUTH, org_id=ORG)
del_logs = log_rows()[before_count:]
check("E2b manual-hours DELETE logs entry_point manual_hours_delete with the REMOVED value as "
      "before_value (3.5 -> None)", len(del_logs) == 1 and del_logs[0]["entry_point"] == "manual_hours_delete"
      and del_logs[0]["before_value"] == "3.5" and del_logs[0]["after_value"] is None, del_logs)

# ── E3: POST /timeclock/override (manager clocks an employee in at an unscheduled store) ══════════
before_count = len(log_rows())
ov = R.clock_in_override({"employee_id": "E46", "store_code": "S2"}, authorization=AUTH, org_id=ORG)
check("E3 override call itself succeeds", ov.get("success") is True, ov)
ov_logs = log_rows()[before_count:]
check("E3a override logs BOTH the shift_added (unscheduled shift on record) and the clock_in itself",
      {"shift_added", "clock_in"} <= {r["field"] for r in ov_logs}, ov_logs)
check("E3b override logs entry_point 'timeclock_override' and the manager's identity",
      all(r["entry_point"] == "timeclock_override" for r in ov_logs)
      and all(r["changed_by_email"] == "dm@luxelink.example" for r in ov_logs), ov_logs)
# clean up the open punch so later force-clockout checks below start from a clean slate for E46
fake.store[("storeops", "timelog")] = [t for t in fake.store[("storeops", "timelog")]
                                        if not (t.get("employee_id") == "E46" and t.get("clock_out") is None)]

# ── E4: force-clockout — manual "run now" (DM-triggered) vs the unattended cron sweep are
# distinguishable entry_points, and BOTH log the auto-stamped clock-out. ═══════════════════════════
_biz_tz = R._biz_tz_for(ORG)
work_today = datetime.now(timezone.utc).astimezone(_biz_tz).date().isoformat()
fake.store[("storeops", "shifts")] = fake.store[("storeops", "shifts")] + [
    {"id": 950, "org_id": ORG, "employee_id": "E46", "employee_name": "Solo Kiosk", "store_code": "S1",
     "shift_date": work_today, "scheduled_hours": 8.0, "actual_hours": 0.0,
     "start_time": "00:00", "end_time": "00:05", "status": "scheduled", "is_deleted": False},
]
fake.store[("storeops", "timelog")] = fake.store[("storeops", "timelog")] + [
    {"id": "openpunch1", "org_id": ORG, "employee_id": "E46", "employee_name": "Solo Kiosk",
     "store_code": "S1", "clock_in": datetime.now(timezone.utc).isoformat(), "clock_out": None,
     "hours": None, "work_date": work_today},
]
before_count = len(log_rows())
manual_run = R.force_clockout_run_now(authorization=AUTH, org_id=ORG)
manual_logs = log_rows()[before_count:]
check("E4a manual 'run now' force-clockout closes the overdue punch and logs entry_point "
      "'force_clockout_manual' attributed to the DM who clicked it",
      manual_run["closed"] == 1 and len(manual_logs) == 1
      and manual_logs[0]["entry_point"] == "force_clockout_manual"
      and manual_logs[0]["changed_by_email"] == "dm@luxelink.example", (manual_run, manual_logs))

# a second overdue punch, closed via the unattended pg_cron sweep this time
fake.store[("storeops", "timelog")] = fake.store[("storeops", "timelog")] + [
    {"id": "openpunch2", "org_id": ORG, "employee_id": "E46", "employee_name": "Solo Kiosk",
     "store_code": "S1", "clock_in": datetime.now(timezone.utc).isoformat(), "clock_out": None,
     "hours": None, "work_date": work_today},
]
before_count = len(log_rows())
from app.core.config import settings as _settings
_settings.NOTIFY_RUN_SECRET = "test-secret"
cron_run = R.force_clockout_run_due(x_notify_secret="test-secret")
cron_logs = log_rows()[before_count:]
check("E4b the unattended pg_cron sweep logs entry_point 'force_clockout_cron' with changed_by "
      "'system' (never a real person's identity for an unattended trigger)",
      cron_run["closed"] == 1 and len(cron_logs) == 1
      and cron_logs[0]["entry_point"] == "force_clockout_cron" and cron_logs[0]["changed_by_email"] == "system",
      (cron_run, cron_logs))

# ── E5: GET /payroll-change-log — lists everything so far, org+store scoped, filterable ═══════════
all_log = R.payroll_change_log(authorization=AUTH, org_id=ORG)
check("E5a change-log endpoint returns available:true once seeded (migration 414 'applied')",
      all_log["available"] is True and len(all_log["items"]) >= 6, all_log["items"])
by_emp = R.payroll_change_log(employee_id="E46", authorization=AUTH, org_id=ORG)
check("E5b employee_id filter narrows to only E46's entries",
      all(r["employee_id"] == "E46" for r in by_emp["items"]) and len(by_emp["items"]) >= 5, by_emp["items"])
by_entry = R.payroll_change_log(entry_point="shift_edit", authorization=AUTH, org_id=ORG)
check("E5c entry_point filter narrows to only shift_edit rows",
      all(r["entry_point"] == "shift_edit" for r in by_entry["items"]) and len(by_entry["items"]) == 1,
      by_entry["items"])

# org isolation: a second tenant's log must never leak into ORG's list
fake.seed("storeops", "tenants", fake.store[("storeops", "tenants")] + [{"org_id": "org-pm-OTHER"}])
fake.store.setdefault(("storeops", "payroll_change_log"), []).append(
    {"id": "leak1", "org_id": "org-pm-OTHER", "employee_id": "EX", "field": "manual_hours",
     "entry_point": "manual_hours_add", "changed_by_email": "other@tenant.example",
     "created_at": datetime.now(timezone.utc).isoformat()})
other_org_log = R.payroll_change_log(authorization=AUTH, org_id=ORG)
check("E5d a DIFFERENT tenant's log row never leaks into this org's change-log list",
      not any(r.get("id") == "leak1" for r in other_org_log["items"]), other_org_log["items"])

# ── E6: degrade-safe pre-migration-414 (table missing entirely) — writes still succeed, log GET
# degrades to available:false instead of a 500.
del fake.store[("storeops", "payroll_change_log")]

class _NoTableSchema(FakeSchema):
    def table(self, t):
        if t == "payroll_change_log":
            raise RuntimeError('relation "storeops.payroll_change_log" does not exist')
        return super().table(t)


_orig_schema = fake.schema
fake.schema = lambda name: _NoTableSchema(fake, name)
try:
    r_predeploy = R.update_shift(shift_id, {"scheduled_hours": 7.0}, authorization=AUTH, org_id=ORG)
    check("E6a shift edit STILL SUCCEEDS when payroll_change_log doesn't exist yet (pre-migration-414)",
          r_predeploy.get("scheduled_hours") == 7.0, r_predeploy)
    predeploy_log = R.payroll_change_log(authorization=AUTH, org_id=ORG)
    check("E6b GET /payroll-change-log degrades to available:false (never a 500) pre-migration",
          predeploy_log == {"items": [], "available": False}, predeploy_log)
finally:
    fake.schema = _orig_schema

print(f"[Section E] {len(PASS)} passed, {len(FAIL)} failed so far")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
