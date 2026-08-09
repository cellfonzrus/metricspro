"""Harness for the two live comp-upload defects (2026-08-09).

  BUG A  comp `quantity` was mapped with safe_float -> '1.0' -> Postgres 22P02 on an integer
         column -> the whole upload died on row 0.
  BUG B  the upload DELETED the target period and then inserted. A failed insert left the period
         empty: raw_comp_report April 2026, 10,431 rows -> 0.

The fake client below reproduces the PostgREST/Postgres behaviour that actually bit us — including
the integer-column rejection, verbatim — so the tests fail for the real reason rather than a
mocked-out one. Where a real captured workbook is available (scratchpad/epaycap/*.xlsx, pulled live
from the portal today) the mapper is exercised against THOSE bytes, not synthetic rows.

Run:  python3 backend/harness_comp_upload_safety.py
"""
import os
import sys
import uuid
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc.calculator import safe_float, safe_int          # noqa: E402
from app.modules.commcalc.epay_sweep import map_comp_report_row           # noqa: E402
from app.modules.commcalc.safe_replace import safe_replace, ReplaceFailed  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {detail}")
        print(f"  FAIL {name} :: {detail}")


from harness_fakedb import (PgError, FakeTable, Query, FakeSchema,  # noqa: E402
                            FakeTableAPI, FakeClient)

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"


def comp_table():
    return FakeTable({"org_id": "text", "period": "text", "quantity": "int",
                      "payment_amount": "num", "begin_date": "text"})


def seed(db, name, tbl, rows):
    db.tables[name] = tbl
    tbl.name = name
    for r in rows:
        db.clock += timedelta(milliseconds=1)
        rr = dict(r)
        rr.setdefault("id", str(uuid.uuid4()))
        if tbl.has_created_at:
            rr.setdefault("created_at", db.clock.isoformat())
        tbl.rows.append(rr)
    db.clock += timedelta(seconds=3600)   # the old load is an hour older than anything new
    return tbl


def scope_period(org, period):
    return lambda q: q.eq("org_id", org).in_("period", [period])


# ═══ SECTION A — BUG A: the integer column ══════════════════════════════════════════════════
print("\n== A: quantity must reach an INTEGER column as an int ==")

check("A1 safe_float is the culprit", isinstance(safe_float("1"), float) and safe_float("1") == 1.0,
      f"safe_float('1')={safe_float('1')!r}")
for raw, want in [("1", 1), ("1.0", 1), (1.0, 1), (2, 2), (" 3 ", 3), ("4,000", 4000),
                  ("", None), (None, None), ("nan", None), ("abc", None), ("1.5", None)]:
    got = safe_int(raw)
    check(f"A2 safe_int({raw!r})->{want!r}", got == want, f"got {got!r}")
check("A3 safe_int never returns a float",
      all(not isinstance(safe_int(v), float) for v in ["1", 1.0, 2, "3"]))
check("A4 a fractional quantity is NOT silently truncated", safe_int("1.5") is None,
      "1.5 must not become 1 — it lands NULL so it is findable")

# the mapper, against REAL bytes pulled from the portal today
CAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "..", "tmp_epaycap")
CAP = os.environ.get("EPAY_CAP_DIR", CAP)
real_files = []
if os.path.isdir(CAP):
    real_files = [os.path.join(CAP, f) for f in sorted(os.listdir(CAP)) if f.endswith(".xlsx")]
if real_files:
    import pandas as pd
    total, bad = 0, 0
    for f in real_files:
        df = pd.read_excel(f, dtype=str).fillna("")
        recs = df.to_dict("records")
        if not recs:
            continue
        rows = [map_comp_report_row(r, {"org_id": ORG, "period": "X"}) for r in recs]
        for r in rows:
            total += 1
            q = r["quantity"]
            if q is not None and not isinstance(q, int):
                bad += 1
    check(f"A5 mapper on {len(real_files)} REAL portal workbooks ({total} rows): every quantity is int",
          total > 0 and bad == 0, f"{bad} non-int of {total}")
    # and those rows must survive the integer column
    db = FakeClient()
    seed(db, "raw_comp_report", comp_table(), [])
    df = pd.read_excel(real_files[0], dtype=str).fillna("")
    rows = [map_comp_report_row(r, {"org_id": ORG, "period": "April 2026"})
            for r in df.to_dict("records")]
    try:
        res = safe_replace(db, "raw_comp_report", rows, scope_period(ORG, "April 2026"))
        check("A6 a real workbook now inserts against the integer column",
              res["saved"] == len(rows), str(res))
    except Exception as e:
        check("A6 a real workbook now inserts against the integer column", False, repr(e))
else:
    print("  (no captured workbooks; set EPAY_CAP_DIR to the scratchpad epaycap dir for A5/A6)")

# the pre-fix mapper must be shown to FAIL, so the test proves the fix and not the mock
db = FakeClient()
seed(db, "raw_comp_report", comp_table(), [])
old_style = [{"org_id": ORG, "period": "April 2026", "quantity": safe_float("1")}]
try:
    db.tables["raw_comp_report"].name = "raw_comp_report"
    FakeTableAPI(db, db.tables["raw_comp_report"]).insert(old_style).execute()
    check("A7 the OLD safe_float mapping is rejected by the integer column", False, "it was accepted")
except PgError as e:
    check("A7 the OLD safe_float mapping is rejected by the integer column", "22P02" in str(e), str(e))
new_style = [{"org_id": ORG, "period": "April 2026", "quantity": safe_int("1")}]
try:
    FakeTableAPI(db, db.tables["raw_comp_report"]).insert(new_style).execute()
    check("A8 the NEW safe_int mapping is accepted", True)
except PgError as e:
    check("A8 the NEW safe_int mapping is accepted", False, str(e))


# ═══ SECTION B — BUG B: a failed insert must not destroy the period ═════════════════════════
print("\n== B: replace is atomic in effect ==")

APRIL = [{"org_id": ORG, "period": "April 2026", "quantity": 1, "payment_amount": 10.0,
          "begin_date": f"2026-04-{d:02d}"} for d in range(1, 30) for _ in range(3)]

# B1 — the exact April incident: good file, insert blows up on row 0
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(), APRIL)
before = len(t.rows)
bad_rows = [{"org_id": ORG, "period": "April 2026", "quantity": 1.0}] * 10   # the float bug
try:
    safe_replace(db, "raw_comp_report", bad_rows, scope_period(ORG, "April 2026"))
    check("B1 a failing insert raises", False, "it did not raise")
except ReplaceFailed as e:
    check("B1 a failing insert raises ReplaceFailed", True)
    check("B1 the period SURVIVED", len(t.rows) == before, f"{len(t.rows)} != {before}")
    check("B1 it reports the data was preserved", e.restored is True, f"restored={e.restored}")
    check("B1 the message says so", "left untouched" in str(e), str(e)[:160])

# B2 — the same scenario under the OLD delete-then-insert, to prove the harness can see the bug
db2 = FakeClient()
t2 = seed(db2, "raw_comp_report", comp_table(), APRIL)
api = FakeTableAPI(db2, t2)
api.delete().eq("org_id", ORG).in_("period", ["April 2026"]).execute()
try:
    api.insert(bad_rows).execute()
except PgError:
    pass
check("B2 the OLD ordering destroys the period (control)", len(t2.rows) == 0,
      f"{len(t2.rows)} rows left — the harness would not have caught the bug")

# B3 — failure half way through a multi-batch insert
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(), APRIL)
before = len(t.rows)
db.fail_on_batch = 3
good = [{"org_id": ORG, "period": "April 2026", "quantity": 2} for _ in range(2000)]
try:
    safe_replace(db, "raw_comp_report", good, scope_period(ORG, "April 2026"))
    check("B3 mid-insert failure raises", False, "no raise")
except ReplaceFailed as e:
    check("B3 mid-insert failure raises", True)
    check("B3 period is byte-identical afterwards", len(t.rows) == before,
          f"{len(t.rows)} != {before}")
    check("B3 no partial rows left behind",
          all(r.get("quantity") != 2 for r in t.rows), "new rows leaked in")
    check("B3 restored=True", e.restored is True)

# B4 — the happy path really does swap
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(), APRIL)
fresh = [{"org_id": ORG, "period": "April 2026", "quantity": 7, "payment_amount": 1.0}
         for _ in range(120)]
res = safe_replace(db, "raw_comp_report", fresh, scope_period(ORG, "April 2026"))
check("B4 saved all new rows", res["saved"] == 120, str(res))
check("B4 old rows retired", len(t.rows) == 120, f"{len(t.rows)} rows")
check("B4 only the new load remains", all(r["quantity"] == 7 for r in t.rows))
check("B4 reports the swap", res["mode"] == "swapped" and res["prior"] == len(APRIL), str(res))
check("B4 no warning", res["warning"] is None, str(res["warning"]))

# B5 — an empty replacement never deletes
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(), APRIL)
res = safe_replace(db, "raw_comp_report", [], scope_period(ORG, "April 2026"))
check("B5 empty set deletes nothing", len(t.rows) == len(APRIL), f"{len(t.rows)}")
check("B5 reports skipped_empty", res["mode"] == "skipped_empty", str(res))

# B6 — other periods and other TENANTS are untouched
db = FakeClient()
rows = (APRIL
        + [{"org_id": ORG, "period": "March 2026", "quantity": 5} for _ in range(40)]
        + [{"org_id": OTHER_ORG, "period": "April 2026", "quantity": 9} for _ in range(25)])
t = seed(db, "raw_comp_report", comp_table(), rows)
safe_replace(db, "raw_comp_report", fresh, scope_period(ORG, "April 2026"))
check("B6 March untouched",
      len([r for r in t.rows if r["period"] == "March 2026"]) == 40)
check("B6 the OTHER tenant's April untouched",
      len([r for r in t.rows if r["org_id"] == OTHER_ORG]) == 25)
check("B6 house April replaced",
      len([r for r in t.rows if r["org_id"] == ORG and r["period"] == "April 2026"]) == 120)

# B7 — a table with NO created_at (daily_sales_feed) uses the id-paging fallback
db = FakeClient()
tf = FakeTable({"org_id": "text", "period": "text", "trans_date": "text"}, has_created_at=False)
seed(db, "daily_sales_feed", tf,
     [{"org_id": ORG, "period": "August 2026", "trans_date": "2026-08-01"} for _ in range(2500)])
newf = [{"org_id": ORG, "period": "August 2026", "trans_date": "2026-08-01"} for _ in range(30)]
res = safe_replace(db, "daily_sales_feed", newf,
                   lambda q: q.eq("org_id", ORG).in_("trans_date", ["2026-08-01"]))
check("B7 no-created_at swap works", len(tf.rows) == 30, f"{len(tf.rows)} rows")
check("B7 reports swapped, prior counted past the 1000 page size",
      res["mode"] == "swapped" and res["prior"] == 2500, str(res))

# B8 — no-created_at table, failing insert, still restored
db = FakeClient()
tf = FakeTable({"org_id": "text", "period": "text", "trans_date": "text", "n": "int"},
               has_created_at=False)
seed(db, "daily_sales_feed", tf,
     [{"org_id": ORG, "trans_date": "2026-08-01", "n": 1} for _ in range(300)])
db.fail_on_batch = 2
try:
    safe_replace(db, "daily_sales_feed",
                 [{"org_id": ORG, "trans_date": "2026-08-01", "n": 2} for _ in range(900)],
                 lambda q: q.eq("org_id", ORG).in_("trans_date", ["2026-08-01"]))
    check("B8 raises", False, "no raise")
except ReplaceFailed as e:
    check("B8 raises + restores with no created_at", len(tf.rows) == 300 and e.restored,
          f"{len(tf.rows)} rows restored={e.restored}")
    check("B8 no new rows survived", all(r["n"] == 1 for r in tf.rows))

# B9 — first-ever load (nothing to replace)
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(), [])
res = safe_replace(db, "raw_comp_report", fresh, scope_period(ORG, "April 2026"))
check("B9 first load inserts", len(t.rows) == 120 and res["mode"] == "inserted", str(res))

# B10 — first-ever load that FAILS leaves the table empty, not half-full
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(), [])
db.fail_on_batch = 2
try:
    safe_replace(db, "raw_comp_report",
                 [{"org_id": ORG, "period": "April 2026", "quantity": 3} for _ in range(900)],
                 scope_period(ORG, "April 2026"))
    check("B10 raises", False, "no raise")
except ReplaceFailed:
    check("B10 failed first load leaves nothing behind", len(t.rows) == 0, f"{len(t.rows)}")

# B11 — the delete of the OLD load failing is reported, and never loses the NEW data
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(), APRIL)
db.fail_delete = True
res = safe_replace(db, "raw_comp_report", fresh, scope_period(ORG, "April 2026"))
check("B11 new rows are safe even if retiring the old load fails",
      len([r for r in t.rows if r["quantity"] == 7]) == 120)
check("B11 and it WARNS about the duplicates", bool(res["warning"]), str(res))

# B12 — the scope must carry org_id (multi-tenant rule) — a scope without it would hit both
#       tenants; assert the helper does not add or assume tenancy of its own.
db = FakeClient()
t = seed(db, "raw_comp_report", comp_table(),
         [{"org_id": OTHER_ORG, "period": "April 2026", "quantity": 9} for _ in range(10)])
res = safe_replace(db, "raw_comp_report", fresh, scope_period(ORG, "April 2026"))
check("B12 an org-scoped replace does not see the other tenant's rows as 'prior'",
      res["prior"] == 0 and res["mode"] == "inserted", str(res))
check("B12 the other tenant still has its rows",
      len([r for r in t.rows if r["org_id"] == OTHER_ORG]) == 10)


# ═══ SECTION C — a multi-month comp file must be refused, not mislabelled ═══════════════════
print("\n== C: multi-month comp file detection ==")
from app.modules.commcalc.epay_sweep import (comp_month_spread,          # noqa: E402
                                             comp_period_from_records)

if real_files:
    import pandas as pd
    by_month = {}
    for f in real_files:
        recs = pd.read_excel(f, dtype=str).fillna("").to_dict("records")
        if recs:
            by_month[os.path.basename(f)] = recs
    single = next((r for r in by_month.values() if len(comp_month_spread(r)) == 1), None)
    check("C1 a single-month real file reports exactly one month",
          single is not None and len(comp_month_spread(single)) == 1)
    # concatenate real pulls from three different months = the owner's 3-month file
    multi = []
    for recs in by_month.values():
        multi += recs
    spread = comp_month_spread(multi)
    check("C2 a concatenated multi-month file reports every month", len(spread) >= 3, str(spread))
    check("C3 the dominant-month check ALONE would have missed it",
          comp_period_from_records(multi) is not None and len(spread) > 1,
          "dominant month hides the other months — this is why C2 exists")
    check("C4 counts add up", sum(n for _p, n in spread) == len([r for r in multi
                                                                 if str(r.get('Begin Date', '')).strip()]),
          str(spread))
else:
    print("  (skipped — needs EPAY_CAP_DIR)")

check("C5 an empty record set has no months", comp_month_spread([]) == [])
check("C6 rows with no Begin Date are ignored, not counted as a month",
      comp_month_spread([{"Begin Date": ""}, {"Begin Date": "07/15/2026"}]) == [("July 2026", 1)],
      str(comp_month_spread([{"Begin Date": ""}, {"Begin Date": "07/15/2026"}])))
check("C7 ISO Begin Dates parse too",
      comp_month_spread([{"Begin Date": "2026-06-20"}]) == [("June 2026", 1)])

print(f"\n{'=' * 60}\nPASS {PASS}   FAIL {FAIL}")
for f in FAILURES:
    print("  -", f)
sys.exit(1 if FAIL else 0)
