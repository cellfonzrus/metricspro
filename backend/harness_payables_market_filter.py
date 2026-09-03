"""Harness — the payables/forecast market filter must actually filter.

Owner report 2026-08-11: "Device Forecasting & Vendor Payables does not pull up anything under market
ny and chicago". Two independent defects, both reproduced here before the fix is proven:

  1. NO `market` FIELD ON THE ROW. The picker was filled from the org roster (which has markets) while
     the filter predicate read `row.market` — undefined on every row — so any market selection emptied
     the table.
  2. VOCABULARY SPLIT, measured on luxelink 2026-08-11: `storeops.stores.address` holds
     "4640 Diversey Chicago" / "2317 Cicero Cicero" / "21880 Hempstead Ave" while the rows hold
     "4640-A W Diversey Ave" / "2317 S Cicero Ave STE A" / "218-80 Hempstead Avenue" — **0 of 20
     match**. `commcalc.store_mapping.store_address` is byte-identical to the row spelling AND carries
     the market, so it is the resolver for both.

Read-only: the fake client raises on every write verb.
"""
import sys, os, types

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


class _Q:
    def __init__(self, rows, sink):
        self.rows, self.sink, self.f = rows, sink, []

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def execute(self):
        r = self.rows
        for c, v in self.f:
            r = [x for x in r if x.get(c) == v]
        self.sink.append(list(self.f))
        return types.SimpleNamespace(data=r)

    def _w(self, *a, **k): raise AssertionError("READ-ONLY harness — write verb called")
    insert = update = upsert = delete = _w


class _S:
    def __init__(self, t, sink, boom=()):
        self.t, self.sink, self.boom = t, sink, boom

    def table(self, n):
        if n in self.boom:
            raise RuntimeError(f"simulated: {n} unavailable")
        return _Q(list(self.t.get(n, [])), self.sink)


class FakeClient:
    def __init__(self, t, boom=()):
        self.t, self.filters, self.boom = t, [], boom

    def schema(self, n): return _S(self.t, self.filters, self.boom)


import app.modules.payables.router as R  # noqa: E402
from app.core import scope as _cscope  # noqa: E402  (canonical resolver cache control, 2026-09-03)

ORG, OTHER = "854f6d7b", "00000000"
# The REAL luxelink split, measured 2026-08-11.
MAPPING = [
    {"org_id": ORG, "store_address": "4640-A W Diversey Ave", "market": "Chicago"},
    {"org_id": ORG, "store_address": "2317 S Cicero Ave STE A", "market": "Chicago"},
    {"org_id": ORG, "store_address": "218-80 Hempstead Avenue", "market": "NY"},
    {"org_id": ORG, "store_address": "957 Pennsylvania Avenue", "market": "NY"},
    {"org_id": OTHER, "store_address": "1 Other St", "market": "XX"},
]
ROSTER_SPELLING = ["4640 Diversey Chicago", "2317 Cicero Cicero", "21880 Hempstead Ave"]

print("\n§1 · THE VOCABULARY SPLIT IS REAL (this is why the roster could never match)")
row_spellings = {m["store_address"] for m in MAPPING if m["org_id"] == ORG}
ok(not (set(ROSTER_SPELLING) & row_spellings),
   "ZERO storeops.stores.address spellings match a report-row store — the two vocabularies are disjoint")

print("\n§2 · store_mapping resolves the market for the row spelling")
_cscope.invalidate_market_index()   # fresh canonical index per fake-client phase
c = FakeClient({"store_mapping": MAPPING})
m = R._market_by_store(c, ORG)
ok(R._market_of(m, "4640-A W Diversey Ave") == "Chicago", "Diversey -> Chicago")
ok(R._market_of(m, "218-80 Hempstead Avenue") == "NY", "Hempstead -> NY")
ok(R._market_of(m, "957 Pennsylvania Avenue") == "NY", "957 Pennsylvania -> NY")
ok(R._market_of(m, "  4640-A   W Diversey Ave ") == "Chicago",
   "whitespace/case-tolerant — a stray double space in an export still resolves")
# 2026-09-03 CANONICAL-RESOLUTION UPDATE (owner "1115 Liberty Ave / fix once for all" directive):
# _market_by_store now delegates to core.scope.store_market_resolver — the UNION resolver. A roster
# spelling that shares an UNAMBIGUOUS leading street number with the row spelling now resolves to
# the SAME store's market (that is the cure for the 1115-Liberty class, where one vocabulary's
# spelling was invisible to the other's resolver). The 2026-08-11 fix's real guarantee — the ROW
# vocabulary always resolves — is proven above and unchanged.
ok(R._market_of(m, "4640 Diversey Chicago") == "Chicago",
   "the ROSTER spelling now ALSO resolves (unambiguous leading street number → same store, same market)")

print("\n§3 · TENANT ISOLATION (RULE ONE)")
ok(R._market_of(m, "1 Other St") is None, "another tenant's store never resolves")
ok(all(any(k == "org_id" and v == ORG for k, v in f) for f in c.filters),
   f"every read filtered on org_id ({len(c.filters)} quer(ies))")

print("\n§4 · UNRESOLVED STAYS SELECTABLE, NEVER SILENTLY DROPPED")
ok(R._market_of(m, "999 Unknown Rd") is None, "an unmapped store resolves to None (renders '(no market)')")
ok(R._market_of(m, "") is None and R._market_of(m, None) is None, "blank/None store is safe")

print("\n§5 · DEGRADES — a missing store_mapping must not 500 the page")
_cscope.invalidate_market_index()   # drop the §2 cache so the degraded read is really exercised
c2 = FakeClient({}, boom=("store_mapping", "stores", "store_aliases"))
m2 = R._market_by_store(c2, ORG)
ok(R._market_of(m2, "4640-A W Diversey Ave") is None and R._market_of(m2, "x") is None,
   "unavailable vocabularies -> resolver answers None for everything, no raise")
_cscope.invalidate_market_index()   # don't let the empty degraded index poison §6

print("\n§6 · /payables/filter-options serves the ROW vocabulary + its markets")
R.sb = lambda: FakeClient({"store_mapping": MAPPING})
res = R.payables_filter_options(org_id=ORG)
stores = [s["store"] for s in res["stores"]]
ok(res["source"] == "store_mapping", "source is named in the response")
ok(len(stores) == 4, f"4 luxelink stores offered (got {len(stores)})")
ok("1 Other St" not in stores, "the other tenant's store is absent")
ok(stores == sorted(stores), "options are sorted (stable pick-don't-type list)")
ok(res["markets"] == ["Chicago", "NY"], f"markets are the real distinct values (got {res['markets']})")
ok(all(s["store"] in row_spellings for s in res["stores"]),
   "every option is in the spelling the ROWS use — so selecting one can actually match")

print("\n§7 · DUPLICATE ADDRESSES COLLAPSE (store_mapping holds LUX-* twins on the same address)")
dup = MAPPING + [{"org_id": ORG, "store_address": "957 Pennsylvania Avenue", "market": "NY"}]
R.sb = lambda: FakeClient({"store_mapping": dup})
res2 = R.payables_filter_options(org_id=ORG)
ok(len([s for s in res2["stores"] if s["store"] == "957 Pennsylvania Avenue"]) == 1,
   "a store listed twice in store_mapping yields ONE option, not a duplicate")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  ✗ " + f)
sys.exit(1 if FAIL else 0)
