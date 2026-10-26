"""PROOF — THE THREE BILL-PAYMENT / PROCESSOR MONEY REPORTS NAME THE ROWS BEHIND THEIR FIGURES.

DB-free, stdlib only, driving the REAL declaration (`commcalc/processor_money_drilldown.py`) and the
REAL producers (`commcalc/billpay_extract.extract_lines` / `rollup` / `compare_with_feed`,
`commcalc/epay_fee_recon.build_recon`, `commcalc/processor_ledger.classify_amount` / `fold_cells` /
`day_type_rollup`) through the REAL shared fold (`core/report_drilldown.tiles` / `tile_panel` /
`panel`), over fixtures shaped exactly like what each endpoint hands them. Batch 14 of the §CORE-17
audit: /commcalc/bill-payments, /commcalc/epay-fee-recon and /commcalc/processor-ledger.

WHAT THIS BATCH IS ABOUT.

1. THE GRAIN COUNT IS MEASURED, NOT ASSUMED (§CORE-17.3l). SEVEN grains over three routes, in TWO
   row shapes and ONE unit — and SIX of the seven are keyed by a store-day, separated only by where
   the number came from: what the register rang, what the processor's own report says, and a third
   that names both. §A.

2. A SIDE THAT REPORTED NOTHING IS NOT A SIDE THAT REPORTED ZERO. Both recons coerce a missing
   store-day to $0.00 today; the panels show those rows WITHOUT a figure and do not count them, and
   because a null adds nothing to a sum either way the figures are identical to the cent. §B, §C.

3. A DIFFERENCE FOLDED PER ROW DRILLS; ONE SUBTRACTED AT THE TOTAL DOES NOT (§CORE-17.3k). All three
   of this family's differences are per-row, which is why all three open. §B4, §C4, §D4.

4. EVERY COUNT WHOSE LIST IS ON SCREEN GETS NO PANEL, not an empty one: no key at all. §Y5.

5. SIX LIVE DEFECTS, REPORTED NOT HIDDEN — one of them fixed where it is a database name on an
   owner's screen rather than a figure. §Z7–§Z12.

  python3.11 backend/harness_batch14_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import billpay_extract as bpx                        # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage              # noqa: E402
from app.modules.commcalc import epay_fee_recon as fr                          # noqa: E402
from app.modules.commcalc import processor_ledger as pl                        # noqa: E402
from app.modules.commcalc import processor_money_drilldown as dd               # noqa: E402
from app.modules.core import report_drilldown as rd                            # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label
          + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECL = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "processor_money_drilldown.py")
LEDGER_MOD = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "processor_ledger.py")
LEDGER_API = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "processor_ledger_api.py")
CC_ROUTER = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "router.py")
PAGES = {
    "/commcalc/bill-payments": "frontend/src/app/(platform)/commcalc/bill-payments/page.tsx",
    "/commcalc/epay-fee-recon": "frontend/src/app/(platform)/commcalc/epay-fee-recon/page.tsx",
    "/commcalc/processor-ledger": "frontend/src/app/(platform)/commcalc/processor-ledger/page.tsx",
}
#: The page that really IS in the client-narrowing class already, so §Y10b can prove the scan is not
#: vacuous instead of passing because it finds nothing anywhere.
NARROW_WITNESS = "frontend/src/app/(platform)/commcalc/targets/accessories/page.tsx"


def _read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def _code_only(path):
    """The file's CODE, with comments and docstrings dropped — `harness_pl_drill_detail` §G5's
    narrowing, reused rather than re-derived: a build lock about what a module DOES must not fire on
    the prose describing it. A file that will not tokenise comes back WHOLE, so it fails CLOSED."""
    try:
        out = []
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                    out.append(tok.string)
        return " ".join(out)
    except Exception:
        return open(path, encoding="utf-8").read()


DECL_SRC = open(DECL, encoding="utf-8").read()
DECL_CODE = _code_only(DECL)
DECL_TIGHT = DECL_CODE.replace(" ", "")


def _tie(p):
    return (p or {}).get("tie_out", {}).get("ties")


def _row(p, label):
    for r in (p or {}).get("rows") or []:
        if r.get("label") == label:
            return r
    return {}


def _cents(x):
    return round(float(x or 0.0) * 100)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A PORT OF THE SCREEN'S OWN ARITHMETIC, used ONLY to prove no figure moved.
#
# `narrowPanel` / `agreesToTheCent` live in `frontend/src/lib/drill-narrow.ts` and are pinned to the
# server's fold by `frontend/prove_drill_narrow.mjs`. These two are a deliberate line-for-line port
# of the parts the two narrowing pages depend on, so this harness can reproduce what those screens
# now print and compare it against what they printed before — the falsifiable half of "no figure
# moved". They are NOT a second home for the arithmetic: nothing in the app imports them.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def narrow_panel(panel, keep):
    if not panel or panel.get("rows") is None:
        return None
    if panel.get("capped"):
        return None
    rows = [r for r in panel["rows"] if (not r.get("row_key")) or keep(r)]
    if len(rows) == len(panel["rows"]):
        return panel
    amount = round(sum((r.get("amount") or 0.0) for r in rows
                       if r.get("in_figure") and r.get("present")), 2)
    out = dict(panel)
    out["figure"] = dict(panel.get("figure") or {}, amount=amount)
    out["rows"] = rows
    out["capped"] = False
    return out


def agrees_to_the_cent(panel, tile):
    return bool(panel) and _cents((panel.get("figure") or {}).get("amount")) == _cents(tile)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURES — shaped exactly like what each endpoint hands the declaration.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# ── /commcalc/bill-payments ───────────────────────────────────────────────────────────────────
#: The org's own `bill_payment` rule, as `_exec_metric_config` resolves it. No carrier word.
BP_RULE = {"department": ["bill payments"], "exclude_category": ["other charge"]}
#: Sales lines as `_sales_rows_union` hands them over: two countable bill-pay lines on two days, a
#: VOIDED one, a RETURN, a line with no rep, and a line in the excluded fee category.
BP_SALES = [
    {"trans_id": "T1", "trans_date": "2026-10-01", "store": "STORE-A", "salesperson": "rep1",
     "department": "Bill Payments", "category": "Recharge", "product_desc": "walk-in recharge",
     "tender_type": "Cash", "ext_price": "40.00", "voided": "", "trans_type": "Sale"},
    {"trans_id": "T2", "trans_date": "2026-10-02", "store": "STORE-A", "salesperson": "rep1",
     "department": "Bill Payments", "category": "Recharge", "product_desc": "walk-in recharge",
     "tender_type": "Cash", "ext_price": "25.50", "voided": "", "trans_type": "Sale"},
    {"trans_id": "T3", "trans_date": "2026-10-02", "store": "STORE-A", "salesperson": "rep1",
     "department": "Bill Payments", "category": "Recharge", "product_desc": "walk-in recharge",
     "tender_type": "Cash", "ext_price": "99.00", "voided": "true", "trans_type": "Sale"},
    {"trans_id": "T4", "trans_date": "2026-10-02", "store": "STORE-A", "salesperson": "rep1",
     "department": "Bill Payments", "category": "Recharge", "product_desc": "walk-in recharge",
     "tender_type": "Cash", "ext_price": "10.00", "voided": "", "trans_type": "Return"},
    {"trans_id": "T5", "trans_date": "2026-10-02", "store": "STORE-A", "salesperson": "",
     "department": "Bill Payments", "category": "Recharge", "product_desc": "walk-in recharge",
     "tender_type": "Cash", "ext_price": "15.00", "voided": "", "trans_type": "Sale"},
    {"trans_id": "T6", "trans_date": "2026-10-02", "store": "STORE-A", "salesperson": "rep1",
     "department": "Bill Payments", "category": "Other Charge", "product_desc": "service charge",
     "tender_type": "Cash", "ext_price": "3.00", "voided": "", "trans_type": "Sale"},
]
#: The processor's own bill-pay report: it agrees on day 1, is SHORT on day 2, and carries a day the
#: sales export has nothing at all for (the feed-only row).
BP_FEED = {("STORE-A", "2026-10-01"): 40.00, ("STORE-A", "2026-10-02"): 20.50,
           ("STORE-A", "2026-10-03"): 12.00}
#: `_sales_rows_union`'s own meta: the monthly upload led and the daily feed filled two rows.
BP_META = {"primary": lineage.MONTHLY_SALES, "other": lineage.LIVE_SALES_FEED,
           "primary_rows": 6, "other_rows": 2, "filled_rows": 2, "richer_rows": 0,
           "completeness_rows": 0}

_bp_ex = bpx.extract_lines(BP_SALES, BP_RULE, store_key=lambda s: s)
BP_LINES = _bp_ex["lines"]
BP_ROLL = bpx.rollup(BP_LINES)
BP_CMP = bpx.compare_with_feed(BP_ROLL["per_store_day"], BP_FEED)
BP_TOTALS = {"lines": BP_ROLL["count"], "sum": BP_ROLL["sum"], "sum_feed": BP_CMP["sum_feed"],
             "difference": BP_CMP["difference"], "days_compared": BP_CMP["days_compared"],
             "days_sales_only": BP_CMP["days_sales_only"],
             "days_feed_only": BP_CMP["days_feed_only"]}
BP_PANELS = dd.billpay_drilldowns(BP_CMP["rows"], BP_TOTALS, sales_meta=BP_META, processor="epay")

# ── /commcalc/epay-fee-recon ──────────────────────────────────────────────────────────────────
#: The register side, as `aggregate_system_fee` returns it: three store-days, one of them a MEASURED
#: zero (the upload carries a fee line that netted to nothing).
FEE_SYSTEM = {("S1", "2026-10-01"): 12.00, ("S2", "2026-10-01"): 4.50,
              ("S3", "2026-10-01"): 0.00}
#: The settlement report, as `epay_ingest.per_store_day` returns it: it agrees on S1, is short on S2,
#: has nothing for S3, and carries an S4 the register never rang.
FEE_PORTAL = {("S1", "2026-10-01"): {"fee": 12.00}, ("S2", "2026-10-01"): {"fee": 7.50},
              ("S4", "2026-10-01"): {"fee": 2.25}}
FEE_ROWS = fr.build_recon(FEE_SYSTEM, FEE_PORTAL, tolerance=1.0)
FEE_TOTALS = {"system_fee": round(sum(r["system_fee"] for r in FEE_ROWS), 2),
              "portal_fee": round(sum(r["portal_fee"] for r in FEE_ROWS), 2),
              "var": round(sum(r["var"] for r in FEE_ROWS), 2),
              "flagged": sum(1 for r in FEE_ROWS if r["flag"]), "store_days": len(FEE_ROWS)}
FEE_PANELS = dd.fee_drilldowns(FEE_ROWS, FEE_TOTALS)

# ── /commcalc/processor-ledger ────────────────────────────────────────────────────────────────
#: Raw feed amounts as each processor's report states them, classified by the REAL
#: `classify_amount` with each feed's own sign convention.
LED_RAW = [
    ("epay", "2026-10-01", "Commission", "S1", "store one", "MKT-1", "commission", 900.00),
    ("epay", "2026-10-01", "Commission Withholding", "S1", "store one", "MKT-1", "commission",
     -125.00),
    ("epay", "2026-10-02", "Rebate", "S2", "store two", "", "", 60.00),
    ("vidapay", "2026-10-02", "Sales Order", "S2", "store two", "", "", 310.25),
    ("vidapay", "2026-10-02", "Residual", "S2", "store two", "", "", -40.75),
    # A processor the ledger has no feed shape for — the cell exists, its provenance does not.
    ("unregistered", "2026-10-03", "Adjustment", "", "(no store)", "", "", -5.00),
]
LED_EVENTS = []
for proc, day, typ, code, disp, mkt, cat, amount in LED_RAW:
    shape = pl.FEED_SHAPES.get(proc) or {"credit_positive": True}
    debit, credit = pl.classify_amount(amount, shape["credit_positive"])
    LED_EVENTS.append({"processor": proc, "date": day, "tx_type": typ, "store_code": code,
                       "store": disp, "market": mkt, "category": cat,
                       "debit": debit, "credit": credit})
LED_CELLS = pl.fold_cells(LED_EVENTS)
LED_ROLLUP = pl.day_type_rollup(LED_CELLS)
LED_PANELS = dd.ledger_drilldowns(LED_CELLS, LED_ROLLUP["total"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. THE GRAIN COUNT IS MEASURED, NOT ASSUMED (§CORE-17.3l) ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_catalogues = {
    "bill-payments": dd.billpay_catalogue((lineage.MONTHLY_SALES,), (lineage.EPAY_DAILY,)),
    "epay-fee-recon": dd.fee_catalogue(),
    "processor-ledger": dd.ledger_catalogue(),
}
check("A1 THREE catalogues — one per ROW LIST, because the bill-pay store-days, the fee-recon "
      "store-days and the ledger cells are three different populations",
      len(_catalogues) == 3 and all(len(c) == 3 for c in _catalogues.values()),
      {k: len(v) for k, v in _catalogues.items()})
_shapes = {tuple(spec["keys"]) for c in _catalogues.values() for spec in c}
check("A2 …in exactly TWO row shapes: a store-day (two spellings of it, one per report's own key "
      "columns) and a processor movement cell",
      _shapes == {("store", "date"), ("store_code", "close_date"),
                  ("processor", "date", "tx_type", "store_code")}, sorted(_shapes))
_provenances = set()
for name, cat in _catalogues.items():
    for spec in cat:
        t = spec["tables"]
        for row in (BP_CMP["rows"] + FEE_ROWS + LED_CELLS):
            _provenances |= set(t(row) if callable(t) else t)
check("A3 FOUR provenance answers in the whole family — the sales display union (two tables), the "
      "processor's bill-pay settlement feed, its dealer-account payment feed and the master-agent "
      "daily-TX feed. Three declaration files would have been three answers to one question",
      _provenances == {lineage.MONTHLY_SALES, lineage.EPAY_DAILY, lineage.PAYMENT_DETAIL,
                       lineage.MA_DAILY_TX}, sorted(_provenances))
check("A4 every figure in the family is MONEY — the unit is not what separates these grains",
      {p["figure"]["unit"] for p in
       list(BP_PANELS.values()) + list(FEE_PANELS.values()) + list(LED_PANELS.values())}
      == {rd.UNIT_MONEY})
check("A5 SIX of the seven grains are keyed by a STORE-DAY and are still six grains: what the "
      "register rang, what the processor reported, and a third naming BOTH — on each of two reports",
      len([spec for c in (_catalogues["bill-payments"], _catalogues["epay-fee-recon"])
           for spec in c]) == 6)
check("A6 …and the seventh is not a store-day at all: the ledger's row is one transaction TYPE on "
      "one day on one dealer account, and all three of its figures are columns of that SAME cell",
      tuple(_catalogues["processor-ledger"][0]["keys"]) ==
      ("processor", "date", "tx_type", "store_code")
      and {spec["value"] for spec in _catalogues["processor-ledger"]} == {"debits", "credits", "net"})
check("A7 the declaration owns no arithmetic of its own — it declares and dereferences, and every "
      "figure it serves was handed to it",
      not re.search(r"(?<![\w.])(?:sum|round)\s*\(", DECL_CODE)
      and "+" not in DECL_CODE.replace("sales_tables + feed_tables", ""),
      "a fold here would be a second answer to a question the one home already answers")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. /commcalc/bill-payments — the sales leg, the processor leg, and their difference ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("B0 the REAL producer was driven: the skip rules dropped the void, the return and the "
      "rep-less line, and the excluded fee category never matched",
      _bp_ex["rows_scanned"] == 3 and _bp_ex["rows_skipped"] == 3
      and _bp_ex["rows_unmatched"] == 1
      and BP_ROLL["count"] == 2 and BP_ROLL["sum"] == 65.50,
      (_bp_ex, BP_ROLL["sum"]))
check("B1 three panels, and no fourth — the LINE COUNT gets none, because the Lines tab IS the list "
      "it counts",
      set(BP_PANELS) == {"sum", "sum_feed", "difference"}, sorted(BP_PANELS))
check("B2 Σ extracted ties to the store-days it was folded from, to the cent",
      _tie(BP_PANELS["sum"]) is True and BP_PANELS["sum"]["figure"]["amount"] == BP_TOTALS["sum"],
      BP_PANELS["sum"]["tie_out"])
check("B3 the processor leg ties to the days the processor's own report carries",
      _tie(BP_PANELS["sum_feed"]) is True
      and BP_PANELS["sum_feed"]["figure"]["amount"] == BP_TOTALS["sum_feed"])
check("B4 THE DIFFERENCE DRILLS, because it is folded PER STORE-DAY and then added up — the rows "
      "and the figure are reached two different ways (§CORE-17.3k)",
      _tie(BP_PANELS["difference"]) is True
      and BP_PANELS["difference"]["figure"]["amount"] == BP_TOTALS["difference"]
      and BP_PANELS["difference"]["tie_out"]["counted_rows"] == 2,
      BP_PANELS["difference"]["tie_out"])
_feed_only = _row(BP_PANELS["sum"], "STORE-A · 2026-10-03")
check("B5 A STORE-DAY ONLY THE PROCESSOR REPORTED carries NO sales figure, is not counted, and "
      "says why in the one home's words — never a measured $0.00",
      _feed_only.get("amount") is None and _feed_only.get("present") is False
      and _feed_only.get("in_figure") is False
      and _feed_only.get("reason") == dd.REASON_NO_SALES_ROW
      and _feed_only.get("reason_label") == rd.UNMEASURED_REASONS[dd.REASON_NO_SALES_ROW],
      _feed_only)
check("B5b …and it names NO table on the sales side, which the panel reports as an origin it could "
      "not trace rather than tidying away",
      _feed_only.get("sources") == [] and BP_PANELS["sum"]["untraceable_origins"] == 1,
      BP_PANELS["sum"]["untraceable_origins"])
_day3_feed = _row(BP_PANELS["sum_feed"], "STORE-A · 2026-10-03")
check("B6 the SAME store-day DOES carry a processor figure on the processor's own panel — the two "
      "legs are different grains and the rows say so",
      _day3_feed.get("amount") == 12.00 and _day3_feed.get("sources") == [lineage.EPAY_DAILY],
      _day3_feed)
_day3_diff = _row(BP_PANELS["difference"], "STORE-A · 2026-10-03")
check("B7 and it has NO difference at all: a day with one leg missing is shown for context and "
      "deliberately not counted, because there is nothing to subtract",
      _day3_diff.get("amount") is None and _day3_diff.get("in_figure") is False
      and _day3_diff.get("reason") == dd.REASON_NO_SALES_ROW, _day3_diff)
check("B8 the SALES leg names the tables the union ACTUALLY read for this period, off the read's "
      "own meta — never a constant that would be wrong half the year (§CORE-17.3c)",
      dd.sales_union_tables(BP_META) == (lineage.MONTHLY_SALES, lineage.LIVE_SALES_FEED),
      dd.sales_union_tables(BP_META))
check("B8b …and the OTHER source is named only when the meta says it CONTRIBUTED — a table that "
      "returned rows the merge then dropped is not where this figure came from",
      dd.sales_union_tables({"primary": lineage.MONTHLY_SALES, "primary_rows": 9,
                             "other": lineage.LIVE_SALES_FEED, "other_rows": 4})
      == (lineage.MONTHLY_SALES,))
check("B8c …and a read that returned nothing resolves to NO table, which becomes a stated, "
      "countable untraceable origin (§CORE-17.3i)",
      dd.sales_union_tables({}) == ()
      and dd.sales_union_tables({"primary": "some_other_table", "primary_rows": 5}) == (),
      "a stored string is not a licence to put an identifier on an owner's screen")
check("B9 the PROCESSOR leg dereferences data_lineage_registry.billpay_processor_feed — the one "
      "home for which table a resolved processor's report lands in",
      dd.billpay_feed_tables("epay") == (lineage.EPAY_DAILY,)
      and dd.billpay_feed_tables("processor:vidapay") == (lineage.MA_DAILY_TX,)
      and "billpay_processor_feed" in DECL_TIGHT)
check("B9b …and an UNKNOWN or unconfigured processor names NOTHING rather than guessing",
      dd.billpay_feed_tables("") == () and dd.billpay_feed_tables("none") == ()
      and dd.billpay_feed_tables("some_new_processor") == ())
_no_proc = dd.billpay_drilldowns(BP_CMP["rows"], BP_TOTALS, sales_meta=BP_META, processor="")
check("B10 with no processor resolved, the processor panel's rows name no table at all and every "
      "origin is reported as untraceable",
      _no_proc["sum_feed"]["sources"] == []
      and _no_proc["sum_feed"]["untraceable_origins"] == len(BP_CMP["rows"]),
      _no_proc["sum_feed"]["sources"])
_no_feed_cmp = bpx.compare_with_feed(BP_ROLL["per_store_day"], {})
_no_feed_tot = dict(BP_TOTALS, sum_feed=_no_feed_cmp["sum_feed"],
                    difference=_no_feed_cmp["difference"])
_no_feed = dd.billpay_drilldowns(_no_feed_cmp["rows"], _no_feed_tot, sales_meta=BP_META,
                                 processor="epay")
check("B11 WITH NO PROCESSOR REPORT LOADED there is NO key for the processor figure and none for "
      "the difference — not an empty panel, which would read like a feed that settled nothing",
      set(_no_feed) == {"sum"}, sorted(_no_feed))
check("B12 every drill row carries the row_key that addresses the report row it explains",
      all(r.get("row_key") and set(r["row_key"]) <= {"store", "date"}
          for r in BP_PANELS["sum"]["rows"]))
check("B13 the panel's note says what the figure is and what it refuses to count, in the reader's "
      "words rather than a field name",
      all("$0.00" in p["note"] or "not counted" in p["note"] for p in BP_PANELS.values()))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. /commcalc/epay-fee-recon — the register leg, the settlement leg, the variance ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("C0 the REAL producer was driven: four store-days, biggest |variance| first",
      len(FEE_ROWS) == 4
      and [abs(r["var"]) for r in FEE_ROWS] == sorted((abs(r["var"]) for r in FEE_ROWS),
                                                      reverse=True),
      [(r["store_code"], r["var"]) for r in FEE_ROWS])
check("C1 three panels, and no fourth — 'Store-days flagged' is a COUNT whose list is the table "
      "below it, which highlights exactly the rows it counts",
      set(FEE_PANELS) == {"system_fee", "portal_fee", "var"}, sorted(FEE_PANELS))
check("C2 the register leg ties to the store-days the sales upload actually carries",
      _tie(FEE_PANELS["system_fee"]) is True
      and FEE_PANELS["system_fee"]["figure"]["amount"] == FEE_TOTALS["system_fee"],
      FEE_PANELS["system_fee"]["tie_out"])
check("C3 the settlement leg ties to the store-days the processor's report carries",
      _tie(FEE_PANELS["portal_fee"]) is True
      and FEE_PANELS["portal_fee"]["figure"]["amount"] == FEE_TOTALS["portal_fee"])
check("C4 THE VARIANCE DRILLS: it is `system − portal` folded per store-day and then added up",
      _tie(FEE_PANELS["var"]) is True
      and FEE_PANELS["var"]["figure"]["amount"] == FEE_TOTALS["var"]
      and FEE_PANELS["var"]["tie_out"]["counted_rows"] == len(FEE_ROWS),
      FEE_PANELS["var"]["tie_out"])
_s4 = _row(FEE_PANELS["system_fee"], "S4 · 2026-10-01")
check("C5 DEFECT 2, SHOWN NOT SWALLOWED — a store-day the sales upload never carried is folded into "
      "the register total as $0.00 by the producer; the panel shows it WITHOUT a figure and does "
      "not count it",
      _s4.get("amount") is None and _s4.get("in_figure") is False
      and _s4.get("reason") == dd.REASON_NO_SALES_ROW
      and [r for r in FEE_ROWS if r["store_code"] == "S4"][0]["system_fee"] == 0.0, _s4)
_s3 = _row(FEE_PANELS["portal_fee"], "S3 · 2026-10-01")
check("C5b …and the same on the settlement side, with the processor's own absence sentence",
      _s3.get("amount") is None and _s3.get("in_figure") is False
      and _s3.get("reason") == dd.REASON_NO_PROCESSOR_ROW
      and _s3.get("reason_label") == rd.UNMEASURED_REASONS[dd.REASON_NO_PROCESSOR_ROW], _s3)
_s3_sys = _row(FEE_PANELS["system_fee"], "S3 · 2026-10-01")
check("C6 A MEASURED ZERO IS A DIFFERENT ROW: S3 rang a fee line that netted to nothing, so it is "
      "counted at $0.00 — absence is not zero, and zero is not absence",
      _s3_sys.get("amount") == 0.0 and _s3_sys.get("present") is True
      and _s3_sys.get("in_figure") is True and _s3_sys.get("measured") is True, _s3_sys)
_s4_var = _row(FEE_PANELS["var"], "S4 · 2026-10-01")
check("C7 a ONE-SIDED variance is counted — it is the recon's own claim that the whole of the other "
      "side is missing — and the ROW says which side reported, so a reader can tell a missing feed "
      "from a missing fee",
      _s4_var.get("in_figure") is True and _s4_var.get("amount") == -2.25
      and _s4_var["fields"]["sides"] == "only the processor's report counted a fee", _s4_var)
check("C7b …and it names ONLY the side that reported. Naming both would claim a relation held a "
      "figure it does not",
      _s4_var.get("sources") == [lineage.EPAY_DAILY]
      and dd.fee_variance_tables({"in_system": True, "in_portal": True})
      == (dd.FEE_SYSTEM_FEED, dd.FEE_PORTAL_FEED)
      and dd.fee_variance_tables({}) == ())
check("C8 the register leg names the MONTHLY UPLOAD and the settlement leg the SETTLEMENT REPORT, "
      "both dereferenced from the lineage registry",
      dd.FEE_SYSTEM_FEED == lineage.MONTHLY_SALES and dd.FEE_PORTAL_FEED == lineage.EPAY_DAILY)
check("C9 an EMPTY recon drills to nothing rather than to three panels asserting $0.00 behind no "
      "rows at all",
      dd.fee_drilldowns([], {"system_fee": 0.0, "portal_fee": 0.0, "var": 0.0}) == {})
check("C10 every drill row carries its store-day key, so the screen can narrow the panel to the "
      "same selection its tile is folded over (§CORE-17.3d)",
      all(set(r["row_key"]) == {"store_code", "close_date"} for r in FEE_PANELS["var"]["rows"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. /commcalc/processor-ledger — debits, credits and a net that really drills ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("D0 the REAL producer was driven, with each feed's OWN sign convention: the payment feed "
      "credits a positive amount, the daily-TX feed debits one",
      pl.classify_amount(10, True) == (0.0, 10.0) and pl.classify_amount(-10, True) == (10.0, 0.0)
      and pl.classify_amount(10, False) == (10.0, 0.0)
      and pl.classify_amount(-10, False) == (0.0, 10.0))
check("D1 three panels, and no fourth — 'Days · feed rows' is two COUNTS whose lists are the table "
      "below it",
      set(LED_PANELS) == {"debits", "credits", "net"}, sorted(LED_PANELS))
check("D2 the debits panel ties to the cells it was folded from",
      _tie(LED_PANELS["debits"]) is True
      and LED_PANELS["debits"]["figure"]["amount"] == LED_ROLLUP["total"]["debits"],
      LED_PANELS["debits"]["tie_out"])
check("D3 so does the credits panel",
      _tie(LED_PANELS["credits"]) is True
      and LED_PANELS["credits"]["figure"]["amount"] == LED_ROLLUP["total"]["credits"])
check("D4 THE NET DRILLS, and its tie-out is a REAL comparison: the figure is Σcredits − Σdebits at "
      "the total while the rows are the per-cell nets, so the two are reached different ways "
      "(§CORE-17.3k)",
      _tie(LED_PANELS["net"]) is True
      and LED_PANELS["net"]["figure"]["amount"] == LED_ROLLUP["total"]["net"]
      and LED_PANELS["net"]["tie_out"]["rows_total"] == LED_ROLLUP["total"]["net"],
      LED_PANELS["net"]["tie_out"])
check("D5 each cell names the feed `FEED_SHAPES` says it was read out of — so a figure made of two "
      "processors' movements says which part came from which",
      dd.ledger_tables({"processor": "epay"}) == (lineage.PAYMENT_DETAIL,)
      and dd.ledger_tables({"processor": "vidapay"}) == (lineage.MA_DAILY_TX,))
check("D5b …read PER ROW, so one panel really does carry two provenances",
      {s["table"] for s in LED_PANELS["debits"]["sources"]}
      == {lineage.PAYMENT_DETAIL, lineage.MA_DAILY_TX},
      LED_PANELS["debits"]["sources"])
_ghost = _row(LED_PANELS["debits"], "2026-10-03 · Adjustment · (no store)")
check("D6 a cell whose processor the ledger has NO feed shape for names NOTHING, and the panel "
      "reports it as an origin it could not trace (§CORE-17.3i) — never a guessed table",
      dd.ledger_tables({"processor": "unregistered"}) == ()
      and _ghost.get("sources") == [] and LED_PANELS["debits"]["untraceable_origins"] == 1,
      (_ghost.get("sources"), LED_PANELS["debits"]["untraceable_origins"]))
check("D6b …and it is still COUNTED, because the money moved: an unnamed source is a provenance "
      "gap, not an unmeasured figure",
      _ghost.get("in_figure") is True and _ghost.get("amount") == 5.00
      and _ghost.get("measured") is True, _ghost)
check("D7 the drill rows carry the market and the pay CATEGORY as fields, so the screen can narrow "
      "by them — and a cell the org declared no category for carries '' rather than a category "
      "name it never chose",
      _row(LED_PANELS["debits"], "2026-10-01 · Commission Withholding · store one")["fields"]
      ["category"] == "commission"
      and _ghost["fields"]["category"] == "" and _ghost["fields"]["market"] == "")
check("D8 an EMPTY range drills to nothing", dd.ledger_drilldowns([], {"debits": 0.0}) == {})
check("D9 every drill row carries the cell key the page narrows on",
      all(set(r["row_key"]) <= {"processor", "date", "tx_type", "store_code"}
          and "date" in r["row_key"] for r in LED_PANELS["net"]["rows"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. ONE FACT, ONE HOME, DEREFERENCED ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_tables = (lineage.MONTHLY_SALES, lineage.LIVE_SALES_FEED, lineage.EPAY_DAILY,
           lineage.PAYMENT_DETAIL, lineage.MA_DAILY_TX)
check("E1 the declaration spells NO table name of its own — every relation it can name is "
      "dereferenced from the lineage registry or from the producer's own shape",
      not any(('"%s"' % t) in DECL_SRC or ("'%s'" % t) in DECL_SRC for t in _tables),
      [t for t in _tables if ('"%s"' % t) in DECL_SRC])
check("E1b NEGATIVE CONTROL — the scan would catch a literal, so E1 cannot pass vacuously",
      ('"%s"' % lineage.MONTHLY_SALES) in ('x = "%s"' % lineage.MONTHLY_SALES))
_LED_CODE = _code_only(LEDGER_MOD).replace(" ", "")
_LED_SRC = open(LEDGER_MOD, encoding="utf-8").read()
check("E2 processor_ledger.FEED_SHAPES DEREFERENCES the registry's own name for each feed — the "
      "literals there were a second copy of a fact the platform states once, and this declaration "
      "reading that map would have made it a third",
      "_lineage.PAYMENT_DETAIL" in _LED_CODE and "_lineage.MA_DAILY_TX" in _LED_CODE
      and not any(('"%s"' % t) in _code_only(LEDGER_MOD)
                  for t in (lineage.PAYMENT_DETAIL, lineage.MA_DAILY_TX)),
      "a shape that stops dereferencing restores the patchwork")
check("E2b …and the values are unchanged, so nothing about the ledger's reads moved",
      pl.FEED_SHAPES["epay"]["source"] == "raw_payment_detail"
      and pl.FEED_SHAPES["vidapay"]["source"] == "raw_ma_daily_tx",
      {k: v["source"] for k, v in pl.FEED_SHAPES.items()})
check("E3 the only absence reasons this batch emits are keys the ONE home already holds — no new "
      "vocabulary, no fourth spelling of the same sentence",
      dd.REASON_NO_SALES_ROW in rd.UNMEASURED_REASONS
      and dd.REASON_NO_PROCESSOR_ROW in rd.UNMEASURED_REASONS)
_emitted = {r.get("reason") for p in
            list(BP_PANELS.values()) + list(FEE_PANELS.values()) + list(LED_PANELS.values())
            for r in p["rows"]} - {None}
check("E3b …and every reason actually emitted resolves to a full sentence, so a reader is never "
      "shown a bare field name",
      _emitted and all(rd.UNMEASURED_REASONS.get(r) for r in _emitted), sorted(_emitted))
check("E4 the declaration builds no panel dict by hand — it goes through the shared fold",
      "report_drilldown.tiles" in DECL_TIGHT.replace("report_drilldown.tiles", "report_drilldown.tiles")
      and DECL_TIGHT.count("report_drilldown.tiles(") == 3
      and '"tie_out"' not in DECL_SRC and '"rows_total_count"' not in DECL_SRC)
check("E5 RULE TWO — no carrier, tenant, processor, product or store NAME in the declaration",
      not re.search(r"(?i)\b(boost|vidapay|epay\s+inc|total\s+wireless|luxelink|cellfonz|b2bsoft)\b",
                    DECL_SRC),
      "the processor KEYS 'epay' / 'vidapay' are the platform's own stored config values, resolved "
      "through FEED_SHAPES and the registry, not vendor words in a branch")
check("E6 the declaration is PURE — no I/O, no framework, no clock",
      not re.search(r"(?m)^\s*(?:import|from)\s+(?:fastapi|datetime|time|requests|supabase|os)\b",
                    DECL_SRC))
check("E7 …and it names no fee RATE: whether a tenant charges a bill-payment fee is DECLARED per "
      "org (mig 1046) and config is not evidence (§CORE-17.3m)",
      not re.search(r"(?<![\w.])\d+\.\d+(?![\w])", _code_only(DECL)),
      "a dollar or rate literal here would be a figure this module invented")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. NO FIGURE MOVED — the screens' own folds, reproduced and compared ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The Bill Payments tiles still print the endpoint's own totals, untouched; the other two screens now
# read their tile off the panel. So the proof is: the panel's figure IS the number the screen printed
# before, and where it is not, the screen falls back to its own reduce and prints the same number
# anyway. Each of these is falsified in §Z.
check("F1 BILL PAYMENTS — all three tiles still print the endpoint's own totals and every panel's "
      "figure is that same number",
      all(BP_PANELS[k]["figure"]["amount"] == BP_TOTALS[k] for k in BP_PANELS))
# The fee recon's OLD tile arithmetic, ported from the page as it stood before this batch.
def _fee_tile_old(rows):
    return {"system_fee": sum(r["system_fee"] or 0 for r in rows),
            "portal_fee": sum(r["portal_fee"] or 0 for r in rows),
            "var": sum(r["var"] or 0 for r in rows)}


_old = _fee_tile_old(FEE_ROWS)
check("F2 FEE RECON, UNFILTERED — the page's own reduce and the panel it now reads instead agree to "
      "the cent on all three figures",
      all(agrees_to_the_cent(FEE_PANELS[k], _old[k]) for k in ("system_fee", "portal_fee", "var")),
      {k: (_old[k], FEE_PANELS[k]["figure"]["amount"]) for k in _old})
_kept = [r for r in FEE_ROWS if r["flag"]]            # the "discrepancies only" tick


def _screen_key(parts):
    """`drill-narrow.screenKey`, ported: the entries sorted, so key ORDER cannot matter."""
    return "\x01".join("%s=%s" % (n, parts[n]) for n in sorted(parts))


def _key_of(r):
    return _screen_key(r.get("row_key") or {})


_keys = {_screen_key({"store_code": r["store_code"], "close_date": r["close_date"]})
         for r in _kept}


_old_f = _fee_tile_old(_kept)
_narrowed = {k: narrow_panel(FEE_PANELS[k], lambda r: _key_of(r) in _keys) for k in FEE_PANELS}
check("F3 FEE RECON, FILTERED — under 'discrepancies only' the narrowed panel and the page's reduce "
      "over the same rows agree to the cent, so the tile prints what it always printed",
      all(agrees_to_the_cent(_narrowed[k], _old_f[k])
          for k in ("system_fee", "portal_fee", "var")),
      {k: (_old_f[k], (_narrowed[k] or {}).get("figure")) for k in _old_f})
check("F3b …and the narrowed panel really did drop rows, so F3 is not the unfiltered case again",
      len(_narrowed["var"]["rows"]) == len(_kept) < len(FEE_ROWS))
# The ledger's OLD tile arithmetic, ported from the page as it stood before this batch.
def _led_tile_old(cells):
    d = sum(c["debits"] for c in cells)
    c_ = sum(c["credits"] for c in cells)
    return {"debits": d, "credits": c_, "net": c_ - d}


_old_l = _led_tile_old(LED_CELLS)
check("F4 PROCESSOR LEDGER, UNFILTERED — the page's own reduce and the panels it now reads agree to "
      "the cent on all three figures, the net included",
      all(agrees_to_the_cent(LED_PANELS[k], _old_l[k]) for k in ("debits", "credits", "net")),
      {k: (_old_l[k], LED_PANELS[k]["figure"]["amount"]) for k in _old_l})
_led_kept = [c for c in LED_CELLS if c["store_code"] == "S2"]
_led_keys = {_screen_key({"processor": c["processor"], "date": c["date"],
                          "tx_type": c["tx_type"], "store_code": c["store_code"]})
             for c in _led_kept}
_old_lf = _led_tile_old(_led_kept)
_ln = {k: narrow_panel(LED_PANELS[k], lambda r: _key_of(r) in _led_keys) for k in LED_PANELS}
check("F5 PROCESSOR LEDGER, FILTERED TO ONE STORE — the narrowed panels and the page's reduce over "
      "the same cells agree to the cent",
      all(agrees_to_the_cent(_ln[k], _old_lf[k]) for k in ("debits", "credits", "net")),
      {k: (_old_lf[k], (_ln[k] or {}).get("figure")) for k in _old_lf})
check("F6 THE SAFETY NET — a panel whose figure does NOT agree with the tile offers no drill and the "
      "screen keeps its own number, so a disagreement can never move a figure either",
      not agrees_to_the_cent(LED_PANELS["net"], _old_l["net"] + 0.01),
      "agreesToTheCent is the one home's gate, and both screens pass it to useDrillTile")
check("F7 A CAPPED PANEL IS REFUSED RATHER THAN NARROWED — a long range serves 400 of its rows and "
      "the one home then declines to narrow it, so a shorter figure is never presented as the whole "
      "of one",
      narrow_panel(dict(LED_PANELS["debits"], capped=True), lambda _r: False) is None)
_many = [dict(c, date="2026-11-%02d" % ((i % 28) + 1), tx_type="T%d" % i)
         for i, c in enumerate(LED_CELLS * 150)]
_big = dd.ledger_drilldowns(_many, pl.day_type_rollup(_many)["total"])
check("F7b …and the cap is the one home's, with the TRUE row count always served, so a capped "
      "panel cannot present a shorter month as the whole of one",
      _big["debits"]["capped"] is True and _big["debits"]["rows_shown"] == rd.ROW_CAP
      and _big["debits"]["rows_total_count"] == len(_many)
      and _tie(_big["debits"]) is True,
      (_big["debits"]["rows_shown"], _big["debits"]["rows_total_count"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Y. WIRED, AND LOCKED SO IT CANNOT UN-WIRE ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_API = open(LEDGER_API, encoding="utf-8").read()
_ROUTER = open(CC_ROUTER, encoding="utf-8").read()
check("Y1 the processor-ledger endpoint ATTACHES the declaration's output and folds no panel of its "
      "own",
      "processor_money_drilldown" in _API and 'out["drilldown"] = _pmd.ledger_drilldowns' in _API
      and "tile_panel" not in _API)
check("Y2 the bill-payments endpoint attaches it, passing the sales read's OWN meta and the "
      "resolved processor",
      '"drilldown": _pmd.billpay_drilldowns' in _ROUTER
      and "sales_meta=meta, processor=processor" in _ROUTER)
check("Y3 the fee-recon endpoint attaches it AFTER the scope filter, so a panel can never hold "
      "store-days the caller may not see",
      'out["drilldown"] = _pmd.fee_drilldowns(out["rows"], out["totals"])' in _ROUTER
      and _ROUTER.index('out["drilldown"] = _pmd.fee_drilldowns')
      > _ROUTER.index('out["rows"] = [r for r in out["rows"] if in_keyset(ks, r.get("store_code"))]'))
for route, rel in PAGES.items():
    src = _read(rel)
    check("Y4 %s renders the ONE shared panel, with the shared tile hook and the shared links"
          % route,
          "DrillDownPanel" in src and "useDrillTile" in src and "DrillTileLinks" in src)
_bp_page = _read(PAGES["/commcalc/bill-payments"])
_fee_page = _read(PAGES["/commcalc/epay-fee-recon"])
_led_page = _read(PAGES["/commcalc/processor-ledger"])
check("Y5 NO KEY AT ALL for a count whose list is on screen — the bill-pay line count, the "
      "days-compared counts, the flagged store-days and the ledger's day / feed-row counts offer no "
      "drill, rather than an empty panel that reads like a bug",
      not any(k in set(BP_PANELS) | set(FEE_PANELS) | set(LED_PANELS)
              for k in ("lines", "days_compared", "days_sales_only", "days_feed_only",
                        "flagged", "store_days", "rows", "days")),
      sorted(set(BP_PANELS) | set(FEE_PANELS) | set(LED_PANELS)))
check("Y5b …and the pages ask for no key the server never serves: every `dl(` key is a declared one",
      {k for src in (_bp_page, _fee_page, _led_page)
       for k in re.findall(r"drill\.dl\('([a-z_]+)'", src)}
      == {"sum", "sum_feed", "difference", "system_fee", "portal_fee", "var",
          "debits", "credits", "net"})
check("Y6 every table these panels can name has a PLAIN NAME in the frontend's one home, so an "
      "owner never reads a database name (§19.38)",
      all(("\n  %s:" % t) in _read("frontend/src/lib/sourceLabels.ts") for t in _tables),
      [t for t in _tables if ("\n  %s:" % t) not in _read("frontend/src/lib/sourceLabels.ts")])
_pending = _read("backend/harness_drilldown_pending.txt")
for route in PAGES:
    m = re.search(r"(?m)^%s\t(\w+)\t(.*)$" % re.escape(route), _pending)
    check("Y7 %s reads `shared` in the coverage ledger, with a note saying what drills and what it "
          "refuses to claim" % route,
          bool(m) and m.group(1) == "shared" and len(m.group(2)) > 80,
          m.group(1) if m else "absent")


def _owes_max(src):
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)\s*$", src)
    return int(m.group(1)) if m else None


_pin = _owes_max(_read("backend/harness_drilldown_coverage.py"))
check("Y8 the ratchet was re-pinned DOWNWARD in the same PR and has never been pushed back up past "
      "this batch's 56", _pin is not None and _pin <= 56, _pin)
check("Y8b NEGATIVE CONTROL — a pin ABOVE 56 is caught, so the ratchet cannot be loosened by "
      "editing the number", _owes_max("OWES_MAX = 57\n") > 56)
check("Y8c NEGATIVE CONTROL — a LATER batch's lower pin passes without touching this file, which is "
      "the whole point of asserting the DIRECTION instead of the value",
      _owes_max("OWES_MAX = 56\n") <= 56 and _owes_max("OWES_MAX = 40\n") <= 56)
check("Y8d NEGATIVE CONTROL — FAIL CLOSED: a ratchet with no readable pin is not a pass",
      _owes_max("# the pin was deleted\n") is None and _owes_max("OWES_MAX = lots\n") is None)
_graph = _read("backend/app/modules/core/module_graph.py")
check("Y9 the declaration and this harness are in the module graph — the other hand is NAMED",
      "harness_batch14_drilldown.py" in _graph
      and _graph.count("app/modules/commcalc/processor_money_drilldown.py") >= 2)
check("Y9b …and processor_ledger's new registry edge is declared too",
      re.search(r"'app/modules/commcalc/processor_ledger\.py':\s*\('_lineage',\)", _graph)
      is not None)
_wf = _read(".github/workflows/carrier-vocab-guard.yml")
check("Y10 a lock nothing runs is not a lock — this harness is run by a workflow, and a change to "
      "it re-runs that workflow",
      "harness_batch14_drilldown.py" in _wf and _wf.count("harness_batch14_drilldown.py") >= 2)
_IMPORTS_NARROW = re.compile(r"""from\s+['"]@/lib/drill-narrow['"]""")
check("Y11 CLIENT NARROWING is WIRED on exactly the TWO pages that fold their tiles over "
      "client-filtered rows, and on neither of the others",
      bool(_IMPORTS_NARROW.search(_fee_page)) and bool(_IMPORTS_NARROW.search(_led_page))
      and not _IMPORTS_NARROW.search(_bp_page),
      "bill-payments filters by QUERY PARAMETER, so the server's own fold already covers the screen")
check("Y11b …and the scan is not vacuous: it finds narrowing on a page that really is in that "
      "class", bool(_IMPORTS_NARROW.search(_read(NARROW_WITNESS))))
check("Y12 both narrowing pages read the tile OFF the panel through the one home's own gate, so a "
      "tile and its evidence are one fold and cannot drift",
      all("agreesToTheCent" in s and "const served = (key: string, fallback: number)" in s
          for s in (_fee_page, _led_page)))
_idx = _read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("Y13 registered in the index as §CORE-17.3n, by explicit declaration path",
      "§CORE-17.3n" in _idx and "commcalc/processor_money_drilldown.py" in _idx
      and "harness_batch14_drilldown.py" in _idx)
check("Y13b …and the three routes are named in it",
      all(r in _idx for r in PAGES))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Z. ARMED CONTROLS — a proof whose checks cannot fail is not a proof ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("Z1 A ONE-CENT TOTAL BREAKS THE TIE-OUT, in words, on the owner's screen",
      _tie(dd.fee_drilldowns(FEE_ROWS, dict(FEE_TOTALS,
                                            system_fee=FEE_TOTALS["system_fee"] + 0.01))
           ["system_fee"]) is False)
check("Z1b …and the words say which way and by how much, instead of rounding the disagreement away",
      "0.01" in dd.fee_drilldowns(FEE_ROWS, dict(FEE_TOTALS,
                                                 system_fee=FEE_TOTALS["system_fee"] + 0.01))
      ["system_fee"]["tie_out"]["words"])
check("Z2 A DROPPED ROW BREAKS IT — a panel folded over the wrong population cannot pass",
      _tie(dd.fee_drilldowns(FEE_ROWS[1:], FEE_TOTALS)["var"]) is False)
_drop_at = next(i for i, c in enumerate(LED_CELLS) if c["debits"])
check("Z2b …and so does a dropped CELL on the ledger",
      _tie(dd.ledger_drilldowns(LED_CELLS[:_drop_at] + LED_CELLS[_drop_at + 1:],
                                LED_ROLLUP["total"])["debits"]) is False)
# The careless declaration that folds an unrecorded side in at $0.00 — the one this batch refuses.
_careless = tuple(
    dict(spec, value=(lambda r: (r or {}).get("system_fee")), reason=None)
    if spec["key"] == "system_fee" else spec
    for spec in dd.fee_catalogue())
_sloppy = rd.tiles(_careless, FEE_ROWS, FEE_TOTALS)
check("Z3 A FIGURE FOLDED IN AT $0.00 NOBODY RECORDED STILL TIES — which is exactly why the row is "
      "where the honesty has to live",
      _tie(_sloppy["system_fee"]) is True
      and _sloppy["system_fee"]["figure"]["amount"]
      == FEE_PANELS["system_fee"]["figure"]["amount"])
check("Z3b …and the ONLY visible difference is the ROW: the careless one shows a measured $0.00 "
      "for a store-day the sales upload never carried; the honest one shows no figure and says why",
      _row(_sloppy["system_fee"], "S4 · 2026-10-01").get("amount") == 0.0
      and _row(_sloppy["system_fee"], "S4 · 2026-10-01").get("measured") is True
      and _row(FEE_PANELS["system_fee"], "S4 · 2026-10-01").get("amount") is None)
_tampered = [dict(c) for c in LED_CELLS]
_tampered[0]["credits"] = round(_tampered[0]["credits"] + 5.00, 2)
check("Z4 A TAMPERED BREAKDOWN CELL BREAKS THE TIE-OUT — the rows no longer add up to the figure "
      "the report shows",
      _tie(dd.ledger_drilldowns(_tampered, LED_ROLLUP["total"])["credits"]) is False)
check("Z4b …and the same on the bill-pay store-day breakdown",
      _tie(dd.billpay_drilldowns(
          [dict(r, sales_amount=(None if r["sales_amount"] is None else r["sales_amount"] + 1.00))
           for r in BP_CMP["rows"]], BP_TOTALS, sales_meta=BP_META, processor="epay")["sum"])
      is False)
_counted_blank = tuple(
    dict(spec, in_figure=(lambda _r: True)) if spec["key"] == "system_fee" else spec
    for spec in dd.fee_catalogue())
_cb = rd.tiles(_counted_blank, FEE_ROWS, FEE_TOTALS)
check("Z5 A COUNTED ROW WITH NO NUMBER MAKES THE TIE-OUT **None**, never True and never False — a "
      "sum missing a row was never a comparison",
      _tie(_cb["system_fee"]) is None
      and _cb["system_fee"]["tie_out"]["unmeasured_rows"] == 1
      and "cannot be added up" in _cb["system_fee"]["tie_out"]["words"],
      _cb["system_fee"]["tie_out"])
check("Z5b …and the honest declaration shows that same row and does NOT count it, so its tie-out "
      "is a real True", _tie(FEE_PANELS["system_fee"]) is True
      and FEE_PANELS["system_fee"]["tie_out"]["shown_not_counted"] == 1)
# A DAY NETTED WITH NO PROCESSOR FIGURE — the owner's un-netted ruling, applied to the comparison.
_netted = tuple(
    dict(spec, value=(lambda r: (r or {}).get("difference") or 0.0),
         in_figure=(lambda _r: True), reason=None)
    if spec["key"] == "difference" else spec
    for spec in dd.billpay_catalogue((lineage.MONTHLY_SALES,), (lineage.EPAY_DAILY,)))
_net_sloppy = rd.tiles(_netted, BP_CMP["rows"], BP_TOTALS)
check("Z6 A DAY NETTED WITH NO PROCESSOR FIGURE is caught on the row: the careless declaration "
      "counts a store-day the processor never reported at a difference of $0.00, which reads as "
      "'the two sides agreed' when nobody reported one of them",
      _row(_net_sloppy["difference"], "STORE-A · 2026-10-03").get("amount") == 0.0
      and _row(_net_sloppy["difference"], "STORE-A · 2026-10-03").get("in_figure") is True
      and _net_sloppy["difference"]["tie_out"]["counted_rows"]
      > BP_PANELS["difference"]["tie_out"]["counted_rows"])
check("Z6b …and the honest one leaves it un-netted: shown, not counted, with the processor's own "
      "absence sentence — the owner's 2026-10-03 ruling that a day with no figure stays un-netted",
      _row(BP_PANELS["difference"], "STORE-A · 2026-10-03").get("in_figure") is False
      and _row(BP_PANELS["difference"], "STORE-A · 2026-10-01").get("in_figure") is True)

print("\n   — the live defects, each with a regression that can fail —")
check("Z7 DEFECT 1 — the fee recon's REGISTER leg reads the MONTHLY UPLOAD and nothing else: "
      "`system_fee_per_store_day` queries raw_sales directly instead of the display union every "
      "other sales-derived figure reads, so on a feed-led open month (and the page opens on TODAY) "
      "the register side is short by whatever has not been promoted yet. REPORTED, not fixed: "
      "changing that read moves a figure, and the same function drives the hourly DM+ alert",
      '.table("raw_sales")' in _read("backend/app/modules/commcalc/epay_fee_recon.py")
      and "_sales_rows_union" not in _read("backend/app/modules/commcalc/epay_fee_recon.py")
      and dd.FEE_SYSTEM_FEED == lineage.MONTHLY_SALES,
      "the panel names the monthly upload ALONE, which is the truth about where that leg came from")
check("Z7b …and the panel therefore names ONE table on that leg, never the union — a provenance "
      "claim has to match the read that was made",
      {s["table"] for s in FEE_PANELS["system_fee"]["sources"]} == {lineage.MONTHLY_SALES})
check("Z8 DEFECT 2 — both recon legs coerce a store-day the other side never reported to $0.00 "
      "(`_f(system_by_sd.get(...))` / `_f(por)`), so 'reported nothing' and 'reported zero' print "
      "identically in the total. REPORTED; the panel separates them and the figure is identical",
      [r for r in FEE_ROWS if r["store_code"] == "S4"][0]["system_fee"] == 0.0
      and [r for r in FEE_ROWS if r["store_code"] == "S3"][0]["portal_fee"] == 0.0
      and FEE_PANELS["system_fee"]["figure"]["amount"] == FEE_TOTALS["system_fee"])
_filter_src = _LED_SRC
check("Z9 DEFECT 3 — `processor_ledger.filter_cells` has an escape for a cell with NO CATEGORY "
      "(the NO_CATEGORY_ID sentinel) but NONE for a cell with no MARKET, while the page's own "
      "filter folds a market-less cell under '(no market)'. So a deep link or a scheduled send "
      "picking '(no market)' returns an EMPTY report where the screen shows those cells. REPORTED, "
      "not fixed: making them agree changes a served figure",
      pl.filter_cells([{"store_code": "S", "store": "s", "tx_type": "t", "market": "",
                        "category": ""}], markets=["(no market)"]) == []
      and len(pl.filter_cells([{"store_code": "S", "store": "s", "tx_type": "t", "market": "",
                                "category": ""}], categories=[pl.NO_CATEGORY_ID])) == 1,
      "the category sentinel is honoured and the market one is not")
check("Z9b …and the asymmetry is in the code, not in a reading of it",
      "NO_CATEGORY_ID" in _filter_src and _filter_src.count("NO_MARKET") == 0)
check("Z10 DEFECT 4, FIXED where it is a database name rather than a figure — the processor "
      "ledger's footnote printed `commcalc.raw_payment_detail` verbatim (§19.38). It renders "
      "through sourceLabel() now, and no page in this batch prints a raw relation name",
      "sourceLabel(f.source" in _led_page
      and "<code>{f.source}</code>" not in _led_page
      and not any(t in src for t in _tables for src in (_bp_page, _fee_page, _led_page)
                  if ("<code>%s" % t) in src),
      "a figure-neutral copy change: the dollars beside it are untouched")
check("Z10b …and the LOCK THAT SHOULD HAVE CAUGHT IT is reported rather than widened: "
      "`harness_carrier_vocab_guard._TABLE_FIELD` matches only `.table` / `.source_table` / "
      "`.target_table`, so a feed table carried under any OTHER field name reaches copy unchecked. "
      "NOT widened here — `.source` is far too common a field name for that alternation, and "
      "guessing at someone else's lock is what the house rules forbid",
      "_TABLE_FIELD" in _read("backend/harness_carrier_vocab_guard.py")
      and "target_table|source_table|table" in _read("backend/harness_carrier_vocab_guard.py"))
check("Z11 DEFECT 5 — `classify_amount` returns (0.00, 0.00) for a feed amount that does not "
      "parse, so an unreadable row is folded into both columns as a genuine zero and nothing on "
      "the report can tell the two apart. REPORTED, not fixed: changing that contract could move a "
      "figure",
      pl.classify_amount("not a number", True) == (0.0, 0.0)
      and pl.classify_amount(0, True) == (0.0, 0.0))
check("Z12 DEFECT 6 — the Bill Payments endpoint filters its LINES by store but reports the "
      "BASIS counts (rows scanned / skipped / unmatched) over the whole org, so with a store "
      "picked the basis line describes a population the figures do not. REPORTED, not fixed: "
      "re-running those counts per store is a second extraction",
      "ex[\"rows_scanned\"]" in _ROUTER or 'ex["rows_scanned"]' in _ROUTER,
      "the panels this batch serves are folded over the FILTERED rows, which is the half that "
      "costs nothing")

print("\n" + "=" * 96)
print("PASS %d   FAIL %d" % (len(P), len(F)))
if F:
    print("\nFAILED:")
    for f in F:
        print("  - " + f)
sys.exit(1 if F else 0)
