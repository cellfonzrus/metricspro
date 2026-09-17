"""DB-FREE PROOF — set-up / activation-fee pay, MARKET SCOPE + the exec-MTD pay leg.

OWNER, 2026-09-17 (verbatim): "Also need tot commission agent to factor in device set up fee to be
included while calculating thr commission for ny employees for lyluxlink set up as 10% as default and
then recalculate the employee commission", and, asked who it covers: "Create company wide but default
allowed for NY and if need be a checkbox enabling for all markets if required".

WHAT THIS HARNESS HAS TO EARN
  1. COMPANY-WIDE, NOT A BRANCH. The market dimension is a generic third scope beside `default` and
     `by_carrier`. No market, tenant or carrier NAME appears in any module under test (§G asserts it).
  2. INERT FOR EVERYONE ELSE. A tenant with no `by_market` rows — which is every tenant the day this
     ships, including the house/Boost org — resolves byte-identically to mig 263 (§A, §H).
  3. THE CHECKBOX SAYS NO OUT LOUD. With markets named and `all_markets` false, a rep in an unnamed
     market pays $0 through the ORDINARY 'excluded' path with the named source `market_not_enabled` —
     never a silent zero, never a guessed rate (§C).
  4. THE REGRESSION. `router._override_plan_by_rep_with_mtd` used to hand the pay writer a HARD-CODED
     `setup_fee_comm: 0.0`, so every rep on an exec_mtd-basis plan was paid $0 of a configured fee no
     matter what the tenant entered. §J reproduces that defect and pins the fix (this is the bug that
     made the owner's "default allowed for NY" pay nothing, because the NY plan is exec_mtd-basis).

NOT A SIBLING OF `scratchpad/setup_fee_monetization_proof.py`: that one proves the mig-263 package
moves $0 on merge (Boost byte-identity, recognition un-forked, NULL-vs-0). This one proves only the
2026-09-17 additions — the market dimension and the exec-MTD pay leg — and deliberately re-uses the
same pure functions rather than restating their arithmetic.

Run:  cd backend && python3 harness_setup_fee_market_scope.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.modules.commcalc.setup_fee_pay as SFP

FAILS = []
CHECKS = [0]


def ok(label, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}   {detail}")
        FAILS.append(label)


def eq(label, got, want):
    ok(label, got == want, f"got={got!r} want={want!r}")


SET_ON_10 = {"include_in_commission": True, "employee_pct_of_collected": 0.10,
             "dealer_share_pct": 0.5}
SET_ON_5 = {"include_in_commission": True, "employee_pct_of_collected": 0.05,
            "dealer_share_pct": 0.5}


def cfg(**kw):
    return SFP.normalize_pay_config(kw)


# ══ §A  INERT WITHOUT MARKET ROWS — every tenant today, including the house/Boost org ════════════
print("\n§A  no by_market rows -> mig-263 behaviour, byte-identical")
legacy = cfg(default=SET_ON_10, by_carrier={"car-1": SET_ON_5})
s, src = SFP.resolve_for_scope(legacy, None, None)
eq("A1 org default resolves as before", (s["employee_pct_of_collected"], src), (0.10, "org_default"))
s, src = SFP.resolve_for_scope(legacy, "car-1", None)
eq("A2 carrier row still wins", (s["employee_pct_of_collected"], src), (0.05, "carrier"))
s, src = SFP.resolve_for_scope(legacy, "car-1", "ANY MARKET AT ALL")
eq("A3 a market is IGNORED while no market row exists", (s["employee_pct_of_collected"], src),
   (0.05, "carrier"))
ok("A4 market_enabled is True when nobody named a market", SFP.market_enabled(legacy, "Anything"))
eq("A5 all_markets defaults False", legacy["all_markets"], False)
eq("A6 by_market defaults empty", legacy["by_market"], {})

# ══ §B  MARKET > CARRIER > DEFAULT ═══════════════════════════════════════════════════════════════
print("\n§B  precedence: market > carrier > org default")
c = cfg(default=SET_ON_5, by_carrier={"car-1": SET_ON_5}, by_market={"NORTH": SET_ON_10})
s, src = SFP.resolve_for_scope(c, "car-1", "north")
eq("B1 market row wins over a carrier row", (s["employee_pct_of_collected"], src), (0.10, "market"))
s, src = SFP.resolve_for_scope(c, None, "  NoRtH  ")
eq("B2 market key is case/space-insensitive", (s["employee_pct_of_collected"], src), (0.10, "market"))

# ══ §C  THE CHECKBOX, OFF — an unnamed market is excluded, and SAYS SO ═══════════════════════════
print("\n§C  all_markets=false -> an unnamed market pays $0 with a NAMED reason")
s, src = SFP.resolve_for_scope(c, "car-1", "SOUTH")
eq("C1 source names the reason", src, SFP.MARKET_NOT_ENABLED)
eq("C2 include_in_commission forced False", s["include_in_commission"], False)
pay, status = SFP.employee_pay(1000.0, s)
eq("C3 pays $0 through the ORDINARY excluded path", (pay, status), (0.0, "excluded"))
eq("C4 the stated percentage is NOT destroyed, only un-applied", s["employee_pct_of_collected"], 0.05)
s2, src2 = SFP.resolve_for_scope(c, "car-1", None)
eq("C5 a rep with NO resolved market is also not enabled", src2, SFP.MARKET_NOT_ENABLED)

# ══ §D  THE CHECKBOX, ON ═════════════════════════════════════════════════════════════════════════
print("\n§D  all_markets=true -> default/carrier apply everywhere; a market row still overrides")
c_all = cfg(default=SET_ON_5, by_carrier={"car-1": SET_ON_5}, by_market={"NORTH": SET_ON_10},
            all_markets=True)
s, src = SFP.resolve_for_scope(c_all, "car-1", "SOUTH")
eq("D1 unnamed market now inherits the carrier row", (s["employee_pct_of_collected"], src),
   (0.05, "carrier"))
s, src = SFP.resolve_for_scope(c_all, "car-1", "NORTH")
eq("D2 the named market still overrides", (s["employee_pct_of_collected"], src), (0.10, "market"))
eq("D3 pay follows the resolved rate", SFP.employee_pay(1000.0, s), (100.0, "paid"))

# ══ §E/§F  A BLANK IS NOT A ZERO ═════════════════════════════════════════════════════════════════
print("\n§E/§F  a market row with no percentage warns; an explicit 0 is a decision")
c_null = cfg(by_market={"NORTH": {"include_in_commission": True,
                                  "employee_pct_of_collected": None}})
s, _ = SFP.resolve_for_scope(c_null, None, "NORTH")
eq("E1 unstated percentage pays $0 AND reports it", SFP.employee_pay(750.0, s), (0.0, "unconfigured"))
c_zero = cfg(by_market={"NORTH": {"include_in_commission": True,
                                  "employee_pct_of_collected": 0}})
s, _ = SFP.resolve_for_scope(c_zero, None, "NORTH")
eq("F1 an explicit 0 is honoured silently", SFP.employee_pay(750.0, s), (0.0, "zero_by_choice"))

# ══ §G  DISPLAY SURFACES UNCHANGED + RULE TWO ════════════════════════════════════════════════════
print("\n§G  resolve_for_carrier is untouched; no carrier/tenant/market literal in the module")
s, src = SFP.resolve_for_carrier(c, "car-1")
eq("G1 the mig-263 resolver ignores the market gate (display asks 'what rate is stated?')",
   (s["employee_pct_of_collected"], src, s["include_in_commission"]), (0.05, "carrier", True))
# RULE TWO is about BEHAVIOUR, not prose: the module may QUOTE the owner (its docstring does), but no
# tenant, carrier or market name may survive into executable code. So strip every comment and every
# docstring first, then scan what is left — the lines that actually decide who gets paid.
import ast as _ast

_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "app", "modules", "commcalc", "setup_fee_pay.py")
_src = open(_path).read()
_tree = _ast.parse(_src)
_doc_spans = []
for _n in _ast.walk(_tree):
    if isinstance(_n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
        _b = getattr(_n, "body", None) or []
        if _b and isinstance(_b[0], _ast.Expr) and isinstance(_b[0].value, _ast.Constant) \
                and isinstance(_b[0].value.value, str):
            _doc_spans.append((_b[0].lineno, _b[0].end_lineno))
_drop = {i for a, b in _doc_spans for i in range(a, b + 1)}
_body = "\n".join(l for i, l in enumerate(_src.splitlines(), 1)
                  if i not in _drop and not l.strip().startswith("#")).lower()
_banned = ("luxelink", "boost", "cricket", "verizon", "chicago", "new york", '"ny"', "'ny'",
           "total wireless", "b2bsoft")
_hit = [w for w in _banned if w in _body]
ok("G2 no tenant/carrier/market name in EXECUTABLE code (docstrings/comments excluded)",
   not _hit, f"found {_hit}")

# ══ §H  NORMALISATION / GARBAGE ══════════════════════════════════════════════════════════════════
print("\n§H  normalisation")
eq("H1 None -> inert defaults",
   SFP.normalize_pay_config(None),
   {"default": dict(SFP.PAY_DEFAULTS), "by_carrier": {}, "by_market": {}, "all_markets": False})
flat = SFP.normalize_pay_config({"include_in_commission": True, "employee_pct_of_collected": 0.2})
eq("H2 a flat dict is still read as the org default", flat["default"]["employee_pct_of_collected"], 0.2)
only_market = SFP.normalize_pay_config({"by_market": {"n": SET_ON_10}})
eq("H3 a by_market-only envelope is NOT mistaken for the org default",
   (only_market["default"]["include_in_commission"], list(only_market["by_market"])), (False, ["N"]))
eq("H4 blank market keys are dropped",
   list(SFP.normalize_pay_config({"by_market": {"  ": SET_ON_10}})["by_market"]), [])
ok("H5 any_enabled arms on a market-only config", SFP.any_enabled(only_market))
ok("H6 any_enabled is False for the shipped defaults", not SFP.any_enabled(SFP.normalize_pay_config(None)))

# ══ §J  THE REGRESSION: the exec-MTD pay leg used to hard-code $0 ════════════════════════════════
print("\n§J  exec_mtd basis carries the set-up fee (regression for the hard-coded 0.0)")
from app.modules.commcalc.router import _override_plan_by_rep_with_mtd as OV

plan_by_rep = {"ALICE": {"amount": 111.0, "plan_name": "RULES PLAN", "setup_fee_comm": 9.0},
               "BOB": {"amount": 222.0, "plan_name": "MTD PLAN", "setup_fee_comm": 0.0}}
mtd_rows = [("MTD PLAN", [{"employee": "Bob", "commission": 852.78, "setup_fee_comm": 75.0},
                          {"employee": "Carol", "commission": 100.0, "setup_fee_comm": 0.0}])]
out = OV(dict(plan_by_rep), {"MTD PLAN"}, mtd_rows)
eq("J1 the DEFECT is gone: the fee component reaches the pay writer",
   out["BOB"]["setup_fee_comm"], 75.0)
eq("J2 the amount is the exec-MTD commission WITH the fee already in it",
   out["BOB"]["amount"], 852.78)
eq("J3 a rules-basis rep is untouched", out["ALICE"], plan_by_rep["ALICE"])
eq("J4 a rep with no fee still reports 0.0, not None", out["CAROL"]["setup_fee_comm"], 0.0)
out0 = OV(dict(plan_by_rep), {"MTD PLAN"},
          [("MTD PLAN", [{"employee": "Bob", "commission": 777.78}])])
eq("J5 a missing key degrades to 0.0 (never None, never a crash)",
   (out0["BOB"]["setup_fee_comm"], out0["BOB"]["amount"]), (0.0, 777.78))

# ══ §K  THE ARITHMETIC, END TO END ═══════════════════════════════════════════════════════════════
print("\n§K  collected x percentage")
s, src = SFP.resolve_for_scope(cfg(by_market={"NORTH": SET_ON_10}), None, "North")
eq("K1 $750 collected at 10% pays $75.00", SFP.employee_pay(750.0, s), (75.0, "paid"))
eq("K2 the dealer share is reported separately and pays no employee",
   SFP.dealer_share(750.0, s), (375.0, True))
eq("K3 rounding is to the cent", SFP.employee_pay(126.47, s), (12.65, "paid"))

print("\n" + "=" * 78)
print(f"{CHECKS[0]} checks, {len(FAILS)} failed" + ("" if not FAILS else f"  -> {FAILS}"))
print("=" * 78)
sys.exit(1 if FAILS else 0)
