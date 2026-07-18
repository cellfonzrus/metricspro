"""Proof for agent/commission/sales-capture-fix — luxelink '957 Pennsylvania Ave' July-1 undercount.

ROOT CAUSE: `_sales_rows_union` (the Sales-Report / Exec-MTD / Daily-Targets display source) merged the
open-month feed vs raw_sales at (day x store) CELL grain, WINNER-TAKE-ALL: on a store-day the feed leads,
every raw_sales transaction the feed lacked for that cell was DROPPED (documented "narrow completeness
edge" in _fetch_actuals). luxelink's hourly feed is chronically incomplete, so real raw_sales transactions
on a feed-led store-day were silently hidden. FIX: a store-scoped, id-normalized COMPLETENESS backfill —
union back any raw_sales transaction the merged set lacks, deduped on (store-cell, trans_id).

Gate-1 REWORK (2026-07-18) proven here:
  M1 store-scoped dedup key  → feed '1624'@A must NOT suppress raw-only '1624'@B (section 5)
  M2 raw-only source gate    → a CLOSED month must NOT resurrect a feed-only trans_id (section 6)
  m2 tid normalization       → '1624' / '1624.0' / '01624' dedup as one transaction (section 7)
  n1 sales-only count        → a voided raw-only line is not counted as "recovered" (section 8)

Pure unit tests over the REAL router functions AND a differential vs the GENUINE origin/main (ee7b657)
`_sales_rows_union` vendored via `git show`. NO live DB.

Run:  cd backend && python3 scratchpad/sales_capture_completeness_proof.py
"""
import sys, os, subprocess, random, textwrap
from collections import Counter

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
    blob = subprocess.check_output(
        ["git", "show", "ee7b657:backend/app/modules/commcalc/router.py"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ).decode("utf-8")
    lines = blob.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("def _sales_rows_union("))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("def _sales_rows_union_txn("))
    body = "\n".join(lines[start:end])
    ns = {
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
         dept="Phones", pdesc="Device", ext=100.0, period=OPEN, n=0, voided="", tt=""):
    return {"trans_id": str(tid), "trans_date": day, "store": store, "salesperson": rep,
            "category": cat, "contract_type": ct, "department": dept, "product_desc": pdesc,
            "ext_price": ext, "gp": 30.0, "voided": voided, "trans_type": tt, "user_login": rep,
            "_src": src, "org_id": "o", "period": period}


def tids_of(rows):
    return sorted({str(r.get("trans_id")).strip() for r in rows if str(r.get("trans_id") or "").strip()})


def keyset(rows):
    return {(router._cell_store_key(r.get("store")), router._norm_sale_tid(r.get("trans_id")))
            for r in rows if router._norm_sale_tid(r.get("trans_id"))}


# ══ (1) THE REPRO: feed has SOME of 957 Pennsylvania Ave July-1; raw_sales has the full set ═══════
print("(1) REPRO — 957 Pennsylvania Ave, July 1: feed partial, raw_sales complete (1624/1641/1721)")
feed = [line("1500", "FEED"), line("1550", "FEED")]
raw = [
    line("1500", "RAW"), line("1550", "RAW"),
    line("1624", "RAW", cat="Tablets", dept="Tablets", pdesc="Tablet", n=0),
    line("1624", "RAW", cat="Phones", dept="Phones", pdesc="Phone", n=1),
    line("1641", "RAW"),
    line("1721", "RAW", cat="Phones", dept="Phones", pdesc="Phone", n=0),
    line("1721", "RAW", cat="Tablets", dept="Tablets", pdesc="Tablet", n=1),
]
fc = FakeClient({"daily_sales_feed": feed, "raw_sales": raw})
old_rows, _ = origin_union(fc, "o", OPEN)
new_rows, new_meta = router._sales_rows_union(fc, "o", OPEN)
check("open month → feed is primary", new_meta["primary"] == "daily_sales_feed")
check("ORIGIN drops 1624/1641/1721 (the bug)", set(tids_of(old_rows)) == {"1500", "1550"})
check("FIXED union now includes 1624/1641/1721", {"1624", "1641", "1721"}.issubset(set(tids_of(new_rows))))
check("FIXED keeps the feed's own 1500/1550 (feed leads its cell)",
      all(r["_src"] == "FEED" for r in new_rows if str(r["trans_id"]) in ("1500", "1550")))
check("completeness_rows == 5 (1624x2, 1641x1, 1721x2)", new_meta["completeness_rows"] == 5)
check("no double count — 1500/1550 present ONCE each",
      len([r for r in new_rows if str(r["trans_id"]) == "1500"]) == 1 and
      len([r for r in new_rows if str(r["trans_id"]) == "1550"]) == 1)
acfg = router._accessory_config(fc, "o")
prem_old = len(router._sales_cell_agg(old_rows, acfg).get((STORE, "Jane Rep", D1), {}).get("_prem", []))
prem_new = len(router._sales_cell_agg(new_rows, acfg).get((STORE, "Jane Rep", D1), {}).get("_prem", []))
print(f"      activations (distinct-txn): origin={prem_old}  fixed={prem_new}")
check("origin Sales Report counted only 2 activations", prem_old == 2)
check("FIXED Sales Report counts all 5 activations", prem_new == 5)

# ══ (2) NO-REGRESSION: healthy feed-only / fully-promoted (raw ⊆ feed) → BYTE-IDENTICAL ══════════
print("(2) NO-REGRESSION — raw_sales ⊆ feed (fully-promoted) byte-identical")
feed2 = [line(str(1000 + i), "FEED", store=f"S{i%4}") for i in range(40)]
raw2 = [line(str(1000 + i), "RAW", store=f"S{i%4}") for i in range(0, 40, 2)]
fc2 = FakeClient({"daily_sales_feed": feed2, "raw_sales": raw2})
o2, _ = origin_union(fc2, "o", OPEN)
n2, m2 = router._sales_rows_union(fc2, "o", OPEN)
check("raw ⊆ feed → completeness_rows == 0", m2["completeness_rows"] == 0)
check("feed-only state → byte-identical to origin", o2 == n2)
print("(2b) NO-REGRESSION — raw_sales EMPTY (Boost open-month, no upload) byte-identical")
fc2b = FakeClient({"daily_sales_feed": feed2, "raw_sales": []})
o2b, _ = origin_union(fc2b, "o", OPEN)
n2b, m2b = router._sales_rows_union(fc2b, "o", OPEN)
check("empty raw_sales → completeness_rows == 0", m2b["completeness_rows"] == 0)
check("empty raw_sales → byte-identical to origin", o2b == n2b)

# ══ (3) CLOSED month (raw_sales leads; feed ⊆ raw) → byte-identical ═══════════════════════════════
print("(3) CLOSED month — raw_sales leads; feed ⊆ raw → byte-identical")
craw = [line(str(2000 + i), "RAW", store=f"S{i%3}", day=f"{CLOSED}-01", period=CLOSED) for i in range(30)]
cfeed = [line(str(2000 + i), "FEED", store=f"S{i%3}", day=f"{CLOSED}-01", period=CLOSED) for i in range(0, 30, 3)]
fc3 = FakeClient({"daily_sales_feed": cfeed, "raw_sales": craw})
o3, _ = origin_union(fc3, "o", CLOSED)
n3, m3 = router._sales_rows_union(fc3, "o", CLOSED)
check("closed month → raw_sales is primary", m3["primary"] == "raw_sales")
check("feed ⊆ raw (closed) → completeness_rows == 0", m3["completeness_rows"] == 0)
check("closed-month feed-subset → byte-identical to origin", o3 == n3)

# ══ (5) M1 — STORE-SCOPED KEY: feed '1624'@A must NOT suppress raw-only '1624'@B ══════════════════
# Store B is ALSO feed-led (feed sold '1700' there), so raw's '1624'@B is a raw-only txn in a feed-led
# cell — the exact case a bare tenant-wide tid dedup mishandles ('1624' already "seen" @ Store A).
print("(5) M1 — cross-store same trans_id: raw-only 1624 @ feed-led Store B survives (bare-tid would drop it)")
def _has(rows, store, tid):
    return any(r["store"] == store and str(r["trans_id"]) == tid for r in rows)
fcm = FakeClient({"daily_sales_feed": [line("1624", "FEED", store="Store A"),
                                       line("1700", "FEED", store="Store B")],   # Store B is feed-led
                  "raw_sales": [line("1624", "RAW", store="Store B")]})          # raw-ONLY 1624 @ B
om, _ = origin_union(fcm, "o", OPEN)
nm, mm = router._sales_rows_union(fcm, "o", OPEN)
check("ORIGIN winner-take-all dropped raw-only 1624 @ feed-led Store B", not _has(om, "Store B", "1624"))
check("FIXED surfaces 1624 @ Store B (store-scoped key)", _has(nm, "Store B", "1624"))
check("FIXED keeps feed's 1624 @ Store A too", _has(nm, "Store A", "1624"))
check("FIXED keeps feed's 1700 @ Store B too", _has(nm, "Store B", "1700"))
check("M1 completeness_rows == 1 (bare-tid dedup would wrongly give 0)", mm["completeness_rows"] == 1)
fcm2 = FakeClient({"daily_sales_feed": [line("1624", "FEED", store="Store A")],
                   "raw_sales": [line("1624", "RAW", store="Store A")]})
nm2, mm2 = router._sales_rows_union(fcm2, "o", OPEN)
check("same store + same tid in both → completeness_rows == 0 (no double count)", mm2["completeness_rows"] == 0)
check("same store + same tid → 1 row, the FEED copy", len(nm2) == 1 and nm2[0]["_src"] == "FEED")

# ══ (6) M2 — RAW-ONLY SOURCE GATE: a CLOSED-month feed-only tid stays DROPPED ═════════════════════
print("(6) M2 — closed month, feed-only 'GHOST' must NOT be resurrected into the raw-led view")
craw6 = [line(str(3000 + i), "RAW", store="S1", day=f"{CLOSED}-01", period=CLOSED) for i in range(5)]
cfeed6 = [line("3000", "FEED", store="S1", day=f"{CLOSED}-01", period=CLOSED),
          line("GHOST", "FEED", store="S1", day=f"{CLOSED}-01", period=CLOSED)]   # GHOST = feed-only
fc6 = FakeClient({"daily_sales_feed": cfeed6, "raw_sales": craw6})
n6, m6 = router._sales_rows_union(fc6, "o", CLOSED)
check("closed month → raw_sales primary, other == feed",
      m6["primary"] == "raw_sales" and m6["other"] == "daily_sales_feed")
check("M2: backfill did NOT run (completeness_rows == 0)", m6["completeness_rows"] == 0)
check("M2: feed-only GHOST NOT resurrected", not any(str(r["trans_id"]) == "GHOST" for r in n6))
fc6b = FakeClient({"daily_sales_feed": [line("GHOST", "FEED", store="S1", cat="")],  # blank category
                   "raw_sales": [line("5001", "RAW", store="S1")]})
n6b, m6b = router._sales_rows_union(fc6b, "o", OPEN)
check("open month, feed has NO category → raw leads, other == feed → backfill gated off",
      m6b["primary"] == "raw_sales" and m6b["completeness_rows"] == 0)

# ══ (7) m2 — TID NORMALIZATION: '1624' vs '1624.0' vs '01624' are ONE transaction ════════════════
print("(7) m2 — id format skew: feed '1624' and raw '1624.0' / '01624' must NOT double-count")
check("_norm_sale_tid collapses formats",
      router._norm_sale_tid("1624") == router._norm_sale_tid("1624.0")
      == router._norm_sale_tid("01624") == "1624")
fc7 = FakeClient({"daily_sales_feed": [line("1624", "FEED", store="S1")],
                  "raw_sales": [line("1624.0", "RAW", store="S1"), line("01624", "RAW", store="S1"),
                                line("1799", "RAW", store="S1")]})   # 1799 genuinely raw-only
n7, m7 = router._sales_rows_union(fc7, "o", OPEN)
check("format variants of 1624 deduped → only the raw-only 1799 recovered", m7["completeness_rows"] == 1)
check("only ONE 1624-family row shown (the feed copy)",
      len([r for r in n7 if router._norm_sale_tid(r["trans_id"]) == "1624"]) == 1)

# ══ (8) n1 — a VOIDED raw-only line is not counted as a 'recovered sale' ══════════════════════════
print("(8) n1 — voided / Return raw-only lines excluded from the recovered-sales count")
fc8 = FakeClient({"daily_sales_feed": [line("1500", "FEED", store="S1")],
                  "raw_sales": [line("1624", "RAW", store="S1"),                # real sale
                                line("1641", "RAW", store="S1", voided="Yes"),  # voided
                                line("1721", "RAW", store="S1", tt="Return")]})  # Return
n8, m8 = router._sales_rows_union(fc8, "o", OPEN)
check("all 3 raw-only rows are in merged", {"1624", "1641", "1721"}.issubset(set(tids_of(n8))))
check("completeness_rows counts only the 1 real sale (voided + Return excluded)", m8["completeness_rows"] == 1)

# ══ (4) DIFFERENTIAL FUZZ vs genuine origin — 400 random scenarios, store-scoped invariants ═══════
print("(4) DIFFERENTIAL FUZZ — 400 random feed/raw scenarios vs genuine origin _sales_rows_union")
random.seed(1418)
inv_A = inv_C = inv_D = True
for _ in range(400):
    stores = [f"S{i}" for i in range(random.randint(1, 5))]
    days = [f"{OPEN}-{d:02d}" for d in random.sample(range(1, 12), random.randint(1, 3))]
    fdr, rwr = [], []
    tid = 5000
    for st in stores:
        for d in days:
            nf, nr = random.randint(0, 6), random.randint(0, 6)
            base = tid
            for i in range(nf):
                fdr.append(line(str(base + i), "FEED", store=st, day=d))
            for i in range(nr):
                rwr.append(line(str(base + i), "RAW", store=st, day=d))
            tid += 20
    fcf = FakeClient({"daily_sales_feed": fdr, "raw_sales": rwr})
    ofz, _ = origin_union(fcf, "o", OPEN)
    nfz, mfz = router._sales_rows_union(fcf, "o", OPEN)
    feed_ks, raw_ks, fixed_ks = keyset(fdr), keyset(rwr), keyset(nfz)
    primary = mfz["primary"]
    # A: raw ⊆ feed (store,tid) AND feed leads → byte-identical to origin
    if primary == "daily_sales_feed" and raw_ks.issubset(feed_ks) and ofz != nfz:
        inv_A = False
    # C: every AUTHORITATIVE raw (store,tid) is present in the fixed set (never lost)
    if not raw_ks.issubset(fixed_ks):
        inv_C = False
    # D: no (store,tid) shows more rows than its richer single source provided (no double count)
    fc_cnt = Counter((router._cell_store_key(r["store"]), router._norm_sale_tid(r["trans_id"])) for r in nfz)
    fd_cnt = Counter((router._cell_store_key(r["store"]), router._norm_sale_tid(r["trans_id"])) for r in fdr)
    rw_cnt = Counter((router._cell_store_key(r["store"]), router._norm_sale_tid(r["trans_id"])) for r in rwr)
    for k, c in fc_cnt.items():
        if c > max(fd_cnt.get(k, 0), rw_cnt.get(k, 0)):
            inv_D = False
            break
check("A: raw ⊆ feed + feed-led → byte-identical to origin in ALL 400 runs", inv_A)
check("C: no authoritative raw (store,tid) ever lost in ALL 400 runs", inv_C)
check("D: no (store,tid) double-counted across sources in ALL 400 runs", inv_D)

print(f"\n{PASS}/{PASS + FAIL} passed" + ("" if not FAIL else f"  ({FAIL} FAILED)"))
sys.exit(1 if FAIL else 0)
