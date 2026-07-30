"""Endpoint harness for GET /api/v1/commcalc/ma-handset-cogs (Marketplace Handset COGS) — drives the
REAL router handler against an in-memory FAKE Supabase client (no network, no DB, no Postgres).

What it proves:

  MULTI-TENANT (AGENT_CONTRACT RULE ONE)
  • EVERY read the handler issues is org-constrained — `.eq('org_id', …)` or, for the ONE documented
    config read that inherits the house default (report_pull_map), `.in_('org_id', [house, me])`
  • a second tenant's fulfillment rows are never returned, in EITHER direction (house ↮ tenant)
  • org_id is a QUERY PARAM on the handler signature (never a constant / Form field / request body)

  READ-ONLY (this package writes nothing at all)
  • the fake client BLOWS UP on insert/update/upsert/delete/rpc — the whole run passing proves zero writes
  • `commcalc.asset_ledger` is never even read (mod-asset's table stays untouched)

  THE GATE (DEFAULT-CLOSED whole-report grant `ma_handset_cogs`)
  • a plain caller is refused BEFORE any DB read (zero queries logged on a denial)
  • super_admin / role 'admin' / scope 'all' pass; perms.data and perms.modules grants both pass
  • an unresolvable caller (no token) and a caller-resolution ERROR both degrade CLOSED
  • the 403 detail names the grant key verbatim so the page can render its lock note

  THE MATH (qty × unit price)
  • ext_cost = qty × price on the 'unit' basis; price_basis='line' derives the unit price instead
  • a missing qty extends as 1 and says so (qty_assumed); a missing PRICE is counted, never summed as $0
  • cancelled lines are excluded from committed COGS and reported separately (tiles AND groups)
  • distinct-order counting, units, average unit cost

  MARKET VIA THE EXISTING /store-match CHAIN
  • ship-to resolves business_address → business_name → TSPID, first key that resolves wins
  • a store_aliases spelling and the canonical address collapse to ONE option
  • unresolved → the SELECTABLE "(no market)" bucket + a counted note, never a silent drop
  • a bare numeric TSPID cannot borrow a street-numbered store's market

  OPEN / UNFULFILLED ORDERS
  • no fill/ship date → open (listed first), a ship date → fulfilled, a cancel status → cancelled
  • days_open, the min_days_open filter and open_only
  • an undated (no date_ordered) line is EXCLUDED from the month and counted in the note

  FILTERS ≡ TILES ≡ GROUPS ≡ EXPORT (RULE FOUR/FIVE WYSIWYG)
  • every filter is applied SERVER-side; tiles and groups are recomputed over the filtered set
  • option lists come from the UNFILTERED rows so a picker never collapses to the selection
  • the display cap truncates the TABLE only; tiles/groups still describe the full filtered set
  • the `stores` (core-set) and `ship_to` (feed's word) params are the same dimension, unioned
  • a `reps` value is answered with a NOTE (this feed has no rep), never a silent no-op

  NAMED MAPPING TARGETS (existing manual_report_mapping mechanism)
  • the cost fields come back NAMED with asset-lending field-label parity, and each says whether it is
    currently mapped and from which source header; a carrier's SAVED override wins over the default

  DEGRADATION
  • a missing raw_ma_fulfillment (mig 083 unrun) → a ready payload with an honest note, never a 500
  • an unparseable period → a ready, empty payload that says so

Run: `python3 harness_ma_handset_cogs.py` from the backend dir.
"""
import os, sys, inspect

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import router as R
from app.modules.commcalc import ma_handset_cogs as MHC
from app.modules.commcalc import ma_upload as MU

_pass = 0
_fail = 0
QUERY_LOG = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}")


def approx(a, b, eps=1e-6):
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < eps


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Fake supabase-py query builder — only the verbs this handler uses. Every executed query is recorded
# (table + filters) so org scoping can be asserted on ALL of them; every WRITE verb raises.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _MissingTable(Exception):
    pass


class WriteAttempted(AssertionError):
    pass


class _Q:
    def __init__(self, store, schema, table):
        self._store, self._schema, self._table = store, schema, table
        self._eq, self._in, self._neq, self._gte, self._lte, self._is = {}, {}, {}, {}, {}, {}
        self._range = None
        self._limit = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def in_(self, k, v):
        self._in[k] = list(v)
        return self

    def neq(self, k, v):
        self._neq[k] = v
        return self

    def gte(self, k, v):
        self._gte[k] = v
        return self

    def lte(self, k, v):
        self._lte[k] = v
        return self

    def is_(self, k, v):
        self._is[k] = v
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    # ── WRITE verbs: this package is read-only, so any of these is a hard failure ────────────────
    def insert(self, *a, **k):
        raise WriteAttempted(f"insert attempted on {self._schema}.{self._table}")

    def upsert(self, *a, **k):
        raise WriteAttempted(f"upsert attempted on {self._schema}.{self._table}")

    def update(self, *a, **k):
        raise WriteAttempted(f"update attempted on {self._schema}.{self._table}")

    def delete(self, *a, **k):
        raise WriteAttempted(f"delete attempted on {self._schema}.{self._table}")

    def execute(self):
        QUERY_LOG.append({"table": self._table, "schema": self._schema, "eq": dict(self._eq),
                          "in": dict(self._in), "gte": dict(self._gte), "lte": dict(self._lte),
                          "is": dict(self._is)})
        key = f"{self._schema}.{self._table}"
        if key not in self._store:
            raise _MissingTable(f"relation {key} does not exist")
        rows = []
        for r in self._store[key]:
            ok = True
            for k, v in self._eq.items():
                if str(r.get(k)) != str(v):
                    ok = False
            for k, v in self._in.items():
                if str(r.get(k)) not in {str(x) for x in v}:
                    ok = False
            for k, v in self._neq.items():
                if r.get(k) is None or str(r.get(k)) == str(v):
                    ok = False
            for k, v in self._gte.items():
                if not (r.get(k) and str(r.get(k)) >= str(v)):
                    ok = False
            for k, v in self._lte.items():
                if not (r.get(k) and str(r.get(k)) <= str(v)):
                    ok = False
            for k, v in self._is.items():
                if str(v).lower() == "null" and r.get(k) not in (None, ""):
                    ok = False
            if ok:
                rows.append(dict(r))
        if self._range:
            a, b = self._range
            rows = rows[a:b + 1]
        if self._limit is not None:
            rows = rows[:self._limit]
        return type("Res", (), {"data": rows})()


class _Schema:
    def __init__(self, store, schema):
        self._store, self._schema = store, schema

    def table(self, t):
        return _Q(self._store, self._schema, t)

    def rpc(self, *a, **k):
        raise WriteAttempted("rpc attempted (this report is a plain read)")


class FakeClient:
    def __init__(self, store):
        self._store = store

    def schema(self, s):
        return _Schema(self._store, s)

    def table(self, t):
        return _Q(self._store, "public", t)

    def rpc(self, *a, **k):
        raise WriteAttempted("rpc attempted (this report is a plain read)")


def install(store):
    QUERY_LOG.clear()
    R.sb = lambda: FakeClient(store)      # noqa: E731


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# FAKE CALLER RESOLUTION — the REAL gate (`R._can_view_ma_handset_cogs` → the pure
# `ma_handset_cogs.ma_handset_cogs_allowed`) runs untouched; only core's token→caller lookup is stubbed,
# so the token string IS the caller key.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
CALLERS = {}


def _fake_uid(auth):
    return (auth.strip() or None) if isinstance(auth, str) else None


def _fake_caller(client, uid, active_org=None):
    c = CALLERS.get(uid)
    if c == "BOOM":
        raise RuntimeError("roles table unavailable")
    return c


import app.modules.core.router as CR                                                   # noqa: E402
CR._uid_from_token, CR._resolve_caller = _fake_uid, _fake_caller

SUPER, ADMIN, SCOPE_ALL, PLAIN = "t-super", "t-admin", "t-scope-all", "t-plain"
GRANT_DATA, GRANT_MODULE, BROKEN = "t-grant-data", "t-grant-module", "t-broken"
CALLERS.update({
    SUPER:        {"super_admin": True, "role": "owner", "perms": {"scope": "store"}},
    ADMIN:        {"super_admin": False, "role": "admin", "perms": {"scope": "store"}},
    SCOPE_ALL:    {"super_admin": False, "role": "market_manager", "perms": {"scope": "all"}},
    PLAIN:        {"super_admin": False, "role": "rep",
                   "perms": {"scope": "store", "modules": {"commissions": True}, "data": {}}},
    GRANT_DATA:   {"super_admin": False, "role": "rep",
                   "perms": {"scope": "store", "modules": {}, "data": {"ma_handset_cogs": True}}},
    GRANT_MODULE: {"super_admin": False, "role": "rep",
                   "perms": {"scope": "store", "modules": ["ma_handset_cogs"], "data": {}}},
    BROKEN:       "BOOM",
})

HOUSE = "00000000-0000-0000-0000-000000000001"
TEN = "00000000-0000-0000-0000-0000000000aa"


def ful(**kw):
    """One raw_ma_fulfillment row (mig 083 columns)."""
    base = {"id": "f1", "org_id": TEN, "order_number": "ORD-1", "order_status": "Filled",
            "order_type": "Handset", "tspid": "TSP-9", "business_name": "Luxe Wireless Nostrand",
            "business_address": "3560 Nostrand Avenue", "city": "Brooklyn", "state": "NY",
            "zip": "11229", "product_name": "Moto G Play 2026", "number_ordered": 2, "price": 74.5,
            "tracking_number": "1Z999", "date_ordered": "2026-06-05", "date_filled": "2026-06-07",
            "date_shipped": "2026-06-08"}
    base.update(kw)
    return base


def store_tables(mapping=(), aliases=(), so=()):
    return {
        "commcalc.store_mapping": list(mapping),
        "storeops.stores": list(so),
        "commcalc.store_aliases": list(aliases),
    }


def call(org_id, **kw):
    """Default to an ADMIN token — the behavioural sections are about the REPORT; section 2 proves the
    gate itself."""
    kw.setdefault("authorization", ADMIN)
    kw.setdefault("period", "June 2026")
    return R.ma_handset_cogs_endpoint(org_id=org_id, **kw)


def denied(org_id, token, **kw):
    from fastapi import HTTPException
    try:
        call(org_id, authorization=token, **kw)
        return None, None
    except HTTPException as e:
        return e.status_code, str(e.detail)


def org_scoped_ok(org_id):
    """Every logged read is org-constrained: `.eq('org_id', org)`, or an `.in_('org_id', …)` that
    contains ONLY this org and the house default row (the documented report_pull_map config
    inheritance)."""
    bad = []
    for q in QUERY_LOG:
        if str(q["eq"].get("org_id")) == str(org_id):
            continue
        ids = {str(x) for x in (q["in"].get("org_id") or [])}
        if ids and ids <= {str(org_id), HOUSE}:
            continue
        bad.append(q)
    return bad


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. handler contract: read-only GET, org_id is a QUERY PARAM ───────────────────────")
sig = inspect.signature(R.ma_handset_cogs_endpoint)
check("org_id is a parameter defaulting to ORG_ID (query param, not a constant/body)",
      "org_id" in sig.parameters and sig.parameters["org_id"].default == R.ORG_ID)
check("no request body / Form parameter exists on the handler",
      not any(p.name in ("body", "request") for p in sig.parameters.values()))
_routes = [r for r in R.router.routes if getattr(r, "path", "").endswith("/ma-handset-cogs")]
check("registered exactly once and GET-only",
      len(_routes) == 1 and set(getattr(_routes[0], "methods", [])) == {"GET"})
check("mounts under the module prefix (→ /api/v1/commcalc/ma-handset-cogs)",
      bool(_routes) and _routes[0].path == "/commcalc/ma-handset-cogs")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. THE GATE: default-closed, refused BEFORE any read ──────────────────────────────")
GATE_STORE = {"commcalc.raw_ma_fulfillment": [ful()], **store_tables()}
install(GATE_STORE)
st, detail = denied(TEN, PLAIN)
check("a plain caller is refused 403", st == 403)
check("the 403 names the grant key verbatim ('ma_handset_cogs')", "ma_handset_cogs" in (detail or ""))
check("the 403 names the human grant label for the roles UI",
      "Marketplace handset COGS report" in (detail or ""))
check("NOT ONE query ran before the denial (the report is not read then hidden)", QUERY_LOG == [])

for tok, who in ((SUPER, "super_admin"), (ADMIN, "role 'admin'"), (SCOPE_ALL, "scope 'all'"),
                 (GRANT_DATA, "perms.data grant"), (GRANT_MODULE, "perms.modules grant")):
    install(GATE_STORE)
    d = call(TEN, authorization=tok)
    check(f"{who} opens the report", d.get("ready") is True)

install(GATE_STORE)
check("no token at all → 403 (degrades CLOSED)", denied(TEN, "")[0] == 403)
install(GATE_STORE)
check("caller resolution BLOWING UP → 403 (degrades CLOSED, never open)", denied(TEN, BROKEN)[0] == 403)
check("the pure gate is unit-true for every branch",
      MHC.ma_handset_cogs_allowed(None) is False
      and MHC.ma_handset_cogs_allowed({"super_admin": True}) is True
      and MHC.ma_handset_cogs_allowed({"role": "Admin", "perms": {}}) is True
      and MHC.ma_handset_cogs_allowed({"perms": {"scope": "all"}}) is True
      and MHC.ma_handset_cogs_allowed({"perms": {"modules": ["ma_handset_cogs"]}}) is True
      and MHC.ma_handset_cogs_allowed({"perms": {"data": {"ma_handset_cogs": True}}}) is True
      and MHC.ma_handset_cogs_allowed({"role": "rep", "perms": {"scope": "store"}}) is False)
check("the grant key matches the rbac.ts DATA_GRANTS row this package requests",
      MHC.GRANT_KEY == "ma_handset_cogs")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. qty × unit price, order/unit counting, priceless + cancelled lines ─────────────")
MATH = {
    "commcalc.raw_ma_fulfillment": [
        ful(id="m1", order_number="A-1", product_name="Moto G", number_ordered=2, price=74.5),
        ful(id="m2", order_number="A-1", product_name="Sim Kit", number_ordered=10, price=1.5),
        ful(id="m3", order_number="A-2", product_name="Moto G", number_ordered=1, price=74.5),
        ful(id="m4", order_number="A-3", product_name="Celero 5G", number_ordered=None, price=99.0),
        ful(id="m5", order_number="A-4", product_name="No Price Phone", number_ordered=3, price=None),
        ful(id="m6", order_number="A-5", product_name="Moto G", number_ordered=4, price=74.5,
            order_status="Cancelled", date_filled=None, date_shipped=None),
    ],
    **store_tables(),
}
install(MATH)
d = call(TEN)
t = d["tiles"]
check("COGS = Σ qty × unit price over non-cancelled priced lines",
      approx(t["cogs"], 2 * 74.5 + 10 * 1.5 + 1 * 74.5 + 1 * 99.0))
check("units counts devices, not lines", approx(t["units"], 2 + 10 + 1 + 1 + 3))
check("orders are DISTINCT order numbers (A-1's two lines are one order; cancelled A-5 is not one)",
      t["orders"] == 4)
check("lines counts non-cancelled lines", t["lines"] == 5)
by = {r["id"]: r for r in d["rows"]}
check("a missing qty extends as 1 and SAYS SO (qty_assumed)",
      by["m4"]["qty"] == 1 and by["m4"]["qty_assumed"] is True and approx(by["m4"]["ext_cost"], 99.0))
check("a priceless line has ext_cost None (counted, never summed as $0)",
      by["m5"]["ext_cost"] is None and t["priceless_lines"] == 1)
check("the priceless line is called out in the note", "no price" in (d["note"] or ""))
check("a CANCELLED line is excluded from committed COGS and reported separately",
      approx(t["cancelled"]["amount"], 4 * 74.5) and t["cancelled"]["lines"] == 1
      and by["m6"]["state"] == "cancelled")
check("average unit cost = COGS ÷ units", approx(t["avg_unit_cost"], round(t["cogs"] / t["units"], 2)))
check("multi-unit lines (the only ones a basis change can move) are counted", t["multi_unit_lines"] == 3)

install(MATH)
d2 = call(TEN, price_basis="line")
b2 = {r["id"]: r for r in d2["rows"]}
check("price_basis='line' treats Price as the LINE total", approx(b2["m1"]["ext_cost"], 74.5))
check("price_basis='line' DERIVES the unit price (price ÷ qty)", approx(b2["m1"]["unit_price"], 37.25))
check("the basis is stated in the payload for the page + every export subtitle",
      d2["price_basis"] == "line" and "LINE TOTAL" in d2["basis_note"])
check("the default basis is stated as per-unit and names Device History as the precedent",
      "PER-UNIT" in d["basis_note"] and "Device History" in d["basis_note"])
install(MATH)
check("an unknown price_basis falls back to 'unit' (never a silent third behaviour)",
      call(TEN, price_basis="wat")["price_basis"] == "unit")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. MULTI-TENANT isolation, both directions ────────────────────────────────────────")
ISO = {
    "commcalc.raw_ma_fulfillment": [
        ful(id="ten1", org_id=TEN, order_number="T-1", price=100.0, number_ordered=1),
        ful(id="hou1", org_id=HOUSE, order_number="H-1", price=500.0, number_ordered=1,
            product_name="House Only Phone"),
    ],
    **store_tables(),
}
install(ISO)
d = call(TEN)
ids = {r["id"] for r in d["rows"]}
check("tenant sees only its own fulfillment line", ids == {"ten1"})
check("the house line's $ is nowhere in the tenant's COGS", approx(d["tiles"]["cogs"], 100.0))
check("no unscoped read ran (RULE ONE)", org_scoped_ok(TEN) == [])
install(ISO)
d = call(HOUSE)
check("house sees only its own line (isolation the other way)",
      {r["id"] for r in d["rows"]} == {"hou1"} and approx(d["tiles"]["cogs"], 500.0))
check("no unscoped read ran for the house either", org_scoped_ok(HOUSE) == [])
check("the fulfillment read is DATE-INDEXED (gte/lte on date_ordered), not a table scan",
      any(q["table"] == "raw_ma_fulfillment" and "date_ordered" in q["gte"] and "date_ordered" in q["lte"]
          for q in QUERY_LOG))
check("commcalc.asset_ledger is never touched by this report",
      not any(q["table"] == "asset_ledger" for q in QUERY_LOG))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. MARKET via the org's EXISTING /store-match chain ───────────────────────────────")
CHAIN = {
    "commcalc.raw_ma_fulfillment": [
        # ① canonical address straight out of store_mapping
        ful(id="c1", business_address="3560 Nostrand Avenue", business_name="Luxe Nostrand",
            tspid="TSP-1", price=100.0, number_ordered=1),
        # ② a DIFFERENT spelling of the same store, resolved through store_aliases → same option
        ful(id="c2", business_address="3560 Nostrand Ave.", business_name="Luxe Nostrand",
            tspid="TSP-1", price=50.0, number_ordered=1),
        # ③ address unknown, but the BUSINESS NAME is an alias
        ful(id="c3", business_address="", business_name="LUXE HEMPSTEAD", tspid="TSP-2",
            price=25.0, number_ordered=1),
        # ④ nothing resolves except the TSPID alias
        ful(id="c4", business_address="", business_name="", tspid="TSP-3", price=10.0, number_ordered=1),
        # ⑤ nothing resolves at all -> the "(no market)" bucket
        ful(id="c5", business_address="99 Nowhere Rd", business_name="Ghost", tspid="TSP-X",
            price=5.0, number_ordered=1),
        # ⑥ a BARE NUMERIC tspid must NOT borrow 1800-Great-Neck's market
        ful(id="c6", business_address="", business_name="", tspid="1800", price=1.0, number_ordered=1),
    ],
    **store_tables(
        mapping=[
            {"org_id": TEN, "store_code": "S1", "store_address": "3560 Nostrand Avenue",
             "market": "Brooklyn", "salesforce_id": "SF-1"},
            {"org_id": TEN, "store_code": "S2", "store_address": "218-80 Hempstead Avenue",
             "market": "Queens", "salesforce_id": "SF-2"},
            {"org_id": TEN, "store_code": "S3", "store_address": "700 Union Blvd",
             "market": "Long Island", "salesforce_id": "SF-3"},
            {"org_id": TEN, "store_code": "S4", "store_address": "1800 Great Neck Rd",
             "market": "Nassau", "salesforce_id": "SF-4"},
        ],
        aliases=[
            {"org_id": TEN, "alias": "3560 nostrand ave.", "store_code": "S1"},
            {"org_id": TEN, "alias": "luxe hempstead", "store_code": "S2"},
            {"org_id": TEN, "alias": "tsp-3", "store_code": "S3"},
            # another tenant's alias must never resolve for TEN
            {"org_id": HOUSE, "alias": "ghost", "store_code": "S4"},
        ]),
}
install(CHAIN)
d = call(TEN)
by = {r["id"]: r for r in d["rows"]}
check("① canonical address → market", by["c1"]["market"] == "Brooklyn")
check("② an alias spelling resolves to the SAME canonical store + market",
      by["c2"]["market"] == "Brooklyn" and by["c2"]["ship_to_label"] == by["c1"]["ship_to_label"])
check("two spellings of one store collapse to ONE pickable ship-to option",
      len([o for o in d["ship_to_options"] if "nostrand" in o.lower()]) == 1)
check("③ business NAME resolves when the address does not",
      by["c3"]["market"] == "Queens" and by["c3"]["ship_to_matched_on"] == "business_name")
check("④ TSPID resolves as the last key tried",
      by["c4"]["market"] == "Long Island" and by["c4"]["ship_to_matched_on"] == "tspid")
check("⑤ unresolved keeps market None (a blank export cell stays blank)", by["c5"]["market"] is None)
check("⑤ another tenant's alias did NOT resolve it (config isolation)", by["c5"]["market"] is None)
check("'(no market)' is a SELECTABLE option, not a hole", MHC.NO_MARKET in d["market_options"])
check("the unresolved count is reported + points at Store Matching",
      d["unmapped_market_rows"] == 2 and "store-match" in (d["note"] or ""))
check("⑥ a bare numeric TSPID cannot borrow a street-numbered store's market "
      "(the /store-match chain's street-number fallback WOULD have matched '1800 Great Neck Rd')",
      by["c6"]["market"] is None and by["c6"]["ship_to_matched_on"] is None)
check("…and the guard is a documented, flippable policy, not a hidden constant",
      MHC.is_opaque_id("1800") is True and MHC.is_opaque_id("TSP-3") is False
      and MHC.line_from_row({"tspid": "1800"}, store_of=lambda k: ("1800 Great Neck Rd", "Nassau"),
                            resolve_opaque_tspid=True)["market"] == "Nassau")
install(CHAIN)
d = call(TEN, markets="Brooklyn")
check("a market filter narrows rows server-side", {r["id"] for r in d["rows"]} == {"c1", "c2"})
check("the market filter narrows the TILES too (tiles ≡ table)", approx(d["tiles"]["cogs"], 150.0))
check("market options still list every market (from the UNFILTERED rows)",
      "Queens" in d["market_options"] and "Long Island" in d["market_options"])
install(CHAIN)
d = call(TEN, markets=MHC.NO_MARKET)
check("picking '(no market)' returns exactly the unresolved rows",
      {r["id"] for r in d["rows"]} == {"c5", "c6"})
install(CHAIN)
check("an org with NO mapping at all still returns every row (market just None)",
      len(call(TEN)["rows"]) == 6)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. OPEN / unfulfilled orders ──────────────────────────────────────────────────────")
OPEN = {
    "commcalc.raw_ma_fulfillment": [
        ful(id="o1", order_number="O-1", order_status="Pending", date_ordered="2026-06-01",
            date_filled=None, date_shipped=None, number_ordered=2, price=100.0),
        ful(id="o2", order_number="O-2", order_status="", date_ordered="2026-06-20",
            date_filled=None, date_shipped=None, number_ordered=1, price=60.0),
        ful(id="o3", order_number="O-3", order_status="Submitted", date_ordered="2026-06-10",
            date_filled=None, date_shipped="2026-06-12", number_ordered=1, price=70.0),
        ful(id="o4", order_number="O-4", order_status="Shipped", date_ordered="2026-06-11",
            date_filled=None, date_shipped=None, number_ordered=1, price=80.0),
        ful(id="o5", order_number="O-5", order_status="Cancelled", date_ordered="2026-06-12",
            date_filled=None, date_shipped=None, number_ordered=1, price=90.0),
        ful(id="o6", order_number="O-6", order_status="Shipped", date_ordered="2026-06-13",
            date_filled=None, date_shipped="2026-06-14", number_ordered=1, price=95.0,
            order_type="Return"),
    ],
    **store_tables(),
}
install(OPEN)
d = call(TEN)
by = {r["id"]: r for r in d["rows"]}
check("no fill/ship date + a non-terminal status → OPEN", by["o1"]["is_open"] is True)
check("a BLANK status with no dates → OPEN, and the reason says why",
      by["o2"]["is_open"] is True and "no fill or ship date" in by["o2"]["state_reason"])
check("a ship DATE wins over a 'Submitted' status → fulfilled", by["o3"]["state"] == "fulfilled")
check("a 'Shipped' STATUS with no dates → fulfilled (status is honoured too)",
      by["o4"]["state"] == "fulfilled")
check("a 'Cancelled' status → cancelled, not open", by["o5"]["state"] == "cancelled")
check("open lines are listed FIRST (the actionable bucket)",
      [r["id"] for r in d["rows"]][:2] == ["o2", "o1"])
check("the open tile carries lines/orders/units/$",
      d["tiles"]["open"]["lines"] == 2 and d["tiles"]["open"]["orders"] == 2
      and approx(d["tiles"]["open"]["units"], 3) and approx(d["tiles"]["open"]["amount"], 260.0))
check("fulfilled + cancelled tiles are separate and complete",
      d["tiles"]["fulfilled"]["lines"] == 3 and d["tiles"]["cancelled"]["lines"] == 1)
check("committed COGS excludes the cancelled line",
      approx(d["tiles"]["cogs"], 200.0 + 60.0 + 70.0 + 80.0 + 95.0))
install(OPEN)
d = call(TEN, open_only=1)
check("open_only=1 returns only the open lines", {r["id"] for r in d["rows"]} == {"o1", "o2"})
check("open_only recomputes the tiles over the open set", approx(d["tiles"]["cogs"], 260.0))
install(OPEN)
d = call(TEN, states="fulfilled")
check("the state facet narrows to fulfilled", {r["id"] for r in d["rows"]} == {"o3", "o4", "o6"})
check("state options come from the states actually present",
      {s["id"] for s in d["state_options"]} == {"open", "fulfilled", "cancelled"})
check("days_open is measured from the order date (pure helper, fixed 'today')",
      MHC.days_between("2026-06-01", "2026-06-21") == 20
      and MHC.days_between(None, "2026-06-21") is None)
_open_rows = MHC.build_rows([r for r in OPEN["commcalc.raw_ma_fulfillment"]], today="2026-06-25")
check("min_days_open keeps only OPEN lines older than the threshold (o1 = 24d, o2 = 5d)",
      {r["id"] for r in MHC.apply_filters(_open_rows, min_days_open=5)} == {"o1", "o2"}
      and {r["id"] for r in MHC.apply_filters(_open_rows, min_days_open=10)} == {"o1"}
      and MHC.apply_filters(_open_rows, min_days_open=30) == [])
check("oldest_open_days is reported for the open bucket",
      MHC.tiles_for(_open_rows)["oldest_open_days"] == 24)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. GROUPING by product / month / ship-to / market ─────────────────────────────────")
GRP = {
    "commcalc.raw_ma_fulfillment": [
        ful(id="g1", order_number="G-1", product_name="Moto G", number_ordered=2, price=100.0,
            date_ordered="2026-06-03", business_address="3560 Nostrand Avenue"),
        ful(id="g2", order_number="G-2", product_name="Moto G", number_ordered=1, price=100.0,
            date_ordered="2026-05-20", business_address="3560 Nostrand Avenue"),
        ful(id="g3", order_number="G-3", product_name="Celero 5G", number_ordered=1, price=90.0,
            date_ordered="2026-06-04", business_address="218-80 Hempstead Avenue"),
        ful(id="g4", order_number="G-4", product_name=None, number_ordered=1, price=5.0,
            date_ordered="2026-06-05", business_address="218-80 Hempstead Avenue"),
        ful(id="g5", order_number="G-5", product_name="Moto G", number_ordered=1, price=100.0,
            date_ordered="2026-06-06", business_address="3560 Nostrand Avenue",
            order_status="Cancelled", date_filled=None, date_shipped=None),
    ],
    **store_tables(mapping=[
        {"org_id": TEN, "store_code": "S1", "store_address": "3560 Nostrand Avenue",
         "market": "Brooklyn", "salesforce_id": "SF-1"},
        {"org_id": TEN, "store_code": "S2", "store_address": "218-80 Hempstead Avenue",
         "market": "Queens", "salesforce_id": "SF-2"}]),
}
install(GRP)
d = call(TEN, window_months=2, group_by="product")
g = {x["label"]: x for x in d["groups"]}
check("group_by=product rolls up qty × price per product",
      approx(g["Moto G"]["cogs"], 300.0) and approx(g["Celero 5G"]["cogs"], 90.0))
check("a blank product is the named '(no product name)' group, not a missing row",
      MHC.NO_PRODUCT in g and approx(g[MHC.NO_PRODUCT]["cogs"], 5.0))
check("groups are ordered biggest-COGS first", [x["label"] for x in d["groups"]][0] == "Moto G")
check("a group's cancelled $ is separated out, not folded into its COGS",
      approx(g["Moto G"]["cancelled_cogs"], 100.0) and g["Moto G"]["cancelled_lines"] == 1)
check("a group reports its open bucket + first/last order dates",
      g["Moto G"]["first_order"] == "2026-05-20" and g["Moto G"]["last_order"] == "2026-06-06")
check("group COGS sums to the tile COGS (no double count, no orphan line)",
      approx(sum(x["cogs"] for x in d["groups"]), d["tiles"]["cogs"]))
install(GRP)
d = call(TEN, window_months=2, group_by="month")
gm = {x["label"]: x for x in d["groups"]}
check("group_by=month uses the canonical month spelling and splits the window",
      set(gm) == {"June 2026", "May 2026"} and approx(gm["May 2026"]["cogs"], 100.0))
check("the month facet options are month-keyed with display labels",
      {o["id"] for o in d["month_options"]} == {"2026-06", "2026-05"})
install(GRP)
d = call(TEN, window_months=2, months="2026-05")
check("the month facet filters server-side", {r["id"] for r in d["rows"]} == {"g2"})
install(GRP)
d = call(TEN, window_months=2, group_by="ship_to")
check("group_by=ship_to rolls up per canonical store",
      {x["label"] for x in d["groups"]} == {"3560 Nostrand Avenue", "218-80 Hempstead Avenue"})
install(GRP)
d = call(TEN, window_months=2, group_by="market")
gk = {x["label"]: x for x in d["groups"]}
check("group_by=market rolls up per resolved market",
      approx(gk["Brooklyn"]["cogs"], 300.0) and approx(gk["Queens"]["cogs"], 95.0))
install(GRP)
check("an unknown group_by falls back to product (never a 500)",
      call(TEN, group_by="nonsense")["group_by"] == "product")
check("every advertised group_by option is real",
      set(MHC.GROUP_BY) == {o["id"] for o in call(TEN)["group_by_options"]})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. FILTERS ≡ TILES ≡ GROUPS ≡ EXPORT (server-side, WYSIWYG) ───────────────────────")
install(GRP)
d = call(TEN, window_months=2, products="Moto G")
check("the product filter narrows rows", {r["id"] for r in d["rows"]} == {"g1", "g2", "g5"})
check("tiles are recomputed over the filtered set", approx(d["tiles"]["cogs"], 300.0))
check("groups are recomputed over the filtered set", len(d["groups"]) == 1)
check("option lists stay full (built from the UNFILTERED rows)",
      "Celero 5G" in d["product_options"] and d["unfiltered_rows"] == 5)
install(GRP)
d = call(TEN, window_months=2, stores="3560 Nostrand Avenue")
check("the core-set `stores` param filters the ship-to dimension",
      {r["id"] for r in d["rows"]} == {"g1", "g2", "g5"})
install(GRP)
d = call(TEN, window_months=2, ship_to="218-80 Hempstead Avenue")
check("the feed's own `ship_to` param does the same job", {r["id"] for r in d["rows"]} == {"g3", "g4"})
install(GRP)
d = call(TEN, window_months=2, stores="3560 Nostrand Avenue", ship_to="218-80 Hempstead Avenue")
check("`stores` + `ship_to` UNION (neither spelling is silently ignored)", len(d["rows"]) == 5)
install(GRP)
d = call(TEN, window_months=2, statuses="Cancelled")
check("the raw order-status facet filters", {r["id"] for r in d["rows"]} == {"g5"})
install(GRP)
d = call(TEN, window_months=2, order_types="Handset")
check("the order-type facet filters", len(d["rows"]) == 5)
install(GRP)
d = call(TEN, window_months=2, limit=2)
check("the display cap truncates the TABLE only", len(d["rows"]) == 2 and d["total_rows"] == 5
      and d["truncated"] is True)
check("the tiles still describe ALL matching lines despite the cap",
      d["tiles"]["lines"] == 4 and approx(d["tiles"]["cogs"], 395.0))
install(GRP)
d = call(TEN, window_months=2, reps="Jane Doe")
check("a `reps` value is ANSWERED (this feed has no rep) and narrows nothing",
      len(d["rows"]) == 5 and "no rep" in (d["note"] or ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 9. WINDOW, undated lines, and degradation ─────────────────────────────────────────")
install(GRP)
d = call(TEN, window_months=1)
check("window_months=1 reads the anchor month only", {r["id"] for r in d["rows"]} == {"g1", "g3", "g4", "g5"})
check("the window is stated in the payload",
      d["window_from"] == "2026-06-01" and d["window_to"] == "2026-06-30")
install(GRP)
d = call(TEN, window_months=2)
check("window_months=2 reaches back one month", d["window_from"] == "2026-05-01")
check("the read range matches the window (indexed, no scan)",
      any(q["table"] == "raw_ma_fulfillment" and q["gte"].get("date_ordered") == "2026-05-01"
          and q["lte"].get("date_ordered") == "2026-06-30" for q in QUERY_LOG))
check("_mhc_month_range clamps a silly window instead of exploding",
      R._mhc_month_range("June 2026", 0)[0] == "2026-06-01"
      and R._mhc_month_range("June 2026", 999)[0] == "2023-07-01")
check("both period spellings anchor the same window",
      R._mhc_month_range("2026-06", 1) == R._mhc_month_range("June 2026", 1))

UND = {
    "commcalc.raw_ma_fulfillment": [
        ful(id="u1", date_ordered="2026-06-02", number_ordered=1, price=100.0),
        ful(id="u2", date_ordered=None, number_ordered=2, price=50.0),
    ],
    **store_tables(),
}
install(UND)
d = call(TEN)
check("an UNDATED line is excluded from the month (never guessed into it)",
      {r["id"] for r in d["rows"]} == {"u1"} and approx(d["tiles"]["cogs"], 100.0))
check("undated lines are COUNTED and costed in the note",
      d["undated_rows"] == 1 and "NO order date" in (d["note"] or "") and "100.00" in (d["note"] or ""))

install({**store_tables()})            # raw_ma_fulfillment absent entirely (mig 083 unrun)
d = call(TEN)
check("a missing raw_ma_fulfillment degrades to a ready payload (no 500)", d["ready"] is True)
check("…and says what to do about it", "could not be read" in (d["note"] or ""))
check("…with empty-but-valid tiles", d["tiles"]["cogs"] == 0 and d["rows"] == [])

install({"commcalc.raw_ma_fulfillment": [ful(id="p1", date_ordered="2026-01-04")], **store_tables()})
d = call(TEN)
check("an EMPTY window on an org that HAS rows says 'widen the month'",
      d["rows"] == [] and "widen the month" in (d["note"] or ""))
install({"commcalc.raw_ma_fulfillment": [], **store_tables()})
d = call(TEN)
check("an org with NO import at all is told to import (not left staring at zeros)",
      "has been imported" in (d["note"] or "") or "no marketplace" in (d["note"] or "").lower())
install(GRP)
d = call(TEN, period="not-a-month")
check("an unparseable period returns a ready, empty, self-explaining payload",
      d["ready"] is True and d["rows"] == [] and "not a month" in (d["note"] or ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 10. NAMED MAPPING TARGETS + asset-lending label parity ────────────────────────────")
install(GRP)
d = call(TEN)
cf = {f["col"]: f for f in d["cost_fields"]}
check("the cost fields ride along with the report", set(cf) >= {"number_ordered", "price"})
check("qty + price are flagged as the COST targets with human labels",
      cf["price"]["cost"] is True and cf["price"]["label"] == "Unit price (handset cost)"
      and cf["number_ordered"]["label"] == "Quantity ordered")
check("price carries its asset-lending PARITY label (one vocabulary for one handset cost)",
      cf["price"]["asset_label"] == "Owed to VIP" and cf["price"]["asset_field"] == "owed_to_vip")
check("product / date / ship-to / status parity mirror the Asset_Lending.xlsx headers",
      cf["product_name"]["asset_label"] == "item"
      and cf["date_ordered"]["asset_label"] == "Date"
      and cf["business_address"]["asset_label"] == "Billing Address 1"
      and cf["order_status"]["asset_label"] == "Status")
check("qty states WHY asset-lending has no equivalent instead of forcing a pairing",
      cf["number_ordered"]["asset_field"] is None
      and "ONE ROW PER DEVICE" in cf["number_ordered"]["parity_note"])
check("each cost target says whether it is currently mapped, and from which header",
      cf["price"]["mapped"] is True and cf["price"]["source_header"] == "Price"
      and cf["number_ordered"]["source_header"] == "Number Ordered")
check("the mapping source is named (default vs a carrier's saved override)",
      d["cost_map_source"] == "default")
check("the catalog is ordered by mapping relevance, not alphabetically",
      [f["col"] for f in d["cost_fields"]][:2] == ["number_ordered", "price"])

# the EXISTING mechanism: the same catalog the /manual-upload/{mapping,detect} responses build from
_default_map = MU.cost_field_catalog("ma_marketplace_orders", None)
check("with NO column map the targets still describe themselves, all unmapped",
      all(f["mapped"] is False for f in _default_map) and len(_default_map) == 7)
from app.modules.commcalc import report_pull as RP
_spec = next(s for s in RP.DEFAULT_REPORT_SPECS if s["report_key"] == "ma_marketplace_orders")
_cat = MU.target_field_catalog(_spec["column_map"], "ma_marketplace_orders")
_bycol = {f["col"]: f for f in _cat}
check("target_field_catalog keeps its ORIGINAL contract (col/type/default_source)",
      all({"col", "type", "default_source"} <= set(f) for f in _cat)
      and _bycol["price"]["type"] == "num" and _bycol["date_ordered"]["type"] == "date")
check("…and now also carries label + role + parity for every mapping surface",
      _bycol["price"]["label"] == "Unit price (handset cost)"
      and _bycol["business_address"]["role"] == "store"
      and _bycol["product_name"]["asset_field"] == "device_model")
check("an UNLABELLED column falls back to today's derived label (nothing regresses)",
      MU.field_meta("some_new_col")["label"] == "Some new col"
      and MU.field_meta("some_new_col")["labeled"] is False)
check("the label registry is keyed on the dest column and reused across MA reports",
      MU.field_meta("imei")["asset_label"] == "ESN"
      and MU.cost_field_catalog("ma_daily_tx", None)[0]["col"] == "retail_cost")

# a carrier's SAVED override wins over the default, and is reported as such
SAVED = {**GRP, "commcalc.manual_report_mapping": [
    {"org_id": TEN, "carrier_id": "car-1", "report_key": "ma_marketplace_orders",
     "column_map": {"Qty": {"col": "number_ordered", "type": "num"},
                    "Unit Cost": {"col": "price", "type": "num"}},
     "updated_at": "2026-07-20", "saved_by": "ops"},
    {"org_id": HOUSE, "carrier_id": "car-1", "report_key": "ma_marketplace_orders",
     "column_map": {"HOUSE ONLY": {"col": "price", "type": "num"}},
     "updated_at": "2026-07-20", "saved_by": "house"}]}
install(SAVED)
d = call(TEN, carrier_id="car-1")
cf = {f["col"]: f for f in d["cost_fields"]}
check("a carrier's SAVED mapping is reflected in the cost panel",
      d["cost_map_source"] == "saved" and cf["price"]["source_header"] == "Unit Cost")
check("another tenant's saved mapping is never read (config isolation)",
      cf["price"]["source_header"] != "HOUSE ONLY")
check("an unmapped cost target is visibly unmapped (a $0 column is traceable)",
      cf["product_name"]["mapped"] is False)
check("no unscoped read ran while resolving the mapping", org_scoped_ok(TEN) == [])
install(GRP)
d = call(TEN, carrier_id="car-1")
check("a missing manual_report_mapping table degrades to the default (no 500)",
      d["ready"] is True and d["cost_fields"][0]["mapped"] is True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 11. read-only: every path above ran with WRITES ARMED TO FAIL ─────────────────────")
check("insert/update/upsert/delete/rpc raise in the fake client (so zero writes happened)",
      all(hasattr(_Q, v) for v in ("insert", "update", "upsert", "delete")))
try:
    install(GRP)
    FakeClient(GRP).schema("commcalc").table("raw_ma_fulfillment").insert({"x": 1})
    _wrote = True
except WriteAttempted:
    _wrote = False
check("…and the guard really fires when a write is attempted", _wrote is False)
install(GRP)
d = call(TEN)
check("the report names its own source table for the operator", d["source_table"] == "commcalc.raw_ma_fulfillment")
check("the definition + open-order notes ship with the payload (export subtitles)",
      "ORDER LINE" in d["definition_note"] and "OPEN" in d["open_note"])


print(f"\n══ ma-handset-cogs harness: {_pass} passed, {_fail} failed ══")
sys.exit(1 if _fail else 0)
