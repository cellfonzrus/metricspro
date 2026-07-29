"""Endpoint harness for GET /api/v1/commcalc/imei-rebates — drives the REAL router handler against an
in-memory FAKE Supabase client (no network, no DB). What this proves that the pure-helper proof cannot:

  MULTI-TENANT (AGENT_CONTRACT RULE ONE)
  • EVERY read the handler issues carries `.eq('org_id', …)` — asserted by recording each query
  • a second tenant's rows are never returned, in either direction (house ↮ tenant)
  • org_id is a QUERY PARAM on the handler signature (never a constant / Form field / body)

  SOURCE RESOLUTION BY DATA PRESENCE (RULE TWO — no tenant/carrier name anywhere)
  • an org with only master-agent rows      -> source 'ma'
  • an org with only sales/residual+ePay    -> source 'epay'
  • an org with BOTH                        -> source 'both', union, rows tagged
  • an org with neither                     -> a ready, empty, honest payload (never a 500)

  BEHAVIOUR
  • the rebate LAG window really reaches a later period's payment rows
  • raw_mi presence alone is NOT an activation (only an in-period MI activation date is), and blank
    activation dates are counted in the note rather than guessed
  • voided POS lines and placeholder serials never become phantom gaps
  • server-side filters + the display cap; tiles always describe the FULL filtered set
  • the `carrier_residual` money gate nulls every $ in rows AND tiles AND orphans
  • a missing table (mig 083 not run) degrades to a note, never a 500

  THE PAGE GATE (owner directive 2026-07-29 — NO DEFAULT ACCESS)
  • super-admins / scope-'all' / role-'admin' open the report; a plain caller gets 403
  • the grant passes under EITHER carrier: perms.data.imei_rebates or perms.modules['imei_rebates']
  • an unresolvable caller (no token) and a caller-resolution ERROR both DEGRADE CLOSED (403)
  • the 403 names the permission, and is raised BEFORE any DB read (zero queries on a denial)
  • the page gate and the money gate are INDEPENDENT: a granted non-admin still gets $ nulled

Run: `python3 harness_imei_rebates.py` from the backend dir.
"""
import os, sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import router as R

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
# A minimal in-memory stand-in for the supabase-py query builder (only the verbs this handler uses).
# Every executed query is recorded so the harness can assert org scoping on ALL of them.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _MissingTable(Exception):
    pass


class _Q:
    def __init__(self, store, schema, table):
        self._store, self._schema, self._table = store, schema, table
        self._eq, self._in, self._neq, self._gte, self._lte = {}, {}, {}, {}, {}
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

    def limit(self, n):
        self._limit = n
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def execute(self):
        QUERY_LOG.append({"table": self._table, "schema": self._schema,
                          "eq": dict(self._eq), "in": dict(self._in)})
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
                # PostgREST `neq` is SQL `<>`: a NULL row is excluded too (NULL <> '' is unknown).
                if r.get(k) is None or str(r.get(k)) == str(v):
                    ok = False
            for k, v in self._gte.items():
                if not (r.get(k) and str(r.get(k)) >= str(v)):
                    ok = False
            for k, v in self._lte.items():
                if not (r.get(k) and str(r.get(k)) <= str(v)):
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


class FakeClient:
    def __init__(self, store):
        self._store = store

    def schema(self, s):
        return _Schema(self._store, s)

    def table(self, t):
        return _Q(self._store, "public", t)


def install(store):
    """Point the router's `sb()` at a fake DB and reset the query log."""
    QUERY_LOG.clear()
    R.sb = lambda: FakeClient(store)      # noqa: E731


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# FAKE CALLER RESOLUTION. The REAL gate (`R._can_view_imei_rebates` -> the pure
# `imei_rebate_report.imei_rebates_allowed`) runs untouched; only core's token->caller resolution is
# stubbed, so the token string IS the caller key. Both gates (page + carrier_residual) resolve through
# this same pair, which is exactly how they behave in production.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
CALLERS = {}


def _fake_uid(auth):
    return (auth.strip() or None) if isinstance(auth, str) else None


def _fake_caller(client, uid, active_org=None):
    c = CALLERS.get(uid)
    if c == "BOOM":                                     # caller resolution blows up -> must degrade CLOSED
        raise RuntimeError("roles table unavailable")
    return c


import app.modules.core.router as CR                                                   # noqa: E402
CR._uid_from_token, CR._resolve_caller = _fake_uid, _fake_caller

SUPER = "t-super"
ADMIN = "t-admin"
SCOPE_ALL = "t-scope-all"
PLAIN = "t-plain"
GRANT_DATA = "t-grant-data"
GRANT_MODULE = "t-grant-module"
BROKEN = "t-broken"
CALLERS.update({
    SUPER:        {"super_admin": True, "role": "owner", "perms": {"scope": "store"}},
    ADMIN:        {"super_admin": False, "role": "admin", "perms": {"scope": "store"}},
    SCOPE_ALL:    {"super_admin": False, "role": "market_manager", "perms": {"scope": "all"}},
    PLAIN:        {"super_admin": False, "role": "rep",
                   "perms": {"scope": "store", "modules": {"commissions": True}, "data": {}}},
    GRANT_DATA:   {"super_admin": False, "role": "rep",
                   "perms": {"scope": "store", "modules": {}, "data": {"imei_rebates": True}}},
    GRANT_MODULE: {"super_admin": False, "role": "rep",
                   "perms": {"scope": "store", "modules": ["imei_rebates"], "data": {}}},
    BROKEN:       "BOOM",
})


HOUSE = "00000000-0000-0000-0000-000000000001"
TEN = "00000000-0000-0000-0000-0000000000aa"
I1, I2, I3, I4 = "355163568356971", "355163568356972", "355163568356973", "355163568356974"


def ma_row(**kw):
    base = {"id": "x", "org_id": TEN, "imei": I1, "tx_date": "2026-06-10", "period": "June 2026",
            "period_month": 6, "period_year": 2026, "merchant_account_id": "MA-1001",
            "user_name": "Jane Doe", "sku": "MOTO-G", "activation_type": "New",
            "activation_type2": "branded", "sub_type": "TWP", "line_status": "Active",
            "is_financed": "No", "platform": "Vidapay", "rebate": 0, "device_margin": 0,
            "consumer_margin": 0, "consumer_financing": 0, "wallet_funding": 0, "fees_margin": 0,
            "spiff_m1": 0, "spiff_m2": 0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0}
    base.update(kw)
    return base


def sale_row(**kw):
    base = {"org_id": HOUSE, "serial_1": I1, "trans_date": "2026-06-10", "period": "June 2026",
            "store": "1800 Great Neck Rd", "salesperson": "Jane Doe", "user_login": "jdoe",
            "product_desc": "Moto G", "sku": "MOTOG", "contract_type": "Activation",
            "voided": "", "ext_price": 129.99}
    base.update(kw)
    return base


def pay_row(**kw):
    base = {"org_id": HOUSE, "imei": I1, "mdn": "5165551234",
            "payment_type": "Device Reimbursement - Month 1", "amount": 200.0,
            "period": "August 2026", "period_month": 8, "period_year": 2026,
            "payment_date": "2026-08-15", "business_address": "1800 Great Neck Rd",
            "rep_username": "jdoe"}
    base.update(kw)
    return base


def mi_row(**kw):
    base = {"org_id": HOUSE, "device_serial": I1, "period": "June 2026",
            "mi_activation_date": "6/10/2026", "customer_plan": "Unlimited 50",
            "subscriber_status": "Active", "rep_username": "jdoe"}
    base.update(kw)
    return base


def call(org_id, **kw):
    """Default to an ADMIN token: every behavioural proof below is about the REPORT, and the report is
    now DEFAULT-CLOSED (section 8 proves the gate itself)."""
    kw.setdefault("authorization", ADMIN)
    return R.imei_rebate_report_endpoint(period=kw.pop("period", "June 2026"), org_id=org_id, **kw)


def denied(org_id, token, **kw):
    """Call and report (status, detail) instead of raising — HTTPException is the 403 contract."""
    from fastapi import HTTPException
    try:
        call(org_id, authorization=token, **kw)
        return None, None
    except HTTPException as e:
        return e.status_code, str(e.detail)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. handler contract: org_id is a QUERY PARAM ─────────────────────────────────────")
import inspect
sig = inspect.signature(R.imei_rebate_report_endpoint)
check("org_id is a parameter with the ORG_ID default (query param, not a constant/body)",
      "org_id" in sig.parameters and sig.parameters["org_id"].default == R.ORG_ID)
check("no request body / Form parameter exists on the handler",
      not any(p.name in ("body", "request") for p in sig.parameters.values()))
_routes = [r for r in R.router.routes if getattr(r, "path", "").endswith("/imei-rebates")]
check("the handler is registered exactly once, read-only (GET)",
      len(_routes) == 1 and set(getattr(_routes[0], "methods", [])) == {"GET"})
check("it mounts under the module prefix (-> /api/v1/commcalc/imei-rebates)",
      _routes and _routes[0].path == "/commcalc/imei-rebates")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. MA-only org: source resolved by DATA PRESENCE ─────────────────────────────────")
STORE_MA = {
    "commcalc.raw_ma_commission": [
        ma_row(id="a1", imei=I1, rebate=-529.0, spiff_m1=-10.0, spiff_m2=-10.0, device_margin=-20.0),
        ma_row(id="a2", imei=I2, rebate=0),                                     # <- GAP
        ma_row(id="a3", imei=I3, rebate=-100.0, tx_date="2026-06-22"),
        # an ADJUSTMENT for I1 two months later — money, but NOT a second activation
        ma_row(id="a4", imei=I1, tx_date="2026-08-03", period="August 2026",
               period_month=8, rebate=-25.0),
        # an M2-spiff line for an activation that happened BEFORE this period: it lands in a window
        # `period` while keeping its original tx_date. It must be ignored entirely — neither an
        # activation here, nor money credited here, nor a phantom "rebate with no activation".
        ma_row(id="a5", imei="355163568350000", tx_date="2026-04-11", period="August 2026",
               period_month=8, rebate=-77.0, spiff_m2=-5.0),
        # another tenant's row — must NEVER appear
        ma_row(id="zz", org_id=HOUSE, imei="999999999999999", rebate=-999.0),
    ],
    "commcalc.raw_sales": [],
    "commcalc.raw_mi": [],
    "commcalc.raw_payment_detail": [],
    "commcalc.store_mapping": [],
    "commcalc.commission_org_config": [],
}
install(STORE_MA)
d = call(TEN)
check("ready", d["ready"] is True)
check("source resolved to 'ma' with no tenant/carrier name involved", d["source"] == "ma")
check("three activations (the August adjustment is not a fourth)", d["tiles"]["activations"] == 3)
by = {r["imei"]: r for r in d["rows"]}
check("the cross-tenant IMEI is absent (isolation)", "999999999999999" not in by)
check("a line whose tx_date predates the period is neither an activation nor an orphan here",
      "355163568350000" not in by
      and not any(o["imei"] == "355163568350000" for o in d["orphans"]))
check("and its money is NOT credited to this period", approx(d["tiles"]["rebate_total"], 554.0 + 100.0))
check("I1 rebate = 529 + the later 25 adjustment, sign-flipped", approx(by[I1]["rebate"], 554.0))
check("I1 spiffs are sign-flipped", approx(by[I1]["spiff_total"], 20.0))
check("I1 'other' carries the device margin", approx(by[I1]["other_paid"], 20.0))
check("I2 is a first-class GAP row", by[I2]["rebate_status"] == "none" and approx(by[I2]["rebate"], 0.0))
check("the gap tile counts it", d["tiles"]["no_rebate"]["count"] == 1)
check("the gap $ is an explicit ESTIMATE", "ESTIMATE" in (d["tiles"]["no_rebate"]["estimate_basis"] or ""))
check("gap rows sort to the top", d["rows"][0]["rebate_status"] in ("partial", "none"))
check("the window covers June -> December", d["window"][0] == "June 2026" and d["window"][-1] == "December 2026")
check("the definition note names the MA source", "raw_ma_commission" in d["definition_note"])
check("the sign note states the flip", "sign-flipped" in (d["sign_note"] or ""))
check("MA rows carry no market (documented deviation)", all(r["market"] is None for r in d["rows"]))
check("EVERY executed read was org-scoped",
      all(q["eq"].get("org_id") == TEN for q in QUERY_LOG if q["schema"] == "commcalc"))
check("more than one table was read (the scoping assertion is not vacuous)",
      len({q["table"] for q in QUERY_LOG}) >= 3)

install(STORE_MA)
d_house = call(HOUSE)
check("the OTHER tenant sees only its own row (isolation both ways)",
      [r["imei"] for r in d_house["rows"]] == ["999999999999999"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. ePay-only org: activation legs, lag window, voids, placeholders ───────────────")
STORE_EP = {
    "commcalc.raw_ma_commission": [],
    "commcalc.raw_sales": [
        sale_row(serial_1=I1),                                          # rebate arrives in August
        sale_row(serial_1=I2, store="42 Main St", salesperson="Sam Ray"),   # <- GAP
        sale_row(serial_1=I3, voided="TRUE"),                           # VOIDED -> not an activation
        sale_row(serial_1="N/A"),                                       # placeholder -> not an activation
        sale_row(serial_1=I4, org_id=TEN),                              # other tenant
    ],
    "commcalc.raw_mi": [
        mi_row(device_serial=I1),                                       # corroborates the I1 sale
        mi_row(device_serial=I4, mi_activation_date="2/03/2026"),       # activated LONG ago -> not June
        mi_row(device_serial="355163568356999", mi_activation_date=""),  # blank date -> excluded + counted
    ],
    "commcalc.raw_payment_detail": [
        pay_row(imei=I1, amount=200.0),                                 # lands 2 months LATER
        pay_row(imei=I2, payment_type="New Activation Bounty", amount=45.0),   # not a rebate
        pay_row(imei="888888888888888", amount=150.0, period="July 2026"),     # ORPHAN
    ],
    "commcalc.store_mapping": [
        {"org_id": HOUSE, "store_code": "1800", "store_address": "1800 Great Neck Rd", "market": "LI"},
        {"org_id": HOUSE, "store_code": "42", "store_address": "42 Main St", "market": "NYC"},
    ],
    "commcalc.commission_org_config": [],
}
install(STORE_EP)
d = call(HOUSE)
check("source resolved to 'epay'", d["source"] == "epay")
check("two activations (void + placeholder + other-tenant + stale-MI all excluded)",
      d["tiles"]["activations"] == 2)
by = {r["imei"]: r for r in d["rows"]}
check("a VOIDED POS line never becomes a phantom gap", I3 not in by)
check("a placeholder serial never becomes a phantom gap", not any(r["imei"] == "N/A" for r in d["rows"]))
check("raw_mi presence alone is NOT an activation (Feb activation date, June report)", I4 not in by)
check("a blank MI activation date is EXCLUDED and COUNTED, not guessed",
      "no MI activation date" in (d["note"] or "") and "1 residual line" in (d["note"] or ""))
check("the rebate LAG window reaches the August payment", approx(by[I1]["rebate"], 200.0)
      and by[I1]["rebate_status"] == "received")
check("the rebate's provenance names its period + table", by[I1]["rebate_period"] == "August 2026"
      and by[I1]["rebate_source"] == "raw_payment_detail")
check("ePay amounts are NOT sign-flipped", approx(by[I1]["rebate"], 200.0))
check("a bounty does not satisfy the rebate — I2 stays a GAP",
      by[I2]["rebate_status"] == "none" and approx(by[I2]["other_paid"], 45.0))
check("the market resolves from store_mapping on the ePay leg",
      by[I1]["market"] == "LI" and by[I2]["market"] == "NYC")
check("both evidence legs merged onto ONE row for I1", by[I1]["evidence"] == ["sale", "residual"])
check("the ORPHAN rebate is in its own section, not the table",
      [o["imei"] for o in d["orphans"]] == ["888888888888888"])
check("the orphan note explains the commonest benign cause",
      "EARLIER period" in (d["orphan_note"] or ""))
check("the definition note names BOTH ePay legs", "raw_sales.serial_1" in d["definition_note"]
      and "raw_mi.mi_activation_date" in d["definition_note"])
check("EVERY executed read was org-scoped to the house org",
      all(q["eq"].get("org_id") == HOUSE for q in QUERY_LOG if q["schema"] == "commcalc"))

install(STORE_EP)
d_sales = call(HOUSE, basis="sales")
check("basis='sales' drops the residual leg from the definition",
      "raw_mi.mi_activation_date" not in d_sales["definition_note"])
check("basis='sales' still finds both sale activations", d_sales["tiles"]["activations"] == 2)
install(STORE_EP)
d_res = call(HOUSE, basis="residual")
check("basis='residual' keeps only MI-dated activations", d_res["tiles"]["activations"] == 1
      and d_res["rows"][0]["imei"] == I1)
install(STORE_EP)
check("an unknown basis falls back to 'both'", call(HOUSE, basis="banana")["basis"] == "both")
install(STORE_EP)
d_lag0 = call(HOUSE, lag_months=0)
check("lag 0 cannot see the August rebate -> the receipt becomes a visible GAP",
      d_lag0["tiles"]["no_rebate"]["count"] == 2 and d_lag0["window"] == ["June 2026"])
check("the window note tells the reader to widen the lag", "widen the lag" in (d_lag0["window_note"] or ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. an org with BOTH feeds: union, tagged ─────────────────────────────────────────")
STORE_BOTH = {
    "commcalc.raw_ma_commission": [ma_row(id="b1", org_id=HOUSE, imei=I3, rebate=-300.0)],
    "commcalc.raw_sales": [sale_row(serial_1=I1)],
    "commcalc.raw_mi": [],
    "commcalc.raw_payment_detail": [pay_row(imei=I1, amount=200.0)],
    "commcalc.store_mapping": [],
    "commcalc.commission_org_config": [],
}
install(STORE_BOTH)
d = call(HOUSE)
check("both paths active -> source 'both'", d["source"] == "both" and set(d["sources"]) == {"ma", "epay"})
check("the union carries both activations", d["tiles"]["activations"] == 2)
check("each row is tagged with the feed that produced it",
      {r["imei"]: r["source"] for r in d["rows"]} == {I1: "epay", I3: "ma"})
check("the definition note states BOTH definitions",
      "raw_ma_commission" in d["definition_note"] and "raw_sales.serial_1" in d["definition_note"])
check("the sign note states BOTH conventions",
      "sign-flipped" in d["sign_note"] and "as-is" in d["sign_note"])
check("totals add across the feeds", approx(d["tiles"]["rebate_total"], 500.0))
check("the source facet can isolate one feed",
      len(call(HOUSE, source="ma")["rows"]) == 1)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. filters are SERVER-side; tiles describe what the table shows ──────────────────")
install(STORE_EP)
d = call(HOUSE, status="none")
check("the status facet narrows server-side", [r["imei"] for r in d["rows"]] == [I2])
check("the tiles follow the filter (WYSIWYG)", d["tiles"]["activations"] == 1
      and d["tiles"]["no_rebate"]["count"] == 1 and d["tiles"]["with_rebate"]["count"] == 0)
check("the option lists stay computed from the UNFILTERED rows (the picker never collapses)",
      set(d["store_options"]) == {"1800 Great Neck Rd", "42 Main St"})
check("unfiltered_rows still reports the full universe", d["unfiltered_rows"] == 2)
install(STORE_EP)
d = call(HOUSE, stores="42 main st")
check("the store filter is case-insensitive server-side", [r["imei"] for r in d["rows"]] == [I2])
install(STORE_EP)
d = call(HOUSE, markets="LI")
check("the market filter narrows", [r["imei"] for r in d["rows"]] == [I1])
install(STORE_EP)
d = call(HOUSE, reps="Sam Ray")
check("the rep filter narrows", [r["imei"] for r in d["rows"]] == [I2])
install(STORE_EP)
d = call(HOUSE, limit=1)
check("the display cap truncates the TABLE", len(d["rows"]) == 1 and d["truncated"] is True)
check("but total_rows still names the full filtered set", d["total_rows"] == 2)
check("and the tiles still describe the full filtered set", d["tiles"]["activations"] == 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. the money gate + graceful degradation ─────────────────────────────────────────")
STORE_GATED = {**STORE_EP,
               "commcalc.commission_org_config": [{"org_id": HOUSE, "pay_disabled": False,
                                                   "residual_visibility": "permissioned"}]}
install(STORE_GATED)
# GRANT_DATA holds 'imei_rebates' but NOT 'carrier_residual' -> passes the PAGE gate, fails the MONEY
# gate. This is the two-independent-gates contract: opening the report grants no dollars.
d = call(HOUSE, authorization=GRANT_DATA)
check("a caller without carrier_residual gets money_gated", d["money_gated"] is True)
check("every row $ is NULLED (never leaks through an export)",
      all(r["rebate"] is None and r["total_received"] is None and r["spiff_by_month"] is None
          for r in d["rows"]))
check("tile $ are nulled too", d["tiles"]["rebate_total"] is None
      and d["tiles"]["with_rebate"]["amount"] is None
      and d["tiles"]["no_rebate"]["estimated_amount"] is None)
check("orphan $ are nulled too", all(o["amount"] is None for o in d["orphans"]))
check("counts and statuses SURVIVE the gate (the operational value is kept)",
      d["tiles"]["activations"] == 2 and d["tiles"]["no_rebate"]["count"] == 1
      and all(r["rebate_status"] for r in d["rows"]))
check("the payload says why", "hidden for your role" in (d["note"] or ""))

install({"commcalc.store_mapping": [], "commcalc.commission_org_config": []})   # no data tables at all
d = call(HOUSE)
check("every source table missing (mig 083 not run, empty tenant) -> ready + honest note, NOT a 500",
      d["ready"] is True and d["tiles"]["activations"] == 0 and d["source"] == "none")
check("the note tells the operator what to import", "Import the" in (d["note"] or ""))
check("the definition note is honest about having no source",
      "No activation source" in d["definition_note"])

install({"commcalc.raw_ma_commission": [ma_row(id="c1", org_id=HOUSE, imei=I1, rebate=-50.0,
                                               tx_date="2026-01-05", period="January 2026")],
         "commcalc.store_mapping": [], "commcalc.commission_org_config": []})
d = call(HOUSE)
check("MA rows exist but none in the window -> a note that says exactly that, no fake rows",
      d["tiles"]["activations"] == 0 and "none fall in the selected" in (d["note"] or ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. period spelling + defaults ────────────────────────────────────────────────────")
STORE_SPELL = {**STORE_MA, "commcalc.raw_ma_commission": [
    ma_row(id="s1", imei=I1, period="2026-06", rebate=-40.0),        # ISO spelling in the table
    ma_row(id="s2", imei=I2, period="June 2026", rebate=-60.0),      # long spelling in the table
]}
install(STORE_SPELL)
a = call(TEN, period="June 2026")
install(STORE_SPELL)
b = call(TEN, period="2026-06")
check("both period spellings find BOTH rows (the _pvariants path)",
      a["tiles"]["activations"] == 2 and b["tiles"]["activations"] == 2)
check("the two spellings return the same money", approx(a["tiles"]["rebate_total"], 100.0)
      and approx(b["tiles"]["rebate_total"], a["tiles"]["rebate_total"]))
check("the period echoes back canonically", a["period"] == b["period"] == "June 2026")
install(STORE_SPELL)
blank = R.imei_rebate_report_endpoint(org_id=TEN, authorization=ADMIN)
check("a blank period defaults to the current month rather than erroring",
      blank["ready"] is True and blank["period"] and len(blank["window"]) == 7)



# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. the PAGE gate: NO DEFAULT ACCESS (owner directive 2026-07-29) ─────────────────")
from app.modules.commcalc import imei_rebate_report as IRR

# 8a. the PURE function, unit-level (no DB, no HTTP, no FastAPI) — the same shape as
#     device_history.device_commission_allowed.
check("pure gate: super_admin allowed", IRR.imei_rebates_allowed(CALLERS[SUPER]) is True)
check("pure gate: role 'admin' allowed", IRR.imei_rebates_allowed(CALLERS[ADMIN]) is True)
check("pure gate: perms.scope 'all' allowed", IRR.imei_rebates_allowed(CALLERS[SCOPE_ALL]) is True)
check("pure gate: perms.data.imei_rebates allowed", IRR.imei_rebates_allowed(CALLERS[GRANT_DATA]) is True)
check("pure gate: 'imei_rebates' in perms.modules allowed",
      IRR.imei_rebates_allowed(CALLERS[GRANT_MODULE]) is True)
check("pure gate: a plain commissions user is DENIED (default-closed)",
      IRR.imei_rebates_allowed(CALLERS[PLAIN]) is False)
check("pure gate: caller=None is DENIED", IRR.imei_rebates_allowed(None) is False)
check("pure gate: an empty perms dict is DENIED (no implicit access)",
      IRR.imei_rebates_allowed({"perms": {}}) is False)
check("pure gate: a DIFFERENT grant does not open this report",
      IRR.imei_rebates_allowed({"perms": {"data": {"device_commission": True, "carrier_residual": True}}}) is False)
check("pure gate: perms.data.imei_rebates FALSE is denied (an explicit off stays off)",
      IRR.imei_rebates_allowed({"perms": {"data": {"imei_rebates": False}}}) is False)
check("the grant key the frontend must mirror is exactly 'imei_rebates'", IRR.GRANT_KEY == "imei_rebates")

# 8b. through the REAL endpoint.
install(STORE_MA)
d = call(TEN, authorization=ADMIN)
_admin_imeis = [r["imei"] for r in d["rows"]]
check("endpoint: an admin opens the report normally", d["ready"] is True and d["tiles"]["activations"] == 3)
install(STORE_MA)
d = call(TEN, authorization=SUPER)
check("endpoint: a super-admin opens the report", d["ready"] is True)
install(STORE_MA)
d = call(TEN, authorization=SCOPE_ALL)
check("endpoint: a company-wide ('all') role opens the report", d["ready"] is True)
install(STORE_MA)
d = call(TEN, authorization=GRANT_DATA)
check("endpoint: the perms.data grant opens the report", d["ready"] is True and d["tiles"]["activations"] == 3)
check("a granted non-admin sees the SAME rows an admin sees (the grant is not a lesser view)",
      [r["imei"] for r in d["rows"]] == _admin_imeis)
install(STORE_MA)
d = call(TEN, authorization=GRANT_MODULE)
check("endpoint: the perms.modules grant opens the report", d["ready"] is True)

install(STORE_MA)
st, detail = denied(TEN, PLAIN)
check("endpoint: a plain commissions user is 403'd", st == 403)
check("the 403 names the permission key by hand ('imei_rebates')", "'imei_rebates'" in (detail or ""))
check("the 403 also names the human grant label for the roles UI",
      "IMEI rebate reconciliation" in (detail or ""))
check("NOT A SINGLE ROW IS READ on a denial (the gate is the first thing after require_org)",
      QUERY_LOG == [])

install(STORE_MA)
st, _ = denied(TEN, "")
check("endpoint: no token at all -> 403 (unresolvable caller degrades CLOSED)", st == 403)
install(STORE_MA)
st, _ = denied(TEN, BROKEN)
check("endpoint: a caller-resolution ERROR -> 403 (degrades CLOSED, never open)", st == 403)
check("a resolution error reads nothing either", QUERY_LOG == [])

# 8c. the two gates are INDEPENDENT and the counts/IMEIs are gated too (not just the $).
install(STORE_GATED)
d = call(HOUSE, authorization=GRANT_DATA)
check("granted non-admin on a 'permissioned' tenant: report opens but every $ is still nulled",
      d["ready"] is True and d["money_gated"] is True
      and all(r["rebate"] is None for r in d["rows"]))
install(STORE_GATED)
st, _ = denied(HOUSE, PLAIN)
check("ungranted caller gets NO counts/statuses/IMEIs either (whole report gated, not just money)",
      st == 403)

# 8d. the gate did not change the route surface.
_routes8 = [r for r in R.router.routes if getattr(r, "path", "") == "/commcalc/imei-rebates"]
check("still exactly ONE GET route for the report (no extra endpoint added)",
      len(_routes8) == 1 and set(getattr(_routes8[0], "methods", [])) == {"GET"})
check("`authorization` is a Header param on the handler (the gate's input)",
      "authorization" in inspect.signature(R.imei_rebate_report_endpoint).parameters)


print(f"\n{'='*90}\n  {_pass} passed, {_fail} failed\n{'='*90}")
sys.exit(1 if _fail else 0)
