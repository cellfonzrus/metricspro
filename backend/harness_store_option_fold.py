"""Truth table: a store dropdown offers each PHYSICAL STORE exactly ONCE.

Owner, 2026-10-02, on the closed store B-2778 merged into its successor B-1598 — "no need to hide,
if they are merged it will show only one data as the store got replaced by the other". The money
merged (the resolver collapses every spelling onto one key); the PICKER did not. Measured live on
the house org that day: `GET /core/filter-options` offered **58 store options for 31 real stores**,
27 of them twice, because the endpoint unioned RAW SPELLINGS from the two vocabularies — a
`storeops.stores` row with no address contributed its bare CODE while the `commcalc.store_mapping`
row contributed the ADDRESS of the same store.

THE CLASS, not the instance: "which spellings are one store" is a fact with ONE home —
`build_market_index.code_groups`, whose own docstring already says a resolver treating two such
codes as two stores "makes a picker offer the same store twice (pick the wrong one and the grant
binds only half the data)". `GET /core/markets` dereferences it for the GRANT picker.
`GET /core/filter-options` did not. The registry existed and a caller was not wired to it — the
exact failure §19.18 records three times. This harness is the lock so it cannot un-wire again.

Pure stdlib + app.core.scope's PURE builders over the REAL composer. No DB, no network.
Run:  cd backend && python3 harness_store_option_fold.py
"""
import re
import sys

sys.path.insert(0, ".")
from app.core.scope import (build_market_index, build_store_options,  # noqa: E402
                            fold_store_spellings, merge_market_options)

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {msg}")
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def labels(opts):
    return [o["store"] for o in opts]


# ── Fixture: the live house shapes that produced the duplicates, structure preserved ────────────
# 1. B-2778: roster row with NO address (its code is all the picker had) + a mapping row whose
#    address is its successor's, post-merge. Two spellings, ONE store.
# 2. B-1598: the successor itself, mapping only.
# 3. B-60TH / B-1: two codes, one shared mapping address, plus a roster address spelled differently.
# 4. B-1115: address on BOTH sides, spelled identically — the ordinary case.
# 5. B-9000: an address recorded ONLY on the roster, market only there too.
# 6. a codeless roster row — a store with an address and no code at all.
STORE_ROWS = [
    {"store_code": "B-2778", "address": None, "market": "PA"},
    {"store_code": "B-60TH", "address": "1 S 60th St, Philadelphia", "market": "PA"},
    {"store_code": "B-1115", "address": "1115 Liberty Ave", "market": "LI"},
    {"store_code": "B-9000", "address": "9000 Roster Only Rd", "market": "LI"},
    {"store_code": None, "address": "77 Codeless Way", "market": "PA"},
]
MAPPING_ROWS = [
    {"store_code": "B-2778", "store_address": "1598 Mount Ephraim Ave", "market": "PA"},
    {"store_code": "B-1598", "store_address": "1598 Mount Ephraim Ave", "market": "PA"},
    {"store_code": "B-60TH", "store_address": "1 S 60th street", "market": "PA"},
    {"store_code": "B-1", "store_address": "1 S 60th street", "market": "PA"},
    {"store_code": "B-1115", "store_address": "1115 Liberty Ave", "market": "LI"},
]
ALIAS_ROWS = [{"store_code": "B-1598", "alias": "2778 Ephraim Ave"}]

IDX = build_market_index(STORE_ROWS, MAPPING_ROWS, ALIAS_ROWS)
OPTS = build_store_options(IDX)
LBL = labels(OPTS)


print("\n§A  REPRODUCE — the raw union is what offered every store twice")
RAW = {}
for r in STORE_ROWS:
    RAW[(r.get("address") or r.get("store_code") or "").strip()] = r.get("market")
for r in MAPPING_ROWS:
    RAW[(r.get("store_address") or "").strip()] = r.get("market")
RAW.pop("", None)
print(f"  raw spellings offered by the old fold: {len(RAW)} — {sorted(RAW)}")
ok("B-2778" in RAW and "1598 Mount Ephraim Ave" in RAW,
   "A1 the old fold offers the closed store's CODE *and* its successor's ADDRESS")
ok("1 S 60th St, Philadelphia" in RAW and "1 S 60th street" in RAW,
   "A2 the old fold offers one store under two address spellings")
ok(len(RAW) > len(OPTS), f"A3 the fold removes duplicates ({len(RAW)} raw -> {len(OPTS)} stores)")


print("\n§B  REPAIR — one option per physical store")
ok(len(OPTS) == 5, f"B1 five physical stores in the fixture, five options (got {len(OPTS)}: {LBL})")
ok("B-2778" not in LBL, "B2 the closed store is no longer a second option of its own")
ok(LBL.count("1598 Mount Ephraim Ave") == 1,
   "B3 the merged store appears exactly once, under its address")
merged = [o for o in OPTS if o["store"] == "1598 Mount Ephraim Ave"][0]
ok("B-2778" in merged["also_known_as"] and "B-1598" in merged["also_known_as"],
   "B4 both codes are kept visible on the option (also_known_as), never hidden")
ok(merged["market"] == "PA", "B5 the merged option keeps its market")
sixty = [o for o in OPTS if "60th" in o["store"]]
ok(len(sixty) == 1, f"B6 two codes sharing an address are ONE option (got {[o['store'] for o in sixty]})")
ok("1 S 60th street" in sixty[0]["also_known_as"],
   "B7 the other address spelling of that store is kept visible")
ok(LBL.count("1115 Liberty Ave") == 1, "B8 the ordinary both-sides store is offered once")
ok("9000 Roster Only Rd" in LBL, "B9 a roster-only store is still offered")
ok("77 Codeless Way" in LBL, "B10 a store with an address and NO code is still offered")


print("\n§C  DISPLAY — an address beats a bare code, deterministically")
ok(all(not re.fullmatch(r"[Bb]-\S+", o["store"]) for o in OPTS if o["also_known_as"]),
   "C1 no folded store is labelled with its bare code while an address exists")
ok(build_store_options(IDX) == OPTS, "C2 the composition is deterministic across calls")
shuffled = build_market_index(list(reversed(STORE_ROWS)), list(reversed(MAPPING_ROWS)), ALIAS_ROWS)
ok(labels(build_store_options(shuffled)) == LBL, "C3 row order does not change the labels")
ok(LBL == sorted(LBL, key=lambda s: (s.casefold(), s)), "C4 options are sorted case-insensitively")
codeless_only = build_store_options(build_market_index(
    [{"store_code": "B-X", "address": None, "market": "PA"}], [], []))
ok(labels(codeless_only) == ["B-X"],
   "C5 a store whose address nobody ever recorded is still offered, by its code")


print("\n§D  ADDITIVE — nothing a surface's own rows carry is dropped")
with_present = build_store_options(IDX, present=["999 Unmapped Blvd"])
ok("999 Unmapped Blvd" in labels(with_present),
   "D1 a spelling the index cannot bind survives verbatim as its own option")
ok(len(with_present) == len(OPTS) + 1, "D2 and adds exactly one option")
for spelling in ("B-2778", "1598 Mount Ephraim Ave", "2778 Ephraim Ave", "b2778", "B-1598"):
    ok(labels(build_store_options(IDX, present=[spelling])) == LBL,
       f"D3 a present spelling the index CAN bind adds nothing: {spelling!r}")
ok(labels(build_store_options(IDX, present=["", "   ", None])) == LBL,
   "D4 blanks are never options")
ok(labels(build_store_options(IDX, present=["1 S 60TH STREET"])) == LBL,
   "D5 present matching is case/punctuation-insensitive, like every other key match")


print("\n§E  FAIL-SOFT — an option list never blanks a working page")
ok(build_store_options(None) == [], "E1 no index -> no options, no exception")
ok(build_store_options({}) == [], "E2 empty index -> no options, no exception")
ok(labels(build_store_options({}, present=["Only Row Street"])) == ["Only Row Street"],
   "E3 with no index the surface's own rows are still offered")
ok(build_store_options(build_market_index([], [], [])) == [], "E4 an org with no stores has no options")


print("\n§F  NO COLLATERAL — the market twin and the index are untouched")
ok(IDX["markets"] == ["LI", "PA"], "F1 the market vocabulary is unchanged by the store fold")
ok(merge_market_options(IDX["markets"]) == ["LI", "PA"], "F2 the market composer is unchanged")
ok(sorted(IDX["code_groups"]["B-2778"]) == ["B-1598", "B-2778"],
   "F3 the ONE home of 'same physical store' is read, not copied")
ok({o["market"] for o in OPTS} <= set(IDX["markets"]) | {None},
   "F4 no option carries a market outside the canonical vocabulary")
conflict = build_market_index(
    [{"store_code": "B-A", "address": "5 Same St", "market": "PA"}],
    [{"store_code": "B-B", "store_address": "5 Same St", "market": "LI"}], [])
ok([o["market"] for o in build_store_options(conflict)] == [None],
   "F5 a group whose rows disagree on the market reports NO market, never a guess")


print("\n§G  LOCK — the caller stays wired to the one home")
SRC = open("app/modules/core/router.py").read()
fo = SRC[SRC.index("def filter_options("):]
fo = fo[:fo.index("\n@router.get")]
ok("org_store_options" in fo,
   "G1 /core/filter-options dereferences the canonical store composer")
ok(fo.index("org_store_options") < fo.index('return {"stores"'),
   "G2 and does so before it returns the option list")
SCOPE = open("app/core/scope.py").read()
ok("code_groups" in SCOPE[SCOPE.index("def build_store_options("):][:6000],
   "G3 the composer reads code_groups rather than re-deriving which spellings are one store")
ok("def org_store_options(" in SCOPE and "def org_market_options(" in SCOPE,
   "G4 the store composer lives beside its market twin, in the one scope home")


print("\n§H  THE CALLER-LIST FOLD — a surface keeps its own spelling, not its duplicates")
# payables prefers commcalc.store_mapping's spelling (measured: that is what its rows carry), so it
# folds its OWN ordered list rather than adopting the composer's label.
PAY = ["1 S 60th street", "1598 Mount Ephraim Ave", "1115 Liberty Ave",
       "1 S 60th St, Philadelphia", "B-2778"]
FOLDED = fold_store_spellings(IDX, PAY)
ok(FOLDED == ["1 S 60th street", "1598 Mount Ephraim Ave", "1115 Liberty Ave"],
   f"H1 one spelling per store, the caller's FIRST one kept (got {FOLDED})")
ok("1 S 60th street" in FOLDED and "1 S 60th St, Philadelphia" not in FOLDED,
   "H2 the caller's preferred vocabulary wins the label, not the composer's")
ok(fold_store_spellings(IDX, ["999 Unmapped Blvd", "1115 Liberty Ave"])
   == ["999 Unmapped Blvd", "1115 Liberty Ave"],
   "H3 a spelling the index cannot bind keeps its own slot, never folded away")
ok(fold_store_spellings(IDX, ["999 Unmapped Blvd", "999 unmapped blvd"]) == ["999 Unmapped Blvd"],
   "H4 two unbindable spellings of one string still collapse (case-insensitively)")
ok(fold_store_spellings(IDX, PAY) == FOLDED, "H5 deterministic")
ok(fold_store_spellings(IDX, ["", "  ", None, "1115 Liberty Ave"]) == ["1115 Liberty Ave"],
   "H6 blanks are dropped")
ok(fold_store_spellings(None, ["a", "b"]) == ["a", "b"], "H7 no index -> the list is returned intact")
ok(fold_store_spellings(IDX, []) == [] and fold_store_spellings(IDX, None) == [],
   "H8 an empty list folds to an empty list")
ok([o["store"] for o in OPTS] == labels(OPTS) and
   len(fold_store_spellings(IDX, labels(OPTS))) == len(OPTS),
   "H9 the composer's own output is already folded (the two agree)")

PAYSRC = open("app/modules/payables/router.py").read()
pfo = PAYSRC[PAYSRC.index("def payables_filter_options("):]
pfo = pfo[:pfo.index("\n@router.get")]
ok("fold_store_spellings" in pfo, "H10 payables /filter-options dereferences the one home too")
ok('return {"stores": stores' in pfo and
   pfo.index("fold_store_spellings") < pfo.index('return {"stores": stores'),
   "H11 and does so before it returns its options")


print(f"\n{'='*70}\n  {PASS} passed, {FAIL} failed\n{'='*70}")
sys.exit(1 if FAIL else 0)
