"""WHERE DID THIS PERFORMANCE FIGURE COME FROM — the declaration for the four reports that answer
*how are our reps and stores performing, and who needs coaching* (§CORE-17, batch 20).

  /commcalc/productivity     Productivity — output per hour worked against each store's own
                             baseline, per employee.
  /commcalc/coaching         Rep Coaching — which KPIs each rep missed and the money they are
                             leaving on the table because of it.
  /commcalc/atu-opportunity  Autopay Opportunity — card-paying customers who are NOT enrolled, and
                             the recharge sitting unconverted behind them.
  /commcalc/kpi-failing      Failing KPIs — every KPI below its target, at store and at rep grain.

ONE FAMILY, AND THE SEAM IS STATED RATHER THAN GLOSSED. All four are read for one purpose: deciding
who to coach and on what. Two of them measure a PERSON (output per hour, money lost to a missed
tier), one measures a DOOR against the carrier's own bar, and the fourth measures a SPECIFIC
behaviour at the counter — asking a card-paying customer one question. They belong together because
a manager reads them in that order: the failing KPI says what is wrong, the coaching card says what
it costs, the productivity view says whether the person had the hours to do better, and the autopay
report is the one concrete thing to ask them to change on Monday. The fourth was the one worth
challenging, and it stays: it is a per-STORE attach count on a coachable counter behaviour, not a
money report, and it keeps its own catalogue and its own grains rather than being forced into theirs.

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED OFF THE PRODUCERS, NOT ASSUMED (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────────────────────
Driving `productivity.compute_productivity`, the coaching per-rep fold in `commcalc/router.py`,
`atu_opportunity.fold_transactions` / `summarize` / `by_store` and `kpi_failing.store_rows` /
`rep_rows` / `summarize` gives the triples below, because a grain fixes the ROW, the UNIT **and** the
PROVENANCE. `harness_batch20_drilldown.py` §H measures them off the panels as a SET and prints the
count, so the number in the index is read off the harness rather than asserted here:

   1. a REP AT A STORE's BOXES                 count, out of whichever sales relations the period's
                                               display union actually read.
   2. a REP AT A STORE's ACCESSORY DOLLARS     the SAME row and the SAME provenance in a different
                                               UNIT, therefore its own grain (§CORE-17.3l).
   3. a REP AT A STORE's HOURS WORKED          the same row a third time, in a third unit, and a
                                               provenance that is the TIME CLOCK alone — never the
                                               schedule, which records an intention rather than an
                                               hour somebody worked.
   4. a REP's INCENTIVE AT RISK                money, out of what the last commission calculation
                                               WROTE DOWN (§CORE-17.3j) — and withheld entirely on a
                                               row whose pay breakdown that run never itemised,
                                               asked of `payout_slice_evidence` rather than read off
                                               the stored column (§CORE-17.3j's own ruling).
   5. a REP's CHARGEBACKS DEDUCTED             money, out of a relation NO migration in this
                                               repository creates — so every row of that panel
                                               reports a stated, countable UNTRACEABLE ORIGIN
                                               (§CORE-17.3i). Same grain shape as 4, separated only
                                               by provenance (§CORE-17.3n), and the provenance is
                                               "nothing nameable".
   6. a REP's OPS CHARGEBACKS DEDUCTED         money again on the same row, out of the recorded
                                               penalty ledger — a third provenance on one rep row.
   7. a REP's MONEY LEFT ON THE TABLE          the same unit on the same row, and the provenance is
                                               whichever of those three that row actually has.
   8. a REP's TIER VERDICT                     count, out of the same calculated snapshot as 4.
   9. a STORE's CARD ACTIVATIONS               count, out of the TRANSACTION-grain sales union.
  10. a STORE's UNCONVERTED RECHARGE           money, same row, same provenance, different unit.
  11. a FAILING-KPI STORE ROW                  count, out of the carrier's own per-door activity
                                               report and nothing else.
  12. a FAILING-KPI REP ROW                    count, out of the calculated snapshot's own KPI
                                               values — which is the SAME triple as 8, on a
                                               different report. Two reports sharing one grain is
                                               not a defect; it is the thing a grain count is for.

A SUBTRACTION, A SCALING AND A MIXED CARDINALITY — THE THREE REFUSALS (§CORE-17.3k, §CORE-17.3h)
───────────────────────────────────────────────────────────────────────────────────────────────
  · **`card_open` per store IS a subtraction at the ROW** (`card_acts - card_atu`, inside
    `by_store`), and the report carries no company figure for it at all — so there is nothing to
    open it from. Both sides of it DO drill. The subtract-at-the-row ruling applies and still
    yields no panel, because the figure the panel would explain does not exist on the payload.
  · **"Failing KPI cells" is a cardinality over TWO row shapes.** The tile is
    Σ(store failing counts) + Σ(rep failing counts), so there is no single row list it was folded
    from — the batch-18 `scopes` and batch-19 `entities*` refusal, a third time. Both sides would
    drill and NEITHER is on the payload as a figure of its own, which is REPORTED (defect 8) rather
    than repaired by adding a figure to somebody else's report.
  · **"Annualised" is the monthly carry times twelve, done in the browser.** A scaling of a figure
    is not a fold of rows; the monthly figure is what a reader needs the rows for, and it is itself
    refused for the reason below.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED PER PAGE BY READING THE PAGE
──────────────────────────────────────────────────────────────────────────────────────────────
This family splits TWO AND TWO, and the split was found by reading each page rather than by guessing
from the endpoint:

  · `/commcalc/productivity` and `/commcalc/atu-opportunity` are **SERVER-FOLDED and NOT narrowed**.
    Every filter on both pages is a QUERY PARAMETER — productivity appends `stores` / `markets` /
    `reps` to its URL and autopay puts `period` / `start` / `end` / `stores` / `markets` in its
    query string — so the server folds again on every change and a panel follows its figure by
    construction. Neither page contains a `reduce(` at all.
  · `/commcalc/coaching` and `/commcalc/kpi-failing` **fold their own tiles in the browser** over
    the rows their filters leave on screen, and are therefore **NARROWED** through
    `lib/drill-narrow.narrowPanel`, with each tile reading its number off its narrowed panel through
    the shared `agreesToTheCent` gate. Coaching reduces four times and ignores the server's own
    `summary` entirely; failing-KPIs reduces twice and re-folds its by-metric card as well.

`harness_batch20_drilldown.py` §F locks all four decisions in BOTH directions off each page's own
text, with a non-vacuity witness for every scan, so "the text is absent" can never be confused with
"the scan is blind".

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **EVERY RATE, INDEX AND SCORE, AND THERE ARE A LOT OF THEM** (§CORE-17.3h). Boxes per hour and
    accessory dollars per hour at both store and company grain; every `vs store` index, which is a
    rate divided by a rate; every attach percentage on the autopay report; the share of the card
    recharge base forgone; the stack-ranking SCORE and the performance-review SCORE, which are
    weighted averages of attainments that are themselves ratios; every per-item attainment. In every
    case the figures the rate divides are offered instead, and on the productivity page those two
    are the drillable numbers printed beside it.
  · **THE TWO COMMISSION-CARRY FIGURES AND THE CUSTOMER SAVING ON THE AUTOPAY REPORT.** Each is a
    fold multiplied by a RATE SOMEBODY TYPED INTO A SETTINGS SCREEN, and one of them has no rows at
    all: its base is a recharge total typed in by hand because those recharges never reach the point
    of sale, which the producer says in its own words on the payload. A panel over store rows would
    present an assumption as a measurement, which is the §CORE-17.3m ruling about declared numbers
    one report along. The open recharge the first of them is derived from DOES drill, and is served.
  · **EVERY CUSTOMER-BASIS COUNT ON THE AUTOPAY REPORT.** The headline counts are per distinct
    mobile number and the per-store table carries no mobile-number counts at all, so those figures
    have no row list on this payload (defect 6). The ACTIVATION-basis counts beside them, which the
    per-store table does carry, are served — and the report already states, in its own caveats, that
    the two denominators differ by design.
  · **THE AUTOPAY REPORT'S `card_total` RECHARGE AND EVERY NON-CARD FIGURE.** `by_store` accumulates
    open recharge only, and only for card transactions, so neither has rows here.
  · **THE STACK RANKING AND THE PERFORMANCE REVIEW TABS.** Two of the four tabs behind the
    productivity route are weighted-attainment scorecards served by two other endpoints, and every
    figure on them is a score or an attainment — refused above. The fourth tab is the item registry,
    which is configuration.
  · **`stores_total` AND `reps_total` ON THE FAILING-KPI REPORT, AND `reps` ON THE COACHING CARD.**
    Each is the number of rows the panel beside it already states in its own header, so a panel for
    one would restate its own row count as a finding.
  · **"WORST KPI".** A label, not a figure.
  · **A FIGURE NO ROW CARRIES A NUMBER FOR.** Where every row of a panel would be shown without a
    figure — a period in which the chargeback file names nobody, a metric no store is failing — NO
    PANEL is served, because a $0.00 or a 0 over zero counted rows ties trivially and would read as
    a measurement.

ABSENCE IS NEVER ZERO
─────────────────────
Four absences in this family are already distinguished by the producers and were reaching no surface:

  · A rep with sales and **no closed time-clock punch** carries `hours` 0.00 and the producer's own
    `no_hours` flag. The hours panel shows that row WITHOUT a figure and does not count it, in the
    one vocabulary's words (`no_time_clock_punches`). **No figure moves by a cent**, because such a
    row contributed 0.00 to the store's hours and contributes 0.00 here; only the ROW changes, from a
    measured zero into a stated blank, which is the entire argument for putting it there.
  · A rep whose pay breakdown **the last calculation never itemised** has a stored subtotal of 0.00
    that is not a measurement at all, and the coaching endpoint multiplies it into an "at risk" of
    $0.00. `payout_slice_evidence` is the one home for that question and is ASKED, never inferred
    from the column: such a row is shown without a figure and not counted.
  · A **KPI the carrier's feed never published** is `no_data` on the producer's own row, and that is
    not a store passing the metric. It is shown without a figure; a store that MET the metric is
    shown with a measured zero and marked as not counted, so the panel tells the three states apart
    instead of printing one dash for two of them.
  · A rep **the chargeback file carries no line for** is shown without a figure rather than as a rep
    with $0.00 of chargebacks.

AN EMPTY REPORT DRILLS TO NOTHING. Every entry point below returns an empty mapping when the
producer folded its totals over no rows.

THE PANEL CANNOT DISCLOSE WHAT THE REPORT DOES NOT, BY CONSTRUCTION
───────────────────────────────────────────────────────────────────
Every entry point's only input is the endpoint's OWN RESPONSE — never a client, never an org id,
never a second read. So a panel is folded over rows the caller is already being served, after every
filter the endpoint applied, and a drill-down cannot become a disclosure. That matters here because
the productivity endpoint applies NO per-store scope filter at all (defect 1), which is REPORTED
rather than quietly repaired: the panels disclose exactly what the report already does, and the
remedy is the scope, not the panel.

LIVE DEFECTS THESE FOUR REPORTS HAVE TODAY — TEN REPORTED, NONE FIXED, NO FIGURE MOVED
─────────────────────────────────────────────────────────────────────────────────────
 1. **`GET /productivity/{period}` applies no RBAC scope at all.** The other three in this family
    run `scope_keyset` / `in_keyset`; this one is org-scoped and nothing narrower, so a store-scoped
    manager is served every store's employees, their hours and their accessory dollars. REPORTED,
    not fixed: adding a scope filter changes who sees which figure, which is a permissions decision
    and not a drill-down one. This is the batch-19 defect-11 shape, one family along.
 2. **A productivity rep row cannot tell "sold nothing" from "the sales feed never named them".**
    `_prod_gather` adds a rep it found only on the time clock with `boxes` and `acc_sales`
    initialised to 0.0, and `compute_productivity` carries no transaction count onto the row — so a
    person who was on the clock and sold nothing, and a person the sales union never mentioned, are
    the same 0.00. Both are COUNTED exactly as the producer counted them and the panels say so in
    their notes (the batch-15 "counted but not trusted" posture), because the alternative would move
    a figure the report shows.
 3. **`no_hours` cannot tell a missing punch from a genuine zero.** It is `hours <= 0` over a sum
    that defaults to 0.0 when the time clock answered for nobody, so a rep clocked in for zero hours
    and a rep with no punch row are one state. The panel reports the one state the producer has, in
    the one vocabulary's words, rather than inventing a distinction the data does not carry.
 4. **The coaching endpoint's chargeback deduction comes out of a relation this repository does not
    create.** `commcalc.chargeback_items` is read by the handler and NO migration here creates it —
    only index statements guarded with `undefined_table`. So it is not a registered relation, cannot
    have a plain name, and naming it on a panel would put a raw database name on a manager's screen.
    Every row of the chargeback panel, and the chargeback share of every money-on-the-table row,
    therefore reports an origin that CANNOT BE TRACED, stated and counted (§CORE-17.3i). This is the
    `exec_month_drilldown` finding of batch 17, unchanged, and the remedy is the same: a migration
    that creates or formally registers the table, which a drill-down PR does not do. REPORTED.
 5. **An ops-chargeback row's own lines need not sum to the deduction it contributes.** The handler
    CAPS the deduction at what is left of the rep's pay after the legacy deduction, and serves the
    uncapped lines beside it — so a reader adding up the lines on a capped row lands above the
    figure. The panel's figure is the per-rep deduction the total is Σ of, so the tie-out is real;
    the discrepancy is between two things the report already prints. REPORTED.
 6. **The autopay report's headline counts and its per-store table are on two different bases.**
    The counts are per distinct mobile number and the table is per transaction, which the report
    states as a caveat; the consequence for a drill-down is that the three customer-basis tiles have
    no row list and get no panel, while the activation-basis figures beside them do. REPORTED.
 7. **The autopay endpoint was discarding the provenance its own sales read returned.**
    `rows, _meta = _sales_rows_union_txn(...)` and `_meta` was never read, so the one fact saying
    which relations the transactions came out of was thrown away every request. Carried through
    read-only now; no figure moved. Same class as batch 19's defects 2 and 3 — a read that returns
    `(rows, meta)` has a meta because somebody needed it, and binding it to `_meta` is how it gets
    lost. Third report in two batches with this exact shape.
 8. **The failing-KPI summary's `failing_cells` mixes two row shapes**, exactly as batch 18's
    `scopes` and batch 19's `entities*` did: store failing counts and rep failing counts added
    together. Neither side is on the payload as a figure of its own, so neither can be opened and
    the mixed figure gets no panel. REPORTED rather than repaired by adding two figures to that
    report.
 9. **The failing-KPI page re-derives its by-metric roll-up that the server already served.** The
    endpoint folds `summary.by_metric` and the page builds its own over the filtered rows instead,
    so the same question has two folds. The panels are declared off the SERVER's figures and the
    page narrows them to its own selection, which is how the two are pinned together rather than
    left to agree by luck. The duplication itself is REPORTED, not removed: removing it would change
    which fold the page prints.
10. **The coaching page re-derives "below full tier" that the endpoint also computes.** The handler
    tests `tier < 1.0` per row for the at-risk figure and again for the summary, and the page tests
    it a third time in the browser. The verdict is now STATED on each row by the handler and this
    declaration dereferences that, so the panel and the figure cannot disagree; the page's own copy
    is REPORTED and left alone, because changing which expression the displayed count comes from
    would move a figure's source on screen for no drill-down reason.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3t.
"""
from app.modules.commcalc import carrier_pay_drilldown as _cpd
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import kpi_failing as _kpif
from app.modules.commcalc import payout_slice_evidence as _pse
from app.modules.commcalc import processor_money_drilldown as _pmd
from app.modules.core import report_drilldown as report_drilldown

# ── THE PROVENANCE ANSWERS THIS FAMILY HAS, EVERY ONE OF THEM DEREFERENCED ───────────────────────

#: WHICH TABLES THE PLATFORM'S SALES READ ACTUALLY ANSWERED OUT OF — not a constant, and that is the
#: point. Resolved off the read's OWN meta through the ONE home for that question,
#: `processor_money_drilldown.sales_union_tables`, bound BY IDENTITY exactly as batches 14, 17, 18
#: and 19 bound it. Extended in this same commit to recognise the TRANSACTION-grain union's way of
#: reporting that the second relation contributed, so one home answers for both unions.
sales_union_tables = _pmd.sales_union_tables

#: THE TIME CLOCK. A registry NAME constant as of this batch, with its one pre-existing literal copy
#: re-keyed in the same commit — the half of a design fix that usually gets left out (§19.18).
TIME_CLOCK = _lineage.TIME_CLOCK

#: THE RECORDED PENALTY LEDGER — the ops chargebacks applied to a person's commission.
OPS_CHARGEBACK = _lineage.OPS_CHARGEBACK

#: THE CARRIER'S OWN PER-DOOR ACTIVITY REPORT. The store-grain KPI actuals are read out of this and
#: out of nothing else, which is what makes a failing store row a single-relation grain.
DLAR_STORE = _lineage.DLAR_STORE

#: WHAT THE LAST COMMISSION CALCULATION WROTE DOWN. Dereferenced from `carrier_pay_drilldown`, which
#: named it once for exactly this purpose (§CORE-17.3j, batch 11) — a second copy in the feed
#: registry would be the divergence that registry exists to prevent, and that reasoning is written
#: into the registry beside where the constant would have gone.
REP_COMMISSIONS = _cpd.REP_COMMISSIONS

#: AN ANSWER THAT IS NO ANSWER, and it is REPORTED rather than guessed. The employee chargeback file
#: the coaching endpoint reads is created by no migration in this repository (defect 4), so there is
#: no registered relation to name and naming one would be a provenance claim about a table nobody
#: creates. `report_drilldown.panel` counts these under `untraceable_origins` (§CORE-17.3i).
UNNAMEABLE = None

SCHEMA = "commcalc"

#: The TIME CLOCK lives in another schema, so a panel naming it carries that schema explicitly rather
#: than inheriting this family's. Dereferenced from the one home's own schema vocabulary.
SCHEMA_STOREOPS = "storeops"

#: WHY A ROW IN THIS FAMILY CARRIES NO NUMBER. Three shapes, every one of them a word that already
#: has a home: two added to `report_drilldown.UNMEASURED_REASONS` by this batch, and one that is
#: `payout_slice_evidence`'s OWN basis value — the producer's vocabulary, dereferenced, never a
#: second spelling of it.
REASON_NO_PUNCHES = "no_time_clock_punches"
REASON_KPI_NOT_REPORTED = "kpi_not_reported"
REASON_SLICES_NOT_RECORDED = _pse.BASIS_NOT_RECORDED

#: The field the coaching endpoint stamps `payout_slice_evidence`'s verdict onto, and the predicate
#: that reads it. Both dereferenced: this declaration asks the one home whether a stored breakdown is
#: evidence at all rather than reading the stored column and hoping (§CORE-17.3j).
SLICE_BASIS_FIELD = _pse.BASIS_FIELD
slices_are_evidence = _pse.is_evidence

#: The producer's own grain words for the failing-KPI report's two row lists, dereferenced from
#: `kpi_failing` so "which grain is this row" is its answer and not a second copy.
GRAIN_REP = _kpif.GRAIN_REP
GRAIN_STORE = _kpif.GRAIN_STORE


# ── NOTES — what each panel is, in the words the screen already uses ────────────────────────────

_PROD_ROW_NOTE = (
    "One row per employee at this store, exactly as the table below lists them. The store, market "
    "and employee pickers on this page are sent to the server before anything is folded, so this "
    "panel and the figure it opened from are one fold and cannot describe different people.")

_PROD_SALES_NOTE = _PROD_ROW_NOTE + (
    " An employee found only on the time clock is on this report with nothing sold, and the report "
    "records no transaction count for them — so somebody who stood on the floor and sold nothing "
    "cannot be told apart from somebody the sales feed never named. Both are counted exactly as the "
    "report counted them, which is why the figure above is unchanged, but the distinction is not "
    "available to show.")

_PROD_HOURS_NOTE = _PROD_ROW_NOTE + (
    " Hours are closed time-clock punches and nothing else — never a scheduled shift, which records "
    "an intention rather than an hour somebody worked. An employee with no punch at all is shown "
    "here WITHOUT a figure and is not counted, because output per hour over a person nobody clocked "
    "is not a low rate, it is an unanswerable question. The report cannot tell that from a genuine "
    "zero-hour punch, and says so rather than inventing the difference.")

_COACH_ROW_NOTE = (
    "One row per employee the commission run produced, exactly as the table below lists them. The "
    "market, store, employee and search filters on this page are applied IN THE BROWSER and this "
    "panel has been narrowed to the same selection, so it explains the figure you are looking at "
    "rather than the whole company.")

_COACH_AT_RISK_NOTE = _COACH_ROW_NOTE + (
    " This is the share of the pay subtotal a part-tier month does not pay out, so it was read out "
    "of what the last commission calculation wrote down and the trail stops at that run. An "
    "employee whose breakdown that run never itemised is shown WITHOUT a figure and is not counted: "
    "a stored subtotal of zero on such a row is a blank rather than a month that earned nothing, and "
    "multiplying it by a shortfall would put a confident $0.00 at risk on a manager's screen.")

_COACH_CB_NOTE = _COACH_ROW_NOTE + (
    " These are the deductions from the employee chargeback file. That file is not a relation this "
    "platform creates or keeps, so there is no registered place to name and every row below reports "
    "a source that cannot be traced rather than a guessed one. An employee the file carries no line "
    "for at all is shown without a figure, because nothing was measured for them.")

_COACH_OPS_NOTE = _COACH_ROW_NOTE + (
    " These are the retail-operations penalties posted against commission, out of the recorded "
    "penalty ledger. The figure on each row is the deduction this period actually took, which is "
    "CAPPED at what is left of the pay after the chargeback file has taken its share — so on a "
    "capped row the individual penalties printed on the table below add up to MORE than the figure. "
    "Both numbers are the report's own; the panel follows the one the total is made of.")

_COACH_TABLE_NOTE = _COACH_ROW_NOTE + (
    " This adds three things on each row — the part-tier shortfall, the chargeback file's deduction "
    "and the posted penalties — so each row names whichever of the three it actually has, and the "
    "chargeback share names nothing because that file is not a registered relation. A row whose pay "
    "breakdown was never itemised is counted here EXACTLY as the report counted it, with a zero "
    "standing in for a shortfall nobody computed; that row is marked below, because the figure is "
    "light by an unknown amount rather than wrong by a known one.")

_COACH_TIER_NOTE = _COACH_ROW_NOTE + (
    " This counts the employees the commission run did not pay a full tier to. The verdict is the "
    "run's own, stated on each row, not a threshold this panel holds — and every employee who DID "
    "reach full tier is shown beside them and deliberately not counted, because which people are "
    "not in the figure is most of what makes it readable.")

_ATU_ROW_NOTE = (
    "One row per store, exactly as the table below lists them, and over the whole selection you are "
    "viewing: the period, the date range, the store and market pickers on this page are all sent to "
    "the server before anything is folded, so this panel and the figure above it are one fold.")

_ATU_SOURCE_NOTE = (
    " Enrolment is a zero-dollar line on the transaction with no department of its own, so it can "
    "only be seen by reading every line of a transaction together — which is why these rows come "
    "out of the transaction-grain sales read rather than a filtered line list. Enrolment is observed "
    "at the counter only, so somebody who enrols later through the carrier app never appears here "
    "and the open position above is an upper bound.")

_ATU_COUNT_NOTE = _ATU_ROW_NOTE + (
    " Counted here is one card-tendered activation per transaction. A split tender that includes a "
    "card counts as a card customer, because the instrument exists. This is NOT the customer count "
    "printed above it: that one is per mobile number and this table carries no mobile numbers, which "
    "the report states as a caveat of its own.") + _ATU_SOURCE_NOTE

_ATU_MONEY_NOTE = _ATU_ROW_NOTE + (
    " Counted here is the recurring recharge on card-tendered transactions that carry NO enrolment "
    "line — the money the question at the counter was not asked about. The commission that could be "
    "earned on it is this figure multiplied by a rate somebody typed into the assumptions on this "
    "page, so that one is an estimate rather than a measurement and is deliberately not opened: "
    "this is the measured half of it.") + _ATU_SOURCE_NOTE

_KPI_STORE_NOTE = (
    "One row per store, exactly as the table below lists them, and narrowed to the same selection "
    "the filter bar leaves on screen — this page folds its own tiles in the browser, so the panel "
    "follows them. The actuals come out of the carrier's own per-door activity report and out of "
    "nothing else.")

_KPI_REP_NOTE = (
    "One row per employee the commission run scored, narrowed to the same selection the filter bar "
    "leaves on screen. These actuals are the values the pay engine actually tiered on, read out of "
    "what the last calculation wrote down — so an employee failing a KPI here IS the employee who "
    "lost tier money for it. Three of the metrics an employee is scored on have never been published "
    "at employee grain, so for those the employee reaches the metric only through their store.")

_KPI_ABSENCE_NOTE = (
    " A metric with no recorded value is shown WITHOUT a figure and is never counted as a failure "
    "against anybody. A row that MET the metric is shown with a measured zero and marked as not "
    "counted, so a blank and a pass are two different things on this panel rather than one dash.")

_KPI_STORE_FAIL_NOTE = _KPI_STORE_NOTE + (
    " Counted here is every store with at least one KPI below its target. A store the report could "
    "measure nothing at all for is shown without a figure, because a door with no published numbers "
    "is not a door that is passing.") + _KPI_ABSENCE_NOTE

_KPI_REP_FAIL_NOTE = _KPI_REP_NOTE + (
    " Counted here is every employee with at least one KPI below its target.") + _KPI_ABSENCE_NOTE

_KPI_METRIC_STORE_NOTE = _KPI_STORE_NOTE + (
    " Counted here is every store failing THIS ONE metric, which is a slice of the failing-store "
    "count above rather than an addition to it.") + _KPI_ABSENCE_NOTE

_KPI_METRIC_REP_NOTE = _KPI_REP_NOTE + (
    " Counted here is every employee failing THIS ONE metric.") + _KPI_ABSENCE_NOTE


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  SHARED READERS. Accessors and names only — nothing here adds, judges or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded column. The producer folded every row's share, so there
    is nothing here to add up."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _flag(field):
    """PURE: the producer's own boolean on this row, read rather than re-decided."""
    def _read(r):
        return bool((r or {}).get(field))
    return _read


def _one_if(predicate):
    """PURE: 1 on a row the figure counts and NO figure on any other, so a count panel's tie-out is
    a real comparison instead of a sum over rows that contribute nothing."""
    def _read(r):
        return 1 if predicate(r) else None
    return _read


def _zero_or_one(predicate, measurable):
    """PURE: 1 on a row the figure counts, a MEASURED 0 on a row it does not, and NO figure at all on
    a row nothing was measured for.

    The three-way answer matters on a count of failures: a store that met the metric and a store the
    feed never published a value for both contribute nothing to the figure, and showing both as a
    blank would say the same thing about two different facts (§CORE-17.3i)."""
    def _read(r):
        if not measurable(r):
            return None
        return 1 if predicate(r) else 0
    return _read


def _label_of(*fields):
    """PURE: the row exactly as the table below prints it, from the first field that carries
    anything — the PAGE's own fallback ladder, so the panel and the table name the same row the same
    way."""
    def _read(r):
        for f in fields:
            v = str((r or {}).get(f) or "").strip()
            if v:
                return v
        return None
    return _read


def _nonzero(amount):
    """PURE: is this figure something other than zero, asked through the one home's own rounding so
    this file holds no dollar literal of its own?"""
    return report_drilldown.money(amount) != report_drilldown.money(0)


def _any_counted(rows, value, in_figure=None):
    """PURE: would this panel count ANY row? A figure whose every row is shown without a number ties
    against nothing (0 == 0, True) and would read as a measurement, so it is served NO panel."""
    for r in rows or ():
        if value(r) is None:
            continue
        if in_figure is None or in_figure(r):
            return True
    return False


def _served(catalogue, rows, totals, cap):
    """PURE: every panel in a catalogue whose figure can be read as a measurement.

    The refusal is made HERE rather than in four entry points, because "an empty report drills to
    nothing" is one rule and four copies of it would be four chances to get it wrong.

    AND IT IS A REFUSAL ABOUT A TRIVIAL TIE, NOT ABOUT A DISAGREEMENT (§CORE-17.3o). A figure of
    ZERO over rows none of which were counted ties 0 against 0, which is True by accident and would
    read to the owner as "nothing was there, and here is the proof". That gets no panel. A figure
    that is NOT zero over rows none of which were counted is the opposite case: the report asserts a
    number its own rows cannot account for, and the panel is SERVED so the tie-out says so in words.
    Refusing that one would hide the very disagreement the tie-out exists to publish.
    """
    keep, amounts = [], {}
    for spec in catalogue or ():
        total_key = spec.get("total")
        if total_key not in (totals or {}) or (totals or {}).get(total_key) is None:
            continue
        if not _any_counted(rows, spec.get("value"), spec.get("in_figure")) \
                and not _nonzero((totals or {}).get(total_key)):
            continue
        keep.append(spec)
        amounts[total_key] = (totals or {}).get(total_key)
    if not keep:
        return {}
    return report_drilldown.tiles(tuple(keep), rows, amounts,
                                  cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  PRODUCTIVITY — three grains on one employee-at-a-store row, two units apart and one feed apart.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: WHAT THE READER SEES BESIDE EACH FIGURE — the page's own wording, in the panel's words.
PROD_LABELS = {
    "boxes": "Boxes sold",
    "acc_sales": "Accessory dollars",
    "hours": "Hours worked",
}

#: The per-store figure each company figure is also folded from, so one catalogue serves both grains
#: of subject. `store_*` is the store header's own key; the bare key is the company total's.
PROD_STORE_TOTALS = {"boxes": "store_boxes", "acc_sales": "store_acc", "hours": "store_hours"}


def _prod_label(r):
    """PURE: the employee as the table prints them, falling back to the key the report rolled up on
    when the display name never resolved."""
    return _label_of("rep", "rep_key")(r)


def _prod_fields(r):
    """PURE: what else a reader needs beside one employee's figure on this row."""
    r = r or {}
    return {"store": r.get("store_label") or r.get("store_code") or None,
            "hours": r.get("hours"),
            "time clock": ("no punch recorded" if r.get("no_hours") else "punched")}


def _prod_hours(r):
    """PURE: the hours, and NO figure at all on an employee the time clock never answered for — the
    producer's own `no_hours` flag, read rather than re-derived from the number beside it."""
    r = r or {}
    return None if r.get("no_hours") else r.get("hours")


def _prod_hours_reason(r):
    return REASON_NO_PUNCHES if (r or {}).get("no_hours") else None


def _prod_hours_tables(r):
    """PURE: the time clock on a row it answered for, and NO table on one it did not — the zero shown
    for such a row was read out of nothing."""
    return () if (r or {}).get("no_hours") else (TIME_CLOCK,)


def productivity_catalogue(sales_tables):
    """PURE: the three productivity panels — ONE employee-at-a-store row, three units, two feeds.

    `boxes` and `acc_sales` share the row AND the provenance and are separated only by UNIT
    (§CORE-17.3l). `hours` is separated by both: it comes out of the time clock alone, never the
    schedule, which is why it opens apart from the two figures it is the denominator of.
    """
    common = {"row_label": _prod_label, "keys": ("store_code", "rep_key"),
              "fields": _prod_fields, "schema": SCHEMA}
    sold = dict(common, tables=tuple(sales_tables or ()), note=_PROD_SALES_NOTE)
    return (
        {"key": "boxes", "total": "boxes", "label": PROD_LABELS["boxes"],
         "value": _column("boxes"), "column": "box_count",
         "unit": report_drilldown.UNIT_COUNT, **sold},
        {"key": "acc_sales", "total": "acc_sales", "label": PROD_LABELS["acc_sales"],
         "value": _column("acc_sales"), "column": "accessory_rev",
         "unit": report_drilldown.UNIT_MONEY, **sold},
        {"key": "hours", "total": "hours", "label": PROD_LABELS["hours"],
         "value": _prod_hours, "column": "hours", "unit": report_drilldown.UNIT_HOURS,
         "tables": _prod_hours_tables, "reason": _prod_hours_reason,
         "schema": SCHEMA_STOREOPS, "note": _PROD_HOURS_NOTE,
         "row_label": _prod_label, "keys": ("store_code", "rep_key"), "fields": _prod_fields},
    )


def _prod_rows(payload):
    """PURE: the employee rows this report folded, each carrying the store it is folded under.

    NEW DICTS, never the payload's own: a declaration that stamped a key onto a producer's row would
    have changed the response it is explaining, which §G of the harness deep-compares against.
    """
    out = []
    for s in (payload or {}).get("stores") or ():
        if not s:
            continue
        for r in s.get("reps") or ():
            if not r:
                continue
            out.append(dict(r, store_code=s.get("store_code"), store_label=s.get("store_label")))
    return out


def productivity_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/productivity/{period}`.

    `payload` is the endpoint's own response. The keys are `boxes` / `acc_sales` / `hours` for the
    COMPANY figures and `store.<store_code>.<figure>` for each store header's own three, so the page
    can offer the three beside the numbers it actually prints.

    THE COMPANY TOTALS ARE SERVED THOUGH THE PAGE DOES NOT PRINT THEM. The endpoint folds them and a
    reader needs them; a figure with no tile is still a figure (the batch-19 `cells_differing`
    posture). The page offers no link for them because the numbers are not on screen.
    """
    payload = payload or {}
    stores = [s for s in (payload.get("stores") or ()) if s]
    rows = _prod_rows(payload)
    if not rows:
        return {}
    tables = sales_union_tables(payload.get("source_meta"))
    cat = productivity_catalogue(tables)
    out = dict(_served(cat, rows, dict(payload.get("totals") or {}), cap))
    for s in stores:
        code = str(s.get("store_code") or "").strip()
        srows = [r for r in rows if str(r.get("store_code") or "").strip() == code]
        if not srows:
            continue
        totals = {k: s.get(v) for k, v in PROD_STORE_TOTALS.items()}
        subject = _label_of("store_label", "store_code")(s)
        for key, panel in _served(cat, srows, totals, cap).items():
            panel["subject"] = subject
            panel["subject_kind"] = GRAIN_STORE
            out["store.%s.%s" % (code, key)] = panel
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  REP COACHING — five figures on one employee row, three of them one PROVENANCE apart.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

COACH_LABELS = {
    "total_at_risk": "Incentive at risk",
    "total_chargebacks": "Chargebacks lost",
    "total_ops_chargebacks": "Posted penalties deducted",
    "total_money_on_table": "Total left on the table",
    "below_tier": "Employees below full tier",
}


def _coach_label(r):
    return _label_of("rep")(r)


def slices_recorded(row):
    """PURE: did the commission run that produced this row ITEMISE its pay breakdown?

    ASKED OF THE ONE HOME, never inferred from the stored column (§CORE-17.3j's own ruling, one fact
    along). `payout_slice_evidence` is where "is this stored breakdown a figure at all" lives, the
    coaching endpoint stamps its verdict onto every row it serves, and this reads that verdict. A row
    served by an older build carries no verdict at all, and an unrecognised basis is NOT evidence by
    that module's own allowlist posture — so a missing verdict withholds the figure rather than
    printing a zero somebody could mistake for a measurement.
    """
    return bool(slices_are_evidence((row or {}).get(SLICE_BASIS_FIELD)))


def _coach_at_risk(r):
    """PURE: the shortfall, and NO figure on a row whose breakdown was never itemised AND whose tier
    is short — which is exactly the pair of conditions the producer multiplies together."""
    r = r or {}
    if r.get("below_tier") and not slices_recorded(r):
        return None
    return r.get("at_risk")


def _coach_at_risk_reason(r):
    return None if _coach_at_risk(r) is not None else REASON_SLICES_NOT_RECORDED


def _coach_has_chargeback_line(r):
    """PURE: does the employee chargeback file carry a line for this person at all? The producer's
    own count, which is incremented once per line — so a zero there means the file named them
    nowhere, and a $0.00 of chargebacks for them would be a number nobody measured."""
    return int((r or {}).get("chargeback_count") or 0) > 0


def _coach_chargebacks(r):
    return (r or {}).get("chargeback_deducted") if _coach_has_chargeback_line(r) else None


def _coach_chargeback_reason(r):
    return None if _coach_has_chargeback_line(r) else "not_recorded"


def _coach_has_ops_line(r):
    """PURE: did the penalty ledger post anything against this person's commission this period? The
    producer serves the lines beside the figure, so their presence is the signal."""
    return bool((r or {}).get("ops_chargeback_lines"))


def _coach_ops(r):
    return (r or {}).get("ops_chargeback_deduction") if _coach_has_ops_line(r) else None


def _coach_ops_reason(r):
    return None if _coach_has_ops_line(r) else "not_recorded"


def _coach_ops_tables(r):
    """PURE: the penalty ledger on a row it posted something against, and NO table on one it did
    not — an employee nobody posted a penalty against was not read out of that ledger at all, and
    naming it for them would claim a row that does not exist."""
    return (OPS_CHARGEBACK,) if _coach_has_ops_line(r) else ()


def _coach_table_tables(r):
    """PURE: WHICH RELATIONS THIS ROW'S MONEY-ON-THE-TABLE WAS ACTUALLY BUILT FROM, read per row
    because three employees on one tile can have three different answers.

    The calculated snapshot is always named — the shortfall is read off it, and so is the tier that
    decides there is one. The penalty ledger is named only where it posted something. The employee
    chargeback file is named as NOTHING WE CAN TRACE where it deducted something, because this
    repository creates no such relation (defect 4) and naming one would be a provenance claim about a
    table nobody creates.
    """
    r = r or {}
    out = [REP_COMMISSIONS]
    if _coach_has_ops_line(r) and _nonzero(r.get("ops_chargeback_deduction")):
        out.append(OPS_CHARGEBACK)
    if _nonzero(r.get("chargeback_deducted")):
        out.append(UNNAMEABLE)
    return tuple(out)


def _coach_fields(r):
    """PURE: what else a reader needs beside one employee's figure, including the one fact that makes
    an understated money-on-the-table row readable."""
    r = r or {}
    return {"store": r.get("store") or None,
            "market": r.get("market") or None,
            "KPIs met": ("%s / %s" % (r.get("kpis_met"), r.get("total_kpis"))),
            "full tier": ("no" if r.get("below_tier") else "yes"),
            "pay breakdown": ("recorded" if slices_recorded(r) else "never itemised")}


def coaching_catalogue():
    """PURE: the five coaching panels — ONE employee row, four money figures separated by WHICH
    RELATION answered (§CORE-17.3n) and one count out of the calculated snapshot."""
    common = {"row_label": _coach_label, "keys": ("rep", "store"), "fields": _coach_fields,
              "schema": SCHEMA, "unit": report_drilldown.UNIT_MONEY}
    return (
        {"key": "total_at_risk", "total": "total_at_risk", "label": COACH_LABELS["total_at_risk"],
         "value": _coach_at_risk, "reason": _coach_at_risk_reason, "column": "subtotal",
         "tables": (REP_COMMISSIONS,), "note": _COACH_AT_RISK_NOTE, **common},
        {"key": "total_chargebacks", "total": "total_chargebacks",
         "label": COACH_LABELS["total_chargebacks"], "value": _coach_chargebacks,
         "reason": _coach_chargeback_reason, "tables": (UNNAMEABLE,),
         "note": _COACH_CB_NOTE, **common},
        {"key": "total_ops_chargebacks", "total": "total_ops_chargebacks",
         "label": COACH_LABELS["total_ops_chargebacks"], "value": _coach_ops,
         "reason": _coach_ops_reason, "column": "amount", "tables": _coach_ops_tables,
         "note": _COACH_OPS_NOTE, **common},
        {"key": "total_money_on_table", "total": "total_money_on_table",
         "label": COACH_LABELS["total_money_on_table"], "value": _column("money_on_table"),
         "tables": _coach_table_tables, "note": _COACH_TABLE_NOTE, **common},
        {"key": "below_tier", "total": "below_tier", "label": COACH_LABELS["below_tier"],
         "value": _one_if(_flag("below_tier")), "in_figure": _flag("below_tier"),
         "column": "tier", "tables": (REP_COMMISSIONS,),
         "note": _COACH_TIER_NOTE, **dict(common, unit=report_drilldown.UNIT_COUNT)},
    )


def coaching_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/coaching/{period}`.

    Every figure is the endpoint's own `summary` entry dereferenced, so a panel can never be a second
    opinion about the tile above it. The PAGE folds those same figures again in the browser over the
    rows its filters leave on screen, and narrows these panels to that selection — which is how the
    two folds are pinned together instead of left to agree by luck (§CORE-17.3d).

    A period the commission run produced no rows for drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("reps") or ()) if r]
    summary = dict(payload.get("summary") or {})
    if not rows or not summary:
        return {}
    return _served(coaching_catalogue(), rows,
                   {k: v for k, v in summary.items() if k in COACH_LABELS}, cap)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  AUTOPAY OPPORTUNITY — two grains on one store row, one unit apart.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

ATU_LABELS = {
    "card_activations": "Card-tendered activations",
    "card_on_atu": "Card activations already enrolled",
    "recharge_open": "Recharge not on autopay",
}

#: WHICH SUMMARY FIGURE EACH PANEL OPENS, as a path into the producer's own nested summary. Declared
#: as a table rather than three lookups so a fourth cannot be added without saying which figure of
#: the report it is, and which per-store column it is Σ of.
ATU_FIGURES = (
    ("card_activations", ("activations", "card"), "card_acts", report_drilldown.UNIT_COUNT),
    ("card_on_atu", ("activations", "card_on_atu"), "card_atu", report_drilldown.UNIT_COUNT),
    ("recharge_open", ("recharge", "card_open"), "rtr_open", report_drilldown.UNIT_MONEY),
)


def _atu_label(r):
    return _label_of("store")(r)


def _atu_fields(r):
    r = r or {}
    return {"market": r.get("market") or None,
            "card activations": r.get("card_acts"),
            "already enrolled": r.get("card_atu")}


def atu_catalogue(sales_tables):
    """PURE: the three autopay panels — ONE store row, a COUNT grain and a MONEY grain over the same
    transaction-grain provenance (§CORE-17.3l)."""
    common = {"row_label": _atu_label, "keys": ("store",), "fields": _atu_fields,
              "schema": SCHEMA, "tables": tuple(sales_tables or ())}
    return tuple(
        dict(common, key=key, total=key, label=ATU_LABELS[key], value=_column(column),
             column=column, unit=unit,
             note=(_ATU_MONEY_NOTE if unit == report_drilldown.UNIT_MONEY else _ATU_COUNT_NOTE))
        for key, _path, column, unit in ATU_FIGURES)


def atu_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/atu-opportunity`.

    The three figures are read out of the producer's own nested summary through `ATU_FIGURES`, so the
    panel and the tile are the same number by dereference rather than by arithmetic.

    A selection with no store rows drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("stores") or ()) if r]
    summary = payload.get("summary") or {}
    if not rows or not summary:
        return {}
    totals = {}
    for key, path, _column, _unit in ATU_FIGURES:
        node = summary
        for step in path:
            node = (node or {}).get(step) if isinstance(node, dict) else None
        if node is not None:
            totals[key] = node
    tables = sales_union_tables(payload.get("source_meta"))
    return _served(atu_catalogue(tables), rows, totals, cap)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  FAILING KPIs — two row shapes, one unit, two relations.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

KPI_LABELS = {
    "stores_failing": "Stores failing at least one KPI",
    "reps_failing": "Employees failing at least one KPI",
}

#: The panel key prefix for one metric's own slice of each count, and the separator the page rebuilds
#: it with. Declared once so the screen and this file cannot spell the key two ways.
KPI_METRIC_PREFIX = "metric"
KPI_KEY_SEP = "."


def metric_key(kpi, grain):
    """PURE: the panel key for ONE metric at ONE grain. The page asks for exactly this string, so it
    is built here rather than spelled on both sides."""
    return KPI_KEY_SEP.join((KPI_METRIC_PREFIX, str(kpi or ""), str(grain or "")))


def _kpi_store_label(r):
    return _label_of("location", "store_code", "address")(r)


def _kpi_rep_label(r):
    return _label_of("rep")(r)


def _kpi_measured(r):
    """PURE: did the report measure ANYTHING on this row? `evaluated_count` is the producer's own
    honest denominator — the metrics that had both a target and a value — so a zero there means
    nothing on this row can be said to be failing or passing."""
    return int((r or {}).get("evaluated_count") or 0) > 0


def _kpi_failing_any(r):
    return int((r or {}).get("failing_count") or 0) > 0


def _kpi_has_metric(field, kpi):
    """PURE: does this row's own named list carry this metric? The producer classified each metric
    once; this reads its answer rather than comparing an actual against a target a second time, which
    would be the sibling derivation the index rules forbid."""
    def _read(r):
        for e in ((r or {}).get(field) or ()):
            if str((e or {}).get("kpi") or "") == str(kpi):
                return True
        return False
    return _read


def _kpi_metric_measured(kpi):
    """PURE: was THIS metric measured on this row? Everything the producer did not put in `no_data`
    and did classify is measured — so a metric in neither list was not evaluated at this grain at
    all, which is the same absence."""
    in_no_data = _kpi_has_metric("no_data", kpi)
    in_failing = _kpi_has_metric("failing", kpi)
    in_met = _kpi_has_metric("met", kpi)

    def _read(r):
        if in_no_data(r):
            return False
        return bool(in_failing(r) or in_met(r) or _kpi_measured(r))
    return _read


def _kpi_store_fields(r):
    r = r or {}
    return {"market": r.get("market") or None,
            "failing": "%s of %s measured" % (r.get("failing_count"), r.get("evaluated_count")),
            "no data": len(r.get("no_data") or ())}


def _kpi_rep_fields(r):
    r = r or {}
    return {"store": r.get("store") or None,
            "tier": r.get("tier"),
            "KPIs met": ("%s / %s" % (r.get("kpis_met"), r.get("total_kpis"))),
            "failing": "%s of %s measured" % (r.get("failing_count"), r.get("evaluated_count")),
            "no data": len(r.get("no_data") or ())}


def _kpi_common(grain):
    if grain == GRAIN_STORE:
        return {"row_label": _kpi_store_label, "keys": ("store_code", "location"),
                "fields": _kpi_store_fields, "tables": (DLAR_STORE,),
                "subject_kind": GRAIN_STORE}
    return {"row_label": _kpi_rep_label, "keys": ("rep", "store"),
            "fields": _kpi_rep_fields, "tables": (REP_COMMISSIONS,),
            "subject_kind": GRAIN_REP}


def kpi_catalogue(grain, metrics=()):
    """PURE: the failing-KPI panels at ONE grain — the row count, plus one slice per metric.

    The two grains share the UNIT and differ only in which relation answered: a store's actual comes
    out of the carrier's own per-door report and an employee's out of what the last commission
    calculation wrote down. They are never pooled into one panel, because "which doors are failing"
    and "which people are failing" are two questions a manager asks of two different people.
    """
    common = dict(_kpi_common(grain), schema=SCHEMA, unit=report_drilldown.UNIT_COUNT)
    is_store = grain == GRAIN_STORE
    overall_key = "stores_failing" if is_store else "reps_failing"
    cat = [
        dict(common, key=overall_key, total=overall_key, label=KPI_LABELS[overall_key],
             value=_zero_or_one(_kpi_failing_any, _kpi_measured),
             in_figure=_kpi_failing_any, reason=_kpi_reason_overall,
             note=(_KPI_STORE_FAIL_NOTE if is_store else _KPI_REP_FAIL_NOTE)),
    ]
    for kpi, label in metrics or ():
        failing = _kpi_has_metric("failing", kpi)
        cat.append(dict(
            common, key=metric_key(kpi, grain), total=metric_key(kpi, grain),
            label="%s — %s" % (label, KPI_LABELS[overall_key].lower()),
            value=_zero_or_one(failing, _kpi_metric_measured(kpi)),
            in_figure=failing, reason=_kpi_reason_metric(kpi), column=str(kpi),
            note=(_KPI_METRIC_STORE_NOTE if is_store else _KPI_METRIC_REP_NOTE)))
    return tuple(cat)


def _kpi_reason_overall(r):
    return None if _kpi_measured(r) else REASON_KPI_NOT_REPORTED


def _kpi_reason_metric(kpi):
    measured = _kpi_metric_measured(kpi)

    def _read(r):
        return None if measured(r) else REASON_KPI_NOT_REPORTED
    return _read


def kpi_failing_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/kpi-failing/{period}`.

    Keyed `stores_failing` / `reps_failing` for the two row counts and
    `metric.<kpi>.<grain>` for each metric's own slice of them, so the page can offer a link beside
    every number it prints — including the per-metric lines, which it re-folds in the browser
    (defect 9) and which these panels are narrowed to follow.

    THE MIXED-GRAIN TILE GETS NOTHING. `failing_cells` adds store failing counts to employee failing
    counts, so there is no one row list it was folded from, and neither side is on the payload as a
    figure of its own. REPORTED, with both halves of it offered as the two panels above.
    """
    payload = payload or {}
    stores = [s for s in (payload.get("stores") or ()) if s]
    reps = [r for r in (payload.get("reps") or ()) if r]
    summary = dict(payload.get("summary") or {})
    if not summary or not (stores or reps):
        return {}
    metrics = [(m.get("kpi"), m.get("label") or m.get("kpi"))
               for m in (summary.get("by_metric") or ()) if m and m.get("kpi")]
    totals = {}
    for key in KPI_LABELS:
        if summary.get(key) is not None:
            totals[key] = summary.get(key)
    for m in (summary.get("by_metric") or ()):
        if not m or not m.get("kpi"):
            continue
        if m.get("stores_failing") is not None:
            totals[metric_key(m["kpi"], GRAIN_STORE)] = m["stores_failing"]
        if m.get("reps_failing") is not None:
            totals[metric_key(m["kpi"], GRAIN_REP)] = m["reps_failing"]
    out = {}
    out.update(_served(kpi_catalogue(GRAIN_STORE, metrics), stores, totals, cap))
    out.update(_served(kpi_catalogue(GRAIN_REP, metrics), reps, totals, cap))
    return out
