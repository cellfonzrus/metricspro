"""Proof harness: THE canonical store→market resolver (core.scope.build_store_market_lookup /
build_market_by_code) — owner directive 2026-09-03 ("1115 Liberty Ave … assigned LI … does not show
up under any filter in the rep incentive report but shows in the daily target report … fix once for
all").

ROOT CAUSE PINNED HERE (§A is the live shape, verified 2026-09-03 against Cellfonz R Us
org 00000000-…-0001): storeops.stores has B-1115 / "1115 Liberty Ave" / market "LI"; commcalc.
store_mapping has NO row for it; rep_commissions rows carry store="1115 Liberty Ave". The Rep
Incentive report stamped market via a store_mapping-ONLY resolver → '' → invisible under every
market filter, while Daily Targets (storeops-sourced) showed the store fine. The canonical resolver
unions BOTH vocabularies + store_aliases, so the same store answers "LI" from any spelling.

Pure stdlib, no DB, no network.  Run:  cd backend && python3 harness_store_market_resolution.py
Exit 0 = the canonical resolution semantics hold; exit 1 = a truth-table row regressed.
"""
import sys

sys.path.insert(0, ".")
from app.core.scope import build_market_index, build_store_market_lookup, build_market_by_code  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def idx(store_rows=(), mapping_rows=(), alias_rows=()):
    return build_market_index(list(store_rows), list(mapping_rows), list(alias_rows))


# ── §A THE 1115-LIBERTY / LI SHAPE: market exists in storeops.stores ONLY ────────────────────────
print("§A market in storeops only (the live Cellfonz B-1115 bug shape)")
i = idx(
    store_rows=[{"store_code": "B-1115", "address": "1115 Liberty Ave", "market": "LI"},
                {"store_code": "B-418", "address": None, "market": "LI"}],
    mapping_rows=[{"store_code": "B-200", "store_address": "200 Main St", "market": "NYC"}],
)
r, markets = build_store_market_lookup(i)
ok(r("1115 Liberty Ave") == "LI", "exact address (storeops spelling) resolves LI")
ok(r("b-1115") == "LI", "store_code, case-insensitive, resolves LI")
ok(r("1115 Liberty Ave Brooklyn, NY 11208") == "LI",
   "sales-file spelling resolves via unambiguous leading street number")
ok(r("B-418") == "LI", "address-less storeops row still resolves by code")
ok(set(markets) == {"LI", "NYC"}, "markets list is the UNION of both vocabularies")
ok(build_market_by_code(i).get("B-1115") == "LI", "market_by_code carries the storeops-only market")

# ── §B THE MIRROR IMAGE: market exists in store_mapping ONLY ─────────────────────────────────────
print("§B market in store_mapping only (the closing/Daily-Targets-side gap)")
i = idx(
    store_rows=[{"store_code": "B-77", "address": "77 Elm St", "market": None}],
    mapping_rows=[{"store_code": "B-77", "store_address": "77 Elm Street", "market": "PA"}],
)
r, _ = build_store_market_lookup(i)
ok(r("B-77") == "PA", "code resolves the store_mapping-side market")
ok(r("77 Elm St") == "PA", "storeops address spelling resolves it")
ok(r("77 Elm Street") == "PA", "store_mapping address spelling resolves it")
ok(build_market_by_code(i).get("B-77") == "PA", "market_by_code fills the storeops blank")

# ── §C ALIASES (POS synonyms) resolve, but never invent stores/markets ───────────────────────────
print("§C store_aliases synonyms")
i = idx(
    store_rows=[{"store_code": "KEDZIE", "address": "3210 W Kedzie Ave", "market": "Chicago"}],
    alias_rows=[{"alias": "Kedzie Plaza Store #2", "store_code": "KEDZIE"},
                {"alias": "ghost synonym", "store_code": "NO-SUCH-CODE"}],
)
r, markets = build_store_market_lookup(i)
ok(r("kedzie plaza store #2") == "Chicago", "explicit POS synonym resolves the store's market")
ok(markets == ["Chicago"], "an alias for an unknown code invents no market")

# ── §D FAIL-CLOSED on ambiguity — never an arbitrary winner ──────────────────────────────────────
print("§D ambiguity fails closed")
i = idx(
    store_rows=[{"store_code": "A-1", "address": "500 Oak St", "market": "East"},
                {"store_code": "A-2", "address": "500 Oak Ave", "market": "West"}],
)
r, _ = build_store_market_lookup(i)
ok(r("500 Oak St") == "East", "each exact spelling still resolves its own market")
ok(r("500 Oak Ave") == "West", "each exact spelling still resolves its own market (2)")
ok(r("500 Oak Blvd") == "", "a duplicated leading number across two markets resolves NOTHING "
                            "(the legacy first-row-wins tiebreak is gone)")
# two CODES sharing one physical address but disagreeing on market → that spelling is ambiguous
i = idx(
    store_rows=[{"store_code": "D1", "address": "9 Pine St", "market": "North"}],
    mapping_rows=[{"store_code": "LUX-D1", "store_address": "9 Pine St", "market": "South"}],
)
r, _ = build_store_market_lookup(i)
ok(r("unknown store") == "", "an unknown spelling resolves ''")

# ── §E FOLD ORDER: storeops wins a per-store disagreement (the documented index fold) ────────────
print("§E per-store fold: first non-empty wins, storeops read first")
i = idx(
    store_rows=[{"store_code": "F-9", "address": "12 Lake St", "market": "Metro"}],
    mapping_rows=[{"store_code": "F-9", "store_address": "12 Lake St", "market": ""}],
)
r, _ = build_store_market_lookup(i)
ok(r("F-9") == "Metro", "blank mapping-side market never blanks the storeops market")
ok(build_market_by_code(i)["F-9"] == "Metro", "market_by_code agrees")

# ── §F GROUP INHERITANCE: two codes, one physical store (the Luxelink shape) ─────────────────────
print("§F code_groups inheritance")
i = idx(
    store_rows=[{"store_code": "Diversey", "address": "2812 Diversey", "market": "Chicago"}],
    mapping_rows=[{"store_code": "LUX-CHI-DIVERSEY", "store_address": "2812 Diversey", "market": ""}],
)
mbc = build_market_by_code(i)
ok(mbc.get("LUX-CHI-DIVERSEY") == "Chicago",
   "a marketless twin code inherits its physical store's one market")
r, _ = build_store_market_lookup(i)
ok(r("lux-chi-diversey") == "Chicago", "…and resolves through the lookup too")

# ── §G EMPTY / DEGENERATE inputs ─────────────────────────────────────────────────────────────────
print("§G degenerate inputs")
r, markets = build_store_market_lookup(idx())
ok(r("anything") == "" and markets == [], "empty index resolves nothing, offers nothing")
r, _ = build_store_market_lookup(None)
ok(r("x") == "", "None index degrades to the empty resolver")
ok(build_market_by_code(None) == {}, "None index → empty code map")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
