"""WHERE DID THIS EXECUTIVE-MONTH FIGURE COME FROM — the declaration for the four EXECUTIVE-MONTH
reports (§CORE-17, batch 17).

  /commcalc/exec            Owner Overview — the month's headline commission tiles and the store
                            leaderboard they were folded from.
  /commcalc/exec/mtd        Executive MTD — the month-to-date location and employee sales report,
                            twenty figures on a pinned TOTAL row.
  /commcalc/month-focus     This Month's Focus — what the month is about, and how many stores carry
                            a target a manager actually saved.
  /commcalc/comp-trend      Total Compensation — the commission paid per rep month over month, and
                            the account-level carrier-comp trend beside it.

Chosen as one family because all four answer one owner question in four parts: *how is the month
going, against what, and is anybody being paid for it.* **These are the surfaces the owner looks at
most**, which is why a figure that cannot explain itself here is the most expensive kind.

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED OFF THE PRODUCERS, NOT ASSUMED (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────────────────────
Reading the four producers (`router.exec_overview` over `router.rep_coaching`, `router._exec_mtd`,
`router._month_focus_targets` and `comp_trend.compute_rep_pay_trend` / `.compute_residual_trend`)
gives **ELEVEN** grains in **FIVE** row shapes, because a grain fixes the ROW, the UNIT **and** the
PROVENANCE:

   1. a LEADERBOARD STORE's COMMISSION MONEY       money, out of the CALCULATED rep-commission
                                                   snapshot, the recorded chargeback and one further
                                                   record this family cannot name (defect 1). TWO
                                                   figures on one grain — what was paid and what was
                                                   left on the table — because both are the same
                                                   fold of the same store rows differing only in
                                                   which column holds the figure.
   2. a LEADERBOARD STORE's COMMISSION AT RISK     the SAME store row in the SAME unit, and a
                                                   different provenance: it is derived from the
                                                   snapshot's own subtotal and tier and NOTHING
                                                   else. Its own grain for exactly that reason
                                                   (§CORE-17.3n) — and not a cosmetic separation,
                                                   because "at risk" is money the snapshot can
                                                   price and the chargebacks beside it are money
                                                   somebody already took.
   3. a LEADERBOARD STORE's CHARGEBACK DEDUCTION   the same row, the same unit, and a provenance
                                                   this platform CANNOT NAME: see defect 1. Every
                                                   row of this panel reports a stated, countable
                                                   untraceable origin (§CORE-17.3i), which is the
                                                   honest answer and the finding.
   4. a LEADERBOARD STORE's REP COUNT              the same row and the same provenance as grain 2
                                                   in a different UNIT, which makes it its own
                                                   grain (§CORE-17.3l).
   5. an MTD STORE row's MONEY                     money, out of the sales display union as THE READ
                                                   ITSELF reports it. NINE figures on one grain.
   6. an MTD STORE row's UNITS                     the same row and the same provenance in a
                                                   different unit — ELEVEN figures. Two of those
                                                   eleven and two of grain 5's nine are PROJECTIONS
                                                   rather than measurements, and the notes say so.
   7. an MTD EMPLOYEE row's MONEY                  a different ROW LIST, therefore a different
   8. an MTD EMPLOYEE row's UNITS                  grain and a different catalogue — a panel over
                                                   the store rows opened from the employee tab would
                                                   be a panel about rows its report is not showing.
   9. a PERSON's MONTH AT ONE STORE                money, out of the CALCULATED rep-commission
                                                   snapshot (§CORE-17.3j). TWO figures — the latest
                                                   month and the whole window — one fold, two
                                                   columns. The ROW is an ATTRIBUTION POINT and not
                                                   a person, because that is the grain the SCREEN
                                                   itself filters at: a store selection narrows the
                                                   MONTHS within each person rather than dropping
                                                   whole people, so a panel keyed on the person
                                                   could not be narrowed to what the reader is
                                                   looking at. Measured off the producer, not
                                                   assumed (§CORE-17.3l).
  10. a ROSTER STORE's EXISTENCE                   count, out of the STORE ROSTER. The answer to
                                                   "which stores is the 22 in '14 of 22'".
  11. a ROSTER STORE's SAVED TARGET                the SAME roster row in the SAME unit, separated
                                                   ONLY by provenance (§CORE-17.3n): this one is
                                                   read out of the target settings, and a store that
                                                   has no saved row carries NO figure and names NO
                                                   table at all.

**Grains 10 and 11 are the same store row in the same unit and are still two grains.** "How many
stores do we have" is answered by the roster; "how many of them has a manager set a goal for" is
answered by the target settings, and a store missing from the second is not missing from the first.
Pooling them would make the panel for the second one claim the roster as its source for a store the
settings never mentioned — which is the §CORE-17.3m error in provenance form.

A FIGURE READ OUT OF A STORED MONTH NAMES THE SNAPSHOT (§CORE-17.3j)
────────────────────────────────────────────────────────────────────
Grains 1, 2, 4 and 9 are read out of `rep_commissions` — what the last Run Calculation WROTE DOWN,
not the sales and statement rows that calculation read. An exec dashboard reading a stored month is
exactly the §CORE-17.3j shape, and the trail stops at the snapshot: the name is dereferenced from
`carrier_pay_drilldown.REP_COMMISSIONS`, which is the one home batch 11 gave it, so this family
restates nothing.

ONE MODULE, FIVE CATALOGUES, AND NONE OF THEM WRITTEN TWICE
───────────────────────────────────────────────────────────
A catalogue is per ROW LIST, so the leaderboard's stores, the MTD report's two tabs, the rep-pay
rows and the roster's stores are five catalogues. The MTD report's two are GENERATED from one spec
by the label key the producer rolls up under, so its twenty figures are declared once and not twice
— two hand-written copies of one column list is precisely the drift the index rules forbid.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED PER PAGE BY READING THE PAGE
──────────────────────────────────────────────────────────────────────────────────────────────
Three of these four screens print figures the SERVER folded and must NOT have their panels narrowed:

  · `/commcalc/exec` — the page carries no filter control at all. There is nothing to narrow.
  · `/commcalc/exec/mtd` — every filter on it (stores, markets, employees, the date range) is a
    QUERY PARAMETER, so the server folds again and the panel follows the TOTAL row by construction.
    `DataGrid` sorts and hides columns; it renders `totalRow` exactly as served and reduces nothing.
  · `/commcalc/month-focus` — no client-side filter exists.

The FOURTH is the §CORE-17.3d class and is narrowed:

  · `/commcalc/comp-trend`, the BY-REP view — its filter bar is applied in the BROWSER (the standard
    bar narrows each rep's per-month attribution points and re-sums them), and both tiles are a
    client `reduce` over the surviving rep rows. So its two panels ARE narrowed through
    `lib/drill-narrow`, and each tile is read OFF its narrowed panel rather than reduced a second
    time, which is what makes the tile and its evidence one fold.

Both decisions are LOCKED, in both directions, by `harness_batch17_drilldown.py` §F — with a
non-vacuous witness for each scan, so "the text is absent" can never be confused with "the scan is
blind".

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **THE THREE P&L TILES ON THE OWNER OVERVIEW.** Revenue, gross profit and net income are three
    columns of ONE stored statement row, and this report folds nothing per store under them — batch
    10's leaf ruling, and the same shape as the open lending week's two figures in batch 16. The
    page already links the P&L, which carries its own drill (ledger state `own`).
  · **"OPEN CHARGEBACKS" AND "REPS BELOW TIER".** Neither is folded per store by this report, so
    there are no rows behind either to open. The first is also a REPORTED defect (defect 2).
  · **`Conv.` AND `APB` ON THE MTD TOTAL ROW.** Two ratios, computed at the TOTAL from two other
    totals — §CORE-17.3h, exactly. Both of the totals each one divides DO drill, which is the whole
    of the answer a reader needs.
  · **EVERY FIGURE ON THE BY-ACCOUNT VIEW OF THE COMPENSATION TREND.** Four refusals with four
    different reasons, and this is the one place in the batch where a whole view gets nothing:
      – `Total comp` and `Residual (MI+ATU)` are one column of ONE served month row. The report
        folds nothing per ACCOUNT for a month, so there are no rows behind them on this report.
      – `Δ vs prior month` is one month row minus another, which is a difference taken at the TOTAL
        and not folded per row (§CORE-17.3k) — and `% vs prev` is its quotient (§CORE-17.3h).
      – `Active accounts` is a count over a set the report never enumerates.
      – `Comp lost to dips` is added ENTIRELY IN THE BROWSER over rows the browser filtered; the
        producer folds no such total, so there is no served figure for a panel to explain. REPORTED
        (defect 7) rather than invented here: a declaration that summed it would be computing a
        business figure, which this layer does not do.
  · **"REPS PAID" AND "ACCOUNTS VANISHED".** Counts whose LIST IS ON SCREEN — the table below each
    one IS the list it counts. No key at all, never an empty panel.
  · **"TOP REP".** A name, not a figure.
  · **THE MONTH-FOCUS SPIFF MARGIN, THE INITIATIVE DOLLARS AND THE RECONCILIATION LISTS.** The
    margin is a per-UNIT rate (§CORE-17.3h); the initiative dollars are a picker option whose fold
    is §60's own `pay_type_options` and which no figure on the page is composed from; and the three
    reconciliation lines are NAME LISTS, which are their own evidence.
  · **"WHAT THIS MONTH STILL NEEDS" AND "WHAT THE NUMBERS SUGGEST".** A work queue and a set of
    rules each carrying its own measured figure already — §CORE-17.3i's population, not a summary.

ABSENCE IS NEVER ZERO
─────────────────────
A store with no saved target is SHOWN and NOT counted, in the producer's own words
(`target_not_set`), because an unset target is not a target of zero and a store without one is not a
store failing it (§CORE-17.3m — load-bearing on an MTD screen, which is attainment against targets).
The figures do not move by a cent: the producer counted such a store in `stores_total` and not in
`with_saved_target`, and this declaration counts it exactly the same way — only the ROW changes,
which is the entire argument for putting it there.

AN EMPTY REPORT DRILLS TO NOTHING. A report that produced its totals over NO ROWS AT ALL would still
get a panel asserting a figure behind an empty list, which ties only by accident and reads like a
bug. Every entry point below returns an empty mapping in that case.

LIVE DEFECTS THESE FOUR REPORTS HAVE TODAY — NONE FIXED, EIGHT REPORTED, NO FIGURE MOVED
────────────────────────────────────────────────────────────────────────────────────────
 1. **The chargeback deduction on the Owner Overview comes out of a relation this repository does
    not create.** `rep_coaching` reads `commcalc.chargeback_items` for the per-rep deduction, and NO
    migration here creates that table — so it is not a registered relation, cannot have a
    `sourceLabels.ts` entry (the infra lock would fail), and naming it on a panel would put a raw
    database name on the owner's screen. Three of the five Owner-Overview figures therefore carry an
    origin that CANNOT BE TRACED, stated and counted rather than omitted (§CORE-17.3i). This is the
    `account/statement_drilldown.UNREGISTERED_FEED_LINES` finding, one report along, and the remedy
    is the same: a migration that creates or formally registers the table, which is a schema change
    and not something a drill-down PR does. REPORTED.
 2. **"Open chargebacks" on the Owner Overview is not scoped to the month the page is showing.** It
    counts every open row in the review bucket for the org, whatever period it belongs to, and sits
    in a strip whose every other figure is that month's. So the tile does not change when the owner
    changes months. NOT fixed: narrowing it would move a figure. REPORTED, with a regression check.
 3. **An MTD row's key is a store SPELLING, not the store.** `_exec_mtd` rolls up by the CANONICAL
    store key and then labels the row with the first-seen RAW spelling, discarding the canonical key
    — so the only identifier a panel can offer is one of possibly several spellings of that store.
    The figures are correct (the collapse happened before the fold); what is lost is the ability to
    address the row. REPORTED.
 4. **The Owner Overview's own comment says its tiles are month-grained because the P&L is, and the
    P&L tiles silently read a DIFFERENT month when the caller uses the dashed period spelling** —
    the endpoint's docstring records this as fixed by `_period_or_400`, and it is, for the commission
    tiles. Stated here only so a reader of the panels knows the three tiles WITHOUT panels are the
    ones that were affected. No action.
 5. **The by-rep compensation trend has no window total of its own.** `compute_rep_pay_trend` folds
    a per-MONTH total and a per-REP total and no grand total, so the page's "Total — last N mo" is a
    browser sum. The panel's figure is therefore the sum of the months the report itself served
    (`report_drilldown.money` over served subtotals, the Liabilities + Equity precedent) — which is
    a REAL tie-out, because the figure is folded BY MONTH and the rows are folded BY REP: a dropped
    rep or a per-rep rounding drift breaks it. The producer's own silence is REPORTED.
 6. **`compute_residual_trend` accepts a `market` parameter and ignores it entirely.** It is
    threaded from the endpoint, echoed back in `params`, and read by nothing — so a caller filtering
    by market gets the whole company and a response that says the filter was applied. The page does
    not send it, which is the only reason this is not already wrong on screen. REPORTED.
 7. **On the by-account view two tiles move under the store filter and four do not.** "Comp lost to
    dips" and "Accounts vanished" are browser folds over the filtered dip rows; the four month tiles
    beside them are the server's own last-month row and are not. So one strip holds both
    §CORE-17.3o classes at once, and the month click-filter has the same split. NOT fixed: the two
    honest repairs are to re-fold the month tiles (which moves a figure) or to make the filters query
    parameters (which is a different report). REPORTED.
 8. **A rep the snapshot names with neither a storeops name nor a carrier username is folded into
    one row labelled "(unknown)".** `compute_rep_pay_trend` keys on the first of the two that is
    present and falls back to that literal, so two such reps are one row in the table and one row in
    every panel. It names the snapshot, which is true. REPORTED rather than tidied away.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it, and the one addition it performs (defect 5) adds subtotals the report itself served.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3q.
"""
from app.modules.commcalc import carrier_pay_drilldown as _cpd
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import exec_metric_defs as _emd
from app.modules.commcalc import processor_money_drilldown as _pmd
from app.modules.commcalc import targets_drilldown as _tdd
from app.modules.core import report_drilldown

# ── THE SIX PROVENANCE ANSWERS THIS FAMILY HAS ───────────────────────────────────────────────────

#: The CALCULATED rep-commission snapshot — what the last Run Calculation wrote down. DEREFERENCED
#: from `carrier_pay_drilldown`, which named it once for exactly this purpose (§CORE-17.3j, batch
#: 11); naming it again here would be the copied fact the registry exists to prevent.
REP_COMMISSIONS = _cpd.REP_COMMISSIONS

#: The RECORDED PENALTY a commission deduction is taken through. The registry's own constant, which
#: the two closing declarations now dereference as well (they each held a literal copy).
OPS_CHARGEBACK = _lineage.OPS_CHARGEBACK

#: The STORE ROSTER a count of stores is read out of. Lives in a DIFFERENT schema from everything
#: else on this family, which is why the panel that names it carries its schema explicitly.
STORE_ROSTER = _lineage.STORE_ROSTER

#: The TARGET SETTINGS row a manager saves a monthly goal into. DEREFERENCED from the targets
#: family's own declaration, which named it once (§CORE-17.3m, batch 13).
TARGETS = _tdd.TARGETS

#: THE FIFTH ANSWER IS NOT A CONSTANT, AND THAT IS THE POINT. Which tables the sales display union
#: ACTUALLY read for a period is a fact the READ knows and records, so the MTD report's provenance is
#: resolved off its own `source` meta through the ONE home for that question —
#: `processor_money_drilldown.sales_union_tables`, which batch 14 wrote for exactly this.
sales_union_tables = _pmd.sales_union_tables

#: THE SIXTH ANSWER IS NO ANSWER AT ALL, and it is REPORTED rather than guessed (defect 1). The
#: legacy chargeback record `rep_coaching` reads is created by no migration in this repository, so it
#: is not a registered relation and cannot be given a plain name without putting a database
#: identifier on the owner's screen. A figure that depends on it names what it CAN and carries this
#: in place of the rest, which `report_drilldown.panel` counts under `untraceable_origins`.
UNNAMEABLE = None

#: Every relation on this family except the roster lives in the commcalc schema.
SCHEMA = "commcalc"
SCHEMA_STOREOPS = "storeops"

#: WHY A ROW ON THIS FAMILY CARRIES NO NUMBER. The targets family's own key, reused rather than
#: re-spelled — an unset target is not a target of zero (§CORE-17.3m).
REASON_TARGET_NOT_SET = "target_not_set"

# ── THE EXECUTIVE-MTD COLUMN VOCABULARY, DEREFERENCED WHERE IT EXISTS ────────────────────────────
#
# WHICH OF THE MTD COLUMNS ARE A CONFIGURED BUCKET'S OWN MEASURE. The five line buckets are config
# (`exec_metric_config`, mig 962) with the built-in vocabulary in ONE home, and each one DECLARES the
# basis it is measured on. So the UNIT of those five columns is not an opinion this file holds — it
# is read off the bucket's own `basis`, and `harness_batch17_drilldown.py` §A fails the build if the
# two drift apart or if a sixth line bucket appears without a column here.
BUCKET_COLUMN = {
    "phones": "total_phones",
    "bill_payment": "bill_payment_qty",
    "accessory": "acc_sales",
    "activation_fee": "activation_fee",
    "protect": "total_protect",
}

#: The two bases a bucket can declare, as the units this mechanism already has words for. Not a third
#: spelling of the unit vocabulary: `report_drilldown.UNIT_*` is the home and this maps onto it.
UNIT_OF_BASIS = {
    "count": report_drilldown.UNIT_COUNT,
    "ext_price": report_drilldown.UNIT_MONEY,
}


def bucket_unit(bucket):
    """PURE: the unit a configured line bucket's own measure is in, off its declared basis."""
    basis = str(((_emd.CODE_DEFAULTS.get(bucket) or {}).get("basis") or "")).strip()
    return UNIT_OF_BASIS.get(basis)


#: THE COLUMNS NO BUCKET DECLARES A BASIS FOR, with their unit stated and the reason it is stated
#: here rather than derived. Four classes:
#:   · the ACTIVATION SPLIT — its one predicate is `line_class.activation_class` now, not a bucket
#:     basis, so there is no basis to read (the `activation` bucket's token lists are the legacy
#:     home and the coverage detector already skips it);
#:   · `amount` — the bill-payment bucket's basis is `count`, and this is the DOLLARS the same bucket
#:     also accumulates, so deriving its unit from that basis would be wrong;
#:   · the SET-UP FEE family — one accumulator the accessory classifier splits out, with no bucket of
#:     its own (owner directive 2026-07-17: a separate pay item, never folded into accessory$);
#:   · the TRENDING columns — a projection of one of the above, in that column's unit.
_COUNT_COLUMNS = (
    "total_activation", "activation", "port", "byod", "tablet", "watch", "home_internet", "edge",
    "upgrade", "trending_box",
)
_MONEY_COLUMNS = (
    "amount", "acc_sales", "trending_acc_sales", "setup_fee", "setup_fee_dealer_share",
    "setup_fee_employee_pay", "acc_plus_setup", "trending_acc_plus_setup", "activation_fee",
)

#: THE TWO COLUMNS THAT CAN BE None ON EVERY ROW — the tenant's set-up-fee economics, derived from a
#: percentage nobody has to state. Named so the entry point can refuse a total served over rows none
#: of which carry a number (see `mtd_drilldowns`).
ECONOMICS_COLUMNS = ("setup_fee_dealer_share", "setup_fee_employee_pay")

#: THE COLUMNS THAT ARE A PROJECTION AND NOT A MEASUREMENT. A reader is told so on the panel, because
#: a projected figure whose rows are real sales reads like a measurement otherwise.
_PROJECTED = ("trending_box", "trending_acc_sales", "trending_acc_plus_setup")

#: WHAT THE READER SEES ON EACH MTD FIGURE. The grid's own column meanings, in the panel's words —
#: never the carrier-relabelled header, because `report_labels` relabels those under a carrier lens
#: and the shared panel is not behind that lens (the batch-16 posture for the financing vendor).
MTD_LABELS = {
    "total_activation": "Activations of every kind",
    "activation": "New activations",
    "port": "Ported-in activations",
    "byod": "Bring-your-own-device activations",
    "tablet": "Tablet activations",
    "watch": "Connected-device activations",
    "home_internet": "Home-internet activations",
    "edge": "Device-financing activations",
    "upgrade": "Upgrades",
    "total_phones": "Handsets sold",
    "trending_box": "Activations projected to a full month",
    "bill_payment_qty": "Bill payments taken",
    "amount": "Bill payments collected",
    "acc_sales": "Accessory sales",
    "trending_acc_sales": "Accessory sales projected to a full month",
    "setup_fee": "Device set-up fee collected",
    "setup_fee_dealer_share": "The dealer's share of the set-up fee",
    "setup_fee_employee_pay": "The employee's share of the set-up fee",
    "acc_plus_setup": "Accessory sales plus the set-up fee",
    "trending_acc_plus_setup": "Accessory sales plus the set-up fee, projected to a full month",
    "activation_fee": "Activation fee collected",
    "total_protect": "Protection plans sold",
}


# ── NOTES — what each panel is, in the words the screen already uses ────────────────────────────

_SNAPSHOT_NOTE = (
    "Each figure here was read out of what the last commission calculation WROTE DOWN for this "
    "month, not out of the sales and statement rows that calculation read — so the trail stops at "
    "the stored run. Re-running the calculation is what changes these numbers.")

_PAID_NOTE = (
    "One row per store, as the leaderboard below lists them. Each figure is that store's share of "
    "the tile, folded from its own people's rows. " + _SNAPSHOT_NOTE + " Part of what makes up these "
    "two figures is a deduction whose own record has NO plain name in this platform yet, so every "
    "row names what it can and reports the rest as an origin that cannot be traced.")

_AT_RISK_NOTE = (
    "One row per store. This figure is worked out from the stored run ALONE — each person's pay "
    "subtotal against the share of it their scorecard earned — and no chargeback record is involved "
    "in it, which is why it opens separately from the money somebody has already taken. "
    + _SNAPSHOT_NOTE + " A person whose subtotal the run never itemised contributes nothing to it "
    "rather than a zero, and the store's figure is light by an unknown amount instead of wrong.")

_CHARGEBACK_NOTE = (
    "One row per store. These rows are the evidence for the figure and they name NO source: the "
    "record this deduction is read out of has no plain name in the platform, so where it came from "
    "cannot honestly be stated. That is reported on every row rather than filled in with a guess.")

_REPS_NOTE = (
    "One row per store, counting the people the stored run priced this month — so a person the run "
    "has not been executed for is in no row here, and this count is the run's reach rather than the "
    "payroll's. " + _SNAPSHOT_NOTE)

_MTD_STORE_NOTE = (
    "One row per store, exactly as the table below groups them, and each figure is that store's own "
    "cell of the pinned TOTAL row. These rows cover the whole selection the report was asked for — "
    "the filter bar, the employee picker and the date range are all applied by the server before "
    "anything is folded, so this panel and the total above it are one fold and cannot describe "
    "different months.")

_MTD_EMP_NOTE = (
    "One row per employee, exactly as the table below groups them, and each figure is that person's "
    "own cell of the pinned TOTAL row. The same rows the location tab folds, grouped the other way — "
    "which is why this is a panel of its own and not the store panel re-labelled.")

_MTD_SPELLING_NOTE = (
    " The report collapses a store's spelling variants into one row before folding, then labels the "
    "row with the first spelling it saw, so the name on a row addresses the store under ONE of its "
    "spellings rather than all of them.")

_PROJECTED_NOTE = (
    " THIS FIGURE IS A PROJECTION, NOT A MEASUREMENT. Each row's number is that row's measured "
    "month-to-date scaled to a full month by the same factor the header states, so the rows are real "
    "and the figure is an estimate built from them.")

_REP_PAY_NOTE = (
    "One row per person per month per store — the window's own attribution points, which is the "
    "grain the table and its filter bar work at, so a person who moved between stores reads as one "
    "row for each month they worked at each. The figure is the commission this business pays them, "
    "never the compensation a carrier pays us. " + _SNAPSHOT_NOTE + " These rows are the selection "
    "you are viewing.")

_REP_WINDOW_NOTE = (
    " The figure itself is the total of the MONTHS this report served, while the rows are folded per "
    "person, month and store — two folds of the same stored run, which is what makes the check "
    "below a real one rather than a sum compared against itself.")

_REP_LATEST_NOTE = (
    " Only the newest month's rows are COUNTED in this figure; every earlier month is shown beside "
    "them with its own number, because which months are NOT in it is most of what makes it "
    "readable.")

_ROSTER_NOTE = (
    "One row per store on the roster, which is the list the count is over — a store is counted here "
    "because it is set up, whatever it has or has not done this month.")

_SAVED_TARGET_NOTE = (
    "One row per store on the roster. A store is COUNTED only where a manager has actually saved a "
    "target for this month, and it names the settings those targets are saved in. A store with no "
    "saved row is shown WITHOUT a figure and names NO source at all: nobody has set it a goal, which "
    "is not a goal of zero and not a store failing one. The targets screen seeds a store with no row "
    "from last month, so a store can display a figure nobody assigned — this counts saved rows.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  THE OWNER OVERVIEW — four grains on one leaderboard store row.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _store_label(r):
    """PURE: the store exactly as the leaderboard prints it."""
    return str((r or {}).get("store") or "") or None


def _store_fields(r):
    """PURE: what else a reader needs beside a leaderboard figure on this row."""
    r = r or {}
    return {"market": r.get("market"), "people": r.get("reps"), "flags": r.get("flags")}


def _column_value(field):
    """PURE: a reader for one already-folded column. The producer has folded every store's share, so
    there is nothing here to add up."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def exec_catalogue():
    """PURE: the Owner Overview's panels — five figures over four grains on the store rows.

    The three P&L tiles, the open-chargeback count and the reps-below-tier count get no key at all;
    see the refusals in the header.
    """
    common = {"row_label": _store_label, "keys": ("store",), "fields": _store_fields,
              "schema": SCHEMA}
    #: Paid and left-on-the-table are ONE grain: the same fold of the same rows through the same three
    #: relations, differing only in which column holds the figure.
    money_mix = dict(common, unit=report_drilldown.UNIT_MONEY,
                     tables=(REP_COMMISSIONS, OPS_CHARGEBACK, UNNAMEABLE), note=_PAID_NOTE)
    return (
        {"key": "commissions_paid", "total": "commissions_paid", "label": "Commission paid",
         "value": _column_value("paid"), "column": "paid", **money_mix},
        {"key": "money_on_table", "total": "money_on_table", "label": "Money left on the table",
         "value": _column_value("on_table"), "column": "on_table", **money_mix},
        {"key": "commission_at_risk", "total": "commission_at_risk", "label": "Commission at risk",
         "value": _column_value("at_risk"), "column": "at_risk",
         "unit": report_drilldown.UNIT_MONEY, "tables": (REP_COMMISSIONS,),
         "note": _AT_RISK_NOTE, **common},
        {"key": "chargebacks_deducted", "total": "chargebacks_deducted",
         "label": "Chargebacks deducted", "value": _column_value("chargebacks"),
         "column": "chargebacks", "unit": report_drilldown.UNIT_MONEY, "tables": (UNNAMEABLE,),
         "note": _CHARGEBACK_NOTE, **common},
        {"key": "reps", "total": "reps", "label": "People paid",
         "value": _column_value("reps"), "column": "reps",
         "unit": report_drilldown.UNIT_COUNT, "tables": (REP_COMMISSIONS,),
         "note": _REPS_NOTE, **common},
    )


def exec_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/exec-overview/{period}`.

    `payload` is the endpoint's own response, and every figure is its `tiles` entry dereferenced — so
    a panel can never be a second opinion about the tile above it. A month with no calculated
    commission has no store rows and drills to nothing.
    """
    rows = [r for r in ((payload or {}).get("stores") or ()) if r]
    totals = dict((payload or {}).get("tiles") or {})
    if not rows or not totals:
        return {}
    return report_drilldown.tiles(exec_catalogue(), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  EXECUTIVE MTD — twenty figures on a pinned TOTAL row, over two row lists.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _mtd_label(label_key):
    """PURE: the row as the grid's pinned first column prints it."""
    def _read(r):
        return str((r or {}).get(label_key) or "") or None
    return _read


def _mtd_fields(r):
    """PURE: the two figures a reader wants beside any MTD figure, whichever one they opened — how
    many activations the row had and how much accessory revenue, which between them say whether a
    row is small because it sold little or small because it sold differently."""
    r = r or {}
    return {"activations": r.get("total_activation"), "accessory $": r.get("acc_sales")}


def mtd_unit(column):
    """PURE: the unit an MTD column is in.

    Read off the CONFIGURED BUCKET's own declared basis where the column IS that bucket's measure,
    and stated explicitly for the four classes of column no bucket declares a basis for (see
    `_COUNT_COLUMNS` / `_MONEY_COLUMNS` and the comment above them). Returns None for a column this
    declaration does not know, so a column added to the producer's total row without being declared
    here is a BUILD FAILURE rather than a tile with a guessed unit.
    """
    for bucket, col in BUCKET_COLUMN.items():
        if col == column:
            return bucket_unit(bucket)
    if column in _COUNT_COLUMNS:
        return report_drilldown.UNIT_COUNT
    if column in _MONEY_COLUMNS:
        return report_drilldown.UNIT_MONEY
    return None


def mtd_columns():
    """PURE: every MTD column this declaration opens, in a stable order.

    Deliberately NOT a copy of the producer's own total-row tuple: the harness reads that tuple out
    of the producer's source and fails the build if the two disagree, so there is one list and this
    is the declared half of it. `conv` and `apb` are absent on purpose (§CORE-17.3h).
    """
    return tuple(sorted(MTD_LABELS))


def mtd_catalogue(label_key, sales_tables):
    """PURE: one MTD tab's panels — every money and count figure on its pinned TOTAL row.

    `label_key` is the field the producer rolls the tab up under (`store` or `employee`), which is
    what makes this ONE declaration serve both tabs without two copies of the column list.
    `sales_tables` is what the display union ACTUALLY read for this period, resolved by the ONE home
    for that question off the read's own meta. Nothing resolvable arrives here as `()` and the panels
    then report an origin they could not trace, rather than naming a feed on a guess.
    """
    base_note = _MTD_STORE_NOTE if label_key == "store" else _MTD_EMP_NOTE
    if label_key == "store":
        base_note += _MTD_SPELLING_NOTE
    common = {"row_label": _mtd_label(label_key), "keys": (label_key,), "fields": _mtd_fields,
              "schema": SCHEMA, "tables": tuple(sales_tables or ())}
    out = []
    for col in mtd_columns():
        unit = mtd_unit(col)
        if unit is None:
            continue
        note = base_note + (_PROJECTED_NOTE if col in _PROJECTED else "")
        out.append({"key": col, "total": col, "label": MTD_LABELS[col],
                    "value": _column_value(col), "column": col, "unit": unit,
                    "note": note, **common})
    return tuple(out)


def mtd_drilldowns(payload, *, cap=None):
    """PURE: both tabs' panels for `GET /commcalc/exec-mtd/{period}`, keyed `<tab>.<column>`.

    `payload` is `router._exec_mtd`'s output as the endpoint serves it, including the `source` meta
    the sales read recorded — which is where the provenance comes from. A tab with no rows drills to
    nothing, and the two set-up-fee economics figures are not served at all until the tenant states
    the percentage, because the producer PRESERVES None for them rather than totalling an unstated
    percentage to $0.00 (§CORE-17.3m's posture, which `tiles` honours by skipping a None total).
    """
    payload = payload or {}
    tables = sales_union_tables((payload.get("source") or {}).get("meta")
                                if isinstance(payload.get("source"), dict) else None)
    out = {}
    for tab, label_key in (("by_location", "store"), ("by_employee", "employee")):
        side = payload.get(tab) or {}
        rows = [r for r in (side.get("rows") or ()) if r]
        totals = dict(side.get("total") or {})
        if not rows or not totals:
            continue
        # A $0.00 OVER ROWS THAT ALL CARRY NO FIGURE IS THE ABSENCE OF A FIGURE, NOT A FIGURE —
        # batch 16's posture for the paid legs of a report with no routing declared, applied to the
        # one pair of columns here that can be None on every row. The producer already serves None
        # for them when the percentage is unstated; this also refuses the panel where it serves a
        # total over rows none of which carry a number, which would otherwise tie by accident.
        for col in ECONOMICS_COLUMNS:
            if col in totals and not any(r.get(col) is not None for r in rows):
                totals.pop(col)
        for key, panel in report_drilldown.tiles(
                mtd_catalogue(label_key, tables), rows, totals,
                cap=cap or report_drilldown.ROW_CAP).items():
            out["%s.%s" % (tab, key)] = panel
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE COMPENSATION TREND, BY REP — one grain, two figures, over a client-narrowed row list.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _rep_label(r):
    """PURE: the point as the table below lets a reader find it — who, which month, which store."""
    r = r or {}
    parts = [str(r.get("rep") or "").strip(), str(r.get("period") or "").strip(),
             str(r.get("store") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def _rep_fields(r):
    """PURE: the market the window's own attribution point was stamped with, which is the other
    thing the screen filters on."""
    return {"market": (r or {}).get("market")}


def attribution_points(result):
    """PURE: the producer's own per-(person, month, store) ATTRIBUTION POINTS as one row list.

    `compute_rep_pay_trend` serves these on each person (`points`) precisely so a store or market
    selection narrows the MONTHS within a person rather than misattributing a floating rep's whole
    window to one store. That makes the POINT the grain the SCREEN folds at, so it is the grain the
    panels open to. This SHAPES what the producer already served — it reads nothing, sums nothing
    and decides nothing — and the person's name is carried onto each of their points so a row can
    say who it is about.
    """
    out = []
    for r in ((result or {}).get("reps") or ()):
        if not r:
            continue
        for p in (r.get("points") or ()):
            if not p:
                continue
            out.append({"rep": r.get("rep"), "period": p.get("period"), "store": p.get("store"),
                        "market": p.get("market"), "pay": p.get("pay")})
    return out


def _latest_month(result):
    """PURE: the newest month the report kept, or None — the producer's own `months`, read rather
    than re-derived, so the panel's figure is the month the table's last column is."""
    months = tuple((result or {}).get("months") or ())
    return months[-1] if months else None


def _in_month(month):
    """PURE: the predicate for WHICH POINTS the newest month's figure counts. Every other point is
    SHOWN beside them with its own number, because which months are NOT in the figure is most of
    what makes it readable — and that is the difference between a drill-down and a filtered list."""
    def _in(r):
        return str((r or {}).get("period") or "") == str(month)
    return _in


def window_total(result):
    """PURE: the total of the MONTHS this report served — defect 5.

    `compute_rep_pay_trend` folds a per-MONTH total and a per-PERSON total and NO window total, so
    the page's "Total — last N mo" tile is a browser sum with no served figure behind it. This adds
    the subtotals the report itself served, which is the one arithmetic this layer is allowed
    (`report_drilldown.money`'s own docstring: the Liabilities + Equity precedent). It is therefore
    NOT the same fold as the rows below it — those are folded per PERSON — which is exactly what
    makes the panel's check a real comparison rather than a tautology.
    """
    months = tuple((result or {}).get("totals_by_month") or ())
    if not months:
        return None
    return report_drilldown.money(
        sum(report_drilldown.money((m or {}).get("total_payout")) for m in months))


def rep_pay_catalogue(latest_month):
    """PURE: the by-rep view's two panels — the latest month's pay and the window's.

    One grain: the same attribution points, the same unit and the same single provenance, differing
    only in WHICH of them the figure counts. "Reps paid" and "Top rep" get no key at all; see the
    refusals.
    """
    common = {"row_label": _rep_label, "keys": ("rep", "period", "store"), "fields": _rep_fields,
              "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA,
              "tables": (REP_COMMISSIONS,), "value": _column_value("pay"),
              "column": "total_payout"}
    out = [{"key": "window", "total": "window", "label": "Commission paid over the window",
            "note": _REP_PAY_NOTE + _REP_WINDOW_NOTE, **common}]
    if latest_month:
        out.insert(0, {"key": "latest", "total": "latest",
                       "label": "Commission paid in the newest month",
                       "in_figure": _in_month(latest_month),
                       "note": _REP_PAY_NOTE + _REP_LATEST_NOTE, **common})
    return tuple(out)


def rep_pay_drilldowns(result, *, cap=None):
    """PURE: both panels for `GET /commcalc/comp/rep-pay-trend`.

    `result` is `comp_trend.compute_rep_pay_trend`'s output as the endpoint serves it. A window in
    which no commission has been calculated has no rep rows and drills to nothing.
    """
    rows = attribution_points(result)
    if not rows:
        return {}
    month = _latest_month(result)
    by_month = {str((m or {}).get("period") or ""): (m or {}).get("total_payout")
                for m in ((result or {}).get("totals_by_month") or ())}
    totals = {"window": window_total(result)}
    if month is not None and month in by_month:
        totals["latest"] = by_month[month]
    return report_drilldown.tiles(rep_pay_catalogue(month), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  THIS MONTH'S FOCUS — two grains on one roster store row, separated only by provenance.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def target_rows(store_codes, with_target):
    """PURE: the roster as ROWS, which is the one thing the month-focus payload does not carry.

    `_month_focus_targets` folds the roster and the saved-target rows into two COUNTS and a list of
    the stores missing one, and a drill-down needs the row list those counts were folded over. This
    SHAPES what the producer already read — it reads nothing, resolves nothing and decides nothing
    about any store: `has_target` is membership in the set the producer itself computed.
    """
    have = {str(c or "").strip().upper() for c in (with_target or ())} - {""}
    out = []
    for code in sorted({str(c or "").strip().upper() for c in (store_codes or ())} - {""}):
        out.append({"store_code": code, "has_target": code in have})
    return out


def _roster_one(_r):
    """PURE: a roster row counts once toward "how many stores do we have"."""
    return 1


def _target_one(r):
    """PURE: 1 where a manager SAVED a target for this store, and None where nobody did — an unset
    target is unmeasurable, never a target of zero (§CORE-17.3m)."""
    return 1 if (r or {}).get("has_target") else None


def _target_tables(r):
    """PURE: the target settings for a store that HAS a saved row, and NO table at all for one that
    does not — because the blank shown for such a store was read out of nothing."""
    return (TARGETS,) if (r or {}).get("has_target") else ()


def _target_reason(r):
    return None if (r or {}).get("has_target") else REASON_TARGET_NOT_SET


def _roster_label(r):
    """PURE: the store as the roster names it."""
    return str((r or {}).get("store_code") or "") or None


def _roster_fields(r):
    return {"target saved": ("yes" if (r or {}).get("has_target") else "no")}


def focus_catalogue():
    """PURE: the month-focus target panels — the roster's own count and the saved-target count.

    TWO grains on one row in one unit, separated ONLY by which relation answered (§CORE-17.3n): the
    roster says how many stores exist, the settings say how many carry a goal, and a store missing
    from the second is not missing from the first. Nothing else on that screen gets a key; see the
    refusals in the header.
    """
    common = {"row_label": _roster_label, "keys": ("store_code",), "fields": _roster_fields,
              "unit": report_drilldown.UNIT_COUNT}
    return (
        {"key": "stores_total", "total": "stores_total", "label": "Stores set up",
         "value": _roster_one, "tables": (STORE_ROSTER,), "schema": SCHEMA_STOREOPS,
         "note": _ROSTER_NOTE, **common},
        {"key": "with_saved_target", "total": "with_saved_target",
         "label": "Stores with a target somebody saved", "value": _target_one,
         "tables": _target_tables, "reason": _target_reason, "schema": SCHEMA,
         "note": _SAVED_TARGET_NOTE, **common},
    )


def focus_drilldowns(payload, rows, *, cap=None):
    """PURE: both target panels for `GET /commcalc/month-focus/{period}`.

    `payload` is the endpoint's own response and `rows` the roster rows `target_rows` shaped, so
    every figure is the payload's `targets` entry dereferenced. A read that FAILED returns
    `stores_total: None` from the producer — that is "we could not measure this", not "no store has a
    target" — and `tiles` serves no panel for a None total, which is the honest outcome.
    """
    rows = [r for r in (rows or ()) if r]
    totals = dict(((payload or {}).get("targets") or {}))
    if not rows or not totals:
        return {}
    return report_drilldown.tiles(focus_catalogue(), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)
