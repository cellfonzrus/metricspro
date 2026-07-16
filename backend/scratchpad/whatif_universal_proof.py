"""Proof harness for agent/commission/whatif-universal — drives the REAL carrier-agnostic What-If
functions (app.modules.commcalc.whatif) + the REAL router gate over an in-memory FakeClient. No DB, no
network. Run: python3 backend/scratchpad/whatif_universal_proof.py  (from the backend dir).

Covers:
  A. Boost baseline BYTE-IDENTICAL — the new template reproduces the exact legacy frontend rows
     (qty, rate, current_comm) + the legacy `actuals`/`rates` keys are still returned.
  B. MA/plan carrier template POPULATES FROM CONFIG (commission plans/rules/tiers), not the Boost defaults.
  C. Empty-state (R1 refusal mirror) when a carrier has no configured pay source.
  D. Residual sign-normalization + the MA daily-tx residual join with MA commission (M1-M6 + rebate).
  E. MA carrier-income build (components from raw_ma_commission + raw_ma_daily_tx) + Boost routes to comp_trend.
  F. Graceful degradation — no mig 209 (whatif_source_config absent) → code defaults; no raw_ma_pr_activation.
  G. The carrier-residual permission gate (the SAME established _can_view/_require pattern).
  H. Source-config resolution precedence (org carrier > org mode-default > house mode-default > code).
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.whatif as W
import app.modules.commcalc.router as R

PASS = 0; FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok  {name}")
    else: FAIL += 1; print(f"FAIL  {name}   {extra}")

HOUSE = "00000000-0000-0000-0000-000000000001"
NIL = "00000000-0000-0000-0000-000000000000"

# ── in-memory fake supabase client (with per-table 'absent' simulation) ────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None): self.data = data or []; self.count = count
class _RpcExec:
    def __init__(self, data): self._d = data
    def execute(self): return FakeResult(data=self._d)
class FakeQuery:
    def __init__(self, store, table, absent):
        self.store = store; self.t = table; self.f = []; self.cnt = False
        self.op = 'select'; self.ins = None; self.rng = None; self.absent = absent
    def select(self, *a, **k):
        if k.get('count') == 'exact': self.cnt = True
        return self
    def eq(self, c, v): self.f.append(('eq', c, v)); return self
    def in_(self, c, v): self.f.append(('in', c, list(v))); return self
    def neq(self, c, v): self.f.append(('neq', c, v)); return self
    def limit(self, n): return self
    def range(self, a, b): self.rng = (a, b); return self
    def order(self, *a, **k): return self
    def delete(self): self.op = 'delete'; return self
    def insert(self, rows): self.op = 'insert'; self.ins = rows if isinstance(rows, list) else [rows]; return self
    def upsert(self, rows, **k): self.op = 'upsert'; self.ins = rows if isinstance(rows, list) else [rows]; return self
    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v: return False
            if k == 'in' and rv not in v: return False
            if k == 'neq' and rv == v: return False
        return True
    def execute(self):
        if self.t in self.absent:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = self.store.setdefault(self.t, [])
        if self.op == 'select':
            m = [r for r in rows if self._m(r)]
            if self.rng: a, b = self.rng; m = m[a:b + 1]
            if self.cnt: return FakeResult(data=m, count=len(m))
            return FakeResult(data=[dict(r) for r in m])
        if self.op == 'delete':
            self.store[self.t] = [r for r in rows if not self._m(r)]; return FakeResult(data=[])
        if self.op in ('insert', 'upsert'):
            for r in self.ins: rows.append(dict(r))
            return FakeResult(data=list(self.ins))
        return FakeResult()
class FakeSchema:
    def __init__(self, store, absent): self.store = store; self.absent = absent
    def table(self, t): return FakeQuery(self.store, t, self.absent)
    def rpc(self, name, params): raise Exception('no such rpc')
class FakeClient:
    def __init__(self, store, absent=None): self.store = store; self.absent = set(absent or [])
    def schema(self, s): return FakeSchema(self.store, self.absent)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── A. Boost baseline BYTE-IDENTICAL ──")
BOOST_ID = "boost-carrier-1"
JUNE = "June 2026"
rates_row = {"org_id": HOUSE, "period": JUNE, "premium_flat": 6, "byod_flat": 4, "byod_extra_spiff": 1,
             "upgrade_flat": 22, "acc_rate": 0.12, "setup_fee_rate": 0.11, "trade_in_spiff": 25, "acima_spiff": 30}
rc1 = {"org_id": HOUSE, "period": JUNE, "premium_acts": 10, "byod_acts": 5, "upgrade_acts": 3,
       "acc_comm": 120.0, "setup_fee_comm": 55.0, "trade_in_comm": 50.0, "acima_comm": 60.0,
       "subtotal": 600.0, "total_payout": 570.0, "tier": 1.0}
rc2 = {"org_id": HOUSE, "period": JUNE, "premium_acts": 4, "byod_acts": 2, "upgrade_acts": 1,
       "acc_comm": 36.0, "setup_fee_comm": 22.0, "trade_in_comm": 25.0, "acima_comm": 30.0,
       "subtotal": 300.0, "total_payout": 285.0, "tier": 0.75}
store_boost = {
    "carrier": [{"id": BOOST_ID, "org_id": HOUSE, "name": "Boost Mobile", "code": "BOOST", "is_default": True}],
    "payout_config": [rates_row],
    "rep_commissions": [rc1, rc2],
    "raw_sales": [{"org_id": HOUSE, "period": JUNE, "period_year": 2026, "period_month": 6}],
    "whatif_source_config": [
        {"org_id": HOUSE, "carrier_id": NIL, "carrier_mode": "boost", "is_active": True,
         "residual_source": "boost_mi_atu", "residual_order_type": None, "residual_amount_field": "merchant_invoice",
         "residual_sign": "as_is", "income_source": "boost_comp_mi_atu", "retail_cost_source": "none"},
        {"org_id": HOUSE, "carrier_id": NIL, "carrier_mode": "plan", "is_active": True,
         "residual_source": "ma_daily_tx", "residual_order_type": "Postpaid Residual Order",
         "residual_amount_field": "merchant_invoice", "residual_sign": "negate", "income_source": "ma",
         "retail_cost_source": "ma_pr_activation"},
    ],
}
cb = FakeClient(store_boost)
out = W.activation_baseline(cb, HOUSE, JUNE, carrier_id=BOOST_ID)
check("boost carrier resolves to mode 'boost'", out["carrier_mode"] == "boost", out["carrier_mode"])
check("template source_kind == boost_rates", out["template"]["source_kind"] == "boost_rates")
check("legacy keys `rates` + `actuals` still present (backward-compat)", "rates" in out and "actuals" in out)

# Reconstruct the LEGACY frontend expectation independently.
_rt = W._rates(cb, HOUSE, JUNE)
_ac = W._boost_actuals(cb, HOUSE, JUNE, _rt)
legacy_rows = {
    "premium": (_ac["premium_acts"], _rt["premium_flat"]),
    "byod": (_ac["byod_acts"], W.safe_float(_rt["byod_flat"]) + W.safe_float(_rt["byod_extra_spiff"])),
    "upgrade": (_ac["upgrade_acts"], _rt["upgrade_flat"]),
    "trade": (_ac["trade_ins"], _rt["trade_in_spiff"]),
    "acima": (_ac["acima_count"], _rt["acima_spiff"]),
    "acc": (_ac["acc_sales"], _rt["acc_rate"]),
    "setup": (_ac["setup_sales"], _rt["setup_fee_rate"]),
}
comps = {c["key"]: c for c in out["template"]["components"]}
ident = True
for k, (q, rt) in legacy_rows.items():
    c = comps.get(k)
    if not c or W.safe_float(c["qty"]) != W.safe_float(q) or W.safe_float(c["rate"]) != W.safe_float(rt) \
       or round(c["current_comm"], 2) != round(W.safe_float(q) * W.safe_float(rt), 2):
        ident = False
check("every Boost component (qty,rate,current$) == legacy frontend rows", ident)
check("actuals.subtotal == Σ rep subtotal (900)", out["actuals"]["subtotal"] == 900.0, out["actuals"]["subtotal"])
check("actuals.total_payout == Σ rep total (855)", out["actuals"]["total_payout"] == 855.0, out["actuals"]["total_payout"])
# rates back-out sanity: acc_sales = acc_comm(156)/acc_rate(0.12) = 1300
check("acc qty backs out of acc_comm/acc_rate (byte-identical math)", comps["acc"]["qty"] == round(156.0 / 0.12, 2), comps["acc"]["qty"])
check("no Boost default leak — premium rate == configured 6 not default 5", comps["premium"]["rate"] == 6)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── B. MA/plan carrier template POPULATES FROM CONFIG ──")
LUX = "lux-org-1"; TOTAL_ID = "total-carrier-1"; PLAN_ID = "plan-1"
store_plan = {
    "carrier": [{"id": TOTAL_ID, "org_id": LUX, "name": "Total by Verizon", "code": "TOTAL", "is_default": True}],
    "commission_plan": [{"id": PLAN_ID, "org_id": LUX, "name": "Total TWP", "carrier_id": TOTAL_ID,
                         "base_tier_metric": "activations", "is_active": True}],
    "commission_rule": [
        {"id": "r1", "org_id": LUX, "plan_id": PLAN_ID, "label": "New activation", "match_field": "contract_type",
         "match_op": "equals", "match_value": "new", "qualifies": True, "payout_kind": "flat_per_unit",
         "amount": 15, "pct": 0, "tiered": False, "sort": 1},
        {"id": "r2", "org_id": LUX, "plan_id": PLAN_ID, "label": "GP share", "match_field": "any",
         "match_op": "equals", "match_value": "", "qualifies": True, "payout_kind": "pct_gp",
         "amount": 0, "pct": 0.1, "tiered": False, "sort": 2},
    ],
    "commission_tier": [{"id": "t1", "org_id": LUX, "plan_id": PLAN_ID, "metric": "activations",
                         "min_count": 10, "multiplier": 1.2, "sort": 1}],
    "commission_plan_assignment": [{"id": "a1", "org_id": LUX, "plan_id": PLAN_ID, "scope": "default",
                                    "scope_value": None, "priority": 0}],
    "raw_sales": [
        {"org_id": LUX, "period": JUNE, "period_year": 2026, "period_month": 6, "salesperson": "ALICE",
         "store": "100 Main", "contract_type": "new", "gp": 50, "ext_price": 200, "voided": "NO", "trans_type": "Sale"},
        {"org_id": LUX, "period": JUNE, "period_year": 2026, "period_month": 6, "salesperson": "ALICE",
         "store": "100 Main", "contract_type": "new", "gp": 30, "ext_price": 150, "voided": "NO", "trans_type": "Sale"},
    ],
    "store_mapping": [{"org_id": LUX, "store_address": "100 Main", "store_code": "100", "market": "NJ"}],
    "whatif_source_config": store_boost["whatif_source_config"],  # only house rows exist
}
cp = FakeClient(store_plan)
outp = W.activation_baseline(cp, LUX, JUNE, carrier_id=TOTAL_ID)
check("non-boost carrier resolves to mode 'plan'", outp["carrier_mode"] == "plan", outp["carrier_mode"])
check("template source_kind == commission_plan", outp["template"]["source_kind"] == "commission_plan")
pcomps = {c["key"]: c for c in outp["template"]["components"]}
check("2 rule components populated from config", len([k for k in pcomps if k.startswith("rule:")]) == 2, list(pcomps))
r1 = pcomps.get("rule:r1"); r2 = pcomps.get("rule:r2")
check("flat rule rate from config (15), NOT Boost default", r1 and r1["rate"] == 15 and r1["kind"] == "flat")
check("flat rule baseline qty backs out to 2 (2 lines × $15 = $30)", r1 and r1["qty"] == 2.0 and r1["current_comm"] == 30.0, r1)
check("pct_gp rule rate from config (0.1), kind pct", r2 and r2["rate"] == 0.1 and r2["kind"] == "pct")
check("pct_gp baseline $ = 0.1×(50+30) = 8", r2 and r2["current_comm"] == 8.0, r2)
check("pct_gp qty backs out to 80 (8/0.1)", r2 and r2["qty"] == 80.0, r2)
check("tier options include the configured ≥10→×1.2 tier", len(outp["template"]["tier"]["options"]) >= 2)
check("NO Boost premium/byod/acc keys leaked into a plan template",
      not any(k in pcomps for k in ("premium", "byod", "upgrade", "acc", "setup")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── C. Empty-state (R1 refusal mirror) when no pay source ──")
store_empty = {
    "carrier": [{"id": "c-x", "org_id": "org-x", "name": "Cricket", "code": "CRK", "is_default": True}],
    "commission_plan": [], "commission_rule": [], "commission_tier": [], "commission_plan_assignment": [],
    "payout_schedule": [], "payout_schedule_line": [],
    "raw_sales": [{"org_id": "org-x", "period": JUNE, "period_year": 2026, "period_month": 6}],
    "whatif_source_config": store_boost["whatif_source_config"],
}
ce = FakeClient(store_empty)
oute = W.activation_baseline(ce, "org-x", JUNE, carrier_id="c-x")
check("no pay source → template.empty True", oute["template"].get("empty") is True)
check("empty reason mentions no plan / pay source", "plan" in (oute["template"].get("reason") or "").lower())
check("empty → configure_url points at /commcalc/commission-plans",
      oute["template"].get("configure_url") == "/commcalc/commission-plans")
check("empty state does NOT silently return Boost $ (no boost components)",
      oute["template"]["source_kind"] == "empty" and oute["actuals"]["total_payout"] == 0.0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── D. Residual sign-normalization + MA daily-tx ↔ MA commission join ──")
check("_normalize_amount negate(-12.5) == 12.5", W._normalize_amount(-12.5, "negate") == 12.5)
check("_normalize_amount abs(-3) == 3", W._normalize_amount(-3, "abs") == 3)
check("_normalize_amount as_is(-4) == -4", W._normalize_amount(-4, "as_is") == -4)
cfg_ma = {"residual_order_type": "Postpaid Residual Order", "residual_amount_field": "merchant_invoice",
          "residual_sign": "negate", "retail_cost_source": "none"}
check("_ma_residual_amount uses configured field + negates (-20 → 20)",
      W._ma_residual_amount({"merchant_invoice": -20}, cfg_ma) == 20)
check("_ma_residual_amount falls back to largest-|x| when configured field empty (-8 → 8)",
      W._ma_residual_amount({"merchant_invoice": None, "merchant_discount": -8, "retail_cost": 2}, cfg_ma) == 8)

store_res = {
    "carrier": [{"id": TOTAL_ID, "org_id": LUX, "name": "Total by Verizon", "code": "TOTAL", "is_default": True}],
    "whatif_source_config": store_boost["whatif_source_config"],
    "raw_ma_daily_tx": [
        {"org_id": LUX, "period": JUNE, "order_type": "Postpaid Residual Order", "account_id": "A1",
         "merchant_invoice": -100.0, "merchant_discount": 0, "retail_cost": 0},
        {"org_id": LUX, "period": JUNE, "order_type": "Postpaid Residual Order", "account_id": "A2",
         "merchant_invoice": -50.0, "merchant_discount": 0, "retail_cost": 0},
        {"org_id": LUX, "period": JUNE, "order_type": "Airtime Topup", "account_id": "A1",
         "merchant_invoice": 0, "merchant_discount": 5.0, "retail_cost": 0},  # NOT a residual row
    ],
    "raw_ma_commission": [
        {"org_id": LUX, "period": JUNE, "activation_type2": "byop", "imei": "111",
         "spiff_m1": 5, "spiff_m2": 5, "spiff_m3": 5, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0, "rebate": 10},
        {"org_id": LUX, "period": JUNE, "activation_type2": "branded", "imei": "222",
         "spiff_m1": 8, "spiff_m2": 0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0, "rebate": 4},
    ],
}
cr = FakeClient(store_res)
outr = W.byod_residual(cr, LUX, months=6, carrier_id=TOTAL_ID)
check("byod_residual dispatch → residual_source ma_daily_tx (from house plan default)",
      outr["residual_source"] == "ma_daily_tx", outr["residual_source"])
check("MA residual = |−100|+|−50| = 150 (sign-normalized to income), airtime row EXCLUDED",
      outr["total_residual"] == 150.0, outr["total_residual"])
check("MA residual subs = 2 distinct accounts", outr["total_subs"] == 2, outr["total_subs"])
check("byod_specific present (BYOD M1-M6 + rebate)", outr["byod_specific"] is not None)
check("BYOD income = 15 (M1-M3) + 10 rebate = 25", outr["byod_specific"]["byod_residual_month"] == 25.0,
      outr["byod_specific"])
check("byod_acts per period counted (1 byop)", (outr["series"][0]["byod_acts"] if outr["series"] else None) == 1)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── E. Carrier income — MA build + Boost routes to comp_trend ──")
ci = W.carrier_income(cr, LUX, months=6, carrier_id=TOTAL_ID)
check("carrier_income MA → income_source 'ma'", ci["income_source"] == "ma")
tbm = {t["period"]: t for t in ci["totals_by_month"]}
june = tbm.get(JUNE, {})
comp = june.get("components", {})
check("MA COMMISSION == Σ M1-M6 (15 + 8 = 23)", comp.get("COMMISSION") == 23.0, comp)
check("MA SPIFF == Σ rebate (10 + 4 = 14)", comp.get("SPIFF") == 14.0, comp)
check("MA residual_mi_atu == 150 (normalized residual orders)", june.get("residual_mi_atu") == 150.0, june)
check("MA UNMAPPED == airtime margin (5)", comp.get("UNMAPPED") == 5.0, comp)
check("MA total_comp == 23+14+5 = 42", june.get("total_comp") == 42.0, june)
# Boost income routes to comp_trend (empty comp → shape preserved, income_source boost)
cb2 = FakeClient({"carrier": store_boost["carrier"], "whatif_source_config": store_boost["whatif_source_config"]})
cib = W.carrier_income(cb2, HOUSE, months=6, carrier_id=BOOST_ID)
check("Boost carrier_income → income_source boost_comp_mi_atu", cib["income_source"] == "boost_comp_mi_atu")
check("Boost carrier_income returns comp_trend shape (totals_by_month)", "totals_by_month" in cib)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── F. Graceful degradation ──")
# no mig 209: whatif_source_config table absent → code default per mode
c_no209 = FakeClient({"carrier": [{"id": TOTAL_ID, "org_id": LUX, "name": "Total", "code": "TOTAL", "is_default": True}]},
                     absent={"whatif_source_config"})
cfg = W._whatif_source_config(c_no209, LUX, TOTAL_ID, "plan")
check("no mig 209 → plan code default (ma_daily_tx)", cfg["residual_source"] == "ma_daily_tx", cfg)
check("no mig 209 → resolved_from code_default", cfg.get("_resolved_from") == "code_default")
cfgb = W._whatif_source_config(c_no209, HOUSE, BOOST_ID, "boost")
check("no mig 209 → boost code default (boost_mi_atu)", cfgb["residual_source"] == "boost_mi_atu")
# no raw_ma_pr_activation table → retail cost None (mig 207 parked)
c_nopr = FakeClient(dict(store_res), absent={"raw_ma_pr_activation"})
rc_out = W._ma_retail_cost(c_nopr, LUX, {"retail_cost_source": "ma_pr_activation"})
check("no raw_ma_pr_activation table → retail cost None (mig 207 parked, graceful)", rc_out is None)
# byod_residual still works fully without the pr_activation table
outr2 = W.byod_residual(c_nopr, LUX, months=6, carrier_id=TOTAL_ID)
check("byod_residual works without raw_ma_pr_activation (residual still 150)", outr2["total_residual"] == 150.0)
# retail_cost_source 'none' → None even when table present
check("retail_cost_source none → None", W._ma_retail_cost(cr, LUX, {"retail_cost_source": "none"}) is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── G. Carrier-residual permission gate (established pattern) ──")
import app.modules.core.router as CORE
_orig_uid = getattr(CORE, "_uid_from_token", None)
_orig_res = getattr(CORE, "_resolve_caller", None)
def _mk_gate_store(vis, caller):
    store = {"commission_org_config": [{"org_id": HOUSE, "residual_visibility": vis, "pay_disabled": False}]}
    fc = FakeClient(store)
    R.sb = lambda: fc
    CORE._uid_from_token = lambda a: ("uid" if a else None)
    CORE._resolve_caller = lambda c, u: caller
    return fc
_orig_sb = R.sb
try:
    _mk_gate_store("all", None)
    check("residual_visibility 'all' → always visible (byte-identical to today)",
          R._can_view_carrier_residual("", HOUSE) is True)
    _mk_gate_store("permissioned", {"super_admin": False, "perms": {"scope": "store", "modules": []}, "role": "rep"})
    check("permissioned + non-admin, no grant → hidden",
          R._can_view_carrier_residual("tok", HOUSE) is False)
    raised = False
    try:
        R._require_carrier_residual("tok", HOUSE)
    except Exception:
        raised = True
    check("_require_carrier_residual raises 403 for a blocked caller", raised)
    _mk_gate_store("permissioned", {"super_admin": True, "perms": {}, "role": "admin"})
    check("permissioned + super_admin → visible", R._can_view_carrier_residual("tok", HOUSE) is True)
    _mk_gate_store("permissioned", {"super_admin": False, "perms": {"scope": "store", "data": {"carrier_residual": True}}})
    check("permissioned + data.carrier_residual grant → visible",
          R._can_view_carrier_residual("tok", HOUSE) is True)
finally:
    R.sb = _orig_sb
    if _orig_uid: CORE._uid_from_token = _orig_uid
    if _orig_res: CORE._resolve_caller = _orig_res


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── H. Source-config resolution precedence ──")
# org override for the exact carrier wins over the house mode-default
store_h = {"whatif_source_config": list(store_boost["whatif_source_config"]) + [
    {"org_id": LUX, "carrier_id": TOTAL_ID, "carrier_mode": "plan", "is_active": True,
     "residual_source": "ma_daily_tx", "residual_order_type": "Custom Residual",
     "residual_amount_field": "merchant_discount", "residual_sign": "negate",
     "income_source": "ma", "retail_cost_source": "ma_pr_activation"},
]}
ch = FakeClient(store_h)
r_org = W._whatif_source_config(ch, LUX, TOTAL_ID, "plan")
check("org carrier override wins (resolved_from org_carrier)", r_org.get("_resolved_from") == "org_carrier")
check("org override applies its residual_amount_field (merchant_discount)",
      r_org["residual_amount_field"] == "merchant_discount")
check("org override applies its order type (Custom Residual)", r_org["residual_order_type"] == "Custom Residual")
# a DIFFERENT carrier (no org row) falls back to the HOUSE plan default
r_house = W._whatif_source_config(ch, LUX, "some-other-carrier", "plan")
check("other carrier → house_mode_default fallback", r_house.get("_resolved_from") == "house_mode_default")
check("house plan default order type == 'Postpaid Residual Order'",
      r_house["residual_order_type"] == "Postpaid Residual Order")
# house org itself resolves its own nil-carrier boost row (no cross-tenant read needed)
r_house_boost = W._whatif_source_config(ch, HOUSE, BOOST_ID, "boost")
check("house boost → org_mode_default (its own seed)", r_house_boost.get("_resolved_from") == "org_mode_default")
check("house boost residual_source == boost_mi_atu", r_house_boost["residual_source"] == "boost_mi_atu")


print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
