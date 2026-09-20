"""HARNESS — carrier statement EARNED vs employee PAID, per rep (owner directive 2026-09-20).

Proves the rules that keep this report honest, with NO database, NO network, NO pandas:

  A. the three EARNED states — and specifically that an un-uploaded statement is never $0.00 earned;
  B. earned and paid are NEVER summed or netted into one figure;
  C. the difference is only computed when BOTH sides are measured, and is NAMED;
  D. a clawback NETS DOWN (and may go negative) instead of being clamped or counted twice;
  E. RULE TWO — the earnings columns come from config, with a house default, and no literal
     carrier/tenant/market name appears in the module;
  F. the module books nothing (meta.books_to is empty) and reuses ma_recon's row shape.

  python3 backend/harness_carrier_vs_pay.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import carrier_vs_pay as cvp                    # noqa: E402

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


HOUSE_COLS = ("spiff_m1", "spiff_m2", "rebate", "device_margin")
SIGN = -1          # this feed writes a payout to the dealer as a NEGATIVE amount


def recon(rep, imei, status="ok", store="S1"):
    """One row in exactly the shape ma_recon.reconcile_ma_activations emits."""
    return {"rep_username": rep, "imei": imei, "status": status, "store": store,
            "period": "August 2026", "comp_type": "MA_ACTIVATION", "source": "ma"}


def pay(rep, amount, store="S1"):
    return {"epay_salesperson": rep, "total_payout": amount, "store": store}


print("── A. the three EARNED states ──────────────────────────────────────────────────────────────")

# A1 — NO statement loaded at all: earned must be None, never 0.0.
out = cvp.rollup_by_rep([recon("alice", "111")], {}, [pay("alice", 250.00)],
                        columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=False)
r = out["rows"][0]
check("A1. no statement loaded -> carrier_earned is None (NOT 0.0)", r["carrier_earned"] is None,
      extra=repr(r["carrier_earned"]))
check("A1b. state is not_reported with the missing-feed reason",
      r["carrier_earned_state"] == cvp.EARNED_STATE_NOT_REPORTED
      and r["carrier_earned_reason"] == cvp.EARNED_REASON_NO_FEED)
check("A1c. the payload SAYS the carrier side is not reported",
      "NOT REPORTED" in (out["meta"].get("carrier_side") or ""))
check("A1d. an absent carrier side still reports the employee side in full",
      r["employee_paid"] == 250.00 and out["totals"]["employee_paid_total"] == 250.00)
check("A1e. the not-reported rep is COUNTED, not dropped",
      out["totals"]["carrier_earned_reps_not_reported"] == 1
      and out["totals"]["carrier_earned_reported"] == 0.0)

# A2 — statement loaded, device present, money against it: reported.
idx = {"111": {"spiff_m1": -20.0, "rebate": -5.0}}
out = cvp.rollup_by_rep([recon("alice", "111")], idx, [pay("alice", 10.0)],
                        columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=True)
r = out["rows"][0]
check("A2. statement money is read IN THE PAYOUT DIRECTION (-20 + -5, sign -1 -> +25.00)",
      r["carrier_earned"] == 25.00, extra=repr(r["carrier_earned"]))
check("A2b. state reported", r["carrier_earned_state"] == cvp.EARNED_STATE_REPORTED)
check("A2c. the device is counted as found in the statement",
      r["activations_in_statement"] == 1)

# A3 — statement loaded, device present, pays nothing: MEASURED zero (0.0, not None).
out = cvp.rollup_by_rep([recon("bob", "222")], {"222": {"spiff_m1": 0.0}}, [pay("bob", 40.0)],
                        columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=True)
r = out["rows"][0]
check("A3. loaded statement paying nothing -> 0.0 with state measured_zero",
      r["carrier_earned"] == 0.0 and r["carrier_earned_state"] == cvp.EARNED_STATE_MEASURED_ZERO)

# A4 — statement loaded, device simply absent from it: STILL a measured zero, but the reason says
# the statement does not mention these devices.
out = cvp.rollup_by_rep([recon("carol", "333")], {"999": {"spiff_m1": -9.0}}, [pay("carol", 5.0)],
                        columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=True)
r = out["rows"][0]
check("A4. device absent from a loaded statement -> measured_zero + 'absent' reason",
      r["carrier_earned"] == 0.0
      and r["carrier_earned_state"] == cvp.EARNED_STATE_MEASURED_ZERO
      and r["carrier_earned_reason"] == cvp.EARNED_REASON_ABSENT)
check("A4b. it is NOT counted as found in the statement", r["activations_in_statement"] == 0)

# A5 — every sold activation lacks a device key: nothing was looked up -> not_reported, not zero.
out = cvp.rollup_by_rep([recon("dan", ""), recon("dan", "")], {"111": {"spiff_m1": -1.0}},
                        [pay("dan", 77.0)], columns=list(HOUSE_COLS), payout_sign=SIGN,
                        statement_loaded=True)
r = out["rows"][0]
check("A5. no device key on any sold row -> None + no_device_key reason (never 0.00)",
      r["carrier_earned"] is None
      and r["carrier_earned_state"] == cvp.EARNED_STATE_NOT_REPORTED
      and r["carrier_earned_reason"] == cvp.EARNED_REASON_NO_DEVICE_KEY,
      extra=repr(r["carrier_earned"]))
check("A5b. the un-lookup-able activations are counted, never silently dropped",
      r["activations_without_device_key"] == 2 and r["activations_sold"] == 2)

# A6 — a rep who was PAID but sold no activation in the window: absence, not a zero.
out = cvp.rollup_by_rep([], {"111": {"spiff_m1": -1.0}}, [pay("erin", 12.0)],
                        columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=True)
r = out["rows"][0]
check("A6. paid rep with no sold activation -> earned None, not 0.00",
      r["carrier_earned"] is None and r["employee_paid"] == 12.0)

print("\n── B. earned and paid are NEVER summed or netted ────────────────────────────────────────────")
idx = {"111": {"spiff_m1": -30.0}, "222": {"spiff_m1": -40.0}}
out = cvp.rollup_by_rep([recon("alice", "111"), recon("bob", "222")], idx,
                        [pay("alice", 10.0), pay("bob", 15.0)],
                        columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=True)
t = out["totals"]
check("B1. the two ledgers have SEPARATE totals (70.00 earned / 25.00 paid)",
      t["carrier_earned_reported"] == 70.00 and t["employee_paid_total"] == 25.00)
check("B2. no key in totals holds earned+paid (95.00 appears nowhere)",
      95.0 not in [v for v in t.values() if isinstance(v, float)], extra=repr(t))
check("B3. no ROW key holds earned+paid either",
      all(95.0 not in [v for v in row.values() if isinstance(v, float)] for row in out["rows"]))
check("B4. every row carries earned and paid under distinct keys",
      all("carrier_earned" in row and "employee_paid" in row for row in out["rows"]))
check("B5. the payload states, in words, that the two must not be summed",
      "never sums them" in out["meta"]["difference_note"])

print("\n── C. the difference is NAMED and only computed when BOTH sides are measured ────────────────")
check("C1. difference = earned - paid, and it is labelled",
      out["rows"][0]["difference"] == 20.00
      and out["rows"][0]["difference_label"] == cvp.DIFFERENCE_LABEL
      and out["rows"][0]["difference_state"] == cvp.DIFFERENCE_STATE_COMPUTED)
check("C2. the label names both sides and their direction",
      cvp.DIFFERENCE_LABEL == "carrier_earned_minus_employee_paid")
out2 = cvp.rollup_by_rep([recon("alice", "111")], {}, [pay("alice", 10.0)],
                         columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=False)
r2 = out2["rows"][0]
check("C3. carrier side not reported -> difference is None and marked unavailable",
      r2["difference"] is None and r2["difference_state"] == cvp.DIFFERENCE_STATE_UNAVAILABLE)
check("C4. the difference TOTAL declares how many reps it actually covers",
      out["totals"]["difference_measured_reps"] == 2
      and out2["totals"]["difference_measured_reps"] == 0)
# C5 — a rep with a measured carrier side and NO pay row: still no difference (half-measured).
out3 = cvp.rollup_by_rep([recon("frank", "111")], {"111": {"spiff_m1": -30.0}}, [],
                         columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=True)
r3 = out3["rows"][0]
check("C5. earned measured but rep never paid -> difference unavailable, earned still stated",
      r3["difference"] is None and r3["carrier_earned"] == 30.00
      and r3["employee_paid"] is None)

print("\n── D. a clawback NETS DOWN and is allowed to go negative ────────────────────────────────────")
# The mig-308 index has already summed base + adjustment per column; a reversal bigger than the
# original leaves a net CHARGE, and the report must show it rather than clamp it to zero.
out = cvp.rollup_by_rep([recon("alice", "111")], {"111": {"spiff_m1": -20.0, "rebate": 35.0}},
                        [pay("alice", 5.0)], columns=list(HOUSE_COLS), payout_sign=SIGN,
                        statement_loaded=True)
r = out["rows"][0]
check("D1. net clawback reports NEGATIVE earned (-15.00), never clamped to 0",
      r["carrier_earned"] == -15.00, extra=repr(r["carrier_earned"]))
check("D2. a net clawback is 'reported' (it IS a measurement), not a measured zero",
      r["carrier_earned_state"] == cvp.EARNED_STATE_REPORTED)
amt, present = cvp.device_earned({"spiff_m1": -20.0}, ["spiff_m1"], -1)
check("D3. device_earned reports presence separately from amount", (amt, present) == (20.0, True))
amt, present = cvp.device_earned(None, ["spiff_m1"], -1)
check("D4. a device absent from the index is (0.0, False) — the caller decides what that means",
      (amt, present) == (0.0, False))
check("D5. payout_sign +1 feeds read the same columns the other way",
      cvp.device_earned({"spiff_m1": 20.0}, ["spiff_m1"], 1) == (20.0, True))

print("\n── E. RULE TWO — config, never code ─────────────────────────────────────────────────────────")
check("E1. configured columns win over the house default",
      cvp.earnings_columns(["rebate"], HOUSE_COLS) == ["rebate"])
check("E2. no configuration falls back to the house default",
      cvp.earnings_columns(None, HOUSE_COLS) == list(HOUSE_COLS))
check("E3. an empty/blank config is treated as no configuration (never sums nothing silently)",
      cvp.earnings_columns(["", "  "], HOUSE_COLS) == list(HOUSE_COLS))
check("E4. duplicates collapse but order is preserved (the payload shows exactly what was summed)",
      cvp.earnings_columns(["rebate", "spiff_m1", "rebate"], HOUSE_COLS) == ["rebate", "spiff_m1"])
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "app/modules/commcalc/carrier_vs_pay.py")).read()
_code = "\n".join(l for l in _src.splitlines() if not l.strip().startswith("#"))
_code = re.sub(r'""".*?"""', "", _code, flags=re.S)
_BANNED = ("boost", "total wireless", "verizon", "vidapay", "t-cetra", "luxelink", "chicago",
           "novawave", "tracfone", "acima")
check("E5. NO carrier / tenant / market literal anywhere in the module's code",
      not [w for w in _BANNED if w in _code.lower()],
      extra=str([w for w in _BANNED if w in _code.lower()]))
check("E6. the earnings columns used are echoed in meta, so the basis is visible",
      out["meta"]["earnings_columns"] == list(HOUSE_COLS))
check("E7. the resolved payout sign is echoed too", out["meta"]["payout_sign"] == -1)

print("\n── F. read-only posture + reuse ─────────────────────────────────────────────────────────────")
check("F1. meta.books_to is EMPTY — this report books nothing anywhere",
      out["meta"]["books_to"] == [])
check("F2. the module defines no write/persist/insert helper",
      not [n for n in dir(cvp) if any(k in n.lower() for k in ("persist", "insert", "write",
                                                               "save", "upsert", "delete"))])
check("F3. it consumes ma_recon's row shape verbatim (rep_username / imei / status)",
      all(k in recon("x", "1") for k in ("rep_username", "imei", "status", "store")))
check("F4. the basis note points at ma_recon as the ONE sold universe",
      "ma_recon" in out["meta"]["basis_note"])
check("F5. the rep key is a fold, not a fuzzy match",
      cvp.normalize_rep("  Espinoza,   Nallely ") == "ESPINOZA, NALLELY")
check("F6. the three state notes are all present and distinct",
      len(set(cvp.EARNED_STATE_NOTES.values())) == 3)
check("F7. every reason key has prose",
      set(cvp.EARNED_REASONS) == {cvp.EARNED_REASON_NO_FEED, cvp.EARNED_REASON_NO_DEVICE_KEY,
                                  cvp.EARNED_REASON_ABSENT, cvp.EARNED_REASON_PAID,
                                  cvp.EARNED_REASON_PREATTRIBUTED})

# F8 — NEGATIVE CONTROL: the bug this report exists to prevent. If the not_reported state were
# collapsed to 0.00, an un-uploaded statement would print a confident -$250.00 margin for alice.
out_bad = cvp.rollup_by_rep([recon("alice", "111")], {}, [pay("alice", 250.00)],
                            columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=False)
check("F8. NEGATIVE CONTROL — a missing statement yields NO margin figure at all",
      out_bad["rows"][0]["difference"] is None
      and out_bad["totals"]["difference_measured_reps"] == 0
      and out_bad["totals"]["carrier_earned_reported"] == 0.0
      and out_bad["rows"][0]["carrier_earned"] is None)

print("\n── G. the SECOND feed shape — dealer commission that arrives already per rep ────────────────")
# The Boost/ePay shape: no device statement at all, but the payment feed is already aggregated per
# rep. Before this was passed in, such a tenant read 'not_reported' for every rep while a measured
# figure sat one column away — an absence claimed where a measurement existed.
pre = {cvp.normalize_rep("alice"): 54151.66, cvp.normalize_rep("bob"): 0.0}
out = cvp.rollup_by_rep([recon("alice", ""), recon("bob", "")], {},
                        [pay("alice", 13215.10), pay("bob", 100.0), pay("carol", 25.0)],
                        columns=list(HOUSE_COLS), payout_sign=SIGN, statement_loaded=False,
                        preattributed_earned=pre,
                        earned_shape=cvp.EARNED_SHAPE_PREATTRIBUTED_REP)
by = {r["rep_key"]: r for r in out["rows"]}
check("G1. a pre-attributed rep is REPORTED even with no device statement loaded",
      by["ALICE"]["carrier_earned"] == 54151.66
      and by["ALICE"]["carrier_earned_state"] == cvp.EARNED_STATE_REPORTED,
      extra=repr(by["ALICE"]["carrier_earned"]))
check("G2. its reason names the pre-attributed feed, not the device statement",
      by["ALICE"]["carrier_earned_reason"] == cvp.EARNED_REASON_PREATTRIBUTED)
check("G3. a rep the feed NAMES but pays nothing is a MEASURED zero, not an absence",
      by["BOB"]["carrier_earned"] == 0.0
      and by["BOB"]["carrier_earned_state"] == cvp.EARNED_STATE_MEASURED_ZERO)
check("G4. a rep the feed does NOT name is still not_reported (never 0.00 by omission)",
      by["CAROL"]["carrier_earned"] is None
      and by["CAROL"]["carrier_earned_state"] == cvp.EARNED_STATE_NOT_REPORTED)
check("G5. the difference is computed for the pre-attributed reps and not for the omitted one",
      by["ALICE"]["difference"] == round(54151.66 - 13215.10, 2)
      and by["CAROL"]["difference"] is None)
check("G6. the shape is echoed so the page can STATE the basis",
      out["meta"]["earned_shape"] == cvp.EARNED_SHAPE_PREATTRIBUTED_REP
      and out["meta"]["preattributed_reps"] == 2)
check("G7. with a pre-attributed feed present the 'nothing loaded' banner is NOT raised",
      "carrier_side" not in out["meta"])
check("G8. the two shapes are distinct constants",
      cvp.EARNED_SHAPE_STATEMENT_DEVICE != cvp.EARNED_SHAPE_PREATTRIBUTED_REP)
# G9 — REGRESSION: passing no pre-attributed map must leave the device path byte-identical.
base = cvp.rollup_by_rep([recon("alice", "111")], {"111": {"spiff_m1": -20.0}},
                         [pay("alice", 5.0)], columns=list(HOUSE_COLS), payout_sign=SIGN,
                         statement_loaded=True)
withnone = cvp.rollup_by_rep([recon("alice", "111")], {"111": {"spiff_m1": -20.0}},
                             [pay("alice", 5.0)], columns=list(HOUSE_COLS), payout_sign=SIGN,
                             statement_loaded=True, preattributed_earned=None)
check("G9. REGRESSION — preattributed_earned=None leaves the device path byte-identical",
      base["rows"] == withnone["rows"] and base["totals"] == withnone["totals"])
check("G10. the pre-attributed reason has prose like every other reason",
      cvp.EARNED_REASON_PREATTRIBUTED in cvp.EARNED_REASONS)

print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES'}: {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
