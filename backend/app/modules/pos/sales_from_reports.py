"""SALES FROM THE LANDED REPORTS — the two sales exports a POS gives (one row per INVOICE with the
tender columns; one row per invoice LINE) combined, per invoice, into ONE structured receipt Document,
imported as a POS sale through the one importer, and reprinted through the registered format of the
tenant's declared POS.

OWNER (2026-09-21, verbatim): *"upload the attached sales data to the pos, 2 different excel reports need
to be combined into one, an upload mechanism to be created for the vzone tenant to upload these 2 reports
from time to time and combine them into usable sales data and then print out in the same exact format of
the receipt uploaded, the data can be combined using the invoice as the common link between the 2"*.

WHAT IS REUSED (the duplicate check, index §30.14):
  · the upload mechanism IS the two report-kind cards / Upload-page tiles the registry already routes —
    'Sales report with IMEI and phone number' (kind `sales` → commcalc.raw_sales, the LINES) and 'Sales
    by invoice with tender types' (kind `sales_by_invoice` → raw_sales_invoice + raw_sales_invoice_tender,
    the HEADER and the TENDER SPLIT). No third upload path.
  · the join key is the invoice number — `raw_sales_invoice.trans_id` ↔ `raw_sales.trans_id`, the same
    pairing report_links makes (§30.11); nothing else pairs them.
  · the document shape is `receipt_formats.base.new_document` — the ONE shape a scanned receipt is parsed
    into; the columns / totals / title / date spelling / financed rule / print geometry come from the
    format REGISTERED for the tenant's declared POS (`registry.get(<pos_key>)`, the key read through
    `report_kinds.tenant_declaration` — never a literal).
  · the tender classes come from the landed tender rows (classed at intake step 2.5b through
    closing.router.TENDER_VOCAB); which classes are CUSTOMER payments is `is_customer_payment` (a class with a
    place on the closing axis) — injected, never a list here. A vendor rebate / coupon tender is NOT a
    payment line on a receipt; it explains why the lines add up to more than the customer paid.
  · the import is `receipt_import.upsert_structured` — `import_structured` on the first run, a REPLACE
    of that invoice's sale on every later run (keyed org × POS × invoice #, provenance 'reports'): never
    a duplicate. The customer is `_match_or_create_customer` (phone, else the full name).
  · the reprint is `receipt_formats.render` (the same renderer a scanned receipt reprints with).

THE PURE CORE (`build_documents`) takes plain rows and injected callables and returns, per invoice, the
document AND a report in words (lines found / not found; Σ line totals vs the invoice's subtotal and net
sales; Σ customer tenders vs the invoice total; the tax residual vs the landed tax column; the financed
total). Deterministic: the same rows give the same document; items are ordered by (SKU, tracking #) —
the landed rows carry no file order.

RULE TWO: no carrier, POS vendor, tenant, product or card-brand name here. stdlib only in the core.
"""
from __future__ import annotations

import datetime as _dt
import re

from app.modules.pos.receipt_formats import base as _base

PROVENANCE_KIND = "reports"


def _s(v):
    return "" if v is None else str(v).strip()


def _f(v):
    if v is None or _s(v) == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return _base.money(v) or 0.0


def money(x):
    return round(float(x or 0.0), 2)


def _qty(v):
    n = _f(v)
    return int(n) if float(n).is_integer() else n


def _qty_text(v):
    n = _qty(v)
    return str(n)


def _sort_key(line):
    """Deterministic item order — the landed rows carry no file order: SKU, tracking #, name, total, then
    the row id so two identical lines (one per device) always come out in the same order."""
    return (_s(line.get("sku")).lower(), _s(line.get("serial_1")), _s(line.get("product_desc")).lower(),
            money(_f(line.get("ext_price"))), _s(line.get("id")))


def _is_financed(line, rule):
    """The format's FINANCED_ITEMS rule over a landed line: the description carries every match word
    and (when the rule says so) the quantity is negative. No rule → no line is financed."""
    if not rule:
        return False
    desc = _s(line.get("product_desc")).lower()
    if not all(m.lower() in desc for m in (rule.get("match") or [])):
        return False
    if rule.get("qty_negative") and not (_f(line.get("quantity")) < 0):
        return False
    return True


def _words_diff(a, b, what_a, what_b):
    d = money(a - b)
    if abs(d) < 0.005:
        return f"{what_a} equal {what_b} to the cent ({a:,.2f})"
    return f"{what_a} are {a:,.2f} and {what_b} {b:,.2f} — a difference of {d:,.2f}"


def build_document(inv, tenders, lines, fmt, *, store_header, customer_lines, footer_text, pays,
                   built_at=None, built_by=None, sources=None):
    """ONE invoice → (document, report). `fmt` = the registered format module (COLUMNS, TOTALS, TITLE,
    DATE_FORMAT, FINANCED_ITEMS, CONTRACT_SECTION, POS_SOURCE, LABEL). Injected:
      store_header(store_string) → {"lines": [...], "phone": str|None, "store_code": str|None, "how": str|None}
      customer_lines(name) → [address lines] (what the POS customer record knows; [] = name only)
      footer_text → the org's receipt-template footer (config) or None
      pays(tender_class) → is it the CUSTOMER's payment (closing.router.is_customer_payment — the one answer the
        intake's 2.5b tie and Cash Collected give too)
    `sources` names the landings the rows came from (table / kind), for the provenance stamp."""
    cols = [{"key": c["key"], "label": c["label"], "kind": c["kind"],
             "align": "right" if c["kind"] in (_base.KIND_MONEY, _base.KIND_TOTAL, _base.KIND_QTY) else "left"}
            for c in fmt.COLUMNS]
    doc = _base.new_document(fmt.POS_SOURCE, fmt.LABEL)
    doc["title"] = getattr(fmt, "TITLE", None) or "Sale"
    inv_no = _s(inv.get("trans_id"))
    date_iso = _s(inv.get("trans_date"))[:10]
    date_fmt = getattr(fmt, "DATE_FORMAT", "%d-%b-%Y")
    store_str = _s(inv.get("store"))
    sh = store_header(store_str) or {}
    salesperson = _s(inv.get("salesperson"))
    tendered_by = _s(inv.get("tendered_by")) or salesperson

    doc["meta"].append({"key": "invoice_no", "label": "Invoice", "value": inv_no, "editable": False})
    if date_iso:
        doc["meta"].append({"key": "sale_date", "label": "Tendered On", "value": _base.format_date(date_iso, date_fmt), "editable": True})
    if salesperson:
        doc["meta"].append({"key": "salesperson", "label": "Sales Person", "value": salesperson, "editable": True})
    if tendered_by:
        doc["meta"].append({"key": "tendered_by", "label": "Tendered By", "value": tendered_by, "editable": False})
    if store_str:
        doc["meta"].append({"key": "tendered_at", "label": "Tendered At", "value": store_str, "editable": False})
    if date_iso:
        iso = _base.parse_iso_date(date_iso)
        if iso:
            doc["meta"].append({"key": "sale_date_iso", "label": "Sale Date (ISO)", "value": iso, "editable": False})

    store_lines = [ln for ln in (sh.get("lines") or []) if _s(ln)]
    if not store_lines and store_str:
        store_lines = [store_str]
    doc["store"]["lines"] = store_lines
    phone = _s(sh.get("phone")) or None
    if phone and not any(phone in ln for ln in store_lines):
        doc["store"]["lines"].append(phone)
    for ln in doc["store"]["lines"]:
        m = re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", ln)
        if m:
            doc["store"]["phone"] = m.group(0)
            break

    cust = _s(inv.get("customer"))
    bt = ([cust] if cust else []) + [ln for ln in (customer_lines(cust) or []) if _s(ln)]
    doc["bill_to"]["lines"] = bt
    if bt:
        doc["bill_to"]["name"] = bt[0]

    doc["columns"] = cols
    fin_rule = getattr(fmt, "FINANCED_ITEMS", None)
    ordered = sorted(lines or [], key=_sort_key)
    sum_lines = 0.0
    financed = 0.0
    refund_lines = 0
    for ln in ordered:
        qty = _f(ln.get("quantity"))
        total = money(_f(ln.get("ext_price")))
        unit = money(total / qty) if qty not in (0, 0.0) else total
        sum_lines += total
        if _is_financed(ln, fin_rule):
            financed += -total
        if _s(ln.get("voided")).lower() in ("yes", "y", "true", "1"):
            refund_lines += 1
        cells = {"sku": _s(ln.get("sku")), "name": _s(ln.get("product_desc")), "tracking": _s(ln.get("serial_1")),
                 "qty": _qty_text(qty if qty else 1), "price": _base.fmt_money(unit), "total": _base.fmt_money(total)}
        doc["items"].append(_base.item({c["key"]: cells.get(c["key"], "") for c in cols}, cols))
    sum_lines = money(sum_lines)
    financed = money(financed)

    subtotal = money(_f(inv.get("subtotal")))
    inv_total = money(_f(inv.get("invoice_total")))
    net_sales = money(_f(inv.get("net_sales")))
    extra = money(_f(inv.get("extra_charges")) + _f(inv.get("donations")))
    tax_residual = money(inv_total - subtotal - extra)
    tax_landed = money(_f(inv.get("tax")))
    spec = {t["key"]: t for t in fmt.TOTALS}
    values = {"subtotal": subtotal, "sales_tax": tax_residual, "financed": financed if financed else None,
              "total": inv_total, "change": 0.0}
    for t in fmt.TOTALS:
        v = values.get(t["key"])
        if v is None:
            continue
        doc["totals"].append({"key": t["key"], "label": t["label"], "amount": v, "editable": bool(t.get("editable"))})

    pay_sum = 0.0
    other = {}
    for t in sorted(tenders or [], key=lambda r: (_s(r.get("tender_label")).lower(), money(_f(r.get("amount"))), _s(r.get("id")))):
        if _s(t.get("role")) not in ("", "tender"):
            continue
        amt = money(_f(t.get("amount")))
        if abs(amt) < 0.005:
            continue
        cls = _s(t.get("tender_class")).lower()
        if pays(cls):
            doc["payments"].append({"label": _s(t.get("tender_label")), "amount": amt})
            pay_sum += amt
        else:
            other[_s(t.get("tender_label"))] = money(other.get(_s(t.get("tender_label")), 0.0) + amt)
    pay_sum = money(pay_sum)
    other_sum = money(sum(other.values()))

    sec = getattr(fmt, "CONTRACT_SECTION", None)
    if sec:
        pairs = sorted({(_s(ln.get("serial_1")), _s(ln.get("contract_no"))) for ln in ordered if _s(ln.get("contract_no"))})
        if pairs:
            doc["sections"].append({"title": sec["title"], "kind": "table",
                                    "columns": [{"key": c["key"], "label": c["label"]} for c in sec["columns"]],
                                    "rows": [[p[0], p[1]] for p in pairs]})
    comments = _s(inv.get("comments"))
    doc["comments"] = comments or None
    doc["footer_text"] = footer_text or None
    doc["derived"] = _base.compute_derived(doc)

    # ── the report, in words ──
    words = []
    if not ordered:
        words.append(f"invoice {inv_no}: NO lines found in the line-level sales export for this invoice — the receipt has the header and totals only")
    else:
        words.append(f"invoice {inv_no}: {len(ordered)} line(s) found" + (f", {refund_lines} flagged as a refund / offset" if refund_lines else ""))
    d_sub = money(sum_lines - subtotal)
    if ordered:
        if abs(d_sub) < 0.005:
            words.append(f"the lines add up to the invoice subtotal to the cent ({sum_lines:,.2f})")
        elif abs(money(d_sub - other_sum)) < 0.005 and other:
            words.append(f"the lines add up to {sum_lines:,.2f}; the invoice subtotal is {subtotal:,.2f}; the difference {d_sub:,.2f} equals "
                         + " + ".join(f"{k} {v:,.2f}" for k, v in other.items())
                         + " — the tender(s) that are not customer payments (the lines the vendor paid; the register prints them at $0.00)")
        else:
            words.append(f"the lines add up to {sum_lines:,.2f}; the invoice subtotal is {subtotal:,.2f} — a difference of {d_sub:,.2f} not explained by the non-customer tenders"
                         + (f" ({other_sum:,.2f})" if other else ""))
        words.append(_words_diff(sum_lines, net_sales, "Σ line totals", "the invoice's net sales"))
    if doc["payments"] or inv_total:
        words.append(_words_diff(pay_sum, inv_total, "the customer tenders", "the invoice total"))
    if abs(money(tax_residual - tax_landed)) >= 0.005:
        words.append(f"sales tax printed as the invoice's residual (total − subtotal − extra charges − donations = {tax_residual:,.2f}); "
                     f"the landed tax column says {tax_landed:,.2f}")
    if financed:
        words.append(f"financed: {financed:,.2f} from {sum(1 for ln in ordered if _is_financed(ln, fin_rule))} financed line(s)")
    if not sh.get("store_code"):
        words.append(f"store '{store_str}' is not resolved to one of the company's stores — the receipt shows the store string; no address or phone")
    if cust and not (customer_lines(cust) or []):
        words.append("bill-to shows the customer name only — the reports carry no address")

    report = {"invoice_no": inv_no, "trans_date": date_iso, "store": store_str, "store_code": sh.get("store_code"),
              "salesperson": salesperson, "customer": cust,
              "lines_found": len(ordered), "refund_lines": refund_lines, "sum_lines": sum_lines, "subtotal": subtotal,
              "net_sales": net_sales, "invoice_total": inv_total, "customer_tenders": pay_sum, "other_tenders": other,
              "tax_residual": tax_residual, "tax_landed": tax_landed, "financed": financed,
              "lines_tie": abs(d_sub) < 0.005 or (bool(other) and abs(money(d_sub - other_sum)) < 0.005),
              "tenders_tie": abs(money(pay_sum - inv_total)) < 0.005, "words": words}
    doc[_base.PROVENANCE_KEY] = {
        "kind": PROVENANCE_KIND, "built_at": built_at or _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "built_by": built_by,
        "invoice": {**(sources or {}).get("invoice", {}), "row_id": inv.get("id"), "trans_id": inv_no, "trans_date": date_iso, "store": store_str},
        "tenders": {**(sources or {}).get("tenders", {}), "rows": len(tenders or []), "row_ids": sorted(_s(t.get("id")) for t in (tenders or []) if t.get("id"))},
        "lines": {**(sources or {}).get("lines", {}), "rows": len(ordered), "row_ids": [ln.get("id") for ln in ordered if ln.get("id")]},
        "report": report,
    }
    return doc, report


def build_documents(invoices, tenders, lines, fmt, **ctx):
    """Every invoice of the slice → [(document, report)], plus the coverage the two landings give each
    other: invoices with lines / without, line invoices with no header. Keyed on the invoice number."""
    by_t = {}
    for t in tenders or []:
        by_t.setdefault(_s(t.get("trans_id")), []).append(t)
    by_l = {}
    for ln in lines or []:
        by_l.setdefault(_s(ln.get("trans_id")), []).append(ln)
    out, seen = [], set()
    for inv in sorted(invoices or [], key=lambda r: (_s(r.get("trans_date")), _s(r.get("trans_id")))):
        k = _s(inv.get("trans_id"))
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(build_document(inv, by_t.get(k, []), by_l.get(k, []), fmt, **ctx))
    orphan_lines = sorted(k for k in by_l if k and k not in seen)
    return {"documents": out, "invoices": len(seen), "with_lines": sum(1 for d, r in out if r["lines_found"]),
            "without_lines": sum(1 for d, r in out if not r["lines_found"]), "line_invoices_without_header": orphan_lines,
            "lines_tie": sum(1 for d, r in out if r["lines_tie"] and r["lines_found"]),
            "tenders_tie": sum(1 for d, r in out if r["tenders_tie"])}


def summary_words(res):
    n = res["invoices"]
    w = [f"{n:,} invoice(s) in the slice: {res['with_lines']:,} with lines, {res['without_lines']:,} without"]
    if res["line_invoices_without_header"]:
        w.append(f"{len(res['line_invoices_without_header']):,} invoice number(s) have lines but no invoice header — not rebuilt (the header report is missing them)")
    if res["with_lines"]:
        w.append(f"the lines tie to the invoice subtotal (or the difference is the vendor-paid tenders) on {res['lines_tie']:,} of {res['with_lines']:,}")
    if n:
        w.append(f"the customer tenders equal the invoice total on {res['tenders_tie']:,} of {n:,}")
    return w


# ── The consumer (I/O): the landed slices → documents → the ONE importer ─────────────────────────────
def resolve_format(client, org_id):
    """The format registered for the tenant's DECLARED POS — read through report_kinds.tenant_declaration
    (the one reader of the declaration) and receipt_formats.registry.get (the one lookup). Answers
    {"ok", "pos", "format", "reason"}; never a fallback to another POS's format."""
    from app.modules.commcalc import report_kinds as _rk
    from app.modules.pos.receipt_formats import registry as _reg
    decl = _rk.tenant_declaration(client, org_id)
    pos = list(decl.get("pos") or [])
    if not pos:
        return {"ok": False, "pos": None, "format": None,
                "reason": "no POS declared for this company — set the POS in Stage 1 (or the pos_system term under Admin → Labels); "
                          "the receipt format follows the declared POS"}
    for key in pos:
        f = _reg.get(key)
        if f:
            return {"ok": True, "pos": key, "format": f, "reason": None}
    return {"ok": False, "pos": pos[0], "format": None,
            "reason": f"no receipt format is registered for the declared POS '{pos[0]}' yet — the platform can rebuild the sale but "
                      f"cannot print it in that POS's layout until a format is added to receipt_formats/registry (a house change)"}


def _store_header_fn(client, org_id):
    """store string → the receipt's store block from the store master: the POS's own store name (the
    string), the roster's address and phone for the resolved store code (§13a chain)."""
    from app.modules.commcalc import router as _cr
    resolve = _cr._intake_store_resolver(client, org_id)
    roster = {}
    try:
        for s in (client.schema("storeops").table("stores").select("store_code,address,phone").eq("org_id", org_id).execute().data) or []:
            roster[_s(s.get("store_code")).upper()] = s
    except Exception:
        roster = {}

    def fn(store_str):
        code, how = resolve(store_str)
        row = roster.get(_s(code).upper()) if code else None
        lines = [store_str] if _s(store_str) else []
        if row and _s(row.get("address")):
            lines.append(_s(row.get("address")))
        return {"lines": lines, "phone": _s(row.get("phone")) or None if row else None, "store_code": code, "how": how}
    return fn


def _customer_lines_fn(client, org_id):
    """customer name → the address lines the POS customer record knows (matched the way the importer
    matches: the full name); [] when unknown. Read once per distinct name."""
    from app.modules.pos import receipt_import as _ri
    cache = {}

    def fn(name):
        n = _s(name)
        if not n:
            return []
        if n in cache:
            return cache[n]
        row = _ri.find_customer(client, org_id, {"customer_name": n})
        lines = []
        if row:
            a1 = " ".join(x for x in (_s(row.get("address_1")), _s(row.get("address_2"))) if x)
            city = " ".join(x for x in (_s(row.get("city")), _s(row.get("state")), _s(row.get("zip"))) if x)
            lines = [x for x in (a1, city) if x]
        cache[n] = lines
        return lines
    return fn


def _footer_text(client, org_id):
    """The org's default receipt template footer (pos.receipt_templates, mig 725) — config, never a
    literal; None when the org has no template row."""
    try:
        rows = (client.schema("pos").table("receipt_templates").select("footer_text,is_default")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    for r in rows:
        if r.get("is_default") and _s(r.get("footer_text")):
            return _s(r.get("footer_text"))
    return None


def rebuild(client, org_id, lo, hi, stores=None, who=None, dry_run=False):
    """Rebuild every invoice of (org × trans_date lo..hi [× stores]) from the two landings and import each
    through receipt_import.upsert_structured under the declared POS. Returns the summary (counts, ties in
    words, the per-invoice reports capped) — never raises for one invoice: a failure is listed."""
    from app.modules.commcalc import router as _cr
    from app.modules.commcalc import column_mapping as _cm
    from app.modules.commcalc import onboarding_intake as _oi
    from app.modules.closing import router as _closing
    from app.modules.pos import receipt_import as _ri

    fmt = resolve_format(client, org_id)
    if not fmt["ok"]:
        return {"ok": False, "ran": False, "reason": fmt["reason"], "pos": fmt["pos"], "invoices": 0, "created": 0, "replaced": 0}
    inv_kind = _oi.REPORT_KEY_BY_KIND["invoice"]
    line_kind = _oi.REPORT_KEY_BY_KIND["sales"]
    inv_table = _cm.TABLE_MAP[inv_kind]
    tender_table = _cm.CHILD_TABLE_MAP[inv_kind]
    line_table = _cm.TABLE_MAP[line_kind]
    invoices = _cr._intake_reread_invoice(client, org_id, stores, lo, hi, kind=inv_kind)
    tenders = _cr._intake_reread_invoice_tenders(client, org_id, stores, lo, hi, kind=inv_kind)
    lines = _cr._intake_reread_sales(client, org_id, stores, lo, hi, table=line_table, kind=line_kind)
    now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    res = build_documents(
        invoices, tenders, lines, fmt["format"]["module"],
        store_header=_store_header_fn(client, org_id), customer_lines=_customer_lines_fn(client, org_id),
        footer_text=_footer_text(client, org_id), pays=_closing.is_customer_payment, built_at=now, built_by=who,
        sources={"invoice": {"table": inv_table, "kind": inv_kind, "from": lo, "to": hi},
                 "tenders": {"table": tender_table, "kind": inv_kind}, "lines": {"table": line_table, "kind": line_kind}})
    rep_resolve = _cr._intake_rep_resolver(client, org_id)
    created = replaced = failed = 0
    failures, reports = [], []
    for doc, report in res["documents"]:
        reports.append(report)
        if dry_run:
            continue
        try:
            emp = None
            try:
                name, _how = rep_resolve(report.get("salesperson") or "")
                emp = (getattr(rep_resolve, "employee_ids", {}) or {}).get(str(name or "").lower()) if name else None
            except Exception:
                emp = None
            r = _ri.upsert_structured(client, org_id=org_id, pos_source=fmt["pos"], document=doc, uploaded_by=who,
                                      store_code=report.get("store_code"), notes=None, employee_id=emp)
            if r.get("replaced"):
                replaced += 1
            else:
                created += 1
        except Exception as e:  # one invoice never sinks the run — it is listed
            failed += 1
            failures.append({"invoice_no": report["invoice_no"], "error": str(e)[:200]})
    words = summary_words(res)
    words.append(f"{created:,} POS sale(s) created, {replaced:,} replaced (re-run), {failed:,} failed" if not dry_run else "dry run — nothing written")
    return {"ok": failed == 0, "ran": True, "pos": fmt["pos"], "format_label": fmt["format"]["label"], "from": lo, "to": hi,
            "stores": stores, "invoices": res["invoices"], "with_lines": res["with_lines"], "without_lines": res["without_lines"],
            "line_invoices_without_header": res["line_invoices_without_header"][:50],
            "lines_tie": res["lines_tie"], "tenders_tie": res["tenders_tie"],
            "created": created, "replaced": replaced, "failed": failed, "failures": failures[:50],
            "words": words, "reports": reports[:200], "reports_total": len(reports),
            "landings": {"invoice": {"table": inv_table, "rows": len(invoices)}, "tenders": {"table": tender_table, "rows": len(tenders)},
                         "lines": {"table": line_table, "rows": len(lines)}}}


def list_rebuilt(client, org_id, lo=None, hi=None, limit=2000):
    """The POS sales rebuilt from the reports (receipt_imports rows whose document says provenance
    'reports'), newest first: invoice #, date, store, customer, total, the payment lines, lines found,
    the tie words. Org-scoped; the print is the existing /receipt-imports/{id}/print."""
    from app.modules.pos import receipt_import as _ri
    q = (client.schema("pos").table("receipt_imports")
         .select("id,sale_id,pos_source,invoice_no,customer_name,total,sale_date,store_code,document,created_at")
         .eq("org_id", org_id).eq(f"document->{_base.PROVENANCE_KEY}->>kind", PROVENANCE_KIND))
    if lo:
        q = q.gte("sale_date", lo)
    if hi:
        q = q.lte("sale_date", hi)
    rows = (q.order("sale_date", desc=True).limit(limit).execute().data) or []
    out = []
    for r in rows:
        d = r.get("document") or {}
        prov = d.get(_base.PROVENANCE_KEY) or {}
        rep = prov.get("report") or {}
        out.append({"id": r.get("id"), "sale_id": r.get("sale_id"), "pos_source": r.get("pos_source"),
                    "invoice_no": r.get("invoice_no"), "sale_date": r.get("sale_date"), "store_code": r.get("store_code"),
                    "store": rep.get("store"), "customer_name": r.get("customer_name"), "total": r.get("total"),
                    "payments": d.get("payments") or [], "lines_found": rep.get("lines_found"),
                    "lines_tie": rep.get("lines_tie"), "tenders_tie": rep.get("tenders_tie"), "words": rep.get("words") or [],
                    "built_at": prov.get("built_at"), "built_by": prov.get("built_by")})
    return out
