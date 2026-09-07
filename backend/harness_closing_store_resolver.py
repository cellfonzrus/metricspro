"""PROOF: the closing X-report's store resolver never silently loses a store's POS cash.

OWNER BUG REPORT 2026-09-07 (verbatim): *"POS data for B-1800 and 1115 store is not capturing"*.

The feed was never missing. `commcalc.pos_tender_summary` held 144 X-report rows for '1115 Liberty
Ave' and 142 for '1800 Great Neck rd' over 2026-07-27..09-06. `closing/router._addr_resolver` was
throwing them away, and `_xreport_tenders_by_store` dropped the unresolved rows with a bare
`continue` — no error, no counter, no log line. Two stores' cash was absent from every closing recon
for months and nothing said so.

Live evidence, house org, replayed with the pre-fix logic before this harness was written:
  · '1115 Liberty Ave'  → None                 (144 rows discarded). The store IS on the storeops
                          MASTER as B-1115; the resolver just never read the master.
  · '1800 Great Neck rd'→ '1800GreatNeckRd'    (142 rows). B-1800's master address is NULL and its
                          store_mapping address is the placeholder 'B-1800', so the real address
                          resolved to a rogue code nothing else reads.
  · closing store_codes with NO POS coverage: exactly ['B-1115', 'B-1800'] — the two the owner named.
After the fix all 28 of the org's X-report store names resolve, and no closing store lacks coverage.

WHAT THIS PINS
  A. lookup ORDER: explicit alias > storeops master address > store_mapping address > unambiguous
     leading street-number. The master outranks store_mapping because a closing row's store_code is
     always a master code — that is the identity the join has to land on.
  B. the two live regressions above, as fixtures.
  C. an unresolved name is still not counted (guessing a store would be worse) but is REPORTED.
  D. org scoping: one tenant's addresses can never resolve another tenant's store.

DB-FREE: an in-memory fake client. stdlib only.
"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


class _Q:
    def __init__(self, rows):
        self.rows, self._org = rows, None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        if col == "org_id":
            self._org = val
        elif col == "close_date":
            self.rows = [r for r in self.rows if r.get("close_date") == val]
        return self

    def execute(self):
        rows = [r for r in self.rows if self._org is None or r.get("org_id") == self._org]
        return type("R", (), {"data": rows})()


class Fake:
    """Just enough Supabase to serve the three tables the resolver reads."""
    def __init__(self, tables):
        self.tables, self._schema = tables, None

    def schema(self, name):
        self._schema = name
        return self

    def table(self, name):
        return _Q(list(self.tables.get((self._schema, name), [])))


ORG = "org-1"
OTHER = "org-2"


def build(aliases=(), master=(), mapping=()):
    t = {
        ("commcalc", "store_aliases"): [dict(org_id=ORG, **r) if "org_id" not in r else r
                                        for r in aliases],
        ("storeops", "stores"): [dict(org_id=ORG, **r) if "org_id" not in r else r for r in master],
        ("commcalc", "store_mapping"): [dict(org_id=ORG, **r) if "org_id" not in r else r
                                        for r in mapping],
    }
    from app.modules.closing.router import _addr_resolver
    return _addr_resolver(Fake(t), ORG)


print("=" * 78)
print("A. Lookup order — the store MASTER outranks store_mapping; an alias outranks both")
print("=" * 78)
r = build(master=[{"store_code": "B-9", "address": "9 Main St"}],
          mapping=[{"store_code": "JUNK-9", "store_address": "9 Main St"}])
check("A1 the master code wins over a store_mapping row claiming the SAME address "
      "(a closing row's store_code is always a master code, so that is the identity to land on)",
      r("9 Main St") == "B-9", r("9 Main St"))
r = build(aliases=[{"alias": "9 Main Street", "store_code": "B-9"}],
          master=[{"store_code": "OTHER", "address": "9 Main Street"}])
check("A2 an EXPLICIT alias outranks every heuristic (an admin has confirmed this spelling)",
      r("9 Main Street") == "B-9", r("9 Main Street"))
r = build(mapping=[{"store_code": "M-9", "store_address": "9 Main St"}])
check("A3 store_mapping still resolves when the master has no address (pre-fix behaviour kept)",
      r("9 Main St") == "M-9", r("9 Main St"))
r = build(master=[{"store_code": "B-77", "address": "77 Elm Ave"}])
check("A4 unambiguous leading street-number still resolves a spelling variant",
      r("77 Elm Avenue Suite B") == "B-77", r("77 Elm Avenue Suite B"))
r = build(master=[{"store_code": "B-5a", "address": "5 Oak St"},
                  {"store_code": "B-5b", "address": "5 Pine St"}])
check("A5 an AMBIGUOUS house number resolves to NOTHING — never a coin-flip between two stores",
      r("5 Maple Rd") is None, r("5 Maple Rd"))

print()
print("=" * 78)
print("B. The two live regressions the owner reported")
print("=" * 78)
# B-1115: on the master, absent from store_mapping. Pre-fix this returned None and 144 rows of POS
# cash were discarded.
r = build(master=[{"store_code": "B-1115", "address": "1115 Liberty Ave"}],
          mapping=[{"store_code": "B-103", "store_address": "103 Fulton Ave"}])
check("B1 '1115 Liberty Ave' resolves to B-1115 from the MASTER "
      "(pre-fix: None — 144 X-report rows silently discarded)",
      r("1115 Liberty Ave") == "B-1115", r("1115 Liberty Ave"))
# B-1800: master address NULL, store_mapping holds a PLACEHOLDER, and the real address is registered
# to a rogue code. Pre-fix the POS cash landed on '1800GreatNeckRd', which nothing reads.
r = build(master=[{"store_code": "B-1800", "address": None}],
          mapping=[{"store_code": "B-1800", "store_address": "B-1800"},
                   {"store_code": "1800GreatNeckRd", "store_address": "1800 Great Neck Rd"}],
          aliases=[{"alias": "1800 Great Neck rd", "store_code": "B-1800"}])
check("B2 '1800 Great Neck rd' reaches B-1800 once an alias names it "
      "(pre-fix: '1800GreatNeckRd', a code no report reads)",
      r("1800 Great Neck rd") == "B-1800", r("1800 Great Neck rd"))
check("B3 a placeholder address ('B-1800' as its own address) resolves to its own code, not a foreign one",
      r("B-1800") == "B-1800", r("B-1800"))

print()
print("=" * 78)
print("C. An unresolved store name is never counted, and never silent")
print("=" * 78)
r = build(master=[{"store_code": "B-9", "address": "9 Main St"}])
check("C1 a name nothing knows resolves to None (a guessed store would put real cash on the wrong one)",
      r("Somewhere Else") is None, r("Somewhere Else"))
check("C2 blank input resolves to None rather than raising inside a recon",
      r("") is None and r(None) is None)

import app.modules.closing.router as CR                       # noqa: E402
_fake = Fake({
    ("commcalc", "store_aliases"): [],
    ("storeops", "stores"): [{"org_id": ORG, "store_code": "B-9", "address": "9 Main St"}],
    ("commcalc", "store_mapping"): [],
    ("commcalc", "pos_tender_summary"): [
        {"org_id": ORG, "close_date": "2026-09-05", "store": "9 Main St",
         "tender_class": "cash", "amount": 100.0},
        {"org_id": ORG, "close_date": "2026-09-05", "store": "Nowhere Rd",
         "tender_class": "cash", "amount": 55.0},
    ],
})
_buf = io.StringIO()
with redirect_stdout(_buf):
    out = CR._xreport_tenders_by_store(_fake, ORG, "2026-09-05")
_log = _buf.getvalue()
check("C3 the resolvable store is aggregated normally",
      out.get("B-9", {}).get("cash") == 100.0, str(out))
check("C4 the unresolvable one is NOT invented onto another store",
      len(out) == 1, str(out))
check("C5 …but it is REPORTED — the bare `continue` that hid this for months is gone",
      "Nowhere Rd" in _log and "NOT counted" in _log, repr(_log[:200]))

print()
print("=" * 78)
print("D. Org scoping — one tenant's address can never resolve another tenant's store")
print("=" * 78)
from app.modules.closing.router import _addr_resolver                       # noqa: E402
_t = {("commcalc", "store_aliases"): [],
      ("storeops", "stores"): [{"org_id": OTHER, "store_code": "LUX-1", "address": "9 Main St"}],
      ("commcalc", "store_mapping"): []}
check("D1 another org's store with the SAME address does not resolve for this org "
      "(the cross-tenant read that made an earlier analysis of this very bug wrong)",
      _addr_resolver(Fake(_t), ORG)("9 Main St") is None)
_t[("storeops", "stores")].append({"org_id": ORG, "store_code": "B-9", "address": "9 Main St"})
check("D2 …and this org's own store still resolves, unaffected by the neighbour",
      _addr_resolver(Fake(_t), ORG)("9 Main St") == "B-9")

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
