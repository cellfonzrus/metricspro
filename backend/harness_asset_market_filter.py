"""Offline proof harness for the 2026-07-27/28 market-filter-dropdown + Inventory Aging / On-Inventory
standard-filters package (mod-asset):

  - _normalize_addr / _resolve_store_market — the normalized address-matching fix (case/whitespace/
    common street-abbreviation tolerant), including the exact "1800 Great Neck Rd" bug shape.
  - _backfill_market — re-runnable, re-reads store_mapping fresh, idempotent, org-scoped, reports
    stats (stores_updated / rows_updated / stores_unmapped / unmapped_examples).
  - POST /asset/resync-market (resync_market) — thin wrapper, correct response shape.
  - NO_MARKET_SENTINEL / _apply_market_filter / _market_matches — the "(no market)" bucket, on both
    the Supabase/PostgREST query-builder path and the RPC (asset_charges_summary) bypass-then-filter
    path.
  - GET /asset/aging — footer totals (total_amount/total_phones_outstanding), per-model breakdown,
    store multi-select, market "(no market)" bucket, acquired_date range vs AS-OF-TODAY bucket math,
    org isolation.
  - GET /asset/on-inventory-by-store — same standard-filters treatment, totals aliases.
  - GET /asset/charges-summary — NO_MARKET_SENTINEL RPC bypass + Python-side filter; ordinary market
    values still pass straight through to the RPC unchanged (no regression).

No database, no network: a small recording fake Supabase/PostgREST client (with real UPDATE
row-mutation and a real, if simplified, `.or_()` parser for the null/blank-market bucket) feeds the
REAL module code directly (no FastAPI test client needed — these are plain async functions).

Run:  cd backend && python3 harness_asset_market_filter.py
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


# ── fake supabase/postgrest client (real UPDATE mutation + a real, simplified .or_() parser) ──────
class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _RpcCall:
    def __init__(self, fn, params):
        self.fn, self.params = fn, params

    def execute(self):
        return _Resp(self.fn(self.params))


def _or_clause_match(row, clause):
    # clause like "market.is.null" or "market.eq." (postgrest dot-syntax, 3 parts: col.op.value)
    parts = clause.split(".", 2)
    if len(parts) != 3:
        return False
    col, op, val = parts
    rv = row.get(col)
    if op == "is":
        return rv is None if val == "null" else (rv is not None)
    if op == "eq":
        return (rv or "") == val
    return False


class _Q:
    def __init__(self, store, schema, table, rpc_fns, log):
        self.store, self.schema, self.table, self.rpc_fns, self.log = store, schema, table, rpc_fns, log
        self.filters = []
        self._op = "select"
        self._payload = None
        self._or_expr = None
        self._limit = None
        self._range = None
        self._order = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def ilike(self, k, v):
        self.filters.append(("ilike", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def gt(self, k, v):
        self.filters.append(("gt", k, v)); return self

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def in_(self, k, v):
        self.filters.append(("in", k, v)); return self

    def or_(self, expr):
        self._or_expr = expr; return self

    def order(self, col, desc=False):
        self._order = (col, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def range(self, a, b):
        self._range = (a, b); return self

    def insert(self, rows):
        self._op, self._payload = "insert", rows; return self

    def update(self, patch):
        self._op, self._payload = "update", patch; return self

    def delete(self):
        self._op = "delete"; return self

    def _keep(self, r):
        for op, k, v in self.filters:
            if op == "eq" and r.get(k) != v:
                return False
            if op == "ilike":
                pat = str(v).replace("%", "")
                if pat.lower() not in str(r.get(k) or "").lower():
                    return False
            if op == "gte" and str(r.get(k) or "") < str(v):
                return False
            if op == "lte" and str(r.get(k) or "") > str(v):
                return False
            if op == "gt" and str(r.get(k) or "") <= str(v):
                return False
            if op == "is" and v == "null" and r.get(k) is not None:
                return False
            if op == "in" and r.get(k) not in v:
                return False
        if self._or_expr:
            clauses = self._or_expr.split(",")
            if not any(_or_clause_match(r, c) for c in clauses):
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
            removed = len(rows) - len(kept)
            self.store[key] = kept
            self.log.append(("delete", key, list(self.filters)))
            return _Resp(None, count=removed)
        if self._op == "update":
            n = 0
            for r in rows:
                if self._keep(r):
                    r.update(self._payload)
                    n += 1
            self.log.append(("update", key, list(self.filters), dict(self._payload)))
            return _Resp([dict(self._payload)] * n)
        # select
        out = [r for r in rows if self._keep(r)]
        if self._order:
            col, desc = self._order
            out = sorted(out, key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._range:
            a, b = self._range
            out = out[a:b + 1]
        if self._limit is not None:
            out = out[: self._limit]
        return _Resp(out, count=len(out))


class _Table:
    def __init__(self, store, schema, table, rpc_fns, log):
        self.store, self.schema, self.table, self.rpc_fns, self.log = store, schema, table, rpc_fns, log

    def select(self, *a, **k):
        return _Q(self.store, self.schema, self.table, self.rpc_fns, self.log).select(*a, **k)

    def insert(self, rows):
        return _Q(self.store, self.schema, self.table, self.rpc_fns, self.log).insert(rows)

    def update(self, patch):
        return _Q(self.store, self.schema, self.table, self.rpc_fns, self.log).update(patch)

    def delete(self):
        return _Q(self.store, self.schema, self.table, self.rpc_fns, self.log).delete()


class _Schema:
    def __init__(self, store, schema, rpc_fns, log):
        self.store, self.schema, self.rpc_fns, self.log = store, schema, rpc_fns, log

    def table(self, name):
        return _Table(self.store, self.schema, name, self.rpc_fns, self.log)

    def rpc(self, name, params):
        fn = self.rpc_fns.get(name)
        if fn is None:
            raise Exception(f"PGRST202 function {name} does not exist (schema cache)")
        return _RpcCall(fn, params)


class FakeClient:
    def __init__(self, store=None, rpc_fns=None):
        self.store = store if store is not None else {}
        self.rpc_fns = rpc_fns if rpc_fns is not None else {}
        self.log = []

    def schema(self, name):
        return _Schema(self.store, name, self.rpc_fns, self.log)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.asset import router as R  # noqa: E402

print("A. _normalize_addr — case/whitespace/common street-abbreviation tolerance")
ok("A1 Rd == Road (the exact reported bug shape)",
   R._normalize_addr("1800 Great Neck Rd") == R._normalize_addr("1800 Great Neck Road"))
ok("A2 Mount == Mt (pre-existing MARKET_OVERRIDES pair generalized)",
   R._normalize_addr("2778 Mount Ephraim Ave") == R._normalize_addr("2778 Mt Ephraim Ave"))
ok("A3 North Street spelled out == N St abbreviated",
   R._normalize_addr("5619 North Broad Street") == R._normalize_addr("5619 N Broad St"))
ok("A4 case + double-space + trailing period all fold the same",
   R._normalize_addr("1800   GREAT NECK RD.") == R._normalize_addr("1800 great neck rd"))
ok("A5 different addresses stay different",
   R._normalize_addr("1800 Great Neck Rd") != R._normalize_addr("116-36 Springfield Blvd"))

print("\nB. _resolve_store_market — resolution order")
exact = {"1800 great neck road": "LI"}
norm = {R._normalize_addr("1800 Great Neck Road"): "LI"}
ok("B1 normalized store_mapping match resolves the Rd/Road spelling gap",
   R._resolve_store_market("1800 Great Neck Rd", exact, norm) == "LI")
exact2 = {"1800 great neck rd": "NYC"}  # exact beats normalized when both would hit
norm2 = {R._normalize_addr("1800 Great Neck Rd"): "LI"}
ok("B2 exact match wins over normalized when both present",
   R._resolve_store_market("1800 Great Neck Rd", exact2, norm2) == "NYC")
ok("B3 falls back to MARKET_OVERRIDES exact",
   R._resolve_store_market("116-36 Springfield Blvd", {}, {}) == "LI")
ok("B4 falls back to MARKET_OVERRIDES normalized (spelled-out variant of an override-only address)",
   R._resolve_store_market("1 South 60Th Street", {}, {}) == "PA")
ok("B5 unresolvable store returns None",
   R._resolve_store_market("999 Nowhere Ave", {}, {}) is None)

print("\nC. _backfill_market — end to end against the fake client")
c = FakeClient(store={
    ("commcalc", "store_mapping"): [
        {"org_id": ORG_A, "store_address": "1800 Great Neck Road", "market": "LI"},  # added AFTER upload
    ],
    ("commcalc", "asset_ledger"): [
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "market": None},
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "market": None},
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "market": None},
        {"org_id": ORG_A, "store": "Totally Unknown Address", "market": None},
        {"org_id": ORG_B, "store": "1800 Great Neck Rd", "market": None},  # org B: no mapping row -> stays unmapped
    ],
})
stats = R._backfill_market(c, ORG_A)
ok("C1 stores_updated == 1 (one distinct store text resolved)", stats["stores_updated"] == 1, stats)
ok("C2 rows_updated == 3 (all 3 rows for that store)", stats["rows_updated"] == 3, stats)
ok("C3 stores_unmapped == 1 (the genuinely-unknown address)", stats["stores_unmapped"] == 1, stats)
ledger_a = c.store[("commcalc", "asset_ledger")]
ok("C4 all 3 Great Neck rows now market=LI",
   sum(1 for r in ledger_a if r["org_id"] == ORG_A and r["store"] == "1800 Great Neck Rd" and r["market"] == "LI") == 3)
ok("C5 org B's row untouched (org-scoped: no store_mapping row for org B) — still unmapped",
   next(r for r in ledger_a if r["org_id"] == ORG_B)["market"] is None)

stats2 = R._backfill_market(c, ORG_A)
ok("C6 idempotent re-run: nothing to update the second time (already correct)",
   stats2["stores_updated"] == 0 and stats2["rows_updated"] == 0, stats2)

# store_mapping market CHANGES after the first backfill -> a second call picks it up (re-reads fresh)
c.store[("commcalc", "store_mapping")][0]["market"] = "NYC"
stats3 = R._backfill_market(c, ORG_A)
ok("C7 a market changed in store_mapping is picked up on the next call (no upload needed)",
   stats3["stores_updated"] == 1 and stats3["rows_updated"] == 3, stats3)
ok("C8 rows now reflect the corrected market",
   all(r["market"] == "NYC" for r in ledger_a if r["org_id"] == ORG_A and r["store"] == "1800 Great Neck Rd"))

print("\nD. NO_MARKET_SENTINEL / _apply_market_filter / _market_matches")
ok("D1 sentinel is a reserved, non-empty, non-real-market string", R.NO_MARKET_SENTINEL and R.NO_MARKET_SENTINEL != "")
ok("D2 _market_matches: real value matches exactly", R._market_matches("LI", "LI") is True)
ok("D3 _market_matches: real value non-match", R._market_matches("NYC", "LI") is False)
ok("D4 _market_matches: sentinel matches falsy market", R._market_matches(None, R.NO_MARKET_SENTINEL) is True)
ok("D5 _market_matches: sentinel does NOT match a real market", R._market_matches("LI", R.NO_MARKET_SENTINEL) is False)
ok("D6 _market_matches: empty filter matches everything", R._market_matches("anything", "") is True)

c2 = FakeClient(store={("commcalc", "asset_ledger"): [
    {"org_id": ORG_A, "store": "S1", "market": "LI"},
    {"org_id": ORG_A, "store": "S2", "market": None},
    {"org_id": ORG_A, "store": "S3", "market": ""},
    {"org_id": ORG_A, "store": "S4", "market": "NYC"},
]})
q = c2.schema("commcalc").table("asset_ledger").select("*").eq("org_id", ORG_A)
q = R._apply_market_filter(q, R.NO_MARKET_SENTINEL)
rows = q.execute().data
ok("D7 (no market) bucket via query builder returns exactly the NULL/blank rows",
   sorted(r["store"] for r in rows) == ["S2", "S3"], rows)

q2 = c2.schema("commcalc").table("asset_ledger").select("*").eq("org_id", ORG_A)
q2 = R._apply_market_filter(q2, "LI")
ok("D8 ordinary market value still does an exact match", [r["store"] for r in q2.execute().data] == ["S1"])

q3 = c2.schema("commcalc").table("asset_ledger").select("*").eq("org_id", ORG_A)
q3 = R._apply_market_filter(q3, "")
ok("D9 empty market = no filter (everything)", len(q3.execute().data) == 4)


def _run(coro):
    return asyncio.run(coro)


print("\nE. POST /asset/resync-market")
c3 = FakeClient(store={
    ("commcalc", "store_mapping"): [{"org_id": ORG_A, "store_address": "1800 Great Neck Road", "market": "LI"}],
    ("commcalc", "asset_ledger"): [
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "market": None},
        {"org_id": ORG_A, "store": "1800 Great Neck Rd", "market": None},
    ],
})
R.sb = lambda: c3
resp = _run(R.resync_market(org_id=ORG_A))
ok("E1 endpoint returns ok + the backfill stats shape", resp.get("ok") is True and resp.get("rows_updated") == 2, resp)

print("\nF. GET /asset/aging — footer totals, per-model, filters, org isolation")
from datetime import date, timedelta
T = date.today()


def d_ago(n):
    return (T - timedelta(days=n)).isoformat()


aging_rows_a = [
    {"org_id": ORG_A, "id": 1, "store": "1800 Great Neck Rd", "market": "LI", "esn_imei": "IMEI1",
     "phone_number": None, "device_model": "iPhone 15", "category": "On Inventory", "status": "Open",
     "acquired_date": d_ago(10), "due_date": None, "date_sold": None, "owed_to_vip": 400.0,
     "reimbursement": 0, "selling_price": None},
    {"org_id": ORG_A, "id": 2, "store": "1800 Great Neck Rd", "market": "LI", "esn_imei": "IMEI2",
     "phone_number": None, "device_model": "iPhone 15", "category": "On Inventory", "status": "Open",
     "acquired_date": d_ago(50), "due_date": None, "date_sold": None, "owed_to_vip": 350.0,
     "reimbursement": 0, "selling_price": None},
    {"org_id": ORG_A, "id": 3, "store": "1800 Great Neck Rd", "market": "LI", "esn_imei": "IMEI3",
     "phone_number": None, "device_model": "Galaxy S24", "category": "On Inventory", "status": "Open",
     "acquired_date": d_ago(90), "due_date": None, "date_sold": None, "owed_to_vip": 500.0,
     "reimbursement": 0, "selling_price": None},
    {"org_id": ORG_A, "id": 4, "store": "Other Store", "market": None, "esn_imei": "IMEI4",
     "phone_number": None, "device_model": "iPhone 15", "category": "On Inventory", "status": "Open",
     "acquired_date": d_ago(5), "due_date": None, "date_sold": None, "owed_to_vip": 0.0,
     "reimbursement": 0, "selling_price": None},
]
aging_rows_b = [
    {"org_id": ORG_B, "id": 99, "store": "Org B Store", "market": "TX", "esn_imei": "IMEIB",
     "phone_number": None, "device_model": "iPhone 15", "category": "On Inventory", "status": "Open",
     "acquired_date": d_ago(70), "due_date": None, "date_sold": None, "owed_to_vip": 999.0,
     "reimbursement": 0, "selling_price": None},
]
c4 = FakeClient(store={("commcalc", "asset_ledger"): aging_rows_a + aging_rows_b})
R.sb = lambda: c4
resp = _run(R.get_aging(org_id=ORG_A, market="LI"))
ok("F1 total_phones_outstanding counts all 3 LI rows (incl. none here are $0)",
   resp["totals"]["total_phones_outstanding"] == 3, resp["totals"])
ok("F2 total_amount sums owed_to_vip across all filtered rows (400+350+500)",
   resp["totals"]["total_amount"] == 1250.0, resp["totals"])
ok("F3 total_amount_column is explicitly labeled", resp["totals"]["total_amount_column"] == "owed_to_vip")
ok("F4 bucket math: 10-day row under45, 50-day row warn, 90-day row missed",
   resp["buckets"]["under45"]["count"] == 1 and resp["buckets"]["warn"]["count"] == 1
   and resp["buckets"]["missed"]["count"] == 1, resp["buckets"])
by_model = {m["device_model"]: m for m in resp["by_model"]}
ok("F5 by_model: iPhone 15 has 2 phones (under45 + warn), Galaxy S24 has 1 (missed)",
   by_model["iPhone 15"]["total"] == 2 and by_model["iPhone 15"]["under45"] == 1
   and by_model["iPhone 15"]["warn"] == 1 and by_model["Galaxy S24"]["missed"] == 1, by_model)
ok("F6 org B's row never appears when filtering org A", all(r["id"] != 99 for r in resp["buckets"]["missed"]["rows"]))

resp_nomkt = _run(R.get_aging(org_id=ORG_A, market=R.NO_MARKET_SENTINEL))
ok("F7 (no market) bucket returns exactly the 1 unmapped 'Other Store' row (it's $0 -> zero_inventory)",
   resp_nomkt["totals"]["total_phones_outstanding"] == 1
   and resp_nomkt["zero_inventory"]["count"] == 1
   and resp_nomkt["zero_inventory"]["rows"][0]["store"] == "Other Store",
   resp_nomkt)

resp_multi = _run(R.get_aging(org_id=ORG_A, store="1800 Great Neck Rd,Other Store"))
ok("F8 store multi-select (comma-separated) includes both stores",
   resp_multi["totals"]["total_phones_outstanding"] == 4, resp_multi["totals"])

resp_range = _run(R.get_aging(org_id=ORG_A, market="LI", date_from=d_ago(60), date_to=d_ago(1)))
ok("F9 date range narrows to acquired within [60,1] days ago -> only the 10d and 50d rows (not the 90d one)",
   resp_range["totals"]["total_phones_outstanding"] == 2, resp_range["totals"])
ok("F10 bucket math for the 50-day row STILL uses today, not the range edge (stays 'warn', not something else)",
   resp_range["buckets"]["warn"]["count"] == 1, resp_range["buckets"])
ok("F11 bucket_basis honestly states as-of-today semantics", "AS OF TODAY" in resp["bucket_basis"])

print("\nG. GET /asset/on-inventory-by-store — totals aliases + filters")
c5 = FakeClient(store={("commcalc", "asset_ledger"): aging_rows_a + aging_rows_b})
R.sb = lambda: c5
resp_oi = _run(R.get_on_inventory_by_store(org_id=ORG_A, market="LI"))
ok("G1 total_amount alias equals legacy 'owed'",
   resp_oi["totals"]["total_amount"] == resp_oi["totals"]["owed"] == 1250.0, resp_oi["totals"])
ok("G2 total_phones_outstanding alias equals legacy 'device_count'",
   resp_oi["totals"]["total_phones_outstanding"] == resp_oi["totals"]["device_count"] == 3, resp_oi["totals"])
ok("G3 total_amount_column labeled", resp_oi["totals"]["total_amount_column"] == "owed_to_vip")

resp_oi_nomkt = _run(R.get_on_inventory_by_store(org_id=ORG_A, market=R.NO_MARKET_SENTINEL))
ok("G4 (no market) bucket reaches on-inventory-by-store too",
   resp_oi_nomkt["totals"]["total_phones_outstanding"] == 1, resp_oi_nomkt["totals"])

print("\nH. GET /asset/charges-summary — NO_MARKET_SENTINEL RPC bypass")
charge_rows = [
    {"category": "PROCESSING FEE", "store": "1800 Great Neck Rd", "market": "LI", "cnt": 2, "owed": 40.0, "reimb": 0},
    {"category": "PROCESSING FEE", "store": "Other Store", "market": None, "cnt": 1, "owed": 20.0, "reimb": 0},
    {"category": "RMA", "store": "S3", "market": "NYC", "cnt": 1, "owed": 100.0, "reimb": 0},
]


def _fake_rpc(params):
    # Real RPC: filters by p_market in SQL. Our fake mirrors that so the "bypass when sentinel"
    # behavior in get_charges_summary is what's actually under test (not the fake's own leniency).
    out = charge_rows
    if params.get("p_market"):
        out = [r for r in out if r["market"] == params["p_market"]]
    return out


c6 = FakeClient(rpc_fns={"asset_charges_summary": _fake_rpc})
R.sb = lambda: c6
resp_cs_all = _run(R.get_charges_summary(org_id=ORG_A))
ok("H1 no market filter -> RPC called with p_market None, all rows counted",
   resp_cs_all["groups"]["vip_fees"]["count"] == 3, resp_cs_all["groups"]["vip_fees"])

resp_cs_li = _run(R.get_charges_summary(org_id=ORG_A, market="LI"))
ok("H2 ordinary market value passes straight through to the RPC unchanged (no regression)",
   resp_cs_li["groups"]["vip_fees"]["count"] == 2, resp_cs_li["groups"]["vip_fees"])

resp_cs_nomkt = _run(R.get_charges_summary(org_id=ORG_A, market=R.NO_MARKET_SENTINEL))
ok("H3 (no market) bucket: RPC called WITHOUT p_market, then Python-filtered to falsy-market rows only",
   resp_cs_nomkt["groups"]["vip_fees"]["count"] == 1
   and resp_cs_nomkt["groups"]["vip_fees"]["by_store"][0]["store"] == "Other Store",
   resp_cs_nomkt["groups"]["vip_fees"])

print(f"\n{'='*60}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
