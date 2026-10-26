"""WHERE DID THIS FIGURE COME FROM — the declaration for **Subsidy Months Missing** (§CORE-17,
batch 22).

  /commcalc/mdf-entitlement-coverage   did every month of a store's ramp-up subsidy arrive, and
                                       which store-month did not?

ITS OWN FAMILY, ON PURPOSE. This report's figures are **counts of store-months**, and the money
beside them is not money that arrived — it is a declared monthly amount applied to a month that did
NOT. Nothing else in the platform folds that row: `pay_data_quality.statement_month_coverage` answers
DAY grain inside one month (§19.54), the carrier-owed family folds installment months of money the
carrier PROVED (§CORE-17.3s), and the two sales-outcome families fold retail lines. The index was
searched for a mechanism serving "which store-month inside a declared window never arrived" before
anything was written here, and `entitlement_coverage.entitlement_store_months` is already the ONE
home for it — this file only names where each of its figures was read.

THE OWNER RULING THIS BATCH DID NOT RE-LITIGATE. Which payments ARE the entitlement is the org's own
declaration through `payment_categories` (§57/§58), and MDF-declared carrier money books to its own
P&L line rather than to a reimbursement line. That is settled and this declaration neither reads nor
restates it: it names the two relations the report's read actually drew rows from, and the config row
the WINDOW was declared in. Nothing here classifies a payment.

HOW MANY GRAINS — MEASURED OFF THE PANELS, NOT ASSUMED (§CORE-17.3l / §CORE-17.3n)
─────────────────────────────────────────────────────────────────────────────────
A grain fixes the ROW, the UNIT **and** the PROVENANCE. One ROW SHAPE — a STORE-MONTH — and three
grains, because on this report **provenance alone** separates two figures in the same unit:

  1. a STORE-MONTH's VERDICT COUNT    count. A month that arrived names the relations the read
                                      answered out of; a month inside a DECLARED window that did
                                      not is on this report because somebody DECLARED the window,
                                      so it names the config row and, where other stores were paid
                                      that month, the feeds that prove the run happened; and a
                                      store-month nothing can be said about at all names NOTHING
                                      NAMEABLE and says so (§CORE-17.3i).
  2. a STORE-MONTH's RECEIVED MONEY   money, out of the relations the read answered out of. Money
                                      that actually arrived.
  3. a STORE-MONTH's PRICED ABSENCE   money again on the same row, separated ONLY BY PROVENANCE
                                      (§CORE-17.3n): the monthly amount a gap is valued at is either
                                      a number somebody TYPED INTO THE CONFIG — a declaration, not a
                                      measurement (§CORE-17.3m) — or, where none was typed, the
                                      store's OWN observed payments where they agree. A gap nothing
                                      can price is counted in the month total and carries NO figure,
                                      so that panel comes back explicitly not tied rather than
                                      pretending a priced total covered every month it counts.

ABSENCE IS NEVER ZERO, AND ON THIS REPORT THAT IS THE WHOLE SUBJECT. Every figure here is a statement
about a month that did NOT arrive, so the four shapes of "we cannot say" must each read as a blank
with a reason rather than a zero:

  · a store whose FIRST ENTITLED MONTH is not declared has no window, so its observed absences are
    `not_measured` with that reason — never twelve gaps against an invented window, which would be a
    fabricated accusation against the carrier;
  · a month in which NOTHING of this kind reached us for ANY store is our ingest, counted in its own
    tile and never netted into the gaps;
  · a store-month whose money may have landed under the SAME physical store's other spelling has its
    verdict withheld for that month only, and the twin is named on the row;
  · a gap NOTHING CAN PRICE is shown without a figure and still counted as a gap, because the month
    is missing whether or not we know what it was worth.

In every case the producer's own words travel on the row: this file spells no verdict and no reason
sentence of its own, and `entitlement_coverage`'s `ROW_STATUSES` / `ROW_REASONS` are dereferenced so
a status renamed in its one home moves the tile and its drill together.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **"STORES" AND "STORES WITH NO DECLARED FIRST MONTH"** — both are cardinalities of a STORE set,
    not Σ over the store-month rows this report folds. A panel for one would be a row list at a
    different grain from the figure it claimed to explain, which is the §4e defect this mechanism
    exists to prevent. The store-month counts they stand beside all drill.
  · **THE REPORT'S OWN TRI-STATE VERDICT SENTENCE** is a verdict, not a figure.
  · **THE "OTHER STORES PAID THAT MONTH" COLUMN** is a count of OTHER stores on each row — a
    cardinality carried as evidence, which every panel row already states in its fields.
  · **THE PER-STORE ROLL-UP** (`by_store`) prints no figure on this page: it fills the store picker.
    A panel for a figure nobody shows would be evidence for nothing.
  · **A $0.00 OR A 0 OVER ZERO COUNTED ROWS**: no panel, through the one home's own `served_tiles`
    rule. A NON-zero figure over no counted rows KEEPS its panel so the tie-out publishes the
    disagreement.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED BY READING THE PAGE
────────────────────────────────────────────────────────────────────────────────────
`/commcalc/mdf-entitlement-coverage` is **SERVER-FOLDED and NOT narrowed**. Its tiles read
`data.totals` as served — the page folds nothing — and its two controls split cleanly: the STORE
picker is a query parameter the server filters on before it folds, and the VERDICT multi-select
narrows the TABLE below and touches no tile. That is §CORE-17.3u's product-mix case exactly, so
`lib/drill-narrow` must not appear in this page; `harness_batch22_drilldown.py` §G fails the build in
both directions off the page's own text.

TWO LIVE DEFECTS, REPORTED, NEITHER FIXED, NO FIGURE MOVED. Each carries a regression check.

 1. **THE UNION OF THE TWO CARRIER FEEDS DOES NOT RECORD WHICH SIDE ANSWERED.** `_mdf_entitlement_run`
    reads both the per-line payment detail and the compensation statement and keeps the **MAX** per
    store-month, because the two are two reads of ONE payment and adding them would double every
    month both carry. That is right — but the union keeps no tag, so a received store-month can only
    name the relations the read drew rows from AT ALL, not the one this row came out of. The panel
    says so in words rather than picking one. The remedy is a tag on the union, which changes the
    report's own read and is not this batch's to make.
 2. **THIS REPORT APPLIES NO PER-STORE RBAC SCOPE.** `GET /commcalc/mdf-entitlement-coverage` takes
    `org_id` and an optional `store`, and passes no keyset: a store-scoped viewer who can reach the
    route sees every store in the estate. The panels disclose exactly what the report already does
    — they are folded over the payload the caller was served and reach for nothing — so this is
    REPORTED, not worked around, and the remedy is the scope. Same shape as batch 21's defect 1 on
    `/commcalc/tax-collected`.

💰 MOVES NO MONEY. Read-only presentation of figures the producer already folded. Books nothing, pays
nothing, writes nothing, and computes no business figure of its own.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3v.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import entitlement_coverage as _ec
from app.modules.core import report_drilldown

#: THE TWO CARRIER RELATIONS THIS REPORT'S READ DRAWS FROM, both DEREFERENCED from the lineage
#: registry — the one home for a feed's name. The report reads BOTH every time and keeps the greater
#: of the two per store-month, so which of them a given figure came out of is a fact the union does
#: not record (defect 1); what IS knowable is which of them answered at all, which is what
#: `feed_tables` below resolves off the run's own row counts.
PAYMENT_DETAIL = _lineage.PAYMENT_DETAIL
COMP_REPORT = _lineage.COMP_REPORT

#: WHERE THE WINDOW AND THE MONTHLY AMOUNT WERE DECLARED. A config row somebody typed, not a feed —
#: so it is NOT in the lineage registry, whose documented scope is external-feed ingest tables, and
#: putting a config row there would add it to the feed watchdog's population. Named here once, which
#: is the posture `targets_drilldown.TARGETS` and `carrier_pay_drilldown.REP_COMMISSIONS` already
#: take for exactly this distinction. The COLUMN is the producer's own constant, dereferenced.
ENTITLEMENT_DECLARATION = "commission_org_config"
DECLARATION_COLUMN = _ec.CONFIG_COLUMN

#: NOTHING NAMEABLE. A store-month read out of no relation at all — the shape §CORE-17.3i settled:
#: naming a table for it would be a provenance claim nothing checked, and the shared fold turns this
#: into a stated, countable untraceable origin.
UNNAMEABLE = None

SCHEMA = "commcalc"

#: THE PRODUCER'S OWN VOCABULARY, DEREFERENCED, NEVER RE-SPELLED. A status or a reason renamed in
#: `entitlement_coverage` moves the tile and its drill together instead of leaving a dead link.
RECEIVED = _ec.ROW_RECEIVED
MISSING = _ec.ROW_MISSING
UNCONFIRMED_MISSING = _ec.ROW_UNCONFIRMED_MISSING
NOT_DUE = _ec.ROW_NOT_DUE
NOT_MEASURED = _ec.ROW_NOT_MEASURED
REASON_WINDOW_NOT_DECLARED = _ec.REASON_WINDOW_NOT_DECLARED

#: WHY A ROW HERE CARRIES NO NUMBER. One shape only, and the vocabulary already holds the word in
#: exactly this sense: a gap nothing can price has no amount recorded anywhere for it.
REASON_UNPRICED = "amount_not_recorded"

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule (§CORE-17.3u), bound by identity.
_served = report_drilldown.served_tiles


# ── NOTES — what each panel is, in the words the screen already uses ───────────────────────────

_ROW_NOTE = (
    "One row per store-month this report judged, exactly as the table below lists them, and over "
    "the whole report rather than the verdicts you have ticked: the tick list filters the table and "
    "moves no figure above it.")

_FEED_NOTE = (
    " Both of the carrier's own records of this payment are read and the greater of the two is "
    "kept, because they are two records of ONE payment — so a month that has landed in one and not "
    "yet the other is never reported as an absence. Which of the two a given month came out of is "
    "not written down anywhere, so these rows name the records that answered rather than claiming "
    "one of them.")

_DECLARED_NOTE = (
    " A month is on this report at all because somebody DECLARED when this store's entitlement "
    "began and how many months it runs. That declaration is a setting, not a measurement, which is "
    "why a store with no declared first month is reported as not measured and never as a store "
    "missing its whole term.")

_RECEIVED_NOTE = _ROW_NOTE + _FEED_NOTE

_MISSING_NOTE = _ROW_NOTE + _DECLARED_NOTE + (
    " Counted here is every month inside a declared entitlement that did not arrive WHILE OTHER "
    "STORES WERE PAID in it — proof the payment run happened and this store was not in it. A month "
    "that did arrive is shown with a measured zero and not counted, and a month whose money may "
    "have landed under this same store's other spelling has no verdict at all and names the other "
    "spelling on its row.")

_UNCONFIRMED_NOTE = _ROW_NOTE + _DECLARED_NOTE + (
    " Counted here is every month inside a declared entitlement in which NO payment of this kind "
    "reached us for ANY store. That is a month of ours that never arrived, not the carrier "
    "withholding it, which is why it is counted apart and never added to the gaps. Re-pull the "
    "month, then read these rows again.")

_NOT_DUE_NOTE = _ROW_NOTE + _DECLARED_NOTE + (
    " Counted here is every month inside a declared entitlement that has not finished yet, or whose "
    "payment run has not happened yet. Nothing is owed for these, and reporting one as missing "
    "would cry wolf every morning.")

_JUDGED_NOTE = _ROW_NOTE + _DECLARED_NOTE + (
    " Counted here is every store-month that fell inside a declared entitlement and could therefore "
    "be judged at all. It is the denominator every other figure on this page rests on, and a store "
    "with no declared first month contributes none of it.")

_UNDECLARED_NOTE = _ROW_NOTE + _FEED_NOTE + (
    " Counted here is every month a store HAD been paid in before and then was not, on a store "
    "whose first entitled month nobody has declared. A gap and the entitlement simply running out "
    "cannot be told apart without that declaration, so these are evidence and NOT an accusation — "
    "they are reported as not measured and are in none of the gap figures above.")

_PRICED_NOTE = _ROW_NOTE + (
    " The dollars here are what a MISSING month is worth, not money that arrived. Each is the "
    "store's monthly amount: the number declared for it in settings where there is one, else the "
    "store's own payments where they all agree. A month nothing can price is counted as missing and "
    "shown WITHOUT a figure, which is why this panel can come back saying the rows do not add up to "
    "the total — the months are missing whether or not we know what they were worth.")

_UNPRICED_NOTE = _ROW_NOTE + (
    " Counted here is every missing month this report cannot put a number on: no monthly amount is "
    "declared for the store and its own payments do not agree with each other, so pricing the gap "
    "would be a guess. These months are in the missing COUNT above and in none of the dollar "
    "figures.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  WHICH RELATIONS ANSWERED, AND WHICH DECLARED. Names only — nothing here reads or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def feed_tables(feed_rows):
    """PURE: the carrier relations this run's read ACTUALLY drew entitlement rows from.

    `feed_rows` is `_mdf_entitlement_run`'s own `{"line": n, "statement": n}` tally, counted AFTER
    the declared-category filter — so it is the read's own record of which relation answered, not a
    guess from the config. A run that drew from neither names nothing nameable rather than claiming
    a relation that returned no row of this kind.

    This is the ONE home for that question on this report, and it is resolved off the run's own meta
    for the same reason `processor_money_drilldown.sales_union_tables` is: which table answered is a
    fact the READ knows, and a constant would be a claim.
    """
    rows = feed_rows if isinstance(feed_rows, dict) else {}

    def _n(key):
        try:
            return int(rows.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    out = []
    if _n("line") > 0:
        out.append(PAYMENT_DETAIL)
    if _n("statement") > 0:
        out.append(COMP_REPORT)
    return tuple(out) or (UNNAMEABLE,)


def _verdict_tables(feeds):
    """PURE: where ONE store-month's VERDICT was read, row by row.

    A month that arrived was read out of the carrier's records. A month inside a DECLARED window
    that did not arrive is on this report because of the declaration, and where other stores WERE
    paid in it the records are the evidence that the run happened — so such a row names both. A
    store-month with no declared window and no payment was read out of nothing nameable, which is
    the answer rather than a gap in the tool (§CORE-17.3i).
    """
    feeds = tuple(t for t in (feeds or ()) if t)

    def _read(r):
        r = r or {}
        status = r.get("status")
        if status == RECEIVED:
            return feeds or (UNNAMEABLE,)
        declared = r.get("entitlement_month") is not None
        paid_others = bool(r.get("stores_paid_in_month"))
        out = []
        if declared:
            out.append(ENTITLEMENT_DECLARATION)
        if paid_others and feeds:
            out.extend(feeds)
        return tuple(out) or (UNNAMEABLE,)
    return _read


def _rate_tables(feeds):
    """PURE: where ONE store-month's MONTHLY AMOUNT came from — the config row somebody typed it
    into, or the store's own observed payments. The producer records which on the row itself
    (`monthly_rate_basis`), so this reads its answer instead of re-deciding it.
    """
    feeds = tuple(t for t in (feeds or ()) if t)

    def _read(r):
        r = r or {}
        if r.get("monthly_rate") is None:
            return (UNNAMEABLE,)
        if str(r.get("monthly_rate_basis") or "") == "declared":
            return (ENTITLEMENT_DECLARATION,)
        return feeds or (UNNAMEABLE,)
    return _read


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  READERS over one store-month row.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _label(r):
    """PURE: the store-month exactly as the table names it. A row with no month is the store itself,
    which is the shape a store nothing could be said about comes back as."""
    r = r or {}
    store = str(r.get("store") or "").strip()
    month = str(r.get("month") or "").strip()
    return " · ".join(p for p in (store, month) if p) or None


def _fields(r):
    """PURE: what else a reader needs beside one store-month."""
    r = r or {}
    return {"verdict": r.get("status_label") or r.get("status") or None,
            "month of term": (f"{r['entitlement_month']} of {r['term_months']}"
                              if r.get("entitlement_month") and r.get("term_months") else None),
            "first entitled month": r.get("window_start") or None,
            "monthly amount": r.get("monthly_rate"),
            "amount basis": r.get("monthly_rate_basis") or None,
            "other stores paid that month": r.get("stores_paid_in_month"),
            "may be this store under": (", ".join(r.get("same_store_paid") or ()) or None),
            "why not measured": r.get("reason_label") or None}


def _is(status):
    def _read(r):
        return (r or {}).get("status") == status
    return _read


def _is_undeclared_absence(r):
    """PURE: an OBSERVED absence on a store whose first entitled month nobody declared — the rows
    the producer counts under its own `undeclared_absence_store_months`. A store with no payments at
    all also carries that reason and NO month, and the producer does not count it, so the month is
    part of the test rather than an afterthought."""
    r = r or {}
    return (r.get("status") == NOT_MEASURED
            and r.get("reason") == REASON_WINDOW_NOT_DECLARED
            and bool(r.get("month")))


def _is_judged(r):
    """PURE: did this store-month fall inside a DECLARED entitlement window? The producer stamps the
    month's position in the term on exactly those rows, so this reads its answer."""
    return (r or {}).get("entitlement_month") is not None


def _is_unpriced_missing(r):
    """PURE: a missing month this report cannot put a number on."""
    r = r or {}
    return r.get("status") == MISSING and r.get("monthly_rate") is None


def _counted(predicate):
    """PURE: 1 on a row the figure counts and a MEASURED 0 on a row it does not.

    Every store-month on this report carries a verdict, so a count here is never unmeasurable — the
    unmeasurable shapes on this report are the DOLLARS, and they say so in their own readers below.
    """
    def _read(r):
        return 1 if predicate(r) else 0
    return _read


def _received_amount(r):
    """PURE: the money that arrived for this store-month, and a measured zero on a month that is not
    a receipt — a gap really did bring in nothing, which is a measurement and not a blank."""
    r = r or {}
    if r.get("status") != RECEIVED:
        return report_drilldown.money(0)
    return r.get("amount")


def _priced_absence(predicate):
    """PURE: what one ABSENT month is worth — the store's monthly amount on a row the figure counts,
    **None** where nothing can price it, and a measured zero on a row the figure does not count.

    The None is the point: an unpriced gap is still counted as a gap, so the panel comes back saying
    its rows do not add up to the priced total rather than letting a short sum look complete.
    """
    def _read(r):
        r = r or {}
        if not predicate(r):
            return report_drilldown.money(0)
        return r.get("monthly_rate")
    return _read


def _priced_reason(predicate):
    """PURE: why one counted absence carries no figure, and nothing on a row that does."""
    def _read(r):
        r = r or {}
        if predicate(r) and r.get("monthly_rate") is None:
            return REASON_UNPRICED
        return None
    return _read


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE CATALOGUE — one store-month row, three grains.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: THE PANEL KEYS, WHICH ARE THE PRODUCER'S OWN TOTALS KEYS. The page asks for a figure by the key
#: `entitlement_store_months` folds it under, so a key renamed in its one home moves the tile and
#: its drill together.
COUNT_FIGURES = (
    ("missing_store_months", "Missing store-months", _is(MISSING), _MISSING_NOTE),
    ("unconfirmed_missing_store_months", "Months no payment reached us at all",
     _is(UNCONFIRMED_MISSING), _UNCONFIRMED_NOTE),
    ("undeclared_absence_store_months", "Named absences on a store with no declared start month",
     _is_undeclared_absence, _UNDECLARED_NOTE),
    ("received_store_months", "Store-months received", _is(RECEIVED), _RECEIVED_NOTE),
    ("not_due_store_months", "Store-months not yet owed", _is(NOT_DUE), _NOT_DUE_NOTE),
    ("store_months_judged", "Store-months inside a declared entitlement", _is_judged, _JUDGED_NOTE),
    ("unpriced_missing_store_months", "Missing months nothing can price", _is_unpriced_missing,
     _UNPRICED_NOTE),
)

#: The three dollar figures that PRICE AN ABSENCE, each over its own verdict's rows.
PRICED_FIGURES = (
    ("missing_amount", "What the missing months are worth", _is(MISSING)),
    ("unconfirmed_missing_amount", "What the unreached months are worth", _is(UNCONFIRMED_MISSING)),
    ("undeclared_absence_amount_at_observed_rate",
     "What the named absences would be worth at the store's own monthly amount",
     _is_undeclared_absence),
)

RECEIVED_TOTAL_KEY = "received_total"


def catalogue(feed_rows):
    """PURE: every panel this report serves — ONE store-month row, counts and two kinds of money.

    `feed_rows` is the run's own per-relation tally, so the provenance on every row is resolved off
    what the read actually answered rather than declared here.
    """
    feeds = feed_tables(feed_rows)
    common = {"row_label": _label, "keys": ("store", "month"), "fields": _fields, "schema": SCHEMA}
    verdict = dict(common, tables=_verdict_tables(feeds))
    out = [
        dict(verdict, key=key, total=key, label=label, value=_counted(pred), in_figure=pred,
             unit=report_drilldown.UNIT_COUNT, column="status", note=note)
        for key, label, pred, note in COUNT_FIGURES
    ]
    out.append(dict(common, key=RECEIVED_TOTAL_KEY, total=RECEIVED_TOTAL_KEY,
                    label="Money received", value=_received_amount, in_figure=_is(RECEIVED),
                    tables=feeds, column="amount", unit=report_drilldown.UNIT_MONEY,
                    note=_RECEIVED_NOTE))
    out.extend(
        dict(common, key=key, total=key, label=label, value=_priced_absence(pred), in_figure=pred,
             reason=_priced_reason(pred), tables=_rate_tables(feeds),
             column=DECLARATION_COLUMN, unit=report_drilldown.UNIT_MONEY, note=_PRICED_NOTE)
        for key, label, pred in PRICED_FIGURES
    )
    return tuple(out)


def entitlement_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/mdf-entitlement-coverage`.

    `payload` is the endpoint's own response and every figure is its `totals` entry dereferenced, so
    a panel can never be a second opinion about the tile above it. An org that has declared no
    entitlement has no store-month inside a window, and a report with no rows drills to nothing.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    totals = dict(payload.get("totals") or {})
    if not rows or not totals:
        return {}
    keys = {k for k, _l, _p, _n in COUNT_FIGURES} | {k for k, _l, _p in PRICED_FIGURES} \
        | {RECEIVED_TOTAL_KEY}
    return dict(_served(catalogue(payload.get("feed_rows")), rows,
                        {k: v for k, v in totals.items() if k in keys},
                        cap or report_drilldown.ROW_CAP))
