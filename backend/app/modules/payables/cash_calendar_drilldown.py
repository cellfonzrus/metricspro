"""THE CASH CALENDAR'S THREE FIGURES NAME THEIR ROWS — the drill-down declaration (§CORE-17).

OWNER RULE (2026-10-24, verbatim): *"update in index thta drilldown is needed for every report
geenerated from any data which comming from within the platforma as a defalut"*.

THIS FILE COMPUTES NOTHING. It is a DECLARATION: per tile, which `totals` key holds the figure, which
field on the report's own day rows it was folded from, which TABLES that field was read out of, and
which rows the tile counts. The fold, the tie-out and the provenance roll-up are
`core/report_drilldown.tiles` — the one home. A drill-down that recomputed its own figure would be a
second opinion about money rather than evidence for the first one.

THE PROVENANCE IS RESOLVED AT RUNTIME AND NEVER ASSUMED HERE, which is the whole reason this is a
function and not a constant. Both of this report's sides answer out of whichever relation the TENANT
declared — the inbound source off `commcalc.receipt_source_map`, the owed side off the device ledger
or the processor's own feed, whichever `owed_read` says answered. So the caller hands the table names
in. A guess here would be a provenance CLAIM nothing checked, which is worse than the blank it
replaces, and it would be one tenant's feed named for all four.

WHICH FIGURES DRILL, AND WHY THE FOURTH DOES NOT
────────────────────────────────────────────────
  · **Expected in** and **owed out** are each Σ of a field on the day rows. They drill.
  · **Net** drills too, and it is the §CORE-17.3e / §CORE-17.3h exception stated out loud: a figure
    computed as A_total − B_total has no set of records adding up to it, so a tie-out against it is a
    check that cannot fail — but this net is folded PER DAY onto the row and then added up, so
    Σ(day nets) against the headline is a real comparison that can disagree.
  · **The closing balance gets NO panel.** It is the opening balance carried through each day's net:
    no set of rows adds up to it, because the opening balance is not a row at all. §CORE-17.3h says a
    figure like that gets none, and inventing a panel for it would show a tie-out that cannot fail.
  · **A day whose side was NOT MEASURED contributes no number**, not a zero — `value` returns None
    and the row says why. That is the one thing this report exists to get right, and it has to hold
    in the drill-down as well as on the screen, or the panel would quietly total what the cell
    refused to.

A tile is added by adding a ROW. `harness_cash_calendar.py` reads this catalogue against the
endpoint's own `totals` keys and fails the build when a money figure on the screen has no
declaration, so the report cannot grow a fourth undrillable tile.

PURE: no I/O, no framework, and no carrier, tenant or product name (RULE TWO).
"""
from app.modules.core import report_drilldown

#: What addresses one row of this report in the tables it names: its DAY. One field, because a day
#: row is addressed by its date in every relation either side reads.
ROW_KEYS = ("date",)

#: What the reader wants beside the number on every row. Purely presentational.
ROW_FIELDS = ("date", "in_count", "out_count")

NET_NOTE = ("Each day's net is this day's expected in minus this day's owed out, folded on the row "
            "and then added up — so the figure above really is the sum of the rows below it.")
UNMEASURED_REASON = "not_measured"


def _label(r):
    return str((r or {}).get("date") or "?")


def _measured(field):
    """A day's contribution to a tile, or None where that side was never measured.

    None is NOT zero here, and the distinction is the report's whole point: a tenant with no declared
    payout cycle has an unknown inbound, and a drill-down that counted it as $0.00 would total
    something the cell above it refused to show."""
    return lambda r: (r or {}).get(field)


def _reason(field):
    return lambda r: (UNMEASURED_REASON if (r or {}).get(field) is None else None)


def catalogue(*, expected_tables=(), expected_date_columns=(), owed_tables=(),
              owed_date_column="due_date"):
    """The three drillable figures, declared. Every table name is the CALLER'S — resolved from this
    tenant's own declarations at request time, never spelled here."""
    exp = tuple(t for t in (expected_tables or ()) if t)
    owed = tuple(t for t in (owed_tables or ()) if t)
    # One row of this report addresses a DAY in each named table, through that table's own date
    # column. Several inbound feeds can land on one day, so the inbound keys are a tuple too.
    # WHICH COLUMN THE DAY IS IN differs per side — an inbound feed's own date column against the
    # payable ledger's `due_date`. Named only where it is UNAMBIGUOUS: with two inbound feeds dated
    # by different columns there is no single answer, and naming one of them would name the wrong
    # column on half the rows. None is honest; a guess is not.
    exp_cols = tuple(c for c in (expected_date_columns or ()) if c)
    exp_col = exp_cols[0] if len(set(exp_cols)) == 1 else None
    common = {"row_label": _label, "keys": ROW_KEYS, "fields": ROW_FIELDS, "schema": "commcalc"}
    return (
        {"key": "total_in", "label": "Expected to come in",
         "total": "total_in", "value": _measured("expected_in"), "tables": exp,
         "column": exp_col, "reason": _reason("expected_in"), **common},
        {"key": "total_out", "label": "Owed out",
         "total": "total_out", "value": _measured("owed_out"), "tables": owed,
         "column": owed_date_column, "reason": _reason("owed_out"), **common},
        {"key": "total_net", "label": "Net after offsetting",
         "total": "total_net", "value": _measured("net"), "tables": exp + owed,
         "reason": _reason("net"), "note": NET_NOTE, **common},
    )


def drilldowns(out, *, expected_tables=(), expected_date_columns=(), owed_tables=(),
               owed_date_column="due_date", cap=None):
    """Attach the panels to a built calendar payload. `out` is the endpoint's own response, so every
    figure in a panel is the figure the screen shows — never a fresh read."""
    totals = {k: (out or {}).get(k) for k in ("total_in", "total_out", "total_net")}
    return report_drilldown.tiles(
        catalogue(expected_tables=expected_tables, expected_date_columns=expected_date_columns,
                  owed_tables=owed_tables, owed_date_column=owed_date_column),
        (out or {}).get("days") or [], totals,
        schema="commcalc", cap=cap or report_drilldown.ROW_CAP)
