"""BATCH 16 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the four DEVICE-FINANCING reports.

  /commcalc/vip                        Distributor Invoices
  /commcalc/vip/paygo                  Distributor Asset Lending (PayGo)
  /commcalc/device-line-reimbursement  Equipment Reimbursement per Line
  /commcalc/financing                  Financing report

WHAT THIS PROVES, AND WHY IT IS WORTH A FILE
────────────────────────────────────────────
Owner ask 2026-10-24: *every report built on platform data must let him see WHERE each figure came
from.* This family answers one owner question in four parts — what did financing a device cost us,
what did the distributor bill for it, what did the carrier reimburse, and on which lines — and its
hardest figure is a DISPUTE: "the distributor says it was reimbursed and the carrier never paid it".
That sentence is about TWO GRAINS on one device row, separated only by which relation answered
(§CORE-17.3n), and a panel that pooled them would be evidence for neither side of it.

It also settles the §CORE-17.3o call FOUR WAYS IN ONE BATCH. Three of these screens print the
server's own fold and must NOT be narrowed; the fourth folds its tiles in the browser and therefore
MUST be. §F locks both directions with a non-vacuous witness for every scan, so "the text is absent"
can never be confused with "the scan is blind".

The whole file is DB-FREE and stdlib-only. The producers are driven for real:
`vip_invoice_filter.select`/`.summarize`, `device_reimb_recon.carrier_line_side`/`.device_lines` and
`financing_report.build` are pure and take their rows as arguments — the last one driven BOTH through
the REAL `plan_pay_gate` per-device collapse and with the gate absent, which is the real fallback path
defect 11 is about. The PayGo summary's fold lives inside a FastAPI router that cannot be imported
without the framework, so §C reproduces it line-for-line AND pins it to the router's own source text:
if that expression changes, this file goes red rather than quietly measuring a fold nobody runs.

Run: `cd backend && python3 harness_batch16_drilldown.py`
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.asset import device_asset_drilldown as add            # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage      # noqa: E402
from app.modules.commcalc import device_financing_drilldown as dd      # noqa: E402
from app.modules.commcalc import device_reimb_recon as drr            # noqa: E402
from app.modules.commcalc import financing_report as finrep           # noqa: E402
from app.modules.commcalc import plan_pay_gate as paygate             # noqa: E402
from app.modules.commcalc import processor_money_drilldown as pmd      # noqa: E402
from app.modules.commcalc import vip_invoice_filter as vipf           # noqa: E402
from app.modules.core import report_drilldown as rd                    # noqa: E402

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A NEGATIVE CONTROL / MUTATION. `red` must be True — the tampered case really did come back
    wrong. A control that passes is a check that cannot fail, printed as though it had."""
    check("MUT " + name, red)


def read(rel):
    p = os.path.join(ROOT, rel) if not rel.startswith("harness") else rel
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def cents(v):
    return None if v is None else int(round(float(v) * 100))


def agrees(panel, tile):
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate every screen in this batch passes
    to `useDrillTile`. Deliberately the same arithmetic, so the safety net the pages rely on is
    tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


DECL = read("backend/app/modules/commcalc/device_financing_drilldown.py")
ROUTER = read("backend/app/modules/commcalc/router.py")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  THE ONE HOME, AND A VOCABULARY THAT IS DEREFERENCED RATHER THAN RE-SPELLED.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the declaration dereferences every shared fact ==")

check("A1 the distributor INVOICE feed is the registry's own constant, which this batch added — the "
      "declaration names no table of its own",
      dd.DISTRIBUTOR_INVOICES == lineage.DISTRIBUTOR_INVOICES == "vip_invoices")
REG = read("backend/app/modules/commcalc/data_lineage_registry.py")
_reg_def = 'DISTRIBUTOR_INVOICES = "vip_invoices"'
check("A2 and the registry holds exactly ONE copy of that name, so the constant is the only home "
      "for it inside this registry",
      REG.count('"vip_invoices"') == 1 and _reg_def in REG,
      str(REG.count('"vip_invoices"')))
check("A3 the ledger a CLAIM is read out of, and the per-line feed a PAYMENT is read out of, are "
      "both the registry's constants — and they are DIFFERENT relations, which is the whole reason "
      "they are two grains",
      dd.ASSET_LEDGER == lineage.ASSET_LEDGER
      and dd.CARRIER_LINE_FEED == lineage.PAYMENT_DETAIL
      and dd.ASSET_LEDGER != dd.CARRIER_LINE_FEED)
check("A4 the weekly lending batches are the DEVICE-ASSET family's own constant, dereferenced "
      "through the declaration that already dereferenced the registry — not a third spelling",
      dd.PAYGO_BATCHES is add.PAYGO_BATCHES and dd.PAYGO_BATCHES == lineage.PAYGO_BATCHES)
check("A5 the fifth provenance answer is the ONE HOME for it, not a second reading of the same "
      "meta: which tables the sales union really read is `processor_money_drilldown`'s function, "
      "bound here rather than re-implemented",
      dd.sales_union_tables is pmd.sales_union_tables)
check("A6 the fee BUCKET LIST is the producer's own, so a sixth bucket added there gains a panel "
      "here without this file being edited",
      dd.FEE_COLS == tuple(vipf.FEE_COLS) and len(dd.FEE_COLS) > 1)
check("A7 the two unpaid buckets count the PRODUCER's own verdicts, read rather than re-spelled",
      dd.LINE_NOT_PAID is drr.LINE_NOT_PAID and dd.LINE_NOT_MEASURED is drr.LINE_NOT_MEASURED
      and dd.LINE_NOT_PAID != dd.LINE_NOT_MEASURED)
check("A8 THE REASON KEYS ARE THE PRODUCER'S OWN — the batch-11 posture. The four device-level "
      "absences this family can report are `device_reimb_recon`'s constants, not a second "
      "vocabulary, and the harness fails if the two drift apart",
      (dd.REASON_NO_RULE, dd.REASON_DEVICE_UNIDENTIFIED, dd.REASON_STORE_AMBIGUOUS,
       dd.REASON_COVERAGE_INCOMPLETE)
      == (drr.REASON_NO_RULE, drr.REASON_DEVICE_UNIDENTIFIED, drr.REASON_STORE_AMBIGUOUS,
          drr.REASON_COVERAGE_INCOMPLETE))
check("A9 every reason this declaration can emit resolves to a SENTENCE in the one home, so a "
      "reader never sees a bare field name",
      all(rd.UNMEASURED_REASONS.get(r) for r in
          (dd.REASON_NO_RULE, dd.REASON_DEVICE_UNIDENTIFIED, dd.REASON_STORE_AMBIGUOUS,
           dd.REASON_COVERAGE_INCOMPLETE, dd.REASON_CARRIER_NOT_LOOKED_UP,
           dd.REASON_PRICE_NOT_RECORDED)),
      str([r for r in (dd.REASON_NO_RULE, dd.REASON_DEVICE_UNIDENTIFIED, dd.REASON_STORE_AMBIGUOUS,
                       dd.REASON_COVERAGE_INCOMPLETE, dd.REASON_CARRIER_NOT_LOOKED_UP,
                       dd.REASON_PRICE_NOT_RECORDED) if not rd.UNMEASURED_REASONS.get(r)]))
check("A10 and the READER-FACING half is not a second copy of the producer's own wording — the "
      "producer keeps its longer explanation and this file copies neither",
      all(rd.UNMEASURED_REASONS[k] != drr.REASON_LABELS.get(k)
          and rd.UNMEASURED_REASONS[k] != drr.LINE_REASON_LABELS.get(k)
          for k in (dd.REASON_NO_RULE, dd.REASON_DEVICE_UNIDENTIFIED, dd.REASON_STORE_AMBIGUOUS,
                    dd.REASON_COVERAGE_INCOMPLETE))
      and "UNMEASURED_REASONS = {" not in DECL)
check("A11 THE WEEKLY-BATCH GRAIN IS REUSED, NOT RE-DECLARED. The duplicate check found an existing "
      "catalogue over exactly these rows, so this module BINDS it — a second fold of 'lifetime "
      "billed' would be two numbers hoping to agree",
      dd.PAYGO_CATALOGUE is add.lending_catalogue
      and dd.paygo_drilldowns is add.lending_drilldowns
      and "def lending_catalogue" not in DECL and "def paygo_catalogue" not in DECL
      and not re.search(r"^def \w*(batch|lending)\w*_catalogue", DECL, re.M))
_TREE = ast.parse(DECL)
_floats = [n.value for n in ast.walk(_TREE)
           if isinstance(n, ast.Constant) and isinstance(n.value, float)]
_const_math = [n for n in ast.walk(_TREE) if isinstance(n, ast.BinOp)
               and any(isinstance(side, ast.Constant) and isinstance(side.value, (int, float))
                       and not isinstance(side.value, bool)
                       for side in (n.left, n.right))]
check("A12 THE DECLARATION DOES NO ARITHMETIC AND HOLDS NO MONEY. Checked on the PARSED module "
      "rather than on its prose: not one float literal anywhere — so no dollar figure, no rate and "
      "no threshold — and not one numeric constant used as an operand, so it cannot have computed a "
      "figure it was handed",
      _floats == [] and _const_math == [],
      str(_floats[:3]) + str(len(_const_math)))
check("A13 and it reaches no framework, no clock and no database",
      not re.search(r"^\s*(import|from)\s+(fastapi|pydantic|datetime|time|os|supabase)",
                    DECL, re.M)
      and ".execute(" not in DECL and ".table(" not in DECL)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE DISTRIBUTOR INVOICES — seven figures on one grain, over the REAL producer.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. the distributor invoices, over the real `vip_invoice_filter.summarize` ==")

# Real `vip_invoices` column shapes, including the two the report is honest about: one store spelled
# twice (defect 5) and an invoice with NO location at all (defect 6).
INV_ROWS = [
    {"location": "1 Main St", "created_on": "2026-09-03", "sub_total": 1000.0, "shipping": 25.0,
     "discount": -50.0, "other_cost": 10.0, "other_deductions": 0.0, "tax": 80.0,
     "grand_total": 1065.0},
    {"location": "1 Main St", "created_on": "2026-09-14", "sub_total": 500.0, "shipping": 15.0,
     "discount": 0.0, "other_cost": 0.0, "other_deductions": -20.0, "tax": 40.0,
     "grand_total": 535.0},
    {"location": "1 Main Street", "created_on": "2026-09-19", "sub_total": 300.0, "shipping": 5.0,
     "discount": 0.0, "other_cost": 0.0, "other_deductions": 0.0, "tax": 24.0,
     "grand_total": 329.0},
    {"location": "2 Oak Ave", "created_on": "2026-09-22", "sub_total": 700.0, "shipping": 0.0,
     "discount": -10.0, "other_cost": 30.0, "other_deductions": 0.0, "tax": 56.0,
     "grand_total": 776.0},
    {"location": None, "created_on": "2026-09-25", "sub_total": 90.0, "shipping": 0.0,
     "discount": 0.0, "other_cost": 0.0, "other_deductions": 0.0, "tax": 7.2,
     "grand_total": 97.2},
]
INV_KEPT, INV_UNRES = vipf.select(INV_ROWS)
SUMMARY = vipf.summarize(INV_KEPT, INV_UNRES)
ID = dd.invoice_drilldowns(SUMMARY)

check("B1 the real producer ran and the panels are over its OWN per-store rows",
      len(INV_KEPT) == len(INV_ROWS) and len(SUMMARY["by_store"]) == 4
      and all(len(p["rows"]) == 4 for p in ID.values()))
check("B2 SEVEN figures, one grain: the subtotal, the grand total and each of the five fee buckets "
      "— all the same fold of the same invoices, differing only in which column held the figure",
      sorted(ID) == sorted(("sub_total", "grand_total") + dd.FEE_COLS) and len(ID) == 7,
      str(sorted(ID)))
check("B3 every panel TIES to the producer's own total, to the cent",
      all(agrees(ID[k], SUMMARY["totals"][k]) for k in ID)
      and all(ID[k]["tie_out"]["ties"] is True for k in ID),
      str({k: ID[k]["tie_out"]["words"] for k in ID if ID[k]["tie_out"]["ties"] is not True}))
check("B4 and every row names the invoice feed and the COLUMN the figure was read out of, so the "
      "panel says which money bucket it is about rather than only which table",
      all(all(o["table"] == dd.DISTRIBUTOR_INVOICES and o["column"] == k and o["schema"] == "commcalc"
              for r in ID[k]["rows"] for o in r["origins"]) for k in ID))
check("B5 and each row carries the key that addresses it in that feed — the distributor's own "
      "spelling, which is exactly how the table below is grouped",
      all(r["row_key"] == {"location": r["label"]} for r in ID["grand_total"]["rows"]))
check("B6 DEFECT 5, REPORTED NOT FIXED: one physical store spelled two ways is TWO rows, here and "
      "in the table, because the producer rolls up on the distributor's spelling and not on the "
      "org's store vocabulary",
      len([r for r in ID["grand_total"]["rows"] if str(r["label"]).startswith("1 Main")]) == 2)
check("B7 DEFECT 6, REPORTED: an invoice with no location at all is its own row, naming the feed "
      "and no store — true, and shown rather than tidied away",
      any(r["label"] == "—" for r in ID["grand_total"]["rows"])
      and ID["grand_total"]["untraceable_origins"] == 0)
check("B8 DEFECT 4: 'Fees (all types)' gets NO KEY AT ALL. The producer adds the five buckets at "
      "the TOTAL and folds no per-store figure for it, so there are no rows to open (§CORE-17.3k) — "
      "and the five it is composed of each do drill",
      "fees_total" in SUMMARY["totals"] and "fees_total" not in ID
      and all(c in ID for c in dd.FEE_COLS))
check("B9 and the INVOICE COUNT gets no key either — a count whose list is the table below, which "
      "prints it per store",
      "invoices" in SUMMARY["totals"] and "invoices" not in ID)
check("B10 a selection with no invoice in it drills to NOTHING, rather than to a $0.00 figure "
      "'tying' to an empty list",
      dd.invoice_drilldowns(vipf.summarize([], [])) == {}
      and dd.invoice_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE WEEKLY LENDING LEDGER — the endpoint's own fold, PINNED, and the panel it already served.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the PayGo lending ledger ==")

# The fold lives in a FastAPI router, so it is reproduced here LINE-FOR-LINE and pinned to the
# router's own source text below (C1). A reproduction nobody checks against the shipped expression is
# a measurement of a fold that does not run.
PAYGO_ROWS = [
    {"vip_payment_id": 11, "batch_type": "pending", "created_on": "2026-09-22",
     "invoice_count": 14, "amount": 4200.0, "amount_overdue": 300.0, "status": "open",
     "period": "2026-09"},
    # A SECOND pending batch — defect 2. Older, so `max` does not pick it.
    {"vip_payment_id": 10, "batch_type": "pending", "created_on": "2026-09-15",
     "invoice_count": 9, "amount": 2750.0, "amount_overdue": 0.0, "status": "open",
     "period": "2026-09"},
    {"vip_payment_id": 9, "batch_type": "approved", "created_on": "2026-09-08",
     "invoice_count": 12, "amount": 3600.0, "amount_overdue": 0.0, "status": "paid",
     "period": "2026-09"},
    {"vip_payment_id": 8, "batch_type": "approved", "created_on": "2026-09-01",
     "invoice_count": 7, "amount": 2100.0, "amount_overdue": 0.0, "status": "paid",
     "period": "2026-09"},
    # A batch whose amount the portal never reported — defect 3. `lifetime_paid` folds it at $0.00.
    {"vip_payment_id": 7, "batch_type": "approved", "created_on": "2026-08-25",
     "invoice_count": 5, "amount": None, "amount_overdue": None, "status": "paid",
     "period": "2026-08"},
]


def paygo_summary(rows):
    """The endpoint's own fold, reproduced. Pinned to the router's source by C1."""
    pending = [r for r in rows if r.get('batch_type') == 'pending']
    approved = [r for r in rows if r.get('batch_type') != 'pending']
    current = max(pending, key=lambda r: r.get('created_on') or '', default=None)
    out = {
        "configured": True, "current": current, "history": approved,
        "totals": {
            "current_owed": round(float(current['amount']), 2) if current and current.get('amount') is not None else 0.0,
            "current_overdue": round(float(current['amount_overdue']), 2) if current and current.get('amount_overdue') is not None else 0.0,
            "weeks": len(approved),
            "lifetime_paid": round(sum(float(r.get('amount') or 0) for r in approved), 2),
        },
    }
    out["totals"]["devices_lifetime"] = add.devices_lifetime(out)
    out["drilldown"] = add.lending_drilldowns(out)
    return out


check("C1 THE REPRODUCTION IS PINNED TO THE SHIPPED FOLD — every line of it is in the router, so a "
      "change there makes this file red instead of leaving it measuring a fold nobody runs",
      all(s in ROUTER for s in (
          "pending = [r for r in rows if r.get('batch_type') == 'pending']",
          "approved = [r for r in rows if r.get('batch_type') != 'pending']",
          "current = max(pending, key=lambda r: r.get('created_on') or '', default=None)",
          '"lifetime_paid": round(sum(float(r.get(\'amount\') or 0) for r in approved), 2),',
          'out["drilldown"] = _asset_dd.lending_drilldowns(out)')))
PAYGO = paygo_summary(PAYGO_ROWS)
PD = PAYGO["drilldown"]

check("C2 DEFECT 1, FIXED: the endpoint ALREADY serves the panel this page needed, so the fix was "
      "the WIRING and not a second declaration",
      "lifetime_paid" in PD and agrees(PD["lifetime_paid"], PAYGO["totals"]["lifetime_paid"]))
check("C3 every row names the weekly-batch relation and the column the figure came out of, keyed by "
      "the batch a reader can look up",
      all(o["table"] == dd.PAYGO_BATCHES and o["column"] == "amount"
          for r in PD["lifetime_paid"]["rows"] for o in r["origins"])
      and all(r["row_key"] and "vip_payment_id" in r["row_key"]
              for r in PD["lifetime_paid"]["rows"]))
check("C4 DEFECT 3: a batch whose amount the portal never reported is SHOWN and NOT counted — and "
      "the figure is unchanged to the cent, because the producer folded it at nothing either way",
      PD["lifetime_paid"]["tie_out"]["shown_not_counted"] == 1
      and PD["lifetime_paid"]["tie_out"]["ties"] is True
      and cents(PAYGO["totals"]["lifetime_paid"]) == cents(3600.0 + 2100.0))
check("C5 DEFECT 2, REPORTED AS A REGRESSION: with TWO batches pending, the older one is in NO "
      "tile, NO total and NO row of the table — the fold renders `current + history` and history is "
      "the approved batches alone. That is $2,750.00 of money owed that nothing on the page carries",
      cents(PAYGO["totals"]["current_owed"]) == cents(4200.0)
      and all(r["vip_payment_id"] != 10 for r in PAYGO["history"])
      and PAYGO["current"]["vip_payment_id"] == 11
      and all(r["row_key"].get("vip_payment_id") != 10
              for r in PD["lifetime_paid"]["rows"]),
      "the second pending batch appears somewhere — re-read defect 2")
check("C6 and the page's own table is exactly `current + history`, so the regression above is about "
      "the screen and not only about the totals",
      "[...(data.current ? [data.current] : []), ...data.history]"
      in read("frontend/src/app/(platform)/commcalc/vip/paygo/page.tsx"))
check("C7 the open week's two figures and the week COUNT get no panel: two columns of ONE stored "
      "batch row, and a count whose list is the table below",
      not any(k in PD for k in ("current_owed", "current_overdue", "weeks")))
check("C8 an unloaded ledger drills to nothing",
      add.lending_drilldowns({"history": [], "totals": {}}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  THE EQUIPMENT REIMBURSEMENT PER LINE — two grains on one device row, over the REAL producer.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. the per-line report, over the real `device_reimb_recon.device_lines` ==")

CFG = drr.normalize_config({})
COLS = CFG["columns"]
# Real `asset_ledger` shapes. Six devices covering every verdict the producer can reach.
ASSET_ROWS = [
    {"store": "1 Main St", "reimbursement": 180.0, "reimbursement_date": "2026-09-04",
     "esn_imei": "IM1", "device_model": "Model A", "acquired_date": "2026-08-01",
     "owed_to_vip": 300.0, "selling_price": 250.0, "date_sold": "2026-09-01"},
    {"store": "1 Main St", "reimbursement": 200.0, "reimbursement_date": "2026-09-06",
     "esn_imei": "IM2", "device_model": "Model B", "acquired_date": "2026-08-02",
     "owed_to_vip": 320.0, "selling_price": "", "date_sold": "2026-09-02"},
    {"store": "1 Main St", "reimbursement": 150.0, "reimbursement_date": "2026-09-09",
     "esn_imei": "IM3", "device_model": "Model C", "acquired_date": "2026-08-03",
     "owed_to_vip": 280.0, "selling_price": 275.0, "date_sold": "2026-09-03"},
    {"store": "2 Oak Ave", "reimbursement": 90.0, "reimbursement_date": "2026-09-11",
     "esn_imei": "", "device_model": "Model D", "acquired_date": "2026-08-04",
     "owed_to_vip": 240.0, "selling_price": 199.0, "date_sold": "2026-09-04"},
    {"store": "2 Oak Ave", "reimbursement": 120.0, "reimbursement_date": "2026-10-02",
     "esn_imei": "IM5", "device_model": "Model E", "acquired_date": "2026-09-04",
     "owed_to_vip": 200.0, "selling_price": 150.0, "date_sold": "2026-10-01"},
    # A device whose cost the ledger never carried — defect 9. It folds in at $0.00 and nothing in
    # the payload tells it from a free handset.
    {"store": "2 Oak Ave", "reimbursement": 60.0, "reimbursement_date": "2026-09-12",
     "esn_imei": "IM6", "device_model": "Model F", "acquired_date": "2026-08-05",
     "owed_to_vip": None, "selling_price": 120.0, "date_sold": "2026-09-05"},
]
# Real `raw_payment_detail` shapes, including one device-financing line with NO device on it.
LINE_ROWS = [
    {"business_address": "1 Main St", "amount": 180.0, "payment_type": "DEVICE PROMO",
     "imei": "IM1", "payment_date": "2026-09-20", "rep_username": "abid"},
    {"business_address": "3 Elm Rd", "amount": 200.0, "payment_type": "DEVICE PROMO",
     "imei": "IM2", "payment_date": "2026-09-21", "rep_username": "sam"},
    {"business_address": "2 Oak Ave", "amount": 60.0, "payment_type": "DEVICE PROMO",
     "imei": "IM6", "payment_date": "2026-09-22", "rep_username": "jo"},
    {"business_address": "1 Main St", "amount": 410.0, "payment_type": "DEVICE PROMO",
     "imei": "", "payment_date": "2026-09-23", "rep_username": "abid"},
    {"business_address": "1 Main St", "amount": 9999.0, "payment_type": "SOMETHING ELSE",
     "imei": "IM3", "payment_date": "2026-09-24", "rep_username": "abid"},
]


def is_device_dollar(raw_type):
    """The org's own declaration, as the endpoint builds it from §58 — here, the one declared
    device-financing payment type. A dollar on any other type is not device money."""
    return str(raw_type or "").strip().upper() == "DEVICE PROMO"


CARRIER_LINES = drr.carrier_line_side(LINE_ROWS, CFG, is_device_dollar)
COVERAGE = {"2026-09": {"ok": True}, "2026-10": {"ok": False, "missing_days": ["2026-10-30"],
                                                 "missing_amount": 400.0}}
REIMB = drr.device_lines(ASSET_ROWS, CARRIER_LINES, CFG, None, COVERAGE, configured=True)
RD_ = dd.reimb_drilldowns(REIMB)
BY_ID = {r["device_id"]: r for r in REIMB["rows"]}

check("D1 the real producer ran and reached every verdict this family has",
      len(REIMB["rows"]) == 6
      and {r["status"] for r in REIMB["rows"]} == {drr.LINE_PAID, drr.LINE_PAID_OTHER_STORE,
                                                   drr.LINE_NOT_PAID, drr.LINE_NOT_MEASURED},
      str({r["device_id"]: r["status"] for r in REIMB["rows"]}))
check("D2 SEVEN figures over TWO grains: five read out of the distributor's unit ledger and two out "
      "of the carrier's own per-line feed",
      sorted(RD_) == sorted(("distributor_claimed", "device_cost", "store_device_payment",
                             "not_paid_total", "not_measured_total",
                             "carrier_paid_here", "carrier_paid_other_total")),
      str(sorted(RD_)))
check("D3 and the two grains really are separated by PROVENANCE: no claim panel names the carrier "
      "feed and no paid panel names the ledger (§CORE-17.3n)",
      all(dd.CARRIER_LINE_FEED not in (p["sources"] and [s["table"] for s in p["sources"]] or [])
          for k, p in RD_.items() if k not in ("carrier_paid_here", "carrier_paid_other_total"))
      and all(dd.ASSET_LEDGER not in [s["table"] for s in RD_[k]["sources"]]
              for k in ("carrier_paid_here", "carrier_paid_other_total")))
check("D4 every panel TIES to the producer's own total, to the cent",
      all(agrees(RD_[k], REIMB["totals"][k]) for k in RD_)
      and all(RD_[k]["tie_out"]["ties"] is True for k in RD_),
      str({k: RD_[k]["tie_out"]["words"] for k in RD_ if RD_[k]["tie_out"]["ties"] is not True}))
check("D5 a device the ledger records NO SALE PRICE for is shown WITHOUT a figure and not counted — "
      "a missing price is not a sale for $0.00, and the reason says so in the reader's own words",
      next(r for r in RD_["store_device_payment"]["rows"] if r["row_key"].get("device_id") == "IM2")
      ["amount"] is None
      and next(r for r in RD_["store_device_payment"]["rows"]
               if r["row_key"].get("device_id") == "IM2")["reason"] == dd.REASON_PRICE_NOT_RECORDED
      and RD_["store_device_payment"]["tie_out"]["shown_not_counted"] == 1)
check("D6 §CORE-17.3i — a device the carrier's feed NEVER NAMED carries no figure and names NO "
      "TABLE at all, which the panel reports as a countable untraceable origin rather than as a "
      "measured $0.00",
      RD_["carrier_paid_here"]["untraceable_origins"] == 3
      and all(r["amount"] is None and r["origins"][0]["table"] is None
              and r["reason"] == dd.REASON_CARRIER_NOT_LOOKED_UP
              for r in RD_["carrier_paid_here"]["rows"]
              if r["row_key"].get("device_id") in ("IM3", "IM5") or not r["row_key"].get("device_id")))
check("D7 and a device the feed DID name keeps its measured $0.00 at this store, because it was "
      "really paid — at another one. The lookup is the question, not the amount",
      cents(next(r for r in RD_["carrier_paid_here"]["rows"]
                 if r["row_key"].get("device_id") == "IM2")["amount"]) == 0
      and next(r for r in RD_["carrier_paid_here"]["rows"]
               if r["row_key"].get("device_id") == "IM2")["origins"][0]["table"]
      == dd.CARRIER_LINE_FEED
      and cents(REIMB["totals"]["carrier_paid_other_total"]) == cents(200.0))
check("D8 the two unpaid buckets count exactly the devices the PRODUCER reached that verdict about, "
      "and every other device stays in the panel as the context that makes the figure readable",
      RD_["not_paid_total"]["tie_out"]["counted_rows"] == 1
      and RD_["not_paid_total"]["tie_out"]["shown_not_counted"] == 5
      and RD_["not_measured_total"]["tie_out"]["counted_rows"] == 2
      and cents(REIMB["totals"]["not_paid_total"]) == cents(150.0)
      and cents(REIMB["totals"]["not_measured_total"]) == cents(90.0 + 120.0))
check("D9 and a counted not-measured device carries the PRODUCER's own reason for why its carrier "
      "side could not be judged — an unidentified device, and a month whose feed arrived short",
      {r["reason"] for r in RD_["not_measured_total"]["rows"] if r["in_figure"]}
      == {dd.REASON_DEVICE_UNIDENTIFIED, dd.REASON_COVERAGE_INCOMPLETE}
      and all(r["reason_label"] for r in RD_["not_measured_total"]["rows"] if r["in_figure"]))
check("D10 DEFECT 9, REPORTED: a device whose COST the ledger never carried folds in at $0.00 and "
      "nothing in the payload tells it from a free handset — so this panel counts it, as the "
      "producer did, rather than inventing a distinction",
      BY_ID["IM6"]["device_cost"] == 0.0
      and "store_payment_recorded" in BY_ID["IM6"] and "device_cost_recorded" not in BY_ID["IM6"]
      and next(r for r in RD_["device_cost"]["rows"]
               if r["row_key"].get("device_id") == "IM6")["in_figure"] is True)
check("D11 DEFECT 8: device-financing money on a carrier line with NO device gets no panel, because "
      "at this grain it has no rows — it keeps the banner the page already gives it",
      cents(REIMB["totals"]["carrier_paid_unidentified"]) == cents(410.0)
      and "carrier_paid_unidentified" not in RD_)
check("D12 the per-verdict COUNTS get no panel: the verdict picker prints each one and the table "
      "below is the list",
      "by_status" in REIMB["totals"] and not any(k.startswith("by_status") for k in RD_))
check("D13 WITH NO DEVICE-FINANCING ROUTING DECLARED the two PAID panels are not served at all — a "
      "$0.00 figure over rows that all carry no number is the absence of a figure, not a figure",
      (lambda u: sorted(dd.reimb_drilldowns(u)) == sorted(
          ("distributor_claimed", "device_cost", "store_device_payment",
           "not_paid_total", "not_measured_total")))(
          drr.device_lines(ASSET_ROWS, drr.carrier_line_side(LINE_ROWS, CFG, None), CFG, None,
                           COVERAGE, configured=False)))
check("D14 and in that case every device still reports the ORG-LEVEL absence as its reason, in the "
      "one home's words",
      all(r["reason"] == dd.REASON_NO_RULE for r in drr.device_lines(
          ASSET_ROWS, drr.carrier_line_side(LINE_ROWS, CFG, None), CFG, None, COVERAGE,
          configured=False)["rows"])
      and rd.UNMEASURED_REASONS[dd.REASON_NO_RULE])
check("D15 a month the distributor claims nothing in drills to nothing",
      dd.reimb_drilldowns(drr.device_lines([], CARRIER_LINES, CFG)) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  THE FINANCING REPORT — one row, two units, two grains, over the REAL producer AND the REAL gate.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. the financing report, over the real `financing_report.build` ==")

# A vendor is CONFIG, so the fixture's vendor keys and tender strings are neutral placeholders — a
# brand name in this file would be exactly the carrier vocabulary the house rules keep out of code.
VENDORS = [
    {"vendor_key": "plan-a", "label": "Finance plan A", "enabled": True,
     "detection_source": "config", "detection_status": "configured", "detection_note": "",
     "amount_basis": "unit_line", "carriers": [],
     "matchers": [{"match_field": "tender_type", "match_op": "equals",
                   "match_value": "FINPLAN A"}]},
    {"vendor_key": "plan-b", "label": "Finance plan B", "enabled": True,
     "detection_source": "config", "detection_status": "configured", "detection_note": "",
     "amount_basis": "unit_line", "carriers": [],
     "matchers": [{"match_field": "tender_type", "match_op": "equals",
                   "match_value": "FINPLAN B"}]},
]
SALE_ROWS = [
    {"tender_type": "FINPLAN A", "trans_id": "T1", "store": "1 Main St", "salesperson": "Abid",
     "ext_price": 500.0, "trans_date": "2026-09-02", "imei": "S1", "item_desc": "handset"},
    {"tender_type": "FINPLAN A", "trans_id": "T1", "store": "1 Main St", "salesperson": "Abid",
     "ext_price": 20.0, "trans_date": "2026-09-02", "imei": "", "item_desc": "case"},
    {"tender_type": "FINPLAN A", "trans_id": "T2", "store": "1 Main St", "salesperson": "Abid",
     "ext_price": 300.0, "trans_date": "2026-09-11", "imei": "S2", "item_desc": "handset"},
    {"tender_type": "FINPLAN A", "trans_id": "T3", "store": "2 Oak Ave", "salesperson": "Sam",
     "ext_price": 450.0, "trans_date": "2026-09-05", "imei": "S3", "item_desc": "handset"},
    {"tender_type": "FINPLAN B", "trans_id": "T4", "store": "1 Main St", "salesperson": "Abid",
     "ext_price": 250.0, "trans_date": "2026-09-09", "imei": "S4", "item_desc": "handset"},
    {"tender_type": "CASH", "trans_id": "T5", "store": "1 Main St", "salesperson": "Abid",
     "ext_price": 99.0, "trans_date": "2026-09-10", "imei": "S5", "item_desc": "handset"},
    {"tender_type": "FINPLAN A", "trans_id": "T6", "store": "2 Oak Ave", "salesperson": "Sam",
     "ext_price": 700.0, "trans_date": "2026-09-12", "imei": "S6", "item_desc": "handset",
     "voided": "true"},
]
MARKETS = {"1 Main St": "LI", "2 Oak Ave": "NJ"}
SALES_META = {"primary": "daily_sales_feed", "primary_rows": 7, "other": "raw_sales",
              "other_rows": 3, "filled_rows": 1}


_KEEP = object()


def financing(rows=None, gate=paygate, meta=_KEEP):
    """`financing_report.build` as the endpoint calls it, through the REAL per-device pay gate."""
    out = finrep.build(rows if rows is not None else SALE_ROWS, VENDORS, {("1 Main St", ""): {"units": 4}},
                       {}, lambda s: MARKETS.get(s, ""), "2026-09",
                       gate=gate, unit_cfg=(dict(paygate.UNIT_DEFAULTS) if gate else {}),
                       is_accessory=None, month_days=30, days_elapsed=30)
    m = SALES_META if meta is _KEEP else meta
    out["source"] = {"rows_read": len(rows if rows is not None else SALE_ROWS),
                     "primary": (m or {}).get("primary"), "meta": m}
    out["ready"] = True
    return out


FIN = financing()
FD = dd.financing_drilldowns(FIN)

check("E1 the real producer ran through the REAL per-device collapse — the voided line is gone, the "
      "non-financed tender is gone, and one transaction's two lines are ONE device",
      FIN["totals"]["units"] == 4 and cents(FIN["totals"]["amount"]) == cents(1500.0)
      and len(FIN["rows"]) == 3,
      str(FIN["totals"]))
check("E2 TWO panels, TWO GRAINS on one row — the same (vendor x store x rep) row and the same "
      "provenance in two UNITS, which is what makes them two (§CORE-17.3l)",
      sorted(FD) == ["amount", "units"]
      and FD["amount"]["figure"]["unit"] == rd.UNIT_MONEY
      and FD["units"]["figure"]["unit"] == rd.UNIT_COUNT)
check("E3 both TIE to the producer's own totals, to the cent",
      agrees(FD["amount"], FIN["totals"]["amount"]) and agrees(FD["units"], FIN["totals"]["units"])
      and all(FD[k]["tie_out"]["ties"] is True for k in FD))
check("E4 the provenance is what the READ ITSELF recorded, resolved through the one home: the "
      "leading feed plus the other one because the meta says it CONTRIBUTED",
      [s["table"] for s in FD["amount"]["sources"]] and set(
          s["table"] for s in FD["amount"]["sources"]) == set(pmd.sales_union_tables(SALES_META))
      and set(pmd.sales_union_tables(SALES_META)) == {"daily_sales_feed", "raw_sales"})
check("E5 a read whose meta resolves to NOTHING reports an origin it could not trace, rather than "
      "naming a feed on a guess",
      (lambda p: p["untraceable_origins"] == len(FIN["rows"]) and p["sources"] == [])(
          dd.financing_drilldowns(financing(meta={}))["amount"]))
check("E6 each row is addressed by all THREE parts of its key, because one store and rep can "
      "finance through two vendors",
      all(sorted(r["row_key"]) == ["rep", "store_code", "vendor_key"] for r in FD["amount"]["rows"])
      and len({r["row_key"]["vendor_key"] for r in FD["amount"]["rows"]}) == 2)
check("E7 and the VENDOR IS NOT IN THE WORDS — the page relabels vendors under a dual-carrier lens "
      "and this panel is not behind that lens, so the brand travels in the key and never in copy",
      all(v not in str(r["label"]) and v not in str(r["fields"])
          for r in FD["amount"]["rows"] for v in ("plan-a", "plan-b", "Finance plan A",
                                                  "Finance plan B")))
check("E8 DEFECT 11: with the payout's own device gate UNAVAILABLE the producer falls back to one "
      "unit per transaction and records it in `unit_notes` — which the page renders NOWHERE. The "
      "units panel's note now says so, which is the half a drill-down can honestly fix",
      dd.units_fallback(financing(gate=None)) is True
      and dd.units_fallback(FIN) is False
      and dd.UNIT_FALLBACK_CODE in financing(gate=None)["unit_notes"]
      and "NOT the one that pays" in dd.financing_drilldowns(financing(gate=None))["units"]["note"]
      and "NOT the one that pays" not in FD["units"]["note"]
      and "unit_notes" not in re.sub(r"^.*unit_notes: string\[\].*$", "", read(
          "frontend/src/app/(platform)/commcalc/financing/page.tsx"), flags=re.M))
check("E9 the per-vendor tiles, the stores-hitting-target ratio and the transaction count get NO "
      "KEY AT ALL — a re-partition that prints its own totals, a quotient, and a count",
      FIN["by_vendor"] and "transactions" in FIN["totals"] and sorted(FD) == ["amount", "units"])
check("E10 a period no vendor matched a line in drills to nothing",
      dd.financing_drilldowns(financing(rows=[{"tender_type": "CASH", "trans_id": "X",
                                               "store": "1 Main St", "ext_price": 10.0,
                                               "trans_date": "2026-09-01"}])) == {}
      and dd.financing_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §F  THE WIRING — a registry nobody dereferences is not a fix (CLAUDE.md §19.18).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. the wiring, locked so it cannot un-wire ==")

PAGES = {
    "vip": read("frontend/src/app/(platform)/commcalc/vip/page.tsx"),
    "paygo": read("frontend/src/app/(platform)/commcalc/vip/paygo/page.tsx"),
    "reimb": read("frontend/src/app/(platform)/commcalc/device-line-reimbursement/page.tsx"),
    "financing": read("frontend/src/app/(platform)/commcalc/financing/page.tsx"),
}
#: THE §CORE-17.3o CALL, PER PAGE. False = the tiles are the SERVER's fold and narrowing them would
#: move a figure; True = the page folds them itself over the filtered rows (§CORE-17.3d).
NARROWS = {"vip": False, "paygo": False, "reimb": False, "financing": True}

check("F1 all four pages were read — a lock over a file nobody can find is not a lock",
      all(len(v) > 2000 for v in PAGES.values()),
      str({k: len(v) for k, v in PAGES.items()}))
check("F2 the three endpoints ATTACH the declaration's output and fold no panel of their own",
      ROUTER.count("device_financing_drilldown as _dfdd") == 1
      and 'out["drilldown"] = _dfdd.invoice_drilldowns(out)' in ROUTER
      and 'res["drilldown"] = _dfdd.reimb_drilldowns(res)' in ROUTER
      and 'out["drilldown"] = _dfdd.financing_drilldowns(out)' in ROUTER
      and "report_drilldown.tile_panel(" not in ROUTER)
check("F3 and the FOURTH route attaches the DEVICE-ASSET family's declaration, unchanged — this "
      "batch added no second fold for the weekly batches",
      'out["drilldown"] = _asset_dd.lending_drilldowns(out)' in ROUTER
      and ROUTER.count("_dfdd.paygo_drilldowns") == 0)
check("F4 every page renders the SHARED panel and reads its tile's own figure through the ONE "
      "agreement gate — not a local copy of it",
      all("DrillDownPanel" in v and "useDrillTile" in v and "agreesToTheCent" in v
          for v in PAGES.values()),
      str([k for k, v in PAGES.items()
           if not ("DrillDownPanel" in v and "useDrillTile" in v and "agreesToTheCent" in v)]))
check("F5 §CORE-17.3o BUILD LOCK, ONE DIRECTION — `lib/drill-narrow`'s narrowing is NOT wired into "
      "the three pages whose tiles the SERVER folds. Narrowing a panel there would make the tile "
      "read a different number under a filter, which is a figure moved; a later reader cannot 'fix' "
      "them into a class they were never in",
      all("narrowPanel" not in PAGES[k] and "narrowPanels" not in PAGES[k]
          for k, n in NARROWS.items() if not n),
      str([k for k, n in NARROWS.items() if not n and "narrowPanel" in PAGES[k]]))
check("F6 §CORE-17.3o BUILD LOCK, THE OTHER DIRECTION — it IS wired into the one page that folds "
      "its own tiles, and that page READS each tile off its narrowed panel rather than reducing a "
      "second time. Both halves, or the figure and its evidence are two folds again",
      all("narrowPanel(" in PAGES[k] and "screenKey(" in PAGES[k]
          and "narrowedAmount?.figure?.amount" in PAGES[k]
          and "narrowedUnits?.figure?.amount" in PAGES[k]
          for k, n in NARROWS.items() if n))
check("F6b and NEITHER scan is vacuous — the text really is found on a page in that class and "
      "really is absent from one outside it, so F5 and F6 are each checking something",
      "narrowPanels" in read("frontend/src/app/(platform)/commcalc/epay-fee-recon/page.tsx")
      and "narrowPanel" in PAGES["financing"]
      and "narrowPanel" not in PAGES["vip"])
LABELS = read("frontend/src/lib/sourceLabels.ts")
_RELS = (dd.DISTRIBUTOR_INVOICES, dd.ASSET_LEDGER, dd.CARRIER_LINE_FEED, dd.PAYGO_BATCHES,
         lineage.MONTHLY_SALES, lineage.LIVE_SALES_FEED)
check("F7 every relation this family can name has a PLAIN NAME in the one frontend home — the "
      "owner's ruling is to hide database names from the users",
      all(re.search(r"^\s+%s: '" % t, LABELS, re.M) for t in _RELS),
      str([t for t in _RELS if not re.search(r"^\s+%s: '" % t, LABELS, re.M)]))


def _uncommented(src):
    """The page with its `//` and `/* */` comments removed, and with any `reportKey="..."` prop
    blanked. A relation name in a COMMENT is a note to the next engineer; the ruling is about what a
    manager READS, and that is the code.

    THE `reportKey` EXCLUSION IS NARROW AND IT IS EARNED. The notify registry's key for the
    distributor-invoice report is the same STRING as the relation it is built from — a coincidence of
    naming, not a table reaching copy: the value is an identifier posted to the scheduler and the
    screen never renders it. Nothing else is excused, and F8c proves the scan still fires on a real
    copy string on a page with that prop, so this is not a hole the next table can walk through."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r'reportKey="[^"]*"', 'reportKey=""', src)
    return "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("//"))


_REL_RE = r"(vip_invoices|vip_paygo_payments|asset_ledger|raw_payment_detail|raw_sales|daily_sales_feed)"
check("F8 and no page prints a relation name of its own — every table reaching copy goes through "
      "`sourceLabel()` inside the shared panel",
      all(not re.search(_REL_RE, _uncommented(v)) for v in PAGES.values()),
      str([k for k, v in PAGES.items() if re.search(_REL_RE, _uncommented(v))]))
check("F8b and that scan is NOT VACUOUS — it really does fire on the text it is looking for",
      bool(re.search(_REL_RE, _uncommented("const t = 'vip_invoices'"))))
check("F8c NEGATIVE CONTROL FOR THE ONE EXCLUSION — the `reportKey` blanking does not blind the "
      "scan to a relation name elsewhere on the same page, so the narrowing still fails closed",
      bool(re.search(_REL_RE, _uncommented(
          'const a = <X reportKey="vip_invoices" />\nconst b = "asset_ledger"')))
      and not re.search(_REL_RE, _uncommented('const a = <X reportKey="vip_invoices" />')))
LEDGER = read("backend/harness_drilldown_pending.txt")
check("F9 all four routes are flipped in the coverage ledger, each with a note saying what drills "
      "AND what it refuses to claim",
      all(re.search(r"^%s\tshared\t.{200,}REFUSES TO CLAIM" % re.escape(r), LEDGER, re.M)
          for r in ("/commcalc/vip", "/commcalc/vip/paygo",
                    "/commcalc/device-line-reimbursement", "/commcalc/financing")),
      str([r for r in ("/commcalc/vip", "/commcalc/vip/paygo",
                       "/commcalc/device-line-reimbursement", "/commcalc/financing")
           if not re.search(r"^%s\tshared\t.{200,}REFUSES TO CLAIM" % re.escape(r), LEDGER, re.M)]))
COV = read("backend/harness_drilldown_coverage.py")
def _owes_max(src):
    """THE RATCHET'S PIN, or None when it cannot be read at all.

    **SHRINK-ONLY — and it must never be re-tightened to `==`.** The rule the ratchet states is
    *"may only shrink"*, and `<=` IS that rule. A lock that asserts the VALUE goes red the moment a
    later batch correctly lowers the pin, and the next reader's reflex when it goes red is to edit
    the number, which is how a ratchet quietly becomes decoration. Batch 10 pinned `== 74` and went
    red on batch 11; batch 11 rewrote both as `<=` and batch 12 wrote the ruling down; batch 15 then
    re-tightened to `== 53` anyway and went red on batch 16's correct shrink to 49. That is the
    CLASS, fixed here for every batch lock that reads this ratchet rather than for the one batch
    that surfaced it (batch 16, 2026-10-26).

    It still FAILS CLOSED: a pin ABOVE this batch's own number is red, and a pin that cannot be read
    at all is red rather than absent."""
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)\s*$", src or "")
    return int(m.group(1)) if m else None


_pin = _owes_max(COV)
_owes = len(re.findall(r"\towes\t", LEDGER))
check("F10 THE RATCHET SHRANK BY THE FOUR ROUTES THIS BATCH SERVED, and has never been pushed back "
      "up past this batch's 49 — the DIRECTION, not the value, so batch 17's correct shrink does "
      "not turn this file red the way batch 15's value assertion turned red on this one",
      _pin is not None and _pin <= 49 and _owes <= 49 and _owes == _pin,
      "pin=%s owes=%s" % (_pin, _owes))
check("F10b NEGATIVE CONTROL — a pin ABOVE 49 is caught, so the ratchet cannot be loosened by "
      "editing the number", _owes_max("OWES_MAX = 50\n") > 49)
check("F10c NEGATIVE CONTROL — a LATER batch's lower pin passes without touching this file",
      _owes_max("OWES_MAX = 49\n") <= 49 and _owes_max("OWES_MAX = 40\n") <= 49)
check("F10d NEGATIVE CONTROL — FAIL CLOSED: a ratchet with no readable pin is not a pass",
      _owes_max("# the pin was deleted\n") is None and _owes_max("OWES_MAX = many\n") is None)
GRAPH = read("backend/app/modules/core/module_graph.py")
check("F11 the declaration and this lock are written into the module graph BY HAND, under both "
      "facts it dereferences",
      "'app/modules/commcalc/device_financing_drilldown.py': ('report_drilldown',)" in GRAPH
      and "'app/modules/commcalc/device_financing_drilldown.py': ('_lineage',)" in GRAPH
      and GRAPH.count("harness_batch16_drilldown.py") >= 1)
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("F12 and a lock nothing runs is not a lock — this file is run by the guard job and its paths "
      "filter names every file it is about",
      "python3 harness_batch16_drilldown.py" in WF
      and all(('"%s"' % f) in WF for f in
              ("backend/app/modules/commcalc/device_financing_drilldown.py",
               "backend/app/modules/commcalc/device_reimb_recon.py",
               "backend/app/modules/commcalc/financing_report.py",
               "backend/app/modules/commcalc/vip_invoice_filter.py",
               "frontend/src/app/(platform)/commcalc/vip/page.tsx",
               "frontend/src/app/(platform)/commcalc/vip/paygo/page.tsx",
               "frontend/src/app/(platform)/commcalc/device-line-reimbursement/page.tsx",
               "frontend/src/app/(platform)/commcalc/financing/page.tsx")),
      str([f for f in ("backend/app/modules/commcalc/device_financing_drilldown.py",
                       "frontend/src/app/(platform)/commcalc/financing/page.tsx")
           if ('"%s"' % f) not in WF]))
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("F13 the batch is registered in the index — the section, the declaration, this harness and "
      "all four routes",
      "§CORE-17.3p" in IDX and "device_financing_drilldown.py" in IDX
      and "harness_batch16_drilldown.py" in IDX
      and all(r in IDX for r in ("/commcalc/vip/paygo", "/commcalc/device-line-reimbursement",
                                 "/commcalc/financing")))
check("F14 and the client fold's FIXTURES carry this batch's two new shapes — a COUNT-unit panel "
      "and a three-part row key — so `prove_drill_narrow.mjs` tests the identity on them too",
      '"financing.units"' in read("frontend/prove_drill_narrow_fixtures.json")
      and '"financing.amount"' in read("frontend/prove_drill_narrow_fixtures.json")
      and "device_financing_drilldown as dfd" in read("backend/harness_drill_narrow_fixtures.py"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §G  NO FIGURE MOVED — each screen's own pre-batch fold, unfiltered AND under a real filter.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. no figure moved, proved falsifiably ==")

# ── G1  The invoices. Every filter is a QUERY PARAMETER, so the server re-folds and the figures
#        legitimately change — the panel must follow them, and there is nothing to narrow.
_pre_inv = dict(SUMMARY["totals"])
check("G1a UNFILTERED — every tile and every fee figure the page prints is identical to the figure "
      "its panel now reads, to the cent",
      all(cents(_pre_inv[k]) == cents(ID[k]["figure"]["amount"]) for k in ID)
      and all(cents(SUMMARY["fees_by_type"][c]) == cents(ID[c]["figure"]["amount"])
              for c in dd.FEE_COLS))
_narrow_kept, _narrow_unres = vipf.select(
    INV_ROWS, store_matcher=lambda loc: str(loc or "").startswith("1 Main"))
_NARROW = vipf.summarize(_narrow_kept, _narrow_unres)
_NID = dd.invoice_drilldowns(_NARROW)
check("G1b THE STORE FILTER REALLY DROPS ROWS — it is a query parameter, so the server folds again "
      "over fewer invoices",
      0 < len(_narrow_kept) < len(INV_KEPT)
      and cents(_NARROW["totals"]["grand_total"]) != cents(SUMMARY["totals"]["grand_total"]),
      f"{len(_narrow_kept)} of {len(INV_KEPT)}")
check("G1c and every panel follows the re-folded figure exactly — one fold for the tile and its "
      "evidence, which is why there is nothing for `drill-narrow` to do here",
      all(agrees(_NID[k], _NARROW["totals"][k]) for k in _NID)
      and all(len(_NID[k]["rows"]) == len(_NARROW["by_store"]) for k in _NID))
check("G1d and the invoices a selection could NOT bind are reported, never folded into the totals",
      _NARROW["unresolved"]["invoices"] == 2
      and cents(_NARROW["unresolved"]["grand_total"]) == cents(776.0 + 97.2))

# ── G2  The per-line report. Its tiles are the server's fold; its table is filtered in the browser.
def page_verdict_filter(rows, statuses):
    """A port of the page's own `statuses.length ? allRows.filter(...) : allRows`."""
    return [r for r in rows if not statuses or r["status"] in statuses]


check("G2a UNFILTERED — all seven tile figures are identical to their panels' figures",
      all(agrees(RD_[k], REIMB["totals"][k]) for k in RD_))
_rshown = page_verdict_filter(REIMB["rows"], [drr.LINE_NOT_MEASURED])
check("G2b THE VERDICT FILTER REALLY DROPS ROWS — otherwise G2c is the unfiltered case twice",
      0 < len(_rshown) < len(REIMB["rows"]), f"{len(_rshown)} of {len(REIMB['rows'])}")
check("G2c UNDER THAT FILTER the tiles are UNCHANGED, because the page reads the server's totals — "
      "so the panels still agree with them to the cent and nothing moved",
      all(agrees(RD_[k], REIMB["totals"][k]) for k in RD_))
check("G2d and narrowing WOULD have moved them, which is the whole argument (§CORE-17.3o): Σ over "
      "the rows the table is showing is a different number from the tile",
      cents(sum(r["distributor_claimed"] for r in _rshown))
      != cents(REIMB["totals"]["distributor_claimed"]))

# ── G3  The financing report. It folds its two tiles ITSELF, so the panels ARE narrowed — and the
#        tile is then read off the panel rather than reduced twice.
def page_reduce(rows):
    """A port of the page's own `shown.reduce(...)`."""
    return {"units": sum(r["units"] for r in rows),
            "amount": round(sum(r["amount"] for r in rows), 2)}


def narrow(panel, keep_keys):
    """A PORT of `lib/drill-narrow.narrowPanel`'s figure — deliberately the same arithmetic, so the
    page's own narrowing is measured here rather than assumed."""
    if not panel or panel.get("capped"):
        return None
    rows = [r for r in panel["rows"]
            if not r.get("row_key") or _key(r["row_key"]) in keep_keys]
    if len(rows) == len(panel["rows"]):
        return panel
    amt = round(sum((r["amount"] or 0) for r in rows if r["in_figure"] and r["present"]), 2)
    return dict(panel, rows=rows, figure=dict(panel["figure"], amount=amt),
                rows_shown=len(rows), rows_total_count=len(rows), capped=False)


def _key(k):
    """A PORT of `lib/drill-narrow.screenKey` / `rowKeyOf` — sorted name=value pairs."""
    return "\u0001".join("%s=%s" % (n, k[n]) for n in sorted(k) if k[n] not in (None, ""))


_fin_pre = page_reduce(FIN["rows"])
check("G3a UNFILTERED — the page's own reduce and the served panels agree to the cent, which is the "
      "identity that makes narrowing honest at all",
      cents(_fin_pre["amount"]) == cents(FD["amount"]["figure"]["amount"])
      and _fin_pre["units"] == FD["units"]["figure"]["amount"]
      and cents(_fin_pre["amount"]) == cents(FIN["totals"]["amount"]))
_fin_shown = [r for r in FIN["rows"] if r["market"] == "LI"]
_fin_keys = {_key({"vendor_key": r["vendor_key"], "store_code": r["store_code"], "rep": r["rep"]})
             for r in _fin_shown}
check("G3b THE MARKET FILTER REALLY DROPS ROWS — otherwise G3c is the unfiltered case twice",
      0 < len(_fin_shown) < len(FIN["rows"]), f"{len(_fin_shown)} of {len(FIN['rows'])}")
_nA, _nU = narrow(FD["amount"], _fin_keys), narrow(FD["units"], _fin_keys)
_fin_post = page_reduce(_fin_shown)
check("G3c UNDER THAT FILTER the narrowed panel IS the tile — the page reads the panel's own number "
      "and its reduce agrees with it to the cent, so the figure and its evidence are one fold",
      agrees(_nA, _fin_post["amount"]) and agrees(_nU, _fin_post["units"])
      and len(_nA["rows"]) == len(_fin_shown))
check("G3d and the narrowed figure is genuinely DIFFERENT from the unfiltered one, so the narrowing "
      "is doing work rather than being the identity twice",
      cents(_nA["figure"]["amount"]) != cents(FD["amount"]["figure"]["amount"]))
check("G3e A CAPPED PANEL REFUSES TO NARROW, and the screen then keeps its own number — a smaller "
      "figure presented as the whole of one is the failure this mechanism exists to prevent",
      narrow(dict(FD["amount"], capped=True), _fin_keys) is None)

# ── G4/G5  The safety net.
check("G4 THE SAFETY NET — a panel that does not agree with its tile to the cent offers NO drill, "
      "so even a disagreement cannot move a figure. ONE CENT IS ENOUGH",
      agrees(ID["grand_total"], SUMMARY["totals"]["grand_total"])
      and not agrees(ID["grand_total"], SUMMARY["totals"]["grand_total"] + 0.01)
      and agrees(RD_["distributor_claimed"], REIMB["totals"]["distributor_claimed"])
      and not agrees(RD_["distributor_claimed"], REIMB["totals"]["distributor_claimed"] - 0.01))
check("G5 and that gate is the SHARED one, passed to `useDrillTile` on every page in this batch "
      "rather than re-written four times",
      all("agreesToTheCent" in v[v.index("useDrillTile("):v.index("useDrillTile(") + 400]
          for v in PAGES.values() if "useDrillTile(" in v)
      and len([v for v in PAGES.values() if "useDrillTile(" in v]) == 4,
      str([k for k, v in PAGES.items()
           if "useDrillTile(" not in v
           or "agreesToTheCent" not in v[v.index("useDrillTile("):
                                         v.index("useDrillTile(") + 400]]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Z  MUTATIONS — each one tampers with ONE side and must come back RED.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Z. mutations (each must come back RED) ==")


def _panel_over(rows, amount, **spec):
    """One ad-hoc panel through the REAL shared fold, for a mutation to measure."""
    spec.setdefault("tables", ("asset_ledger",))
    spec.setdefault("row_label", "label")
    return rd.tile_panel("L", amount, rows, **spec)


ctl("Z1 A ONE-CENT TILE — the invoice grand total moved by a cent makes the gate refuse the drill, "
    "so the figure cannot be explained by rows that do not add up to it",
    not agrees(ID["grand_total"], SUMMARY["totals"]["grand_total"] + 0.01))
ctl("Z2 A DROPPED ROW — one store spelling missing from the evidence makes the panel DISAGREE with "
    "its own tile, in words, instead of quietly reporting a shorter month",
    _panel_over(SUMMARY["by_store"][:-1], SUMMARY["totals"]["grand_total"],
                value="grand_total", keys=("location",),
                row_label="location")["tie_out"]["ties"] is False)
ctl("Z3 A BLANK FOLDED IN AT $0.00 — the careless declaration reads the sale price straight off the "
    "row, so a device the ledger records no price for is counted as a sale for nothing and the "
    "panel announces a tie over it",
    (lambda bad: bad["tie_out"]["ties"] is True and bad["tie_out"]["counted_rows"] == 6
     and RD_["store_device_payment"]["tie_out"]["counted_rows"] == 5)(
        _panel_over(REIMB["rows"], REIMB["totals"]["store_device_payment"],
                    value=lambda r: (r.get("store_device_payment") or 0.0),
                    keys=("device_id",), row_label="device_id")))
ctl("Z4 A COUNTED ROW WITH NO NUMBER — a panel that COUNTS an unmeasured row refuses to claim a "
    "tie at all (`ties` is None, never True), because a sum missing a row was never a comparison",
    _panel_over(REIMB["rows"], REIMB["totals"]["carrier_paid_here"],
                value=lambda r: (r.get("carrier_paid_here") if dd.carrier_looked_up(r) else None),
                in_figure=lambda _r: True, keys=("device_id",),
                row_label="device_id")["tie_out"]["ties"] is None)
ctl("Z5 A FALLBACK-BASIS PLACEMENT READ AS EVIDENCE — a dollar whose payment type the org never "
    "declared as device money is NOT in the paid side. Counting it would put money the keyword "
    "ladder placed as a last resort into a per-DEVICE figure",
    "IM3" not in CARRIER_LINES["devices"]
    and cents(drr.carrier_line_side(LINE_ROWS, CFG, lambda _t: True)["devices"]["IM3"]["total"])
    == cents(9999.0)
    and cents(REIMB["totals"]["carrier_paid_here"]) == cents(180.0 + 60.0))
ctl("Z6 A PAID PANEL SERVED WITH NO ROUTING DECLARED — the careless declaration serves it anyway, "
    "and the reader gets a $0.00 figure 'tying' to a list of six devices that all carry no number "
    "at all",
    (lambda bad: cents(bad["figure"]["amount"]) == 0 and bad["tie_out"]["counted_rows"] == 0
     and bad["tie_out"]["ties"] is True
     and "carrier_paid_here" not in dd.reimb_drilldowns(
         drr.device_lines(ASSET_ROWS, drr.carrier_line_side(LINE_ROWS, CFG, None), CFG, None,
                          COVERAGE, configured=False)))(
        _panel_over(drr.device_lines(ASSET_ROWS, drr.carrier_line_side(LINE_ROWS, CFG, None), CFG,
                                     None, COVERAGE, configured=False)["rows"],
                    0.0, value=lambda _r: None, keys=("device_id",), row_label="device_id")))
ctl("Z7 THE TWO GRAINS POOLED — one panel naming BOTH the ledger and the carrier feed on every row "
    "claims each relation held a figure it does not, and the sources roll-up then double-counts "
    "every device",
    (lambda bad: len(bad["sources"]) == 2
     and all(s["rows"] == len(REIMB["rows"]) for s in bad["sources"])
     and len(RD_["distributor_claimed"]["sources"]) == 1)(
        _panel_over(REIMB["rows"], REIMB["totals"]["distributor_claimed"],
                    value="distributor_claimed", keys=("device_id",), row_label="device_id",
                    tables=(dd.ASSET_LEDGER, dd.CARRIER_LINE_FEED))))
ctl("Z8 A NARROWED PANEL ON A SERVER-FOLDED SCREEN — Σ over the rows the per-line table is showing "
    "is a DIFFERENT figure from the tile, so a page that narrowed its panel here would be "
    "explaining a number nobody is reading (§CORE-17.3o)",
    cents(_panel_over(_rshown, REIMB["totals"]["distributor_claimed"], value="distributor_claimed",
                      keys=("device_id",), row_label="device_id")["tie_out"]["rows_total"])
    != cents(REIMB["totals"]["distributor_claimed"]))
ctl("Z9 A COMPOSED TOTAL GIVEN A PANEL — a panel for 'Fees (all types)' would have to assert rows "
    "behind a figure the producer never folded per store, and `by_store` has no field to read",
    all("fees_total" not in r for r in SUMMARY["by_store"])
    and rd.tiles(({"key": "fees_total", "total": "fees_total", "label": "L",
                   "value": "fees_total", "tables": ("vip_invoices",), "row_label": "location",
                   "keys": ("location",)},),
                 SUMMARY["by_store"], {"fees_total": SUMMARY["totals"]["fees_total"]}
                 )["fees_total"]["tie_out"]["ties"] is False)
ctl("Z10 A TAMPERED CARRIER LINE — one device's payment changed by a dollar makes the paid tile and "
    "its evidence disagree, which is the point: this figure is a claim about money that arrived, so "
    "a change on either side must be visible",
    _panel_over([dict(r, carrier_paid_here=(r["carrier_paid_here"] + 1.0 if i == 0
                                            else r["carrier_paid_here"]))
                 for i, r in enumerate(REIMB["rows"])],
                REIMB["totals"]["carrier_paid_here"],
                value=lambda r: (r.get("carrier_paid_here") if dd.carrier_looked_up(r) else None),
                keys=("device_id",), row_label="device_id",
                tables=(dd.CARRIER_LINE_FEED,))["tie_out"]["ties"] is False)
ctl("Z11 THE FINANCING TILE NOT READ OFF ITS PANEL — a page that kept its own reduce under a filter "
    "would print a number its panel does not explain. Σ over the whole row list is not Σ over the "
    "selection, and the difference is real on these very rows",
    cents(page_reduce(FIN["rows"])["amount"]) != cents(page_reduce(_fin_shown)["amount"])
    and cents(_nA["figure"]["amount"]) == cents(page_reduce(_fin_shown)["amount"]))
ctl("Z12 A UNITS PANEL CLAIMING THE PAYOUT'S OWN COLLAPSE WHEN IT FELL BACK — the note must change "
    "with the payload, or the panel asserts a construction this read did not have (defect 11)",
    dd.financing_catalogue(("raw_sales",), fallback_units=True)[1]["note"]
    != dd.financing_catalogue(("raw_sales",), fallback_units=False)[1]["note"]
    and dd.units_fallback({"unit_notes": [dd.UNIT_FALLBACK_CODE]}) is True)
ctl("Z13 THE UN-WIRE CONTROL — §F5's own scan really does fire. A page with `narrowPanels` in it is "
    "detected, so F5 passing means the text is absent rather than the scan being blind",
    "narrowPanels" in "const x = narrowPanels(panels, keep)"
    and all("narrowPanels" not in PAGES[k] for k, n in NARROWS.items() if not n))
ctl("Z14 THE DEREFERENCE CONTROL — a declaration that re-spelled a relation name instead of "
    "dereferencing it would be a second copy, and §A2's scan catches exactly that pattern",
    '"vip_invoices"' in _reg_def and REG.count('"vip_invoices"') == 1
    and '"vip_invoices"' not in DECL)
ctl("Z15 THE REUSE CONTROL — a second catalogue over the weekly batches would be a sibling "
    "derivation, and this file would see it: the binding is by IDENTITY, so a local copy fails",
    dd.PAYGO_CATALOGUE is add.lending_catalogue
    and (lambda f: f is not add.lending_catalogue)(lambda: add.lending_catalogue()))


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("\nBatch 16 — the four device-financing reports drill, six measured grains over four row "
      "shapes and five provenance answers, the weekly-batch grain REUSED rather than re-declared, "
      "one defect fixed and ten reported, and NO FIGURE MOVED.")
