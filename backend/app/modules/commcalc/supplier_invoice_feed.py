"""WHAT ONE LINE OF A SUPPLIER'S INVOICE SAYS — pure, supplier-neutral (index §COMMCALC-38).

THE OWNER'S WORDS (2026-10-27): *"i want to pull in invoices from ups and then compare to the sales
reports to make sure every single shipment is being paid for and cash collected by the sales reps,
the time should be recorded so incase the payment is not taken then it shoudl be addressed with the
employees … make it as a recon under supplier / distribuot"*.

WHAT THIS MODULE IS. The parse half: a downloaded supplier-invoice export → normalized invoice lines.
PURE — rows in, dicts out, no DB, no browser, no network. The pull is the existing one loop
(`portal_report_pull`, §COMMCALC-07) and the recon is its own change that READS these rows.

WHY IT IS SUPPLIER-NEUTRAL, AND WHY IT IS NOT `vip_invoices`. The platform already holds distributor
invoices in `commcalc.vip_invoices` / `vip_invoice_lines` (mig 008). Those tables are keyed on one
portal's internal invoice id, carry no supplier or source column at all, and are read by the P&L, the
balance sheet, device COGS and the Distributor Invoices report. Landing a second supplier's invoices
in them would make every one of those readers wrong, silently, on money. So "a supplier invoice" gets
a home that names no supplier — and `vip_invoices` is named in the index as the sibling that should
fold into this one, with the reason it has not yet (it sits on money paths another change owns).
That is an EXCUSED sibling, stated, not an unnoticed duplicate.

THE COLUMN VOCABULARY IS DECLARED. House synonyms below cover what a parcel-carrier or distributor
export usually calls these facts; a tenant whose export says something else adds it in
`portal_calibration.column_synonyms` (RULE TWO — config, never a code change). No supplier's name, no
service name and no tenant's spelling appears in this file.

WHAT A BLANK MEANS, DELIBERATELY. A charge cell that says nothing reads as None, never 0.0. A $0.00
charge asserts the supplier billed nothing for this shipment; a blank asserts nothing at all, and a
recon that cannot tell them apart will report a shipment as "collected in full" because both sides
were zero. Same lesson as §COMMCALC-33, in the place it would have cost the most.

Proof: `backend/harness_supplier_invoice_feed.py` (DB-free, browser-free).
"""
from app.modules.commcalc import report_columns as rc
from app.modules.commcalc.merchant_portals import iso_date

# ── the facts an invoice line can carry ──────────────────────────────────────────────────────────
# The AXES of an invoice, never a supplier's or a service's name: which invoice, when, who was billed,
# what the charge was for, what it cost, and the two references that let a shipment be found again.
COLUMN_SYNONYMS = {
    "invoice_number": ("invoice number", "invoice no", "invoice", "invoice id", "bill number",
                       "statement number", "document number"),
    "invoice_date": ("invoice date", "bill date", "statement date", "billing date", "issue date"),
    "due_date": ("due date", "payment due", "payment due date", "net due date"),
    "charge_date": ("shipped date", "ship date", "pickup date", "transaction date", "service date",
                    "delivery date", "date"),
    "store_code": ("store code", "store number", "store no", "store id", "center", "center number",
                   "location code", "location number", "account number", "shipper number",
                   "account", "franchise id"),
    "store_label": ("store", "store name", "location", "location name", "shipper name", "branch",
                    "site", "bill to", "bill to name", "dba"),
    # THE recon key: the supplier's own per-shipment identifier.
    "shipment_ref": ("tracking number", "tracking no", "tracking", "shipment number",
                     "shipment id", "package id", "waybill", "waybill number", "airbill",
                     "pro number", "barcode"),
    # What the counter typed when the shipment was created — a receipt or transaction number on the
    # POS side. Where the export carries it the recon match is exact rather than inferred.
    "counter_ref": ("reference", "reference number", "reference 1", "reference no",
                    "customer reference", "shipper reference", "receipt", "receipt number",
                    "transaction number", "transaction id", "order number", "po number"),
    "service": ("service", "service type", "service level", "service description", "description",
                "charge description", "product", "item", "charge type", "detail"),
    "quantity": ("quantity", "qty", "pieces", "packages", "units", "count"),
    "weight": ("weight", "billed weight", "actual weight", "dim weight"),
    "amount": ("net charge", "net amount", "total charge", "charge", "amount", "line total",
               "extended amount", "billed amount", "total"),
}

# Why a line could not be used, as a shared vocabulary rather than local prose — a consumer must be
# able to tell "the export did not carry this" from "the export said nothing on this row".
SKIP_NO_INVOICE = "no_invoice_number"
SKIP_NO_DATE = "no_date"
SKIP_NO_AMOUNT = "no_amount"
SKIP_REASONS = (SKIP_NO_INVOICE, SKIP_NO_DATE, SKIP_NO_AMOUNT)

# Which reference a recon may match a shipment on. An ALLOWLIST: a reference kind added later reads
# as NOT matchable until somebody rules on it, rather than silently becoming a join key (the
# `component_is_evidence` shape, index §66).
MATCH_KEYS = ("shipment_ref", "counter_ref")

_LINE_FIELDS = ("invoice_number", "invoice_date", "due_date", "charge_date", "store_code",
                "store_label", "store_key", "shipment_ref", "shipment_key", "counter_ref",
                "counter_key", "service", "quantity", "weight", "amount", "source_line")


def match_key(value):
    """A reference as the recon will compare it. `report_columns.norm` and only that — the supplier
    prints `1Z 999 AA1 01 2345 6784` and the POS prints `1Z999AA1012345 6784`, and if the two sides
    normalize under different rules the shipment is reported as unbilled on one side and uncollected
    on the other. ONE rule, dereferenced."""
    return rc.norm(value)


def map_headers(header_row, synonyms=None):
    """{field: column index} over THIS feed's vocabulary. The mechanism is the one home's
    (§COMMCALC-38); what is local is the vocabulary above."""
    return rc.map_headers(header_row, COLUMN_SYNONYMS, synonyms)


def header_score(row, synonyms=None) -> int:
    """How many of our fields a row looks like — what `portal_report_pull.read_table` uses to tell
    the real header from the banner a portal prints above it."""
    return rc.header_score(row, COLUMN_SYNONYMS, synonyms)


def normalize(table, *, calibration=None, org_id=None, source_id=None, connector=None,
              report_key=None, resolve_store=None, store_code_default=None):
    """One exported supplier invoice → `{rows, skipped, warnings, headers}`. PURE.

    One row per invoice LINE. A line is kept when it names its invoice, a date and a charge; anything
    else is SKIPPED with its reason and reported, never coerced into a usable-looking row. The store
    is resolved through the platform's one resolver when the caller passes it (`coa.store_resolver`,
    §64) — this module resolves nothing itself, so there is no second store vocabulary to drift.
    """
    cal = calibration if isinstance(calibration, dict) else {}
    syn = cal.get("column_synonyms") if isinstance(cal.get("column_synonyms"), dict) else None
    rows, skipped, warnings = [], [], []
    table = [r for r in (table or []) if r is not None]
    if not table:
        return {"rows": [], "skipped": [], "warnings": ["This export held no rows."], "headers": {}}

    hdr = map_headers(table[0], syn)
    if not hdr:
        return {"rows": [], "skipped": [],
                "warnings": ["No column of this export was recognised. Declare this report's "
                             "column_synonyms on the source and pull it again."],
                "headers": {}}
    for field in ("invoice_number", "amount"):
        if field not in hdr:
            warnings.append("This export carries no %s column, so every line of it is unusable "
                            "until one is declared." % field.replace("_", " "))

    for raw in table[1:]:
        if not any(str(c or "").strip() for c in (raw or [])):
            continue
        get = lambda f: rc.cell(raw, hdr.get(f))                      # noqa: E731
        inv = str(get("invoice_number") or "").strip()
        amount = rc.money_or_none(get("amount"))
        charge_date = iso_date(get("charge_date")) or iso_date(get("invoice_date"))
        if not inv:
            skipped.append({"why": SKIP_NO_INVOICE, "line": raw[:8]})
            continue
        if not charge_date:
            skipped.append({"why": SKIP_NO_DATE, "line": raw[:8], "invoice_number": inv})
            continue
        if amount is None:
            # Reported, never filled in with a zero: a blank charge is not a free shipment, and a
            # recon told it was free would close the line as collected.
            skipped.append({"why": SKIP_NO_AMOUNT, "line": raw[:8], "invoice_number": inv})
            continue
        code = str(get("store_code") or "").strip() or (store_code_default or "")
        label = str(get("store_label") or "").strip()
        if resolve_store is not None:
            try:
                hit = resolve_store(code or label) or resolve_store(label) or None
            except Exception:
                hit = None
            if hit:
                code = str(hit.get("store_code") or code or "").strip()
                label = str(hit.get("store_label") or hit.get("address") or label or "").strip()
        ship = str(get("shipment_ref") or "").strip()
        counter = str(get("counter_ref") or "").strip()
        rows.append({
            "org_id": org_id, "source_id": source_id, "supplier_key": connector,
            "report_key": report_key,
            "invoice_number": inv,
            "invoice_date": iso_date(get("invoice_date")),
            "due_date": iso_date(get("due_date")),
            "charge_date": charge_date,
            "store_code": code or None, "store_label": label or None,
            "store_key": rc.norm(code or label) or "",
            "shipment_ref": ship or None, "shipment_key": match_key(ship),
            "counter_ref": counter or None, "counter_key": match_key(counter),
            "service": str(get("service") or "").strip() or None,
            "quantity": rc.money_or_none(get("quantity")),
            "weight": rc.money_or_none(get("weight")),
            "amount": amount,
            "source_line": [None if c is None else str(c) for c in raw][:40],
        })
    if rows and not any(r["shipment_key"] for r in rows):
        # Said once, up front, because it decides how the recon can match at all — and a recon that
        # matched on day and amount without saying so would read as exact when it is not.
        warnings.append("No line of this export carries a shipment reference, so a reconciliation "
                        "against the sales reports cannot match shipment-for-shipment. Declare the "
                        "shipment_ref column for this report if the export has one.")
    return {"rows": rows, "skipped": skipped, "warnings": warnings, "headers": hdr}


def line_key(r):
    """The natural key of one invoice line. A re-pull of an invoice must RESTATE its lines rather
    than landing them twice, and an invoice's own line numbering is not in every export — so the key
    is what identifies the charge: the invoice, the shipment, the service and the day."""
    return (str(r.get("invoice_number") or ""), str(r.get("shipment_key") or ""),
            rc.norm(r.get("service")), str(r.get("charge_date") or ""))


def dedupe(rows):
    """Lines that share the natural key SUMMED, not overwritten. A supplier that bills one shipment
    twice on one invoice (a surcharge printed as its own line with the same description) is $X + $Y
    owed, and keeping the last line would silently drop the rest."""
    out, order = {}, []
    for r in rows or []:
        k = line_key(r)
        if k not in out:
            out[k] = dict(r)
            order.append(k)
            continue
        cur = out[k]
        for f in ("amount", "quantity", "weight"):
            a, b = cur.get(f), r.get(f)
            cur[f] = b if a is None else (a if b is None else a + b)
    return [out[k] for k in order]


def totals_by_invoice(rows):
    """Σ per invoice — what a header check compares against the supplier's own printed total."""
    out = {}
    for r in rows or []:
        k = str(r.get("invoice_number") or "")
        out[k] = round((out.get(k) or 0.0) + (r.get("amount") or 0.0), 2)
    return out
