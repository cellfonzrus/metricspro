"""Proof harness — the RETROACTIVE BACKFILL over the REAL endpoint, DB-free
(owner 2026-10-02, index §19.39).

`harness_closing_source.py` proves the pure registry and `harness_closing_source_sweep.py` proves one
day's sweep. This one drives the actual backfill endpoint — `closing.router.derive_closing_range` and
`_derive_date_span` — against the in-memory client, because a backfill's whole risk is in the span: a
day silently missed, a day done twice, or a re-run that rewrites history it should have left alone.

  A. THE SPAN IS THE SPAN. Every day from start to end inclusive is visited exactly once, oldest
     first, and the response's own `days` agrees with the days it reports.
  B. ONE SWEEP, NOT A SECOND DERIVATION. The backfill's per-day numbers equal what the one-day sweep
     reports for the same day — the claim that there is no parallel copy of the derivation rules.
  C. A DAY WITH NO FEED IS REPORTED, NOT WRITTEN. An empty day in the middle of the span yields no
     row and names its reason, and the span continues past it.
  D. IDEMPOTENT ACROSS THE SPAN. A second backfill over the same range writes nothing: every day
     reports `unchanged`.
  E. A HUMAN'S ROW SURVIVES A BACKFILL. A rep's row inside the span is kept and counted, not
     overwritten — the property that makes a retroactive run safe to point at months of history.
  F. `dry_run` over a whole span writes nothing at all, while still reporting what it would do.
  G. THE BOUNDS REFUSE RATHER THAN GUESS. A reversed range, an unparseable date and an over-long
     span are refused; the limit is a stated number, not an unbounded sweep.
  H. A REP-ENTRY STORE IS NEVER BACKFILLED, on any day of the span.

Run: python3 backend/harness_closing_source_backfill.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from fastapi import HTTPException  # noqa: E402

from harness_intake_fakes import FakeDB  # noqa: E402
from app.modules.closing import router as R  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


ORG = "11111111-1111-1111-1111-111111111111"
START, END = "2026-09-10", "2026-09-14"          # a closed month, so `raw_sales` is the primary read
DAYS = [(date.fromisoformat(START) + timedelta(days=i)).isoformat() for i in range(5)]
DARK_DAY = DAYS[2]                                # the hole in the middle of the span
PERIOD = "September 2026"


def sale(store, trans, tender, amount, day, contract="New Activation"):
    return {"org_id": ORG, "period": PERIOD, "store": store, "salesperson": "Ana",
            "department": "Phones", "category": "Phones", "product_desc": "Handset",
            "gp": 10.0, "ext_price": amount, "trans_id": f"{trans}-{day}", "trans_date": day,
            "contract_type": contract, "tender_type": tender, "voided": "false",
            "trans_type": "Sale", "mdn": f"555{trans}", "serial_1": f"SN{trans}{day}"}


def fresh(cfg_rows, skip_days=(DARK_DAY,)):
    """Two stores, one derived and one on rep entry, with the feed carrying every day of the span
    except `skip_days` — so the hole is real data absence, not a fixture convenience."""
    db = FakeDB()
    db.seed("stores", [
        {"org_id": ORG, "store_code": "B-FEED", "address": "1 Feed St", "market": "North", "is_active": True},
        {"org_id": ORG, "store_code": "B-REP", "address": "2 Rep Ave", "market": "North", "is_active": True},
    ])
    db.seed("store_mapping", [
        {"org_id": ORG, "store_code": "B-FEED", "store_address": "1 Feed St", "market": "North",
         "salesforce_id": "SF-FEED"},
        {"org_id": ORG, "store_code": "B-REP", "store_address": "2 Rep Ave", "market": "North",
         "salesforce_id": "SF-REP"},
    ])
    db.declared["closing_source_config"] = ["id", "org_id", "store_code", "source", "updated_at", "updated_by"]
    if cfg_rows:
        db.seed("closing_source_config", [dict(r, org_id=ORG) for r in cfg_rows])
    sales = []
    for d in DAYS:
        if d in skip_days:
            continue
        sales += [sale("1 Feed St", "T1", "Cash", 300.0, d),
                  sale("1 Feed St", "T2", "Visa", 200.0, d),
                  sale("2 Rep Ave", "T3", "Cash", 90.0, d)]
    db.seed("raw_sales", sales)
    return db


def closings(db, store=None):
    rows = [r for r in (db.tables.get("daily_closing") or []) if r.get("org_id") == ORG]
    return [r for r in rows if store is None or r.get("store_code") == store]


def run(db, start=START, end=END, dry_run=False):
    """Drive the REAL endpoint: its own permission gate and client factory, pointed at the fake."""
    orig_sb, orig_perms = R.sb, R._can_edit_closing_setting
    R.sb = lambda *a, **k: db
    R._can_edit_closing_setting = lambda *a, **k: True
    try:
        return R.derive_closing_range(start=start, end=end, dry_run=dry_run, org_id=ORG, authorization="")
    finally:
        R.sb, R._can_edit_closing_setting = orig_sb, orig_perms


DERIVED_CFG = [{"store_code": "B-FEED", "source": "b2b_derived"},
               {"store_code": "B-REP", "source": "rep_entry"}]

print("A. THE SPAN IS THE SPAN")
db = fresh(DERIVED_CFG)
res = run(db)
check("every day of the inclusive span is visited exactly once",
      [d["date"] for d in res["per_day"]] == DAYS, str([d["date"] for d in res["per_day"]]))
check("the reported day count agrees with the days reported",
      res["days"] == len(DAYS) == len(res["per_day"]), str(res["days"]))
check("the bounds come back as the bounds asked for",
      res["start"] == START and res["end"] == END)
check("the days that had a feed are counted separately from the span",
      res["days_with_feed"] == 4, str(res["days_with_feed"]))

print("B. ONE SWEEP, NOT A SECOND DERIVATION")
db2 = fresh(DERIVED_CFG)
one = R._derive_closing_day(db2, ORG, DAYS[0])
first = res["per_day"][0]
check("the backfill's first day matches the one-day sweep's own numbers",
      first["wrote"] == len(one["wrote"]) == 1 and first["skipped"] == len(one["skipped"]),
      f"{first} vs {one['wrote']}/{one['skipped']}")
check("the span's totals are the sum of its days",
      res["totals"]["wrote"] == sum(d["wrote"] for d in res["per_day"]) == 4, str(res["totals"]))

print("C. A DAY WITH NO FEED IS REPORTED, NOT WRITTEN")
hole = [d for d in res["per_day"] if d["date"] == DARK_DAY][0]
check("the empty day wrote nothing", hole["wrote"] == 0 and hole["updated"] == 0)
check("the empty day names its reason", hole["reasons"] == ["no_feed"], str(hole))
check("no closing row exists for the empty day",
      [r for r in closings(db) if r.get("close_date") == DARK_DAY] == [])
check("the span continued past the hole",
      [d["date"] for d in res["per_day"] if d["wrote"]] == [DAYS[0], DAYS[1], DAYS[3], DAYS[4]])
check("exactly the four days with a feed were written",
      sorted(r["close_date"] for r in closings(db, "B-FEED")) == [DAYS[0], DAYS[1], DAYS[3], DAYS[4]])

print("D. IDEMPOTENT ACROSS THE SPAN")
again = run(db)
check("a second backfill wrote nothing", again["totals"]["wrote"] == 0
      and again["totals"]["updated"] == 0, str(again["totals"]))
check("every day with a feed reports unchanged", again["totals"]["unchanged"] == 4,
      str(again["totals"]))
check("the row count did not grow", len(closings(db, "B-FEED")) == 4)

print("E. A HUMAN'S ROW SURVIVES A BACKFILL")
db3 = fresh(DERIVED_CFG)
db3.seed("daily_closing", [{"org_id": ORG, "store_code": "B-FEED", "close_date": DAYS[1],
                            "period": PERIOD, "employee_name": "Ana", "source": "manual",
                            "t_cash": 11.0, "t_credit": 22.0}])
res3 = run(db3)
check("the rep's day is kept, not overwritten", res3["totals"]["kept_manual"] == 1,
      str(res3["totals"]))
kept = [r for r in closings(db3, "B-FEED") if r.get("close_date") == DAYS[1]]
check("the human's own figures are still there", len(kept) == 1 and float(kept[0]["t_cash"]) == 11.0,
      str(kept))
check("the other days were still derived around it", res3["totals"]["wrote"] == 3,
      str(res3["totals"]))

print("F. dry_run over a whole span writes nothing")
db4 = fresh(DERIVED_CFG)
res4 = run(db4, dry_run=True)
check("dry_run is reported back", res4["dry_run"] is True)
check("it reports what it WOULD write", res4["totals"]["wrote"] == 4, str(res4["totals"]))
check("and wrote nothing", closings(db4) == [])

print("G. THE BOUNDS REFUSE RATHER THAN GUESS")


def refused(**kw):
    try:
        run(fresh(DERIVED_CFG), **kw)
        return None
    except HTTPException as e:
        return e.status_code


check("a reversed range is refused", refused(start=END, end=START) == 400)
check("an unparseable date is refused", refused(start="last tuesday", end=END) == 400)
check("an over-long span is refused",
      refused(start="2024-01-01", end="2026-09-14") == 400)
check("the limit is a stated number", isinstance(R.MAX_DERIVE_BACKFILL_DAYS, int)
      and R.MAX_DERIVE_BACKFILL_DAYS > 0)
check("a span exactly at the limit is allowed",
      len(R._derive_date_span("2026-01-01",
                              (date(2026, 1, 1) + timedelta(days=R.MAX_DERIVE_BACKFILL_DAYS - 1)).isoformat()))
      == R.MAX_DERIVE_BACKFILL_DAYS)
check("a one-day span is the single day", R._derive_date_span(START, START) == [START])

print("H. A REP-ENTRY STORE IS NEVER BACKFILLED")
check("no derived row exists for the rep-entry store on any day of the span",
      closings(db, "B-REP") == [], str(closings(db, "B-REP")))
db5 = fresh([{"store_code": None, "source": "rep_entry"}])
res5 = run(db5)
check("a tenant on rep entry backfills nothing at all",
      res5["totals"]["wrote"] == 0 and closings(db5) == [], str(res5["totals"]))

print()
if FAILS:
    print(f"RED — {len(FAILS)} failed:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("GREEN — the retroactive backfill visits every day once, derives through the one sweep, "
      "reports the days it could not, never overwrites a person's row, and refuses a span it "
      "cannot bound.")
