"""PROOF: an .xlsx whose string table is mis-cased or missing reads — and read byte-identically before.

Owner bug report 2026-09-20: uploading the sales-by-product export failed with
    Could not read file: "There is no item named 'xl/sharedStrings.xml' in the archive"

DB-free. Builds a real two-sheet workbook with openpyxl, then breaks it the two ways real exporters do:
  (a) the string table stored as `xl/SharedStrings.xml` (case variant);
  (b) every text cell rewritten inline and the string table DELETED, while the workbook still declares it.
For each: the stock `pd.read_excel` must REFUSE with exactly the owner's message (the reproduction), and
`xlsx_tolerant.read_excel` must return the identical frame. A clean file must pass through untouched,
and a non-ZIP file named .xlsx must produce a sentence, not a stack trace.

Run:  python3 backend/harness_xlsx_tolerant.py
"""
import io
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd
from openpyxl import Workbook

from app.modules.commcalc import xlsx_tolerant as XT

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name if ok else f"{name} :: {detail}")


# ── the fixture: a real workbook with a title block, strings, numbers, two sheets ────────────────
def build_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales by Product"
    ws.append(["Sales by Product — August 2026"])
    ws.append([])
    ws.append(["Invoice #", "Product Name", "Category", "Quantity", "Total Price"])
    ws.append(["INV-1001", "Phone Case", "Accessories", 2, 39.98])
    ws.append(["INV-1002", "Screen Protector", "Accessories", 1, 19.99])
    ws.append(["INV-1003", "Charger", "Accessories", 3, 44.97])
    ws2 = wb.create_sheet("Sheet2")
    ws2.append(["Note", "second sheet keeps its strings too"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def with_shared_strings(contents: bytes) -> bytes:
    """FIXTURE REPAIR (2026-09-20): openpyxl 3.1.x writes every text cell INLINE and emits NO
    `xl/sharedStrings.xml` at all, so a workbook it saves cannot be 'broken' the way a real exporter's
    can — the breakages below need a string table to mis-case / delete. This rewrites the clean
    workbook the way Excel and the POS exporters write it: a shared-string table, `t="s"` cells, the
    part declared in [Content_Types].xml and the workbook rels. The stock reader accepts the result
    (pinned in A1), so it is the honest 'clean' fixture."""
    zin = zipfile.ZipFile(io.BytesIO(contents))
    if XT.SHARED_STRINGS in zin.namelist():
        return contents
    strings, index = [], {}
    inline_re = re.compile(r'<c r="([A-Z]+\d+)"([^>]*?) t="inlineStr"([^>]*)><is><t[^>]*>(.*?)</t></is></c>', re.S)

    def to_shared(m):
        txt = m.group(4)
        if txt not in index:
            index[txt] = len(strings)
            strings.append(txt)
        return f'<c r="{m.group(1)}"{m.group(2)}{m.group(3)} t="s"><v>{index[txt]}</v></c>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            d = zin.read(n)
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"):
                d = inline_re.sub(to_shared, d.decode("utf-8")).encode("utf-8")
            elif n == "[Content_Types].xml":
                d = d.decode("utf-8").replace(
                    "</Types>",
                    '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
                    'officedocument.spreadsheetml.sharedStrings+xml"/></Types>').encode("utf-8")
            elif n == "xl/_rels/workbook.xml.rels":
                d = d.decode("utf-8").replace(
                    "</Relationships>",
                    '<Relationship Id="rIdSst" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    'relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>').encode("utf-8")
            zout.writestr(n, d)
        sst = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<sst xmlns="http://schemas.openxmlformats.org/'
               f'spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">'
               + "".join(f"<si><t>{t}</t></si>" for t in strings) + "</sst>")
        zout.writestr(XT.SHARED_STRINGS, sst.encode("utf-8"))
    return out.getvalue()


def rezip(contents: bytes, transform) -> bytes:
    """Copy every member through `transform(name, data) -> (name|None, data)`; None drops it."""
    zin = zipfile.ZipFile(io.BytesIO(contents))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            new_name, data = transform(n, zin.read(n))
            if new_name is not None:
                zout.writestr(new_name, data)
    return out.getvalue()


def break_case(contents: bytes) -> bytes:
    return rezip(contents, lambda n, d: ("xl/SharedStrings.xml" if n == XT.SHARED_STRINGS else n, d))


def break_inline(contents: bytes) -> bytes:
    """Rewrite every shared-string cell as an inline string and DROP the table, leaving the workbook's
    declaration of it in place — the inline-string exporter shape."""
    zin = zipfile.ZipFile(io.BytesIO(contents))
    sst_xml = zin.read(XT.SHARED_STRINGS).decode("utf-8")
    strings = re.findall(r"<t[^>]*>(.*?)</t>", sst_xml, flags=re.S)

    def to_inline(m):
        idx = int(m.group(2))
        return f'<c r="{m.group(1)}"{m.group(3)} t="inlineStr"><is><t>{strings[idx]}</t></is></c>'

    cell_re = re.compile(r'<c r="([A-Z]+\d+)"([^>]*?) t="s"([^>]*)><v>(\d+)</v></c>')

    def transform(n, d):
        if n == XT.SHARED_STRINGS:
            return None, d
        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"):
            xml = d.decode("utf-8")
            xml = cell_re.sub(lambda m: f'<c r="{m.group(1)}"{m.group(2)}{m.group(3)} t="inlineStr">'
                                        f'<is><t>{strings[int(m.group(4))]}</t></is></c>', xml)
            return n, xml.encode("utf-8")
        return n, d
    return rezip(contents, transform)


def frames_equal(a, b) -> bool:
    if set(a) != set(b):
        return False
    return all(a[k].fillna("").astype(str).values.tolist() == b[k].fillna("").astype(str).values.tolist()
               for k in a)


KW = dict(dtype=str, sheet_name=None, header=None)      # exactly what _read_upload_grids passes
clean = with_shared_strings(build_workbook())
expected = pd.read_excel(io.BytesIO(clean), **KW)
check("A0 the fixture carries a real shared-string table (the shape Excel / the POS exporters write; openpyxl alone writes inline strings)",
      XT.SHARED_STRINGS in zipfile.ZipFile(io.BytesIO(clean)).namelist())
check("A1 the fixture is a real two-sheet workbook the stock reader accepts",
      set(expected) == {"Sales by Product", "Sheet2"} and expected["Sales by Product"].shape[0] == 6)

# ── B. REPRODUCE the owner's failure, both shapes ───────────────────────────────────────────────
for label, broken in (("case-variant SharedStrings.xml", break_case(clean)),
                      ("inline strings, table deleted", break_inline(clean))):
    try:
        pd.read_excel(io.BytesIO(broken), **KW)
        check(f"B REPRODUCE [{label}]: the stock reader refuses the file", False, "it read it")
    except Exception as e:
        check(f"B REPRODUCE [{label}]: the stock reader refuses the file",
              XT.missing_part(e) == XT.SHARED_STRINGS, f"{type(e).__name__}: {str(e)[:120]}")

# ── C. the tolerant read returns the identical frame ────────────────────────────────────────────
got, action = XT.read_excel(break_case(clean), **KW)
check("C1 case-variant: read succeeds", True)
check("C2 case-variant: the frame is identical to the clean read", frames_equal(got, expected))
check("C3 case-variant: the action names what was done", action == "renamed:xl/SharedStrings.xml", action)

got, action = XT.read_excel(break_inline(clean), **KW)
check("C4 inline/deleted: read succeeds", True)
check("C5 inline/deleted: the frame is identical to the clean read", frames_equal(got, expected),
      str(got.get("Sales by Product", pd.DataFrame()).head(6).values.tolist())[:300])
check("C6 inline/deleted: the action is 'added_empty'", action == "added_empty", action)

# ── D. a clean file passes through untouched ────────────────────────────────────────────────────
got, action = XT.read_excel(clean, **KW)
check("D1 a clean file reads with action 'ok'", action == "ok", action)
check("D2 …and repair() on it is a no-op returning the same bytes",
      XT.repair(clean) == (clean, "unchanged"))
check("D3 the header=None/dtype=str call the intake makes is honoured (title row survives)",
      got["Sales by Product"].iloc[0, 0] == "Sales by Product — August 2026")

# ── E. a file that is not a workbook is told the truth ──────────────────────────────────────────
html = b"<html><body><table><tr><td>Invoice #</td></tr></table></body></html>"
check("E1 describe(): html", XT.describe(html) == "html")
check("E2 describe(): xlsx", XT.describe(clean) == "xlsx")
check("E3 describe(): xls signature", XT.describe(b"\xd0\xcf\x11\xe0" + b"\0" * 40) == "xls")
check("E4 describe(): empty", XT.describe(b"") == "empty")
check("E5 repair() refuses a non-zip honestly", XT.repair(html) == (html, "not_zip"))

# ── F. the repair is narrow: only the member the reader named, only after it refused ────────────
check("F1 missing_part() reads the member out of the exact owner message",
      XT.missing_part(KeyError("There is no item named 'xl/sharedStrings.xml' in the archive")) == XT.SHARED_STRINGS)
check("F2 missing_part() is None for any other KeyError", XT.missing_part(KeyError("col")) is None)
other_member = rezip(clean, lambda n, d: (None if n == "xl/styles.xml" else n, d))
try:
    _f3, _act = XT.read_excel(other_member, **KW)
    # this openpyxl reads a workbook without styles.xml on its own — then NOTHING may have been repaired
    f3 = _act == "ok"; f3d = f"stock read succeeded but action was {_act!r}"
except Exception as e:
    f3 = XT.missing_part(e) != XT.SHARED_STRINGS           # an honest failure is fine — but never one blamed on the string table
    f3d = str(e)[:100]
check("F3 a different missing member is not silently papered over (either the stock reader reads it untouched, or it fails honestly)", f3, f3d)

# ── G. NEGATIVE CONTROL: with the repair disabled, C fails ──────────────────────────────────────
_orig = XT.repair
XT.repair = lambda contents, part=XT.SHARED_STRINGS: (contents, "unchanged")
try:
    XT.read_excel(break_case(clean), **KW)
    check("G1 negative control: disabling repair makes the tolerant read fail again", False, "it still read")
except Exception as e:
    check("G1 negative control: disabling repair makes the tolerant read fail again",
          XT.missing_part(e) == XT.SHARED_STRINGS, str(e)[:100])
finally:
    XT.repair = _orig
got, _ = XT.read_excel(break_case(clean), **KW)
check("G2 …and restoring it reads again", frames_equal(got, expected))

for p in PASS:
    print(f"  PASS  {p}")
for f in FAIL:
    print(f"  FAIL  {f}")

# ── H. THE LOCK (CLAUDE.md, #252: "a design fix ships with a check that FAILS THE BUILD if a caller stops
#      dereferencing the shared fact, or if a second copy appears"). ONE tolerant reader — router._xlsx_read —
#      is the only place in backend/app/modules that may call pandas' read_excel / ExcelFile on a payload.
#      Every other call site is named here with its reason, or the build fails naming file:line.
import ast
APP = os.path.join(HERE, "app", "modules")
ALLOWED = {
    # (relative path, enclosing function) -> why a direct pandas call is legitimate there
    ("commcalc/xlsx_tolerant.py", "read_excel"): "the tolerant reader itself — the ONE stock read, before and after the repair",
    ("commcalc/router.py", "_xlsx_read"): "the shared entry point every upload / portal payload rides",
    ("commcalc/epay_sweep.py", "_describe_workbook"): "a DIAGNOSTIC that runs only after _read_report_records (which rides _xlsx_read) "
                                                       "has already failed — it describes the downloaded file's sheets in the error text",
}
found, unexpected = [], []
for root, _dirs, files in os.walk(APP):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(root, fn)
        rel = os.path.relpath(path, APP).replace(os.sep, "/")
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        except SyntaxError as e:
            unexpected.append(f"{rel}: unparseable ({e})")
            continue
        # enclosing function per call: walk with a stack
        def visit(node, func):
            for child in ast.iter_child_nodes(node):
                name = func
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = child.name
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                        and child.func.attr in ("read_excel", "ExcelFile"):
                    found.append((rel, child.lineno, name or "<module>"))
                visit(child, name)
        visit(tree, None)
for rel, line, func in found:
    if (rel, func) not in ALLOWED:
        unexpected.append(f"{rel}:{line} in {func}() calls pandas read_excel/ExcelFile directly — route it through router._xlsx_read")
check("H1 no module outside router._xlsx_read reads a workbook with pandas directly (every allowed site is named with its reason)",
      not unexpected, "; ".join(unexpected))
check("H2 every allowed site is still present (an allow-set entry that no longer matches is stale, not free)",
      {(r, f) for r, _l, f in found} >= set(ALLOWED), sorted(set(ALLOWED) - {(r, f) for r, _l, f in found}))
_router = open(os.path.join(APP, "commcalc", "router.py"), encoding="utf-8").read()
import re as _re
def _body(name):
    m = _re.search(r"^(async )?def " + name + r"\(.*?(?=^(async )?def |^@router)", _router, _re.S | _re.M)
    return m.group(0) if m else ""
for fn in ("_read_upload_df", "_read_upload_grids", "_read_excel_all_sheets", "_parse_xreport_detail", "detect_column_mapping"):
    check(f"H3 router.{fn} dereferences _xlsx_read (the shared fact), not a copy", "_xlsx_read(" in _body(fn), fn)
for rel, fn_name in (("closing/router.py", "upload_closing"), ("closing/tender_config.py", None), ("asset/asset_parser.py", "_read_asset_df"),
                     ("commcalc/hotsheet_parser.py", None), ("commcalc/agency.py", "parse_csv_bytes"), ("commcalc/report_pull.py", None),
                     ("commcalc/vip_sweep.py", None), ("commcalc/epay_sweep.py", "_read_report_records")):
    src = open(os.path.join(APP, rel), encoding="utf-8").read()
    check(f"H4 {rel} rides _xlsx_read for its workbook payload", "_xlsx_read(" in src, rel)

print(f"\nharness_xlsx_tolerant: {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
