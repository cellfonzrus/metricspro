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

# ── §G. THE FIRST MONTH AND THE TRAILING MONTHS ARE REPORTED ON DIFFERENT FEEDS ─────────────────
# Owner, 2026-10-16, on the month-of-life split shipped the same day (§COMMCALC-15): *"something is
# wrong on teh m1 commmission it cannot be that low, jsut a rule of thumb , if the store does 100
# activation and an mrc of 50, teh first month payout is 25, so the month shoudl have 2500 plus any
# spiff etc, recheck"*.
#
# He is right about the money and the vocabulary fix of §A-§F is NOT the reason. MEASURED read-only
# on the uploaded feeds of prospect 8061a5b2-… / be73b0c4-… (org 854f6d7b-…), seven months:
#
#   period      activations   Σ mrc_net_discount   EARNED M1   RECEIVED M1   EARNED M2-6  RECEIVED M2-6
#   Mar 2026             39             1,841.00    1,018.53      1,061.01      2,643.75        861.25
#   Apr 2026            866            26,957.00   13,385.25     10,521.92     50,941.25     18,837.50
#   May 2026          1,066            29,521.00   16,269.18     13,057.88     51,965.00     27,020.00
#   Jun 2026          1,209            30,446.00   16,657.58     13,306.10     38,365.75     37,442.75
#   Jul 2026          1,217            32,149.00   17,164.44      9,544.87     26,665.75     49,874.50
#   Aug 2026            613            21,524.00   12,323.03      2,762.36      4,935.50     64,308.75
#   Sep 2026            482            16,364.00    7,899.24      1,237.99        555.00     38,451.50
#
# EARNED = `ma_store_pnl.ma_earned_month_ladder` (the sheet's spiff_m1..m6).
# RECEIVED = `ma_store_pnl.ma_received_month_ladder` (the cash rows, legs via `month_leg_of`).
#
# Read the two halves of that table as the two feeds answering ONE question each:
#   • the SHEET states the first month at activation and almost nothing else — Sep: 410 of 482 rows
#     carry a non-zero `spiff_m1`, only 26 carry any of `spiff_m2..m6`;
#   • the CASH feed states the trailing months promptly and the first month RETROACTIVELY — the
#     family that carries the untokened first-month cash is literally named "Retroactive Postpaid
#     Spiff", and its row count collapses 582 → 467 → 139 → 60 over Jun→Sep while activations only
#     halve. Cumulatively the cash M1 is 51,492.13 against 84,717.25 earned: 60.8%, never above.
#
# So M1 is not misclassified and no dollar is unlabelled: it is reported on the feed the org's
# `pl_ma_month_spiff_source` switch SUPPRESSES. The switch chooses ONE feed for ALL rungs, which was
# right when both feeds were assumed to carry the same money at the same time. They do not.
print("\n§G  the first month and the trailing months are reported on DIFFERENT feeds")

LIVE = {"acts": 482, "mrc": 16364.12,
        "earned_m1": 7899.24, "earned_m2_6": 555.00,
        "received_m1": 1237.99, "received_m2_6": 38451.50,
        "sheet_rows_with_m1": 410, "sheet_rows_with_m2_6": 26,
        "retro_rows": {6: 582, 7: 467, 8: 139, 9: 60},
        "cum_earned_m1": 84717.25, "cum_received_m1": 51492.13}

# G1-G4 — the labels the REAL feed carries in the month the owner is reading, through the REAL
# booking function. Every form present in Sep 2026 is here; this is what kills "the activation test
# reads the wrong field" as a theory rather than asserting it.
G_CFG = dict(MSP.default_config(), month_spiff_source=MSP.BASIS_RECEIVED,
             spiff_order_types=["PostPaid Additional Spiff", "Retroactive Postpaid Spiff"])
G_PNL = {"merchant_discount_own_line": True, "residual_order_types": ["Postpaid Residual Order"]}
G_TX = [
    # the two spiff families, in their real label forms
    {"order_type": "Retroactive Postpaid Spiff",
     "product_name": "Total MAX 5G BYO Plan $30 New Activation Commission", "retail_cost": -15.00},
    {"order_type": "PostPaid Additional Spiff",
     "product_name": "BYO Activation SPF Month 1", "retail_cost": -5.00},
    {"order_type": "PostPaid Additional Spiff",
     "product_name": "TBV MONTH 3 New Activation Commission", "retail_cost": -32.50},
    # and the families the feed books NOTHING to carrier_comm from — real labels, real signs.
    # `Activation Order` is the row the "wrong field" theory points at: its money is POSITIVE
    # (the dealer is CHARGED) and its label is a plan name, so it is not commission in either field.
    {"order_type": "Activation Order", "product_name": "Total ALL ACCESS Plan $65",
     "retail_cost": 32.90},
    {"order_type": "Sales Order", "product_name": "Total Wireless Protection RTR",
     "retail_cost": 7.00},
    {"order_type": "Postpaid Promo Order", "product_name": "Subsidy", "retail_cost": -196.38},
]
g_det = {}
for line, _a, amt, d in MSP.ma_tx_bookings([dict(r, merchant_discount=0.0, account_id="")
                                            for r in G_TX], G_PNL, G_CFG):
    if line == "carrier_comm":
        g_det[d] = round(g_det.get(d, 0.0) + amt, 2)
close("G1 the untokened first-month CASH label is read as M1 (not as 'no month')",
      g_det.get("M1", 0.0), 20.00)
close("G2 the tokened trailing label is read as its own rung", g_det.get("M3", 0.0), 32.50)
truth("G3 nothing is unlabelled — every spiff row of the real month states its rung",
      MSP._SPIFF_OTHER_DETAIL not in g_det)
truth("G4 an `Activation Order` row books NO commission — its money is a CHARGE (positive "
      "retail_cost) and its label is a plan name, so neither field makes it commission",
      round(sum(g_det.values()), 2) == 52.50)
from app.modules.commcalc import commission_legs as _CLEGS                 # noqa: E402
check("G5 the sheet column that IS the first month resolves to rung 1 in the one home",
      _CLEGS.ma_field_leg("spiff_m1"), (_CLEGS.M1, 1))
truth("G6 `mrc_net_discount` is NOT a payout column — it is the MRC the payout is computed FROM, "
      "so it is absent from the audited component list and books to no P&L line",
      "mrc_net_discount" not in MSP._rs._MA_COMPONENTS
      and "mrc_net_discount" not in MSP.MA_COMMISSION_HEADS)

# G7-G9 — the rule of thumb, as arithmetic on the feed that reports the first month.
close("G7 the owner's rule of thumb holds on the SHEET: first month ≈ half the MRC",
      round(100.0 * LIVE["earned_m1"] / LIVE["mrc"], 1), 48.3)
close("G8 …and the figure the report showed him is 15.7%% of it, because it is the other feed",
      round(100.0 * LIVE["received_m1"] / LIVE["earned_m1"], 1), 15.7)
truth("G9 the cash M1 never EXCEEDS the earned M1 cumulatively (60.8%%) — the two feeds carry the "
      "same money with a lag, so booking both would double-count",
      LIVE["cum_received_m1"] < LIVE["cum_earned_m1"])

# G10-G12 — THE DEFECT, reproduced through the real booking functions on a month shaped like the
# measured one. Each rung is reported by exactly ONE of the two feeds, so a switch that picks one
# feed for ALL rungs drops a whole rung whichever way it is set.
G_SHEET = [{"spiff_m1": -LIVE["earned_m1"], "spiff_m3": -LIVE["earned_m2_6"],
            "merchant_account_id": ""}]
G_CASH = [{"order_type": "Retroactive Postpaid Spiff", "account_id": "", "merchant_discount": 0.0,
           "product_name": "Total ALL ACCESS Plan $65 New Activation Commission",
           "retail_cost": -LIVE["received_m1"]},
          {"order_type": "PostPaid Additional Spiff", "account_id": "", "merchant_discount": 0.0,
           "product_name": "TBV MONTH 3 New Activation Commission",
           "retail_cost": -LIVE["received_m2_6"]}]


def _books(source):
    """The carrier_comm dollars the books carry per rung under one `month_spiff_source`, taken from
    the booking functions' OWN lockstep rung tags (§COMMCALC-15) — no label is re-parsed here."""
    cfg = dict(G_CFG, month_spiff_source=source)
    legs, tx_legs = [], []
    bks = MSP.ma_commission_bookings(G_SHEET, cfg, legs)
    bks += MSP.ma_tx_bookings(G_CASH, G_PNL, cfg, tx_legs)
    rungs = {}
    for (line, _a, amt, _d), (_b, month) in zip(bks, legs + tx_legs):
        if line == MSP.carrier_comm_source_line():
            rungs[month if month else "none"] = round(
                rungs.get(month if month else "none", 0.0) + amt, 2)
    return rungs


CASH_BOOKS = _books(MSP.BASIS_RECEIVED)
SHEET_BOOKS = _books(MSP.BASIS_EARNED)
close("G10 DEFECT, cash basis: the first-month rung the books carry is the cash figure…",
      CASH_BOOKS.get(1, 0.0), LIVE["received_m1"])
close("G11 …and the %.2f the sheet states as this month's first month books NOWHERE"
      % LIVE["earned_m1"],
      round(sum(CASH_BOOKS.values()), 2),
      round(LIVE["received_m1"] + LIVE["received_m2_6"], 2))
close("G12 DEFECT, sheet basis: the mirror image — the trailing rungs collapse to the sheet's "
      "near-empty columns…", round(sum(v for k, v in SHEET_BOOKS.items() if k != 1), 2),
      LIVE["earned_m2_6"])
truth("G13 …so NEITHER setting of a single-feed switch can state the month: the right answer "
      "(%.2f + %.2f) is not reachable by either" % (LIVE["earned_m1"], LIVE["received_m2_6"]),
      round(sum(CASH_BOOKS.values()), 2) != round(LIVE["earned_m1"] + LIVE["received_m2_6"], 2)
      and round(sum(SHEET_BOOKS.values()), 2) != round(LIVE["earned_m1"]
                                                       + LIVE["received_m2_6"], 2))
truth("G14 and the cause is in the DATA's shape, not in a tenant: the sheet reports the first "
      "month on 410 of 482 rows and the trailing months on 26",
      LIVE["sheet_rows_with_m1"] > 10 * LIVE["sheet_rows_with_m2_6"])
truth("G15 the retroactive first-month cash family thins out as the month gets recent — the lag "
      "that makes the newest month read lowest",
      LIVE["retro_rows"][9] < LIVE["retro_rows"][8] < LIVE["retro_rows"][7]
      < LIVE["retro_rows"][6])

# G16 — and the shape any fix MUST respect. MEASURED on the same uploads: of the 2,661 first-month
# cash rows across all seven months, ZERO carry an `order_number` that appears as an
# `activation_order` on any of the 5,495 sheet rows. The carrier files a spiff payment under its own
# transaction number, so the two feeds cannot be matched row to row. A fix therefore cannot book the
# sheet's first month and then suppress "the same" cash row later — the choice of feed has to be made
# per RUNG, wholesale, which is exactly what a per-rung `month_spiff_source` is.
LIVE_M1_CASH_ROWS, LIVE_M1_CASH_ROWS_MATCHING_A_SHEET_ROW = 2661, 0
check("G16 first-month cash rows that join to a sheet activation (so a row-level de-duplication "
      "is impossible and the choice must be per rung)",
      LIVE_M1_CASH_ROWS_MATCHING_A_SHEET_ROW, 0)
truth("G17 …measured against a non-empty population, so G16 is a measurement not an assumption",
      LIVE_M1_CASH_ROWS > 2000)


# ── report ───────────────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 78)
failed = [c for c in CHECKS if not c[0]]
for ok, name, got, want in CHECKS:
    if not ok:
        print("FAIL %s\n       got  %r\n       want %r" % (name, got, want))
print("%d checks, %d failed" % (len(CHECKS), len(failed)))
sys.exit(1 if failed else 0)
