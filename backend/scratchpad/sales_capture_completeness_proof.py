"""Proof for agent/commission/sales-capture-fix — luxelink '957 Pennsylvania Ave' July-1 undercount.

ROOT CAUSE: `_sales_rows_union` (the Sales-Report / Exec-MTD / Daily-Targets display source) merged the
open-month feed vs raw_sales at (day x store) CELL grain, WINNER-TAKE-ALL: on a store-day the feed leads,
every raw_sales transaction the feed lacked for that cell was DROPPED (documented "narrow completeness
edge" in _fetch_actuals). luxelink's hourly feed is chronically incomplete, so real raw_sales transactions
on a feed-led store-day were silently hidden. FIX: a dedup-by-trans_id COMPLETENESS backfill — union back
any raw_sales transaction the merged set lacks, never double-counting one present in both sources.

Pure unit tests over the REAL router functions AND a differential against the GENUINE origin/main
(ee7b657) `_sales_rows_union` vendored via `git show`. NO live DB.

Run:  cd backend && python3 scratchpad/sales_capture_completeness_proof.py
"""
import sys, os, subprocess, random, textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date as _date  # noqa: E402
from app.modules.commcalc import router  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


# ── Fake Supabase client (chainable, in-memory) — mirrors exec_mtd_union_grain_proof ─────────────
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data; self.count = count


class _Query:
    def __init__(self, client, table):
        self.c = client; self.table = table; self.count_mode = False

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


# ── Vendor the GENUINE origin/main (ee7b657) _sales_rows_union for a byte-differential ───────────
def _vendor_origin_union():
    """Extract the pre-fix `_sales_rows_union` source from ee7b657 and exec it in a namespace backed by
    the (unchanged) helper functions of the patched router, giving a faithful ORIGIN function to diff."""
    blob = subprocess.check_output(
        ["git", "show", "ee7b657:backend/app/modules/commcalc/router.py"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ).decode("utf-8")
    lines = blob.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("def _sales_rows_union("))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("def _sales_rows_union_txn("))
    body = "\n".join(lines[start:end])
    ns = {  # helpers the origin body references — all UNCHANGED by this fix
        "_open_month_source": router._open_month_source,
        "_merge_cells_richer": router._merge_cells_richer,
        "_cell_store_key": router._cell_store_key,
        "_pvariants": router._pvariants,
        "_SALES_DISPLAY_COLS": router._SALES_DISPLAY_COLS,
        "print": lambda *a, **k: None,
    }
    exec(compile(textwrap.dedent(body), "<origin_union>", "exec"), ns)
    return ns["_sales_rows_union"]


origin_union = _vendor_origin_union()

_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
_py, _pm = (_T.year - 1, 12) if _T.month == 1 else (_T.year, _T.month - 1)
CLOSED = f"{_py}-{_pm:02d}"
D1 = f"{OPEN}-01"
STORE = "957 Pennsylvania Ave"


def line(tid, src, store=STORE, day=D1, rep="Jane Rep", ct="Activation", cat="Phones",
         dept="Phones", pdesc="Device", ext=100.0, period=OPEN, n=0):
    return {"trans_id": str(tid), "trans_date": day, "store": store, "salesperson": rep,
            "category": cat, "contract_type": ct, "department": dept, "product_desc": pdesc,
            "ext_price": ext, "gp": 30.0, "voided": "", "trans_type": "", "user_login": rep,
            "_src": src, "org_id": "o", "period": period}


def tids_of(rows):
    return sorted({str(r.get("trans_id")).strip() for r in rows if str(r.get("trans_id") or "").strip()})


# ══ (1) THE REPRO: feed has SOME of 957 Pennsylvania Ave July-1; raw_sales has the full set ═══════
print("(1) REPRO — 957 Pennsylvania Ave, July 1: feed partial, raw_sales complete (1624/1641/1721)")
# Feed captured only two earlier tickets for this store-day (hourly feed missed the rest):
feed = [line("1500", "FEED"), line("1550", "FEED")]
# raw_sales (authoritative monthly upload) has ALL of them, including the owner's three:
raw = [
    line("1500", "RAW"), line("1550", "RAW"),
    line("1624", "RAW", cat="Tablets", dept="Tablets", pdesc="Tablet", n=0),   # 1624 line 1: tablet
    line("1624", "RAW", cat="Phones", dept="Phones", pdesc="Phone", n=1),      # 1624 line 2: phone
    line("1641", "RAW"),                                                        # 1641: 1 sale
    line("1721", "RAW", cat="Phones", dept="Phones", pdesc="Phone", n=0),       # 1721 line 1: phone
    line("1721", "RAW", cat="Tablets", dept="Tablets", pdesc="Tablet", n=1),    # 1721 line 2: tablet
]
fc = FakeClient({"daily_sales_feed": feed, "raw_sales": raw})

old_rows, _ = origin_union(fc, "o", OPEN)
new_rows, new_meta = router._sales_rows_union(fc, "o", OPEN)

check("open month → feed is primary", new_meta["primary"] == "daily_sales_feed")
check("ORIGIN drops 1624/1641/1721 (the bug)",
      set(tids_of(old_rows)) == {"1500", "1550"})
check("FIXED union now includes 1624/1641/1721",
      {"1624", "1641", "1721"}.issubset(set(tids_of(new_rows))))
check("FIXED union keeps the feed's own copies of 1500/1550 (feed still leads its cell)",
      all(r["_src"] == "FEED" for r in new_rows if str(r["trans_id"]) in ("1500", "1550")))
check("backfilled rows come from raw_sales (5 line items: 1624x2, 1641x1, 1721x2)",
      new_meta["completeness_rows"] == 5)
check("no double count — 1500/1550 present ONCE each (feed copy only)",
      len([r for r in new_rows if str(r["trans_id"]) == "1500"]) == 1 and
      len([r for r in new_rows if str(r["trans_id"]) == "1550"]) == 1)

# ── classification: these now count as ACTIVATIONS in the shared Sales-Report aggregation ────────
acfg = router._accessory_config(fc, "o")  # empty tables → default classifier (house/Boost)
agg_old = router._sales_cell_agg(old_rows, acfg)
agg_new = router._sales_cell_agg(new_rows, acfg)
key = (STORE, "Jane Rep", D1)
prem_old = len(agg_old[key]["_prem"]) if key in agg_old else 0
prem_new = len(agg_new[key]["_prem"]) if key in agg_new else 0
txn_old = len(agg_old[key]["_txn"]) if key in agg_old else 0
txn_new = len(agg_new[key]["_txn"]) if key in agg_new else 0
print(f"      activations (distinct-txn): origin={prem_old}  fixed={prem_new}   txns: {txn_old} -> {txn_new}")
check("origin Sales Report counted only 2 activations for the cell", prem_old == 2)
check("FIXED Sales Report counts all 5 activations (1500,1550,1624,1641,1721)", prem_new == 5)
check("FIXED transaction count also restored to 5", txn_new == 5)

# ══ (2) NO-REGRESSION: healthy feed-only / fully-promoted (raw_sales ⊆ feed) → BYTE-IDENTICAL ════
print("(2) NO-REGRESSION — feed-only / fully-promoted state (raw_sales subset of feed) byte-identical")
feed2 = [line(str(1000 + i), "FEED", store=f"S{i%4}") for i in range(40)]
raw2 = [line(str(1000 + i), "RAW", store=f"S{i%4}") for i in range(0, 40, 2)]  # a promoted subset
fc2 = FakeClient({"daily_sales_feed": feed2, "raw_sales": raw2})
o2, _ = origin_union(fc2, "o", OPEN)
n2, m2 = router._sales_rows_union(fc2, "o", OPEN)
check("raw_sales ⊆ feed → completeness_rows == 0 (nothing added)", m2["completeness_rows"] == 0)
check("feed-only state → merged rows byte-identical to origin", o2 == n2)

print("(2b) NO-REGRESSION — raw_sales EMPTY (Boost open-month, no monthly upload) byte-identical")
fc2b = FakeClient({"daily_sales_feed": feed2, "raw_sales": []})
o2b, _ = origin_union(fc2b, "o", OPEN)
n2b, m2b = router._sales_rows_union(fc2b, "o", OPEN)
check("empty raw_sales → completeness_rows == 0", m2b["completeness_rows"] == 0)
check("empty raw_sales → byte-identical to origin", o2b == n2b)

# ══ (3) CLOSED month (raw_sales leads) — feed ⊆ raw_sales → byte-identical ════════════════════════
print("(3) CLOSED month — raw_sales leads; feed ⊆ raw_sales → byte-identical")
craw = [line(str(2000 + i), "RAW", store=f"S{i%3}", day=f"{CLOSED}-01", period=CLOSED) for i in range(30)]
cfeed = [line(str(2000 + i), "FEED", store=f"S{i%3}", day=f"{CLOSED}-01", period=CLOSED) for i in range(0, 30, 3)]
fc3 = FakeClient({"daily_sales_feed": cfeed, "raw_sales": craw})
o3, _ = origin_union(fc3, "o", CLOSED)
n3, m3 = router._sales_rows_union(fc3, "o", CLOSED)
check("closed month → raw_sales is primary", m3["primary"] == "raw_sales")
check("feed ⊆ raw_sales (closed) → completeness_rows == 0", m3["completeness_rows"] == 0)
check("closed-month feed-subset → byte-identical to origin", o3 == n3)

# ══ (4) DIFFERENTIAL FUZZ vs genuine origin/main — 400 random scenarios ═══════════════════════════
print("(4) DIFFERENTIAL FUZZ — 400 random feed/raw scenarios vs genuine origin _sales_rows_union")
random.seed(1418)
identical_when_subset, superset_ok, never_lost, no_dupes = True, True, True, True
for _ in range(400):
    stores = [f"S{i}" for i in range(random.randint(1, 5))]
    days = [f"{OPEN}-{d:02d}" for d in random.sample(range(1, 12), random.randint(1, 3))]
    fdr, rwr = [], []
    tid = 5000
    for st in stores:
        for d in days:
            nf = random.randint(0, 6)
            nr = random.randint(0, 6)
            base = tid
            for i in range(nf):
                fdr.append(line(str(base + i), "FEED", store=st, day=d))
            # raw_sales overlaps some feed tids + adds some unique ones
            for i in range(nr):
                rwr.append(line(str(base + i), "RAW", store=st, day=d))
            tid += 20
    fcf = FakeClient({"daily_sales_feed": fdr, "raw_sales": rwr})
    ofz, _ = origin_union(fcf, "o", OPEN)
    nfz, mfz = router._sales_rows_union(fcf, "o", OPEN)
    o_t, n_t = set(tids_of(ofz)), set(tids_of(nfz))
    feed_t = {str(r["trans_id"]) for r in fdr}
    raw_t = {str(r["trans_id"]) for r in rwr}
    # invariant A: when raw ⊆ feed (by trans_id), output is byte-identical to origin
    if raw_t.issubset(feed_t) and ofz != nfz:
        identical_when_subset = False
    # invariant B: fixed output trans_ids = origin ∪ (all authoritative + feed) = feed ∪ raw
    if n_t != (feed_t | raw_t):
        superset_ok = False
    # invariant C: no authoritative transaction is ever lost
    if not (feed_t | raw_t).issubset(n_t):
        never_lost = False
    # invariant D: every trans_id appears exactly once per source-copy — no NEW duplicates vs origin
    #   (a trans_id shared by both sources must appear as the feed copy only)
    for t in (feed_t & raw_t):
        copies = [r for r in nfz if str(r["trans_id"]) == t]
        if any(r["_src"] != "FEED" for r in copies) or len(copies) != len([r for r in fdr if str(r["trans_id"]) == t]):
            no_dupes = False
check("A: raw ⊆ feed → byte-identical to origin in ALL 400 runs", identical_when_subset)
check("B: fixed trans_id set == feed ∪ raw in ALL 400 runs", superset_ok)
check("C: no authoritative transaction ever lost in ALL 400 runs", never_lost)
check("D: shared trans_ids kept as the FEED copy only (no double count) in ALL 400 runs", no_dupes)

print(f"\n{PASS}/{PASS + FAIL} passed" + ("" if not FAIL else f"  ({FAIL} FAILED)"))
sys.exit(1 if FAIL else 0)
