"""Offline proof (no live DB/network) for the 2026-07-26 owner directive: Boost managers could not
reschedule an employee with approved/requested time off — POST /shifts hard-blocked with a 409 and
no override. This proves the new default: scheduling over approved time off is ALLOWED with a
non-blocking `timeoff_warning`, org-configurable back to the old hard-block via
GET/PUT /storeops/timeoff-conflict-mode, degrading to 'warn' whenever the config is missing/unset.

Runs the ACTUAL shipped functions from app.modules.storeops.router (create_shift,
get_timeoff_conflict_mode, set_timeoff_conflict_mode, update_time_off, apply_templates,
reconcile_timeoff_duplicates) against an in-memory fake Supabase client.
Run: `python3 harness_timeoff_reschedule.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (eq/gte/lte/lt/in_ filters + insert/update/delete/select) ─────────────
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._limit = None
        self._mode = None
        self._payload = None
        self._order_desc = False

    def select(self, cols=None):
        self._mode = self._mode or "select"
        return self

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

    def order(self, *a, **k):
        self._order_desc = k.get("desc", False)
        return self

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
        if self._mode == "update":
            matched = [r for r in rows if self._match(r)]
            for r in matched:
                r.update(self._payload)
            return FakeResult(matched)
        if self._mode == "delete":
            matched = [r for r in rows if self._match(r)]
            self.store[self.table_name] = [r for r in rows if r not in matched]
            return FakeResult(matched)
        raise RuntimeError("no mode set")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeSchemaClient:
    """schema()/table() both return a query rooted in the SAME flat store (schema name ignored,
    matching every sibling harness's convention — this module only ever touches storeops.*)."""
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return self

    def table(self, name):
        return FakeQuery(self.store, name)


class ExplodingTenantsClient(FakeSchemaClient):
    """Simulates migration 409 not yet applied — the tenants.timeoff_conflict_mode SELECT itself
    raises (e.g. undefined column), everything else behaves normally."""
    def table(self, name):
        if name == "tenants":
            raise RuntimeError("simulated: column timeoff_conflict_mode does not exist")
        return FakeQuery(self.store, name)


STORE = {}
FAKE_CLIENT = FakeSchemaClient(STORE)


def fake_get_supabase():
    return FAKE_CLIENT


import app.modules.storeops.router as router_mod          # noqa: E402
import app.modules.core.router as core_router_mod         # noqa: E402
from fastapi import HTTPException                         # noqa: E402

router_mod.get_supabase = fake_get_supabase
core_router_mod._uid_from_token = lambda auth: ("mgr-uid" if auth == "Bearer manager" else
                                                 ("rep-uid" if auth == "Bearer rep" else None))

ORG = "ORG-A"
ORG2 = "ORG-B"


def reset():
    STORE.clear()
    STORE["app_users"] = [
        {"auth_id": "mgr-uid", "org_id": ORG, "email": "boss@x.com", "role": "admin", "employee_id": "MGR1"},
        {"auth_id": "rep-uid", "org_id": ORG, "email": "rep@x.com", "role": "rep", "employee_id": "EMP1"},
    ]
    STORE["roles"] = [{"org_id": ORG, "name": "rep", "permissions": {"scope": "self"}}]
    STORE["time_off_requests"] = [
        {"id": 1, "org_id": ORG, "employee_id": "EMP1", "status": "approved",
         "start_date": "2026-07-28", "end_date": "2026-07-28", "type": "PTO", "notes": "family trip"},
    ]
    STORE["shifts"] = []
    STORE["hours_budget"] = []
    STORE["tenants"] = []


def shifts_for(org):
    return [s for s in STORE["shifts"] if s.get("org_id") == org]


# ══ 1: DEFAULT — no tenants row at all → mode resolves to 'warn' (missing-config degrade) ═════════
reset()
check("1a _timeoff_conflict_mode() with no tenants row at all → 'warn'",
      router_mod._timeoff_conflict_mode(ORG) == "warn")
check("1b GET /timeoff-conflict-mode with no row → {'mode': 'warn'}",
      router_mod.get_timeoff_conflict_mode(org_id=ORG) == {"mode": "warn"})

# ══ 2: WARN mode (explicit) — create_shift on a day with approved time off SUCCEEDS + warns ═══════
reset()
STORE["tenants"] = [{"org_id": ORG, "timeoff_conflict_mode": "warn"}]
out = router_mod.create_shift({
    "employee_id": "EMP1", "employee_name": "Alice Rep", "store_code": "Store1",
    "shift_date": "2026-07-28", "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8,
}, org_id=ORG)
check("2a warn mode: the shift WAS actually created (no 409)", len(shifts_for(ORG)) == 1, shifts_for(ORG))
check("2b warn mode: response carries a timeoff_warning naming the employee + date",
      out.get("timeoff_warning") == "Alice Rep has approved time off on 2026-07-28.", out)
check("2c warn mode: org_id stamped on the inserted shift (RULE ONE)",
      shifts_for(ORG)[0].get("org_id") == ORG, shifts_for(ORG)[0])

# ══ 3: BLOCK mode (opt-in) — same conflict now hard-409s, NOTHING inserted ═════════════════════════
reset()
STORE["tenants"] = [{"org_id": ORG, "timeoff_conflict_mode": "block"}]
try:
    router_mod.create_shift({
        "employee_id": "EMP1", "employee_name": "Alice Rep", "store_code": "Store1",
        "shift_date": "2026-07-28", "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8,
    }, org_id=ORG)
    check("3a block mode: raises HTTPException(409)", False, "no exception raised")
except HTTPException as e:
    check("3a block mode: raises HTTPException(409)", e.status_code == 409, e.detail)
check("3b block mode: NOTHING was inserted (true block, not a warn-then-rollback)",
      len(shifts_for(ORG)) == 0, shifts_for(ORG))

# ══ 4: missing-config fallback — tenants SELECT itself raises (migration 409 not yet run) ═════════
reset()
old_get_supabase = router_mod.get_supabase
router_mod.get_supabase = lambda: ExplodingTenantsClient(STORE)
try:
    check("4a mode resolution never raises even when tenants lookup explodes",
          router_mod._timeoff_conflict_mode(ORG) == "warn")
    out4 = router_mod.create_shift({
        "employee_id": "EMP1", "employee_name": "Alice Rep", "store_code": "Store1",
        "shift_date": "2026-07-28", "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8,
    }, org_id=ORG)
    check("4b pre-migration-409: create_shift still SUCCEEDS (degrades to warn, never a 500)",
          len(shifts_for(ORG)) == 1, shifts_for(ORG))
    check("4c pre-migration-409: still carries the warning string",
          "timeoff_warning" in out4, out4)
finally:
    router_mod.get_supabase = old_get_supabase

# ══ 5: no conflict at all → response carries NO timeoff_warning key (frontend does `if (x?.key)`) ══
reset()
STORE["tenants"] = [{"org_id": ORG, "timeoff_conflict_mode": "warn"}]
out5 = router_mod.create_shift({
    "employee_id": "EMP1", "employee_name": "Alice Rep", "store_code": "Store1",
    "shift_date": "2026-08-15", "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8,
}, org_id=ORG)
check("5a a day with no approved time-off never adds timeoff_warning to the response",
      "timeoff_warning" not in out5, out5)

# ══ 6: multi-tenant isolation — org B's own default 'warn' is untouched by org A's 'block' row ═════
reset()
STORE["tenants"] = [{"org_id": ORG, "timeoff_conflict_mode": "block"}]
STORE["time_off_requests"].append(
    {"id": 2, "org_id": ORG2, "employee_id": "EMP9", "status": "approved",
     "start_date": "2026-07-28", "end_date": "2026-07-28", "type": "PTO", "notes": ""})
check("6a org B (no row of its own) still resolves 'warn' despite org A being 'block'",
      router_mod._timeoff_conflict_mode(ORG2) == "warn")
out6 = router_mod.create_shift({
    "employee_id": "EMP9", "employee_name": "Zed Other", "store_code": "StoreZ",
    "shift_date": "2026-07-28", "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8,
}, org_id=ORG2)
check("6b org B's own conflicting shift succeeds with a warning, unaffected by org A's block mode",
      out6.get("timeoff_warning") and len(shifts_for(ORG2)) == 1, (out6, shifts_for(ORG2)))

# ══ 7: PUT /timeoff-conflict-mode — manager-gated, validates mode, persists org-scoped ═════════════
reset()
STORE["tenants"] = [{"org_id": ORG, "timeoff_conflict_mode": "warn"}]
try:
    router_mod.set_timeoff_conflict_mode({"mode": "block"}, authorization="Bearer rep", org_id=ORG)
    check("7a non-manager caller is rejected", False, "no exception raised")
except HTTPException as e:
    check("7a non-manager caller is rejected", e.status_code == 403, e.detail)
check("7b rejected PUT never changed the stored value",
      router_mod._timeoff_conflict_mode(ORG) == "warn")
try:
    router_mod.set_timeoff_conflict_mode({"mode": "nonsense"}, authorization="Bearer manager", org_id=ORG)
    check("7c invalid mode value is rejected 400", False, "no exception raised")
except HTTPException as e:
    check("7c invalid mode value is rejected 400", e.status_code == 400, e.detail)
r7 = router_mod.set_timeoff_conflict_mode({"mode": "block"}, authorization="Bearer manager", org_id=ORG)
check("7d manager PUT succeeds", r7 == {"ok": True, "mode": "block"}, r7)
check("7e GET now reflects the persisted 'block' mode",
      router_mod.get_timeoff_conflict_mode(org_id=ORG) == {"mode": "block"})

# ══ 8: update_time_off — editing dates/type/notes on an EXISTING request (the reschedule ask) ═════
reset()
updated = router_mod.update_time_off(1, {
    "start_date": "2026-08-01", "end_date": "2026-08-03", "type": "Personal", "notes": "moved trip",
}, org_id=ORG)
check("8a edited request's dates persisted", updated["start_date"] == "2026-08-01" and updated["end_date"] == "2026-08-03", updated)
check("8b edited request's type/notes persisted", updated["type"] == "Personal" and updated["notes"] == "moved trip", updated)
check("8c editing dates alone does NOT flip status (still 'approved', no denied-cascade misfire)",
      updated["status"] == "approved", updated)
check("8d a NEW shift on the request's OLD date (now vacated) no longer sees a conflict",
      "timeoff_warning" not in router_mod.create_shift({
          "employee_id": "EMP1", "employee_name": "Alice Rep", "store_code": "Store1",
          "shift_date": "2026-07-28", "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8,
      }, org_id=ORG))
check("8e a shift on the request's NEW (rescheduled) date DOES warn",
      "timeoff_warning" in router_mod.create_shift({
          "employee_id": "EMP1", "employee_name": "Alice Rep", "store_code": "Store1",
          "shift_date": "2026-08-02", "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8,
      }, org_id=ORG))

# ══ 9: apply_templates — bulk apply keeps SKIPPING time-off days, unchanged, regardless of mode ═══
reset()
STORE["tenants"] = [{"org_id": ORG, "timeoff_conflict_mode": "warn"}]   # even in warn mode...
STORE["shift_templates"] = [
    {"org_id": ORG, "employee_id": "EMP1", "employee_name": "Alice Rep", "store_code": "Store1",
     "weekday": 1, "start_time": "09:00", "end_time": "17:00", "scheduled_hours": 8},  # Tue
]
r9 = router_mod.apply_templates({"week_start": "2026-07-27"}, org_id=ORG)  # Mon 07-27 -> Tue 07-28 (the time-off day)
check("9a bulk apply-templates still SKIPS the time-off day (unchanged, safe-default behavior)",
      r9.get("skipped_timeoff") == 1 and r9.get("added") == 0, r9)
check("9b no phantom shift was created for the skipped day", len(shifts_for(ORG)) == 0, shifts_for(ORG))

# ══ 10: reconcile_timeoff_duplicates — still returns cleanly (stray dead-code line removed) ═══════
reset()
r10 = router_mod.reconcile_timeoff_duplicates(org_id=ORG)
check("10a reconcile endpoint still returns the expected shape after the dead-line cleanup",
      set(r10.keys()) == {"ok", "reconciled", "ids"} and r10["ok"] is True, r10)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
