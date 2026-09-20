"""Tolerant .xlsx reading — repair the archive shapes that make openpyxl refuse a real POS export.

THE DEFECT (owner bug report 2026-09-20, the sales-by-product export): every upload reads through
`pd.read_excel` → openpyxl, and openpyxl opens `xl/sharedStrings.xml` by the EXACT member name the
workbook's content-types declare. Two exporter habits break that:

  (a) the part is stored under a different case — `xl/SharedStrings.xml` — and ZIP member names are
      case-sensitive, so the declared name is "not in the archive";
  (b) the part is declared but ABSENT, because the exporter wrote every text cell inline (`t="inlineStr"`)
      and never emitted a string table.

Either way openpyxl raises `KeyError: "There is no item named 'xl/sharedStrings.xml' in the archive"`
before a single row is seen, and the upload dies with "Could not read file".

WHAT THIS DOES — and does not. It never parses a cell. It rewrites the ZIP so the declared part exists
(a case-variant is renamed to the declared name; a missing one becomes an EMPTY string table, which is
exactly what an inline-string workbook means) and hands the bytes to the SAME pandas/openpyxl reader
every upload already uses. A file that reads first time is returned untouched, byte for byte — the
repair runs only after the reader has refused, and only for the member it named. A file that is not a
ZIP at all (an HTML or XML table saved with an .xlsx name) is NOT repaired: `describe()` says what it is
so the person is told the truth instead of a stack trace.

Proof: backend/harness_xlsx_tolerant.py — builds a real workbook, breaks it BOTH ways, shows the
stock reader refuse each, and shows this read return the identical frame.
"""
import io
import re
import zipfile

SHARED_STRINGS = "xl/sharedStrings.xml"
_EMPTY_SST = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
              b'count="0" uniqueCount="0"/>')
_MISSING_RE = re.compile(r"There is no item named '([^']+)' in the archive")


def missing_part(err) -> str | None:
    """The archive member an openpyxl/zipfile error says is missing, else None."""
    m = _MISSING_RE.search(str(err))
    return m.group(1) if m else None


def describe(contents: bytes) -> str:
    """What the bytes actually are, by signature — 'xlsx' (ZIP), 'xls' (BIFF), 'html', 'xml', 'text',
    'empty'. Used only for the message when a repair is impossible."""
    head = (contents or b"")[:512]
    if not head:
        return "empty"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    low = head.lstrip().lower()
    if low.startswith(b"<!doctype html") or low.startswith(b"<html") or b"<table" in low:
        return "html"
    if low.startswith(b"<?xml") or low.startswith(b"<"):
        return "xml"
    return "text"


def repair(contents: bytes, part: str = SHARED_STRINGS):
    """(bytes, action). action ∈ 'unchanged' (part already present — nothing to do), 'not_zip',
    'renamed:<old member>' (a case-variant existed), 'added_empty' (no variant — an empty string table
    is written, which is what an inline-string workbook means). Every other member is copied verbatim."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile:
        return contents, "not_zip"
    names = zin.namelist()
    if part in names:
        return contents, "unchanged"
    variant = next((n for n in names if n.lower() == part.lower()), None)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(part if n == variant else n, zin.read(n))
        if variant is None:
            zout.writestr(part, _EMPTY_SST)
    return out.getvalue(), (f"renamed:{variant}" if variant else "added_empty")


class UnreadableWorkbook(ValueError):
    """Raised with a sentence a person can act on, never a zip stack trace."""


def read_excel(contents: bytes, **read_excel_kw):
    """`pd.read_excel(BytesIO(contents), **kw)`, repaired on the one failure this module understands.
    Returns (result, action) — action 'ok' when the stock read succeeded untouched."""
    import pandas as pd
    try:
        return pd.read_excel(io.BytesIO(contents), **read_excel_kw), "ok"
    except KeyError as e:                     # zipfile surfaces a missing member as KeyError
        part = missing_part(e)
        if not part:
            raise
        fixed, action = repair(contents, part)
        if action == "not_zip":
            kind = describe(contents)
            raise UnreadableWorkbook(
                f"This file is named like an Excel workbook but its content is {kind}. Open it in Excel "
                f"and save it as .xlsx, or export it as CSV, then upload that.") from e
        if action == "unchanged":
            raise                              # the member IS there — a different problem; be honest
        return pd.read_excel(io.BytesIO(fixed), **read_excel_kw), action
