"""Proof for /mrc-mapping/bulk-classify: write-in filter semantics, bulk apply in one call, cross-menu
conflict guard (blocks whole batch + accurate list), money-safety (existing MRC $ preserved, item_mapping
never written = sync deferred), org isolation. Drives the REAL endpoint against a FakeClient."""
import sys, asyncio, uuid, copy
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fastapi import HTTPException
import app.modules.commcalc.router as r

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000bb"

# ── FakeClient emulating the supabase query chain over two tables ──────────────────────────────────
class Q:
    def __init__(self, store, table):
        self.store = store; self.table = table
        self.f = {}; self.op = None; self.payload = None; self._order = None
    def select(self, *a, **k): self.op = 'select'; return self
    def insert(self, payload): self.op = 'insert'; self.payload = payload; return self
    def update(self, payload): self.op = 'update'; self.payload = payload; return self
    def delete(self): self.op = 'delete'; return self
    def eq(self, c, v): self.f[c] = v; return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def _match(self, row): return all(row.get(c) == v for c, v in self.f.items())
    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == 'select':
            return type('R', (), {'data': [copy.deepcopy(x) for x in rows if self._match(x)]})()
        if self.op == 'insert':
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            ins = []
            for p in payload:
                p = dict(p)
                p.setdefault('id', str(uuid.uuid4()))
                rows.append(p); ins.append(copy.deepcopy(p))
            return type('R', (), {'data': ins})()
        if self.op == 'update':
            n = 0
            for x in rows:
                if self._match(x):
                    x.update(self.payload); n += 1
            return type('R', (), {'data': [], 'count': n})()
        if self.op == 'delete':
            keep = [x for x in rows if not self._match(x)]
            self.store[self.table] = keep
            return type('R', (), {'data': []})()
        raise RuntimeError('no op')

class Schema:
    def __init__(self, store): self.store = store
    def table(self, name): return Q(self.store, name)

class FakeClient:
    def __init__(self): self.store = {'product_mrc': [], 'item_mapping': []}
    def schema(self, name): return Schema(self.store)

fake = FakeClient()
r.sb = lambda: fake

def seed_item(org, item_key, sales_cat, item_desc=None):
    fake.store['item_mapping'].append({
        "id": str(uuid.uuid4()), "org_id": org, "item_key": item_key,
        "item_desc": item_desc or item_key, "sales_category": sales_cat, "kpi_category": None,
        "item_type": "unclassified"})

def seed_mrc(org, plan, mrc, classification=None, carrier_id=None, match_op="equals"):
    fake.store['product_mrc'].append({
        "id": str(uuid.uuid4()), "org_id": org, "carrier_id": carrier_id, "plan_pattern": plan,
        "match_op": match_op, "mrc": mrc, "priority": 100, "is_active": True,
        "classification": classification, "confirmed": False})

def mrc_rows(org):
    return [x for x in fake.store['product_mrc'] if x['org_id'] == org]
def item_rows(org):
    return [x for x in fake.store['item_mapping'] if x['org_id'] == org]

def call(body, org=ORG_A):
    return asyncio.get_event_loop().run_until_complete(
        r.mrc_mapping_bulk_classify(body, authorization="", org_id=org))

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond))); print(("PASS" if cond else "FAIL"), "|", name, "|", detail)

# ── 1. write-in filter semantics (mirrors the client predicate over the plan strings) ──────────────
sample = ["RTR $50 Unlimited", "RTR Bring-Your-Own", "Boost Infinite 60", "rtr promo", "iPhone 16"]
def client_filter(cands, q):  # exactly the page's: !q || plan.toLowerCase().includes(q.toLowerCase())
    q = q.strip().lower()
    return [c for c in cands if (not q) or (q in c.lower())]
narrowed = client_filter(sample, "rtr")
check("1 filter 'rtr' narrows to the 3 RTR plans (case-insensitive)",
      narrowed == ["RTR $50 Unlimited", "RTR Bring-Your-Own", "rtr promo"], str(narrowed))
check("1 empty filter shows all", client_filter(sample, "") == sample)

# ── 2. bulk apply N rows in ONE call (no conflicts) ────────────────────────────────────────────────
fake.store = {'product_mrc': [], 'item_mapping': []}
items = [{"plan": p, "mrc": 50} for p in ["RTR A", "RTR B", "RTR C"]]
resp = call({"items": items, "classification": "activation"})
check("2 bulk apply == N in one call", resp["applied"] == 3, str(resp))
rows = mrc_rows(ORG_A)
check("2 all rows got the classification", len(rows) == 3 and all(x["classification"] == "activation" for x in rows))
check("2 all rows confirmed=true", all(x.get("confirmed") is True for x in rows))
check("2 per-row result list returned", len(resp["results"]) == 3 and all(x["saved"] for x in resp["results"]))

# ── 3. de-dupe: same plan twice collapses to one ───────────────────────────────────────────────────
fake.store = {'product_mrc': [], 'item_mapping': []}
resp = call({"items": [{"plan": "RTR X"}, {"plan": "rtr x"}, {"plan": "RTR X"}], "classification": "upgrade"})
check("3 case-insensitive de-dupe (3 in → 1 applied)", resp["applied"] == 1 and len(mrc_rows(ORG_A)) == 1, str(resp))

# ── 4. conflict BLOCKS the whole batch + accurate conflict list + writes nothing ───────────────────
fake.store = {'product_mrc': [], 'item_mapping': []}
seed_item(ORG_A, "RTR A", "accessory")           # different category on the other menu
resp = call({"items": [{"plan": "RTR A"}, {"plan": "RTR B"}], "classification": "activation", "dry_run": True})
check("4 dry_run surfaces the conflict", len(resp["conflicts"]) == 1 and resp["would_apply"] == 0, str(resp))
c0 = resp["conflicts"][0]
check("4 conflict fields accurate",
      c0["plan"] == "RTR A" and c0["assigning"] == "activation" and c0["other_category"] == "accessory"
      and c0["other_menu"] == "Item / Model Mapping", str(c0))
blocked = False
try:
    call({"items": [{"plan": "RTR A"}, {"plan": "RTR B"}], "classification": "activation"})
except HTTPException as e:
    blocked = (e.status_code == 409 and e.detail.get("error") == "cross_menu_conflict"
               and e.detail.get("applied") == 0 and len(e.detail.get("conflicts", [])) == 1)
check("4 real write 409s whole batch (accurate detail)", blocked)
check("4 NOTHING written on block (RTR B not applied either)", len(mrc_rows(ORG_A)) == 0)

# ── 5. same category on both menus = NO conflict → applies ──────────────────────────────────────────
fake.store = {'product_mrc': [], 'item_mapping': []}
seed_item(ORG_A, "RTR A", "accessory")
resp = call({"items": [{"plan": "RTR A"}], "classification": "accessory"})
check("5 matching category is not a conflict → applied", resp["applied"] == 1 and not resp["conflicts"], str(resp))

# ── 6. misc_other is non-committal → never conflicts ───────────────────────────────────────────────
fake.store = {'product_mrc': [], 'item_mapping': []}
seed_item(ORG_A, "RTR A", "accessory")
resp = call({"items": [{"plan": "RTR A"}], "classification": "misc_other", "dry_run": True})
check("6 assigning misc_other never conflicts", resp["conflicts"] == [] and resp["would_apply"] == 1, str(resp))

# ── 7. existing MRC $ is PRESERVED (money-safe) when assigning only a category ──────────────────────
fake.store = {'product_mrc': [], 'item_mapping': []}
seed_mrc(ORG_A, "RTR A", 42.5, classification=None)   # pre-existing dollar, no category
resp = call({"items": [{"plan": "RTR A"}], "classification": "activation"})  # no mrc sent
row = mrc_rows(ORG_A)[0]
check("7 existing mrc $ preserved (not clobbered)", row["mrc"] == 42.5 and row["classification"] == "activation",
      f"mrc={row['mrc']} class={row['classification']}")
check("7 update-in-place (still 1 row)", len(mrc_rows(ORG_A)) == 1)

# ── 8. item_mapping is NEVER written (guard read-only; two-way sync deferred) ───────────────────────
fake.store = {'product_mrc': [], 'item_mapping': []}
seed_item(ORG_A, "RTR A", "activation")   # SAME category → no conflict → applies
before = copy.deepcopy(fake.store['item_mapping'])
call({"items": [{"plan": "RTR A"}], "classification": "activation"})
check("8 item_mapping byte-identical after apply (no write-through sync)",
      fake.store['item_mapping'] == before)

# ── 9. org isolation: org A conflict does NOT block org B; writes land in the right tenant ──────────
fake.store = {'product_mrc': [], 'item_mapping': []}
seed_item(ORG_A, "RTR A", "accessory")    # conflict exists ONLY for org A
resp_b = call({"items": [{"plan": "RTR A"}], "classification": "activation"}, org=ORG_B)
check("9 org B has no conflict from org A's item_mapping → applied", resp_b["applied"] == 1, str(resp_b))
check("9 write landed under org B only", len(mrc_rows(ORG_B)) == 1 and len(mrc_rows(ORG_A)) == 0)
# and org A would still block:
blocked_a = False
try:
    call({"items": [{"plan": "RTR A"}], "classification": "activation"}, org=ORG_A)
except HTTPException as e:
    blocked_a = e.status_code == 409
check("9 org A still blocked by its own item_mapping", blocked_a)

npass = sum(1 for _, c in results if c); ntot = len(results)
print(f"\n==== {npass}/{ntot} checks PASS ====")
sys.exit(0 if npass == ntot else 1)
