"""Proof harness — WHERE a store's daily closing comes from (owner 2026-10-02, mig 1035, index §19.39).

Owner: *"the admin should be able to check a box to input daily closing by sales reps for all stores or
pull b2b data from directly into daily closing in case the tenant does not want to have people submit
daily closing, so it is derived via the permission selected at the time of setting up the store - it
could be changed later at any time by the tenant admin, all other features like cash pick up etc will
stay as they are a following action / reports after the data gets populated."*

Proves, stdlib-only and DB-free:
  A. normalize / the vocabulary: two values, unknown folds to the house default, no third value.
  B. resolve: house default → org default → per-store override, case-insensitive codes, and the
     PRE-MIGRATION case (no rows at all) resolving to rep entry for every store — the byte-identical
     claim for every tenant that never opens the screen.
  C. expects_rep_submission / is_derived: the question the four dereferencing callers actually ask.
  D. derivable: the two REPORTED refusals — no feed, and sales with no cash/card split — so a missing
     feed is never written as a clean $0 close.
  E. derive_row: the tender columns match what the REP path writes for the same money; counts come
     from the shared activation classifier's output; no fabricated employee; the feed's unclassified
     'other' bucket is RECORDED, not folded into a tender it is not.
  F. changed_fields: an unchanged feed re-run is a no-op; a moved dollar or count is detected.
  G. plan_day: the whole day's decision — derive / skip-with-reason / leave to the reps — and the
     invariant that every store lands in exactly one bucket.

Run: python3 backend/harness_closing_source.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

from app.modules.closing import closing_source as cs  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


ORG = "00000000-0000-0000-0000-000000000001"

print("A. the vocabulary")
check("two sources and no more", cs.SOURCES == ("rep_entry", "b2b_derived"))
check("house default is existing manual rep entry", cs.HOUSE_DEFAULT == "rep_entry")
check("normalize accepts both", cs.normalize("rep_entry") == "rep_entry"
      and cs.normalize(" B2B_DERIVED ") == "b2b_derived")
check("unknown / blank / None fold to the house default",
      all(cs.normalize(v) == "rep_entry" for v in (None, "", "   ", "auto", "pos", 7, {})))
check("every source has a label", all(k in cs.LABELS for k in cs.SOURCES))

print("B. resolve — override, then org default, then house")
check("no rows at all (pre-migration) → rep entry everywhere",
      cs.resolve([], None) == "rep_entry" and cs.resolve(None, "B-1") == "rep_entry")
org_derived = [{"store_code": None, "source": "b2b_derived"}]
check("org default alone applies to every store",
      cs.resolve(org_derived, "B-1") == "b2b_derived" and cs.resolve(org_derived, None) == "b2b_derived")
mixed = [{"store_code": None, "source": "b2b_derived"},
         {"store_code": "B-1", "source": "rep_entry"}]
check("a per-store override beats the org default", cs.resolve(mixed, "B-1") == "rep_entry")
check("a store with no override follows the org default", cs.resolve(mixed, "B-9") == "b2b_derived")
check("store codes match case-insensitively (the schema's posture everywhere)",
      cs.resolve(mixed, "b-1") == "rep_entry")
check("a blank-string store_code row IS the org default row (not a store named '')",
      cs.resolve([{"store_code": "  ", "source": "b2b_derived"}], "B-7") == "b2b_derived")
check("a garbage stored source cannot decide how a store closes its books",
      cs.resolve([{"store_code": "B-1", "source": "whatever"}], "B-1") == "rep_entry")
check("source_map is resolve, batched",
      cs.source_map(mixed, ["B-1", "B-9"]) == {"B-1": "rep_entry", "B-9": "b2b_derived"})
check("source_map drops blank codes", cs.source_map(mixed, ["", None, "B-9"]) == {"B-9": "b2b_derived"})

print("C. the question the callers ask")
check("rep entry expects a submission", cs.expects_rep_submission("rep_entry") is True
      and cs.is_derived("rep_entry") is False)
check("derived expects none", cs.expects_rep_submission("b2b_derived") is False
      and cs.is_derived("b2b_derived") is True)
check("an unknown value is treated as rep entry, never as 'nobody submits'",
      cs.expects_rep_submission("garbage") is True)
check("the refusal names the store and where to change it",
      "B-1" in cs.refusal_message("B-1") and "Store Setup" in cs.refusal_message("B-1"))
check("the refusal survives a missing store code", "This store" in cs.refusal_message(None))

print("D. derivable — the two refusals are reported, never written as zero")
check("no feed rows for the store", cs.derivable({"total": 0.0}) == (False, "no_feed"))
check("no store entry at all", cs.derivable(None) == (False, "no_feed"))
check("sales with every line in the feed's unclassified bucket",
      cs.derivable({"total": 500.0, "cash": 0.0, "card": 0.0, "other": 500.0,
                    "tenders_available": False}) == (False, "no_tender_split"))
check("a real day with a tender split is derivable",
      cs.derivable({"total": 500.0, "cash": 300.0, "card": 200.0,
                    "tenders_available": True}) == (True, ""))

print("E. derive_row — the rep path's own columns, and no invention")
b2b = {"cash": 1234.567, "card": 765.431, "other": 50.0, "acc_gross": 99.99, "total": 2050.0,
       "tenders_available": True}
row = cs.derive_row(ORG, "B-1", "2026-10-01", b2b, {"activations": 4, "upgrades": 3},
                    {"store_address": "1 Main St", "sfid": "SF1"}, now_iso="2026-10-02T06:00:00Z")
check("org / period / date / store keyed as the rep path keys them",
      row["org_id"] == ORG and row["period"] == "2026-10" and row["close_date"] == "2026-10-01"
      and row["store_code"] == "B-1")
check("cash and card are the feed's, rounded to cents",
      row["t_cash"] == 1234.57 and row["t_credit"] == 765.43)
check("the tenders the feed does not carry stay zero — never guessed",
      all(row[k] == 0.0 for k in ("t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima")))
check("legacy mirror columns use the rep path's formulas",
      row["store_cash"] == 1234.57 and row["store_cc"] == 765.43
      and row["epay_cash"] == 0.0 and row["epay_cc"] == 0.0 and row["other_account"] == 0.0)
check("the feed's unclassified bucket is RECORDED, not folded into a tender",
      row["derived_other"] == 50.0
      and row["t_cash"] + row["t_credit"] == 2000.0)   # 50.00 'other' is NOT in the tenders
check("counts come from the shared classifier's activations / upgrades",
      row["new_line_count"] == 4 and row["upgrade_count"] == 3 and row["postpaid_count"] == 0)
check("accessory sale carried", row["acc_sale"] == 99.99)
check("NO fabricated submitter — the envelope report and coaching must not gain a phantom person",
      row["employee_name"] is None)
check("marked as derived, distinguishably from 'manual' and 'sheet_upload'",
      row["source"] == "b2b_derived" and row["source"] == cs.DERIVED_ROW_SOURCE
      and row["derived_at"] == "2026-10-02T06:00:00Z")
check("the 3-try close gate's fields are absent — there is no human count to reconcile",
      not any(k in row for k in ("attempts", "auto_accepted", "mgmt_flag")))
check("no expense is invented", row["expense_amount"] == 0.0 and row["expense_description"] is None
      and row["expense_approved"] is False)
check("store meta rides along when known", row["store_address"] == "1 Main St" and row["sfid"] == "SF1")
row_nometa = cs.derive_row(ORG, "B-2", "2026-10-01", b2b, None, None, now_iso="t")
check("missing meta / counts degrade to None / 0, never crash",
      row_nometa["store_address"] is None and row_nometa["sfid"] is None
      and row_nometa["new_line_count"] == 0 and row_nometa["upgrade_count"] == 0)

print("F. changed_fields — a re-run over an unchanged feed writes nothing")
stored = dict(row)
check("identical feed → no change", cs.changed_fields(stored, row) == [])
stored_eq = dict(row, submitted_at="whenever", derived_at="later", employee_name="x")
check("the bookkeeping fields are not 'changes' (they would update every night)",
      cs.changed_fields(stored_eq, row) == [])
moved = dict(stored, t_cash=1234.58)
check("one cent of cash is a change", cs.changed_fields(moved, row) == ["t_cash"])
check("a count moving is a change", "upgrade_count" in cs.changed_fields(dict(stored, upgrade_count=9), row))
check("money compares at 2dp, so float noise is not a change",
      cs.changed_fields(dict(stored, t_cash=1234.5700000001), row) == [])
check("a row that never had the column (pre-migration) reads as a change to fill it",
      "derived_other" in cs.changed_fields({k: v for k, v in stored.items() if k != "derived_other"}, row))
check("an unparseable stored value is reported as a change rather than silently kept",
      "t_cash" in cs.changed_fields(dict(stored, t_cash="n/a"), row))

print("G. plan_day — the whole day's decision")
cfg = [{"store_code": None, "source": "b2b_derived"},
       {"store_code": "B-REP", "source": "rep_entry"}]
codes = ["B-OK", "B-REP", "B-NOFEED", "B-NOSPLIT"]
by_store = {"B-OK": {"total": 100.0, "cash": 60.0, "card": 40.0, "tenders_available": True},
            "B-REP": {"total": 90.0, "cash": 90.0, "card": 0.0, "tenders_available": True},
            "B-NOSPLIT": {"total": 70.0, "cash": 0.0, "card": 0.0, "other": 70.0,
                          "tenders_available": False}}
plan = cs.plan_day(cfg, codes, by_store, {"B-OK": {"activations": 1, "upgrades": 0}})
check("the derivable derived store is derived", plan["derive"] == ["B-OK"])
check("the rep-entry store is left to its reps even though the feed HAS its money",
      plan["rep_submits"] == ["B-REP"])
check("both refusals are reported with their reason",
      sorted(plan["skipped"], key=lambda x: x["store_code"]) ==
      [{"store_code": "B-NOFEED", "reason": "no_feed"},
       {"store_code": "B-NOSPLIT", "reason": "no_tender_split"}])
check("every store lands in exactly one bucket (no store silently vanishes)",
      len(plan["derive"]) + len(plan["rep_submits"]) + len(plan["skipped"]) == len(codes)
      and set(plan["derive"]) | set(plan["rep_submits"]) | {s["store_code"] for s in plan["skipped"]} == set(codes))
plan_house = cs.plan_day([], codes, by_store, {})
check("THE BYTE-IDENTICAL CLAIM: with no config, plan_day derives NOTHING and leaves every store "
      "to its reps", plan_house["derive"] == [] and plan_house["skipped"] == []
      and plan_house["rep_submits"] == codes)

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_closing_source: ALL PASS")
