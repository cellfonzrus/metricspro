"""WHERE DID THIS DEVICE-FINANCING FIGURE COME FROM — the declaration for the four DEVICE-FINANCING
reports (§CORE-17, batch 16).

  /commcalc/vip                        Distributor Invoices — what the distributor billed the dealer
                                       account, per invoice and per store spelling, with the five
                                       money buckets that make up "fees".
  /commcalc/vip/paygo                  Distributor Asset Lending (PayGo) — what the distributor bills
                                       each week for the devices it has lent us.
  /commcalc/device-line-reimbursement  Equipment Reimbursement per Line — per DEVICE: what the
                                       distributor CLAIMED, what the device cost, what the store
                                       charged for it, and what the carrier actually PAID.
  /commcalc/financing                  Financing report — financed units and financed dollars by
                                       vendor x store x rep against each store's monthly target.

Chosen as one family because all four answer one owner question in four parts: *what did financing a
device cost us, what did the distributor bill for it, what did the carrier reimburse, and on which
lines.*

THE SETTLED RULINGS THIS BATCH DOES NOT RE-ARGUE
────────────────────────────────────────────────
Carrier promo / offer / upgrade money is device **REIMBURSEMENT**, not commission, and the
distributor's figure is a CLAIM about that same payment rather than a second payment (owner,
2026-10-08). So the claim leg and the paid leg of the per-line report are two grains on one device row
and the subtraction between them is never netted into one signed number — this declaration carries
that posture through rather than restating it.

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED OFF THE PRODUCERS, NOT ASSUMED (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────────────────────
Reading the four producers (`vip_invoice_filter.summarize`, the PayGo summary fold,
`device_reimb_recon.device_lines` and `financing_report.build`) gives **SIX** grains in **FOUR** row
shapes and **TWO** units, because a grain fixes the ROW, the UNIT **and** the PROVENANCE:

  1. a DISTRIBUTOR STORE SPELLING's INVOICED MONEY   money, out of the distributor invoice feed.
                                                     SEVEN figures on one grain — the subtotal, the
                                                     grand total and each of the five fee buckets —
                                                     because all seven are the same fold of the same
                                                     invoices, differing only in which column holds
                                                     the figure.
  2. a WEEKLY LENDING BATCH's BILLED AMOUNT          money, out of the weekly lending batches. This
                                                     grain was DECLARED ALREADY, by the device-asset
                                                     family (§CORE-17.3l), and the endpoint has been
                                                     serving its panels since that batch — so this
                                                     one REUSES that declaration and writes no
                                                     second catalogue over the same rows. See
                                                     defect 1: what was missing was the WIRING.
  3. a DEVICE's CLAIM                                money, out of the distributor's unit ledger.
                                                     FIVE figures — the claim, the cost, what the
                                                     store charged, and the two unpaid buckets,
                                                     which are the same claim column under two
                                                     verdict predicates.
  4. a DEVICE's CARRIER PAYMENT                      the SAME device row, the SAME unit, and a
                                                     completely different provenance: the carrier's
                                                     own per-line feed. Its own grain for exactly
                                                     that reason (§CORE-17.3n), and the whole point
                                                     of the report — "the distributor says it was
                                                     reimbursed and the carrier never paid it" is a
                                                     sentence about two grains.
  5. a (VENDOR x STORE x REP) ROW's FINANCED DOLLARS money, out of the sales display union as THE
                                                     READ ITSELF reports it.
  6. the same row's FINANCED UNITS                   the same row and the same provenance in a
                                                     different UNIT, which makes it its own grain
                                                     (§CORE-17.3l) — and not a cosmetic one: the
                                                     dollars are an Ext Price stand-in while the
                                                     units are the payout's own device collapse, so
                                                     the two are trustworthy to different degrees
                                                     and the notes say which.

**Grains 3 and 4 are the same device row in the same unit and are still two grains.** The general
lesson is §CORE-17.3n's and it is restated here only because this family is where it bites hardest:
the reader is being asked to dispute a carrier, and a panel that pooled "what we claimed" with "what
arrived" would be evidence for neither side of that dispute.

ONE MODULE, THREE CATALOGUES, AND A FOURTH THIS BATCH REFUSED TO WRITE
─────────────────────────────────────────────────────────────────────
A catalogue is per ROW LIST, so the invoices' store spellings, the per-line report's devices and the
financing report's vendor-store-rep rows are three catalogues: one over all three would be a panel
about rows its report is not showing. The FOURTH — the weekly lending batches — already exists in
`asset/device_asset_drilldown.lending_catalogue`, so writing one here would have been the sibling
derivation the index rules forbid, and two folds of "lifetime billed" would be two numbers hoping to
agree. The duplicate check found it, and this batch dereferences it instead (`PAYGO_CATALOGUE`).

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED PER PAGE
──────────────────────────────────────────────────────────────────────────
Three of these four screens print tiles the SERVER folded and must NOT have their panels narrowed:

  · `/commcalc/vip` — every filter on it is a QUERY PARAMETER, so the server folds again and the
    panel follows the tile by construction. The invoice table's free-text search narrows that table
    alone and moves no tile.
  · `/commcalc/vip/paygo` — no client-side filter exists at all.
  · `/commcalc/device-line-reimbursement` — the VERDICT multi-select filters the table in the
    browser while the six tiles are the server's own fold. Narrowing those panels would make each
    tile read a different number under a filter, which is a figure moved. REPORTED (defect 7), not
    papered over.

The FOURTH is the §CORE-17.3d class and is narrowed:

  · `/commcalc/financing` — the page already recomputes its financed-units and financed-dollars
    tiles in TypeScript from the rows the reader has filtered down to. So its two panels ARE narrowed
    through `lib/drill-narrow`, and each tile is read OFF its narrowed panel rather than reduced a
    second time, which is what makes the tile and its evidence one fold.

Both decisions are LOCKED, in both directions, by `harness_batch16_drilldown.py` §E — with a
non-vacuous witness for each scan, so "the text is absent" can never be confused with "the scan is
blind".

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **"FEES (ALL TYPES)" ON THE DISTRIBUTOR INVOICES.** `summarize` adds the five buckets at the
    TOTAL and folds no per-store fees figure, so there are no rows behind it to open — the
    §CORE-17.3k test with the answer it usually gets for a difference, applied to a sum. The five
    buckets it is composed of each DO drill, and the per-store table prints all five per store.
  · **EVERY COUNT WHOSE LIST IS ON SCREEN.** The invoice count (the per-store table prints it per
    store), the weeks of history (the weekly table IS that list), the per-verdict device counts (the
    verdict picker shows each count and the table is the list), and the financing report's
    transaction count. No key at all, never an empty panel.
  · **THE OPEN LENDING WEEK's TWO FIGURES.** "Current week owed" and "currently overdue" are two
    columns of ONE stored batch row with nothing under them on this report — batch 10's leaf ruling,
    already written into the catalogue this batch reuses.
  · **"STORES HITTING TARGET".** Two counts printed as a ratio, over stores whose target is a
    declared number rather than a measurement (§CORE-17.3h + §CORE-17.3m). The By-store table prints
    every store's own attainment.
  · **THE PER-VENDOR FINANCING TILES.** They re-partition precisely the rows the two served panels
    open to, and the Vendors tab prints each vendor's own units and amount as its own total. A
    second answer beside an existing one is the defect, not the fix.
  · **ONE INVOICE's CONTENTS.** `GET /commcalc/vip/invoice/{id}` already opens any invoice to its
    line items and its devices, and the table already links it from the row.
  · **CARRIER MONEY WITH NO DEVICE ON IT.** The per-line report reports, in its own banner, the
    device-financing dollars whose carrier line carries no identifier. At a per-DEVICE grain there
    are no rows for it — that is the whole reason it is reported separately — so no panel is served
    and the figure keeps the banner it already has (defect 8).

ABSENCE IS NEVER ZERO
─────────────────────
A device the ledger records no sale price for, and a device the carrier's feed never named at all,
are SHOWN and NOT counted, with the reason in the reader's own words. The figures do not move by a
cent: the producer added nothing for the first and $0.00 for the second, and $0.00 adds nothing to a
sum either way — only the ROW changes, which is the entire argument for putting it there.

AN EMPTY REPORT DRILLS TO NOTHING. A report that produced its totals over NO ROWS AT ALL would still
get a panel asserting a figure behind an empty list, which ties only by accident and reads like a bug.
Every entry point below returns an empty mapping in that case.

LIVE DEFECTS THESE FOUR REPORTS HAVE TODAY — ONE FIXED, TEN REPORTED, NO FIGURE MOVED
─────────────────────────────────────────────────────────────────────────────────────
 1. **The PayGo page rendered no drill-down although its own endpoint had been serving two panels
    since batch 12.** `GET /vip/paygo/summary` attaches `lending_drilldowns` for the Asset Lending
    page, and the PayGo page reads the SAME endpoint and threw the panels away — so "Lifetime
    billed" was the same figure with evidence on one screen and none on the other, and the coverage
    ledger recorded the route as owing a drill-down that was already in the payload. **FIXED** here,
    by wiring the page to the panel that existed, and by reusing the catalogue rather than writing a
    second one.
 2. **A SECOND pending lending batch vanishes from every figure AND from the table.** The fold takes
    `max(pending, key=created_on)` as "current" and renders `current + history`, where history is the
    APPROVED batches alone. So where two batches are pending, the older one is in no tile, in no
    total and in no row: money owed that nothing on the page carries. NOT fixed — the repair is a
    change to what the endpoint returns, and §C of the harness pins the defect as a regression so it
    cannot be "fixed" by a change that hides it instead.
 3. **`lifetime_paid` coerces a NULL batch amount to $0.00**, so a week nobody was billed for and a
    week whose amount the portal never reported are identical in the total. Already recorded by the
    catalogue this batch reuses, whose `in_figure` shows such a batch WITHOUT a figure and does not
    count it; the total is unchanged to the cent. The half that stays REPORTED is the total itself.
 4. **"Fees (all types)" is added at the TOTAL.** See the refusals above — no per-store fold exists,
    so the tile has no row-level evidence and is served none.
 5. **The distributor invoices are rolled up per the DISTRIBUTOR's OWN SPELLING of a store address,
    not per physical store.** `summarize` keys `by_store` on `location`, so one store under two
    spellings is two rows in the table and two rows in every panel — measured live by the module
    itself (29 spellings, 26 of them bindable). The market / store FILTER does resolve spellings
    through the org vocabulary, which is why the two can disagree about how many stores there are.
    NOT fixed: re-keying that rollup moves every row and every figure in the table.
 6. **An invoice with no location at all folds into one row labelled with an em dash.** It names the
    invoice feed and no store, which is true, and is reported here rather than tidied away.
 7. **The per-line report's six tiles are the server's fold while the verdict filter narrows the
    table in the browser.** So the tiles and the table disagree about which devices they are about
    the moment a verdict is picked. NOT fixed: the two honest repairs are to re-fold the tiles
    (which moves a figure) or to make the picker a query parameter (which is a different report).
    See §CORE-17.3o.
 8. **Device-financing money the carrier paid on a line carrying no device identifier is in no
    per-store figure.** Correctly so — it cannot be placed on a device — and it is already reported
    in its own banner. Stated here so the absence of a panel is never read as an oversight.
 9. **`device_cost` coerces an unrecorded cost to $0.00 with no recorded-flag.** The sale price has
    `store_payment_recorded` and the cost has no equivalent, so a device whose cost the ledger never
    carried is indistinguishable from a free one, and the cost total is silently light. NOTHING IN
    THE PAYLOAD distinguishes the two, and inventing the distinction here would be a guess (the
    batch-15 defect-7 precedent). REPORTED.
10. **On the financing strip, two tiles move under a filter and two do not.** Financed units and
    financed dollars are recomputed in the browser from the filtered rows; the per-vendor tiles
    beside them are the server's own `by_vendor` fold and are not. So one strip holds both
    §CORE-17.3o classes at once. The two client-folded tiles are narrowed (and read off their
    panels); the per-vendor tiles are served no panel, and the disagreement is REPORTED rather than
    resolved by moving one of them.
11. **The financing report tells the payload, and nobody, when it counted units the FALLBACK way.**
    `_collapse_units` falls back to a per-TRANSACTION collapse when the payout's own device gate is
    unavailable and records `unit_fallback_per_transaction` in `unit_notes` — which the page declares
    in its response type and renders nowhere. So the one circumstance in which the report's units are
    NOT the payout's units is invisible. The units panel's note now SAYS so when the payload says so,
    which is the half a drill-down can honestly fix; the screen's own silence is REPORTED.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it. PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store
/ vendor name (RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3p.
"""
from app.modules.asset import device_asset_drilldown as _asset_dd
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import device_reimb_recon as _drr
from app.modules.commcalc import processor_money_drilldown as _pmd
from app.modules.commcalc import vip_invoice_filter as _vipf
from app.modules.core import report_drilldown

# ── THE FOUR PROVENANCE ANSWERS THIS FAMILY HAS ──────────────────────────────────────────────────
#: The distributor's INVOICE feed. Added to the registry by this batch and dereferenced here, never
#: spelled at the call site.
DISTRIBUTOR_INVOICES = _lineage.DISTRIBUTOR_INVOICES

#: The distributor's UNIT LEDGER — the relation a reimbursement CLAIM, a device cost and the price a
#: store charged are all read out of. The registry's own constant, which the device-asset family
#: already dereferences.
ASSET_LEDGER = _lineage.ASSET_LEDGER

#: The carrier's PER-LINE feed — the relation a PAYMENT is read out of, and the only one on this
#: family that can answer "did the money actually arrive".
CARRIER_LINE_FEED = _lineage.PAYMENT_DETAIL

#: The weekly lending batches. DEREFERENCED from the device-asset family's declaration, which already
#: dereferences the registry — naming it a third time would be the copied fact the registry exists to
#: prevent.
PAYGO_BATCHES = _asset_dd.PAYGO_BATCHES

#: THE FIFTH ANSWER IS NOT A CONSTANT, AND THAT IS THE POINT. Which tables the sales display union
#: ACTUALLY read for a period is a fact the READ knows and records, so the financing report's
#: provenance is resolved off its own `source.meta` through the ONE home for that question —
#: `processor_money_drilldown.sales_union_tables`, which batch 14 wrote for exactly this. A second
#: reading of the same meta here would be a second answer to one question.
sales_union_tables = _pmd.sales_union_tables

#: Every relation on this batch lives in the commcalc schema, so one schema serves every panel.
SCHEMA = "commcalc"

#: The invoice money buckets that make up "fees" — the producer's OWN list (`vip_invoice_filter
#: .FEE_COLS`), dereferenced. A sixth bucket added there gains a panel here without anybody editing
#: this file, which is the only way the tile strip and the catalogue cannot drift.
FEE_COLS = tuple(_vipf.FEE_COLS)

#: The two invoice columns that are not fee buckets, and the columns a device figure is read out of.
#: Named so a panel can say WHICH column held the money rather than only which table.
COL_SUB_TOTAL = "sub_total"
COL_GRAND_TOTAL = "grand_total"

#: The per-line report's own verdicts, dereferenced from the producer rather than re-spelled, so the
#: two unpaid buckets here count exactly the rows its totals counted.
LINE_NOT_PAID = _drr.LINE_NOT_PAID
LINE_NOT_MEASURED = _drr.LINE_NOT_MEASURED

#: WHY A ROW ON THIS FAMILY CARRIES NO NUMBER. The first four are the PRODUCER's own reason keys
#: (`device_reimb_recon`), which this batch added to the one home's vocabulary as the reader-facing
#: half — they are not re-worded here and the producer's longer explanation is not copied here. The
#: last two already existed in that vocabulary and are reused rather than re-spelled.
REASON_NO_RULE = _drr.REASON_NO_RULE
REASON_DEVICE_UNIDENTIFIED = _drr.REASON_DEVICE_UNIDENTIFIED
REASON_STORE_AMBIGUOUS = _drr.REASON_STORE_AMBIGUOUS
REASON_COVERAGE_INCOMPLETE = _drr.REASON_COVERAGE_INCOMPLETE
#: The carrier's feed was never looked up for this device, so it carries no figure rather than a
#: zero somebody could mistake for a measurement. Batch 11's key, reused.
REASON_CARRIER_NOT_LOOKED_UP = "absent_from_the_loaded_statement"
#: The ledger carries no price for what the store charged. Batch 10's key, reused.
REASON_PRICE_NOT_RECORDED = "amount_not_recorded"

#: The weekly-lending catalogue, DEREFERENCED. Batch 12 declared this grain and the endpoint has been
#: serving its panels ever since; a catalogue of our own over the same batch rows would be the
#: sibling derivation the index rules forbid.
PAYGO_CATALOGUE = _asset_dd.lending_catalogue
paygo_drilldowns = _asset_dd.lending_drilldowns


# ── NOTES — what each panel is, in the words the screen already uses ────────────────────────────

#: Said on the panels whose screen filters its own table in the browser WITHOUT re-folding the tile
#: (§CORE-17.3o). The reader is told which population the figure is about instead of being left to
#: assume it is the one below.
_WHOLE_RANGE = (
    " This figure, and these rows, cover everything the report was asked for — not the selection the "
    "table below is filtered to. The tile is the server's own fold and narrowing it here would change "
    "the number you are reading.")

_INVOICE_NOTE = (
    "One row per store as the DISTRIBUTOR spells it, which is how the table below is grouped: these "
    "are its own invoice records, and one physical store it spells two ways is two rows here. Each "
    "figure is that spelling's share of the tile, read out of the invoices themselves — so the rows "
    "add up to the tile and can be taken back to the invoice list underneath, which opens any single "
    "invoice to its line items and its devices.")

_CLAIM_NOTE = (
    "One row per device, as the table below lists them. Each figure is what the DISTRIBUTOR's own "
    "unit ledger says about that device — a claim, a cost, or the price the store charged — and not "
    "one cent of it is evidence that any money arrived. What arrived is the carrier's side, which "
    "has its own panels on the tiles beside this one." + _WHOLE_RANGE)

_PRICE_NOTE = (
    "One row per device: the price the ledger records the store as having charged for it. A device "
    "the ledger carries no price for is shown WITHOUT a figure and is not counted — a missing price "
    "is not a sale for $0.00, and the tile's own subtitle says how many devices that is." +
    _WHOLE_RANGE)

_UNPAID_NOTE = (
    "One row per device. The figure counts the CLAIM on each device the report reached this verdict "
    "about, and every other device is shown beside them for context with its own verdict, because "
    "which devices are NOT in this bucket is most of what makes the number readable. The claim is "
    "measured on every row; what was not measured is the carrier's side of it." + _WHOLE_RANGE)

_PAID_NOTE = (
    "One row per device, and each figure came out of the CARRIER's own per-line feed rather than the "
    "distributor's ledger — a different relation answering a different question, which is why it is "
    "a panel of its own beside the claim. A device the feed never named at all is shown WITHOUT a "
    "figure and is not counted: nothing is known about it, which is not the same as nothing having "
    "been paid for it." + _WHOLE_RANGE)

_FINANCING_AMOUNT_NOTE = (
    "One row per store and salesperson for each financing vendor, as the detail tab lists them — the "
    "vendor is deliberately not named in this panel, because the page relabels vendors under a "
    "dual-carrier lens and this panel is not behind that lens. Each figure is the Ext Price of the "
    "financed device line, which is a LABELLED STAND-IN: the export's own financed-amount column is "
    "not stored, so this is the price of the thing financed rather than the amount financed. These "
    "rows are the selection you are viewing.")

_FINANCING_UNITS_NOTE = (
    "One row per store and salesperson for each financing vendor. Each figure counts DEVICES, not "
    "receipt lines, collapsed by the same rule the payout uses — so this count and the count that "
    "pays agree by construction. These rows are the selection you are viewing.")

#: Appended to the units note when, and only when, the payload says the device collapse FELL BACK to
#: one-per-transaction (defect 11). The report records that in `unit_notes` and the screen prints it
#: nowhere; a panel that claimed the payout's own collapse in that case would be claiming a
#: construction it did not have.
_UNITS_FALLBACK_NOTE = (
    " FOR THIS READ THE PAYOUT'S OWN DEVICE COLLAPSE WAS UNAVAILABLE, so units were counted once per "
    "transaction instead. A contract that financed two handsets therefore counts once here, and this "
    "count is NOT the one that pays.")

#: The producer's own code for that fallback, dereferenced from nothing because the producer spells it
#: in one place and exposes it only in the payload — matched by name, and the harness fails the build
#: if the producer stops emitting it.
UNIT_FALLBACK_CODE = "unit_fallback_per_transaction"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  THE DISTRIBUTOR INVOICES — seven figures on one store-spelling row.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _invoice_label(r):
    """PURE: the store exactly as the report's own table prints it — the distributor's spelling."""
    return str((r or {}).get("location") or "") or None


def _invoice_fields(r):
    """PURE: what else the reader needs beside an invoice figure on this row."""
    r = r or {}
    return {"invoices": r.get("invoices"), "subtotal": r.get("sub_total"),
            "grand total": r.get("grand_total")}


def _column_value(field):
    """PURE: a reader for one invoice money column. The producer has already folded every spelling's
    share of each column, so there is nothing here to add up."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def invoice_catalogue():
    """PURE: the Distributor Invoices panels — the subtotal, the grand total and the five fee buckets.

    The fee buckets are DERIVED from the producer's own `FEE_COLS`, so a sixth bucket added there
    gains a panel without this file being edited. "Fees (all types)" is absent on purpose: the
    producer adds those five at the TOTAL and folds no per-store figure for it (§CORE-17.3k), and the
    invoice COUNT is absent because the table below prints it per store.
    """
    common = {"row_label": _invoice_label, "keys": ("location",), "fields": _invoice_fields,
              "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA,
              "tables": (DISTRIBUTOR_INVOICES,), "note": _INVOICE_NOTE}
    out = [
        {"key": COL_SUB_TOTAL, "total": COL_SUB_TOTAL, "label": "Invoiced subtotal",
         "value": _column_value(COL_SUB_TOTAL), "column": COL_SUB_TOTAL, **common},
        {"key": COL_GRAND_TOTAL, "total": COL_GRAND_TOTAL, "label": "Invoiced grand total",
         "value": _column_value(COL_GRAND_TOTAL), "column": COL_GRAND_TOTAL, **common},
    ]
    for col in FEE_COLS:
        out.append({"key": col, "total": col, "label": "Invoiced %s" % col.replace("_", " "),
                    "value": _column_value(col), "column": col, **common})
    return tuple(out)


def invoice_drilldowns(summary, *, cap=None):
    """PURE: every panel for `GET /commcalc/vip/summary`.

    `summary` is `vip_invoice_filter.summarize`'s own output as the endpoint serves it, and every
    figure is its `totals` entry dereferenced — so a panel can never be a second opinion about the
    tile above it. A selection with no invoice in it drills to nothing.
    """
    rows = [r for r in ((summary or {}).get("by_store") or ()) if r]
    totals = dict((summary or {}).get("totals") or {})
    if not rows or not totals:
        return {}
    return report_drilldown.tiles(invoice_catalogue(), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE EQUIPMENT REIMBURSEMENT PER LINE — two grains on one device row, separated by provenance.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _device_label(r):
    """PURE: the device as the table below identifies it — when it was reimbursed, at which store,
    what it is and which handset, which together are what a reader looking for one row needs."""
    r = r or {}
    parts = [str(r.get("reimbursement_date") or "")[:10], str(r.get("store") or "").strip(),
             str(r.get("model") or "").strip(), str(r.get("device_id") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def _device_fields(r):
    """PURE: the verdict and the context a reader needs to judge a device figure — the producer's own
    words for both, never a second wording of them."""
    r = r or {}
    return {"verdict": r.get("status_label"), "why": r.get("reason_label"),
            "sold by": r.get("sold_by"), "sold at": r.get("sold_by_store"),
            "paid in": r.get("carrier_paid_here_month_label"),
            "paid late": ("yes" if r.get("carrier_paid_after_claim_month") else ""),
            "claimed": r.get("distributor_claimed"),
            "paid here": r.get("carrier_paid_here"),
            "paid elsewhere": r.get("carrier_paid_other_total")}


def carrier_looked_up(r):
    """PURE: did the carrier's per-line feed name this device AT ALL?

    The producer's own `carrier_paid_lines` — the number of carrier lines it found for the device,
    anywhere. Zero means the feed was never looked up for it (an unidentified device) or never
    mentioned it, and in both cases the $0.00 the producer folded was read out of nothing. A device
    the feed DID name legitimately carries a measured $0.00 at this store when it was paid at
    another one, which is exactly why this asks about the LOOKUP and not about the amount."""
    return int((r or {}).get("carrier_paid_lines") or 0) > 0


def _paid_value(field):
    """PURE: a carrier figure, or None where the feed never named the device (§CORE-17.3i)."""
    def _read(r):
        return (r or {}).get(field) if carrier_looked_up(r) else None
    return _read


def _paid_tables(r):
    """PURE: the carrier feed, and NO table at all for a device it never named — because the $0.00
    shown for such a device was read out of nothing."""
    return (CARRIER_LINE_FEED,) if carrier_looked_up(r) else ()


def _paid_reason(r):
    return None if carrier_looked_up(r) else REASON_CARRIER_NOT_LOOKED_UP


def _price_value(r):
    """PURE: the price the store charged, or None when the ledger records none — the producer's own
    `store_payment_recorded`, which is why this one absence is tellable from a zero."""
    r = r or {}
    return r.get("store_device_payment") if r.get("store_payment_recorded") else None


def _price_reason(r):
    return None if (r or {}).get("store_payment_recorded") else REASON_PRICE_NOT_RECORDED


def _verdict_is(status):
    """PURE: the predicate for which devices one of the two unpaid buckets COUNTS — the producer's own
    verdict, read rather than re-derived, so the panel counts exactly the rows the total counted."""
    def _in(r):
        return (r or {}).get("status") == status
    return _in


def _unpaid_reason(status):
    """PURE: the row's OWN reason, on the rows the bucket counts, and nothing on the rest.

    A device in the not-measured bucket carries the producer's reason for why its carrier side could
    not be judged; a device shown for context carries its verdict in `fields` instead, because a
    reason printed against a row the figure does not count would read as an exclusion rule."""
    def _read(r):
        return (r or {}).get("reason") if (r or {}).get("status") == status else None
    return _read


def reimb_catalogue(configured=True):
    """PURE: the per-line report's panels — five on the CLAIM grain, two on the PAYMENT grain.

    `configured` is the producer's own flag: with no device-financing routing declared, every device
    is `not_measured`, the carrier feed was never classified and both paid-leg totals are $0.00 over
    rows that all carry no figure at all. That is the absence of a figure rather than a figure, so
    the two paid-leg panels are NOT SERVED in that case — §CORE-17.3m's posture, and the shape batch
    15's own negative control was written to catch.
    """
    common = {"row_label": _device_label, "keys": ("store", "device_id"),
              "fields": _device_fields, "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA}
    claim = dict(common, tables=(ASSET_LEDGER,))
    out = [
        {"key": "distributor_claimed", "total": "distributor_claimed",
         "label": "Reimbursement the distributor claims",
         "value": _column_value("distributor_claimed"), "column": "distributor_claimed",
         "note": _CLAIM_NOTE, **claim},
        {"key": "device_cost", "total": "device_cost", "label": "What the devices cost",
         "value": _column_value("device_cost"), "column": "device_cost",
         "note": _CLAIM_NOTE, **claim},
        {"key": "store_device_payment", "total": "store_device_payment",
         "label": "What the stores charged for them",
         "value": _price_value, "column": "store_device_payment", "reason": _price_reason,
         "note": _PRICE_NOTE, **claim},
        {"key": "not_paid_total", "total": "not_paid_total",
         "label": "Claimed and not paid anywhere",
         "value": _column_value("distributor_claimed"), "column": "distributor_claimed",
         "in_figure": _verdict_is(LINE_NOT_PAID), "reason": _unpaid_reason(LINE_NOT_PAID),
         "note": _UNPAID_NOTE, **claim},
        {"key": "not_measured_total", "total": "not_measured_total",
         "label": "Claimed and could not be measured",
         "value": _column_value("distributor_claimed"), "column": "distributor_claimed",
         "in_figure": _verdict_is(LINE_NOT_MEASURED), "reason": _unpaid_reason(LINE_NOT_MEASURED),
         "note": _UNPAID_NOTE, **claim},
    ]
    if configured:
        for key, label in (("carrier_paid_here", "What the carrier paid this store"),
                           ("carrier_paid_other_total", "What it paid a DIFFERENT store")):
            out.append({"key": key, "total": key, "label": label, "value": _paid_value(key),
                        "column": key, "tables": _paid_tables, "reason": _paid_reason,
                        "note": _PAID_NOTE, **common})
    return tuple(out)


def reimb_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /commcalc/device-line-reimbursement`.

    `payload` is the endpoint's own response. A month the distributor claims no device reimbursement
    in has no device rows and therefore no panels at all.
    """
    rows = [r for r in ((payload or {}).get("rows") or ()) if r]
    totals = dict((payload or {}).get("totals") or {})
    if not rows or not totals:
        return {}
    return report_drilldown.tiles(
        reimb_catalogue(bool((payload or {}).get("configured", True))), rows, totals,
        cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE FINANCING REPORT — one row, one provenance, two units, two grains.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _financing_label(r):
    """PURE: the row as the detail tab identifies it, MINUS the vendor.

    The page relabels every vendor generically under a dual-carrier lens (`carrier-scope
    .financingVendorLabel`) precisely so a brand never reaches a screen it should not, and the shared
    panel is not behind that lens. So the vendor is carried in the row KEY, where it addresses the
    row, and never in the words — the house's own carrier-vocabulary posture, applied to a panel.
    """
    r = r or {}
    parts = [str(r.get("store") or r.get("store_code") or "").strip(), str(r.get("rep") or "").strip()]
    return " · ".join(p for p in parts if p) or None


def _financing_fields(r):
    r = r or {}
    return {"market": r.get("market"), "transactions": r.get("transactions"),
            "first": r.get("first_date"), "last": r.get("last_date")}


def units_fallback(result):
    """PURE: did this read count units the FALLBACK way (defect 11)?

    The producer's own `unit_notes`, read rather than guessed. True means the payout's device collapse
    was unavailable and units were counted once per transaction, which the panel then SAYS."""
    return UNIT_FALLBACK_CODE in tuple((result or {}).get("unit_notes") or ())


def financing_catalogue(sales_tables, *, fallback_units=False):
    """PURE: the financing report's two panels — financed dollars and financed units.

    `sales_tables` is what the display union ACTUALLY read for this period, resolved by the ONE home
    for that question off the read's own meta. Nothing resolvable arrives here as `()` and the panels
    then report an origin they could not trace, rather than naming a feed on a guess.

    The per-vendor tiles, the stores-hitting-target ratio and the transaction count get no key at all
    — see the refusals in the header.
    """
    tables = tuple(sales_tables or ())
    common = {"row_label": _financing_label, "keys": ("vendor_key", "store_code", "rep"),
              "fields": _financing_fields, "schema": SCHEMA, "tables": tables}
    units_note = _FINANCING_UNITS_NOTE + (_UNITS_FALLBACK_NOTE if fallback_units else "")
    return (
        {"key": "amount", "total": "amount", "label": "Financed amount",
         "value": _column_value("amount"), "column": "ext_price",
         "unit": report_drilldown.UNIT_MONEY, "note": _FINANCING_AMOUNT_NOTE, **common},
        {"key": "units", "total": "units", "label": "Financed units",
         "value": _column_value("units"), "unit": report_drilldown.UNIT_COUNT,
         "note": units_note, **common},
    )


def financing_drilldowns(result, *, cap=None):
    """PURE: both panels for `GET /commcalc/financing/{period}`.

    `result` is `financing_report.build`'s output as the endpoint serves it, including the `source`
    meta the sales read recorded — which is where the provenance comes from. A period in which no
    vendor matched a line has no rows and drills to nothing.
    """
    rows = [r for r in ((result or {}).get("rows") or ()) if r]
    totals = dict((result or {}).get("totals") or {})
    if not rows or not totals:
        return {}
    meta = ((result or {}).get("source") or {}).get("meta")
    return report_drilldown.tiles(
        financing_catalogue(sales_union_tables(meta), fallback_units=units_fallback(result)),
        rows, totals, cap=cap or report_drilldown.ROW_CAP)
