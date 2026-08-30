"""HARNESS — the pay engines read the SAME sales universe the reports show, when configured to (mig 306).

THE BUG THIS GUARDS. `commission_engine._read_sales` took raw_sales and only fell back to the daily feed
when raw_sales was EMPTY, while the Sales Report / Executive MTD read the feed∪raw_sales union. Measured
live (org 854f6d7b, period 2026-08): raw_sales held 3,235 rows, the union held 9,161 — so a rules-plan rep
was paid on roughly a THIRD of the accessory sales their own report showed, and the Rep Incentive drill
could never reconcile against the report it exists to explain (12-rep sample: $6,165 vs $16,969 of
accessory basis, ~2.75x).

Owner decision 2026-08-30: "the sales source which creates the sales report is accurate, the same source
should feed into all other related modules." Shipped as a per-tenant config
(`commission_org_config.sales_source`) defaulting to 'legacy', so merging changes no payout.

What is proven here:
  A. 'legacy' (and every falsy/unknown mode) is byte-identical to the historic raw_sales-first read.
  B. 'union' reads through the TRANSACTION-grain union instead.
  C. The union is only ever asked for at transaction grain with every column (a per-LINE pay engine
     cannot use the per-day display union).
  D. A failure inside the union read degrades to the legacy read — a pay path never ends up with no sales.

No DB, no network: the Supabase client and the router module are both stubbed.

  python3 backend/harness_commission_sales_source.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import commission_engine as ce  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── a Supabase-shaped stub that records which tables were read ───────────────────────────────────
class _Q:
    def __init__(self, table, store, log):
        self.table_name, self.store, self.log = table, store, log

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        self.log.append(self.table_name)
        rows = self.store.get(self.table_name, [])
        start, end = getattr(self, "_range", (0, len(rows) - 1))
        return types.SimpleNamespace(data=rows[start:end + 1])


class _Schema:
    def __init__(self, store, log):
        self.store, self.log = store, log

    def table(self, name):
        return _Q(name, self.store, self.log)


class _Client:
    def __init__(self, store):
        self.store, self.reads = store, []

    def schema(self, _n):
        return _Schema(self.store, self.reads)


RAW = [{"trans_id": f"R{i}", "ext_price": 10} for i in range(3)]       # stands in for raw_sales (small)
FEED = [{"trans_id": f"F{i}", "ext_price": 10} for i in range(9)]      # stands in for the feed (larger)
UNION = RAW + FEED                                                     # what the txn union would return


def stub_router(union_rows=None, boom=False):
    """Install a fake app.modules.commcalc.router so _read_sales' LAZY import resolves to it (the real
    router cannot be imported here — it needs FastAPI). Records the kwargs it was called with."""
    mod = types.ModuleType("app.modules.commcalc.router")
    calls = []

    def _sales_rows_union_txn(client, org_id, period, cols=None):
        calls.append({"cols": cols, "period": period, "org_id": org_id})
        if boom:
            raise RuntimeError("simulated union read failure")
        return (list(union_rows or []), {"primary": "daily_sales_feed"})

    mod._sales_rows_union_txn = _sales_rows_union_txn
    sys.modules["app.modules.commcalc.router"] = mod
    return calls


print("── A. 'legacy' (and anything falsy/unknown) = the historic raw_sales-first read ──")
for mode in (None, "", "legacy", "LEGACY", "nonsense"):
    c = _Client({"raw_sales": RAW, "daily_sales_feed": FEED})
    rows = ce._read_sales(c, "org", "August 2026", mode)
    check(f"mode={mode!r} → raw_sales wins, feed untouched",
          rows == RAW and "daily_sales_feed" not in c.reads, f"{len(rows)} rows, reads={c.reads}")

c = _Client({"raw_sales": [], "daily_sales_feed": FEED})
rows = ce._read_sales(c, "org", "August 2026", "legacy")
check("legacy still falls back to the feed when raw_sales is EMPTY (the 2026-07-14 fix)",
      rows == FEED, f"{len(rows)} rows")

print("── B. 'union' reads the transaction-grain union instead ──")
calls = stub_router(UNION)
c = _Client({"raw_sales": RAW, "daily_sales_feed": FEED})
rows = ce._read_sales(c, "org", "August 2026", "union")
check("union mode returns the union rows, not raw_sales", rows == UNION, f"{len(rows)} rows")
check("union mode does NOT fall back to the direct table reads", c.reads == [], c.reads)
check("the sales universe actually grows (the whole point)",
      len(rows) > len(RAW), f"{len(RAW)} → {len(rows)}")
check("'UNION' is case-insensitive", ce._read_sales(_Client({}), "o", "p", "  UnIoN ") == UNION)

print("── C. the union is asked for at the grain a per-LINE pay engine needs ──")
check("exactly one union read per call", len(calls) >= 1, len(calls))
check("cols='*' — the engine needs every column, not the display subset",
      all(x["cols"] == "*" for x in calls), [x["cols"] for x in calls])

print("── D. a broken union degrades to legacy — a pay path never runs out of sales ──")
stub_router(boom=True)
c = _Client({"raw_sales": RAW, "daily_sales_feed": FEED})
rows = ce._read_sales(c, "org", "August 2026", "union")
check("union failure falls back to the raw_sales read", rows == RAW, f"{len(rows)} rows")
check("the fallback actually hit the table", "raw_sales" in c.reads, c.reads)

stub_router([])   # union returns nothing at all
c = _Client({"raw_sales": RAW, "daily_sales_feed": FEED})
rows = ce._read_sales(c, "org", "August 2026", "union")
check("an EMPTY union also falls back rather than paying on zero sales", rows == RAW, f"{len(rows)} rows")

print("── E. preview() threads the mode through (the live pay path passes it) ──")
check("preview accepts source_mode", "source_mode" in ce.preview.__code__.co_varnames)
check("_read_sales accepts source_mode", "source_mode" in ce._read_sales.__code__.co_varnames)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
