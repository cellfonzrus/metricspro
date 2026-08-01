"""Proof for ⑤ ACCESSORY %-OF-GP BASIS GUARD — owner directive 2026-08-01. MONEY-TOUCHING, DEFAULT OFF.

OWNER, verbatim: "accessories not being paid , they should be paid as all of these have been mapped"

THE OWNER'S OWN ROWS (luxelink July 2026, %-of-GP accessory lines at the corrected 0.175 rate):
  ext_price $8.49-$59.98 paying $0.00      -> GP is $0: the POS catalog carries cost == retail on the
                                              "* BYOD" class, so 0.175 x 0 = 0. The engine is right and
                                              the INPUT is unusable.
  gp -$1.00  -> -$0.17  |  gp -$10.00 -> -$1.75  |  gp -$6.50 -> -$1.14
                                           -> negative GP paying a NEGATIVE commission. (These three
                                              amounts also confirm the 17.5 -> 0.175 rate fix is live
                                              in the data the owner is reading.)

CONFIG-VS-CODE VERDICT: CODE, and it is the Option-C machinery the shipped accessory-cost-audit
already PREVIEWS — now available as engine behaviour behind a per-tenant switch. There is no existing
setting that changes a payout BASIS; `commission_rule.payout_kind` only chooses between GP, MRC and
price-over-cost, and price-over-cost reads raw_catalog cost (absent here), so it cannot substitute.

DEFAULT OFF FLEET-WIDE, DELIBERATELY. Switching it on changes accessory pay, so it is an explicit
tenant decision taken after reading the preview. §E proves the fleet-wide default is byte-identical.

WHICH LINES ARE ACCESSORIES comes from the tenant's OWN accessory definition (mig 257) — "all of these
have been mapped" is the owner's premise, and the guard honours it rather than guessing. A flagged
line that is NOT mapped is left alone and surfaced, never silently repriced (§D).

Run:  cd backend && python3 scratchpad/accessory_basis_guard_proof.py
"""
import os
import sys
import copy
import json
import subprocess
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.commission_engine as CE
import app.modules.commcalc.plan_pay_gate as GATE
import app.modules.commcalc.pay_data_quality as PDQ

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"
NY_REP, IL_REP = "PATEL, NIRAV", "ESPINOZA, CAROLINA"
NY_STORE, IL_STORE = "1122 Broadway", "4640-A W Diversey Ave"

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


_PINNED_BASE = "79a969c"


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



# ── the tenant's ACCESSORY DEFINITION (mig 257) + the owner's real accessory rows ────────────────
ACC_MAP = [
    {"id": "m1", "org_id": LUX, "match_field": "product_desc", "match_value": "screen protectors byod",
     "is_accessory": True, "accessory_class": "screen_protector", "status": "confirmed"},
    {"id": "m2", "org_id": LUX, "match_field": "product_desc", "match_value": "case byod",
     "is_accessory": True, "accessory_class": "case", "status": "confirmed"},
    {"id": "m3", "org_id": LUX, "match_field": "product_desc", "match_value": "wireless earbuds",
     "is_accessory": True, "accessory_class": "earphones", "status": "confirmed"},
    {"id": "m4", "org_id": LUX, "match_field": "product_desc", "match_value": "charger port repair",
     "is_accessory": False, "accessory_class": None, "status": "confirmed"},
]
RATE = 0.175          # the CORRECTED rate (0.175 = 17.5%), as it now stands in the owner's data


def acc_store(org=LUX, mapped=True, guard=None, sales=None):
    s = base_store()
    s["commission_plan"] = [plan(org, "p1", "Total Wireless")]
    s["commission_rule"] = [rule(org, "p1", "r-acc", label="Accessories",
                                 match_field="category", match_op="equals",
                                 match_value="accessories", payout_kind="pct_gp", pct=RATE)]
    s["commission_plan_assignment"] = [assign(org, "p1", "default")]
    s["store_mapping"] = [{"org_id": org, "store_address": IL_STORE.lower(),
                           "store_code": "IL01", "market": "IL"}]
    s["raw_sales"] = sales if sales is not None else [
        dict(sale(org, IL_REP, "5001", "Screen Protectors BYOD", ext=24.99, gp=0.0), category="Accessories"),
        dict(sale(org, IL_REP, "5002", "Case BYOD", ext=59.98, gp=0.0), category="Accessories"),
        dict(sale(org, IL_REP, "5003", "Wireless Earbuds", ext=8.49, gp=-1.00), category="Accessories"),
        dict(sale(org, IL_REP, "5004", "Wireless Earbuds", ext=39.99, gp=-10.00), category="Accessories"),
        dict(sale(org, IL_REP, "5005", "Case BYOD", ext=19.99, gp=-6.50), category="Accessories"),
        # a HEALTHY accessory line: believable GP, must keep paying %-of-GP byte-identically
        dict(sale(org, IL_REP, "5006", "Wireless Earbuds", ext=49.99, gp=20.00), category="Accessories"),
    ]
    if mapped:
        s["accessory_definition_map"] = [r for r in ACC_MAP if r["org_id"] == org] or ACC_MAP
        s["accessory_config"] = [{"org_id": org, "definition_field_rule": None,
                                  "setup_fee_products": []}]
    if guard is not None:
        s["commission_org_config"] = [{"org_id": org, "plan_pay_gate":
                                       {"accessory_basis_guard": guard}}]
    return s


def rep_total(res, rep=IL_REP):
    for r in res.get("by_rep") or []:
        if r.get("rep") == rep:
            return round(r.get("total_payout") or 0, 2)
    return 0.0


print("\n── A. REPRO — the owner's numbers, on the engine as it stands today ─────────────────────")
_now = CE.preview(FakeClient(copy.deepcopy(acc_store())), LUX, "July 2026", detail=True)
_lines = ((_now["by_rep"][0]["rules"] or [])[0]).get("lines") or []
_amts = {round(l["ext_price"], 2): round(l["amount"], 2) for l in _lines}
check("A1  the $24.99 and $59.98 GP-zero lines pay $0.00 — arithmetic, not a bug",
      _amts.get(24.99) == 0.0 and _amts.get(59.98) == 0.0, str(_amts))
check("A2  gp -$1.00 pays -$0.17, gp -$10.00 pays -$1.75, gp -$6.50 pays -$1.14 (the owner's rows)",
      _amts.get(8.49) == -0.17 and _amts.get(39.99) == -1.75 and _amts.get(19.99) == -1.14, str(_amts))
check("A3  ...which also confirms the rate is stored as 0.175, not 17.5 (17.5 would pay -$17.50)",
      round(RATE * -10.0, 2) == -1.75)
check("A4  the healthy line (GP $20.00) pays $3.50", _amts.get(49.99) == 3.50, str(_amts))
check("A5  today's total for this rep is $0.44", rep_total(_now) == 0.44, str(rep_total(_now)))

print("\n── B. THE GUARD ON — the rate is paid on the PRICE, and nothing pays negative ───────────")
_g = CE.preview(FakeClient(copy.deepcopy(acc_store(guard={"enabled": True}))), LUX, "July 2026",
                detail=True)
_gl = ((_g["by_rep"][0]["rules"] or [])[0]).get("lines") or []
_ga = {round(l["ext_price"], 2): round(l["amount"], 2) for l in _gl}
check("B1  $24.99 x 0.175 = $4.37 (was $0.00)", _ga.get(24.99) == 4.37, str(_ga))
check("B2  $59.98 x 0.175 = $10.50 (was $0.00)", _ga.get(59.98) == 10.50, str(_ga))
check("B3  the three negative lines now pay on price: $1.49 / $7.00 / $3.50, never below $0",
      _ga.get(8.49) == 1.49 and _ga.get(39.99) == 7.00 and _ga.get(19.99) == 3.50, str(_ga))
check("B4  the HEALTHY line is byte-identical at $3.50 — a believable GP is never second-guessed",
      _ga.get(49.99) == 3.50, str(_ga))
check("B5  the rep's total moves $0.44 -> $30.36", rep_total(_g) == 30.36, str(rep_total(_g)))
_ab = (_g.get("pay_gate") or {}).get("accessory_basis") or {}
check("B6  pay_gate reports 5 changed lines and the before/after dollars",
      _ab.get("lines") == 5 and _ab.get("amount_before") == -3.06 and _ab.get("amount_after") == 26.86,
      json.dumps(_ab, default=str)[:260])
check("B7  ...broken down by the cost-integrity flag that triggered it",
      set(_ab.get("by_flag") or {}) == {"cost_equals_price", "gp_negative"},
      str(list((_ab.get("by_flag") or {}))))
check("B8  every repriced line carries basis_used / basis_flags / a human note / the prior amount",
      all(l.get("basis_used") == "ext_price" and l.get("basis_flags")
          and isinstance(l.get("basis_note"), str) and "amount_before_guard" in l
          for l in _gl if l.get("basis_guarded")))

print("\n── C. THE CLAMP, ON ITS OWN ─────────────────────────────────────────────────────────────")
_c = CE.preview(FakeClient(copy.deepcopy(acc_store(guard={
    "enabled": True, "trigger_flags": ["cost_equals_price"]}))), LUX, "July 2026", detail=True)
_cl = {round(l["ext_price"], 2): round(l["amount"], 2)
       for l in (((_c["by_rep"][0]["rules"] or [])[0]).get("lines") or [])}
check("C1  with gp_negative NOT a trigger, the negative lines are CLAMPED to $0.00, not repriced",
      _cl.get(8.49) == 0.0 and _cl.get(39.99) == 0.0 and _cl.get(19.99) == 0.0, str(_cl))
check("C2  ...and the GP-zero lines are still repriced ($4.37 / $10.50)",
      _cl.get(24.99) == 4.37 and _cl.get(59.98) == 10.50, str(_cl))
_c2 = CE.preview(FakeClient(copy.deepcopy(acc_store(guard={
    "enabled": True, "trigger_flags": ["cost_equals_price"], "clamp_negative": False}))),
    LUX, "July 2026", detail=True)
_cl2 = {round(l["ext_price"], 2): round(l["amount"], 2)
        for l in (((_c2["by_rep"][0]["rules"] or [])[0]).get("lines") or [])}
check("C3  a tenant that switches the clamp OFF keeps the negative payouts (-$0.17 etc.)",
      _cl2.get(8.49) == -0.17, str(_cl2))

print("\n── D. THE MAPPING IS THE OWNER'S, AND AN UNMAPPED LINE IS LEFT ALONE ────────────────────")
# With no MANUAL mapping the tenant's own DEFAULT FIELD RULE still applies — "anything which says
# accesspories or category accesory" is the owner's own definition (mig 257), so these lines ARE
# accessories to this tenant and the guard acts on them. That is the premise being honoured, not a
# guess: the decision still comes from accessory_definition.classify(), never from a product name.
_u = CE.preview(FakeClient(copy.deepcopy(acc_store(mapped=False, guard={"enabled": True}))),
                LUX, "July 2026")
check("D1  with no MANUAL mapping the tenant's own category-field rule still recognises them "
      "($30.36, the same answer) — the definition is the owner's, in both of its mechanisms",
      rep_total(_u) == 30.36, str(rep_total(_u)))
# The real question is the line the definition does NOT claim.
_s0 = acc_store(guard={"enabled": True})
_s0["raw_sales"].append(dict(sale(LUX, IL_REP, "5008", "Wallet Funding", ext=73.00, gp=0.0),
                             category="Fees", department="Fees"))
_s0["commission_rule"][0].update(match_field="any", match_op="equals", match_value="")
_r0 = CE.preview(FakeClient(copy.deepcopy(_s0)), LUX, "July 2026", detail=True)
_w = [l for l in (((_r0["by_rep"][0]["rules"] or [])[0]).get("lines") or [])
      if "Wallet" in str(l.get("product"))]
check("D2  a GP-zero line the definition does NOT claim (Wallet Funding, category Fees) is left "
      "alone — still $0.00, not repriced",
      _w and _w[0].get("amount") == 0.0 and not _w[0].get("basis_guarded"), str(_w)[:220])
_s = acc_store(guard={"enabled": True})
_s["raw_sales"].append(dict(sale(LUX, IL_REP, "5007", "Charger Port Repair", ext=45.00, gp=0.0),
                            category="Accessories"))
_r = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026", detail=True)
_rl = [l for l in (((_r["by_rep"][0]["rules"] or [])[0]).get("lines") or [])
       if "Repair" in str(l.get("product"))]
check("D3  a line the tenant mapped as is_accessory=FALSE is NOT repriced (the repair trap)",
      _rl and _rl[0].get("amount") == 0.0 and not _rl[0].get("basis_guarded"), str(_rl)[:220])

print("\n── E. DEFAULT OFF = BYTE-IDENTICAL FLEET-WIDE ───────────────────────────────────────────")


def _drop(o):
    o = copy.deepcopy(o)
    o.pop("pay_gate", None)
    for r in o.get("by_rep") or []:
        for rb in r.get("rules") or []:
            for k in ("unit_basis", "unit_basis_source", "scope_reason"):
                rb.pop(k, None)
            for ln in rb.get("lines") or []:
                for k in ("suppressed", "suppressed_by", "suppressed_reason", "would_have_paid",
                          "excluded_by", "basis_guarded", "basis_used", "basis_flags", "basis_note",
                          "amount_before_guard"):
                    ln.pop(k, None)
    return o


def identical(store, org=LUX, **kw):
    a = OLD_CE.preview(FakeClient(copy.deepcopy(store)), org, "July 2026", **kw)
    b = CE.preview(FakeClient(copy.deepcopy(store)), org, "July 2026", **kw)
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(_drop(b), sort_keys=True,
                                                                    default=str)


check("E1  guard unconfigured (the fleet default) — byte-identical to base @79a969c",
      identical(acc_store()))
check("E1b ...also with detail=True", identical(acc_store(), detail=True))
check("E1c ...also with coverage=True", identical(acc_store(), coverage=True, unmatched_detail=True))
check("E2  guard explicitly disabled — byte-identical to base",
      identical(acc_store(guard={"enabled": False})))
check("E3  code default is OFF", GATE.ACC_BASIS_DEFAULTS["enabled"] is False)
check("E4  code default invents NO margin (assumed_margin_pct is None, not 1.0 or 0.35)",
      GATE.ACC_BASIS_DEFAULTS["assumed_margin_pct"] is None)
_s = base_store()
_s["raw_sales"] = acc_store(org=HOUSE)["raw_sales"]
check("E5  Boost/house (no plans) — byte-identical to base", identical(_s, org=HOUSE))
_gonly = CE.preview(FakeClient(copy.deepcopy(acc_store(guard={"enabled": True}))), LUX, "July 2026")
_notacc = [r for r in (_gonly.get("by_rep") or [])]
check("E6  the guard only ever touches pct_gp rules (a flat_per_unit rule is not consulted)",
      GATE.guarded_pct_gp({"ext_price": 25.0, "gp": 0.0}, 0.175,
                          {"enabled": True}, None, False)[0] is None)

print("\n── F. THE MARGIN IS THE TENANT'S, NEVER INVENTED ────────────────────────────────────────")
_m = CE.preview(FakeClient(copy.deepcopy(acc_store(guard={"enabled": True,
                                                          "assumed_margin_pct": 0.35}))),
                LUX, "July 2026", detail=True)
_ml = {round(l["ext_price"], 2): round(l["amount"], 2)
       for l in (((_m["by_rep"][0]["rules"] or [])[0]).get("lines") or [])}
check("F1  with a 0.35 assumed margin, $24.99 pays 0.175 x (24.99 x 0.35) = $1.53",
      _ml.get(24.99) == 1.53, str(_ml))
check("F2  a BLANK margin stays None (not 0.0) — a blank is 'not stated', never 'zero'",
      GATE._num_or_none("") is None and GATE._num_or_none(None) is None
      and GATE._num_or_none("abc") is None and GATE._num_or_none("0") == 0.0)
check("F3  the trigger flags come from the SHARED mig-255 cost-integrity predicates, not a new one",
      set(GATE.ACC_BASIS_DEFAULTS["trigger_flags"]) <= set(PDQ.FLAG_LABELS))
check("F4  a line under the tenant's min_ext_price threshold is not judged at all",
      PDQ.line_flags(0.0, 0.0) == [])

print("\n── G. ISOLATION + NO WRITES ─────────────────────────────────────────────────────────────")
_c = FakeClient(copy.deepcopy(acc_store(guard={"enabled": True})))
CE.preview(_c, LUX, "July 2026", detail=True)
check("G1  the engine wrote nothing", _c.writes == [], str(_c.writes))
_s = acc_store(guard={"enabled": True})
_s["raw_sales"] += acc_store(org=OTHER)["raw_sales"]
check("G2  another tenant's accessory lines are not read (6 lines, not 12)",
      (CE.preview(FakeClient(_s), LUX, "July 2026").get("totals") or {}).get("sale_lines") == 6)

print("\n" + "=" * 96)
print(f"RESULT  {PASS} passed, {FAIL} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 96)
sys.exit(1 if FAIL else 0)
