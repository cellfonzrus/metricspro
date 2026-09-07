"""Offline proof (no live DB/network) for the 2026-08-06 mod-commission escalation (mod-people,
branch agent/people/store-mapping-dedupe-fix): live Luxelink defect — 19 of 20 stores duplicated in
commcalc.store_mapping, created by two bulk syncs 47 minutes apart on 2026-08-05 under two DIFFERENT
store_code naming schemes for the SAME physical address (a `LUX-<CITY>-<NAME>` set and the plain
storeops roster codes). ROOT CAUSE: `_sync_store_mapping`'s "already have it?" check was
`.in_("store_code", ...)`, while every real CONSUMER of commcalc.store_mapping keys on
`store_address` — a second sync under a different code for an address that already had a mapping row
was invisible to that check and inserted a duplicate.

FIX: the "already have it?" check is now the UNION of a store_code match OR a normalized
store_address match (see _sync_store_mapping's own docstring, router.py). Deliberately NOT a DB-level
`.upsert(on_conflict=...)` — commcalc.store_mapping's identity columns are nullable (the exact
ON-CONFLICT-against-a-nullable-unique-column trap that already bit prod once, 2026-08-04) — this stays
the original SELECT-existing-then-INSERT-only-new shape, just widened to check both columns.

Covers:
  A. THE HEADLINE CASE (explicit ask): syncing the SAME address under TWO DIFFERENT store_codes,
     across two SEPARATE calls (simulating the real 47-minutes-apart double sync) -> exactly ONE
     store_mapping row survives, under the FIRST code seen (never silently relabeled).
  B. Regression: the ORIGINAL rule (same store_code re-synced) still dedupes — an idempotent re-upload
     never creates a second row for the SAME code either.
  C. A genuinely different address (even under an unrelated code) is NOT over-merged — both survive.
  D. Address normalization: whitespace/case differences in the SAME address still dedupe
     ("123 Main St" vs " 123 MAIN ST ").
  E. No-address stores (the common case in this dataset — 25 of 26 house stores have none): each
     falls back to store_code as its own "address", so two DIFFERENT no-address stores never
     accidentally collide with each other.
  F. TWO NEW rows for the SAME never-before-seen address in ONE call (e.g. a single bulk upload that
     itself contains an internal duplicate) -> only the first of the two survives (the in-batch guard).
  G. Multi-tenant (RULE ONE): the SAME address under DIFFERENT orgs never cross-contaminates the
     dedupe — each org's sync is independent.
  H. End-to-end through the REAL POST /stores/bulk handler (not just the helper in isolation).

Run: `python3 harness_store_mapping_dedupe.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (same pattern as harness_timeclock_multisession.py) ──────────────────────
class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v))
        return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(str(x) for x in vals)))
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v):
                return False
            if kind == "in" and str(rv) not in v:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if getattr(self, "_mode", None) == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                row = dict(p)
                row.setdefault("id", f"row{len(rows) + len(out) + 1}")
                out.append(row)
            rows.extend(out)
            return Result(out)
        matched = [r for r in rows if self._matches(r)]
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
R._collect_markets = lambda org_id: []          # no-op RULE THREE canonicalizer for this harness
R._cscope.invalidate_market_index = lambda *a, **k: None

ORG = "org-lux-dedupe-1"


def _body(model, d):
    """Build the request model FastAPI hands the handler, instead of a plain dict.

    These endpoints were migrated from `body: dict` to a declared pydantic model, so the handler
    reads `body.<field>`. A probe passing a dict dies with AttributeError BEFORE reaching the logic
    under test — the harness then reads as "failing" while proving nothing. `model_validate`
    reproduces FastAPI's own call shape, including which fields count as explicitly set
    (`model_fields_set`), which several handlers branch on.
    """
    return model.model_validate(d)
OTHER_ORG = "org-house-dedupe-2"


def reset():
    fake.store.clear()


def mapping_rows(org=ORG):
    return [r for r in fake.store.get(("commcalc", "store_mapping"), []) if r.get("org_id") == org]


# ══ A: THE HEADLINE CASE — same address, two DIFFERENT store_codes, two SEPARATE sync calls ═══════
reset()
R._sync_store_mapping(ORG, [{"store_code": "LUX-NYC-UTICA", "address": "123 Utica Ave"}])
R._sync_store_mapping(ORG, [{"store_code": "Utica", "address": "123 Utica Ave"}])   # 47 min later, 2nd scheme
rows = mapping_rows()
check("A1 exactly ONE row survives for the SAME address synced under two different codes",
      len(rows) == 1, rows)
check("A2 the surviving row is the FIRST one seen (never silently relabeled to the 2nd sync's code)",
      rows and rows[0]["store_code"] == "LUX-NYC-UTICA", rows)

# ══ B: regression — same store_code re-synced (idempotent re-upload) still dedupes ═════════════════
reset()
R._sync_store_mapping(ORG, [{"store_code": "B-100", "address": "100 Main St"}])
R._sync_store_mapping(ORG, [{"store_code": "B-100", "address": "100 Main St"}])
rows = mapping_rows()
check("B1 re-syncing the SAME store_code is still idempotent (1 row, not 2)", len(rows) == 1, rows)

# ══ C: a genuinely different address must NOT be over-merged ═══════════════════════════════════════
reset()
R._sync_store_mapping(ORG, [{"store_code": "S1", "address": "1 First Ave"}])
R._sync_store_mapping(ORG, [{"store_code": "S2", "address": "2 Second Ave"}])
rows = mapping_rows()
check("C1 two genuinely different addresses both survive (the fix never over-merges)",
      {r["store_code"] for r in rows} == {"S1", "S2"}, rows)

# ══ D: address normalization — whitespace/case differences still dedupe ═══════════════════════════
reset()
R._sync_store_mapping(ORG, [{"store_code": "CODE-A", "address": "123 Main St"}])
R._sync_store_mapping(ORG, [{"store_code": "CODE-B", "address": " 123 main st "}])
rows = mapping_rows()
check("D1 whitespace/case-different renderings of the SAME address still dedupe to 1 row",
      len(rows) == 1, rows)

# ══ E: no-address stores (the dominant real-world case — 25/26 house stores) never collide ═════════
reset()
R._sync_store_mapping(ORG, [{"store_code": "T-902", "address": None}])
R._sync_store_mapping(ORG, [{"store_code": "T-957", "address": None}])
rows = mapping_rows()
check("E1 two DIFFERENT no-address stores (each falls back to its own code) both survive, no false merge",
      {r["store_code"] for r in rows} == {"T-902", "T-957"}, rows)
check("E2 the fallback address stored is each store's own code (unchanged pre-existing behavior)",
      {r["store_address"] for r in rows} == {"T-902", "T-957"}, rows)

# ══ F: an in-batch internal duplicate (two new rows, SAME never-before-seen address, ONE call) ═════
reset()
R._sync_store_mapping(ORG, [
    {"store_code": "DUP-A", "address": "9 Ninth St"},
    {"store_code": "DUP-B", "address": "9 Ninth St"},
])
rows = mapping_rows()
check("F1 an internal duplicate within a single sync call also collapses to 1 row (in-batch guard)",
      len(rows) == 1, rows)
check("F2 the surviving row is the first of the two in that batch",
      rows and rows[0]["store_code"] == "DUP-A", rows)

# ══ G: multi-tenant — the SAME address under DIFFERENT orgs is independent (RULE ONE) ══════════════
reset()
R._sync_store_mapping(ORG, [{"store_code": "SAME-CODE", "address": "1 Shared Address Rd"}])
R._sync_store_mapping(OTHER_ORG, [{"store_code": "SAME-CODE", "address": "1 Shared Address Rd"}])
rows_a = mapping_rows(ORG)
rows_b = mapping_rows(OTHER_ORG)
check("G1 org A has its own row for the shared address", len(rows_a) == 1, rows_a)
check("G2 org B ALSO gets its own row (never suppressed by org A's identical address)", len(rows_b) == 1, rows_b)
check("G3 org isolation: org A's fetch never returns org B's row and vice versa",
      all(r["org_id"] == ORG for r in rows_a) and all(r["org_id"] == OTHER_ORG for r in rows_b),
      (rows_a, rows_b))

# ══ H: end-to-end through the REAL POST /stores/bulk handler ═══════════════════════════════════════
reset()
R.bulk_create_stores(
    _body(R.BulkCreateStoresIn, {"stores": [{"store_code": "LUX-NYC-UTICA", "address": "123 Utica Ave"}]}), org_id=ORG)
R.bulk_create_stores(
    _body(R.BulkCreateStoresIn, {"stores": [{"store_code": "Utica2", "address": "123 Utica Ave"}]}), org_id=ORG)
rows = mapping_rows()
check("H1 end-to-end via POST /stores/bulk: a re-upload under a 2nd naming scheme still yields 1 mapping row",
      len(rows) == 1, rows)
check("H1b the storeops.stores TABLE itself still gets BOTH rows (this fix only narrows store_mapping's "
      "propagation — it never blocks creating the 2nd storeops.stores row itself, a separate/legitimate "
      "action the admin took)",
      len(fake.store.get(("storeops", "stores"), [])) == 2, fake.store.get(("storeops", "stores")))


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
