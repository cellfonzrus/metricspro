"""Offline proof (no live DB/network) for the 2026-07-22 owner-directed package:
  1. `_closing_gate_block`'s new EFFECTIVE-CLOSER semantics (backend/app/modules/storeops/router.py).
  2. `_missed_closing_notice` degrading gracefully when closing/ops_chargebacks.py isn't importable.
  3. `GET /storeops/my-chargebacks` self-scoping (identity from the caller, never a client id) + org
     isolation.
  4. `GET /storeops/payroll-chargebacks` + `POST /storeops/payroll-chargebacks/{id}/decision`:
     degrade-gracefully when commcalc.ops_chargeback doesn't exist yet, manager gating on the write,
     and that POST/WAIVE only ever UPDATEs (never inserts) an existing org+applied_to-scoped row.

Runs the REAL functions from app.modules.storeops.router against an in-memory fake Supabase client
(same convention as harness_pto_router_integration.py). Run: `python3 harness_closer_chargebacks.py`
from backend/.
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client: schema("x").table("y").select().eq().in_().order().limit().execute() ──
class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store = store
        self.key = key          # (schema, table)
        self.filters = []
        self._mode = None
        self._payload = None
        self._limit = None

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(str(x) for x in vals))); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def order(self, *_a, **_k):
        return self

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
            if kind == "gte" and str(rv) < str(v):
                return False
            if kind == "lt" and str(rv) >= str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                rows.append(dict(p))
            return Result(payload)
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
        if self._mode == "delete":
            self.store[self.key] = [r for r in rows if not self._matches(r)]
            return Result(matched)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name):
        self.client = client; self.name = name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchema(self, name)

    def table(self, t):        # bare .table() defaults to the 'storeops' schema (mirrors sb())
        return FakeQuery(self.store, ("storeops", t))

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


# ── wire the fake client into the real router module ─────────────────────────────────────────────
import app.modules.storeops.router as R

fake = FakeClient()
R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-test-1"
ORG2 = "org-test-2"
TODAY = datetime.now(timezone.utc).astimezone(R._biz_tz_for(ORG)).date().isoformat()
YEST = "2020-01-01"   # any date != TODAY


def reset():
    fake.store.clear()
    fake.seed("storeops", "tenants", [{"org_id": ORG, "closing_gate_enabled": True}])
    fake.seed("storeops", "employees", [
        {"org_id": ORG, "employee_id": "E1", "id": 101, "name": "Alice"},
        {"org_id": ORG, "employee_id": "E2", "id": 102, "name": "Bob"},
        {"org_id": ORG, "employee_id": "E3", "id": 103, "name": "Cara"},
    ])
    fake.seed("commcalc", "daily_closing", [])
    fake.seed("storeops", "store_closer", [])
    fake.seed("storeops", "timelog", [])


# ═══ 1. EFFECTIVE-CLOSER GATE ═══════════════════════════════════════════════════════════════════

# 1a. Closing already submitted -> nobody is gated, regardless of everything else.
reset()
fake.seed("commcalc", "daily_closing", [{"org_id": ORG, "close_date": TODAY, "store_code": "S1"}])
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
fake.seed("storeops", "timelog", [{"org_id": ORG, "employee_id": "E1", "work_date": TODAY, "store_code": "S1", "clock_out": None}])
check("1a submitted-closing never gates", R._closing_gate_block(ORG, "E1", "S1", TODAY) is None)

# 1b. Static closer worked today (case a) -> the closer IS gated.
reset()
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
fake.seed("storeops", "timelog", [{"org_id": ORG, "employee_id": "E1", "work_date": TODAY, "store_code": "S1", "clock_out": None}])
b = R._closing_gate_block(ORG, "E1", "S1", TODAY)
check("1b static closer worked today -> gated", b is not None, b)

# 1c. Static closer worked today -> a DIFFERENT employee (not the closer) passes, even though they're
#     also still clocked in (the closer, not them, is on the hook).
reset()
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
fake.seed("storeops", "timelog", [
    {"org_id": ORG, "employee_id": "E1", "work_date": TODAY, "store_code": "S1", "clock_out": "2026-01-01T20:00:00+00:00"},
    {"org_id": ORG, "employee_id": "E2", "work_date": TODAY, "store_code": "S1", "clock_out": None},
])
check("1c non-closer passes while static closer worked (even if closer already clocked out)",
      R._closing_gate_block(ORG, "E2", "S1", TODAY) is None)

# 1d. Static closer configured but did NOT work today -> falls to effective-closer: the caller passes
#     if ANOTHER employee is still clocked in at the store (they might be the true last-to-leave).
reset()
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])   # E1 didn't work
fake.seed("storeops", "timelog", [
    {"org_id": ORG, "employee_id": "E2", "work_date": TODAY, "store_code": "S1", "clock_out": None},
    {"org_id": ORG, "employee_id": "E3", "work_date": TODAY, "store_code": "S1", "clock_out": None},
])
check("1d closer absent + someone else still clocked in -> caller passes",
      R._closing_gate_block(ORG, "E2", "S1", TODAY) is None)

# 1e. Same setup, but now E2 is the LAST one still clocked in (E3 already clocked out) -> E2 IS gated.
reset()
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
fake.seed("storeops", "timelog", [
    {"org_id": ORG, "employee_id": "E2", "work_date": TODAY, "store_code": "S1", "clock_out": None},
    {"org_id": ORG, "employee_id": "E3", "work_date": TODAY, "store_code": "S1", "clock_out": "2026-01-01T19:00:00+00:00"},
])
b = R._closing_gate_block(ORG, "E2", "S1", TODAY)
check("1e last-one-clocked-in IS gated when the static closer never worked", b is not None, b)

# 1f. No store_closer row configured at ALL -> same effective-closer fallback applies (last one out).
reset()
fake.seed("storeops", "timelog", [
    {"org_id": ORG, "employee_id": "E2", "work_date": TODAY, "store_code": "S1", "clock_out": None},
])
b = R._closing_gate_block(ORG, "E2", "S1", TODAY)
check("1f unconfigured closer -> sole worker is the effective (last) closer -> gated", b is not None, b)

# 1g. Stale punch (work_date != today) is NEVER gated, regardless of closer/last-out status
#     (this is the d0adba0 behavior this package is required to preserve).
reset()
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
fake.seed("storeops", "timelog", [{"org_id": ORG, "employee_id": "E1", "work_date": YEST, "store_code": "S1", "clock_out": None}])
check("1g stale punch never gated", R._closing_gate_block(ORG, "E1", "S1", YEST) is None)

# 1h. Tenant closing_gate_enabled OFF -> nobody is ever gated.
reset()
fake.store[("storeops", "tenants")] = [{"org_id": ORG, "closing_gate_enabled": False}]
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
fake.seed("storeops", "timelog", [{"org_id": ORG, "employee_id": "E1", "work_date": TODAY, "store_code": "S1", "clock_out": None}])
check("1h gate disabled -> never gated", R._closing_gate_block(ORG, "E1", "S1", TODAY) is None)

# 1i. Store-code spelling variant on the submitted closing still matches (normalized) -> not gated.
reset()
fake.seed("commcalc", "daily_closing", [{"org_id": ORG, "close_date": TODAY, "store_code": " s1 "}])
fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
fake.seed("storeops", "timelog", [{"org_id": ORG, "employee_id": "E1", "work_date": TODAY, "store_code": "S1", "clock_out": None}])
check("1i normalized store-code match on submitted closing", R._closing_gate_block(ORG, "E1", "S1", TODAY) is None)

# 1j. A lookup blowing up (simulated) degrades to no block, never a 500/deadlock.
reset()
class ExplodingFake(FakeClient):
    def schema(self, name):
        raise RuntimeError("simulated outage")
old_get_supabase = R.get_supabase
R.get_supabase = lambda: ExplodingFake()
check("1j exception during lookup degrades to no-block", R._closing_gate_block(ORG, "E1", "S1", TODAY) is None)
R.get_supabase = old_get_supabase


# ═══ 2. _missed_closing_notice degrades gracefully (module not importable yet) ══════════════════
notice = R._missed_closing_notice(ORG, "E1")
check("2 missed_closing_notice returns None when closing.ops_chargebacks doesn't exist", notice is None, notice)


# ═══ 3. GET /storeops/my-chargebacks — self-scoped + org isolation ═══════════════════════════════
reset()
fake.seed("commcalc", "ops_chargeback", [
    {"id": "cb1", "org_id": ORG, "employee_id": "E1", "employee_name": "Alice", "store_code": "S1",
     "reason": "missed_closing", "incident_date": "2026-07-10", "amount": 25, "status": "pending",
     "applied_to": "payroll"},
    {"id": "cb2", "org_id": ORG, "employee_id": "E2", "employee_name": "Bob", "store_code": "S1",
     "reason": "missed_dm_verify", "incident_date": "2026-07-11", "amount": 15, "status": "posted",
     "applied_to": "commission"},
    {"id": "cb3", "org_id": ORG2, "employee_id": "101", "employee_name": "Alice-other-org", "store_code": "S9",
     "reason": "missed_closing", "incident_date": "2026-07-10", "amount": 999, "status": "pending",
     "applied_to": "payroll"},
])
old_caller_identity = R._caller_identity
R._caller_identity = lambda auth: (ORG, "E1")
resp = R.my_chargebacks(authorization="whatever", org_id=ORG)
items = resp["items"]
check("3a my-chargebacks returns only the caller's own rows", len(items) == 1 and items[0]["id"] == "cb1", items)
check("3b reason_label is plain-language", items[0]["reason_label"] == "Missed store closing", items)
check("3c org2's row never leaks (org isolation)", all(i["id"] != "cb3" for i in items))

# identity swap proves it's NEVER a client-supplied id — same call, different token identity -> different result
R._caller_identity = lambda auth: (ORG, "E2")
resp2 = R.my_chargebacks(authorization="whatever", org_id=ORG)
check("3d different token identity -> different (still self-scoped) result",
      len(resp2["items"]) == 1 and resp2["items"][0]["id"] == "cb2", resp2)
R._caller_identity = old_caller_identity


# ═══ 4. Payroll chargebacks: degrade gracefully, manager gating, UPDATE-only (never insert) ══════
# 4a. Table doesn't exist yet -> empty list, never a 500.
reset()
class NoTableFake(FakeClient):
    def schema(self, name):
        if name == "commcalc":
            raise RuntimeError("relation commcalc.ops_chargeback does not exist")
        return FakeSchema(self, name)
R.get_supabase = lambda: NoTableFake()
resp = R.payroll_chargebacks(month="2026-07", authorization="", org_id=ORG)
check("4a payroll_chargebacks degrades to empty list pre-migration", resp == {"items": []}, resp)
R.get_supabase = lambda: fake

# 4b. Real data: only applied_to='payroll' + month-window rows come back, with reason_label attached.
reset()
fake.seed("commcalc", "ops_chargeback", [
    {"id": "cb1", "org_id": ORG, "employee_id": "E1", "employee_name": "Alice", "store_code": "S1",
     "reason": "missed_closing", "incident_date": "2026-07-10", "amount": 25, "status": "pending",
     "applied_to": "payroll"},
    {"id": "cb2", "org_id": ORG, "employee_id": "E2", "employee_name": "Bob", "store_code": "S1",
     "reason": "missed_dm_verify", "incident_date": "2026-07-11", "amount": 15, "status": "posted",
     "applied_to": "commission"},   # applied_to != payroll -> must be excluded
    {"id": "cb3", "org_id": ORG, "employee_id": "E3", "employee_name": "Cara", "store_code": "S1",
     "reason": "missed_closing", "incident_date": "2026-06-01", "amount": 10, "status": "pending",
     "applied_to": "payroll"},   # outside the July window -> excluded when month is filtered
])
resp = R.payroll_chargebacks(month="2026-07", authorization="", org_id=ORG)
ids = sorted(i["id"] for i in resp["items"])
check("4b only payroll-applied, in-month rows returned", ids == ["cb1"], ids)

# 4c. decision() requires a manager — a non-manager caller is rejected (403), row untouched.
def fake_require_manager_reject(*_a, **_k):
    from fastapi import HTTPException
    raise HTTPException(403, "not a manager")
old_require_manager = R._require_manager
R._require_manager = fake_require_manager_reject
raised = False
try:
    R.decide_payroll_chargeback("cb1", {"decision": "post", "period": "2026-07"}, authorization="", org_id=ORG)
except Exception as e:
    raised = getattr(e, "status_code", None) == 403
check("4c non-manager rejected with 403", raised)
row = fake.store[("commcalc", "ops_chargeback")][0]
check("4c row untouched by the rejected attempt", row["status"] == "pending", row)

# 4d. A real manager POSTS cb1 -> status='posted', posted_ref=period, decided_by=email, decided_at set.
R._require_manager = lambda *_a, **_k: {"org_id": ORG, "email": "manager@example.com"}
out = R.decide_payroll_chargeback("cb1", {"decision": "post", "period": "2026-07"}, authorization="", org_id=ORG)
row = next(r for r in fake.store[("commcalc", "ops_chargeback")] if r["id"] == "cb1")
check("4d posted correctly", row["status"] == "posted" and row["posted_ref"] == "2026-07"
      and row["decided_by"] == "manager@example.com" and row.get("decided_at"), row)
check("4d response echoes the new status", out["status"] == "posted", out)

# 4e. This never INSERTS — table row count is unchanged after the decision (UPDATE-only, per contract:
#     detect_missed_closings owns creation, this router never inserts a chargeback).
check("4e no new row was inserted by post/waive", len(fake.store[("commcalc", "ops_chargeback")]) == 3)

# 4f. WAIVE on a different row -> status='waived', never touches posted_ref.
out = R.decide_payroll_chargeback("cb3", {"decision": "waive"}, authorization="", org_id=ORG)
row3 = next(r for r in fake.store[("commcalc", "ops_chargeback")] if r["id"] == "cb3")
check("4f waived correctly, no posted_ref stamped", row3["status"] == "waived" and row3.get("posted_ref") is None, row3)

# 4g. Cross-tenant: a manager whose OWN resolved tenant (from their auth token, same "manager's own
#     tenant is authoritative" rule /timeclock/override already uses) is ORG2 tries to decide on
#     ORG's chargeback id -> no matching (id, org_id) row -> 404, never a cross-tenant write.
R._require_manager = lambda *_a, **_k: {"org_id": ORG2, "email": "other-tenant-manager@example.com"}
raised404 = False
try:
    R.decide_payroll_chargeback("cb1", {"decision": "waive"}, authorization="", org_id=ORG2)
except Exception as e:
    raised404 = getattr(e, "status_code", None) == 404
check("4g cross-tenant decision id 404s, no leak", raised404)
row1_again = next(r for r in fake.store[("commcalc", "ops_chargeback")] if r["id"] == "cb1")
check("4g cb1 (org ORG) untouched by the ORG2 attempt", row1_again["status"] == "posted", row1_again)

R._require_manager = old_require_manager


# ── report ─────────────────────────────────────────────────────────────────────────────────────
print(f"{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
print("ALL GREEN" if not FAIL else "FAILURES ABOVE")
sys.exit(1 if FAIL else 0)
