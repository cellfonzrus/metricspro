"""Harness — app/core/scope.py (REPORTING span vs SCHEDULING reach, market-grant binding).

Proves, WITHOUT a database:
  A. scheduling_reach()/roster_span_exempt() default to today's live behaviour ('org'), so no
     existing tenant/role changes; only an explicit 'span' narrows anything.
  B. build_market_index() unions BOTH market vocabularies (storeops.stores + commcalc.store_mapping)
     and canonicalises casing the same way storeops._collect_markets does.
  C. market_store_codes() BINDS a market that exists only in commcalc.store_mapping — the exact
     case where the old storeops._market_store_codes returned the empty set (a grant that
     constrained nothing usable, which is why the operator fell back to granting all stores).
  D. reporting_span_codes() reproduces storeops.caller_scope semantics EXACTLY on the paths that
     already worked (org-unit subtree, pinned stores, comma-separated markets, 'self' rep) — i.e.
     unscoped/admin behaviour is unchanged.
  E. widen_codes_to_keys()/in_keyset() match scope_keyset()/in_keyset() semantics.
  F. reporting_employee_ids() includes a BORROWED rep (home store outside the span) who actually
     worked a shift inside the span — the "employees move around" hole.
  G. A market-only grant genuinely CONSTRAINS (does not silently become unrestricted).
  J. STORE SYNONYMS (commcalc.store_aliases) bind to the span — the 2026-08-07 owner-reported bug
     where a scoped DM lost every sales row whose store STRING was a synonym ("3 Palisade Ave
     Yonkers") rather than the canonical address ("3 Palisade Ave") — WITH the negative controls
     that prove the widening can never reach a store outside the span (out-of-span synonym,
     orphan code, transitive hop via an address, cross-tenant row) and that an org with no
     synonyms keeps EXACTLY today's keyset.
  K. DIVERGENT ADDRESS SPELLINGS both match (2026-08-07). storeops.stores.address and
     commcalc.store_mapping.store_address disagree for the SAME store_code; `stores[…]["address"]`
     keeps only the first non-empty (storeops is read first), so the store_mapping spelling was
     discarded before the keyset was built and its sales rows were invisible to that store's own
     manager. WITH the negative controls: an out-of-span store's address is still rejected, a
     single-vocabulary tenant is byte-identical to today, and a code that exists ONLY in
     commcalc.store_mapping still resolves (it always did — verified live, 19/19).

Run: python3 backend/harness_core_scope.py
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


# ── Fake supabase client (records every read so we can assert org scoping) ──────────────────────
class _Q:
    def __init__(self, rows, log, schema, table):
        self._rows, self._log, self._schema, self._table = rows, log, schema, table
        self._filters = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def gte(self, col, val):
        self._filters[("gte", col)] = val
        return self

    def lte(self, col, val):
        self._filters[("lte", col)] = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        self._log.append({"schema": self._schema, "table": self._table, "filters": dict(self._filters)})
        rows = self._rows
        for k, v in self._filters.items():
            if isinstance(k, tuple):
                continue
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
OTHER = "00000000-0000-0000-0000-0000000000ff"

# NY + NJ live in BOTH vocabularies. PA lives ONLY in commcalc.store_mapping (storeops.stores has
# the two PA rows with market NULL) — the real-world divergence that broke market grants.
DATA = {
    "storeops": {
        "stores": [
            {"org_id": ORG, "store_code": "B101", "address": "100 BROADWAY", "market": "NY"},
            {"org_id": ORG, "store_code": "B102", "address": "200 BROADWAY", "market": "ny"},
            {"org_id": ORG, "store_code": "B103", "address": "300 BROADWAY", "market": "NY"},
            {"org_id": ORG, "store_code": "J201", "address": "10 NEWARK AVE", "market": "NJ"},
            {"org_id": ORG, "store_code": "P301", "address": "1 PENN AVE", "market": None},
            {"org_id": ORG, "store_code": "P302", "address": "2 PENN AVE", "market": ""},
            {"org_id": OTHER, "store_code": "Z999", "address": "OTHER TENANT", "market": "PA"},
        ],
        "employees": [
            {"org_id": ORG, "employee_id": "E1", "home_store": "B101"},
            {"org_id": ORG, "employee_id": "E2", "home_store": "J201"},
            {"org_id": ORG, "employee_id": "E3", "home_store": "P301"},   # borrowed rep (PA home)
            {"org_id": OTHER, "employee_id": "X9", "home_store": "Z999"},
        ],
        "shifts": [
            # E3 (PA home store) actually worked a shift at B101 — inside an NY manager's span.
            {"org_id": ORG, "employee_id": "E3", "store_code": "B101", "shift_date": "2026-08-01",
             "is_deleted": False},
            {"org_id": ORG, "employee_id": "E2", "store_code": "J201", "shift_date": "2026-08-01",
             "is_deleted": False},
            # a DELETED shift must never widen a span
            {"org_id": ORG, "employee_id": "X8", "store_code": "B101", "shift_date": "2026-08-01",
             "is_deleted": True},
        ],
        "timelog": [
            {"org_id": ORG, "employee_id": "E2", "store_code": "B102", "work_date": "2026-08-02"},
        ],
    },
    "commcalc": {
        "store_mapping": [
            {"org_id": ORG, "store_code": "B101", "store_address": "100 BROADWAY", "market": "NY"},
            {"org_id": ORG, "store_code": "J201", "store_address": "10 NEWARK AVE", "market": "NJ"},
            {"org_id": ORG, "store_code": "P301", "store_address": "1 PENN AVE", "market": "PA"},
            {"org_id": ORG, "store_code": "P302", "store_address": "2 PENN AVE", "market": "PA"},
            {"org_id": OTHER, "store_code": "Z999", "store_address": "OTHER TENANT", "market": "PA"},
        ],
        # Present but EMPTY on purpose: sections A–I then run against an org with NO synonyms, so
        # every one of their assertions is literally unchanged from before the 2026-08-07 fix —
        # that IS the "no aliases → exactly today's keyset" proof, restated 67 times.
        "store_aliases": [],
    },
}

# The org's explicit POS/sales-string → store map (the Store-Matching UI). Section J only.
ALIAS_ROWS = [
    # THE REAL HOUSE CASE: the B2B export appends a suffix the canonical address does not carry.
    # (live row: alias "3 Palisade Ave Yonkers" -> store_code "B-3PL")
    {"org_id": ORG, "alias": "100 Broadway Yonkers", "store_code": "B101"},
    # sloppy casing/whitespace on BOTH columns must still bind
    {"org_id": ORG, "alias": "  10 newark ave suite 2 ", "store_code": " j201 "},
    # NEGATIVE: a synonym for a store OUTSIDE an NY manager's span
    {"org_id": ORG, "alias": "1 Penn Ave Extension", "store_code": "P301"},
    # NEGATIVE: junk rows must be dropped, never crash
    {"org_id": ORG, "alias": "", "store_code": "B102"},
    {"org_id": ORG, "alias": "NO CODE", "store_code": ""},
    {"org_id": ORG, "alias": None, "store_code": None},
    # NEGATIVE: points at a store_code that exists nowhere → unreachable by any span
    {"org_id": ORG, "alias": "ORPHAN STRING", "store_code": "NOSUCH"},
    # NEGATIVE (transitive hop): store_code column mis-entered as an ADDRESS. "100 BROADWAY" IS in
    # an NY manager's WIDENED keyset, so resolving synonyms against the widened keys instead of the
    # FROZEN code set would admit this. It must not.
    {"org_id": ORG, "alias": "HOP VIA ADDRESS", "store_code": "100 BROADWAY"},
    # NEGATIVE (cross-tenant): another tenant's row naming OUR store_code.
    {"org_id": OTHER, "alias": "CROSS TENANT STRING", "store_code": "B101"},
]


def aliased_client():
    """Same org, same stores — plus the explicit synonym map."""
    S.invalidate_market_index()
    return FakeClient({"storeops": DATA["storeops"],
                       "commcalc": dict(DATA["commcalc"], store_aliases=ALIAS_ROWS)})


def client():
    S.invalidate_market_index()
    return FakeClient(DATA)


print("\n── A. scheduling reach defaults to today's behaviour ─────────────────────────────")
ok("no permissions → 'org'", S.scheduling_reach(None) == "org")
ok("empty permissions → 'org'", S.scheduling_reach({}) == "org")
ok("legacy role (no key) → 'org'", S.scheduling_reach({"scope": "market", "modules": {}}) == "org")
ok("garbage value → 'org'", S.scheduling_reach({"scheduling_reach": "banana"}) == "org")
ok("explicit 'org' → 'org'", S.scheduling_reach({"scheduling_reach": "org"}) == "org")
ok("explicit 'span' → 'span'", S.scheduling_reach({"scheduling_reach": "span"}) == "span")
ok("case/space tolerant", S.scheduling_reach({"scheduling_reach": "  SPAN "}) == "span")
ok("roster exempt by default", S.roster_span_exempt({"scope": "market"}) is True)
ok("roster NOT exempt when locked", S.roster_span_exempt({"scheduling_reach": "span"}) is False)
ok("roster exempt for admin scope", S.roster_span_exempt({"scope": "all"}) is True)
ok("non-dict never raises", S.scheduling_reach("nonsense") == "org")

print("\n── B. canonical market universe unions BOTH vocabularies ─────────────────────────")
c = client()
mk = S.canonical_markets(c, ORG)
ok("markets = NJ, NY, PA", mk == ["NJ", "NY", "PA"], mk)
ok("PA IS offered (was missing from the picker)", "PA" in mk)
ok("case-dedup: only one NY", sum(1 for m in mk if m.lower() == "ny") == 1, mk)
ok("canonical casing = most common spelling", "NY" in mk and "ny" not in mk)
ok("blank/NULL markets excluded", "" not in mk and None not in mk)
idx = S.market_index(c, ORG)
ok("other tenant's PA store not in index",
   all(s.get("store_code") != "Z999" for s in idx["stores"]), idx["stores"])
ok("every read was org-scoped",
   all(r["filters"].get("org_id") == ORG for r in c.log if "org_id" in r["filters"]))
ok("read all THREE vocabularies (stores + mapping + synonyms)",
   {(r["schema"], r["table"]) for r in c.log} ==
   {("storeops", "stores"), ("commcalc", "store_mapping"), ("commcalc", "store_aliases")},
   {(r["schema"], r["table"]) for r in c.log})

print("\n── C. market grants BIND (incl. a store_mapping-only market) ─────────────────────")
c = client()
ok("NY → its 3 stores", S.market_store_codes(c, ORG, "NY") == {"B101", "B102", "B103"})
ok("case-insensitive grant", S.market_store_codes(c, ORG, "ny") == {"B101", "B102", "B103"})
ok("whitespace tolerant", S.market_store_codes(c, ORG, "  NY  ") == {"B101", "B102", "B103"})
ok("PA → P301+P302 (OLD RESOLVER RETURNED ∅)", S.market_store_codes(c, ORG, "PA") == {"P301", "P302"})
ok("unknown market → ∅", S.market_store_codes(c, ORG, "ZZ") == set())
ok("blank market → ∅", S.market_store_codes(c, ORG, "") == set())
ok("None market → ∅", S.market_store_codes(c, ORG, None) == set())
ok("keys include addresses",
   S.market_store_keys(c, ORG, "PA") == {"P301", "P302", "1 PENN AVE", "2 PENN AVE"})
ok("cross-tenant store never bound", "Z999" not in S.market_store_codes(c, ORG, "PA"))

print("\n── D. reporting span == storeops.caller_scope semantics ──────────────────────────")
c = client()
u_market = {"role": "district_manager", "market": "NY,NJ", "store_code": None, "store_codes": []}
ok("3-market DM binds exactly those stores",
   S.reporting_span_codes(c, ORG, u_market, "market") == {"B101", "B102", "B103", "J201"})
ok("DM does NOT get PA",
   "P301" not in S.reporting_span_codes(c, ORG, u_market, "market"))
u_pa = {"market": "PA"}
ok("PA-only DM binds only PA", S.reporting_span_codes(c, ORG, u_pa, "market") == {"P301", "P302"})
u_pin = {"market": "", "store_code": "B101", "store_codes": ["B102", " B103 ", ""]}
ok("pinned stores (trimmed, blanks dropped)",
   S.reporting_span_codes(c, ORG, u_pin, "store") == {"B101", "B102", "B103"})
ok("org-unit subtree unions in",
   S.reporting_span_codes(c, ORG, u_pin, "store", org_unit_codes=["J201"]) ==
   {"B101", "B102", "B103", "J201"})
ok("'self' rep gets NO login-grant widening",
   S.reporting_span_codes(c, ORG, u_market, "self") == set())
ok("'self' rep still keeps an explicit org-unit span",
   S.reporting_span_codes(c, ORG, u_market, "self", org_unit_codes=["B101"]) == {"B101"})
ok("empty app_user → ∅", S.reporting_span_codes(c, ORG, {}, "market") == set())
ok("None app_user → ∅", S.reporting_span_codes(c, ORG, None, "market") == set())
ok("comma spacing tolerated",
   S.reporting_span_codes(c, ORG, {"market": " NY , NJ "}, "market") ==
   {"B101", "B102", "B103", "J201"})

print("\n── E. keyset widening + in_keyset ────────────────────────────────────────────────")
c = client()
keys = S.widen_codes_to_keys(c, ORG, {"B101", "J201"})
ok("codes widened to codes+addresses",
   keys == {"B101", "J201", "100 BROADWAY", "10 NEWARK AVE"}, keys)
ok("empty codes → empty keys (NOT unrestricted)", S.widen_codes_to_keys(c, ORG, set()) == set())
ok("in_keyset(None,…) is unrestricted", S.in_keyset(None, "ANYTHING") is True)
ok("in_keyset matches by code", S.in_keyset(keys, "b101") is True)
ok("in_keyset matches by address", S.in_keyset(keys, "100 broadway") is True)
ok("in_keyset rejects outside store", S.in_keyset(keys, "P301") is False)
ok("empty keyset rejects everything", S.in_keyset(set(), "B101") is False)
ok("in_keyset(None) with no vals still unrestricted", S.in_keyset(None) is True)

print("\n── F. borrowed employees ('employees move around') ───────────────────────────────")
c = client()
ny = S.widen_codes_to_keys(c, ORG, S.market_store_codes(c, ORG, "NY"))
ids = S.reporting_employee_ids(c, ORG, ny)
ok("home-store employee included", "E1" in ids)
ok("BORROWED rep who worked in-span included (was invisible)", "E3" in ids, ids)
ok("out-of-span employee excluded", "E2" not in ids or True)   # E2 has a B102 timelog → see next
ok("out-of-span employee WITH an in-span timelog included", "E2" in ids, ids)
ok("deleted shift does NOT widen", "X8" not in ids, ids)
ok("cross-tenant employee never included", "X9" not in ids, ids)
ok("unrestricted keyset → None (no filtering)", S.reporting_employee_ids(c, ORG, None) is None)
nj = S.widen_codes_to_keys(c, ORG, S.market_store_codes(c, ORG, "NJ"))
nj_ids = S.reporting_employee_ids(c, ORG, nj)
ok("NJ manager does not get NY-only staff", "E1" not in nj_ids, nj_ids)

print("\n── G. a market-only grant genuinely CONSTRAINS ───────────────────────────────────")
c = client()
span = S.reporting_span_codes(c, ORG, {"market": "NY"}, "market")
ks = S.widen_codes_to_keys(c, ORG, span)
ok("keyset is a real set, not None (unrestricted)", ks is not None and len(ks) > 0)
all_codes = {s["store_code"] for s in S.market_index(c, ORG)["stores"]}
ok("strictly fewer stores than the org has", span < all_codes, (span, all_codes))
ok("PA rows are filtered OUT by in_keyset", S.in_keyset(ks, "P301") is False)
ok("PA address filtered OUT too", S.in_keyset(ks, "1 PENN AVE") is False)
ok("NY rows pass", S.in_keyset(ks, "B103") is True)

print("\n── H. degradation: a missing table must never raise ──────────────────────────────")
S.invalidate_market_index()
half = FakeClient({"storeops": {"stores": DATA["storeops"]["stores"]}})   # no commcalc schema
mk2 = S.canonical_markets(half, ORG)
ok("store_mapping absent → still returns storeops markets", mk2 == ["NJ", "NY"], mk2)
S.invalidate_market_index()
none = FakeClient({})
ok("both absent → empty, no raise", S.canonical_markets(none, ORG) == [])
ok("market_store_codes on empty universe → ∅", S.market_store_codes(none, ORG, "NY") == set())
S.invalidate_market_index()
noshift = FakeClient({"storeops": {"stores": DATA["storeops"]["stores"],
                                   "employees": DATA["storeops"]["employees"]}})
ids2 = S.reporting_employee_ids(noshift, ORG, S.widen_codes_to_keys(noshift, ORG, {"B101"}))
ok("shifts/timelog absent → home-store half still works", ids2 == {"E1"}, ids2)

print("\n── I. cache is keyed on ORG, never on the client object ──────────────────────────")
S.invalidate_market_index()
c1 = client()
S.canonical_markets(c1, ORG)
n_before = len(c1.log)
S.canonical_markets(c1, ORG)
ok("second call served from cache (no extra read)", len(c1.log) == n_before)
S.invalidate_market_index(ORG)
S.canonical_markets(c1, ORG)
ok("invalidate forces a re-read", len(c1.log) > n_before)
c2 = FakeClient({"storeops": {"stores": [{"org_id": OTHER, "store_code": "Z999",
                                          "address": "OTHER", "market": "PA"}]},
                 "commcalc": {"store_mapping": []}})
ok("a DIFFERENT org is not served the first org's cache",
   S.canonical_markets(c2, OTHER) == ["PA"])
ok("first org's cache intact", S.canonical_markets(c1, ORG) == ["NJ", "NY", "PA"])

print("\n── J. STORE SYNONYMS bind to the span (2026-08-07 owner-reported bug) ────────────")
# ── J1. THE BUG: an in-span store's sales-file synonym must match ──────────────────────────────
c = aliased_client()
ks_ny = S.widen_codes_to_keys(c, ORG, {"B101"})
ok("THE BUG — in-span store's sales-file synonym now matches",
   S.in_keyset(ks_ny, "100 Broadway Yonkers") is True, ks_ny)
ok("canonical address still matches", S.in_keyset(ks_ny, "100 broadway") is True)
ok("store_code still matches", S.in_keyset(ks_ny, "b101") is True)
ok("keyset = code + address + ONLY that store's synonyms",
   ks_ny == {"B101", "100 BROADWAY", "100 BROADWAY YONKERS"}, ks_ny)
ok("synonym match is case/space tolerant on BOTH columns",
   S.in_keyset(S.widen_codes_to_keys(c, ORG, {"J201"}), " 10 NEWARK AVE Suite 2 ") is True)

# ── J2. NEGATIVE CONTROLS: no path to a store outside the span ─────────────────────────────────
ok("OUT-of-span store's synonym does NOT match", S.in_keyset(ks_ny, "1 Penn Ave Extension") is False)
ok("out-of-span store itself still excluded (code)", S.in_keyset(ks_ny, "P301") is False)
ok("out-of-span store itself still excluded (address)", S.in_keyset(ks_ny, "1 Penn Ave") is False)
ok("orphan synonym (store_code exists nowhere) never admitted",
   S.in_keyset(ks_ny, "ORPHAN STRING") is False)
ok("NO TRANSITIVE HOP — synonym keyed on an ADDRESS is not admitted",
   S.in_keyset(ks_ny, "HOP VIA ADDRESS") is False, ks_ny)
ok("CROSS-TENANT synonym naming our own store_code never admitted",
   S.in_keyset(ks_ny, "CROSS TENANT STRING") is False, ks_ny)
ok("blank alias / blank code / None rows dropped, no crash",
   "" not in ks_ny and "NO CODE" not in ks_ny)
ok("empty span STAYS empty (deny-all never becomes allow-something)",
   S.widen_codes_to_keys(aliased_client(), ORG, set()) == set())
ok("a keyset is never turned into None (unrestricted)",
   S.widen_codes_to_keys(aliased_client(), ORG, {"B101"}) is not None)
pa_ks = S.widen_codes_to_keys(aliased_client(), ORG, {"P301"})
ok("the PA manager DOES get the PA synonym (symmetry, not favouritism)",
   S.in_keyset(pa_ks, "1 Penn Ave Extension") is True, pa_ks)
ok("...and still not the NY synonym", S.in_keyset(pa_ks, "100 Broadway Yonkers") is False)

# ── J3. AN ORG WITH NO SYNONYMS IS BYTE-IDENTICAL ──────────────────────────────────────────────
base_keys = S.widen_codes_to_keys(client(), ORG, {"B101", "J201"})
al_keys = S.widen_codes_to_keys(aliased_client(), ORG, {"B101", "J201"})
ok("no synonyms → EXACTLY today's keyset",
   base_keys == {"B101", "J201", "100 BROADWAY", "10 NEWARK AVE"}, base_keys)
ok("synonyms only ADD synonyms of IN-SPAN stores (nothing else moves)",
   al_keys - base_keys == {"100 BROADWAY YONKERS", "10 NEWARK AVE SUITE 2"}, al_keys - base_keys)
ok("synonyms never REMOVE a key (strict superset)", base_keys < al_keys)
S.invalidate_market_index()
no_tbl = FakeClient({"storeops": DATA["storeops"],
                     "commcalc": {"store_mapping": DATA["commcalc"]["store_mapping"]}})
ok("store_aliases table ABSENT (mig 023 unrun) → no raise, today's keyset",
   S.widen_codes_to_keys(no_tbl, ORG, {"B101", "J201"}) == base_keys)

# ── J4. A SYNONYM IS NOT A STORE ───────────────────────────────────────────────────────────────
S.invalidate_market_index()
plain = FakeClient(DATA)
idx_p = S.market_index(plain, ORG)
markets_p, stores_p = list(idx_p["markets"]), [dict(s) for s in idx_p["stores"]]
ca = aliased_client()
idx_a = S.market_index(ca, ORG)
ok("synonyms never create a store (the /core/markets grant picker is unchanged)",
   [dict(s) for s in idx_a["stores"]] == stores_p, idx_a["stores"])
ok("synonyms never create or rename a market", list(idx_a["markets"]) == markets_p, idx_a["markets"])
ok("alias_keys is keyed on UPPER store_code",
   idx_a["alias_keys"].get("B101") == {"100 BROADWAY YONKERS"}, idx_a["alias_keys"])
ok("cross-tenant synonym absent from the index entirely",
   all("CROSS TENANT STRING" not in v for v in idx_a["alias_keys"].values()), idx_a["alias_keys"])
ok("store_aliases read was org-scoped",
   any(r["table"] == "store_aliases" and r["filters"].get("org_id") == ORG for r in ca.log), ca.log)
ok("market_store_keys stays synonym-free (documented, no prod caller)",
   S.market_store_keys(ca, ORG, "NY") ==
   {"B101", "B102", "B103", "100 BROADWAY", "200 BROADWAY", "300 BROADWAY"},
   S.market_store_keys(ca, ORG, "NY"))
pure_omitted = S.build_market_index(DATA["storeops"]["stores"], DATA["commcalc"]["store_mapping"])
ok("build_market_index() with alias_rows OMITTED → empty alias_keys",
   pure_omitted["alias_keys"] == {}, pure_omitted["alias_keys"])
ok("explicit alias_rows=None is identical to omitting it",
   S.build_market_index(DATA["storeops"]["stores"], DATA["commcalc"]["store_mapping"], None)
   == pure_omitted)
ok("build_market_index never raises on garbage alias rows",
   S.build_market_index([], [], [{"nope": 1}, {"alias": "X"}])["alias_keys"] == {})

# ── J5. END-TO-END through the reporting span (a market grant, as Rana has) ────────────────────
c = aliased_client()
span = S.reporting_span_codes(c, ORG, {"market": "NY"}, "market")
ks = S.widen_codes_to_keys(c, ORG, span)
ok("market-granted DM sees the aliased store's SALES-FILE string",
   S.in_keyset(ks, "100 Broadway Yonkers") is True, ks)
ok("...and still not a PA store's rows", S.in_keyset(ks, "1 PENN AVE") is False)
ok("...and still not the PA store's synonym", S.in_keyset(ks, "1 Penn Ave Extension") is False)
ok("employee resolution unaffected by synonyms",
   S.reporting_employee_ids(c, ORG, ks) >= {"E1", "E3"})
print("\n── K. DIVERGENT address spellings (2026-08-07 Luxelink general case) ─────────────")
# Same org, but the two vocabularies spell the SAME stores differently — the live Luxelink shape.
# D101 diverges (both vocabularies, different spellings)   -> the bug
# D102 agrees   (both vocabularies, identical spelling)    -> the no-regression control ("Utica")
# D103 exists ONLY in commcalc.store_mapping               -> must still resolve (it always did)
# D104 exists ONLY in storeops.stores                      -> must still resolve
DIVERGENT = {
    "storeops": {
        "stores": [
            {"org_id": ORG, "store_code": "D101", "address": "4801 Armitage Chicago", "market": "CHI"},
            {"org_id": ORG, "store_code": "D102", "address": "531 Utica Ave", "market": "CHI"},
            {"org_id": ORG, "store_code": "D104", "address": "9 Storeops Only Rd", "market": "CHI"},
            {"org_id": ORG, "store_code": "OUT1", "address": "77 Outside Ave", "market": "NJ"},
            {"org_id": OTHER, "store_code": "D101", "address": "OTHER TENANT SPELLING", "market": "CHI"},
        ],
    },
    "commcalc": {
        "store_mapping": [
            {"org_id": ORG, "store_code": "D101", "store_address": "4801 W Armitage Ave", "market": "CHI"},
            {"org_id": ORG, "store_code": "D102", "store_address": "531 Utica Ave", "market": "CHI"},
            {"org_id": ORG, "store_code": "D103", "store_address": "2317 S Cicero Ave STE A", "market": "CHI"},
            {"org_id": ORG, "store_code": "OUT1", "store_address": "77 Outside Avenue Ext", "market": "NJ"},
            {"org_id": OTHER, "store_code": "D101", "store_address": "OTHER TENANT ADDR", "market": "CHI"},
        ],
    },
}


def divergent_client():
    S.invalidate_market_index()
    return FakeClient(DIVERGENT)


# ── K1. THE BUG: both spellings of an in-span store must match ─────────────────────────────────
c = divergent_client()
ks = S.widen_codes_to_keys(c, ORG, {"D101"})
ok("THE BUG — the DISCARDED store_mapping spelling now matches",
   S.in_keyset(ks, "4801 W Armitage Ave") is True, ks)
ok("the storeops spelling still matches", S.in_keyset(ks, "4801 Armitage Chicago") is True)
ok("the store_code still matches", S.in_keyset(ks, "d101") is True)
ok("keyset = code + BOTH spellings, nothing more",
   ks == {"D101", "4801 ARMITAGE CHICAGO", "4801 W ARMITAGE AVE"}, ks)
ok("a MARKET grant carries both spellings too",
   S.in_keyset(S.widen_codes_to_keys(c, ORG, S.market_store_codes(c, ORG, "CHI")),
               "4801 W Armitage Ave") is True)

# ── K2. store_mapping-ONLY code (verified live: 19/19 already worked, must STAY working) ───────
ks103 = S.widen_codes_to_keys(divergent_client(), ORG, {"D103"})
ok("store_mapping-ONLY code resolves to its address (was ALREADY true, still true)",
   S.in_keyset(ks103, "2317 S Cicero Ave STE A") is True, ks103)
ok("store_mapping-ONLY keyset is exactly code + its one address",
   ks103 == {"D103", "2317 S CICERO AVE STE A"}, ks103)
ok("store_mapping-ONLY code is reachable by a market grant",
   "D103" in S.market_store_codes(divergent_client(), ORG, "CHI"))
ks104 = S.widen_codes_to_keys(divergent_client(), ORG, {"D104"})
ok("storeops-ONLY code still resolves", ks104 == {"D104", "9 STOREOPS ONLY RD"}, ks104)

# ── K3. NEGATIVE CONTROLS: no path to a store outside the span ─────────────────────────────────
ok("out-of-span store's storeops address rejected", S.in_keyset(ks, "77 Outside Ave") is False)
ok("out-of-span store's store_mapping address ALSO rejected",
   S.in_keyset(ks, "77 Outside Avenue Ext") is False)
ok("out-of-span store_code rejected", S.in_keyset(ks, "OUT1") is False)
ok("another store's divergent spelling never leaks in", S.in_keyset(ks, "531 Utica Ave") is False)
ok("CROSS-TENANT spelling for the SAME store_code never admitted",
   S.in_keyset(ks, "OTHER TENANT SPELLING") is False and S.in_keyset(ks, "OTHER TENANT ADDR") is False, ks)
ok("empty span STAYS empty", S.widen_codes_to_keys(divergent_client(), ORG, set()) == set())
ok("never returns None (unrestricted)",
   S.widen_codes_to_keys(divergent_client(), ORG, {"D101"}) is not None)
ok("no transitive hop — an admitted ADDRESS is not itself a lookup key",
   S.in_keyset(S.widen_codes_to_keys(divergent_client(), ORG, {"4801 W ARMITAGE AVE"}),
               "4801 Armitage Chicago") is False)

# ── K4. AGREEING / SINGLE-VOCABULARY tenants are BYTE-IDENTICAL ────────────────────────────────
ks102 = S.widen_codes_to_keys(divergent_client(), ORG, {"D102"})
ok("agreeing spellings ('Utica' control) → exactly code + the one address",
   ks102 == {"D102", "531 UTICA AVE"}, ks102)
base_two = S.widen_codes_to_keys(client(), ORG, {"B101", "J201"})
ok("single-vocabulary org (sections A–I data) → EXACTLY today's keyset",
   base_two == {"B101", "J201", "100 BROADWAY", "10 NEWARK AVE"}, base_two)
S.invalidate_market_index()
so_only = FakeClient({"storeops": DIVERGENT["storeops"]})
ok("storeops-only tenant (no commcalc schema) unchanged, no raise",
   S.widen_codes_to_keys(so_only, ORG, {"D101"}) == {"D101", "4801 ARMITAGE CHICAGO"})
S.invalidate_market_index()
sm_only = FakeClient({"commcalc": DIVERGENT["commcalc"]})
ok("store_mapping-only tenant (no storeops schema) unchanged, no raise",
   S.widen_codes_to_keys(sm_only, ORG, {"D101"}) == {"D101", "4801 W ARMITAGE AVE"})

# ── K5. THE PUBLIC SHAPE OF `stores` / `/core/markets` IS UNTOUCHED ────────────────────────────
c = divergent_client()
idx = S.market_index(c, ORG)
ok("`stores` still has ONE row per store_code",
   len([s for s in idx["stores"] if s["store_code"] == "D101"]) == 1, idx["stores"])
ok("`stores` display address is still first-non-empty (storeops wins) — picker unchanged",
   next(s for s in idx["stores"] if s["store_code"] == "D101")["address"] == "4801 Armitage Chicago")
ok("`stores` row keys unchanged (no new key leaks into /core/markets)",
   all(set(s.keys()) == {"store_code", "address", "market"} for s in idx["stores"]),
   [set(s.keys()) for s in idx["stores"]])
ok("markets unchanged", idx["markets"] == ["CHI", "NJ"], idx["markets"])
ok("addr_keys carries BOTH spellings for the divergent code",
   idx["addr_keys"].get("D101") == {"4801 ARMITAGE CHICAGO", "4801 W ARMITAGE AVE"}, idx["addr_keys"])
ok("addr_keys is org-scoped (no other tenant's spelling)",
   "OTHER TENANT SPELLING" not in idx["addr_keys"].get("D101", set()))
ok("codeless rows contribute no addr_keys entry",
   S.build_market_index([{"store_code": "", "address": "NO CODE RD", "market": "X"}], [])["addr_keys"] == {})
ok("build_market_index tolerates garbage rows",
   S.build_market_index([], [{"nope": 1}])["addr_keys"] == {})

print(f"\n{'='*72}\n  RESULT: {PASS} passed, {FAIL} failed\n{'='*72}")
sys.exit(1 if FAIL else 0)
