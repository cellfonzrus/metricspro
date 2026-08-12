"""HARNESS — "What would I make?" pays accessories, and includes the multi-month chain
(mod-commission, owner 2026-08-12: "what will i make does not include the multi month and calculates 0
for accessories … 2 options for accessories one monthly goal and second per item, for example
$30 x 50 = 1500 * 17.5%, or $6000*17.5% — the user should be able to use both to assess").

Proves five things, offline, against an in-memory fake Supabase client (no network, no DB, no writes):

  A. ACCESSORY $0 — REPRODUCED, THEN FIXED. A `pct_price` accessory rule (the kind luxelink's own
     accessory rules use) paid $0 for any quantity because `pct_price` was missing from the
     simulator's `_KIND_INPUT` map, so no price input existed and every synthetic line was minted at
     ext_price 0. The negative control removes the entry again and asserts the OLD $0, so this test
     fails if the fix is ever reverted.
  B. BOTH READINGS AGREE. 50 accessories at $30 each (per-item) and $1,500 of accessories this month
     (monthly goal) are the SAME sale, so they must pay the same 17.5% — and $6,000/month pays $1,050.
  C. PARITY. Every dollar equals what `commission_engine.preview()` returns for the same lines: the
     simulator must still own no arithmetic of its own.
  D. MULTI-MONTH. A 3-month chain (M1 5% of MRC, M2 flat $0, M3 13% of MRC) on 10 activations at $65
     MRC is projected month by month by the REAL `sale_installment_engine`, and the totals match hand
     arithmetic: M1 $32.50, M3 $84.50, chain $117.00 — while the plan-rule total stays separate.
  E. ENGINE SAFETY. The new `_sales_override` hook cannot be persisted, and a line that does NOT carry
     the simulation key resolves its MRC exactly as it did before (no drift for real sales).

    python3 backend/harness_sim_accessory_multimonth.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_engine as ce            # noqa: E402
from app.modules.commcalc import pay_simulator as ps                # noqa: E402
from app.modules.commcalc import sale_installment_engine as sie     # noqa: E402

ORG = "00000000-0000-0000-0000-0000000000aa"
REP = "doe, jane"
STORE = "1234 MAIN ST"
PERIOD = "June 2026"
PLAN = "P1"


# ── in-memory fake of the client surface both engines use (real .eq filtering, no write verbs) ─────
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


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, name):
        return _Schema(self._t, name)

    def table(self, t):
        return _Q(self._t.get(("public", t), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


ACCESSORY_RULE = {
    "id": "RACC", "label": "accessory", "match_field": "accessory", "match_op": "equals",
    "match_value": "yes", "payout_kind": "pct_price", "amount": 0.175, "pct": 0.175,
    "tiered": False, "sort": 1,
}
# The 3MR chain luxelink actually runs: premium/byod activations, 3 months, 5% then nothing then 13%.
SCHEDULE = {
    "id": "S1", "org_id": ORG, "plan_id": PLAN, "name": "3MR Commission Payment", "is_active": True,
    "trigger_match_field": "activation_bucket", "trigger_match_op": "in",
    "trigger_match_value": "premium,byod", "num_months": 3, "gate_mode": "paid_residual",
    "m1_gate": "inherit", "gate_from_month": 1, "qualifying_categories": None, "category_payout": None,
    "effective_from": None, "effective_to": None, "eligible_sale_periods": None,
}
SCHEDULE_LINES = [
    {"id": "L1", "org_id": ORG, "schedule_id": "S1", "month_index": 1, "payout_kind": "pct_mrc",
     "flat_amount": 0.0, "mrc_pct": 0.05, "mrc_source": "product_catalog"},
    {"id": "L2", "org_id": ORG, "schedule_id": "S1", "month_index": 2, "payout_kind": "flat",
     "flat_amount": 0.0, "mrc_pct": 0.0, "mrc_source": "product_catalog"},
    {"id": "L3", "org_id": ORG, "schedule_id": "S1", "month_index": 3, "payout_kind": "pct_mrc",
     "flat_amount": 0.0, "mrc_pct": 0.13, "mrc_source": "product_catalog"},
]


def tables(rules=(ACCESSORY_RULE,), with_schedule=False):
    t = {
        ("commcalc", "commission_plan"): [
            {"id": PLAN, "org_id": ORG, "name": "Total Employee Comp Chicago", "is_active": True,
             "carrier_id": None, "base_tier_metric": "none", "tier_count_basis": None,
             "tier_below_min_multiplier": None}],
        ("commcalc", "commission_rule"): [dict(r, org_id=ORG, plan_id=PLAN) for r in rules],
        ("commcalc", "commission_tier"): [],
        ("commcalc", "commission_plan_assignment"): [
            {"id": "A1", "org_id": ORG, "plan_id": PLAN, "scope": "default", "scope_value": None}],
        ("commcalc", "store_mapping"): [
            {"org_id": ORG, "store_code": "1234", "store_address": STORE, "market": "NY"}],
        # The tenant's accessory definition — what makes the synthetic accessory line classify.
        ("commcalc", "accessory_config"): [
            {"org_id": ORG, "departments": ["Ondigo"], "categories": ["Accessories"],
             "product_keywords": []}],
    }
    if with_schedule:
        t[("commcalc", "plan_installment_schedule")] = [SCHEDULE]
        t[("commcalc", "plan_installment_line")] = SCHEDULE_LINES
    return t


def _money(v):
    return round(float(v or 0), 2)


def _sim(client, inputs):
    return ps.simulate(client, ORG, PERIOD, REP, STORE, "NY", inputs)


# ── A. the reported defect: accessories paid $0, with a negative control ───────────────────────────
def run_accessory_zero():
    print("── A. ACCESSORY $0 (pct_price) — reproduced, then fixed ────────────────────────")
    client = FakeClient(tables())
    inputs = {"rule:RACC": {"units": 50, "amount": 30, "basis": "item"}}
    fixed = _money((_sim(client, inputs).get("result") or {}).get("total_payout"))

    # NEGATIVE CONTROL — put the module back the way it was and assert the old, wrong answer.
    saved = ps._KIND_INPUT.pop("pct_price")
    try:
        old = _money((_sim(FakeClient(tables()), inputs).get("result") or {}).get("total_payout"))
    finally:
        ps._KIND_INPUT["pct_price"] = saved

    want = _money(50 * 30 * 0.175)                       # $262.50
    ok = fixed == want and old == 0.0
    print(f"  50 accessories x $30 @ 17.5%   before ${old:>8,.2f}  after ${fixed:>8,.2f}  "
          f"expected ${want:>8,.2f}   {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"     (negative control must be $0.00 and the fix must be ${want:,.2f})")
    return ok


# ── B. the owner's two readings of the same month, and C. parity with the engine ───────────────────
def run_two_bases():
    print("── B/C. PER ITEM vs MONTHLY GOAL (+ engine parity) ─────────────────────────────")
    cases = [
        ("50 items x $30      (per item)", {"units": 50, "amount": 30, "basis": "item"}, 262.50),
        ("$1,500 this month   (monthly)", {"units": 50, "amount": 1500, "basis": "month"}, 262.50),
        ("$6,000 this month   (monthly)", {"units": 0, "amount": 6000, "basis": "month"}, 1050.00),
        ("$6,000 over 200     (monthly)", {"units": 200, "amount": 6000, "basis": "month"}, 1050.00),
    ]
    ok = True
    for name, spec, want in cases:
        client = FakeClient(tables())
        inputs = {"rule:RACC": spec}
        sim = _sim(client, inputs)
        got = _money((sim.get("result") or {}).get("total_payout"))
        # PARITY: the same lines through preview() directly must produce the identical number.
        plan, _ready, _r = ps._resolve_my_plan(FakeClient(tables()), ORG, REP, STORE, "NY")
        lines, mrc, _a, _w = ps.build_lines(FakeClient(tables()), ORG, plan, REP, STORE, PERIOD, inputs)
        pv = ce.preview(FakeClient(tables()), ORG, PERIOD, plan_id=PLAN, only_rep=REP,
                        sales_override=lines, mrc_override=mrc)
        eng = _money((pv.get("by_rep") or [{}])[0].get("total_payout"))
        good = got == _money(want) and got == eng
        ok = ok and good
        print(f"  {name}  ->  ${got:>9,.2f}  (engine ${eng:>9,.2f}, expected ${want:>9,.2f})   "
              f"{'PASS' if good else 'FAIL'}")
    return ok


# ── D. the multi-month chain the projection used to omit entirely ──────────────────────────────────
def run_multimonth():
    print("── D. MULTI-MONTH CHAIN (3MR: M1 5%, M2 flat $0, M3 13% of MRC) ────────────────")
    client = FakeClient(tables(with_schedule=True))
    plan, _ready, _r = ps._resolve_my_plan(client, ORG, REP, STORE, "NY")
    levers = ps.build_multimonth_levers(client, ORG, plan)
    if not levers:
        print("  FAIL — no multi-month lever was built from the schedule")
        return False
    lv = levers[0]
    inputs = {lv["key"]: {"units": 10, "amount": 65}}
    mm = ps.simulate_multimonth(FakeClient(tables(with_schedule=True)), ORG, PERIOD, REP, STORE,
                                plan, inputs)
    by_month = {m["month_index"]: _money(m["amount"]) for m in (mm.get("months") or [])}
    want = {1: _money(10 * 65 * 0.05), 2: 0.0, 3: _money(10 * 65 * 0.13)}   # 32.50 / 0 / 84.50
    chain = _money(mm.get("total_chain"))
    ok = by_month == want and chain == _money(sum(want.values()))
    for m in sorted(want):
        print(f"  month {m}  projected ${by_month.get(m, 0.0):>8,.2f}   expected ${want[m]:>8,.2f}")
    print(f"  chain total ${chain:>8,.2f}  expected ${_money(sum(want.values())):>8,.2f}   "
          f"{'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"     months={mm.get('months')}  warnings={mm.get('warnings')}")

    # The projected activation must land in a category the owner's defaults PAY on. A placeholder
    # serial resolves to 'unknown', and a tenant that switches 'unknown' off would then see the whole
    # chain silently drop to $0 — so assert the engine reads these as phones.
    lines, _err = ps.build_multimonth_lines(FakeClient(tables(with_schedule=True)), ORG, lv, REP,
                                            STORE, PERIOD, {"units": 2, "amount": 65})
    res = sie.compute_sale_installments(FakeClient(tables(with_schedule=True)), ORG, PERIOD,
                                        persist=False, _sales_override={PERIOD: lines})
    cats = {r.get("device_category") for r in (res.get("ledger") or [])}
    cat_ok = cats == {"phone"}
    print(f"  projected activations classify as {sorted(cats) or ['(none)']}   "
          f"{'PASS' if cat_ok else 'FAIL'}")

    # And the two engines stay SEPARATE figures in the payload the page renders.
    full = ps.simulate(FakeClient(tables((ACCESSORY_RULE,), with_schedule=True)), ORG, PERIOD, REP,
                       STORE, "NY", {"rule:RACC": {"units": 50, "amount": 30, "basis": "item"},
                                     lv["key"]: {"units": 10, "amount": 65}})
    res = full.get("result") or {}
    rules_only = _money(res.get("total_payout"))
    this_month = _money(res.get("this_month_total"))
    grand = _money(res.get("chain_grand_total"))
    sep_ok = (rules_only == 262.50 and this_month == _money(262.50 + 32.50)
              and grand == _money(262.50 + 117.00))
    print(f"  plan rules ${rules_only:,.2f} | this month ${this_month:,.2f} | "
          f"over the chain ${grand:,.2f}   {'PASS' if sep_ok else 'FAIL'}")
    return ok and sep_ok and cat_ok


# ── E. the engine hooks are read-only and invisible to real sales ──────────────────────────────────
def run_engine_safety():
    print("── E. ENGINE HOOKS — read-only, and no drift for real lines ────────────────────")
    client = FakeClient(tables(with_schedule=True))
    ok = True
    try:
        sie.compute_sale_installments(client, ORG, PERIOD, persist=True, _sales_override={PERIOD: []})
        print("  FAIL — persisting a simulated override was ALLOWED")
        ok = False
    except ValueError:
        print("  persist=True with _sales_override -> refused (ValueError)                  PASS")

    # A real line (no simulation key) must resolve its MRC through the unchanged ladder.
    real = {"product_desc": "Total ALL ACCESS Plan $65", "ext_price": 65, "customer_plan": ""}
    r_rank, r_mrc, r_src = sie._mrc_candidate(real, {}, None, None)
    sim_line = dict(real, **{sie.SIM_MRC_KEY: 42.0})
    s_rank, s_mrc, s_src = sie._mrc_candidate(sim_line, {}, None, None)
    drift_ok = r_src != "simulated" and (s_src, s_mrc, s_rank) == ("simulated", 42.0, 0)
    print(f"  real line -> ({r_rank}, ${r_mrc:,.2f}, {r_src}) | simulated -> "
          f"({s_rank}, ${s_mrc:,.2f}, {s_src})   {'PASS' if drift_ok else 'FAIL'}")

    # And the default call path (no override at all) still reads sales from the database layer.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "app/modules/commcalc/sale_installment_engine.py"), encoding="utf-8").read()
    default_ok = "if _sales_override is not None else _read_sales(client, org_id, sale_period)" in src
    print(f"  _sales_override=None still calls _read_sales                                "
          f"{'PASS' if default_ok else 'FAIL'}")
    return ok and drift_ok and default_ok


def run_no_writes():
    print("── F. NO-WRITE ─────────────────────────────────────────────────────────────────")
    import re
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "app/modules/commcalc/pay_simulator.py"), encoding="utf-8").read()
    bad = [w for w in (".insert(", ".upsert(", ".delete(", ".rpc(") if w in src]
    chained = [ln.strip() for ln in src.splitlines()
               if ".table(" in ln and re.search(r"\.(insert|upsert|update|delete)\(", ln)]
    persist = "persist=False" in src and "persist=True" not in src
    ok = not bad and not chained and persist
    print(f"  no write verbs in pay_simulator.py, installment engine called persist=False   "
          f"{'PASS' if ok else 'FAIL'}")
    if bad or chained:
        print(f"     found={bad or chained}")
    return ok


if __name__ == "__main__":
    results = [run_accessory_zero(), run_two_bases(), run_multimonth(), run_engine_safety(),
               run_no_writes()]
    print("\n" + ("ALL PASS" if all(results) else "FAILURES ABOVE"))
    sys.exit(0 if all(results) else 1)
