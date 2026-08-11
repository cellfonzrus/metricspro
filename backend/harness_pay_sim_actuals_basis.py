"""Harness — "What would I make?" starts from the rep's REAL numbers, and accessories can be
expressed either per-unit or per-month.

Owner 2026-08-11, two asks against /commcalc/pay-simulator:
  (a) "should have the current numbers not just placeholder 10 each"
  (b) "adding the rate of accessories or accessories per month as a drop down option … the selling
       price and the number of accessories, or the acc per month x % give the what if on acc"

Before: seedInputs() hard-coded units = flat ? 1 : 10 and amount = mrc ? 50 : 25 — figures nobody
chose, rendered as if they were a projection. And a percent lever could ONLY be entered per-unit.
"""
import sys, os, types

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


import app.modules.commcalc.pay_simulator as P  # noqa: E402

ORG, REP, STORE = "854f6d7b", "Yasir", "957 Pennsylvania Avenue"
PERIOD = "August 2026"

ACC_RULE = {"id": "r-acc", "label": "Accessories", "payout_kind": "pct_price_over_cost",
            "match_field": "accessory", "match_value": None, "pct": 0.10, "tiered": True}
ACT_RULE = {"id": "r-act", "label": "New activation", "payout_kind": "flat_per_unit",
            "match_field": "activation_bucket", "match_value": "premium", "amount": 25.0}
PLAN = {"id": "plan-1", "name": "Luxelink Plan", "rules": [ACC_RULE, ACT_RULE]}


def patch_env():
    P._accessory_line_hints = lambda c, o: {"department": "ACCESSORIES", "category": "Accessory"}
    P._bucket_contract_types = lambda c, o: {"premium": "New - Premium"}
    P._period_midpoint = lambda p: "2026-08-15"


patch_env()


def build(inputs):
    return P.build_lines(None, ORG, PLAN, REP, STORE, PERIOD, inputs)


print("\n§1 · ITEM BASIS (default) — today's behaviour, byte-for-byte")
lines, _mrc, applied, warns = build({"rule:r-acc": {"units": 4, "amount": 25}})
acc = [l for l in lines if l["product_desc"] == "Accessories"]
ok(len(acc) == 4, f"4 accessories -> 4 lines (got {len(acc)})")
ok(all(l["ext_price"] == 25 for l in acc), "each line carries the per-item price of $25")
ok(sum(l["ext_price"] for l in acc) == 100, "dollar base = 4 x 25 = $100")
ok(not warns, "no warnings on the plain path")
ok(applied[0]["basis"] == "item", "applied[] records the basis used")

print("\n§2 · MONTH BASIS — 'acc per month x %' (the owner's second phrasing)")
lines, _m, applied, warns = build({"rule:r-acc": {"units": 4, "amount": 100, "basis": "month"}})
acc = [l for l in lines if l["product_desc"] == "Accessories"]
ok(sum(l["ext_price"] for l in acc) == 100,
   f"the MONTH TOTAL is preserved: ${sum(l['ext_price'] for l in acc)} on the same 10% rule")
ok(len(acc) == 4,
   "…and the UNIT COUNT is preserved too (4 lines) — so a tiered rule still qualifies correctly")
ok(all(l["ext_price"] == 25 for l in acc), "$100 spread across 4 accessories = $25 each")
ok(not warns, "a month total WITH a count needs no warning")

print("\n§3 · THE TIER TRAP — collapsing to one line would silently change qualification")
one, _m2, _a2, _w2 = build({"rule:r-acc": {"units": 1, "amount": 100, "basis": "month"}})
ok(len([l for l in one if l["product_desc"] == "Accessories"]) == 1,
   "count=1 really does produce 1 line, so §2's 4 lines are the split working, not a coincidence")

print("\n§4 · MONTH BASIS WITH NO COUNT — still projects, and SAYS what it cannot model")
lines, _m, _a, warns = build({"rule:r-acc": {"units": 0, "amount": 250, "basis": "month"}})
acc = [l for l in lines if l["product_desc"] == "Accessories"]
ok(len(acc) == 1 and acc[0]["ext_price"] == 250,
   "a blank count still models the full $250 (it is NOT dropped)")
ok(any(w["code"] == "month_basis_no_count" for w in warns),
   "…and warns that unit-based tier qualification is not being modelled")

print("\n§5 · ITEM BASIS WITH NO UNITS IS STILL SKIPPED (unchanged)")
lines, _m, _a, _w = build({"rule:r-acc": {"units": 0, "amount": 25}})
ok(not lines, "units=0 on the item basis contributes nothing, exactly as before")

print("\n§6 · A FLAT LEVER IGNORES BASIS ENTIRELY")
lines, _m, applied, _w = build({"rule:r-act": {"units": 3, "amount": 0, "basis": "month"}})
act = [l for l in lines if l["product_desc"] == "New activation"]
ok(len(act) == 3, "3 flat units -> 3 lines regardless of basis")
ok(applied[0]["basis"] is None, "basis is reported as None for a lever that takes no amount")

print("\n§7 · SEEDING FROM ACTUALS — the engine's own answer, not a placeholder")
fake_pv = {"by_rep": [{"rep": REP, "rules": [
    {"rule_id": "r-acc", "qualifying_units": 7, "payout": 21.0,
     "lines": [{"ext_price": 30, "gp": 30}, {"ext_price": 20, "gp": 20}]},
    {"rule_id": "r-act", "qualifying_units": 12, "payout": 300.0, "lines": []},
]}]}
ce = types.SimpleNamespace(preview=lambda *a, **k: fake_pv,
                           _canon_person=lambda s: str(s or "").strip().lower())
# `from app.modules.commcalc import commission_engine` resolves the ATTRIBUTE on the package, so
# patching sys.modules alone is not enough — set both.
import app.modules.commcalc as _pkg
def _use(mod):
    sys.modules["app.modules.commcalc.commission_engine"] = mod
    _pkg.commission_engine = mod
_use(ce)
cur = P.current_actuals(None, ORG, PERIOD, PLAN, REP)
ok(cur["rule:r-acc"]["units"] == 7, f"units come from the engine (7, got {cur['rule:r-acc']['units']})")
ok(cur["rule:r-acc"]["amount"] == 25.0,
   f"amount is the AVERAGE per line, not the total (30+20)/2 = 25 (got {cur['rule:r-acc']['amount']})")
ok(cur["rule:r-acc"]["month_total"] == 50.0, "month_total is the sum, for the 'month' basis")
ok(cur["rule:r-acc"]["from_actuals"] is True, "flagged as real, so the UI can say so")
ok(cur["rule:r-act"]["units"] == 12 and cur["rule:r-act"]["amount"] == 0.0,
   "a lever with no detail lines still seeds its unit count")
ok(all(v["units"] != 10 for v in cur.values()), "nothing seeds the old placeholder 10")

print("\n§8 · NO HISTORY ⇒ ZERO, NEVER AN INVENTED NUMBER")
_use(types.SimpleNamespace(preview=lambda *a, **k: {"by_rep": []}, _canon_person=lambda s: s))
ok(P.current_actuals(None, ORG, PERIOD, PLAN, REP) == {},
   "a rep the engine returns nothing for seeds nothing (the UI then shows 0, not 10)")

print("\n§9 · NEVER RAISES — a broken engine must not break the page")
_use(types.SimpleNamespace(
    preview=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")), _canon_person=lambda s: s))
ok(P.current_actuals(None, ORG, PERIOD, PLAN, REP) == {}, "an engine failure degrades to no seed")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  ✗ " + f)
sys.exit(1 if FAIL else 0)
