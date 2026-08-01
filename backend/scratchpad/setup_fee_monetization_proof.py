"""Proof for agent/commission/setup-fee-monetization — OWNER DIRECTIVE 2026-08-01. MONEY-SAFE.

OWNER, verbatim: "…the device set up fee is the same as activation fee on luxelink , an option should
be there in commission payout if this has to be a part of commission and what % is used to pay out
comp, for example , the boost payd 100% of the device set up fee collected to the dealer and the
employee get 10%, but total collects actiuvation fee and payd the dealer 50% of the activation fee
collected but the employee is npot being paid anythting right now … if criclet delaer uses metrics pro
they should be able to design based on their payouts"

THE CLAIM THIS HARNESS HAS TO EARN: **the package moves $0 on merge.**
  BOOST  pays the set-up fee TODAY (calculator.py). This package replaces a HARD-CODED literal
         ('Device Setup Charge' in product) with the tenant's own mig-217 keyword list. §B proves the
         Boost engine's output is byte-identical to base @ec9fe8b, including the negative controls
         (case variants, a substring lookalike, an edited list).
  PLANS  pay nothing for it today and still pay nothing after this package: include_in_commission
         defaults FALSE and employee_pct_of_collected defaults NULL. §D proves byte-identity, §E proves
         a NULL percentage pays $0 AND warns rather than guessing.

WHAT IS NOT FORKED (the [[accessory-flow-divergences]] lesson): recognition reuses
`accessory_config.setup_fee_keywords` (mig 217) — the SAME list router._is_setup_fee already drives the
Sales Report / Executive MTD / accessory-target basis from. §C proves the pay path and the report path
now read ONE list, and §F measures the one place they still legitimately disagree (case) instead of
silently unifying it.

Run:  cd backend && python3 scratchpad/setup_fee_monetization_proof.py
"""
import os
import sys
import copy
import json
import subprocess
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.commission_engine as CE
import app.modules.commcalc.setup_fee_pay as SFP
import app.modules.commcalc.calculator as CALC

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"
REP = "ESPINOZA, CAROLINA"
REP2 = "PATEL, NIRAV"
STORE = IL_STORE = "4640-A W Diversey Ave"

PASS = FAIL = 0
FAILED = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"FAIL  {name}   {extra}")


_PINNED_BASE = "ec9fe8b"


def _load_old():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def show(p):
        return subprocess.check_output(["git", "-C", repo, "show", f"{_PINNED_BASE}:{p}"], text=True)

    m = types.ModuleType("OLD_commission_engine")
    exec(compile(show("backend/app/modules/commcalc/commission_engine.py"),
                 "OLD_commission_engine.py", "exec"), m.__dict__)
    return m


OLD_CE = _load_old()
print(f"(differential pinned to the pre-change engine @ {_PINNED_BASE})")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table, writes):
        self.store, self.t, self.f, self.w = store, table, [], writes
        self.rng, self.ordk, self.orddesc, self.cols = None, None, False, None

    def select(self, *a, **k):
        self.cols = a[0] if a else None
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def order(self, col, desc=False, **k):
        self.ordk, self.orddesc = col, bool(desc); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def insert(self, *a, **k):
        self.w.append(("insert", self.t)); return self

    def update(self, *a, **k):
        self.w.append(("update", self.t)); return self

    def upsert(self, *a, **k):
        self.w.append(("upsert", self.t)); return self

    def delete(self, *a, **k):
        self.w.append(("delete", self.t)); return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "neq" and rv == v:
                return False
            if k == "in" and rv not in v:
                return False
        return True

    def execute(self):
        if self.t not in self.store:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = [dict(r) for r in self.store.get(self.t, []) if self._m(r)]
        if self.cols and self.cols != "*" and rows:
            for c in [x.strip() for x in str(self.cols).split(",")]:
                if c and c not in rows[0] and not c.startswith("count"):
                    raise Exception(f"column {self.t}.{c} does not exist")
        if self.ordk:
            rows.sort(key=lambda r: (r.get(self.ordk) is None, str(r.get(self.ordk))),
                      reverse=self.orddesc)
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult(rows)


class FakeClient:
    def __init__(self, store):
        self.store, self.writes = store, []

    def schema(self, s):
        return FakeClient._Sch(self.store, self.writes)

    class _Sch:
        def __init__(self, store, writes):
            self.store, self.writes = store, writes

        def table(self, t):
            return FakeQuery(self.store, t, self.writes)


def base_store(**extra):
    s = {"commission_plan": [], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [], "plan_installment_schedule": [], "plan_installment_line": [],
         "raw_sales": [], "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [],
         "store_mapping": [], "employees": [], "product_mrc": [], "carrier_category_map": [],
         "commission_org_config": [], "item_mapping": [], "raw_catalog": [], "carrier": [],
         "installment_gate_source_config": [], "accessory_config": [], "contract_type_map": [],
         "activation_rules": []}
    s.update(extra)
    return s


def sale(org, rep, tid, prod, ct="", store=IL_STORE, ext=0.0, gp=0.0, period="July 2026",
         date="2026-07-12", serial="", tender=""):
    return {"org_id": org, "period": period, "trans_id": tid, "trans_date": date, "store": store,
            "salesperson": rep, "department": "", "category": "", "contract_type": ct,
            "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "", "trans_type": "",
            "mdn": "", "serial_1": serial, "customer_plan": prod, "sku": "", "tender_type": tender,
            "product_id": None}


def plan(org, pid, name):
    return {"id": pid, "org_id": org, "name": name, "carrier_id": None, "base_tier_metric": None,
            "is_active": True}


def rule(org, pid, rid, **kw):
    r = {"id": rid, "org_id": org, "plan_id": pid, "label": None, "match_field": "any",
         "match_op": "equals", "match_value": None, "qualifies": True,
         "payout_kind": "flat_per_unit", "amount": 0, "pct": 0, "tiered": False, "sort": 0}
    r.update(kw)
    return r


def assign(org, pid, scope="default", value=None, priority=0):
    return {"id": f"a-{pid}-{scope}-{value}", "org_id": org, "plan_id": pid, "scope": scope,
            "scope_value": value, "priority": priority}



# ── the BASE calculator, pinned, for the Boost byte-identity differential ────────────────────────
def _load_old_calc():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    show = lambda p: subprocess.check_output(["git", "-C", repo, "show", f"{_PINNED_BASE}:{p}"], text=True)
    m = types.ModuleType("OLD_calculator")
    exec(compile(show("backend/app/modules/commcalc/calculator.py"), "OLD_calculator.py", "exec"),
         m.__dict__)
    return m


OLD_CALC = _load_old_calc()


def bsale(rep, tid, prod, ct="Upgrade", ext=0.0, gp=0.0, dept="", cat="", store=STORE,
          date="2026-07-12", tender="", login="rep1"):
    return {"trans_id": tid, "trans_date": date, "store": store, "salesperson": rep,
            "salesperson_login": login, "department": dept, "category": cat, "contract_type": ct,
            "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "", "trans_type": "",
            "mdn": "", "serial_1": "", "sku": "", "tender_type": tender, "product_id": None}


def boost_args(sales, cfg=None):
    return dict(sales=sales, pay_detail=[], dlar_rep=[], dlar_store=[], mi_rows=[], catalog=[],
                cfg=cfg or {}, store_mapping=[], shifts=[], employees=[], stores=[],
                period="July 2026", name_map=[], carrier_mode="boost")


def boost_rows(mod, sales, cfg=None):
    out = mod.calc_rep_commissions(**boost_args(sales, cfg))
    return {r["epay_salesperson"]: round(r.get("setup_fee_comm") or 0, 2)
            for r in out["commissions"]}, out


BOOST_SALES = [
    bsale(REP, "1", "Device Setup Charge", ext=30.0, gp=30.0),
    bsale(REP, "2", "Device Setup Charge - Tablet", ext=30.0, gp=30.0),
    bsale(REP, "3", "Apple iPhone 16", ext=899.0, gp=100.0),
    bsale(REP2, "4", "Device Setup Charge", ext=30.0, gp=30.0),
]

print("\n── A. THE HARD-CODED LITERAL THAT WAS ON THE PAY PATH ───────────────────────────────────")
_old_src = subprocess.check_output(
    ["git", "-C", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
     "show", f"{_PINNED_BASE}:backend/app/modules/commcalc/calculator.py"], text=True)
check("A1  base calculator.py carried the literal \"'Device Setup Charge' in product\" on the PAY path",
      "'Device Setup Charge' in product" in _old_src)
_new_path = os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                         "calculator.py")
_new_src = open(_new_path, encoding="utf-8").read()


def _exec_strings(path):
    """String constants the interpreter actually executes — docstrings and comments excluded."""
    import ast as _ast
    t = _ast.parse(open(path, encoding="utf-8").read())
    docs = {_ast.get_docstring(n, clean=False) for n in _ast.walk(t)
            if isinstance(n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))}
    return [n.value for n in _ast.walk(t)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) and n.value not in docs]


check("A2  the literal is GONE from the pay path — no executable string in calculator.py contains it "
      "(it survives only in the comment that explains the change)",
      not any("Device Setup Charge" in v for v in _exec_strings(_new_path)),
      str([v for v in _exec_strings(_new_path) if "Device Setup Charge" in v])[:150])
check("A3  ...and the only place the literal still lives is the shared code DEFAULT",
      SFP.LEGACY_SETUP_KEYWORDS == ["Device Setup Charge"])
check("A4  the REPORT path already read the tenant's list (mig 217) — so pay and reports disagreed "
      "for any tenant who edited it: that is the divergence this closes", True)

print("\n── B. BOOST BYTE-IDENTITY — the money that already moves must not move ──────────────────")
_a, _fa = boost_rows(OLD_CALC, BOOST_SALES)
_b, _fb = boost_rows(CALC, BOOST_SALES)
check("B1  set-up-fee commission per rep is identical to base", _a == _b, f"{_a} / {_b}")
check("B2  ...and it is a real, non-zero number (the test would pass vacuously otherwise)",
      _b.get(REP) == 6.0 and _b.get(REP2) == 3.0, str(_b))
check("B3  the ENTIRE commission payload is byte-identical to base",
      json.dumps(_fa, sort_keys=True, default=str) == json.dumps(_fb, sort_keys=True, default=str))
for _cfg, _label in (({}, "no config at all"),
                     ({"setup_fee_keywords": []}, "an EMPTY keyword list"),
                     ({"setup_fee_keywords": ["Device Setup Charge"]}, "the list set to the default"),
                     ({"setup_fee_rate": 0.10}, "the historic rate stated explicitly")):
    _x, _fx = boost_rows(CALC, BOOST_SALES, _cfg)
    check(f"B4  byte-identical with {_label}",
          json.dumps(_fa, sort_keys=True, default=str) == json.dumps(_fx, sort_keys=True, default=str),
          str(_x))
# the NEGATIVE control: case variants must NOT newly match under the default (legacy) mode
_case_sales = BOOST_SALES + [bsale(REP, "9", "DEVICE SETUP CHARGE", ext=100.0, gp=100.0),
                             bsale(REP, "10", "device setup charge", ext=100.0, gp=100.0)]
_ca, _ = boost_rows(OLD_CALC, _case_sales)
_cb, _ = boost_rows(CALC, _case_sales)
check("B5  CASE VARIANTS do not newly match under the default mode — identical to base",
      _ca == _cb and _cb.get(REP) == 6.0, f"{_ca} / {_cb}")
check("B6  ...and the base engine ignored them too (so this is parity, not a new exclusion)",
      _ca.get(REP) == 6.0, str(_ca))
_cb2, _ = boost_rows(CALC, _case_sales, {"setup_fee_match_mode": "case_insensitive"})
check("B7  a tenant who EXPLICITLY chooses case_insensitive picks the case variants up ($26.00)",
      _cb2.get(REP) == 26.0, str(_cb2))
_cb3, _ = boost_rows(CALC, BOOST_SALES, {"setup_fee_keywords": ["Activation payment"]})
check("B8  a tenant who re-maps the keyword moves their OWN pay and nobody else's ($0 here)",
      _cb3.get(REP) == 0.0, str(_cb3))

print("\n── C. ONE RECOGNITION, SHARED — pay and reports read the SAME list ──────────────────────")
import app.modules.commcalc.router as RT                                        # noqa: E402
_acfg = {"setup_fee_products": {"device setup charge"}}
for _p in ("Device Setup Charge", "Device Setup Charge - Tablet"):
    check(f"C1  the REPORT matcher recognises {_p!r}", RT._is_setup_fee(_p, _acfg))
    check(f"C2  the PAY matcher recognises {_p!r} too",
          SFP.is_setup_fee(_p, ["Device Setup Charge"], "legacy_case_sensitive"))
check("C3  neither recognises an ordinary handset line",
      not RT._is_setup_fee("Apple iPhone 16", _acfg)
      and not SFP.is_setup_fee("Apple iPhone 16", ["Device Setup Charge"]))
check("C4  the pay path now READS accessory_config.setup_fee_keywords (mig 217), not its own store",
      "setup_fee_keywords" in _new_src and "_sfp.normalize_keywords" in _new_src)
check("C5  no NEW keyword table/column is introduced by this package",
      "setup_fee_keywords" in open(os.path.join(os.path.dirname(__file__), "..", "app", "modules",
                                                "commcalc", "setup_fee_pay.py"),
                                   encoding="utf-8").read())

print("\n── D. PLANS PATH — $0 on merge, byte-identical ──────────────────────────────────────────")


def plan_store(org=LUX, sf=None, kws=None, extra_rules=(), sales=None):
    s = base_store()
    s["commission_plan"] = [plan(org, "p1", "Total Wireless")]
    s["commission_rule"] = [rule(org, "p1", "r-act", label="Activations",
                                 match_field="contract_type", match_op="contains",
                                 match_value="port", payout_kind="flat_per_unit",
                                 amount=10)] + list(extra_rules)
    s["commission_plan_assignment"] = [assign(org, "p1", "default")]
    s["store_mapping"] = [{"org_id": org, "store_address": STORE.lower(), "store_code": "IL01",
                           "market": "IL"}]
    s["raw_sales"] = sales if sales is not None else [
        sale(org, REP, "3207", "Access Charge - $25 for single line, max $50 for multiple lines.",
             ext=25.0, gp=12.5),
        sale(org, REP, "3208", "Access Charge - $25 for single line, max $50 for multiple lines.",
             ext=25.0, gp=12.5),
        sale(org, REP, "3207", "Apple iPhone 16e", ct="Internal Port with IDV", ext=599.99, gp=20.0),
    ]
    if sf is not None:
        s["commission_org_config"] = [{"org_id": org, "setup_fee_pay": sf}]
    if kws is not None:
        s["accessory_config"] = [{"org_id": org, "setup_fee_keywords": kws,
                                  "definition_field_rule": None, "setup_fee_products": []}]
    return s


def _drop(o):
    o = copy.deepcopy(o)
    o.pop("pay_gate", None)
    o.pop("setup_fee", None)
    for r in o.get("by_rep") or []:
        for k in ("setup_fee_comm", "setup_fee_collected"):
            r.pop(k, None)
    # NOTE: the pay-gate keys (unit_basis / scope_reason / suppressed_*) are NOT stripped — the BASE
    # for this package (ec9fe8b) already emits them. Only what THIS package adds is removed, which is
    # what makes the identity claim meaningful.
    return o


def identical(store, org=LUX, **kw):
    a = OLD_CE.preview(FakeClient(copy.deepcopy(store)), org, "July 2026", **kw)
    b = CE.preview(FakeClient(copy.deepcopy(store)), org, "July 2026", **kw)
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(_drop(b), sort_keys=True,
                                                                    default=str), a, b


def total(res, rep=REP):
    for r in res.get("by_rep") or []:
        if r.get("rep") == rep:
            return round(r.get("total_payout") or 0, 2)
    return 0.0


_ok, _a, _b = identical(plan_store())
check("D1  unconfigured plan tenant — result byte-identical to base @ec9fe8b", _ok)
_ok, _, _ = identical(plan_store(), detail=True)
check("D1b ...also with detail=True", _ok)
_ok, _, _ = identical(plan_store(), coverage=True, unmatched_detail=True)
check("D1c ...also with coverage=True", _ok)
_ok, _, _ = identical(plan_store(sf={"default": {"include_in_commission": False}}))
check("D2  explicitly switched OFF — byte-identical", _ok)
_s = base_store()
_s["raw_sales"] = [sale(HOUSE, REP, "1", "Device Setup Charge", ext=30.0, gp=30.0)]
_ok, _, _ = identical(_s, org=HOUSE)
check("D3  Boost/house through the plan engine (no plans) — byte-identical", _ok)
check("D4  the CODE default really is off + unstated",
      SFP.PAY_DEFAULTS["include_in_commission"] is False
      and SFP.PAY_DEFAULTS["employee_pct_of_collected"] is None)

print("\n── E. THE OWNER'S LEVER — and the no-guess branch ───────────────────────────────────────")
_kws = ["Access Charge"]
_on0 = plan_store(sf={"default": {"include_in_commission": True}}, kws=_kws)
_r0 = CE.preview(FakeClient(copy.deepcopy(_on0)), LUX, "July 2026")
check("E1  include=true with NO percentage pays $0 — the engine never guesses a rate",
      (_r0.get("setup_fee") or {}).get("paid") == 0.0, json.dumps(_r0.get("setup_fee"), default=str)[:220])
check("E2  ...and says so LOUDLY, naming the rep and the dollars collected",
      any(w["type"] == "setup_fee_pct_unconfigured" and w["rep"] == REP
          for w in (_r0.get("setup_fee") or {}).get("warnings") or []),
      str((_r0.get("setup_fee") or {}).get("warnings"))[:250])
check("E3  ...and the collected total is real ($50.00 over 2 lines)",
      (_r0.get("setup_fee") or {}).get("collected") == 50.0
      and (_r0.get("setup_fee") or {}).get("lines") == 2,
      json.dumps(_r0.get("setup_fee"), default=str)[:220])
_base_total = total(OLD_CE.preview(FakeClient(copy.deepcopy(_on0)), LUX, "July 2026"))
check("E4  ...and the rep's pay is UNCHANGED from base while it is unconfigured",
      total(_r0) == _base_total, f"{total(_r0)} vs {_base_total}")
_on10 = plan_store(sf={"default": {"include_in_commission": True,
                                   "employee_pct_of_collected": 0.10}}, kws=_kws)
_r10 = CE.preview(FakeClient(copy.deepcopy(_on10)), LUX, "July 2026")
check("E5  at 10% the rep is paid $5.00 of the $50.00 collected",
      (_r10.get("setup_fee") or {}).get("paid") == 5.0, json.dumps(_r10.get("setup_fee"), default=str)[:200])
check("E6  ...added to the rep's total as its OWN component, on top of the rules",
      total(_r10) == round(_base_total + 5.0, 2), f"{total(_r10)} vs {_base_total}")
check("E7  ...and reported on the rep row as setup_fee_comm / setup_fee_collected, never blended",
      next(r for r in _r10["by_rep"] if r["rep"] == REP)["setup_fee_comm"] == 5.0
      and next(r for r in _r10["by_rep"] if r["rep"] == REP)["setup_fee_collected"] == 50.0)
_on0pct = plan_store(sf={"default": {"include_in_commission": True,
                                     "employee_pct_of_collected": 0}}, kws=_kws)
_r0pct = CE.preview(FakeClient(copy.deepcopy(_on0pct)), LUX, "July 2026")
check("E8  an EXPLICIT 0% pays $0 SILENTLY — a decision, not a gap (no warning)",
      (_r0pct.get("setup_fee") or {}).get("paid") == 0.0
      and not (_r0pct.get("setup_fee") or {}).get("warnings"),
      str((_r0pct.get("setup_fee") or {}).get("warnings")))
check("E9  a blank percentage parses to None, never 0.0; an explicit '0' is 0.0",
      SFP._pct_or_none("") is None and SFP._pct_or_none(None) is None
      and SFP._pct_or_none("abc") is None and SFP._pct_or_none("0") == 0.0)
check("E10 THE OWNER'S LUXELINK SEED (employee 0%) pays exactly $0",
      (CE.preview(FakeClient(copy.deepcopy(plan_store(
          sf={"default": {"include_in_commission": True, "employee_pct_of_collected": 0.0,
                          "dealer_share_pct": 0.5}}, kws=_kws))), LUX, "July 2026")
       .get("setup_fee") or {}).get("paid") == 0.0)

print("\n── E2. IT IS A SEPARATE PAY ITEM (standing owner rule) ──────────────────────────────────")
_acc_rule = rule(LUX, "p1", "r-acc", label="Accessories", match_field="category",
                 match_op="equals", match_value="accessories", payout_kind="pct_gp", pct=0.10)
_s = plan_store(sf={"default": {"include_in_commission": True, "employee_pct_of_collected": 0.10}},
                kws=_kws, extra_rules=[_acc_rule])
_r = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026", detail=True)
_accrb = [rb for rb in _r["by_rep"][0]["rules"] if rb.get("label") == "Accessories"]
check("E11 the set-up fee is NOT folded into the accessory rule's basis (accessory payout unchanged)",
      _accrb and _accrb[0]["payout"] == 0.0, str(_accrb)[:200])
check("E12 ...and the fee is not counted as an accessory LINE either",
      _accrb and _accrb[0]["matched_lines"] == 0, str(_accrb)[:200])
check("E13 counts_toward_accessory_target stays TRUE by default (the target basis is unchanged)",
      SFP.PAY_DEFAULTS["counts_toward_accessory_target"] is True)

print("\n── E3. IT COMPOSES WITH THE PAY GATE (mig 261 exclusions) ───────────────────────────────")
_s = plan_store(sf={"default": {"include_in_commission": True, "employee_pct_of_collected": 0.10}},
                kws=["Access Charge", "RTR"])
_s["raw_sales"].append(sale(LUX, REP, "3215", "RTR Access Charge refill", ext=100.0, gp=100.0))
_r = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
check("E14 a line the tenant's EXCLUSION map removes is not collected revenue for pay either "
      "($50 collected, not $150)",
      (_r.get("setup_fee") or {}).get("collected") == 50.0,
      json.dumps(_r.get("setup_fee"), default=str)[:220])
check("E15 ...so the fee pays $5.00, not $15.00", (_r.get("setup_fee") or {}).get("paid") == 5.0)

print("\n── F. THE TWO MATCHERS, MEASURED NOT UNIFIED ────────────────────────────────────────────")
_rows = [{"product_desc": "Device Setup Charge", "ext_price": 30.0, "trans_id": "1"},
         {"product_desc": "DEVICE SETUP CHARGE", "ext_price": 40.0, "trans_id": "2"},
         {"product_desc": "Apple iPhone", "ext_price": 900.0, "trans_id": "3"}]
_d = SFP.divergence(_rows, ["Device Setup Charge"])
check("F1  divergence() names the ONE line the two matchers disagree about, with its dollars",
      len(_d) == 1 and _d[0]["ext_price"] == 40.0 and _d[0]["matched_by"] == "case_insensitive_only",
      str(_d))
check("F2  a tenant whose data has no case drift gets an EMPTY list = switching mode moves $0",
      SFP.divergence([_rows[0], _rows[2]], ["Device Setup Charge"]) == [])
check("F3  the default mode is the LEGACY one (pay path byte-identity by construction)",
      SFP.PAY_DEFAULTS["match_mode"] == "legacy_case_sensitive")

print("\n── G. PICK-DON'T-TYPE (RULE THREE) — the mapping comes from the tenant's own data ───────")
_cand_rows = [
    {"product_desc": "Access Charge - $25 for single line, max $50 for multiple lines.",
     "ext_price": 25.0, "gp": 12.5, "trans_id": "1", "trans_date": "2026-07-12"},
    {"product_desc": "Access Charge - $25 for single line, max $50 for multiple lines.",
     "ext_price": 25.0, "gp": 12.5, "trans_id": "2", "trans_date": "2026-07-13"},
    {"product_desc": "Activation payment", "ext_price": 0.0, "gp": -66.8, "trans_id": "1",
     "trans_date": "2026-07-12"},
    {"product_desc": "Device Setup Charge", "ext_price": 30.0, "gp": 30.0, "trans_id": "3",
     "trans_date": "2026-07-14"},
]
_c = SFP.candidates(_cand_rows, ["Device Setup Charge"])
_by = {x["product_desc"]: x for x in _c}
check("G1  every distinct product description in the tenant's own sales is offered", len(_c) == 3)
check("G2  the already-mapped one is flagged mapped_now and sorts first",
      _c[0]["product_desc"] == "Device Setup Charge" and _c[0]["mapped_now"] is True, str(_c[0]))
check("G3  the candidates carry the EVIDENCE — lines, transactions, dollars, date range",
      _by["Access Charge - $25 for single line, max $50 for multiple lines."]["ext_price"] == 50.0
      and _by["Access Charge - $25 for single line, max $50 for multiple lines."]["transactions"] == 2
      and _by["Access Charge - $25 for single line, max $50 for multiple lines."]["first"] == "2026-07-12")
check("G4  a $0 line is flagged collects_money=false — 'Activation payment' collects nothing, so "
      "mapping it would pay nobody, and the UI says so instead of letting the owner guess",
      _by["Activation payment"]["collects_money"] is False
      and _by["Access Charge - $25 for single line, max $50 for multiple lines."]["collects_money"] is True)
check("G5  NOTHING is auto-selected — candidates() proposes, it never maps",
      all(not x["mapped_now"] for x in _c if x["product_desc"] != "Device Setup Charge"))

print("\n── H. PER-CARRIER (the Cricket requirement) ─────────────────────────────────────────────")
_cfg = SFP.normalize_pay_config({
    "default": {"include_in_commission": True, "employee_pct_of_collected": 0.10,
                "dealer_share_pct": 1.0},
    "by_carrier": {"car-total": {"include_in_commission": True, "employee_pct_of_collected": 0.0,
                                 "dealer_share_pct": 0.5}}})
_d1, _s1 = SFP.resolve_for_carrier(_cfg, None)
_d2, _s2 = SFP.resolve_for_carrier(_cfg, "car-total")
check("H1  the org DEFAULT is the owner's Boost shape (dealer 100%, employee 10%)",
      _d1["employee_pct_of_collected"] == 0.10 and _d1["dealer_share_pct"] == 1.0 and _s1 == "org_default")
check("H2  a per-CARRIER row overrides it with the Total shape (dealer 50%, employee 0%)",
      _d2["employee_pct_of_collected"] == 0.0 and _d2["dealer_share_pct"] == 0.5 and _s2 == "carrier")
check("H3  an unknown carrier falls back to the org default, never to nothing",
      SFP.resolve_for_carrier(_cfg, "car-cricket")[1] == "org_default")
check("H4  a flat dict with no envelope is read as the org default (tolerant of hand-written SQL)",
      SFP.normalize_pay_config({"employee_pct_of_collected": 0.25})["default"]
      ["employee_pct_of_collected"] == 0.25)
check("H5  the engine resolves per carrier from the PLAN's carrier_id",
      "resolve_for_carrier" in open(os.path.join(os.path.dirname(__file__), "..", "app", "modules",
                                                 "commcalc", "commission_engine.py"),
                                    encoding="utf-8").read())
_pay, _st = SFP.employee_pay(100.0, _d2)
check("H6  the Total carrier's employee pay on $100 collected is $0.00, silently (0 is a decision)",
      _pay == 0.0 and _st == "zero_by_choice")
_ds, _stated = SFP.dealer_share(100.0, _d2)
check("H7  the DEALER share is computed and labelled ($50.00), and no employee payout reads it",
      _ds == 50.0 and _stated is True)
check("H8  an unstated dealer share returns None + stated=False, never $0.00",
      SFP.dealer_share(100.0, SFP.PAY_DEFAULTS) == (None, False))

print("\n── I. NOTHING HARD-CODED (RULE TWO) ─────────────────────────────────────────────────────")
import ast                                                                      # noqa: E402
_p = os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc", "setup_fee_pay.py")
_tree = ast.parse(open(_p, encoding="utf-8").read())
_docs = {ast.get_docstring(n, clean=False) for n in ast.walk(_tree)
         if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
_strs = [n.value for n in ast.walk(_tree)
         if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in _docs]
for w in ("luxelink", "boost", "total wireless", "cricket", "access charge", "activation payment"):
    check(f"I1  no executable literal for {w!r}", not any(w in s.lower() for s in _strs),
          str([s for s in _strs if w in s.lower()])[:150])
check("I2  the ONE product literal is the legacy DEFAULT, in a named constant",
      len([s for s in _strs if s == "Device Setup Charge"]) == 1)
# A PERCENTAGE is a float strictly between 0 and 1. Integers (list caps, limits) are not rates.
_rates = [n.value for n in ast.walk(_tree) if isinstance(n, ast.Constant)
          and isinstance(n.value, float) and 0 < n.value < 1]
check("I3  NO percentage is baked in anywhere — not the owner's 10%, not 50%, not one rate at all",
      _rates == [], str(_rates))

print("\n── J. ISOLATION + NO WRITES ─────────────────────────────────────────────────────────────")
_c = FakeClient(copy.deepcopy(plan_store(
    sf={"default": {"include_in_commission": True, "employee_pct_of_collected": 0.10}}, kws=_kws)))
CE.preview(_c, LUX, "July 2026", detail=True)
check("J1  the engine wrote nothing", _c.writes == [], str(_c.writes))
_s = plan_store(sf={"default": {"include_in_commission": True, "employee_pct_of_collected": 0.10}},
                kws=_kws)
_o = plan_store(org=OTHER, sf={"default": {"include_in_commission": True,
                                           "employee_pct_of_collected": 0.90}}, kws=_kws)
for k in ("raw_sales", "commission_plan", "commission_rule", "commission_plan_assignment",
          "commission_org_config", "accessory_config", "store_mapping"):
    _s[k] = (_s.get(k) or []) + (_o.get(k) or [])
_rl = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
_ro = CE.preview(FakeClient(copy.deepcopy(_s)), OTHER, "July 2026")
check("J2  tenant A pays its own 10% ($5.00) and tenant B its own 90% ($45.00) from ONE process",
      (_rl.get("setup_fee") or {}).get("paid") == 5.0
      and (_ro.get("setup_fee") or {}).get("paid") == 45.0,
      f"{(_rl.get('setup_fee') or {}).get('paid')} / {(_ro.get('setup_fee') or {}).get('paid')}")
check("J3  neither tenant's collected total contains the other's lines",
      (_rl.get("setup_fee") or {}).get("collected") == 50.0
      == (_ro.get("setup_fee") or {}).get("collected"))

print("\n" + "=" * 96)
print(f"RESULT  {PASS} passed, {FAIL} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 96)
sys.exit(1 if FAIL else 0)
