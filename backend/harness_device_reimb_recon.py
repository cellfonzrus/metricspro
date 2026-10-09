"""DB-FREE PROOF — what ePay PAID vs what the distributor CLAIMS it reimbursed, per store per month.

OWNER REPORT 2026-10-08, verbatim:
    "i just checked the commission details for boost, the commission is over stated as the device
     reimbursement is being added in the commision and also in device reimbursement
     example is 103 fulton street
       Commission (promo) $7,583.96
       Device-financing reimbursements (Distributor) $7,999.93
     the report which pays us is same as what is reported in commision 7583.96"
and, when asked what the difference between the two sides means:
    "distributors payments are not in additon to the reimbursement they are the same payments but
     the discrepancy nbetween them shows that the distributor claims it was paid bunt epay never
     paid it"

THE DEFECT CLASS, not the instance. The instance is one store-month at one tenant. The class is:

    **two feeds that are the SAME money were never reconciled, so nothing could tell "the
    distributor was paid and we were not" from "both numbers are fine".**

Measured live, read-only, house org, 2026-10-08 — his example reproduces to the cent. September
2026, store '103 Fulton Ave': the carrier statement classifies $7,583.96 under the key the owner
identified (three quarter-named promo types, $3,964.96 + $3,204.00 + $415.00), the distributor's
ledger claims $7,999.93 across 30 devices acquired as far back as 2025-12-23, and the gap is
$415.97.

AND THE REASON THAT GAP IS **NOT** FLAGGED AS "CLAIMED AND NEVER PAID" TODAY — the point of §C/§D.
The statement for September arrived missing 2026-09-30 (it is missing the final day of EVERY closed
month, $111,949.22 in total, §19.48). So the paid side is a FLOOR. Over the 15 days that did arrive
the statement averages $505.60 a day of device-financing money for this store, which is MORE than
the $415.97 shortfall — the gap and the missing day are indistinguishable from the data. Flagging
the store for a theft-shaped "the distributor says it was paid and ePay never paid it" on that
evidence would be a false accusation about a real person's store. It is reported NOT MEASURED, with
the missing day and the run-rate named, so a human re-pulls the statement instead of acting on it.

WHAT THIS HARNESS LOCKS:
  §A the two sides, and the classification/store/coverage facts being DEREFERENCED, never re-decided
  §B the owner's own numbers, reproduced through the shipped code
  §C AN ABSENCE IS NEVER A ZERO — every not-measured reason, and no $0.00 difference for any of them
  §D THE FALSE-ACCUSATION GUARD — the coverage rule is direction-asymmetric, and a floor only ever
     proves the direction it points
  §E the two directions are never netted into one signed total
  §F the flags: the existing registry, the existing table, the existing board — and which absences
     deliberately earn no flag
  §G RULE TWO and purity — no carrier, tenant, store, product or quarter name, no DB, no clock
  §H the un-wiring locks: the build fails if a caller stops dereferencing a shared fact, or if a
     second classification / coverage / store-resolution path appears
  §I controls — each guard is MUTATED and shown to be what produces the honest answer

Run:  cd backend && python3 harness_device_reimb_recon.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.modules.commcalc import device_reimb_recon as R
from app.modules.commcalc import flag_registry as FR

MOD = "app/modules/commcalc/device_reimb_recon.py"
ROUTER = "app/modules/commcalc/router.py"
REG = "app/modules/commcalc/flag_registry.py"
MIG = "../database/migrations/1063_device_reimb_recon_config.sql"
WORKFLOW = "../.github/workflows/carrier-vocab-guard.yml"
INDEX = "../docs/SYSTEM_DATA_FLOW_INDEX.md"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def eq(name, got, want):
    ok(name, got == want, f"\n     got:  {got!r}\n     want: {want!r}")


def _src(rel):
    return open(os.path.join(_HERE, rel)).read()


def _src_opt(rel):
    """The file's text, or "" when it does not exist — so a MISSING migration / workflow / index
    makes the check that needs it FAIL rather than making the whole lock raise. A lock that crashes
    reports nothing about the rest of the file."""
    try:
        return _src(rel)
    except OSError:
        return ""


def _code_only(src):
    """Source with comments and docstrings stripped, so a lock tests CODE not prose."""
    out = [ln.split("  #")[0] for ln in src.splitlines() if not ln.strip().startswith("#")]
    txt = "\n".join(out)
    try:
        tree = ast.parse(txt)
    except SyntaxError:
        return txt
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                txt = txt.replace(d, "")
    return txt


def _fn_src(rel, name):
    txt = _code_only(_src(rel))
    tree = ast.parse(txt)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(txt, node) or ""
    return ""


# ── the live shapes, as DATA. Nothing below spells a carrier, tenant or product name: these are
#    the neutral stand-ins a config row would carry, and §G proves the module never sees a real one.
STORE_A = "103 ANY ST"          # the owner's example store, anonymised
STORE_B = "200 OTHER AVE"
STORE_C = "300 THIRD RD"

#: the org's own classification rules, as the ONE classification home returns them
_CLASS = {
    "PROMO TYPE ONE": {"component": "COMMISSION", "subtype": "promo"},
    "PROMO TYPE TWO": {"component": "COMMISSION", "subtype": "promo"},
    "PROMO TYPE THREE": {"component": "COMMISSION", "subtype": "promo"},
    "SUBSIDY TYPE": {"component": "REIMBURSEMENT", "subtype": "subsidy"},
    "BOUNTY TYPE": {"component": "SPIFF", "subtype": "bounty"},
}


def classify(cat):
    return _CLASS.get(str(cat or "").strip(), {"component": None, "subtype": None})


#: the declaration under test: the key the owner's $7,583.96 falls under
DECLARED = {"carrier_sources": [{"component": "COMMISSION", "subtype": "promo"}]}


def carrier(store, period, cat, amount, day=None):
    return {"business_address": store, "period": period, "compensation_type": cat,
            "payment_amount": amount, "begin_date": day}


def device(store, rdate, amount, imei="", model="", acquired=""):
    return {"store": store, "reimbursement_date": rdate, "reimbursement": amount,
            "category": "Sold and Reimbursed", "status": "Paid In Full",
            "esn_imei": imei, "device_model": model, "acquired_date": acquired}


def run(carrier_rows, device_rows, cfg_raw=DECLARED, coverage=None):
    cfg = R.normalize_config(cfg_raw)
    c = R.carrier_side(carrier_rows, cfg, classify)
    d = R.distributor_side(device_rows, cfg)
    return cfg, R.reconcile(c, d, cfg, coverage)


def rowof(res, store, month):
    for r in res["rows"]:
        if r["store"] == store and r["month"] == month:
            return r
    return None


COMPLETE = {"2026-09": {"complete": True, "missing_from_statement": []}}
SHORT = {"2026-09": {"complete": False, "missing_from_statement": ["2026-09-30"]}}

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A THE TWO SIDES, AND THE FACTS THEY DEREFERENCE RATHER THAN RE-DECIDE")
# ══════════════════════════════════════════════════════════════════════════════════════════════
_cfg, _res = run(
    [carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 3964.96, "2026-09-03"),
     carrier(STORE_A, "September 2026", "PROMO TYPE TWO", 3204.00, "2026-09-10"),
     carrier(STORE_A, "September 2026", "PROMO TYPE THREE", 415.00, "2026-09-18"),
     carrier(STORE_A, "September 2026", "SUBSIDY TYPE", 5000.00, "2026-09-04"),
     carrier(STORE_A, "September 2026", "BOUNTY TYPE", 337.80, "2026-09-05")],
    [device(STORE_A, "2026-09-14", 7999.93, "35100000000001", "MODEL X", "2025-12-23")],
    coverage=COMPLETE)
_r = rowof(_res, STORE_A, "2026-09")
eq("A1 the carrier side counts ONLY the declared classification key", _r["carrier_paid"], 7583.96)
eq("A2 ... and the rest of the statement is reported as NOT claimed, by classification key",
   _r["carrier_not_claimed_total"], 5337.80)
eq("A3 ... broken down so the config can be corrected from the report",
   _r["carrier_not_claimed_by_class"],
   {"REIMBURSEMENT/subsidy": 5000.00, "SPIFF/bounty": 337.80})
eq("A4 the distributor side is the claim, device-counted", _r["distributor_claimed"], 7999.93)
eq("A5 ... with the devices that make it up as evidence", len(_r["device_sample"]), 1)
eq("A6 ... carrying the identifier, model and acquired date a human needs",
   (_r["device_sample"][0]["device_id"], _r["device_sample"][0]["model"],
    _r["device_sample"][0]["acquired"]), ("35100000000001", "MODEL X", "2025-12-23"))
ok("A7 the module NEVER classifies a carrier dollar itself — `classify` is injected",
   "classify" in _fn_src(MOD, "carrier_side").split("(")[1].split(")")[0])
ok("A8 ... and no classification vocabulary is spelled in the module",
   not any(w in _code_only(_src(MOD)) for w in ("carrier_category_map", "match_rule(", "re.search")))
ok("A9 the store key is INJECTED too — no second store-resolution chain here",
   "resolve_store" in _fn_src(MOD, "carrier_side")
   and "store_mapping" not in _code_only(_src(MOD))
   and "store_aliases" not in _code_only(_src(MOD)))
ok("A10 the coverage verdict is INJECTED — this module runs no coverage test of its own and knows "
   "nothing about calendars",
   "coverage" in _fn_src(MOD, "reconcile")
   and not any(w in _code_only(_src(MOD))
               for w in ("day_coverage_gap", "month_days", "month_state", "calendar", "monthrange")))
eq("A11 the paid side is honest about having NO device grain, so nobody is invited to click through",
   _r["carrier_has_device_grain"], False)
eq("A12 a month spelling is read from either feed's own form",
   (R.month_key("September 2026"), R.month_key("2026-09-14"), R.month_key("09/14/2026"),
    R.month_key("9/14/26"), R.month_key("")),
   ("2026-09", "2026-09", "2026-09", "2026-09", None))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B THE OWNER'S OWN NUMBERS, THROUGH THE SHIPPED CODE")
# ══════════════════════════════════════════════════════════════════════════════════════════════
eq("B1 claimed minus paid is the gap he would see", _r["difference"], 415.97)
eq("B2 the direction is the one he named: claimed, and never paid",
   _r["direction"], R.DIRECTION_CLAIMED_NOT_PAID)
eq("B3 with the month's coverage complete it is a MEASURED fact", _r["verdict"], R.VERDICT_MEASURED)
eq("B4 and it is the only store-month in this reconciliation", _res["totals"]["store_months"], 1)
eq("B5 the confirmed exception total is that gap and nothing else",
   (_res["totals"]["claimed_not_paid_store_months"], _res["totals"]["claimed_not_paid_total"]),
   (1, 415.97))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C AN ABSENCE IS NEVER A ZERO (§19.48/§19.49/§19.50/§19.51, fourth precedent)")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. nothing declared: there is no comparison to make, and it is not a $0.00 paid side.
_c0, _r0 = run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03")],
               [device(STORE_A, "2026-09-14", 7999.93)], cfg_raw={}, coverage=COMPLETE)
_x = rowof(_r0, STORE_A, "2026-09")
eq("C1 with no device-financing key declared the verdict is not-measured",
   (_x["verdict"], _x["reason"]), (R.VERDICT_NOT_MEASURED, R.REASON_NO_RULE))
eq("C2 ... and the difference is None, never 0.00", _x["difference"], None)
eq("C3 ... and the result says so at the top, because a configuration fact is org-level",
   _r0["configured"], False)
# 2. the carrier statement never arrived for this store-month.
_c1, _r1 = run([], [device(STORE_B, "2026-09-02", 1200.00)], coverage=COMPLETE)
_x = rowof(_r1, STORE_B, "2026-09")
eq("C4 a claim with no statement at all is not-measured, not a full shortfall",
   (_x["verdict"], _x["reason"], _x["difference"]),
   (R.VERDICT_NOT_MEASURED, R.REASON_CARRIER_ABSENT, None))
# 3. the statement arrived, but none of it is classified as device financing.
_c2, _r2 = run([carrier(STORE_C, "September 2026", "BOUNTY TYPE", 900.00, "2026-09-07")],
               [device(STORE_C, "2026-09-07", 1200.00)], coverage=COMPLETE)
_x = rowof(_r2, STORE_C, "2026-09")
eq("C5 a statement with no device-financing money is an UNKNOWN paid side, not a zero one",
   (_x["verdict"], _x["reason"], _x["difference"]),
   (R.VERDICT_NOT_MEASURED, R.REASON_CARRIER_UNCLASSIFIED, None))
eq("C6 ... and it carries what IS there, so the config can be fixed from the report",
   (_x["carrier_not_claimed_total"], _x["carrier_not_claimed_by_class"]),
   (900.00, {"SPIFF/bounty": 900.00}))
# 4. nobody claimed anything.
_c3, _r3 = run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 500.00, "2026-09-07")],
               [], coverage=COMPLETE)
_x = rowof(_r3, STORE_A, "2026-09")
eq("C7 a store-month with no claim is not-measured with its own reason, and never a difference",
   (_x["verdict"], _x["reason"], _x["difference"]),
   (R.VERDICT_NOT_MEASURED, R.REASON_DISTRIBUTOR_ABSENT, None))
ok("C8 every reason a reader can meet has a sentence a human can act on",
   all(R.REASON_LABELS.get(x) for x in R.REASONS))
ok("C9 no reason label leaks a table, column or hosting name (index §19.38)",
   not any(w in " ".join(R.REASON_LABELS.values()).lower()
           for w in ("raw_comp", "asset_ledger", "supabase", "postgres", "jsonb", "org_id")))
eq("C10 not-measured store-months are counted BY REASON, so an absence can be worked through",
   {k: v for k, v in R.reconcile(
       R.carrier_side([], R.normalize_config(DECLARED), classify),
       R.distributor_side([device(STORE_B, "2026-09-02", 1200.00),
                           device(STORE_C, "2026-09-02", 50.00)],
                          R.normalize_config(DECLARED)),
       R.normalize_config(DECLARED), COMPLETE)["totals"]["not_measured_by_reason"].items()},
   {R.REASON_CARRIER_ABSENT: 2})

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D THE FALSE-ACCUSATION GUARD — a floor proves only the direction it points")
# ══════════════════════════════════════════════════════════════════════════════════════════════
_c4, _r4 = run(
    [carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03")],
    [device(STORE_A, "2026-09-14", 7999.93)], coverage=SHORT)
_x = rowof(_r4, STORE_A, "2026-09")
eq("D1 claimed above a SHORT month's statement is WITHHELD, not accused",
   (_x["verdict"], _x["reason"]), (R.VERDICT_NOT_MEASURED, R.REASON_COVERAGE_INCOMPLETE))
eq("D2 ... while still carrying both figures and the gap, so nothing is hidden from the reader",
   (_x["carrier_paid"], _x["distributor_claimed"], _x["difference"]), (7583.96, 7999.93, 415.97))
eq("D3 ... and naming the days that never arrived", _x["carrier_missing_days"], ["2026-09-30"])
eq("D4 ... with the store's own per-day run-rate over the days that DID arrive, as context",
   _x["carrier_per_day"], 7583.96)
eq("D5 the withheld dollars are counted separately, so the size of the silence is visible",
   (_r4["totals"]["unconfirmed_claimed_not_paid_store_months"],
    _r4["totals"]["unconfirmed_claimed_not_paid_total"]), (1, 415.97))
eq("D6 ... and it is NOT counted as a confirmed exception",
   (_r4["totals"]["claimed_not_paid_store_months"], _r4["totals"]["claimed_not_paid_total"]),
   (0, 0.0))
# the other direction: a floor ABOVE the claim proves itself, short month or not.
_c5, _r5 = run([carrier(STORE_B, "September 2026", "PROMO TYPE ONE", 9000.00, "2026-09-03")],
               [device(STORE_B, "2026-09-14", 7999.93)], coverage=SHORT)
_x = rowof(_r5, STORE_B, "2026-09")
eq("D7 paid ABOVE the claim on a short month is proven — a floor above a number stays above it",
   (_x["verdict"], _x["direction"], _x["difference"]),
   (R.VERDICT_MEASURED_FLOOR, R.DIRECTION_PAID_ABOVE_CLAIM, 1000.07))
# an UNTESTED month is not a tested one (§19.49).
_c6, _r6 = run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03")],
               [device(STORE_A, "2026-09-14", 7999.93)], coverage=None)
eq("D8 a month with NO coverage verdict is treated exactly like an incomplete one",
   (rowof(_r6, STORE_A, "2026-09")["verdict"], rowof(_r6, STORE_A, "2026-09")["reason"]),
   (R.VERDICT_NOT_MEASURED, R.REASON_COVERAGE_INCOMPLETE))
eq("D9 a coverage dict that claims complete while listing a missing day is not believed",
   rowof(run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03")],
             [device(STORE_A, "2026-09-14", 7999.93)],
             coverage={"2026-09": {"complete": True,
                                   "missing_from_statement": ["2026-09-30"]}})[1],
         STORE_A, "2026-09")["reason"], R.REASON_COVERAGE_INCOMPLETE)
# The coverage home's OWN answer shape (§19.54), read through the one helper.
HOME_SHORT = {"2026-09": {"verdict": "failed", "ok": False, "missing_days": ["2026-09-30"],
                          "missing_amount": 23050.60,
                          "gap": {"complete": False, "missing_from_statement": ["2026-09-30"]}}}
HOME_OK = {"2026-09": {"verdict": "passed", "ok": True, "missing_days": [], "missing_amount": 0.0}}
HOME_UNMEASURED = {"2026-09": {"verdict": "not_measured", "ok": None, "missing_days": [],
                               "missing_amount": 0.0}}
eq("D11 the coverage home's own verdict is read — `ok` decides, and a named missing day is carried",
   R.coverage_verdict(HOME_SHORT["2026-09"]), (False, ["2026-09-30"], 23050.60))
eq("D12 ... `ok is None` is NOT complete: a month nobody measured is never a pass (§19.49)",
   R.coverage_verdict(HOME_UNMEASURED["2026-09"])[0], False)
eq("D13 ... and a complete month is complete, with nothing missing",
   R.coverage_verdict(HOME_OK["2026-09"]), (True, [], 0.0))
eq("D14 ... a flag claiming complete while naming a missing day is not believed",
   R.coverage_verdict({"ok": True, "missing_days": ["2026-09-30"]})[0], False)
eq("D15 ... an unpriceable shortfall reports None, never $0.00",
   R.coverage_verdict({"ok": False, "missing_days": ["2026-09-30"]})[2], None)
eq("D16 ... and the older two-feed answer is still read, so no caller is forced to fabricate",
   R.coverage_verdict({"complete": False, "missing_from_statement": ["2026-09-30"]}),
   (False, ["2026-09-30"], None))
_c10, _r10 = run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03")],
                 [device(STORE_A, "2026-09-14", 7999.93)], coverage=HOME_SHORT)
_x = rowof(_r10, STORE_A, "2026-09")
eq("D17 a withheld shortfall carries what the missing days were WORTH, measured not estimated",
   (_x["verdict"], _x["reason"], _x["carrier_missing_amount"]),
   (R.VERDICT_NOT_MEASURED, R.REASON_COVERAGE_INCOMPLETE, 23050.60))
ok("D18 ... and says so in the words a manager reads",
   "$23,050.60" in R.recon_flags(_r10, _c10, period_label="September 2026")[0]["description"])

eq("D10 agreement within tolerance on a complete month is MEASURED and reports a real 0.00",
   [(r["verdict"], r["difference"], r["direction"]) for r in run(
       [carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 1000.00, "2026-09-03")],
       [device(STORE_A, "2026-09-14", 1000.00)], coverage=COMPLETE)[1]["rows"]],
   [(R.VERDICT_MEASURED, 0.0, R.DIRECTION_AGREED)])

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E THE TWO DIRECTIONS ARE NEVER NETTED INTO ONE SIGNED TOTAL")
# ══════════════════════════════════════════════════════════════════════════════════════════════
_c7, _r7 = run(
    [carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03"),
     carrier(STORE_B, "September 2026", "PROMO TYPE ONE", 9000.00, "2026-09-03")],
    [device(STORE_A, "2026-09-14", 7999.93), device(STORE_B, "2026-09-14", 7999.93)],
    coverage=COMPLETE)
_t = _r7["totals"]
eq("E1 the exception the owner asked for has its own count and total",
   (_t["claimed_not_paid_store_months"], _t["claimed_not_paid_total"]), (1, 415.97))
eq("E2 money received that nobody claimed has its OWN bucket — a different fact",
   (_t["paid_above_claim_store_months"], _t["paid_above_claim_total"]), (1, 1000.07))
ok("E3 one store's overpayment can never cancel another's shortfall",
   _t["claimed_not_paid_total"] > 0 and _t["paid_above_claim_total"] > 0
   and "net" not in _t)
eq("E4 every store-month lands in exactly one verdict bucket",
   _t["measured"] + _t["measured_floor"] + _t["not_measured"], _t["store_months"])
eq("E5 ... and every measured one in exactly one direction bucket",
   _t["claimed_not_paid_store_months"] + _t["paid_above_claim_store_months"]
   + _t["agreed_store_months"], _t["measured"] + _t["measured_floor"])
eq("E6 a row that cannot be placed in a store-month is counted and PRICED, never absorbed",
   (R.carrier_side([carrier("", "", "PROMO TYPE ONE", 42.00)],
                   R.normalize_config(DECLARED), classify)["unplaced"],
    R.distributor_side([device("", "", 13.00)], R.normalize_config(DECLARED))["unplaced"]),
   ({"rows": 1, "amount": 42.0}, {"rows": 1, "amount": 13.0}))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F THE FLAGS — the existing registry, the existing table, the existing board")
# ══════════════════════════════════════════════════════════════════════════════════════════════
_f7 = R.recon_flags(_r7, _c7, period_label="September 2026")
eq("F1 a confirmed shortfall and nothing else earns the exception flag",
   sorted({f["flag_type"] for f in _f7}), [R.FLAG_CLAIMED_NOT_PAID])
eq("F2 ... one per store-month, keyed on the store-month and not on a dollar figure",
   [f["source_ref"] for f in _f7], ["2026-09|" + STORE_A])
ok("F3 ... and the description says these are the same money, not two payments",
   "same money" in _f7[0]["description"] and "never received" in _f7[0]["description"])
ok("F4 paid-above-claim is reported but NOT flagged — it asks nobody to act",
   not any(f["store_address"] == STORE_B for f in _f7))
_f4 = R.recon_flags(_r4, _c4, period_label="September 2026")
eq("F5 a WITHHELD shortfall is flagged as something to settle, never as a shortfall finding",
   [f["flag_type"] for f in _f4], [R.FLAG_NOT_MEASURED])
ok("F6 ... and its words send the reader to the feed, with the missing day and the run-rate",
   "missing 1 day" in _f4[0]["description"] and "re-pulled" in _f4[0]["description"]
   and "NOT MEASURED" in _f4[0]["description"])
eq("F7 an unconfigured org gets NO per-store flags — a configuration fact is not a store's fault",
   R.recon_flags(_r0, _c0), [])
eq("F8 a store-month nobody claimed anything about earns no flag either",
   R.recon_flags(_r3, _c3), [])
eq("F9 the flagged absences are exactly the three that withhold judgement on a real claim",
   list(R.FLAGGED_REASONS),
   [R.REASON_CARRIER_ABSENT, R.REASON_CARRIER_UNCLASSIFIED, R.REASON_COVERAGE_INCOMPLETE])
for _t2 in (R.FLAG_CLAIMED_NOT_PAID, R.FLAG_NOT_MEASURED):
    ok(f"F10 {_t2} is REGISTERED in the one flag registry (§53)", FR.canon_type(_t2) == _t2)
    ok(f"F11 {_t2} declares its review area and its grain",
       FR.area_of(_t2) in FR.AREA_KEYS and FR.grain_of(_t2) == "store_period")
    # `.get` rather than `[...]`: an UNREGISTERED type must make this check FAIL, not make the
    # harness crash — a lock that raises reports nothing about the rest of the file.
    ok(f"F12 {_t2} names this module as its writer, so a reader can find the rule",
       FR.TYPES.get(_t2, {}).get("writer") == "commcalc/device_reimb_recon.py")
ok("F13 the two types are reviewed in DIFFERENT areas: a distributor finding and a feed finding",
   FR.area_of(R.FLAG_CLAIMED_NOT_PAID) != FR.area_of(R.FLAG_NOT_MEASURED))
ok("F14 severity is a config threshold, never a constant in a branch",
   (R.severity_for(3000, _c7), R.severity_for(600, _c7), R.severity_for(10, _c7))
   == ("CRITICAL", "HIGH", "MEDIUM")
   and R.severity_for(3000, R.normalize_config({"severity_critical_at": 99999,
                                                "severity_high_at": 88888})) == "MEDIUM")
ok("F15 every flag severity canonicalises onto the registry's ONE scale",
   all(FR.canon_sev(f["severity"]) in FR.SEVERITIES for f in _f7 + _f4))
ok("F16 a flag row carries no org_id and no flag_key — identity belongs to flag_persist",
   not any(k in f for f in _f7 + _f4 for k in ("org_id", "flag_key")))
_rt = _code_only(_src(ROUTER))
ok("F17 the writer goes through the ADDITIVE merge, so a manager's review survives a re-read",
   "flag_persist.sync" in _fn_src(ROUTER, "device_reimbursement_recon_sync_flags"))
ok("F18 ... and stamps the registry before writing (the write-side dereference)",
   "flag_registry.stamp" in _fn_src(ROUTER, "device_reimbursement_recon_sync_flags"))
ok("F19 ... and routes each finding to a district manager through the existing resolver",
   "flag_store_resolver" in _fn_src(ROUTER, "device_reimbursement_recon_sync_flags"))
ok("F20 NO sibling flag table, page, queue or tile is created — the board already exists",
   not any(w in _fn_src(ROUTER, "device_reimbursement_recon_sync_flags")
           for w in ("ui_label_override", "create table", "CREATE TABLE")))
ok("F21 a pre-migration database is REPORTED, never quietly churned with a delete-first write",
   "delete()" not in _fn_src(ROUTER, "device_reimbursement_recon_sync_flags")
   and "write_error" in _fn_src(ROUTER, "device_reimbursement_recon_sync_flags"))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§G RULE TWO AND PURITY")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# over the CODE, not the docstring: the owner's own words are quoted verbatim up top, and a
# docstring is not a screen (§19.38 is about what a reader sees; §C9 covers the reader-facing text).
_m = _code_only(_src(MOD)).lower()
for word in ("boost", "luxelink", "cellfonz", "verizon", "vidapay", "epay", "vip wireless",
             "fulton", "q3", "promo pic", "acima", "t-mobile", "att", "total wireless"):
    ok(f"G1 no '{word}' anywhere in the module — carrier/tenant/product names are DATA", word not in _m)
_mc = _code_only(_src(MOD))
ok("G2 no quarter-named or product-named literal in the CODE",
   not any(w in _mc.lower() for w in ("2026 q", "promo ", "upgrade", "offer", "subsidy", "sold and")))
ok("G3 the module imports nothing but __future__ — DB-free, framework-free, clock-free",
   [ln for ln in _mc.splitlines() if ln.startswith(("import ", "from "))]
   == ["from __future__ import annotations"])
ok("G4 no client, no execute, no now() anywhere in the module",
   not any(w in _mc for w in ("client", "execute(", "datetime", "now()", "schema(")))
eq("G5 nothing is declared by default — an unconfigured org measures nothing and flags nobody",
   R.CODE_DEFAULT["carrier_sources"], [])
eq("G6 column names are config too, so a tenant whose feed lands them elsewhere re-points them",
   sorted(R.CODE_DEFAULT["columns"]) == sorted(
       R.normalize_config({"columns": {"carrier_amount": "amt"}})["columns"])
   and R.normalize_config({"columns": {"carrier_amount": "amt"}})["columns"]["carrier_amount"],
   "amt")
eq("G7 a config typo can only make this report LESS — it can never widen the declared side",
   R.normalize_config({"carrier_sources": "not-a-list"})["carrier_sources"], [])
eq("G8 ... nor turn an unconfigured org into one that flags stores",
   R.normalize_config({"carrier_sources": [{"component": ""}]})["carrier_sources"], [])
eq("G9 a declared component with no subtype matches any subtype of it",
   (R.carrier_source_matcher(R.normalize_config(
       {"carrier_sources": [{"component": "COMMISSION"}]}))({"component": "COMMISSION",
                                                             "subtype": "anything"}),
    R.carrier_source_matcher(R.normalize_config(DECLARED))({"component": "COMMISSION",
                                                            "subtype": "other"})),
   (True, False))
eq("G10 an empty distributor category/status config is the honest SUPERSET, not an empty result",
   (R.distributor_claim_matcher(R.normalize_config(DECLARED))(
       {"reimbursement": 5, "category": "anything", "status": "anything"}),
    R.distributor_claim_matcher(R.normalize_config(
        {"distributor_categories": ["one spelling"]}))(
       {"reimbursement": 5, "category": "another", "status": ""})),
   (True, False))
eq("G11 the house defaults inherit and a tenant row overrides them key by key",
   (R.config_from_rows({"tolerance": 0.5}, {"severity_high_at": 10.0})["tolerance"],
    R.config_from_rows({"tolerance": 0.5}, {"severity_high_at": 10.0})["severity_high_at"]),
   (0.5, 10.0))
# Counted rather than eyeballed: EVERY `.table(...)` in the reader carries an org filter, so a read
# added later without one changes the balance and fails this.
_inp_g = _fn_src(ROUTER, "_device_reimb_recon_inputs")
ok("G12 the reconciliation is org-scoped at every read — one org filter per table touched",
   _inp_g.count(".table(") >= 3 and _inp_g.count(".table(") == _inp_g.count('.eq("org_id"'))
ok("G13 the migration is additive, idempotent, reversible and declares no money decision",
   all(w in _src_opt(MIG) for w in ("ADD COLUMN IF NOT EXISTS", "-- REVERT:", "IS NULL",
                                '"carrier_sources": []'))
   and "RAISE EXCEPTION" in _src_opt(MIG))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§H THE UN-WIRING LOCKS — the build fails if a caller stops dereferencing a shared fact")
# ══════════════════════════════════════════════════════════════════════════════════════════════
_inp = _fn_src(ROUTER, "_device_reimb_recon_inputs")
ok("H1 the classification comes from the ONE classification home, bound to the org's own rules",
   "carrier_map.load_rules" in _inp and "carrier_map.classify" in _inp)
ok("H2 the store key comes from the ONE store canonicalization the P&L books under",
   "coa.store_resolver" in _inp)
ok("H3 the coverage verdict comes from the ONE coverage home — the month-complete one (§19.54), "
   "not the bare two-feed gap, so a CLOSED month is judged against its whole calendar",
   "_pdq.statement_month_coverage" in _inp and "_pdq.day_coverage_gap" not in _inp)
# RESTATED 2026-10-09 (index §65), not relaxed. This check counted CALL SITES — the definition plus
# one use — which made a second legitimate CALLER of the shared reader fail it. The claim it exists to
# hold is that there is one READING RULE, so it is now pinned the way that is actually true: exactly
# one function interprets the coverage entry, every other site calls it, and no site anywhere reads
# `ok` / `missing_days` out of the entry itself. The device-grain layer (§65) is the second caller and
# is named here so a THIRD one still has to be a deliberate edit of this line.
_mod_code = _code_only(_src(MOD))
_cov_callers = ("reconcile", "device_lines")
ok("H3b ... and ONE function interprets that answer — every other site calls it, and nothing else "
   f"reads the entry's own keys ({len(_cov_callers)} callers, named)",
   _mod_code.count("coverage_verdict(") == 1 + len(_cov_callers)
   and all("coverage_verdict(" in _fn_src(MOD, f) for f in _cov_callers)
   and '"ok" in e' in _fn_src(MOD, "coverage_verdict")
   # A caller may NAME the days it was handed back (both do, on the row that reports them); what it
   # may not do is reach into the coverage ENTRY itself, which is the thing that would become a
   # second reading rule.
   and all(not any(t in _fn_src(MOD, f) for t in ('.get("ok")', '"ok" in', '.get("missing_days")',
                                                  '.get("complete")', '.get("missing_amount")'))
           for f in _cov_callers))
ok("H4 every feed read goes through the ONE complete paged read — no literal row ceiling (§19.48)",
   # the local `_read` helper IS that read, and the one read that cannot use it (the distributor
   # snapshot has no period column) calls it directly. Both are counted, so a fourth read added by
   # hand with its own page loop fails this.
   "_feed_read.read_all" in _inp and _inp.count("_feed_read.read_all") >= 2
   and _inp.count('_read("') + _inp.count("_feed_read.read_all") >= 4
   and "range(" not in _inp
   # the only `.limit(` allowed is the single-row CONFIG read; a feed read may never carry one
   and _inp.count(".limit(") == _inp.count(".limit(1)"))
ok("H5 the config is read from the org's own row with the house row behind it (RULE TWO)",
   "config_from_rows" in _inp and "commission_org_config" in _inp
   and "device_reimb_recon_config" in _inp)
ok("H6 NO second classification mechanism exists anywhere in this change",
   _code_only(_src(MOD)).count("component") > 0
   and "COMPONENTS" not in _code_only(_src(MOD))
   and "carrier_category_map" not in _code_only(_src(MOD)))
ok("H7 the read endpoint writes nothing",
   not any(w in _fn_src(ROUTER, "device_reimbursement_recon")
           for w in ("insert(", "upsert(", "delete(", "flag_persist")))
ok("H8 this harness is wired into CI — in the paths filter AND as a step",
   _src_opt(WORKFLOW).count("harness_device_reimb_recon.py") >= 2)
ok("H9 the subsystem is registered in the index with its own section",
   "19.52" in _src_opt(INDEX) and "device_reimb_recon" in _src_opt(INDEX))
ok("H10 ... and in the §16-18 cross-references, with the endpoints",
   "/commcalc/device-reimbursement-recon" in _src_opt(INDEX))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§I CONTROLS — each guard is shown to be WHAT produces the honest answer")
# ══════════════════════════════════════════════════════════════════════════════════════════════


# Each control MUTATES the thing the guard depends on and shows the guard is what produces the
# honest answer — not that the answer happened to come out right.

# 1. NETTING. The same two store-months, netted into one signed figure, report -$584.10: neither the
#    shortfall the owner asked for nor the overpayment, and a number nobody can act on.
_netted = round(_r7["totals"]["claimed_not_paid_total"]
                - _r7["totals"]["paid_above_claim_total"], 2)
ok("control: netting the two directions reports -584.10 and loses BOTH findings",
   _netted == -584.10
   and (_r7["totals"]["claimed_not_paid_total"], _r7["totals"]["paid_above_claim_total"])
   == (415.97, 1000.07))

# 2. AN ABSENCE AS A ZERO. A claim with no statement at all would, zero-filled, be arithmetically
#    identical to a real agreement — the shipped verdict is the ONLY thing that tells them apart.
_absent = rowof(_r1, STORE_B, "2026-09")
_agreed = run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 1000.00, "2026-09-03")],
              [device(STORE_A, "2026-09-14", 1000.00)], coverage=COMPLETE)[1]["rows"][0]
ok("control: zero-filling an absence would make it identical to a measured agreement",
   (_absent["difference"] is None and _agreed["difference"] == 0.0
    and _absent["verdict"] != _agreed["verdict"]))

# 3. THE FLOOR RULE. Hand the SAME short-month inputs a forged 'complete' coverage verdict and the
#    reconciliation does accuse the store — which is exactly what the guard withholds.
_c8, _r8 = run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03")],
               [device(STORE_A, "2026-09-14", 7999.93)], coverage=COMPLETE)
_f8 = R.recon_flags(_r8, _c8, period_label="September 2026")
ok("control: with coverage forged complete the same rows DO raise a claimed-and-never-paid flag",
   [f["flag_type"] for f in _f8] == [R.FLAG_CLAIMED_NOT_PAID]
   and [f["flag_type"] for f in _f4] == [R.FLAG_NOT_MEASURED])

# 4. THE CONFIG ABSENCE. The same rows WITH the declaration produce a finding, so §F7's empty list
#    is caused by the missing declaration and not by there being nothing there.
_c9, _r9 = run([carrier(STORE_A, "September 2026", "PROMO TYPE ONE", 7583.96, "2026-09-03")],
               [device(STORE_A, "2026-09-14", 7999.93)], cfg_raw=DECLARED, coverage=COMPLETE)
ok("control: the same rows WITH a declared key do produce a finding",
   len(R.recon_flags(_r9, _c9)) == 1 and R.recon_flags(_r0, _c0) == [])

# 5. THE REGISTRY. A drive-by type really is rejected, so F10 is a test and not a tautology.
ok("control: an unregistered flag type is rejected by the registry",
   FR.canon_type("DEVICE_REIMB_SOMETHING_NOBODY_DECLARED") is None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
