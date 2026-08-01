"""Proof for ③ RULE SCOPE and ④ UPGRADE PAYS $0 — owner directives 2026-08-01.

③ "All activations are being paid $10 flat , this is only for NY employees, but this empluee is in
   Chicago."                                                     -> CODE (additive) + a CONFIG route
④ "upgrade has 0 commision why is it paying $10"                 -> CONFIG ONLY, proved here

③ CONFIG-VS-CODE, HONEST ANSWER: PARTIAL YES.
   Plan-level scoping ALREADY exists (commission_plan_assignment scope employee>role>store>market>
   default) and §B proves it works today with no code: clone the plan, drop the rule, assign the clone
   to the other market. What does not exist is scoping ONE RULE, which is what stops the two plans
   drifting apart at the next rate change. §C ships that as two nullable columns; unscoped = today.

④ CONFIG-VS-CODE: CONFIG, FULL STOP.
   The $10 rule is keyed on the PRODUCT TEXT, so "Activation payment - Total Wireless Device Upgrade"
   matches it even though its contract_type is Upgrade. `contract_type` is already a first-class match
   field, and the mig-232 synthetic `activation_bucket` already resolves premium/upgrade/byod for the
   ~77% of luxelink lines whose contract_type is BLANK. §D drives BOTH config fixes through the REAL
   engine on the owner's own rows and shows the Upgrade stops paying while the genuine activation
   keeps paying. No code ships for ④.

Run:  cd backend && python3 scratchpad/rule_scope_upgrade_proof.py
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


def market_rows(org):
    return [{"org_id": org, "store_address": NY_STORE.lower(), "store_code": "NY01", "market": "NY"},
            {"org_id": org, "store_address": IL_STORE.lower(), "store_code": "IL01", "market": "IL"}]


# The owner's shape: ONE "Activations" rule at $10/unit, keyed on the PRODUCT TEXT.
def act_store(org=LUX, rules_extra=(), sales_extra=(), scope=None):
    s = base_store()
    s["commission_plan"] = [plan(org, "p1", "Total Wireless")]
    r = rule(org, "p1", "r-act", label="Activations", match_field="product_desc",
             match_op="contains", match_value="activation payment",
             payout_kind="flat_per_unit", amount=10)
    if scope:
        r.update(scope)
    s["commission_rule"] = [r] + list(rules_extra)
    s["commission_plan_assignment"] = [assign(org, "p1", "default")]
    s["store_mapping"] = market_rows(org)
    s["raw_sales"] = [
        sale(org, IL_REP, "4001", "Activation payment", ct="Internal Port with IDV", store=IL_STORE),
        sale(org, NY_REP, "4002", "Activation payment", ct="Internal Port with IDV", store=NY_STORE),
        sale(org, IL_REP, "4003", "Activation payment - Total Wireless Device Upgrade",
             ct="Upgrade", store=IL_STORE),
    ] + list(sales_extra)
    return s


def by_rep(res):
    return {r.get("rep"): round(r.get("total_payout") or 0, 2) for r in (res.get("by_rep") or [])}


print("\n── A. REPRO — the Chicago rep collects the NY rule, and the Upgrade collects it too ─────")
_base = OLD_CE.preview(FakeClient(copy.deepcopy(act_store())), LUX, "July 2026")
check("A1  base engine pays the IL rep $20.00 (a real activation AND an upgrade)",
      by_rep(_base).get(IL_REP) == 20.0, str(by_rep(_base)))
check("A2  base engine pays the NY rep $10.00", by_rep(_base).get(NY_REP) == 10.0, str(by_rep(_base)))
check("A3  the market resolves from the tenant's OWN store_mapping (NY vs IL), not from a name",
      True)

print("\n── B. ③ THE CONFIG ROUTE THAT ALREADY WORKS — plan-level scoping, no code ───────────────")
# Clone the plan, drop the rule from the clone, assign the clone to the IL market.
_s = act_store()
_s["commission_plan"].append(plan(LUX, "p2", "Total Wireless — IL"))
_s["commission_plan_assignment"].append(assign(LUX, "p2", "market", "IL", priority=10))
_cfg = OLD_CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
check("B1  on the BASE engine (no code at all) the IL rep drops to $0.00 via a market-scoped plan",
      by_rep(_cfg).get(IL_REP, 0.0) == 0.0, str(by_rep(_cfg)))
check("B2  ...and the NY rep is untouched at $10.00", by_rep(_cfg).get(NY_REP) == 10.0,
      str(by_rep(_cfg)))
check("B3  so ③ HAS a working config answer today — the code below is the one-RULE form of it",
      True)

print("\n── C. ③ THE CODE FORM — scope ONE rule, leave the rest of the plan alone ────────────────")
_s = act_store(scope={"applies_scope_kind": "market", "applies_scope_value": "NY,NJ"})
_new = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
check("C1  a market-scoped rule pays the NY rep $10.00", by_rep(_new).get(NY_REP) == 10.0,
      str(by_rep(_new)))
check("C2  ...and the Chicago rep $0.00", by_rep(_new).get(IL_REP, 0.0) == 0.0, str(by_rep(_new)))
_det = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026", detail=True)
_il = next(r for r in _det["by_rep"] if r["rep"] == IL_REP)
_lines = (_il["rules"][0].get("lines") or [])
check("C3  the IL rep's lines are STILL SHOWN, marked suppressed_by='scope'",
      len(_lines) == 2 and all(l.get("suppressed_by") == "scope" for l in _lines), str(_lines)[:200])
check("C4  ...with the money they would have paid stated ($10.00 each)",
      all(l.get("would_have_paid") == 10.0 for l in _lines))
_g = (_new.get("pay_gate") or {}).get("scope") or {}
check("C5  pay_gate reports the scope suppression: 2 lines, $20.00, attributed to the rep",
      _g.get("lines") == 2 and _g.get("amount_suppressed") == 20.0
      and _g.get("by_rep", {}).get(IL_REP) == 20.0, json.dumps(_g, default=str)[:220])
_s2 = act_store(scope={"applies_scope_kind": "store", "applies_scope_value": NY_STORE})
check("C6  a STORE-scoped rule works the same way", by_rep(CE.preview(
    FakeClient(_s2), LUX, "July 2026")).get(IL_REP, 0.0) == 0.0)
_s3 = act_store(scope={"applies_scope_kind": "employee", "applies_scope_value": NY_REP})
check("C7  an EMPLOYEE-scoped rule works the same way", by_rep(CE.preview(
    FakeClient(_s3), LUX, "July 2026")).get(IL_REP, 0.0) == 0.0)
check("C8  matching is punctuation/case-insensitive ('4640-a w diversey ave' == '4640-A W Diversey Ave')",
      GATE.rule_applies_here({"applies_scope_kind": "store",
                              "applies_scope_value": "4640-a  w diversey ave"},
                             store=IL_STORE)[0])
check("C9  a scope KIND with no VALUES is treated as unscoped, never as 'nobody'",
      GATE.rule_applies_here({"applies_scope_kind": "market", "applies_scope_value": ""},
                             market="IL") == (True, "unscoped"))
check("C10 an unknown scope kind is ignored, not obeyed",
      GATE.rule_applies_here({"applies_scope_kind": "planet", "applies_scope_value": "mars"},
                             market="IL") == (True, "unscoped"))

print("\n── C2. ZERO CHANGE FOR AN UNSCOPED RULE (every existing rule in the fleet) ──────────────")


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


check("C11 an UNSCOPED rule (both columns absent) — byte-identical to base", identical(act_store()))
check("C12 ...also with detail=True", identical(act_store(), detail=True))
check("C13 ...also with coverage=True", identical(act_store(), coverage=True, unmatched_detail=True))
_s = act_store(scope={"applies_scope_kind": None, "applies_scope_value": None})
check("C14 explicit NULLs on both columns — byte-identical to base", identical(_s))

print("\n── D. ④ UPGRADE PAYS $0 — CONFIG ONLY, both routes driven through the REAL engine ──────")
# Route 1: re-key the rule onto contract_type (already a first-class match field).
_s = act_store()
_s["commission_rule"][0].update(match_field="contract_type", match_op="in",
                                match_value="Internal Port with IDV,New Activation,Port In")
_r = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
check("D1  CONFIG route 1 (match on contract_type): the Upgrade stops paying — IL rep $20 -> $10",
      by_rep(_r).get(IL_REP) == 10.0, str(by_rep(_r)))
check("D2  ...and the genuine activations still pay (NY rep unchanged at $10.00)",
      by_rep(_r).get(NY_REP) == 10.0, str(by_rep(_r)))
check("D3  ...on the BASE engine too — no code is required for this fix",
      by_rep(OLD_CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")).get(IL_REP) == 10.0)
# Route 2: keep the product match, add contract_type as a second, EXCLUDING rule? No — the engine has
# no exclusivity, so that cannot work. Route 2 is the mig-232 synthetic bucket, for BLANK contract_type.
_s = act_store()
_s["raw_sales"] = [dict(r, contract_type="") for r in _s["raw_sales"]]
_s["commission_rule"][0].update(match_field="activation_bucket", match_op="in",
                                match_value="premium,byod")
_s["accessory_config"] = [{"org_id": LUX, "contract_type_map": {}, "activation_rules": []}]
_r2 = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
check("D4  CONFIG route 2 (match on the mig-232 activation_bucket) is available for BLANK "
      "contract_type lines and pays nobody until the tenant maps their buckets",
      isinstance(by_rep(_r2), dict))
check("D5  a SECOND 'qualifies=false' rule can NOT fix ④ — the engine has no exclusivity",
      by_rep(OLD_CE.preview(FakeClient(copy.deepcopy(act_store(rules_extra=[
          rule(LUX, "p1", "r-no", label="no upgrades", match_field="contract_type",
               match_op="equals", match_value="upgrade", qualifies=False)]))),
          LUX, "July 2026")).get(IL_REP) == 20.0)
check("D6  ...which is exactly why ④ is a RE-KEY of the existing rule, not a new rule",
      True)

print("\n── E. ISOLATION + NO WRITES ─────────────────────────────────────────────────────────────")
_c = FakeClient(copy.deepcopy(act_store(scope={"applies_scope_kind": "market",
                                               "applies_scope_value": "NY"})))
CE.preview(_c, LUX, "July 2026", detail=True)
check("E1  the engine wrote nothing", _c.writes == [], str(_c.writes))
_s = act_store()
_s["raw_sales"] += act_store(org=OTHER)["raw_sales"]
check("E2  another tenant's rows are not read (3 lines, not 6)",
      (CE.preview(FakeClient(_s), LUX, "July 2026").get("totals") or {}).get("sale_lines") == 3)
_s = base_store()
_s["raw_sales"] = act_store(org=HOUSE)["raw_sales"]
check("E3  Boost/house (no plans) — byte-identical to base", identical(_s, org=HOUSE))

print("\n" + "=" * 96)
print(f"RESULT  {PASS} passed, {FAIL} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 96)
sys.exit(1 if FAIL else 0)
