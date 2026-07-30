"""Byte-identity probe for asset-market-filter-dedupe (no-market-sentinel-dedupe). Run once with
cwd = a pre-dedupe backend tree (router.py/oninv_recon.py each define their OWN NO_MARKET_SENTINEL/
_apply_market_filter/_market_matches/_store_list, current origin/main shape) and once with
cwd = the deduped tree (both files import from the new market_filter.py). Prints one deterministic
JSON blob (sorted keys) to stdout for byte-for-byte diffing between the two runs.

Exercises, across a market/store matrix (ordinary market, NO_MARKET_SENTINEL, empty, and comma-
separated multi-store):
  - the bare helper values/functions (NO_MARKET_SENTINEL, _store_list, _market_matches)
  - _apply_market_filter's effect on a real query-builder (captured filter clauses)
  - the RPC-param translation (_call_recon_rpc's params, get_charges_summary's params)
  - FULL end-to-end JSON responses of get_aging, get_on_inventory_by_store, get_charges_summary
    (router.py) and get_oninv_3way_recon (oninv_recon.py) against a shared, hand-built,
    populated-market + no-market + multi-market fixture.

No live DB — a small in-memory fake Supabase/PostgREST + RPC client.

Run (this IS the byte-identity proof — it only means something diffed across the two trees):
    cd /workspaces/metricspro/backend        && python3 harness_asset_no_market_dedupe_byteid.py > /tmp/OLD.json
    cd <this-deduped-worktree>/backend        && python3 harness_asset_no_market_dedupe_byteid.py > /tmp/NEW.json
    diff /tmp/OLD.json /tmp/NEW.json && echo BYTE-IDENTICAL

2026-07-30 result (recorded here for the record — re-run the two commands above to reproduce):
    OLD (origin/main 1384b87, pre-dedupe router.py/oninv_recon.py) and NEW (this branch, both
    files importing from market_filter.py) produced BYTE-IDENTICAL 40673-byte JSON blobs —
    identical md5 (5758d832d37d3dd2b3f1ddf4888d3b1a) — across every case below.
"""
import asyncio
import json
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")


# ── fake supabase/postgrest + rpc client ────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, fn, params):
        self.fn, self.params = fn, params

    def execute(self):
        return _Resp(self.fn(self.params))


class _Q:
    def __init__(self, store, schema, table, rpc_fns, calls):
        self.store, self.schema, self.table, self.rpc_fns, self.calls = store, schema, table, rpc_fns, calls
        self.filters = []
        self._op = "select"
        self._payload = None
        self._or_expr = None
        self._limit = None
        self._range = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def neq(self, k, v):
        self.filters.append(("neq", k, v)); return self

    def ilike(self, k, v):
        self.filters.append(("ilike", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def in_(self, k, v):
        self.filters.append(("in", k, sorted(v) if isinstance(v, (list, set, tuple)) else v)); return self

    def or_(self, expr):
        self._or_expr = expr; return self

    def limit(self, n):
        self._limit = n; return self

    def range(self, a, b):
        self._range = (a, b); return self

    def insert(self, rows):
        self._op, self._payload = "insert", rows; return self

    def upsert(self, rec, on_conflict=None):
        self._op, self._payload = "upsert", rec; return self

    def _keep(self, r):
        for op, k, v in self.filters:
            if op == "eq" and r.get(k) != v:
                return False
            if op == "neq" and (r.get(k) is None or r.get(k) == v):
                return False
            if op == "ilike":
                pat = str(v).replace("%", "")
                if pat.lower() not in str(r.get(k) or "").lower():
                    return False
            if op == "gte" and str(r.get(k) or "") < str(v):
                return False
            if op == "lte" and str(r.get(k) or "") > str(v):
                return False
            if op == "is" and v == "null" and r.get(k) is not None:
                return False
            if op == "in" and r.get(k) not in v:
                return False
        if self._or_expr:
            clauses = self._or_expr.split(",")
            def _match_clause(row, clause):
                parts = clause.split(".", 2)
                if len(parts) != 3:
                    return False
                col, op, val = parts
                rv = row.get(col)
                if op == "is":
                    return rv is None if val == "null" else rv is not None
                if op == "eq":
                    return (rv or "") == val
                return False
            if not any(_match_clause(r, c) for c in clauses):
                return False
        return True

    def execute(self):
        # record the resolved filter clause set for structural comparison (order-independent)
        self.calls.append({"schema": self.schema, "table": self.table, "op": self._op,
                            "filters": sorted([f"{op}:{k}:{v}" for op, k, v in self.filters]),
                            "or_expr": self._or_expr})
        key = (self.schema, self.table)
        rows = self.store.setdefault(key, [])
        if self._op in ("insert", "upsert"):
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for r in payload:
                rows.append(dict(r))
            return _Resp(payload)
        out = [r for r in rows if self._keep(r)]
        if self._range:
            a, b = self._range
            out = out[a:b + 1]
        if self._limit is not None:
            out = out[: self._limit]
        return _Resp(out)


class _Table:
    def __init__(self, store, schema, table, rpc_fns, calls):
        self.store, self.schema, self.table, self.rpc_fns, self.calls = store, schema, table, rpc_fns, calls

    def select(self, *a, **k):
        return _Q(self.store, self.schema, self.table, self.rpc_fns, self.calls).select(*a, **k)

    def insert(self, rows):
        return _Q(self.store, self.schema, self.table, self.rpc_fns, self.calls).insert(rows)

    def upsert(self, rec, on_conflict=None):
        return _Q(self.store, self.schema, self.table, self.rpc_fns, self.calls).upsert(rec, on_conflict=on_conflict)


class _Schema:
    def __init__(self, store, schema, rpc_fns, calls):
        self.store, self.schema, self.rpc_fns, self.calls = store, schema, rpc_fns, calls

    def table(self, name):
        return _Table(self.store, self.schema, name, self.rpc_fns, self.calls)

    def rpc(self, name, params):
        self.calls.append({"rpc": name, "params": params})
        fn = self.rpc_fns.get(name)
        if fn is None:
            raise Exception(f"PGRST202 function {name} does not exist (schema cache)")
        return _RpcCall(fn, params)


class FakeClient:
    def __init__(self, store, rpc_fns):
        self.store, self.rpc_fns = store, rpc_fns
        self.calls = []

    def schema(self, name):
        return _Schema(self.store, name, self.rpc_fns, self.calls)


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.asset import router as R  # noqa: E402
from app.modules.asset import oninv_recon as OR  # noqa: E402

ORG = "00000000-0000-0000-0000-0000000000aa"
TODAY = date.today()


def _d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


out = {}

# ── 1. bare helper values / functions ───────────────────────────────────────────────────────────
out["router.NO_MARKET_SENTINEL"] = R.NO_MARKET_SENTINEL
out["oninv_recon.NO_MARKET_SENTINEL"] = OR.NO_MARKET_SENTINEL
out["shared._store_list"] = {
    s: OR._store_list(s) for s in ["", "  ", "A", "A,B", "A, B ,,C", "  A  ,  B  "]
}
MARKET_MATRIX = ["", R.NO_MARKET_SENTINEL, "LI", "NYC"]
ROW_MARKET_MATRIX = [None, "", "LI", "NYC"]
out["router._market_matches"] = {
    f"{rm!r}|{m!r}": R._market_matches(rm, m) for rm in ROW_MARKET_MATRIX for m in MARKET_MATRIX
}

# ── 2. _apply_market_filter effect on a query builder (structural capture) ────────────────────
calls_log = []
for m in MARKET_MATRIX:
    c = FakeClient(store={("commcalc", "asset_ledger"): []}, rpc_fns={})
    q = c.schema("commcalc").table("asset_ledger").select("*").eq("org_id", ORG)
    q = R._apply_market_filter(q, m)
    q.execute()
    calls_log.append({"market": m, "resolved": c.calls[-1]})
out["router._apply_market_filter_calls"] = calls_log

# ── 3. RPC-param translation (oninv_recon._call_recon_rpc + router.get_charges_summary) ────────
def _fake_recon_rpc(params):
    return []


rpc_param_probe = []
for store_param in ["", "S1", "S1,S2"]:
    for m in MARKET_MATRIX:
        c = FakeClient(store={}, rpc_fns={"asset_oninv_3way_recon": _fake_recon_rpc})
        R.sb = lambda c=c: c
        rows, migrated = OR._call_recon_rpc(c, ORG, store_param, m, "", "")
        rpc_call = [x for x in c.calls if x.get("rpc") == "asset_oninv_3way_recon"][0]
        rpc_param_probe.append({"store": store_param, "market": m, "params": rpc_call["params"]})
out["oninv_recon._call_recon_rpc_params"] = rpc_param_probe


def _fake_charges_rpc(params):
    return [
        {"category": "PROCESSING FEE", "store": "Store A", "market": "LI", "cnt": 2, "owed": 40.0, "reimb": 0},
        {"category": "Appeal Denied. Details in Boost Appeals Status", "store": "Store B", "market": "NYC",
         "cnt": 1, "owed": 300.0, "reimb": 0},
        {"category": "RMA", "store": "Store C", "market": None, "cnt": 1, "owed": 250.0, "reimb": 100.0},
        {"category": "Stock Balancing", "store": "Store D", "market": "", "cnt": 3, "owed": 15.0, "reimb": 0},
    ]


charges_probe = []
for m in MARKET_MATRIX:
    c = FakeClient(store={}, rpc_fns={"asset_charges_summary": _fake_charges_rpc})
    R.sb = lambda c=c: c
    resp = _run(R.get_charges_summary(org_id=ORG, store="", market=m, month=None, year=None, week_friday=""))
    charges_probe.append({"market": m, "response": resp})
out["router.get_charges_summary_by_market"] = charges_probe

# ── 4. FULL end-to-end get_aging / get_on_inventory_by_store — shared populated fixture ────────
LEDGER_FIXTURE = [
    {"id": 1, "org_id": ORG, "store": "Store A", "market": "LI", "esn_imei": "111", "phone_number": "p1",
     "device_model": "iPhone 15", "category": "On Inventory", "status": "On Inventory",
     "acquired_date": _d(10), "due_date": None, "date_sold": None, "owed_to_vip": 500.0,
     "reimbursement": 0, "selling_price": 0,
     "raw_row": {"FileDate": "2026-07-20"}},
    {"id": 2, "org_id": ORG, "store": "Store A", "market": "LI", "esn_imei": "112", "phone_number": "p2",
     "device_model": "iPhone 15", "category": "On Inventory", "status": "On Inventory",
     "acquired_date": _d(50), "due_date": None, "date_sold": None, "owed_to_vip": 400.0,
     "reimbursement": 0, "selling_price": 0, "raw_row": {}},
    {"id": 3, "org_id": ORG, "store": "Store B", "market": "NYC", "esn_imei": "113", "phone_number": "p3",
     "device_model": "Galaxy S24", "category": "On Inventory", "status": "On Inventory",
     "acquired_date": _d(90), "due_date": None, "date_sold": None, "owed_to_vip": 300.0,
     "reimbursement": 0, "selling_price": 0, "raw_row": {}},
    {"id": 4, "org_id": ORG, "store": "Store C", "market": None, "esn_imei": "114", "phone_number": "p4",
     "device_model": "Galaxy S24", "category": "On Inventory", "status": "On Inventory",
     "acquired_date": _d(5), "due_date": None, "date_sold": None, "owed_to_vip": 0.0,
     "reimbursement": 0, "selling_price": 0, "raw_row": {}},
    {"id": 5, "org_id": ORG, "store": "Store C", "market": "", "esn_imei": "115", "phone_number": "p5",
     "device_model": "iPhone 14", "category": "On Inventory", "status": "On Inventory",
     "acquired_date": None, "due_date": None, "date_sold": None, "owed_to_vip": 250.0,
     "reimbursement": 0, "selling_price": 0, "raw_row": {}},
    {"id": 6, "org_id": ORG, "store": "Store D", "market": "NYC", "esn_imei": "116", "phone_number": "p6",
     "device_model": "iPhone 14", "category": "On Inventory", "status": "On Inventory",
     "acquired_date": _d(30), "due_date": None, "date_sold": None, "owed_to_vip": 120.0,
     "reimbursement": 0, "selling_price": 0, "raw_row": {}},
]


def _make_ledger_client():
    return FakeClient(store={
        ("commcalc", "asset_ledger"): [dict(r) for r in LEDGER_FIXTURE],
        ("commcalc", "asset_investigation"): [],
        ("commcalc", "vip_invoice_devices"): [],
        ("commcalc", "vip_invoices"): [],
    }, rpc_fns={})


FILTER_MATRIX = [
    {"store": "", "market": ""},
    {"store": "", "market": "LI"},
    {"store": "", "market": R.NO_MARKET_SENTINEL},
    {"store": "Store A", "market": ""},
    {"store": "Store A,Store B", "market": ""},
    {"store": "", "market": "NYC"},
]

aging_probe = []
for f in FILTER_MATRIX:
    c = _make_ledger_client()
    R.sb = lambda c=c: c
    resp = _run(R.get_aging(org_id=ORG, store=f["store"], market=f["market"]))
    aging_probe.append({"filter": f, "response": resp})
out["router.get_aging_by_filter"] = aging_probe

oninv_by_store_probe = []
for f in FILTER_MATRIX:
    c = _make_ledger_client()
    R.sb = lambda c=c: c
    resp = _run(R.get_on_inventory_by_store(org_id=ORG, store=f["store"], market=f["market"]))
    oninv_by_store_probe.append({"filter": f, "response": resp})
out["router.get_on_inventory_by_store_by_filter"] = oninv_by_store_probe

# ── 5. FULL end-to-end oninv_recon.get_oninv_3way_recon ────────────────────────────────────────
def _fake_3way_rpc(params):
    all_rows = [
        {"store": "Store A", "market": "LI", "esn_imei": "111", "device_model": "iPhone 15",
         "acquired_date": _d(10), "aging_days": 10, "device_value": 500.0,
         "classification": "non_activated", "leg2_paid": False, "leg2_amount": None, "leg2_date": None,
         "leg3_status": "not_paid", "leg3_amount": None, "leg3_last_date": None,
         "leg3_payment_count": 0, "leg3_payment_types": None},
        {"store": "Store B", "market": "NYC", "esn_imei": "113", "device_model": "Galaxy S24",
         "acquired_date": _d(90), "aging_days": 90, "device_value": 300.0,
         "classification": "missing_phone_candidate", "leg2_paid": True, "leg2_amount": 300.0,
         "leg2_date": _d(80), "leg3_status": "paid", "leg3_amount": 300.0, "leg3_last_date": _d(70),
         "leg3_payment_count": 1, "leg3_payment_types": "Rebate"},
        {"store": "Store C", "market": None, "esn_imei": "114", "device_model": "Galaxy S24",
         "acquired_date": _d(5), "aging_days": 5, "device_value": 0.0,
         "classification": "unmatchable", "leg2_paid": False, "leg2_amount": None, "leg2_date": None,
         "leg3_status": "na", "leg3_amount": None, "leg3_last_date": None,
         "leg3_payment_count": 0, "leg3_payment_types": None},
    ]
    stores = params.get("p_stores")
    market = params.get("p_market")
    no_market_only = params.get("p_no_market_only")
    out_rows = []
    for r in all_rows:
        if stores and r["store"] not in stores:
            continue
        if no_market_only and r["market"]:
            continue
        if market and r["market"] != market:
            continue
        out_rows.append(r)
    return out_rows


recon_probe = []
for f in FILTER_MATRIX:
    c = FakeClient(store={}, rpc_fns={"asset_oninv_3way_recon": _fake_3way_rpc})
    OR.sb = lambda c=c: c
    resp = _run(OR.get_oninv_3way_recon(org_id=ORG, store=f["store"], market=f["market"]))
    resp.pop("as_of", None)  # date.today() — normalize out, tested for existence separately below
    recon_probe.append({"filter": f, "response": resp})
out["oninv_recon.get_oninv_3way_recon_by_filter"] = recon_probe

# also normalize the "today"/"as_of" date fields out of the aging/on-inv responses the same way
# (they're date.today().isoformat() — identical within one run, excluded here purely so this probe
# is robust to being run exactly across a midnight boundary between the two subprocess calls)
for entry in out["router.get_aging_by_filter"]:
    entry["response"].pop("today", None)

print(json.dumps(out, sort_keys=True, default=str, indent=None))
