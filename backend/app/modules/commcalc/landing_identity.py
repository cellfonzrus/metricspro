"""LANDING IDENTITY — which report kind wrote a row, what a landing may replace, who reads it. PURE.

OWNER (2026-09-20, verbatim): *"the data is not flowing into the exec mtd from wherever it is uploaded —
need to know where the data is uploaded and it should be mentioned on the upload page where this
upload will be reflected, with a link. If the user does not know and uploads the data it does no good."*

THE INSTANCE (org f4f1c16e…, measured from upload_trace): the line-level sales export landed 48,875 rows
in `raw_sales` on 2026-09-13; seven days later the by-product aggregate (10,823 rows, no IMEI, no
department / category / product name) landed in the SAME table for the same store × the same date span
through the intake, and the slice replace — store × dates only — deleted every one of the 48,875 rows.
The Executive MTD then read rows that carried nothing it could count. A tender-summary workbook dropped
on the `daily_sales` tile the same morning was refused with a column list instead of the page it belonged to.

THE CLASS (fixed here for every caller — CLAUDE.md "A fix is a DESIGN fix"):
  1. A landing did not record which report kind wrote its rows, so a replace could not be scoped to
     its own kind. Now every writer to a table that more than one kind can land in STAMPS the kind
     (`KIND_STAMP` — the column per table, ONE home), the slice a file replaces is store × dates × KIND,
     rows of a DIFFERENT kind in that slice are never deleted silently: the landing is REFUSED naming the
     loss in plain words unless the caller confirms the replacement. NULL (every row landed before this)
     reads as the table's DEFAULT kind, so nothing existing changes meaning.
  2. Two report kinds answering different questions shared one table. The by-product aggregate
     (`pos_product_sales`) lands in its OWN table (`raw_sales_product`, mig 1011 — written, NOT applied);
     `column_mapping.TABLE_MAP` is where that fact lives and every caller dereferences it.
  3. A landing whose consumers could not use it was accepted silently. `CONSUMERS` — the ONE map of
     who reads each table and which fields they need — gates the commit: a frame whose rows are blank on
     every field a gating consumer needs is refused BEFORE a row is written, naming the consumer.
  4. The person was not told where an upload shows up. `shows_in(row)` derives "this upload will show
     in …" from the registry row's landing → table → `CONSUMERS`; every upload surface renders it; the
     Executive MTD's empty banner links back through `feeds_for_table`. No second list anywhere
     (`harness_landing_identity_lock.py` fails the build on one).
  5. A wrong file was refused with a column list. `looks_like(headers)` runs the registry's own
     detection over the header row and names the card and the page it belongs to.

RULE TWO: no carrier, POS vendor, tenant or product is named here. Screen keys are the names of
`frontend/src/components/ScreenLink.tsx` (`SCREENS`), whose hrefs come from `lib/rbac.ts` NAV — the
ONE screen → href map (the lock pins that every key here exists there).
"""
import re

from app.modules.commcalc import report_kinds as _rk

# ── 1. THE KIND STAMP — per table, the column that records the report kind that wrote a row ──────
# Only tables MORE THAN ONE report kind may land in need a stamp; a table with exactly one layout is
# excused by name (its rows have one meaning). `default` = what NULL means — the kind every row landed
# before this design belongs to, so existing tenants read exactly as they did.
KIND_STAMP = {
    # mig 727 added `source` as writer provenance (NULL | 'pos_builtin'); a report-kind key in it is
    # one more vocabulary of the same column: "who wrote this row". NULL / 'pos_builtin' / any value
    # that is not a layout key targeting this table = the default kind.
    "raw_sales":         {"column": "source", "default": "sales",
                          "what": "line-level sales rows (one row per sale line, with IMEI / phone number)"},
    "raw_sales_product": {"column": "source", "default": "pos_product_sales",
                          "what": "product-level sales rows (one row per invoice line with SKU, cost and selling price)"},
    # Sales by invoice with the tender types (mig 1012, owner 2026-09-21): the invoice header and its
    # child tender / tax-component rows. One layout today; stamped from day one so a second layout
    # (another POS's invoice export) can never collide silently. The child rows carry the PARENT's kind.
    "raw_sales_invoice":        {"column": "source", "default": "sales_by_invoice",
                                 "what": "invoice-level sales rows (one row per invoice with its totals and tax)"},
    "raw_sales_invoice_tender": {"column": "source", "default": "sales_by_invoice",
                                 "what": "invoice tender rows (one row per invoice and tender column, with its class)"},
}
# Tables a landing path targets that a migration must create first — the refusal names the file.
TABLE_MIGRATION = {"raw_sales_product": "1011_raw_sales_product.sql",
                   "raw_sales_invoice": "1012_sales_by_invoice.sql", "raw_sales_invoice_tender": "1012_sales_by_invoice.sql"}

# ── 3. THE CONSUMERS — who reads each landing table, and which fields must not be blank for them ──
# `needs` = the fields the consumer classifies or sums on; a frame blank on EVERY one of them lands
# nothing that consumer can use. `gate` = refuse the landing when that happens (the money / display
# readers); a non-gating consumer is informational ("this upload will show in …"), never a refusal.
# Screen keys are ScreenLink SCREENS keys (frontend), hrefs come from NAV — never spelled here.
CONSUMERS = {
    "raw_sales": [
        {"screen": "exec_mtd", "label": "Executive MTD", "needs": ["department", "category", "product_desc"], "gate": True,
         "why": "the line metrics (bill payments, phones, protect, activation fee) match department / category / product name"},
        {"screen": "sales_report", "label": "Sales Report", "needs": ["contract_type", "department", "category", "product_desc"], "gate": True,
         "why": "activations, accessories and boxes are classified from contract type, department, category and product name"},
        {"screen": "gp_report", "label": "Gross Profit", "needs": ["ext_price", "gp"], "gate": True,
         "why": "revenue and gross profit are summed from the price and GP columns"},
        {"screen": "daily_targets", "label": "Daily Targets", "needs": ["contract_type", "product_desc", "ext_price"], "gate": False,
         "why": "actuals against target come from the same classified lines"},
        {"screen": "rep_commissions", "label": "Rep commissions", "needs": ["salesperson", "contract_type", "product_desc"], "gate": False,
         "why": "each rep's activations and accessory sales are read per salesperson"},
        {"screen": "sales_recon", "label": "Sales Feed Recon", "needs": ["trans_id"], "gate": False,
         "why": "the monthly file is reconciled against the daily feed at transaction grain"},
        {"screen": "bill_payments", "label": "Bill Payments", "needs": ["department", "category", "product_desc"], "gate": False,
         "why": "bill payments are extracted from the sales lines by the same rule the Executive MTD uses"},
        {"screen": "tax_collected", "label": "Tax Collected", "needs": ["tax"], "gate": False,
         "why": "sales tax collected is summed from the tax column when the export carries one"},
        {"screen": "inventory_sold_recon", "label": "Inventory vs Sold", "needs": ["serial_1", "mdn"], "gate": False,
         "why": "sold units are paired to on-hand inventory by IMEI / serial, else by the phone number on the line"},
        {"screen": "imei_recon", "label": "IMEI Reconciliation", "needs": ["serial_1"], "gate": False,
         "why": "sold IMEIs are matched against the carrier's rebate and inventory feeds"},
        {"screen": "pl_statement", "label": "P&L Statement", "needs": ["ext_price", "gp"], "gate": False,
         "why": "device and accessory revenue book to the P&L from the sales lines"},
        {"screen": "pos_receipts", "label": "POS sales / receipts", "needs": ["trans_id", "product_desc", "ext_price"], "gate": False,
         "why": "each invoice's lines become the items of a POS sale rebuilt with the sales-by-invoice header, "
                "reprintable in the declared POS's receipt format (index §30.14)"},
    ],
    "raw_sales_product": [
        {"screen": "onboarding_intake", "label": "Onboarding — Stage 4 verify and report links", "needs": ["sku", "ext_price"], "gate": True,
         "why": "the product-level rows are verified (rows, Σ price, Σ cost) and linked to the other reports by invoice and SKU; "
                "no money report sums them — summing product-level rows against line-level rows would double-count (owner decision 2026-09-20)"},
    ],
    # Sales by invoice with the tender types (owner 2026-09-21). HONEST: the header table's only reader is
    # the intake's Stage 4 (the invoice totals, Σ tax as a tie-out, the report links by invoice number /
    # store / date) — the Tax Collected aggregator does NOT read it yet (PROPOSED, index §30.13), so it is
    # not listed: "this upload will show in" must never name a page that does not read it. The TENDER
    # rows ARE read by every closing cash / card recon when the org's tender basis is 'invoice' or
    # 'x_report_else_invoice' (closing.router._tender_split_by_store — owner: "nothing on cash collected").
    "raw_sales_invoice": [
        {"screen": "onboarding_intake", "label": "Onboarding — Stage 4 verify, tender split vs X-report, tax tie-out, report links",
         "needs": ["trans_id", "invoice_total"], "gate": True,
         "why": "each invoice's totals are verified, its tender columns are split per store-day beside the register's X-report, "
                "Σ tax is shown as a tie-out, and the invoice number links the report to the line-level sales export"},
        {"screen": "pos_receipts", "label": "POS sales / receipts", "needs": ["trans_id", "invoice_total"], "gate": False,
         "why": "each invoice header becomes a POS sale (with its lines from the line-level export, by invoice number), "
                "reprintable in the declared POS's receipt format (index §30.14)"},
    ],
    "raw_sales_invoice_tender": [
        {"screen": "onboarding_intake", "label": "Onboarding — Stage 4 tender split vs X-report", "needs": ["amount", "tender_class"], "gate": True,
         "why": "one row per invoice and tender column, classed through the one tender vocabulary; summed per store-day beside the X-report"},
        {"screen": "closing_recon", "label": "Closing Reconciliation (cash collected, cash / card recon, deposit recon)", "needs": ["amount", "tender_class"], "gate": False,
         "why": "the tender split per store-day the closing recons read when the company's tender basis is the invoice tenders "
                "(or the X-report else the invoice) — set at intake step 2.5b; the house default reads the X-report"},
        {"screen": "pos_receipts", "label": "POS sales / receipts", "needs": ["amount", "tender_class"], "gate": False,
         "why": "the customer-payment tender rows print as the receipt's payment lines; a tender class with no place on the closing "
                "axis (a vendor rebate, a coupon) explains the difference between the lines and what the customer paid (index §30.14)"},
    ],
    "daily_sales_feed": [
        {"screen": "sales_recon", "label": "Sales Feed Recon", "needs": ["trans_id"], "gate": False,
         "why": "the daily feed is the live side of the monthly-vs-daily reconciliation"},
        {"screen": "exec_mtd", "label": "Executive MTD", "needs": ["department", "category", "product_desc"], "gate": False,
         "why": "read when the month's line-level table is empty (the live side of the pair)"},
        {"screen": "sales_report", "label": "Sales Report", "needs": ["contract_type", "product_desc"], "gate": False,
         "why": "read when the month's line-level table is empty"},
        {"screen": "bill_payments", "label": "Bill Payments", "needs": ["department", "category", "product_desc"], "gate": False,
         "why": "bill payments are extracted from the feed's lines too"},
        {"screen": "tax_collected", "label": "Tax Collected", "needs": ["tax"], "gate": False, "why": "tax collected reads the feed's tax column"},
    ],
    "inventory_aging_device": [
        {"screen": "inventory_sold_recon", "label": "Inventory vs Sold", "needs": ["imei", "serial"], "gate": False,
         "why": "on-hand units are checked against sales and activations"},
        {"screen": "imei_recon", "label": "IMEI Reconciliation", "needs": ["imei"], "gate": False, "why": "inventory IMEIs vs sold IMEIs"},
        {"screen": "device_history", "label": "Device History", "needs": ["imei"], "gate": False, "why": "each unit's timeline"},
        {"screen": "inventory_recon", "label": "Inventory Recon", "needs": ["store", "unit_cost"], "gate": False, "why": "on-hand value by store"},
    ],
    "commission_ledger": [
        {"screen": "commission_ledger", "label": "Commission Ledger", "needs": ["raw_amount"], "gate": False, "why": "the five buckets and the tie-out"},
        {"screen": "carrier_vs_pay", "label": "Carrier Earned vs Employee Paid", "needs": ["raw_amount", "rep_user"], "gate": False,
         "why": "what the carrier paid per rep beside what the rep was paid"},
        {"screen": "whatif", "label": "What-If Analysis", "needs": ["raw_amount"], "gate": False, "why": "carrier income headings"},
    ],
    "pos_tender_summary": [
        {"screen": "closing_recon", "label": "Closing Reconciliation", "needs": ["amount"], "gate": False,
         "why": "the register's cash / card / other per store-day beside the closing sheet"},
    ],
    "merchant_settlement_day": [
        {"screen": "closing_recon", "label": "Closing Reconciliation", "needs": ["net_amount"], "gate": False,
         "why": "the processor's settlement per merchant-day beside the card tender"},
    ],
    "raw_ma_daily_tx": [
        {"screen": "bill_payments", "label": "Bill Payments", "needs": ["retail_cost"], "gate": False,
         "why": "the processor side of the bill-pay coverage check"},
        {"screen": "pl_statement", "label": "P&L Statement", "needs": ["retail_cost"], "gate": False, "why": "the bill-pay carve-out"},
    ],
    "raw_epay_daily_tx": [
        {"screen": "bill_payments", "label": "Bill Payments", "needs": ["retail"], "gate": False,
         "why": "the processor side of the bill-pay coverage check"},
    ],
    "raw_vendor_rebate": [
        {"screen": "vendor_rebates", "label": "Vendor Rebate History", "needs": ["earned_amount"], "gate": False,
         "why": "earned per line, reported beside collected — never summed"},
    ],
    "raw_custom_import": [
        {"screen": "activations", "label": "Activations", "needs": [], "gate": False,
         "why": "a self-serve custom sheet is read by the page its sheet label names"},
    ],
    "raw_payment_detail": [
        {"screen": "pay_discrepancy", "label": "Pay Discrepancy", "needs": ["amount"], "gate": False, "why": "paid vs sold activations"},
        {"screen": "rep_commissions", "label": "Rep commissions", "needs": ["amount"], "gate": False, "why": "the processor's payment detail per rep"},
    ],
    "raw_mi": [
        {"screen": "rep_commissions", "label": "Rep commissions", "needs": ["actual_mi_payout"], "gate": False, "why": "monthly incentive per subscriber"},
    ],
    "raw_comp_report": [
        {"screen": "pl_statement", "label": "P&L Statement", "needs": ["payment_amount"], "gate": False, "why": "store-level rebates and MDF"},
    ],
    "raw_dlar_rep": [{"screen": "kpi", "label": "KPI Metrics", "needs": [], "gate": False, "why": "per-rep KPI figures"}],
    "raw_dlar_store": [{"screen": "kpi", "label": "KPI Metrics", "needs": [], "gate": False, "why": "store-level KPI figures"}],
    "raw_catalog": [{"screen": "gp_report", "label": "Gross Profit", "needs": ["cost"], "gate": False, "why": "product cost and category for GP"}],
    "raw_categories": [{"screen": "gp_report", "label": "Gross Profit", "needs": [], "gate": False, "why": "payment type → category"}],
    "raw_ma_commission": [
        {"screen": "pay_discrepancy", "label": "Pay Discrepancy", "needs": [], "gate": False, "why": "per-activation commission detail"},
        {"screen": "pl_statement", "label": "P&L Statement", "needs": [], "gate": False, "why": "commission received"},
    ],
    "raw_ma_fulfillment": [{"screen": "ma_handsets", "label": "Marketplace Handset COGS", "needs": [], "gate": False, "why": "handset fulfillment orders"}],
}


def _s(v):
    return "" if v is None else str(v).strip()


# ── which kinds land in a table (dereferenced from the layout registry, never listed here) ──────
def kinds_for_table(table, table_map):
    """The layout keys (column_mapping.TABLE_MAP report keys) that target `table`."""
    return sorted(k for k, t in (table_map or {}).items() if t == table)


def other_kinds(table, kind, table_map):
    return [k for k in kinds_for_table(table, table_map) if k != kind]


def multi_kind_tables(table_map):
    """Every table more than one layout targets — each MUST carry a stamp (the lock asserts it)."""
    seen = {}
    for k, t in (table_map or {}).items():
        seen.setdefault(t, []).append(k)
    return sorted(t for t, ks in seen.items() if len(ks) > 1)


def stamp_column(table):
    return (KIND_STAMP.get(table) or {}).get("column")


def default_kind(table):
    return (KIND_STAMP.get(table) or {}).get("default")


def kind_of_row(table, row, table_map):
    """The report kind a landed row belongs to: its stamp when it is a layout key of this table, else
    the table's default kind (NULL, a writer-provenance word, an unknown value — every pre-existing
    row — reads as the default; nothing existing changes meaning)."""
    col = stamp_column(table)
    if not col:
        return None
    v = _s((row or {}).get(col))
    return v if v in kinds_for_table(table, table_map) else default_kind(table)


def stamp(rows, table, kind):
    """Stamp the report kind on every row bound for a stamped table. Returns the column stamped (or
    None when the table carries no stamp). One call, every writer."""
    col = stamp_column(table)
    if not col or not kind:
        return None
    for r in rows or []:
        r[col] = kind
    return col


def partition_slice(table, rows, kind, table_map):
    """Split the rows already in a file's slice into the ones THIS kind may replace and the ones of
    OTHER kinds (counted per kind) — the cross-kind loss a landing must name before it happens."""
    mine, others, by_kind = [], [], {}
    for r in rows or []:
        k = kind_of_row(table, r, table_map)
        if k is None or k == kind:
            mine.append(r)
        else:
            others.append(r)
            by_kind[k] = by_kind.get(k, 0) + 1
    return {"mine": mine, "others": others, "others_by_kind": by_kind}


def kind_label(kind, registry_rows=None, table=None):
    """The layman label of a layout key: the registry row whose layout is the key, else the stamped
    table's own description of its default kind, else the key."""
    for r in registry_rows or []:
        if _s(r.get("layout")) == _s(kind) and _s(r.get("label")):
            return _s(r.get("label"))
    if table and default_kind(table) == kind:
        return KIND_STAMP[table]["what"]
    return _s(kind).replace("_", " ")


def cross_kind_refusal(table, kind, incoming, others_by_kind, scope, registry_rows=None):
    """The plain-words sentence a landing is refused with when rows of a DIFFERENT kind sit in the
    slice it owns: what would be lost, what would replace it, where, and how to proceed."""
    lost = ", ".join(f"{n:,} rows of '{kind_label(k, registry_rows, table)}'" for k, n in sorted(others_by_kind.items()))
    where = ""
    if scope:
        where = (f" in the same {len(scope.get('values') or [])} {scope.get('partition_col')} value(s) between "
                 f"{scope.get('lo')} and {scope.get('hi')}")
    return (f"Not landed — this would replace {lost} with {incoming:,} rows of '{kind_label(kind, registry_rows, table)}'"
            f"{where} in commcalc.{table}. They answer different questions and one would silently overwrite the other. "
            f"Either upload the file under its own report kind, or confirm the replacement (replace_other_kinds) "
            f"to delete those rows on purpose.")


def apply_kind_filter(q, table, kind, table_map):
    """Narrow a period-keyed delete / select to THIS kind's rows on a stamped table. The default kind
    owns every row that is NULL or carries no other kind's key (writer-provenance words included);
    another kind owns exactly its stamped rows. A no-op when no other kind targets the table — so the
    legacy period-wide writers are byte-identical until a second layout lands beside them."""
    col = stamp_column(table)
    others = other_kinds(table, kind, table_map) if col else []
    if not col or not others:
        return q
    if kind != default_kind(table):
        return q.eq(col, kind)
    return q.or_(f"{col}.is.null,{col}.not.in.({','.join(others)})")


# ── 3. the consumer gate ─────────────────────────────────────────────────────────────────────────
def consumers_for_table(table):
    return list(CONSUMERS.get(table) or [])


def blank_consumer_fields(rows, table):
    """The GATING consumers of `table` whose every needed field is blank on every row — what makes a
    landing 'accepted but unusable'. [] = every gating consumer has at least one field it can read."""
    out = []
    rows = list(rows or [])
    if not rows:
        return out
    for c in CONSUMERS.get(table) or []:
        if not c.get("gate") or not c.get("needs"):
            continue
        filled = [f for f in c["needs"] if any(_s(r.get(f)) not in ("", "0", "0.0", "None") for r in rows)]
        if not filled:
            out.append({"screen": c["screen"], "label": c["label"], "fields": list(c["needs"]), "why": c.get("why")})
    return out


def consumer_refusal(blank, table, kind=None, registry_rows=None):
    parts = [f"{b['label']} needs {' / '.join(b['fields'])}" for b in blank]
    what = f" '{kind_label(kind, registry_rows, table)}'" if kind else ""
    return (f"Not landed — every row of this{what} file is blank on the columns its readers count: "
            + "; ".join(parts) + f". Nothing written to commcalc.{table}. Map those columns at the column step "
            "(or upload the export that carries them) — a landing nobody can read is not a landing.")


# ── 4. where an upload shows up — DERIVED from the registry row, never listed per surface ─────────
def landing_table_for(row, table_map, route_tables=None, landing_tables=None):
    """The table a registry row's upload lands in: its layout's table (TABLE_MAP), else the table of
    its legacy upload route, else the table its intake landing writes, else the custom-import table."""
    layout = _s((row or {}).get("layout"))
    if layout and (table_map or {}).get(layout):
        return table_map[layout]
    for u in (row or {}).get("upload_types") or []:
        t = (route_tables or {}).get(u)
        if t:
            return t
    t = (landing_tables or {}).get(_s((row or {}).get("landing")))
    if t:
        return t
    if _s((row or {}).get("custom_sheet_label")):
        return "raw_custom_import"
    return None


def where_to_upload(row):
    """The page an upload of this kind belongs on: the intake (a landing the intake takes) — else the
    Upload Files page through its route key(s). Screen keys of ScreenLink SCREENS."""
    r = row or {}
    if _s(r.get("landing")) in _rk.INTAKE_LANDINGS:
        return {"screen": "onboarding_intake", "label": "Onboarding — Commission Intake", "upload_types": list(r.get("upload_types") or [])}
    if r.get("upload_types"):
        return {"screen": "upload_files", "label": "Upload Files", "upload_types": list(r.get("upload_types") or [])}
    if _s(r.get("custom_sheet_label")):
        return {"screen": "upload_files", "label": "Upload Files", "upload_types": [], "custom_sheet_label": _s(r.get("custom_sheet_label"))}
    return {"screen": "upload_files", "label": "Upload Files", "upload_types": []}


def shows_in(row, table_map, route_tables=None, landing_tables=None):
    """"This upload will show in: …" for one registry row — the table it lands in and that table's
    consumers (screen keys + labels + the fields each needs). Recorded-only kinds say so."""
    t = landing_table_for(row, table_map, route_tables, landing_tables)
    if not t:
        return {"table": None, "consumers": [], "note": "recorded as received — no table reads it yet (an owner decision names one)"}
    cons = [{"screen": c["screen"], "label": c["label"], "needs": list(c.get("needs") or []), "gate": bool(c.get("gate")), "why": c.get("why")}
            for c in CONSUMERS.get(t) or []]
    return {"table": t, "consumers": cons,
            "note": None if cons else f"lands in commcalc.{t}; no report reads it yet"}


def feeds_for_table(table, registry_rows, table_map, route_tables=None, landing_tables=None):
    """The registry kinds whose upload lands in `table` — the way back from an empty report to the
    upload that feeds it (the Executive MTD banner): key, layman label, and where to upload it."""
    out = []
    for r in registry_rows or []:
        if not r.get("is_active", True):
            continue
        if landing_table_for(r, table_map, route_tables, landing_tables) == table:
            out.append({"key": r["key"], "label": r["label"], "where": where_to_upload(r)})
    return out


def blank_fields_over(rows, fields):
    """Of `fields`, the ones blank on EVERY row (the 'rows exist but nothing to count' signal)."""
    rows = list(rows or [])
    if not rows:
        return []
    return [f for f in fields if not any(_s(r.get(f)) not in ("", "0", "0.0", "None") for r in rows)]


# ── 5. a wrong file names the right page ─────────────────────────────────────────────────────────
def looks_like(headers, registry_rows, signature_rows=None, target_fields=None):
    """Run the registry's own detection over a header row and, when it confirms a kind, say which
    card it is and where it belongs. None when detection cannot say (then only the column list)."""
    try:
        ranked = _rk.detect_report_kind(headers, registry_rows, signature_rows, target_fields=target_fields)
        dec = _rk.decide(ranked)
    except Exception:
        return None
    if dec.get("mode") == "none" or not dec.get("candidates"):
        return None
    by_key = {r["key"]: r for r in registry_rows or []}
    cands = []
    for k, conf, _ev in dec["candidates"]:
        r = by_key.get(k)
        if r:
            cands.append({"key": k, "label": r["label"], "confidence": conf, "where": where_to_upload(r)})
    if not cands:
        return None
    return {"mode": dec["mode"], "candidates": cands}


def looks_like_sentence(found):
    """'This looks like a <label>. Upload it under <page>.' — or the ask form when two are close."""
    if not found or not found.get("candidates"):
        return ""
    c = found["candidates"]
    if found.get("mode") == "ask" and len(c) > 1:
        names = " or ".join(f"'{x['label']}'" for x in c)
        return f"This looks like {names} — upload it under {c[0]['where']['label']} and confirm which."
    x = c[0]
    w = x["where"]
    via = ""
    if w.get("upload_types"):
        via = f" (the '{w['upload_types'][0]}' tile)"
    elif w.get("custom_sheet_label"):
        via = f" (the '{w['custom_sheet_label']}' sheet)"
    return f"This looks like a '{x['label']}'. Upload it under {w['label']}{via}."


def screen_keys():
    """Every ScreenLink key this module names — the lock pins each exists in SCREENS."""
    keys = {"onboarding_intake", "upload_files"}
    for cons in CONSUMERS.values():
        for c in cons:
            keys.add(c["screen"])
    return sorted(keys)


_KEY_RE = re.compile(r"^[a-z0-9_]+$")
assert all(_KEY_RE.match(k) for k in screen_keys()), "screen keys are snake_case ScreenLink keys"
