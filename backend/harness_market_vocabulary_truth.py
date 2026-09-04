"""Truth table: the B-1115/LI market-vocabulary shape can never go invisible again.

Owner directive 2026-09-04 (verbatim): "B-1115 is under super nova and LI market under Cellfonz R
us, that has been missing from a lot of reports when market is chosen, this needs to be fixed as a
design not a band aid as this could happen to a new store also, first find the root cause then fix
and put precaution in place."

THE SHAPE (live house rows, 2026-09-04): store B-1115 "1115 Liberty Ave" carries market "LI" ONLY
on storeops.stores — NO commcalc.store_mapping row, NO store_aliases row. §13a (2026-09-03) made
store→market RESOLUTION canonical; this harness is the §13c half — ENUMERATION: a market recorded
on only one vocabulary (either side), or typed onto a brand-new store, must
  (1) appear in the canonical vocabulary (build_market_index / canonical markets list),
  (2) appear in every OPTION list composed through merge_market_options (the §13c composition),
  (3) resolve its store under the filter (build_store_market_lookup / build_market_by_code), and
  (4) bind as a market GRANT keyset member (by_market codes) — so an admin sees it with zero setup
      and a scoped manager sees it the moment the market is granted.

Pure stdlib + app.core.scope's PURE builders. No DB, no network.
Run:  cd backend && python3 harness_market_vocabulary_truth.py
"""
import sys

sys.path.insert(0, ".")
from app.core.scope import (build_market_index, build_store_market_lookup,   # noqa: E402
                            build_market_by_code, merge_market_options)

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {msg}")
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


# ── Fixture: the live house shape, anonymized to the structure ───────────────────────────────────
# B-1115: storeops-ONLY store with market LI (no mapping row, no alias).
# B-418:  present in BOTH vocabularies, market LI on the mapping side only.
# B-509:  storeops row, market NYC; mapping row with the same code, blank market.
# PAONLY: market "PA" exists ONLY in commcalc.store_mapping (the mirror image of the LI shape).
# NEWSTORE: a brand-new storeops row typed with a brand-new market "CT" — nothing else knows CT.
STORE_ROWS = [
    {"store_code": "B-1115", "address": "1115 Liberty Ave", "market": "LI"},
    {"store_code": "B-418", "address": None, "market": None},
    {"store_code": "B-509", "address": "509 Main St", "market": "NYC"},
    {"store_code": "B-NEW", "address": "77 Fresh Blvd", "market": "CT"},
]
MAPPING_ROWS = [
    {"store_code": "B-418", "store_address": "418 Uniondale Ave", "market": "LI"},
    {"store_code": "B-509", "store_address": "509 Main Street", "market": None},
    {"store_code": "B-PA1", "store_address": "2778 Ephraim Ave", "market": "PA"},
]

idx = build_market_index(STORE_ROWS, MAPPING_ROWS, [])
resolve, markets = build_store_market_lookup(idx)
by_code = build_market_by_code(idx)

print("1) VOCABULARY — every market from EITHER vocabulary, including brand-new, is enumerated")
ok(set(markets) == {"LI", "NYC", "PA", "CT"},
   f"canonical vocabulary is the union of both sides: {markets}")
ok("LI" in markets, "storeops-ONLY market (LI, the B-1115 shape) is in the vocabulary")
ok("PA" in markets, "store_mapping-ONLY market (PA, the mirror shape) is in the vocabulary")
ok("CT" in markets, "a market typed on a brand-new store is in the vocabulary immediately")

print("2) OPTIONS — merge_market_options: every dropdown is a superset of the vocabulary")
opts = merge_market_options(markets, [])
ok(opts == sorted(["CT", "LI", "NYC", "PA"], key=str.casefold),
   f"empty data still offers the full vocabulary: {opts}")
opts = merge_market_options(markets, ["li", "Orphan Stamp"])
ok("LI" in opts and "li" not in opts,
   "case drift in a data stamp collapses to the CANONICAL spelling (never 'LI' and 'li')")
ok("Orphan Stamp" in opts,
   "a stamp the vocabulary does not know stays selectable (labels real rows), never dropped")
ok(merge_market_options([], ["OnlyData"]) == ["OnlyData"],
   "no vocabulary at all degrades to data-present (options never blank a working page)")
ok(merge_market_options(["A", "a", " A "], []) == ["A"],
   "vocabulary-side duplicates collapse to one option")

print("3) FILTER — the enumerated market actually returns its stores (options ≡ resolvable)")
ok(resolve("1115 Liberty Ave") == "LI", "B-1115 by its storeops address resolves to LI")
ok(resolve("B-1115") == "LI", "B-1115 by store code resolves to LI")
ok(resolve("1115 Liberty Ave Brooklyn, NY 11208") == "LI",
   "the sales-feed spelling (leading street number) resolves to LI — the row the filter must keep")
ok(resolve("2778 Ephraim Ave") == "PA", "the mapping-only market's store resolves (mirror shape)")
ok(resolve("77 Fresh Blvd") == "CT", "the brand-new store resolves its brand-new market")
ok(by_code.get("B-1115") == "LI", "market_by_code carries the storeops-only store")
for mk in markets:
    b = (idx.get("by_market") or {}).get(mk.lower()) or {}
    ok(bool(b.get("codes")), f"every enumerated market binds at least one store code ({mk})")

print("4) GRANT/SPAN — the market grant keyset includes the single-vocabulary store")
li = (idx.get("by_market") or {}).get("li") or {}
ok("B-1115" in (li.get("codes") or set()),
   "a manager granted market LI spans B-1115 (grant machinery reads the same union)")
pa = (idx.get("by_market") or {}).get("pa") or {}
ok("B-PA1" in (pa.get("codes") or set()), "mirror shape: PA grant spans the mapping-only store")

print("5) FAIL-CLOSED — ambiguity never guesses")
amb = build_market_index(
    [{"store_code": "X1", "address": "9 Dual Rd", "market": "East"}],
    [{"store_code": "X2", "store_address": "9 Dual Rd", "market": "West"}], [])
r2, _ = build_store_market_lookup(amb)
ok(r2("9 Dual Rd") == "", "a spelling claimed by two markets resolves to '' (never a guess)")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
