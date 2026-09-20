"""DB-FREE PROOF — the canonical commission ledger's SIGN CONVENTION, and a brand-new carrier
onboarding through the system we already have.

    python3 harness_commission_ledger_sign.py        (from backend/; no network, no DB, no pip)

THE SCENARIO THIS HARNESS IS WRITTEN AS (owner directive 2026-09-20: "we should be able to test this
as a new carrier to be able to work with the system we built"). A tenant uploads a commission
statement from a carrier the platform has never seen. Nobody edits the database. Everything the
tenant does, they do on the mapping screen and the Category Map screen:

    1. they map the file's columns          -> commcalc.column_mapping, incl. the AMOUNT column's
                                               sign convention (mig 1006)
    2. they import it with no rules yet      -> every line surfaces as unmapped, and NOTHING is
                                               silently classified with another carrier's patterns
    3. they add their own bucket rules       -> commcalc.commission_category_map, their own labels
    4. the five canonical buckets come out right, and tie to the statement's own total

THE TWO DEFECTS IT PINS (both reported 2026-09-20, both reproduced here before they are fixed):

  A. SILENT CROSS-TEMPLATE RULE FALLBACK. `load_rules` returned the built-in MA Daily Tx patterns for
     ANY template with no rows of its own — so a tenant-created rule-set with zero rules classified a
     different carrier's statement with a master agent's keywords and reported a confident wrong
     answer. Now the built-in defaults belong to the ONE template they mirror (the 071 seed), and any
     other template falls back to NOTHING and says so (`rules_source == 'none'`).

  B. THE SIGN CONVENTION WAS NOT DECLARABLE. `classify()` hard-coded `is_payout = amt < 0` and
     `build_row()` booked `abs(raw)`. On a statement written the other way up that books the EARNINGS
     as charges and the CHARGEBACKS as the payout. `sign_rule='any'` is NOT the fix and this harness
     proves it: under 'any' a matching rule takes abs() of a −$1,500 deactivation and books +$1,500
     of EARNED commission — on the fixture below that overstates by $14,792.54 and points the total
     the wrong way. The convention is now declared on the AMOUNT COLUMN'S MAPPING ROW, so direction is
     known BEFORE the magnitude is taken.

  Plus what the fixture exposed on the way: the statement's own GRAND-TOTAL row was ingested as a
  522nd data line. It is dropped by the EXISTING mig-1004 feed-shape rule (no second footer
  derivation), and the count is reported, never swallowed.

THE FIXTURE IS REAL AND MEASURED, not invented: the 522 stored lines of the reported import, as
(section, sub-section, amount, line-count) triples — 181,336.95 positive over 399 lines, −7,396.27
negative over 121, net 173,940.68, of which one row of 86,970.34 is the file's own total. No carrier
is named anywhere in this file, because no carrier is named anywhere in the code it proves.

NEGATIVE CONTROLS ARE PART OF THE RUN (§G): each defect is reintroduced in-process, the checks that
cover it are asserted to GO RED, and the fix is restored and re-verified. A harness that cannot fail
proves nothing.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import feed_shape as FS

MIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database", "migrations")
HOUSE = "00000000-0000-0000-0000-000000000001"

_pass = _fail = 0
_failures = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        _failures.append(name)
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def money(x):
    return round(float(x or 0.0), 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURE — the 522 stored lines of the reported import, as measured
# (report section, report sub-section, signed amount, how many lines carried exactly that amount).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STATEMENT = [
    ('', '', 86970.34, 1),
    ('Activations', 'DPP Receivable - Activation', 10.0, 4),
    ('Activations', 'DPP Receivable - Activation', 155.0, 1),
    ('Activations', 'DPP Receivable - Activation', 365.0, 1),
    ('Activations', 'DPP Receivable - Activation', 610.0, 2),
    ('Activations', 'DPP Receivable - Activation', 710.0, 1),
    ('Activations', 'DPP Receivable - Activation', 840.0, 4),
    ('Activations', 'DPP Receivable - Activation', 914.0, 1),
    ('Activations', 'DPP Receivable - Activation', 1010.0, 1),
    ('Activations', 'DPP Receivable - Activation', 1110.0, 2),
    ('Activations', 'DPP Receivable - Activation', 1410.0, 2),
    ('Activations', 'DPP Receivable - Deactivation', -10.0, 2),
    ('Activations', 'DPP Service Fee - Activation', -42.3, 2),
    ('Activations', 'DPP Service Fee - Activation', -33.3, 2),
    ('Activations', 'DPP Service Fee - Activation', -30.3, 1),
    ('Activations', 'DPP Service Fee - Activation', -27.42, 1),
    ('Activations', 'DPP Service Fee - Activation', -25.2, 4),
    ('Activations', 'DPP Service Fee - Activation', -21.3, 1),
    ('Activations', 'DPP Service Fee - Activation', -18.3, 2),
    ('Activations', 'DPP Service Fee - Activation', -10.95, 1),
    ('Activations', 'DPP Service Fee - Activation', -4.65, 1),
    ('Activations', 'DPP Service Fee - Activation', -0.3, 4),
    ('Activations', 'DPP Service Fee - Deactivation', 0.3, 2),
    ('Activations', 'Optional Service Activations', 5.0, 3),
    ('Activations', 'Optional Service Activations', 10.0, 5),
    ('Activations', 'Optional Service Activations', 15.0, 1),
    ('Activations', 'Optional Service Activations', 20.0, 27),
    ('Activations', 'Optional Service Activations', 25.0, 12),
    ('Activations', 'Optional Service Activations', 65.0, 23),
    ('Activations', 'Optional Service Activations', 75.0, 27),
    ('Activations', 'Optional Service Activations', 88.0, 1),
    ('Activations', 'Optional Service Activations', 840.0, 3),
    ('Activations', 'Optional Service Activations', 1110.0, 2),
    ('Activations', 'Optional Service Activations', 1410.0, 1),
    ('Activations', 'Optional Service Chargebacks', -75.0, 13),
    ('Activations', 'Optional Service Chargebacks', -65.0, 2),
    ('Activations', 'Optional Service Chargebacks', -5.0, 2),
    ('Activations', 'Price Plan Activations', 50.0, 1),
    ('Activations', 'Price Plan Activations', 65.0, 1),
    ('Activations', 'Price Plan Activations', 70.0, 1),
    ('Activations', 'Price Plan Activations', 80.0, 1),
    ('Activations', 'Price Plan Activations', 85.0, 1),
    ('Activations', 'Price Plan Activations', 90.0, 1),
    ('Activations', 'Price Plan Activations', 100.0, 3),
    ('Activations', 'Price Plan Activations', 130.0, 1),
    ('Activations', 'Price Plan Activations', 140.0, 4),
    ('Activations', 'Price Plan Activations', 150.0, 33),
    ('Activations', 'Price Plan Deactivations', -275.0, 1),
    ('Activations', 'Price Plan Deactivations', -150.0, 6),
    ('Activations', 'Price Plan Deactivations', -125.0, 1),
    ('Activations', 'Price Plan Deactivations', -65.0, 2),
    ('Activations', 'Price Plan Deactivations', -60.0, 1),
    ('Activations', 'Price Plan Deactivations', -10.0, 1),
    ('Adjustments', 'Charitable Contribution-Activation', -8.25, 1),
    ('Adjustments', 'Charitable Contribution-Upgrade', -12.75, 1),
    ('Adjustments', 'CoOp', 0.0, 2),
    ('FiOS', 'FiOS Commissions', 200.0, 5),
    ('FiOS', 'FiOS Commissions', 500.0, 1),
    ('Incentives', 'New Account', 74.0, 1),
    ('Incentives', 'New Account', 79.0, 1),
    ('Incentives', 'New Account', 170.0, 1),
    ('Incentives', 'New Account', 219.0, 18),
    ('Incentives', 'New Account Deactivations', -219.0, 2),
    ('Incentives', 'New Account Deactivations', -207.0, 1),
    ('Incentives', 'New Account Deactivations', -170.0, 1),
    ('Incentives', 'New Add-A-Line', 29.0, 5),
    ('Incentives', 'New Add-A-Line', 115.0, 1),
    ('Incentives', 'New Add-A-Line', 139.0, 7),
    ('Incentives', 'New Add-A-Line Deactivations', -139.0, 1),
    ('Incentives', 'New Add-A-Line Deactivations', -115.0, 1),
    ('Incentives', 'New Unlimited Plan', 25.0, 13),
    ('Incentives', 'New Unlimited Plan', 60.0, 5),
    ('Incentives', 'New Unlimited Plan Deactivations', -60.0, 2),
    ('Incentives', 'New Unlimited Plan Deactivations', -25.0, 3),
    ('Incentives', 'Upgrade Unlimited Plan', 10.0, 4),
    ('Incentives', 'Upgrade Unlimited Plan', 25.0, 25),
    ('Incentives', 'Upgrade Unlimited Plan', 50.0, 1),
    ('Incentives', 'Upgrade Unlimited Plan Deactivations', -25.0, 2),
    ('Trade', 'Trade Commission Amount', 1.5, 1),
    ('Trade', 'Trade Commission Amount', 3.53, 1),
    ('Trade', 'Trade Commission Amount', 4.5, 1),
    ('Trade', 'Trade Commission Amount', 7.05, 1),
    ('Trade', 'Trade Commission Amount', 8.63, 1),
    ('Trade', 'Trade Commission Amount', 9.0, 1),
    ('Trade', 'Trade Commission Amount', 9.75, 3),
    ('Trade', 'Trade Commission Amount', 13.13, 1),
    ('Trade', 'Trade Commission Amount', 13.5, 1),
    ('Trade', 'Trade Commission Amount', 15.0, 19),
    ('Upgrades', 'DPP Receivable - Upgrade', 10.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 138.0, 3),
    ('Upgrades', 'DPP Receivable - Upgrade', 287.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 477.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 610.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 830.0, 4),
    ('Upgrades', 'DPP Receivable - Upgrade', 840.0, 7),
    ('Upgrades', 'DPP Receivable - Upgrade', 914.0, 3),
    ('Upgrades', 'DPP Receivable - Upgrade', 999.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1000.0, 2),
    ('Upgrades', 'DPP Receivable - Upgrade', 1099.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1110.0, 2),
    ('Upgrades', 'DPP Receivable - Upgrade', 1202.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1210.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 1299.99, 2),
    ('Upgrades', 'DPP Receivable - Upgrade', 1310.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 1410.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1499.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1610.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1899.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 2599.98, 1),
    ('Upgrades', 'DPP Receivable - Upgrade Deact', -1299.99, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -78.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -50.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -48.3, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -45.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -42.3, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -39.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade', -39.0, 2),
    ('Upgrades', 'DPP Service Fee - Upgrade', -36.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade', -36.06, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -33.3, 2),
    ('Upgrades', 'DPP Service Fee - Upgrade', -33.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -30.0, 3),
    ('Upgrades', 'DPP Service Fee - Upgrade', -27.42, 3),
    ('Upgrades', 'DPP Service Fee - Upgrade', -25.2, 7),
    ('Upgrades', 'DPP Service Fee - Upgrade', -24.9, 4),
    ('Upgrades', 'DPP Service Fee - Upgrade', -18.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade', -14.31, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -8.61, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -4.14, 3),
    ('Upgrades', 'DPP Service Fee - Upgrade', -0.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade Deact', 39.0, 1),
    ('Upgrades', 'Phone Upgrade', 85.0, 5),
    ('Upgrades', 'Phone Upgrade', 125.0, 8),
    ('Upgrades', 'Phone Upgrade', 155.0, 39),
    ('Upgrades', 'Phone Upgrade', 310.0, 1),
    ('Upgrades', 'Phone Upgrade Deactivations', -155.0, 2),
]
# The file's own headers, as the importer saw them, and the mapping the tenant saved for them.
FILE_HEADERS = ["Gross", "Report Section", "Report SubSection", "AgentSSOID", "Master Service Date"]
MAPPED = {"raw_amount": "Gross", "product_name": "Report Section", "order_type": "Report SubSection",
          "rep_user": "AgentSSOID", "trans_date": "Master Service Date"}
TEMPLATE = "new_carrier_statement"        # a tenant-created rule-set namespace; the code never sees it


def statement_rows():
    """Every fixture line as a mapped source row — what column_mapping.apply_mapping yields."""
    out = []
    for pn, ot, amt, n in STATEMENT:
        for _ in range(n):
            out.append({"product_name": pn, "order_type": ot, "raw_amount": amt,
                        "rep_user": "" if not pn else "R1",
                        "trans_date": None if not pn else "2026-08-12"})
    return out


ROWS = statement_rows()
POS = money(sum(r["raw_amount"] for r in ROWS if r["raw_amount"] > 0))
NEG = money(sum(r["raw_amount"] for r in ROWS if r["raw_amount"] < 0))
FOOTER_AMOUNT = money([r["raw_amount"] for r in ROWS if not r["product_name"] and not r["order_type"]][0])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE FIXTURE IS THE REPORTED IMPORT")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("522 lines, as reported", len(ROWS) == 522, len(ROWS))
check("positive money 181,336.95 over 399 lines",
      POS == 181336.95 and sum(1 for r in ROWS if r["raw_amount"] > 0) == 399, POS)
check("negative money -7,396.27 over 121 lines",
      NEG == -7396.27 and sum(1 for r in ROWS if r["raw_amount"] < 0) == 121, NEG)
check("net 173,940.68", money(POS + NEG) == 173940.68, money(POS + NEG))
check("one line carries no identity at all and 86,970.34 of money",
      sum(1 for r in ROWS if not r["product_name"] and not r["order_type"]) == 1
      and FOOTER_AMOUNT == 86970.34, FOOTER_AMOUNT)
DETAIL = [r for r in ROWS if r["product_name"] or r["order_type"]]
DETAIL_NET = money(sum(r["raw_amount"] for r in DETAIL))
check("...and it is the file's OWN TOTAL: it equals the other 521 lines' net to the cent "
      "(94,366.61 earned less 7,396.27 charged back = 86,970.34)",
      DETAIL_NET == FOOTER_AMOUNT and len(DETAIL) == 521, (DETAIL_NET, FOOTER_AMOUNT))
check("...so counting it as data DOUBLES the statement (86,970.34 -> 173,940.68)",
      money(DETAIL_NET + FOOTER_AMOUNT) == 173940.68)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. NO CARRIER NAME IN THE CODE (RULE TWO)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The fix has to work for a carrier the code has never heard of. The way to prove that is that the
# code contains the name of none of them.
BANNED = ("verizon", "vzw", "fios", "agentssoid")
for mod in (CL, CM, FS):
    src = io.open(mod.__file__, encoding="utf-8").read().lower()
    hits = [b for b in BANNED if b in src]
    check(os.path.basename(mod.__file__) + " names no carrier", not hits, hits)
# router.py is the whole commcalc surface (28k+ lines) and carries carrier vocabulary in places that
# have nothing to do with this path, so the scan is scoped to THE LEDGER PATH'S OWN FUNCTIONS, read
# back off the live module with inspect — not a line range that would drift.
import inspect
from app.modules.commcalc import router as R
LEDGER_FUNCS = ["_ledger_convention", "_ledger_convention_for", "_ledger_footer_drop",
                "_ledger_source_rules", "commission_ledger_import", "commission_ledger_analyze",
                "commission_ledger_summary", "commission_ledger_observed_types",
                "commission_ledger_templates", "get_commission_category_map",
                "upsert_commission_category_map", "upsert_column_mapping", "column_mapping_targets",
                "_ledger_ma_derive", "_ledger_ma_payload"]
missing = [f for f in LEDGER_FUNCS if not hasattr(R, f)]
check("every ledger endpoint/helper this fix touches still exists by name", not missing, missing)
ledger_src = "\n".join(inspect.getsource(getattr(R, f)) for f in LEDGER_FUNCS if hasattr(R, f)).lower()
check("the ledger path in router.py names no carrier",
      not [b for b in BANNED if b in ledger_src], [b for b in BANNED if b in ledger_src])
check("...and branches on no template key either (no 'if source_report ==' in the money path)",
      "source_report ==" not in ledger_src and 'source_report=="' not in ledger_src)
MIG1006 = io.open(os.path.join(MIG_DIR, "1006_column_mapping_sign_convention.sql"), encoding="utf-8").read()
mig = MIG1006.lower()
check("migration 1006 names no carrier", not [b for b in BANNED if b in mig])
check("migration 1006 seeds no classification rule and declares no convention for anyone",
      "insert into" not in mig and "update commcalc.column_mapping" not in mig)
check("...and adds the column NULLABLE with no default, so every existing mapping is untouched",
      "add column if not exists sign_convention text;" in re.sub(r"\s+", " ", mig))
check("...and is reversible, with the REVERT stated",
      "-- revert:" in mig and "drop column if exists sign_convention" in mig)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. BYTE-IDENTITY — every shipped feed classifies exactly as it did before")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The pre-change contract, restated in full. Deliberately an INDEPENDENT reimplementation, so
# "identical" means identical to the documented old behaviour, not to whatever the module does now.
def legacy_classify(raw, ot, pn, rules):
    amt = CL._sf(raw)
    is_payout = amt < 0
    for pass_class in (True, False):
        for rule in rules:
            if (rule.get("match_op") == CL.CLASS_MATCH_OP) != pass_class:
                continue
            if rule.get("sign_rule") == "any" or is_payout:
                if CL._match(rule, ot, pn):
                    cat = rule.get("category") or "other"
                    return cat, (cat not in ("charge", "exclude"))
    return ("other", True) if is_payout else ("charge", False)


def legacy_build(src, base, rules):
    cat, pay = legacy_classify(src.get("raw_amount"), src.get("order_type"), src.get("product_name"), rules)
    raw = CL._sf(src.get("raw_amount"))
    row = dict(base)
    row.update({"source_report": base.get("source_report") or "ma_daily_tx",
                "account_id": src.get("account_id"), "account_name": src.get("account_name"),
                "store": src.get("store"), "rep_user": src.get("rep_user"),
                "order_number": src.get("order_number"), "order_type": src.get("order_type"),
                "product_name": src.get("product_name"), "trans_date": src.get("trans_date"),
                "due_date": src.get("due_date"),
                "payment_month": CL.parse_payment_month(src.get("product_name")),
                "category": cat, "raw_amount": round(raw, 2), "is_payout": pay,
                "payout_total": round(abs(raw), 2) if pay else 0.0,
                "commission": 0, "spiff": 0, "equipment_rebate": 0, "residual_monthly": 0,
                "autopay_residual": 0})
    if cat in CL.CATEGORIES:
        row[cat] = row["payout_total"]
    return row


def parse_seeded_rules(filename, template):
    """The rules a migration REALLY seeds, parsed out of the file — so this cannot pass against a
    rule-set that migration does not contain."""
    sql = io.open(os.path.join(MIG_DIR, filename), encoding="utf-8").read()
    pat = re.compile(r"\('" + re.escape(HOUSE) + r"','(?P<sr>[a-z_]+)','(?P<mf>\w+)','(?P<op>\w+)',"
                     r"'(?P<pat>[^']*)','(?P<cat>\w+)','(?P<sign>\w+)',(?P<pri>\d+)")
    out = []
    for m in pat.finditer(sql):
        if m.group("sr") != template:
            continue
        out.append({"match_field": m.group("mf"), "match_op": m.group("op"), "pattern": m.group("pat"),
                    "category": m.group("cat"), "sign_rule": m.group("sign"),
                    "priority": int(m.group("pri"))})
    return out


SEED_MA = parse_seeded_rules("071_commission_ledger.sql", "ma_daily_tx")
SEED_BOOST = parse_seeded_rules("072_boost_commission_template.sql", "boost")
check("the 071 migration really seeds 7 rules for its own template", len(SEED_MA) == 7, len(SEED_MA))
check("the 072 migration really seeds a POSITIVE-amount rule-set (140 rules, every one sign_rule='any')",
      len(SEED_BOOST) == 140 and set(r["sign_rule"] for r in SEED_BOOST) == {"any"},
      (len(SEED_BOOST), sorted(set(r["sign_rule"] for r in SEED_BOOST))))
check("DEFAULT_RULES mirror the 071 seed EXACTLY (field, op, pattern, category, sign, priority)",
      [(r["match_field"], r["match_op"], r["pattern"], r["category"], r["sign_rule"], r["priority"])
       for r in CL.default_rules_for("ma_daily_tx")]
      == [(r["match_field"], r["match_op"], r["pattern"], r["category"], r["sign_rule"], r["priority"])
          for r in SEED_MA])

CLASS_RULES = [{"match_field": "product_name", "match_op": CL.CLASS_MATCH_OP, "pattern": "spiff",
                "category": "spiff", "sign_rule": "negative_only", "priority": 5,
                "_class_index": {"Widget": "spiff"}}] + SEED_MA
LABELS = ["Commission Month 1", "SPF MONTH 4", "Trac Autopay Residual", "Postpaid Residual Order",
          "Subsidy", "Widget", "", None, "Boost Ready Bounty - Month 2", "$10 Network Change SPIFF",
          "ePay RTR Invoice Reimbursement", "2024 Q3 Promo Upgrade",
          "TBV MONTH 2 New Activation Commission"]
ORDER_TYPES = ["Promo Order", "Activation", "", None, "PostPaid Additional Spiff"]
AMOUNTS = [-1500.0, -0.01, 0.0, 0.01, 1500.0, -86970.34, 86970.34, None, ""]
SHIPPED = (("ma_daily_tx (071 seed)", SEED_MA), ("boost (072 seed, positive amounts)", SEED_BOOST),
           ("product_class wiring (mig 265)", CLASS_RULES))


def shipped_feed_diffs():
    """Every shipped rule-set, every line-variant: the current module vs the pre-change contract."""
    diffs, compared = [], 0
    for name, rl in SHIPPED:
        old_rows, new_rows = [], []
        for pn in LABELS:
            for ot in ORDER_TYPES:
                for amt in AMOUNTS:
                    compared += 1
                    src = {"product_name": pn, "order_type": ot, "raw_amount": amt}
                    base = {"org_id": HOUSE, "source_report": "t", "period": "Aug 2026"}
                    o, n = legacy_build(src, base, rl), CL.build_row(src, base, rl)
                    if o != n:
                        diffs.append((name, pn, ot, amt, o.get("category"), n.get("category"),
                                      o.get("payout_total"), n.get("payout_total")))
                    old_rows.append(o)
                    new_rows.append(n)
        if CL.summarize(old_rows, rules=rl) != CL.summarize(new_rows, rules=rl):
            diffs.append((name, "summarize() differs"))
    return diffs, compared


_d, _n = shipped_feed_diffs()
check("every shipped rule-set classifies, books and summarises identically to the pre-change "
      "contract (" + str(_n) + " line-variants x 3 rule-sets, no convention passed)", not _d, _d[:3])
check("...because the default convention IS the old behaviour",
      CL.DEFAULT_CONVENTION == {"payout_sign": -1, "reversal_handling": "charge"})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. STEP 1 — THE TENANT MAPS THE FILE, AND DECLARES WHAT THE AMOUNT MEANS")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
suggestions = CM.suggest(FILE_HEADERS, "commission_ledger")
by_field = {s["target_field"]: s for s in suggestions}
check("the wizard offers the ledger's canonical fields for this file",
      set(MAPPED) <= set(by_field), sorted(set(MAPPED) - set(by_field)))
check("the AMOUNT field is the one — and the only one — that carries a sign convention",
      [f for f, s in by_field.items() if "sign_convention" in s] == [CL.AMOUNT_FIELD],
      [f for f, s in by_field.items() if "sign_convention" in s])
check("...and it starts UNDECLARED, so nothing is assumed on the tenant's behalf",
      by_field[CL.AMOUNT_FIELD]["sign_convention"] == "")
# What the wizard could and could not guess from this file's headers — reported, not glossed over.
guessed = {f: by_field[f]["suggested_source"] for f in MAPPED if by_field.get(f)}
check("the wizard cannot guess a new carrier's own header names and does not pretend to "
      "(it proposes " + str(sum(1 for v in guessed.values() if v)) + " of " + str(len(MAPPED))
      + "; the rest the tenant picks from the file's headers, which the screen lists)",
      all(v == "" or v in FILE_HEADERS for v in guessed.values()), guessed)

# The mapping the tenant saves. These rows are exactly what POST /commcalc/column-mapping writes.
MAPPING_RULES = [{"target_field": tf, "source_header": h,
                  "transform": ("number" if tf == "raw_amount" else
                                "date10" if tf == "trans_date" else "text")}
                 for tf, h in MAPPED.items()]
DECLARED = [dict(r, sign_convention=CL.SIGN_PAYOUT_POSITIVE) if r["target_field"] == CL.AMOUNT_FIELD
            else dict(r) for r in MAPPING_RULES]

conv_undeclared, meta_undeclared = CL.convention_from_mapping(MAPPING_RULES)
conv, conv_meta = CL.convention_from_mapping(DECLARED)
check("an UNDECLARED mapping reads the long-standing way (negative = earned) — the safe default",
      conv_undeclared == {"payout_sign": -1, "reversal_handling": "charge"}
      and meta_undeclared["source"] == "default")
check("declaring 'a positive amount is money earned' on the amount column is what changes it",
      conv == {"payout_sign": 1, "reversal_handling": "signed"} and conv_meta["source"] == "mapping")
check("...and the declaration is reported with the column it was made on, not buried",
      conv_meta["amount_field"] == "raw_amount" and conv_meta["amount_header"] == "Gross"
      and conv_meta["is_default"] is False, conv_meta)
check("a garbled / stale declaration falls back to the default — it can never invent a third direction",
      CL.convention_from_mapping([dict(DECLARED[0], sign_convention="sideways")])[0]
      == CL.DEFAULT_CONVENTION)
check("a mapping with no amount column at all resolves to the default and says 'unmapped'",
      CL.convention_from_mapping([])[1]["source"] == "unmapped")
check("the vocabulary is exactly three NAMED conventions — a tenant states what the FILE does, "
      "not how the engine should behave (the third, negative-earned-with-netted-chargebacks, is the "
      "onboarding intake's 'earned is negative' answer; mig 1008)",
      tuple(CL.SIGN_CONVENTIONS) == ("payout_negative", "payout_positive", "payout_negative_netted")
      and set(CL.CONVENTIONS) == set(CL.SIGN_CONVENTIONS)
      and CL.CONVENTIONS["payout_negative_netted"] == {"payout_sign": -1, "reversal_handling": "signed"})
check("all are labelled in plain words for the wizard",
      all(CL.SIGN_CONVENTION_LABELS.get(v) for v in CL.SIGN_CONVENTIONS))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. STEP 2 — IMPORTED WITH NO RULES YET: nothing is borrowed, everything is surfaced")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class FakeQuery:
    def __init__(self, store, table, log):
        self.store, self.table, self.log, self.filters = store, table, log, {}

    def select(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def in_(self, k, vals):
        self.filters[k] = list(vals)
        return self

    def execute(self):
        self.log.append({"table": self.table, "filters": dict(self.filters)})
        rows = []
        for r in self.store.get(self.table, []):
            ok = True
            for k, v in self.filters.items():
                if isinstance(v, list):
                    ok = ok and r.get(k) in v
                else:
                    ok = ok and r.get(k) == v
            if ok:
                rows.append(dict(r))
        return type("Res", (), {"data": rows})()


class FakeClient:
    def __init__(self, store):
        self.store, self.log = store, []

    def schema(self, _s):
        return self

    def table(self, t):
        return FakeQuery(self.store, t, self.log)


TENANT = "00000000-0000-0000-0000-0000000000a2"
# The tenant's world on the day of the report: a statement imported under its own new template, and
# NOT ONE classification rule anywhere for it. The house org's own rule-sets exist, as they really do.
store = {"commission_category_map": (
    [dict(r, org_id=HOUSE, source_report="ma_daily_tx") for r in SEED_MA]
    + [dict(r, org_id=HOUSE, source_report="boost") for r in SEED_BOOST]),
    "commission_ledger": [{"org_id": TENANT, "source_report": TEMPLATE} for _ in range(522)]}
client = FakeClient(store)

rules, rules_source = CL.load_rules_meta(client, TENANT, TEMPLATE)
check("DEFECT A, FIXED: a tenant template with no rules of its own inherits NOTHING",
      rules == [] and rules_source == CL.RULES_NONE, (len(rules), rules_source))
check("...and it does NOT reach into another org's rule-sets either (every read is org-scoped)",
      all(q["filters"].get("org_id") == TENANT for q in client.log), client.log)
ma_rules, ma_source = CL.load_rules_meta(client, HOUSE, "ma_daily_tx")
check("...while the template the built-in defaults actually describe still falls back to them "
      "(pre-migration safety, unchanged)",
      CL.load_rules_meta(FakeClient({}), TENANT, "ma_daily_tx")[1] == CL.RULES_BUILTIN)
check("...and a tenant that HAS its own rules uses its own",
      ma_source == CL.RULES_TENANT and len(ma_rules) == 7, (ma_source, len(ma_rules)))
# WHICH TEMPLATES KEEP THE BUILT-IN FALLBACK — the decision, pinned so it cannot drift silently.
empty = FakeClient({})
check("the two SHIPPED MA templates keep the built-in defaults (their own label vocabulary, and the "
      "Daily Tx template's 071 seed mirrored) — unchanged",
      all(CL.load_rules_meta(empty, TENANT, k)[1] == CL.RULES_BUILTIN
          for k in ("ma_daily_tx", "ma_commission")))
check("the Boost template does NOT borrow them: it ships its own complete rule-set, and MA keywords "
      "are not its words",
      CL.load_rules_meta(empty, TENANT, "boost")[1] == CL.RULES_NONE)
check("...and that costs nothing today: MA's defaults are all payout-direction-only, so on a feed "
      "whose amounts are positive they matched nothing either way",
      all(r["sign_rule"] == "negative_only" for r in CL.default_rules_for("ma_daily_tx")))
check("every built-in template is either in the fallback map or deliberately out of it — no key is "
      "unaccounted for",
      set(CL.BUILTIN_TEMPLATES) == set(CL.DEFAULT_RULES_BY_TEMPLATE) | {"boost"},
      (sorted(CL.BUILTIN_TEMPLATES), sorted(CL.DEFAULT_RULES_BY_TEMPLATE)))
# THE CHICKEN-AND-EGG that stopped a new carrier being bucketed through the UI at all.
tmpls = {t["key"]: t for t in CL.list_templates(FakeClient(store), TENANT)}
check("a template the tenant's own LEDGER ROWS name is offered in the picker even before it has a "
      "single rule — otherwise its first rule could never be written on the page",
      TEMPLATE in tmpls and tmpls[TEMPLATE]["rule_count"] == 0
      and tmpls[TEMPLATE]["ledger_lines"] == 522, tmpls.get(TEMPLATE))
check("...and the built-in templates are still listed alongside it",
      all(k in tmpls for k in CL.BUILTIN_TEMPLATES))

# What the tenant sees on the screen before writing a single rule.
no_rule_rows = [CL.build_row(r, {"org_id": TENANT, "source_report": TEMPLATE}, rules, conv)
                for r in DETAIL]
s0 = CL.summarize(no_rule_rows, rules=rules, conv=conv)
ZERO_LINES = sum(1 for r in DETAIL if money(r["raw_amount"]) == 0.0)
check("every line that carries money shows as UNMAPPED ('other') — which is what the Category Map "
      "link on that screen is for — and not one dollar lands in a bucket",
      s0["other_count"] == 521 - ZERO_LINES
      and all(v["total"] == 0.0 for v in s0["categories"].values()),
      (s0["other_count"], ZERO_LINES, {k: v["total"] for k, v in s0["categories"].items()}))
check("...the " + str(ZERO_LINES) + " zero-amount lines are neither earned nor reversed, so they "
      "book nothing at all (a $0 line must never read as a payout)",
      ZERO_LINES == 2 and sum(1 for r in no_rule_rows if r["category"] == "charge") == ZERO_LINES)
check("...and the unmapped money is the statement's own net, not a number pointing the wrong way",
      s0["payout_total"] == 86970.34, s0["payout_total"])

# THE DEFECT AS REPORTED, reproduced: the same lines, the borrowed rules, the old hard-coded sign.
# All 522 stored lines, exactly as the owner measured them (the file's own total row included — it
# was ingested as data, which is the third finding).
borrowed = [legacy_build(r, {"org_id": TENANT, "source_report": TEMPLATE}, SEED_MA) for r in ROWS]
s_bug = CL.summarize(borrowed)
check("REPORTED DEFECT REPRODUCED: with another template's rules and the hard-coded sign, all five "
      "buckets read 0.00",
      all(v["total"] == 0.0 for v in s_bug["categories"].values()),
      {k: v["total"] for k, v in s_bug["categories"].items()})
check("...the 181,336.95 actually EARNED was booked as a charge ('not a payout')",
      s_bug["charge_total"] == 181336.95, s_bug["charge_total"])
check("...and the 7,396.27 of CHARGEBACKS was booked as the payout, over 121 lines — exactly "
      "backwards, and that is the number the Commission Ledger reported",
      s_bug["payout_total"] == 7396.27 and s_bug["other_count"] == 121
      and s_bug["line_count"] == 522,
      (s_bug["payout_total"], s_bug["other_count"], s_bug["line_count"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. STEP 3 — THE TENANT'S OWN RULES, THROUGH THE EDITOR, ON THEIR OWN LABELS")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Nine rows the tenant creates on the Category Map page from the labels their own statement shows.
# They are the tenant's DATA, not a seed: no migration writes them and no code knows them. Every one
# uses the editor's DEFAULT sign_rule, because under a declared convention the tenant never has to
# reason about signs — which is the point.
TENANT_RULES = [
    {"match_field": "order_type", "match_op": "contains", "pattern": "DPP",
     "category": "equipment_rebate", "sign_rule": "negative_only", "priority": 10},
    {"match_field": "order_type", "match_op": "contains", "pattern": "Optional Service",
     "category": "spiff", "sign_rule": "negative_only", "priority": 11},
    {"match_field": "order_type", "match_op": "contains", "pattern": "Price Plan",
     "category": "commission", "sign_rule": "negative_only", "priority": 12},
    {"match_field": "order_type", "match_op": "contains", "pattern": "Phone Upgrade",
     "category": "commission", "sign_rule": "negative_only", "priority": 13},
    {"match_field": "order_type", "match_op": "contains", "pattern": "FiOS",
     "category": "commission", "sign_rule": "negative_only", "priority": 14},
    {"match_field": "order_type", "match_op": "contains", "pattern": "Trade",
     "category": "equipment_rebate", "sign_rule": "negative_only", "priority": 15},
    {"match_field": "product_name", "match_op": "equals", "pattern": "Incentives",
     "category": "spiff", "sign_rule": "negative_only", "priority": 20},
    {"match_field": "product_name", "match_op": "equals", "pattern": "Adjustments",
     "category": "commission", "sign_rule": "negative_only", "priority": 21},
]
check("every rule the tenant writes is accepted by the editor's OWN vocabulary — nothing here needs "
      "a field, an operator or a sign mode the UI cannot offer",
      all(r["match_field"] in CL.MATCH_FIELDS and r["match_op"] in CL.MATCH_OPS
          and r["sign_rule"] in CL.SIGN_RULES and r["category"] in CL.CATEGORIES
          for r in TENANT_RULES))

store["commission_category_map"] += [dict(r, org_id=TENANT, source_report=TEMPLATE)
                                     for r in TENANT_RULES]
rules, rules_source = CL.load_rules_meta(FakeClient(store), TENANT, TEMPLATE)
check("the editor's rows are what the classifier then uses",
      rules_source == CL.RULES_TENANT and len(rules) == len(TENANT_RULES))

# THE IMPORT, as it now runs: the feed-shape footer rule first, then classification.
kept, dropped = CM.drop_footer_rows(list(ROWS), "commission_ledger", None, None, None,
                                    fields=CM.identity_fields("commission_ledger"))
check("the EXISTING feed-shape rule (mig 1004) drops the statement's own total row — one row, "
      "86,970.34 — and REPORTS it rather than swallowing it",
      dropped == 1 and len(kept) == 521
      and money(sum(r["raw_amount"] for r in ROWS) - sum(r["raw_amount"] for r in kept)) == 86970.34,
      (dropped, len(kept)))
check("...identified by SHAPE, not position: it is the only row with no identity at all",
      all(not FS.is_footer_row(r, CM.identity_fields("commission_ledger")) for r in kept))
check("...and a real line that merely lacks a label is NOT a total (the rule needs EVERY identity "
      "field blank)",
      not FS.is_footer_row({"order_type": "Phone Upgrade", "product_name": "", "raw_amount": -12.0},
                           CM.identity_fields("commission_ledger")))

FINAL = [CL.build_row(r, {"org_id": TENANT, "source_report": TEMPLATE, "period": "Aug 2026"},
                      rules, conv) for r in kept]
S = CL.summarize(FINAL, rules=rules, conv=conv)
buckets = {k: v["total"] for k, v in S["categories"].items()}
check("NOTHING is left unmapped once the tenant's own rules are in", S["other_count"] == 0, buckets)
check("THE TIE-OUT — the five canonical buckets sum to the statement's OWN grand total, 86,970.34. "
      "This holds whatever buckets the tenant chose, which is why it is the real proof",
      money(sum(buckets.values())) == 86970.34 and S["payout_total"] == 86970.34,
      (money(sum(buckets.values())), S["payout_total"]))
check("the buckets are: commission 13,829.00 | spiff 15,087.00 | equipment rebate 58,054.34 "
      "| residual 0.00 | auto-pay residual 0.00",
      buckets == {"commission": 13829.0, "spiff": 15087.0, "equipment_rebate": 58054.34,
                  "residual_monthly": 0.0, "autopay_residual": 0.0}, buckets)
check("every one of the 521 lines is in the payout stream; no earned line is filed as a 'charge'",
      S["line_count"] == 521 and S["charge_total"] == 0.0, (S["line_count"], S["charge_total"]))

# NO CLAWBACK IS EVER abs()-ED INTO EARNINGS.
rev = [r for r in FINAL if r["raw_amount"] < 0]
check("the 121 chargeback lines book NEGATIVE into the very bucket they reverse",
      len(rev) == 121 and all(r["payout_total"] < 0 for r in rev)
      and money(sum(r["payout_total"] for r in rev)) == -7396.27,
      (len(rev), money(sum(r["payout_total"] for r in rev))))
check("...and each one lands in a bucket, not in a limbo category",
      all(r["category"] in CL.CATEGORIES for r in rev))

# WHY sign_rule='any' IS NOT THE FIX — the same rules, the old direction-blind booking.
any_rules = [dict(r, sign_rule="any") for r in TENANT_RULES]
any_rows = [legacy_build(r, {"org_id": TENANT, "source_report": TEMPLATE}, any_rules) for r in kept]
S_any = CL.summarize(any_rows)
check("sign_rule='any' + abs() books the chargebacks as EARNINGS: 101,762.88 instead of 86,970.34",
      S_any["payout_total"] == 101762.88, S_any["payout_total"])
check("...an overstatement of 14,792.54 — exactly twice the chargebacks — pointing the total the "
      "wrong way; the convention is what fixes it, not the rule's sign mode",
      money(S_any["payout_total"] - S["payout_total"]) == 14792.54
      and money(2 * abs(NEG)) == 14792.54)

# The leg dimension still decomposes exactly, with netted reversals in play.
check("the 1st-month / M2-M12 leg split still sums back to each bucket and to the total",
      S["leg_identity_ok"] is True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. NEGATIVE CONTROLS — each defect put back, the checks watched to go red, then restored")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def rerun(fn):
    """Run a closure with the counters isolated; returns (passed, failed) of that closure only."""
    global _pass, _fail, _failures
    p0, f0, fl0 = _pass, _fail, list(_failures)
    out = io.StringIO()
    real, sys.stdout = sys.stdout, out
    try:
        fn()
    finally:
        sys.stdout = real
    p, f = _pass - p0, _fail - f0
    _pass, _fail, _failures = p0, f0, fl0
    return p, f


def probe_fallback():
    r, src = CL.load_rules_meta(FakeClient({}), TENANT, TEMPLATE)
    check("x", r == [] and src == CL.RULES_NONE)


def probe_direction():
    rows = [CL.build_row(r, {"org_id": TENANT, "source_report": TEMPLATE}, rules, conv) for r in kept]
    t = CL.summarize(rows, rules=rules, conv=conv)
    check("x", t["payout_total"] == 86970.34)


def probe_reversal():
    rows = [CL.build_row(r, {"org_id": TENANT, "source_report": TEMPLATE}, rules, conv) for r in kept]
    check("x", all(r["payout_total"] < 0 for r in rows if r["raw_amount"] < 0))


def probe_identity():
    d, _n = shipped_feed_diffs()
    check("x", not d)


check("baseline: all four probes are green before anything is broken",
      rerun(probe_fallback) == (1, 0) and rerun(probe_direction) == (1, 0)
      and rerun(probe_reversal) == (1, 0) and rerun(probe_identity) == (1, 0))

# 1. DEFECT A back: the built-in defaults claimed by every template again.
_keep_map = CL.DEFAULT_RULES_BY_TEMPLATE
CL.DEFAULT_RULES_BY_TEMPLATE = type("AnyKey", (dict,), {"get": lambda s, k, d=None: CL.DEFAULT_RULES})()
check("ARMED — restoring the cross-template fallback turns the 'inherits nothing' check RED",
      rerun(probe_fallback) == (0, 1))
CL.DEFAULT_RULES_BY_TEMPLATE = _keep_map
check("RESTORED — it is green again", rerun(probe_fallback) == (1, 0))

# 2. DEFECT B back, part one: direction hard-coded to 'negative is earned'.
_keep_dir = CL.direction
CL.direction = lambda raw, conv=None: (CL.DIR_PAYOUT if CL._sf(raw) < 0 else
                                       (CL.DIR_REVERSAL if CL._sf(raw) > 0 else CL.DIR_FLAT))
check("ARMED — hard-coding the sign again turns the statement's tie-out RED",
      rerun(probe_direction) == (0, 1))
CL.direction = _keep_dir
check("RESTORED — the tie-out is green again", rerun(probe_direction) == (1, 0))

# 3. DEFECT B back, part two: the magnitude taken with abs() regardless of direction.
_keep_book = CL.booked_amount
CL.booked_amount = lambda raw, booking: (round(abs(CL._sf(raw)), 2) if booking else 0.0)
check("ARMED — abs()-ing a clawback turns the 'chargebacks book negative' check RED",
      rerun(probe_reversal) == (0, 1))
check("...and it silently overstates the total by 14,792.54 while doing it",
      money(CL.summarize([CL.build_row(r, {"org_id": TENANT, "source_report": TEMPLATE}, rules, conv)
                          for r in kept], rules=rules, conv=conv)["payout_total"]) == 101762.88)
CL.booked_amount = _keep_book
check("RESTORED — clawbacks net again", rerun(probe_reversal) == (1, 0))

# 4. The byte-identity guard itself must be capable of failing.
_keep_conv = CL.DEFAULT_CONVENTION
CL.DEFAULT_CONVENTION = {"payout_sign": 1, "reversal_handling": "signed"}
check("ARMED — changing the DEFAULT convention turns the shipped-feed identity check RED "
      "(so 'byte-identical' is a measurement, not a claim)",
      rerun(probe_identity) == (0, 1))
CL.DEFAULT_CONVENTION = _keep_conv
check("RESTORED — every shipped feed is byte-identical again", rerun(probe_identity) == (1, 0))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. THE FIX IS WIRED — the import path really uses all of it")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A rule nothing calls proves nothing. These read the REAL endpoint bodies back off the module.
imp = inspect.getsource(R.commission_ledger_import)
ana = inspect.getsource(R.commission_ledger_analyze)
for nm, src in (("import", imp), ("analyze", ana)):
    check("POST /commission-ledger/" + nm + " reads the convention off the MAPPING it just loaded",
          "_ledger_convention(hdr_rules)" in src)
    check("POST /commission-ledger/" + nm + " passes it into every row it builds",
          "build_row(src, base, cat_rules, conv)" in src or
          "cat_rules, conv)" in src)
    check("POST /commission-ledger/" + nm + " drops the file's own total row through the shared "
          "feed-shape rule",
          "_ledger_footer_drop(" in src)
    check("POST /commission-ledger/" + nm + " reports what it dropped and which rules it used",
          "footer_rows_dropped" in src and "rules_source" in src)
check("the footer drop delegates to column_mapping (one footer rule in the codebase, not two)",
      "drop_footer_rows(" in inspect.getsource(R._ledger_footer_drop))
check("GET /commission-ledger/summary reports the convention it summarised under",
      "convention" in inspect.getsource(R.commission_ledger_summary))
check("the Category Map page is told when a report inherits no rules at all",
      "unclassified_note" in inspect.getsource(R.get_commission_category_map))
check("POST /column-mapping validates the declaration and only writes it on an amount column",
      all(t in inspect.getsource(R.upsert_column_mapping)
          for t in ("SIGN_CONVENTIONS", "_known_columns", 'transform != "number"')))


print("\n══ commission-ledger sign convention: %d passed, %d failed ══" % (_pass, _fail))
if _failures:
    print("   failed: " + "; ".join(_failures))
sys.exit(1 if _fail else 0)
