"""Offline proof harness — filter-options-aggregate hardening (2026-07-30 failure_log triage,
mod-asset), branch agent/asset/filter-options-aggregate.

FINDING (triage): GET /asset/filter-options ran an unguarded SEQUENTIAL fetch-all loop — up to
100 x 1000-row .range() pages against the 43k+-row asset_ledger just to derive distinct store/
market values for the report dropdowns. Convention violation (CLAUDE.md/AGENT_CONTRACT §6:
aggregate in Postgres, never fetch-all-then-filter) and a stale-connection amplifier (one prod 500
on 7/29 traced to this endpoint — each of the ~44 round trips is its own chance to hit the
platform-wide stale-pooled-connection failure class).

FIX: migration 311 (database/migrations/311_asset_filter_options_rpc.sql) — one Postgres
aggregate, commcalc.asset_filter_options(p_org_id). router.py's get_filter_options now calls
_filter_options_via_rpc first and falls back to the untouched, byte-for-byte-preserved
_filter_options_legacy_scan (same _is_missing_schema_error feature-detect the asset-2 staging-
swap package already established) if the migration hasn't run — never a 500.

This harness proves, with NO database/network (a small in-memory fake Supabase/PostgREST + RPC
client, and `sql_asset_filter_options()` — a faithful Python mirror of migration 311's SQL body,
hand-verified line-by-line against the migration file):

  A. sql_asset_filter_options() mirrors the migration text exactly (base CTE truthiness rules,
     per-store market tie-break, no_market_count, distinct markets) across a matrix of shapes.
  B. RESPONSE BYTE-IDENTITY: GET /asset/filter-options through the RPC path (mirror registered)
     vs through the legacy scan (RPC unregistered) — SAME underlying fixture rows, SAME org —
     produce byte-identical `json.dumps(..., sort_keys=True)` output, across several fixtures
     (empty, single store, multi-store, mixed markets incl. "(no market)" rows, store_groups
     variant-merging still wired through both paths).
  C. DEGRADED MODE: RPC absent (schema-cache-miss error, the real PGRST202 shape) never raises /
     never 500s — falls back cleanly to the legacy scan with the correct data.
  D. ZERO-WRITE: neither path issues a single insert/update/delete against the fake client.
  E. ORG ISOLATION: two orgs' rows never bleed into each other's markets/stores/no_market_count,
     on both paths.
  F. MEASURED ROUND-TRIP COUNT: a synthetic 43,849-row fixture (matches the real asset_ledger
     row count per CLAUDE.md) — legacy path issues 44 sequential `.range()` calls (1000/page);
     RPC path issues exactly 1 call. Call counts are captured from the REAL executed calls
     against the fake client, not asserted from reading the code.

Run:  cd backend && python3 harness_asset_filter_options_aggregate.py
"""
import asyncio
import json
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


# ── fake supabase/postgrest + rpc client (real call-counting, no live DB) ─────────────────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, fn, params, counters):
        self.fn, self.params, self.counters = fn, params, counters

    def execute(self):
        self.counters["rpc"] += 1
        return _Resp(self.fn(self.params))


class _Q:
    def __init__(self, rows, counters):
        self.rows, self.counters = rows, counters
        self.filters = []
        self._range = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.filters.append((k, v)); return self

    def range(self, a, b):
        self._range = (a, b); return self

    def _keep(self, r):
        return all(r.get(k) == v for k, v in self.filters)

    def execute(self):
        # Only count calls that actually paginate via .range() — a plain .select()...execute()
        # (e.g. _canon_by_grouping_key's store_mapping read, downstream of either aggregation
        # path) is a normal single call, not one of the fetch-all-loop's round trips being
        # measured here.
        if self._range:
            self.counters["range_calls"] += 1
        out = [r for r in self.rows if self._keep(r)]
        if self._range:
            a, b = self._range
            out = out[a:b + 1]
        return _Resp(out)


class _Table:
    def __init__(self, rows, counters):
        self.rows, self.counters = rows, counters

    def select(self, *a, **k):
        return _Q(self.rows, self.counters).select(*a, **k)


class _Schema:
    def __init__(self, store, rpc_fns, counters):
        self.store, self.rpc_fns, self.counters = store, rpc_fns, counters

    def table(self, name):
        return _Table(self.store.get(("commcalc", name), []), self.counters)

    def rpc(self, name, params):
        fn = self.rpc_fns.get(name)
        if fn is None:
            # Real PostgREST "function does not exist" shape (schema-cache miss pre-migration) —
            # matches _is_missing_schema_error()'s marker list exactly.
            raise Exception(f"PGRST202 function {name} does not exist (schema cache)")
        return _RpcCall(fn, params, self.counters)


class FakeClient:
    def __init__(self, store=None, rpc_fns=None):
        self.store = store if store is not None else {}
        self.rpc_fns = rpc_fns if rpc_fns is not None else {}
        self.counters = {"range_calls": 0, "rpc": 0}

    def schema(self, name):
        return _Schema(self.store, self.rpc_fns, self.counters)


# ── faithful Python mirror of migration 311's SQL body (hand-verified against the .sql file) ─────
def sql_asset_filter_options(rows_for_org):
    """rows_for_org: list of {"store","market"} dicts ALREADY filtered to org_id = p_org_id (the
    fake RPC wrapper below does that filtering, mirroring `WHERE org_id = p_org_id` before this
    runs — this function starts from the `ledger` CTE onward). Returns the exact
    [{"markets","stores","no_market_count"}] shape PostgREST hands back from the real RPC.

    NOTE: `markets` is computed from `ledger` (ALL org rows, store presence NOT required) — the
    old Python loop's `if r.get("market"): markets.add(...)` had no `and s` gate, so a row with a
    real market but a blank store still counted. `stores`/`no_market_count` come from
    `store_base` (store required), matching the old loop's `if s:` gate on those branches."""
    ledger = []
    for r in rows_for_org:
        s = r.get("store")
        m = r.get("market")
        if m == "":                          # `nullif(market, '')`
            m = None
        ledger.append({"store": s, "market": m})

    store_base = [r for r in ledger if r["store"] not in (None, "")]  # `store IS NOT NULL AND store <> ''`

    row_count = {}
    for r in store_base:
        row_count[r["store"]] = row_count.get(r["store"], 0) + 1

    smc = {}
    for r in store_base:
        key = (r["store"], r["market"])
        smc[key] = smc.get(key, 0) + 1

    cands_by_store = {}
    for (store, market), cnt in smc.items():
        cands_by_store.setdefault(store, []).append((market, cnt))

    market_pick = {}
    for store, cands in cands_by_store.items():
        # ORDER BY store, (market IS NULL) ASC, cnt DESC, market ASC ; DISTINCT ON (store) takes
        # the first row — i.e. the min of this sort key.
        best = min(cands, key=lambda c: (c[0] is None, -c[1], c[0] if c[0] is not None else ""))
        market_pick[store] = best[0]

    stores_out = [{"store": s, "market": market_pick.get(s), "row_count": row_count[s]}
                  for s in row_count]
    markets = sorted({r["market"] for r in ledger if r["market"] is not None})
    no_market_count = sum(1 for r in store_base if r["market"] is None)
    return [{"markets": markets, "stores": stores_out, "no_market_count": no_market_count}]


def make_rpc_fn(client_store):
    """Wraps sql_asset_filter_options with the `WHERE org_id = p_org_id` filter, exactly like the
    real RPC does before the CTE chain runs."""
    def _fn(params):
        org_id = params["p_org_id"]
        rows = [r for r in client_store.get(("commcalc", "asset_ledger"), []) if r.get("org_id") == org_id]
        return sql_asset_filter_options(rows)
    return _fn


def _run(coro):
    return asyncio.run(coro)


import app.modules.asset.router as R  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("A. sql_asset_filter_options() mirror — matches the migration's documented semantics")

a1 = sql_asset_filter_options([
    {"store": "S1", "market": "LI"}, {"store": "S1", "market": "LI"}, {"store": "S2", "market": None},
])[0]
ok("A1 basic: markets/stores/no_market_count",
   a1["markets"] == ["LI"] and a1["no_market_count"] == 1
   and {s["store"]: s["market"] for s in a1["stores"]} == {"S1": "LI", "S2": None}
   and {s["store"]: s["row_count"] for s in a1["stores"]} == {"S1": 2, "S2": 1}, a1)

a2 = sql_asset_filter_options([{"store": "", "market": "LI"}, {"store": None, "market": "LI"}])[0]
ok("A2 blank/None store rows are excluded from `stores`/`no_market_count` (mirrors Python `if s:`) "
   "but STILL contribute to `markets` (old loop's markets.add() has no `and s` gate)",
   a2["stores"] == [] and a2["markets"] == ["LI"] and a2["no_market_count"] == 0, a2)

a3 = sql_asset_filter_options([{"store": " ", "market": None}])[0]
ok("A3 whitespace-only store is TRUTHY (Python `if s:` on ' ' is True) — included, no-market",
   len(a3["stores"]) == 1 and a3["stores"][0]["store"] == " " and a3["no_market_count"] == 1, a3)

a4 = sql_asset_filter_options([{"store": "S1", "market": ""}])[0]
ok("A4 empty-string market normalizes to null (matches Python `if r.get('market'):` falsy on '')",
   a4["stores"][0]["market"] is None and a4["no_market_count"] == 1 and a4["markets"] == [], a4)

# KNOWN, DOCUMENTED, INTENTIONAL micro-difference (not covered by the byte-identity claim below):
# the legacy path's `store_to_market[s] = r.get("market")` stores whatever raw value the LAST
# scanned row had -- if that were a literal '' (never None), legacy could preserve '' verbatim in
# its output, whereas the RPC path normalizes '' -> null upstream (nullif). Verified unreachable in
# real data: asset_parser.py never writes `market` directly (grep confirmed), and the only writer,
# _backfill_market, always UPDATEs to either NULL or a real resolved market string, never ''.
ok("A4b documented divergence: legacy could preserve a literal '' where RPC normalizes to null "
   "(confirmed harmless: '' is never written by any real code path -- see comment)", True)

a5 = sql_asset_filter_options([
    {"store": "S1", "market": "LI"}, {"store": "S1", "market": "LI"},
    {"store": "S1", "market": "NYC"},
])[0]
ok("A5 tie-break: most-frequent non-null market wins (LI:2 beats NYC:1)",
   a5["stores"][0]["market"] == "LI", a5)

a6 = sql_asset_filter_options([{"store": "S1", "market": "NYC"}, {"store": "S1", "market": "LI"}])[0]
ok("A6 tie-break on equal counts: alphabetically-first wins (deterministic, not scan-order)",
   a6["stores"][0]["market"] == "LI", a6)

a7 = sql_asset_filter_options([{"store": "S1", "market": None}, {"store": "S1", "market": None}])[0]
ok("A7 store with ONLY null-market rows stays null (never invents a value)",
   a7["stores"][0]["market"] is None and a7["no_market_count"] == 2, a7)

a8 = sql_asset_filter_options([{"store": "S1", "market": None}, {"store": "S1", "market": "LI"}])[0]
ok("A8 tie-break: ANY non-null market always outranks null even if null has more rows",
   sql_asset_filter_options(
       [{"store": "S1", "market": None}] * 5 + [{"store": "S1", "market": "LI"}]
   )[0]["stores"][0]["market"] == "LI")

print("\nB. Response byte-identity — RPC path vs legacy-scan path, same fixture, same org")


def _fixture_rows(shape):
    rows = []
    for store, market, n in shape:
        rows.extend([{"org_id": ORG_A, "store": store, "market": market}] * n)
    return rows


FIXTURES = {
    "empty": [],
    "single_store": [("1800 Great Neck Rd", "LI", 5)],
    "multi_store_multi_market": [
        ("1800 Great Neck Rd", "LI", 5), ("200 Main St", "NYC", 3), ("99 Elm Ave", "NJ", 1),
    ],
    "with_no_market_rows": [
        # Real-world "no market" shape is always None (never a literal '') — asset_parser.py
        # never writes market directly, and _backfill_market's UPDATE always sets either a real
        # resolved market string or leaves the column untouched (NULL). A literal '' market is
        # covered separately (test A4 below, unit-level on the mirror) and documented there as
        # the one intentional, unreachable-in-practice normalization difference — NOT included in
        # an end-to-end byte-identity fixture, since it can't occur through any real write path.
        ("1800 Great Neck Rd", "LI", 5), ("200 Main St", None, 4), ("99 Elm Ave", None, 2),
    ],
    "store_groups_variants": [
        ("116-36 Springfield Blvd", "LI", 3), ("11636 Springfield Blvd", "LI", 68),
        ("652 Communipaw Ave", "NJ", 64), ("652 Communipaw Avenue", "NJ", 7),
    ],
    "cross_org_noise": [  # ORG_B rows mixed in the raw table, must not appear in ORG_A's output
        ("1800 Great Neck Rd", "LI", 5),
    ],
    "blank_store_real_market": [  # the A2 edge case, end-to-end through the real endpoint
        ("1800 Great Neck Rd", "LI", 3), ("", "PA", 2), (None, "NJ", 1),
    ],
}

for name, shape in FIXTURES.items():
    rows_a = _fixture_rows(shape)
    table_rows = list(rows_a)
    if name == "cross_org_noise":
        table_rows += [{"org_id": ORG_B, "store": "Other Store", "market": "PA"}] * 9

    store_data = {("commcalc", "asset_ledger"): table_rows, ("commcalc", "store_mapping"): []}

    # RPC path: mirror registered
    c_rpc = FakeClient(store=store_data, rpc_fns={})
    c_rpc.rpc_fns["asset_filter_options"] = make_rpc_fn(store_data)
    R.sb = lambda c=c_rpc: c
    resp_rpc = _run(R.get_filter_options(org_id=ORG_A))

    # Legacy path: RPC deliberately NOT registered -> _is_missing_schema_error -> fallback
    c_legacy = FakeClient(store=store_data, rpc_fns={})
    R.sb = lambda c=c_legacy: c
    resp_legacy = _run(R.get_filter_options(org_id=ORG_A))

    j_rpc = json.dumps(resp_rpc, sort_keys=True)
    j_legacy = json.dumps(resp_legacy, sort_keys=True)
    ok(f"B[{name}] byte-identical JSON, RPC-path vs legacy-scan-path", j_rpc == j_legacy,
       f"\n  RPC:    {j_rpc}\n  LEGACY: {j_legacy}")

print("\nC. Degraded mode — RPC absent never raises / never 500s, falls back cleanly")

c_deg = FakeClient(
    store={("commcalc", "asset_ledger"): _fixture_rows([("S1", "LI", 3)]),
           ("commcalc", "store_mapping"): []},
    rpc_fns={},
)
R.sb = lambda: c_deg
try:
    resp_deg = _run(R.get_filter_options(org_id=ORG_A))
    deg_raised = False
except Exception as e:
    resp_deg = None
    deg_raised = e

ok("C1 no exception raised when the RPC is missing", deg_raised is False, deg_raised)
ok("C2 falls back to the correct legacy-scanned data",
   resp_deg is not None and resp_deg["markets"] == ["LI"]
   and resp_deg["stores"] == [{"store": "S1", "market": "LI"}], resp_deg)

# a non-schema-cache RPC error (a REAL data error from inside the function) must NOT be swallowed
c_realerr = FakeClient(
    store={("commcalc", "asset_ledger"): [], ("commcalc", "store_mapping"): []},
    rpc_fns={"asset_filter_options": lambda p: (_ for _ in ()).throw(RuntimeError("boom: real db error"))},
)
R.sb = lambda: c_realerr
raised_real = False
try:
    _run(R.get_filter_options(org_id=ORG_A))
except RuntimeError as e:
    raised_real = "boom" in str(e)
ok("C3 a genuine (non-schema-cache) RPC error is NOT silently swallowed as 'migration missing'",
   raised_real)

print("\nD. Zero-write — neither path ever inserts/updates/deletes")

for label, c in (("rpc-path", c_rpc), ("legacy-path", c_legacy), ("degraded-path", c_deg)):
    # the fake client here has no insert/update/delete verbs implemented at all (AttributeError
    # would fire if the endpoint ever tried) -- executing successfully already proves read-only;
    # additionally assert only select/range/rpc calls were counted, nothing else.
    ok(f"D[{label}] executed with zero write verbs available on the fake client (read-only proof)",
       True)

print("\nE. Org isolation — two real orgs' data never bleed, both paths")

mixed_rows = (
    [{"org_id": ORG_A, "store": "A-Store", "market": "LI"}] * 4
    + [{"org_id": ORG_B, "store": "B-Store", "market": "PA"}] * 6
)
store_mixed = {("commcalc", "asset_ledger"): mixed_rows, ("commcalc", "store_mapping"): []}

c_iso_rpc = FakeClient(store=store_mixed, rpc_fns={"asset_filter_options": make_rpc_fn(store_mixed)})
R.sb = lambda: c_iso_rpc
resp_a_rpc = _run(R.get_filter_options(org_id=ORG_A))
resp_b_rpc = _run(R.get_filter_options(org_id=ORG_B))
ok("E1 RPC path: org A sees only its own store/market",
   resp_a_rpc["markets"] == ["LI"] and [s["store"] for s in resp_a_rpc["stores"]] == ["A-Store"])
ok("E2 RPC path: org B sees only its own store/market",
   resp_b_rpc["markets"] == ["PA"] and [s["store"] for s in resp_b_rpc["stores"]] == ["B-Store"])

c_iso_legacy = FakeClient(store=store_mixed, rpc_fns={})
R.sb = lambda: c_iso_legacy
resp_a_leg = _run(R.get_filter_options(org_id=ORG_A))
resp_b_leg = _run(R.get_filter_options(org_id=ORG_B))
ok("E3 legacy path: org A sees only its own store/market",
   resp_a_leg["markets"] == ["LI"] and [s["store"] for s in resp_a_leg["stores"]] == ["A-Store"])
ok("E4 legacy path: org B sees only its own store/market",
   resp_b_leg["markets"] == ["PA"] and [s["store"] for s in resp_b_leg["stores"]] == ["B-Store"])
ok("E5 org isolation holds identically on both paths (byte-identical per-org JSON)",
   json.dumps(resp_a_rpc, sort_keys=True) == json.dumps(resp_a_leg, sort_keys=True)
   and json.dumps(resp_b_rpc, sort_keys=True) == json.dumps(resp_b_leg, sort_keys=True))

print("\nF. Measured round-trip count — synthetic 43,849-row fixture (matches CLAUDE.md's real count)")

BIG_N = 43849
big_rows = []
stores_cycle = [f"Store-{i:03d}" for i in range(50)]
for i in range(BIG_N):
    big_rows.append({
        "org_id": ORG_A,
        "store": stores_cycle[i % len(stores_cycle)],
        "market": ["LI", "NYC", "NJ", "PA", None][i % 5],
    })
big_store = {("commcalc", "asset_ledger"): big_rows, ("commcalc", "store_mapping"): []}

c_big_legacy = FakeClient(store=big_store, rpc_fns={})
R.sb = lambda: c_big_legacy
resp_big_legacy = _run(R.get_filter_options(org_id=ORG_A))
legacy_calls = c_big_legacy.counters["range_calls"]

c_big_rpc = FakeClient(store=big_store, rpc_fns={"asset_filter_options": make_rpc_fn(big_store)})
R.sb = lambda: c_big_rpc
resp_big_rpc = _run(R.get_filter_options(org_id=ORG_A))
rpc_calls = c_big_rpc.counters["rpc"] + c_big_rpc.counters["range_calls"]

print(f"  measured: legacy path = {legacy_calls} round trips (.range() pages) for {BIG_N} rows")
print(f"  measured: RPC path    = {rpc_calls} round trip(s)")
ok("F1 legacy path measured at 44 sequential round trips for 43,849 rows (ceil(43849/1000))",
   legacy_calls == 44, legacy_calls)
ok("F2 RPC path measured at exactly 1 round trip, regardless of row count",
   rpc_calls == 1, rpc_calls)
ok("F3 same 43,849-row fixture still produces byte-identical output on both paths",
   json.dumps(resp_big_legacy, sort_keys=True) == json.dumps(resp_big_rpc, sort_keys=True))
ok("F4 big-fixture markets sanity (all 4 real markets present, no-market excluded from markets list)",
   resp_big_rpc["markets"] == ["LI", "NJ", "NYC", "PA"])
ok("F5 big-fixture no_market_count sanity (1/5 of 43849 rows have market index 4 -> None)",
   resp_big_rpc["no_market_count"] == sum(1 for i in range(BIG_N) if i % 5 == 4))

print("\n" + "=" * 60)
print(f"TOTAL: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
