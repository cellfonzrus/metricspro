"""Offline proof harness — the POS X-Report dropped the External Credit Card tender row.

OWNER BUG REPORT 2026-09-07, with the primary document attached: the B2B Soft X-Report for
117 E Burnside Ave on 2026-09-05 reads

    Cash                     522.08 ... 522.08
    Check                      0.00 ...   0.00
    Externel Credit Card     112.22 ... 112.22   <-- the POS's own spelling
    Gift Card                  0.00 ...   0.00
    Store Account              0.00 ...   0.00
                             $634.30

while the closing money reconciliation for that store-day showed

    cash:   closing $521.00 vs X-report $522.08   d -$1.08
    credit: closing $113.97 vs X-report   $0.00   d +$113.97     <-- the whole amount, as a gap

ROOT CAUSE - TWO VOCABULARIES FOR ONE THING. `commcalc/router._XR_TENDERS` is the INGEST list;
`closing/router._canon_tender` + `CANON_TENDER_LABEL` is the 3-way RECON axis, and the recon has
known 'External Credit Card' as `ext_cc` all along. The ingest list never carried it in any spelling,
so the row was skipped at import, never reached `commcalc.pos_tender_summary`, and the recon then
compared a real declared $113.97 against a $0.00 that only meant "we did not store this row". Cash on
the SAME sheet ingested fine, which is exactly what made this read as "the POS is not capturing"
rather than "one label was dropped" - the same shape as the 2026-09-07 store-resolver defect.

THE FIX: a label none of the lists carry is offered to the RECON's own canonical mapper before being
skipped (`_xr_canon_known`), so the two ends share ONE vocabulary. It is substring-based ('ext' ->
ext_cc), so the POS's misspelling costs nothing and no carrier/vendor literal enters the code
(RULE TWO). The permissive match is fenced by `_xr_is_amount`, so it can only ever accept a row that
actually carries a number in the Net column.

Also folded in: `_xr_tender_class` - ONE cash/card/other rule for BOTH ingest paths. The multi-sheet
loop inlined its own ternary and the flat loop its own `_tclass`, and the two had already drifted
(only the flat one knew 'cc' / 'chip' / 'emv').

Runs the REAL parser against a REAL .xlsx built in memory. No DB, no network.
Run: `cd backend && python3 harness_xreport_tender_vocab.py`
"""
import io
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


import pandas as pd                                    # noqa: E402
from app.modules.commcalc import router as R           # noqa: E402
from app.modules.closing import router as CR           # noqa: E402

STORE = "117 E Burnside Ave"
FNAME = "X-Report_09052026-09052026.xlsx"

# The sheet exactly as B2B Soft writes it: a preamble, the section caption, ONE flattened header row,
# then the tender matrix, then a totals row whose label cell is blank (that blank ends the block).
HEADER = ["Tender Types", "Sales", "Returns/Trade In", "Sub Net", "Drop", "Pickup",
          "Payments", "Refunds", "Net"]


def money_row(label, amt):
    return [label, f"{amt:.2f}", "0.00", f"{amt:.2f}", "0.00", "0.00", "0.00", "0.00", f"{amt:.2f}"]


def sheet_rows(extra=None):
    rows = [
        ["Report Date:9/5/2026 - 9/5/2026", "", "Locations:117 E Burnside Ave", "", "", "", "", "", ""],
        ["Report Grouping:No grouping", "", "", "", "", "", "", "", ""],
        ["Tendered Amounts", "", "", "", "", "", "", "", ""],
        HEADER,
        money_row("Cash", 522.08),
        money_row("Check", 0.00),
        money_row("Externel Credit Card", 112.22),
        money_row("Gift Card", 0.00),
        money_row("Store Account", 0.00),
    ]
    rows += (extra or [])
    rows += [["", "$634.30", "$0.00", "$634.30", "$0.00", "$0.00", "$0.00", "$0.00", "$634.30"]]
    return rows


def workbook(rows, store=STORE):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name=store, header=False, index=False)
    return buf.getvalue()


def parsed(rows=None, store=STORE, fname=FNAME):
    out, diag = R._parse_xreport_detail(workbook(rows or sheet_rows(), store), fname)
    return {t: a for (_s, _d, t, a) in out}, diag, out


print("\n== A. one vocabulary: the recon's canonical mapper is consulted before a label is dropped ==")
check("A1 the POS's own spelling 'Externel Credit Card' is recognized",
      R._xr_canon_known("externel credit card") is True)
check("A2 so is the correct spelling",
      R._xr_canon_known("external credit card") is True)
check("A3 it resolves to the recon's ext_cc bucket, not a new one",
      CR._canon_tender("Externel Credit Card") == "ext_cc")
check("A4 'External Credit Card' is the recon's own display label for that bucket - the two ends "
      "were always describing the same tender",
      CR.CANON_TENDER_LABEL["ext_cc"] == "External Credit Card")
check("A5 the ingest list still does NOT carry it - the fix is the shared mapper, not a 44th literal",
      not any("ext" in t for t in R._XR_TENDERS))
check("A6 a label the recon genuinely cannot place stays unknown",
      R._xr_canon_known("loyalty points") is False and R._xr_canon_known("check") is False)
check("A7 an empty label is never accepted", R._xr_canon_known("") is False)


print("\n== B. the permissive match is fenced: it may only accept a real matrix row ==")
check("B1 a plain amount qualifies", R._xr_is_amount("112.22") is True)
check("B2 so does an accounting-formatted one", R._xr_is_amount("$1,234.56") is True
      and R._xr_is_amount("(45.00)") is True)
check("B3 a blank cell does not", R._xr_is_amount("") is False and R._xr_is_amount(None) is False)
check("B4 prose does not - this is what stops 'Cash Drawer Detail' being read as a tender",
      R._xr_is_amount("Detail") is False)


print("\n== C. ONE cash/card/other rule for both parser paths ==")
for lab, want in [("Cash", "cash"), ("Externel Credit Card", "card"), ("Credit Card", "card"),
                  ("Debit", "card"), ("CC", "card"), ("Chip", "card"), ("EMV", "card"),
                  ("Check", "other"), ("Store Account", "other")]:
    check(f"C {lab!r} -> {want}", R._xr_tender_class(lab) == want, R._xr_tender_class(lab))
_src = open("app/modules/commcalc/router.py").read()
check("C10 the multi-sheet upsert no longer inlines its own class ternary",
      '"tender_class": _xr_tender_class(tender)' in _src
      and '"cash" if "cash" in tender.lower()' not in _src)
check("C11 the flat path is the SAME function, not a second copy",
      "_tclass = _xr_tender_class" in _src)
check("C12 both ingest paths now agree on a bare 'CC' (the drift the two copies had)",
      R._xr_tender_class("CC") == "card")
# Found BY this harness while folding the two copies together. The flat path's 'cc' hint was a
# SUBSTRING test, and "store a-cc-ount" contains it — so every store-account dollar a flat-shaped
# X-report carried was written as tender_class 'card' and then compared against declared CREDIT in
# the closing money recon. The multi-sheet path never had that bug (it carried no 'cc' at all), so
# the two parsers disagreed about the same label; the shared rule keeps the multi-sheet answer.
check("C13 'Store Account' is NOT card - the register codes are whole tokens, not substrings",
      R._xr_tender_class("Store Account") == "other", R._xr_tender_class("Store Account"))
check("C14 nor is 'On Account' / 'Account'",
      R._xr_tender_class("On Account") == "other" and R._xr_tender_class("Account") == "other")
check("C15 the codes still match when they really are the label",
      all(R._xr_tender_class(x) == "card" for x in ("cc", "CC", "Chip & PIN", "EMV chip", "cc/debit")))
check("C16 and the descriptive words are still substrings",
      R._xr_tender_class("VISA/MC settled") == "card")


print("\n== D. END TO END on the owner's real 2026-09-05 Burnside sheet ==")
amts, diag, rows = parsed()
check("D1 the Cash row still ingests, unchanged", amts.get("Cash") == 522.08, amts)
check("D2 the External Credit Card row NOW ingests at its Net figure",
      amts.get("Externel Credit Card") == 112.22, amts)
check("D3 the sheet's own $634.30 total is fully accounted for",
      round(sum(amts.values()), 2) == 634.30, amts)
check("D4 it is recorded as a canon match, so a spelling the POS invents is visible, not merely working",
      diag["canon_matched_rows"] == 1 and diag["canon_matched_labels"] == ["Externel Credit Card"],
      diag.get("canon_matched_labels"))
check("D5 nothing on the sheet is skipped any more", diag["tender_rows_skipped"] == 0, diag)
check("D6 the date comes off the single-day filename", diag["date"] == "2026-09-05", diag["date"])
check("D7 the store is the sheet name, verbatim (the address the resolver maps)",
      {s for (s, _d, _t, _a) in rows} == {STORE})
check("D8 the blank-label totals row still ends the block - $634.30 is never read as a tender",
      "" not in amts and 634.30 not in [v for k, v in amts.items() if k != "Cash"])


print("\n== E. REGRESSION - the same sheet under the PRE-FIX rule ==")
# Exactly the old acceptance test: the canonical mapper is not consulted at all.
_real = R._xr_canon_known
R._xr_canon_known = lambda _l: False
try:
    old_amts, old_diag, _ = parsed()
finally:
    R._xr_canon_known = _real
check("E1 the credit row WAS dropped before this fix", "Externel Credit Card" not in old_amts, old_amts)
check("E2 and only $522.08 of the sheet's $634.30 reached the recon",
      round(sum(old_amts.values()), 2) == 522.08, old_amts)
check("E3 the $112.22 the store really took is the exact money the recon could not see",
      round(634.30 - sum(old_amts.values()), 2) == 112.22)
check("E4 cash was unaffected then and now - which is why this read as a POS capture failure",
      old_amts.get("Cash") == amts.get("Cash") == 522.08)


print("\n== F. the money reconciliation the owner was shown, recomputed ==")
DECLARED_CASH, DECLARED_CREDIT = 521.00, 113.97
xcash = sum(a for t, a in amts.items() if R._xr_tender_class(t) == "cash")
xcard = sum(a for t, a in amts.items() if R._xr_tender_class(t) == "card")
check("F1 X-report cash is unchanged at 522.08", round(xcash, 2) == 522.08)
check("F2 X-report credit is 112.22, not 0.00", round(xcard, 2) == 112.22, xcard)
check("F3 the cash variance the owner saw is reproduced exactly",
      round(DECLARED_CASH - xcash, 2) == -1.08)
check("F4 the credit variance drops from the whole tender to a real $1.75",
      round(DECLARED_CREDIT - xcard, 2) == 1.75, round(DECLARED_CREDIT - xcard, 2))
old_xcard = sum(a for t, a in old_amts.items() if R._xr_tender_class(t) == "card")
check("F5 the PRE-FIX credit variance was the owner's reported +$113.97",
      round(DECLARED_CREDIT - old_xcard, 2) == 113.97)
check("F6 a tender the sheet shows as 0.00 still ties out at 0.00, not as a gap",
      round(0.0 - sum(a for t, a in amts.items() if t == "Gift Card"), 2) == 0.0)


print("\n== G. a label NOTHING can place is still skipped AND still named ==")
amts2, diag2, _ = parsed(sheet_rows(extra=[money_row("Loyalty Points", 9.99)]))
check("G1 it does not silently become money", "Loyalty Points" not in amts2, amts2)
check("G2 it is counted as skipped", diag2["tender_rows_skipped"] == 1, diag2)
check("G3 and reported VERBATIM so it can be mapped under Closing -> Tender Config",
      diag2["unmatched_labels"] == ["Loyalty Points"], diag2["unmatched_labels"])
check("G4 the rows around it still ingest - an unknown label never ends the block",
      amts2.get("Externel Credit Card") == 112.22 and amts2.get("Cash") == 522.08)
out2 = R._xreport_outcome(saved=5, path="multi_sheet", diag=diag2, flat_diag=None, attempts=5,
                          save_failures=0, first_error=None, rows_read=5, stores=1,
                          date="2026-09-05")
check("G5 the upload refuses a clean green tick while a label's dollars are missing",
      out2.get("skipped") == "x_report_unmapped_labels" and "Loyalty Points" in out2.get("note", ""),
      out2.get("skipped"))
out1 = R._xreport_outcome(saved=5, path="multi_sheet", diag=diag, flat_diag=None, attempts=5,
                          save_failures=0, first_error=None, rows_read=5, stores=1,
                          date="2026-09-05")
check("G6 the owner's sheet now uploads with NO caveat at all",
      out1.get("success") is True and "skipped" not in out1, out1.get("skipped"))
check("G7 and the canon match is carried in the diagnostic",
      out1["xreport_diag"]["canon_matched_labels"] == ["Externel Credit Card"])


print("\n== H. a caption below the matrix cannot be swallowed by the permissive match ==")
# No blank row before it: the ONLY thing standing between 'Cash Drawer Detail' (which contains
# 'cash') and being read as a $0 tender is the _xr_is_amount fence.
amts3, diag3, _ = parsed(sheet_rows(extra=[["Cash Drawer Detail", "", "", "", "", "", "", "", ""]]))
check("H1 the caption is not ingested as a tender", "Cash Drawer Detail" not in amts3, amts3)
check("H2 the real tenders above it are untouched",
      amts3.get("Cash") == 522.08 and amts3.get("Externel Credit Card") == 112.22)


print("\n== I. no carrier / tenant / vendor literal enters the vocabulary (RULE TWO) ==")
# Scan CODE, not prose. The docstrings in this package deliberately cite the owner's real store and
# the real POS product as the EVIDENCE for the fix; a guard that reads comments would fail on its own
# audit trail and push the next person to delete the evidence (harness_tax_collected hit exactly this
# on 2026-09-07). tokenize drops COMMENT and STRING tokens, leaving identifiers and operators.
import io as _io, tokenize as _tok                                             # noqa: E402
_seg = _src[_src.index("def _xr_canon_known"):_src.index("def _parse_xreport_detail")]
_code = []
for _t in _tok.generate_tokens(_io.StringIO(_seg).readline):
    if _t.type not in (_tok.COMMENT, _tok.STRING):
        _code.append(_t.string)
_code = " ".join(_code).lower()
check("I0 the scan really did drop the prose (the docstring's own evidence is not in it)",
      "burnside" in _seg.lower() and "burnside" not in _code)
for banned in ("burnside", "b2bsoft", "cellfonz", "metro", "t-mobile", "verizon", "at&t", "acima"):
    check(f"I {banned!r} appears in no vocabulary or class rule",
          banned not in " ".join(R._XR_CARD_WORDS).lower() and banned not in _code)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
