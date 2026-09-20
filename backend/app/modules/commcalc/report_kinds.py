"""THE REPORT-KIND REGISTRY — one fact, one home (owner directives 2026-09-20; design §7). PURE.

OWNER, verbatim: *"for the layman we need to be more clear of what report we are trying to get them
uploaded — sales reports with imei and phone number; sales report with cost and selling price;
commission report … residual report; inventory at hand report; inventory aging report; Bill Payment
transactions report; X report; any other report which they feel is needed and the system to check
against what report it matches using intelligence gained by using all the reports."* And: *"it is
very important that we don't have extra file upload paths for a new tenant who does not need those
based on the carrier they pick … no patchwork, it should work as a design. Currently in the Verizon
tenant we have all the table uploads for B2B when it has been declared that the POS is not B2B, it
is RQ."*

THE CLASS (measured before building). Five surfaces offer uploads — the Upload page, the Upload
wizard, Email imports (filename patterns + the "apply standard" preset), the intake's 2.0 cards and
the POS-gated tile block of #234 — and only ONE of them dereferenced the tenant's declaration, by a
POS vendor name hardcoded in the page. The other four carried their own lists. This module is the one
home those five now READ:

  · `commcalc.report_kind`      (mig 1010) — every report kind the platform can take, with what it
                                 APPLIES TO (POS codes / carrier codes / any), who DEFINED it (house
                                 seed or a tenant's confirmed intake), how a layman recognises it, and
                                 the canonical fields that identify it (names from
                                 `column_mapping.TARGET_FIELDS` — no second alias list).
  · `commcalc.report_signature` (mig 1010) — the header fingerprints CONFIRMED at an intake, per org
                                 with a house copy, so the platform recognises a report it has seen.
                                 HEADER NAMES ONLY — never a cell value, a filename, a store, a rep
                                 or a customer string (pinned by the harness).
  · `tenant_declaration`        — THE ONE READER of what the tenant declared: POS from the
                                 `pos_system` vocabulary term (mig 953, tenant override > carrier
                                 preset) else the org's active `pos_profile` rows; carriers from the
                                 org's `commcalc.carrier` rows. No surface reads those rows itself.
  · `visible_kinds`             — THE ONE VISIBILITY FUNCTION (the `carrier_visible` / `posVisible`
                                 family, extended to report kinds): a row shows iff active AND its
                                 POS applies-to intersects the declared POS (or is any) AND its
                                 carrier applies-to intersects the declared carriers (or is any),
                                 with the super-admin `cap` override (`kind:<key>` show|hide, the
                                 EXISTING ui_label_override mechanism) recorded as provenance.
                                 `frontend/src/lib/carrier-scope.ts::reportKindsVisible` is its twin,
                                 clause for clause; both harnesses run the same cases.
  · `detect_report_kind`        — PURE detection over header names: a confirmed fingerprint first
                                 ("seen before as …, confirmed N times"), then ≥80% header overlap
                                 with a confirmed signature, then the kind's signature fields through
                                 the TARGET_FIELDS aliases + its layman recognisable columns. Two
                                 candidates within a small margin → ASK. Confirm, never reassign.

RULE TWO. Not one POS, carrier, processor or tenant name appears in this module. `HOUSE_KINDS` is
the CODE MIRROR of the 1010 seed (byte-equal, pinned by `harness_report_kinds.py`) so every surface
renders honestly before the migration is applied — and says `registry_ready:false`. The seed carries
POS / carrier CODES as data; this file carries none.

PRE-MIGRATION HONESTY. Without the table, `visible_kinds` runs over the mirror with the same rule.
Nothing is hidden by a fallback wider than the registry would be: if the declaration is unknown, the
POS-specific and carrier-specific kinds are NOT shown and the payload says why (`declaration.reasons`).
"""
import re
from datetime import datetime, timezone

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
MIGRATION = "1010_report_kind_registry.sql"
TABLE = "report_kind"
SIGNATURE_TABLE = "report_signature"
DEFINED_BY = ("house", "tenant")
STATEMENT_TYPES = ("commission", "residual")
# Where a kind LANDS = the intake's source kinds (onboarding_intake.SOURCE_KINDS) plus three the
# intake does not take: a custom-import sheet (self-serve capture, mig 099), a report registered
# through the connector registry (report_definitions, uploaded by its legacy route), a module page.
LANDINGS = ("sales", "pos", "inventory", "commission", "x_report", "merchant_payments", "bill_payments", "other",
            "custom_import", "carrier_report", "module")
INTAKE_LANDINGS = ("sales", "pos", "inventory", "commission", "x_report", "merchant_payments", "bill_payments", "other")
CAP_PREFIX = "kind:"                 # ui_label_override scope 'cap' key namespace (beside carrier:/pos:)
SURFACES = ("intake", "upload", "wizard", "email_imports", "tiles")
# Detection thresholds (design: exact fingerprint > ≥80% overlap > signature presence; ask on a tie).
OVERLAP_MIN = 0.80
SIGNAL_MIN = 0.50                    # at least half a kind's signals present before it is a candidate
ASK_MARGIN = 0.15                    # two candidates closer than this → ask with both evidences
PROV_HOUSE = "house default"
PROV_OVERRIDE = "your override"
PROV_CONFIRMED = "confirmed by you"
PROV_TENANT_DEFINED = "defined by a tenant on this carrier"
PROV_WIDENED = "widened by super-admin"
PROV_HIDDEN = "hidden by override"

_COLS = ("key", "label", "what_in_it", "recognisable_columns", "source_hint", "applies_to_pos",
         "applies_to_carrier", "defined_by", "statement_type", "landing", "layout", "signature_fields",
         "requires_columns", "excludes_columns", "upload_types", "custom_sheet_label", "sort_order", "is_active")


def _k(**kw):
    row = {"recognisable_columns": [], "source_hint": None, "applies_to_pos": [], "applies_to_carrier": [],
           "defined_by": "house", "statement_type": None, "layout": None, "signature_fields": [],
           "requires_columns": [], "excludes_columns": [], "upload_types": [], "custom_sheet_label": None,
           "is_active": True}
    row.update(kw)
    return row


# ── THE HOUSE SEED'S MIRROR (byte-equal to mig 1010 — the harness parses the SQL and compares) ────
# The layman cards the owner asked for come first (sort 10–110); the rows after them are the
# reports the Upload page / wizard already offered by hand, now data: each carries the legacy route
# key it is uploaded through (`upload_types`) and the carrier or POS code it applies to.
HOUSE_KINDS = [
    _k(key="sales_imei_phone", label="Sales report with IMEI and phone number",
       what_in_it="Every sale line your POS rang up, with the device IMEI / serial and the customer's mobile number on the line.",
       recognisable_columns=["IMEI", "Serial", "Mobile Number", "Trans ID", "Ext Price", "Salesperson"],
       source_hint="export from your POS", landing="sales", layout="sales",
       signature_fields=["trans_id", "mdn", "serial_1", "ext_price"], upload_types=["daily_sales", "sales"], sort_order=10),
    _k(key="sales_cost_price", label="Sales report with cost and selling price",
       what_in_it="One row per invoice line with the product SKU, what it cost you and what it sold for (gross profit).",
       recognisable_columns=["Invoice #", "Product SKU", "Total Price", "Total Cost", "Gross Profit", "Sold By"],
       source_hint="export from your POS", landing="pos", layout="pos_product_sales",
       signature_fields=["trans_id", "sku", "ext_price", "total_cost", "gp"], sort_order=20),
    _k(key="commission_statement", label="Commission report from the carrier",
       what_in_it="The carrier's statement of what it paid you for activations, upgrades and spiffs — one line per payment, with its label.",
       recognisable_columns=["Gross", "Report Section", "Report SubSection", "AgentSSOID", "Master Service Date", "Commission"],
       source_hint="from the carrier portal", landing="commission", layout="commission_ledger", statement_type="commission",
       signature_fields=["raw_amount", "product_name", "trans_date"], excludes_columns=["Residual"], sort_order=30),
    _k(key="residual_statement", label="Residual report from the carrier",
       what_in_it="The carrier's monthly residual statement — recurring pay per line or account for the service month.",
       recognisable_columns=["Residual", "Service Month", "MDN", "Plan", "Account"],
       source_hint="from the carrier portal", landing="commission", layout="carrier_commission", statement_type="residual",
       signature_fields=["residual", "mdn", "account_id"], requires_columns=["Residual"], sort_order=40),
    _k(key="inventory_on_hand", label="Inventory on hand",
       what_in_it="What is in stock right now at each store: one row per unit with SKU, IMEI / serial and cost.",
       recognisable_columns=["Product SKU", "Tracking #", "Location", "Unit Cost", "Quantity", "Status"],
       source_hint="export from your POS", landing="inventory", layout="pos_inventory_listing",
       signature_fields=["sku", "imei", "store", "unit_cost"], excludes_columns=["Received", "Days in Stock", "Age"], sort_order=50),
    _k(key="inventory_aging", label="Inventory aging",
       what_in_it="The on-hand listing with a received date or days-in-stock per unit, so old stock shows its age.",
       recognisable_columns=["Received", "Days in Stock", "Age", "SKU", "Cost", "Location"],
       source_hint="export from your POS", landing="inventory", layout="pos_inventory_listing",
       signature_fields=["sku", "unit_cost"], requires_columns=["Received", "Days in Stock", "Age"],
       upload_types=["inventory_aging"], sort_order=60),
    _k(key="bill_payments_pos", label="Bill payment transactions (from your POS)",
       what_in_it="Every bill payment your stores took over the counter, as your POS reports it.",
       recognisable_columns=["Bill Payment", "Payment Amount", "Carrier", "Phone Number", "Store"],
       source_hint="export from your POS", applies_to_pos=["b2bsoft"], landing="custom_import",
       custom_sheet_label="Bill Payments", sort_order=70),
    _k(key="bill_payments_carrier", label="Bill payment report from the carrier's processor",
       what_in_it="The processor's list of bill payments taken on your account — the feed the bill-pay coverage check reads.",
       recognisable_columns=["Order Type", "Retail Cost", "Account ID", "Product Name", "Order Number"],
       source_hint="from the carrier portal", landing="bill_payments", layout="ma_daily_tx",
       signature_fields=["account_id", "order_type", "retail_cost"], sort_order=80),
    _k(key="x_report", label="Cash register / X-report",
       what_in_it="Your POS's end-of-day tender summary per store: cash, card and other takings for the day.",
       recognisable_columns=["Cash", "Credit", "Tender", "Register", "Total"],
       source_hint="export from your POS", landing="x_report", upload_types=["x_report"], sort_order=90),
    _k(key="merchant_settlement", label="Credit-card (merchant) report",
       what_in_it="Your card processor's settlement export: per merchant, per business day, per card brand, with fees.",
       recognisable_columns=["Merchant", "Settlement", "Card Type", "Fees", "Net Amount", "Batch"],
       source_hint="from the card processor's portal", landing="merchant_payments", sort_order=100),
    _k(key="something_else", label="Something else",
       what_in_it="Any other report you have. It is recorded as received with its columns and row count, and the file is kept.",
       source_hint="wherever it comes from", landing="other", sort_order=110),
    # ── the reports the Upload page / wizard already offered — now rows (upload_types = legacy route key) ──
    _k(key="payment_detail", label="Commission payment detail (processor)",
       what_in_it="The payment processor's commission payment detail for the period.",
       source_hint="from the processor portal", applies_to_carrier=["boost"], landing="carrier_report",
       upload_types=["payment_detail"], sort_order=200),
    _k(key="mi_report", label="MI & ATU report",
       what_in_it="Monthly incentive and ATU subscriber details.",
       source_hint="from the processor portal", applies_to_carrier=["boost"], landing="carrier_report",
       upload_types=["mi_report"], sort_order=210),
    _k(key="comp_report", label="Comprehensive comp report",
       what_in_it="Carrier store-level rebates and MDF for the period.",
       source_hint="from the processor portal", applies_to_carrier=["boost"], landing="carrier_report",
       upload_types=["comp_report"], sort_order=220),
    _k(key="dlar_rep", label="Rep KPI report (carrier portal)",
       what_in_it="Per-rep KPI figures from the carrier's KPI portal.",
       source_hint="from the carrier portal", landing="carrier_report", upload_types=["dlar_rep"], sort_order=230),
    _k(key="dlar_store", label="Store KPI report (carrier portal)",
       what_in_it="Store-level KPI figures from the carrier's KPI portal.",
       source_hint="from the carrier portal", landing="carrier_report", upload_types=["dlar_store"], sort_order=240),
    _k(key="catalog", label="Product catalog",
       what_in_it="Product catalog with cost and category.",
       source_hint="export from your POS", landing="carrier_report", upload_types=["catalog"], sort_order=250),
    _k(key="master_cats", label="Payment categories",
       what_in_it="Payment type to category mapping.",
       source_hint="export from your POS", landing="carrier_report", upload_types=["master_cats"], sort_order=260),
    _k(key="ma_commission", label="Marketplace commission details",
       what_in_it="Per-activation commission detail from the carrier marketplace feed.",
       source_hint="from the carrier portal", applies_to_carrier=["total"], landing="carrier_report",
       upload_types=["ma_commission"], sort_order=270),
    _k(key="ma_daily_tx", label="Marketplace daily transactions",
       what_in_it="Daily airtime / top-up transactions from the carrier marketplace feed.",
       source_hint="from the carrier portal", applies_to_carrier=["total"], landing="carrier_report",
       upload_types=["ma_daily_tx"], sort_order=280),
    _k(key="ma_fulfillment", label="Marketplace handset fulfillment",
       what_in_it="Handset fulfillment orders from the carrier marketplace feed.",
       source_hint="from the carrier portal", applies_to_carrier=["total"], landing="carrier_report",
       upload_types=["ma_fulfillment"], sort_order=290),
    _k(key="hotsheet", label="Pricing hotsheet",
       what_in_it="Carrier promo pricing by device, with its effective date.",
       source_hint="from the carrier portal", landing="module", upload_types=["hotsheet"], sort_order=300),
    _k(key="vip_workbook", label="Distributor workbook",
       what_in_it="The distributor scraper workbook (invoices, lines, devices).",
       source_hint="from the distributor portal", applies_to_carrier=["boost"], landing="module",
       upload_types=["vip_workbook"], sort_order=310),
    _k(key="asset_ledger", label="Asset ledger",
       what_in_it="The distributor's asset-lending ledger.",
       source_hint="from the distributor portal", applies_to_carrier=["boost"], landing="module",
       upload_types=["asset_ledger"], sort_order=320),
    _k(key="daily_closing", label="Daily closing sheet",
       what_in_it="The envelopes export — one row per rep per day.",
       source_hint="from your closing form", landing="module", upload_types=["daily_closing"], sort_order=330),
    _k(key="pos_activation_details", label="Activation details (from your POS)",
       what_in_it="One row per activation, as your POS reports it — drives the store activation counts.",
       source_hint="export from your POS", applies_to_pos=["b2bsoft"], landing="custom_import",
       custom_sheet_label="Activation Details", sort_order=340),
    _k(key="pos_sales_by_product", label="Sales by product (from your POS)",
       what_in_it="Accessory sales by department, as your POS reports it.",
       source_hint="export from your POS", applies_to_pos=["b2bsoft"], landing="custom_import",
       custom_sheet_label="Sales by Product", sort_order=350),
    _k(key="pos_inventory_recon", label="Inventory recon (structured entry)",
       what_in_it="On-hand inventory by store and category — structured entry and recon on its own page.",
       source_hint="export from your POS", applies_to_pos=["b2bsoft"], applies_to_carrier=["boost"], landing="module",
       upload_types=["b2b_inventory"], sort_order=360),
]
HOUSE_KEYS = [r["key"] for r in HOUSE_KINDS]


# ── helpers ───────────────────────────────────────────────────────────────────────────────────────
def _s(v):
    return "" if v is None else str(v).strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def code(v):
    """Case- and punctuation-insensitive key for a POS or carrier code ('My POS' → 'mypos').
    The twin of carrier-scope.ts::posSquash — one squash on both sides, so the two gates agree."""
    return re.sub(r"[^a-z0-9]+", "", _s(v).lower())


def norm_header(h):
    """A header name normalised for matching: lower-case, punctuation collapsed to one space."""
    return re.sub(r"[^a-z0-9#]+", " ", _s(h).lower()).strip()


def header_fingerprint(headers):
    """The normalised, ORDERED header list joined by '|' — the identity of a layout. Header names
    only: nothing from a data row can reach this function's input by construction (callers pass
    `shape['headers']`), and the harness pins that no signature row carries anything else."""
    return "|".join(norm_header(h) for h in (headers or []) if _s(h))


def _list(v):
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{") and s.endswith("}"):          # a postgres text[] literal
            s = s[1:-1]
            return [x.strip().strip('"') for x in s.split(",") if x.strip()]
        return [s] if s else []
    return [x for x in v if _s(x)]


def normalise_row(row):
    """A registry row as the engine reads it — arrays as lists, codes squashed, defaults filled."""
    out = _k()
    out.update({k: row.get(k) for k in _COLS if k in row})
    out["org_id"] = row.get("org_id") or HOUSE_ORG
    out["defined_by_org"] = row.get("defined_by_org")
    for k in ("recognisable_columns", "signature_fields", "requires_columns", "excludes_columns", "upload_types"):
        out[k] = _list(out.get(k))
    out["applies_to_pos"] = [code(c) for c in _list(out.get("applies_to_pos")) if code(c)]
    out["applies_to_carrier"] = [code(c) for c in _list(out.get("applies_to_carrier")) if code(c)]
    out["is_active"] = out.get("is_active") is not False
    out["sort_order"] = int(out.get("sort_order") or 100)
    out["defined_by"] = out.get("defined_by") if out.get("defined_by") in DEFINED_BY else "house"
    return out


def merge_rows(rows, org_id):
    """House rows + this org's rows merged PER KEY — the tenant's row overrides the house row (the
    mig-207 shape). A row from a THIRD org is never read (the loader does not fetch one)."""
    house, own = {}, {}
    for r in rows or []:
        n = normalise_row(r)
        if _s(n["org_id"]) == HOUSE_ORG:
            house[n["key"]] = n
        elif _s(n["org_id"]) == _s(org_id):
            own[n["key"]] = n
        # any other org's row is not this tenant's business — ignored
    out = []
    for k in sorted(set(house) | set(own), key=lambda kk: ((own.get(kk) or house.get(kk))["sort_order"], kk)):
        n = dict(own.get(k) or house.get(k))
        n["_source"] = "override" if k in own and k in house else ("tenant" if k in own else "house")
        out.append(n)
    return out


def house_mirror():
    return [dict(normalise_row({**r, "org_id": HOUSE_ORG}), _source="house") for r in HOUSE_KINDS]


# ── THE ONE DECLARATION READER ────────────────────────────────────────────────────────────────────
def declaration_from(term_label, term_source, pos_profile_rows, carrier_rows, carrier_code_fn=None):
    """PURE: what this tenant declared, from where.

    POS: the `pos_system` vocabulary term (mig 953 — the Stage-1 answer: tenant override, else the
    carrier preset) is THE declaration when it resolved; else the org's active `pos_profile` rows
    (applying a POS standard is a declaration too); else unknown — and unknown HIDES the POS-specific
    kinds (design §7: show less and say why), never widens.
    Carriers: the org's `commcalc.carrier` rows, squashed through the injected normaliser."""
    reasons = []
    pos, pos_source = [], "unknown"
    if _s(term_label) and (term_source or "").startswith("report_term"):
        pos, pos_source = [code(term_label)], term_source
    else:
        keys = [code(r.get("pos_key")) for r in (pos_profile_rows or []) if r.get("is_active", True) and code(r.get("pos_key"))]
        if keys:
            pos, pos_source = sorted(set(keys)), "pos_profile"
    if not pos:
        reasons.append("no POS declared — POS-specific report kinds are not offered until the POS is set "
                       "(Stage 1, or the pos_system term under Admin → Labels)")
    norm = carrier_code_fn or (lambda v: code(v))
    carriers = sorted({code(norm(_s(c.get("code")) or _s(c.get("name")))) for c in (carrier_rows or [])
                       if _s(c.get("code")) or _s(c.get("name"))})
    carriers = [c for c in carriers if c]
    if not carriers:
        reasons.append("no carrier declared — carrier-specific report kinds are not offered until a carrier row exists")
    return {"pos": pos, "pos_source": pos_source, "carriers": carriers, "carrier_source": "carrier_rows",
            "reasons": reasons}


def tenant_declaration(client, org_id):
    """I/O wrapper — THE ONE PLACE the platform reads the tenant's declaration for report gating.
    `harness_report_kind_lock.py` §C pins that no other backend function reads `pos_system` for
    visibility. Every read is org-scoped; every failure degrades to 'unknown', never to 'any'."""
    from app.modules.commcalc import report_labels as _rl
    try:
        label, source = _rl.term_from_payload(_rl.load_report_labels(client, org_id), "pos_system", "")
    except Exception:
        label, source = "", "neutral_default"
    try:
        prof = (client.schema("commcalc").table("pos_profile").select("pos_key,is_active")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        prof = []
    try:
        crows = (client.schema("commcalc").table("carrier").select("id,name,code,is_default")
                 .eq("org_id", org_id).execute().data) or []
    except Exception:
        crows = []
    return declaration_from(label, source, prof, crows, _rl.normalize_carrier_code)


# ── THE ONE VISIBILITY FUNCTION ───────────────────────────────────────────────────────────────────
def applies(row, declaration):
    """Does a row's applies-to intersect the declaration? Empty applies-to = any."""
    pos_ok = not row["applies_to_pos"] or bool(set(row["applies_to_pos"]) & set(declaration.get("pos") or []))
    car_ok = not row["applies_to_carrier"] or bool(set(row["applies_to_carrier"]) & set(declaration.get("carriers") or []))
    return pos_ok and car_ok


def cap_override(caps, key):
    v = (caps or {}).get(CAP_PREFIX + key)
    return True if v is True else (False if v is False else None)


def visible_kinds(rows, declaration, caps=None, org_id=None, confirmations=None):
    """Rows visible to a tenant, each with its provenance — THE rule every surface dereferences.

      · inactive → never;
      · cap override `kind:<key>` hide → hidden (recorded); show → shown as 'widened by super-admin';
      · else shown iff applies(row, declaration).
    `rows` are already merged per key (merge_rows). `confirmations` = {kind_key: N} from this org's
    own signature rows, for the "confirmed by you N times" provenance."""
    out = []
    conf = confirmations or {}
    for r in rows or []:
        if not r["is_active"]:
            continue
        ov = cap_override(caps, r["key"])
        if ov is False:
            continue
        by_rule = applies(r, declaration)
        if not by_rule and ov is not True:
            continue
        if ov is True and not by_rule:
            prov = PROV_WIDENED
        elif r.get("_source") in ("override", "tenant"):
            prov = PROV_OVERRIDE
        elif r["defined_by"] == "tenant" and _s(r.get("defined_by_org")) != _s(org_id):
            prov = PROV_TENANT_DEFINED
        else:
            prov = PROV_HOUSE
        n = conf.get(r["key"]) or 0
        row = dict(r)
        row["provenance"] = prov
        row["confirmations"] = n
        row["provenance_text"] = prov + (f" · {PROV_CONFIRMED} {n} time{'s' if n != 1 else ''}" if n else "")
        out.append(row)
    return out


def hidden_kinds(rows, declaration, caps=None):
    """The rows NOT shown and why — so a surface can say 'N kinds hidden because …' instead of nothing."""
    out = []
    for r in rows or []:
        if not r["is_active"]:
            out.append({"key": r["key"], "label": r["label"], "why": "inactive"})
            continue
        ov = cap_override(caps, r["key"])
        if ov is False:
            out.append({"key": r["key"], "label": r["label"], "why": PROV_HIDDEN})
        elif ov is not True and not applies(r, declaration):
            why = []
            if r["applies_to_pos"] and not (set(r["applies_to_pos"]) & set(declaration.get("pos") or [])):
                why.append("applies to POS " + "/".join(r["applies_to_pos"]) + " — you declared " + ("/".join(declaration.get("pos") or []) or "no POS"))
            if r["applies_to_carrier"] and not (set(r["applies_to_carrier"]) & set(declaration.get("carriers") or [])):
                why.append("applies to carrier " + "/".join(r["applies_to_carrier"]) + " — you declared " + ("/".join(declaration.get("carriers") or []) or "no carrier"))
            out.append({"key": r["key"], "label": r["label"], "why": "; ".join(why)})
    return out


def for_surface(visible, surface):
    """What each surface renders, from the ONE visible set — no surface keeps a list of its own."""
    if surface == "intake":
        return [r for r in visible if r["landing"] in INTAKE_LANDINGS]
    if surface in ("upload", "tiles"):
        return [r for r in visible if r["upload_types"] or r.get("custom_sheet_label")]
    if surface == "wizard":
        return [r for r in visible if r["upload_types"]]
    if surface == "email_imports":
        return [r for r in visible if r["upload_types"] or r.get("custom_sheet_label")]
    return list(visible)


def upload_types_of(visible):
    out = []
    for r in visible:
        for u in r["upload_types"]:
            if u not in out:
                out.append(u)
    return out


def filename_rules_for(profile_rules, visible):
    """The declared POS standard's filename rules, restricted to upload types a visible kind owns —
    a rule that routes to a hidden kind is a path the tenant cannot use, so it is not offered."""
    allowed = set(upload_types_of(visible))
    out = []
    for r in profile_rules or []:
        if not isinstance(r, dict):
            continue
        ut = _s(r.get("upload_type"))
        if ut and ut in allowed and _s(r.get("pattern")):
            out.append({"pattern": _s(r.get("pattern")), "upload_type": ut, "note": _s(r.get("note")) or None})
    return out


# ── DETECTION ─────────────────────────────────────────────────────────────────────────────────────
def _field_names(layout, field, target_fields):
    """Every spelling TARGET_FIELDS knows for a canonical field of a layout — the ONE alias source."""
    for t in (target_fields or {}).get(layout) or []:
        if t[0] == field:
            return [t[4]] + list(t[5] or [])
    return []


def _hit(names, headers_norm):
    for n in names:
        nn = norm_header(n)
        if not nn:
            continue
        if nn in headers_norm:
            return True
        if any(nn in h for h in headers_norm):     # 'received' matches 'received date'
            return True
    return False


def kind_signals(row, headers, target_fields):
    """The evidence a kind's registry row asks for, and which of it the headers carry.
    signals = signature fields (through the layout's TARGET_FIELDS aliases) ∪ recognisable columns."""
    hn = [norm_header(h) for h in headers if _s(h)]
    hits, misses = [], []
    for f in row.get("signature_fields") or []:
        names = _field_names(row.get("layout"), f, target_fields)
        (hits if _hit(names, hn) else misses).append(f"field {f}")
    for c in row.get("recognisable_columns") or []:
        (hits if _hit([c], hn) else misses).append(f"column {c!r}")
    req = row.get("requires_columns") or []
    req_ok = (not req) or _hit(req, hn)
    exc = row.get("excludes_columns") or []
    exc_ok = (not exc) or not _hit(exc, hn)
    return {"hits": hits, "misses": misses, "requires_ok": req_ok, "excludes_ok": exc_ok}


def _overlap(a_fp, b_fp):
    a, b = set(a_fp.split("|")) - {""}, set(b_fp.split("|")) - {""}
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def signature_index(signature_rows):
    """{fingerprint: [rows]} — a fingerprint confirmed under DIFFERENT kinds is AMBIGUOUS."""
    idx = {}
    for s in signature_rows or []:
        fp = _s(s.get("fingerprint"))
        if fp:
            idx.setdefault(fp, []).append(s)
    return idx


def detect_report_kind(headers, registry_rows, signature_rows=None, target_fields=None):
    """PURE: ranked [(key, confidence, evidence)] for a header list.

    1. an EXACT confirmed fingerprint → 1.0 ("seen before as <kind>, confirmed N times"); the same
       fingerprint confirmed under two kinds → both at 1.0 with the ambiguity stated (→ ask);
    2. ≥80% header overlap with a confirmed signature → 0.8–0.99, evidence says which;
    3. the kind's own signals (signature fields via TARGET_FIELDS aliases + recognisable columns),
       requires / excludes columns honoured → fraction of signals present (≥ SIGNAL_MIN to count).
    A kind whose `requires_columns` are absent, or whose `excludes_columns` are present, is dropped."""
    if target_fields is None:
        from app.modules.commcalc import column_mapping as _cm
        # every layout the registry rows name, through the ONE seed-field source (TARGET_FIELDS + the
        # derived MA layouts) — no layout is listed here
        target_fields = {r["layout"]: _cm._base_fields(r["layout"]) for r in registry_rows or [] if r.get("layout")}
    fp = header_fingerprint(headers)
    by_key = {r["key"]: r for r in registry_rows or []}
    scores = {}

    def put(key, conf, ev):
        if key not in by_key:
            return
        cur = scores.get(key)
        if cur is None or conf > cur[0]:
            scores[key] = (conf, ev)

    idx = signature_index(signature_rows)
    exact = idx.get(fp) or []
    kinds_exact = {}
    for s in exact:
        # the house copy already carries every tenant's confirmations of this layout, and an org row
        # is a subset of it — so the count is the MAX over rows, never a sum of the two copies
        k = _s(s.get("report_kind_key"))
        kinds_exact[k] = max(kinds_exact.get(k, 0), int(s.get("confirmations") or 1))
    for k, n in kinds_exact.items():
        amb = " — AMBIGUOUS: the same layout was confirmed as " + " and ".join(sorted(kinds_exact)) if len(kinds_exact) > 1 else ""
        put(k, 1.0, [f"seen before as {by_key.get(k, {}).get('label') or k}, confirmed {n} time{'s' if n != 1 else ''}{amb}"])
    if fp and not exact:
        for sfp, rows in idx.items():
            ov = _overlap(fp, sfp)
            if ov >= OVERLAP_MIN:
                for s in rows:
                    k = _s(s.get("report_kind_key"))
                    put(k, min(0.99, 0.8 + (ov - OVERLAP_MIN)), [f"{int(round(ov * 100))}% of the headers match a layout confirmed as {by_key.get(k, {}).get('label') or k}"])
    for k, r in by_key.items():
        if r["landing"] == "other" or not (r.get("signature_fields") or r.get("recognisable_columns")):
            continue
        sig = kind_signals(r, headers, target_fields)
        if not sig["requires_ok"] or not sig["excludes_ok"]:
            continue
        n = len(sig["hits"]) + len(sig["misses"])
        if n == 0:
            continue
        frac = len(sig["hits"]) / n
        if frac >= SIGNAL_MIN:
            put(k, round(0.4 + 0.39 * frac, 3), [f"has {', '.join(sig['hits'])}"] + ([f"missing {', '.join(sig['misses'])}"] if sig["misses"] else []))
    ranked = sorted(((k, c, ev) for k, (c, ev) in scores.items()), key=lambda t: (-t[1], by_key[t[0]]["sort_order"], t[0]))
    return ranked


def decide(ranked):
    """confirm | ask | none — never a silent assignment."""
    if not ranked:
        return {"mode": "none", "candidates": []}
    top = ranked[0][1]
    close = [r for r in ranked if top - r[1] < ASK_MARGIN]
    if len(close) > 1:
        return {"mode": "ask", "candidates": close}
    return {"mode": "confirm", "candidates": [ranked[0]]}


# ── WHICH KIND A CONFIRMED INTAKE IS (for the signature it writes) ────────────────────────────────
def kind_key_for(rows, landing, layout=None, statement_type=None, headers=None, chosen=None, target_fields=None):
    """The registry key a confirmed intake instance belongs to: the card the person chose if it is a
    row of that landing; else the row of that landing (+ statement type for commission; + layout;
    + requires/excludes over the headers for on-hand vs aging); else None (nothing learned)."""
    rows = [r for r in rows or [] if r["landing"] == landing]
    if chosen and any(r["key"] == chosen for r in rows):
        return chosen
    st = _s(statement_type).lower()
    if landing == "commission":
        want = "residual" if "residual" in st else "commission"
        rows = [r for r in rows if (r.get("statement_type") or "commission") == want] or rows
    if layout:
        rows = [r for r in rows if not r.get("layout") or r["layout"] == layout] or rows
    if headers and len(rows) > 1:
        hn_rows = []
        for r in rows:
            sig = kind_signals(r, headers, target_fields or {})
            if sig["requires_ok"] and sig["excludes_ok"]:
                hn_rows.append(r)
        rows = hn_rows or rows
    return rows[0]["key"] if rows else None


def signature_row(org_id, headers, kind_key, statement_type=None, layout=None, house_copy=False):
    """The row `learn` writes — HEADER NAMES ONLY. The caller passes the detected header list; the
    harness asserts no cell value, filename, store, rep or customer string can be in this row."""
    return {"org_id": org_id, "fingerprint": header_fingerprint(headers), "report_kind_key": kind_key,
            "statement_type": _s(statement_type) or None, "layout": _s(layout) or None,
            "header_count": len([h for h in headers or [] if _s(h)]), "house_copy": bool(house_copy)}


def learn_signature(client, org_id, headers, kind_key, statement_type=None, layout=None, house_copy=True):
    """Upsert the org's signature row (+1 confirmation) and, by default, the house copy — so the
    NEXT tenant's file is recognised. Degrades (returns the reason) before mig 1010; never raises."""
    if not kind_key or not header_fingerprint(headers):
        return {"learned": False, "reason": "no kind or no headers"}
    written = []
    try:
        for oid, hc in ((org_id, False), (HOUSE_ORG, True)) if house_copy else ((org_id, False),):
            row = signature_row(oid, headers, kind_key, statement_type, layout, house_copy=hc)
            got = (client.schema("commcalc").table(SIGNATURE_TABLE).select("id,confirmations")
                   .eq("org_id", oid).eq("fingerprint", row["fingerprint"]).eq("report_kind_key", kind_key)
                   .limit(1).execute().data) or []
            ts = now_iso()
            if got:
                client.schema("commcalc").table(SIGNATURE_TABLE).update(
                    {"confirmations": int(got[0].get("confirmations") or 0) + 1, "last_confirmed_at": ts,
                     "statement_type": row["statement_type"], "layout": row["layout"]}).eq("id", got[0]["id"]).execute()
            else:
                client.schema("commcalc").table(SIGNATURE_TABLE).insert(
                    {**row, "confirmations": 1, "first_confirmed_at": ts, "last_confirmed_at": ts}).execute()
            written.append(oid)
        return {"learned": True, "orgs": written, "kind": kind_key}
    except Exception as e:                       # pragma: no cover - pre-migration
        return {"learned": False, "reason": f"{MIGRATION} not applied ({str(e)[:80]})"}


def tenant_kind_row(org_id, name, landing="other", applies_to_pos=(), applies_to_carrier=(), headers=None):
    """A kind DEFINED BY A TENANT through 'Something else' — written at the HOUSE org so the next
    tenant on that carrier / POS is offered it (design §7 rule 3). Header names become its
    recognisable columns; no value, filename or identity string is stored."""
    key = re.sub(r"[^a-z0-9]+", "_", _s(name).lower()).strip("_")[:60]
    if not key:
        return None
    return {"org_id": HOUSE_ORG, "key": key, "label": _s(name)[:120],
            "what_in_it": f"Defined by a tenant's intake as '{_s(name)[:80]}'.",
            "recognisable_columns": [_s(h)[:60] for h in (headers or []) if _s(h)][:12],
            "source_hint": None, "applies_to_pos": [code(c) for c in applies_to_pos if code(c)],
            "applies_to_carrier": [code(c) for c in applies_to_carrier if code(c)],
            "defined_by": "tenant", "defined_by_org": org_id, "statement_type": None, "landing": landing,
            "layout": None, "signature_fields": [], "requires_columns": [], "excludes_columns": [],
            "upload_types": [], "custom_sheet_label": None, "sort_order": 500, "is_active": True}


def define_kind(client, org_id, name, applies_to_pos=(), applies_to_carrier=(), headers=None):
    """Insert the tenant-defined kind at the house org if no row has that key yet (never overwrite a
    house seed or another tenant's definition). Degrades before mig 1010."""
    row = tenant_kind_row(org_id, name, applies_to_pos=applies_to_pos, applies_to_carrier=applies_to_carrier, headers=headers)
    if not row:
        return {"defined": False, "reason": "no name"}
    try:
        have = (client.schema("commcalc").table(TABLE).select("id,defined_by")
                .eq("org_id", HOUSE_ORG).eq("key", row["key"]).limit(1).execute().data) or []
        if have:
            return {"defined": False, "reason": "already defined", "key": row["key"]}
        client.schema("commcalc").table(TABLE).insert({**row, "updated_at": now_iso()}).execute()
        return {"defined": True, "key": row["key"]}
    except Exception as e:                       # pragma: no cover - pre-migration
        return {"defined": False, "reason": f"{MIGRATION} not applied ({str(e)[:80]})"}


# ── LOADERS (org-scoped; house + this org only) ───────────────────────────────────────────────────
def load_registry(client, org_id):
    """(rows merged per key, ready) — house rows + this org's rows from mig 1010, else the mirror."""
    try:
        rows = (client.schema("commcalc").table(TABLE).select("*")
                .in_("org_id", sorted({HOUSE_ORG, org_id})).execute().data) or []
        if not rows:
            return house_mirror(), False
        return merge_rows(rows, org_id), True
    except Exception:
        return house_mirror(), False


def load_signatures(client, org_id):
    """This org's confirmed signatures + the house copies (other tenants' confirmations, header
    names only) — what detection consults. Org-scoped to {org, house}."""
    try:
        return (client.schema("commcalc").table(SIGNATURE_TABLE).select("*")
                .in_("org_id", sorted({HOUSE_ORG, org_id})).execute().data) or []
    except Exception:
        return []


def confirmations_by_kind(signature_rows, org_id):
    out = {}
    for s in signature_rows or []:
        if _s(s.get("org_id")) == _s(org_id) and not s.get("house_copy"):
            k = _s(s.get("report_kind_key"))
            out[k] = out.get(k, 0) + int(s.get("confirmations") or 0)
    return out


def payload(rows, ready, declaration, caps, signature_rows, org_id, profile_rules=None, standard=None):
    """The GET /commcalc/report-kinds body — everything a surface needs, computed ONCE here."""
    conf = confirmations_by_kind(signature_rows, org_id)
    vis = visible_kinds(rows, declaration, caps, org_id, conf)
    public = []
    for r in vis:
        public.append({k: r.get(k) for k in _COLS + ("provenance", "provenance_text", "confirmations", "defined_by_org")})
    caps_kind = {k: v for k, v in (caps or {}).items() if str(k).startswith(CAP_PREFIX)}
    return {
        "registry_ready": ready, "migration": MIGRATION,
        "declaration": declaration,
        "kinds": public,
        "hidden": hidden_kinds(rows, declaration, caps),
        "surfaces": {s: [r["key"] for r in for_surface(vis, s)] for s in SURFACES},
        "upload_types": upload_types_of(vis),
        "filename_rules": filename_rules_for(profile_rules, vis),
        "standard": standard,
        "caps": caps_kind,
        "all_keys": [{"key": r["key"], "label": r["label"], "applies_to_pos": r["applies_to_pos"],
                      "applies_to_carrier": r["applies_to_carrier"]} for r in rows if r["is_active"]],
    }
