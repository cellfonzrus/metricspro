"""Harness for the SSOT backfill seed — app/core/identity_backfill.py (blueprint Part 3e / proof #3).
No live DB — an in-memory fake Supabase client.

Proves:
  1. IDEMPOTENT: running the 3b seed twice inserts ZERO rows the second time (WHERE-NOT-EXISTS on the
     unique index).
  2. 1:1 ENTITY COVERAGE: every storeops.stores store_code and every storeops.employees employee_id
     gets exactly ONE entity_id (one 'code'/'business_id' alias each, all distinct entities).
  3. 1115-LIBERTY: a store with NO store_mapping row still gets a code AND an address alias from its
     stores row alone.
  4. TWINS STAGED, NOT MERGED: a carrier/LUX twin sharing an address is written to
     store_alias_proposal, and is NOT auto-attached as a resolving alias of the primary entity.

Run:  python harness_backfill_idempotent.py
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload = None, None

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def eq(self, k, v):
        self.filters.append((k, v)); return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            rows.extend(dict(r) for r in payload)
            return Result([dict(r) for r in payload])
        matched = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in self.filters)]
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchema(self, name)

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


from app.core import identity_backfill as BF  # noqa: E402
from app.core import identity as I  # noqa: E402

ORG = "org-bf-1"

fake = FakeClient()
fake.seed("storeops", "stores", [
    {"entity_id": "e-1115", "org_id": ORG, "store_code": "B-1115", "address": "1115 Liberty"},   # ORPHAN
    {"entity_id": "e-902", "org_id": ORG, "store_code": "T-902", "address": "902 Main St"},
    {"entity_id": "e-penn", "org_id": ORG, "store_code": "957", "address": "957 Pennsylvania Ave"},
    {"entity_id": "e-lux", "org_id": ORG, "store_code": "LUX-NY-PENN", "address": "957 Pennsylvania Ave"},  # TWIN
])
fake.seed("commcalc", "store_mapping", [
    {"org_id": ORG, "store_code": "T-902", "store_address": "902 Main Street", "salesforce_id": "SF-902"},
    {"org_id": ORG, "store_code": "957", "store_address": "957 Pennsylvania Ave", "salesforce_id": "SF-957"},
    # deliberately NO row for B-1115 (the orphan)
])
fake.seed("commcalc", "store_aliases", [
    {"org_id": ORG, "alias": "3 Palisade Ave Yonkers", "store_code": "T-902"},
])
fake.seed("storeops", "store_merchant_id", [
    {"org_id": ORG, "store_code": "T-902", "merchant_id": "MID-902"},
])
fake.seed("storeops", "employees", [
    {"entity_id": "p-rob", "org_id": ORG, "employee_id": "E45", "id": 45, "name": "Robert Smith",
     "epay_login": "rsmith", "epay_salesperson": "Bob Smith"},
    {"entity_id": "p-ab", "org_id": ORG, "employee_id": "E70", "id": 70, "name": "Abdul Kakar",
     "epay_login": "akakar", "epay_salesperson": ""},
])
fake.seed("commcalc", "name_map", [
    {"org_id": ORG, "epay_login": "bsmith", "epay_salesperson": "Bob Smith", "storeops_name": "Robert Smith"},
])
fake.seed("commcalc", "rep_aliases", [
    {"org_id": ORG, "alias": "Abdul K", "canonical": "Abdul Kakar"},
])
fake.seed("storeops", "store_alias", [])
fake.seed("storeops", "employee_alias", [])
fake.seed("storeops", "store_alias_proposal", [])

# ══ 1: first seed inserts rows ════════════════════════════════════════════════════════════════════
r1 = BF.seed(fake, ORG)
check("1a first seed inserts store aliases", r1["store_aliases_inserted"] > 0, r1)
check("1b first seed inserts employee aliases", r1["employee_aliases_inserted"] > 0, r1)
check("1c first seed stages at least one twin proposal", r1["proposals_inserted"] >= 1, r1)

# ══ 2: second seed is a no-op ═════════════════════════════════════════════════════════════════════
r2 = BF.seed(fake, ORG)
check("2a second seed inserts ZERO store aliases (idempotent)", r2["store_aliases_inserted"] == 0, r2)
check("2b second seed inserts ZERO employee aliases (idempotent)", r2["employee_aliases_inserted"] == 0, r2)
check("2c second seed inserts ZERO proposals (idempotent)", r2["proposals_inserted"] == 0, r2)

sa = fake.store[("storeops", "store_alias")]
ea = fake.store[("storeops", "employee_alias")]
props = fake.store[("storeops", "store_alias_proposal")]

# ══ 3: 1:1 entity coverage — one code alias per store, one business_id per employee, all distinct ══
code_aliases = [r for r in sa if r.get("alias_kind") == "code"]
code_entities = {r["entity_id"] for r in code_aliases}
check("3a exactly one 'code' alias per store (4 stores)", len(code_aliases) == 4, len(code_aliases))
check("3b every store_code maps to a DISTINCT entity_id", len(code_entities) == 4, code_entities)
biz_aliases = [r for r in ea if r.get("alias_kind") == "business_id"]
check("3c exactly one 'business_id' alias per employee (2)", len(biz_aliases) == 2, len(biz_aliases))
check("3d every employee_id maps to a DISTINCT entity_id",
      len({r["entity_id"] for r in biz_aliases}) == 2, biz_aliases)

# ══ 4: 1115-Liberty gets code + address alias from the stores row alone (no mapping row) ═══════════
o = {(r["alias_kind"], r["alias_value"]) for r in sa if r.get("entity_id") == "e-1115"}
check("4a B-1115 code alias from stores row", ("code", "B-1115") in o, o)
check("4b B-1115 address alias from stores row", ("address", "1115 Liberty") in o, o)

# ══ 5: twin staged, not merged ════════════════════════════════════════════════════════════════════
twin_prop = [p for p in props if p.get("twin_code") == "LUX-NY-PENN"]
check("5a LUX twin staged as a proposal against the 957 entity",
      twin_prop and twin_prop[0].get("entity_id") == "e-penn", twin_prop)
# the twin is NOT auto-attached as a resolving alias of the primary entity (only its OWN entity's code)
merged_alias = [r for r in sa if r.get("alias_value") == "LUX-NY-PENN" and r.get("entity_id") == "e-penn"]
check("5b LUX twin is NOT auto-merged onto entity 957 (no resolving alias there)", merged_alias == [],
      merged_alias)

# the resolver, reading the seeded aliases, resolves the seeded vocabulary correctly
I.invalidate()
idx = I._build_store_index(fake.store[("storeops", "stores")],
                           fake.store[("commcalc", "store_mapping")], sa)
r = I._resolve_store_in_index(idx, "3 Palisade Ave Yonkers")
check("5c seeded sales_file_spelling alias resolves to T-902", r and r.entity_id == "e-902",
      r and r.entity_id)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
