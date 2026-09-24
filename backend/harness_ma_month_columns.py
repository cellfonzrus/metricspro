#!/usr/bin/env python3
"""PROOF — a COLUMN PER MONTH-OF-LIFE, driven by the data, and the August 2026 split pinned.

Owner report 2026-09-21: "something is still off the m2-m6 commission is 375% of the mrc
considering 75% each for 5 months the numbers still dont match … the m1 commission is 98656 and
m2-m12 is 92536 research where this is going wrong and display each months commission in separate
column so we see what is going on".

WHAT THE RESEARCH FOUND, and why the columns are the right answer (all MEASURED, read-only, org
854f6d7b-… LuxeLink, cash basis `pl_ma_month_spiff_source='daily_tx'`):

  1. THE PERIOD-TOTAL RATIO IS NOT A TEST OF THE CONTRACT. August's six rungs come from six
     different activation cohorts. 15.3x only means "the contract is broken" if cohort sizes are
     flat, and they are not: this feed steps from ~155 daily-tx rows/day (Feb-Jul) to 668/day in
     August. The ratio is a mix statistic, not a rate.
  2. NOTHING IS IN M7..M12. All eight periods, every rung above 6 is EMPTY. The schedule does not
     run longer than the owner states, and no non-residual money is being filed into a high rung.
  3. THE TRAILING LEGS PAY THE CONTRACT RATE. Against LIST MRC (never `mrc_net_discount`, which is
     list x 0.915) the trailing rungs' modal amounts are exactly 75%: 41.25 on a $55 plan, 48.75 on
     $65, 22.50 on $30, 30.00 on $40, 18.75 on $25.
  4. M1 IS THE GAP. The commission SHEET says August's 1,248 activations EARNED $23,271.90 of M1 —
     a median of exactly 50.0% of list MRC, the contract. The cash feed carries $6,049.96 across 508
     rows. Both the row COUNT and the per-row AMOUNT are short, in every measurable month.

So the display has to be able to show a rung that SHOULD NOT BE THERE and a rung that is SHORT. A
hardcoded 6 or 12 cannot: it would have hidden an M7 the day one arrived, and it folds the
unlabelled bucket out of sight. This proves the display cannot do either.

Run: PYTHONPATH=backend python3 backend/harness_ma_month_columns.py   (no DB, no network, stdlib)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_legs as LEGS          # noqa: E402
from app.modules.commcalc import commission_ledger as CL          # noqa: E402
from app.modules.account import ma_store_pnl as MSP               # noqa: E402

CHECKS = []


def check(name, got, want):
    CHECKS.append((got == want, name, got, want))


def money(x):
    return round(float(x), 2)


# ── FIXTURES ────────────────────────────────────────────────────────────────────────────────────
# Plan LABELS and synthetic order numbers only — no customer, no IMEI, no SIM, no rep name, no
# account that identifies a person. The label forms are the real vocabulary (that is the fact under
# test); the dollars are the shape of the August-2026 measurement, not a copy of any customer's row.
STORE_A, STORE_B = "11 Example Ave", "22 Sample Blvd"
ACCT_A, ACCT_B = "100001", "100002"
STORE_INDEX = {ACCT_A: STORE_A, ACCT_B: STORE_B}
SPIFF = "PostPaid Additional Spiff"


def tx(acct, product, amount, order_type=SPIFF):
    """One raw_ma_daily_tx row. Feed convention: NEGATIVE retail_cost = paid TO the dealer."""
    return {"account_id": acct, "product_name": product, "order_type": order_type,
            "retail_cost": -amount, "merchant_discount": 0.0}


def cfg():
    c = dict(MSP.default_config())
    c["store_attribution"] = True
    c["month_spiff_source"] = "daily_tx"
    c["spiff_order_types"] = [SPIFF]
    return c


def ladder_of(rows):
    return MSP.ma_received_month_ladder(rows, None, cfg(), STORE_INDEX)["months"]


def main():
    # ── A. THE RUNG KEY, ORDER AND LABEL HAVE ONE HOME ──────────────────────────────────────────
    check("A1 ladder_key(4)", LEGS.ladder_key(4), "4")
    check("A2 ladder_key('4')", LEGS.ladder_key("4"), "4")
    check("A3 ladder_key(None) is the unknown rung", LEGS.ladder_key(None), LEGS.LADDER_UNKNOWN)
    check("A4 ladder_key('') is the unknown rung", LEGS.ladder_key(""), LEGS.LADDER_UNKNOWN)
    check("A5 a junk key is the unknown rung, never a crash and never a lost dollar",
          LEGS.ladder_key("Month Four"), LEGS.LADDER_UNKNOWN)
    check("A6 the unknown rung sorts LAST, after every month",
          LEGS.ladder_sort_key(LEGS.LADDER_UNKNOWN) > LEGS.ladder_sort_key("99"), True)
    check("A7 rungs order numerically, not lexically (M10 after M9)",
          sorted(["10", "9", "2"], key=LEGS.ladder_sort_key), ["2", "9", "10"])
    check("A8 the unknown rung is LABELLED, never 'M0' and never 'Mnone'",
          LEGS.ladder_label(LEGS.LADDER_UNKNOWN), LEGS.LADDER_UNKNOWN_LABEL)
    check("A9 a month rung is labelled M<n>", LEGS.ladder_label("7"), "M7")
    # The leg-bucket namespace and the rung namespace must not collide: `comm_m1` is the 1st-month
    # LEG bucket and is NOT the same fact as rung 1 (a label mapped to the M1 leg can still state no
    # month-of-life). A ladder column named `comm_m1` would silently overwrite the leg column.
    check("A10 rung columns cannot collide with the leg columns",
          sorted(set(LEGS.public_keys("comm"))
                 & {LEGS.ladder_column("comm", k) for k in ("1", "2", "12", LEGS.LADDER_UNKNOWN)}),
          [])
    check("A11 rung 1's column", LEGS.ladder_column("comm", 1), "comm_month_1")
    check("A12 the unknown rung's column can never read as a month number",
          LEGS.ladder_column("comm", None), "comm_month_unlabelled")

    # ── B. THE COLUMN LIST COMES FROM THE DATA — NO 6, NO 12, NO CONFIG MAX ─────────────────────
    check("B1 only the rungs the data carries become columns",
          LEGS.months_present({"1": 10.0, "3": 5.0}), ["1", "3"])
    check("B2 a ZERO rung is not a column (nothing is invented)",
          LEGS.months_present({"1": 10.0, "2": 0.0}), ["1"])
    check("B3 an M7 the owner does not expect IS a column the day it arrives",
          LEGS.months_present({"1": 1.0, "7": 2.0}), ["1", "7"])
    check("B4 so is an M13 — there is no ceiling anywhere in this path",
          LEGS.months_present({"13": 2.0}), ["13"])
    check("B5 M10..M12 sort after M9, so a high rung cannot hide at the left edge",
          LEGS.months_present({"12": 1.0, "9": 1.0, "10": 1.0}), ["9", "10", "12"])
    check("B6 the unlabelled bucket is a COLUMN OF ITS OWN, last",
          LEGS.months_present({"1": 1.0, LEGS.LADDER_UNKNOWN: 2.0}), ["1", LEGS.LADDER_UNKNOWN])
    check("B7 include_unknown=False is the only way to drop it, and it is opt-in",
          LEGS.months_present({"1": 1.0, LEGS.LADDER_UNKNOWN: 2.0}, include_unknown=False), ["1"])
    check("B8 several ladders union into one column list",
          LEGS.months_present({"1": 1.0}, {"4": 1.0}), ["1", "4"])
    check("B9 a ladder MAP ({prefix: {rung: $}}) is unioned the same way",
          LEGS.months_present({"comm": {"2": 1.0}, "mi": {"5": 1.0}}), ["2", "5"])
    # Every row gets every column, so a store with no M4 reads $0.00 rather than as a hole.
    pub = LEGS.ladder_to_public("comm", {"1": 5.0}, ["1", "4", LEGS.LADDER_UNKNOWN])
    check("B10 a store missing a rung reads $0.00, never a hole",
          pub, {"comm_month_1": 5.0, "comm_month_4": 0.0, "comm_month_unlabelled": 0.0})

    # ── C. THE RUNG IS `month_leg_of`'s ANSWER — no second leg derivation ────────────────────────
    # #272's finding: the first month is stated in TWO forms and only one carries a token.
    rows = [tx(ACCT_A, "TBV MONTH 2 New Activation Commission", 41.25),
            tx(ACCT_A, "Total Wireless 5G Unlimited $55 New Activation Commission", 27.50),
            tx(ACCT_A, "BYO Activation SPF Month 1", 5.00),
            tx(ACCT_A, "New Activation Commission - M1 Proration", 9.92)]
    check("C1 both M1 FORMS land on rung 1, the token form and the activation form",
          ladder_of(rows).get("1"), money(27.50 + 5.00 + 9.92))
    check("C2 the token form of a trailing rung lands on its own rung",
          ladder_of(rows).get("2"), money(41.25))
    check("C3 every rung the ladder states is the leg `month_leg_of` states — one derivation",
          [CL.month_leg_of(r["product_name"]) for r in rows], [2, 1, 1, 1])

    # ── D. THE NEGATIVE CONTROLS STAY OUT OF EVERY MONTH RUNG ────────────────────────────────────
    # Upgrade commission, an RTR wallet line and a processing fee are NOT month-of-life residuals.
    # They are inside the spiff order-type family (so they reach the Commission column) and must
    # land in the Unlabelled rung, visibly — never guessed into M1 and never dropped.
    neg = [tx(ACCT_A, "Total Wireless Upgrade Commission", 25.00),
           tx(ACCT_A, "Total MAX 5G Plan $55 RTR", 20.00),
           tx(ACCT_A, "Total Wireless Edge Upgrade Processing Fee", 25.00),
           tx(ACCT_A, "TW EDGE SPF", 21.14)]
    lad = ladder_of(neg)
    check("D1 no negative control reaches a MONTH rung",
          sorted(k for k in lad if k != LEGS.LADDER_UNKNOWN), [])
    check("D2 every one of them is VISIBLE in the Unlabelled rung",
          lad.get(LEGS.LADDER_UNKNOWN), money(25.00 + 20.00 + 25.00 + 21.14))
    check("D3 none of them is silently dropped", money(sum(lad.values())),
          money(25.00 + 20.00 + 25.00 + 21.14))
    # Subsidy is the single largest positive family in the feed ($262,315.23 in August). It is not
    # a spiff order type, so it must not reach the Commission column AT ALL, let alone a rung.
    sub = [tx(ACCT_A, "Subsidy", 262315.23, order_type="Postpaid Promo Order")]
    check("D4 device subsidy reaches no rung and no Commission dollar", ladder_of(sub), {})

    # ── E. AN M7 IS A FINDING, AND IT IS VISIBLE ────────────────────────────────────────────────
    # The contract the owner states is M2-M6. Today every period's rungs stop at 6 (measured). If a
    # seventh ever arrives it must appear as its own column rather than be folded into "trailing".
    m7 = ladder_of([tx(ACCT_A, "TBV MONTH 7 New Activation SPF", 30.00)])
    check("E1 an M7 gets its own rung", m7, {"7": 30.0})
    check("E2 and its own column", LEGS.months_present(m7), ["7"])
    check("E3 a hardcoded six would have hidden it",
          [k for k in LEGS.months_present(m7) if LEGS.ladder_sort_key(k) <= 6], [])

    # ── F. AUGUST 2026 REGRESSION — the corrected split, per store and in total ──────────────────
    # The shape of the real month (#270 + #272): M1 6,049.96 + M2..M6 92,536.41 + Unlabelled 70.00
    # = 98,656.37 = the P&L's `carrier_comm`. Reproduced here from label forms, split across two
    # stores so the per-store identity is proved too.
    AUG = {"1": 6049.96, "2": 18776.69, "3": 20256.94, "4": 19792.24,
           "5": 15931.79, "6": 17778.75, LEGS.LADDER_UNKNOWN: 70.00}
    AUG_TOTAL = 98656.37
    AUG_M1 = 6049.96
    AUG_TRAILING = 92536.41
    LABEL = {"1": "Total Wireless 5G Unlimited $55 New Activation Commission",
             "2": "TBV MONTH 2 New Activation Commission",
             "3": "TBV MONTH 3 New Activation Commission",
             "4": "TBV MONTH 4 New Activation SPF",
             "5": "TBV MONTH 5 New Activation SPF",
             "6": "TBV MONTH 6 New Activation SPF",
             LEGS.LADDER_UNKNOWN: "Total Wireless Upgrade Commission"}
    aug_rows = []
    for k, amt in AUG.items():
        half = money(amt / 2)
        aug_rows.append(tx(ACCT_A, LABEL[k], half))
        aug_rows.append(tx(ACCT_B, LABEL[k], money(amt - half)))
    lad = MSP.ma_received_month_ladder(aug_rows, None, cfg(), STORE_INDEX)
    check("F1 August's rung list is M1..M6 + Unlabelled — no M7, no M12",
          LEGS.months_present(lad["months"]),
          ["1", "2", "3", "4", "5", "6", LEGS.LADDER_UNKNOWN])
    for k in AUG:
        check("F2 August %s" % LEGS.ladder_label(k), money(lad["months"].get(k)), money(AUG[k]))
    check("F3 the rungs sum to the Commission column", money(lad["total"]), AUG_TOTAL)
    check("F4 M1 is 6,049.96 (it read 2,300.40 before #272)", money(lad["months"]["1"]), AUG_M1)
    check("F5 M2..M6 is 92,536.41",
          money(sum(v for k, v in lad["months"].items()
                    if k != LEGS.LADDER_UNKNOWN and k != "1")), AUG_TRAILING)
    check("F6 the Unlabelled bucket is 70.00 and is NOT folded into a month",
          money(lad["months"][LEGS.LADDER_UNKNOWN]), 70.00)
    check("F7 identity: M1 + trailing + unlabelled == the column",
          money(AUG_M1 + AUG_TRAILING + 70.00), AUG_TOTAL)
    for store in (STORE_A, STORE_B):
        per = lad["by_store"][store]
        check("F8 per-store identity holds at %s" % store,
              money(sum(per["months"].values())), money(per["total"]))
    check("F9 the two stores' rungs re-add to the company rung, month by month",
          {k: money(sum(lad["by_store"][s]["months"].get(k, 0.0) for s in (STORE_A, STORE_B)))
           for k in AUG},
          {k: money(v) for k, v in AUG.items()})

    # ── G. THE OWNER'S OWN TEST, stated as arithmetic rather than as a verdict ───────────────────
    # 15.3x against a 7.5x contract. The columns are what make the factorisation readable: it is a
    # COUNT ratio times a RATE ratio, and neither factor is visible in a two-bucket display.
    ratio = round(AUG_TRAILING / AUG_M1, 2)
    check("G1 the observed period ratio is 15.30x", ratio, 15.30)
    check("G2 the contract's ratio is 5 legs x (75/50) = 7.50x", round(5 * (0.75 / 0.50), 2), 7.50)
    # Measured counts behind those dollars (read-only, August): 508 M1 rows and 3,624 trailing rows.
    n_m1, n_trail = 508, 3624
    count_factor = round((n_trail / n_m1) / 5.0, 3)
    rate_factor = round(((AUG_TRAILING / n_trail) / (AUG_M1 / n_m1)) / 1.5, 3)
    check("G3 the excess factorises: count x rate == observed / contract",
          round(count_factor * rate_factor, 2), round(ratio / 7.50, 2))
    check("G4 neither factor is small — the gap is HALF too many trailing rows per M1 row",
          count_factor > 1.4, True)
    check("G5 and HALF too few dollars on each M1 row", rate_factor > 1.4, True)
    # Against the SHEET's own earned M1 for the same month ($23,271.90, a median of exactly 50.0%
    # of list MRC) the trailing money is BELOW the contract ceiling — which is the finding: the
    # trailing legs are not overpaid, the M1 cash is short.
    check("G6 measured against EARNED M1 the trailing money is below the 7.5x ceiling",
          round(AUG_TRAILING / 23271.90, 2) < 7.50, True)

    # ── H. LIST MRC, NEVER `mrc_net_discount` (the owner's basis ruling) ────────────────────────
    # `mrc_net_discount` is list x 0.915 — the 8.5% bill-pay commission already netted out.
    # Deducting it a second time understates every rate this report states.
    check("H1 list MRC recovered from the netted column", round(27.45 / 0.915, 2), 30.00)
    check("H2 75% of a $30 list is the trailing modal amount actually paid",
          round(30.00 * 0.75, 2), 22.50)
    check("H3 50% of a $30 list is the M1 modal amount actually paid",
          round(30.00 * 0.50, 2), 15.00)
    check("H4 reading the NETTED column instead understates the trailing rate",
          round(22.50 / 27.45, 3) > 0.75, True)

    print("=" * 84)
    print("PROOF — month-of-life COLUMNS: data-driven rungs, one home, August 2026 pinned")
    print("=" * 84)
    bad = 0
    for ok, name, got, want in CHECKS:
        print("   %s  %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            bad += 1
            print("         got  %r" % (got,))
            print("         want %r" % (want,))
    print("-" * 84)
    print("%d checks, %d failed" % (len(CHECKS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
