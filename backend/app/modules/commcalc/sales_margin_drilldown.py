"""WHERE DID THIS SALES-AND-MARGIN FIGURE COME FROM — the declaration for the four reports that
answer *what did we sell, and at what margin* (§CORE-17, batch 18).

  /commcalc/gp                 Gross Profit Report — seven headline tiles over the store rows, and
                               the one page in this family whose tiles are a BROWSER fold.
  /commcalc/sales-analyzer     Retention Analysis (3MR) — a rep cohort, the subscribers it lost
                               before the third bill, and what each loss was worth.
  /commcalc/sales-comparison    Sales Comparison — the same items over two windows, per store.
  /commcalc/zero-sales         Zero Sales — the store-days and rep-days that sold no activation.

Chosen as one family because all four are read for one purpose: *what did we sell, and at what
margin*. They therefore share a sales-read vocabulary, and the SALES DISPLAY UNION is the one home
for "which tables did this period's sales actually come from" —
`processor_money_drilldown.sales_union_tables`, dereferenced below by identity and never re-spelled.

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED OFF THE PRODUCERS, NOT ASSUMED (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────────────────────
Reading the four producers (`gp_report.calc_gp_report`, `sales_analyzer.analyze`,
`sales_comparison.build` and `zero_sales.build_report`) gives **FOURTEEN** grains in **SIX** row
shapes, because a grain fixes the ROW, the UNIT **and** the PROVENANCE:

   1. a GP STORE ROW's REVENUE               money, out of the sales display union ∪ the carrier
                                             money feed ∪ the residual report. THREE figures on it
                                             — total revenue, phone cost and net profit — one fold
                                             of one row list differing only in which column holds
                                             the figure.
   2. a GP STORE ROW's CARRIER COMMISSION    the SAME store row in the SAME unit, and a provenance
                                             that is the CARRIER FEED ALONE — its own grain for
                                             exactly that reason (§CORE-17.3n), and not a cosmetic
                                             split: the two commission-leg tiles are the only
                                             figures on that page whose whole trail is one feed.
   3. a GP STORE ROW's REP PAY               the same row, the same unit, and the CALCULATED
                                             rep-commission snapshot (§CORE-17.3j) — what the last
                                             Run Calculation wrote down, not the sales it read.
   4. a GP STORE ROW's STORE EXPENSES        the same row, the same unit, and the ENTERED EXPENSE
                                             SHEET — plus, on a store whose salary this month was
                                             derived from its own payroll, an origin this report
                                             CANNOT NAME (defect 6). Its own grain by provenance.
   5. a 3MR REP ROW's COHORT                 count, out of the residual/subscriber report. THREE
                                             figures — the cohort, what churned out of it and what
                                             was retained.
   6. a CHURNED SUBSCRIBER's LOST SALE       money, out of the MONTHLY SALES ARCHIVE ALONE (defect
                                             1). TWO figures — what the device sold for and what
                                             accessory rode with it.
   7. a CHURNED SUBSCRIBER's LOST MRC        the SAME churn row in the SAME unit, separated ONLY by
                                             provenance (§CORE-17.3n): the monthly charge is the
                                             subscriber report's own column and no sale is involved
                                             in it at all.
   8. a CHURNED SUBSCRIBER's LOSS VERDICT    the same row again, in a different UNIT and over both
                                             relations at once — therefore its own grain
                                             (§CORE-17.3l). Three figures, one per verdict.
   9. a COMPARISON STORE-ITEM's BASE UNITS   count, out of the sales display union **as the BASE
                                             window's own read reports it**.
  10. a COMPARISON STORE-ITEM's BASE DOLLARS the same row, the same provenance, a different unit.
  11. a COMPARISON STORE-ITEM's COMPARE …    the same row and the same two units again, and a
                                             DIFFERENT provenance: the comparison window is a
                                             different period, so the union read it separately and
                                             may have read different tables for it. Two grains, and
                                             the sharpest case in the batch for §CORE-17.3n.
  12. a COMPARISON STORE's TRANSACTIONS      a different ROW LIST (per store, not per store-item),
                                             therefore a different grain and a different catalogue
                                             — two figures per window.
  13. a COMPARISON STORE's EXPENSES          a THIRD row list, money, out of the entered expense
                                             sheet. An expense is not an item sold and never enters
                                             the revenue beside it.
  14. a ZERO-SALES SCOPE ROW                 count, out of the sales display union for the store-day
                                             states and the SCHEDULE for the trading days — and for
                                             two of its four figures, out of NOTHING AT ALL, because
                                             "no feed landed" and "no schedule exists" are absences
                                             and an absence is not read out of a relation
                                             (§CORE-17.3i).

GROSS PROFIT IS REVENUE MINUS COST — AND ON THIS REPORT THE SUBTRACTION IS AT THE **ROW** (§CORE-17.3k)
──────────────────────────────────────────────────────────────────────────────────────────────────────
`calc_gp_report` computes `net_profit = total_rev - rep_pay - exp_total - net_phone_cost` **inside
the per-store loop**, stores it on the store row, and the header's figure is Σ of those rows. So net
profit DRILLS, and the panel is a real fold rather than one total minus three others: a store whose
expenses are missing shows up as a row, and the tie-out is a genuine comparison because the figure is
the page's own browser fold over the rows while the rows are the server's per-store arithmetic.

The same ruling goes the other way on the Sales Comparison: every `Δ` and every `Δ%` there is taken
at the TOTAL from two independent window folds, so neither gets a panel — and BOTH SIDES of each one
do, which is the whole of the answer a reader needs. That is the batch-17 `Δ vs prior month`
precedent, applied.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED PER PAGE BY READING THE PAGE
──────────────────────────────────────────────────────────────────────────────────────────────
  · `/commcalc/gp` — **NARROWED.** The page builds its seven tiles with
    `COLS_BASE.forEach(c => totals[c.key] = rows.reduce(…))` over `rows`, which is `allRows` after
    its own market and store filters. That is a client `reduce` over client-filtered rows — the
    §CORE-17.3d class exactly — so each panel is narrowed through `lib/drill-narrow` and each tile
    is READ OFF its narrowed panel rather than reduced a second time.
  · `/commcalc/sales-analyzer` — **NOT narrowed.** Its three filters (the rep search box, the
    clicked rep and the loss-type chips) narrow the two TABLES and the tiles do not move: every
    tile reads `data.totals` straight off the server. Narrowing the panels would make them explain
    a figure the tile is not showing. Reported (defect 3) rather than fixed, because the two honest
    repairs each change a figure.
  · `/commcalc/sales-comparison` — **NOT narrowed.** Its store and market pickers are QUERY
    PARAMETERS (`qs.set('stores', …)`), so the server folds again and every tile follows by
    construction. The page reduces nothing.
  · `/commcalc/zero-sales` — **NOT narrowed.** Its whole filter bar is query parameters too. The
    page does carry two `reduce` calls, and neither is a tile fold: both are inside `DigestPreview`,
    adding up the dry-run digest's own per-tenant counts. A scan that treated "this file contains
    `reduce(`" as "this page folds its tiles" would get this page wrong, which is why the lock
    asserts the GP page's reduce by its ACTUAL text and the other three by the absence of
    `narrowPanel`.

All four decisions are LOCKED, in both directions, by `harness_batch18_drilldown.py` §F — each with
a non-vacuity witness, so "the text is absent" can never be confused with "the scan is blind".

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **EVERY PERCENTAGE AND EVERY MARGIN PERCENTAGE.** `3MR retention`, every `Change %` on the
    comparison, `net_profit_attainment` on a GP row — §CORE-17.3h, and a margin percentage is a
    rate. The figures each one divides do drill.
  · **EVERY `Δ` ON THE SALES COMPARISON** — the subtract-at-the-TOTAL ruling above.
  · **THE GP `NP TARGET` COLUMN AND ITS ATTAINMENT.** A target is a DECLARED number (§CORE-17.3m),
    and this report cannot say which stores' targets anybody declared: the endpoint substitutes a
    code default for a store with no saved row, and the payload keeps no flag to tell the two apart.
    A panel naming the roster for a store whose figure came from a literal would be a provenance
    claim nothing checked — the §CORE-17.3n error in its worst form. REPORTED (defect 5).
  · **THE `comp_*` COLUMNS.** They are DISPLAY-ONLY: `total_rev` does not contain them and
    `net_profit` does not move with them, so there is no tile and no revenue term they explain. The
    notes on the carrier-commission panels say so, because a reader comparing the Comp columns
    against Total Revenue will not reconcile and should know why.
  · **THE COMMISSION MONTH-OF-LIFE LADDER COLUMNS.** `comm_month_<n>` is a re-partition of the very
    commission the two leg tiles already open, printed with its own totals on its own sheet — the
    batch-16 `by_vendor` refusal, one report along.
  · **THE ZERO-SALES `scopes` COUNT.** It counts store rows AND rep rows together, so it is a
    cardinality over two grains at once and has no single row list (defect 12). It is on no tile.
  · **THE COMPARISON EXPENSE LINE'S UNIT COLUMNS.** `_expense_cell` pins `current` / `previous` /
    `delta` at 0 because an expense has no unit. A panel over them would serve a $0-shaped count
    that ties trivially and looks measured, so none is served (defect 9).
  · **THE COMPARISON'S `detection_status`, THE ZERO-SALES LEGEND AND THE DIGEST PREVIEW.** A status,
    a set of state notes and a list of emails that would go out. None is a folded figure.
  · **EVERY FIGURE ON THE GP BY-REP VIEW.** `rep_rows` carries no revenue total, no expense and no
    net profit, and the page's seven tiles are the BY-STORE fold whichever view is open — so a panel
    opened from the By-Rep view would be about a row list the screen is not showing. The tiles drill
    in the By-Store view, which is the view they are the fold of.

ABSENCE IS NEVER ZERO
─────────────────────
A churned subscriber whose sale could not be matched carries NO figure for the two sale-side money
grains, in the producer's own vocabulary (`no_sales_feed_match`), because the $0.00 the producer
contributes for it is a fabrication rather than a measurement (defect 2). The figures do not move by
a cent: that row contributed 0.00 to the total and contributes 0.00 here — only the ROW changes, from
a measured zero into a stated blank, which is the entire argument for putting it there.

A ZERO OVER ROWS THAT ALL CARRY NO FIGURE IS THE ABSENCE OF A FIGURE. Where a column can be `None` on
every row — the churn money on a month whose sale archive is empty, the unknown-calendar count on an
estate whose schedule is complete — the entry point serves NO PANEL rather than a zero that ties
against itself and reads as measured.

AN EMPTY REPORT DRILLS TO NOTHING. Every entry point below returns an empty mapping when the producer
folded its totals over no rows at all.

LIVE DEFECTS THESE FOUR REPORTS HAVE TODAY — NONE FIXED, TWELVE REPORTED, NO FIGURE MOVED
─────────────────────────────────────────────────────────────────────────────────────────
 1. **The Retention Analysis reads the MONTH-END SALES ARCHIVE ALONE.** `sales_analyzer._sales_index`
    reads `raw_sales` and never the display union, which is the exact class the Gross Profit report
    was repaired for on 2026-10-17 ("a reader bound to the archive goes blind on an unpromoted
    month"). For any cohort month the promote step has not run on, every churn row's device model,
    sold price, cost, margin and accessory attach read empty — and three tiles then report $0.00 as
    though somebody had measured it. NOT fixed: switching the read moves figures. REPORTED, and the
    panels name the archive rather than the union so a reader can see which read answered.
 2. **An unmatched sale contributes a fabricated $0.00.** With no matching sale row, `sold`, `gp` and
    `acc` are set to `0.0` and go into `lost_value_sold` / `lost_accessory` indistinguishably from a
    device genuinely sold for nothing. The panels show those rows WITHOUT a figure and do not count
    them; the producer's total is unchanged because zero adds nothing. REPORTED.
 3. **On the Retention Analysis the filters move the tables and not the tiles.** The rep box, the
    clicked rep and the loss chips all filter in the browser while every tile is the server's
    whole-period fold, so under any filter the strip and the tables below it describe different
    populations and nothing on screen says so. NOT fixed (§CORE-17.3o). REPORTED.
 4. **The Zero Sales report throws away the provenance its own read returned.** `_zero_sales_core`
    binds `prows, _meta = _sales_rows_union(...)` and never reads `_meta`, so the one fact that says
    which tables a period's sales came from was discarded. That is why this batch had to collect it:
    the metas are now accumulated and attached, read-only, and no figure moved. REPORTED as the gap
    that made the collection necessary.
 5. **The GP report's `NP Target` is a CODE LITERAL for any store nobody set one for.**
    `get_gp_report` does `t = float(_tgt.get(...))` and falls back to `10000.0` on the `TypeError`,
    so a store with no saved target displays $10,000.00 — and its attainment percentage is measured
    against a number no manager chose. An unset target is not a target of ten thousand dollars
    (§CORE-17.3m). NOT fixed: removing the default moves a figure and empties a column. REPORTED,
    and it is exactly why that column gets no panel.
 6. **The GP `−Expenses` column folds payroll-derived salary rows whose own record it does not name.**
    `labour_actuals.as_expense_rows` synthesises a SALARY row per store from that month's own
    payroll, and those rows go into `exp_total` beside the entered sheet's. The payload records no
    per-store table for that half, so every row of the expenses panel whose store had measurable
    hours reports a stated, countable UNTRACEABLE ORIGIN beside the sheet (§CORE-17.3i). REPORTED.
 7. **The carrier keyword ladder behind the GP carrier columns has no `carrier_id` filter.** A
    known, already-reported defect, restated here because it bears on these figures: it is what
    places an undeclared payment type into a GP money column, so for a dual-affiliation org one
    carrier's keyword rules can classify the other's dollars. REPORTED again, in context, and not
    fixed here.
 8. **`sales_comparison.tally` computes `other_units` and nothing ever reads it.** Home-internet,
    SIM and unclassified device sales are counted per store "for honesty" and `build` never puts
    them on the payload, so the honesty never reaches a screen. The headline items are unaffected.
    REPORTED.
 9. **The comparison's expense line prints 0 units.** `_expense_cell` pins the three unit fields at
    0 and the table renders them, so a reader sees "0" where the right answer is "an expense has no
    unit". The producer's docstring calls the 0 honest; this declaration serves no panel for those
    columns rather than a zero that ties against itself. REPORTED.
10. **The Retention Analysis's sale index is capped at 300,000 rows and says nothing when it
    truncates.** `_fetch_all(…, cap=300000)` stops paging silently, and every churn past the cut
    then reads as "no sale matched" — a data-volume limit wearing the clothes of a missing sale.
    REPORTED.
11. **The Retention Analysis scopes the SUBSCRIBERS to a manager's stores and the SALE INDEX to the
    whole org.** A churn row's `store`, price and margin come from the sale, so a scoped manager can
    be shown another store's name and another store's dollars on their own rep's churn. REPORTED.
12. **The Zero Sales `scopes` total mixes two grains.** It is `len(rows)` over store rows AND rep
    rows, so "scopes" is a number with no row list of one grain behind it. It reaches the API
    contract and no tile. REPORTED, and it gets no panel.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3r.
"""
from app.modules.commcalc import carrier_dollar_class as _cdc
from app.modules.commcalc import carrier_pay_drilldown as _cpd
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import processor_money_drilldown as _pmd
from app.modules.core import report_drilldown

# ── THE PROVENANCE ANSWERS THIS FAMILY HAS, EVERY ONE OF THEM DEREFERENCED ───────────────────────

#: THE FIRST ANSWER IS NOT A CONSTANT, AND THAT IS THE POINT. Which tables the sales display union
#: ACTUALLY read for a period is a fact the READ knows and records, so it is resolved off the read's
#: own meta through the ONE home for that question — `processor_money_drilldown.sales_union_tables`,
#: which batch 14 wrote for exactly this and batch 17 bound by identity.
sales_union_tables = _pmd.sales_union_tables

#: The MONTH-END SALES ARCHIVE, named on its own because ONE report in this family reads it alone
#: rather than through the union (defect 1). The registry's constant, not a second spelling.
MONTHLY_SALES = _lineage.MONTHLY_SALES

#: The ePay carrier-money feed every GP money column but the residuals is read out of.
PAYMENT_DETAIL = _lineage.PAYMENT_DETAIL

#: The subscriber / residual report. It answers TWO different questions in this family: the MI and
#: ATU dollars on a GP store row, and the whole of the 3MR cohort.
MI_RESIDUAL = _lineage.MI_RESIDUAL

#: The master-agent feeds the GP commission column is read out of INSTEAD, for an org whose ePay
#: payment detail is empty. Which of the two answered is the PRODUCER's own gate, read off the
#: payload rather than re-decided here (`carrier_commission_tables`).
MA_COMMISSION = _lineage.MA_COMMISSION
MA_DAILY_TX = _lineage.MA_DAILY_TX

#: The CALCULATED rep-commission snapshot — what the last Run Calculation wrote down. DEREFERENCED
#: from `carrier_pay_drilldown`, which named it once for exactly this purpose (§CORE-17.3j).
REP_COMMISSIONS = _cpd.REP_COMMISSIONS

#: The ENTERED EXPENSE SHEET and the SCHEDULE. Both are registry constants as of this batch —
#: `shifts` had been spelled as a literal in TWO drill declarations and both are re-keyed in this
#: same commit, which is the half of a design fix that is usually left out (CLAUDE.md §19.18).
STORE_EXPENSES = _lineage.STORE_EXPENSES
SCHEDULE = _lineage.SCHEDULE

#: The STORE ROSTER, which is where a zero-sales scope comes from for a store that neither sold nor
#: was scheduled. Lives in a DIFFERENT schema, which is why panels naming it state theirs.
STORE_ROSTER = _lineage.STORE_ROSTER

#: AN ANSWER THAT IS NO ANSWER, and it is REPORTED rather than guessed. "No feed landed for this
#: store-day" and "no schedule exists for this store" are ABSENCES: there is no relation they were
#: read out of, and naming one would be a provenance claim about a row that was never written. Also
#: the payroll half of the GP expense figure (defect 6), which the payload records no table for.
#: `report_drilldown.panel` counts these under `untraceable_origins`.
UNNAMEABLE = None

#: IS A CARRIER DOLLAR'S COMPONENT EVIDENCE ABOUT THAT DOLLAR, OR A PLACEMENT OF LAST RESORT? The
#: ONE home for that question (§66, index §CORE-17.3j's "a fallback basis is not evidence"),
#: dereferenced BY IDENTITY. This family does not classify a dollar — it says, on the carrier
#: panels, whether every dollar the report classified rests on a basis the allowlist calls evidence,
#: so a figure resting on a keyword guess is not presented with the confidence of a declaration.
placement_is_evidence = _cdc.component_is_evidence

SCHEMA = "commcalc"
SCHEMA_STOREOPS = "storeops"

#: WHY A ROW IN THIS FAMILY CARRIES NO NUMBER. The producer vocabulary already in
#: `report_drilldown.UNMEASURED_REASONS`, reused rather than re-spelled: a churn whose sale the
#: archive does not carry is the same shape as a closing with no sales-feed match.
REASON_NO_SALE_MATCH = "no_sales_feed_match"


# ── NOTES — what each panel is, in the words the screen already uses ────────────────────────────

_GP_ROW_NOTE = (
    "One row per store, exactly as the table below lists them — including a row for money no store "
    "could be matched to, which is shown under the spelling the feed actually sent rather than "
    "dropped. These rows are the selection you are viewing: the figure above is this page's own "
    "total over them, so the two are one fold.")

_GP_REV_NOTE = _GP_ROW_NOTE + (
    " Revenue on this report is what the sales feed rang plus what the carrier paid plus the "
    "residuals, so each row names all three. The Comp columns in the table are NOT part of it and "
    "net profit does not move with them.")

_GP_NET_NOTE = _GP_ROW_NOTE + (
    " Net profit is revenue less rep pay, less store expenses, less phone cost — and that "
    "subtraction is done PER STORE, on the row, which is why it opens at all. A store whose "
    "expenses or payroll were never entered is a row here with the gap visible, instead of a "
    "shortfall spread invisibly across the company.")

_GP_PHONE_NOTE = _GP_ROW_NOTE + (
    " Phone cost is the cash the customer paid for the handset plus the carrier's reimbursement for "
    "it, so a row names the sales read and the carrier feed.")

_GP_COMM_NOTE = _GP_ROW_NOTE + (
    " This is the carrier's own commission money and nothing else — the one figure on this page "
    "whose whole trail is a single feed. It is a DECOMPOSITION of the Commission column and adds "
    "nothing to it: the first-month leg, the trailing legs and anything whose source states no "
    "month-of-life add back to that column exactly.")

_GP_COMM_GUESS_NOTE = (
    " PART OF THE CARRIER MONEY ON THIS REPORT WAS PLACED BY A PLATFORM GUESS. At least one payment "
    "type in this period is not declared by this company, so where its dollars landed was decided "
    "by a keyword rule rather than by anybody's word. Declaring the payment type replaces the guess "
    "with a fact.")

_GP_REP_PAY_NOTE = _GP_ROW_NOTE + (
    " Each figure was read out of what the last commission calculation WROTE DOWN for this month, "
    "not out of the sales and statement rows that calculation read — so the trail stops at the "
    "stored run, and re-running it is what changes these numbers.")

_GP_EXP_NOTE = _GP_ROW_NOTE + (
    " The expense figure is the sheet somebody entered for this month — or, where that month has "
    "none, the latest earlier month carried forward, exactly as the statements book it. Part of it "
    "is this month's OWN salary, worked out from the hours recorded for the store; this report does "
    "not record which payroll record that half came out of, so those rows name the sheet and report "
    "the rest as an origin that cannot be traced.")

_REP_COHORT_NOTE = (
    "One row per rep, exactly as the By-Rep table below lists them. The rows are that rep's share "
    "of the figure, counted off the subscriber report alone — no sale is involved in these three "
    "numbers. A rep who activated nobody in the cohort month is in no row here at all.")

_REP_RETAINED_NOTE = _REP_COHORT_NOTE + (
    " Retained is the cohort less what churned, and the report works that out PER REP as well as in "
    "total, which is why it opens: these rows are a real fold and not one total minus another.")

_CHURN_SALE_NOTE = (
    "One row per lost subscriber, exactly as the Churned-transactions table below lists them. This "
    "figure is read out of the MONTH-END SALES UPLOAD and NOT out of the daily sales feed, so for a "
    "month whose upload has not been promoted yet there is nothing for it to match against. A "
    "subscriber whose sale could not be matched is shown here WITHOUT a figure and is not counted — "
    "the report itself contributes $0.00 for such a row, which is a blank rather than a device "
    "somebody gave away.")

_CHURN_MRC_NOTE = (
    "One row per lost subscriber. This one is the subscriber report's own monthly charge and no "
    "sale is involved in it at all, which is why it opens separately from the two figures beside it "
    "— those depend on matching the churn to a sale and this one does not.")

_CHURN_VERDICT_NOTE = (
    "One row per lost subscriber, counting only the ones this verdict was reached for; every other "
    "churn is shown beside them with its own verdict, because which losses are NOT in the figure is "
    "most of what makes it readable. The verdict reads BOTH relations — the subscriber report for "
    "the churn and the sales upload for the margin and the attach — so a churn with no matching "
    "sale is judged on the half that was found.")

_CMP_BASE_NOTE = (
    "One row per store for this item, exactly as the table below groups them. These rows cover the "
    "whole selection the report was asked for: the store and market pickers are applied by the "
    "server before anything is folded, so this panel and the tile above it are one fold and cannot "
    "describe different stores.")

_CMP_CMP_NOTE = (
    "One row per store for this item, for the COMPARISON window. It opens separately from the base "
    "window's figure beside it because it is a different period, and the sales read may have "
    "answered out of different tables for it — which is exactly what a reader comparing two months "
    "needs to be able to see.")

_CMP_TXN_NOTE = (
    "One row per store, which is the row list the per-store summary below prints — not the "
    "store-and-item rows the item tiles open to. A transaction is counted once however many lines "
    "it carried.")

_CMP_EXP_NOTE = (
    "One row per store, from the expense sheet and not from anything sold. An expense never enters "
    "the revenue beside it. A month nobody entered inherits the latest earlier month, exactly as "
    "the statements book it, and the report says above which month that was.")

_ZS_ZERO_NOTE = (
    "One row per store, exactly as the table below lists them, counting the days the feed DID carry "
    "the store and none of its transactions was an activation or an upgrade. The rep rows beneath "
    "each store are shown here and deliberately not counted — they are the same days one grain "
    "finer, and counting both would count every day twice.")

_ZS_NOT_REPORTED_NOTE = (
    "One row per store, counting the days NOTHING landed for it. These rows name NO source, and "
    "that is the honest answer rather than an omission: a day with no rows was not read out of any "
    "relation, so there is nothing to point at. That is also why such a day is never a zero and "
    "never part of a run of consecutive zero days.")

_ZS_ALERT_NOTE = (
    "One row per store. A store is COUNTED only where its run of consecutive trading days with no "
    "activation has reached the threshold this company set, and every other store is shown beside "
    "it with its own run. The days come from the sales read and which days were TRADING days comes "
    "from the schedule, so a row names both.")

_ZS_CALENDAR_NOTE = (
    "One row per store. A store is COUNTED only where the schedule says nothing about it in this "
    "window — so every day was evaluated for it, which makes its run the loosest claim on the "
    "screen. Such a row names NO source, because 'there is no schedule' was not read out of one; "
    "a store that IS scheduled is shown beside it and not counted.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  SHARED READERS. Accessors and names only — nothing here adds, judges or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded column. The producer folded every row's share, so there
    is nothing here to add up."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _label_of(field):
    """PURE: the row exactly as the table below prints it."""
    def _read(r):
        return str((r or {}).get(field) or "") or None
    return _read


def carrier_commission_tables(payload):
    """PURE: which feed the GP Commission column was read out of, THROUGH THE PRODUCER'S OWN GATE.

    `calc_gp_report` fills that column from the ePay payment detail, EXCEPT for an org whose payment
    detail came back empty, where `router._compute_gp` falls through to the master-agent sources and
    the producer then stamps the Commission leg row with the BASIS it booked. So "which side
    answered" is a fact the payload already carries, and this reads it rather than deciding it a
    second time — a second gate here would be the sibling derivation the index rules forbid, and it
    would be a tenant branch into the bargain (RULE TWO).
    """
    src = ((payload or {}).get("commission_legs") or {}).get("sources") or ()
    comm = next((s for s in src if (s or {}).get("key") == "comm"), None) or {}
    if "basis" in comm:
        return (MA_COMMISSION, MA_DAILY_TX)
    return (PAYMENT_DETAIL,)


def carrier_placement_all_evidence(payload):
    """PURE: does EVERY carrier dollar this report classified rest on a basis the ONE home calls
    evidence?

    The GP report already carries `carrier_class_coverage` — `carrier_dollar_class.tally` over the
    very rows it bucketed — and that report breaks the money down BY BASIS. So this asks the
    allowlist about each basis that actually carried money, and nothing else: it holds no basis
    name, no threshold and no opinion of its own, and a basis nobody has ruled on reads as NOT
    evidence exactly as the allowlist intends. True when there is no carrier money at all, because
    a report with nothing to place has placed nothing on a guess.
    """
    rows = ((payload or {}).get("carrier_class_coverage") or {}).get("by_basis") or ()
    return all(placement_is_evidence((b or {}).get("basis"))
               for b in rows if report_drilldown.money((b or {}).get("amount")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE GROSS PROFIT REPORT — four grains on one store row, and the one narrowed page here.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The GP tiles, in the order the page prints them, each with the `store_rows` column it is Σ over.
#: Deliberately NOT a copy of the page's own tile list: the harness reads that list out of the page
#: source and fails the build if the two disagree, so there is ONE list and this is the declared
#: half of it.
GP_TILE_COLUMNS = ("total_rev", "comm_m1", "comm_m2_12", "net_phone_cost", "rep_pay", "exp_total",
                   "net_profit")

#: WHAT THE READER SEES ON EACH GP FIGURE — the page's own tile wording, in the panel's words.
GP_LABELS = {
    "total_rev": "Total revenue",
    "comm_m1": "First-month carrier commission",
    "comm_m2_12": "Trailing-leg carrier commission",
    "net_phone_cost": "Phone cost",
    "rep_pay": "Rep incentives",
    "exp_total": "Store expenses",
    "net_profit": "Net profit",
}


def _gp_fields(r):
    """PURE: what else a reader needs beside a GP figure on this row — and, where the store could
    not be matched at all, the producer's OWN sentence for why, never a rewording of it."""
    r = r or {}
    out = {"market": r.get("market") or None, "store code": r.get("store_code") or None}
    if r.get("store_unplaced"):
        out["no store matched"] = r.get("store_unplaced_why")
    return out


def _gp_salary_derived(r):
    """PURE: did this report derive a SALARY figure of its own for this store this month?

    Read off `labour_coverage.detail[<code>].hours_basis`, which the GP payload already carries and
    which is set exactly where hours were found to work a salary out from. It is stamped onto the
    row by `gp_drilldowns` before the fold, so this accessor decides nothing — it reads a flag the
    producer's own coverage report put there.
    """
    return bool((r or {}).get("_salary_measured"))


def _gp_expense_tables(r):
    """PURE: the entered sheet, plus an origin that CANNOT BE TRACED on a store whose salary half
    this report derived itself (defect 6). Naming a payroll table here would be a guess: the payload
    records none at row grain, and the half really did come from somewhere."""
    if _gp_salary_derived(r):
        return (STORE_EXPENSES, UNNAMEABLE)
    return (STORE_EXPENSES,)


def gp_catalogue(sales_tables, carrier_tables, *, placement_evidential=True):
    """PURE: the Gross Profit Report's seven panels — four grains on one store row.

    `sales_tables` is what the display union ACTUALLY read for this period, resolved by the ONE home
    off the read's own meta; nothing resolvable arrives as `()` and the panels then report an origin
    they could not trace rather than naming a feed on a guess. `carrier_tables` is the producer's
    own answer to which carrier feed filled the Commission column.

    `placement_evidential` False adds ONE sentence to the two carrier panels: part of this period's
    carrier money was placed by a keyword guess rather than by the company's declaration. It changes
    no figure and no provenance — it is the §66 question asked on behalf of a reader who would
    otherwise read a guess with the confidence of a fact.
    """
    sales = tuple(sales_tables or ())
    carrier = tuple(carrier_tables or ())
    common = {"row_label": _label_of("store"), "keys": ("store",), "fields": _gp_fields,
              "schema": SCHEMA, "unit": report_drilldown.UNIT_MONEY}
    comm_note = _GP_COMM_NOTE + ("" if placement_evidential else _GP_COMM_GUESS_NOTE)
    #: Revenue, phone cost and net profit are ONE grain: the same fold of the same rows over the
    #: same three relations, differing only in which column holds the figure.
    revenue_mix = dict(common, tables=(sales + carrier + (MI_RESIDUAL,)) or (UNNAMEABLE,))
    spec = {
        "total_rev": dict(revenue_mix, note=_GP_REV_NOTE),
        "net_profit": dict(revenue_mix, note=_GP_NET_NOTE),
        "net_phone_cost": dict(common, tables=(sales + carrier) or (UNNAMEABLE,),
                               note=_GP_PHONE_NOTE),
        "comm_m1": dict(common, tables=carrier or (UNNAMEABLE,), note=comm_note),
        "comm_m2_12": dict(common, tables=carrier or (UNNAMEABLE,), note=comm_note),
        "rep_pay": dict(common, tables=(REP_COMMISSIONS,), note=_GP_REP_PAY_NOTE),
        "exp_total": dict(common, tables=_gp_expense_tables, note=_GP_EXP_NOTE),
    }
    return tuple(
        dict(spec[col], key=col, total=col, label=GP_LABELS[col], value=_column(col), column=col)
        for col in GP_TILE_COLUMNS)


def gp_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/gp/{period}`.

    `payload` is the endpoint's own response, and every figure is its `totals` entry dereferenced —
    so a panel can never be a second opinion about the tile above it. The PAGE then narrows each
    panel to its own filtered rows and reads the tile off the narrowed panel (§CORE-17.3o), which is
    what keeps the figure and its evidence one fold under a filter.

    A period with no store rows drills to nothing. The `net_profit_target` column is deliberately
    absent: see the refusals in the header.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("store_rows") or ()) if r]
    totals = {k: v for k, v in (payload.get("totals") or {}).items() if k in GP_TILE_COLUMNS}
    if not rows or not totals:
        return {}
    # The store's own labour-coverage verdict, stamped onto the row so the expense panel's
    # provenance can be resolved per store. READ off the payload's own coverage report — this
    # decides nothing about any store, and it is done here rather than in the accessor so the
    # accessor stays a pure field read.
    detail = ((payload.get("labour_coverage") or {}).get("detail") or {})
    rows = [dict(r, _salary_measured=bool(
        (detail.get(str(r.get("store_code") or "").strip().upper())
         or detail.get(str(r.get("store_code") or "").strip()) or {}).get("hours_basis")))
        for r in rows]
    tables = sales_union_tables(payload.get("sales_source"))
    return report_drilldown.tiles(
        gp_catalogue(tables, carrier_commission_tables(payload),
                     placement_evidential=carrier_placement_all_evidence(payload)),
        rows, totals, cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE RETENTION ANALYSIS — four grains over two row lists.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The three verdicts the producer reaches about a loss, with the total each one is served under.
#: Keyed by the producer's OWN `loss_type` value, so a fourth verdict is a build failure here rather
#: than a tile with no panel (`harness_batch18_drilldown.py` §C).
LOSS_TOTALS = {"employee": "employee_driven", "customer": "customer_driven", "mixed": "mixed"}

LOSS_LABELS = {
    "employee": "Losses the sale itself explains",
    "customer": "Losses a clean sale still suffered",
    "mixed": "Losses only partly explained by the sale",
}


def _rep_label(r):
    """PURE: the rep as the By-Rep table prints them."""
    return str((r or {}).get("rep") or "") or None


def _rep_fields(r):
    r = r or {}
    return {"cohort": r.get("cohort"), "churned": r.get("churned")}


def rep_cohort_catalogue():
    """PURE: the 3MR By-Rep panels — one grain, three figures, all out of the subscriber report."""
    common = {"row_label": _rep_label, "keys": ("rep_login",), "fields": _rep_fields,
              "schema": SCHEMA, "unit": report_drilldown.UNIT_COUNT, "tables": (MI_RESIDUAL,)}
    return (
        {"key": "cohort", "total": "cohort", "label": "Subscribers activated in the cohort month",
         "value": _column("cohort"), "column": "subscriber_id", "note": _REP_COHORT_NOTE, **common},
        {"key": "churned", "total": "churned", "label": "Subscribers lost before the third bill",
         "value": _column("churned"), "column": "mi_deactivation_date",
         "note": _REP_COHORT_NOTE, **common},
        {"key": "retained", "total": "retained", "label": "Subscribers still active at the third bill",
         "value": _column("retained"), "column": "subscriber_status",
         "note": _REP_RETAINED_NOTE, **common},
    )


def _churn_label(r):
    """PURE: the churn row as the table lets a reader find it — who, which number, which device."""
    r = r or {}
    parts = [str(r.get("rep") or "").strip(), str(r.get("phone_number") or "").strip(),
             str(r.get("device_model") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def _churn_fields(r):
    r = r or {}
    return {"store": r.get("store") or None, "days active": r.get("days_active"),
            "why it ended": r.get("reason") or None, "verdict": r.get("loss_type") or None}


def sale_matched(r):
    """PURE: did the producer find a sale for this churn at all?

    `sales_analyzer.analyze` sets `sold_for` from the matched sale and leaves it `0.0` when nothing
    matched, which is the defect this answers rather than hides (defect 2): a device sold for
    nothing and a device nobody could find are the same number in that payload. The one signal the
    row still carries is the DEVICE MODEL, which the matcher fills from the sale and which falls
    back to the subscriber's plan name only when there was no sale — so the honest test is the one
    the producer itself can support: a sale was matched when the row carries a sold price.
    """
    return report_drilldown.money((r or {}).get("sold_for")) != 0.0


def _sale_money(field):
    """PURE: the sale-side figure, or None where there was no sale to read it out of."""
    def _read(r):
        return (r or {}).get(field) if sale_matched(r) else None
    return _read


def _sale_tables(r):
    """PURE: the month-end archive on a row whose sale was matched, and NO table on one whose was
    not — the blank shown for such a row was read out of nothing."""
    return (MONTHLY_SALES,) if sale_matched(r) else ()


def _sale_reason(r):
    return None if sale_matched(r) else REASON_NO_SALE_MATCH


def churn_sale_catalogue():
    """PURE: the two churn figures that depend on matching a sale. ONE grain."""
    common = {"row_label": _churn_label, "keys": ("phone_number", "activation_date"),
              "fields": _churn_fields, "schema": SCHEMA, "unit": report_drilldown.UNIT_MONEY,
              "tables": _sale_tables, "reason": _sale_reason, "note": _CHURN_SALE_NOTE}
    return (
        {"key": "lost_value_sold", "total": "lost_value_sold",
         "label": "What the lost handsets sold for", "value": _sale_money("sold_for"),
         "column": "ext_price", **common},
        {"key": "lost_accessory", "total": "lost_accessory",
         "label": "Accessory sold alongside the lost handsets",
         "value": _sale_money("accessory_sale"), "column": "ext_price", **common},
    )


def churn_mrc_catalogue():
    """PURE: the churn figure that depends on NO sale — the same row and unit as the two above,
    separated only by provenance (§CORE-17.3n)."""
    return (
        {"key": "lost_mrc", "total": "lost_mrc", "label": "Monthly charge lost",
         "value": _column("charged_mrc"), "column": "base_mrc",
         "row_label": _churn_label, "keys": ("phone_number", "activation_date"),
         "fields": _churn_fields, "schema": SCHEMA, "unit": report_drilldown.UNIT_MONEY,
         "tables": (MI_RESIDUAL,), "note": _CHURN_MRC_NOTE},
    )


def _verdict_is(kind):
    """PURE: the predicate for WHICH churns a verdict figure counts. Every other churn is SHOWN with
    its own verdict beside them — the difference between a drill-down and a filtered list."""
    def _in(r):
        return str((r or {}).get("loss_type") or "") == kind
    return _in


def _verdict_one(kind):
    def _read(r):
        return 1 if str((r or {}).get("loss_type") or "") == kind else None
    return _read


def churn_verdict_catalogue():
    """PURE: the three loss-verdict counts — the same churn row in a different UNIT and over BOTH
    relations at once, which makes it its own grain (§CORE-17.3l)."""
    common = {"row_label": _churn_label, "keys": ("phone_number", "activation_date"),
              "fields": _churn_fields, "schema": SCHEMA, "unit": report_drilldown.UNIT_COUNT,
              "tables": (MI_RESIDUAL, MONTHLY_SALES), "note": _CHURN_VERDICT_NOTE}
    return tuple(
        {"key": total, "total": total, "label": LOSS_LABELS[kind], "value": _verdict_one(kind),
         "in_figure": _verdict_is(kind), "column": "loss_type", **common}
        for kind, total in sorted(LOSS_TOTALS.items()))


def analyzer_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/sales-analyzer/{period}`.

    `payload` is the endpoint's own response. The rep panels fold the `reps` rows; the churn panels
    fold the `churned` rows, which is a DIFFERENT row list and therefore a different catalogue — a
    panel of rep rows opened from a churn figure would be a panel about rows its figure is not made
    of.

    A VACUOUS TIE IS REFUSED. For a cohort month whose sale archive carries nothing, every churn row
    has no sale-side figure at all; the producer's total is then $0.00 over zero measured rows, which
    ties trivially and reads as a measurement. Those two panels are not served in that case.
    """
    payload = payload or {}
    totals = dict(payload.get("totals") or {})
    reps = [r for r in (payload.get("reps") or ()) if r]
    churn = [r for r in (payload.get("churned") or ()) if r]
    cap = cap or report_drilldown.ROW_CAP
    out = {}
    if reps:
        out.update(report_drilldown.tiles(
            rep_cohort_catalogue(), reps,
            {k: v for k, v in totals.items() if k in ("cohort", "churned", "retained")}, cap=cap))
    if not churn:
        return out
    sale_totals = {k: v for k, v in totals.items()
                   if k in ("lost_value_sold", "lost_accessory")}
    if not any(sale_matched(r) for r in churn):
        sale_totals = {}
    out.update(report_drilldown.tiles(churn_sale_catalogue(), churn, sale_totals, cap=cap))
    out.update(report_drilldown.tiles(
        churn_mrc_catalogue(), churn, {k: v for k, v in totals.items() if k == "lost_mrc"}, cap=cap))
    out.update(report_drilldown.tiles(
        churn_verdict_catalogue(), churn,
        {k: v for k, v in totals.items() if k in LOSS_TOTALS.values()}, cap=cap))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  THE SALES COMPARISON — two windows, two provenances, three row lists.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The two figures a window contributes to an item tile, with the UNIT each is in. The pair is
#: declared ONCE and generated for both windows, so the base and compare halves cannot drift apart.
CMP_WINDOW_COLUMNS = (("current", "previous", report_drilldown.UNIT_COUNT, "units sold"),
                      ("current_rev", "previous_rev", report_drilldown.UNIT_MONEY, "revenue"))

#: The per-store summary's own two pairs.
CMP_STORE_COLUMNS = (("current_txns", "previous_txns", report_drilldown.UNIT_COUNT, "transactions"),
                     ("current_rev", "previous_rev", report_drilldown.UNIT_MONEY, "revenue"))


def _cmp_fields(r):
    return {"market": (r or {}).get("market") or None}


def _cmp_item_rows(rows, category_key):
    """PURE: the report's own rows for ONE item. SHAPING, not computing: it selects, it does not
    fold — a per-item panel over every item's rows would be a panel about rows its figure is not
    made of, and `tiles` takes one row list."""
    return [r for r in (rows or ())
            if r and str(r.get("category_key") or "") == str(category_key)]


def _cmp_catalogue(columns, base_tables, compare_tables, *, base_note, compare_note,
                   expense=False):
    """PURE: one row list's panels — the base window's figures and the comparison window's, with
    their two provenances kept apart (§CORE-17.3n).

    `expense` names the expense row list, whose figures come out of the entered sheet rather than out
    of anything sold. Every Δ and every Δ% is absent on purpose — see the refusals in the header.
    """
    common = {"row_label": _label_of("store"), "keys": ("store",), "fields": _cmp_fields,
              "schema": SCHEMA}
    out = []
    for cur, prev, unit, what in columns:
        if expense and unit == report_drilldown.UNIT_COUNT:
            continue   # an expense has no unit; the producer pins the field at 0 (defect 9)
        out.append({"key": cur, "total": cur, "label": "This window's " + what,
                    "value": _column(cur), "column": cur, "unit": unit,
                    "tables": tuple(base_tables or ()) or (UNNAMEABLE,),
                    "note": base_note, **common})
        out.append({"key": prev, "total": prev, "label": "The comparison window's " + what,
                    "value": _column(prev), "column": prev, "unit": unit,
                    "tables": tuple(compare_tables or ()) or (UNNAMEABLE,),
                    "note": compare_note, **common})
    return tuple(out)


def comparison_item_catalogue(base_tables, compare_tables):
    """PURE: one sale ITEM's four panels — base units, base dollars, comparison units, comparison
    dollars."""
    return _cmp_catalogue(CMP_WINDOW_COLUMNS, base_tables, compare_tables,
                          base_note=_CMP_BASE_NOTE, compare_note=_CMP_CMP_NOTE)


def comparison_store_catalogue(base_tables, compare_tables):
    """PURE: the overall strip's four panels, over the PER-STORE summary rows."""
    return _cmp_catalogue(CMP_STORE_COLUMNS, base_tables, compare_tables,
                          base_note=_CMP_TXN_NOTE, compare_note=_CMP_TXN_NOTE)


def comparison_expense_catalogue():
    """PURE: the expense line's two money panels. One provenance, and it is not a sales read."""
    return _cmp_catalogue((CMP_WINDOW_COLUMNS[1],), (STORE_EXPENSES,), (STORE_EXPENSES,),
                          base_note=_CMP_EXP_NOTE, compare_note=_CMP_EXP_NOTE, expense=True)


def comparison_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/sales-comparison`, keyed `item.<key>.<figure>`,
    `overall.<figure>` and `expenses.<figure>`.

    `payload` is the endpoint's own response, including the `source_meta` the two sales reads
    recorded — which is where both provenances come from, resolved by the ONE home for that question
    off each read's own meta. A window the union read nothing for arrives as `()` and the panels then
    report an origin they could not trace.

    An item with no rows at all drills to nothing, and so does the expense line when the expense read
    failed (the producer then serves no expense category rather than $0.00).
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    cats = [c for c in (payload.get("totals_by_category") or ()) if c]
    if not rows or not cats:
        return {}
    meta = payload.get("source_meta") or {}
    base = sales_union_tables(meta.get("base"))
    comp = sales_union_tables(meta.get("compare"))
    cap = cap or report_drilldown.ROW_CAP
    out = {}
    for c in cats:
        key = str(c.get("key") or "")
        if not key:
            continue
        side = _cmp_item_rows(rows, key)
        if not side:
            continue
        if c.get("expense"):
            cat, prefix = comparison_expense_catalogue(), "expenses."
        else:
            cat, prefix = comparison_item_catalogue(base, comp), "item.%s." % key
        for k, panel in report_drilldown.tiles(cat, side, c, cap=cap).items():
            out[prefix + k] = panel
    summary = [r for r in (payload.get("store_summary") or ()) if r]
    overall = dict(payload.get("overall") or {})
    if summary and overall:
        for k, panel in report_drilldown.tiles(
                comparison_store_catalogue(base, comp), summary, overall, cap=cap).items():
            out["overall." + k] = panel
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  ZERO SALES — one scope row, and two of its four figures read out of nothing.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The producer's own grain word for a STORE row. Dereferenced so "which rows does a store-grain
#: total count" is the producer's answer and not a second spelling of it.
GRAIN_STORE = "store"


def _zs_store(r):
    """PURE: a store row counts toward a store-grain figure; a rep row beneath it does not — the
    same days one grain finer, and counting both would count every day twice."""
    return str((r or {}).get("grain") or "") == GRAIN_STORE


def _zs_fields(r):
    r = r or {}
    return {"state": r.get("state") or None, "consecutive zero days": r.get("zero_days"),
            "trading calendar": r.get("trading_calendar") or None,
            "alerting": ("yes" if r.get("alerting") else "no")}


def _zs_count(field):
    """PURE: a store row's own tally, and NO figure at all on a rep row — a rep row is shown for
    context and the store-grain total does not contain it."""
    def _read(r):
        return (r or {}).get(field) if _zs_store(r) else None
    return _read


def _zs_alerting(r):
    return 1 if (_zs_store(r) and (r or {}).get("alerting")) else None


def _zs_unknown_calendar(r):
    return 1 if (_zs_store(r) and str((r or {}).get("trading_calendar") or "") == "unknown") else None


def _zs_scope_tables(sales_tables):
    """PURE: the sales read AND the schedule for a store the report could evaluate, and the roster
    alone for one it could not place in either — a store on the roster that neither sold nor was
    scheduled is in the row list BECAUSE the roster says it exists."""
    sales = tuple(sales_tables or ())

    def _read(r):
        if not _zs_store(r):
            return ()
        if str((r or {}).get("state") or "") == "rule_refused":
            return (STORE_ROSTER,)
        return (sales + (SCHEDULE,)) or (STORE_ROSTER,)
    return _read


def _zs_absence_tables(r):
    """PURE: NO table, ever. "Nothing landed" and "there is no schedule" are absences; they were not
    read out of a relation and naming one would be a claim about a row nobody wrote
    (§CORE-17.3i)."""
    return ()


def _zs_present(read):
    """PURE: the predicate for WHICH rows a store-COUNT figure counts — the rows that carry a number
    for it. A store that is not alerting, and a store whose schedule IS known, are SHOWN beside the
    counted ones with their own state rather than counted as a zero: a row counted with no number is
    a sum that cannot be checked, and the tie-out would come back unmeasurable for every panel."""
    def _in(r):
        return read(r) is not None
    return _in


def union_tables_over(metas):
    """PURE: the display union's tables over SEVERAL reads.

    The Zero Sales window spans months and the union is read ONCE PER MONTH, so a window's
    provenance is the union of what each month's read reported. Each month is resolved by the ONE
    home (`sales_union_tables`); this only merges, in the order the reads gave, and resolves nothing
    itself.
    """
    out = []
    for m in (metas or ()):
        for t in sales_union_tables(m):
            if t not in out:
                out.append(t)
    return tuple(out)


def zero_sales_catalogue(sales_tables):
    """PURE: the four Zero Sales panels — one row list, two provenance answers and two absences.

    The two figures ABOUT AN ABSENCE name no table at all, which `panel` reports as a countable
    untraceable origin. That is the honest answer and it is also the finding: the operator asking
    "where did '14 store-days not reported' come from" is asking about days there is no record of.
    """
    common = {"row_label": _label_of("label"), "keys": ("scope_key",), "fields": _zs_fields,
              "schema": SCHEMA_STOREOPS, "unit": report_drilldown.UNIT_COUNT}
    return (
        {"key": "stores_alerting", "total": "stores_alerting",
         "label": "Stores at or past the alert threshold", "value": _zs_alerting,
         "in_figure": _zs_present(_zs_alerting),
         "tables": _zs_scope_tables(sales_tables), "column": "trans_date",
         "note": _ZS_ALERT_NOTE, **common},
        {"key": "store_days_measured_zero", "total": "store_days_measured_zero",
         "label": "Store-days the feed carried with no activation",
         "value": _zs_count("days_measured_zero"), "in_figure": _zs_store,
         "tables": _zs_scope_tables(sales_tables),
         "column": "trans_date", "note": _ZS_ZERO_NOTE, **common},
        {"key": "store_days_not_reported", "total": "store_days_not_reported",
         "label": "Store-days nothing landed for", "value": _zs_count("days_not_reported"),
         "in_figure": _zs_store,
         "tables": _zs_absence_tables, "note": _ZS_NOT_REPORTED_NOTE, **common},
        {"key": "stores_unknown_calendar", "total": "stores_unknown_calendar",
         "label": "Stores the schedule says nothing about", "value": _zs_unknown_calendar,
         "in_figure": _zs_present(_zs_unknown_calendar),
         "tables": _zs_absence_tables, "note": _ZS_CALENDAR_NOTE, **common},
    )


def zero_sales_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/zero-sales`.

    `payload` is the endpoint's own response, including the `sales_source` meta this batch had to
    start collecting because the read's provenance was being discarded (defect 4).

    A VACUOUS TIE IS REFUSED for the unknown-calendar count: on an estate whose schedule covers
    every store, no row carries a figure for it and the producer's 0 would tie against nothing. The
    same refusal applies to the alerting count on a window where no store is alerting.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    totals = dict(payload.get("totals") or {})
    if not rows or not totals:
        return {}
    for key, read in (("stores_alerting", _zs_alerting),
                      ("stores_unknown_calendar", _zs_unknown_calendar)):
        if key in totals and not any(read(r) is not None for r in rows):
            totals.pop(key)
    return report_drilldown.tiles(
        zero_sales_catalogue(union_tables_over(payload.get("sales_source"))),
        rows, {k: v for k, v in totals.items() if k != "scopes"},
        cap=cap or report_drilldown.ROW_CAP)
