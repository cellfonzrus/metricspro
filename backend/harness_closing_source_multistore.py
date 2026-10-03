"""Proof harness — SETTING THE DAILY-CLOSING SOURCE FOR SEVERAL STORES AT ONCE, over the REAL
endpoint, DB-free (owner 2026-10-03, index §19.39).

Owner: *"in store setup to assign the store it should be a drop down list to select multiple stores."*

The screen now picks many stores in one dropdown, so `PUT /closing/source-config` takes many codes.
The risk in "many" is entirely in the fan-out: a store written twice, a store silently missed, a
partially-applied save reported as a success, or a second bulk write path that drifts from the single
one. This drives `closing.router.put_closing_source_config` itself against the in-memory client.

  A. MANY IS THE SAME AS ONE, N TIMES. Picking three stores leaves exactly three override rows, and
     each store resolves to what was picked — through the SAME single row-writer one store uses.
  B. NO STORE IS WRITTEN TWICE. A code offered twice (any casing, any padding) yields ONE row, and a
     re-save UPDATES the row instead of inserting a second — the partial unique index's own claim,
     proved without the database.
  C. AN EMPTY SELECTION IS STILL THE ORG DEFAULT. No codes writes the org-default row, exactly as a
     blank `store_code` did before this existed — the backward-compatibility claim for the
     company-default select and the Add-store row.
  D. CLEARING WORKS FOR A SELECTION. Clearing several stores drops their overrides and reports what
     each store resolves to afterwards; clearing with no store is REFUSED rather than wiping the
     org default.
  E. THE GATE STILL GATES. A caller who cannot edit closing settings is refused for a selection of
     ten stores exactly as for one — "bulk" is not a way around the permission.
  F. A BAD SOURCE IS REFUSED BEFORE ANYTHING IS WRITTEN. An unknown source value with a ten-store
     selection leaves zero rows behind — no half-applied save.
  G. THE OLD SHAPE IS UNCHANGED. A single `store_code` body behaves byte-identically to before, and
     the response still carries `store_code` / `source` / `scope` for the existing callers.

Run: python3 backend/harness_closing_source_multistore.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from fastapi import HTTPException  # noqa: E402

from harness_intake_fakes import FakeDB  # noqa: E402
from app.modules.closing import router as R  # noqa: E402
from app.modules.closing import closing_source as cs  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


ORG = "11111111-1111-1111-1111-111111111111"
CODES = ["B-1", "B-2", "B-3"]


def fresh(seed_rows=None):
    db = FakeDB()
    db.declared["closing_source_config"] = ["id", "org_id", "store_code", "source", "updated_at", "updated_by"]
    if seed_rows:
        db.seed("closing_source_config", [dict(r, org_id=ORG) for r in seed_rows])
    return db


def put(db, body, can_edit=True):
    """Drive the REAL endpoint: its own permission gate, client factory and caller identity, pointed
    at the fake. Nothing about the write is re-implemented here."""
    orig_sb, orig_perms, orig_email = R.sb, R._can_edit_closing_setting, R._caller_email
    R.sb = lambda *a, **k: db
    R._can_edit_closing_setting = lambda *a, **k: can_edit
    R._caller_email = lambda *a, **k: "admin@example.com"
    try:
        return R.put_closing_source_config(R.PutClosingSourceIn(**body), org_id=ORG, authorization="")
    finally:
        R.sb, R._can_edit_closing_setting, R._caller_email = orig_sb, orig_perms, orig_email


def cfg(db):
    return [r for r in (db.tables.get("closing_source_config") or []) if r.get("org_id") == ORG]


def overrides(db):
    return [r for r in cfg(db) if str(r.get("store_code") or "").strip()]


print("A. MANY IS THE SAME AS ONE, N TIMES")
db = fresh()
res = put(db, {"store_codes": CODES, "source": "b2b_derived"})
check("one call writes one override row per store picked",
      sorted(r["store_code"] for r in overrides(db)) == sorted(CODES),
      str([r.get("store_code") for r in overrides(db)]))
check("every picked store now resolves to the source that was picked",
      all(cs.resolve(cfg(db), c) == cs.SOURCE_B2B_DERIVED for c in CODES))
check("the response reports how many stores it covered, and which",
      res["count"] == 3 and res["store_codes"] == CODES and res["source"] == "b2b_derived",
      str(res))
check("a store NOT picked is untouched and still follows the org default",
      cs.resolve(cfg(db), "B-9") == cs.HOUSE_DEFAULT)

print("B. NO STORE IS WRITTEN TWICE")
db = fresh()
put(db, {"store_codes": ["B-1", "b-1", " B-1 "], "source": "b2b_derived"})
check("a store offered three times in one selection leaves ONE row",
      len(overrides(db)) == 1, str(overrides(db)))
put(db, {"store_codes": ["B-1"], "source": "rep_entry"})
check("re-saving the same store UPDATES its row rather than inserting a second",
      len(overrides(db)) == 1 and overrides(db)[0]["source"] == "rep_entry", str(overrides(db)))
db = fresh()
put(db, {"store_codes": CODES, "source": "b2b_derived"})
put(db, {"store_codes": CODES, "source": "rep_entry"})
check("re-saving a whole selection updates in place — three stores, three rows",
      len(overrides(db)) == 3 and {r["source"] for r in overrides(db)} == {"rep_entry"},
      str(overrides(db)))

print("C. AN EMPTY SELECTION IS STILL THE ORG DEFAULT")
db = fresh()
res = put(db, {"store_codes": [], "source": "b2b_derived"})
check("no codes writes the ORG DEFAULT row, not a store row",
      len(cfg(db)) == 1 and not str(cfg(db)[0].get("store_code") or "").strip(), str(cfg(db)))
check("the response says so (`scope` = org_default), as the company-default select expects",
      res["scope"] == "org_default" and res["store_code"] is None, str(res))
check("with the org default set, an unmentioned store follows it",
      cs.resolve(cfg(db), "B-7") == cs.SOURCE_B2B_DERIVED)
db = fresh()
put(db, {"source": "b2b_derived"})
check("an omitted selection (the old company-default body) is unchanged",
      len(cfg(db)) == 1 and not str(cfg(db)[0].get("store_code") or "").strip(), str(cfg(db)))

print("D. CLEARING WORKS FOR A SELECTION")
db = fresh([{"store_code": None, "source": "b2b_derived"}])
put(db, {"store_codes": CODES, "source": "rep_entry"})
res = put(db, {"store_codes": ["B-1", "B-2"], "clear": True})
check("clearing a selection drops exactly those stores' overrides",
      [r["store_code"] for r in overrides(db)] == ["B-3"], str(overrides(db)))
check("it reports what each cleared store resolves to now (the org default)",
      res["sources"] == {"B-1": "b2b_derived", "B-2": "b2b_derived"}, str(res.get("sources")))
check("the store that was NOT cleared keeps its own setting",
      cs.resolve(cfg(db), "B-3") == cs.SOURCE_REP_ENTRY)
try:
    put(db, {"store_codes": [], "clear": True})
    check("clearing with no store selected is REFUSED (the org default is never wiped)", False)
except HTTPException as e:
    check("clearing with no store selected is REFUSED (the org default is never wiped)",
          e.status_code == 400, str(e.detail))
check("the refusal wrote nothing — the org default row is still there",
      any(not str(r.get("store_code") or "").strip() for r in cfg(db)))

print("E. THE GATE STILL GATES")
db = fresh()
many = [f"B-{i}" for i in range(10)]
try:
    put(db, {"store_codes": many, "source": "b2b_derived"}, can_edit=False)
    check("a caller without the closing-settings permission is refused for a ten-store selection", False)
except HTTPException as e:
    check("a caller without the closing-settings permission is refused for a ten-store selection",
          e.status_code == 403, str(e.detail))
check("the refused bulk call wrote NOTHING", cfg(db) == [], str(cfg(db)))

print("F. A BAD SOURCE IS REFUSED BEFORE ANYTHING IS WRITTEN")
db = fresh()
try:
    put(db, {"store_codes": many, "source": "whatever_the_caller_felt_like"})
    check("an unknown source value is refused", False)
except HTTPException as e:
    check("an unknown source value is refused", e.status_code == 400, str(e.detail))
check("no store was half-written by the refused call", cfg(db) == [], str(cfg(db)))

print("G. THE OLD SHAPE IS UNCHANGED")
db = fresh()
res = put(db, {"store_code": "B-1", "source": "b2b_derived"})
check("a single `store_code` body still writes one override row",
      [r["store_code"] for r in overrides(db)] == ["B-1"], str(overrides(db)))
check("the response still carries the keys the existing screen reads",
      res["store_code"] == "B-1" and res["source"] == "b2b_derived" and res["scope"] == "store",
      str(res))
res = put(db, {"store_code": "B-1", "clear": True})
check("a single-store clear still reports the store and what it resolves to now",
      res["store_code"] == "B-1" and res["cleared"] is True and overrides(db) == [], str(res))

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_closing_source_multistore: ALL PASS")
