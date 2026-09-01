"""Multi-sheet workbook stitching — PURE helpers (stdlib only, DB-free, pandas-free).

WHY (owner incident 2026-09-01, "LuxeLink sales feed frozen at 08-30"): B2B Soft's scheduled
'My Sales Transaction Details (Legacy)' export SPLITS across continuation worksheets once the
month-to-date file outgrows one sheet (each continuation sheet repeats the same header row).
The generic upload path parsed only the FIRST sheet (`pd.read_excel(..)` default), so every
hourly email parsed to the identical ~24k rows ending mid-08-30 while the real file kept
growing — days 08-31+ lived on sheet 2 and were silently dropped. These helpers decide, from
HEADERS ALONE, which extra sheets are continuations of the first (same columns) and which rows
are repeated header echoes, so the router can concatenate exactly the right sheets. Pure so
`harness_multisheet_ingest.py` proves the rules without pandas in the container.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §2 (universal upload).
"""

__all__ = ["norm_header", "same_header", "continuation_sheet_names", "is_header_echo"]


def norm_header(cols):
    """Normalize a header row for comparison: str(), strip, case preserved (b2bsoft is
    consistent about case; preserving it keeps 'Store' vs 'store' visible in audits)."""
    return tuple(str(c).strip() for c in cols)


def same_header(cols_a, cols_b):
    """True when two sheets carry the SAME header — same names in the same order after
    normalization. Order matters: a summary sheet that merely reuses some column names in a
    different layout must NOT be concatenated into the detail data."""
    a, b = norm_header(cols_a), norm_header(cols_b)
    return len(a) == len(b) and a == b


def continuation_sheet_names(primary_cols, other_sheets):
    """Which extra sheets continue the first sheet's data?

    `other_sheets`: iterable of (sheet_name, header_cols) for every sheet AFTER the first,
    in workbook order. Returns the names whose header matches the primary sheet's header —
    those are continuation pages of the same export. Sheets with different headers (summary
    tabs, notes) are excluded, preserving the old single-sheet behavior for them.
    """
    primary = norm_header(primary_cols)
    return [name for name, cols in other_sheets
            if len(norm_header(cols)) == len(primary) and norm_header(cols) == primary]


def is_header_echo(row_values, header_cols):
    """True when a DATA row is actually the header repeated mid-data (paged report exports
    re-print the column names). A row is an echo only when EVERY non-empty cell equals its
    own column name and at least two cells are non-empty — so a legitimate record that
    happens to contain one column-name string (e.g. a product literally named 'Store') is
    never dropped, and a fully blank row is not an echo (blank rows are someone else's rule).
    """
    header = norm_header(header_cols)
    vals = [str(v).strip() if v is not None else "" for v in row_values]
    if len(vals) != len(header):
        return False
    non_empty = [(v, h) for v, h in zip(vals, header) if v != "" and v.lower() != "nan"]
    if len(non_empty) < 2:
        return False
    return all(v == h for v, h in non_empty)
