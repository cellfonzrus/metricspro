"""Offline proof harness — asset-8 critical review (2026-07-30), branch
agent/asset/inventory-b2b-import.

CONTEXT: this branch's original commit (5f310d1, "PARKED WIP — NOT reviewed, NOT shipped") was a
first, uncommitted-then-committed attempt at "b2b-inventory upload carries a $ value into
commcalc.inventory_value (Balance Sheet link)". A SECOND, independent attempt at the exact same
task (branch agent/asset/inventory-b2b-import2, commit 9eb35f3) was built later, reviewed, and
SHIPPED to origin/main — it already has everything 5f310d1 has (canonicalized store key via
_store_canon_map/_canon_store, the same commcalc.inventory_value upsert shape) plus more
(canonicalizes at parse time instead of only for the value side, one extra "inventory cost" value
-column synonym). Diffing 5f310d1 against origin/main's current router.py confirms this: rebasing
5f310d1 onto origin/main conflicts because origin/main's `/b2b-inventory/upload` already contains
an equivalent-or-better version of every line 5f310d1 added — the WIP is fully superseded, not
missing anything.

This harness proves TWO things about what's ALREADY on origin/main (not a re-implementation of the
superseded WIP):

  (1) money-adjacent safety, exactly as asked: the write is org-scoped, and NEVER touches
      commcalc.inventory_value.manual_value (the finance-owned "admin override wins" column, set
      only via PUT /accounts/inventory-values) — confirmed against the REAL upsert payload shape,
      not just by reading the code.

  (2) a genuine gap found during this review and fixed in this package: the endpoint's upsert had
      no ordering guard, so re-uploading an OLDER b2bsoft export (e.g. by mistake, or a stale email
      attachment) after a NEWER one already landed would silently REGRESS the Balance Sheet's
      swept_value to a stale number. Fixed with a new `_iso_date_key` comparison + a batched
      pre-read of the existing as_of_date for just the touched stores — never a full-table scan —
      that skips (never blocks the rest of the upload) any store whose on-file as_of_date is
      already newer, and reports exactly which stores were skipped and why
      (`inventory_value_skipped_stale`). Fails OPEN (does not block) if either date can't be read
      as ISO YYYY-MM-DD, matching the file's existing "never let an edge case break an
      already-working upload" posture.

No database, no network: a small recording fake Supabase/PostgREST client (real upsert-by-conflict-
key semantics: only the columns present in the payload are overwritten on an existing row — exactly
what a real `INSERT ... ON CONFLICT (cols) DO UPDATE SET <payload-columns only>` does, which is the
crux of proof (1) above) feeds the REAL module code directly.

Run:  cd backend && python3 harness_asset_b2b_inventory_value.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


ORG_A = "00000000-0000-0000-0000-0000000000aa"
ORG_B = "00000000-0000-0000-0000-0000000000bb"


# ── minimal fake supabase/postgrest client, with REAL upsert-by-conflict-key semantics ─────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, schema, table, log):
        self.store, self.schema, self.table, self.log = store, schema, table, log
        self.filters = []
        self._op = "select"
        self._payload = None
        self._on_conflict = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, v):
        self.filters.append(("in", k, v)); return self

    def insert(self, rows):
        self._op, self._payload = "insert", rows; return self

    def delete(self):
        self._op = "delete"; return self

    def upsert(self, rec, on_conflict=None):
        self._op, self._payload, self._on_conflict = "upsert", rec, on_conflict; return self

    def _keep(self, r):
        for op, k, v in self.filters:
            if op == "eq" and r.get(k) != v:
                return False
            if op == "in" and r.get(k) not in v:
                return False
        return True

    def execute(self):
        key = (self.schema, self.table)
        rows = self.store.setdefault(key, [])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for r in payload:
                rows.append(dict(r))
            self.log.append(("insert", key, payload))
            return _Resp(payload)
        if self._op == "delete":
            kept = [r for r in rows if not self._keep(r)]
            self.store[key] = kept
            self.log.append(("delete", key, list(self.filters)))
            return _Resp(None)
        if self._op == "upsert":
            rec = self._payload
            conflict_cols = (self._on_conflict or "").split(",") if self._on_conflict else list(rec.keys())
            match = None
            for r in rows:
                if all(r.get(c) == rec.get(c) for c in conflict_cols):
                    match = r
                    break
            if match is not None:
                # Real Postgres ON CONFLICT DO UPDATE SET <payload columns> — only overwrites the
                # columns present in `rec`; every OTHER column on the existing row (e.g.
                # manual_value, which this endpoint's payload never includes) is left untouched.
                match.update(rec)
            else:
                rows.append(dict(rec))
            self.log.append(("upsert", key, dict(rec)))
            return _Resp([dict(rec)])
        # select
        out = [r for r in rows if self._keep(r)]
        return _Resp(out)


class _Table:
    def __init__(self, store, schema, table, log):
        self.store, self.schema, self.table, self.log = store, schema, table, log

    def select(self, *a, **k):
        return _Q(self.store, self.schema, self.table, self.log).select(*a, **k)

    def insert(self, rows):
        return _Q(self.store, self.schema, self.table, self.log).insert(rows)

    def delete(self):
        return _Q(self.store, self.schema, self.table, self.log).delete()

    def upsert(self, rec, on_conflict=None):
        return _Q(self.store, self.schema, self.table, self.log).upsert(rec, on_conflict=on_conflict)


class _Schema:
    def __init__(self, store, schema, log):
        self.store, self.schema, self.log = store, schema, log

    def table(self, name):
        return _Table(self.store, self.schema, name, self.log)


class FakeClient:
    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.log = []

    def schema(self, name):
        return _Schema(self.store, name, self.log)


class _BrokenTable:
    """Simulates commcalc.inventory_value not existing yet (pre-migration-026 tenant) —
    every call raises, same as a real PGRST205 schema-cache-miss."""
    def select(self, *a, **k):
        raise Exception("PGRST205 relation \"commcalc.inventory_value\" does not exist")

    def upsert(self, *a, **k):
        raise Exception("PGRST205 relation \"commcalc.inventory_value\" does not exist")


class BrokenInventoryValueClient(FakeClient):
    def schema(self, name):
        real = super().schema(name)
        if name == "commcalc":
            orig_table = real.table

            def table(tname):
                if tname == "inventory_value":
                    return _BrokenTable()
                return orig_table(tname)
            real.table = table
        return real


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.asset import router as R  # noqa: E402

ROWS_VALUE_ONLY = [
    {"store": "1800 Great Neck Rd", "value": "1000"},
    {"store": "1800 Great Neck Rd", "value": "500"},   # same store, 2 rows -> summed
]

print("1. Org isolation — upload for org A never touches org B's inventory_value rows")
c1 = FakeClient(store={
    ("commcalc", "store_mapping"): [],
    ("commcalc", "inventory_value"): [
        {"org_id": ORG_B, "store": "1800 Great Neck Rd", "swept_value": 999, "manual_value": None,
         "as_of_date": "2026-06-01", "source": "asset_b2b_upload"},
    ],
})
R.sb = lambda: c1
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-01", "rows": ROWS_VALUE_ONLY}, org_id=ORG_A))
rows_a = [r for r in c1.store[("commcalc", "inventory_value")] if r["org_id"] == ORG_A]
rows_b = [r for r in c1.store[("commcalc", "inventory_value")] if r["org_id"] == ORG_B]
ok("1a org A gets exactly one summed row (1000+500)",
   len(rows_a) == 1 and rows_a[0]["swept_value"] == 1500.0, rows_a)
ok("1b org B's pre-existing row is byte-for-byte untouched",
   rows_b == [{"org_id": ORG_B, "store": "1800 Great Neck Rd", "swept_value": 999, "manual_value": None,
               "as_of_date": "2026-06-01", "source": "asset_b2b_upload"}], rows_b)
ok("1c response totals match org A's write only",
   resp["inventory_value_stores"] == 1 and resp["inventory_value_total"] == 1500.0, resp)

print("\n2. manual_value is NEVER clobbered by this endpoint (the money-adjacent guardrail)")
c2 = FakeClient(store={
    ("commcalc", "store_mapping"): [],
    ("commcalc", "inventory_value"): [
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "swept_value": 100, "manual_value": 42000.0,
         "as_of_date": "2026-06-01", "source": "manual", "note": "physical count 06-01"},
    ],
})
R.sb = lambda: c2
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-15", "rows": ROWS_VALUE_ONLY}, org_id=ORG_A))
row = [r for r in c2.store[("commcalc", "inventory_value")] if r["org_id"] == ORG_A][0]
ok("2a swept_value DID update (newer as_of_date, normal path)", row["swept_value"] == 1500.0, row)
ok("2b manual_value is EXACTLY what it was before (42000.0, never touched)",
   row["manual_value"] == 42000.0, row)
ok("2c the admin's note is also untouched (proves the upsert payload never included it)",
   row["note"] == "physical count 06-01", row)
ok("2d this endpoint's own upsert payload literally never contains the key 'manual_value'",
   all("manual_value" not in call[2] for call in c2.log if call[0] == "upsert"), c2.log)

print("\n3. Stale as_of_date guard — an older re-upload never regresses a newer swept_value")
c3 = FakeClient(store={
    ("commcalc", "store_mapping"): [],
    ("commcalc", "inventory_value"): [
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "swept_value": 5000.0, "manual_value": None,
         "as_of_date": "2026-07-20", "source": "asset_b2b_upload"},
    ],
})
R.sb = lambda: c3
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-01", "rows": ROWS_VALUE_ONLY}, org_id=ORG_A))
row = [r for r in c3.store[("commcalc", "inventory_value")] if r["org_id"] == ORG_A][0]
ok("3a swept_value stays at the NEWER value (5000), not overwritten by the older upload's 1500",
   row["swept_value"] == 5000.0, row)
ok("3b as_of_date on file is untouched (still 2026-07-20)", row["as_of_date"] == "2026-07-20", row)
ok("3c response reports exactly 0 stores written, 0 total (skipped, not silently succeeded)",
   resp["inventory_value_stores"] == 0 and resp["inventory_value_total"] == 0.0, resp)
ok("3d response's inventory_value_skipped_stale names the store + both dates, never silent",
   resp["inventory_value_skipped_stale"] == [{"store": "1800 Great Neck Rd",
       "existing_as_of_date": "2026-07-20", "attempted_as_of_date": "2026-07-01"}], resp)

print("\n4. Same-date re-upload (a same-day correction) is NOT blocked by the stale guard")
c4 = FakeClient(store={
    ("commcalc", "store_mapping"): [],
    ("commcalc", "inventory_value"): [
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "swept_value": 1000.0, "manual_value": None,
         "as_of_date": "2026-07-01", "source": "asset_b2b_upload"},
    ],
})
R.sb = lambda: c4
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-01", "rows": ROWS_VALUE_ONLY}, org_id=ORG_A))
row = [r for r in c4.store[("commcalc", "inventory_value")] if r["org_id"] == ORG_A][0]
ok("4a equal as_of_date corrects the value (1500), not blocked", row["swept_value"] == 1500.0, row)
ok("4b nothing reported as skipped-stale", resp["inventory_value_skipped_stale"] == [], resp)

print("\n5. First-ever upload for a store (no existing row) is never blocked")
c5 = FakeClient(store={("commcalc", "store_mapping"): [], ("commcalc", "inventory_value"): []})
R.sb = lambda: c5
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-01", "rows": ROWS_VALUE_ONLY}, org_id=ORG_A))
ok("5a new row created", resp["inventory_value_stores"] == 1 and resp["inventory_value_total"] == 1500.0, resp)
ok("5b nothing reported as skipped-stale", resp["inventory_value_skipped_stale"] == [], resp)

print("\n6. Malformed / missing as_of_date fails OPEN (never blocks the write)")
c6 = FakeClient(store={
    ("commcalc", "store_mapping"): [],
    ("commcalc", "inventory_value"): [
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "swept_value": 5000.0, "manual_value": None,
         "as_of_date": "not-a-date", "source": "asset_b2b_upload"},
    ],
})
R.sb = lambda: c6
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-01", "rows": ROWS_VALUE_ONLY}, org_id=ORG_A))
row = [r for r in c6.store[("commcalc", "inventory_value")] if r["org_id"] == ORG_A][0]
ok("6a garbage existing as_of_date -> guard can't read it -> write proceeds (fails open)",
   row["swept_value"] == 1500.0, row)
ok("6b _iso_date_key itself returns None for garbage / None / short strings",
   R._iso_date_key("not-a-date") is None and R._iso_date_key(None) is None
   and R._iso_date_key("2026-07") is None and R._iso_date_key("2026-07-01") == "2026-07-01")

print("\n7. Degrade path — commcalc.inventory_value missing (pre-migration-026) never breaks the upload")
c7 = BrokenInventoryValueClient(store={("commcalc", "store_mapping"): []})
R.sb = lambda: c7
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-01", "rows": ROWS_VALUE_ONLY}, org_id=ORG_A))
ok("7a degrades to 0 stores / 0 total / 0 skipped, no exception raised",
   resp["inventory_value_stores"] == 0 and resp["inventory_value_total"] == 0.0
   and resp["inventory_value_skipped_stale"] == [], resp)

print("\n8. Regression check — canonicalization (store_mapping) + category/qty recon unaffected")
c8 = FakeClient(store={
    ("commcalc", "store_mapping"): [{"org_id": ORG_A, "store_address": "1800 Great Neck Rd"}],
    ("commcalc", "inventory_value"): [],
    ("commcalc", "b2b_inventory"): [],
})
R.sb = lambda: c8
mixed_rows = [
    {"store": "1800 great neck rd", "category": "iPhone", "qty": "3", "value": "900"},   # lower-case variant
    {"store": "1800 Great Neck Rd", "category": "iPhone", "qty": "2", "value": "600"},   # canon variant
    {"store": "1800 Great Neck Rd", "category": "Accessory", "qty": "5"},                # unmappable bucket, no value
]
resp = _run(R.upload_b2b_inventory({"as_of_date": "2026-07-01", "rows": mixed_rows}, org_id=ORG_A))
inv_rows = [r for r in c8.store[("commcalc", "inventory_value")] if r["org_id"] == ORG_A]
ok("8a both store-spelling variants land under ONE canonical inventory_value row",
   len(inv_rows) == 1 and inv_rows[0]["store"] == "1800 Great Neck Rd" and inv_rows[0]["swept_value"] == 1500.0,
   inv_rows)
b2b_rows = c8.store[("commcalc", "b2b_inventory")]
ok("8b qty/category recon still aggregates the 2 mappable iPhone rows (3+2=5) under the canon store",
   any(r["store"] == "1800 Great Neck Rd" and r["category"] == "iphone" and r["qty"] == 5 for r in b2b_rows),
   b2b_rows)
ok("8c the unmappable 'Accessory' row (no bucket) is reported skipped, doesn't block the value write",
   resp["skipped"] == 1 and resp["inventory_value_stores"] == 1, resp)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
