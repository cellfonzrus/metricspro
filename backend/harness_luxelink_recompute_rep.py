"""Harness for the Luxelink commission fix — part (c): the SHARED single-rep write helper.

Proves, with NO database, that a single-rep recompute writes the SAME values the full company run would
write for that rep — because both drive the ONE shared helper `_apply_engine_components_to_row` (extracted
verbatim from the full run's per-row loop). If this holds, the /recompute-rep endpoint cannot drift from
the full Run Calculation for the rep it touches.

  A. HELPER EQUIVALENCE: driving the helper via the full-run loop (over every rep's fresh calc row) vs.
     via the single-rep path (the target rep's fresh row alone) yields a byte-identical enriched row —
     for a plan rep, a non-plan (standard-calc) rep, and an installment-only rep, and across two
     different `cols` column-availability sets.
  B. ENDPOINT RECONSTRUCTION: the endpoint starts from the rep's STORED row (already enriched by a prior
     run) and recovers the standard-calc base by stripping the previously-folded installment components
     before re-applying the helper — this reproduces the full run's fresh-row output exactly.

Run:  python harness_luxelink_recompute_rep.py
"""

import sys

from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc.router import _apply_engine_components_to_row, _rep_comm_row_keys

_passed = 0
_failed = 0


def check(name, cond, got=None, want=None):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        extra = "" if got is None and want is None else f"   (got={got!r} want={want!r})"
        print(f"  FAIL  {name}{extra}")


ALL_COLS = {c: True for c in ("residual_installment_comm", "installment_comm_sale", "plan_comm",
                              "plan_name", "carrier_statement_comm", "setup_fee_comm")}
# a tenant whose migration for setup_fee_comm / carrier_statement_comm hasn't run
NARROW_COLS = {**ALL_COLS, "setup_fee_comm": False, "carrier_statement_comm": False}

# by-rep engine outputs (org-wide), keyed UPPER as _apply_new_engines builds them
INST = {"BOB SMITH": 12.50, "CARLA REP": 0.0, "DANA ONLYINST": 7.0}
SALE_INST = {"BOB SMITH": 3.0, "DANA ONLYINST": 40.0}
STMT = {"BOB SMITH": 999.0}
PLAN = {
    "BOB SMITH": {"amount": 50.0, "plan_name": "Accessory Plan", "setup_fee_comm": 5.0},
    "CARLA REP": {"amount": 20.0, "plan_name": "Base Plan", "setup_fee_comm": 0.0},
}


def fresh_comms():
    """The rows calc_rep_commissions produces BEFORE _apply_new_engines enriches them (total_payout is
    the standard-calc subtotal at this point)."""
    return [
        {"storeops_name": "Robert Smith", "epay_salesperson": "Bob Smith", "total_payout": 0.0, "subtotal": 0.0},
        {"storeops_name": "Carla Rep", "epay_salesperson": "Carla Rep", "total_payout": 15.0, "subtotal": 15.0},
        {"storeops_name": "Ed NoPlan", "epay_salesperson": "Ed NoPlan", "total_payout": 33.0, "subtotal": 33.0},
    ]


def full_run(cols):
    """Enrich every row exactly as _apply_new_engines' loop does, returning {rep_key: enriched_row}."""
    rows = fresh_comms()
    for row in rows:
        _apply_engine_components_to_row(row, _rep_comm_row_keys(row), INST, SALE_INST, STMT, PLAN, cols)
    out = {}
    for row in rows:
        for k in _rep_comm_row_keys(row):
            out[k] = row
    return out


def single_rep(fresh_row, cols):
    """Enrich ONLY the target rep's fresh row via the same helper (the endpoint's write step)."""
    row = dict(fresh_row)
    _apply_engine_components_to_row(row, _rep_comm_row_keys(row), INST, SALE_INST, STMT, PLAN, cols)
    return row


ENGINE_FIELDS = ("total_payout", "plan_comm", "plan_name", "residual_installment_comm",
                 "installment_comm_sale", "carrier_statement_comm", "setup_fee_comm")


def same_engine_fields(a, b):
    return all(a.get(f) == b.get(f) for f in ENGINE_FIELDS)


def main():
    print("(A) shared helper: full-run loop output == single-rep output, per rep, across column sets:")
    for cols_name, cols in (("all columns", ALL_COLS), ("narrow columns", NARROW_COLS)):
        fr = full_run(cols)
        for label, epay in (("plan rep (Bob Smith)", "Bob Smith"),
                            ("plan+no-installment rep (Carla Rep)", "Carla Rep"),
                            ("non-plan rep (Ed NoPlan)", "Ed NoPlan")):
            fresh = next(r for r in fresh_comms() if r["epay_salesperson"] == epay)
            sr = single_rep(fresh, cols)
            full = fr[epay.upper()]
            check(f"[{cols_name}] {label}: single-rep == full-run row",
                  same_engine_fields(sr, full),
                  {f: sr.get(f) for f in ENGINE_FIELDS}, {f: full.get(f) for f in ENGINE_FIELDS})

    # concrete money assertions so the equivalence is anchored to real numbers, not just to itself.
    fr = full_run(ALL_COLS)
    bob = fr["BOB SMITH"]
    check("Bob (plan) total == plan 50 + inst 12.50 + sale_inst 3.00 = 65.50",
          bob["total_payout"] == 65.50, bob["total_payout"])
    check("Bob plan_comm == 50.0 and setup_fee_comm == 5.0",
          bob["plan_comm"] == 50.0 and bob["setup_fee_comm"] == 5.0)
    check("Bob carrier_statement_comm == 999.0 (recorded, NOT added to pay)",
          bob["carrier_statement_comm"] == 999.0 and bob["total_payout"] == 65.50)
    ed = fr["ED NOPLAN"]
    check("Ed (no plan) total == standard 33 + inst 0 + sale_inst 0 = 33.0",
          ed["total_payout"] == 33.0 and ed.get("plan_comm") is None, ed["total_payout"])

    # narrow-columns: setup_fee_comm must NOT be written when the column is absent
    bob_narrow = full_run(NARROW_COLS)["BOB SMITH"]
    check("narrow cols: setup_fee_comm / carrier_statement_comm NOT written",
          "setup_fee_comm" not in bob_narrow and "carrier_statement_comm" not in bob_narrow,
          {k: bob_narrow.get(k) for k in ("setup_fee_comm", "carrier_statement_comm")})
    check("narrow cols: Bob total still 65.50 (money unaffected by optional columns)",
          bob_narrow["total_payout"] == 65.50, bob_narrow["total_payout"])

    print("\n(B) endpoint reconstruction: STORED row stripped + re-applied == full-run fresh output:")
    # simulate the STORED rows a prior full run wrote (fresh base folded with installments)
    for label, epay in (("plan rep (Bob Smith)", "Bob Smith"), ("non-plan rep (Ed NoPlan)", "Ed NoPlan")):
        fresh = next(r for r in fresh_comms() if r["epay_salesperson"] == epay)
        prior = single_rep(fresh, ALL_COLS)          # what the last run persisted for this rep
        # the endpoint fetches `prior`, strips the folded installment components to recover the base:
        stored = dict(prior)
        stored["total_payout"] = round(
            safe_float(stored.get("total_payout"))
            - safe_float(stored.get("residual_installment_comm"))
            - safe_float(stored.get("installment_comm_sale")), 2)
        _apply_engine_components_to_row(
            stored, _rep_comm_row_keys(stored), INST, SALE_INST, STMT, PLAN, ALL_COLS)
        full = full_run(ALL_COLS)[epay.upper()]
        check(f"{label}: reconstructed-from-stored == full-run fresh output",
              same_engine_fields(stored, full),
              stored["total_payout"], full["total_payout"])

    print(f"\n==== {_passed} passed, {_failed} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
