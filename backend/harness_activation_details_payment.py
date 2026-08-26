"""HARNESS — pay activations from the ingested "Activation Details" report (mig 296), per-tenant opt-in.

Drives the REAL commission_engine.preview() over an in-memory fake Supabase client (no DB, no network,
no writes). Proves the locked design:

  A. FLAG ON pays $10 x DISTINCT ACTIVATION from the Activation Details report — a Diversey-shaped
     49-activation set (incl. a multi-line device: Service Plan + insurance Plan-Option under ONE Serial#)
     pays exactly $490, NOT per line. Upgrade / Other / Returns / cancelled are excluded.

  B. SINGLE SOURCE, no double-count. A rep with BOTH raw_sales activation lines AND Activation Details
     lines pays ONLY the Detail count under the flag — the raw_sales activations are suppressed. The same
     rep's raw_sales ACCESSORIES still pay (non-activation lines keep coming from raw_sales).

  C. FLAG OFF (default / NULL) is byte-identical to the raw_sales path — the same fixture pays the
     raw_sales activation count, and the Activation Details report is never consulted.

  D. pct_mrc on an activation-detail line prices off the report's own MRC column.

  E. pct_gp / pct_price on an activation-detail line is REFUSED ($0) with a plain-language note — never
     mis-priced (the report has no cost/price column).

  F. NAME BRIDGE — a rep whose Activation Details salesperson name differs from the roster still resolves
     (and gets paid) via the SAME name_map/rep_aliases bridge the money path uses, instead of dropping to $0.

  python3 backend/harness_activation_details_payment.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_engine as ce            # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
PERIOD = "August 2026"
PLAN = "ADPLAN"

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


# ── in-memory fake of the client surface the engine + resolver use ──────────────────────────────────
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


# ── rule / plan builders ────────────────────────────────────────────────────────────────────────
def act_rule(kind="flat_per_unit", amount=10.0, pct=0.0):
    return {"id": "RACT", "label": "$ per activation (premium+byod)", "match_field": "activation_bucket",
            "match_op": "in", "match_value": "premium,byod", "qualifies": True,
            "payout_kind": kind, "amount": amount, "pct": pct, "tiered": False,
            "unit_basis": "per_transaction", "sort": 1}


ACCESSORY_RULE = {"id": "RACC", "label": "10% of accessory sales", "match_field": "accessory",
                  "match_op": "equals", "match_value": "yes", "qualifies": True,
                  "payout_kind": "pct_price", "amount": 0.0, "pct": 0.10, "tiered": False,
                  "unit_basis": "", "sort": 2}


def base_tables(rules, activation_source="activation_details", scope="default",
                scope_value=None, name_map=None):
    cfg = {"org_id": ORG}
    if activation_source is not None:
        cfg["activation_source"] = activation_source
    return {
        ("commcalc", "commission_plan"): [
            {"id": PLAN, "org_id": ORG, "name": "AD Plan", "is_active": True, "carrier_id": None,
             "base_tier_metric": "none", "tier_count_basis": None, "tier_below_min_multiplier": None}],
        ("commcalc", "commission_rule"): [dict(r, org_id=ORG, plan_id=PLAN) for r in rules],
        ("commcalc", "commission_tier"): [],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "A1", "org_id": ORG, "plan_id": PLAN, "scope": scope, "scope_value": scope_value}],
        ("commcalc", "store_mapping"): [
            {"org_id": ORG, "store_code": "DIV", "store_address": "diversey", "market": "Chicago"}],
        ("commcalc", "accessory_config"): [
            {"org_id": ORG, "departments": ["Accessories"], "categories": ["Accessories"],
             "product_keywords": []}],
        ("commcalc", "commission_org_config"): [cfg],
        ("commcalc", "name_map"): name_map or [],
        ("commcalc", "rep_aliases"): [],
        ("commcalc", "raw_custom_import"): [],
        ("commcalc", "raw_sales"): [],
    }


# ── Activation Details custom-import row (device-serial shape) ─────────────────────────────────────
def ad_row(serial, ct, rep="REP D", store="Diversey", sp="", prod="", cat="", mrc=0.0,
           trans_type="Sale", status="Active", trans_id=None, date="8/10/2026"):
    return {
        "org_id": ORG, "period": PERIOD, "report_key": "activation_details",
        "source_filename": "activation_details_aug.csv", "row_index": ad_row._i,
        "data": {
            "Serial#": serial, "Contract Type": ct, "Salesperson": rep, "Store": store,
            "Trans Date": date, "Trans ID": trans_id or f"T{serial}", "SP/PO Name": sp,
            "Product Desc": prod, "Category": cat, "Carrier": "TMO", "MRC": mrc,
            "Trans Type": trans_type, "Activation Status": status, "Action Type": ct,
        },
    }


ad_row._i = 0


def _next_i():
    ad_row._i += 1
    return ad_row._i


def diversey_49():
    """49 distinct payable activations (40 premium + 9 byod), Diversey shape, plus lines that MUST NOT pay:
    a shared-serial insurance Plan-Option line (dedupe), 3 Upgrades, 1 Return, 1 cancelled."""
    rows = []
    n = 0

    def add(serial, ct, **kw):
        r = ad_row(serial, ct, **kw)
        r["row_index"] = _next_i()
        rows.append(r)

    # premium families
    for i in range(20):
        add(f"NA{i}", "New Activation", sp="Premium Plan", prod="iPhone 15")            # 20 new act
    for i in range(10):
        add(f"PT{i}", "Port with IDV", sp="Premium Plan", prod="Galaxy S24")            # 10 port -> premium
    for i in range(5):
        add(f"TB{i}", "New Activation", sp="Tablet Plan", prod="Galaxy Tab", cat="Tablet")  # 5 tablet
    for i in range(4):
        add(f"HI{i}", "New Activation", sp="Home Internet", prod="Home Internet Gateway")    # 4 home int
    add("ED0", "New Activation", sp="Edge Plan", prod="Edge Device")                     # 1 edge
    # byod
    for i in range(9):
        add(f"BY{i}", "BYOD Activation", sp="BYOD Plan", prod="Customer Phone")          # 9 byod
    # ── lines that MUST NOT create/duplicate a paid activation ──
    add("NA0", "", sp="IDV Insurance", prod="Protection", cat="Insurance", mrc=8.0)      # SAME serial as a
    #   New Activation device (insurance Plan-Option) -> deduped, one activation, never a second $10
    for i in range(3):
        add(f"UP{i}", "Upgrade")                                                          # 3 upgrades excluded
    add("RET0", "New Activation", trans_type="Return")                                    # return excluded
    add("CAN0", "New Activation", status="Cancelled")                                     # cancelled excluded
    return rows


def raw_sale(tid, rep, ct="", dept="", cat="", ext=0.0, store="Diversey"):
    return {"org_id": ORG, "period": PERIOD, "trans_id": tid, "salesperson": rep, "store": store,
            "trans_date": "2026-08-10", "contract_type": ct, "department": dept, "category": cat,
            "product_desc": (cat or dept or ct or "item"), "ext_price": ext, "gp": ext,
            "voided": "", "trans_type": "Sale", "mdn": ""}


def money(v):
    return round(float(v or 0), 2)


def rep_payout(res, rule_id=None):
    total = money(res["totals"]["payout"])
    per_rule = {}
    for rep in res["by_rep"]:
        for rb in rep.get("rules", []):
            per_rule[rb["rule_id"]] = money(per_rule.get(rb["rule_id"], 0.0)) + money(rb["payout"])
    return total, per_rule


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── A. FLAG ON: $10 x 49 distinct activations = $490 (Diversey shape) ────────────")
t = base_tables([act_rule()], activation_source="activation_details")
t[("commcalc", "raw_custom_import")] = diversey_49()
res = ce.preview(FakeClient(t), ORG, PERIOD)
total, per_rule = rep_payout(res)
adm = res.get("activation_source") or {}
check("total payout is $490 (49 x $10)", total == 490.0, total)
check("activation rule paid $490", per_rule.get("RACT") == 490.0, per_rule)
check("meta reports source=activation_details", adm.get("source") == "activation_details", adm)
check("meta reports raw_sales activations suppressed",
      adm.get("raw_sales_activation_bucket_suppressed") is True, adm)
check("meta counts 49 distinct activations",
      (adm.get("detail") or {}).get("distinct_activations") == 49, adm.get("detail"))
check("meta bucket split 40 premium / 9 byod",
      (adm.get("detail") or {}).get("by_bucket") == {"premium": 40, "byod": 9},
      (adm.get("detail") or {}).get("by_bucket"))
# the shared-serial insurance line is collapsed INSIDE the resolver (New Activation outranks it on the
# same Serial#), so it never reaches _activation_detail_lines as a separate row; the 3 Upgrades are what
# this stage drops. Return/cancelled were excluded by the resolver. resolver_rows = 49 payable + 3 upgrade.
check("resolver returned 52 rows (49 payable + 3 upgrade; insurance deduped, return/cancelled excluded)",
      (adm.get("detail") or {}).get("resolver_rows") == 52, (adm.get("detail") or {}))
check("meta dropped the 3 upgrades (Upgrade/Other not payable)",
      (adm.get("detail") or {}).get("dropped_upgrade_other") == 3, (adm.get("detail") or {}))

print("── B. SINGLE SOURCE: raw_sales activations suppressed, accessories still paid ───")
t = base_tables([act_rule(), ACCESSORY_RULE], activation_source="activation_details")
# same rep has 5 raw_sales activation lines (would pay $50 on the raw_sales path) + 3 Detail activations
t[("commcalc", "raw_sales")] = (
    [raw_sale(f"RS{i}", "REP X", ct="Activation") for i in range(5)]
    + [raw_sale(f"AC{i}", "REP X", dept="Accessories", cat="Accessories", ext=100.0) for i in range(4)])
t[("commcalc", "raw_custom_import")] = [
    ad_row("SX0", "New Activation", rep="REP X"), ad_row("SX1", "New Activation", rep="REP X"),
    ad_row("SX2", "BYOD Activation", rep="REP X")]
for r in t[("commcalc", "raw_custom_import")]:
    r["row_index"] = _next_i()
res = ce.preview(FakeClient(t), ORG, PERIOD)
total, per_rule = rep_payout(res)
check("activations pay the DETAIL count (3 x $10 = $30), NOT the raw_sales 5, NOT the union 8",
      per_rule.get("RACT") == 30.0, per_rule)
check("raw_sales accessories still pay (4 x $100 x 10% = $40)", per_rule.get("RACC") == 40.0, per_rule)
check("total is $70 (no double-count)", total == 70.0, total)

print("── C. FLAG OFF (default): byte-identical to the raw_sales path ──────────────────")
# activation_source omitted entirely (column/NULL) -> raw_sales; Details ignored.
t_off = base_tables([act_rule()], activation_source=None)
t_off[("commcalc", "raw_sales")] = [raw_sale(f"RS{i}", "REP X", ct="Activation") for i in range(5)]
t_off[("commcalc", "raw_custom_import")] = diversey_49()   # present but MUST be ignored when OFF
res_off = ce.preview(FakeClient(t_off), ORG, PERIOD)
total_off, per_rule_off = rep_payout(res_off)
check("flag OFF pays the raw_sales activation count (5 x $10 = $50)", total_off == 50.0, total_off)
check("flag OFF does NOT emit an activation_source block", "activation_source" not in res_off,
      list(res_off.keys()))
# explicit 'raw_sales' behaves identically to omitted
t_rs = base_tables([act_rule()], activation_source="raw_sales")
t_rs[("commcalc", "raw_sales")] = [raw_sale(f"RS{i}", "REP X", ct="Activation") for i in range(5)]
t_rs[("commcalc", "raw_custom_import")] = diversey_49()
check("explicit 'raw_sales' == omitted (both $50, Details ignored)",
      money(ce.preview(FakeClient(t_rs), ORG, PERIOD)["totals"]["payout"]) == 50.0)

print("── D. pct_mrc on a Detail line prices off the report's MRC column ───────────────")
t = base_tables([act_rule(kind="pct_mrc", amount=0.0, pct=0.5)], activation_source="activation_details")
t[("commcalc", "raw_custom_import")] = [
    ad_row("M0", "New Activation", rep="REP M", mrc=40.0),
    ad_row("M1", "BYOD Activation", rep="REP M", mrc=20.0)]
for r in t[("commcalc", "raw_custom_import")]:
    r["row_index"] = _next_i()
res = ce.preview(FakeClient(t), ORG, PERIOD)
total, per_rule = rep_payout(res)
check("pct_mrc pays 50% of (40+20)=$30 off the report MRC", per_rule.get("RACT") == 30.0, per_rule)

print("── E. pct_gp / pct_price on a Detail line is REFUSED ($0) with a note ───────────")
t = base_tables([act_rule(kind="pct_price", amount=0.0, pct=0.10)],
                activation_source="activation_details")
t[("commcalc", "raw_custom_import")] = [ad_row("G0", "New Activation", rep="REP G", mrc=40.0)]
t[("commcalc", "raw_custom_import")][0]["row_index"] = _next_i()
res = ce.preview(FakeClient(t), ORG, PERIOD)
total, per_rule = rep_payout(res)
adm = res.get("activation_source") or {}
unsup = adm.get("unsupported_payout_kinds") or []
check("pct_price on a Detail line pays $0 (refused, never mis-priced)", total == 0.0, total)
check("the refusal is reported with the rule + kind",
      any(u.get("payout_kind") == "pct_price" for u in unsup), unsup)
check("the note names the missing cost/price column",
      bool(unsup) and ("cost" in unsup[0]["note"] or "price" in unsup[0]["note"]), unsup)

print("── F. NAME BRIDGE: a report name that differs from roster still resolves + pays ──")
# employee-scope plan pinned to the ROSTER name; the Detail salesperson is a nickname that COMMA-FLIP
# canon alone cannot bridge ("NIVAS K" -> "nivas k" != "nivas sriram"), so only the name_map alias
# rescues it. Without the bridge this rep would drop to $0 (unassigned) — the Luxelink $0 class.
nm = [{"org_id": ORG, "epay_salesperson": "NIVAS K", "storeops_name": "Nivas Sriram"}]
t = base_tables([act_rule()], activation_source="activation_details",
                scope="employee", scope_value="Nivas Sriram", name_map=nm)
t[("commcalc", "raw_custom_import")] = [
    ad_row("Z0", "New Activation", rep="NIVAS K"),
    ad_row("Z1", "New Activation", rep="NIVAS K")]
for r in t[("commcalc", "raw_custom_import")]:
    r["row_index"] = _next_i()
res = ce.preview(FakeClient(t), ORG, PERIOD)
total, per_rule = rep_payout(res)
reps = [rp["rep"] for rp in res["by_rep"]]
check("the report-named rep resolves to the roster identity and is paid 2 x $10 = $20",
      total == 20.0, (total, reps))
check("the rep is listed under the roster name (not the report spelling)",
      "Nivas Sriram" in reps, reps)
# NEGATIVE CONTROL: with NO name_map, comma-flip canon cannot bridge "NIVAS K" -> plan cannot attach -> $0.
t_nb = base_tables([act_rule()], activation_source="activation_details",
                   scope="employee", scope_value="Nivas Sriram", name_map=[])
t_nb[("commcalc", "raw_custom_import")] = [ad_row("Z0", "New Activation", rep="NIVAS K")]
t_nb[("commcalc", "raw_custom_import")][0]["row_index"] = _next_i()
check("without the bridge the same rep drops to $0 (the failure the bridge prevents)",
      money(ce.preview(FakeClient(t_nb), ORG, PERIOD)["totals"]["payout"]) == 0.0)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
