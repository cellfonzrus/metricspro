"""Proof harness for agent/commission/xreport-upload-honesty (2026-07-28).

OWNER LIVE BUG: a real B2B Soft (Web Explorer) X-Report uploaded through Data Imports returned
"✅ Saved 0 rows." — green, zero rows, zero explanation — while commcalc.pos_tender_summary stayed
EMPTY org-wide (no X-Report has ever ingested). Four defects made that outcome unreadable:

  D1  both pos_tender_summary upsert loops wrapped every row in `except Exception: pass`, so a TOTAL
      save failure still returned success with tenders:0.
  D2  `_parse_xreport` returned [] on ANY shape mismatch with no trace — and its tender-row loop
      `break`s on the FIRST unrecognized label, silently losing every row below it. The recognized
      set was a hard-coded 14 labels (RULE TWO violation).
  D3  both paths ended in {'success': True, 'tenders': 0} → the UI rendered a green "Saved 0 rows".
  D4  (found while fixing) the response never carried `saved` at all, so readUploadOutcome printed
      "Saved 0 rows" even on a GOOD upload, the email sweep recorded rows_saved=0 (its dedup only
      marks a message done when rows_saved > 0 ⇒ the same attachment re-ingests hourly), and
      _write_upload_trace read the key as 'tenants' — a typo for 'tenders'.

Proves, with NO DB and NO network (in-memory xlsx via openpyxl + an in-memory Supabase double):

  A. PARSER — documented real shape (2 store sheets, 'Tender Types | Sales | Refunds | Sub Net | Net'):
     rows parsed, per-sheet diag names header row + which wording matched.
  B. UNKNOWN LABEL MID-BLOCK — skip-and-record, not break. Includes a DIFFERENTIAL against the REAL
     pre-change `_parse_xreport` source (extracted from git origin/main) proving it lost the rows.
  C. HEADER WORDING DRIFT — 'Tender Type' singular now accepted (with net/refunds signals present);
     a genuinely unrecognizable header reports header_not_found + the closest row's cells VERBATIM.
  D. RULE TWO — the recognized vocabulary extends from the tenant's commcalc.closing_tender_map rows
     (report 'x_report'|'both'), read org-scoped: another org's mapping does NOT leak.
  E. FULL UPLOAD TAXONOMY over the REAL upload_file: happy path, every 0-row reason
     (no_sheets_matched / header_not_found / all_labels_unmatched / no_flat_columns /
     all_upserts_failed), plus the partial/unmapped caveats.
  F. D1 — an EXPLODING client surfaces save_failures + first_error instead of a green zero.
  G. FLAT/CSV path still works and now reports its columns honestly.
  H. TRACE + SWEEP — upload_trace rows_saved/status are honest; the email-sweep branch records
     'skipped' + the parser's reason instead of "ok · 0 rows".
  I. BACK-COMPAT — `_parse_xreport`'s signature/return shape is unchanged for its external caller
     (closing.tender_config.classify_sample_file), which still classifies the sample as 'x_report'.

Run:  cd backend && python3 scratchpad/xreport_upload_honesty_proof.py
"""
import asyncio
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from starlette.datastructures import UploadFile as _UF

import app.modules.commcalc.router as R

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {detail}")


HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"


# ── in-memory Supabase double ────────────────────────────────────────────────────────────────────
class FakeTable:
    def __init__(self, store, table, explode=None):
        self.store, self.table, self.explode = store, table, explode
        self._rows = list(store.get(table, []))

    def select(self, *a, **k):
        return self

    def eq(self, key, val):
        self._rows = [r for r in self._rows if r.get(key) == val]
        return self

    def in_(self, key, vals):
        self._rows = [r for r in self._rows if r.get(key) in vals]
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def insert(self, row):
        self.store.setdefault(self.table, [])
        (self.store[self.table].extend if isinstance(row, list) else self.store[self.table].append)(row)
        return self

    def upsert(self, row, on_conflict=None):
        if self.explode and self.table in self.explode:
            raise RuntimeError(self.explode[self.table])
        self.store.setdefault(self.table, [])
        rows = row if isinstance(row, list) else [row]
        keys = [k.strip() for k in (on_conflict or "").split(",") if k.strip()]
        for rec in rows:
            hit = None
            if keys:
                for existing in self.store[self.table]:
                    if all(existing.get(k) == rec.get(k) for k in keys):
                        hit = existing
                        break
            if hit is not None:
                hit.update(rec)
            else:
                self.store[self.table].append(dict(rec))
        return self

    def update(self, upd):
        self._upd = upd
        return self

    def delete(self):
        self._del = True
        return self

    def execute(self):
        class Res:
            pass
        res = Res()
        if getattr(self, "_upd", None) is not None:
            for r in self._rows:
                r.update(self._upd)
        res.data = self._rows
        res.count = len(self._rows)
        return res


class FakeSchema:
    def __init__(self, store, explode=None):
        self.store, self.explode = store, explode

    def table(self, t):
        return FakeTable(self.store, t, self.explode)


class FakeClient:
    def __init__(self, store, explode=None):
        self.store, self.explode = store, explode

    def schema(self, s):
        return FakeSchema(self.store, self.explode)


# ── xlsx fixtures (in-memory, openpyxl) ──────────────────────────────────────────────────────────
def make_xlsx(sheets):
    """sheets = {sheet_name: [[cell, ...], ...]} → .xlsx bytes."""
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name[:31])
        for r in rows:
            ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def xr_sheet(tender_rows, header=("Tender Types", "Sales", "Refunds", "Sub Net", "Net"),
             title="X-Report", trailer=True):
    """The DOCUMENTED real B2B Soft X-Report store sheet: title block, a 'Tendered Amounts' super
    header, the DETAILED tender header, the tender matrix, a blank row, then a totals section."""
    rows = [[title, "", "", "", ""],
            ["Printed 07/28/2026 06:50 PM", "", "", "", ""],
            ["Tendered Amounts", "", "", "", ""],
            list(header)]
    for label, sales, refunds, net in tender_rows:
        rows.append([label, sales, refunds, net, net])
    if trailer:
        rows.append(["", "", "", "", ""])
        rows.append(["Totals", "800.00", "20.00", "780.00", "780.00"])
        rows.append(["Cash", "9999.99", "0.00", "9999.99", "9999.99"])   # a LATER section's 'Cash'
    return rows


HAPPY = make_xlsx({
    "3 Palisade Ave": xr_sheet([("Cash", "500.00", "0.00", "500.00"),
                                ("Credit Card", "300.00", "20.00", "280.00")]),
    "100 Main St": xr_sheet([("Cash", "125.50", "0.00", "125.50")]),
})

UNKNOWN_MID = make_xlsx({
    "3 Palisade Ave": xr_sheet([("Cash", "500.00", "0.00", "500.00"),
                                ("Zelle", "75.00", "0.00", "75.00"),        # unrecognized
                                ("Credit Card", "300.00", "20.00", "280.00")]),
})

SINGULAR_HDR = make_xlsx({
    "3 Palisade Ave": xr_sheet([("Cash", "500.00", "0.00", "500.00")],
                               header=("Tender Type", "Sales", "Refunds", "Sub Net", "Net")),
})

DRIFTED_HDR = make_xlsx({
    "3 Palisade Ave": xr_sheet([("Cash", "500.00", "0.00", "500.00")],
                               header=("Payment Media", "Gross Sales", "Returned", "Net Total")),
})

ALL_UNKNOWN = make_xlsx({
    "3 Palisade Ave": xr_sheet([("Klarna", "500.00", "0.00", "500.00"),
                                ("Dish SmartPay", "75.00", "0.00", "75.00")]),
})

EMPTY_WB = make_xlsx({"Sheet1": []})


def to_csv(rows, columns=None):
    import csv
    cols = columns or list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue().encode("utf-8")


FLAT_CSV = to_csv([
    {"Store": "3 Palisade Ave", "Tender Type": "Cash", "Amount": "500.00", "Date": "2026-07-27"},
    {"Store": "3 Palisade Ave", "Tender Type": "Visa", "Amount": "300.00", "Date": "2026-07-27"},
    {"Store": "100 Main St", "Tender Type": "Cash", "Amount": "125.50", "Date": "2026-07-27"},
])
NO_COLUMNS_CSV = to_csv([{"Foo": "1", "Bar": "2"}, {"Foo": "3", "Bar": "4"}])
EMPTY_CSV = b"Foo,Bar\n"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== A. PARSER — the documented real X-Report shape ===")

rows, diag = R._parse_xreport_detail(HAPPY, "X-Report_07272026-07272026.xlsx")
check("A1 3 tender rows parsed across 2 store sheets", len(rows) == 3, rows)
check("A2 date from the single-day filename range", all(r[1] == "2026-07-27" for r in rows), rows)
check("A3 store = sheet name", {r[0] for r in rows} == {"3 Palisade Ave", "100 Main St"}, rows)
check("A4 net amount read from the LAST 'Net' column", sorted(r[3] for r in rows) == [125.5, 280.0, 500.0],
      [r[3] for r in rows])
check("A5 diag.sheets_read = 2, headers_found = 2", diag["sheets_read"] == 2 and diag["headers_found"] == 2,
      diag)
s0 = diag["sheets"][0]
check("A6 per-sheet: header_row = 3 (the DETAILED header, not the super-header)", s0["header_row"] == 3, s0)
check("A7 per-sheet: which wording matched is reported",
      s0["header_wording"] == "Tender Types + Net + Refunds", s0)
check("A8 per-sheet outcome 'rows' + matched 2 / skipped 0",
      s0["outcome"] == "rows" and s0["matched"] == 2 and s0["skipped"] == 0, s0)
check("A9 the blank row still ENDS the block — the later section's 9999.99 'Cash' is NOT ingested",
      all(r[3] != 9999.99 for r in rows), rows)
check("A10 no unmatched labels on a clean file", diag["unmatched_labels"] == [], diag)

# NO-BLANK-SEPARATOR guard: removing the `break` means the scan can reach a later section on an export
# whose totals block is not preceded by a blank row. FIRST occurrence wins, so the real drawer figure
# can never be overwritten by a totals figure (the caller's dedupe was LAST-wins).
_nogap = make_xlsx({"3 Palisade Ave": [
    ["X-Report", "", "", "", ""],
    ["Tendered Amounts", "", "", "", ""],
    ["Tender Types", "Sales", "Refunds", "Sub Net", "Net"],
    ["Cash", "500.00", "0.00", "500.00", "500.00"],
    ["Grand Totals", "", "", "", ""],
    ["Cash", "9999.99", "0.00", "9999.99", "9999.99"],
]})
rows_ng, diag_ng = R._parse_xreport_detail(_nogap, "X-Report_07272026-07272026.xlsx")
check("A11 NO blank separator: the FIRST 'Cash' (500.00) wins, the later section's 9999.99 cannot "
      "overwrite it", [r[3] for r in rows_ng] == [500.0], rows_ng)
check("A12 the duplicate row is COUNTED, not hidden", diag_ng["duplicate_label_rows"] == 1, diag_ng)

try:
    R._parse_xreport_detail(HAPPY, "X-Report_07012026-07272026.xlsx")
    _mr = "no raise"
except ValueError as e:
    _mr = str(e)
check("A13 multi-day filename range still raises ValueError → 400 (unchanged)",
      "SINGLE day" in _mr, _mr)

_nb = make_xlsx({"3 Palisade Ave": xr_sheet([("Credit  Card", "300.00", "20.00", "280.00")])})
rows_nb, _ = R._parse_xreport_detail(_nb, "X-Report_07272026-07272026.xlsx")
check("A14 an INTERNAL nbsp/double-space in 'Credit  Card' now matches (normalize, not remap)",
      len(rows_nb) == 1 and rows_nb[0][3] == 280.0, rows_nb)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== B. UNKNOWN LABEL MID-BLOCK — skip-and-record, not break (D2c) ===")

rows, diag = R._parse_xreport_detail(UNKNOWN_MID, "X-Report_07272026-07272026.xlsx")
check("B1 the rows BELOW the unknown label survive (Cash + Credit Card)",
      len(rows) == 2 and sorted(r[2] for r in rows) == ["Cash", "Credit Card"], rows)
check("B2 nothing is invented for the unknown label (no 'Zelle' row)",
      all(r[2] != "Zelle" for r in rows), rows)
check("B3 the skipped label is reported VERBATIM", diag["unmatched_labels"] == ["Zelle"], diag)
check("B4 per-sheet counters: matched 2 / skipped 1",
      diag["sheets"][0]["matched"] == 2 and diag["sheets"][0]["skipped"] == 1, diag["sheets"][0])

# DIFFERENTIAL vs the REAL pre-change parser source on origin/main
old_src = subprocess.check_output(
    ["git", "-C", REPO, "show", "origin/main:backend/app/modules/commcalc/router.py"]).decode("utf-8")
m = re.search(r"\ndef _parse_xreport\(.*?\n    return out\n", old_src, re.S)
ns = {"pd": R.pd, "io": R.io, "datetime": R.datetime, "timezone": R.timezone,
      "settings": R.settings, "safe_float": R.safe_float, "_XR_TENDERS": R._XR_TENDERS}
exec(m.group(0), ns)
old_rows = ns["_parse_xreport"](UNKNOWN_MID, "X-Report_07272026-07272026.xlsx")
check("B5 DIFFERENTIAL: the OLD parser lost every row below the unknown label (1 of 3)",
      len(old_rows) == 1 and old_rows[0][2] == "Cash", old_rows)
old_happy = ns["_parse_xreport"](HAPPY, "X-Report_07272026-07272026.xlsx")
new_happy, _ = R._parse_xreport_detail(HAPPY, "X-Report_07272026-07272026.xlsx")
check("B6 DIFFERENTIAL: on a CLEAN file old and new agree byte-for-byte (no drift)",
      old_happy == new_happy, (old_happy, new_happy))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== C. HEADER WORDING (D2b) ===")

rows, diag = R._parse_xreport_detail(SINGULAR_HDR, "X-Report_07272026-07272026.xlsx")
check("C1 'Tender Type' (singular) accepted when Net + Refunds are on the same row", len(rows) == 1, rows)
check("C2 the accepted wording is reported, not assumed",
      diag["sheets"][0]["header_wording"] == "Tender Type + Net + Refunds", diag["sheets"][0])

rows, diag = R._parse_xreport_detail(DRIFTED_HDR, "X-Report_07272026-07272026.xlsx")
check("C3 an unrecognizable header yields 0 rows (unchanged — nothing invented)", rows == [], rows)
check("C4 but the sheet outcome is now NAMED 'header_not_found'",
      diag["sheets"][0]["outcome"] == "header_not_found", diag["sheets"][0])
cr = diag["sheets"][0]["closest_row"]
_near = ([cr] + list(cr.get("others") or [])) if cr else []
_cells = [c for n in _near for c in (n.get("cells") or [])]
check("C5 the CLOSEST-looking rows are reported with their cells VERBATIM (the section caption AND "
      "the real column header — one heuristic winner would have hidden the useful one)",
      "Tendered Amounts" in _cells and "Payment Media" in _cells and "Net Total" in _cells, _near)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== D. RULE TWO — vocabulary extends from commcalc.closing_tender_map (org-scoped) ===")

MAP_STORE = {"closing_tender_map": [
    {"org_id": LUX, "tender_key": "zelle", "report": "x_report", "source_labels": ["Zelle", "CashApp"]},
    {"org_id": LUX, "tender_key": "acima", "report": "both", "source_labels": ["Acima Lease"]},
    {"org_id": LUX, "tender_key": "gift", "report": "sales", "source_labels": ["Gift Cert"]},
    {"org_id": HOUSE, "tender_key": "klarna", "report": "x_report", "source_labels": ["Klarna"]},
]}
labs = R._xreport_config_labels(FakeClient(MAP_STORE), LUX)
check("D1 luxelink's x_report + both labels load", labs == {"Zelle", "CashApp", "Acima Lease"}, labs)
check("D2 the 'sales'-leg rule is NOT applied to the x_report leg", "Gift Cert" not in labs, labs)
check("D3 ORG ISOLATION: the house org's 'Klarna' does not leak into luxelink", "Klarna" not in labs, labs)
check("D4 an un-migrated / missing table degrades to an empty set (no crash)",
      R._xreport_config_labels(FakeClient({}), LUX) == set())

rows, diag = R._parse_xreport_detail(UNKNOWN_MID, "X-Report_07272026-07272026.xlsx",
                                     extra_labels=labs)
check("D5 with the tenant mapping, 'Zelle' now INGESTS (3 rows, no code change)",
      len(rows) == 3 and any(r[2] == "Zelle" for r in rows), rows)
check("D6 diag counts the tenant labels that extended the vocabulary",
      diag["config_label_count"] == 3 and diag["builtin_label_count"] == len(R._XR_TENDERS), diag)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== E/F/G. FULL UPLOAD TAXONOMY over the REAL upload_file ===")

_orig_sb = R.sb


def run_upload(contents, filename="X-Report_07272026-07272026.xlsx", org=HOUSE, store=None,
               explode=None, close_date=""):
    store = {} if store is None else store
    R.sb = lambda: FakeClient(store, explode)
    try:
        uf = _UF(io.BytesIO(contents), filename=filename)
        res = asyncio.get_event_loop().run_until_complete(
            R.upload_file("x_report", uf, "", force=False, close_date=close_date, org_id=org))
    finally:
        R.sb = _orig_sb
    return res, store


# ── E1 happy path ────────────────────────────────────────────────────────────────────────────────
res, st = run_upload(HAPPY)
check("E1 success True + saved 3 + tenders 3 (the `saved` key the sweeps/UI read — D4)",
      res.get("success") is True and res.get("saved") == 3 and res.get("tenders") == 3, res)
check("E2 parser_path 'multi_sheet' + format 'multi-sheet' (existing key preserved)",
      res.get("parser_path") == "multi_sheet" and res.get("format") == "multi-sheet", res)
check("E3 no `skipped` marker on a clean save", res.get("skipped") is None, res)
check("E4 3 rows really written to pos_tender_summary, all org-stamped",
      len(st.get("pos_tender_summary", [])) == 3
      and all(r.get("org_id") == HOUSE for r in st["pos_tender_summary"]),
      st.get("pos_tender_summary"))
check("E5 tender_class derived (cash/card)",
      sorted({r["tender_class"] for r in st["pos_tender_summary"]}) == ["card", "cash"],
      st.get("pos_tender_summary"))
tr = (st.get("upload_trace") or [{}])[-1]
check("E6 upload_trace rows_saved = 3 (was NULL/store-count via the 'tenants' typo — D4)",
      tr.get("rows_saved") == 3 and tr.get("status") == "ok", tr)
check("E7 upload_trace target_table = pos_tender_summary", tr.get("target_table") == "pos_tender_summary", tr)

# ── E8 all_labels_unmatched ──────────────────────────────────────────────────────────────────────
res, st = run_upload(ALL_UNKNOWN)
check("E8 success FALSE on 0 saved (never a plain success — D3)", res.get("success") is False, res)
check("E9 reason = 'all_labels_unmatched'", res.get("skipped") == "all_labels_unmatched", res)
check("E10 the unrecognized labels ride back VERBATIM",
      res["xreport_diag"]["unmatched_labels"] == ["Klarna", "Dish SmartPay"], res["xreport_diag"])
check("E11 the note names the labels AND where to map them",
      "Klarna" in res["note"] and "Tender Config" in res["note"], res.get("note"))
check("E12 nothing written", not st.get("pos_tender_summary"), st.get("pos_tender_summary"))
tr = (st.get("upload_trace") or [{}])[-1]
check("E13 upload_trace status 'skipped' + the diag rides on `guard`",
      tr.get("status") == "skipped" and "xreport_diag" in (tr.get("guard") or {}), tr)

# ── E14 header_not_found ─────────────────────────────────────────────────────────────────────────
res, st = run_upload(DRIFTED_HDR)
check("E14 reason = 'header_not_found'", res.get("skipped") == "header_not_found", res)
check("E15 the note prints the closest-looking rows' cells VERBATIM",
      "Payment Media" in (res.get("note") or "") and "Net Total" in (res.get("note") or ""),
      res.get("note"))
check("E16 the note also reports what the FLAT fallback saw (both diagnoses, one message)",
      "flat fallback" in (res.get("note") or ""), res.get("note"))
check("E17 parser_path 'neither'", res.get("parser_path") == "neither", res)

# ── E18 no_flat_columns (a CSV that is not an X-report at all) ───────────────────────────────────
res, st = run_upload(NO_COLUMNS_CSV, filename="whatever.csv")
check("E18 reason = 'no_flat_columns'", res.get("skipped") == "no_flat_columns", res)
check("E19 the note lists the columns actually found",
      "Foo" in (res.get("note") or "") and "Bar" in (res.get("note") or ""), res.get("note"))
check("E20 xreport_diag.flat names which of store/tender/amount columns matched (all None)",
      res["xreport_diag"]["flat"]["tender_col"] is None
      and res["xreport_diag"]["flat"]["rows"] == 2, res["xreport_diag"]["flat"])

# ── E21 no_sheets_matched ────────────────────────────────────────────────────────────────────────
res, st = run_upload(EMPTY_CSV, filename="empty.csv")
check("E21 an empty file → 'no_sheets_matched' (not a green zero)",
      res.get("skipped") == "no_sheets_matched" and res.get("success") is False, res)
res, st = run_upload(EMPTY_WB, filename="X-Report_07272026-07272026.xlsx")
check("E22 an EMPTY workbook → 'no_sheets_matched' too (every sheet empty)",
      res.get("skipped") == "no_sheets_matched", res)
check("E23 the empty-workbook note says every sheet was empty (not 'no header')",
      "EVERY one was empty" in (res.get("note") or ""), res.get("note"))

# ── F. D1: every upsert fails ────────────────────────────────────────────────────────────────────
res, st = run_upload(HAPPY, explode={"pos_tender_summary": "duplicate key value violates unique constraint"})
check("F1 D1 FIXED: a total save failure is no longer swallowed → success False",
      res.get("success") is False, res)
check("F2 reason = 'all_upserts_failed'", res.get("skipped") == "all_upserts_failed", res)
check("F3 save_failures counted (3 attempts, 3 failures)",
      res.get("save_failures") == 3 and res["xreport_diag"]["upsert_attempts"] == 3, res)
check("F4 the FIRST real DB error is surfaced verbatim",
      "duplicate key value" in (res.get("first_error") or ""), res.get("first_error"))
check("F5 the note points at mig 062's UNIQUE constraint the upsert needs",
      "062" in (res.get("note") or ""), res.get("note"))

# partial save: one store's writes fail, the other's land
class HalfExplode(FakeClient):
    def __init__(self, store):
        super().__init__(store)
        self.n = 0

    def schema(self, s):
        outer = self

        class S(FakeSchema):
            def table(self, t):
                if t == "pos_tender_summary":
                    outer.n += 1
                    if outer.n == 1:
                        return FakeTable(outer.store, t, {"pos_tender_summary": "network blip"})
                return FakeTable(outer.store, t)
        return S(self.store)


_st = {}
R.sb = lambda: HalfExplode(_st)
try:
    res = asyncio.get_event_loop().run_until_complete(
        R.upload_file("x_report", _UF(io.BytesIO(HAPPY), filename="X-Report_07272026-07272026.xlsx"),
                      "", force=False, org_id=HOUSE))
finally:
    R.sb = _orig_sb
check("F6 PARTIAL save → success True, saved 2, save_failures 1",
      res.get("success") is True and res.get("saved") == 2 and res.get("save_failures") == 1, res)
check("F7 marked 'x_report_partial_save' with the first error in the note",
      res.get("skipped") == "x_report_partial_save" and "network blip" in (res.get("note") or ""), res)
tr = (_st.get("upload_trace") or [{}])[-1]
check("F8 upload_trace status 'partial' (rows DID land — not 'skipped')", tr.get("status") == "partial", tr)

# unmapped-label caveat on an otherwise successful ingest
res, st = run_upload(UNKNOWN_MID)
check("F9 rows saved BUT an unmapped label present → 'x_report_unmapped_labels' (amber, not green)",
      res.get("saved") == 2 and res.get("skipped") == "x_report_unmapped_labels", res)
check("F10 the note says the skipped label's dollars are MISSING from the recon",
      "Zelle" in (res.get("note") or "") and "missing from the recon" in (res.get("note") or ""),
      res.get("note"))

# ── G. flat / CSV path ───────────────────────────────────────────────────────────────────────────
res, st = run_upload(FLAT_CSV, filename="xreport.csv")
check("G1 the flat CSV path still ingests (3 tenders, 2 stores)",
      res.get("saved") == 3 and res.get("stores") == 2, res)
check("G2 parser_path 'flat' + format 'flat'",
      res.get("parser_path") == "flat" and res.get("format") == "flat", res)
check("G3 the flat diag names the matched columns",
      res["xreport_diag"]["flat"]["store_col"] == "Store"
      and res["xreport_diag"]["flat"]["tender_col"] == "Tender Type"
      and res["xreport_diag"]["flat"]["amount_col"] == "Amount", res["xreport_diag"]["flat"])
check("G4 rows land under the file's own date, org-stamped",
      all(r["close_date"] == "2026-07-27" and r["org_id"] == HOUSE for r in st["pos_tender_summary"]),
      st.get("pos_tender_summary"))

# ── D7: the tenant mapping reaches the REAL upload path (org-scoped) ────────────────────────────
st = {"closing_tender_map": list(MAP_STORE["closing_tender_map"])}
res, st = run_upload(UNKNOWN_MID, org=LUX, store=st)
check("D7 RULE TWO end-to-end: with luxelink's mapping the 'Zelle' row INGESTS (3 saved, clean)",
      res.get("saved") == 3 and res.get("skipped") is None, res)
st2 = {"closing_tender_map": list(MAP_STORE["closing_tender_map"])}
res2, st2 = run_upload(UNKNOWN_MID, org="11111111-1111-1111-1111-111111111111", store=st2)
check("D8 a THIRD org with no mapping still skips 'Zelle' (no cross-tenant vocabulary leak)",
      res2.get("saved") == 2 and res2.get("skipped") == "x_report_unmapped_labels", res2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== H. EMAIL-SWEEP honesty (the branch that used to record 'ok · 0 rows') ===")

SWEEP_STORE = {
    "email_sweep_config": [{"org_id": HOUSE, "account": "default", "imap_host": "imap.example.com",
                            "patterns": [{"pattern": "*X-Report*", "upload_type": "x_report"}]}],
}
ATTACH = {"message_id": "<msg-x@b2bsoft>", "name": "X-Report_07272026-07272026.xlsx", "size": 4096,
          "upload_type": "x_report", "bytes": ALL_UNKNOWN}
_orig_fetch = R._email.fetch_new_attachments


def fake_fetch(cfg, already):
    if (ATTACH["message_id"], ATTACH["name"]) in already:
        return []
    return [dict(ATTACH)]


R._email.fetch_new_attachments = fake_fetch
R.sb = lambda: FakeClient(SWEEP_STORE)
try:
    out = asyncio.get_event_loop().run_until_complete(R._run_email_sweep(HOUSE, "default"))
finally:
    R._email.fetch_new_attachments = _orig_fetch
    R.sb = _orig_sb
row = (SWEEP_STORE.get("email_processed") or [{}])[-1]
check("H1 sweep records status 'skipped' (was a green 'ok · 0 rows')", row.get("status") == "skipped", row)
check("H2 the history detail carries the parser's own reason (the labels)",
      "Klarna" in (row.get("detail") or ""), row.get("detail"))
check("H3 rows_saved 0 → the dedup keeps retrying, so it self-heals once mapped",
      row.get("rows_saved") == 0, row)

SWEEP2 = {"email_sweep_config": list(SWEEP_STORE["email_sweep_config"])}
ATTACH["bytes"] = HAPPY
R._email.fetch_new_attachments = fake_fetch
R.sb = lambda: FakeClient(SWEEP2)
try:
    asyncio.get_event_loop().run_until_complete(R._run_email_sweep(HOUSE, "default"))
finally:
    R._email.fetch_new_attachments = _orig_fetch
    R.sb = _orig_sb
row = (SWEEP2.get("email_processed") or [{}])[-1]
check("H4 a GOOD X-Report now records ok + rows_saved 3 (was 0 → re-ingested every hour)",
      row.get("status") == "ok" and row.get("rows_saved") == 3, row)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== I. BACK-COMPAT for the external caller (closing.tender_config) ===")

from app.modules.closing import tender_config as TC
leg, labels, detail = TC.classify_sample_file(HAPPY, "X-Report_07272026-07272026.xlsx")
check("I1 classify_sample_file still detects the multi-sheet X-Report leg", leg == "x_report", (leg, detail))
check("I2 it still gets the distinct tender labels", labels == {"Cash", "Credit Card"}, labels)
check("I3 _parse_xreport keeps its 3-positional-arg signature + list return",
      isinstance(R._parse_xreport(HAPPY, "X-Report_07272026-07272026.xlsx", None), list))
leg2, labels2, _ = TC.classify_sample_file(UNKNOWN_MID, "X-Report_07272026-07272026.xlsx")
check("I4 BONUS: the sample wizard now ALSO surfaces the rows below an unknown label",
      leg2 == "x_report" and labels2 == {"Cash", "Credit Card"}, labels2)
try:
    TC.classify_sample_file(HAPPY, "X-Report_07012026-07272026.xlsx")
    _v = "no raise"
except ValueError as e:
    _v = str(e)
check("I5 the multi-day ValueError still propagates to the wizard", "SINGLE day" in _v, _v)


print(f"\n{'='*100}\nRESULT: {PASS} passed, {FAIL} failed\n{'='*100}")
sys.exit(1 if FAIL else 0)
