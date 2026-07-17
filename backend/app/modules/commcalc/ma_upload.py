"""Manual MA-report upload — pure, DB-free, Playwright-free logic (SaaS/SAP manual ingest track).

The MA reports (MA Commission, MA Daily Tx, MA Handset Ordering) are pulled by the flaky live portal
AND, in parallel, uploaded by hand. This module holds the pure pieces of the MANUAL path so they are
unit-testable with zero DB / browser:

  • the per-report natural DEDUP keys (append-idempotence),
  • multi-month SPLIT of one file across its real months (from the row's own date column),
  • effective-mapping resolution (a saved per-(org,carrier,report) override wins over the report_pull
    default; absent override => the report is still mapped by the default),
  • the Activation-Order ↔ Order-Number LINKAGE count (a cheap post-upload indicator; recon is future).

The actual parse (CSV/Excel) + per-row column mapping + period derivation reuse report_pull.py
(parse_export_bytes / apply_column_map) — this module never re-implements them. The DB orchestration
(read existing keys, delete-by-month, insert, upload_trace) lives in router.py and calls these functions.

MONEY RULE: this whole track is INGEST-ONLY. Nothing here computes or triggers a payout. Once
raw_ma_commission fills, the Total residual/commission surfaces PRESENT the numbers to the owner before
any recalc.
"""
from collections import OrderedDict

# System/period columns stamped onto a mapped row that never participate in row IDENTITY.
_META_COLS = ("org_id", "source_id", "carrier_id", "id", "created_at",
              "period", "period_month", "period_year")

# ── per-report natural dedup keys (over DEST columns) ────────────────────────────────────────────
# Chosen from the raw_ma_* schemas (mig 083) + the report_pull column maps. A row's identity is the
# activation/order plus the line-identifying fields, so re-uploading the same file inserts ZERO new
# rows. If EVERY key field of a row is empty, a full-content signature is used as the fallback key so
# such a row still dedups deterministically (never silently duplicates, never silently collapses two
# genuinely different empties into one — the content sig covers all mapped columns).
DEDUP_KEYS = {
    # Activation Order + date + the line-identifying "component" (device/SIM/SKU/sub-type).
    "ma_commission":          ("activation_order", "tx_date", "imei", "sim", "sku", "sub_type"),
    # Order Number + date + the product line + its amount (one order → many product lines).
    "ma_daily_tx":            ("order_number", "tx_date", "product_name", "retail_cost", "merchant_invoice"),
    # Order Number + product + order date + qty (one order → many handset lines).
    "ma_marketplace_orders":  ("order_number", "product_name", "date_ordered", "number_ordered"),
    # the two calibration reports keep whole rows in raw_row → dedup on the report date + natural_key.
    "ma_sim_assignment":      ("report_date", "natural_key"),
    "ma_pr_activation":       ("report_date", "natural_key"),
}

# The dest column carrying each report's primary date (drives the per-row month split + the delete
# window). Mirrors report_pull param_spec.date_col / period_from; kept here so the pure logic is
# self-contained. resolve helpers below prefer the spec's date_col when a spec is supplied.
PERIOD_DATE_FIELD = {
    "ma_commission":         "tx_date",
    "ma_daily_tx":           "tx_date",
    "ma_marketplace_orders": "date_ordered",
    "ma_sim_assignment":     "report_date",
    "ma_pr_activation":      "report_date",
}


def _norm(v):
    """Normalize a value for identity: None/''/'nan' -> '', numbers canonicalized (strip trailing .0)."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none", "nat"):
        return ""
    if s.endswith(".0") and s[:-2].lstrip("-").isdigit():
        s = s[:-2]
    return s


def content_sig(row):
    """A deterministic signature over every non-meta dest column (sorted), used as the fallback key."""
    parts = []
    for k in sorted(row.keys()):
        if k in _META_COLS:
            continue
        parts.append(f"{k}={_norm(row.get(k))}")
    return "|".join(parts)


def natural_key(report_key, row):
    """The dedup key for a mapped row. Uses the report's DEDUP_KEYS; if every key field is empty, falls
    back to a full-content signature so nothing silently duplicates or collapses. Always a string."""
    cols = DEDUP_KEYS.get(report_key)
    if cols:
        vals = [_norm(row.get(c)) for c in cols]
        if any(vals):
            return report_key + "\x1f" + "\x1f".join(vals)
    return report_key + "\x1fSIG\x1f" + content_sig(row)


def dedupe_within(rows, report_key):
    """Collapse duplicate rows WITHIN one file (keep first). Returns (unique_rows, dropped_count)."""
    seen = set()
    out = []
    dropped = 0
    for r in rows:
        k = natural_key(report_key, r)
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        out.append(r)
    return out, dropped


def filter_new(existing_keys, rows, report_key):
    """APPEND mode: keep only rows whose key is not already present (existing_keys is a set), after an
    in-file dedupe. Returns (new_rows, dup_count) where dup_count = in-file dups + already-present."""
    deduped, within_dropped = dedupe_within(rows, report_key)
    new = []
    already = 0
    ek = existing_keys or set()
    for r in deduped:
        if natural_key(report_key, r) in ek:
            already += 1
            continue
        new.append(r)
    return new, within_dropped + already


def date_field_for(report_key, spec=None):
    """The dest date column for a report — spec.param_spec.date_col wins, else the local default."""
    if spec:
        ps = spec.get("param_spec") or {}
        dc = ps.get("date_col") or ps.get("period_from")
        if dc:
            return dc
    return PERIOD_DATE_FIELD.get(report_key)


def date_span(rows, date_col):
    """(min, max) 'YYYY-MM-DD' over the rows' date column, or (None, None)."""
    if not date_col:
        return None, None
    ds = [str(r.get(date_col))[:10] for r in rows if r.get(date_col)]
    ds = [d for d in ds if d and d.lower() not in ("nan", "none", "nat")]
    if not ds:
        return None, None
    return min(ds), max(ds)


def group_by_period(rows):
    """Split rows to their real months. apply_column_map already set row['period'] ('Month YYYY') per
    row from the date column, so this just buckets on it. Rows with no derivable period bucket under
    '' (still inserted, just uncounted per-month). Returns an OrderedDict period -> [rows], oldest key
    order preserved by first appearance."""
    out = OrderedDict()
    for r in rows:
        p = str(r.get("period") or "").strip()
        out.setdefault(p, []).append(r)
    return out


def period_counts(rows):
    """{'July 2026': 412, …} — per-period saved counts for upload_trace.periods."""
    counts = {}
    for r in rows:
        p = str(r.get("period") or "").strip() or "(no period)"
        counts[p] = counts.get(p, 0) + 1
    return counts


def date_counts(rows, date_col):
    """{'2026-07-01': 33, …} — per-date saved counts for upload_trace.date_counts."""
    if not date_col:
        return {}
    counts = {}
    for r in rows:
        d = str(r.get(date_col) or "")[:10]
        if not d or d.lower() in ("nan", "none", "nat"):
            continue
        counts[d] = counts.get(d, 0) + 1
    return counts


def detected_periods(rows, date_col):
    """Distinct 'Month YYYY' present in a parsed file (for the period dropdown / a multi-month preview).
    Prefers the derived row['period']; falls back to deriving from the date column. Sorted chronologically."""
    from datetime import datetime
    seen = {}
    for r in rows:
        p = str(r.get("period") or "").strip()
        key = None
        if p:
            key = p
        else:
            d = str(r.get(date_col) or "")[:10] if date_col else ""
            if len(d) >= 7 and d[:4].isdigit() and d[5:7].isdigit():
                try:
                    key = datetime(int(d[:4]), int(d[5:7]), 1).strftime("%B %Y")
                except Exception:
                    key = None
        if not key:
            continue
        if key not in seen:
            try:
                dt = datetime.strptime(key, "%B %Y")
            except Exception:
                dt = datetime.max
            seen[key] = dt
    return [k for k, _ in sorted(seen.items(), key=lambda kv: kv[1])]


# ── Activation Order ↔ Order Number linkage (cheap post-upload indicator; recon is future) ────────
def _order_values(rows, col):
    out = set()
    for r in rows:
        v = _norm(r.get(col))
        if v:
            out.add(v)
    return out


def linkage_counts(report_key, mapped_rows, other_order_numbers):
    """How many of THIS report's order keys are also present as MA Daily Tx order numbers (and back).
    `other_order_numbers` is a set of the counterpart table's order-number values (already normalized
    or normalizable). MA Commission joins on activation_order; MA Daily Tx joins on order_number.
    Returns {matched, unmatched, distinct} or None when the report isn't part of the join."""
    if report_key == "ma_commission":
        mine = _order_values(mapped_rows, "activation_order")
    elif report_key == "ma_daily_tx":
        mine = _order_values(mapped_rows, "order_number")
    else:
        return None
    other = {_norm(x) for x in (other_order_numbers or set()) if _norm(x)}
    matched = len(mine & other)
    return {"key": "Activation Order ↔ Order Number", "distinct": len(mine),
            "matched": matched, "unmatched": len(mine) - matched,
            "counterpart_rows_seen": len(other)}


# ── effective mapping resolution (saved manual override wins; else the report_pull default) ───────
def effective_column_map(saved_map, default_column_map):
    """The column_map the manual upload will use: a non-empty saved override wins, else the default."""
    if isinstance(saved_map, dict) and saved_map:
        return saved_map
    return default_column_map or {}


def mapping_status(saved_row, default_column_map):
    """Describe a report's mapping state for the per-carrier report list (item 1). Precedence:
    saved override > report_pull default > none. A user NEVER has to re-map what a default already covers."""
    saved_map = (saved_row or {}).get("column_map") if isinstance(saved_row, dict) else None
    if isinstance(saved_map, dict) and saved_map:
        return {"mapped": True, "source": "saved", "columns": len(saved_map),
                "saved_at": (saved_row or {}).get("updated_at"),
                "saved_by": (saved_row or {}).get("saved_by")}
    if isinstance(default_column_map, dict) and default_column_map:
        return {"mapped": True, "source": "default", "columns": len(default_column_map),
                "saved_at": None, "saved_by": None}
    return {"mapped": False, "source": "none", "columns": 0, "saved_at": None, "saved_by": None}


def target_field_catalog(column_map):
    """From a column_map ({source_header: dest|{col,type}}) build the list of DEST fields for the
    mapping UI (pick-don't-type): [{col, type, default_source}]. Deterministic order (dest col name)."""
    out = {}
    for src_h, spec_v in (column_map or {}).items():
        if isinstance(spec_v, dict):
            col, typ = spec_v.get("col"), (spec_v.get("type") or "text")
        else:
            col, typ = spec_v, "text"
        if not col:
            continue
        # keep the first source header seen as the default suggestion for this dest col
        out.setdefault(col, {"col": col, "type": typ, "default_source": src_h})
    return [out[c] for c in sorted(out.keys())]


def suggest_sources(headers, column_map):
    """Suggest, per DEST field, which uploaded header maps to it: exact (from the column_map) first,
    else a case/space-insensitive contains match on the dest col name. Returns {col: header|''}."""
    hnorm = {str(h).strip().lower(): str(h).strip() for h in (headers or []) if str(h).strip()}
    fields = target_field_catalog(column_map)
    out = {}
    for f in fields:
        col = f["col"]
        ds = str(f.get("default_source") or "").strip().lower()
        if ds and ds in hnorm:                       # the mapped header is present in the file
            out[col] = hnorm[ds]
            continue
        token = col.replace("_", "")
        hit = ""
        for low, orig in hnorm.items():
            if token and token in low.replace(" ", "").replace("_", ""):
                hit = orig
                break
        out[col] = hit
    return out


def build_column_map(field_sources, default_column_map):
    """Turn a {dest_col: chosen_source_header} UI selection into a column_map ({source_header:
    {col,type}}) to persist. Types are inherited from the default column_map so numeric/date casting is
    preserved; unknown dest cols default to text. Empty selections are dropped."""
    type_by_col = {}
    for spec_v in (default_column_map or {}).values():
        if isinstance(spec_v, dict) and spec_v.get("col"):
            type_by_col[spec_v["col"]] = (spec_v.get("type") or "text")
        elif isinstance(spec_v, str):
            type_by_col[spec_v] = "text"
    cm = {}
    for col, src in (field_sources or {}).items():
        src = str(src or "").strip()
        if not col or not src:
            continue
        typ = type_by_col.get(col, "text")
        cm[src] = {"col": col, "type": typ}
    return cm
