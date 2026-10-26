"""WHERE DID THIS FIGURE COME FROM — the declaration for the four reports that answer *what did a
month of this estate's sales lines actually produce, store by store* (§CORE-17, batch 21).

  /commcalc/peer-comparison  Peer Sales Comparison — stores of comparable foot traffic, ranked on
                             what they did with it.
  /commcalc/product-mix      Product Mix & Ports — what was sold, at what price, and who is not
                             attaching or porting.
  /commcalc/spiff-impact     Spiff Impact — what one carrier pay type paid on those sales, and what
                             it is worth to the store's profit.
  /commcalc/tax-collected    Tax Collected — the retail sales tax those same lines took, and what
                             is owed to the state.

ONE FAMILY, AND THE SEAM IS STATED RATHER THAN GLOSSED. All four fold ONE month of the same retail
sales lines, and a reviewer reads them in that order: the peer screen says which door failed to turn
its footfall into boxes, the mix report says what inside those boxes explains it, the spiff report
says what the carrier paid for them and what that is worth to the books, and the tax report says what
the same lines collected for the state. The FOURTH is the one worth challenging and it stays, with
its difference written down: its reader is a controller rather than a coach, and its figure is a
LIABILITY rather than a performance result. It is in the family because it is the fourth thing one
month of those lines produced, it is the only one whose provenance comes through the TRANSACTION-grain
RANGE read (which could not name its own source until this batch — see the design fix below), and it
keeps its own grains — a store row and a store-DAY row — rather than being forced into the others'.

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED OFF THE PRODUCERS, NOT ASSUMED (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────────────────────
Driving `peer_comparison.build` / `lagging`, `product_mix.build` (`rep_rows` / `rank` /
`store_rollup`), `spiff_impact.build` (`store_money` / `profit_effect`) and
`tax_collected.aggregate` gives the triples below, because a grain fixes the ROW, the UNIT **and**
the PROVENANCE. `harness_batch21_drilldown.py` §H measures them off the panels as a SET and compares
for EQUALITY, so the number in the index is read off the harness rather than asserted here:

   1. a PEER STORE ROW, counted                count, out of whichever sales relations the period's
                                              display union actually read.
   2. a REP-AT-A-STORE ROW's BOXES            count, same provenance, a different ROW SHAPE — the
                                              mix report folds per (store, rep), not per store.
   3. a REP-AT-A-STORE ROW's ACCESSORY $      the SAME row and provenance in a different UNIT,
                                              therefore its own grain (§CORE-17.3l).
   4. a SPIFF STORE ROW's CARRIER MONEY       money, out of the carrier's compensation statement.
   5. a SPIFF STORE ROW's PAID UNITS          the SAME row and provenance, a different UNIT.
   6. a SPIFF STORE ROW's PROFIT              money again on the same row, separated ONLY by
                                              PROVENANCE (§CORE-17.3n): the stored financial
                                              statements, which is a SNAPSHOT and the trail stops
                                              there (§CORE-17.3j).
   7. a SPIFF STORE ROW's BOXES               count on the same row, out of the sales union — a
                                              third provenance on one row.
   8. a SPIFF STORE ROW's LOSS VERDICT        count over the same stored statements as 6.
   9. a TAX STORE ROW's MONEY                 money, out of the TRANSACTION-grain sales union for
                                              every month the window touched.
  10. a TAX STORE ROW's RETURN LINES          count, same row, same provenance, a different unit.
  11. a TAX STORE-DAY ROW's MONEY             money, same provenance, a different ROW — one day of
                                              one store, which is the grain the page expands to.

A SUBTRACTION, A MEDIAN AND A SET — THE THREE SHAPES THIS FAMILY REFUSES
───────────────────────────────────────────────────────────────────────
  · **A FIGURE COMPOSED AT THE TOTAL FROM TWO OTHER TOTALS GETS NO PANEL** (§CORE-17.3k). Two here:
    the tax report's company NON-TAXABLE SALES (`revenue − taxable_revenue`, taken at the total) and
    the spiff report's estate PROFIT WITHOUT THE SPIFF (`net_income − spiff_amount`, likewise). Both
    sides of each ARE served. The second one is also a live defect in its own right — see defect 5.
  · **A MEDIAN, A BEST, A MIN AND A MAX ARE NOT FOLDS.** The peer report's band best / median and
    bill-payment low / high, and the mix report's three medians, are each ONE row's value selected
    out of the band, not a sum over the rows — so there is no row list the figure was folded from,
    and a tie-out against one could only ever fail. Which stores are IN the band is served instead,
    and it is the question a reader actually opens.
  · **A DISTINCT-TRANSACTION COUNT IS NOT Σ OVER THE CELLS IT CAME FROM.** The peer report's
    bill payments, add-a-line and six box PARTS are sets UNIONED across the shared sales cells
    precisely because one receipt must not count twice (`peer_comparison.roll_up`'s own documented
    trap), so a panel that summed a per-cell count would print a bigger number than the report.
    Those columns get no panel and the refusal names the producer's reason.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED PER PAGE BY READING THE PAGE
──────────────────────────────────────────────────────────────────────────────────────────────
This family splits THREE AND ONE, and the split was found by reading each page:

  · `/commcalc/peer-comparison`, `/commcalc/product-mix` and `/commcalc/spiff-impact` are
    **SERVER-FOLDED and NOT narrowed**. Every filter that moves a figure on them — the period, the
    markets, the band or price cut-offs, the selected pay type — is a QUERY PARAMETER, so the server
    folds again on every change and a panel follows its figure by construction. The mix page's one
    client control (`Only reps low on both`) filters the TABLE and moves no tile.
  · `/commcalc/tax-collected` **folds its own tiles in the browser** over the stores its store and
    market pickers leave on screen, and is therefore **NARROWED** through `lib/drill-narrow`, with
    each tile reading its number off its narrowed panel through the shared `agreesToTheCent` gate.
    It narrows on the row key's STORE, which every row of every panel on that page carries — the
    company rows, the tender rows and each store's own DAY rows — so one predicate narrows all
    three row shapes.

`harness_batch21_drilldown.py` §F locks all four decisions in BOTH directions off each page's own
text, with a non-vacuity witness for every scan.

WHAT ELSE GETS NO PANEL HERE, AND WHICH RULING SAYS SO
──────────────────────────────────────────────────────
  · **EVERY RATE, SHARE, INDEX AND CORRELATION** (§CORE-17.3h). Boxes per bill payment, accessory $
    per box, family plan %, AAL conversion %, port share, cheap and free share, average device
    price, the effective tax rate, the share of the P&L line, the share of carrier money, the profit
    lift, the margin points and paid units per 100 boxes. In every case the figures the rate divides
    ARE served — which is the whole content of the refusal — and the two correlation coefficients
    are refused for the same reason one layer up: a coefficient over rates is not a sum of anything.
  · **THE ESTATE MODEL TABLE on the mix report.** Each row is a MODEL folded from the per-rep model
    lists, and `product_mix.build` deliberately drops `all_models` off every rep row before
    returning so the payload stays small — so that fold's rows are not on the response at all. The
    refusal is stated rather than worked around, because serving them would change the payload the
    report has chosen to keep small (defect 6).
  · **A ROW COUNT THE PANEL BESIDE IT ALREADY STATES.** The mix report's `listed_reps`, the spiff
    report's `stores` and `stores_with_pl`, the tax report's `rows_in_window`, and the peer report's
    "not compared" count: each is the number of rows a panel's own header states, so a panel for one
    would restate its row count as a finding. The batch-20 precedent, unchanged.
  · **THE RENAMED-STORE DOLLARS on the tax report.** `store_renames` is keyed on the RAW export
    label a canonical key absorbed — a different grain from every row on that payload — and it is a
    data-quality list rather than a reported figure.
  · **WHAT A SHARED TABLE COMPONENT FOLDS IN ITS OWN FOOTER.** Three of these four pages print a
    table through `components/ReportShell`, which sums its numeric columns over the rows ITS OWN
    search box leaves on screen. Those footers are not this family's figures and wiring a drill into
    them would change a shared component 38 reports render; it is recorded here as a disposition
    rather than left silent, and the per-store and per-rep figures those footers sum ARE served
    above them.
  · **A FIGURE NO ROW CARRIES A NUMBER FOR**, and **a $0.00 or a 0 over zero counted rows**: served
    NO panel, through the one home's own `served_tiles` rule, because such a panel ties 0 against 0
    and reads as a measurement. A NON-zero figure over no counted rows KEEPS its panel so the
    tie-out publishes the disagreement instead of the report hiding it.

ABSENCE IS NEVER ZERO, FOUR TIMES
─────────────────────────────────
Four absences in this family are already distinguished by the producers and were reaching no surface:

  · A store **ALONE IN ITS TRAFFIC BAND** carries no gap at all — `peer_comparison._gaps` returns
    `{}` below `BAND_MIN_PEERS` — and that is not a store that is keeping up. It is shown WITHOUT a
    figure, is not counted, and says why in a new vocabulary entry (`no_peer_in_band`) whose own
    words are that being alone in a band is not a result.
  · A rep **BELOW THE RANKING FLOOR** has `verdict: None` and the producer's own reason: too few
    boxes for a per-box rate to mean anything. Shown without a figure and not counted
    (`below_ranking_floor`), never as a rep who passed.
  · A store with **NO STORED P&L SNAPSHOT** is `None` on every profit figure by the producer's own
    rule, and the blank is a missing statement run, not a store that earned nothing (`not_computed`,
    §CORE-17.3j — the trail stops at the snapshot).
  · A store the carrier paid for that **THE SALES READ NAMED NO ROWS FOR** carries no box count, so
    it is shown without one rather than as a store that sold nothing (`not_recorded`). The producer
    already refuses to rank it for exactly this reason.

**IN EVERY CASE NO FIGURE MOVED BY A CENT**: each of those rows contributed nothing to the
producer's own total and contributes nothing here. Only the ROW changes, from a measured zero into a
stated blank, which is the entire argument for putting it there.

A DESIGN FIX, WITH ITS OTHER HALF, AND ITS SCOPE WRITTEN DOWN
─────────────────────────────────────────────────────────────
TWO facts moved to one home in this commit, and both halves of each are in it:

  · **THE STORED FINANCIAL STATEMENTS.** The spiff report's profit figures are read out of the
    statement SNAPSHOT, whose name was a LITERAL in `account/statement_drilldown.SNAPSHOT` with no
    constant anywhere. It is `data_lineage_registry.FINANCIAL_STATEMENTS` now, and **that copy was
    re-keyed in the same commit**, asserted by object identity rather than by equal text (§19.18:
    writing the registry without wiring the callers is not a fix at all — it had happened three
    times before batch 20). The constant's SCOPE is stated in its own comment, exactly as
    `TIME_CLOCK` and `STORE_EXPENSES` carry: it is the one home for the PROVENANCE NAME and
    deliberately NOT a rename of the statement engine's and filter's `.table(...)` reads, which are
    I/O rather than a second statement about where a figure came from — and finance work is the
    finance agent's (CLAUDE.md).
  · **"CAN THIS FIGURE HONESTLY BE EXPLAINED AT ALL" IS ONE RULE, NOT ONE PER DECLARATION.** The
    refusal that withholds a panel for a figure the report never produced, and for a zero over no
    counted rows, was written inside `performance_coaching_drilldown` by batch 20. This batch needed
    exactly the same refusal, and a second implementation of a refusal rule is two chances to get a
    refusal wrong. It moved into `core/report_drilldown.served_tiles`, batch 20's copy now
    DEREFERENCES it by identity, and this module holds no copy either.

ONE HOME EXTENDED RATHER THAN FORKED, AND THE DEFECT BATCH 20 HANDED THIS BATCH
──────────────────────────────────────────────────────────────────────────────
`processor_money_drilldown.sales_union_tables` is the one home for "which tables did this period's
sales actually come from", read off the read's OWN meta. Batch 20 reported (its defect 11) that
`router._sales_rows_union_txn_range` builds its meta by SUMMING only the numeric keys and drops
`primary` and `other` — the first two things that resolver reads — so a RANGE read named **no source
at all** however many rows it returned. It was latent because its one caller, `/commcalc/tax-collected`,
bound the meta and discarded it. This is the batch that wires that report, so it is fixed, in the ONE
home, and **not** with a sibling resolver.

It is fixed ONE STEP FURTHER than batch 20's note proposed, and the reason is written down here
because it is a finding of this batch: carrying a single `primary` / `other` pair through the range
would have been **wrong for a multi-month window**, because two months of one range can be led by
DIFFERENT relations — an open month is feed-led and a closed one raw-led — so one pair would have
named one month's leader for the whole range and under-reported the other's. The range now carries
the per-month metas it already had in hand, and the ONE resolver answers over them and unions its own
answers. A day or single-month meta carries no such key, so every existing caller is byte-identical.

A PANEL CANNOT DISCLOSE WHAT THE REPORT DOES NOT, BY CONSTRUCTION
─────────────────────────────────────────────────────────────────
Every entry point below takes **only the endpoint's own response** — no client, no org id, no second
read — so a panel can only fold rows the caller was already served, after every filter the endpoint
applied. That matters here because `/commcalc/tax-collected` applies NO per-store RBAC scope at all
(defect 1), which is REPORTED rather than papered over: the panel discloses exactly what the report
already does, and the remedy is the scope, not the panel.

SEVEN LIVE DEFECTS, ALL REPORTED, NONE FIXED, NO FIGURE MOVED
─────────────────────────────────────────────────────────────
Each carries a regression check in `harness_batch21_drilldown.py`.

 1. **`GET /commcalc/tax-collected` applies no RBAC store scope at all.** It takes no authorization
    header and runs no `scope_keyset` / `in_keyset`, unlike the other three in this family, so a
    store-scoped manager is served every store's tax, revenue and tender split. REPORTED, not fixed:
    adding a scope filter changes who sees which figure, which is a permissions decision and not a
    drill-down one. Same shape as batch 20's defect 1, one family along.
 2. **A TAX LINE WITH NO TRANSACTION DATE IS IN THE STORE TOTAL AND IN NO DAY.**
    `tax_collected.aggregate` accumulates a dated row into both the store and its day, and an UNDATED
    row into the store only — so a store's `days` array can add up to LESS than the store row above
    it, with nothing on the payload saying so. The per-store day panel's tie-out therefore comes back
    in words saying the report and its own evidence disagree, which is the §CORE-17.3o posture: the
    panel is SERVED so the disagreement is published rather than hidden. No figure moved.
 3. **The spiff endpoint was discarding the provenance its own sales read returned.**
    `srows, smeta = _sales_rows_union(...)` and `smeta` was never read, so the one fact saying which
    relations the boxes came out of was thrown away every request. Carried through read-only now; no
    figure moved. Batch 20 reported the identical shape on `/atu-opportunity` (its defect 7) and
    batch 19 twice before that — a read that returns `(rows, meta)` has a meta because somebody
    needed it, and binding it to `_meta` is how it gets lost. Fourth report in three batches.
 4. **A RANGE READ OF THE SALES UNION COULD NOT NAME ITS OWN SOURCE** — batch 20's defect 11, handed
    to this batch and fixed here in the one home (see above). It was latent only because the one
    caller discarded the meta; this batch is the one that reads it.
 5. **The spiff report's ESTATE profit base subtracts money the base does not contain.**
    `spiff_impact.build` computes `net_income_ex_spiff` as the sum of every store's net income MINUS
    the whole estate's spiff dollars — but the net-income sum covers only the stores with a stored
    P&L snapshot, while the spiff sum covers all of them. So a store with carrier money and no
    snapshot takes its spiff out of other stores' profit, and the estate profit lift that divides by
    that base is understated. The per-STORE figure is correct (both sides are that store's own), and
    the estate figure gets NO panel under §CORE-17.3k anyway — it is composed at the total. REPORTED,
    not fixed: changing it would move a figure on a report nobody asked this batch to change.
 6. **The mix report's estate model table has no row list on its own payload.** `build` pops
    `all_models` off every rep row deliberately, so the model fold's rows are gone by the time the
    response exists. REPORTED; the refusal above says so rather than quietly serving nothing.
 7. **The peer report's own bill-payment, add-a-line and box-part columns cannot be summed.** They
    are distinct-transaction SETS unioned across cells, and the payload carries only their counts —
    so there is no additive row list for them at store grain. That is the producer being correct, not
    a bug, and it is recorded as a defect only in the sense that those columns can never carry a
    panel from this payload. The refusal names the producer's own reason.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3u.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import peer_comparison as _peer
from app.modules.commcalc import processor_money_drilldown as _pmd
from app.modules.commcalc import product_mix as _pmix
from app.modules.commcalc import spiff_impact as _spiff
from app.modules.core import report_drilldown as report_drilldown

# ── THE PROVENANCE ANSWERS THIS FAMILY HAS, EVERY ONE OF THEM DEREFERENCED ───────────────────────

#: WHICH TABLES THE PLATFORM'S SALES READ ACTUALLY ANSWERED OUT OF — not a constant, and that is the
#: point. Resolved off the read's OWN meta through the ONE home for that question,
#: `processor_money_drilldown.sales_union_tables`, bound BY IDENTITY exactly as batches 14, 17, 18,
#: 19 and 20 bound it. Extended in this same commit to answer for a multi-month RANGE read by
#: unioning its own answers over the per-month metas the range now carries — one home for all three
#: unions rather than a second resolver beside it (see the module docstring).
sales_union_tables = _pmd.sales_union_tables

#: THE CARRIER'S OWN COMPENSATION STATEMENT — where every dollar and every paid unit on the spiff
#: report was read out of.
COMP_REPORT = _lineage.COMP_REPORT

#: THE STORED FINANCIAL STATEMENTS. A registry NAME constant as of this batch, with its one
#: pre-existing literal copy re-keyed in the same commit — the half of a design fix that usually gets
#: left out (§19.18). A figure read out of it names the SNAPSHOT and the trail stops there
#: (§CORE-17.3j): what the statement run read is that run's business, not this panel's claim.
FINANCIAL_STATEMENTS = _lineage.FINANCIAL_STATEMENTS

SCHEMA = "commcalc"

#: WHY A ROW IN THIS FAMILY CARRIES NO NUMBER. Four shapes, every one of them a word that already has
#: a home: two added to `report_drilldown.UNMEASURED_REASONS` by this batch, and two that the
#: vocabulary already carried in exactly the sense meant here.
REASON_NO_PEER = "no_peer_in_band"
REASON_BELOW_FLOOR = "below_ranking_floor"
REASON_NO_SNAPSHOT = "not_computed"
REASON_NO_SALES_ROWS = "not_recorded"

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the ONE home's own rule (see the design fix above), bound
#: by identity. This module holds no copy of it, and neither does batch 20's declaration any more.
_served = report_drilldown.served_tiles

#: THE PRODUCERS' OWN WORDS AND THRESHOLDS, DEREFERENCED, NEVER RE-SPELLED. "How many peers make a
#: band comparable" and "which metric is this report ranked on by default" are `peer_comparison`'s
#: facts; the mix report's two floors are `product_mix`'s; the spiff metric key is `spiff_impact`'s.
#: This file compares nothing against any of them — they are read so a NOTE can say what the producer
#: refused and why, in the producer's own numbers.
BAND_MIN_PEERS = _peer.BAND_MIN_PEERS
DEFAULT_GAP_METRIC = _peer.DEFAULT_GAP_METRIC
MIN_BOXES_TO_RANK = _pmix.MIN_BOXES_TO_RANK
SPIFF_METRIC = _spiff.SPIFF_METRIC


# ── NOTES — what each panel is, in the words the screen already uses ────────────────────────────

_PEER_ROW_NOTE = (
    "One row per store the sales read named in this window, exactly as the table below lists them, "
    "and over the whole selection you are viewing: the month, the market picker and the band "
    "cut-offs on this page are all sent to the server before anything is folded, so this panel and "
    "the figure it opened from are one fold.")

_PEER_BAND_NOTE = _PEER_ROW_NOTE + (
    " Counted here are the stores in THIS traffic band; every other store is shown beside them and "
    "deliberately not counted, because which doors are being compared with which is most of what "
    "makes the comparison readable. A band is traffic measured in distinct bill-payment "
    "transactions, which is the report's stand-in for people through the door.")

_PEER_BEHIND_NOTE = _PEER_ROW_NOTE + (
    " Counted here is every store BEHIND the median of its own traffic band on the measure this "
    "page is ranked by. A store that is at or above its band's median is shown with a measured zero "
    "and not counted, and a store ALONE in its band is shown without a figure at all: there is "
    "nobody for it to be behind, which is not the same as keeping up. The bill payments, the "
    "add-a-line count and the six box parts beside each store are distinct-transaction counts rather "
    "than sums, so they carry no panel of their own — adding them up across the cells they came from "
    "would count one receipt several times, which is the exact trap the producer unions those sets to "
    "avoid.")

_MIX_REP_NOTE = (
    "One row per salesperson at one store, exactly as the by-rep table below lists them, and over "
    "the whole selection you are viewing: the month, the market picker and the price cut-offs are "
    "all sent to the server before anything is folded. The `Only reps low on both` tick filters the "
    "table below and moves no figure above it.")

_MIX_SOLD_NOTE = _MIX_REP_NOTE + (
    " What was sold comes out of whichever sales relations the period's union actually read. Boxes "
    "count device-department lines plus the activation buckets this tenant has configured, and the "
    "report's own caveats above the table say what that currently includes — a column that reads low "
    "because of a setting is a setting, not a sales result.")

_MIX_VERDICT_NOTE = _MIX_REP_NOTE + (
    " Counted here is every salesperson the report flagged as low on BOTH signals — accessory "
    "dollars per box and port share — which is the only combination it flags, because measured live "
    "the two are independent and a blended score would hide the rep who ports well and attaches "
    "nothing. A rep at or above the medians is shown with a measured zero and not counted. A rep "
    "below the ranking floor is shown WITHOUT a figure: the report reaches no verdict about them at "
    "all, and counting them as not-flagged would read as a pass they were never given.")

_MIX_RANKED_NOTE = _MIX_REP_NOTE + (
    " Counted here is every salesperson who sold enough boxes in the window to be ranked at all. "
    "The ones below that floor are shown beside them and not counted, which is the denominator every "
    "median and every verdict on this report actually rests on.")

_SPIFF_ROW_NOTE = (
    "One row per store, exactly as the table below lists them, and over the whole selection you are "
    "viewing: the month, the pay type, the market picker and the band cut-offs are all sent to the "
    "server before anything is folded, so this panel and the figure above it are one fold.")

_SPIFF_MONEY_NOTE = _SPIFF_ROW_NOTE + (
    " These dollars and units come out of the carrier's own compensation statement for the month, "
    "for the ONE pay type this report is set to. A store the statement named and paid nothing of "
    "this type to is a real $0.00 and is counted — that is the finding this report exists for — "
    "while a store the statement never named at all is not on this report's money side and says so "
    "in its own row.")

_SPIFF_PROFIT_NOTE = _SPIFF_ROW_NOTE + (
    " Profit and revenue are read out of the STORED financial statements this period's run wrote "
    "down, so the trail stops at that snapshot: what the run read to produce it is that run's "
    "business, not this panel's claim. A store with no stored statement is shown WITHOUT a figure "
    "and is not counted, because a missing statement run is not a store that earned nothing.")

_SPIFF_LOSS_NOTE = _SPIFF_ROW_NOTE + (
    " Counted here is every store whose profit WITHOUT this pay type is not positive — the stores "
    "for which no percentage lift can honestly be quoted, which is why the report shows the dollars "
    "there instead. A store with no stored statement carries no verdict at all and is shown without "
    "a figure.")

_SPIFF_BOXES_NOTE = _SPIFF_ROW_NOTE + (
    " Boxes come out of whichever sales relations the period's union actually read, and they are the "
    "denominator of the paid-units rate this report ranks on. A store the carrier paid for that the "
    "sales read named no rows for is shown WITHOUT a box count: the report already refuses to rank "
    "it, because ranking a store at zero would accuse a mapping defect of being a sales result.")

_TAX_STORE_NOTE = (
    "One row per store, exactly as the table below lists them, and narrowed to the same stores and "
    "markets your pickers leave on screen — this page folds its own tiles in the browser, so the "
    "panel follows them. Every figure comes out of the transaction-grain sales read for every month "
    "the window touched; voided lines are in none of them.")

_TAX_GROSS_NOTE = _TAX_STORE_NOTE + (
    " Return lines are deliberately NOT in this figure, which is the report's headline unchanged. "
    "They are carried beside it, because refunded tax is money the tenant does not owe the state.")

_TAX_NET_NOTE = _TAX_STORE_NOTE + (
    " This one IS net of the return lines' own tax, because that is what the liability has to be. "
    "Non-taxable sales are the only company figure on this page with no panel: that one is taken as "
    "a subtraction between two totals rather than folded from these rows, and both totals it is the "
    "difference of are served here.")

_TAX_TENDER_NOTE = _TAX_STORE_NOTE + (
    " Counted here is only the share of each store that was paid this way. A line whose tender names "
    "SEVERAL tenders is reported as mixed rather than assigned whole to one of them, because the row "
    "carries one amount and no split — choosing one would silently overstate it.")

_TAX_DAY_NOTE = (
    "One row per DAY of this store, exactly as the rows this page expands underneath it. A sales "
    "line that carries no transaction date is in the store's figure above and in NO day below it, so "
    "these rows can add up to less than the figure they explain — where they do, the tie-out says so "
    "in words rather than the panel quietly presenting a short month as the whole of one.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  SHARED READERS. Accessors and names only — nothing here adds, judges or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded column. The producer folded every row's share, so there
    is nothing here to add up."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _nested(*path):
    """PURE: a reader for one already-folded column inside a nested dict on the row — the tax
    report's per-tender split, which is one bucket of one store and nothing else."""
    def _read(r):
        node = r or {}
        for step in path:
            node = (node or {}).get(step) if isinstance(node, dict) else None
        return node
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


def _zero_or_one(predicate, measurable):
    """PURE: 1 on a row the figure counts, a MEASURED 0 on a row it does not, and NO figure at all on
    a row nothing was measured for.

    The three-way answer is the whole point of a count panel in this family: a store at its band's
    median and a store with no peer to compare against both contribute nothing to "stores behind
    their band", and showing both as a blank would say the same thing about two different facts
    (§CORE-17.3i)."""
    def _read(r):
        if not measurable(r):
            return None
        return 1 if predicate(r) else 0
    return _read


def _reason_when(measurable, reason):
    """PURE: the producer's own absence reason on a row nothing was measured for, and nothing on a
    row that was."""
    def _read(r):
        return None if measurable(r) else reason
    return _read


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  PEER SALES COMPARISON — one store row, counted two ways.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

PEER_BEHIND_KEY = "behind_median"
PEER_BAND_PREFIX = "band"
PEER_KEY_SEP = "."


def band_key(band):
    """PURE: the panel key for ONE traffic band's store count. The page asks for exactly this string,
    so it is built here rather than spelled on both sides."""
    return PEER_KEY_SEP.join((PEER_BAND_PREFIX, str(band)))


def _peer_label(r):
    return _label_of("store")(r)


def _peer_fields(r):
    """PURE: what else a reader needs beside one store's row on the peer panels."""
    r = r or {}
    return {"market": r.get("market") or None,
            "traffic band": r.get("band_label") or None,
            "bill payments": r.get("billpay_txns"),
            "boxes": r.get("boxes"),
            "peers in band": r.get("peers")}


def _in_band(band):
    def _read(r):
        return (r or {}).get("band") == band
    return _read


def _peer_has_peers(r):
    """PURE: does this store have anybody in its band to be compared with? The producer's own peer
    count, read rather than re-derived from the band's membership."""
    return int((r or {}).get("peers") or 0) > 0


def _peer_gap(metric):
    """PURE: this store's gap entry for the ranked metric, which the producer wrote or did not."""
    def _read(r):
        return ((r or {}).get("gaps") or {}).get(metric)
    return _read


def _peer_behind_measurable(metric):
    """PURE: did the report reach a verdict about this store on this metric at all? A store with no
    peer has no gap by `_gaps`' own rule, and a store carrying no value for the ranked metric has no
    entry either — two absences, told apart by the reason below."""
    gap = _peer_gap(metric)

    def _read(r):
        return bool(gap(r))
    return _read


def _peer_behind(metric):
    gap = _peer_gap(metric)

    def _read(r):
        g = gap(r)
        return bool(g and g.get("behind_median"))
    return _read


def _peer_behind_reason(metric):
    """PURE: WHY this store carries no behind-or-not verdict — the two absences told apart. A store
    alone in its band has nobody to be behind; a store whose value for the ranked metric the report
    could not answer has nothing to compare."""
    measurable = _peer_behind_measurable(metric)

    def _read(r):
        if measurable(r):
            return None
        return REASON_NO_PEER if not _peer_has_peers(r) else REASON_NO_SALES_ROWS
    return _read


def peer_catalogue(sales_tables, bands=(), metric=DEFAULT_GAP_METRIC, metric_label=None):
    """PURE: the peer panels — ONE store row, counted per traffic band and once for "behind its own
    band".

    Both are COUNTS over the same rows out of the same provenance, which is why this report
    contributes exactly ONE grain to the family: a peer store row, counted. Every other column on it
    is a rate, a median or a distinct-transaction set, and the header says which ruling refuses each.
    """
    common = {"row_label": _peer_label, "keys": ("store",), "fields": _peer_fields,
              "schema": SCHEMA, "tables": tuple(sales_tables or ()),
              "unit": report_drilldown.UNIT_COUNT}
    cat = [
        dict(common, key=PEER_BEHIND_KEY, total=PEER_BEHIND_KEY,
             label="Stores behind their own traffic band on %s"
                   % str(metric_label or metric).lower(),
             value=_zero_or_one(_peer_behind(metric), _peer_behind_measurable(metric)),
             in_figure=_peer_behind(metric),
             reason=_peer_behind_reason(metric), note=_PEER_BEHIND_NOTE),
    ]
    for band, label in bands or ():
        cat.append(dict(
            common, key=band_key(band), total=band_key(band),
            label="%s — stores in this traffic band" % (label or band_key(band)),
            value=_zero_or_one(_in_band(band), lambda _r: True),
            in_figure=_in_band(band), note=_PEER_BAND_NOTE))
    return tuple(cat)


def peer_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/peer-comparison`.

    `payload` is the endpoint's own response. The keys are `behind_median` for the lagging count the
    page headlines and `band.<n>` for each traffic band's store count, so the page can offer a link
    beside every number it prints that a row list exists for.

    THE RANKED METRIC IS THE PAYLOAD'S OWN. The endpoint stamps it into `params`, and the page's
    `Compare on` picker is a query parameter, so the panel is folded over the same metric the figure
    was. A window nothing could be banded in drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    if not rows:
        return {}
    metric = str((payload.get("params") or {}).get("metric") or "").strip() \
        or str(payload.get("default_gap_metric") or "").strip() or DEFAULT_GAP_METRIC
    label = None
    for m in payload.get("gap_metrics") or ():
        if (m or {}).get("key") == metric:
            label = m.get("label")
    bands = [(b.get("band"), b.get("label")) for b in (payload.get("bands") or ()) if b]
    totals = {PEER_BEHIND_KEY: len([x for x in (payload.get("lagging") or ()) if x])}
    for b in payload.get("bands") or ():
        if b and b.get("stores") is not None:
            totals[band_key(b.get("band"))] = b.get("stores")
    tables = sales_union_tables((payload.get("source_meta") or {}).get("sales"))
    return _served(peer_catalogue(tables, bands=bands, metric=metric, metric_label=label),
                   rows, totals, cap)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  PRODUCT MIX & PORTS — one rep-at-a-store row, two units and two verdicts.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

MIX_LABELS = {
    "boxes": "Boxes sold",
    "accessory_revenue": "Accessory dollars",
    "ports": "Port-ins",
    "device_lines": "Devices sold",
    "cheap_devices": "Devices in the cheap bands",
    "flagged_reps": "Salespeople low on both signals",
    "ranked_reps": "Salespeople with enough boxes to rank",
}

#: WHICH FIGURE EACH PANEL OPENS AT COMPANY GRAIN, as a path into the producer's own payload. The
#: flagged count is the LENGTH of the producer's own flagged list and the ranked count is its own
#: `basis` entry — neither is folded here.
MIX_STORE_TOTALS = ("boxes", "accessory_revenue", "ports", "device_lines", "cheap_devices",
                    "flagged_reps", "ranked_reps")


def _mix_label(r):
    return _label_of("rep")(r)


def _mix_fields(r):
    """PURE: what else a reader needs beside one salesperson's figure on this row."""
    r = r or {}
    return {"store": r.get("store") or None,
            "transactions": r.get("transactions"),
            "boxes": r.get("boxes"),
            "verdict": (r.get("verdict") or "not ranked"),
            "ranked": ("yes" if r.get("ranked") else "no")}


def _mix_ranked(r):
    """PURE: the producer's own ranking verdict for this row, read rather than re-tested against a
    box floor this file does not hold."""
    return bool((r or {}).get("ranked"))


def _mix_has_verdict(r):
    """PURE: did the report reach a two-signal verdict about this person at all? A rep below the
    ranking floor carries `verdict: None` and the producer's own reason."""
    return bool((r or {}).get("verdict"))


def _mix_flagged(r):
    """PURE: is this the verdict the report flags — low on BOTH signals? The producer's own word,
    read off the row, never a second comparison against two medians."""
    return str((r or {}).get("verdict") or "") == _pmix.VERDICTS[0][0]


def mix_catalogue(sales_tables):
    """PURE: the mix panels — ONE rep-at-a-store row, a COUNT grain and a MONEY grain over the same
    sales provenance (§CORE-17.3l), plus the two counts the report's verdicts rest on."""
    common = {"row_label": _mix_label, "keys": ("store", "rep"), "fields": _mix_fields,
              "schema": SCHEMA, "tables": tuple(sales_tables or ())}
    counted = dict(common, unit=report_drilldown.UNIT_COUNT, note=_MIX_SOLD_NOTE)
    return (
        {"key": "boxes", "total": "boxes", "label": MIX_LABELS["boxes"],
         "value": _column("boxes"), "column": "box_count", **counted},
        {"key": "accessory_revenue", "total": "accessory_revenue",
         "label": MIX_LABELS["accessory_revenue"], "value": _column("accessory_revenue"),
         "column": "accessory_rev", "unit": report_drilldown.UNIT_MONEY,
         "note": _MIX_SOLD_NOTE, **common},
        {"key": "ports", "total": "ports", "label": MIX_LABELS["ports"],
         "value": _column("ports"), "column": "contract_type", **counted},
        {"key": "device_lines", "total": "device_lines", "label": MIX_LABELS["device_lines"],
         "value": _column("device_lines"), "column": "product_desc", **counted},
        {"key": "cheap_devices", "total": "cheap_devices", "label": MIX_LABELS["cheap_devices"],
         "value": _column("cheap_devices"), "column": "ext_price", **counted},
        {"key": "flagged_reps", "total": "flagged_reps", "label": MIX_LABELS["flagged_reps"],
         "value": _zero_or_one(_mix_flagged, _mix_has_verdict), "in_figure": _mix_flagged,
         "reason": _reason_when(_mix_has_verdict, REASON_BELOW_FLOOR),
         "note": _MIX_VERDICT_NOTE, **dict(common, unit=report_drilldown.UNIT_COUNT)},
        {"key": "ranked_reps", "total": "ranked_reps", "label": MIX_LABELS["ranked_reps"],
         "value": _zero_or_one(_mix_ranked, lambda _r: True), "in_figure": _mix_ranked,
         "note": _MIX_RANKED_NOTE, **dict(common, unit=report_drilldown.UNIT_COUNT)},
    )


def product_mix_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/product-mix`.

    The keys are the bare figure names for the COMPANY figures and `store.<store>.<figure>` for each
    store's own, so the page can offer a link beside the tiles it prints and beside each store row.

    Every figure is the producer's own: the flagged count is the length of its own flagged list, the
    ranked count its own `basis` entry, and each store's figures its own store roll-up. A window
    nobody sold anything in drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("reps") or ()) if r]
    if not rows:
        return {}
    basis = dict(payload.get("basis") or {})
    tables = sales_union_tables((payload.get("source_meta") or {}).get("sales"))
    cat = mix_catalogue(tables)
    totals = {"flagged_reps": len([r for r in (payload.get("flagged") or ()) if r])}
    if basis.get("ranked_reps") is not None:
        totals["ranked_reps"] = basis.get("ranked_reps")
    out = dict(_served(cat, rows, totals, cap))
    for s in payload.get("stores") or ():
        if not s:
            continue
        code = str(s.get("store") or "").strip()
        if not code:
            continue
        srows = [r for r in rows if str(r.get("store") or "").strip() == code]
        if not srows:
            continue
        stotals = {k: s.get(k) for k in MIX_STORE_TOTALS if s.get(k) is not None}
        for key, panel in _served(cat, srows, stotals, cap).items():
            panel["subject"] = code
            panel["subject_kind"] = "store"
            out["store.%s.%s" % (code, key)] = panel
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  SPIFF IMPACT — one store row, three provenances and two units.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

SPIFF_LABELS = {
    "spiff_amount": "Paid for this pay type",
    "spiff_units": "Paid units",
    "line_total": "Everything the carrier paid to this P&L line",
    "carrier_total": "Everything the carrier paid",
    "net_income": "Net profit, with this pay type in it",
    "revenue": "Revenue",
    "boxes": "Boxes sold",
    "stores_at_a_loss": "Stores with no profit base to lift",
}


def _spiff_label(r):
    return _label_of("store")(r)


def _spiff_fields(r):
    """PURE: what else a reader needs beside one store's figure on this row."""
    r = r or {}
    return {"market": r.get("market") or None,
            "traffic band": r.get("band_label") or None,
            "paid units": r.get("spiff_units"),
            "boxes": r.get("boxes"),
            "stored P&L": ("recorded" if r.get("net_income") is not None else "none for this month")}


def _spiff_has_snapshot(r):
    """PURE: did a statement run write a P&L down for this store this period? The producer's own
    answer — `profit_effect` returns None on every profit figure when it did not."""
    return (r or {}).get("net_income") is not None


def _spiff_has_boxes(r):
    """PURE: did the sales read name any rows for this store? The producer carries None rather than
    zero, and refuses to rank such a store for exactly this reason."""
    return (r or {}).get("boxes") is not None


def _spiff_at_a_loss(r):
    """PURE: is this store's profit WITHOUT the pay type not positive? The producer's own pair of
    figures, read off the row — this file holds no threshold and no subtraction of its own."""
    r = r or {}
    base = r.get("net_income_ex_spiff")
    return base is not None and report_drilldown.money(base) <= report_drilldown.money(0)


def _spiff_loss_measurable(r):
    r = r or {}
    return _spiff_has_snapshot(r) and r.get("net_income_ex_spiff") is not None


def spiff_catalogue(sales_tables):
    """PURE: the spiff panels — ONE store row and THREE provenances on it, which is what makes this
    report's grains interesting: the carrier's statement, the stored financial statements, and
    whichever sales relations the period's union read. Money and a count sit on two of the three
    (§CORE-17.3l), and the statements are separated from the statement alone by PROVENANCE
    (§CORE-17.3n)."""
    common = {"row_label": _spiff_label, "keys": ("store",), "fields": _spiff_fields,
              "schema": SCHEMA}
    carrier = dict(common, tables=(COMP_REPORT,), note=_SPIFF_MONEY_NOTE)
    books = dict(common, tables=(FINANCIAL_STATEMENTS,), note=_SPIFF_PROFIT_NOTE,
                 reason=_reason_when(_spiff_has_snapshot, REASON_NO_SNAPSHOT))
    return (
        {"key": "spiff_amount", "total": "spiff_amount", "label": SPIFF_LABELS["spiff_amount"],
         "value": _column("spiff_amount"), "column": "payment_amount",
         "unit": report_drilldown.UNIT_MONEY, **carrier},
        {"key": "spiff_units", "total": "spiff_units", "label": SPIFF_LABELS["spiff_units"],
         "value": _column("spiff_units"), "column": "quantity",
         "unit": report_drilldown.UNIT_COUNT, **carrier},
        {"key": "line_total", "total": "line_total", "label": SPIFF_LABELS["line_total"],
         "value": _column("line_total"), "column": "payment_amount",
         "unit": report_drilldown.UNIT_MONEY, **carrier},
        {"key": "carrier_total", "total": "carrier_total", "label": SPIFF_LABELS["carrier_total"],
         "value": _column("carrier_total"), "column": "payment_amount",
         "unit": report_drilldown.UNIT_MONEY, **carrier},
        {"key": "net_income", "total": "net_income", "label": SPIFF_LABELS["net_income"],
         "value": _column("net_income"), "column": "net_income",
         "unit": report_drilldown.UNIT_MONEY, **books},
        {"key": "revenue", "total": "revenue", "label": SPIFF_LABELS["revenue"],
         "value": _column("revenue"), "column": "revenue",
         "unit": report_drilldown.UNIT_MONEY, **books},
        {"key": "stores_at_a_loss", "total": "stores_at_a_loss",
         "label": SPIFF_LABELS["stores_at_a_loss"],
         "value": _zero_or_one(_spiff_at_a_loss, _spiff_loss_measurable),
         "in_figure": _spiff_at_a_loss,
         "reason": _reason_when(_spiff_loss_measurable, REASON_NO_SNAPSHOT),
         "column": "net_income", "unit": report_drilldown.UNIT_COUNT,
         **dict(common, tables=(FINANCIAL_STATEMENTS,), note=_SPIFF_LOSS_NOTE)},
        {"key": "boxes", "total": "boxes", "label": SPIFF_LABELS["boxes"],
         "value": _column("boxes"), "column": "box_count",
         "reason": _reason_when(_spiff_has_boxes, REASON_NO_SALES_ROWS),
         "unit": report_drilldown.UNIT_COUNT,
         **dict(common, tables=tuple(sales_tables or ()), note=_SPIFF_BOXES_NOTE)},
    )


def spiff_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/spiff-impact`.

    Every figure is the endpoint's own `estate` entry dereferenced, so a panel can never be a second
    opinion about the tile above it. A period the carrier statement carries nothing for drills to
    nothing.

    THE ESTATE PROFIT BASE IS DELIBERATELY NOT HERE. `net_income_ex_spiff` is composed at the TOTAL
    from two other totals (§CORE-17.3k) and is also understated, because the profit side covers only
    the stores with a stored statement while the spiff side covers all of them (defect 5). Both
    figures it is composed from ARE served.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    estate = dict(payload.get("estate") or {})
    if not rows or not estate:
        return {}
    tables = sales_union_tables((payload.get("source_meta") or {}).get("sales"))
    totals = {k: v for k, v in estate.items() if k in SPIFF_LABELS}
    return _served(spiff_catalogue(tables), rows, totals, cap)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  TAX COLLECTED — one store row, one store-DAY row, and the tender split of the first.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

TAX_LABELS = {
    "tax": "Sales tax collected",
    "revenue": "Total sales, pre-tax",
    "taxable_revenue": "Sales that carried tax",
    "tax_net": "Sales tax net of refunds",
    "revenue_net": "Total sales net of refunds",
    "returns_tax": "Tax refunded on returns",
    "returns_revenue": "Sales refunded on returns",
    "return_rows": "Return lines",
}

#: The figures a STORE's own panel opens, over that store's DAY rows. The headline only: the other
#: three come out of the same days through the same read, and the page already prints every one of
#: those day rows underneath the store it expands.
TAX_STORE_FIGURES = ("tax",)

#: The tender split's four figures, each a column of one bucket on one store row. Declared as a
#: table so a fifth cannot be added without saying which figure of the report it is.
TAX_TENDER_FIGURES = (
    ("sales", "Sales paid this way", report_drilldown.UNIT_MONEY),
    ("taxable_revenue", "Sales paid this way that carried tax", report_drilldown.UNIT_MONEY),
    ("untaxed_revenue", "Sales paid this way that carried no tax", report_drilldown.UNIT_MONEY),
    ("tax", "Tax collected on sales paid this way", report_drilldown.UNIT_MONEY),
)

TAX_STORE_PREFIX = "store"
TAX_TENDER_PREFIX = "tender"
TAX_KEY_SEP = "."


def tax_store_key(store, figure):
    """PURE: the panel key for ONE store's own figure. The page asks for exactly this string."""
    return TAX_KEY_SEP.join((TAX_STORE_PREFIX, str(store or ""), str(figure or "")))


def tax_tender_key(bucket, figure):
    """PURE: the panel key for ONE tender bucket's figure."""
    return TAX_KEY_SEP.join((TAX_TENDER_PREFIX, str(bucket or ""), str(figure or "")))


def _tax_label(r):
    return _label_of("store")(r)


def _tax_fields(r):
    r = r or {}
    return {"market": r.get("market") or None,
            "taxable sales": r.get("taxable_revenue"),
            "non-taxable sales": r.get("untaxed_revenue"),
            "return lines": r.get("return_rows")}


def _tax_day_label(r):
    return _label_of("date")(r)


def _tax_day_fields(r):
    r = r or {}
    return {"taxable sales": r.get("taxable_revenue"),
            "non-taxable sales": r.get("untaxed_revenue"),
            "return lines": r.get("return_rows")}


def tax_catalogue(sales_tables):
    """PURE: the tax panels at COMPANY grain — one store row, money seven ways and a line COUNT,
    every one of them out of the transaction-grain sales read for every month the window touched."""
    common = {"row_label": _tax_label, "keys": ("store",), "fields": _tax_fields,
              "schema": SCHEMA, "tables": tuple(sales_tables or ())}
    out = []
    for key in ("tax", "revenue", "taxable_revenue"):
        out.append(dict(common, key=key, total=key, label=TAX_LABELS[key], value=_column(key),
                        column=("tax" if key == "tax" else "ext_price"),
                        unit=report_drilldown.UNIT_MONEY, note=_TAX_GROSS_NOTE))
    for key in ("tax_net", "revenue_net", "returns_tax", "returns_revenue"):
        out.append(dict(common, key=key, total=key, label=TAX_LABELS[key], value=_column(key),
                        column=("tax" if "tax" in key else "ext_price"),
                        unit=report_drilldown.UNIT_MONEY, note=_TAX_NET_NOTE))
    out.append(dict(common, key="return_rows", total="return_rows", label=TAX_LABELS["return_rows"],
                    value=_column("return_rows"), column="trans_type",
                    unit=report_drilldown.UNIT_COUNT, note=_TAX_NET_NOTE))
    return tuple(out)


def tax_tender_catalogue(sales_tables, bucket):
    """PURE: ONE tender bucket's four figures, each Σ(that bucket's column) over the store rows."""
    common = {"row_label": _tax_label, "keys": ("store",), "fields": _tax_fields,
              "schema": SCHEMA, "tables": tuple(sales_tables or ()), "note": _TAX_TENDER_NOTE}
    return tuple(
        dict(common, key=tax_tender_key(bucket, figure), total=tax_tender_key(bucket, figure),
             label=label, value=_nested("tender", bucket, figure),
             column=("tax" if figure == "tax" else "ext_price"), unit=unit)
        for figure, label, unit in TAX_TENDER_FIGURES)


def tax_store_catalogue(sales_tables):
    """PURE: ONE store's own figure over its DAY rows — the grain the page expands to."""
    common = {"row_label": _tax_day_label, "keys": ("store", "date"), "fields": _tax_day_fields,
              "schema": SCHEMA, "tables": tuple(sales_tables or ()), "note": _TAX_DAY_NOTE}
    return tuple(
        dict(common, key=figure, total=figure, label=TAX_LABELS[figure], value=_column(figure),
             column=("tax" if "tax" in figure else "ext_price"),
             unit=report_drilldown.UNIT_MONEY)
        for figure in TAX_STORE_FIGURES)


def tax_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/tax-collected`.

    The keys are the bare figure names for the company tiles, `tender.<bucket>.<figure>` for the
    tender split, and `store.<store>.tax` for each store's own figure over its days — so the page can
    offer a link beside every number it prints that a row list exists for.

    THE COMPANY NON-TAXABLE FIGURE IS DELIBERATELY NOT HERE. `totals['untaxed_revenue']` is taken as
    a subtraction between two other totals rather than folded from the store rows (§CORE-17.3k), and
    the two it is the difference of are both served. The per-bucket and per-store non-taxable figures
    ARE folded per row by the producer, and the first of those is served.

    A window with no store rows drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("stores") or ()) if r]
    totals = dict(payload.get("totals") or {})
    if not rows or not totals:
        return {}
    tables = sales_union_tables((payload.get("source_meta") or {}).get("sales"))
    out = dict(_served(tax_catalogue(tables), rows, {k: v for k, v in totals.items()
                                                     if k in TAX_LABELS}, cap))
    # THE TENDER SPLIT, for the buckets the report actually shows. The page prints a bucket only when
    # it took sales or tax, so a panel for an empty bucket would be a panel with no tile — and a
    # bucket of 0.00 over rows that all read 0.00 would tie trivially and read as a measurement.
    tender_totals = dict(totals.get("tender") or {})
    for bucket, figures in sorted(tender_totals.items()):
        f = dict(figures or {})
        if report_drilldown.money(f.get("sales")) == report_drilldown.money(0) \
                and report_drilldown.money(f.get("tax")) == report_drilldown.money(0):
            continue
        btotals = {tax_tender_key(bucket, k): v for k, v in f.items()}
        for key, panel in _served(tax_tender_catalogue(tables, bucket), rows, btotals, cap).items():
            panel["subject"] = bucket
            panel["subject_kind"] = "tender"
            out[key] = panel
    # EACH STORE'S OWN FIGURE, over its DAY rows — a different ROW SHAPE, and the one the undated-row
    # defect shows up in (defect 2): the tie-out publishes it rather than the panel hiding it.
    for s in rows:
        code = str(s.get("store") or "").strip()
        days = [dict(d, store=code) for d in (s.get("days") or ()) if d]
        if not code or not days:
            continue
        stotals = {k: s.get(k) for k in TAX_STORE_FIGURES if s.get(k) is not None}
        for key, panel in _served(tax_store_catalogue(tables), days, stotals, cap).items():
            panel["subject"] = code
            panel["subject_kind"] = "store"
            out[tax_store_key(code, key)] = panel
    return out
