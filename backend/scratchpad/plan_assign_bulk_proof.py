"""Proof for the people-centric BULK plan-assignment surface (owner directive 2026-07-23).

Drives the REAL endpoints (r.bulk_assign_commission_plan / r.commission_plan_roster) against a
FakeClient mirroring the supabase query chain. Covers:
  • bulk multi-person upsert (N people → N assigned, one call)
  • org_id STAMPED on every inserted row + org-scoped reads/deletes (verified as a NON-house tenant)
  • identical row shape to single-assign ({org_id, plan_id, scope:'employee', scope_value, priority})
  • already-had-this-plan (no-op, no dup row)
  • replace-existing: replaced counts + old rows deleted + person ends on exactly the new plan
  • replace_existing=false leaves a different-plan person untouched (skipped_has_other)
  • empty selection / missing plan_id REJECTED (400); unknown/cross-tenant plan → 404
  • case-insensitive de-dupe of the selection
  • roster: role + market + value(epay||name) + current_plans; role/market facets; org isolation
"""


def run_route(x):
    """Call a commcalc route handler in EITHER shape.

    ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers were converted from `async def` to
    `def` so FastAPI runs them in the threadpool instead of on the single uvicorn event loop (an
    `async def` doing blocking Supabase I/O froze the whole product for its duration). The only textual
    change was the keyword. This helper awaits a coroutine when it gets one and passes a plain result
    straight through, so the proof works against BOTH shapes and needs no further edit if a handler
    ever legitimately becomes a coroutine again."""
    import asyncio as _a
    return _a.run(x) if _a.iscoroutine(x) else x
import sys, os, asyncio, uuid, copy
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fastapi import HTTPException
import app.modules.commcalc.router as r

ORG_A = "00000000-0000-0000-0000-000000000001"       # house
ORG_B = "00000000-0000-0000-0000-0000000000bb"       # a NON-house tenant (org-isolation + stamping)


# ── FakeClient emulating the supabase query chain (eq + in_ filters, insert/delete/select) ──────────
class Q:
    def __init__(self, store, table):
        self.store = store; self.table = table
        self.f = {}; self.fin = {}; self.op = None; self.payload = None
    def select(self, *a, **k): self.op = 'select'; return self
    def insert(self, payload): self.op = 'insert'; self.payload = payload; return self
    def delete(self): self.op = 'delete'; return self
    def eq(self, c, v): self.f[c] = v; return self
    def in_(self, c, vals): self.fin[c] = list(vals); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def _match(self, row):
        if not all(row.get(c) == v for c, v in self.f.items()):
            return False
        return all(row.get(c) in vals for c, vals in self.fin.items())
    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == 'select':
            return type('R', (), {'data': [copy.deepcopy(x) for x in rows if self._match(x)]})()
        if self.op == 'insert':
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            ins = []
            for p in payload:
                p = dict(p); p.setdefault('id', str(uuid.uuid4()))
                rows.append(p); ins.append(copy.deepcopy(p))
            return type('R', (), {'data': ins})()
        if self.op == 'delete':
            keep = [x for x in rows if not self._match(x)]
            self.store[self.table] = keep
            return type('R', (), {'data': []})()
        raise RuntimeError('no op')


class Schema:
    def __init__(self, store): self.store = store
    def table(self, name): return Q(self.store, name)


class FakeClient:
    def __init__(self):
        self.store = {'employees': [], 'stores': [], 'commission_plan': [], 'commission_plan_assignment': []}
    def schema(self, name): return Schema(self.store)


fake = FakeClient()
r.sb = lambda: fake


def reset():
    fake.store = {'employees': [], 'stores': [], 'commission_plan': [], 'commission_plan_assignment': []}


def seed_emp(org, name, role="", home_store="", email="", epay="", active=True):
    fake.store['employees'].append({"id": str(uuid.uuid4()), "org_id": org, "name": name, "role": role,
        "home_store": home_store, "email": email, "epay_salesperson": epay, "is_active": active})


def seed_store(org, code, addr, market):
    fake.store['stores'].append({"id": str(uuid.uuid4()), "org_id": org, "store_code": code,
        "address": addr, "market": market})


def seed_plan(org, name):
    pid = str(uuid.uuid4())
    fake.store['commission_plan'].append({"id": pid, "org_id": org, "name": name, "is_active": True})
    return pid


def seed_assign(org, plan_id, value, scope="employee"):
    fake.store['commission_plan_assignment'].append({"id": str(uuid.uuid4()), "org_id": org,
        "plan_id": plan_id, "scope": scope, "scope_value": value, "priority": 0})


def assigns(org):
    return [a for a in fake.store['commission_plan_assignment'] if a['org_id'] == org]


def call_bulk(body, org=ORG_A):
    return run_route(r.bulk_assign_commission_plan(body, org_id=org))


def call_roster(org=ORG_A, include_inactive=True):
    return run_route(
        r.commission_plan_roster(org_id=org, include_inactive=include_inactive))


results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond))); print(("PASS" if cond else "FAIL"), "|", name, "|", detail)


# ── 1. bulk multi-person upsert: N people → N assigned in ONE call, correct row shape ───────────────
reset()
pid = seed_plan(ORG_A, "Plan A")
resp = call_bulk({"plan_id": pid, "people": ["Ana Ruiz", "Bo Kim", "Cy Lee"]})
check("1 three people all 'assigned'", resp["summary"]["assigned"] == 3 and resp["summary"]["rows_inserted"] == 3, str(resp["summary"]))
check("1 three assignment rows written", len(assigns(ORG_A)) == 3)
row = assigns(ORG_A)[0]
check("1 row shape identical to single-assign (keys)",
      set(row.keys()) == {"id", "org_id", "plan_id", "scope", "scope_value", "priority"}, str(sorted(row.keys())))
check("1 rows are employee-scope, priority 0, correct plan",
      all(a["scope"] == "employee" and a["priority"] == 0 and a["plan_id"] == pid for a in assigns(ORG_A)))
check("1 per-person results echo the values",
      {r_["value"] for r_ in resp["results"]} == {"Ana Ruiz", "Bo Kim", "Cy Lee"})

# ── 2. org_id STAMPED = the caller's org (a NON-house tenant), not a constant ───────────────────────
reset()
pidB = seed_plan(ORG_B, "Plan B")
call_bulk({"plan_id": pidB, "people": ["Zed Q"]}, org=ORG_B)
check("2 inserted row stamped with the caller's org (ORG_B)",
      len(assigns(ORG_B)) == 1 and assigns(ORG_B)[0]["org_id"] == ORG_B and len(assigns(ORG_A)) == 0)

# ── 3. already-had-this-plan → no-op, no duplicate row ──────────────────────────────────────────────
reset()
pid = seed_plan(ORG_A, "Plan A")
seed_assign(ORG_A, pid, "Ana Ruiz")
resp = call_bulk({"plan_id": pid, "people": ["Ana Ruiz", "Bo Kim"]})
check("3 existing → already, new → assigned",
      resp["summary"]["already"] == 1 and resp["summary"]["assigned"] == 1, str(resp["summary"]))
check("3 no duplicate row for Ana (2 total: her old + Bo new)", len(assigns(ORG_A)) == 2)
check("3 Ana status is already_assigned",
      next(x for x in resp["results"] if x["value"] == "Ana Ruiz")["status"] == "already_assigned")

# ── 4. replace-existing: different plan, replace_existing=true → replaced + old removed ─────────────
reset()
p_old = seed_plan(ORG_A, "Old")
p_new = seed_plan(ORG_A, "New")
seed_assign(ORG_A, p_old, "Ana Ruiz")
resp = call_bulk({"plan_id": p_new, "people": ["Ana Ruiz"], "replace_existing": True})
check("4 replaced=1, rows_deleted=1, rows_inserted=1",
      resp["summary"]["replaced"] == 1 and resp["summary"]["rows_deleted"] == 1 and resp["summary"]["rows_inserted"] == 1, str(resp["summary"]))
ana = [a for a in assigns(ORG_A) if a["scope_value"] == "Ana Ruiz"]
check("4 Ana ends on EXACTLY the new plan (old gone)", len(ana) == 1 and ana[0]["plan_id"] == p_new, str(ana))

# ── 5. replace_existing=false leaves the different-plan person untouched (skipped_has_other) ────────
reset()
p_old = seed_plan(ORG_A, "Old")
p_new = seed_plan(ORG_A, "New")
seed_assign(ORG_A, p_old, "Ana Ruiz")
resp = call_bulk({"plan_id": p_new, "people": ["Ana Ruiz"], "replace_existing": False})
check("5 skipped_has_other, nothing written/deleted",
      resp["summary"]["skipped"] == 1 and resp["summary"]["rows_inserted"] == 0 and resp["summary"]["rows_deleted"] == 0, str(resp["summary"]))
check("5 Ana still on the OLD plan only", [a["plan_id"] for a in assigns(ORG_A)] == [p_old])

# ── 6. replace count reflects MULTIPLE existing direct plans for one person ─────────────────────────
reset()
p1 = seed_plan(ORG_A, "P1"); p2 = seed_plan(ORG_A, "P2"); p3 = seed_plan(ORG_A, "P3")
seed_assign(ORG_A, p1, "Ana Ruiz"); seed_assign(ORG_A, p2, "Ana Ruiz")
resp = call_bulk({"plan_id": p3, "people": ["Ana Ruiz"], "replace_existing": True})
check("6 both old direct plans removed (rows_deleted=2), one new inserted",
      resp["summary"]["rows_deleted"] == 2 and resp["summary"]["rows_inserted"] == 1, str(resp["summary"]))
check("6 per-person replaced count = 2",
      next(x for x in resp["results"] if x["value"] == "Ana Ruiz")["replaced"] == 2)
check("6 Ana ends on exactly P3", [a["plan_id"] for a in assigns(ORG_A)] == [p3])

# ── 7. empty selection / missing plan_id REJECTED (400) ─────────────────────────────────────────────
reset(); pid = seed_plan(ORG_A, "Plan A")
def expect_http(status, fn):
    try:
        fn(); return False
    except HTTPException as e:
        return e.status_code == status
check("7 empty people → 400", expect_http(400, lambda: call_bulk({"plan_id": pid, "people": []})))
check("7 whitespace-only people → 400", expect_http(400, lambda: call_bulk({"plan_id": pid, "people": ["", "  "]})))
check("7 missing plan_id → 400", expect_http(400, lambda: call_bulk({"plan_id": "", "people": ["Ana"]})))

# ── 8. unknown / cross-tenant plan_id → 404 (guards a stale or other-tenant plan) ───────────────────
reset()
pidA = seed_plan(ORG_A, "Plan A")
check("8 plan id from another org → 404 (not written into ORG_B)",
      expect_http(404, lambda: call_bulk({"plan_id": pidA, "people": ["Ana"]}, org=ORG_B)))
check("8 nothing written to ORG_B on 404", len(assigns(ORG_B)) == 0)

# ── 9. case/space-insensitive de-dupe of the selection → one insert ────────────────────────────────
reset(); pid = seed_plan(ORG_A, "Plan A")
resp = call_bulk({"plan_id": pid, "people": ["Ana Ruiz", "ana ruiz", "  Ana Ruiz  "]})
check("9 3 spellings collapse to 1 assigned row",
      resp["summary"]["assigned"] == 1 and len(assigns(ORG_A)) == 1, str(resp["summary"]))

# ── 10. accepts {value} objects as well as plain strings ───────────────────────────────────────────
reset(); pid = seed_plan(ORG_A, "Plan A")
resp = call_bulk({"plan_id": pid, "people": [{"value": "Ana Ruiz"}, "Bo Kim"]})
check("10 mixed object/string people both assigned", resp["summary"]["assigned"] == 2 and len(assigns(ORG_A)) == 2, str(resp["summary"]))

# ── 11. ROSTER: role + market + value(epay||name) + current_plans; facets ──────────────────────────
reset()
seed_store(ORG_A, "S1", "1 Main St", "North")
seed_store(ORG_A, "S2", "2 Oak Ave", "South")
seed_emp(ORG_A, "Ana Ruiz", role="Sales Rep", home_store="S1", email="ana@x.com")
seed_emp(ORG_A, "Bo Kim", role="Manager", home_store="2 Oak Ave", email="bo@x.com", epay="Kim, Bo")
pid = seed_plan(ORG_A, "Plan A")
seed_assign(ORG_A, pid, "Ana Ruiz")
ros = call_roster(ORG_A)
by = {p["name"]: p for p in ros["people"]}
check("11 roster returns both people", len(ros["people"]) == 2)
check("11 role carried through", by["Ana Ruiz"]["role"] == "Sales Rep" and by["Bo Kim"]["role"] == "Manager")
check("11 market resolves via store_code AND address", by["Ana Ruiz"]["market"] == "North" and by["Bo Kim"]["market"] == "South")
check("11 value = epay_salesperson || name",
      by["Ana Ruiz"]["value"] == "Ana Ruiz" and by["Bo Kim"]["value"] == "Kim, Bo")
check("11 current_plans reflects employee-scope assignment",
      [c["plan_name"] for c in by["Ana Ruiz"]["current_plans"]] == ["Plan A"] and by["Bo Kim"]["current_plans"] == [])
check("11 role/market facets sorted-distinct",
      ros["roles"] == ["Manager", "Sales Rep"] and ros["markets"] == ["North", "South"])

# ── 12. ROSTER current_plans matches on the epay value (what single-assign writes) ─────────────────
reset()
seed_emp(ORG_A, "Bo Kim", role="Manager", epay="Kim, Bo")
pid = seed_plan(ORG_A, "Plan A")
seed_assign(ORG_A, pid, "Kim, Bo")   # single-assign stored the epay value
ros = call_roster(ORG_A)
check("12 assignment on epay value surfaces as Bo's current plan",
      [c["plan_name"] for c in ros["people"][0]["current_plans"]] == ["Plan A"])

# ── 13. ROSTER org isolation: ORG_B sees none of ORG_A's roster / assignments ──────────────────────
reset()
seed_emp(ORG_A, "Ana Ruiz", role="Sales Rep")
pid = seed_plan(ORG_A, "Plan A"); seed_assign(ORG_A, pid, "Ana Ruiz")
seed_emp(ORG_B, "Zed Q", role="Rep")
ros_b = call_roster(ORG_B)
check("13 ORG_B roster = only its own person, no ORG_A plans",
      [p["name"] for p in ros_b["people"]] == ["Zed Q"] and ros_b["people"][0]["current_plans"] == [])

# ── 14. BULK org isolation: an ORG_A assignment does not affect ORG_B already/replace logic ─────────
reset()
pA = seed_plan(ORG_A, "Shared name"); seed_assign(ORG_A, pA, "Ana Ruiz")
pB = seed_plan(ORG_B, "Shared name")
resp = call_bulk({"plan_id": pB, "people": ["Ana Ruiz"]}, org=ORG_B)
check("14 same-named rep in ORG_B is a fresh 'assigned' (ORG_A row ignored)",
      resp["summary"]["assigned"] == 1 and len(assigns(ORG_B)) == 1, str(resp["summary"]))

# ── 15. include_inactive=false hides inactive; default (true) shows them ────────────────────────────
reset()
seed_emp(ORG_A, "Ana Ruiz", role="Rep", active=True)
seed_emp(ORG_A, "Old Rep", role="Rep", active=False)
check("15 default include_inactive=true shows both", len(call_roster(ORG_A)["people"]) == 2)
check("15 include_inactive=false hides the inactive rep",
      [p["name"] for p in call_roster(ORG_A, include_inactive=False)["people"]] == ["Ana Ruiz"])

npass = sum(1 for _, c in results if c); ntot = len(results)
print(f"\n==== {npass}/{ntot} checks PASS ====")
sys.exit(0 if npass == ntot else 1)
