"""Harness for the SSOT guard fix — `_sync_store_mapping_update` is now an APP-SIDE UPSERT
(design blueprint Part 3d.1 / proof #2). No live DB — an in-memory fake Supabase client.

Proves:
  1. INSERT BRANCH (the 1115-Liberty regression): a store with NO commcalc.store_mapping row gets one
     CREATED, with the correct market, when its market is edited via PATCH /stores. The orphan class
     (a storeops.stores row whose market never reached store_mapping → never reached asset_ledger) is
     closed.
  2. is_active on a mapping-less store also lands (row created, is_active applied).
  3. UPDATE BRANCH is byte-identical to before: a store that ALREADY has a mapping row is UPDATED in
     place, no second row inserted, and a control field-only edit still touches nothing.
  4. Entity + aliases are registered on the write (guard fix 3d.2) — additive, backend-only.

Run:  python harness_store_mapping_upsert.py
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

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def eq(self, k, v):
        self.filters.append((k, v)); return self

    def limit(self, *_a, **_k):
        return self

    def _matches(self, row):
        return all(str(row.get(k)) == str(v) for k, v in self.filters)

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            rows.extend(dict(r) for r in payload)
            return Result([dict(r) for r in payload])
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
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

    def table(self, t):
        return FakeSchema(self, "storeops").table(t)

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402
from app.core import identity as _identity  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-up-1"


def sm_rows(org_id, code):
    rows = fake.store.get(("commcalc", "store_mapping"), [])
    return [r for r in rows if r.get("org_id") == org_id and r.get("store_code") == code]


# ══ 1: mapping-less store (B-1115 orphan) — market edit CREATES the mapping row ════════════════════
fake.store.clear(); _identity.invalidate()
fake.seed("storeops", "stores", [
    {"id": 1, "org_id": ORG, "store_code": "B-1115", "address": "1115 Liberty", "market": "LI",
     "is_active": True, "entity_id": "ent-1115"},
])
fake.seed("commcalc", "store_mapping", [])   # THE ORPHAN: no mapping row at all
check("1a precondition: B-1115 has NO store_mapping row", sm_rows(ORG, "B-1115") == [], sm_rows(ORG, "B-1115"))
R.update_store(1, {"market": "LI"}, org_id=ORG)
created = sm_rows(ORG, "B-1115")
check("1b market edit CREATES a store_mapping row (orphan closed)", len(created) == 1, created)
check("1c created row carries market LI", created and created[0].get("market") == "LI", created)
check("1d created row carries the store_address", created and created[0].get("store_address") == "1115 Liberty",
      created)

# ══ 2: is_active on a mapping-less store lands too ════════════════════════════════════════════════
fake.store.clear(); _identity.invalidate()
fake.seed("storeops", "stores", [
    {"id": 2, "org_id": ORG, "store_code": "B-777", "address": "777 Main", "market": "PA",
     "is_active": True, "entity_id": "ent-777"},
])
fake.seed("commcalc", "store_mapping", [])
R.update_store(2, {"is_active": False}, org_id=ORG)
row = sm_rows(ORG, "B-777")
check("2a is_active edit on mapping-less store CREATES the row", len(row) == 1, row)
check("2b created row is_active is False (toggled value applied, not the default)",
      row and row[0].get("is_active") is False, row)
check("2c created row carries market PA from the live stores row (patch had no market)",
      row and row[0].get("market") == "PA", row)

# ══ 3: store WITH a mapping row — UPDATE in place, byte-identical to the old update-only path ═══════
fake.store.clear(); _identity.invalidate()
fake.seed("storeops", "stores", [
    {"id": 3, "org_id": ORG, "store_code": "T-902", "address": "902 Main", "market": "North",
     "is_active": True, "entity_id": "ent-902"},
])
fake.seed("commcalc", "store_mapping", [
    {"org_id": ORG, "store_code": "T-902", "store_address": "902 Main", "market": "North", "is_active": True},
])
R.update_store(3, {"market": "South"}, org_id=ORG)
after = sm_rows(ORG, "T-902")
check("3a still exactly ONE mapping row (no duplicate inserted)", len(after) == 1, after)
check("3b existing row UPDATED in place to market South", after and after[0].get("market") == "South", after)

# control: a field store_mapping doesn't carry never touches it
before = dict(sm_rows(ORG, "T-902")[0])
R.update_store(3, {"monthly_target": 5000}, org_id=ORG)
check("3c a monthly_target-only edit leaves the mapping row byte-identical",
      sm_rows(ORG, "T-902")[0] == before, (before, sm_rows(ORG, "T-902")[0]))

# ══ 4: entity + code/address aliases registered on the write (guard 3d.2, additive) ════════════════
aliases = [r for r in fake.store.get(("storeops", "store_alias"), []) if r.get("entity_id") == "ent-902"]
kinds = {(r.get("alias_kind"), r.get("alias_value")) for r in aliases}
check("4a code alias written for the store", ("code", "T-902") in kinds, kinds)
check("4b address alias written for the store", ("address", "902 Main") in kinds, kinds)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
