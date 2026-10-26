"""WHERE DID THIS FIGURE COME FROM — the declaration for the two screens that fold ONE MONTH of the
CALCULATED REP-INCENTIVE SNAPSHOT (§CORE-17, batch 22).

  /commcalc          CommCalc Dashboard — the payout headline and the activation counts above the
                     Top Earners list.
  /commcalc/reports  Rep Incentive Report — the header total and the Rep Breakdown footer.

ONE FAMILY, AND THE REASON IS NOT "THEY ARE BOTH ABOUT COMMISSION". Both screens read the SAME
endpoint (`GET /commcalc/commissions/{period}`), fold the SAME rep rows of the SAME stored month, and
print the SAME figure as their headline: Σ(`total_payout`) over the reps the caller is allowed to
see. The dashboard adds two activation counts over those same rows; the report adds a Subtotal
footer over them. There is exactly one row shape and exactly one provenance in the whole family —
which is why ONE catalogue serves both, and why two would have been the sibling derivation the index
rules forbid.

WHY THIS BATCH HAD TO ADD AN ENDPOINT RATHER THAN A `drilldown` KEY
──────────────────────────────────────────────────────────────────
`GET /commcalc/commissions/{period}` returns a **bare JSON LIST** of rep rows — there is no object
on it to hang a `drilldown` key off, and giving it one would change the shape every caller of that
read already depends on (the exports, the emailed report, the month range, the statement PDF, the
mobile client). So the panels are served by `GET /commcalc/commissions/{period}/drilldown`, which
calls the single-month handler **in process, with the caller's own authorization**, exactly as
`/commissions-range` already does. A panel therefore holds precisely the rows that caller was served
— after the SELF / span scope and after the rep-row allow-list (§6m) — and cannot disclose a row the
report would not.

WHERE THE FIGURES THEMSELVES COME FROM, AND WHY THIS MODULE SUMS NOTHING
───────────────────────────────────────────────────────────────────────
The endpoint has no producer that folds a month total, so the one that DOES was dereferenced instead
of a second one being written here: `rep_incentive_range.assemble`, the Rep Incentive month range's
own assembler, already folds exactly these fields for one month and does it through
`payout_slice_evidence.sum_slice` — the ONE home for "may this named slice be added up at all"
(§COMMCALC-09). Handing it a one-month window is the whole of the endpoint's arithmetic. This
declaration adds, judges and rounds nothing.

HOW MANY GRAINS THIS FAMILY HAS — MEASURED OFF THE PANELS, NOT ASSUMED (§CORE-17.3l)
───────────────────────────────────────────────────────────────────────────────────
A grain fixes the ROW, the UNIT **and** the PROVENANCE. Driving the real assembler over real rep rows
gives **TWO**, and `harness_batch22_drilldown.py` §I measures them off the panels as a SET compared
for EQUALITY so the number in the index is read rather than claimed:

  1. a REP ROW's PAY                 money, out of the CALCULATED rep-commission snapshot — what the
                                     last Run Calculation wrote down (§CORE-17.3j: the trail stops
                                     at the snapshot; what the run read to produce it is that run's
                                     business, not this panel's claim).
  2. a REP ROW's ACTIVATION COUNT    the SAME row and the SAME provenance in a different UNIT, and
                                     therefore its own grain.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **"TOTAL ACTIVATIONS" ON THE DASHBOARD** — `premium_acts + byod_acts`, added IN THE BROWSER from
    two of the report's own counts. A figure composed at the TOTAL from two other totals has no rows
    of its own (§CORE-17.3k), so the tile offers **both halves**, each with a real panel, and that is
    the whole content of the refusal.
  · **THE "INSTALLMENT" FOOTER ON THE REP BREAKDOWN** — the page's own `instOf` is
    `residual_installment_comm + carrier_statement_comm`, composed at the total again, and the second
    of those two columns is not one the one home sums at all (defect 2 below). Refused rather than
    served over a fold nobody runs.
  · **"REPS CALCULATED" AND THE TIER DISTRIBUTION** — a cardinality, not Σ(a field): `reps.length`
    counts the rows a panel's own header already states, and each tier bar is |rows in that tier|
    beside a WIDTH that is a share of them (§CORE-17.3h). The house settled this twice already — the
    Daily Closing dashboard's `days` tile ("a distinct count, not a sum") and `/commcalc/comp-trend`'s
    "Reps paid" ("the table below IS the list it counts").
  · **THE COMPENSATION-BY-LINE TAB** prints no total of any kind: every cell on it is one rep's own
    stored slice, and the slices are already shown as a dash with their reason where the run never
    itemised them. There is no folded figure on that tab for a panel to explain.
  · **THE INDIVIDUAL-REP TAB** is already a drill: `/commcalc/commission-explain` and
    `/commcalc/commission-drill` itemise one person's pay to the transactions, which is why
    `/commcalc/commission-ledger` and `/commcalc/daily-commission` sit on the ledger as `own`. This
    batch neither extends nor duplicates them.
  · **THE MONTH-RANGE TAB — NAMED, EXCUSED, AND THE CONVERGENCE STATED.** Its six per-month
    subtotals and six grand totals fold a **rep-MONTH** row of this same snapshot, which is the grain
    `exec_month_drilldown.rep_pay_catalogue` ALREADY serves for `/commcalc/comp-trend` (§CORE-17.3q).
    A second catalogue over the range payload would be two paths answering one question. They are not
    one call away either, because that catalogue is bound to `comp_trend`'s attribution-point shape
    (`rep` / `period` / `pay`) and the range payload carries `storeops_name` / `range_month` /
    `total_payout` — so converging them means reshaping the range payload, which is a PR of its own
    and is written down here rather than quietly half-done. REPORTED, not hidden: the ledger note for
    this route says the range tab is still owed.
  · **A FIGURE NO ROW CARRIES A NUMBER FOR**, and **a $0.00 or a 0 over zero counted rows**: no
    panel, through the one home's own `served_tiles` rule, because such a panel ties 0 against 0 and
    reads as a measurement. A NON-zero figure over no counted rows KEEPS its panel so the tie-out
    publishes the disagreement.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED PER PAGE BY READING THE PAGE
─────────────────────────────────────────────────────────────────────────────────────────────
This family splits ONE AND ONE, and the split was found by reading each page:

  · `/commcalc/reports` **folds its own tiles in the browser** over the rows its StandardFilterBar
    leaves on screen (`filterRows` by store / market / rep, then `filtered.reduce`), so every panel is
    **NARROWED** through `lib/drill-narrow` and each figure is READ OFF its narrowed panel through the
    shared `agreesToTheCent` gate.
  · `/commcalc` **is NOT narrowed, and it is the interesting case of the batch.** It reduces in the
    browser too — but it has **no filter at all**: the reduce runs over exactly the rows the server
    served, so narrowing would be the identity and `drill-narrow` must not appear in it. The call is
    not "does this page add anything up", it is "does this page fold a figure over a population the
    server did not" (§CORE-17.3u's ruling, read from the other side). `harness_batch22_drilldown.py`
    §G fails the build in BOTH directions off each page's own text.

THREE LIVE DEFECTS, ALL REPORTED, NONE FIXED, NO FIGURE MOVED. Each carries a regression check.

 1. **THE REP BREAKDOWN'S SUBTOTAL FOOTER ADDS A NEVER-COMPUTED SLICE IN AS ZERO.** The page prints
    each rep's Subtotal CELL as a dash where `payout_slice_basis` is not `recorded` (§COMMCALC-09 is
    wired per row), and then totals the column with `filtered.reduce((s, r) => s + (r.subtotal || 0))`
    — adding those same rows in at 0.00. That is the exact arithmetic `payout_slice_evidence.sum_slice`
    was written to refuse, and the month-range tab already uses the honest path. The panel served here
    is folded the HONEST way: an un-itemised rep is shown WITHOUT a figure, carrying the evidence
    module's own basis word, and is not counted. On a month where any rep is un-itemised the panel and
    the footer therefore DISAGREE, the shared gate withholds the drill link for that one figure, and
    the page keeps its own number — a disagreement costs the link, never the number. Reproduced in
    §D; no figure on either screen moved.
 2. **THE TWO TABS MEAN DIFFERENT THINGS BY "INSTALLMENT".** The Rep Breakdown footer adds
    `residual_installment_comm + carrier_statement_comm`; `rep_incentive_range.MONEY_FIELDS` sums
    `residual_installment_comm + installment_comm_sale` and never sums `carrier_statement_comm` at
    all. One screen's Installment column is not the other's, and nothing says so. REPORTED; the
    composed footer is refused a panel for that reason as well as §CORE-17.3k.
 3. **THE DASHBOARD READS THE SNAPSHOT TWICE.** The tiles come from `GET /commissions/{period}` and
    the panels from `…/drilldown`, so a Run Calculation landing between the two requests leaves the
    page holding a figure from one read and evidence from another. The shared `agreesToTheCent` gate
    makes that visible rather than silent — the link is withheld when the two disagree — and the real
    remedy is one read, which means giving that endpoint an object shape and is the change this module
    deliberately did not make (see above).

💰 MOVES NO MONEY. Read-only presentation of figures the one home already folded. Books nothing, pays
nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3v.
"""
from app.modules.commcalc import carrier_pay_drilldown as _cpd
from app.modules.commcalc import payout_slice_evidence as _pse
from app.modules.core import report_drilldown

#: THE CALCULATED REP-COMMISSION SNAPSHOT — what the last Run Calculation wrote down, and the ONE
#: provenance in this whole family. DEREFERENCED from `carrier_pay_drilldown`, which named it once
#: for exactly this purpose (batch 11), and which `exec_month_drilldown`, `targets_drilldown`,
#: `sales_margin_drilldown` and `performance_coaching_drilldown` all dereference as well. The index
#: settled in §CORE-17.3t's registry note that this name stays THERE and does not become a
#: `data_lineage_registry` constant, so naming it again here — in a registry OR in a literal — would
#: be the copied fact the one-home rule exists to prevent.
REP_COMMISSIONS = _cpd.REP_COMMISSIONS

SCHEMA = "commcalc"

#: WHY A ROW HERE CARRIES NO NUMBER. The ONE home for that verdict is `payout_slice_evidence`, and
#: its own basis word is the reason string — this module spells no sentence of its own about it, and
#: `report_drilldown.UNMEASURED_REASONS` already carries that word in exactly this sense.
REASON_NOT_RECORDED = _pse.BASIS_NOT_RECORDED

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule (§CORE-17.3u), bound by identity.
#: This module holds no copy of it.
_served = report_drilldown.served_tiles

#: THE NAMED SLICES this family prints a FOOTER for, read off the evidence module rather than listed
#: here: a column that is a named slice may not be added up for a row the run never itemised, and
#: which columns those are is not this file's fact.
NAMED_SLICES = _pse.NAMED_SLICES


# ── NOTES — what each panel is, in the words the screens already use ───────────────────────────

_ROW_NOTE = (
    "One row per person this month's calculation wrote down, exactly as the Rep Breakdown lists "
    "them, and only the people you are allowed to see — the panel is folded after the same scope "
    "the report applies, so it can never show you a row the report would not.")

_SNAPSHOT_NOTE = (
    " Every figure here was read out of what the last Run Calculation WROTE DOWN for this month. "
    "The trail stops at that stored month on purpose: what the run read to produce it is the run's "
    "own business, and the per-person drill-down that itemises it to the transactions is the "
    "magnifier beside each name.")

_PAYOUT_NOTE = _ROW_NOTE + _SNAPSHOT_NOTE + (
    " This is the payout BEFORE any chargeback or ops-accountability deduction, which is the figure "
    "both screens print.")

_SUBTOTAL_NOTE = _ROW_NOTE + _SNAPSHOT_NOTE + (
    " A person whose pay BREAKDOWN the run never itemised is shown here WITHOUT a figure and is not "
    "counted: their total is correct and their itemisation was never written, so adding them in at "
    "$0.00 would tell them they earned nothing. Those are the months to re-run, and the report "
    "names them.")

_COUNT_NOTE = _ROW_NOTE + _SNAPSHOT_NOTE + (
    " These are the activation counts the calculation stored against each person, not a fresh count "
    "of the sales feed — so a month that has not been recalculated since a late file landed shows "
    "what was calculated, which is what the people were paid on.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  READERS. Accessors and names only — nothing here adds, judges or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one stored column of one rep row."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _slice_column(field):
    """PURE: a reader for a NAMED SLICE — the evidence module's own answer, which is **None** where
    the run never itemised the row. Dereferenced, never re-derived: this file does not know which
    rows are evidence and must not learn."""
    def _read(r):
        return _pse.slice_value(r or {}, field)
    return _read


def _slice_reason(r):
    """PURE: the evidence module's own basis word on a row whose slices are not figures, and nothing
    on a row whose are."""
    return None if _pse.slices_are_evidence(r or {}) else REASON_NOT_RECORDED


def _rep_label(r):
    """PURE: the person exactly as both screens name them — the roster spelling when there is one,
    else the POS spelling. The pages' own fallback ladder, so the panel and the table name one person
    one way."""
    r = r or {}
    for f in ("storeops_name", "epay_salesperson"):
        v = str(r.get(f) or "").strip()
        if v:
            return v
    return None


def _rep_fields(r):
    """PURE: what else a reader needs beside one person's row."""
    r = r or {}
    return {"store": r.get("store") or None,
            "market": r.get("market") or None,
            "tier": r.get("tier"),
            "KPIs met": r.get("kpis_met"),
            "payout": r.get("total_payout"),
            "breakdown": (r.get(_pse.BASIS_FIELD) or None)}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE CATALOGUE — one rep row, money and counts, one provenance.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: THE PANEL KEYS, WHICH ARE THE ONE HOME'S FIELD NAMES. Both screens ask for a figure by the field
#: `rep_incentive_range` folds it under, so a field renamed in the assembler moves the tile and its
#: drill together instead of leaving a dead link.
PAYOUT_KEY = "total_payout"
SUBTOTAL_KEY = "subtotal"
PREMIUM_KEY = "premium_acts"
BYOD_KEY = "byod_acts"
UPGRADE_KEY = "upgrade_acts"

#: EVERY FIGURE THIS FAMILY SERVES, in the order the screens print them. Deliberately NOT every field
#: the assembler folds: a panel for a figure neither screen shows would be evidence for nothing.
SERVED_KEYS = (PAYOUT_KEY, SUBTOTAL_KEY, PREMIUM_KEY, BYOD_KEY, UPGRADE_KEY)

#: THE FIGURES BOTH SCREENS COMPOSE IN THE BROWSER AND THIS FAMILY REFUSES, each paired with the
#: served halves it offers instead (§CORE-17.3k). Named here so the pages can link to the halves
#: without either side spelling the pairing twice.
COMPOSED_REFUSALS = {
    "activations": (PREMIUM_KEY, BYOD_KEY),
    "installment": (),
}


def catalogue():
    """PURE: the five panels this family serves — ONE rep row, two units, one provenance.

    `total_payout` is a column every writer in every mode fills in, so it is read straight.
    `subtotal` is a NAMED SLICE and is read through the evidence module, so a row the run never
    itemised comes back blank with its reason rather than as a measured zero (§COMMCALC-09).
    """
    common = {"row_label": _rep_label, "keys": ("epay_salesperson", "store"),
              "fields": _rep_fields, "schema": SCHEMA, "tables": (REP_COMMISSIONS,)}
    money = dict(common, unit=report_drilldown.UNIT_MONEY)
    count = dict(common, unit=report_drilldown.UNIT_COUNT)
    return (
        dict(money, key=PAYOUT_KEY, total=PAYOUT_KEY, label="Total incentive payout",
             value=_column(PAYOUT_KEY), column=PAYOUT_KEY, note=_PAYOUT_NOTE),
        dict(money, key=SUBTOTAL_KEY, total=SUBTOTAL_KEY, label="Subtotal before the tier",
             value=_slice_column(SUBTOTAL_KEY), column=SUBTOTAL_KEY,
             reason=_slice_reason, note=_SUBTOTAL_NOTE),
        dict(count, key=PREMIUM_KEY, total=PREMIUM_KEY, label="Premium activations",
             value=_column(PREMIUM_KEY), column=PREMIUM_KEY, note=_COUNT_NOTE),
        dict(count, key=BYOD_KEY, total=BYOD_KEY, label="BYOD activations",
             value=_column(BYOD_KEY), column=BYOD_KEY, note=_COUNT_NOTE),
        dict(count, key=UPGRADE_KEY, total=UPGRADE_KEY, label="Upgrades",
             value=_column(UPGRADE_KEY), column=UPGRADE_KEY, note=_COUNT_NOTE),
    )


def month_drilldowns(rows, totals, *, cap=None):
    """PURE: every panel for ONE month of the Rep Incentive snapshot.

    `rows` are the rep rows the single-month read returned, UNCHANGED and already scoped. `totals` is
    `rep_incentive_range.assemble`'s own `month_totals` entry for that month — the ONE home for these
    sums, so a panel can never be a second opinion about the figure above it.

    A month with no calculated commission has no rep rows and drills to nothing.
    """
    rows = [r for r in (rows or ()) if r]
    totals = dict(totals or {})
    if not rows or not totals:
        return {}
    return dict(_served(catalogue(), rows, {k: v for k, v in totals.items() if k in SERVED_KEYS},
                        cap or report_drilldown.ROW_CAP))
