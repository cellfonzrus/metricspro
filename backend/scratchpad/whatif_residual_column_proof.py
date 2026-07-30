"""Proof harness — agent/commission/whatif-residual-column (finance escalation 2026-07-30 §④.1/2/3).

Drives the REAL commcalc.whatif functions + the REAL router source-config endpoints over an in-memory
FakeClient, and DIFFERENTIALS them against the BASE copy of whatif.py pulled straight out of git
(origin/main 875a3b9) and loaded as a second module. No DB, no network, ZERO writes (the fake client
raises on insert/update/upsert/delete), non-house tenants throughout.

Run from the backend dir:  python3 scratchpad/whatif_residual_column_proof.py

Sections
  A. Money-vs-identifier class fix (roles checked against the ma_upload catalogue; defaults; fallback set)
  B. Bug reproduction + differential — an MA fixture whose merchant_invoice holds 12-digit IDs and whose
     retail_cost holds real signed dollars: OLD sums the IDs (-$2.96e12, the -$4.9e11 class), NEW sums
     retail_cost (+$825.85); BOTH legs (tab 2 + tab 4) move identically (one shared helper).
  C. Config-override precedence — an org that explicitly configured a field KEEPS it (including the old
     merchant_invoice), and then the page carries a loud warning instead of a silent lie.
  D. Fallback heuristic REVERSED — cents/negative-bearing money column instead of max(|value|); an
     identifier can never be picked, even as the only non-empty column.
  E. Sign consistency for COMMISSION / SPIFF / RESIDUAL (§④.2) + cross-surface parity against the REAL
     /ma-commission/summary handler + config opt-out (`ma_commission_sign`).
  F. Ingest-coverage flag (§④.3): per-report row counts, comp_source_missing, the DATA-GAP note, and the
     explicit "different, thinner source — NOT a stale ledger" answer.
  G. BOOST byte-identity — boost residual (raw_mi) + boost carrier income (comp_trend) payloads identical
     old vs new, and the boost helper SOURCE TEXT byte-identical to base.
  H. Zero-write + org scoping + cross-tenant isolation + period-spelling duality.
  I. UI contract — the real GET/PUT source-config endpoints (option list, ⚠ label, new key savable) and
     the page's key names.
  J. Migration 252 — real PostgreSQL parse (pglast), plpgsql body parse, idempotency simulation,
     no-op-if-owner-already-ran, additive-only, no grants/policies, band + collision check, and code↔SQL
     agreement with finance §③ #6.
"""
import copy, importlib.util, json, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.whatif as W
import app.modules.commcalc.router as R
from app.modules.commcalc import ma_upload

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
MAY, JUNE = "May 2026", "June 2026"

WRITES = []      # any attempted write lands here (must stay empty)
READS = []       # (table, filters) for every read


# ── in-memory fake supabase client — reads only ───────────────────────────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table, absent):
        self.store, self.t, self.absent = store, table, absent
        self.f, self.rng, self.cnt = [], None, False

    def select(self, *a, **k):
        if k.get('count') == 'exact':
            self.cnt = True
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
        READS.append((self.t, list(self.f)))
        rows = self.store.setdefault(self.t, [])
        m = [dict(r) for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        return FakeResult(data=m, count=(len(m) if self.cnt else None))


class FakeSchema:
    def __init__(self, store, absent):
        self.store, self.absent = store, absent

    def table(self, t):
        return FakeQuery(self.store, t, self.absent)

    def rpc(self, name, params):
        raise Exception('no such rpc: ' + name)     # force the documented Python fallbacks


class FakeClient:
    def __init__(self, store, absent=None):
        self.store, self.absent = store, set(absent or [])

    def schema(self, s):
        return FakeSchema(self.store, self.absent)


# ── the BASE module (origin/main 875a3b9) loaded side by side ──────────────────────────────────────
BASE_REV = "875a3b9"
_repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_base_src = subprocess.check_output(
    ["git", "-C", _repo, "show", f"{BASE_REV}:backend/app/modules/commcalc/whatif.py"]).decode()
_tmp = tempfile.NamedTemporaryFile("w", suffix="_whatif_base.py", delete=False)
_tmp.write(_base_src)
_tmp.close()
_spec = importlib.util.spec_from_file_location("whatif_base_875a3b9", _tmp.name)
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────────
# The seeded mig-209 config as it exists in PRODUCTION TODAY (before the owner's §③ #6 statement):
# residual_amount_field = merchant_invoice  ← the defect.
def cfg_rows(field="merchant_invoice", org=HOUSE, extra=None):
    row = {"org_id": org, "carrier_id": NIL, "carrier_mode": "plan", "is_active": True,
           "residual_source": "ma_daily_tx", "residual_order_type": "Postpaid Residual Order",
           "residual_amount_field": field, "residual_sign": "negate", "income_source": "ma",
           "retail_cost_source": "none"}
    row.update(extra or {})
    boost = {"org_id": org, "carrier_id": NIL, "carrier_mode": "boost", "is_active": True,
             "residual_source": "boost_mi_atu", "residual_order_type": None,
             "residual_amount_field": field, "residual_sign": "as_is",
             "income_source": "boost_comp_mi_atu", "retail_cost_source": "none"}
    return [row, boost]


# 12-digit Merchant Invoice NUMBERS next to real signed dollars in retail_cost.
INV = [987654321012, 987654321013, 987654321014]
COST = [-412.37, -298.11, -115.37]                       # Σ = -825.85  → +825.85 as income
INV_SUM_NEGATED = -sum(INV)                              # -2,962,962,963,039  (the -4.9e11 class)
COST_SUM_NEGATED = round(-sum(COST), 2)                  # +825.85


def ma_store(org=LUX, period=MAY, with_commission=True, carrier_org=None):
    tx = [{"org_id": org, "period": period, "order_type": "Postpaid Residual Order",
           "account_id": f"A{i+1}", "order_number": f"ON{i+1}", "product_name": "Postpaid Residual",
           "merchant_invoice": INV[i], "merchant_discount": 0, "retail_cost": COST[i]}
          for i in range(3)]
    tx.append({"org_id": org, "period": period, "order_type": "Airtime Topup", "account_id": "A1",
               "order_number": "ON9", "product_name": "Airtime", "merchant_invoice": 987654321099,
               "merchant_discount": 4.37, "retail_cost": 25.00})
    # June: BOTH reports present (so coverage has a covered month to contrast with the gap month)
    tx.append({"org_id": org, "period": JUNE, "order_type": "Postpaid Residual Order",
               "account_id": "A1", "order_number": "ON10", "product_name": "Postpaid Residual",
               "merchant_invoice": 987654321020, "merchant_discount": 0, "retail_cost": -100.00})
    comm = []
    if with_commission:
        # MA Commission Details convention: NEGATIVE = paid to the dealer.
        comm = [
            {"org_id": org, "period": JUNE, "merchant_account_id": "A1", "activation_type2": "byop",
             "imei": "111", "ban": "b1", "spiff_m1": -5, "spiff_m2": -5, "spiff_m3": -5,
             "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0, "rebate": -10, "user_name": "Rep One",
             "platform": "P1", "activation_type": "new", "sub_type": "s", "mrc_net_discount": 30,
             "device_margin": 0, "consumer_margin": 0, "consumer_financing": 0, "wallet_funding": 0,
             "fees_margin": 0, "tx_date": "2026-06-04"},
            {"org_id": org, "period": JUNE, "merchant_account_id": "A2", "activation_type2": "branded",
             "imei": "222", "ban": "b2", "spiff_m1": -8, "spiff_m2": 0, "spiff_m3": 0,
             "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0, "rebate": -4, "user_name": "Rep Two",
             "platform": "P1", "activation_type": "add", "sub_type": "s", "mrc_net_discount": 25,
             "device_margin": 0, "consumer_margin": 0, "consumer_financing": 0, "wallet_funding": 0,
             "fees_margin": 0, "tx_date": "2026-06-05"},
        ]
    return {
        "carrier": [{"id": TOTAL_ID, "org_id": carrier_org or org, "name": "Total by Verizon",
                     "code": "TOTAL", "is_default": True}],
        "whatif_source_config": cfg_rows(),
        "raw_ma_daily_tx": tx,
        "raw_ma_commission": comm,
    }


def with_config(store, field=None, extra=None, org=HOUSE):
    s = copy.deepcopy(store)
    s["whatif_source_config"] = cfg_rows(field=field or "merchant_invoice", org=org, extra=extra)
    return s


print("=" * 100)
print("A. MONEY vs IDENTIFIER — the class fix")
print("=" * 100)
check("merchant_invoice is NOT a money column", W.is_ma_money_column("merchant_invoice") is False)
check("retail_cost IS a money column", W.is_ma_money_column("retail_cost") is True)
check("merchant_discount IS a money column", W.is_ma_money_column("merchant_discount") is True)
check("blank field is not money", W.is_ma_money_column("") is False and W.is_ma_money_column(None) is False)
check("ma_upload catalogue agrees: merchant_invoice role == 'key'",
      ma_upload.FIELD_LABELS["merchant_invoice"]["role"] == "key")
check("ma_upload catalogue agrees: retail_cost role == 'money'",
      ma_upload.FIELD_LABELS["retail_cost"]["role"] == "money")
check("no role drift detected at import (_MA_COLUMN_ROLE_DRIFT empty)", W._MA_COLUMN_ROLE_DRIFT == [],
      W._MA_COLUMN_ROLE_DRIFT)
check("plan default residual $ column == retail_cost",
      W._CFG_DEFAULTS["plan"]["residual_amount_field"] == "retail_cost")
check("boost default residual $ column == retail_cost (unused there, but never the id)",
      W._CFG_DEFAULTS["boost"]["residual_amount_field"] == "retail_cost")
check("NO code default anywhere points at an identifier column",
      all(W.is_ma_money_column(d["residual_amount_field"]) for d in W._CFG_DEFAULTS.values()))
check("BASE had merchant_invoice as the default in BOTH modes (the defect, for the record)",
      B._CFG_DEFAULTS["plan"]["residual_amount_field"] == "merchant_invoice"
      and B._CFG_DEFAULTS["boost"]["residual_amount_field"] == "merchant_invoice")
check("fallback candidate set excludes every identifier column",
      all(c not in W._MA_IDENTIFIER_COLUMNS for c in W._MA_MONEY_COLUMNS))
check("ma_commission_sign is a resolvable config key (_CFG_KEYS)", "ma_commission_sign" in W._CFG_KEYS)
check("default MA commission sign == negate", W._CFG_DEFAULTS["plan"]["ma_commission_sign"] == "negate")


print("=" * 100)
print("B. BUG REPRODUCTION + DIFFERENTIAL — ids summed as dollars vs real signed dollars")
print("=" * 100)
store_old = ma_store()                                     # config still says merchant_invoice
store_new = with_config(ma_store(), field="retail_cost")    # config after mig 252 / §③ #6

# BASE code + BASE (defective) config  → the garbage the owner pasted
b_res = B.byod_residual(FakeClient(copy.deepcopy(store_old)), LUX, months=6, carrier_id=TOTAL_ID)
b_may = next((s for s in b_res["series"] if s["period"] == MAY), {})
check("BASE tab-2 May residual is the -4.9e11 CLASS garbage (sum of invoice NUMBERS)",
      b_may.get("residual") == INV_SUM_NEGATED and b_may["residual"] < -1e11, b_may)
b_inc = B.carrier_income(FakeClient(copy.deepcopy(store_old)), LUX, months=6, carrier_id=TOTAL_ID)
b_inc_may = next((t for t in b_inc["totals_by_month"] if t["period"] == MAY), {})
check("BASE tab-4 May residual_mi_atu is the IDENTICAL garbage number (one shared helper)",
      b_inc_may.get("residual_mi_atu") == b_may.get("residual"), (b_inc_may, b_may))

# NEW code + corrected config → real dollars
n_res = W.byod_residual(FakeClient(copy.deepcopy(store_new)), LUX, months=6, carrier_id=TOTAL_ID)
n_may = next((s for s in n_res["series"] if s["period"] == MAY), {})
check(f"NEW tab-2 May residual == {COST_SUM_NEGATED} (Σ retail_cost, sign-normalized)",
      n_may.get("residual") == COST_SUM_NEGATED, n_may)
n_inc = W.carrier_income(FakeClient(copy.deepcopy(store_new)), LUX, months=6, carrier_id=TOTAL_ID)
n_inc_may = next((t for t in n_inc["totals_by_month"] if t["period"] == MAY), {})
check("NEW tab-4 May residual_mi_atu matches tab 2 exactly (still one helper)",
      n_inc_may.get("residual_mi_atu") == n_may.get("residual"), (n_inc_may, n_may))
check("the delta this fixes is the whole garbage figure",
      round(n_may["residual"] - b_may["residual"], 2) == round(COST_SUM_NEGATED - INV_SUM_NEGATED, 2))
check("NEW per_sub is believable ($275.28/sub over 3 accounts), BASE was not",
      n_may.get("per_sub") == round(COST_SUM_NEGATED / 3, 2) and abs(b_may.get("per_sub")) > 1e10,
      (n_may.get("per_sub"), b_may.get("per_sub")))
check("NEW residual_amount_field is reported on the payload", n_res.get("residual_amount_field") == "retail_cost")
check("NEW payload carries NO warning once the column is money", n_res.get("residual_field_warning") is None)

# Same-config differential: with the CONFIG unchanged (merchant_invoice), everything OTHER than the
# residual legs must be untouched by this package — isolate by dropping the commission rows (their sign
# is deliberately changed in section E).
store_nocomm_old = ma_store(with_commission=False)
store_nocomm_new = with_config(ma_store(with_commission=False), field="retail_cost")
b_nc = B.byod_residual(FakeClient(copy.deepcopy(store_nocomm_old)), LUX, months=6, carrier_id=TOTAL_ID)
n_nc = W.byod_residual(FakeClient(copy.deepcopy(store_nocomm_new)), LUX, months=6, carrier_id=TOTAL_ID)
NEW_KEYS = {"residual_amount_field", "residual_field_warning", "ma_commission_sign"}
check("tab 2: NEW adds exactly 3 read-only keys and removes none",
      set(n_nc) - set(b_nc) == NEW_KEYS and set(b_nc) - set(n_nc) == set())
MONEY_MOVED = {"series", "total_residual", "avg_residual_per_sub", "latest"}
check("tab 2: every key except the residual figures is byte-identical old vs new",
      all(json.dumps(b_nc[k], sort_keys=True, default=str) == json.dumps(n_nc[k], sort_keys=True, default=str)
          for k in set(b_nc) - MONEY_MOVED),
      [k for k in set(b_nc) - MONEY_MOVED
       if json.dumps(b_nc[k], sort_keys=True, default=str) != json.dumps(n_nc[k], sort_keys=True, default=str)])
check("tab 2: months/subs/byod_acts untouched (only the $ column changed)",
      [s["period"] for s in b_nc["series"]] == [s["period"] for s in n_nc["series"]]
      and [s["subs"] for s in b_nc["series"]] == [s["subs"] for s in n_nc["series"]]
      and [s["byod_acts"] for s in b_nc["series"]] == [s["byod_acts"] for s in n_nc["series"]])

# A tenant whose merchant_invoice HAPPENS to hold money → correcting the column is a no-op for it
sane = {"carrier": ma_store()["carrier"], "whatif_source_config": cfg_rows(),
        "raw_ma_daily_tx": [{"org_id": LUX, "period": MAY, "order_type": "Postpaid Residual Order",
                             "account_id": "A1", "order_number": "ON1", "merchant_invoice": -12.34,
                             "merchant_discount": 0, "retail_cost": -12.34}],
        "raw_ma_commission": []}
b_sane = B.byod_residual(FakeClient(copy.deepcopy(sane)), LUX, months=6, carrier_id=TOTAL_ID)
n_sane = W.byod_residual(FakeClient(with_config(sane, field="retail_cost")["whatif_source_config"] and
                                    copy.deepcopy(with_config(sane, field="retail_cost"))),
                         LUX, months=6, carrier_id=TOTAL_ID)
check("where both columns hold the same money, OLD == NEW ($12.34) — the fix breaks nothing already right",
      b_sane["total_residual"] == n_sane["total_residual"] == 12.34,
      (b_sane["total_residual"], n_sane["total_residual"]))


print("=" * 100)
print("C. CONFIG-OVERRIDE PRECEDENCE (RULE TWO) — an explicit choice always wins")
print("=" * 100)
# org-level per-carrier override wins over the new code default
ov = copy.deepcopy(store_new)
ov["whatif_source_config"].append({
    "org_id": LUX, "carrier_id": TOTAL_ID, "carrier_mode": "plan", "is_active": True,
    "residual_source": "ma_daily_tx", "residual_order_type": "Postpaid Residual Order",
    "residual_amount_field": "merchant_discount", "residual_sign": "negate", "income_source": "ma",
    "retail_cost_source": "none"})
r_ov = W._whatif_source_config(FakeClient(copy.deepcopy(ov)), LUX, TOTAL_ID, "plan")
check("org+carrier override keeps merchant_discount (NOT the new retail_cost default)",
      r_ov["residual_amount_field"] == "merchant_discount" and r_ov["_resolved_from"] == "org_carrier")
res_ov = W.byod_residual(FakeClient(copy.deepcopy(ov)), LUX, months=6, carrier_id=TOTAL_ID)
# The override's column is BLANK on these residual rows (merchant_discount only carries airtime margin),
# so the documented per-row fallback fires — and it now lands on a MONEY column (retail_cost), which is
# the whole point: a blank configured column can no longer resolve to the invoice NUMBER.
check("...blank override column → fallback to a MONEY column, never the identifier",
      next(s for s in res_ov["series"] if s["period"] == MAY)["residual"] == COST_SUM_NEGATED,
      next(s for s in res_ov["series"] if s["period"] == MAY))
check("...BASE's fallback on the same override picked the invoice NUMBER instead",
      next(s for s in B.byod_residual(FakeClient(copy.deepcopy(ov)), LUX, months=6,
                                      carrier_id=TOTAL_ID)["series"]
           if s["period"] == MAY)["residual"] == INV_SUM_NEGATED)
check("an org that configured merchant_discount gets NO warning (it is money)",
      res_ov.get("residual_field_warning") is None)

# an org still configured to the identifier column KEEPS it (config is king) but is told, loudly
res_id = W.byod_residual(FakeClient(copy.deepcopy(store_old)), LUX, months=6, carrier_id=TOTAL_ID)
check("configured merchant_invoice is HONORED, not silently overridden",
      next(s for s in res_id["series"] if s["period"] == MAY)["residual"] == INV_SUM_NEGATED)
warn = res_id.get("residual_field_warning") or ""
check("...and the payload now WARNS that it is an invoice NUMBER, not money", bool(warn))
check("...the warning names the offending column and the fix (retail_cost)",
      "merchant_invoice" in warn and "retail_cost" in warn and "NUMBER" in warn, warn)
check("BASE had no such warning (silent garbage)", "residual_field_warning" not in b_res)
check("mode fallback with NO config table at all → retail_cost (graceful, mig 209/252 unrun)",
      W._whatif_source_config(FakeClient({}, absent=["whatif_source_config"]), LUX, None, "plan")
      ["residual_amount_field"] == "retail_cost")
check("...and _resolved_from says code_default",
      W._whatif_source_config(FakeClient({}, absent=["whatif_source_config"]), LUX, None, "plan")
      ["_resolved_from"] == "code_default")


print("=" * 100)
print("D. FALLBACK HEURISTIC REVERSED — cents/negative, never max(|value|), never an identifier")
print("=" * 100)
cfg_blank = {"residual_order_type": "Postpaid Residual Order", "residual_amount_field": "retail_cost",
             "residual_sign": "negate"}
row_blank = {"merchant_invoice": 987654321012, "merchant_discount": -4.37, "retail_cost": None}
check("NEW: blank configured column → the cents/negative money column (4.37)",
      W._ma_residual_amount(row_blank, cfg_blank) == 4.37, W._ma_residual_amount(row_blank, cfg_blank))
cfg_blank_old = dict(cfg_blank, residual_amount_field="merchant_invoice")
check("BASE on the same row picked the 12-digit ID (max |value|)",
      B._ma_residual_amount(row_blank, cfg_blank_old) == -987654321012,
      B._ma_residual_amount(row_blank, cfg_blank_old))
check("BASE's own fallback also picked the ID when its configured column was blank",
      B._ma_residual_amount({"merchant_invoice": None, "merchant_discount": -4.37,
                             "retail_cost": None, }, cfg_blank_old) == 4.37)
check("NEW: an identifier is NEVER picked, even as the only non-empty column → 0",
      W._ma_residual_amount({"merchant_invoice": 987654321012}, cfg_blank) == 0.0)
check("NEW: prefers a cents-bearing value over a larger whole number",
      W._first_ma_money_value({"retail_cost": 900, "merchant_discount": -0.63}) == -0.63)
check("NEW: prefers retail_cost when IT is the money-shaped one",
      W._first_ma_money_value({"retail_cost": -412.37, "merchant_discount": 900}) == -412.37)
check("NEW: no money-shaped value → first non-zero money column",
      W._first_ma_money_value({"retail_cost": 0, "merchant_discount": 25}) == 25)
check("NEW: nothing present at all → 0", W._first_ma_money_value({}) == 0)
check("configured column present and non-zero → fallback never runs",
      W._ma_residual_amount({"retail_cost": -10, "merchant_discount": -999}, cfg_blank) == 10)
check("_normalize_amount unchanged (negate/abs/as_is)",
      (W._normalize_amount(-12.5, "negate"), W._normalize_amount(-3, "abs"), W._normalize_amount(-4, "as_is"))
      == (12.5, 3, -4))


print("=" * 100)
print("E. SIGN CONSISTENCY — COMMISSION / SPIFF vs RESIDUAL (§④.2)")
print("=" * 100)
# BASE code, but with the residual COLUMN already corrected (i.e. exactly what the owner would see after
# running §③ #6 without this package): residual posts POSITIVE while commission/spiff post NEGATIVE.
b_ci = B.carrier_income(FakeClient(copy.deepcopy(store_new)), LUX, months=6, carrier_id=TOTAL_ID)
b_jun = next(t for t in b_ci["totals_by_month"] if t["period"] == JUNE)
check("BASE: June posts NEGATIVE commission (-23) and NEGATIVE spiff (-14)…",
      b_jun["components"]["COMMISSION"] == -23.0 and b_jun["components"]["SPIFF"] == -14.0, b_jun)
check("…while BASE's RESIDUAL on the SAME row is POSITIVE (+100) — the inconsistency, reproduced",
      b_jun["residual_mi_atu"] == 100.0, b_jun)
check("…and BASE's June total_comp is NEGATIVE money the company supposedly did not earn (-37)",
      b_jun["total_comp"] == -37.0, b_jun)

n_ci = W.carrier_income(FakeClient(copy.deepcopy(store_new)), LUX, months=6, carrier_id=TOTAL_ID)
n_jun = next(t for t in n_ci["totals_by_month"] if t["period"] == JUNE)
check("NEW: COMMISSION == +23 (Σ|M1-M6|, normalized to income)", n_jun["components"]["COMMISSION"] == 23.0, n_jun)
check("NEW: SPIFF == +14 (Σ|rebate|)", n_jun["components"]["SPIFF"] == 14.0, n_jun)
check("NEW: RESIDUAL == +100 (retail_cost -100 normalized)", n_jun["residual_mi_atu"] == 100.0, n_jun)
check("NEW: all three money buckets share ONE sign convention (all ≥ 0 for a paid month)",
      min(n_jun["components"]["COMMISSION"], n_jun["components"]["SPIFF"], n_jun["residual_mi_atu"]) > 0)
check("NEW: total_comp = 23+14+airtime(0) = 37 for June", n_jun["total_comp"] == 37.0, n_jun)
check("NEW: airtime margin (UNMAPPED) still read AS-IS, not flipped (matches the owner-sane control group)",
      next(t for t in n_ci["totals_by_month"] if t["period"] == MAY)["components"]["UNMAPPED"] == 4.37)
check("tab 2's BYOD-specific commission is normalized the same way (+25, not −25)",
      n_res["byod_specific"]["byod_residual_month"] == 25.0, n_res.get("byod_specific"))
check("BASE tab 2 reported it NEGATIVE (−25)", b_res["byod_specific"]["byod_residual_month"] == -25.0,
      b_res.get("byod_specific"))
check("sign convention is reported on the payload for the reader",
      n_ci["params"]["ma_commission_sign"] == "negate" and n_res["ma_commission_sign"] == "negate")
check("observed row-sign diagnostic counts the fixture's 2 negative rows",
      n_ci["params"]["commission_row_signs"] == {"negative": 2, "positive": 0, "zero": 0},
      n_ci["params"]["commission_row_signs"])

# CROSS-SURFACE PARITY against the REAL /ma-commission/summary handler (the shipped Total roll-up)
_real_sb = R.sb
try:
    R.sb = lambda: FakeClient(copy.deepcopy(store_new))
    summ = R.ma_commission_summary(period=JUNE, org_id=LUX)
finally:
    R.sb = _real_sb
check("/ma-commission/summary is ready on the same fixture", summ.get("ready") is True, summ.get("note"))
check("whatif COMMISSION == /ma-commission/summary spiffs_total (23) — one convention, two surfaces",
      n_jun["components"]["COMMISSION"] == summ["components"]["spiffs_total"] == 23.0,
      (n_jun["components"]["COMMISSION"], summ["components"]["spiffs_total"]))
check("whatif SPIFF == /ma-commission/summary rebates (14)",
      n_jun["components"]["SPIFF"] == summ["components"]["rebates"] == 14.0,
      (n_jun["components"]["SPIFF"], summ["components"]["rebates"]))
check("BASE disagreed with the shipped roll-up by exactly 2× (sign)",
      b_jun["components"]["COMMISSION"] == -summ["components"]["spiffs_total"])

# config opt-out for a tenant whose export already arrives positive
pos = with_config(ma_store(), field="retail_cost", extra={"ma_commission_sign": "as_is"})
n_pos = W.carrier_income(FakeClient(copy.deepcopy(pos)), LUX, months=6, carrier_id=TOTAL_ID)
p_jun = next(t for t in n_pos["totals_by_month"] if t["period"] == JUNE)
check("ma_commission_sign='as_is' is honored per org/carrier (raw −23 kept)",
      p_jun["components"]["COMMISSION"] == -23.0 and n_pos["params"]["ma_commission_sign"] == "as_is")
check("...proving the convention is CONFIG, not a hard-coded carrier rule",
      W._ma_commission_sign({"ma_commission_sign": "abs"}) == "abs"
      and W._ma_commission_sign({}) == "negate")


print("=" * 100)
print("F. INGEST-COVERAGE FLAG (§④.3) — a thin source, not a stale ledger")
print("=" * 100)
cov = {c["period"]: c for c in n_ci["ma_coverage"]}
check("coverage counts MA Daily Tx rows per month (May 4, June 1)",
      cov[MAY]["daily_tx_rows"] == 4 and cov[JUNE]["daily_tx_rows"] == 1, cov)
check("coverage counts MA Commission Details rows per month (May 0, June 2)",
      cov[MAY]["commission_rows"] == 0 and cov[JUNE]["commission_rows"] == 2, cov)
may_row = next(t for t in n_ci["totals_by_month"] if t["period"] == MAY)
check("May is flagged comp_source_missing (daily-tx rows, no commission rows)",
      may_row["comp_source_missing"] is True and may_row["components"]["COMMISSION"] == 0.0)
check("June is NOT flagged", n_jun["comp_source_missing"] is False)
note = n_ci.get("data_note") or ""
check("data_note exists and names ONLY the gap month", MAY in note and JUNE not in note, note)
check("data_note says DATA GAP, not a calculation error", "DATA GAP" in note and "not a calculation error" in note)
check("data_note answers the coordinator's question in words: NOT a stale ledger",
      "NOT a stale ledger" in note and "Commission Ledger" in note, note)
check("data_note tells the operator exactly what to pull",
      "MA Commission Details" in note and "Data Imports" in note and "12 months" in note, note)
full = with_config(ma_store(), field="retail_cost")
full["raw_ma_commission"].append(dict(full["raw_ma_commission"][0], period=MAY, spiff_m1=-1, rebate=0))
n_full = W.carrier_income(FakeClient(copy.deepcopy(full)), LUX, months=6, carrier_id=TOTAL_ID)
check("no gap → data_note is None (no scare banner when coverage is complete)",
      n_full.get("data_note") is None, n_full.get("data_note"))
check("...and every month reports comp_source_missing False",
      all(t["comp_source_missing"] is False for t in n_full["totals_by_month"]))
empty_ma = with_config({"carrier": ma_store()["carrier"], "raw_ma_daily_tx": [],
                        "raw_ma_commission": []}, field="retail_cost")
n_empty = W.carrier_income(FakeClient(copy.deepcopy(empty_ma)), LUX, months=6, carrier_id=TOTAL_ID)
check("no MA rows at all → the pre-existing 'pull the reports' note, coverage empty, no crash",
      n_empty["ma_coverage"] == [] and n_empty["data_note"] is None and "pull the MA" in (n_empty["note"] or ""))


print("=" * 100)
print("G. BOOST BYTE-IDENTITY — the Boost engine is untouched")
print("=" * 100)
import inspect
for fn in ("_boost_byod_residual", "_byod_specific_residual", "_normalize_amount", "activation_baseline",
           "_boost_actuals", "_rates", "_carrier_ctx", "_pvariants", "_list_periods", "_ma_pkey",
           "accessory_byod_correlation", "_ma_retail_cost"):
    check(f"source of {fn}() byte-identical to base {BASE_REV}",
          inspect.getsource(getattr(W, fn)) == inspect.getsource(getattr(B, fn)))

boost_store = {
    "carrier": [{"id": BOOST_ID, "org_id": LUX, "name": "Boost Mobile", "code": "BOOST", "is_default": True}],
    "whatif_source_config": cfg_rows(),
    "raw_mi": [
        {"org_id": LUX, "period": MAY, "period_year": 2026, "period_month": 5, "salesforce_id": "SF1",
         "phone_number": "5551110001", "actual_mi_payout": 3.10, "actual_atu_payout": 1.20},
        {"org_id": LUX, "period": MAY, "period_year": 2026, "period_month": 5, "salesforce_id": "SF1",
         "phone_number": "5551110002", "actual_mi_payout": 2.00, "actual_atu_payout": 0.50},
        {"org_id": LUX, "period": JUNE, "period_year": 2026, "period_month": 6, "salesforce_id": "SF1",
         "phone_number": "5551110001", "actual_mi_payout": 3.30, "actual_atu_payout": 1.00},
    ],
    "store_mapping": [{"org_id": LUX, "store_address": "100 Main St", "market": "North",
                       "store_code": "S1", "salesforce_id": "SF1", "is_active": True}],
    "rep_commissions": [{"org_id": LUX, "period": MAY, "store": "100 Main St", "total_payout": 500.0,
                         "byod_acts": 7, "premium_acts": 3, "upgrade_acts": 1, "acc_comm": 10.0,
                         "setup_fee_comm": 5.0, "trade_in_comm": 0.0, "acima_comm": 0.0,
                         "subtotal": 480.0, "tier": 1}],
}
b_boost = B.byod_residual(FakeClient(copy.deepcopy(boost_store)), LUX, months=6, carrier_id=BOOST_ID)
READS.clear()
n_boost = W.byod_residual(FakeClient(copy.deepcopy(boost_store)), LUX, months=6, carrier_id=BOOST_ID)
check("boost residual reads raw_mi and NEVER an MA table",
      any(t == "raw_mi" for t, _f in READS) and not any(t.startswith("raw_ma_") for t, _f in READS),
      sorted({t for t, _f in READS}))
check("boost residual actually produced numbers on the fixture (a real, not vacuous, comparison)",
      (b_boost["total_residual"] or 0) > 0 and len(b_boost["series"]) >= 1, b_boost["total_residual"])
check("BOOST tab-2 payload BYTE-IDENTICAL old vs new",
      json.dumps(b_boost, sort_keys=True, default=str) == json.dumps(n_boost, sort_keys=True, default=str),
      [k for k in set(b_boost) | set(n_boost)
       if json.dumps(b_boost.get(k), sort_keys=True, default=str) != json.dumps(n_boost.get(k), sort_keys=True, default=str)])
b_binc = B.carrier_income(FakeClient(copy.deepcopy(boost_store)), LUX, months=6, carrier_id=BOOST_ID)
n_binc = W.carrier_income(FakeClient(copy.deepcopy(boost_store)), LUX, months=6, carrier_id=BOOST_ID)
check("BOOST tab-4 (comp_trend) payload BYTE-IDENTICAL old vs new",
      json.dumps(b_binc, sort_keys=True, default=str) == json.dumps(n_binc, sort_keys=True, default=str))
check("boost tab 4 carries NO MA coverage keys (they belong to the MA leg only)",
      "ma_coverage" not in n_binc and "data_note" not in n_binc)
check("boost income_source unchanged", n_binc["income_source"] == "boost_comp_mi_atu")
b_mix = B.activation_baseline(FakeClient(copy.deepcopy(boost_store)), LUX, MAY, carrier_id=BOOST_ID)
n_mix = W.activation_baseline(FakeClient(copy.deepcopy(boost_store)), LUX, MAY, carrier_id=BOOST_ID)
check("BOOST tab-1 employee-payout template BYTE-IDENTICAL old vs new",
      json.dumps(b_mix, sort_keys=True, default=str) == json.dumps(n_mix, sort_keys=True, default=str))


print("=" * 100)
print("H. ZERO WRITES · ORG SCOPING · CROSS-TENANT ISOLATION · PERIOD SPELLING")
print("=" * 100)
READS.clear()
mt = with_config(ma_store(), field="retail_cost")
mt["raw_ma_daily_tx"] += [dict(r, org_id=OTHER) for r in ma_store(org=OTHER)["raw_ma_daily_tx"]]
mt["raw_ma_commission"] += [dict(r, org_id=OTHER) for r in ma_store(org=OTHER)["raw_ma_commission"]]
mt["carrier"] = mt["carrier"] + [{"id": TOTAL_ID, "org_id": OTHER, "name": "Total by Verizon",
                                  "code": "TOTAL", "is_default": True}]
iso_res = W.byod_residual(FakeClient(copy.deepcopy(mt)), LUX, months=6, carrier_id=TOTAL_ID)
iso_inc = W.carrier_income(FakeClient(copy.deepcopy(mt)), LUX, months=6, carrier_id=TOTAL_ID)
check("a second tenant's IDENTICAL rows do not leak into LUX residual",
      next(s for s in iso_res["series"] if s["period"] == MAY)["residual"] == COST_SUM_NEGATED)
check("...nor into LUX carrier income",
      next(t for t in iso_inc["totals_by_month"] if t["period"] == JUNE)["components"]["COMMISSION"] == 23.0)
o_res = W.byod_residual(FakeClient(copy.deepcopy(mt)), OTHER, months=6, carrier_id=None)
check("the other tenant sees its OWN rows (isolation, not blanking)",
      o_res["total_residual"] == round(COST_SUM_NEGATED + 100.0, 2), o_res["total_residual"])
bad = [(t, f) for t, f in READS
       if not any(k == 'eq' and c == 'org_id' and v in (LUX, OTHER) for k, c, v in f)
       and not (t == 'whatif_source_config' and any(k == 'eq' and c == 'org_id' and v == HOUSE for k, c, v in f))]
check("EVERY read is org-scoped to the caller (the only HOUSE read is mig-209 config inheritance)",
      bad == [], bad[:4])
check("no write was ever attempted (fake client raises on insert/update/upsert/delete)", WRITES == [], WRITES)
# The zero-write guard must be able to FIRE, or "no writes" proves nothing. Trip it deliberately.
_tripped = []
for _op in ("insert", "update", "upsert", "delete"):
    try:
        getattr(FakeQuery({}, "raw_ma_daily_tx", set()), _op)([{}])
    except AssertionError:
        _tripped.append(_op)
check("...and the guard genuinely fires when a write IS attempted (all 4 verbs)",
      _tripped == ["insert", "update", "upsert", "delete"] and len(WRITES) == 4, (_tripped, WRITES))
WRITES.clear()

num_store = with_config(ma_store(period="2026-05"), field="retail_cost")
n_num = W.byod_residual(FakeClient(copy.deepcopy(num_store)), LUX, months=6, carrier_id=TOTAL_ID)
check("period spelling '2026-05' yields the SAME dollars as 'May 2026'",
      next(s for s in n_num["series"] if s["period"] == "2026-05")["residual"] == COST_SUM_NEGATED)
n_num_inc = W.carrier_income(FakeClient(copy.deepcopy(num_store)), LUX, months=6, carrier_id=TOTAL_ID)
check("coverage + gap note work in the numeric spelling too",
      any(c["period"] == "2026-05" and c["commission_rows"] == 0 for c in n_num_inc["ma_coverage"])
      and "2026-05" in (n_num_inc.get("data_note") or ""))
check("months still sort chronologically in both spellings",
      [t["period"] for t in n_num_inc["totals_by_month"]] == ["2026-05", JUNE])


print("=" * 100)
print("I. UI CONTRACT — the real endpoints + the page's keys")
print("=" * 100)
_real_sb = R.sb
try:
    R.sb = lambda: FakeClient(copy.deepcopy(store_old))
    got = R.whatif_get_source_config(carrier_id=TOTAL_ID, org_id=LUX)
finally:
    R.sb = _real_sb
opts = got["options"]
check("options list residual_amount_field with retail_cost FIRST (the recommended value)",
      opts["residual_amount_field"][0] == "retail_cost")
check("merchant_invoice is still selectable (an org may have it saved)",
      "merchant_invoice" in opts["residual_amount_field"])
lbl = got["option_labels"]["residual_amount_field"]["merchant_invoice"]
check("...but it carries the ⚠ invoice NUMBER — not money label", "⚠" in lbl and "not money" in lbl, lbl)
check("ma_commission_sign is offered with negate first",
      opts["ma_commission_sign"] == ["negate", "as_is", "abs"])
check("every offered residual $ column except the labelled identifier is a money column",
      [c for c in opts["residual_amount_field"] if not W.is_ma_money_column(c)] == ["merchant_invoice"])
check("the endpoint still resolves + returns the org's raw rows (shape unchanged)",
      set(got) == {"carrier", "carrier_mode", "carriers", "resolved", "rows", "options", "option_labels"},
      set(got))
check("resolved config is reported to the admin panel as-is (merchant_invoice, warts and all)",
      got["resolved"]["residual_amount_field"] == "merchant_invoice")

# PUT: the new key is in the allowlist (drive the real handler; admin gate stubbed, DB write intercepted)
saved = {}


class PutClient(FakeClient):
    def schema(self, s):
        outer = self

        class _S:
            def table(_self, t):
                class _T:
                    def upsert(_s2, row, **k):
                        saved.update(row)

                        class _E:
                            def execute(_s3):
                                return FakeResult(data=[row])
                        return _E()
                return _T()
        return _S()


_real_gate = R._require_commission_admin
try:
    R._require_commission_admin = lambda *a, **k: None
    R.sb = lambda: PutClient({})
    put = R.whatif_put_source_config({"carrier_id": TOTAL_ID, "carrier_mode": "plan",
                                      "residual_amount_field": "retail_cost",
                                      "ma_commission_sign": "as_is"}, org_id=LUX)
finally:
    R._require_commission_admin = _real_gate
    R.sb = _real_sb
check("PUT saves the corrected residual column", put.get("ok") is True and saved.get("residual_amount_field") == "retail_cost")
check("PUT saves the new ma_commission_sign key (allowlisted)", saved.get("ma_commission_sign") == "as_is")
check("PUT still stamps the CALLER's org_id (never a constant)", saved.get("org_id") == LUX)

page = open(os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'src', 'app',
                         '(platform)', 'commcalc', 'whatif', 'page.tsx')).read()
for key in ("residual_field_warning", "data_note", "comp_source_missing", "option_labels",
            "ma_commission_sign"):
    check(f"page.tsx consumes `{key}`", key in page)
check("page renders the warning banner on the BYOD-residual tab (tab 2)",
      "data.residual_field_warning && <NoteBanner tone=\"warn\"" in page)
check("page renders the warning banner on the carrier-income tab (tab 4)",
      "trend.residual_field_warning && <NoteBanner tone=\"warn\"" in page)
check("page renders the coverage DATA-GAP note (tab 4)",
      "trend.data_note && <NoteBanner tone=\"info\"" in page)
check("payload actually supplies every key the page reads",
      all(k in n_res for k in ("residual_field_warning",))
      and all(k in n_ci for k in ("data_note",))
      and all("comp_source_missing" in t for t in n_ci["totals_by_month"]))


print("=" * 100)
print("J. MIGRATION 252 — real Postgres parse, idempotency, band, code↔SQL agreement")
print("=" * 100)
MIG = os.path.join(_repo, "database", "migrations", "252_commission_whatif_residual_amount_field.sql")
sql = open(MIG).read()
try:
    import pglast
    st = pglast.parse_sql(sql)
    check("pglast (real PostgreSQL parser) parses the migration", len(st) >= 1)
    check("pglast parses the plpgsql body of the DO block", len(pglast.parse_plpgsql(sql)) == 1)
    inner = re.findall(r"\$(?:upd|c1|c2)\$(.*?)\$(?:upd|c1|c2)\$", sql, re.S)
    check("every EXECUTE'd statement parses on its own (%d)" % len(inner),
          len(inner) == 3 and all(pglast.parse_sql(s) for s in inner))
    for lit in ("ALTER TABLE commcalc.whatif_source_config\n             ALTER COLUMN residual_amount_field SET DEFAULT ''retail_cost''",):
        check("the quoted ALTER ... SET DEFAULT parses",
              bool(pglast.parse_sql(lit.replace("''", "'"))))
except ImportError:
    check("pglast available for a real parse", False, "pip install pglast")

check("migration number is inside band 200–299", 200 <= int(os.path.basename(MIG)[:3]) <= 299)
_files = sorted(os.listdir(os.path.dirname(MIG)))
check("exactly ONE 252_* file exists and it is this package's",
      [f for f in _files if f.startswith("252")] == [os.path.basename(MIG)],
      [f for f in _files if f.startswith("252")])
check("this package takes exactly ONE band-200 number (no second migration hidden in it)",
      len([f for f in _files if f.endswith(".sql") and "whatif_residual" in f]) == 1)
# 251 was the concurrent ledger-ma-sync package's number (it shipped to main on 2026-07-30 while this
# was building); taking 252 is what kept the two from colliding.
check("251 belongs to the sibling package, never to this one",
      all("whatif" not in f for f in _files if f.startswith("251")),
      [f for f in _files if f.startswith("251")])
check("additive only: no DROP / DELETE / TRUNCATE / ALTER..DROP",
      not re.search(r"\b(DROP|DELETE\s+FROM|TRUNCATE)\b", sql, re.I))
check("contract §5: no GRANT and no CREATE POLICY",
      not re.search(r"\bGRANT\b|CREATE\s+POLICY", sql, re.I))
sql_code = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
check("contract §5: no anon / authenticated role appears in EXECUTABLE sql (comments may name the rule)",
      not re.search(r"\b(anon|authenticated)\b", sql_code, re.I))
check("idempotent: column add is IF NOT EXISTS", "ADD COLUMN IF NOT EXISTS ma_commission_sign" in sql)
check("guarded: wrapped in a table-exists check so it can't fail before mig 209",
      "information_schema.tables" in sql and "whatif_source_config" in sql)
check("the UPDATE is filtered on the defective value (that IS the idempotency)",
      "WHERE residual_amount_field = 'merchant_invoice'" in sql)
check("the UPDATE sets exactly the value finance §③ #6 sets", "residual_amount_field = 'retail_cost'" in sql)

# Simulate the UPDATE twice over an in-memory table: run 2 must change NOTHING (and the owner having
# run their own statement first must make run 1 a no-op too).
def apply_upd(rows):
    n = 0
    for r in rows:
        if r["residual_amount_field"] == "merchant_invoice":
            r["residual_amount_field"] = "retail_cost"
            r["notes"] = (r.get("notes") or "") + " [mig 252 ...]"
            n += 1
    return n


rows = [{"residual_amount_field": "merchant_invoice", "notes": None},
        {"residual_amount_field": "merchant_invoice", "notes": "seed"},
        {"residual_amount_field": "merchant_discount", "notes": "an org override"}]
r1 = apply_upd(rows)
snap = json.dumps(rows, sort_keys=True)
r2 = apply_upd(rows)
check("run 1 fixes the 2 defective rows and leaves the org override alone", r1 == 2
      and rows[2]["residual_amount_field"] == "merchant_discount")
check("run 2 is a NO-OP (0 rows, byte-identical table) — safe to re-run",
      r2 == 0 and json.dumps(rows, sort_keys=True) == snap)
owner_ran = [{"residual_amount_field": "retail_cost", "notes": "owner ran §③ #6"}]
check("owner already ran their statement → this migration touches 0 rows, appends no duplicate note",
      apply_upd(owner_ran) == 0 and owner_ran[0]["notes"] == "owner ran §③ #6")
check("code default AGREES with the migration + finance §③ #6 (retail_cost)",
      W._CFG_DEFAULTS["plan"]["residual_amount_field"] == "retail_cost"
      and "residual_amount_field = 'retail_cost'" in sql)
check("code default AGREES with the SQL column DEFAULT", "SET DEFAULT ''retail_cost''" in sql)
check("ma_commission_sign default agrees between code and SQL",
      W._CFG_DEFAULTS["plan"]["ma_commission_sign"] == "negate"
      and "ma_commission_sign TEXT NOT NULL DEFAULT ''negate''" in sql)
check("mig 209's own seed is what this corrects (documented in the header)",
      "mig 209" in sql and "-$492,946,277,716" in sql)

os.unlink(_tmp.name)
print()
print("=" * 100)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 100)
sys.exit(1 if FAIL else 0)
