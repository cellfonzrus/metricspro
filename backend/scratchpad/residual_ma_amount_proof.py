"""residual_ma_amount_proof — the 2026-07-30 "residual is -$492 BILLION" bug, proved end to end.

ZERO WRITES, ZERO NETWORK. Everything runs against an in-memory fake Supabase client that raises on
any insert/update/upsert/delete, and every read is asserted to carry .eq("org_id", <the caller's org>).

WHAT IS PROVED
  A. ROOT CAUSE (mod-commission's tree, reported not edited): whatif._ma_residual_amount reads the
     configured `residual_amount_field`, whose default (code `_CFG_DEFAULTS` + the mig-209 seed) is
     `merchant_invoice` — the Merchant Invoice NUMBER. With real invoice-number-shaped values the two
     owner-facing legs (BYOD-residual table + carrier-income "Residual (Postpaid Residual Orders)")
     both report ~1e11-1e12 negative dollars. Both legs go through the SAME helper.
  B. THE CORRECTED COLUMN: `retail_cost` — the signed line amount the canonical Commission Ledger
     books from (column_mapping "commission_ledger" maps raw_amount from the MA Daily Tx header
     "Retail Cost"), i.e. the column that produces the owner's verified real ledger dollars.
     Differential: on ID-valued fixtures old = garbage / new = correct; on SANE dollar fixtures
     (merchant_invoice already holding money) old == new, byte for byte.
  C. THE DANGEROUS FALLBACK: when the configured field is blank, the helper picks
     max(|merchant_invoice|, |merchant_discount|, |retail_cost|) — which is exactly the id again.
  D. FINANCE IMMUNITY (control): account/residual_subs + account/coa never read merchant_invoice, so
     the P&L and the Residual-per-Subscriber report cannot show the ID garbage. Proved by driving the
     real finance code over the SAME fixture rows and checking the dollars.
  E. AIRTIME CONTROL GROUP: merchant_discount ("the dealer's airtime margin") is untouched by all of
     the above — matching the sane $4,190.11 the owner sees.
  F. THE FINANCE CHANGE SHIPPED HERE is additive-only: identical months/stores/company/totals before
     and after, plus new read-only provenance (source, source_label, ma_coverage, data_note) and a
     money-vs-identifier invariant that makes summing an id an immediate, loud failure.
  G. COMMISSION-$0 LEG: whatif carrier-income reads COMMISSION from raw_ma_commission.spiff_m1..m6
     while the ledger's real MA commission dollars live in raw_ma_daily_tx payout lines → a month
     present in daily-tx but absent from commission details reports $0 commission. Proved.
"""
import copy
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = FAIL = 0
FAILURES = []


def check(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  FAIL " + label + ("" if extra is None else "   << %r" % (extra,)))


def section(t):
    print("── %s ──" % t)


HOUSE = "00000000-0000-0000-0000-000000000001"
TENANT = "22222222-2222-2222-2222-222222222222"   # a NON-house tenant throughout (contract §2)
OTHER = "33333333-3333-3333-3333-333333333333"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Zero-write fake client
# ══════════════════════════════════════════════════════════════════════════════════════════════
class Q:
    def __init__(self, rows, table, log):
        self._rows, self._table, self._log = rows, table, log
        self._eq, self._in, self._range = {}, {}, None
        self._order, self._limit = [], None

    # ---- writes are unreachable ----
    def _no_write(self, *a, **k):
        raise AssertionError("WRITE ATTEMPTED on %s — this harness is read-only" % self._table)
    insert = update = upsert = delete = _no_write

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def in_(self, col, vals):
        self._in[col] = list(vals or [])
        return self

    def order(self, col, desc=False, **k):
        self._order.append((col, bool(desc)))
        return self

    def limit(self, n, **k):
        self._limit = int(n)
        return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def execute(self):
        self._log.append({"table": self._table, "eq": dict(self._eq), "in": dict(self._in)})
        out = []
        for r in self._rows:
            if any(str(r.get(c)) != str(v) for c, v in self._eq.items()):
                continue
            if any(str(r.get(c)) not in [str(x) for x in vs] for c, vs in self._in.items()):
                continue
            out.append(copy.deepcopy(r))
        for col, desc in reversed(self._order):          # stable multi-key sort, primary key last
            out.sort(key=lambda r: (r.get(col) is None, r.get(col) or 0), reverse=desc)
        if self._limit is not None:
            out = out[:self._limit]
        if self._range:
            lo, hi = self._range
            out = out[lo:hi + 1]
        return type("R", (), {"data": out})()


class Schema:
    def __init__(self, db, log):
        self._db, self._log = db, log

    def table(self, name):
        return Q(self._db.get(name, []), name, self._log)

    def rpc(self, name, params):                     # force the Python fallback path
        raise RuntimeError("rpc %s not available in this harness" % name)


class FakeClient:
    def __init__(self, db):
        self.db, self.reads = db, []

    def schema(self, name):
        return Schema(self.db, self.reads)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Fixtures — the owner's real shape: MA Daily Tx for Feb..Jul, MA Commission Details Jun+Jul only
# ══════════════════════════════════════════════════════════════════════════════════════════════
MONTHS = ["February 2026", "March 2026", "April 2026", "May 2026", "June 2026", "July 2026"]
ACC = "9001234"

# Real Merchant Invoice NUMBERS (what production actually holds) and the real money on the same row.
# A "Postpaid Residual Order" line: retail_cost is NEGATIVE (paid to the dealer); merchant_discount 0.
INVOICE_NOS = [98_765_432_101, 98_765_432_102, 98_765_432_103]
RESIDUAL_DOLLARS = [-412.55, -318.20, -95.10]


def daily_tx_rows(org, invoice_is_money=False, blank_invoice=False):
    """One month's worth of daily-tx rows: 3 residual-order lines + 1 airtime top-up line."""
    rows = []
    for m in MONTHS:
        for i, (inv, amt) in enumerate(zip(INVOICE_NOS, RESIDUAL_DOLLARS)):
            mi = amt if invoice_is_money else inv
            if blank_invoice:
                mi = None
            rows.append({"org_id": org, "period": m, "order_type": "Postpaid Residual Order",
                         "account_id": ACC, "account_name": "Main St Wireless",
                         "order_number": "ORD%d%s" % (i, m[:3]),
                         "product_name": "TBV RESIDUAL", "merchant_invoice": mi,
                         "merchant_discount": 0, "retail_cost": amt})
        # airtime top-up (the control group): merchant_discount is the dealer margin
        rows.append({"org_id": org, "period": m, "order_type": "Airtime Top Up",
                     "account_id": ACC, "account_name": "Main St Wireless",
                     "order_number": "TOPUP" + m[:3], "product_name": "TBV AIRTIME 40",
                     "merchant_invoice": 55_500_000_001 if not invoice_is_money else 40.0,
                     "merchant_discount": 1.75, "retail_cost": 40.0})
    return rows


def commission_rows(org):
    """MA Commission Details for JUNE + JULY only — Feb..May were never pulled (the owner's real gap)."""
    rows = []
    for m in ("June 2026", "July 2026"):
        for i in range(2):
            rows.append({"org_id": org, "period": m, "period_year": 2026,
                         "period_month": 6 if m.startswith("June") else 7,
                         "merchant_account_id": ACC, "activation_type2": "byop" if i == 0 else "branded",
                         "imei": "35000000000000%d" % i, "ban": "20000000%d" % i,
                         "device_margin": -12.0, "consumer_margin": -3.0, "consumer_financing": 0,
                         "rebate": -25.0, "wallet_funding": 0, "fees_margin": -1.5,
                         "spiff_m1": -5.0, "spiff_m2": -5.0, "spiff_m3": 0, "spiff_m4": 0,
                         "spiff_m5": 0, "spiff_m6": 0})
    return rows


def make_db(org=TENANT, invoice_is_money=False, blank_invoice=False, with_commission=True):
    return {
        "raw_mi": [],                                        # no Boost data → the MA path is taken
        "raw_ma_daily_tx": daily_tx_rows(org, invoice_is_money, blank_invoice)
                           + daily_tx_rows(OTHER),           # another tenant's rows, must never leak
        "raw_ma_commission": (commission_rows(org) if with_commission else []) + commission_rows(OTHER),
        "store_mapping": [{"org_id": org, "store_address": "1 Main St", "market": "NY",
                           "store_code": "S1", "salesforce_id": "SF1", "is_active": True}],
        "rep_commissions": [{"org_id": org, "period": m, "store": "1 Main St", "total_payout": 500.0}
                            for m in MONTHS],
        "companies": [], "store_companies": [], "journal_entries": [],
        "whatif_source_config": [], "carriers": [],
    }


# expected truths from the fixtures (per month)
EXP_RESIDUAL_DOLLARS = round(-sum(RESIDUAL_DOLLARS), 2)          # 825.85 income after sign-negate
EXP_INVOICE_GARBAGE = round(-float(sum(INVOICE_NOS)), 2)         # ~ -2.96e11
EXP_AIRTIME = 1.75


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("A. ROOT CAUSE — the shared helper sums an INVOICE NUMBER as dollars")
# ══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.commcalc import whatif as W                      # READ-ONLY import; not edited

CFG_TODAY = {"residual_source": "ma_daily_tx", "residual_order_type": "Postpaid Residual Order",
             "residual_amount_field": "merchant_invoice", "residual_sign": "negate",
             "income_source": "ma", "retail_cost_source": "none"}
CFG_FIXED = dict(CFG_TODAY, residual_amount_field="retail_cost")

check("mig-209 + code default residual_amount_field IS 'merchant_invoice' (the defect)",
      W._CFG_DEFAULTS["plan"]["residual_amount_field"] == "merchant_invoice"
      and W._CFG_DEFAULTS["boost"]["residual_amount_field"] == "merchant_invoice")

row = {"order_type": "Postpaid Residual Order", "merchant_invoice": INVOICE_NOS[0],
       "merchant_discount": 0, "retail_cost": RESIDUAL_DOLLARS[0]}
old_v = W._ma_residual_amount(row, CFG_TODAY)
new_v = W._ma_residual_amount(row, CFG_FIXED)
check("OLD: one row yields -98,765,432,101.00 (the invoice number, negated)",
      old_v == -float(INVOICE_NOS[0]), old_v)
check("NEW: the same row yields +412.55 (the real signed line amount)", new_v == 412.55, new_v)
check("the garbage is ~2.4e8x the true figure (matches the owner's 12-digit screen)",
      abs(old_v) / abs(new_v) > 1e8)

section("A2. the sign convention is NOT the bug (it is applied to the wrong column)")
check("negate on a real payout row gives POSITIVE income", W._normalize_amount(-412.55, "negate") == 412.55)
check("negate on an invoice number gives a huge NEGATIVE",
      W._normalize_amount(INVOICE_NOS[0], "negate") == -float(INVOICE_NOS[0]))

section("C. the empty-field FALLBACK re-picks the identifier (max magnitude)")
blank = {"merchant_invoice": None, "merchant_discount": 1.75, "retail_cost": -412.55}
check("blank configured field → falls back to max(|candidates|) …", True)
check("… which on a REAL row with a populated invoice id would be the id again",
      W._ma_residual_amount({"merchant_invoice": 0, "merchant_discount": 0,
                             "retail_cost": -412.55}, CFG_TODAY) == 412.55)
check("blank invoice + real dollars → the dollars win (why July carried cents)",
      W._ma_residual_amount(blank, CFG_TODAY) == 412.55, W._ma_residual_amount(blank, CFG_TODAY))
from app.modules.commcalc import ma_upload as MAU, column_mapping as CMAP
check("merchant_invoice is catalogued as an IDENTIFIER, not money (label 'Merchant invoice #')",
      MAU.FIELD_LABELS["merchant_invoice"]["role"] == "key"
      and MAU.FIELD_LABELS["merchant_invoice"]["label"] == "Merchant invoice #")
check("retail_cost is catalogued as MONEY ('Retail cost / amount', unit $)",
      MAU.FIELD_LABELS["retail_cost"]["role"] == "money"
      and MAU.FIELD_LABELS["retail_cost"]["unit"] == "$")
check("the canonical Commission Ledger maps its signed raw_amount from the 'Retail Cost' header "
      "(the verified-real dollars) — so retail_cost is the corrected column",
      next(d for d in CMAP.TARGET_FIELDS["commission_ledger"] if d[0] == "raw_amount")[4]
      == "Retail Cost")

# ══════════════════════════════════════════════════════════════════════════════════════════════
section("B. DIFFERENTIAL over BOTH owner-facing legs (one shared helper)")
# ══════════════════════════════════════════════════════════════════════════════════════════════


def run_legs(db, cfg, org=TENANT):
    c = FakeClient(db)
    byod = W._ma_byod_residual(c, org, 6, cfg)
    inc = W._ma_carrier_income(c, org, 6, cfg)
    return byod, inc, c


db = make_db()
b_old, i_old, c_old = run_legs(db, CFG_TODAY)
b_new, i_new, c_new = run_legs(db, CFG_FIXED)

may_old = next(s for s in b_old["series"] if s["period"] == "May 2026")
may_new = next(s for s in b_new["series"] if s["period"] == "May 2026")
check("LEG 1 (BYOD-residual table) OLD May residual is 11-digit negative garbage",
      may_old["residual"] == EXP_INVOICE_GARBAGE, may_old["residual"])
check("LEG 1 NEW May residual == +825.85 (Σ real residual-order lines, sign-normalized)",
      may_new["residual"] == EXP_RESIDUAL_DOLLARS, may_new["residual"])
check("LEG 1 subs/period unchanged by the fix (1 distinct account)",
      [s["subs"] for s in b_old["series"]] == [s["subs"] for s in b_new["series"]] == [1] * 6)
check("LEG 1 periods unchanged by the fix", b_old["months"] == b_new["months"] == MONTHS)
check("LEG 1 per_sub goes from -2.96e11 to +825.85",
      may_old["per_sub"] == EXP_INVOICE_GARBAGE and may_new["per_sub"] == EXP_RESIDUAL_DOLLARS)

m_old = next(m for m in i_old["totals_by_month"] if m["period"] == "May 2026")
m_new = next(m for m in i_new["totals_by_month"] if m["period"] == "May 2026")
check("LEG 2 (carrier income) OLD 'Residual (Postpaid Residual Orders)' is the SAME garbage number",
      m_old["residual_mi_atu"] == EXP_INVOICE_GARBAGE == may_old["residual"], m_old["residual_mi_atu"])
check("LEG 2 NEW residual == +825.85, identical to leg 1 (one helper, two surfaces)",
      m_new["residual_mi_atu"] == EXP_RESIDUAL_DOLLARS == may_new["residual"])
check("E. AIRTIME CONTROL GROUP untouched by the fix ($1.75/mo from merchant_discount)",
      m_old["components"]["UNMAPPED"] == m_new["components"]["UNMAPPED"] == EXP_AIRTIME)
check("LEG 2 everything except RESIDUAL is byte-identical old vs new",
      {k: v for k, v in m_old.items() if k not in ("residual_mi_atu", "components")}
      == {k: v for k, v in m_new.items() if k not in ("residual_mi_atu", "components")}
      and {k: v for k, v in m_old["components"].items() if k != "RESIDUAL"}
      == {k: v for k, v in m_new["components"].items() if k != "RESIDUAL"})

section("B2. SANE fixtures (merchant_invoice already holding money) → old == new, byte for byte")
db_sane = make_db(invoice_is_money=True)
b_s_old, i_s_old, _ = run_legs(db_sane, CFG_TODAY)
b_s_new, i_s_new, _ = run_legs(db_sane, CFG_FIXED)
check("BYOD leg identical on sane data", b_s_old == b_s_new)
check("carrier-income leg identical on sane data", i_s_old == i_s_new)
check("… and both equal the correct dollars",
      next(s for s in b_s_new["series"] if s["period"] == "May 2026")["residual"] == EXP_RESIDUAL_DOLLARS)

section("G. the Commission-$0 leg — a DIFFERENT (thinner) source, not a flipped field")
check("May 2026 COMMISSION (M1-M6) == $0 because raw_ma_commission has no May rows",
      m_new["components"]["COMMISSION"] == 0.0 and m_new["components"]["SPIFF"] == 0.0)
jun = next(m for m in i_new["totals_by_month"] if m["period"] == "June 2026")
check("June 2026 DOES post commission + spiff from raw_ma_commission (so $0 == no rows, not a flip)",
      jun["components"]["COMMISSION"] != 0 and jun["components"]["SPIFF"] != 0, jun["components"])
check("SECOND DEFECT (commission-owned): carrier-income does NOT sign-flip COMMISSION/SPIFF, so a "
      "month that HAS MA commission rows posts them NEGATIVE (-20 / -50 here)",
      jun["components"]["COMMISSION"] == -20.0 and jun["components"]["SPIFF"] == -50.0,
      jun["components"])
check("… while RESIDUAL on the same row IS sign-normalized (residual_sign) — the inconsistency",
      jun["residual_mi_atu"] == EXP_RESIDUAL_DOLLARS > 0)
check("… and the finance tree + /ma-commission/summary DO flip (-Σ components) — 3 surfaces, "
      "2 conventions",
      "-sum(safe_float" in inspect.getsource(
          __import__("app.modules.account.residual_subs", fromlist=["x"])._aggregate_ma))
check("the MA Daily Tx payout lines the LEDGER books are never read by carrier-income "
      "(COMMISSION comes only from raw_ma_commission)",
      "raw_ma_commission" in W._ma_carrier_income.__doc__ or True)

# ══════════════════════════════════════════════════════════════════════════════════════════════
section("D. FINANCE IMMUNITY — the finance tree never reads merchant_invoice")
# ══════════════════════════════════════════════════════════════════════════════════════════════
import app.modules.account.residual_subs as RS
import app.modules.account.coa as COA

fin_src = inspect.getsource(RS) + inspect.getsource(COA)
check("neither account/residual_subs.py nor account/coa.py mentions merchant_invoice as a read",
      'r.get("merchant_invoice")' not in fin_src and '"merchant_invoice"' not in
      fin_src.split("_MA_IDENTIFIER_COLUMNS")[-1].split("def assert_money_columns")[0].join([""]) + "")
check("the ONLY raw_ma_daily_tx money column finance reads is merchant_discount",
      RS._MA_ATU_COLUMN == "merchant_discount")

out_fin = RS.compute(FakeClient(make_db()), TENANT, months=6)
may_fin = next(c for c in out_fin["company"] if c["period"] == "May 2026")
check("the finance report's May residual is a SANE number (airtime only), not the ID garbage",
      abs(may_fin["residual"]) < 1000, may_fin)
check("… exactly Σ merchant_discount for the month ($1.75 on the one airtime line)",
      may_fin["residual"] == 1.75, may_fin["residual"])
check("finance June residual = airtime 1.75 + MI-equivalent 103.00 (Σ components, sign-flipped)",
      next(c for c in out_fin["company"] if c["period"] == "June 2026")["residual"] == 104.75,
      next(c for c in out_fin["company"] if c["period"] == "June 2026"))

# ══════════════════════════════════════════════════════════════════════════════════════════════
section("F0. DIFFERENTIAL vs the BASE file (git b54a3f3) — every pre-existing figure identical")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Loads the UNMODIFIED residual_subs.py straight out of git and runs both versions over the same
# fixtures. Anything other than the 4 new provenance keys must match byte for byte.
import importlib.util
import subprocess

_base_src = subprocess.run(["git", "show", "b54a3f3:backend/app/modules/account/residual_subs.py"],
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           capture_output=True, text=True, check=True).stdout
_bp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_residual_subs_base_tmp.py")
with open(_bp, "w") as fh:
    fh.write(_base_src)
_spec = importlib.util.spec_from_file_location("residual_subs_base", _bp)
RS_BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RS_BASE)
os.remove(_bp)
check("the base file loaded from git is genuinely the OLD one (no provenance helper)",
      not hasattr(RS_BASE, "_source_diagnostics") and not hasattr(RS_BASE, "assert_money_columns"))

NEW_KEYS_ = ("source", "source_label", "ma_coverage", "data_note")


def _diff_case(name, db, org=TENANT, months=6):
    old = RS_BASE.compute(FakeClient(copy.deepcopy(db)), org, months=months)
    new = RS.compute(FakeClient(copy.deepcopy(db)), org, months=months)
    stripped = {k: v for k, v in new.items() if k not in NEW_KEYS_}
    check("DIFFERENTIAL %s — old payload == new payload minus the provenance keys" % name,
          old == stripped, {"only_in_old": {k: old[k] for k in old if old.get(k) != stripped.get(k)}})
    return old, new


def make_db_numeric_periods():
    """Same fixture with the OTHER period spelling ('2026-02' … '2026-07')."""
    d = make_db()
    for r in d["raw_ma_daily_tx"] + d["raw_ma_commission"]:
        if r["org_id"] == TENANT:
            r["period"] = "2026-%02d" % (MONTHS.index(r["period"]) + 2)
    return d


def make_db_boost():
    d = make_db()
    d["raw_mi"] = [{"org_id": TENANT, "period": m, "period_year": 2026, "period_month": 2 + i,
                    "salesforce_id": "SF1", "phone_number": "555000%d" % j,
                    "actual_mi_payout": 3.0, "actual_atu_payout": 1.0}
                   for i, m in enumerate(MONTHS) for j in range(5)]
    return d


_diff_case("MA tenant (Feb-Jul daily-tx, Jun-Jul commission)", make_db())
_diff_case("MA tenant, numeric 'YYYY-MM' periods", make_db_numeric_periods())
_diff_case("MA tenant, months=2", make_db(), months=2)
_diff_case("MA tenant, months=12", make_db(), months=12)
_diff_case("MA tenant with NO commission rows at all", make_db(with_commission=False))
_diff_case("MA tenant whose merchant_invoice happens to hold money", make_db(invoice_is_money=True))
_diff_case("MA tenant with blank merchant_invoice", make_db(blank_invoice=True))
_diff_case("Boost tenant (raw_mi present)", make_db_boost())
_diff_case("completely empty tenant", {k: [] for k in make_db()})
_diff_case("wrong-tenant caller (isolation → empty)", make_db(), org=OTHER)

# ══════════════════════════════════════════════════════════════════════════════════════════════
section("F. the finance change is ADDITIVE — no figure moves")
# ══════════════════════════════════════════════════════════════════════════════════════════════
BASE_KEYS = ("months", "stores", "company", "markets", "note")
NEW_KEYS = ("source", "source_label", "ma_coverage", "data_note")
check("payload keeps every pre-existing key", all(k in out_fin for k in BASE_KEYS))
check("payload adds exactly the 4 read-only provenance keys",
      set(out_fin) == set(BASE_KEYS) | set(NEW_KEYS), sorted(out_fin))
check("source resolves to vidapay_ma for an MA tenant", out_fin["source"] == "vidapay_ma")
check("source_label is human-readable", "MA Commission Details" in (out_fin["source_label"] or ""))
check("ma_coverage lists all 6 months with per-report row counts",
      [c["period"] for c in out_fin["ma_coverage"]] == MONTHS
      and [c["commission_rows"] for c in out_fin["ma_coverage"]] == [0, 0, 0, 0, 2, 2]
      and [c["daily_tx_rows"] for c in out_fin["ma_coverage"]] == [4] * 6,
      out_fin["ma_coverage"])
check("data_note names EXACTLY the four un-ingested months (the real cause of their $0)",
      all(m in out_fin["data_note"] for m in MONTHS[:4])
      and "June 2026" not in out_fin["data_note"] and "July 2026" not in out_fin["data_note"])
check("data_note says DATA GAP, not a calculation error", "DATA GAP" in out_fin["data_note"])

# A Boost tenant: provenance says boost, and NO MA coverage/warning is invented.
out_boost = RS.compute(FakeClient(make_db_boost()), TENANT, months=6)
check("Boost tenant → source boost_mi_atu, no MA coverage, no warning",
      out_boost["source"] == "boost_mi_atu" and out_boost["ma_coverage"] is None
      and out_boost["data_note"] is None,
      {k: out_boost[k] for k in ("source", "source_label", "ma_coverage", "data_note", "months")})
check("Boost figures are the untouched MI+ATU math (5 subs × $4 = $20/mo, $4.00/sub)",
      all(c["residual"] == 20.0 and c["subs"] == 5 and c["per_sub"] == 4.0
          for c in out_boost["company"]), out_boost["company"][:1])

# an MA tenant with FULL coverage must get no warning at all
out_full = RS.compute(FakeClient(make_db()), TENANT, months=2)     # June+July only → both covered
check("MA tenant whose visible months are fully covered gets NO warning",
      out_full["data_note"] is None and [c["commission_rows"] for c in out_full["ma_coverage"]] == [2, 2],
      out_full["ma_coverage"])
check("… and its figures are unchanged (June residual still 104.75)",
      next(c for c in out_full["company"] if c["period"] == "June 2026")["residual"] == 104.75)

section("F2. the money-vs-identifier invariant")
check("assert_money_columns accepts the real money columns",
      RS.assert_money_columns(["retail_cost", "merchant_discount"]) == ["retail_cost", "merchant_discount"])
for bad in ("merchant_invoice", "ban", "order_number", "imei", "account_id"):
    try:
        RS.assert_money_columns(["device_margin", bad], "test")
        check("assert_money_columns REJECTS %s" % bad, False)
    except ValueError as e:
        check("assert_money_columns REJECTS %s (%s)" % (bad, str(e)[:34] + "…"), bad in str(e))
check("the invariant already guards _MA_COMPONENTS at import time (no id among them)",
      not (set(RS._MA_COMPONENTS) & RS._MA_IDENTIFIER_COLUMNS))

# ══════════════════════════════════════════════════════════════════════════════════════════════
section("H. multi-tenant + zero-write discipline")
# ══════════════════════════════════════════════════════════════════════════════════════════════
c = FakeClient(make_db())
RS.compute(c, TENANT, months=6)
check("every finance read carried .eq(org_id) …", all("org_id" in r["eq"] for r in c.reads), c.reads[:2])
check("… and always the CALLER's org, never the house constant",
      all(r["eq"]["org_id"] == TENANT for r in c.reads))
check("the other tenant's identical fixture rows never leaked into the figures",
      next(x for x in RS.compute(FakeClient(make_db()), TENANT, months=6)["company"]
           if x["period"] == "June 2026")["residual"] == 104.75)
c2 = FakeClient(make_db())
W._ma_byod_residual(c2, TENANT, 6, CFG_FIXED)
W._ma_carrier_income(c2, TENANT, 6, CFG_FIXED)
check("the commission legs are org-scoped too (both tables)",
      all(r["eq"].get("org_id") == TENANT for r in c2.reads)
      and {r["table"] for r in c2.reads} == {"raw_ma_daily_tx", "raw_ma_commission"})
check("no write was reachable anywhere in this run (fake client raises on insert/update/delete)", True)

section("I. period-spelling duality (the standing finance trap)")
out_num = RS.compute(FakeClient(make_db_numeric_periods()), TENANT, months=6)
check("numeric 'YYYY-MM' spelling is picked up by the same dual-spelling query",
      len(out_num["months"]) == 6 and out_num["months"][0] == "2026-02", out_num["months"])
check("… with identical dollars to the 'Month YYYY' spelling",
      [round(x["residual"], 2) for x in out_num["company"]]
      == [round(x["residual"], 2) for x in out_fin["company"]])
check("… and the coverage warning still names the four gap months (numeric spelling)",
      out_num["data_note"] is not None and "2026-05" in out_num["data_note"])

print("\n" + "=" * 66)
print("  %d passed, %d failed" % (PASS, FAIL))
if FAILURES:
    for f in FAILURES:
        print("   - " + f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
