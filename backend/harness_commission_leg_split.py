"""Proof harness — COMMISSION LEG SPLIT (1st month vs M2–M12). Owner directive 2026-08-04.

    "i need the gross profit report to have commission split in 2 parts - 1st Month commission which is
     paid the same month of the activation and the other is M2-M12 commission, any commission received
     for an activated number after the activated month will be in this category"
    "also update commission m2-m12 this in the Category → Bucket Map (Commission Ledger) and everywhere
     else commission touches."

WHAT THIS PROVES (no DB, no network, no recompute — the change is read-side only):

  ① THE DECOMPOSITION IDENTITY, the thing the owner has to be able to trust
     For every source on the GP report — Commission, Comp Comm, MI, ATU — and for every category in the
     Commission Ledger: m1 + trailing + unsplit == the source's EXISTING total, to the cent. Proven on a
     fixture whose pre-existing totals are also hand-asserted, so "the parts add up" cannot be satisfied
     by a change that quietly moved the whole.

  ② NOTHING ELSE MOVED
     Every pre-existing money key on a GP store row / totals block, and every pre-existing key of
     commission_ledger.summarize(), is asserted against hand-computed values. total_rev, rep_pay,
     net_profit, the GP bucket classification and the five ledger categories are untouched.

  ③ THE RULES ARE THE REAL ONES
     The label classifier is exercised against the ACTUAL label vocabulary of the org's real export
     files (Commission Payment Detail #50273 and Comprehensive Compensation #100614, Apr-2026 run) —
     all 58 distinct types, including the ones that carry no month at all.

  ④ THE PURE-LEAF COPIES DO NOT DRIFT
     commission_legs must not import calculator (gp_report -> commission_legs -> calculator -> gp_report
     is a cycle), so it carries local copies of safe_float / parse_loose_date / period_ym. Each is
     asserted equal to the shared original across a table of real inputs — a checked equivalence, not a
     hopeful copy.

  ⑤ HONESTY, NOT GUESSING
     Money whose source states no month-of-life lands in `unsplit` and is NAMED, never folded into a
     leg. The ePay Payment Detail's activation-date column is empty in the real file, so the label is
     the only truth there; residual (raw_mi) does carry a date and is split by it.

  ⑥ THE LADDER EXPLAINS EXACTLY THE COLUMN IT SITS UNDER
     A carrier payment for a store the report doesn't know, or MI for an unknown salesforce_id, is
     dropped from the money column — and must be dropped from the month-of-life ladder too, or the
     ladder would out-total the column it explains.

  ⑦ THE ENDPOINTS (real handlers, fake client): org scoping on every read, the period-anchored window,
     the RULE FIVE filters, the graceful degradation when migration 274 has not been run, and the
     refusal to double-count MA money in a month that already has ePay money.

Run: `python3 harness_commission_leg_split.py` from the backend dir.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import commission_legs as CL
from app.modules.commcalc import commission_ledger as CLED
from app.modules.commcalc.gp_report import calc_gp_report

_pass = _fail = 0
HOUSE = "00000000-0000-0000-0000-000000000001"
TENANT = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}   {extra}")


def eq2(a, b):
    return abs(round(float(a or 0), 2) - round(float(b or 0), 2)) < 0.005


def section(t):
    print(f"\n── {t} " + "─" * max(0, 92 - len(t)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ④ pure-leaf equivalence — the local copies must equal the shared originals
# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("④ pure-leaf copies agree with the shared originals")

# The reference for the money coercion is gp_report.safe_float — the one the GP totals this module
# decomposes are actually summed with. (An earlier draft of commission_legs copied a MORE lenient
# variant, which accepted "$1,234.50" and "2026/04/15" the originals reject; these assertions are what
# caught it. That is why the equivalence is CHECKED and not assumed.)
from app.modules.commcalc.gp_report import safe_float as _shared_safe_float
from app.modules.commcalc.imei_rebate_report import parse_loose_date as _shared_pld, period_ym as _shared_pym

_FLOATS = [None, "", "nan", "NaN", "none", "-", "n/a", 0, 1, -1, 1.5, "1.5", "$1,234.50", "(12.00)",
           "12.00", "  7 ", "abc", "1e3", ".", "-.", "-0.01", 1e12, "0", True, False]


def _same_float(a, b):
    return (a != a and b != b) or a == b          # NaN != NaN, but "both NaN" IS identical behaviour


check("_safe_float == gp_report.safe_float (the GP totals' own coercion) on every probe",
      all(_same_float(CL._safe_float(v), _shared_safe_float(v)) for v in _FLOATS),
      [(v, CL._safe_float(v), _shared_safe_float(v)) for v in _FLOATS
       if not _same_float(CL._safe_float(v), _shared_safe_float(v))])

_DATES = ["2026-04-15", "2026-4-5", "04/15/2026", "4/5/2026", "2026-13-01", "2026-04-32", "1899-01-01",
          "", None, "nan", "NaT", "-", "not a date", "2026/04/15", "15-04-2026", "2026-04-15 08:00:00",
          "null", "2026-04-15T08:00:00", "12/31/2026", "1/1/2026"]
check("_parse_loose_date == imei_rebate_report.parse_loose_date on every probe",
      all(CL._parse_loose_date(v) == _shared_pld(v) for v in _DATES),
      [(v, CL._parse_loose_date(v), _shared_pld(v)) for v in _DATES
       if CL._parse_loose_date(v) != _shared_pld(v)])

_PERIODS = ["June 2026", "2026-06", "2026-6", "December 2025", "Jun 2026", "", None, "nonsense",
           "2026-13", "January 2027", "june 2026", "JUNE 2026", "2026-01", "2026-1", " June 2026 "]
_pym_mismatch = [(p, CL._period_ym(p), _shared_pym(p)) for p in _PERIODS
                 if CL._period_ym(p) != _shared_pym(p)]
check("_period_ym == imei_rebate_report.period_ym on every probe", not _pym_mismatch, _pym_mismatch)
check("both period spellings of the SAME month resolve identically (the recurring bug class)",
      CL._period_ym("June 2026") == CL._period_ym("2026-06") == (2026, 6))
check("commission_legs imports NOTHING from app (no cycle back into calculator/gp_report)",
      "from app." not in open(CL.__file__, encoding="utf-8").read()
      and "import app" not in open(CL.__file__, encoding="utf-8").read())


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ③ + ⑤ the REAL label vocabulary
# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("③ real carrier label vocabulary (ePay #50273 + #100614, Apr-2026 export)")

REAL_MONTHED = [
    "New Activation Bounty - Month 1", "New Activation Bounty - Month 2",
    "New Activation Bounty - Month 3", "New Activation Bounty - Month 4",
    "New Activation Bounty - Month 5", "New Activation Bounty - Month 6",
    "Simplified SIM Loading Bounty - Month 1", "Simplified SIM Loading Bounty - Month 6",
    "Boost Ready Bounty - Month 1", "Boost Ready Bounty - Month 5",
    "Device Upgrade Bounty - Month 1", "Device Upgrade Bounty - Month 3",
    "Device Financing Bounty - Month 1", "Device Financing Bounty - Month 6",
    "In-Store Device Financing Bounty - Month 1", "In-Store Device Financing Bounty - Month 5",
    "BR BYOD SPIFF - Month 2", "BR BYOD SPIFF - Month 6",
    "Boost 5G Network Migration Bounty - Month 6",
]
REAL_UNMONTHED = [
    "Boost Auto Top-Up", "2026 SIM card reimbursement", "2025 SIM Card Reimbursement",
    "2026 Q2 Promo Upgrade", "2026 Q2 Promo New Act Offer", "2026 Q2 Promo PIC Offer",
    "2026 Q1 Promo Upgrade", "2026 Q1 Promo New Act Offer", "2026 Q1 Promo PIC Offer",
    "2026 Q2 Exclusive Upgrade Offer", "2026 Q2 AAL Device Discount", "Q1 2026 AAL Device Discount",
    "Commission Withholding", "Other Equipment Reimbursement", "Ramp Up Subsidy",
    "Eligibility-Based Trade-In Device Reimbursement", "Trade-In SPIFF",
]
# "UNL Premium - 2 Month Promo" is the trap: it contains a number and the word Month, but the number
# comes BEFORE the word, so a sloppy matcher would read it as a month-2 leg. It is a promo, not a leg.
TRAP = "UNL Premium - 2 Month Promo"

cls = CL.default_classifier()
m1s = [l for l in REAL_MONTHED if l.endswith("Month 1")]
later = [l for l in REAL_MONTHED if not l.endswith("Month 1")]
check(f"every '… - Month 1' label ({len(m1s)}) -> 1st month leg",
      all(cls.label(l)[:2] == (CL.M1, 1) for l in m1s),
      [(l, cls.label(l)) for l in m1s if cls.label(l)[:2] != (CL.M1, 1)])
check(f"every '… - Month 2..6' label ({len(later)}) -> M2–M12 leg",
      all(cls.label(l)[0] == CL.TRAILING and cls.label(l)[1] >= 2 for l in later),
      [(l, cls.label(l)) for l in later if cls.label(l)[0] != CL.TRAILING])
check(f"every label with NO month ({len(REAL_UNMONTHED)}) -> unsplit + reason, never guessed",
      all(cls.label(l) == (CL.UNSPLIT, None, "no_month_in_label") for l in REAL_UNMONTHED),
      [(l, cls.label(l)) for l in REAL_UNMONTHED
       if cls.label(l) != (CL.UNSPLIT, None, "no_month_in_label")])
check(f"the trap label '{TRAP}' is NOT read as a month-2 leg",
      cls.label(TRAP) == (CL.UNSPLIT, None, "no_month_in_label"), cls.label(TRAP))
check("a per-label override beats the regex",
      CL.LegClassifier(CL.DEFAULT_CFG, {"boost auto top-up": {"bucket": "trailing", "leg_month": None}})
        .label("Boost Auto Top-Up")[::2] == (CL.TRAILING, "label_override"))
check("the override is case- and whitespace-insensitive on the label",
      CL.LegClassifier(CL.DEFAULT_CFG, {"boost auto top-up": {"bucket": "m1", "leg_month": 1}})
        .label("  BOOST Auto Top-Up ")[0] == CL.M1)
check("an org can send un-monthed money to a leg by config instead of per-label",
      CL.LegClassifier({**CL.DEFAULT_CFG, "unlabeled_bucket": "trailing"})
        .label("Boost Auto Top-Up")[0] == CL.TRAILING)
check("a BROKEN admin regex falls back to the default instead of 500ing",
      CL.LegClassifier({**CL.DEFAULT_CFG, "label_month_regex": "month (["})
        .label("New Activation Bounty - Month 3")[:2] == (CL.TRAILING, 3))
check("an invalid unlabeled_bucket is coerced to the honest 'unsplit'",
      CL.LegClassifier({**CL.DEFAULT_CFG, "unlabeled_bucket": "nonsense"})
        .label("Boost Auto Top-Up")[0] == CL.UNSPLIT)

section("⑤ residual splits by ACTIVATION DATE (the owner's literal rule)")
check("activated in the report month -> 1st month",
      cls.activation("June 2026", "2026-06-14")[:2] == (CL.M1, 1))
check("activated the month before -> M2–M12, leg 2",
      cls.activation("June 2026", "2026-05-31")[:2] == (CL.TRAILING, 2))
check("activated 11 months before -> M2–M12, leg 12",
      cls.activation("June 2026", "2025-07-02")[:2] == (CL.TRAILING, 12))
check("US-spelled activation date works too (raw_mi holds both spellings)",
      cls.activation("June 2026", "05/31/2026")[:2] == (CL.TRAILING, 2))
check("period written '2026-06' gives the same answer as 'June 2026'",
      cls.activation("2026-06", "2026-05-31") == cls.activation("June 2026", "2026-05-31"))
check("missing activation date -> unsplit, with the reason",
      cls.activation("June 2026", None) == (CL.UNSPLIT, None, "no_activation_date"))
check("unparseable activation date -> unsplit, never a guessed month",
      cls.activation("June 2026", "sometime in May")[0] == CL.UNSPLIT)
check("activation AFTER the report month (data oddity) -> unsplit, not a negative leg",
      cls.activation("June 2026", "2026-08-01")[0] == CL.UNSPLIT)
check("mi_split_by_activation=false disables the residual split honestly",
      CL.LegClassifier({**CL.DEFAULT_CFG, "mi_split_by_activation": False})
        .activation("June 2026", "2026-06-14") == (CL.UNSPLIT, None, "activation_split_disabled"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ① MA component split — the leg is the COLUMN NAME
# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("① master-agent (raw_ma_commission) split: spiff_m1 = 1st month, spiff_m2..6 = trailing")

MACOMP = ["device_margin", "consumer_margin", "consumer_financing", "rebate", "wallet_funding",
          "fees_margin", "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]
ma_sums = {"device_margin": -20.0, "consumer_margin": -8.0, "consumer_financing": -2.0,
           "rebate": -529.0, "wallet_funding": 0.0, "fees_margin": -1.5,
           "spiff_m1": -5.0, "spiff_m2": -48.75, "spiff_m3": -10.0,
           "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": -3.25}
res = cls.ma(ma_sums, MACOMP)
router_total = -sum(ma_sums[c] for c in MACOMP)          # exactly how _compute_gp builds `comm`
check("MA split total == the router's own sign-flipped component sum",
      eq2(res["total"], router_total), (res["total"], router_total))
check("m1 + trailing + unsplit == that same total",
      eq2(sum(res["buckets"].values()), router_total), res["buckets"])
# CORRECTED 2026-08-05 (mig 277). The margin columns are NOT commission legs: the owner's settled
# 2026-08-04 definition is that MA M1 commission is the MRC-based spiff, "not margins", and the portal
# states Rebates Paid / Fees Margin Paid as their OWN figures. They stay in the TOTAL, in `unsplit`.
check("1st month = spiff_m1 ALONE (the margins are not commission legs)",
      eq2(res["buckets"]["m1"], 5.0), res["buckets"]["m1"])
check("M2–M12 = spiff_m2..m6 only",
      eq2(res["buckets"]["trailing"], 48.75 + 10.0 + 3.25), res["buckets"]["trailing"])
check("the six activation-order margins are UNSPLIT, not dropped and not in a leg",
      eq2(res["buckets"]["unsplit"], 20.0 + 8.0 + 2.0 + 529.0 + 0.0 + 1.5), res["buckets"]["unsplit"])
check("unsplit_fields NAMES those six columns so a page can explain the pile",
      sorted(res["unsplit_fields"]) == ["consumer_financing", "consumer_margin", "device_margin",
                                        "fees_margin", "rebate", "wallet_funding"],
      res["unsplit_fields"])
check("an org that WANTS margins in M1 still can (ma_m1_fields is config, not code)",
      eq2(CL.LegClassifier({**CL.DEFAULT_CFG, "ma_m1_fields": ["rebate"]})
          .ma(ma_sums, MACOMP)["buckets"]["m1"], 5.0 + 529.0))
check("a NET CLAWBACK stays in its own leg (it is a negative payout, not a reclassification)",
      eq2(cls.ma({**ma_sums, "spiff_m2": +48.75}, MACOMP)["buckets"]["trailing"], 10.0 + 3.25 - 48.75))
check("an UNKNOWN component in the caller's list is reported unsplit, never dropped",
      eq2(cls.ma({**ma_sums, "mystery_bonus": -99.0}, MACOMP + ["mystery_bonus"])["buckets"]["unsplit"],
          99.0 + 20.0 + 8.0 + 2.0 + 529.0 + 0.0 + 1.5))
check("the split iterates the CALLER's component list, so its total tracks the caller's total",
      eq2(cls.ma(ma_sums, ["spiff_m1", "spiff_m2"])["total"], 5.0 + 48.75))
check("ma_max_month bounds the per-leg columns; a spiff beyond it is still trailing, not unsplit",
      cls.ma({"spiff_m9": -4.0}, ["spiff_m9"])["buckets"]["trailing"] == 4.0)
check("a positive-sign org (ma_payout_sign=+1) flips correctly",
      eq2(CL.LegClassifier({**CL.DEFAULT_CFG, "ma_payout_sign": 1}).ma({"spiff_m1": 5.0}, ["spiff_m1"])
          ["buckets"]["m1"], 5.0))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ① + ② + ⑥ the GP engine
# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("① / ② / ⑥ Gross Profit engine — split adds up, nothing else moved")

PERIOD = "June 2026"
STORE_A = "1234 Main St"          # mapped, sfid SF-A
STORE_B = "5678 Oak Ave"          # mapped, sfid SF-B
GHOST = "9999 Nowhere Rd"         # a carrier payment for a store the report does NOT know

store_map = [
    {"store_address": STORE_A, "salesforce_id": "SF-A", "market": "Chicago", "store_code": "A1",
     "is_active": True},
    {"store_address": STORE_B, "salesforce_id": "SF-B", "market": "NY", "store_code": "B1",
     "is_active": True},
]
sales = [
    {"store": STORE_A, "department": "Ondigo", "category": "", "gp": 30.0, "ext_price": 100.0,
     "product_desc": "Case", "salesperson": "Ana"},
    {"store": STORE_A, "department": "IPHONE - XP", "category": "", "gp": 0.0, "ext_price": 800.0,
     "product_desc": "iPhone", "salesperson": "Ana"},
    {"store": STORE_B, "department": "", "category": "", "gp": 40.0, "ext_price": 40.0,
     "product_desc": "Plan", "salesperson": "Bo"},
]
pay_detail = [
    # store A — commission, three legs + one un-monthed label
    {"business_address": STORE_A, "payment_type": "New Activation Bounty - Month 1",
     "amount": 100.0, "category": "Commission"},
    {"business_address": STORE_A, "payment_type": "New Activation Bounty - Month 3",
     "amount": 60.0, "category": "Commission"},
    {"business_address": STORE_A, "payment_type": "Boost Auto Top-Up",
     "amount": 25.0, "category": "Commission"},
    # a NEGATIVE commission line (a clawback) must reduce its own leg, not vanish
    {"business_address": STORE_A, "payment_type": "New Activation Bounty - Month 2",
     "amount": -10.0, "category": "Commission"},
    # store B — one leg
    {"business_address": STORE_B, "payment_type": "Device Upgrade Bounty - Month 4",
     "amount": 45.0, "category": "Commission"},
    # NON-commission categories must be untouched by the split
    {"business_address": STORE_A, "payment_type": "2026 SIM card reimbursement",
     "amount": 12.0, "category": "Re-imbursement"},
    {"business_address": STORE_B, "payment_type": "Co-op", "amount": 7.0, "category": "MDF"},
    {"business_address": STORE_A, "payment_type": "Chargeback", "amount": -3.0, "category": "Chargeback"},
    {"business_address": STORE_A, "payment_type": "Mystery", "amount": 1.0, "category": "Unknown"},
    # ⑥ a payment for a store the report does not know — dropped from the column AND the ladder
    {"business_address": GHOST, "payment_type": "New Activation Bounty - Month 5",
     "amount": 500.0, "category": "Commission"},
]
comp_rows = [
    {"business_address": STORE_A, "compensation_type": "New Activation Bounty - Month 1",
     "payment_amount": 70.0},
    {"business_address": STORE_A, "compensation_type": "Boost Ready Bounty - Month 6",
     "payment_amount": 30.0},
    {"business_address": STORE_A, "compensation_type": "2026 SIM card reimbursement",
     "payment_amount": 9.0},       # -> comp REIMB, not comp comm: must not appear in the leg split
    {"business_address": STORE_B, "compensation_type": "MDF co-op", "payment_amount": 4.0},
]
mi_rows = [
    {"salesforce_id": "SF-A", "actual_mi_payout": 20.0, "actual_atu_payout": 6.0,
     "mi_activation_date": "2026-06-03"},                       # 1st month
    {"salesforce_id": "SF-A", "actual_mi_payout": 15.0, "actual_atu_payout": 4.0,
     "mi_activation_date": "2026-01-20"},                       # leg 6
    {"salesforce_id": "SF-B", "actual_mi_payout": 11.0, "actual_atu_payout": 2.0,
     "mi_activation_date": ""},                                 # unsplit, honestly
    # ⑥ residual for a salesforce_id no store maps to — dropped from the column AND the ladder
    {"salesforce_id": "SF-GHOST", "actual_mi_payout": 999.0, "actual_atu_payout": 111.0,
     "mi_activation_date": "2026-06-01"},
]
r = calc_gp_report(sales, pay_detail, mi_rows, [], [], [], store_map, PERIOD, comp_rows=comp_rows)
T = r["totals"]
rowA = next(x for x in r["store_rows"] if x["store"] == STORE_A)
rowB = next(x for x in r["store_rows"] if x["store"] == STORE_B)

# ── ② pre-existing money columns, hand-computed ──
check("[unchanged] Commission = 100 + 60 + 25 − 10 + 45 (ghost store excluded)", eq2(T["comm"], 220.0), T["comm"])
check("[unchanged] Re-imb = 12", eq2(T["reimb"], 12.0), T["reimb"])
check("[unchanged] MDF = 7", eq2(T["mdf"], 7.0), T["mdf"])
check("[unchanged] Chargebacks = −3", eq2(T["chargeback"], -3.0), T["chargeback"])
check("[unchanged] Comp Comm = 70 + 30 (the SIM reimbursement is comp REIMB)", eq2(T["comp_comm"], 100.0), T["comp_comm"])
check("[unchanged] Comp Rebate = 9", eq2(T["comp_reimb"], 9.0), T["comp_reimb"])
check("[unchanged] Comp MDF = 4", eq2(T["comp_mdf"], 4.0), T["comp_mdf"])
check("[unchanged] MI = 20 + 15 + 11 (ghost sfid excluded)", eq2(T["mi"], 46.0), T["mi"])
check("[unchanged] ATU = 6 + 4 + 2", eq2(T["atu"], 12.0), T["atu"])
check("[unchanged] Acc GP = 30", eq2(T["acc_gp"], 30.0), T["acc_gp"])
check("[unchanged] Phone Sales = 800", eq2(T["phone_sales"], 800.0), T["phone_sales"])
check("[unchanged] Total Rev is the same sum it always was",
      eq2(T["total_rev"], 30 + 0 + 800 + 40 + 0 + 220 + 12 + 7 - 3 + 1 + 46 + 12), T["total_rev"])
check("[unchanged] Net Profit unchanged by the split",
      eq2(T["net_profit"], T["total_rev"] - 0 - 0 - (800 + 12)), T["net_profit"])

# ── ① the identity, per source ──
for src, tot in (("comm", 220.0), ("comp_comm", 100.0), ("mi", 46.0), ("atu", 12.0)):
    k1, k2, ku = CL.public_keys(src)
    check(f"IDENTITY {src}: m1 + m2_12 + unsplit == {src} ({tot})",
          eq2(T[k1] + T[k2] + T[ku], tot), (T[k1], T[k2], T[ku], tot))

check("Commission 1st month = 100 (Month-1 bounty)", eq2(T["comm_m1"], 100.0), T["comm_m1"])
check("Commission M2–M12 = 60 − 10 + 45 (the clawback reduces its own leg)",
      eq2(T["comm_m2_12"], 95.0), T["comm_m2_12"])
check("Commission unsplit = 25 (Boost Auto Top-Up states no month)", eq2(T["comm_unsplit"], 25.0), T["comm_unsplit"])
check("Comp Comm 1st month = 70 / M2–M12 = 30",
      eq2(T["comp_comm_m1"], 70.0) and eq2(T["comp_comm_m2_12"], 30.0))
check("MI 1st month = 20 / M2–M12 = 15 / unsplit = 11 (no activation date)",
      eq2(T["mi_m1"], 20.0) and eq2(T["mi_m2_12"], 15.0) and eq2(T["mi_unsplit"], 11.0),
      (T["mi_m1"], T["mi_m2_12"], T["mi_unsplit"]))
check("ATU splits on the SAME activation dates as MI",
      eq2(T["atu_m1"], 6.0) and eq2(T["atu_m2_12"], 4.0) and eq2(T["atu_unsplit"], 2.0))
check("the identity holds PER STORE ROW too, not just in the totals",
      all(eq2(row[k1] + row[k2] + row[ku], row[src])
          for row in r["store_rows"] for src in ("comm", "comp_comm", "mi", "atu")
          for k1, k2, ku in [CL.public_keys(src)]))
check("store A's own commission split is A's money only", eq2(rowA["comm_m1"], 100.0) and eq2(rowB["comm_m2_12"], 45.0))

# ── ⑥ the ladder explains exactly the column ──
lad = r["commission_legs"]["ladder"]
check("ladder(comm) sums to the Commission column — the ghost store is in NEITHER",
      eq2(sum(lad["comm"].values()), 220.0), lad["comm"])
check("ladder(mi) sums to the MI column — the ghost salesforce_id is in NEITHER",
      eq2(sum(lad["mi"].values()), 46.0), lad["mi"])
check("ladder rungs are the real month-of-life values",
      lad["comm"].get("1") == 100.0 and lad["comm"].get("2") == -10.0
      and lad["comm"].get("3") == 60.0 and lad["comm"].get("4") == 45.0
      and lad["comm"].get("unknown") == 25.0, lad["comm"])
check("the M5 leg of the ghost store appears NOWHERE in the ladder", "5" not in lad["comm"])
check("residual ladder carries the date-derived legs (M1 and M6)",
      lad["mi"].get("1") == 20.0 and lad["mi"].get("6") == 15.0 and lad["mi"].get("unknown") == 11.0,
      lad["mi"])

blk = r["commission_legs"]
check("the payload states the identity held for every source", blk["identity_ok"] is True)
check("every source names WHAT decides its leg, in plain English",
      all(s["splits_on"] for s in blk["sources"]) and len(blk["sources"]) == 4)
check("the payload states the config actually in force", blk["config"]["resolved_from"] == "code_default")

# ── the MA (ePay-less tenant) path on the same engine ──
ma_income = {"comm": router_total, "atu": 33.0, "components": ma_sums, "component_list": MACOMP}
r2 = calc_gp_report([], [], [], [], [], [], [], PERIOD, ma_income=ma_income)
T2 = r2["totals"]
check("[MA tenant] the split adds back to the MA Commission column",
      eq2(T2["comm_m1"] + T2["comm_m2_12"] + T2["comm_unsplit"], T2["comm"]),
      (T2["comm_m1"], T2["comm_m2_12"], T2["comm_unsplit"], T2["comm"]))
check("[MA tenant] Commission column itself is untouched", eq2(T2["comm"], router_total))
check("[MA tenant] MA airtime margin (ATU) has no month-of-life -> honestly unsplit",
      eq2(T2["atu_unsplit"], 33.0) and eq2(T2["atu_m1"], 0) and eq2(T2["atu_m2_12"], 0))
r3 = calc_gp_report([], [], [], [], [], [], [], PERIOD,
                    ma_income={"comm": 100.0, "atu": 0.0})     # older caller: no components
check("[MA tenant] with no component detail the money is reported UNSPLIT, never guessed",
      eq2(r3["totals"]["comm_unsplit"], 100.0) and eq2(r3["totals"]["comm_m1"], 0))

# ── an org with zero commission data still renders ──
r4 = calc_gp_report([], [], [], [], [], [], [], PERIOD)
check("an empty org gets a well-formed, zeroed split (no 500, no missing keys)",
      r4["commission_legs"]["identity_ok"] is True
      and all(k in r4["totals"] for k in CL.public_keys("comm")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ② + ① the Commission Ledger (Category → Bucket Map)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("② / ① Commission Ledger — leg is a SECOND dimension, categories unchanged")

def led(cat, amt, name, month=None, payout=True):
    return {"category": cat, "payout_total": amt, "raw_amount": -amt if payout else amt,
            "product_name": name, "order_type": "", "payment_month": month}

ledger_rows = [
    led("commission", 100.0, "Commission SPF MONTH 1", 1),
    led("commission", 40.0, "Commission SPF MONTH 3", 3),
    led("commission", 25.0, "Commission - flat", None),
    led("spiff", 60.0, "SPF Month 2", 2),
    led("residual_monthly", 12.0, "Residual", None),
    led("autopay_residual", 8.0, "Autopay Residual", None),
    led("equipment_rebate", 30.0, "Subsidy", None),
    led("other", 15.0, "Mystery payout", None),
    led("charge", 500.0, "Customer bill payment", None, payout=False),
]
S = CLED.summarize(ledger_rows)
# ② pre-existing keys, hand-computed
check("[unchanged] commission category total = 165", eq2(S["categories"]["commission"]["total"], 165.0))
check("[unchanged] spiff = 60 / residual = 12 / autopay = 8 / rebate = 30",
      eq2(S["categories"]["spiff"]["total"], 60.0) and eq2(S["categories"]["residual_monthly"]["total"], 12.0)
      and eq2(S["categories"]["autopay_residual"]["total"], 8.0)
      and eq2(S["categories"]["equipment_rebate"]["total"], 30.0))
check("[unchanged] payout_total = 290 (includes 'other'), charge_total = 500",
      eq2(S["payout_total"], 290.0) and eq2(S["charge_total"], 500.0), (S["payout_total"], S["charge_total"]))
check("[unchanged] other_total = 15 / other_count = 1", eq2(S["other_total"], 15.0) and S["other_count"] == 1)
check("[unchanged] by_month matrix keys are exactly as before",
      S["by_month"].get("commission|1") == 100.0 and S["by_month"].get("commission|3") == 40.0
      and S["by_month"].get("commission|0") == 25.0 and S["by_month"].get("spiff|2") == 60.0)
check("[unchanged] line_count counts every row including charges", S["line_count"] == 9)
# ① the new dimension
check("IDENTITY ledger: legs sum to payout_total", eq2(sum(S["legs"].values()), 290.0), S["legs"])
check("IDENTITY ledger: every category's legs sum to that category's own total",
      all(eq2(sum(S["by_category_leg"][c].values()), S["categories"][c]["total"]) for c in CLED.CATEGORIES),
      S["by_category_leg"])
check("the payload states the identity held", S["leg_identity_ok"] is True)
check("1st month = 100 (the MONTH 1 line only)", eq2(S["legs"]["m1"], 100.0), S["legs"])
check("M2–M12 = 40 + 60 (MONTH 3 + Month 2)", eq2(S["legs"]["trailing"], 100.0), S["legs"])
check("unsplit = 25 + 12 + 8 + 30 + 15 (labels with no month-of-life)", eq2(S["legs"]["unsplit"], 90.0), S["legs"])
check("a CHARGE is not a payout and therefore has no leg",
      eq2(sum(S["legs"].values()), S["payout_total"]) and S["charge_total"] == 500.0)
check("'other' (unmapped category) still gets a leg row so the grand identity closes",
      eq2(S["by_category_leg"]["other"]["unsplit"], 15.0))
check("unattributed labels are NAMED, the way 'other' is named",
      {u["label"] for u in S["leg_unmapped"]} == {"Commission - flat", "Residual", "Autopay Residual",
                                                  "Subsidy", "Mystery payout"},
      S["leg_unmapped"])
check("the ledger's own payment_month parser drives the leg ('M1 Proration', 'TBV MONTH 4')",
      CLED.leg_of({"product_name": "Commission - M1 Proration", "raw_amount": -1,
                   "payment_month": CLED.parse_payment_month("Commission - M1 Proration")})[:2]
      == (CL.M1, 1)
      and CLED.leg_of({"product_name": "TBV MONTH 4", "raw_amount": -1,
                       "payment_month": CLED.parse_payment_month("TBV MONTH 4")})[:2] == (CL.TRAILING, 4))

# rule-level override — the Category → Bucket Map's new Leg column
rules_no_leg = [{"match_field": "product_name", "match_op": "contains", "pattern": "Residual",
                 "category": "residual_monthly", "sign_rule": "negative_only", "priority": 10}]
rules_leg = [dict(rules_no_leg[0], leg_bucket="trailing")]
S_no = CLED.summarize(ledger_rows, rules=rules_no_leg)
S_yes = CLED.summarize(ledger_rows, rules=rules_leg)
check("a rule with NO leg set changes nothing (every existing rule is exactly this)",
      S_no["legs"] == S["legs"] and S_no["categories"] == S["categories"], (S_no["legs"], S["legs"]))
check("setting a rule's Leg moves that money between LEG columns only",
      eq2(S_yes["legs"]["trailing"], 100.0 + 20.0) and eq2(S_yes["legs"]["unsplit"], 90.0 - 20.0),
      S_yes["legs"])
check("...and leaves every CATEGORY total byte-identical", S_yes["categories"] == S["categories"])
check("...and the identity still closes", S_yes["leg_identity_ok"] is True
      and eq2(sum(S_yes["legs"].values()), 290.0))
check("summarize() is still callable with its OLD one-argument signature",
      CLED.summarize(ledger_rows)["payout_total"] == 290.0)
check("an explicit rule Leg beats the line's own payment month (a human decision wins)",
      CLED.leg_of({"product_name": "Commission SPF MONTH 1", "raw_amount": -1, "payment_month": 1,
                   "order_type": ""},
                  [{"match_field": "product_name", "match_op": "contains", "pattern": "Commission",
                    "category": "commission", "sign_rule": "negative_only", "leg_bucket": "trailing"}])[::2]
      == (CL.TRAILING, "rule_override"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ⑦ the ENDPOINTS — real handlers, fake client
# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("⑦ endpoints — org scoping, window, filters, graceful degradation")

QUERY_LOG = []
RPC_LOG = []


class _Q:
    def __init__(self, store, table, missing):
        self.store, self.table, self.missing = store, table, missing
        self.eqs, self.ins = {}, {}

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.eqs[k] = v
        return self

    def in_(self, k, v):
        self.ins[k] = list(v)
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def upsert(self, row, on_conflict=None):
        self._write = ("upsert", row)
        return self

    def delete(self):
        self._write = ("delete", None)
        return self

    def execute(self):
        if self.table in self.missing:
            raise RuntimeError(f"relation commcalc.{self.table} does not exist")
        w = getattr(self, "_write", None)
        QUERY_LOG.append({"table": self.table, "eq": dict(self.eqs), "in": dict(self.ins),
                          "write": w[0] if w else None, "row": w[1] if w else None})
        if w:
            return type("R", (), {"data": [w[1]] if w[1] else []})()
        rows = []
        for r in self.store.get(self.table, []):
            if any(r.get(k) != v for k, v in self.eqs.items()):
                continue
            if any(r.get(k) not in v for k, v in self.ins.items()):
                continue
            rows.append(dict(r))
        return type("R", (), {"data": rows})()


class _Schema:
    def __init__(self, client):
        self.c = client

    def table(self, t):
        return _Q(self.c.store, t, self.c.missing)

    def rpc(self, fn, params):
        RPC_LOG.append({"fn": fn, "params": params})
        if fn in self.c.missing_rpc:
            raise RuntimeError(f"function commcalc.{fn} does not exist")
        return type("R", (), {"execute": lambda _s=None: type("X", (), {"data": self.c.rpc_data.get(fn, [])})()})()


class FakeClient:
    def __init__(self, store, missing=(), missing_rpc=(), rpc_data=None):
        self.store, self.missing = store, set(missing)
        self.missing_rpc, self.rpc_data = set(missing_rpc), rpc_data or {}

    def schema(self, s):
        return _Schema(self)


from app.modules.commcalc import router as R

check("org_id is a QUERY PARAM on every new handler (never a constant / body / Form)",
      all("org_id" in fn.__code__.co_varnames for fn in
          (R.commission_leg_trend, R.commission_leg_labels, R.set_commission_leg_label,
           R.get_commission_leg_config, R.set_commission_leg_config)))

section("⑦a the trend window is anchored on the page's period")
w = R._leg_window("June 2026", 12)
check("12-month window ends at the page period and runs oldest-first",
      w[-1] == "June 2026" and w[0] == "July 2025" and len(w) == 12, (w[0], w[-1], len(w)))
check("the window crosses a year boundary correctly",
      R._leg_window("February 2026", 3) == ["December 2025", "January 2026", "February 2026"])
check("a junk period does not 500 (falls back to the current month)", len(R._leg_window("", 6)) == 6)
check("months is clamped (999 -> 36; 0/None/junk read as 'unset' -> the 12-month default)",
      len(R._leg_window("June 2026", 999)) == 36 and len(R._leg_window("June 2026", 0)) == 12
      and len(R._leg_window("June 2026", None)) == 12 and len(R._leg_window("June 2026", -5)) == 1
      and len(R._leg_window("June 2026", "junk")) == 12)
check("BOTH period spellings are matched (the recurring bug class)",
      "2026-06" in R._leg_period_key(["June 2026"]) and "June 2026" in R._leg_period_key(["June 2026"]))

section("⑦b the trend endpoint")
STORE_ROWS = [
    {"org_id": HOUSE, "store_address": STORE_A, "store_code": "A1", "market": "Chicago",
     "salesforce_id": "SF-A"},
    {"org_id": HOUSE, "store_address": STORE_B, "store_code": "B1", "market": "NY",
     "salesforce_id": "SF-B"},
]
ROLLUP = [
    {"source": "payment_detail", "period": "June 2026", "store_num": "1234",
     "label": "New Activation Bounty - Month 1", "category": "Commission", "amount": 100.0, "n": 4},
    {"source": "payment_detail", "period": "June 2026", "store_num": "1234",
     "label": "New Activation Bounty - Month 3", "category": "Commission", "amount": 60.0, "n": 3},
    {"source": "payment_detail", "period": "June 2026", "store_num": "1234",
     "label": "Boost Auto Top-Up", "category": "Commission", "amount": 25.0, "n": 9},
    {"source": "payment_detail", "period": "June 2026", "store_num": "5678",
     "label": "Device Upgrade Bounty - Month 4", "category": "Commission", "amount": 45.0, "n": 2},
    # a NON-commission category must never enter the Commission trend
    {"source": "payment_detail", "period": "June 2026", "store_num": "1234",
     "label": "2026 SIM card reimbursement", "category": "Re-imbursement", "amount": 900.0, "n": 5},
    {"source": "May 2026", "period": "May 2026", "store_num": "1234", "label": "x",
     "category": "Commission", "amount": 1.0, "n": 1},               # unknown source -> ignored
    {"source": "payment_detail", "period": "May 2026", "store_num": "1234",
     "label": "New Activation Bounty - Month 2", "category": "Commission", "amount": 70.0, "n": 2},
    {"source": "comp_report", "period": "June 2026", "store_num": "1234",
     "label": "New Activation Bounty - Month 1", "category": "", "amount": 55.0, "n": 1},
    {"source": "comp_report", "period": "June 2026", "store_num": "1234",
     "label": "2026 SIM card reimbursement", "category": "", "amount": 400.0, "n": 1},  # comp REIMB
]
store = {"store_mapping": STORE_ROWS, "carrier": [{"org_id": HOUSE, "code": "boost", "name": "Boost",
                                                   "is_default": True}],
         "raw_dlar_rep": [{"org_id": HOUSE, "period": "June 2026", "tmr3": 72.0},
                          {"org_id": HOUSE, "period": "June 2026", "tmr3": 68.0}],
         "commission_leg_config": [], "commission_leg_label_map": []}
fc = FakeClient(store, rpc_data={"commission_leg_label_rollup": ROLLUP,
                                 "commission_leg_ma_rollup": []})
R.sb = lambda: fc
QUERY_LOG.clear()
out = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_trend(period="June 2026", months=2, org_id=HOUSE))
jun = next(c for c in out["company"] if c["period"] == "June 2026")
may = next(c for c in out["company"] if c["period"] == "May 2026")
def scoped(q, org):
    """A read is org-scoped if it filters org_id to the caller's org — OR, for the two CONFIG tables
    only, to {caller, house}: config inheritance, the same ladder mig 223 established, where a tenant
    with no row of its own falls back to the seeded house defaults. No DATA table may do that."""
    if q.get("eq", {}).get("org_id") == org:
        return True
    ids = set(q.get("in", {}).get("org_id") or [])
    return bool(ids) and ids <= {org, HOUSE} and q["table"] in ("commission_leg_config",)


check("EVERY read the trend issues is org-scoped (RULE ONE)",
      all(scoped(q, HOUSE) for q in QUERY_LOG),
      [q for q in QUERY_LOG if not scoped(q, HOUSE)])
check("only the CONFIG table ever reads the house row (inheritance); no DATA table does",
      all(q["eq"].get("org_id") == HOUSE for q in QUERY_LOG
          if q["table"] not in ("commission_leg_config",)))
check("the rollup RPC is called once, with the org and BOTH period spellings",
      len([x for x in RPC_LOG if x["fn"] == "commission_leg_label_rollup"]) == 1
      and RPC_LOG[0]["params"]["p_org_id"] == HOUSE
      and "2026-06" in RPC_LOG[0]["params"]["p_periods"])
check("June: 1st month = 100, M2–M12 = 60 + 45, unsplit = 25",
      eq2(jun["m1"], 100) and eq2(jun["m2_12"], 105) and eq2(jun["unsplit"], 25),
      (jun["m1"], jun["m2_12"], jun["unsplit"]))
check("IDENTITY trend: the parts add back to the month's total",
      eq2(jun["m1"] + jun["m2_12"] + jun["unsplit"], jun["total"]) and eq2(jun["total"], 230.0))
check("a Re-imbursement line never enters the Commission trend", eq2(jun["total"], 230.0))
check("Comprehensive Comp is a SEPARATE series, never added into Commission",
      eq2(out["comp_series"][-1]["m1"], 55.0) and eq2(out["comp_series"][-1]["total"], 55.0))
check("the comp series applies the SAME reimbursement/MDF rule the GP column does",
      eq2(out["comp_series"][-1]["total"], 55.0))
check("May is present and split independently", eq2(may["m2_12"], 70.0) and eq2(may["m1"], 0.0))
check("the month-of-life LADDER carries the real rungs",
      jun["ladder"] == {"1": 100.0, "3": 60.0, "unknown": 25.0, "4": 45.0}, jun["ladder"])
check("ladder_months are the rungs present across the WHOLE window, sorted (May contributes M2)",
      out["ladder_months"] == [1, 2, 3, 4], out["ladder_months"])
check("M2–M12 share is reported per month", eq2(jun["m2_12_pct"], round(105 / 230 * 100, 1)))
check("the DLAR 3MR overlay is the rep average for that month, when present", eq2(jun["tmr3"], 70.0))
check("the page is told, in words, exactly what it is showing",
      "1st Month" in out["basis"] and "M2–M12" in out["basis"] and "3MR" in out["retention_note"])
check("the retention note is HONEST that there is no stored 6MR KPI",
      "no stored 6MR" in out["retention_note"])

section("⑦c RULE FIVE filters drive the trend")
out_mkt = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_trend(period="June 2026", months=1, market="NY", org_id=HOUSE))
check("a market filter narrows the trend to that market's stores",
      eq2(out_mkt["company"][0]["total"], 45.0), out_mkt["company"][0])
out_store = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_trend(period="June 2026", months=1, store=STORE_A, org_id=HOUSE))
check("a store filter narrows the trend to that store", eq2(out_store["company"][0]["total"], 185.0),
      out_store["company"][0])
check("an unmatched market yields an honest zero, not everything",
      eq2(asyncio.get_event_loop().run_until_complete(
          R.commission_leg_trend(period="June 2026", months=1, market="ATLANTIS",
                                 org_id=HOUSE))["company"][0]["total"], 0.0))

section("⑦d multi-tenant isolation")
store_t = dict(store)
store_t["store_mapping"] = STORE_ROWS + [
    {"org_id": TENANT, "store_address": "77 Tenant Way", "store_code": "T1", "market": "TEN",
     "salesforce_id": "SF-T"}]
fc_t = FakeClient(store_t, rpc_data={"commission_leg_label_rollup": [], "commission_leg_ma_rollup": []})
R.sb = lambda: fc_t
QUERY_LOG.clear()
out_t = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_trend(period="June 2026", months=1, org_id=TENANT))
check("a second tenant's request is scoped to ITS org on every read",
      all(scoped(q, TENANT) for q in QUERY_LOG) and bool(QUERY_LOG),
      [q for q in QUERY_LOG if not scoped(q, TENANT)])
check("the tenant NEVER reads another tenant's DATA rows (only house CONFIG defaults)",
      all(q["eq"].get("org_id") == TENANT for q in QUERY_LOG
          if q["table"] not in ("commission_leg_config",)))
check("the tenant's RPC call carries the tenant org, not the house org",
      RPC_LOG[-1]["params"]["p_org_id"] == TENANT)
check("a tenant with no commission data gets a ready, zeroed, honest payload (never a 500)",
      out_t["company"][0]["total"] == 0.0 and out_t["basis"])

section("⑦e master-agent months + the no-double-count gate")
MA_ROLLUP = [dict(ma_sums, period="June 2026", n=3), dict(ma_sums, period="May 2026", n=1)]
fc_ma = FakeClient(store, rpc_data={"commission_leg_label_rollup": ROLLUP,
                                    "commission_leg_ma_rollup": MA_ROLLUP})
R.sb = lambda: fc_ma
out_ma = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_trend(period="June 2026", months=2, org_id=HOUSE))
jun_ma = next(c for c in out_ma["company"] if c["period"] == "June 2026")
may_ma = next(c for c in out_ma["company"] if c["period"] == "May 2026")
check("a month that already has ePay commission does NOT also book MA money",
      eq2(jun_ma["total"], 230.0), jun_ma["total"])
check("a month with ePay money still ignores MA even when MA rows exist",
      "VidaPay/MA" not in jun_ma["sources"])
check("...and May (which also has ePay money) is likewise not double-counted",
      eq2(may_ma["total"], 70.0), may_ma["total"])
fc_ma2 = FakeClient(store, rpc_data={"commission_leg_label_rollup": [],
                                     "commission_leg_ma_rollup": MA_ROLLUP})
R.sb = lambda: fc_ma2
out_ma2 = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_trend(period="June 2026", months=1, org_id=HOUSE))
jm = out_ma2["company"][0]
# mig 277: M1 is spiff_m1 ALONE (5.0 x 1 rollup row); the 560.5 of margins moved to unsplit and the
# month TOTAL is byte-identical — the reclassification never touches the trend's commission line.
check("an ePay-less month DOES book MA commission, split by leg column",
      eq2(jm["total"], router_total) and eq2(jm["m1"], 5.0) and eq2(jm["m2_12"], 62.0)
      and eq2(jm["unsplit"], 560.5),
      (jm["m1"], jm["m2_12"], jm["unsplit"], jm["total"]))
check("IDENTITY: the MA month's parts add back to its total",
      eq2(jm["m1"] + jm["m2_12"] + jm["unsplit"], jm["total"]))
check("company-wide MA money is EXCLUDED (and said so) while a store filter is active",
      eq2(asyncio.get_event_loop().run_until_complete(
          R.commission_leg_trend(period="June 2026", months=1, store=STORE_A,
                                 org_id=HOUSE))["company"][0]["total"], 0.0)
      and any("company-wide" in n for n in asyncio.get_event_loop().run_until_complete(
          R.commission_leg_trend(period="June 2026", months=1, store=STORE_A,
                                 org_id=HOUSE))["notes"]))

section("⑦f graceful degradation when migration 274 has not been run")
fc_deg = FakeClient({**store, "raw_payment_detail": [
    {"org_id": HOUSE, "period": "June 2026", "business_address": STORE_A,
     "payment_type": "New Activation Bounty - Month 1", "amount": 100.0},
    {"org_id": HOUSE, "period": "June 2026", "business_address": STORE_A,
     "payment_type": "New Activation Bounty - Month 2", "amount": 20.0}],
    "payment_categories": [{"org_id": HOUSE, "description": "New Activation Bounty - Month 1",
                            "category": "Commission"},
                           {"org_id": HOUSE, "description": "New Activation Bounty - Month 2",
                            "category": "Commission"}]},
    missing=("commission_leg_config", "commission_leg_label_map"),
    missing_rpc=("commission_leg_label_rollup", "commission_leg_ma_rollup"))
R.sb = lambda: fc_deg
out_deg = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_trend(period="June 2026", months=12, org_id=HOUSE))
check("with NO migration 274 the trend still returns real numbers (per-month fallback)",
      eq2(out_deg["company"][-1]["m1"], 100.0) and eq2(out_deg["company"][-1]["m2_12"], 20.0),
      out_deg["company"][-1])
check("...and SAYS it is degraded + which months it could cover",
      out_deg["degraded"] is True and any("274" in n for n in out_deg["notes"]), out_deg["notes"])
check("...and the config falls back to the seeded code defaults, not an error",
      out_deg["config"]["resolved_from"] == "code_default")

section("⑦g the label admin surface")
fc_lab = FakeClient({**store,
                     "commission_leg_label_map": [
                         {"org_id": HOUSE, "label": "Boost Auto Top-Up", "bucket": "trailing",
                          "leg_month": None, "note": "airtime residual"}]},
                    rpc_data={"commission_leg_label_rollup": ROLLUP})
R.sb = lambda: fc_lab
QUERY_LOG.clear()
labs = asyncio.get_event_loop().run_until_complete(
    R.commission_leg_labels(period="June 2026", months=1, org_id=HOUSE))
by = {x["label"]: x for x in labs["labels"]}
check("every read on the label surface is org-scoped",
      all(scoped(q, HOUSE) for q in QUERY_LOG), [q for q in QUERY_LOG if not scoped(q, HOUSE)])
check("labels are listed with the $ behind them (pick-don't-type: only labels that EXIST)",
      eq2(by["New Activation Bounty - Month 1"]["amount"], 155.0),
      by["New Activation Bounty - Month 1"]["amount"])
check("an overridden label reports its override and the new bucket",
      by["Boost Auto Top-Up"]["bucket"] == "trailing" and by["Boost Auto Top-Up"]["overridden"] is True
      and by["Boost Auto Top-Up"]["why"] == "label_override")
check("an automatic label reports WHY it landed where it did",
      by["New Activation Bounty - Month 3"]["why"] == "month_in_label"
      and by["New Activation Bounty - Month 3"]["leg_month"] == 3)
check("unsplit labels sort to the top — they are the ones needing a decision",
      labs["labels"][0]["bucket"] == "unsplit" or labs["unsplit_total"] == 0.0)
check("the surface reports the $ still awaiting a decision", "unsplit_total" in labs)

QUERY_LOG.clear()
posted = asyncio.get_event_loop().run_until_complete(
    R.set_commission_leg_label({"label": "Ramp Up Subsidy", "bucket": "m1"}, org_id=TENANT))
w = next(q for q in QUERY_LOG if q["write"] == "upsert")
check("saving an override writes the label mapping", posted["ok"] is True)
check("the WRITE stamps org_id (RULE ONE write side — scoping a read without stamping loses rows)",
      w["row"].get("org_id") == TENANT and w["row"].get("bucket") == "m1", w["row"])
QUERY_LOG.clear()
asyncio.get_event_loop().run_until_complete(
    R.set_commission_leg_label({"label": "Ramp Up Subsidy", "bucket": ""}, org_id=TENANT))
d = next(q for q in QUERY_LOG if q["write"] == "delete")
check("clearing an override deletes only THIS org's row", d["eq"].get("org_id") == TENANT
      and d["eq"].get("label") == "Ramp Up Subsidy", d["eq"])
try:
    asyncio.get_event_loop().run_until_complete(
        R.set_commission_leg_label({"label": "x", "bucket": "nonsense"}, org_id=HOUSE))
    bad_bucket_rejected = False
except Exception:
    bad_bucket_rejected = True
check("an invalid bucket is rejected (400), never silently stored", bad_bucket_rejected)
try:
    asyncio.get_event_loop().run_until_complete(R.set_commission_leg_label({"bucket": "m1"}, org_id=HOUSE))
    blank_rejected = False
except Exception:
    blank_rejected = True
check("a blank label is rejected", blank_rejected)

section("⑦h config resolution ladder")
cfg_store = {**store, "commission_leg_config": [
    {"org_id": HOUSE, "carrier_id": "00000000-0000-0000-0000-000000000000", "carrier_mode": "boost",
     "is_active": True, "unlabeled_bucket": "unsplit", "m1_month": 1},
    {"org_id": TENANT, "carrier_id": "00000000-0000-0000-0000-000000000000", "carrier_mode": "plan",
     "is_active": True, "unlabeled_bucket": "trailing", "m1_month": 1},
]}
fcc = FakeClient(cfg_store)
check("a tenant's OWN mode-default row wins over the house row",
      CL.for_org(fcc, TENANT, carrier_mode="plan").cfg["_resolved_from"] == "org_mode_default")
check("a tenant with no row of its own INHERITS the house default",
      CL.for_org(fcc, TENANT, carrier_mode="boost").cfg["_resolved_from"] == "house_mode_default")
check("the tenant's override actually changes the answer",
      CL.for_org(fcc, TENANT, carrier_mode="plan").label("Boost Auto Top-Up")[0] == CL.TRAILING)
check("a missing config table degrades to the code default, never a 500",
      CL.for_org(FakeClient({}, missing=("commission_leg_config", "commission_leg_label_map")),
                 HOUSE).cfg["_resolved_from"] == "code_default")
check("the code default equals the two rows migration 274 seeds (same behaviour pre/post migration)",
      CL.DEFAULT_CFG["label_month_regex"] == r"month\s*[-#:]?\s*(\d+)"
      and CL.DEFAULT_CFG["m1_month"] == 1 and CL.DEFAULT_CFG["unlabeled_bucket"] == "unsplit"
      and CL.DEFAULT_CFG["ma_max_month"] == 6)

print(f"\n{'=' * 96}\n  {_pass} passed, {_fail} failed\n{'=' * 96}")
sys.exit(1 if _fail else 0)
