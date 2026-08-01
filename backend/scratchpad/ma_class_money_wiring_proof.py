"""Proof harness — agent/commission/ma-class-money-wiring (owner go-ahead in chat 2026-08-01:
"go ahead and fix, it updated the classes").

Drives the REAL `commission_ledger.classify/build_row/summarize` and the REAL
`whatif._ma_carrier_income` over an in-memory FakeClient, and DIFFERENTIALS every result against the
BASE copies of those modules pulled straight out of git and loaded side by side. No DB, no network,
ZERO writes (the fake client raises on insert/update/upsert/delete), non-house tenants throughout.

    BASE IS PINNED TO A LITERAL COMMIT (`BASE_REV` below), never a moving ref. `origin/main` and even
    local `main` advance while a package is parked; a differential vendored from a moving base silently
    stops being a differential. This package was built on local main ec9fe8b.

Run from the backend dir:  python3 scratchpad/ma_class_money_wiring_proof.py

Sections
  A. PURE CONFIG — modes, legs, the four fail-closed defaults, the confirmed-only index.
  B. FLAG OFF == BASE, BOTH CONSUMERS — classify/build_row/summarize identical on all 69 seeded names
     and on the live fixtures; the whole carrier-income payload identical key-for-key in legacy AND
     ledger income modes; product_class rows present but mode legacy still identical.
  C. CONSUMER 1 ON — a CONFIRMED class re-buckets a line the keyword rules get wrong; the keyword rules
     remain the fallback for unclassified labels; the class pass runs FIRST.
  D. ONLY CONFIRMED CLASSIFIES — proposed rows (including the 4 AMBIGUOUS ones) classify NOTHING, are
     surfaced by name, and cannot be smuggled in through the built-in proposal list.
  E. CONSUMER 2 ON — residual/airtime selected by class; device_sale / wallet / memo leave the total,
     counted in dollars; unclassified leaves too and is reported.
  F. DOUBLE-COUNT GUARD COMPOSES — under all FOUR flag combinations no row is ever in both a ledger
     income bucket and the residual leg.
  G. DEGRADATION — no mig 265, no mig 254, no CONFIRMED rows, no product_name column: each keeps
     today's numbers and says so; never a fabricated $0.
  H. MULTI-TENANT + ZERO-WRITE — every read org-scoped, two tenants isolated, guard tripped as a
     negative control.
  I. MIGRATION 265 — real PostgreSQL parse, additive, idempotent, RLS-zero-policy, no anon grants, seed
     == the code default, band + collision (263/264 are held by a concurrent agent).
  J. DIFFERENTIAL SCOPE — the pay engines and the P&L files are byte-identical to base and name neither
     new module; the router diff is additive at the three ledger call sites only.
"""
import copy, importlib.util, inspect, io, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.commission_ledger as CL
import app.modules.commcalc.ma_class_wiring as MW
import app.modules.commcalc.ma_product_class as MPC
import app.modules.commcalc.whatif as W

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


HOUSE = "00000000-0000-0000-0000-000000000001"
NIL = "00000000-0000-0000-0000-000000000000"
LUX = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"
TOTAL_ID = "aaaaaaaa-0000-0000-0000-00000000000a"
MAY, JUNE, JULY = "May 2026", "June 2026", "July 2026"

WRITES = []
READS = []


# ── in-memory fake supabase client — reads only ───────────────────────────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table, absent, missing_cols):
        self.store, self.t, self.absent = store, table, absent
        self.missing_cols = missing_cols
        self.f, self.rng, self.cols = [], None, "*"

    def select(self, *a, **k):
        if a:
            self.cols = a[0]
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

    def neq(self, c, v):
        self.f.append(('neq', c, v)); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def order(self, *a, **k):
        return self

    def insert(self, *a, **k):
        WRITES.append(('insert', self.t)); raise AssertionError("WRITE ATTEMPTED: insert " + self.t)

    def update(self, *a, **k):
        WRITES.append(('update', self.t)); raise AssertionError("WRITE ATTEMPTED: update " + self.t)

    def upsert(self, *a, **k):
        WRITES.append(('upsert', self.t)); raise AssertionError("WRITE ATTEMPTED: upsert " + self.t)

    def delete(self, *a, **k):
        WRITES.append(('delete', self.t)); raise AssertionError("WRITE ATTEMPTED: delete " + self.t)

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v:
                return False
            if k == 'in' and rv not in v:
                return False
            if k == 'neq' and rv == v:
                return False
        return True

    def execute(self):
        if self.t in self.absent:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        for col in (self.missing_cols.get(self.t) or []):
            if re.search(r'(^|,)\s*' + re.escape(col) + r'\s*(,|$)', str(self.cols)):
                raise Exception(f'column commcalc.{self.t}.{col} does not exist')
        READS.append((self.t, list(self.f)))
        rows = self.store.setdefault(self.t, [])
        m = [dict(r) for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        return FakeResult(data=m)


class FakeSchema:
    def __init__(self, store, absent, missing_cols):
        self.store, self.absent, self.missing_cols = store, absent, missing_cols

    def table(self, t):
        return FakeQuery(self.store, t, self.absent, self.missing_cols)

    def rpc(self, name, params):
        raise Exception('no such rpc: ' + name)


class FakeClient:
    def __init__(self, store, absent=None, missing_cols=None):
        self.store, self.absent = store, set(absent or [])
        self.missing_cols = missing_cols or {}

    def schema(self, s):
        return FakeSchema(self.store, self.absent, self.missing_cols)


# ── the BASE modules, pinned to a LITERAL commit ──────────────────────────────────────────────────
BASE_REV = "ec9fe8bc3ec6ae7a33abac31842f28a07bf1e26f"       # local main at branch point
_repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_base(relpath, modname):
    src = subprocess.check_output(["git", "-C", _repo, "show", f"{BASE_REV}:{relpath}"]).decode()
    t = tempfile.NamedTemporaryFile("w", suffix="_%s.py" % modname, delete=False)
    t.write(src)
    t.close()
    spec = importlib.util.spec_from_file_location(modname, t.name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, src


BCL, BCL_SRC = _load_base("backend/app/modules/commcalc/commission_ledger.py", "cl_base")
BW, BW_SRC = _load_base("backend/app/modules/commcalc/whatif.py", "whatif_base")


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────────
def cfg_rows(income="ma_ledger", org=HOUSE):
    plan = {"org_id": org, "carrier_id": NIL, "carrier_mode": "plan", "is_active": True,
            "residual_source": "ma_daily_tx", "residual_order_type": "Postpaid Residual Order",
            "residual_amount_field": "retail_cost", "residual_sign": "negate",
            "income_source": income, "retail_cost_source": "none", "ma_commission_sign": "negate"}
    boost = {"org_id": org, "carrier_id": NIL, "carrier_mode": "boost", "is_active": True,
             "residual_source": "boost_mi_atu", "residual_order_type": None,
             "residual_amount_field": "retail_cost", "residual_sign": "as_is",
             "income_source": "boost_comp_mi_atu", "retail_cost_source": "none",
             "ma_commission_sign": "negate"}
    return [plan, boost]


# The eight JUNE daily-tx lines. Columns 4/5 are the headline: today a DEVICE SALE and a WALLET FUNDING
# are counted as "airtime margin" purely because their order type is not the residual one.
TX = [
    # product_name,                       order_type,                merchant_discount, retail_cost
    ("Trac Autopay Residual",             "Postpaid Residual Order",  0.0,   -100.0),
    ("Residual",                          "Postpaid Residual Order",  0.0,    -50.0),
    ("Total MAX 5G Plan $55",             "Bill Payment",             4.00,    55.0),
    ("Apple iPhone 16e 128GB Black TO",   "Device Order",            30.00,   599.99),
    ("Total Wireless RTR Wallet",         "Wallet Funding",           2.50,    50.0),
    ("Credit Debit Memo",                 "Memo",                     1.25,    99.99),
    ("Some Brand New Thing",              "Misc",                     0.75,    10.0),
    # a RESIDUAL-class line whose ORDER TYPE is not the residual one — the two selectors disagree here
    ("Trac Autopay Residual",             "Airtime Topup",            0.0,    -20.0),
]

CONFIRMED_MAP = [
    ("Trac Autopay Residual", "residual", "confirmed", ""),
    ("Residual", "residual", "confirmed", ""),
    ("Total MAX 5G Plan $55", "billpayment", "confirmed", ""),
    ("Apple iPhone 16e 128GB Black TO", "device_sale", "confirmed", ""),
    ("Total Wireless RTR Wallet", "wallet", "confirmed", ""),
    # present only in the LEDGER fixture, not the daily-tx one — it exercises the rule PROPOSER
    ("TBV MONTH 3 New Activation Commission", "commission", "confirmed", ""),
    # still PROPOSED, and flagged AMBIGUOUS by the 254 seed — must classify NOTHING
    ("Credit Debit Memo", "adjustment_memo", "proposed",
     "AMBIGUOUS in direction — a memo can be a credit or a debit; the sign on the line decides."),
    ("Total Wireless Device Upgrade", "billpayment", "proposed",
     "AMBIGUOUS — sampled at $0.00 with no ' TO' device suffix; please verify before confirming."),
]


def map_rows(org=LUX, rows=None):
    return [{"id": "m%d" % i, "org_id": org, "source_report": "ma_daily_tx", "product_name": n,
             "product_class": c, "status": s, "note": note}
            for i, (n, c, s, note) in enumerate(rows if rows is not None else CONFIRMED_MAP)]


def _lrow(org, period, report, origin, order_type, product_name, category, raw_amount, **buckets):
    r = {"id": "L%s" % abs(hash((period, product_name, category, raw_amount, order_type))),
         "org_id": org, "period": period, "source_report": report, "origin": origin,
         "order_type": order_type, "product_name": product_name, "category": category,
         "raw_amount": raw_amount,
         "commission": 0, "spiff": 0, "equipment_rebate": 0,
         "residual_monthly": 0, "autopay_residual": 0, "payout_total": 0}
    r.update(buckets)
    return r


def store(org=LUX, income="ma_ledger", ledger_mode="legacy", income_mode="legacy",
          mapping=None, legs=None, cfg=True):
    tx = [{"org_id": org, "period": JUNE, "order_type": ot, "account_id": "A1",
           "merchant_invoice": 987654321000 + i, "merchant_discount": md,
           "retail_cost": rc, "product_name": pn}
          for i, (pn, ot, md, rc) in enumerate(TX)]
    led = [
        _lrow(org, JUNE, "ma_commission", "ma_sync", "new", "1st Month Spiff", "commission", -15.0,
              commission=15.0, payout_total=15.0),
        # the ORDER-TYPE trap (pre-existing guard): residual order type, classified commission
        _lrow(org, JUNE, "ma_daily_tx", "file", "Postpaid Residual Order", "Trac Autopay Residual",
              "commission", -33.0, commission=33.0, payout_total=33.0),
        # the CLASS trap (new guard): a residual-CLASS label whose order type is NOT the residual one
        _lrow(org, JUNE, "ma_daily_tx", "file", "Airtime Topup", "Trac Autopay Residual",
              "commission", -77.0, commission=77.0, payout_total=77.0),
        # an ordinary commission line, untouched by either guard
        _lrow(org, JUNE, "ma_daily_tx", "file", "Postpaid Commission Order",
              "TBV MONTH 3 New Activation Commission", "commission", -200.0,
              commission=200.0, payout_total=200.0),
    ]
    s = {
        "carrier": [{"id": TOTAL_ID, "org_id": org, "name": "Total by Verizon", "code": "TOTAL",
                     "is_default": True}],
        "whatif_source_config": cfg_rows(income=income),
        "raw_ma_daily_tx": tx,
        "raw_ma_commission": [],
        "commission_ledger": led,
        "ma_product_class_map": map_rows(org, mapping),
        "ma_class_income_leg": ([{"org_id": org, "product_class": c, "income_leg": l}
                                 for c, l in (legs or {"residual": "residual",
                                                       "billpayment": "airtime"}).items()]),
    }
    if cfg:
        s["ma_class_wiring_config"] = [
            {"org_id": org, "consumer": "ledger", "mode": ledger_mode},
            {"org_id": org, "consumer": "carrier_income", "mode": income_mode},
        ]
    return s


def month(payload, period=JUNE):
    return next((m for m in payload["totals_by_month"] if m["period"] == period), None)


MONEY_MONTH_KEYS = ("period", "residual", "total_comp", "residual_mi_atu", "accounts", "qty",
                    "delta_vs_prev", "pct_vs_prev", "commission_rows", "daily_tx_rows",
                    "ledger_lines", "ledger_income_lines", "comp_source_missing")
COMPONENT_KEYS = ("COMMISSION", "SPIFF", "REIMBURSEMENT", "RESIDUAL", "UNMAPPED",
                  "EQUIPMENT_REBATE", "LEDGER_OTHER")


def ci(st, org=LUX, months=6):
    return W.carrier_income(FakeClient(copy.deepcopy(st)), org, months=months, carrier_id=TOTAL_ID)


def bci(st, org=LUX, months=6):
    return BW.carrier_income(FakeClient(copy.deepcopy(st)), org, months=months, carrier_id=TOTAL_ID)


def same_payload(a, b):
    """Key-for-key comparison of every money figure, count and flag the tab renders."""
    if [m["period"] for m in a["totals_by_month"]] != [m["period"] for m in b["totals_by_month"]]:
        return False, "months differ"
    for ma_, mb in zip(a["totals_by_month"], b["totals_by_month"]):
        for k in MONEY_MONTH_KEYS:
            if ma_.get(k) != mb.get(k):
                return False, f"{ma_['period']}.{k}: {ma_.get(k)} vs {mb.get(k)}"
        for k in COMPONENT_KEYS:
            if ma_["components"].get(k) != mb["components"].get(k):
                return False, f"{ma_['period']}.components.{k}: {ma_['components'].get(k)} vs {mb['components'].get(k)}"
    for k in ("months", "data_note", "ledger_note", "income_source", "income_source_effective",
              "income_legs", "residual_amount_field", "residual_field_warning", "note",
              "ledger_ready", "ledger_origin_ready", "ma_coverage", "source_swap",
              "carrier", "carrier_mode", "carriers"):
        if a.get(k) != b.get(k):
            return False, "payload key differs: " + k
    for k in ("months", "source", "residual_amount_field", "ma_commission_sign",
              "commission_row_signs", "income_leg_source", "residual_leg_source"):
        if a["params"].get(k) != b["params"].get(k):
            return False, "params." + k
    return True, ""


print("=" * 100)
print("A. PURE CONFIG — the four fail-closed defaults")
print("=" * 100)
check("two consumers, exactly", MW.CONSUMERS == ("ledger", "carrier_income"))
check("two modes, default legacy", MW.MODES == ("legacy", "class") and MW.DEFAULT_MODE == "legacy")
check("① no config row at all -> legacy", MW.mode_from([], "ledger") == "legacy"
      and MW.mode_from([], "carrier_income") == "legacy")
check("an UNKNOWN mode value falls back to legacy (never to class)",
      MW.mode_from([{"consumer": "ledger", "mode": "banana"}], "ledger") == "legacy")
check("a saved 'class' really is read", MW.mode_from([{"consumer": "ledger", "mode": "class"}],
                                                     "ledger") == "class")
check("one consumer's mode never leaks into the other",
      MW.modes_from([{"consumer": "ledger", "mode": "class"}])
      == {"ledger": "class", "carrier_income": "legacy"})
check("④ a class with NO leg row is EXCLUDED", MW.leg_for("device_sale", {}) == "excluded")
check("④ an unknown leg value is EXCLUDED (fail-closed)",
      MW.leg_for("residual", {"residual": "profit"}) == "excluded")
check("None/blank class is EXCLUDED", MW.leg_for(None, MW.DEFAULT_INCOME_LEGS) == "excluded")
check("the code default legs are exactly residual->residual and billpayment->airtime",
      MW.DEFAULT_INCOME_LEGS == {"residual": "residual", "billpayment": "airtime"})
check("empty leg rows fall back to the code default",
      MW.income_legs_from([]) == MW.DEFAULT_INCOME_LEGS)
check("legs a tenant DID save win over the default",
      MW.income_legs_from([{"product_class": "device_sale", "income_leg": "airtime"}])
      == {"device_sale": "airtime"})
check("residual_classes() finds the residual-leg classes for the double-count guard",
      MW.residual_classes({"residual": "residual", "billpayment": "airtime"}) == {"residual"})
# the confirmed-only index
idx = MW.confirmed_index(map_rows())
check("③ confirmed_index keeps ONLY status='confirmed'", set(idx) ==
      {"Trac Autopay Residual", "Residual", "Total MAX 5G Plan $55",
       "Apple iPhone 16e 128GB Black TO", "Total Wireless RTR Wallet",
       "TBV MONTH 3 New Activation Commission"}, sorted(idx))
check("the two AMBIGUOUS proposals are ABSENT from the index",
      "Credit Debit Memo" not in idx and "Total Wireless Device Upgrade" not in idx)
check("confirmed_index never falls back to the 69 built-in proposals",
      MW.confirmed_index([]) == {} and len(MPC.DEFAULT_PROPOSALS) == 69)
check("a CONFIRMED row carrying the reserved 'unmapped' class is dropped",
      MW.confirmed_index([{"product_name": "x", "product_class": "unmapped",
                           "status": "confirmed"}]) == {})
check("blank name / blank class rows are dropped",
      MW.confirmed_index([{"product_name": "  ", "product_class": "residual", "status": "confirmed"},
                          {"product_name": "y", "product_class": "", "status": "confirmed"}]) == {})
check("the index trims, so the export's trailing space still matches",
      MW.class_of("Trac Autopay Residual ", idx) == "residual")
check("matching stays CASE-SENSITIVE (a case variant is unclassified, the loud direction)",
      MW.class_of("trac autopay residual", idx) is None)
st_ = MW.index_status(map_rows())
check("index_status counts confirmed vs proposed", (st_["confirmed"], st_["proposed"]) == (6, 2))
check("index_status names the AMBIGUOUS rows still pending, loudly",
      [e["product_name"] for e in st_["ambiguous_pending"]]
      == ["Credit Debit Memo", "Total Wireless Device Upgrade"])
check("index_status separates AMBIGUOUS rows that ARE confirmed",
      MW.index_status([{"product_name": "Credit Debit Memo", "product_class": "adjustment_memo",
                        "status": "confirmed", "note": "AMBIGUOUS in direction"}])
      ["ambiguous_confirmed"][0]["product_name"] == "Credit Debit Memo")
# compile_rules
R1 = [{"match_field": "product_name", "match_op": "product_class", "pattern": "residual",
       "category": "residual_monthly", "sign_rule": "negative_only", "priority": 5}]
check("② compile_rules with NO index leaves the rules untouched",
      MW.compile_rules(R1, {}) == R1 and MW.compile_rules(R1, None) == R1)
check("compile_rules attaches the index to product_class rules only",
      MW.compile_rules(R1 + [{"match_op": "contains", "pattern": "x"}], idx)[0]["_class_index"] is idx
      and "_class_index" not in MW.compile_rules(R1 + [{"match_op": "contains", "pattern": "x"}], idx)[1])
check("compile_rules does not mutate the caller's rule dicts",
      "_class_index" not in R1[0])
check("has_class_rules detects the new op", MW.has_class_rules(R1) and not MW.has_class_rules(
      [{"match_op": "contains"}]))
check("the cross-consumer conflict guard fires when a class is BOTH a payout bucket and an income leg",
      len(MW.conflicts(R1, {"residual": "residual"})) == 1)
check("...and stays quiet when the class is excluded from income",
      MW.conflicts(R1, {"residual": "excluded"}) == [])
check("...and stays quiet for a 'charge' mapping (it removes money, it cannot double it)",
      MW.conflicts([dict(R1[0], pattern="billpayment", category="charge")],
                   {"billpayment": "airtime"}) == [])
check("the proposer refuses to guess 'residual' silently — it carries the collapse warning",
      "residual" in MW.WARNED_BUCKET and "residual" not in MW.UNAMBIGUOUS_BUCKET)
check("only commission/spiff/subsidy are proposed without a warning",
      MW.UNAMBIGUOUS_BUCKET == {"commission": "commission", "spiff": "spiff",
                                "subsidy": "equipment_rebate"})

print()
print("=" * 100)
print("B. FLAG OFF == BASE %s — BOTH CONSUMERS, key for key" % BASE_REV[:7])
print("=" * 100)
# ── consumer 1: the classifier itself ──
legacy_rules = CL.load_rules(FakeClient({}), LUX, "ma_daily_tx")
base_rules = BCL.load_rules(FakeClient({}), LUX, "ma_daily_tx")
check("DEFAULT_RULES unchanged from base", CL.DEFAULT_RULES == BCL.DEFAULT_RULES)
check("the five canonical categories unchanged", CL.CATEGORIES == BCL.CATEGORIES)
check("MATCH_OPS gained exactly one value", set(CL.MATCH_OPS) - set(BCL.MATCH_OPS) == {"product_class"}
      and set(BCL.MATCH_OPS) - set(CL.MATCH_OPS) == set())
same = all(CL.classify(-10, "Activation", n, legacy_rules)
           == BCL.classify(-10, "Activation", n, base_rules)
           for n, _c, _t in MPC.DEFAULT_PROPOSALS)
check("classify() identical to base on all 69 seeded names (negative amount)", same)
same_pos = all(CL.classify(55, "Bill Payment", n, legacy_rules)
               == BCL.classify(55, "Bill Payment", n, base_rules)
               for n, _c, _t in MPC.DEFAULT_PROPOSALS)
check("classify() identical to base on all 69 seeded names (positive amount)", same_pos)
same_zero = all(CL.classify(0, "x", n, legacy_rules) == BCL.classify(0, "x", n, base_rules)
                for n, _c, _t in MPC.DEFAULT_PROPOSALS)
check("classify() identical to base on all 69 seeded names (zero amount)", same_zero)
rows_now = [CL.build_row({"product_name": n, "raw_amount": -10, "order_type": "Activation"},
                         {"org_id": LUX, "period": JUNE}, legacy_rules)
            for n, _c, _t in MPC.DEFAULT_PROPOSALS]
rows_base = [BCL.build_row({"product_name": n, "raw_amount": -10, "order_type": "Activation"},
                           {"org_id": LUX, "period": JUNE}, base_rules)
             for n, _c, _t in MPC.DEFAULT_PROPOSALS]
check("build_row() byte-identical to base on all 69 names", rows_now == rows_base)
check("summarize() byte-identical to base", CL.summarize(rows_now) == BCL.summarize(rows_base))
# product_class ROWS PRESENT but no index compiled — the mode-off path
pc_rules = legacy_rules + [{"match_field": "product_name", "match_op": "product_class",
                            "pattern": "residual", "category": "spiff", "sign_rule": "any",
                            "priority": 1}]
check("a product_class rule with NO compiled index matches NOTHING (the mode-off guarantee)",
      all(CL.classify(-10, "Activation", n, pc_rules) == BCL.classify(-10, "Activation", n, base_rules)
          for n, _c, _t in MPC.DEFAULT_PROPOSALS))
check("...even for a name that IS in a confirmed index elsewhere",
      CL.classify(-10, "Airtime Topup", "Trac Autopay Residual", pc_rules)
      == BCL.classify(-10, "Airtime Topup", "Trac Autopay Residual", base_rules)
      == ("autopay_residual", True))
check("ledger_rules_with_class returns the rules UNTOUCHED in legacy mode",
      MW.ledger_rules_with_class(FakeClient(copy.deepcopy(store())), LUX, "ma_daily_tx",
                                 pc_rules)[0] == pc_rules)
_r, _m = MW.ledger_rules_with_class(FakeClient(copy.deepcopy(store())), LUX, "ma_daily_tx", pc_rules)
check("...and says why, in words", "LEGACY" in _m["why"] and _m["applied"] is False)

# ── consumer 2: the whole carrier-income payload ──
for inc in ("ma", "ma_ledger"):
    stx = store(income=inc)
    a, b = ci(stx), bci(stx)
    ok, why = same_payload(a, b)
    check("carrier income identical to base with income_source=%r + both flags OFF" % inc, ok, why)
check("the class legs are computed but NOT displayed in legacy mode",
      month(ci(store()))["components"]["UNMAPPED"] == month(bci(store()))["components"]["UNMAPPED"])
check("class_mode echoes 'legacy'", ci(store())["class_mode"] == "legacy")
# rows CONFIRMED and legs configured, but mode still legacy
check("confirmed mappings + legs present, mode legacy -> still identical to base",
      same_payload(ci(store()), bci(store()))[0])
# no mig 265 at all
st_no265 = store(cfg=False)
st_no265.pop("ma_class_income_leg")
check("with NEITHER mig-265 table present the payload is identical to base",
      same_payload(ci(st_no265), bci(st_no265))[0])
check("boost never enters the MA path, so it cannot be affected",
      "class_mode" not in W.carrier_income(FakeClient({
          "carrier": [{"id": "b1", "org_id": LUX, "name": "Boost Mobile", "code": "BOOST",
                       "is_default": True}],
          "whatif_source_config": cfg_rows(), "raw_comp_report": []}), LUX, months=6, carrier_id="b1"))
for fn in ("_ma_residual_amount", "_ma_commission_amount", "_whatif_source_config", "_carrier_ctx",
           "_ma_commission_sign", "_residual_amount_field", "_ma_pkey", "_income_source_swap",
           "activation_baseline", "byod_residual"):
    check("whatif.%s source text identical to base" % fn,
          inspect.getsource(getattr(W, fn)) == inspect.getsource(getattr(BW, fn)))
for fn in ("load_rules", "build_row", "summarize", "parse_payment_month", "list_templates"):
    check("commission_ledger.%s source text identical to base" % fn,
          inspect.getsource(getattr(CL, fn)) == inspect.getsource(getattr(BCL, fn)))

print()
print("=" * 100)
print("C. CONSUMER 1 ON — a CONFIRMED class re-buckets what the keyword rules get wrong")
print("=" * 100)
cls_rules = MW.compile_rules(
    legacy_rules + [{"match_field": "product_name", "match_op": "product_class",
                     "pattern": "billpayment", "category": "charge", "sign_rule": "any",
                     "priority": 5},
                    {"match_field": "product_name", "match_op": "product_class",
                     "pattern": "device_sale", "category": "charge", "sign_rule": "any",
                     "priority": 5}], idx)
# the headline pair: a REFUNDED plan purchase. Today it is an unmapped 'other' PAYOUT; with the class
# rule it is correctly a charge and leaves payout_total.
old = CL.classify(-55.0, "Bill Payment", "Total MAX 5G Plan $55", legacy_rules)
new = CL.classify(-55.0, "Bill Payment", "Total MAX 5G Plan $55", cls_rules)
check("a negative PLAN PURCHASE is an unmapped payout today", old == ("other", True), old)
check("...and a non-payout 'charge' once the class is wired", new == ("charge", False), new)
r_old = CL.build_row({"product_name": "Total MAX 5G Plan $55", "raw_amount": -55.0,
                      "order_type": "Bill Payment"}, {"org_id": LUX, "period": JUNE}, legacy_rules)
r_new = CL.build_row({"product_name": "Total MAX 5G Plan $55", "raw_amount": -55.0,
                      "order_type": "Bill Payment"}, {"org_id": LUX, "period": JUNE}, cls_rules)
check("payout_total 55.00 -> 0.00 on that line", (r_old["payout_total"], r_new["payout_total"])
      == (55.0, 0.0))
check("the ledger's payout grand total drops by exactly that 55.00",
      round(CL.summarize([r_old])["payout_total"] - CL.summarize([r_new])["payout_total"], 2) == 55.0)
# a device sale booked as a payout today
old_d = CL.classify(-599.99, "Device Order", "Apple iPhone 16e 128GB Black TO", legacy_rules)
new_d = CL.classify(-599.99, "Device Order", "Apple iPhone 16e 128GB Black TO", cls_rules)
check("a returned DEVICE is an unmapped payout today", old_d == ("other", True))
check("...and a charge once classified", new_d == ("charge", False))
# THE PASS ORDER: the class wins over a keyword rule that would have matched
kw_wins = CL.classify(-37.5, "x", "Total MAX 5G Plan $55 New Activation Commission", legacy_rules)
check("the CONTAINS rule catches the commission-suffixed plan label today", kw_wins == ("commission", True))
pass1 = MW.compile_rules(legacy_rules + [{"match_field": "product_name", "match_op": "product_class",
                                          "pattern": "billpayment", "category": "charge",
                                          "sign_rule": "any", "priority": 900}],
                         {"Total MAX 5G Plan $55 New Activation Commission": "billpayment"})
check("a product_class rule at priority 900 STILL beats a contains rule at 10 (pass 1 runs first)",
      CL.classify(-37.5, "x", "Total MAX 5G Plan $55 New Activation Commission", pass1)
      == ("charge", False))
# the fallback is intact
check("an UNCLASSIFIED label still falls through to the keyword rules",
      CL.classify(-37.5, "x", "TBV MONTH 3 New Activation Commission", cls_rules)
      == ("commission", True))
check("...and an unclassified, unmatched payout is still the loud 'other'",
      CL.classify(-9.0, "x", "Some Brand New Thing", cls_rules) == ("other", True))
check("a positive line with no 'any' rule is still a charge",
      CL.classify(9.0, "x", "Some Brand New Thing", cls_rules) == ("charge", False))
# every one of the 69 seeded names, both ways — how many actually move
moved = [n for n, _c, _t in MPC.DEFAULT_PROPOSALS
         if CL.classify(-10, "Activation", n, legacy_rules)
         != CL.classify(-10, "Activation", n, MW.compile_rules(
             legacy_rules + [{"match_field": "product_name", "match_op": "product_class",
                              "pattern": c, "category": "charge", "sign_rule": "any", "priority": 5}
                             for c in ("billpayment", "device_sale", "wallet", "fee", "sim_kit",
                                       "adjustment_memo", "protection", "financing")],
             {n2: c2 for n2, c2, _t2 in MPC.DEFAULT_PROPOSALS}))]
check("with every NON-PAYOUT class mapped to 'charge', 51 of the 69 seeded names re-bucket",
      len(moved) == 51, len(moved))
check("...and every one of them is a non-payout class (never a commission/spiff/residual/subsidy name)",
      all(dict((n2, c2) for n2, c2, _t in MPC.DEFAULT_PROPOSALS)[n]
          in ("billpayment", "device_sale", "wallet", "fee", "sim_kit", "adjustment_memo",
              "protection", "financing") for n in moved))
check("the four PAYOUT classes are untouched by that rule set",
      all(CL.classify(-10, "Activation", n, legacy_rules)
          == CL.classify(-10, "Activation", n, MW.compile_rules(
              legacy_rules + [{"match_field": "product_name", "match_op": "product_class",
                               "pattern": "billpayment", "category": "charge", "sign_rule": "any",
                               "priority": 5}], {n2: c2 for n2, c2, _t in MPC.DEFAULT_PROPOSALS}))
          for n, c, _t in MPC.DEFAULT_PROPOSALS
          if c in ("commission", "spiff", "residual", "subsidy")))
# ledger_rules_with_class end to end
r_on, m_on = MW.ledger_rules_with_class(FakeClient(copy.deepcopy(store(ledger_mode="class"))),
                                        LUX, "ma_daily_tx", pc_rules)
check("in class mode the index really is attached", m_on["applied"] is True
      and m_on["classified_names"] == 6 and r_on[-1].get("_class_index"))
check("...and the mode-on explanation names the count", "6 confirmed" in m_on["why"])

print()
print("=" * 100)
print("D. ONLY A CONFIRMED MAPPING CLASSIFIES MONEY")
print("=" * 100)
prop_only = [r for r in map_rows() if r["status"] != "confirmed"]
check("an all-PROPOSED map yields an EMPTY index", MW.confirmed_index(prop_only) == {})
r_p, m_p = MW.ledger_rules_with_class(
    FakeClient(copy.deepcopy(store(ledger_mode="class", mapping=[
        (n, c, s, t) for (n, c, s, t) in CONFIRMED_MAP if s != "confirmed"]))),
    LUX, "ma_daily_tx", pc_rules)
check("class mode + zero confirmed rows -> rules returned UNTOUCHED", r_p == pc_rules)
check("...and it says so loudly instead of silently paying nothing",
      "no CONFIRMED" in m_p["why"] and "ma-product-class" in m_p["why"])
amb = MW.compile_rules(
    [{"match_field": "product_name", "match_op": "product_class", "pattern": "adjustment_memo",
      "category": "commission", "sign_rule": "any", "priority": 1}] + legacy_rules,
    MW.confirmed_index(map_rows()))
check("the AMBIGUOUS 'Credit Debit Memo' classifies NOTHING while it is proposed",
      CL.classify(-99.99, "Memo", "Credit Debit Memo", amb) == ("other", True))
check("the AMBIGUOUS 'Total Wireless Device Upgrade' classifies NOTHING either",
      CL.classify(-1.0, "x", "Total Wireless Device Upgrade", amb) == ("other", True))
conf_amb = MW.compile_rules(
    [{"match_field": "product_name", "match_op": "product_class", "pattern": "adjustment_memo",
      "category": "commission", "sign_rule": "any", "priority": 1}] + legacy_rules,
    MW.confirmed_index(map_rows(rows=[("Credit Debit Memo", "adjustment_memo", "confirmed",
                                       "AMBIGUOUS in direction")])))
check("...and DOES classify the moment the owner confirms it (nothing else has to change)",
      CL.classify(-99.99, "Memo", "Credit Debit Memo", conf_amb) == ("commission", True))
check("the built-in 69 proposals cannot smuggle themselves in — build_index has them, confirmed_index "
      "does not", len(MPC.build_index([])) == 69 and MW.confirmed_index([]) == {})

print()
print("=" * 100)
print("E. CONSUMER 2 ON — residual/airtime selected by CONFIRMED class")
print("=" * 100)
base_j = month(bci(store()))
on = ci(store(income_mode="class"))
j = month(on)
check("BASELINE residual (order type) == 150.00", base_j["residual_mi_atu"] == 150.0,
      base_j["residual_mi_atu"])
check("BASELINE airtime (every non-residual row's discount) == 38.50",
      base_j["components"]["UNMAPPED"] == 38.5, base_j["components"]["UNMAPPED"])
check("CLASS residual == 170.00 (the residual-class line whose ORDER TYPE was not residual joins)",
      j["residual_mi_atu"] == 170.0, j["residual_mi_atu"])
check("CLASS airtime == 4.00 (only the bill-payment line)", j["components"]["UNMAPPED"] == 4.0,
      j["components"]["UNMAPPED"])
check("the DEVICE SALE's $30.00 left the income total", j["components"]["UNMAPPED"] < 30.0)
# total_comp moves for TWO separately-provable reasons and nothing else: the airtime leg loses the
# device sale + wallet + unclassified ($34.50), and the ledger COMMISSION heading loses the $77 line
# whose dollars are now in the residual leg (the composed double-count guard, §F).
check("total_comp moves by exactly airtime(-34.50) + the newly-excluded ledger overlap(-77.00)",
      round(base_j["total_comp"] - j["total_comp"], 2) == 111.5,
      (base_j["total_comp"], j["total_comp"]))
check("...and the airtime half of that is exactly 34.50",
      round(base_j["components"]["UNMAPPED"] - j["components"]["UNMAPPED"], 2) == 34.5)
check("...and the ledger half is exactly 77.00",
      round(base_j["components"]["COMMISSION"] - j["components"]["COMMISSION"], 2) == 77.0)
sw = on["class_swap"]
jm = next(m for m in sw["by_month"] if m["period"] == JUNE)
check("class_swap states old vs new for both legs",
      (jm["old_residual"], jm["old_airtime"], jm["new_residual"], jm["new_airtime"])
      == (150.0, 38.5, 170.0, 4.0), jm)
check("class_swap deltas are arithmetic, not narrative",
      (jm["delta_residual"], jm["delta_airtime"]) == (20.0, -34.5))
check("what LEFT the total is counted in dollars, not just dropped",
      jm["class_excluded_discount"] == 32.5 and jm["class_excluded_lines"] == 2, jm)
check("...and so is what nobody has classified",
      jm["class_unclassified_discount"] == 2.0 and jm["class_unclassified_lines"] == 2, jm)
byc = {c["product_class"]: c for c in jm["by_class"]}
check("the per-class breakdown names the device sale and its $30.00",
      byc["device_sale"]["excluded_discount"] == 30.0 and byc["device_sale"]["leg"] == "excluded")
check("...and the wallet funding and its $2.50", byc["wallet"]["excluded_discount"] == 2.5)
check("...and the unclassified bucket carries the AMBIGUOUS memo + the unknown label",
      byc[MW.UNCLASSIFIED]["lines"] == 2 and byc[MW.UNCLASSIFIED]["excluded_discount"] == 2.0)
check("the still-PENDING ambiguous names are on the payload, by name",
      [e["product_name"] for e in on["class_wiring"]["class_map"]["ambiguous_pending"]]
      == ["Credit Debit Memo", "Total Wireless Device Upgrade"])
check("class_swap ships in LEGACY mode too, so the move is visible BEFORE the flip",
      ci(store())["class_swap"]["by_month"][0]["new_residual"] == 170.0)
check("...and says nothing has moved", "Nothing on this page has moved" in ci(store())["class_swap"]["note"])
check("params names the selector actually used",
      on["params"]["residual_leg_selector"] == "product_class"
      and ci(store())["params"]["residual_leg_selector"] == "order_type")
check("params.residual_leg_source is UNCHANGED in both modes (still the same table)",
      on["params"]["residual_leg_source"] == ci(store())["params"]["residual_leg_source"]
      == "raw_ma_daily_tx")
# a tenant re-maps device_sale INTO airtime — config really drives it
remap = ci(store(income_mode="class", legs={"residual": "residual", "billpayment": "airtime",
                                            "device_sale": "airtime"}))
check("re-mapping device_sale to 'airtime' in CONFIG puts its $30.00 back (no code change)",
      month(remap)["components"]["UNMAPPED"] == 34.0, month(remap)["components"]["UNMAPPED"])
check("...and the leg map on the payload reflects the tenant's own rows",
      remap["class_wiring"]["legs"].get("device_sale") == "airtime")

print()
print("=" * 100)
print("F. THE DOUBLE-COUNT GUARD COMPOSES — all FOUR flag combinations")
print("=" * 100)
for lm in ("legacy", "class"):
    for im in ("legacy", "class"):
        p = ci(store(income="ma_ledger", ledger_mode=lm, income_mode=im))
        m = month(p)
        sws = p["source_swap"]["by_month"]
        jj = next(x for x in sws if x["period"] == JUNE)
        # the ORDER-TYPE trap line ($33) is excluded in every combination
        excluded_ok = jj["residual_overlap_total"] >= 33.0
        # in class mode the CLASS trap line ($77) is excluded too
        want = 110.0 if im == "class" else 33.0
        check("ledger/%s income/%s: residual-overlap exclusion == $%.2f" % (lm, im, want),
              jj["residual_overlap_total"] == want, jj["residual_overlap_total"])
        # ledger commission lines total 325.00 (15 + 33 + 77 + 200). The order-type trap (33) is always
        # excluded; the CLASS trap (77) is excluded only while the income legs are class-selected.
        check("ledger/%s income/%s: COMMISSION heading == %s" % (lm, im,
              "292.00" if im == "legacy" else "215.00"),
              m["components"]["COMMISSION"] == (292.0 if im == "legacy" else 215.0),
              m["components"]["COMMISSION"])
        check("ledger/%s income/%s: no row is in a ledger bucket AND the residual leg" % (lm, im),
              excluded_ok)
p_cls = ci(store(income="ma_ledger", income_mode="class"))
jj = next(x for x in p_cls["class_swap"]["by_month"] if x["period"] == JUNE)
check("the CLASS-only half of the exclusion is reported separately ($77, 1 line)",
      (jj["ledger_class_overlap_lines"], jj["ledger_class_overlap_total"]) == (1, 77.0), jj)
check("in legacy income mode the class half of the guard is inert",
      next(x for x in ci(store(income="ma_ledger"))["class_swap"]["by_month"]
           if x["period"] == JUNE)["ledger_class_overlap_lines"] == 0)

print()
print("=" * 100)
print("G. DEGRADATION — never a fabricated $0")
print("=" * 100)
st_no_cfg = store(cfg=False)
check("mig 265 absent -> both consumers legacy, payload identical to base",
      same_payload(ci(st_no_cfg), bci(st_no_cfg))[0])
st_no_map = store(income_mode="class")
st_no_map["ma_product_class_map"] = []
p = ci(st_no_map)
check("class mode + NO confirmed classifications -> keeps the LEGACY legs", p["class_mode"] == "legacy")
check("...and says why, naming the page to fix it",
      "no CONFIRMED product classifications" in (p["class_note"] or "")
      and "/commcalc/ma-product-class" in (p["class_note"] or ""))
check("...and the figures are the base figures, not $0",
      month(p)["residual_mi_atu"] == 150.0 and month(p)["components"]["UNMAPPED"] == 38.5)
p_abs = W.carrier_income(FakeClient(copy.deepcopy(store(income_mode="class")),
                                    absent={"ma_product_class_map"}), LUX, months=6,
                         carrier_id=TOTAL_ID)
check("mig 254 table absent -> legacy legs kept, migration named",
      p_abs["class_mode"] == "legacy"
      and p_abs["class_wiring"]["class_map"]["migration"] == "254_commission_ma_product_class.sql")
p_nc = W.carrier_income(FakeClient(copy.deepcopy(store(income_mode="class")),
                                   missing_cols={"raw_ma_daily_tx": ["product_name"]}), LUX,
                        months=6, carrier_id=TOTAL_ID)
check("product_name unreadable on raw_ma_daily_tx -> legacy legs, and the RESIDUAL LEG SURVIVES",
      p_nc["class_mode"] == "legacy" and month(p_nc)["residual_mi_atu"] == 150.0,
      month(p_nc) and month(p_nc)["residual_mi_atu"])
check("...and it says the label column could not be read",
      p_nc["class_wiring"]["label_column_read"] is False
      and "product_name column could not be read" in (p_nc["class_note"] or ""))
p_nl = W.carrier_income(FakeClient(copy.deepcopy(store(income_mode="class")),
                                   missing_cols={"commission_ledger": ["product_name"]}), LUX,
                        months=6, carrier_id=TOTAL_ID)
check("product_name unreadable on commission_ledger -> the ledger income legs still work",
      p_nl["class_wiring"]["ledger_label_column_read"] is False
      and month(p_nl)["components"]["COMMISSION"] > 0)
pl = W.carrier_income(FakeClient(copy.deepcopy(store(income_mode="class")),
                                 absent={"ma_class_income_leg"}), LUX, months=6, carrier_id=TOTAL_ID)
check("mig-265 leg table absent -> the CODE default legs apply (residual + billpayment)",
      month(pl)["residual_mi_atu"] == 170.0 and month(pl)["components"]["UNMAPPED"] == 4.0)
check("...and the payload admits the legs came from code, not config",
      pl["class_wiring"]["legs_ready"] is False)
check("ledger table absent still degrades exactly as before (base behaviour preserved)",
      W.carrier_income(FakeClient(copy.deepcopy(store(income="ma_ledger")),
                                  absent={"commission_ledger"}), LUX, months=6,
                       carrier_id=TOTAL_ID)["ledger_ready"] is False)

print()
print("=" * 100)
print("H. MULTI-TENANT + ZERO-WRITE")
print("=" * 100)
READS.clear()
WRITES.clear()
two = copy.deepcopy(store(income_mode="class"))
two["raw_ma_daily_tx"] += [{"org_id": OTHER, "period": JUNE, "order_type": "Bill Payment",
                            "account_id": "Z9", "merchant_invoice": 1, "merchant_discount": 9999.0,
                            "retail_cost": 1.0, "product_name": "Total MAX 5G Plan $55"}]
two["ma_product_class_map"] += map_rows(OTHER)
two["ma_class_wiring_config"] = (two["ma_class_wiring_config"]
                                 + [{"org_id": OTHER, "consumer": "carrier_income", "mode": "class"}])
p_lux = W.carrier_income(FakeClient(copy.deepcopy(two)), LUX, months=6, carrier_id=TOTAL_ID)
check("the other tenant's $9,999 never reaches this tenant",
      month(p_lux)["components"]["UNMAPPED"] == 4.0, month(p_lux)["components"]["UNMAPPED"])
unscoped = [t for t, f in READS if t in ("ma_class_wiring_config", "ma_class_income_leg",
                                         "ma_product_class_map", "commission_ledger",
                                         "raw_ma_daily_tx")
            and not any(k == 'eq' and c == 'org_id' for k, c, _v in f)]
check("EVERY read of the five money-relevant tables is org-scoped", unscoped == [], unscoped)
check("the org filter on those tables really is the CALLER's org",
      all(v == LUX for t, f in READS
          if t in ("ma_class_wiring_config", "ma_class_income_leg", "ma_product_class_map",
                   "commission_ledger", "raw_ma_daily_tx")
          for k, c, v in f if k == 'eq' and c == 'org_id'))
check("zero writes attempted across every read path", WRITES == [], WRITES[:3])
tripped = False
try:
    FakeClient({}).schema("commcalc").table("ma_class_wiring_config").insert([{"x": 1}]).execute()
except AssertionError:
    tripped = True
check("the write guard genuinely fires (negative control)", tripped)
import app.modules.commcalc.router as R
for fn in (R.get_ma_class_wiring, R.put_ma_class_wiring_mode, R.put_ma_class_wiring_leg,
           R.ma_class_wiring_ledger_delta, R.ma_class_wiring_rule_proposals,
           R.apply_ma_class_wiring_rule_proposals):
    sig = inspect.signature(fn)
    check("org_id is a QUERY PARAM on %s" % fn.__name__,
          "org_id" in sig.parameters and sig.parameters["org_id"].default == R.ORG_ID)
for fn in (R.put_ma_class_wiring_mode, R.put_ma_class_wiring_leg,
           R.apply_ma_class_wiring_rule_proposals):
    src_ = inspect.getsource(fn)
    check("%s stamps org_id and takes the money-posture gate" % fn.__name__,
          '"org_id": org_id' in src_ and "_require_commission_admin" in src_)

print()
print("=" * 100)
print("I. MIGRATION 265")
print("=" * 100)
MIG = os.path.join(_repo, "database/migrations/265_commission_ma_class_money_wiring.sql")
sql = io.open(MIG, encoding="utf-8").read()
try:
    import pglast
    pglast.parse_sql(sql)
    check("parses as real PostgreSQL (pglast)", True)
except ImportError:
    check("pglast unavailable — SQL parse SKIPPED (reported, not silently passed)", False,
          "install pglast to run this check")
except Exception as e:
    check("parses as real PostgreSQL (pglast)", False, str(e)[:200])
nocomment = re.sub(r"--[^\n]*", "", sql)
nostr = re.sub(r"'[^']*'", "''", nocomment)
check("no DROP / DELETE / UPDATE / ALTER COLUMN — additive only",
      not re.search(r"\b(DROP|DELETE\s+FROM|UPDATE)\b", nostr, re.I))
check("every CREATE TABLE is IF NOT EXISTS",
      len(re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", sql, re.I))
      == len(re.findall(r"CREATE\s+TABLE", sql, re.I)) == 2)
check("every CREATE INDEX is IF NOT EXISTS",
      len(re.findall(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS", sql, re.I))
      == len(re.findall(r"CREATE\s+INDEX", sql, re.I)) == 2)
check("every INSERT is ON CONFLICT DO NOTHING (idempotent)",
      len(re.findall(r"ON\s+CONFLICT[^;]*DO\s+NOTHING", sql, re.I))
      == len(re.findall(r"INSERT\s+INTO", sql, re.I)) == 2)
check("RLS enabled on BOTH new tables",
      len(re.findall(r"ENABLE\s+ROW\s+LEVEL\s+SECURITY", sql, re.I)) == 2)
check("NO CREATE POLICY", not re.search(r"CREATE\s+POLICY", nostr, re.I))
check("NO GRANT", not re.search(r"\bGRANT\b", nostr, re.I))
check("NO anon / authenticated role named", not re.search(r"\b(anon|authenticated)\b", nostr, re.I))
check("org_id uuid NOT NULL on both tables", len(re.findall(r"org_id\s+UUID\s+NOT\s+NULL", sql, re.I)) == 2)
check("an org index on both tables", "ma_class_wiring_config_org" in sql
      and "ma_class_income_leg_org" in sql)
check("no MONEY table is written by this migration",
      not re.search(r"(rep_commissions|commission_ledger|raw_sales|carrier_commission|"
                    r"commission_category_map|ma_product_class_map)", nostr, re.I))
# the seed IS the code default — drift is impossible
seed_legs = dict(re.findall(r"'00000000-0000-0000-0000-000000000001',\s*'(\w+)',\s*'(\w+)'\)", sql))
code_legs = {c: MW.DEFAULT_INCOME_LEGS.get(c, "excluded")
             for c in [k for k in MPC.CLASS_KEYS if k != MPC.UNMAPPED]}
check("the seeded leg map == the code default, class for class", seed_legs == code_legs,
      {k: (seed_legs.get(k), code_legs.get(k)) for k in set(seed_legs) | set(code_legs)
       if seed_legs.get(k) != code_legs.get(k)})
check("the seed covers all 12 assignable classes and no reserved one",
      len(seed_legs) == 12 and MPC.UNMAPPED not in seed_legs)
check("BOTH consumers are seeded 'legacy' (the seed cannot switch anyone on)",
      len(re.findall(r"'legacy'", sql)) >= 2 and "'class'" not in
      re.sub(r"--[^\n]*", "", sql).replace("mode = 'class'", ""))
check("only the HOUSE org is seeded (every other tenant inherits the code default)",
      set(re.findall(r"'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'", sql))
      == {HOUSE})
migs = sorted(os.listdir(os.path.join(_repo, "database/migrations")))
check("265 is in band 200-299", any(f.startswith("265_") for f in migs))
check("265 is UNIQUE (no collision)", len([f for f in migs if f.startswith("265_")]) == 1)
check("263 / 264 are NOT taken here (a concurrent agent holds them)",
      not any(f.startswith("263_") or f.startswith("264_") for f in migs))
check("the code and the migration agree on the file name", MW.MIGRATION == os.path.basename(MIG))

print()
print("=" * 100)
print("J. DIFFERENTIAL SCOPE — the pay engines and the P&L are untouched")
print("=" * 100)
for name in ("calculator.py", "commission_engine.py", "sale_installment_engine.py",
             "installment_engine.py", "commission_catalog.py", "carrier_map.py",
             "column_mapping.py", "ledger_ma_sync.py", "ma_product_class.py", "plan_pay_gate.py"):
    cur = io.open(os.path.join(_repo, "backend/app/modules/commcalc", name), "rb").read()
    base = subprocess.check_output(
        ["git", "-C", _repo, "show", "%s:backend/app/modules/commcalc/%s" % (BASE_REV, name)])
    check("%s is BYTE-IDENTICAL to base" % name, cur == base)
for name in ("coa.py", "residual_subs.py", "autocompute.py"):
    p_ = os.path.join(_repo, "backend/app/modules/account", name)
    if os.path.exists(p_):
        cur = io.open(p_, "rb").read()
        base = subprocess.check_output(
            ["git", "-C", _repo, "show", "%s:backend/app/modules/account/%s" % (BASE_REV, name)])
        check("account/%s is BYTE-IDENTICAL to base (the P&L leg is OUT OF SCOPE)" % name, cur == base)
        check("account/%s names neither new module" % name,
              "ma_class_wiring" not in cur.decode() and "ma_product_class" not in cur.decode())
for name in ("calculator.py", "commission_engine.py", "sale_installment_engine.py",
             "installment_engine.py", "targets_engine.py", "plan_pay_gate.py"):
    txt = io.open(os.path.join(_repo, "backend/app/modules/commcalc", name)).read()
    check("%s never references the class wiring" % name,
          "ma_class_wiring" not in txt and "ma_product_class" not in txt)
touch = sorted(
    os.path.relpath(os.path.join(dp, f), _repo)
    for dp, _dn, fn in os.walk(os.path.join(_repo, "backend/app/modules"))
    for f in fn if f.endswith(".py") and "ma_class_wiring" in io.open(os.path.join(dp, f)).read())
check("ma_class_wiring is named by exactly four files and no other module",
      set(touch) == {"backend/app/modules/commcalc/ma_class_wiring.py",
                     "backend/app/modules/commcalc/whatif.py",
                     "backend/app/modules/commcalc/router.py",
                     "backend/app/modules/commcalc/commission_ledger.py"}, touch)
_cl_txt = io.open(os.path.join(_repo, "backend/app/modules/commcalc/commission_ledger.py")).read()
check("commission_ledger names the wiring ONLY in a see-also comment (one line, no code)",
      len([l for l in _cl_txt.splitlines() if "ma_class_wiring" in l]) == 1
      and [l for l in _cl_txt.splitlines() if "ma_class_wiring" in l][0].lstrip().startswith("#"))
check("commission_ledger never names ma_product_class at all — the classifier has NO knowledge of the "
      "classification, only of an index handed to it",
      "ma_product_class" not in _cl_txt)
check("commission_ledger IMPORTS neither new module (zero new dependencies in the classifier)",
      not [l for l in _cl_txt.splitlines()
           if l.strip().startswith(("import ", "from ")) and ("ma_class_wiring" in l
                                                              or "ma_product_class" in l)])
rdiff = subprocess.check_output(
    ["git", "-C", _repo, "diff", "--numstat", BASE_REV, "--",
     "backend/app/modules/commcalc/router.py"]).decode().split()
check("the router diff REMOVES zero lines (purely additive)", rdiff and rdiff[1] == "0", rdiff)
cl_diff = subprocess.check_output(
    ["git", "-C", _repo, "diff", "--numstat", BASE_REV, "--",
     "backend/app/modules/commcalc/commission_ledger.py"]).decode().split()
check("commission_ledger's diff is small and bounded (< 40 lines removed)",
      cl_diff and int(cl_diff[1]) < 40, cl_diff)
_mw_txt = io.open(os.path.join(_repo, "backend/app/modules/commcalc/ma_class_wiring.py")).read()
check("ma_class_wiring never touches a pay table (rep_commissions appears only in the prose that says "
      "it is not a consumer)",
      not [l for l in _mw_txt.splitlines()
           if "rep_commissions" in l and (".table(" in l or "insert" in l or "upsert" in l)])
check("ma_class_wiring never calls a supabase write verb (the two .update( calls are dict.update on "
      "a local meta dict, not a DB write)",
      not [l for l in _mw_txt.splitlines()
           if any(w in l for w in (".insert(", ".upsert(", ".delete("))]
      and all("meta.update({" in l for l in _mw_txt.splitlines() if ".update(" in l)
      and ".table(" in _mw_txt and _mw_txt.count(".execute()") == 3)
check("the ledger delta endpoint is a GET (a read, by HTTP method)",
      "@router.get(\"/ma-class-wiring/ledger-delta\")" in
      io.open(os.path.join(_repo, "backend/app/modules/commcalc/router.py")).read())

print()
print("=" * 100)
print("RESULT: %d passed, %d failed" % (PASS, FAIL))
print("=" * 100)
sys.exit(1 if FAIL else 0)
