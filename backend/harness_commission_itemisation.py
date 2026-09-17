"""DB-FREE PROOF — a plan-mode rep_commissions row ITEMISES what the rep was actually paid.

OWNER, 2026-09-17: "Did the gap" — approving the fix to the `acc_comm` / `subtotal` blanking.

THE DEFECT, and why it was worth money even though it moved none:
  `calculator.calc_rep_commissions` emits a ZEROED SKELETON row for every rep of a non-Boost
  (plan-mode) tenant — `acc_comm: 0`, `subtotal: 0`, every component literally 0 — deliberately,
  because the Boost flat-spiff/KPI model does not apply and there is no `payout_config` to compute one
  from. `_apply_engine_components_to_row` then filled in `plan_comm` / `total_payout` and NOTHING
  ELSE. So a CORRECT total sat beside a breakdown of $0.00, on every plan.
  Live, org 854f6d7b, July + August 2026: **0 of 90 rows had acc_comm > 0** — not on the exec_mtd
  plan, not on either rules plan. One rep read that breakdown, concluded he had been paid nothing for
  accessories, and opened a dispute over $400 that the system had in fact paid him. A blank
  itemisation on a correct total manufactures disputes.

THE HARD CONSTRAINT THIS HARNESS EXISTS TO ENFORCE: **this is a DISPLAY fix. `total_payout` must not
move by one cent.** §C asserts it directly, §D asserts it for the Boost path, and §F asserts the row's
own identity (subtotal x tier + installments == total_payout) still closes.

WHAT IS DELIBERATELY NOT WRITTEN (§E) — `premium_comm` / `byod_comm` / `upgrade_comm` stay 0. The
activation money is real, but the counts stored beside them come from the CALCULATOR's raw_sales
classification while the paying basis may count from Activation Details (per-plan `activation_source`);
measured live the two disagree for most reps on all three legs. Writing pay beside those counts would
make pay / count read as a rate nobody is paid, so the activation slice is the ARITHMETIC COMPLEMENT
and is documented as such. Naming what is unrepresentable beats inventing a column that lies.

Run:  cd backend && python3 harness_commission_itemisation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.modules.commcalc.commission_engine as CE
from app.modules.commcalc.router import _apply_engine_components_to_row as APPLY
from app.modules.commcalc.router import _rep_comm_row_keys as KEYS

FAILS = []
N = [0]


def ok(label, cond, detail=""):
    N[0] += 1
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"   {detail}"))
    if not cond:
        FAILS.append(label)


def eq(label, got, want):
    ok(label, got == want, f"got={got!r} want={want!r}")


COLS = {c: True for c in ("residual_installment_comm", "installment_comm_sale", "plan_comm",
                          "plan_name", "carrier_statement_comm", "setup_fee_comm")}


def skeleton(rep="KELLIE, MARK", **over):
    """EXACTLY the shape calculator.calc_rep_commissions emits in plan mode: counts, zeros, nothing else."""
    row = {"epay_salesperson": rep, "storeops_name": "", "store": "957 Pennsylvania Avenue",
           "tier": 1.0, "tier_source": "plan", "kpis_met": 0, "total_kpis": 0, "kpi_values": {},
           "premium_acts": 48, "byod_acts": 16, "upgrade_acts": 5,
           "premium_comm": 0, "byod_comm": 0, "upgrade_comm": 0, "acc_comm": 0,
           "setup_fee_comm": 0, "trade_in_comm": 0, "acima_comm": 0, "custom_comm": 0,
           "acc_target": 0, "subtotal": 0, "total_payout": 0}
    row.update(over)
    return row


def run(row, pv, inst=0.0, sale=0.0, stmt=0.0):
    ks = KEYS(row)
    pbr = {} if pv is None else {str(row["epay_salesperson"]).upper(): pv}
    APPLY(row, ks, {k: inst for k in ks}, {k: sale for k in ks}, {k: stmt for k in ks}, pbr, COLS)
    return row


# ══ §A  THE REGRESSION — the blanked row, reproduced then fixed ══════════════════════════════════
print("\n§A  the reported defect: a correct total beside a $0.00 breakdown")
# The pay writer used to receive ONLY {amount, plan_name, setup_fee_comm}. That is this pv.
legacy_pv = {"amount": 777.78, "plan_name": "A PLAN", "setup_fee_comm": 0.0}
r = run(skeleton(), legacy_pv)
eq("A1 the total was always right", r["total_payout"], 777.78)
eq("A2 DEFECT REPRODUCED: with no slice handed over, acc_comm stays 0", r["acc_comm"], 0)
ok("A3 ...but `subtotal` is no longer blank — the plan total names itself", r["subtotal"] == 777.78,
   f"subtotal={r['subtotal']!r}")
# and with the slice handed over, as both pay bases now do:
r = run(skeleton(), {"amount": 777.78, "plan_name": "A PLAN", "setup_fee_comm": 0.0,
                     "acc_comm": 57.78})
eq("A4 FIXED: the accessory slice is named", r["acc_comm"], 57.78)
eq("A5 the total is untouched by naming it", r["total_payout"], 777.78)
eq("A6 subtotal = the plan's own payout", r["subtotal"], 777.78)
eq("A7 the activation slice is the complement, and it is the right number",
   round(r["subtotal"] - r["acc_comm"] - r["setup_fee_comm"], 2), 720.00)

# ══ §B  THE ACCESSORY SLICE IS READ FROM THE RULE'S OWN match_field ══════════════════════════════
print("\n§B  accessory_slice: the plan's own rules decide, nothing is hard-coded")
rules = [{"id": "acc", "match_field": "accessory"},
         {"id": "twp", "match_field": "product_desc"},
         {"id": "acc2", "match_field": "  Accessory  "},
         {"id": "edge", "match_field": "tender_type"}]
rb = {"acc": {"payout": 359.94, "tiered": False}, "twp": {"payout": 30.0, "tiered": False},
      "acc2": {"payout": 20.0, "tiered": False}, "edge": {"payout": 25.0, "tiered": False}}
eq("B1 sums every accessory rule, ignores the rest", CE.accessory_slice(rules, rb, 1.0), 379.94)
ok("B2 match_field is case/space-insensitive", CE.is_accessory_rule({"match_field": " ACCESSORY "}))
ok("B3 a non-accessory field is not claimed", not CE.is_accessory_rule({"match_field": "product_desc"}))
ok("B4 a missing match_field is not claimed", not CE.is_accessory_rule({}))
eq("B5 no accessory rule -> 0.0, a TRUE statement about that plan",
   CE.accessory_slice([{"id": "x", "match_field": "product_desc"}], {"x": {"payout": 9.0}}, 1.0), 0.0)
eq("B6 a rule that matched nothing contributes nothing",
   CE.accessory_slice([{"id": "acc", "match_field": "accessory"}], {}, 1.0), 0.0)
rb_t = {"acc": {"payout": 100.0, "tiered": True}, "flat": {"payout": 50.0, "tiered": False}}
rules_t = [{"id": "acc", "match_field": "accessory"}, {"id": "flat", "match_field": "accessory"}]
eq("B7 a TIERED accessory rule is scaled by the same multiplier its dollars were",
   CE.accessory_slice(rules_t, rb_t, 0.5), 100.00)
ok("B8 so the slice can never exceed the payout it is a slice of",
   CE.accessory_slice(rules_t, rb_t, 0.5) <= round(100.0 * 0.5 + 50.0, 2))
eq("B9 rounds to the cent", CE.accessory_slice(
    [{"id": "a", "match_field": "accessory"}], {"a": {"payout": 577.82 * 0.1}}, 1.0), 57.78)
eq("B10 None inputs are survivable", CE.accessory_slice(None, None, 1.0), 0.0)

# ══ §C  THE HARD CONSTRAINT — total_payout does not move ═════════════════════════════════════════
print("\n§C  total_payout is identical with and without the itemisation")
for label, inst, sale in (("no installments", 0.0, 0.0), ("residual only", 12.34, 0.0),
                          ("sale only", 0.0, 4.75), ("both", 12.34, 4.75)):
    base = {"amount": 263.13, "plan_name": "P", "setup_fee_comm": 0.0}
    without = run(skeleton(), dict(base), inst=inst, sale=sale)["total_payout"]
    withit = run(skeleton(), {**base, "acc_comm": 208.13}, inst=inst, sale=sale)["total_payout"]
    ok(f"C1 {label}: {withit} == {without}", withit == without, f"{withit} != {without}")
eq("C2 the total is still base + the two installment legs", withit, round(263.13 + 12.34 + 4.75, 2))
# a slice that is somehow larger than the plan total must STILL not touch the total
r = run(skeleton(), {"amount": 100.0, "plan_name": "P", "setup_fee_comm": 0.0, "acc_comm": 999.0})
eq("C3 even an absurd slice cannot move the total", r["total_payout"], 100.0)

# ══ §D  BOOST IS UNTOUCHED (pv is None — no plan) ════════════════════════════════════════════════
print("\n§D  a Boost row has no plan, so neither column is written")
boost = skeleton(rep="SAUL, MARK", acc_comm=377.19, subtotal=871.19, total_payout=871.19, tier=0.5)
r = run(dict(boost), None)
eq("D1 acc_comm is the calculator's own number, not overwritten", r["acc_comm"], 377.19)
eq("D2 subtotal is the calculator's own number, not overwritten", r["subtotal"], 871.19)
eq("D3 the total keeps the standard calc + installments", r["total_payout"], 871.19)

# ══ §E  WHAT IS DELIBERATELY NOT WRITTEN (no fudge row) ══════════════════════════════════════════
print("\n§E  the unrepresentable legs are left at 0 on purpose, not filled with a plausible lie")
r = run(skeleton(), {"amount": 777.78, "plan_name": "P", "setup_fee_comm": 0.0, "acc_comm": 57.78,
                     # even if a caller offered them, the writer must not place activation pay beside
                     # counts produced by a DIFFERENT classifier
                     "premium_comm": 490.0, "byod_comm": 160.0, "upgrade_comm": 0.0})
eq("E1 premium_comm stays 0", r["premium_comm"], 0)
eq("E2 byod_comm stays 0", r["byod_comm"], 0)
eq("E3 upgrade_comm stays 0", r["upgrade_comm"], 0)
eq("E4 the counts are left exactly as the calculator classified them",
   (r["premium_acts"], r["byod_acts"], r["upgrade_acts"]), (48, 16, 5))
ok("E5 and the activation money is still recoverable, as the complement",
   round(r["subtotal"] - r["acc_comm"] - r["setup_fee_comm"], 2) == 720.00)

# ══ §F  THE ROW'S OWN IDENTITY STILL CLOSES ══════════════════════════════════════════════════════
print("\n§F  subtotal x tier + installments == total_payout")
for amount, inst, sale in ((777.78, 0.0, 0.0), (263.13, 0.0, 4.75), (434.94, 0.0, 71.75),
                           (957.36, 5.0, 2.5)):
    r = run(skeleton(), {"amount": amount, "plan_name": "P", "setup_fee_comm": 0.0,
                         "acc_comm": round(amount * 0.1, 2)}, inst=inst, sale=sale)
    lhs = round(r["subtotal"] * r["tier"] + inst + sale, 2)
    ok(f"F1 amount={amount} inst={inst} sale={sale}: {lhs} == {r['total_payout']}",
       lhs == r["total_payout"], f"{lhs} != {r['total_payout']}")
eq("F2 plan mode stores tier 1.0 / 'plan', which is what makes that identity hold",
   (skeleton()["tier"], skeleton()["tier_source"]), (1.0, "plan"))

# ══ §G  SET-UP FEE IS A NAMED SLICE TOO, ONCE THE CONFIG IS LIVE ═════════════════════════════════
print("\n§G  the set-up-fee slice composes with the accessory slice")
r = run(skeleton(), {"amount": 852.78, "plan_name": "P", "setup_fee_comm": 75.0, "acc_comm": 57.78})
eq("G1 both slices are named", (r["acc_comm"], r["setup_fee_comm"]), (57.78, 75.0))
eq("G2 subtotal still equals the plan total", r["subtotal"], 852.78)
eq("G3 the complement is the activation slice",
   round(r["subtotal"] - r["acc_comm"] - r["setup_fee_comm"], 2), 720.00)
eq("G4 the total is still untouched", r["total_payout"], 852.78)

# ══ §H  RULE TWO ═════════════════════════════════════════════════════════════════════════════════
print("\n§H  no tenant/carrier/market/product name in the new logic")
import ast as _ast
_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules", "commcalc",
                  "commission_engine.py")
_src = open(_p).read()
_tree = _ast.parse(_src)
_docs = set()
for _n in _ast.walk(_tree):
    if isinstance(_n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
        _b = getattr(_n, "body", None) or []
        if _b and isinstance(_b[0], _ast.Expr) and isinstance(_b[0].value, _ast.Constant) \
                and isinstance(_b[0].value.value, str):
            _docs.update(range(_b[0].lineno, _b[0].end_lineno + 1))
_fn = [n for n in _tree.body if isinstance(n, _ast.FunctionDef)
       and n.name in ("accessory_slice", "is_accessory_rule")]
eq("H1 both helpers exist as PURE module-level functions", len(_fn), 2)
_lines = _src.splitlines()
_keep = []
for _n in _fn:
    for _i in range(_n.lineno, _n.end_lineno + 1):
        if _i in _docs:
            continue                      # the docstring may quote the owner; prose is not a branch
        _l = _lines[_i - 1]
        if _l.strip().startswith("#"):
            continue
        _keep.append(_l)
_body = "\n".join(_keep).lower()
_banned = ("luxelink", "boost", "cricket", "total wireless", "chicago", '"ny"', "'ny'",
           "accessories", "handsetbranded", "c2wireless")
eq("H2 the helpers name no tenant, carrier, market, department or product",
   [w for w in _banned if w in _body], [])
# The real invariant is not "how many times does the word appear" (it is a legitimate field NAME in
# the allowed-fields list and in the per-line stamp) but "how many places COMPARE match_field to it".
# That comparison is the predicate, and there must be exactly one — inside is_accessory_rule.
import re as _re
_cmp = [(i + 1, l) for i, l in enumerate(_src.splitlines())
        if _re.search(r"match_field.*==.*accessor", l, _re.I)
        or _re.search(r"accessor.*==.*match_field", l, _re.I)]
_pred = next(n for n in _fn if n.name == "is_accessory_rule")
_outside = [c for c in _cmp if not (_pred.lineno <= c[0] <= _pred.end_lineno)]
eq("H3 the match_field comparison exists in exactly ONE place (is_accessory_rule)",
   [c[0] for c in _outside], [])
ok("H4 and the predicate is CALLED, not re-typed, at every use site",
   _src.count("is_accessory_rule(") >= 4,
   f"call sites (incl. def) = {_src.count('is_accessory_rule(')}")

print("\n" + "=" * 78)
print(f"{N[0]} checks, {len(FAILS)} failed" + ("" if not FAILS else f"  -> {FAILS}"))
print("=" * 78)
sys.exit(1 if FAILS else 0)
