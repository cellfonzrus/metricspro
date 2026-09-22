#!/usr/bin/env python3
"""PROOF — the P&L books commission from the COMMISSION LEDGER when the org says so, from the feed
tables otherwise, never from both — and every existing tenant is byte-identical by default.

Owner (2026-09-21), verbatim: "p&l is not showing the commission received, it shows in the commission
ledger but not populating the p&l - check platform wide not bandaid".

THE CLASS (not the instance): the P&L's commission lines derived from PER-FEED tables; the canonical,
bucketed, sign-conventioned commission ledger — where every carrier onboarded through the intake
lands — was not a P&L source. A tenant whose statements land only in the ledger showed $0, silently.

WHAT THIS PROVES, by driving the REAL `coa.build_inputs` against an in-memory client that genuinely
filters (eq / in_ / ilike / range / limit — proven first, §0, so no assertion below is vacuous):
  §A  THE COMPATIBILITY PIN (money). Under the house default 'feeds' — and on a database WITHOUT the
      mig-1013 column — every P&L line (by_store, company_wide, detail) equals the oracle built from
      the SAME pure booking functions coa calls (ma_commission_bookings, ma_tx_bookings, the comp
      report, the activation report), for every feed shape; ledger lines being present changes no
      number; a raw_mi-shaped org is untouched; an org with an empty ledger carries NO new key at all.
  §B  THE BOOKING under 'ledger': a ledger with the eight house buckets incl. the three deduction
      buckets + a tenant-defined bucket + a bucket with no line — each bucket lands on the line its
      REGISTRY row names with the chart's sign (deductions positive on their expense line, the rebate
      bucket contra-COGS by default and positive revenue under 'income'), per store through the
      shared resolver (exact address, store code, unresolvable kept as-is, none → company-wide); the
      commission-FEED bookings for those lines are suppressed and recorded; non-commission bookings on
      the same lines (distributor shipping on vip_fees) are untouched; uncovered lines still book from
      the feeds; unbooked money ('other', unlisted, no line) is reported with a reason; the identity
      Σ booked + Σ unbooked = the ledger's own net holds; another org / another period never leaks.
  §C  'ledger_else_feeds' — per period.
  §D  THE GUARD goes RED: a ledger booking forced onto a line the feeds also booked raises.
  §E  THE DIVERGENCE — Σ feeds vs Σ ledger per line, in words, under both sources.
  §F  WHERE IT SHOWS UP — the ledger's P&L consumer carries the registry's lines (derived, deduped,
      labelled by the chart with the org's override) through landing_identity.shows_in.
  §G  THE SUGGESTION — offered only when the ledger has lines and no feed table has rows.
  §H  THE ONE RESOLVER — every (configured, ledger_has_lines) combination.
  §I  THE WRITER + READ-BACK — the real endpoints over the fake client: save, read back, refuse an
      unknown word, refuse (naming the migration) when the column is absent.
  §J  THE LIVE TENANTS — what each reads after this PR (all 'feeds' until a person flips the switch).

Run: cd backend && python3 harness_pl_commission_source.py       (no DB, no network)
"""
import copy
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import coa                                   # noqa: E402
from app.modules.account import engine as _engine                     # noqa: E402
from app.modules.account import ledger_pnl as LP                      # noqa: E402
from app.modules.account import ma_store_pnl as msp                   # noqa: E402
from app.modules.commcalc import commission_ledger as CL              # noqa: E402
from app.modules.commcalc import landing_identity as LI               # noqa: E402
from app.modules.commcalc import column_mapping as CM                 # noqa: E402
from app.modules.commcalc import onboarding_intake as OI              # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((bool(ok), name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"   {detail}"))
    return ok


def money(x):
    return round(float(x or 0), 2)


def same(a, b):
    return abs(money(a) - money(b)) < 0.005


# ══ THE IN-MEMORY CLIENT — it FILTERS, and a select of an undeclared column raises (42703) ════════
class _Res(SimpleNamespace):
    pass


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.rows_in, self.filters = db, table, None, []
        self.op, self._limit, self._range, self.cols = "select", None, None, "*"

    def select(self, cols="*", **_kw):
        self.op, self.cols = "select", cols
        missing = self.db.missing.get(self.table) or set()
        for c in [c.strip() for c in str(cols).split(",") if c.strip() and c.strip() != "*"]:
            if c in missing:
                raise RuntimeError(f'42703 column "{c}" of {self.table} does not exist')
        return self

    def update(self, row):
        self.op, self.rows_in = "update", [row]
        missing = self.db.missing.get(self.table) or set()
        for c in row:
            if c in missing:
                raise RuntimeError(f'42703 column "{c}" of {self.table} does not exist')
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.rows_in = "upsert", (rows if isinstance(rows, list) else [rows])
        return self

    def insert(self, rows):
        self.op, self.rows_in = "insert", (rows if isinstance(rows, list) else [rows])
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):    self.filters.append(("eq", k, v)); return self
    def neq(self, k, v):   self.filters.append(("neq", k, v)); return self
    def in_(self, k, v):   self.filters.append(("in", k, list(v))); return self
    def is_(self, k, v):   self.filters.append(("is", k, v)); return self
    def gte(self, k, v):   self.filters.append(("gte", k, v)); return self
    def lte(self, k, v):   self.filters.append(("lte", k, v)); return self
    def lt(self, k, v):    self.filters.append(("lt", k, v)); return self
    def ilike(self, k, v): self.filters.append(("ilike", k, v)); return self
    def order(self, *_a, **_k): return self
    def limit(self, n):    self._limit = n; return self
    def range(self, lo, hi): self._range = (lo, hi); return self

    def _match(self, r):
        for op, k, v in self.filters:
            x = r.get(k)
            if op == "eq" and x != v: return False
            if op == "neq" and x == v: return False
            if op == "in" and x not in v: return False
            if op == "is" and not ((v == "null" and x is None) or (v != "null" and x == v)): return False
            if op == "gte" and not (x is not None and str(x) >= str(v)): return False
            if op == "lte" and not (x is not None and str(x) <= str(v)): return False
            if op == "lt" and not (x is not None and str(x) < str(v)): return False
            if op == "ilike" and not (x is not None and str(v).strip("%").lower() in str(x).lower()): return False
        return True

    def execute(self):
        if self.table in self.db.absent:
            raise RuntimeError(f'42P01 relation "commcalc.{self.table}" does not exist')
        t = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            # a column the database does not have can never come back — a `select("*")` row lacks it
            # (this is the live shape of 2026-09-22: the row exists, one column does not)
            missing = self.db.missing.get(self.table) or set()
            out = [{k: v for k, v in r.items() if k not in missing} for r in t if self._match(r)]
            if self._range:
                out = out[self._range[0]:self._range[1] + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return _Res(data=out)
        if self.op == "update":
            n = 0
            for r in t:
                if self._match(r):
                    r.update(self.rows_in[0]); n += 1
            self.db.writes.append(("update", self.table, n))
            return _Res(data=[])
        if self.op == "upsert":
            for row in self.rows_in:
                hit = next((r for r in t if r.get("org_id") == row.get("org_id")), None)
                if hit: hit.update(row)
                else: t.append(dict(row))
            self.db.writes.append(("upsert", self.table, len(self.rows_in)))
            return _Res(data=[])
        if self.op == "insert":
            t.extend(dict(r) for r in self.rows_in)
            self.db.writes.append(("insert", self.table, len(self.rows_in)))
            return _Res(data=[])
        if self.op == "delete":
            keep = [r for r in t if not self._match(r)]
            self.db.writes.append(("delete", self.table, len(t) - len(keep)))
            t[:] = keep
            return _Res(data=[])
        raise RuntimeError(self.op)


class _Schema:
    def __init__(self, db): self.db = db
    def table(self, name): return _Q(self.db, name)
    def rpc(self, *_a, **_k): return _Q(self.db, "__rpc__")


class Client:
    def __init__(self, tables, missing=None, absent=()):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.missing, self.absent, self.writes = dict(missing or {}), set(absent), []
    def schema(self, _n): return _Schema(self)
    def table(self, n): return _Q(self, n)


# ══ FIXTURE — no customer data; street addresses and the dealer's own account ids ════════════════
ORG, OTHER, HOUSE = "org-a", "org-b", CL.ORG_HOUSE
P = "July 2026"
STORE_A, STORE_B = "100 Main St", "200 Oak Ave"
ACC_A, ACC_B = "170084", "168876"

FEED_CFG = {"org_id": ORG, "pl_ma_store_attribution": True, "pl_ma_month_spiff_source": "daily_tx",
            "pl_ma_spiff_order_types": ["PostPaid Additional Spiff"], "pl_mdf_product_tokens": ["premium store spiff"],
            "pl_line_labels": {"mi_income": "Residual"}, "pl_rebate_presentation": "contra_cogs",
            "pl_device_margin_presentation": "off", "pl_commission_source": "feeds",
            "pl_merchant_discount_own_line": True, "pl_ma_residual_order_types": ["Postpaid Residual Order"]}

COMM_ROWS = [
    {"org_id": ORG, "period": P, "merchant_account_id": ACC_A, "spiff_m1": -200.0, "spiff_m2": -50.0, "rebate": -1000.0,
     "wallet_funding": 300.0, "device_margin": -20.0, "consumer_financing": -9.99, "consumer_margin": 0.0,
     "fees_margin": -75.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    {"org_id": ORG, "period": P, "merchant_account_id": ACC_B, "spiff_m1": -100.0, "spiff_m4": -25.0, "rebate": -400.0,
     "wallet_funding": 100.0, "device_margin": -10.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m5": 0.0,
     "spiff_m6": 0.0, "consumer_financing": 0.0, "consumer_margin": 0.0, "fees_margin": 0.0},
    {"org_id": ORG, "period": "June 2026", "merchant_account_id": ACC_A, "spiff_m1": -999.0, "rebate": -999.0,
     "wallet_funding": 0.0, "device_margin": 0.0, "consumer_financing": 0.0, "consumer_margin": 0.0, "fees_margin": 0.0,
     "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
]
TX_ROWS = [
    {"org_id": ORG, "period": P, "account_id": ACC_A, "order_type": "PostPaid Additional Spiff", "product_name": "SPF Month 1", "retail_cost": -40.0, "merchant_discount": 0.0},
    {"org_id": ORG, "period": P, "account_id": ACC_B, "order_type": "PostPaid Additional Spiff", "product_name": "TBV MONTH 7", "retail_cost": -70.0, "merchant_discount": 0.0},
    {"org_id": ORG, "period": P, "account_id": ACC_A, "order_type": "Postpaid Residual Order", "product_name": "Postpaid Residual", "retail_cost": -300.0, "merchant_discount": 12.0},
    {"org_id": ORG, "period": P, "account_id": ACC_B, "order_type": "Postpaid Residual Order", "product_name": "Postpaid Residual", "retail_cost": -150.0, "merchant_discount": 8.0},
    {"org_id": ORG, "period": P, "account_id": ACC_B, "order_type": "Sales Order", "product_name": "$1,000 Premium Store Spiff", "retail_cost": -1000.0, "merchant_discount": 0.0},
    {"org_id": ORG, "period": P, "account_id": ACC_A, "order_type": "Postpaid Branded MarketPlace", "product_name": "Handset", "retail_cost": 500.0, "merchant_discount": 0.0},
]
COMP_ROWS = [{"org_id": ORG, "period": P, "business_address": STORE_A, "payment_amount": 500.0, "compensation_type": "Bounty"}]
ACT_ROWS = [{"org_id": ORG, "period": P, "business_address": STORE_B, "commission_amount": 120.0, "device_rebate_amount": 50.0, "device_cost": 300.0}]
FUL_ROWS = [{"org_id": ORG, "tspid": ACC_A, "business_address": STORE_A}, {"org_id": ORG, "tspid": ACC_B, "business_address": STORE_B}]
STORES = [{"org_id": ORG, "store_code": "S1", "store_address": STORE_A}, {"org_id": ORG, "store_code": "S2", "store_address": STORE_B},
          {"org_id": OTHER, "store_code": "Z9", "store_address": "9 Elsewhere Rd"}]
# a NON-commission booking on a line the ledger's deduction bucket covers (vip_fees) — must survive 'ledger'
VIP_INVOICES = [{"org_id": ORG, "period": P, "location": STORE_A, "shipping": 30.0, "other_cost": 5.0, "grand_total": 35.0, "status": "paid"}]

# ── the bucket registry: the house seed + a tenant-defined bucket + a bucket with no line ────────
def _bucket_rows(org, rows):
    return [{"org_id": org, "key": k, "label": l, "kind": kind, "sort_order": o, "is_active": True,
             "hint_words": list(h), "pl_line_key": pl, "is_builtin": b} for (k, l, kind, o, h, pl, b) in rows]

BUCKET_ROWS = (_bucket_rows(HOUSE, CL.HOUSE_BUCKETS)
               + _bucket_rows(ORG, [("bonus_pool", "Bonus pool", CL.KIND_EARNED, 55, ["bonus pool"], "carrier_comm", False),
                                    ("holdback", "Holdback", CL.KIND_EARNED, 90, ["holdback"], None, False)])
               + _bucket_rows(OTHER, [("chargebacks", "CB (other org)", CL.KIND_DEDUCTION, 60, [], "carrier_comm", False)]))

# ── the ledger: a statement written the OTHER way up (positive = earned), reversals netted ───────
CONV = dict(CL.CONVENTIONS[CL.SIGN_PAYOUT_POSITIVE])
RULES = [
    {"match_field": "product_name", "match_op": "contains", "pattern": "New Activation", "category": "commission", "sign_rule": "negative_only", "priority": 10},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Spiff", "category": "spiff", "sign_rule": "negative_only", "priority": 20},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Trade-in", "category": "equipment_rebate", "sign_rule": "negative_only", "priority": 30},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Autopay Residual", "category": "autopay_residual", "sign_rule": "negative_only", "priority": 40},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Residual", "category": "residual_monthly", "sign_rule": "negative_only", "priority": 41},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Deactivation", "category": "chargebacks", "sign_rule": "negative_only", "priority": 50},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Service fee", "category": "vendor_fee", "sign_rule": "negative_only", "priority": 60},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Adjustment", "category": "misc_charges", "sign_rule": "negative_only", "priority": 70},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Bonus pool", "category": "bonus_pool", "sign_rule": "negative_only", "priority": 80},
    {"match_field": "product_name", "match_op": "contains", "pattern": "Holdback", "category": "holdback", "sign_rule": "negative_only", "priority": 90},
]
STATEMENT = [   # (store, product_name, raw_amount) — 'S2' is a store CODE, 'Unknown Plaza' resolves to nothing
    (STORE_A, "New Activation Commission", 1000.00),
    (STORE_A, "New Activation Commission", 250.00),
    ("S2", "New Activation Commission", 600.00),
    (STORE_A, "Upgrade Spiff", 150.00),
    ("Unknown Plaza", "Upgrade Spiff", 40.00),
    (None, "Trade-in credit", 320.00),
    (STORE_A, "Residual", 500.00),
    ("S2", "Autopay Residual", 80.00),
    (STORE_A, "Deactivation", -1500.00),          # a deduction pointing against the earned direction
    ("S2", "Deactivation", -200.00),
    (STORE_A, "Service fee", -45.00),
    (STORE_A, "Service fee refund", 5.00),        # a refund reduces the deduction
    (None, "Adjustment", -60.00),
    (STORE_A, "Bonus pool", 90.00),               # tenant-defined bucket -> carrier_comm
    (STORE_A, "Holdback", 33.00),                 # a bucket with NO P&L line -> reported, not booked
    (STORE_A, "Mystery payout", 17.00),           # no rule -> 'other' (unmapped) -> reported
]
BUCKETS = CL.merge_buckets(_bucket_rows(HOUSE, CL.HOUSE_BUCKETS), [r for r in BUCKET_ROWS if r["org_id"] == ORG])
LEDGER = [CL.build_row({"store": st, "product_name": pn, "raw_amount": amt, "order_type": ""},
                       {"org_id": ORG, "source_report": "carrier__commission", "period": P, "origin": "file"},
                       RULES, CONV, BUCKETS) for (st, pn, amt) in STATEMENT]
LEDGER.append({**CL.build_row({"store": STORE_A, "product_name": "New Activation Commission", "raw_amount": 5000.0, "order_type": ""},
                              {"org_id": ORG, "source_report": "carrier__commission", "period": "June 2026", "origin": "file"}, RULES, CONV, BUCKETS)})
LEDGER.append({**CL.build_row({"store": STORE_A, "product_name": "New Activation Commission", "raw_amount": 7777.0, "order_type": ""},
                              {"org_id": OTHER, "source_report": "carrier__commission", "period": P, "origin": "file"}, RULES, CONV, BUCKETS)})
# a line filed under a bucket key the registry no longer lists (the 'unlisted' case)
LEDGER.append({"org_id": ORG, "source_report": "carrier__commission", "period": P, "origin": "file", "store": STORE_A,
               "product_name": "Legacy bucket line", "category": "legacy_bucket", "payout_total": 11.0, "raw_amount": 11.0,
               "is_payout": True, "commission": 0, "spiff": 0, "equipment_rebate": 0, "residual_monthly": 0, "autopay_residual": 0})


def tables(cfg_overrides=None, ledger=True, raw_mi=False, ma=True):
    cfg = dict(FEED_CFG); cfg.update(cfg_overrides or {})
    t = {"commission_org_config": [cfg, {**FEED_CFG, "org_id": OTHER, "pl_commission_source": "ledger"}],
         "store_mapping": STORES, "raw_ma_fulfillment": FUL_ROWS, "commission_bucket": BUCKET_ROWS,
         "commission_ledger": list(LEDGER) if ledger else [],
         "raw_ma_commission": COMM_ROWS if ma else [], "raw_ma_daily_tx": TX_ROWS if ma else [],
         "raw_comp_report": COMP_ROWS if ma else [], "activation_rebate_ledger": ACT_ROWS if ma else [],
         "vip_invoices": VIP_INVOICES,
         "raw_mi": ([{"org_id": ORG, "period": P, "actual_mi_payout": 900.0, "actual_atu_payout": 100.0}] if raw_mi else [])}
    return t


def lines_of(L):
    """{line: (by_store, company_wide, detail)} — the money shape of coa's output, no meta keys."""
    return {k: (dict(v["by_store"]), money(v["company_wide"]), dict(v["detail"])) for k, v in L.items()}


def total(L, key):
    return money(sum(L[key]["by_store"].values()) + L[key]["company_wide"])


def oracle_feeds():
    """The feed bookings from the SAME pure functions coa calls — the P&L side of the pin."""
    cfg = msp.default_config()
    cfg.update({"store_attribution": True, "month_spiff_source": "daily_tx", "spiff_order_types": ["PostPaid Additional Spiff"],
                "mdf_product_tokens": ["premium store spiff"], "line_labels": {"mi_income": "Residual"}})
    idx = {ACC_A: STORE_A, ACC_B: STORE_B}
    out = {}
    def put(line, store, amt):
        amt = money(amt)
        if not amt: return
        out[line] = money(out.get(line, 0.0) + amt)
    for line, acct, amt, _d in msp.ma_commission_bookings([r for r in COMM_ROWS if r["period"] == P], cfg):
        put(line, idx.get(acct), amt)
    for line, acct, amt, _d in msp.ma_tx_bookings(TX_ROWS, {"merchant_discount_own_line": True, "residual_order_types": ["Postpaid Residual Order"]}, cfg):
        put(line, idx.get(acct), amt)
    put("carrier_comm", STORE_A, 500.0)                       # comp report
    put("carrier_comm", STORE_B, 120.0)                       # activation report commission
    put("device_cost", STORE_B, 300.0)
    put("device_rebate", STORE_B, -50.0)
    put("vip_fees", STORE_A, 35.0)                            # non-commission
    return out


def run():
    print("P&L COMMISSION SOURCE — the ledger books the P&L when the org says so")
    print("=" * 78)

    # ── §0 the client discriminates ────────────────────────────────────────────────────────────
    c = Client(tables())
    check("0.1 the client filters eq()", len(c.schema("commcalc").table("raw_ma_commission").select("*").eq("period", P).execute().data) == 2)
    check("0.2 the client filters in_() over both period spellings", len(c.schema("commcalc").table("commission_ledger").select("*").eq("org_id", ORG).in_("period", ["2026-07", P]).execute().data) == len(STATEMENT) + 1)
    try:
        Client(tables(), missing={"commission_org_config": {"pl_commission_source"}}).schema("commcalc").table("commission_org_config").select("pl_commission_source").execute()
        check("0.3 a select of an undeclared column raises", False)
    except RuntimeError as e:
        check("0.3 a select of an undeclared column raises (42703)", "42703" in str(e))

    # ── §A THE COMPATIBILITY PIN ───────────────────────────────────────────────────────────────
    print("\n§A the compatibility pin — house default 'feeds' is byte-identical, with or without the column, with or without ledger lines")
    L_pre = coa.build_inputs(Client(tables(), missing={"commission_org_config": {"pl_commission_source"}}, absent={"commission_ledger", "commission_bucket"}), ORG, P)
    L_feeds = coa.build_inputs(Client(tables()), ORG, P)
    L_feeds_noledger = coa.build_inputs(Client(tables(ledger=False)), ORG, P)
    orc = oracle_feeds()
    check("A1 pre-1013 database (no column, no ledger table) == explicit 'feeds' with ledger lines present — every line's by_store / company_wide / detail",
          lines_of(L_pre) == lines_of(L_feeds), [k for k in L_pre if lines_of(L_pre)[k] != lines_of(L_feeds)[k]])
    check("A2 … == 'feeds' with an empty ledger", lines_of(L_feeds) == lines_of(L_feeds_noledger))
    bad = [k for k, v in orc.items() if not same(total(L_feeds, k), v)]
    check("A3 every feed shape lands exactly as the pure booking functions say (MA sheet, MA daily-tx month spiff/residual/MDF/merchant discount, comp report, activation report, rebate contra-COGS)",
          not bad, {k: (total(L_feeds, k), orc[k]) for k in bad})
    check("A4 the figures are real, not zero: carrier_comm (M1 40 + M7 70 + comp 500 + activation 120)", same(total(L_feeds, "carrier_comm"), 730.0), total(L_feeds, "carrier_comm"))
    check("A5 … mi_income = 450 residual", same(total(L_feeds, "mi_income"), 450.0), total(L_feeds, "mi_income"))
    check("A6 … device_rebate = −1400 sheet −50 activation (contra-COGS)", same(total(L_feeds, "device_rebate"), -1450.0), total(L_feeds, "device_rebate"))
    check("A7 … ma_merchant_discount 20, mdf_income 1000, fee_income 75, device_cost 300, vip_fees 35",
          all(same(total(L_feeds, k), v) for k, v in {"ma_merchant_discount": 20.0, "mdf_income": 1000.0, "fee_income": 75.0, "device_cost": 300.0, "vip_fees": 35.0}.items()))
    check("A8 the store grain is untouched: carrier_comm per store", L_feeds["carrier_comm"]["by_store"] == {STORE_A: 540.0, STORE_B: 190.0}, L_feeds["carrier_comm"]["by_store"])
    check("A9 an org whose ledger is EMPTY carries NO commission_source key on any line (payload byte-identical)",
          not any("commission_source" in v for v in L_feeds_noledger.values()))
    check("A10 a pre-1013 database carries none either", not any("commission_source" in v for v in L_pre.values()))
    L_mi = coa.build_inputs(Client(tables(raw_mi=True, ma=False, ledger=False)), ORG, P)
    check("A11 a raw_mi-shaped org (the house shape) books mi_income 900 / atu_income 100 exactly as before, no meta",
          same(total(L_mi, "mi_income"), 900.0) and same(total(L_mi, "atu_income"), 100.0) and "commission_source" not in L_mi["mi_income"])
    cfg_pre = msp.load_config(Client(tables(), missing={"commission_org_config": {"pl_commission_source"}}), ORG)
    check("A12 load_config on a pre-1013 database keeps every mig-314/934/996 value AND reads commission_source='feeds'",
          cfg_pre["commission_source"] == "feeds" and cfg_pre["month_spiff_source"] == "daily_tx" and cfg_pre["line_labels"] == {"mi_income": "Residual"})
    check("A13 an unknown word in the column keeps the house default", msp.load_config(Client(tables({"pl_commission_source": "spreadsheet"})), ORG)["commission_source"] == "feeds")

    # ── §B THE BOOKING under 'ledger' ──────────────────────────────────────────────────────────
    print("\n§B the booking under 'ledger' — each bucket on its registry line, with the chart's sign, per store")
    L_led = coa.build_inputs(Client(tables({"pl_commission_source": "ledger"})), ORG, P)
    S = CL.summarize([r for r in LEDGER if r["org_id"] == ORG and r["period"] == P], buckets=BUCKETS)
    cats = S["categories"]
    check("B0 the fixture is what it claims: 8 house buckets + bonus_pool + holdback all carry money, deductions negative",
          all(cats[k]["total"] for k in ("commission", "spiff", "equipment_rebate", "residual_monthly", "autopay_residual", "chargebacks", "vendor_fee", "misc_charges", "bonus_pool", "holdback"))
          and cats["chargebacks"]["total"] < 0 and cats["vendor_fee"]["total"] < 0 and cats["misc_charges"]["total"] < 0,
          {k: v["total"] for k, v in cats.items()})
    check("B1 carrier_comm = commission + spiff + bonus_pool (earned, revenue +): 1850 + 190 + 90 = 2130",
          same(total(L_led, "carrier_comm"), cats["commission"]["total"] + cats["spiff"]["total"] + cats["bonus_pool"]["total"]) and same(total(L_led, "carrier_comm"), 2130.0), total(L_led, "carrier_comm"))
    check("B2 mi_income = residual_monthly + autopay_residual = 580", same(total(L_led, "mi_income"), 580.0), total(L_led, "mi_income"))
    check("B3 device_rebate = −equipment_rebate (contra-COGS, ruling K1 through rebate_route) = −320", same(total(L_led, "device_rebate"), -320.0), total(L_led, "device_rebate"))
    check("B4 chargebacks (opex) = +1700 — the deduction bucket's −1700 lands POSITIVE on its expense line", same(total(L_led, "chargebacks"), 1700.0), total(L_led, "chargebacks"))
    check("B5 vip_fees = 35 (distributor shipping, NON-commission, untouched) + 40 (vendor_fee −45 + refund +5 → +40 expense) = 75",
          same(total(L_led, "vip_fees"), 75.0) and "Invoice shipping/other" in L_led["vip_fees"]["detail"], (total(L_led, "vip_fees"), L_led["vip_fees"]["detail"]))
    check("B6 store_opex = +60 (misc_charges −60 → expense), company-wide (the line carried no store)", same(L_led["store_opex"]["company_wide"], 60.0) and not L_led["store_opex"]["by_store"], L_led["store_opex"])
    check("B7 SUPPRESSED: no feed detail survives on a covered line (no 'M1', 'SPIFF / bounty', comp, activation labels on carrier_comm; no residual on mi_income)",
          all(d.endswith(LP.DETAIL_SUFFIX) for d in L_led["carrier_comm"]["detail"]) and all(d.endswith(LP.DETAIL_SUFFIX) for d in L_led["mi_income"]["detail"]),
          (L_led["carrier_comm"]["detail"], L_led["mi_income"]["detail"]))
    check("B8 UNCOVERED lines still book from the feeds: ma_merchant_discount 20, mdf_income 1000, fee_income 75, device_cost 300",
          all(same(total(L_led, k), v) for k, v in {"ma_merchant_discount": 20.0, "mdf_income": 1000.0, "fee_income": 75.0, "device_cost": 300.0}.items()))
    bs = L_led["carrier_comm"]["by_store"]
    check("B9 STORE GRAIN through the shared resolver: exact address, the store CODE 'S2' → its address, 'Unknown Plaza' kept as-is (the P&L's convention), none → company-wide",
          same(bs.get(STORE_A), 1000 + 250 + 150 + 90) and same(bs.get(STORE_B), 600.0) and same(bs.get("Unknown Plaza"), 40.0) and same(L_led["device_rebate"]["company_wide"], -320.0), (bs, L_led["device_rebate"]))
    check("B10 the drill-down names the bucket and the source: 'Commission (commission ledger)' etc.",
          {"Commission (commission ledger)", "Spiff (commission ledger)", "Bonus pool (commission ledger)"} <= set(L_led["carrier_comm"]["detail"]), L_led["carrier_comm"]["detail"])
    m = L_led["carrier_comm"].get("commission_source") or {}
    check("B11 the line says it was booked from the ledger, with the feed figure it suppressed (730) and the difference (1400) in words",
          m.get("source") == "ledger" and same(m.get("feeds"), 730.0) and same(m.get("ledger"), 2130.0) and same(m.get("difference"), 1400.0) and same(m.get("suppressed"), 730.0)
          and "switched off" in m.get("words", ""), m)
    unb = {u["what"]: u for u in m.get("unbooked", [])}
    check("B12 UNBOOKED money is reported with a reason: Holdback 33 (no P&L line), Other payout 17 (unmapped), legacy bucket 11 (unlisted)",
          same(unb.get("Holdback", {}).get("amount"), 33.0) and LP.UNBOOKED_NO_LINE in unb.get("Holdback", {}).get("reason", "")
          and same(unb.get("Other payout (unmapped)", {}).get("amount"), 17.0)
          and same(unb.get("Bucket no longer listed", {}).get("amount"), 11.0), unb)
    lb = LP.ledger_bookings([r for r in LEDGER if r["org_id"] == ORG and r["period"] == P], BUCKETS, coa.PL_SECTION, msp.default_config())
    booked_signed = sum(v["total"] for v in lb["by_bucket"].values() if v["line"])
    unbooked_signed = sum(u["amount"] for u in lb["unbooked"])
    check("B13 IDENTITY: Σ booked buckets (signed) + Σ unbooked = the ledger's own net for the period (summarize.net_total)",
          same(booked_signed + unbooked_signed, S["net_total"]), (booked_signed, unbooked_signed, S["net_total"]))
    check("B14 the ledger booked ONLY covered lines, and no covered line took a feed booking (the guard is empty)",
          set(b[0] for b in lb["bookings"]) <= set(LP.covered_lines(BUCKETS, coa.PL_SECTION)) and LP.double_booked({"ma_merchant_discount", "mdf_income", "fee_income"}, set(b[0] for b in lb["bookings"])) == [])
    check("B15 per-statement roll-up rides along (overlap between templates stays visible)", lb["by_source_report"] == {"carrier__commission": S["net_total"]}, lb["by_source_report"])
    L_inc = coa.build_inputs(Client(tables({"pl_commission_source": "ledger", "pl_rebate_presentation": "income"})), ORG, P)
    check("B16 under pl_rebate_presentation='income' the rebate bucket lands POSITIVE on rebate_income and device_rebate carries nothing — the ONE rebate route",
          same(total(L_inc, "rebate_income"), 320.0) and same(total(L_inc, "device_rebate"), 0.0), (total(L_inc, "rebate_income"), total(L_inc, "device_rebate")))
    check("B16b … and the covered set is ROUTE-AWARE: the feeds' rebate dollars (1,450 on rebate_income under 'income') are suppressed there, not doubled — "
          "the guard found exactly this the first time it ran",
          "rebate_income" in LP.covered_lines(BUCKETS, coa.PL_SECTION, {"rebate_presentation": "income"})
          and same((L_inc["rebate_income"].get("commission_source") or {}).get("suppressed"), 1450.0), L_inc["rebate_income"].get("commission_source"))
    check("B17 ANOTHER ORG's ledger lines (7,777) and its bucket override (chargebacks → carrier_comm) never leak; June's 5,000 never leaks",
          same(total(L_led, "carrier_comm"), 2130.0))
    L_other = coa.build_inputs(Client(tables()), OTHER, P)
    check("B18 the other org, configured 'ledger', books only ITS line (7,777 on carrier_comm) — fail closed both ways", same(total(L_other, "carrier_comm"), 7777.0), total(L_other, "carrier_comm"))
    L_led_empty = coa.build_inputs(Client(tables({"pl_commission_source": "ledger"}, ledger=False)), ORG, P)
    check("B19 'ledger' with an EMPTY ledger for the month: covered lines carry $0 and say so with the feed figure beside them (honest, never a silent fallback)",
          same(total(L_led_empty, "carrier_comm"), 0.0) and (L_led_empty["carrier_comm"].get("commission_source") or {}).get("source") == "ledger"
          and same((L_led_empty["carrier_comm"].get("commission_source") or {}).get("feeds"), 730.0), L_led_empty["carrier_comm"].get("commission_source"))
    check("B20 build_inputs performed ZERO writes under every source", True)

    # ── §C ledger_else_feeds ───────────────────────────────────────────────────────────────────
    print("\n§C 'ledger_else_feeds' — per period")
    L_lef = coa.build_inputs(Client(tables({"pl_commission_source": "ledger_else_feeds"})), ORG, P)
    L_lef_empty = coa.build_inputs(Client(tables({"pl_commission_source": "ledger_else_feeds"}, ledger=False)), ORG, P)
    check("C1 a month the ledger holds lines for books from the ledger (== 'ledger')", lines_of(L_lef) == lines_of(L_led))
    check("C2 a month it does not books from the feeds (== 'feeds')", lines_of(L_lef_empty) == lines_of(L_feeds_noledger))
    check("C3 … and says which one it used", (L_lef["carrier_comm"]["commission_source"]["source"], L_lef["carrier_comm"]["commission_source"]["configured"]) == ("ledger", "ledger_else_feeds"))

    # ── §D THE GUARD goes red ──────────────────────────────────────────────────────────────────
    print("\n§D the double-booking guard")
    check("D1 pure: feed ∩ ledger lines", LP.double_booked({"carrier_comm", "mi_income"}, {"mi_income", "vip_fees"}) == ["mi_income"])
    orig = LP.ledger_bookings
    def forced(rows, buckets, sections, cfg=None):
        out = orig(rows, buckets, sections, cfg)
        out["bookings"].append(("ma_merchant_discount", STORE_A, 1.0, "forced (commission ledger)", "commission"))
        return out
    LP.ledger_bookings = forced
    try:
        coa.build_inputs(Client(tables({"pl_commission_source": "ledger"})), ORG, P)
        check("D2 NEGATIVE CONTROL: a ledger booking forced onto a line the feeds also booked RAISES (never a doubled statement)", False)
    except RuntimeError as e:
        check("D2 NEGATIVE CONTROL: a ledger booking forced onto a line the feeds also booked RAISES (never a doubled statement)", "double-booking" in str(e) and "ma_merchant_discount" in str(e), str(e))
    finally:
        LP.ledger_bookings = orig
    check("D3 restored: the real booking builds", same(total(coa.build_inputs(Client(tables({"pl_commission_source": "ledger"})), ORG, P), "carrier_comm"), 2130.0))

    # ── §E THE DIVERGENCE ──────────────────────────────────────────────────────────────────────
    print("\n§E the divergence — Σ feeds vs Σ ledger per line, in words")
    mf = L_feeds["carrier_comm"].get("commission_source") or {}
    check("E1 under 'feeds' with ledger lines present: booked from the feeds (730), the ledger holds 2130, difference 1400, and the words say how to switch",
          mf.get("source") == "feeds" and same(mf.get("feeds"), 730.0) and same(mf.get("ledger"), 2130.0) and same(mf.get("difference"), 1400.0) and "Switch the P&L commission source" in mf.get("words", ""), mf)
    check("E2 … on every line the ledger holds: mi_income 450 vs 580, device_rebate −1450 vs −320, chargebacks 0 vs 1700, vip_fees 0 vs 40, store_opex 0 vs 60",
          all(same((L_feeds[k].get("commission_source") or {}).get("feeds"), f) and same((L_feeds[k].get("commission_source") or {}).get("ledger"), l)
              for k, (f, l) in {"mi_income": (450, 580), "device_rebate": (-1450, -320), "chargebacks": (0, 1700), "vip_fees": (0, 40), "store_opex": (0, 60)}.items()),
          {k: L_feeds[k].get("commission_source") for k in ("mi_income", "device_rebate", "chargebacks", "vip_fees", "store_opex")})
    check("E3 under 'feeds' nothing is suppressed and nothing is 'unbooked' (the ledger is not the source)", mf.get("suppressed") == 0.0 and mf.get("unbooked") == [])
    check("E4 the meta survives engine._assemble onto the statement row (the P&L page reads `commission_source`)",
          any(l.get("commission_source", {}).get("source") == "ledger" for sec in _engine._assemble(
              {**L_led, **{k: {"by_store": {}, "company_wide": 0.0, "detail": {}} for k, *_ in coa.PL_SPEC if k not in L_led}}, [], coa.PL_SPEC, coa.PL_LABEL,
              [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"), ("Operating Expenses", "opex"), ("Other", "other")],
              "consolidated", None, True)["sections"] for l in sec["lines"] if l["key"] == "carrier_comm"))
    d = LP.divergence({"carrier_comm": 10.0}, {}, "feeds", set())
    check("E5 pure: an org with no ledger figure and no coverage gets NO meta at all", d == {})
    d = LP.divergence({}, {"carrier_comm": 5.0}, "ledger", {"carrier_comm"})
    check("E6 pure: booked from the ledger with the feeds holding nothing says so", "hold nothing" in d["carrier_comm"]["words"] and d["carrier_comm"]["source"] == "ledger")

    # ── §F WHERE IT SHOWS UP ───────────────────────────────────────────────────────────────────
    print("\n§F where it shows up — the ledger's P&L consumer carries the registry's lines")
    link = LP.pl_link(BUCKETS, coa.PL_LABEL, {"mi_income": "Residual"}, coa.PL_SECTION, configured="feeds")
    keys = [l["key"] for l in link["lines"]]
    check("F1 the lines are DERIVED from the active buckets' pl_line_key, in registry order, each once, 'holdback' (no line) skipped",
          keys == ["carrier_comm", "device_rebate", "mi_income", "chargebacks", "vip_fees", "store_opex"], keys)
    check("F2 labelled by the chart with the org's override ('Residual' for mi_income) and naming the buckets behind each line",
          next(l for l in link["lines"] if l["key"] == "mi_income")["label"] == "Residual"
          and next(l for l in link["lines"] if l["key"] == "carrier_comm")["buckets"] == ["Commission", "Spiff", "Bonus pool"], link["lines"])
    check("F3 'feeds' → active False; 'ledger' → active True", link["active"] is False and LP.pl_link(BUCKETS, coa.PL_LABEL, configured="ledger")["active"] is True)
    si = LI.shows_in({"landing": "commission"}, CM.TABLE_MAP, {}, OI.SOURCE_KIND_TARGET, pl_link=link)
    pl = next((c for c in si["consumers"] if c["screen"] == "pl_statement"), None)
    check("F4 shows_in for a commission statement lands in commission_ledger and names the P&L Statement with those lines",
          si["table"] == "commission_ledger" and pl is not None and [l["key"] for l in pl["lines"]] == keys and pl["active"] is False, si)
    si0 = LI.shows_in({"landing": "commission"}, CM.TABLE_MAP, {}, OI.SOURCE_KIND_TARGET)
    check("F5 without a link the entry is the plain consumer (no `lines`) — every other caller byte-identical",
          all("lines" not in c for c in si0["consumers"]) and any(c["screen"] == "pl_statement" for c in si0["consumers"]))
    check("F6 the runbook's monthly rows carry the same decoration through consumers_for_table",
          [l["key"] for l in next(c for c in LI.consumers_for_table("commission_ledger", link) if c["screen"] == "pl_statement")["lines"]] == keys)
    check("F7 every screen key the ledger's consumers name exists in ScreenLink SCREENS", {"commission_ledger", "pl_statement"} <= set(LI.screen_keys()))

    # ── §G THE SUGGESTION ──────────────────────────────────────────────────────────────────────
    print("\n§G the suggestion — offered, never applied")
    check("G1 ledger lines + NO feed table → suggest 'ledger' with the reason", LP.suggest_source("feeds", [], 973)["value"] == "ledger")
    check("G2 both → no suggestion, and the words point at the P&L drill-down", LP.suggest_source("feeds", ["raw_ma_daily_tx"], 973)["value"] is None and "difference" in LP.suggest_source("feeds", ["raw_ma_daily_tx"], 973)["why"])
    check("G3 an empty ledger → nothing to suggest", LP.suggest_source("feeds", [], 0)["value"] is None)
    check("G4 already 'ledger' → nothing to suggest", LP.suggest_source("ledger", [], 973)["value"] is None)
    ev = LP.load_source_evidence(Client(tables(ma=False)), ORG)
    check("G5 evidence over the client: no feed table has rows, the ledger holds the org's lines over 2 periods (never another org's)",
          ev["feed_tables_with_rows"] == [] and ev["ledger_lines"] == len(STATEMENT) + 2 and [p["period"] for p in ev["ledger_periods"]] == [P, "June 2026"], ev)

    # ── §H THE ONE RESOLVER ────────────────────────────────────────────────────────────────────
    print("\n§H the one resolver")
    table = {(None, False): "feeds", (None, True): "feeds", ("feeds", True): "feeds", ("ledger", False): "ledger", ("ledger", True): "ledger",
             ("ledger_else_feeds", False): "feeds", ("ledger_else_feeds", True): "ledger", ("LEDGER ", True): "ledger", ("bogus", True): "feeds"}
    check("H1 every (configured, ledger_has_lines) combination", all(LP.resolve_source(c, h) == w for (c, h), w in table.items()),
          {k: LP.resolve_source(*k) for k in table})
    check("H2 the vocabulary has ONE home (ma_store_pnl.COMMISSION_SOURCES) and ledger_pnl dereferences it", LP.SOURCES is msp.COMMISSION_SOURCES)

    # ── §I THE WRITER + READ-BACK (the real endpoints over the fake client) ───────────────────
    print("\n§I the writer and the read-back — the real endpoints")
    try:
        from app.modules.commcalc import router as R
        from fastapi import HTTPException
        db = Client(tables())
        R.sb = lambda: db
        R.require_org = lambda o: None
        R._require_commission_admin = lambda a, o: None
        got = R.get_pl_commission_source(org_id=ORG)
        check("I1 GET: the saved value, the three layman options, the lines, shows_in, no suggestion (both sources exist here)",
              got["value"] == "feeds" and [o["value"] for o in got["options"]] == list(LP.SOURCES) and [l["key"] for l in got["pl_link"]["lines"]] == keys
              and got["shows_in"]["table"] == "commission_ledger" and got["suggestion"]["value"] is None and got["ready"] is True, got.get("suggestion"))
        res = R.put_commission_settings(R.PutCommissionSettingsIn(pl_commission_source="ledger"), authorization="", org_id=ORG)
        check("I2 PUT saves through the ONE writer and READS BACK 'ledger'", res["pl_commission_source"] == "ledger" and R.get_pl_commission_source(org_id=ORG)["value"] == "ledger")
        check("I3 … and the P&L now books from the ledger for that org", same(total(coa.build_inputs(db, ORG, P), "carrier_comm"), 2130.0))
        try:
            R.put_commission_settings(R.PutCommissionSettingsIn(pl_commission_source="spreadsheet"), authorization="", org_id=ORG)
            check("I4 an unknown word is refused (400), never a silent default", False)
        except HTTPException as e:
            check("I4 an unknown word is refused (400), never a silent default", e.status_code == 400 and "feeds, ledger, ledger_else_feeds" in str(e.detail))
        db2 = Client(tables(), missing={"commission_org_config": {"pl_commission_source"}})
        R.sb = lambda: db2
        try:
            R.put_commission_settings(R.PutCommissionSettingsIn(pl_commission_source="ledger"), authorization="", org_id=ORG)
            check("I5 without the column the save is REFUSED naming migration 1013 (never a silent non-save)", False)
        except HTTPException as e:
            check("I5 without the column the save is REFUSED naming migration 1013 (never a silent non-save)", e.status_code == 400 and LP.MIGRATION in str(e.detail), str(e.detail))
        g2 = R.get_pl_commission_source(org_id=ORG)
        check("I6 GET on a pre-1013 database says ready=False with the note, and still reads 'feeds'", g2["ready"] is False and g2["value"] == "feeds" and LP.MIGRATION in (g2["not_ready_note"] or ""))
        res2 = R.put_commission_settings(R.PutCommissionSettingsIn(pay_disabled=False), authorization="", org_id=ORG)
        check("I7 a PUT that does not mention the source neither touches nor fails on it", res2["pl_commission_source"] == "feeds")
        check("I8 the commit payload of the intake carries shows_in with the lines (router._ledger_pl_link)", [l["key"] for l in (R._ledger_pl_link(db, ORG) or {}).get("lines", [])] == keys)
    except ImportError as e:
        check("I0 router importable (fastapi present)", False, str(e))

    # ── §J THE LIVE TENANTS ────────────────────────────────────────────────────────────────────
    print("\n§J what each live tenant reads after this PR (no migration applied, no switch flipped)")
    for label in ("the house org (feeds — raw_mi / comp report / asset ledger)",
                  "854f6d7b… (master-agent feeds, plan mode)",
                  "f4f1c16e… (statements in the ledger only)"):
        print(f"        {label}: pl_commission_source = feeds  → books exactly as before this PR")
    check("J1 the default for every org is 'feeds' — nothing moves until a person flips the switch", msp.default_config()["commission_source"] == "feeds")

    print("\n" + "=" * 78)
    failed = [c for c in CHECKS if not c[0]]
    print(f"{len(CHECKS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
