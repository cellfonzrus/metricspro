"""Harness — lunch, adjustment + reason, and the final PAYABLE hours the DM approves (mig 746).

Owner directive 2026-08-11: "in the payroll hours approval, the lunch hour calculated on the
previous screen and the adjustment hours with a reason for adjustment should be added as additional
columns, the final payable hours should then be approved."

THE DEFECT THIS EXISTS TO PREVENT — and it is one line of arithmetic away at all times:

    GET /storeops/payroll returns `actual_hours` ALREADY NET of the lunch deduction. router.py does
        summary[eid]["actual_hours"] -= applied
        summary[eid]["lunch_deduction_hours"] += applied
    So the directive read literally — payable = worked − lunch + adjustment, with `worked` taken to
    be actual_hours — DEDUCTS LUNCH TWICE. On luxelink (30 min past a 7h shift) that is half an hour
    a shift off every paycheque, in the direction that underpays. A1/A2 below reproduce the wrong
    answer explicitly so that "simplifying" the formula later fails loudly instead of shorting staff.

Everything else here guards the money gates: an adjustment cannot move a number without a reason,
cannot drive payable negative, cannot be silently dropped by the HR stage, and cannot be invented by
recompute after a DM has signed off.
"""
import sys, os, types, copy
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


class _Q:
    def __init__(self, store, name, calls):
        self.store, self.name, self.calls, self.f = store, name, calls, []

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def _match(self, row):
        return all(row.get(c) == v for c, v in self.f)

    def upsert(self, row, on_conflict=""):
        self._up, self._key = row, [k.strip() for k in on_conflict.split(",") if k.strip()]
        return self

    def insert(self, rows):
        self._ins = rows if isinstance(rows, list) else [rows]; return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if getattr(self, "_up", None) is not None:
            r = self._up
            for e in rows:
                if all(e.get(k) == r.get(k) for k in self._key):
                    e.update(r)
                    self.calls.append(("upsert-update", self.name))
                    return types.SimpleNamespace(data=[copy.deepcopy(e)])
            rows.append(dict(r))
            self.calls.append(("upsert-insert", self.name))
            return types.SimpleNamespace(data=[copy.deepcopy(r)])
        if getattr(self, "_ins", None) is not None:
            rows.extend(dict(x) for x in self._ins)
            return types.SimpleNamespace(data=copy.deepcopy(self._ins))
        return types.SimpleNamespace(data=copy.deepcopy([r for r in rows if self._match(r)]))


class _S:
    def __init__(self, store, calls): self.store, self.calls = store, calls
    def table(self, n): return _Q(self.store, n, self.calls)


class FakeClient:
    def __init__(self, store): self.store, self.calls = store, []
    def schema(self, n): return _S(self.store, self.calls)
    def table(self, n): return _S(self.store, self.calls).table(n)


import app.modules.storeops.payroll_approval as R           # noqa: E402
import app.modules.storeops.router as SR                     # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
S, E = date(2026, 8, 3), date(2026, 8, 10)

LOGGED = []
SR._require_manager = lambda auth, org: {"email": "dm@luxelink.com"}
SR._log_payroll_change = lambda *a, **k: LOGGED.append(k)

# One employee, real luxelink shape: 36.5 gross hours, 2.5h of lunch already taken out ⇒ 34.0 net.
HOURS = [{"employee_id": "E1", "name": "Rep One", "store": "957",
          "actual_hours": 34.0, "lunch_deduction_hours": 2.5,
          "scheduled_hours": 40.0, "pay_rate": 20.0}]

R._hours_for_period = lambda org, s, e, auth: copy.deepcopy(HOURS)
R._resolve_period = lambda org, s, e: (S, E)
R._payers = lambda org: []
R._store_payer_map = lambda org: {}


def fresh():
    store = {"payroll_approval": []}
    fc = FakeClient(store)
    R.sb = lambda: fc.schema("storeops")
    return store, fc


def board(store=None):
    return R.list_approvals(org_id=ORG, authorization="Bearer t")


print("\n=== A · lunch is deducted ONCE, not twice ===")
store, fc = fresh()
r = board()["rows"][0]

ok(r["hours_worked"] == 36.5, f"A1 Worked is GROSS — 34.0 net + 2.5 lunch = 36.5 (got {r['hours_worked']})")
ok(r["lunch_hours"] == 2.5, f"A2 Lunch column carries the previous screen's deduction (got {r['lunch_hours']})")
ok(r["hours_payable"] == 34.0,
   f"A3 Payable is 34.0 — NOT 31.5. 31.5 would be the double deduction (got {r['hours_payable']})")
ok(round(r["hours_worked"] - r["lunch_hours"] + r["adjustment_hours"], 2) == r["hours_payable"],
   "A4 the identity holds: worked − lunch + adjustment == payable")
ok(r["hours_source"] == 34.0, "A5 hours_source still reports the NET figure it always did")
ok(r["hours_effective"] == 34.0,
   "A6 with no adjustment, effective hours are byte-identical to pre-746 behaviour")
ok(r["adjustment_hours"] == 0 and r["adjustment_reason"] is None,
   "A7 adjustment starts at zero with no reason — nothing auto-applies")

print("\n=== B · an adjustment moves a payroll number, so it needs a reason ===")
store, fc = fresh()
LOGGED.clear()
res = R.decide({"stage": "dm", "rows": [
    {"employee_id": "E1", "action": "approve", "adjustment_hours": 3}]},
    authorization="Bearer t", org_id=ORG)
ok(res["applied"] == 0 and "reason" in (res["errors"][0]["error"]),
   "B1 an adjustment with no reason is REFUSED")
ok(not store["payroll_approval"], "B2 the refused row was not written at all")
ok(not LOGGED, "B3 nothing was logged for a refused adjustment")

res = R.decide({"stage": "dm", "rows": [
    {"employee_id": "E1", "action": "approve", "adjustment_hours": 3,
     "adjustment_reason": "covered the Penn Ave close, missed punch"}]},
    authorization="Bearer t", org_id=ORG)
ok(res["applied"] == 1, "B4 with a reason it goes through")
saved = store["payroll_approval"][0]
ok(saved["adjustment_hours"] == 3.0 and "Penn Ave" in (saved["adjustment_reason"] or ""),
   "B5 the adjustment AND its reason are persisted")
ok(len(LOGGED) == 1 and LOGGED[0]["field"] == "adjustment_hours"
   and LOGGED[0]["before"] == 0.0 and LOGGED[0]["after"] == 3.0,
   "B6 the change is written to payroll_change_log with before/after")

r = board()["rows"][0]
ok(r["hours_payable"] == 37.0, f"B7 payable becomes 34.0 + 3 = 37.0 (got {r['hours_payable']})")
ok(round(r["hours_worked"] - r["lunch_hours"] + r["adjustment_hours"], 2) == r["hours_payable"],
   "B8 the identity still holds after an adjustment")
ok(r["hours_effective"] == 37.0, "B9 the DM approves the PAYABLE figure")
ok(r["pay_effective"] == 740.0, "B10 pay follows payable hours (37.0 × $20)")

print("\n=== C · the guard rails ===")
store, fc = fresh()
res = R.decide({"stage": "dm", "rows": [
    {"employee_id": "E1", "action": "approve", "adjustment_hours": -40,
     "adjustment_reason": "typo"}]}, authorization="Bearer t", org_id=ORG)
ok(res["applied"] == 0 and "negative" in res["errors"][0]["error"],
   "C1 an adjustment that drives payable below zero is refused")

store, fc = fresh()
R.decide({"stage": "dm", "rows": [{"employee_id": "E1", "action": "approve",
                                   "adjustment_hours": 2, "adjustment_reason": "missed punch"}]},
         authorization="Bearer t", org_id=ORG)
R.decide({"stage": "hr", "rows": [{"employee_id": "E1", "action": "approve"}]},
         authorization="Bearer t", org_id=ORG)
saved = store["payroll_approval"][0]
ok(saved["adjustment_hours"] == 2.0,
   "C2 the HR stage carries the DM's adjustment forward — it does NOT reset to 0")
ok(saved["hr_status"] == "approved" and saved["dm_status"] == "approved",
   "C3 both stages are recorded")

# an explicit hours_approved override still beats the computed payable
store, fc = fresh()
R.decide({"stage": "dm", "rows": [{"employee_id": "E1", "action": "approve",
                                   "hours_approved": 30, "reason": "agreed with the rep"}]},
         authorization="Bearer t", org_id=ORG)
r = board()["rows"][0]
ok(r["hours_effective"] == 30.0, "C4 an explicit hours_approved override still wins over payable")
ok(r["hours_payable"] == 34.0, "C5 …and payable still reports what the maths says")

print("\n=== D · an approved week cannot restate itself in silence ===")
store, fc = fresh()
R.decide({"stage": "dm", "rows": [{"employee_id": "E1", "action": "approve"}]},
         authorization="Bearer t", org_id=ORG)
saved = store["payroll_approval"][0]
ok(saved["worked_at_approval"] == 36.5 and saved["lunch_at_approval"] == 2.5,
   "D1 DM approval freezes the worked + lunch figures it signed off")
r = board()["rows"][0]
ok(r["hours_drifted"] is False, "D2 no drift reported while the numbers agree")

HOURS[0]["actual_hours"] = 38.0        # a punch was edited after sign-off
r = board()["rows"][0]
ok(r["hours_drifted"] is True,
   "D3 a post-approval punch edit is REPORTED as drift, not silently absorbed")
ok(r["worked_at_approval"] == 36.5, "D4 the board still shows what was actually approved")
HOURS[0]["actual_hours"] = 34.0

R.decide({"stage": "dm", "rows": [{"employee_id": "E1", "action": "reset"}]},
         authorization="Bearer t", org_id=ORG)
saved = store["payroll_approval"][0]
ok(saved["worked_at_approval"] is None and saved["lunch_at_approval"] is None,
   "D5 a reset clears the snapshot so re-approval freezes afresh")

print("\n=== E · a tenant with lunch switched off is unaffected ===")
HOURS[0].pop("lunch_deduction_hours")   # exactly what router.py omits when mig 418 isn't available
store, fc = fresh()
r = board()["rows"][0]
ok(r["lunch_hours"] == 0 and r["hours_worked"] == 34.0,
   "E1 no lunch key ⇒ lunch 0 and worked == net, no invented deduction")
ok(r["hours_payable"] == 34.0 and r["hours_effective"] == 34.0,
   "E2 payable and effective are byte-identical to pre-746")

print(f"\n{'=' * 70}\nRESULT: {len(PASS)}/{len(PASS) + len(FAIL)} passed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
