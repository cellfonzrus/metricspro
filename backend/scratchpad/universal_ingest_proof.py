"""Pure-logic proof harness for the universal-ingest-contract package.

Imports the REAL router functions (_sales_rows_union, _pvariants, _is_open_month, _open_month_source,
_write_upload_trace, upload_file wrapper's trace derivation) over an in-memory FakeClient — no network.
Proves:
  A. The union resolver: luxelink shape (feed days 1-8 + hand-uploaded raw_sales 1-13) → union shows 1-13
     (feed-wins-per-day, raw_sales fills 9-13); plus feed-empty, raw-empty, closed-month, and no-mask edges.
  B. The period-spelling matrix: _pvariants covers both spellings so feed ('July 2026') matches a '2026-07'
     request and vice-versa, and the resolver reads across the spelling boundary.
  C. The trace record shape: _write_upload_trace derives status/target/periods/date_counts/rows from a
     result dict, degrades gracefully when the table insert raises, and captures errors.
Run: python3 universal_ingest_proof.py   (from the package's backend venv)
"""
import sys, datetime
sys.path.insert(0, "/workspaces/mp-wt-comm-ingest/backend")
import app.modules.commcalc.router as R

PASS = 0
FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}  {detail}")

LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
HOUSE = "00000000-0000-0000-0000-000000000001"

# Today is pinned via _is_open_month using date.today(); build periods relative to the real current month
# so 'open month' is genuinely open regardless of when this runs.
_t = R._date.today()
OPEN_MONTH_NUM = f"{_t.year}-{_t.month:02d}"
OPEN_MONTH_NAME = f"{R._calendar.month_name[_t.month]} {_t.year}"
# a definitely-closed prior month
_pm = (_t.month - 1) or 12
_py = _t.year if _t.month > 1 else _t.year - 1
CLOSED_NUM = f"{_py}-{_pm:02d}"
CLOSED_NAME = f"{R._calendar.month_name[_pm]} {_py}"


class FakeTable:
    def __init__(self, store, schema, table):
        self.store, self.table = store, table
        self._rows = list(store.get(table, []))
        self._count_mode = None
    # select supports both data reads and count='exact'
    def select(self, cols, count=None):
        self._count_mode = count
        return self
    def eq(self, k, v):
        self._rows = [r for r in self._rows if r.get(k) == v]; return self
    def neq(self, k, v):
        self._rows = [r for r in self._rows if r.get(k) != v]; return self
    def in_(self, k, vals):
        vs = set(vals); self._rows = [r for r in self._rows if r.get(k) in vs]; return self
    def gte(self, k, v):
        self._rows = [r for r in self._rows if str(r.get(k) or "") >= v]; return self
    def lt(self, k, v):
        self._rows = [r for r in self._rows if str(r.get(k) or "") < v]; return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    def range(self, a, b): return self
    def insert(self, row):
        self.store.setdefault(self.table, [])
        if isinstance(row, list): self.store[self.table].extend(row)
        else: self.store[self.table].append(row)
        self._pending = row
        return self
    def execute(self):
        class Res: pass
        res = Res()
        if self._count_mode == 'exact':
            res.count = len(self._rows); res.data = self._rows[:1]
        else:
            res.data = self._rows
        return res


class FakeSchema:
    def __init__(self, store): self.store = store
    def table(self, t): return FakeTable(self.store, "x", t)


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, s): return FakeSchema(self.store)


def feed_row(day, cat="Regular", ext=100.0):
    return {"org_id": LUX, "period": OPEN_MONTH_NAME, "trans_id": f"F{day}", "trans_date": f"{OPEN_MONTH_NUM}-{day:02d}",
            "store": "3 Palisade Ave", "salesperson": "REP A", "department": "", "category": cat,
            "product_desc": "x", "contract_type": "New Activation", "ext_price": ext, "gp": 10.0,
            "voided": "", "trans_type": ""}

def raw_row(day, period=None, cat="Regular", ext=100.0):
    return {"org_id": LUX, "period": period or OPEN_MONTH_NAME, "trans_id": f"R{day}", "trans_date": f"{OPEN_MONTH_NUM}-{day:02d}",
            "store": "3 Palisade Ave", "salesperson": "REP A", "department": "", "category": cat,
            "product_desc": "x", "contract_type": "New Activation", "ext_price": ext, "gp": 10.0,
            "voided": "", "trans_type": ""}


print("\n=== A. UNION RESOLVER ===")

# A1: THE luxelink incident shape — feed days 1-8, raw_sales days 1-13. Open month.
store = {"daily_sales_feed": [feed_row(d) for d in range(1, 9)],
         "raw_sales": [raw_row(d) for d in range(1, 14)]}
rows, meta = R._sales_rows_union(FakeClient(store), LUX, OPEN_MONTH_NUM)
days = sorted({r["trans_date"][8:10] for r in rows})
check("A1 luxelink shape: union shows days 1-13", days == [f"{d:02d}" for d in range(1, 14)], f"got {days}")
check("A1 primary=feed (feed-wins)", meta["primary"] == "daily_sales_feed", meta)
check("A1 days 1-8 come from the FEED (F* ids)",
      all(r["trans_id"].startswith("F") for r in rows if int(r["trans_date"][8:10]) <= 8))
check("A1 days 9-13 filled from raw_sales (R* ids)",
      all(r["trans_id"].startswith("R") for r in rows if int(r["trans_date"][8:10]) >= 9))
check("A1 filled_days == 9..13", meta["filled_days"] == [f"{OPEN_MONTH_NUM}-{d:02d}" for d in range(9, 14)], meta["filled_days"])
check("A1 meta counts: feed 8, raw 13, shown 13, filled 5",
      (meta["feed_rows"], meta["raw_rows"], meta["shown_rows"], meta["filled_rows"]) == (8, 13, 13, 5), meta)

# A2: no double-count on overlap days — day 1 exists in BOTH; only ONE row for it, from the feed.
day1 = [r for r in rows if r["trans_date"].endswith("-01")]
check("A2 overlap day appears once, from feed", len(day1) == 1 and day1[0]["trans_id"] == "F1", day1)

# A3: feed empty → entire raw_sales shows (no masking of a hand-uploaded month with an empty feed).
store = {"daily_sales_feed": [], "raw_sales": [raw_row(d) for d in range(1, 14)]}
rows, meta = R._sales_rows_union(FakeClient(store), LUX, OPEN_MONTH_NUM)
# _open_month_source flips primary to raw_sales when feed has 0 category rows and raw>0
check("A3 feed empty → all 13 raw days shown", sorted({r['trans_date'][8:10] for r in rows}) == [f"{d:02d}" for d in range(1, 14)], f"{len(rows)}")

# A4: raw empty, feed 1-8 → shows 1-8 (Boost/house steady state — feed only, never 500).
store = {"daily_sales_feed": [feed_row(d) for d in range(1, 9)], "raw_sales": []}
rows, meta = R._sales_rows_union(FakeClient(store), LUX, OPEN_MONTH_NUM)
check("A4 raw empty → feed's 8 days shown", len(rows) == 8 and meta["primary"] == "daily_sales_feed")

# A5: closed month → raw_sales leads, feed fills a gap day.
store = {"daily_sales_feed": [feed_row(20)], "raw_sales": [raw_row(d, period=CLOSED_NAME) for d in range(1, 6)]}
# feed row is for OPEN month, won't match CLOSED period → primary=raw, feed contributes nothing for CLOSED
rows, meta = R._sales_rows_union(FakeClient(store), LUX, CLOSED_NUM)
check("A5 closed month primary=raw_sales", meta["primary"] == "raw_sales", meta)
check("A5 closed month shows raw's 5 days", len(rows) == 5, f"{len(rows)}")

# A6: read never raises even if a table is absent from the store (KeyError path → [] via try/except).
try:
    rows, meta = R._sales_rows_union(FakeClient({}), LUX, OPEN_MONTH_NUM)
    check("A6 empty store → [] rows, no raise", rows == [] and meta["shown_rows"] == 0)
except Exception as e:
    check("A6 empty store → [] rows, no raise", False, str(e))


print("\n=== B. PERIOD-SPELLING MATRIX ===")
# feed stores 'July 2026' style; the page requests '2026-07' style. _pvariants must bridge both.
pv_num = set(R._pvariants(OPEN_MONTH_NUM))
pv_name = set(R._pvariants(OPEN_MONTH_NAME))
check("B1 _pvariants('2026-07' form) contains the 'Month YYYY' form", OPEN_MONTH_NAME in pv_num, pv_num)
check("B2 _pvariants('Month YYYY' form) contains the '2026-07' form", OPEN_MONTH_NUM in pv_name, pv_name)
check("B3 both spellings expand to the SAME set", pv_num == pv_name, (pv_num, pv_name))
check("B4 malformed period passes through unchanged", R._pvariants("not-a-month") == ["not-a-month"])
# resolver reads across the boundary: request '2026-07', feed rows stored as 'July 2026'
store = {"daily_sales_feed": [feed_row(d) for d in range(1, 4)], "raw_sales": []}
rows, _ = R._sales_rows_union(FakeClient(store), LUX, OPEN_MONTH_NUM)   # numeric request
check("B5 numeric request matches month-name feed rows", len(rows) == 3, f"{len(rows)}")


print("\n=== C. TRACE RECORD SHAPE ===")
# C1: a clean sales save → status ok, rows_in/saved + per-period/per-day counts captured.
store = {}
res_ok = {"saved": 4533, "file_type": "sales", "period": OPEN_MONTH_NAME, "shrink": [],
          "_trace": {"rows_in": 4533, "target_table": "raw_sales",
                     "periods": {OPEN_MONTH_NAME: 4533},
                     "date_counts": {f"{OPEN_MONTH_NUM}-{d:02d}": 349 for d in range(1, 14)}}}
_orig_sb = R.sb
R.sb = lambda: FakeClient(store)
try:
    R._write_upload_trace(LUX, source="manual", filename="Sales.xlsx", upload_type="sales",
                          period=OPEN_MONTH_NAME, result=res_ok, duration_ms=1200, error=None)
    rec = store.get("upload_trace", [{}])[-1]
    check("C1 status ok", rec.get("status") == "ok", rec)
    check("C1 org stamped = luxelink", rec.get("org_id") == LUX)
    check("C1 rows_in/saved captured", (rec.get("rows_in"), rec.get("rows_saved")) == (4533, 4533), rec)
    check("C1 target_table=raw_sales", rec.get("target_table") == "raw_sales")
    check("C1 per-day counts captured (13 days)", len(rec.get("date_counts") or {}) == 13)
    check("C1 duration captured", rec.get("duration_ms") == 1200)

    # C2: price-guard FULL refusal → status skipped, guard carried.
    store["upload_trace"] = []
    res_guard = {"saved": 0, "file_type": "daily_sales", "skipped": "price_guard",
                 "shrink": [{"key": "price-guard", "reason": "refused: fewer priced rows"}],
                 "_trace": {"rows_in": 0, "target_table": "daily_sales_feed", "periods": {}, "date_counts": {}}}
    R._write_upload_trace(LUX, source="email_sweep", filename="Sales.csv", upload_type="daily_sales",
                          period="", result=res_guard, duration_ms=50, error=None)
    rec = store["upload_trace"][-1]
    check("C2 status skipped", rec.get("status") == "skipped", rec)
    check("C2 skipped reason captured", rec.get("skipped") == "price_guard")
    check("C2 source=email_sweep", rec.get("source") == "email_sweep")
    check("C2 guard detail captured", bool(rec.get("guard")))

    # C3: partial price-guard → status partial.
    store["upload_trace"] = []
    R._write_upload_trace(LUX, source="email_sweep", upload_type="daily_sales", period="",
                          result={"saved": 120, "skipped": "price_guard_partial", "guarded_dates": ["x"]})
    check("C3 partial status", store["upload_trace"][-1].get("status") == "partial")

    # C4: exception path → status error, message captured.
    store["upload_trace"] = []
    R._write_upload_trace(LUX, source="manual", upload_type="sales", period=OPEN_MONTH_NAME,
                          result=None, duration_ms=10, error="500: Insert failed at row 0: boom")
    rec = store["upload_trace"][-1]
    check("C4 status error", rec.get("status") == "error")
    check("C4 error message captured", "Insert failed" in (rec.get("error") or ""))

    # C5: inventory honest-zero (skipped, success=False) → status skipped.
    store["upload_trace"] = []
    R._write_upload_trace(LUX, source="email_sweep", upload_type="inventory_aging", period="",
                          result={"success": False, "skipped": "inventory_no_stores",
                                  "note": "parsed 0 stores"})
    check("C5 inventory honest-zero → skipped", store["upload_trace"][-1].get("status") == "skipped")
finally:
    R.sb = _orig_sb

# C6: degrade gracefully — insert raises → no exception escapes.
class BoomClient:
    def schema(self, s): raise RuntimeError("mig 202 not run")
R.sb = lambda: BoomClient()
try:
    R._write_upload_trace(LUX, source="manual", upload_type="sales", period="x", result={"saved": 1})
    check("C6 trace insert failure is swallowed", True)
except Exception as e:
    check("C6 trace insert failure is swallowed", False, str(e))
finally:
    R.sb = _orig_sb


print(f"\n===== {PASS} PASS / {FAIL} FAIL =====")
sys.exit(1 if FAIL else 0)
