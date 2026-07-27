"""Offline proof harness for the asset module's settings-audit package (2026-07-26):
  - attention.py's 3 admin-attention providers (asset_ledger_stale, asset_market_gap,
    asset_pipeline_issues)
  - router.py's new _log_asset_pipeline_issue() (tenant disabled-category opt-out, fail-open on a
    read error, never raises on a write error)
  - the _backfill_market resilience fix in process_asset_ledger_bytes (an exception there no longer
    aborts selling-price backfill / the three flag syncs that run after it)

No database, no network: a small recording fake Supabase client feeds the REAL module code.

Run:  cd backend && python3 harness_asset_settings_audit.py
"""
import sys
from datetime import datetime, timezone, timedelta, date

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
TODAY = date.today()


def iso_days_ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


# ── fake supabase client ────────────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _RpcCall:
    """Mirrors supabase-py's rpc(...) builder: rpc() returns a request object, .execute() runs it."""
    def __init__(self, fn, params):
        self.fn, self.params = fn, params

    def execute(self):
        return _Resp(self.fn(self.params))


class _Q:
    def __init__(self, store, schema, table, rpc_fns, log):
        self.store, self.schema, self.table, self.rpc_fns, self.log = store, schema, table, rpc_fns, log
        self.filters = []       # list of (op, k, v)
        self._op = "select"
        self._payload = None
        self._rpc_name = None
        self._rpc_params = None
        self._limit = None
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

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def in_(self, k, v):
        self.filters.append(("in", k, v)); return self

    def order(self, col, desc=False):
        self._order = (col, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def range(self, a, b):
        return self

    def insert(self, rows):
        self._op, self._payload = "insert", rows; return self

    def upsert(self, rows, **k):
        self._op, self._payload = "upsert", rows; return self

    def update(self, patch):
        self._op, self._payload = "update", patch; return self

    def delete(self):
        self._op = "delete"; return self

    def execute(self):
        key = (self.schema, self.table)
        rows = list(self.store.get(key, []))
        if self._op == "insert":
            for r in (self._payload if isinstance(self._payload, list) else [self._payload]):
                rows2 = self.store.setdefault(key, [])
                rows2.append(dict(r))
            self.log.append(("insert", key, self._payload))
            return _Resp(self._payload)
        if self._op == "delete":
            def match(r):
                for op, k, v in self.filters:
                    if op == "eq" and r.get(k) != v:
                        return False
                return True
            kept = [r for r in rows if not match(r)]
            self.store[key] = kept
            self.log.append(("delete", key, list(self.filters)))
            return _Resp(None)
        # select
        def keep(r):
            for op, k, v in self.filters:
                if op == "eq" and r.get(k) != v:
                    return False
                if op == "ilike":
                    pat = str(v).replace("%", "")
                    if not str(r.get(k) or "").lower().startswith(pat.lower()):
                        return False
                if op == "gte" and str(r.get(k) or "") < str(v):
                    return False
                if op == "lte" and str(r.get(k) or "") > str(v):
                    return False
                if op == "is" and v == "null" and r.get(k) is not None:
                    return False
            return True
        out = [r for r in rows if keep(r)]
        if self._order:
            col, desc = self._order
            out = sorted(out, key=lambda r: str(r.get(col) or ""), reverse=desc)
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
    """store: {(schema, table): [row, ...]}; rpc_fns: {name: fn(params) -> data}"""

    def __init__(self, store=None, rpc_fns=None):
        self.store = store if store is not None else {}
        self.rpc_fns = rpc_fns if rpc_fns is not None else {}
        self.log = []

    def schema(self, name):
        return _Schema(self.store, name, self.rpc_fns, self.log)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("A. attention.py — _p_asset_ledger_stale")
from app.modules.asset import attention as A  # noqa: E402

# A1: Boost org, zero ledger rows -> "never uploaded"
c = FakeClient(store={
    ("commcalc", "carrier"): [{"org_id": ORG_A, "name": "Boost", "code": "BOOST", "is_default": True}],
    ("commcalc", "asset_ledger"): [],
})
items = A._p_asset_ledger_stale(c, ORG_A, {})
ok("A1 never-uploaded fires for Boost org with 0 rows", len(items) == 1 and items[0]["key"] == "asset_ledger_never_uploaded",
   items)
ok("A1 deep_link points at the upload page", items and items[0]["deep_link"] == "/commcalc/asset")

# A2: Boost org, ledger has a row with an old FileDate -> stale, severity error
c = FakeClient(store={
    ("commcalc", "carrier"): [{"org_id": ORG_A, "name": "Boost", "code": "BOOST", "is_default": True}],
    ("commcalc", "asset_ledger"): [{"org_id": ORG_A, "raw_row": {"FileDate": iso_days_ago(10)}}],
})
items = A._p_asset_ledger_stale(c, ORG_A, {})
ok("A2 stale fires at 10 days old", len(items) == 1 and items[0]["key"] == "asset_ledger_stale"
   and items[0]["severity"] == "error" and items[0]["count"] == 10, items)

# A3: Boost org, ledger has a fresh row (today) -> no item
c = FakeClient(store={
    ("commcalc", "carrier"): [{"org_id": ORG_A, "name": "Boost", "code": "BOOST", "is_default": True}],
    ("commcalc", "asset_ledger"): [{"org_id": ORG_A, "raw_row": {"FileDate": TODAY.isoformat()}}],
})
items = A._p_asset_ledger_stale(c, ORG_A, {})
ok("A3 fresh ledger -> no item", items == [], items)

# A4: non-Boost org (Total default carrier), zero ledger rows -> NO item (empty is expected)
c = FakeClient(store={
    ("commcalc", "carrier"): [{"org_id": ORG_A, "name": "Total Wireless", "code": "TOTAL", "is_default": True}],
    ("commcalc", "asset_ledger"): [],
})
items = A._p_asset_ledger_stale(c, ORG_A, {})
ok("A4 non-Boost tenant with empty ledger -> silent (working as intended)", items == [], items)

# A5: no carriers configured at all -> conservative default 'boost' -> item still fires
c = FakeClient(store={
    ("commcalc", "carrier"): [],
    ("commcalc", "asset_ledger"): [],
})
items = A._p_asset_ledger_stale(c, ORG_A, {})
ok("A5 unconfigured-carrier org defaults to boost posture -> item fires", len(items) == 1, items)

print("\nB. attention.py — _p_asset_market_gap")

# B1: RPC returns a partial gap -> warning
c = FakeClient(rpc_fns={
    "asset_market_gap": lambda p: [{"total_rows": 100, "unmapped_rows": 10,
                                     "unmapped_stores": ["123 Main St", "456 Oak Ave"]}]
})
items = A._p_asset_market_gap(c, ORG_A, {})
ok("B1 partial gap -> warning, count=10", len(items) == 1 and items[0]["severity"] == "warning"
   and items[0]["count"] == 10, items)
ok("B1 detail names example stores", "123 Main St" in items[0]["detail"], items)

# B2: majority of ledger unmapped -> error
c = FakeClient(rpc_fns={
    "asset_market_gap": lambda p: [{"total_rows": 100, "unmapped_rows": 60, "unmapped_stores": []}]
})
items = A._p_asset_market_gap(c, ORG_A, {})
ok("B2 majority-unmapped -> error severity", items and items[0]["severity"] == "error", items)

# B3: zero gap -> no item
c = FakeClient(rpc_fns={
    "asset_market_gap": lambda p: [{"total_rows": 100, "unmapped_rows": 0, "unmapped_stores": []}]
})
items = A._p_asset_market_gap(c, ORG_A, {})
ok("B3 zero gap -> silent", items == [], items)

# B4: RPC missing (migration 302 not run) -> silent, no exception
c = FakeClient(rpc_fns={})
try:
    items = A._p_asset_market_gap(c, ORG_A, {})
    ok("B4 missing RPC degrades to empty, no raise", items == [], items)
except Exception as e:
    ok("B4 missing RPC degrades to empty, no raise", False, e)

print("\nC. attention.py — _p_asset_pipeline_issues")

now = datetime.now(timezone.utc)


def flog(org, cat, days_ago, msg="x"):
    return {"org_id": org, "category": cat, "message": msg,
            "created_at": (now - timedelta(days=days_ago)).isoformat()}


# C1: two categories, one with 2 rows -> 2 items, correct counts, org-isolated
c = FakeClient(store={
    ("core", "failure_log"): [
        flog(ORG_A, "asset_market_backfill_failed", 1),
        flog(ORG_A, "asset_market_backfill_failed", 3),
        flog(ORG_A, "asset_rma_flag_sync_failed", 2),
        flog(ORG_B, "asset_market_backfill_failed", 1),   # must NOT leak into org A's result
    ],
})
items = A._p_asset_pipeline_issues(c, ORG_A, {"now": now})
by_key = {i["key"]: i for i in items}
ok("C1 two distinct categories surfaced", len(items) == 2, items)
ok("C1 count aggregates per category (2)", by_key.get("asset_pipeline:asset_market_backfill_failed", {}).get("count") == 2, items)
ok("C1 known category gets the friendly hint, not the raw message",
   "Store Mapping" in by_key["asset_pipeline:asset_market_backfill_failed"]["detail"])
ok("C1 org isolation: org B's failure never counted into org A's total",
   by_key.get("asset_pipeline:asset_market_backfill_failed", {}).get("count") == 2)

items_b = A._p_asset_pipeline_issues(c, ORG_B, {"now": now})
ok("C1b org B sees only its own row", len(items_b) == 1 and items_b[0]["count"] == 1, items_b)

# C2: empty failure_log -> no items
c = FakeClient(store={("core", "failure_log"): []})
items = A._p_asset_pipeline_issues(c, ORG_A, {"now": now})
ok("C2 empty failure_log -> silent", items == [], items)

# C3: failure_log table missing entirely -> degrades to [], no raise
class _RaisingClient(FakeClient):
    def schema(self, name):
        raise Exception("PGRST205 relation core.failure_log does not exist")

try:
    items = A._p_asset_pipeline_issues(_RaisingClient(), ORG_A, {"now": now})
    ok("C3 missing failure_log table degrades to empty, no raise", items == [], items)
except Exception as e:
    ok("C3 missing failure_log table degrades to empty, no raise", False, e)

print("\nD. router.py — _log_asset_pipeline_issue")
sys.path.insert(0, "app/modules/asset")
from app.modules.asset import router as R  # noqa: E402

# D1: normal write
c = FakeClient(store={("storeops", "tenants"): [{"org_id": ORG_A, "failure_log_disabled_categories": []}]})
R._log_asset_pipeline_issue(c, ORG_A, "asset_market_backfill_failed", "boom")
rows = c.store.get(("core", "failure_log"), [])
ok("D1 writes a failure_log row", len(rows) == 1 and rows[0]["category"] == "asset_market_backfill_failed", rows)

# D2: category disabled by tenant preference -> no write
c = FakeClient(store={("storeops", "tenants"): [{"org_id": ORG_A,
                       "failure_log_disabled_categories": ["asset_market_backfill_failed"]}]})
R._log_asset_pipeline_issue(c, ORG_A, "asset_market_backfill_failed", "boom")
rows = c.store.get(("core", "failure_log"), [])
ok("D2 disabled category is NOT written", rows == [], rows)

# D3: tenants read raises -> fail OPEN (still logs)
class _TenantsRaise(FakeClient):
    def schema(self, name):
        if name == "storeops":
            raise Exception("boom")
        return super().schema(name)

c = _TenantsRaise()
R._log_asset_pipeline_issue(c, ORG_A, "asset_market_backfill_failed", "boom")
rows = c.store.get(("core", "failure_log"), [])
ok("D3 tenants-read failure still logs (fails open)", len(rows) == 1, rows)

# D4: failure_log insert itself raises -> never propagates
class _FailureLogRaise(FakeClient):
    def schema(self, name):
        s = super().schema(name)
        if name == "core":
            orig_table = s.table
            def table(t):
                tt = orig_table(t)
                def insert(rows):
                    raise Exception("insert boom")
                tt.insert = insert
                return tt
            s.table = table
        return s

c = _FailureLogRaise(store={("storeops", "tenants"): []})
try:
    R._log_asset_pipeline_issue(c, ORG_A, "asset_market_backfill_failed", "boom")
    ok("D4 a failing failure_log insert never raises out", True)
except Exception as e:
    ok("D4 a failing failure_log insert never raises out", False, e)

print("\nE. router.py — process_asset_ledger_bytes market-backfill resilience")


# Monkeypatch the module's own helper functions to observe call order / simulate a market-backfill
# crash, without touching the real DB or the parser (this proves the CONTROL FLOW fix, independent
# of what _backfill_market/_sync_* actually do).
calls = []


def _boom_backfill_market(client, org_id):
    calls.append("market")
    raise RuntimeError("simulated store_mapping read error")


def _ok_selling_price(client, org_id):
    calls.append("selling_price")
    return 0


def _ok_appeal(client, org_id):
    calls.append("appeal")
    return 0


def _ok_rma(client, org_id):
    calls.append("rma")
    return 0


def _ok_undercharge(client, org_id):
    calls.append("undercharge")
    return 0


orig = (R._backfill_market, R._backfill_selling_price, R._sync_appeal_flags,
        R._sync_rma_flags, R._sync_undercharge_flags)
R._backfill_market = _boom_backfill_market
R._backfill_selling_price = _ok_selling_price
R._sync_appeal_flags = _ok_appeal
R._sync_rma_flags = _ok_rma
R._sync_undercharge_flags = _ok_undercharge


class _StagingUnavailable(FakeClient):
    """Force the legacy_direct swap path (simplest to simulate) so this test focuses purely on
    what happens AFTER the swap, not the staging machinery (covered by asset-2's own harness)."""
    def schema(self, name):
        s = super().schema(name)
        if name == "commcalc":
            orig_table = s.table
            def table(t):
                tt = orig_table(t)
                if t == "asset_ledger_staging":
                    def select(*a, **k):
                        raise Exception("PGRST205 does not exist")
                    tt.select = select
                return tt
            s.table = table
        return s


import app.modules.asset.asset_parser as PARSER  # noqa: E402
orig_parse = PARSER.parse_asset_ledger
PARSER.parse_asset_ledger = lambda file_bytes, org_id: [{"org_id": org_id, "esn_imei": "1"}]

orig_sb = R.sb
R.sb = lambda: _StagingUnavailable(store={("storeops", "tenants"): [], ("commcalc", "carrier"): []})

try:
    res = R.process_asset_ledger_bytes(b"whatever", ORG_A)
    ok("E1 upload completes despite market-backfill exception", res.get("rows_imported") == 1, res)
    ok("E2 market backfill was attempted", "market" in calls, calls)
    ok("E3 selling-price backfill STILL ran after market backfill raised", "selling_price" in calls, calls)
    ok("E4 all three flag syncs STILL ran after market backfill raised",
       {"appeal", "rma", "undercharge"} <= set(calls), calls)
finally:
    R._backfill_market, R._backfill_selling_price, R._sync_appeal_flags, \
        R._sync_rma_flags, R._sync_undercharge_flags = orig
    PARSER.parse_asset_ledger = orig_parse
    R.sb = orig_sb

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
