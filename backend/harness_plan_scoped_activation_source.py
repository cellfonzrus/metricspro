"""HARNESS — PER-PLAN activation source (mig 297). The Chicago-not-zeroed proof.

Context: the Chicago/Luxelink org (854f6d7b-6590-4e4d-88ab-646f560d4f4c) holds BOTH the NY reps and 13
Chicago stores in ONE org. mig 296 could pay activations from the ingested "Activation Details" report, but
its switch was ORG-WIDE — flipping it would ZERO every Chicago activation (Chicago is not in the NY report).
mig 297 moves the control to the PLAN. This harness drives the REAL commission_engine.preview() over an
in-memory fake Supabase client (no DB, no network, no writes) and proves the per-rep scoping:

  ONE org, TWO reps:
    • Rep A is on PLAN_A, whose activation_source = 'activation_details'  (the "NY plan").
    • Rep B is on PLAN_B, left 'inherit'  → org-level default 'raw_sales'  (a "Chicago plan").

  A. REP A is paid activations FROM the Activation Details report (deduped, $10/activation), and A's OWN
     raw_sales activations are SUPPRESSED (single source, no double-count). A's accessories still pay from
     raw_sales.

  B. REP B is paid activations FROM raw_sales exactly as today — UNAFFECTED by A's plan being AD. B's
     accessories pay from raw_sales. This is the Chicago-not-zeroed proof: the whole reason for mig 297.

  C. BYTE-IDENTICAL CHICAGO. Rep B's payout is IDENTICAL whether or not rep A's plan is on activation_details
     — proven by running the SAME fixture with both plans 'inherit' and asserting B's number does not move.

  D. A STRAY report activation for rep B (a Chicago name that leaked into the report) is DROPPED and never
     pays B — report activations only ever reach a rep whose effective plan is activation_details.

  E. NO plan AD (both 'inherit', org 'raw_sales') → byte-identical: no activation_source block emitted, the
     Details report is ignored, everyone pays from raw_sales.

  python3 backend/harness_plan_scoped_activation_source.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_engine as ce            # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
PERIOD = "August 2026"
PLAN_A = "PLAN_A_NY"
PLAN_B = "PLAN_B_CHI"

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


# ── in-memory fake of the client surface the engine + resolver use (copied from the mig-296 harness) ──
class _Q:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def neq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) != str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in vs]
        return self

    def not_(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def lte(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def range(self, a, b):
        self._rows = self._rows[a:b + 1]
        return self

    def execute(self):
        return type("R", (), {"data": list(self._rows)})()


class _Schema:
    def __init__(self, store, name):
        self._store, self._name = store, name

    def table(self, t):
        return _Q(self._store.get((self._name, t), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, name):
        return _Schema(self._t, name)

    def table(self, t):
        return _Q(self._t.get(("public", t), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


# ── rules ────────────────────────────────────────────────────────────────────────────────────────
def act_rule():
    return {"id": "RACT", "label": "$ per activation (premium+byod)", "match_field": "activation_bucket",
            "match_op": "in", "match_value": "premium,byod", "qualifies": True,
            "payout_kind": "flat_per_unit", "amount": 10.0, "pct": 0.0, "tiered": False,
            "unit_basis": "per_transaction", "sort": 1}


def acc_rule():
    return {"id": "RACC", "label": "10% of accessory sales", "match_field": "accessory",
            "match_op": "equals", "match_value": "yes", "qualifies": True,
            "payout_kind": "pct_price", "amount": 0.0, "pct": 0.10, "tiered": False,
            "unit_basis": "", "sort": 2}


def plan_row(pid, name, activation_source):
    p = {"id": pid, "org_id": ORG, "name": name, "is_active": True, "carrier_id": None,
         "base_tier_metric": "none", "tier_count_basis": None, "tier_below_min_multiplier": None}
    if activation_source is not None:
        p["activation_source"] = activation_source
    return p


def base_tables(plan_a_src="activation_details", plan_b_src="inherit", org_src=None):
    """TWO plans (A=NY, B=Chicago), each employee-scoped to its rep. plan_a_src / plan_b_src set the
    PLAN-level activation_source (None = column absent = 'inherit'). org_src = the org-level mig-296 value."""
    cfg = {"org_id": ORG}
    if org_src is not None:
        cfg["activation_source"] = org_src
    rules = ([dict(act_rule(), org_id=ORG, plan_id=PLAN_A), dict(acc_rule(), org_id=ORG, plan_id=PLAN_A)]
             + [dict(act_rule(), org_id=ORG, plan_id=PLAN_B), dict(acc_rule(), org_id=ORG, plan_id=PLAN_B)])
    return {
        ("commcalc", "commission_plan"): [
            plan_row(PLAN_A, "Total Employee Comp NY", plan_a_src),
            plan_row(PLAN_B, "Chicago Store Comp", plan_b_src)],
        ("commcalc", "commission_rule"): rules,
        ("commcalc", "commission_tier"): [],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "AA", "org_id": ORG, "plan_id": PLAN_A, "scope": "employee", "scope_value": "Rep A"},
            {"id": "AB", "org_id": ORG, "plan_id": PLAN_B, "scope": "employee", "scope_value": "Rep B"}],
        ("commcalc", "store_mapping"): [
            {"org_id": ORG, "store_code": "NY", "store_address": "manhattan", "market": "New York"},
            {"org_id": ORG, "store_code": "DIV", "store_address": "diversey", "market": "Chicago"}],
        ("commcalc", "accessory_config"): [
            {"org_id": ORG, "departments": ["Accessories"], "categories": ["Accessories"],
             "product_keywords": []}],
        ("commcalc", "commission_org_config"): [cfg],
        ("commcalc", "name_map"): [],
        ("commcalc", "rep_aliases"): [],
        ("commcalc", "raw_custom_import"): [],
        ("commcalc", "raw_sales"): [],
    }


def ad_row(serial, ct, rep, store="Manhattan", mrc=0.0):
    ad_row._i += 1
    return {
        "org_id": ORG, "period": PERIOD, "report_key": "activation_details",
        "source_filename": "activation_details_aug.csv", "row_index": ad_row._i,
        "data": {
            "Serial#": serial, "Contract Type": ct, "Salesperson": rep, "Store": store,
            "Trans Date": "8/10/2026", "Trans ID": f"T{serial}", "SP/PO Name": "",
            "Product Desc": "", "Category": "", "Carrier": "TMO", "MRC": mrc,
            "Trans Type": "Sale", "Activation Status": "Active", "Action Type": ct,
        },
    }


ad_row._i = 0


def raw_sale(tid, rep, ct="", dept="", cat="", ext=0.0, store="Manhattan"):
    return {"org_id": ORG, "period": PERIOD, "trans_id": tid, "salesperson": rep, "store": store,
            "trans_date": "2026-08-10", "contract_type": ct, "department": dept, "category": cat,
            "product_desc": (cat or dept or ct or "item"), "ext_price": ext, "gp": ext,
            "voided": "", "trans_type": "Sale", "mdn": ""}


def money(v):
    return round(float(v or 0), 2)


def per_rep_payout(res):
    out = {}
    for rep in res["by_rep"]:
        out[rep["rep"]] = money(rep.get("total_payout"))
    return out


def build_sales(t):
    """Rep A (NY): 5 raw activations + 2 accessories($100). Rep B (Chicago): 4 raw activations + 2 acc."""
    t[("commcalc", "raw_sales")] = (
        [raw_sale(f"A-ACT{i}", "Rep A", ct="Activation", store="Manhattan") for i in range(5)]
        + [raw_sale(f"A-ACC{i}", "Rep A", dept="Accessories", cat="Accessories", ext=100.0,
                    store="Manhattan") for i in range(2)]
        + [raw_sale(f"B-ACT{i}", "Rep B", ct="Activation", store="Diversey") for i in range(4)]
        + [raw_sale(f"B-ACC{i}", "Rep B", dept="Accessories", cat="Accessories", ext=100.0,
                    store="Diversey") for i in range(2)])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── A/B. Rep A = report-sourced ($30), Rep B = raw_sales ($40), both accessories $20 ──────────")
t = base_tables(plan_a_src="activation_details", plan_b_src="inherit", org_src="raw_sales")
build_sales(t)
# Rep A: 3 report activations (2 premium + 1 byod). Rep B: NONE (Chicago not in the NY report).
t[("commcalc", "raw_custom_import")] = [
    ad_row("A0", "New Activation", "Rep A"), ad_row("A1", "New Activation", "Rep A"),
    ad_row("A2", "BYOD Activation", "Rep A")]
res = ce.preview(FakeClient(t), ORG, PERIOD)
pay = per_rep_payout(res)
adm = res.get("activation_source") or {}

# Rep A: report activations 3*$10=$30, raw activations suppressed, accessories 2*$100*10%=$20 -> $50
check("Rep A total is $50 (report $30 + accessories $20)", pay.get("Rep A") == 50.0, pay)
# Rep B: raw_sales activations 4*$10=$40, accessories 2*$100*10%=$20 -> $60 (UNAFFECTED)
check("Rep B total is $60 (raw_sales $40 + accessories $20)", pay.get("Rep B") == 60.0, pay)
check("grand total is $110", money(res["totals"]["payout"]) == 110.0, res["totals"])
check("meta: source=activation_details, scope=per_plan",
      adm.get("source") == "activation_details" and adm.get("scope") == "per_plan", adm)
check("meta: ONLY PLAN_A is activation-details-sourced",
      adm.get("activation_details_plan_ids") == [PLAN_A], adm.get("activation_details_plan_ids"))
check("meta: Rep A paid from the report", adm.get("reps_paid_from_report") == ["Rep A"],
      adm.get("reps_paid_from_report"))
check("meta: Rep A's raw_sales activations suppressed (Rep B's are NOT)",
      adm.get("reps_raw_sales_suppressed") == ["Rep A"], adm.get("reps_raw_sales_suppressed"))
check("meta: no report activation was dropped (none belonged to a non-AD rep here)",
      adm.get("detail_lines_dropped_non_ad_rep") == 0, adm.get("detail_lines_dropped_non_ad_rep"))

# Per-rep rule breakdown: A's activation dollars come from 3 report lines, not the 5 raw ones (no double-count)
def rep_rule(res, rep_name, rule_id):
    for rep in res["by_rep"]:
        if rep["rep"] == rep_name:
            for rb in rep.get("rules", []):
                if rb["rule_id"] == rule_id:
                    return money(rb["payout"])
    return None


# re-run with detail=True to read the rule breakdown
resd = ce.preview(FakeClient(t), ORG, PERIOD, detail=True)
check("Rep A activation rule pays $30 (the 3 report lines, NOT the 5 raw, NOT the union 8)",
      rep_rule(resd, "Rep A", "RACT") == 30.0, rep_rule(resd, "Rep A", "RACT"))
check("Rep A accessory rule pays $20 (raw_sales)", rep_rule(resd, "Rep A", "RACC") == 20.0,
      rep_rule(resd, "Rep A", "RACC"))
check("Rep B activation rule pays $40 (the 4 raw_sales activations)",
      rep_rule(resd, "Rep B", "RACT") == 40.0, rep_rule(resd, "Rep B", "RACT"))
check("Rep B accessory rule pays $20 (raw_sales)", rep_rule(resd, "Rep B", "RACC") == 20.0,
      rep_rule(resd, "Rep B", "RACC"))

print("── C. BYTE-IDENTICAL CHICAGO: Rep B's pay does not move when A's plan flips to AD ────────────")
# Same fixture but BOTH plans 'inherit' (org raw_sales) -> nobody is report-sourced.
t_off = base_tables(plan_a_src="inherit", plan_b_src="inherit", org_src="raw_sales")
build_sales(t_off)
t_off[("commcalc", "raw_custom_import")] = [
    ad_row("A0b", "New Activation", "Rep A"), ad_row("A1b", "New Activation", "Rep A"),
    ad_row("A2b", "BYOD Activation", "Rep A")]   # present but MUST be ignored when nobody is AD
res_off = ce.preview(FakeClient(t_off), ORG, PERIOD)
pay_off = per_rep_payout(res_off)
check("Rep B is byte-identical across the two runs ($60 either way)",
      pay_off.get("Rep B") == pay.get("Rep B") == 60.0, (pay_off.get("Rep B"), pay.get("Rep B")))
check("with nobody AD, Rep A pays from raw_sales (5*$10 + $20 = $70)", pay_off.get("Rep A") == 70.0,
      pay_off.get("Rep A"))
check("with nobody AD, NO activation_source block is emitted (byte-identical result dict)",
      "activation_source" not in res_off, list(res_off.keys()))

print("── D. A stray report activation for a non-AD rep (Rep B) is DROPPED, never pays B ────────────")
t2 = base_tables(plan_a_src="activation_details", plan_b_src="inherit", org_src="raw_sales")
build_sales(t2)
t2[("commcalc", "raw_custom_import")] = [
    ad_row("A0c", "New Activation", "Rep A"), ad_row("A1c", "New Activation", "Rep A"),
    ad_row("A2c", "BYOD Activation", "Rep A"),
    ad_row("Bx", "New Activation", "Rep B")]   # a Chicago name that leaked into the report
res2 = ce.preview(FakeClient(t2), ORG, PERIOD)
pay2 = per_rep_payout(res2)
adm2 = res2.get("activation_source") or {}
check("Rep B is STILL $60 — the stray report activation did not pay B (dropped)",
      pay2.get("Rep B") == 60.0, pay2)
check("Rep A is STILL $50 — unchanged by the stray B line", pay2.get("Rep A") == 50.0, pay2)
check("meta counts the 1 dropped non-AD report activation",
      adm2.get("detail_lines_dropped_non_ad_rep") == 1, adm2.get("detail_lines_dropped_non_ad_rep"))

print("── E. NO plan AD + org raw_sales = the default fleet posture, byte-identical ─────────────────")
t3 = base_tables(plan_a_src=None, plan_b_src=None, org_src=None)   # columns absent everywhere
build_sales(t3)
t3[("commcalc", "raw_custom_import")] = [ad_row("Z0", "New Activation", "Rep A")]  # ignored
res3 = ce.preview(FakeClient(t3), ORG, PERIOD)
pay3 = per_rep_payout(res3)
check("both reps pay from raw_sales (A $70, B $60)",
      pay3.get("Rep A") == 70.0 and pay3.get("Rep B") == 60.0, pay3)
check("no activation_source block", "activation_source" not in res3, list(res3.keys()))

print("── F. PLAN PINS raw_sales against an ORG-WIDE flip (Chicago belt-and-braces) ─────────────────")
# Org flipped to 'activation_details' (mig-296 org-wide). PLAN_A inherits -> AD. PLAN_B PINS 'raw_sales',
# so Rep B stays on raw_sales even though the org switch is on. This is how mig 297 protects Chicago even
# if someone flips the org-level switch.
t4 = base_tables(plan_a_src="inherit", plan_b_src="raw_sales", org_src="activation_details")
build_sales(t4)
t4[("commcalc", "raw_custom_import")] = [
    ad_row("A0f", "New Activation", "Rep A"), ad_row("A1f", "New Activation", "Rep A"),
    ad_row("A2f", "BYOD Activation", "Rep A")]
res4 = ce.preview(FakeClient(t4), ORG, PERIOD)
pay4 = per_rep_payout(res4)
adm4 = res4.get("activation_source") or {}
check("Rep A (inherit -> org activation_details) is report-sourced ($50)", pay4.get("Rep A") == 50.0, pay4)
check("Rep B (plan pinned raw_sales) stays on raw_sales ($60), immune to the org flip",
      pay4.get("Rep B") == 60.0, pay4)
check("meta: only PLAN_A is AD-sourced (PLAN_B pinned out), org_activation_source echoed",
      adm4.get("activation_details_plan_ids") == [PLAN_A]
      and adm4.get("org_activation_source") == "activation_details", adm4)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
