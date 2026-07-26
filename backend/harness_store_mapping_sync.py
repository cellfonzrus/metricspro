"""Offline proof (no live DB/network) for the 2026-07-25 owner-directed fix: "the stores for
t-902/531/218 etc are not getting inactive." Covers the `commcalc.store_mapping` sync gap found
during diagnosis: `storeops.sync_to_commcalc()` (migration 003) is a trigger FUNCTION that was never
actually ATTACHED to storeops.stores (no `CREATE TRIGGER ... ON storeops.stores` exists for it
anywhere in the migration history), and the app-side `_sync_store_mapping` only ever INSERTS a
mapping row for a brand-new store — an EXISTING store's is_active toggle never reached
commcalc.store_mapping at all. `_sync_store_mapping_update` (new) closes that gap from the PATCH
/stores/{id} write path.

Runs the REAL `update_store` + `_sync_store_mapping_update` against an in-memory fake Supabase
client. Run: `python3 harness_store_mapping_sync.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload = None, None

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def _matches(self, row):
        return all(str(row.get(k)) == str(v) for _, k, v in self.filters)

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
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
        return FakeQuery(self.store, ("storeops", t))

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-sm-1"
ORG2 = "org-sm-2"


def reset():
    fake.store.clear()
    fake.seed("storeops", "stores", [
        {"id": 1, "org_id": ORG, "store_code": "T-902", "address": "902 Main St", "market": "North", "is_active": True},
    ])
    fake.seed("commcalc", "store_mapping", [
        {"org_id": ORG, "store_code": "T-902", "store_address": "902 Main St", "market": "North", "is_active": True},
        {"org_id": ORG2, "store_code": "T-902", "store_address": "other tenant", "market": "Other", "is_active": True},
    ])


def sm(org_id, code):
    rows = fake.store[("commcalc", "store_mapping")]
    return next((r for r in rows if r.get("org_id") == org_id and r.get("store_code") == code), None)


# ══ 1: deactivating a store via PATCH propagates to commcalc.store_mapping.is_active ═══════════════
reset()
R.update_store(1, {"is_active": False}, org_id=ORG)
check("1a storeops.stores.is_active correctly set false",
      fake.store[("storeops", "stores")][0]["is_active"] is False,
      fake.store[("storeops", "stores")][0])
check("1b commcalc.store_mapping.is_active for THIS org's T-902 now false too (the actual gap fixed)",
      sm(ORG, "T-902")["is_active"] is False, sm(ORG, "T-902"))
check("1c a DIFFERENT tenant's same-named store_code T-902 is UNTOUCHED (org-scoped write, RULE ONE)",
      sm(ORG2, "T-902")["is_active"] is True, sm(ORG2, "T-902"))

# ══ 2: re-activating flows through identically ══════════════════════════════════════════════════
R.update_store(1, {"is_active": True}, org_id=ORG)
check("2a re-activating via PATCH flips store_mapping back to true",
      sm(ORG, "T-902")["is_active"] is True, sm(ORG, "T-902"))

# ══ 3: address/market changes also propagate (store_mapping's own store_address/market columns) ═══
reset()
R.update_store(1, {"address": "902 New Ave", "market": "South"}, org_id=ORG)
check("3a address change propagates to store_mapping.store_address",
      sm(ORG, "T-902")["store_address"] == "902 New Ave", sm(ORG, "T-902"))
check("3b market change propagates to store_mapping.market",
      sm(ORG, "T-902")["market"] == "South", sm(ORG, "T-902"))

# ══ 4: an update to a field store_mapping doesn't carry (e.g. monthly_target) never touches it ═════
reset()
before = dict(sm(ORG, "T-902"))
R.update_store(1, {"monthly_target": 5000}, org_id=ORG)
check("4a a monthly_target-only update never calls the sync (store_mapping row byte-identical)",
      sm(ORG, "T-902") == before, (before, sm(ORG, "T-902")))

# ══ 5: sync degrades gracefully if commcalc.store_mapping isn't reachable (never breaks the write) ═
reset()


class ExplodingSchema:
    def table(self, t):
        raise RuntimeError("simulated outage")


class ExplodingClient(FakeClient):
    def schema(self, name):
        if name == "commcalc":
            return ExplodingSchema()
        return FakeSchema(self, name)


old_get_supabase = R.get_supabase
R.get_supabase = lambda: ExplodingClient()
try:
    result = R.update_store(1, {"is_active": False}, org_id=ORG)
    check("5a store update itself still SUCCEEDS even when the store_mapping sync explodes",
          result.get("is_active") is False, result)
except Exception as e:
    check("5a store update itself still SUCCEEDS even when the store_mapping sync explodes", False, e)
R.get_supabase = old_get_supabase

# ══ 6: a foreign store_id (wrong org) is a 404, no sync attempted, no cross-tenant write ═══════════
reset()
from fastapi import HTTPException  # noqa: E402
try:
    R.update_store(1, {"is_active": False}, org_id=ORG2)
    check("6a foreign org's store_id raises 404", False, "no exception raised")
except HTTPException as e:
    check("6a foreign org's store_id raises 404", e.status_code == 404, e.detail)
check("6b the row is completely untouched by the rejected cross-tenant attempt",
      fake.store[("storeops", "stores")][0]["is_active"] is True, fake.store[("storeops", "stores")][0])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
