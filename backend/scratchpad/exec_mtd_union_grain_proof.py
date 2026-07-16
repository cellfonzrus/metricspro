"""Proof for agent/commission/execmtd-union-grain — the luxelink July 2026 Exec-MTD "only 6 NY stores"
incident. Pure unit tests over the REAL router functions; NO live DB.

Run:  cd backend && python3 scratchpad/exec_mtd_union_grain_proof.py

Task 1 — _sales_rows_union merges at (day x store) CELL grain (was per-DAY):
 (a) OPEN month, feed carries 6 NY stores + raw_sales carries 19 stores on the SAME days →
     the union shows ALL 19, and the feed WINS its own 6 store cells (its rows, not raw's)
 (b) a store cell present in BOTH sources → the primary (feed) wins it
 (c) a store cell where the OTHER source is materially richer (>=10 rows AND primary <50%) is
     SWAPPED to the other; boundary (exactly 50% → no) + floor (<10 rows → no)
 (d) CLOSED month → raw_sales leads unchanged (all 19 stores, feed contributes nothing)
 (e) meta reports store coverage (stores_shown / primary_stores / stores_from_other / *_cells)

Task 2 — /exec-mtd filters the union rows SERVER-SIDE before bucketing (store/market/rep):
 (f) no filter → all stores + reps present; filters options listed from the real data
 (g) store filter → by_location + by_employee both restricted; rep filter likewise; both consistent
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date as _date  # noqa: E402
from app.modules.commcalc import router  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


# ── Fake Supabase client (chainable, in-memory) — mirrors the promotion_dedup_proof pattern ──────
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
            # mirrors _open_month_source._cat: count rows with a non-blank category
            return _Resp(count=sum(1 for r in rows if str(r.get("category") or "") != ""))
        return _Resp(data=list(rows))


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def schema(self, _s):
        return self

    def table(self, t):
        return _Query(self, t)


# open month = the current calendar month; closed = last month (drives _open_month_source / _is_open_month)
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
_py, _pm = (_T.year - 1, 12) if _T.month == 1 else (_T.year, _T.month - 1)
CLOSED = f"{_py}-{_pm:02d}"
D1, D2 = f"{OPEN}-01", f"{OPEN}-02"          # two open-month days
CD1, CD2 = f"{CLOSED}-01", f"{CLOSED}-02"    # two closed-month days


def srow(store, day, src, n=0, cat="Accessory", rep="REP", ct="", dept="", pdesc="", ext=10.0):
    return {"trans_id": f"{src}-{store}-{day}-{n}", "trans_date": day, "store": store,
            "salesperson": rep, "category": cat, "contract_type": ct, "department": dept,
            "product_desc": pdesc, "ext_price": ext, "gp": 5.0, "voided": "", "trans_type": "",
            "_src": src, "org_id": "o", "period": OPEN}


def many(store, day, src, count, **kw):
    return [srow(store, day, src, n=i, **kw) for i in range(count)]


# ══ (a) OPEN month: feed 6 NY stores, raw 19 stores same days → union shows all 19, feed wins its cells ══
print("(a) open month — feed 6 NY stores + raw 19 stores same days → all 19 shown, feed wins its 6 cells")
NY = [f"NY{i}" for i in range(1, 7)]                 # 6 stores the daily feed carries
OTHERS = [f"CHI{i}" for i in range(1, 14)]           # 13 stores ONLY in the re-uploaded raw_sales
ALL19 = NY + OTHERS
feed_rows, raw_rows = [], []
for st in NY:
    for d in (D1, D2):
        feed_rows += many(st, d, "FEED", 3)          # feed: only the 6 NY stores
for st in ALL19:
    for d in (D1, D2):
        raw_rows += many(st, d, "RAW", 3)            # raw: all 19 stores (the full re-upload)

fc = FakeClient({"daily_sales_feed": feed_rows, "raw_sales": raw_rows})
merged, meta = router._sales_rows_union(fc, "o", OPEN)
shown = {str(r.get("store")) for r in merged}
check("feed is the primary for the open month", meta["primary"] == "daily_sales_feed")
check("union shows ALL 19 stores (was ~6 before the cell-grain fix)", shown == set(ALL19))
ny_srcs = {r["_src"] for r in merged if r["store"] in NY}
other_srcs = {r["_src"] for r in merged if r["store"] in OTHERS}
check("the 6 feed (NY) store cells are won by the FEED (feed rows, not raw's)", ny_srcs == {"FEED"})
check("the 13 other stores are FILLED from raw_sales", other_srcs == {"RAW"})
# NY: 6 stores x 2 days x 3 feed rows = 36 ; OTHERS: 13 x 2 x 3 raw rows = 78 → 114
check("merged row count = feed's 6-store cells + raw's 13-store cells (36 + 78 = 114)", len(merged) == 114)

# ══ (b) a cell in BOTH sources → primary wins it ══
print("(b) a store-day cell present in both sources → the primary (feed) wins")
ny1_d1 = [r for r in merged if r["store"] == "NY1" and str(r["trans_date"])[:10] == D1]
check("NY1 on day1 (in both feed+raw) shows only the FEED copy", ny1_d1 and all(r["_src"] == "FEED" for r in ny1_d1))
check("NY1 day1 not double-counted (3 rows, not 6)", len(ny1_d1) == 3)

# ══ (c) richer-cell swap at the 10-row floor / 50% ratio threshold ══
print("(c) richer-cell swap — other >=10 rows AND primary <50%; boundary + floor")
# feed NY1/day1 = 4 rows, raw NY1/day1 = 12 rows → 12>=10 and 4 < 0.5*12=6 → SWAP to raw
sf = many("NY1", D1, "FEED", 4) + many("NY2", D1, "FEED", 3)
sr = many("NY1", D1, "RAW", 12) + many("NY2", D1, "RAW", 3)
fc2 = FakeClient({"daily_sales_feed": sf, "raw_sales": sr})
m2, meta2 = router._sales_rows_union(fc2, "o", OPEN)
ny1 = [r for r in m2 if r["store"] == "NY1"]
ny2 = [r for r in m2 if r["store"] == "NY2"]
check("degraded NY1/day1 cell SWAPPED to raw_sales (12 raw rows)", len(ny1) == 12 and all(r["_src"] == "RAW" for r in ny1))
check("healthy NY2/day1 cell NOT swapped (feed keeps it)", len(ny2) == 3 and all(r["_src"] == "FEED" for r in ny2))
check("meta.richer_cells == 1", meta2["richer_cells"] == 1)
# direct-threshold unit checks on _merge_cells_richer
cell = lambda r: (str(r.get("trans_date"))[:10], router._cell_store_key(r.get("store")))
# exactly 50% (6 of 12) → NOT swapped
_, rc_eq, _ = router._merge_cells_richer(many("S", D1, "P", 6), many("S", D1, "O", 12), cell)
check("primary at exactly 50% → NOT swapped", rc_eq == [])
# just under 50% (5 of 12) → swapped
_, rc_lt, _ = router._merge_cells_richer(many("S", D1, "P", 5), many("S", D1, "O", 12), cell)
check("primary just under 50% → swapped", len(rc_lt) == 1)
# other below the 10-row floor → never swaps however tiny the primary
_, rc_floor, _ = router._merge_cells_richer(many("S", D1, "P", 1), many("S", D1, "O", 9), cell)
check("other below the 10-row floor → NOT swapped (noise-protected)", rc_floor == [])
# store-key canonicalization: case/whitespace drift does NOT split a cell (no double count)
p_drift = many("NY store", D1, "P", 3)
o_drift = many("ny  STORE", D1, "O", 3)               # same store, cosmetic drift
mrg_d, _, fill_d = router._merge_cells_richer(p_drift, o_drift, cell)
check("case/whitespace store drift stays ONE cell (primary wins, no fill/double-count)",
      len(mrg_d) == 3 and fill_d == [])

# ══ (d) CLOSED month → raw_sales leads, unchanged (all 19 stores, feed contributes nothing) ══
print("(d) closed month — raw_sales leads unchanged, all 19 stores, feed adds nothing")
cfeed, craw = [], []
for st in NY:
    for d in (CD1, CD2):
        cfeed += many(st, d, "FEED", 3)
        cfeed[-1]["period"] = CLOSED
for st in ALL19:
    for d in (CD1, CD2):
        craw += many(st, d, "RAW", 3)
        craw[-1]["period"] = CLOSED
for r in cfeed:
    r["period"] = CLOSED
for r in craw:
    r["period"] = CLOSED
fc3 = FakeClient({"daily_sales_feed": cfeed, "raw_sales": craw})
m3, meta3 = router._sales_rows_union(fc3, "o", CLOSED)
shown3 = {str(r.get("store")) for r in m3}
check("raw_sales is primary for the closed month", meta3["primary"] == "raw_sales")
check("closed month shows all 19 stores", shown3 == set(ALL19))
check("every closed-month row comes from raw_sales (feed contributes nothing — raw has all cells)",
      {r["_src"] for r in m3} == {"RAW"})
check("closed month: no filled/richer cells (raw already complete)",
      meta3["filled_cells"] == 0 and meta3["richer_cells"] == 0)

# ══ (e) meta reports coverage ══
print("(e) meta store-coverage transparency")
check("meta.stores_shown == 19 (case a)", meta["stores_shown"] == 19)
check("meta.primary_stores == 6 (the feed's stores)", meta["primary_stores"] == 6)
check("meta.other_stores == 19 (raw_sales)", meta["other_stores"] == 19)
check("meta.stores_from_other == 13 (only in raw_sales)", meta["stores_from_other"] == 13)
check("meta.filled_cells == 26 (13 stores x 2 days filled from raw)", meta["filled_cells"] == 26)
check("meta.richer_cells == 0 (case a — no degraded feed cell)", meta["richer_cells"] == 0)
check("meta.filled_days rolls up to the 2 days", set(meta["filled_days"]) == {D1, D2})
check("meta.shown_rows == len(merged)", meta["shown_rows"] == len(merged))

# ══ (f) + (g) /exec-mtd server-side filtering ══
print("(f/g) exec-mtd server-side filters — store/rep restrict BOTH tables consistently")
# open-month feed: 2 stores, 2 reps, activation lines (contract_type non-blank = an activation)
ef = []
ef += many("NY1", D1, "FEED", 1, rep="ALICE", cat="CellPhone", ct="Port In")
ef += many("NY1", D1, "FEED", 1, rep="BOB", cat="CellPhone", ct="Upgrade")
ef += many("CHI1", D1, "FEED", 1, rep="ALICE", cat="CellPhone", ct="New Activation")
ef += many("CHI1", D1, "FEED", 1, rep="CARLA", cat="CellPhone", ct="BYOD")
fc4 = FakeClient({"daily_sales_feed": ef, "raw_sales": [], "store_mapping": [], "stores": [],
                  "exec_metric_config": []})

res = router._exec_mtd(fc4, "o", OPEN)
loc = {r["store"] for r in res["by_location"]["rows"]}
emp = {r["employee"] for r in res["by_employee"]["rows"]}
check("no filter → both stores in by_location", loc == {"NY1", "CHI1"})
check("no filter → all 3 reps in by_employee", emp == {"ALICE", "BOB", "CARLA"})
check("filters.stores lists both real stores", set(res["filters"]["stores"]) == {"NY1", "CHI1"})
check("filters.reps lists the 3 real reps (pick-don't-type)", set(res["filters"]["reps"]) == {"ALICE", "BOB", "CARLA"})

res_s = router._exec_mtd(fc4, "o", OPEN, stores=["NY1"])
loc_s = {r["store"] for r in res_s["by_location"]["rows"]}
emp_s = {r["employee"] for r in res_s["by_employee"]["rows"]}
check("store=NY1 → by_location only NY1", loc_s == {"NY1"})
check("store=NY1 → by_employee only reps who sold at NY1 (ALICE, BOB — not CARLA)", emp_s == {"ALICE", "BOB"})
check("store=NY1 → applied echoed", res_s["applied"]["stores"] == ["ny1"])

res_r = router._exec_mtd(fc4, "o", OPEN, reps=["ALICE"])
loc_r = {r["store"] for r in res_r["by_location"]["rows"]}
emp_r = {r["employee"] for r in res_r["by_employee"]["rows"]}
check("rep=ALICE → by_employee only ALICE", emp_r == {"ALICE"})
check("rep=ALICE → by_location both stores ALICE sold at (NY1 + CHI1)", loc_r == {"NY1", "CHI1"})
# ALICE at NY1 = 1 activation (Port), ALICE at CHI1 = 1 (New) → total_activation across her locations = 2
alice_total = res_r["by_employee"]["total"]["total_activation"]
check("rep=ALICE total_activation across her stores == 2", alice_total == 2)

print(f"\n{'PASS' if FAIL == 0 else 'FAIL'} — {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
