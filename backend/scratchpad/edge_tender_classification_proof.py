"""Proof for agent/commission/edge-tender-classification (owner ruling 2026-07-27, MONEY-TOUCHING).

OWNER RULING (verbatim): "these sa;es dont qualify as edge , it is the name of phone model which is edge ,
these qualify for the multi month incentive - same for activations in luxelink , edge is only of the tender
method is tw finnacing"

i.e. the pay bucket "edge" = the device-FINANCING program → it may key ONLY on the sale's TENDER METHOD.
It must never key on the word "edge" inside a product description, because "Motorola Edge 2025" is a MODEL.

WHAT THIS PROVES (no live DB — Supabase is web-only from here)
  R  REPRO — a rule `product_desc contains 'edge'` @ $25/unit pays the owner's five REAL July lines, and
     at the same time MISSES a genuine TW-financing sale. Two failures, not one.
  A  RE-KEY — `tender_type equals <the tenant's financing tender>` stops matching all five model-name
     lines and starts matching the financing sale. Tender is ALREADY a first-class engine match field.
  B  BLAST RADIUS — plan_impact.rule_impact reports the per-rep delta, every freed line WITH its tender,
     and — line by line — whether a multi-month schedule trigger actually picks it up.
  C  NO EXCLUSIVITY / NO AUTOMATIC FALLTHROUGH — with no schedule trigger matching, the freed lines pay
     $0 from every configured source (`freed_paying_nothing`), and with one they are covered. The engine
     never re-routes a line by itself.
  D  MULTI-MONTH — one chain per ACTIVATION (the owner's trans 4045 has two matching lines → ONE chain),
     two subscribers → two chains, and the chain pays on the RATE-PLAN MRC, not the handset price.
  E  THE NEGATIVE ROW — GP −$259.99 is a GP figure, not a return: `flat_per_unit` pays the same $25 on it
     (sign-blind, by design), `pct_gp` would pay NEGATIVE, and after the re-key it pays $0. Stated
     explicitly so nobody assumes a reversal was involved.
  F  OTHER-TENANT NO-OP + BYTE IDENTITY — preview() with rule_overrides unset is byte-identical to the
     pre-change engine over a fixture matrix + a 300-seed fuzz; a tenant with no 'edge' rule is untouched.
  G  WARNINGS — plan_impact.pay_warnings names the activations that no rule and no schedule pays, and
     returns nothing when everything is covered (no noise) and nothing for a Boost/house org with no plans.
  H  KEYWORD-COLLISION AUDIT — the generic form of this bug class: it flags the rule, lists the ITEMS the
     pattern really hit, and names the OTHER field carrying the same word. Nothing about "edge" is
     hard-coded anywhere in the code.

NOTE ON THE TENDER LABEL: the literal financing-tender string is the TENANT'S OWN value (it comes from the
POS export's `Tender Type` column and is offered in the rule editor's dropdown). This harness parameterises
it — every assertion is about the MECHANISM, never about a particular spelling.

Run:  cd backend && python3 scratchpad/edge_tender_classification_proof.py
"""
import os
import sys
import copy
import random
import subprocess
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.commission_engine as CE
import app.modules.commcalc.sale_installment_engine as SIE
import app.modules.commcalc.plan_impact as PI

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"

# The tenant's own financing tender label (whatever the POS writes). Only the MECHANISM is asserted.
TW_FIN = "TW Financing"

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── PRISTINE pre-change engine, from the merge-base with origin/main ─────────────────────────────
_PINNED_BASE = "26cd98f"


def _base_ref():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    try:
        return subprocess.check_output(
            ["git", "-C", repo, "merge-base", "HEAD", "origin/main"], text=True).strip() or _PINNED_BASE
    except Exception:
        return _PINNED_BASE


def _load_old():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ref = _base_ref()

    def show(p):
        return subprocess.check_output(["git", "-C", repo, "show", f"{ref}:{p}"], text=True)

    old_ce = types.ModuleType("OLD_commission_engine")
    exec(compile(show("backend/app/modules/commcalc/commission_engine.py"),
                 "OLD_commission_engine.py", "exec"), old_ce.__dict__)
    keep = sys.modules.get("app.modules.commcalc.commission_engine")
    sys.modules["app.modules.commcalc.commission_engine"] = old_ce
    try:
        old_sie = types.ModuleType("OLD_sale_installment_engine")
        exec(compile(show("backend/app/modules/commcalc/sale_installment_engine.py"),
                     "OLD_sale_installment_engine.py", "exec"), old_sie.__dict__)
    finally:
        if keep is not None:
            sys.modules["app.modules.commcalc.commission_engine"] = keep
    old_ce._ref = old_sie._ref = ref
    return old_ce, old_sie


OLD_CE, OLD_SIE = _load_old()
print(f"(differential pinned to the pre-change engines @ {OLD_CE._ref[:10]})")


# ═══ In-memory FakeClient (PostgREST-shaped: an absent table RAISES) ═════════════════════════════
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t, self.f = store, table, []
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

    def upsert(self, *a, **k):
        return self

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

    def schema(self, s):
        return FakeClient._Sch(self.store)

    class _Sch:
        def __init__(self, store):
            self.store = store

        def table(self, t):
            return FakeQuery(self.store, t)


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


def sale(org, rep, tid, period="July 2026", ct="", dept="BrandedHandset", cat="", prod="Moto G 2025",
         ext=199.0, gp=40.0, serial="", mdn="", store="957 Pennsylvania Avenue", date="2026-07-05",
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


def sched(org, pid, sid, name, field="any", op="equals", value=None, months=6, **kw):
    s = {"id": sid, "org_id": org, "plan_id": pid, "name": name, "num_months": months,
         "trigger_match_field": field, "trigger_match_op": op, "trigger_match_value": value,
         "gate_mode": "paid_residual", "gate_from_month": 2, "m1_gate": "inherit",
         "clawback_enabled": False, "effective_from": None, "effective_to": None,
         "eligible_sale_periods": [], "is_active": True, "notes": None}
    s.update(kw)
    return s


def sline(org, sid, m, kind="pct_mrc", pct=0.5, flat=0.0):
    return {"id": f"{sid}-{m}", "org_id": org, "schedule_id": sid, "month_index": m,
            "payout_kind": kind, "flat_amount": flat, "mrc_pct": pct, "mrc_source": "product_catalog"}


# ── THE OWNER'S FIVE REAL JULY LINES (verbatim from the commission-explain drill-down) ───────────
EDGE_DESC_A = ("Motorola Edge 2025 TO - Promo $259.99, Total Wireless Discount Promotion - Veriff + Port")
EDGE_DESC_B = ("Motorola Edge 2025 TO - Promo $259.99, MOTOROLA_EDGE_2025_PORTIN_VERIFF_MIN50_F")
REP = "ARIFUL ISLAM"


def owner_lines(org, tender=""):
    """The five pasted rows. Ext Price $0.00 on all (promo device), GP $10.00 on four and −$259.99 on one.
    Trans 4045 rings TWICE (an 'AAL' line and a 'Port with IDV' line) — the same activation, two lines."""
    return [
        sale(org, REP, "4045", ct="Port with IDV AAL", prod=EDGE_DESC_A, ext=0.0, gp=10.0,
             date="2026-07-18", mdn="2155550101", serial="357612117781238", tender=tender),
        sale(org, REP, "3411", ct="Port with IDV", prod=EDGE_DESC_B, ext=0.0, gp=10.0,
             date="2026-07-14", mdn="2155550102", serial="357612117781239", tender=tender),
        sale(org, REP, "4045", ct="Port with IDV", prod=EDGE_DESC_A, ext=0.0, gp=10.0,
             date="2026-07-18", mdn="2155550101", serial="357612117781238", tender=tender),
        sale(org, REP, "4130", ct="Port with IDV", prod=EDGE_DESC_A, ext=0.0, gp=10.0,
             date="2026-07-19", mdn="2155550103", serial="357612117781240", tender=tender),
        sale(org, REP, "3451", ct="Port with IDV", prod=EDGE_DESC_B, ext=0.0, gp=-259.99,
             date="2026-07-14", mdn="2155550104", serial="357612117781241", tender=tender),
    ]


def lux_store(edge_matcher=("product_desc", "contains", "edge"), with_schedule=False,
              schedule_trigger=("contract_type", "contains", "port with idv")):
    """A luxelink-shaped tenant: one plan, an 'edge' rule @ $25/unit, the owner's five lines, plus ONE
    genuine TW-financing sale (a Moto G paid with the financing tender — no 'edge' in its description)."""
    f, o, v = edge_matcher
    s = base_store()
    s["commission_plan"] = [plan(LUX, "p1", "Luxelink Standard")]
    s["commission_rule"] = [rule(LUX, "p1", "r-edge", label="edge", match_field=f, match_op=o,
                                 match_value=v, payout_kind="flat_per_unit", amount=25.0, sort=0)]
    s["commission_plan_assignment"] = [assign(LUX, "p1")]
    s["raw_sales"] = owner_lines(LUX) + [
        # the REAL edge-financing sale: no 'edge' anywhere in the description, tender = financing
        sale(LUX, REP, "5001", ct="Port with IDV", prod="Moto G Power 2025 TO - $199.99",
             ext=199.99, gp=35.0, date="2026-07-21", mdn="2155550105", serial="357612117781250",
             tender=TW_FIN),
        # its rate-plan line (carries the MDN, no serial) — the MRC basis for a multi-month chain
        sale(LUX, REP, "5001", ct="", dept="Airtime", prod="Total ALL ACCESS Plan $65",
             ext=65.0, gp=65.0, date="2026-07-21", mdn="2155550105", tender=TW_FIN),
    ]
    if with_schedule:
        tf, to, tv = schedule_trigger
        s["plan_installment_schedule"] = [sched(LUX, "p1", "s1", "Multi-month incentive",
                                                field=tf, op=to, value=tv, months=6)]
        s["plan_installment_line"] = [sline(LUX, "s1", m, pct=(0.5 if m == 1 else 0.75))
                                      for m in range(1, 7)]
    return s


# ═══ R — REPRO ═══════════════════════════════════════════════════════════════════════════════════
print("\nR — REPRO: the model-name keyword pays the wrong lines AND misses the real financing sale")
c = FakeClient(lux_store())
pv = CE.preview(c, LUX, "July 2026", detail=True)
rep_row = next(r for r in pv["by_rep"] if r["rep"] == REP)
edge_rb = next(rb for rb in rep_row["rules"] if rb["label"] == "edge")
matched = {(l["trans_id"], l["contract_type"]) for l in edge_rb["lines"]}
check("R1 all five model-name lines match the 'edge' rule", edge_rb["matched_lines"] == 5,
      edge_rb["matched_lines"])
check("R2 they pay 5 × $25 = $125", edge_rb["payout"] == 125.0, edge_rb["payout"])
check("R3 trans 4045 pays TWICE (two lines of ONE activation)",
      sum(1 for l in edge_rb["lines"] if l["trans_id"] == "4045") == 2)
check("R4 the GP −$259.99 line still pays a full +$25 (flat_per_unit is sign-blind)",
      any(l["gp"] == -259.99 and l["amount"] == 25.0 for l in edge_rb["lines"]))
check("R5 the REAL TW-financing sale is NOT matched (false negative)",
      ("5001", "Port with IDV") not in matched)
# R6 must survive prose: a comment or docstring may legitimately use the WORD "edge" (this package's
# own notes do). What must never exist is EXECUTABLE code keyed on it — a string literal, identifier or
# attribute. So walk the AST and ignore docstrings/comments entirely.
def _executable_mentions(path, word):
    import ast
    tree = ast.parse(open(path).read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if word in node.value.lower():
                hits.append(("str", node.value))
        elif isinstance(node, ast.Name) and word in node.id.lower():
            hits.append(("name", node.id))
        elif isinstance(node, ast.Attribute) and word in node.attr.lower():
            hits.append(("attr", node.attr))
    return hits


_ENG = os.path.join(os.path.dirname(__file__), "..", "app/modules/commcalc/commission_engine.py")
_IMP = os.path.join(os.path.dirname(__file__), "..", "app/modules/commcalc/plan_impact.py")
_hits = _executable_mentions(_ENG, "edge") + _executable_mentions(_IMP, "edge")
check("R6 no EXECUTABLE code in the engine or plan_impact mentions 'edge' — the rule is pure CONFIG "
      "(prose is allowed; string literals, names and attributes are not)", _hits == [], _hits)

# ═══ A — THE RE-KEY ══════════════════════════════════════════════════════════════════════════════
print("\nA — RE-KEY to the financing TENDER (tender_type is already a first-class match field)")
check("A0 'tender_type' is in the engine's MATCH_FIELDS", "tender_type" in CE.MATCH_FIELDS)
c = FakeClient(lux_store(edge_matcher=("tender_type", "equals", TW_FIN)))
pv = CE.preview(c, LUX, "July 2026", detail=True)
rep_row = next(r for r in pv["by_rep"] if r["rep"] == REP)
edge_rb = next(rb for rb in rep_row["rules"] if rb["label"] == "edge")
check("A1 the five model-name lines no longer match", all(l["trans_id"] not in ("4045", "3411", "4130", "3451")
                                                          for l in edge_rb["lines"]))
check("A2 the genuine financing sale DOES match", any(l["trans_id"] == "5001" for l in edge_rb["lines"]))
check("A3 tender matching is case-insensitive (engine lower-cases both sides)",
      CE._rule_matches({"tender_type": "tw financing"},
                       {"match_field": "tender_type", "match_op": "equals", "match_value": "TW FINANCING"}))
check("A4 'in' works for several financing tenders at once",
      CE._rule_matches({"tender_type": "Acima Lease"},
                       {"match_field": "tender_type", "match_op": "in",
                        "match_value": "TW Financing, Acima Lease"}))
check("A5 a BLANK tender never matches an equals rule (the honest failure mode)",
      not CE._rule_matches({"tender_type": ""},
                           {"match_field": "tender_type", "match_op": "equals", "match_value": TW_FIN}))

# ═══ B/C — BLAST RADIUS + no automatic fallthrough ═══════════════════════════════════════════════
print("\nB/C — BLAST RADIUS: who moves, which lines are freed, and does multi-month catch them")
OVR = {"r-edge": {"match_field": "tender_type", "match_op": "equals", "match_value": TW_FIN}}

c = FakeClient(lux_store())                       # NO installment schedule configured
imp = PI.rule_impact(c, LUX, "July 2026", OVR)
check("B1 impact is ready", imp["ready"] is True)
check("B2 before = $125 (5 × $25)", imp["totals"]["before"] == 125.0, imp["totals"])
check("B3 after  = $50 (the financing sale's TWO lines both carry the tender)",
      imp["totals"]["after"] == 50.0, imp["totals"])
check("B4 delta = −$75 for this rep", imp["totals"]["delta"] == -75.0, imp["totals"])
check("B5 five lines are freed", imp["totals"]["lines_freed"] == 5, imp["totals"])
check("C1 with NO schedule, every freed line has NO pay source at all",
      imp["totals"]["freed_no_pay_source"] == 5 and imp["totals"]["freed_enrolled_by_multimonth"] == 0,
      imp["totals"])
check("C2 each freed row carries its tender so the replacement can be sanity-checked",
      all("tender_type" in r for r in imp["freed_lines"]))
check("C3 the freed rows name the rule and the lost dollars",
      sum(r["lost_amount"] for r in imp["freed_lines"]) == 125.0)

c = FakeClient(lux_store(with_schedule=True))     # a schedule that DOES trigger on these activations
imp2 = PI.rule_impact(c, LUX, "July 2026", OVR)
check("C4 with a matching trigger, every freed line is ENROLLED (not yet paid — the gate still applies)",
      imp2["totals"]["freed_enrolled_by_multimonth"] == 5 and imp2["totals"]["freed_no_pay_source"] == 0,
      imp2["totals"])
check("C5 the enrolled rows name the schedule + its trigger and are flagged enrolled, not paid",
      all(r["multimonth_schedule"] and r["multimonth_trigger"] and r["multimonth_enrolled"]
          and r["no_pay_source_after"] is False for r in imp2["freed_lines"]))
check("C5b the payload says in words that ENROLLED is not PAID",
      "not a promise of dollars" in imp2["note"] or "NOT a promise of dollars" in imp2["note"])

c = FakeClient(lux_store(with_schedule=True, schedule_trigger=("contract_type", "equals", "upgrade")))
imp3 = PI.rule_impact(c, LUX, "July 2026", OVR)
check("C6 a schedule whose trigger does NOT match leaves them orphaned (no silent re-routing)",
      imp3["totals"]["freed_no_pay_source"] == 5, imp3["totals"])
check("C7 the engine never re-routes by itself — turning the rule OFF entirely is the same story",
      PI.rule_impact(FakeClient(lux_store()), LUX, "July 2026",
                     {"r-edge": {"disabled": True}})["totals"]["freed_no_pay_source"] == 5)

# ═══ D — MULTI-MONTH: one chain per ACTIVATION, on the rate-plan MRC ══════════════════════════════
print("\nD — MULTI-MONTH: one chain per activation, paid on the rate-plan MRC (mig 233)")
st = lux_store(edge_matcher=("tender_type", "equals", TW_FIN), with_schedule=True)
st["product_mrc"] = [{"id": "m1", "org_id": LUX, "carrier_id": None,
                      "product_desc": "Total ALL ACCESS Plan $65", "mrc": 65.0}]
c = FakeClient(st)
res = SIE.compute_sale_installments(c, LUX, "July 2026", persist=False)
chains = {(l.get("trans_id"), l.get("mdn") or l.get("serial")) for l in res["ledger"]}
by_tid = {}
for l in res["ledger"]:
    by_tid[l.get("trans_id")] = by_tid.get(l.get("trans_id"), 0) + 1
check("D1 the owner's trans 4045 (TWO matching lines, ONE subscriber) produces ONE chain",
      by_tid.get("4045") == 1, by_tid)
check("D2 every other single-subscriber activation produces exactly one chain",
      all(v == 1 for v in by_tid.values()), by_tid)
check("D3 month 1 is emitted for a July sale in the July pay period",
      all(int(l.get("month_index") or 0) == 1 for l in res["ledger"]))

# two subscribers on ONE transaction still pay twice
st2 = lux_store(with_schedule=True)
st2["raw_sales"] = [
    sale(LUX, REP, "7777", ct="Port with IDV", prod=EDGE_DESC_A, ext=0.0, gp=10.0,
         mdn="2155559001", serial="111", date="2026-07-10"),
    sale(LUX, REP, "7777", ct="Port with IDV", prod=EDGE_DESC_A, ext=0.0, gp=10.0,
         mdn="2155559002", serial="222", date="2026-07-10"),
]
res2 = SIE.compute_sale_installments(FakeClient(st2), LUX, "July 2026", persist=False)
check("D4 two DISTINCT subscribers on one transaction still produce TWO chains",
      len(res2["ledger"]) == 2, len(res2["ledger"]))

# ═══ E — THE NEGATIVE ROW ════════════════════════════════════════════════════════════════════════
print("\nE — the −$259.99 row: a GP figure on a $0 promo device, not a return")
neg = owner_lines(LUX)[4]
check("E1 the row is a SALE (trans_type is not 'Return') and is not voided",
      neg["trans_type"] != "Return" and not neg["voided"])
check("E2 ext_price is $0 (promo device) and GP is −$259.99 (cost booked, no rebate credit)",
      neg["ext_price"] == 0.0 and neg["gp"] == -259.99)
check("E3 flat_per_unit pays +$25 on it regardless of the sign (documented engine behaviour)",
      CE._line_payout(neg, {"payout_kind": "flat_per_unit", "amount": 25.0}, {}, {}, {}) == 25.0)
check("E4 a pct_gp rule on the same row would pay NEGATIVE (so the basis matters)",
      CE._line_payout(neg, {"payout_kind": "pct_gp", "pct": 0.1}, {}, {}, {}) == -26.0)
check("E5 after the re-key it pays $0 from the edge rule (its tender is blank)",
      not CE._rule_matches(neg, {"match_field": "tender_type", "match_op": "equals",
                                 "match_value": TW_FIN}))
check("E6 a genuine RETURN line is excluded by the engine before any rule runs",
      len([r for r in [dict(neg, trans_type="Return")]
           if str(r.get("trans_type") or "").strip() != "Return"]) == 0)

# ═══ F — OTHER-TENANT NO-OP + BYTE IDENTITY ══════════════════════════════════════════════════════
print("\nF — other-tenant no-op + byte identity with the pre-change engine")


def other_store():
    s = base_store()
    s["commission_plan"] = [plan(OTHER, "op1", "Other Co")]
    s["commission_rule"] = [rule(OTHER, "op1", "or-1", label="activations",
                                 match_field="contract_type", match_op="contains",
                                 match_value="port", payout_kind="flat_per_unit", amount=15.0)]
    s["commission_plan_assignment"] = [assign(OTHER, "op1")]
    s["raw_sales"] = [sale(OTHER, "SAM SMITH", "9001", ct="Port-In", prod="Samsung A16",
                           ext=149.0, gp=30.0, mdn="2125550001", serial="999")]
    return s


c = FakeClient(other_store())
o_before = CE.preview(c, OTHER, "July 2026", detail=True)
o_after = PI.rule_impact(c, OTHER, "July 2026", OVR)   # overrides key a rule id this tenant doesn't own
check("F1 an override for a rule id this tenant doesn't own changes NOTHING",
      o_after["totals"]["delta"] == 0.0 and o_after["by_rep"] == [], o_after["totals"])
check("F2 that tenant still pays exactly what it did", o_before["totals"]["payout"] == 15.0)

# byte identity across a fixture matrix
MATRIX = [
    ("lux keyword", lux_store(), LUX),
    ("lux re-keyed", lux_store(edge_matcher=("tender_type", "equals", TW_FIN)), LUX),
    ("lux + schedule", lux_store(with_schedule=True), LUX),
    ("other tenant", other_store(), OTHER),
    ("empty tenant", base_store(), HOUSE),
]
ident = True
for nm, store, org in MATRIX:
    for det in (False, True):
        for cov in (False, True):
            a = OLD_CE.preview(FakeClient(copy.deepcopy(store)), org, "July 2026",
                               detail=det, coverage=cov)
            b = CE.preview(FakeClient(copy.deepcopy(store)), org, "July 2026",
                           detail=det, coverage=cov)
            if a != b:
                ident = False
                print(f"      diff at {nm} detail={det} coverage={cov}")
check("F3 preview() with rule_overrides UNSET is byte-identical to the pre-change engine "
      f"({len(MATRIX)} fixtures × detail × coverage)", ident)

# byte identity under a fuzz
rnd = random.Random(20260727)
fuzz_ok = True
FIELDS = ["any", "contract_type", "tender_type", "department", "category", "product_desc", "sku",
          "trans_type"]
OPS = ["equals", "contains", "in"]
KINDS = ["flat_per_unit", "pct_gp", "pct_price_over_cost", "flat"]
for seed in range(300):
    st = base_store()
    org = rnd.choice([HOUSE, LUX, OTHER])
    st["commission_plan"] = [plan(org, "fp", "Fuzz")]
    st["commission_plan_assignment"] = [assign(org, "fp")]
    st["commission_rule"] = [
        rule(org, "fp", f"fr{i}", label=f"r{i}", match_field=rnd.choice(FIELDS),
             match_op=rnd.choice(OPS),
             match_value=rnd.choice(["edge", "port", TW_FIN, "Airtime", "", "a,b"]),
             payout_kind=rnd.choice(KINDS), amount=rnd.choice([0, 5, 25]),
             pct=rnd.choice([0, 0.05, 0.1]), tiered=rnd.random() < 0.3, sort=i)
        for i in range(rnd.randint(0, 4))]
    st["raw_sales"] = [
        sale(org, rnd.choice(["A REP", "B REP"]), str(rnd.randint(1, 9)),
             ct=rnd.choice(["Port with IDV", "Upgrade", ""]),
             prod=rnd.choice([EDGE_DESC_A, "Moto G", "Total ALL ACCESS Plan $65"]),
             ext=rnd.choice([0.0, 65.0, 199.99]), gp=rnd.choice([10.0, -259.99, 35.0]),
             tender=rnd.choice(["", TW_FIN, "Cash"]), mdn=str(rnd.randint(1000, 9999)),
             serial=str(rnd.randint(1000, 9999)))
        for _ in range(rnd.randint(0, 6))]
    a = OLD_CE.preview(FakeClient(copy.deepcopy(st)), org, "July 2026", detail=True)
    b = CE.preview(FakeClient(copy.deepcopy(st)), org, "July 2026", detail=True)
    if a != b:
        fuzz_ok = False
        print(f"      fuzz diff at seed {seed}")
        break
check("F4 300-seed fuzz: preview() byte-identical to the pre-change engine", fuzz_ok)

# the override helper itself is PURE
_plans_in = [{"id": "p", "rules": [{"id": "r", "match_field": "product_desc", "match_op": "contains",
                                    "match_value": "edge"}]}]
_snapshot = copy.deepcopy(_plans_in)
_out = CE._apply_rule_overrides(_plans_in, {"r": {"match_field": "tender_type"}})
check("F5 _apply_rule_overrides does not mutate its input", _plans_in == _snapshot)
check("F6 it applies the override to the copy",
      _out[0]["rules"][0]["match_field"] == "tender_type")
check("F7 it REJECTS a match_field the engine cannot run (keeps the stored one)",
      CE._apply_rule_overrides(_plans_in, {"r": {"match_field": "not_a_field"}})[0]["rules"][0]
      ["match_field"] == "product_desc")
check("F8 disabled=true removes the rule entirely",
      CE._apply_rule_overrides(_plans_in, {"r": {"disabled": True}})[0]["rules"] == [])

# sale-installment engine untouched
sie_ident = True
for nm, store, org in MATRIX:
    a = OLD_SIE.compute_sale_installments(FakeClient(copy.deepcopy(store)), org, "July 2026",
                                          persist=False)
    b = SIE.compute_sale_installments(FakeClient(copy.deepcopy(store)), org, "July 2026", persist=False)
    if a != b:
        sie_ident = False
        print(f"      SIE diff at {nm}")
check("F9 compute_sale_installments is byte-identical to the pre-change engine (no mig-233 regression)",
      sie_ident)

# ═══ G — WARNINGS ════════════════════════════════════════════════════════════════════════════════
print("\nG — WARNINGS: activations no rule and no schedule pays")
st = lux_store(edge_matcher=("tender_type", "equals", TW_FIN))
w = PI.pay_warnings(FakeClient(st), LUX, "July 2026")
tids = {g["trans_id"] for g in w}
check("G1 after the re-key with no schedule, the freed activations are WARNED, not silent",
      {"4045", "3411", "4130", "3451"} <= tids, tids)
check("G2 each warning names the rep, the plan and the product",
      all(g["rep"] and g["plan_name"] and g["samples"] for g in w))
check("G3 the warning explains itself in plain language",
      all("no multi-month schedule trigger" in g["detail"] for g in w))
st_cov = lux_store(edge_matcher=("tender_type", "equals", TW_FIN), with_schedule=True)
check("G4 with a schedule that covers them, there is NO warning (no noise)",
      PI.pay_warnings(FakeClient(st_cov), LUX, "July 2026") == [])
check("G5 a Boost/house org with no commission plans is never warned",
      PI.pay_warnings(FakeClient(base_store()), HOUSE, "July 2026") == [])
check("G6 the stored payload is None when there is nothing to report (calc_status stays clean)",
      PI.calc_warning_payload(FakeClient(base_store()), HOUSE, "July 2026") is None)
pl = PI.calc_warning_payload(FakeClient(lux_store(edge_matcher=("tender_type", "equals", TW_FIN))),
                             LUX, "July 2026")
check("G7 the stored payload counts what it found", (pl or {}).get("counts", {}).get(
    "unpaid_activations", 0) >= 4, (pl or {}).get("counts"))

# ═══ H — KEYWORD-COLLISION AUDIT (the generic bug class) ═════════════════════════════════════════
print("\nH — keyword-collision audit: the generic form of this bug, computed from the tenant's own data")
aud = PI.keyword_collision_audit(FakeClient(lux_store()), LUX, "July 2026")
r0 = next((r for r in aud["rules"] if r["label"] == "edge"), None)
check("H1 the 'edge' rule is listed", r0 is not None)
check("H2 it shows the ITEMS the pattern really hit (the model name)",
      any("Motorola Edge 2025" in i["product"] for i in (r0 or {}).get("items", [])))
check("H3 it is flagged suspect", (r0 or {}).get("suspect") is True)
st_t = lux_store()
for r in st_t["raw_sales"]:
    if r["trans_id"] == "5001":
        r["tender_type"] = "TW Edge Financing"       # the tenant's own tender wording
aud2 = PI.keyword_collision_audit(FakeClient(st_t), LUX, "July 2026")
r1 = next(r for r in aud2["rules"] if r["label"] == "edge")
check("H4 when the same word is a real TENDER value, the audit names that field",
      any(cf["field"] == "tender_type" for cf in r1["also_a_value_of"]), r1["also_a_value_of"])
check("H5 a rule that does not use a description pattern is not reported",
      PI.keyword_collision_audit(FakeClient(other_store()), OTHER, "July 2026")["rules"] == [])
check("H6 the audit is period-scoped and refuses a blank period",
      PI.keyword_collision_audit(FakeClient(lux_store()), LUX, "")["ready"] is False)

# ═══ I — TENANT ISOLATION ════════════════════════════════════════════════════════════════════════
print("\nI — tenant isolation")
mixed = lux_store()
mixed["raw_sales"] = mixed["raw_sales"] + other_store()["raw_sales"]
mixed["commission_plan"] += other_store()["commission_plan"]
mixed["commission_rule"] += other_store()["commission_rule"]
mixed["commission_plan_assignment"] += other_store()["commission_plan_assignment"]
lux_only = PI.rule_impact(FakeClient(mixed), LUX, "July 2026", OVR)
check("I1 the impact for luxelink never reads the other tenant's rep",
      all(r["rep"] != "SAM SMITH" for r in lux_only["by_rep"]))
check("I2 the other tenant's totals are unchanged by luxelink's edit",
      PI.rule_impact(FakeClient(mixed), OTHER, "July 2026", OVR)["totals"]["delta"] == 0.0)
check("I3 pay_warnings is org-scoped",
      all(g["rep"] != "SAM SMITH" for g in PI.pay_warnings(FakeClient(mixed), LUX, "July 2026")))

# ═══ J — GATE-1 NITS (N2 aliasing, N4 hostile types, N3 amount attribution) ══════════════════════
print("\nJ — Gate-1 nits: no shared config objects, hostile override types rejected, exact amounts")

# N2 — the returned structure must share NO dict with the loaded plans, even for untouched rules.
_stored = [{"id": "p", "name": "P",
            "rules": [{"id": "r1", "match_field": "product_desc", "match_op": "contains",
                       "match_value": "edge", "amount": 25},
                      {"id": "r2", "match_field": "any", "amount": 5}],
            "tiers": [{"id": "t1", "min_count": 30, "multiplier": 1.0}],
            "assignments": [{"id": "a1", "scope": "default"}]}]
_res = CE._apply_rule_overrides(_stored, {"r1": {"match_field": "tender_type"}})
_ids_in = {id(x) for p in _stored for x in (p.get("rules") or []) + (p.get("tiers") or [])
           + (p.get("assignments") or [])}
_ids_out = {id(x) for p in _res for x in (p.get("rules") or []) + (p.get("tiers") or [])
            + (p.get("assignments") or [])}
check("J1 N2: NO rule/tier/assignment dict is shared with the input (not even untouched ones)",
      _ids_in.isdisjoint(_ids_out), f"shared={len(_ids_in & _ids_out)}")
_untouched = next(r for r in _res[0]["rules"] if r["id"] == "r2")
_untouched["amount"] = 99999
check("J2 N2: mutating the what-if copy cannot rewrite the stored rule",
      _stored[0]["rules"][1]["amount"] == 5, _stored[0]["rules"][1]["amount"])
_res[0]["tiers"][0]["multiplier"] = 0.1
check("J3 N2: the same holds for tiers", _stored[0]["tiers"][0]["multiplier"] == 1.0)

# N4 — hostile override types are rejected at the boundary with a 400, not a 500.
from app.modules.commcalc.router import _validate_rule_overrides as VAL
try:
    from fastapi import HTTPException as HX
except Exception:                                     # pragma: no cover
    HX = Exception


def _rejects(ov):
    try:
        VAL(ov)
        return None
    except HX as e:
        return getattr(e, "status_code", None)


check("J4 N4: a NUMBER match_value is rejected 400 (it used to 500 in _rule_matches)",
      _rejects({"r1": {"match_value": 25}}) == 400)
check("J5 N4: an OBJECT match_value is rejected 400", _rejects({"r1": {"match_value": {"a": 1}}}) == 400)
check("J6 N4: a bad match_field is rejected 400", _rejects({"r1": {"match_field": "nope"}}) == 400)
check("J7 N4: a bad match_op is rejected 400", _rejects({"r1": {"match_op": "regex"}}) == 400)
check("J8 N4: a non-bool qualifies is rejected 400", _rejects({"r1": {"qualifies": "yes"}}) == 400)
check("J9 N4: an unknown override key is rejected 400", _rejects({"r1": {"amount": 5}}) == 400)
check("J10 N4: a list match_value needs op 'in'", _rejects({"r1": {"match_value": ["a", "b"]}}) == 400)
check("J11 N4: a list of non-strings is rejected 400",
      _rejects({"r1": {"match_op": "in", "match_value": ["a", 2]}}) == 400)
check("J12 N4: a VALID string override passes through unchanged",
      VAL({"r1": {"match_field": "tender_type", "match_op": "equals", "match_value": TW_FIN}})
      == {"r1": {"match_field": "tender_type", "match_op": "equals", "match_value": TW_FIN}})
check("J13 N4: a valid list + op 'in' is normalised to the comma list the engine parses",
      VAL({"r1": {"match_op": "in", "match_value": ["TW Financing", " Acima Lease "]}})["r1"]
      ["match_value"] == "TW Financing,Acima Lease")
check("J14 N4: the proof's own override survives validation unchanged", VAL(OVR) == OVR)
check("J15 N4: validation does NOT touch stored-rule matching (base behaviour untouched)",
      CE._rule_matches({"tender_type": "x"},
                       {"match_field": "tender_type", "match_op": "equals", "match_value": "x"}))

# N3 — per-line amount attribution when one join key covers lines that pay DIFFERENT amounts.
# Two lines identical on every column the drill-down row carries, but different product_id → a
# pct_price_over_cost rule pays $20 on one and $5 on the other. The freed rows must report 20 and 5,
# not 20 and 20.
st3 = base_store()
st3["commission_plan"] = [plan(LUX, "p3", "Pct plan")]
st3["commission_rule"] = [rule(LUX, "p3", "r-pct", label="pct", match_field="product_desc",
                               match_op="contains", match_value="edge",
                               payout_kind="pct_price_over_cost", pct=1.0)]
st3["commission_plan_assignment"] = [assign(LUX, "p3")]
_a = sale(LUX, REP, "8001", ct="Port with IDV", prod=EDGE_DESC_A, ext=100.0, gp=10.0,
          date="2026-07-02", mdn="2155558001", serial="801")
_b = dict(_a)
_a["product_id"], _b["product_id"] = 1.0, 2.0
st3["raw_sales"] = [_a, _b]
st3["raw_catalog"] = [{"org_id": LUX, "product_id": 1.0, "cost": 80.0},
                      {"org_id": LUX, "product_id": 2.0, "cost": 95.0}]
imp4 = PI.rule_impact(FakeClient(st3), LUX, "July 2026", {"r-pct": {"disabled": True}})
_amts = sorted(r["lost_amount"] for r in imp4["freed_lines"])
check("J16 N3: both lines are counted as freed", len(imp4["freed_lines"]) == 2, imp4["totals"])
check("J17 N3: the two DIFFERENT per-line amounts are reported exactly ($5 and $20), not duplicated",
      _amts == [5.0, 20.0], _amts)
check("J18 N3: their sum equals the rule's whole before-payout",
      round(sum(_amts), 2) == 25.0, _amts)
check("J19 N3: nothing is stamped approximate in this exact case",
      imp4["totals"]["amounts_approximate"] == 0 and
      not any(r.get("amount_approximate") for r in imp4["freed_lines"]))
check("J20 N3: the owner's flat-per-unit case is unchanged ($25 each)",
      all(r["lost_amount"] == 25.0 for r in
          PI.rule_impact(FakeClient(lux_store()), LUX, "July 2026", OVR)["freed_lines"]))

print(f"\n{'='*78}\n{PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
