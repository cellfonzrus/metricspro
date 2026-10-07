#!/usr/bin/env python3
"""DB-free proof of the ONE payment-type → pay-category home, and of the ledger's category filter.

Proves the three things a human depends on when they ask "is this line commission or a rebate":
  A. the fold — one key rule, so the carrier's inconsistent spelling cannot hide a mapping
  B. absence stays absence — an unmapped type is never a category, in the data or in the filter
  C. the filter and the option list behave, including the "(not classified)" pick

Stdlib only. Imports the REAL modules, never a copy of their logic.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import payment_category as pc          # noqa: E402
from app.modules.commcalc import processor_ledger as pl           # noqa: E402
from app.modules.commcalc.pay_data_quality import (               # noqa: E402
    PLACEABLE_CATEGORIES, UNCATEGORISED)

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}   {detail}")


# The live house shape, trimmed: the map is keyed by the tenant's own descriptions, and the feed
# spells the same type with different case and stray whitespace from month to month.
LIVE_MAP_ROWS = [
    {"description": "Boost Auto Top-Up", "category": "Commission"},
    {"description": "2024 Q3 Promo PIC Offer", "category": "Re-imbursement"},
    {"description": "Ramp Up Subsidy", "category": "MDF"},
    {"description": "Something The Tenant Invented", "category": "Store Credit"},
    {"description": "Mapped But Blank", "category": ""},
    {"description": "   ", "category": "Commission"},
]


class _FakeClient:
    """The thinnest stand-in for the supabase client `load_map` uses. No network, no DB."""

    def __init__(self, rows, raise_on_read=False):
        self._rows, self._raise = rows, raise_on_read

    def schema(self, _s):
        return self

    def table(self, _t):
        return self

    def select(self, _c):
        return self

    def eq(self, _k, _v):
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("transient read failure")
        return type("R", (), {"data": self._rows})()


print("\nA. one folding rule for the payment-type key")
cmap = pc.load_map(_FakeClient(LIVE_MAP_ROWS), "org")
ok(pc.category_of(cmap, "Boost Auto Top-Up") == "Commission", "the exact spelling resolves")
ok(pc.category_of(cmap, "  boost auto top-up  ") == "Commission",
   "case and surrounding whitespace do not change the answer")
ok(pc.category_of(cmap, "Boost  Auto   Top-Up") == "Commission",
   "collapsed inner whitespace resolves too (the feed double-spaces)")
ok(pc.category_of(cmap, "") is None, "an empty type is not a lookup")
ok("   " not in cmap and len(cmap) == 5, "a blank description is not a mapping", f"map={cmap}")

print("\nB. absence is reported, never converted into a category")
ok(pc.category_of(cmap, "2026 Q3 Promo PIC Offer") is None,
   "the live defect: a type the org has NOT mapped is None, even with a prior-year twin mapped")
ok(pc.category_of(cmap, "Mapped But Blank") is None,
   "a mapping that names no category has categorised nothing")
ok(pc.label_of(cmap, "2026 Q3 Promo PIC Offer") == UNCATEGORISED,
   "the human label is the SHARED sentinel, not a second word for one state")
ok(pc.load_map(_FakeClient([], raise_on_read=True), "org") == {},
   "a failed read degrades to 'nothing declared', never to a wrong category")
ok(pc.category_of(pc.load_map(_FakeClient([], raise_on_read=True), "org"), "Boost Auto Top-Up") is None,
   "...and every type then reads as unmapped rather than as Commission")

print("\nC. the option list is the tenant's, not the house's")
ok(pc.declared_categories(cmap) == ["Commission", "MDF", "Re-imbursement", "Store Credit"],
   "every declared category is offered, including one the house has never heard of (RULE TWO)",
   f"got={pc.declared_categories(cmap)}")
ok(UNCATEGORISED not in pc.declared_categories(cmap),
   "the unmapped sentinel is NOT a category in the list")
ok(pc.placeable_categories() == tuple(PLACEABLE_CATEGORIES),
   "the placeable set is dereferenced from its one home, not restated")
ok("Store Credit" not in pc.placeable_categories(),
   "a declared category the pay engine cannot place is still offered but is not placeable")

print("\nD. the ledger's category filter")
CELLS = [
    {"date": "2026-10-01", "tx_type": "Boost Auto Top-Up", "category": "Commission",
     "store_code": "B-1", "store": "1 Main", "market": "LI", "debits": 0.0, "credits": 100.0, "rows": 2},
    {"date": "2026-10-01", "tx_type": "2026 Q3 Promo PIC Offer", "category": "",
     "store_code": "B-1", "store": "1 Main", "market": "LI", "debits": 0.0, "credits": 500.0, "rows": 3},
    {"date": "2026-10-02", "tx_type": "Commission Withholding", "category": "Commission",
     "store_code": "B-2", "store": "2 Oak", "market": "NY", "debits": 40.0, "credits": 0.0, "rows": 1},
    {"date": "2026-10-02", "tx_type": "2026 SIM card reimbursement", "category": "Re-imbursement",
     "store_code": "B-2", "store": "2 Oak", "market": "NY", "debits": 0.0, "credits": 7.0, "rows": 1},
]
ok(len(pl.filter_cells(CELLS)) == 4, "no filter keeps everything, unclassified included")
ok({c["tx_type"] for c in pl.filter_cells(CELLS, categories=["Commission"])}
   == {"Boost Auto Top-Up", "Commission Withholding"}, "one category narrows to it")
ok({c["tx_type"] for c in pl.filter_cells(CELLS, categories=["commission"])}
   == {"Boost Auto Top-Up", "Commission Withholding"}, "the pick is case-insensitive")
ok([c["tx_type"] for c in pl.filter_cells(CELLS, categories=[pl.NO_CATEGORY_ID])]
   == ["2026 Q3 Promo PIC Offer"],
   "THE OWNER'S ASK: the unclassified money is reachable by an explicit pick")
ok(not any(c["category"] == "" for c in pl.filter_cells(CELLS, categories=["Commission"])),
   "a category-less cell never answers to a real category")
ok({c["tx_type"] for c in pl.filter_cells(CELLS, categories=["Commission", "Re-imbursement"])}
   == {"Boost Auto Top-Up", "Commission Withholding", "2026 SIM card reimbursement"},
   "two picks union")
ok([c["tx_type"] for c in pl.filter_cells(CELLS, categories=["Commission"], markets=["NY"])]
   == ["Commission Withholding"], "category AND-composes with the other filters")
ok(pl.filter_cells(CELLS, categories=["Nothing Declares This"]) == [],
   "a category nothing carries yields nothing, rather than everything")

print("\nE. the fold carries the category without widening the key")
events = [
    {"processor": "epay", "date": "2026-10-01", "tx_type": "Boost Auto Top-Up", "store_code": "B-1",
     "store": "1 Main", "market": "LI", "category": "Commission", "debit": 0.0, "credit": 60.0},
    {"processor": "epay", "date": "2026-10-01", "tx_type": "Boost Auto Top-Up", "store_code": "B-1",
     "store": "1 Main", "market": "LI", "category": "Commission", "debit": 0.0, "credit": 40.0},
    {"processor": "epay", "date": "2026-10-01", "tx_type": "2026 Q3 Promo PIC Offer",
     "store_code": "B-1", "store": "1 Main", "market": "LI", "category": "", "debit": 0.0,
     "credit": 500.0},
]
folded = pl.fold_cells(events)
ok(len(folded) == 2, "two types fold to two cells", f"got {len(folded)}")
byt = {c["tx_type"]: c for c in folded}
ok(byt["Boost Auto Top-Up"]["credits"] == 100.0 and byt["Boost Auto Top-Up"]["rows"] == 2,
   "same type, same category folds and sums")
ok(byt["Boost Auto Top-Up"]["category"] == "Commission", "the category survives the fold")
ok(byt["2026 Q3 Promo PIC Offer"]["category"] == "",
   "and an unmapped type folds with '' — not with the sentinel string in the data")
ok(sum(c["credits"] for c in folded) == 600.0,
   "totals are unchanged by the new column (the money still ties: 60 + 40 + 500)",
   f"got {sum(c['credits'] for c in folded)}")

print("\nF. negative controls — the checks above would catch a regression")
ok(pc.category_of({"a": "X"}, "b") is None, "a lookup miss is not the first value in the map")
broken = dict(cmap)
broken["2026 q3 promo pic offer"] = "Re-imbursement"
ok(pc.category_of(broken, "2026 Q3 Promo PIC Offer") == "Re-imbursement",
   "...and once the owner DOES map it, it resolves with no code change")
ok(pl.filter_cells([{"tx_type": "x", "category": "Commission", "debits": 0, "credits": 1,
                     "store_code": "", "store": "", "market": ""}],
                   categories=[pl.NO_CATEGORY_ID]) == [],
   "a classified cell does not answer to the unclassified pick")

print("\n" + "=" * 94)
print(f"{PASS} passed, {FAIL} failed")
if FAIL:
    print("One payment-category home — BROKEN")
    sys.exit(1)
print("OK — one payment-category home; an unmapped type is reported, never guessed, and is "
      "filterable on its own.")
