"""WHERE A WEEKLY REVIEW FIGURE CAME FROM — the declaration catalogue (index §CORE-17.3, §COMMCALC-76).

PURE. It declares, per review item, the rows behind the figure and the relation each row was read
out of; `core/report_drilldown.py` does the folding and the tie-out. This file computes no figure.

THE HARD PART OF A COMPOSED REPORT, AND THE RULE THIS FILE FOLLOWS
──────────────────────────────────────────────────────────────────
The Weekly Review QUOTES other reports. For some items the relation behind every row is certain —
a rep's payout is a `commcalc.rep_commissions` row, an accessory target is a `commcalc.targets` row
— and those get a real panel naming the real table.

For the others it is NOT certain at this level, and a panel is REFUSED with its reason rather than
invented:

  · profit by store is the Gross Profit report's own composition of four feeds per store, and which
    feed answered differs store by store. Naming one would be a provenance claim nothing checked
    (`report_drilldown.origin`'s own rule). The GP report carries that drill-down; the pack links to
    it.
  · payroll is the one labour authority's figure, and the evidence for a person-day is a punch, a
    correction or a schedule — the Payroll report's own drill-down answers that per person.
  · inventory aging is folded here PER CARRIER from device rows that each name their own source
    relation; the devices open on the Inventory Aging report (§CORE-17.3y), which already serves a
    panel per band naming `asset_ledger` or `device_payable_ledger` per row.
  · growth is a RATE and a subtraction (§CORE-17.3h/k): what can be opened is the figures it divides,
    and those are the sales cells the Sales Report serves.
  · "reps behind" is a COUNT whose list is the table on the pack itself (§CORE-17.3h).

A refusal that says which report does answer is worth more than a panel whose provenance is a guess.
"""
from app.modules.core import report_drilldown as rd

SCHEMA = "commcalc"

#: The items that get a real panel, and the relation each row is read out of. Adding an item here is
#: a claim that EVERY row of it comes from that relation — `harness_weekly_review.py` §D checks that
#: each one is a relation `frontend/src/lib/sourceLabels.ts` can name in words.
PANELLED = ("commission", "accessory")

#: The items that get no panel, and WHY — in the words the pack prints beside the figure. Each names
#: the report that does answer, so a refusal is a direction rather than a dead end.
REFUSALS = {
    "profit": ("Profit is the Gross Profit report's own composition of sales, carrier pay, expenses "
               "and rep pay, and which feed answered differs store by store — that report carries "
               "the drill-down for it.",
               "/commcalc/gp"),
    "payroll": ("Payroll is the platform's one labour authority; the evidence for a person-day is a "
                "punch, a correction or a schedule, and the Payroll report opens that per person.",
                "/storeops/payroll"),
    "aging": ("Each carrier's devices open on the Inventory Aging report, which names the ledger "
              "every device was read out of.",
              "/commcalc/asset/aging"),
    "growth": ("Growth is a percentage change between two weeks (§CORE-17.3h): what can be opened "
               "is the sales behind each week, on the Sales Report.",
               "/commcalc/sales-report"),
    "rep_watch": ("The count of reps behind IS the list printed beneath it; a tally's rows are the "
                  "table itself.",
                  "/commcalc/targets/action-plan"),
}


def _commission_panel(item):
    """The rep payouts behind this scope's commission total. One row per rep, each addressed by the
    period's `rep_commissions` row for that store."""
    return rd.tile_panel(
        "Commission paid to reps", item.get("figure"), item.get("rows") or [],
        value=lambda r: r.get("payout"),
        row_label=lambda r: (r.get("rep") or "(rep not named)", r.get("store_code") or ""),
        keys=("store_code", "rep"),
        tables=("rep_commissions",), schema=SCHEMA, column="total_payout",
        fields=lambda r: {"store": r.get("store_code") or "—"},
        subject="commission", subject_kind="period",
        note="The pay engine's own calculated rows for the period — this report adds nothing to "
             "them and takes nothing away.")


def _accessory_panel(item):
    """The accessory TARGETS this scope's gap is measured against. The gap itself is a subtraction
    and does not drill (§CORE-17.3k); what opens is the target each store was held to, and a store
    with no saved target is SHOWN, carrying no figure, rather than counted as a zero goal."""
    detail = item.get("detail") or {}
    return rd.tile_panel(
        "Accessory target in force", detail.get("target_total"), item.get("rows") or [],
        value=lambda r: r.get("target"),
        row_label=lambda r: r.get("store_code") or "(store not named)",
        keys=("store_code",),
        tables=("targets",), schema=SCHEMA, column="accessories_monthly",
        measured=lambda r: r.get("target") is not None,
        reason=lambda r: r.get("reason") or "",
        fields=lambda r: {"achieved": r.get("achieved"), "gap": r.get("gap")},
        subject="accessories", subject_kind="period",
        note="A store with no saved accessory target is listed without a figure: it is not a store "
             "whose goal is zero.")


_PANELS = {"commission": _commission_panel, "accessory": _accessory_panel}


def review_drilldowns(review):
    """{item key -> panel} for ONE review pack, plus the refusals in words.

    Only a MEASURED item is offered a panel: a figure that was not measured has no rows behind it,
    and serving an empty panel would assert that it had none rather than that nobody measured it.
    """
    panels, refused = {}, {}
    for item in (review or {}).get("items") or []:
        key = item.get("key")
        if key in _PANELS and item.get("state") == "measured":
            try:
                panels[key] = _PANELS[key](item)
            except Exception as e:                             # a bad row cannot blank the pack
                refused[key] = {"reason": f"the panel could not be built ({e})", "report": None}
        elif key in REFUSALS:
            reason, where = REFUSALS[key]
            refused[key] = {"reason": reason, "report": where}
        elif key in _PANELS:
            # A PANELLED item that was not measured. It is not refused on principle — it has no rows
            # today — and saying so is different from saying it never drills.
            refused[key] = {"reason": item.get("reason") or "this item was not measured this week, "
                                                            "so there are no rows behind it",
                            "report": None}
    return panels, refused
