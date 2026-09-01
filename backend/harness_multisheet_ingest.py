"""Proof harness — multisheet.py (continuation-worksheet stitching rules). Pure stdlib, DB-free.

Run:  python3 backend/harness_multisheet_ingest.py
Proves the sheet-selection and header-echo rules behind the 2026-09-01 LuxeLink feed-freeze fix
(_read_excel_all_sheets in commcalc/router.py): only sheets whose header EXACTLY matches the
first sheet's are concatenated, and repeated header rows inside the data are dropped — with
negative controls showing summary tabs, reordered columns and near-miss rows are left alone.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from app.modules.commcalc.multisheet import (
    norm_header, same_header, continuation_sheet_names, is_header_echo)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}")

HDR = ["Store", "Trans ID", "Trans Date Time", "Salesperson", "Product Desc", "Total Sales"]

# ── norm_header ──────────────────────────────────────────────────────────────────────────────
check("norm strips whitespace", norm_header([" Store ", "Trans ID\n"]) == ("Store", "Trans ID"))
check("norm coerces non-str", norm_header([1, None]) == ("1", "None"))
check("norm preserves case", norm_header(["store"]) == ("store",))

# ── same_header ──────────────────────────────────────────────────────────────────────────────
check("identical headers match", same_header(HDR, list(HDR)))
check("whitespace-only diff matches", same_header(HDR, [f" {c} " for c in HDR]))
check("REORDERED columns do NOT match", not same_header(HDR, list(reversed(HDR))))
check("extra column does NOT match", not same_header(HDR, HDR + ["Extra"]))
check("missing column does NOT match", not same_header(HDR, HDR[:-1]))
check("renamed column does NOT match", not same_header(HDR, HDR[:-1] + ["Total"]))
check("case diff does NOT match", not same_header(HDR, [c.lower() for c in HDR]))

# ── continuation_sheet_names ─────────────────────────────────────────────────────────────────
sheets = [("Sheet2", list(HDR)),                       # true continuation
          ("Summary", ["Store", "Total Sales"]),       # summary tab — excluded
          ("Sheet3", [f"{c} " for c in HDR]),          # continuation w/ trailing spaces
          ("Notes", ["A", "B", "C", "D", "E", "F"]),   # same width, different names — excluded
          ("Sheet4", list(reversed(HDR)))]             # same names, reordered — excluded
got = continuation_sheet_names(HDR, sheets)
check("picks exactly the matching sheets, in order", got == ["Sheet2", "Sheet3"])
check("no extra sheets → empty", continuation_sheet_names(HDR, []) == [])
check("all mismatched → empty",
      continuation_sheet_names(HDR, [("S", ["X"] * len(HDR))]) == [])

# ── is_header_echo ───────────────────────────────────────────────────────────────────────────
check("exact echo row detected", is_header_echo(list(HDR), HDR))
check("echo with padding detected", is_header_echo([f" {c} " for c in HDR], HDR))
check("real data row is NOT an echo",
      not is_header_echo(["4640-A W Diversey Ave", "10000", "8/31/2026 7:58:27 PM",
                          "Cabrera, Natasha", "Total Wireless RTR Wallet", "$120.00"], HDR))
check("one column-name cell alone is NOT an echo (product literally named 'Store')",
      not is_header_echo(["Store", "", "", "", "", ""], HDR))
check("two matching cells + rest empty IS an echo",
      is_header_echo(["Store", "Trans ID", "", "", "", ""], HDR))
check("one cell mismatched → NOT an echo",
      not is_header_echo(["Store", "Trans ID", "8/31/2026", "", "", ""], HDR))
check("fully blank row is NOT an echo", not is_header_echo([""] * len(HDR), HDR))
check("'nan' strings treated as empty (pandas dtype=str artifact)",
      not is_header_echo(["nan"] * len(HDR), HDR))
check("width mismatch is NOT an echo", not is_header_echo(list(HDR[:-1]), HDR))
check("None cells treated as empty", not is_header_echo([None] * len(HDR), HDR))

# ── end-to-end shape: simulate the router's selection on the incident workbook ───────────────
# Sheet1 = truncated Aug data (24k rows), Sheet2 = header + 08-31 rows. The fix must select
# Sheet2 and drop its echoed header, so 08-31 rows reach the mapper.
book = [("Sheet1", list(HDR)), ("Sheet2", list(HDR))]
keep = continuation_sheet_names(book[0][1], book[1:])
check("incident shape: Sheet2 is kept", keep == ["Sheet2"])
sheet2_rows = [list(HDR),   # echoed header (pandas would make this a data row only if header=None;
                            # with header=0 it becomes columns — echo rule still guards paged re-prints)
               ["957 Pennsylvania Avenue", "9822", "8/31/2026 10:22:59 AM",
                "Sobhan, Salman", "Total Wireless RTR Wallet", "$40.00"]]
kept_rows = [r for r in sheet2_rows if not is_header_echo(r, HDR)]
check("incident shape: echo dropped, data row kept", kept_rows == sheet2_rows[1:])

print(f"harness_multisheet_ingest: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
