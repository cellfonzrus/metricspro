"""Proof for agent/commission/edge-per-sale-dedup — OWNER DIRECTIVES 2026-08-01. MONEY-TOUCHING.

① EDGE UNIT DEDUP     "one trans id but paying out multiple times for the edge sale, one imie ca be
                       paid only once for the edge sale" + "any accessory or rate plan wil not paid
                       for the edge sale"
② PAYOUT EXCLUSION    "there shgould be no paymentfor any rtr trasactions , again nothing hardocded,
                       but with mapping, map it in teh back end but let the user define going forward"
③ RULE SCOPE          "All activations are being paid $10 flat , this is only for NY employees, but
                       this empluee is in Chicago."
⑤ ACCESSORY BASIS     "accessories not being paid , they should be paid as all of these have been
                       mapped"

THE DEFECT, ROOT-CAUSED (both halves, and they are inseparable)
  (a) CLASSIFIER GRANULARITY. The owner's `edge` rule was re-keyed to the sale's TENDER on 2026-07-27
      (commit 5aa2ff6, "edge is a FINANCING tender, not a phone model"). `tender_type` is a
      TRANSACTION-level attribute — the POS stamps the same tender on every line of the receipt — so
      the rule correctly matches the transaction and, as a side effect, matches all EIGHT of its lines.
  (b) PAYOUT GRANULARITY. `commission_engine._line_payout` returns the flat amount for EVERY matching
      line and `preview()` adds it up per line, with no dedup by device or transaction
      (commission_engine.py `_line_payout` / the rule loop inside `preview`). "flat_per_unit" has
      always meant "flat per matching LINE".
  Neither half is wrong alone; together they paid one financed sale 8 x $25 = $200.

  The engine ALREADY knew this hazard and had solved it once: `_activation_buckets()` collapses a
  rescued activation to ONE representative line, with the comment "stamping every line of it would
  make a flat-per-unit rule pay 2-3x for ONE activation". That collapse only covers the mig-224 rescue
  path, not a tender-keyed rule. This package generalises it.

Run:  cd backend && python3 scratchpad/edge_unit_dedup_proof.py
"""
import os
import sys
import copy
import json
import random
import subprocess
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.modules.commcalc.commission_engine as CE
import app.modules.commcalc.plan_pay_gate as GATE

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"

# The tenant's own financing-tender label. Only the MECHANISM is asserted anywhere below.
TW_FIN = "TW Financing"

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


# ── THE OWNER'S TRANSACTION, verbatim (luxelink, 2026-07-12, trans 3207) ─────────────────────────
# bucket edge · $/unit · $25.00 on EVERY line. IMEI is on the financed handset line only — every other
# line of this receipt is a rate plan, a fee, an accessory, a protection plan or a wallet load.
REP = "ESPINOZA, CAROLINA"
IMEI_A = "352915117781238"       # 15 digits -> serial_kind 'imei'
IMEI_B = "356938035643809"


def owner_trans_3207(org, tender=TW_FIN, tid="3207"):
    return [
        sale(org, REP, tid, ct="Internal Port with IDV", prod="Total ALL ACCESS Plan $65",
             ext=0.0, gp=32.50, tender=tender, dept="RatePlan", cat="Rate Plans"),
        sale(org, REP, tid, ct="Activation", prod="Activation payment",
             ext=0.0, gp=-66.80, tender=tender, dept="Fees", cat="Fees"),
        sale(org, REP, tid, ct="Internal Port with IDV",
             prod=("Apple iPhone 16e 128GB Black TO - Total Wireless Edge Promotional Credit by Glow "
                   "Financial Services Activation; Monthly Credit Amount: $25; Total Subsidy Amount: "
                   "$599.99; Term Length: 24. Final sale..."),
             ext=599.99, gp=20.0, serial=IMEI_A, mdn="7736481456", tender=tender,
             dept="Handset", cat="BrandedHandset"),
        sale(org, REP, tid, prod="Case BYOD", ext=29.99, gp=0.0, tender=tender,
             dept="Handset", cat="Accessories"),
        sale(org, REP, tid, prod="Access Charge - $25 for single line, max $50 for multiple lines.",
             ext=25.0, gp=12.50, tender=tender, dept="Fees", cat="Fees"),
        sale(org, REP, tid, ct="Internal Port with IDV", prod="Total Wireless Protect+",
             ext=0.0, gp=0.0, tender=tender, dept="Protection", cat="Protection"),
        sale(org, REP, tid, prod="Screen Protectors BYOD", ext=24.99, gp=0.0, tender=tender,
             dept="Handset", cat="Accessories"),
        sale(org, REP, tid, prod="Wallet Funding", ext=73.0, gp=73.0, tender=tender,
             dept="Fees", cat="Fees"),
    ]


def edge_plan_store(org=LUX, tender=TW_FIN, extra_sales=(), rules_extra=(), org_cfg=None,
                    with_exclusion_table=False):
    """The tenant's real shape: ONE plan, the edge rule keyed on the TENDER at $25/unit."""
    s = base_store()
    s["commission_plan"] = [plan(org, "p1", "Total Wireless")]
    s["commission_rule"] = [rule(org, "p1", "r-edge", label="edge", match_field="tender_type",
                                 match_op="contains", match_value="edge",
                                 payout_kind="flat_per_unit", amount=25)] + list(rules_extra)
    s["commission_plan_assignment"] = [assign(org, "p1", "default")]
    s["raw_sales"] = owner_trans_3207(org, tender) + list(extra_sales)
    if org_cfg is not None:
        s["commission_org_config"] = [{"org_id": org, **org_cfg}]
    if with_exclusion_table:
        s[GATE.EXCLUSION_TABLE] = []
    return s


def payout(store, org=LUX, period="July 2026", engine=CE, **kw):
    c = FakeClient(store)
    r = engine.preview(c, org, period, **kw)
    return r, c


def rep_total(res, rep=REP):
    for r in res.get("by_rep") or []:
        if r.get("rep") == rep:
            return round(r.get("total_payout") or 0, 2)
    return 0.0


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── R. REPRO — the owner's transaction on the BASE engine ────────────────────────────────")
# The edge rule's tender value is 'contains edge'; the POS tender string is the tenant's own.
S = edge_plan_store(tender="TW Edge Financing")
base_res, _ = payout(S, engine=OLD_CE)
check("R1  base engine pays the owner's single transaction 8 x $25 = $200.00",
      rep_total(base_res) == 200.0, f"got {rep_total(base_res)}")
_rb = (base_res["by_rep"][0]["rules"] or [])[0]
check("R2  base engine reports 8 matched lines and 8 qualifying units on ONE rule",
      _rb["matched_lines"] == 8 and _rb["qualifying_units"] == 8, json.dumps(_rb, default=str)[:200])
check("R3  the multiplication is per LINE, not per device: 8 lines but only 1 carries an IMEI",
      sum(1 for r in S["raw_sales"] if r.get("serial_1")) == 1)

print("\n── A. THE FIX — one payment per device, on the device line ──────────────────────────────")
S = edge_plan_store(tender="TW Edge Financing")
new_res, cl = payout(S)
check("A1  fixed engine pays the SAME transaction exactly $25.00 (one device, one payment)",
      rep_total(new_res) == 25.0, f"got {rep_total(new_res)}")
check("A2  delta for this rep on this transaction is -$175.00",
      round(rep_total(base_res) - rep_total(new_res), 2) == 175.0)
_rb = (new_res["by_rep"][0]["rules"] or [])[0]
check("A3  all 8 lines still show as MATCHED (nothing vanishes from the drill-down)",
      _rb["matched_lines"] == 8, str(_rb.get("matched_lines")))
check("A4  qualifying units drop 8 -> 1 (the tier metric counts one activation, not eight lines)",
      _rb["qualifying_units"] == 1, str(_rb.get("qualifying_units")))
check("A5  the engine wrote NOTHING (zero insert/update/upsert/delete)", cl.writes == [], str(cl.writes))

print("\n── B. OWNER RULE 2 — the payment lands on the DEVICE line, never on an accessory/rate plan ─")
_det, _ = payout(edge_plan_store(tender="TW Edge Financing"), detail=True)
_lines = ((_det["by_rep"][0]["rules"] or [])[0]).get("lines") or []
_paid = [l for l in _lines if (l.get("amount") or 0) > 0]
check("B1  exactly ONE line pays", len(_paid) == 1, str([l.get("product") for l in _paid]))
check("B2  the paying line is the financed handset (the one carrying the IMEI)",
      _paid and _paid[0].get("imei") == IMEI_A, str(_paid[0].get("product"))[:80] if _paid else "-")
_names = {str(l.get("product") or "")[:28] for l in _lines if l.get("suppressed")}
for want in ("Total ALL ACCESS Plan $65", "Activation payment", "Case BYOD", "Total Wireless Protect+",
             "Screen Protectors BYOD", "Wallet Funding", "Access Charge - $25 for sing"):
    check(f"B3  suppressed and shown with a reason: {want[:28]!r}",
          want[:28] in _names, str(sorted(_names))[:200])
check("B4  every suppressed line carries would_have_paid = $25.00 (the money is stated, not hidden)",
      all(round(l.get("would_have_paid") or 0, 2) == 25.0 for l in _lines if l.get("suppressed")))
check("B5  every suppressed line carries a human reason string",
      all(isinstance(l.get("suppressed_reason"), str) and l["suppressed_reason"]
          for l in _lines if l.get("suppressed")))
check("B6  pay_gate reports 1 collapsed transaction, 7 suppressed lines, $175.00",
      (new_res.get("pay_gate") or {}).get("unit", {}).get("transactions") == 1
      and new_res["pay_gate"]["unit"]["lines_suppressed"] == 7
      and new_res["pay_gate"]["unit"]["amount_suppressed"] == 175.0,
      json.dumps((new_res.get("pay_gate") or {}).get("unit"), default=str)[:250])
check("B7  pay_gate is a TOP-LEVEL key, deliberately NOT inside totals",
      "pay_gate" in new_res and "pay_gate" not in new_res["totals"])

print("\n── C. MULTI-IMEI — two financed devices on ONE transaction pay TWICE ────────────────────")
S = edge_plan_store(tender="TW Edge Financing")
S["raw_sales"].append(sale(LUX, REP, "3207", prod="Apple iPhone 16e 128GB Blue TO - Edge Promotional "
                                                  "Credit by Glow Financial Services Activation",
                           ext=599.99, gp=20.0, serial=IMEI_B, mdn="7736481457",
                           tender="TW Edge Financing", dept="Handset", cat="BrandedHandset"))
two_res, _ = payout(S)
check("C1  two devices on one transaction pay exactly 2 x $25 = $50.00",
      rep_total(two_res) == 50.0, f"got {rep_total(two_res)}")
base_two, _ = payout(S, engine=OLD_CE)
check("C2  the base engine paid 9 x $25 = $225.00 for the same fixture",
      rep_total(base_two) == 225.0, f"got {rep_total(base_two)}")

print("\n── D. DUPLICATE / RE-RUN — the same IMEI twice still pays once ──────────────────────────")
S = edge_plan_store(tender="TW Edge Financing")
_dup = dict(S["raw_sales"][2])          # byte-copy of the device line (a re-ingested duplicate)
S["raw_sales"].append(_dup)
dup_res, _ = payout(S)
check("D1  a duplicated device LINE (same trans, same IMEI) still pays exactly $25.00",
      rep_total(dup_res) == 25.0, f"got {rep_total(dup_res)}")
S2 = edge_plan_store(tender="TW Edge Financing")
S2["raw_sales"].append(dict(S2["raw_sales"][2], ext_price=1.0))   # same IMEI, different price
check("D2  same IMEI at a different price still pays once (dedup keys on the device, not the price)",
      rep_total(payout(S2)[0]) == 25.0, f"got {rep_total(payout(S2)[0])}")
S3 = edge_plan_store(tender="TW Edge Financing")
S3["raw_sales"] += owner_trans_3207(LUX, "TW Edge Financing", tid="3208")
check("D3  TWO separate financed sales pay 2 x $25 = $50.00 (dedup is per transaction, not per month)",
      rep_total(payout(S3)[0]) == 50.0, f"got {rep_total(payout(S3)[0])}")

print("\n── E. THE MISSING-IMEI DATA GAP — never silently zero a real sale ───────────────────────")
S = edge_plan_store(tender="TW Edge Financing")
for r in S["raw_sales"]:
    r["serial_1"] = ""
gap_res, _ = payout(S)
check("E1  a transaction whose device line lost its IMEI still pays ONCE ($25.00), not $0",
      rep_total(gap_res) == 25.0, f"got {rep_total(gap_res)}")
_n = [n for n in (gap_res.get("pay_gate") or {}).get("unit", {}).get("notes", [])
      if n.get("code") == "unit_no_device_id"]
check("E2  ...and says so loudly (unit_no_device_id note naming the transaction)",
      len(_n) == 1 and _n[0].get("trans_id") == "3207", str(_n)[:200])
S = edge_plan_store(tender="TW Edge Financing",
                    org_cfg={"plan_pay_gate": {"unit_basis": {"no_unit_fallback": "skip"}}})
for r in S["raw_sales"]:
    r["serial_1"] = ""
check("E3  a tenant that sets no_unit_fallback='skip' pays $0 for it instead (their choice, config)",
      rep_total(payout(S)[0]) == 0.0, f"got {rep_total(payout(S)[0])}")

print("\n── F. CONFIG, NOT CODE (RULE TWO) ───────────────────────────────────────────────────────")
S = edge_plan_store(tender="TW Edge Financing",
                    org_cfg={"plan_pay_gate": {"unit_basis": {"enabled": False}}})
check("F1  a tenant that switches the dedup OFF gets the old $200.00 back (nothing is imposed)",
      rep_total(payout(S)[0]) == 200.0, f"got {rep_total(payout(S)[0])}")
S = edge_plan_store(tender="TW Edge Financing",
                    org_cfg={"plan_pay_gate": {"unit_basis": {"auto_txn_level_fields": []}}})
check("F2  emptying auto_txn_level_fields also restores $200.00 (the trigger is config, not a name)",
      rep_total(payout(S)[0]) == 200.0, f"got {rep_total(payout(S)[0])}")
S = edge_plan_store(tender="TW Edge Financing")
S["commission_rule"][0]["unit_basis"] = "per_line"
check("F3  a per-RULE unit_basis='per_line' overrides the auto-detection -> $200.00",
      rep_total(payout(S)[0]) == 200.0, f"got {rep_total(payout(S)[0])}")
S = edge_plan_store(tender="TW Edge Financing")
S["commission_rule"][0]["unit_basis"] = "per_transaction"
check("F4  unit_basis='per_transaction' pays once per sale regardless of device count -> $25.00",
      rep_total(payout(S)[0]) == 25.0, f"got {rep_total(payout(S)[0])}")
S = edge_plan_store(tender="TW Edge Financing")
S["commission_rule"][0]["match_field"] = "department"
S["commission_rule"][0]["match_value"] = "handset"
S["commission_rule"][0]["match_op"] = "equals"
check("F5  the SAME rule keyed on a LINE-level field is NOT deduped (3 handset-dept lines -> $75)",
      rep_total(payout(S)[0]) == 75.0, f"got {rep_total(payout(S)[0])}")
_GPATH = os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                      "plan_pay_gate.py")
_src = open(_GPATH, encoding="utf-8").read()
_body = _src.split('"""', 2)[2]      # strip the module docstring (which quotes the owner verbatim)
for lit in ("luxelink", "boost", "total wireless", "glow", "iphone", "854f6d7b"):
    check(f"F6  no hard-coded tenant/carrier/product literal in the gate's code: {lit!r}",
          lit not in _body.lower(), "found")


def _executable_strings_and_names(path):
    """Every STRING CONSTANT and IDENTIFIER the interpreter actually executes — docstrings and
    comments excluded. A word that survives this filter is a real branch; a word that does not is
    documentation."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                docs.add(d)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docs:
                out.append(node.value)
        elif isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
    return out


_exec_tokens = _executable_strings_and_names(_GPATH)
check("F7  'edge' appears in NO executable string or identifier — it is documentation only",
      not any("edge" in t.lower() for t in _exec_tokens),
      str([t for t in _exec_tokens if "edge" in t.lower()])[:200])
check("F7b the same test on the tenant/product literals (executable tokens only)",
      not any(any(w in t.lower() for w in ("luxelink", "glow", "iphone", "boost"))
              for t in _exec_tokens),
      str([t for t in _exec_tokens if "glow" in t.lower()])[:200])
check("F8  the code default is a NAMED constant, not a literal buried in a branch",
      "UNIT_DEFAULTS" in _body and "GATE_DEFAULTS" in _body)

print("\n── G. ZERO CHANGE ELSEWHERE — the differential ──────────────────────────────────────────")


def _drop_gate(o):
    """Remove the additive keys this package introduces, so what remains is comparable byte-for-byte
    with the BASE engine's answer."""
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


def identical(store, org=LUX, period="July 2026", **kw):
    a = OLD_CE.preview(FakeClient(copy.deepcopy(store)), org, period, **kw)
    b = CE.preview(FakeClient(copy.deepcopy(store)), org, period, **kw)
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(_drop_gate(b), sort_keys=True,
                                                                    default=str), a, b


# G1 — BOOST / house: no plans at all
_s = base_store()
_s["raw_sales"] = owner_trans_3207(HOUSE, "TW Edge Financing")
ok, a, b = identical(_s, org=HOUSE)
check("G1  Boost/house org (no commission plans) — result byte-identical to base", ok)
check("G1b ...and pays $0 from the plan engine either way",
      (a.get("totals") or {}).get("payout", 0) == 0 == (b.get("totals") or {}).get("payout", 0))

# G2 — a plan tenant whose rules are all LINE-level
_s = base_store()
_s["commission_plan"] = [plan(OTHER, "p9", "Line rules")]
_s["commission_rule"] = [
    rule(OTHER, "p9", "r1", label="acc", match_field="category", match_op="equals",
         match_value="accessories", payout_kind="pct_gp", pct=0.10),
    rule(OTHER, "p9", "r2", label="act", match_field="contract_type", match_op="contains",
         match_value="port", payout_kind="flat_per_unit", amount=10),
    rule(OTHER, "p9", "r3", label="bonus", match_field="any", payout_kind="flat", amount=50),
]
_s["commission_plan_assignment"] = [assign(OTHER, "p9", "default")]
_s["raw_sales"] = owner_trans_3207(OTHER, "Cash")
ok, a, b = identical(_s, org=OTHER)
check("G2  plan tenant, every rule keyed on a LINE-level field — byte-identical to base", ok)
ok, a, b = identical(_s, org=OTHER, detail=True)
check("G2b ...also byte-identical with detail=True (per-line drill-down rows)", ok)
ok, a, b = identical(_s, org=OTHER, coverage=True, unmatched_detail=True)
check("G2c ...also byte-identical with coverage=True + unmatched_detail=True", ok)

# G3 — the SAME tenant, the SAME plan: only the edge rule moves, the others do not
_s = edge_plan_store(tender="TW Edge Financing", rules_extra=[
    rule(LUX, "p1", "r-acc", label="Accessories", match_field="category", match_op="equals",
         match_value="accessories", payout_kind="pct_gp", pct=0.175),
    rule(LUX, "p1", "r-act", label="Activations", match_field="contract_type", match_op="contains",
         match_value="port", payout_kind="flat_per_unit", amount=10),
])
_a = OLD_CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")
_b = CE.preview(FakeClient(copy.deepcopy(_s)), LUX, "July 2026")


def by_rule(res, rep=REP):
    for r in res.get("by_rep") or []:
        if r.get("rep") == rep:
            return {rb.get("label"): round(rb.get("payout") or 0, 2) for rb in (r.get("rules") or [])}
    return {}


_ra, _rb2 = by_rule(_a), by_rule(_b)
check("G3  the 'edge' rule alone changes: $200.00 -> $25.00",
      _ra.get("edge") == 200.0 and _rb2.get("edge") == 25.0, f"{_ra} / {_rb2}")
check("G3b every OTHER rule in the same plan is byte-identical",
      {k: v for k, v in _ra.items() if k != "edge"} == {k: v for k, v in _rb2.items() if k != "edge"},
      f"{_ra} / {_rb2}")
check("G3c the Activations rule keeps paying per LINE (3 'Internal Port with IDV' lines x $10 = $30)",
      _rb2.get("Activations") == 30.0, str(_rb2))

# G4 — 300-seed fuzz over rule shapes that are NOT the defect signature
random.seed(20260801)
_fields = ["contract_type", "department", "category", "product_desc", "sku", "trans_type", "any"]
_kinds = ["flat_per_unit", "pct_gp", "pct_price_over_cost", "flat"]
_bad = 0
for i in range(300):
    org = f"fz-{i}"
    st = base_store()
    st["commission_plan"] = [plan(org, "p", "P")]
    st["commission_rule"] = [
        rule(org, "p", f"r{j}", label=f"r{j}",
             match_field=random.choice(_fields), match_op=random.choice(["equals", "contains", "in"]),
             match_value=random.choice(["port", "accessories", "handset", "sale", "byod", ""]),
             payout_kind=random.choice(_kinds), amount=random.choice([0, 5, 10, 25]),
             pct=random.choice([0, 0.1, 0.175]), qualifies=random.random() > 0.15,
             tiered=random.random() > 0.7)
        for j in range(random.randint(1, 4))]
    st["commission_plan_assignment"] = [assign(org, "p", "default")]
    st["raw_sales"] = owner_trans_3207(org, random.choice(["Cash", "Credit", "TW Edge Financing"]))
    ok, _, _ = identical(st, org=org)
    if not ok:
        _bad += 1
check("G4  300-seed fuzz over NON-tender-keyed rule shapes — every one byte-identical",
      _bad == 0, f"{_bad} mismatches")

# G5 — the defect signature is the ONLY thing that moves, even under fuzz
random.seed(4242)
_moved, _same = 0, 0
for i in range(120):
    org = f"fz2-{i}"
    st = base_store()
    st["commission_plan"] = [plan(org, "p", "P")]
    _f = random.choice(_fields + ["tender_type"])
    _k = random.choice(_kinds)
    st["commission_rule"] = [rule(org, "p", "r", label="x", match_field=_f, match_op="contains",
                                  match_value="edge", payout_kind=_k, amount=25, pct=0.1)]
    st["commission_plan_assignment"] = [assign(org, "p", "default")]
    st["raw_sales"] = owner_trans_3207(org, "TW Edge Financing")
    ok, _, _ = identical(st, org=org)
    if _f == "tender_type" and _k == "flat_per_unit":
        _moved += 0 if ok else 1
    else:
        _same += 1 if ok else 0
        if not ok:
            _bad += 1
check("G5  under fuzz, ONLY (match_field=tender_type AND payout_kind=flat_per_unit) ever differs",
      _bad == 0, f"{_bad} unexpected mismatches")
check("G5b ...and that signature DOES differ every time it occurs", _moved > 0, f"moved={_moved}")

# G6 — %-of-basis rules are never deduped, even on the tender
_s = edge_plan_store(tender="TW Edge Financing")
_s["commission_rule"][0].update(payout_kind="pct_gp", pct=0.10, amount=0)
ok, a, b = identical(_s)
check("G6  a pct_gp rule keyed on the TENDER is NOT deduped (deleting % dollars would be a new bug)",
      ok)
_s = edge_plan_store(tender="TW Edge Financing")
_s["commission_rule"][0].update(payout_kind="pct_gp", pct=0.10, amount=0, unit_basis="per_device")
ok, a, b = identical(_s)
check("G6b ...and an explicit unit_basis on a pct rule is refused, not honoured", ok)
_bs, _src2 = GATE.resolve_unit_basis({"payout_kind": "pct_gp", "unit_basis": "per_device"},
                                     GATE.UNIT_DEFAULTS)
check("G6c ...the refusal is REPORTED, not silent (source='ignored_non_flat_per_unit')",
      _bs == "per_line" and _src2 == "ignored_non_flat_per_unit", f"{_bs}/{_src2}")

print("\n── H. MIGRATION-UNAPPLIED DEGRADATION ───────────────────────────────────────────────────")
_s = edge_plan_store(tender="TW Edge Financing")      # no plan_pay_gate column, no exclusion table
_r, _ = payout(_s)
check("H1  with migrations 260/261 unapplied the OWNER'S RULE is the behaviour ($25.00)",
      rep_total(_r) == 25.0, f"got {rep_total(_r)}")
check("H2  ...and the guard says the config came from the code default, not a tenant row",
      (_r.get("pay_gate") or {}).get("config_source") == "code_default")
check("H3  ...and reports that the exclusion table is not there yet",
      (_r.get("pay_gate") or {}).get("exclusion_map_ready") is False)
_s = edge_plan_store(tender="TW Edge Financing", with_exclusion_table=True)
check("H4  with the exclusion table present but empty, the seed still applies and pay is unchanged",
      rep_total(payout(_s)[0]) == 25.0)

print("\n── I. TENANT ISOLATION ──────────────────────────────────────────────────────────────────")
_s = edge_plan_store(tender="TW Edge Financing")
_s["raw_sales"] += owner_trans_3207(OTHER, "TW Edge Financing", tid="9001")
_s["commission_plan"].append(plan(OTHER, "p2", "Other tenant"))
_s["commission_rule"].append(rule(OTHER, "p2", "r-o", label="edge", match_field="tender_type",
                                  match_op="contains", match_value="edge",
                                  payout_kind="flat_per_unit", amount=25))
_s["commission_plan_assignment"].append(assign(OTHER, "p2", "default"))
_lux, _ = payout(_s, org=LUX)
_oth, _ = payout(_s, org=OTHER)
check("I1  tenant A sees only its own transaction ($25.00)", rep_total(_lux) == 25.0)
check("I2  tenant B sees only its own transaction ($25.00)", rep_total(_oth) == 25.0)
check("I3  neither total contains the other's lines",
      (_lux.get("totals") or {}).get("sale_lines") == 8
      and (_oth.get("totals") or {}).get("sale_lines") == 8,
      f"{_lux['totals']} / {_oth['totals']}")

print("\n── J. THE CLASS QUESTION — which OTHER $/unit rules multiply inside a transaction? ──────")
# The audit is data-driven: it reports any flat_per_unit rule paying more than once per transaction,
# whatever field it keys on, so the operator can see whether anything besides the tender is affected.
_s = edge_plan_store(tender="TW Edge Financing", rules_extra=[
    rule(LUX, "p1", "r-act", label="Activations", match_field="contract_type", match_op="contains",
         match_value="internal port", payout_kind="flat_per_unit", amount=10),
])
_aud = GATE  # audit lives in plan_pay_gate via the router endpoint; assert the primitive here
_res, _ = payout(_s, detail=True)
_acts = [rb for rb in (_res["by_rep"][0]["rules"] or []) if rb.get("label") == "Activations"]
check("J1  a LINE-level $/unit rule that legitimately hits 3 lines of one sale is left alone",
      _acts and _acts[0]["payout"] == 30.0, str(_acts)[:200])
check("J2  ...and is reported as per_line with source 'default' (visible, not silent)",
      _acts and _acts[0].get("unit_basis") == "per_line"
      and _acts[0].get("unit_basis_source") == "default", str(_acts)[:200])

print("\n" + "=" * 96)
print(f"RESULT  {PASS} passed, {FAIL} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 96)
sys.exit(1 if FAIL else 0)
