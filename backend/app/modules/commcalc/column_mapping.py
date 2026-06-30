"""Generic, config-driven column mapping — the "any-carrier" ingestion spine (SaaS framework A2).

A new carrier's export uses different column headers than Boost's. Rather than hard-code a
map_*_row per report, commcalc.column_mapping stores the mapping as DATA: source spreadsheet
header -> canonical target field (+ a transform). This module is the engine over that table.

ADDITIVE + SAFE: the legacy hard-coded upload branches (router.upload_file) are untouched and stay
the proven path for the seeded Boost reports. The generic importer (router.upload_mapped) uses this
engine for NEW connector reports. TARGET_FIELDS below also seeds sensible defaults (the existing
Boost layouts) so every report shows up editable in the mapping UI from day one.
"""
from datetime import datetime, timezone


def _sf(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


# ── value transforms ─────────────────────────────────────────────────────────────────────────
def _t_text(v):   return "" if v is None else str(v).strip()
def _t_number(v): return _sf(v)
def _t_int(v):
    f = _sf(v)
    return int(f) if f else None
def _t_date10(v):
    s = "" if v is None else str(v).strip()
    return s[:10] or None
def _t_mdn(v):    return ("" if v is None else str(v)).replace(".0", "").strip()
def _t_upper(v):  return _t_text(v).upper()
def _t_lower(v):  return _t_text(v).lower()
def _t_bool(v):   return str(v).strip().lower() in ("1", "true", "yes", "y", "t")

TRANSFORMS = {
    "text": _t_text, "number": _t_number, "int": _t_int, "date10": _t_date10,
    "mdn": _t_mdn, "upper": _t_upper, "lower": _t_lower, "bool": _t_bool,
}
TRANSFORM_KEYS = list(TRANSFORMS.keys())


def apply_transform(val, transform):
    return TRANSFORMS.get(transform or "text", _t_text)(val)


# ── canonical target-field registry per report_key ───────────────────────────────────────────
# Each field: (target_field, label, transform, required, default_source_header, [aliases...])
# default_source_header + aliases drive both seeding (POST /column-mapping/seed) and the
# upload-sample auto-suggest. Derived verbatim from the existing map_*_row functions.
TARGET_FIELDS = {
    "comp_report": [
        ("begin_date", "Begin date", "date10", True, "Begin Date", ["BeginDate"]),
        ("end_date", "End date", "date10", False, "End Date", ["EndDate"]),
        ("retailer_account", "Retailer account", "text", False, "Retailer Account", ["RetailerAccount"]),
        ("owner_id", "Owner ID", "text", False, "OwnerID", ["Owner ID"]),
        ("terminal_id", "Terminal ID", "text", False, "TerminalID", ["Terminal ID"]),
        ("account_id", "Account ID", "text", False, "AccountID", ["Account ID"]),
        ("business_name", "Business name", "text", False, "Business Name", ["BusinessName"]),
        ("business_address", "Business address", "text", False, "Business Address", ["BusinessAddress"]),
        ("compensation_type", "Compensation type", "text", True, "Compensation Type", ["CompensationType", "category"]),
        ("brand", "Brand", "text", False, "Brand", []),
        ("salesforce_id", "Salesforce ID", "text", False, "SalesForce ID", ["Salesforce ID", "SalesForceID"]),
        ("quantity", "Quantity", "number", False, "Quantity", []),
        ("payment_amount", "Payment amount", "number", True, "Payment Amount", ["PaymentAmount", "amount"]),
        ("external_reference_id", "External reference ID", "text", False, "ExternalReferenceID", ["External Reference ID"]),
        ("has_payment_detail", "Has payment detail", "text", False, "HasPaymentDetail", ["Has Payment Detail"]),
        ("internal_brand", "Internal brand", "text", False, "InternalBrand", ["Internal Brand"]),
    ],
    "mi_report": [
        ("salesforce_id", "Salesforce ID", "text", True, "SalesForceID", ["Salesforce ID"]),
        ("subscriber_id", "Subscriber ID", "text", False, "SubscriberID", ["Subscriber ID"]),
        ("subscriber_status", "Subscriber status", "text", True, "Subscriber Status", []),
        ("phone_number", "Phone number", "mdn", False, "Phone Number", ["MDN"]),
        ("device_serial", "Device serial", "mdn", False, "Device Serial", ["IMEI", "Serial"]),
        ("mi_activation_date", "MI activation date", "date10", False, "MI Activation Date", []),
        ("mi_deactivation_date", "MI deactivation date", "date10", False, "MI Deactivation Date", []),
        ("residual_transfer_in_date", "Residual transfer-in date", "date10", False, "Residual Transfer In Date", []),
        ("residual_transfer_out_date", "Residual transfer-out date", "date10", False, "Residual Transfer Out Date", []),
        ("customer_plan", "Customer plan", "text", False, "Customer Plan", []),
        ("base_mrc", "Base MRC", "number", False, "Base MRC Amount", []),
        ("commissionable_mrc", "Commissionable MRC", "number", False, "Commissionable MRC Amount", []),
        ("actual_mi_payout", "Actual MI payout", "number", False, "Actual MI Payout Amount", []),
        ("actual_atu_payout", "Actual ATU payout", "number", False, "Actual ATU Payout Amount", []),
        ("rep_username", "Rep username", "text", False, "Rep Username", []),
        ("door_type", "Door type", "text", False, "Door Type", []),
        ("report_month", "Report month", "text", False, "Report Month", []),
    ],
    "payment_detail": [
        ("business_address", "Business address", "text", False, "Business Address", []),
        ("payment_type", "Payment type", "text", True, "Payment Type", []),
        ("amount", "Amount", "number", True, "Amount", []),
        ("mdn", "MDN / phone", "mdn", False, "Phone Number", ["MDN"]),
        ("imei", "IMEI", "mdn", False, "IMEI", []),
        ("payment_date", "Payment date", "date10", False, "Payment Date", []),
        ("rep_username", "Rep username", "text", False, "Rep Username", []),
    ],
    "sales": [
        ("store", "Store", "text", False, "Store", []),
        ("salesperson", "Salesperson", "text", True, "Salesperson", []),
        ("user_login", "User login", "text", False, "User Login", []),
        ("contract_type", "Contract type", "text", False, "Contract Type", []),
        ("department", "Department", "text", False, "Department", []),
        ("category", "Category", "text", False, "Category", []),
        ("product_desc", "Product description", "text", False, "Product Desc", ["Product Description"]),
        ("product_id", "Product ID", "int", False, "Product ID", []),
        ("gp", "Gross profit", "number", False, "GP", []),
        ("ext_price", "Ext price", "number", False, "Ext Price", []),
        ("trans_id", "Transaction ID", "text", True, "Trans ID", []),
        ("trans_date", "Transaction date", "date10", False, "Trans Date Time", ["Trans Date"]),
        ("mdn", "Mobile number", "mdn", False, "Activated Mobile Number", ["Primary Account Number"]),
        ("serial_1", "Serial", "text", False, "Serial 1", []),
        ("register", "Register", "text", False, "Register", []),
        ("tender_type", "Tender type", "text", False, "Tender Type", []),
        ("voided", "Voided", "text", False, "Voided", []),
        ("trans_type", "Transaction type", "text", False, "Trans Type", []),
        ("customer", "Customer", "text", False, "Customer", []),
        ("email", "Email", "text", False, "Email", []),
        ("customer_no", "Customer #", "mdn", False, "Customer #", ["Customer No"]),
    ],
    # GENERIC carrier commission STATEMENT (Total Wireless / VidaPay, Cricket, …). Defaults match Total's
    # "MA - Commission Details"; any carrier maps the columns it has — unmapped amount fields stay 0.
    "carrier_commission": [
        ("rep_name", "Rep name", "text", True, "User Name", ["Rep Name", "Sales Rep", "User"]),
        ("rep_user_id", "Rep user id", "text", False, "User Id", ["UserId"]),
        ("store", "Store / merchant account", "text", False, "MerchantAccountId", ["Merchant Account Id", "BAN", "Account ID"]),
        ("account_id", "Account id", "text", False, "BAN", ["Account ID", "AccountId"]),
        ("carrier_name", "Carrier name", "text", False, "Carrier Name", []),
        ("trans_date", "Date", "date10", False, "Date", ["Transaction Date", "Date of Transaction"]),
        ("activation_type", "Activation type", "text", False, "Activation Type", ["Activation Type 2", "Order Type"]),
        ("sub_type", "Sub type", "text", False, "Sub Type", []),
        ("sku", "SKU", "text", False, "SKU", []),
        ("imei", "IMEI", "mdn", False, "IMEI", []),
        ("mdn", "MDN", "mdn", False, "MDN", ["Phone Number"]),
        ("order_id", "Order id", "text", False, "Activation Order", ["Order Number", "POS Invoice"]),
        ("device_margin", "Device margin", "number", False, "Device Margin", []),
        ("consumer_margin", "Consumer margin", "number", False, "Consumer Margin", []),
        ("rebate", "Rebate", "number", False, "Rebate", []),
        ("mrc_net_discount", "MRC net discount", "number", False, "MRC Net Discount", []),
        ("fees_margin", "Fees margin", "number", False, "Fees Margin", ["Fees"]),
        ("spiff_m1", "1st month spiff", "number", False, "1st Month Spiff", []),
        ("spiff_m2", "2nd month spiff", "number", False, "2nd Month Spiff", []),
        ("spiff_m3", "3rd month spiff", "number", False, "3rd Month Spiff", []),
        ("spiff_m4", "4th month spiff", "number", False, "4th Month Spiff", []),
        ("spiff_m5", "5th month spiff", "number", False, "5th Month Spiff", []),
        ("spiff_m6", "6th month spiff", "number", False, "6th Month Spiff", []),
        ("residual", "Residual", "number", False, "Residual", ["Residual Amount"]),
        ("other_amount", "Other amount", "number", False, "Other", []),
    ],
    # CANONICAL commission/payout LEDGER source (Total/MA Daily Tx + any carrier). The single signed
    # amount (raw_amount) is classified into the five canonical buckets by commission_ledger.classify();
    # the bucket columns are DERIVED, not mapped. Defaults match the MA Daily Tx headers.
    "commission_ledger": [
        ("account_id", "Account id", "text", False, "Account ID", ["AccountId"]),
        ("account_name", "Account name", "text", False, "Account Name", ["Direct MA Name"]),
        ("store", "Store / dealer", "text", False, "Direct MA Name", ["Account Name", "Top MA Name"]),
        ("rep_user", "Rep / user", "text", False, "User", ["Rep", "Salesperson", "User Name"]),
        ("order_number", "Order number", "text", False, "Order Number", ["Order Id", "Order #"]),
        ("order_type", "Order type", "text", False, "Order Type", ["Transaction Type", "Type"]),
        ("product_name", "Product / description", "text", True, "Product Name", ["Description", "Product Desc"]),
        ("trans_date", "Transaction date", "date10", False, "Date of Transaction", ["Trans Date", "Date"]),
        ("due_date", "Due date", "date10", False, "Date Due", ["Due Date"]),
        ("raw_amount", "Amount (signed; negative = payout)", "number", True, "Retail Cost", ["Amount", "Net Amount", "Payout"]),
    ],
}

# Amount fields summed into carrier_commission.total_commission (the rep's statement commission).
CARRIER_COMMISSION_AMOUNTS = ("device_margin", "consumer_margin", "rebate", "mrc_net_discount",
                              "fees_margin", "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4",
                              "spiff_m5", "spiff_m6", "residual", "other_amount")

# Target table for the seeded report keys. New report keys resolve their target_table from
# report_definitions (the endpoint passes it in).
TABLE_MAP = {
    "comp_report": "raw_comp_report",
    "mi_report": "raw_mi",
    "payment_detail": "raw_payment_detail",
    "sales": "raw_sales",
    "carrier_commission": "carrier_commission",
    "commission_ledger": "commission_ledger",
}


def _registry_overlay(report_key, client=None, org_id=None):
    """Hard-coded field tuples for report_key, with the per-tenant target_field_registry (migration 070)
    overlaid: a registry row OVERRIDES a default with the same target_field (relabel/alias/transform), and
    registry-only fields are APPENDED. Pure defaults when no client/org_id or the table is absent — so
    every caller below degrades byte-for-byte to today's behaviour. The merge mirrors
    commission_catalog.merged_target_fields but generalised to ANY report_key (C-Phase2)."""
    base = list(TARGET_FIELDS.get(report_key, []))
    if client is None or not org_id:
        return base
    try:
        from app.modules.commcalc import target_registry
        reg = target_registry.registry_tuples(client, org_id, report_key)
    except Exception:
        return base
    if not reg:
        return base
    by_tf = {t[0]: t for t in base}
    order = [t[0] for t in base]
    for t in reg:
        if t[0] not in by_tf:
            order.append(t[0])
        by_tf[t[0]] = t
    return [by_tf[tf] for tf in order]


def known_report_keys(client=None, org_id=None):
    """The seeded report keys, plus any report_key a tenant introduced via the registry (so a brand-new
    report type appears in the mapping picker + readiness matrix). Pure list when no client/org_id."""
    keys = list(TARGET_FIELDS.keys())
    if client is not None and org_id:
        try:
            from app.modules.commcalc import target_registry
            for k in target_registry.registry_report_keys(client, org_id):
                if k not in keys:
                    keys.append(k)
        except Exception:
            pass
    return keys


def target_fields(report_key, client=None, org_id=None):
    """The canonical field registry for a report_key, as a list of dicts for the UI. When (client, org_id)
    are passed, the per-tenant registry is merged on top of the hard-coded defaults."""
    out = []
    for (tf, label, transform, required, default_src, aliases) in _registry_overlay(report_key, client, org_id):
        out.append({"target_field": tf, "label": label, "transform": transform,
                    "required": required, "default_source": default_src, "aliases": aliases})
    return out


def default_mapping(report_key, client=None, org_id=None):
    """Seed rows (target_field -> default source header) from the known layout, registry merged in."""
    return [{"target_field": tf, "source_header": default_src, "transform": transform, "priority": 100}
            for (tf, label, transform, required, default_src, aliases) in _registry_overlay(report_key, client, org_id)]


# ── rule loading + application ────────────────────────────────────────────────────────────────
def load_rules(client, org_id, report_key, carrier_id=None):
    """Effective mapping rules for (org, report_key, carrier): carrier-specific rules override the
    NULL/global rules for the same target_field. Returns a list of rule dicts."""
    q = (client.schema("commcalc").table("column_mapping").select("*")
         .eq("org_id", org_id).eq("report_key", report_key).eq("is_active", True))
    rows = q.execute().data or []
    by_field = {}
    # global (carrier_id NULL) first, then carrier-specific overrides win
    for r in sorted(rows, key=lambda x: (x.get("carrier_id") is not None, x.get("priority") or 100)):
        cid = r.get("carrier_id")
        if cid and carrier_id and cid != carrier_id:
            continue          # a different carrier's override — ignore
        if cid and not carrier_id:
            continue          # carrier-specific rule but no carrier context — ignore
        by_field[r["target_field"]] = r
    return list(by_field.values())


def apply_mapping(row, rules, base):
    """Build a target-table row from a source spreadsheet row using the mapping rules.
    Header match is case-insensitive. `base` carries org_id + period fields."""
    idx = {str(k).strip().lower(): v for k, v in row.items()}
    out = dict(base)
    for rule in rules:
        src = str(rule.get("source_header") or "").strip().lower()
        val = idx.get(src)
        out[rule["target_field"]] = apply_transform(val, rule.get("transform"))
    return out


def map_records(records, rules, base):
    """Map a list of source rows; drop rows that produced nothing but the base (org_id/period)."""
    org_id = base.get("org_id")
    keys = set(base.keys())
    out = []
    for r in records:
        mapped = apply_mapping(r, rules, base)
        if any(v for k, v in mapped.items() if k not in keys and v not in (None, "", 0, 0.0)):
            out.append(mapped)
    return out


# ── auto-suggest from an uploaded sample's headers ────────────────────────────────────────────
def suggest(headers, report_key, existing_rules=None, client=None, org_id=None):
    """For each canonical target field, suggest the best-matching header from the uploaded file.
    Confidence: 'mapped' (already configured) > 'exact' > 'alias' > 'fuzzy' > '' (no match).
    (client, org_id) merge the per-tenant registry so user-added fields are auto-suggested too."""
    existing = {r["target_field"]: r for r in (existing_rules or [])}
    hmap = {str(h).strip().lower(): str(h).strip() for h in headers if str(h).strip()}
    out = []
    for (tf, label, transform, required, default_src, aliases) in _registry_overlay(report_key, client, org_id):
        suggested, conf = "", ""
        if tf in existing and str(existing[tf].get("source_header") or "").strip().lower() in hmap:
            suggested, conf = hmap[str(existing[tf]["source_header"]).strip().lower()], "mapped"
        else:
            candidates = [default_src] + list(aliases) + [label]
            for c in candidates:                       # exact header match
                if str(c).strip().lower() in hmap:
                    suggested, conf = hmap[str(c).strip().lower()], ("exact" if c == default_src else "alias")
                    break
            if not suggested:                          # fuzzy: header contains the field token
                token = tf.replace("_", "")
                for low, orig in hmap.items():
                    if token and token in low.replace(" ", "").replace("_", ""):
                        suggested, conf = orig, "fuzzy"
                        break
        out.append({"target_field": tf, "label": label, "transform": transform, "required": required,
                    "suggested_source": suggested, "confidence": conf})
    return out


def now_iso():
    return datetime.now(timezone.utc).isoformat()
