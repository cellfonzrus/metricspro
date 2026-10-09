"""DB-FREE PROOF — EQUIPMENT REIMBURSEMENT PER DEVICE: claimed, cost, charged in store, and WHO was paid.

OWNER REPORT 2026-10-09, verbatim:
    "i was checking the p&l for 652 , the equipment rebate is almost 5000 less than the equipment
     reimbursement, we need to check what is going on and also create another report for the
     equipment reimbursement per line , cost per line, and device payment charged in the store to
     asses which line items dod not get paid, this will be in inventory module and also the carrier
     commission recon"
and, when the cause came back:
    "add who sold the phone to the report and move the cost to a different store if this happens but
     capture that in a report for phones activated under different report with selling price,
     reimbursement, etc"

HIS NUMBER, MEASURED LIVE AND REPRODUCED HERE AS A FIXTURE. Read-only, house org, store
'652 Communipaw Avenue', September 2026, through the SHIPPED code and the REAL
`account/coa.store_resolver` against production:

    distributor claims         $15,274.91  across 47 devices (cost $18,839.53)
    carrier paid THIS store    $10,314.93  on 32 devices
    carrier paid ANOTHER store  $5,004.98  on 15 devices   <- his "almost 5000"
    not paid anywhere               $0.00

THE DEFECT CLASS, not the instance. The instance is 15 handsets at one store in one month. The class:

    **"which store does a device-financing dollar belong to?" was answered by two authorities that
      nothing reconciled per device — the distributor ledger books the claim to the store the device
      was STOCKED to, the carrier statement pays the store it was ACTIVATED at.**

At store-month grain the two are two totals and the difference is unexplained. At device grain it is
a named list of transferred handsets with the rep who sold each one, which is a thing a human can
act on. Estate-wide, September 2026: 140 devices, $43,568.83, and $0.00 genuinely unpaid.

WHAT THIS HARNESS LOCKS:
  §A the grain is ADDED to the existing one home, and every shared fact is DEREFERENCED
  §B the owner's own numbers, reproduced through the shipped code
  §C PAID-TO-ANOTHER-STORE IS NEVER "NOT PAID" — the false-accusation guard at device grain
  §D AN ABSENCE IS NEVER A ZERO — no recorded sale price, no declaration, no device identifier
  §E the coverage rule, direction-asymmetric exactly as the store-month layer's is
  §F the SYMMETRIC lag window, and why one-sided is wrong here
  §G the buckets are never netted, and every dollar lands in exactly one
  §H RULE TWO and purity — no carrier, tenant, store, product or person name, no DB, no clock
  §I the un-wiring locks: the build fails if a caller stops dereferencing a shared fact
  §J controls — each guard is MUTATED and shown to be what produces the honest answer

Run:  cd backend && python3 harness_device_line_reimbursement.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.modules.commcalc import device_reimb_recon as R
from app.modules.commcalc import imei_rebate_report as IRR

MOD = "app/modules/commcalc/device_reimb_recon.py"
ROUTER = "app/modules/commcalc/router.py"
COA_MOD = "app/modules/account/coa.py"
GRAPH = "app/modules/core/module_graph.py"
INDEX = "../docs/SYSTEM_DATA_FLOW_INDEX.md"
PAGE = "../frontend/src/app/(platform)/commcalc/device-line-reimbursement/page.tsx"
RBAC = "../frontend/src/lib/rbac.ts"
REPORTS = "../frontend/src/lib/reports.ts"

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


CFG = R.config_from_rows(None)
COLS = CFG["columns"]
COMPLETE = {"ok": True}
SHORT = {"ok": False, "missing_days": ["2026-09-30"], "missing_amount": 1234.56}


def line(store, imei, amount, day, ptype="A DECLARED REIMBURSEMENT TYPE", rep=""):
    return {COLS["line_store"]: store, COLS["line_device_id"]: imei,
            COLS["line_amount"]: amount, COLS["line_date"]: day,
            COLS["line_category"]: ptype, COLS["line_rep"]: rep}


def dev(store, imei, claim, reimb_date, cost=0.0, sale=None, sold="", model="A MODEL"):
    return {COLS["distributor_store"]: store, COLS["distributor_device_id"]: imei,
            COLS["distributor_amount"]: claim, COLS["distributor_date"]: reimb_date,
            COLS["distributor_cost"]: cost, COLS["distributor_sale"]: sale,
            COLS["distributor_sold"]: sold, COLS["distributor_device_model"]: model,
            COLS["distributor_category"]: "A CLAIM CATEGORY", COLS["distributor_status"]: "A STATUS"}


def run(lines, devices, coverage=None, configured=True, cfg=None):
    c = cfg or CFG
    is_dd = (lambda t: "REIMBURSEMENT" in str(t or "").upper()) if configured else None
    cl = R.carrier_line_side(lines, c, is_dd)
    return cl, R.device_lines(devices, cl, c, None, coverage, configured=configured)


def row_of(res, imei):
    return next((r for r in res["rows"] if r["device_id"] == imei), None)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  THE GRAIN IS ADDED TO THE EXISTING ONE HOME, AND SHARED FACTS ARE DEREFERENCED")
# ════════════════════════════════════════════════════════════════════════════════════════════════
_mod = _src(MOD)
ok("A1. the device grain lives in the SAME module as the store-month reconciliation — one home for "
   "'ePay paid vs distributor claimed', not a sibling that would drift from it",
   all(hasattr(R, n) for n in ("carrier_line_side", "device_lines", "carrier_side",
                               "distributor_side", "reconcile")))
ok("A2. no sibling module answers the same question",
   not any(os.path.exists(os.path.join(_HERE, "app/modules/commcalc", f)) for f in
           ("device_line_recon.py", "device_line_reimb.py", "reimbursement_per_line.py",
            "device_reimb_lines.py")))
ok("A3. the device layer reuses the store-month layer's own CLAIM matcher — it does not decide for "
   "itself which ledger rows are a claim",
   "distributor_claim_matcher(cfg)" in _fn_src(MOD, "device_lines"))
ok("A4. the device layer reuses the store-month layer's own COVERAGE reader",
   "coverage_verdict(" in _fn_src(MOD, "device_lines"))
ok("A5. it reuses the existing REASON vocabulary rather than inventing a second one",
   R.REASON_NO_RULE in R.REASONS and R.REASON_COVERAGE_INCOMPLETE in R.REASONS
   and set(R.REASON_LABELS) >= {R.REASON_NO_RULE, R.REASON_COVERAGE_INCOMPLETE})
ok("A6. it classifies NOTHING of its own: the only thing that says a line is device-financing money "
   "is the INJECTED callable, so the §58 classification home stays the single authority",
   "is_device_dollar" in _fn_src(MOD, "carrier_line_side")
   and "carrier_dollar_class" not in _code_only(_mod)
   and "carrier_map" not in _code_only(_mod))
_rt = _fn_src(ROUTER, "_device_line_reimb_run")
ok("A7. the ROUTER binds that callable to §58's one home and to the org's OWN component routing — "
   "never a keyword guess and never a hard-coded component",
   "carrier_dollar_class" in _rt and "_cdc.classify(" in _rt
   and 'component_lines' in _rt)
ok("A8. the router reuses the store-month layer's own input assembly (config, store resolver, "
   "coverage) instead of re-reading them",
   "_device_reimb_recon_inputs(" in _rt)
_dev_fns = _fn_src(MOD, "carrier_line_side") + _fn_src(MOD, "device_lines")
ok("A9. the store key is §64's ONE store-identity home, reached through `coa.store_resolver` — the "
   "device layer never matches, folds or tokenises a store itself",
   "resolve_store" in _fn_src(MOD, "carrier_line_side")
   and "resolve_store" in _fn_src(MOD, "device_lines")
   and not any(t in _dev_fns for t in ("split(", ".lower()", "partition(", "store_mapping",
                                        "store_alias", "street")))
ok("A9b. and the ROUTER hands it §64's resolver rather than a spelling of its own",
   "resolve_store" in _rt and "store_identity" not in _rt and "split(" not in _rt)
ok("A10. the LAG WINDOW is §27's own enumeration, dereferenced — no second lag rule in the router",
   "imei_rebate_report import period_window" in _rt and callable(IRR.period_window))
ok("A11. the module graph carries the device grain on the EXISTING fact, not a 45th one",
   "'19.53', '65'" in _src(GRAPH)
   and "harness_device_line_reimbursement.py" in _src(GRAPH))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  THE OWNER'S OWN NUMBERS, REPRODUCED THROUGH THE SHIPPED CODE")
# ════════════════════════════════════════════════════════════════════════════════════════════════
# The live September shape at his store, as fixtures: four iPhone-class units the carrier paid a
# second store for, one Motorola-class unit paid at a third, and the rest paid at home. Amounts are
# the real ones; the store strings are placeholders (RULE TWO — no real address in a harness).
HOME, OTHER_A, OTHER_B, OTHER_C = ("STORE UNDER REVIEW", "ANOTHER STORE", "A THIRD STORE",
                                   "A FOURTH STORE")
# THE 15 TRANSFERRED DEVICES — claim, cost and the amount the carrier paid elsewhere are the live
# figures. Note that on two units the carrier paid $137.50 against a $135.00 claim: the two sides are
# the same money but not the same number, which is why this report never derives one from the other.
_T = (
    [(f"T-A{i}", 525.00, 599.99, 525.00, OTHER_A) for i in range(4)]
    + [(f"T-B{i}", 450.00, 599.99, 450.00, OTHER_A) for i in range(4)]
    + [("T-C0", 240.00, 149.99, 240.00, OTHER_B)]
    + [(f"T-D{i}", 159.99, 159.99, 159.99, OTHER_C) for i in range(2)]
    + [(f"T-E{i}", 135.00, 159.99, 137.50, OTHER_B) for i in range(2)]
    + [(f"T-F{i}", 135.00, 159.99, 135.00, OTHER_C) for i in range(2)]
)
# THE 31 + 1 DEVICES PAID AT HOME. The carrier paid $40.00 more than was claimed across them, which
# is the `paid_above_claim` direction the store-month layer already names and keeps in its own
# bucket — carried here so the fixture is the live shape and not a tidied one.
_H = [(f"P{i}", 320.00, 400.00, 320.00) for i in range(31)] + [("P31", 354.93, 529.68, 394.93)]
_B_DEVICES = (
    [dev(HOME, i, claim, "2026-09-17", cost=cost, sold="2026-09-09") for i, claim, cost, _p, _s in _T]
    + [dev(HOME, i, claim, "2026-09-12", cost=cost, sale=29.99, sold="2026-09-05")
       for i, claim, cost, _p in _H]
)
_B_LINES = (
    [line(st, i, paid, "2026-09-17", rep="A REP") for i, _c, _co, paid, st in _T]
    + [line(HOME, i, paid, "2026-09-12", rep="A HOME REP") for i, _c, _co, paid in _H]
)
_cl, _B = run(_B_LINES, _B_DEVICES, coverage={"2026-09": COMPLETE})
_t = _B["totals"]
eq("B1. 47 devices claimed", _t["devices"], 47)
eq("B2. the distributor's claim totals his $15,274.91", _t["distributor_claimed"], 15274.91)
eq("B3. the carrier paid THIS store $10,314.93", _t["carrier_paid_here"], 10314.93)
eq("B4. the carrier paid ANOTHER store his 'almost 5000' — $5,004.98",
   _t["carrier_paid_other_total"], 5004.98)
eq("B5. 15 devices are the transferred ones", _t["by_status"][R.LINE_PAID_OTHER_STORE], 15)
eq("B6. 32 devices were paid at home", _t["by_status"][R.LINE_PAID], 32)
eq("B7. NOTHING is unpaid anywhere — the money is on the wrong store, not missing",
   (_t["not_paid_total"], _t["by_status"][R.LINE_NOT_PAID]), (0.0, 0))
eq("B8. the device COST carried by the store under review", _t["device_cost"], 18839.53)
ok("B9. the gap the owner saw is the transferred devices' own CLAIM — $4,999.98 — and the carrier "
   "paid $5,004.98 against it. The two sides are the same money and NOT the same number, so neither "
   "is ever derived from the other",
   round(sum(r["distributor_claimed"] for r in _B["rows"]
             if r["status"] == R.LINE_PAID_OTHER_STORE), 2) == 4999.98
   and _t["carrier_paid_other_total"] == 5004.98)
_a0 = row_of(_B, "T-A0")
eq("B10. a transferred row NAMES the store that was paid",
   [(o["store"], o["amount"]) for o in _a0["carrier_paid_other_stores"]], [(OTHER_A, 525.00)])
eq("B11. and names WHO SOLD IT, with where they sold it (owner ask 2026-10-09)",
   (_a0["sold_by"], _a0["sold_by_store"]), ("A REP", OTHER_A))
eq("B12. a device paid at home names the rep paid HERE, not one at another store",
   (row_of(_B, "P0")["sold_by"], row_of(_B, "P0")["sold_by_store"]), ("A HOME REP", HOME))
ok("B13. the per-store roll-up and the header are ONE accumulation applied twice, so they cannot "
   "state different numbers",
   len(_B["by_store"]) == 1 and all(
       _B["by_store"][0][k] == _t[k] for k in
       ("devices", "distributor_claimed", "device_cost", "carrier_paid_here",
        "carrier_paid_other_total", "not_paid_total", "not_measured_total")))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  PAID-TO-ANOTHER-STORE IS NEVER 'NOT PAID' — the false-accusation guard at device grain")
# ════════════════════════════════════════════════════════════════════════════════════════════════
ok("C1. the two are different statuses with different words a human reads",
   R.LINE_PAID_OTHER_STORE != R.LINE_NOT_PAID
   and R.LINE_STATUS_LABELS[R.LINE_PAID_OTHER_STORE] != R.LINE_STATUS_LABELS[R.LINE_NOT_PAID])
ok("C2. a transferred device contributes NOTHING to the not-paid total",
   _t["not_paid_total"] == 0.0 and _t["carrier_paid_other_total"] == 5004.98)
_cl2, _r2 = run([line(OTHER_A, "IMEI-X", 500.00, "2026-09-11")],
                [dev(HOME, "IMEI-X", 500.00, "2026-09-17", cost=600.00)],
                coverage={"2026-09": SHORT})
eq("C3. PROVEN WHATEVER THE COVERAGE: money that DID arrive at a named store cannot be unproven by "
   "the days that did not, so a short month still reports the transfer",
   row_of(_r2, "IMEI-X")["status"], R.LINE_PAID_OTHER_STORE)
_cl3, _r3 = run([line(HOME, "IMEI-Y", 300.00, "2026-09-11"),
                 line(OTHER_A, "IMEI-Y", 20.00, "2026-09-12")],
                [dev(HOME, "IMEI-Y", 320.00, "2026-09-17")], coverage={"2026-09": COMPLETE})
_y = row_of(_r3, "IMEI-Y")
eq("C4. a device paid at BOTH stores is PAID here, with the other store's share still reported — "
   "never folded away, never promoted to a transfer",
   (_y["status"], _y["carrier_paid_here"], _y["carrier_paid_other_total"]),
   (R.LINE_PAID, 300.00, 20.00))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  AN ABSENCE IS NEVER A ZERO")
# ════════════════════════════════════════════════════════════════════════════════════════════════
_cl4, _r4 = run([], [dev(HOME, "IMEI-NS", 100.00, "2026-09-17", cost=120.00, sale=None)],
                coverage={"2026-09": COMPLETE})
_ns = row_of(_r4, "IMEI-NS")
eq("D1. a device the ledger records NO sale price for reports null, never $0.00",
   (_ns["store_device_payment"], _ns["store_payment_recorded"]), (None, False))
eq("D2. a recorded price of exactly 0 is a RECORDED zero and says so",
   [(r["store_device_payment"], r["store_payment_recorded"]) for r in
    run([], [dev(HOME, "IMEI-Z", 1.0, "2026-09-17", sale=0)],
        coverage={"2026-09": COMPLETE})[1]["rows"]],
   [(0.0, True)])
eq("D3. the total counts how many devices record no price, instead of averaging them in as zeros",
   (_r4["totals"]["store_payment_not_recorded"], _r4["totals"]["store_device_payment"]), (1, 0.0))
_cl5, _r5 = run(_B_LINES, _B_DEVICES, coverage={"2026-09": COMPLETE}, configured=False)
ok("D4. with NOTHING declared as device-financing money every device is NOT MEASURED with that "
   "reason — not one dollar is called unpaid, and the report says it is unconfigured",
   _r5["configured"] is False
   and {r["status"] for r in _r5["rows"]} == {R.LINE_NOT_MEASURED}
   and {r["reason"] for r in _r5["rows"]} == {R.REASON_NO_RULE}
   and _r5["totals"]["not_paid_total"] == 0.0
   and _r5["totals"]["not_measured_total"] == 15274.91)
_cl6, _r6 = run([line(HOME, "", 400.00, "2026-09-11")],
                [dev(HOME, "", 400.00, "2026-09-17")], coverage={"2026-09": COMPLETE})
eq("D5. a claim with NO device identifier cannot be looked up, so it is NOT MEASURED with its own "
   "reason — never 'not paid'",
   (row_of(_r6, "")["status"], row_of(_r6, "")["reason"]),
   (R.LINE_NOT_MEASURED, R.REASON_DEVICE_UNIDENTIFIED))
eq("D6. a PAID line with no device identifier is counted and reported, never dropped, so the paid "
   "side is never quietly understated",
   (_cl6["unidentified"]["rows"], _r6["totals"]["carrier_paid_unidentified"]), (1, 400.00))
_cl7, _r7 = run([], [dev(HOME, "IMEI-U", 50.00, "")], coverage={"2026-09": COMPLETE})
eq("D7. a claim with no reimbursement DATE belongs to no month, so it is reported unplaced rather "
   "than filed under the month being viewed",
   (_r7["rows"], _r7["unplaced"]), ([], {"rows": 1, "amount": 50.0}))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  THE COVERAGE RULE, DIRECTION-ASYMMETRIC EXACTLY AS THE STORE-MONTH LAYER'S IS")
# ════════════════════════════════════════════════════════════════════════════════════════════════
_unpaid = [dev(HOME, "IMEI-N", 450.00, "2026-09-17", cost=500.00)]
_cs, _rs = run([], _unpaid, coverage={"2026-09": SHORT})
_n = row_of(_rs, "IMEI-N")
eq("E1. nothing paid anywhere in a month whose feed arrived SHORT is NOT MEASURED, with the "
   "coverage reason — never 'the carrier never paid you for this handset'",
   (_n["status"], _n["reason"]), (R.LINE_NOT_MEASURED, R.REASON_COVERAGE_INCOMPLETE))
eq("E2. and it carries the EVIDENCE for withholding: which days are missing and what they were worth",
   (_n["coverage_missing_days"], _n["coverage_missing_amount"]), (["2026-09-30"], 1234.56))
_cc, _rc = run([], _unpaid, coverage={"2026-09": COMPLETE})
eq("E3. on a COMPLETE month the same device IS reported unpaid — the absence is then a fact",
   (row_of(_rc, "IMEI-N")["status"], _rc["totals"]["not_paid_total"]), (R.LINE_NOT_PAID, 450.00))
_cu, _ru = run([], _unpaid, coverage={})
eq("E4. UNKNOWN coverage is handled exactly like incomplete — an untested feed is not a tested one",
   row_of(_ru, "IMEI-N")["status"], R.LINE_NOT_MEASURED)
ok("E5. a not-measured row never carries the missing-day evidence of a DIFFERENT reason",
   row_of(_r6, "")["coverage_missing_days"] == []
   and row_of(_r6, "")["coverage_missing_amount"] is None)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  THE LAG WINDOW IS SYMMETRIC, AND A ONE-SIDED WINDOW IS SHOWN TO BE WRONG HERE")
# ════════════════════════════════════════════════════════════════════════════════════════════════
eq("F1. the measured default is 1 month, not §27's 6 — the answer saturates at one either side",
   CFG["lag_months"], 1)
ok("F2. a tenant widens or narrows it with ONE config row, and a typo cannot unbound it",
   R.normalize_config({"lag_months": 4})["lag_months"] == 4
   and R.normalize_config({"lag_months": 99})["lag_months"] == 24
   and R.normalize_config({"lag_months": -5})["lag_months"] == 0
   and R.normalize_config({"lag_months": "nonsense"})["lag_months"] == CFG["lag_months"])
_win = _rt[_rt.index("_lag_window"):]
ok("F3. the router asks §27 for the window starting LAG MONTHS BEFORE the claim month and twice as "
   "wide — the symmetry is in the code, not in a comment",
   "_lag * 2" in _win and "- _lag" in _win)
_bw = IRR.period_window("2026-08", 2)
eq("F4. §27's own enumeration, asked that way, really does include the month before the anchor",
   _bw[:3], ["August 2026", "September 2026", "October 2026"])
# The regression that made the symmetry necessary: a device PAID in August, CLAIMED in September.
_clf, _rf = run([line(OTHER_A, "IMEI-EARLY", 240.00, "2026-08-24")],
                [dev(HOME, "IMEI-EARLY", 240.00, "2026-09-03", cost=149.99)],
                coverage={"2026-09": COMPLETE})
eq("F5. REGRESSION (live, store under review, September 2026): a device the carrier paid in AUGUST "
   "and the distributor claimed in SEPTEMBER is a transfer, not an unpaid device",
   row_of(_rf, "IMEI-EARLY")["status"], R.LINE_PAID_OTHER_STORE)
_cll, _rl = run([line(HOME, "IMEI-LATE", 300.00, "2026-10-04")],
                [dev(HOME, "IMEI-LATE", 300.00, "2026-09-17")], coverage={"2026-09": COMPLETE})
_l = row_of(_rl, "IMEI-LATE")
ok("F6. a payment that arrived in a LATER month is paid, and SAYS it was late rather than hiding "
   "the lag inside a bare 'paid'",
   _l["status"] == R.LINE_PAID and _l["carrier_paid_after_claim_month"] is True
   and _l["carrier_paid_here_month"] == "2026-10")
ok("F7. a payment in the claim month is NOT flagged late",
   row_of(_B, "P0")["carrier_paid_after_claim_month"] is False)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§G  THE BUCKETS ARE NEVER NETTED, AND EVERY DOLLAR LANDS IN EXACTLY ONE")
# ════════════════════════════════════════════════════════════════════════════════════════════════
_MIX = [dev(HOME, "IMEI-M1", 100.00, "2026-09-17"), dev(HOME, "IMEI-M2", 200.00, "2026-09-17"),
        dev(HOME, "IMEI-M3", 300.00, "2026-09-17"), dev(HOME, "IMEI-M4", 400.00, "")]
_clm, _rm = run([line(HOME, "IMEI-M1", 100.00, "2026-09-02"),
                 line(OTHER_A, "IMEI-M2", 200.00, "2026-09-02")],
                _MIX, coverage={"2026-09": COMPLETE})
_tm = _rm["totals"]
eq("G1. every claimed dollar of a PLACED device is in exactly one status bucket",
   round(sum(r["distributor_claimed"] for r in _rm["rows"]), 2), 600.00)
ok("G2. the unpaid buckets are kept apart and never netted against what was paid",
   (_tm["not_paid_total"], _tm["not_measured_total"]) == (300.00, 0.0)
   and _tm["carrier_paid_here"] == 100.00 and _tm["carrier_paid_other_total"] == 200.00)
eq("G3. an unplaceable claim is reported on its own, never folded into a status",
   _rm["unplaced"], {"rows": 1, "amount": 400.0})
ok("G4. the status counts add up to the devices on the report, with no device in two buckets",
   sum(_tm["by_status"].values()) == _tm["devices"] == len(_rm["rows"]))
ok("G5. the payload states that THIS layer's paid side is device-keyed, unlike the store-month "
   "layer's — a reader is never invited to click through to a device that is not there",
   _rm["carrier_has_device_grain"] is True
   and "carrier_has_device_grain" in _code_only(_src(MOD)))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§H  RULE TWO AND PURITY")
# ════════════════════════════════════════════════════════════════════════════════════════════════
_code = _code_only(_src(MOD))
# A DEFAULT COLUMN NAME IS SCHEMA, NOT A BRANCH — it names a column of the org's own feed table and
# H4 proves every one of them is re-pointable with a config row, which is what RULE TWO asks for. A
# carrier, tenant, store, product, person or quarter name is a different thing and none appears.
_BANNED = ("boost", "luxelink", "vzone", "cellfonz", "vidapay", "communipaw", "motorola",
           "iphone", "apple", "samsung", "springfield", "broadway", "nostrand", "promo",
           "bogo", "upgrade offer")
ok("H1. no carrier, tenant, store, product, person or quarter name anywhere in the module's CODE "
   f"({len(_BANNED)} spellings checked)",
   [b for b in _BANNED if b in _code.lower()] == [],
   [b for b in _BANNED if b in _code.lower()])
ok("H2. no DB, no framework, no clock reachable from the module",
   not any(t in _code for t in ("supabase", "fastapi", "psycopg", "requests.",
                                "datetime.now", "date.today", "time.time", "client.")))
ok("H3. every column name a tenant's feed might spell differently is CONFIG, not a literal",
   all(k in COLS for k in ("line_store", "line_amount", "line_category", "line_device_id",
                           "line_date", "line_rep", "distributor_cost", "distributor_sale",
                           "distributor_sold")))
ok("H4. a tenant re-points any of them with one config row, and an unknown key is ignored rather "
   "than silently creating a column nobody reads",
   R.normalize_config({"columns": {"line_rep": "a_different_column"}})["columns"]["line_rep"]
   == "a_different_column"
   and "not_a_column" not in R.normalize_config({"columns": {"not_a_column": "x"}})["columns"])
ok("H5. the DEVICE LAYER writes nothing, books nothing and pays nobody — no table write, and no "
   "pay rate, tier, plan or payout vocabulary is reachable from it",
   not any(t in _dev_fns for t in ("insert(", "upsert(", ".update(", "delete(", "commission_rate",
                                    " tier", "payout", "pay_plan", "flag_persist")))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§I  THE UN-WIRING LOCKS, AND THE SURFACES THE OWNER ASKED FOR")
# ════════════════════════════════════════════════════════════════════════════════════════════════
_router = _src(ROUTER)
ok("I1. the endpoint exists and is READ-ONLY (a GET, with no writer beside it)",
   '@router.get("/device-line-reimbursement")' in _router
   and '@router.post("/device-line-reimbursement' not in _router)
ok("I2. it refuses to answer without a period rather than guessing one",
   'raise HTTPException(400, "period required")' in _fn_src(ROUTER, "device_line_reimbursement"))
ok("I3. it is org-scoped like every other sensitive read",
   "require_org(org_id)" in _fn_src(ROUTER, "device_line_reimbursement"))
ok("I4. the per-line feed is read through the PAGED one home — no literal row ceiling on a feed "
   "that grows by ~30,000 rows a month",
   "_feed_read.read_all(" in _rt)
_rbac = _src_opt(RBAC)
ok("I5. the report is in the INVENTORY (Assets) module the owner asked for",
   "'/commcalc/device-line-reimbursement', label: 'Reimbursement per Line', icon: '📱', module: 'asset'"
   in _rbac)
ok("I6. and beside the carrier reconciliations, which is the other place he asked for it",
   "'/commcalc/device-line-reimbursement', label: 'Reimbursement per Line', icon: '📱', module: 'commissions'"
   in _rbac)
ok("I7. the transferred phones are their own listed report (owner ask 2026-10-09) and are a DEEP "
   "LINK into the same derivation, never a second page that could disagree",
   "/commcalc/device-line-reimbursement?view=transferred" in _rbac
   and "/commcalc/device-line-reimbursement?view=transferred" in _src_opt(REPORTS)
   and not os.path.exists(os.path.join(
       _HERE, "../frontend/src/app/(platform)/commcalc/device-line-transferred")))
_page = _src_opt(PAGE)
ok("I8. the screen renders and never computes: no arithmetic on the money columns",
   _page != "" and not any(t in _page for t in ("reduce(", ".sort((", "+ r.carrier", "- r.device")))
ok("I9. the screen shows an em dash for a price the ledger does not record — it never prints a 0 "
   "that would read as a sale for nothing",
   "store_payment_recorded ? r.store_device_payment : '—'" in _page)
ok("I10. the screen names who sold the phone and where (owner ask 2026-10-09)",
   "'Sold by'" in _page and "'Sold at'" in _page)
ok("I11. index §65 exists and names this report",
   "## 65." in _src_opt(INDEX) and "device-line-reimbursement" in _src_opt(INDEX))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§J  CONTROLS — each guard is MUTATED and shown to be what produces the honest answer")
# ════════════════════════════════════════════════════════════════════════════════════════════════
# 1. The transferred status is doing the work: fold it into 'not paid' and the owner's $5,004.98
#    becomes a theft-shaped accusation against his own store.
_folded = round(sum(r["distributor_claimed"] for r in _B["rows"]
                    if r["status"] in (R.LINE_PAID_OTHER_STORE, R.LINE_NOT_PAID)), 2)
ok("J1. control: folding the transferred devices into 'not paid' would accuse the store of "
   "$4,999.98 never paid — which is the false accusation the separate status prevents",
   _folded == 4999.98 and _B["totals"]["not_paid_total"] == 0.0, _folded)
# 2. The symmetric window is doing the work: a forward-only window calls F5's device unpaid.
_clj, _rj = run([], [dev(HOME, "IMEI-EARLY", 240.00, "2026-09-03", cost=149.99)],
                coverage={"2026-09": COMPLETE})
ok("J2. control: with the earlier month's lines withheld (a forward-only window) the SAME device "
   "reads 'not paid' — so §F5 is the window's doing and not a tautology",
   row_of(_rj, "IMEI-EARLY")["status"] == R.LINE_NOT_PAID
   and row_of(_rf, "IMEI-EARLY")["status"] == R.LINE_PAID_OTHER_STORE)
# 3. The coverage guard is doing the work: forge 'complete' and the same rows DO accuse.
ok("J3. control: with coverage forged complete the same short-month rows DO report unpaid — so §E1 "
   "is the guard withholding, not an absence of data",
   row_of(_rc, "IMEI-N")["status"] == R.LINE_NOT_PAID
   and row_of(_rs, "IMEI-N")["status"] == R.LINE_NOT_MEASURED)
# 4. The declaration is doing the work: the same rows WITH it produce measured verdicts.
ok("J4. control: the same rows WITH a declaration are measured, so §D4's not-measured is caused by "
   "the missing declaration and not by there being nothing there",
   _B["configured"] is True and _r5["configured"] is False
   and _B["totals"]["by_status"][R.LINE_NOT_MEASURED] == 0)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§K  AN AMBIGUOUS STORE IDENTITY WITHHOLDS THE TRANSFER VERDICT, IT DOES NOT GUESS")
# ════════════════════════════════════════════════════════════════════════════════════════════════
# Measured live (house org, September 2026) BEFORE this guard existed: 135 devices worth $41,548.83
# read as "the carrier paid a different store", and 23 of them, worth $6,164.95, had one of the three
# addresses §13d reports as claimed by TWO store records on one side of the pair. Calling those a
# transfer would move real cost between two records of ONE physical store. With the guard: 112
# devices / $35,383.88 proven, 23 reported not measured, and store 652 untouched (15 / $5,004.98 —
# none of its four counterpart stores is ambiguous).
_AMB = "AN ADDRESS TWO STORE RECORDS CLAIM"
_k_lines = [line(_AMB, "K-1", 525.00, "2026-09-10"), line(OTHER_A, "K-2", 450.00, "2026-09-11"),
            line(OTHER_A, "K-3", 240.00, "2026-09-12")]
_k_devs = [dev(HOME, "K-1", 525.00, "2026-09-15", cost=599.99),      # paid at the ambiguous address
           dev(_AMB, "K-2", 450.00, "2026-09-15", cost=599.99),      # claimed BY the ambiguous one
           dev(OTHER_B, "K-3", 240.00, "2026-09-15", cost=149.99)]   # neither side ambiguous
_k_cl = R.carrier_line_side(_k_lines, CFG, lambda t: "REIMBURSEMENT" in str(t or "").upper())
_k_blind = R.device_lines(_k_devs, _k_cl, CFG, None, {"2026-09": COMPLETE}, configured=True)
_k_seen = R.device_lines(_k_devs, _k_cl, CFG, None, {"2026-09": COMPLETE}, configured=True,
                         ambiguous_stores={_AMB})
ok("K1. the ambiguous SET is injected, not derived — the signature takes it and the module states no "
   "identity rule of its own",
   "ambiguous_stores" in _fn_src(MOD, "device_lines").split(")")[0]
   and "store_mapping" not in _code_only(_fn_src(MOD, "device_lines")))
ok("K2. with the set injected, a device the carrier paid at an ambiguous address is NOT MEASURED — "
   "never 'paid to another store'",
   row_of(_k_seen, "K-1")["status"] == R.LINE_NOT_MEASURED
   and row_of(_k_seen, "K-1")["reason"] == R.REASON_STORE_AMBIGUOUS)
ok("K3. …and so is a device CLAIMED by the ambiguous address and paid elsewhere (both sides, not "
   "just the paying one)",
   row_of(_k_seen, "K-2")["status"] == R.LINE_NOT_MEASURED
   and row_of(_k_seen, "K-2")["reason"] == R.REASON_STORE_AMBIGUOUS)
ok("K4. the money is still ON the withheld row, named — only the VERDICT is withheld, so nothing is "
   "hidden from the reader",
   row_of(_k_seen, "K-1")["carrier_paid_other_total"] == 525.00
   and [o["store"] for o in row_of(_k_seen, "K-1")["carrier_paid_other_stores"]] == [_AMB])
ok("K5. a transfer with NEITHER side ambiguous is still proven — the guard withholds the three "
   "addresses, not the finding",
   row_of(_k_seen, "K-3")["status"] == R.LINE_PAID_OTHER_STORE)
ok("K6. the withheld dollars are counted apart and never netted into 'not paid'",
   _k_seen["totals"]["not_paid_total"] == 0.0
   and _k_seen["totals"]["by_status"][R.LINE_NOT_MEASURED] == 2
   and _k_seen["totals"]["by_status"][R.LINE_PAID_OTHER_STORE] == 1)
ok("K7. the reason carries a human sentence, and no table or column name reaches it",
   isinstance(R.LINE_REASON_LABELS.get(R.REASON_STORE_AMBIGUOUS), str)
   and row_of(_k_seen, "K-1")["reason_label"] == R.LINE_REASON_LABELS[R.REASON_STORE_AMBIGUOUS]
   and not any(t in R.LINE_REASON_LABELS[R.REASON_STORE_AMBIGUOUS].lower()
               for t in ("store_mapping", "asset_ledger", "raw_payment_detail", "column", "table")))
ok("K8. the ambiguity fact is read from the ONE identity home through coa's I/O twin, and coa "
   "derives nothing itself",
   "ambiguous_identities" in _src(COA_MOD) and "store_identity_index" in _src(COA_MOD)
   and "_sid.ambiguous_identities" in _fn_src(COA_MOD, "ambiguous_store_keys"))
ok("K9. the router passes it in — the guard is wired, not merely available (the §19.18 failure mode)",
   "ambiguous_store_keys" in _src(ROUTER)
   and "ambiguous_stores=" in _src(ROUTER))
# The control that arms §K: without the set the SAME rows DO accuse, so the withholding is the
# guard's doing and not an absence of data.
ok("K10. control: with no set injected the same two rows read 'paid to another store' — so §K2/§K3 "
   "are the guard withholding and not a tautology",
   row_of(_k_blind, "K-1")["status"] == R.LINE_PAID_OTHER_STORE
   and row_of(_k_blind, "K-2")["status"] == R.LINE_PAID_OTHER_STORE
   and _k_blind["totals"]["by_status"][R.LINE_PAID_OTHER_STORE] == 3)
ok("K12. a dollar §58 placed on a category the component vocabulary has no row for is DECLINED as "
   "device money — the router reads §58's own `basis`, it does not re-judge the category",
   "BASIS_DECLARED_UNMAPPED" in _src(ROUTER) and "BASIS_UNRESOLVED" in _src(ROUTER)
   and "basis" in _src(ROUTER).split("def is_device_dollar")[1].split("return hit")[0])
ok("K13. …and that decision lives at the CALL SITE, not as a second classifier in the pure layer",
   not any(t in _code_only(_src(MOD)) for t in ("declared_category_unmapped", "payment_categories",
                                                "carrier_category_map")))

ok("K11. control: an UNAMBIGUOUS set withholds nothing, so the guard cannot quietly swallow real "
   "findings",
   R.device_lines(_k_devs, _k_cl, CFG, None, {"2026-09": COMPLETE}, configured=True,
                  ambiguous_stores={"A STORE NOBODY WAS PAID AT"}
                  )["totals"]["by_status"][R.LINE_PAID_OTHER_STORE] == 3)

# 5. The banned-name check really rejects something.
ok("J5. control: the RULE TWO scan really does reject a carrier name, so §H1 is a test",
   "boost" in _code_only("x = 'boost'").lower())

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
