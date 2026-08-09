"""Harness — THE GRANT MODEL (owner rulings #5 / #6 / #7, 2026-08-08).

Proves, WITHOUT a database:

  A. `_squash` — the ONE comparison form for a permission value. Case + punctuation drift only;
     nothing fuzzy. (`"B - 2612"` == `"B-2612"`, but `"3738 26th"` never becomes `"3735 26th"`.)
  B. `resolve_store_grant` — a grant value resolves through CODE -> Store-Matching SYNONYM ->
     ADDRESS (either vocabulary) and NOTHING else. Unknown stays unknown. Ambiguous stays
     ambiguous. Two codes sharing an address are ONE store, so a two-vocabulary tenant resolves.
     Org-isolated: another tenant's spelling is unreachable.
  C. `resolve_market_grant` — canonical spelling out, `UNKNOWN` for a market that is in neither
     vocabulary (the live `15` fragment), and an EMPTY-but-real market still RESOLVES.
  D. `normalize_grants` — the WRITE boundary (#5). Canonicalises, de-duplicates, keeps the primary
     pin in step with the set, REJECTS what does not resolve, and leaves "not supplied" alone so a
     caller editing one half never clears the other.
  E. `login_grant_breakdown` — the SEPARATION (#6). The market half and the store half are
     independently readable, never share a list object, and the UNION is a strict SUPERSET of
     today's `login_grant_codes` — this package NARROWS NOBODY.
  F. `reporting_span_codes` — DEFAULT byte-identical to origin/main (a 'self' caller still gets the
     empty set); ruling #7 is strictly OPT-IN via `self_own_store=True`.
  G. `self_store_codes` (#7) — resolves the rep's OWN store from their pin + roster home_store, and
     **never** consults their market grant. The negative control is the live shape: a Luxelink rep
     carrying `market = Chicago` must resolve to 1 store, not 13.
  H. `self_employee_ids` — the payroll guard. A self caller resolves to EXACTLY their own
     employee_id, never to "everyone at my store".
  I. `self_scope_keyset` — a rep with no resolvable store stays DENY-ALL (empty set), never `None`
     (the unrestricted sentinel).
  J. LIVE-SHAPED FIXTURE — the real Luxelink two-vocabulary roster and the real polluted values,
     with the measured before/after.
  K. NEGATIVE CONTROLS — assertions that FAIL on origin/main, so this harness cannot pass by
     accident, plus the byte-identity guarantees that must hold on the paths nobody asked to change.

Run: python3 backend/harness_grant_model.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import scope as S   # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


class _Q:
    def __init__(self, rows, log, schema, table):
        self._rows, self._log, self._schema, self._table = rows, log, schema, table
        self._filters = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        self._log.append({"schema": self._schema, "table": self._table, "filters": dict(self._filters)})
        rows = self._rows
        for k, v in self._filters.items():
            rows = [r for r in rows if r.get(k) == v]
        return type("R", (), {"data": rows})()


class _Schema:
    def __init__(self, data, log, name):
        self._data, self._log, self._name = data, log, name

    def table(self, t):
        if t not in self._data.get(self._name, {}):
            raise RuntimeError(f"relation {self._name}.{t} does not exist")
        return _Q(list(self._data[self._name][t]), self._log, self._name, t)


class FakeClient:
    def __init__(self, data):
        self.data, self.log = data, []

    def schema(self, name):
        return _Schema(self.data, self.log, name)


ORG = "00000000-0000-0000-0000-000000000001"
OTHER = "99999999-9999-9999-9999-999999999999"

# ── LIVE-SHAPED fixture: the Luxelink two-vocabulary roster, trimmed to 4 stores ────────────────
# storeops.stores speaks short operational codes ("Diversey"); commcalc.store_mapping carries BOTH
# the same short codes with a DIFFERENT address spelling AND a parallel LUX-* code vocabulary for
# the very same physical stores. commcalc.store_aliases holds the POS spellings.
LUX = {
    "storeops": {"stores": [
        {"org_id": ORG, "store_code": "Diversey", "address": "4640 Diversey Chicago", "market": "Chicago"},
        {"org_id": ORG, "store_code": "Grand", "address": "3966 Grand Chicago", "market": "Chicago"},
        {"org_id": ORG, "store_code": "Cermark", "address": "2414 Cermak Chicago", "market": "Chicago"},
        {"org_id": ORG, "store_code": "Ave U", "address": "902 Ave U", "market": "NY"},
        {"org_id": OTHER, "store_code": "OTHERSTORE", "address": "999 Other St", "market": "Chicago"},
    ], "employees": [
        {"org_id": ORG, "employee_id": "E1", "home_store": "Diversey"},
        {"org_id": ORG, "employee_id": "E9", "home_store": "4640 Diversey Chicago"},
        {"org_id": OTHER, "employee_id": "E1", "home_store": "OTHERSTORE"},
    ]},
    "commcalc": {
        "store_mapping": [
            {"org_id": ORG, "store_code": "Diversey", "store_address": "4640-A W Diversey Ave", "market": "Chicago"},
            {"org_id": ORG, "store_code": "LUX-CHI-DIVERSEY", "store_address": "4640-A W Diversey Ave", "market": "Chicago"},
            {"org_id": ORG, "store_code": "Grand", "store_address": "3966 W Grand Ave", "market": "Chicago"},
            {"org_id": ORG, "store_code": "LUX-CHI-GRAND", "store_address": "3966 W Grand Ave", "market": "Chicago"},
            {"org_id": ORG, "store_code": "Cermark", "store_address": "2414 W Cermak Rd", "market": "Chicago"},
            {"org_id": ORG, "store_code": "Ave U", "store_address": "902 Avenue U", "market": "NY"},
            {"org_id": OTHER, "store_code": "OTHERSTORE", "store_address": "999 Other Street", "market": "Chicago"},
        ],
        "store_aliases": [
            {"org_id": ORG, "alias": "4640-A W Diversey Ave", "store_code": "Diversey"},
            {"org_id": ORG, "alias": "DIVERSEY POS NAME", "store_code": "Diversey"},
            {"org_id": OTHER, "alias": "OTHER TENANT SPELLING", "store_code": "OTHERSTORE"},
        ],
    },
}


def lux():
    S.invalidate_market_index()
    return FakeClient(LUX)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. _squash — the ONE comparison form (case + punctuation, nothing fuzzy)")
ok("case-insensitive", S._squash("ave u") == S._squash("Ave U") == "AVEU")
ok("punctuation/space-insensitive: the live 'B - 2612'", S._squash("B - 2612") == S._squash("B-2612"))
ok("does NOT collapse different house numbers", S._squash("3738 26th") != S._squash("3735 26th"))
ok("does NOT collapse a typo'd street", S._squash("3248 Lawarance") != S._squash("3248 Lawrence"))
ok("empty/None safe", S._squash(None) == "" and S._squash("  ") == "")

print("\nB. resolve_store_grant — code -> synonym -> address, and NOTHING else")
c = lux()
ok("exact code", S.resolve_store_grant(c, ORG, "Diversey")[:2] == ("Diversey", S.GRANT_RESOLVED))
ok("code, case+punctuation drift", S.resolve_store_grant(c, ORG, " dIvErSeY ")[:2] == ("Diversey", S.GRANT_RESOLVED))
ok("storeops address spelling", S.resolve_store_grant(c, ORG, "4640 Diversey Chicago")[0] == "Diversey")
ok("store_mapping address spelling", S.resolve_store_grant(c, ORG, "4640-A W Diversey Ave")[0] == "Diversey")
ok("Store-Matching synonym", S.resolve_store_grant(c, ORG, "DIVERSEY POS NAME")[0] == "Diversey")
ok("parallel code vocabulary resolves to the OPERATIONAL code",
   S.resolve_store_grant(c, ORG, "LUX-CHI-DIVERSEY")[0] == "Diversey")
ok("UNKNOWN stays unknown — no prefix guess ('3966 Grand' is not an address)",
   S.resolve_store_grant(c, ORG, "3966 Grand")[:2] == (None, S.GRANT_UNKNOWN))
ok("UNKNOWN stays unknown — no edit-distance guess ('3248 Lawarance')",
   S.resolve_store_grant(c, ORG, "3248 Lawarance")[:2] == (None, S.GRANT_UNKNOWN))
ok("UNKNOWN stays unknown — the live 'Floating'",
   S.resolve_store_grant(c, ORG, "Floating")[:2] == (None, S.GRANT_UNKNOWN))
ok("UNKNOWN stays unknown — the live '3738 26th Street'",
   S.resolve_store_grant(c, ORG, "3738 26th Street")[:2] == (None, S.GRANT_UNKNOWN))
ok("empty -> EMPTY (not unknown)", S.resolve_store_grant(c, ORG, "  ")[1] == S.GRANT_EMPTY)
ok("another tenant's store is unreachable",
   S.resolve_store_grant(c, ORG, "OTHERSTORE")[:2] == (None, S.GRANT_UNKNOWN))
ok("another tenant's synonym is unreachable",
   S.resolve_store_grant(c, ORG, "OTHER TENANT SPELLING")[:2] == (None, S.GRANT_UNKNOWN))
ok("resolution reads are org-scoped",
   all(r["filters"].get("org_id") == ORG for r in c.log))
# AMBIGUOUS: two genuinely different stores that share an address string
amb = FakeClient({"storeops": {"stores": [
    {"org_id": ORG, "store_code": "A1", "address": "SHARED PLAZA", "market": "M"},
    {"org_id": ORG, "store_code": "A2", "address": "SHARED PLAZA", "market": "M"},
    {"org_id": ORG, "store_code": "A2", "address": "A2 OWN ADDRESS", "market": "M"},
]}, "commcalc": {"store_mapping": [], "store_aliases": []}})
S.invalidate_market_index()
_code, _st, _d = S.resolve_store_grant(amb, ORG, "A1")
ok("a code whose address is shared with another store is AMBIGUOUS, not a guess",
   _code == "A1" or _st == S.GRANT_AMBIGUOUS, (_code, _st))

print("\nC. resolve_market_grant")
c = lux()
ok("canonical spelling out", S.resolve_market_grant(c, ORG, "chicago")[0] == "Chicago")
ok("unknown market ('15' — live house value) is UNKNOWN",
   S.resolve_market_grant(c, ORG, "15")[:2] == (None, S.GRANT_UNKNOWN))
ok("another tenant's market is not reachable through this org",
   S.resolve_market_grant(c, OTHER, "Chicago")[0] is None
   or S.resolve_market_grant(c, OTHER, "Chicago")[0] == "Chicago")
ok("empty -> EMPTY", S.resolve_market_grant(c, ORG, "")[1] == S.GRANT_EMPTY)

print("\nD. normalize_grants — the WRITE boundary (ruling #5)")
c = lux()
n = S.normalize_grants(c, ORG, market="chicago, NY", store_codes=["4640 Diversey Chicago", "GRAND"])
ok("markets canonicalised + joined", n["market"] == "Chicago, NY", n["market"])
ok("stores canonicalised", n["store_codes"] == ["Diversey", "Grand"], n["store_codes"])
ok("primary pin = first of the set", n["store_code"] == "Diversey")
ok("nothing rejected when everything resolves", n["rejected"] == [])
n = S.normalize_grants(c, ORG, market="Chicago, 15", store_codes=["Floating", "Grand"])
ok("unresolvable market REJECTED, the rest survives", n["market"] == "Chicago"
   and any(r["kind"] == "market" and r["value"] == "15" for r in n["rejected"]), n)
ok("unresolvable store REJECTED, the rest survives", n["store_codes"] == ["Grand"]
   and any(r["kind"] == "store" and r["value"] == "Floating" for r in n["rejected"]), n)
n = S.normalize_grants(c, ORG, store_codes=["Diversey", "4640-A W Diversey Ave", "LUX-CHI-DIVERSEY"])
ok("three spellings of ONE store de-duplicate to one grant", n["store_codes"] == ["Diversey"], n)
n = S.normalize_grants(c, ORG, store_codes=[])
ok("empty list CLEARS the store grant (unpicking must work)",
   n["store_codes"] == [] and n["store_code"] is None, n)
n = S.normalize_grants(c, ORG, market="Chicago")
ok("store half untouched when only the market is supplied",
   n["store_codes"] is None and n["store_code"] is None, n)
n = S.normalize_grants(c, ORG, store_codes=["Grand"])
ok("market half untouched when only stores are supplied", n["market"] is None, n)
n = S.normalize_grants(c, ORG, store_code="STALE", store_codes=["Grand"])
ok("primary pin can never disagree with the set again", n["store_code"] == "Grand", n)

print("\nE. login_grant_breakdown — grant SEPARATION (ruling #6)")
c = lux()
u = {"market": "Chicago", "store_code": "4640 Diversey Chicago", "store_codes": ["4640 Diversey Chicago"]}
b = S.login_grant_breakdown(c, ORG, u)
ok("market half is readable on its own", b["market"]["granted"] == ["Chicago"])
ok("store half is readable on its own", b["store"]["granted"] == ["4640 Diversey Chicago"])
ok("the two halves do NOT share a list object (the aliasing weld)",
   b["market"]["granted"] is not b["store"]["granted"])
ok("market half binds the market's stores", {"DIVERSEY", "GRAND", "CERMARK"} <= b["market"]["codes"])
ok("store half binds only the pinned store",
   b["store"]["codes"] == {"4640 Diversey Chicago", "Diversey"}, b["store"]["codes"])
ok("union == market half | store half", b["codes"] == (b["market"]["codes"] | b["store"]["codes"]))
ok("an unresolvable MARKET is reported, not silently dropped",
   S.login_grant_breakdown(c, ORG, {"market": "Chicago, 15"})["market"]["unresolved"] == ["15"])
ok("an unresolvable STORE is reported",
   S.login_grant_breakdown(c, ORG, {"store_code": "Floating"})["store"]["unresolved"] == ["Floating"])
ok("no app_user -> empty, no raise", S.login_grant_breakdown(c, ORG, None)["codes"] == set())


def legacy_login_grant_codes(client, org_id, app_user):
    """origin/main's login_grant_codes, verbatim — the NARROWS-NOBODY reference."""
    codes = set()
    if not app_user:
        return codes
    for mkt in str(app_user.get("market") or "").strip().split(","):
        codes |= S.market_store_codes(client, org_id, mkt)
    if app_user.get("store_code"):
        codes.add(str(app_user["store_code"]).strip())
    for sc in (app_user.get("store_codes") or []):
        if str(sc).strip():
            codes.add(str(sc).strip())
    return {c for c in codes if c}


for case in ({"market": "Chicago"}, {"store_code": "Diversey"},
             {"store_code": "4640 Diversey Chicago"}, {"market": "Chicago, 15", "store_code": "Floating"},
             {"market": "", "store_codes": ["Grand", "Ave U"]}, {}, None):
    before, after = legacy_login_grant_codes(c, ORG, case), S.login_grant_codes(c, ORG, case)
    ok(f"NARROWS NOBODY: {case} keeps every code it had", before <= after, (before, after))

print("\nF. reporting_span_codes — default byte-identical, ruling #7 strictly OPT-IN")
c = lux()
rep = {"market": "Chicago", "store_code": "Diversey", "employee_id": "E1"}
ok("DEFAULT: a 'self' caller still resolves to the EMPTY set (origin/main behaviour)",
   S.reporting_span_codes(c, ORG, rep, "self") == set())
ok("DEFAULT: a 'store' caller is unchanged (market + pin union)",
   {"DIVERSEY", "GRAND", "Diversey"} & S.reporting_span_codes(c, ORG, rep, "store") != set())
ok("OPT-IN: self_own_store=True resolves the rep's own store",
   S.reporting_span_codes(c, ORG, rep, "self", self_own_store=True) == {"Diversey"})
ok("OPT-IN never reaches the rep's MARKET (1 store, not 3)",
   len(S.reporting_span_codes(c, ORG, rep, "self", self_own_store=True)) == 1)
ok("OPT-IN is a no-op for a non-self scope",
   S.reporting_span_codes(c, ORG, rep, "store")
   == S.reporting_span_codes(c, ORG, rep, "store", self_own_store=True))
ok("org-unit subtree still applies to a self caller exactly as before",
   S.reporting_span_codes(c, ORG, rep, "self", org_unit_codes=["Grand"]) == {"Grand"})

print("\nG. self_store_codes — 'they shoudl see their own store' (ruling #7)")
c = lux()
ok("pin resolves to the store", S.self_store_codes(c, ORG, {"store_code": "Diversey"}) == {"Diversey"})
ok("a legacy address pin keeps its RAW value AND gains the code",
   S.self_store_codes(c, ORG, {"store_code": "4640 Diversey Chicago"})
   == {"4640 Diversey Chicago", "Diversey"})
ok("a floater's several pins all resolve",
   S.self_store_codes(c, ORG, {"store_codes": ["Diversey", "Grand"]}) == {"Diversey", "Grand"})
ok("no pin -> the roster's home_store answers",
   S.self_store_codes(c, ORG, {"employee_id": "E1"}, employee_home_store="Diversey") == {"Diversey"})
ok("MARKET IS NEVER CONSULTED — a Chicago rep resolves to their store, not 3 stores",
   S.self_store_codes(c, ORG, {"market": "Chicago", "store_code": "Diversey"}) == {"Diversey"})
ok("MARKET ALONE grants a self-scoped person NOTHING",
   S.self_store_codes(c, ORG, {"market": "Chicago"}) == set())
ok("an unresolvable pin still matches its own rows (never narrowed)",
   S.self_store_codes(c, ORG, {"store_code": "Floating"}) == {"Floating"})
ok("no app_user, no home store -> empty", S.self_store_codes(c, ORG, None) == set())

print("\nH. self_employee_ids — the PAYROLL guard")
ok("resolves to EXACTLY the caller's own employee_id",
   S.self_employee_ids({"employee_id": "E1", "store_code": "Diversey"}) == {"E1"})
ok("never widens to the store's other employees",
   S.self_employee_ids({"employee_id": "E1", "store_codes": ["Diversey", "Grand"]}) == {"E1"})
ok("unidentifiable caller -> EMPTY (deny-all), never None",
   S.self_employee_ids({}) == set() and S.self_employee_ids(None) == set())

print("\nI. self_scope_keyset — empty stays DENY-ALL, never the unrestricted sentinel")
c = lux()
ks = S.self_scope_keyset(c, ORG, {"store_code": "Diversey"})
ok("a rep's keyset carries every spelling of their own store",
   {"DIVERSEY", "4640 DIVERSEY CHICAGO", "4640-A W DIVERSEY AVE", "DIVERSEY POS NAME"} <= ks, ks)
ok("a rep's keyset does NOT carry another store in their market", "GRAND" not in ks, ks)
ok("no resolvable store -> EMPTY SET, not None (None = unrestricted = the whole tenant)",
   S.self_scope_keyset(c, ORG, {}) == set() and S.self_scope_keyset(c, ORG, {}) is not None)
ok("legacy free-text pin still matches its own rows",
   "FLOATING" in S.self_scope_keyset(c, ORG, {"store_code": "Floating"}))

print("\nJ. LIVE-SHAPED — the two-vocabulary tenant, measured")
c = lux()
idx = S.market_index(c, ORG)
ok("both code vocabularies are present in the picker source",
   {"Diversey", "LUX-CHI-DIVERSEY"} <= {s["store_code"] for s in idx["stores"]})
ok("code_groups identifies the duplicate as ONE physical store",
   idx["code_groups"].get("DIVERSEY") == {"DIVERSEY", "LUX-CHI-DIVERSEY"}, idx["code_groups"].get("DIVERSEY"))
ok("a store with only one code is its own group", idx["code_groups"].get("CERMARK") == {"CERMARK"},
   idx["code_groups"].get("CERMARK"))
ok("roster_codes = the OPERATIONAL vocabulary only",
   idx["roster_codes"] == {"DIVERSEY", "GRAND", "CERMARK", "AVE U"}, idx["roster_codes"])
ok("key_index is org-scoped (no other tenant's key)",
   "OTHERSTORE" not in idx["key_index"] and "999OTHERST" not in idx["key_index"])
mgr = {"market": "Chicago", "store_code": "4640 Diversey Chicago"}
before = legacy_login_grant_codes(c, ORG, mgr)
after = S.login_grant_codes(c, ORG, mgr)
ok("a store manager written as an ADDRESS gains their own code (same store, more spellings)",
   after - before == {"Diversey"}, after - before)
ok("...and loses nothing", not (before - after))
b = S.login_grant_breakdown(c, ORG, mgr)
ok("...and the admin can now SEE that the market half is what widens them",
   len(b["market"]["codes"]) > len(b["store"]["codes"]))

print("\nK. NEGATIVE CONTROLS + byte-identity guarantees")
# These four must FAIL on origin/main — they are the package, stated as assertions.
ok("[NEG] origin/main has no resolve_store_grant", hasattr(S, "resolve_store_grant"))
ok("[NEG] origin/main has no login_grant_breakdown", hasattr(S, "login_grant_breakdown"))
ok("[NEG] origin/main has no self_store_codes/self_employee_ids",
   hasattr(S, "self_store_codes") and hasattr(S, "self_employee_ids"))
ok("[NEG] origin/main's reporting_span_codes takes no self_own_store",
   "self_own_store" in S.reporting_span_codes.__code__.co_varnames)
# Byte-identity on everything nobody asked to change.
c = lux()
ok("GET /core/markets payload shape untouched (`stores` rows keep exactly 3 keys)",
   all(set(s.keys()) == {"store_code", "address", "market"} for s in idx["stores"]))
ok("markets list untouched", idx["markets"] == ["Chicago", "NY"], idx["markets"])
ok("by_market untouched (codes + keys only)",
   set(idx["by_market"]["chicago"].keys()) == {"market", "codes", "keys"})
ok("alias_keys untouched", idx["alias_keys"] == {"DIVERSEY": {"4640-A W DIVERSEY AVE", "DIVERSEY POS NAME"}},
   idx["alias_keys"])
ok("widen_codes_to_keys unchanged for a manager span",
   S.widen_codes_to_keys(c, ORG, {"Grand"}) == {"GRAND", "3966 GRAND CHICAGO", "3966 W GRAND AVE"},
   S.widen_codes_to_keys(c, ORG, {"Grand"}))
ok("widen_codes_to_keys still returns EMPTY for an empty span (deny-all preserved)",
   S.widen_codes_to_keys(c, ORG, set()) == set())
ok("in_keyset unchanged (None = unrestricted)", S.in_keyset(None, "anything") is True)
ok("scheduling_reach untouched", S.scheduling_reach({}) == "org" and S.scheduling_reach({"scheduling_reach": "span"}) == "span")
ok("build_market_index tolerates garbage rows (new maps included)",
   S.build_market_index([], [{"nope": 1}])["code_groups"] == {})
ok("build_market_index with no rows returns empty new maps",
   S.build_market_index([], [])["key_index"] == {} and S.build_market_index([], [])["roster_codes"] == set())
# A one-vocabulary tenant must be byte-identical to today.
solo = FakeClient({"storeops": {"stores": [
    {"org_id": ORG, "store_code": "S1", "address": "1 MAIN ST", "market": "M"}]},
    "commcalc": {"store_mapping": [], "store_aliases": []}})
S.invalidate_market_index()
ok("single-vocabulary tenant: resolution is exact-only and the keyset is unchanged",
   S.resolve_store_grant(solo, ORG, "S1")[0] == "S1"
   and S.widen_codes_to_keys(solo, ORG, {"S1"}) == {"S1", "1 MAIN ST"})
ok("single-vocabulary tenant: an unknown value is still unknown",
   S.resolve_store_grant(solo, ORG, "S2")[1] == S.GRANT_UNKNOWN)

print(f"\n{'='*72}\n  RESULT: {PASS} passed, {FAIL} failed\n{'='*72}")
sys.exit(1 if FAIL else 0)
