"""WHERE DID THIS FIGURE COME FROM — the declaration for WHAT THE STOCK ON THE SHELF IS WORTH
(§CORE-17, batch 23).

  /accounts/inventory   Inventory Values — the per-store value that drives the Balance Sheet's
                        inventory line, and its reconciliation against the unsold-phone ledger.

ITS OWN FAMILY, ON PURPOSE. The question a reader works this screen through is *what is the stock on
our shelves worth, and do the two sources that claim to know agree?* — one subject, one row (a
store), one unit (money, plus the device COUNT beside it). It is deliberately NOT folded through
`account/statement_drilldown.py`, which owns the stored-statement grain: the Balance Sheet's
inventory LINE is a figure read out of the snapshot and its trail stops there (§CORE-17.3j), while
these figures are the LIVE per-store sources that line is computed FROM. Folding them through the
statement catalogue would claim the snapshot produced a figure the aging report produced. And it is
not the device-billing family either: that one is about what the distributor BILLED, this one about
what is still on the shelf.

HOW MANY GRAINS — MEASURED OFF THE PANELS, NOT ASSUMED (§CORE-17.3l / §CORE-17.3n)
─────────────────────────────────────────────────────────────────────────────────
ONE row shape — a store — and FOUR grains on it, THREE of which share a unit and are separated by
PROVENANCE ALONE. That is the sharpest §CORE-17.3n case in the finance module so far, because the
provenance here is decided PER ROW by a precedence rule rather than per figure:

  1. a STORE's REPORT VALUE      money, out of the emailed value snapshot
  2. a STORE's LEDGER VALUE      money, out of the per-device aging rows — the SAME row, the SAME
                                 unit, a different relation
  3. a STORE's LEDGER DEVICES    count, out of the per-device aging rows
  4. a STORE's EFFECTIVE VALUE   money, out of **whichever relation won for that store** — a manual
                                 override a human typed, else the emailed snapshot, else the device
                                 rows. Each row names the one that actually answered, read off the
                                 producer's own `effective_source`, never guessed. A store with
                                 neither source is shown WITHOUT a figure and is not counted
                                 (§CORE-17.3i — an absence is not a zero).

The DELTA figure is grain 1 and 2 subtracted, and it is drillable because the producer folds it PER
ROW (§CORE-17.3k): each row carries its own `delta`, so the panel explains the figure rather than
re-deriving it. Its rows therefore name BOTH relations, which makes the delta a fifth grain by the
rule and a fourth row of the same shape in practice — measured, not assumed, in
`harness_batch23_drilldown.py` §I.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **THE EXCLUDED-GHOST CHIP** (unplaced and superseded devices, with their money) — those rows are
    excluded from the cells the report folds, so they are not rows OF any figure above. They are
    already printed in full beside the figures, which is the whole point of the chip.
  · **THE SWEEP STATUS LINE** — a connector's last run, not a folded figure.
  · **THE RECON TAB'S `effective` TOTAL** — the page prints no such total on that tab, and a panel
    for a figure a screen does not print would be evidence for nothing. The editable grid above it
    does print one, and that is the panel this module serves.
  · **A STORE'S OWN CELLS** — they are the rows, not folds of anything.
  · **A 0 OVER ZERO COUNTED ROWS** — refused by the one home's `served_tiles`, so an org that has
    never swept an aging report drills to nothing rather than to a tie of 0 against 0 that reads as
    a measurement.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), DECIDED BY READING THE PAGE
────────────────────────────────────────────────────────────────────────────────────
`/accounts/inventory` is **SERVER-FOLDED and NOT narrowed**. It has no store, market or period
filter of any kind: the only controls are the override inputs, the connector form and a save, and
both totals are printed exactly as the two endpoints served them. `lib/drill-narrow` must not appear
in this page, and `harness_batch23_drilldown.py` §G fails the build in both directions off the page's
own text.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already folded, dereferenced.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3w.

───────────────────────────────────────────────────────────────────────────────────────────────────
THREE LIVE DEFECTS, REPORTED, NONE REPAIRED, NO FIGURE MOVED. Each carries a regression check in
`harness_batch23_drilldown.py` driven off the real code.

 1. **THE GRID'S "EFFECTIVE (ON BS)" COLUMN AND ITS TOTAL IGNORE THE ORG'S CONFIGURED INVENTORY
    BASIS, SO ON TWO OF THE THREE BASES THE PAGE PRINTS A NUMBER THE BALANCE SHEET DOES NOT USE.**
    `GET /account/inventory-values` computes `effective` inline as *manual override, else the swept
    report value* and nothing else, while the Balance Sheet itself goes through
    `balance_sheet.apply_inventory_basis`, whose precedence is *manual, else the CONFIGURED basis,
    else the other source* — and under the `asset_ledger` basis the emailed report is not a source at
    all. So for an org on the `devices` or `asset_ledger` basis the column headed "Effective (on BS)"
    and the total above it are a THIRD rule, agreeing with neither. The same page's reconciliation
    tab, which reads the other endpoint, prints the TRUE effective value under the configured basis
    in a column with the SAME HEADER — two columns, one header, two rules, on one screen. The panel
    this module serves for the grid total is folded over the grid's own rows and its note says in the
    reader's words which rule produced them, so the disagreement is VISIBLE rather than averaged
    away. REPORTED and not repaired: the repair changes a displayed finance figure and is surfaced
    for the owner.
 2. **THE GRID'S PER-ROW PROVENANCE IS A THIRD VOCABULARY FOR THE SAME FACT.** That endpoint writes
    `effective_source` as the product name of the portal it swept from, where the shared basis
    resolver writes the words "report", "devices", "asset_ledger" and "manual" — so the same fact
    about the same store has two spellings depending on which endpoint you ask. This declaration
    reads each payload's own word and maps it to a RELATION through one table (`SOURCE_RELATION`),
    keyed on the value as served, so a new basis is a ROW there and never a branch (RULE TWO); a word
    neither endpoint has ever served resolves to NO relation and the row says it cannot be traced
    rather than being attributed to a guess.
 3. **A STORE WHOSE AGING ROWS WERE NEVER PLACED, OR SUPERSEDED BY A NEWER SNAPSHOT, IS EXCLUDED
    FROM THE CELLS AND ITS MONEY IS REPORTED ONLY AS A CHIP.** The producer counts it in
    `device_meta` and the page prints it as a warning, so the device-side total is legitimately
    short of the ledger's own sum. The panels count exactly what the report counts and the note names
    the exclusion in the report's own words, because a panel that quietly added those rows back would
    disagree with the figure above it.
"""
from app.modules.account import balance_sheet as _bs
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: WHERE EACH FIGURE WAS ACTUALLY READ OUT OF. DEREFERENCED from the lineage registry, the one home
#: for a relation's name.
VALUE_SNAPSHOT = _lineage.INVENTORY_VALUE_SNAPSHOT
DEVICE_ROWS = _lineage.INVENTORY_AGING_DEVICES

SCHEMA = "commcalc"

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule, bound by IDENTITY.
_served = report_drilldown.served_tiles

#: WHICH RELATION A BASIS WORD MEANS — the ONE table in this module that turns a producer's own
#: `effective_source` value into the relation a panel may name. Keyed on the value AS SERVED, by
#: BOTH endpoints (defect 2), so a new basis is a ROW here rather than a branch. A word that is not
#: in here resolves to no relation at all and the row reports itself as untraceable, which is the
#: honest answer: naming a relation for a basis nobody declared would be a provenance claim nothing
#: checked.
SOURCE_RELATION = {
    "manual": VALUE_SNAPSHOT,       # a human typed it into the value snapshot's override column
    "report": VALUE_SNAPSHOT,       # the emailed aging report's own per-store total
    "b2bsoft": VALUE_SNAPSHOT,      # the SAME fact under the grid endpoint's own spelling
    "devices": DEVICE_ROWS,         # the unsold phones on the shelf, per device
    "asset_ledger": DEVICE_ROWS,    # the consignment basis reads the SAME per-device rows
}

#: THE BASIS VOCABULARY ITSELF IS NOT RESTATED HERE — it is `balance_sheet.INVENTORY_BASES`, the one
#: home for which bases exist, and `SOURCE_RELATION` must cover every one of them plus the two words
#: that are NOT bases: a human's manual override, which wins over every basis, and the grid
#: endpoint's own older spelling of the report basis (defect 2). Compared for EQUALITY by
#: `harness_batch23_drilldown.py` §E9b, so a NEW basis fails the build here instead of silently
#: resolving to no relation at all.
#:
#: One of those words, the consignment basis's, happens to READ LIKE a relation name. It is not one:
#: it is a config value, and the relation it maps to is the per-device aging rows, which is why it is
#: spelled as the vocabulary word it is rather than dereferenced from the feed registry.
NON_BASIS_SOURCES = ("manual", "b2bsoft")
BASIS_VOCABULARY = tuple(_bs.INVENTORY_BASES)

#: THE OVERRIDE COLUMN AND THE SWEPT COLUMN, in the producer's own spellings, so a reader opening a
#: row can find the cell the figure came from and not merely the table.
MANUAL_COLUMN = "manual_value"
SWEPT_COLUMN = "swept_value"


# ── NOTES — in the words the screen already uses ───────────────────────────────────────────────

_STORE_NOTE = "One row per store, exactly as the table lists them."

_GHOST_NOTE = (
    " A device the aging rows could not place at a store, or that a newer snapshot superseded, is "
    "excluded from these cells and counted separately on the report — so this figure is the report's "
    "own population and not the whole ledger.")

_EFFECTIVE_NOTE = _STORE_NOTE + (
    " This is the value the editable grid calls the one on the Balance Sheet, and each row names "
    "which source actually answered for that store: a manual override a person typed, else the "
    "emailed aging total. A store with neither is shown WITHOUT a figure rather than as zero. "
    "CAUTION, and it is this report's own: the grid applies that precedence regardless of which "
    "inventory basis the company has configured, while the Balance Sheet applies the configured "
    "one — so on a company whose basis is the device ledger this figure and the statement's can "
    "legitimately differ. The reconciliation table lower on the same page is the one computed under "
    "the configured basis.")

_REPORT_NOTE = _STORE_NOTE + (
    " Counted here is the emailed inventory-aging report's own per-store total, as of each store's "
    "own snapshot date. A store the report has never covered is shown without a figure and is not "
    "counted.")

_DEVICE_VALUE_NOTE = _STORE_NOTE + (
    " Counted here is what the unsold phones standing at each store are worth on the per-device "
    "aging rows — the other claim about the same shelf." + _GHOST_NOTE)

_DEVICE_COUNT_NOTE = _STORE_NOTE + (
    " Counted here are the phones themselves rather than their money, at each store's own snapshot."
    + _GHOST_NOTE)

_DELTA_NOTE = _STORE_NOTE + (
    " Each row is that store's own difference — the devices on the ledger less the emailed report — "
    "so this figure is the sum of per-store differences and not a difference of two sums. A row "
    "names both relations because the disagreement is between them. A store only one side knows "
    "about carries its own difference, which is the whole of it.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  READERS over one store row. Accessors and names only — no arithmetic, no judgement.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one already-folded figure on one row."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _label(r):
    """PURE: the store exactly as the table names it."""
    return str((r or {}).get("store") or "").strip() or None


def effective_relation(row):
    """PURE: the relations a row's EFFECTIVE value may honestly name — a one-tuple when the
    producer's own basis word is one `SOURCE_RELATION` knows, and EMPTY otherwise.

    An empty tuple is deliberate and is handled by the one home: `tile_panel` turns it into an origin
    that names no table, which the panel reports under `untraceable_origins`. A store with no value
    at all also lands here, and that is right — there is no source to name for a figure that does not
    exist.
    """
    word = str((row or {}).get("effective_source") or "").strip()
    table = SOURCE_RELATION.get(word)
    return (table,) if table else ()


def _effective_column(row):
    """PURE: which column of the value snapshot an effective figure was read out of, where that is
    knowable. A figure that came off the device rows has no single column, and says none."""
    word = str((row or {}).get("effective_source") or "").strip()
    if word == "manual":
        return MANUAL_COLUMN
    if word in ("report", "b2bsoft"):
        return SWEPT_COLUMN
    return None


def _effective_fields(r):
    """PURE: what else a reader needs beside one store's effective figure — the producer's own basis
    word, and both candidate figures, so the precedence is readable rather than asserted."""
    r = r or {}
    return {"source": (str(r.get("effective_source") or "").strip() or None),
            "manual override": r.get("manual_value"),
            "from the report": r.get("swept_value")}


def _recon_fields(r):
    """PURE: what else a reader needs beside one store's reconciliation row."""
    r = r or {}
    return {"report as of": r.get("report_as_of"),
            "devices as of": r.get("device_as_of"),
            "phones": r.get("device_count")}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE GRID CATALOGUE — one figure, one store row, a provenance decided per row.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

TOTAL_EFFECTIVE = "total_effective"


def values_catalogue():
    """PURE: the one figure the editable grid prints above its rows."""
    return (
        dict(key=TOTAL_EFFECTIVE, total=TOTAL_EFFECTIVE, label="Total on-hand inventory (effective)",
             row_label=_label, keys=("store",), fields=_effective_fields, schema=SCHEMA,
             value=_column("effective"), column=_effective_column,
             tables=effective_relation, unit=report_drilldown.UNIT_MONEY, note=_EFFECTIVE_NOTE),
    )


def value_drilldowns(payload, *, cap=None):
    """PURE: the panel for `GET /account/inventory-values`.

    The figure is the payload's own `total_effective` dereferenced, so the panel can never be a
    second opinion about the number above it — including when that number is computed by a rule the
    Balance Sheet does not use (defect 1), which is exactly the disagreement a tie-out exists to
    publish rather than hide.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    totals = ({TOTAL_EFFECTIVE: payload.get(TOTAL_EFFECTIVE)}
              if TOTAL_EFFECTIVE in payload else {})
    return dict(_served(values_catalogue(), rows, totals, cap or report_drilldown.ROW_CAP))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE RECONCILIATION CATALOGUE — two claims about one shelf, and their per-row difference.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

REPORT_VALUE = "report_value"
DEVICE_VALUE = "device_value"
DEVICE_COUNT = "device_count"
DELTA = "delta"


def recon_catalogue():
    """PURE: every figure the reconciliation header prints that is Σ(one field) over its own rows."""
    common = {"row_label": _label, "keys": ("store",), "fields": _recon_fields, "schema": SCHEMA}
    return (
        dict(common, key=REPORT_VALUE, total=REPORT_VALUE, label="Report (email)",
             value=_column("report_value"), column=SWEPT_COLUMN, tables=(VALUE_SNAPSHOT,),
             unit=report_drilldown.UNIT_MONEY, note=_REPORT_NOTE),
        dict(common, key=DEVICE_VALUE, total=DEVICE_VALUE, label="Devices (ledger)",
             value=_column("device_value"), tables=(DEVICE_ROWS,),
             unit=report_drilldown.UNIT_MONEY, note=_DEVICE_VALUE_NOTE),
        dict(common, key=DEVICE_COUNT, total=DEVICE_COUNT, label="Phones",
             value=_column("device_count"), tables=(DEVICE_ROWS,),
             unit=report_drilldown.UNIT_COUNT, note=_DEVICE_COUNT_NOTE),
        # THE DIFFERENCE DRILLS BECAUSE IT IS FOLDED PER ROW (§CORE-17.3k). Both relations on every
        # row, because the disagreement is between them and naming one would hide the other side.
        dict(common, key=DELTA, total=DELTA, label="Delta (devices − report)",
             value=_column("delta"), tables=(DEVICE_ROWS, VALUE_SNAPSHOT),
             unit=report_drilldown.UNIT_MONEY, note=_DELTA_NOTE),
    )


def recon_drilldowns(payload, *, cap=None):
    """PURE: every panel for `GET /account/inventory-recon`.

    An org that has never swept an aging report and holds no device rows has no reconciliation rows,
    and drills to `{}` — through the one home's own refusal rule, not through a branch here.
    """
    payload = payload or {}
    rows = [r for r in (payload.get("rows") or ()) if r]
    totals = dict(payload.get("totals") or {})
    return dict(_served(recon_catalogue(), rows,
                        {k: totals.get(k) for k in (REPORT_VALUE, DEVICE_VALUE, DEVICE_COUNT, DELTA)
                         if k in totals},
                        cap or report_drilldown.ROW_CAP))


#: EVERY FIGURE KEY THIS DECLARATION CAN SERVE, derived from the catalogues rather than listed twice.
VALUE_KEYS = tuple(t["key"] for t in values_catalogue())
RECON_KEYS = tuple(t["key"] for t in recon_catalogue())
