"""Truth table: the distributor-invoice report's standard filters — one selector, one vocabulary.

Owner request 2026-10-03: *"add date range and market with standard filters for distributor
invoices"*. Two things could go wrong, and both have gone wrong before in this repo:

1. **The tiles and the table answer different questions.** `/vip/summary` (totals, fees-by-type,
   fees-by-store) and `/vip/invoices` (the table + export) used to carry two separately-written
   PostgREST filter chains. A new condition added to one renders a table whose rows do not add up
   to the tiles above them, confidently. So there is ONE selector and both endpoints read it, and
   §A/§D pin that.

2. **A market filter re-derives what a store spelling means.** `commcalc.vip_invoices.location` is
   a store address in the DISTRIBUTOR's own spelling and the table has no market column, so the
   market half has to be resolved through the org's store vocabulary. That vocabulary has ONE home
   (`core.scope.market_index`, read via `account.statement_filter.resolve_store_matcher` — the same
   one the P&L filter reads). §D fails the build if this module grows a sibling matcher or the
   router stops dereferencing that one.

§C pins the regression the first live run of this code produced: `SUMMARY_COLS` did not fetch
`created_on`, and because `in_date_window` is fail-closed on a dateless row, EVERY date window
returned zero invoices — a confident, empty, wrong answer.

§E pins `statement_filter.unbound_spellings`, added with this work. Feeding a feed's raw vocabulary
straight to `build_store_options(present=…)` re-offers stores it already lists, because that
composer tests bindability with `scope._squash` while the MATCHER uses `coa._squash_key` (which
folds street-suffix drift: "Ave" vs "Avenue"). Measured live on the house org 2026-10-03: the
picker grew from 33 to 35 options that way, re-introducing the §13e "one store offered twice"
defect through the back door. `unbound_spellings` asks the matcher's own vocabulary instead.

Pure stdlib over the REAL modules. No DB, no network.
Run:  cd backend && python3 harness_vip_invoice_filter.py
"""
import sys

sys.path.insert(0, ".")
from app.core.scope import build_market_index, build_store_options            # noqa: E402
from app.modules.account import statement_filter as sf                        # noqa: E402
from app.modules.commcalc import vip_invoice_filter as vf                     # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {msg}")
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def inv(loc, day, sub=100.0, grand=110.0, ship=10.0):
    """One invoice row in the shape `_vip_fetch` returns (created_on is a timestamptz)."""
    return {"location": loc, "created_on": (f"{day}T08:49:34+00:00" if day else None),
            "sub_total": sub, "grand_total": grand, "shipping": ship,
            "discount": 0.0, "other_cost": 0.0, "other_deductions": 0.0, "tax": 0.0}


# ── §A  summarize: the arithmetic is unchanged, and the three panels agree ──────────────────────
print("\n§A  summarize — totals, fees-by-type and fees-by-store over one row set")
ROWS = [
    inv("1 S 60th St", "2026-06-03", 100.0, 110.0, 10.0),
    inv("1 S 60th St", "2026-06-10", 200.0, 215.0, 15.0),
    inv("5619 N Broad St", "2026-07-01", 50.0, 55.0, 5.0),
]
S = vf.summarize(ROWS)
ok(S["totals"]["invoices"] == 3, "A1 invoice count")
ok(round(S["totals"]["sub_total"], 2) == 350.00, "A2 subtotal sums")
ok(round(S["totals"]["grand_total"], 2) == 380.00, "A3 grand total sums")
ok(round(S["totals"]["fees_total"], 2) == 30.00, "A4 fees_total is the sum of the fee buckets")
ok(S["fees_by_type"] == {c: S["totals"][c] for c in vf.FEE_COLS},
   "A5 the fees-by-type panel IS the totals' fee buckets, not a second derivation")
ok(sum(r["invoices"] for r in S["by_store"]) == S["totals"]["invoices"] and
   round(sum(r["grand_total"] for r in S["by_store"]), 2) == round(S["totals"]["grand_total"], 2),
   "A6 fees-by-store adds up to the tiles above it")
ok([r["location"] for r in S["by_store"]] == ["1 S 60th St", "5619 N Broad St"],
   "A7 by_store is ordered by grand_total, descending")
ok(vf.summarize([])["totals"]["invoices"] == 0 and vf.summarize(None)["by_store"] == [],
   "A8 an empty feed summarizes to zeroes, never raises")
ok(round(vf.summarize([inv("X", "2026-06-01", "$1,234.56", "($42.50)", 0)])
         ["totals"]["sub_total"], 2) == 1234.56,
   "A9 currency-formatted text reads as money (safe_float, not float)")
ok(vf.summarize([inv(None, "2026-06-01")])["by_store"][0]["location"] == "—",
   "A10 a locationless invoice buckets under the em-dash, never dropped from the total")

# ── §B  in_date_window: inclusive both ends, fail-closed on a dateless row ──────────────────────
print("\n§B  in_date_window — the date RANGE semantics")
R = inv("X", "2026-06-15")
ok(vf.in_date_window(R, "", ""), "B1 no window set -> every row is in scope")
ok(vf.in_date_window(R, "2026-06-15", "2026-06-15"),
   "B2 a single-day window INCLUDES that day (a timestamp is compared as a day, not as a datetime)")
ok(vf.in_date_window(R, "2026-06-01", "2026-06-30"), "B3 inside a month window")
ok(not vf.in_date_window(R, "2026-06-16", "2026-06-30"), "B4 before the start is out")
ok(not vf.in_date_window(R, "2026-06-01", "2026-06-14"), "B5 after the end is out")
ok(vf.in_date_window(R, "2026-06-15", ""), "B6 an open end is open (from a day onward)")
ok(vf.in_date_window(R, "", "2026-06-15"), "B7 an open start is open (through a day)")
ok(not vf.in_date_window(inv("X", None), "2026-06-01", "2026-06-30"),
   "B8 a row with NO date is EXCLUDED once a window is set (fail-closed, as the client agrees)")
ok(vf.in_date_window(inv("X", None), "", ""),
   "B9 ...and is kept when no window is set — a dateless row is only a problem for a window")

# ── §C  the regression: the fetched columns must carry the date the window reads ────────────────
print("\n§C  SUMMARY_COLS carries the date field (the first live run returned zero for every window)")
ok("created_on" in vf.SUMMARY_COLS,
   "C1 SUMMARY_COLS fetches created_on — without it in_date_window fail-closes on EVERY row")
ok("location" in vf.SUMMARY_COLS and "grand_total" in vf.SUMMARY_COLS,
   "C2 ...and the columns summarize() reads")
DATED = [inv("A", "2026-06-03"), inv("B", "2026-07-03")]
kept, _u = vf.select(DATED, date_from="2026-06-01", date_to="2026-06-30")
ok(len(kept) == 1 and kept[0]["location"] == "A",
   "C3 a June window over rows that HAVE created_on keeps exactly June")
STRIPPED = [{k: v for k, v in r.items() if k != "created_on"} for r in DATED]
kept2, _u2 = vf.select(STRIPPED, date_from="2026-06-01", date_to="2026-06-30")
ok(kept2 == [],
   "C4 ...and over rows fetched WITHOUT it keeps nothing — the exact shape of the live defect, so a "
   "column list that drops the date can never pass silently")

# ── §D  one selector, one vocabulary: the router dereferences, never re-derives ─────────────────
print("\n§D  the selection is computed once, and the market half is dereferenced")
SRC = open("app/modules/commcalc/router.py").read()


def endpoint(name):
    body = SRC[SRC.index(f'def {name}('):]
    cut = body.index("\n@router.get", 1) if "\n@router.get" in body[1:] else len(body)
    return body[:cut]


summary_src, list_src = endpoint("vip_summary"), endpoint("vip_invoices_list")
for nm, body in (("vip_summary", summary_src), ("vip_invoices_list", list_src)):
    ok("_vip_select(" in body,
       f"D1 {nm} reads the ONE selector")
    ok(".eq('location'" not in body and ".in_('period'" not in body,
       f"D2 {nm} writes no filter chain of its own")
    for p in ("date_from", "date_to", "stores", "markets"):
        ok(f"{p}: str" in body, f"D3 {nm} accepts {p}")
sel_src = endpoint("_vip_select")
ok("resolve_store_matcher" in sel_src,
   "D4 the selector resolves stores/markets through statement_filter.resolve_store_matcher — the "
   "SAME home the P&L store/market filter reads")
# The ban is on CODE, not on prose: the module's docstring NAMES the one home it defers to
# (`core.scope.market_index`), which is the point. So the source is stripped of its docstrings and
# comments first — otherwise explaining the design would trip the lock that protects it.
def code_only(src):
    import ast as _ast
    tree = _ast.parse(src)
    docstrings = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            d = _ast.get_docstring(node, clean=False)
            if d is not None:
                docstrings.add(d)
    out = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    body = "\n".join(out)
    for d in docstrings:
        body = body.replace(d, "")
    return body


VF_CODE = code_only(open("app/modules/commcalc/vip_invoice_filter.py").read())
for banned in ("_squash", "addr_keys", "alias_keys", "by_market", "code_groups", "market_index",
               "store_mapping", "storeops"):
    ok(banned not in VF_CODE,
       f"D5 vip_invoice_filter carries NO store-spelling rule of its own ({banned})")
ok("market_index" in open("app/modules/commcalc/vip_invoice_filter.py").read(),
   "D5b ...while its docstring still NAMES the one home it defers to")
ok("VIP_FEE_COLS = _vip_filter.FEE_COLS" in SRC,
   "D6 the router dereferences the one fee-bucket list instead of keeping a second copy")

# ── §E  select: unresolved rows are REPORTED, never folded into the totals ──────────────────────
print("\n§E  select — what a store/market selection could not bind is reported")
MIXED = [inv("1 S 60th St", "2026-06-03", 100.0, 110.0),
         inv("5619 N Broad St", "2026-06-04", 50.0, 55.0)]
only_broad = lambda a: str(a or "") == "5619 N Broad St"            # noqa: E731
kept, unres = vf.select(MIXED, store_matcher=only_broad)
ok([r["location"] for r in kept] == ["5619 N Broad St"], "E1 the matcher decides what is kept")
ok([r["location"] for r in unres] == ["1 S 60th St"],
   "E2 a row the matcher rejects is returned as unresolved, not thrown away")
rep = vf.summarize(kept, unres)
ok(rep["unresolved"]["invoices"] == 1 and round(rep["unresolved"]["grand_total"], 2) == 110.00,
   "E3 summarize REPORTS the unresolved count and money")
ok(rep["unresolved"]["locations"] == ["1 S 60th St"], "E4 ...and names the spellings")
ok(round(rep["totals"]["grand_total"], 2) == 55.00,
   "E5 ...and never folds them into the total (guessing a store into a market invents money)")
kept0, unres0 = vf.select(MIXED, store_matcher=None)
ok(len(kept0) == 2 and unres0 == [],
   "E6 no store/market selection -> nothing is excluded and nothing is 'unresolved'")
ok(vf.summarize(kept0)["unresolved"]["invoices"] == 0,
   "E7 ...and the report says so rather than omitting the key")
kept3, unres3 = vf.select(MIXED, store_matcher=only_broad, date_from="2026-07-01")
ok(kept3 == [] and unres3 == [],
   "E8 the date window is applied FIRST — a row outside it is out of scope, not 'unresolved'")

# ── §F  unbound_spellings: a picker never re-offers a store the matcher can already bind ────────
print("\n§F  statement_filter.unbound_spellings — the picker's bindability test is the MATCHER's")
# "5135 Bergenline Avenue" on the roster vs "5135 Bergenline Ave" in the feed: `scope._squash` sees
# two strings, `coa._squash_key` folds the street suffix. This is the live shape that grew the
# picker from 33 to 35 options.
# "1 Market St" shares the leading street number "1" with the 60th St store, which makes that
# number AMBIGUOUS and so fail-closed — the live condition, where house-org number "1" is claimed
# by more than one identity. Without it the matcher binds "1 S 60th St" on the number alone and
# this section proves nothing.
IDX = build_market_index(
    [{"store_code": "B-5135", "address": "5135 Bergenline Avenue", "market": "NJ"},
     {"store_code": "B-MKT", "address": "1 Market St, Wilmington", "market": "NJ"},
     {"store_code": "B-60TH", "address": "1 S 60th St, Philadelphia", "market": "PA"}],
    [{"store_code": "B-5135", "store_address": "5135 Bergenline Avenue", "market": "NJ"}],
    [],
)
FEED = ["5135 Bergenline Ave", "1 S 60th St", "999 Nowhere Rd"]
ok(sf.unbound_spellings(IDX, ["5135 Bergenline Ave"]) == [],
   "F1 a feed spelling the MATCHER binds is not unbound (suffix drift folded)")
ok(sf.unbound_spellings(IDX, ["999 Nowhere Rd"]) == ["999 Nowhere Rd"],
   "F2 a spelling nothing binds IS unbound")
ok("1 S 60th St" in sf.unbound_spellings(IDX, FEED),
   "F3 ...and so is one the matcher genuinely cannot bind (city suffix, no alias row)")
naive = build_store_options(IDX, present=FEED)
careful = build_store_options(IDX, present=sf.unbound_spellings(IDX, FEED))
ok(len(careful) < len(naive),
   "F4 passing the RAW feed vocabulary offers more options than there are stores (the §13e defect "
   "through the back door); passing only the unbound ones does not")
ok("5135 Bergenline Ave" not in [o["store"] for o in careful],
   "F5 the store bound under suffix drift is offered ONCE, under its canonical spelling")
ok(sf.unbound_spellings(IDX, ["999 Nowhere Rd", "999 nowhere rd"]) == ["999 Nowhere Rd"],
   "F6 deduped case-insensitively, first spelling kept")
ok(sf.unbound_spellings(IDX, ["", "  ", None]) == [], "F7 blanks are never options")
ok(sf.unbound_spellings(IDX, []) == [] and sf.unbound_spellings(IDX, None) == [],
   "F8 an empty vocabulary yields nothing")
ALIASED = build_market_index(
    [{"store_code": "B-MKT", "address": "1 Market St, Wilmington", "market": "NJ"},
     {"store_code": "B-60TH", "address": "1 S 60th St, Philadelphia", "market": "PA"}], [],
    [{"alias": "1 S 60th St", "store_code": "B-60TH"}])
ok(sf.unbound_spellings(ALIASED, ["1 S 60th St"]) == [],
   "F9 a store_aliases row is the fix: one DATA row and the spelling binds for every caller at once")
u, sq, nums = sf.market_key_expansion(ALIASED, ["PA"])
ok(sf.build_store_matcher(set(), u, sq, nums)("1 S 60th St"),
   "F10 ...including the MARKET filter, which is why the fix belongs in the vocabulary, not here")

print(f"\n{'='*70}\n  {PASS} passed, {FAIL} failed\n{'='*70}")
sys.exit(1 if FAIL else 0)
