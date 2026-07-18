"""Integration-style proof for the payroll data-flow fix (mod-people, 2026-07-18 luxelink payroll
audit). Runs the ACTUAL shipped functions from app.modules.storeops.router (get_payroll,
get_payroll_by_store, payroll_raw) against an in-memory fake Supabase client — no live DB/network.
Run: `python3 harness_payroll_data_flow.py` from backend/.

BACKGROUND: a tenant (luxelink) with real employees/shifts/rates in the platform saw an EMPTY
Payroll Report / Payroll-with-Tax page. Root cause: /payroll (+/payroll-by-store) sourced hours
EXCLUSIVELY from `shifts` (schedule) with zero fallback to real `timelog` clock punches when no
shift row exists that day; /payroll-raw sourced hours EXCLUSIVELY from `timelog`+`manual_hours`
with zero fallback to `shifts.scheduled_hours` when an employee has never clocked in. A tenant that
tracks hours purely via the kiosk (no formal Schedule usage) vanished from Payroll Report; a
tenant/employee whose schedule is entered but who hasn't clocked in yet (or doesn't use the kiosk)
vanished from Payroll-with-Tax — despite the underlying data genuinely existing in the platform.

FIX (additive-only, in router.py): each endpoint now also reads the OTHER data source and adds a
row/hours contribution ONLY for the days/employees that are otherwise completely unrepresented —
never touching a day/employee that already has data through its existing source. This guarantees
Boost (whose reps both schedule shifts AND clock in) is byte-identical, while a tenant relying on
just one of the two sources now actually sees their payroll.

Proves:
  1-4.   /payroll: existing dominant-store attribution + totals math is UNCHANGED (regression guard
         for the rule5-wave1 package this fix sits on top of).
  5-7.   /payroll: the NEW timelog-only fallback — an employee with zero shift rows this month but
         real closed clock punches now appears, hours-only (no schedule fabricated); a day already
         covered by a shift is never double-counted; an inactive/no-employees-row clock-in still
         gets a name via `timelog.employee_name`.
  8-9.   /payroll-by-store: the same fallback at the store-aggregate level, incl. no-double-count.
  10-12. /payroll-raw: existing dominant-clocked-store attribution + manual-hours-only fallback is
         UNCHANGED (regression guard).
  13-16. /payroll-raw: the NEW scheduled-hours fallback — a zero-clocked-and-zero-manual employee
         with real shift hours in range now appears (basis="scheduled"); an employee with real
         clocked/manual hours is COMPLETELY untouched (basis stays "clocked", numbers identical to
         before the fix) even if they also happen to have shifts in range; an employee with neither
         clocked/manual NOR shift hours stays correctly excluded (genuinely no data, not fabricated).
  17.    Org isolation: a second tenant's shift-only employee never leaks into org 1's /payroll-raw
         scheduled-fallback rows.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (eq/gte/lte/lt/in_/is_/order/limit filters + select/insert) ─────────────
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._limit = None
        self._mode = "select"
        self._payload = None
        self._order_desc = False

    def select(self, cols=None):
        self._mode = "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(vals))); return self

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def order(self, *a, **k):
        self._order_desc = k.get("desc", False); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def delete(self):
        self._mode = "delete"; return self

    def _match(self, row):
        for op, k, v in self.filters:
            rv = row.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
            if op == "in" and rv not in v:
                return False
            if op == "is":
                if v == "null" and rv is not None:
                    return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._mode == "select":
            matched = [r for r in rows if self._match(r)]
            if self._order_desc:
                matched = list(reversed(matched))
            if self._limit:
                matched = matched[: self._limit]
            return FakeResult(matched)
        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payloads:
                row = dict(p)
                row.setdefault("id", f"{self.table_name}-{len(rows)}")
                rows.append(row)
                out.append(row)
            return FakeResult(out)
        raise RuntimeError(f"unsupported mode {self._mode} in this harness")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeSchemaClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return self   # single flat store — schema name ignored, matches FakeQuery's table keying

    def table(self, name):
        return FakeQuery(self.store, name)


STORE = {}
FAKE_CLIENT = FakeSchemaClient(STORE)


def fake_get_supabase():
    return FAKE_CLIENT


import app.modules.storeops.router as router_mod   # noqa: E402

router_mod.get_supabase = fake_get_supabase

ORG = "ORG1"
ORG2 = "ORG2"   # a second tenant, to prove isolation

STORE["employees"] = [
    {"id": 1, "employee_id": "E1", "org_id": ORG, "name": "Alice Rep", "home_store": "Store1", "pay_rate": 20.0, "is_active": True},
    {"id": 2, "employee_id": "E2", "org_id": ORG, "name": "Bob Floater", "home_store": "Store1", "pay_rate": 25.0, "is_active": True},
    {"id": 3, "employee_id": "E3", "org_id": ORG, "name": "Cara Nostore", "home_store": "", "pay_rate": 18.0, "is_active": True},
    {"id": 4, "employee_id": "E4", "org_id": ORG, "name": "Dana Kiosk", "home_store": "Store2", "pay_rate": 22.0, "is_active": True},
    {"id": 5, "employee_id": "E5", "org_id": ORG, "name": "Evan Scheduled", "home_store": "Store1", "pay_rate": 19.0, "is_active": True},
    {"id": 6, "employee_id": "E6", "org_id": ORG, "name": "Fay BothSources", "home_store": "Store1", "pay_rate": 21.0, "is_active": True},
    {"id": 7, "employee_id": "E7", "org_id": ORG, "name": "Gus Nothing", "home_store": "Store1", "pay_rate": 17.0, "is_active": True},
    # ORG2 (isolation control)
    {"id": 8, "employee_id": "X1", "org_id": ORG2, "name": "Other Org", "home_store": "OStore", "pay_rate": 15.0, "is_active": True},
]
STORE["shifts"] = [
    # E1: single-store, one shift, no actual_hours recorded -> scheduled fallback (regression)
    {"org_id": ORG, "employee_id": "E1", "employee_name": "Alice Rep", "store_code": "Store1",
     "shift_date": "2026-07-05", "scheduled_hours": 8, "actual_hours": 0, "is_deleted": False},
    # E2: floater, 2 shifts on 2 different stores -> dominant = whichever has more combined hours
    {"org_id": ORG, "employee_id": "E2", "employee_name": "Bob Floater", "store_code": "Store1",
     "shift_date": "2026-07-06", "scheduled_hours": 4, "actual_hours": 4, "is_deleted": False},
    {"org_id": ORG, "employee_id": "E2", "employee_name": "Bob Floater", "store_code": "Store2",
     "shift_date": "2026-07-07", "scheduled_hours": 10, "actual_hours": 10, "is_deleted": False},
    # E3: shift with NO store_code -> falls back to home_store (empty here, proving "" fallback)
    {"org_id": ORG, "employee_id": "E3", "employee_name": "Cara Nostore", "store_code": "",
     "shift_date": "2026-07-05", "scheduled_hours": 6, "actual_hours": 0, "is_deleted": False},
    # E5: has a schedule (used by /payroll-raw's new scheduled-fallback case) but never clocks in
    {"org_id": ORG, "employee_id": "E5", "employee_name": "Evan Scheduled", "store_code": "Store1",
     "shift_date": "2026-07-08", "scheduled_hours": 5, "actual_hours": 0, "is_deleted": False},
    {"org_id": ORG, "employee_id": "E5", "employee_name": "Evan Scheduled", "store_code": "Store1",
     "shift_date": "2026-07-09", "scheduled_hours": 5, "actual_hours": 0, "is_deleted": False},
    # E6: has BOTH a shift on 07-10 AND a same-day clock punch -> must NOT double count that day
    {"org_id": ORG, "employee_id": "E6", "employee_name": "Fay BothSources", "store_code": "Store1",
     "shift_date": "2026-07-10", "scheduled_hours": 8, "actual_hours": 8, "is_deleted": False},
    # ORG2 (isolation control): a shift-only employee in a DIFFERENT tenant
    {"org_id": ORG2, "employee_id": "X1", "employee_name": "Other Org", "store_code": "OStore",
     "shift_date": "2026-07-05", "scheduled_hours": 8, "actual_hours": 0, "is_deleted": False},
]
STORE["timelog"] = [
    # E4: KIOSK-ONLY employee — NO shift row at all this month, only real clock punches.
    {"org_id": ORG, "employee_id": "E4", "employee_name": "Dana Kiosk", "store_code": "Store2",
     "clock_in": "2026-07-11T13:00:00+00:00", "clock_out": "2026-07-11T17:00:00+00:00",
     "hours": 4.0, "work_date": "2026-07-11"},
    {"org_id": ORG, "employee_id": "E4", "employee_name": "Dana Kiosk", "store_code": "Store2",
     "clock_in": "2026-07-12T13:00:00+00:00", "clock_out": "2026-07-12T18:00:00+00:00",
     "hours": 5.0, "work_date": "2026-07-12"},
    # E6: a clock punch on the SAME day as its shift (07-10) -> the shift already covers that day
    {"org_id": ORG, "employee_id": "E6", "employee_name": "Fay BothSources", "store_code": "Store1",
     "clock_in": "2026-07-10T13:00:00+00:00", "clock_out": "2026-07-10T21:00:00+00:00",
     "hours": 8.0, "work_date": "2026-07-10"},
    # An OPEN punch (no clock_out) must never count anywhere.
    {"org_id": ORG, "employee_id": "E4", "employee_name": "Dana Kiosk", "store_code": "Store2",
     "clock_in": "2026-07-20T13:00:00+00:00", "clock_out": None, "hours": None, "work_date": "2026-07-20"},
]
STORE["manual_hours"] = []


# ══════════════════════════════════════════════════════════════════════════════════════════════
# GET /payroll
# ══════════════════════════════════════════════════════════════════════════════════════════════
payroll = {r["employee_id"]: r for r in router_mod.get_payroll(month="2026-07", org_id=ORG)}

# 1-4: regression — existing behavior on employees whose hours are already schedule-tracked
check("1: E1 scheduled fallback unchanged (act==0 -> act=sched)",
      payroll["E1"]["scheduled_hours"] == 8 and payroll["E1"]["actual_hours"] == 8, payroll.get("E1"))
check("2: E2 floater store = Store2 (dominant by combined hours: 10 > 4)",
      payroll["E2"]["store"] == "Store2", payroll.get("E2"))
check("3: E2 totals unaffected by attribution (14h either way)",
      payroll["E2"]["scheduled_hours"] == 14 and payroll["E2"]["actual_hours"] == 14, payroll.get("E2"))
check("4: E3 no-store-code shift falls back to home_store (empty string)",
      payroll["E3"]["store"] == "", payroll.get("E3"))

# 5-7: NEW timelog-only fallback
check("5: E4 (kiosk-only, zero shifts) now APPEARS in /payroll (used to be entirely absent)",
      "E4" in payroll, list(payroll.keys()))
check("6: E4's hours come from real clock punches only (4+5=9h), no schedule fabricated",
      payroll["E4"]["actual_hours"] == 9 and payroll["E4"]["scheduled_hours"] == 0, payroll.get("E4"))
check("7: E4's open (not-yet-clocked-out) punch on 07-20 is excluded (only closed punches count)",
      payroll["E4"]["actual_hours"] == 9, payroll.get("E4"))
check("7b: E4's store = Store2 (from the clock punches) and name comes from timelog.employee_name",
      payroll["E4"]["store"] == "Store2" and payroll["E4"]["name"] == "Dana Kiosk", payroll.get("E4"))
check("8: E6 (shift AND same-day clock punch) is NOT double-counted — stays at the shift's 8h",
      payroll["E6"]["actual_hours"] == 8 and payroll["E6"]["scheduled_hours"] == 8, payroll.get("E6"))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# GET /payroll-by-store
# ══════════════════════════════════════════════════════════════════════════════════════════════
by_store = {r["store_code"]: r for r in router_mod.get_payroll_by_store(month="2026-07", org_id=ORG)["stores"]}

check("9: Store2 picks up E4's kiosk-only hours (E2's shift 10h*$25=$250 + E4's clock-only 9h*$22=$198 "
      "= 19h / $448) even though E4 has ZERO shift rows at Store2",
      by_store.get("Store2", {}).get("hours") == 19 and abs(by_store.get("Store2", {}).get("amount", 0) - 448.0) < 0.01,
      by_store.get("Store2"))
check("10: Store1 total does NOT double-count E6's same-day shift+punch (8h from the shift only)",
      by_store.get("Store1", {}).get("hours") ==
      (8 +          # E1
       4 +          # E2 (its Store1 shift)
       0 +          # E3 (no store_code -> excluded from by-store)
       5 + 5 +      # E5's 2 shifts
       8),          # E6 (once, not 16)
      by_store.get("Store1"))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# GET /payroll-raw
# ══════════════════════════════════════════════════════════════════════════════════════════════
raw = {r["employee_id"]: r for r in router_mod.payroll_raw(start="2026-07-01", end="2026-07-31", org_id=ORG)["rows"]}

# 10-12 (renumbered 11-13 below to avoid clashing with the by-store block's own #9/#10): regression
check("11: E4's dominant CLOCKED store (Store2) attribution unchanged, basis='clocked'",
      raw["E4"]["store"] == "Store2" and raw["E4"]["basis"] == "clocked" and raw["E4"]["total_hours"] == 9,
      raw.get("E4"))

STORE["manual_hours"].append({"org_id": ORG, "employee_id": "E1", "hours": 2.0, "work_date": "2026-07-05"})
STORE["employees"][0]["home_store"] = "Store1"   # unchanged, just documenting the fallback path
raw2 = {r["employee_id"]: r for r in router_mod.payroll_raw(start="2026-07-01", end="2026-07-31", org_id=ORG)["rows"]}
check("12: E1 with real clocked hours is untouched by the scheduled-fallback (basis stays 'clocked')",
      raw2["E1"]["basis"] == "clocked" and raw2["E1"]["clocked_hours"] == 0 and raw2["E1"]["manual_hours"] == 2.0,
      raw2.get("E1"))
check("13: manual-hours-only (no timelog) still falls back to home_store, as before",
      raw2["E1"]["store"] == "Store1", raw2.get("E1"))

# 13-16: NEW scheduled-hours fallback
check("14: E5 (zero clocked, zero manual, HAS a schedule) now APPEARS with basis='scheduled'",
      "E5" in raw2 and raw2["E5"]["basis"] == "scheduled", raw2.get("E5"))
check("15: E5's total_hours = summed SCHEDULED hours (5+5=10), store = the scheduled store",
      raw2["E5"]["total_hours"] == 10 and raw2["E5"]["store"] == "Store1", raw2.get("E5"))
check("16: E6 has real clocked hours (8h on 07-10) -> completely untouched by the scheduled fallback "
      "even though it ALSO has a shift in range (basis stays 'clocked', no blending)",
      raw2["E6"]["basis"] == "clocked" and raw2["E6"]["total_hours"] == 8, raw2.get("E6"))
check("17: E7 (zero clocked, zero manual, zero shifts) stays correctly EXCLUDED — genuinely no data",
      "E7" not in raw2, list(raw2.keys()))

# 18: org isolation — ORG2's shift-only employee never leaks into ORG's scheduled-fallback rows
check("18: ORG2's X1 (shift-only) never appears in ORG's /payroll-raw", "X1" not in raw2, list(raw2.keys()))
raw_org2 = {r["employee_id"]: r for r in router_mod.payroll_raw(start="2026-07-01", end="2026-07-31", org_id=ORG2)["rows"]}
check("19: ORG2's own /payroll-raw call DOES see X1 via the same scheduled-fallback (org-scoped correctly)",
      "X1" in raw_org2 and raw_org2["X1"]["basis"] == "scheduled" and raw_org2["X1"]["total_hours"] == 8,
      raw_org2.get("X1"))


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
