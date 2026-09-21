"""THE LOCK — SALES REBUILT FROM THE LANDED REPORTS keep ONE document shape, ONE importer, ONE renderer,
and the receipt format follows the tenant's DECLARED POS (owner 2026-09-21; index §30.14; CLAUDE.md
2026-09-20 "A fix is a DESIGN fix … lock it so it cannot un-wire"). stdlib only — runs in CI beside the
report-kind / mapping-key / report-links / landing-identity / tender-vocab locks (carrier-vocab-guard.yml).
    python3 harness_pos_sales_from_reports_lock.py        (from backend/)

  (a) ONE DOCUMENT SHAPE — `new_document` is defined once (receipt_formats/base.py); no module under
      backend/app/modules builds a receipt document dict literal of its own (pos_source / columns / totals).
  (b) ONE IMPORTER — receipt_imports rows (and receipt-import sales) are inserted only in pos/receipt_import.py;
      the rebuild goes through `upsert_structured` → `import_structured`.
  (c) ONE RENDERER — render_html / render_words / render_text are defined once (receipt_formats/render.py);
      the print endpoint and the round-trip proof both call it.
  (d) THE FORMAT IS THE DECLARED POS's — pos/sales_from_reports.py resolves it through
      report_kinds.tenant_declaration + receipt_formats.registry.get and never names a POS key, a tender
      class or a card brand (the tender vocabulary is closing.router.TENDER_VOCAB; the axis is injected).
  (e) THE TWO UPLOADS SAY WHERE THEY SHOW — landing_identity.CONSUMERS names the POS screen on raw_sales,
      raw_sales_invoice and raw_sales_invoice_tender, and ScreenLink SCREENS carries `pos_receipts`.
  (f) RULE TWO — the module is held by the carrier-vocab guard's POS_BACKEND_LOGIC list.
  (g) registration (index §30.14 + §16 / §17 / §18, the design doc) and CI wiring.
  Negative controls prove (a)–(d) go red.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.modules.pos.receipt_formats import registry as REG          # noqa: E402  (stdlib-only package)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BE = os.path.join(ROOT, "backend", "app", "modules")
_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}" + (f"  — {str(extra)[:400]}" if extra != "" else ""))


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def scan_files(root, exts=(".py",)):
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            if fn.endswith(exts):
                p = os.path.join(dp, fn)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = io.open(p, encoding="utf-8", errors="replace").read()
    return out


def lock_violations(files, sources=None):
    """files = {relative path under backend/app/modules: text}. Returns [(path, rule, detail)]."""
    sources = REG.sources() if sources is None else sources
    v = []
    for rel, body in files.items():
        if re.search(r"^def new_document\(", body, re.M) and rel != "pos/receipt_formats/base.py":
            v.append((rel, "second_document_shape", "defines new_document outside receipt_formats/base.py"))
        if rel != "pos/receipt_formats/base.py" and re.search(r"\{\s*[\"']pos_source[\"']\s*:[^}]*[\"']columns[\"']\s*:[^}]*[\"']totals[\"']\s*:", body, re.S):
            v.append((rel, "second_document_shape", "a receipt document dict literal outside base.new_document"))
        if rel != "pos/receipt_import.py" and re.search(r"table\(\s*[\"']receipt_imports[\"']\s*\)\s*\.insert\(", body):
            v.append((rel, "second_importer", "inserts into receipt_imports outside receipt_import.py"))
        if rel != "pos/receipt_import.py" and re.search(r"table\(\s*[\"']sales[\"']\s*\)\s*\.insert\([^)]*receipt_import", body, re.S):
            v.append((rel, "second_importer", "inserts a receipt_import sale outside receipt_import.py"))
        for fn in ("render_html", "render_words", "render_text"):
            if re.search(rf"^def {fn}\(", body, re.M) and rel != "pos/receipt_formats/render.py":
                v.append((rel, "second_renderer", f"defines {fn} outside receipt_formats/render.py"))
        if rel == "pos/sales_from_reports.py":
            for s in sources:
                if re.search(rf"[\"']{re.escape(s)}[\"']", body):
                    v.append((rel, "literal_pos", f"names the POS key '{s}' instead of the declaration"))
            if "tenant_declaration(" not in body or ".get(" not in body:
                v.append((rel, "no_declaration", "does not resolve the format through tenant_declaration + registry.get"))
            if re.search(r"[\"'](cash|credit|debit|visa|mastercard|amex|discover)[\"']", body, re.I):
                v.append((rel, "tender_list", "spells a tender class / brand — the vocabulary is closing.router.TENDER_VOCAB"))
    return v


def main():
    real = scan_files(BE)
    viol = lock_violations(real)
    sfr = real.get("pos/sales_from_reports.py", "")
    ri = real.get("pos/receipt_import.py", "")
    pr = real.get("pos/router.py", "")
    check("(a) ONE document shape: new_document defined once; no receipt document literal elsewhere", not [x for x in viol if x[1] == "second_document_shape"], viol)
    check("(b) ONE importer: receipt_imports inserted only in receipt_import.py; the rebuild calls upsert_structured, which calls import_structured",
          not [x for x in viol if x[1] == "second_importer"] and "upsert_structured(" in sfr and "def upsert_structured(" in ri
          and "import_structured(" in ri[ri.index("def upsert_structured("):])
    check("(c) ONE renderer: render_html / render_words / render_text defined once; the print endpoint renders through it",
          not [x for x in viol if x[1] == "second_renderer"] and "_rrender.render_html(" in pr)
    check("(d) the format is the declared POS's: tenant_declaration + registry.get; no literal POS key, tender class or card brand in the consumer",
          not [x for x in viol if x[1] in ("literal_pos", "no_declaration", "tender_list")] and sfr, viol)
    li = real.get("commcalc/landing_identity.py", "")
    ok_e = True
    for t in ("raw_sales", "raw_sales_invoice", "raw_sales_invoice_tender"):
        m = re.search(rf'^\s*"{t}":\s*\[(.*?)^\s*\],', li, re.M | re.S)
        ok_e = ok_e and bool(m) and '"screen": "pos_receipts"' in m.group(1)
    sl = read("frontend/src/components/ScreenLink.tsx")
    check("(e) CONSUMERS names pos_receipts on raw_sales / raw_sales_invoice / raw_sales_invoice_tender and ScreenLink SCREENS carries it",
          ok_e and "'pos_receipts'" in sl and re.search(r"^\s*pos_receipts:\s*\{\s*href:\s*'/pos/receipts'", sl, re.M))
    vg = read("backend/harness_carrier_vocab_guard.py")
    check("(f) RULE TWO: pos/sales_from_reports.py is in the carrier-vocab guard's POS_BACKEND_LOGIC",
          (lambda m: bool(m) and '"pos/sales_from_reports.py"' in m.group(1))(re.search(r"^POS_BACKEND_LOGIC\s*=\s*\[(.*?)\]", vg, re.M | re.S)))
    idx = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
    dd = read("docs/ONBOARDING_FLOW_DESIGN.md")
    yml = read(".github/workflows/carrier-vocab-guard.yml")
    check("(g) registered: index §30.14 + rows naming the module / endpoints / consumer; the design doc; CI runs this lock and watches the module",
          "### 30.14" in idx and "sales_from_reports" in idx and "/pos/sales-from-reports" in idx and "pos_receipts" in idx and "sales_from_reports" in dd
          and "harness_pos_sales_from_reports_lock.py" in yml and "backend/app/modules/pos/sales_from_reports.py" in yml)

    # negative controls
    ctl = dict(real)
    ctl["pos/second_builder.py"] = 'def build():\n    return {"pos_source": "x", "columns": [], "totals": [], "items": []}\n'
    check("N1 a second receipt-document builder → RED", any(x[1] == "second_document_shape" for x in lock_violations(ctl)))
    ctl = dict(real)
    ctl["pos/other_import.py"] = 'def f(client):\n    client.schema("pos").table("receipt_imports").insert({}).execute()\n'
    check("N2 a second importer inserting into receipt_imports → RED", any(x[1] == "second_importer" for x in lock_violations(ctl)))
    ctl = dict(real)
    ctl["pos/render2.py"] = "def render_html(doc):\n    return ''\n"
    check("N3 a second renderer → RED", any(x[1] == "second_renderer" for x in lock_violations(ctl)))
    ctl = dict(real)
    ctl["pos/sales_from_reports.py"] = sfr.replace("_reg.get(key)", f'_reg.get("{REG.sources()[0]}")')
    check("N4 the consumer resolving the format from a literal POS key → RED", any(x[1] == "literal_pos" for x in lock_violations(ctl)))
    ctl = dict(real)
    ctl["pos/sales_from_reports.py"] = sfr.replace("tenant_declaration(", "declaration_of(")
    check("N5 the consumer not reading the declaration → RED", any(x[1] == "no_declaration" for x in lock_violations(ctl)))
    ctl = dict(real)
    ctl["pos/sales_from_reports.py"] = sfr + '\nPAY = ["cash", "credit"]\n'
    check("N6 a tender list in the consumer → RED", any(x[1] == "tender_list" for x in lock_violations(ctl)))
    check("N7 the real tree is clean", viol == [], viol)

    print(f"\n{_pass} passed, {_fail} failed")
    if _fail:
        print("RED — a second document shape / importer / renderer, or a consumer that names the POS instead of reading the declaration. "
              "Dereference base.new_document, receipt_import.upsert_structured, receipt_formats.render and report_kinds.tenant_declaration.")
        sys.exit(1)
    print("OK — one document shape, one importer, one renderer; the receipt format follows the declared POS.")


if __name__ == "__main__":
    main()
