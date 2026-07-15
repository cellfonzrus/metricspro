"""Configurable report-pull mapping + ingest engine (RULE TWO: config, never hard-coded).

The VidaPay / T-CETRA "Billing Manager → Reports" page exposes several reports (MA Commission
Details, MA Daily Tx SubMA, MA Marketplace Handset Fulfillment Orders, Activation SIM Assignment,
PR Activation Details). WHICH report lands in WHICH table, and HOW each source header maps to a
destination column, is DATA — it lives in `commcalc.report_pull_map` (one row per report_key, per
org, with a house/default row every tenant inherits unless it has an override) and is editable from
the admin page /commcalc/report-mappings. Nothing about a carrier/tenant/report is branched in code.

This module is deliberately Playwright-free so the parse → column-map → ingest path is unit-testable
against synthetic CSVs (see the proof). vidapay_sweep.run_vidapay_sweep supplies the browser driver
(select report → postback → fill params → Submit → Export) and calls the pure functions here to map
+ ingest each downloaded export.

DEFAULT_REPORT_SPECS is the CANONICAL default set: it seeds `report_pull_map` (migration 207 mirrors
it) AND is the graceful fallback the engine uses when the table/seed is not yet present — so the pull
degrades to sane defaults instead of breaking if the migration hasn't run.
"""
from datetime import datetime
import calendar
import io

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"


# ── value casting (shared by every report; type comes from the column_map, not hard-coded) ──────
def _num(v):
    """Parse a currency/number cell to float; blank/NaN/'-'/'$1,234.5' all handled → 0.0 on failure."""
    if v is None:
        return 0.0
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return 0.0
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat", "-", "--"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        f = float(s)
        return -f if neg else f
    except (ValueError, TypeError):
        return 0.0


def to_date10(v):
    """Normalize a date cell to 'YYYY-MM-DD' (handles Excel serials AND date strings), else None."""
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    try:
        import pandas as pd
    except Exception:
        pd = None
    if pd is not None:
        try:
            ts = pd.to_datetime(float(s), origin="1899-12-30", unit="D")   # Excel serial
        except (ValueError, TypeError):
            ts = pd.to_datetime(s, errors="coerce")
        return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")
    # pandas-free fallback: try a few common formats
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s[:len(f) + 6], f).strftime("%Y-%m-%d")
        except Exception:
            continue
    return None


def _text(v):
    s = str(v if v is not None else "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s or None


def _colspec(spec_v):
    """A column_map value is either a bare dest-column string (text) or {'col':..,'type':text|num|date}."""
    if isinstance(spec_v, dict):
        return spec_v.get("col"), (spec_v.get("type") or "text").lower()
    return spec_v, "text"


def _cast(val, typ):
    if typ == "num":
        return _num(val)
    if typ == "date":
        return to_date10(val)
    return _text(val)


def _jsonable(v):
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    if v is None:
        return None
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


# ── month iteration (respects VidaPay's ≤1-month window + ≤1-year-back hard limits) ─────────────
def month_windows(start, end, interval_months=1):
    """Split [start, end] into calendar-month windows each ≤ interval_months long (VidaPay caps the
    MA Commission window at 1 month). Windows are clipped to the requested range. Returns a list of
    (win_start, win_end) datetimes, oldest first."""
    if start > end:
        start, end = end, start
    interval_months = max(1, int(interval_months or 1))
    wins = []
    cur = datetime(start.year, start.month, 1)
    guard = 0
    while cur <= end and guard < 240:
        guard += 1
        em = cur.month + interval_months - 1
        ey = cur.year + (em - 1) // 12
        em = (em - 1) % 12 + 1
        last = calendar.monthrange(ey, em)[1]
        w_end = datetime(ey, em, last, 23, 59, 0)
        cs = max(cur, datetime(start.year, start.month, start.day, 0, 0, 0))
        ce = min(w_end, end)
        wins.append((cs, ce))
        nm = em + 1
        ny = ey + (nm - 1) // 12
        nm = (nm - 1) % 12 + 1
        cur = datetime(ny, nm, 1)
    return wins


# ── export parsing (CSV preferred; Excel fallback) ──────────────────────────────────────────────
def parse_export_bytes(content, filename_or_ext=""):
    """Parse a downloaded report export (bytes) → list of row-dicts with whitespace-trimmed headers.
    CSV and Excel both supported; the export_pref decides which link the driver clicked."""
    if content is None:
        return []
    if isinstance(content, str):
        content = content.encode("utf-8", "replace")
    ext = (filename_or_ext or "").lower()
    import pandas as pd
    is_excel = ext.endswith(".xlsx") or ext.endswith(".xls") or content[:4] == b"PK\x03\x04" or content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    if is_excel:
        df = pd.read_excel(io.BytesIO(content))
    else:
        df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df.to_dict("records")


# ── column mapping (the config decides dest table + per-header dest column + type) ──────────────
def apply_column_map(rows, spec, org_id, source_id=None, carrier_id=None):
    """Map parsed source rows → destination-column dicts using spec['column_map'] (+ param_spec for
    period derivation). Stamps org_id (+ source_id/carrier_id). For a 'generic' report (unknown
    columns, e.g. the un-calibrated SIM/PR reports) the whole source row is preserved in raw_row so
    nothing is lost before the mapping is pinned. Skips all-empty rows."""
    column_map = spec.get("column_map") or {}
    ps = spec.get("param_spec") or {}
    has_period = ps.get("has_period", True)
    date_col = ps.get("date_col")
    period_from = ps.get("period_from") or date_col
    generic = bool(ps.get("generic"))
    out = []
    for r in rows:
        rr = {str(k).strip(): v for k, v in dict(r).items()}
        dest = {"org_id": org_id}
        if source_id is not None:
            dest["source_id"] = source_id
        if carrier_id is not None:
            dest["carrier_id"] = carrier_id
        for src_h, spec_v in column_map.items():
            col, typ = _colspec(spec_v)
            if not col:
                continue
            dest[col] = _cast(rr.get(src_h), typ)
        if generic:
            dest["raw_row"] = {k: _jsonable(v) for k, v in rr.items()}
            if date_col and not dest.get(date_col):
                # heuristic: first column that looks like a date
                for k, v in rr.items():
                    if any(t in k.lower() for t in ("date", "day")):
                        d = to_date10(v)
                        if d:
                            dest[date_col] = d
                            break
        # period derivation from the report's primary date
        if has_period:
            dv = dest.get(period_from) if period_from else None
            d10 = to_date10(dv) if dv else None
            if d10:
                y, m = int(d10[:4]), int(d10[5:7])
                dest["period"] = datetime(y, m, 1).strftime("%B %Y")
                dest["period_month"] = m
                dest["period_year"] = y
        meaningful = [v for k, v in dest.items()
                      if k not in ("org_id", "source_id", "carrier_id") and v not in (None, "", 0.0, {})]
        if meaningful:
            out.append(dest)
    return out


# ── idempotent ingest (delete-then-insert by this source's date window; never touches other rows) ─
def ingest_report_rows(client, org_id, target_table, mapped, *, source_id=None,
                       date_col=None, win_start=None, win_end=None, batch=500):
    """Delete-then-insert `mapped` into commcalc.<target_table>, scoped to (org_id, source_id) and the
    [win_start, win_end] date window, so a re-run of the SAME login/window replaces its own rows (no
    dup) and never disturbs another login's or a manual/email upload's rows (source_id NULL). Returns
    the number of rows inserted."""
    if not mapped:
        return 0
    win_start = str(win_start)[:10] if win_start else None
    win_end = str(win_end)[:10] if win_end else None
    # Expand the delete window to also cover the actual date span of the rows being inserted, so a
    # re-run clears EVERYTHING it is about to re-insert even if the portal spilled a few rows past the
    # requested window (otherwise those stragglers would duplicate on the next pull).
    if date_col:
        ds = [str(r.get(date_col))[:10] for r in mapped if r.get(date_col)]
        if ds:
            lo, hi = min(ds), max(ds)
            win_start = min(win_start, lo) if win_start else lo
            win_end = max(win_end, hi) if win_end else hi
    if date_col and win_start and win_end:
        try:
            d = (client.schema("commcalc").table(target_table).delete()
                 .eq("org_id", org_id).gte(date_col, win_start).lte(date_col, win_end))
            if source_id is not None:
                d = d.eq("source_id", source_id)
            d.execute()
        except Exception:
            pass
    n = 0
    for i in range(0, len(mapped), batch):
        chunk = mapped[i:i + batch]
        client.schema("commcalc").table(target_table).insert(chunk).execute()
        n += len(chunk)
    return n


# ── config resolution (org override wins over the house default; graceful fallback to defaults) ──
def default_specs(processor="vidapay"):
    """The canonical default report_pull_map rows (also mirrored into migration 207's seed)."""
    procs = ("vidapay", "total_access")
    return [dict(s) for s in DEFAULT_REPORT_SPECS
            if (processor is None or (s.get("processor") in procs))]


def resolve_report_specs(client, org_id, processor="vidapay", only_enabled=True):
    """Effective specs for this org: the org's own override row for a report_key wins, else the house
    default row, else (table/seed absent) the Python DEFAULT_REPORT_SPECS. Filtered to the processor
    family and (optionally) enabled rows."""
    rows = []
    try:
        rows = (client.schema("commcalc").table("report_pull_map").select("*")
                .in_("org_id", [HOUSE_ORG, org_id]).execute().data) or []
    except Exception:
        rows = []
    if not rows:
        rows = [{**s, "org_id": org_id, "_inherited": False} for s in default_specs(processor)]
    procs = ("vidapay", "total_access")
    by_key = {}
    for r in dict_rows(rows):
        if r.get("processor") and r.get("processor") not in procs and processor is not None:
            continue
        k = r.get("report_key")
        if not k:
            continue
        cur = by_key.get(k)
        is_override = (str(r.get("org_id")) == str(org_id))
        if cur is None:
            by_key[k] = r
        elif is_override and str(cur.get("org_id")) != str(org_id):
            by_key[k] = r
    specs = list(by_key.values())
    if only_enabled:
        specs = [s for s in specs if s.get("enabled", True)]
    specs.sort(key=lambda s: (s.get("sort_order") or 0, s.get("report_key") or ""))
    return specs


def dict_rows(rows):
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════════════════════════
# CANONICAL DEFAULTS — the 5 T-CETRA/VidaPay reports, calibrated 2026-07-15 (owner screenshots).
# column_map: source-header -> dest column (+type). param_spec: how to drive the page + iterate.
#   static field source 'account_id'/'session_id'  -> the data_source.account_id (Account_ID == SessionId)
#   date field   role 'start'/'end' + format        -> the month window boundary, formatted
# ════════════════════════════════════════════════════════════════════════════════════════════════
_N = "num"
_D = "date"

DEFAULT_REPORT_SPECS = [
    {
        "report_key": "ma_commission",
        "display_name": "MA - Commission Details",
        "target_table": "raw_ma_commission",
        "processor": "vidapay",
        "export_pref": "csv",
        "enabled": True,
        "sort_order": 10,
        "column_map": {
            "Date": {"col": "tx_date", "type": _D},
            "Time": "tx_time",
            "Carrier Name": "carrier_name",
            "Activation Order": "activation_order",
            "MerchantAccountId": "merchant_account_id",
            "IMEI": "imei", "SIM": "sim", "SKU": "sku",
            "Activation Type": "activation_type",
            "Activation Type 2": "activation_type2",
            "Sub Type": "sub_type",
            "Device Margin": {"col": "device_margin", "type": _N},
            "Consumer Margin": {"col": "consumer_margin", "type": _N},
            "Consumer Financing": {"col": "consumer_financing", "type": _N},
            "Rebate": {"col": "rebate", "type": _N},
            "Perfect Sale": "perfect_sale",
            "Wallet Funding Amount": {"col": "wallet_funding", "type": _N},
            "MRC Net Discount": {"col": "mrc_net_discount", "type": _N},
            "Fees": {"col": "fees", "type": _N},
            "Fees Margin": {"col": "fees_margin", "type": _N},
            "1st Month Spiff": {"col": "spiff_m1", "type": _N},
            "2nd Month Spiff": {"col": "spiff_m2", "type": _N},
            "3rd Month Spiff": {"col": "spiff_m3", "type": _N},
            "4th Month Spiff": {"col": "spiff_m4", "type": _N},
            "5th Month Spiff": {"col": "spiff_m5", "type": _N},
            "6th Month Spiff": {"col": "spiff_m6", "type": _N},
            "Port Status": "port_status",
            "ID Verification": "id_verification",
            "Is Financed": "is_financed",
            "User Id": "user_id", "User Name": "user_name",
            "BAN": "ban", "BIN": "bin", "POS Invoice": "pos_invoice",
            "Line Status": "line_status",
            "Status Change Date": "status_change_date",
            "Suspension Reason": "suspension_reason",
            "Consumer Value": {"col": "consumer_value", "type": _N},
            "Platform": "platform",
            "Platform Transaction Id": "platform_tx_id",
            "External Reference Id": "external_ref",
        },
        "param_spec": {
            "has_period": True, "date_col": "tx_date", "period_from": "tx_date",
            "iterate_months": True, "interval_months": 1, "max_months_back": 12,
            "submit_timeout_s": 300,
            "fields": [
                {"name": "Account_ID", "kind": "static", "source": "account_id"},
                {"name": "StartDate", "kind": "date", "role": "start", "format": "%m/%d/%Y %H:%M"},
                {"name": "EndDate", "kind": "date", "role": "end", "format": "%m/%d/%Y %H:%M"},
                {"name": "MonthIntervalLimit", "kind": "select", "literal": "1 Month"},
                {"name": "SessionId", "kind": "static", "source": "session_id"},
            ],
        },
    },
    {
        "report_key": "ma_daily_tx",
        "display_name": "MA Daily Tx SubMA",
        "target_table": "raw_ma_daily_tx",
        "processor": "vidapay",
        "export_pref": "csv",
        "enabled": True,
        "sort_order": 20,
        "column_map": {
            "Date of Transaction": {"col": "tx_date", "type": _D},
            "Date Due": {"col": "due_date", "type": _D},
            "Account ID": "account_id", "Account Name": "account_name",
            "Direct MA ID": "direct_ma_id", "Direct MA Name": "direct_ma_name",
            "Top MA ID": "top_ma_id", "Top MA Name": "top_ma_name",
            "Order Number": "order_number", "User": "user_name",
            "Order Type": "order_type", "Product Name": "product_name",
            "Retail Cost": {"col": "retail_cost", "type": _N},
            "Merchant Discount": {"col": "merchant_discount", "type": _N},
            "Merchant Invoice": {"col": "merchant_invoice", "type": _N},
        },
        "param_spec": {
            "has_period": True, "date_col": "tx_date", "period_from": "tx_date",
            "iterate_months": True, "interval_months": 1, "max_months_back": 12,
            "submit_timeout_s": 300,
            "fields": [
                {"name": "Session ID", "kind": "static", "source": "session_id"},
                {"name": "Master Agent ID", "kind": "static", "source": "blank", "optional": True},
                {"name": "Start Date", "kind": "date", "role": "start", "format": "%m/%d/%Y"},
                {"name": "End Date", "kind": "date", "role": "end", "format": "%m/%d/%Y"},
            ],
        },
    },
    {
        # MA - Marketplace Handset Fulfillment Orders. TARGET = the existing raw_ma_fulfillment (mig 083)
        # — the very report that table was built for (identical columns). mod-asset reads it through the
        # clean view commcalc.raw_ma_marketplace_orders (migration 207) to build the purchases/landing view.
        "report_key": "ma_marketplace_orders",
        "display_name": "MA - Marketplace Handset Fulfillment Orders",
        "target_table": "raw_ma_fulfillment",
        "processor": "vidapay",
        "export_pref": "csv",
        "enabled": True,
        "sort_order": 30,
        "column_map": {
            "Date Ordered": {"col": "date_ordered", "type": _D},
            "Date Filled": {"col": "date_filled", "type": _D},
            "Date Shipped": {"col": "date_shipped", "type": _D},
            "Order Number": "order_number", "Order Status": "order_status",
            "Order Type": "order_type", "TSPID": "tspid",
            "Business Name": "business_name", "Business Address": "business_address",
            "City": "city", "State": "state", "Zip": "zip",
            "Product Name": "product_name",
            "Number Ordered": {"col": "number_ordered", "type": _N},
            "Price": {"col": "price", "type": _N},
            "Tracking Number": "tracking_number",
        },
        "param_spec": {
            "has_period": False, "date_col": "date_ordered", "period_from": "date_ordered",
            "iterate_months": True, "interval_months": 1, "max_months_back": 12,
            "submit_timeout_s": 300,
            "fields": [
                {"name": "Start Date Ordered", "kind": "date", "role": "start", "format": "%m/%d/%Y"},
                {"name": "End Date Ordered", "kind": "date", "role": "end", "format": "%m/%d/%Y"},
                {"name": "Order Number", "kind": "static", "source": "blank", "optional": True},
                {"name": "Session ID", "kind": "static", "source": "session_id"},
            ],
        },
    },
    {
        # Params NOT screenshotted — GENERIC calibration spec: heuristic date fields + Session ID, whole
        # row preserved in raw_row, first live run returns a DOM diagnostic so the fields can be pinned.
        "report_key": "ma_sim_assignment",
        "display_name": "Activation SIM Assignment Report",
        "target_table": "raw_ma_sim_assignment",
        "processor": "vidapay",
        "export_pref": "csv",
        "enabled": True,
        "sort_order": 40,
        "column_map": {},
        "param_spec": {
            "has_period": True, "date_col": "report_date", "period_from": "report_date",
            "generic": True, "iterate_months": True, "interval_months": 1, "max_months_back": 6,
            "submit_timeout_s": 300, "calibration": True,
            "fields": [
                {"name": "Start Date", "kind": "date", "role": "start", "format": "%m/%d/%Y", "optional": True},
                {"name": "End Date", "kind": "date", "role": "end", "format": "%m/%d/%Y", "optional": True},
                {"name": "Session ID", "kind": "static", "source": "session_id", "optional": True},
            ],
        },
    },
    {
        "report_key": "ma_pr_activation",
        "display_name": "PR Activation Details",
        "target_table": "raw_ma_pr_activation",
        "processor": "vidapay",
        "export_pref": "csv",
        "enabled": True,
        "sort_order": 50,
        "column_map": {},
        "param_spec": {
            "has_period": True, "date_col": "report_date", "period_from": "report_date",
            "generic": True, "iterate_months": True, "interval_months": 1, "max_months_back": 6,
            "submit_timeout_s": 300, "calibration": True,
            "fields": [
                {"name": "Start Date", "kind": "date", "role": "start", "format": "%m/%d/%Y", "optional": True},
                {"name": "End Date", "kind": "date", "role": "end", "format": "%m/%d/%Y", "optional": True},
                {"name": "Session ID", "kind": "static", "source": "session_id", "optional": True},
            ],
        },
    },
]


def resolve_static(source, source_row):
    """Resolve a static param field's value from the data_source row. Account_ID and SessionId are the
    same value (the login's account_id) per the T-CETRA calibration; 'blank' means send an empty
    (optional) field."""
    sr = source_row or {}
    if source == "blank":
        return ""
    if source in ("account_id", "session_id", "sessionid", "account"):
        return str(sr.get("account_id") or "").strip()
    return str(sr.get(source) or "").strip()
