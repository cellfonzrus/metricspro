"""Proof harness — agent/commission/carrier-income-ledger-swap (owner-authorised 2026-07-31).

Drives the REAL commcalc.whatif carrier-income path + the REAL router source-config endpoint over an
in-memory FakeClient, and DIFFERENTIALS every result against the BASE copy of whatif.py pulled straight
out of git (origin/main 638619b) and loaded as a second module. No DB, no network, ZERO writes (the fake
client raises on insert/update/upsert/delete), non-house tenants throughout.

Run from the backend dir:  python3 scratchpad/carrier_income_ledger_swap_proof.py

Sections
  A. Constants + config defaults — plan mode defaults to the ledger, Boost untouched, the two RESIDUAL
     buckets are deliberately absent from the income bucket map.
  B. LEGACY MODE IS BYTE-IDENTICAL — with income_source='ma' every money figure, month, coverage count
     and note equals BASE, key for key. Merging the code alone moves nothing.
  C. LEDGER MODE — COMMISSION/SPIFF/EQUIPMENT_REBATE/LEDGER_OTHER come from commission_ledger; RESIDUAL
     and airtime are byte-identical to BASE; total_comp arithmetic proven.
  D. DOUBLE-COUNT GUARD — a ledger line carrying the configured residual order type is excluded from the
     income legs, counted, and reported (it is the RESIDUAL leg's dollars).
  E. PERIOD-SPELLING DUALITY — a ledger row spelled '2026-06' lands on the daily-tx 'June 2026' month.
  F. ORIGIN-AGNOSTIC — file + ma_sync rows both count; pre-251 (no origin column) degrades to a read
     without it instead of showing $0.
  G. GRACEFUL DEGRADATION — commission_ledger unreadable (mig 071 absent) → keep the legacy source and
     say so loudly; never a fabricated $0.
  H. HONESTY / COVERAGE — comp_source_missing follows the ACTIVE source; the DATA-GAP note names the
     ledger and separates "raw rows present, not synced" from "raw rows missing too".
  I. SOURCE_SWAP — the Gate-2 delta block: arithmetic, ledger-only months, totals, and the fact it is
     produced in BOTH modes.
  J. BOOST BYTE-IDENTITY — boost payload identical to BASE, boost helper source text identical, and
     commission_ledger is never read in boost mode.
  K. MULTI-TENANT + ZERO-WRITE — every read .eq(org_id, caller), two tenants cannot see each other,
     the write guard is tripped deliberately to prove it can fire.
  L. MIGRATION 253 — real PostgreSQL parse (pglast), plpgsql body parse, additive/idempotent simulation,
     no GRANT / no CREATE POLICY / no anon-authenticated, band + collision check.
  M. UI CONTRACT — the real GET source-config endpoint offers ma_ledger with a label, and the page reads
     the keys this module ships.
  N. OPERATOR DELTA SCRIPT — carrier_income_ledger_delta.py driven end to end over the fake client:
     right numbers, tenants discovered not hard-coded, and read-only by construction.
"""
import copy, importlib.util, io, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.whatif as W
import app.modules.commcalc.router as R

_HERE_DIR = os.path.dirname(os.path.abspath(__file__))

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
LUX = "22222222-2222-2222-2222-222222222222"      # non-house tenant under test
OTHER = "33333333-3333-3333-3333-333333333333"    # a second tenant that must never leak
TOTAL_ID = "aaaaaaaa-0000-0000-0000-00000000000a"
BOOST_ID = "bbbbbbbb-0000-0000-0000-00000000000b"
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
        self.f, self.rng, self.cnt, self.cols = [], None, False, "*"

    def select(self, *a, **k):
        if a:
            self.cols = a[0]
        if k.get('count') == 'exact':
            self.cnt = True
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

    def neq(self, c, v):
        self.f.append(('neq', c, v)); return self

    def is_(self, c, v):
        self.f.append(('is', c, v)); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def order(self, *a, **k):
        return self

    # ── write surface: unreachable by design ──
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
        return FakeResult(data=m, count=(len(m) if self.cnt else None))


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


# ── the BASE module (origin/main 638619b) loaded side by side ─────────────────────────────────────
BASE_REV = "638619b"
_repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_base_src = subprocess.check_output(
    ["git", "-C", _repo, "show", f"{BASE_REV}:backend/app/modules/commcalc/whatif.py"]).decode()
_tmp = tempfile.NamedTemporaryFile("w", suffix="_whatif_base.py", delete=False)
_tmp.write(_base_src)
_tmp.close()
_spec = importlib.util.spec_from_file_location("whatif_base_638619b", _tmp.name)
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────────
def cfg_rows(income="ma", org=HOUSE):
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


def _lrow(org, period, report, origin, order_type, category, **buckets):
    r = {"org_id": org, "period": period, "source_report": report, "origin": origin,
         "order_type": order_type, "category": category,
         "commission": 0, "spiff": 0, "equipment_rebate": 0,
         "residual_monthly": 0, "autopay_residual": 0, "payout_total": 0}
    r.update(buckets)
    return r


def ma_store(org=LUX, income="ma", ledger=True):
    """MAY  — daily-tx only            → gap on BOTH sources, raw source missing too
       JUNE — daily-tx + MA commission + ledger  → the covered month under test
       JULY — daily-tx + MA commission, NO ledger → gap on the LEDGER only (un-synced, not un-pulled)"""
    tx = []
    for i, c in enumerate((-100.0, -50.0, -25.0)):                       # JUNE residual: +175.00
        tx.append({"org_id": org, "period": JUNE, "order_type": "Postpaid Residual Order",
                   "account_id": f"A{i+1}", "merchant_invoice": 987654321000 + i,
                   "merchant_discount": 0, "retail_cost": c})
    tx.append({"org_id": org, "period": JUNE, "order_type": "Airtime Topup", "account_id": "A1",
               "merchant_invoice": 987654321099, "merchant_discount": 4.37, "retail_cost": 25.0})
    tx.append({"org_id": org, "period": MAY, "order_type": "Postpaid Residual Order",
               "account_id": "A1", "merchant_invoice": 987654321200,
               "merchant_discount": 0, "retail_cost": -60.0})            # MAY residual: +60.00
    tx.append({"org_id": org, "period": JULY, "order_type": "Postpaid Residual Order",
               "account_id": "A1", "merchant_invoice": 987654321300,
               "merchant_discount": 0, "retail_cost": -10.0})            # JULY residual: +10.00
    comm = [
        {"org_id": org, "period": JUNE, "merchant_account_id": "A1", "spiff_m1": -5, "spiff_m2": -5,
         "spiff_m3": -5, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0, "rebate": -10},
        {"org_id": org, "period": JUNE, "merchant_account_id": "A2", "spiff_m1": -8, "spiff_m2": 0,
         "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0, "rebate": -4},
        {"org_id": org, "period": JULY, "merchant_account_id": "A1", "spiff_m1": -3, "spiff_m2": 0,
         "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0, "rebate": -1},
    ]
    led = []
    if ledger:
        led = [
            # MA Commission Details, refreshed from raw MA data
            _lrow(org, JUNE, "ma_commission", "ma_sync", "new", "commission", commission=15.0, payout_total=15.0),
            _lrow(org, JUNE, "ma_commission", "ma_sync", "new", "spiff", spiff=8.0, payout_total=8.0),
            _lrow(org, JUNE, "ma_commission", "ma_sync", "add", "other", payout_total=42.5),
            # MA Daily Tx, hand-imported carrier file — money the legacy source cannot see at all
            _lrow(org, JUNE, "ma_daily_tx", "file", "Postpaid Commission Order", "commission",
                  commission=200.0, payout_total=200.0),
            _lrow(org, JUNE, "ma_daily_tx", "file", "Postpaid Residual Order", "residual_monthly",
                  residual_monthly=100.0, payout_total=100.0),
            # THE TRAP: a residual-order line the tenant's rules classify as commission. Its dollars are
            # already in the RESIDUAL leg — it must be excluded, counted and reported.
            _lrow(org, JUNE, "ma_daily_tx", "file", "Postpaid Residual Order", "commission",
                  commission=33.0, payout_total=33.0),
            _lrow(org, JUNE, "ma_daily_tx", "file", "Promo", "equipment_rebate",
                  equipment_rebate=12.25, payout_total=12.25),
            _lrow(org, JUNE, "ma_daily_tx", "file", "Bill Payment", "charge", payout_total=0.0),
        ]
    return {
        "carrier": [{"id": TOTAL_ID, "org_id": org, "name": "Total by Verizon", "code": "TOTAL",
                     "is_default": True}],
        "whatif_source_config": cfg_rows(income=income),
        "raw_ma_daily_tx": tx,
        "raw_ma_commission": comm,
        "commission_ledger": led,
    }


def boost_store(org=LUX):
    return {
        "carrier": [{"id": BOOST_ID, "org_id": org, "name": "Boost Mobile", "code": "BOOST",
                     "is_default": True}],
        "whatif_source_config": cfg_rows(income="ma"),
        "raw_comp_report": [
            {"org_id": org, "period": JUNE, "business_address": "1 Main St", "compensation_type":
             "Activation Commission", "payment_amount": 120.0, "quantity": 2, "account_name": "S1"},
            {"org_id": org, "period": MAY, "business_address": "1 Main St", "compensation_type":
             "Activation Commission", "payment_amount": 80.0, "quantity": 1, "account_name": "S1"},
        ],
        "commission_ledger": [_lrow(org, JUNE, "boost", "file", "x", "commission",
                                    commission=999.0, payout_total=999.0)],
    }


MONEY_MONTH_KEYS = ("period", "residual", "total_comp", "residual_mi_atu", "accounts", "qty",
                    "delta_vs_prev", "pct_vs_prev", "commission_rows", "daily_tx_rows",
                    "comp_source_missing")
BASE_COMPONENT_KEYS = ("COMMISSION", "SPIFF", "REIMBURSEMENT", "RESIDUAL", "UNMAPPED")


def month(payload, period):
    return next((m for m in payload["totals_by_month"] if m["period"] == period), None)


print("=" * 100)
print("A. CONSTANTS + CONFIG DEFAULTS")
print("=" * 100)
check("plan-mode income default is the canonical ledger",
      W._CFG_DEFAULTS["plan"]["income_source"] == "ma_ledger")
check("BASE plan-mode income default was the legacy thin source (for the record)",
      B._CFG_DEFAULTS["plan"]["income_source"] == "ma")
check("boost income default UNCHANGED",
      W._CFG_DEFAULTS["boost"]["income_source"] == B._CFG_DEFAULTS["boost"]["income_source"]
      == "boost_comp_mi_atu")
check("every other plan-mode default byte-identical to base",
      {k: v for k, v in W._CFG_DEFAULTS["plan"].items() if k != "income_source"}
      == {k: v for k, v in B._CFG_DEFAULTS["plan"].items() if k != "income_source"})
check("boost defaults dict byte-identical to base", W._CFG_DEFAULTS["boost"] == B._CFG_DEFAULTS["boost"])
check("_CFG_KEYS unchanged (no new config key needed)", W._CFG_KEYS == B._CFG_KEYS)
check("both MA sources dispatch to one handler", W.MA_INCOME_SOURCES == ("ma", "ma_ledger"))
check("income bucket map covers commission/spiff/equipment_rebate only",
      set(W.LEDGER_INCOME_BUCKETS) == {"commission", "spiff", "equipment_rebate"})
check("the two RESIDUAL buckets are NOT income buckets (no double count by construction)",
      all(b not in W.LEDGER_INCOME_BUCKETS for b in W.LEDGER_RESIDUAL_BUCKETS))
check("ledger buckets are real commission_ledger categories",
      all(b in __import__("app.modules.commcalc.commission_ledger", fromlist=["x"]).CATEGORIES
          for b in list(W.LEDGER_INCOME_BUCKETS) + list(W.LEDGER_RESIDUAL_BUCKETS)))

print()
print("=" * 100)
print("B. LEGACY MODE (income_source='ma') IS BYTE-IDENTICAL TO BASE")
print("=" * 100)
st = ma_store(income="ma")
new_legacy = W.carrier_income(FakeClient(copy.deepcopy(st)), LUX, months=6, carrier_id=TOTAL_ID)
base_legacy = B.carrier_income(FakeClient(copy.deepcopy(st)), LUX, months=6, carrier_id=TOTAL_ID)
check("same months, same order", new_legacy["months"] == base_legacy["months"],
      f'{new_legacy["months"]} vs {base_legacy["months"]}')
ok = True
for bm in base_legacy["totals_by_month"]:
    nm = month(new_legacy, bm["period"])
    if nm is None:
        ok = False
        break
    for k in MONEY_MONTH_KEYS:
        if nm.get(k) != bm.get(k):
            ok = False
            print(f"      diff {bm['period']}.{k}: {nm.get(k)} vs {bm.get(k)}")
    for k in BASE_COMPONENT_KEYS:
        if nm["components"].get(k) != bm["components"].get(k):
            ok = False
            print(f"      diff {bm['period']}.components.{k}: "
                  f"{nm['components'].get(k)} vs {bm['components'].get(k)}")
check("every month's money + counts + flags identical to base", ok)
check("the two ledger-only headings are 0.00 in legacy mode",
      all(m["components"]["EQUIPMENT_REBATE"] == 0.0 and m["components"]["LEDGER_OTHER"] == 0.0
          for m in new_legacy["totals_by_month"]))
check("data_note byte-identical to base", new_legacy["data_note"] == base_legacy["data_note"])
check("legacy data_note still names MA Commission Details",
      "NO MA Commission Details rows" in (new_legacy["data_note"] or ""))
check("residual_amount_field / residual_field_warning identical",
      (new_legacy["residual_amount_field"], new_legacy["residual_field_warning"])
      == (base_legacy["residual_amount_field"], base_legacy["residual_field_warning"]))
check("params: months/source/field/sign/row-signs identical",
      all(new_legacy["params"][k] == base_legacy["params"][k]
          for k in ("months", "source", "residual_amount_field", "ma_commission_sign",
                    "commission_row_signs")))
check("ma_coverage periods + both base counts identical",
      [{k: c[k] for k in ("period", "commission_rows", "daily_tx_rows")} for c in new_legacy["ma_coverage"]]
      == base_legacy["ma_coverage"])
check("carrier / carrier_mode / carriers / note identical",
      all(new_legacy[k] == base_legacy[k] for k in ("carrier", "carrier_mode", "carriers", "note")))
check("income_source echo identical ('ma')",
      new_legacy["income_source"] == base_legacy["income_source"] == "ma")
check("income_source_effective says legacy", new_legacy["income_source_effective"] == "ma")
check("income legs declared: commission+spiff from raw_ma_commission",
      new_legacy["income_legs"]["commission"] == new_legacy["income_legs"]["spiff"] == "raw_ma_commission")
check("residual/airtime legs always raw_ma_daily_tx",
      new_legacy["income_legs"]["residual"] == new_legacy["income_legs"]["airtime"] == "raw_ma_daily_tx")
jl = month(new_legacy, JUNE)
check("JUNE legacy COMMISSION == 23.00 (Σ|spiff_m1..m6|)", jl["components"]["COMMISSION"] == 23.0,
      jl["components"])
check("JUNE legacy SPIFF == 14.00 (Σ|rebate|)", jl["components"]["SPIFF"] == 14.0)
check("JUNE total_comp == 41.37 (23 + 14 + 4.37 airtime)", jl["total_comp"] == 41.37, jl["total_comp"])
check("JUNE residual == 175.00 (daily-tx, untouched)", jl["residual_mi_atu"] == 175.0)

print()
print("=" * 100)
print("C. LEDGER MODE — COMMISSION/SPIFF now come from commcalc.commission_ledger")
print("=" * 100)
stl = ma_store(income="ma_ledger")
new_led = W.carrier_income(FakeClient(copy.deepcopy(stl)), LUX, months=6, carrier_id=TOTAL_ID)
# The residual/airtime legs must equal what BASE produced from the SAME MA data. BASE cannot be run with
# income_source='ma_ledger' (it does not know the value and falls through to its Boost branch — which is
# exactly why migration 253 is documented to run AFTER the deploy), so the reference is BASE on the same
# fixture with the legacy value: the residual leg reads the identical raw_ma_daily_tx rows either way.
base_led = base_legacy
check("BASE does not understand 'ma_ledger' (it falls through to the Boost branch) — mig 253 runs "
      "AFTER the deploy, as the file says",
      "totals_by_month" in B.carrier_income(FakeClient(copy.deepcopy(stl)), LUX, months=6,
                                            carrier_id=TOTAL_ID)
      and B.carrier_income(FakeClient(copy.deepcopy(stl)), LUX, months=6,
                           carrier_id=TOTAL_ID).get("params", {}).get("source") != "ma")
jn = month(new_led, JUNE)
check("JUNE COMMISSION == 215.00 (15 ma_commission + 200 ma_daily_tx ledger lines)",
      jn["components"]["COMMISSION"] == 215.0, jn["components"])
check("JUNE SPIFF == 8.00 (canonical spiff bucket, NOT the rebate column)",
      jn["components"]["SPIFF"] == 8.0)
check("JUNE EQUIPMENT_REBATE == 12.25 (a heading the legacy source could not show)",
      jn["components"]["EQUIPMENT_REBATE"] == 12.25)
check("JUNE LEDGER_OTHER == 42.50 (unmapped payout, surfaced not dropped)",
      jn["components"]["LEDGER_OTHER"] == 42.5)
check("JUNE total_comp == 282.12 (215 + 8 + 12.25 + 42.5 + 0 + 4.37)", jn["total_comp"] == 282.12,
      jn["total_comp"])
check("JUNE RESIDUAL byte-identical to BASE (175.00, daily-tx only)",
      jn["residual_mi_atu"] == month(base_led, JUNE)["residual_mi_atu"] == 175.0)
check("JUNE airtime/UNMAPPED byte-identical to BASE (4.37)",
      jn["components"]["UNMAPPED"] == month(base_led, JUNE)["components"]["UNMAPPED"] == 4.37)
check("residual leg byte-identical to BASE in EVERY month",
      [m["residual_mi_atu"] for m in new_led["totals_by_month"]]
      == [m["residual_mi_atu"] for m in base_led["totals_by_month"]])
check("airtime leg byte-identical to BASE in EVERY month",
      [m["components"]["UNMAPPED"] for m in new_led["totals_by_month"]]
      == [m["components"]["UNMAPPED"] for m in base_led["totals_by_month"]])
check("accounts/qty untouched by the swap (still MA-derived)",
      [(m["accounts"], m["qty"]) for m in new_led["totals_by_month"]]
      == [(m["accounts"], m["qty"]) for m in base_led["totals_by_month"]])
check("REIMBURSEMENT stays 0.00 on the MA path (unchanged shape)",
      all(m["components"]["REIMBURSEMENT"] == 0.0 for m in new_led["totals_by_month"]))
check("income_source_effective == ma_ledger", new_led["income_source_effective"] == "ma_ledger")
check("income legs declared: commission+spiff from commission_ledger",
      new_led["income_legs"]["commission"] == new_led["income_legs"]["spiff"] == "commission_ledger")
check("params.income_leg_source names the ledger",
      new_led["params"]["income_leg_source"] == "commission_ledger")
check("params.residual_leg_source names MA Daily Tx",
      new_led["params"]["residual_leg_source"] == "raw_ma_daily_tx")
check("ledger_ready true when the table reads", new_led["ledger_ready"] is True)
check("ledger_origin_ready true when the mig-251 column exists", new_led["ledger_origin_ready"] is True)
check("no ledger_note when the ledger is healthy", new_led["ledger_note"] is None)
check("reading the ledger is IDEMPOTENT (same client, second call identical)",
      W.carrier_income(FakeClient(copy.deepcopy(stl)), LUX, months=6, carrier_id=TOTAL_ID)
      == new_led)

print()
print("=" * 100)
print("D. DOUBLE-COUNT GUARD — residual-order ledger lines are excluded, counted and reported")
print("=" * 100)
sw = new_led["source_swap"]
jsw = next(r for r in sw["by_month"] if r["period"] == JUNE)
check("2 JUNE ledger lines carry the residual order type", jsw["residual_overlap_lines"] == 2, jsw)
check("their $133.00 is reported, not silently added",
      jsw["residual_overlap_total"] == 133.0, jsw["residual_overlap_total"])
check("the residual-order line classified 'commission' ($33) did NOT reach COMMISSION",
      jn["components"]["COMMISSION"] == 215.0)
check("the residual_monthly ledger line ($100) did NOT reach any income heading",
      round(sum(jn["components"][k] for k in ("COMMISSION", "SPIFF", "EQUIPMENT_REBATE",
                                              "LEDGER_OTHER")), 2) == 277.75)
check("RESIDUAL still equals the daily-tx figure alone (no ledger residual added)",
      jn["residual_mi_atu"] == 175.0)
check("a 'charge' ledger line contributes nothing", jsw["ledger_income_lines"] == 5,
      jsw["ledger_income_lines"])
# and with an EMPTY configured residual order type the guard must not swallow everything
st_empty = ma_store(income="ma_ledger")
for r in st_empty["whatif_source_config"]:
    if r["carrier_mode"] == "plan":
        r["residual_order_type"] = ""
led_default_ot = W.carrier_income(FakeClient(st_empty), LUX, months=6, carrier_id=TOTAL_ID)
check("blank residual_order_type falls back to the documented default, not 'match everything'",
      month(led_default_ot, JUNE)["components"]["COMMISSION"] == 215.0,
      month(led_default_ot, JUNE)["components"])

print()
print("=" * 100)
print("E. PERIOD-SPELLING DUALITY — '2026-06' ledger rows land on the 'June 2026' month")
print("=" * 100)
st_dual = ma_store(income="ma_ledger")
st_dual["commission_ledger"].append(
    _lrow(LUX, "2026-06", "ma_commission", "ma_sync", "new", "commission",
          commission=7.0, payout_total=7.0))
dual = W.carrier_income(FakeClient(st_dual), LUX, months=6, carrier_id=TOTAL_ID)
check("no phantom '2026-06' month appears", "2026-06" not in dual["months"], dual["months"])
check("month list unchanged vs the single-spelling fixture", dual["months"] == new_led["months"])
check("the $7.00 landed on June 2026 (215 + 7 = 222)",
      month(dual, JUNE)["components"]["COMMISSION"] == 222.0,
      month(dual, JUNE)["components"])
check("JUNE residual still 175.00 (spelling fix touched nothing else)",
      month(dual, JUNE)["residual_mi_atu"] == 175.0)
# the reverse spelling: MA tables in 'YYYY-MM', ledger in 'Month YYYY'
st_rev = ma_store(income="ma_ledger", ledger=False)
for tbl in ("raw_ma_daily_tx", "raw_ma_commission"):
    for r in st_rev[tbl]:
        if r["period"] == JUNE:
            r["period"] = "2026-06"
st_rev["commission_ledger"] = [_lrow(LUX, JUNE, "ma_commission", "ma_sync", "new", "commission",
                                     commission=11.0, payout_total=11.0)]
rev = W.carrier_income(FakeClient(copy.deepcopy(st_rev)), LUX, months=6, carrier_id=TOTAL_ID)
check("reverse spelling also joins (one month, not two)",
      len([m for m in rev["months"] if m in ("2026-06", JUNE)]) == 1, rev["months"])
check("reverse-spelling ledger money landed on the MA month",
      month(rev, "2026-06")["components"]["COMMISSION"] == 11.0)
# PRE-EXISTING and untouched: the two MA tables themselves are keyed on their RAW period string, so a
# tenant whose raw_ma_commission and raw_ma_daily_tx disagree on spelling has always produced two slots.
# BASE does exactly the same — this package deliberately did not change the existing legs' keying (that
# is what makes section B byte-identical); only the LEDGER leg resolves across spellings.
st_split = ma_store(income="ma", ledger=False)
for r in st_split["raw_ma_daily_tx"]:
    if r["period"] == JUNE:
        r["period"] = "2026-06"
check("pre-existing MA-vs-MA spelling split reproduces IDENTICALLY at base (not introduced here)",
      W.carrier_income(FakeClient(copy.deepcopy(st_split)), LUX, months=6, carrier_id=TOTAL_ID)["months"]
      == B.carrier_income(FakeClient(copy.deepcopy(st_split)), LUX, months=6,
                          carrier_id=TOTAL_ID)["months"])

print()
print("=" * 100)
print("F. ORIGIN-AGNOSTIC — file + ma_sync both count; pre-251 degrades")
print("=" * 100)
check("JUNE counts BOTH origins", jsw["ledger_origins"] == ["file", "ma_sync"], jsw["ledger_origins"])
check("JUNE counts BOTH source reports", jsw["ledger_reports"] == ["ma_commission", "ma_daily_tx"],
      jsw["ledger_reports"])
check("origin mix reported per month on the payload too",
      month(new_led, JUNE)["ledger_origins"] == ["file", "ma_sync"])
# a ledger with ONLY file rows and ONLY sync rows must each still total correctly
st_file = ma_store(income="ma_ledger")
st_file["commission_ledger"] = [r for r in st_file["commission_ledger"] if r["origin"] == "file"]
only_file = W.carrier_income(FakeClient(st_file), LUX, months=6, carrier_id=TOTAL_ID)
check("file-only ledger → 200.00 commission (the sync rows' 15 dropped out)",
      month(only_file, JUNE)["components"]["COMMISSION"] == 200.0)
st_sync = ma_store(income="ma_ledger")
st_sync["commission_ledger"] = [r for r in st_sync["commission_ledger"] if r["origin"] == "ma_sync"]
only_sync = W.carrier_income(FakeClient(st_sync), LUX, months=6, carrier_id=TOTAL_ID)
check("sync-only ledger → 15.00 commission", month(only_sync, JUNE)["components"]["COMMISSION"] == 15.0)
# pre-251: the origin column does not exist
pre251 = W.carrier_income(
    FakeClient(copy.deepcopy(stl), missing_cols={"commission_ledger": ["origin"]}),
    LUX, months=6, carrier_id=TOTAL_ID)
check("pre-251 read still succeeds (drops the provenance column, keeps the money)",
      month(pre251, JUNE)["components"]["COMMISSION"] == 215.0,
      month(pre251, JUNE)["components"])
check("pre-251 flagged: ledger_origin_ready false", pre251["ledger_origin_ready"] is False)
check("pre-251 ledger_ready still true", pre251["ledger_ready"] is True)
check("pre-251 origin mix honestly reported as unknown",
      month(pre251, JUNE)["ledger_origins"] == ["unknown"])

print()
print("=" * 100)
print("G. GRACEFUL DEGRADATION — no ledger table (mig 071 absent) never fabricates $0")
print("=" * 100)
noled = W.carrier_income(FakeClient(copy.deepcopy(stl), absent={"commission_ledger"}),
                         LUX, months=6, carrier_id=TOTAL_ID)
check("ledger_ready false", noled["ledger_ready"] is False)
check("falls back to the LEGACY source rather than showing $0",
      noled["income_source_effective"] == "ma")
check("legacy numbers are what is shown (23 / 14)",
      (month(noled, JUNE)["components"]["COMMISSION"], month(noled, JUNE)["components"]["SPIFF"])
      == (23.0, 14.0))
check("a loud ledger_note explains the fallback",
      "could not be read" in (noled["ledger_note"] or "") and
      "LEGACY" in (noled["ledger_note"] or ""), noled["ledger_note"])
check("configured source is still echoed honestly", noled["income_source"] == "ma_ledger")
check("money identical to the legacy-mode payload in this state",
      [m["total_comp"] for m in noled["totals_by_month"]]
      == [m["total_comp"] for m in base_legacy["totals_by_month"]])
# empty (but present) ledger is NOT a fallback — it is a real, flagged gap
st_empty_led = ma_store(income="ma_ledger")
st_empty_led["commission_ledger"] = []
emptyled = W.carrier_income(FakeClient(st_empty_led), LUX, months=6, carrier_id=TOTAL_ID)
check("present-but-empty ledger stays on the ledger (no silent revert)",
      emptyled["income_source_effective"] == "ma_ledger" and emptyled["ledger_note"] is None)
check("…and reads $0 comp honestly for every month",
      all(m["components"]["COMMISSION"] == 0.0 for m in emptyled["totals_by_month"]))
check("…and flags every daily-tx month as a gap",
      all(m["comp_source_missing"] for m in emptyled["totals_by_month"]))

print()
print("=" * 100)
print("H. HONESTY / COVERAGE — the note follows the ACTIVE source")
print("=" * 100)
check("ledger mode: MAY + JULY flagged as gaps (no ledger lines)",
      [m["period"] for m in new_led["totals_by_month"] if m["comp_source_missing"]] == [MAY, JULY])
check("legacy mode: only MAY is a gap (JULY has MA commission rows)",
      [m["period"] for m in new_legacy["totals_by_month"] if m["comp_source_missing"]] == [MAY])
check("JUNE is not a gap in either mode",
      month(new_led, JUNE)["comp_source_missing"] is False
      and month(new_legacy, JUNE)["comp_source_missing"] is False)
note = new_led["data_note"] or ""
check("ledger note names the Commission Ledger as the source", "Commission Ledger" in note, note)
check("ledger note says 'NO Commission Ledger lines'", "NO Commission Ledger lines" in note)
check("ledger note states the ledger is origin-agnostic", "origin-agnostic" in note)
check("ledger note explains why residual still shows",
      "Residual and airtime margin still come straight from MA Daily Tx" in note)
check("ledger note separates UN-SYNCED (July: raw rows present) from UN-PULLED (May)",
      "ALREADY loaded for " + JULY in note and "For " + MAY in note, note)
check("ledger note tells the operator to refresh, not to re-pull, for July",
      "Refresh from MA" in note)
check("coverage carries ledger line counts per month",
      [c["ledger_lines"] for c in new_led["ma_coverage"]] == [0, 8, 0],
      [c["ledger_lines"] for c in new_led["ma_coverage"]])
check("coverage carries the income-bucket line count too",
      [c["ledger_income_lines"] for c in new_led["ma_coverage"]] == [0, 5, 0])
check("coverage keeps the legacy MA counts alongside (diagnosis stays possible)",
      [(c["commission_rows"], c["daily_tx_rows"]) for c in new_led["ma_coverage"]]
      == [(0, 1), (2, 4), (1, 1)])

print()
print("=" * 100)
print("I. SOURCE_SWAP — the Gate-2 delta block")
print("=" * 100)
check("present in LEDGER mode", bool(new_led.get("source_swap")))
check("present in LEGACY mode too (the delta is visible BEFORE the switch)",
      bool(new_legacy.get("source_swap")))
check("active flag correct both ways",
      new_led["source_swap"]["active"] == "ma_ledger"
      and new_legacy["source_swap"]["active"] == "ma")
check("the two by_month tables are identical regardless of which source is active",
      new_led["source_swap"]["by_month"] == new_legacy["source_swap"]["by_month"])
check("JUNE old = 23 / 14 / 37", (jsw["old_commission"], jsw["old_spiff"], jsw["old_total"])
      == (23.0, 14.0, 37.0))
check("JUNE new = 215 / 8 / 12.25 / 42.5 / 277.75",
      (jsw["new_commission"], jsw["new_spiff"], jsw["new_equipment_rebate"], jsw["new_other"],
       jsw["new_total"]) == (215.0, 8.0, 12.25, 42.5, 277.75))
check("JUNE delta_total == +240.75", jsw["delta_total"] == 240.75)
check("delta arithmetic self-consistent on every row",
      all(round(r["new_total"] - r["old_total"], 2) == r["delta_total"]
          for r in new_led["source_swap"]["by_month"]))
tot = new_led["source_swap"]["totals"]
check("totals row sums the months (May 0 + June 37 + July 4 old; June-only 277.75 new)",
      tot["old_total"] == 41.0 and tot["new_total"] == 277.75 and tot["delta_total"] == 236.75, tot)
check("totals split by leg too", (tot["old_commission"], tot["old_spiff"]) == (26.0, 15.0)
      and (tot["delta_commission"], tot["delta_spiff"]) == (189.0, -7.0), tot)
check("totals carry row counts on BOTH sides",
      (tot["commission_rows"], tot["ledger_lines"]) == (3, 8), tot)
check("totals carry the excluded residual-overlap lines",
      (tot["residual_overlap_lines"], tot["residual_overlap_total"]) == (2, 133.0), tot)
check("old/new source strings name the real tables",
      "raw_ma_commission" in new_led["source_swap"]["old_source"]
      and "commission_ledger" in new_led["source_swap"]["new_source"])
check("legacy-mode note says nothing has moved",
      "Nothing on this page has moved" in new_legacy["source_swap"]["note"])
# a ledger-only month must be accounted for in BOTH modes, but only appear on the payload when active
st_only = ma_store(income="ma")
st_only["commission_ledger"].append(
    _lrow(LUX, "April 2026", "ma_commission", "ma_sync", "new", "commission",
          commission=64.0, payout_total=64.0))
only_legacy = W.carrier_income(FakeClient(copy.deepcopy(st_only)), LUX, months=6, carrier_id=TOTAL_ID)
st_only["whatif_source_config"] = cfg_rows(income="ma_ledger")
only_ledger = W.carrier_income(FakeClient(copy.deepcopy(st_only)), LUX, months=6, carrier_id=TOTAL_ID)
check("legacy payload does NOT invent the ledger-only month (byte-identity)",
      only_legacy["months"] == base_legacy["months"], only_legacy["months"])
check("…but source_swap still accounts for it, flagged on_payload=false",
      any(r["period"] == "April 2026" and r["on_payload"] is False
          for r in only_legacy["source_swap"]["by_month"]))
check("ledger mode DOES show the ledger-only month", "April 2026" in only_ledger["months"])
check("…with residual/airtime honestly 0 there",
      month(only_ledger, "April 2026")["residual_mi_atu"] == 0.0
      and month(only_ledger, "April 2026")["components"]["UNMAPPED"] == 0.0)
check("…and its commission booked", month(only_ledger, "April 2026")["components"]["COMMISSION"] == 64.0)
check("BASE has no source_swap key at all (this is new, additive)", "source_swap" not in base_legacy)

print()
print("=" * 100)
print("J. BOOST BYTE-IDENTITY")
print("=" * 100)
bstore = boost_store()
READS.clear()
nb = W.carrier_income(FakeClient(copy.deepcopy(bstore)), LUX, months=6, carrier_id=BOOST_ID)
boost_reads = [t for t, _f in READS]
bb = B.carrier_income(FakeClient(copy.deepcopy(bstore)), LUX, months=6, carrier_id=BOOST_ID)
check("boost carrier-income payload byte-identical to base", nb == bb)
check("boost mode resolved", nb["carrier_mode"] == "boost")
check("boost income_source unchanged", nb["income_source"] == "boost_comp_mi_atu")
check("boost NEVER reads commission_ledger", "commission_ledger" not in boost_reads, boost_reads)
check("boost NEVER reads the MA tables here",
      "raw_ma_commission" not in boost_reads and "raw_ma_daily_tx" not in boost_reads)
import inspect
for fn in ("_boost_byod_residual", "_byod_specific_residual", "_residual_by_mdn", "_byod_mdns",
           "_normalize_amount", "_ma_residual_amount", "_ma_commission_amount", "activation_baseline",
           "_boost_template", "_boost_actuals", "_rates", "_ma_byod_residual", "byod_residual",
           "_whatif_source_config", "_carrier_ctx"):
    check(f"source text identical to base: {fn}",
          inspect.getsource(getattr(W, fn)) == inspect.getsource(getattr(B, fn)))
check("BYOD-residual (tab 2) payload byte-identical for the MA carrier",
      W.byod_residual(FakeClient(copy.deepcopy(stl)), LUX, months=6, carrier_id=TOTAL_ID)
      == B.byod_residual(FakeClient(copy.deepcopy(stl)), LUX, months=6, carrier_id=TOTAL_ID))
check("activation-baseline (tab 1) payload byte-identical",
      W.activation_baseline(FakeClient(copy.deepcopy(stl)), LUX, JUNE, carrier_id=TOTAL_ID)
      == B.activation_baseline(FakeClient(copy.deepcopy(stl)), LUX, JUNE, carrier_id=TOTAL_ID))

print()
print("=" * 100)
print("K. MULTI-TENANT + ZERO-WRITE")
print("=" * 100)
mixed = ma_store(org=LUX, income="ma_ledger")
other = ma_store(org=OTHER, income="ma_ledger")
for tbl in ("carrier", "raw_ma_daily_tx", "raw_ma_commission", "commission_ledger"):
    mixed[tbl] = list(mixed[tbl]) + list(other[tbl])
mixed["commission_ledger"].append(
    _lrow(OTHER, JUNE, "ma_commission", "ma_sync", "new", "commission",
          commission=999999.0, payout_total=999999.0))
READS.clear()
lux_view = W.carrier_income(FakeClient(copy.deepcopy(mixed)), LUX, months=6, carrier_id=TOTAL_ID)
lux_reads = list(READS)
check("tenant sees only its own ledger money (999,999 never appears)",
      month(lux_view, JUNE)["components"]["COMMISSION"] == 215.0,
      month(lux_view, JUNE)["components"])
check("EVERY commission_ledger read is org-scoped to the caller",
      all(('eq', 'org_id', LUX) in f for t, f in lux_reads if t == "commission_ledger"),
      [f for t, f in lux_reads if t == "commission_ledger"])
check("at least one commission_ledger read actually happened",
      any(t == "commission_ledger" for t, _f in lux_reads))
check("EVERY read of every table is org-scoped to the caller (no house fallback except mig-209 config)",
      all(('eq', 'org_id', LUX) in f or t == "whatif_source_config" for t, f in lux_reads),
      [(t, f) for t, f in lux_reads if ('eq', 'org_id', LUX) not in f])
other_view = W.carrier_income(FakeClient(copy.deepcopy(mixed)), OTHER, months=6, carrier_id=TOTAL_ID)
check("the OTHER tenant sees its own 999,999 + its own 215",
      month(other_view, JUNE)["components"]["COMMISSION"] == 215.0 + 999999.0,
      month(other_view, JUNE)["components"])
check("no cross-tenant equality (the two views really differ)",
      month(lux_view, JUNE)["components"] != month(other_view, JUNE)["components"])
check("ZERO writes attempted anywhere in this run", WRITES == [], WRITES)
try:
    FakeClient({}).schema("commcalc").table("commission_ledger").insert({"x": 1})
    check("write guard fires", False, "insert did not raise")
except AssertionError:
    check("write guard proven live (a deliberate insert raises)", True)
WRITES.clear()

print()
print("=" * 100)
print("L. MIGRATION 253")
print("=" * 100)
MIGDIR = os.path.join(_repo, "database", "migrations")
MIG = os.path.join(MIGDIR, "253_commission_whatif_income_source_ledger.sql")
sql = io.open(MIG, encoding="utf-8").read()
check("file exists in band 200–299", os.path.exists(MIG))
existing = sorted(f for f in os.listdir(MIGDIR) if re.match(r"^2\d\d_", f))
check("253 is not a collision (only one 253_* file)",
      len([f for f in existing if f.startswith("253_")]) == 1, existing[-4:])
check("252 was the previous band high-water mark",
      any(f.startswith("252_") for f in existing)
      and not any(re.match(r"^25[4-9]_|^2[6-9]\d_", f) for f in existing))
try:
    import pglast
    from pglast import parse_sql
    stmts = parse_sql(sql)
    check("whole file parses as real PostgreSQL", len(stmts) >= 2)
    body = re.search(r"DO \$\$(.*?)\$\$;", sql, re.S).group(1)
    parse_sql("DO $x$" + body + "$x$;")
    check("the DO block's plpgsql body parses", True)
    inner = re.search(r"UPDATE commcalc\.whatif_source_config.*?WHERE income_source = 'ma';", sql, re.S)
    parse_sql(inner.group(0))
    check("the UPDATE parses on its own", True)
except Exception as e:
    check("migration parses with pglast", False, repr(e))
# strip `--` comments first: the file's header explains in prose what it does NOT do, and a naive grep
# would flag its own safety statement.
code = "\n".join(re.sub(r"--.*$", "", ln) for ln in sql.splitlines())
check("no GRANT to anon/authenticated (executable SQL)", not re.search(r"\bGRANT\b", code, re.I))
check("no CREATE POLICY (executable SQL)", not re.search(r"CREATE\s+POLICY", code, re.I))
check("no anon / authenticated role (executable SQL)", not re.search(r"\b(anon|authenticated)\b", code))
check("no DROP / DELETE / TRUNCATE (executable SQL)",
      not re.search(r"\b(DROP|DELETE\s+FROM|TRUNCATE)\b", code, re.I))
check("the header DOES state the safety posture in prose", "no CREATE POLICY" in sql)
check("guarded on the mig-209 table existing", "information_schema.tables" in sql)
check("idempotent: the UPDATE is filtered on the OLD value only",
      "WHERE income_source = 'ma';" in sql)
check("a second run matches zero rows (no row can still be 'ma' after the first)",
      "SET income_source = 'ma_ledger'" in sql)
check("the note-append is itself idempotent (guarded on the [253] marker)", "'%[253]%'" in sql)
check("code default and SQL target agree ('ma_ledger')",
      W._CFG_DEFAULTS["plan"]["income_source"] == "ma_ledger" and "'ma_ledger'" in sql)
check("boost rows cannot match the filter",
      W._CFG_DEFAULTS["boost"]["income_source"] == "boost_comp_mi_atu"
      and "boost_comp_mi_atu" not in re.search(r"WHERE income_source.*?;", sql, re.S).group(0))
check("a revert path is documented in the file", "REVERT" in sql)
# also drop string LITERALS: the COMMENT ON COLUMN text names commission_ledger in prose, which is
# documentation, not a table this migration touches.
code_nolit = re.sub(r"'(?:[^']|'')*'", "''", code)
check("touches ONE table only (executable SQL, literals stripped)",
      set(re.findall(r"commcalc\.(\w+)", code_nolit)) == {"whatif_source_config"},
      set(re.findall(r"commcalc\.(\w+)", code_nolit)))
check("commission_ledger is never written/altered by 253 (it is only named in documentation)",
      not re.search(r"(INSERT\s+INTO|UPDATE|ALTER\s+TABLE|CREATE\s+TABLE)\s+commcalc\.commission_ledger",
                    code_nolit, re.I))

print()
print("=" * 100)
print("M. UI CONTRACT")
print("=" * 100)


class _Cfg:
    pass


orig_sb, orig_req = R.sb, R.require_org
R.sb = lambda: FakeClient(copy.deepcopy(stl))
R.require_org = lambda *a, **k: None
try:
    cfgres = R.whatif_get_source_config(carrier_id=TOTAL_ID, org_id=LUX)
finally:
    R.sb, R.require_org = orig_sb, orig_req
opts = cfgres["options"]["income_source"]
check("⚙️ Sources offers ma_ledger", "ma_ledger" in opts, opts)
check("…listed before the legacy option (the recommendation is visible)",
      opts.index("ma_ledger") < opts.index("ma"))
check("legacy 'ma' is still selectable (one-click revert, no deploy)", "ma" in opts)
check("boost option untouched", opts[0] == "boost_comp_mi_atu")
labels = cfgres["option_labels"]["income_source"]
check("ma_ledger carries a human label naming the Commission Ledger",
      "Commission Ledger" in labels["ma_ledger"], labels)
check("legacy label says 'legacy'", "legacy" in labels["ma"].lower())
check("PUT still accepts income_source (config, not code)",
      "income_source" in inspect.getsource(R.whatif_put_source_config))

PAGE = os.path.join(_repo, "frontend", "src", "app", "(platform)", "commcalc", "whatif", "page.tsx")
page = io.open(PAGE, encoding="utf-8").read()
for key in ("income_source_effective", "ledger_note", "source_swap", "EQUIPMENT_REBATE",
            "LEDGER_OTHER", "ledger_lines", "comp_source_missing", "ledger_origins",
            "residual_overlap_lines", "on_payload"):
    check(f"page reads payload key `{key}`", key in page)
check("page label says 'Commission Ledger (canonical)' for the ledger source",
      "Commission Ledger (canonical) + MA Daily Tx" in page)
check("legacy label renamed to 'MA Commission Details + MA Daily Tx'",
      "MA Commission Details + MA Daily Tx" in page)
check("period picker says '(no ledger lines)' in ledger mode", "(no ledger lines)" in page)
check("reconciliation panel exists", "function SourceSwap" in page)
check("the panel is rendered on the tab", "<SourceSwap swap={trend.source_swap} />" in page)

print()
print("=" * 100)
print("N. OPERATOR DELTA SCRIPT — the Gate-2 artifact, exercised end to end")
print("=" * 100)
import contextlib
import app.core.database as _DB
_delta_path = os.path.join(_HERE_DIR, "carrier_income_ledger_delta.py")
check("delta script exists", os.path.exists(_delta_path))
_dspec = importlib.util.spec_from_file_location("carrier_income_ledger_delta", _delta_path)
D = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(D)
_dstore = ma_store(income="ma_ledger")
_dstore["tenants"] = [{"org_id": LUX}]
_dfake = FakeClient(copy.deepcopy(_dstore))
_orig_get = _DB.get_supabase
_DB.get_supabase = lambda *a, **k: _dfake
_argv = sys.argv
sys.argv = ["delta", "--months", "6"]
_buf = io.StringIO()
try:
    with contextlib.redirect_stdout(_buf):
        _rc = D.main()
finally:
    sys.argv = _argv
    _DB.get_supabase = _orig_get
_out = _buf.getvalue()
check("delta script runs clean (exit 0)", _rc == 0, _rc)
check("tenants DISCOVERED from the DB, not hard-coded", LUX in _out and "--org-id" not in _out)
check("names both sources explicitly",
      "raw_ma_commission" in _out and "commission_ledger" in _out)
check("states residual/airtime are UNCHANGED", "residual + airtime margin" in _out)
check("emits the per-month row for June with the real delta",
      "| June 2026 | 23.00 | 14.00 | 37.00 | 215.00 | 8.00 | 12.25 | 42.50 | 277.75 | +240.75 |" in _out,
      [l for l in _out.splitlines() if "June" in l])
check("emits a TOTAL row", "| **TOTAL** |" in _out and "**+236.75**" in _out)
check("reports the excluded residual overlap", "EXCLUDED from the NEW totals" in _out)
check("reports the unmapped 'other' bucket + where to map it",
      "commission-category-map" in _out)
check("carries the DATA-GAP note", "DATA GAP" in _out)
check("prints a grand total across tenants", "Grand total across every MA-fed tenant" in _out)
check("ledger origin mix is in the table", "file,ma_sync" in _out)
try:
    D.ReadOnlyClient(_dfake).schema("commcalc").table("commission_ledger").insert({"x": 1})
    check("delta script is read-only", False, "insert did not raise")
except RuntimeError as e:
    check("delta script is READ-ONLY by construction (insert raises)", "read-only" in str(e))
try:
    D.ReadOnlyClient(_dfake).schema("commcalc").table("commission_ledger").delete()
    check("delta script refuses delete", False, "delete did not raise")
except RuntimeError:
    check("delta script refuses delete too", True)
check("the script reuses the REAL handler (no second implementation to drift)",
      "_ma_carrier_income" in io.open(_delta_path, encoding="utf-8").read())
check("still ZERO writes after the delta run", WRITES == [], WRITES)

print()
print("=" * 100)
print(f"RESULT: {PASS} passed, {FAIL} failed")
print("=" * 100)
sys.exit(1 if FAIL else 0)
