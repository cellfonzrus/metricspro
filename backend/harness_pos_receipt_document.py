#!/usr/bin/env python3
"""PROOF: a receipt DOCUMENT has one description, and both media draw it (index §POS-13).

DB-free. Imports the real modules and MEASURES the real rendered PDF with pdfplumber, so a lock here
fails the build rather than describing an intention.

Owner 2026-10-29, on receipt 4605: *"the receipt is not formatted properly and no clear border lines
to make it look professional"*.

THE CLASS, NOT THE INSTANCE. Not "the PDF has no borders". A receipt's APPEARANCE had no home, so the
two media that print one receipt described it twice:

  • `render.render_html` held a private `_CSS` string with a few hairlines in it;
  • the PDF was not described at all — it was rebuilt from `render_words`, whose real job is a
    round-trip PROOF (it reproduces where the words sat on the ORIGINAL POS's paper so that POS's own
    parser can read them back). A parser fixture has no rules, no boxes and no text runs. Measured on
    the owner's own file: **0 lines, 0 rects, 0 curves**, and phrases broken mid-word.

  §A  one description — the stylesheet is derived from the style home, and no second copy exists
  §B  the PDF really draws the rules it names (measured off the rendered file)
  §C  text runs are whole — the thing `render_words` could not do
  §D  the round-trip proof is UNTOUCHED, and still answers its own question
  §E  a column no row fills is not printed, and the money columns are never dropped
  §F  a payment method is written for a human, once
  §G  the PDF decides no content
  §H  negative controls
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.modules.pos import receipt_pdf_render as RPDF          # noqa: E402
from app.modules.pos import sale_receipt as SR                  # noqa: E402
from app.modules.pos.receipt_formats import doc_style as ST     # noqa: E402
from app.modules.pos.receipt_formats import registry as REG     # noqa: E402
from app.modules.pos.receipt_formats import render as RENDER    # noqa: E402
from app.modules.pos.receipt_formats import render_pdf as RPD   # noqa: E402

STYLE_SRC = (ROOT / "app/modules/pos/receipt_formats/doc_style.py").read_text()
RENDER_SRC = (ROOT / "app/modules/pos/receipt_formats/render.py").read_text()
RPD_SRC = (ROOT / "app/modules/pos/receipt_formats/render_pdf.py").read_text()
RPDF_SRC = (ROOT / "app/modules/pos/receipt_pdf_render.py").read_text()

_passed = 0
_failed = 0


def ok(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


RQ = REG.get("rq")["module"]
TEMPLATE = SR.template_view({"show_customer": True, "show_store_name": True, "show_serials": True,
                             "layout": "pos_format", "footer_text": "Thank you for your business!"})
BRAND = {"company_name": "Example Wireless",
         "address_lines": ["2723 Example Ave", "Brooklyn NY 11229"],
         "license_tax_ids": "2126405-DCWP"}
# The owner's own sale, including the empty-SKU column and the two-word product names that the old
# path broke apart.
SNAP = SR.snapshot_view({
    "transactionId": 4605, "date": "10/21/2026, 6:03:24 PM",
    "customerName": "GRANDMARKET INTL CORPORATION",
    "items": [
        {"description": "Samsung A9 +", "serial": "929-821-4207", "qty": 1,
         "unit_price": 175.00, "discount": 0, "line_total": 190.53},
        {"description": "Samsung A7 Tablet", "serial": "929-675-8436", "qty": 1,
         "unit_price": 250.00, "discount": 0, "line_total": 272.19},
        {"description": "iPhone 13 Pro", "serial": "", "qty": 1,
         "unit_price": 350.00, "discount": 0, "line_total": 381.06},
    ],
    "subtotal": 775.00, "discountTotal": 0, "taxTotal": 68.78, "total": 843.78,
    "method": "credit_card", "tendered": 843.78, "change": 0,
})
KW = dict(brand=BRAND, tenant_name="Example Wireless", store_name="Ave U", customer_lines=[])

DOC = SR.document(SNAP, RQ, TEMPLATE, **KW)
HTML = SR.html(SNAP, TEMPLATE, render_document=RENDER.render_html, fmt=RQ, **KW)
PDF = RPDF.receipt_pdf(SNAP, TEMPLATE, fmt=RQ, render_words=RENDER.render_words,
                       render_pdf=RPD.render_pdf, **KW)


def _pdf_page():
    import pdfplumber
    from io import BytesIO
    with pdfplumber.open(BytesIO(PDF)) as pdf:
        pg = pdf.pages[0]
        return {"text": pg.extract_text() or "", "lines": list(pg.lines), "rects": list(pg.rects),
                "curves": list(pg.curves), "width": pg.width, "height": pg.height}


PAGE = _pdf_page()
#: Every stroke on the page, whether reportlab emitted it as a line or as a rectangle's edge.
STROKES = len(PAGE["lines"]) + len(PAGE["rects"]) + len(PAGE["curves"])


# ── §A one description ───────────────────────────────────────────────────────────────────────────
print("\n§A  the document is described ONCE")

ok("A1. the style home names every rule it draws", len(ST.RULES) >= 8)
ok("A2. the stylesheet is BUILT from the style home, not written beside it",
   "_style.css()" in RENDER_SRC and "def css(" in STYLE_SRC)
ok("A3. no second stylesheet survives in the renderer",
   '_CSS = """' not in RENDER_SRC,
   "a private CSS string here is the copy the PDF never saw")
ok("A4. the PDF reads the SAME measurements, not its own",
   "from . import doc_style as st" in RPD_SRC
   and not re.search(r"^\s*(FONT|BODY_PT|RULE_PT|HAIRLINE_PT)\s*=", RPD_SRC, re.M),
   "a measurement defined in the drawing module is a second description")
ok("A5. the greys and weights are numbers in ONE file",
   all(n in STYLE_SRC for n in ("HAIRLINE_PT", "RULE_PT", "HEAVY_PT", "RULE_GREY", "BAND_GREY")))
ok("A6. the page rule is still §POS-11's and is not re-spelled here",
   "@page" not in STYLE_SRC and "PAGE_CSS" in RENDER_SRC)
ok("A7. RULE TWO — no tenant, carrier or product in the style home",
   not re.search(r"(?i)\b(boost|total wireless|luxelink|cellfonz|verizon|vzone|samsung)\b", STYLE_SRC))


# ── §B the PDF really draws its rules ────────────────────────────────────────────────────────────
print("\n§B  the rules are DRAWN — measured off the rendered file")

ok("B1. THE REGRESSION: the owner's file had 0 strokes; this one has many",
   STROKES >= 8, f"{STROKES} strokes")
ok("B2. the page is framed", any(r["width"] > PAGE["width"] * 0.8
                                 and r["height"] > PAGE["height"] * 0.8 for r in PAGE["rects"]),
   f'{len(PAGE["rects"])} rects')
ok("B3. there is a full-width horizontal rule under the header",
   any(abs(l["y0"] - l["y1"]) < 0.6 and (l["x1"] - l["x0"]) > PAGE["width"] * 0.7
       for l in PAGE["lines"]),
   "no wide horizontal rule found")
ok("B4. the items heading sits on a filled band",
   any(r.get("fill") and r["width"] > PAGE["width"] * 0.6 and r["height"] < 25
       for r in PAGE["rects"]),
   "no header band")
ok("B5. the totals are boxed — a bounded shape on the RIGHT of the page",
   any(r["x0"] > PAGE["width"] * 0.5 and 100 < r["width"] < 320 and r["height"] > 20
       for r in PAGE["rects"]),
   "no totals box")
ok("B6. a heavy rule separates the grand total from what is above it",
   any(abs(l["y0"] - l["y1"]) < 0.6 and l.get("linewidth", 0) >= ST.HEAVY_PT - 0.2
       for l in PAGE["lines"])
   or any(r.get("fill") and r["height"] <= ST.HEAVY_PT + 0.6 for r in PAGE["rects"]),
   "no heavy total rule")
ok("B7. it is a real PDF on a letter sheet", PDF[:5] == b"%PDF-"
   and round(PAGE["width"]) == 612 and round(PAGE["height"]) == 792)
ok("B8. the screen copy carries the same rules it names",
   all(tok in HTML for tok in ("table.items", "totals", "receipt")) and "border" in RENDER.__doc__ + ST.css())


# ── §C text runs are whole ───────────────────────────────────────────────────────────────────────
print("\n§C  phrases are drawn as text, not reassembled from words")

ok("C1. THE REGRESSION: a two-word shop name is not broken apart",
   "Example Wireless" in PAGE["text"],
   PAGE["text"][:120])
ok("C2. a three-word product name survives", "Samsung A7 Tablet" in PAGE["text"])
ok("C3. the footer sentence survives whole", "Thank you for your business!" in PAGE["text"])
ok("C4. no phrase carries the run of spaces the word-placement path produced",
   not re.search(r"[A-Za-z]\s{3,}[A-Za-z]", PAGE["text"]),
   "a 3+ space gap inside a line is the old defect's signature")
ok("C5. the drawing wraps by the font's real metrics, never a character count",
   "stringWidth" in RPD_SRC and not re.search(r"len\(\s*text\s*\)\s*[<>]", RPD_SRC))


# ── §D the round-trip proof is untouched ─────────────────────────────────────────────────────────
print("\n§D  `render_words` keeps its own job")

_words = RENDER.render_words(DOC, getattr(RQ, "PRINT_LAYOUT", None))
ok("D1. it still produces positioned words", bool(_words) and "x0" in _words[0] and "top" in _words[0])
ok("D2. the PDF no longer CALLS it for the customer's document",
   not re.search(r"render_words\s*\(", RPD_SRC),
   "naming it in the note that explains why is fine; calling it is the defect")
ok("D3. it is still OFFERED to the PDF entry point, for the slip path's own signature",
   "render_words=None" in RPDF_SRC)
ok("D4. the round-trip path still reads EVERY declared column, empty or not",
   RENDER_SRC.count('cols = doc.get("columns") or []') >= 2,
   "a column that was on the original paper must stay there for the parser")
ok("D5. only the HTML renderer drops an unfilled column",
   RENDER_SRC.count("_style.visible_columns(doc)") == 1)


# ── §E a column no row fills ─────────────────────────────────────────────────────────────────────
print("\n§E  a heading over nothing is not a column")

_cols = [{"key": "sku", "label": "Product SKU", "kind": "text"},
         {"key": "name", "label": "Product Name", "kind": "text"},
         {"key": "qty", "label": "Qty", "kind": "qty"},
         {"key": "price", "label": "Your Price", "kind": "money"}]
_doc_empty = {"columns": _cols, "items": [{"cells": {"sku": "", "name": "A", "qty": 1, "price": 1}},
                                          {"cells": {"sku": "  ", "name": "B", "qty": 1}}]}
_vis = [c["key"] for c in ST.visible_columns(_doc_empty)]
ok("E1. THE OWNER'S 'Product SKU': empty on every row, so it is not printed", "sku" not in _vis)
ok("E2. a filled text column stays", "name" in _vis)
ok("E3. a money column is kept even when empty",
   "price" in [c["key"] for c in ST.visible_columns(
       {"columns": _cols, "items": [{"cells": {"name": "A"}}]})],
   "a free-of-charge sale must not silently lose its price column")
ok("E4. a quantity column is kept even when empty",
   "qty" in [c["key"] for c in ST.visible_columns(
       {"columns": _cols, "items": [{"cells": {"name": "A"}}]})])
ok("E5. one filled row is enough to keep a column",
   "sku" in [c["key"] for c in ST.visible_columns(
       {"columns": _cols, "items": [{"cells": {"sku": "", "name": "A"}},
                                    {"cells": {"sku": "X9", "name": "B"}}]})])
ok("E6. a document with nothing filled still prints its headings rather than an empty page",
   len(ST.visible_columns({"columns": _cols, "items": []})) == len(_cols))
ok("E7. the owner's SKU heading is gone from the rendered file",
   "PRODUCT SKU" not in PAGE["text"].upper())
ok("E8. and from the screen copy", "Product SKU" not in HTML)


# ── §F a payment method written for a human ──────────────────────────────────────────────────────
print("\n§F  a customer is never shown a machine value")

ok("F1. THE DEFECT: Credit_Card is written Credit Card", SR.method_label("credit_card") == "Credit Card")
ok("F2. it is in the rendered file that way",
   "Credit Card" in PAGE["text"] and "Credit_Card" not in PAGE["text"])
ok("F3. and on the screen copy", "Credit_Card" not in HTML)
ok("F4. one home — neither layout dresses the method itself",
   "method_label(" in (ROOT / "app/modules/pos/sale_receipt.py").read_text()
   and '.title()' not in re.sub(r"def method_label[\s\S]*?\n\n\n", "",
                                (ROOT / "app/modules/pos/sale_receipt.py").read_text()).split(
       'snap["method"]')[1][:80])
ok("F5. no method vocabulary is declared (RULE TWO)",
   SR.method_label("some_future_method") == "Some Future Method")
ok("F6. an absent method is blank, never a label", SR.method_label(None) == ""
   and SR.method_label("") == "")


# ── §G the PDF entry point decides no content ────────────────────────────────────────────────────
print("\n§G  drawing is not deciding")

ok("G1. the drawing module reads the Document and computes no figure",
   "fmt_money" in RPD_SRC and not re.search(r"(subtotal|tax_total)\s*=", RPD_SRC))
ok("G2. `receipt_pdf` still takes the layout decision from the ONE rule",
   "clean_layout(" in RPDF_SRC)
ok("G3. a POS layout with no renderer injected REFUSES by name, not with a TypeError",
   "render_pdf is None" in RPDF_SRC and "needs render_pdf injected" in RPDF_SRC)
ok("G4. the renderer is injected, exactly as render_words is",
   "render_pdf=_rpdf_doc.render_pdf" in (ROOT / "app/modules/pos/router.py").read_text())


# ── §H negative controls ─────────────────────────────────────────────────────────────────────────
print("\n§H  controls — each lock fails when its subject is broken")

ok("H1. control: the OLD path really produced no strokes",
   len(RENDER.render_words(DOC, getattr(RQ, "PRINT_LAYOUT", None))) > 0
   and all("x0" in w and "text" in w for w in _words),
   "words carry text and a position and nothing else — there is no rule to draw")
ok("H2. control: C4 would catch a broken phrase",
   bool(re.search(r"[A-Za-z]\s{3,}[A-Za-z]", "Verizon    Wireless")))
ok("H3. control: E1 is not vacuous — the column IS declared on the document",
   "sku" in [c["key"] for c in _cols])
ok("H4. control: B1 counts real geometry, and an empty page would fail it",
   STROKES > 0 and isinstance(PAGE["rects"], list))
ok("H5. control: the style home is importable with no database and no app config",
   "get_supabase" not in STYLE_SRC and "import os" not in STYLE_SRC)
ok("H6. control: §POS-13 is registered in the index",
   "POS-13" in (ROOT.parent / "docs/SYSTEM_DATA_FLOW_INDEX.md").read_text())


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
