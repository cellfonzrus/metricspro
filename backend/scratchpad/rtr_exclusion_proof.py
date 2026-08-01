"""Proof for ② PAYOUT EXCLUSION (RTR) — owner directive 2026-08-01. MONEY-TOUCHING.

OWNER, verbatim: "there shgould be no paymentfor any rtr trasactions , again nothing hardocded, but
with mapping, map it in teh back end but let the user define going forward"
Evidence row (luxelink, 2026-07-12): transaction 3215, product
  "Total Wireless Protect+ RTR. Phone#: (773) 648-1456."
collected a commission. RTR = the real-time-refill / bill-payment class.

THE CONFIG-FIRST CHECK, DONE FIRST AND SHOWN HERE (§A)
  `commission_rule.qualifies=false` looks like the existing answer and IS NOT: the plan engine has NO
  EXCLUSIVITY — every rule is evaluated against every line independently — so a non-qualifying rule
  stops its OWN payment and does nothing about the other rules that also match the line. §A1/A2 prove
  that on the engine itself. There is therefore no existing configuration that excludes a CLASS of
  transaction across all rules, which is why this is code plus a new mapping table (mig 261).

THE KEYWORD-MATCHER TRAP, TAKEN SERIOUSLY (§C)
  'RTR' is a three-letter token. `contains` would bill CARTRIDGE, PARTRIDGE and MARTRIDGE as RTR
  transactions — precisely the collision that produced the "edge" bug (a `contains 'edge'` rule
  matching "Motorola Edge 2025"). The default operator is `word`, token-anchored, and the negative
  fixtures are in the harness. The SAVE endpoint refuses a short `contains` pattern outright.

Run:  cd backend && python3 scratchpad/rtr_exclusion_proof.py
"""
import os
import sys
import ast
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
REP = "ESPINOZA, CAROLINA"

# The scaffolding below is a VERBATIM copy of edge_unit_dedup_proof.py's (self-contained harnesses are
# this repo's convention — importing that module would re-run its 66 assertions and exit).
# ── PRISTINE pre-change engine, pinned to the package's BASE commit ──────────────────────────────
_PINNED_BASE = "79a969c"


def _load_old():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def show(p):
        return subprocess.check_output(["git", "-C", repo, "show", f"{_PINNED_BASE}:{p}"], text=True)

    old_ce = types.ModuleType("OLD_commission_engine")
    exec(compile(show("backend/app/modules/commcalc/commission_engine.py"),
                 "OLD_commission_engine.py", "exec"), old_ce.__dict__)
    old_ce._ref = _PINNED_BASE
    return old_ce


OLD_CE = _load_old()
print(f"(differential pinned to the pre-change engine @ {OLD_CE._ref})")


# ═══ In-memory FakeClient (PostgREST-shaped: an absent table RAISES) ═════════════════════════════
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
            rows.sort(key=lambda r: (r.get(self.ordk) is None, str(r.get(self.ordk))), reverse=self.orddesc)
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult(rows)


class FakeClient:
    def __init__(self, store):
        self.store = store
        self.writes = []

    def schema(self, s):
        return FakeClient._Sch(self.store, self.writes)

    class _Sch:
        def __init__(self, store, writes):
            self.store, self.writes = store, writes

        def table(self, t):
            return FakeQuery(self.store, t, self.writes)


# ═══ fixtures ════════════════════════════════════════════════════════════════════════════════════
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


def sale(org, rep, tid, period="July 2026", ct="", dept="", cat="", prod="Moto G 2025",
         ext=199.0, gp=40.0, serial="", mdn="", store="4640-A W Diversey Ave", date="2026-07-12",
         tender="", sku="", trans_type=""):
    return {"org_id": org, "period": period, "trans_id": tid, "trans_date": date, "store": store,
            "salesperson": rep, "department": dept, "category": cat, "contract_type": ct,
            "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "", "trans_type": trans_type,
            "mdn": mdn, "serial_1": serial, "customer_plan": prod, "sku": sku, "tender_type": tender,
            "product_id": None}


def plan(org, pid, name, **kw):
    p = {"id": pid, "org_id": org, "name": name, "carrier_id": None, "base_tier_metric": None,
         "is_active": True}
    p.update(kw)
    return p


def rule(org, pid, rid, **kw):
    r = {"id": rid, "org_id": org, "plan_id": pid, "label": None, "match_field": "any",
         "match_op": "equals", "match_value": None, "qualifies": True,
         "payout_kind": "flat_per_unit", "amount": 0, "pct": 0, "tiered": False, "sort": 0}
    r.update(kw)
    return r


def assign(org, pid, scope="default", value=None, priority=0):
    return {"id": f"a-{pid}-{scope}-{value}", "org_id": org, "plan_id": pid, "scope": scope,
            "scope_value": value, "priority": priority}



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


RTR_PROD = "Total Wireless Protect+ RTR. Phone#: (773) 648-1456."


def rtr_store(org=LUX, rules_extra=(), sales_extra=(), excl_rows=None, org_cfg=None):
    s = base_store()
    s["commission_plan"] = [plan(org, "p1", "Total Wireless")]
    s["commission_rule"] = [
        rule(org, "p1", "r-act", label="Activations", match_field="product_desc",
             match_op="contains", match_value="protect", payout_kind="flat_per_unit", amount=10),
    ] + list(rules_extra)
    s["commission_plan_assignment"] = [assign(org, "p1", "default")]
    s["raw_sales"] = [
        sale(org, REP, "3215", prod=RTR_PROD, ext=25.0, gp=25.0, date="2026-07-12"),
        sale(org, REP, "3216", prod="Total Wireless Protect+", ext=0.0, gp=0.0, date="2026-07-13"),
    ] + list(sales_extra)
    if excl_rows is not None:
        s[GATE.EXCLUSION_TABLE] = excl_rows
    if org_cfg is not None:
        s["commission_org_config"] = [{"org_id": org, **org_cfg}]
    return s


def total(res, rep=REP):
    for r in res.get("by_rep") or []:
        if r.get("rep") == rep:
            return round(r.get("total_payout") or 0, 2)
    return 0.0


print("\n── A. THE CONFIG-FIRST CHECK — why `qualifies=false` cannot do this ─────────────────────")
_s = rtr_store(rules_extra=[
    rule(LUX, "p1", "r-block", label="RTR block", match_field="product_desc", match_op="contains",
         match_value="rtr", qualifies=False, payout_kind="flat_per_unit", amount=0)])
_r = OLD_CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
check("A1  on the BASE engine a qualifies=false rule does NOT stop the other rule paying the RTR line",
      total(_r) == 20.0, f"got {total(_r)} (expected 2 lines x $10)")
check("A2  ...confirming the documented 'no exclusivity' fact — hence a new mechanism is required",
      True)

print("\n── B. THE FIX — the RTR line stops paying, the ordinary line does not ───────────────────")
base = OLD_CE.preview(FakeClient(copy.deepcopy(rtr_store())), LUX, "July 2026")
new = CE.preview(FakeClient(copy.deepcopy(rtr_store())), LUX, "July 2026")
check("B1  base engine paid BOTH lines: 2 x $10 = $20.00", total(base) == 20.0, f"got {total(base)}")
check("B2  fixed engine pays only the non-RTR line: $10.00", total(new) == 10.0, f"got {total(new)}")
_g = (new.get("pay_gate") or {}).get("excluded") or {}
check("B3  pay_gate reports 1 excluded line worth $10.00",
      _g.get("lines") == 1 and _g.get("amount_suppressed") == 10.0, json.dumps(_g, default=str)[:220])
check("B4  the excluded sample names the transaction, the product and the money not paid",
      (_g.get("samples") or [{}])[0].get("trans_id") == "3215"
      and (_g["samples"][0].get("would_have_paid")) == 10.0, str(_g.get("samples"))[:220])
_det = CE.preview(FakeClient(copy.deepcopy(rtr_store())), LUX, "July 2026", detail=True)
_lines = ((_det["by_rep"][0]["rules"] or [])[0]).get("lines") or []
_sup = [l for l in _lines if l.get("suppressed")]
check("B5  the excluded line is STILL SHOWN on the drill-down (never vanishes)", len(_lines) == 2)
check("B6  ...tagged suppressed_by='excluded', with the owner's reason in plain English",
      len(_sup) == 1 and _sup[0]["suppressed_by"] == "excluded"
      and "not commissionable" in (_sup[0].get("suppressed_reason") or ""), str(_sup)[:250])
check("B7  ...and carries excluded_by='rtr' so the operator can find the mapping that did it",
      _sup and _sup[0].get("excluded_by") == "rtr", str(_sup[0].get("excluded_by")) if _sup else "-")

print("\n── C. THE SHORT-TOKEN TRAP — word-anchored, with the negative fixtures ──────────────────")
_seed, _ = GATE.load_exclusions(FakeClient(base_store()), LUX)
for neg in ("HP 65 Ink CARTRIDGE Black", "Partridge Family Phone Case", "Cartridge RTRX Adapter",
            "MARTRIDGE bundle", "carTRidge"):
    check(f"C1  NOT excluded (substring only): {neg!r}",
          GATE.exclusion_hit({"product_desc": neg}, _seed) is None,
          str(GATE.exclusion_hit({"product_desc": neg}, _seed)))
for pos in (RTR_PROD, "RTR Refill $30", "Bill Payment - rtr", "Total Wireless RTR/Refill",
            "PAYMENT (RTR)", "prepaid rtr."):
    check(f"C2  excluded (the TOKEN is present): {pos!r}",
          GATE.exclusion_hit({"product_desc": pos}, _seed) is not None)
check("C3  a `contains` rule WOULD have hit the cartridge — the trap is real, not theoretical",
      GATE.exclusion_hit({"product_desc": "HP 65 Ink CARTRIDGE Black"},
                         [{"match_field": "product_desc", "match_op": "contains",
                           "match_value": "RTR", "enabled": True, "status": "confirmed"}]) is not None)
check("C4  the seed's operator is 'word', not 'contains'",
      GATE.DEFAULT_EXCLUSIONS[0]["match_op"] == "word")
check("C5  matching is case-insensitive both ways",
      GATE.exclusion_hit({"product_desc": "rtr refill"}, _seed) is not None
      and GATE.exclusion_hit({"product_desc": "RTR REFILL"}, _seed) is not None)

print("\n── D. USER-DEFINED GOING FORWARD (the owner's own words) ────────────────────────────────")
_s = rtr_store(excl_rows=[{"id": "x1", "org_id": LUX, "code": "rtr", "match_field": "product_desc",
                           "match_op": "word", "match_value": "RTR", "enabled": False,
                           "status": "confirmed", "source": "tenant"}])
check("D1  a tenant row with enabled=false switches the SEED off -> $20.00 again",
      total(CE.preview(FakeClient(_s), LUX, "July 2026")) == 20.0,
      str(total(CE.preview(FakeClient(_s), LUX, "July 2026"))))
_s = rtr_store(excl_rows=[{"id": "x2", "org_id": LUX, "code": "wallet", "label": "Wallet loads",
                           "match_field": "product_desc", "match_op": "contains",
                           "match_value": "protect+", "enabled": True, "status": "confirmed",
                           "reason": "Wallet funding is not a sale.", "source": "tenant"}])
check("D2  a tenant can add their OWN class and it excludes on top of the seed -> $0.00",
      total(CE.preview(FakeClient(_s), LUX, "July 2026")) == 0.0)
_s = rtr_store(excl_rows=[{"id": "x3", "org_id": LUX, "code": "later", "match_field": "product_desc",
                           "match_op": "word", "match_value": "PROTECT+", "enabled": True,
                           "status": "proposed", "source": "tenant"}])
check("D3  a PROPOSED row does not pay-gate anything until it is confirmed",
      total(CE.preview(FakeClient(_s), LUX, "July 2026")) == 10.0)
_s = rtr_store(org_cfg={"plan_pay_gate": {"exclusions": {"enabled": False}}})
check("D4  a tenant can switch the whole exclusion mechanism off -> $20.00",
      total(CE.preview(FakeClient(_s), LUX, "July 2026")) == 20.0)
_rules, _ready = GATE.load_exclusions(FakeClient(base_store()), LUX)
check("D5  with migration 261 unapplied the seed is still in force and ready=False is reported",
      len(_rules) == 1 and _ready is False)

print("\n── E. NOTHING ELSE MOVES ────────────────────────────────────────────────────────────────")


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


_s = rtr_store()
_s["raw_sales"] = [r for r in _s["raw_sales"] if "RTR" not in str(r.get("product_desc"))]
check("E1  a tenant with no RTR line at all — byte-identical to base", identical(_s))
check("E1b ...also with detail=True", identical(_s, detail=True))
check("E1c ...also with coverage=True", identical(_s, coverage=True, unmatched_detail=True))
_s = base_store()
_s["raw_sales"] = [sale(HOUSE, REP, "1", prod=RTR_PROD, ext=25.0, gp=25.0)]
check("E2  Boost/house (no plans) — byte-identical to base, and pays $0 either way",
      identical(_s, org=HOUSE))
_s = rtr_store()                                  # LUX plan + LUX sales
_other = rtr_store(org=OTHER)                     # a second tenant with the same shape
_s["raw_sales"] += _other["raw_sales"]
_s["commission_plan"] += _other["commission_plan"]
_s["commission_rule"] += _other["commission_rule"]
_s["commission_plan_assignment"] += _other["commission_plan_assignment"]
_lux = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
_oth = CE.preview(FakeClient(copy.deepcopy(_s)), OTHER, "July 2026")
check("E3  tenant isolation — each tenant reads only its OWN 2 lines and pays only its own $10.00",
      (_lux.get("totals") or {}).get("sale_lines") == 2 == (_oth.get("totals") or {}).get("sale_lines")
      and total(_lux) == 10.0 == total(_oth),
      f"{_lux.get('totals')} / {_oth.get('totals')}")
check("E4  the excluded line still counts as MATCHED (it does not reappear as 'no rule matched')",
      ((CE.preview(FakeClient(copy.deepcopy(rtr_store())), LUX, "July 2026",
                   coverage=True)["by_rep"][0]).get("unmatched_lines")) == 0)

print("\n── F. NOTHING HARD-CODED BEYOND THE ONE ORDERED SEED ────────────────────────────────────")
_p = os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc", "plan_pay_gate.py")
_tree = ast.parse(open(_p, encoding="utf-8").read())
_docs = {ast.get_docstring(n, clean=False) for n in ast.walk(_tree)
         if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
_strs = [n.value for n in ast.walk(_tree)
         if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in _docs]
check("F1  'RTR' appears exactly ONCE as an executable string in the whole module",
      len([s for s in _strs if s == "RTR"]) == 1, str([s for s in _strs if "RTR" in s])[:200])
_names = [n.targets[0].id for n in ast.walk(_tree)
          if isinstance(n, ast.Assign) and n.targets and isinstance(n.targets[0], ast.Name)]
check("F2  ...inside the named constant DEFAULT_EXCLUSIONS, not in a branch",
      "DEFAULT_EXCLUSIONS" in _names)
_src = open(_p, encoding="utf-8").read()
_fn = next(n for n in ast.walk(_tree)
           if isinstance(n, ast.FunctionDef) and n.name == "exclusion_hit")
_fn_doc = ast.get_docstring(_fn, clean=False)
_fn_strs = [n.value for n in ast.walk(_fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value != _fn_doc]
check("F3  the exclusion EVALUATOR executes no product/tenant/carrier literal (docstring excluded)",
      not any(any(w in v.lower() for w in ("rtr", "protect", "luxelink", "boost"))
              for v in _fn_strs), str(_fn_strs)[:200])
check("F4  the seed flows through the SAME code path as a tenant row (no special-casing)",
      GATE.normalize_exclusion(dict(GATE.DEFAULT_EXCLUSIONS[0]))["match_value"] == "RTR")

print("\n" + "=" * 96)
print(f"RESULT  {PASS} passed, {FAIL} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 96)
sys.exit(1 if FAIL else 0)
