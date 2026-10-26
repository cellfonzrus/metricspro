"""WHERE DID THIS FIGURE COME FROM — the declaration for the HOURS BUDGET screen, whose figure is a
store-week of SCHEDULED hours folded server-side out of rows the screen never shows (§CORE-17,
batch 24).

  /storeops/hours-budget   Hours budget — each store's "Scheduled this week" figure, against the
                           weekly budget a manager typed and the DM override that unlocks it.

WHY THIS IS A FAMILY OF ITS OWN, AND NOT PART OF THE PAYROLL ONE (§CORE-17.3l)
─────────────────────────────────────────────────────────────────────────────
A grain fixes the ROW, the UNIT and the PROVENANCE. The payroll family's row is ONE PERSON over one
PAY PERIOD and its hours figure can come out of the time clock, the schedule or nothing at all. This
report's row is ONE STORE over one WORK-WEEK and its figure comes out of the SCHEDULE and nothing
else, because the question is not what anybody worked — it is how many hours have been PLANNED
against a limit. Same unit, different row, different provenance: a different grain, and stretching
one declaration over the two would be the sibling derivation the index rules forbid.

THE FIX IS AT THE READ, NOT IN THIS DECLARATION (the §CORE-17.3c posture, taken again)
─────────────────────────────────────────────────────────────────────────────────────
`router._store_week_hours` summed the week's shift rows and returned a float: WHICH ROWS, and which
column of each, was a fact that read knew and every caller threw away. A declaration cannot get it
back without re-reading the table — a second read of the same question, which would be free to
disagree with the figure it is supposed to explain. So the read now has a sourced form,
`_store_week_hours_sourced(...) -> (total, rows)`, the plain one is a thin wrapper over it (the
over-budget projection check still calls the wrapper and is byte-identical), and the per-row fact
each drill row needs travels on the row:

  `drill_hours`  what this shift row contributed to the figure, as the READ computed it — never
                 recomputed here, because a recomputation is a second opinion.
  `drill_basis`  WHETHER THAT CAME FROM A STORED COLUMN OR FROM THE CLOCK TIMES. A shift row with no
                 `scheduled_hours` has its hours DERIVED from `start_time`/`end_time`, and a panel
                 that named the stored column for such a row would name a column that is empty.

THE BUDGET ITSELF GETS NO PANEL, AND THAT IS A RULING, NOT AN OMISSION
─────────────────────────────────────────────────────────────────────
`Weekly budget (h)` is a LEAF — one column of one config row somebody typed (§CORE-17.3h as batch 10
extended it, and §CORE-17.3m for the declared-versus-measured half of it). There is no set of records
that adds up to it: it is a declaration, and the honest thing to show a reader is the plain-words
label for where it was typed, which `sourceLabels.ts` already carries. The same goes for the DM
override — a decision, recorded with who made it and why, printed in full in the table beneath. A
panel is for a figure that was FOLDED; neither of these was.

  · `Over budget` / `Override approved` are VERDICTS computed from the figure and the declaration
    beside it, not figures of their own.
  · A store nobody has scheduled this week has a 0.0 over no rows at all, and the one home's own
    `served_tiles` rule refuses it a panel: a tie of 0 against 0 reads as "we checked, there was
    nothing", which is indistinguishable from a week nobody has written yet.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o), AND THIS PAGE IS THE NEGATIVE CASE
───────────────────────────────────────────────────────────────────────────────────────────
The page does have a browser filter (store / market), but every figure it prints is a SINGLE STORE'S
own server-folded number — it folds nothing across stores and prints no strip. Filtering therefore
hides whole rows and never changes a figure, so these panels must NOT be narrowed, and
`frontend/src/lib/drill-narrow` is deliberately absent from the page. `harness_batch24_drilldown.py`
§G locks that in both directions off the page's own text, beside the payroll pages where the
opposite is locked.

💰 MOVES NO MONEY. This report prints no money at all. Read-only presentation of a figure the read
already folded; books nothing, writes nothing, and computes no business figure of its own.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3x.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: THE SCHEDULE — the one relation that records a planned shift, and the ONLY provenance in this
#: family. Dereferenced from the lineage registry, never spelled.
SCHEDULE = _lineage.SCHEDULE

SCHEMA = "storeops"

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule, bound BY IDENTITY (§CORE-17.3u).
_served = report_drilldown.served_tiles

#: THE FIELDS THE SOURCED READ STAMPS ON EACH SHIFT ROW IT SUMMED. Named HERE, once, and
#: dereferenced by the read — so the read and the panel cannot disagree about which key holds the
#: contribution, which is the duplicate-fact defect the index rules forbid.
HOURS_FIELD = "drill_hours"
BASIS_FIELD = "drill_basis"
#: THE TWO WAYS A SHIFT ROW'S HOURS ARE KNOWN, and the column each was read out of. A row whose
#: `scheduled_hours` is blank had its hours worked out from the two clock times, and naming the empty
#: column for it would be false.
BASIS_STORED = "stored_scheduled_hours"
BASIS_DERIVED = "derived_from_shift_times"
BASIS_COLUMNS = {BASIS_STORED: "scheduled_hours", BASIS_DERIVED: None}
BASIS_WORDS = {
    BASIS_STORED: "the shift's own stored scheduled hours",
    BASIS_DERIVED: "worked out from the shift's start and end times, because no scheduled-hours "
                   "figure is stored on it",
}

#: The one figure this family serves, under the key the report serves it as.
USED_KEY = "used_hours"

_NOTE = (
    "One row per shift the schedule holds for this store in this work-week — the rows the figure "
    "above was added up from, which this screen does not otherwise show. These are PLANNED hours: "
    "the schedule records an intention, so nothing here is a measurement of anything worked, and the "
    "Payroll Report is where a punched hour is named. A shift with no stored scheduled-hours figure "
    "says so on its own row, because its hours were worked out from its start and end times instead.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  READERS. Accessors and names only — nothing here adds, judges or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _hours(r):
    """PURE: what the READ said this shift row contributed. Read, never recomputed."""
    return (r or {}).get(HOURS_FIELD)


def _column(r):
    """PURE: the column the figure was read out of, which differs row by row."""
    return BASIS_COLUMNS.get((r or {}).get(BASIS_FIELD))


def _label(r):
    """PURE: the shift as a reader would look for it — who, on which day."""
    r = r or {}
    day = str(r.get("shift_date") or "")[:10]
    who = str(r.get("employee_name") or "").strip()
    if day and who:
        return "%s · %s" % (day, who)
    return day or who or None


def _fields(r):
    """PURE: what else a reader needs beside one shift row. Every value the row's own."""
    r = r or {}
    return {
        "store": r.get("store_code") or None,
        "day": str(r.get("shift_date") or "")[:10] or None,
        "from": r.get("start_time") or None,
        "to": r.get("end_time") or None,
        "hours from": BASIS_WORDS.get(r.get(BASIS_FIELD)) or None,
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE CATALOGUE — one store-week of planned hours, one unit, one provenance.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def store_catalogue(store_code=None, week_start=None, week_end=None):
    """PURE: the ONE panel this family serves, for ONE store's work-week.

    The subject carries the store and the week, because the figure is a store-WEEK and a panel keyed
    on the store alone would address every week there has ever been — the §4e grain defect the shared
    mechanism exists to prevent.
    """
    label = "Scheduled hours this week"
    week = " · ".join(str(x) for x in (week_start, week_end) if str(x or "").strip())
    return ({"key": USED_KEY, "total": USED_KEY, "label": label,
             "value": _hours, "column": _column, "tables": (SCHEDULE,),
             "row_label": _label, "keys": ("store_code", "shift_date"), "fields": _fields,
             "schema": SCHEMA, "unit": report_drilldown.UNIT_HOURS,
             "subject": (store_code or None), "subject_kind": "store",
             "note": (_NOTE + (" Work-week %s." % week if week else ""))},)


def budget_drilldowns(budget_rows, shifts_by_store, *, week_start=None, week_end=None, cap=None):
    """PURE: one panel per store row the report serves, keyed on the store code the report keys its
    own rows on — so a panel can never hold a different week than the row it was opened from.

    `budget_rows` are the rows the endpoint serves, UNCHANGED. `shifts_by_store` is what the sourced
    read returned for each of them. A store the schedule holds nothing for, whose figure is therefore
    a zero over no rows, is refused a panel by the one home's own rule rather than served a tie
    against nothing.
    """
    out = {}
    for b in (budget_rows or ()):
        sc = str((b or {}).get("store_code") or "").strip()
        if not sc:
            continue
        panels = _served(store_catalogue(sc, week_start, week_end),
                         (shifts_by_store or {}).get(sc) or (),
                         {USED_KEY: (b or {}).get(USED_KEY)},
                         cap or report_drilldown.ROW_CAP)
        if panels.get(USED_KEY):
            out[sc] = panels[USED_KEY]
    return out
