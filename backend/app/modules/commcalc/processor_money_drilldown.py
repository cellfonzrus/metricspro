"""WHAT DID THE PROCESSOR OWE US, AND WHAT ACTUALLY LANDED — the declaration for the three
bill-payment / processor money reports (§CORE-17, batch 14).

  /commcalc/bill-payments      Bill Payments — the bill-pay lines EXTRACTED from the sales export,
                               beside the processor's own bill-pay report for the same store-days.
  /commcalc/epay-fee-recon     Fee Reconciliation — the customer service fee the register rang
                               against the fee the processor's settlement report counted.
  /commcalc/processor-ledger   Daily Debits & Credits — every money movement the processor made on
                               the dealer's account, day × transaction type, with the net.

THE FAMILY'S GRAIN COUNT, MEASURED RATHER THAN ASSUMED (§CORE-17.3l)
───────────────────────────────────────────────────────────────────
Three routes. Reading the four producers (`billpay_extract.rollup` / `compare_with_feed`,
`epay_fee_recon.build_recon`, `processor_ledger.fold_cells` / `day_type_rollup`) gives **SEVEN**
grains in **TWO** row shapes and **ONE** unit:

| grain | the figure folded over it | why it is not one of the others |
|---|---|---|
| a STORE-DAY's bill-pay money AS THE SALES EXPORT RANG IT | "Σ extracted" | money, out of the sales display union |
| a STORE-DAY's bill-pay money AS THE PROCESSOR REPORTED IT | "Σ carrier report" | the SAME row in the SAME unit, out of a completely different relation — the processor's bill-pay feed |
| a STORE-DAY's DIFFERENCE between those two | "Difference (days both carry)" | the same row again, naming BOTH, so it can disagree with either (§CORE-17.3k) |
| a STORE-DAY's SERVICE FEE AS THE REGISTER RANG IT | "System fee" | a different quantity AND a different read: the monthly sales upload alone, never the union (defect 1) |
| a STORE-DAY's SERVICE FEE AS THE SETTLEMENT REPORT COUNTED IT | "Portal fee" | the same row, the same unit, the settlement report |
| a STORE-DAY's fee VARIANCE | "Net variance" | the same row, naming both, and folded per row |
| a PROCESSOR × DAY × TYPE × STORE movement CELL | "Debits", "Credits", "Net" | not a store-day at all: the row is one transaction TYPE on one day on one dealer account, and all three of its figures are columns of the SAME cell |

**Six of the seven are keyed by a store-day and they are still six grains**, which is batch 13's
ruling doing the work twice over: *one is what the register rang, one is what the processor's own
report says, and a third names both.* A single catalogue stretched over them would have to claim the
sales export produced the processor's figure — the lie the tie-out would then assert on our behalf.

ONE MODULE, THREE CATALOGUES, AND WHY THAT IS NOT THE SIBLING DEFECT
────────────────────────────────────────────────────────────────────
The batch-11/12 argument, one subsystem further out. What these three reports share is not a row
shape but the thing a drill-down exists for — *which stored relation was this bill-payment money read
out of* — and there are exactly FOUR answers in the family: the sales display union, the processor's
bill-pay settlement feed, the processor's dealer-account payment feed, and the master-agent daily-TX
feed. Three files would be three answers to one question. A catalogue, by contrast, is per ROW LIST:
the bill-pay store-days, the fee-recon store-days and the ledger cells are three different
populations, and one catalogue over all three would be a panel about rows its report is not showing.

DUPLICATE CHECK (build gate, CLAUDE.md). Searched the index for an existing mechanism serving the
same purpose: §CORE-17.3a (`closing/cash_recon_drilldown` — the tile shape, REUSED through
`report_drilldown.tiles`), §CORE-17.3c (`closing/day_recon_drilldown` — the ePay BANK-DEPOSIT day
recon, a different pair of legs on a different page), §CORE-17.3k
(`commcalc/carrier_pay_drilldown` — carrier commission, not bill payments), §CORE-17.3l
(`asset/device_asset_drilldown`), §12 (the bill-pay coverage recon's own reader) and §2 /
`data_lineage_registry.billpay_processor_feed` (which table a resolved processor's feed lands in —
DEREFERENCED here, never re-derived). Nothing already answers "which rows is this bill-payment /
processor-money figure" for these three routes; this EXTENDS the one fold rather than siblinging it,
and computes no figure of its own.

ONE FACT, ONE HOME, DEREFERENCED — never copied:
  · **which table a resolved bill-pay processor's feed is** — `data_lineage_registry
    .billpay_processor_feed`. An unknown processor resolves to NOTHING and the row reports an origin
    it could not trace; a wrong provenance claim is worse than an admitted gap.
  · **which table each PROCESSOR-LEDGER feed is** — `processor_ledger.FEED_SHAPES[*]["source"]`,
    which this batch re-keyed onto the registry constants so the ledger's own answer and the
    registry's are one fact (locked: `harness_batch14_drilldown.py` §Y2).
  · **which tables the DISPLAY sales union may have read** — `data_lineage_registry
    .SALES_DISPLAY_SOURCES`, filtered by what the read itself reported in its `meta`. The union does
    NOT tag rows with their source, so the slice-level answer is the honest one (§CORE-17.3c's
    `_b2b_sales_rows_sourced` shape); a constant would be wrong half the year and naming both
    unconditionally would be a provenance claim nothing checked.
  · **"nothing was recorded here"** — `report_drilldown.UNMEASURED_REASONS` keys only. This batch
    adds NO new reason: `not_recorded` and `no_processor_data` are already the one home's words for
    exactly these two absences.

A DECLARED FEE IS CONFIG, NOT EVIDENCE (§CORE-17.3m). Whether a tenant charges a bill-payment fee at
all is DECLARED per org (mig 1046) and is never inferred here. This module names the relation a fee
figure was READ OUT OF; it does not name, assert or dereference a fee RATE, and it never infers from
an absent fee line that no fee was charged.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, computes no business figure of its own. PURE: no I/O, no framework
import, no clock, no org / carrier / tenant / processor / product / store name (RULE TWO).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3n.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import processor_ledger as _pl
from app.modules.core import report_drilldown

#: The tables the DISPLAY sales union may read, as the registry spells them. Not a list written twice.
SALES_DISPLAY_SOURCES = tuple(_lineage.SALES_DISPLAY_SOURCES)

#: The ONE table the fee recon's "system" leg reads — `epay_fee_recon.system_fee_per_store_day`
#: queries the monthly upload and nothing else, which is defect 1 below rather than a choice this
#: declaration gets to improve on.
FEE_SYSTEM_FEED = _lineage.MONTHLY_SALES

#: The settlement report the fee recon's "portal" leg is read out of — `epay_ingest.per_store_day`.
FEE_PORTAL_FEED = _lineage.EPAY_DAILY

#: WHY A ROW CARRIES NO NUMBER. Keys of `report_drilldown.UNMEASURED_REASONS`, imported by name so a
#: typo is a build failure rather than a bare field name on an owner's screen (locked: §Y12).
REASON_NO_SALES_ROW = "not_recorded"
REASON_NO_PROCESSOR_ROW = "no_processor_data"


# ── NOTES — what each panel is, in the words the reader needs. ─────────────────────────────────

_BILLPAY_SALES_NOTE = (
    "One row per store-day. The figure is the bill-pay money your own sales export rang, summed over "
    "exactly these days. A store-day that appears ONLY on the processor's report is shown here "
    "without a figure and is not counted — the export carries nothing for it, which is not the same "
    "as its having sold nothing.")

_BILLPAY_FEED_NOTE = (
    "One row per store-day, as the processor's own bill-payment report states it. A day the "
    "processor's report does not carry is shown without a figure and not counted: an absent day is a "
    "day nobody reported, never a settled $0.00.")

_BILLPAY_DIFF_NOTE = (
    "Your export minus the processor's report, per store-day, and only for the days BOTH sides "
    "carry — the figure is the sum of those per-day differences, not one total subtracted from "
    "another. A day only one side carries is shown here and deliberately not counted, because a "
    "day with one leg missing has no difference to measure.")

_FEE_SYSTEM_NOTE = (
    "One row per store-day: the customer service fee your register rang, as the monthly sales upload "
    "holds it. A store-day the upload carries no fee line for at all is shown without a figure and "
    "not counted.")

_FEE_PORTAL_NOTE = (
    "One row per store-day: the fee the processor's settlement report counted. A store-day the "
    "report does not carry is shown without a figure and not counted.")

_FEE_VAR_NOTE = (
    "The register's fee minus the processor's, per store-day — folded per row and then added up, "
    "which is why it opens at all. Where only ONE side reported, the variance IS the whole of that "
    "side: the row says which side was missing, so a large variance can be read as a missing feed "
    "rather than as a missing fee.")

_LEDGER_NOTE = (
    "One row per transaction type per day per store on the processor's own account feed. Each row "
    "names the feed it was read out of, so a figure made of two processors' movements says which "
    "part came from which.")


# ── WHERE A BILL-PAYMENT FIGURE CAME FROM ─────────────────────────────────────────────────────

def sales_union_tables(meta):
    """WHICH TABLES THE DISPLAY SALES UNION ACTUALLY READ for this period, off the read's OWN meta.

    `router._sales_rows_union` merges `daily_sales_feed` and `raw_sales` per (day × store) cell and
    does NOT tag a merged row with the side it came from, so there is no per-ROW answer to give. The
    read does report which source LED, how many rows each held, and whether the other source filled,
    swapped or back-filled anything — and that is a fact the read knows, so it is read rather than
    guessed (§CORE-17.3c's posture, at the grain the producer records).

    The leading source is named when it returned rows at all; the other is named only when the meta
    says it CONTRIBUTED (a filled cell, a richer cell or a completeness back-fill). A name the
    registry's display union does not know is ignored — a stored string is not a licence to put an
    arbitrary identifier on an owner's screen. Nothing resolvable ⇒ `()`, and the panel then reports
    an origin it could not trace."""
    meta = meta if isinstance(meta, dict) else {}
    out = []
    primary = str(meta.get("primary") or "").strip()
    other = str(meta.get("other") or "").strip()
    if primary in SALES_DISPLAY_SOURCES and int(meta.get("primary_rows") or 0) > 0:
        out.append(primary)
    # WHAT COUNTS AS THE OTHER SIDE HAVING CONTRIBUTED, and why `other_only_rows` is in the list
    # (added 2026-10-26, batch 20). The per-DAY union reports a contribution as a filled, richer or
    # back-filled CELL; the TRANSACTION-grain union (`router._sales_rows_union_txn`) reports the same
    # fact as `other_only_rows` -- the transactions the primary did not have, kept whole. Without
    # that key this function named only the leading relation for a transaction-grain read, so a
    # report whose rows genuinely came out of BOTH would have under-stated its own provenance. One
    # home answers the question for BOTH unions rather than a second copy appearing beside it for the
    # transaction grain. A DAY meta carries no `other_only_rows`, so every existing caller is
    # byte-identical (asserted by `harness_batch20_drilldown.py` §A2b).
    contributed = any(int(meta.get(k) or 0) > 0
                      for k in ("filled_rows", "richer_rows", "completeness_rows",
                                "other_only_rows"))
    if other in SALES_DISPLAY_SOURCES and int(meta.get("other_rows") or 0) > 0 and (
            contributed or not out):
        out.append(other)
    return tuple(out)


def billpay_feed_tables(processor):
    """The table behind the resolved bill-pay processor — `data_lineage_registry
    .billpay_processor_feed`, the one home, dereferenced. An unknown or unconfigured processor
    resolves to `()` and the panel reports an untraceable origin rather than naming a table on a
    guess."""
    table = _lineage.billpay_processor_feed(processor)
    return tuple(t for t in (table,) if t)


def _billpay_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (r.get("store"), r.get("date")) if str(x or "").strip()) \
        or "(no store-day)"


def _billpay_fields(r):
    r = r or {}
    return {
        "lines": r.get("sales_count"),
        "verdict": r.get("status"),
        "from_sales": r.get("sales_amount"),
        "from_processor": r.get("feed_amount"),
    }


def billpay_catalogue(sales_tables, feed_tables):
    """The three drillable figures on the Bill Payments report, as a declaration table.

    Each names the `totals` key its figure lives under, the field each row contributes, the tables
    that field was read from PER ROW, and which rows the figure counts. Nothing is computed.

    THE THREE FIGURES THE REPORT DOES *NOT* DECLARE, and why (so a missing link is never read as an
    oversight): the bill-pay LINE COUNT — the Lines tab below IS the list it counts; the
    DAYS-COMPARED / sales-only / feed-only counts — same ruling; and the By-store and By-token
    tables, which re-partition lines the report already prints in full and already carry their own
    totals (the batch-12 "a rollup that already has a drill of its own" refusal)."""
    sales_tables, feed_tables = tuple(sales_tables or ()), tuple(feed_tables or ())
    common = {"row_label": _billpay_label, "keys": ("store", "date"),
              "fields": _billpay_fields, "schema": "commcalc"}
    return (
        dict(common, key="sum", total="sum",
             label="Bill payments extracted from the sales export",
             value=lambda r: (r or {}).get("sales_amount"),
             tables=lambda r: (sales_tables if (r or {}).get("sales_amount") is not None else ()),
             reason=lambda r: (None if (r or {}).get("sales_amount") is not None
                               else REASON_NO_SALES_ROW),
             note=_BILLPAY_SALES_NOTE),
        dict(common, key="sum_feed", total="sum_feed",
             label="Bill payments on the processor's own report",
             value=lambda r: (r or {}).get("feed_amount"),
             tables=lambda r: (feed_tables if (r or {}).get("feed_amount") is not None else ()),
             reason=lambda r: (None if (r or {}).get("feed_amount") is not None
                               else REASON_NO_PROCESSOR_ROW),
             note=_BILLPAY_FEED_NOTE),
        dict(common, key="difference", total="difference",
             label="Difference on the days both sides carry",
             value=lambda r: (r or {}).get("difference"),
             tables=lambda r: ((sales_tables + feed_tables)
                               if (r or {}).get("difference") is not None else ()),
             reason=_billpay_difference_reason,
             note=_BILLPAY_DIFF_NOTE),
    )


def _billpay_difference_reason(r):
    """WHICH SIDE is missing on a day that has no difference — the row's own `status`, read rather
    than re-decided (`billpay_extract.compare_with_feed` already recorded it)."""
    r = r or {}
    if r.get("difference") is not None:
        return None
    return REASON_NO_PROCESSOR_ROW if r.get("sales_amount") is not None else REASON_NO_SALES_ROW


def billpay_drilldowns(per_store_day, totals, *, sales_meta=None, processor=None, cap=None):
    """PURE: one panel per drillable figure on the Bill Payments report, folded over the SAME
    per-store-day rows and the SAME totals the endpoint serves.

    A figure the report did not produce — `sum_feed` and `difference` are **None** when no processor
    report covers the period — gets NO key at all, which is `tiles()`'s own rule: a panel asserting
    $0.00 behind nothing would read as a loaded feed that settled nothing."""
    return report_drilldown.tiles(
        billpay_catalogue(sales_union_tables(sales_meta), billpay_feed_tables(processor)),
        list(per_store_day or []), dict(totals or {}),
        cap=cap or report_drilldown.ROW_CAP)


# ── THE FEE RECONCILIATION ────────────────────────────────────────────────────────────────────

def _fee_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (r.get("store_code"), r.get("close_date"))
                      if str(x or "").strip()) or "(no store-day)"


def fee_sides(r):
    """WHICH SIDES OF THE FEE COMPARISON REPORTED, in words — off the producer's own `in_system` /
    `in_portal` verdicts, so the row and the report cannot disagree about it."""
    r = r or {}
    sys_, por = bool(r.get("in_system")), bool(r.get("in_portal"))
    if sys_ and por:
        return "both sides reported"
    if sys_:
        return "only the register rang a fee"
    if por:
        return "only the processor's report counted a fee"
    return "neither side reported"


def fee_variance_tables(r):
    """The tables a store-day's fee VARIANCE was read out of — whichever sides actually reported.
    Naming both on a one-sided row would claim a relation held a figure it does not."""
    r = r or {}
    out = []
    if r.get("in_system"):
        out.append(FEE_SYSTEM_FEED)
    if r.get("in_portal"):
        out.append(FEE_PORTAL_FEED)
    return tuple(out)


def _fee_fields(r):
    r = r or {}
    return {"sides": fee_sides(r), "register": r.get("system_fee"),
            "processor": r.get("portal_fee"), "flagged": bool(r.get("flag"))}


def fee_catalogue():
    """The three drillable figures on the Fee Reconciliation report.

    NOT declared, and why: "Store-days flagged" is a COUNT whose list is the table below it (the
    rows it counts are exactly the highlighted ones), and the tolerance is a threshold rather than a
    figure — it has no rows at all."""
    common = {"row_label": _fee_label, "keys": ("store_code", "close_date"),
              "fields": _fee_fields, "schema": "commcalc"}
    return (
        dict(common, key="system_fee", total="system_fee",
             label="Service fee the register rang",
             value=lambda r: ((r or {}).get("system_fee") if (r or {}).get("in_system") else None),
             tables=lambda r: ((FEE_SYSTEM_FEED,) if (r or {}).get("in_system") else ()),
             reason=lambda r: (None if (r or {}).get("in_system") else REASON_NO_SALES_ROW),
             note=_FEE_SYSTEM_NOTE),
        dict(common, key="portal_fee", total="portal_fee",
             label="Service fee the processor's report settled",
             value=lambda r: ((r or {}).get("portal_fee") if (r or {}).get("in_portal") else None),
             tables=lambda r: ((FEE_PORTAL_FEED,) if (r or {}).get("in_portal") else ()),
             reason=lambda r: (None if (r or {}).get("in_portal") else REASON_NO_PROCESSOR_ROW),
             note=_FEE_PORTAL_NOTE),
        dict(common, key="var", total="var", label="Net variance (register − processor)",
             value=lambda r: (r or {}).get("var"),
             tables=fee_variance_tables,
             note=_FEE_VAR_NOTE),
    )


def fee_drilldowns(rows, totals, *, cap=None):
    """PURE: one panel per drillable figure on the Fee Reconciliation report, over the SAME recon
    rows and the SAME totals the endpoint serves. An empty recon drills to nothing."""
    rows = list(rows or [])
    if not rows:
        return {}
    return report_drilldown.tiles(fee_catalogue(), rows, dict(totals or {}),
                                  cap=cap or report_drilldown.ROW_CAP)


# ── THE PROCESSOR MONEY-MOVEMENT LEDGER ───────────────────────────────────────────────────────

def ledger_tables(cell):
    """WHICH FEED THIS CELL WAS READ OUT OF — `processor_ledger.FEED_SHAPES`' own answer for the
    cell's stored `processor` value, dereferenced rather than mapped again here.

    A cell whose processor is not a shape the ledger knows resolves to `()`: the report folded it
    from somewhere, but nothing in the platform says where, and that is reported as an untraceable
    origin rather than guessed (§CORE-17.3i)."""
    shape = _pl.FEED_SHAPES.get(str((cell or {}).get("processor") or "").strip())
    table = str((shape or {}).get("source") or "").strip()
    return (table,) if table else ()


def _ledger_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (r.get("date"), r.get("tx_type"), r.get("store"))
                      if str(x or "").strip()) or "(no cell)"


def _ledger_fields(r):
    r = r or {}
    return {
        "store": r.get("store"),
        "market": r.get("market") or "",
        "category": r.get("category") or "",
        "feed_rows": r.get("rows"),
        "debits": r.get("debits"),
        "credits": r.get("credits"),
    }


def ledger_catalogue():
    """The three drillable figures on the processor ledger's strip.

    ALL THREE OPEN, including the net. `net` is `credits − debits` folded PER CELL and then added up,
    so the rows and the figure are reached two different ways and the tie-out is a real comparison
    (§CORE-17.3k) rather than a restatement.

    NOT declared: the "Days · feed rows" tile, which is two COUNTS whose lists are the table below
    it, and the day × type rollup rows themselves, which are a re-partition of exactly these cells
    and are printed in full with their own subtotals."""
    common = {"row_label": _ledger_label,
              "keys": ("processor", "date", "tx_type", "store_code"),
              "fields": _ledger_fields, "tables": ledger_tables, "schema": "commcalc",
              "note": _LEDGER_NOTE}
    return (
        dict(common, key="debits", total="debits", value="debits",
             label="Debits — money the processor took or charged"),
        dict(common, key="credits", total="credits", value="credits",
             label="Credits — money the processor paid you"),
        dict(common, key="net", total="net", value="net", label="Net (credits − debits)"),
    )


def ledger_drilldowns(cells, total, *, cap=None):
    """PURE: one panel per figure on the processor ledger's strip, over the SAME cells and the SAME
    `day_type_rollup` total the endpoint serves. An empty range drills to nothing."""
    cells = list(cells or [])
    if not cells:
        return {}
    return report_drilldown.tiles(ledger_catalogue(), cells, dict(total or {}),
                                  cap=cap or report_drilldown.ROW_CAP)
