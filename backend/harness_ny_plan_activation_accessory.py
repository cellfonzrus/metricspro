"""HARNESS — the NY commission plan the owner was blocked on: $10 per ACTIVATION + 10% of ACCESSORY $.

Owner definition (confirmed via coordinator):
  • $10 for EVERY activation, where an activation = classify_contract_type in {premium, byod}
    (new-line activations AND bring-your-own-device); UPGRADES are excluded ($0).
  • 10% of accessory SALES DOLLARS (10% of accessory ext_price).
  • Worked example: 100 activations + $1,000 of accessories -> $1,000 + $100 = $1,100.

Proves, offline, against an in-memory fake Supabase client (no network, no DB, no writes):

  A. THE RECIPE PAYS $1,100. A plan with exactly two commission_rule rows —
        (1) activation_bucket IN premium,byod   flat_per_unit  $10  unit_basis=per_transaction
        (2) accessory        EQUALS yes         pct_price      0.10
     computes $1,100 on 60 premium + 40 byod activations (100) and $1,000 of accessories, through the
     REAL commission_engine.preview(). Upgrades present in the feed pay $0.

  B. THE EDITOR CAN NOW EXPRESS RULE (2). plan_options.vocabulary() now serves 'pct_price' in its
     payout_kinds (it was dropped before, so the editor never offered "% of price (sale price)" and the
     owner could not build a 10%-of-accessory-DOLLARS rule). Negative control: remove it and the dropdown
     no longer offers it — the exact gap that blocked the owner.

  C. COUNT AGREEMENT (the Diversey concern). The activation COUNT the commission engine pays on equals
     the DISTINCT-TRANSACTION premium+byod count the daily/store report (_sales_cell_agg) shows for the
     SAME feed — because both route through the shared _resolve_ct_bucket / _blank_ct_bucket_map. A
     Diversey-shaped feed (7 new-activation + 18 port + 6 byod + 12 tablet + 5 home-internet + 1 edge =
     49, with a non-empty contract_type_map so the non-phone auto-count fires) pays exactly 49 * $10.

  D. PER-TRANSACTION vs PER-LINE. An activation transaction carrying the contract-type label on TWO lines
     pays $10 ONCE under unit_basis=per_transaction (matching the report's distinct-txn count), and would
     pay $20 under the per_line default — the concrete over-count per_transaction prevents.

    python3 backend/harness_ny_plan_activation_accessory.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_engine as ce            # noqa: E402
from app.modules.commcalc import plan_options as po                 # noqa: E402
from app.modules.commcalc import router as cr                       # noqa: E402

ORG = "00000000-0000-0000-0000-0000000000ny"
PERIOD = "August 2026"
PLAN = "NYPLAN"


# ── in-memory fake of the client surface the engine uses (real .eq/.in_ filtering, no write verbs) ──
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


ACTIVATION_RULE = {
    "id": "RACT", "label": "$10 per activation (premium+byod)", "match_field": "activation_bucket",
    "match_op": "in", "match_value": "premium,byod", "qualifies": True,
    "payout_kind": "flat_per_unit", "amount": 10.0, "pct": 0.0, "tiered": False,
    "unit_basis": "per_transaction", "sort": 1,
}
ACCESSORY_RULE = {
    "id": "RACC", "label": "10% of accessory sales", "match_field": "accessory",
    "match_op": "equals", "match_value": "yes", "qualifies": True,
    "payout_kind": "pct_price", "amount": 0.0, "pct": 0.10, "tiered": False,
    "unit_basis": "", "sort": 2,
}


def _base_tables(rules, ct_map=None, activation_rules=None):
    acfg = {"org_id": ORG, "departments": ["Accessories"], "categories": ["Accessories"],
            "product_keywords": []}
    if ct_map is not None:
        acfg["contract_type_map"] = ct_map
    if activation_rules is not None:
        acfg["activation_rules"] = activation_rules
    return {
        ("commcalc", "commission_plan"): [
            {"id": PLAN, "org_id": ORG, "name": "NY Employee Comp", "is_active": True,
             "carrier_id": None, "base_tier_metric": "none", "tier_count_basis": None,
             "tier_below_min_multiplier": None}],
        ("commcalc", "commission_rule"): [dict(r, org_id=ORG, plan_id=PLAN) for r in rules],
        ("commcalc", "commission_tier"): [],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "A1", "org_id": ORG, "plan_id": PLAN, "scope": "default", "scope_value": None}],
        ("commcalc", "store_mapping"): [
            {"org_id": ORG, "store_code": "NY01", "store_address": "1 broadway", "market": "NY"}],
        ("commcalc", "accessory_config"): [acfg],
    }


def _row(tid, rep, ct="", dept="", cat="", ext=0.0, store="1 Broadway"):
    return {"org_id": ORG, "period": PERIOD, "trans_id": tid, "salesperson": rep, "store": store,
            "trans_date": "2026-08-10", "contract_type": ct, "department": dept, "category": cat,
            "product_desc": (cat or dept or ct or "item"), "ext_price": ext, "gp": ext,
            "voided": "", "trans_type": "Sale"}


def _money(v):
    return round(float(v or 0), 2)


def _preview(tables):
    return ce.preview(FakeClient(tables), ORG, PERIOD)


# ── A. the recipe pays exactly $1,100 ──────────────────────────────────────────────────────────────
def run_recipe_1100():
    print("── A. RECIPE: $10 x (premium+byod) + 10% accessory$  ->  $1,100 ────────────────")
    rows = []
    for i in range(60):
        rows.append(_row(f"P{i}", "REP ONE", ct="Activation"))           # premium
    for i in range(40):
        rows.append(_row(f"B{i}", "REP ONE", ct="BYOD Activation"))      # byod
    for i in range(10):
        rows.append(_row(f"U{i}", "REP ONE", ct="Upgrade"))              # upgrade -> $0
    for i in range(20):
        rows.append(_row(f"A{i}", "REP ONE", dept="Accessories", cat="Accessories", ext=50.0))  # $1,000

    t = _base_tables([ACTIVATION_RULE, ACCESSORY_RULE])
    t[("commcalc", "raw_sales")] = rows
    res = _preview(t)
    total = _money(res["totals"]["payout"])

    # per-rule proof
    rep = res["by_rep"][0]
    by_rule = {rb["rule_id"]: _money(rb["payout"]) for rb in rep["rules"]}
    act_pay, acc_pay = by_rule.get("RACT", 0.0), by_rule.get("RACC", 0.0)
    print(f"   activations paid : ${act_pay:,.2f}  (expect $1,000.00 = 100 x $10)")
    print(f"   accessories paid : ${acc_pay:,.2f}  (expect $100.00 = 10% of $1,000)")
    print(f"   TOTAL            : ${total:,.2f}  (expect $1,100.00)")
    assert act_pay == 1000.0, act_pay
    assert acc_pay == 100.0, acc_pay
    assert total == 1100.0, total
    print("   PASS\n")
    return total


# ── B. the editor can now offer pct_price (the accessory-$ gap) ─────────────────────────────────────
def run_editor_offers_pct_price():
    print("── B. EDITOR VOCABULARY now offers 'pct_price' (% of sale price) ───────────────")
    kinds = {k["value"] for k in po.vocabulary()["payout_kinds"]}
    print(f"   served payout kinds: {sorted(kinds)}")
    assert "pct_price" in kinds, "pct_price still missing from the editor dropdown"
    assert "pct_price" in ce.PAYOUT_KINDS, "engine no longer supports pct_price"

    # NEGATIVE CONTROL — reconstruct the PRE-FIX served list (the exact tuple vocabulary() iterated before)
    # and prove it omitted pct_price, reproducing the gap that blocked the owner. Fails if the served
    # tuple is ever narrowed back to the old set.
    _pre_fix_tuple = ("flat_per_unit", "pct_mrc", "pct_gp", "pct_price_over_cost", "flat")
    before = {k for k in _pre_fix_tuple if k in ce.PAYOUT_KINDS}
    print(f"   without the fix    : {sorted(before)}  (no pct_price -> owner cannot build 10% of $)")
    assert "pct_price" not in before
    assert kinds - before == {"pct_price"}, "the fix should add exactly pct_price and nothing else"
    print("   PASS\n")


# ── C. engine activation count == daily-report distinct-txn premium+byod count (Diversey) ───────────
def run_count_agreement_diversey():
    print("── C. COUNT AGREEMENT — Diversey shape (49) engine == store report ─────────────")
    # A non-empty contract_type_map marks the tenant CONFIG-DRIVEN so the non-phone activation auto-count
    # (Tablet / Home Internet / Edge) fires in BOTH _resolve_ct_bucket paths. Map only the plain labels;
    # the auto-count handles the non-phone categories by keyword.
    ct_map = {"activation": "premium", "port": "premium", "byod": "byod"}
    rows = []
    for i in range(7):
        rows.append(_row(f"NA{i}", "REP D", ct="Activation", store="Diversey"))       # 7 new activation
    for i in range(18):
        rows.append(_row(f"PT{i}", "REP D", ct="Port", store="Diversey"))             # 18 port -> premium
    for i in range(6):
        rows.append(_row(f"BY{i}", "REP D", ct="BYOD", store="Diversey"))             # 6 byod
    for i in range(12):
        rows.append(_row(f"TB{i}", "REP D", ct="Tablet", store="Diversey"))           # 12 tablet (auto)
    for i in range(5):
        rows.append(_row(f"HI{i}", "REP D", ct="Home Internet", store="Diversey"))    # 5 home internet
    for i in range(1):
        rows.append(_row(f"ED{i}", "REP D", ct="Edge", store="Diversey"))             # 1 edge
    # a few accessories + an upgrade that must NOT count as activations
    for i in range(3):
        rows.append(_row(f"AC{i}", "REP D", dept="Accessories", cat="Accessories", ext=25.0,
                         store="Diversey"))
    rows.append(_row("UP0", "REP D", ct="Upgrade", store="Diversey"))

    # DISPLAY: the store report's distinct-txn premium+byod count for the same feed.
    client = FakeClient(_base_tables([ACTIVATION_RULE, ACCESSORY_RULE], ct_map=ct_map))
    acfg = cr._accessory_config(client, ORG)
    cells = cr._sales_cell_agg(rows, acfg)
    display_prem = len(set().union(*[c["_prem"] for c in cells.values()])) if cells else 0
    display_byod = len(set().union(*[c["_byod"] for c in cells.values()])) if cells else 0
    display_act = display_prem + display_byod
    print(f"   store report     : premium {display_prem} + byod {display_byod} = {display_act}")

    # ENGINE: $10/activation on the same feed; paid activations = total / $10.
    t = _base_tables([ACTIVATION_RULE], ct_map=ct_map)
    t[("commcalc", "raw_sales")] = rows
    res = _preview(t)
    engine_pay = _money(res["totals"]["payout"])
    engine_act = int(round(engine_pay / 10.0))
    print(f"   engine paid      : ${engine_pay:,.2f}  ->  {engine_act} activations")
    assert display_act == 49, f"display expected 49, got {display_act}"
    assert engine_act == 49, f"engine expected 49, got {engine_act}"
    assert engine_pay == 490.0, engine_pay
    print("   PASS — both reconcile to 49; upgrades and accessories correctly excluded\n")


# ── D. per_transaction vs per_line on a multi-line activation ───────────────────────────────────────
def run_per_transaction_vs_per_line():
    print("── D. PER-TRANSACTION prevents the multi-line over-count ───────────────────────")
    # ONE premium activation whose contract-type label lands on TWO lines of the same transaction.
    rows = [_row("MULTI", "REP T", ct="Activation"),
            dict(_row("MULTI", "REP T", ct="Activation"), product_desc="second line")]

    t_txn = _base_tables([ACTIVATION_RULE])                       # unit_basis=per_transaction
    t_txn[("commcalc", "raw_sales")] = rows
    pay_txn = _money(_preview(t_txn)["totals"]["payout"])

    line_rule = dict(ACTIVATION_RULE, unit_basis="")             # per_line default
    t_line = _base_tables([line_rule])
    t_line[("commcalc", "raw_sales")] = rows
    pay_line = _money(_preview(t_line)["totals"]["payout"])

    print(f"   per_transaction  : ${pay_txn:,.2f}  (expect $10.00 — 1 activation)")
    print(f"   per_line default : ${pay_line:,.2f}  (would be $20.00 — the over-count)")
    assert pay_txn == 10.0, pay_txn
    assert pay_line == 20.0, pay_line
    print("   PASS — per_transaction matches the report's distinct-txn count\n")


# ── E. TOTAL: one activation transaction, many lines, one phone -> pays $10 ONCE ────────────────────
def run_total_multi_line_one_activation():
    print("── E. TOTAL blank-CT: activation+insurance+handset, one phone -> $10 once ──────")
    # A Total activation: contract_type BLANK on every line; the activation SIGNAL is the
    # 'activation payment' line (department 'system'), and the transaction also rings an insurance line
    # and a handset line — all under ONE trans_id / one phone number (mdn).
    activation_rules = [{"bucket": "premium",
                         "all_of": [{"field": "department", "equals_any": ["system"]}]}]
    ct_map = {"__force_config_driven__": "none"}  # non-empty => tenant is config-driven (Total)
    rows = [
        _row("T1", "REP N", ct="", dept="system", cat="activation payment", ext=25.0),   # the signal
        _row("T1", "REP N", ct="", dept="insurance", cat="protection", ext=15.0),        # insurance
        _row("T1", "REP N", ct="", dept="handset", cat="phone", ext=800.0),              # handset
    ]
    for r in rows:
        r["mdn"] = "5551234567"   # ONE phone number across all three lines

    t = _base_tables([ACTIVATION_RULE], ct_map=ct_map, activation_rules=activation_rules)
    t[("commcalc", "raw_sales")] = rows
    res = _preview(t)
    pay = _money(res["totals"]["payout"])
    print(f"   engine paid      : ${pay:,.2f}  (expect $10.00 — one activation, not 3 lines)")
    assert pay == 10.0, pay

    # And the display report counts this same transaction as exactly ONE activation.
    client = FakeClient(t)
    acfg = cr._accessory_config(client, ORG)
    cells = cr._sales_cell_agg(rows, acfg)
    disp = sum(len(c["_prem"]) + len(c["_byod"]) for c in cells.values())
    print(f"   store report     : {disp} activation(s)  (expect 1)")
    assert disp == 1, disp
    print("   PASS — activation_bucket collapses the multi-line Total txn to one payment/count\n")


if __name__ == "__main__":
    run_recipe_1100()
    run_editor_offers_pct_price()
    run_count_agreement_diversey()
    run_per_transaction_vs_per_line()
    run_total_multi_line_one_activation()
    print("ALL PROOFS PASS")
