"""Offline proof (no live DB/network) for the 2026-07-28 owner directive: "populate the market with
a drop down menu... rather than typing in" on the StoreOps Admin Stores editor
(frontend/src/app/(platform)/storeops/admin/page.tsx). RULE THREE (pick-don't-type).

Covers the new backend pieces:
  - `_collect_markets(org_id)` — distinct market vocabulary sourced from BOTH storeops.stores.market
    AND commcalc.store_mapping.market (per the dispatch: offer distinct values from both so the two
    vocabularies can't diverge silently), deduped case-insensitively, canonical casing = most-common
    variant, blanks excluded, org-isolated.
  - `GET /storeops/markets` — the dropdown-options endpoint.
  - `_canonicalize_market(value, canonical_markets)` — normalize-on-save (btrim; case-insensitive
    match to an existing market saves the canonical casing; a genuinely new value is kept as typed;
    blank stays blank — Unassigned remains explicit/possible).
  - Wiring into `create_store` / `update_store` / `bulk_create_stores` so the normalization actually
    applies on every write path, not just the new read endpoint.

Runs the REAL router functions against an in-memory fake Supabase client (same harness pattern as
harness_store_mapping_sync.py). Run: `python3 harness_market_dropdown.py` from backend/.
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
        self._mode, self._payload, self._in = None, None, None

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self._in = (k, set(vals)); return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        # app.core.scope.market_index() (2026-08-03 storeops-scope-wiring) calls .limit(5000) on
        # every read — the real supabase-py client supports it, so the fake must too or every
        # market_index() read silently degrades to empty via its try/except I/O guard.
        return self

    def _matches(self, row):
        if not all(str(row.get(k)) == str(v) for _, k, v in self.filters):
            return False
        if self._in and str(row.get(self._in[0])) not in {str(v) for v in self._in[1]}:
            return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            new_rows = []
            for p in payload:
                row = dict(p)
                row.setdefault("id", len(rows) + len(new_rows) + 1)
                new_rows.append(row)
            rows.extend(new_rows)
            return Result(new_rows)
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
        self.store.setdefault((schema, table), []).extend(dict(r) for r in rows)


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-mkt-1"


def _body(model, d):
    """Build the request model FastAPI hands the handler, instead of a plain dict.

    These endpoints were migrated from `body: dict` to a declared pydantic model, so the handler
    reads `body.<field>`. A probe passing a dict dies with AttributeError BEFORE reaching the logic
    under test — the harness then reads as "failing" while proving nothing. `model_validate`
    reproduces FastAPI's own call shape, including which fields count as explicitly set
    (`model_fields_set`), which several handlers branch on.
    """
    return model.model_validate(d)
ORG2 = "org-mkt-2"


def reset():
    fake.store.clear()
    fake.seed("storeops", "stores", [
        {"id": 1, "org_id": ORG, "store_code": "T-100", "address": "100 Main St", "market": "LI", "is_active": True},
        {"id": 2, "org_id": ORG, "store_code": "T-101", "address": "101 Main St", "market": "li", "is_active": True},
        {"id": 3, "org_id": ORG, "store_code": "T-102", "address": "102 Main St", "market": "  Queens  ", "is_active": True},
        {"id": 4, "org_id": ORG, "store_code": "T-103", "address": "103 Main St", "market": "", "is_active": True},
        {"id": 5, "org_id": ORG, "store_code": "T-104", "address": "104 Main St", "market": None, "is_active": True},
        {"id": 6, "org_id": ORG2, "store_code": "T-900", "address": "other tenant", "market": "OtherOrgMarket", "is_active": True},
    ])
    fake.seed("commcalc", "store_mapping", [
        {"org_id": ORG, "store_code": "T-100", "store_address": "100 Main St", "market": "LI"},
        # a market that exists ONLY in store_mapping (not yet on any storeops.stores row) — must
        # still surface in the dropdown per the dispatch ("offer distinct values from BOTH").
        {"org_id": ORG, "store_code": "T-200", "store_address": "200 Side Ave", "market": "Nassau"},
        {"org_id": ORG2, "store_code": "T-900", "store_address": "other tenant", "market": "OtherOrgMarket"},
    ])


# ══ 1: _collect_markets dedupes case-insensitively, trims, excludes blanks, merges both sources ═══
reset()
markets = R._collect_markets(ORG)
check("1a 'LI'/'li' collapse to ONE canonical entry", markets.count("LI") + markets.count("li") == 1, markets)
check("1b canonical casing for LI/li is the MOST-COMMON variant ('LI', 2 stores vs 'li' 1)",
      "LI" in markets and "li" not in markets, markets)
check("1c whitespace-padded 'Queens' trims to 'Queens'", "Queens" in markets, markets)
check("1d blank ('') and None markets are excluded entirely", "" not in markets and None not in markets, markets)
check("1e a market that exists ONLY in store_mapping (Nassau, no storeops.stores row yet) still surfaces",
      "Nassau" in markets, markets)
check("1f exactly 3 distinct markets for this org (LI, Queens, Nassau)", len(markets) == 3, markets)

# ══ 2: org isolation — a different tenant's market never leaks into this org's dropdown ═══════════
check("2a ORG2's 'OtherOrgMarket' does NOT appear in ORG's list", "OtherOrgMarket" not in markets, markets)
markets2 = R._collect_markets(ORG2)
check("2b ORG2's own list contains ONLY its own market", markets2 == ["OtherOrgMarket"], markets2)

# ══ 3: sorted case-insensitively for a stable dropdown ═════════════════════════════════════════════
check("3a list is sorted case-insensitively", markets == sorted(markets, key=lambda s: s.lower()), markets)

# ══ 4: GET /storeops/markets — the dropdown-options endpoint ═══════════════════════════════════════
reset()
resp = R.list_markets(org_id=ORG)
check("4a endpoint returns {'markets': [...]}", "markets" in resp, resp)
check("4b endpoint result matches _collect_markets", resp["markets"] == R._collect_markets(ORG), resp)

# ══ 5: _canonicalize_market — normalize-on-save ═════════════════════════════════════════════════════
canon = ["LI", "Queens", "Nassau"]
check("5a exact case match passes through unchanged", R._canonicalize_market("LI", canon) == "LI")
check("5b different-case match saves the CANONICAL casing", R._canonicalize_market("li", canon) == "LI")
check("5c whitespace is trimmed before matching", R._canonicalize_market("  queens  ", canon) == "Queens")
check("5d a genuinely NEW market is kept as-typed (btrimmed) — the create-new path",
      R._canonicalize_market("  Suffolk  ", canon) == "Suffolk")
check("5e blank stays blank (Unassigned stays explicit/possible)", R._canonicalize_market("", canon) == "")
check("5f whitespace-only counts as blank", R._canonicalize_market("   ", canon) == "")
check("5g None stays blank", R._canonicalize_market(None, canon) == "")

# ══ 6: normalization is actually WIRED into the write paths (not just available) ═══════════════════
reset()
# 6a: create_store with a differently-cased existing market saves canonical casing
row = R.create_store({"store_code": "T-300", "address": "300 X St", "market": "li"}, org_id=ORG)
check("6a create_store normalizes 'li' -> canonical 'LI'", row["market"] == "LI", row)

# 6b: create_store with a brand-new market is kept as typed (create-new path, no server-side reject)
row2 = R.create_store({"store_code": "T-301", "address": "301 X St", "market": "  Brooklyn "}, org_id=ORG)
check("6b create_store keeps a genuinely new market, trimmed", row2["market"] == "Brooklyn", row2)

# 6c: update_store normalizes an existing row's differently-cased market on PATCH
upd = R.update_store(3, {"market": "queens"}, org_id=ORG)  # store 3 was "  Queens  "
check("6c update_store normalizes 'queens' -> canonical 'Queens'", upd["market"] == "Queens", upd)

# 6d: update_store leaves an explicit blank (Unassigned) alone, doesn't force a market
upd2 = R.update_store(1, {"market": ""}, org_id=ORG)
check("6d update_store allows explicitly clearing to Unassigned", upd2["market"] == "", upd2)

# 6e: bulk_create_stores normalizes every row against ONE canonical snapshot (not per-row queries)
reset()
before_calls = len(fake.store.get(("storeops", "stores"), []))
res = R.bulk_create_stores(_body(R.BulkCreateStoresIn, {"stores": [
    {"store_code": "B-1", "address": "B1 addr", "market": "li"},
    {"store_code": "B-2", "address": "B2 addr", "market": "NEW-MARKET"},
]}), org_id=ORG)
check("6e bulk insert reports 2 inserted", res["inserted"] == 2, res)
b1 = next(r for r in fake.store[("storeops", "stores")] if r["store_code"] == "B-1")
b2 = next(r for r in fake.store[("storeops", "stores")] if r["store_code"] == "B-2")
check("6f bulk-created B-1 normalized 'li' -> 'LI'", b1["market"] == "LI", b1)
check("6g bulk-created B-2 (genuinely new) kept as typed", b2["market"] == "NEW-MARKET", b2)

# ══ 7: cross-tenant write can't poison another org's canonical list ════════════════════════════════
reset()
row3 = R.create_store({"store_code": "T-900B", "address": "x", "market": "otherorgmarket"}, org_id=ORG)
check("7a ORG's create_store does NOT normalize against ORG2's 'OtherOrgMarket' (org-isolated canon)",
      row3["market"] == "otherorgmarket", row3)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
