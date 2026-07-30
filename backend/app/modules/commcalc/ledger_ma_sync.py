"""REFRESH the canonical Commission Ledger from the raw MA tables that ALREADY flow automatically —
`commcalc.raw_ma_daily_tx` and `commcalc.raw_ma_commission` (migration 083, fed by VidaPay/Total Access
pulls + the manual MA upload) — instead of waiting on a hand-uploaded file per period.

WHY THIS EXISTS (owner directive 2026-07-30: "commission ledger has stale data and should be updated from
ma commission and ma tx"). The ledger's ONLY ingest today is `POST /commission-ledger/import`: a human
uploads the carrier's file for a period. The MA data behind that file is already in the database and keeps
arriving. So the ledger goes stale silently while its source is current.

THE DESIGN RULE THAT MAKES THIS SAFE: this module does NOT re-implement classification, bucket booking,
month parsing or summarisation, and it does NOT invent a second mapping system. It builds, per raw row, the
**pseudo file row** that the file-import path would have received — a dict keyed by the SOURCE HEADERS the
tenant's own ledger column-mapping already names — and then hands it to the exact same two calls the file
import makes:

    column_mapping.apply_mapping(pseudo_row, hdr_rules, base)  ->  commission_ledger.build_row(src, base, cat_rules)

That is why the differential proof holds: identical fixture data through either path produces identical
ledger rows. Anything that decides MONEY (which bucket, which sign, which month) stays in the tenant's
config — `commcalc.column_mapping` (header -> ledger field) and `commcalc.commission_category_map`
(label -> canonical bucket). Nothing here classifies.

HOW A RAW COLUMN IS FOUND (no new hard-coded carrier mapping — RULE TWO). Two config maps are COMPOSED:

    ledger field  <--(commcalc.column_mapping / TARGET_FIELDS default)--  source header
    source header --(report_pull spec / commcalc.manual_report_mapping)-> raw_ma_* column

Precedence per field mirrors `column_mapping.suggest()` exactly: exact header match ('mapped') > a
registry/default ALIAS ('alias') > the field-token fuzzy match ('fuzzy') > unresolved. Every field's
resolution AND its confidence are reported to the preview, so an unresolved field is a visible config gap
(fix it on Target Fields / the column mapping) rather than a silent NULL.

TWO SOURCE SHAPES:
  • 'row' (MA Daily Tx)      — one raw row == one ledger line; the signed amount is one column.
  • 'component' (MA Commission) — one raw row carries MANY payout amounts (device margin, rebate, the
    1st..6th month spiffs). Each configured component becomes ONE ledger line whose product label is the
    REPORT'S OWN HEADER for that column ("4th Month Spiff"), so the tenant's existing rules classify it and
    an unmatched label surfaces as 'other' instead of being guessed at here. The component set defaults to
    the SAME columns the residual/P&L surfaces already treat as dealer payable
    (`account.residual_subs._MA_COMPONENTS`) so the ledger and the residual roll-up can never disagree
    about what a Total/VidaPay dealer is paid. `mrc_net_discount` is deliberately NOT a component — it is
    the subscriber's plan price, not a payment to the dealer.

THE AMOUNT-COLUMN GUARD (an ID-like column must never be summed as dollars). `raw_ma_daily_tx` stores
`merchant_invoice` as NUMERIC even though it is an INVOICE NUMBER (`ma_upload.FIELD_LABELS` marks it
role='key'); reading it as money is a real, live bug class on the residual surfaces. So:
  1. any column whose registered role is identifying (key/store/device/date/status) is REFUSED as an
     amount — the source contributes zero rows and says why, rather than importing an id as a payout;
  2. every line is checked against a per-tenant sanity CEILING; a line over it is EXCLUDED, counted and
     shown in the preview with examples — never imported silently.

PROVENANCE. Rows written here carry origin='ma_sync' (+ source_table, source_row_id, synced_at). File
imports stay origin='file'. Each side's delete-then-insert is scoped to its OWN origin, so a refresh never
touches file rows and a re-upload never wipes synced rows. Overlap (both origins in one period) is
SURFACED, never silently merged.

PURE + DB-FREE: no client, no network, no I/O. The DB orchestration (config load, paged reads, the scoped
delete + insert, upload_trace) lives in router.py.
"""

# ── source registry defaults (overridable per tenant via commcalc.ledger_sync_config, migration 251) ──
# report_key      the MA report (report_pull / manual_report_mapping key) whose rows we read
# source_table    the raw_ma_* table (migration 083)
# kind            'row' (one raw row -> one ledger line) | 'component' (wide row -> one line per amount)
# date_col        the row's own date column — the period fallback when raw.period is blank/differently spelled
# field_hints     {ledger_field: raw_col} — this source's OWN name for a field whose header it doesn't
#                 carry. Only for CONTEXT fields (who/where/which order); the amount and the product label
#                 are never hinted (the amount comes from the mapping or the component, so an ID column can
#                 never sneak in through a hint). Editable per tenant in ledger_sync_config.field_hints.
DEFAULT_SOURCES = {
    "ma_daily_tx": {"report_key": "ma_daily_tx", "source_table": "raw_ma_daily_tx",
                    "kind": "row", "date_col": "tx_date",
                    "label": "MA Daily Tx (airtime / residual / spiff lines)",
                    "field_hints": {}},
    "ma_commission": {"report_key": "ma_commission", "source_table": "raw_ma_commission",
                      "kind": "component", "date_col": "tx_date",
                      "label": "MA Commission Details (per-activation components)",
                      # This report names the same facts differently than MA Daily Tx: the order is the
                      # ACTIVATION order (also the join key to raw_ma_fulfillment), the store is the
                      # merchant's processor account (mig 083: "the store's account on the processor"),
                      # the subscriber account is the BAN, and the order-type-ish label is the activation
                      # type. Stating that here is the same kind of claim report_pull's column_map makes.
                      "field_hints": {"order_number": "activation_order",
                                      "store": "merchant_account_id",
                                      "account_id": "ban",
                                      "order_type": "activation_type"}},
}

# Ledger fields a hint may NEVER fill — the money column and the label the classifier reads. Both are
# resolved from the tenant's own mapping (row shape) or synthesized from the component (wide shape).
HINT_FORBIDDEN_FIELDS = ("raw_amount", "product_name")

# Which raw source(s) feed a ledger TEMPLATE (commission_ledger.source_report) by default. A template gets
# its own namespace so synced rows can never mingle with another template's numbers. A tenant re-points
# this with a commcalc.ledger_sync_config row; unknown templates default to a same-named source.
DEFAULT_TEMPLATE_SOURCES = {
    "ma_daily_tx": ["ma_daily_tx"],
    "ma_commission": ["ma_commission"],
}

# Per-line sanity ceiling on |amount| (dollars). Configurable per (org, template) — see migration 251.
AMOUNT_CEILING_DEFAULT = 25000.0

# A column registered with one of these ROLES identifies something; it can never be an amount.
# (`ma_upload.FIELD_LABELS` is the registry; `merchant_invoice` is role='key' — the live bug this blocks.)
BLOCKED_AMOUNT_ROLES = ("key", "store", "device", "date", "status")

# Column-name shapes that are identifiers regardless of registry coverage (belt AND braces: a raw column
# nobody has labelled yet must still not become money). Matched on the column's WORD PARTS (split on '_'),
# never as a substring — a substring rule would block a legitimate 'amount_paid' ("…paid" ends in "id").
BLOCKED_AMOUNT_NAME_PARTS = ("id", "ids", "invoice", "number", "num", "no", "ban", "bin", "imei", "sim",
                             "order", "account", "tspid", "sku", "date", "time", "status", "serial",
                             "esn", "iccid", "ref", "key")

# raw_ma_commission payout components. Mirrors account.residual_subs._MA_COMPONENTS EXACTLY (12 columns,
# same order) so the ledger and the residual/P&L roll-up can never diverge on the dealer's payable.
# `payment_month` is the installment month the component belongs to (the M1..M6 display), None when the
# component is not month-indexed. Labels are NOT stored here — they are derived from the report's own
# column_map header, so the classifier sees the carrier's own vocabulary (see `component_label`).
DEFAULT_COMPONENTS = {
    "ma_commission": [
        {"col": "device_margin", "payment_month": None},
        {"col": "consumer_margin", "payment_month": None},
        {"col": "consumer_financing", "payment_month": None},
        {"col": "rebate", "payment_month": None},
        {"col": "wallet_funding", "payment_month": None},
        {"col": "fees_margin", "payment_month": None},
        {"col": "spiff_m1", "payment_month": 1},
        {"col": "spiff_m2", "payment_month": 2},
        {"col": "spiff_m3", "payment_month": 3},
        {"col": "spiff_m4", "payment_month": 4},
        {"col": "spiff_m5", "payment_month": 5},
        {"col": "spiff_m6", "payment_month": 6},
    ],
}

ORIGIN_SYNC = "ma_sync"
ORIGIN_FILE = "file"
ORIGINS = (ORIGIN_FILE, ORIGIN_SYNC)
ORIGIN_LABELS = {ORIGIN_FILE: "File import", ORIGIN_SYNC: "MA data sync"}


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────
def _sf(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


def _nh(h):
    """Normalize a header/column for matching: lowercase, strip, drop spaces/underscores."""
    return str(h or "").strip().lower().replace(" ", "").replace("_", "")


def source_def(report_key, overrides=None):
    """The source definition for a report_key: the built-in default with a config row layered on top.
    An UNKNOWN report_key still returns a usable definition (kind 'row', table raw_<key>) so a tenant can
    register a new MA report by config alone. A config `field_hints` dict MERGES onto the defaults (one
    tenant override never erases the rest), and forbidden fields are stripped even from config."""
    base = dict(DEFAULT_SOURCES.get(report_key) or
                {"report_key": report_key, "source_table": f"raw_{report_key}", "kind": "row",
                 "date_col": "tx_date", "label": report_key, "field_hints": {}})
    base["field_hints"] = dict(base.get("field_hints") or {})
    for k, v in (overrides or {}).items():
        if k == "field_hints":
            if isinstance(v, dict):
                base["field_hints"].update({kk: vv for kk, vv in v.items() if vv})
            continue
        if v not in (None, ""):
            base[k] = v
    base["field_hints"] = {k: v for k, v in base["field_hints"].items()
                           if k not in HINT_FORBIDDEN_FIELDS}
    base["report_key"] = report_key
    return base


def template_sources(source_report, config_rows=None):
    """The report_key(s) feeding one ledger template. Config rows (commcalc.ledger_sync_config) win; then
    the built-in map; then a same-named source. Disabled config rows are dropped."""
    rows = [r for r in (config_rows or []) if str(r.get("source_report") or "") == str(source_report)]
    if rows:
        return [str(r.get("report_key")) for r in rows
                if r.get("report_key") and r.get("enabled") is not False]
    return list(DEFAULT_TEMPLATE_SOURCES.get(source_report) or [source_report])


def ma_column_map(report_key, saved_map=None):
    """{source_header: dest_col} for a raw MA report — the tenant's saved manual_report_mapping override
    when present, else report_pull's default spec. Same precedence `ma_upload.effective_column_map` uses
    for the manual upload, so the sync and the upload always read a file the same way."""
    from app.modules.commcalc import ma_upload
    default_map = {}
    try:
        from app.modules.commcalc import report_pull
        spec = next((s for s in getattr(report_pull, "DEFAULT_REPORT_SPECS", [])
                     if s.get("report_key") == report_key), None)
        default_map = (spec or {}).get("column_map") or {}
    except Exception:
        default_map = {}
    eff = ma_upload.effective_column_map(saved_map, default_map)
    out = {}
    for src_h, spec_v in (eff or {}).items():
        col = spec_v.get("col") if isinstance(spec_v, dict) else spec_v
        if col:
            out[str(src_h)] = str(col)
    return out


def component_label(report_key, col, col_map=None):
    """The human label a payout component is classified under: the REPORT'S OWN header for that column
    ('4th Month Spiff'), so the tenant's rules see the carrier's vocabulary. Falls back to the derived
    label when the column is not in the report's map."""
    for src_h, c in (col_map or ma_column_map(report_key)).items():
        if c == col:
            return str(src_h)
    from app.modules.commcalc import ma_upload
    return ma_upload.derived_label(col)


def components_for(report_key, config_component_map=None, col_map=None):
    """The payout components of a 'component'-shaped source: [{col, label, payment_month}]. A config
    component_map ({col: {label?, payment_month?, enabled?}}) OVERRIDES/EXTENDS the defaults, so adding a
    carrier's new spiff column is a config edit. Deterministic order: defaults first, then config extras."""
    cfg = config_component_map if isinstance(config_component_map, dict) else {}
    out, seen = [], set()
    for d in DEFAULT_COMPONENTS.get(report_key, []):
        col = d["col"]
        over = cfg.get(col) if isinstance(cfg.get(col), dict) else {}
        if over.get("enabled") is False:
            seen.add(col)
            continue
        seen.add(col)
        out.append({"col": col,
                    "label": str(over.get("label") or component_label(report_key, col, col_map)),
                    "payment_month": (over.get("payment_month") if over.get("payment_month") is not None
                                      else d.get("payment_month"))})
    for col, over in cfg.items():
        if col in seen or not isinstance(over, dict) or over.get("enabled") is False:
            continue
        out.append({"col": col,
                    "label": str(over.get("label") or component_label(report_key, col, col_map)),
                    "payment_month": over.get("payment_month")})
    return out


def blocked_amount_reason(col):
    """Why `col` may never carry money, or None when it may. Registered identifying ROLE first (the
    `merchant_invoice` case), then the column-name shape. PURE."""
    from app.modules.commcalc import ma_upload
    meta = ma_upload.field_meta(col)
    role = (meta.get("role") or "").strip().lower()
    if role in BLOCKED_AMOUNT_ROLES:
        return (f"'{col}' is registered as role '{role}' ({meta.get('label')}) — an identifier, "
                f"not a dollar amount")
    if meta.get("cost") or role in ("money", "amount"):
        return None
    name = str(col or "").strip().lower()
    parts = [p for p in name.replace("-", "_").split("_") if p]
    blocked = {t.lower() for t in BLOCKED_AMOUNT_NAME_PARTS}
    for p in (parts or [name]):
        if p in blocked:
            return (f"'{col}' looks like an identifier (name part '{p}'), not a dollar amount")
    return None


# ── composing the two config maps into ledger-field -> raw column ────────────────────────────────
def resolve_field_sources(hdr_rules, field_defs, col_map, field_hints=None):
    """Compose the ledger's header rules with an MA report's column map.

    hdr_rules   [{target_field, source_header, transform}] — the tenant's EFFECTIVE ledger mapping (the
                same list the file import uses); `source_header` is the header a FILE would carry.
    field_defs  [{target_field, aliases, label, ...}]      — column_mapping.target_fields('commission_ledger')
                with the per-tenant registry merged, for the alias fallback.
    col_map     {source_header: raw_col}                   — the MA report's map.
    field_hints {ledger_field: raw_col}                    — this source's own name for a CONTEXT field
                (never the amount or the product label: HINT_FORBIDDEN_FIELDS are ignored here).

    Returns (resolved, unresolved) where resolved is {ledger_field: {header, col, confidence, label}} and
    `header` is ALWAYS the rule's own source_header (the key the pseudo row must use so apply_mapping finds
    it), while `col` is the raw column the value comes from. Precedence: 'mapped' (the rule's header is a
    column of this source) > 'hint' (this source's curated name for the field) > 'alias' (a registry/default
    alias — same list column_mapping.suggest() uses) > 'fuzzy' (the field token appears in a header). PURE."""
    by_norm = {_nh(h): c for h, c in (col_map or {}).items()}
    cols = {str(c) for c in (col_map or {}).values()}
    hints = {k: v for k, v in (field_hints or {}).items()
             if k not in HINT_FORBIDDEN_FIELDS and v}
    aliases = {}
    labels = {}
    for fd in (field_defs or []):
        aliases[fd.get("target_field")] = list(fd.get("aliases") or []) + \
            ([fd.get("default_source")] if fd.get("default_source") else [])
        labels[fd.get("target_field")] = fd.get("label")
    resolved, unresolved = {}, []
    for rule in (hdr_rules or []):
        tf = rule.get("target_field")
        if not tf:
            continue
        header = str(rule.get("source_header") or "")
        col, conf = None, ""
        if _nh(header) in by_norm:
            col, conf = by_norm[_nh(header)], "mapped"
        if not col and tf in hints and str(hints[tf]) in cols:
            col, conf = str(hints[tf]), "hint"
        if not col:
            for a in aliases.get(tf) or []:
                if _nh(a) in by_norm:
                    col, conf = by_norm[_nh(a)], "alias"
                    break
        if not col:
            token = _nh(tf)
            for nh, c in by_norm.items():
                if token and token in nh:
                    col, conf = c, "fuzzy"
                    break
        if col:
            resolved[tf] = {"header": header, "col": col, "confidence": conf,
                            "label": labels.get(tf) or tf}
        else:
            unresolved.append({"target_field": tf, "header": header,
                               "label": labels.get(tf) or tf})
    return resolved, unresolved


def rule_header(hdr_rules, target_field, fallback=""):
    """The source header the tenant's ledger mapping uses for one field (what a pseudo row must be keyed
    on). Independent of whether the field resolves to a raw column — the component path synthesizes the
    amount/product headers itself. PURE."""
    for r in (hdr_rules or []):
        if r.get("target_field") == target_field:
            return str(r.get("source_header") or "") or fallback
    return fallback


def pseudo_row(raw_row, resolved, skip_fields=()):
    """The dict a FILE row would have been: {source_header: value} for every resolved ledger field.
    `skip_fields` omits fields the caller supplies itself (the component path's amount/product). PURE."""
    out = {}
    for tf, r in (resolved or {}).items():
        if tf in skip_fields:
            continue
        out[r["header"]] = (raw_row or {}).get(r["col"])
    return out


# ── derivation ───────────────────────────────────────────────────────────────────────────────────
def _amount_state(raw, ceiling):
    """('ok'|'over_ceiling'|'empty', amount). An amount at/over the ceiling is quarantined, not booked."""
    amt = _sf(raw)
    if amt == 0:
        return "empty", 0.0
    try:
        c = float(ceiling)
    except (TypeError, ValueError):
        c = AMOUNT_CEILING_DEFAULT
    if c > 0 and abs(amt) > c:
        return "over_ceiling", amt
    return "ok", amt


def derive(raw_rows, *, kind, resolved, hdr_rules, cat_rules, base, components=None,
           ceiling=AMOUNT_CEILING_DEFAULT, source_table=None, synced_at=None, report_key=""):
    """Turn raw MA rows into canonical ledger rows through the FILE-IMPORT code path.

    Returns (rows, diag). `diag` carries every honesty counter the preview shows: excluded-by-ceiling
    lines (+ dollars + examples), skipped-empty amounts, per-component line counts, and the refusal
    reason when a source can't be read at all. NOTHING is dropped silently. PURE (no DB, no clock unless
    `synced_at` is passed through)."""
    from app.modules.commcalc import column_mapping
    from app.modules.commcalc import commission_ledger

    diag = {"report_key": report_key, "source_table": source_table, "kind": kind,
            "rows_in": len(raw_rows or []), "lines_out": 0,
            "excluded_ceiling": 0, "excluded_ceiling_total": 0.0, "excluded_examples": [],
            "skipped_empty_amount": 0, "skipped_no_content": 0,
            "by_component": {}, "ceiling": ceiling, "refused": None}

    amount_header = rule_header(hdr_rules, "raw_amount", "Amount")
    product_header = rule_header(hdr_rules, "product_name", "Product Name")

    # ── the amount column must exist and must not be an identifier ───────────────────────────────
    if kind == "row":
        amt_res = (resolved or {}).get("raw_amount")
        if not amt_res:
            diag["refused"] = (f"no amount column — the ledger's 'raw_amount' maps to header "
                               f"'{amount_header}', which this source does not carry. Map it on the "
                               f"column mapping (or add the header as an alias on Target Fields).")
            return [], diag
        why = blocked_amount_reason(amt_res["col"])
        if why:
            diag["refused"] = f"amount column refused — {why}. Nothing was read."
            diag["blocked_amount_col"] = amt_res["col"]
            return [], diag
        diag["amount_col"] = amt_res["col"]
        diag["amount_header"] = amt_res["header"]
        diag["amount_confidence"] = amt_res["confidence"]

    comps = []
    if kind == "component":
        for c in (components or []):
            why = blocked_amount_reason(c["col"])
            if why:
                diag["by_component"][c["col"]] = {"label": c.get("label"), "lines": 0, "total": 0.0,
                                                  "refused": why}
                continue
            comps.append(c)
            diag["by_component"][c["col"]] = {"label": c.get("label"), "lines": 0, "total": 0.0,
                                              "payment_month": c.get("payment_month")}
        if not comps:
            diag["refused"] = "no usable payout components configured for this source."
            return [], diag
        diag["components"] = [c["col"] for c in comps]

    out = []

    def _book(pseudo, raw_row, comp=None):
        src = column_mapping.apply_mapping(pseudo, hdr_rules, {})
        # SAME usable-row guard as POST /commission-ledger/import
        if not (src.get("product_name") or src.get("raw_amount") or src.get("order_type")):
            diag["skipped_no_content"] += 1
            return
        row = commission_ledger.build_row(src, base, cat_rules)
        if source_table:
            row["source_table"] = source_table
        if (raw_row or {}).get("id"):
            row["source_row_id"] = raw_row["id"]
        if synced_at:
            row["synced_at"] = synced_at
        # A month-indexed component states its month explicitly (the report's header carries no parsable
        # month token). Never overrides a month the product label DID state.
        if comp is not None and comp.get("payment_month") and not row.get("payment_month"):
            row["payment_month"] = int(comp["payment_month"])
        out.append(row)
        diag["lines_out"] += 1
        if comp is not None:
            b = diag["by_component"][comp["col"]]
            b["lines"] += 1
            b["total"] = round(b["total"] + _sf(row.get("payout_total")), 2)

    for raw in (raw_rows or []):
        if kind == "component":
            shared = pseudo_row(raw, resolved, skip_fields=("raw_amount", "product_name"))
            for c in comps:
                state, amt = _amount_state(raw.get(c["col"]), ceiling)
                if state == "empty":
                    diag["skipped_empty_amount"] += 1
                    continue
                if state == "over_ceiling":
                    diag["excluded_ceiling"] += 1
                    diag["excluded_ceiling_total"] = round(diag["excluded_ceiling_total"] + abs(amt), 2)
                    if len(diag["excluded_examples"]) < 10:
                        diag["excluded_examples"].append(
                            {"source_table": source_table, "column": c["col"], "label": c.get("label"),
                             "amount": amt, "row_id": (raw or {}).get("id")})
                    continue
                p = dict(shared)
                p[amount_header] = amt
                p[product_header] = c["label"]
                _book(p, raw, comp=c)
        else:
            amt_col = resolved["raw_amount"]["col"]
            state, amt = _amount_state(raw.get(amt_col), ceiling)
            if state == "over_ceiling":
                diag["excluded_ceiling"] += 1
                diag["excluded_ceiling_total"] = round(diag["excluded_ceiling_total"] + abs(amt), 2)
                if len(diag["excluded_examples"]) < 10:
                    diag["excluded_examples"].append(
                        {"source_table": source_table, "column": amt_col,
                         "label": (raw or {}).get(resolved.get("product_name", {}).get("col")
                                                  if resolved.get("product_name") else None),
                         "amount": amt, "row_id": (raw or {}).get("id")})
                continue
            _book(pseudo_row(raw, resolved), raw)
    return out, diag


def merge_diags(diags):
    """Roll per-source diags into one honesty block for the preview/response."""
    tot = {"rows_in": 0, "lines_out": 0, "excluded_ceiling": 0, "excluded_ceiling_total": 0.0,
           "skipped_empty_amount": 0, "skipped_no_content": 0, "excluded_examples": [], "refused": []}
    for d in (diags or []):
        for k in ("rows_in", "lines_out", "excluded_ceiling", "skipped_empty_amount", "skipped_no_content"):
            tot[k] += int(d.get(k) or 0)
        tot["excluded_ceiling_total"] = round(tot["excluded_ceiling_total"] +
                                              float(d.get("excluded_ceiling_total") or 0), 2)
        tot["excluded_examples"].extend((d.get("excluded_examples") or [])[:10])
        if d.get("refused"):
            tot["refused"].append({"report_key": d.get("report_key"),
                                   "source_table": d.get("source_table"), "reason": d["refused"]})
    tot["excluded_examples"] = tot["excluded_examples"][:20]
    return tot


def overlap_note(existing_by_origin, writing_lines):
    """The honest sentence the preview shows when a period is already populated. Returns None when there
    is nothing to warn about. Never merges or wipes anything — it describes. PURE."""
    file_lines = int(((existing_by_origin or {}).get(ORIGIN_FILE) or {}).get("lines") or 0)
    sync_lines = int(((existing_by_origin or {}).get(ORIGIN_SYNC) or {}).get("lines") or 0)
    if file_lines and writing_lines:
        return (f"This period already holds {file_lines:,} FILE-IMPORTED line(s) in this template. "
                f"Refreshing adds {writing_lines:,} MA-sync line(s) as a SEPARATE source — the two are "
                f"then counted TOGETHER in the totals unless you filter by source. Nothing file-imported "
                f"is deleted or changed.")
    if sync_lines and writing_lines:
        return (f"Replacing the {sync_lines:,} existing MA-sync line(s) for this period with "
                f"{writing_lines:,} freshly derived one(s). File-imported rows are untouched.")
    return None
