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


# ── A BLANK IDENTIFIER IS NULL, NOT THE EMPTY STRING (owner bug report 2026-09-16) ────────────────
# THE DEFECT, reproduced on the owner's own RQ inventory export: the file carries 455 rows, of which
# 72 are ORDERED or BACK-ORDERED units. No handset has arrived for those, so their IMEI cell is blank
# — and `apply_transform('', 'text')` yields the EMPTY STRING, not None. Postgres treats '' as a real
# value, so all 72 collide on `inventory_aging_device_org_imei_uq` (UNIQUE on org_id, imei — mig 216)
# and the whole import dies with 23505 on the second blank. The upload could never succeed.
#
# NULL is also what the data actually means: the feed left the cell blank, which is precisely what
# NULL says, and NULLs are DISTINCT in a unique index so any number of them coexist.
#
# SCOPED TO UNIQUE-KEY FIELDS ON PURPOSE. Blanking every empty string platform-wide would change what
# every existing feed stores, for a cosmetic gain; this touches only the columns where '' is actually
# a bug, and the map below states a SCHEMA FACT (which index exists on which table), not a policy.
# No carrier, tenant or product name appears — RULE TWO holds.
UNIQUE_KEY_FIELDS = {
    # table                        fields in a UNIQUE constraint      (the index that enforces it)
    "inventory_aging_device":      ("imei",),                         # ..._org_imei_uq, mig 216
}


def blank_keys_to_null(mapped, table, key_fields=None):
    """Turn a blank unique-key field into None, in place-ish. Returns (rows, n_changed).

    A no-op for any table with no unique-key field registered, and for any row whose key is already
    populated or already None — so every existing feed is byte-identical. `key_fields` is injectable
    so a caller (and the harness) can state the columns directly rather than relying on the map.
    """
    fields = tuple(key_fields if key_fields is not None else UNIQUE_KEY_FIELDS.get(table, ()))
    if not fields:
        return mapped, 0
    changed = 0
    for row in mapped or []:
        for f in fields:
            if f in row and row[f] is not None and str(row[f]).strip() == "":
                row[f] = None
                changed += 1
    return mapped, changed
