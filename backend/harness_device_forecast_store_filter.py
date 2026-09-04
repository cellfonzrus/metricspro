"""Harness — Device Forecasting store attribution + filter semantics, PLATFORM-WIDE.

Owner directive 2026-09-04 (verbatim): "in device forecasting the cellfonz r us has the market and
store filter but luxelink does not, need to be platform wide, also on the luxlink the store name is
not showing on the forecasting of the phones."

TWO root causes, both measured live 2026-09-04 before the fix:

  1. STORE ATTRIBUTION STARVED ON THE TOTAL SIDE. A Total/MA activation is booked against the
     DEALER account (no store on the row); the only store resolution was IMEI → the POS line that
     sold it (raw_sales.serial_1). Luxelink's POS feed last fed 2026-08-09 with blank serials on
     the newer rows → 0 rows resolve in any forecast window and 0 of 1,192 ledger rows carried a
     store. Boost-side rows come from asset_ledger with a real `store` column, so the house org
     never noticed. FIX: `engine.resolve_ma_store` — POS sale line → inventory_aging_device (§11
     device snapshot, store 20/20 in the store_mapping vocabulary) → mig-314 account→store index
     (`ma_store_pnl.load_store_index`, addresses collapsed through `coa.store_resolver`) → None.
     Canonical sources only; never a new derivation.

  2. THE FILTER BAR WAS GATED ON THE ROWS. The frontend rendered StandardFilterBar only when
     optionsFromRows(loaded rows) yielded a store/market — so the tenant whose rows carried no
     store lost the WHOLE bar. The gate now keys on the canonical org roster
     (/payables/filter-options) ∪ the rows; the roster exists for every tenant, so the bar does
     too. This harness proves the SERVER half of that contract: filter-options serves non-empty
     stores+markets for a tenant whose ROWS carry no store at all.

Read-only against real tables: the fake client records writes but persists nothing.
"""
import sys, os, types

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


class _Q:
    def __init__(self, rows, sink, writes):
        self.rows, self.sink, self.writes, self.f = rows, sink, writes, []

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self

    @property
    def not_(self): return self
    def is_(self, *a, **k): return self

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def in_(self, c, vals):
        self.f.append((c, ("__in__", list(vals)))); return self

    def range(self, a, b):
        self.f.append(("__range__", (a, b))); return self

    def execute(self):
        r = self.rows
        rng = None
        for c, v in self.f:
            if c == "__range__":
                rng = v
            elif isinstance(v, tuple) and v and v[0] == "__in__":
                r = [x for x in r if x.get(c) in v[1]]
            else:
                r = [x for x in r if x.get(c) == v]
        if rng:
            r = r[rng[0]:rng[1] + 1]
        self.sink.append(list(self.f))
        return types.SimpleNamespace(data=r)

    def insert(self, rows):
        self.writes.append(("insert", rows)); return self

    def delete(self):
        self.writes.append(("delete", list(self.f))); return self

    def update(self, *a, **k):
        self.writes.append(("update", a)); return self

    upsert = update


class _S:
    def __init__(self, t, sink, writes, boom=()):
        self.t, self.sink, self.writes, self.boom = t, sink, writes, boom

    def table(self, n):
        if n in self.boom:
            raise RuntimeError(f"simulated: {n} unavailable")
        return _Q(list(self.t.get(n, [])), self.sink, self.writes)


class FakeClient:
    def __init__(self, t, boom=()):
        self.t, self.filters, self.writes, self.boom = t, [], [], boom

    def schema(self, n):
        return _S(self.t, self.filters, self.writes, self.boom)


import app.modules.payables.engine as E  # noqa: E402

LUXE, HOUSE = "854f6d7b", "00000000"

print("\n§1 · PURE PRECEDENCE — resolve_ma_store (POS → inventory → mig-314 account → None)")
INV = {"350000000000001": "4640-A W Diversey Ave", "350000000000002": "957 Pennsylvania Avenue"}
ACCT = {"170401": "2317 S Cicero Ave STE A", "170402": "3735 W 26th St"}
BYIMEI = {"350000000000003": "170401", "350000000000009": "170499"}
r = E.resolve_ma_store
ok(r("350000000000001", None, "218-80 Hempstead Avenue", INV, ACCT, BYIMEI) == "218-80 Hempstead Avenue",
   "a POS sale line ALWAYS wins (sold at B though stocked at A) — Boost-side behaviour byte-identical")
ok(r("350000000000001", "170401", None, INV, ACCT, BYIMEI) == "4640-A W Diversey Ave",
   "no POS line → the inventory snapshot's DEVICE-grain store outranks the account")
ok(r("350000000000003", "170402", None, INV, ACCT, BYIMEI) == "3735 W 26th St",
   "not in inventory → the row's own merchant account resolves via the mig-314 index")
ok(r("350000000000003", None, None, INV, ACCT, BYIMEI) == "2317 S Cicero Ave STE A",
   "row carries no account (ledger rows) → the device's own MA account fills in (imei→account)")
ok(r("350000000000009", None, None, INV, ACCT, BYIMEI) is None,
   "account known but NOT in the mig-314 index → None, never an arbitrary store")
ok(r("350000000000099", None, None, INV, ACCT, BYIMEI) is None,
   "nothing resolves → None (renders '(unassigned)', honest beats mis-attributed)")
ok(r("350000000000001", "  ", "", INV, ACCT, BYIMEI) == "4640-A W Diversey Ave",
   "blank strings are treated as missing, not as values")
ok(r("350000000000099", None, None, {}, {}, {}) is None and r("", None, None, None, None, None) is None,
   "empty/None maps are safe (org with no MA data at all)")

print("\n§2 · ma_store_resolution — composition over the CANONICAL sources (Total-side org)")
TABLES = {
    "inventory_aging_device": [
        {"org_id": LUXE, "imei": " 350000000000001 ", "store": "4640-A W Diversey Ave"},
        {"org_id": LUXE, "imei": "350000000000002", "store": None},          # store-NULL ghost row
        {"org_id": HOUSE, "imei": "350000000000008", "store": "1 Boost St"},  # other tenant
    ],
    # mig-314 inputs: fulfillment names account + address on every order row; one ambiguous tspid
    "raw_ma_fulfillment": [
        {"org_id": LUXE, "tspid": "170401", "business_address": "2317 S Cicero Ave"},
        {"org_id": LUXE, "tspid": "170403", "business_address": "3966 W Grand Ave"},
        {"org_id": LUXE, "tspid": "170404", "business_address": "5601 W Belmont Ave"},
        {"org_id": LUXE, "tspid": "170404", "business_address": "999 Somewhere Else"},  # ambiguous
    ],
    "ma_account_store_map": [
        {"org_id": LUXE, "account_id": "170405", "store_address": "957 Pennsylvania Avenue"},
    ],
    # the canonical store_mapping vocabulary the addresses must collapse onto
    "store_mapping": [
        {"org_id": LUXE, "store_code": "LUX-01", "store_address": "2317 S Cicero Ave STE A", "market": "Chicago"},
        {"org_id": LUXE, "store_code": "LUX-02", "store_address": "3966 W Grand Ave", "market": "Chicago"},
        {"org_id": LUXE, "store_code": "LUX-03", "store_address": "957 Pennsylvania Avenue", "market": "NY"},
        {"org_id": LUXE, "store_code": "LUX-04", "store_address": "4640-A W Diversey Ave", "market": "Chicago"},
    ],
    "store_aliases": [],
    "raw_ma_commission": [
        {"org_id": LUXE, "imei": "350000000000003", "merchant_account_id": "170403"},
        {"org_id": LUXE, "imei": "350000000000001", "merchant_account_id": "170401"},
    ],
}
c = FakeClient(TABLES)
resolve, meta = E.ma_store_resolution(c, LUXE)
ok(meta["inventory_imeis"] == 1, f"inventory map: normalized imei kept, NULL-store ghost dropped (got {meta})")
ok(resolve("350000000000001") == "4640-A W Diversey Ave", "device-grain: inventory store answers")
ok(resolve("350000000000003") == "3966 W Grand Ave",
   "account-grain: imei→account→mig-314 index answers for a device not in inventory")
ok(resolve("350000000000099", account="170401") == "2317 S Cicero Ave STE A",
   "the fulfillment spelling '2317 S Cicero Ave' collapses onto the canonical store_mapping "
   "spelling '2317 S Cicero Ave STE A' (coa.store_resolver — the mig-314 normalization)")
ok(resolve("350000000000099", account="170405") == "957 Pennsylvania Avenue",
   "an owner-pinned ma_account_store_map override resolves")
ok(resolve("350000000000099", account="170404") is None,
   "an AMBIGUOUS tspid (two addresses) is dropped from the index — company-wide beats mis-attributed")
ok(resolve("350000000000008") is None, "another tenant's inventory row never resolves (RULE ONE)")
ok(all(any(k == "org_id" for k, _ in f) for f in c.filters if f),
   f"every read is org-scoped ({len(c.filters)} quer(ies), all carry org_id)")
ok(not c.writes, "resolution is READ-ONLY — no write verb was invoked")

print("\n§3 · DEGRADES — no table may 500 the page")
c3 = FakeClient({}, boom=("inventory_aging_device", "raw_ma_fulfillment", "ma_account_store_map",
                          "store_mapping", "store_aliases", "raw_ma_commission"))
resolve3, meta3 = E.ma_store_resolution(c3, LUXE)
ok(resolve3("350000000000001") is None and meta3["accounts"] == 0,
   "every source unavailable → resolver answers None for everything, no raise")

print("\n§4 · LEDGER BUILD WIRING — Total-shape builds the fallback, Boost-shape never does")
calls = []
_orig = E.ma_store_resolution
E.ma_store_resolution = lambda cl, org: (calls.append(org) or (lambda i, a=None, pos_store=None: None), {})
try:
    boost_cfg = {"carrier_id": "c-boost", "source_table": "asset_ledger", "imei_field": "esn_imei",
                 "store_field": "store", "owed_field": "owed_to_vip"}
    total_cfg = {"carrier_id": "c-total", "source_table": "raw_ma_commission", "imei_field": "imei",
                 "model_field": "sku", "invoice_date_source": "field", "invoice_date_field": "tx_date",
                 "due_date_mode": "net_terms", "sold_source": "sales_match",
                 "sold_match_table": "raw_sales", "sold_match_imei_field": "serial_1",
                 "reimbursement_source": "imei_match"}
    cb = FakeClient({"asset_ledger": [], "raw_ma_commission": []})
    import datetime as _dt
    E._build_one_carrier(cb, HOUSE, boost_cfg, _dt.date(2026, 9, 4), 25, {}, set())
    ok(calls == [], "Boost-shape (store_field configured) NEVER builds the MA fallback — byte-identical")
    E._build_one_carrier(cb, LUXE, total_cfg, _dt.date(2026, 9, 4), 25, {}, set())
    ok(calls == [LUXE], "Total-shape (no store_field) builds the fallback exactly once, for its own org")
finally:
    E.ma_store_resolution = _orig

print("\n§5 · FILL SEMANTICS — the fallback only ever fills a BLANK store")
inv5 = {"350000000000001": "4640-A W Diversey Ave"}
ok(E.resolve_ma_store("350000000000001", None, "218-80 Hempstead Avenue", inv5, {}, {})
   == "218-80 Hempstead Avenue", "a store the POS/source already attributed is NEVER overwritten")

print("\n§6 · FILTER-OPTIONS CONTRACT — the bar's gate: options exist even when ROWS carry no store")
# The frontend now gates the StandardFilterBar on the canonical roster ∪ rows. Server half of the
# contract: a tenant whose ledger/forecast rows have NO store still gets non-empty stores+markets
# from /payables/filter-options (its vocabulary is store_mapping/the canonical union index — row
# coverage is irrelevant). This is exactly the luxelink shape.
import app.modules.payables.router as R  # noqa: E402
from app.core import scope as _cscope  # noqa: E402
_cscope.invalidate_market_index()
R.sb = lambda: FakeClient({"store_mapping": TABLES["store_mapping"], "store_aliases": [], "stores": []})
res = R.payables_filter_options(org_id=LUXE)
ok(len(res["stores"]) == 4 and all(s["market"] for s in res["stores"]),
   f"4 stores offered, each with a market — with ZERO rows loaded anywhere (got {len(res['stores'])})")
ok(set(res["markets"]) >= {"Chicago", "NY"},
   f"markets list is non-empty and canonical (got {res['markets']}) — the bar renders for this tenant")
_cscope.invalidate_market_index()

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  ✗ " + f)
sys.exit(1 if FAIL else 0)
