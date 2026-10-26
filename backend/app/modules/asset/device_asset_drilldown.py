"""WHERE DID THE DEVICE-ASSET MONEY COME FROM — the declaration for the seven asset reports
(§CORE-17, batch 12).

  /commcalc/asset                  Asset Ledger — the five headline cards over the distributor's
                                   unit ledger.
  /commcalc/asset/dashboard        Asset Charges Dashboard — five charge-group tiles and the Total
                                   Loss headline.
  /commcalc/asset/on-inventory     On-Inventory by Store — unsold exposure per store, in aging bands.
  /commcalc/asset/owed-weekly      Weekly Owed to Distributor — what bills on one Friday, per store.
  /commcalc/asset/inventory-recon  On-Inventory vs the POS snapshot — unit counts, per store.
  /commcalc/asset/borrowed         Inter-store Borrowed Money — one row per loan.
  /commcalc/asset/lending          Asset Lending (Distributor PayGo) — one row per weekly batch.
  /commcalc/asset/aging            Inventory Aging — the aging bands, at DEVICE grain (batch 25).

HOW MANY GRAINS THIS FAMILY REALLY HAS — MEASURED, NOT ASSUMED
──────────────────────────────────────────────────────────────
The batch was handed over as "probably one or two grains (a device, and a store-week of devices
owed)". Reading the seven producers says otherwise, and the honest count is **six**, because a grain
is the row a figure is Σ OVER, not the subject the report is about:

  1. a STATUS / CATEGORY bucket of the ledger   `/commcalc/asset` folds its cards over its own
                                                By-Status and By-Category tables
  2. a STORE × CATEGORY aggregate of the ledger `/commcalc/asset/dashboard` is totals-only: the
                                                Postgres aggregate `asset_charges_summary` returns
                                                these rows and no device rows exist to fold
  3. a STORE's unsold exposure                  `/commcalc/asset/on-inventory`
  4. a STORE on ONE billing Friday              `/commcalc/asset/owed-weekly`
  5. a STORE compared between TWO inventories   `/commcalc/asset/inventory-recon`
  6. an inter-store LOAN                        `/commcalc/asset/borrowed`
  7. a weekly PayGo BATCH                       `/commcalc/asset/lending`
  8. an unsold DEVICE                           `/commcalc/asset/aging` — added by batch 25, and the
                                                SEVENTH grain: every producer above it is keyed by a
                                                store, a loan or a batch, and this report's bands
                                                are Σ over one row per unsold IMEI. See §8.

Four of those are keyed by a store and are still four grains: a store's unsold exposure, a store's
Friday bill, a store's unit disagreement with the POS and a store's share of one charge group are
four different populations of rows, measured in three different units. One catalogue stretched over
them would have to lie about three of its reports — and `tie_out` would say so out loud, which is
exactly how we would find out.

ONE MODULE, SEVEN CATALOGUES, AND WHY THAT IS NOT THE SIBLING DEFECT
────────────────────────────────────────────────────────────────────
Same argument as §CORE-17.3k's three commission reports, one subsystem further out: what these seven
share is not a row shape but the thing a drill-down exists for — *which stored relation was this
device-asset figure read out of*. There are exactly **three** answers in this family and they live
here once, dereferenced from `data_lineage_registry`:

  · the distributor's unit LEDGER (`ASSET_LEDGER`) — five of the seven reports;
  · the POS unit SNAPSHOT (`POS_INVENTORY_SNAPSHOT`) — the recon's other side, named per row, so a
    store the POS file never mentioned names only the ledger and vice versa;
  · the FINANCING records the platform writes itself — the two borrowing tables and the PayGo
    batches the portal sweep lands.

Seven files would be seven answers to one question, which is the duplicate defect `CLAUDE.md`
forbids arriving one report at a time.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **A FIGURE COMPOSED FROM TWO OTHER TILES.** Total Loss is `appeals + rma`, and the PayGo Grand
    Total is `lifetime + open week`. No set of records adds up to either, so a tie-out against one
    is a check that cannot fail (§CORE-17.3h as batch 10 extended it). Both halves of both get
    panels instead, and the screen says so.
  · **A LEAF.** "Outstanding now" and "Devices on the open week" are ONE stored PayGo batch's own
    two columns, with nothing under them on this report (the batch's invoice list is a different
    endpoint). Batch 10's leaf ruling: a figure that is already the leaf gets no panel.
  · **A COUNT WHOSE LIST IS ON SCREEN.** The ledger's device count, the recon's mismatched-store
    count, the borrowing count and the on-inventory store count are cardinalities of tables printed
    immediately below them (batch 11 precedent).
  · **A ROLLUP THAT ALREADY HAS A DRILL OF ITS OWN.** The PayGo By-year rows filter the weekly
    ledger to exactly their batches on click, and the Asset Ledger's By-Category rows already open
    to their device rows through `GET /asset/category-detail`. A second answer beside an existing
    one is the defect, not the fix.

AND A DIFFERENCE THAT *IS* DRILLABLE, BY THE §CORE-17.3k TEST. The recon's `total_abs_diff` is
Σ|asset − POS| folded per store × device type and then added up per store, so the rows and the figure
come from different places and CAN disagree. A loan's `outstanding` is likewise `amount − repaid`
per loan — while the ENDPOINT serves `Σamount − Σrepaid`, subtracted at the total. The panel is
folded over the rows' own differences on purpose: that makes the tie-out a real comparison of the two
ways of getting there, and it is the one on this batch most likely to catch a rounding divergence
rather than assert one away.

LIVE DEFECTS THESE REPORTS HAVE TODAY — REPORTED, NOT FIXED, AND NO FIGURE MOVED
───────────────────────────────────────────────────────────────────────────────
1. **The Charges Dashboard's RMA tile can only ever read "0 items".** The page builds that tile as
   `{count: 0, owed: d.total_loss.rma}` because `RMA` is not one of `CHARGE_GROUPS`' keys, so
   `data.groups['rma']` is undefined and there is no count to show. The dollar figure beside it is
   real. NOT fixed here: putting the true count on the tile changes a figure the owner reads. The
   panel's `rows_total_count` states how many store-RMA rows are behind the money, which is the
   honest half that costs nothing.
2. **The On-Inventory ↔ POS recon drops every device whose model does not classify.**
   `_asset_oninv_by_bucket` skips a row when `inv_bucket(device_model)` returns nothing, so the ASSET
   side of the comparison is silently short and the gap reads as missing POS stock. The count of
   dropped devices now travels per store as `asset_unclassified` and is shown on the drill row,
   NOT counted — the figure is untouched and the reader can finally see the hole.
3. **"On Inventory" is matched case-SENSITIVELY by `GET /asset/summary` and case-INSENSITIVELY by the
   two reads beside it.** `counts_as_on_inventory` below is the summary endpoint's own predicate,
   named once so its tile and its drill-down cannot disagree. It is deliberately NOT unified with the
   two `ilike` reads: making the summary case-insensitive would move the On-Inventory card for any
   tenant whose file spells the category differently, which is a decision about a figure.
4. **A PayGo batch with no amount is folded into Lifetime Billed as $0.00.** `float(r['amount'] or 0)`
   coerces it. The panel shows such a batch WITHOUT a figure and does not count it; a null adds zero
   to a sum either way, so the figure is identical to the cent and only the row changes.

AN EMPTY REPORT DRILLS TO NOTHING. `tiles()` skips a figure whose total key the report did not
produce, but a report that produced its totals over NO ROWS AT ALL (an unloaded ledger, a Friday
nothing bills on, a tenant with no loans) would still get a panel asserting a figure behind an empty
list — which ties only by accident and reads like a bug. Every entry point below therefore returns an
empty mapping in that case, which is `tiles()`'s own rule applied to the row list instead of the
total.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: every amount it carries was
handed to it. PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product /
store name (RULE TWO).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3l.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown
# The ONE home for the shape and the provenance of an unsold-device row (§COMMCALC-28). Imported
# for its two NAME constants only — the stamp it writes and the value it writes — so this module
# holds no second copy of either. Pure, like everything else here.
from app.modules.payables import unsold_inventory as _unsold

#: THE THREE PROVENANCE ANSWERS THIS FAMILY HAS, dereferenced from the one home and never spelled
#: here as literals.
ASSET_LEDGER = _lineage.ASSET_LEDGER
POS_INVENTORY = _lineage.POS_INVENTORY_SNAPSHOT
STORE_BORROWINGS = _lineage.STORE_BORROWINGS
STORE_BORROWING_PAYMENTS = _lineage.STORE_BORROWING_PAYMENTS
PAYGO_BATCHES = _lineage.PAYGO_BATCHES
#: The carrier-agnostic per-IMEI payable ledger — the OTHER relation the aging report reads since
#: §COMMCALC-28, for a tenant that has no consignment ledger of its own. Dereferenced, never spelled.
DEVICE_PAYABLE_LEDGER = _lineage.DEVICE_PAYABLE_LEDGER

#: Every relation on this batch lives in the commcalc schema, so one schema serves every panel —
#: `tile_panel` takes ONE schema per tile and a row whose tables spanned two would have to name none.
SCHEMA = "commcalc"

#: The ledger columns a figure on these reports is read out of. Named so a panel can say WHICH column
#: held the money rather than only which table.
COL_OWED = "owed_to_vip"
COL_FEES = "commissions"
COL_REIMBURSED = "reimbursement"

#: `GET /asset/summary`'s own status value for a device still owed for. Dereferenced by the endpoint,
#: so the Open-Balance card and its drill-down count the same rows by construction.
OPEN_STATUS = "Open"

#: The charge GROUP whose owed total IS the "denied appeals" half of the Total Loss headline, and
#: the charge CATEGORY the RMA half is folded over. Both are the endpoint's own keys, named here
#: once and dereferenced by it, so the headline and its two panels cannot come to mean different
#: rows. The charge category the Charges Dashboard's RMA tile is folded over It is deliberately NOT one of
#: `CHARGE_GROUPS`' keys (see live defect 1) — the tile reads the Total Loss block instead.
APPEALS_GROUP = "appeals"
RMA_CATEGORY = "RMA"

#: How far under the owed figure a reimbursement has to fall before the shortfall is a loss, as
#: `GET /asset/charges-summary` has always had it. Dereferenced rather than re-typed, so the tile and
#: its evidence cannot drift by a cent.
RMA_SHORTFALL_TOLERANCE = 0.01


# ── NOTES — what each panel is, in the words the screen already uses ─────────────────────────────

_LEDGER_NOTE = (
    "One row per status in the ledger, which is the By Status table on this page. The card is Σ of "
    "the rows it counts; the rows it does not count are shown for context, because a card that "
    "counts one status is only readable beside the others.")

_LEDGER_CATEGORY_NOTE = (
    "One row per ledger category, which is the By Category table on this page. Clicking a category "
    "there already opens its device rows — this panel says which categories the card was folded "
    "over and which it left out.")

_CHARGES_NOTE = (
    "One row per store in this charge group, as the ledger aggregate returned it. Every row was read "
    "out of the distributor's unit ledger for the period and filters on the bar above.")

_RMA_NOTE = (
    "One row per store with returned devices. The figure is the net loss per store — the whole owed "
    "amount where nothing was reimbursed, the shortfall where the reimbursement fell short, and a "
    "measured $0.00 where it did not. Those zeros are real measurements and are counted.")

_ON_INVENTORY_NOTE = (
    "One row per store, as the per-store table below shows it (spelling variants of one address are "
    "merged into one row there and here). The aging bands are measured as of today, which is why a "
    "band total can move without the ledger changing.")

_OWED_WEEKLY_NOTE = (
    "One row per store billing on this Friday. The device count for each path travels on the row "
    "beside the money, and the device table below lists the devices themselves.")

_RECON_NOTE = (
    "One row per store, and the figure is Σ|ledger − POS| over the five device types — folded per "
    "store and per type, then added up, which is what makes this difference drillable at all. A "
    "store the POS file never mentioned names only the ledger, and one the ledger holds nothing for "
    "names only the POS snapshot. Devices whose model does not classify into one of the five types "
    "are NOT on the ledger side of this comparison; each row says how many it is missing.")

_BORROWED_NOTE = (
    "One row per inter-store loan, addressed by its own id — which is also the key its paybacks "
    "carry in the payments table. The repaid figure is read out of those paybacks, so a loan nobody "
    "has paid against reads $0.00 from a table that really was looked up for it.")

_OUTSTANDING_NOTE = (
    "One row per loan, each carrying its OWN amount minus its OWN paybacks. The report's headline "
    "subtracts the two totals instead, so this tie-out compares the two ways of reaching the same "
    "number rather than restating one of them.")

_AGING_NOTE = (
    "One row per unsold device, as the band tables below list them — each naming the store, the "
    "market, the day it was purchased and how many days it has been held. Every band is measured "
    "AS OF TODAY, so a band total moves as days pass even when no ledger changed, and a date filter "
    "narrows which devices are INCLUDED rather than when the age was taken. Each row also says "
    "which relation it was read out of: a consignment device comes off the distributor's unit "
    "ledger and a device the platform owes for off the per-device payable ledger.")

_LENDING_NOTE = (
    "One row per approved weekly billing batch from the distributor's lending portal. A batch the "
    "portal reported without an amount is shown WITHOUT a figure and is not counted — a missing "
    "amount is not a week that billed nothing.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §1  THE ASSET LEDGER'S FIVE CARDS — Σ over its own By-Status / By-Category tables.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def counts_as_on_inventory(category):
    """`GET /asset/summary`'s predicate for the On-Inventory card — the ONE home for it, so the card
    and its drill-down count the same categories by construction.

    Case-SENSITIVE, byte-for-byte what that endpoint has always computed. The two `ilike` reads on the
    other asset reports are case-INSENSITIVE, and that divergence is REPORTED (live defect 3) rather
    than resolved here: resolving it would move this card for a tenant whose file spells the category
    differently, which is a decision about a figure. PURE."""
    return "On Inventory" in str(category or "")


def status_rows(summary):
    """The report's own By Status table, as drill rows. Ordered the way the screen orders it — by
    open balance, descending — so the panel and the table read the same way."""
    out = [dict(v, status=k) for k, v in ((summary or {}).get("by_status") or {}).items()]
    out.sort(key=lambda r: -report_drilldown.money(r.get("owed")))
    return out


def category_rows(summary):
    """The report's own By Category table, as drill rows."""
    out = [dict(v, category=k) for k, v in ((summary or {}).get("by_category") or {}).items()]
    out.sort(key=lambda r: -report_drilldown.money(r.get("owed")))
    return out


def _status_fields(r):
    return {"detail": "%s device(s)" % (r or {}).get("count"),
            "reimbursed": (r or {}).get("reimbursed"), "fees": (r or {}).get("fees")}


def _category_fields(r):
    return {"detail": "%s device(s)" % (r or {}).get("count"), "fees": (r or {}).get("fees")}


def _is_open_status(r):
    return str((r or {}).get("status") or "") == OPEN_STATUS


def _is_on_inventory_row(r):
    return counts_as_on_inventory((r or {}).get("category"))


def ledger_catalogue():
    """The five headline cards, declared. `total` names the key in the endpoint's own response, so a
    figure it did not produce has no panel instead of a panel asserting one.

    `rows` names WHICH of the report's two tables each card was folded over — the one thing a plain
    `tiles()` catalogue cannot express, because a report with two row families has two populations
    and a panel over the wrong one would be proof of a different number."""
    status_common = {"rows": "status", "row_label": "status", "keys": ("status",),
                     "fields": _status_fields, "tables": (ASSET_LEDGER,), "note": _LEDGER_NOTE}
    return (
        {"key": "total_fees", "label": "Distributor fees collected", "total": "total_fees",
         "value": "fees", "column": COL_FEES, **status_common},
        {"key": "total_open_balance", "label": "Open balance owed", "total": "total_open_balance",
         "value": "owed", "column": COL_OWED, "in_figure": _is_open_status, **status_common},
        {"key": "total_reimbursed", "label": "Total reimbursed", "total": "total_reimbursed",
         "value": "reimbursed", "column": COL_REIMBURSED, **status_common},
        {"key": "total_owed_alltime", "label": "All-time owed", "total": "total_owed_alltime",
         "value": "owed", "column": COL_OWED, **status_common},
        {"key": "on_inventory", "label": "On inventory (not yet sold)", "total": "on_inventory",
         "value": "owed", "column": COL_OWED, "rows": "category", "row_label": "category",
         "keys": ("category",), "fields": _category_fields, "tables": (ASSET_LEDGER,),
         "in_figure": _is_on_inventory_row, "note": _LEDGER_CATEGORY_NOTE},
    )


def ledger_drilldowns(summary, *, cap=None):
    """PURE: one panel per headline card, over the rows the report already serves.

    A card whose figure the endpoint did not produce — an unloaded ledger returns `{"loaded": False}`
    and nothing else — gets no panel at all."""
    summary = summary or {}
    if not summary.get("loaded"):
        return {}
    families = {"status": status_rows(summary), "category": category_rows(summary)}
    out = {}
    for spec in ledger_catalogue():
        spec = dict(spec)
        key, label = spec.pop("key"), spec.pop("label")
        total_key = spec.pop("total")
        rows = families[spec.pop("rows")]
        amount = summary.get(total_key)
        if amount is None or not rows:
            continue
        out[key] = report_drilldown.tile_panel(
            label, amount, rows, schema=SCHEMA, cap=cap or report_drilldown.ROW_CAP, **spec)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §2  THE CHARGES DASHBOARD — five group tiles over the ledger aggregate's own rows.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def rma_net_loss(row):
    """THE PER-ROW RMA NET LOSS, named once so the tile and its evidence are one arithmetic.

    `GET /asset/charges-summary` folded this inline and the drill-down would have had to restate it —
    the copied-fact defect in its smallest form. The rule is unchanged: the whole owed amount where
    nothing came back, the shortfall where what came back fell short, and zero where it did not.
    Returns a RAW float, deliberately: the endpoint's total is one `round` over the raw sum, exactly
    as it has always been, so factoring this out cannot move the figure by a cent. PURE."""
    owed = float((row or {}).get("owed") or 0)
    reimb = float((row or {}).get("reimb") or 0)
    if reimb <= 0:
        return owed
    if reimb < owed - RMA_SHORTFALL_TOLERANCE:
        return owed - reimb
    return 0.0


def is_rma_row(row):
    """Is this aggregate row the returned-device category the Total Loss headline counts? PURE."""
    return str((row or {}).get("category") or "") == RMA_CATEGORY


def _agg_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (r.get("store") or "(no store)", r.get("category"))
                      if str(x or "").strip())


def _agg_fields(r):
    r = r or {}
    return {"market": r.get("market"), "detail": "%s item(s)" % (r.get("cnt") or 0),
            "reimbursed": r.get("reimb")}


def charges_drilldowns(agg, groups, total_loss, *, group_of, cap=None):
    """PURE: a panel per charge-group tile and one per half of the Total Loss headline.

    `agg` is the Postgres aggregate's own rows — one per store × category — and they are the only rows
    this report has: `/asset/charges-summary` is totals-only by design, so there are no device rows to
    fold and a store-category row is the honest grain (§CORE-17.3a's "the report's own rows").

    `group_of` is the router's `_cat_to_group`, passed in rather than copied: which categories make up
    a charge group is that module's declaration and a second copy here would drift from it.

    THE TOTAL LOSS TOTAL GETS NO KEY. It is `appeals + rma`, composed from the two figures below it,
    so no set of rows adds up to it (§CORE-17.3h as batch 10 extended it). Its two halves open."""
    cap = cap or report_drilldown.ROW_CAP
    rows = [r for r in (agg or []) if r]
    groups = groups or {}
    panels = {}
    common = {"row_label": _agg_label, "keys": ("store", "category"), "fields": _agg_fields,
              "tables": (ASSET_LEDGER,), "column": COL_OWED, "schema": SCHEMA}
    for gkey, g in groups.items():
        amount = (g or {}).get("owed")
        mine = [r for r in rows if group_of(str(r.get("category") or "")) == gkey]
        if amount is None or not mine:
            continue
        panels["group_%s" % gkey] = report_drilldown.tile_panel(
            (g or {}).get("label") or gkey, amount, mine, value="owed",
            subject=(g or {}).get("label"), subject_kind="charge group",
            note=_CHARGES_NOTE, cap=cap, **common)
    appeals = (total_loss or {}).get("appeals")
    appeals_rows = [r for r in rows if group_of(str(r.get("category") or "")) == APPEALS_GROUP]
    if appeals is not None and appeals_rows:
        panels["loss_appeals"] = report_drilldown.tile_panel(
            "Denied appeals — direct loss", appeals, appeals_rows, value="owed",
            note=_CHARGES_NOTE, cap=cap, **common)
    rma = (total_loss or {}).get("rma")
    rma_rows = [r for r in rows if is_rma_row(r)]
    if rma is not None and rma_rows:
        panels["loss_rma"] = report_drilldown.tile_panel(
            "Returned devices — net loss", rma, rma_rows, value=rma_net_loss,
            note=_RMA_NOTE, cap=cap, **common)
    return panels


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §3  ON-INVENTORY BY STORE — six figures over the per-store table the screen prints.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _store_fields(r):
    r = r or {}
    return {"market": r.get("market"), "detail": "%s device(s)" % (r.get("count") or 0),
            "under45_owed": r.get("under45_owed"), "unknown_age_count": r.get("unknown_age_count")}


def on_inventory_catalogue():
    """Every figure the On-Inventory page shows above or below its table, declared.

    `store_count` is deliberately absent: it is the cardinality of the table printed immediately
    below it (batch 11 precedent), and a panel listing the stores to prove how many stores there are
    is a tautology dressed as evidence."""
    common = {"row_label": "store", "keys": ("store",), "fields": _store_fields,
              "tables": (ASSET_LEDGER,), "note": _ON_INVENTORY_NOTE}
    _money = dict(common, column=COL_OWED)
    _count = dict(common, unit=report_drilldown.UNIT_COUNT)
    return (
        {"key": "total_phones_outstanding", "label": "Total phones outstanding",
         "total": "total_phones_outstanding", "value": "count", **_count},
        {"key": "total_amount", "label": "Total amount owed (unsold exposure)",
         "total": "total_amount", "value": "owed", **_money},
        {"key": "warn_owed", "label": "45–60 day band — owed", "total": "warn_owed",
         "value": "warn_owed", **_money},
        {"key": "missed_owed", "label": "Past 60 days — owed", "total": "missed_owed",
         "value": "missed_owed", **_money},
        {"key": "unknown_age_owed", "label": "No usable acquired date — owed",
         "total": "unknown_age_owed", "value": "unknown_age_owed", **_money},
        {"key": "zero_count", "label": "Devices owing nothing", "total": "zero_count",
         "value": "zero_count", **_count},
    )


def on_inventory_drilldowns(out, *, cap=None):
    """PURE: one panel per figure, over `GET /asset/on-inventory-by-store`'s OWN rows and totals."""
    out = out or {}
    rows = out.get("stores") or []
    if not rows:
        return {}
    return report_drilldown.tiles(on_inventory_catalogue(), rows, out.get("totals") or {},
                                  schema=SCHEMA, cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §4  WEEKLY OWED TO DISTRIBUTOR — three figures over the By-Store table, for one billing Friday.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def owed_weekly_rows(out):
    """The report's own By-Store table, each row stamped with the billing Friday it is about — so a
    drill row's keys address the ledger rows exactly as the report selected them, and a panel can
    never be read as being about a different week."""
    out = out or {}
    friday = out.get("thursday")
    return [dict(s, billing_friday=friday) for s in (out.get("by_store") or []) if s]


def owed_weekly_totals(out):
    """The three figures the KPI strip shows, flattened out of the endpoint's own nested block. A
    dereference, not a recomputation: nothing here adds anything up."""
    due = (out or {}).get("due_this_week") or {}
    return {
        "total_owed": ((due.get("total") or {}).get("owed")),
        "sold_owed": ((due.get("sold") or {}).get("owed")),
        "aging_owed": ((due.get("aging") or {}).get("owed")),
    }


def _week_fields(r):
    r = r or {}
    return {"market": r.get("market"),
            "detail": "%s sold · %s aged" % (r.get("sold_count") or 0, r.get("aging_count") or 0)}


def owed_weekly_catalogue():
    """The three money figures on the strip. The device COUNTS beside them get no panel: every panel
    row already carries both of them in `detail`, which is the same reason batch 11 refused the
    carrier-vs-pay rep counts."""
    common = {"row_label": ("store", "billing_friday"), "keys": ("store", "billing_friday"),
              "fields": _week_fields, "tables": (ASSET_LEDGER,), "column": COL_OWED,
              "note": _OWED_WEEKLY_NOTE}
    return (
        {"key": "total_owed", "label": "Total due this Friday", "total": "total_owed",
         "value": "total_owed", **common},
        {"key": "sold_owed", "label": "Sold phones — billed this week", "total": "sold_owed",
         "value": "sold_owed", **common},
        {"key": "aging_owed", "label": "Aged past 60 days, never sold", "total": "aging_owed",
         "value": "aging_owed", **common},
    )


def owed_weekly_drilldowns(out, *, cap=None):
    """PURE: one panel per KPI, over the By-Store rows the endpoint already serves."""
    rows = owed_weekly_rows(out)
    if not rows:
        return {}
    return report_drilldown.tiles(owed_weekly_catalogue(), rows, owed_weekly_totals(out),
                                  schema=SCHEMA, cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §5  ON-INVENTORY ↔ POS RECON — a difference, folded per store and per device type.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def recon_tables(r):
    """WHICH SIDE OF THE COMPARISON THIS STORE'S ROW WAS READ OUT OF, per row.

    A store the POS file never mentioned names only the ledger; a store the ledger holds no unsold
    device for names only the POS snapshot. Both flags are the producer's own
    (`in_asset` / `in_b2b`), so this reads its verdict rather than forming a second one. PURE."""
    r = r or {}
    out = []
    if r.get("in_asset"):
        out.append(ASSET_LEDGER)
    if r.get("in_b2b"):
        out.append(POS_INVENTORY)
    return tuple(out)


def _recon_fields(r):
    r = r or {}
    cats = r.get("categories") or {}
    diffs = " ".join("%s %+d" % (k, (v or {}).get("diff") or 0)
                     for k, v in cats.items() if (v or {}).get("diff"))
    return {"market": r.get("market"), "detail": diffs or "every device type agrees",
            # LIVE DEFECT 2, VISIBLE ON THE ROW: devices the ledger holds whose model does not
            # classify into one of the five types are on NEITHER side of the comparison.
            "unclassified_on_ledger": r.get("asset_unclassified")}


def inventory_recon_catalogue():
    """The one figure on this report that is Σ over its own rows.

    `mismatch_stores` gets no panel: it counts the rows of the table printed below it, which
    highlights exactly those rows and sorts them to the top. The POS snapshot DATE is not a figure."""
    return (
        {"key": "total_abs_diff", "label": "Total unit disagreement (|ledger − POS|)",
         "total": "total_abs_diff", "value": "total_abs_diff", "row_label": "store",
         "keys": ("store",), "fields": _recon_fields, "tables": recon_tables,
         "unit": report_drilldown.UNIT_COUNT, "note": _RECON_NOTE},
    )


def inventory_recon_drilldowns(out, *, cap=None):
    """PURE: the unit-disagreement panel, over the per-store rows the endpoint already serves.

    NO PANEL AT ALL UNTIL THE POS SIDE IS LOADED. With no snapshot the "difference" is the ledger
    minus nothing, which is not a comparison — and a panel tying that out to itself would read like
    proof that the two inventories agree about a store the POS has never reported on."""
    out = out or {}
    rows = out.get("rows") or []
    if not out.get("b2b_loaded") or not rows:
        return {}
    return report_drilldown.tiles(inventory_recon_catalogue(), rows,
                                  {"total_abs_diff": out.get("total_abs_diff")}, schema=SCHEMA,
                                  cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §6  INTER-STORE BORROWED MONEY — three figures over the loans themselves.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _loan_label(r):
    r = r or {}
    return "%s ← %s · %s" % (r.get("borrower_store") or "(no borrower)",
                             r.get("lender_store") or "(no lender)",
                             r.get("borrowed_date") or "(no date)")


def _loan_fields(r):
    r = r or {}
    return {"market": r.get("market"),
            "detail": "%s payback(s)%s" % (len(r.get("payments") or []),
                                           " · settled" if r.get("settled") else ""),
            "borrowed": r.get("amount"), "repaid": r.get("repaid")}


def borrowings_catalogue():
    """The three money figures on the strip.

    The borrowing COUNT gets no panel — the ledger of every loan is printed below it. The "who owes
    whom" and "net position by store" tables get none either: both re-partition exactly these loans,
    and the loan each of their rows is made of is on the same screen with its borrower, its lender
    and its outstanding amount."""
    common = {"row_label": _loan_label, "keys": ("id",), "fields": _loan_fields, "note": _BORROWED_NOTE}
    return (
        {"key": "total_borrowed", "label": "Total borrowed", "total": "total_borrowed",
         "value": "amount", "tables": (STORE_BORROWINGS,), "column": "amount", **common},
        {"key": "total_repaid", "label": "Total repaid", "total": "total_repaid",
         "value": "repaid", "tables": (STORE_BORROWING_PAYMENTS,), "column": "amount", **common},
        {"key": "total_outstanding", "label": "Outstanding", "total": "total_outstanding",
         "value": "outstanding", "tables": (STORE_BORROWINGS, STORE_BORROWING_PAYMENTS),
         **{k: v for k, v in common.items() if k != "note"}, "note": _OUTSTANDING_NOTE},
    )


def borrowings_drilldowns(out, *, cap=None):
    """PURE: one panel per figure, over `GET /asset/borrowings`' own loan rows and totals."""
    out = out or {}
    rows = out.get("borrowings") or []
    if not rows:
        return {}
    return report_drilldown.tiles(borrowings_catalogue(), rows, out,
                                  schema=SCHEMA, cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §7  ASSET LENDING (DISTRIBUTOR PAYGO) — the lifetime figures over the weekly batches.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def devices_lifetime(summary):
    """Σ(devices on every approved weekly batch) — the figure the LENDING PAGE used to reduce for
    itself in TypeScript.

    One fold, served (§CORE-17.3d): a tile the client folds has no server-folded evidence, and two
    folds of one figure are two numbers hoping to agree. The arithmetic is the page's own, so the
    figure is identical. PURE."""
    return sum(int((r or {}).get("invoice_count") or 0) for r in ((summary or {}).get("history") or []))


def _batch_label(r):
    r = r or {}
    return "week of %s%s" % (r.get("created_on") or "(no date)",
                             (" · %s" % r.get("period")) if r.get("period") else "")


def _batch_fields(r):
    r = r or {}
    return {"detail": "%s device(s)%s" % (r.get("invoice_count") or 0,
                                          (" · %s" % r.get("status")) if r.get("status") else ""),
            "overdue": r.get("amount_overdue"), "batch_id": r.get("vip_payment_id")}


def _batch_amount_recorded(r):
    """Did the portal report an amount for this batch at all? `lifetime_paid` coerces a null to 0.0
    (live defect 4), so a week nobody was billed for and a week whose amount was never reported look
    identical in the total. They do not here — and because a null adds zero to a sum either way, the
    figure is unchanged."""
    return (r or {}).get("amount") is not None


def lending_catalogue():
    """The two LIFETIME figures on the strip.

    "Outstanding now" and "Devices on the open week" get no panel: each is one column of ONE stored
    batch row with nothing under it on this report — batch 10's leaf ruling. "Grand total" gets none
    either: it is the lifetime figure plus the open week, composed from two other tiles."""
    common = {"row_label": _batch_label, "keys": ("vip_payment_id",), "fields": _batch_fields,
              "tables": (PAYGO_BATCHES,), "note": _LENDING_NOTE}
    return (
        {"key": "lifetime_paid", "label": "Lifetime billed", "total": "lifetime_paid",
         "value": "amount", "column": "amount", "in_figure": _batch_amount_recorded,
         "reason": lambda r: None if _batch_amount_recorded(r) else "amount_not_recorded",
         **common},
        {"key": "devices_lifetime", "label": "Devices billed (lifetime)",
         "total": "devices_lifetime", "value": "invoice_count", "column": "invoice_count",
         "unit": report_drilldown.UNIT_COUNT, **common},
    )


def lending_drilldowns(summary, *, cap=None):
    """PURE: one panel per lifetime figure, over the approved batches the endpoint already serves."""
    summary = summary or {}
    rows = summary.get("history") or []
    if not rows:
        return {}
    return report_drilldown.tiles(lending_catalogue(), rows, summary.get("totals") or {},
                                  schema=SCHEMA, cap=cap or report_drilldown.ROW_CAP)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §8  INVENTORY AGING — the bands, at DEVICE grain (batch 25, §CORE-17.3y).
#
# OWNER ASK 2026-10-26, verbatim, quoting the banner on this page: *"124 devices in the 9-12 day
# window - sell before day 12 / $29,598.76 at risk of being billed unsold. These have days left; act
# now., need a drill donw for these , whic store market date range etc"*.
#
# WHY THIS IS A SEVENTH GRAIN AND NOT ONE OF THE SIX. The header's count was taken over the six
# producers this module already declared, and none of them is a device: four are keyed by a STORE,
# one by a loan and one by a weekly batch. This report's bands are Σ OVER DEVICES — one row per
# unsold IMEI — and the grain is the row a figure is summed over, not the subject the report is
# about. Stretching the store-keyed On-Inventory catalogue over it would have declared a store's
# exposure as the evidence for a device's, and `tie_out` would have said so out loud.
#
# WHY THE LEDGER SAID THIS REPORT WAS `flat`, AND WHY THAT PIN WAS WRONG. The 2026-10-24 audit
# recorded `/commcalc/asset/aging` as "already shows the source rows; nothing is summarised above
# them". The first half is true and the second is not: the page shows a band tile per bucket, two
# footer totals and two alert banners above its tables, every one of them a fold the reader had no
# way into — which is exactly the state the audit existed to find. REPORTED here rather than quietly
# re-pinned, because a mis-classified route is how a ratchet stops ratcheting.
#
# ONE PANEL PER BAND, OVER THAT BAND'S OWN DEVICES — and this is the one place this module does not
# hand `tiles()` a single row list. Every other report here folds each tile over the SAME rows, so
# the rows a tile does not count are shown beside it as context (§CORE-17.3l's own rule). A device
# belongs to exactly ONE band, so doing that here would serve the warning band a panel of 1,755
# devices with 124 of them counted — and `ROW_CAP` would then very likely omit the 124 the owner
# clicked on, which is worse than losing the context. So each band tile is folded over its own
# devices and the declaration carries a `rows_of` accessor saying which.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: Which relation a shaped aging row was read out of, dereferenced from the module that stamps it —
#: a literal here would be a second copy of the same fact and would stop matching the day it moved.
_SOURCE_LEDGER = _unsold.SOURCE_LEDGER
_SOURCE_FIELD = _unsold.SOURCE_FIELD

#: The producer's own bucket keys, stamped on every row it returns (`_bucket`). Named once here so
#: the catalogue and the row filter cannot come to mean different populations.
BAND_FIELD = "_bucket"
BAND_SAFE = "under45"
BAND_WARN = "warn"
BAND_MISSED = "missed"
BAND_UNKNOWN_AGE = "unknown_age"
#: Not a band: every unsold device, which is what the footer totals are over.
BAND_ALL = "*"


def band_rows(rows, band):
    """PURE: the devices in ONE band, or every device for `BAND_ALL`.

    Reads the producer's own stamp. Nothing here re-decides which band a device is in — the ladder
    is the carrier's declared window (§COMMCALC-26) and re-applying it would be a second verdict."""
    if band == BAND_ALL:
        return [r for r in (rows or []) if r]
    return [r for r in (rows or []) if r and (r.get(BAND_FIELD) or "") == band]


def device_tables(r):
    """WHICH RELATION THIS DEVICE WAS READ OUT OF, per row.

    This report serves two tenants off two different relations since §COMMCALC-28, so a panel that
    named one of them for every row would be wrong on every row of the other. The producer already
    stamps its answer; this reads it rather than forming a second one. PURE."""
    r = r or {}
    return ((DEVICE_PAYABLE_LEDGER,) if (r.get(_SOURCE_FIELD) or "") == _SOURCE_LEDGER
            else (ASSET_LEDGER,))


def device_keys(r):
    """How a human finds this device row again IN THE RELATION IT CAME FROM.

    The two relations do not spell the IMEI the same way — the consignment ledger's column is
    `esn_imei` and the payable ledger's is `imei` — so the keys are per row too. Naming one for both
    would hand a support query a column that half the rows' table does not have. PURE."""
    r = r or {}
    if (r.get(_SOURCE_FIELD) or "") == _SOURCE_LEDGER:
        return {"imei": r.get("esn_imei"), "store": r.get("store")}
    return {"id": r.get("id"), "esn_imei": r.get("esn_imei")}


def _device_fields(r):
    """WHICH STORE, WHICH MARKET, WHICH DATE — the owner's three words, on every row."""
    r = r or {}
    held = r.get("days_aged")
    held = r.get("days_held") if held is None else held
    acquired = r.get("acquired_date")
    return {"market": r.get("market"),
            "detail": "%s · %s" % (
                ("%s day(s) held" % held) if held is not None else "no usable purchase date",
                ("purchased %s" % str(acquired)[:10]) if acquired else "purchase date not recorded"),
            "device_model": r.get("device_model"),
            "purchased": (str(acquired)[:10] if acquired else None),
            "days_held": held,
            "due_date": r.get("due_date"),
            # The deadline and the verdict the page already prints per row, carried so a reader who
            # opened the band from the banner does not have to go back to the table for them.
            "sell_within_days": r.get("sell_within_days"),
            "days_overdue": r.get("days_overdue"),
            "out_of_pocket": r.get("out_of_pocket")}


def _device_amount(r):
    """The device's exposure, or None where nobody recorded one.

    NOT `float(x or 0)`, which is how the report's own footer reads it: a device whose cost was never
    recorded and a device that genuinely owes nothing are different facts, and coercing the first to
    $0.00 makes a gap in the data look like a measurement. Such a row is SHOWN here, with its reason,
    and is not counted — so the figure it is missing from is visible rather than averaged away."""
    v = (r or {}).get(COL_OWED)
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def aging_totals(out):
    """The five money figures this page folds, flattened out of the endpoint's OWN response.

    A dereference, not a recomputation: nothing here adds anything up. A figure the response did not
    carry is absent rather than zero, and `tiles()` then serves no panel for it."""
    out = out or {}
    buckets = out.get("buckets") or {}
    totals = out.get("totals") or {}
    got = {}
    if "total_amount" in totals:
        got["total_amount"] = totals.get("total_amount")
    for band, key in ((BAND_SAFE, "under45_owed"), (BAND_WARN, "warn_owed"),
                      (BAND_MISSED, "missed_owed")):
        b = buckets.get(band)
        if isinstance(b, dict) and "owed" in b:
            got[key] = b.get("owed")
    unknown = out.get("unknown_age")
    if isinstance(unknown, dict) and "owed" in unknown:
        got["unknown_age_owed"] = unknown.get("owed")
    return got


def aging_rows(out):
    """Every unsold device the RESPONSE carries, across its bands.

    The response caps its zero-owed and unknown-age lists for payload size, so this is what a reader
    holding only the response can see — and the panel's own `capped` / `rows_total_count` say so. The
    producer passes its UNCAPPED population instead (see `aging_drilldowns`), which is why the live
    screen's tie-out is over every device and not over a page of them."""
    out = out or {}
    buckets = out.get("buckets") or {}
    rows = []
    for band in (BAND_SAFE, BAND_WARN, BAND_MISSED):
        rows.extend((buckets.get(band) or {}).get("rows") or [])
    rows.extend(((out.get("zero_inventory") or {}).get("rows")) or [])
    rows.extend(((out.get("unknown_age") or {}).get("rows")) or [])
    return [r for r in rows if r]


def aging_catalogue():
    """Every figure this page folds above its tables, declared — with the band each is folded over.

    WHAT GETS NO PANEL HERE, and each refusal has a ruling behind it:

      · **The device COUNT beside every band** ("124 devices", "Total Phones Outstanding"). Each is
        the cardinality of the panel immediately beside it — the money tile for the same band lists
        exactly those devices and serves `rows_total_count` — so a second tile proving how many
        there are is a tautology dressed as evidence (the On-Inventory catalogue's `store_count`
        ruling, batch 11).
      · **`flagged_owed` / `flagged_count`.** Σ of the three band tiles, so no set of records adds
        up to it that is not already under one of them; a tie-out against it could not fail
        (§CORE-17.3h as batch 10 extended it). All three halves get panels instead.
      · **The out-of-pocket headline.** It is NOT Σ(`owed_to_vip`) over the flagged devices: since
        §COMMCALC-33 it is that cost NET of what the carrier already recovered per device, and the
        netting is the payables module's verdict rather than a column on these rows. A panel folded
        over this report's own amount column would therefore explain a DIFFERENT number than the one
        on screen and the tie-out would say so — correctly, and uselessly. It is declared in the PR
        that carries the per-device recovery onto these rows, not guessed at here.
      · **`age_buckets`.** The same bands over the same devices, counted rather than priced — a
        second shape of a figure already drillable, which would be two answers to one question.
    """
    common = {"row_label": ("store", "esn_imei"), "keys": device_keys, "fields": _device_fields,
              "tables": device_tables, "column": COL_OWED, "note": _AGING_NOTE,
              "value": _device_amount, "reason": lambda r: (None if _device_amount(r) is not None
                                                            else "amount_not_recorded")}
    return (
        {"key": "total_amount", "label": "Total amount owed (every unsold device)",
         "total": "total_amount", "rows_of": BAND_ALL, **common},
        {"key": "under45_owed", "label": "Inside the selling window — owed",
         "total": "under45_owed", "rows_of": BAND_SAFE, **common},
        {"key": "warn_owed", "label": "In the warning window — owed, still sellable",
         "total": "warn_owed", "rows_of": BAND_WARN, **common},
        {"key": "missed_owed", "label": "Past the selling window — owed",
         "total": "missed_owed", "rows_of": BAND_MISSED, **common},
        {"key": "unknown_age_owed", "label": "No usable purchase date — owed",
         "total": "unknown_age_owed", "rows_of": BAND_UNKNOWN_AGE, **common},
    )


def aging_drilldowns(out, rows=None, *, cap=None):
    """PURE: one panel per band, over THAT band's own devices.

    `rows` is the producer's uncapped population; a caller holding only the response passes nothing
    and gets the devices the response carries (see `aging_rows`). Either way the figure each panel
    explains is the response's own, read through `aging_totals` and never recomputed.

    A figure the response did not produce is skipped rather than served with a zero, and a band with
    no devices at all gets no panel — `tiles()`' own rule applied to the row list instead of the
    total, which is what stops an unloaded ledger from drilling to a reassuring empty tie."""
    totals = aging_totals(out)
    if not totals:
        return {}
    pool = rows if rows is not None else aging_rows(out)
    if not pool:
        return {}
    panels = {}
    for spec in aging_catalogue():
        spec = dict(spec)
        band = spec.pop("rows_of", BAND_ALL)
        mine = band_rows(pool, band)
        if not mine:
            continue
        panels.update(report_drilldown.tiles(
            (spec,), mine, totals, schema=SCHEMA, cap=cap or report_drilldown.ROW_CAP))
    return panels
