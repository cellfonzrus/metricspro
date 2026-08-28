"""Proof for the $0-clocked-day payroll fix (mod-people, 2026-08-24 — Luxelink / Alondra Navarro).

Runs the ACTUAL shipped get_payroll + payroll_actual_hours_detail from app.modules.storeops.router
against an in-memory fake Supabase client (no live DB / network). The fake client has no `.rpc`, so
_payroll_month_groups degrades to the LEGACY Python path — which now carries the same guard the mig-913
RPC does. Run: `python3 harness_zero_hour_shift_punch.py` from backend/.

THE BUG: a rep clocked in on an UNSCHEDULED day (manager clock-in override) and was paid $0.
`clock_in_override` inserts a shifts row to put the store "on record" (status='scheduled') with NO
scheduled_hours / NO actual_hours — a pure ZERO-HOUR SHELL. Every payroll reader's no-double-count
rule dropped that day's real closed punch merely because *a* shifts row existed for
(employee_id, work_date), never checking the shift carried hours; the shell itself contributed 0, so
the day paid nothing while a real 6.5h punch was hidden.

THE FIX (_shift_contributes_hours + mig 913): a shift may suppress its day's punch ONLY when it
carries hours (scheduled_hours>0 OR actual_hours>0). A genuine SCHEDULED shift (sched>0) still
suppresses — schedule-vs-punch policy on real scheduled days is UNCHANGED. Only the never-paying zero
shell stops hiding a punch. Never double-counts (shell adds 0).

Proves, on ONE active employee mirroring Alondra's period. NOTE: the PR #74 zero-hour-shell fix this
file was written for is still fully preserved (checks 1 & 5b/5c), but the pay model has since become
PUNCH-DRIVEN (owner directive 2026-08-24 — see harness_punch_driven_pay.py for the full matrix), so a
SCHEDULED day with a closed punch now pays the PUNCH, not the schedule. Values updated accordingly:
  1. UNSCHEDULED override-shell day (sched=0, act=0) + clean 6.5h closed punch -> pays 6.5h (was $0).
     PR #74 FIX — preserved.
  2. Real SCHEDULED day (sched=6.3, act=0) + 6.6h closed punch -> now pays the PUNCH's 6.6h (was 6.3h
     pre-punch-driven). scheduled_hours column stays 6.3 (unchanged — only ACTUAL/pay follow the punch).
  3. Pure kiosk day, NO shift at all + 6.0h closed punch -> pays 6.0h (regression: already worked).
  4. Forgot-to-clock-out day: OPEN punch (clock_out None / hours None) -> excluded everywhere.
  5. The drill-down (payroll_actual_hours_detail) RECONCILES EXACTLY to get_payroll's actual_hours,
     and now marks BOTH the override-shell's punch AND the scheduled day's punch counted=True (each
     drives its own day's pay) — the honest, reconciling explanation of the paid number.
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
# Isolate the reconciliation rule under test: stub the identity/scope/lunch helpers so the harness
# exercises the shift/punch counted logic, not auth or lunch config.
R._emp_id_variants = lambda org_id, employee_id: ({str(employee_id)}, None)
R.scope_keyset = lambda authorization, org_id=None: None
R._lunch_get_config = lambda org_id, sb: ({}, {}, False)

ORG = "ORG1"
EID = "E45"          # Alondra's business id (the id her kiosk punches AND the override shell carry)

STORE["employees"] = [
    {"id": 45, "employee_id": EID, "org_id": ORG, "name": "Alondra Navarro",
     "home_store": "HOME", "pay_rate": 17.05, "is_active": True},
]
STORE["shifts"] = [
    # (1) UNSCHEDULED override-shell day: status='scheduled' but NO hours (both 0) — the clock_in_override row.
    {"org_id": ORG, "employee_id": EID, "employee_name": "Alondra Navarro", "store_code": "3735 26TH",
     "shift_date": "2026-08-06", "scheduled_hours": 0, "actual_hours": 0, "status": "scheduled",
     "is_deleted": False},
    # (2) Real SCHEDULED day: sched 6.3, actual not reconciled (0).
    {"org_id": ORG, "employee_id": EID, "employee_name": "Alondra Navarro", "store_code": "HOME",
     "shift_date": "2026-08-13", "scheduled_hours": 6.3, "actual_hours": 0, "status": "scheduled",
     "is_deleted": False},
]
STORE["timelog"] = [
    # (1) clean closed punch on the override-shell day -> must now pay 6.5h
    {"org_id": ORG, "employee_id": EID, "employee_name": "Alondra Navarro", "store_code": "3735 26TH",
     "clock_in": "2026-08-06T14:36:00+00:00", "clock_out": "2026-08-06T21:07:00+00:00",
     "hours": 6.5, "work_date": "2026-08-06"},
    # (2) closed punch on the scheduled day (6.6h) -> stays suppressed; day pays scheduled 6.3
    {"org_id": ORG, "employee_id": EID, "employee_name": "Alondra Navarro", "store_code": "HOME",
     "clock_in": "2026-08-13T14:33:00+00:00", "clock_out": "2026-08-13T21:09:00+00:00",
     "hours": 6.6, "work_date": "2026-08-13"},
    # (3) pure kiosk day, no shift -> 6.0h (regression)
    {"org_id": ORG, "employee_id": EID, "employee_name": "Alondra Navarro", "store_code": "3735 26TH",
     "clock_in": "2026-08-09T14:00:00+00:00", "clock_out": "2026-08-09T20:00:00+00:00",
     "hours": 6.0, "work_date": "2026-08-09"},
    # (4) forgot-to-clock-out: OPEN punch, hours NULL -> excluded everywhere
    {"org_id": ORG, "employee_id": EID, "employee_name": "Alondra Navarro", "store_code": "3735 26TH",
     "clock_in": "2026-08-07T14:00:00+00:00", "clock_out": None, "hours": None, "work_date": "2026-08-07"},
]
STORE["manual_hours"] = []
STORE["payroll_change_log"] = []

START, END = "2026-08-06", "2026-08-19"

# ── get_payroll (the MONEY figure) ───────────────────────────────────────────────────────────────
rows = R.get_payroll(start=START, end=END, authorization="", org_id=ORG)
row = next((r for r in rows if r["employee_id"] == EID), None)
check("payroll row exists", row is not None, str(rows))
if row:
    # PUNCH-DRIVEN: override-shell 6.5 + scheduled-day PUNCH 6.6 + kiosk 6.0 = 19.1 ; open punch excluded.
    check("1. override-shell punch paid AND scheduled-day punch drives pay (actual_hours=19.1)",
          abs(row["actual_hours"] - 19.1) < 1e-6, f"actual_hours={row['actual_hours']}")
    check("1b. actual_pay = 19.1 * 17.05",
          abs(row["actual_pay"] - round(19.1 * 17.05, 2)) < 1e-6, f"actual_pay={row['actual_pay']}")
    check("2. scheduled_hours column unchanged at 6.3 (only ACTUAL/pay follow the punch)",
          abs(row["scheduled_hours"] - 6.3) < 1e-6, f"scheduled_hours={row['scheduled_hours']}")

# Before-change control: reproduce the OLD schedule-authoritative model by making every closed punch
# on a hours-carrying shift day suppressed and every shift pay act>0?act:sched — showing the scheduled
# day paid 6.3 (not 6.6) and the total was 18.8 before punch-driven pay.
_sac = R._shift_actual_contribution
R._shift_actual_contribution = lambda s, md, pd: (float(s.get("actual_hours") or 0)
                                                  or float(s.get("scheduled_hours") or 0))
_pcf = R._punch_counts_for_pay
# old suppression: a punch is dropped when its day carries any hours-bearing shift (sched>0 or act>0)
_shell_days = {("E45", "2026-08-06")}   # the only zero-hour shell in this fixture
R._punch_counts_for_pay = lambda t, md: bool(t.get("clock_out") and t.get("hours") is not None) and (
    (str(t.get("employee_id")), str(t.get("work_date") or "")[:10]) not in {
        (str(s.get("employee_id")), str(s.get("shift_date") or "")[:10])
        for s in STORE["shifts"]
        if (float(s.get("scheduled_hours") or 0) > 0 or float(s.get("actual_hours") or 0) > 0)})
rows_old = R.get_payroll(start=START, end=END, authorization="", org_id=ORG)
row_old = next((r for r in rows_old if r["employee_id"] == EID), None)
R._shift_actual_contribution, R._punch_counts_for_pay = _sac, _pcf
if row_old:
    # old: shell day = 6.5 (PR#74) + scheduled day = 6.3 (punch suppressed) + kiosk 6.0 = 18.8
    check("0. CONTROL: pre-punch-driven paid the SCHEDULE on the scheduled day (actual_hours=18.8)",
          abs(row_old["actual_hours"] - 18.8) < 1e-6, f"pre-change actual_hours={row_old['actual_hours']}")

# ── payroll_actual_hours_detail (the drill-down must reconcile to the paid number) ────────────────
detail = R.payroll_actual_hours_detail(employee_id=EID, start=START, end=END, authorization="", org_id=ORG)
check("5a. drill-down total reconciles to payroll actual_hours",
      row is not None and abs(detail["total_actual_hours"] - row["actual_hours"]) < 1e-6,
      f"detail={detail['total_actual_hours']} payroll={row and row['actual_hours']}")
days = {d["work_date"]: d for d in detail["days"]}
shell = days.get("2026-08-06", {})
sched = days.get("2026-08-13", {})
check("5b. override-shell day total = 6.5 (punch counted)",
      abs(shell.get("actual_hours", -1) - 6.5) < 1e-6, str(shell))
check("5c. override-shell punch is counted=True",
      bool(shell.get("punches")) and shell["punches"][0]["counted"] is True, str(shell.get("punches")))
check("5d. scheduled day total = 6.6 (PUNCH-DRIVEN: the punch, not the 6.3 schedule)",
      abs(sched.get("actual_hours", -1) - 6.6) < 1e-6, str(sched))
check("5e. scheduled day punch is now counted=True (it drives pay)",
      bool(sched.get("punches")) and sched["punches"][0]["counted"] is True, str(sched.get("punches")))
check("5f. forgot-to-clock-out day (open punch) contributes 0 & counted=False",
      abs(days.get("2026-08-07", {}).get("actual_hours", -1) - 0.0) < 1e-6
      and days["2026-08-07"]["punches"][0]["counted"] is False, str(days.get("2026-08-07")))

print("\n".join(f"PASS  {p}" for p in PASS))
if FAIL:
    print("\n".join(f"FAIL  {f}" for f in FAIL))
    sys.exit(1)
print(f"\nALL {len(PASS)} CHECKS PASSED")
