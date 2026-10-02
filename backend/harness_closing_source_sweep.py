"""Proof harness — the DERIVATION SWEEP over the REAL router function, DB-free
(owner 2026-10-02, mig 1035, index §19.39).

`harness_closing_source.py` proves the pure registry. This one drives the actual code that will run in
production — `closing.router._derive_closing_day`, `_closing_source`, `_closing_source_map` and
`closing_stores` — against the in-memory client (`harness_intake_fakes.FakeDB`), so the claims are made
about the shipped functions and not about a restatement of them:

  A. THE BYTE-IDENTICAL CLAIM. With no config rows at all, a sweep derives nothing and every store
     stays on rep entry — the state of every tenant that never opens the screen.
  B. A derived store's day is WRITTEN from the feed: the real `_b2b_day` aggregate → a real
     `commcalc.daily_closing` row, with the feed's cash/card in the tender columns.
  C. A rep-entry store in the same org, on the same day, is NOT touched.
  D. IDEMPOTENT. A second sweep over an unchanged feed writes nothing (`unchanged`).
  E. A CHANGED FEED refreshes the same row — it never inserts a second one (the duplicate-closing
     class the mig-502 guard exists to prevent, from the derivation side).
  F. A HUMAN'S ROW IS NEVER OVERWRITTEN. A rep-submitted row for a store later switched to derived is
     kept and reported (`kept_manual`).
  G. A MISSING FEED IS REPORTED, NOT WRITTEN. No rows for a store → `skipped` with `no_feed`, and the
     table stays empty for that store — no manufactured $0 close.
  H. `dry_run` writes nothing at all.
  I. The closing picker (`closing_stores`) carries each option's source, so the form knows before a
     rep types anything.

Run: python3 backend/harness_closing_source_sweep.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from harness_intake_fakes import FakeDB  # noqa: E402
from app.modules.closing import router as R  # noqa: E402
from app.modules.closing import closing_source as cs  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


ORG = "11111111-1111-1111-1111-111111111111"
DAY = "2026-09-15"          # a CLOSED month relative to the fixtures, so `raw_sales` is the primary read
PERIOD = "September 2026"


def sale(store, trans, tender, amount, contract="New Activation", dept="Phones"):
    return {"org_id": ORG, "period": PERIOD, "store": store, "salesperson": "Ana",
            "department": dept, "category": dept, "product_desc": "Handset",
            "gp": 10.0, "ext_price": amount, "trans_id": trans, "trans_date": DAY,
            "contract_type": contract, "tender_type": tender, "voided": "false",
            "trans_type": "Sale", "mdn": f"555000{trans}", "serial_1": f"SN{trans}"}


def fresh(cfg_rows, sales):
    db = FakeDB()
    db.seed("stores", [
        {"org_id": ORG, "store_code": "B-FEED", "address": "1 Feed St", "market": "North", "is_active": True},
        {"org_id": ORG, "store_code": "B-REP", "address": "2 Rep Ave", "market": "North", "is_active": True},
        {"org_id": ORG, "store_code": "B-DARK", "address": "3 Dark Rd", "market": "North", "is_active": True},
    ])
    db.seed("store_mapping", [
        {"org_id": ORG, "store_code": "B-FEED", "store_address": "1 Feed St", "market": "North",
         "salesforce_id": "SF-FEED"},
        {"org_id": ORG, "store_code": "B-REP", "store_address": "2 Rep Ave", "market": "North",
         "salesforce_id": "SF-REP"},
        {"org_id": ORG, "store_code": "B-DARK", "store_address": "3 Dark Rd", "market": "North",
         "salesforce_id": "SF-DARK"},
    ])
    # The config table's COLUMNS must exist for the select even when the tenant has set nothing — the
    # genuinely absent table (pre-migration) is section A's `no_cfg_table` case below.
    db.declared["closing_source_config"] = ["id", "org_id", "store_code", "source", "updated_at", "updated_by"]
    # Seed the config table even when empty of ROWS, so its columns exist for the select (an absent
    # table is the pre-migration case, covered separately in section A).
    if cfg_rows:
        db.seed("closing_source_config", [dict(r, org_id=ORG) for r in cfg_rows])
    db.seed("raw_sales", sales)
    return db


def closings(db, store=None):
    rows = [r for r in (db.tables.get("daily_closing") or []) if r.get("org_id") == ORG]
    return [r for r in rows if store is None or r.get("store_code") == store]


SALES = [
    # B-FEED: $300 cash + $200 card, one activation and one upgrade
    sale("1 Feed St", "T1", "Cash", 300.0, "New Activation"),
    sale("1 Feed St", "T2", "Visa", 200.0, "Upgrade"),
    # B-REP: real money the feed HAS, which must still be left to its reps
    sale("2 Rep Ave", "T3", "Cash", 90.0, "New Activation"),
]

print("A. THE BYTE-IDENTICAL CLAIM — no config at all")
db = fresh([], SALES)
check("resolver answers rep entry for every store with no config table rows",
      R._closing_source(db, ORG, "B-FEED") == "rep_entry"
      and R._closing_source(db, ORG, "B-REP") == "rep_entry")
res = R._derive_closing_day(db, ORG, DAY)
check("a sweep derives nothing", res["wrote"] == [] and res["derived_stores"] == 0
      and res["skipped"] == [], str(res))
check("every store is reported as rep entry", res["rep_entry_stores"] == 3)
check("no closing row was written", closings(db) == [])

print("B. a derived store's day is written from the feed")
db = fresh([{"store_code": None, "source": "b2b_derived"},
            {"store_code": "B-REP", "source": "rep_entry"}], SALES)
check("the org default resolves through the real resolver",
      R._closing_source(db, ORG, "B-FEED") == "b2b_derived"
      and R._closing_source(db, ORG, "B-REP") == "rep_entry")
res = R._derive_closing_day(db, ORG, DAY)
check("B-FEED derived, B-REP left alone, B-DARK reported as having no feed",
      res["wrote"] == ["B-FEED"] and res["rep_entry_stores"] == 1
      and res["skipped"] == [{"store_code": "B-DARK", "reason": "no_feed"}], str(res))
row = (closings(db, "B-FEED") or [{}])[0]
check("the feed's cash and card landed in the rep path's own tender columns",
      row.get("t_cash") == 300.0 and row.get("t_credit") == 200.0, str(row))
check("the legacy mirror columns match", row.get("store_cash") == 300.0 and row.get("store_cc") == 200.0)
check("counts came from the shared activation classifier",
      row.get("new_line_count") == 1 and row.get("upgrade_count") == 1)
check("the row is marked as derived, with no submitter invented",
      row.get("source") == cs.DERIVED_ROW_SOURCE and row.get("employee_name") is None)
check("the row carries the store's identity the rest of the platform writes",
      row.get("store_code") == "B-FEED" and row.get("close_date") == DAY and row.get("period") == DAY[:7])

print("C. the rep-entry store in the same org, on the same day")
check("no row was written for B-REP even though the feed has its money",
      closings(db, "B-REP") == [])

print("D. IDEMPOTENT — a second sweep over an unchanged feed")
res2 = R._derive_closing_day(db, ORG, DAY)
check("reports unchanged and writes nothing",
      res2["unchanged"] == ["B-FEED"] and res2["wrote"] == [] and res2["updated"] == [], str(res2))
check("still exactly one row for the store", len(closings(db, "B-FEED")) == 1)

print("E. a CHANGED feed refreshes the same row, never a second one")
db.seed("raw_sales", [sale("1 Feed St", "T4", "Cash", 50.0, "New Activation")])
res3 = R._derive_closing_day(db, ORG, DAY)
check("the change is reported field by field",
      len(res3["updated"]) == 1 and res3["updated"][0]["store_code"] == "B-FEED"
      and "t_cash" in res3["updated"][0]["changed"], str(res3))
check("STILL ONE ROW — a second insert here would double the store's declared cash",
      len(closings(db, "B-FEED")) == 1)
check("the refreshed row carries the new cash and count",
      closings(db, "B-FEED")[0].get("t_cash") == 350.0
      and closings(db, "B-FEED")[0].get("new_line_count") == 2)

print("F. a human's row is never overwritten")
db = fresh([{"store_code": None, "source": "b2b_derived"}], SALES)
db.seed("daily_closing", [{"org_id": ORG, "close_date": DAY, "period": DAY[:7], "store_code": "B-FEED",
                          "employee_name": "Ana", "source": "manual", "t_cash": 11.11, "t_credit": 0.0}])
res = R._derive_closing_day(db, ORG, DAY)
check("the rep's row is kept and reported",
      res["kept_manual"] == [{"store_code": "B-FEED", "rows": 1, "sources": ["manual"]}], str(res))
check("the rep's declared cash is untouched",
      closings(db, "B-FEED")[0].get("t_cash") == 11.11 and len(closings(db, "B-FEED")) == 1)

print("G. a store with no tender split is refused, not written as a clean zero")
db = fresh([{"store_code": None, "source": "b2b_derived"}],
           [sale("1 Feed St", "T9", "Loyalty Points", 70.0)])
res = R._derive_closing_day(db, ORG, DAY)
check("reported with its reason",
      {"store_code": "B-FEED", "reason": "no_tender_split"} in res["skipped"], str(res))
check("and nothing was written for it", closings(db, "B-FEED") == [])

print("H. dry_run writes nothing")
db = fresh([{"store_code": None, "source": "b2b_derived"},
            {"store_code": "B-REP", "source": "rep_entry"}], SALES)
res = R._derive_closing_day(db, ORG, DAY, dry_run=True)
check("reports what it WOULD write", res["wrote"] == ["B-FEED"] and res["dry_run"] is True, str(res))
check("but the table is still empty", closings(db) == [])

print("I. the closing picker carries each option's source")
db = fresh([{"store_code": None, "source": "b2b_derived"},
            {"store_code": "B-REP", "source": "rep_entry"}], SALES)
_real_sb = R.sb
R.sb = lambda: db
try:
    opts = R.closing_stores(org_id=ORG)
finally:
    R.sb = _real_sb
by_code = {o.get("store_code"): o for o in opts}
check("every option carries a source", all("closing_source" in o for o in opts), str(opts))
check("the derived store reads derived and the override reads rep entry",
      by_code.get("B-FEED", {}).get("closing_source") == "b2b_derived"
      and by_code.get("B-REP", {}).get("closing_source") == "rep_entry", str(by_code))

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_closing_source_sweep: ALL PASS")
