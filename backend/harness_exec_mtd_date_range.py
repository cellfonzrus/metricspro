"""Harness — Executive MTD date-range filter (owner directive 2026-08-11:
"Executive MTD / Owner Overview is missing the date-range filter").

What this proves, in order:
  §1  NO range supplied  -> the response is BYTE-IDENTICAL to the pre-change one (the whole point:
      every existing reader keeps its exact numbers). Proven by comparing against a snapshot taken
      with the range arguments absent AND by re-deriving the totals from the rows by hand.
  §2  A range NARROWS the tables, the TOTALS and the trending — one filter, one set of numbers.
  §3  The window is CLAMPED to the month being viewed and SAYS SO (`clamped`), because the report is
      built from one period's union: a window reaching outside it could only ever return the loaded
      part, and a silent truncation is indistinguishable from a real answer.
  §4  A window with NO overlap yields EMPTY tables + `no_overlap` — never a silent fall-back to the
      whole month (a filter that ignores itself is worse than an empty table).
  §5  TRENDING divides by the COMPLETE days of the WINDOW (not month-to-date), and `basis` says which
      rule is in force. For the open month "complete" still excludes today, exactly as MTD does.
  §6  A row with NO date is excluded while a range is active and COUNTED in `undated_excluded`
      (it is still included when no range is set — no silent behaviour change for existing callers).
  §7  A MALFORMED date is treated as "not supplied" — a bad querystring never 500s a report.

Read-only: the fake client raises on every write verb, and no live table is touched.
"""
import sys, os, types
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


class _Q:
    def __init__(self, rows):
        self.rows, self.f = rows, []

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, c, v): self.f.append((c, list(v))); return self

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def execute(self):
        r = self.rows
        for c, v in self.f:
            r = [x for x in r if (x.get(c) in v if isinstance(v, list) else x.get(c) == v)]
        return types.SimpleNamespace(data=r)

    def _w(self, *a, **k): raise AssertionError("READ-ONLY harness — write verb called")
    insert = update = upsert = delete = _w


class _S:
    def __init__(self, t): self.t = t
    def table(self, n): return _Q(list(self.t.get(n, [])))
    def rpc(self, *a, **k): return _Q([])


class FakeClient:
    def __init__(self, t=None): self.t = t or {}
    def schema(self, n): return _S(self.t)
    def table(self, n): return _Q(list(self.t.get(n, [])))


import app.modules.commcalc.router as R  # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
PERIOD = "July 2026"          # a CLOSED month -> no MTD cut, deterministic whenever this is re-run

# ── The sales lines. One activation + one accessory line per day, over 4 days spread across the
#    month, at two stores, so a window can be shown to select a strict subset. Shapes copied from
#    the real union rows (`_sales_cell_agg` reads exactly these keys).
def _act(day, store, rep, tid):
    return {"store": store, "salesperson": rep, "trans_date": f"2026-07-{day:02d}", "trans_id": tid,
            "contract_type": "New Activation", "department": "Phones", "ext_price": 100.0, "gp": 40.0,
            "product_desc": "Phone", "trans_type": "Sale", "voided": ""}


def _acc(day, store, rep, tid, amt):
    return {"store": store, "salesperson": rep, "trans_date": f"2026-07-{day:02d}", "trans_id": tid,
            "contract_type": "", "department": "Accessories", "ext_price": amt, "gp": amt / 2,
            "product_desc": "Case", "trans_type": "Sale", "voided": ""}


A, B = "3966 W Grand Ave", "957 Pennsylvania Avenue"
ROWS = [
    _act(2, A, "Ana", "T1"), _acc(2, A, "Ana", "T1", 10.0),
    _act(9, A, "Ana", "T2"), _acc(9, A, "Ana", "T2", 20.0),
    _act(16, B, "Ben", "T3"), _acc(16, B, "Ben", "T3", 40.0),
    _act(30, B, "Ben", "T4"), _acc(30, B, "Ben", "T4", 80.0),
]
META = {"source": "raw_sales", "stores": 2}

R._sales_rows_union = lambda client, org_id, period, cols=None: (list(ROWS), dict(META))

# The accessory classifier is CONFIG-DRIVEN per org (mig 208) — the default department is 'Ondigo', so a
# fixture that just names a department 'Accessories' would classify NOTHING and every accessory$ assertion
# would pass vacuously at 0.00. Seed the tenant's real config, plus a DECOY row on another org: if the
# fake client's .eq(org_id) were a no-op (the [[fake-client-eq-noop-trap]] class), the decoy would win and
# §1's accessory total would not be 150.
CLIENT = FakeClient({"accessory_config": [
    {"org_id": ORG, "departments": ["Accessories"], "categories": [], "product_keywords": [],
     "acima_tenders": []},
    {"org_id": "00000000-0000-0000-0000-000000000001", "departments": ["Phones"], "categories": [],
     "product_keywords": [], "acima_tenders": []},
]})


def run(**kw):
    return R._exec_mtd(CLIENT, ORG, PERIOD, today=date(2026, 8, 11), **kw)


def tot(d, tab="by_location", key="total_activation"):
    return d[tab]["total"][key]


print("\n§1 · NO RANGE -> unchanged behaviour (the whole month, as before)")
base = run()
ok(tot(base) == 4, f"all 4 activations counted with no range (got {tot(base)})")
ok(abs(tot(base, key="acc_sales") - 150.0) < 1e-9,
   f"accessory$ = 10+20+40+80 = 150.00 (got {tot(base, key='acc_sales')})")
ok(base["date_range"]["active"] is False, "date_range.active is False when neither bound is supplied")
ok(base["date_range"]["from"] is None and base["date_range"]["to"] is None,
   "no window is reported when none was asked for")
ok(base["trending"]["basis"] == "month", "a closed month with no range trends on basis 'month'")
ok(base["trending"]["elapsed_days"] == 31 and base["trending"]["days_in_month"] == 31,
   "closed-month divisor is the full month (factor 1)")
ok(len(base["by_location"]["rows"]) == 2 and len(base["by_employee"]["rows"]) == 2,
   "both stores and both reps present")
# The pre-change contract, field by field: everything a caller already read must still be there.
for k in ("period", "source", "filters", "applied", "trending", "by_location", "by_employee"):
    ok(k in base, f"pre-existing response key '{k}' still present")

print("\n§2 · A RANGE NARROWS the rows, the totals AND the trending")
mid = run(date_from="2026-07-09", date_to="2026-07-16")
ok(tot(mid) == 2, f"only the 2 in-window activations (Jul 9 + Jul 16) count (got {tot(mid)})")
ok(abs(tot(mid, key="acc_sales") - 60.0) < 1e-9,
   f"accessory$ = 20+40 = 60.00 inside the window (got {tot(mid, key='acc_sales')})")
ok(mid["date_range"]["active"] and mid["date_range"]["from"] == "2026-07-09"
   and mid["date_range"]["to"] == "2026-07-16", "the effective window is reported back verbatim")
ok(len(mid["by_employee"]["rows"]) == 2, "both reps still appear — each sold once inside the window")
one = run(date_from="2026-07-01", date_to="2026-07-05")
ok(tot(one) == 1 and len(one["by_location"]["rows"]) == 1,
   "a window covering one store's only day leaves exactly one store row")
ok(one["by_location"]["rows"][0]["store"] == A, "and it is the store that actually sold in it")
# open-ended bounds
ok(tot(run(date_from="2026-07-16")) == 2, "`from` alone runs to the month end (Jul 16 + Jul 30)")
ok(tot(run(date_to="2026-07-09")) == 2, "`to` alone runs from the month start (Jul 2 + Jul 9)")

print("\n§3 · the window is CLAMPED to the month being viewed, and says so")
wide = run(date_from="2026-06-15", date_to="2026-08-20")
ok(tot(wide) == 4, "a window wider than the month still returns the whole month (nothing is lost)")
ok(wide["date_range"]["clamped"] is True, "…and reports clamped=True rather than implying it read June+August")
ok(wide["date_range"]["from"] == "2026-07-01" and wide["date_range"]["to"] == "2026-07-31",
   "the effective window is the month's own bounds")
ok(wide["date_range"]["requested_from"] == "2026-06-15" and wide["date_range"]["requested_to"] == "2026-08-20",
   "the REQUESTED window is echoed too, so the UI can explain what it did")
ok(run(date_from="2026-07-01", date_to="2026-07-31")["date_range"]["clamped"] is False,
   "a window exactly equal to the month is NOT reported as clamped")

print("\n§4 · a window with NO overlap = empty tables, never a silent whole-month fallback")
none_ = run(date_from="2026-09-01", date_to="2026-09-30")
ok(none_["date_range"]["no_overlap"] is True, "no_overlap is reported")
ok(tot(none_) == 0 and none_["by_location"]["rows"] == [] and none_["by_employee"]["rows"] == [],
   "and every table is empty (the filter is honoured, not ignored)")
back = run(date_from="2026-07-20", date_to="2026-07-05")     # backwards window
ok(back["date_range"]["no_overlap"] is True and tot(back) == 0,
   "a BACKWARDS window (to < from) selects nothing rather than quietly swapping the bounds")

print("\n§5 · TRENDING follows the window, and names its basis")
ok(mid["trending"]["basis"] == "range", "basis == 'range' whenever a window is active")
ok(mid["trending"]["elapsed_days"] == 8, f"Jul 9→16 inclusive = 8 complete days (got {mid['trending']['elapsed_days']})")
ok(abs(mid["trending"]["factor"] - 31 / 8) < 1e-6, "factor = days_in_month / window days")
_ta = mid["by_location"]["total"]["total_activation"]
ok(mid["by_location"]["total"]["trending_box"] == round(_ta * 31 / 8),
   "Trending Box = window activations × the window factor")
# OPEN month: 'complete days' must still exclude today, exactly as the MTD divisor does.
_open = R._is_open_month
R._is_open_month = lambda p: True
try:
    op = R._exec_mtd(CLIENT, ORG, PERIOD, today=date(2026, 7, 10),
                     date_from="2026-07-01", date_to="2026-07-31")
    ok(op["trending"]["elapsed_days"] == 9,
       f"open month: Jul 1→10 counts 9 COMPLETE days, today excluded (got {op['trending']['elapsed_days']})")
    ok(op["by_location"]["total"]["total_activation"] == 2,
       "open month: the MTD cut still applies inside the window (Jul 16 + Jul 30 are future-dated)")
    op_today = R._exec_mtd(CLIENT, ORG, PERIOD, today=date(2026, 7, 2),
                           date_from="2026-07-02", date_to="2026-07-02")
    ok(op_today["trending"]["elapsed_days"] == 1,
       "a window of only TODAY divides by 1, never by 0 (no ZeroDivisionError, no infinite trend)")
finally:
    R._is_open_month = _open

print("\n§6 · a row with NO date cannot be placed in a window — excluded AND counted")
undated = dict(_act(2, A, "Ana", "T9")); undated["trans_date"] = ""
R._sales_rows_union = lambda client, org_id, period, cols=None: (ROWS + [undated], dict(META))
try:
    nofilter = run()
    ok(nofilter["by_location"]["total"]["total_activation"] == 5,
       "with NO range the undated row is still counted (behaviour unchanged for existing callers)")
    ok(nofilter["date_range"]["undated_excluded"] == 0, "…and nothing is reported as excluded")
    win = run(date_from="2026-07-01", date_to="2026-07-31")
    ok(win["by_location"]["total"]["total_activation"] == 4, "with a range the undated row is excluded")
    ok(win["date_range"]["undated_excluded"] == 1,
       f"…and it is COUNTED, not silently dropped (got {win['date_range']['undated_excluded']})")
finally:
    R._sales_rows_union = lambda client, org_id, period, cols=None: (list(ROWS), dict(META))

print("\n§7 · a MALFORMED date is 'not supplied' — a bad querystring never breaks the report")
junk = run(date_from="not-a-date", date_to="")
ok(junk["date_range"]["active"] is False, "unparseable bounds deactivate the window")
ok(tot(junk) == tot(base), "…and the report is identical to the no-range one")
half = run(date_from="2026-07-09", date_to="garbage")
ok(half["date_range"]["from"] == "2026-07-09" and half["date_range"]["to"] == "2026-07-31",
   "one good bound + one bad bound = the good bound + the month's own end")

print(f"\n{'─' * 78}\n  {len(PASS)} PASS · {len(FAIL)} FAIL")
if FAIL:
    for f in FAIL:
        print("   ✗ " + f)
sys.exit(1 if FAIL else 0)
