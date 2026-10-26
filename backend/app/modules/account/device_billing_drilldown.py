"""WHERE DID THIS FIGURE COME FROM — the declaration for THE DISTRIBUTOR'S DEVICE BILLING
(§CORE-17, batch 23).

  /accounts/device-purchases   what the distributor BILLED us for devices in a window
  /accounts/device-payable     which of the devices it had billed by a given day were still UNPAID

ONE FAMILY, AND THE GRAIN RULE SAYS SO RATHER THAN TASTE. A reader works these two through in
sequence to answer ONE question — *what did we buy from the distributor, and what of it do we still
owe?* Both fold the SAME subject (the units the distributor billed against our dealer account) at the
SAME ROW (a store, rolled up by company), out of the SAME two relations (the invoice LINES carry the
money, the SERIALISED unit rows carry the units). `device_payable` even imports the voided-invoice
rule FROM `device_purchases` so the two can never disagree about which invoices count. Two
declarations over that would be the sibling derivation the index rules forbid; two CATALOGUES inside
one declaration is the `day_recon_drilldown` / `statement_drilldown` precedent (§CORE-17.3c,
§CORE-17.3j).

HOW MANY GRAINS — MEASURED OFF THE PANELS, NOT ASSUMED (§CORE-17.3l / §CORE-17.3n)
─────────────────────────────────────────────────────────────────────────────────
The ROW shapes are three, not one, and the UNITS and PROVENANCES are read off the panels:

  1. a STORE ROW's MONEY          money, out of the invoice lines
  2. a STORE ROW's UNITS          count, out of the serialised unit rows — the SAME row and a
                                  different UNIT *and* a different RELATION, which is why this
                                  report carries both unit bases and explains the gap between them
  3. a STORE ROW's UNPAID MONEY   money, out of the serialised unit rows AND the unit ledger — the
                                  SAME row and the SAME unit as 1, separated by PROVENANCE ALONE
                                  (§CORE-17.3n): what was BILLED is the invoice line's own money,
                                  while what is still OWED is the LEDGER's own per-unit amount
                                  judged against the LEDGER's own per-unit payment date. Two
                                  relations answer "what does this store owe for devices", and a
                                  reader is owed that distinction because only one of them is the
                                  invoice we were sent
  4. a STORE ROW's UNPAID UNITS   count, out of the same two relations
  5. a BILLED-ITEM ROW            money, out of the invoice lines — the non-device items, a
                                  different row shape with a DIFFERENT BASIS the payload names
                                  itself ("billed", settlement `not_measured`)
  6. an UNRESOLVED-LOCATION ROW   money, out of the invoice lines — the spend whose location no
                                  store vocabulary matched, which is a real row of this report

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **"DEVICES MATCHED TO THE UNIT LEDGER"** — a RATE (matched ÷ invoiced). §CORE-17.3h: a rate gets
    no panel, and the two counts it divides are offered instead.
  · **"INVOICES IN WINDOW"** — a DISTINCT COUNT of invoice numbers, not Σ(a field) over the
    report's rows. The store rows carry no invoice count to add up, so a panel would have to count
    something the report did not.
  · **THE UNIT-RECONCILIATION BLOCK** on the purchases report (serialised vs declared quantity, and
    its three causes) — every figure in it is a DIFFERENCE taken at the TOTAL between two counts the
    report folds separately (§CORE-17.3k), and the two counts themselves each drill.
  · **"PAYABLE INCLUDING VOIDED" and the five `excluded_voided_*` figures** — composed at the total
    from the counted figure plus what the void rule removed. The counted half drills; the excluded
    half is the payload's own per-invoice list printed directly underneath it.
  · **`distinct_device_payable_*`** — the counted figure minus the repeat-serial rows, a subtraction
    at the TOTAL (§CORE-17.3k).
  · **THE COVERAGE BLOCK** — a derived window and a per-month scan, not a folded figure, and when it
    says `not_measured` the report prints NO figure at all, so there is nothing to explain. Such a
    period drills to `{}` through the one home's own `served_tiles` rule rather than through a branch
    here.
  · **A 0 OVER ZERO COUNTED ROWS** — refused by `served_tiles`, because such a panel ties 0 against 0
    and reads to a reader as "nothing was there, and here is the proof". A NON-zero figure over no
    counted rows KEEPS its panel, so the tie-out publishes the disagreement.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED BY READING BOTH PAGES
──────────────────────────────────────────────────────────────────────────────────────
**BOTH pages fold their own tiles in the browser** — each reduces over the store rows its standard
filter bar left on screen — so both NARROW their panels through `lib/drill-narrow` and read each
store-keyed figure OFF its narrowed panel through the shared `agreesToTheCent` gate. The figures the
pages themselves refuse to narrow (they print `—` under a filter: the non-device money, the
unresolved-location spend, the already-paid total) keep the server's own unnarrowed panel, offered
only while nothing is selected — the page's own rule, not a second one.
`harness_batch23_drilldown.py` §G fails the build in both directions off each page's own text.

💰 MOVES NO MONEY. Read-only presentation of figures the two producers already folded. Books nothing,
writes nothing, and computes no business figure of its own — every amount is the payload's own,
dereferenced.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3w.

───────────────────────────────────────────────────────────────────────────────────────────────────
TWO LIVE DEFECTS, REPORTED, NEITHER REPAIRED, NO FIGURE MOVED. Each carries a regression check in
`harness_batch23_drilldown.py` driven off the real producer.

 1. **THE COMPANY ROLL-UP ON THE PURCHASES REPORT COUNTS A STORE THE SERVER ALREADY COUNTED ONCE
    MORE UNDER ITS OWN NAME.** `device_purchases.aggregate` emits `by_store` rows whose `company` is
    the placeholder label for "no company assignment", AND repeats exactly those rows under
    `unresolved.company_not_mapped` — the SAME dict objects, not copies. The page renders both
    tables, so a reader sees that money twice and the second table reads as a separate finding
    rather than as a view of the first. The panels count the `by_store` population ONCE, exactly as
    the tiles do, and the unresolved table's own figures get no panel — a panel for a second printing
    of a figure already explained would be two panels for one number. REPORTED rather than changed:
    which tables a finance report prints is a screen decision.
 2. **"ALREADY PAID BY THIS DATE" IS NOT FOLDED FROM THE ROWS THE PAGE SHOWS, AND THE PAGE HIDES
    THAT BY DASHING IT.** On `/accounts/device-payable` the paid total is served whole and the page
    prints `—` for it under any filter rather than narrowing it, while the payable total beside it
    DOES narrow. Both are Σ over the same store rows, so the asymmetry is the page's and not the
    producer's. The panel is therefore offered for the paid figure only on the unfiltered view —
    honest, and it makes the asymmetry visible instead of papering over it with a narrowed figure the
    tile does not print.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: WHERE EACH FIGURE WAS ACTUALLY READ OUT OF. DEREFERENCED from the lineage registry, the one home
#: for a relation's name — spelling one here would be the copied fact that registry exists to
#: prevent, and a provenance claim nobody checked.
INVOICE_LINES = _lineage.DISTRIBUTOR_INVOICE_LINES
INVOICE_UNITS = _lineage.DISTRIBUTOR_INVOICE_UNITS
UNIT_LEDGER = _lineage.ASSET_LEDGER

#: THE LEDGER COLUMN THE PAYABLE MONEY IS READ OUT OF, in the producer's own spelling. It is named
#: in the panel's NOTE and deliberately NOT as the `column` of either origin: these rows name TWO
#: relations and the one home applies one column name to every relation a row names, so declaring it
#: would assert that the serialised invoice rows carry a column they do not have. A false statement
#: about a relation is worse than a missing one.
OWED_COLUMN = "owed_to_vip"

SCHEMA = "commcalc"

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule (§CORE-17.3u), bound by IDENTITY
#: so this module holds no copy of it.
_served = report_drilldown.served_tiles


# ── NOTES — what each panel is, in the words the two screens already use ───────────────────────

_STORE_NOTE = (
    "One row per store, exactly as the By store table lists them, with the store and the company "
    "resolved by the same two resolvers the P&L books through — so this report cannot disagree with "
    "the P&L about where a store sits. A location no store vocabulary matched is NOT dropped: it "
    "carries its own labelled row.")

_VOID_NOTE = (
    " An invoice whose header says it was voided is in none of these figures, and what it would "
    "have contributed is listed per invoice on the report itself.")

_PURCHASE_MONEY_NOTE = _STORE_NOTE + _VOID_NOTE + (
    " This is what the distributor BILLED, recognised on the invoice date — every billed unit, "
    "whether it has since sold, is still on the shelf, or was never activated. It is not what the "
    "units we sold cost us, and the two are not meant to tie.")

_PURCHASE_UNIT_NOTE = _STORE_NOTE + _VOID_NOTE + (
    " A unit here is a row that arrived with a serial on it, which is a different count from the "
    "quantity the invoice line declares; the report carries both and decomposes the gap between "
    "them into its causes.")

_NON_DEVICE_NOTE = (
    "One row per billed item that is not a device — these carry no serial, so they are reported "
    "beside the device figure under their own basis rather than inside it. \"All lines\" on the "
    "invoices is always devices plus these." + _VOID_NOTE)

_UNRESOLVED_NOTE = (
    "One row per billed location that nothing in the store vocabulary matched at all. This money is "
    "real and is shown rather than absorbed; a store-alias row is what makes such a location land on "
    "a store deliberately." + _VOID_NOTE)

_PAYABLE_MONEY_NOTE = _STORE_NOTE + _VOID_NOTE + (
    " Counted here is every unit the distributor had already billed on the day asked for and whose "
    "per-unit payment date is later than that day, or absent. Which units were billed comes from the "
    "serialised invoice rows and both the amount owed and the day it was settled come from the unit "
    "ledger (its `" + OWED_COLUMN + "` column), so each row names both. This is a BACKDATED "
    "question and the current open-balance figures elsewhere answer a different one. A unit the "
    "distributor states no amount on is counted as unpaid and adds nothing to the money, which the "
    "report says beside the figure.")

_PAYABLE_UNIT_NOTE = _STORE_NOTE + _VOID_NOTE + (
    " Counted here are the unpaid UNITS rather than their money, and the two can disagree on "
    "purpose: the feed puts no money at all on some unpaid units, and the report states how many.")

_PAID_NOTE = _STORE_NOTE + _VOID_NOTE + (
    " Counted here is the opposite side: the units already settled on or before the day asked for, "
    "by their own per-unit payment date.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  READERS over one row of either report. Accessors and names only — no arithmetic.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded figure on one row."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _store_label(r):
    """PURE: the store exactly as the table names it, with its company beside it — the two together
    are what the reader sees, because one store can only sit under one company and the roll-up is
    keyed on both."""
    r = r or {}
    store = str(r.get("store") or "").strip()
    company = str(r.get("company") or "").strip()
    if store and company:
        return f"{store} · {company}"
    return store or company or None


def _store_fields(r):
    """PURE: what else a reader needs beside one store's row."""
    r = r or {}
    return {"company": (str(r.get("company") or "").strip() or None),
            "market": (str(r.get("market") or "").strip() or None)}


def _item_label(r):
    """PURE: the billed item as the report names it."""
    return str((r or {}).get("name") or "").strip() or None


def _location_label(r):
    """PURE: the raw billed location, which is the whole point of this row list."""
    return str((r or {}).get("location") or "").strip() or None


def _lines_fields(r):
    """PURE: how many invoice lines stand behind one row, which is the reader's next question."""
    r = r or {}
    return {"invoice lines": r.get("lines")}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE PURCHASES CATALOGUE — three row shapes, two relations, one window.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The payload's own field names, read once here so the page and this declaration ask for a panel by
#: the same string.
DEVICE_AMOUNT = "device_amount"
SERIALISED_UNITS = "serialised_units"
NON_DEVICE_AMOUNT = "non_device_amount"
UNRESOLVED_AMOUNT = "unresolved_amount"


def purchases_catalogue():
    """PURE: every figure `/accounts/device-purchases` prints that is Σ(one field) over its own rows.

    Three row shapes and therefore three entries' worth of `rows`, which is why
    `purchase_drilldowns` below folds each against the list it actually came out of rather than
    against one shared list.
    """
    store = {"row_label": _store_label, "keys": ("store", "company"), "fields": _store_fields,
             "schema": SCHEMA}
    return (
        dict(store, key=DEVICE_AMOUNT, total=DEVICE_AMOUNT, label="Device purchases",
             value=_column("amount"), column="total", tables=(INVOICE_LINES,),
             unit=report_drilldown.UNIT_MONEY, note=_PURCHASE_MONEY_NOTE),
        dict(store, key=SERIALISED_UNITS, total=SERIALISED_UNITS, label="Units billed (serialised)",
             value=_column("serialised_units"), tables=(INVOICE_UNITS,),
             unit=report_drilldown.UNIT_COUNT, note=_PURCHASE_UNIT_NOTE),
    )


def non_device_catalogue():
    """PURE: the non-device money, folded over the non-device LINE rows — a different row shape with
    a different basis, which the payload names itself."""
    return (
        dict(key=NON_DEVICE_AMOUNT, total=NON_DEVICE_AMOUNT, label="Not devices (reported, excluded)",
             row_label=_item_label, keys=("name",), fields=_lines_fields, schema=SCHEMA,
             value=_column("amount"), column="total", tables=(INVOICE_LINES,),
             unit=report_drilldown.UNIT_MONEY, note=_NON_DEVICE_NOTE),
    )


def unresolved_catalogue():
    """PURE: the spend whose location reached no store — folded over those rows themselves."""
    return (
        dict(key=UNRESOLVED_AMOUNT, total=UNRESOLVED_AMOUNT, label="Unresolved location spend",
             row_label=_location_label, keys=("location",), fields=_lines_fields, schema=SCHEMA,
             value=_column("amount"), column="total", tables=(INVOICE_LINES,),
             unit=report_drilldown.UNIT_MONEY, note=_UNRESOLVED_NOTE),
    )


def unresolved_total(payload):
    """PURE: the unresolved-location figure the PAGE prints, dereferenced from the rows the page
    reduces rather than recomputed in a second way.

    The producer does not fold this one into `totals` — the page reduces the rows itself — so the
    figure is taken from the same list the panel is built over. `None` when the report served no such
    list at all, which keeps `served_tiles`' first refusal in charge of it: a figure the report did
    not produce gets no panel rather than a $0.00 that asserts one.
    """
    rows = ((payload or {}).get("unresolved") or {}).get("store_not_mapped") or ()
    rows = [r for r in rows if r]
    if not rows:
        return None
    return report_drilldown.money(sum(report_drilldown.money(r.get("amount") or 0) for r in rows))


def purchase_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /account/device-purchases`.

    The keys are the payload's own field names where it has one, so the page can offer a link beside
    every figure a row list exists for. Each figure is the payload's own total dereferenced — a panel
    can never be a second opinion about the figure above it.
    """
    payload = payload or {}
    totals = dict(payload.get("totals") or {})
    cap = cap or report_drilldown.ROW_CAP
    out = {}
    out.update(_served(purchases_catalogue(), [r for r in (payload.get("by_store") or ()) if r],
                       {k: totals.get(k) for k in (DEVICE_AMOUNT, SERIALISED_UNITS)
                        if k in totals}, cap))
    out.update(_served(non_device_catalogue(),
                       [r for r in (payload.get("non_device_lines") or ()) if r],
                       {NON_DEVICE_AMOUNT: totals.get(NON_DEVICE_AMOUNT)}
                       if NON_DEVICE_AMOUNT in totals else {}, cap))
    out.update(_served(unresolved_catalogue(),
                       [r for r in ((payload.get("unresolved") or {}).get("store_not_mapped") or ())
                        if r],
                       {UNRESOLVED_AMOUNT: unresolved_total(payload)}, cap))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE PAYABLE CATALOGUE — the same store row, judged against a payment date in another relation.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

PAYABLE_AMOUNT = "payable_amount"
PAYABLE_DEVICES = "payable_devices"
PAID_AMOUNT = "paid_amount"
NON_DEVICE_TOTAL = "non_device_amount"


def payable_catalogue():
    """PURE: every figure `/accounts/device-payable` prints that is Σ(one field) over its store rows.

    `match_rate` is deliberately absent — a rate gets no panel (§CORE-17.3h) — and so are the
    composed-at-the-total figures the module docstring lists.
    """
    store = {"row_label": _store_label, "keys": ("store", "company"), "fields": _store_fields,
             "schema": SCHEMA}
    #: BOTH relations on every row of these three, and the order is the reader's: WHICH units the
    #: distributor had billed by the day asked for comes off the serialised invoice rows, and BOTH
    #: the amount owed on a unit and the day it was settled come off the unit ledger. Naming only
    #: one would make half the judgement invisible. This is the report's whole method and it is why
    #: these figures are a different PROVENANCE from the purchase money above, on the same row and
    #: in the same unit (§CORE-17.3n).
    billed_and_settled = (INVOICE_UNITS, UNIT_LEDGER)
    return (
        dict(store, key=PAYABLE_AMOUNT, total=PAYABLE_AMOUNT, label="Device payable",
             value=_column("payable_amount"), tables=billed_and_settled,
             unit=report_drilldown.UNIT_MONEY, note=_PAYABLE_MONEY_NOTE),
        dict(store, key=PAYABLE_DEVICES, total=PAYABLE_DEVICES, label="Devices still unpaid",
             value=_column("payable_devices"), tables=billed_and_settled,
             unit=report_drilldown.UNIT_COUNT, note=_PAYABLE_UNIT_NOTE),
        dict(store, key=PAID_AMOUNT, total=PAID_AMOUNT, label="Already paid by this date",
             value=_column("paid_amount"), tables=billed_and_settled,
             unit=report_drilldown.UNIT_MONEY, note=_PAID_NOTE),
    )


def payable_non_device_catalogue():
    """PURE: the non-device money on the payable report, folded over its own item rows. ITS BASIS IS
    THE PAYLOAD'S OWN AND IT IS NOT A PAYABLE: these items carry no serial and no per-line payment
    date, so the report states in words that whether each was settled cannot be evidenced, and the
    panel's note repeats the report's own sentence rather than inventing a softer one."""
    return (
        dict(key=NON_DEVICE_TOTAL, total=NON_DEVICE_TOTAL,
             label="Non-device items billed (separate basis)",
             row_label=_item_label, keys=("name",), fields=_lines_fields, schema=SCHEMA,
             value=_column("amount"), column="total", tables=(INVOICE_LINES,),
             unit=report_drilldown.UNIT_MONEY, note=_NON_DEVICE_NOTE),
    )


def payable_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /account/device-payable`.

    A date the per-unit ledger does not cover comes back `coverage.state != 'measured'`, and the
    producer then serves `None` for every total and NO store rows — so nothing survives
    `served_tiles` and this returns `{}`. That is the right answer and it needs no branch here: a
    report that prints no figure has no figure to explain (§CORE-17.3i — an absence is not a zero).
    """
    payload = payload or {}
    totals = dict(payload.get("totals") or {})
    non_device = dict(payload.get("non_device") or {})
    cap = cap or report_drilldown.ROW_CAP
    out = {}
    out.update(_served(payable_catalogue(), [r for r in (payload.get("by_store") or ()) if r],
                       {k: totals.get(k)
                        for k in (PAYABLE_AMOUNT, PAYABLE_DEVICES, PAID_AMOUNT) if k in totals},
                       cap))
    out.update(_served(payable_non_device_catalogue(),
                       [r for r in (non_device.get("by_item") or ()) if r],
                       {NON_DEVICE_TOTAL: non_device.get("amount")}
                       if "amount" in non_device else {}, cap))
    return out


#: EVERY FIGURE KEY THIS DECLARATION CAN SERVE, derived from the catalogues it serves them from
#: rather than listed a second time.
PURCHASE_KEYS = tuple(t["key"] for t in
                      (purchases_catalogue() + non_device_catalogue() + unresolved_catalogue()))
PAYABLE_KEYS = tuple(t["key"] for t in (payable_catalogue() + payable_non_device_catalogue()))
