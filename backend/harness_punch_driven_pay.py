"""Proof for PUNCH-DRIVEN PAY (owner-approved pay-model change, 2026-08-24).

Runs the ACTUAL shipped get_payroll + payroll_actual_hours_detail from app.modules.storeops.router
against an in-memory fake Supabase client (no live DB / network). The fake client has no `.rpc`, so
_payroll_month_groups degrades to the LEGACY Python path — which now carries the same punch-driven
logic migration 914 puts in the RPC. (Legacy==RPC byte-identical for these scenarios is separately
proven by harness_payroll_rpc_equivalence: E2 = punch-driven, E6 = manual-precedence, on BOTH paths.)
Run: `python3 harness_punch_driven_pay.py` from backend/.

THE CHANGE: when a rep has a CLOSED punch (timelog.hours NOT NULL) on a (employee_id, work_date), the
PUNCH hours are AUTHORITATIVE for pay that day, overriding scheduled_hours. No closed punch -> pay the
schedule, as before. Open/forgot-to-clock-out punches (hours NULL) are NOT a punch here.

PRECEDENCE (money-critical constraint):  manual correction  >  closed punch  >  scheduled_hours.
A MANUAL correction is a human-set shifts.actual_hours (>0). The ONLY writer of shifts.actual_hours is
the DM edit path (PATCH /storeops/shifts, logged to storeops.payroll_change_log); nothing auto-fills it
from a punch. So actual_hours>0 == a manual correction and MUST win over the raw punch — a DM who fixed
a forgotten punch is not overwritten by a partial/again-forgotten punch.

Proves, on ONE active employee, across a two-week period (each day is a distinct scenario):
  A. SCHEDULED day (sched 6.3, act 0) + 6.6h closed punch  -> pays 6.6h  (PUNCH-DRIVEN; was 6.3).
  B. SCHEDULED day (sched 6.0, act 0) + NO punch           -> pays 6.0h  (schedule fallback, unchanged).
  C. MANUAL-CORRECTION day (sched 6.3, act 7.0) + 6.6 punch-> pays 7.0h  (MANUAL WINS — punch ignored).
  D. FORGOT-TO-CLOCK-OUT: scheduled day (sched 5.0) + OPEN punch (hours NULL) -> pays 5.0h (open punch
     is not a punch -> schedule fallback).
  E. UNSCHEDULED zero-hour shell (sched 0, act 0) + 6.5 punch -> pays 6.5h (PR #74 case, preserved).
  F. NO DOUBLE COUNT: the employee total = 6.6+6.0+7.0+5.0+6.5 = 31.1 (each day counted once).
  G. Drill-down (payroll_actual_hours_detail) reconciles EXACTLY to get_payroll, with honest per-day
     `counted` flags: the manual day's punch counted=False (manual won), each punch-driven day's punch
     counted=True, the manual/scheduled shift on those days counted honestly.
"""
import sys
sys.path.insert(0, ".")

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.table_name = store, table
        self.filters, self._limit = [], None
        self._mode, self._payload, self._order_desc = "select", None, False
    def select(self, cols=None): self._mode = "select"; return self
    def eq(self, k, v): self.filters.append(("eq", k, v)); return self
    def gte(self, k, v): self.filters.append(("gte", k, v)); return self
    def lte(self, k, v): self.filters.append(("lte", k, v)); return self
    def lt(self, k, v): self.filters.append(("lt", k, v)); return self
    def in_(self, k, vals): self.filters.append(("in", k, set(str(x) for x in vals))); return self
    def is_(self, k, v): self.filters.append(("is", k, v)); return self
    def order(self, *a, **k): self._order_desc = k.get("desc", False); return self
    def limit(self, n): self._limit = n; return self
    def insert(self, payload): self._mode = "insert"; self._payload = payload; return self
    def _match(self, row):
        for op, k, v in self.filters:
            rv = row.get(k)
            if op == "eq" and rv != v: return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)): return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)): return False
            if op == "in" and str(rv) not in v: return False
            if op == "is" and v == "null" and rv is not None: return False
        return True
    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._mode == "select":
            matched = [r for r in rows if self._match(r)]
            if self._order_desc: matched = list(reversed(matched))
            if self._limit: matched = matched[: self._limit]
            return FakeResult(matched)
        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payloads:
                r = dict(p); r.setdefault("id", f"{self.table_name}-{len(rows)}"); rows.append(r); out.append(r)
            return FakeResult(out)
        raise RuntimeError("unsupported mode")


class FakeResult:
    def __init__(self, data): self.data = data


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, name): return self
    def table(self, name): return FakeQuery(self.store, name)
    # deliberately NO .rpc -> _payroll_month_groups hits AttributeError -> legacy Python path.


STORE = {}
FAKE = FakeClient(STORE)

import app.modules.storeops.router as R   # noqa: E402
R.get_supabase = lambda: FAKE
R._emp_id_variants = lambda org_id, employee_id: ({str(employee_id)}, None)
R.scope_keyset = lambda authorization, org_id=None: None
R._lunch_get_config = lambda org_id, sb: ({}, {}, False)

ORG = "ORG1"
EID = "E100"
RATE = 20.0

STORE["employees"] = [
    {"id": 100, "employee_id": EID, "org_id": ORG, "name": "Pat Punch",
     "home_store": "HOME", "pay_rate": RATE, "is_active": True},
]
STORE["shifts"] = [
    # A. scheduled day, no manual actual -> punch drives
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "HOME",
     "shift_date": "2026-08-03", "scheduled_hours": 6.3, "actual_hours": 0, "status": "scheduled", "is_deleted": False},
    # B. scheduled day, no punch -> schedule fallback
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "HOME",
     "shift_date": "2026-08-04", "scheduled_hours": 6.0, "actual_hours": 0, "status": "scheduled", "is_deleted": False},
    # C. MANUAL correction (a DM set actual_hours=7.0) + a same-day punch -> manual MUST win
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "HOME",
     "shift_date": "2026-08-05", "scheduled_hours": 6.3, "actual_hours": 7.0, "status": "scheduled", "is_deleted": False},
    # D. scheduled day with only an OPEN punch (forgot to clock out) -> open punch excluded -> schedule
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "HOME",
     "shift_date": "2026-08-06", "scheduled_hours": 5.0, "actual_hours": 0, "status": "scheduled", "is_deleted": False},
    # E. UNSCHEDULED zero-hour override shell (PR #74) — sched 0/act 0
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "3735 26TH",
     "shift_date": "2026-08-07", "scheduled_hours": 0, "actual_hours": 0, "status": "scheduled", "is_deleted": False},
]
STORE["timelog"] = [
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "HOME",   # A: 6.6h
     "clock_in": "2026-08-03T14:00:00+00:00", "clock_out": "2026-08-03T20:36:00+00:00", "hours": 6.6, "work_date": "2026-08-03"},
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "HOME",   # C: 6.6h (ignored — manual 7.0 wins)
     "clock_in": "2026-08-05T14:00:00+00:00", "clock_out": "2026-08-05T20:36:00+00:00", "hours": 6.6, "work_date": "2026-08-05"},
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "HOME",   # D: OPEN punch, hours NULL
     "clock_in": "2026-08-06T14:00:00+00:00", "clock_out": None, "hours": None, "work_date": "2026-08-06"},
    {"org_id": ORG, "employee_id": EID, "employee_name": "Pat Punch", "store_code": "3735 26TH",  # E: 6.5h shell day
     "clock_in": "2026-08-07T14:00:00+00:00", "clock_out": "2026-08-07T20:30:00+00:00", "hours": 6.5, "work_date": "2026-08-07"},
]
STORE["manual_hours"] = []
STORE["payroll_change_log"] = []

START, END = "2026-08-03", "2026-08-16"

# ── get_payroll (the MONEY figure) ───────────────────────────────────────────────────────────────
rows = R.get_payroll(start=START, end=END, authorization="", org_id=ORG)
row = next((r for r in rows if r["employee_id"] == EID), None)
check("payroll row exists", row is not None, str(rows))
if row:
    # F. total = A 6.6 + B 6.0 + C 7.0 (manual) + D 5.0 (schedule) + E 6.5 = 31.1
    check("F. employee total actual_hours = 31.1 (each day counted exactly once, no double count)",
          abs(row["actual_hours"] - 31.1) < 1e-6, f"actual_hours={row['actual_hours']}")
    check("F2. actual_pay = 31.1 * 20.0",
          abs(row["actual_pay"] - round(31.1 * RATE, 2)) < 1e-6, f"actual_pay={row['actual_pay']}")
    # scheduled_hours column unchanged by punch-driven pay: 6.3+6.0+6.3+5.0+0 = 23.6
    check("scheduled_hours column unchanged (23.6 — sum of scheduled, independent of punches)",
          abs(row["scheduled_hours"] - 23.6) < 1e-6, f"scheduled_hours={row['scheduled_hours']}")

# ── drill-down: reconcile EXACTLY + honest per-day counted flags ──────────────────────────────────
detail = R.payroll_actual_hours_detail(employee_id=EID, start=START, end=END, authorization="", org_id=ORG)
check("G. drill-down total reconciles EXACTLY to get_payroll actual_hours",
      row is not None and abs(detail["total_actual_hours"] - row["actual_hours"]) < 1e-6,
      f"detail={detail['total_actual_hours']} payroll={row and row['actual_hours']}")
days = {d["work_date"]: d for d in detail["days"]}

A, B, C, D, E = (days.get("2026-08-03", {}), days.get("2026-08-04", {}), days.get("2026-08-05", {}),
                 days.get("2026-08-06", {}), days.get("2026-08-07", {}))
check("A. scheduled day + punch -> day total 6.6 (punch drives)", abs(A.get("actual_hours", -1) - 6.6) < 1e-6, str(A))
check("A2. its punch counted=True; its scheduled shift counted=False (replaced by the punch)",
      bool(A.get("punches")) and A["punches"][0]["counted"] is True
      and A.get("shift") and A["shift"]["counted"] is False, str(A))
check("B. scheduled day, no punch -> day total 6.0 (schedule fallback)",
      abs(B.get("actual_hours", -1) - 6.0) < 1e-6, str(B))
check("B2. its scheduled shift counted=True (no punch to replace it)",
      B.get("shift") and B["shift"]["counted"] is True, str(B))
check("C. MANUAL day -> day total 7.0 (manual actual wins, punch does NOT override)",
      abs(C.get("actual_hours", -1) - 7.0) < 1e-6, str(C))
check("C2. manual shift counted=True; the 6.6 punch counted=False (manual > punch)",
      C.get("shift") and C["shift"]["counted"] is True
      and bool(C.get("punches")) and C["punches"][0]["counted"] is False, str(C))
check("D. forgot-to-clock-out (open punch) -> day total 5.0 (schedule fallback)",
      abs(D.get("actual_hours", -1) - 5.0) < 1e-6, str(D))
check("D2. open punch counted=False; scheduled shift counted=True",
      bool(D.get("punches")) and D["punches"][0]["counted"] is False
      and D.get("shift") and D["shift"]["counted"] is True, str(D))
check("E. PR #74 zero-hour shell + punch -> day total 6.5 (preserved)",
      abs(E.get("actual_hours", -1) - 6.5) < 1e-6, str(E))
check("E2. shell punch counted=True; shell shift counted=False",
      bool(E.get("punches")) and E["punches"][0]["counted"] is True
      and E.get("shift") and E["shift"]["counted"] is False, str(E))

# ── by-store: reconciles and attributes punch-driven hours to the punch's store ───────────────────
bys = {s["store_code"]: s for s in R.get_payroll_by_store(start=START, end=END, authorization="", org_id=ORG)["stores"]}
# HOME: A 6.6 + B 6.0 + C 7.0(manual) + D 5.0 = 24.6 ; 3735 26TH: E 6.5
check("by-store HOME = 24.6h (punch-driven + manual + schedule days at HOME), 3735 26TH = 6.5h (shell punch)",
      abs(bys.get("HOME", {}).get("hours", -1) - 24.6) < 1e-6
      and abs(bys.get("3735 26TH", {}).get("hours", -1) - 6.5) < 1e-6, str(bys))
check("by-store total hours == /payroll actual_hours (reconciles: 24.6 + 6.5 = 31.1)",
      abs(sum(s["hours"] for s in bys.values()) - row["actual_hours"]) < 1e-6, str(bys))

print("\n".join(f"PASS  {p}" for p in PASS))
if FAIL:
    print("\n".join(f"FAIL  {f}" for f in FAIL))
    sys.exit(1)
print(f"\nALL {len(PASS)} CHECKS PASSED")
