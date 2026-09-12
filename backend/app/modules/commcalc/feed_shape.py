"""Generic FEED-SHAPE rules for the config-driven importer (/upload-mapped).

WHY THIS EXISTS. `commcalc.column_mapping` (mig 042) already makes "which source header feeds which
canonical column" DATA rather than code, so a new carrier onboards without a code change. But three
SHAPE facts about a spreadsheet were still un-handled, and each one silently corrupts an import:

  1. A FOOTER/TOTALS ROW. Many POS exports append a grand-total row that repeats every numeric
     column while leaving the row's identity columns blank. Ingested as data it DOUBLES every sum.
     Measured on the first RQ feed: a 48,876-row sheet whose last row carried the whole file's
     totals — Total Price summed to 9,208,584.86 against a true 4,604,292.43.
  2. A US-SPELLED DATETIME. `_t_date10` truncates to 10 characters, so '06/27/2025 15:52:40'
     stored '06/27/2025' — not ISO, and ambiguous with DD/MM on the 39% of rows whose day is <= 12.
  3. A FILE THAT SPANS MANY MONTHS. `_ingest_mapped_df` stamps ONE caller-supplied `period` on every
     row. A 20-month history file then lands entirely under a single month label and every
     period-scoped report reads it wrong.

NONE of these is a carrier fact, so per RULE TWO none of them is a carrier branch: they are
properties of a FILE, decided by config already on the report (its required fields, its date field).
A feed without the shape is byte-identical to today — every rule here is a no-op unless it fires.

PURE / STDLIB-ONLY on purpose: these rules decide which rows are dropped and which month a row is
booked to, i.e. they are money-misstating if wrong, so they are proven DB-free by
backend/harness_rq_ingest.py. Keep this module import-clean (no pandas / fastapi / supabase).
"""
from datetime import datetime

# The canonical month label every period-scoped read matches ('June 2025'). Identical formula to
# report_pull.apply_column_map — ONE spelling of the label, never a second that could drift.
PERIOD_LABEL_FMT = "%B %Y"


def period_fields(iso10):
    """{'period','period_month','period_year'} for an ISO 'YYYY-MM-DD', or {} when undatable.

    Returns {} rather than guessing: a row whose date could not be parsed must inherit the caller's
    period (or be reported), never be booked to an invented month."""
    s = str(iso10 or "").strip()[:10]
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return {}
    try:
        y, m = int(s[:4]), int(s[5:7])
        return {"period": datetime(y, m, 1).strftime(PERIOD_LABEL_FMT),
                "period_month": m, "period_year": y}
    except ValueError:
        return {}


def is_footer_row(mapped_row, required_fields, base_keys=()):
    """True when a mapped row is a FOOTER/TOTALS row rather than a record.

    The test is the report's OWN config, not a magic string: a real record always carries at least
    one of the report's REQUIRED identity fields (column_mapping.TARGET_FIELDS `required=True` —
    e.g. a sale's transaction id). A totals row leaves every one of them blank while still carrying
    numbers. With no required fields declared the rule cannot fire and returns False — a report that
    has not declared its identity keeps today's behaviour exactly.

    Deliberately NOT "the last row": a footer is identified by shape, so a file with no footer, or
    one whose footer is not last, is handled correctly either way."""
    req = [f for f in (required_fields or []) if f and f not in set(base_keys)]
    if not req:
        return False
    for f in req:
        v = mapped_row.get(f)
        if v not in (None, "") and str(v).strip() != "":
            return False
    return True


def category_path(value, sep=">>"):
    """Split a hierarchical category cell into (top_level, leaf).

    POS exports spell a category tree in one cell (' >> A >> B >> C '). The canonical `category`
    column keeps the FULL path (nothing is lost); `department` is the human top level. Returns
    ('','') for a blank cell and (whole, whole) for a cell with no separator — never a guess."""
    s = str(value or "").strip()
    if not s:
        return ("", "")
    parts = [p.strip() for p in s.split(sep) if p.strip()]
    if not parts:
        return ("", "")
    return (parts[0], parts[-1])
