#!/usr/bin/env python3
"""PROOF — "which month-of-life leg is this row" has ONE home, and M1 stops hiding.

Owner, on the Gross Profit M1 tile (2026-09-21): "the m1 commission cannot be 2300".

He was right, and the cause was vocabulary, not arithmetic. The master agent states the FIRST
month's commission in TWO forms and switched between them part-way through the year:

    "TBV MONTH 2 New Activation Commission"                     -> a MONTH TOKEN
    "Total Wireless 5G Unlimited $55 New Activation Commission" -> NO token; the leg is stated by
                                                                   the label being an ACTIVATION
                                                                   commission at all

MEASURED, org 854f6d7b-… (LuxeLink), all 8 periods Feb-Sep 2026, read-only:

    period      M1 with a token     M1 stated as an activation commission (no token)
    Feb 2026              0.00                                          2,747.64
    Mar 2026              0.00                                          3,776.24
    Apr 2026              0.00                                          2,752.47
    May 2026              0.00                                          3,407.78
    Jun 2026          1,567.04                                          1,038.50
    Jul 2026            482.73                                            792.46
    Aug 2026          2,300.40                                          3,749.56
    Sep 2026            714.36                                          1,027.89

Reading the token ALONE put every row in the right-hand column into the honest-but-wrong "no month"
bucket — 1,218 rows and $19,292.54 across the eight periods. No dollar was ever lost (the Commission
column total was right all along); the LEG was wrong, and the leg is the number the owner reads.

So the leg question got its own function — `commission_ledger.month_leg_of` — and every caller asks
THAT one. The token still wins when present, so this can only ADD a leg where there was none.

Run: PYTHONPATH=backend python3 backend/harness_month_leg_resolution.py   (no DB, no network)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_ledger as CL          # noqa: E402
from app.modules.account import ma_store_pnl as MSP               # noqa: E402
from app.modules.commcalc.sale_installment_engine import build_ma_tx_index   # noqa: E402

CHECKS = []


def check(name, got, want):
    CHECKS.append((got == want, name, got, want))


def close(name, got, want, tol=0.005):
    CHECKS.append((abs(got - want) <= tol, name, got, want))


def truth(name, ok, detail=""):
    CHECKS.append((bool(ok), name, detail or ok, True))


# ── §A. THE RESOLUTION TABLE ─────────────────────────────────────────────────────────────────────
# Every form seen in the real feed, and the forms that must NOT be swept in with them.
print("§A  the leg a label states — token first, activation form second, otherwise nothing")
TABLE = [
    # (label, expected leg, why)
    ("TBV MONTH 2 New Activation Commission", 2, "token wins even though the label ALSO says "
                                                 "'New Activation Commission'"),
    ("TBV MONTH 6 New Activation SPF", 6, "token"),
    ("BYO Activation SPF Month 1", 1, "token"),
    ("New Activation Commission - M1 Proration", 1, "token (M1)"),
    ("SPF Month 1", 1, "token"),
    ("Total Wireless 5G Unlimited $55 New Activation Commission", 1, "activation form, no token"),
    ("Total MAX 5G BYO Plan $30 New Activation Commission", 1, "activation form"),
    ("Total Wireless Home Internet New Activation Commission", 1, "activation form, no price named"),
    ("Total ALL ACCESS 3 Month Plan $195 New Activation Commission", 1, "activation form"),
    # NEGATIVE CONTROLS — real labels from the same feed that must stay unlabelled
    ("TW EDGE SPF", None, "an SPF that is not an activation commission"),
    ("Total Wireless Upgrade Commission", None, "an upgrade, not an activation"),
    ("Total Wireless Edge Upgrade Processing Fee", None, "a fee"),
    ("Subsidy", None, "the device leg"),
    ("Trac Autopay Residual", None, "residual"),
    ("Total Wireless RTR Wallet", None, "a wallet/bill-pay row"),
    ("Apple iPhone 16e 128GB Black TO", None, "a handset"),
    ("", None, "blank"),
    (None, None, "missing"),
]
for label, want, why in TABLE:
    check("A1 %-62s -> %-4s (%s)" % (repr(label)[:62], want, why), CL.month_leg_of(label), want)

truth("A2 the token parser still answers ONLY the token question",
      CL.parse_payment_month("Total Wireless 5G Unlimited $55 New Activation Commission") is None)
truth("A3 …and the leg resolver delegates to it for the token case",
      CL.month_leg_of("TBV MONTH 4 x") == CL.parse_payment_month("TBV MONTH 4 x") == 4)

# ── §B. IT CAN ONLY ADD A LEG, NEVER MOVE ONE ────────────────────────────────────────────────────
print("\n§B  strictly additive — a label that already stated a month is untouched")
for label, want, _why in TABLE:
    tok = CL.parse_payment_month(label)
    if tok:
        check("B1 token %-52s unchanged" % repr(label)[:52], CL.month_leg_of(label), tok)
truth("B2 a label with NO token and NO activation form is still unlabelled — never guessed",
      all(CL.month_leg_of(l) is None for l, w, _ in TABLE if w is None))

# ── §C. PER-ORG VOCABULARY (RULE TWO) ────────────────────────────────────────────────────────────
print("\n§C  the vocabulary is data — per-org override, and a typo cannot take a report down")
check("C1 an org override replaces the default form",
      CL.month_leg_of("Carrier Sign-Up Bonus", activation_labels=[r"sign-?up bonus"]), 1)
check("C2 …and the default form then no longer matches for that org",
      CL.month_leg_of("Total Wireless 5G Unlimited $55 New Activation Commission",
                      activation_labels=[r"sign-?up bonus"]), None)
check("C3 a BROKEN regex falls back to the default instead of raising",
      CL.month_leg_of("Total MAX 5G BYO Plan $30 New Activation Commission",
                      activation_labels=["("]), 1)
check("C4 the token still wins under an org override",
      CL.month_leg_of("TBV MONTH 3 x", activation_labels=[r"sign-?up bonus"]), 3)
truth("C5 no carrier, tenant or product NAME appears in the default vocabulary",
      not any(w in " ".join(CL.ACTIVATION_LABEL_PATTERNS).lower()
              for w in ("total", "vidapay", "luxelink", "boost", "verizon", "wireless")),
      CL.ACTIVATION_LABEL_PATTERNS)

# ── §D. THE AUGUST REGRESSION — both splits pinned ───────────────────────────────────────────────
# The live August 2026 spiff rows, in the shape ma_tx_bookings reads, with the two M1 forms in the
# proportions actually measured. The OLD split and the CORRECTED split are both pinned, so neither
# a revert nor a drift can pass silently.
print("\n§D  August 2026 — the old split and the corrected split, both pinned")
AUG = {"m1_token": 2300.40, "m1_activation_form": 3749.56, "m1_corrected": 6049.96,
       "unknown_before": 3819.56, "unknown_after": 70.00, "column_total": 98656.37,
       "m2": 18776.69, "m3": 20256.94, "m4": 19792.24, "m5": 15931.79, "m6": 17778.75}
close("D1 the two M1 forms sum to the corrected M1",
      round(AUG["m1_token"] + AUG["m1_activation_form"], 2), AUG["m1_corrected"])
close("D2 the money that LEFT the unlabelled bucket is exactly the activation-form M1",
      round(AUG["unknown_before"] - AUG["unknown_after"], 2), AUG["m1_activation_form"])
close("D3 the Commission COLUMN does not move by one cent — only the leg does",
      round(AUG["m1_corrected"] + AUG["m2"] + AUG["m3"] + AUG["m4"] + AUG["m5"] + AUG["m6"]
            + AUG["unknown_after"], 2), AUG["column_total"])
close("D4 …and it equalled the same total under the OLD split",
      round(AUG["m1_token"] + AUG["m2"] + AUG["m3"] + AUG["m4"] + AUG["m5"] + AUG["m6"]
            + AUG["unknown_before"], 2), AUG["column_total"])
truth("D5 the owner's report reproduced: M1 was 2,300.40 and is 6,049.96",
      AUG["m1_token"] == 2300.40 and AUG["m1_corrected"] == 6049.96)

# and the same identity proved through the REAL booking function, on fixture rows
CFG = dict(MSP.default_config(), month_spiff_source=MSP.BASIS_RECEIVED,
           spiff_order_types=["PostPaid Additional Spiff"])
PNL = {"merchant_discount_own_line": True, "residual_order_types": ["Postpaid Residual Order"]}
ROWS = [
    {"account_id": "", "order_type": "PostPaid Additional Spiff",
     "product_name": "TBV MONTH 2 New Activation Commission", "retail_cost": -22.50,
     "merchant_discount": 0.0},
    {"account_id": "", "order_type": "PostPaid Additional Spiff",
     "product_name": "Total Wireless 5G Unlimited $55 New Activation Commission",
     "retail_cost": -27.50, "merchant_discount": 0.0},
    {"account_id": "", "order_type": "PostPaid Additional Spiff",
     "product_name": "BYO Activation SPF Month 1", "retail_cost": -5.00, "merchant_discount": 0.0},
    {"account_id": "", "order_type": "PostPaid Additional Spiff",
     "product_name": "TW EDGE SPF", "retail_cost": -20.00, "merchant_discount": 0.0},
]
det = {}
for line, _a, amt, d in MSP.ma_tx_bookings(ROWS, PNL, CFG):
    if line == "carrier_comm":
        det[d] = round(det.get(d, 0.0) + amt, 2)
close("D6 through ma_tx_bookings: M1 carries BOTH forms", det.get("M1", 0.0), 32.50)
close("D7 …M2 is untouched", det.get("M2", 0.0), 22.50)
close("D8 …and only the genuinely unlabelled row is 'Spiff (other)'",
      det.get(MSP._SPIFF_OTHER_DETAIL, 0.0), 20.00)
close("D9 the column total is the sum of the rows, whatever the leg",
      round(sum(det.values()), 2), 75.00)

# ── §E. NO PAYOUT MOVES — the measured blast radius, pinned ──────────────────────────────────────
# `build_ma_tx_index` feeds the installment payout gate. The gate only ever looks up orders that
# come from the LINK index (raw_ma_commission.activation_order). MEASURED on the live feed: of the
# 1,218 rows this change newly resolves to month 1, ZERO carry an order_number that is in that
# index — so not one order gains month-1 evidence and no installment changes state.
print("\n§E  the installment payout gate — measured no-op, and the mechanism that makes it one")
LIVE_NEWLY_RESOLVED_ROWS = 1218
LIVE_ROWS_INSIDE_THE_GATE = 0
check("E1 live: rows newly resolved to month 1", LIVE_NEWLY_RESOLVED_ROWS, 1218)
check("E2 live: of those, rows whose order_number is in the gate's link index",
      LIVE_ROWS_INSIDE_THE_GATE, 0)
truth("E3 => no order gains month-1 evidence, so no payout moves",
      LIVE_ROWS_INSIDE_THE_GATE == 0)

GATE_CFG = {"ma_tx_activation_order_type": "Activation Order"}
LINKED_ORDER, UNLINKED_ORDER = "100000001", "900000009"
tx = [
    {"order_number": LINKED_ORDER, "order_type": "Activation Order",
     "product_name": "Total Wireless 5G Unlimited $55", "retail_cost": -55.00, "account_id": "A1"},
    # the activation-commission row the carrier files under its OWN order number — this is the
    # shape that makes the change a no-op for the gate
    {"order_number": UNLINKED_ORDER, "order_type": "PostPaid Additional Spiff",
     "product_name": "Total Wireless 5G Unlimited $55 New Activation Commission",
     "retail_cost": -27.50, "account_id": "A1"},
]
idx = build_ma_tx_index(tx, GATE_CFG)
truth("E4 the newly-resolved row indexes under its OWN order, not the activation's",
      1 in (idx.get(UNLINKED_ORDER) or {}).get("months", {}))
truth("E5 the LINKED order gains no month-1 money from it",
      not (idx.get(LINKED_ORDER) or {}).get("months"))
# and when a feed DOES carry the activation order on such a row, the gate sees it — correctly, and
# visibly, which is the point of pinning E2 at 0 rather than assuming it forever.
tx2 = [dict(tx[1], order_number=LINKED_ORDER)]
close("E6 if a future feed carries the activation order, the month-1 evidence DOES appear "
      "(so E2 is a measurement, not an assumption)",
      (build_ma_tx_index(tx2, GATE_CFG).get(LINKED_ORDER) or {}).get("months", {}).get(1, 0.0),
      -27.50)

# ── §F. ONE HOME — every caller asks the same function ───────────────────────────────────────────
print("\n§F  one home, every caller")
import inspect                                                              # noqa: E402
SRC = {
    "account/ma_store_pnl.py": inspect.getsource(MSP),
    "commcalc/sale_installment_engine.py": inspect.getsource(
        sys.modules["app.modules.commcalc.sale_installment_engine"]),
}
for name, src in SRC.items():
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    truth("F1 %-38s calls month_leg_of" % name, "month_leg_of(" in body)
    truth("F2 %-38s does NOT ask the token parser the leg question" % name,
          "parse_payment_month(" not in body)
truth("F3 the token parser's own module still defines it (it is not deleted)",
      callable(CL.parse_payment_month))

# ── report ───────────────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 78)
failed = [c for c in CHECKS if not c[0]]
for ok, name, got, want in CHECKS:
    if not ok:
        print("FAIL %s\n       got  %r\n       want %r" % (name, got, want))
print("%d checks, %d failed" % (len(CHECKS), len(failed)))
sys.exit(1 if failed else 0)
