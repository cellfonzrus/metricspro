"""TENANT ONBOARDING — the COMMISSION-STATEMENT INTAKE (design stage 3, steps 3.1–3.9). PURE.

Owner (2026-09-20): *"I will not do anything manually — the system should ask me while onboarding
under 3.4 what is considered commission positive or negative. The user does not know how this system
works, they only can upload their existing data … We should be able to take those reports and assign
the existing fields to the uploaded data and SAVE that, use the intelligence to assign the categories
to the fields and ask the user to confirm. Whatever is being uploaded should be able to save is most
important — previously mostly all imports did not save the first time."*

WHAT THIS MODULE IS. Every decision the intake makes about a FILE — which sheet, which row is the
header, which row is the footer and what it says, which column is which and WHY we think so, the two
panels of the 3.4 sign question, the distinct labels with their sign mix, the bucket each label is
pre-placed in and where that guess came from, the five-bucket gross/chargebacks/net card, and the
tie-out against the file's own total — expressed over plain lists and dicts so
`backend/harness_onboarding_intake.py` can run the real rules with no database, no pandas and no
network. The router (`onboarding_intake_*` in commcalc/router.py) does the I/O: it reads the upload,
loads the tenant's saved mapping/rules, calls in here, and writes through the EXISTING writers.

WHAT IT DELIBERATELY DOES NOT DO (duplicate-check, CLAUDE.md build gate):
  · it does not classify a line — `commission_ledger.classify_line / build_row / summarize` do;
  · it does not decide direction — `commission_ledger.direction / booked_amount` under a NAMED
    convention (`commission_ledger.CONVENTIONS`, mig 1006 + 1008);
  · it does not derive a footer of its own — it calls `feed_shape.is_footer_row` with the identity
    list the router takes from `column_mapping.identity_fields` (the mig-1004 rule);
  · it does not guess header→field matches on its own — `column_mapping.suggest` does; this module
    only ATTACHES PROVENANCE (from your file / house default for <carrier> / your earlier choice)
    and three sample values, which is what a person needs to confirm a proposal;
  · it does not persist — the mapping goes through `POST /commcalc/column-mapping` (the ONE writer,
    §25.11), the bucket rules through `POST /commcalc/commission-category-map`, the lines through the
    ledger importer, and the resumable state through mig 1007's two tables.
  · it never names a carrier, tenant or product (RULE TWO): the carrier is a row the router hands in
    as `carrier_code` + `carrier_label`; the "house default" provenance is the house org's rows for
    that code, never a branch.

STAGE B (2026-09-20) — sales / POS / inventory / "other" reports on the SAME spine (design §2, 2.0–2.6):
`source_kind` names the kind, `target_table` the landing table (an EXISTING one, or none for 'other'),
`identity_fields` the report's own identity list, and the verify block always carries our numbers beside
the file's. The Stage-2 section below adds: the shared store / rep RESOLUTION shape (`resolve_stores`,
`resolve_reps` — the router hands in the §13a chain; zero unresolved is the exit gate), the verify numbers
per kind (`sales_verify`, `inventory_verify`, `other_summary`, `simple_tie`), the gate (`stage2_refusals`),
and the rail's Stage-4 `verify_table` + Stage-5 `runbook`. Nothing here lands a row: the router's
`_intake_land` calls the existing importer / snapshot writer per kind.
"""
import re
from datetime import datetime

from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import multisheet
from app.modules.commcalc.feed_shape import is_footer_row, period_fields

# ── vocabulary ──────────────────────────────────────────────────────────────────────────────────
SOURCE_KINDS = ("commission", "sales", "inventory", "pos", "other")
SOURCE_KIND_LABELS = {
    "commission": "Commission statement", "sales": "Sales report", "inventory": "Inventory report",
    "pos": "POS report", "other": "Other report (bill payments, card payments, X-reports…)",
}
# WHERE EACH KIND LANDS (Stage B, 2026-09-20) — the EXISTING destination tables (index §2), never a
# sibling raw_* table: a sales / POS export → raw_sales through the mapped importer, an inventory
# listing → inventory_aging_device through the snapshot writer, a commission statement → the ledger.
# 'other' (bill payments, card / merchant payments, X-reports, anything else) has NO destination table
# on the platform today; it is recorded as RECEIVED with its headers and row count, never faked into
# a table and never dropped (see other_summary / the router's commit).
SOURCE_KIND_TARGET = {"commission": CL.LEDGER_TABLE, "sales": "raw_sales", "pos": "raw_sales",
                      "inventory": "inventory_aging_device"}
# The column-mapping REPORT KEY (column_mapping.TARGET_FIELDS / TABLE_MAP) each kind maps through —
# the default LAYOUT; a kind may offer several layouts that land in the same table (layouts_for_kind).
REPORT_KEY_BY_KIND = {"commission": CL.MAPPING_REPORT_KEY, "sales": "sales", "pos": "pos_product_sales",
                      "inventory": "pos_inventory_listing"}
LAYOUT_LABELS = {
    "sales": "daily sales export (store / salesperson / product lines)",
    "pos_product_sales": "POS product-sales export (one row per invoice line)",
    "pos_inventory_listing": "POS on-hand inventory listing (one row per unit)",
}
# The field each kind SUMS for its tie-out, its date, its store, its rep, its transaction id.
KIND_FIELDS = {
    "sales":     {"amount": "ext_price", "gp": "gp", "date": "trans_date", "store": "store",
                  "rep": "salesperson", "txn": "trans_id", "void": "voided"},
    "pos":       {"amount": "ext_price", "gp": "gp", "date": "trans_date", "store": "store",
                  "rep": "salesperson", "txn": "trans_id", "void": "voided"},
    "inventory": {"amount": "total_cost", "unit_cost": "unit_cost", "qty": "quantity", "store": "store",
                  "key": "imei", "key2": "serial", "sku": "sku"},
    "commission": {"amount": CL.AMOUNT_FIELD, "store": "store", "rep": "rep_user", "date": "trans_date"},
}
# The columns a kind MUST have before it may land — what the slice-scoped replace and the tie-out need
# (design §2: period from each row's own date; replace only the slice the file owns = store × dates).
KIND_REQUIRED = {"sales": ("store", "trans_date", "ext_price"), "pos": ("store", "trans_date", "ext_price"),
                 "inventory": ("store", "sku"), "commission": (CL.AMOUNT_FIELD, "product_name"), "other": ()}
STATEMENT_TYPE_DEFAULT = "commission statement"
INVENTORY_NONE_KEY = "inventory:none:none"          # the explicit "no inventory export" choice (design §6.6)

STAGE_COMMISSION = "3"
STAGE_SALES = "2"
KIND_STAGE = {"commission": STAGE_COMMISSION, "sales": STAGE_SALES, "pos": STAGE_SALES,
              "inventory": STAGE_SALES, "other": STAGE_SALES}
STEPS = [
    ("3.1", "Upload the statement"),
    ("3.2", "Sheet, header row, footer"),
    ("3.3", "Confirm the columns"),
    ("3.4", "Which sign is money earned"),
    ("3.5", "Labels found in the file"),
    ("3.6", "Put every label in a bucket"),
    ("3.7", "Store / account attribution"),
    ("3.8", "Our totals beside the file's"),
    ("3.9", "Confirm this statement"),
]
STEPS_STAGE2 = [
    ("2.0", "What do you have?"),
    ("2.1", "Upload the export"),
    ("2.2", "Sheet, header row, footer"),
    ("2.3", "Confirm the columns"),
    ("2.4", "Stores and reps"),
    ("2.5", "Our numbers beside the file's"),
    ("2.6", "Confirm this export"),
]
STEPS_STAGE4 = [("4.1", "Every source, verified"), ("4.2", "Sign-off")]
STEPS_STAGE5 = [("5.1", "Monthly runbook")]
STEPS_BY_STAGE = {"2": STEPS_STAGE2, "3": STEPS, "4": STEPS_STAGE4, "5": STEPS_STAGE5}
STEP_KEYS = [k for k, _ in STEPS]
STEP_KEYS_STAGE2 = [k for k, _ in STEPS_STAGE2]
ALL_STEP_KEYS = [k for st in ("2", "3", "4", "5") for k, _ in STEPS_BY_STAGE[st]]
STATUS_NOT_STARTED, STATUS_IN_PROGRESS, STATUS_NEEDS_INPUT, STATUS_VERIFIED = (
    "not_started", "in_progress", "needs_input", "verified")
STATUSES = (STATUS_NOT_STARTED, STATUS_IN_PROGRESS, STATUS_NEEDS_INPUT, STATUS_VERIFIED)

# THE THREE PROVENANCES (design §0.3). There is no fourth, and none of them is "another carrier".
PROV_FILE = "from your file"
PROV_HOUSE = "house default"            # rendered as "house default for <carrier>"
PROV_EARLIER = "your earlier choice"
PROV_GUESS = "guess"                    # a neutral keyword hint, flagged as such
PROVENANCES = (PROV_FILE, PROV_HOUSE, PROV_EARLIER, PROV_GUESS)

# The 3.4 answers, verbatim on screen: "In this file, is money you EARNED positive or negative?"
SIGN_QUESTION = "In this file, is money you EARNED positive or negative?"
SIGN_POSITIVE, SIGN_NEGATIVE = "positive", "negative"
SIGN_ANSWERS = (SIGN_POSITIVE, SIGN_NEGATIVE)
# Both answers NET a reversal into the bucket it reverses (design §3.6): the same money coming back
# is booked NEGATIVE in its own bucket, never abs()-ed, never a sixth bucket.
SIGN_ANSWER_TO_CONVENTION = {SIGN_POSITIVE: CL.SIGN_PAYOUT_POSITIVE,
                             SIGN_NEGATIVE: CL.SIGN_PAYOUT_NEGATIVE_NETTED}
CONVENTION_TO_SIGN_ANSWER = {
    CL.SIGN_PAYOUT_POSITIVE: SIGN_POSITIVE,
    CL.SIGN_PAYOUT_NEGATIVE_NETTED: SIGN_NEGATIVE,
    # a mapping declared through the older wizard as payout_negative is ALSO "earned is negative";
    # the intake displays it as such and, on commit, re-declares the netted form.
    CL.SIGN_PAYOUT_NEGATIVE: SIGN_NEGATIVE,
}

BUCKETS = list(CL.CATEGORIES)
BUCKET_LABELS = {c: CL.CATEGORY_LABELS[c] for c in CL.CATEGORIES}
UNASSIGNED = "unassigned"
# A label whose TEXT says it is money coming back (design §3.6). Neutral words, no carrier vocabulary.
REVERSAL_WORDS = ("chargeback", "charge back", "charge-back", "deact", "reversal", "clawback",
                  "claw back", "claw-back")
# The neutral keyword hints — the SAME patterns commission_ledger ships as its built-in defaults, so
# the guess vocabulary lives in one place. They are offered as a 'guess', never applied silently.
KEYWORD_HINTS = [(pat, cat) for (_mf, _op, pat, cat, _sr, _pr) in CL.DEFAULT_RULES]

LABEL_FIELD, SUBLABEL_FIELD, AMOUNT_FIELD = "product_name", "order_type", CL.AMOUNT_FIELD
BLANK_LABEL = "(blank)"
HEADER_TEXT_RATIO = 0.6       # design §3.2: ≥60% of the cells are non-numeric strings
NEXT_ROW_FILL_RATIO = 0.5     # …and the next row is mostly populated
SAMPLES_PER_FIELD = 3
SIGN_PANEL_ROWS = 3
MONEY_HEADER_WORDS = ("amount", "gross", "net", "total", "commission", "paid", "payout", "payable",
                      "spiff", "residual", "rebate", "margin", "price", "cost", "$", "fee", "credit",
                      "debit", "balance", "earn")


# ── small helpers ───────────────────────────────────────────────────────────────────────────────
def _sf(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


def _s(v):
    return "" if v is None else str(v).strip()


def money(x):
    return round(float(x or 0.0), 2)


_NUM_RE = re.compile(r"^\(?[-+]?[$£€]?\s*[\d,]*\.?\d+\s*\)?$")


def is_number_text(s):
    """Does this cell READ as a number (money spellings included)? '' is not a number."""
    t = _s(s)
    if not t or t.lower() in ("nan", "none", "nat"):
        return False
    return bool(_NUM_RE.match(t.replace(" ", "")))


def slug(text):
    """A stable key from a display string: lower, [a-z0-9_] only, no leading/trailing '_'."""
    t = re.sub(r"[^a-z0-9]+", "_", _s(text).lower())
    return t.strip("_")


def source_report_key(carrier_code, statement_type=STATEMENT_TYPE_DEFAULT):
    """The tenant's OWN rule-set namespace for (carrier, statement type) — the commission_category_map
    `source_report` and the commission_ledger `source_report` this intake writes under. A statement
    type is a ROW-LIKE key, not a branch: a carrier that sends a separate residual file gets a second
    key the same way (design §3.1)."""
    c, t = slug(carrier_code), slug(statement_type or STATEMENT_TYPE_DEFAULT)
    if not c:
        raise ValueError("carrier code required")
    return f"{c}__{t or slug(STATEMENT_TYPE_DEFAULT)}"


def instance_key(source_kind, carrier_id, statement_type=STATEMENT_TYPE_DEFAULT):
    """The Stage-3 instance a stage_state row is keyed by (design §4): one per carrier × statement
    type. Nothing from one instance prefills another."""
    return f"{source_kind}:{_s(carrier_id)}:{slug(statement_type or STATEMENT_TYPE_DEFAULT)}"


def stage2_instance_key(source_kind, source_ref, layout_or_name):
    """The Stage-2 instance key: `sales:<pos source>:<layout>` / `inventory:<pos source>:<layout>` /
    `other:<pos source or 'file'>:<the name the person typed>`. The POS source is a code the person
    picked or typed (a row, never a branch); one file per (source, layout) — a second POS is a second
    instance, prefilled from nothing of the first."""
    kind = _s(source_kind).lower()
    if kind not in KIND_STAGE or kind == "commission":
        raise ValueError("stage-2 kinds are sales | pos | inventory | other")
    return f"{kind}:{slug(source_ref) or 'file'}:{slug(layout_or_name) or 'report'}"


def kind_of_instance(ikey):
    return _s(ikey).split(":", 1)[0]


def stage_of_kind(kind):
    return KIND_STAGE.get(_s(kind).lower(), STAGE_SALES)


def layouts_for_kind(kind, table_map):
    """The column-mapping report keys a kind may map through — every registered key whose target is
    the kind's destination table (column_mapping.TABLE_MAP is the registry; nothing here names a
    POS). The kind's default layout comes first."""
    kind = _s(kind).lower()
    tgt = SOURCE_KIND_TARGET.get(kind)
    if not tgt:
        return []
    default = REPORT_KEY_BY_KIND.get(kind)
    keys = [k for k, t in (table_map or {}).items() if t == tgt]
    keys.sort(key=lambda k: (k != default, k))
    return [{"report_key": k, "label": LAYOUT_LABELS.get(k, k.replace("_", " ")), "default": k == default}
            for k in keys]


# ── 3.2 detect: sheet, header row, records ──────────────────────────────────────────────────────
def _row_cells(row):
    return [_s(c) for c in (row or [])]


def detect_header_row(grid, max_scan=50):
    """The first row where ≥60% of its non-blank cells are non-numeric strings AND one of the next
    few rows is mostly populated (design §3.2). None when nothing qualifies (an empty sheet). A title
    block above the real header is skipped because its rows have one or two cells."""
    rows = [_row_cells(r) for r in (grid or [])]
    if not rows:
        return None
    width = max(len(r) for r in rows) or 1
    for i in range(min(len(rows), max_scan)):
        cells = [c for c in rows[i] if c]
        if len(cells) < 2 or len(cells) < width * 0.5:
            continue
        text = [c for c in cells if not is_number_text(c)]
        if len(text) / len(cells) < HEADER_TEXT_RATIO:
            continue
        # …and one of the next few rows is mostly populated (a statement may put a subtotal or a
        # blank line right under its header; a title block never has a populated row under it)
        need = max(1, int(len(cells) * NEXT_ROW_FILL_RATIO))
        for j in range(i + 1, min(i + 4, len(rows))):
            if len([c for c in rows[j] if c]) >= need:
                return i
    return None


def records_from_grid(grid, header_row):
    """(headers, records) for a sheet: the header row's cells name the columns; every later row that
    is not blank and not a header echo (multisheet.is_header_echo — the SAME rule the multi-sheet
    stitcher uses) becomes a {header: cell} record. A blank header cell is named by position."""
    rows = [_row_cells(r) for r in (grid or [])]
    if header_row is None or header_row >= len(rows):
        return [], []
    raw_hdr = rows[header_row]
    headers = [h or f"column_{i + 1}" for i, h in enumerate(raw_hdr)]
    # de-duplicate repeated headers positionally so a repeated name never swallows a column
    seen = {}
    for i, h in enumerate(headers):
        if h in seen:
            seen[h] += 1
            headers[i] = f"{h} ({seen[h]})"
        else:
            seen[h] = 1
    out = []
    for r in rows[header_row + 1:]:
        if not any(r):
            continue
        if multisheet.is_header_echo(r[:len(raw_hdr)], raw_hdr):
            continue
        rec = {}
        for i, h in enumerate(headers):
            rec[h] = r[i] if i < len(r) else ""
        out.append(rec)
    return headers, out


def stitch_sheets(sheets, sheet_name=None, header_row=None):
    """Pick the primary sheet, detect its header, append continuation sheets, drop header echoes.

    `sheets`: [(name, grid)] in workbook order, every cell already a string. The primary sheet is
    the one with the WIDEST consistent header block (design §3.2); ties go to workbook order. A later
    sheet whose detected header is IDENTICAL (multisheet.same_header) is a continuation page and is
    appended (design §5.6 — continuation sheets are never silently dropped); every sheet is listed
    with its row count and whether it was used, so nothing disappears without a trace.
    `sheet_name` / `header_row` are the person's OVERRIDES (design §3.2: shown and overridable): the
    named sheet becomes primary and the given row its header, whatever the detector thought."""
    detected = []
    for name, grid in (sheets or []):
        forced = (sheet_name is not None and _s(name) == _s(sheet_name))
        hr = header_row if (forced and header_row is not None) else detect_header_row(grid)
        if forced and header_row is None and hr is None and grid:
            hr = 0
        headers, recs = records_from_grid(grid, hr) if hr is not None else ([], [])
        detected.append({"name": name, "header_row": hr, "headers": headers, "records": recs,
                         "rows": len(grid or []), "forced": forced})
    usable = [d for d in detected if d["header_row"] is not None and (d["records"] or d["forced"])]
    if not usable:
        return {"sheet": None, "header_row": None, "headers": [], "records": [],
                "sheets": [{"name": d["name"], "rows": d["rows"], "header_row": d["header_row"],
                            "data_rows": len(d["records"]), "used": False, "role": "unreadable"}
                           for d in detected]}
    primary = next((d for d in usable if d["forced"]), None) or \
        max(usable, key=lambda d: (len(d["headers"]), -detected.index(d)))
    records = list(primary["records"])
    listing = []
    for d in detected:
        if d is primary:
            role, used = "primary", True
        elif d["header_row"] is not None and multisheet.same_header(d["headers"], primary["headers"]):
            role, used = "continuation", True
            records.extend(d["records"])
        else:
            role, used = "other", False
        listing.append({"name": d["name"], "rows": d["rows"], "header_row": d["header_row"],
                        "data_rows": len(d["records"]), "used": used, "role": role})
    return {"sheet": primary["name"], "header_row": primary["header_row"],
            "headers": list(primary["headers"]), "records": records, "sheets": listing}


# ── 3.3 columns: proposal with provenance + samples ─────────────────────────────────────────────
def sample_values(records, header, n=SAMPLES_PER_FIELD):
    """The first n DISTINCT non-blank values a column holds — what tells a person whether a header
    match is right (a header named 'Invoiced At' that holds store names, §25)."""
    out = []
    for r in records or []:
        v = _s((r or {}).get(header))
        if v and v.lower() not in ("nan", "none", "nat") and v not in out:
            out.append(v[:60])
            if len(out) >= n:
                break
    return out


def _header_in_file(header, headers):
    hmap = {h.strip().lower(): h for h in headers if _s(h)}
    return hmap.get(_s(header).lower())


def propose_columns(fields, suggestions, saved_rules, house_rules, headers, records,
                    carrier_label="", overrides=None):
    """One row per platform field: the column we propose, WHERE that proposal came from, and three
    sample values. Precedence (design §3.6's order, applied to columns): the tenant's own saved row
    → the house default for THIS carrier code → the header match `column_mapping.suggest` found in
    the file → nothing. `overrides` ({target_field: header}) is what the person has already chosen on
    screen and wins outright, with provenance 'your earlier choice' only if it equals a saved row,
    else 'from your file'."""
    saved = {r.get("target_field"): r for r in (saved_rules or []) if r.get("target_field")}
    house = {r.get("target_field"): r for r in (house_rules or []) if r.get("target_field")}
    sugg = {s.get("target_field"): s for s in (suggestions or [])}
    overrides = overrides or {}
    out = []
    for f in fields or []:
        tf = f.get("target_field")
        column, prov = "", None
        if tf in overrides:
            column = _header_in_file(overrides.get(tf), headers) or ""
            if column:
                prov = PROV_EARLIER if _s(saved.get(tf, {}).get("source_header")).lower() == column.lower() else PROV_FILE
        if not column and tf in saved:
            column = _header_in_file(saved[tf].get("source_header"), headers) or ""
            prov = PROV_EARLIER if column else None
        if not column and tf in house:
            column = _header_in_file(house[tf].get("source_header"), headers) or ""
            prov = PROV_HOUSE if column else None
        if not column and sugg.get(tf, {}).get("suggested_source"):
            column = _header_in_file(sugg[tf]["suggested_source"], headers) or ""
            prov = PROV_FILE if column else None
        out.append({
            "target_field": tf, "label": f.get("label"), "required": bool(f.get("required")),
            "transform": f.get("transform") or "text",
            "column": column,
            "provenance": prov,
            "provenance_label": (f"house default for {carrier_label or 'this carrier'}" if prov == PROV_HOUSE
                                 else prov),
            "confidence": sugg.get(tf, {}).get("confidence") or "",
            "samples": sample_values(records, column) if column else [],
        })
    return out


def mapping_rules(proposal):
    """The header→field rules the ledger's `column_mapping.apply_mapping` reads, from a confirmed
    proposal — the SAME shape `column_mapping.load_rules` returns, so preview and import cannot
    disagree about what a column means."""
    return [{"target_field": p["target_field"], "source_header": p["column"],
             "transform": p.get("transform") or "text"}
            for p in (proposal or []) if p.get("column")]


def money_columns(headers, records, amount_header, mapped_text_headers=()):
    """Every column that READS as money, with its Σ — so a money column the person did not pick is
    RECORDED with its total, never silently ignored (design §5.9). A column is money when ≥90% of its
    non-blank cells parse as numbers and it is not a text-mapped identifier, and either its header
    says money, or its values carry decimals or negatives (an integer-only id column does not)."""
    skip = {h.strip().lower() for h in mapped_text_headers if _s(h)}
    out = []
    for h in headers or []:
        if _s(h).lower() in skip:
            continue
        vals = [_s((r or {}).get(h)) for r in records or []]
        vals = [v for v in vals if v and v.lower() not in ("nan", "none", "nat")]
        if not vals:
            continue
        nums = [v for v in vals if is_number_text(v)]
        if len(nums) / len(vals) < 0.9:
            continue
        header_says_money = any(w in _s(h).lower() for w in MONEY_HEADER_WORDS)
        has_fraction = any(("." in v and not v.endswith(".0")) for v in nums)
        has_negative = any(_sf(v) < 0 for v in nums)
        if not (header_says_money or has_fraction or has_negative):
            continue
        total = money(sum(_sf(v) for v in nums))
        out.append({"header": h, "sum": total, "cells": len(nums),
                    "is_amount": _s(h).lower() == _s(amount_header).lower()})
    return out


# ── mapped rows: identity, footer ───────────────────────────────────────────────────────────────
def usable(src):
    """The ledger importer's own 'is this a line at all' test, kept identical to router.py's."""
    return bool(src.get(LABEL_FIELD) or src.get(AMOUNT_FIELD) or src.get(SUBLABEL_FIELD))


def split_footer(mapped, identity_fields, mapped_fields=None, amount_field=AMOUNT_FIELD):
    """(kept, footers) — the file's own TOTAL rows, found by the mig-1004 shape rule
    (`feed_shape.is_footer_row`: every identity field blank, money present) with the identity list
    the router takes from `column_mapping.identity_fields`. Not a second derivation: the same
    predicate `column_mapping.drop_footer_rows` applies, except the rows are RETURNED so the value
    the file claims can be shown and compared (design §3.2 / §3.8).

    `mapped_fields` (the target fields that actually have a column) narrows the identity list to
    the fields the file CAN populate: before the person has confirmed a label/store/date column,
    every row's identity is blank and the rule would call the whole file a footer. With no mapped
    identity field the rule cannot fire and nothing is dropped — the summary says so."""
    kept, footers = [], []
    ident = list(identity_fields or [])
    if mapped_fields is not None:
        ident = [f for f in ident if f in set(mapped_fields)]
    for i, m in enumerate(mapped or []):
        if ident and is_footer_row(m, ident, ()):
            footers.append({"row": i, "raw_amount": money(_sf(m.get(amount_field)))})
        else:
            kept.append(m)
    return kept, footers


def footer_summary(kept, footers, identity_mapped=True, amount_field=AMOUNT_FIELD):
    """What the footer says, and whether it equals the other lines' own sum — the number the verify
    step compares against. `file_total_raw` is None when the file states no total (the tenant may
    then type one; recorded as 'typed'), or when no identity column is mapped yet (the rule cannot
    tell a total from a line until it knows which columns identify a line)."""
    lines_sum = money(sum(_sf(m.get(amount_field)) for m in kept or []))
    if not identity_mapped:
        return {"detected": False, "rows": [], "file_total_raw": None, "lines_sum_raw": lines_sum,
                "equals_lines_sum": None, "basis": "not detectable yet — confirm the label / store / date columns first"}
    if not footers:
        return {"detected": False, "rows": [], "file_total_raw": None, "lines_sum_raw": lines_sum,
                "equals_lines_sum": None, "basis": "no total row detected — every identity field blank on no row"}
    total = money(sum(f["raw_amount"] for f in footers))
    return {"detected": True, "rows": footers, "file_total_raw": total, "lines_sum_raw": lines_sum,
            "equals_lines_sum": money(total - lines_sum) == 0.0,
            "basis": "row(s) with every identity field blank and an amount (mig-1004 shape rule)"}


# ── 3.4 the sign question ───────────────────────────────────────────────────────────────────────
def sign_panels(kept, n=SIGN_PANEL_ROWS):
    """The three largest positive and three largest negative rows — label, sub-label, store, amount —
    so the person answers 3.4 by LOOKING at real lines rather than reading a definition."""
    def row(m):
        return {"label": _s(m.get(LABEL_FIELD)) or BLANK_LABEL, "sub_label": _s(m.get(SUBLABEL_FIELD)),
                "store": _s(m.get("store")) or _s(m.get("account_name")) or _s(m.get("account_id")),
                "amount": money(_sf(m.get(AMOUNT_FIELD)))}
    pos = sorted((m for m in kept or [] if _sf(m.get(AMOUNT_FIELD)) > 0), key=lambda m: -_sf(m.get(AMOUNT_FIELD)))
    neg = sorted((m for m in kept or [] if _sf(m.get(AMOUNT_FIELD)) < 0), key=lambda m: _sf(m.get(AMOUNT_FIELD)))
    return {"question": SIGN_QUESTION, "positive": [row(m) for m in pos[:n]], "negative": [row(m) for m in neg[:n]],
            "positive_count": len(pos), "negative_count": len(neg),
            "options": [{"value": SIGN_POSITIVE, "label": "Earned is positive"},
                        {"value": SIGN_NEGATIVE, "label": "Earned is negative"}]}


def convention_for_answer(answer):
    """The NAMED convention (commission_ledger.CONVENTIONS key) the 3.4 answer declares on the amount
    column's mapping row. Anything else is 'unanswered' — there is no default (design §3.4)."""
    return SIGN_ANSWER_TO_CONVENTION.get(_s(answer).lower())


def answer_for_convention(name):
    return CONVENTION_TO_SIGN_ANSWER.get(_s(name).lower())


# ── 3.5 labels ──────────────────────────────────────────────────────────────────────────────────
def label_of(src):
    """The label a line is bucketed by: the label column, else the sub-label when the label is blank
    (a rule can then be written on that field), else '(blank)'."""
    return _s(src.get(LABEL_FIELD)) or _s(src.get(SUBLABEL_FIELD)) or BLANK_LABEL


def label_field_of(src):
    return LABEL_FIELD if _s(src.get(LABEL_FIELD)) else (SUBLABEL_FIELD if _s(src.get(SUBLABEL_FIELD)) else None)


def label_summary(kept, conv=None):
    """Distinct labels with row count, Σ raw, Σ canonical, sign mix and their sub-labels, sorted by
    |Σ raw| descending (design §3.5). Canonical = raw × payout_sign of the convention, or None when
    3.4 is unanswered — never a defaulted direction."""
    sign = (conv or {}).get("payout_sign") if conv else None
    agg = {}
    for m in kept or []:
        lbl = label_of(m)
        a = agg.setdefault(lbl, {"label": lbl, "match_field": label_field_of(m), "count": 0,
                                 "sum_raw": 0.0, "positives": 0, "negatives": 0, "zeros": 0,
                                 "sub_labels": {}})
        amt = _sf(m.get(AMOUNT_FIELD))
        a["count"] += 1
        a["sum_raw"] += amt
        if amt > 0:
            a["positives"] += 1
        elif amt < 0:
            a["negatives"] += 1
        else:
            a["zeros"] += 1
        sub = _s(m.get(SUBLABEL_FIELD)) if a["match_field"] == LABEL_FIELD else ""
        if sub:
            s = a["sub_labels"].setdefault(sub, {"sub_label": sub, "count": 0, "sum_raw": 0.0})
            s["count"] += 1
            s["sum_raw"] += amt
    out = []
    for a in agg.values():
        a["sum_raw"] = money(a["sum_raw"])
        a["sum_canonical"] = money(a["sum_raw"] * sign) if sign else None
        a["sign_mix"] = ("mixed" if a["positives"] and a["negatives"] else
                         "all_positive" if a["positives"] else
                         "all_negative" if a["negatives"] else "all_zero")
        a["sub_labels"] = sorted(({**s, "sum_raw": money(s["sum_raw"])} for s in a["sub_labels"].values()),
                                 key=lambda s: -abs(s["sum_raw"]))
        a["reversal_flag"] = reversal_preflag(a, sign)
        out.append(a)
    return sorted(out, key=lambda a: (-abs(a["sum_raw"]), a["label"]))


def reversal_preflag(label_row, payout_sign=None):
    """Pre-set the reversal flag when the label's text says so, or when every line points against
    the earned direction after normalisation (design §3.6). Unanswered sign → text only."""
    text = _s(label_row.get("label")).lower()
    if any(w in text for w in REVERSAL_WORDS):
        return True
    if payout_sign:
        mix = label_row.get("sign_mix")
        return (mix == "all_negative" and payout_sign > 0) or (mix == "all_positive" and payout_sign < 0)
    return False


# ── 3.6 bucket suggestions ──────────────────────────────────────────────────────────────────────
def _rule_hits(label, sub_labels, rules, label_field=LABEL_FIELD):
    """The category of the first rule (ascending priority) that matches this label under the
    ledger's own matcher semantics (equals / contains on product_name or order_type)."""
    for r in sorted(rules or [], key=lambda r: (r.get("priority") if r.get("priority") is not None else 100)):
        cat = _s(r.get("category")).lower()
        if cat not in BUCKETS:
            continue
        pat = _s(r.get("pattern")).lower()
        op = _s(r.get("match_op")) or "contains"
        field = _s(r.get("match_field")) or LABEL_FIELD
        if not pat or op not in ("equals", "contains"):
            continue
        # a label taken from the sub-label column (blank main label) is matched on THAT field
        if field == LABEL_FIELD:
            targets = [label.lower()] if label_field == LABEL_FIELD else []
        else:
            targets = [s.lower() for s in sub_labels] + ([label.lower()] if label_field == SUBLABEL_FIELD else [])
        for t in targets:
            if (t == pat) if op == "equals" else (pat in t):
                return cat
    return None


def _hint(text):
    t = _s(text).lower()
    for pat, cat in KEYWORD_HINTS:
        if pat.lower() in t and cat in BUCKETS:
            return cat
    return None


def keyword_guess(label, sub_labels=()):
    """The neutral keyword hint for a label — the ledger's own built-in vocabulary — or None. When
    the label itself says nothing, its sub-labels are consulted and a hint is offered only when
    every hinted sub-label agrees (a section that mixes 'Commission' and 'Spiff' lines is nobody's
    guess to make)."""
    b = _hint(label)
    if b:
        return b
    hints = {h for h in (_hint(s) for s in sub_labels or ()) if h}
    return hints.pop() if len(hints) == 1 else None


def suggest_buckets(labels, tenant_rules=None, house_rules=None, carrier_label=""):
    """Attach {bucket, provenance} to each label row: the tenant's own earlier rules for THIS
    source_report ('your earlier choice') → the house org's rules for THIS carrier code ('house
    default for <carrier>') → a keyword hint ('guess') → none (stays in the Unassigned tray)."""
    out = []
    for a in labels or []:
        subs = [s["sub_label"] for s in a.get("sub_labels") or []]
        lf = a.get("match_field") or LABEL_FIELD
        b, prov = _rule_hits(a["label"], subs, tenant_rules, lf), PROV_EARLIER
        if not b:
            b, prov = _rule_hits(a["label"], subs, house_rules, lf), PROV_HOUSE
        if not b:
            b, prov = keyword_guess(a["label"], subs), PROV_GUESS
        row = dict(a)
        row["bucket"] = b or UNASSIGNED
        row["provenance"] = prov if b else None
        row["provenance_label"] = (f"house default for {carrier_label or 'this carrier'}" if (b and prov == PROV_HOUSE)
                                   else (prov if b else None))
        out.append(row)
    return out


def rules_for_assignments(source_report, assignments):
    """The commission_category_map rows an assignment list becomes — one `equals` rule per label on
    the field the label came from, ascending priority in tray order. `sign_rule` stays the default:
    under a NETTING convention a reversal is offered to the rules by direction, so it books −|amt|
    into the bucket it reverses; 'any' would be the abs() inflation the sign proof forbids."""
    rules, seen = [], set()
    for i, a in enumerate(assignments or []):
        label, bucket = _s(a.get("label")), _s(a.get("bucket")).lower()
        if not label or label == BLANK_LABEL or bucket not in BUCKETS:
            continue
        field = _s(a.get("match_field")) or LABEL_FIELD
        key = (field, label.lower())
        if key in seen:
            continue
        seen.add(key)
        rules.append({"source_report": source_report, "match_field": field, "match_op": "equals",
                      "pattern": label, "category": bucket, "sign_rule": "negative_only",
                      "priority": 10 + i})
    return rules


def unassigned_labels(labels, assignments):
    """Every label in the file that the assignment list does not put in one of the five buckets."""
    placed = {_s(a.get("label")): _s(a.get("bucket")).lower() for a in assignments or []}
    return [a["label"] for a in labels or [] if placed.get(a["label"]) not in BUCKETS]


# ── 3.7 identity strings (surfaced; resolution is the shared resolver's job) ────────────────────
def identity_strings(kept, fields=("store", "account_id", "account_name", "rep_user")):
    """Distinct store / account / rep strings with count and Σ raw — what 3.7 hands the resolver."""
    out = {}
    for f in fields:
        agg = {}
        for m in kept or []:
            v = _s(m.get(f))
            if not v:
                continue
            a = agg.setdefault(v, {"value": v, "count": 0, "sum_raw": 0.0})
            a["count"] += 1
            a["sum_raw"] += _sf(m.get(AMOUNT_FIELD))
        if agg:
            out[f] = sorted(({**a, "sum_raw": money(a["sum_raw"])} for a in agg.values()),
                            key=lambda a: -abs(a["sum_raw"]))[:200]
    return out


# ── period ──────────────────────────────────────────────────────────────────────────────────────
def period_proposal(kept, date_field="trans_date"):
    """The period the statement's OWN dates imply (design §6.8): the months present with counts,
    the span, and a proposal when one month dominates. Asked, not assumed, when it spans two."""
    months = {}
    lo = hi = None
    for m in kept or []:
        pf = period_fields(_s(m.get(date_field))[:10])
        if not pf:
            continue
        months[pf["period"]] = months.get(pf["period"], 0) + 1
        d = _s(m.get(date_field))[:10]
        lo = d if lo is None or d < lo else lo
        hi = d if hi is None or d > hi else hi
    ordered = sorted(months.items(), key=lambda kv: -kv[1])
    return {"months": [{"period": p, "rows": n} for p, n in ordered],
            "span_from": lo, "span_to": hi,
            "proposed": ordered[0][0] if ordered else None,
            "spans_two_months": len(ordered) > 1,
            "dated_rows": sum(months.values())}


# ── 3.8 totals + tie-out ────────────────────────────────────────────────────────────────────────
def bucket_totals(ledger_rows):
    """gross / chargebacks / net per bucket from LEDGER rows (built or re-read — the same shape), plus
    the unmapped ('other') and charge lines that keep a tie-out from closing. Never abs(): gross is
    the Σ of positive bookings, chargebacks the Σ of negative bookings, net their sum."""
    b = {c: {"gross": 0.0, "chargebacks": 0.0, "net": 0.0, "count": 0} for c in BUCKETS}
    other = {"gross": 0.0, "chargebacks": 0.0, "net": 0.0, "count": 0}
    charge_total, charge_count = 0.0, 0
    for r in ledger_rows or []:
        cat = _s(r.get("category"))
        amt = _sf(r.get("payout_total"))
        tgt = b.get(cat) if cat in b else (other if cat == "other" else None)
        if tgt is None:
            if cat == "charge":
                charge_total += _sf(r.get("raw_amount"))
                charge_count += 1
            continue
        tgt["count"] += 1
        tgt["net"] += amt
        if amt > 0:
            tgt["gross"] += amt
        elif amt < 0:
            tgt["chargebacks"] += amt
    for d in list(b.values()) + [other]:
        for k in ("gross", "chargebacks", "net"):
            d[k] = money(d[k])
    net_total = money(sum(d["net"] for d in b.values()))
    return {"buckets": b, "bucket_labels": BUCKET_LABELS, "net_total": net_total,
            "other": other, "charges": {"total": money(charge_total), "count": charge_count},
            "rows": len(ledger_rows or [])}


def tie_out(totals, file_total_raw, payout_sign, typed_total=None):
    """Σ canonical (all five buckets) beside the file's own total, to the cent (design §3.8).
    The file's total is the footer's RAW value × the earned sign; a typed total (no footer) is
    recorded as 'typed'. `match` is None when there is nothing to compare against."""
    src, ft = None, None
    if file_total_raw is not None:
        src, ft = "footer", money(_sf(file_total_raw) * (payout_sign or 1))
    elif typed_total not in (None, ""):
        src, ft = "typed", money(_sf(typed_total) * (payout_sign or 1))
    net = money((totals or {}).get("net_total") or 0.0)
    diff = money(net - ft) if ft is not None else None
    return {"our_total": net, "file_total": ft, "file_total_source": src, "difference": diff,
            "match": (diff == 0.0) if diff is not None else None,
            "other_unassigned": money(((totals or {}).get("other") or {}).get("net") or 0.0),
            "other_count": int(((totals or {}).get("other") or {}).get("count") or 0)}


def sanity_banners(labels_assigned, totals, payout_sign):
    """The yellow banners of design §3.4: the commission bucket netting negative, or a reversal-flagged
    label netting POSITIVE, both mean the sign answer is probably the wrong way round."""
    out = []
    comm = ((totals or {}).get("buckets") or {}).get("commission") or {}
    if comm.get("count") and comm.get("net", 0) < 0:
        out.append("Your commission bucket nets NEGATIVE — re-check 3.4: your sign choice makes chargebacks positive.")
    for a in labels_assigned or []:
        if a.get("is_reversal") and payout_sign and a.get("sum_raw") is not None:
            if money(_sf(a.get("sum_raw")) * payout_sign) > 0:
                out.append(f"'{a.get('label')}' is flagged as a reversal but nets POSITIVE under your sign choice — re-check 3.4.")
    return out


# ── 3.9 commit gate ─────────────────────────────────────────────────────────────────────────────
def commit_refusals(sign_answer, labels, assignments, tie, attestation=None):
    """Why a commit is REFUSED (design §0.1, §5.3, §5.11): 3.4 unanswered; any label unassigned; a
    non-zero difference (or nothing to compare against) without an explicit attestation carrying a
    reason. An empty list means the commit may proceed."""
    out = []
    if not convention_for_answer(sign_answer):
        out.append("3.4 is unanswered — say whether money you EARNED is positive or negative in this file.")
    missing = unassigned_labels(labels, assignments)
    if missing:
        out.append(f"{len(missing)} label(s) are still Unassigned: " + ", ".join(missing[:8])
                   + (" …" if len(missing) > 8 else ""))
    att = attestation or {}
    att_ok = bool(_s(att.get("reason")))
    if tie is not None:
        if tie.get("match") is None and not att_ok:
            out.append("The file states no total and none was typed — enter the statement's total, or attest why there is none.")
        elif tie.get("match") is False and not att_ok:
            out.append(f"Our total {tie.get('our_total'):,.2f} differs from the file's {tie.get('file_total'):,.2f} "
                       f"by {tie.get('difference'):,.2f} — fix the mapping/buckets, or attest the difference with a reason.")
        if tie.get("file_total") == 0.0 and tie.get("our_total") == 0.0 and not att_ok:
            out.append("Both totals are $0.00 — a zero statement is verified only with an attestation and a reason.")
    return out


# ── STAGE 2 (sales / POS / inventory / other) — identity resolution, verify numbers, the gate ─────
# The SAME spine as stage 3: the file is read the same way, the columns are proposed with the same
# provenance, the footer is found by the same rule; what differs is the NUMBERS shown beside the file's
# (design §2 verify: rows, distinct txns, Σ amount, Σ GP, date span, voided, per-store, per-rep) and
# the exit gate (zero unresolved store strings). Everything below is pure over mapped rows.
IDENTITY_ACTIONS = ("assign", "create", "not_ours", "company_level")
ID_RESOLVED, ID_UNRESOLVED = "resolved", "unresolved"


def identity_rows(kept, field, amount_field, cap=500):
    """Distinct values of ONE identity column with count and Σ — what the resolver is handed."""
    agg = {}
    for m in kept or []:
        v = _s(m.get(field))
        if not v:
            continue
        a = agg.setdefault(v, {"value": v, "count": 0, "sum_raw": 0.0})
        a["count"] += 1
        a["sum_raw"] += _sf(m.get(amount_field))
    return sorted(({**a, "sum_raw": money(a["sum_raw"])} for a in agg.values()),
                  key=lambda a: (-a["count"], a["value"]))[:cap]


def resolve_stores(rows, resolve, decisions=None, allow_company_level=False):
    """Attach the resolver's answer and the person's decision to each store string.

    `resolve(raw)` → (store_code or None, how) is the SHARED resolver (index §13a: aliases →
    store_mapping → the storeops roster; the router builds it from the existing `_store_maps`).
    `decisions` = {raw: {action: assign|create|not_ours|company_level, store_code, reason}} is what the
    person chose on 2.4 / 3.7. A string's `status` is 'resolved' when the resolver knows it or a
    decision covers it, else 'unresolved' — and zero unresolved is the exit gate (design §5.7: an
    unresolved store string never lands as 'Default'). `not_ours` needs a reason; it is recorded and
    its rows are EXCLUDED from the landing, never silently kept."""
    out = []
    for r in rows or []:
        raw = r["value"]
        code, how = resolve(raw) if resolve else (None, None)
        d = dict((decisions or {}).get(raw) or {})
        action = _s(d.get("action")).lower() or None
        row = {**r, "resolved_code": code or None, "how": how if code else None, "action": action,
               "decision_code": _s(d.get("store_code")) or None, "reason": _s(d.get("reason")) or None,
               "address": _s(d.get("address")) or None}
        if action == "assign" and row["decision_code"]:
            row["status"], row["lands_as"] = ID_RESOLVED, row["decision_code"]
        elif action == "create" and row["decision_code"]:
            row["status"], row["lands_as"] = ID_RESOLVED, row["decision_code"]
        elif action == "not_ours":
            row["status"] = ID_RESOLVED if row["reason"] else ID_UNRESOLVED
            row["lands_as"] = None
            if not row["reason"]:
                row["needs"] = "a reason is required to mark a store as not yours"
        elif action == "company_level" and allow_company_level:
            row["status"], row["lands_as"] = ID_RESOLVED, None
        elif code:
            row["status"], row["lands_as"] = ID_RESOLVED, code
        else:
            row["status"], row["lands_as"] = ID_UNRESOLVED, None
        out.append(row)
    return out


def unresolved_stores(rows):
    return [r["value"] for r in rows or [] if r.get("status") != ID_RESOLVED]


def excluded_stores(rows):
    """The strings the person marked 'not ours' (with a reason): their rows do not land."""
    return {r["value"]: r.get("reason") for r in rows or [] if r.get("action") == "not_ours" and r.get("reason")}


def resolve_reps(rows, resolve, decisions=None):
    """Rep strings → employees. `resolve(raw)` → (canonical employee name or None, how) over the
    roster + rep_aliases. A rep the roster does not know is SURFACED, not a gate (design §2 gates on
    stores; a rep's pay attaches later through the same aliases). Decision: assign → a rep_aliases row
    alias → canonical; leave → recorded as-is."""
    out = []
    for r in rows or []:
        raw = r["value"]
        name, how = resolve(raw) if resolve else (None, None)
        d = dict((decisions or {}).get(raw) or {})
        action = _s(d.get("action")).lower() or None
        row = {**r, "resolved_name": name or None, "how": how if name else None, "action": action,
               "decision_name": _s(d.get("canonical")) or None}
        if action == "assign" and row["decision_name"]:
            row["status"] = ID_RESOLVED
        elif name:
            row["status"] = ID_RESOLVED
        else:
            row["status"] = "leave" if action == "leave" else ID_UNRESOLVED
        out.append(row)
    return out


def date_span(kept, date_field):
    lo = hi = None
    undated = 0
    for m in kept or []:
        d = _s(m.get(date_field))[:10]
        if not period_fields(d):
            undated += 1
            continue
        lo = d if lo is None or d < lo else lo
        hi = d if hi is None or d > hi else hi
    return {"from": lo, "to": hi, "undated_rows": undated}


def _per(kept, field, amount_field, cap=300):
    agg = {}
    for m in kept or []:
        k = _s(m.get(field)) or "(blank)"
        a = agg.setdefault(k, {"value": k, "count": 0, "sum": 0.0})
        a["count"] += 1
        a["sum"] += _sf(m.get(amount_field))
    return sorted(({**a, "sum": money(a["sum"])} for a in agg.values()), key=lambda a: -a["count"])[:cap]


def is_voided(v):
    return _s(v).lower() in ("yes", "y", "true", "1", "t", "voided", "void", "refund", "refunded")


def sales_verify(kept, fields=None):
    """The design-§2 verify numbers for a sales / POS export over mapped rows (built OR re-read — the
    same shape, so the commit compares like with like): rows, distinct transaction ids, Σ amount,
    Σ GP, date span, voided count, per-store and per-rep counts with Σ."""
    f = fields or KIND_FIELDS["sales"]
    amt, gp, dt, st, rep, txn, vd = (f["amount"], f.get("gp"), f["date"], f["store"], f.get("rep"),
                                     f.get("txn"), f.get("void"))
    kept = list(kept or [])
    txns = {_s(m.get(txn)) for m in kept if txn and _s(m.get(txn))}
    return {
        "rows": len(kept),
        "distinct_txns": len(txns),
        "sum_amount": money(sum(_sf(m.get(amt)) for m in kept)),
        "sum_gp": money(sum(_sf(m.get(gp)) for m in kept)) if gp else None,
        "date_span": date_span(kept, dt),
        "voided_rows": sum(1 for m in kept if vd and is_voided(m.get(vd))),
        "per_store": _per(kept, st, amt),
        "per_rep": _per(kept, rep, amt) if rep else [],
        "storeless_rows": sum(1 for m in kept if not _s(m.get(st))),
    }


def inventory_verify(kept, fields=None):
    """Units, Σ cost, per-store units, rows with / without a device key (an ordered unit legitimately
    has no IMEI yet — reported, never mistaken for a total)."""
    f = fields or KIND_FIELDS["inventory"]
    amt, uc, qty, st, k1, k2 = f["amount"], f["unit_cost"], f["qty"], f["store"], f["key"], f["key2"]
    kept = list(kept or [])

    def units(m):
        q = _sf(m.get(qty)) if _s(m.get(qty)) else 1.0
        return q if q else 1.0

    def cost(m):
        return _sf(m.get(amt)) if _s(m.get(amt)) else _sf(m.get(uc)) * units(m)
    per = {}
    for m in kept:
        k = _s(m.get(st)) or "(blank)"
        a = per.setdefault(k, {"value": k, "count": 0, "units": 0.0, "sum": 0.0})
        a["count"] += 1
        a["units"] += units(m)
        a["sum"] += cost(m)
    keyed = sum(1 for m in kept if _s(m.get(k1)) or _s(m.get(k2)))
    return {
        "rows": len(kept), "units": round(sum(units(m) for m in kept), 2),
        "sum_cost": money(sum(cost(m) for m in kept)),
        "sum_unit_cost": money(sum(_sf(m.get(uc)) for m in kept)),
        "rows_with_device_key": keyed, "rows_without_device_key": len(kept) - keyed,
        "per_store": sorted(({**a, "units": round(a["units"], 2), "sum": money(a["sum"])} for a in per.values()),
                            key=lambda a: -a["count"])[:300],
        "storeless_rows": sum(1 for m in kept if not _s(m.get(st))),
    }


def other_summary(headers, records, money_cols):
    """What we can honestly say about a report with NO destination: its headers, row count and
    every money column's Σ. Recorded as 'received', shown as such, never faked into a table."""
    return {"rows": len(records or []), "headers": list(headers or []),
            "money_columns": [{"header": m["header"], "sum": m["sum"], "cells": m["cells"]} for m in money_cols or []],
            "destination": None,
            "basis": "received — no destination table on the platform for this report yet; kept with its headers and row count"}


def simple_tie(our_total, file_total_raw, typed_total=None):
    """Σ ours beside the file's own total (footer, else typed — recorded as such), to the cent."""
    src, ft = None, None
    if file_total_raw is not None:
        src, ft = "footer", money(_sf(file_total_raw))
    elif typed_total not in (None, ""):
        src, ft = "typed", money(_sf(typed_total))
    ours = money(our_total)
    diff = money(ours - ft) if ft is not None else None
    return {"our_total": ours, "file_total": ft, "file_total_source": src, "difference": diff,
            "match": (diff == 0.0) if diff is not None else None}


def stage2_refusals(kind, mapped_fields, stores, tie, attestation=None, undated_rows=0,
                    storeless_rows=0, rows_to_land=0):
    """Why a Stage-2 commit is REFUSED (design §0.1, §2 exit, §5.3, §5.7). An empty list = proceed."""
    out = []
    kind = _s(kind).lower()
    missing = [f for f in KIND_REQUIRED.get(kind, ()) if f not in set(mapped_fields or [])]
    if missing:
        out.append("Map these columns first: " + ", ".join(missing) + " — they identify the slice this file owns.")
    unresolved = unresolved_stores(stores)
    if unresolved:
        out.append(f"{len(unresolved)} store string(s) are unresolved: " + ", ".join(unresolved[:8])
                   + (" …" if len(unresolved) > 8 else "") + " — assign each to one of your stores, create the store, or mark it not yours with a reason.")
    if kind in ("sales", "pos") and undated_rows:
        out.append(f"{undated_rows} row(s) carry no parseable date — a row without a date cannot be booked to a month; fix the date column or the file.")
    if storeless_rows:
        out.append(f"{storeless_rows} row(s) carry no store — the file cannot prove which slice it owns; fix the store column or the file.")
    if rows_to_land == 0 and kind != "other":
        out.append("Nothing would land — every row is a footer, excluded, or empty.")
    att_ok = bool(_s((attestation or {}).get("reason")))
    if tie is not None and kind != "other":
        if tie.get("match") is None and not att_ok:
            out.append("The file states no total and none was typed — enter the file's total, or attest why there is none.")
        elif tie.get("match") is False and not att_ok:
            out.append(f"Our total {tie.get('our_total'):,.2f} differs from the file's {tie.get('file_total'):,.2f} "
                       f"by {tie.get('difference'):,.2f} — fix the columns or the stores, or attest the difference with a reason.")
        if tie.get("file_total") == 0.0 and tie.get("our_total") == 0.0 and not att_ok:
            out.append("Both totals are $0.00 — a zero file is verified only with an attestation and a reason.")
    return out


# ── §4 state: the rail ──────────────────────────────────────────────────────────────────────────
def default_stage_rows():
    return []


STAGE_LABELS = {"1": "Company setup", "2": "Sales & inventory", "3": "Commission statements",
                "4": "Verify everything", "5": "Done"}


def _instance_label(r):
    """A human label for a stage row from its key and payload — no lookups."""
    p = r.get("payload") or {}
    ik = _s(r.get("instance_key"))
    kind = kind_of_instance(ik)
    parts = ik.split(":")
    if ik == INVENTORY_NONE_KEY:
        return "Inventory export — none (recorded)"
    if kind == "commission":
        return f"{p.get('carrier_name') or 'carrier ' + (parts[1][:8] if len(parts) > 1 else '')} — {(parts[2] if len(parts) > 2 else '').replace('_', ' ')}"
    if kind == "other":
        return f"{p.get('name') or (parts[2] if len(parts) > 2 else 'other report').replace('_', ' ')} (other report)"
    return f"{SOURCE_KIND_LABELS.get(kind, kind)} — {p.get('source_ref') or (parts[1] if len(parts) > 1 else '')}"


def _stage_lamp(instances):
    if not instances:
        return STATUS_NOT_STARTED
    if all(i["status"] == STATUS_VERIFIED for i in instances):
        return STATUS_VERIFIED
    if any(i["status"] == STATUS_NEEDS_INPUT for i in instances):
        return STATUS_NEEDS_INPUT
    return STATUS_IN_PROGRESS


def company_lamp(company):
    """Stage 1 is NOT built in this flow: its lamp READS the existing company / store / carrier rows
    (design: the roster confirmed elsewhere). Verified = at least one store and one carrier."""
    c = company or {}
    n = lambda k: int(c.get(k) or 0)
    if n("stores") and n("carriers"):
        return STATUS_VERIFIED
    if n("stores") or n("carriers") or n("companies"):
        return STATUS_IN_PROGRESS
    return STATUS_NOT_STARTED


def rail(stage_rows, current_instance=None, company=None, run=None):
    """The left rail as a projection of the persisted stage rows (design §0.2): every instance of
    stages 2 and 3 with its step and lamp, the stage lamps, the Stage-4 verify table and the Stage-5
    runbook. Reopening lands on the first non-verified step of the current instance."""
    instances = []
    for r in stage_rows or []:
        st_no = _s(r.get("stage"))
        if st_no not in ("2", "3"):
            continue
        st = _s(r.get("status")) if _s(r.get("status")) in STATUSES else STATUS_IN_PROGRESS
        keys = [k for k, _ in STEPS_BY_STAGE[st_no]]
        instances.append({"instance_key": r.get("instance_key"), "stage": st_no,
                          "kind": kind_of_instance(r.get("instance_key")),
                          "label": _instance_label(r),
                          "step": r.get("step") or keys[0],
                          "status": st, "payload": r.get("payload") or {},
                          "verified_numbers": r.get("verified_numbers"),
                          "verified_by": r.get("verified_by"), "verified_at": r.get("verified_at"),
                          "blocking_reason": r.get("blocking_reason"), "updated_at": r.get("updated_at"),
                          "current": r.get("instance_key") == current_instance})
    s2 = [i for i in instances if i["stage"] == "2"]
    s3 = [i for i in instances if i["stage"] == "3"]
    signed = bool((run or {}).get("signed_off_at"))
    all_verified = bool(instances) and all(i["status"] == STATUS_VERIFIED for i in instances)
    stages = [
        {"stage": "1", "label": STAGE_LABELS["1"], "status": company_lamp(company), "built": False,
         "note": "reads your company, store and carrier rows — set up on the company / store screens"},
        {"stage": "2", "label": STAGE_LABELS["2"], "status": _stage_lamp(s2), "built": True},
        {"stage": "3", "label": STAGE_LABELS["3"], "status": _stage_lamp(s3), "built": True},
        {"stage": "4", "label": STAGE_LABELS["4"], "built": True,
         "status": (STATUS_VERIFIED if (all_verified and signed) else
                    STATUS_IN_PROGRESS if instances else STATUS_NOT_STARTED)},
        {"stage": "5", "label": STAGE_LABELS["5"], "built": True,
         "status": STATUS_VERIFIED if (all_verified and signed) else STATUS_NOT_STARTED},
    ]
    cur = next((i for i in instances if i["current"]), None) or \
        next((i for i in instances if i["status"] != STATUS_VERIFIED), None)
    return {"stages": stages,
            "steps": [{"key": k, "label": l} for k, l in STEPS],
            "steps_by_stage": {st: [{"key": k, "label": l} for k, l in steps] for st, steps in STEPS_BY_STAGE.items()},
            "instances": instances,
            "resume": {"instance_key": cur["instance_key"], "step": cur["step"], "stage": cur["stage"]} if cur else None,
            "verify_table": verify_table(instances),
            "runbook": runbook(instances),
            "sign_off": {"signed": signed, "by": (run or {}).get("signed_off_by"),
                         "at": (run or {}).get("signed_off_at"),
                         "on_behalf": bool((run or {}).get("signed_off_on_behalf")),
                         "all_verified": all_verified}}


def _tie_of(vn):
    t = (vn or {}).get("tie") or {}
    return t.get("our_total"), t.get("file_total"), t.get("difference"), t.get("match")


def verify_table(instances):
    """Stage 4.1 — one row per source: our total, the file's total, the difference, status, who,
    when; a red row links to the step that fixes it. Numbers come from `verified_numbers` (what was
    RE-READ after landing), never from a screen."""
    rows = []
    for i in instances or []:
        vn = i.get("verified_numbers") or {}
        our, ft, diff, match = _tie_of(vn)
        p = i.get("payload") or {}
        kind = i["kind"]
        declined = i["instance_key"] == INVENTORY_NONE_KEY
        if kind == "other":
            our = None
            basis = vn.get("basis") or "received"
        elif declined:
            basis = "no inventory export — recorded by " + _s(i.get("verified_by") or "?")
        else:
            basis = vn.get("basis")
        red = i["status"] != STATUS_VERIFIED
        # the step that fixes a red row: the identity gate first, then the totals, else where it stopped
        fix_step = i["step"]
        if red and i["stage"] == "2":
            fix_step = "2.4" if "unresolved" in _s(i.get("blocking_reason")) else "2.5" if vn else i["step"]
        elif red and i["stage"] == "3":
            fix_step = "3.8" if vn else i["step"]
        rows.append({"instance_key": i["instance_key"], "stage": i["stage"], "kind": kind, "label": i["label"],
                     "period": p.get("period") or vn.get("period") or (
                         "–".join(x for x in ((vn.get("date_span") or {}).get("from"), (vn.get("date_span") or {}).get("to")) if x) or None),
                     "our_total": our, "file_total": ft, "difference": diff, "match": match,
                     "rows_landed": vn.get("rows_landed"), "status": i["status"], "red": red,
                     "verified_by": i.get("verified_by"), "verified_at": i.get("verified_at"),
                     "blocking_reason": i.get("blocking_reason"), "basis": basis, "fix_step": fix_step})
    return rows


def runbook(instances):
    """Stage 5 — what to upload each month, generated from the instances that were configured, plus
    the two links the owner asked for (design §2 Stage 5)."""
    monthly = []
    for i in instances or []:
        if i["instance_key"] == INVENTORY_NONE_KEY:
            continue
        p = i.get("payload") or {}
        kind = i["kind"]
        monthly.append({"instance_key": i["instance_key"], "kind": kind, "label": i["label"],
                        "filename_example": p.get("filename"),
                        "lands_in": SOURCE_KIND_TARGET.get(kind) or "(no destination yet — recorded only)",
                        "mapping_saved": bool(p.get("column_map")),
                        "status": i["status"]})
    return {"links": [{"label": "Sales report", "href": "/commcalc/sales-report", "screen": "sales_report"},
                      {"label": "Commissions", "href": "/commcalc/commission-ledger", "screen": "commission_ledger"}],
            "monthly": monthly,
            "note": "Each month: drop the same exports here (stages 2–3 only). A file whose columns match the saved mapping is pre-mapped; the stores and reps you resolved are remembered."}


def next_step(step):
    keys = STEP_KEYS_STAGE2 if _s(step).startswith("2.") else STEP_KEYS
    try:
        i = keys.index(step)
    except ValueError:
        return keys[0]
    return keys[min(i + 1, len(keys) - 1)]


def now_iso():
    return datetime.utcnow().isoformat() + "Z"
