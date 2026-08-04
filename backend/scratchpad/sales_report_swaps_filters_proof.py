"""Proof for agent/commission/sales-report-totals (v2 rework) — the SWAPS count + the RULE FIVE §3d
pick-don't-type filter options (`stores` / `markets`) now returned by GET /sales-report.

Pure unit test over the REAL router.sales_report endpoint (monkeypatched sb() → in-memory FakeClient);
NO live DB. Drives the whole handler so the swaps bucketing, the finalize loop, the totals and the
filter-option builders are all exercised as they actually run.

Run:  cd backend && python3 scratchpad/sales_report_swaps_filters_proof.py

Proves:
 1. SWAPS is a distinct-trans_id count of Contract Types containing 'swap' (case-insensitive:
    SIM / device / warranty / BYOD swaps) — per (store,rep,day) AND in the period totals.
 2. Swaps detection is INDEPENDENT: activations / byod / upgrades are byte-identical to a
    classifier-only computation (the shared classify_contract_type is untouched) — money-safe.
 3. 'BYOD Swap' is DELIBERATELY counted in BOTH byod (classifier) and swaps (contains 'swap'),
    and swaps is DISTINCT-trans_id (a 2-line BYOD Swap → swaps += 1, not 2).
 4. `stores` (union rows actually shown) + `markets` (rows' resolved market ∪ every store_mapping
    market — a market with no sales is still a valid pick) are returned for the page's MultiSelects.
"""


def run_route(x):
    """Call a commcalc route handler in EITHER shape.

    ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers were converted from `async def` to
    `def` so FastAPI runs them in the threadpool instead of on the single uvicorn event loop (an
    `async def` doing blocking Supabase I/O froze the whole product for its duration). The only textual
    change was the keyword. This helper awaits a coroutine when it gets one and passes a plain result
    straight through, so the proof works against BOTH shapes and needs no further edit if a handler
    ever legitimately becomes a coroutine again."""
    import asyncio as _a
    return _a.run(x) if _a.iscoroutine(x) else x
import sys, os, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date as _date  # noqa: E402
from app.modules.commcalc import router  # noqa: E402
from app.modules.commcalc.calculator import classify_contract_type  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


# ── in-memory chainable Supabase stub (same shape as exec_mtd / promotion_dedup proofs) ──────────
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, client, table):
        self.c = client
        self.table = table
        self.count_mode = False

    def select(self, *a, **kw):
        if kw.get("count"):
            self.count_mode = True
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        rows = self.c.tables.get(self.table, [])
        if self.count_mode:
            return _Resp(count=sum(1 for r in rows if str(r.get("category") or "") != ""))
        return _Resp(data=list(rows))


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def schema(self, _s):
        return self

    def table(self, t):
        return _Query(self, t)


# open month = the current calendar month (so _sales_rows_union treats the feed as primary)
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
D1 = f"{OPEN}-01"


def srow(store, rep, ct, tid, cat="CellPhone", ext=100.0):
    return {"trans_id": tid, "trans_date": D1, "store": store, "salesperson": rep,
            "category": cat, "contract_type": ct, "department": "", "product_desc": "",
            "ext_price": ext, "gp": 20.0, "voided": "", "trans_type": "", "org_id": "o", "period": OPEN}


# STORE A — three reps exercising every bucket + the BYOD-Swap overlap; STORE B — one activation.
feed = [
    # JANE @ A: one activation, one upgrade, one byod  (swaps 0)
    srow("STORE A", "JANE", "Activation", "T1"),
    srow("STORE A", "JANE", "Upgrade", "T2"),
    srow("STORE A", "JANE", "BYOD Activation", "T3"),
    # BOB @ A: two SWAP-only transactions (classifier → None; contains 'swap' → swaps)
    srow("STORE A", "BOB", "SIM Swap", "T4"),
    srow("STORE A", "BOB", "Device Swap", "T5"),
    # AMY @ A: a BYOD Swap on TWO lines (same trans_id) → byod +1 AND swaps +1 (distinct-trans_id)
    srow("STORE A", "AMY", "BYOD Swap", "T6"),
    srow("STORE A", "AMY", "BYOD Swap", "T6"),
    # STORE B: CARL one Port-In activation
    srow("STORE B", "CARL", "Port In", "T7"),
]
# store_mapping: A→North, B→South, and a SALES-LESS store C→West (its market must still be a pick)
store_mapping = [
    {"store_code": "A", "store_address": "STORE A", "market": "North"},
    {"store_code": "B", "store_address": "STORE B", "market": "South"},
    {"store_code": "C", "store_address": "STORE C", "market": "West"},
]

fc = FakeClient({"daily_sales_feed": feed, "raw_sales": [], "store_mapping": store_mapping,
                 "accessory_config": [], "flag_rules": [], "gp_category_map": [], "stores": []})

# Drive the REAL endpoint (monkeypatch sb()).
_orig_sb = router.sb
router.sb = lambda: fc
try:
    res = run_route(router.sales_report(period=OPEN, authorization="", org_id="o"))
finally:
    router.sb = _orig_sb

rows = res["rows"]
totals = res["totals"]
by = {(r["store"], r["salesperson"]): r for r in rows}

print("(1) SWAPS bucket — per-cell + totals")
check("BOB @ STORE A swaps == 2 (SIM Swap + Device Swap)", by[("STORE A", "BOB")]["swaps"] == 2)
check("AMY @ STORE A swaps == 1 (BYOD Swap, distinct trans_id over 2 lines)", by[("STORE A", "AMY")]["swaps"] == 1)
check("JANE @ STORE A swaps == 0 (no swap contract types)", by[("STORE A", "JANE")]["swaps"] == 0)
check("period totals.swaps == 3 (2 + 1)", totals["swaps"] == 3)
check("every row carries a 'swaps' key", all("swaps" in r for r in rows))
check("totals carries a 'swaps' key", "swaps" in totals)

print("\n(2) INDEPENDENCE — activations/byod/upgrades unchanged by swap detection (money-safe)")
# recompute the three EXISTING buckets from the shared classifier ONLY (no swap logic at all)
exp = {}
for r in feed:
    k = (r["store"], r["salesperson"])
    d = exp.setdefault(k, {"a": set(), "b": set(), "u": set()})
    cls = classify_contract_type(r["contract_type"])
    tid = r["trans_id"]
    if cls == "byod":
        d["b"].add(tid)
    elif cls == "upgrade":
        d["u"].add(tid)
    elif cls == "premium":
        d["a"].add(tid)
ok_ind = True
for k, d in exp.items():
    row = by[k]
    if not (row["activations"] == len(d["a"]) and row["byod"] == len(d["b"]) and row["upgrades"] == len(d["u"])):
        ok_ind = False
check("per-cell activations/byod/upgrades == classifier-only result (swap logic changed nothing)", ok_ind)
check("totals.activations == 2 (JANE Activation + CARL Port-In)", totals["activations"] == 2)
check("totals.byod == 2 (JANE BYOD Activation + AMY BYOD Swap)", totals["byod"] == 2)
check("totals.upgrades == 1 (JANE Upgrade)", totals["upgrades"] == 1)

print("\n(3) 'BYOD Swap' overlap is DELIBERATE — counted in BOTH byod and swaps")
amy = by[("STORE A", "AMY")]
check("AMY BYOD Swap → byod == 1 (classifier, unchanged)", amy["byod"] == 1)
check("AMY BYOD Swap → swaps == 1 (contains 'swap', independent tally)", amy["swaps"] == 1)
check("classify_contract_type('BYOD Swap') is still 'byod' (shared classifier untouched)",
      classify_contract_type("BYOD Swap") == "byod")

print("\n(4) RULE FIVE §3d pick-don't-type filter options returned to the page")
check("res['stores'] == distinct union stores shown (['STORE A','STORE B'])",
      res["stores"] == ["STORE A", "STORE B"])
check("res['markets'] == rows' markets ∪ store_mapping markets incl. sales-less 'West'",
      res["markets"] == ["North", "South", "West"])
check("row market resolves via store_mapping address (STORE A → North)",
      by[("STORE A", "JANE")]["market"] == "North")

print(f"\n{'PASS' if FAIL == 0 else 'FAIL'} — {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
