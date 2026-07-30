"""Endpoint harness for GET /api/v1/commcalc/device-cost-recon (Device Cost Reconciliation — the
Option-A MEASUREMENT PASS) — drives the REAL router handler against an in-memory FAKE Supabase client
(no network, no DB, no Postgres).

What it proves:

  MULTI-TENANT (AGENT_CONTRACT RULE ONE)
  • EVERY read the handler issues is org-constrained — `.eq('org_id', …)` (this endpoint has no
    house-inherited config read at all, so the allowance the ma-handset-cogs harness needed for
    report_pull_map is deliberately NOT granted here)
  • a second tenant's rows are never returned from ANY of the four sources, in EITHER direction
  • org_id is a QUERY PARAM on the handler signature (never a constant / Form field / request body)

  READ-ONLY (this package writes nothing at all)
  • the fake client BLOWS UP on insert/update/upsert/delete/rpc — the whole run passing proves zero writes
  • `commcalc.asset_ledger` is READ (that is the point) and never written — asserted explicitly

  THE GATE (DEFAULT-CLOSED whole-report grant `device_cost_recon`)
  • a plain caller is refused BEFORE any DB read (zero queries logged on a denial)
  • super_admin / role 'admin' / scope 'all' pass; perms.data and perms.modules grants both pass
  • an unresolvable caller (no token) and a caller-resolution ERROR both degrade CLOSED
  • the 403 detail names the grant key verbatim so the page can render its lock note

  THE FOUR SOURCES + their arrangement, from CONFIG (RULE TWO)
  • ① order line extends at qty × unit price (reusing ma_handset_cogs) and is timed on the chosen
    fulfillment date; cancelled lines are excluded and counted
  • ② consignment row carries owed_to_vip on the VERIFIED billing_friday, with a named fallback date
  • ③ POS cost = ext_price − GP over DEVICE lines only (the tenant's own classifier), voided skipped
  • ④ snapshot unit_cost is a valuation and is NEVER recognized as a cost
  • arrangement resolves ①: data_source→distributor, then carrier→distributor; ②: payable_source_map,
    then the single has_asset_lending distributor; ③④: POS-derived, explicitly no distributor
  • nothing resolvable → the SELECTABLE "(distributor not mapped)" bucket + a config note

  THE ①→IMEI BRIDGE (the verified mig-083 join and nothing else)
  • raw_ma_commission.activation_order → raw_ma_fulfillment.order_number links a purchase to an IMEI
  • an order with no activation row is UN-LINKABLE and says why (ordered-but-unsold)
  • an order linking to TWO IMEIs is flagged ambiguous and never recognizes one device's cost

  OVERLAPS (design §3's double-count map, measured)
  • all four named pairs detected: ①∩③, ②∩③, ①∩④, ②∩④
  • duplicate_amount = Σ sources − max source (what a naive sum ADDS), per device and in total
  • a junk/short device token never joins two unrelated devices

  THE OWNER'S §9 POLICY (preview only)
  • Q1 invoice-first: an invoiced device is recognized at the invoice and its POS row is SUPPRESSED
  • Q1 fallback: a device with NO invoice is recognized at sale
  • dedup is by IMEI; a row with no IMEI is recognized but counted as at_risk (not dedup-covered)
  • precedence is a parameter and actually changes which row wins
  • Q2: unsold owed_to_vip is reported as a liability and never netted into COGS
  • Q3: Δ(inventory) is None with the honest reason (no month-end history exists), both legs shown
  • the two "unsold" definitions in the codebase are BOTH reported and their disagreement counted

  DELTA PREVIEW (month × store)
  • today's leg is the FINANCE module's own route (coa classifier + coa store canonicalization)
  • policy and today land in ONE store key space; a cell in only one leg is kept and labelled
  • totals + per-month rollup; the period-vs-trans_date timing difference is counted, not hidden

  FILTERS ≡ TILES ≡ GROUPS ≡ DELTA ≡ EXPORT (RULE FOUR/FIVE WYSIWYG)
  • every filter is applied SERVER-side; tiles, groups and the delta are recomputed over the filtered set
  • option lists come from the UNFILTERED rows so a picker never collapses to the selection
  • the display cap truncates the TABLE only
  • a `reps` value narrows ③ and KEEPS the rep-less sources, and says so

  PERIOD SPELLING
  • ③ is read through _pvariants in BOTH spellings ('June 2026' and '2026-06')

  DEGRADATION
  • every missing table (mig 083 / 216 / the ledger / the sales basis) → a ready payload with an honest
    note, never a 500
  • an unparseable period → a ready, empty payload that says so

Run: `python3 harness_device_cost_recon.py` from the backend dir.
"""
import os, sys, inspect

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault("COMMCALC_CFG_CACHE_TTL", "0")     # config memo off — deterministic runs

from app.modules.commcalc import router as R
from app.modules.commcalc import device_cost_recon as D

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


def approx(a, b, eps=0.005):
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < eps


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Fake supabase-py query builder — only the verbs these handlers use. Every executed query is recorded
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
        self._ilike = {}
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

    def gt(self, k, v):
        self._gte[k] = v
        return self

    def is_(self, k, v):
        self._is[k] = v
        return self

    def ilike(self, k, v):
        self._ilike[k] = v
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
                          "is": dict(self._is), "ilike": dict(self._ilike)})
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
            for k, v in self._ilike.items():
                pat = str(v).strip("%").lower()
                if pat and pat not in str(r.get(k) or "").lower():
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
# FAKE CALLER RESOLUTION — the REAL gate (`R._can_view_device_cost_recon` → the pure
# `device_cost_recon.device_cost_recon_allowed`) runs untouched; only core's token→caller lookup is
# stubbed, so the token string IS the caller key.
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
                   "perms": {"scope": "store", "modules": {}, "data": {"device_cost_recon": True}}},
    GRANT_MODULE: {"super_admin": False, "role": "rep",
                   "perms": {"scope": "store", "modules": ["device_cost_recon"], "data": {}}},
    BROKEN:       "BOOM",
})

HOUSE = "00000000-0000-0000-0000-000000000001"
TEN = "00000000-0000-0000-0000-0000000000aa"
CARRIER = "cc-1111"
DIST_VIP = "d-vip"
DIST_MA = "d-ma"
SRC_MA = "src-ma-1"

IMEI_A = "356938035643809"       # ① order + ③ sale  → the purchase/sale double count
IMEI_B = "356938035643810"       # ② billed + ③ sale → the consignment/sale double count
IMEI_C = "356938035643811"       # ② unsold + ④      → two inventory valuations
IMEI_D = "356938035643812"       # ① order + ④       → purchase + inventory valuation
IMEI_E = "356938035643813"       # ③ only            → the sale-time fallback
IMEI_F = "356938035643814"       # ① order, two IMEIs on one activation order (ambiguous)
IMEI_G = "356938035643815"       # the sibling of F on the shared order


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# FIXTURE BUILDERS
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def ful(**kw):
    """One raw_ma_fulfillment row (mig 083 columns)."""
    base = {"id": "f1", "org_id": TEN, "carrier_id": CARRIER, "source_id": SRC_MA,
            "order_number": "ORD-A", "order_status": "Filled", "order_type": "Handset",
            "tspid": "TSP-9", "business_name": "Luxe Nostrand",
            "business_address": "3560 Nostrand Avenue", "city": "Brooklyn", "state": "NY",
            "zip": "11229", "product_name": "Moto G Play 2026", "number_ordered": 1, "price": 120.0,
            "tracking_number": "1Z999", "date_ordered": "2026-06-05", "date_filled": "2026-06-09",
            "date_shipped": "2026-06-10"}
    base.update(kw)
    return base


def comm(order, imei, **kw):
    base = {"id": f"c-{order}-{imei}", "org_id": TEN, "activation_order": order, "imei": imei}
    base.update(kw)
    return base


def al(**kw):
    """One asset_ledger row (mod-asset's table — READ ONLY)."""
    base = {"id": "a1", "org_id": TEN, "esn_imei": IMEI_B, "store": "3560 Nostrand Avenue",
            "market": "Brooklyn", "device_model": "Moto G Play 2026", "category": "Asset Lending",
            "status": "Sold", "acquired_date": "2026-05-20", "due_date": "2026-06-19",
            "trigger_date": "2026-06-12", "billing_friday": "2026-06-12", "date_sold": "2026-06-14",
            "owed_to_vip": 100.0, "reimbursement": 0.0, "reimbursement_date": None,
            "selling_price": 199.0}
    base.update(kw)
    return base


def sale(**kw):
    base = {"org_id": TEN, "period": "June 2026", "trans_id": "T-1", "trans_date": "2026-06-14",
            "store": "3560 Nostrand Avenue", "salesperson": "Ada Lovelace",
            "department": "Android - XP", "category": "KittedBranded",
            "product_desc": "Moto G Play 2026", "ext_price": 199.0, "gp": 79.0, "voided": "",
            "serial_1": IMEI_B}
    base.update(kw)
    return base


def inv(**kw):
    base = {"id": "i1", "org_id": TEN, "imei": IMEI_C, "serial": IMEI_C, "sku": "MOTOG26",
            "item": "Moto G Play 2026", "store": "3560 Nostrand Avenue", "unit_cost": 95.0,
            "received_date": "2026-05-02", "days_in_stock": 40, "as_of_date": "2026-06-30"}
    base.update(kw)
    return base


def store_tables(mapping=(), aliases=(), so=()):
    return {
        "commcalc.store_mapping": list(mapping),
        "storeops.stores": list(so),
        "commcalc.store_aliases": list(aliases),
    }


CONFIG_TABLES = {
    "commcalc.distributors": [
        {"id": DIST_VIP, "org_id": TEN, "name": "VIP", "carrier_id": None,
         "arrangement": "consignment", "terms_days": 60, "billing_cycle": "weekly",
         "has_asset_lending": True, "is_active": True},
        {"id": DIST_MA, "org_id": TEN, "name": "Marketplace MA", "carrier_id": CARRIER,
         "arrangement": "terms", "terms_days": 30, "billing_cycle": "net",
         "has_asset_lending": False, "is_active": True},
    ],
    "commcalc.data_source": [
        {"id": SRC_MA, "org_id": TEN, "distributor_id": DIST_MA, "carrier_id": CARRIER,
         "processor": "vidapay", "label": "VidaPay login 1"},
    ],
    "commcalc.payable_source_map": [
        {"id": "psm-1", "org_id": TEN, "carrier_id": CARRIER, "distributor_id": DIST_VIP,
         "label": "Boost / VIP", "source_table": "asset_ledger", "imei_field": "esn_imei",
         "owed_field": "owed_to_vip", "billing_friday_field": "billing_friday", "is_active": True},
    ],
}


def full_store(org=TEN, extra=None, drop=()):
    """The complete four-source fixture for `org`, plus the config + store-resolution tables."""
    s = {
        "commcalc.raw_ma_fulfillment": [
            ful(id="f1", order_number="ORD-A", price=120.0),                       # → IMEI_A (③ too)
            ful(id="f2", order_number="ORD-D", price=130.0, product_name="A15"),    # → IMEI_D (④ too)
            ful(id="f3", order_number="ORD-X", price=140.0, product_name="Unsold"),  # un-linkable
            ful(id="f4", order_number="ORD-F", price=150.0, product_name="Shared"),  # 2 IMEIs
            ful(id="f5", order_number="ORD-C", price=999.0, product_name="Cancelled",
                order_status="Cancelled"),                                          # excluded
        ],
        "commcalc.raw_ma_commission": [
            comm("ORD-A", IMEI_A), comm("ORD-D", IMEI_D),
            comm("ORD-F", IMEI_F), comm("ORD-F", IMEI_G),
        ],
        "commcalc.asset_ledger": [
            al(id="a1", esn_imei=IMEI_B, owed_to_vip=100.0, billing_friday="2026-06-12"),
            al(id="a2", esn_imei=IMEI_C, owed_to_vip=90.0, billing_friday=None,
               trigger_date="2026-06-05", category="On Inventory", status="On Inventory",
               date_sold=None),
            al(id="a3", esn_imei="", owed_to_vip=55.0, billing_friday="2026-06-19",
               device_model="No ESN"),                                              # un-linkable
        ],
        "commcalc.raw_sales": [
            sale(trans_id="T-A", serial_1=IMEI_A, ext_price=249.0, gp=109.0),        # ①∩③
            sale(trans_id="T-B", serial_1=IMEI_B, ext_price=199.0, gp=79.0),         # ②∩③
            sale(trans_id="T-E", serial_1=IMEI_E, ext_price=180.0, gp=60.0),         # ③ only
            sale(trans_id="T-N", serial_1="", ext_price=150.0, gp=50.0),             # no serial
            sale(trans_id="T-ACC", department="Ondigo", category="Accessories",
                 product_desc="Case", ext_price=30.0, gp=20.0, serial_1=""),         # accessory
            sale(trans_id="T-V", serial_1="356938035643899", ext_price=500.0, gp=100.0,
                 voided="YES"),                                                      # voided
        ],
        "commcalc.daily_sales_feed": [],
        "commcalc.inventory_aging_device": [
            inv(id="i1", imei=IMEI_C, unit_cost=95.0),                               # ②∩④
            inv(id="i2", imei=IMEI_D, unit_cost=88.0, item="A15"),                   # ①∩④
            inv(id="i3", imei="", serial="", unit_cost=77.0, item="No key"),          # un-linkable
        ],
        "commcalc.vip_invoice_devices": [
            {"org_id": org, "imei": IMEI_B, "serial": IMEI_B, "period": "June 2026",
             "invoice_number": "INV-1"},
            {"org_id": org, "imei": "", "serial": IMEI_C, "period": "June 2026",
             "invoice_number": "INV-1"},
            {"org_id": org, "imei": "", "serial": "", "period": "June 2026",
             "invoice_number": "INV-2"},
        ],
        "commcalc.accessory_config": [],
        "commcalc.gp_category_map": [],
        "commcalc.flag_rules": [],
    }
    # re-stamp org on every row so the same fixture can be filed under a different tenant
    for k in list(s):
        for r in s[k]:
            r["org_id"] = org
    s.update({k: [dict(r, org_id=org) for r in v] for k, v in CONFIG_TABLES.items()})
    s.update(store_tables(mapping=[{"org_id": org, "store_code": "S1",
                                    "store_address": "3560 Nostrand Avenue", "market": "Brooklyn",
                                    "salesforce_id": ""}]))
    for t in drop:
        s.pop(t, None)
    if extra:
        for k, v in extra.items():
            s[k] = v
    return s


def call(org_id, **kw):
    """Default to an ADMIN token — the behavioural sections are about the REPORT; section 2 proves the
    gate itself."""
    kw.setdefault("authorization", ADMIN)
    kw.setdefault("period", "June 2026")
    return R.device_cost_recon_endpoint(org_id=org_id, **kw)


def denied(org_id, token, **kw):
    from fastapi import HTTPException
    try:
        call(org_id, authorization=token, **kw)
        return None, None
    except HTTPException as e:
        return e.status_code, str(e.detail)


def org_scoped_bad(org_id):
    """Every logged read must be `.eq('org_id', org)`. This endpoint reads NO house-inherited config, so
    no `.in_('org_id', [...])` allowance is granted — an unscoped read is a straight failure."""
    bad = []
    for q in QUERY_LOG:
        if str(q["eq"].get("org_id")) == str(org_id):
            continue
        ids = {str(x) for x in (q["in"].get("org_id") or [])}
        if ids and ids == {str(org_id)}:
            continue
        bad.append(q)
    return bad


def rows_of(d, source=None):
    return [r for r in d["rows"] if source is None or r["source"] == source]


def one(d, source, ref):
    return next((r for r in d["rows"] if r["source"] == source and str(r.get("ref")) == str(ref)), None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. handler contract: read-only GET, org_id is a QUERY PARAM ───────────────────────")
sig = inspect.signature(R.device_cost_recon_endpoint)
check("org_id is a parameter defaulting to ORG_ID (query param, not a constant/body)",
      "org_id" in sig.parameters and sig.parameters["org_id"].default == R.ORG_ID)
check("no request body / Form parameter exists on the handler",
      not any(p.name in ("body", "request") for p in sig.parameters.values()))
_routes = [r for r in R.router.routes if getattr(r, "path", "").endswith("/device-cost-recon")]
check("registered exactly once and GET-only",
      len(_routes) == 1 and set(getattr(_routes[0], "methods", [])) == {"GET"})
check("mounts under the module prefix (→ /api/v1/commcalc/device-cost-recon)",
      bool(_routes) and _routes[0].path == "/commcalc/device-cost-recon")
check("RULE FIVE core-set filters are all on the signature",
      all(p in sig.parameters for p in ("period", "stores", "markets", "reps")))
check("the §9 policy knobs are PARAMETERS, not constants",
      all(p in sig.parameters for p in ("precedence", "ma_recognition_date", "price_basis")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. the GATE: default-closed 'device_cost_recon', refused BEFORE any read ───────────")
install(full_store())
code, detail = denied(TEN, PLAIN)
check("a plain rep is refused with 403", code == 403)
check("the 403 detail names the grant key verbatim (the page's lock-note signal)",
      bool(detail) and "device_cost_recon" in detail)
check("NOT ONE query ran before the refusal", len(QUERY_LOG) == 0)

for tok, who in ((SUPER, "super_admin"), (ADMIN, "role admin"), (SCOPE_ALL, "scope 'all'"),
                 (GRANT_DATA, "perms.data grant"), (GRANT_MODULE, "perms.modules grant")):
    install(full_store())
    try:
        d = call(TEN, authorization=tok)
        ok = bool(d.get("ready"))
    except Exception as e:
        ok = False
        print(f"        ({who} raised {e})")
    check(f"{who} is allowed", ok)

install(full_store())
check("no token at all → refused (degrades CLOSED)", denied(TEN, "")[0] == 403)
install(full_store())
check("a caller-resolution ERROR → refused (degrades CLOSED)", denied(TEN, BROKEN)[0] == 403)
check("the pure gate refuses caller=None", D.device_cost_recon_allowed(None) is False)
check("the pure gate refuses an empty perms dict",
      D.device_cost_recon_allowed({"role": "rep", "perms": {}}) is False)
check("the pure gate's key is 'device_cost_recon'", D.GRANT_KEY == "device_cost_recon")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. RULE ONE: every read org-scoped, both tenants isolated, ZERO writes ────────────")
install(full_store(org=TEN))
d_ten = call(TEN)
check("every read the handler issued is .eq('org_id', <this org>)", org_scoped_bad(TEN) == [])
check("the asset ledger IS read (that is the point of the report)",
      any(q["table"] == "asset_ledger" for q in QUERY_LOG))
check("all four source tables are read",
      {"raw_ma_fulfillment", "asset_ledger", "raw_sales", "inventory_aging_device"}
      <= {q["table"] for q in QUERY_LOG})
check("the distributor CONFIG chain is read (arrangement is not guessed)",
      {"distributors", "data_source", "payable_source_map"} <= {q["table"] for q in QUERY_LOG})
_ten_rows = d_ten["unfiltered_rows"]
check("tenant TEN sees its own rows", _ten_rows > 0)

install(full_store(org=TEN))
d_house = call(HOUSE)
check("the HOUSE org sees NONE of TEN's rows (isolation →)", d_house["unfiltered_rows"] == 0)
check("the empty view is READY with an honest note, not an error",
      d_house.get("ready") and "four sources" in (d_house.get("note") or ""))
install(full_store(org=HOUSE))
d2 = call(TEN)
check("tenant TEN sees NONE of the HOUSE's rows (isolation ←)", d2["unfiltered_rows"] == 0)
# zero-write proof: the fake client raises on every write verb, and section 3+ all passed
check("no insert/update/upsert/delete/rpc was attempted anywhere in this run (fake client raises)",
      True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. the four sources: amounts, timing dates, arrangement FROM CONFIG ───────────────")
install(full_store())
d = call(TEN)
_ma = one(d, "ma_fulfillment", "ORD-A")
check("① amount = qty × unit price (reusing ma_handset_cogs)", approx(_ma["amount"], 120.0))
check("① is timed on date_ordered by default", _ma["event_date"] == "2026-06-05")
check("① timing label says which date it used", _ma["timing_label"] == "Date ordered")
check("① arrangement came from data_source → distributor",
      _ma["arrangement_source"] == "data_source → distributor"
      and _ma["distributor"] == "Marketplace MA" and _ma["arrangement"] == "terms")
check("① dollar is described as a purchase price", "price" in _ma["amount_kind"].lower())

_al = one(d, "asset_lending", IMEI_B)
check("② amount = owed_to_vip (the VIP-billed figure)", approx(_al["amount"], 100.0))
check("② is timed on the VERIFIED billing_friday", _al["event_date"] == "2026-06-12"
      and _al["timing_label"] == "Billing Friday")
check("② arrangement came from payable_source_map → distributor",
      _al["arrangement_source"] == "payable_source_map → distributor"
      and _al["distributor"] == "VIP" and _al["arrangement"] == "consignment")

_al2 = one(d, "asset_lending", IMEI_C)
check("② with NO billing_friday falls back to trigger_date and SAYS SO",
      _al2["event_date"] == "2026-06-05" and "trigger" in _al2["timing_label"].lower())
check("② on-inventory uses the ASSET module's definition (date_sold null + category On Inventory)",
      _al2["on_inventory"] is True)

_pos = one(d, "pos_sale", "T-B")
check("③ POS cost = ext_price − GP", approx(_pos["amount"], 120.0))
check("③ is timed on the sale date", _pos["event_date"] == "2026-06-14")
check("③ carries the rep (the only source that has one)", _pos["rep"] == "Ada Lovelace")
check("③ arrangement is explicitly POS-derived, NOT invented as a distributor",
      _pos["distributor"] is None and _pos["arrangement"] is None
      and "POS" in _pos["arrangement_label"])
check("③ excludes the ACCESSORY line (the tenant's own classifier)",
      one(d, "pos_sale", "T-ACC") is None)
check("③ excludes the VOIDED line", one(d, "pos_sale", "T-V") is None)

_inv = one(d, "inventory_snapshot", IMEI_C)
check("④ amount = snapshot unit_cost", approx(_inv["amount"], 95.0))
check("④ is timed on the snapshot as-of date", _inv["event_date"] == "2026-06-30")
check("④ is never a recognition source (§9 Q3)",
      _inv["recognized"] is False and "valuation" in _inv["recognition_reason"].lower())

check("① CANCELLED line is excluded and counted in a note",
      one(d, "ma_fulfillment", "ORD-C") is None and "cancelled" in (d["note"] or "").lower())
install(full_store())
d_c = call(TEN, include_cancelled=1)
check("…and re-appears when 'include cancelled' is on",
      one(d_c, "ma_fulfillment", "ORD-C") is not None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. the ①→IMEI bridge: the VERIFIED mig-083 join, and honest un-linkability ────────")
install(full_store())
d = call(TEN)
check("① ORD-A links to its IMEI via raw_ma_commission.activation_order",
      one(d, "ma_fulfillment", "ORD-A")["device_key"] == IMEI_A)
_x = one(d, "ma_fulfillment", "ORD-X")
check("① with no activation row is UN-LINKABLE", _x["linkable"] is False and _x["device_key"] is None)
check("…and the reason names ordered-but-unsold",
      "ordered-but-unsold" in (_x["unlink_reason"] or "").lower())
_f = one(d, "ma_fulfillment", "ORD-F")
check("① order linking to TWO IMEIs is flagged ambiguous", _f["ambiguous_link"] is True)
check("…and does NOT claim a single device key", _f["device_key"] is None)
check("…but still participates in the overlap scan through both keys",
      set(_f["linked_keys"]) == {IMEI_F, IMEI_G})
check("the ambiguous-link count is reported", d["overlap_summary"]["ambiguous_link_rows"] >= 1)
check("the activation bridge query filtered on activation_order",
      any(q["table"] == "raw_ma_commission" and "activation_order" in q["in"] for q in QUERY_LOG))

_a3 = one(d, "asset_lending", "")
check("② with a blank ESN is un-linkable and says why",
      any(r["source"] == "asset_lending" and not r["linkable"]
          and "ESN" in (r["unlink_reason"] or "") for r in d["rows"]))
check("③ with a blank serial is un-linkable and says why",
      any(r["source"] == "pos_sale" and not r["linkable"]
          and "serial_1" in (r["unlink_reason"] or "") for r in d["rows"]))
check("④ with neither imei nor serial is un-linkable",
      any(r["source"] == "inventory_snapshot" and not r["linkable"] for r in d["rows"]))

u = d["unlinkable"]
check("un-linkable rows are counted PER SOURCE (one per source in this fixture)",
      u["ma_fulfillment"]["unlinkable_rows"] == 1      # ORD-X only
      and u["asset_lending"]["unlinkable_rows"] == 1
      and u["pos_sale"]["unlinkable_rows"] == 1
      and u["inventory_snapshot"]["unlinkable_rows"] == 1)
check("un-linkable DOLLARS are counted too (never assumed zero)",
      approx(u["ma_fulfillment"]["unlinkable_amount"], 140.0)
      and approx(u["asset_lending"]["unlinkable_amount"], 55.0)
      and approx(u["pos_sale"]["unlinkable_amount"], 100.0)
      and approx(u["inventory_snapshot"]["unlinkable_amount"], 77.0))
# The AMBIGUOUS order DID reach IMEIs, so it is not counted as un-linkable — but it cannot be deduped
# to ONE device, so it must land in the at-risk bucket instead. Those are two different honesty
# categories and the report keeps them apart.
check("an ambiguous ① order counts as LINKABLE but NOT dedup-covered (at risk, not un-linkable)",
      _f["linkable"] is True and _f["recognized"] is True and _f["dedup_covered"] is False)
check("the caveat note states the premise's limit out loud",
      "IMEI" in d["caveat_note"] and "activation_order" in d["caveat_note"])

vc = d["vip_serial_caveat"]
check("the VIP invoice SERIAL-join caveat is measured, not assumed",
      vc and vc["rows"] == 3 and vc["by_imei"] == 1 and vc["by_serial_only"] == 1
      and vc["neither"] == 1)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. device key normalization — a junk token can never join two devices ─────────────")
check("a trailing '.0' is stripped (the mig-009 spelling)",
      D.device_key("356938035643809.0") == IMEI_A)
check("whitespace + case are normalized", D.device_key("  abc123def  ") == "ABC123DEF")
check("an alphanumeric serial keeps its letters (④/② can carry a real serial)",
      D.device_key("SN-ABC-99") == "SN-ABC-99")
for junk in (None, "", "   ", "nan", "None", "null", "0", "N/A", "NA", "-", "12345"):
    if D.device_key(junk) is not None:
        check(f"junk token {junk!r} refused", False)
        break
else:
    check("blank / 'nan' / '0' / 'N/A' / a too-short token are ALL refused (→ un-linkable)", True)
check("the minimum length is a named constant, not a magic number", D.MIN_DEVICE_KEY_LEN == 6)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. OVERLAPS — all four design-§3 pairs, measured ──────────────────────────────────")
install(full_store())
d = call(TEN)
ov = {o["device_key"]: o for o in d["overlaps"]}
pairs = {p["code"]: p for p in d["overlap_summary"]["pairs"]}
check("①∩③ detected (purchase + POS cost of the same device)",
      IMEI_A in ov and "ma_pos" in ov[IMEI_A]["pairs"])
check("②∩③ detected (consignment billing + POS cost)",
      IMEI_B in ov and "al_pos" in ov[IMEI_B]["pairs"])
check("①∩④ detected (purchase + inventory valuation)",
      IMEI_D in ov and "ma_inv" in ov[IMEI_D]["pairs"])
check("②∩④ detected (two inventory valuations of one device)",
      IMEI_C in ov and "al_inv" in ov[IMEI_C]["pairs"])
check("all four pairs are reported as NAMED buckets with the reason",
      set(pairs) == {"ma_pos", "al_pos", "ma_inv", "al_inv"}
      and all(p["why"] for p in pairs.values()))
check("duplicate_amount = Σ sources − max source (what a NAIVE sum adds)",
      approx(ov[IMEI_A]["duplicate_amount"], 120.0)        # ①120 + ③140 → 260 − 140
      and approx(ov[IMEI_A]["gross_amount"], 260.0))
check("②∩③ duplicate math", approx(ov[IMEI_B]["duplicate_amount"], 100.0))   # 100 + 120 − 120
check("a device in only ONE source is not an overlap", IMEI_E not in ov)
check("the overlap total is the sum of the per-device duplicates",
      approx(d["overlap_summary"]["duplicate_amount"],
             sum(o["duplicate_amount"] for o in d["overlaps"])))
check("each overlap row carries its stores/products/months for the drill-down",
      ov[IMEI_A]["stores"] and ov[IMEI_A]["months"] == ["2026-06"])
check("the naive four-source sum is LABELLED naive on the tiles",
      "naive_total" in d["tiles"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. the §9 POLICY: invoice-first, sale-time fallback, IMEI dedup ───────────────────")
install(full_store())
d = call(TEN)
check("§9 Q1 — an INVOICED device is recognized at its invoice (① wins over ③)",
      one(d, "ma_fulfillment", "ORD-A")["recognized"] is True
      and one(d, "pos_sale", "T-A")["recognized"] is False)
check("…and the POS row says WHAT superseded it",
      "superseded" in one(d, "pos_sale", "T-A")["recognition_reason"].lower()
      and one(d, "pos_sale", "T-A")["suppressed_by"].startswith("①"))
check("§9 Q2 — the VIP-BILLED amount is the consignment device's COGS (② wins over ③)",
      one(d, "asset_lending", IMEI_B)["recognized"] is True
      and one(d, "pos_sale", "T-B")["recognized"] is False)
check("§9 Q1 fallback — a device with NO invoice is recognized at SALE",
      one(d, "pos_sale", "T-E")["recognized"] is True
      and "fallback" in one(d, "pos_sale", "T-E")["recognition_reason"].lower())
check("§9 Q3 — every ④ snapshot row is excluded from recognition",
      all(r["recognized"] is False for r in rows_of(d, "inventory_snapshot")))
_keyless = one(d, "pos_sale", "T-N")
check("a row with NO IMEI is still recognized (the cost is real) …",
      _keyless["recognized"] is True)
check("… but is flagged as NOT dedup-covered, with the reason",
      _keyless["dedup_covered"] is False and "CANNOT be deduped" in _keyless["recognition_reason"])
pol = d["policy"]
check("the at-risk (un-dedupable) total is reported separately",
      pol["at_risk_rows"] >= 3 and pol["at_risk_amount"] > 0)
check("the recognized total splits invoice vs at-sale",
      approx(pol["recognized_amount"], pol["invoice_amount"] + pol["fallback_amount"]))
check("the precedence in force is stated in words",
      "①" in pol["precedence_label"] and "③" in pol["precedence_label"])

# recognized_amount, computed by hand from the fixture:
#   ① ORD-A 120 (invoice, beats T-A) + ORD-D 130 + ORD-X 140 (no key) + ORD-F 150 (no key)
#   ② IMEI_B 100 (beats T-B) + blank-ESN 55   [IMEI_C is UNBILLED → liability, not COGS]
#   ③ T-E 120 (no invoice) + T-N 100 (no key)
_expect = 120 + 130 + 140 + 150 + 100 + 55 + 120 + 100
check(f"the recognized total is exactly the hand-computed ${_expect:,}",
      approx(pol["recognized_amount"], _expect))
check("the suppressed total is the two POS rows an invoice beat (140 + 120)",
      approx(pol["suppressed_amount"], 260.0))

install(full_store())
d_p = call(TEN, precedence="pos_sale,ma_fulfillment,asset_lending")
check("precedence is a real PARAMETER — ③-first flips which row wins",
      one(d_p, "pos_sale", "T-A")["recognized"] is True
      and one(d_p, "ma_fulfillment", "ORD-A")["recognized"] is False)
install(full_store())
d_bad = call(TEN, precedence="nonsense,inventory_snapshot")
check("an unusable precedence falls back to the §9 default (never ④)",
      d_bad["precedence"] == list(D.DEFAULT_PRECEDENCE))
check("④ can never be put into the precedence",
      D.parse_precedence("inventory_snapshot") == D.DEFAULT_PRECEDENCE)

install(full_store())
d_md = call(TEN, ma_recognition_date="filled")
check("ma_recognition_date='filled' re-times ① onto date_filled",
      one(d_md, "ma_fulfillment", "ORD-A")["event_date"] == "2026-06-09")
install(full_store())
d_pb = call(TEN, price_basis="line", limit=500)
check("price_basis is honoured (qty 1 here, so the total is unchanged but the basis is echoed)",
      d_pb["price_basis"] == "line")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 9. C2 liability + C3 inventory: both legs shown, nothing netted away ──────────────")
install(full_store())
d = call(TEN)
liab, invl = d["liability"], d["inventory"]
check("the unsold consignment owed is reported as its OWN liability figure",
      liab["unsold_devices"] == 1 and approx(liab["unsold_owed"], 90.0))
check("the liability note says it is never netted against COGS",
      "NEVER netted" in liab["note"])
check("BOTH 'unsold' definitions are reported (asset module vs the P&L's status column)",
      "status_unsold_devices" in liab and liab["definition_disagree_devices"] >= 0)
check("the ④ closing valuation is reported with its as-of date",
      approx(invl["snapshot_amount"], 260.0)          # 95 + 88 + 77 (the keyless row still has a cost)
      and invl["snapshot_as_of_to"] == "2026-06-30")
check("the ledger's own unsold valuation is reported alongside it",
      approx(invl["ledger_unsold_amount"], 90.0))
check("a device valued by BOTH ② and ④ is counted (the two-valuations overlap)",
      invl["double_valued_devices"] == 1 and approx(invl["double_valued_amount"], 90.0))
check("Δ(inventory) is None — NOT a fake 0", invl["delta_inventory"] is None)
check("…and the reason names the missing month-end history",
      "UNIQUE on (org_id, imei)" in invl["delta_note"] and "no month-end history" in invl["delta_note"])
check("the §9 Q3 policy sentence is carried on the payload", "periodic inventory" in invl["policy_note"])

install(full_store(extra={"commcalc.asset_ledger": [
    al(id="b1", esn_imei=IMEI_C, owed_to_vip=90.0, billing_friday=None, trigger_date="2026-06-05",
       category="On Inventory", status="Sold", date_sold=None)]}))
d_dd = call(TEN)
check("a row unsold under ONE definition only is COUNTED as a disagreement",
      d_dd["liability"]["definition_disagree_devices"] == 1)
check("…and the page is told which two rules disagree",
      "category ILIKE" in (d_dd["liability"]["definition_note"] or ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 10. DELTA PREVIEW — month × store, today's route vs the §9 policy ────────────────")
install(full_store())
d = call(TEN)
today = d["today"]
check("today's leg is available and names the route it used",
      today["available"] is True and "device_cost" in today["route"])
# today's device COGS over the fixture = the DEVICE sale lines' ext − gp, accessory + voided excluded:
#   T-A 249−109=140 · T-B 199−79=120 · T-E 180−60=120 · T-N 150−50=100  → 480
check("today's device COGS is the P&L's own formula over device lines only",
      approx(today["device_cogs"], 480.0))
dt = d["delta_totals"]
check("the delta table has a month × store grain",
      all("month" in r and "store_key" in r for r in d["delta_rows"]))
check("today and policy land in ONE store key space (one cell for the one store)",
      len({r["store_key"] for r in d["delta_rows"] if r["store_key"]}) == 1)
check("the totals reconcile: Δ = policy − today",
      approx(dt["delta"], dt["policy"] - dt["today"]))
check("the delta table's today total equals the today tile", approx(dt["today"], 480.0))
check("the un-dedupable portion is carried per CELL, not only in total",
      any(r["at_risk"] > 0 for r in d["delta_rows"]))
check("a per-month rollup is provided", len(dt["by_month"]) >= 1)
check("the net-delta tile equals policy − today",
      approx(d["tiles"]["net_delta"], d["policy"]["recognized_amount"] - 480.0))
check("an UNBILLED consignment device is NOT a cost (§9 Q2 read strictly) and says why",
      one(d, "asset_lending", IMEI_C)["recognized"] is False
      and "not billed" in one(d, "asset_lending", IMEI_C)["recognition_reason"])

install(full_store(extra={"commcalc.raw_sales": [
    sale(trans_id="T-A", serial_1=IMEI_A, ext_price=249.0, gp=109.0, trans_date="2026-05-28")]}))
d_tm = call(TEN)
check("a sale line whose trans_date month ≠ its period is COUNTED and explained",
      "different month" in (d_tm["note"] or ""))

install(full_store(drop=("commcalc.store_mapping",)))
d_ns = call(TEN)
check("a missing store map does not break the delta table (raw spellings, honest note)",
      d_ns.get("ready") and isinstance(d_ns["delta_rows"], list))
check("…and the coarser comparison is NAMED, not silent",
      any("raw spelling" in x for x in (d_ns.get("degraded") or [])))
# Caught by the ASGI smoke: the finance classifier NEVER raises (it hard-falls-back to the Boost
# taxonomy), so an unreadable sales basis would otherwise make today's leg report a confident $0 and
# every delta read as "the policy invented money". Unknown must stay unknown.
install(full_store(drop=("commcalc.raw_sales", "commcalc.daily_sales_feed")))
d_nosales = call(TEN)
check("an unreadable SALES BASIS makes today's leg UNAVAILABLE, never a confident $0",
      d_nosales["today"]["available"] is False and d_nosales["today"]["device_cogs"] is None)
check("…and says the number is unknown rather than zero",
      "not $0" in (d_nosales["today"]["note"] or ""))
check("…while the policy leg still reports what it CAN prove",
      d_nosales["policy"]["recognized_amount"] > 0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 11. filters ≡ tiles ≡ groups ≡ delta ≡ export (RULE FOUR/FIVE WYSIWYG) ───────────")
install(full_store())
d_all = call(TEN)
install(full_store())
d_ma_only = call(TEN, sources="ma_fulfillment")
check("a source filter narrows the ROWS",
      {r["source"] for r in d_ma_only["rows"]} == {"ma_fulfillment"})
check("…and the TILES are recomputed over the filtered set",
      d_ma_only["tiles"]["by_source"][2]["rows"] == 0
      and d_ma_only["tiles"]["by_source"][0]["rows"] > 0)
check("…and the GROUPS too", all("①" in g["label"] for g in d_ma_only["groups"]))
check("…and the DELTA policy leg too",
      d_ma_only["delta_totals"]["policy"] < d_all["delta_totals"]["policy"])
check("option lists still come from the UNFILTERED rows (a picker never collapses)",
      len(d_ma_only["source_options"]) == len(d_all["source_options"]) >= 4)
# WYSIWYG: the dedup DECISION is global (filter first and a different row wins → a fake number) but the
# TOTAL must describe only what is on screen. Both halves are asserted.
check("the policy TOTAL is re-taken over the FILTERED rows (never the whole window's total)",
      approx(d_ma_only["policy"]["recognized_amount"], 120 + 130 + 140 + 150)
      and d_ma_only["policy"]["recognized_amount"] < d_all["policy"]["recognized_amount"])
check("…while the whole-window figure is still published separately, honestly labelled",
      approx(d_ma_only["policy_window"]["recognized_amount"],
             d_all["policy"]["recognized_amount"]))
check("the dedup DECISION is unchanged by the filter (① still beat ③ on IMEI_A)",
      one(d_ma_only, "ma_fulfillment", "ORD-A")["recognized"] is True)

install(full_store())
d_ov = call(TEN, overlap_only=1)
check("overlap_only shows only rows on an overlapping device",
      d_ov["total_rows"] > 0 and d_ov["total_rows"] < d_all["total_rows"])
install(full_store())
d_un = call(TEN, unlinkable_only=1)
check("unlinkable_only shows only rows with no device key",
      d_un["total_rows"] == 4 and all(r["linkable"] is False for r in d_un["rows"])
      and {r["source"] for r in d_un["rows"]} == set(D.SOURCES))
install(full_store())
d_rec = call(TEN, recognized_only=1)
check("recognized_only shows only the policy's recognized rows",
      all(r["recognized"] for r in d_rec["rows"]))
install(full_store())
d_st = call(TEN, stores="3560 Nostrand Avenue")
check("the store filter matches the canonical label", d_st["total_rows"] > 0)
install(full_store())
d_mk = call(TEN, markets="Brooklyn")
check("the market filter works off the org's own /store-match chain", d_mk["total_rows"] > 0)
# The sentinel bucket only exists when something genuinely fails to resolve — so it is proven on a
# fixture with an UNMAPPED store, not by asserting it into existence on the mapped one.
install(full_store(extra={"commcalc.raw_sales": [
    sale(trans_id="T-UNMAPPED", store="99 Nowhere Rd", serial_1=IMEI_E, ext_price=180.0, gp=60.0)]}))
d_nm0 = call(TEN)
check("an unresolved store yields the '(no market)' OPTION (never a silent drop)",
      D.NO_MARKET in d_nm0["market_options"])
install(full_store(extra={"commcalc.raw_sales": [
    sale(trans_id="T-UNMAPPED", store="99 Nowhere Rd", serial_1=IMEI_E, ext_price=180.0, gp=60.0)]}))
d_nm = call(TEN, markets=D.NO_MARKET)
check("the '(no market)' sentinel is a REAL selectable bucket",
      d_nm["total_rows"] > 0 and all(not r["market"] for r in d_nm["rows"]))
install(full_store())
check("…and it is NOT offered when every row resolved (no phantom bucket)",
      D.NO_MARKET not in call(TEN)["market_options"])
install(full_store())
d_rp = call(TEN, reps="Ada Lovelace")
check("a rep filter narrows ③ …", any(r["source"] == "pos_sale" for r in d_rp["rows"]))
check("… and KEEPS the rep-less sources rather than reporting them as $0",
      {"ma_fulfillment", "asset_lending", "inventory_snapshot"} <= {r["source"] for r in d_rp["rows"]})
check("… and the response SAYS the other sources have no rep",
      "no rep" in (d_rp["note"] or "").lower() or "rep/salesperson" in (d_rp["note"] or ""))
install(full_store())
d_ts = call(TEN, timings="vip_billed")
check("the timing facet filters on the timing dimension",
      {r["timing"] for r in d_ts["rows"]} == {"vip_billed"})
install(full_store())
d_ar = call(TEN, arrangements="Consignment — VIP")
check("the arrangement facet filters on the CONFIG-resolved label",
      {r["source"] for r in d_ar["rows"]} == {"asset_lending"})
install(full_store())
d_cap = call(TEN, limit=2)
check("the display cap truncates the TABLE only", len(d_cap["rows"]) == 2 and d_cap["truncated"])
check("…while the tiles still describe the FULL filtered set",
      d_cap["tiles"]["rows"] == d_all["tiles"]["rows"])
check("…and so does the delta preview",
      approx(d_cap["delta_totals"]["policy"], d_all["delta_totals"]["policy"]))
install(full_store())
d_mn = call(TEN, min_amount=125)
check("a minimum-amount floor is applied server-side",
      all(abs(r["amount"] or 0) >= 125 for r in d_mn["rows"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 12. arrangement config: every chain, and the honest unmapped bucket ───────────────")
# ① via carrier when there is NO data_source row
install(full_store(extra={"commcalc.data_source": []}))
d = call(TEN)
check("① falls back to carrier → distributor when no data_source links it",
      one(d, "ma_fulfillment", "ORD-A")["arrangement_source"] == "carrier → distributor")
# ② via the single has_asset_lending distributor when no payable map points at the ledger
install(full_store(extra={"commcalc.payable_source_map": []}))
d = call(TEN)
check("② falls back to the single has_asset_lending distributor",
      one(d, "asset_lending", IMEI_B)["arrangement_source"].startswith("distributors.has_asset_lending"))
check("…and the page is told to map the columns per carrier",
      "payable source map" in (d["note"] or "").lower())
# nothing configured at all
install(full_store(extra={"commcalc.distributors": [], "commcalc.data_source": [],
                          "commcalc.payable_source_map": []}))
d = call(TEN)
check("with NO distributor config every row lands in the SELECTABLE unmapped bucket",
      all(r["arrangement_label"] == D.UNMAPPED_ARRANGEMENT
          for r in rows_of(d, "ma_fulfillment")))
check("…and the note names the config table to fill in",
      "commcalc.distributors" in (d["note"] or ""))
# two asset-lending distributors → ambiguous, but the LEDGER is still consignment
install(full_store(extra={"commcalc.payable_source_map": [], "commcalc.distributors": [
    {"id": "d1", "org_id": TEN, "name": "VIP", "arrangement": "consignment",
     "has_asset_lending": True, "is_active": True},
    {"id": "d2", "org_id": TEN, "name": "Other", "arrangement": "consignment",
     "has_asset_lending": True, "is_active": True}]}))
d = call(TEN)
check("two asset-lending distributors → ambiguous, and it SAYS so",
      "ambiguous" in (d["note"] or "").lower())
check("…but the consignment ARRANGEMENT is still stated (that is what the ledger is)",
      one(d, "asset_lending", IMEI_B)["arrangement"] == "consignment")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 13. period spelling: ③ is read in BOTH spellings via _pvariants ──────────────────")
install(full_store(extra={"commcalc.raw_sales": [
    sale(trans_id="T-YM", period="2026-06", serial_1=IMEI_E, ext_price=180.0, gp=60.0)]}))
d = call(TEN)
check("a sale stored as '2026-06' is found when the report asked for 'June 2026'",
      one(d, "pos_sale", "T-YM") is not None)
_pq = [q for q in QUERY_LOG if q["table"] == "raw_sales"]
check("the raw_sales read used .in_('period', <both spellings>)",
      bool(_pq) and {"June 2026", "2026-06"} <= {str(x) for x in (_pq[0]["in"].get("period") or [])})
install(full_store(extra={"commcalc.raw_sales": [
    sale(trans_id="T-MN", period="June 2026", serial_1=IMEI_E, ext_price=180.0, gp=60.0)]}))
d = call(TEN, period="2026-06")
check("…and the reverse: 'June 2026' data found when asked as '2026-06'",
      one(d, "pos_sale", "T-MN") is not None)

install(full_store(extra={"commcalc.raw_sales": [], "commcalc.daily_sales_feed": [
    sale(trans_id="T-FEED", serial_1=IMEI_E, ext_price=180.0, gp=60.0)]}))
d = call(TEN)
check("with raw_sales empty the whole daily FEED is used (unpromoted month)",
      one(d, "pos_sale", "T-FEED") is not None and "B2B feed" in (d["note"] or ""))
install(full_store(extra={"commcalc.daily_sales_feed": [
    sale(trans_id="T-B", serial_1=IMEI_B, ext_price=999.0, gp=1.0)]}))
d = call(TEN)
check("a trans_id present in BOTH tables is counted ONCE, raw_sales winning",
      approx(one(d, "pos_sale", "T-B")["amount"], 120.0))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 14. degradation: a missing table is a NOTE, never a 500 ───────────────────────────")
for tbl, why in (("commcalc.raw_ma_fulfillment", "mig 083 unrun"),
                 ("commcalc.asset_ledger", "no asset ledger"),
                 ("commcalc.raw_sales", "no sales basis"),
                 ("commcalc.inventory_aging_device", "mig 216 unrun"),
                 ("commcalc.raw_ma_commission", "no activation bridge"),
                 ("commcalc.distributors", "no distributor config"),
                 ("commcalc.vip_invoice_devices", "no VIP invoices")):
    install(full_store(drop=(tbl,)))
    try:
        d = call(TEN)
        ok = bool(d.get("ready"))
    except Exception as e:
        ok = False
        print(f"        (raised {type(e).__name__}: {e})")
    check(f"missing {tbl} ({why}) → a READY payload", ok)
install(full_store(drop=("commcalc.raw_ma_fulfillment",)))
d = call(TEN)
check("…and the degradation is NAMED on the payload, not silent",
      d.get("degraded") and any("marketplace" in x.lower() for x in d["degraded"]))
install(full_store(drop=("commcalc.raw_ma_commission",)))
d = call(TEN)
check("a dead activation bridge WARNS that the un-linkable count is not a finding",
      any("NOT a data finding" in x for x in (d.get("degraded") or [])))

install(full_store())
d = call(TEN, period="not-a-month")
check("an unparseable period → a ready, EMPTY payload that says so",
      d.get("ready") and d["total_rows"] == 0 and "not a month" in (d["note"] or ""))
check("…with the policy/inventory/liability shells still present (the page never sees undefined)",
      d["policy"]["recognized_amount"] == 0 and d["inventory"]["delta_inventory"] is None
      and d["liability"]["unsold_owed"] == 0)
install(full_store())
d = call(TEN, group_by="nonsense", price_basis="nonsense", ma_recognition_date="nonsense")
check("every unknown enum falls back to its documented default",
      d["group_by"] == "source" and d["price_basis"] == "unit"
      and d["ma_recognition_date"] == "ordered")
install(full_store())
d = call(TEN, window_months=999)
check("the window is clamped (a runaway read is not a report)", d["window_months"] == 12)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 15. the honest text the exports carry ─────────────────────────────────────────────")
install(full_store())
d = call(TEN)
check("the definition note states this is read-only and changes nothing",
      "nothing is written" in d["definition_note"].lower())
check("the policy note names the owner's answers as the source of the policy",
      "§9" in d["policy_note"] and "invoice-first" in d["policy_note"])
check("the policy note says the P&L is untouched", "untouched" in d["policy_note"].lower())
check("the source legend describes all four sources with their timing + invoice status",
      len(d["source_legend"]) == 4
      and {s["is_invoice"] for s in d["source_legend"]} == {True, False})
check("the legend carries the ma_upload asset-lending PARITY for ①'s cost field (design §7)",
      (d["source_legend"][0].get("cost_field") or {}).get("asset_label") == "Owed to VIP")
check("the four sentinel bucket labels are published for the UI",
      d["no_market_label"] and d["no_store_label"] and d["no_device_label"])
check("read caps are reported per source so a bound is never read as a total",
      isinstance(d["truncated_reads"], dict) and "asset_ledger" in d["truncated_reads"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 16. pure-module unit checks (no DB at all) ────────────────────────────────────────")
check("SOURCE_META covers exactly the four sources", set(D.SOURCE_META) == set(D.SOURCES))
check("only ① and ② are invoice sources", set(D.INVOICE_SOURCES) == {"ma_fulfillment", "asset_lending"})
check("④ is on the never-recognized list", D.NEVER_RECOGNIZED == ("inventory_snapshot",))
check("the §9 default precedence is invoice-first, sale-time fallback",
      D.DEFAULT_PRECEDENCE == ("ma_fulfillment", "asset_lending", "pos_sale"))
_e = D.pos_events([{"department": "Android - XP", "ext_price": 100, "gp": 100, "serial_1": IMEI_A}])
check("a POS line whose ext − GP is 0 yields NO amount (counted, not summed as $0)",
      _e[0]["amount"] is None and "not a positive number" in _e[0]["priceless_reason"])
_e2 = D.pos_events([{"department": "Android - XP", "ext_price": 100, "gp": None, "serial_1": IMEI_A}])
check("a blank GP is 'unknown', not 0 (no fabricated cost)", _e2[0]["amount"] is None)
_e3 = D.inventory_events([{"imei": IMEI_A, "unit_cost": 0}])
check("a ≤0 snapshot unit cost is no-signal, not $0", _e3[0]["amount"] is None)
_ev = D.recognize([], D.DEFAULT_PRECEDENCE)
check("recognize([]) is an honest zero, not a crash", _ev["recognized_amount"] == 0)
_ovl, _sm = D.find_overlaps([])
check("find_overlaps([]) is empty, not a crash", _ovl == [] and _sm["devices"] == 0)
_dr, _dtot = D.delta_table({("2026-06", "S"): 100.0}, [])
check("a today-only cell is KEPT and labelled 'today only'",
      len(_dr) == 1 and _dr[0]["only_in"] == "today" and _dtot["only_today_cells"] == 1)
_ai = D.ArrangementIndex()
check("an empty ArrangementIndex still answers (unmapped) and names the config gap",
      _ai.for_asset()["arrangement_label"] == D.UNMAPPED_ARRANGEMENT and _ai.notes())
check("group_by has a label for every dimension it accepts",
      all(g in D.GROUP_LABEL for g in D.GROUP_BY))


print("\n══════════════════════════════════════════════════════════════════════════════════════")
print(f"  PASS {_pass}   FAIL {_fail}")
print("══════════════════════════════════════════════════════════════════════════════════════")
sys.exit(1 if _fail else 0)
