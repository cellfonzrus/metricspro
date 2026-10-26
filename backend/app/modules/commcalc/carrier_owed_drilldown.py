"""WHERE DID THIS CARRIER-OWED FIGURE COME FROM — the declaration for the four reports that answer
*what did we expect the carrier to pay, what did it pay, and what is being clawed back or is
missing* (§CORE-17, batch 19).

  /commcalc/expected-commission  Expected vs Earned — what each multi-month installment WOULD pay,
                                 beside what it actually did, and who moved one across by hand.
  /commcalc/ma-commission        Total Processor Commissions — what the master agent's own statement
                                 says it owes us, per processor account.
  /commcalc/recovery             Appeal Recovery — commission the carrier DENIED, on lines that
                                 later paid or activated, inside the claw-back window.
  /commcalc/dlar-vs-platform     Feed vs store transactions — whether the carrier's own activity
                                 report and our transactions even agree on what happened.

ONE FAMILY, AND THE SEAM IS STATED RATHER THAN GLOSSED. All four are read for one purpose: the
dealer's position against the carrier. Three of them are MONEY at four places on that position —
expected, paid, denied-and-reclaimed — and the fourth is the COUNT the money is computed on, which is
why it belongs: a commission dispute that starts "you paid me for 180 activations and I rang 206" is
settled on `/dlar-vs-platform` before any of the three dollar figures can be argued. It shares no
relation with the other three and its unit is a comparison cell rather than a dollar, so it gets its
own catalogue and its own grains — not a forced place in theirs.

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED OFF THE PRODUCERS, NOT ASSUMED (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────────────────────
Driving `sale_installment_engine.compute_sale_installments` + the endpoint's own row fold,
`router.ma_commission_summary`'s per-account fold, `recovery.engine.build_recovery_ledger` +
`recovery.engine.bucket_totals`, and `dlar_vs_platform.entity_row` / `summarize` gives **NINE**
grains in **FOUR** row shapes, because a grain fixes the ROW, the UNIT **and** the PROVENANCE:

   1. an INSTALLMENT MONTH's EARNED            money, out of WHICHEVER GATE released it — and that
                                               differs ROW BY ROW: the subscriber residual report,
                                               the master-agent statement (alone, or with the daily
                                               transactions beside it), the activation transaction
                                               itself, or the declared ladder on a month the
                                               schedule does not gate at all. ONE figure.
   2. an INSTALLMENT MONTH's EXPECTED           the SAME row in the SAME unit, and a provenance that
                                               is the DECLARED LADDER alone — its own grain for
                                               exactly that reason (§CORE-17.3n). An expected dollar
                                               is config somebody saved multiplied by a sale; no
                                               carrier feed is involved in it at all. TWO figures,
                                               differing only in which rows they count.
   3. an INSTALLMENT MONTH's PROMOTED EARNING   the same row a third time, same unit, and the
                                               provenance is a PERSON'S APPROVAL (§CORE-17.3n). The
                                               one figure in this family whose evidence is not a
                                               feed, a sheet or a statement but a human decision.
   4. a PROCESSOR ACCOUNT's COMMISSION MONEY    money, out of the MASTER-AGENT COMMISSION STATEMENT
                                               alone. THREE figures — the payable, the rebates and
                                               the month-1-to-6 spiffs — one fold of one row list
                                               differing only in which column holds the figure.
   5. a PROCESSOR ACCOUNT's ACTIVATION COUNT    the same row in a different UNIT, therefore its own
                                               grain (§CORE-17.3l). ONE figure.
   6. a PROCESSOR ACCOUNT's AIRTIME MARGIN      the same row in the SAME unit as (4), separated ONLY
                                               by provenance: the airtime margin is the MASTER-AGENT
                                               DAILY TRANSACTIONS and nothing else, it is not in the
                                               payable beside it, and the page itself drops it under
                                               a rep filter because it has no rep dimension.
   7. a RECOVERY LEDGER ROW's OWED COMMISSION   money, out of the STORED RECOVERY RUN (§CORE-17.3j)
                                               plus, per row, the relation the snapshot recorded as
                                               its EVIDENCE — or none at all for a row nothing
                                               proved. FOUR figures, one per bucket, each counting
                                               only its own bucket and SHOWING the other three.
   8. a RECOVERY LEDGER ROW's DEVICE COUNT      the same row in a different UNIT. FOUR figures.
   9. an ENTITY COMPARISON ROW's CELL COUNT     count, over the carrier's own activity report at
                                               that entity's grain ∪ whichever basis produced the
                                               platform side — and, for an entity one of the two
                                               sides never mentioned, over NOTHING on that side,
                                               because an absence is not a row in a relation
                                               (§CORE-17.3i). FOUR figures.

A SUBTRACTION AT THE TOTAL GETS NO PANEL, AND BOTH ITS SIDES DO (§CORE-17.3k)
─────────────────────────────────────────────────────────────────────────────
`/dlar-vs-platform` prints five tiles and TWO of them are subtractions the BROWSER does over the
server's own totals: `Agree` is `cells_compared - cells_differing` and `Attributed` is
`cells_differing - cells_unattributed - cells_undecidable`. Neither gets a panel. All FOUR of the
figures they are composed of do — including `cells_differing`, which is on no tile at all and is
served precisely so that both sides of `Agree` can be opened. That is the batch-18 Sales-Comparison
`Δ` ruling, in the other direction: there the two sides were already tiles, here one of them is not.

The same ruling goes the other way on `/expected-commission`, where the subtraction IS at the row:
`expected_not_yet_earned` is Σ(expected) over the rows whose gate is unmet — the producer's own
predicate, not `Σexpected − Σearned` — so it drills, and a month waiting on the carrier is a row.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED PER PAGE BY READING THE PAGE
──────────────────────────────────────────────────────────────────────────────────────────────
**All four are SERVER-FOLDED and none is narrowed**, and the two reasons are different:

  · `/commcalc/ma-commission` and `/commcalc/dlar-vs-platform`'s period, and
    `/commcalc/recovery`'s bucket, are QUERY PARAMETERS — the server folds again and every panel
    follows its tile by construction. The MA page reduces nothing; the recovery page reduces nothing.
  · `/commcalc/expected-commission` and `/commcalc/dlar-vs-platform` ALSO carry browser filters, and
    on both of them those filters move the TABLES and not the TILES: every tile reads `data.totals`
    / `data.summary` straight off the server while the tables below are `filterRows(...)`-ed. So
    narrowing the panels would make them explain a figure the tile is not showing. REPORTED
    (defects 9 and 10) rather than fixed, because each honest repair changes a figure on screen.

Neither page contains a `reduce(` that folds a tile. `harness_batch19_drilldown.py` §F locks all
four decisions, in both directions, each with a non-vacuity witness so "the text is absent" can
never be confused with "the scan is blind".

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **`Agree` AND `Attributed` ON THE COMPARISON** — the subtract-at-the-TOTAL ruling above. All
    four figures they are made of drill.
  · **EVERY RATE AND EVERY AVERAGE.** The comparison's `rate`-kind metrics (the app, ATU, protect,
    BYOD, 3MR, family-plan and AAL percentages), the MA report's `Avg MRC`, and every per-metric
    `net_delta` over a rate — §CORE-17.3h. The two figures each divides are the ones that drill.
  · **THE MA COMMISSION LEGS AND THE SPIFF-BY-MONTH LADDER.** `m1` / `m2_12` / `unsplit` and every
    `m<n>` rung are a RE-PARTITION of the very payable the money panel already opens, worked out
    from the ORG-WIDE component totals — the payload carries no per-account figure for any rung, so
    there is no row list of that grain to fold. The batch-16 `by_vendor` refusal, one report along.
  · **THE MA REPORT'S `airtime.orders` AND `airtime.retail`.** `by_store` carries an airtime MARGIN
    per account and neither an order count nor a retail figure, so those two have no row list on
    this payload at all. Reported rather than approximated.
  · **THE MA REPORT'S FOUR OTHER COMPONENTS** (`device_margin`, `consumer_margin`,
    `consumer_financing`, `wallet_funding`, `fees_margin`). They are on no tile and `by_store`
    carries no column for any of them — same refusal, same reason.
  · **`by_rep`, `by_platform`, `by_metric` AND `expected-commission`'s `by_rep`.** Re-partitions
    that print their own totals on their own tables.
  · **THE COMPARISON'S `entities*` COUNTS.** They count rep rows AND store rows together, so each is
    a cardinality over two kinds of thing with no row list of one grain (defect 12). They are on no
    tile — the batch-18 zero-sales `scopes` refusal.
  · **THE RECOVERY CLAIM BATCHES.** A claim's `device_count` and `total_amount` are stored on the
    claim row and this payload carries none of its lines, so the figures have no rows here. The
    claim's own route serves them (defect 8).
  · **`promotes_applied`, `rows`, `date_range`, `detection` AND EVERY CONFIG FIGURE.** A count the
    panel already states, a row count, a date span, a status and numbers somebody typed into a
    settings screen. None is a folded figure.
  · **A FIGURE NO ROW CARRIES A NUMBER FOR.** Where a column can be absent on every row — the
    airtime margin on a period whose daily-transaction feed never landed, a bucket nothing fell into
    — NO PANEL is served rather than a $0.00 that ties against zero counted rows and reads as a
    measurement.

ABSENCE IS NEVER ZERO
─────────────────────
A processor account the COMMISSION statement never mentions is on the by-store table anyway, because
the AIRTIME feed named it — and the producer initialises its payable, its spiffs, its rebates and its
activation count at zero. Those four are fabrications: nothing was measured for that account at all.
Such a row is SHOWN WITHOUT A FIGURE for all four, with a reason, and is not counted — and **no
figure moves by a cent**, because the row contributed 0.00 and 0 to the producer's totals and
contributes 0.00 and 0 here. Only the ROW changes, from a measured zero into a stated blank.

An entity the carrier's report never mentioned, and an entity our transactions never mentioned, each
name only the side that DID answer — the other side was not read out of a relation at all.

AN EMPTY REPORT DRILLS TO NOTHING. Every entry point below returns an empty mapping when the producer
folded its totals over no rows.

THE PANEL CANNOT DISCLOSE WHAT THE REPORT DOES NOT, BY CONSTRUCTION
───────────────────────────────────────────────────────────────────
Every entry point's only input is the endpoint's OWN RESPONSE — never a client, never a query. So a
panel is folded over rows the caller is already being served, after every filter the endpoint
applied, and a drill-down cannot become a disclosure. That matters especially here: three of these
four endpoints apply NO per-store RBAC scope at all (defect 11), which is reported rather than
quietly repaired — and because the panels fold the payload's own rows, this batch neither widens nor
narrows what any caller can see.

LIVE DEFECTS THESE FOUR REPORTS HAVE TODAY — FOURTEEN REPORTED, NO FIGURE MOVED
─────────────────────────────────────────────────────────────────────────────
 1. **The multi-month engine's SALE READ does not record which table answered.**
    `commission_engine._read_sales` is `raw_sales` *or*, when that is empty, `daily_sales_feed` —
    and it returns rows only, discarding which of the two it read. So for the one EARNED grain whose
    evidence is the activation transaction itself (a month-1 `activation_payment` gate), this report
    cannot name the relation its proof came out of. Those rows report a stated, countable
    UNTRACEABLE ORIGIN (§CORE-17.3i) rather than a guessed feed. NOT fixed: returning the meta is a
    pay-path change, and the pay path is money.
 2. **`/expected-commission` was discarding the gate's own `gate_source`.** The engine stamps
    `gate_source` ('ma_commission' / 'ma_tx') on every master-agent-gated ledger row precisely so a
    surface can say which feed proved the month — and the endpoint's row builder never copied it, so
    the one fact distinguishing "the statement paid it" from "the statement and the daily
    transactions together paid it" was thrown away every request. Carried through read-only now;
    no figure moved. Same class as batch 18's defect 4.
 3. **`/dlar-vs-platform` was discarding the provenance its own sales read returned.**
    `rows, _umeta = _sales_rows_union(...)` and `_umeta` was never read, so the fact saying which
    tables the platform side came out of was discarded. Collected read-only now. Same class again —
    the THIRD report in two batches with this exact shape, which is why it is worth naming as a
    class: a read that returns `(rows, meta)` has a meta because somebody needed it, and binding it
    to `_meta` is how it gets lost.
 4. **A processor account with no commission row reports $0.00 of commission and 0 activations.**
    `by_store` is `setdefault`-ed from the airtime feed as well as the commission statement, so an
    account that only ever bought airtime is initialised at zero across four commission figures.
    Shown without a figure here and not counted; the producer's totals are unchanged.
 5. **An account's AIRTIME MARGIN of $0.00 cannot be told from "no airtime feed for this account".**
    `by_store` records a margin and no per-account order count, so a genuine zero-margin month and
    a month the daily transactions never covered are the same number. COUNTED (the producer really
    did add it) and said so in the panel's note — the batch-15 "counted but not trusted" posture.
 6. **The MA roll-up's `total_payable` and its `by_store` payables are rounded at DIFFERENT
    LEVELS.** The total is `round(Σ raw, 2)` and each row is `round(row, 2)`, so over enough
    accounts Σ(rows) can differ from the total by cents. The tie-out SAYS so when it happens, and
    the shared `agreesToTheCent` gate then withholds the drill link rather than showing a panel
    about a figure it disagrees with. NOT fixed: re-rounding either side moves a displayed figure.
 7. **The recovery snapshot keeps no reference to the appeal row it was built from.**
    `build_recovery_ledger` reads `owed_to_vip` off the distributor's unit ledger and writes
    `owed_amount` into its own table without the ledger row's key, so the trail stops at the stored
    run (§CORE-17.3j). The panels name the run, and the evidence feed where the row recorded one.
 8. **The recovery claim batches carry figures with no lines on this payload.** `/recovery/ledger`
    serves `claims` with a `device_count` and a `total_amount` each, and none of their lines — those
    live behind `/recovery/claims/{id}`. No panel is served for them.
 9. **On `/expected-commission` the filters move the tables and not the tiles.** The store and rep
    pickers are `filterRows` in the browser while all four tiles read `data.totals` — the server's
    whole-period fold. Under any filter the strip and the tables below it describe different
    populations and nothing on screen says so. NOT fixed (§CORE-17.3o).
10. **The same, on `/dlar-vs-platform`, with a second axis.** Its filter bar AND its per-rep /
    per-store toggle AND its "only rows with a difference" checkbox all work in the browser, while
    the five tiles are the server's fold over BOTH grains at once. So the strip counts rep cells and
    store cells together while the table shows one of the two. NOT fixed; the panels say which
    population they are over, in words.
11. **Three of these four endpoints apply no per-store RBAC scope at all.**
    `/expected-commission`, `/ma-commission/summary` and `/recovery/ledger` are org-scoped and
    nothing narrower; only `/dlar-vs-platform` runs `scope_keyset` / `in_keyset`. REPORTED, not
    fixed: adding a scope filter changes who sees which figure, which is a permissions decision and
    not a drill-down one. The panels disclose nothing new either way — see the section above.
12. **`/dlar-vs-platform`'s `entities*` summary counts mix two grains**, exactly as batch 18's
    `scopes` did: `len(rows)` over rep rows AND store rows. They reach the API contract and no tile,
    and they get no panel.
13. **`/expected-commission`'s own `rep=` / `store=` parameters narrow its ROWS and not its
    TOTALS.** The server filters `rows` and `by_rep` inside the loop and then reads every tile
    figure out of the engine's whole-period `totals` / `expected_guard`. The page never sends those
    parameters (it filters in the browser instead — defect 9), so the mismatch is latent rather than
    on screen; a direct API caller that does send one gets a strip describing a population the rows
    beneath it do not. REPORTED. The panel is folded over the rows the endpoint served, so for such
    a call the tie-out STATES the disagreement instead of hiding it, and the shared
    `agreesToTheCent` gate withholds the drill link on the page rather than showing a panel about a
    figure it disagrees with.
14. **`/recovery/ledger`'s rows are capped at 2,000 and the cap is silent.** The bucket COUNTS are
    over every row, so a reader whose estate exceeds the cap sees a tile the table below it cannot
    account for. The panels are folded over the uncapped set (which is what the tiles are) and the
    one home's own `ROW_CAP` then states its own cap honestly.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3s.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import dlar_vs_platform as _dvp
from app.modules.commcalc import expected_commission as _xc
from app.modules.commcalc import processor_money_drilldown as _pmd
from app.modules.core import report_drilldown as report_drilldown

# ── THE PROVENANCE ANSWERS THIS FAMILY HAS, EVERY ONE OF THEM DEREFERENCED ───────────────────────

#: THE CARRIER'S OWN DAILY-ACTIVITY REPORTS, at the two grains it publishes them at. Registry
#: constants as of this batch — both had been spelled as literals FIVE times inside the registry
#: itself, and all five are re-keyed in this same commit, which is the half of a design fix that
#: usually gets left out (CLAUDE.md §19.18).
DLAR_REP = _lineage.DLAR_REP
DLAR_STORE = _lineage.DLAR_STORE

#: WHICH TABLES THE PLATFORM SIDE'S SALES READ ACTUALLY ANSWERED OUT OF — not a constant, and that is
#: the point. Resolved off the read's OWN meta through the ONE home for that question,
#: `processor_money_drilldown.sales_union_tables`, bound BY IDENTITY exactly as batches 14, 17 and 18
#: bound it.
sales_union_tables = _pmd.sales_union_tables

#: The CUSTOM-REPORT capture the uploaded Activation Details report lands in. When the org has stated
#: Activation Details as its activation source of truth, the platform's activation counts are
#: REPLACED out of this relation and the sales union did not answer for them at all — which is the
#: producer's own gate, read off the payload rather than re-decided here (`platform_tables`).
CUSTOM_CAPTURE = _lineage.CUSTOM_CAPTURE

#: The master-agent statement pair. The commission money comes out of the first alone; the airtime
#: margin out of the second alone, which is what makes them two grains on one row (§CORE-17.3n).
MA_COMMISSION = _lineage.MA_COMMISSION
MA_DAILY_TX = _lineage.MA_DAILY_TX

#: The subscriber / residual report — the default paid gate for a direct-carrier chain.
MI_RESIDUAL = _lineage.MI_RESIDUAL

#: The STORED DENIED-APPEAL RECOVERY RUN. A snapshot names itself (§CORE-17.3j): the figures on that
#: report were read out of this relation and not out of the ledger the run was built from (defect 7).
RECOVERY_LEDGER = _lineage.RECOVERY_LEDGER

#: The DECLARED MULTI-MONTH LADDER. An EXPECTED dollar is this config multiplied by a sale, so the
#: ladder is its provenance and no carrier feed is involved in it at all.
INSTALLMENT_SCHEDULE = _lineage.INSTALLMENT_SCHEDULE

#: THE APPROVED EXPECTED-TO-EARNED MOVES, dereferenced from the module that OWNS the promote rather
#: than from a second copy of its name in the feed registry. `expected_commission.TABLE` is the one
#: home for "which relation records a person moving a month into earned", and this is that fact read
#: rather than re-spelled.
INSTALLMENT_PROMOTE = _xc.TABLE

#: AN ANSWER THAT IS NO ANSWER, and it is REPORTED rather than guessed. The multi-month engine's
#: sale read does not record which of the two sales relations answered (defect 1), and an entity one
#: side of the comparison never mentioned was not read out of anything on that side. There is no
#: relation to name, and naming one would be a provenance claim about a row nobody wrote (§CORE-17.3i).
#: `report_drilldown.panel` counts these under `untraceable_origins`.
UNNAMEABLE = None

SCHEMA = "commcalc"

#: WHY A ROW IN THIS FAMILY CARRIES NO NUMBER. The one new shape this batch meets: a processor
#: account that is on the by-store table only because the AIRTIME feed named it, whose four
#: commission figures the producer initialises at zero (defect 4). Added to
#: `report_drilldown.UNMEASURED_REASONS` — the ONE vocabulary — rather than spelled here.
REASON_NO_COMMISSION_ROWS = "no_commission_rows_for_account"

#: THE PRODUCER'S OWN WORDS FOR WHICH GATE RELEASED A MONTH, dereferenced so this declaration holds
#: no second spelling of them. `sale_installment_engine` stamps `gate_kind` on the ledger row and
#: `gate_source` beside it; both travel to the payload and are READ here.
GATE_ACTIVATION_PAYMENT = "activation_payment"
GATE_MA_RESIDUAL = "ma_residual"
GATE_MANUAL_PROMOTE = "manual_promote"
GATE_SOURCE_MA_TX = "ma_tx"

#: The producer's own grain words for the comparison's two row lists, dereferenced from
#: `dlar_vs_platform` so "which rows is a rep row" is its answer and not a second copy.
GRAIN_REP = _dvp.GRAIN_REP
GRAIN_STORE = _dvp.GRAIN_STORE

#: The producer's own word for "this side does not carry this metric", dereferenced for the same
#: reason — the comparison's absence vocabulary has one home and it is not this file.
STATE_NOT_REPORTED = _dvp.STATE_NOT_REPORTED

#: The activation BASIS value that means the platform side was replaced out of the uploaded
#: Activation Details report rather than read off the sales union. The producer states it on the
#: payload (`activation_source.basis`); this is the value it states, read and not re-decided.
BASIS_ACTIVATION_DETAILS = "activation_details"


# ── NOTES — what each panel is, in the words the screen already uses ────────────────────────────

_XC_ROW_NOTE = (
    "One row per installment month of one activation, exactly as the table below lists them. These "
    "rows cover the WHOLE pay period: the store and rep pickers on this page filter the tables "
    "beneath and do not move the figure above, so this panel describes everything the period "
    "contains rather than the selection you are viewing.")

_XC_EARNED_NOTE = _XC_ROW_NOTE + (
    " Earned is what actually paid, and each row names the thing that PROVED it: the subscriber "
    "report, the master agent's statement, the payment collected at the activation itself, or — on a "
    "month the schedule does not gate — the schedule alone. A month whose proof never arrived is a "
    "row here paying nothing, which is the whole point of reading it.")

_XC_EXPECTED_NOTE = _XC_ROW_NOTE + (
    " Expected is NOT a payment and is added into nobody's pay. It is what the multi-month ladder "
    "somebody saved says this month would pay, worked out against the sale — so every row names that "
    "ladder and no carrier report is involved in the figure at all. Months outside the configured "
    "window are shown beside the counted ones and deliberately not counted.")

_XC_UNEARNED_NOTE = _XC_ROW_NOTE + (
    " This counts the in-window months whose proof has NOT arrived, at what the ladder says each "
    "would pay. Every month that HAS been proved is shown beside them and not counted — which months "
    "are not in the figure is most of what makes it readable. The subtraction is done per month, on "
    "the row, which is why it opens at all.")

_XC_PROMOTED_NOTE = _XC_ROW_NOTE + (
    " Each of these was moved into Earned BY A PERSON, because the carrier's report was late or "
    "wrong, and the row's evidence is that approval and not a statement — which is why it opens "
    "separately from the figure beside it. Every other month is shown here and not counted. The "
    "amount paid is always TODAY'S expected figure, never the one approved at the time; where the "
    "two differ this report says so above.")

_MA_ROW_NOTE = (
    "One row per processor account, exactly as the By-store table below lists them — under the "
    "store's own name where this platform can resolve the account to one, and under the account "
    "otherwise, never a guessed store. These rows are the whole of what you are viewing: the account "
    "and rep pickers on this page are applied by the server before anything is folded, so this panel "
    "and the tile above it are one fold and cannot describe different accounts.")

_MA_MONEY_NOTE = _MA_ROW_NOTE + (
    " This figure is the master agent's own commission statement and nothing else. An account that "
    "statement carries no row for is shown here WITHOUT a figure and not counted — it is on the table "
    "below because the airtime feed named it, and reporting $0.00 of commission for it would be a "
    "number nobody measured. The month-by-month leg split printed beneath this report is a "
    "re-partition of this same figure and adds nothing to it.")

_MA_COUNT_NOTE = _MA_ROW_NOTE + (
    " This counts the lines on the commission statement, one per activation it paid on. An account "
    "the statement carries no row for is shown without a figure rather than as an account that "
    "activated nobody.")

_MA_AIRTIME_NOTE = _MA_ROW_NOTE + (
    " Airtime margin comes out of the DAILY TRANSACTIONS and not out of the commission statement, "
    "which is why it opens separately from the money figures beside it — and it is not part of the "
    "payable: adding the two together is what the By-store table's last column does, and no tile on "
    "this page does it. A $0.00 here may be an account that sold no top-ups OR an account this "
    "period's transactions never covered; the report records no order count per account, so the two "
    "cannot be told apart and the figure is counted exactly as the report counted it.")

_REC_ROW_NOTE = (
    "One row per device, over EVERY bucket — not only the one the table below is filtered to, "
    "because the four figures above are each over the whole ledger and the table shows one of them "
    "at a time. Only this bucket's devices are counted; the rest are shown beside them with their "
    "own state, because which devices are NOT in the figure is most of what makes it readable.")

_REC_SNAPSHOT_NOTE = (
    " Every figure here was read out of the LAST REBUILD of this ledger, so the trail stops at that "
    "run: press Rebuild and these numbers change. The run did not record which appeal row it read "
    "each owed amount off, so that half of the trail is not available to name. Where the run found "
    "proof that the line later paid or activated, the row names the report that proof came out of.")

_REC_MONEY_NOTE = _REC_ROW_NOTE + _REC_SNAPSHOT_NOTE

_REC_COUNT_NOTE = _REC_ROW_NOTE + (
    " This one counts devices rather than dollars, so a device the run recorded no amount for still "
    "counts here while contributing nothing to the money figure beside it."
) + _REC_SNAPSHOT_NOTE

_DVP_ROW_NOTE = (
    "One row per entity compared — and this figure counts BOTH the per-rep rows and the per-store "
    "rows, while the table below shows one of the two at a time. The same activations are therefore "
    "compared twice: once against the carrier's rep report and once against its store report. A "
    "'cell' here is one metric on one entity, not a sale. The filters and the only-differences "
    "checkbox below apply to the table and do not move this figure.")

_DVP_SOURCE_NOTE = (
    " Each row names the carrier's own report at that entity's grain, beside whichever basis produced "
    "the platform side. An entity the carrier's report never mentioned, and an entity our own "
    "transactions never mentioned, name only the side that answered — the other side was not read "
    "out of anything, which is not the same as its reporting zero.")

_DVP_COMPARED_NOTE = _DVP_ROW_NOTE + (
    " Counted here is every metric BOTH sides carry a figure for. A metric only one side carries is "
    "not compared at all and is shown as a dash on the table rather than as a zero."
) + _DVP_SOURCE_NOTE

_DVP_DIFFERING_NOTE = _DVP_ROW_NOTE + (
    " Counted here is every compared metric the two sides disagree on, whatever the reason. This "
    "figure is on no tile of its own: it is served so that both sides of the Agree count above can "
    "be opened, because that one is a subtraction this page does rather than a fold of any rows."
) + _DVP_SOURCE_NOTE

_DVP_UNDECIDABLE_NOTE = _DVP_ROW_NOTE + (
    " Counted here is every disagreement the two sides were NOT on equal footing for — our stored "
    "slice of the carrier's report does not reach the end of the period, or the carrier publishes "
    "several rows for this one person. Neither is a counting dispute and neither is a finding: "
    "re-pulling the report is what settles them."
) + _DVP_SOURCE_NOTE

_DVP_UNATTRIBUTED_NOTE = _DVP_ROW_NOTE + (
    " Counted here is every disagreement where the slice IS complete and nothing explains the gap. "
    "This is the output of the whole report — the cells worth chasing."
) + _DVP_SOURCE_NOTE


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  SHARED READERS. Accessors and names only — nothing here adds, judges or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded column. The producer folded every row's share, so there
    is nothing here to add up."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _label_of(*fields):
    """PURE: the row exactly as the table below prints it, from the first field that carries
    anything. That fallback ladder is the PAGE'S own — an MA row prints the resolved store, then the
    account name, then the account id — so the panel and the table name the same row the same way."""
    def _read(r):
        for f in fields:
            v = str((r or {}).get(f) or "").strip()
            if v:
                return v
        return None
    return _read


def _counts_one(field, value):
    """PURE: the predicate for WHICH rows a bucketed figure counts — the rows whose own state field
    carries this value. Every other row stays in the panel as the context that makes the figure
    readable, which is the difference between a drill-down and a filtered list."""
    def _in(r):
        return str((r or {}).get(field) or "") == str(value)
    return _in


def _one_if(field, value):
    """PURE: 1 on a row this bucket counts and NO figure on any other, so a count panel's tie-out is
    a real comparison instead of a sum over rows that contribute nothing."""
    def _read(r):
        return 1 if str((r or {}).get(field) or "") == str(value) else None
    return _read


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  EXPECTED vs EARNED — three grains on one installment month, separated only by provenance.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: WHAT THE READER SEES ON EACH TILE — the page's own wording, in the panel's words.
XC_LABELS = {
    "earned": "Earned this period",
    "expected_in_window": "Expected across the months in the window",
    "expected_not_yet_earned": "Expected and not yet earned",
    "promoted_amount": "Moved into earned by a person",
}


def _xc_fields(r):
    """PURE: what else a reader needs beside an installment figure on this row."""
    r = r or {}
    return {"store": r.get("store") or None, "month": r.get("month_index"),
            "sold in": r.get("sale_period") or None,
            "device / plan": r.get("label") or None,
            "status": r.get("status") or None,
            "proved": ("yes" if r.get("gate_met") else "no"),
            "moved by": r.get("promoted_by") or None}


def _xc_label(r):
    """PURE: the row as the table lets a reader find it again — who, which month, which device."""
    r = r or {}
    parts = [str(r.get("rep") or "").strip(),
             ("M%s" % r.get("month_index")) if r.get("month_index") is not None else "",
             str(r.get("label") or r.get("imei") or r.get("mdn") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def earned_gate_tables(row):
    """PURE: WHICH RELATION PROVED THIS MONTH, through the PRODUCER'S OWN GATE.

    `sale_installment_engine` decides the paid gate per carrier from config and then STAMPS what it
    used onto the ledger row — `gate_kind`, and `gate_source` beside it for the master-agent paths.
    So "which side answered" is a fact the row already carries, and this reads it rather than
    deciding it a second time: a second gate here would be the sibling derivation the index rules
    forbid, and a carrier branch into the bargain (RULE TWO).

    A month-1 `activation_payment` gate was proved by the SALE ITSELF, and the engine's sale read
    does not record which of the two sales relations answered (defect 1) — so that row reports an
    origin it cannot trace rather than naming one on a guess. A month the schedule does not gate at
    all was released by the LADDER, which is a declaration and nameable.
    """
    row = row or {}
    kind = str(row.get("gate_kind") or "")
    if row.get("promoted") or kind == GATE_MANUAL_PROMOTE:
        return (INSTALLMENT_PROMOTE,)
    if kind == GATE_ACTIVATION_PAYMENT:
        return (UNNAMEABLE,)
    if kind == GATE_MA_RESIDUAL:
        if str(row.get("gate_source") or "") == GATE_SOURCE_MA_TX:
            return (MA_COMMISSION, MA_DAILY_TX)
        return (MA_COMMISSION,)
    return (MI_RESIDUAL,)


def _xc_promoted(r):
    """PURE: did a person move this month into earned? The producer's own flag, read."""
    return bool((r or {}).get("promoted"))


def _xc_promoted_amount(r):
    """PURE: a promoted month's earning, and NO figure on any other row — the figure counts only the
    months somebody approved, and every other month is shown beside them."""
    return (r or {}).get("earned") if _xc_promoted(r) else None


def _xc_in_window(r):
    return bool((r or {}).get("expected_in_window"))


def _xc_unearned_in_figure(r):
    """PURE: the producer's OWN predicate for `expected_not_yet_earned` — in the window and the gate
    not met. Deliberately NOT `expected - earned`: that is the report's per-row column, and reading
    it here would make the panel a second opinion about which months the tile counts."""
    return _xc_in_window(r) and not bool((r or {}).get("gate_met"))


def _xc_expected(r):
    """PURE: the expected figure, and NO figure at all on a month outside the window — the report
    shows a blank for those and the tile does not contain them."""
    return (r or {}).get("expected") if _xc_in_window(r) else None


def expected_catalogue():
    """PURE: the four Expected-vs-Earned panels — THREE grains on one installment month.

    `earned` and `expected` share the row and the unit and are separated ONLY by provenance
    (§CORE-17.3n): what the carrier PROVED against what the ladder DECLARES. `promoted_amount` is a
    third provenance on the same row — a person's approval — and is the one figure in this family
    whose evidence is not a feed at all.
    """
    common = {"row_label": _xc_label, "keys": ("trans_id", "mdn", "month_index"),
              "fields": _xc_fields, "schema": SCHEMA,
              "unit": report_drilldown.UNIT_MONEY}
    ladder = dict(common, tables=(INSTALLMENT_SCHEDULE,), column="flat_amount")
    return (
        {"key": "earned", "total": "earned", "label": XC_LABELS["earned"],
         "value": _column("earned"), "column": "amount", "tables": earned_gate_tables,
         "note": _XC_EARNED_NOTE, **common},
        {"key": "expected_in_window", "total": "expected_in_window",
         "label": XC_LABELS["expected_in_window"], "value": _xc_expected,
         "in_figure": _xc_in_window, "note": _XC_EXPECTED_NOTE, **ladder},
        {"key": "expected_not_yet_earned", "total": "expected_not_yet_earned",
         "label": XC_LABELS["expected_not_yet_earned"], "value": _xc_expected,
         "in_figure": _xc_unearned_in_figure, "note": _XC_UNEARNED_NOTE, **ladder},
        {"key": "promoted_amount", "total": "promoted_amount",
         "label": XC_LABELS["promoted_amount"], "value": _xc_promoted_amount,
         "in_figure": _xc_promoted, "column": "expected_at_promote",
         "tables": (INSTALLMENT_PROMOTE,), "note": _XC_PROMOTED_NOTE, **common},
    )


def expected_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/expected-commission/{period}`.

    `payload` is the endpoint's own response, and every figure is its `totals` entry dereferenced —
    so a panel can never be a second opinion about the tile above it.

    A VACUOUS TIE IS REFUSED. On a period whose window contains no month at all, every row has no
    expected figure and the producer's $0.00 would tie against zero counted rows and read as a
    measurement; the same for the promoted figure on a period nobody promoted anything in. Those
    panels are not served in that case.

    A period with no installment rows drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    totals = dict(payload.get("totals") or {})
    if not rows or not totals:
        return {}
    for key, read in (("expected_in_window", _xc_expected),
                      ("expected_not_yet_earned", _xc_expected),
                      ("promoted_amount", _xc_promoted_amount)):
        if key in totals and not any(read(r) is not None for r in rows):
            totals.pop(key)
    if "expected_not_yet_earned" in totals and not any(_xc_unearned_in_figure(r) for r in rows):
        totals.pop("expected_not_yet_earned")
    return report_drilldown.tiles(
        expected_catalogue(), rows,
        {k: v for k, v in totals.items() if k in XC_LABELS},
        cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE MASTER-AGENT ROLL-UP — three grains on one processor account, two of them one unit apart
#     and two of them one FEED apart.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The money tiles, with the `by_store` column each is Σ over and the statement column it was read
#: out of. Declared as a table rather than three near-identical dicts so a fourth cannot be added
#: without saying which column holds it.
MA_MONEY_COLUMNS = (
    ("total_payable", "payable", "spiff_m1", "Commission the statement says is payable"),
    ("rebates", "rebates", "rebate", "Rebates on the statement"),
    ("spiffs_total", "spiffs", "spiff_m1", "Month 1-6 spiffs on the statement"),
)


def _ma_label(r):
    return _label_of("store", "name", "account_id")(r)


def _ma_fields(r):
    r = r or {}
    return {"processor account": r.get("account_id") or None,
            "store": r.get("store") or None,
            "activations on the statement": r.get("activations")}


def statement_covers(r):
    """PURE: does the COMMISSION STATEMENT carry a row for this account at all?

    `router.ma_commission_summary` builds its by-store bag from the commission statement AND from
    the airtime transactions, and an account reached only by the second is initialised with zero
    activations and zero money (defect 4). The producer's own signal is the activation count: it is
    incremented once per statement line, so a zero there means the statement named this account
    nowhere. A $0.00 of commission for such an account is a fabrication, not a measurement.
    """
    return int((r or {}).get("activations") or 0) > 0


def _ma_money(field):
    """PURE: the statement figure, or None on an account the statement does not carry."""
    def _read(r):
        return (r or {}).get(field) if statement_covers(r) else None
    return _read


def _ma_statement_tables(r):
    """PURE: the commission statement on an account it carries, and NO table on one it does not —
    the zero shown for such a row was read out of nothing."""
    return (MA_COMMISSION,) if statement_covers(r) else ()


def _ma_reason(r):
    return None if statement_covers(r) else REASON_NO_COMMISSION_ROWS


def _ma_activations(r):
    return (r or {}).get("activations") if statement_covers(r) else None


def ma_money_catalogue():
    """PURE: the three commission-money panels — ONE grain, three figures out of one statement."""
    common = {"row_label": _ma_label, "keys": ("account_id",), "fields": _ma_fields,
              "schema": SCHEMA, "unit": report_drilldown.UNIT_MONEY,
              "tables": _ma_statement_tables, "reason": _ma_reason, "note": _MA_MONEY_NOTE}
    return tuple(
        {"key": key, "total": key, "label": label, "value": _ma_money(col), "column": column,
         **common}
        for key, col, column, label in MA_MONEY_COLUMNS)


def ma_count_catalogue():
    """PURE: the activation count — the same account row in a different UNIT, therefore its own
    grain (§CORE-17.3l)."""
    return (
        {"key": "activations", "total": "activations",
         "label": "Activations the statement paid on", "value": _ma_activations,
         "column": "activation_type", "row_label": _ma_label, "keys": ("account_id",),
         "fields": _ma_fields, "schema": SCHEMA, "unit": report_drilldown.UNIT_COUNT,
         "tables": _ma_statement_tables, "reason": _ma_reason, "note": _MA_COUNT_NOTE},
    )


def ma_airtime_catalogue():
    """PURE: the airtime margin — the same row in the SAME unit as the money panels, separated ONLY
    by which feed it came out of (§CORE-17.3n), and not part of the payable beside it."""
    return (
        {"key": "margin", "total": "margin", "label": "Airtime margin",
         "value": _column("airtime_margin"), "column": "merchant_discount",
         "row_label": _ma_label, "keys": ("account_id",), "fields": _ma_fields,
         "schema": SCHEMA, "unit": report_drilldown.UNIT_MONEY,
         "tables": (MA_DAILY_TX,), "note": _MA_AIRTIME_NOTE},
    )


def ma_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/ma-commission/summary`, keyed `total_payable`,
    `rebates`, `spiffs_total`, `activations` and `margin`.

    `payload` is the endpoint's own response. Each figure is dereferenced out of the block the page
    reads it from — `total_payable` at the top level, the two component figures out of `components`,
    the count out of `activations` and the margin out of `airtime` — so a panel can never be a second
    opinion about the tile above it.

    A VACUOUS TIE IS REFUSED for the airtime margin: on a period whose daily transactions never
    landed, the producer's margin is $0.00 over rows that are all zero by initialisation, which ties
    against nothing and reads as a measurement. The producer's own order count is what says so.

    A period with no accounts, or one the producer answered `ready: False` for, drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("by_store") or ()) if r]
    if not payload.get("ready") or not rows:
        return {}
    comps = dict(payload.get("components") or {})
    money_totals = {"total_payable": payload.get("total_payable"),
                    "rebates": comps.get("rebates"), "spiffs_total": comps.get("spiffs_total")}
    money_totals = {k: v for k, v in money_totals.items() if v is not None}
    if not any(statement_covers(r) for r in rows):
        money_totals = {}
    cap = cap or report_drilldown.ROW_CAP
    out = {}
    out.update(report_drilldown.tiles(ma_money_catalogue(), rows, money_totals, cap=cap))
    counts = {"activations": (payload.get("activations") or {}).get("total")}
    if counts["activations"] is None or not any(statement_covers(r) for r in rows):
        counts = {}
    out.update(report_drilldown.tiles(ma_count_catalogue(), rows, counts, cap=cap))
    airtime = dict(payload.get("airtime") or {})
    if not int(airtime.get("orders") or 0):
        airtime = {}
    out.update(report_drilldown.tiles(
        ma_airtime_catalogue(), rows,
        {k: v for k, v in airtime.items() if k == "margin"}, cap=cap))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  THE DENIED-APPEAL RECOVERY LEDGER — two grains on one stored snapshot row, four figures each.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The four buckets the report prints, each with the word the reader sees. Keyed by the PRODUCER's
#: own status value, so a fifth bucket is a build failure here rather than a tile with no panel.
RECOVERY_BUCKETS = {
    "recoverable": "Commission the carrier still owes",
    "expired": "Commission the claw-back window closed on",
    "not_recoverable": "Devices nothing proved",
    "needs_data": "Devices nothing could be checked against",
}

#: WHICH RELATION THE RUN RECORDED AS ITS PROOF, by the `evidence.source` value it wrote. The
#: recovery engine's own two words, mapped to the two relations they name — and a source the map does
#: not know resolves to nothing rather than putting an arbitrary identifier on an owner's screen,
#: exactly as the sales-union resolver does.
RECOVERY_EVIDENCE_FEEDS = {
    "epay_payment": _lineage.PAYMENT_DETAIL,
    "epay_mi": MI_RESIDUAL,
}


def recovery_evidence_tables(row):
    """PURE: the stored run ALWAYS, plus the relation it recorded its proof out of where it recorded
    one. A row nothing proved names the run alone — which is the honest answer: the figure really was
    read out of the snapshot, and there is no second relation behind it."""
    src = str(((row or {}).get("evidence") or {}).get("source") or "")
    feed = RECOVERY_EVIDENCE_FEEDS.get(src)
    return (RECOVERY_LEDGER, feed) if feed else (RECOVERY_LEDGER,)


def _rec_label(r):
    """PURE: the device as the table lets a reader find it — which store, which handset, which
    serial."""
    r = r or {}
    parts = [str(r.get("store") or "").strip(), str(r.get("device_model") or "").strip(),
             str(r.get("imei") or r.get("mdn") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def _rec_fields(r):
    r = r or {}
    ev = r.get("evidence") or {}
    return {"state": r.get("status") or None, "denied": r.get("denied_date") or None,
            "why it was denied": r.get("category") or None,
            "mobile #": r.get("mdn") or None,
            "proof": (str(ev.get("type") or "") or None),
            "proof dated": ev.get("date") or None}


def _rec_owed(status):
    """PURE: this bucket's owed dollars on a row in it, and NO figure on any other — every other
    device is shown beside them with its own state."""
    def _read(r):
        return (r or {}).get("owed_amount") if str((r or {}).get("status") or "") == status else None
    return _read


def recovery_money_catalogue():
    """PURE: the four owed-commission panels — one grain, four figures over one row list."""
    common = {"row_label": _rec_label, "keys": ("imei", "mdn", "denied_date"),
              "fields": _rec_fields, "schema": SCHEMA, "unit": report_drilldown.UNIT_MONEY,
              "tables": recovery_evidence_tables, "column": "owed_amount",
              "note": _REC_MONEY_NOTE}
    return tuple(
        {"key": "owed." + status, "total": "owed." + status, "label": label,
         "value": _rec_owed(status), "in_figure": _counts_one("status", status), **common}
        for status, label in sorted(RECOVERY_BUCKETS.items()))


def recovery_count_catalogue():
    """PURE: the four device counts — the same snapshot row in a different UNIT, therefore its own
    grain (§CORE-17.3l)."""
    common = {"row_label": _rec_label, "keys": ("imei", "mdn", "denied_date"),
              "fields": _rec_fields, "schema": SCHEMA, "unit": report_drilldown.UNIT_COUNT,
              "tables": recovery_evidence_tables, "column": "status",
              "note": _REC_COUNT_NOTE}
    return tuple(
        {"key": "devices." + status, "total": "devices." + status, "label": label,
         "value": _one_if("status", status), "in_figure": _counts_one("status", status), **common}
        for status, label in sorted(RECOVERY_BUCKETS.items()))


def recovery_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /recovery/ledger`, keyed `owed.<bucket>` and `devices.<bucket>`.

    `payload` is the endpoint's own response, with `rows` holding the WHOLE ledger — which is the
    population the four tiles are folded over, and deliberately not the one bucket the table below
    is filtered to. Those are the same rows the endpoint serves the moment a reader clicks the tile,
    and this endpoint applies no scope narrower than the organisation (defect 11), so the panel
    discloses nothing the report does not.

    A bucket nothing fell into is served NO PANEL: its figure is a $0.00 over zero counted rows,
    which ties trivially and would read as a measurement. An empty ledger drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    buckets = dict(payload.get("buckets") or {})
    if not rows or not buckets:
        return {}
    money, counts = {}, {}
    for status in RECOVERY_BUCKETS:
        b = buckets.get(status)
        if not b or not int(b.get("count") or 0):
            continue
        money["owed." + status] = b.get("owed")
        counts["devices." + status] = b.get("count")
    cap = cap or report_drilldown.ROW_CAP
    out = {}
    out.update(report_drilldown.tiles(recovery_money_catalogue(), rows, money, cap=cap))
    out.update(report_drilldown.tiles(recovery_count_catalogue(), rows, counts, cap=cap))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  FEED vs STORE TRANSACTIONS — one entity comparison row, four counts, and two subtractions
#     this declaration refuses.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The four comparison counts this report folds, with the per-entity column each is Σ over and the
#: note the reader sees. `cells_differing` is on NO TILE and is served anyway, so that both sides of
#: the browser's `Agree` subtraction can be opened (§CORE-17.3k).
DVP_COUNT_COLUMNS = (
    ("cells_compared", "compared", "Metric cells both sides carry", _DVP_COMPARED_NOTE),
    ("cells_differing", "differing", "Metric cells the two sides disagree on", _DVP_DIFFERING_NOTE),
    ("cells_undecidable", "undecidable", "Disagreements the two sides were not on equal footing for",
     _DVP_UNDECIDABLE_NOTE),
    ("cells_unattributed", "unattributed", "Disagreements nothing explains", _DVP_UNATTRIBUTED_NOTE),
)

#: WHICH OF THE CARRIER'S TWO REPORTS ANSWERS AT WHICH GRAIN. The producer's own grain words, mapped
#: to the two registry constants — so a third grain is a build failure rather than a panel naming the
#: wrong report.
DVP_FEED_BY_GRAIN = {GRAIN_REP: DLAR_REP, GRAIN_STORE: DLAR_STORE}


def platform_tables(activation_source, sales_meta):
    """PURE: WHICH RELATION THE PLATFORM SIDE WAS READ OUT OF, through the PRODUCER'S OWN GATE.

    The platform side is Executive MTD's own activation cells, and `_apply_activation_basis` is the
    ONE place that decides what fills them: by default the sales display union's own rows, but for an
    org that has STATED Activation Details as its activation source of truth those counts are
    REPLACED out of the uploaded custom report. The endpoint already serves that decision as
    `activation_source`, so this reads it rather than deciding it a second time — a second gate here
    would be the sibling derivation the index rules forbid.

    On the sales basis the tables come from the read's OWN meta through the one home for that
    question; nothing resolvable arrives as `()` and the panels then report an origin they could not
    trace rather than naming a feed on a guess.
    """
    src = activation_source if isinstance(activation_source, dict) else {}
    if src.get("active") and str(src.get("basis") or "") == BASIS_ACTIVATION_DETAILS:
        return (CUSTOM_CAPTURE,)
    return tuple(sales_union_tables(sales_meta))


def _dvp_label(r):
    return _label_of("label", "key")(r)


def _dvp_fields(r):
    r = r or {}
    v = r.get("vintage") or {}
    return {"grain": r.get("grain") or None, "store": r.get("store") or None,
            "metrics compared": r.get("compared"),
            "metrics the carrier does not report": r.get("not_reported_feed"),
            "metrics our transactions cannot speak to": r.get("not_reported_platform"),
            "carrier report as of": v.get("as_of") or None}


def _dvp_feed_present(r):
    """PURE: did the carrier's own report carry a row for this entity? The producer's own flag."""
    return bool((r or {}).get("feed_row_present"))


def _dvp_platform_present(r):
    """PURE: did our transactions produce a side for this entity at all?

    `router.get_dlar_vs_platform` passes `platform=None` for an entity present ONLY in the carrier's
    feed, and `compare` then marks every metric `not_reported` on the platform side. So the
    producer's own signal is that every metric came back not-reported there — read off the metric
    rows it built, never re-derived.
    """
    mets = [m for m in ((r or {}).get("metrics") or ()) if m]
    if not mets:
        return False
    return any(str(m.get("platform_state") or "") != STATE_NOT_REPORTED for m in mets)


def _dvp_tables(platform):
    """PURE: the carrier's report at THIS row's grain, beside whichever basis produced the platform
    side — and only the side that actually answered. An entity one side never mentioned was not read
    out of a relation on that side at all (§CORE-17.3i)."""
    plat = tuple(platform or ())

    def _read(r):
        out = []
        if _dvp_feed_present(r):
            feed = DVP_FEED_BY_GRAIN.get(str((r or {}).get("grain") or ""))
            if feed:
                out.append(feed)
        if _dvp_platform_present(r):
            out.extend(plat)
        return tuple(out)
    return _read


def comparison_catalogue(platform):
    """PURE: the four comparison-count panels — ONE grain over a row list that is the per-rep rows
    and the per-store rows together, which the notes state rather than hide (defect 10)."""
    common = {"row_label": _dvp_label, "keys": ("grain", "key"), "fields": _dvp_fields,
              "schema": SCHEMA, "unit": report_drilldown.UNIT_COUNT,
              "tables": _dvp_tables(platform), "column": "metric"}
    return tuple(
        {"key": key, "total": key, "label": label, "value": _column(col), "note": note, **common}
        for key, col, label, note in DVP_COUNT_COLUMNS)


def comparison_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/dlar-vs-platform/{period}`.

    `payload` is the endpoint's own response — including the `sales_source` meta this batch had to
    start collecting because the read's provenance was being discarded (defect 3), and the
    `activation_source` the producer already served. Both are READ; neither is re-decided.

    THE ROW LIST IS THE TWO TABLES TOGETHER, because that is what the figures are Σ over. The page
    shows one of them at a time, which is a REPORTED defect and is said in every note rather than
    worked around: narrowing the panel to one grain would make it explain a figure half its size.

    The `Agree` and `Attributed` tiles get no panel — the browser subtracts them at the total — and
    all four of the figures they are made of are here, which is the whole of the answer a reader
    needs. The `entities*` counts get none either: they mix the two grains.
    """
    payload = payload or {}
    rows = [r for r in list(payload.get("reps") or ()) + list(payload.get("stores") or ()) if r]
    summary = dict(payload.get("summary") or {})
    if not rows or not summary:
        return {}
    plat = platform_tables(payload.get("activation_source"), payload.get("sales_source"))
    served = {c[0] for c in DVP_COUNT_COLUMNS}
    return report_drilldown.tiles(
        comparison_catalogue(plat), rows,
        {k: v for k, v in summary.items() if k in served},
        cap=cap or report_drilldown.ROW_CAP)
