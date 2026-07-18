"""CommCalc API Router — all /api/v1/commcalc/* endpoints"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks, Header, Query
from fastapi.responses import JSONResponse
from typing import List, Optional
import pandas as pd
import io
import re
from app.core.database import get_supabase
from app.modules.commcalc.calculator import calc_rep_commissions, parse_period, safe_float, classify_contract_type
from app.modules.commcalc import whatif
from app.modules.commcalc.gp_report import calc_gp_report
from app.modules.commcalc.flags import calc_flags
from app.modules.commcalc.portout_flags import calc_portout_flags
from app.modules.commcalc.hotsheet_parser import parse_hotsheet
from app.modules.commcalc.discrepancy_engine import run_discrepancy
from app.modules.commcalc import targets_engine
from app.modules.commcalc import vip_sweep
from app.modules.commcalc import dlar_sweep
from app.modules.commcalc import epay_sweep
from app.modules.commcalc import installment_engine
from app.modules.commcalc import commission_engine
from app.modules.commcalc import sale_installment_engine
from app.modules.commcalc import b2b_sweep
from app.modules.commcalc import sales_analyzer
from app.modules.commcalc import sales_recon
from app.modules.commcalc import comp_trend
from app.modules.commcalc import carrier_map
from app.modules.commcalc import column_mapping
from app.modules.commcalc import commission_catalog
from app.modules.commcalc import ma_upload
from app.modules.commcalc import target_registry
from app.modules.commcalc import commission_ledger
from app.modules.commcalc import device_history
from app.modules.commcalc import custom_report
from app.modules.commcalc import productivity as _prod
from app.core.config import settings
from datetime import date as _date, timedelta as _timedelta, datetime as _datetime, timezone as _timezone
# Plain names too: 45+ call sites across this router use bare datetime/timezone/timedelta (all the
# classes — datetime.now/.fromisoformat, timezone.utc, timedelta(...)); without this they NameError
# when their branch executes (most sat in swallowed try/except, so it went unnoticed).
from datetime import datetime, timezone, timedelta
import calendar as _calendar
import threading


router = APIRouter(prefix="/commcalc", tags=["CommCalc"])

# ── Helper ───────────────────────────────────────────────────
ORG_ID = "00000000-0000-0000-0000-000000000001"


def sb():
    return get_supabase()

def require_org(org_id: str):
    if not org_id:
        raise HTTPException(400, "org_id required")


# ── UNIVERSAL UPLOAD TRACE (mig 202) ─────────────────────────────────────────────────────────────
# Which table each upload_type lands in — so a trace record can name the target table without threading
# the local TABLE_MAP through every caller. Kept in sync with upload_file's TABLE_MAP.
_TRACE_TARGET_TABLE = {
    "sales": "raw_sales", "daily_sales": "daily_sales_feed", "payment_detail": "raw_payment_detail",
    "mi_report": "raw_mi", "dlar_rep": "raw_dlar_rep", "dlar_store": "raw_dlar_store",
    "catalog": "raw_catalog", "master_cats": "raw_categories", "comp_report": "raw_comp_report",
    "ma_commission": "raw_ma_commission", "ma_daily_tx": "raw_ma_daily_tx",
    "ma_fulfillment": "raw_ma_fulfillment", "x_report": "pos_tender_summary",
    "inventory_aging": "inventory_aging",
}


def _write_upload_trace(org_id, *, source="manual", filename=None, upload_type=None, period="",
                        result=None, duration_ms=None, error=None, status=None):
    """Record ONE row in commcalc.upload_trace for an ingest attempt (the owner's debug-first surface).
    Best-effort and NON-RAISING: any failure (mig 202 unrun, transient DB error) is swallowed so it can
    never break an upload. `result` is the dict the ingest returned; a rich `result['_trace']`
    ({rows_in,target_table,periods,date_counts}) is used when present (the sales/daily money-path attaches
    it), otherwise coarse fields are derived from the result. `error` set ⇒ status='error'."""
    try:
        res = result if isinstance(result, dict) else {}
        tr = res.get("_trace") if isinstance(res.get("_trace"), dict) else {}
        # rows saved: the sales/daily path returns 'saved'; x_report 'tenants'; inventory 'saved'; custom 'rows'.
        rows_saved = res.get("saved")
        if rows_saved is None:
            rows_saved = res.get("tenants", res.get("stores", res.get("rows")))
        skipped = res.get("skipped") or (None if res.get("success", True) else res.get("skipped"))
        if status is None:
            if error:
                status = "error"
            elif res.get("skipped") == "price_guard_partial":
                status = "partial"
            elif res.get("skipped") or res.get("success") is False:
                status = "skipped"
            else:
                status = "ok"
        guard = res.get("shrink") or res.get("guarded_dates") or res.get("guard") or res.get("note")
        row = {
            "org_id": org_id, "source": source, "filename": filename,
            "upload_type": upload_type,
            "target_table": tr.get("target_table") or _TRACE_TARGET_TABLE.get(upload_type),
            "rows_in": tr.get("rows_in"),
            "rows_saved": (int(rows_saved) if isinstance(rows_saved, (int, float)) else None),
            "status": status, "skipped": (str(skipped) if skipped else None),
            "guard": guard if isinstance(guard, (list, dict)) else ({"note": guard} if guard else None),
            "periods": tr.get("periods"), "date_counts": tr.get("date_counts"),
            "duration_ms": (int(duration_ms) if duration_ms is not None else None),
            "note": (str(res.get("note"))[:500] if res.get("note") else None),
            "error": (str(error)[:800] if error else None),
        }
        sb().schema("commcalc").table("upload_trace").insert(row).execute()
    except Exception as e:
        print(f"WARN upload_trace insert skipped (run mig 202?): {e}")


def _month_year(period: str):
    """Parse either 'June 2026' or '2026-06' → (month, year). (0,0) if unrecognized.
    calculator.parse_period only handles the month-name form and silently returns January
    for '2026-06', so endpoints that may receive either spelling use this instead."""
    s = (period or "").strip()
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-":
        try:
            return int(s[5:7]), int(s[:4])
        except Exception:
            return 0, 0
    pm = parse_period(s)
    return pm.get("month", 0), pm.get("year", 0)

def _pvariants(period):
    """All known spellings of a month-period so a query matches data stored as EITHER 'June 2026' or
    '2026-06' (spelling-agnostic — the recurring period-mismatch class of bug). Returns the original
    plus both canonical forms, deduped; falls back to [period] if it can't be parsed as a month-period
    (so non-month values pass through unchanged). Use `.in_('period', _pvariants(period))` in place of
    `.in_('period', _pvariants(period))`. Safe for both reads (superset match) and same-month delete-then-insert."""
    p = str(period or "").strip()
    # STRICT parse (don't lean on parse_period — it leniently maps unknown input to January). Only a
    # clearly-valid month-period is expanded; anything else passes through unchanged → behaves exactly
    # like the old .eq (and never 500s on empty/malformed input).
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        mo, yr = int(p[5:7]), int(p[:4])
    else:
        parts = p.split()
        names = {m.lower(): i for i, m in enumerate(_calendar.month_name) if m}
        if len(parts) == 2 and parts[0].lower() in names and parts[1].isdigit():
            mo, yr = names[parts[0].lower()], int(parts[1])
        else:
            return [p]
    if not (1 <= mo <= 12 and yr):
        return [p]
    return list({p, f"{_calendar.month_name[mo]} {yr}", f"{yr}-{mo:02d}"})

def _canon_period(period):
    """The SINGLE canonical 'Month YYYY' spelling of a month-period — what the sweeps and the existing
    May/June calc_status + rep_commissions rows use. So '2026-07' and 'July 2026' collapse to one key
    (calc_status upserts key on this to avoid two divergent status rows for the same month). Reuses the
    file's _month_year helper; anything that can't be parsed as a month-period passes through unchanged."""
    mo, yr = _month_year(period)
    return f"{_calendar.month_name[mo]} {yr}" if (1 <= mo <= 12 and yr) else str(period or "").strip()

def _flatten_grouped_sales(df):
    """Flatten a B2B Soft GROUPED 'Sales Transaction Details (Legacy)' export. In the grouped layout
    Store and Trans ID are GROUP-HEADER rows — the first column reads 'Store: <addr>' / 'Trans ID:
    <id>' instead of being columns — and each transaction is followed by a numeric SUBTOTAL row
    (empty first column). We fill Store + Trans ID DOWN onto every detail line and drop the header +
    subtotal rows, yielding the flat shape the mapper expects (Store + Trans ID columns present).

    NO-OP on an already-flat file (the standard 78-col export, or any frame with no 'Store:'/'Trans ID:'
    header rows) — it's returned unchanged, so the existing upload path has zero regression. The Legacy
    export still omits Contract Type + Department; those stay inferred/empty downstream as before."""
    if df is None or df.empty:
        return df
    first_col = df.columns[0]
    col0 = df[first_col].astype(str)
    if not col0.str.startswith("Store:").any() and not col0.str.startswith("Trans ID:").any():
        return df  # already flat — leave untouched
    cur_store, cur_tid, rows = None, None, []
    for rec in df.to_dict("records"):
        c0 = str(rec.get(first_col, "")).strip()
        if c0.startswith("Store:"):
            cur_store = c0[len("Store:"):].strip(); continue
        if c0.startswith("Trans ID:"):
            cur_tid = c0[len("Trans ID:"):].strip(); continue
        if not c0:
            continue  # per-transaction subtotal / blank row (no timestamp)
        rec["Store"] = cur_store or rec.get("Store", "")
        rec["Trans ID"] = cur_tid or rec.get("Trans ID", "")
        rows.append(rec)
    return pd.DataFrame(rows) if rows else df


# ── POS X-Report parser (multi-sheet: one SHEET PER STORE, tender matrix) ────────────────────────
_XR_TENDERS = {"cash", "check", "credit card", "gift card", "store account",
               "debit card", "credit", "debit", "card",
               "acima", "acima lease", "acima leasing", "acima (lease)", "lease"}


def _parse_xreport(contents: bytes, filename: str, fallback_date: str = None):
    """Parse the POS 'X-Report' workbook, which is ONE SHEET PER STORE (sheet name = store address),
    each holding a 'Tendered Amounts' matrix (Tender Types rows × Sales..Net columns). Returns
    [(store, date_iso, tender_type, net_amount)]. The date is the filename range
    X-Report_MMDDYYYY-MMDDYYYY — which MUST be a single day (start==end); a multi-day range raises
    ValueError (an X-Report reconciles ONE day's drawer). With no filename date, uses fallback_date
    (YYYY-MM-DD, e.g. the day being viewed) else the business-local date.
    Returns [] if the workbook isn't this format (caller then tries the generic flat parser)."""
    import re as _re
    m = _re.search(r'(\d{2})(\d{2})(\d{4})\s*-\s*(\d{2})(\d{2})(\d{4})', filename or "")
    if m:
        if (m.group(1), m.group(2), m.group(3)) != (m.group(4), m.group(5), m.group(6)):
            raise ValueError(
                f"X-Report must be for a SINGLE day — this file covers a range "
                f"({m.group(1)}/{m.group(2)}/{m.group(3)} – {m.group(4)}/{m.group(5)}/{m.group(6)}). "
                f"Re-run the X-Report for one day and upload that.")
        date_iso = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"   # single day (YYYY-MM-DD)
    elif fallback_date and len(str(fallback_date)) >= 10 and str(fallback_date)[4] == "-":
        date_iso = str(fallback_date)[:10]
    else:
        try:
            from zoneinfo import ZoneInfo
            date_iso = datetime.now(timezone.utc).astimezone(
                ZoneInfo(settings.BUSINESS_TZ or "America/New_York")).date().isoformat()
        except Exception:
            date_iso = datetime.now(timezone.utc).date().isoformat()
    try:
        sheets = pd.read_excel(io.BytesIO(contents), sheet_name=None, header=None, dtype=str)
    except Exception:
        return []
    out = []
    for sheet_name, df in sheets.items():
        store = str(sheet_name).strip()
        rows = df.fillna('').values.tolist()
        hdr_idx, net_col = None, None
        for i, r in enumerate(rows):
            low = [str(c).strip().lower() for c in r]
            # the DETAILED tender header (not the super-header) carries 'refunds'/'sub net' + 'net'
            if "tender types" in low and "net" in low and ("refunds" in low or "sub net" in low):
                hdr_idx = i
                net_col = max(j for j, c in enumerate(low) if c == "net")
                break
        if hdr_idx is None:
            continue
        for r in rows[hdr_idx + 1:]:
            cells = [str(c).strip() for c in r]
            label = (cells[0].lower() if cells else "")
            if not label or label == "0" or label not in _XR_TENDERS:
                break   # blank row / next section ends the tender block
            amt = safe_float(cells[net_col]) if net_col < len(cells) else 0.0
            out.append((store, date_iso, cells[0], amt))
    return out


# Row-count guardrail thresholds (user 2026-07-05): flag an ingest that SHRINKS a day/period which
# previously held real data — the fingerprint of a truncated/partial export. Detection lives in
# upload_file (all ingest paths); the email sweep escalates a hit to a WhatsApp/email alert.
_SHRINK_MIN_PRIOR = 100   # only guard a key that already had a meaningful row count (avoids day-start noise)
_SHRINK_RATIO = 0.5       # alert when the incoming count is < 50% of what it replaces


# ── Built-in vs. self-serve custom import types ──────────────────────────────────────────────
# The seeded, hard-coded parsers upload_file knows. Anything NOT here is treated as a user-defined
# custom sheet (migration 099) IF it's registered in report_definitions — captured generically as
# JSONB, no code. Kept as a module constant so upload_file and the custom-type endpoints agree.
BUILTIN_UPLOAD_TYPES = ["sales", "daily_sales", "payment_detail", "mi_report", "dlar_rep", "dlar_store",
                        "catalog", "master_cats", "comp_report", "inventory_aging", "x_report",
                        "ma_commission", "ma_daily_tx", "ma_fulfillment"]  # Total/VidaPay MA reports (mig 083)
# Derived / summary b2b reports that have NO importer — a filename sweep may match them but we skip them
# cleanly (not a hard error) so one un-ingestable attachment doesn't show as a failed sweep. A tenant that
# wants one captured can register it under Data Imports → Custom Reports (report_definitions).
KNOWN_IGNORED_TYPES = {"sales_trend"}
CUSTOM_IMPORT_TABLE = "raw_custom_import"


def _custom_report_def(client, org_id, report_key):
    """Return the report_definitions row for a USER-DEFINED custom-capture sheet (generic JSONB), else
    None. A custom sheet is one whose target_table is the catch-all or whose upload_endpoint is 'custom'
    — created self-serve on Email/FTP Imports, never a built-in/column-mapped report."""
    if not report_key:
        return None
    try:
        rows = (client.schema("commcalc").table("report_definitions").select("*")
                .eq("org_id", org_id).eq("report_key", report_key).limit(1).execute().data) or []
    except Exception:
        return None
    rd = rows[0] if rows else None
    if rd and (rd.get("upload_endpoint") == "custom" or rd.get("target_table") == CUSTOM_IMPORT_TABLE):
        return rd
    return None


async def _ingest_custom_report(report_key, file, period, org_id, rdef=None):
    """Generic JSONB capture for a self-serve custom sheet (migration 099). Reads the sheet and stores
    every row verbatim into commcalc.raw_custom_import keyed by report_key — same guards as the other
    importers: never wipe on an empty/unreadable file, replace-by-period (or by filename when periodless)
    so a re-import is idempotent, batched insert, upload_log. Returns {saved, ...} like upload_mapped so
    the FTP/email sweeps read res['saved'] uniformly."""
    client = sb()
    contents = await file.read()
    fname = getattr(file, "filename", "") or ""
    try:
        df = _read_upload_df(contents, fname)
    except Exception as e:
        raise HTTPException(400, f"Could not read file for '{report_key}': {e}")
    # Honor the report's period_mode: 'none' → capture periodless (ignore any month the sweep supplied).
    if (rdef or {}).get("period_mode") == "none":
        period = ""
    pm = parse_period(period) if period else {"month": 0, "year": 0}
    records = df.to_dict("records")
    rows = []
    for i, r in enumerate(records):
        data = {str(k).strip(): ("" if v is None else str(v)) for k, v in r.items() if str(k).strip()}
        if not any(str(v).strip().lower() not in ("", "nan", "none") for v in data.values()):
            continue  # skip a fully-blank row
        rows.append({"org_id": org_id, "report_key": report_key, "period": period or None,
                     "period_month": pm["month"] or None, "period_year": pm["year"] or None,
                     "source_filename": fname or None, "row_index": i, "data": data})
    if not rows:
        # Empty/misaligned file → never wipe what's already captured.
        return {"saved": 0, "report_key": report_key, "target_table": CUSTOM_IMPORT_TABLE, "period": period,
                "note": "no data rows found — nothing captured (existing data preserved)"}
    # Replace prior rows for this sheet+period (or sheet+filename when periodless) so re-imports don't stack.
    try:
        q = (client.schema("commcalc").table(CUSTOM_IMPORT_TABLE).delete()
             .eq("org_id", org_id).eq("report_key", report_key))
        q = q.in_("period", _pvariants(period)) if period else q.eq("source_filename", fname or "")
        q.execute()
    except Exception as e:
        raise HTTPException(500, f"Failed to clear existing custom-import rows for '{report_key}': {e}")
    saved = 0
    for i in range(0, len(rows), 500):
        try:
            client.schema("commcalc").table(CUSTOM_IMPORT_TABLE).insert(rows[i:i + 500]).execute()
            saved += len(rows[i:i + 500])
        except Exception as e:
            raise HTTPException(500, f"Insert into {CUSTOM_IMPORT_TABLE} failed at row {i}: {e}")
    try:
        client.schema("commcalc").table("upload_log").insert(
            {"org_id": org_id, "file_type": report_key, "period": period or None,
             "filename": fname or None, "rows_saved": saved}).execute()
    except Exception as e:
        print(f"WARN upload_log insert failed: {e}")
    return {"saved": saved, "report_key": report_key, "target_table": CUSTOM_IMPORT_TABLE,
            "period": period, "rows_read": len(records)}


# ── Upload endpoints ─────────────────────────────────────────
@router.post("/upload/{file_type}")
async def upload_file(
    file_type: str,
    file: UploadFile = File(...),
    period: str = "",
    force: bool = False,
    close_date: str = "",
    org_id: str = "00000000-0000-0000-0000-000000000001",
    trace_source: str = "manual",
):
    """Upload a data file (sales, payment_detail, mi, dlar_rep, dlar_store, catalog).

    THIN TRACED WRAPPER (mig 202): the real work is in `_upload_file_impl`; this records exactly ONE
    `upload_trace` row per call — for the manual route AND for the email/FTP sweeps that call this
    directly — capturing which org the rows landed in, rows-in vs saved, per-period/per-day counts, the
    guard outcome, duration, and any exception (so a failed upload is traced too). `trace_source` is a
    harmless optional query param the sweeps set ('email_sweep'/'ftp_sweep'); the UI never sends it →
    stays 'manual'. Never lets a trace failure affect the upload result."""
    import time as _t_up
    _t0 = _t_up.monotonic()
    _fname = getattr(file, "filename", None)
    _res = None
    _err = None
    try:
        _res = await _upload_file_impl(file_type, file, period, force, close_date, org_id)
        # Return a clean copy WITHOUT the internal `_trace` payload; `_write_upload_trace` (finally) still
        # sees the rich `_trace` because it reads `_res`, not the returned copy.
        return {k: v for k, v in _res.items() if k != "_trace"} if isinstance(_res, dict) else _res
    except HTTPException as he:
        _err = f"{getattr(he, 'status_code', 500)}: {str(he.detail)[:400]}"
        raise
    except Exception as e:
        _err = str(e)[:400]
        raise
    finally:
        _write_upload_trace(org_id, source=trace_source, filename=_fname, upload_type=file_type,
                            period=period, result=_res,
                            duration_ms=int((_t_up.monotonic() - _t0) * 1000), error=_err)


async def _upload_file_impl(
    file_type: str,
    file: UploadFile = File(...),
    period: str = "",
    force: bool = False,
    close_date: str = "",
    org_id: str = "00000000-0000-0000-0000-000000000001"
):
    """Upload a data file (sales, payment_detail, mi, dlar_rep, dlar_store, catalog).

    For comp_report, the selected `period` is checked against the month the file's rows actually
    belong to (their Begin Date); a mismatch is rejected (pass force=true to override) so a file
    can't be mislabeled into the wrong month — the bug that wiped a month's residual trend."""
    require_org(org_id)

    SUPPORTED = BUILTIN_UPLOAD_TYPES
    if file_type not in SUPPORTED:
        # Self-serve custom sheet (report_definitions, target_table=raw_custom_import / upload_endpoint=
        # 'custom') → generic JSONB capture, no code. Both sweeps AND manual upload reach it through here,
        # so a user can add a new auto-import sheet (e.g. B2B "Sales Trend") without a code change.
        _rdef = _custom_report_def(sb(), org_id, file_type)
        if _rdef:
            return await _ingest_custom_report(file_type, file, period, org_id, _rdef)
        if file_type in KNOWN_IGNORED_TYPES:
            return {"status": "skipped", "file_type": file_type, "rows": 0,
                    "reason": f"'{file_type}' is a derived report with no importer — ignored. "
                              f"Register it under Data Imports → Custom Reports to capture it."}
        raise HTTPException(400, f"Unknown file type: {file_type}. Supported: {SUPPORTED}")
    
    contents = await file.read()
    fname = (getattr(file, "filename", "") or "").lower()
    try:
        if fname.endswith((".csv", ".txt")):
            # b2bsoft (and most Windows POS) export CSVs as Windows-1252, not UTF-8 — byte 0x96 (en-dash)
            # makes the default read_csv fail. Try UTF-8, then cp1252, then latin-1 (maps all 256 bytes so
            # it never raises) — so a non-UTF-8 export imports instead of erroring on one stray character.
            df = None
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    df = pd.read_csv(io.BytesIO(contents), dtype=str, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
        else:
            df = pd.read_excel(io.BytesIO(contents), dtype=str)
    except Exception as e:
        raise HTTPException(400, f"Could not read file ({fname or 'upload'}): {e}")

    df = df.fillna('')

    # B2B Soft can only schedule the GROUPED 'Sales Transaction Details (Legacy)' export — Store and
    # Trans ID come as group-header rows, not columns. Flatten so each detail line carries its Store +
    # Trans ID (no-op on already-flat files). Lets the emailed/swept Legacy file ingest without a
    # manual flatten. See email_sweep / ftp_sweep routing.
    if file_type in ("sales", "daily_sales"):
        df = _flatten_grouped_sales(df)

    # ── Validate file matches the expected slot ──────────────────
    SIGNATURES = {
        'sales':          ['Salesperson', 'Trans ID'],
        'daily_sales':    ['Salesperson', 'Trans ID'],
        'payment_detail': ['Payment Type', 'Amount'],
        'mi_report':      ['SalesForceID', 'Subscriber Status'],
        'dlar_rep':       ['Advocate Name', 'ATU %'],
        'dlar_store':     ['Salesforce ID', 'Family Plan %'],
        'catalog':        ['Product ID', 'Cost'],
        'master_cats':    ['description'],
        'comp_report':    ['Compensation Type', 'Payment Amount'],
        # Total / VidaPay Master-Agent portal exports (mig 083)
        'ma_commission':  ['MerchantAccountId', 'Activation Type'],
        'ma_daily_tx':    ['Order Number', 'Retail Cost'],
        'ma_fulfillment': ['TSPID', 'Tracking Number'],
    }
    cols = set(str(col).strip() for col in df.columns)
    expected = SIGNATURES.get(file_type, [])
    missing = [col for col in expected if col not in cols]
    if missing:
        raise HTTPException(
            400,
            f"This doesn't look like the right file for '{file_type}'. "
            f"Missing expected column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(sorted(cols))[:200]}"
        )

    rows = df.to_dict('records')

    pm = parse_period(period) if period else {'month': 0, 'year': 0}
    has_period = file_type not in ['catalog', 'master_cats']

    client = sb()

    # Inventory Aging (b2bsoft / any POS): a per-store inventory-value snapshot → commcalc.inventory_value
    # (upsert per store; manual overrides preserved). This is NOT a period table — handle it here and
    # return before the period/TABLE_MAP flow. Auto-importable via the email/FTP sweep like the other
    # reports (the sweep routes a matching attachment to upload_file with this file_type).
    if file_type == 'inventory_aging':
        from app.modules.commcalc import b2b_sweep
        store_values = b2b_sweep.normalize_inventory(rows)
        as_of = datetime.now(timezone.utc).date().isoformat()
        for dk in ('As Of', 'AsOf', 'as_of', 'AsOfDate', 'Snapshot Date', 'Date'):
            v = (rows[0].get(dk) if rows else None)
            if v:
                as_of = str(v)[:10]
                break
        saved = b2b_sweep.write_inventory_values(client, org_id, store_values, as_of)
        # v2 (owner directive 2026-07-17): the SAME file carries a per-DEVICE cost the device-history
        # lookup needs → also persist per-device rows (imei/serial + unit_cost + received/aging date)
        # into commcalc.inventory_aging_device. Additive + org-scoped; degrades gracefully until mig 216
        # is run (a missing table must NOT break the per-store inventory_value ingest above).
        devices_saved = 0
        try:
            devices = b2b_sweep.extract_inventory_devices(rows, as_of_date=as_of)
            if devices:
                devices_saved = b2b_sweep.write_inventory_devices(client, org_id, devices, as_of)
        except Exception as e:
            print(f"WARN inventory_aging per-device persist skipped (mig 216 pending?): {e}")
        try:
            client.schema('commcalc').table('upload_log').insert(
                {'org_id': org_id, 'file_type': 'inventory_aging', 'period': as_of,
                 'filename': getattr(file, 'filename', None), 'rows_saved': saved}).execute()
        except Exception:
            pass
        # HONEST-ZERO: a 0-store parse USED to return success:True with a soft note → the email sweep
        # recorded status='ok', rows_saved=0 (a silent green ✓ on a file that ingested NOTHING — every
        # one of luxelink's 9 hourly Inventory Aging.csv ingests, 2026-07-14). Return a DISTINCT
        # skipped outcome that names exactly which columns we looked for vs what the file actually has,
        # so the email-imports history (which renders skipped/error) shows what's wrong instead of a ✓.
        # `saved` is carried on BOTH branches so the sweep's rows_saved read is honest (a successful
        # ingest now records N, not 0, so its dedup finally marks it done instead of re-pulling hourly).
        if not saved and not devices_saved:
            diag = b2b_sweep.inventory_diagnostics(rows)
            ddiag = b2b_sweep.device_diagnostics(rows)
            found = ', '.join(str(c) for c in diag['columns'][:25]) or '(no columns — empty/misread file)'
            note = (f"parsed 0 stores + 0 devices from {diag['n_rows']} row(s) — need either a STORE "
                    f"column (Store / Store Name / Location / Site / Branch) + a VALUE column "
                    f"(Cost / Ext Cost / Total Value), OR a per-device IMEI/Serial + Cost column. "
                    f"Found columns: {found}."
                    + (" A grouped 'Store:' header was seen but no priced detail rows followed it."
                       if diag['grouped'] else "")
                    + (f" (imei col: {ddiag['imei_col'] or ddiag['serial_col'] or 'none'}, "
                       f"cost col: {ddiag['cost_col'] or 'none'})"))
            return {'success': False, 'file_type': 'inventory_aging', 'stores': 0, 'saved': 0,
                    'devices': 0, 'skipped': 'inventory_no_stores', 'as_of': as_of,
                    'rows_read': len(rows), 'note': note}
        return {'success': True, 'file_type': 'inventory_aging', 'stores': saved, 'saved': saved,
                'devices': devices_saved, 'as_of': as_of, 'rows_read': len(rows)}

    # POS "X report": daily takings BY TENDER TYPE per store → commcalc.pos_tender_summary, for the tender
    # reconciliation against the daily closing sheet. Flexible column detection (any POS). Periodless.
    if file_type == 'x_report':
        # First try the real B2B Soft X-Report: a MULTI-SHEET workbook (one sheet per store, tender
        # matrix), which the generic flat parser below can't read. Falls through if not that shape.
        # A multi-day filename range is rejected (400) — an X-Report reconciles ONE day.
        try:
            xr = _parse_xreport(contents, fname, fallback_date=close_date or None)
        except ValueError as _e:
            raise HTTPException(400, str(_e))
        if xr:
            saved = 0
            for (store, d, tender) , amount in {(s, dd, t): a for (s, dd, t, a) in xr}.items():
                try:
                    client.schema('commcalc').table('pos_tender_summary').upsert(
                        {"org_id": org_id, "close_date": d, "store": store, "tender_type": tender,
                         "tender_class": ("cash" if "cash" in tender.lower() else
                                          ("card" if any(k in tender.lower() for k in
                                           ("credit", "debit", "card", "visa", "master", "amex", "discover")) else "other")),
                         "amount": amount, "source": "x_report",
                         "updated_at": datetime.now(timezone.utc).isoformat()},
                        on_conflict="org_id,close_date,store,tender_type").execute()
                    saved += 1
                except Exception:
                    pass
            try:
                client.schema('commcalc').table('upload_log').insert(
                    {'org_id': org_id, 'file_type': 'x_report',
                     'period': (xr[0][1] if xr else None), 'filename': getattr(file, 'filename', None),
                     'rows_saved': saved}).execute()
            except Exception:
                pass
            return {'success': True, 'file_type': 'x_report', 'tenders': saved,
                    'stores': len({s for (s, _d, _t, _a) in xr}), 'date': (xr[0][1] if xr else None),
                    'format': 'multi-sheet'}
        def _pick(r, cands):
            for c in cands:
                if c in r and str(r.get(c)).strip().lower() not in ("", "nan", "none"):
                    return r.get(c)
            return None
        STORE_K = ("store", "Store", "location", "Location", "store_name", "StoreName", "Site", "Register", "register")
        DATE_K = ("close_date", "Close Date", "date", "Date", "Business Date", "BusinessDate", "trans_date", "Trans Date")
        TENDER_K = ("tender_type", "Tender Type", "tender", "Tender", "payment_type", "Payment Type", "Payment", "Type", "Media", "media")
        AMT_K = ("amount", "Amount", "total", "Total", "value", "Value", "net", "Net", "Net Amount", "amt", "Amt")

        def _tclass(t):
            t = (t or "").lower()
            if "cash" in t:
                return "cash"
            if any(k in t for k in ("credit", "debit", "card", "visa", "master", "amex", "discover", "cc", "chip", "emv")):
                return "card"
            return "other"
        # Stamp the BUSINESS-local date (not UTC): the X report is swept in the evening (~6:50 PM ET),
        # which is already the next UTC day part of the year — a UTC stamp would file it under tomorrow.
        if close_date and len(close_date) >= 10 and close_date[4] == "-":
            default_date = close_date[:10]
        else:
            try:
                from zoneinfo import ZoneInfo
                default_date = datetime.now(timezone.utc).astimezone(
                    ZoneInfo(settings.BUSINESS_TZ or "America/New_York")).date().isoformat()
            except Exception:
                default_date = datetime.now(timezone.utc).date().isoformat()
        agg = {}
        current_store, current_date = None, None
        for r in rows:
            store, tender, amt = _pick(r, STORE_K), _pick(r, TENDER_K), _pick(r, AMT_K)
            d = _pick(r, DATE_K)
            # GROUPED-BY-STORE exports put the store (and sometimes the date) on a section-HEADER row,
            # with the tender lines beneath carrying no store — forward-fill from the last seen header.
            if store is not None and str(store).strip():
                current_store = str(store).strip()
            if d is not None and str(d).strip():
                current_date = str(d)[:10]
            if tender is None or str(amt).strip() in ("", "nan", "none"):
                continue   # a store/date header row, or a blank/subtotal line with no tender
            use_store = str(store).strip() if (store is not None and str(store).strip()) else current_store
            if not use_store:
                continue
            use_date = str(d)[:10] if (d is not None and str(d).strip()) else (current_date or default_date)
            key = (use_store, use_date, str(tender).strip())
            agg[key] = round(agg.get(key, 0.0) + safe_float(amt), 2)
        saved = 0
        for (store, d, tender), amount in agg.items():
            try:
                client.schema('commcalc').table('pos_tender_summary').upsert(
                    {"org_id": org_id, "close_date": d, "store": store, "tender_type": tender,
                     "tender_class": _tclass(tender), "amount": amount, "source": "x_report",
                     "updated_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="org_id,close_date,store,tender_type").execute()
                saved += 1
            except Exception:
                pass
        try:
            client.schema('commcalc').table('upload_log').insert(
                {'org_id': org_id, 'file_type': 'x_report', 'period': default_date,
                 'filename': getattr(file, 'filename', None), 'rows_saved': saved}).execute()
        except Exception:
            pass
        return {'success': True, 'file_type': 'x_report', 'tenders': saved, 'rows_read': len(rows),
                'note': (None if saved else "No tender rows found — the X report needs a store, a tender/"
                         "payment-type column, and an amount column (run migration 062 if just added).")}

    # Determine target table
    TABLE_MAP = {
        "sales": "raw_sales",
        "payment_detail": "raw_payment_detail", 
        "mi_report": "raw_mi",
        "dlar_rep": "raw_dlar_rep",
        "dlar_store": "raw_dlar_store",
        "catalog": "raw_catalog",
        "master_cats": "raw_categories",
        "comp_report": "raw_comp_report",
        # daily B2B feed lands in its OWN table so the monthly 'sales' period-replace never wipes it;
        # the two are reconciled at trans_id grain (GET /commcalc/sales-recon). See migration 047.
        "daily_sales": "daily_sales_feed",
        # Total / VidaPay Master-Agent reports (mig 083) — the Total-side MI/ATU equivalents
        "ma_commission": "raw_ma_commission",
        "ma_daily_tx": "raw_ma_daily_tx",
        "ma_fulfillment": "raw_ma_fulfillment",
    }
    # Try schema-qualified first, fall back to public prefix
    table = TABLE_MAP[file_type]
    
    # Guard against mislabeling a comp file into the wrong month (which deletes the chosen period
    # then loads foreign data under it — how a month's comp got clobbered). The file's true month
    # comes from its rows' Begin Date; reject a mismatch unless explicitly forced.
    if file_type == 'comp_report' and period and not force:
        derived = epay_sweep.comp_period_from_records(rows)
        if derived and derived[0].strip().lower() != period.strip().lower():
            raise HTTPException(
                400,
                f"This comp file's data is for '{derived[0]}', but you selected period '{period}'. "
                f"Uploading it as '{period}' would overwrite that month with the wrong data. "
                f"Re-select '{derived[0]}', or pass force=true if you really intend this.")

    # NOTE: the delete-existing step is deliberately DEFERRED to after the rows are mapped (below),
    # and guarded by `if mapped`. Deleting first meant a file that mapped to ZERO rows (a column-name
    # drift that still passed the loose signature check, an all-header file, etc.) wiped the period
    # and inserted nothing — silent data loss. Now an empty map leaves the existing data untouched.

    # Map and insert rows
    mapped = []
    for r in rows:
        base = {'org_id': org_id}
        if has_period:
            base.update({'period': period, 'period_month': pm['month'], 'period_year': pm['year']})
        
        if file_type in ("sales", "daily_sales"):
            # Skip store header rows
            trans_date_raw = str(r.get('Trans Date Time', r.get('Trans Date', ''))).strip()
            if trans_date_raw.startswith('Store:') or not trans_date_raw:
                continue
            # For daily_sales, derive period from the row's date
            if file_type == 'daily_sales':
                try:
                    # Handle Excel serial numbers AND regular date strings
                    try:
                        serial = float(trans_date_raw)
                        td = pd.Timestamp('1899-12-30') + pd.Timedelta(days=serial)
                    except (ValueError, TypeError):
                        td = pd.to_datetime(trans_date_raw, errors='coerce')
                    if pd.isna(td):
                        continue
                    row_period = td.strftime('%B %Y')
                    row_pm = {'month': td.month, 'year': td.year}
                except Exception:
                    continue
                base = {'org_id': org_id, 'period': row_period, 'period_month': row_pm['month'], 'period_year': row_pm['year']}
            row = {**base,
                'store': r.get('Store',''), 'salesperson': r.get('Salesperson',''),
                'user_login': r.get('User Login',''),
                'contract_type': (r.get('Contract Type','') or (
                    # Infer from product_desc for Legacy format
                    'Upgrade' if 'Upgrade' in str(r.get('Product Desc','')) else
                    'Port-In' if 'Port-In' in str(r.get('Product Desc','')) else
                    'Add A Line' if 'Add A Line' in str(r.get('Product Desc','')) else
                    'Activation' if any(x in str(r.get('Product Desc','')) for x in ['New Act','Activation','New Line']) else
                    ''
                )),
                # 'category' also accepts the custom "for Metrics pro" export's 'System Category' column
                # (values Regular / RTR Product / Accessory / CellPhone) → accessories classify EXACTLY by
                # category='Accessory' (no product-keyword list needed).
                'department': r.get('Department',''),
                'category': r.get('Category','') or r.get('System Category',''),
                'product_desc': r.get('Product Desc',''), 'product_id': safe_float(r.get('Product ID')) or None,
                'gp': safe_float(r.get('GP')), 'ext_price': safe_float(r.get('Ext Price')),
                'tax': safe_float(r.get('Tax') or r.get('Sales Tax') or r.get('Tax Amount') or r.get('Tax Amt')),
                'trans_id': str(r.get('Trans ID','')).replace('.0','').strip(),
                'trans_date': str(r.get('Trans Date Time',r.get('Trans Date','')))[:10] or None,
                'mdn': str(r.get('Activated Mobile Number','') or r.get('Primary Account Number','')).replace('.0','').strip(),
                'serial_1': str(r.get('Serial 1','')).replace('.0','').strip()[:30],
                'register': str(r.get('Register','')).strip(),
                'tender_type': str(r.get('Tender Type','')).strip(),
                'voided': str(r.get('Voided','')).strip(),
                'trans_type': str(r.get('Trans Type','')).strip(),
                # customer identity (78-col Sales Transaction Details) — feeds the fraud detectors
                'customer': str(r.get('Customer','')).strip() or None,
                'email': str(r.get('Email','')).strip() or None,
                'customer_no': str(r.get('Customer #','') or r.get('Customer No','')).replace('.0','').strip() or None,
            }
            # For the daily feed, use the parsed date (handles Excel serials too) so the per-day
            # idempotent re-pull and the recon's date column are always a clean ISO date.
            if file_type == 'daily_sales':
                row['trans_date'] = td.strftime('%Y-%m-%d')
        elif file_type == "payment_detail":
            # Single source of truth shared with the epay sweep (epay_sweep.map_payment_detail_row).
            row = epay_sweep.map_payment_detail_row(r, base)
        elif file_type == "comp_report":
            # Real mapper (previously comp_report fell to the empty else-branch). Shared w/ sweep.
            row = epay_sweep.map_comp_report_row(r, base)
        elif file_type == "mi_report":
            # Single source of truth for the MI/ATU column->raw_mi mapping, shared with the
            # epay auto-sweep (epay_sweep.map_mi_row) so manual + swept files are identical.
            row = epay_sweep.map_mi_row(r, base)
        elif file_type == "dlar_rep":
            ga_prepaid = safe_float(r.get('GA Prepaid'))
            bounty = safe_float(r.get('Boost Ready Bounty'))
            boost_app = (bounty / ga_prepaid * 100) if ga_prepaid > 0 else 0
            row = {**base,
                'salesforce_id': r.get('Salesforce ID',''),
                'door_name': r.get('Door Name',''),
                'door_address': r.get('Door Address',''),
                'door_city': r.get('Door City',''),
                'door_state': r.get('Door State',''),
                'door_zip': str(r.get('Door Zip','')).replace('.0','').strip(),
                'advocate_name': r.get('Advocate Name',''),
                'rep_name': r.get('Advocate Name',''),
                'store': r.get('Door Address',''),
                'gross_adds': safe_float(r.get('Gross Adds')),
                'ga_prepaid': ga_prepaid,
                'ga_postpaid': safe_float(r.get('GA Postpaid')),
                'upgrades': safe_float(r.get('Upgrades')),
                'byod_pct': safe_float(r.get('BYOD %')),
                'atu': safe_float(r.get('ATU')),
                'atu_pct': safe_float(r.get('ATU %')),
                'protect_pct': safe_float(r.get('Device Insurance %')),
                'device_insurance_total': safe_float(r.get('Device Insurance Total')),
                'device_insurance_ga': safe_float(r.get('Device Insurance GA')),
                'device_insurance_upgrades': safe_float(r.get('Device Insurance Upgrades')),
                'device_insurance_pct': safe_float(r.get('Device Insurance %')),
                'platinum_pts': safe_float(r.get('Platinum Pts')),
                'avg_platinum_pts': safe_float(r.get('Avg Platinum Pts')),
                'platinum_pts_5plus': safe_float(r.get('Platinum Pts (5+)')),
                'boost_ready_bounty': bounty,
                'tablet_ga': safe_float(r.get('Tablet GA')),
                'boost_app_pct': boost_app,
            }
        elif file_type == "dlar_store":
            row = {**base,
                'salesforce_id': r.get('Salesforce ID',''),
                'address': r.get('Address',''),
                'location': r.get('Location',''),
                'store_code': r.get('Door_ID',''),
                'gross_adds': safe_float(r.get('Gross Adds')),
                'pay_now_acts': safe_float(r.get('Pay Now Acts')),
                'pay_later_acts': safe_float(r.get('Pay Later Acts')),
                'total_upgrades': safe_float(r.get('Total Upgrades')),
                'psa_projected': safe_float(r.get('Projected PSA', r.get('PSA Projected', 0))),
                'family_plan_pct': safe_float(r.get('Family Plan %')),
                'tmr3': safe_float(r.get('3MR')),
                'aal_conversion': safe_float(r.get('AAL Conversion')),
                'protect_pct': safe_float(r.get('Boost Protect %')),
                'atu': safe_float(r.get('ATU')),
                'byod_pct': safe_float(r.get('BYOD Adds %')),
                'port_pct': safe_float(r.get('Port %')),
                'conversion_rate': safe_float(r.get('Conversion Rate')),
                'acc_attach_rate': safe_float(r.get('Accessory Attach Rate')),
                'avg_first_mrc': safe_float(r.get('Avg First MRC')),
            }
        elif file_type == "catalog":
            row = {**base,
                'product_id': safe_float(r.get('Product ID')) or None,
                'product_desc': r.get('Product Desc',''),
                'cost': safe_float(r.get('Cost')),
                'sku': r.get('SKU',''),
            }
        elif file_type in ("ma_commission", "ma_daily_tx", "ma_fulfillment"):
            # Total / VidaPay Master-Agent exports. Date-grain reports: derive period per ROW (like
            # daily_sales) so no period selection is needed and hourly email re-pulls stay idempotent.
            def _d10(v):
                s = str(v or "").strip()
                if not s or s.lower() in ("nan", "none", "nat"):
                    return None
                try:
                    ts = pd.to_datetime(float(s), origin="1899-12-30", unit="D")  # Excel serial
                except (ValueError, TypeError):
                    ts = pd.to_datetime(s, errors="coerce")
                return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")

            date_key = {"ma_commission": "Date", "ma_daily_tx": "Date of Transaction",
                        "ma_fulfillment": "Date Ordered"}[file_type]
            d10 = _d10(r.get(date_key))
            base = {"org_id": org_id}
            if d10 and file_type != "ma_fulfillment":
                td = pd.to_datetime(d10)
                base.update({"period": td.strftime("%B %Y"), "period_month": td.month, "period_year": td.year})
            if file_type == "ma_commission":
                row = {**base, "tx_date": d10, "tx_time": str(r.get("Time", "")).strip() or None,
                    "carrier_name": str(r.get("Carrier Name", "")).strip() or None,
                    "activation_order": str(r.get("Activation Order", "")).strip() or None,
                    "merchant_account_id": str(r.get("MerchantAccountId", "")).replace(".0", "").strip() or None,
                    "imei": str(r.get("IMEI", "")).replace(".0", "").strip() or None,
                    "sim": str(r.get("SIM", "")).replace(".0", "").strip() or None,
                    "sku": str(r.get("SKU", "")).strip() or None,
                    "activation_type": str(r.get("Activation Type", "")).strip() or None,
                    "activation_type2": str(r.get("Activation Type 2", "")).strip() or None,
                    "sub_type": str(r.get("Sub Type", "")).strip() or None,
                    "device_margin": safe_float(r.get("Device Margin")),
                    "consumer_margin": safe_float(r.get("Consumer Margin")),
                    "consumer_financing": safe_float(r.get("Consumer Financing")),
                    "rebate": safe_float(r.get("Rebate")),
                    "perfect_sale": str(r.get("Perfect Sale", "")).strip() or None,
                    "wallet_funding": safe_float(r.get("Wallet Funding Amount")),
                    "mrc_net_discount": safe_float(r.get("MRC Net Discount")),
                    "fees": safe_float(r.get("Fees")), "fees_margin": safe_float(r.get("Fees Margin")),
                    "spiff_m1": safe_float(r.get("1st Month Spiff")), "spiff_m2": safe_float(r.get("2nd Month Spiff")),
                    "spiff_m3": safe_float(r.get("3rd Month Spiff")), "spiff_m4": safe_float(r.get("4th Month Spiff")),
                    "spiff_m5": safe_float(r.get("5th Month Spiff")), "spiff_m6": safe_float(r.get("6th Month Spiff")),
                    "port_status": str(r.get("Port Status", "")).strip() or None,
                    "id_verification": str(r.get("ID Verification", "")).strip() or None,
                    "is_financed": str(r.get("Is Financed", "")).strip() or None,
                    "user_id": str(r.get("User Id", "")).replace(".0", "").strip() or None,
                    "user_name": str(r.get("User Name", "")).strip() or None,
                    "ban": str(r.get("BAN", "")).replace(".0", "").strip() or None,
                    "bin": str(r.get("BIN", "")).replace(".0", "").strip() or None,
                    "pos_invoice": str(r.get("POS Invoice", "")).strip() or None,
                    "line_status": str(r.get("Line Status", "")).strip() or None,
                    "status_change_date": str(r.get("Status Change Date", "")).strip() or None,
                    "suspension_reason": str(r.get("Suspension Reason", "")).strip() or None,
                    "consumer_value": safe_float(r.get("Consumer Value")),
                    "platform": str(r.get("Platform", "")).strip() or None,
                    "platform_tx_id": str(r.get("Platform Transaction Id", "")).strip() or None,
                    "external_ref": str(r.get("External Reference Id", "")).strip() or None}
            elif file_type == "ma_daily_tx":
                row = {**base, "tx_date": d10, "due_date": _d10(r.get("Date Due")),
                    "account_id": str(r.get("Account ID", "")).replace(".0", "").strip() or None,
                    "account_name": str(r.get("Account Name", "")).strip() or None,
                    "direct_ma_id": str(r.get("Direct MA ID", "")).replace(".0", "").strip() or None,
                    "direct_ma_name": str(r.get("Direct MA Name", "")).strip() or None,
                    "top_ma_id": str(r.get("Top MA ID", "")).replace(".0", "").strip() or None,
                    "top_ma_name": str(r.get("Top MA Name", "")).strip() or None,
                    "order_number": str(r.get("Order Number", "")).replace(".0", "").strip() or None,
                    "user_name": str(r.get("User", "")).strip() or None,
                    "order_type": str(r.get("Order Type", "")).strip() or None,
                    "product_name": str(r.get("Product Name", "")).strip() or None,
                    "retail_cost": safe_float(r.get("Retail Cost")),
                    "merchant_discount": safe_float(r.get("Merchant Discount")),
                    "merchant_invoice": safe_float(r.get("Merchant Invoice"))}
            else:  # ma_fulfillment
                row = {**base, "date_ordered": d10, "date_filled": _d10(r.get("Date Filled")),
                    "date_shipped": _d10(r.get("Date Shipped")),
                    "order_number": str(r.get("Order Number", "")).replace(".0", "").strip() or None,
                    "order_status": str(r.get("Order Status", "")).strip() or None,
                    "order_type": str(r.get("Order Type", "")).strip() or None,
                    "tspid": str(r.get("TSPID", "")).replace(".0", "").strip() or None,
                    "business_name": str(r.get("Business Name", "")).strip() or None,
                    "business_address": str(r.get("Business Address", "")).strip() or None,
                    "city": str(r.get("City", "")).strip() or None,
                    "state": str(r.get("State", "")).strip() or None,
                    "zip": str(r.get("Zip", "")).replace(".0", "").strip() or None,
                    "product_name": str(r.get("Product Name", "")).strip() or None,
                    "number_ordered": safe_float(r.get("Number Ordered")),
                    "price": safe_float(r.get("Price")),
                    "tracking_number": str(r.get("Tracking Number", "")).strip() or None}
        else:
            row = {**base}
        
        if any(v for v in row.values() if v and v != org_id):
            mapped.append(row)

    # ma_commission: resolve carrier_id from the file's own Carrier Name ('Total by Verizon' →
    # the tenant's Total carrier) — carrier-neutral lookup, NULL when the tenant has no match.
    if file_type == 'ma_commission' and mapped:
        try:
            _carr = (client.schema('commcalc').table('carrier').select('id,name,code')
                     .eq('org_id', org_id).execute().data) or []
        except Exception:
            _carr = []
        _ccache = {}
        def _carrier_for(nm):
            nl = (nm or '').strip().lower()
            if not nl:
                return None
            if nl not in _ccache:
                hit = None
                for c in _carr:
                    cn = str(c.get('name') or '').strip().lower()
                    cc = str(c.get('code') or '').strip().lower()
                    if (cn and (cn in nl or nl in cn)) or (cc and cc in nl):
                        hit = c.get('id')
                        break
                _ccache[nl] = hit
            return _ccache[nl]
        for m in mapped:
            m['carrier_id'] = _carrier_for(m.get('carrier_name'))

    # GUARD: only NOW (rows successfully mapped) do we clear the existing data, and only if the
    # upload actually produced rows — so a file that parsed to nothing can never wipe a populated
    # period. catalog/master_cats replace the whole table.
    DATE_KEYED = {'daily_sales': 'trans_date', 'ma_commission': 'tx_date',
                  'ma_daily_tx': 'tx_date', 'ma_fulfillment': 'date_ordered'}
    # Row-count guardrail (user 2026-07-05): BEFORE the delete-and-replace, compare the incoming count
    # against what's already stored for the same day/period. A day-to-date feed only grows through the
    # day and a monthly file only shrinks on a bad/partial export (June arrived ~1/6th complete). A big
    # shrink is recorded in `shrink` so the email sweep can alert. Best-effort — NEVER blocks the upload.
    shrink = []
    # PRICE-COVERAGE GUARD (2026-07-08; made PER-DATE 2026-07-14): the hourly feed sometimes re-delivers a
    # DEGRADED "Sales Transaction Details" export that dropped the Ext Price/GP columns (the price-less
    # "for Metrics pro"/.csv variant). Because daily_sales does a delete-then-insert PER DAY, ingesting it
    # WIPES the real dollars for the days it covers. Evaluate EACH trans_date on its own: refuse only the
    # day(s) whose incoming priced-row count collapses below half of what's already stored for that same
    # day (and only when >= 50 priced rows exist there to protect), while still ingesting the file's fresh /
    # better days. This unblocks a multi-day SUBSET feed that is fuller on early days but is the ONLY copy of
    # later days (luxelink July 1-13, 2026-07-14): the early days are kept as stored, the later days flow
    # through — where the old per-file guard refused the WHOLE file and discarded those only-copies with it.
    # (Boost/house files are single-source day-to-date pulls, so every day is fresh and this never trips —
    # path byte-identical. Thresholds unchanged: protect at existing_priced >= 50, refuse a day at
    # incoming_priced < 0.5 x existing_priced.)
    price_guard_partial = None   # set when SOME (not all) of the file's days were refused → PARTIAL ingest
    if file_type == 'daily_sales' and mapped:
        _pg_dates = sorted({m.get('trans_date') for m in mapped if m.get('trans_date')})
        _inc_by_date = {}
        for _m in mapped:
            _d = _m.get('trans_date')
            if _d and safe_float(_m.get('ext_price')) != 0:
                _inc_by_date[_d] = _inc_by_date.get(_d, 0) + 1
        # ONE batched lookup of existing priced rows across the whole date set, counted PER DAY in Python —
        # NOT one count query per day. PostgREST can't GROUP BY without an RPC, so fetch just the trans_date
        # column for the file's dates where ext_price != 0 and tally locally (the date set is small). The
        # explicit high .limit mirrors the established pattern in this module (email_processed dedup fetch);
        # under-fetching here would silently UNDER-count existing dollars and weaken the guard, so pull all.
        _ex_by_date = {}
        try:
            if _pg_dates:
                _pg_rows = ((client.schema('commcalc').table('daily_sales_feed').select('trans_date')
                             .eq('org_id', org_id).in_('trans_date', _pg_dates).neq('ext_price', 0)
                             .limit(1000000).execute().data) or [])
                for _r in _pg_rows:
                    _d = _r.get('trans_date')
                    if _d:
                        _ex_by_date[_d] = _ex_by_date.get(_d, 0) + 1
        except Exception as _pge:
            print(f'WARN price-guard existing-priced lookup skipped: {_pge}')
            _ex_by_date = {}
        _guarded = [d for d in _pg_dates
                    if _ex_by_date.get(d, 0) >= 50 and _inc_by_date.get(d, 0) < _ex_by_date.get(d, 0) * 0.5]
        if _guarded:
            _g_ex = sum(_ex_by_date.get(d, 0) for d in _guarded)
            _g_inc = sum(_inc_by_date.get(d, 0) for d in _guarded)
            _g_list = ', '.join(str(d) for d in _guarded)
            if set(_guarded) == set(_pg_dates):
                # EVERY day the file covers is degraded → unchanged FULL-refusal shape (drop the whole file,
                # keeping the shipped `skipped:'price_guard'` UI/sweep handling working byte-for-byte).
                print(f'PRICE GUARD: refused degraded daily_sales file — incoming_priced={_g_inc} vs '
                      f'existing_priced={_g_ex} for dates {_pg_dates}; kept existing dollars')
                return {"saved": 0, "file_type": file_type, "period": period, "fraud": None, "recon": None,
                        "skipped": "price_guard",
                        "shrink": [{"key": "price-guard", "prior": int(_g_ex), "new": int(_g_inc),
                                    "reason": "refused: far fewer priced (Ext Price) rows than already stored — "
                                              "a degraded/price-less export. Kept existing dollars. Ensure the "
                                              "scheduled b2bsoft report keeps the Ext Price + GP columns."}],
                        "_trace": {"rows_in": len(mapped), "target_table": table, "periods": {},
                                   "date_counts": {str(d): int(_inc_by_date.get(d, 0)) for d in _pg_dates}}}
            # SOME days degraded, others fresh/better → PARTIAL. Drop the degraded days' rows so the per-date
            # delete-then-insert below never clears them (their stored rows survive untouched); the file's
            # remaining days ingest normally. The marker is merged into the final response and its shrink
            # entry rides the existing partial-export alert path in the email sweep.
            _g_set = set(_guarded)
            print(f'PRICE GUARD (partial): refused {len(_guarded)} degraded day(s) [{_g_list}] — '
                  f'incoming_priced={_g_inc} vs existing_priced={_g_ex}; kept existing dollars for those '
                  f'day(s); ingesting the file\'s fresh day(s)')
            mapped = [m for m in mapped if m.get('trans_date') not in _g_set]
            price_guard_partial = {
                "guarded_dates": [str(d) for d in _guarded],
                "shrink": {"key": "price-guard-partial", "prior": int(_g_ex), "new": int(_g_inc),
                           "reason": (f"kept existing data for {_g_list} — a degraded/price-less export carried "
                                      f"far fewer priced (Ext Price) rows for those day(s) than already stored, "
                                      f"so those day(s) were left as-is; ingested the file's fresh day(s) only. "
                                      f"Ensure the scheduled b2bsoft report keeps the Ext Price + GP columns.")},
            }
    if mapped:
        if file_type in DATE_KEYED:
            # Date-grain feeds are keyed by DAY, not month. Make a re-pull of the same day(s)
            # idempotent by clearing only the dates this file covers (never the whole month — other
            # days' rows survive). Rows with no parseable date can't be deduped, so they just append.
            dk = DATE_KEYED[file_type]
            feed_dates = sorted({m.get(dk) for m in mapped if m.get(dk)})
            if feed_dates:
                try:
                    new_by_date = {}
                    for _m in mapped:
                        _d = _m.get(dk)
                        if _d:
                            new_by_date[_d] = new_by_date.get(_d, 0) + 1
                    for _d in feed_dates:
                        prior = (client.schema('commcalc').table(table).select('org_id', count='exact')
                                 .eq('org_id', org_id).eq(dk, _d).execute().count) or 0
                        newc = new_by_date.get(_d, 0)
                        if prior >= _SHRINK_MIN_PRIOR and newc < prior * _SHRINK_RATIO:
                            shrink.append({'key': str(_d), 'prior': int(prior), 'new': int(newc)})
                except Exception as e:
                    print(f'WARN row-count guardrail (date) skipped: {e}')
                try:
                    client.schema('commcalc').table(table).delete()\
                        .eq('org_id', org_id).in_(dk, feed_dates).execute()
                except Exception as e:
                    mig = 'migration 047' if file_type == 'daily_sales' else 'migration 083'
                    raise HTTPException(500, f"Failed to clear existing {file_type} rows: {e}. Run {mig}.")
        elif has_period and period:
            try:
                prior = (client.schema('commcalc').table(table).select('org_id', count='exact')
                         .eq('org_id', org_id).in_('period', _pvariants(period)).execute().count) or 0
                if prior >= _SHRINK_MIN_PRIOR and len(mapped) < prior * _SHRINK_RATIO:
                    shrink.append({'key': period, 'prior': int(prior), 'new': len(mapped)})
            except Exception as e:
                print(f'WARN row-count guardrail (period) skipped: {e}')
            try:
                client.schema('commcalc').table(table).delete().eq('org_id', org_id).in_('period', _pvariants(period)).execute()
            except Exception as e:
                raise HTTPException(500, f"Failed to clear existing data: {e}. Run commcalc_master_fix.sql")
        elif not has_period:
            try:
                client.schema('commcalc').table(table).delete().eq('org_id', org_id).neq('id', '00000000-0000-0000-0000-000000000000').execute()
            except Exception:
                pass

    # Insert in batches
    saved = 0
    for i in range(0, len(mapped), 500):
        batch = mapped[i:i+500]
        try:
            # Plain insert for all upload types. The period was wiped just above (only when the
            # upload produced rows), so there are no conflicts. The old unique dedup index was
            # dropped because one transaction has many line items that share a single Trans ID.
            client.schema('commcalc').table(table).insert(batch).execute()
            saved += len(batch)
        except Exception as e:
            raise HTTPException(500, f"Insert failed at row {i}: {e}")
    
    print(f'DEBUG upload complete: file_type={file_type} saved={saved} mapped={len(mapped)} period={period!r}')

    # Record this upload so the UI can show what's already been uploaded (and
    # when), surviving page reloads. daily_sales derives its period per-row, so
    # log the distinct period(s) actually touched. Best-effort: a logging
    # failure (e.g. 007_upload_log.sql not run yet) must never break an upload.
    if file_type in ('daily_sales', 'ma_commission', 'ma_daily_tx'):
        _log_periods = sorted({m.get('period') for m in mapped if m.get('period')})
        log_period = ', '.join(_log_periods) if _log_periods else (period or None)
    else:
        log_period = period or None
    try:
        client.schema('commcalc').table('upload_log').insert({
            'org_id': org_id,
            'file_type': file_type,
            'period': log_period,
            'filename': getattr(file, 'filename', None),
            'rows_saved': saved,
        }).execute()
    except Exception as e:
        print(f'WARN upload_log insert failed (run 007_upload_log.sql?): {e}')

    # After a MONTHLY sales upload, scan for fraud (fake/reused email, duplicate id) → chargeback
    # bucket. The daily feed lands in daily_sales_feed (not raw_sales, which the detectors read), and
    # is for recon — so it does not trigger fraud scanning.
    fraud = None
    if file_type == 'sales' and mapped:
        try:
            for p in sorted({m.get('period') for m in mapped if m.get('period')}) or [period]:
                fr = _detect_fraud(client, org_id, p)
                fraud = {'email_flags': (fraud or {}).get('email_flags', 0) + fr['email_flags'],
                         'dupe_flags': (fraud or {}).get('dupe_flags', 0) + fr['dupe_flags']}
        except Exception as e:
            print(f'WARN fraud scan after sales upload failed (run 036?): {e}')

    # Refresh the sales-feed recon FLAGS for each touched period after EITHER side of the recon changes:
    #   • a DAILY feed upload (manual daily_sales OR the FTP/email sweep, which reuse this endpoint), and
    #   • a MONTHLY 'sales' re-upload — the authoritative side. Without this, re-uploading a fresh monthly
    #     file (the fix for a stale-monthly false-leak flood) would change the recon truth but leave the
    #     old 'sales_leak' flags stale until the next daily sweep. sync_recon_flags is delete-first by
    #     source='sales_recon', so a monthly re-upload now SELF-HEALS the leak flags to the new residual.
    # Sync only — designated-notify stays manual/scheduled to avoid spam. Best-effort: a recon failure
    # must never break the upload.
    recon = None
    if file_type in ('daily_sales', 'sales') and mapped:
        recon = {'flagged': 0, 'periods': []}
        for p in sorted({m.get('period') for m in mapped if m.get('period')}):
            try:
                rr = sales_recon.sync_recon_flags(p, org_id=org_id)
                recon['flagged'] += rr.get('flagged', 0)
                recon['periods'].append(p)
            except Exception as e:
                print(f'WARN sales-recon flag sync after {file_type} upload failed (run 047?): {e}')

    out = {"saved": saved, "file_type": file_type, "period": period, "fraud": fraud, "recon": recon,
           "shrink": shrink}
    if price_guard_partial:
        # PARTIAL price-guard outcome: SOME day(s) were refused (kept as stored) while the file's fresh
        # day(s) ingested. Say so explicitly. The guard entry is prepended so readUploadOutcome/shrink[0]
        # surfaces the "kept existing data for <days>" reason first; row-count shrink entries (if any) ride
        # behind it, and every entry still flows to the email sweep's partial-export alert.
        out["skipped"] = "price_guard_partial"
        out["guarded_dates"] = price_guard_partial["guarded_dates"]
        out["shrink"] = [price_guard_partial["shrink"]] + shrink
    # Rich trace payload (mig 202) — per-period + per-day SAVED counts, computed from the rows actually
    # written (`mapped`), plus rows-in and the target table. Consumed + stripped by the traced wrapper.
    _tr_periods, _tr_dates = {}, {}
    for _m in mapped:
        _p = _m.get("period")
        if _p:
            _tr_periods[_p] = _tr_periods.get(_p, 0) + 1
        _d = _m.get("trans_date")
        if _d:
            _tr_dates[str(_d)] = _tr_dates.get(str(_d), 0) + 1
    out["_trace"] = {"rows_in": len(rows), "target_table": table,
                     "periods": _tr_periods, "date_counts": _tr_dates}
    return out


@router.get("/whatif/activation-baseline")
def whatif_activation_baseline(period: str, carrier_id: str = "", org_id: str = ORG_ID):
    """What-If tool #1 — CARRIER-AGNOSTIC employee-payout template. Boost carriers keep the legacy 8
    components (byte-identical); non-Boost carriers' components auto-populate from their configured
    Commission Plans / rules / tiers + payout_schedule installments; a carrier with no pay source gets an
    explicit empty state pointing at /commcalc/commission-plans. Employee-payout perspective (no residual
    money) → not gated behind the carrier-residual grant."""
    require_org(org_id)
    return whatif.activation_baseline(sb(), org_id, period, carrier_id=(carrier_id or None))


@router.get("/whatif/byod-residual")
def whatif_byod_residual(months: int = 6, carrier_id: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """What-If tool #2 — BYOD → recurring-residual analysis. Residual source resolved per carrier
    (whatif_source_config, mig 209): Boost → raw_mi MI+ATU (unchanged); MA-fed → raw_ma_daily_tx residual
    rows (sign-normalized) joined with raw_ma_commission M1-M6 + rebate. Residual = carrier-income money →
    gated behind the carrier-residual visibility grant."""
    require_org(org_id)
    _require_carrier_residual(authorization, org_id)   # carrier-residual visibility gate (mig 201)
    return whatif.byod_residual(sb(), org_id, max(1, min(months, 24)), carrier_id=(carrier_id or None))


@router.get("/whatif/accessory-byod")
def whatif_accessory_byod(months: int = 4, org_id: str = ORG_ID):
    """What-If tool #3 — per store/period BYOD activations vs accessory revenue vs total revenue,
    with Pearson correlations."""
    require_org(org_id)
    return whatif.accessory_byod_correlation(sb(), org_id, months)


@router.get("/whatif/carrier-income")
def whatif_carrier_income(months: int = 6, carrier_id: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """What-If tool #4 — the COMPANY perspective: what the carrier / master-agent pays the company, by
    heading, month over month. Boost → Comprehensive Comp + MI+ATU (unchanged shape); MA-fed →
    raw_ma_commission (M1-M6 spiffs + rebate) + raw_ma_daily_tx (residual + airtime margin). Company
    payout / carrier income = residual-class money → gated behind the carrier-residual visibility grant."""
    require_org(org_id)
    _require_carrier_residual(authorization, org_id)
    return whatif.carrier_income(sb(), org_id, max(1, min(months, 24)), carrier_id=(carrier_id or None))


@router.get("/whatif/source-config")
def whatif_get_source_config(carrier_id: str = "", org_id: str = ORG_ID):
    """The RESOLVED What-If source config (whatif_source_config, mig 209) for the selected carrier, plus
    the org's raw override rows. Drives the ⚙️ Sources admin panel. Read-only, degrades to code defaults
    when mig 209 is absent."""
    require_org(org_id)
    client = sb()
    carriers, picked, mode = whatif._carrier_ctx(client, org_id, (carrier_id or None))
    resolved = whatif._whatif_source_config(client, org_id, (picked or {}).get("id"), mode)
    try:
        rows = (client.schema("commcalc").table("whatif_source_config").select("*")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    return {
        "carrier": ({"id": picked.get("id"), "name": picked.get("name"), "code": picked.get("code")} if picked else None),
        "carrier_mode": mode,
        "carriers": [{"id": c.get("id"), "name": c.get("name"), "code": c.get("code"),
                      "is_default": bool(c.get("is_default"))} for c in carriers],
        "resolved": resolved,
        "rows": rows,
        "options": {
            "residual_source": ["boost_mi_atu", "ma_daily_tx", "none"],
            "residual_sign": ["as_is", "negate", "abs"],
            "income_source": ["boost_comp_mi_atu", "ma"],
            "retail_cost_source": ["none", "ma_pr_activation"],
            "residual_amount_field": ["merchant_invoice", "merchant_discount", "retail_cost"],
        },
    }


@router.put("/whatif/source-config")
def whatif_put_source_config(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Admin-only. Upsert a PER-CARRIER What-If source override (or the org's mode-default row when
    carrier_id is the nil UUID). Config, not code (RULE TWO). Degrades with an ok=false hint before mig
    209 runs."""
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    carrier_id = (body.get("carrier_id") or "").strip() or "00000000-0000-0000-0000-000000000000"
    carrier_mode = (body.get("carrier_mode") or "boost").strip().lower()
    if carrier_mode not in ("boost", "plan"):
        carrier_mode = "boost"
    row = {"org_id": org_id, "carrier_id": carrier_id, "carrier_mode": carrier_mode,
           "updated_at": _datetime.now(_timezone.utc).isoformat()}
    for k in ("residual_source", "residual_order_type", "residual_amount_field", "residual_sign",
              "income_source", "retail_cost_source", "notes"):
        if k in body:
            row[k] = body.get(k)
    if "is_active" in body:
        row["is_active"] = bool(body.get("is_active"))
    try:
        client = sb()
        client.schema("commcalc").table("whatif_source_config").upsert(
            row, on_conflict="org_id,carrier_id,carrier_mode").execute()
    except Exception as e:
        return {"ok": False, "hint": "run migration 209 (commcalc.whatif_source_config)", "error": str(e)}
    return {"ok": True, "saved": row}


def _store_market_resolver(client, org_id):
    """(resolve_market, all_markets) from store_mapping — keyed by address, store_code, or leading store
    number (same resolver the Sales Report / commission-trend use). Never raises."""
    import re as _re_mk
    try:
        sm = (client.schema('commcalc').table('store_mapping')
              .select('store_code,store_address,market').eq('org_id', org_id).execute().data) or []
    except Exception:
        sm = []
    by_code, by_addr, by_num, markets = {}, {}, {}, set()

    def _lead(s):
        m = _re_mk.match(r"\s*(\d+)", str(s or "")); return m.group(1) if m else ""
    for s in sm:
        mk = (s.get('market') or '').strip()
        if not mk:
            continue
        markets.add(mk)
        code = str(s.get('store_code') or '').strip()
        addr = str(s.get('store_address') or '').strip()
        if code:
            by_code[code] = mk
        if addr:
            by_addr[addr.lower()] = mk
        n = _lead(addr)
        if n:
            by_num.setdefault(n, mk)

    def resolve(store):
        st = str(store or '').strip()
        return (by_addr.get(st.lower()) or by_code.get(st) or by_num.get(_lead(st)) or '')
    return resolve, sorted(markets)


@router.get("/tax-collected")
def tax_collected(period: str, start: str = "", end: str = "", org_id: str = ORG_ID):
    """Retail SALES TAX collected, per store WITH a per-day drill-down, for a period (from the sales
    export's Tax column, mig 105). Sourced from the UNIFIED sales set (raw_sales ∪ daily_sales_feed deduped
    by trans_id — `_sales_rows_union_txn`) so a tenant on the daily feed (no monthly upload) still gets a
    tax report and a promoted month is never masked by a stale feed. `start`/`end` (YYYY-MM-DD, optional)
    narrow to a date range WITHIN the period. Each store row carries its `market` (store_mapping) and a
    `days` array so the frontend can drill store → day and multi-select by store / market. Also returns
    `effective_rate` (tax ÷ pre-tax merchandise)."""
    require_org(org_id)
    client = sb()
    rows, _meta = _sales_rows_union_txn(
        client, org_id, period,
        cols='trans_id,trans_date,store,ext_price,tax,voided,trans_type')
    resolve_market, all_markets = _store_market_resolver(client, org_id)
    s0 = (start or '').strip()[:10]
    s1 = (end or '').strip()[:10]
    by_store = {}
    for r in rows:
        if str(r.get('voided') or '').strip().lower() in ('true', 'yes', '1', 'voided', 'void'):
            continue
        if str(r.get('trans_type') or '').strip() == 'Return':
            continue
        day = str(r.get('trans_date') or '')[:10]
        if s0 and day and day < s0:
            continue
        if s1 and day and day > s1:
            continue
        store = (r.get('store') or '?').strip() or '?'
        s = by_store.get(store)
        if not s:
            s = by_store[store] = {'store': store, 'market': resolve_market(store),
                                   'tax': 0.0, 'revenue': 0.0, '_days': {}}
        tx = safe_float(r.get('tax'))
        ext = safe_float(r.get('ext_price'))
        s['tax'] += tx
        s['revenue'] += ext
        if day:
            d = s['_days'].setdefault(day, {'date': day, 'tax': 0.0, 'revenue': 0.0})
            d['tax'] += tx
            d['revenue'] += ext
    out = []
    for s in by_store.values():
        days = sorted(s['_days'].values(), key=lambda d: d['date'])
        for d in days:
            d['tax'] = round(d['tax'], 2)
            d['revenue'] = round(d['revenue'], 2)
            d['effective_rate'] = round(100 * d['tax'] / d['revenue'], 2) if d['revenue'] else 0.0
        out.append({'store': s['store'], 'market': s['market'],
                    'tax': round(s['tax'], 2), 'revenue': round(s['revenue'], 2),
                    'effective_rate': round(100 * s['tax'] / s['revenue'], 2) if s['revenue'] else 0.0,
                    'days': days})
    out.sort(key=lambda x: -x['tax'])
    total_tax = round(sum(x['tax'] for x in out), 2)
    return {'period': period, 'start': s0, 'end': s1, 'stores': out, 'markets': all_markets,
            'totals': {'tax': total_tax, 'revenue': round(sum(x['revenue'] for x in out), 2)},
            'has_tax': total_tax > 0,
            'note': (None if total_tax > 0 else
                     'No tax captured for this period yet — re-send a Sales Transaction Details file that '
                     'includes the Tax column (migration 105 adds the field; the parser maps Tax / Sales Tax).')}


@router.get("/upload/history")
async def upload_history(org_id: str = ORG_ID, period: str = "", limit: int = 100):
    """Recent uploads, newest first. Powers the Upload page's 'already
    uploaded' badges and the collapsible history menu. Optionally filter to a
    single period. Degrades to [] if 007_upload_log.sql hasn't been run yet."""
    require_org(org_id)
    client = sb()
    try:
        q = (client.schema('commcalc').table('upload_log')
             .select('id,file_type,period,filename,rows_saved,uploaded_at')
             .eq('org_id', org_id))
        if period:
            q = q.in_('period', _pvariants(period))
        resp = q.order('uploaded_at', desc=True).limit(min(max(limit, 1), 500)).execute()
        return resp.data or []
    except Exception as e:
        print(f'WARN upload_history query failed (run 007_upload_log.sql?): {e}')
        return []


# ── VIP Wireless invoice import (scraped via tools/vip_scraper) ───────────────
def _vip_money(v):
    """'$1,234.50' / '(12.47)' / '199.5' / '' -> float or None."""
    import re as _re
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ('nan', 'nat', 'none'):
        return None
    neg = '(' in s and ')' in s
    s = _re.sub(r'[^0-9.\-]', '', s.replace(',', ''))
    if s in ('', '-', '.'):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def _vip_int(v):
    s = str(v or '').strip()
    if not s or s.lower() in ('nan', 'none'):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _vip_ts(v):
    """Pass an ISO timestamp string through; '' -> None."""
    s = str(v or '').strip()
    if not s or s.lower() in ('nan', 'nat', 'none'):
        return None
    return s[:19] if 'T' in s else (s[:10] or None)


def _vip_period(created_on):
    """'2026-06-12T02:25:37' -> ('June 2026', 6, 2026)."""
    s = str(created_on or '').strip()
    if len(s) < 7:
        return None, None, None
    try:
        y, m = int(s[0:4]), int(s[5:7])
        return f"{_calendar.month_name[m]} {y}", m, y
    except Exception:
        return None, None, None


@router.post("/vip/upload")
async def upload_vip_invoices(file: UploadFile = File(...), org_id: str = ORG_ID):
    """Import the VIP scraper workbook (Invoices / Lines / Devices sheets) from
    tools/vip_scraper. Full replace: the portal has no per-period upload, so we
    wipe all VIP rows for the org and re-insert the full history each time."""
    require_org(org_id)
    contents = await file.read()
    try:
        xls = pd.ExcelFile(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(400, f"Could not read Excel file: {e}")
    sheets = set(xls.sheet_names)
    if 'Invoices' not in sheets:
        raise HTTPException(400, f"Missing 'Invoices' sheet. Found: {sorted(sheets)}")

    def sheet(name):
        if name not in sheets:
            return []
        return pd.read_excel(xls, sheet_name=name, dtype=str).fillna('').to_dict('records')

    def numc(r, *names):
        for n in names:
            if str(r.get(n, '')).strip() not in ('', 'nan', 'None'):
                return _vip_money(r.get(n))
        return None

    invoices = []
    for r in sheet('Invoices'):
        period, pm, py = _vip_period(r.get('CreatedOn', ''))
        invoices.append({
            'org_id': org_id,
            'vip_id': _vip_int(r.get('Id')),
            'invoice_number': str(r.get('InvoiceNumber', '')).strip() or None,
            'order_number': str(r.get('OrderNumber', '')).strip() or None,
            'location': r.get('Location', '') or None,
            'company_id': _vip_int(r.get('CompanyId')),
            'email': r.get('Email', '') or None,
            'status': r.get('Status', '') or None,
            'sub_total': numc(r, 'SubTotalNum', 'SubTotal'),
            'shipping': numc(r, 'ShippingNum', 'Shipping'),
            'discount': numc(r, 'DiscountNum', 'Discount'),
            'other_cost': numc(r, 'OtherCostNum', 'OtherCost'),
            'other_deductions': numc(r, 'OtherDeductionsNum', 'OtherDeductions'),
            'tax': numc(r, 'TaxNum', 'Tax'),
            'grand_total': numc(r, 'GrandTotalNum', 'GrandTotal'),
            'note': r.get('Note', '') or None,
            'created_on': _vip_ts(r.get('CreatedOn')),
            'transaction_date': _vip_ts(r.get('TransactionDate')),
            'due_date': _vip_ts(r.get('DueDate')),
            'period': period, 'period_month': pm, 'period_year': py,
        })

    lines = []
    for r in sheet('Lines'):
        period, pm, py = _vip_period(r.get('CreatedOn', ''))
        lines.append({
            'org_id': org_id,
            'vip_invoice_id': _vip_int(r.get('InvoiceId')),
            'invoice_number': str(r.get('InvoiceNumber', '')).strip() or None,
            'location': r.get('Location', '') or None,
            'status': r.get('Status', '') or None,
            'created_on': _vip_ts(r.get('CreatedOn')),
            'name': r.get('Name', '') or None,
            'note': r.get('Note', '') or None,
            'sku': r.get('SKU', '') or None,
            'price': numc(r, 'PriceNum', 'Price'),
            'quantity': _vip_money(r.get('Quantity')),
            'total': numc(r, 'TotalNum', 'Total'),
            'period': period, 'period_month': pm, 'period_year': py,
        })

    devices = []
    for r in sheet('Devices'):
        period, pm, py = _vip_period(r.get('CreatedOn', ''))
        devices.append({
            'org_id': org_id,
            'vip_invoice_id': _vip_int(r.get('InvoiceId')),
            'invoice_number': str(r.get('InvoiceNumber', '')).strip() or None,
            'location': r.get('Location', '') or None,
            'created_on': _vip_ts(r.get('CreatedOn')),
            'serial': str(r.get('Serial', '')).strip() or None,
            'product_name': r.get('ProductName', '') or None,
            'imei': str(r.get('IMEI', '')).strip() or None,
            'sim': str(r.get('SIM', '')).strip() or None,
            'period': period, 'period_month': pm, 'period_year': py,
        })

    client = sb()
    SENTINEL = '00000000-0000-0000-0000-000000000000'
    for tbl in ('vip_invoices', 'vip_invoice_lines', 'vip_invoice_devices'):
        try:
            # scope the wipe to THIS tenant — a bare delete-all would nuke every tenant's VIP invoices
            client.schema('commcalc').table(tbl).delete().eq('org_id', org_id).neq('id', SENTINEL).execute()
        except Exception as e:
            raise HTTPException(500, f"Failed clearing {tbl}: {e}. Did you run 008_vip_invoices.sql?")

    def insert_all(tbl, rows):
        saved = 0
        for i in range(0, len(rows), 500):
            batch = rows[i:i + 500]
            try:
                client.schema('commcalc').table(tbl).insert(batch).execute()
            except Exception as e:
                raise HTTPException(500, f"Insert into {tbl} failed at row {i}: {e}")
            saved += len(batch)
        return saved

    n_inv = insert_all('vip_invoices', invoices)
    n_line = insert_all('vip_invoice_lines', lines)
    n_dev = insert_all('vip_invoice_devices', devices)

    try:
        client.schema('commcalc').table('upload_log').insert({
            'org_id': org_id, 'file_type': 'vip_invoices', 'period': None,
            'filename': getattr(file, 'filename', None),
            'rows_saved': n_inv + n_line + n_dev,
        }).execute()
    except Exception as e:
        print(f'WARN upload_log insert failed: {e}')

    return {"invoices": n_inv, "lines": n_line, "devices": n_dev}


# ── VIP invoice reports ──────────────────────────────────────────────────────
VIP_FEE_COLS = ['shipping', 'discount', 'other_cost', 'other_deductions', 'tax']


def _vip_fetch(client, org_id, period=None, location=None, status=None, cols="*"):
    """Paginated fetch of vip_invoices (Supabase caps at 1000 rows/request)."""
    PAGE, out, frm = 1000, [], 0
    while True:
        q = client.schema('commcalc').table('vip_invoices').select(cols).eq('org_id', org_id)
        if period:
            q = q.in_('period', _pvariants(period))
        if location:
            q = q.eq('location', location)
        if status:
            q = q.eq('status', status)
        batch = (q.range(frm, frm + PAGE - 1).execute().data) or []
        out.extend(batch)
        if len(batch) < PAGE:
            break
        frm += PAGE
    return out


@router.get("/vip/filter-options")
async def vip_filter_options(org_id: str = ORG_ID):
    """Distinct stores / periods / statuses for the VIP page filter bar."""
    require_org(org_id)
    rows = _vip_fetch(sb(), org_id, cols="location,period,period_year,period_month,status")
    locations = sorted({r['location'] for r in rows if r.get('location')})
    statuses = sorted({r['status'] for r in rows if r.get('status')})
    pmap = {}
    for r in rows:
        if r.get('period'):
            pmap[r['period']] = (r.get('period_year') or 0, r.get('period_month') or 0)
    periods = sorted(pmap, key=lambda p: pmap[p], reverse=True)
    return {"locations": locations, "periods": periods, "statuses": statuses}


@router.get("/vip/summary")
async def vip_summary(org_id: str = ORG_ID, period: str = "", location: str = "", status: str = ""):
    """Totals, fees-by-type (invoice money buckets), and per-store breakdown."""
    require_org(org_id)
    cols = "location,sub_total,shipping,discount,other_cost,other_deductions,tax,grand_total"
    rows = _vip_fetch(sb(), org_id, period or None, location or None, status or None, cols=cols)

    def f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    totals = {"invoices": len(rows), "sub_total": 0.0, "grand_total": 0.0}
    for c in VIP_FEE_COLS:
        totals[c] = 0.0
    by_store: dict = {}
    for r in rows:
        loc = r.get('location') or '—'
        s = by_store.setdefault(loc, {"location": loc, "invoices": 0, "sub_total": 0.0,
                                      "grand_total": 0.0, **{c: 0.0 for c in VIP_FEE_COLS}})
        s["invoices"] += 1
        s["sub_total"] += f(r.get('sub_total'))
        s["grand_total"] += f(r.get('grand_total'))
        totals["sub_total"] += f(r.get('sub_total'))
        totals["grand_total"] += f(r.get('grand_total'))
        for c in VIP_FEE_COLS:
            v = f(r.get(c))
            s[c] += v
            totals[c] += v
    totals["fees_total"] = sum(totals[c] for c in VIP_FEE_COLS)
    by_store_list = sorted(by_store.values(), key=lambda x: x["grand_total"], reverse=True)
    return {"totals": totals,
            "fees_by_type": {c: totals[c] for c in VIP_FEE_COLS},
            "by_store": by_store_list}


@router.get("/vip/invoices")
async def vip_invoices_list(org_id: str = ORG_ID, period: str = "", location: str = "",
                            status: str = "", limit: int = 2000, offset: int = 0):
    """Invoice list for the table + Excel/PDF export (newest first)."""
    require_org(org_id)
    q = sb().schema('commcalc').table('vip_invoices').select(
        "vip_id,invoice_number,order_number,location,status,created_on,due_date,"
        "sub_total,shipping,discount,other_cost,other_deductions,tax,grand_total,period"
    ).eq('org_id', org_id)
    if period:
        q = q.in_('period', _pvariants(period))
    if location:
        q = q.eq('location', location)
    if status:
        q = q.eq('status', status)
    lim = min(max(limit, 1), 5000)
    return (q.order('created_on', desc=True).range(offset, offset + lim - 1).execute().data) or []


@router.get("/vip/invoice/{vip_id}")
async def vip_invoice_detail(vip_id: int, org_id: str = ORG_ID):
    """One invoice's full contents for the click-through preview: header + line items + devices."""
    require_org(org_id)
    client = sb()
    hdr = client.schema('commcalc').table('vip_invoices').select(
        "vip_id,invoice_number,order_number,location,status,created_on,transaction_date,due_date,"
        "sub_total,shipping,discount,other_cost,other_deductions,tax,grand_total,note,period"
    ).eq('org_id', org_id).eq('vip_id', vip_id).limit(1).execute().data
    if not hdr:
        raise HTTPException(404, f"Invoice {vip_id} not found")
    lines = client.schema('commcalc').table('vip_invoice_lines').select(
        "name,note,sku,price,quantity,total"
    ).eq('org_id', org_id).eq('vip_invoice_id', vip_id).execute().data or []
    devices = client.schema('commcalc').table('vip_invoice_devices').select(
        "serial,product_name,imei,sim"
    ).eq('org_id', org_id).eq('vip_invoice_id', vip_id).execute().data or []
    return {"invoice": hdr[0], "lines": lines, "devices": devices}


# ── VIP portal auto-sweep (admin-configurable credentials + schedule) ─────────
# Runs the scraper INSIDE the backend on a schedule (pg_cron → /vip/sweep/run-due),
# instead of the manual Codespace run. Creds + schedule live in the backend-only table
# commcalc.vip_sweep_config; the password is never returned to the browser.
def _vip_cfg(client, org_id):
    rows = client.schema('commcalc').table('vip_sweep_config').select('*') \
        .eq('org_id', org_id).limit(1).execute().data
    return rows[0] if rows else None


def _vip_next_run(frequency, day_of_week, day_of_month, hour, tzname):
    """Next run (UTC ISO) after now, in `tzname`. day_of_week 0=Mon..6=Sun."""
    from zoneinfo import ZoneInfo
    import calendar as _c
    try:
        tz = ZoneInfo(tzname or 'America/New_York')
    except Exception:
        tz = ZoneInfo('America/New_York')
    now = _datetime.now(tz)
    hour = int(hour if hour is not None else 6)
    if frequency == 'hourly':
        # Intra-day feed (e.g. the daily-transaction report pulled every hour for live targets).
        # `hour` is ignored; the next run is simply the top of the next hour.
        nxt = now.replace(minute=0, second=0, microsecond=0) + _timedelta(hours=1)
    elif frequency == 'daily':
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += _timedelta(days=1)
    elif frequency == 'monthly':
        dom = int(day_of_month if day_of_month is not None else 1)

        def at(y, m):
            d = min(dom, _c.monthrange(y, m)[1])
            return now.replace(year=y, month=m, day=d, hour=hour, minute=0, second=0, microsecond=0)
        nxt = at(now.year, now.month)
        if nxt <= now:
            ny, nm = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
            nxt = at(ny, nm)
    else:  # weekly (default)
        target = int(day_of_week if day_of_week is not None else 0)
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        nxt += _timedelta(days=(target - nxt.weekday()) % 7)
        if nxt <= now:
            nxt += _timedelta(days=7)
    return nxt.astimezone(_timezone.utc).isoformat()


_VIP_CFG_DEFAULTS = {'enabled': False, 'frequency': 'weekly', 'day_of_week': 0,
                     'day_of_month': 1, 'hour': 6, 'timezone': 'America/New_York',
                     'lookback_days': 14, 'sweep_invoices': True, 'sweep_asset': False,
                     'sweep_creditmemo': False, 'sweep_asset_ledger': True, 'sweep_chargebacks': True}


def _vip_public_cfg(cfg):
    """Config WITHOUT the password — only whether credentials are set."""
    if not cfg:
        return {**_VIP_CFG_DEFAULTS, 'configured': False, 'has_credentials': False,
                'portal_user': None, 'next_run_at': None, 'last_run_at': None,
                'last_status': None, 'last_detail': None}
    out = {k: cfg.get(k) for k in (
        'enabled', 'frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
        'lookback_days', 'sweep_invoices', 'sweep_asset', 'sweep_creditmemo', 'sweep_asset_ledger',
        'sweep_chargebacks', 'portal_user', 'next_run_at', 'last_run_at', 'last_status', 'last_detail')}
    out['configured'] = True
    out['has_credentials'] = bool(cfg.get('portal_user') and cfg.get('portal_pass'))
    return out


def _vip_set_status(client, org_id, status, detail, mark_run=False):
    upd = {'last_status': status, 'last_detail': (detail or '')[:600]}
    if mark_run:
        upd['last_run_at'] = _datetime.now(_timezone.utc).isoformat()
    client.schema('commcalc').table('vip_sweep_config').update(upd).eq('org_id', org_id).execute()


def _registry_auto_map(client, org_id):
    """report_definitions.auto by report_key — the registry (Connectors page) drives which reports a
    sweep pulls. A report with no registry row falls back to the connector's config toggle, so this is
    a zero-behavior-change cutover (the seeded auto flags already match the live toggles)."""
    try:
        rows = (client.schema('commcalc').table('report_definitions')
                .select('report_key,auto').eq('org_id', org_id).execute().data) or []
        return {r['report_key']: bool(r['auto']) for r in rows if r.get('report_key')}
    except Exception:
        return {}


def _do_vip_sweep(org_id):
    """Background worker: read creds from the config table, run the invoice sweep, record status."""
    client = sb()
    cfg = _vip_cfg(client, org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        _vip_set_status(client, org_id, 'error', 'No Distributor credentials set in the admin area', mark_run=True)
        return
    _vip_set_status(client, org_id, 'running', 'Sweep in progress…')
    # Default to the invoice sweep (back-compat: cfg may predate the toggles). sweep_asset
    # additionally pulls the PayGo / asset-lending weekly billing ledger (migration 014).
    amap = _registry_auto_map(client, org_id)
    def _en(key, fb):
        return amap[key] if key in amap else fb
    do_invoices = _en('vip_workbook', cfg.get('sweep_invoices') is not False)
    do_asset = bool(cfg.get('sweep_asset'))               # PayGo billing: no registry row → config
    do_creditmemo = bool(cfg.get('sweep_creditmemo'))     # credit memos: no registry row → config
    do_asset_ledger = _en('asset_ledger', cfg.get('sweep_asset_ledger') is not False)
    do_chargebacks = _en('vip_chargebacks', cfg.get('sweep_chargebacks') is not False)
    lookback = int(cfg.get('lookback_days') or 14)
    u, pw = cfg['portal_user'], cfg['portal_pass']
    helpers = (_vip_money, _vip_int, _vip_ts, _vip_period)
    parts, errs = [], []

    # Each step runs independently (its own login + session): a network timeout / portal hiccup on
    # one report no longer aborts the rest, so the asset-ledger + chargebacks still land even if an
    # earlier step flakes. A login failure surfaces per-step. Status = ok | partial | error.
    def _step(name, enabled, fn):
        if not enabled:
            return
        try:
            parts.append(fn())
        except vip_sweep.VipLoginError as e:
            errs.append(f"{name}: {e}")
        except Exception as e:
            errs.append(f"{name}: {str(e)[:140]}")

    def _invoices():
        res = vip_sweep.run_invoice_sweep(client, org_id, u, pw, lookback, helpers)
        return f"{res['invoices']} invoices, {res['lines']} lines, {res['devices']} devices ({res['window']})"

    def _paygo():
        ar = vip_sweep.run_paygo_sweep(client, org_id, u, pw, lookback)
        owed = f"${ar['current_owed']:,.2f}" if ar.get('current_owed') is not None else "n/a"
        return f"PayGo: {ar['payments']} batches (current owed {owed})"

    def _creditmemo():
        cr = vip_sweep.run_creditmemo_sweep(client, org_id, u, pw, helpers)
        return f"Credit memos: {cr['credit_memos']} ({cr['xfinity_excluded']} Xfinity excluded)"

    def _asset_ledger():
        al = vip_sweep.run_asset_ledger_sweep(client, org_id, u, pw)
        return f"Asset ledger: {al['rows']} rows"

    def _chargebacks():
        cb = vip_sweep.run_chargeback_sweep(client, org_id, u, pw)
        return f"Chargebacks: {cb['rows']} staged"

    _step('invoices', do_invoices, _invoices)
    _step('paygo', do_asset, _paygo)
    _step('creditmemo', do_creditmemo, _creditmemo)
    _step('asset_ledger', do_asset_ledger, _asset_ledger)
    _step('chargebacks', do_chargebacks, _chargebacks)

    if not parts and not errs:
        _vip_set_status(client, org_id, 'ok', "Nothing enabled (tick a report on the Distributor Sweep page)", mark_run=True)
        return
    status = 'ok' if not errs else ('partial' if parts else 'error')
    detail = (("OK — " if status == 'ok' else "") + " · ".join(parts)
              + ((" | FAILED: " if parts else "FAILED: ") + " · ".join(errs) if errs else ""))
    _vip_set_status(client, org_id, status, detail, mark_run=True)


@router.get("/vip/sweep/config")
async def vip_sweep_get_config(org_id: str = ORG_ID):
    require_org(org_id)
    return _vip_public_cfg(_vip_cfg(sb(), org_id))


@router.put("/vip/sweep/config")
async def vip_sweep_put_config(body: dict, org_id: str = ORG_ID):
    """Update creds + schedule. Password is WRITE-ONLY: send portal_pass to change it,
    omit/blank to keep the existing one. Never returns the password."""
    require_org(org_id)
    client = sb()
    cur = _vip_cfg(client, org_id) or {}
    row = {'org_id': org_id}
    for k in ('frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
              'lookback_days', 'sweep_invoices', 'sweep_asset', 'sweep_creditmemo',
              'sweep_asset_ledger', 'sweep_chargebacks', 'enabled', 'portal_user'):
        if k in body and body[k] is not None:
            row[k] = body[k]
    pw = (body.get('portal_pass') or '').strip()
    if pw:
        row['portal_pass'] = pw
    row['updated_at'] = _datetime.now(_timezone.utc).isoformat()
    merged = {**_VIP_CFG_DEFAULTS, **cur, **row}
    row['next_run_at'] = _vip_next_run(
        merged.get('frequency') or 'weekly', merged.get('day_of_week'),
        merged.get('day_of_month'), merged.get('hour'), merged.get('timezone'))
    client.schema('commcalc').table('vip_sweep_config').upsert(row, on_conflict='org_id').execute()
    return _vip_public_cfg(_vip_cfg(client, org_id))


@router.post("/vip/sweep/run-now")
async def vip_sweep_run_now(background_tasks: BackgroundTasks, org_id: str = ORG_ID):
    """Manual 'Run now' from the admin page (background task)."""
    require_org(org_id)
    cfg = _vip_cfg(sb(), org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        raise HTTPException(400, "Set the Distributor credentials first.")
    background_tasks.add_task(_do_vip_sweep, org_id)
    return {"status": "started"}


@router.post("/vip/sweep/run-due")
async def vip_sweep_run_due(background_tasks: BackgroundTasks, x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint: run every enabled config whose next_run_at has passed.
    Reuses NOTIFY_RUN_SECRET so no new env var is needed."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _datetime.now(_timezone.utc).isoformat()
    due = client.schema('commcalc').table('vip_sweep_config').select('*') \
        .eq('enabled', True).lte('next_run_at', now_iso).execute().data or []
    for cfg in due:
        oid = cfg.get('org_id') or ORG_ID
        nxt = _vip_next_run(cfg.get('frequency') or 'weekly', cfg.get('day_of_week'),
                            cfg.get('day_of_month'), cfg.get('hour'), cfg.get('timezone'))
        client.schema('commcalc').table('vip_sweep_config').update(
            {'next_run_at': nxt}).eq('org_id', oid).execute()
        background_tasks.add_task(_do_vip_sweep, oid)
    return {"triggered": len(due)}


# ── Carrier category map (SaaS framework Phase 1: config-driven canonical components) ────────
def _period_variants(p):
    p = (p or "").strip()
    if not p:
        return []
    out = {p}
    try:
        from dateutil import parser as _dp
        d = _dp.parse(p if len(p) > 7 else p + "-01")
        out.add(d.strftime("%B %Y"))
        out.add(d.strftime("%Y-%m"))
    except Exception:
        pass
    return list(out)


@router.get("/carriers")
def list_carriers(org_id: str = ORG_ID):
    require_org(org_id)
    return (sb().schema("commcalc").table("carrier").select("*").eq("org_id", org_id).order("name").execute().data) or []


@router.post("/carriers")
def create_carrier(body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    is_default = bool(body.get("is_default"))
    row = {"org_id": org_id, "name": name, "code": (body.get("code") or "").strip() or None,
           "is_default": is_default}
    client = sb()
    if is_default:  # only one default carrier per org
        client.schema("commcalc").table("carrier").update({"is_default": False}).eq("org_id", org_id).execute()
    r = client.schema("commcalc").table("carrier").upsert(row, on_conflict="org_id,name").execute()
    return r.data[0] if r.data else row


@router.patch("/carriers/{cid}")
def update_carrier(cid: str, body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    patch = {}
    if "name" in body:
        nm = (body.get("name") or "").strip()
        if not nm:
            raise HTTPException(400, "name cannot be empty")
        patch["name"] = nm
    if "code" in body:
        patch["code"] = (body.get("code") or "").strip() or None
    if "is_default" in body:
        patch["is_default"] = bool(body.get("is_default"))
    if not patch:
        raise HTTPException(400, "nothing to update")
    client = sb()
    if patch.get("is_default"):  # only one default carrier per org
        client.schema("commcalc").table("carrier").update({"is_default": False}).eq("org_id", org_id).neq("id", cid).execute()
    client.schema("commcalc").table("carrier").update(patch).eq("org_id", org_id).eq("id", cid).execute()
    return {"ok": True, "id": cid}


@router.delete("/carriers/{cid}")
def delete_carrier(cid: str, org_id: str = ORG_ID):
    require_org(org_id)
    sb().schema("commcalc").table("carrier").delete().eq("org_id", org_id).eq("id", cid).execute()
    return {"ok": True}


@router.get("/payout-plans/overview")
def payout_plans_overview(org_id: str = ORG_ID):
    """Per-carrier map of HOW each enabled carrier's reps get paid, and whether the calculator will
    actually pay them. Drives the unified 'Commission Payout Plans' hub. Uses the SAME carrier gate
    as the live calc, so what it shows is what /calculate will do."""
    require_org(org_id)
    client = sb()
    def _all(table, cols="*"):
        try:
            return (client.schema("commcalc").table(table).select(cols)
                    .eq("org_id", org_id).limit(10000).execute().data) or []
        except Exception:
            return []
    carriers = _all("carrier")
    plans    = _all("commission_plan", "id,carrier_id,is_active,name")
    assigns  = _all("commission_plan_assignment", "plan_id,scope,scope_value")
    scheds   = _all("payout_schedule", "id,carrier_id,is_active")
    mode     = _resolve_carrier_mode(carriers)
    default  = next((c for c in carriers if c.get("is_default")), None)
    assign_by_plan = {}
    for a in assigns:
        assign_by_plan[a.get("plan_id")] = assign_by_plan.get(a.get("plan_id"), 0) + 1
    def _is_boost(c):
        return 'boost' in ((c.get('code') or '') + ' ' + (c.get('name') or '')).lower()
    out = []
    for c in carriers:
        cid = c.get("id")
        boost = _is_boost(c)
        cplans  = [p for p in plans if p.get("carrier_id") in (cid, None) and p.get("is_active", True)]
        cassign = sum(assign_by_plan.get(p["id"], 0) for p in cplans)
        cscheds = [s for s in scheds if s.get("carrier_id") in (cid, None) and s.get("is_active", True)]
        if boost:
            pays_via, ready = "boost_rates", True
        elif cassign > 0 or len(cscheds) > 0:
            pays_via, ready = "commission_plans", True
        else:
            pays_via, ready = "unconfigured", False
        out.append({
            "id": cid, "name": c.get("name"), "code": c.get("code"),
            "is_default": bool(c.get("is_default")), "is_boost": boost,
            "pays_via": pays_via, "ready": ready,
            "plan_count": len(cplans), "assignment_count": cassign,
            "schedule_count": len(cscheds),
        })
    return {
        "org_carrier_mode": mode,
        "default_carrier": ({"id": default.get("id"), "name": default.get("name"),
                             "code": default.get("code")} if default else None),
        "carriers": out,
    }


@router.get("/payout-plans/diagnose")
def payout_plans_diagnose(period: str, org_id: str = ORG_ID):
    """WHY aren't reps showing in the commission report for this period? Read-only. Reports the rep-roster
    sources (raw_sales / raw_mi), configured plans + assignments, what the plan + installment engines WOULD
    produce, the current rep_commissions count, and a plain-language reason list. Drives the Overview
    'Why is my report empty?' panel."""
    require_org(org_id)
    client = sb()
    from app.modules.commcalc import commission_engine, installment_engine

    def _roster(table, col):
        rows, start, page, names = 0, 0, 1000, {}
        while True:
            try:
                chunk = (client.schema('commcalc').table(table).select(col)
                         .eq('org_id', org_id).in_('period', _pvariants(period))
                         .range(start, start + page - 1).execute().data) or []
            except Exception:
                break
            for r in chunk:
                v = str(r.get(col) or '').strip()
                if v:
                    names[v.upper()] = names.get(v.upper(), 0) + 1
            rows += len(chunk)
            if len(chunk) < page:
                break
            start += page
        return rows, sorted(names.keys())

    sales_n, sales_reps = _roster('raw_sales', 'salesperson')
    mi_n, mi_reps = _roster('raw_mi', 'epay_salesperson')
    carriers = (client.schema('commcalc').table('carrier').select('*').eq('org_id', org_id).execute().data) or []
    mode = _resolve_carrier_mode(carriers)

    plans, _ready = commission_engine._load_plans(client, org_id)
    plan_info = [{"name": p.get("name"), "carrier_id": p.get("carrier_id"),
                  "is_active": p.get("is_active", True), "rules": len(p.get("rules") or []),
                  "assignments": [{"scope": a.get("scope"), "value": a.get("scope_value")}
                                  for a in (p.get("assignments") or [])]} for p in plans]
    n_assign = sum(len(p.get("assignments") or []) for p in plans)
    scoped = [a for p in plans for a in (p.get('assignments') or []) if a.get('scope') in ('market', 'store')]

    try:
        prev = commission_engine.preview(client, org_id, period)
    except Exception as e:
        prev = {"by_rep": [], "note": f"preview error: {e}"}
    try:
        inst = installment_engine.compute_installments(client, org_id, period, persist=False)
    except Exception as e:
        inst = {"by_rep": {}, "totals": {"reps": 0}, "note": f"installment error: {e}"}
    scheds = (client.schema('commcalc').table('payout_schedule').select('id')
              .eq('org_id', org_id).eq('is_active', True).execute().data) or []
    rc = (client.schema('commcalc').table('rep_commissions').select('epay_salesperson')
          .eq('org_id', org_id).in_('period', _pvariants(period)).limit(5000).execute().data) or []

    prev_reps = [r.get("rep") for r in (prev.get("by_rep") or [])]
    inst_reps = list((inst.get("by_rep") or {}).keys())
    reasons = []
    if mode == 'boost':
        reasons.append("Carrier mode is BOOST for this org — it runs the Boost engine, not commission plans. If it should run plans (Total), set its DEFAULT carrier to a non-Boost carrier on tenant Carriers / Carrier Mapping.")
    if sales_n == 0:
        reasons.append("No raw_sales for this period. Commission-PLAN pay is computed from sale LINES, so plan reps come only from sales; with no sales, reps can come only from multi-month installments (raw_mi) or a carrier statement." + (" (In Boost mode this ABORTS the whole calc — now fixed for plan mode.)" if mode == 'boost' else ""))
    if not plans:
        reasons.append("No commission plans configured for this org.")
    elif n_assign == 0:
        reasons.append("Plans exist but have NO rep assignments — no rep is covered. Assign each plan to employees/stores/markets on Commission Plans.")
    if scheds and mi_n == 0:
        reasons.append("Payout schedules exist but there is no raw_mi for this period — multi-month installments have no subscriber/rep rows to pay on. Import the carrier MI/commission file for this period.")
    if scoped:
        reasons.append(f"{len(scoped)} assignment(s) use STORE/MARKET scope — those attach to a rep only if the rep's store (raw_sales.store) resolves to that store/market via Store Matching. An unmapped store → the plan won't attach. If unsure, use EMPLOYEE-scope assignments (rep name must match raw_sales.salesperson / raw_mi rep).")
    if not prev_reps and not inst_reps:
        reasons.append("Neither the plan engine nor the installment engine produced ANY rep for this period → nothing to write. Fix the data/assignment issues above.")
    elif len(rc) == 0:
        reasons.append("The engines WOULD produce reps but rep_commissions is EMPTY — the calc has not been re-run since setup. Click Run Calculation for this period (POST /calculate).")
    if not reasons:
        reasons.append("Engines produced reps and rep_commissions has rows — the report should be populated. If it isn't, check the report's period selector and any rep/store filters.")

    return {
        "period": period, "carrier_mode": mode,
        "sales": {"rows": sales_n, "reps": sales_reps},
        "raw_mi": {"rows": mi_n, "reps": mi_reps},
        "plans": plan_info, "assignments_total": n_assign, "schedules": len(scheds),
        "plan_engine": {"reps": prev_reps, "note": prev.get("note")},
        "installment_engine": {"reps": inst_reps, "totals": inst.get("totals"), "note": inst.get("note")},
        "rep_commissions_now": len(rc),
        "reasons": reasons,
    }


# ── Generic column mapping (A2) — config-driven, any-carrier ingestion ────────────────────────
@router.get("/column-mapping/targets")
def column_mapping_targets(report_key: str = "", org_id: str = ORG_ID):
    """Canonical target fields for a report_key (drives the mapping UI), plus the list of known
    report keys. New report keys (from report_definitions) have no registry → empty fields list,
    and the UI lets the user type target field names freely."""
    require_org(org_id)
    # merged_target_fields layers the per-tenant catalog (user-created categories) on top of the
    # hard-coded defaults; degrades to the defaults when migration 066 isn't applied.
    client = sb()
    fields = commission_catalog.merged_target_fields(client, org_id, report_key) if report_key else []
    return {"report_keys": column_mapping.known_report_keys(client, org_id),
            "transforms": column_mapping.TRANSFORM_KEYS,
            "fields": fields}


@router.get("/column-mapping")
def list_column_mapping(report_key: str = "", carrier_id: str = "", org_id: str = ORG_ID):
    require_org(org_id)
    q = sb().schema("commcalc").table("column_mapping").select("*").eq("org_id", org_id)
    if report_key:
        q = q.eq("report_key", report_key)
    if carrier_id:
        q = q.eq("carrier_id", carrier_id)
    return q.order("priority").execute().data or []


# Which UPLOADED source reports each DESIRED OUTPUT report needs (the implementation wizard's matrix).
_DESIRED_OUTPUTS = {
    "Pay Discrepancy":     ["sales"],
    "Total Compensation":  ["comp_report", "mi_report"],
    "Commissions":         ["sales", "payment_detail", "comp_report"],
    "Gross Profit / P&L":  ["sales", "payment_detail", "mi_report", "comp_report"],
    # statement-driven carriers (Total/VidaPay, Cricket…) pay from their commission statement
    "Commissions (statement carrier)": ["carrier_commission"],
}


@router.get("/column-mapping/readiness")
def column_mapping_readiness(carrier_id: str = "", org_id: str = ORG_ID):
    """Implementation-wizard summary: for each mappable SOURCE report (sales/payment_detail/mi/comp),
    how many of its REQUIRED fields are mapped + is it ready; and for each DESIRED OUTPUT report,
    whether all the source reports it needs are ready. Drives /commcalc/implementation."""
    require_org(org_id)
    client = sb()
    rules = (client.schema("commcalc").table("column_mapping")
             .select("report_key,target_field,source_header,carrier_id").eq("org_id", org_id).execute().data) or []
    by_report: dict = {}
    for r in rules:
        rc = r.get("carrier_id")
        if carrier_id and rc and rc != carrier_id:
            continue
        if r.get("source_header"):
            by_report.setdefault(r["report_key"], set()).add(r["target_field"])
    reports = {}
    for rk in column_mapping.known_report_keys(client, org_id):
        flds = column_mapping.target_fields(rk, client, org_id)
        req = [f["target_field"] for f in flds if f.get("required")]
        mapped = by_report.get(rk, set())
        req_mapped = [t for t in req if t in mapped]
        reports[rk] = {"required": len(req), "required_mapped": len(req_mapped),
                       "total_mapped": len(mapped), "total_fields": len(flds),
                       "ready": bool(req) and len(req_mapped) == len(req)}
    # Custom display name (label) + report_definition id per report, so the wizard can show and
    # rename each report. def_id lets the frontend PATCH the existing row (vs creating a new one).
    defs = (sb().schema("commcalc").table("report_definitions")
            .select("id,report_key,label").eq("org_id", org_id).execute().data) or []
    def_by_key = {d.get("report_key"): d for d in defs}
    for rk, info in reports.items():
        d = def_by_key.get(rk) or {}
        info["label"] = d.get("label")
        info["def_id"] = d.get("id")
    outputs = {}
    for name, needs in _DESIRED_OUTPUTS.items():
        missing = [s for s in needs if not reports.get(s, {}).get("ready")]
        outputs[name] = {"needs": needs, "missing": missing, "ready": not missing}
    return {"reports": reports, "outputs": outputs}


@router.post("/column-mapping")
def upsert_column_mapping(body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    rk = (body.get("report_key") or "").strip()
    tf = (body.get("target_field") or "").strip()
    sh = (body.get("source_header") or "").strip()
    if not rk or not tf or not sh:
        raise HTTPException(400, "report_key, target_field and source_header are required")
    transform = (body.get("transform") or "text").strip()
    if transform not in column_mapping.TRANSFORMS:
        raise HTTPException(400, "transform must be one of " + "|".join(column_mapping.TRANSFORM_KEYS))
    row = {"org_id": org_id, "report_key": rk, "carrier_id": body.get("carrier_id") or None,
           "target_field": tf, "source_header": sh, "transform": transform,
           "is_active": body.get("is_active", True) is not False,
           "priority": int(body.get("priority") or 100),
           "updated_at": column_mapping.now_iso()}
    client = sb()
    if body.get("id"):
        client.schema("commcalc").table("column_mapping").update(row).eq("id", body["id"]).execute()
        return {"ok": True, "id": body["id"]}
    r = client.schema("commcalc").table("column_mapping").upsert(
        row, on_conflict="org_id,report_key,carrier_id,target_field").execute()
    return r.data[0] if r.data else row


@router.delete("/column-mapping/{rid}")
def delete_column_mapping(rid: str, org_id: str = ORG_ID):
    require_org(org_id)
    sb().schema("commcalc").table("column_mapping").delete().eq("org_id", org_id).eq("id", rid).execute()
    return {"ok": True}


@router.post("/column-mapping/seed")
def seed_column_mapping(report_key: str, carrier_id: str = "", overwrite: bool = False, org_id: str = ORG_ID):
    """Seed the known Boost layout for a report_key as editable mapping rows, so a new carrier can
    start from the default and only change the headers that differ. Skips fields already mapped
    unless overwrite=true."""
    require_org(org_id)
    client = sb()
    defaults = column_mapping.default_mapping(report_key, client, org_id)
    if not defaults:
        raise HTTPException(400, f"No default layout known for report_key '{report_key}'")
    existing = {r["target_field"] for r in (client.schema("commcalc").table("column_mapping").select("target_field")
                .eq("org_id", org_id).eq("report_key", report_key)
                .eq("carrier_id", carrier_id) if carrier_id else
                client.schema("commcalc").table("column_mapping").select("target_field")
                .eq("org_id", org_id).eq("report_key", report_key).is_("carrier_id", "null")).execute().data or []}
    seeded = 0
    for d in defaults:
        if not overwrite and d["target_field"] in existing:
            continue
        client.schema("commcalc").table("column_mapping").upsert(
            {"org_id": org_id, "report_key": report_key, "carrier_id": carrier_id or None,
             "target_field": d["target_field"], "source_header": d["source_header"],
             "transform": d["transform"], "priority": d["priority"], "is_active": True,
             "updated_at": column_mapping.now_iso()},
            on_conflict="org_id,report_key,carrier_id,target_field").execute()
        seeded += 1
    return {"ok": True, "seeded": seeded, "report_key": report_key}


@router.post("/column-mapping/detect")
async def detect_column_mapping(report_key: str = Form(...), carrier_id: str = Form(""),
                                file: UploadFile = File(...), org_id: str = ORG_ID):
    """Read an uploaded sample sheet's headers and suggest a source-header → target-field mapping
    (no ingest). Confidence: mapped > exact > alias > fuzzy. Lets the wizard pre-fill the mapping."""
    require_org(org_id)
    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents), dtype=str, nrows=5)
    except Exception as e:
        raise HTTPException(400, f"Could not read Excel file: {e}")
    headers = [str(c).strip() for c in df.columns]
    client = sb()
    rules = column_mapping.load_rules(client, org_id, report_key, carrier_id or None)
    return {"headers": headers, "suggestions": column_mapping.suggest(headers, report_key, rules, client, org_id)}


def _table_has_column(client, table, col):
    """Cheap probe: True if commcalc.<table> has <col>. Lets the config-driven ingest stamp OPTIONAL
    columns (e.g. carrier_id pre/post migration 081) without ever failing an upload on 42703."""
    try:
        client.schema("commcalc").table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


# Positive-only column cache (persist per (table,col) for the process; a genuinely-absent column is
# re-probed each time so a just-run migration is picked up without a redeploy). Bounds the pre-validation
# probe cost on the hot path while never caching a stale "missing".
_TABLE_COL_PRESENT: dict = {}


def _known_columns(client, table, cols):
    """The subset of `cols` that are REAL columns on commcalc.<table> (cached-positive probe per column).
    Used to strip stray/misnamed mapped keys BEFORE the insert so a delete-first replace can never 42703
    (which wipes the period AND errors — the owner's double symptom)."""
    out = set()
    for c in cols:
        key = (table, c)
        if _TABLE_COL_PRESENT.get(key):
            out.add(c)
            continue
        if _table_has_column(client, table, c):
            _TABLE_COL_PRESENT[key] = True
            out.add(c)
    return out


_RESTORE_STRIP_COLS = ("id", "created_at", "updated_at")


def _select_replace_slice(client, table, org_id, period, *, source_null_only=False, chunk=1000):
    """SELECT the (org, period[, source_id IS NULL]) slice that a manual replace is about to delete, so a
    failed insert can restore it. Chunked + id-ordered (stable pagination); serial/default cols stripped
    for clean re-insert. MEMORY BOUND: at most ONE such slice is held at a time, read `chunk` rows per
    round-trip. Raises on read failure so the caller can refuse to delete without a safety net."""
    rows, start = [], 0
    while True:
        q = (client.schema("commcalc").table(table).select("*")
             .eq("org_id", org_id).in_("period", _pvariants(period)))
        if source_null_only:
            q = q.is_("source_id", "null")
        try:
            page = (q.order("id").range(start, start + chunk - 1).execute().data) or []
        except Exception:
            # table without an `id` column (none of ours today) — best-effort single unordered read.
            q2 = (client.schema("commcalc").table(table).select("*")
                  .eq("org_id", org_id).in_("period", _pvariants(period)))
            if source_null_only:
                q2 = q2.is_("source_id", "null")
            page = (q2.execute().data) or []
            rows.extend(page)
            break
        if not page:
            break
        rows.extend(page)
        if len(page) < chunk:
            break
        start += chunk
    return [{k: v for k, v in r.items() if k not in _RESTORE_STRIP_COLS} for r in rows]


def _restore_rows(client, table, rows, chunk=500):
    """Re-insert a saved slice (from _select_replace_slice) to compensate a failed import. Returns
    (restored_count, error_or_None) — a partial restore reports how many made it back so the caller can
    surface exactly what may have been lost. Never raises."""
    restored = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        try:
            client.schema("commcalc").table(table).insert(part).execute()
            restored += len(part)
        except Exception as e:
            return restored, e
    return restored, None


@router.post("/upload-mapped")
async def upload_mapped(
    report_key: str = Form(...),
    target_table: str = Form(""),
    carrier_id: str = Form(""),
    period: str = Form(""),
    file: UploadFile = File(...),
    org_id: str = ORG_ID,
):
    """Generic, config-driven ingest for a NEW carrier's report. Maps the sheet via
    commcalc.column_mapping into the report's target_table, applying the SAME safety guards as the
    legacy upload (defer-delete, never-wipe-on-empty, batched insert, upload_log). The legacy
    /upload/{file_type} path is untouched — this is the additive any-carrier path."""
    require_org(org_id)
    table = (target_table or column_mapping.TABLE_MAP.get(report_key) or "").strip()
    if not table:
        # resolve from report_definitions if the caller didn't pass it
        rd = (sb().schema("commcalc").table("report_definitions").select("target_table")
              .eq("org_id", org_id).eq("report_key", report_key).limit(1).execute().data) or []
        table = (rd[0].get("target_table") if rd else "") or ""
    if not table:
        raise HTTPException(400, f"No target_table for report_key '{report_key}'. Pass target_table or set it on the report definition.")

    rules = column_mapping.load_rules(sb(), org_id, report_key, carrier_id or None)
    used_defaults = False
    if not rules:
        # No SAVED column_mapping rows → fall back to the code-default seed layout (report_pull-derived for
        # the MA reports, TARGET_FIELDS for the Boost layouts) so the onboarding import works out of the box
        # without a manual "Seed default layout" click. Previously this raised 400 → the MA reports (which
        # had NO seed at all) could never import from onboarding. A genuinely-unmapped key still 400s below.
        rules = column_mapping.default_mapping(report_key, sb(), org_id)
        used_defaults = bool(rules)
    if not rules:
        raise HTTPException(400, f"No column mapping configured for '{report_key}' and no default layout "
                                 f"exists to seed. Map its columns first on the Column Mapping page.")

    contents = await file.read()
    try:
        df = _read_upload_df(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")

    pm = parse_period(period) if period else {"month": 0, "year": 0}
    base = {"org_id": org_id}
    if period:
        base.update({"period": period, "period_month": pm["month"], "period_year": pm["year"]})
    # Stamp the statement's carrier on every row when the caller names one AND the target table has
    # the column (probe = pre-081 tolerant). The installment engine resolves carrier-SCOPED payout
    # schedules (Total Wireless, mig 078) from row.carrier_id — unstamped rows can never match one.
    # Boost is unaffected: its ePay/legacy paths don't come through here and stay NULL.
    if (carrier_id or "").strip() and _table_has_column(sb(), table, "carrier_id"):
        base["carrier_id"] = carrier_id.strip()
    mapped = column_mapping.map_records(df.to_dict("records"), rules, base)

    # carrier_commission: roll the mapped component amounts into total_commission (the rep's statement
    # commission) so the calc can sum per rep. Amount columns come from the per-tenant catalog (so
    # user-created categories are summed too); falls back to the hard-coded tuple pre-066. Any carrier
    # maps the columns it has; the rest stay 0.
    if report_key == 'carrier_commission':
        amt_fields = commission_catalog.amount_fields(sb(), org_id, 'carrier_commission')
        for m in mapped:
            m['total_commission'] = round(sum(safe_float(m.get(a)) for a in amt_fields), 2)

    client = sb()
    started = _datetime.now(timezone.utc)
    fname = getattr(file, "filename", None)
    parsed_n = len(df)

    def _trace(status, rows_saved, note, error=None):
        # mig-202 upload_trace (source='onboarding-import') so 🩺 Ingest health sees these imports too.
        guard = {"dropped_columns": dropped_columns} if dropped_columns else None
        _write_upload_trace(
            org_id, source="onboarding-import", filename=fname, upload_type=report_key, period=period,
            result={"saved": rows_saved, "note": note, "guard": guard,
                    "_trace": {"rows_in": parsed_n, "target_table": table}},
            duration_ms=int((_datetime.now(timezone.utc) - started).total_seconds() * 1000),
            error=error, status=status)

    # ── (a) PRE-VALIDATE columns: keep only keys that are REAL columns on the target table so a stray
    #     unmapped/misnamed field can NEVER 42703 the insert (which — with a delete-first — wipes the
    #     period AND errors: the owner's "no data uploads / manual data overwritten" double symptom). Base
    #     keys (org_id/period/carrier_id) are ours and always kept; dropped keys are reported, not swallowed.
    always_keep = set(base.keys())
    dropped_columns: list = []
    if mapped:
        seen_keys: set = set()
        for m in mapped:
            seen_keys.update(m.keys())
        candidate = [k for k in seen_keys if k not in always_keep]
        valid = _known_columns(client, table, candidate)
        dropped_columns = sorted(k for k in candidate if k not in valid)
        if dropped_columns:
            keep = always_keep | valid
            mapped = [{k: v for k, v in m.items() if k in keep} for m in mapped]
        # Friendly 400 (never a raw Postgres 500) when the mapping is so misaligned NOTHING would land.
        if not any(any(k not in always_keep for k in m) for m in mapped):
            _trace("error", 0, "no known columns after mapping — nothing changed",
                   error=f"unknown columns: {', '.join(dropped_columns) or '(none)'}")
            raise HTTPException(400, f"None of the mapped columns exist on commcalc.{table}. "
                f"Unknown column(s): {', '.join(dropped_columns) or '(none)'}. "
                f"Fix the column mapping for '{report_key}' and re-import — no data was changed.")

    # ── (b) SOURCE-AWARE + COMPENSATING-RESTORE replace. On the raw_ma_* family (source_id column) the
    #     manual replace scopes its delete to source_id IS NULL — portal-pulled rows survive (report_pull's
    #     coexistence contract, mirrors ma_upload HISTORICAL). Legacy tables (no source_id) keep the full-
    #     period replace. Either way: snapshot the to-be-deleted slice FIRST; on ANY insert failure restore
    #     it so a failed import leaves the table AT LEAST as full as before (no DB transactions in supabase-py).
    source_aware = _table_has_column(client, table, "source_id")
    saved = 0
    if mapped and period:
        try:
            snapshot = _select_replace_slice(client, table, org_id, period, source_null_only=source_aware)
        except Exception as e:
            _trace("error", 0, "aborted before delete — could not snapshot existing rows", error=str(e))
            raise HTTPException(500, f"Could not snapshot existing {table}/{period} rows for a safe replace; "
                                     f"aborted to avoid data loss: {e}")
        try:
            d = (client.schema("commcalc").table(table).delete()
                 .eq("org_id", org_id).in_("period", _pvariants(period)))
            if source_aware:
                d = d.is_("source_id", "null")
            d.execute()
        except Exception as e:
            _trace("error", 0, "delete failed — nothing deleted", error=str(e))
            raise HTTPException(500, f"Failed to clear existing data for {table}/{period}: {e}")
        try:
            for i in range(0, len(mapped), 500):
                client.schema("commcalc").table(table).insert(mapped[i:i + 500]).execute()
                saved += len(mapped[i:i + 500])
        except Exception as e:
            # RESTORE the snapshot so the table is at least as full as before this failed import.
            restored, rerr = _restore_rows(client, table, snapshot)
            if rerr is not None:
                lost = max(0, len(snapshot) - restored)
                note = (f"insert failed after delete AND restore INCOMPLETE — {restored}/{len(snapshot)} prior "
                        f"row(s) re-inserted, {lost} at risk. Re-pull (portal) or re-upload (manual) {report_key}.")
                _trace("restore_failed", saved, note, error=f"{e} | restore error: {rerr}")
                raise HTTPException(500, f"Insert into {table} failed AND restore was incomplete "
                    f"({restored}/{len(snapshot)} prior rows restored, {lost} at risk). Original error: {e}")
            note = (f"insert failed at row {saved}; restored {restored} prior row(s) — no data lost.")
            _trace("error", 0, note, error=str(e))
            raise HTTPException(400, f"Import failed: {e}. Restored {restored} prior row(s) for {table}/{period} — "
                                     f"no data lost. Fix the file/mapping and re-import.")
    else:
        # No period ⇒ pure append (never deletes). Guard each chunk; nothing to restore.
        try:
            for i in range(0, len(mapped), 500):
                client.schema("commcalc").table(table).insert(mapped[i:i + 500]).execute()
                saved += len(mapped[i:i + 500])
        except Exception as e:
            _trace("partial" if saved else "error", saved, "append insert failed (no delete performed)", error=str(e))
            raise HTTPException(500, f"Insert into {table} failed at row {saved}: {e}")

    try:
        client.schema("commcalc").table("upload_log").insert(
            {"org_id": org_id, "file_type": report_key, "period": period or None,
             "filename": fname, "rows_saved": saved}).execute()
    except Exception as e:
        print(f"WARN upload_log insert failed: {e}")
    note = (f"onboarding import: {saved} row(s) into {table}"
            + (f" (default layout)" if used_defaults else "")
            + (f"; scoped to manual rows (source_id IS NULL) — portal-pulled rows preserved" if source_aware and period else "")
            + (f"; dropped {len(dropped_columns)} unknown column(s): {', '.join(dropped_columns)}" if dropped_columns else ""))
    _trace("partial" if dropped_columns else "ok", saved, note)
    return {"saved": saved, "report_key": report_key, "target_table": table, "period": period,
            "rules_used": len(rules), "used_defaults": used_defaults, "mapped": len(mapped),
            "dropped_columns": dropped_columns, "source_scoped": bool(source_aware and period), "note": note}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# UNIVERSAL COMMISSION MAPPER (SAP-style, self-extending) — catalog of categories + the import wizard.
# Categories are DATA (commcalc.commission_field_catalog, mig 066). A new category creates a real column
# on carrier_commission via the RPC commcalc.add_commission_column (mig 067). All ADDITIVE + BOOST-SAFE:
# only carrier_commission + the catalog table are touched; the live Boost calc is never involved.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _read_upload_df(contents: bytes, filename: str):
    """Read an uploaded sheet into a string DataFrame, honoring .csv/.txt vs Excel by extension
    (the wizard advertises CSV too — pd.read_excel alone throws on a CSV)."""
    fname = (filename or "").lower()
    if fname.endswith((".csv", ".txt")):
        return pd.read_csv(io.BytesIO(contents), dtype=str).fillna("")
    return pd.read_excel(io.BytesIO(contents), dtype=str).fillna("")


@router.get("/commission-fields")
def list_commission_fields(report_key: str = "carrier_commission", org_id: str = ORG_ID):
    """The category catalog for a report_key: merged (defaults + tenant catalog) for the wizard dropdown,
    plus the raw catalog rows and whether the self-extend RPC is usable. Degrades pre-066/067."""
    require_org(org_id)
    client = sb()
    merged = commission_catalog.merged_target_fields(client, org_id, report_key)
    raw = commission_catalog.load_catalog(client, org_id, report_key)
    return {"report_key": report_key, "fields": merged, "catalog": raw,
            "kinds": commission_catalog.KINDS, "transforms": column_mapping.TRANSFORM_KEYS,
            "catalog_ready": bool(raw)}


@router.post("/commission-fields")
def create_commission_field(body: dict, org_id: str = ORG_ID):
    """Create a NEW commission category → adds the physical column on carrier_commission (via RPC) AND a
    catalog row. body: {report_key?, label, kind?, data_type?, is_amount?, month_index?, target_field?}.
    Returns a clear 400 (not a 500) if migration 067 isn't installed yet."""
    require_org(org_id)
    label = (body.get("label") or "").strip()
    if not label and not body.get("target_field"):
        raise HTTPException(400, "label (or target_field) is required")
    try:
        row = commission_catalog.add_field(
            sb(), org_id, body.get("report_key") or "carrier_commission",
            label=label, kind=body.get("kind") or "other", data_type=body.get("data_type") or "number",
            is_amount=body.get("is_amount"), month_index=body.get("month_index"),
            target_field=body.get("target_field"), sort_order=int(body.get("sort_order") or 100))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "field": row}


@router.delete("/commission-fields")
def delete_commission_field(target_field: str, report_key: str = "carrier_commission", org_id: str = ORG_ID):
    """Remove a USER-CREATED category from the catalog (seeded defaults are protected; the physical column
    is left intact so existing data is never dropped)."""
    require_org(org_id)
    removed = commission_catalog.remove_field(sb(), org_id, report_key, target_field)
    if not removed:
        raise HTTPException(400, f"'{target_field}' is a seeded default or not found — cannot remove.")
    return {"ok": True, "removed": target_field}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# GENERIC TARGET FIELD REGISTRY (C-Phase2) — per-tenant canonical fields for ANY report_key, merged on
# top of the hard-coded column_mapping.TARGET_FIELDS. Generalises the commission catalog (066) to every
# report type, with NO commission semantics and NO schema DDL (commcalc.target_field_registry, mig 070).
# ═══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/target-fields")
def list_target_fields(report_key: str = "", org_id: str = ORG_ID):
    """The canonical target fields for a report_key (defaults + per-tenant registry merged), each tagged
    default|custom, plus the raw registry rows and the known report keys. Degrades to defaults pre-070."""
    require_org(org_id)
    client = sb()
    fields = column_mapping.target_fields(report_key, client, org_id) if report_key else []
    raw = target_registry.load_registry(client, org_id, report_key) if report_key else []
    custom = {r.get("target_field") for r in raw}
    for f in fields:
        f["source"] = "custom" if f["target_field"] in custom else "default"
    return {"report_key": report_key,
            "report_keys": column_mapping.known_report_keys(client, org_id),
            "fields": fields, "registry": raw,
            "transforms": column_mapping.TRANSFORM_KEYS,
            "registry_ready": target_registry.table_ready(client)}


@router.post("/target-fields")
def create_target_field(body: dict, org_id: str = ORG_ID):
    """Create/update a per-tenant target field for ANY report_key. body: {report_key, label, transform?,
    required?, default_source?, aliases?(list|comma-string), sort_order?, target_field?}. NO DDL — this is
    purely the mappable field list. Returns a clear 400 (not a 500) if migration 070 isn't applied."""
    require_org(org_id)
    rk = (body.get("report_key") or "").strip()
    label = (body.get("label") or "").strip()
    if not rk:
        raise HTTPException(400, "report_key is required")
    if not label and not body.get("target_field"):
        raise HTTPException(400, "label (or target_field) is required")
    transform = (body.get("transform") or "text").strip()
    if transform not in column_mapping.TRANSFORMS:
        raise HTTPException(400, "transform must be one of " + "|".join(column_mapping.TRANSFORM_KEYS))
    try:
        row = target_registry.add_field(
            sb(), org_id, rk, label=label, transform=transform,
            required=bool(body.get("required")), default_source=body.get("default_source") or "",
            aliases=body.get("aliases"), sort_order=int(body.get("sort_order") or 100),
            target_field=body.get("target_field"))
    except Exception as e:
        raise HTTPException(400, f"Could not save — run migration 070_target_field_registry.sql first. [{e}]")
    return {"ok": True, "field": row}


@router.delete("/target-fields")
def delete_target_field(target_field: str, report_key: str, org_id: str = ORG_ID):
    """Remove a USER-CREATED registry field (built-in defaults are protected — they live in code, not the
    registry, so they can never be deleted here)."""
    require_org(org_id)
    removed = target_registry.remove_field(sb(), org_id, report_key, target_field)
    if not removed:
        raise HTTPException(400, f"'{target_field}' is a built-in default or not found — cannot remove.")
    return {"ok": True, "removed": target_field}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# CANONICAL COMMISSION/PAYOUT LEDGER (SAP-style) — normalise ANY carrier's commission file into five
# canonical buckets (Commission / Spiff / Equipment rebate / Residual-monthly / Auto Pay residual) via a
# per-tenant rule map. A multi-month payout stays one category but keeps its payment_month. Negative =
# payout; positive = a bill/activation payment (stored, kept out of the buckets). Tables: mig 071.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _ledger_source_rules(client, org_id, carrier_id=""):
    """Header→field rules for the commission_ledger source: saved column_mapping if present, else the
    built-in MA Daily Tx default layout — so a fresh tenant's MA file imports with zero configuration."""
    rules = column_mapping.load_rules(client, org_id, "commission_ledger", carrier_id or None)
    if rules:
        return rules
    return [{"target_field": d["target_field"], "source_header": d["source_header"], "transform": d["transform"]}
            for d in column_mapping.default_mapping("commission_ledger")]


@router.post("/commission-ledger/import")
async def commission_ledger_import(
    file: UploadFile = File(...),
    source_report: str = Form("ma_daily_tx"),
    period: str = Form(""),
    carrier_id: str = Form(""),
    org_id: str = ORG_ID,
):
    """Upload a commission/tx file → map its headers → CLASSIFY each line into the five canonical buckets
    (via commission_category_map / DEFAULT_RULES) → persist to commcalc.commission_ledger. Negative amounts
    are payouts; positives are bill/activation payments (is_payout=false, no bucket). Re-upload for a period
    replaces it (never wipes on an empty/misaligned file)."""
    require_org(org_id)
    contents = await file.read()
    try:
        df = _read_upload_df(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    client = sb()
    hdr_rules = _ledger_source_rules(client, org_id, carrier_id)
    cat_rules = commission_ledger.load_rules(client, org_id, source_report)
    base = {"org_id": org_id, "source_report": source_report}
    if period:
        base["period"] = period
    rows = []
    for r in df.to_dict("records"):
        src = column_mapping.apply_mapping(r, hdr_rules, {})
        if not (src.get("product_name") or src.get("raw_amount") or src.get("order_type")):
            continue
        rows.append(commission_ledger.build_row(src, base, cat_rules))
    if not rows:
        raise HTTPException(400, "No usable rows — check the column mapping for this file.")
    # GUARD: only clear once we have rows; scope the wipe to this source_report + period.
    if period:
        try:
            client.schema("commcalc").table("commission_ledger").delete() \
                .eq("org_id", org_id).eq("source_report", source_report).eq("period", period).execute()
        except Exception as e:
            raise HTTPException(500, f"Failed to clear existing ledger for {source_report}/{period}: {e}")
    saved = 0
    for i in range(0, len(rows), 500):
        try:
            client.schema("commcalc").table("commission_ledger").insert(rows[i:i + 500]).execute()
            saved += len(rows[i:i + 500])
        except Exception as e:
            raise HTTPException(500, f"Insert into commission_ledger failed at row {i}: {e} — is migration 071 applied?")
    try:
        client.schema("commcalc").table("upload_log").insert(
            {"org_id": org_id, "file_type": "commission_ledger", "period": period or None,
             "filename": getattr(file, "filename", None), "rows_saved": saved}).execute()
    except Exception as e:
        print(f"WARN upload_log insert failed: {e}")
    summary = commission_ledger.summarize(rows)
    return {"saved": saved, "source_report": source_report, "period": period, "summary": summary}


@router.post("/commission-ledger/analyze")
async def commission_ledger_analyze(
    file: UploadFile = File(...),
    source_report: str = Form("ma_daily_tx"),
    carrier_id: str = Form(""),
    org_id: str = ORG_ID,
):
    """READ-ONLY preview for the setup wizard — reads the uploaded file, shows which columns it detected
    (header → field), and PREVIEWS how every line would classify into the five buckets, WITHOUT saving
    anything. Surfaces any unmapped ('other') labels so the user can add a rule before importing for real."""
    require_org(org_id)
    contents = await file.read()
    try:
        df = _read_upload_df(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    client = sb()
    headers = [str(h).strip() for h in df.columns if str(h).strip()]
    saved = column_mapping.load_rules(client, org_id, "commission_ledger", carrier_id or None)
    suggestions = column_mapping.suggest(headers, "commission_ledger", saved, client, org_id)
    hdr_rules = _ledger_source_rules(client, org_id, carrier_id)
    cat_rules = commission_ledger.load_rules(client, org_id, source_report)
    rows = []
    for r in df.to_dict("records"):
        src = column_mapping.apply_mapping(r, hdr_rules, {})
        if not (src.get("product_name") or src.get("raw_amount") or src.get("order_type")):
            continue
        rows.append(commission_ledger.build_row(src, {"org_id": org_id, "source_report": source_report}, cat_rules))
    agg = {}
    for r in rows:
        key = (r.get("order_type") or "", r.get("product_name") or "")
        a = agg.setdefault(key, {"order_type": key[0], "product_name": key[1], "count": 0,
                                 "payout_total": 0.0, "category": r.get("category")})
        a["count"] += 1
        a["payout_total"] = round(a["payout_total"] + safe_float(r.get("payout_total")), 2)
    observed = sorted(agg.values(), key=lambda x: (-x["payout_total"], x["product_name"]))
    amount_src = next((s["suggested_source"] for s in suggestions if s["target_field"] == "raw_amount"), "")
    return {"headers": headers, "row_count": int(len(df)), "usable_rows": len(rows),
            "suggestions": suggestions, "amount_source": amount_src,
            "summary": commission_ledger.summarize(rows), "observed": observed,
            "categories": commission_ledger.CATEGORIES, "category_labels": commission_ledger.CATEGORY_LABELS}


@router.get("/commission-ledger/summary")
def commission_ledger_summary(source_report: str = "ma_daily_tx", period: str = "", org_id: str = ORG_ID):
    """Per-category totals + counts, a (category × payment_month) matrix, payout/charge/other totals — for
    the canonical commission report. Empty (not 500) if migration 071 isn't applied yet."""
    require_org(org_id)
    try:
        q = (sb().schema("commcalc").table("commission_ledger").select("*")
             .eq("org_id", org_id).eq("source_report", source_report))
        if period:
            q = q.eq("period", period)
        rows = q.execute().data or []
    except Exception:
        rows = []
    return {"source_report": source_report, "period": period, **commission_ledger.summarize(rows)}


@router.get("/commission-ledger/rows")
def commission_ledger_rows(source_report: str = "ma_daily_tx", period: str = "", category: str = "",
                           rep_user: str = "", limit: int = 2000, org_id: str = ORG_ID):
    """Ledger line items (optionally filtered by category/rep) for the report drill-downs."""
    require_org(org_id)
    try:
        q = (sb().schema("commcalc").table("commission_ledger").select("*")
             .eq("org_id", org_id).eq("source_report", source_report))
        if period:
            q = q.eq("period", period)
        if category:
            q = q.eq("category", category)
        if rep_user:
            q = q.eq("rep_user", rep_user)
        rows = q.order("payout_total", desc=True).limit(min(int(limit or 2000), 5000)).execute().data or []
    except Exception:
        rows = []
    return {"rows": rows, "count": len(rows)}


@router.get("/commission-ledger/observed-types")
def commission_ledger_observed_types(source_report: str = "ma_daily_tx", period: str = "", org_id: str = ORG_ID):
    """Distinct (order_type, product_name) seen in the ledger with line count, summed payout, and the
    category they CURRENTLY classify to under the active rules — so the user can spot 'other' (unmapped)
    labels and add a rule. Mirrors the gp-departments pattern for the category-map editor."""
    require_org(org_id)
    client = sb()
    try:
        q = (client.schema("commcalc").table("commission_ledger")
             .select("order_type,product_name,category,payout_total,is_payout")
             .eq("org_id", org_id).eq("source_report", source_report))
        if period:
            q = q.eq("period", period)
        rows = q.limit(20000).execute().data or []
    except Exception:
        rows = []
    agg = {}
    for r in rows:
        key = (r.get("order_type") or "", r.get("product_name") or "")
        a = agg.setdefault(key, {"order_type": key[0], "product_name": key[1], "count": 0,
                                 "payout_total": 0.0, "category": r.get("category"), "is_payout": r.get("is_payout")})
        a["count"] += 1
        a["payout_total"] = round(a["payout_total"] + safe_float(r.get("payout_total")), 2)
    out = sorted(agg.values(), key=lambda x: (-x["payout_total"], x["product_name"]))
    return {"types": out, "count": len(out)}


@router.get("/commission-ledger/by-rep")
def commission_ledger_by_rep(source_report: str = "ma_daily_tx", period: str = "", org_id: str = ORG_ID):
    """Per-REP rollup of the canonical commission ledger: each rep's five-bucket payout totals from
    commcalc.commission_ledger, joined to what the LIVE calc actually pays them (rep_commissions.total_payout)
    for the same period, keyed on the canonical rep name. This is the 'unified rep payout view' — the ledger
    sits ALONGSIDE the existing payout, it does not replace it. READ-ONLY: never writes rep_commissions and
    never touches the live calc. Empty (not 500) if migration 071 isn't applied yet."""
    require_org(org_id)
    client = sb()
    CATS = commission_ledger.CATEGORIES
    # 1. ledger payouts for this template/period, grouped by canonical rep
    try:
        q = (client.schema("commcalc").table("commission_ledger")
             .select("rep_user,payout_total," + ",".join(CATS))
             .eq("org_id", org_id).eq("source_report", source_report))
        if period:
            q = q.in_("period", _pvariants(period))
        lrows = q.limit(100000).execute().data or []
    except Exception:
        lrows = []
    cmap = _rep_canon_map(client, org_id)
    reps = {}
    for r in lrows:
        rep = _canon((r.get("rep_user") or "").strip(), cmap) or "(unattributed)"
        a = reps.setdefault(rep, {"rep": rep, "lines": 0, "ledger_payout": 0.0, **{c: 0.0 for c in CATS}})
        a["lines"] += 1
        a["ledger_payout"] = round(a["ledger_payout"] + safe_float(r.get("payout_total")), 2)
        for c in CATS:
            a[c] = round(a[c] + safe_float(r.get(c)), 2)
    # 2. live rep_commissions payout for the same period, keyed by the same canonical name
    live = {}
    if period:
        try:
            rc = (client.schema("commcalc").table("rep_commissions")
                  .select("epay_salesperson,storeops_name,total_payout")
                  .eq("org_id", org_id).in_("period", _pvariants(period)).execute().data) or []
            for cr in rc:
                for nm in (cr.get("storeops_name"), cr.get("epay_salesperson")):
                    k = _canon((nm or "").strip(), cmap)
                    if k:
                        live[k] = round(live.get(k, 0.0) + safe_float(cr.get("total_payout")), 2)
                        break  # credit each rep_commissions row once
        except Exception:
            pass
    out = []
    for rep, a in reps.items():
        a["live_payout"] = live.get(rep)            # None when the rep has no live rep_commissions row
        a["matched"] = rep in live
        out.append(a)
    out.sort(key=lambda x: -x["ledger_payout"])
    totals = {"ledger_payout": round(sum(a["ledger_payout"] for a in out), 2),
              "live_payout": round(sum((a.get("live_payout") or 0.0) for a in out), 2),
              **{c: round(sum(a[c] for a in out), 2) for c in CATS}}
    return {"source_report": source_report, "period": period, "reps": out, "totals": totals,
            "categories": CATS, "category_labels": commission_ledger.CATEGORY_LABELS,
            "matched_count": sum(1 for a in out if a["matched"]), "rep_count": len(out)}


@router.get("/commission-ledger/templates")
def commission_ledger_templates(org_id: str = ORG_ID):
    """Preconfigured templates (Total/Boost) + any tenant-created rule-sets — a new tenant adopts one or
    forks their own. Drives the template picker on the report + category-map pages."""
    require_org(org_id)
    return {"templates": commission_ledger.list_templates(sb(), org_id),
            "categories": commission_ledger.CATEGORIES,
            "category_labels": commission_ledger.CATEGORY_LABELS}


@router.get("/commission-category-map")
def get_commission_category_map(source_report: str = "ma_daily_tx", org_id: str = ORG_ID):
    """The classification rules for a template (ascending priority = match order), plus the metadata the
    editor needs (categories, match fields/ops, sign rules). Falls back to DEFAULT_RULES pre-071."""
    require_org(org_id)
    client = sb()
    try:
        rows = (client.schema("commcalc").table("commission_category_map").select("*")
                .eq("org_id", org_id).eq("source_report", source_report).order("priority").execute().data) or []
        ready = True
    except Exception:
        rows, ready = [], False
    return {"source_report": source_report, "rules": rows, "ready": ready,
            "using_defaults": not rows,
            "default_rules": [{"match_field": mf, "match_op": op, "pattern": pat, "category": cat,
                               "sign_rule": sr, "priority": pr} for (mf, op, pat, cat, sr, pr) in commission_ledger.DEFAULT_RULES],
            "categories": commission_ledger.CATEGORIES, "category_labels": commission_ledger.CATEGORY_LABELS,
            "match_fields": commission_ledger.MATCH_FIELDS, "match_ops": commission_ledger.MATCH_OPS,
            "sign_rules": commission_ledger.SIGN_RULES}


@router.post("/commission-category-map")
def upsert_commission_category_map(body: dict, org_id: str = ORG_ID):
    """Create/update one classification rule. body: {id?, source_report, match_field, match_op, pattern,
    category, sign_rule?, priority?}. 400 (not 500) if migration 071 isn't applied."""
    require_org(org_id)
    sr = (body.get("source_report") or "ma_daily_tx").strip()
    pattern = (body.get("pattern") or "").strip()
    category = (body.get("category") or "").strip().lower()
    if not pattern or not category:
        raise HTTPException(400, "pattern and category are required")
    mf = (body.get("match_field") or "product_name").strip()
    op = (body.get("match_op") or "contains").strip()
    sign = (body.get("sign_rule") or "negative_only").strip()
    if mf not in commission_ledger.MATCH_FIELDS or op not in commission_ledger.MATCH_OPS or sign not in commission_ledger.SIGN_RULES:
        raise HTTPException(400, "invalid match_field / match_op / sign_rule")
    row = {"org_id": org_id, "source_report": sr, "match_field": mf, "match_op": op, "pattern": pattern,
           "category": category, "sign_rule": sign, "priority": int(body.get("priority") or 100),
           "is_seeded": False, "updated_at": column_mapping.now_iso()}
    client = sb()
    try:
        if body.get("id"):
            client.schema("commcalc").table("commission_category_map").update(row).eq("id", body["id"]).execute()
            return {"ok": True, "id": body["id"]}
        r = client.schema("commcalc").table("commission_category_map").upsert(
            row, on_conflict="org_id,source_report,match_field,match_op,pattern").execute()
        return {"ok": True, "rule": (r.data[0] if r.data else row)}
    except Exception as e:
        raise HTTPException(400, f"Could not save — run migration 071_commission_ledger.sql first. [{e}]")


@router.delete("/commission-category-map/{rid}")
def delete_commission_category_map(rid: str, org_id: str = ORG_ID):
    """Delete one classification rule by id (seeded defaults can be deleted too — they re-seed only via SQL)."""
    require_org(org_id)
    sb().schema("commcalc").table("commission_category_map").delete().eq("org_id", org_id).eq("id", rid).execute()
    return {"ok": True}


@router.post("/commission-import/analyze")
async def commission_import_analyze(file: UploadFile = File(...), report_key: str = Form("carrier_commission"),
                                    carrier_id: str = Form(""), org_id: str = ORG_ID):
    """Wizard step 1: read the uploaded sheet → return its columns + a few SAMPLE VALUES per column, the
    catalog categories to map onto, auto-suggested matches, and any already-saved mapping for this
    (report_key, carrier). The frontend renders one row per source column with a category dropdown."""
    require_org(org_id)
    contents = await file.read()
    try:
        df = _read_upload_df(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    headers = [str(h).strip() for h in df.columns if str(h).strip()]
    samples = {}
    for h in headers:
        vals = [str(v).strip() for v in df[h].tolist() if str(v).strip()]
        samples[h] = vals[:5]
    client = sb()
    rules = column_mapping.load_rules(client, org_id, report_key, carrier_id or None)
    suggestions = column_mapping.suggest(headers, report_key, rules)
    fields = commission_catalog.merged_target_fields(client, org_id, report_key)
    saved = {r["target_field"]: r.get("source_header") for r in rules}
    return {"report_key": report_key, "headers": headers, "row_count": int(len(df)), "samples": samples,
            "suggestions": suggestions, "fields": fields, "saved_mapping": saved,
            "kinds": commission_catalog.KINDS, "transforms": column_mapping.TRANSFORM_KEYS}


@router.post("/commission-import/commit")
async def commission_import_commit(
    file: UploadFile = File(...),
    report_key: str = Form("carrier_commission"),
    carrier_id: str = Form(""),
    period: str = Form(""),
    new_fields: str = Form("[]"),
    mappings: str = Form("[]"),
    save_template: bool = Form(True),
    org_id: str = ORG_ID,
):
    """Wizard step 2 (one shot): (1) create any NEW categories the user defined (column + catalog row),
    (2) optionally persist the column→category mapping as a reusable per-carrier template, (3) ingest the
    file into carrier_commission with the dynamic total roll-up. Same safety guards as /upload-mapped
    (never wipe on empty, batched insert, upload_log). BOOST-SAFE — only carrier_commission is written."""
    import json
    require_org(org_id)
    client = sb()
    table = (column_mapping.TABLE_MAP.get(report_key) or "").strip()
    if not table:
        rd = (client.schema("commcalc").table("report_definitions").select("target_table")
              .eq("org_id", org_id).eq("report_key", report_key).limit(1).execute().data) or []
        table = (rd[0].get("target_table") if rd else "") or ""
    if not table:
        raise HTTPException(400, f"No target_table for report_key '{report_key}'.")

    try:
        new_field_list = json.loads(new_fields or "[]")
        mapping_list = json.loads(mappings or "[]")
    except Exception as e:
        raise HTTPException(400, f"Bad new_fields/mappings JSON: {e}")

    # 1) create new categories first (so their columns exist before insert + before mapping persists).
    created = []
    for nf in new_field_list:
        try:
            row = commission_catalog.add_field(
                client, org_id, report_key, label=(nf.get("label") or "").strip(),
                kind=nf.get("kind") or "other", data_type=nf.get("data_type") or "number",
                is_amount=nf.get("is_amount"), month_index=nf.get("month_index"),
                target_field=nf.get("target_field"))
            created.append(row["target_field"])
        except RuntimeError as e:
            raise HTTPException(400, str(e))

    # 2) build the effective rules from the wizard's mapping; optionally persist as a template.
    rules, persisted = [], 0
    for m in mapping_list:
        tf = (m.get("target_field") or "").strip()
        src = (m.get("source_header") or "").strip()
        if not tf or not src:
            continue
        transform = m.get("transform") or "text"
        rules.append({"target_field": tf, "source_header": src, "transform": transform})
        if save_template:
            try:
                client.schema("commcalc").table("column_mapping").upsert(
                    {"org_id": org_id, "report_key": report_key, "carrier_id": carrier_id or None,
                     "target_field": tf, "source_header": src, "transform": transform, "priority": 100,
                     "is_active": True, "updated_at": column_mapping.now_iso()},
                    on_conflict="org_id,report_key,carrier_id,target_field").execute()
                persisted += 1
            except Exception as e:
                print(f"WARN column_mapping upsert failed for {tf}: {e}")
    if not rules:
        raise HTTPException(400, "No usable column→category mappings provided.")

    # 3) ingest (mirrors /upload-mapped guards).
    contents = await file.read()
    try:
        df = _read_upload_df(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    pm = parse_period(period) if period else {"month": 0, "year": 0}
    base = {"org_id": org_id}
    if period:
        base.update({"period": period, "period_month": pm["month"], "period_year": pm["year"]})
    # Same carrier stamp as /upload-mapped (see comment there) — wizard commits carry carrier_id too.
    if (carrier_id or "").strip() and _table_has_column(client, table, "carrier_id"):
        base["carrier_id"] = carrier_id.strip()
    mapped = column_mapping.map_records(df.to_dict("records"), rules, base)
    if report_key == "carrier_commission":
        amt_fields = commission_catalog.amount_fields(client, org_id, "carrier_commission")
        for mrow in mapped:
            mrow["total_commission"] = round(sum(safe_float(mrow.get(a)) for a in amt_fields), 2)

    if mapped and period:
        try:
            client.schema("commcalc").table(table).delete().eq("org_id", org_id).in_("period", _pvariants(period)).execute()
        except Exception as e:
            raise HTTPException(500, f"Failed to clear existing {table}/{period}: {e}")
    saved = 0
    for i in range(0, len(mapped), 500):
        try:
            client.schema("commcalc").table(table).insert(mapped[i:i + 500]).execute()
            saved += len(mapped[i:i + 500])
        except Exception as e:
            raise HTTPException(500, f"Insert into {table} failed at row {i}: {e}")
    try:
        client.schema("commcalc").table("upload_log").insert(
            {"org_id": org_id, "file_type": report_key, "period": period or None,
             "filename": getattr(file, "filename", None), "rows_saved": saved}).execute()
    except Exception as e:
        print(f"WARN upload_log insert failed: {e}")
    return {"saved": saved, "report_key": report_key, "target_table": table, "period": period,
            "mapped": len(mapped), "new_categories": created, "template_rows": persisted}


@router.post("/carrier-comm-file/extract")
async def carrier_comm_file_extract(file: UploadFile = File(...), org_id: str = ORG_ID):
    """Extract a carrier commission/comp file into tabular rows for preview. PDF → pdfplumber table
    extraction (per page); Excel/CSV → pandas. Returns {sheets:[{name, rows:[[...]]}], note?}."""
    contents = await file.read()
    name = (getattr(file, "filename", "") or "").lower()
    try:
        if name.endswith(".pdf"):
            import pdfplumber
            sheets = []
            with pdfplumber.open(io.BytesIO(contents)) as pdf:
                for pi, page in enumerate(pdf.pages):
                    tables = page.extract_tables() or []
                    for ti, tbl in enumerate(tables):
                        rows = [[("" if c is None else str(c).strip()) for c in r]
                                for r in tbl if any(c not in (None, "") for c in r)]
                        if rows:
                            sheets.append({"name": f"Page {pi + 1}" + (f" · table {ti + 1}" if len(tables) > 1 else ""),
                                           "rows": rows})
            if not sheets:
                return {"sheets": [], "note": "No ruled tables detected — the PDF may be image-based or unruled. "
                                               "Export the statement to Excel/CSV and upload that instead."}
            return {"sheets": sheets}
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents), header=None, dtype=str).fillna("")
            return {"sheets": [{"name": "CSV", "rows": df.astype(str).values.tolist()}]}
        xls = pd.read_excel(io.BytesIO(contents), sheet_name=None, header=None, dtype=str)
        sheets = [{"name": str(n), "rows": d.fillna("").astype(str).values.tolist()} for n, d in xls.items() if len(d)]
        return {"sheets": sheets}
    except Exception as e:
        raise HTTPException(400, f"Could not read the file: {e}")


@router.get("/x-tender-recon")
def x_tender_recon(date: str = "", period: str = "", tolerance: float = 1.0, org_id: str = ORG_ID):
    """Reconcile the POS X-report tenders (pos_tender_summary) vs the daily closing sheet (daily_closing)
    per store, cash vs card. Pass date='YYYY-MM-DD' for one day, or period ('2026-06' / 'June 2026') for a
    month. Returns per-store variances + totals. Store keys are matched by exact string (POS store name vs
    closing address) — use Store Matching if they differ. Degrades to empty if migration 062 isn't applied."""
    require_org(org_id)
    client = sb()
    xq = client.schema('commcalc').table('pos_tender_summary').select('close_date,store,tender_class,amount').eq('org_id', org_id)
    dq = client.schema('commcalc').table('daily_closing').select(
        'close_date,store_code,store_name,store_address,store_cash,store_cc,epay_cash,epay_cc').eq('org_id', org_id)
    if date:
        xq = xq.eq('close_date', date); dq = dq.eq('close_date', date)
    elif period:
        if period[:4].isdigit() and '-' in period:
            y, m = int(period[:4]), int(period[5:7])
        else:
            pm = parse_period(period); y, m = pm['year'], pm['month']
        start = f"{y}-{m:02d}-01"
        end = f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01"
        xq = xq.gte('close_date', start).lt('close_date', end)
        dq = dq.gte('close_date', start).lt('close_date', end)
    try:
        xrows = xq.execute().data or []
    except Exception:
        return {"ready": False, "rows": [], "note": "Run migration 062_pos_tender_summary.sql + import an X report."}
    drows = dq.execute().data or []

    pos = {}
    for r in xrows:
        s = (r.get('store') or '').strip()
        if not s:
            continue
        p = pos.setdefault(s, {'cash': 0.0, 'card': 0.0, 'other': 0.0})
        cls = (r.get('tender_class') or 'other')
        p[cls if cls in p else 'other'] += safe_float(r.get('amount'))
    clo = {}
    for r in drows:
        s = (r.get('store_address') or r.get('store_name') or r.get('store_code') or '').strip()
        if not s:
            continue
        c = clo.setdefault(s, {'cash': 0.0, 'card': 0.0})
        c['cash'] += safe_float(r.get('store_cash')) + safe_float(r.get('epay_cash'))
        c['card'] += safe_float(r.get('store_cc')) + safe_float(r.get('epay_cc'))

    rows_out, t = [], {'pos_cash': 0.0, 'closing_cash': 0.0, 'pos_card': 0.0, 'closing_card': 0.0}
    for s in sorted(set(pos) | set(clo)):
        pc = pos.get(s, {'cash': 0, 'card': 0, 'other': 0})
        cc = clo.get(s, {'cash': 0, 'card': 0})
        cash_var = round(pc['cash'] - cc['cash'], 2)
        card_var = round(pc['card'] - cc['card'], 2)
        rows_out.append({
            'store': s,
            'pos_cash': round(pc['cash'], 2), 'closing_cash': round(cc['cash'], 2), 'cash_variance': cash_var,
            'pos_card': round(pc['card'], 2), 'closing_card': round(cc['card'], 2), 'card_variance': card_var,
            'pos_other': round(pc.get('other', 0), 2),
            'match': abs(cash_var) <= tolerance and abs(card_var) <= tolerance,
            'in_pos': s in pos, 'in_closing': s in clo,
        })
        t['pos_cash'] += pc['cash']; t['closing_cash'] += cc['cash']
        t['pos_card'] += pc['card']; t['closing_card'] += cc['card']
    return {"ready": True, "date": date, "period": period, "tolerance": tolerance,
            "rows": rows_out,
            "totals": {**{k: round(v, 2) for k, v in t.items()},
                       "cash_variance": round(t['pos_cash'] - t['closing_cash'], 2),
                       "card_variance": round(t['pos_card'] - t['closing_card'], 2),
                       "stores": len(rows_out), "mismatches": sum(1 for r in rows_out if not r['match'])}}


@router.get("/carrier-category-map")
def list_category_map(carrier_id: str = "", org_id: str = ORG_ID):
    require_org(org_id)
    q = sb().schema("commcalc").table("carrier_category_map").select("*").eq("org_id", org_id)
    if carrier_id:
        q = q.eq("carrier_id", carrier_id)
    return q.order("priority").execute().data or []


@router.post("/carrier-category-map")
def upsert_category_rule(body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    comp = (body.get("component") or "").strip().upper()
    if comp not in carrier_map.COMPONENTS:
        raise HTTPException(400, "component must be one of " + "|".join(carrier_map.COMPONENTS))
    raw = (body.get("raw_category") or "").strip()
    if not raw:
        raise HTTPException(400, "raw_category required")
    row = {"org_id": org_id, "carrier_id": body.get("carrier_id") or None, "raw_category": raw,
           "match_type": (body.get("match_type") or "exact").lower(), "component": comp,
           "subtype": (body.get("subtype") or "").strip() or None,
           "priority": int(body.get("priority") or 100),
           "is_active": body.get("is_active", True) is not False,
           "updated_at": _datetime.now(_timezone.utc).isoformat()}
    if body.get("id"):
        sb().schema("commcalc").table("carrier_category_map").update(row).eq("id", body["id"]).execute()
        return {"ok": True, "id": body["id"]}
    r = sb().schema("commcalc").table("carrier_category_map").upsert(
        row, on_conflict="org_id,carrier_id,raw_category,match_type").execute()
    return r.data[0] if r.data else row


@router.delete("/carrier-category-map/{rid}")
def delete_category_rule(rid: str, org_id: str = ORG_ID):
    require_org(org_id)
    sb().schema("commcalc").table("carrier_category_map").delete().eq("org_id", org_id).eq("id", rid).execute()
    return {"ok": True}


@router.get("/carrier-category-map/unmapped")
def unmapped_categories(period: str = "", carrier_id: str = "", org_id: str = ORG_ID):
    """Distinct raw comp categories (raw_comp_report) split into mapped vs NOT-yet-mapped, so the
    admin can map what's missing. Never silently drops a category from the canonical model."""
    require_org(org_id)
    client = sb()
    rules = carrier_map.load_rules(client, org_id, carrier_id or None)
    q = client.schema("commcalc").table("raw_comp_report").select("compensation_type,payment_amount,period").eq("org_id", org_id)
    if period:
        q = q.in_("period", _period_variants(period))
    rows = q.limit(200000).execute().data or []
    agg = {}
    for r in rows:
        cat = (r.get("compensation_type") or "").strip()
        if not cat:
            continue
        d = agg.setdefault(cat, {"category": cat, "amount": 0.0, "count": 0})
        d["amount"] += safe_float(r.get("payment_amount"))
        d["count"] += 1
    mapped, unmapped = [], []
    for cat, d in agg.items():
        d["amount"] = round(d["amount"], 2)
        m = carrier_map.match_rule(rules, cat)
        if m:
            d["component"], d["subtype"] = m.get("component"), m.get("subtype")
            mapped.append(d)
        else:
            unmapped.append(d)
    mapped.sort(key=lambda x: -x["amount"])
    unmapped.sort(key=lambda x: -x["amount"])
    return {"period": period, "mapped": mapped, "unmapped": unmapped,
            "unmapped_total": round(sum(x["amount"] for x in unmapped), 2)}


@router.get("/comp-by-component")
def comp_by_component(period: str = "", carrier_id: str = "", org_id: str = ORG_ID):
    """Apply the category map to raw_comp_report → $ per canonical component (the payoff)."""
    require_org(org_id)
    client = sb()
    rules = carrier_map.load_rules(client, org_id, carrier_id or None)
    q = client.schema("commcalc").table("raw_comp_report").select("compensation_type,payment_amount,period").eq("org_id", org_id)
    if period:
        q = q.in_("period", _period_variants(period))
    rows = q.limit(200000).execute().data or []
    out = {c: 0.0 for c in carrier_map.COMPONENTS}
    unmapped = 0.0
    for r in rows:
        amt = safe_float(r.get("payment_amount"))
        m = carrier_map.match_rule(rules, r.get("compensation_type"))
        if m and m.get("component") in out:
            out[m["component"]] += amt
        else:
            unmapped += amt
    comps = {k: round(v, 2) for k, v in out.items()}
    comps["UNMAPPED"] = round(unmapped, 2)
    comps["TOTAL"] = round(sum(out.values()) + unmapped, 2)
    return {"period": period, "components": comps}


@router.get("/carrier-category-options")
def carrier_category_options(period: str = "", org_id: str = ORG_ID):
    """Pick-don't-type option lists for the Carrier Mapping page (AGENT_CONTRACT RULE THREE).

    Returns the distinct RAW comp categories actually present in THIS org's comp data
    (raw_comp_report.compensation_type) — the only strings the classifier ever matches against
    (see comp_by_component / unmapped_categories) — plus the distinct subtypes already used in this
    org's mapping rules. Org-scoped. `period` is optional: omit it to get all-period categories,
    since a mapping rule applies to every period (the caller does exactly that so the full set is
    pickable); pass it to narrow the list to one month's real labels.
    """
    require_org(org_id)
    client = sb()
    q = (client.schema("commcalc").table("raw_comp_report")
         .select("compensation_type").eq("org_id", org_id))
    if period:
        q = q.in_("period", _period_variants(period))
    rows = q.limit(200000).execute().data or []
    cats = sorted(
        {(r.get("compensation_type") or "").strip() for r in rows
         if (r.get("compensation_type") or "").strip()},
        key=lambda s: s.lower())
    srows = (client.schema("commcalc").table("carrier_category_map")
             .select("subtype").eq("org_id", org_id).execute().data) or []
    subs = sorted(
        {(r.get("subtype") or "").strip() for r in srows
         if (r.get("subtype") or "").strip()},
        key=lambda s: s.lower())
    return {"categories": cats, "subtypes": subs}


# ── Unified connector model (SaaS framework Phase 2: registry + live status + run-now dispatch) ──
def _connector_status(client, org_id, cfg_table):
    """Live status (last run / next run / enabled / schedule) from the connector's *_sweep_config.
    The schedule fields are read best-effort in a second query so a config table without them
    never breaks the primary status."""
    if not cfg_table:
        return {}
    try:
        rows = (client.schema('commcalc').table(cfg_table)
                .select('enabled,last_run_at,last_status,last_detail,next_run_at')
                .eq('org_id', org_id).limit(1).execute().data) or []
        out = rows[0] if rows else {}
    except Exception:
        return {}
    try:
        sch = (client.schema('commcalc').table(cfg_table)
               .select('frequency,day_of_week,day_of_month,hour,timezone')
               .eq('org_id', org_id).limit(1).execute().data) or []
        if sch:
            out = {**out, **sch[0]}
    except Exception:
        pass
    return out


def _connector_creds(client, org_id, cfg_table):
    """Whether the connector's portal credentials are present — NEVER returns the password.
    Lets the registry show a cred/readiness state without exposing or relocating any secret
    (the actual move of creds under the connector model is a separate, human-reviewed step)."""
    blank = {'has_user': False, 'has_pass': False, 'user_hint': ''}
    if not cfg_table:
        return blank
    try:
        rows = (client.schema('commcalc').table(cfg_table)
                .select('portal_user,portal_pass').eq('org_id', org_id).limit(1).execute().data) or []
    except Exception:
        return blank
    if not rows:
        return blank
    u = (rows[0].get('portal_user') or '').strip()
    has_pass = bool((rows[0].get('portal_pass') or '').strip())  # presence only; value discarded
    hint = ''
    if u:
        hint = (u[:2] + '***' + ('@' + u.split('@', 1)[1] if '@' in u else '')) if len(u) > 2 else '***'
    return {'has_user': bool(u), 'has_pass': has_pass, 'user_hint': hint}


@router.get("/connectors")
def list_connectors(org_id: str = ORG_ID):
    """Every vendor portal + the reports it provides + live sweep status. The single registry."""
    require_org(org_id)
    client = sb()
    conns = (client.schema('commcalc').table('connector_instances').select('*')
             .eq('org_id', org_id).order('sort_order').execute().data) or []
    defs = (client.schema('commcalc').table('report_definitions').select('*')
            .eq('org_id', org_id).order('sort_order').execute().data) or []
    # last upload per report (upload_log.file_type == report_key for the period reports)
    last_up = {}
    try:
        logs = (client.schema('commcalc').table('upload_log').select('file_type,period,uploaded_at,rows_saved')
                .eq('org_id', org_id).order('uploaded_at', desc=True).limit(500).execute().data) or []
        for lg in logs:
            ft = lg.get('file_type')
            if ft and ft not in last_up:
                last_up[ft] = lg
    except Exception:
        pass
    by_conn = {}
    for d in defs:
        d['last_upload'] = last_up.get(d.get('report_key'))
        by_conn.setdefault(d.get('connector_id'), []).append(d)
    return [{**c, 'status': _connector_status(client, org_id, c.get('config_table')),
             'creds': _connector_creds(client, org_id, c.get('config_table')),
             'reports': by_conn.get(c['id'], [])} for c in conns]


@router.post("/connectors")
def create_connector(body: dict, org_id: str = ORG_ID):
    """Onboard a new vendor connector to the registry (no SQL). Upsert by vendor_name."""
    require_org(org_id)
    name = (body.get('vendor_name') or '').strip()
    if not name:
        raise HTTPException(400, "vendor_name required")
    row = {'org_id': org_id, 'vendor_name': name, 'label': body.get('label'),
           'sweep_kind': (body.get('sweep_kind') or 'manual').strip(), 'portal_url': body.get('portal_url'),
           'auth_type': body.get('auth_type') or 'form', 'twofa_method': body.get('twofa_method') or 'none',
           'twofa_status': body.get('twofa_status') or 'needs_setup',
           'automatable': body.get('automatable', True) is not False, 'enabled': True,
           'config_table': (body.get('config_table') or '').strip() or None,
           'account_id': (body.get('account_id') or '').strip() or None,        # e.g. Total Wireless retailer #
           'login_username': (body.get('login_username') or '').strip() or None,
           'sort_order': int(body.get('sort_order') or 100),
           'updated_at': _datetime.now(_timezone.utc).isoformat()}
    r = sb().schema('commcalc').table('connector_instances').upsert(row, on_conflict='org_id,vendor_name').execute()
    return r.data[0] if r.data else row


@router.patch("/connectors/{cid}")
def update_connector(cid: str, body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    allow = ('label', 'enabled', 'automatable', 'twofa_method', 'twofa_status', 'portal_url', 'sort_order', 'notes',
             'account_id', 'login_username')
    row = {k: body[k] for k in allow if k in body}
    row['updated_at'] = _datetime.now(_timezone.utc).isoformat()
    sb().schema('commcalc').table('connector_instances').update(row).eq('org_id', org_id).eq('id', cid).execute()
    return {"ok": True}


@router.post("/report-definitions")
def create_report_def(body: dict, org_id: str = ORG_ID):
    """Add a report to a connector in the registry (no SQL). Upsert by report_key."""
    require_org(org_id)
    rk = (body.get('report_key') or '').strip()
    if not rk:
        raise HTTPException(400, "report_key required")
    row = {'org_id': org_id, 'connector_id': body.get('connector_id'), 'report_key': rk,
           'label': body.get('label'), 'source_name': body.get('source_name'), 'report_id': body.get('report_id'),
           'period_mode': body.get('period_mode') or 'current', 'target_table': body.get('target_table'),
           'upload_endpoint': body.get('upload_endpoint'), 'source_url': body.get('source_url'),
           'auto': bool(body.get('auto')), 'refresh_months': int(body.get('refresh_months') or 1),
           'sort_order': int(body.get('sort_order') or 100),
           'updated_at': _datetime.now(_timezone.utc).isoformat()}
    r = sb().schema('commcalc').table('report_definitions').upsert(row, on_conflict='org_id,report_key').execute()
    return r.data[0] if r.data else row


@router.patch("/report-definitions/{rid}")
def update_report_def(rid: str, body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    allow = ('label', 'source_name', 'report_id', 'period_mode', 'target_table', 'upload_endpoint',
             'source_url', 'auto', 'refresh_months', 'sort_order', 'note')
    row = {k: body[k] for k in allow if k in body}
    row['updated_at'] = _datetime.now(_timezone.utc).isoformat()
    sb().schema('commcalc').table('report_definitions').update(row).eq('org_id', org_id).eq('id', rid).execute()
    return {"ok": True}


# ── Self-serve custom import sheets (migration 099) ─────────────────────────────────────────────
# A user adds a NEW auto-import sheet (e.g. B2B "Sales Trend") from the UI: create a custom type here,
# then add a filename pattern on Email/FTP Imports pointing at its report_key. The sweep captures every
# row as JSONB into raw_custom_import — no code, no per-report table. See _ingest_custom_report above.
def _slugify_report_key(s):
    out = "".join(ch if ch.isalnum() else "_" for ch in (s or "").lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "custom_report"


@router.get("/custom-import-types")
def list_custom_import_types(org_id: str = ORG_ID):
    """Every user-defined custom sheet + its captured-row count, for the Email/FTP Imports dropdown."""
    require_org(org_id)
    client = sb()
    defs = (client.schema('commcalc').table('report_definitions').select('*')
            .eq('org_id', org_id).execute().data) or []
    custom = [d for d in defs if d.get('upload_endpoint') == 'custom' or d.get('target_table') == CUSTOM_IMPORT_TABLE]
    out = []
    for d in custom:
        rk = d.get('report_key')
        try:
            cnt = (client.schema('commcalc').table(CUSTOM_IMPORT_TABLE).select('id', count='exact')
                   .eq('org_id', org_id).eq('report_key', rk).limit(1).execute()).count or 0
        except Exception:
            cnt = 0
        out.append({'report_key': rk, 'label': d.get('label') or rk,
                    'period_mode': d.get('period_mode'), 'note': d.get('note'), 'rows': cnt})
    return sorted(out, key=lambda r: (r['label'] or '').lower())


@router.post("/custom-import-types")
def create_custom_import_type(body: dict, org_id: str = ORG_ID):
    """Register a self-serve custom sheet. body: {label, report_key?, period_mode?, note?}. Auto-slugs a
    report_key from the label, rejects a collision with a built-in type, and marks it as a generic JSONB
    capture (target_table=raw_custom_import). Returns the report_key to use in a filename pattern.
    Idempotent (upsert by report_key)."""
    require_org(org_id)
    label = (body.get('label') or '').strip()
    if not label:
        raise HTTPException(400, "label required")
    rk = _slugify_report_key((body.get('report_key') or '').strip() or label)
    if rk in BUILTIN_UPLOAD_TYPES:
        raise HTTPException(400, f"'{rk}' is a built-in report type — pick a different name")
    row = {'org_id': org_id, 'report_key': rk, 'label': label,
           'period_mode': (body.get('period_mode') or 'current'),
           'target_table': CUSTOM_IMPORT_TABLE, 'upload_endpoint': 'custom',
           'auto': True, 'note': (body.get('note') or None),
           'updated_at': _datetime.now(_timezone.utc).isoformat()}
    r = sb().schema('commcalc').table('report_definitions').upsert(row, on_conflict='org_id,report_key').execute()
    return (r.data[0] if r.data else row)


@router.delete("/custom-import-types/{report_key}")
def delete_custom_import_type(report_key: str, purge: bool = False, org_id: str = ORG_ID):
    """Remove a custom sheet definition. purge=true also deletes its captured rows (default keeps them)."""
    require_org(org_id)
    client = sb()
    if not _custom_report_def(client, org_id, report_key):
        raise HTTPException(404, f"no custom sheet '{report_key}'")
    client.schema('commcalc').table('report_definitions').delete().eq('org_id', org_id).eq('report_key', report_key).execute()
    purged = 0
    if purge:
        try:
            res = (client.schema('commcalc').table(CUSTOM_IMPORT_TABLE).delete()
                   .eq('org_id', org_id).eq('report_key', report_key).execute())
            purged = len(res.data or [])
        except Exception:
            pass
    return {"ok": True, "report_key": report_key, "purged_rows": purged}


@router.get("/custom-import/{report_key}")
def view_custom_import(report_key: str, limit: int = 200, period: str = "", org_id: str = ORG_ID):
    """Viewer: recent captured rows for a custom sheet + the union of columns seen, so it isn't a black
    box. Flattens each row's JSONB `data` for display."""
    require_org(org_id)
    client = sb()
    q = (client.schema('commcalc').table(CUSTOM_IMPORT_TABLE)
         .select('period,source_filename,row_index,data,created_at')
         .eq('org_id', org_id).eq('report_key', report_key))
    if period:
        q = q.in_('period', _pvariants(period))
    rows = (q.order('created_at', desc=True).order('row_index').limit(min(limit, 2000)).execute().data) or []
    cols, seen = [], set()
    for r in rows:
        for k in (r.get('data') or {}).keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)
    flat = [{**{c: (r.get('data') or {}).get(c, '') for c in cols},
             '_period': r.get('period'), '_file': r.get('source_filename')} for r in rows]
    periods = sorted({r.get('period') for r in rows if r.get('period')})
    return {"report_key": report_key, "columns": cols, "rows": flat, "count": len(flat), "periods": periods}


# ── Connector sweep registry (B-phase2 de-hardcode) ────────────────────────────────────────────
# Maps a connector's sweep_kind → its puller so NEITHER dispatch site below hard-codes the vendor
# list. Built-in pullers resolve via this module's globals (defined later in the file, so order
# doesn't matter — they're looked up at call time); the closing sweep is wired lazily to dodge a
# circular import; and any new POS / payment-processor can self-register from its own module via
# register_sweep(kind, fn) with NO edit here. Behavior-identical to the old inline dicts for the
# existing kinds (vip/dlar/epay/b2b/google_closing). SaaS goal: adding a provider is data + a
# puller, never dispatcher surgery.
_SWEEP_BUILTINS = {'vip': '_do_vip_sweep', 'dlar': '_do_dlar_sweep',
                   'epay': '_do_epay_sweep', 'b2b': '_do_b2b_sweep'}
_SWEEP_EXTERNAL: dict = {}  # populated by register_sweep() from other modules / new providers


def register_sweep(kind: str, fn):
    """Register a connector puller under its sweep_kind so it dispatches without editing router.py.
    Idempotent (last registration wins). Returns fn so it can double as a decorator."""
    k = (kind or '').strip()
    if k and callable(fn):
        _SWEEP_EXTERNAL[k] = fn
    return fn


def _sweep_registry() -> dict:
    """The full sweep_kind → puller table. Resolves built-ins from module globals (works regardless
    of definition order), lazily wires the closing sweep, then overlays externally-registered
    providers. Cheap to rebuild per call (a few dict lookups)."""
    reg = {}
    g = globals()
    for kind, fname in _SWEEP_BUILTINS.items():
        fn = g.get(fname)
        if callable(fn):
            reg[kind] = fn
    try:
        from app.modules.closing.router import _do_closing_sweep
        reg['google_closing'] = _do_closing_sweep
    except Exception:
        pass
    reg.update(_SWEEP_EXTERNAL)
    return reg


@router.post("/connectors/{cid}/run-now")
def connector_run_now(cid: str, background_tasks: BackgroundTasks, org_id: str = ORG_ID):
    """Generic run-now: dispatch to the registered sweep by sweep_kind."""
    require_org(org_id)
    rows = sb().schema('commcalc').table('connector_instances').select('*').eq('org_id', org_id).eq('id', cid).execute().data or []
    if not rows:
        raise HTTPException(404, "connector not found")
    kind = (rows[0].get('sweep_kind') or '').strip()
    dispatch = _sweep_registry()
    if kind in dispatch:
        background_tasks.add_task(dispatch[kind], org_id)
        return {"status": "started", "kind": kind}
    raise HTTPException(400, f"'{rows[0].get('vendor_name')}' is manual-only — upload it on the Upload Wizard.")


@router.patch("/connectors/{cid}/schedule")
def update_connector_schedule(cid: str, body: dict, org_id: str = ORG_ID):
    """Set a connector's sweep schedule (frequency/day/hour/timezone/enabled) from the registry —
    written to its *_sweep_config and re-deriving next_run_at via the shared scheduler. The registry
    becomes the single place to schedule, instead of hunting for each vendor's own sweep page."""
    require_org(org_id)
    client = sb()
    rows = (client.schema('commcalc').table('connector_instances').select('*')
            .eq('org_id', org_id).eq('id', cid).execute().data) or []
    if not rows:
        raise HTTPException(404, "connector not found")
    conn = rows[0]
    tbl = (conn.get('config_table') or '').strip()
    if not tbl:
        raise HTTPException(400, f"'{conn.get('vendor_name')}' is manual-only — nothing to schedule.")
    try:
        cur = (client.schema('commcalc').table(tbl)
               .select('frequency,day_of_week,day_of_month,hour,timezone,enabled')
               .eq('org_id', org_id).limit(1).execute().data) or []
    except Exception as e:
        raise HTTPException(400, f"{tbl} has no schedule columns: {str(e)[:120]}")
    if not cur:
        raise HTTPException(400, f"Set up {conn.get('vendor_name')}'s credentials first — no config row yet.")
    merged = {**cur[0]}
    upd = {}
    for k in ('frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone', 'enabled'):
        if k in body and body[k] is not None:
            upd[k] = body[k]
            merged[k] = body[k]
    upd['next_run_at'] = _vip_next_run(merged.get('frequency') or 'daily', merged.get('day_of_week'),
                                       merged.get('day_of_month'), merged.get('hour'), merged.get('timezone'))
    try:
        client.schema('commcalc').table(tbl).update({**upd, 'updated_at': _datetime.now(_timezone.utc).isoformat()}) \
            .eq('org_id', org_id).execute()
    except Exception:
        client.schema('commcalc').table(tbl).update(upd).eq('org_id', org_id).execute()
    return {"ok": True, "next_run_at": upd['next_run_at']}


@router.post("/connectors/run-due")
def connectors_run_due(background_tasks: BackgroundTasks, x_notify_secret: str = Header(default=""), org_id: str = ORG_ID):
    """ONE pg_cron entrypoint that fans out to every connector whose schedule is due — replacing the
    per-vendor /{vendor}/sweep/run-due crons. Reads each enabled connector's *_sweep_config
    (enabled + next_run_at), dispatches the due ones by sweep_kind, and advances next_run_at.
    Additive: the per-vendor run-dues still work; point a single cron here and disable the others to
    avoid double-runs. Guarded by NOTIFY_RUN_SECRET (reused — no new env var)."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _datetime.now(_timezone.utc).isoformat()
    dispatch = _sweep_registry()
    conns = (client.schema('commcalc').table('connector_instances').select('*')
             .eq('enabled', True).execute().data) or []
    triggered, checked = [], 0
    for c in conns:
        kind = (c.get('sweep_kind') or '').strip()
        tbl = (c.get('config_table') or '').strip()
        if kind not in dispatch or not tbl:
            continue
        oid = c.get('org_id') or org_id
        try:
            cfg = (client.schema('commcalc').table(tbl)
                   .select('enabled,next_run_at,frequency,day_of_week,day_of_month,hour,timezone')
                   .eq('org_id', oid).limit(1).execute().data) or []
        except Exception:
            continue
        if not cfg:
            continue
        checked += 1
        cf = cfg[0]
        if not cf.get('enabled'):
            continue
        nra = cf.get('next_run_at')
        if nra and nra > now_iso:
            continue
        nxt = _vip_next_run(cf.get('frequency') or 'daily', cf.get('day_of_week'),
                            cf.get('day_of_month'), cf.get('hour'), cf.get('timezone'))
        try:
            client.schema('commcalc').table(tbl).update({'next_run_at': nxt}).eq('org_id', oid).execute()
        except Exception:
            pass
        background_tasks.add_task(dispatch[kind], oid)
        triggered.append(c.get('vendor_name'))
    return {"triggered": triggered, "checked": checked}


# ── Chargeback review bucket (VIP file + fraud) → assign to the rep → employee chargeback ────
def _cb_now():
    return _datetime.now(_timezone.utc).isoformat()


@router.get("/chargeback-review")
def chargeback_review_list(status: str = None, source: str = None, store: str = None, org_id: str = ORG_ID):
    """The chargeback bucket. Each row is a candidate to ASSIGN to the rep who did the sale.
    Adds a suggested_rep (matched from raw_sales by serial/phone) to the still-open rows."""
    require_org(org_id)
    client = sb()
    q = client.schema('commcalc').table('chargeback_review').select('*').eq('org_id', org_id)
    if status:
        q = q.eq('status', status)
    if source:
        q = q.eq('source', source)
    rows = q.order('created_at', desc=True).limit(5000).execute().data or []
    if store:
        rows = [r for r in rows if r.get('store_code') == store]
    # suggested rep: match esn/imei → raw_sales.serial_1, phone → raw_sales.mdn (best-effort)
    by_serial, by_phone = {}, {}
    if any(not r.get('assigned_rep') and not r.get('suggested_rep') for r in rows):
        try:
            sales = (client.schema('commcalc').table('raw_sales')
                     .select('serial_1,mdn,salesperson').eq('org_id', org_id).limit(100000).execute().data) or []
            for s in sales:
                sp = (s.get('salesperson') or '').strip()
                if not sp:
                    continue
                ser = re.sub(r'\W', '', str(s.get('serial_1') or '')).upper()
                ph = re.sub(r'\D', '', str(s.get('mdn') or ''))
                if ser:
                    by_serial.setdefault(ser, sp)
                if ph:
                    by_phone.setdefault(ph[-10:], sp)
        except Exception:
            pass
    for r in rows:
        if r.get('assigned_rep') or r.get('suggested_rep'):
            continue
        sug = None
        for key in (r.get('esn'), r.get('imei')):
            k = re.sub(r'\W', '', str(key or '')).upper()
            if k and k in by_serial:
                sug = by_serial[k]
                break
        if not sug:
            ph = re.sub(r'\D', '', str(r.get('phone_number') or ''))[-10:]
            if ph and ph in by_phone:
                sug = by_phone[ph]
        r['suggested_rep'] = sug
    counts = {}
    for r in rows:
        st = r.get('status') or 'open'
        counts[st] = counts.get(st, 0) + 1
    return {"rows": rows, "counts": counts, "total": len(rows)}


@router.post("/chargeback-review")
def create_chargeback_review(payload: dict, org_id: str = ORG_ID):
    """Manually add a candidate to the chargeback bucket (e.g. Sales-Analyzer early-churn → charge
    the rep the rebate lost). Upsert by dedupe_key so re-flagging the same item is idempotent."""
    require_org(org_id)
    detail = (payload.get('detail') or 'Chargeback')
    dk = payload.get('dedupe_key') or f"manual:{detail[:50]}"
    row = {'org_id': org_id, 'source': (payload.get('source') or 'manual').strip(),
           'severity': payload.get('severity') or 'warning', 'needs_review': bool(payload.get('needs_review')),
           'store_code': payload.get('store_code'),
           'store_address': payload.get('store_address') or payload.get('store'),
           'period': payload.get('period') or '', 'occurred_date': payload.get('occurred_date'),
           'customer_name': payload.get('customer_name'), 'email': payload.get('email'),
           'phone_number': payload.get('phone_number'), 'esn': payload.get('esn'),
           'imei': payload.get('imei') or payload.get('esn'),
           'amount': abs(safe_float(payload.get('amount'))), 'detail': detail[:300],
           'suggested_rep': (payload.get('suggested_rep') or '').strip() or None, 'dedupe_key': dk}
    sb().schema('commcalc').table('chargeback_review').upsert(row, on_conflict='org_id,dedupe_key').execute()
    return {"ok": True, "dedupe_key": dk}


@router.post("/chargeback-review/{cb_id}/assign")
def chargeback_review_assign(cb_id: str, payload: dict, org_id: str = ORG_ID):
    """Assign a chargeback to the rep → write the employee chargeback_items row for that period.
    For a fraud_dupe item this also records a 'disapproved' review (mgmt confirmed it's bad)."""
    require_org(org_id)
    client = sb()
    rows = client.schema('commcalc').table('chargeback_review').select('*').eq('org_id', org_id).eq('id', cb_id).execute().data or []
    if not rows:
        raise HTTPException(404, "chargeback not found")
    cb = rows[0]
    rep = (payload.get('rep') or '').strip()
    if not rep:
        raise HTTPException(400, "rep required")
    reason = (payload.get('reason') or '').strip()
    amount = abs(safe_float(payload.get('amount'))) if payload.get('amount') is not None else abs(safe_float(cb.get('amount')))
    period = cb.get('period') or ''
    if not period and cb.get('occurred_date'):
        try:
            from dateutil import parser as _dp
            period = _dp.parse(str(cb['occurred_date'])).strftime('%B %Y')
        except Exception:
            period = ''
    ref = f"cbr-{cb_id}"
    desc = (cb.get('detail') or 'Chargeback') + (f" — {reason}" if reason else "")
    by = (payload.get('assigned_by') or 'admin')
    item = {
        'org_id': org_id, 'period': period or 'Unassigned', 'epay_salesperson': rep,
        'store': cb.get('store_address') or cb.get('store_code') or '',
        'source': 'chargeback_review', 'source_ref': ref, 'description': desc[:300],
        'amount': amount, 'mdn': cb.get('phone_number') or '',
        'imei': cb.get('imei') or cb.get('esn') or '',
        'deduct': True if payload.get('deduct') is None else bool(payload.get('deduct')),
        'decided_at': _cb_now(),
    }
    client.schema('commcalc').table('chargeback_items').delete().eq('org_id', org_id).eq('source', 'chargeback_review').eq('source_ref', ref).execute()
    client.schema('commcalc').table('chargeback_items').insert(item).execute()
    upd = {'status': 'assigned', 'assigned_rep': rep, 'assigned_by': by, 'assigned_at': _cb_now(),
           'reason': reason or None, 'amount': amount, 'chargeback_item_ref': ref, 'updated_at': _cb_now()}
    if cb.get('needs_review'):
        upd.update({'review': 'disapproved', 'reviewed_by': by, 'reviewed_at': _cb_now()})
    client.schema('commcalc').table('chargeback_review').update(upd).eq('id', cb_id).execute()
    return {"ok": True, "period": period, "rep": rep, "amount": amount}


@router.post("/chargeback-review/{cb_id}/dismiss")
def chargeback_review_dismiss(cb_id: str, payload: dict = {}, org_id: str = ORG_ID):
    """Dismiss a candidate (no chargeback). For a fraud review item this records 'approved' (legit)."""
    require_org(org_id)
    client = sb()
    ref = f"cbr-{cb_id}"
    client.schema('commcalc').table('chargeback_items').delete().eq('org_id', org_id).eq('source', 'chargeback_review').eq('source_ref', ref).execute()
    cur = client.schema('commcalc').table('chargeback_review').select('needs_review').eq('org_id', org_id).eq('id', cb_id).execute().data or []
    body = {'status': 'dismissed', 'assigned_rep': None, 'assigned_at': None,
            'chargeback_item_ref': None, 'reason': (payload or {}).get('reason') or None, 'updated_at': _cb_now()}
    if cur and cur[0].get('needs_review'):
        body.update({'review': 'approved', 'reviewed_by': (payload or {}).get('reviewed_by') or 'admin', 'reviewed_at': _cb_now()})
    client.schema('commcalc').table('chargeback_review').update(body).eq('id', cb_id).execute()
    return {"ok": True}


@router.post("/chargeback-review/{cb_id}/reopen")
def chargeback_review_reopen(cb_id: str, org_id: str = ORG_ID):
    require_org(org_id)
    client = sb()
    ref = f"cbr-{cb_id}"
    client.schema('commcalc').table('chargeback_items').delete().eq('org_id', org_id).eq('source', 'chargeback_review').eq('source_ref', ref).execute()
    client.schema('commcalc').table('chargeback_review').update({
        'status': 'open', 'assigned_rep': None, 'assigned_at': None, 'review': None,
        'chargeback_item_ref': None, 'updated_at': _cb_now()}).eq('id', cb_id).execute()
    return {"ok": True}


# ── Fraud detectors → chargeback_review (fake/reused email · duplicate id/name) ─────────────
def _is_fake_email(em: str) -> bool:
    em = (em or "").strip().lower()
    if not em:
        return False
    if "@" not in em or "." not in em.split("@")[-1]:
        return True
    local = em.split("@", 1)[0]
    if len(local) <= 1:
        return True
    BAD = ("test@", "asdf", "qwerty", "noemail", "no@", "none@", "fake", "example.com",
           "mailinator", "tempmail", "xxx@", "aaa@", "abc@", "123@", "n/a")
    return any(b in em for b in BAD)


def _fraud_row(org_id, r, source, severity, detail, dedupe_key, needs_review=False):
    return {
        "org_id": org_id, "source": source, "severity": severity, "needs_review": needs_review,
        "store_address": r.get("store"), "period": r.get("period") or "",
        "occurred_date": str(r.get("trans_date") or "")[:10],
        "customer_name": r.get("customer"), "email": r.get("email"), "customer_no": r.get("customer_no"),
        "phone_number": r.get("mdn"), "esn": r.get("serial_1"), "imei": r.get("serial_1"),
        "amount": 0, "detail": detail,
        "suggested_rep": (r.get("salesperson") or "").strip() or None,
        "dedupe_key": dedupe_key,
        "raw": {"trans_id": r.get("trans_id"), "contract_type": r.get("contract_type")},
    }


def _detect_fraud(client, org_id, period=None):
    """Scan raw_sales activations for (a) fake / reused-across-customers email and (b) the same
    customer id/name on multiple activations → stage candidates into chargeback_review. Each
    candidate carries the sale's salesperson as suggested_rep. Re-runs preserve assignment/review."""
    from collections import defaultdict
    q = (client.schema("commcalc").table("raw_sales").select(
        "trans_id,trans_date,period,store,salesperson,customer,email,customer_no,mdn,serial_1,contract_type,voided")
        .eq("org_id", org_id))
    if period:
        q = q.in_("period", _pvariants(period))
    rows = q.limit(200000).execute().data or []
    acts = []
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        mdn = re.sub(r"\D", "", str(r.get("mdn") or ""))
        if not mdn and not (r.get("contract_type") or "").strip():
            continue  # not an activation line
        if not (r.get("email") or r.get("customer_no") or r.get("customer")):
            continue  # no customer identity captured yet (pre-036 rows)
        acts.append(r)

    email_custs = defaultdict(set)
    id_acts = defaultdict(list)
    for r in acts:
        em = (r.get("email") or "").strip().lower()
        cust = (r.get("customer_no") or "").strip() or (r.get("customer") or "").strip().lower()
        if em:
            email_custs[em].add(cust or re.sub(r"\D", "", str(r.get("mdn") or "")))
        if cust:
            id_acts[cust].append(r)

    cands = []
    for r in acts:
        em = (r.get("email") or "").strip().lower()
        if em:
            reused = len(email_custs.get(em, set())) > 1
            fake = _is_fake_email(em)
            if reused or fake:
                why = "reused across customers" if reused else "fake/invalid"
                cands.append(_fraud_row(org_id, r, "fraud_email", "critical",
                                        f"Email {why}: {em}", f"fe:{r.get('trans_id')}"))
        cust = (r.get("customer_no") or "").strip() or (r.get("customer") or "").strip().lower()
        grp = id_acts.get(cust, [])
        phones = {re.sub(r"\D", "", str(x.get("mdn") or "")) for x in grp if x.get("mdn")}
        if cust and len(grp) > 1 and len(phones) > 1:
            cands.append(_fraud_row(org_id, r, "fraud_dupe", "warning",
                                    f"Same customer on {len(grp)} activations ({r.get('customer') or r.get('customer_no')})",
                                    f"fd:{r.get('trans_id')}", needs_review=True))
    for i in range(0, len(cands), 500):
        client.schema("commcalc").table("chargeback_review").upsert(
            cands[i:i + 500], on_conflict="org_id,dedupe_key").execute()
    return {"email_flags": sum(1 for c in cands if c["source"] == "fraud_email"),
            "dupe_flags": sum(1 for c in cands if c["source"] == "fraud_dupe"),
            "scanned": len(acts)}


@router.post("/chargeback-review/scan-fraud")
def chargeback_scan_fraud(period: str = None, org_id: str = ORG_ID):
    """Run the fraud detectors over raw_sales (optionally one period) → stage into the bucket."""
    require_org(org_id)
    try:
        res = _detect_fraud(sb(), org_id, period)
    except Exception as e:
        raise HTTPException(500, f"fraud scan failed: {e}")
    return {"ok": True, **res}


# ── Item mapping + "accessory sold over $X" → chargeback (migration 041) ─────────────────────
# item_mapping maps each sales item (SKU, else description) to a type (accessory|phone|other|
# unclassified) and a phone model (the "SU sheet"). It self-maintains: unseen items auto-add as a
# guessed stub for the user to correct. The accessory-flags report finds accessory lines priced
# above the user-set threshold and pushes them to the chargeback bucket, assigned to the seller.
ACC_HINTS = ("ACCESS", "CASE", "SCREEN", "PROTECT", "CHARGER", "CABLE", "EARBUD", "HEADPHONE",
             "HEADSET", "MOUNT", "HOLDER", "POPSOCKET", "GLASS", "TEMPERED", "SPEAKER", "BATTERY",
             "POWER BANK", "ADAPTER", "STYLUS", "GRIP", "ONDIGO", "LIQUID", "WARRANTY")
PHONE_HINTS = ("IPHONE", "GALAXY", "HANDSET", "SMARTPHONE", "TABLET", "ANDROID", "MOTO",
               "MOTOROLA", "SAMSUNG", "APPLE", "PIXEL", " - XP")
# Promo / equipment-rebate credit lines (e.g. "MOTO G STYLUS 5G - 2023 - 2024 Q2 Promo New Act
# Boost 5G - $269.99"): a reimbursement the carrier owes when a phone sells on a promo, NOT a
# saleable SKU. Classified 'rebate' so it's kept OUT of accessory $ and phone-unit counts (and out
# of the forecast). The expected $ itself is a hotsheet/payables concern, not item_mapping.
REBATE_HINTS = ("PROMO", "REBATE", "REIMB", "NEW ACT", "SPIFF", "CLAWBACK", "CLAW BACK")
# Strong phone brand/family tokens — these WIN over a stray accessory word embedded in a MODEL name
# (e.g. "MOTO G STYLUS" contains the accessory word STYLUS but is a handset, not a stylus pen).
PHONE_STRONG = ("IPHONE", "GALAXY", "PIXEL", "MOTOROLA", "MOTO ", "SAMSUNG", "APPLE",
                "SMARTPHONE", "HANDSET", " - XP")


def _item_key(sku, desc):
    s = str(sku or "").strip()
    if s and s.lower() not in ("nan", "none", "0", "0.0"):
        return s.upper()[:200]
    return str(desc or "").strip().upper()[:200]


def _guess_item_type(department, category, desc):
    """Best-effort first-sight classification from raw_sales Department / Category / description.
    Order matters: a promo/rebate CREDIT line is neither a phone nor an accessory (→ 'rebate'); and a
    phone whose MODEL name embeds an accessory word ('Moto G STYLUS') must not be mistaken for one."""
    blob = " ".join(str(x or "") for x in (department, category, desc)).upper()
    if any(h in blob for h in REBATE_HINTS):
        return "rebate"
    strong_phone = any(h in blob for h in PHONE_STRONG)
    acc = any(h in blob for h in ACC_HINTS)
    if acc and not strong_phone:
        return "accessory"
    if strong_phone or any(h in blob for h in PHONE_HINTS):
        return "phone"
    if acc:
        return "accessory"
    return "unclassified"


def _flag_rules(client, org_id):
    try:
        rows = client.schema("commcalc").table("flag_rules").select("*").eq("org_id", org_id).eq("id", 1).limit(1).execute().data or []
        if rows:
            return rows[0]
    except Exception:
        pass
    return {"accessory_threshold": 35, "accessory_chargeback_amount": 0, "accessory_min_threshold": 0}


def _accessory_config(client, org_id):
    """Configurable accessory classification, resolved PER-ORG (mig 208 commcalc.accessory_config, keyed on
    org_id — REPLACES the flag_rules singleton, which could physically hold only ONE org's config because
    of its id=1 PK + CHECK(id=1), so _accessory_config(<non-house org>) fell through to the house default
    'Ondigo' and a tenant's real accessory categories never matched — the luxelink Sales-Report $0 bug).

    Resolution (empty at every step → the historical default department 'Ondigo', i.e. byte-identical Boost):
      1. commcalc.accessory_config for this org — the per-tenant source (admin-edited via Sales Report →
         Accessory settings → put_accessory_config).
      2. else the legacy flag_rules singleton — backward-compat for the house/Boost org BEFORE mig 208 runs
         (keeps Boost byte-identical during the transition; after 208 the house is backfilled into #1).
      3. PLUS any commcalc.gp_category_map department mapped to category 'accessory' (REUSE of the mig-069
         GP map so the Sales Report and the GP report agree on accessory DEPARTMENTS; additive + empty-safe,
         the house GP map is empty → no effect).
    Returns normalized sets + the raw lists."""
    depts, cats, kws, acima = [], [], [], []
    got = False
    try:
        rows = (client.schema("commcalc").table("accessory_config")
                .select("departments,categories,product_keywords,acima_tenders")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            got = True
            depts = [d for d in (rows[0].get("departments") or []) if d]
            cats = [c for c in (rows[0].get("categories") or []) if c]
            kws = [k for k in (rows[0].get("product_keywords") or []) if k]
            acima = [t for t in (rows[0].get("acima_tenders") or []) if t]
    except Exception:
        got = False
    if not got:
        # Pre-mig-208 fallback: the legacy flag_rules singleton (serves only whichever org owns the single
        # row — the house). Preserves the exact pre-208 result so nothing regresses until 208 is applied.
        try:
            rows = (client.schema("commcalc").table("flag_rules")
                    .select("accessory_departments,accessory_categories,accessory_product_keywords,acima_tenders")
                    .eq("org_id", org_id).eq("id", 1).limit(1).execute().data) or []
            if rows:
                depts = [d for d in (rows[0].get("accessory_departments") or []) if d]
                cats = [c for c in (rows[0].get("accessory_categories") or []) if c]
                kws = [k for k in (rows[0].get("accessory_product_keywords") or []) if k]
                acima = [t for t in (rows[0].get("acima_tenders") or []) if t]
        except Exception:
            pass
    # REUSE gp_category_map (mig 069): a department the tenant mapped to 'accessory' there is ALSO an
    # accessory department here. Empty-safe (the house map is empty → byte-identical); never raises.
    try:
        gp = (client.schema("commcalc").table("gp_category_map")
              .select("department,category").eq("org_id", org_id)
              .eq("category", "accessory").limit(1000).execute().data) or []
        have = {d.strip().lower() for d in depts}
        for r in gp:
            d = str(r.get("department") or "").strip()
            if d and d.lower() not in have:
                depts.append(d)
                have.add(d.lower())
    except Exception:
        pass
    if not depts and not cats and not kws:
        depts = ["Ondigo"]
    # BOX departments (mig 218; per-org, admin-editable) — which POS Departments count as a "box" (device
    # unit) for the Sales-Report box count, Daily-Targets conversion, and Productivity/Stack-Ranking/Review
    # boxes. Fetched in its OWN defensive query so a missing column (pre-218) can NEVER disturb the accessory
    # resolution above — it falls back to the code default _BOX_DEPTS (the Boost XP labels), keeping the
    # house/Boost box numbers BYTE-IDENTICAL. Matched EXACTLY against the raw dept string (same as the
    # original constant), so config-driven for a tenant without changing case semantics.
    box_depts = []
    try:
        brows = (client.schema("commcalc").table("accessory_config")
                 .select("box_departments").eq("org_id", org_id).limit(1).execute().data) or []
        if brows:
            box_depts = [b for b in (brows[0].get("box_departments") or []) if b]
    except Exception:
        box_depts = []
    if not box_depts:
        box_depts = list(_BOX_DEPTS)
    # Device SET-UP FEE keywords (mig 217 — Package A field, surfaced here so the shared Classification-
    # settings UI can edit it; CONSUMPTION of it — _is_setup_fee / accessory-target folding — lives in
    # Package A). Fetched in its OWN defensive query; missing column/empty → the code default
    # ['Device Setup Charge']. CROSS-PACKAGE OVERLAP: pkg A also resolves this identically (clean merge).
    setup_kws = []
    try:
        srows = (client.schema("commcalc").table("accessory_config")
                 .select("setup_fee_keywords").eq("org_id", org_id).limit(1).execute().data) or []
        if srows:
            setup_kws = [k for k in (srows[0].get("setup_fee_keywords") or []) if k]
    except Exception:
        setup_kws = []
    if not setup_kws:
        setup_kws = ["Device Setup Charge"]
    return {"departments": {d.strip().lower() for d in depts},
            "categories": {c.strip().lower() for c in cats},
            "products": {k.strip().lower() for k in kws},
            "departments_list": depts, "categories_list": cats, "products_list": kws,
            "acima_tenders_list": acima,
            "box_departments": {b.strip() for b in box_depts},
            "box_departments_list": box_depts,
            "setup_fee_products": {k.strip().lower() for k in setup_kws},
            "setup_fee_keywords_list": setup_kws}


def _is_accessory(dept, category, product, acfg):
    """A sale line is an accessory if its department OR category is in the configured lists, OR its
    product description contains a configured keyword (for POS feeds that carry no dept/category)."""
    d = (dept or "").strip().lower()
    c = (category or "").strip().lower()
    if d in acfg["departments"]:
        return True
    if c and c in acfg["categories"]:
        return True
    if acfg["products"]:
        p = (product or "").strip().lower()
        if p and any(k in p for k in acfg["products"]):
            return True
    return False


def _is_setup_fee(product, acfg):
    """A sale line is a DEVICE SET-UP FEE if its product description contains a configured set-up-fee
    keyword (mig 217; default ['Device Setup Charge']). Config-driven (RULE TWO) — no engine hard-codes the
    string. Kept SEPARATE from _is_accessory: the set-up fee is counted toward the accessory TARGET but
    reported on its own line/column, never silently blended into the accessory$ number."""
    kws = acfg.get("setup_fee_products") or set()
    if not kws:
        return False
    p = (product or "").strip().lower()
    return bool(p) and any(k in p for k in kws)


def _load_item_map(client, org_id):
    """{item_key: row} for the org's item_mapping (empty dict if migration 041 isn't run yet)."""
    try:
        rows = client.schema("commcalc").table("item_mapping").select("*").eq("org_id", org_id).limit(100000).execute().data or []
        return {r["item_key"]: r for r in rows if r.get("item_key")}
    except Exception:
        return {}


@router.get("/flag-rules")
def get_flag_rules(org_id: str = ORG_ID):
    """The user-defined accessory flag rules: threshold + default chargeback amount."""
    require_org(org_id)
    return _flag_rules(sb(), org_id)


@router.put("/flag-rules")
def put_flag_rules(body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    row = {"id": 1, "org_id": org_id, "updated_at": _cb_now()}
    if body.get("accessory_threshold") is not None:
        row["accessory_threshold"] = safe_float(body.get("accessory_threshold"))
    if body.get("accessory_chargeback_amount") is not None:
        row["accessory_chargeback_amount"] = safe_float(body.get("accessory_chargeback_amount"))
    if body.get("accessory_min_threshold") is not None:
        row["accessory_min_threshold"] = safe_float(body.get("accessory_min_threshold"))
    try:
        sb().schema("commcalc").table("flag_rules").upsert(row, on_conflict="id").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migrations 041 + 051 first: {e}")
    return _flag_rules(sb(), org_id)


# ── DUAL-CATEGORY item mapping (mig 210): per-org EDITABLE category value lists (RULE TWO) ───────────
# Each item_mapping row now also carries sales_category (master/sales dimension) + kpi_category (KPI
# dimension). The allowed VALUES per dimension are per-org configurable (commcalc.item_category_config),
# seeded with sensible defaults. The M1 activation-payment gate reads sales_category|kpi_category ==
# 'activation_payment'. STABLE INTERFACE for the parallel custom-report package — see the inbox note.
DEFAULT_SALES_CATEGORIES = [
    {"value": "activation", "label": "Activation", "sort_order": 10},
    {"value": "upgrade", "label": "Upgrade", "sort_order": 20},
    {"value": "accessory", "label": "Accessory", "sort_order": 30},
    {"value": "swap", "label": "Swap", "sort_order": 40},
    {"value": "bill_payment", "label": "Bill payment", "sort_order": 50},
    {"value": "rebate", "label": "Rebate", "sort_order": 60},
    {"value": "activation_payment", "label": "Activation payment", "sort_order": 70},
    {"value": "misc_other", "label": "Other", "sort_order": 80},
]
DEFAULT_KPI_CATEGORIES = [
    {"value": "protection", "label": "Protection", "sort_order": 10},
    {"value": "wireless_home_internet", "label": "Wireless home internet", "sort_order": 20},
    {"value": "activation_payment", "label": "Activation payment", "sort_order": 30},
    {"value": "accessory", "label": "Accessory", "sort_order": 40},
    {"value": "plan", "label": "Plan", "sort_order": 50},
    {"value": "other", "label": "Other", "sort_order": 60},
]
_DEFAULT_CATS = {"sales": DEFAULT_SALES_CATEGORIES, "kpi": DEFAULT_KPI_CATEGORIES}


def _item_category_values(client, org_id, dimension, seed_if_empty=True):
    """The org's category value list for a dimension ('sales'|'kpi'), sorted. Falls back to the seeded
    defaults when the table is empty for this org (best-effort seeding it so it becomes editable) or when
    mig 210 isn't applied. Degrades to the code defaults — never raises."""
    dim = dimension if dimension in _DEFAULT_CATS else "sales"
    try:
        rows = (client.schema("commcalc").table("item_category_config").select("*")
                .eq("org_id", org_id).eq("dimension", dim).eq("is_active", True).execute().data) or []
    except Exception:
        return [{**d, "source": "default"} for d in _DEFAULT_CATS[dim]]
    if not rows:
        if seed_if_empty:
            try:
                seed = [{"org_id": org_id, "dimension": dim, "value": d["value"], "label": d["label"],
                         "sort_order": d["sort_order"], "is_active": True, "source": "seed"} for d in _DEFAULT_CATS[dim]]
                client.schema("commcalc").table("item_category_config").upsert(
                    seed, on_conflict="org_id,dimension,value").execute()
            except Exception:
                pass
        return [{**d, "source": "seed"} for d in _DEFAULT_CATS[dim]]
    rows.sort(key=lambda r: (r.get("sort_order") or 100, r.get("label") or r.get("value") or ""))
    return rows


@router.get("/item-categories")
def get_item_categories(org_id: str = ORG_ID):
    """The per-org editable category value lists for BOTH dimensions (sales + kpi). Seeded defaults are
    returned (and best-effort persisted) when unset. Drives the dual-category pickers."""
    require_org(org_id)
    client = sb()
    return {"sales": _item_category_values(client, org_id, "sales"),
            "kpi": _item_category_values(client, org_id, "kpi"), "ready": True}


@router.put("/item-categories")
def put_item_category(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Admin-only. Add/rename/deactivate ONE category value. body: {dimension('sales'|'kpi'), value,
    label?, is_active?, sort_order?}. value is the canonical key stored on item_mapping.*_category."""
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    dim = (body.get("dimension") or "").strip().lower()
    val = (body.get("value") or "").strip().lower().replace(" ", "_")
    if dim not in ("sales", "kpi") or not val:
        raise HTTPException(400, "dimension ('sales'|'kpi') and value are required.")
    row = {"org_id": org_id, "dimension": dim, "value": val,
           "label": (body.get("label") or val.replace("_", " ").title()),
           "is_active": bool(body.get("is_active")) if body.get("is_active") is not None else True,
           "source": "manual", "updated_at": _datetime.now(_timezone.utc).isoformat()}
    if body.get("sort_order") is not None:
        row["sort_order"] = int(body.get("sort_order") or 100)
    try:
        sb().schema("commcalc").table("item_category_config").upsert(row, on_conflict="org_id,dimension,value").execute()
    except Exception as e:
        raise HTTPException(500, f"could not save category (is migration 210 applied?): {e}")
    return {"ok": True, "dimension": dim, "value": val}


@router.get("/item-mapping/facets")
def item_mapping_facets(period: str = "", org_id: str = ORG_ID):
    """Distinct STORES / departments / categories present in the org's raw_sales (optionally one period),
    for the item-mapping filter dropdowns (pick-don't-type). Read-only; degrades to empty lists."""
    require_org(org_id)
    from collections import Counter
    stores, depts, cats = Counter(), Counter(), Counter()
    try:
        q = sb().schema("commcalc").table("raw_sales").select("store,department,category").eq("org_id", org_id)
        if period:
            q = q.in_("period", _pvariants(period))
        for r in (q.limit(100000).execute().data or []):
            s = str(r.get("store") or "").strip()
            if s:
                stores[s] += 1
            d = str(r.get("department") or "").strip()
            if d:
                depts[d] += 1
            c = str(r.get("category") or "").strip()
            if c:
                cats[c] += 1
    except Exception:
        pass
    return {"stores": [s for s, _ in stores.most_common()],
            "departments": [d for d, _ in depts.most_common()],
            "categories": [c for c, _ in cats.most_common()]}


@router.get("/item-mapping")
def get_item_mapping(search: str = None, item_type: str = None, store: str = None,
                     department: str = None, category: str = None, org_id: str = ORG_ID):
    """The item → type/model + DUAL-category (sales_category, kpi_category, mig 210) mapping. Filters:
    search (sku/desc/model text), item_type, department, category, and store (restricts to item_keys sold
    in that store, computed from raw_sales). Pick-don't-type options come from GET /item-mapping/facets."""
    require_org(org_id)
    try:
        q = sb().schema("commcalc").table("item_mapping").select("*").eq("org_id", org_id)
        if item_type:
            q = q.eq("item_type", item_type)
        rows = q.limit(100000).execute().data or []
    except Exception as e:
        return {"items": [], "ready": False, "detail": str(e)[:200], "counts": {}, "total": 0}
    if department:
        rows = [r for r in rows if (r.get("department") or "") == department]
    if category:
        rows = [r for r in rows if (r.get("category") or "") == category]
    if store:
        # which item_keys were sold in this store (org-scoped raw_sales scan)
        keyset = set()
        try:
            srows = (sb().schema("commcalc").table("raw_sales").select("sku,product_desc")
                     .eq("org_id", org_id).eq("store", store).limit(100000).execute().data) or []
            for sr in srows:
                keyset.add(_item_key(sr.get("sku"), sr.get("product_desc")))
        except Exception:
            keyset = None
        if keyset is not None:
            rows = [r for r in rows if (r.get("item_key") or "") in keyset]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.get("item_desc") or "").lower()
                or s in (r.get("sku") or "").lower() or s in (r.get("device_model") or "").lower()]
    rows.sort(key=lambda r: (r.get("item_type") or "", (r.get("item_desc") or r.get("sku") or "")))
    counts = {}
    for r in rows:
        t = r.get("item_type") or "unclassified"
        counts[t] = counts.get(t, 0) + 1
    return {"items": rows, "ready": True, "counts": counts, "total": len(rows)}


@router.post("/item-mapping")
def upsert_item_mapping(body: dict, org_id: str = ORG_ID):
    """Classify / edit one item (type + device_model). Keyed by item_key (sku, else description)."""
    require_org(org_id)
    key = (body.get("item_key") or _item_key(body.get("sku"), body.get("item_desc")))
    if not key:
        raise HTTPException(400, "item_key (or sku/item_desc) required")
    item_type = (body.get("item_type") or "unclassified").strip()
    device_model = (body.get("device_model") or "").strip() or None
    if item_type == "phone" and not device_model:
        raise HTTPException(400, "Phone model is required when the item type is 'phone'.")
    row = {"org_id": org_id, "item_key": key,
           "sku": (body.get("sku") or "").strip() or None,
           "item_desc": (body.get("item_desc") or "").strip() or None,
           "item_type": item_type, "device_model": device_model,
           "department": body.get("department"), "category": body.get("category"),
           "source": "manual", "updated_at": _cb_now()}
    # DUAL-category (mig 210): only stamp when present so a type-only save never nulls a category.
    if "sales_category" in body:
        row["sales_category"] = (str(body.get("sales_category") or "").strip() or None)
    if "kpi_category" in body:
        row["kpi_category"] = (str(body.get("kpi_category") or "").strip() or None)
    try:
        sb().schema("commcalc").table("item_mapping").upsert(row, on_conflict="org_id,item_key").execute()
        if device_model:
            _register_device_model(org_id, device_model)
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 041 first: {e}")
    return {"ok": True, "item_key": key}


@router.post("/item-mapping/bulk")
def bulk_item_mapping(body: dict, org_id: str = ORG_ID):
    """Apply a type and/or phone model to MANY items at once. Body: {item_keys: [...],
    item_type?, device_model?}. Only the provided fields are changed on each row."""
    require_org(org_id)
    keys = [k for k in (body.get("item_keys") or []) if k]
    if not keys:
        raise HTTPException(400, "item_keys required")
    item_type = (body.get("item_type") or "").strip() or None
    device_model = (body.get("device_model") or "").strip() or None
    if item_type == "phone" and not device_model:
        raise HTTPException(400, "Phone model is required when setting type to 'phone'. Pick a model to apply to the selected items.")
    sales_category = (body.get("sales_category") or "").strip() if "sales_category" in body else None
    kpi_category = (body.get("kpi_category") or "").strip() if "kpi_category" in body else None
    if not item_type and not device_model and sales_category is None and kpi_category is None:
        raise HTTPException(400, "Provide item_type, device_model, sales_category and/or kpi_category to apply.")
    patch = {"source": "manual", "updated_at": _cb_now()}
    if item_type:
        patch["item_type"] = item_type
    if device_model:
        patch["device_model"] = device_model
    # DUAL-category bulk assign (mig 210): '' clears the category, a value sets it, absent leaves it alone.
    if sales_category is not None:
        patch["sales_category"] = sales_category or None
    if kpi_category is not None:
        patch["kpi_category"] = kpi_category or None
    client = sb()
    updated = 0
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        try:
            client.schema("commcalc").table("item_mapping").update(patch).eq("org_id", org_id).in_("item_key", chunk).execute()
            updated += len(chunk)
        except Exception as e:
            raise HTTPException(500, f"bulk update failed — run migration 041 first: {e}")
    if device_model:
        _register_device_model(org_id, device_model)
    return {"ok": True, "updated": len(keys), "item_type": item_type, "device_model": device_model}


@router.delete("/item-mapping/{item_id}")
def delete_item_mapping(item_id: str, org_id: str = ORG_ID):
    require_org(org_id)
    sb().schema("commcalc").table("item_mapping").delete().eq("org_id", org_id).eq("id", item_id).execute()
    return {"ok": True}


# ── Phone-model registry (the Item/Model "catalogue") ─────────────────────────────────────────
def _register_device_model(org_id, model):
    """Best-effort: ensure a model used on an item is in the canonical registry too."""
    m = (model or "").strip()
    if not m:
        return
    try:
        sb().schema("commcalc").table("device_model").upsert(
            {"org_id": org_id, "model": m}, on_conflict="org_id,model").execute()
    except Exception:
        pass  # registry table may not exist yet (migration 043) — non-fatal


@router.get("/device-models")
def list_device_models(org_id: str = ORG_ID):
    """Canonical phone models for the combobox: the registry UNION the distinct models already used
    on item_mapping, so existing data is never lost even before the registry is populated."""
    require_org(org_id)
    client = sb()
    models = set()
    reg = []
    try:
        reg = client.schema("commcalc").table("device_model").select("id,model").eq("org_id", org_id).execute().data or []
        for r in reg:
            if r.get("model"):
                models.add(r["model"].strip())
    except Exception:
        pass  # registry table not created yet
    try:
        used = client.schema("commcalc").table("item_mapping").select("device_model").eq("org_id", org_id).eq("item_type", "phone").limit(100000).execute().data or []
        for r in used:
            if r.get("device_model"):
                models.add(r["device_model"].strip())
    except Exception:
        pass
    return {"models": sorted(m for m in models if m), "registry": reg}


@router.post("/device-models")
def add_device_model(body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "model required")
    try:
        r = sb().schema("commcalc").table("device_model").upsert(
            {"org_id": org_id, "model": model}, on_conflict="org_id,model").execute()
    except Exception as e:
        raise HTTPException(500, f"add failed — run migration 043 first: {e}")
    return r.data[0] if r.data else {"ok": True, "model": model}


@router.delete("/device-models/{mid}")
def delete_device_model(mid: str, org_id: str = ORG_ID):
    """Remove a model from the registry. Does NOT touch items already using it."""
    require_org(org_id)
    sb().schema("commcalc").table("device_model").delete().eq("org_id", org_id).eq("id", mid).execute()
    return {"ok": True}


@router.post("/item-mapping/seed-from-catalog")
def seed_item_mapping_from_catalog(org_id: str = ORG_ID):
    """Seed item_mapping from the Product Catalog ('SU sheet'): each catalog SKU → device_model
    (from product_desc) + guessed type. Skips rows the user has classified (source='manual')."""
    require_org(org_id)
    client = sb()
    try:
        cat = client.schema("commcalc").table("raw_catalog").select("sku,product_desc").eq("org_id", org_id).limit(100000).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"catalog read failed: {e}")
    existing = _load_item_map(client, org_id)
    rows, seen = [], set()
    for c in cat:
        key = _item_key(c.get("sku"), c.get("product_desc"))
        if not key or key in seen:
            continue
        seen.add(key)
        cur = existing.get(key)
        if cur and cur.get("source") == "manual":
            continue
        desc = (c.get("product_desc") or "").strip()
        rows.append({"org_id": org_id, "item_key": key, "sku": (c.get("sku") or "").strip() or None,
                     "item_desc": desc or None, "device_model": (cur.get("device_model") if cur else None) or desc or None,
                     "item_type": (cur.get("item_type") if cur else None) or _guess_item_type(None, None, desc),
                     "source": "catalog", "updated_at": _cb_now()})
    n = 0
    try:
        for i in range(0, len(rows), 500):
            client.schema("commcalc").table("item_mapping").upsert(rows[i:i + 500], on_conflict="org_id,item_key").execute()
            n += len(rows[i:i + 500])
    except Exception as e:
        raise HTTPException(500, f"seed failed — run migration 041 first: {e}")
    return {"ok": True, "seeded": n, "catalog_rows": len(cat)}


@router.get("/accessory-flags")
def accessory_flags(start: str = None, end: str = None, store: str = None, rep: str = None,
                    period: str = None, threshold: float = None, min_threshold: float = None,
                    authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Accessory sales priced ABOVE the threshold, over a date range / store / rep.
    The threshold defaults to the saved flag_rules value but can be OVERRIDDEN per request via the
    `threshold` query param (the page applies a user-defined value on Load without persisting it; the
    page's Save still writes the default). Each row carries the phone model (the transaction's phone
    line model, else the item's own mapped model) + a default chargeback amount. Unseen items auto-add
    to item_mapping. already_flagged = a chargeback_review row already exists for that sale. The
    response also rolls the flagged rows up per rep + per store (count + $ total) for the dashboard."""
    require_org(org_id)
    client = sb()
    rules = _flag_rules(client, org_id)
    threshold = safe_float(threshold) if threshold is not None else safe_float(rules.get("accessory_threshold"))
    min_t = safe_float(min_threshold) if min_threshold is not None else safe_float(rules.get("accessory_min_threshold"))
    default_cb = safe_float(rules.get("accessory_chargeback_amount"))

    q = client.schema("commcalc").table("raw_sales").select(
        "id,trans_id,trans_date,period,store,salesperson,department,category,product_desc,sku,ext_price,voided").eq("org_id", org_id)
    if period:
        q = q.in_("period", _pvariants(period))
    if start:
        q = q.gte("trans_date", start)
    if end:
        q = q.lte("trans_date", end)
    if store:
        q = q.eq("store", store)
    if rep:
        q = q.eq("salesperson", rep)
    sales = q.limit(200000).execute().data or []

    # SCOPE (B6): a manager with a span sees only their own stores' flagged sales — this is a
    # chargeback/pricing tool, so it must not leak other markets. None = admin/unrestricted; an empty set
    # (a self rep) is not narrowed here (they don't reach this page — nav is ['all','market']).
    from app.modules.storeops.router import scope_keyset, in_keyset
    _ks = scope_keyset(authorization, org_id)
    if _ks:
        sales = [r for r in sales if in_keyset(_ks, r.get("store"))]

    imap = _load_item_map(client, org_id)
    # Auto-add unseen items so the mapping self-maintains (guess type from dept/category/desc).
    new_rows = {}
    for r in sales:
        key = _item_key(r.get("sku"), r.get("product_desc"))
        if not key or key in imap or key in new_rows:
            continue
        new_rows[key] = {"org_id": org_id, "item_key": key, "sku": (r.get("sku") or "").strip() or None,
                         "item_desc": (r.get("product_desc") or "").strip() or None,
                         "department": r.get("department"), "category": r.get("category"),
                         "item_type": _guess_item_type(r.get("department"), r.get("category"), r.get("product_desc")),
                         "source": "auto", "updated_at": _cb_now()}
    if new_rows:
        vals = list(new_rows.values())
        try:
            for i in range(0, len(vals), 500):
                client.schema("commcalc").table("item_mapping").upsert(vals[i:i + 500], on_conflict="org_id,item_key").execute()
            imap.update(new_rows)
        except Exception:
            pass  # migration 041 not run yet → degrade to dept/category-only matching below

    # Per-transaction phone model = the phone line's mapped model (what the accessory was attached to).
    trans_phone = {}
    for r in sales:
        m = imap.get(_item_key(r.get("sku"), r.get("product_desc")))
        if m and m.get("item_type") == "phone":
            model = (m.get("device_model") or m.get("item_desc") or r.get("product_desc") or "").strip()
            if model:
                trans_phone.setdefault(str(r.get("trans_id") or ""), model)

    flagged = set()
    try:
        crs = client.schema("commcalc").table("chargeback_review").select("dedupe_key") \
            .eq("org_id", org_id).eq("source", "accessory_over").limit(100000).execute().data or []
        flagged = {c.get("dedupe_key") for c in crs}
    except Exception:
        pass

    def _is_acc(m, r):
        if m:
            return m.get("item_type") == "accessory"
        # Migration not run / item missing → fall back to a dept/category guess.
        return _guess_item_type(r.get("department"), r.get("category"), r.get("product_desc")) == "accessory"

    out = []
    for r in sales:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        key = _item_key(r.get("sku"), r.get("product_desc"))
        m = imap.get(key)
        if not _is_acc(m, r):
            continue
        # Boost Protect (and all its variants) is a protection/insurance plan, NOT an accessory —
        # never flag it. Substring match keeps real accessories like "screen protector" in scope.
        _pd = (r.get("product_desc") or "").lower()
        if "boost protect" in _pd:
            continue
        # Xfinity boxes / products are a carrier device, NOT an accessory — never flag them.
        if "xfinity" in _pd:
            continue
        price = safe_float(r.get("ext_price"))
        # Flag accessories priced ABOVE the max threshold OR sold BELOW the allowed minimum (underselling).
        # min_t <= 0 disables the under-min check (default), so behavior is unchanged until a min is set.
        over = price > threshold
        under = min_t > 0 and price < min_t
        if not (over or under):
            continue
        reason = "over" if over else "under"
        tid = str(r.get("trans_id") or "")
        phone_model = trans_phone.get(tid) or ((m or {}).get("device_model") or "").strip() or None
        dk = f"acc:{tid}:{key}"
        out.append({
            "sale_id": r.get("id"), "trans_id": tid, "trans_date": r.get("trans_date"),
            "period": r.get("period"), "store": r.get("store"), "rep": r.get("salesperson"),
            "department": r.get("department"), "category": r.get("category"),
            "item_desc": r.get("product_desc"), "sku": r.get("sku"),
            "ext_price": round(price, 2), "phone_model": phone_model, "flag_reason": reason,
            "chargeback_amount": default_cb, "dedupe_key": dk, "already_flagged": dk in flagged,
        })
    out.sort(key=lambda x: (x["store"] or "", x["rep"] or "", -(x["ext_price"] or 0)))

    # Rollups for the dashboard: "number of flags" = flagged line items; "$ amount" = ext_price total.
    # keyfn yields the grouping key (a single field, or a (store, rep) tuple for the cross breakdown).
    def _rollup(rows, keyfn, extra=None):
        agg = {}
        for x in rows:
            k = keyfn(x)
            a = agg.setdefault(k, {"txns": set(), "flags": 0, "total": 0.0, "chargeback_total": 0.0,
                                   "already": 0, "over": 0, "under": 0})
            a["txns"].add(x["trans_id"])
            a["flags"] += 1                      # number of flagged accessory sales
            a["total"] += x["ext_price"] or 0    # $ the accessories rung out in those flags
            a["chargeback_total"] += x["chargeback_amount"] or 0
            a[x.get("flag_reason") or "over"] += 1
            if x["already_flagged"]:
                a["already"] += 1
        res = []
        for k, a in agg.items():
            row = {"txns": len(a["txns"]), "flags": a["flags"], "items": a["flags"],
                   "total": round(a["total"], 2), "chargeback_total": round(a["chargeback_total"], 2),
                   "flagged": a["already"], "over": a["over"], "under": a["under"]}
            row.update(extra(k) if extra else {"name": k})
            res.append(row)
        res.sort(key=lambda r: -r["total"])
        return res

    summary = {
        "txns": len({x["trans_id"] for x in out}), "items": len(out), "flags": len(out),
        "total": round(sum(x["ext_price"] or 0 for x in out), 2),
        "chargeback_total": round(sum(x["chargeback_amount"] or 0 for x in out), 2),
        "over": sum(1 for x in out if x.get("flag_reason") != "under"),
        "under": sum(1 for x in out if x.get("flag_reason") == "under"),
    }
    # RULE FIVE / RULE THREE filter OPTIONS (B6): universal, ORG-SCOPED store + market lists (from
    # commcalc.store_mapping — the same universal source the rest of the module uses) so a DM/market user
    # always has pick-don't-type options. Previously the page sourced markets/stores from the ASSET module's
    # /asset/filter-options, which a non-asset user can't read → the market & store filters were empty.
    # Options are narrowed to the caller's span (in_keyset) so a DM only picks from their own stores.
    filt_markets, filt_stores, _seen = set(), [], set()
    try:
        sm = (client.schema("commcalc").table("store_mapping")
              .select("store_code,store_address,market").eq("org_id", org_id).execute().data) or []
    except Exception:
        sm = []
    for m in sm:
        addr = str(m.get("store_address") or "").strip()
        code = str(m.get("store_code") or "").strip()
        mk = str(m.get("market") or "").strip()
        if _ks and not in_keyset(_ks, addr, code):
            continue
        if mk:
            filt_markets.add(mk)
        if addr and addr.lower() not in _seen:
            _seen.add(addr.lower())
            filt_stores.append({"value": addr, "market": mk})
    # include any store string actually present in the flagged sales but not in store_mapping (unmapped),
    # so it can still be filtered — keyset already applied to `sales` above.
    for x in out:
        st = str(x.get("store") or "").strip()
        if st and st.lower() not in _seen:
            _seen.add(st.lower())
            filt_stores.append({"value": st, "market": ""})
    filters = {"stores": sorted(filt_stores, key=lambda s: s["value"]), "markets": sorted(filt_markets)}
    return {"rows": out, "threshold": threshold, "min_threshold": min_t, "default_chargeback": default_cb,
            "total": len(out), "flagged_qty": sum(1 for x in out if x["already_flagged"]),
            "summary": summary, "filters": filters,
            "by_rep": _rollup(out, lambda x: x.get("rep") or "—"),
            "by_store": _rollup(out, lambda x: x.get("store") or "—"),
            "by_store_rep": _rollup(out, lambda x: ((x.get("store") or "—"), (x.get("rep") or "—")),
                                    extra=lambda k: {"store": k[0], "rep": k[1]})}


@router.get("/accessory-flags/receipt")
def accessory_receipt(trans_id: str, org_id: str = ORG_ID):
    """Full receipt snapshot for one transaction — EVERY raw_sales line item for that trans_id (phones
    + accessories + plans), with a header (store/rep/date/register/tender). Powers the click-through
    drill-down on the Accessory Flags page."""
    require_org(org_id)
    client = sb()
    rows = (client.schema("commcalc").table("raw_sales").select(
        "trans_id,trans_date,period,store,salesperson,user_login,department,category,contract_type,"
        "product_desc,sku,product_id,gp,ext_price,mdn,serial_1,register,tender_type,trans_type,voided")
        .eq("org_id", org_id).eq("trans_id", str(trans_id)).limit(2000).execute().data) or []
    if not rows:
        return {"trans_id": trans_id, "header": {}, "lines": [], "line_count": 0, "total": 0.0}
    imap = _load_item_map(client, org_id)
    lines, total = [], 0.0
    for r in rows:
        price = safe_float(r.get("ext_price"))
        total += price
        m = imap.get(_item_key(r.get("sku"), r.get("product_desc")))
        itype = (m.get("item_type") if m else None) or _guess_item_type(r.get("department"), r.get("category"), r.get("product_desc"))
        lines.append({
            "product_desc": r.get("product_desc"), "sku": r.get("sku"), "item_type": itype,
            "department": r.get("department"), "category": r.get("category"),
            "contract_type": r.get("contract_type"), "ext_price": round(price, 2),
            "gp": round(safe_float(r.get("gp")), 2), "mdn": r.get("mdn"), "serial_1": r.get("serial_1"),
            "voided": str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"),
        })
    # phones/plans first, accessories next, then the rest — reads like a real receipt
    order = {"phone": 0, "plan": 1, "accessory": 2}
    lines.sort(key=lambda l: (order.get(l["item_type"], 3), -(l["ext_price"] or 0)))
    h = rows[0]
    header = {"trans_id": trans_id, "trans_date": h.get("trans_date"), "period": h.get("period"),
              "store": h.get("store"), "rep": h.get("salesperson"), "register": h.get("register"),
              "tender_type": h.get("tender_type"), "trans_type": h.get("trans_type")}
    return {"trans_id": trans_id, "header": header, "lines": lines, "line_count": len(lines), "total": round(total, 2)}


@router.post("/accessory-flags/push")
def accessory_flags_push(body: dict, org_id: str = ORG_ID):
    """Flag selected accessory rows → chargeback bucket, ASSIGNED to the rep who sold it (writes the
    employee chargeback_items row). Body: {rows:[{trans_id,sku,item_desc,dedupe_key,rep,store,
    store_code,period,trans_date,phone_model,ext_price,chargeback_amount}], assigned_by}.
    Idempotent per dedupe_key — re-pushing updates the amount instead of duplicating."""
    require_org(org_id)
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows[] required")
    by = body.get("assigned_by") or "admin"
    client = sb()
    pushed, errors = 0, []
    for r in rows:
        rep = (r.get("rep") or "").strip()
        dk = r.get("dedupe_key") or f"acc:{r.get('trans_id')}:{_item_key(r.get('sku'), r.get('item_desc'))}"
        amount = abs(safe_float(r.get("chargeback_amount")))
        if not rep:
            errors.append({"dedupe_key": dk, "error": "no rep/salesperson on the sale"})
            continue
        detail = ("Accessory over threshold: " + (r.get("item_desc") or r.get("sku") or "accessory")
                  + (f" ({r.get('phone_model')})" if r.get("phone_model") else "")
                  + f" — sold ${round(safe_float(r.get('ext_price')), 2)}")
        period = (r.get("period") or "").strip()
        if not period and r.get("trans_date"):
            try:
                from dateutil import parser as _dp
                period = _dp.parse(str(r.get("trans_date"))).strftime("%B %Y")
            except Exception:
                period = ""
        review = {"org_id": org_id, "source": "accessory_over", "severity": "warning",
                  "status": "assigned", "store_address": r.get("store"), "store_code": r.get("store_code"),
                  "period": period or "", "occurred_date": str(r.get("trans_date") or "")[:10],
                  "detail": detail[:300], "amount": amount, "suggested_rep": rep,
                  "assigned_rep": rep, "assigned_by": by, "assigned_at": _cb_now(), "dedupe_key": dk,
                  "raw": {"trans_id": r.get("trans_id"), "sku": r.get("sku"),
                          "phone_model": r.get("phone_model"), "ext_price": r.get("ext_price")}}
        try:
            res = client.schema("commcalc").table("chargeback_review").upsert(
                review, on_conflict="org_id,dedupe_key").execute()
            cb_id = (res.data or [{}])[0].get("id")
            ref = f"cbr-{cb_id}" if cb_id else f"acc-{dk}"
            item = {"org_id": org_id, "period": period or "Unassigned", "epay_salesperson": rep,
                    "store": r.get("store") or r.get("store_code") or "",
                    "source": "chargeback_review", "source_ref": ref, "description": detail[:300],
                    "amount": amount, "mdn": "", "imei": "", "deduct": True, "decided_at": _cb_now()}
            client.schema("commcalc").table("chargeback_items").delete().eq("org_id", org_id) \
                .eq("source", "chargeback_review").eq("source_ref", ref).execute()
            client.schema("commcalc").table("chargeback_items").insert(item).execute()
            if cb_id:
                client.schema("commcalc").table("chargeback_review").update(
                    {"chargeback_item_ref": ref, "updated_at": _cb_now()}).eq("id", cb_id).execute()
            pushed += 1
        except Exception as e:
            errors.append({"dedupe_key": dk, "error": str(e)[:200]})
    return {"ok": True, "pushed": pushed, "errors": errors, "total": len(rows)}


# ── VIP PayGo / asset-lending ledger (read endpoints; data from the sweep, migration 014) ──
@router.get("/vip/paygo/summary")
async def vip_paygo_summary(org_id: str = ORG_ID):
    """Current week owed + weekly history of the VIP asset-lending (PayGo) billing.
    Degrades to empty if migration 014 hasn't been run yet."""
    require_org(org_id)
    client = sb()
    try:
        rows = client.schema('commcalc').table('vip_paygo_payments') \
            .select('vip_payment_id,batch_type,dealer,created_on,invoice_count,amount,amount_overdue,status,period') \
            .eq('org_id', org_id).order('created_on', desc=True).limit(500).execute().data or []
    except Exception as e:
        return {"configured": False, "detail": str(e)[:200], "current": None,
                "history": [], "totals": {}}
    pending = [r for r in rows if r.get('batch_type') == 'pending']
    approved = [r for r in rows if r.get('batch_type') != 'pending']
    current = max(pending, key=lambda r: r.get('created_on') or '', default=None)
    return {
        "configured": True,
        "current": current,
        "history": approved,
        "totals": {
            "current_owed": round(float(current['amount']), 2) if current and current.get('amount') is not None else 0.0,
            "current_overdue": round(float(current['amount_overdue']), 2) if current and current.get('amount_overdue') is not None else 0.0,
            "weeks": len(approved),
            "lifetime_paid": round(sum(float(r.get('amount') or 0) for r in approved), 2),
        },
    }


@router.get("/vip/paygo/payment/{vip_payment_id}")
async def vip_paygo_payment_detail(vip_payment_id: int, org_id: str = ORG_ID):
    """One PayGo batch + its invoice numbers (which join vip_invoices.invoice_number)."""
    require_org(org_id)
    client = sb()
    pay = client.schema('commcalc').table('vip_paygo_payments').select('*') \
        .eq('org_id', org_id).eq('vip_payment_id', vip_payment_id).limit(1).execute().data or []
    invs = client.schema('commcalc').table('vip_paygo_payment_invoices') \
        .select('invoice_number,dealer,created_on') \
        .eq('org_id', org_id).eq('vip_payment_id', vip_payment_id).limit(5000).execute().data or []
    return {"payment": pay[0] if pay else None, "invoices": invs, "invoice_count": len(invs)}


# ── DLAR portal auto-sweep (boostelevatego.com — replaces the manual monthly upload) ──
# Same pattern as the VIP sweep: backend logs into the Boost Elevate GO portal on a
# schedule (pg_cron → /dlar/sweep/run-due), pulls the store (DLAR) + rep (Advocate)
# reports, and wipes+inserts raw_dlar_store / raw_dlar_rep for the period. Creds live in
# the backend-only table commcalc.dlar_sweep_config; the password is never returned.
def _dlar_cfg(client, org_id):
    rows = client.schema('commcalc').table('dlar_sweep_config').select('*') \
        .eq('org_id', org_id).limit(1).execute().data
    return rows[0] if rows else None


_DLAR_CFG_DEFAULTS = {'enabled': False, 'frequency': 'daily', 'day_of_week': 0,
                      'day_of_month': 1, 'hour': 7, 'timezone': 'America/New_York'}


def _dlar_public_cfg(cfg):
    """Config WITHOUT the password — only whether credentials are set."""
    if not cfg:
        return {**_DLAR_CFG_DEFAULTS, 'configured': False, 'has_credentials': False,
                'portal_user': None, 'next_run_at': None, 'last_run_at': None,
                'last_status': None, 'last_detail': None}
    out = {k: cfg.get(k) for k in (
        'enabled', 'frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
        'portal_user', 'next_run_at', 'last_run_at', 'last_status', 'last_detail')}
    out['configured'] = True
    out['has_credentials'] = bool(cfg.get('portal_user') and cfg.get('portal_pass'))
    return out


def _dlar_set_status(client, org_id, status, detail, mark_run=False):
    upd = {'last_status': status, 'last_detail': (detail or '')[:600]}
    if mark_run:
        upd['last_run_at'] = _datetime.now(_timezone.utc).isoformat()
    client.schema('commcalc').table('dlar_sweep_config').update(upd).eq('org_id', org_id).execute()


def _do_dlar_sweep(org_id):
    """Background worker: read creds from the config table, run the DLAR sweep, record status."""
    client = sb()
    cfg = _dlar_cfg(client, org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        _dlar_set_status(client, org_id, 'error', 'No Carrier portal credentials set in the admin area', mark_run=True)
        return
    _dlar_set_status(client, org_id, 'running', 'Sweep in progress…')
    try:
        res = dlar_sweep.run_dlar_sweep(client, org_id, cfg['portal_user'], cfg['portal_pass'])
        detail = (f"OK — {res['stores']} stores, {res['reps']} reps for {res['period']} "
                  f"(import_date {res['import_date']})")
        # Auto-recompute commissions for the just-imported period so the KPI Metrics page
        # and Targets employee KPIs — which read the rep_commissions snapshot, NOT live DLAR
        # — stay current with the freshly-imported DLAR. This runs on every sweep (the daily
        # cron AND manual 'Import DLAR now'). A recalc failure must NOT fail the sweep, so it
        # is isolated and only noted in last_detail. (_do_dlar_sweep is a sync background
        # worker running in a threadpool thread, so asyncio.run() has no running loop.)
        try:
            import asyncio
            asyncio.run(_run_calculation(res['period'], org_id))
            detail += f" · recalculated commissions for {res['period']}"
        except Exception as _ce:
            detail += f" · ⚠ auto-recalc failed: {_ce}"
        _dlar_set_status(client, org_id, 'ok', detail, mark_run=True)
    except dlar_sweep.DlarLoginError as e:
        _dlar_set_status(client, org_id, 'error', str(e), mark_run=True)
    except Exception as e:
        _dlar_set_status(client, org_id, 'error', f"Sweep failed: {e}", mark_run=True)


@router.get("/dlar/sweep/config")
async def dlar_sweep_get_config(org_id: str = ORG_ID):
    require_org(org_id)
    return _dlar_public_cfg(_dlar_cfg(sb(), org_id))


@router.put("/dlar/sweep/config")
async def dlar_sweep_put_config(body: dict, org_id: str = ORG_ID):
    """Update creds + schedule. Password is WRITE-ONLY: send portal_pass to change it,
    omit/blank to keep the existing one. Never returns the password."""
    require_org(org_id)
    client = sb()
    cur = _dlar_cfg(client, org_id) or {}
    row = {'org_id': org_id}
    for k in ('frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
              'enabled', 'portal_user'):
        if k in body and body[k] is not None:
            row[k] = body[k]
    pw = (body.get('portal_pass') or '').strip()
    if pw:
        row['portal_pass'] = pw
    row['updated_at'] = _datetime.now(_timezone.utc).isoformat()
    merged = {**_DLAR_CFG_DEFAULTS, **cur, **row}
    row['next_run_at'] = _vip_next_run(
        merged.get('frequency') or 'daily', merged.get('day_of_week'),
        merged.get('day_of_month'), merged.get('hour'), merged.get('timezone'))
    client.schema('commcalc').table('dlar_sweep_config').upsert(row, on_conflict='org_id').execute()
    return _dlar_public_cfg(_dlar_cfg(client, org_id))


@router.post("/dlar/sweep/run-now")
async def dlar_sweep_run_now(background_tasks: BackgroundTasks, org_id: str = ORG_ID):
    """Manual 'Run now' / 'Import DLAR now' (background task)."""
    require_org(org_id)
    cfg = _dlar_cfg(sb(), org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        raise HTTPException(400, "Set the Carrier portal credentials first.")
    background_tasks.add_task(_do_dlar_sweep, org_id)
    return {"status": "started"}


@router.post("/dlar/sweep/run-due")
async def dlar_sweep_run_due(background_tasks: BackgroundTasks, x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint: run every enabled config whose next_run_at has passed.
    Reuses NOTIFY_RUN_SECRET so no new env var is needed."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _datetime.now(_timezone.utc).isoformat()
    due = client.schema('commcalc').table('dlar_sweep_config').select('*') \
        .eq('enabled', True).lte('next_run_at', now_iso).execute().data or []
    for cfg in due:
        oid = cfg.get('org_id') or ORG_ID
        nxt = _vip_next_run(cfg.get('frequency') or 'daily', cfg.get('day_of_week'),
                            cfg.get('day_of_month'), cfg.get('hour'), cfg.get('timezone'))
        client.schema('commcalc').table('dlar_sweep_config').update(
            {'next_run_at': nxt}).eq('org_id', oid).execute()
        background_tasks.add_task(_do_dlar_sweep, oid)
    return {"triggered": len(due)}


# ── b2bsoft Inventory Aging auto-sweep (wsreports.b2bsoft.com → commcalc.inventory_value) ──
# Real-time on-hand inventory VALUE per store, feeding the Account Module Balance Sheet
# (editable: a manual override always wins over the swept value). Same scheduling pattern as
# the DLAR/VIP sweeps. Creds live in the backend-only commcalc.b2b_sweep_config; the portal
# client itself is stubbed until wsreports.b2bsoft.com is reverse-engineered (see b2b_sweep.py).
_B2B_CFG_DEFAULTS = {'enabled': False, 'frequency': 'daily', 'day_of_week': 0,
                     'day_of_month': 1, 'hour': 6, 'timezone': 'America/New_York'}


def _b2b_cfg(client, org_id):
    rows = client.schema('commcalc').table('b2b_sweep_config').select('*') \
        .eq('org_id', org_id).limit(1).execute().data
    return rows[0] if rows else None


def _b2b_public_cfg(cfg):
    if not cfg:
        return {**_B2B_CFG_DEFAULTS, 'configured': False, 'has_credentials': False,
                'portal_user': None, 'next_run_at': None, 'last_run_at': None,
                'last_status': None, 'last_detail': None}
    out = {k: cfg.get(k) for k in (
        'enabled', 'frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
        'portal_user', 'next_run_at', 'last_run_at', 'last_status', 'last_detail')}
    out['configured'] = True
    out['has_credentials'] = bool(cfg.get('portal_user') and cfg.get('portal_pass'))
    return out


def _b2b_set_status(client, org_id, status, detail, mark_run=False):
    upd = {'last_status': status, 'last_detail': (detail or '')[:600]}
    if mark_run:
        upd['last_run_at'] = _datetime.now(_timezone.utc).isoformat()
    client.schema('commcalc').table('b2b_sweep_config').update(upd).eq('org_id', org_id).execute()


def _do_b2b_sweep(org_id):
    """Background worker: read creds, run the b2bsoft Inventory Aging sweep, record status."""
    client = sb()
    cfg = _b2b_cfg(client, org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        _b2b_set_status(client, org_id, 'error', 'No b2bsoft credentials set in the admin area', mark_run=True)
        return
    _b2b_set_status(client, org_id, 'running', 'Sweep in progress…')
    try:
        res = b2b_sweep.run_inventory_sweep(client, org_id, cfg['portal_user'], cfg['portal_pass'])
        _b2b_set_status(client, org_id, 'ok',
                        f"OK — {res['stores']} stores, ${res['total_value']:,.2f} on-hand "
                        f"(as of {res['as_of_date']}). Re-compute statements to apply.", mark_run=True)
    except b2b_sweep.B2BLoginError as e:
        _b2b_set_status(client, org_id, 'error', str(e), mark_run=True)
    except b2b_sweep.B2BNotConfigured as e:
        _b2b_set_status(client, org_id, 'error', str(e), mark_run=True)
    except Exception as e:
        _b2b_set_status(client, org_id, 'error', f"Sweep failed: {e}", mark_run=True)


@router.get("/b2b/sweep/config")
async def b2b_sweep_get_config(org_id: str = ORG_ID):
    require_org(org_id)
    return _b2b_public_cfg(_b2b_cfg(sb(), org_id))


@router.put("/b2b/sweep/config")
async def b2b_sweep_put_config(body: dict, org_id: str = ORG_ID):
    """Update creds + schedule. Password is WRITE-ONLY: send portal_pass to change it,
    omit/blank to keep the existing one. Never returns the password."""
    require_org(org_id)
    client = sb()
    cur = _b2b_cfg(client, org_id) or {}
    row = {'org_id': org_id}
    for k in ('frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
              'enabled', 'portal_user'):
        if k in body and body[k] is not None:
            row[k] = body[k]
    pw = (body.get('portal_pass') or '').strip()
    if pw:
        row['portal_pass'] = pw
    row['updated_at'] = _datetime.now(_timezone.utc).isoformat()
    merged = {**_B2B_CFG_DEFAULTS, **cur, **row}
    row['next_run_at'] = _vip_next_run(
        merged.get('frequency') or 'daily', merged.get('day_of_week'),
        merged.get('day_of_month'), merged.get('hour'), merged.get('timezone'))
    client.schema('commcalc').table('b2b_sweep_config').upsert(row, on_conflict='org_id').execute()
    return _b2b_public_cfg(_b2b_cfg(client, org_id))


@router.post("/b2b/sweep/run-now")
async def b2b_sweep_run_now(background_tasks: BackgroundTasks, org_id: str = ORG_ID):
    """Manual 'Fetch inventory now' (background task)."""
    require_org(org_id)
    cfg = _b2b_cfg(sb(), org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        raise HTTPException(400, "Set the b2bsoft credentials first.")
    background_tasks.add_task(_do_b2b_sweep, org_id)
    return {"status": "started"}


@router.post("/b2b/sweep/run-due")
async def b2b_sweep_run_due(background_tasks: BackgroundTasks, x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint: run every enabled config whose next_run_at has passed.
    Reuses NOTIFY_RUN_SECRET so no new env var is needed."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _datetime.now(_timezone.utc).isoformat()
    due = client.schema('commcalc').table('b2b_sweep_config').select('*') \
        .eq('enabled', True).lte('next_run_at', now_iso).execute().data or []
    for cfg in due:
        oid = cfg.get('org_id') or ORG_ID
        nxt = _vip_next_run(cfg.get('frequency') or 'daily', cfg.get('day_of_week'),
                            cfg.get('day_of_month'), cfg.get('hour'), cfg.get('timezone'))
        client.schema('commcalc').table('b2b_sweep_config').update(
            {'next_run_at': nxt}).eq('org_id', oid).execute()
        background_tasks.add_task(_do_b2b_sweep, oid)
    return {"triggered": len(due)}


# ── epay Owner Portal MI+ATU auto-sweep (#5b — headless Playwright; WAF-protected SPA) ──
# Same admin/schedule pattern as the DLAR sweep, but the connector drives headless Chromium
# (see epay_sweep.py). Creds live in the backend-only table commcalc.epay_sweep_config.
_EPAY_CFG_DEFAULTS = {'enabled': False, 'frequency': 'daily', 'day_of_week': 0,
                      'day_of_month': 1, 'hour': 6, 'timezone': 'America/New_York',
                      'portal_url': epay_sweep.DEFAULT_URL,
                      'sweep_mi': True, 'sweep_comp': False, 'sweep_payment': False}


def _epay_cfg(client, org_id):
    rows = client.schema('commcalc').table('epay_sweep_config').select('*') \
        .eq('org_id', org_id).limit(1).execute().data
    return rows[0] if rows else None


def _epay_public_cfg(cfg):
    """Config WITHOUT the password — only whether credentials are set."""
    if not cfg:
        return {**_EPAY_CFG_DEFAULTS, 'configured': False, 'has_credentials': False,
                'portal_user': None, 'next_run_at': None, 'last_run_at': None,
                'last_status': None, 'last_detail': None}
    out = {k: cfg.get(k) for k in (
        'enabled', 'frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
        'portal_url', 'portal_user', 'sweep_mi', 'sweep_comp', 'sweep_payment',
        'next_run_at', 'last_run_at', 'last_status', 'last_detail')}
    out['configured'] = True
    out['has_credentials'] = bool(cfg.get('portal_user') and cfg.get('portal_pass'))
    # sweep_mi defaults on (back-compat: pre-toggle configs only ever pulled MI)
    if out.get('sweep_mi') is None:
        out['sweep_mi'] = True
    return out


def _epay_set_status(client, org_id, status, detail, mark_run=False):
    upd = {'last_status': status, 'last_detail': (detail or '')[:600]}
    if mark_run:
        upd['last_run_at'] = _datetime.now(_timezone.utc).isoformat()
    client.schema('commcalc').table('epay_sweep_config').update(upd).eq('org_id', org_id).execute()


def _do_epay_sweep(org_id):
    """Background worker: read creds, run the epay sweep, record status (no secrets)."""
    client = sb()
    cfg = _epay_cfg(client, org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        _epay_set_status(client, org_id, 'error', 'No epay portal credentials set in the admin area', mark_run=True)
        return
    _epay_set_status(client, org_id, 'running', 'Sweep in progress…')
    # which reports to pull — driven by the registry (report_definitions.auto), falling back to the
    # config toggle per report. registry 'mi_report' maps to the epay_sweep key 'mi'.
    amap = _registry_auto_map(client, org_id)
    def _en(key, fb):
        return amap[key] if key in amap else fb
    reports = []
    if _en('mi_report', cfg.get('sweep_mi') is not False):
        reports.append('mi')
    if _en('comp_report', bool(cfg.get('sweep_comp'))):
        reports.append('comp_report')
    if _en('payment_detail', bool(cfg.get('sweep_payment'))):
        reports.append('payment_detail')
    try:
        res = epay_sweep.run_epay_sweep(client, org_id, cfg.get('portal_url'),
                                        cfg['portal_user'], cfg['portal_pass'], reports=reports)
        _epay_set_status(client, org_id, 'ok', f"OK — {res}", mark_run=True)
    except epay_sweep.EpayLoginError as e:
        _epay_set_status(client, org_id, 'error', str(e), mark_run=True)
    except epay_sweep.EpayPortalError as e:
        # Login worked, but a later step (report run/download/parse) failed — surface the detail.
        _epay_set_status(client, org_id, 'error', f"Login OK · {e}", mark_run=True)
    except Exception as e:
        _epay_set_status(client, org_id, 'error', f"Sweep failed: {e}", mark_run=True)


@router.get("/epay/sweep/config")
async def epay_sweep_get_config(org_id: str = ORG_ID):
    require_org(org_id)
    return _epay_public_cfg(_epay_cfg(sb(), org_id))


@router.put("/epay/sweep/config")
async def epay_sweep_put_config(body: dict, org_id: str = ORG_ID):
    """Update creds + schedule. Password is WRITE-ONLY: send portal_pass to change it,
    omit/blank to keep the existing one. Never returns the password."""
    require_org(org_id)
    client = sb()
    cur = _epay_cfg(client, org_id) or {}
    row = {'org_id': org_id}
    for k in ('frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
              'enabled', 'portal_user', 'portal_url', 'sweep_mi', 'sweep_comp', 'sweep_payment'):
        if k in body and body[k] is not None:
            row[k] = body[k]
    pw = (body.get('portal_pass') or '').strip()
    if pw:
        row['portal_pass'] = pw
    row['updated_at'] = _datetime.now(_timezone.utc).isoformat()
    merged = {**_EPAY_CFG_DEFAULTS, **cur, **row}
    row['next_run_at'] = _vip_next_run(
        merged.get('frequency') or 'daily', merged.get('day_of_week'),
        merged.get('day_of_month'), merged.get('hour'), merged.get('timezone'))
    try:
        client.schema('commcalc').table('epay_sweep_config').upsert(row, on_conflict='org_id').execute()
    except Exception as e:
        # Pre-025 fallback: the sweep_* toggle columns may not exist yet. Drop them and retry so
        # the page keeps saving creds/schedule until migration 025 is run.
        if any(k in str(e) for k in ('sweep_mi', 'sweep_comp', 'sweep_payment')):
            for k in ('sweep_mi', 'sweep_comp', 'sweep_payment'):
                row.pop(k, None)
            client.schema('commcalc').table('epay_sweep_config').upsert(row, on_conflict='org_id').execute()
        else:
            raise
    return _epay_public_cfg(_epay_cfg(client, org_id))


@router.post("/epay/sweep/run-now")
async def epay_sweep_run_now(background_tasks: BackgroundTasks, org_id: str = ORG_ID):
    """Manual 'Run now' (background task)."""
    require_org(org_id)
    cfg = _epay_cfg(sb(), org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        raise HTTPException(400, "Set the epay portal credentials first.")
    background_tasks.add_task(_do_epay_sweep, org_id)
    return {"status": "started"}


@router.post("/epay/sweep/discover-reports")
async def epay_discover_reports(org_id: str = ORG_ID):
    """Enumerate the epay Commissions report menu (id → label) so the Commission Payment Detail
    and Comprehensive Compensation report ids can be wired into the multi-report sweep. Runs the
    headless browser server-side (the portal WAF only allows Railway's egress), synchronously."""
    require_org(org_id)
    cfg = _epay_cfg(sb(), org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        raise HTTPException(400, "Set the epay portal credentials first.")
    import asyncio
    try:
        # sync Playwright can't run inside the asyncio loop — run it in a worker thread.
        reports = await asyncio.to_thread(
            epay_sweep.discover_reports, cfg.get('portal_url'), cfg['portal_user'], cfg['portal_pass'])
        return {"reports": reports, "count": len(reports)}
    except Exception as e:
        raise HTTPException(500, f"discover failed: {type(e).__name__}: {e}")


@router.post("/epay/sweep/run-due")
async def epay_sweep_run_due(background_tasks: BackgroundTasks, x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint: run every enabled config whose next_run_at has passed.
    Reuses NOTIFY_RUN_SECRET so no new env var is needed."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _datetime.now(_timezone.utc).isoformat()
    due = client.schema('commcalc').table('epay_sweep_config').select('*') \
        .eq('enabled', True).lte('next_run_at', now_iso).execute().data or []
    for cfg in due:
        oid = cfg.get('org_id') or ORG_ID
        nxt = _vip_next_run(cfg.get('frequency') or 'daily', cfg.get('day_of_week'),
                            cfg.get('day_of_month'), cfg.get('hour'), cfg.get('timezone'))
        client.schema('commcalc').table('epay_sweep_config').update(
            {'next_run_at': nxt}).eq('org_id', oid).execute()
        background_tasks.add_task(_do_epay_sweep, oid)
    return {"triggered": len(due)}


# ── Calculate endpoint ────────────────────────────────────────
@router.post("/calculate/{period}")
async def calculate(
    period: str,
    background_tasks: BackgroundTasks,
    org_id: str = "00000000-0000-0000-0000-000000000001",
    force: bool = False
):
    """Trigger commission calculation for a period. force=true bypasses the zero-wipe guard
    (deliberately overwrite a paying snapshot with an all-$0 plan-mode result)."""
    require_org(org_id)

    client = sb()

    # Mark as pending. Key calc_status on the SINGLE canonical 'Month YYYY' spelling so '2026-07' and
    # 'July 2026' don't maintain two divergent status rows for the same month (the read path
    # /calc-status/{period} already resolves via _pvariants). The calc itself still runs on the raw
    # `period` string — only the status key is normalized.
    try:
        client.schema('commcalc').table('calc_status').upsert({
            'org_id': org_id, 'period': _canon_period(period), 'calc_status': 'running'
        }, on_conflict='org_id,period').execute()
    except: pass

    background_tasks.add_task(_run_calculation, period, org_id, force)
    return {"status": "started", "period": period, "message": "Calculation running in background"}


def _resolve_carrier_mode(carriers):
    """'boost' -> legacy verified Boost engine; 'plan' -> pay ONLY from configurable Commission
    Plans / Payout Schedules. Conservative so existing Boost tenants (and the house org, whose
    default carrier IS Boost) are never flipped: return 'plan' ONLY when the org's CHOSEN carrier is
    explicitly non-Boost. No explicit default + a Boost carrier present => 'boost'."""
    def _is_boost(c):
        return 'boost' in ((c.get('code') or '') + ' ' + (c.get('name') or '')).lower()
    carriers = carriers or []
    if not carriers:
        return 'boost'
    default = next((c for c in carriers if c.get('is_default')), None)
    if default is not None:
        return 'boost' if _is_boost(default) else 'plan'
    if any(_is_boost(c) for c in carriers):
        return 'boost'
    return 'plan'


def _commission_org_config(client, org_id):
    """Per-tenant commission posture (mig 201, commission-0 §7b). Degrades to safe defaults if the table
    is absent (pre-mig-201): {'pay_disabled': False, 'residual_visibility': 'all'}."""
    default = {"pay_disabled": False, "residual_visibility": "all"}
    try:
        rows = (client.schema('commcalc').table('commission_org_config').select('*')
                .eq('org_id', org_id).limit(1).execute().data) or []
    except Exception:
        return default
    if not rows:
        return default
    r = rows[0]
    return {"pay_disabled": bool(r.get("pay_disabled")),
            "residual_visibility": (r.get("residual_visibility") or "all").strip().lower()}


def _can_view_carrier_residual(authorization, org_id):
    """Permission gate for CARRIER-RESIDUAL (raw_mi-derived) visibility (commission-0 §7b decision 6).
    Carrier-agnostic. Default posture 'all' → always visible (byte-identical to today). A tenant that sets
    residual_visibility='permissioned' requires the 'carrier_residual' RBAC grant: super-admins / scope-all
    admins always pass; a role passes if permissions.modules contains 'carrier_residual' OR
    permissions.data.carrier_residual is true. Degrades OPEN on any resolution error (never 500s a read)."""
    try:
        cfg = _commission_org_config(sb(), org_id)
    except Exception:
        return True
    if cfg.get("residual_visibility") != "permissioned":
        return True  # 'all' — unchanged
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        caller = _resolve_caller(sb(), uid) if uid else None
        if not caller:
            return False
        if caller.get("super_admin"):
            return True
        perms = caller.get("perms") or {}
        if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
            return True
        if "carrier_residual" in (perms.get("modules") or []):
            return True
        if bool((perms.get("data") or {}).get("carrier_residual")):
            return True
        return False
    except Exception:
        return True


def _require_carrier_residual(authorization, org_id):
    """Raise 403 (carrier-agnostic message) when the caller may not see carrier-residual data."""
    if not _can_view_carrier_residual(authorization, org_id):
        raise HTTPException(403, "Carrier residual data is restricted for this tenant — you need the "
                                 "'carrier_residual' permission to view it.")


def _can_view_device_commission(authorization, org_id):
    """Gate for the device-history MONEY table (commission-16). ADMIN-ONLY BY DEFAULT, grantable via the
    DATA_GRANTS 'device_commission' key — same resolution shape as `_can_view_carrier_residual` but
    DEFAULT-CLOSED (device commission $ is not open-by-default). Frontend mirror: `hasDataGrant(
    'device_commission')`. Degrades CLOSED on any resolution error — it can only ever hide $ behind the
    lock note, never leak it. (No tenant toggle: unlike carrier_residual there is no 'all' posture — the
    money table is restricted until the grant is registered + assigned.)"""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        caller = _resolve_caller(sb(), uid) if uid else None
        return device_history.device_commission_allowed(caller)
    except Exception:
        return False


def _has_any_pay_source(client, org_id, period):
    """True if this org has ANYTHING configured to pay reps from for the period: active commission-plan
    ASSIGNMENTS, an active payout_schedule (raw_mi installments), an active plan_installment_schedule
    (sale-triggered installments), or carrier_commission statement rows for the period. Used by the R1
    refuse-to-pay guard — carrier-name-agnostic (asks 'is there anything to pay from', not 'which carrier')."""
    def _count(table, extra=None):
        # select org_id (present on EVERY commcalc table) so a table without an 'id' column can't be
        # miscounted as empty and wrongly trip the refusal.
        try:
            q = (client.schema('commcalc').table(table).select('org_id', count='exact').eq('org_id', org_id))
            if extra:
                q = extra(q)
            return (q.limit(1).execute().count) or 0
        except Exception:
            return 0
    if _count('commission_plan_assignment') > 0:
        return True
    if _count('payout_schedule', lambda q: q.eq('is_active', True)) > 0:
        return True
    if _count('plan_installment_schedule', lambda q: q.eq('is_active', True)) > 0:
        return True
    if _count('carrier_commission', lambda q: q.in_('period', _pvariants(period))) > 0:
        return True
    return False


def _apply_new_engines(client, org_id, period, comms, carrier_mode='boost'):
    """ADDITIVE layer of the new configurable payout engines on top of the standard (Boost) calc.

    BOOST-SAFE: with no commcalc.payout_schedule and no commcalc.commission_plan, the installment + plan
    engines both return EMPTY and this returns `comms` BYTE-IDENTICAL (early return before any mutation).
    Best-effort — any exception keeps the standard result. Only writes optional columns that actually
    exist on rep_commissions (probed), so an un-applied migration can never break the insert.

      • multi-month installments (installment_engine) → residual_installment_comm, ADDED to total_payout.
      • commission plans (commission_engine) → for a PLAN-COVERED rep the plan total REPLACES their spiff
        subtotal (the plan IS their pay structure); plan_comm/plan_name record it. Boost reps have no plan
        assignment, so they are never covered → never touched.
    """
    try:
        from app.modules.commcalc import installment_engine, commission_engine
        inst_by_rep = {}
        try:
            ir = installment_engine.compute_installments(client, org_id, period, persist=True)
            for rep, amt in (ir.get("by_rep") or {}).items():
                if rep:
                    inst_by_rep[str(rep).strip().upper()] = safe_float(amt)
        except Exception:
            inst_by_rep = {}
        # SALE-TRIGGERED multi-month installments (mig 201, doctrine commission-0): rep pay from the SALE
        # LINE, paid-gated on the line being active + receiving residual. Separate component column
        # (installment_comm_sale) from the raw_mi path above. Boost/house has no schedule → returns {} →
        # byte-identical. Flags for sold-but-unpaid are synced separately in _run_calculation.
        sale_inst_by_rep = {}
        try:
            sr = sale_installment_engine.compute_sale_installments(client, org_id, period, persist=True)
            for rep, amt in (sr.get("by_rep") or {}).items():
                if rep:
                    sale_inst_by_rep[str(rep).strip().upper()] = safe_float(amt)
        except Exception:
            sale_inst_by_rep = {}
        plan_by_rep = {}
        try:
            pr = commission_engine.preview(client, org_id, period)
            for r in (pr.get("by_rep") or []):
                rn = str(r.get("rep") or "").strip().upper()
                if rn:
                    plan_by_rep[rn] = {"amount": safe_float(r.get("total_payout")), "plan_name": r.get("plan_name")}
        except Exception:
            plan_by_rep = {}
        # carrier commission STATEMENT (Total/VidaPay etc.): sum total_commission per rep for the period.
        stmt_by_rep = {}
        try:
            srows, _s, _pg = [], 0, 1000
            while True:
                chunk = (client.schema('commcalc').table('carrier_commission')
                         .select('rep_name,total_commission').eq('org_id', org_id)
                         .in_('period', _pvariants(period)).range(_s, _s + _pg - 1).execute().data) or []
                srows.extend(chunk)
                if len(chunk) < _pg:
                    break
                _s += _pg
            for r in srows:
                rn = str(r.get('rep_name') or '').strip().upper()
                if rn:
                    stmt_by_rep[rn] = round(stmt_by_rep.get(rn, 0.0) + safe_float(r.get('total_commission')), 2)
        except Exception:
            stmt_by_rep = {}

        # BOOST PATH: nothing configured → comms returned exactly as-is.
        if not inst_by_rep and not plan_by_rep and not stmt_by_rep and not sale_inst_by_rep:
            return comms

        cols = {}
        for c in ("residual_installment_comm", "installment_comm_sale", "plan_comm", "plan_name", "carrier_statement_comm"):
            try:
                client.schema('commcalc').table('rep_commissions').select(c).limit(1).execute()
                cols[c] = True
            except Exception:
                cols[c] = False

        def _keys(row):
            return {str(row.get("storeops_name") or "").strip().upper(),
                    str(row.get("epay_salesperson") or "").strip().upper()} - {""}

        plan_matched = set()
        for row in comms:
            ks = _keys(row)
            inst = next((inst_by_rep[k] for k in ks if k in inst_by_rep), 0.0)
            sale_inst = next((sale_inst_by_rep[k] for k in ks if k in sale_inst_by_rep), 0.0)
            stmt = next((stmt_by_rep[k] for k in ks if k in stmt_by_rep), 0.0)
            pv = next((plan_by_rep[k] for k in ks if k in plan_by_rep), None)
            if cols["residual_installment_comm"]:
                row["residual_installment_comm"] = inst
            if cols["installment_comm_sale"]:
                row["installment_comm_sale"] = sale_inst
            # carrier_statement_comm = what the CARRIER paid the dealer for this rep (dealer revenue).
            # Recorded for VISIBILITY / recon — NOT auto-added to rep pay. The rep's commission comes from
            # the configured plan / multi-month %MRC schedule, not the dealer-level statement totals.
            if cols["carrier_statement_comm"]:
                row["carrier_statement_comm"] = stmt
            if pv is not None:
                plan_matched |= (ks & set(plan_by_rep))
                if cols["plan_comm"]:
                    row["plan_comm"] = pv["amount"]
                if cols["plan_name"]:
                    row["plan_name"] = pv.get("plan_name")
                base = safe_float(pv["amount"])                       # a plan REPLACES the spiff subtotal
            else:
                base = safe_float(row.get("total_payout"))            # keep the standard calc
            row["total_payout"] = round(base + inst + sale_inst, 2)   # plan + raw_mi + sale installments

        # reps with a PLAN but no standard row → add them (statement-only reps are captured in
        # commcalc.carrier_commission for recon, not paid here)
        pm = parse_period(period)
        for rn, pv in plan_by_rep.items():
            if rn in plan_matched:
                continue
            inst = inst_by_rep.get(rn, 0.0)
            sale_inst = sale_inst_by_rep.get(rn, 0.0)
            base = safe_float(pv["amount"])
            newrow = {"org_id": org_id, "period": period,
                      "period_month": pm.get("month"), "period_year": pm.get("year"),
                      "storeops_name": rn.title(), "epay_salesperson": rn,
                      "subtotal": base, "tier": 1,
                      "total_payout": round(base + inst + sale_inst, 2)}
            if cols["plan_comm"]:
                newrow["plan_comm"] = pv["amount"]
            if cols["plan_name"]:
                newrow["plan_name"] = pv.get("plan_name")
            if cols["residual_installment_comm"]:
                newrow["residual_installment_comm"] = inst
            if cols["installment_comm_sale"]:
                newrow["installment_comm_sale"] = sale_inst
            if cols["carrier_statement_comm"]:
                newrow["carrier_statement_comm"] = stmt_by_rep.get(rn, 0.0)
            comms.append(newrow)

        # PLAN MODE: reps paid ONLY via multi-month installments or a carrier statement (no base row and
        # no commission plan) would otherwise be dropped. Capture them so a Total/plan tenant's report is
        # complete. Boost/house-org unaffected (only runs when carrier_mode == 'plan').
        if carrier_mode == 'plan':
            represented = set()
            for row in comms:
                represented |= _keys(row)
            for rn in (set(inst_by_rep) | set(stmt_by_rep) | set(sale_inst_by_rep)) - represented:
                inst = inst_by_rep.get(rn, 0.0)
                sale_inst = sale_inst_by_rep.get(rn, 0.0)
                newrow = {"org_id": org_id, "period": period,
                          "period_month": pm.get("month"), "period_year": pm.get("year"),
                          "storeops_name": rn.title(), "epay_salesperson": rn,
                          "subtotal": 0.0, "tier": 1, "tier_source": "plan",
                          "total_payout": round(safe_float(inst) + safe_float(sale_inst), 2)}
                if cols["residual_installment_comm"]:
                    newrow["residual_installment_comm"] = safe_float(inst)
                if cols["installment_comm_sale"]:
                    newrow["installment_comm_sale"] = safe_float(sale_inst)
                if cols["carrier_statement_comm"]:
                    newrow["carrier_statement_comm"] = stmt_by_rep.get(rn, 0.0)
                comms.append(newrow)
        print(f"INFO new-engines applied org={org_id} period={period}: "
              f"plan_reps={len(plan_by_rep)} statement_reps={len(stmt_by_rep)} installment_reps={len(inst_by_rep)} "
              f"sale_installment_reps={len(sale_inst_by_rep)}")
        return comms
    except Exception as e:
        print(f"WARN new-engine wiring skipped (standard calc kept): {e}")
        return comms


async def _run_calculation(period: str, org_id: str, force: bool = False):
    """Background calculation task. force=True bypasses the zero-wipe guard."""
    client = sb()
    save_errors = []
    
    try:
        # Load all data
        def fetch(table, filters={}):
            # Org-scope EVERY read so a calc runs over ONLY the caller's tenant. Without this the engine
            # folded every tenant's raw sales/MI/payments/employees into the caller's snapshot (multi-tenant
            # leak). All tables fetched here carry org_id.
            q = client.schema('commcalc').table(table).select('*').eq('org_id', org_id)
            for k, v in filters.items():
                # A LIST filter value → .in_ so the read is period-spelling tolerant: the sweeps store
                # 'July 2026' while a manual /calculate passes '2026-07', and an exact .eq('period', …)
                # then loads ZERO rows and silently underpays. Callers pass _pvariants(period) for period.
                q = q.in_(k, v) if isinstance(v, (list, tuple, set)) else q.eq(k, v)
            try:
                r = q.limit(50000).execute()
                return r.data or []
            except: return []
        
        # Sales come from the ONE unified source (same as the Sales Report / targets): the OPEN month
        # reads the daily feed (the hourly-emailed Sales Transaction Details lands there; raw_sales lags/
        # isn't promoted), a closed month reads the authoritative raw_sales — each falling back to the
        # other, period-spelling agnostic. This is what makes CURRENT-month commissions calculate.
        def _fetch_sales_unified(_period):
            def _q(table):
                try:
                    return (client.schema('commcalc').table(table).select('*')
                            .eq('org_id', org_id).in_('period', _pvariants(_period))
                            .limit(200000).execute().data) or []
                except Exception:
                    return []
            _primary, _other = _open_month_source(client, org_id, _period)
            _rows = _q(_primary)
            return _rows if _rows else _q(_other)
        sales      = _fetch_sales_unified(period)
        # Period-spelling tolerant (_pvariants): the sweeps stamp 'July 2026' but a manual
        # /calculate/2026-07 passes '2026-07'; an exact .eq('period', …) loaded ZERO KPI/MI/pay rows
        # → empty kpi_values, flat 0.5 tier, boost_commission=None (silent underpay, 2026-07-14).
        pay_detail = fetch('raw_payment_detail', {'period': _pvariants(period)})
        mi_rows    = fetch('raw_mi', {'period': _pvariants(period)})
        dlar_rep   = fetch('raw_dlar_rep', {'period': _pvariants(period)})
        dlar_store = fetch('raw_dlar_store', {'period': _pvariants(period)})
        catalog    = fetch('raw_catalog')
        pay_cats   = fetch('payment_categories')
        cfg_rows   = fetch('payout_config', {'period': _pvariants(period)})
        store_map  = fetch('store_mapping')
        name_map   = fetch('name_map')
        shifts     = fetch('storeops_shifts') if False else []  # use storeops schema when migrated
        employees  = fetch('employees')
        stores     = fetch('stores')
        
        # payout_config is now period-spelling tolerant (.in_ above), so a month could return rows under
        # BOTH 'July 2026' and '2026-07'. cfg_rows[0] wins, but dedupe defensively: if both spellings have
        # a row, prefer the one stored under the sweep-canonical 'Month YYYY' spelling (what May/June + the
        # sweeps write) so the winner is deterministic regardless of row order.
        _cfg_canon = _canon_period(period)
        cfg = (next((r for r in cfg_rows if str(r.get('period', '')).strip() == _cfg_canon), None)
               or (cfg_rows[0] if cfg_rows else {}))
        # Thread the configurable accessory classification (mig 092) into the money path so commission
        # accessory pay uses the same department/category rules as the reports (default 'Ondigo').
        _acfg = _accessory_config(client, org_id)
        cfg = {**cfg, 'accessory_departments': _acfg['departments_list'],
               'accessory_categories': _acfg['categories_list'],
               'accessory_product_keywords': _acfg['products_list'],
               'acima_tenders': _acfg['acima_tenders_list']}

        # Resolve payment categories
        cat_map = {r['description'].strip(): r['category'] for r in pay_cats if r.get('description')}
        for r in pay_detail:
            pt = str(r.get('payment_type','')).strip()
            r['category'] = cat_map.get(pt, 'Unknown')
        
        valid = [r for r in sales if str(r.get('voided','')).upper().strip() != 'YES' and str(r.get('trans_type','')).strip() != 'Return']
        # (sales guard moved below the carrier gate — a plan-driven tenant may have no raw_sales)
        
        # Carrier gate: Boost tenants run the legacy verified engine; a tenant whose CHOSEN carrier
        # is explicitly non-Boost (e.g. Total / luxelink) skips the Boost tier/spiff math and is paid
        # ONLY from its configured Commission Plans + Payout Schedules (applied in _apply_new_engines).
        carrier_mode = _resolve_carrier_mode(fetch('carrier'))
        print(f"INFO calc org={org_id} period={period} carrier_mode={carrier_mode}")
        # Boost needs sale lines for its spiff/tier math → abort if none. A plan-driven (non-Boost) tenant
        # may legitimately have no raw_sales (paid from carrier statements / installments) → do NOT abort.
        if not sales and carrier_mode == 'boost':
            raise Exception(f"No sales data for {period}")

        # Run calculation
        result = calc_rep_commissions(
            sales=sales, pay_detail=pay_detail, dlar_rep=dlar_rep,
            dlar_store=dlar_store, mi_rows=mi_rows, catalog=catalog,
            cfg=cfg, store_mapping=store_map, shifts=shifts,
            employees=employees, stores=stores, period=period,
            name_map=name_map, carrier_mode=carrier_mode
        )
        
        # Save commissions
        comms = result['commissions']
        for row in comms:
            row['org_id'] = org_id
        # ADDITIVE: layer the new configurable engines (multi-month payout + commission plans) on top.
        # Boost-safe: with no schedule/plan configured this returns comms byte-identical (see helper).
        # Applied BEFORE the delete so the zero-wipe guard below inspects the FINAL rows while the
        # existing snapshot is still intact.
        comms = _apply_new_engines(client, org_id, period, comms, carrier_mode)

        # R1 UNCONFIGURED-TENANT REFUSAL (commission-0 §7b decision 7, doctrine): a non-Boost tenant with
        # REAL sales but NOTHING configured to pay from must not silently produce a $0 (or accidental)
        # snapshot — REFUSE loudly and preserve the last good snapshot (raise before the delete/insert),
        # with an actionable message the frontend turns into a link to /commcalc/commission-plans. This is
        # CONFIG-based (fires even with no prior paid snapshot), complementing the OUTCOME-based zero-wipe
        # guard below. Silenced by: force=true, or a tenant that DELIBERATELY runs no commissions
        # (commission_org_config.pay_disabled — the permission-gated override). Boost path untouched.
        _org_cfg = _commission_org_config(client, org_id)
        if (not force and carrier_mode != 'boost' and sales
                and not _org_cfg["pay_disabled"]
                and not _has_any_pay_source(client, org_id, period)):
            raise Exception(
                f"REFUSED to calculate {period}: this tenant is in PLAN mode with {len(sales)} sale line(s) "
                f"but has NO commission source configured (no Commission Plan assignments, no payout "
                f"schedules, no sale-triggered installment schedules, no carrier statement for the period). "
                f"Refusing to write an unconfigured snapshot and keeping the last good one. Configure pay at "
                f"/commcalc/commission-plans and assign reps, then recalculate. If this tenant intentionally "
                f"pays no commissions, enable 'pay disabled' in commission settings (needs permission); or "
                f"POST /calculate/{period}?force=true to override once.")

        # ZERO-WIPE GUARD (2026-07-14, owner-approved): in plan mode, an all-$0 result computed FROM
        # real sales must never replace a snapshot that currently pays someone. On 2026-07-13 a
        # transiently-defaulted non-Boost carrier flipped this org to plan mode with nothing configured
        # to pay from, and the 7am DLAR sweep silently zeroed every Boost rep's open month. Config-count
        # checks don't catch this (the house org holds 14 Total payout schedules that resolve to $0) —
        # only the OUTCOME does. Fail loudly into calc_status instead; ?force=true overwrites deliberately.
        if (not force and carrier_mode != 'boost' and sales and comms
                and all(safe_float(c.get('total_payout')) == 0 for c in comms)):
            try:
                prior_paid = (client.schema('commcalc').table('rep_commissions')
                              .select('org_id', count='exact').eq('org_id', org_id)
                              .in_('period', _pvariants(period)).neq('total_payout', 0)
                              .execute().count) or 0
            except Exception:
                prior_paid = 0
            if prior_paid:
                raise Exception(
                    f"REFUSED to overwrite {period}: plan-mode calc produced $0 for all {len(comms)} reps "
                    f"while {prior_paid} stored rows currently pay non-zero — the 2026-07-13 zero-wipe "
                    f"signature (non-Boost default carrier with nothing configured to pay from). Kept the "
                    f"existing snapshot. Fix the default carrier on the Carriers page or configure "
                    f"Commission Plan assignments, then recalculate; or POST /calculate/{period}"
                    f"?force=true to overwrite deliberately.")

        try:
            client.schema('commcalc').table('rep_commissions').delete().eq('org_id', org_id).in_('period', _pvariants(period)).execute()
            for i in range(0, len(comms), 500):
                client.schema('commcalc').table('rep_commissions').insert(comms[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f"commissions: {e}")
        
        # Compute and save flags
        try:
            pm = parse_period(period)
            # asset lookup (device model + reimbursement by IMEI) so a chargeback shows the REBATE LOST
            asset_by_imei = {}
            try:
                for a in fetch('asset_ledger'):
                    k = str(a.get('esn_imei') or '').replace('.0', '').strip().upper()
                    if k:
                        asset_by_imei[k] = a
            except Exception:
                pass
            flag_list = calc_flags(
                sales=valid,
                pay_detail=pay_detail,
                mi_rows=mi_rows,
                dlar_store=dlar_store,
                store_mapping=store_map,
                period=period,
                period_month=pm['month'],
                period_year=pm['year'],
                asset_by_imei=asset_by_imei,
            )
            # Add port-out / transfer-out / suspended flags from MI report
            try:
                po_flags = calc_portout_flags(mi_rows, valid, store_map, period, pm['month'], pm['year'])
                flag_list = (flag_list or []) + po_flags
            except Exception as pe:
                save_errors.append(f'portout: {pe}')

            client.schema('commcalc').table('flags').delete().eq('org_id', org_id).in_('period', _pvariants(period)).execute()
            if flag_list:
                for row in flag_list:
                    row['org_id'] = org_id
                for i in range(0, len(flag_list), 500):
                    client.schema('commcalc').table('flags').insert(flag_list[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f'flags: {e}')

        # SALE-TRIGGERED INSTALLMENT flags (mig 201 / doctrine §7b decision 2): a sold line whose paid
        # gate FAILED (line not active / not receiving residual) emits TWO flags — 'commission_rebate_tracking'
        # + 'employee_miss'. Written AFTER the full-period flags wipe above (so they survive) with the
        # delete-first-BY-SOURCE pattern (like the asset flag sync), so they're idempotent on recalc and
        # never touch the other flag sources. No-op for Boost (no schedules → engine returns no flags).
        try:
            si_flags = (sale_installment_engine.compute_sale_installments(client, org_id, period, persist=False)
                        .get('flags') or [])
            _INSTALLMENT_FLAG_SOURCES = ['commission_rebate_tracking', 'employee_miss']
            (client.schema('commcalc').table('flags').delete().eq('org_id', org_id)
             .in_('period', _pvariants(period)).in_('source', _INSTALLMENT_FLAG_SOURCES).execute())
            if si_flags:
                for row in si_flags:
                    row['org_id'] = org_id
                for i in range(0, len(si_flags), 500):
                    client.schema('commcalc').table('flags').insert(si_flags[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f'installment_flags: {e}')

        # ── Detect potential chargebacks per rep ─────────────────
        try:
            existing = client.schema('commcalc').table('chargeback_items').select('source,source_ref,deduct').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data or []
            decided = {(e['source'], e['source_ref']): e['deduct'] for e in existing}
            cb_items = []
            prem_rate = float(cfg.get('premium_flat') or 5)

            for s in sales:
                ct = str(s.get('contract_type') or '').lower()
                if 'ineligible' in ct:
                    ref = str(s.get('trans_id') or '').strip()
                    if not ref: continue
                    cb_items.append({
                        'org_id': org_id, 'period': period,
                        'epay_salesperson': str(s.get('salesperson') or '').strip(),
                        'store': str(s.get('store') or ''),
                        'source': 'ineligible', 'source_ref': ref,
                        'description': f"Ineligible activation: {s.get('contract_type','')}",
                        'amount': prem_rate,
                        'mdn': str(s.get('mdn') or ''), 'imei': str(s.get('serial_1') or ''),
                        'deduct': decided.get(('ineligible', ref), False),
                    })

            for p in pay_detail:
                if str(p.get('category') or '').strip() == 'Chargeback':
                    ref = f"{p.get('mdn','')}-{p.get('payment_type','')}-{p.get('amount','')}"
                    cb_items.append({
                        'org_id': org_id, 'period': period,
                        'epay_salesperson': str(p.get('rep_username') or '').strip(),
                        'store': str(p.get('business_address') or ''),
                        'source': 'epay_chargeback', 'source_ref': ref,
                        'description': f"EPay chargeback: {p.get('payment_type','')}",
                        'amount': abs(safe_float(p.get('amount'))),
                        'mdn': str(p.get('mdn') or ''), 'imei': str(p.get('imei') or ''),
                        'deduct': decided.get(('epay_chargeback', ref), False),
                    })

            for fl in (flag_list or []):
                rep = str(fl.get('epay_salesperson') or '').strip()
                if not rep: continue
                ref = f"{fl.get('flag_type','')}-{fl.get('imei','') or fl.get('mdn','')}"
                cb_items.append({
                    'org_id': org_id, 'period': period,
                    'epay_salesperson': rep, 'store': str(fl.get('store_address') or ''),
                    'source': 'flag', 'source_ref': ref,
                    'description': f"Flag: {fl.get('flag_type','')} - {str(fl.get('description',''))[:120]}",
                    'amount': abs(safe_float(fl.get('amount'))),
                    'mdn': str(fl.get('mdn') or ''), 'imei': str(fl.get('imei') or ''),
                    'deduct': decided.get(('flag', ref), False),
                })

            seen = set()
            unique_items = []
            for it in cb_items:
                k = (it['source'], it['source_ref'])
                if k in seen:
                    continue
                seen.add(k)
                unique_items.append(it)
            cb_items = unique_items
            # preserve manually-assigned chargebacks from the review bucket (recalc only manages
            # the auto-detected ones); otherwise an assigned VIP/fraud chargeback vanishes on recalc.
            (client.schema('commcalc').table('chargeback_items').delete()
             .eq('org_id', org_id).in_('period', _pvariants(period)).neq('source', 'chargeback_review').execute())
            if cb_items:
                for i in range(0, len(cb_items), 500):
                    client.schema('commcalc').table('chargeback_items').insert(cb_items[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f'chargebacks: {e}')

        # Update calc status — keyed on the canonical 'Month YYYY' spelling (see /calculate) so a month
        # has exactly ONE status row regardless of which spelling triggered the calc.
        client.schema('commcalc').table('calc_status').upsert({
            'org_id': org_id, 'period': _canon_period(period),
            'calc_status': 'done',
            'calc_finished_at': 'now()',
            'save_errors': save_errors or None,
        }, on_conflict='org_id,period').execute()

    except Exception as e:
        try:
            client.schema('commcalc').table('calc_status').upsert({
                'org_id': org_id, 'period': _canon_period(period),
                'calc_status': 'error',
                'save_errors': [str(e)],
            }, on_conflict='org_id,period').execute()
        except: pass


# ── Report endpoints ──────────────────────────────────────────
def _caller_rep_keys(authorization: str, org_id: str):
    """For a SELF-scoped (rep) caller, the UPPER name keys identifying THEIR OWN rep rows in rep_commissions
    — the caller's storeops employee name (app_user.employee_id → employees.name) PLUS any epay/alias that
    canonicalizes to it (name_map / rep_aliases). Returns:
      • None       — not self-scoped (admin / unrestricted / a real manager span → scope_keyset governs).
      • {keys}     — a self rep → keep ONLY the rows matching these keys.
      • set()       — a self rep we could NOT map to a rep → the caller sees NOTHING (never another rep's
                      row; the KPI page shows an empty state + hint, not other people's pay).
    Same identity path My Targets uses (app_user → employee → rep name). Reads app_users/employees
    (public schema) READ-ONLY, org-scoped. Never raises."""
    try:
        from app.modules.storeops.router import _rbac_enabled, _role_scope
        from app.modules.core.router import _uid_from_token
    except Exception:
        return None
    try:
        if not _rbac_enabled(org_id):
            return None
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        urows = (sb().table("app_users").select("role,employee_id")
                 .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
        if not urows:
            return None
        u = urows[0]
        if _role_scope(org_id, (u.get("role") or "").strip()) != "self":
            return None
        name = ""
        eid = (u.get("employee_id") or "").strip()
        if eid:
            try:
                emp = (sb().table("employees").select("name")
                       .eq("org_id", org_id).eq("employee_id", eid).limit(1).execute().data) or []
                if emp:
                    name = str(emp[0].get("name") or "").strip()
            except Exception:
                name = ""
        keys = set()
        if name:
            keys.add(name.upper())
            try:
                cmap = _rep_canon_map(sb(), org_id)
                for alias, canon in cmap.items():
                    if str(canon).strip().upper() == name.upper():
                        keys.add(str(alias).strip().upper())
            except Exception:
                pass
        return keys   # empty set = self rep with no resolvable rep → sees nothing
    except Exception:
        return None


@router.get("/commissions/{period}")
async def get_commissions(period: str, authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('rep_commissions').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).order('total_payout', desc=True).execute()
    comms = r.data or []
    # RULE FIVE (§3d): stamp each rep row with its `market` (store_mapping, same resolver the Sales Report
    # uses) so the Rep Commission report's standard filter bar can market-filter client-side. Additive +
    # org-scoped; the pay numbers are untouched (read-only enrichment).
    _resolve_market, _ = _store_market_resolver(client, org_id)
    for cr in comms:
        cr['market'] = _resolve_market(cr.get('store'))
    # Apply chargeback deductions (deduct=true) per rep
    cb = client.schema('commcalc').table('chargeback_items').select('epay_salesperson,amount,deduct').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data or []
    ded_by_rep = {}
    for item in cb:
        if item.get('deduct'):
            rep = item.get('epay_salesperson') or ''
            # safe_float: a manually-assigned chargeback can store amount as a string
            # ("25.00"), and 0 + "25.00" raised TypeError → the whole endpoint 500'd for that rep.
            ded_by_rep[rep] = ded_by_rep.get(rep, 0) + safe_float(item.get('amount'))
    for cr in comms:
        rep = cr.get('epay_salesperson') or ''
        d = ded_by_rep.get(rep, 0)
        cr['chargeback_deduction'] = d
        cr['final_payout'] = safe_float(cr.get('total_payout')) - safe_float(d)
    from app.modules.storeops.router import scope_keyset, in_keyset
    # SELF scope (B2): an employee sees ONLY their OWN rep row(s) — their own KPIs/commission, never a
    # coworker's pay. scope_keyset returns an empty set for a self rep (would hide everything); instead we
    # match the caller's own rep identity by name (canon-aware). A self rep we can't map sees nothing.
    rep_keys = _caller_rep_keys(authorization, org_id)
    if rep_keys is not None:
        cmap = _rep_canon_map(client, org_id)
        def _mine(c):
            cand = set()
            for f in ('storeops_name', 'epay_salesperson', 'salesperson'):
                v = str(c.get(f) or '').strip()
                if v:
                    cand.add(v.upper())
                    cand.add(str(_canon(v, cmap)).strip().upper())
            return bool(cand & rep_keys)
        return [c for c in comms if _mine(c)]
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / rbac off)
    return [c for c in comms if in_keyset(ks, c.get('store'), c.get('store_code'))]

@router.get("/dlar-store/{period}")
async def get_dlar_store_kpis(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Store-level KPIs straight from the Elevate Go Store DLAR (raw_dlar_store) for the
    Store view of the KPI Metrics page. Values are whole-number percents (e.g. 55.0).

    The sweep/upload store the period as 'June 2026', but the page may pass '2026-06' — so we
    match on period_month/period_year (parsed from either spelling) rather than the raw string,
    which previously returned [] on a format mismatch."""
    require_org(org_id)
    mo, yr = _month_year(period)
    q = sb().schema('commcalc').table('raw_dlar_store').select(
        'location,address,store_code,atu,protect_pct,byod_pct,family_plan_pct,tmr3,'
        'aal_conversion,conversion_rate,total_acts,gross_adds,total_upgrades'
    ).eq('org_id', org_id)
    q = q.eq('period_month', mo).eq('period_year', yr) if mo and yr else q.in_('period', _pvariants(period))
    rows = q.order('location').execute().data or []
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / rbac off)
    return [r for r in rows if in_keyset(ks, r.get('store_code'), r.get('address'), r.get('location'))]


@router.get("/flags/{period}")
async def get_flags(period: str, authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('flags').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).order('severity').execute()
    rows = r.data or []
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / rbac off)
    return [f for f in rows if in_keyset(ks, f.get('store_address'), f.get('store_code'))]

@router.get("/config/{period}")
async def get_config(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('payout_config').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).limit(1).execute()
    if r.data: return r.data[0]
    return {}

@router.put("/config/{period}")
async def save_config(period: str, config: dict, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    config.update({'period': period, 'org_id': org_id})
    r = client.schema('commcalc').table('payout_config').upsert(config, on_conflict='org_id,period').execute()
    return r.data[0] if r.data else config


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# COMMISSION-0 doctrine (mig 201): sale-triggered multi-month rep-pay under Commission Plans, the
# per-tenant commission posture (R1 override + residual visibility), and the classification-first
# MRC-mapping flow. All degrade gracefully to a code default before mig 201 runs.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _require_commission_admin(authorization, org_id):
    """Money-posture writes (pay_disabled, residual_visibility) are admin-only. Degrades OPEN when the
    caller can't be resolved (RBAC off / house admin) so it never locks out the house org."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        caller = _resolve_caller(sb(), uid) if uid else None
        if caller is None:
            return  # unresolved (no token / rbac off) — allow, same posture as the rest of the module
        if caller.get("super_admin"):
            return
        perms = caller.get("perms") or {}
        if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
            return
        raise HTTPException(403, "Only an administrator may change commission pay posture for this tenant.")
    except HTTPException:
        raise
    except Exception:
        return  # never 500 a settings save on a resolution error


@router.get("/commission-settings")
async def get_commission_settings(org_id: str = ORG_ID):
    """Per-tenant commission posture (mig 201): pay_disabled (the R1 refuse-to-pay override) +
    residual_visibility ('all' | 'permissioned'). Code default before mig 201 runs."""
    require_org(org_id)
    return _commission_org_config(sb(), org_id)


@router.put("/commission-settings")
async def put_commission_settings(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Admin-only. Sets pay_disabled and/or residual_visibility for the tenant. pay_disabled=true means
    'this tenant INTENTIONALLY pays no commissions' → silences the R1 unconfigured-tenant refusal."""
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    row = {"org_id": org_id, "updated_at": _datetime.now(_timezone.utc).isoformat()}
    if "pay_disabled" in body:
        row["pay_disabled"] = bool(body.get("pay_disabled"))
    if "residual_visibility" in body:
        rv = (body.get("residual_visibility") or "all").strip().lower()
        row["residual_visibility"] = rv if rv in ("all", "permissioned") else "all"
    try:
        client = sb()
        client.schema('commcalc').table('commission_org_config').upsert(row, on_conflict='org_id').execute()
    except Exception as e:
        raise HTTPException(500, f"could not save commission settings (is migration 201 applied?): {e}")
    return _commission_org_config(sb(), org_id)


# ── installment-schedule EDIT helpers (mig 210): shared create/update writer + audit trail ──────────
def _caller_uid(authorization):
    """The signed-in auth uid (for updated_by / audit changed_by), or 'web' when unresolved. Never raises."""
    try:
        from app.modules.core.router import _uid_from_token
        return _uid_from_token(authorization) or 'web'
    except Exception:
        return 'web'


def _installment_snapshot(client, org_id, sid):
    """Schedule header + its month lines as a plain dict — the audit before/after payload. Best-effort:
    returns None if the schedule is gone or the tables are absent (pre-mig-201)."""
    if not sid:
        return None
    try:
        sc = (client.schema('commcalc').table('plan_installment_schedule').select('*')
              .eq('org_id', org_id).eq('id', sid).limit(1).execute().data) or []
        if not sc:
            return None
        ln = (client.schema('commcalc').table('plan_installment_line').select('*')
              .eq('org_id', org_id).eq('schedule_id', sid).execute().data) or []
        head = dict(sc[0]); head['lines'] = sorted(ln, key=lambda x: x.get('month_index') or 0)
        return head
    except Exception:
        return None


def _installment_audit(client, org_id, sid, action, before, after, changed_by):
    """Write ONE edit-trail row (mig 210 commcalc.plan_installment_schedule_audit). Best-effort — a
    missing audit table must NEVER block the money-config save (degrades to a no-op pre-mig-210)."""
    try:
        client.schema('commcalc').table('plan_installment_schedule_audit').insert({
            'org_id': org_id, 'schedule_id': sid, 'action': action,
            'changed_by': changed_by or 'web', 'before_json': before, 'after_json': after,
        }).execute()
    except Exception:
        pass


def _installment_head(body, org_id):
    """The plan_installment_schedule header dict from a request body (create OR update). m1_gate (mig 210)
    composes with gate_mode/gate_from_month: 'inherit' = today's behaviour; 'activation_payment' gates
    MONTH 1 on the sale's own activation payment (months 2..N keep gate_mode)."""
    m1g = (str(body.get("m1_gate") or "inherit").strip().lower())
    return {
        "org_id": org_id, "plan_id": body["plan_id"],
        "name": (body.get("name") or None),
        "num_months": max(1, min(12, int(body.get("num_months") or 1))),
        "trigger_match_field": (body.get("trigger_match_field") or "any").strip() or "any",
        "trigger_match_op": (body.get("trigger_match_op") or "equals").strip() or "equals",
        "trigger_match_value": body.get("trigger_match_value"),
        "gate_mode": (body.get("gate_mode") or "paid_residual").strip() or "paid_residual",
        "gate_from_month": max(1, int(body.get("gate_from_month") or 1)),
        "m1_gate": m1g if m1g in ("inherit", "activation_payment") else "inherit",
        "clawback_enabled": bool(body.get("clawback_enabled", False)),
        "effective_from": (body.get("effective_from") or None),
        "effective_to": (body.get("effective_to") or None),
        "eligible_sale_periods": [str(pp).strip() for pp in (body.get("eligible_sale_periods") or []) if str(pp).strip()],
        "is_active": bool(body.get("is_active", True)),
        "notes": body.get("notes"),
    }


def _installment_lines(body, sid, org_id):
    return [{
        "org_id": org_id, "schedule_id": sid,
        "month_index": max(1, int(ln.get("month_index") or 1)),
        "payout_kind": (ln.get("payout_kind") or "flat").strip() or "flat",
        "flat_amount": safe_float(ln.get("flat_amount")),
        "mrc_pct": safe_float(ln.get("mrc_pct")),
        "mrc_source": (ln.get("mrc_source") or "product_catalog").strip() or "product_catalog",
    } for ln in (body.get("lines") or [])]


def _write_installment_schedule(client, org_id, body, sid, changed_by):
    """Shared create/update writer (mig 201 + mig 210). sid None → INSERT; else UPDATE that id. Replaces
    the month lines (delete-then-insert). Stamps updated_by/updated_at (mig 210) and records a before/after
    audit row. m1_gate/updated_by degrade if mig 210 isn't applied — retried without them so a save on a
    mig-201-only DB still works. Does NOT recompute pay (that waits for POST /calculate)."""
    action = "update" if sid else "create"
    before = _installment_snapshot(client, org_id, sid) if sid else None
    head = _installment_head(body, org_id)
    head["updated_at"] = _datetime.now(_timezone.utc).isoformat()
    head["updated_by"] = changed_by
    tbl = client.schema('commcalc').table('plan_installment_schedule')

    def _do(head_dict):
        if sid:
            tbl.update(head_dict).eq('id', sid).eq('org_id', org_id).execute()
            return sid
        r = tbl.insert(head_dict).execute()
        return (r.data or [{}])[0].get('id')

    try:
        new_sid = _do(head)
    except Exception:
        # mig 210 not applied → m1_gate / updated_by columns absent. Retry mig-201-compatible.
        h2 = {k: v for k, v in head.items() if k not in ("m1_gate", "updated_by")}
        new_sid = _do(h2)
    if not new_sid:
        raise HTTPException(500, "could not save installment schedule header")

    client.schema('commcalc').table('plan_installment_line').delete().eq('org_id', org_id).eq('schedule_id', new_sid).execute()
    lines = _installment_lines(body, new_sid, org_id)
    if lines:
        client.schema('commcalc').table('plan_installment_line').insert(lines).execute()

    after = _installment_snapshot(client, org_id, new_sid)
    _installment_audit(client, org_id, new_sid, action, before, after, changed_by)
    return new_sid


# ── SALE-TRIGGERED installment SCHEDULES (mig 201) — attach to a Commission Plan, triggered by the sale line
@router.get("/plan-installments")
async def list_plan_installments(org_id: str = ORG_ID):
    """All sale-triggered installment schedules + their month lines, grouped by plan. [] (not 500) if
    migration 201 isn't applied yet."""
    require_org(org_id)
    client = sb()
    try:
        scheds = (client.schema('commcalc').table('plan_installment_schedule').select('*')
                  .eq('org_id', org_id).execute().data) or []
        lines = (client.schema('commcalc').table('plan_installment_line').select('*')
                 .eq('org_id', org_id).execute().data) or []
    except Exception:
        return {"schedules": [], "ready": False, "note": "Run migration 201_commission_sale_installments.sql to enable."}
    by_sched = {}
    for ln in lines:
        by_sched.setdefault(ln.get('schedule_id'), []).append(ln)
    for s in scheds:
        s['lines'] = sorted(by_sched.get(s['id'], []), key=lambda x: x.get('month_index') or 0)
    return {"schedules": scheds, "ready": True}


@router.post("/plan-installments")
async def save_plan_installment(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """CREATE a sale-triggered schedule + its month lines (money config → admin-only). Body:
    {id?, plan_id (required), name?, num_months, trigger_match_field?, trigger_match_op?, trigger_match_value?,
     gate_mode?, gate_from_month?, m1_gate?('inherit'|'activation_payment'), clawback_enabled?, effective_from?,
     effective_to?, eligible_sale_periods?[], is_active?, lines:[{month_index, payout_kind, flat_amount?,
     mrc_pct?, mrc_source?}]}. Replaces the lines (delete-then-insert). If `id` is present this UPDATES that
     schedule (same path as PUT) for backward compatibility. Does NOT recompute pay (waits for POST /calculate)."""
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    if not body.get("plan_id"):
        raise HTTPException(400, "plan_id is required (a sale-triggered schedule attaches to a Commission Plan).")
    client = sb()
    try:
        sid = _write_installment_schedule(client, org_id, body, body.get("id") or None, _caller_uid(authorization))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"could not save installment schedule (is migration 201 applied?): {e}")
    return {"id": sid, "saved": True}


@router.put("/plan-installments/{sid}")
async def update_plan_installment(sid: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """EDIT an existing sale-triggered schedule + its month lines (money config → admin-only). Same body
    shape as POST (minus id). RECOMPUTE SEMANTICS: the edit takes effect from the NEXT POST /calculate
    onward — it does NOT retroactively rewrite pay. sale_installment_ledger rows already written for PAST
    pay periods are IMMUTABLE unless the operator explicitly re-runs POST /calculate for that period (and
    even then, a paid month only re-derives from the edited schedule if that period is recomputed). Every
    edit is captured in commcalc.plan_installment_schedule_audit (before/after + who/when)."""
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    if not body.get("plan_id"):
        raise HTTPException(400, "plan_id is required.")
    client = sb()
    exists = _installment_snapshot(client, org_id, sid)
    if exists is None:
        raise HTTPException(404, "installment schedule not found for this tenant (or migration 201 not applied).")
    try:
        _write_installment_schedule(client, org_id, body, sid, _caller_uid(authorization))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"could not update installment schedule: {e}")
    return {"id": sid, "saved": True, "updated": True}


@router.delete("/plan-installments/{sid}")
async def delete_plan_installment(sid: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    client = sb()
    before = _installment_snapshot(client, org_id, sid)
    try:
        client.schema('commcalc').table('plan_installment_schedule').delete().eq('id', sid).eq('org_id', org_id).execute()
    except Exception as e:
        raise HTTPException(500, f"delete failed: {e}")
    _installment_audit(client, org_id, sid, "delete", before, None, _caller_uid(authorization))
    return {"deleted": True}


@router.get("/plan-installments/{sid}/audit")
async def plan_installment_audit(sid: str, org_id: str = ORG_ID):
    """Edit trail for one schedule (mig 210). [] if the audit table isn't applied yet."""
    require_org(org_id)
    try:
        rows = (sb().schema('commcalc').table('plan_installment_schedule_audit').select('*')
                .eq('org_id', org_id).eq('schedule_id', sid).order('changed_at', desc=True).limit(200).execute().data) or []
    except Exception:
        return {"audit": [], "ready": False}
    return {"audit": rows, "ready": True}


@router.get("/plan-installments/preview/{period}")
async def preview_plan_installments(period: str, org_id: str = ORG_ID):
    """READ-ONLY preview of what the sale-triggered installment engine WOULD pay for a pay period,
    incl. the paid-gate outcome per line and the two-flag list for sold-but-unpaid lines. Writes nothing."""
    require_org(org_id)
    try:
        return sale_installment_engine.compute_sale_installments(sb(), org_id, period, persist=False)
    except Exception as e:
        raise HTTPException(500, f"installment preview failed: {type(e).__name__}: {e}")


# ── ACTIVATION-PAYMENT MATCHER (mig 210): what counts as "payment received at activation" (per-tenant) ─
@router.get("/plan-installments/activation-matcher")
async def get_activation_matcher(period: str = "", org_id: str = ORG_ID):
    """The tenant's 'payment received at activation' matcher used by the month-1 'activation_payment' gate,
    PLUS the distinct raw_sales departments/categories present (pick-don't-type editor). Falls back to the
    engine's seeded default when unset (is_default=true). Read-only."""
    require_org(org_id)
    from app.modules.commcalc.sale_installment_engine import DEFAULT_ACTIVATION_PAYMENT_MATCHER
    from collections import Counter
    client = sb()
    stored, ready = None, True
    try:
        rows = (client.schema('commcalc').table('commission_org_config')
                .select('activation_payment_matcher').eq('org_id', org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get('activation_payment_matcher')
    except Exception:
        ready = False
    eff = stored or DEFAULT_ACTIVATION_PAYMENT_MATCHER
    matcher = {
        "departments": [str(x) for x in (eff.get("departments") or [])],
        "categories": [str(x) for x in (eff.get("categories") or [])],
        "product_keywords": [str(x) for x in (eff.get("product_keywords") or [])],
        "value_field": (eff.get("value_field") or "ext_price"),
        "min_amount": safe_float(eff.get("min_amount")) if eff.get("min_amount") is not None else 0.01,
    }
    depts, cats = [], []
    try:
        q = client.schema('commcalc').table('raw_sales').select('department,category').eq('org_id', org_id)
        if period:
            q = q.in_('period', _pvariants(period))
        rs = q.limit(50000).execute().data or []
        depts = [d for d, _ in Counter(str(r.get('department') or '').strip() for r in rs if str(r.get('department') or '').strip()).most_common()]
        cats = [c for c, _ in Counter(str(r.get('category') or '').strip() for r in rs if str(r.get('category') or '').strip()).most_common()]
    except Exception:
        pass
    return {"matcher": matcher, "is_default": stored is None, "ready": ready,
            "value_fields": ["ext_price", "gp"], "departments": depts, "categories": cats}


@router.put("/plan-installments/activation-matcher")
async def put_activation_matcher(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Admin-only. Save the tenant's activation-payment matcher (mig 210). body: {departments[], categories[],
    product_keywords[], value_field('ext_price'|'gp'), min_amount} — OR {reset:true} to revert to the engine
    default (stored NULL). 500 with a 'run migration 210' hint if the column is absent."""
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    client = sb()
    if body.get("reset"):
        matcher = None
    else:
        vf = str(body.get("value_field") or "ext_price").strip().lower()
        matcher = {
            "departments": [str(x).strip() for x in (body.get("departments") or []) if str(x).strip()],
            "categories": [str(x).strip() for x in (body.get("categories") or []) if str(x).strip()],
            "product_keywords": [str(x).strip() for x in (body.get("product_keywords") or []) if str(x).strip()],
            "value_field": vf if vf in ("ext_price", "gp") else "ext_price",
            "min_amount": safe_float(body.get("min_amount")) if body.get("min_amount") is not None else 0.01,
        }
    row = {"org_id": org_id, "activation_payment_matcher": matcher,
           "updated_by": _caller_uid(authorization), "updated_at": _datetime.now(_timezone.utc).isoformat()}
    try:
        client.schema('commcalc').table('commission_org_config').upsert(row, on_conflict='org_id').execute()
    except Exception as e:
        raise HTTPException(500, f"could not save activation-payment matcher (is migration 210 applied?): {e}")
    return {"saved": True, "is_default": matcher is None}


# ── Classification-first MRC MAPPING (§7b decision 1): classify imported plan lines + prefill $ MRC ──
@router.get("/mrc-mapping/candidates")
async def mrc_mapping_candidates(period: str, org_id: str = ORG_ID):
    """Scan a period's sale lines for distinct PLAN/product descriptions, AUTO-CLASSIFY each (reusing the
    existing accessory/carrier-category config), AUTO-PREFILL the $ MRC from the description text, and
    cross-reference the product_mrc catalog to show which are already CONFIRMED. Read-only — the user
    confirms/overwrites via POST /mrc-mapping/confirm. This is the classification-first flow."""
    require_org(org_id)
    client = sb()
    acc = sale_installment_engine._acc_sets(client, org_id)
    ccmap = sale_installment_engine._load_ccmap(client, org_id)
    catalog = installment_engine._load_product_mrc(client, org_id)
    # existing confirmed mappings keyed by lowered plan pattern
    confirmed = {}
    for r in catalog:
        confirmed[str(r.get('plan_pattern') or '').strip().lower()] = {
            "mrc": safe_float(r.get('mrc')), "confirmed": bool(r.get('confirmed')),
            "classification": r.get('classification')}
    sales = commission_engine._read_sales(client, org_id, period)
    seen = {}
    for row in sales:
        if str(row.get('voided', '') or '').upper().strip() == 'YES':
            continue
        key = str(row.get('customer_plan') or row.get('product_desc') or '').strip()
        if not key:
            continue
        lk = key.lower()
        e = seen.get(lk)
        if not e:
            e = seen[lk] = {"plan": key, "count": 0,
                            "classification": sale_installment_engine.classify_line(row, acc, ccmap),
                            "prefill_mrc": sale_installment_engine.extract_mrc_from_desc(
                                row.get('product_desc') or row.get('customer_plan')),
                            "confirmed": False, "confirmed_mrc": None}
        e["count"] += 1
        c = confirmed.get(lk)
        if c:
            e["confirmed"] = c["confirmed"]
            e["confirmed_mrc"] = c["mrc"]
            if c.get("classification"):
                e["classification"] = c["classification"]
    out = sorted(seen.values(), key=lambda x: (x["confirmed"], -x["count"]))
    return {"period": period, "candidates": out, "count": len(out)}


@router.post("/mrc-mapping/confirm")
async def mrc_mapping_confirm(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Upsert user-confirmed product_mrc rows (money config → admin-only). Body: {carrier_id?, match_op?,
    items:[{plan, mrc, classification?}]}. Marks each row confirmed=true. Reuses the existing product_mrc
    table (mig 074) — never a new mapping table."""
    require_org(org_id)
    _require_commission_admin(authorization, org_id)
    client = sb()
    carrier_id = body.get("carrier_id") or None
    match_op = (body.get("match_op") or "equals").strip() or "equals"
    saved = 0
    for it in (body.get("items") or []):
        plan = str(it.get("plan") or "").strip()
        if not plan:
            continue
        row = {"org_id": org_id, "carrier_id": carrier_id, "plan_pattern": plan, "match_op": match_op,
               "mrc": safe_float(it.get("mrc")), "is_active": True, "confirmed": True,
               "classification": it.get("classification"), "source_desc": it.get("source_desc"),
               "prefill_mrc": (safe_float(it.get("prefill_mrc")) if it.get("prefill_mrc") is not None else None)}
        try:
            client.schema('commcalc').table('product_mrc').upsert(
                row, on_conflict='org_id,carrier_id,plan_pattern,match_op').execute()
            saved += 1
        except Exception:
            # pre-mig-201 (no confirmed/classification cols): retry without them so the MRC still saves
            for k in ("confirmed", "classification", "source_desc", "prefill_mrc"):
                row.pop(k, None)
            try:
                client.schema('commcalc').table('product_mrc').upsert(
                    row, on_conflict='org_id,carrier_id,plan_pattern,match_op').execute()
                saved += 1
            except Exception:
                pass
    return {"saved": saved}


# ── Multi-month payout SCHEDULES (migration 057) — generic per-carrier installment payouts ────────
# A schedule spreads one activation's commission over N months (flat or %MRC), with months 2..N gated
# on the bill being paid + residual received that month. With no schedule, payouts are single-month
# (unchanged). The engine is READ-ONLY/PREVIEW here — not yet summed into the live calc (see HANDOFF).

@router.get("/payout-schedule")
async def list_payout_schedules(org_id: str = ORG_ID):
    """All schedules + their installment lines. [] (not 500) if migration 057 isn't applied yet."""
    client = sb()
    try:
        scheds = client.schema('commcalc').table('payout_schedule').select('*').eq('org_id', org_id).execute().data or []
        lines = client.schema('commcalc').table('payout_schedule_line').select('*').eq('org_id', org_id).execute().data or []
    except Exception:
        return {"schedules": [], "ready": False, "note": "Run migration 057_multi_month_payout.sql to enable."}
    by_sched = {}
    for ln in lines:
        by_sched.setdefault(ln.get('schedule_id'), []).append(ln)
    for s in scheds:
        s['lines'] = sorted(by_sched.get(s['id'], []), key=lambda x: x.get('month_index') or 0)
    return {"schedules": scheds, "ready": True}


@router.post("/payout-schedule")
async def save_payout_schedule(body: dict, org_id: str = ORG_ID):
    """Create/replace a schedule + its lines. Body: {id?, company_id?, carrier_id?, activation_type?,
    num_months, gate_signal?, bypass_tier?, is_active?, lines:[{month_index, payout_kind, flat_amount?,
    mrc_pct?, mrc_basis?, requires_paid}]}. Replaces the lines for the schedule (delete-then-insert)."""
    client = sb()
    head = {
        "org_id": org_id,
        "company_id": body.get("company_id") or None,
        "carrier_id": body.get("carrier_id") or None,
        "activation_type": (body.get("activation_type") or "*").strip() or "*",
        "num_months": int(body.get("num_months") or 1),
        "gate_signal": body.get("gate_signal") or "paid_residual",
        "bypass_tier": bool(body.get("bypass_tier", True)),
        "is_active": bool(body.get("is_active", True)),
    }
    try:
        if body.get("id"):
            client.schema('commcalc').table('payout_schedule').update(head).eq('id', body['id']).eq('org_id', org_id).execute()
            sid = body['id']
        else:
            r = client.schema('commcalc').table('payout_schedule').upsert(
                head, on_conflict='org_id,company_id,carrier_id,activation_type').execute()
            sid = (r.data or [{}])[0].get('id')
        if not sid:
            raise HTTPException(500, "could not save schedule header")
        client.schema('commcalc').table('payout_schedule_line').delete().eq('org_id', org_id).eq('schedule_id', sid).execute()
        lines = []
        for ln in (body.get("lines") or []):
            lines.append({
                "org_id": org_id, "schedule_id": sid,
                "month_index": int(ln.get("month_index") or 1),
                "payout_kind": ln.get("payout_kind") or "flat",
                "flat_amount": safe_float(ln.get("flat_amount")),
                "mrc_pct": safe_float(ln.get("mrc_pct")),
                "mrc_basis": ln.get("mrc_basis") or "commissionable_mrc",
                "requires_paid": bool(ln.get("requires_paid")),
            })
        if lines:
            client.schema('commcalc').table('payout_schedule_line').insert(lines).execute()
        return {"id": sid, "lines": len(lines)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"save payout-schedule failed (is migration 057 applied?): {e}")


@router.delete("/payout-schedule/{schedule_id}")
async def delete_payout_schedule(schedule_id: str, org_id: str = ORG_ID):
    client = sb()
    client.schema('commcalc').table('payout_schedule_line').delete().eq('org_id', org_id).eq('schedule_id', schedule_id).execute()
    client.schema('commcalc').table('payout_schedule').delete().eq('org_id', org_id).eq('id', schedule_id).execute()
    return {"deleted": schedule_id}


@router.get("/payout-schedule/preview")
async def preview_payout_installments(period: str, org_id: str = ORG_ID):
    """READ-ONLY preview: what the configured schedules WOULD pay for `period` (per rep + per-subscriber
    ledger), computed from raw_mi. Writes nothing and does NOT affect the live commission calc — it's
    the safe way to validate a schedule before wiring installments into the payout."""
    require_org(org_id)
    try:
        return installment_engine.compute_installments(sb(), org_id, period, persist=False)
    except Exception as e:
        raise HTTPException(500, f"payout preview failed: {type(e).__name__}: {e}")


# ── Per-product MRC catalog (migration 074) — resolves the MRC for %-of-MRC installments when a carrier's
# statement carries no per-subscriber MRC (e.g. Total Wireless). Keyed on raw_mi.customer_plan. Additive:
# with no rows, the installment engine reads the raw_mi MRC column exactly as before (Boost unaffected).
@router.get("/product-mrc")
async def list_product_mrc(org_id: str = ORG_ID):
    """All catalog entries (specific-first). [] (not 500) if migration 074 isn't applied yet."""
    client = sb()
    try:
        rows = (client.schema('commcalc').table('product_mrc').select('*')
                .eq('org_id', org_id).order('priority').execute().data) or []
    except Exception:
        return {"items": [], "ready": False, "note": "Run migration 074_product_mrc.sql to enable."}
    return {"items": rows, "ready": True}


@router.post("/product-mrc")
async def save_product_mrc(body: dict, org_id: str = ORG_ID):
    """Create/update one catalog entry. Body: {id?, carrier_id?, plan_pattern, match_op?, mrc, priority?,
    is_active?, note?}. plan_pattern is matched (case-insensitive) against raw_mi.customer_plan."""
    client = sb()
    plan = (body.get("plan_pattern") or "").strip()
    if not plan:
        raise HTTPException(400, "plan_pattern is required")
    op = (body.get("match_op") or "equals").strip()
    if op not in ("equals", "contains"):
        raise HTTPException(400, "match_op must be 'equals' or 'contains'")
    rec = {
        "org_id": org_id,
        "carrier_id": body.get("carrier_id") or None,
        "plan_pattern": plan,
        "match_op": op,
        "mrc": safe_float(body.get("mrc")),
        "priority": int(body.get("priority") or 100),
        "is_active": bool(body.get("is_active", True)),
        "note": (body.get("note") or "").strip() or None,
    }
    try:
        if body.get("id"):
            client.schema('commcalc').table('product_mrc').update(rec).eq('id', body['id']).eq('org_id', org_id).execute()
            return {"id": body["id"]}
        r = client.schema('commcalc').table('product_mrc').insert(rec).execute()
        return {"id": (r.data or [{}])[0].get("id")}
    except Exception as e:
        raise HTTPException(500, f"save product-mrc failed (is migration 074 applied?): {e}")


@router.delete("/product-mrc/{item_id}")
async def delete_product_mrc(item_id: str, org_id: str = ORG_ID):
    sb().schema('commcalc').table('product_mrc').delete().eq('id', item_id).eq('org_id', org_id).execute()
    return {"deleted": item_id}


def _detect_mrc_columns(headers):
    """Best-guess which uploaded columns hold the plan name and its monthly price."""
    plan_col = mrc_col = ""
    for h in headers:
        hl = str(h).lower()
        if not plan_col and re.search(r"plan|product|offer|description|name", hl):
            plan_col = h
    for h in headers:
        hl = str(h).lower()
        if h != plan_col and not mrc_col and re.search(r"mrc|price|rate|monthly|charge|amount|cost", hl):
            mrc_col = h
    return plan_col, mrc_col


@router.post("/product-mrc/import")
async def import_product_mrc(
    file: UploadFile = File(...),
    carrier_id: str = Form(""),
    plan_col: str = Form(""),
    mrc_col: str = Form(""),
    match_op: str = Form("equals"),
    dry_run: bool = Form(False),
    org_id: str = ORG_ID,
):
    """Bulk-load the per-product MRC catalog from a carrier price sheet (Excel/CSV) so nobody has
    to remember/type every plan name. dry_run=true returns the headers, the auto-detected plan/MRC
    columns and a parsed preview — the UI lets the user re-pick columns from dropdowns, then
    commits. Existing entries (same carrier + plan + match) are UPDATED, new ones inserted."""
    require_org(org_id)
    if match_op not in ("equals", "contains"):
        raise HTTPException(400, "match_op must be 'equals' or 'contains'")
    contents = await file.read()
    try:
        df = _read_upload_df(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    headers = [str(h).strip() for h in df.columns if str(h).strip()]
    auto_plan, auto_mrc = _detect_mrc_columns(headers)
    use_plan = (plan_col or "").strip() or auto_plan
    use_mrc = (mrc_col or "").strip() or auto_mrc

    parsed, skipped = [], 0
    if use_plan and use_mrc and use_plan in headers and use_mrc in headers:
        seen = set()
        for r in df.to_dict("records"):
            plan = str(r.get(use_plan) or "").strip()
            mrc = safe_float(re.sub(r"[$,\s]", "", str(r.get(use_mrc) or "")))
            if not plan or plan.lower() in seen or mrc <= 0:
                skipped += 1
                continue
            seen.add(plan.lower())
            parsed.append({"plan": plan, "mrc": round(mrc, 2)})

    if dry_run:
        return {"headers": headers, "plan_col": use_plan, "mrc_col": use_mrc,
                "rows": parsed[:15], "total": len(parsed), "skipped": skipped,
                "note": None if (use_plan and use_mrc) else
                "Could not auto-detect the plan/price columns — pick them from the dropdowns."}
    if not parsed:
        raise HTTPException(400, "No usable rows — pick the plan and MRC columns (dry_run shows the headers).")

    client = sb()
    try:
        existing = (client.schema('commcalc').table('product_mrc')
                    .select('id,carrier_id,plan_pattern,match_op').eq('org_id', org_id).execute().data) or []
    except Exception:
        raise HTTPException(400, "Run migration 074_product_mrc.sql first.")
    cid = (carrier_id or "").strip() or None
    by_key = {((e.get('carrier_id') or ''), str(e.get('plan_pattern') or '').lower(), e.get('match_op')): e['id']
              for e in existing}
    saved = updated = 0
    inserts = []
    for p in parsed:
        key = ((cid or ''), p['plan'].lower(), match_op)
        if key in by_key:
            client.schema('commcalc').table('product_mrc').update(
                {"mrc": p['mrc'], "is_active": True}).eq('id', by_key[key]).eq('org_id', org_id).execute()
            updated += 1
        else:
            inserts.append({"org_id": org_id, "carrier_id": cid, "plan_pattern": p['plan'],
                            "match_op": match_op, "mrc": p['mrc'], "priority": 100, "is_active": True,
                            "note": f"imported from {getattr(file, 'filename', 'sheet')}"})
    for i in range(0, len(inserts), 200):
        client.schema('commcalc').table('product_mrc').insert(inserts[i:i + 200]).execute()
        saved += len(inserts[i:i + 200])
    return {"saved": saved, "updated": updated, "skipped": skipped, "total": len(parsed),
            "plan_col": use_plan, "mrc_col": use_mrc}


@router.get("/product-mrc/coverage")
async def product_mrc_coverage(period: str = "", org_id: str = ORG_ID):
    """Distinct raw_mi.customer_plan values (optionally for one period) with row counts + whether the
    catalog resolves an MRC for each — so the user can see which plans still need one. Read-only.
    Unmatched plans sort first, then by count desc."""
    client = sb()
    catalog = installment_engine._load_product_mrc(client, org_id)
    # raw_mi.carrier_id arrives with migration 081 — probe once and degrade to plan-only so this
    # helper keeps working on a pre-081 database (rows then key under carrier None).
    cols = 'customer_plan,carrier_id'
    try:
        client.schema('commcalc').table('raw_mi').select(cols).limit(1).execute()
    except Exception:
        cols = 'customer_plan'
    plans, start, page = {}, 0, 1000
    try:
        while True:
            q = (client.schema('commcalc').table('raw_mi').select(cols)
                 .eq('org_id', org_id))
            if period.strip():
                q = q.in_('period', _pvariants(period.strip()))
            rows = q.range(start, start + page - 1).execute().data or []
            for r in rows:
                plan = str(r.get('customer_plan') or '').strip()
                if not plan:
                    continue
                key = (plan, r.get('carrier_id'))
                plans[key] = plans.get(key, 0) + 1
            if len(rows) < page:
                break
            start += page
    except Exception as e:
        return {"plans": [], "ready": False, "note": f"raw_mi read failed: {e}"}
    out = []
    for (plan, carrier_id), cnt in plans.items():
        mrc = installment_engine._catalog_mrc(catalog, carrier_id, plan)
        out.append({"customer_plan": plan, "carrier_id": carrier_id, "subscribers": cnt,
                    "mrc": mrc, "matched": mrc is not None})
    out.sort(key=lambda x: (x["matched"], -x["subscribers"]))
    return {"plans": out, "catalog_size": len(catalog), "period": period, "ready": True}


# ── Distributors (suppliers) + universal payment-funding ledger (migration 058) ───────────────────
# A distributor is who a tenant sources devices/inventory from, on a per-distributor ARRANGEMENT:
# 'terms' (net credit), 'consignment' (lent devices billed on a cycle = Asset Lending, like VIP), or
# 'cod'. The payment ledger records HOW each payment was funded — own vs borrowed account — for any
# company. "VIP" is now just one seeded distributor under the generic Distributors category.

# ═══ UI label nicknames + nav capabilities (SaaS B-phase2) ══════════════════════════════════════
# Per-tenant DISPLAY config for the sidebar: nickname labels (commcalc.ui_label_override, mig 068) +
# computed capability flags so the nav can hide irrelevant items. Pure read for the platform layout;
# writes are admin config. GRACEFUL: if mig 068 is absent, labels = {} and the built-in labels render.
def _asset_lending_capability(client, org_id):
    """True if the tenant has an active consignment / asset-lending distributor; False if it has
    distributors but none lend assets; None if unknown (mig 058 absent or no distributors yet). The
    sidebar shows the Asset-Lending nav on None/True and hides it ONLY on an explicit False — so a new
    or un-migrated tenant always sees it (default-show safe)."""
    try:
        rows = (client.schema('commcalc').table('distributors')
                .select('arrangement,has_asset_lending,is_active').eq('org_id', org_id).execute().data) or []
    except Exception:
        return None
    active = [r for r in rows if r.get('is_active', True)]
    if not active:
        return None
    return any(bool(r.get('has_asset_lending')) or (r.get('arrangement') == 'consignment') for r in active)


@router.get("/nav-config")
def get_nav_config(org_id: str = ORG_ID):
    """Sidebar config for a tenant: {labels:{key:nickname}, capabilities:{asset_lending:bool|null}}.
    Consumed by the (platform) layout; degrades to empty labels (built-in labels show) pre-068."""
    client = sb()
    labels = {}
    caps = {}
    layout = {}
    try:
        rows = (client.schema('commcalc').table('ui_label_override').select('scope,key,label')
                .eq('org_id', org_id).execute().data) or []
        for r in rows:
            scope = r.get('scope'); k = (r.get('key') or ''); lab = r.get('label')
            if scope == 'cap':   # per-tenant capability override (e.g. 'carrier:<href>' = show|hide)
                if k:
                    v = (lab or '').lower()
                    caps[k] = True if v == 'show' else (False if v == 'hide' else None)
                continue
            if scope == 'layout':
                try:
                    import json as _json
                    layout = _json.loads(lab) if lab else {}
                except Exception:
                    layout = {}
                continue
            if scope == 'group':
                k = 'group:' + k
            if k and lab:
                labels[k] = lab
    except Exception:
        labels = {}
    caps['asset_lending'] = _asset_lending_capability(client, org_id)
    return {"labels": labels, "capabilities": caps, "layout": layout}


@router.post("/nav-labels")
def set_nav_label(body: dict, org_id: str = ORG_ID):
    """Upsert one display nickname. body: {scope?: 'nav'|'group', key, label}. An empty label REMOVES
    the override (reverts to the built-in label). Clear 400 (not 500) if migration 068 isn't applied.
    Display-only: this never touches a DB column, route, report_key or data path."""
    scope = (body.get('scope') or 'nav').strip()
    key = (body.get('key') or '').strip()
    label = (body.get('label') or '').strip()
    if not key:
        raise HTTPException(400, "key required")
    client = sb()
    try:
        if not label:
            client.schema('commcalc').table('ui_label_override').delete() \
                .eq('org_id', org_id).eq('scope', scope).eq('key', key).execute()
            return {"ok": True, "removed": key}
        client.schema('commcalc').table('ui_label_override').upsert(
            {"org_id": org_id, "scope": scope, "key": key, "label": label,
             "updated_at": _datetime.now(_timezone.utc).isoformat()},
            on_conflict="org_id,scope,key").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save label — run migration 068_ui_label_override.sql first. [{e}]")
    return {"ok": True, "scope": scope, "key": key, "label": label}


@router.post("/nav-layout")
def set_nav_layout(body: dict, org_id: str = ORG_ID):
    """Save the per-tenant sidebar LAYOUT (admin config): which group each nav item appears under, plus
    hidden items. Body = {items: {<href>: {group?: str, hidden?: bool, also?: [str]}}, groups?: [str]}.
      · group  — the item's PRIMARY group (a MOVE from its built-in group)
      · also   — ADDITIONAL groups the SAME item is DUPLICATED into (extra sidebar links to one href)
      · hidden — remove the item everywhere
      · groups — admin-created group names that may have NO items yet (so the designer can keep an empty
                 group; the sidebar itself ignores empty groups)
    ADDITIVE / BACKWARD-COMPATIBLE: a legacy body carrying only group/hidden stores byte-identically to
    before (no `also`/`groups` written when absent). Stored as ONE JSON row in commcalc.ui_label_override
    (scope='layout', reusing mig 068 — no new migration); an empty layout (no items AND no groups) clears
    it (reverts to the built-in menu). Display-only — never touches routes, data, or access control."""
    import json as _json
    raw_items = (body or {}).get('items') or {}
    items = {}
    for h, v in raw_items.items():
        if not isinstance(v, dict):
            continue
        g = (v.get('group') or '').strip()
        # sanitize `also`: non-empty trimmed group names, de-duped, never the primary group
        also = []
        for a in (v.get('also') or []):
            a = (a or '').strip() if isinstance(a, str) else ''
            if a and a != g and a not in also:
                also.append(a)
        # keep only meaningful overrides (a move, a hide, or a duplicate)
        if not (g or v.get('hidden') or also):
            continue
        entry = {}
        if g:
            entry['group'] = g
        if v.get('hidden'):
            entry['hidden'] = True
        if also:
            entry['also'] = also
        items[h] = entry
    # admin-created group names (may be empty of items) — de-duped, non-empty
    groups = []
    for gname in ((body or {}).get('groups') or []):
        gname = (gname or '').strip() if isinstance(gname, str) else ''
        if gname and gname not in groups:
            groups.append(gname)
    client = sb()
    try:
        if not items and not groups:
            client.schema('commcalc').table('ui_label_override').delete() \
                .eq('org_id', org_id).eq('scope', 'layout').eq('key', '__nav__').execute()
            return {"ok": True, "cleared": True}
        payload = {"items": items}
        if groups:
            payload["groups"] = groups
        client.schema('commcalc').table('ui_label_override').upsert(
            {"org_id": org_id, "scope": "layout", "key": "__nav__",
             "label": _json.dumps(payload),
             "updated_at": _datetime.now(_timezone.utc).isoformat()},
            on_conflict="org_id,scope,key").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save menu layout — run migration 068_ui_label_override.sql first. [{e}]")
    return {"ok": True, "items": items, "groups": groups}


@router.get("/distributors")
async def list_distributors(org_id: str = ORG_ID):
    client = sb()
    try:
        rows = client.schema('commcalc').table('distributors').select('*').eq('org_id', org_id).order('name').execute().data or []
    except Exception:
        return {"distributors": [], "ready": False, "note": "Run migration 058_distributors.sql to enable."}
    return {"distributors": rows, "ready": True}


@router.post("/distributors")
async def save_distributor(body: dict, org_id: str = ORG_ID):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    row = {
        "org_id": org_id, "name": name,
        "carrier_id": body.get("carrier_id") or None,
        "arrangement": (body.get("arrangement") or "terms"),
        "terms_days": int(body.get("terms_days") or 0) or None,
        "billing_cycle": body.get("billing_cycle") or "net",
        "has_asset_lending": bool(body.get("has_asset_lending")),
        "default_funding": body.get("default_funding") or "own",
        "portal_provider": body.get("portal_provider") or None,
        "is_active": bool(body.get("is_active", True)),
        "notes": body.get("notes") or None,
    }
    client = sb()
    try:
        if body.get("id"):
            r = client.schema('commcalc').table('distributors').update(row).eq('id', body['id']).eq('org_id', org_id).execute()
        else:
            r = client.schema('commcalc').table('distributors').upsert(row, on_conflict='org_id,name').execute()
        return (r.data or [{}])[0]
    except Exception as e:
        raise HTTPException(500, f"save distributor failed (is migration 058 applied?): {e}")


@router.delete("/distributors/{distributor_id}")
async def delete_distributor(distributor_id: str, org_id: str = ORG_ID):
    client = sb()
    client.schema('commcalc').table('distributors').delete().eq('org_id', org_id).eq('id', distributor_id).execute()
    return {"deleted": distributor_id}


@router.get("/distributor-payments")
async def list_distributor_payments(distributor_id: str = "", org_id: str = ORG_ID):
    """Payment ledger for a distributor (or all), with own-vs-borrowed funding totals."""
    client = sb()
    try:
        q = client.schema('commcalc').table('distributor_payments').select('*').eq('org_id', org_id)
        if distributor_id:
            q = q.eq('distributor_id', distributor_id)
        rows = q.order('pay_date', desc=True).limit(500).execute().data or []
    except Exception:
        return {"payments": [], "ready": False}
    own = sum(safe_float(r.get('amount')) for r in rows if (r.get('funding_source') or 'own') == 'own')
    borrowed = sum(safe_float(r.get('amount')) for r in rows if r.get('funding_source') == 'borrowed')
    return {"payments": rows, "ready": True,
            "totals": {"own": round(own, 2), "borrowed": round(borrowed, 2), "total": round(own + borrowed, 2)}}


@router.post("/distributor-payments")
async def add_distributor_payment(body: dict, org_id: str = ORG_ID):
    row = {
        "org_id": org_id, "distributor_id": body.get("distributor_id") or None,
        "pay_date": body.get("pay_date") or None, "period": body.get("period") or None,
        "amount": safe_float(body.get("amount")),
        "funding_source": body.get("funding_source") or "own",
        "account_label": body.get("account_label") or None,
        "ref": body.get("ref") or None, "notes": body.get("notes") or None,
    }
    client = sb()
    try:
        r = client.schema('commcalc').table('distributor_payments').insert(row).execute()
        return (r.data or [{}])[0]
    except Exception as e:
        raise HTTPException(500, f"add payment failed (is migration 058 applied?): {e}")


@router.delete("/distributor-payments/{payment_id}")
async def delete_distributor_payment(payment_id: str, org_id: str = ORG_ID):
    client = sb()
    client.schema('commcalc').table('distributor_payments').delete().eq('org_id', org_id).eq('id', payment_id).execute()
    return {"deleted": payment_id}


# ── Configurable commission PLANS (migration 059) — user-built rules, assigned per scope ───────────
# A PLAN is a set of RULES the user creates: each rule matches sale lines on any sales-report field and
# defines how matching lines pay (flat/unit, %MRC, %GP, %price-over-cost, flat bonus), optionally tiered.
# Plans are assigned to employee/store/market/default (precedence employee>store>market>default). The
# preview is READ-ONLY — it never writes rep_commissions and never touches the live POST /calculate path
# (the new system built ALONGSIDE calculator.py; wiring it live is a later, explicit step).

@router.get("/commission-plans")
async def list_commission_plans(org_id: str = ORG_ID):
    """Plans with nested rules + tiers + assignments. {ready:false} if migration 059 isn't applied."""
    client = sb()
    plans, ready = commission_engine._load_plans(client, org_id)
    if not ready:
        return {"plans": [], "ready": False, "note": "Run migration 059_commission_plans.sql to enable."}
    return {"plans": plans, "ready": True}


@router.post("/commission-plans")
async def save_commission_plan(body: dict, org_id: str = ORG_ID):
    """Upsert a plan + REPLACE its rules / tiers / assignments (delete-then-insert children)."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    client = sb()
    plan_row = {
        "org_id": org_id, "name": name,
        "carrier_id": body.get("carrier_id") or None,
        "base_tier_metric": body.get("base_tier_metric") or None,
        "is_active": bool(body.get("is_active", True)),
        "notes": body.get("notes") or None,
    }
    try:
        if body.get("id"):
            r = client.schema('commcalc').table('commission_plan').update(plan_row).eq('id', body['id']).eq('org_id', org_id).execute()
            plan_id = body['id']
        else:
            r = client.schema('commcalc').table('commission_plan').upsert(plan_row, on_conflict='org_id,name').execute()
            plan_id = (r.data or [{}])[0].get('id')
        if not plan_id:
            # upsert on an existing name returns no row in some PostgREST configs — look it up
            got = client.schema('commcalc').table('commission_plan').select('id').eq('org_id', org_id).eq('name', name).execute().data or []
            plan_id = got[0]['id'] if got else None
        if not plan_id:
            raise HTTPException(500, "could not resolve plan id after save")

        # replace children (delete-then-insert by plan)
        for tbl in ('commission_rule', 'commission_tier', 'commission_plan_assignment'):
            client.schema('commcalc').table(tbl).delete().eq('org_id', org_id).eq('plan_id', plan_id).execute()

        rules = []
        for i, rl in enumerate(body.get("rules") or []):
            mf = (rl.get("match_field") or "any")
            if mf not in commission_engine.MATCH_FIELDS:
                mf = "any"
            pk = (rl.get("payout_kind") or "flat_per_unit")
            if pk not in commission_engine.PAYOUT_KINDS:
                pk = "flat_per_unit"
            rules.append({
                "org_id": org_id, "plan_id": plan_id,
                "label": rl.get("label") or None,
                "match_field": mf,
                "match_op": (rl.get("match_op") or "equals"),
                "match_value": rl.get("match_value") or None,
                "qualifies": bool(rl.get("qualifies", True)),
                "payout_kind": pk,
                "amount": safe_float(rl.get("amount")),
                "pct": safe_float(rl.get("pct")),
                "tiered": bool(rl.get("tiered")),
                "sort": int(rl.get("sort") if rl.get("sort") is not None else i),
            })
        if rules:
            client.schema('commcalc').table('commission_rule').insert(rules).execute()

        tiers = []
        for i, t in enumerate(body.get("tiers") or []):
            tiers.append({
                "org_id": org_id, "plan_id": plan_id,
                "metric": t.get("metric") or body.get("base_tier_metric") or None,
                "min_count": int(t.get("min_count") or 0),
                "multiplier": safe_float(t.get("multiplier")) or 1,
                "sort": int(t.get("sort") if t.get("sort") is not None else i),
            })
        if tiers:
            client.schema('commcalc').table('commission_tier').insert(tiers).execute()

        assigns = []
        for a in body.get("assignments") or []:
            scope = (a.get("scope") or "default")
            assigns.append({
                "org_id": org_id, "plan_id": plan_id, "scope": scope,
                "scope_value": (a.get("scope_value") or None) if scope != "default" else None,
                "priority": int(a.get("priority") or 0),
            })
        if assigns:
            client.schema('commcalc').table('commission_plan_assignment').insert(assigns).execute()

        return {"id": plan_id, "saved": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"save plan failed (is migration 059 applied?): {e}")


@router.delete("/commission-plans/{plan_id}")
async def delete_commission_plan(plan_id: str, org_id: str = ORG_ID):
    """Delete a plan (children cascade via FK ON DELETE CASCADE)."""
    client = sb()
    client.schema('commcalc').table('commission_plan').delete().eq('org_id', org_id).eq('id', plan_id).execute()
    return {"deleted": plan_id}


@router.get("/commission-plans/preview")
async def preview_commission_plan(period: str, plan_id: str = "", org_id: str = ORG_ID):
    """READ-ONLY preview: apply plan rules to a period's raw_sales → per-rep payout + breakdown.
    Writes nothing; does not touch rep_commissions or the live calc. plan_id optional (else per-rep
    via assignment precedence)."""
    if not period:
        raise HTTPException(400, "period required")
    client = sb()
    return commission_engine.preview(client, org_id, period, plan_id=plan_id or None)


@router.get("/stores")
async def get_stores(org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('store_mapping').select('*').eq('org_id', org_id).order('store_address').execute()
    return r.data or []

@router.put("/stores/{store_id}")
async def update_store(store_id: str, body: dict, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    allowed = {k: v for k, v in body.items() if k in ['market', 'store_code', 'store_address', 'is_active', 'salesforce_id']}
    if not allowed:
        raise HTTPException(400, "No valid fields to update")
    r = client.schema('commcalc').table('store_mapping').update(allowed).eq('id', store_id).execute()
    return r.data[0] if r.data else {}


# ── store-name aliases (migration 023): map alternate spellings of a store in the B2B sales
# file to a canonical store_code, so Daily Targets actuals attach correctly. Additive — does
# NOT touch store_mapping (which the asset market join depends on). Mirrors rep name aliases.
@router.get("/store-aliases")
async def list_store_aliases(org_id: str = ORG_ID):
    require_org(org_id)
    client = sb()
    try:
        aliases = (client.schema('commcalc').table('store_aliases').select('*')
                   .eq('org_id', org_id).order('store_code').execute().data) or []
    except Exception as e:
        print(f'WARN store_aliases query failed (run 023_store_aliases.sql?): {e}')
        aliases = []
    stores = (client.schema('commcalc').table('store_mapping')
              .select('store_code,store_address').eq('org_id', org_id)
              .order('store_address').execute().data) or []
    return {"aliases": aliases, "stores": stores}


@router.post("/store-aliases")
async def add_store_alias(body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    alias = (body.get('alias') or '').strip()
    code = (body.get('store_code') or '').strip()
    if not alias or not code:
        raise HTTPException(400, "alias and store_code required")
    client = sb()
    # replace any existing alias with the same text (case-insensitive) to keep it unique
    existing = (client.schema('commcalc').table('store_aliases').select('id,alias')
                .eq('org_id', org_id).execute().data) or []
    for r in existing:
        if (r.get('alias') or '').strip().lower() == alias.lower():
            client.schema('commcalc').table('store_aliases').delete().eq('id', r['id']).execute()
    row = {'org_id': org_id, 'alias': alias, 'store_code': code,
           'note': (body.get('note') or '').strip() or None}
    res = client.schema('commcalc').table('store_aliases').insert(row).execute()
    return {"alias": (res.data or [row])[0]}


@router.delete("/store-aliases/{alias_id}")
async def delete_store_alias(alias_id: str, org_id: str = ORG_ID):
    require_org(org_id)
    sb().schema('commcalc').table('store_aliases').delete() \
        .eq('org_id', org_id).eq('id', alias_id).execute()
    return {"ok": True}


@router.get("/store-unmatched")
async def store_unmatched(org_id: str = ORG_ID):
    """Diagnose store mismatches for the Store-Matching UI: distinct raw store strings across the
    data sources that do NOT resolve to a canonical commcalc.store_mapping address (after the
    alias / store_code / leading-number chain). These are the stores to map (add an alias) so the
    P&L, Daily Targets and recon all attribute to one canonical store instead of splitting it."""
    require_org(org_id)
    client = sb()
    from app.modules.account import coa
    canon = {}
    for m in (client.schema('commcalc').table('store_mapping')
              .select('store_code,store_address,market').eq('org_id', org_id).execute().data or []):
        a = (m.get('store_address') or '').strip()
        if a:
            canon[a.lower()] = m
    resolve = coa.store_resolver(client, org_id)
    # (table, column) carrying a store string in its own spelling
    srcs = [('raw_sales', 'store'), ('asset_ledger', 'store'), ('rep_commissions', 'store'),
            ('raw_comp_report', 'business_address'), ('vip_paygo', 'dealer'), ('vip_invoices', 'location')]
    seen = {}
    for table, col in srcs:
        try:
            rows = (client.schema('commcalc').table(table).select(col)
                    .eq('org_id', org_id).limit(60000).execute().data) or []
        except Exception:
            continue  # table missing org_id / not present → skip that source
        for r in rows:
            v = (r.get(col) or '').strip()
            if not v:
                continue
            e = seen.setdefault(v.lower(), {'raw': v, 'sources': set()})
            e['sources'].add(table)
    unmatched = []
    for e in seen.values():
        res = resolve(e['raw'])
        if not res or res.lower() not in canon:
            unmatched.append({'raw': e['raw'], 'sources': sorted(e['sources']), 'guess': res})
    unmatched.sort(key=lambda x: x['raw'].lower())
    return {'unmatched': unmatched, 'unmatched_count': len(unmatched),
            'matched_distinct': len(seen) - len(unmatched),
            'sources_scanned': [s[0] for s in srcs]}


def _compute_gp(client, org_id, period, market=""):
    """The Gross-Profit engine call, factored out so BOTH GET /gp/{period} and the Trends gp-trend can
    use it. Returns the full result; pass `market` to filter store_rows. Same narrowed selects as before."""
    pv = _pvariants(period)
    sc = client.schema('commcalc')
    sales      = sc.table('raw_sales').select('store,department,gp,product_desc,ext_price,salesperson').eq('org_id', org_id).in_('period', pv).limit(50000).execute().data or []
    pay_detail = sc.table('raw_payment_detail').select('business_address,amount,payment_type').eq('org_id', org_id).in_('period', pv).limit(50000).execute().data or []
    mi_rows    = sc.table('raw_mi').select('salesforce_id,actual_mi_payout,actual_atu_payout').eq('org_id', org_id).in_('period', pv).execute().data or []
    rep_comms  = sc.table('rep_commissions').select('store,total_payout,epay_salesperson,storeops_name').eq('org_id', org_id).in_('period', pv).execute().data or []
    expenses   = sc.table('store_expenses').select('store_code,amount').eq('org_id', org_id).in_('period', pv).execute().data or []
    catalog    = sc.table('raw_catalog').select('product_id,cost').eq('org_id', org_id).execute().data or []
    store_map  = sc.table('store_mapping').select('store_address,salesforce_id,market,store_code,is_active').eq('org_id', org_id).execute().data or []
    pay_cats   = sc.table('payment_categories').select('description,category').eq('org_id', org_id).execute().data or []
    comp_rows  = sc.table('raw_comp_report').select('business_address,compensation_type,payment_amount').eq('org_id', org_id).in_('period', pv).limit(50000).execute().data or []
    cat_map    = {r['description'].strip(): r['category'] for r in pay_cats if r.get('description')}
    for r in pay_detail:
        pt = str(r.get('payment_type', '') or '').strip()
        r['category'] = cat_map.get(pt, 'Unknown')
    try:   # per-tenant department→GP-category overrides (mig 069); empty/missing = built-in Boost buckets
        gp_cat_map = sc.table('gp_category_map').select('department,category').eq('org_id', org_id).execute().data or []
    except Exception:
        gp_cat_map = []
    result = calc_gp_report(sales, pay_detail, mi_rows, rep_comms, expenses, catalog, store_map, period,
                            comp_rows=comp_rows, gp_category_map=gp_cat_map)
    if market:
        result['store_rows'] = [r for r in result['store_rows'] if r.get('market', '').upper() == market.upper()]
    return result


def _gp_snapshot_rows(result):
    return [{'store': r.get('store'), 'store_code': r.get('store_code'), 'market': r.get('market'),
             'total_rev': r.get('total_rev'), 'net_profit': r.get('net_profit')}
            for r in (result.get('store_rows') or [])]


def _write_gp_snapshot(client, org_id, period, result):
    """Cache per-period GP totals for the Trends hub (best-effort; never breaks the report)."""
    try:
        client.schema('commcalc').table('gp_snapshot').upsert(
            {'org_id': org_id, 'period': period, 'store_rows': _gp_snapshot_rows(result),
             'computed_at': _datetime.now(_timezone.utc).isoformat()},
            on_conflict='org_id,period').execute()
    except Exception as e:
        print(f"WARN gp_snapshot upsert failed (run migration 102?): {e}")


@router.get("/gp/{period}")
async def get_gp_report(period: str, view: str = "store", market: str = "", authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    result = _compute_gp(client, org_id, period, market="")   # full (unfiltered) so the snapshot is complete
    _write_gp_snapshot(client, org_id, period, result)   # snapshot stays company-complete
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / rbac off)
    if ks is not None:
        result = {**result, 'store_rows': [r for r in result['store_rows'] if in_keyset(ks, r.get('store'), r.get('store_code'), r.get('address'))]}
    if market:
        result = {**result, 'store_rows': [r for r in result['store_rows'] if r.get('market', '').upper() == market.upper()]}
    return result


# ═══ TRENDS (month-over-month) — power the Trends hub + per-report charts ═════════════════════════
def _tperiods(periods_present, months):
    """Sort present 'Month YYYY' periods chronologically; keep the most recent `months`."""
    kept = sorted({(p or '').strip() for p in periods_present if p},
                  key=lambda p: (parse_period(p)['year'], parse_period(p)['month']))
    return kept[-months:] if months and months > 0 else kept


def _trend_shape(kept, by_store, comp, mkt, value_keys):
    """Assemble the common trend response from per-(store,period) values."""
    stores = []
    for code, per in by_store.items():
        series = [{'period': p, **{k: round((per.get(p) or {}).get(k, 0.0), 2) for k in value_keys}} for p in kept]
        stores.append({'store': code, 'store_code': code, 'market': mkt.get(code, 'Boost'), 'series': series})
    sort_key = value_keys[0]
    stores.sort(key=lambda x: -sum(s[sort_key] for s in x['series']))
    company = [{'period': p, **{k: round(comp[p].get(k, 0.0), 2) for k in value_keys}} for p in kept]
    return stores, company


@router.get("/expenses-trend")
async def expenses_trend(months: int = 6, org_id: str = ORG_ID):
    """Total store expenses per month, per store (+ company total). Cheap (store_expenses only)."""
    require_org(org_id)
    sc = sb().schema('commcalc')
    rows = sc.table('store_expenses').select('period,store_code,amount').eq('org_id', org_id).limit(200000).execute().data or []
    sm = sc.table('store_mapping').select('store_code,market').eq('org_id', org_id).execute().data or []
    mkt = {str(s.get('store_code') or '').strip(): (s.get('market') or 'Boost') for s in sm}
    kept = _tperiods({r.get('period') for r in rows}, months); ks = set(kept)
    by, comp = {}, {p: {'total': 0.0} for p in kept}
    for r in rows:
        p = (r.get('period') or '').strip()
        if p not in ks:
            continue
        code = str(r.get('store_code') or '').strip()
        amt = safe_float(r.get('amount'))
        by.setdefault(code, {}).setdefault(p, {'total': 0.0})
        by[code][p]['total'] += amt
        comp[p]['total'] += amt
    stores, company = _trend_shape(kept, by, comp, mkt, ['total'])
    return {'months': kept, 'company': company, 'stores': stores,
            'markets': sorted({s['market'] for s in stores if s['market']}), 'money': True}


@router.get("/commission-trend")
async def commission_trend(months: int = 6, org_id: str = ORG_ID):
    """Commission WE PAY (Σ rep_commissions.total_payout) per month, per store (+ company total)."""
    require_org(org_id)
    import re as _re
    sc = sb().schema('commcalc')
    comms = sc.table('rep_commissions').select('period,store,total_payout').eq('org_id', org_id).limit(200000).execute().data or []
    sm = sc.table('store_mapping').select('store_code,store_address,market').eq('org_id', org_id).execute().data or []

    def _num(a):
        m = _re.match(r'\s*(\d+)', str(a or ''))
        return m.group(1) if m else ''

    code_by_num, mkt, codes = {}, {}, set()
    for s in sm:
        code = str(s.get('store_code') or '').strip()
        if code:
            codes.add(code); mkt[code] = s.get('market') or 'Boost'
        n = _num(s.get('store_address'))
        if n and code:
            code_by_num.setdefault(n, code)
    kept = _tperiods({r.get('period') for r in comms}, months); ks = set(kept)
    by, comp = {}, {p: {'total': 0.0} for p in kept}
    for r in comms:
        p = (r.get('period') or '').strip()
        if p not in ks:
            continue
        pay = safe_float(r.get('total_payout'))
        comp[p]['total'] += pay
        st = str(r.get('store') or '').strip()
        code = st if st in codes else code_by_num.get(_num(st))
        if code:
            by.setdefault(code, {}).setdefault(p, {'total': 0.0})
            by[code][p]['total'] += pay
    stores, company = _trend_shape(kept, by, comp, mkt, ['total'])
    return {'months': kept, 'company': company, 'stores': stores,
            'markets': sorted({s['market'] for s in stores if s['market']}), 'money': True}


@router.get("/gp-trend")
async def gp_trend(months: int = 6, compute_missing: int = 3, org_id: str = ORG_ID):
    """Revenue + Net Profit per month, per store (+ company total), from the gp_snapshot cache. Computes
    up to `compute_missing` newest un-cached months inline (then caches them) so the hub fills in over a
    few loads without recomputing 40k rows every time; older un-cached months are reported in pending_months."""
    require_org(org_id)
    client = sb(); sc = client.schema('commcalc')
    try:
        snaps = {r['period']: (r.get('store_rows') or []) for r in
                 (sc.table('gp_snapshot').select('period,store_rows').eq('org_id', org_id).execute().data or [])}
    except Exception:
        snaps = {}   # migration 102 not run yet
    cand = set(snaps.keys())
    for t in ('store_expenses', 'rep_commissions'):
        for r in (sc.table(t).select('period').eq('org_id', org_id).limit(200000).execute().data or []):
            if r.get('period'):
                cand.add(r['period'].strip())
    kept = _tperiods(cand, months)
    missing = [p for p in kept if p not in snaps]
    for p in list(reversed(missing))[:max(0, compute_missing)]:
        try:
            res = _compute_gp(client, org_id, p, market="")
            _write_gp_snapshot(client, org_id, p, res)
            snaps[p] = _gp_snapshot_rows(res)
        except Exception as e:
            print(f"WARN gp-trend compute {p} failed: {e}")
    mkt, by, comp = {}, {}, {p: {'total_rev': 0.0, 'net_profit': 0.0} for p in kept}
    for p in kept:
        for r in snaps.get(p, []):
            code = str(r.get('store_code') or r.get('store') or '').strip()
            if not code:
                continue
            mkt.setdefault(code, r.get('market') or 'Boost')
            by.setdefault(code, {})[p] = {'total_rev': safe_float(r.get('total_rev')),
                                          'net_profit': safe_float(r.get('net_profit'))}
            comp[p]['total_rev'] += safe_float(r.get('total_rev'))
            comp[p]['net_profit'] += safe_float(r.get('net_profit'))
    stores, company = _trend_shape(kept, by, comp, mkt, ['net_profit', 'total_rev'])
    pending = [p for p in kept if p not in snaps]
    return {'months': kept, 'company': company, 'stores': stores,
            'markets': sorted({s['market'] for s in stores if s['market']}), 'money': True,
            'pending_months': pending,
            'note': (f"{len(pending)} month(s) not yet computed — open the Gross Profit report for them, or reload to compute a few more." if pending else None)}


# ═══ GP / P&L department → category map (de-hardcode Gross Profit, mig 069) ══════════════════════
@router.get("/gp-category-map")
async def get_gp_category_map(org_id: str = ORG_ID):
    """The tenant's POS department → GP-category overrides. Empty/un-migrated = built-in Boost buckets
    (device = Android/IPHONE/TABLET-XP at ext_price, accessory = Ondigo, blank = plan, else = other)."""
    from app.modules.commcalc.gp_report import DEVICE_DEPTS, ONDIGO_DEPT, GP_CATEGORIES
    try:
        rows = sb().schema('commcalc').table('gp_category_map').select('*').eq('org_id', org_id).order('department').execute().data or []
        ready = True
    except Exception:
        rows, ready = [], False
    return {"rows": rows, "ready": ready, "categories": sorted(GP_CATEGORIES),
            "defaults": {"device": sorted(DEVICE_DEPTS), "accessory": [ONDIGO_DEPT]}}


@router.post("/gp-category-map")
async def set_gp_category_map(body: dict, org_id: str = ORG_ID):
    """Upsert ONE department→category override. body: {department, category}. An empty category REMOVES
    the override (reverts to the built-in default). 400 (not 500) if migration 069 isn't applied."""
    if 'department' not in body:
        raise HTTPException(400, "department required")
    dept = str(body.get('department') or '').strip()
    cat = str(body.get('category') or '').strip().lower()
    try:
        if not cat:
            sb().schema('commcalc').table('gp_category_map').delete() \
                .eq('org_id', org_id).eq('department', dept).execute()
            return {"ok": True, "removed": dept}
        sb().schema('commcalc').table('gp_category_map').upsert(
            {"org_id": org_id, "department": dept, "category": cat,
             "updated_at": _datetime.now(_timezone.utc).isoformat()},
            on_conflict="org_id,department").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — run migration 069_gp_category_map.sql first. [{e}]")
    return {"ok": True, "department": dept, "category": cat}


@router.get("/gp-departments")
async def get_gp_departments(period: str = "", org_id: str = ORG_ID):
    """Distinct POS department labels in raw_sales (optionally for a period) with their line count and
    CURRENT GP category — so the tenant can see + map every real label. Drives the GP Category Map UI."""
    from collections import Counter
    from app.modules.commcalc.gp_report import _dept_classifier
    sc = sb().schema('commcalc')
    q = sc.table('raw_sales').select('department').eq('org_id', org_id)
    if period:
        q = q.in_('period', _pvariants(period))
    rows = q.limit(50000).execute().data or []
    cnt = Counter(str(r.get('department') or '').strip() for r in rows)
    try:
        omap = (sc.table('gp_category_map').select('department,category').eq('org_id', org_id).execute().data) or []
    except Exception:
        omap = []
    mapped_keys = {str(r.get('department') or '').strip() for r in omap}
    classify = _dept_classifier(omap)
    out = [{"department": d, "count": n, "category": classify(d), "mapped": d in mapped_keys}
           for d, n in sorted(cnt.items(), key=lambda kv: -kv[1])]
    return {"departments": out}


@router.get("/chargebacks/{period}")
async def get_chargebacks(period: str, authorization: str = Header(default=""), org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('chargeback_items').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).order('epay_salesperson').execute()
    rows = r.data or []
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / rbac off)
    return [c for c in rows if in_keyset(ks, c.get('store_code'), c.get('store_address'))]

@router.put("/chargebacks/{item_id}")
async def update_chargeback(item_id: str, body: dict, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    update = {'deduct': bool(body.get('deduct', False)), 'decided_at': 'now()'}
    if body.get('decided_by'):
        update['decided_by'] = body['decided_by']
    r = client.schema('commcalc').table('chargeback_items').update(update).eq('id', item_id).execute()
    return r.data[0] if r.data else {}

@router.get("/calc-status/{period}")
async def get_calc_status(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('calc_status').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).limit(1).execute()
    return r.data[0] if r.data else {'calc_status': 'not_run'}


# ─────────────────────────────────────────────
# HOTSHEET ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/hotsheet/upload")
async def upload_hotsheet(
    file: UploadFile = File(...),
    effective_date: str = Form(...),
    org_id: str = ORG_ID,   # query param (NOT Form) so tenant middleware can rewrite it; Form fields are unreachable to it
):
    """Upload a pricing hotsheet CSV/Excel. effective_date = YYYY-MM-DD"""
    from datetime import date as date_type
    try:
        eff = date_type.fromisoformat(effective_date)
    except Exception:
        raise HTTPException(status_code=400, detail="effective_date must be YYYY-MM-DD")

    file_bytes = await file.read()
    try:
        rows = parse_hotsheet(file_bytes, eff, org_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        raise HTTPException(status_code=400, detail="No valid rows found in hotsheet file")

    client = sb()
    # Upsert — on conflict (org_id, effective_date, device_model) update
    client.schema("commcalc").table("hotsheet").upsert(
        rows, on_conflict="org_id,effective_date,device_model"
    ).execute()

    return {"status": "ok", "rows_uploaded": len(rows), "effective_date": effective_date}


@router.get("/hotsheet")
async def get_hotsheet(org_id: str = ORG_ID):
    """List all hotsheet uploads grouped by effective_date."""
    client = sb()
    resp = client.schema("commcalc").table("hotsheet")        .select("effective_date,device_model,srp,promo_port_in,promo_non_port,promo_upgrade,promo_aal,boost_protect_fee,notes")        .eq("org_id", org_id)        .order("effective_date", desc=True)        .execute()
    return resp.data or []


@router.delete("/hotsheet/{effective_date}")
async def delete_hotsheet(effective_date: str, org_id: str = ORG_ID):
    """Delete all hotsheet rows for a given effective_date."""
    client = sb()
    client.schema("commcalc").table("hotsheet")        .delete()        .eq("org_id", org_id)        .eq("effective_date", effective_date)        .execute()
    return {"status": "ok", "deleted_date": effective_date}


# ─────────────────────────────────────────────
# COMP RATES ENDPOINTS
# ─────────────────────────────────────────────

@router.get("/comp-rates")
async def get_comp_rates(org_id: str = ORG_ID):
    client = sb()
    resp = client.schema("commcalc").table("comp_rates")        .select("*")        .eq("org_id", org_id)        .order("comp_type")        .order("effective_date", desc=True)        .execute()
    return resp.data or []


@router.post("/comp-rates")
async def upsert_comp_rate(payload: dict, org_id: str = ORG_ID):
    """Add or update a comp rate. Send: comp_type, rate_type, value, effective_date, plan_category, duration_months, notes"""
    client = sb()
    payload["org_id"] = org_id
    client.schema("commcalc").table("comp_rates").upsert(
        payload, on_conflict="org_id,comp_type,plan_category,effective_date"
    ).execute()
    return {"status": "ok"}


@router.delete("/comp-rates/{comp_rate_id}")
async def delete_comp_rate(comp_rate_id: int, org_id: str = ORG_ID):
    client = sb()
    client.schema("commcalc").table("comp_rates")        .delete().eq("org_id", org_id).eq("id", comp_rate_id).execute()
    return {"status": "ok"}


# ─────────────────────────────────────────────
# SALES REPORT — the actual sales done, all stores, from the imported Sales Transaction Details
# ─────────────────────────────────────────────
@router.get("/sales-report")
async def sales_report(period: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Sales actually done, per (store, rep, day), from the imported Sales Transaction Details.
    Reads raw_sales (the authoritative monthly upload) for `period`, FALLING BACK to daily_sales_feed
    (the emailed daily feed) when raw_sales has no rows for that period — so the report works even
    before the feed→raw_sales promotion is turned on (the current Boost case). One aggregated row per
    store + salesperson + date; the frontend's ReportShell adds the rep/store/date/month filters,
    group-by, export and send. `period` accepts 'June 2026' or '2026-06'; blank = current month."""
    client = sb()
    if not period:
        n = datetime.now(timezone.utc)
        period = f"{n.year}-{n.month:02d}"
    cols = _SALES_DISPLAY_COLS
    acfg = _accessory_config(client, org_id)

    # THE canonical display source: a per-day UNION of the daily feed and raw_sales (feed-wins-per-day for
    # the open month, exactly promotion's merge). This is what makes a hand-uploaded raw_sales month
    # VISIBLE even while a partial feed exists (the luxelink July incident) — where the old single-source
    # pick showed only the feed's days and silently hid the rest of raw_sales. `_sales_rows_union` never
    # raises (each side reads under try/except), so the primary-read 500 that rendered as a blank page is
    # gone. `src_meta` drives the report's transparency line.
    rows, src_meta = _sales_rows_union(client, org_id, period, cols)
    source = src_meta['primary'] if src_meta['primary_rows'] else src_meta['other']

    # Distinct periods available (both tables) so the UI can offer a month picker.
    periods = set()
    for t in ("raw_sales", "daily_sales_feed"):
        try:
            for r in (client.schema("commcalc").table(t).select("period")
                      .eq("org_id", org_id).limit(100000).execute().data) or []:
                if r.get("period"):
                    periods.add(str(r["period"]))
        except Exception:
            pass

    # THE shared per-(store, rep, day) pass — the ONE aggregation the Sales Report, Executive MTD and Daily
    # Targets all consume (see _sales_cell_agg), so they can never disagree: canonical skip rules (voided /
    # Returns / the blank-or-'admin' rep), the shared classify_contract_type + _is_accessory, DISTINCT
    # trans_id per bucket (a multi-line AAL transaction = 1, not N), and the swaps tally. This IS the
    # aggregation the owner calls correct — Exec MTD now derives its cumulative numbers from the very same
    # cells (rolled up by store/employee with an MTD date-cut) instead of its old per-line/config loop.
    agg = _sales_cell_agg(rows, acfg)

    # Resolve each store to its market (store_mapping) so the report can filter by market —
    # keyed by address, store_code, or leading store-number, matching commission-trend's resolver.
    import re as _re_sr
    try:
        sm_rows = (client.schema("commcalc").table("store_mapping")
                   .select("store_code,store_address,market").eq("org_id", org_id).execute().data) or []
    except Exception:
        sm_rows = []   # market resolution is optional — never 500 the report over a store_mapping read
    def _lead_sr(s):
        m = _re_sr.match(r"\s*(\d+)", str(s or "")); return m.group(1) if m else ""
    mkt_by_code, mkt_by_addr, mkt_by_num, all_markets = {}, {}, {}, set()
    for s in sm_rows:
        mk = (s.get("market") or "").strip()
        if not mk:
            continue
        all_markets.add(mk)
        code = str(s.get("store_code") or "").strip()
        addr = str(s.get("store_address") or "").strip()
        if code:
            mkt_by_code[code] = mk
        if addr:
            mkt_by_addr[addr.lower()] = mk
        n = _lead_sr(addr)
        if n:
            mkt_by_num.setdefault(n, mk)
    def _market_for(store):
        st = str(store or "").strip()
        return (mkt_by_addr.get(st.lower()) or mkt_by_code.get(st)
                or mkt_by_num.get(_lead_sr(st)) or "")

    out = []
    for a in agg.values():
        st = a["store"] or "—"
        out.append({
            "store": st, "salesperson": a["salesperson"], "trans_date": a["trans_date"],
            "txns": len(a["_txn"]), "activations": len(a["_prem"]), "byod": len(a["_byod"]),
            "upgrades": len(a["_upg"]), "swaps": len(a["_swap"]), "lines": a["lines"],
            "accessory_rev": round(a["accessory_rev"], 2), "revenue": round(a["revenue"], 2),
            "gp": round(a["gp"], 2), "market": _market_for(st),
        })
    out.sort(key=lambda r: (r["store"], r["trans_date"], r["salesperson"]))
    try:
        from app.modules.storeops.router import scope_keyset, in_keyset
        ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / rbac off)
        if ks is not None:
            out = [r for r in out if in_keyset(ks, r.get("store"))]
    except Exception:
        pass   # a span-scope resolution error must not blank the report (fail open to unrestricted)
    totals = {
        "txns": sum(r["txns"] for r in out), "lines": sum(r["lines"] for r in out),
        "activations": sum(r["activations"] for r in out), "byod": sum(r["byod"] for r in out),
        "upgrades": sum(r["upgrades"] for r in out), "swaps": sum(r["swaps"] for r in out),
        "accessory_rev": round(sum(r["accessory_rev"] for r in out), 2),
        "revenue": round(sum(r["revenue"] for r in out), 2), "gp": round(sum(r["gp"] for r in out), 2),
    }
    # RULE FIVE §3d pick-don't-type filter options — built from the org's REAL data (never a hard-coded
    # or house-only list): distinct stores from the union rows actually shown, and markets from those
    # rows' resolved market UNION every store_mapping market (so a market with no sales this month is
    # still a valid pick). The page's market/store MultiSelects render EMPTY without these keys.
    filter_stores = sorted({r["store"] for r in out if r.get("store")})
    filter_markets = sorted({m for m in ([r.get("market") for r in out] + list(all_markets)) if m})
    return {"period": period, "source": source, "org_id": org_id, "rows": out, "totals": totals,
            "periods": sorted(periods, reverse=True),
            "stores": filter_stores, "markets": filter_markets,
            # Transparency (owner's debug-first mandate): exactly what this read used, so a truncated or
            # wrong-tenant view is self-evident. `org_id` above surfaces the super-admin org-resolution
            # default (a no-org_id request reads the HOUSE org). `source_meta` explains the union.
            "source_meta": src_meta,
            "feed_rows": src_meta.get("feed_rows"), "raw_rows": src_meta.get("raw_rows"),
            "shown_rows": src_meta.get("shown_rows"), "filled_days": src_meta.get("filled_days")}


@router.get("/sales-report/detail")
async def sales_report_detail(period: str = "", store: str = "", salesperson: str = "",
                              date: str = "", org_id: str = ORG_ID):
    """Transaction drill-down for one Sales Report cell (store + rep + day): every transaction that
    rolled into that line, each with its line items (product, contract type, price, GP). Same
    raw_sales → daily_sales_feed fallback as the report, so it works off whichever source it used."""
    client = sb()
    if not period:
        n = datetime.now(timezone.utc)
        period = f"{n.year}-{n.month:02d}"
    # Columns present in BOTH sales tables. `sku` is a DEVICE/product identifier the owner wants alongside
    # the model, but it is added PER TABLE only where it actually exists (raw_sales carries it; the
    # daily_sales_feed does NOT — and selecting a missing column throws, which was swallowed to [] = the
    # historic "drill-down shows 0 transactions" bug). `_known_columns` is a cached probe, so this costs at
    # most one extra round-trip per table per process, and daily-feed months simply show no SKU (safe).
    _base_cols = ["trans_id", "trans_date", "store", "salesperson", "customer", "department", "category",
                  "contract_type", "product_desc", "ext_price", "gp", "mdn", "serial_1", "voided"]

    def _q(table):
        cols = list(_base_cols)
        if "sku" in _known_columns(client, table, ["sku"]):
            cols.append("sku")
        # Bound to the day in the query (trans_date may be a DATE or 'YYYY-MM-DD HH:MM' text — a
        # lexicographic gte/lt range works for both). We DON'T .eq() store/salesperson here: the report
        # STRIPPED those, so an exact match on the raw DB value (trailing spaces / case) returns nothing —
        # that was the "drill-down shows no transactions" bug. We match them normalized in Python below.
        q = (client.schema("commcalc").table(table).select(",".join(cols))
             .eq("org_id", org_id).in_("period", _pvariants(period)).limit(50000))
        if date:
            try:
                from datetime import date as _d, timedelta as _td
                nxt = (_d.fromisoformat(date) + _td(days=1)).isoformat()
                q = q.gte("trans_date", date).lt("trans_date", nxt)
            except Exception:
                pass
        return q.execute().data or []
    _primary, _other = _open_month_source(client, org_id, period)
    rows = _q(_primary)
    if not rows:
        try:
            rows = _q(_other)
        except Exception:
            rows = []

    def _n(s):
        return (s or "").strip().lower()
    ns, nr = _n(store), _n(salesperson)
    rows = [r for r in rows
            if (not date or str(r.get("trans_date") or "")[:10] == date)
            and (not store or _n(r.get("store")) == ns)
            and (not salesperson or _n(r.get("salesperson")) == nr)]

    # Which POS Departments count as a device "box" — CONFIG-DRIVEN (mig 218, the SAME list the box count
    # on the Sales Report / Productivity uses), default = the Boost XP labels. Used ONLY to LABEL which line
    # carries the phone that was sold so the drill-down can show "which phone" at a glance — never to change
    # a count, a classification, or a payout. Degrades to the code default pre-migration.
    try:
        _box_depts = _accessory_config(client, org_id).get("box_departments") or set(_BOX_DEPTS)
    except Exception:
        _box_depts = set(_BOX_DEPTS)

    txns = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        tid = r.get("trans_id") or "—"
        t = txns.get(tid)
        if not t:
            t = txns[tid] = {"trans_id": tid, "trans_date": str(r.get("trans_date") or "")[:10],
                             "customer": (r.get("customer") or "").strip(), "lines": [],
                             "total": 0.0, "gp": 0.0, "_devices": []}
        try:
            ext = float(r.get("ext_price") or 0)
        except (TypeError, ValueError):
            ext = 0.0
        try:
            gp = float(r.get("gp") or 0)
        except (TypeError, ValueError):
            gp = 0.0
        pdesc = (r.get("product_desc") or "").strip()
        serial = (r.get("serial_1") or "").strip()
        dept = str(r.get("department") or "").strip()
        # A device (box) line = its Department is a configured box department OR it carries a device
        # serial/IMEI (a universal fallback so the phone still surfaces for a tenant whose box config isn't
        # set yet). Accessory / feature / setup-fee lines have neither. DISPLAY-only tag.
        is_device = bool(pdesc) and (dept in _box_depts or bool(serial))
        t["lines"].append({"department": r.get("department"), "category": r.get("category"),
                           "contract_type": r.get("contract_type"), "product": r.get("product_desc"),
                           "sku": r.get("sku"), "mdn": r.get("mdn"), "serial": r.get("serial_1"),
                           "ext_price": round(ext, 2), "gp": round(gp, 2), "is_device": is_device})
        if is_device and pdesc not in t["_devices"]:
            t["_devices"].append(pdesc)
        t["total"] += ext
        t["gp"] += gp
        if not t["customer"] and (r.get("customer") or "").strip():
            t["customer"] = (r.get("customer") or "").strip()
    out = sorted(txns.values(), key=lambda x: str(x["trans_id"]))
    for t in out:
        t["total"] = round(t["total"], 2)
        t["gp"] = round(t["gp"], 2)
        t["line_count"] = len(t["lines"])
        # The phone(s) sold on this transaction — the box/device line product description(s), joined for a
        # one-glance header ("which phone was sold"). None for an accessory-only / no-device transaction.
        devs = t.pop("_devices", [])
        t["device"] = " · ".join(devs) if devs else None
    return {"store": store, "salesperson": salesperson, "date": date, "period": period,
            "transactions": out, "txn_count": len(out)}


@router.get("/sales-diagnostics")
def sales_diagnostics(period: str = "", org_id: str = ORG_ID):
    """Why do the Action-Plan / targets tiles show what they show? For a period this reports what the
    sales tables ACTUALLY hold — row counts, the exact period spellings present, and the distinct
    Contract Type + Department values (with counts) in daily_sales_feed and raw_sales — plus the
    computed actuals totals (activations/byod/upgrades/accessory$). Read-only; the go-to when a store's
    numbers look wrong (usually a Contract Type label the old rigid SQL didn't recognize)."""
    from collections import Counter
    client = sb()
    if not period:
        n = datetime.now(timezone.utc)
        period = f"{n.year}-{n.month:02d}"
    pv = _pvariants(period)

    def _scan(table):
        try:
            rows = (client.schema('commcalc').table(table)
                    .select('period,contract_type,department,category,product_desc,salesperson')
                    .eq('org_id', org_id).in_('period', pv).limit(200000).execute().data) or []
        except Exception as e:
            return {'error': str(e)}
        ct, dept, cat, prod, per = Counter(), Counter(), Counter(), Counter(), Counter()
        # Products on the NON-phone lines (blank Contract Type) — that's where accessories hide when a
        # POS doesn't carry Department/Category (the B2B daily feed case).
        for r in rows:
            per[str(r.get('period') or '')] += 1
            ctv = (r.get('contract_type') or '').strip()
            ct[ctv or '(blank)'] += 1
            dept[(r.get('department') or '').strip() or '(blank)'] += 1
            cat[(r.get('category') or '').strip() or '(blank)'] += 1
            if not ctv:
                prod[(r.get('product_desc') or '').strip() or '(blank)'] += 1
        return {'rows': len(rows), 'periods': dict(per),
                'contract_types': dict(ct.most_common(40)), 'departments': dict(dept.most_common(40)),
                'categories': dict(cat.most_common(40)),
                'products_on_nonphone_lines': dict(prod.most_common(40))}

    feed_actuals = _compute_feed_actuals_py(client, org_id, period)
    tot = {'activations': sum(a['prem_count'] for a in feed_actuals),
           'byod': sum(a['byod_count'] for a in feed_actuals),
           'upgrades': sum(a['upg_count'] for a in feed_actuals),
           'accessory_gp': round(sum(a['acc_gp'] for a in feed_actuals), 2),
           'store_rep_days': len(feed_actuals)}
    acfg = _accessory_config(client, org_id)
    return {'period': period, 'period_variants': pv, 'open_month': _is_open_month(period),
            'daily_sales_feed': _scan('daily_sales_feed'), 'raw_sales': _scan('raw_sales'),
            'computed_actuals_totals': tot,
            'accessory_config': {'departments': acfg['departments_list'], 'categories': acfg['categories_list']}}


@router.get("/upload-trace")
def upload_trace(period: str = "", upload_type: str = "", source: str = "",
                 status: str = "", limit: int = 100, org_id: str = ORG_ID):
    """WHERE ARE MY ROWS? (mig 202) — the debug-first trace for THIS org: one record per ingest attempt
    (manual upload, email sweep, FTP sweep, feed→raw_sales promotion), newest first. Each row says which
    ORG the data landed in, rows-in vs saved, per-period + per-day saved counts, the guard/shrink outcome,
    duration, and any error — so 'I uploaded a file and the page shows nothing' is answered from data.
    Optional filters: upload_type, source, status, and period (matched against the periods JSONB, spelling
    -agnostic). Read-only + org-scoped. Degrades gracefully (returns ok=false + a hint) if mig 202 is
    unrun. The echoed `org_id` is deliberate: it exposes the super-admin org-resolution default (a request
    with no org_id reads the HOUSE org) so a wrong-tenant view is self-evident."""
    require_org(org_id)
    client = sb()
    try:
        q = (client.schema("commcalc").table("upload_trace").select("*")
             .eq("org_id", org_id).order("created_at", desc=True).limit(max(1, min(limit, 1000))))
        if upload_type:
            q = q.eq("upload_type", upload_type)
        if source:
            q = q.eq("source", source)
        if status:
            q = q.eq("status", status)
        rows = q.execute().data or []
    except Exception as e:
        return {"ok": False, "org_id": org_id, "records": [],
                "hint": f"upload_trace unavailable — run migration 202 first ({e})."}
    # Period filter is applied in Python against the periods JSONB so it matches either spelling.
    if period:
        pv = set(_pvariants(period))
        rows = [r for r in rows if not r.get("periods") or (set((r.get("periods") or {}).keys()) & pv)
                or str(r.get("period") or "") in pv]
    return {"ok": True, "org_id": org_id, "count": len(rows), "records": rows}


@router.get("/accessory-config")
def get_accessory_config(org_id: str = ORG_ID):
    """The configured accessory dept/category/product-keyword lists + the ACIMA-lease tender(s) + the
    BOX departments (mig 218; which POS Departments count as a device 'box')."""
    c = _accessory_config(sb(), org_id)
    return {"departments": c["departments_list"], "categories": c["categories_list"],
            "product_keywords": c["products_list"], "acima_tenders": c["acima_tenders_list"],
            "box_departments": c["box_departments_list"],
            "setup_fee_keywords": c["setup_fee_keywords_list"]}


@router.put("/accessory-config")
def put_accessory_config(body: dict, org_id: str = ORG_ID):
    """Set what counts as accessory sales + which Tender Type = an ACIMA lease. Body: {departments:[...],
    categories:[...], product_keywords:[...], acima_tenders:[...]}. A line is an accessory if its
    department OR category is listed OR its product description contains a keyword. ACIMA commission =
    distinct transactions whose Tender Type contains any acima_tenders value × acima_spiff. Drives the
    Sales Report, the Action-Plan accessory tile, and (on recalc) commission accessory + ACIMA pay.
    Persists PER-ORG in commcalc.accessory_config (mig 208) — NOT the flag_rules singleton, whose id=1 PK
    meant a non-house save overwrote the house row's org_id. Only the keys present in the body are updated
    (a partial save reads the current per-org row and re-writes the untouched lists)."""
    require_org(org_id)
    client = sb()
    cur = _accessory_config(client, org_id)
    row = {"org_id": org_id, "updated_at": _cb_now(),
           "departments": cur["departments_list"], "categories": cur["categories_list"],
           "product_keywords": cur["products_list"], "acima_tenders": cur["acima_tenders_list"]}
    if "departments" in body or "categories" in body or "product_keywords" in body:
        row["departments"] = [str(x).strip() for x in (body.get("departments") or []) if str(x).strip()]
        row["categories"] = [str(x).strip() for x in (body.get("categories") or []) if str(x).strip()]
        row["product_keywords"] = [str(x).strip() for x in (body.get("product_keywords") or []) if str(x).strip()]
    if "acima_tenders" in body:
        row["acima_tenders"] = [str(x).strip() for x in (body.get("acima_tenders") or []) if str(x).strip()]
    # BOX departments (mig 218). Included defensively: pre-218 the column doesn't exist, so a save carrying
    # it 500s — we retry WITHOUT it so editing the accessory lists never breaks before 218 is applied (box
    # counting then falls back to the code default _BOX_DEPTS).
    row["box_departments"] = ([str(x).strip() for x in (body.get("box_departments") or []) if str(x).strip()]
                              if "box_departments" in body else cur["box_departments_list"])
    # Device SET-UP FEE keywords (mig 217, pkg A field — editable from the shared Classification-settings UI).
    row["setup_fee_keywords"] = ([str(x).strip() for x in (body.get("setup_fee_keywords") or []) if str(x).strip()]
                                 if "setup_fee_keywords" in body else cur["setup_fee_keywords_list"])
    # Persist defensively: pre-mig-217/218 those columns don't exist, so a save carrying them 500s — retry
    # progressively dropping the newest columns so editing the accessory lists never breaks before the
    # migrations run (box → _BOX_DEPTS default, set-up fee → 'Device Setup Charge' default).
    for _drop in ([], ["setup_fee_keywords"], ["setup_fee_keywords", "box_departments"]):
        attempt = dict(row)
        for k in _drop:
            attempt.pop(k, None)
        try:
            client.schema("commcalc").table("accessory_config").upsert(attempt, on_conflict="org_id").execute()
            break
        except Exception as e2:
            if _drop == ["setup_fee_keywords", "box_departments"]:
                raise HTTPException(500, f"save failed — run migration 208_commission_accessory_config.sql first: {e2}")
    return get_accessory_config(org_id)


@router.get("/sales-fields")
def sales_fields(period: str = "", org_id: str = ORG_ID):
    """Populate the accessory-settings pickers: the distinct Department + Category values in the sales
    data, plus the top PRODUCT descriptions on the non-phone (blank Contract Type) lines — that's where
    accessories live when a POS carries no dept/category — as keyword suggestions. `period` blank = all.
    Also returns the distinct Contract Type + Transaction Type values so the Commission-Plan `match_value`
    picker can offer the OBSERVED values for the selected match_field (RULE THREE §3b — pick, don't type).
    READ-ONLY; adds keys only (never removes) so existing accessory-settings callers are unaffected."""
    client = sb()
    depts, cats, tenders, ctypes, ttypes = set(), set(), set(), set(), set()
    prod = {}
    for tbl in ("daily_sales_feed", "raw_sales"):
        try:
            q = client.schema("commcalc").table(tbl).select("department,category,product_desc,contract_type,tender_type,trans_type").eq("org_id", org_id)
            if period:
                q = q.in_("period", _pvariants(period))
            for r in (q.limit(200000).execute().data or []):
                d = (r.get("department") or "").strip()
                c = (r.get("category") or "").strip()
                t = (r.get("tender_type") or "").strip()
                ct = (r.get("contract_type") or "").strip()
                tt = (r.get("trans_type") or "").strip()
                if d:
                    depts.add(d)
                if c:
                    cats.add(c)
                if t:
                    tenders.add(t)
                if ct:
                    ctypes.add(ct)
                if tt:
                    ttypes.add(tt)
                if not ct:   # non-phone line → candidate accessory
                    p = (r.get("product_desc") or "").strip()
                    if p:
                        prod[p] = prod.get(p, 0) + 1
        except Exception:
            pass
    top_products = [p for p, _ in sorted(prod.items(), key=lambda kv: -kv[1])[:60]]
    cur = _accessory_config(client, org_id)
    return {"departments": sorted(depts), "categories": sorted(cats), "products": top_products,
            "tenders": sorted(tenders), "contract_types": sorted(ctypes), "trans_types": sorted(ttypes),
            "accessory_departments": cur["departments_list"], "accessory_categories": cur["categories_list"],
            "accessory_product_keywords": cur["products_list"], "acima_tenders": cur["acima_tenders_list"],
            # Box (device-unit) departments + device set-up-fee keywords for the Classification-settings UI —
            # options are the DISTINCT `departments`/`products` above (pick-don't-type, RULE THREE).
            "box_departments": cur["box_departments_list"], "setup_fee_keywords": cur["setup_fee_keywords_list"]}


@router.get("/commission-drill")
def commission_drill(period: str, rep: str = "", org_id: str = ORG_ID):
    """The exact transactions behind each paid-out commission component for one rep + period, so every
    number on the Rep Commission Report can be verified. Replays the SAME classification the calculator
    uses (shared contract-type classifier + configurable accessory + setup-fee + ACIMA-tender), over the
    rep's raw_sales rows (the calc's source; falls back to the daily feed for visibility). Premium/BYOD/
    upgrade/ACIMA are DISTINCT transactions (matching the pay counts); accessories/setup are line items."""
    import re as _re
    client = sb()
    acfg = _accessory_config(client, org_id)
    _acima_tender_set = {t.strip().lower() for t in acfg["acima_tenders_list"] if t.strip()} or {"acima"}
    cols = ("trans_id,trans_date,store,salesperson,department,category,product_desc,contract_type,"
            "tender_type,ext_price,gp,voided,trans_type,mdn,serial_1")   # no 'sku' — feed lacks that column

    def _q(table):
        return (client.schema("commcalc").table(table).select(cols)
                .eq("org_id", org_id).in_("period", _pvariants(period)).limit(200000).execute().data) or []
    rows = _q("raw_sales")
    source = "raw_sales"
    if not rows:
        try:
            rows = _q("daily_sales_feed"); source = "daily_sales_feed"
        except Exception:
            rows = []

    def _norm(s):
        return _re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()
    rnorm = _norm(rep)
    rtok = set(rnorm.split())

    def _match(sp):
        s = _norm(sp)
        if not (s and rnorm):
            return False
        if s == rnorm:
            return True
        st = set(s.split())
        return bool(st) and (st <= rtok or rtok <= st)
    if rep:
        rows = [r for r in rows if _match(r.get("salesperson"))]

    def _line(r):
        return {"trans_id": r.get("trans_id"), "date": str(r.get("trans_date") or "")[:10],
                "product": r.get("product_desc"), "contract_type": r.get("contract_type"),
                "ext_price": round(safe_float(r.get("ext_price")), 2), "gp": round(safe_float(r.get("gp")), 2),
                "tender_type": r.get("tender_type"), "mdn": r.get("mdn"), "serial": r.get("serial_1")}
    prem, byod, upg, acima = {}, {}, {}, {}
    acc, setup = [], []
    for r in rows:
        if str(r.get("voided") or "").strip().upper() == "YES":
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        tid = str(r.get("trans_id") or "").strip()
        cls = classify_contract_type(r.get("contract_type"))
        if tid and cls == "premium":
            prem.setdefault(tid, _line(r))
        elif tid and cls == "byod":
            byod.setdefault(tid, _line(r))
        elif tid and cls == "upgrade":
            upg.setdefault(tid, _line(r))
        if _is_accessory(r.get("department"), r.get("category"), r.get("product_desc"), acfg):
            acc.append(_line(r))
        if "Device Setup Charge" in str(r.get("product_desc") or ""):
            setup.append(_line(r))
        _tl = str(r.get("tender_type") or "").lower()
        if tid and any(at in _tl for at in _acima_tender_set):
            acima.setdefault(tid, _line(r))

    def _bucket(d):
        items = list(d.values()) if isinstance(d, dict) else d
        items.sort(key=lambda x: (x["date"], str(x["trans_id"])))
        return {"count": len(items), "sales": round(sum(x["ext_price"] for x in items), 2),
                "gp": round(sum(x["gp"] for x in items), 2), "items": items[:2000]}
    return {"rep": rep, "period": period, "source": source,
            "premium": _bucket(prem), "byod": _bucket(byod), "upgrade": _bucket(upg),
            "accessories": _bucket(acc), "setup": _bucket(setup), "acima": _bucket(acima)}


# ─────────────────────────────────────────────
# SALES FEED RECON (Theme 5) — monthly authoritative vs daily B2B feed, trans_id grain
# ─────────────────────────────────────────────

@router.get("/sales-recon")
async def sales_feed_recon(period: str = "", org_id: str = ORG_ID):
    """Reconcile the authoritative monthly sales upload (raw_sales) against the daily B2B feed
    (daily_sales_feed) for a period. `period` accepts 'June 2026' or '2026-06'. Read-only.
    Returns {} structure with summary + by_store + rows (see sales_recon.run_sales_recon)."""
    require_org(org_id)
    if not period:
        raise HTTPException(400, "period required")
    try:
        return sales_recon.run_sales_recon(period, org_id)
    except Exception as e:
        raise HTTPException(500, f"Sales recon failed: {e} (run migration 047?)")


@router.get("/sales-recon/transaction")
async def sales_feed_recon_transaction(period: str, trans_id: str, org_id: str = ORG_ID):
    """Line-item drill-down for one transaction — monthly (raw_sales) vs daily (daily_sales_feed)
    lines side by side, so you can see exactly what differs. Powers the recon row click-through."""
    require_org(org_id)
    if not period or not trans_id:
        raise HTTPException(400, "period and trans_id required")
    try:
        return sales_recon.transaction_detail(period, trans_id, org_id)
    except Exception as e:
        raise HTTPException(500, f"Transaction detail failed: {e}")


@router.post("/sales-recon/sync-flags")
async def sales_recon_sync_flags(period: str = "", notify: bool = False,
                                 include_mismatch: bool = True, org_id: str = ORG_ID):
    """Persist the sales-feed recon findings for `period` into commcalc.flags (source='sales_recon':
    missing_in_monthly → critical 'sales_leak'; amount_mismatch → warning). Idempotent — delete-first
    by source, so re-running refreshes without touching other flag sources. If notify=true AND there
    are leaks, also sends the 'sales_recon' report to its designated recipients (Theme 4 routing).
    Returns the flag counts + any notify result."""
    require_org(org_id)
    if not period:
        raise HTTPException(400, "period required")
    try:
        result = sales_recon.sync_recon_flags(period, include_mismatch=include_mismatch, org_id=org_id)
    except Exception as e:
        raise HTTPException(500, f"Sales recon flag sync failed: {e} (run migration 047?)")
    if notify and result.get("missing_in_monthly", 0) > 0:
        try:
            from app.modules.notify import router as N  # lazy: avoids notify↔commcalc import cycle
            result["notify"] = await N.send_to_designated(
                {"report_key": "sales_recon", "filters": {"period": result["period"]},
                 "message": (f"{result['missing_in_monthly']} sales-feed leak(s) totalling "
                             f"${(result.get('leak_total') or 0):,.2f} detected for {result['period']}.")},
                org_id=org_id)
        except Exception as e:
            result["notify_error"] = str(e)
    return result


# ─────────────────────────────────────────────
# DISCREPANCY ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/discrepancy/run")
async def run_discrepancy_check(payload: dict, org_id: str = ORG_ID):
    """Trigger discrepancy detection. Send: { "period": "2026-04" }"""
    period = payload.get("period")
    if not period or len(period) != 7:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    try:
        result = run_discrepancy(period, org_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


@router.get("/discrepancy/{period}")
async def get_discrepancy_results(period: str, org_id: str = ORG_ID):
    """Get all discrepancy results for a period, grouped by store."""
    client = sb()
    resp = client.schema("commcalc").table("discrepancy_results")        .select("*")        .eq("org_id", org_id)        .in_("period", _pvariants(period))        .order("store")        .order("gap", desc=True)        .execute()
    rows = resp.data or []

    # Group by store
    stores = {}
    for r in rows:
        store = r["store"] or "Unknown"
        if store not in stores:
            stores[store] = {"store": store, "total_gap": 0.0, "flagged_count": 0, "rows": []}
        stores[store]["rows"].append(r)
        if r.get("status") == "open" and r["gap"] > 0.50:
            stores[store]["total_gap"] = round(stores[store]["total_gap"] + r["gap"], 2)
            stores[store]["flagged_count"] += 1

    return {
        "period": period,
        "summary": sorted(stores.values(), key=lambda x: x["total_gap"], reverse=True),
        "total_gap_usd": round(sum(s["total_gap"] for s in stores.values()), 2),
        "total_flagged": sum(s["flagged_count"] for s in stores.values()),
    }


@router.patch("/discrepancy/{discrepancy_id}")
async def update_discrepancy_status(discrepancy_id: int, payload: dict, org_id: str = ORG_ID):
    """Update status of a discrepancy row: open, resolved, disputed"""
    client = sb()
    client.schema("commcalc").table("discrepancy_results")        .update({"status": payload.get("status"), "notes": payload.get("notes")})        .eq("org_id", org_id)        .eq("id", discrepancy_id)        .execute()
    return {"status": "ok"}


def _period_ym(period: str) -> tuple[int, int]:
    """(year, month) from a period label, accepting both 'June 2026' and '2026-06'.

    Both forms are used across the app; these endpoints originally parsed only the
    numeric form and 500'd (ValueError) on the month-name form that every sibling
    endpoint accepts. Raises 400 with a clear message on anything unrecognized
    instead of leaking a ValueError as a 500. (_MONTH_TOKENS is a module global
    defined below; resolved at call time.)
    """
    p = (period or "").strip()
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        year, month = int(p[:4]), int(p[5:7])
    elif p and p.lower().split()[0] in _MONTH_TOKENS:
        pm = parse_period(p)
        year, month = pm["year"], pm["month"]
    else:
        raise HTTPException(400, f"Unrecognized period '{period}' (expected 'Month YYYY' or 'YYYY-MM')")
    if not 1 <= month <= 12:
        raise HTTPException(400, f"Invalid month in period '{period}'")
    return year, month

@router.get("/discrepancy/{period}/phantom")
async def get_phantom_payments(period: str, org_id: str = ORG_ID):
    """Payments received in the period with no matching commissionable sale (by MDN or IMEI)."""
    from datetime import date as _date
    client = sb()
    year, month = _period_ym(period)
    plabel = _date(year, month, 1).strftime("%B %Y")

    # Build sets of MDNs and IMEIs from ALL sales (any period) AND all MI lines.
    # True phantom = payment matches NO sale anywhere AND no subscriber record.
    sales = (client.schema("commcalc").table("raw_sales")
             .select("serial_1,mdn").eq("org_id", org_id).execute().data) or []
    sale_mdns = {(s.get("mdn") or "").strip() for s in sales if s.get("mdn")}
    sale_imeis = {(s.get("serial_1") or "").strip() for s in sales if s.get("serial_1")}

    mi = (client.schema("commcalc").table("raw_mi")
          .select("device_serial,phone_number").eq("org_id", org_id).execute().data) or []
    mi_mdns = {(m.get("phone_number") or "").strip() for m in mi if m.get("phone_number")}
    mi_imeis = {(m.get("device_serial") or "").strip() for m in mi if m.get("device_serial")}
    known_mdns = sale_mdns | mi_mdns
    known_imeis = sale_imeis | mi_imeis

    # All payments in the period
    pays = (client.schema("commcalc").table("raw_payment_detail")
            .select("imei,mdn,payment_type,amount,business_address,payment_date")
            .eq("org_id", org_id).eq("period_month", month)
            .eq("period_year", year).execute().data) or []

    phantom = []
    matched_total = 0.0
    phantom_total = 0.0
    for p in pays:
        mdn = (p.get("mdn") or "").strip()
        imei = (p.get("imei") or "").strip()
        amt = float(p.get("amount") or 0)
        is_matched = (mdn and mdn in known_mdns) or (imei and imei in known_imeis)
        if is_matched:
            matched_total += amt
        else:
            phantom_total += amt
            phantom.append({
                "mdn": mdn, "imei": imei,
                "payment_type": p.get("payment_type"),
                "amount": round(amt, 2),
                "business_address": p.get("business_address"),
                "payment_date": str(p.get("payment_date"))[:10] if p.get("payment_date") else None,
            })

    # Group phantom by business_address
    by_store = {}
    for ph in phantom:
        addr = ph["business_address"] or "Unknown"
        if addr not in by_store:
            by_store[addr] = {"business_address": addr, "total": 0.0, "count": 0, "rows": []}
        by_store[addr]["total"] = round(by_store[addr]["total"] + ph["amount"], 2)
        by_store[addr]["count"] += 1
        by_store[addr]["rows"].append(ph)

    return {
        "period": period,
        "phantom_total": round(phantom_total, 2),
        "phantom_count": len(phantom),
        "matched_total": round(matched_total, 2),
        "by_store": sorted(by_store.values(), key=lambda x: x["total"], reverse=True),
    }


@router.get("/top-sellers/{period}")
async def get_top_sellers(period: str, limit: int = 10, org_id: str = ORG_ID):
    """Top-selling device models for the period (by activation volume) to prioritize hotsheet updates."""
    from datetime import date as _date
    client = sb()
    year, month = _period_ym(period)
    plabel = _date(year, month, 1).strftime("%B %Y")

    sales = (client.schema("commcalc").table("raw_sales")
             .select("product_desc,contract_type,ext_price")
             .eq("org_id", org_id).eq("period", plabel)
             .neq("contract_type", "").execute().data) or []

    # Aggregate by device model (strip promo suffix after " - " for cleaner grouping)
    models = {}
    for s in sales:
        desc = (s.get("product_desc") or "").strip()
        if not desc:
            continue
        # Only count rows that look like a device (has a category-ish device name, not a plan)
        # Heuristic: device rows usually contain a model + " - " promo, or brand keywords
        base = desc.split(" - ")[0].strip()
        low = base.lower()
        if not any(k in low for k in ["iphone","samsung","galaxy","moto","pixel","celero","tcl","summit","apple","motorola","ipad","watch","tab"]):
            continue
        if base not in models:
            models[base] = {"model": base, "units": 0, "sample_desc": desc}
        models[base]["units"] += 1

    ranked = sorted(models.values(), key=lambda x: x["units"], reverse=True)[:limit]
    return {"period": period, "top_sellers": ranked}


@router.get("/device-history")
async def get_device_history(q: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """DEVICE HISTORY LOOKUP (commission-16) — employee-portal widget backend. Enter an IMEI OR a phone
    number (one box; the shape is auto-detected but BOTH keys are searched). Returns, org-scoped and
    carrier-agnostic:
      • device + sale from the B2B sales data (raw_sales -> daily_sales_feed fallback): model, sold
        date, sale price. Match IMEI->serial_1, phone->mdn.
      • activation + tenure from the tenant's RESIDUAL data (raw_mi): first residual period =
        activation; months active = COUNT of DISTINCT residual periods (residual-months, NOT calendar).
      • a salesperson-facing prompt: NOT sold by us -> sell a NEW phone; sold by us -> offer an UPGRADE.
      • (admin-only, gated by the 'device_commission' DATA_GRANT) a per-period MONEY table with
        COMMISSION (raw_mi residual MI+ATU) and REBATE (raw_payment_detail reimbursement classes) as
        SEPARATE categories, each with a subtotal + a grand total.
    READ-ONLY: DISPLAY of already-recorded data, no pay-path change."""
    require_org(org_id)
    q = (q or "").strip()
    if not q:
        raise HTTPException(400, "Enter an IMEI or phone number.")
    client = sb()
    cands = device_history.query_candidates(q)
    shape = device_history.detect_shape(q)

    def _rows(table, cols, key_cols):
        out = []
        for kc in key_cols:
            try:
                out += (client.schema("commcalc").table(table).select(cols)
                        .eq("org_id", org_id).in_(kc, cands).limit(2000).execute().data) or []
            except Exception as e:
                print(f"WARN device-history {table}.{kc} read failed: {e}")
        return out

    # ── B2B SALE (raw_sales authoritative, daily_sales_feed fallback) ──────────────────────────────
    # gp is selected so the at-sale POS cost can be derived (ext_price − GP) — there is no explicit
    # cost column on raw_sales / daily_sales_feed; the 78-col export carries GP + Ext Price.
    sale_cols = "trans_id,trans_date,store,salesperson,product_desc,contract_type,ext_price,gp,mdn,serial_1"
    sale_rows = [r for r in _rows("raw_sales", sale_cols, ("serial_1", "mdn"))
                 if device_history.keys_match(cands, r.get("serial_1"), r.get("mdn"))]
    sale_source = "raw_sales" if sale_rows else None
    if not sale_rows:
        feed = [r for r in _rows("daily_sales_feed", sale_cols, ("serial_1", "mdn"))
                if device_history.keys_match(cands, r.get("serial_1"), r.get("mdn"))]
        if feed:
            sale_rows, sale_source = feed, "daily_sales_feed"

    sold_by_us = bool(sale_rows)
    device = None
    if sale_rows:
        # the DEVICE line: prefer a row carrying a serial (the handset line), else the biggest-ticket line.
        dev = max(sale_rows, key=lambda r: (1 if str(r.get("serial_1") or "").strip() else 0,
                                            safe_float(r.get("ext_price"))))
        dates = sorted(str(r.get("trans_date"))[:10] for r in sale_rows if r.get("trans_date"))
        device = {
            "phone_model": (dev.get("product_desc") or "").strip() or None,
            "sold_by_us": True,
            "sold_date": dates[0] if dates else (str(dev.get("trans_date"))[:10] if dev.get("trans_date") else None),
            "sale_price": round(safe_float(dev.get("ext_price")), 2),
            "gp": (round(safe_float(dev.get("gp")), 2) if dev.get("gp") is not None else None),
            "store": (dev.get("store") or "").strip() or None,
            "salesperson": (dev.get("salesperson") or "").strip() or None,
            "contract_type": (dev.get("contract_type") or "").strip() or None,
            "sale_source": sale_source,
            "mdn": (dev.get("mdn") or "").strip() or None,
            "imei": (dev.get("serial_1") or "").strip() or None,
        }

    # ── RESIDUAL / MI (raw_mi across ALL periods → activation + tenure + commission rows) ──────────
    mi_cols = "period,device_serial,phone_number,actual_mi_payout,actual_atu_payout,customer_plan,subscriber_status"
    mi_rows = [r for r in _rows("raw_mi", mi_cols, ("device_serial", "phone_number"))
               if device_history.keys_match(cands, r.get("device_serial"), r.get("phone_number"))]
    tenure = device_history.tenure_from_periods([r.get("period") for r in mi_rows])
    mi_matches = [{"period": r.get("period"),
                   "amount": safe_float(r.get("actual_mi_payout")) + safe_float(r.get("actual_atu_payout"))}
                  for r in mi_rows]

    # ── REBATES (raw_payment_detail across ALL periods) ───────────────────────────────────────────
    pay_cols = "imei,mdn,payment_type,amount,period_month,period_year,payment_date"
    pay_rows = [r for r in _rows("raw_payment_detail", pay_cols, ("imei", "mdn"))
                if device_history.keys_match(cands, r.get("imei"), r.get("mdn"))]

    def _pay_period(r):
        try:
            mo, yr = int(r.get("period_month") or 0), int(r.get("period_year") or 0)
            if 1 <= mo <= 12 and yr:
                return device_history.canon_display_period(f"{yr}-{mo:02d}")
        except Exception:
            pass
        return str(r.get("payment_date"))[:7] if r.get("payment_date") else ""
    payment_matches = [{"period": _pay_period(r), "amount": safe_float(r.get("amount")),
                        "payment_type": r.get("payment_type")} for r in pay_rows]

    # ── AGING + OUR PURCHASE PRICE (UNGATED — reps may see cost per owner directive 2026-07-17) ─────
    # v2: the purchase price is UNIVERSAL / POS-SKU based — owed_to_vip is NOT universal (VIP/house
    # only) so it is DEMOTED to last resort. Source priority (first real number wins), all org-scoped:
    #   ① per-IMEI inventory-aging cost   commcalc.inventory_aging_device.unit_cost   (POS/SKU, universal)
    #   ② at-sale POS cost                raw_sales/daily_sales_feed  ext_price − GP   (universal, b2bsoft)
    #   ③ MA marketplace order price      raw_ma_commission.imei → .activation_order →
    #                                     raw_ma_fulfillment.order_number → .price     (Total/MA)
    #   ④ asset_ledger.raw_row explicit device-cost column                             (VIP/house)
    #   ⑤ asset_ledger.owed_to_vip — LAST RESORT, VIP billing basis (house only)
    # A tenant with none of these gets an HONEST 'no cost on file' line (never a fabricated 0).
    from datetime import date as _dh_date
    today_iso = _dh_date.today().isoformat()
    sale_sold_date = device.get("sold_date") if device else None

    # asset_ledger (VIP / asset-lending — house aging path + sources ④⑤). Org-scoped via `_rows`.
    asset_cols = ("id,esn_imei,phone_number,store,market,device_model,category,status,acquired_date,"
                  "due_date,payg_date,reimbursement_date,trigger_date,billing_friday,bill_path,"
                  "date_sold,owed_to_vip,reimbursement,selling_price,raw_row")
    asset_rows = [r for r in _rows("asset_ledger", asset_cols, ("esn_imei", "phone_number"))
                  if device_history.keys_match(cands, r.get("esn_imei"), r.get("phone_number"))]
    asset_row = None
    if asset_rows:
        asset_row = max(asset_rows, key=lambda r: (
            1 if device_history.keys_match(cands, r.get("esn_imei")) else 0,
            str(r.get("acquired_date") or "")))

    # per-IMEI inventory-aging device row (source ① + non-VIP aging path). Latest snapshot wins.
    inv_cols = "id,imei,serial,sku,item,store,unit_cost,received_date,days_in_stock,as_of_date"
    inv_rows = [r for r in _rows("inventory_aging_device", inv_cols, ("imei", "serial"))
                if device_history.keys_match(cands, r.get("imei"), r.get("serial"))]
    inv_row = max(inv_rows, key=lambda r: str(r.get("as_of_date") or "")) if inv_rows else None

    # MA marketplace linkage (source ③): imei → raw_ma_commission.activation_order → fulfillment price.
    ma_comm_rows = [r for r in _rows("raw_ma_commission", "imei,activation_order,sku,tx_date", ("imei",))
                    if device_history.keys_match(cands, r.get("imei"))]
    ma_fulfil_rows = []
    ord_cands = device_history.order_candidates(
        [r.get("activation_order") for r in ma_comm_rows])
    if ord_cands:
        try:
            ma_fulfil_rows = (client.schema("commcalc").table("raw_ma_fulfillment")
                              .select("order_number,price,product_name,date_ordered")
                              .eq("org_id", org_id).in_("order_number", ord_cands)
                              .limit(2000).execute().data) or []
        except Exception as e:
            print(f"WARN device-history raw_ma_fulfillment read failed: {e}")
    ma_price = device_history.pick_ma_marketplace_price(ma_comm_rows, ma_fulfil_rows)

    # aging: house/VIP asset_ledger when present, else the non-VIP inventory-aging device row.
    if asset_row:
        aging = device_history.build_aging(asset_row, sale_sold_date, today_iso)
    elif inv_row:
        aging = device_history.build_aging_inventory(inv_row, sale_sold_date, today_iso)
    else:
        aging = device_history.build_aging(None, sale_sold_date, today_iso)

    # our purchase price — source-priority pick (①→⑤) with provenance (UNGATED).
    price_candidates = []
    # ① per-IMEI inventory-aging cost (POS/SKU — universal)
    if inv_row:
        inv_amt, inv_sku = device_history.inv_device_cost(inv_row)
        if inv_amt is not None:
            price_candidates.append({
                "amount": inv_amt, "source": "inventory_aging_device.unit_cost",
                "label": ("POS inventory cost" + (f" — SKU {inv_sku}" if inv_sku else " (SKU-based)"))})
    # ② at-sale POS cost = ext_price − GP (universal for every b2bsoft tenant incl. house)
    if device:
        pos_cost = device_history.pos_cost_from_sale(device.get("sale_price"), device.get("gp"))
        if pos_cost is not None:
            price_candidates.append({
                "amount": pos_cost, "source": f"{sale_source or 'raw_sales'} (ext − GP)",
                "label": "POS sale line (ext − GP)"})
    # ③ MA marketplace order price (Total/MA tenants)
    if ma_price.get("found"):
        price_candidates.append({
            "amount": ma_price["amount"], "source": "raw_ma_fulfillment.price",
            "label": ("MA marketplace order" + (f" {ma_price['order_number']}" if ma_price.get("order_number") else ""))})
    # ④ asset_ledger.raw_row explicit device-cost column (VIP/house)
    if asset_row:
        rr_amt, rr_hdr = device_history.scan_raw_row_price((asset_row.get("raw_row") or {}))
        if rr_amt is not None:
            price_candidates.append({"amount": rr_amt,
                                     "source": f"asset_ledger.raw_row[{rr_hdr}]",
                                     "label": f"VIP asset ledger — {rr_hdr}"})
        # ⑤ asset_ledger.owed_to_vip — LAST RESORT, VIP billing basis (house only)
        owed = safe_float(asset_row.get("owed_to_vip"))
        price_candidates.append({"amount": (round(owed, 2) if owed > 0 else None),
                                 "source": "asset_ledger.owed_to_vip",
                                 "label": "VIP billing basis (house — Owed to VIP)"})
    purchase_price = device_history.pick_purchase_price(price_candidates)

    # ── MONEY TABLE (gated: admin-only by default via the 'device_commission' grant) ───────────────
    from app.modules.commcalc.discrepancy_engine import parse_payment_type
    can_money = _can_view_device_commission(authorization, org_id)
    money, money_locked = None, None
    if can_money:
        money = device_history.build_money_table(
            mi_matches, payment_matches, lambda pt: parse_payment_type(pt)[0])
    else:
        money_locked = {"note": "Commission details are restricted — this requires the "
                                "'device_commission' data grant (admin-only by default)."}

    return {
        "query": q, "detected": shape, "org_id": org_id,
        "found": bool(sale_rows or mi_rows or pay_rows or asset_rows or inv_rows or ma_comm_rows),
        "device": device,
        "sold_by_us": sold_by_us,
        "prompt": device_history.prompt_for(sold_by_us, device.get("sold_date") if device else None),
        "tenure": tenure,
        "residual_periods": tenure["months_active"],
        "aging": aging,
        "purchase_price": purchase_price,
        "commission_visible": can_money,
        "money": money,
        "money_locked": money_locked,
    }


@router.get("/sales-analyzer/{period}")
async def get_sales_analyzer(period: str, window_days: int = 90, rep: str = "",
                            authorization: str = Header(default=""), org_id: str = ORG_ID):
    """3-Month Retention (3MR) behavior per rep: each rep's activations from 3 months before
    `period` and which churned (cancelled/ported/suspended/deactivated) before their 3rd bill
    (within window_days). Returns per-rep summary + churned line items (model, MRC, sold-for,
    dates, store). RBAC-scoped: a non-admin manager only sees subscribers in their stores."""
    require_org(org_id)
    from app.modules.storeops.router import scope_keyset
    ks = scope_keyset(authorization, org_id)
    try:
        return sales_analyzer.analyze(sb(), org_id, period, window_days=window_days, rep=rep, store_keys=ks)
    except Exception as e:
        raise HTTPException(500, f"sales-analyzer failed: {type(e).__name__}: {e}")


@router.get("/comp/residual-trend")
async def get_comp_residual_trend(months: int = 6, store: str = "", market: str = "",
                                  min_drop_pct: float = 20.0, min_drop_amt: float = 1.0,
                                  authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Month-over-month carrier residual (Comprehensive Comp) trend. Returns total residual per
    month with deltas, plus per-account DIPS (residual fell or the account vanished from the
    report = likely cancellation) labeled by the month each dip occurred — so you can see which
    month a residual dropped and why."""
    require_org(org_id)
    _require_carrier_residual(authorization, org_id)   # carrier-residual visibility gate (mig 201)
    try:
        return comp_trend.compute_residual_trend(
            sb(), org_id, months=months, store=store, market=market,
            min_drop_pct=min_drop_pct, min_drop_amt=min_drop_amt)
    except Exception as e:
        raise HTTPException(500, f"comp-residual-trend failed: {type(e).__name__}: {e}")


@router.get("/comp/rep-pay-trend")
async def get_comp_rep_pay_trend(months: int = 6, store: str = "", org_id: str = ORG_ID):
    """Per-REP commission trend — the commission WE ACTUALLY PAY each rep (rep_commissions.total_payout)
    month over month. This is the per-rep number the Total Compensation page was missing (its other
    view is account-level carrier comp). One row per rep with each kept month's payout + a total."""
    require_org(org_id)
    try:
        client = sb()
        result = comp_trend.compute_rep_pay_trend(client, org_id, months=months, store=store)
        # RULE FIVE (§3d): stamp each rep row with its `market` (store_mapping resolver) so the Total
        # Compensation page can market-filter the per-rep view client-side. Additive + org-scoped.
        _resolve_market, _ = _store_market_resolver(client, org_id)
        for rr in (result.get('reps') or []):
            rr['market'] = _resolve_market(rr.get('store'))
        return result
    except Exception as e:
        raise HTTPException(500, f"comp-rep-pay-trend failed: {type(e).__name__}: {e}")


# ── Daily Sales Targets ──────────────────────────────────────
_MONTH_TOKENS = {
    'january', 'february', 'march', 'april', 'may', 'june', 'july',
    'august', 'september', 'october', 'november', 'december',
}


def _period_bounds(period: str, today_override: str = ""):
    """Return (month_start, next_month_start, resolved_today) for a period label.

    `today_override` (YYYY-MM-DD) lets the client pass its *local* date so "today"
    isn't computed in the server's UTC clock — critical for an evening sales floor
    where UTC has already rolled to tomorrow. Falls back to the server date.
    """
    # parse_period silently falls back to January on an unknown month; guard here
    # so a malformed period fails loudly instead of returning wrong bounds.
    if not period or period.lower().split()[0] not in _MONTH_TOKENS:
        raise HTTPException(400, f"Unrecognized period '{period}' (expected 'Month YYYY')")
    pm = parse_period(period)
    year, month = pm['year'], pm['month']
    start = _date(year, month, 1)
    last_day = _calendar.monthrange(year, month)[1]
    end = _date(year, month, last_day) + _timedelta(days=1)
    real = _date.today()
    if today_override:
        try:
            real = _date.fromisoformat(today_override[:10])
        except ValueError:
            pass  # ignore a malformed override, keep server date
    if real < start:
        today = start                       # future period — nothing is "past" yet
    elif real >= end:
        today = _date(year, month, last_day)  # past period — whole month is realized
    else:
        today = real
    return start, end, today


def _rep_canon_map(client, org_id=ORG_ID):
    """alias(UPPER) -> canonical rep name. From commcalc.name_map (epay_salesperson ->
    storeops_name) + commcalc.rep_aliases (user merges, override). Degrades to {} if absent."""
    m = {}
    try:
        for n in (client.schema('commcalc').table('name_map')
                  .select('epay_salesperson,storeops_name').eq('org_id', org_id).execute().data or []):
            a = (n.get('epay_salesperson') or '').strip().upper()
            c = (n.get('storeops_name') or '').strip()
            if a and c:
                m[a] = c
    except Exception:
        pass
    try:
        for r in (client.schema('commcalc').table('rep_aliases')
                  .select('alias,canonical').eq('org_id', org_id).execute().data or []):
            a = (r.get('alias') or '').strip().upper()
            c = (r.get('canonical') or '').strip()
            if a and c:
                m[a] = c
    except Exception:
        pass
    return m


def _canon(name, cmap):
    if not name:
        return name
    return cmap.get(name.strip().upper(), name)


def _fetch_shifts(client, start, end, org_id=ORG_ID):
    rows = (client.schema('storeops').table('shifts')
            .select('employee_name,store_code,shift_date,scheduled_hours,is_deleted')
            .eq('org_id', org_id)
            .gte('shift_date', start.isoformat())
            .lt('shift_date', end.isoformat())
            .limit(50000).execute().data) or []
    cmap = _rep_canon_map(client, org_id)
    for r in rows:
        r['employee_name'] = _canon(r.get('employee_name'), cmap)
    return rows


def _is_open_month(period):
    """True if `period` is the current in-progress calendar month. Handles BOTH 'June 2026' and the
    '2026-07' shape (parse_period only understands the month-name form → it silently mapped '2026-07'
    to January, so July read as a CLOSED month — the source-selection bug)."""
    try:
        p = str(period).strip()
        if len(p) >= 7 and p[:4].isdigit() and p[4] == '-' and p[5:7].isdigit():
            yr, mo = int(p[:4]), int(p[5:7])
        else:
            pm = parse_period(period)
            yr, mo = pm['year'], pm['month']
        t = _date.today()
        return mo == t.month and yr == t.year
    except Exception:
        return False


_BOX_DEPTS = {'Android - XP', 'IPHONE - XP', 'TABLET - XP'}


def _open_month_source(client, org_id, period):
    """(primary, other) sales tables for a period. Closed month → the authoritative raw_sales first.
    Open month → the daily feed first, EXCEPT when the feed has NO Category-bearing rows for the period
    but raw_sales does — then raw_sales (the richer custom data) leads. Keeps a Legacy (Category-blank)
    feed from masking a better raw_sales for the open month. No-op in the healthy state where the feed
    already carries Category. Best-effort — any error falls back to the plain feed-first rule."""
    if not _is_open_month(period):
        return 'raw_sales', 'daily_sales_feed'
    primary, other = 'daily_sales_feed', 'raw_sales'
    try:
        def _cat(tbl):
            return (client.schema('commcalc').table(tbl).select('id', count='exact')
                    .eq('org_id', org_id).in_('period', _pvariants(period))
                    .neq('category', '').limit(1).execute().count) or 0
        if _cat('daily_sales_feed') == 0 and _cat('raw_sales') > 0:
            primary, other = 'raw_sales', 'daily_sales_feed'
    except Exception:
        pass
    return primary, other


# Default column projection for the display sales resolver — the exact set every reader agrees on and
# that exists in BOTH raw_sales and daily_sales_feed (selecting a feed-absent column like `sku` throws;
# see the sales-report/detail note). Callers may pass their own `cols`.
_SALES_DISPLAY_COLS = ("trans_id,trans_date,store,salesperson,department,category,product_desc,"
                       "contract_type,ext_price,gp,voided,trans_type")


# ── Feed↔raw_sales merge / dedupe / promotion-mutex primitives (all pure + unit-testable) ──────────
# Extracted so the promotion-dedup + display day-pick logic can be tested without a live DB
# (backend/scratchpad/promotion_dedup_proof.py). Introduced 2026-07-16 for the luxelink July 2026
# feed-less-day compounding-duplication incident.

# Day-grain "richer source wins" thresholds — a deliberate MIRROR of the ingest price-guard
# (_SHRINK_* / router.py upload_file: protect a day at existing >= 50 priced rows, refuse it at
# incoming < 0.5 x existing). Same 50-row floor + 50% ratio, applied to READS so the display never
# shows a degraded copy of a day when the other table holds a materially fuller copy of that same day.
_RICHER_DAY_MIN_ROWS = 50
_RICHER_DAY_RATIO = 0.5


def _merge_days_richer(prows, orows, day_fn, min_rows=_RICHER_DAY_MIN_ROWS, ratio=_RICHER_DAY_RATIO):
    """Per-DAY merge of a PRIMARY and OTHER row set. The primary keeps every day it has EXCEPT a day
    whose primary copy is DEGRADED versus the other's — i.e. the other holds >= `min_rows` rows for that
    day AND the primary holds < `ratio` x the other's count for it (the price-guard 50%/50-row rule). On
    such a day the other's richer copy is used and the day is reported as SWAPPED. Days the primary lacks
    entirely are FILLED from the other (the prior behaviour). Blank-day rows (day_fn falsy) on the primary
    are always kept; blank-day rows on the other are dropped (can't be day-compared). Primary order is
    preserved for kept rows; other-source rows are appended.

    Returns (merged_rows, swapped_days_sorted, filled_days_sorted). Pure — no I/O, never raises."""
    if not prows:
        odays = sorted({day_fn(r) for r in orows if day_fn(r)})
        return list(orows), [], odays
    p_cnt, o_cnt = {}, {}
    for r in prows:
        d = day_fn(r)
        if d:
            p_cnt[d] = p_cnt.get(d, 0) + 1
    for r in orows:
        d = day_fn(r)
        if d:
            o_cnt[d] = o_cnt.get(d, 0) + 1
    pdays = set(p_cnt)
    swapped = sorted(d for d, oc in o_cnt.items()
                     if oc >= min_rows and p_cnt.get(d, 0) > 0 and p_cnt.get(d, 0) < ratio * oc)
    swap_set = set(swapped)
    filled = sorted(d for d in o_cnt if d not in pdays)
    kept_primary = [r for r in prows if day_fn(r) not in swap_set]
    add_from_other = [r for r in orows
                      if day_fn(r) and (day_fn(r) in swap_set or day_fn(r) not in pdays)]
    return kept_primary + add_from_other, swapped, filled


# Cell-grain (day × store) "richer source wins" thresholds — the store-aware successor to the day-grain
# rule above. The day-grain merge masked EVERY store on a day the primary led: the luxelink July 2026
# incident — the daily b2bsoft feed carries only ~6 NY stores, so on a feed-led day the freshly
# re-uploaded raw_sales rows for the other ~13 stores (Chicago / NJ) were hidden, and only the feed-less
# days showed all stores. Merging at (day × store) grain lets the primary win only the CELLS it actually
# holds; every other store's cell fills from the other source. The row FLOOR is scaled DOWN from the
# day-level 50 to 10 (a single store-day is far fewer rows than a whole day); the 50% ratio is unchanged.
_RICHER_CELL_MIN_ROWS = 10
_RICHER_CELL_RATIO = 0.5


def _cell_store_key(store):
    """Cheap canonicalization for the store half of a (day, store) cell key: strip, collapse internal
    whitespace, casefold. NO store_mapping / resolver lookup (deliberately out of scope) — just enough
    that a feed copy and a raw_sales copy of the SAME store don't split into two cells over case /
    whitespace drift (which would double-count that store-day). The row's ORIGINAL store string is
    preserved in the merged output; this key only decides which source wins a cell."""
    return ' '.join(str(store or '').split()).casefold()


def _merge_cells_richer(prows, orows, cell_fn, min_rows=_RICHER_CELL_MIN_ROWS, ratio=_RICHER_CELL_RATIO):
    """Per-(day × store)-CELL merge of a PRIMARY and OTHER row set — the store-aware successor to
    `_merge_days_richer`. The primary keeps every cell it has EXCEPT a cell whose primary copy is
    DEGRADED versus the other's — the other holds >= `min_rows` rows for that cell AND the primary holds
    < `ratio` x the other's count (the ingest price-guard rule, scaled to store-day cells). On such a
    cell the other's richer copy is used and the cell is reported as RICHER (swapped). Cells the primary
    lacks entirely are FILLED from the other. Rows whose `cell_fn` is falsy (blank day) on the primary
    are always kept; such rows on the other are dropped (can't be cell-compared). Primary order preserved
    for kept rows; other-source rows appended.

    Returns (merged_rows, richer_cells_sorted, filled_cells_sorted), each cell a (day, store_key) tuple.
    Pure — no I/O, never raises."""
    if not prows:
        ocells = sorted({c for c in (cell_fn(r) for r in orows) if c})
        return list(orows), [], ocells
    p_cnt, o_cnt = {}, {}
    for r in prows:
        c = cell_fn(r)
        if c:
            p_cnt[c] = p_cnt.get(c, 0) + 1
    for r in orows:
        c = cell_fn(r)
        if c:
            o_cnt[c] = o_cnt.get(c, 0) + 1
    pcells = set(p_cnt)
    richer = sorted(c for c, oc in o_cnt.items()
                    if oc >= min_rows and p_cnt.get(c, 0) > 0 and p_cnt.get(c, 0) < ratio * oc)
    richer_set = set(richer)
    filled = sorted(c for c in o_cnt if c not in pcells)
    kept_primary = [r for r in prows if cell_fn(r) not in richer_set]
    add_from_other = [r for r in orows
                      if cell_fn(r) and (cell_fn(r) in richer_set or cell_fn(r) not in pcells)]
    return kept_primary + add_from_other, richer, filled


def _row_content_sig(row, drop_keys=('id', 'created_at')):
    """A stable, order-independent full-content signature of a row: every column except `drop_keys`,
    values normalized to str (None → '') so ('5' vs 5) and (None vs '') can't split a true duplicate.
    Used to collapse read-skew / compounded duplicate rows. NOTE: two genuinely-identical line items on
    one real ticket produce the same signature and WILL collapse to one — accepted, because the
    authoritative monthly re-upload restores exact per-line truth; the feed merge is a self-heal, not the
    system of record."""
    drop = set(drop_keys)
    return tuple(sorted((k, '' if v is None else str(v)) for k, v in row.items() if k not in drop))


def _dedupe_rows(rows, drop_keys=('id', 'created_at')):
    """Dedupe rows on their full-content signature (`_row_content_sig`), keeping the FIRST occurrence of
    each signature. Returns (deduped_rows, dropped_count). Pure — no I/O."""
    seen, out, dropped = set(), [], 0
    for r in rows:
        sig = _row_content_sig(r, drop_keys)
        if sig in seen:
            dropped += 1
            continue
        seen.add(sig)
        out.append(r)
    return out, dropped


# In-process, per-(org_id, period) promotion mutex. Railway runs a SINGLE process, so a module-level
# lock table is sufficient to serialize the hourly email-sweep promotion side-effect against the
# scheduled _promote_all_due (and any manual promote-feed): two overlapping delete-then-insert cycles
# for the same org+period would otherwise interleave into a double-count. Non-blocking by design — a
# second concurrent caller SKIPS rather than queueing.
_PROMO_LOCKS = {}
_PROMO_LOCKS_GUARD = threading.Lock()


def _promo_lock_for(org_id, period_key):
    """Return the shared threading.Lock for one (org_id, period_key), creating it under the guard lock
    on first use (so two threads racing to create it get the SAME lock object)."""
    key = (org_id, period_key)
    with _PROMO_LOCKS_GUARD:
        lk = _PROMO_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _PROMO_LOCKS[key] = lk
        return lk


def _sales_rows_union(client, org_id, period, cols=_SALES_DISPLAY_COLS):
    """THE canonical DISPLAY sales source: a per-DAY UNION of daily_sales_feed and raw_sales.

    The open-month PRIMARY source (per `_open_month_source`) wins every day it has rows for; the OTHER
    source fills only the days the primary lacks. This is promotion's feed-wins merge (see
    `_promote_feed_to_raw_sales`) applied at day grain to READS, so a manually-uploaded raw_sales month
    can NEVER be invisible behind a partial feed (the luxelink July 2026 incident: feed days 1-8 + a
    hand-uploaded raw_sales 1-13 → the union shows 1-13, not a truncated 1-8), and a promoted raw_sales
    month is never masked by a stale feed. Closed month → raw_sales leads, feed fills gaps.

    Per-(day × store)-CELL pick WITH a degradation guard (2026-07-16): the primary leads each store-day
    CELL it has UNLESS its copy of that cell is degraded versus the other source's — the other holds
    >= 10 rows for the cell AND the primary holds < 50% of that (a MIRROR of the ingest price-guard,
    scaled to store-day grain) — in which case the other's richer copy of that cell is shown and it is
    counted in meta['richer_cells']. Cells the primary lacks are filled from the other. STORE-grain (not
    the old day-grain) is what stops a partial feed from MASKING other stores: the luxelink July 2026
    incident — the daily feed carries only ~6 NY stores, so on a feed-led day the re-uploaded raw_sales
    rows for the other ~13 stores (Chicago / NJ) were dropped; now only the feed's own store cells win
    and every other store fills from raw_sales, so all stores show on every day. meta reports store
    coverage (`stores_shown` / `primary_stores` / `stores_from_other`) so the page can show which source
    covered what.

    NEVER raises: a failed read on either table degrades to whatever the other returned (so a column
    drift on one side can't 500 the page). Returns `(rows, meta)`; `meta` carries the per-source counts,
    which source led, which days were filled from the other, and which days were swapped to the richer
    source (`richer_days`) — for the report's transparency line.

    DISPLAY ONLY. The commission CALC path (`_run_calculation`/`_fetch_sales_unified`,
    `commission_engine._read_sales`) is deliberately NOT wired to this — that is money and gets its own
    gate; this function must not be called from a payout path."""
    primary, other = _open_month_source(client, org_id, period)

    def _q(table):
        try:
            return (client.schema('commcalc').table(table).select(cols)
                    .eq('org_id', org_id).in_('period', _pvariants(period))
                    .limit(200000).execute().data) or []
        except Exception as e:
            print(f"WARN _sales_rows_union read of {table} failed: {e}")
            return []

    prows = _q(primary)
    orows = _q(other)

    def _day(r):
        return str(r.get('trans_date') or '')[:10]

    def _cell(r):
        d = _day(r)
        return (d, _cell_store_key(r.get('store'))) if d else None

    # Primary leads each (day × store) CELL it has; a cell where the other source is materially richer
    # (>= 10 rows AND the primary < 50% of it) is SWAPPED to the other; cells the primary lacks are
    # FILLED from the other. Store-grain (not day-grain) so a feed carrying only some stores can no longer
    # mask the other stores' rows on a day it leads (the luxelink July 2026 incident).
    merged, richer_cells, filled_cells = _merge_cells_richer(prows, orows, _cell)

    feed_rows = len(prows) if primary == 'daily_sales_feed' else len(orows)
    raw_rows = len(prows) if primary == 'raw_sales' else len(orows)
    filled_set, richer_set = set(filled_cells), set(richer_cells)
    fill_rows = sum(1 for r in orows if _cell(r) in filled_set)
    richer_rows = sum(1 for r in orows if _cell(r) in richer_set)
    # Day-level rollups of the cell picks — kept so the existing sales-report transparency line
    # (`filled_days` / `richer_days`) still reads; now "days on which >= 1 store cell was filled / swapped".
    filled_days = sorted({c[0] for c in filled_cells})
    richer_days = sorted({c[0] for c in richer_cells})

    # Store coverage — the transparency the page needs to SEE that all stores are present (not just the
    # feed's ~6): how many distinct stores each side holds, how many the merged view shows, and how many
    # only appear because of the other source.
    def _stores_of(rs):
        return {_cell_store_key(r.get('store')) for r in rs if str(r.get('store') or '').strip()}
    p_stores, o_stores, shown_stores = _stores_of(prows), _stores_of(orows), _stores_of(merged)
    stores_from_other = sorted(s for s in shown_stores if s and s not in p_stores)

    meta = {
        'primary': primary, 'other': other,
        'primary_rows': len(prows), 'other_rows': len(orows),
        'feed_rows': feed_rows, 'raw_rows': raw_rows,
        'filled_rows': fill_rows, 'filled_days': filled_days, 'filled_cells': len(filled_cells),
        'richer_rows': richer_rows, 'richer_days': richer_days, 'richer_cells': len(richer_cells),
        'shown_rows': len(merged),
        'stores_shown': len(shown_stores), 'primary_stores': len(p_stores),
        'other_stores': len(o_stores), 'stores_from_other': len(stores_from_other),
    }
    return merged, meta


def _sales_rows_union_txn(client, org_id, period, cols=_SALES_DISPLAY_COLS):
    """TRANSACTION-grain union of daily_sales_feed and raw_sales, deduped by trans_id — the READ-side
    mirror of `_promote_feed_to_raw_sales`'s merge. The open-month PRIMARY source (`_open_month_source`;
    the feed for the open month) wins a WHOLE trans_id; the OTHER source contributes only transactions the
    primary lacks (`monthly_only` in promotion). Every LINE ITEM of a kept transaction is preserved
    (raw_sales/feed are line-item grain), so accessory / tax ext_price sums stay correct, and a trans_id
    present in BOTH tables is counted ONCE — never double-counted across sources.

    Differs from `_sales_rows_union` (per-DAY): this keeps a raw_sales transaction the feed doesn't have
    even on a day the feed also sold — the completeness the Daily-Targets actuals + Tax drill-down need.
    In the healthy feed-only state (raw_sales empty or fully promoted from the feed) the result is the feed
    verbatim → byte-identical to reading the feed alone. NEVER raises. Returns (rows, meta)."""
    primary, other = _open_month_source(client, org_id, period)

    def _q(table):
        try:
            return (client.schema('commcalc').table(table).select(cols)
                    .eq('org_id', org_id).in_('period', _pvariants(period))
                    .limit(200000).execute().data) or []
        except Exception as e:
            print(f"WARN _sales_rows_union_txn read of {table} failed: {e}")
            return []

    prows = _q(primary)
    orows = _q(other)
    ptids = {str(r.get('trans_id')).strip() for r in prows if str(r.get('trans_id') or '').strip()}
    # `monthly_only`: other-source transactions whose trans_id isn't in the primary (blank trans_id → ''
    # → not in ptids → kept, exactly as promotion keeps a blank-trans_id monthly row).
    extra = [r for r in orows if str(r.get('trans_id') or '').strip() not in ptids]
    merged = list(prows) + extra
    feed_rows = len(prows) if primary == 'daily_sales_feed' else len(orows)
    raw_rows = len(prows) if primary == 'raw_sales' else len(orows)
    meta = {
        'primary': primary, 'other': other,
        'primary_rows': len(prows), 'other_rows': len(orows),
        'feed_rows': feed_rows, 'raw_rows': raw_rows,
        'other_only_rows': len(extra), 'shown_rows': len(merged),
        'primary_trans': len(ptids),
    }
    return merged, meta


_ACTUALS_COLS = ("trans_id,trans_date,store,salesperson,user_login,contract_type,department,category,"
                 "product_desc,gp,ext_price,voided,trans_type")


# ── THE ONE shared per-(store, rep, day) sales aggregation ─────────────────────────────────────────
# The Sales Report, Executive MTD and Daily Targets ALL consume this single row-level pass so they can
# never disagree again (owner directive 2026-07-16: "the Sales Report is CORRECT — Exec MTD should take
# its cumulative numbers from there, and Daily Targets should flow from the Sales Report too"). Before
# this, each of the three had its OWN loop with slightly different rules — which is exactly how Exec MTD
# "kept taking data from somewhere" the Sales Report dropped (see the divergence table in the handoff):
#   • Exec counted activations PER LINE; the Sales Report counts DISTINCT trans_id (a 4-device AAL under
#     one trans_id was 4 in Exec, 1 in the Sales Report — the dominant inflation).
#   • Exec did NOT skip Return rows or the blank/'admin' rep; the Sales Report + Targets do.
#   • Exec classified activations from exec_metric_config tokens ("any non-blank contract_type = an
#     activation"); the Sales Report uses the shared classify_contract_type (recognized labels only).
#   • Exec measured accessory$ from its OWN exec_metric_config['accessory'] token match; the Sales
#     Report + Targets use the shared _is_accessory (the Classification-settings config).
# This function applies ONE canonical skip set + ONE classification (the shared classify_contract_type +
# _is_accessory, DISTINCT-trans_id counting) and returns per-cell bucket SETS + sums. Consumers roll it up
# their own way (Sales Report → the cell rows as-is; Exec MTD → by store / employee + an MTD date-cut +
# trending; Targets → by canonical store_code). Extension-only line metrics a single consumer needs (Exec
# phones / bill-payment / activation-fee / protect — b2bsoft per-line columns the Sales Report doesn't
# show; Targets box / bill-payment) are computed in the SAME pass so there is never a divergent second
# scan. Pure — no I/O, never raises. DISPLAY ONLY: the commission CALC path is deliberately NOT wired here.
_VOID_TOKENS = ('true', 'yes', '1', 'voided', 'void')   # the Sales Report's set = the source of truth


def _sales_cell_agg(rows, acfg, exec_cfg=None):
    """Aggregate raw sales lines → {(store, rep, day): cell}. `acfg` = _accessory_config(...) (the ONE
    accessory classifier). `exec_cfg` (optional _exec_metric_config result) turns ON the Executive-MTD
    extension line-metrics + the configurable Port sub-split; when None those are skipped (Sales Report /
    Targets). See the block comment above."""
    act_rules = (exec_cfg.get('activation', {}) or {}).get('rules', {}) if exec_cfg else {}
    agg = {}
    for r in rows:
        # ── THE canonical skip rules — shared by all three (was three slightly different predicates).
        if str(r.get('voided') or '').strip().lower() in _VOID_TOKENS:
            continue
        if str(r.get('trans_type') or '').strip() == 'Return':
            continue
        rep = str(r.get('salesperson') or '').strip()
        if not rep or rep.lower() == 'admin':
            continue
        store = str(r.get('store') or '').strip()
        date = str(r.get('trans_date') or '')[:10]
        tid = str(r.get('trans_id') or '').strip()
        ct = str(r.get('contract_type') or '')
        ctl = ct.lower()
        dept = str(r.get('department') or '').strip()
        ext = safe_float(r.get('ext_price'))
        gp = safe_float(r.get('gp'))
        k = (store, rep, date)
        a = agg.get(k)
        if not a:
            a = agg[k] = {'store': store, 'salesperson': rep, 'trans_date': date, 'login': None,
                          '_txn': set(), '_prem': set(), '_byod': set(), '_upg': set(),
                          '_port': set(), '_swap': set(), '_billpay': set(),
                          'lines': 0, 'revenue': 0.0, 'gp': 0.0, 'accessory_rev': 0.0, 'setup_fee_rev': 0.0,
                          'box_count': 0,
                          'total_phones': 0, 'bill_qty': 0, 'bill_amt': 0.0, 'activation_fee': 0.0,
                          'protect': 0}
        if a['login'] is None and r.get('user_login'):
            a['login'] = r.get('user_login')
        if tid:
            a['_txn'].add(tid)
        a['lines'] += 1
        a['revenue'] += ext
        a['gp'] += gp
        # SHARED activation classifier (money-adjacent; identical to commissions + targets), DISTINCT-txn.
        _cls = classify_contract_type(ct)
        if tid and _cls == 'byod':
            a['_byod'].add(tid)
        elif tid and _cls == 'upgrade':
            a['_upg'].add(tid)
        elif tid and _cls == 'premium':
            a['_prem'].add(tid)
            # Port is a SUB-split of premium/activation (never a redefinition of it) — the token stays
            # exec_metric_config-configurable; only needed when exec_cfg is present (Exec MTD).
            if exec_cfg and _exec_act_class(ct, act_rules) == 'port':
                a['_port'].add(tid)
        # Swaps — distinct-txn, contract_type contains 'swap' (independent tally; changes none of the above).
        if tid and 'swap' in ctl:
            a['_swap'].add(tid)
        # Accessory$ — the ONE shared _is_accessory classifier (all three agree; Exec MTD no longer uses
        # its own exec_metric_config['accessory'] token match for the number — see the handoff note).
        # DEVICE SET-UP FEE (mig 217) is tallied in its OWN accumulator, SEPARATE from accessory_rev, and
        # is EXCLUDED from accessory_rev so the two never double-count when the targets attainment folds
        # them together (accessory_rev + setup_fee_rev). For the house/Boost org the set-up-fee line lives
        # in a fee/'other' department (not the accessory department), so `not _setup` never removes an
        # accessory line → accessory_rev stays BYTE-IDENTICAL (Sales Report / Exec MTD / productivity
        # unchanged). Pre-217 / empty config → _is_setup_fee is False → no-op.
        _setup = _is_setup_fee(r.get('product_desc'), acfg)
        if _setup:
            a['setup_fee_rev'] += ext
        elif _is_accessory(dept, r.get('category'), r.get('product_desc'), acfg):
            a['accessory_rev'] += ext
        # Targets extension metrics. BOX departments are now CONFIG-DRIVEN (mig 218; acfg['box_departments'],
        # default = _BOX_DEPTS → house byte-identical) so boxes count correctly for non-Boost tenants across
        # every surface that shares this aggregation (Sales Report / conversion / Productivity / Review).
        if dept in (acfg.get('box_departments') or _BOX_DEPTS):
            a['box_count'] += 1
        _pl = str(r.get('product_desc') or '').lower()
        if tid and ('boost rtr' in _pl or 'xfinity prepaid refill' in _pl):
            a['_billpay'].add(tid)
        # Executive-MTD extension LINE metrics — b2bsoft per-line columns (phones / bill payment /
        # activation fee / protect) the Sales Report doesn't carry, so they have NO equality requirement;
        # computed only when exec_cfg is supplied (config-driven, SAP-configurable, unchanged semantics).
        if exec_cfg:
            _d, _c = dept.lower(), str(r.get('category') or '').strip().lower()
            if _exec_line_match(exec_cfg['phones']['rules'], _d, _c, _pl):
                a['total_phones'] += 1
            if _exec_line_match(exec_cfg['bill_payment']['rules'], _d, _c, _pl):
                a['bill_qty'] += 1
                a['bill_amt'] += ext
            if _exec_line_match(exec_cfg['activation_fee']['rules'], _d, _c, _pl):
                a['activation_fee'] += ext
            if _exec_line_match(exec_cfg['protect']['rules'], _d, _c, _pl):
                a['protect'] += 1
    return agg


def _store_code_resolver(client, org_id):
    """Return resolve(store_string) -> canonical store_code — the SAME mapping the Daily-Targets actuals
    use so a store's Exec-MTD trending attaches to the matching target row. coa.store_resolver (exact
    address → store_aliases → store_code → unambiguous leading number) then store_mapping address→code.
    Never raises; falls back to the raw string when nothing resolves."""
    try:
        from app.modules.account import coa
        _resolve_store = coa.store_resolver(client, org_id)
    except Exception:
        _resolve_store = None
    try:
        sm = (client.schema('commcalc').table('store_mapping')
              .select('store_code,store_address').eq('org_id', org_id).execute().data) or []
    except Exception:
        sm = []
    addr_to_code = {}
    for m in sm:
        a = (m.get('store_address') or '').strip().lower()
        c = (m.get('store_code') or '').strip()
        if a and c:
            addr_to_code[a] = c

    def _resolve(store):
        canon = (_resolve_store(store) if (_resolve_store and store) else None) or store
        return addr_to_code.get(str(canon).strip().lower(), canon)
    return _resolve


def _compute_feed_actuals_py(client, org_id, period, source='daily_sales_feed', rows=None):
    """The ONE processed sales source, computed in Python so the whole targets/recon system agrees.

    Mirrors the daily_sales_feed_actuals RPC BUT (a) is period-spelling agnostic (_pvariants), and
    (b) classifies contract_type by CONTAINS — anything with 'byod' is BYOD, 'upgrade' is an upgrade,
    any other non-empty Contract Type is an activation — instead of a rigid hardcoded label list. That
    is what fixes "activations/accessories show 0" when B2B's Contract Type labels drift from the exact
    strings the SQL function hardcodes (the July Action-Plan bug). Reads `source` (the daily feed),
    falling back to raw_sales — UNLESS the caller passes a pre-built `rows` set (e.g. the trans-id-deduped
    feed∪raw_sales union from _fetch_actuals), in which case those rows are aggregated as-is. Returns the
    same shape targets_engine expects."""
    cols = _ACTUALS_COLS

    if rows is None:
        def _q(table):
            return (client.schema('commcalc').table(table).select(cols)
                    .eq('org_id', org_id).in_('period', _pvariants(period)).limit(200000).execute().data) or []
        rows = _q(source)
        if not rows and source != 'raw_sales':
            try:
                rows = _q('raw_sales')
            except Exception:
                rows = []
    if not rows:
        return []
    acfg = _accessory_config(client, org_id)
    # Canonicalize the feed's store string through the SAME resolver the P&L / Store-Matching UI use
    # (exact address → store_aliases → store_code → unambiguous leading number). Without this the actuals
    # key on the RAW feed spelling (e.g. "3 Palisade Ave Yonkers") while the Daily Target keys on the
    # canonical store_code (B-3PL) → scope_achieved_mtd matches nothing → the store shows 0 activations
    # even though its sales are in the feed. Any store needing an alias/leading-number was silently 0.
    _resolve_code = _store_code_resolver(client, org_id)
    # THE shared per-(store, rep, day) pass — identical skip rules + classification + distinct-txn counting
    # as the Sales Report + Executive MTD (see _sales_cell_agg). Targets then re-keys each cell to the
    # canonical store_code (so scope_achieved_mtd matches the Daily Target's store_code) and rep.upper(),
    # merging any raw-store spellings that resolve to the same code by UNIONing the distinct-txn sets.
    cells = _sales_cell_agg(rows, acfg)
    agg = {}
    for (store, rep, date), a in cells.items():
        if not date:
            continue
        code = _resolve_code(store)
        k = (code, rep.upper(), date)
        o = agg.get(k)
        if not o:
            o = agg[k] = {'store_code': code, 'store': store, 'rep_name': rep, 'login': a.get('login'),
                          'trans_date': date, '_prem': set(), '_byod': set(), '_upg': set(),
                          'acc_gp': 0.0, 'setup_fee': 0.0, 'box_count': 0, '_billpay': set()}
        o['_prem'] |= a['_prem']
        o['_byod'] |= a['_byod']
        o['_upg'] |= a['_upg']
        o['_billpay'] |= a['_billpay']
        # Accessory "achieved" for TARGET attainment = accessory sales revenue + device set-up fee
        # (owner directive 2026-07-17). setup_fee is ALSO carried separately (reported on its own line).
        # accessory_rev already EXCLUDES set-up-fee lines (see _sales_cell_agg) → no double-count.
        o['setup_fee'] += a['setup_fee_rev']
        o['acc_gp'] += a['accessory_rev'] + a['setup_fee_rev']
        o['box_count'] += a['box_count']
    out = []
    for o in agg.values():
        out.append({'store_code': o['store_code'], 'store': o['store'], 'rep_name': o['rep_name'],
                    'login': o['login'], 'trans_date': o['trans_date'],
                    'prem_count': len(o['_prem']), 'byod_count': len(o['_byod']),
                    'upg_count': len(o['_upg']), 'acc_gp': round(o['acc_gp'], 2),
                    'setup_fee': round(o['setup_fee'], 2),
                    'box_count': o['box_count'], 'billpay_count': len(o['_billpay'])})
    return out


def _fetch_actuals(client, org_id, period):
    """MTD 'achieved' actuals for Daily Targets — flowing from the EXACT SAME sales aggregation the Sales
    Report + Executive MTD use (owner directive 2026-07-16: "the Sales Report should flow into the Daily
    Targets"). Reads the SAME union (`_sales_rows_union`, the (day × store) cell-grain feed∪raw_sales the
    Sales Report reads) and the SAME row-level pass (`_compute_feed_actuals_py` → `_sales_cell_agg`), so
    the three surfaces can never disagree.

    Previously this used the TRANSACTION-grain union (`_sales_rows_union_txn`) — which could keep a raw_sales
    transaction the feed lacked even on a store-day the feed also sold, i.e. count sales the Sales Report's
    cell-grain view does NOT show. Repointing to the cell-grain union trades that narrow completeness edge
    for guaranteed consistency with the Sales Report (the owner's "never disagree again"): the feed still
    wins each store-day cell it holds, every other store fills from raw_sales (so luxelink's re-uploaded
    stores are still present), and in the healthy feed-only state the union is the feed verbatim → Boost
    unchanged. Display-only (targets), never commission payout."""
    rows, _umeta = _sales_rows_union(client, org_id, period, cols=_ACTUALS_COLS)
    rows = _compute_feed_actuals_py(client, org_id, period, rows=rows)
    cmap = _rep_canon_map(client, org_id)
    for r in rows:
        if r.get('rep_name'):
            r['rep_name'] = _canon(r.get('rep_name'), cmap)
    return rows


def _targets_trending_by_code(client, org_id, period, today=None):
    """Per-store-code TRENDING (projected month-end) accessory$ + activation/box count, taken DIRECTLY
    from the Executive-MTD computation (`_exec_mtd`) so the Daily-Targets / Accessory-Targets pages show
    the SAME trending numbers as Executive MTD — ONE source, both move together when the shared
    aggregation or the trend formula changes. Exec MTD keys `by_location` on the raw store string; this
    folds each row into the canonical store_code via the SAME resolver the actuals use, so a store's
    trending attaches to its Daily-Targets row (raw spellings that resolve to one code sum together).
    Display-only; never raises (a trending failure must not break the targets summary)."""
    try:
        ex = _exec_mtd(client, org_id, period, today=today)
    except Exception as e:
        print(f"WARN _targets_trending_by_code exec-mtd failed: {e}")
        return {}, {}
    resolve = _store_code_resolver(client, org_id)
    by_code = {}
    for r in (ex.get('by_location', {}) or {}).get('rows', []) or []:
        code = str(resolve(r.get('store')) or '').strip().upper()
        if not code:
            continue
        d = by_code.setdefault(code, {'trending_acc_sales': 0.0, 'trending_box': 0,
                                      'acc_sales': 0.0, 'total_activation': 0})
        d['trending_acc_sales'] += safe_float(r.get('trending_acc_sales'))
        d['trending_box'] += int(r.get('trending_box') or 0)
        d['acc_sales'] += safe_float(r.get('acc_sales'))
        d['total_activation'] += int(r.get('total_activation') or 0)
    for d in by_code.values():
        d['trending_acc_sales'] = round(d['trending_acc_sales'], 2)
        d['acc_sales'] = round(d['acc_sales'], 2)
    return by_code, (ex.get('trending', {}) or {})


def _byod_pct_default(client, period, org_id=ORG_ID):
    try:
        r = (client.schema('commcalc').table('payout_config')
             .select('kpi_byod_target').eq('org_id', org_id).in_('period', _pvariants(period)).limit(1).execute().data) or []
        if r and r[0].get('kpi_byod_target') is not None:
            return safe_float(r[0]['kpi_byod_target'])
    except Exception:
        pass
    return 35.0


# ── Month-over-month target carry-forward + stretch ──────────────────────────────────────────────
# The prior month's target carries forward automatically; a store that HIT a category's target last
# month gets a 110% STRETCH on it, one that missed carries the same number forward. Evaluated per
# category (activations / upgrades / accessories). Powers the Target Settings seed preview + the
# "Roll forward from last month" action (which persists).
_MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December']
STRETCH_FACTOR = 1.10


def _prior_period(period: str) -> str:
    """The 'Month YYYY' label one month before `period`."""
    pm = parse_period(period)
    m, y = pm['month'] - 1, pm['year']
    if m < 1:
        m, y = 12, y - 1
    return f"{_MONTH_NAMES[m - 1]} {y}"


def _carry_forward_map(client, org_id, period, stores):
    """Per store_code (UPPER): the suggested next-month target derived from the prior period —
    carry each category forward, or ×1.10 when the store achieved that category's prior target.
    basis[cat] ∈ 'stretch' | 'carry' | 'new' (no prior target to work from)."""
    prior = _prior_period(period)
    prows = (client.schema('commcalc').table('targets')
             .select('*').eq('org_id', org_id).in_('period', _pvariants(prior)).execute().data) or []
    prior_by_code = {str(r.get('store_code', '')).upper(): r for r in prows}
    byod_prior = _byod_pct_default(client, prior, org_id)
    p_actuals = _fetch_actuals(client, org_id, prior)
    out = {}
    for s in stores:
        code = str(s.get('store_code', '') or '').strip()
        if not code:
            continue
        cu = code.upper()
        ptrow = prior_by_code.get(cu)
        p_monthly = targets_engine.derive_monthly_by_cat(
            ptrow if ptrow else {'accessories_monthly': safe_float(s.get('monthly_target'))}, byod_prior)
        achieved = targets_engine.scope_achieved_mtd(p_actuals, code, None, None)  # whole prior month
        res, basis = {}, {}
        for cat, col in (('activations', 'activations_monthly'), ('upgrades', 'upgrades_monthly'),
                         ('accessories', 'accessories_monthly')):
            tgt = safe_float(p_monthly.get(cat))
            got = safe_float(achieved.get(cat))
            if tgt > 0 and got >= tgt:
                val, basis[cat] = tgt * STRETCH_FACTOR, 'stretch'
            elif tgt > 0:
                val, basis[cat] = tgt, 'carry'
            else:
                val, basis[cat] = tgt, 'new'
            res[col] = round(val) if cat != 'accessories' else round(val, 2)
        res['byod_pct'] = ptrow.get('byod_pct') if ptrow else None
        res['basis'] = basis
        out[cu] = res
    return {'prior_period': prior, 'by_code': out}


@router.get("/targets/{period}")
async def get_targets(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """List per-store monthly target config. Stores without a row are SEEDED from the prior month —
    each category carried forward, or +10% stretched when last month's target was met (see
    _carry_forward_map). Accessories still fall back to storeops.stores.monthly_target when there's
    no prior; byod_pct from KPI config."""
    client = sb()
    pm = parse_period(period)
    rows = (client.schema('commcalc').table('targets')
            .select('*').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    by_code = {str(r.get('store_code', '')).upper(): r for r in rows}
    byod_def = _byod_pct_default(client, period, org_id)

    stores = (client.schema('storeops').table('stores')
              .select('store_code,address,market,monthly_target,is_active')
              .eq('org_id', org_id)
              .execute().data) or []
    # Only pull last month's actuals for the carry-forward when at least one store still needs seeding.
    need_seed = any((str(s.get('store_code', '') or '').strip().upper() or None) not in by_code
                    for s in stores if str(s.get('store_code', '') or '').strip())
    cf = (_carry_forward_map(client, org_id, period, stores) if need_seed
          else {'by_code': {}, 'prior_period': _prior_period(period)})
    out = []
    for s in stores:
        code = str(s.get('store_code', '') or '').strip()
        if not code:
            continue
        existing = by_code.get(code.upper())
        if existing:
            row = dict(existing)
            row['address'] = s.get('address')
            row['market'] = s.get('market')
            row['_seeded'] = False
        else:
            cfrow = cf['by_code'].get(code.upper(), {})
            acc = cfrow.get('accessories_monthly')
            row = {
                'org_id': org_id, 'store_code': code, 'period': period,
                'period_month': pm['month'], 'period_year': pm['year'],
                'activations_monthly': cfrow.get('activations_monthly', 0),
                'upgrades_monthly': cfrow.get('upgrades_monthly', 0),
                'accessories_monthly': acc if acc is not None else safe_float(s.get('monthly_target')),
                'byod_pct': cfrow.get('byod_pct') or byod_def, 'notes': None,
                'address': s.get('address'), 'market': s.get('market'),
                '_seeded': True, '_seed_basis': cfrow.get('basis'), '_prior_period': cf['prior_period'],
            }
        out.append(row)
    out.sort(key=lambda r: str(r.get('address') or r.get('store_code') or ''))
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / enforcement off)
    if ks is not None:
        out = [r for r in out if in_keyset(ks, r.get('store_code'), r.get('address'))]
    return {'period': period, 'byod_pct_default': byod_def, 'targets': out}


@router.put("/targets/{period}")
async def save_target(period: str, body: dict, org_id: str = ORG_ID):
    """Upsert one store's monthly target config (Settings page save)."""
    client = sb()
    code = str(body.get('store_code', '') or '').strip()
    if not code:
        raise HTTPException(400, "store_code required")
    pm = parse_period(period)
    row = {
        'org_id': org_id, 'store_code': code, 'period': period,
        'period_month': pm['month'], 'period_year': pm['year'],
        'activations_monthly': safe_float(body.get('activations_monthly')),
        'upgrades_monthly': safe_float(body.get('upgrades_monthly')),
        'accessories_monthly': safe_float(body.get('accessories_monthly')),
        # Blank field → NULL (fall back to KPI default), not 0% which would zero the BYOD target.
        'byod_pct': (safe_float(body.get('byod_pct'))
                     if str(body.get('byod_pct') if body.get('byod_pct') is not None else '').strip() != ''
                     else None),
        'notes': body.get('notes'),
        'updated_by': body.get('updated_by') or 'web',
    }
    r = (client.schema('commcalc').table('targets')
         .upsert(row, on_conflict='org_id,store_code,period').execute())
    return r.data[0] if r.data else row


@router.post("/targets/{period}/roll-forward")
async def roll_forward_targets(period: str, body: dict = None, org_id: str = ORG_ID):
    """Persist the month-over-month carry-forward into `period`: each store's prior-month target
    carried forward, or +10% where last month's target was met (see _carry_forward_map). By default
    only stores WITHOUT a target row for this period are written (never overwrites a hand-set target);
    pass {overwrite:true} to refresh them all. Stores with nothing to carry (all-zero) are skipped."""
    client = sb()
    pm = parse_period(period)
    overwrite = bool((body or {}).get('overwrite'))
    existing = {str(r.get('store_code', '')).upper()
                for r in ((client.schema('commcalc').table('targets')
                           .select('store_code').eq('org_id', org_id)
                           .in_('period', _pvariants(period)).execute().data) or [])}
    stores = (client.schema('storeops').table('stores')
              .select('store_code,address,market,monthly_target,is_active')
              .eq('org_id', org_id).execute().data) or []
    cf = _carry_forward_map(client, org_id, period, stores)
    prior = cf['prior_period']
    written, skipped = [], 0
    for s in stores:
        code = str(s.get('store_code', '') or '').strip()
        if not code:
            continue
        cu = code.upper()
        if cu in existing and not overwrite:
            skipped += 1
            continue
        cfrow = cf['by_code'].get(cu, {})
        acc = cfrow.get('accessories_monthly')
        acc = acc if acc is not None else safe_float(s.get('monthly_target'))
        act = safe_float(cfrow.get('activations_monthly'))
        upg = safe_float(cfrow.get('upgrades_monthly'))
        if act <= 0 and upg <= 0 and acc <= 0:   # nothing to carry — don't create an empty row
            skipped += 1
            continue
        row = {
            'org_id': org_id, 'store_code': code, 'period': period,
            'period_month': pm['month'], 'period_year': pm['year'],
            'activations_monthly': act, 'upgrades_monthly': upg, 'accessories_monthly': acc,
            'byod_pct': cfrow.get('byod_pct'),
            'notes': f'Rolled forward from {prior}', 'updated_by': 'roll-forward',
        }
        client.schema('commcalc').table('targets').upsert(row, on_conflict='org_id,store_code,period').execute()
        written.append({'store_code': code, 'basis': cfrow.get('basis')})
    return {'period': period, 'prior_period': prior, 'written': len(written),
            'skipped': skipped, 'overwrite': overwrite, 'stores': written}


@router.get("/targets/{period}/calendar")
async def get_target_calendar(
    period: str, store_code: str, scope: str = "store",
    rep: str = "", today: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID,
):
    """Schedule-weighted daily targets + catch-up + pace + day-by-day calendar
    for a single store (scope=store) or a single rep within it (scope=rep)."""
    client = sb()
    from app.modules.storeops.router import scope_keyset, in_keyset
    # Only a manager WITH a span (non-empty keyset) is restricted here. None = admin/unrestricted;
    # an empty set = a self-scope rep (no managed stores) viewing their OWN store from the portal —
    # must NOT be blocked. The store list a manager can reach is already span-scoped upstream.
    ks = scope_keyset(authorization, org_id)
    if ks and not in_keyset(ks, store_code):
        raise HTTPException(403, "That store is outside your assigned area.")
    start, end, today = _period_bounds(period, today)
    byod_def = _byod_pct_default(client, period, org_id)

    trow = (client.schema('commcalc').table('targets')
            .select('*').eq('org_id', org_id).in_('period', _pvariants(period))
            .eq('store_code', store_code).limit(1).execute().data) or []
    target_row = trow[0] if trow else {}
    # Seed accessories from store monthly_target when no explicit row yet.
    if not trow:
        srow = (client.schema('storeops').table('stores')
                .select('monthly_target').eq('org_id', org_id).eq('store_code', store_code).limit(1).execute().data) or []
        if srow:
            target_row = {'accessories_monthly': safe_float(srow[0].get('monthly_target'))}
    monthly = targets_engine.derive_monthly_by_cat(target_row, byod_def)

    shifts = _fetch_shifts(client, start, end, org_id)
    actuals = _fetch_actuals(client, org_id, period)
    rep_arg = rep if scope == 'rep' and rep else None

    hours_by_day = targets_engine.scope_hours_by_day(shifts, store_code, rep_arg)
    actuals_by_day = targets_engine.scope_actuals_by_day(actuals, store_code, rep_arg)
    month_end = end - _timedelta(days=1)

    # Employee target PRORATION (#10/#11): a rep's monthly target = the store monthly × the rep's
    # share of the store's scheduled hours (projected to the full month), NOT the full store target.
    # compute_scope then spreads it over the rep's days, so Σ(rep days) = store_monthly × rep_share.
    rep_share = 1.0
    if rep_arg:
        store_hours = targets_engine.scope_hours_by_day(shifts, store_code, None)
        store_eff = {**store_hours, **targets_engine.project_future_hours(store_hours, today, month_end)}
        rep_eff = {**hours_by_day, **targets_engine.project_future_hours(hours_by_day, today, month_end)}
        sh, rh = sum(store_eff.values()), sum(rep_eff.values())
        rep_share = (rh / sh) if sh > 0 else 0.0
        monthly = {c: round((v or 0) * rep_share, 2) for c, v in monthly.items()}

    result = targets_engine.compute_scope(monthly, hours_by_day, actuals_by_day, today,
                                          round_counts=True, month_end=month_end)
    result.update({
        'period': period, 'scope': scope, 'store_code': store_code,
        'rep': rep_arg, 'monthly_targets': monthly, 'rep_share': round(rep_share, 4),
        'reps': targets_engine.reps_in_scope(shifts, actuals, store_code),
    })
    # Conversion (boxes ÷ bill-payments). Always include the store rate; for a rep scope
    # also include the rep's rate + whether it's dragging the store down.
    store_conv = targets_engine.scope_conversion(actuals, store_code, None, today)
    conv = {'store': store_conv}
    if rep_arg:
        rep_conv = targets_engine.scope_conversion(actuals, store_code, rep_arg, today)
        rep_conv['below_store'] = rep_conv['rate'] < store_conv['rate']
        conv['rep'] = rep_conv
    result['conversion'] = conv
    return result


def _caller_self_keyset(authorization: str, org_id: str):
    """For a SELF-scoped (rep) caller, the UPPER store keyset (store_code(s) + their addresses) of the
    rep's OWN store(s). Returns (is_self, keyset|None):
      • (False, None)  — not self-scoped (admin / unrestricted / a real manager span). Caller keeps
                         scope_keyset's own result.
      • (True, {keys}) — a self rep with a pinned store → restrict to THEIR store(s).
      • (True, None)   — a self rep with NO pinned store → fall back to UNRESTRICTED so they are never
                         locked out of picking their store (the pre-login "pick your store" behaviour).
    WHY: scope_keyset returns an EMPTY SET for a self rep (reps get no manager span), and callers that
    filter with `if ks is not None` then drop EVERY row → the rep's own targets/store never show (the
    'My Targets not showing for employees' bug). This substitutes the rep's own store so they see their
    own data. Reads app_users (public schema) READ-ONLY, org-scoped. Never raises."""
    try:
        from app.modules.storeops.router import _rbac_enabled, _role_scope
        from app.modules.core.router import _uid_from_token
    except Exception:
        return (False, None)
    try:
        if not _rbac_enabled(org_id):
            return (False, None)
        uid = _uid_from_token(authorization)
        if not uid:
            return (False, None)
        rows = (sb().table("app_users")
                .select("role,store_code,store_codes")
                .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
        if not rows:
            return (False, None)
        u = rows[0]
        if _role_scope(org_id, (u.get("role") or "").strip()) != "self":
            return (False, None)
        codes = set()
        if u.get("store_code"):
            codes.add(str(u.get("store_code")).strip().upper())
        for c in (u.get("store_codes") or []):
            if str(c).strip():
                codes.add(str(c).strip().upper())
        codes.discard("")
        if not codes:
            return (True, None)   # self rep, no pinned store → don't lock them out
        keys = set(codes)
        try:
            meta = (sb().table("stores").select("store_code,address")
                    .eq("org_id", org_id).execute().data) or []
            for s in meta:
                if str(s.get("store_code") or "").strip().upper() in codes:
                    ad = str(s.get("address") or "").strip().upper()
                    if ad:
                        keys.add(ad)
        except Exception:
            pass
        return (True, keys)
    except Exception:
        return (False, None)


@router.get("/targets/{period}/summary")
async def get_targets_summary(period: str, today: str = "", include_untargeted: bool = False,
                              stores: Optional[List[str]] = Query(default=None),
                              markets: Optional[List[str]] = Query(default=None),
                              reps: Optional[List[str]] = Query(default=None),
                              authorization: str = Header(default=""), org_id: str = ORG_ID):
    """All-stores overview: store-level today/pace/need/monthly/achieved per category. When RBAC
    enforcement is on, a non-admin manager only sees the stores in their org-unit span (Phase 5).
    include_untargeted=1 also returns stores that have sales/achieved but no target set (so the
    Accessory tab can track achieved accessory $ even before per-store accessory targets exist).

    RULE FIVE standardized filters (2026-07-17): optional repeated `stores` (store_code or address) /
    `markets` / `reps` query params filter the returned stores SERVER-SIDE. store/market pick which stores
    are shown (and thus the tiles + the trending sum); rep narrows the per-rep breakdown to the selected
    reps and keeps only stores where they worked/sold — the store-level target/achieved/trending stay
    WHOLE-STORE (a store target can't be split per rep). Filter OPTIONS are returned in `filters`
    (pick-don't-type over the org's real stores/markets/reps).

    TRENDING (2026-07-17): each store carries `trending_acc_sales` + `trending_box` (projected month-end)
    read DIRECTLY from Executive MTD's `_exec_mtd` (`_targets_trending_by_code`) so the Targets pages show
    the SAME trending numbers as Exec MTD — one shared aggregation + trend formula, both move together."""
    client = sb()
    start, end, today = _period_bounds(period, today)
    byod_def = _byod_pct_default(client, period, org_id)

    trows = (client.schema('commcalc').table('targets')
             .select('*').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    tgt_by_code = {str(r.get('store_code', '')).upper(): r for r in trows}
    store_rows = (client.schema('storeops').table('stores')
                  .select('store_code,address,market,monthly_target').eq('org_id', org_id).execute().data) or []
    shifts = _fetch_shifts(client, start, end, org_id)
    actuals = _fetch_actuals(client, org_id, period)
    # Whole-store projected-month-end trending, straight from Executive MTD (one source, moves together).
    trend_by_code, trend_meta = _targets_trending_by_code(client, org_id, period, today=today)

    # ── RULE FIVE filter OPTIONS (pick-don't-type over real data), computed from the FULL universe so a
    #    selection can always be changed. Store value = store_code (label = address); market = storeops
    #    market; rep = canonical rep name present in the period's actuals.
    store_opts = [{'value': str(s.get('store_code') or '').strip(),
                   'label': str(s.get('address') or s.get('store_code') or '').strip()}
                  for s in store_rows if str(s.get('store_code') or '').strip()]
    market_opts = sorted({str(s.get('market') or '').strip() for s in store_rows if str(s.get('market') or '').strip()})
    rep_opts = sorted({str(a.get('rep_name') or '').strip() for a in actuals if str(a.get('rep_name') or '').strip()})
    filters = {'stores': store_opts, 'markets': market_opts, 'reps': rep_opts}

    store_sel = {str(x).strip().lower() for x in (stores or []) if str(x).strip()}
    market_sel = {str(x).strip().lower() for x in (markets or []) if str(x).strip()}
    rep_sel = {str(x).strip().upper() for x in (reps or []) if str(x).strip()}
    applied = {'stores': sorted(store_sel), 'markets': sorted(market_sel), 'reps': sorted(rep_sel)}

    out = []
    for s in store_rows:
        code = str(s.get('store_code', '') or '').strip()
        if not code:
            continue
        addr = str(s.get('address') or '').strip()
        mkt = str(s.get('market') or '').strip()
        # store / market filters select which stores are shown (whole-store math unchanged).
        if store_sel and code.lower() not in store_sel and addr.lower() not in store_sel:
            continue
        if market_sel and mkt.lower() not in market_sel:
            continue
        trow = tgt_by_code.get(code.upper())
        if not trow:
            trow = {'accessories_monthly': safe_float(s.get('monthly_target'))}
        monthly = targets_engine.derive_monthly_by_cat(trow, byod_def)
        if sum(monthly.values()) <= 0 and not include_untargeted:
            continue
        hours_by_day = targets_engine.scope_hours_by_day(shifts, code, None)
        actuals_by_day = targets_engine.scope_actuals_by_day(actuals, code, None)
        res = targets_engine.compute_scope(monthly, hours_by_day, actuals_by_day, today,
                                           round_counts=True, month_end=end - _timedelta(days=1))
        # Untargeted store (only reachable via include_untargeted): keep it only when it has real
        # achieved actuals — else it's just noise. Targeted stores always pass.
        if sum(monthly.values()) <= 0:
            _cats = res.get('categories', {}) or {}
            if not any(safe_float((_cats.get(c) or {}).get('achieved_mtd')) > 0 for c in _cats):
                continue
        store_conv = targets_engine.scope_conversion(actuals, code, None, today)
        # Reps who worked/sold at this store + their MTD performance, so the store
        # row breaks down into the people driving it (for corrective action).
        reps_out = []
        for rep_name in targets_engine.reps_in_scope(shifts, actuals, code):
            ach = targets_engine.scope_achieved_mtd(actuals, code, rep_name, today)
            rconv = targets_engine.scope_conversion(actuals, code, rep_name, today)
            reps_out.append({'rep': rep_name, **ach, 'conversion': rconv,
                             'below_store': rconv['rate'] < store_conv['rate']})
        # rep filter: narrow the per-rep breakdown to the selected reps + drop stores none of them touch.
        if rep_sel:
            reps_out = [r for r in reps_out if str(r['rep']).strip().upper() in rep_sel]
            if not reps_out:
                continue
        reps_out.sort(key=lambda r: -r['activations'])
        trend = trend_by_code.get(code.upper(), {})
        out.append({
            'store_code': code, 'address': s.get('address'), 'market': s.get('market'),
            'scheduled_hours_total': res['scheduled_hours_total'],
            'categories': res['categories'],
            'conversion': store_conv,
            'trending_acc_sales': safe_float(trend.get('trending_acc_sales')),
            'trending_box': int(trend.get('trending_box') or 0),
            'reps': reps_out,
        })
    out.sort(key=lambda r: str(r.get('address') or r.get('store_code') or ''))
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    # A self-scoped rep gets an EMPTY keyset from scope_keyset (no manager span) → `is not None` would drop
    # every store and the rep's own targets would never show (My Targets empty). Substitute the rep's OWN
    # store(s) so they see their own data; a rep with no pinned store falls back to unrestricted (pick).
    is_self, self_ks = _caller_self_keyset(authorization, org_id)
    if is_self:
        ks = self_ks
    if ks is not None:
        out = [s for s in out if in_keyset(ks, s.get('store_code'), s.get('address'))]
    return {'period': period, 'today': today.isoformat(), 'stores': out,
            'filters': filters, 'applied': applied, 'trending': trend_meta}


# KPI → commission tier inputs (mirrors calculator.py KPI defaults).
# Each is (key, label, payout_config column, default target %).
ACTION_KPI_DEFS = [
    ('atu', 'ATU', 'kpi_atu_target', 55),
    ('protect', 'Protect', 'kpi_protect_target', 80),
    ('boostapp', 'Carrier App', 'kpi_boostapp_target', 65),
    ('familyplan', 'Family Plan', 'kpi_familyplan_target', 45),
    ('byod', 'BYOD', 'kpi_byod_target', 35),
    ('tmr3', 'TMR3', 'kpi_tmr3_target', 70),
    ('aal', 'AAL', 'kpi_aal_target', 5),
]
_AP_CAT_LABEL = {'activations': 'Activations', 'upgrades': 'Upgrades',
                 'byod': 'BYOD', 'accessories': 'Accessories'}
_KPI_DEFAULT_CARRIER = '00000000-0000-0000-0000-000000000000'  # nil carrier_id = the org default set


def _kpi_defs(org_id=ORG_ID, carrier_id=None):
    """Per-carrier KPI metric definitions (migration 060) as (key, label, payout_config_col, default)
    tuples — SAME shape as ACTION_KPI_DEFS, so callers are unchanged. Carrier-specific rows override the
    org-default (nil-carrier) set per key; falls back to the hard-coded ACTION_KPI_DEFS if the table is
    empty or migration 060 isn't applied (so behavior is unchanged until a tenant configures their own)."""
    try:
        rows = (sb().schema('commcalc').table('carrier_kpi_metric').select('*')
                .eq('org_id', org_id).eq('is_active', True).order('sort').execute().data) or []
    except Exception:
        return ACTION_KPI_DEFS
    if not rows:
        return ACTION_KPI_DEFS
    keep = [r for r in rows if r.get('carrier_id') in (_KPI_DEFAULT_CARRIER, carrier_id)]
    by_key = {}
    for r in sorted(keep, key=lambda r: 0 if r.get('carrier_id') == carrier_id and carrier_id else 1):
        k = r.get('metric_key')
        if k and k not in by_key:
            by_key[k] = r
    ordered = sorted(by_key.values(), key=lambda r: r.get('sort') or 0)
    out = [(r['metric_key'], r.get('label') or r['metric_key'],
            r.get('payout_config_col') or f"kpi_{r['metric_key']}_target",
            safe_float(r.get('target_default'))) for r in ordered]
    return out or ACTION_KPI_DEFS


@router.get("/carrier-kpi-metrics")
def list_carrier_kpi_metrics(carrier_id: str = "", org_id: str = ORG_ID):
    """KPI metric definitions for the org (optionally a carrier). Falls back to the built-in defaults as
    rows if migration 060 isn't applied yet, so the UI always has something to show."""
    try:
        rows = (sb().schema('commcalc').table('carrier_kpi_metric').select('*')
                .eq('org_id', org_id).order('sort').execute().data) or []
    except Exception:
        return {"metrics": [], "ready": False,
                "defaults": [{"metric_key": k, "label": l, "payout_config_col": c, "target_default": d}
                             for (k, l, c, d) in ACTION_KPI_DEFS]}
    if carrier_id:
        rows = [r for r in rows if r.get('carrier_id') in (_KPI_DEFAULT_CARRIER, carrier_id)]
    return {"metrics": rows, "ready": True, "default_carrier": _KPI_DEFAULT_CARRIER}


@router.post("/carrier-kpi-metrics")
def save_carrier_kpi_metric(body: dict, org_id: str = ORG_ID):
    """Create/edit one KPI metric definition. carrier_id omitted/blank → the org default (nil) set."""
    key = (body.get("metric_key") or "").strip()
    if not key:
        raise HTTPException(400, "metric_key required")
    row = {"org_id": org_id, "carrier_id": body.get("carrier_id") or _KPI_DEFAULT_CARRIER,
           "metric_key": key, "label": body.get("label") or key,
           "target_default": safe_float(body.get("target_default")),
           "payout_config_col": body.get("payout_config_col") or f"kpi_{key}_target",
           "sort": int(body.get("sort") or 0), "is_active": bool(body.get("is_active", True))}
    try:
        if body.get("id"):
            r = sb().schema('commcalc').table('carrier_kpi_metric').update(row).eq('id', body['id']).eq('org_id', org_id).execute()
        else:
            r = sb().schema('commcalc').table('carrier_kpi_metric').upsert(row, on_conflict='org_id,carrier_id,metric_key').execute()
        return (r.data or [{}])[0]
    except Exception as e:
        raise HTTPException(500, f"save kpi metric failed (is migration 060 applied?): {e}")


@router.delete("/carrier-kpi-metrics/{metric_id}")
def delete_carrier_kpi_metric(metric_id: str, org_id: str = ORG_ID):
    sb().schema('commcalc').table('carrier_kpi_metric').delete().eq('org_id', org_id).eq('id', metric_id).execute()
    return {"deleted": metric_id}


@router.get("/coaching/{period}")
def rep_coaching(period: str, store: str = "", market: str = "", rep: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-rep COACHING view: which KPIs each rep met vs missed, and WHY they're losing money
    (commission at risk below tier 1.0 + chargebacks deducted) + flags & coaching notes. Reuses
    the KPI defs + tier/at-risk logic from the action plan; adds per-rep chargebacks + flags.
    Powers the admin/DM coaching dashboard and the employee's own coaching card (rep filter)."""
    require_org(org_id)
    client = sb()
    cfg_rows = (client.schema('commcalc').table('payout_config')
                .select('*').eq('org_id', org_id).in_('period', _pvariants(period)).limit(1).execute().data) or []
    cfg = cfg_rows[0] if cfg_rows else {}
    kpi_targets = {k: (safe_float(cfg.get(col)) or float(dv)) for (k, _l, col, dv) in _kpi_defs(org_id)}
    t100 = int(cfg.get('tier_100_min_kpis') or 7)
    comms = (client.schema('commcalc').table('rep_commissions').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    cb = (client.schema('commcalc').table('chargeback_items')
          .select('epay_salesperson,amount,deduct').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    flags = (client.schema('commcalc').table('flags')
             .select('epay_salesperson,severity,description,coaching_note').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    stores = (client.schema('storeops').table('stores').select('store_code,address,market').eq('org_id', org_id).execute().data) or []
    mkt_by = {}
    for s in stores:
        for key in (s.get('store_code'), s.get('address')):
            if key:
                mkt_by[str(key).strip().upper()] = s.get('market') or ''

    cb_by, fl_by = {}, {}
    for it in cb:
        k = (it.get('epay_salesperson') or '').strip().upper()
        if not k:
            continue
        d = cb_by.setdefault(k, {'total': 0.0, 'count': 0, 'deducted': 0.0})
        amt = safe_float(it.get('amount'))
        d['count'] += 1
        d['total'] += amt
        if it.get('deduct'):
            d['deducted'] += amt
    for f in flags:
        k = (f.get('epay_salesperson') or '').strip().upper()
        if not k:
            continue
        d = fl_by.setdefault(k, {'count': 0, 'high': 0, 'notes': []})
        d['count'] += 1
        if (f.get('severity') or '').upper() == 'HIGH':
            d['high'] += 1
        note = f.get('coaching_note') or f.get('description')
        if note and note not in d['notes'] and len(d['notes']) < 3:
            d['notes'].append(note)

    reps = []
    for cr in comms:
        name = (cr.get('storeops_name') or cr.get('epay_salesperson') or '').strip()
        if not name:
            continue
        eslp = (cr.get('epay_salesperson') or '').strip()
        if rep and rep.strip().upper() not in (name.upper(), eslp.upper()):
            continue
        st = (cr.get('store') or '').strip()
        if store and store.strip().upper() != st.upper():
            continue
        mk = mkt_by.get(st.upper(), '')
        if market and mk != market:
            continue
        tier = safe_float(cr.get('tier'))
        subtotal = safe_float(cr.get('subtotal'))
        kv = cr.get('kpi_values') or {}
        kpis = [{'kpi': k, 'label': lab, 'target': kpi_targets[k],
                 'actual': round(safe_float(kv.get(k)), 1), 'met': safe_float(kv.get(k)) >= kpi_targets[k]}
                for (k, lab, _c, _d) in _kpi_defs(org_id)]
        kpis_met = cr.get('kpis_met')
        kpis_met = sum(1 for x in kpis if x['met']) if kpis_met is None else kpis_met
        at_risk = round(subtotal * (1.0 - tier), 2) if tier < 1.0 else 0.0
        keys = {name.upper(), eslp.upper()} - {''}
        cbd = {'total': 0.0, 'count': 0, 'deducted': 0.0}
        fld = {'count': 0, 'high': 0, 'notes': []}
        for kk in keys:
            x = cb_by.get(kk)
            if x:
                cbd['total'] += x['total']; cbd['count'] += x['count']; cbd['deducted'] += x['deducted']
            y = fl_by.get(kk)
            if y:
                fld['count'] += y['count']; fld['high'] += y['high']
                for n in y['notes']:
                    if n not in fld['notes'] and len(fld['notes']) < 3:
                        fld['notes'].append(n)
        total_payout = round(safe_float(cr.get('total_payout')), 2)
        fp = cr.get('final_payout')
        final_payout = round(safe_float(fp) if fp is not None else total_payout - cbd['deducted'], 2)
        reps.append({
            'rep': name, 'store': st, 'market': mk, 'tier': tier,
            'kpis_met': kpis_met, 'total_kpis': cr.get('total_kpis') or 7,
            'subtotal': round(subtotal, 2), 'total_payout': total_payout, 'final_payout': final_payout,
            'at_risk': at_risk, 'kpis': kpis, 'short_kpis': [x['label'] for x in kpis if not x['met']],
            'need_for_full': max(0, t100 - kpis_met),
            'chargeback_total': round(cbd['total'], 2), 'chargeback_deducted': round(cbd['deducted'], 2),
            'chargeback_count': cbd['count'], 'flag_count': fld['count'], 'flag_high': fld['high'],
            'coaching_notes': fld['notes'], 'money_on_table': round(at_risk + cbd['deducted'], 2),
        })
    reps.sort(key=lambda r: -r['money_on_table'])
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        reps = [r for r in reps if in_keyset(ks, r.get('store'))]
    summary = {'reps': len(reps),
               'total_at_risk': round(sum(r['at_risk'] for r in reps), 2),
               'total_chargebacks': round(sum(r['chargeback_deducted'] for r in reps), 2),
               'total_money_on_table': round(sum(r['money_on_table'] for r in reps), 2),
               'below_tier': sum(1 for r in reps if r['tier'] < 1.0)}
    return {"period": period, "reps": reps, "summary": summary}


def _team_totals(stores):
    """Sum the additive per-category target fields across a set of stores for the team headline."""
    cats = {}
    for s in stores:
        for cat, c in (s.get('categories') or {}).items():
            t = cats.setdefault(cat, {'unit': c.get('unit'), 'monthly': 0.0, 'achieved_mtd': 0.0,
                                      'need': 0.0, 'today_target': 0.0})
            t['monthly']      += safe_float(c.get('monthly'))
            t['achieved_mtd'] += safe_float(c.get('achieved_mtd'))
            t['need']         += safe_float(c.get('need'))
            t['today_target'] += safe_float(c.get('today_target'))
    for t in cats.values():
        for k in ('monthly', 'achieved_mtd', 'need', 'today_target'):
            t[k] = round(t[k], 2)
        t['pct'] = round(100 * t['achieved_mtd'] / t['monthly'], 1) if t['monthly'] > 0 else 0.0
    return cats


@router.get("/team/{period}/snapshot")
async def team_snapshot(period: str, authorization: str = Header(default=""),
                        unit_id: str = "", today: str = "", org_id: str = ORG_ID):
    """Manager TEAM snapshot for the signed-in caller's span (or a chosen unit_id within it).
    Reuses get_targets_summary (per-store today/pace/need/achieved) + rep_coaching (per-rep
    money-at-risk/KPIs/chargebacks), filtered to the span's store_codes. Per-rep drill-down uses the
    existing GET /core/employee-dashboard. Default-scoped (no hard refusal yet — Phase 5 enforces)."""
    from app.modules.storeops.router import _caller_span_codes, _unit_store_codes, caller_scope
    if unit_id:
        codes = _unit_store_codes(org_id, unit_id)
    else:
        try:
            codes = _caller_span_codes(authorization, org_id)
        except HTTPException:
            codes = []
    # Phase 5: when enforcement is on, a non-admin can't roll up a unit outside their own span.
    allowed = caller_scope(authorization, org_id)   # None = unrestricted (admin / open mode)
    if allowed is not None:
        codes = [c for c in codes if c in allowed]
    codes_set = {c.strip().upper() for c in codes}
    if not codes_set:
        return {"period": period, "is_manager": False, "span_store_codes": [],
                "stores": [], "reps": [], "totals": {}, "money_on_table": 0.0}
    # store_code <-> address keys so coaching reps (whose 'store' may be an address) still match.
    stores_meta = sb().schema('storeops').table('stores').select('store_code,address').eq('org_id', org_id).execute().data or []
    keys = set(codes_set)
    for s in stores_meta:
        sc = str(s.get('store_code') or '').strip().upper()
        if sc in codes_set:
            ad = str(s.get('address') or '').strip().upper()
            if ad:
                keys.add(ad)
    summ = await get_targets_summary(period, today=today, org_id=org_id)
    in_span = [s for s in summ.get('stores', []) if str(s.get('store_code') or '').strip().upper() in codes_set]
    coach = rep_coaching(period, org_id=org_id)
    team_reps = [r for r in coach.get('reps', []) if str(r.get('store') or '').strip().upper() in keys]
    return {"period": period, "today": summ.get('today'), "is_manager": True,
            "span_store_codes": sorted(codes_set), "stores": in_span, "reps": team_reps,
            "totals": _team_totals(in_span),
            "money_on_table": round(sum(safe_float(r.get('money_on_table')) for r in team_reps), 2)}


@router.get("/exec-overview/{period}")
def exec_overview(period: str, org_id: str = ORG_ID):
    """Owner/exec single-pane: headline tiles + a store leaderboard, rolled up from the per-rep
    coaching aggregation (commissions paid / at-risk / chargebacks / flags / reps below tier)."""
    require_org(org_id)
    cd = rep_coaching(period, org_id=org_id)
    reps = cd.get("reps", [])
    s = cd.get("summary", {})
    try:
        cbr = sb().schema('commcalc').table('chargeback_review').select('status').eq('org_id', org_id).execute().data or []
        open_cb = sum(1 for r in cbr if (r.get('status') or 'open') == 'open')
    except Exception:
        open_cb = 0
    by_store = {}
    for r in reps:
        st = r.get('store') or '—'
        d = by_store.setdefault(st, {'store': st, 'market': r.get('market') or '', 'reps': 0,
                                     'paid': 0.0, 'at_risk': 0.0, 'chargebacks': 0.0, 'flags': 0, 'on_table': 0.0})
        d['reps'] += 1
        d['paid'] += r.get('final_payout') or 0
        d['at_risk'] += r.get('at_risk') or 0
        d['chargebacks'] += r.get('chargeback_deducted') or 0
        d['flags'] += r.get('flag_count') or 0
        d['on_table'] += r.get('money_on_table') or 0
    stores = sorted(by_store.values(), key=lambda x: -x['paid'])
    for d in stores:
        for k in ('paid', 'at_risk', 'chargebacks', 'on_table'):
            d[k] = round(d[k], 2)
    # P&L headline from the Account module's consolidated statement (period stored as YYYY-MM).
    pl = {}
    try:
        pm = parse_period(period)
        ym = f"{pm['year']}-{pm['month']:02d}"
        prow = (sb().schema('commcalc').table('account_statements').select('payload')
                .eq('org_id', org_id).eq('period', ym).eq('statement_type', 'pl')
                .eq('scope_key', 'consolidated').limit(1).execute().data) or []
        if prow:
            p = prow[0].get('payload') or {}
            pl = {'revenue': p.get('revenue'), 'gross_profit': p.get('gross_profit'), 'net_income': p.get('net_income')}
    except Exception:
        pass
    return {"period": period, "pl": pl,
            "tiles": {"commissions_paid": round(sum(r.get('final_payout') or 0 for r in reps), 2),
                      "commission_at_risk": s.get('total_at_risk', 0),
                      "chargebacks_deducted": s.get('total_chargebacks', 0),
                      "money_on_table": s.get('total_money_on_table', 0),
                      "reps": s.get('reps', 0), "below_tier": s.get('below_tier', 0),
                      "open_chargebacks": open_cb},
            "stores": stores}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# EXECUTIVE MTD SUMMARY — the b2bsoft "Month To Date Location/Employee Sales Report", replicated from the
# org-corrected sales source so it works for EVERY tenant (luxelink + house) with NO monthly upload.
# DISPLAY-ONLY: reads sales, writes nothing, touches no pay path. Metric DEFINITIONS are CONFIG
# (exec_metric_config, mig 204) — NOT a ninth hard-coded accessory classifier (accessory-divergence note).
# ════════════════════════════════════════════════════════════════════════════════════════════════
# Defaults DERIVED from the real luxelink Total-Wireless Sales-Transaction-Details export. The ingest
# stores category = Category-column OR System-Category-column (router.py ~694), so each token list carries
# BOTH variants (System Category CellPhone/RTR Product/Accessory + Category KittedBranded/HandsetBranded/
# Other Carr. payments). Tokens are lowercase; the engine lowercases each sale line before matching.
_EXEC_METRIC_DEFAULTS = {
    'activation':     {'rules': {'byod': ['byod'], 'upgrade': ['upgrade'], 'port': ['port']}, 'basis': 'count'},
    'phones':         {'rules': {'category': ['cellphone', 'kittedbranded']}, 'basis': 'count'},
    'bill_payment':   {'rules': {'department': ['rtr'], 'category': ['rtr product', 'other carr. payments']}, 'basis': 'count'},
    'accessory':      {'rules': {'category': ['accessory', 'handsetbranded', 'accessories']}, 'basis': 'ext_price'},
    'activation_fee': {'rules': {'product_desc_contains': ['access charge']}, 'basis': 'ext_price'},
    'protect':        {'rules': {'product_desc_contains': ['protect'],
                                 'exclude_product_desc_contains': ['screen protect'],
                                 'exclude_department': ['rtr'],
                                 'exclude_category': ['rtr product', 'other carr. payments']}, 'basis': 'count'},
}
_EXEC_BUCKETS = tuple(_EXEC_METRIC_DEFAULTS.keys())


def _exec_metric_config(client, org_id):
    """Per-tenant Executive-MTD metric DEFINITIONS (exec_metric_config, mig 204), falling back to the
    DERIVED code defaults so the report works before the migration runs / for an un-seeded tenant. Returns
    {bucket: {'rules': {...}, 'basis': 'count'|'ext_price'}}. SAP-configurable: definitions are config, not
    a hard-coded classifier."""
    cfg = {k: {'rules': dict(v['rules']), 'basis': v['basis']} for k, v in _EXEC_METRIC_DEFAULTS.items()}
    try:
        rows = (client.schema('commcalc').table('exec_metric_config')
                .select('bucket,rules,basis').eq('org_id', org_id).execute().data) or []
        for r in rows:
            b = r.get('bucket')
            if b in cfg:
                cfg[b] = {'rules': r.get('rules') or {}, 'basis': r.get('basis') or cfg[b]['basis']}
    except Exception:
        pass
    return cfg


def _exec_line_match(rule, dept, cat, pdesc):
    """True if a sale line matches a bucket rule (all case-insensitive; inputs already lowercased).
    category/department = exact membership in a token list; product_desc_contains = substring; exclude_*
    negate first. Match = ANY positive predicate true AND no exclusion true."""
    if rule.get('exclude_department') and dept in rule['exclude_department']:
        return False
    if rule.get('exclude_category') and cat in rule['exclude_category']:
        return False
    if rule.get('exclude_product_desc_contains') and any(t in pdesc for t in rule['exclude_product_desc_contains']):
        return False
    if rule.get('category') and cat in rule['category']:
        return True
    if rule.get('department') and dept in rule['department']:
        return True
    if rule.get('product_desc_contains') and any(t in pdesc for t in rule['product_desc_contains']):
        return True
    return False


def _exec_act_class(ct, rules):
    """Activation split (b2bsoft Location/Employee report): BYOD/Upgrade/Port by keyword-CONTAINS on the
    contract_type (config tokens), any OTHER non-blank contract_type = a plain Activation. Priority
    upgrade > byod > port > activation (so 'BYOD Port' = BYOD, 'Port with IDV' = Port). This is the ONE
    split the spreadsheet uses; it reuses the same 'non-blank contract_type = an activation' rule as
    classify_contract_type/_compute_feed_actuals_py but adds the Port refinement. None = not an activation
    line (blank contract_type — e.g. an accessory/bill-payment line)."""
    c = (ct or '').strip().lower()
    if not c:
        return None
    if any(t in c for t in (rules.get('upgrade') or [])):
        return 'upgrade'
    if any(t in c for t in (rules.get('byod') or [])):
        return 'byod'
    if any(t in c for t in (rules.get('port') or [])):
        return 'port'
    return 'activation'


def _exec_mtd(client, org_id, period, stores=None, markets=None, reps=None, today=None):
    """Executive Month-To-Date summary — the b2bsoft 'Month To Date Location/Employee Sales Report',
    now DERIVED FROM the EXACT SAME aggregation the Sales Report uses (owner directive 2026-07-16: "the
    Sales Report is correct — Exec MTD should take its cumulative numbers from there"). Reads the SAME
    `_sales_rows_union` (feed leads the OPEN month, raw_sales a closed one, filling the other's (day × store)
    gaps) and the SAME `_sales_cell_agg` row-level pass — canonical skip rules (voided / Returns / the
    blank-or-'admin' rep), the shared classify_contract_type + _is_accessory, DISTINCT trans_id per bucket —
    then rolls the cells up by store / employee, applies the MTD date-cut, and trends. Works for luxelink AND
    the house org with NO monthly upload. DISPLAY-ONLY (reads sales, writes nothing).

    The base activation buckets (Activation / Port / BYOD / Upgrade) + Accessory$ come from the shared
    classifier so they EQUAL the Sales Report's; exec_metric_config now only EXTENDS — the configurable Port
    sub-split + the b2bsoft per-line columns the Sales Report doesn't carry (Total Phones / Bill Payment /
    Activation Fee / Total Protect). `today` is injectable (defaults to the server date) so the MTD date-cut
    + trending are unit-testable.

    RULE FIVE standardized filters (2026-07-16): optional `stores` / `markets` / `reps` multi-selects filter
    the UNION rows SERVER-SIDE (before bucketing) so the by-location table, the by-employee table, the
    trending math AND the exports all reflect the same filtered set. Filter OPTIONS are returned in `filters`
    (pick-don't-type over the org's real data) computed from the UNFILTERED union.

    Verified formulas: Total Activation = Activation+Port+BYOD+Upgrade; Trending Box = Total Activation ×
    days_in_month ÷ complete_days_elapsed; Trending Acc. Sales likewise on Acc. Sales; Conv. = Total
    Activation ÷ Bill Payment Qty; APB = Acc. Sales ÷ Total Activation. Closed/past month trending = actual."""
    cfg = _exec_metric_config(client, org_id)
    acfg = _accessory_config(client, org_id)
    rows, meta = _sales_rows_union(client, org_id, period)

    # ── Market resolver (store_mapping; keyed by address / code / leading store-number) — inline + optional
    #    (never 500 the summary over a store_mapping read). Mirrors the sales-report resolver so the two
    #    pages agree on which market a store belongs to.
    import re as _re_em
    try:
        _sm_rows = (client.schema('commcalc').table('store_mapping')
                    .select('store_code,store_address,market').eq('org_id', org_id).execute().data) or []
    except Exception:
        _sm_rows = []

    def _lead_em(s):
        m = _re_em.match(r'\s*(\d+)', str(s or ''))
        return m.group(1) if m else ''
    _mkt_code, _mkt_addr, _mkt_num, _all_markets = {}, {}, {}, set()
    for s in _sm_rows:
        mk = (s.get('market') or '').strip()
        if not mk:
            continue
        _all_markets.add(mk)
        code = str(s.get('store_code') or '').strip()
        addr = str(s.get('store_address') or '').strip()
        if code:
            _mkt_code[code] = mk
        if addr:
            _mkt_addr[addr.lower()] = mk
        n = _lead_em(addr)
        if n:
            _mkt_num.setdefault(n, mk)

    def _market_for(store):
        st = str(store or '').strip()
        return (_mkt_addr.get(st.lower()) or _mkt_code.get(st) or _mkt_num.get(_lead_em(st)) or '')

    # ── Filter OPTIONS from the UNFILTERED union (+ storeops roster + store_mapping markets), so the
    #    pickers list every real store/market/rep present, not just what survived the current selection.
    opt_stores, opt_reps = set(), set()
    for r in rows:
        st = str(r.get('store') or '').strip()
        if st:
            opt_stores.add(st)
            mk = _market_for(st)
            if mk:
                _all_markets.add(mk)
        rp = str(r.get('salesperson') or '').strip()
        if rp and rp.lower() != 'admin':
            opt_reps.add(rp)
    try:
        for s in (client.schema('storeops').table('stores')
                  .select('address').eq('org_id', org_id).execute().data) or []:
            a = str(s.get('address') or '').strip()
            if a:
                opt_stores.add(a)
    except Exception:
        pass
    filters = {'stores': sorted(opt_stores), 'markets': sorted(_all_markets), 'reps': sorted(opt_reps)}

    # ── Apply the selected filters to the UNION rows SERVER-SIDE (before bucketing) — case-insensitive
    #    membership; a market filter resolves each row's store to its market. Empty selection = no filter.
    store_sel = {str(s).strip().lower() for s in (stores or []) if str(s).strip()}
    rep_sel = {str(s).strip().lower() for s in (reps or []) if str(s).strip()}
    market_sel = {str(s).strip().lower() for s in (markets or []) if str(s).strip()}
    applied = {'stores': sorted(store_sel), 'markets': sorted(market_sel), 'reps': sorted(rep_sel)}

    def _keep(r):
        st = str(r.get('store') or '').strip()
        if store_sel and st.lower() not in store_sel:
            return False
        if rep_sel and str(r.get('salesperson') or '').strip().lower() not in rep_sel:
            return False
        if market_sel and _market_for(st).strip().lower() not in market_sel:
            return False
        return True
    if store_sel or rep_sel or market_sel:
        rows = [r for r in rows if _keep(r)]

    # trending divisor: complete days elapsed (= yesterday's day-of-month) for the OPEN month; the full
    # month (factor 1) for a closed/past month. days_in_month from the calendar. `today` injectable.
    _today = today or _date.today()
    mo, yr = _month_year(period)
    dim = _calendar.monthrange(yr, mo)[1] if (1 <= mo <= 12 and yr) else 30
    open_m = _is_open_month(period)
    elapsed = max(1, _today.day - 1) if open_m else dim
    trend_factor = (dim / elapsed) if elapsed else 1.0
    # MTD DATE-CUT — an OPEN month is cumulative THROUGH TODAY (a line dated after today is excluded, so the
    # cumulative is a true month-to-date). Real sales are never future-dated, so this is a no-op on live
    # data; it is the ONE intentional difference from the Sales Report's whole-month total (proof case b).
    # A closed/past month has no cut (the whole month).
    cut = _today.isoformat() if open_m else None

    # THE shared per-(store, rep, day) pass — identical skip rules + classification + distinct-txn counting
    # as the Sales Report + Daily Targets (see _sales_cell_agg). Exec MTD consumes the SAME cells, applies
    # the MTD date-cut, then rolls up by store / employee. A trans_id lives in exactly one (store,rep,day)
    # cell, so SUMMING the per-cell distinct-txn counts across a store's cells == that store's distinct-txn
    # total. Activation = premium−port (Port is a sub-split of premium); Total Activation = prem+byod+upg,
    # which EQUALS the Sales Report's (activations+byod+upgrades) over the same rows.
    cells = _sales_cell_agg(rows, acfg, exec_cfg=cfg)

    def _blank():
        return {'activation': 0, 'port': 0, 'byod': 0, 'upgrade': 0, 'total_phones': 0,
                'bill_qty': 0, 'bill_amt': 0.0, 'acc_sales': 0.0, 'activation_fee': 0.0, 'protect': 0}
    by_store, by_emp = {}, {}
    for (st, rep, date), a in cells.items():
        if cut and date and date > cut:
            continue
        prem, port = len(a['_prem']), len(a['_port'])
        for d in (by_store.setdefault(st or '—', _blank()), by_emp.setdefault(rep or '—', _blank())):
            d['activation'] += prem - port
            d['port'] += port
            d['byod'] += len(a['_byod'])
            d['upgrade'] += len(a['_upg'])
            d['total_phones'] += a['total_phones']
            d['bill_qty'] += a['bill_qty']
            d['bill_amt'] += a['bill_amt']
            d['acc_sales'] += a['accessory_rev']
            d['activation_fee'] += a['activation_fee']
            d['protect'] += a['protect']

    def _row(name, label_key, d):
        ta = d['activation'] + d['port'] + d['byod'] + d['upgrade']
        return {label_key: name,
                'total_activation': ta, 'activation': d['activation'], 'port': d['port'],
                'byod': d['byod'], 'upgrade': d['upgrade'], 'total_phones': d['total_phones'],
                'trending_box': round(ta * trend_factor),
                'bill_payment_qty': d['bill_qty'], 'amount': round(d['bill_amt'], 2),
                'conv': round(ta / d['bill_qty'], 4) if d['bill_qty'] else 0.0,
                'acc_sales': round(d['acc_sales'], 2),
                'apb': round(d['acc_sales'] / ta, 2) if ta else 0.0,
                'trending_acc_sales': round(d['acc_sales'] * trend_factor, 2),
                'activation_fee': round(d['activation_fee'], 2),
                'total_protect': d['protect']}

    def _finish(agg, label_key):
        out = [_row(n, label_key, d) for n, d in agg.items()]
        out.sort(key=lambda x: -x['total_activation'])
        return out

    def _totals(rowset, label_key):
        t = {label_key: 'TOTAL'}
        for k in ('total_activation', 'activation', 'port', 'byod', 'upgrade', 'total_phones',
                  'trending_box', 'bill_payment_qty', 'amount', 'acc_sales', 'trending_acc_sales',
                  'activation_fee', 'total_protect'):
            t[k] = round(sum(r.get(k, 0) for r in rowset), 2)
        t['conv'] = round(t['total_activation'] / t['bill_payment_qty'], 4) if t['bill_payment_qty'] else 0.0
        t['apb'] = round(t['acc_sales'] / t['total_activation'], 2) if t['total_activation'] else 0.0
        return t

    store_rows = _finish(by_store, 'store')
    emps = _finish(by_emp, 'employee')
    return {'period': period, 'source': meta,
            'filters': filters, 'applied': applied,
            'trending': {'elapsed_days': elapsed, 'days_in_month': dim, 'factor': round(trend_factor, 6)},
            'by_location': {'rows': store_rows, 'total': _totals(store_rows, 'store')},
            'by_employee': {'rows': emps, 'total': _totals(emps, 'employee')}}


@router.get("/exec-mtd/{period}")
def exec_mtd(period: str, org_id: str = ORG_ID,
             stores: Optional[List[str]] = Query(default=None),
             markets: Optional[List[str]] = Query(default=None),
             reps: Optional[List[str]] = Query(default=None)):
    """Executive Month-To-Date summary (b2bsoft Location + Employee MTD sales) — replicated from the
    org-corrected sales source so it works for every tenant with NO monthly upload. DISPLAY-ONLY,
    org-scoped, config-driven metric definitions.

    RULE FIVE standardized filters: optional repeated `stores` / `markets` / `reps` query params filter
    the union rows SERVER-SIDE (before bucketing) so the tables, trending AND exports stay consistent.
    The response's `filters` object lists the pick-don't-type options over the org's real data."""
    require_org(org_id)
    return _exec_mtd(sb(), org_id, period, stores=stores, markets=markets, reps=reps)


@router.get("/exec-metric-config")
def get_exec_metric_config(org_id: str = ORG_ID):
    """The tenant's Executive-MTD metric DEFINITIONS (config, falling back to code defaults). Drives the
    admin-editable 'Metric definitions' panel — SAP-configurable, no hard-coded classifier."""
    require_org(org_id)
    cfg = _exec_metric_config(sb(), org_id)
    return {"buckets": list(_EXEC_BUCKETS), "config": cfg}


@router.put("/exec-metric-config")
def put_exec_metric_config(body: dict, org_id: str = ORG_ID):
    """Upsert one bucket's metric definition (org-scoped). body = {bucket, rules:{...}, basis}. Only the
    six known buckets are accepted. Degrades gracefully if mig 204 hasn't run (returns ok=false hint)."""
    require_org(org_id)
    bucket = str(body.get('bucket') or '').strip()
    if bucket not in _EXEC_BUCKETS:
        raise HTTPException(400, f"unknown bucket (allowed: {', '.join(_EXEC_BUCKETS)})")
    rules = body.get('rules') if isinstance(body.get('rules'), dict) else {}
    basis = 'ext_price' if str(body.get('basis') or 'count') == 'ext_price' else 'count'
    try:
        sb().schema('commcalc').table('exec_metric_config').upsert(
            {'org_id': org_id, 'bucket': bucket, 'rules': rules, 'basis': basis,
             'updated_at': _datetime.now(_timezone.utc).isoformat()},
            on_conflict='org_id,bucket').execute()
        return {"ok": True, "bucket": bucket}
    except Exception as e:
        return {"ok": False, "hint": "run migration 204 (exec_metric_config)", "error": str(e)[:200]}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PRODUCTIVITY · STACK-RANKING · PERFORMANCE-REVIEW  (mod-commission — NON-money, display/analytics)
# ---------------------------------------------------------------------------------------------------
# Feature 1: per-employee output-per-hour vs the store's own baseline. Feature 2: weighted stack ranking.
# Feature 3: performance review scorecard. ONE unified per-org item registry (commcalc.productivity_item,
# mig 215) drives both the ranker and the review. Hours come from StoreOps time-clock punches
# (storeops.timelog — the SAME closed-punch basis payroll_raw + the Daily-Closing 'who worked' use), joined
# to the SHARED commission sales aggregation (_sales_rows_union → _sales_cell_agg) — no forked classifier.
# The commission tie-in (perf KPI keys) is INERT: no calc engine reads this; activation is owner-gated.
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def _prod_org_rows(client, org_id):
    """The tenant's productivity_item override rows (mig 215). Degrades to [] (code defaults only)."""
    try:
        return (client.schema('commcalc').table('productivity_item').select('*')
                .eq('org_id', org_id).execute().data) or []
    except Exception:
        return []


def _prod_registry(client, org_id):
    return _prod.resolve_registry(_prod_org_rows(client, org_id))


def _prod_store_maps(client, org_id):
    """Resolvers so a raw sales store string joins to a storeops store_code (which is what timelog carries)
    and to a market. Mirrors _compute_feed_actuals_py / the sales-report resolver — never raises."""
    try:
        from app.modules.account import coa
        _resolve_store = coa.store_resolver(client, org_id)
    except Exception:
        _resolve_store = None
    try:
        sm = (client.schema('commcalc').table('store_mapping')
              .select('store_code,store_address,market').eq('org_id', org_id).execute().data) or []
    except Exception:
        sm = []
    addr_to_code, code_to_addr, code_set = {}, {}, set()
    mkt_by_code, mkt_by_addr, mkt_by_num, all_markets = {}, {}, {}, set()
    for m in sm:
        code = str(m.get('store_code') or '').strip()
        addr = str(m.get('store_address') or '').strip()
        mk = str(m.get('market') or '').strip()
        if code:
            code_set.add(code)
        if addr and code:
            addr_to_code[addr.lower()] = code
            code_to_addr.setdefault(code, addr)
        if mk:
            all_markets.add(mk)
            if code:
                mkt_by_code[code] = mk
            if addr:
                mkt_by_addr[addr.lower()] = mk
            mm = re.match(r'\s*(\d+)', addr)
            if mm:
                mkt_by_num.setdefault(mm.group(1), mk)
    num_to_code = {}
    for code, addr in code_to_addr.items():
        mm = re.match(r'\s*(\d+)', addr)
        if mm:
            num_to_code.setdefault(mm.group(1), code)

    def resolve_code(store_str):
        s = str(store_str or '').strip()
        if not s:
            return ''
        if s in code_set:
            return s
        canon = s
        if _resolve_store:
            try:
                canon = _resolve_store(s) or s
            except Exception:
                canon = s
        code = addr_to_code.get(str(canon).strip().lower()) or addr_to_code.get(s.lower())
        if code:
            return code
        mm = re.match(r'\s*(\d+)', s)
        if mm and mm.group(1) in num_to_code:
            return num_to_code[mm.group(1)]
        return s  # unresolved → raw string (won't match a timelog store_code; surfaces as no-hours)

    def market_for_code(code):
        c = str(code or '').strip()
        addr = code_to_addr.get(c, '')
        mm = re.match(r'\s*(\d+)', addr or c)
        return (mkt_by_code.get(c) or mkt_by_addr.get(addr.lower())
                or (mkt_by_num.get(mm.group(1)) if mm else '') or '')

    def label_for_code(code):
        c = str(code or '').strip()
        return code_to_addr.get(c) or c

    return {'resolve_code': resolve_code, 'market_for_code': market_for_code,
            'label_for_code': label_for_code, 'all_markets': all_markets}


def _prod_kpi_by_rep(client, org_id, period, cmap):
    """Per-rep KPI values (rep_commissions.kpi_values — the SAME snapshot the KPI Metrics page reads) plus a
    composite kpi_attainment (% of the 6 KPIs meeting their configured target). Keyed by canonical rep
    (UPPER). Degrades to {} (⇒ n/a). Targets from payout_config with the KPI-page defaults."""
    KPI = [('atu', 'kpi_atu', 'kpi_atu_target', 55.0), ('protect', 'kpi_protect', 'kpi_protect_target', 80.0),
           ('byod', 'kpi_byod', 'kpi_byod_target', 35.0), ('familyplan', 'kpi_familyplan', 'kpi_familyplan_target', 45.0),
           ('tmr3', 'kpi_tmr3', 'kpi_tmr3_target', 70.0), ('aal', 'kpi_aal', 'kpi_aal_target', 5.0)]
    cfg = {}
    try:
        c = (client.schema('commcalc').table('payout_config').select('*')
             .eq('org_id', org_id).in_('period', _pvariants(period)).limit(1).execute().data) or []
        cfg = c[0] if c else {}
    except Exception:
        cfg = {}
    tgt = {rk: (safe_float(cfg.get(tc)) if cfg.get(tc) is not None else dv) for (rk, sk, tc, dv) in KPI}
    out = {}
    try:
        rows = (client.schema('commcalc').table('rep_commissions')
                .select('salesperson,epay_salesperson,kpi_values')
                .eq('org_id', org_id).in_('period', _pvariants(period)).limit(20000).execute().data) or []
    except Exception:
        rows = []
    for r in rows:
        nm = (r.get('salesperson') or r.get('epay_salesperson') or '').strip()
        if not nm:
            continue
        key = _canon(nm, cmap).strip().upper()
        kv = r.get('kpi_values') or {}
        vals, met, total = {}, 0, 0
        for (rk, sk, tc, dv) in KPI:
            v = kv.get(rk)
            if v is None:
                continue
            fv = safe_float(v)
            vals[sk] = fv
            total += 1
            if fv >= tgt[rk]:
                met += 1
        if total:
            vals['kpi_attainment'] = round(met / total * 100, 1)
        d = out.setdefault(key, {})
        for k, v in vals.items():
            d[k] = v  # first non-empty wins per key (a rep appears once per period)
        for k, v in vals.items():
            d.setdefault(k, v)
    return out


def _prod_upkeep_by_code(client, org_id, start_iso, end_iso):
    """DM store-visit upkeep per store_code — the AVERAGE checklist pass-rate (checked ÷ total responses)
    across the COMPLETED (status='submitted') visits whose check_in_at falls in the period, ×100. A store
    with no completed visit in the period → absent (⇒ n/a). Read-only cross-module read (storeops schema)."""
    out = {}
    try:
        visits = (client.schema('storeops').table('store_visits')
                  .select('id,store_code,status,check_in_at').eq('org_id', org_id)
                  .gte('check_in_at', start_iso).lt('check_in_at', end_iso).limit(5000).execute().data) or []
    except Exception:
        return out
    vids = [v.get('id') for v in visits if v.get('status') == 'submitted' and v.get('store_code') and v.get('id')]
    if not vids:
        return out
    try:
        resp = (client.schema('storeops').table('store_visit_responses')
                .select('visit_id,checked').eq('org_id', org_id).in_('visit_id', vids)
                .limit(100000).execute().data) or []
    except Exception:
        return out
    by_visit = {}
    for r in resp:
        d = by_visit.setdefault(r.get('visit_id'), [0, 0])
        d[1] += 1
        if r.get('checked'):
            d[0] += 1
    rate_by_visit = {vid: (c[0] / c[1] * 100.0) for vid, c in by_visit.items() if c[1] > 0}
    acc = {}
    for v in visits:
        vid = v.get('id')
        code = str(v.get('store_code') or '').strip()
        if v.get('status') != 'submitted' or vid not in rate_by_visit or not code:
            continue
        a = acc.setdefault(code, [0.0, 0])
        a[0] += rate_by_visit[vid]
        a[1] += 1
    for code, a in acc.items():
        if a[1] > 0:
            out[code] = round(a[0] / a[1], 1)
    return out


def _prod_targets_by_code(client, org_id, period, maps):
    """Daily-Targets ACTIVATION attainment per store_code — store MTD activations achieved ÷ the store's
    activation target ×100 (the SAME targets table + _fetch_actuals + scope_achieved_mtd the Daily Targets
    pages use). Applied to the store's reps (a store metric → rep, like upkeep). Degrades to {} (⇒ n/a)."""
    out = {}
    try:
        actuals = _fetch_actuals(client, org_id, period)
    except Exception:
        return out
    try:
        trows = (client.schema('commcalc').table('targets').select('*')
                 .eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    except Exception:
        trows = []
    tgt_by_code = {}
    for t in trows:
        code = str(t.get('store_code') or '').strip().upper()
        av = t.get('activations_monthly')
        if code and av is not None:
            tgt_by_code[code] = safe_float(av)
    if not tgt_by_code:
        return out
    for code, tv in tgt_by_code.items():
        if tv <= 0:
            continue
        try:
            ach = targets_engine.scope_achieved_mtd(actuals, code, None, None)
        except Exception:
            continue
        out[code] = round(safe_float(ach.get('activations')) / tv * 100, 1)
    return out


def _prod_gather(client, org_id, period, stores=None, markets=None, reps=None, today=None):
    """Assemble everything the three productivity surfaces need from ONE pass of the shared sales
    aggregation + StoreOps hours + the cross-module source reads. Returns store_reps / hours_by_key /
    per_rep_values / filters / labels. RULE FIVE filters (stores/markets/reps) are applied SERVER-SIDE to
    the sales rows AND the hours before aggregation, so tables + exports stay consistent. Never raises on a
    cross-module read (each degrades to n/a)."""
    acfg = _accessory_config(client, org_id)
    cmap = _rep_canon_map(client, org_id)
    maps = _prod_store_maps(client, org_id)
    resolve_code = maps['resolve_code']
    rows, meta = _sales_rows_union(client, org_id, period)

    # ── filter OPTIONS from the UNFILTERED union (+ storeops roster) — pick-don't-type over real data.
    opt_stores, opt_reps = set(), set()
    for r in rows:
        st = str(r.get('store') or '').strip()
        if st:
            opt_stores.add(st)
        rp = str(r.get('salesperson') or '').strip()
        if rp and rp.lower() != 'admin':
            opt_reps.add(_canon(rp, cmap))
    try:
        for s in (client.schema('storeops').table('stores').select('address')
                  .eq('org_id', org_id).execute().data) or []:
            a = str(s.get('address') or '').strip()
            if a:
                opt_stores.add(a)
    except Exception:
        pass
    filters = {'stores': sorted(opt_stores), 'markets': sorted(maps['all_markets']), 'reps': sorted(opt_reps)}

    store_sel = {str(s).strip().lower() for s in (stores or []) if str(s).strip()}
    rep_sel = {str(s).strip().lower() for s in (reps or []) if str(s).strip()}
    market_sel = {str(s).strip().lower() for s in (markets or []) if str(s).strip()}
    applied = {'stores': sorted(store_sel), 'markets': sorted(market_sel), 'reps': sorted(rep_sel)}
    sel_codes = {resolve_code(s) for s in store_sel} if store_sel else None

    def _keep(r):
        st = str(r.get('store') or '').strip()
        if store_sel and st.lower() not in store_sel:
            return False
        if rep_sel and _canon(str(r.get('salesperson') or '').strip(), cmap).strip().lower() not in rep_sel:
            return False
        if market_sel and maps['market_for_code'](resolve_code(st)).strip().lower() not in market_sel:
            return False
        return True
    if store_sel or rep_sel or market_sel:
        rows = [r for r in rows if _keep(r)]

    cells = _sales_cell_agg(rows, acfg)
    store_reps, display_by_key, store_label, market_by_code = {}, {}, {}, {}
    rep_store_map = {}   # rep_key -> set(code)
    for (store_str, rep_str, day), a in cells.items():
        code = resolve_code(store_str)
        canon = _canon(rep_str, cmap)
        rep_key = canon.strip().upper()
        if not rep_key:
            continue
        display_by_key.setdefault(rep_key, canon or rep_str)
        store_label.setdefault(code, maps['label_for_code'](code) or store_str)
        market_by_code.setdefault(code, maps['market_for_code'](code))
        c = store_reps.get((code, rep_key))
        if not c:
            c = store_reps[(code, rep_key)] = {'store_label': store_label[code], 'market': market_by_code[code],
                                               'rep_label': display_by_key[rep_key], 'boxes': 0.0, 'acc_sales': 0.0,
                                               'activations': 0, 'upgrades': 0, 'swaps': 0, 'txns': 0}
        c['boxes'] += a['box_count']
        c['acc_sales'] += a['accessory_rev']
        c['activations'] += len(a['_prem'])
        c['upgrades'] += len(a['_upg'])
        c['swaps'] += len(a['_swap'])
        c['txns'] += len(a['_txn'])
        rep_store_map.setdefault(rep_key, set()).add(code)
    sales_codes = {code for (code, _rk) in store_reps.keys()}

    # ── StoreOps time-clock hours (closed punches only — the payroll_raw / who-worked basis), per
    #    (store_code, rep). Filtered to the same selection. Only kept at stores that had sales (avoids
    #    phantom stores from a store_code mismatch); a rep clocked-in-but-no-sales at a selling store is
    #    added with 0 output (real: on the clock, sold nothing → below baseline).
    hours_by_key, per_rep_hours = {}, {}
    try:
        start, end, _t = _period_bounds(period, today or '')
        tl = (client.schema('storeops').table('timelog')
              .select('employee_name,store_code,hours,clock_out,work_date').eq('org_id', org_id)
              .gte('work_date', start.isoformat()).lt('work_date', end.isoformat()).limit(50000).execute().data) or []
    except Exception:
        tl = []
    for t in tl:
        if not (t.get('clock_out') and t.get('hours') is not None):
            continue
        code = str(t.get('store_code') or '').strip()
        rep_key = _canon(t.get('employee_name'), cmap).strip().upper()
        if not rep_key:
            continue
        if sel_codes is not None and code not in sel_codes:
            continue
        if rep_sel and display_by_key.get(rep_key, '').strip().lower() not in rep_sel \
                and _canon(t.get('employee_name'), cmap).strip().lower() not in rep_sel:
            continue
        hrs = safe_float(t.get('hours'))
        per_rep_hours[rep_key] = per_rep_hours.get(rep_key, 0.0) + hrs
        if code in sales_codes:
            hours_by_key[(code, rep_key)] = hours_by_key.get((code, rep_key), 0.0) + hrs
            if (code, rep_key) not in store_reps:
                display_by_key.setdefault(rep_key, _canon(t.get('employee_name'), cmap) or (t.get('employee_name') or ''))
                store_reps[(code, rep_key)] = {'store_label': store_label.get(code, code),
                                               'market': market_by_code.get(code, ''),
                                               'rep_label': display_by_key.get(rep_key), 'boxes': 0.0, 'acc_sales': 0.0,
                                               'activations': 0, 'upgrades': 0, 'swaps': 0, 'txns': 0}
                rep_store_map.setdefault(rep_key, set()).add(code)

    # ── store-metric sources (upkeep, targets attainment) → applied to each store's reps.
    upkeep_by_code, targets_by_code = {}, {}
    try:
        s, e, _t = _period_bounds(period, today or '')
        upkeep_by_code = _prod_upkeep_by_code(client, org_id, s.isoformat(), e.isoformat())
    except Exception:
        upkeep_by_code = {}
    targets_by_code = _prod_targets_by_code(client, org_id, period, maps)
    # targets/upkeep tables key store_code case-sensitively in different places — index case-insensitively.
    upkeep_ci = {str(k).strip().upper(): v for k, v in upkeep_by_code.items()}
    targets_ci = {str(k).strip().upper(): v for k, v in targets_by_code.items()}
    kpi_by_rep = _prod_kpi_by_rep(client, org_id, period, cmap)

    # ── per-rep value map (across stores) for the ranker + review.
    per_rep_values = {}
    all_reps = set(rep_store_map.keys())
    for (code, rk) in store_reps.keys():
        all_reps.add(rk)
    for rk in all_reps:
        agg = {'acc_sales': 0.0, 'activations': 0, 'upgrades': 0, 'swaps': 0, 'boxes': 0.0}
        for (code, r2), c in store_reps.items():
            if r2 != rk:
                continue
            agg['acc_sales'] += c['acc_sales']; agg['activations'] += c['activations']
            agg['upgrades'] += c['upgrades']; agg['swaps'] += c['swaps']; agg['boxes'] += c['boxes']
        hrs = per_rep_hours.get(rk, 0.0)
        codes = sorted(rep_store_map.get(rk, set()))
        upk = [upkeep_ci[c.upper()] for c in codes if c.upper() in upkeep_ci]
        tga = [targets_ci[c.upper()] for c in codes if c.upper() in targets_ci]
        kp = kpi_by_rep.get(rk, {})
        vals = {
            'acc_sales': round(agg['acc_sales'], 2), 'activations': agg['activations'],
            'upgrades': agg['upgrades'], 'swaps': agg['swaps'], 'boxes': round(agg['boxes'], 2),
            'hours_worked': round(hrs, 2),
            'boxes_per_hour': (round(agg['boxes'] / hrs, 3) if hrs > 0 else None),
            'acc_per_hour': (round(agg['acc_sales'] / hrs, 2) if hrs > 0 else None),
            'store_upkeep': (round(sum(upk) / len(upk), 1) if upk else None),
            'targets_attainment': (round(sum(tga) / len(tga), 1) if tga else None),
            'kpi_attainment': kp.get('kpi_attainment'),
            'kpi_atu': kp.get('kpi_atu'), 'kpi_protect': kp.get('kpi_protect'), 'kpi_byod': kp.get('kpi_byod'),
            'kpi_familyplan': kp.get('kpi_familyplan'), 'kpi_tmr3': kp.get('kpi_tmr3'), 'kpi_aal': kp.get('kpi_aal'),
            '_label': display_by_key.get(rk, rk),
            '_market': (market_by_code.get(codes[0], '') if codes else ''),
            '_stores': [store_label.get(c, c) for c in codes],
        }
        per_rep_values[rk] = vals

    return {'store_reps': store_reps, 'hours_by_key': hours_by_key, 'per_rep_values': per_rep_values,
            'filters': filters, 'applied': applied, 'store_label': store_label,
            'market_by_code': market_by_code, 'source_meta': meta}


@router.get("/productivity/sources")
def get_productivity_sources(org_id: str = ORG_ID):
    """The pickable SOURCE CATALOG for the 'add item' affordance (RULE THREE — pick, don't type)."""
    require_org(org_id)
    return {"sources": _prod.source_catalog()}


@router.get("/productivity/config")
def get_productivity_config(org_id: str = ORG_ID):
    """The unified item registry (code defaults overlaid by the org overrides) + the source catalog + the
    perf KPI keys the (inert) commission tie-in exposes. Drives the ⚙️ admin config tab."""
    require_org(org_id)
    reg = _prod_registry(sb(), org_id)
    return {"items": reg, "sources": _prod.source_catalog(), "kpi_keys": _prod.perf_kpi_keys(reg),
            "value_types": ["number", "dollar", "percent", "score"]}


def _require_perf_review_edit(authorization, org_id):
    """Gate performance-review / productivity CONFIG writes on their OWN permission (owner directive
    2026-07-17: "config in performance review should have a separate permission"). Uses core's
    _can_edit_setting with the 'performance_review' settings area (the per-setting pattern —
    roles.permissions.settings['performance_review']). Until that area is registered in core.SETTING_AREAS +
    the Roles UI (NEEDS CORE), _can_edit_setting DEGRADES to admin-only (scope='all' / role='admin') — a
    SAFE default that never opens config to a non-admin. RBAC off / unidentifiable caller → no active block
    (config stays require_org-gated, unchanged behaviour)."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller, _can_edit_setting
    except Exception:
        return
    try:
        uid = _uid_from_token(authorization)
        caller = _resolve_caller(sb(), uid, org_id) if uid else None
    except Exception:
        caller = None
    if caller is not None and not _can_edit_setting(caller, 'performance_review'):
        raise HTTPException(403, "You don't have permission to edit performance-review configuration.")


@router.put("/productivity/config")
def put_productivity_config(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Upsert ONE registry item (add a custom item or edit/enable/disable a default). item_key required;
    source_key must be in the SOURCE CATALOG (pick-don't-type — no free-form formula). Degrades with a hint
    if mig 215 isn't applied. GATED on the 'performance_review' settings permission (B5)."""
    require_org(org_id)
    _require_perf_review_edit(authorization, org_id)
    item_key = str(body.get('item_key') or '').strip()
    if not item_key:
        raise HTTPException(400, "item_key required")
    source_key = str(body.get('source_key') or '').strip()
    if source_key and source_key not in _prod.SOURCE_CATALOG:
        raise HTTPException(400, f"unknown source_key (pick from the catalog): {source_key}")
    row = {'org_id': org_id, 'item_key': item_key}
    for c in ('label', 'source_key', 'standard_type'):
        if body.get(c) is not None:
            row[c] = str(body.get(c))
    if 'standard' in body:
        row['standard'] = None if body.get('standard') in (None, '') else safe_float(body.get('standard'))
    if 'weight' in body:
        row['weight'] = safe_float(body.get('weight'))
    for b in ('count_in_stack_ranker', 'count_in_review', 'enabled', 'hidden'):
        if b in body:
            row[b] = bool(body.get(b))
    if 'sort' in body:
        row['sort'] = int(safe_float(body.get('sort')))
    row['is_seed_default'] = item_key in {d['item_key'] for d in _prod.DEFAULT_ITEMS}
    row['updated_at'] = _datetime.now(_timezone.utc).isoformat()
    try:
        sb().schema('commcalc').table('productivity_item').upsert(row, on_conflict='org_id,item_key').execute()
        return {"ok": True, "item_key": item_key}
    except Exception as e:
        return {"ok": False, "hint": "run migration 215 (productivity_item)", "error": str(e)[:200]}


@router.delete("/productivity/config/{item_key}")
def delete_productivity_config(item_key: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Delete an item. A CUSTOM item is hard-deleted; a seed DEFAULT can't be removed from code, so it's
    persisted as a hidden override (restored by 'reset to defaults'). GATED on 'performance_review' (B5)."""
    require_org(org_id)
    _require_perf_review_edit(authorization, org_id)
    ik = str(item_key or '').strip()
    is_default = ik in {d['item_key'] for d in _prod.DEFAULT_ITEMS}
    try:
        if is_default:
            sb().schema('commcalc').table('productivity_item').upsert(
                {'org_id': org_id, 'item_key': ik, 'hidden': True, 'is_seed_default': True,
                 'updated_at': _datetime.now(_timezone.utc).isoformat()}, on_conflict='org_id,item_key').execute()
        else:
            sb().schema('commcalc').table('productivity_item').delete().eq('org_id', org_id).eq('item_key', ik).execute()
        return {"ok": True, "item_key": ik, "hidden": is_default}
    except Exception as e:
        return {"ok": False, "hint": "run migration 215 (productivity_item)", "error": str(e)[:200]}


@router.post("/productivity/config/reset")
def reset_productivity_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Reset the registry to the code defaults (delete all org overrides). GATED on 'performance_review' (B5)."""
    require_org(org_id)
    _require_perf_review_edit(authorization, org_id)
    try:
        sb().schema('commcalc').table('productivity_item').delete().eq('org_id', org_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "hint": "run migration 215 (productivity_item)", "error": str(e)[:200]}


@router.get("/productivity/{period}")
def get_productivity(period: str, org_id: str = ORG_ID, today: str = "",
                     stores: Optional[List[str]] = Query(default=None),
                     markets: Optional[List[str]] = Query(default=None),
                     reps: Optional[List[str]] = Query(default=None)):
    """FEATURE 1 — per-employee productivity (boxes/hr, accessory $/hr) vs the store's own baseline, grouped
    by store with rep rows. Hours = StoreOps time-clock closed punches. Zero-hours reps are surfaced (never
    divide by zero). RULE FIVE filters + RULE FOUR exports. DISPLAY-ONLY (no calc, no recompute)."""
    require_org(org_id)
    try:
        g = _prod_gather(sb(), org_id, period, stores=stores, markets=markets, reps=reps, today=today)
        res = _prod.compute_productivity(g['store_reps'], g['hours_by_key'])
        return {"period": period, "org_id": org_id, **res,
                "filters": g['filters'], "applied": g['applied'], "source_meta": g['source_meta']}
    except Exception as e:
        # A report must DEGRADE, not 500. Any unexpected cross-module condition → an empty, well-formed
        # payload the page renders as "no data" (never a crash / white screen).
        print(f"WARN productivity/{period} failed: {e}")
        return {"period": period, "org_id": org_id, "stores": [], "totals": {},
                "filters": {"stores": [], "markets": [], "reps": []}, "applied": {}, "error": str(e)[:200]}


@router.get("/productivity/rankings/{period}")
def get_productivity_rankings(period: str, org_id: str = ORG_ID, today: str = "",
                              stores: Optional[List[str]] = Query(default=None),
                              markets: Optional[List[str]] = Query(default=None),
                              reps: Optional[List[str]] = Query(default=None)):
    """FEATURE 2 — weighted stack ranking over the registry's count_in_stack_ranker items. Per-metric
    attainment is returned so a rep can see WHY their rank is what it is. RULE FIVE + RULE FOUR. NON-money."""
    require_org(org_id)
    try:
        client = sb()
        g = _prod_gather(client, org_id, period, stores=stores, markets=markets, reps=reps, today=today)
        reg = _prod_registry(client, org_id)
        res = _prod.compute_rankings(reg, g['per_rep_values'])
        return {"period": period, "org_id": org_id, **res,
                "filters": g['filters'], "applied": g['applied']}
    except Exception as e:
        print(f"WARN productivity/rankings/{period} failed: {e}")
        return {"period": period, "org_id": org_id, "items": [], "rows": [],
                "filters": {"stores": [], "markets": [], "reps": []}, "applied": {}, "error": str(e)[:200]}


@router.get("/productivity/review/{period}")
def get_productivity_review(period: str, org_id: str = ORG_ID, today: str = "",
                            stores: Optional[List[str]] = Query(default=None),
                            markets: Optional[List[str]] = Query(default=None),
                            reps: Optional[List[str]] = Query(default=None)):
    """FEATURE 3 — per-employee performance-review scorecard over the registry's count_in_review items,
    each measured against its definable standard (attainment %, weight, weighted score, total). A
    missing-source item shows n/a and is excluded from the total. RULE FIVE + RULE FOUR. NON-money."""
    require_org(org_id)
    try:
        client = sb()
        g = _prod_gather(client, org_id, period, stores=stores, markets=markets, reps=reps, today=today)
        reg = _prod_registry(client, org_id)
        res = _prod.compute_review(reg, g['per_rep_values'])
        return {"period": period, "org_id": org_id, **res,
                "filters": g['filters'], "applied": g['applied']}
    except Exception as e:
        # The Performance Review page must never "error out" — degrade to an empty scorecard set.
        print(f"WARN productivity/review/{period} failed: {e}")
        return {"period": period, "org_id": org_id, "items": [], "rows": [],
                "filters": {"stores": [], "markets": [], "reps": []}, "applied": {}, "error": str(e)[:200]}


@router.get("/productivity/kpi-values/{period}")
def get_productivity_kpi_values(period: str, org_id: str = ORG_ID,
                                stores: Optional[List[str]] = Query(default=None),
                                markets: Optional[List[str]] = Query(default=None),
                                reps: Optional[List[str]] = Query(default=None)):
    """COMMISSION TIE-IN (INERT / read-only) — per-rep performance KPI values a payout engine COULD
    reference: 'performance_score' (weighted review score) + 'perf:<item_key>' (per-item attainment %).
    INERT: no calc engine reads this; wiring it into payout is a separate owner-gated, money-touching step
    (see the module return). Zero payout change until then."""
    require_org(org_id)
    client = sb()
    g = _prod_gather(client, org_id, period, stores=stores, markets=markets, reps=reps)
    reg = _prod_registry(client, org_id)
    review = _prod.compute_review(reg, g['per_rep_values'])
    keys = _prod.perf_kpi_keys(reg)
    out = []
    for r in review['rows']:
        vals = {k['kpi_key']: _prod.perf_kpi_value(k['kpi_key'], r['review_score'], r['items']) for k in keys}
        out.append({"rep": r['rep'], "rep_key": r['rep_key'], "values": vals})
    return {"period": period, "org_id": org_id, "kpi_keys": keys, "rows": out,
            "inert": True, "note": "Registerable in a Commission Plan; inert until an engine is wired to "
            "resolve these keys AND the owner recalcs."}


def _acc_flags_by_rep(client, org_id, period):
    """Lightweight per-rep accessory-flag counts (over/under threshold) for the action plan — ONE read of
    raw_sales via the configurable accessory classifier. NO item_mapping load/write (unlike the full
    accessory-flags report), so it's cheap on the hot action-plan path. {REP_UPPER: {flags,over,under,total}}."""
    rules = _flag_rules(client, org_id)
    threshold = safe_float(rules.get("accessory_threshold"))
    min_t = safe_float(rules.get("accessory_min_threshold"))
    acfg = _accessory_config(client, org_id)
    rows = (client.schema("commcalc").table("raw_sales")
            .select("salesperson,department,category,product_desc,ext_price,voided,trans_type")
            .eq("org_id", org_id).in_("period", _pvariants(period)).limit(200000).execute().data) or []
    out = {}
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        pd = (r.get("product_desc") or "").lower()
        if "boost protect" in pd or "xfinity" in pd:
            continue
        if not _is_accessory(r.get("department"), r.get("category"), r.get("product_desc"), acfg):
            continue
        price = safe_float(r.get("ext_price"))
        over = price > threshold
        under = min_t > 0 and price < min_t
        if not (over or under):
            continue
        rep = (r.get("salesperson") or "").strip().upper()
        if not rep:
            continue
        a = out.setdefault(rep, {"flags": 0, "over": 0, "under": 0, "total": 0.0})
        a["flags"] += 1
        a["over"] += 1 if over else 0
        a["under"] += 1 if under else 0
        a["total"] += price
    return out


@router.get("/targets/{period}/action-plan")
async def get_action_plan(period: str, today: str = "", store_code: str = "", rep: str = "",
                          authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Daily Action Plan — prioritized focus areas per store (per-category catch-up
    + conversion) and per rep (conversion + commission-at-risk). Reuses the SAME
    targets engine + conversion the Daily Targets pages use, plus the computed
    rep_commissions (tier/KPIs) so 'commission at risk' reconciles with the payroll."""
    client = sb()
    start, end, today = _period_bounds(period, today)
    byod_def = _byod_pct_default(client, period, org_id)
    month_end = end - _timedelta(days=1)

    trows = (client.schema('commcalc').table('targets')
             .select('*').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    by_code = {str(r.get('store_code', '')).upper(): r for r in trows}
    stores = (client.schema('storeops').table('stores')
              .select('store_code,address,market,monthly_target').eq('org_id', org_id).execute().data) or []
    shifts = _fetch_shifts(client, start, end, org_id)
    actuals = _fetch_actuals(client, org_id, period)
    rank = targets_engine.SEV_RANK

    # ── Commission context: KPI targets + each rep's computed tier ($ at risk = the
    #    payout forfeited below tier 1.0 = subtotal × (1 − tier)). Empty/graceful if
    #    commissions haven't been run for the period yet.
    cfg_rows = (client.schema('commcalc').table('payout_config')
                .select('*').eq('org_id', org_id).in_('period', _pvariants(period)).limit(1).execute().data) or []
    cfg = cfg_rows[0] if cfg_rows else {}
    kpi_targets = {k: (safe_float(cfg.get(col)) or float(dv)) for (k, _l, col, dv) in _kpi_defs(org_id)}
    t100 = int(cfg.get('tier_100_min_kpis') or 7)
    t75 = int(cfg.get('tier_75_min_kpis') or 5)
    comm_rows = (client.schema('commcalc').table('rep_commissions')
                 .select('storeops_name,epay_salesperson,tier,kpis_met,total_kpis,'
                         'kpi_values,subtotal,total_payout')
                 .eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    comm_by_rep = {}
    for cr in comm_rows:
        key = (cr.get('storeops_name') or cr.get('epay_salesperson') or '').strip().upper()
        if key:
            comm_by_rep[key] = cr

    # Accessory flags per rep (over/under-priced accessory sales) — surfaced as a rep action item so
    # employees see their accessory-pricing issues alongside conversion + commission.
    try:
        acc_flag_by_rep = _acc_flags_by_rep(client, org_id, period)   # {REP_UPPER: {flags,over,under,total}} — light, read-only
    except Exception:
        acc_flag_by_rep = {}

    def rep_commission(rep_name):
        cr = comm_by_rep.get((rep_name or '').strip().upper())
        if not cr:
            return None
        tier = safe_float(cr.get('tier'))
        subtotal = safe_float(cr.get('subtotal'))
        kv = cr.get('kpi_values') or {}
        kpis = []
        for (k, lab, _col, _dv) in _kpi_defs(org_id):
            actual = safe_float(kv.get(k))
            kpis.append({'kpi': k, 'label': lab, 'target': kpi_targets[k],
                         'actual': round(actual, 1), 'met': actual >= kpi_targets[k]})
        kpis_met = cr.get('kpis_met')
        if kpis_met is None:
            kpis_met = sum(1 for x in kpis if x['met'])
        return {'tier': tier, 'kpis_met': kpis_met, 'total_kpis': cr.get('total_kpis') or 7,
                'subtotal': round(subtotal, 2),
                'total_payout': round(safe_float(cr.get('total_payout')), 2),
                'at_risk': round(subtotal * (1.0 - tier), 2) if tier < 1.0 else 0.0,
                'short_kpis': [x['label'] for x in kpis if not x['met']],
                'kpis': kpis, 't100': t100, 't75': t75}

    def commission_item(comm):
        if not comm or comm['tier'] >= 1.0:
            return None
        sev = 'critical' if comm['kpis_met'] < t75 else 'warning'
        need_next = max(0, t100 - comm['kpis_met'])
        short = ', '.join(comm['short_kpis']) or '—'
        tail = f' — hit {need_next} more KPI(s) for full payout.' if need_next else '.'
        return {'severity': sev, 'metric': 'commission',
                'title': f'Commission at risk — {int(round(comm["tier"] * 100))}% tier',
                'detail': f'{comm["kpis_met"]}/{comm["total_kpis"]} KPIs met; short on {short}. '
                          f'${comm["at_risk"]:,.0f} of commission at risk this period{tail}'}

    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / enforcement off)

    out = []
    tot_crit = tot_warn = 0
    tot_at_risk = 0.0
    for s in stores:
        code = str(s.get('store_code', '') or '').strip()
        if not code:
            continue
        if store_code and code.upper() != store_code.strip().upper():
            continue
        if ks is not None and not in_keyset(ks, code, s.get('address')):
            continue   # outside the signed-in manager's span
        trow = by_code.get(code.upper())
        if not trow:
            trow = {'accessories_monthly': safe_float(s.get('monthly_target'))}
        monthly = targets_engine.derive_monthly_by_cat(trow, byod_def)
        if sum(monthly.values()) <= 0:
            continue
        hours_by_day = targets_engine.scope_hours_by_day(shifts, code, None)
        actuals_by_day = targets_engine.scope_actuals_by_day(actuals, code, None)
        res = targets_engine.compute_scope(monthly, hours_by_day, actuals_by_day, today,
                                           round_counts=True, month_end=month_end)
        store_conv = targets_engine.scope_conversion(actuals, code, None, today)
        store_items = targets_engine.build_action_items(res, store_conv, include_categories=True)

        # Target-vs-achieved metrics per category (what's expected vs what they're doing).
        metrics = []
        for cat in targets_engine.CATEGORIES:
            m = res['categories'].get(cat) or {}
            if (m.get('monthly') or 0) <= 0:
                continue
            metrics.append({'cat': cat, 'label': _AP_CAT_LABEL.get(cat, cat),
                            'unit': m.get('unit', 'count'), 'target': m.get('monthly', 0),
                            'achieved': m.get('achieved_mtd', 0), 'need': m.get('need', 0),
                            'pace': m.get('pace', 0), 'today_target': m.get('today_target', 0),
                            # device set-up fee portion of accessory achieved (0 for non-accessory cats),
                            # reported separately (owner directive 2026-07-17).
                            'setup_fee_mtd': m.get('setup_fee_mtd', 0)})

        rep_plans = []
        for rep_name in targets_engine.reps_in_scope(shifts, actuals, code):
            rep_conv = targets_engine.scope_conversion(actuals, code, rep_name, today)
            below = rep_conv['rate'] < store_conv['rate']
            rep_items = targets_engine.build_action_items(
                {'categories': {}, 'open_days_total': 0}, rep_conv,
                include_categories=False, rep_below_store=below)
            comm = rep_commission(rep_name)
            citem = commission_item(comm)
            if citem:
                rep_items.append(citem)
            _af = acc_flag_by_rep.get((rep_name or '').strip().upper())
            if _af and (_af.get('flags') or 0) > 0:
                _o, _u = int(_af.get('over') or 0), int(_af.get('under') or 0)
                _lbl = ', '.join([p for p in (f'{_o} over-priced' if _o else '', f'{_u} under-priced' if _u else '') if p]) or 'check pricing'
                rep_items.append({'severity': 'warning', 'metric': 'accessory',
                                  'title': f'{int(_af["flags"])} accessory flag(s)',
                                  'detail': f'{_lbl}; ${safe_float(_af.get("total")):,.0f} rung in flagged accessory sales — review pricing & attach.'})
            if not rep_items:
                continue  # nothing to flag and no commission row
            rep_items.sort(key=lambda it: rank.get(it['severity'], 9))
            rep_plans.append({'rep': rep_name, 'conversion': rep_conv, 'below_store': below,
                              'items': rep_items, 'commission': comm})

        if rep:
            rep_plans = [rp for rp in rep_plans
                         if rp['rep'].strip().upper() == rep.strip().upper()]
            # Cross-store rep view (rep picked, no single store): drop stores this rep didn't
            # work, and focus on the rep — suppress store-level items.
            if not store_code:
                if not rep_plans:
                    continue
                store_items = []
        store_at_risk = round(sum((rp['commission'] or {}).get('at_risk', 0)
                                  for rp in rep_plans if rp.get('commission')), 2)
        if store_at_risk > 0 and not (rep and not store_code):
            n = sum(1 for rp in rep_plans if (rp.get('commission') or {}).get('at_risk', 0) > 0)
            store_items.append({'severity': 'warning', 'metric': 'commission',
                                'title': 'Commission at risk (store)',
                                'detail': f'${store_at_risk:,.0f} across {n} rep(s) below full KPI '
                                          f'tier — see the rep breakdown.'})
        store_items.sort(key=lambda it: rank.get(it['severity'], 9))
        tot_at_risk += store_at_risk

        all_items = store_items + [it for rp in rep_plans for it in rp['items']]
        c = sum(1 for it in all_items if it['severity'] == 'critical')
        w = sum(1 for it in all_items if it['severity'] == 'warning')
        tot_crit += c
        tot_warn += w
        rep_plans.sort(key=lambda rp: min((rank.get(it['severity'], 9) for it in rp['items']),
                                          default=9))
        out.append({
            'store_code': code, 'address': s.get('address'), 'market': s.get('market'),
            'conversion': store_conv, 'metrics': metrics, 'items': store_items,
            'reps': rep_plans, 'commission_at_risk': store_at_risk,
            'counts': {'critical': c, 'warning': w},
        })

    # Stores needing the most attention first.
    out.sort(key=lambda r: (-r['counts']['critical'], -r['counts']['warning'],
                            str(r.get('address') or r.get('store_code') or '')))
    return {'period': period, 'today': today.isoformat(),
            'summary': {'critical': tot_crit, 'warning': tot_warn, 'stores': len(out),
                        'commission_at_risk': round(tot_at_risk, 2)},
            'stores': out}



# ── Rep name mapping / merge (#4 — dedupe same-person variants) ────────────────
@router.get("/rep-aliases")
async def get_rep_aliases(org_id: str = ORG_ID):
    """Existing alias->canonical merges + all distinct rep name-strings seen (shifts +
    DLAR), to drive the merge UI."""
    client = sb()
    configured = True
    aliases = []
    try:
        aliases = (client.schema('commcalc').table('rep_aliases').select('*')
                   .eq('org_id', org_id).order('canonical').execute().data) or []
    except Exception:
        configured = False  # migration 016 not run yet — still return names so the UI shows dupes
    names = set()
    try:
        for s in (client.schema('storeops').table('shifts').select('employee_name')
                  .eq('org_id', org_id).eq('is_deleted', False).limit(50000).execute().data or []):
            n = (s.get('employee_name') or '').strip()
            if n:
                names.add(n)
    except Exception:
        pass
    try:
        for r in (client.schema('commcalc').table('raw_dlar_rep').select('rep_name')
                  .eq('org_id', org_id).limit(50000).execute().data or []):
            n = (r.get('rep_name') or '').strip()
            if n:
                names.add(n)
    except Exception:
        pass
    return {"configured": configured, "aliases": aliases, "names": sorted(names)}


@router.post("/rep-aliases")
async def post_rep_aliases(body: dict, org_id: str = ORG_ID):
    """Merge rep name-variants into one canonical. Body: {canonical, aliases:[...]}."""
    canonical = (body.get('canonical') or '').strip()
    aliases = body.get('aliases') or []
    if not canonical or not isinstance(aliases, list) or not aliases:
        raise HTTPException(400, "canonical + aliases[] required")
    client = sb()
    merged = 0
    for a in aliases:
        a = (a or '').strip()
        if not a or a.upper() == canonical.upper():
            continue
        try:
            client.schema('commcalc').table('rep_aliases').upsert(
                {'org_id': org_id, 'alias': a, 'canonical': canonical},
                on_conflict='org_id,alias').execute()
            merged += 1
        except Exception as e:
            raise HTTPException(500, f"save failed - run migration 016_rep_aliases.sql first: {e}")
    return {"merged": merged, "canonical": canonical}


@router.delete("/rep-aliases/{alias}")
async def delete_rep_alias(alias: str, org_id: str = ORG_ID):
    client = sb()
    try:
        client.schema('commcalc').table('rep_aliases').delete() \
            .eq('org_id', org_id).eq('alias', alias).execute()
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"deleted": alias}



# ── Store Expenses (#11 — schema-correct CRUD; old page used the dead public table) ──
def _exp_period_key(p):
    try:
        pp = parse_period(p)
        return (pp['year'], pp['month'])
    except Exception:
        return (0, 0)


@router.get("/expenses/{period}")
async def get_expenses(period: str, org_id: str = ORG_ID):
    """Store expenses for a period. STICKY: if the period has none yet, carry forward the latest
    prior period's expenses (returned pre-filled with carried_from set) so they persist month-to-month
    until changed — the user reviews and Saves to keep them for this period."""
    client = sb()
    rows = (client.schema('commcalc').table('store_expenses').select('*')
            .eq('org_id', org_id).in_('period', _pvariants(period)).order('store_code').execute().data) or []
    carried_from = None
    if not rows:
        allp = (client.schema('commcalc').table('store_expenses').select('period')
                .eq('org_id', org_id).execute().data) or []
        cur = _exp_period_key(period)
        priors = sorted({p['period'] for p in allp if p.get('period') and _exp_period_key(p['period']) < cur},
                        key=_exp_period_key)
        if priors:
            carried_from = priors[-1]
            rows = (client.schema('commcalc').table('store_expenses').select('*')
                    .eq('org_id', org_id).eq('period', carried_from).order('store_code').execute().data) or []
    return {"period": period, "expenses": rows, "carried_from": carried_from}


# ── SYSTEM (auto-computed) expense lines ─────────────────────────────────────────────────────────
# A "system line" is a store-expense row written by an automated producer (e.g. mod-people's payroll run
# inserting the per-store 'Paid Leave Accumulated' PTO accrual) rather than typed by a human. It is tagged
# with a non-null `source_key` (mig 206) so the UI can render it read-only and the MANUAL expense paths
# (put_expenses / bulk-apply / apply-to-months) never overwrite or copy it. NULL source_key == manual.
# Everything degrades gracefully before mig 206: the column is absent → no system rows can exist → every
# guard falls back to the pre-existing behavior (there is nothing to protect yet).

def _is_missing_col_err(e, col='source_key'):
    """True ONLY when a PostgREST error is 'column <col> does not exist' / schema-cache miss (pre-migration),
    never a transient/network error — so a guarded query degrades to the unguarded path only when the column
    genuinely isn't there yet. Both the PG (42703) and PostgREST schema-cache messages name the column."""
    s = str(e).lower()
    return (col in s) or ('42703' in s)


def _system_line_expand(org_id, period, source_key, label, cells, expense_type='Fixed'):
    """PURE (no DB, unit-testable): expand a system-line payload into store_expenses INSERT rows tagged with
    `source_key` (marks the row AUTO). Accepts `store` or `store_code` per cell; last-write-wins per store;
    drops blank stores and zero/blank amounts. org_id/period are baked in here — the caller stamps nothing."""
    etype = str(expense_type or 'Fixed').strip() or 'Fixed'
    by_store = {}
    for c in cells or []:
        sc = str((c or {}).get('store') or (c or {}).get('store_code') or '').strip()
        if not sc:
            continue
        try:
            amt = float((c or {}).get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        by_store[sc] = amt          # last write wins per store
    return [{'org_id': org_id, 'period': period, 'store_code': sc, 'expense_name': label,
             'expense_type': etype, 'amount': amt, 'source_key': source_key}
            for sc, amt in by_store.items() if amt != 0]


def _delete_manual_expenses(client, org_id, pv, extra=None):
    """Delete MANUAL (source_key IS NULL) store_expense rows for (org, period∈pv) — NEVER an auto system
    line. `extra` = optional {column: value|list} additional filters (e.g. expense_name / store_code list).
    Degrades to the pre-mig-206 unguarded delete when store_expenses.source_key is absent (no system rows
    exist yet, so the fallback is safe)."""
    def _base():
        q = client.schema('commcalc').table('store_expenses').delete().eq('org_id', org_id).in_('period', pv)
        for k, v in (extra or {}).items():
            q = q.in_(k, v) if isinstance(v, list) else q.eq(k, v)
        return q
    try:
        _base().is_('source_key', 'null').execute()
    except Exception as e:
        if _is_missing_col_err(e):
            _base().execute()
        else:
            raise


def _system_line_keys(client, org_id, pv):
    """The set of (store_code, expense_name) that are AUTO system lines for (org, period∈pv) — used to stop a
    manual save from shadowing a system line with a duplicate manual row (which would double-count in GP).
    Empty pre-mig-206 (column absent → select raises → caught)."""
    try:
        rows = (client.schema('commcalc').table('store_expenses')
                .select('store_code,expense_name,source_key')
                .eq('org_id', org_id).in_('period', pv).execute().data) or []
        return {(str(r.get('store_code') or ''), str(r.get('expense_name') or ''))
                for r in rows if r.get('source_key')}
    except Exception:
        return set()


@router.put("/expenses/{period}")
async def put_expenses(period: str, body: dict, org_id: str = ORG_ID):
    """Replace all MANUAL expenses for the period (matrix save + bulk upload). Body:
    {rows:[{store_code, expense_name, expense_type, amount}]}. Zero/blank rows are dropped.
    AUTO 'system' lines (source_key not null — e.g. the payroll-computed Paid Leave Accumulated) are
    NEVER deleted or shadowed here: the delete is manual-only, and an incoming row that collides with a
    system (store, expense) is dropped (the system line owns that cell), so GP never double-counts."""
    rows = body.get('rows') or []
    client = sb()
    pv = _pvariants(period)
    sys_keys = _system_line_keys(client, org_id, pv)
    _delete_manual_expenses(client, org_id, pv)          # deletes MANUAL rows only — protects system lines
    ins = []
    for r in rows:
        try:
            amt = float(r.get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        if amt == 0 or not (r.get('store_code') and r.get('expense_name')):
            continue
        sc, nm = str(r['store_code']).strip(), str(r['expense_name']).strip()
        if (sc, nm) in sys_keys:                          # never shadow an auto system line with a manual dup
            continue
        ins.append({'org_id': org_id, 'period': period, 'store_code': sc, 'expense_name': nm,
                    'expense_type': (r.get('expense_type') or 'Fixed'), 'amount': amt})
    for i in range(0, len(ins), 500):
        client.schema('commcalc').table('store_expenses').insert(ins[i:i + 500]).execute()
    return {"saved": len(ins), "period": period}


def _bulk_apply_expand(cells):
    """Pure payload→rows expansion for the bulk-apply endpoint (kept separate so it's unit-testable
    without a DB). Groups the requested cells by expense_name; for each expense name it returns the set
    of affected store_codes (to CLEAR) and the non-zero rows (to INSERT). Last write wins on a repeated
    (store, expense) pair → the expansion is idempotent. Returns (by_expense, ins, cleared) where
    by_expense maps expense_name -> {store_code: {'type','amount'}}."""
    by_expense = {}
    for c in cells or []:
        sc = str((c or {}).get('store_code') or '').strip()
        nm = str((c or {}).get('expense_name') or '').strip()
        if not sc or not nm:
            continue
        try:
            amt = float(c.get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        by_expense.setdefault(nm, {})[sc] = {
            'type': (c.get('expense_type') or 'Fixed'), 'amount': amt}
    ins, cleared = [], 0
    for nm, stores in by_expense.items():
        for sc, v in stores.items():
            if v['amount'] == 0:
                cleared += 1          # amount 0 = clear the cell (delete, no re-insert)
                continue
            ins.append({'store_code': sc, 'expense_name': nm,
                        'expense_type': v['type'], 'amount': v['amount']})
    return by_expense, ins, cleared


@router.post("/expenses/{period}/bulk-apply")
async def bulk_apply_expenses(period: str, body: dict, org_id: str = ORG_ID):
    """Idempotent per-CELL upsert of specific (store, expense) cells for a period — powers the
    'copy one column to many stores' and 'multi-store common expense' bulk actions in ONE request
    (never N sequential saves). Body: {cells:[{store_code, expense_name, expense_type, amount}]}.
    Unlike PUT /expenses (which FULL-replaces the whole period), this touches ONLY the
    (store_code, expense_name) pairs in the payload — every other cell in the period is left as-is.
    amount 0 CLEARS the cell. Delete-then-insert-nonzero per (expense_name × affected stores) → no
    unique index required and safe to re-run. org-scoped on every read AND write."""
    require_org(org_id)
    by_expense, ins_bare, cleared = _bulk_apply_expand(body.get('cells') or [])
    client = sb()
    pv = _pvariants(period)
    # Clear every affected (period, expense_name, store_code) cell. Compound (store,expense) IN isn't
    # expressible in one PostgREST filter, so we group by expense_name (one delete per expense name).
    for nm, stores in by_expense.items():
        scodes = list(stores.keys())
        for i in range(0, len(scodes), 200):
            # manual-only delete → a bulk apply can never clobber an auto system line, even on a name collision
            _delete_manual_expenses(client, org_id, pv,
                                    {'expense_name': nm, 'store_code': scodes[i:i + 200]})
    ins = [{'org_id': org_id, 'period': period, **row} for row in ins_bare]
    for i in range(0, len(ins), 500):
        client.schema('commcalc').table('store_expenses').insert(ins[i:i + 500]).execute()
    stores_touched = len({sc for st in by_expense.values() for sc in st})
    return {"saved": len(ins), "cleared": cleared, "cells": len(ins) + cleared,
            "stores": stores_touched, "expenses": len(by_expense), "period": period}


@router.post("/expenses/{period}/system-line")
async def upsert_expense_system_line(period: str, body: dict, org_id: str = ORG_ID):
    """RECEIVER for an AUTO-COMPUTED ('system') store-expense line. mod-people's payroll run is the CALLER:
    it computes the per-store cost (e.g. 'Paid Leave Accumulated' / PTO accrual) and POSTs it here to be
    inserted into the Store Expenses matrix. The line coexists with manual expenses and rolls into the SAME
    GP/P&L totals (store_expenses is summed by amount, agnostic of source_key) so the PTO cost hits the books.
    Body: { source_key:'pto_accrual', label:'Paid Leave Accumulated',
            expense_type:'Fixed'(optional), cells:[{store:'<store_code>', amount:<number>}, ...] }
    IDEMPOTENT: each call REPLACES the prior values for this (org, period, source_key) — delete-by-
    (org,period,source_key) then insert the non-zero cells — so a re-run of payroll never double-writes.
    Rows are tagged with source_key (marks them AUTO) so the UI renders them read-only and the MANUAL paths
    (put_expenses / bulk-apply / apply-to-months) never overwrite or copy them. org-scoped (org_id query
    param + require_org); stamps org_id on writes; _pvariants for period-spelling.
    MONEY: NON-money on the PAY path — it only INSERTS a cost line (feeds GP/P&L); no commission payout,
    rate, tier, or plan changes, and it does NOT recompute anything. Returns {ok, period, source_key, label,
    stores_written, total}."""
    require_org(org_id)
    source_key = str(body.get('source_key') or '').strip()
    label = str(body.get('label') or '').strip()
    if not source_key:
        return {"ok": False, "error": "source_key required"}
    if not label:
        return {"ok": False, "error": "label required"}
    ins = _system_line_expand(org_id, period, source_key, label,
                              body.get('cells') or [], body.get('expense_type') or 'Fixed')
    client = sb()
    pv = _pvariants(period)
    # Replace the prior values for THIS (source_key, period): delete-by-source_key then insert the non-zero
    # cells. Never touches manual rows or another producer's source_key → idempotent, no double-write.
    try:
        client.schema('commcalc').table('store_expenses').delete() \
            .eq('org_id', org_id).in_('period', pv).eq('source_key', source_key).execute()
    except Exception as e:
        if _is_missing_col_err(e):
            return {"ok": False, "error": "store_expenses.source_key column missing",
                    "hint": "run migration 206_commission_expense_system_line.sql (band 200-299)",
                    "period": period, "source_key": source_key}
        raise
    for i in range(0, len(ins), 500):
        client.schema('commcalc').table('store_expenses').insert(ins[i:i + 500]).execute()
    total = round(sum(float(r['amount']) for r in ins), 2)
    return {"ok": True, "period": period, "source_key": source_key, "label": label,
            "stores_written": len(ins), "total": total}


# ── EXPENSES: apply a source month across many months (except commission/salary) ─────────────────
# RULE TWO: the PROTECTED set (expenses NEVER copied across months) is CONFIG, not a magic list baked
# into the handler. It lives in commcalc.expense_apply_config (org-scoped, admin-editable via
# GET/PUT /expenses/apply-config) and matches an expense_name case-insensitively by SUBSTRING token.
# The code default {commission, salary} applies until a tenant configures its own set, so protection
# holds everywhere even before mig 205 runs.
# Default protected tokens. 'salaries' is listed alongside 'salary' because the match is a plain substring
# and the real category names are the PLURAL "Employee Salaries" / "Owner / Mgmt Salaries" (which 'salary'
# alone would miss). All are lowercase; the match lowercases the expense name.
_EXPENSE_APPLY_DEFAULT_TOKENS = ['commission', 'salary', 'salaries']


def _expense_apply_tokens(client, org_id):
    """The configured expense-name tokens EXCLUDED from cross-month apply (case-insensitive substring
    match on expense_name). Read from commcalc.expense_apply_config (org-scoped); falls back to the seed
    default {commission, salary} when the table/rows are absent — degrades gracefully before mig 205."""
    try:
        rows = (client.schema('commcalc').table('expense_apply_config')
                .select('token').eq('org_id', org_id).execute().data) or []
        toks = [str(r.get('token') or '').strip() for r in rows if str(r.get('token') or '').strip()]
        if toks:
            return toks
    except Exception:
        pass
    return list(_EXPENSE_APPLY_DEFAULT_TOKENS)


def _apply_to_months_expand(source_cells, target_periods, excluded_tokens=None, selection=None):
    """Pure expansion for 'apply expenses across months' (unit-testable, no DB — mirrors _bulk_apply_expand).
    Inputs: the SOURCE month's (store, expense) cells; a list of TARGET periods; the configured EXCLUDED
    tokens (case-insensitive substring on expense_name — commission/salary by default); an optional
    `selection` of expense names to copy (None/empty = all-except-excluded). Returns (rows, affected, skipped):
      rows     — insert rows {period, store_code, expense_name, expense_type, amount} (nonzero only)
      affected — {period: {expense_name: [store_code,...]}} the EXACT cells to delete-then-insert (never a
                 whole-month wipe)
      skipped  — sorted list of source expense names dropped because they matched an exclusion token
    Excluded expenses are NEVER copied (commission/salary protection). Idempotent: re-expanding identical
    inputs yields identical rows/affected → the endpoint's per-cell delete-then-insert is safe to re-run.
    A target equal to a prior target (dup) is collapsed; the source cells are org-agnostic (the caller reads
    them org-scoped and stamps org_id on the insert)."""
    toks = [str(t).strip().lower() for t in (excluded_tokens or []) if str(t).strip()]
    sel = None
    if selection:
        sel = {str(s).strip().lower() for s in selection if str(s).strip()}

    def excluded(nm):
        low = nm.lower()
        return any(t in low for t in toks)

    src = {}
    skipped = set()
    for c in source_cells or []:
        sc = str((c or {}).get('store_code') or '').strip()
        nm = str((c or {}).get('expense_name') or '').strip()
        if not sc or not nm:
            continue
        if sel is not None and nm.lower() not in sel:
            continue
        if excluded(nm):
            skipped.add(nm)
            continue
        try:
            amt = float(c.get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        src[(nm, sc)] = {'expense_type': (c.get('expense_type') or 'Fixed'), 'amount': amt}

    rows, affected = [], {}
    for p in target_periods or []:
        p = str(p or '').strip()
        if not p or p in affected:
            continue
        affected[p] = {}
        for (nm, sc), v in src.items():
            lst = affected[p].setdefault(nm, [])
            if sc not in lst:
                lst.append(sc)
            if v['amount'] != 0:
                rows.append({'period': p, 'store_code': sc, 'expense_name': nm,
                             'expense_type': v['expense_type'], 'amount': v['amount']})
    return rows, affected, sorted(skipped)


@router.get("/expenses/apply-config")
async def get_expense_apply_config(org_id: str = ORG_ID):
    """The expense-name tokens excluded from 'apply to other months' (commission/salary by default).
    `source` = 'config' when the org has saved its own set, else 'default' (the code fallback)."""
    require_org(org_id)
    client = sb()
    toks = _expense_apply_tokens(client, org_id)
    configured = False
    try:
        rows = (client.schema('commcalc').table('expense_apply_config')
                .select('token').eq('org_id', org_id).execute().data) or []
        configured = bool(rows)
    except Exception:
        configured = False
    return {"tokens": toks, "source": "config" if configured else "default",
            "default_tokens": list(_EXPENSE_APPLY_DEFAULT_TOKENS)}


@router.put("/expenses/apply-config")
async def put_expense_apply_config(body: dict, org_id: str = ORG_ID):
    """Replace the org's excluded-expense tokens (the admin-editable protected set). Body {tokens:[...]}.
    Case-insensitively deduped. Degrades gracefully (ok=false + hint) until mig 205 creates the table."""
    require_org(org_id)
    toks = []
    for t in (body.get('tokens') or []):
        t = str(t or '').strip()
        if t and t.lower() not in [x.lower() for x in toks]:
            toks.append(t)
    client = sb()
    try:
        client.schema('commcalc').table('expense_apply_config').delete().eq('org_id', org_id).execute()
        if toks:
            client.schema('commcalc').table('expense_apply_config').insert(
                [{'org_id': org_id, 'token': t} for t in toks]).execute()
        return {"ok": True, "tokens": toks}
    except Exception as e:
        return {"ok": False, "error": str(e), "tokens": toks,
                "hint": "run migration 205_commission_expense_apply_config.sql"}


@router.post("/expenses/apply-to-months")
async def apply_expenses_to_months(body: dict, org_id: str = ORG_ID):
    """Copy a SOURCE month's store expenses onto a chosen set of TARGET months — EXCEPT the configured
    protected expenses (commission + salary by default; see GET/PUT /expenses/apply-config). Body:
      { source_period: 'July 2026',
        target_periods: ['April 2026','May 2026','June 2026'],
        expense_names:  [..optional selection; omit/empty = all-except-excluded..],
        source_cells:   [..optional {store_code,expense_name,expense_type,amount} override so the page can
                          pass its LIVE grid for the current month; omit = read the SAVED source month..] }
    Idempotent: per (target period × affected (store,expense) cell) delete-then-insert — it touches ONLY
    those cells, NEVER wipes a whole target month. org-scoped on every read AND write, _pvariants for
    period-spelling. Does NOT recompute commissions or GP: writing expenses into a closed prior month
    SHIFTS that month's Gross Profit / P&L — re-run Calculation (or refresh the P&L) to reflect it."""
    require_org(org_id)
    source_period = str(body.get('source_period') or '').strip()
    if not source_period:
        return {"ok": False, "error": "source_period required"}
    targets = []
    for p in (body.get('target_periods') or []):
        p = str(p or '').strip()
        if p and p != source_period and p not in targets:   # never write back onto the source month
            targets.append(p)
    if not targets:
        return {"ok": False, "error": "no target_periods (after dropping the source month)"}
    client = sb()
    src_override = body.get('source_cells')
    if isinstance(src_override, list) and src_override:
        src_rows = src_override                     # LIVE grid passed by the page (WYSIWYG for the open month)
    else:
        _spv = _pvariants(source_period)
        try:   # read MANUAL source rows only — an auto system line (e.g. PTO accrual) is never copied forward
            src_rows = (client.schema('commcalc').table('store_expenses')
                        .select('store_code,expense_name,expense_type,amount')
                        .eq('org_id', org_id).in_('period', _spv).is_('source_key', 'null').execute().data) or []
        except Exception as e:
            if not _is_missing_col_err(e):
                raise
            src_rows = (client.schema('commcalc').table('store_expenses')
                        .select('store_code,expense_name,expense_type,amount')
                        .eq('org_id', org_id).in_('period', _spv).execute().data) or []
    excluded_tokens = _expense_apply_tokens(client, org_id)
    selection = body.get('expense_names')
    sel = selection if (isinstance(selection, list) and selection) else None
    rows, affected, skipped = _apply_to_months_expand(src_rows, targets, excluded_tokens, sel)
    # Idempotent per-cell delete-then-insert into each target period (never a whole-month wipe). Compound
    # (store,expense) IN isn't one PostgREST filter, so group by expense_name (one delete per name/period).
    for p, by_expense in affected.items():
        pv = _pvariants(p)
        for nm, scodes in by_expense.items():
            for i in range(0, len(scodes), 200):
                # manual-only delete → a cross-month apply can never clobber a target month's system line
                _delete_manual_expenses(client, org_id, pv,
                                        {'expense_name': nm, 'store_code': scodes[i:i + 200]})
    ins = [{'org_id': org_id, **row} for row in rows]
    for i in range(0, len(ins), 500):
        client.schema('commcalc').table('store_expenses').insert(ins[i:i + 500]).execute()
    return {"ok": True, "source_period": source_period, "target_periods": targets,
            "months": len(targets), "cells": len(ins), "saved": len(ins),
            "copied_expenses": sorted({r['expense_name'] for r in rows}),
            "skipped_excluded": skipped, "excluded_tokens": excluded_tokens}


@router.get("/commission-by-store/{period}")
async def commission_by_store(period: str, org_id: str = ORG_ID):
    """Σ rep_commissions.total_payout per STORE CODE for the period — feeds the Store Expenses
    'Employee Commission' auto-fill (the commission we PAY reps, booked as a store expense).
    rep_commissions.store is an address/label, so it's resolved to a store_code via store_mapping by
    street number (the same bridge gp_report uses). Returns commission_by_store {store_code: $} plus
    the total that couldn't be matched to a store."""
    require_org(org_id)
    import re as _re
    client = sb()
    comms = (client.schema('commcalc').table('rep_commissions')
             .select('store,total_payout').eq('org_id', org_id)
             .in_('period', _pvariants(period)).execute().data) or []
    sm = (client.schema('commcalc').table('store_mapping')
          .select('store_code,store_address').eq('org_id', org_id).execute().data) or []

    def _num(a):
        m = _re.match(r'\s*(\d+)', str(a or ''))
        return m.group(1) if m else ''

    # street-number -> store_code, and also allow rep_commissions.store to already BE a store_code
    code_by_num, codes = {}, set()
    for s in sm:
        code = str(s.get('store_code') or '').strip()
        if code:
            codes.add(code)
        n = _num(s.get('store_address'))
        if n and code:
            code_by_num.setdefault(n, code)

    out, unmatched = {}, 0.0
    for r in comms:
        pay = safe_float(r.get('total_payout'))
        st = str(r.get('store') or '').strip()
        code = st if st in codes else code_by_num.get(_num(st))
        if code:
            out[code] = round(out.get(code, 0.0) + pay, 2)
        else:
            unmatched = round(unmatched + pay, 2)
    return {"period": period, "commission_by_store": out,
            "stores": len(out), "unmatched_total": unmatched}


# ═══════════════════════════════════════════════════════════════════════════════
# GENERIC FTP-PULL SWEEP (Theme 6) — pull vendor files (B2B etc.) → route to upload parsers (mig 046)
# ═══════════════════════════════════════════════════════════════════════════════
from app.modules.commcalc import ftp_sweep as _ftp


def _ftp_cfg(client, org_id):
    rows = client.schema('commcalc').table('ftp_sweep_config').select('*').eq('org_id', org_id).limit(1).execute().data or []
    return rows[0] if rows else None


def _ftp_current_period():
    return _datetime.now().strftime("%B %Y")


async def _run_ftp_sweep(org_id):
    """Connect, download every NEW file matching a configured pattern, route each through the existing
    upload pipeline (signature-validated, guarded), and record what was processed."""
    from starlette.datastructures import UploadFile as _UF
    client = sb()
    cfg = _ftp_cfg(client, org_id)
    if not cfg or not (cfg.get('host') or '').strip():
        return {"ok": False, "error": "FTP not configured"}
    seen = client.schema('commcalc').table('ftp_processed').select('filename,file_size').eq('org_id', org_id).limit(100000).execute().data or []
    already = {(r['filename'], r.get('file_size') or 0) for r in seen}
    try:
        files = _ftp.fetch_new_files(cfg, already)
    except Exception as e:
        client.schema('commcalc').table('ftp_sweep_config').update(
            {'last_run_at': _datetime.now(_timezone.utc).isoformat(), 'last_status': f"connect error: {e}"}).eq('org_id', org_id).execute()
        return {"ok": False, "error": str(e)}
    results = []
    for f in files:
        name, size, ut = f['name'], f['size'], f.get('upload_type')
        if f.get('bytes') is None:   # download failed — don't record (retry next run)
            results.append({"file": name, "status": "download_failed", "detail": f.get('error')})
            continue
        period = "" if ut == "daily_sales" else _ftp_current_period()
        status, detail, rows_saved = "ok", None, 0
        try:
            uf = _UF(io.BytesIO(f['bytes']), filename=name)
            # org_id passed as a KEYWORD (was the 5th positional, which landed in `close_date` while org_id
            # silently defaulted to the house org — a latent multi-tenant misroute, inert today because
            # sweeps run as the house org; see PARKED note). trace_source tags the mig-202 upload_trace.
            res = await upload_file(ut, uf, period, force=False, org_id=org_id, trace_source='ftp_sweep')
            rows_saved = (res or {}).get('saved', 0)
        except HTTPException as he:
            status, detail = "error", str(he.detail)[:300]
        except Exception as e:
            status, detail = "error", str(e)[:300]
        try:
            client.schema('commcalc').table('ftp_processed').upsert(
                {'org_id': org_id, 'filename': name, 'file_size': size, 'upload_type': ut,
                 'rows_saved': rows_saved, 'status': status, 'detail': detail,
                 'processed_at': _datetime.now(_timezone.utc).isoformat()},
                on_conflict='org_id,filename,file_size').execute()
        except Exception:
            pass
        results.append({"file": name, "upload_type": ut, "status": status, "rows_saved": rows_saved, "detail": detail})
    ok = sum(1 for r in results if r['status'] == 'ok')
    client.schema('commcalc').table('ftp_sweep_config').update(
        {'last_run_at': _datetime.now(_timezone.utc).isoformat(),
         'last_status': f"{ok}/{len(results)} files ingested"}).eq('org_id', org_id).execute()
    return {"ok": True, "ingested": ok, "files": results}


@router.get("/ftp-sweep/config")
def get_ftp_config(org_id: str = ORG_ID):
    """Config WITHOUT the password (presence only)."""
    require_org(org_id)
    cfg = _ftp_cfg(sb(), org_id) or {"org_id": org_id, "patterns": [], "port": 21, "passive": True}
    cfg = dict(cfg)
    cfg['has_password'] = bool(cfg.pop('password', None))
    return cfg


@router.put("/ftp-sweep/config")
def put_ftp_config(body: dict, org_id: str = ORG_ID):
    """Save config. Password only updated when a non-empty value is supplied (so it isn't wiped)."""
    require_org(org_id)
    row = {"org_id": org_id, "host": (body.get("host") or "").strip() or None,
           "port": int(body.get("port") or 21), "username": (body.get("username") or "").strip() or None,
           "use_tls": bool(body.get("use_tls")), "passive": body.get("passive", True) is not False,
           "remote_dir": (body.get("remote_dir") or "/").strip(),
           "patterns": body.get("patterns") or [], "enabled": bool(body.get("enabled")),
           "frequency": body.get("frequency") or "daily", "hour": int(body.get("hour") or 7),
           "updated_at": _datetime.now(_timezone.utc).isoformat()}
    if (body.get("password") or "").strip():
        row["password"] = body["password"]
    if body.get("enabled"):
        row["next_run_at"] = _vip_next_run(row["frequency"], None, None, row["hour"], "America/New_York")
    sb().schema("commcalc").table("ftp_sweep_config").upsert(row, on_conflict="org_id").execute()
    return {"ok": True}


@router.post("/ftp-sweep/test")
def test_ftp(body: dict, org_id: str = ORG_ID):
    """List the remote directory (merging any unsaved overrides from the body) + which files match a
    pattern. Used by the 'Test connection' button before saving creds."""
    require_org(org_id)
    cfg = dict(_ftp_cfg(sb(), org_id) or {})
    for k in ("host", "port", "username", "password", "use_tls", "passive", "remote_dir", "patterns"):
        if k in body and body[k] not in (None, ""):
            cfg[k] = body[k]
    try:
        files = _ftp.list_files(cfg)
    except Exception as e:
        raise HTTPException(400, f"connection failed: {e}")
    pats = cfg.get("patterns") or []
    for f in files:
        f["matches"] = _ftp.match_upload_type(f["name"], pats)
    return {"files": files, "count": len(files)}


@router.post("/ftp-sweep/run-now")
async def ftp_run_now(org_id: str = ORG_ID):
    require_org(org_id)
    return await _run_ftp_sweep(org_id)


@router.get("/ftp-sweep/processed")
def ftp_processed(org_id: str = ORG_ID, limit: int = 100):
    require_org(org_id)
    return (sb().schema("commcalc").table("ftp_processed").select("*").eq("org_id", org_id)
            .order("processed_at", desc=True).limit(limit).execute().data) or []


@router.post("/ftp-sweep/run-due")
async def ftp_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint — run the FTP sweep if enabled + due, then advance next_run_at."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _datetime.now(_timezone.utc).isoformat()
    due = (client.schema('commcalc').table('ftp_sweep_config').select('*')
           .eq('enabled', True).lte('next_run_at', now_iso).execute().data) or []
    ran = []
    for cfg in due:
        oid = cfg.get('org_id') or ORG_ID
        res = await _run_ftp_sweep(oid)
        nxt = _vip_next_run(cfg.get('frequency') or 'daily', None, None, cfg.get('hour'), 'America/New_York')
        client.schema('commcalc').table('ftp_sweep_config').update({'next_run_at': nxt}).eq('org_id', oid).execute()
        ran.append({"org_id": oid, "result": res})
    return {"ran": len(ran), "detail": ran}


# ═══════════════════════════════════════════════════════════════════════════════
# GENERIC EMAIL (IMAP) SWEEP — sibling of the FTP sweep for vendors that EMAIL reports (mig 049)
# ═══════════════════════════════════════════════════════════════════════════════
from app.modules.commcalc import email_sweep as _email


# ═══════════════════════════════════════════════════════════════════════════════
# b2bsoft POS "standard profile" (mig 200) — one config-driven standard for any tenant whose POS is
# b2bsoft, so a new tenant's sales-ingest setup is IDENTICAL to house BY CONSTRUCTION (not hand-copied).
# The standard lives in commcalc.pos_profile (per-tenant, UI-editable — SAP-configurable rule); this code
# default mirrors the migration seed so the endpoints DEGRADE GRACEFULLY before mig 200 is applied.
# ═══════════════════════════════════════════════════════════════════════════════
_B2BSOFT_POS_DEFAULT = {
    "pos_key": "b2bsoft",
    "label": "B2B Soft (standard)",
    "imap_defaults": {"imap_port": 993, "use_ssl": True, "mailbox": "INBOX", "since_days": 14},
    "filename_rules": [
        {"pattern": "*Sales*Transaction*Details*", "upload_type": "daily_sales",
         "note": "daily B2B sales export — use the full-column \"for Metrics pro\" report (Ext Price + GP)"},
        {"pattern": "*Inventory*Aging*", "upload_type": "inventory_aging",
         "note": "b2bsoft inventory aging → Balance-Sheet inventory value"},
        {"pattern": "*X-Report*", "upload_type": "x_report",
         "note": "POS X-report tender summary → daily-closing cash/credit recon"},
    ],
    "schedule_defaults": {"frequency": "hourly", "hour": 7},
    "report_defs": [
        {"report_key": "sales", "label": "Sales Transactions",
         "source_name": "Sales Transaction Details (78-col)", "target_table": "raw_sales",
         "upload_endpoint": "commcalc/upload/sales", "period_mode": "current", "auto": True, "sort_order": 10},
        {"report_key": "inventory", "label": "Inventory Aging", "source_name": "Inventory Aging",
         "target_table": "inventory_value", "upload_endpoint": None, "period_mode": "snapshot",
         "auto": False, "sort_order": 20},
    ],
}


def _pos_profile(client, org_id, pos_key="b2bsoft"):
    """This tenant's editable POS standard profile, else the code default (so it works before mig 200).
    Org-scoped read — never reaches into another tenant's row."""
    try:
        rows = (client.schema("commcalc").table("pos_profile").select("*")
                .eq("org_id", org_id).eq("pos_key", pos_key).limit(1).execute().data) or []
        if rows:
            r = rows[0]
            # Fill any blank column from the code default so a partially-edited row still applies cleanly.
            for k in ("imap_defaults", "schedule_defaults"):
                if not r.get(k):
                    r[k] = _B2BSOFT_POS_DEFAULT[k]
            if not r.get("filename_rules"):
                r["filename_rules"] = _B2BSOFT_POS_DEFAULT["filename_rules"]
            if not r.get("report_defs"):
                r["report_defs"] = _B2BSOFT_POS_DEFAULT["report_defs"]
            return r
    except Exception:
        pass
    return {"org_id": org_id, **_B2BSOFT_POS_DEFAULT}


def _mailbox_cross_org(client, username, exclude_org):
    """Every OTHER tenant that has a mailbox configured at the same address `username`. This is the
    misfile detector: the Luxelink mailbox was filed under the HOUSE org and would have ingested Total
    sales into Boost. Case-insensitive; uses the service client (crosses orgs on purpose — a safety
    check), and returns only org_id/account/label/enabled (no creds), never another tenant's data."""
    u = (username or "").strip()
    if not u:
        return []
    try:
        rows = (client.schema("commcalc").table("email_sweep_config")
                .select("org_id,account,label,enabled,username").ilike("username", u).execute().data) or []
    except Exception:
        return []
    out = []
    for r in rows:
        if str(r.get("org_id") or "") == str(exclude_org or ""):
            continue
        if (r.get("username") or "").strip().lower() != u.lower():
            continue
        out.append({"org_id": r.get("org_id"), "account": r.get("account"),
                    "label": r.get("label"), "enabled": bool(r.get("enabled"))})
    return out


def _email_cfg(client, org_id, account='default'):
    """One mailbox config for (org, account). Tolerant of pre-075 schema (no 'account' column) so the
    single-mailbox setup keeps working before the migration is applied."""
    try:
        rows = (client.schema('commcalc').table('email_sweep_config').select('*')
                .eq('org_id', org_id).eq('account', account).limit(1).execute().data) or []
    except Exception:
        rows = (client.schema('commcalc').table('email_sweep_config').select('*')
                .eq('org_id', org_id).limit(1).execute().data) or []
    return rows[0] if rows else None


def _email_accounts(client, org_id):
    """Every mailbox configured for a tenant (multi-mailbox, mig 075). Pre-075 this returns the single
    row. Each row is a distinct inbox with its own creds + patterns + schedule."""
    try:
        rows = (client.schema('commcalc').table('email_sweep_config').select('*')
                .eq('org_id', org_id).order('account').execute().data) or []
    except Exception:
        rows = (client.schema('commcalc').table('email_sweep_config').select('*')
                .eq('org_id', org_id).limit(1).execute().data) or []
    return rows


def _email_status_update(client, org_id, account, upd):
    """Patch one mailbox's status columns, scoped to its account. Falls back to org-only pre-075
    (no 'account' column) so the single-mailbox setup still updates. Best-effort."""
    try:
        client.schema('commcalc').table('email_sweep_config').update(upd) \
            .eq('org_id', org_id).eq('account', account).execute()
    except Exception:
        try:
            client.schema('commcalc').table('email_sweep_config').update(upd).eq('org_id', org_id).execute()
        except Exception:
            pass


async def _run_email_sweep(org_id, account='default'):
    """Connect to ONE tenant mailbox (org, account), download every NEW attachment matching a configured
    pattern, route each through the existing upload pipeline, and record what was processed (dedup by
    account+message_id+name)."""
    from starlette.datastructures import UploadFile as _UF
    client = sb()
    cfg = _email_cfg(client, org_id, account)
    account = (cfg or {}).get('account') or account
    if not cfg or not (cfg.get('imap_host') or '').strip():
        return {"ok": False, "error": "Email/IMAP not configured", "account": account}
    # An empty rules list matches NOTHING — fail loudly instead of a silent "0/0 ingested" while
    # reports sit in the inbox (bit the Total/luxelink mailbox setup 2026-07-02).
    if not any((p.get('pattern') or '').strip() for p in (cfg.get('patterns') or []) if isinstance(p, dict)):
        _email_status_update(client, org_id, account,
            {'last_run_at': _datetime.now(_timezone.utc).isoformat(),
             'last_status': "no filename rules configured — add patterns, nothing can match"})
        return {"ok": False, "account": account,
                "error": "This mailbox has no filename rules — add a rule (e.g. *Sales*Transaction*Details* → daily sales) and Save."}
    seen = client.schema('commcalc').table('email_processed').select('message_id,filename,rows_saved,status').eq('org_id', org_id).limit(100000).execute().data or []
    # Skip a file ONLY if it ACTUALLY ingested (status ok + rows saved). A prior attempt that errored or
    # saved 0 rows is NOT treated as done, so it auto-retries on the next sweep — fixes "a file that failed
    # once is skipped forever (0 ingested)" without any manual email_processed cleanup.
    already = {(r.get('message_id'), r.get('filename')) for r in seen
               if r.get('status') == 'ok' and (r.get('rows_saved') or 0) > 0}
    try:
        files = _email.fetch_new_attachments(cfg, already)
    except Exception as e:
        _email_status_update(client, org_id, account,
            {'last_run_at': _datetime.now(_timezone.utc).isoformat(), 'last_status': f"connect error: {e}"})
        return {"ok": False, "error": str(e), "account": account}
    results = []
    shrinks = []   # row-count guardrail hits (a truncated/partial export) → alert after the loop
    for f in files:
        name, size, ut, mid = f['name'], f['size'], f.get('upload_type'), f.get('message_id')
        period = "" if ut in ("daily_sales", "ma_commission", "ma_daily_tx", "ma_fulfillment") else _ftp_current_period()
        status, detail, rows_saved, shrink, skipped_flag = "ok", None, 0, [], None
        try:
            uf = _UF(io.BytesIO(f['bytes']), filename=name)
            # org_id passed as a KEYWORD (was the 5th positional, which landed in `close_date` while org_id
            # silently defaulted to the house org — a latent multi-tenant misroute that would send a tenant
            # mailbox's swept sales into Boost the moment that mailbox is filed under its own org; inert
            # today because sweeps run as the house org). trace_source tags the mig-202 upload_trace.
            res = await upload_file(ut, uf, period, force=False, org_id=org_id, trace_source='email_sweep')
            rows_saved = (res or {}).get('saved', 0)
            shrink = (res or {}).get('shrink') or []
            skipped_flag = (res or {}).get('skipped')
            # The PRICE-COVERAGE GUARD refuses a degraded/price-less re-delivery with
            # {saved:0, skipped:'price_guard', shrink:[{reason}]} (HTTP 200, not an error). Record it as
            # a distinct 'skipped' status carrying the guard's reason so the history shows an honest amber
            # "refused to protect existing data" instead of a green "✓ 0 rows" (indistinguishable from a
            # broken upload — the luxelink incident 2026-07-14). NO behavior change: rows_saved is still 0
            # so the dedup at the top of this sweep keeps auto-retrying, the price-guard `shrink` entry
            # still rides the row-count alert path below, and the money-writing auto-promote/recalc trigger
            # further down treats 'skipped' the same as it treated this row before (it was 'ok' + 0 rows).
            if (res or {}).get('skipped') == 'price_guard':
                status = 'skipped'
                detail = ((shrink[0].get('reason') if shrink else None)
                          or 'refused: fuller priced data already stored for that day (price guard)')[:300]
            elif (res or {}).get('skipped') == 'price_guard_partial':
                # PARTIAL price-guard: the file's fresh day(s) DID ingest (rows_saved > 0) while degraded
                # day(s) were kept as stored. It's a real ingest with a warning, NOT a full refusal — leave
                # status='ok' so the sweep dedup treats the file as done (its useful data is in) and so the
                # money-writing auto-promote/recalc below runs on the fresh days. Carry the guard reason in
                # `detail` so the history row can flag it amber, and the shrink entry still rides the
                # partial-export alert path below. guarded_dates is available on `res` for callers that want it.
                _gd = (res or {}).get('guarded_dates') or []
                detail = ((shrink[0].get('reason') if shrink else None)
                          or f"ingested fresh day(s); kept existing data for {', '.join(map(str, _gd))} (price guard)")[:300]
            elif (res or {}).get('skipped') == 'inventory_no_stores':
                # HONEST-ZERO for Inventory Aging: the attachment WAS read but produced 0 per-store values
                # (renamed/unknown store or value column, or a layout we don't yet flatten). This used to be
                # recorded as status='ok' + 0 rows — a green ✓ on a file that ingested nothing (luxelink,
                # 2026-07-14). Record a distinct 'skipped' carrying the parser's honest reason (expected
                # columns vs the columns actually found) so the history shows WHAT is wrong. rows_saved stays
                # 0, so the sweep dedup keeps auto-retrying → it self-heals the moment a synonym/flatten
                # matches or the source report is corrected. Not a money path (no daily_sales promote/recalc).
                status = 'skipped'
                detail = (str((res or {}).get('note') or 'Inventory Aging parsed 0 stores'))[:300]
        except HTTPException as he:
            status, detail = "error", str(he.detail)[:300]
        except Exception as e:
            status, detail = "error", str(e)[:300]
        try:
            client.schema('commcalc').table('email_processed').upsert(
                {'org_id': org_id, 'account': account, 'message_id': mid, 'filename': name, 'file_size': size,
                 'upload_type': ut, 'rows_saved': rows_saved, 'status': status, 'detail': detail,
                 'processed_at': _datetime.now(_timezone.utc).isoformat()},
                on_conflict='org_id,account,message_id,filename').execute()
        except Exception:
            pass
        for s in shrink:
            shrinks.append({'file': name, 'upload_type': ut, **s})
        results.append({"file": name, "upload_type": ut, "status": status, "rows_saved": rows_saved,
                        "detail": detail, "shrink": shrink, "skipped": skipped_flag})
    ok = sum(1 for r in results if r['status'] == 'ok')
    # A truncated/partial emailed export (far fewer rows than the day/period it replaced) would silently
    # corrupt reports — alert the connector recipients (same scope as connector-health) so it's caught.
    if shrinks:
        try:
            from app.modules.closing.router import _send_alert  # lazy: avoids a commcalc<->closing cycle
            _today = _datetime.now(_timezone.utc).date()
            for s in shrinks:
                subject = f"⚠️ Partial data export: {s['upload_type']} dropped to {s['new']} rows for {s['key']}"
                text = (f"MetricsPro — an emailed {s['upload_type']} file ingested FAR fewer rows than the data "
                        f"it replaced for {s['key']}: {s['new']} rows vs {s['prior']} previously.\n\n"
                        f"File: {s['file']}\nMailbox: {account}\n\nThis is the signature of a truncated or "
                        f"partial export. Verify the source report is complete before trusting {s['upload_type']} "
                        f"numbers for {s['key']}; re-sending the full report self-heals (the ingest replaces it).")
                ref = f"datadrop:{org_id}:{s['upload_type']}:{s['key']}:{_today}"
                await _send_alert(client, org_id, "connector", subject, text, ref)
        except Exception as e:
            print(f"WARN row-count guardrail alert failed: {e}")
    errs = [r for r in results if r['status'] == 'error']
    skipped = [r for r in results if r['status'] == 'skipped']
    status_msg = (f"{ok}/{len(results)} attachments ingested" if results
                  else "no new attachments to import — matched files already imported OK, or none match your rules (use Test connection)")
    if errs:
        status_msg += " · errors: " + "; ".join(f"{e['file']}: {e['detail']}" for e in errs[:2])[:240]
    if skipped:
        status_msg += f" ⚠️ {len(skipped)} refused by price guard (kept existing data)"
    if shrinks:
        status_msg += f" ⚠️ {len(shrinks)} partial-export drop(s)"
    _email_status_update(client, org_id, account,
        {'last_run_at': _datetime.now(_timezone.utc).isoformat(), 'last_status': status_msg})
    # Auto-derive the monthly commission basis (raw_sales) from the feed — best-effort + guarded,
    # never breaks the sweep. DEFAULT ON when the registry has no 'sales' row: new tenants never had
    # the row the house has, so their raw_sales silently stayed empty and plan-mode pay was $0
    # (luxelink, 2026-07-14). An explicit auto=false row still opts a tenant out.
    try:
        # 'skipped' is treated the same as 'ok' here ONLY to keep this money-writing trigger byte-identical
        # to before the honest-history change above: a price-guard skip used to be recorded as status='ok'
        # (with 0 rows) and thus reached this promote/recalc exactly as it does now. The promote reads the
        # unchanged (guard-protected) feed, so it is a no-op on the same data either way.
        if (any(r['upload_type'] == 'daily_sales' and r['status'] in ('ok', 'skipped') for r in results)
                and _registry_auto_map(client, org_id).get('sales', True)):
            _pr = _promote_feed_to_raw_sales(client, org_id, _ftp_current_period())
            # Plan-mode tenants have no other automatic recompute (the DLAR auto-recalc is Boost-only),
            # so a promotion that actually wrote rows recalculates the period — sales flow to pay every
            # sweep with nobody pressing Run Calculation. Best-effort; the zero-wipe guard protects the
            # snapshot, and Boost orgs are excluded (their recompute cadence stays the daily DLAR sweep).
            try:
                if (_pr or {}).get('written'):
                    _carriers = (client.schema('commcalc').table('carrier').select('*')
                                 .eq('org_id', org_id).execute().data) or []
                    if _resolve_carrier_mode(_carriers) != 'boost':
                        await _run_calculation(_ftp_current_period(), org_id)
            except Exception as e2:
                print(f"WARN auto-recalc after promote failed: {e2}")
    except Exception as e:
        print(f"WARN auto-promote feed->raw_sales failed: {e}")
    return {"ok": True, "account": account, "ingested": ok, "files": results}


async def _run_email_sweep_all(org_id):
    """Run EVERY configured mailbox for a tenant (used by run-now with no account). Returns a per-account
    roll-up. Runs each account even if others fail."""
    accounts = [a.get('account') or 'default' for a in _email_accounts(sb(), org_id)] or ['default']
    out = []
    for acct in accounts:
        try:
            out.append(await _run_email_sweep(org_id, acct))
        except Exception as e:
            out.append({"ok": False, "account": acct, "error": str(e)})
    return {"ok": True, "accounts": len(out), "runs": out,
            "ingested": sum((r.get('ingested') or 0) for r in out)}


def _promote_feed_to_raw_sales(client, org_id, period, dry_run=False, force=False, retain=0.85):
    """Derive the authoritative monthly raw_sales for `period` from the accumulated daily B2B email
    feed (daily_sales_feed), so the monthly Sales file no longer has to be uploaded by hand.

    MERGE, not blind replace: the rebuilt raw_sales = every feed transaction for the month PLUS any
    transaction that exists in the current raw_sales but NOT in the feed — so a transaction is NEVER
    dropped (the feed can lag the monthly file by a handful of transactions during the transition).
    daily_sales_feed and raw_sales share an identical column shape (same parser), so feed rows copy
    straight across; the period label is normalized to the monthly 'Month YYYY' form.

    Guarded: if the merged result would shrink the existing raw_sales line count below `retain` of its
    current size, the write is SKIPPED (a half-delivered feed can't wipe a good month) unless force.
    dry_run returns the would-be delta WITHOUT writing — the safe way to validate before committing.

    ANTI-DUPLICATION (2026-07-16, luxelink July 2026 incident — feed-less days compounding ~2x→16x per
    run): (1) the paginated `_all()` read now `.order('id')` so unordered OFFSET pages can't re-read rows;
    (2) the carried-over `monthly_only` rows (existing raw_sales rows the feed lacks — the feed-less days)
    are content-deduped before persisting (`summary['dupes_dropped']`), which also SELF-HEALS pre-existing
    bloat on the next run — NOTE this collapses two genuinely-identical line items on one real ticket to
    one (accepted; the authoritative monthly re-upload restores exact per-line truth); (3) an in-process
    per-(org_id, period) mutex serializes the hourly email-sweep promotion against the scheduled
    _promote_all_due — a second concurrent run for the same org+period SKIPS ('promotion already running')
    rather than interleaving a delete/insert into a double-count. dry_run is read-only → not mutex-gated."""
    pv = _pvariants(period)
    canon = next((v for v in pv if v[:1].isalpha()), period)  # 'June 2026' form for raw_sales
    # DEFECT 3 mutex — real (writing) runs only; dry_run is read-only so it must never block/skip a real
    # run. Non-blocking acquire: a concurrent real run for the same org+period skips with a trace note.
    _lock = None if dry_run else _promo_lock_for(org_id, canon)
    if _lock is not None and not _lock.acquire(blocking=False):
        note = "promotion already running for this org+period — skipped (concurrent run)"
        _write_upload_trace(org_id, source="promotion", filename=None, upload_type="sales",
                            period=canon, result={"saved": 0, "skipped": note, "note": note})
        return {"period": canon, "dry_run": dry_run, "skipped": note}
    try:
        return _promote_feed_impl(client, org_id, pv, canon, dry_run, force, retain)
    finally:
        if _lock is not None:
            _lock.release()


def _promote_feed_impl(client, org_id, pv, canon, dry_run, force, retain):
    """Core feed→raw_sales merge, mutex-guarded by `_promote_feed_to_raw_sales` (which precomputes
    pv/canon). See that wrapper's docstring for the full contract."""

    def _all(table):
        out, start = [], 0
        while True:
            rows = (client.schema('commcalc').table(table).select('*')
                    .eq('org_id', org_id).in_('period', pv)
                    .order('id').range(start, start + 999).execute().data) or []
            out.extend(rows)
            if len(rows) < 1000:
                return out
            start += 1000

    feed = _all('daily_sales_feed')
    existing = _all('raw_sales')
    feed_trans = {r.get('trans_id') for r in feed if r.get('trans_id')}
    raw_cols = set(existing[0].keys()) if existing else None
    DROP = {'id', 'created_at'}
    if raw_cols is None and feed:
        # First promotion for this org: raw_sales has no row to learn its columns from, and the feed
        # carries feed-only columns raw_sales lacks — inserting them 500s the whole promotion (hit by
        # luxelink 2026-07-14; the house org never hit it because its raw_sales was never empty).
        # Probe each feed column against raw_sales once; unknown columns are dropped. org_id/period
        # are re-stamped explicitly below, so they survive even if a probe hiccups.
        raw_cols = set()
        for c in feed[0].keys():
            if c in DROP:
                continue
            try:
                client.schema('commcalc').table('raw_sales').select(c).limit(1).execute()
                raw_cols.add(c)
            except Exception:
                continue

    new_rows = []
    for r in feed:
        row = {k: v for k, v in r.items() if k not in DROP and (raw_cols is None or k in raw_cols)}
        row['org_id'] = org_id
        row['period'] = canon
        new_rows.append(row)
    # DEFECT 2 — the feed-less-day rows (in raw_sales but NOT the feed) are carried over VERBATIM, so any
    # read-skew / previously-persisted duplicate compounds run-over-run (the feed-covered days don't,
    # because they're rebuilt from the feed each run). Content-dedupe them (signature drops id + created_at)
    # keeping the FIRST occurrence — this also self-heals existing bloat on the next run.
    monthly_only = [r for r in existing if r.get('trans_id') not in feed_trans]
    monthly_only, dupes_dropped = _dedupe_rows(monthly_only, drop_keys=('id', 'created_at'))
    for r in monthly_only:
        new_rows.append({k: v for k, v in r.items() if k != 'id'})

    def _amt(rows):
        return round(sum((safe_float(x.get('ext_price')) or 0) for x in rows), 2)
    summary = {
        "period": canon, "dry_run": dry_run,
        "feed_lines": len(feed), "feed_trans": len(feed_trans),
        "existing_lines": len(existing), "existing_trans": len({r.get('trans_id') for r in existing}),
        "monthly_only_trans": len({r.get('trans_id') for r in monthly_only}),
        "dupes_dropped": dupes_dropped,
        "result_lines": len(new_rows), "result_trans": len({r.get('trans_id') for r in new_rows}),
        "existing_amount": _amt(existing), "result_amount": _amt(new_rows),
    }
    def _trace_promo(saved, skipped=None, error=None, note=None):
        # mig 202: trace the promotion (feed→raw_sales) like any other ingest. dry_run previews aren't
        # traced (no write). Best-effort — never affects the promotion. `note` surfaces the dedupe heal
        # (dupes_dropped) so the self-healing is observable in the trace even on a clean success.
        if dry_run:
            return
        _write_upload_trace(org_id, source="promotion", filename=None, upload_type="sales",
                            period=canon,
                            result={"saved": saved, "skipped": skipped, "note": note or skipped,
                                    "_trace": {"rows_in": summary.get("feed_lines"),
                                               "target_table": "raw_sales",
                                               "periods": {canon: summary.get("result_lines", 0)},
                                               "date_counts": {}}},
                            error=error)

    if not new_rows:
        summary["skipped"] = "no feed or monthly rows for this period"
        _trace_promo(0, skipped=summary["skipped"])
        return summary
    if existing and not force and len(new_rows) < retain * len(existing):
        summary["skipped"] = (f"guard: result {len(new_rows)} lines < {int(retain * 100)}% of existing "
                              f"{len(existing)} — feed looks incomplete (use force to override)")
        _trace_promo(0, skipped=summary["skipped"])
        return summary
    if dry_run:
        return summary

    try:
        client.schema('commcalc').table('raw_sales').delete().eq('org_id', org_id).in_('period', pv).execute()
        for i in range(0, len(new_rows), 500):
            client.schema('commcalc').table('raw_sales').insert(new_rows[i:i + 500]).execute()
    except Exception as e:
        _trace_promo(0, error=str(e))
        raise
    summary["written"] = len(new_rows)
    _heal_note = (f"healed {dupes_dropped} duplicate monthly-only line(s) via content-dedupe"
                  if dupes_dropped else None)
    _trace_promo(len(new_rows), note=_heal_note)
    return summary


@router.post("/sales/promote-feed")
def promote_feed(period: str, org_id: str = ORG_ID, dry_run: bool = True, force: bool = False):
    """Build the monthly commission basis (raw_sales) for a period from the daily B2B email feed.
    dry_run=true (default) PREVIEWS the delta without writing — pass dry_run=false to commit, then
    recompute the period. Idempotent + guarded; merges so no transaction is ever dropped."""
    require_org(org_id)
    return _promote_feed_to_raw_sales(sb(), org_id, period, dry_run=dry_run, force=force)


def _promote_all_due(client, period=None):
    """ORG-AGNOSTIC, self-healing feed→raw_sales promotion. For the OPEN month (default) it reconciles
    EVERY tenant that has daily_sales_feed rows — so a tenant whose email sweep hasn't ingested a NEW
    attachment recently still gets its raw_sales backfilled from the accumulated feed. This is the
    org-level fix for "the daily feed is ingesting but the Daily Sales reports show nothing": the promotion
    was previously invoked ONLY as a side-effect of the email sweep processing a fresh attachment
    (_run_email_sweep ~line 9126); when no new file arrives, the ~10 raw_sales-only display consumers
    (gp_report / sales_analyzer / top-sellers / discrepancy phantom / accessory-flags / fraud scan + the
    finance P&L and retail closing _b2b_day) show empty for a tenant with a healthy feed (luxelink July 2026).

    MONEY-SAFE by construction: promotion MERGES + dedups by trans_id (a trans_id present in BOTH tables is
    counted ONCE — feed wins, monthly-only kept once) and is guarded by the existing retain guard; it runs
    on the OPEN month only, which the commission calculator (_fetch_sales_unified) reads from the FEED
    regardless — so Boost/house pay is byte-identical and no pay is recomputed here (the sweep path + manual
    /calculate own recompute). Per-org opt-out via report_definitions.auto for report_key='sales' (the same
    gate the in-sweep promote uses)."""
    period = period or _ftp_current_period()
    pv = _pvariants(period)
    # 1) every tenant with feed rows for this period — fast RPC (mig 204), else a bounded distinct scan so
    #    the job still self-heals before the migration runs.
    orgs = []
    try:
        rows = client.schema('commcalc').rpc('sales_feed_orgs_for_period', {'p_periods': pv}).execute().data or []
        orgs = [r['org_id'] for r in rows if r.get('org_id')]
    except Exception:
        try:
            seen, start = set(), 0
            while True:
                batch = (client.schema('commcalc').table('daily_sales_feed').select('org_id')
                         .in_('period', pv).range(start, start + 4999).execute().data) or []
                for r in batch:
                    if r.get('org_id'):
                        seen.add(r['org_id'])
                if len(batch) < 5000:
                    break
                start += 5000
            orgs = list(seen)
        except Exception as e:
            return {"ok": False, "error": f"could not enumerate feed orgs: {e}", "period": period}
    out = []
    for oid in orgs:
        try:
            if not _registry_auto_map(client, oid).get('sales', True):
                out.append({"org_id": oid, "skipped": "sales auto=false"})
                continue
            pr = _promote_feed_to_raw_sales(client, oid, period)
            out.append({"org_id": oid, "written": (pr or {}).get('written', 0),
                        "result_lines": (pr or {}).get('result_lines'),
                        "skipped": (pr or {}).get('skipped')})
        except Exception as e:
            out.append({"org_id": oid, "error": str(e)[:200]})
    return {"ok": True, "period": period, "orgs": len(orgs),
            "written_orgs": sum(1 for r in out if r.get('written')), "detail": out}


@router.post("/sales/promote-due")
def sales_promote_due(x_notify_secret: str = Header(default=""), period: str = None):
    """pg_cron entrypoint — org-agnostic, self-healing feed→raw_sales promotion for the OPEN month across
    EVERY tenant (see _promote_all_due). Reuses NOTIFY_RUN_SECRET (no new env var). DISPLAY-safe: OPEN
    month only, promotion dedups by trans_id, no pay recompute — a run can never change Boost numbers or
    double-count. Schedule hourly (offset from the email-sweep cron) so raw_sales never lags the feed for
    any tenant."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    return _promote_all_due(sb(), period)


def _strip_pw(cfg):
    cfg = dict(cfg)
    cfg['has_password'] = bool(cfg.pop('password', None))
    return cfg


@router.get("/email-sweep/accounts")
def list_email_accounts(org_id: str = ORG_ID):
    """Every mailbox configured for this tenant (passwords stripped). Multi-mailbox = one row per
    report source (e.g. B2B feed + Total Wireless). [] if none configured yet."""
    require_org(org_id)
    return {"accounts": [_strip_pw(a) for a in _email_accounts(sb(), org_id)]}


@router.get("/email-sweep/config")
def get_email_config(org_id: str = ORG_ID, account: str = "default"):
    """One mailbox's config WITHOUT the password (presence only)."""
    require_org(org_id)
    cfg = _email_cfg(sb(), org_id, account) or {"org_id": org_id, "account": account, "patterns": [],
                                                "imap_port": 993, "use_ssl": True, "mailbox": "INBOX"}
    return _strip_pw(cfg)


@router.put("/email-sweep/config")
def put_email_config(body: dict, org_id: str = ORG_ID):
    """Save one mailbox. `account` keys which mailbox (default 'default'); pass a distinct key + label to
    add another (e.g. account='total', label='Total Wireless'). Password only updated when supplied."""
    require_org(org_id)
    account = (body.get("account") or "default").strip() or "default"
    row = {"org_id": org_id, "account": account, "label": (body.get("label") or "").strip() or None,
           "imap_host": (body.get("imap_host") or "").strip() or None,
           "imap_port": int(body.get("imap_port") or 993), "username": (body.get("username") or "").strip() or None,
           "use_ssl": body.get("use_ssl", True) is not False, "mailbox": (body.get("mailbox") or "INBOX").strip(),
           "from_filter": (body.get("from_filter") or "").strip() or None,
           "since_days": int(body.get("since_days") or 14),
           "patterns": body.get("patterns") or [], "enabled": bool(body.get("enabled")),
           "frequency": body.get("frequency") or "daily", "hour": int(body.get("hour") or 7),
           "updated_at": _datetime.now(_timezone.utc).isoformat()}
    if (body.get("password") or "").strip():
        row["password"] = body["password"]
    if body.get("enabled"):
        row["next_run_at"] = _vip_next_run(row["frequency"], None, None, row["hour"], "America/New_York")
    # MISFILE GUARD (the cross-org class has bitten twice — the Luxelink mailbox filed under the HOUSE
    # org would ingest Total sales into Boost, and the same physical inbox enabled under two orgs makes
    # BOTH tenants sweep it → double-ingest). If this address is already ENABLED under a DIFFERENT tenant,
    # refuse to PERSIST an enabled save until the caller explicitly acknowledges. A disabled save (or a
    # save with acknowledge_cross_org) goes through but still carries the warning so the UI can surface it.
    conflicts = _mailbox_cross_org(sb(), row.get("username"), org_id)
    enabled_conflicts = [c for c in conflicts if c.get("enabled")]
    if row.get("enabled") and enabled_conflicts and not body.get("acknowledge_cross_org"):
        return {"ok": False, "account": account, "warning": "cross_org_mailbox",
                "message": (f"The mailbox '{row.get('username')}' is already configured and ENABLED under "
                            f"another tenant. Enabling it here too would make BOTH tenants ingest the same "
                            f"emails — a cross-tenant misfile. If this inbox truly belongs to THIS tenant, "
                            f"disable/delete it on the other tenant first; otherwise re-save to acknowledge."),
                "conflicts": enabled_conflicts}
    try:
        sb().schema("commcalc").table("email_sweep_config").upsert(row, on_conflict="org_id,account").execute()
    except Exception:
        # pre-075 fallback: no 'account' column yet → save the single mailbox on org_id
        row.pop("account", None); row.pop("label", None)
        sb().schema("commcalc").table("email_sweep_config").upsert(row, on_conflict="org_id").execute()
    resp = {"ok": True, "account": account}
    if conflicts:
        resp["warning"] = "cross_org_mailbox"
        resp["conflicts"] = conflicts
    return resp


@router.delete("/email-sweep/account/{account}")
def delete_email_account(account: str, org_id: str = ORG_ID):
    """Remove a mailbox and its processed history. Won't delete the last 'default' row silently — any
    named account is fair game."""
    require_org(org_id)
    client = sb()
    try:
        client.schema("commcalc").table("email_processed").delete().eq("org_id", org_id).eq("account", account).execute()
    except Exception:
        pass
    client.schema("commcalc").table("email_sweep_config").delete().eq("org_id", org_id).eq("account", account).execute()
    return {"deleted": account}


@router.post("/email-sweep/test")
def test_email(body: dict, org_id: str = ORG_ID):
    """Connect to the mailbox (merging any unsaved overrides) and list recent messages + their
    attachments and which match a pattern. Used by the 'Test connection' button before saving creds."""
    require_org(org_id)
    cfg = dict(_email_cfg(sb(), org_id, (body.get("account") or "default").strip() or "default") or {})
    for k in ("imap_host", "imap_port", "username", "password", "use_ssl", "mailbox", "from_filter", "since_days", "patterns"):
        if k in body and body[k] not in (None, ""):
            cfg[k] = body[k]
    try:
        msgs = _email.list_messages(cfg)
    except Exception as e:
        em = str(e)
        hint = ""
        if any(s in em.upper() for s in ("AUTHENTICATIONFAILED", "INVALID CREDENTIALS", "LOGIN FAILED", "AUTH")):
            hint = (" — AUTH FAILED. Check: (1) use a 16-char App Password, not the normal password "
                    "(Gmail: 2-Step Verification must be ON to create one); (2) the Username is the FULL email "
                    "address; (3) Gmail IMAP is always on now — there is NO toggle to enable, so that's fine; "
                    "(4) Google Workspace / Microsoft 365 accounts may have IMAP restricted by the admin or "
                    "require OAuth (a normal/App password won't work there).")
        raise HTTPException(400, f"connection failed: {em}{hint}")
    matched = sum(1 for m in msgs for a in m.get("attachments", []) if a.get("matches"))
    return {"messages": msgs, "count": len(msgs), "matched_attachments": matched}


@router.post("/email-sweep/run-now")
async def email_run_now(org_id: str = ORG_ID, account: str = ""):
    """Run one mailbox (?account=total) or ALL of the tenant's mailboxes (omit account)."""
    require_org(org_id)
    if account.strip():
        return await _run_email_sweep(org_id, account.strip())
    return await _run_email_sweep_all(org_id)


@router.get("/email-sweep/processed")
def email_processed(org_id: str = ORG_ID, limit: int = 100):
    require_org(org_id)
    return (sb().schema("commcalc").table("email_processed").select("*").eq("org_id", org_id)
            .order("processed_at", desc=True).limit(limit).execute().data) or []


# ── b2bsoft POS standard-profile endpoints (mig 200) ──────────────────────────────────────────────
@router.get("/pos-profiles")
def list_pos_profiles(org_id: str = ORG_ID, pos_key: str = "b2bsoft"):
    """This tenant's editable POS standard profile (falls back to the code default before mig 200 runs,
    so the page never breaks). Drives the 'Apply … standard' button + the editable template."""
    require_org(org_id)
    return {"profile": _pos_profile(sb(), org_id, pos_key)}


@router.put("/pos-profiles")
def put_pos_profile(body: dict, org_id: str = ORG_ID):
    """Edit this tenant's POS standard profile (SAP-configurable — the standard is a config row, not code).
    Degrades gracefully: if mig 200 isn't applied yet, returns ok=False with a hint instead of 500."""
    require_org(org_id)
    pos_key = (body.get("pos_key") or "b2bsoft").strip() or "b2bsoft"
    row = {"org_id": org_id, "pos_key": pos_key,
           "label": (body.get("label") or "").strip() or None,
           "imap_defaults": body.get("imap_defaults") or {},
           "filename_rules": body.get("filename_rules") or [],
           "schedule_defaults": body.get("schedule_defaults") or {},
           "report_defs": body.get("report_defs") or [],
           "is_active": body.get("is_active", True) is not False,
           "updated_at": _datetime.now(_timezone.utc).isoformat()}
    try:
        sb().schema("commcalc").table("pos_profile").upsert(row, on_conflict="org_id,pos_key").execute()
    except Exception as e:
        return {"ok": False, "error": f"Could not save profile (run migration 200): {e}"}
    return {"ok": True, "pos_key": pos_key}


@router.post("/pos-profiles/{pos_key}/apply")
def apply_pos_profile(pos_key: str, org_id: str = ORG_ID, account: str = "default"):
    """One-click: make this tenant's mailbox + report registry match the POS standard, BY CONSTRUCTION.

    Strictly ADDITIVE / merge (never money-touching): it (1) creates or updates the (org, account) mailbox
    row — filling BLANK imap/schedule defaults + label, and adding any STANDARD filename rule that isn't
    already present — but NEVER clobbers a saved password, host, username, enabled flag, or an existing
    rule; and (2) seeds the standard report_definitions (ON CONFLICT DO NOTHING, so a tenant's existing
    auto toggle is untouched) under a b2bsoft connector. The tenant still enters host + credentials and
    flips Enabled — this just guarantees the RULES/SCHEDULE are the house standard, not hand-typed."""
    require_org(org_id)
    account = (account or "default").strip() or "default"
    client = sb()
    prof = _pos_profile(client, org_id, pos_key)
    imapd = prof.get("imap_defaults") or {}
    sched = prof.get("schedule_defaults") or {}
    std_rules = prof.get("filename_rules") or []

    existing = _email_cfg(client, org_id, account) or {}
    created = not existing
    # Union the standard rules onto whatever's there, keyed by (lowercased pattern) so a re-apply is a no-op.
    cur_rules = list(existing.get("patterns") or [])
    have = {str(p.get("pattern") or "").strip().lower() for p in cur_rules if isinstance(p, dict)}
    added = 0
    for r in std_rules:
        pat = str(r.get("pattern") or "").strip().lower()
        if pat and pat not in have:
            cur_rules.append(dict(r)); have.add(pat); added += 1

    row = {"org_id": org_id, "account": account,
           "label": existing.get("label") or prof.get("label"),
           "imap_host": existing.get("imap_host"),          # tenant-supplied — never overwritten
           "imap_port": existing.get("imap_port") or int(imapd.get("imap_port") or 993),
           "username": existing.get("username"),
           "use_ssl": existing.get("use_ssl") if "use_ssl" in existing else (imapd.get("use_ssl", True) is not False),
           "mailbox": existing.get("mailbox") or imapd.get("mailbox") or "INBOX",
           "from_filter": existing.get("from_filter"),
           "since_days": existing.get("since_days") or int(imapd.get("since_days") or 14),
           "patterns": cur_rules,
           "enabled": bool(existing.get("enabled")),        # never auto-enable a mailbox with no creds
           "frequency": existing.get("frequency") or sched.get("frequency") or "hourly",
           "hour": existing.get("hour") if existing.get("hour") is not None else int(sched.get("hour") or 7),
           "updated_at": _datetime.now(_timezone.utc).isoformat()}
    try:
        client.schema("commcalc").table("email_sweep_config").upsert(row, on_conflict="org_id,account").execute()
    except Exception:
        row.pop("account", None); row.pop("label", None)
        client.schema("commcalc").table("email_sweep_config").upsert(row, on_conflict="org_id").execute()

    # Seed the report registry so the Data Imports / Connectors page shows the same reports house has.
    # Best-effort + non-destructive: ensure a b2bsoft connector, then INSERT each report_def ON CONFLICT
    # DO NOTHING (an existing report_definitions.auto toggle — a money-adjacent setting — is left as-is).
    reports_seeded = 0
    try:
        conn_id = None
        try:
            crow = {"org_id": org_id, "vendor_name": "B2B Soft", "label": "B2B Soft wsreports",
                    "sweep_kind": "b2b", "portal_url": "https://wsreports.b2bsoft.com",
                    "config_table": "b2b_sweep_config", "updated_at": _datetime.now(_timezone.utc).isoformat()}
            client.schema("commcalc").table("connector_instances").upsert(
                crow, on_conflict="org_id,vendor_name").execute()
            got = (client.schema("commcalc").table("connector_instances").select("id")
                   .eq("org_id", org_id).eq("vendor_name", "B2B Soft").limit(1).execute().data) or []
            conn_id = got[0]["id"] if got else None
        except Exception:
            conn_id = None
        have_defs = {r.get("report_key") for r in
                     ((client.schema("commcalc").table("report_definitions").select("report_key")
                       .eq("org_id", org_id).execute().data) or [])}
        for rd in (prof.get("report_defs") or []):
            rk = rd.get("report_key")
            if not rk or rk in have_defs:
                continue
            client.schema("commcalc").table("report_definitions").insert({
                "org_id": org_id, "connector_id": conn_id, "report_key": rk,
                "label": rd.get("label"), "source_name": rd.get("source_name"),
                "period_mode": rd.get("period_mode") or "current", "target_table": rd.get("target_table"),
                "upload_endpoint": rd.get("upload_endpoint"), "source_url": "https://wsreports.b2bsoft.com",
                "auto": bool(rd.get("auto")), "sort_order": rd.get("sort_order") or 100}).execute()
            reports_seeded += 1
    except Exception as e:
        print(f"WARN apply_pos_profile report_definitions seed skipped: {e}")

    return {"ok": True, "account": account, "created": created, "rules_added": added,
            "rules_total": len(cur_rules), "reports_seeded": reports_seeded,
            "needs_credentials": not (row.get("imap_host") and existing.get("username")),
            "pos_key": pos_key}


@router.get("/email-sweep/ingest-health")
def email_ingest_health(org_id: str = ORG_ID, account: str = "", days: int = 14):
    """Per-day ingest health for the last N days so "my file isn't ingesting" is answerable from the page.
    Three DISTINCT, honest states per day (no more silent green ✓):
      • ingested  — the daily feed has PRICED rows for that business date (healthy);
      • zero_priced — rows landed but every Ext Price is 0 (a degraded/price-less export → parse issue);
      • missing   — no feed rows for that date at all (b2bsoft didn't deliver, or the guard refused it).
    Plus the recent processing outcomes (ok / partial / refused / parse-skip / error) and the mailbox's
    last run + any cross-org misfile warning. All org-scoped reads; falls back to a Python tally if the
    aggregate RPC (mig 200) isn't applied yet."""
    require_org(org_id)
    client = sb()
    days = max(1, min(int(days or 14), 60))
    try:
        from zoneinfo import ZoneInfo
        today = _datetime.now(_timezone.utc).astimezone(ZoneInfo(settings.BUSINESS_TZ or "America/New_York")).date()
    except Exception:
        today = _datetime.now(_timezone.utc).date()
    start = today - _timedelta(days=days - 1)

    # Per-day feed tally — aggregate in Postgres (RPC), fall back to a bounded Python tally.
    by_date = {}
    try:
        rpc = client.schema("commcalc").rpc("sales_feed_daily_health", {
            "p_org": org_id, "p_from": start.isoformat(), "p_to": today.isoformat()}).execute().data or []
        for r in rpc:
            by_date[str(r.get("trans_date"))[:10]] = {
                "rows": int(r.get("n_rows") or 0), "priced": int(r.get("n_priced") or 0),
                "amount": float(r.get("amount") or 0)}
    except Exception:
        try:
            rows = (client.schema("commcalc").table("daily_sales_feed").select("trans_date,ext_price")
                    .eq("org_id", org_id).gte("trans_date", start.isoformat())
                    .lte("trans_date", today.isoformat()).limit(1000000).execute().data) or []
            for r in rows:
                d = str(r.get("trans_date") or "")[:10]
                if not d:
                    continue
                b = by_date.setdefault(d, {"rows": 0, "priced": 0, "amount": 0.0})
                b["rows"] += 1
                if safe_float(r.get("ext_price")) != 0:
                    b["priced"] += 1
                    b["amount"] = round(b["amount"] + safe_float(r.get("ext_price")), 2)
        except Exception:
            by_date = {}

    day_list = []
    d = start
    while d <= today:
        ds = d.isoformat()
        b = by_date.get(ds) or {"rows": 0, "priced": 0, "amount": 0}
        state = ("ingested" if b["priced"] > 0 else ("zero_priced" if b["rows"] > 0 else "missing"))
        day_list.append({"date": ds, "rows": b["rows"], "priced": b["priced"],
                         "amount": round(float(b["amount"]), 2), "state": state})
        d += _timedelta(days=1)

    # Recent processing outcomes, classified into distinct honest states (org-scoped, optional account).
    q = (client.schema("commcalc").table("email_processed").select("*").eq("org_id", org_id)
         .order("processed_at", desc=True).limit(60))
    if account.strip():
        try:
            q = q.eq("account", account.strip())
        except Exception:
            pass
    proc = q.execute().data or []
    counts = {"ok": 0, "partial": 0, "refused": 0, "parse_skip": 0, "error": 0}
    for p in proc:
        st = (p.get("status") or "").lower()
        if st == "ok":
            counts["partial" if p.get("detail") else "ok"] += 1
        elif st == "skipped":
            # 'skipped' covers a price-guard refusal AND an Inventory-Aging 0-store parse — keep them distinct.
            counts["parse_skip" if "parsed 0" in (p.get("detail") or "").lower() else "refused"] += 1
        elif st == "error":
            counts["error"] += 1

    # Mailbox status + the cross-org misfile warning for the selected mailbox.
    cfg = _email_cfg(client, org_id, account.strip() or "default") or {}
    conflicts = _mailbox_cross_org(client, cfg.get("username"), org_id)

    return {"days": day_list, "window_days": days, "from": start.isoformat(), "to": today.isoformat(),
            "recent_counts": counts, "recent": proc[:20],
            "mailbox": {"account": cfg.get("account") or account.strip() or "default",
                        "username": cfg.get("username"), "enabled": bool(cfg.get("enabled")),
                        "last_run_at": cfg.get("last_run_at"), "last_status": cfg.get("last_status"),
                        "next_run_at": cfg.get("next_run_at")},
            "cross_org_warning": bool(conflicts), "cross_org_conflicts": conflicts}


# ── Connector-health alerts (user 2026-07-04): if a data source/sweep ERRORS or goes STALE, WhatsApp/
# email the assigned person. Reuses the cash-mgmt alert foundation (closing._send_alert → email+WhatsApp,
# deduped via storeops.alert_log). Recipients come from alert_recipient scope 'connector' (falls back to
# the tenant DM); a cron hits /connector-health/run-due hourly. ──────────────────────────────────────
_CONNECTOR_HEALTH_SOURCES = [
    ("data_source", "Portal login"), ("email_sweep_config", "Email import"),
    ("epay_sweep_config", "ePay sweep"), ("dlar_sweep_config", "DLAR sweep"),
    ("vip_sweep_config", "VIP sweep"), ("b2b_sweep_config", "B2B sweep"),
    ("ftp_sweep_config", "FTP import"),
]
_CONNECTOR_STALE_HOURS = 30  # a daily source with no run in >30h has silently stalled


def _scan_connector_health(client):
    """Enabled data sources across the sweep + portal registries that have ERRORED or gone STALE."""
    now = _datetime.now(_timezone.utc)
    out = []
    for table, label in _CONNECTOR_HEALTH_SOURCES:
        try:
            rows = (client.schema("commcalc").table(table).select("*").execute().data) or []
        except Exception:
            rows = []
        for r in rows:
            if r.get("enabled", r.get("is_enabled", True)) is False:
                continue
            status = (r.get("last_status") or "").lower()
            name = (r.get("label") or r.get("source_name") or r.get("account")
                    or r.get("vendor_name") or label)
            failed = ("error" in status) or ("fail" in status) or ("403" in status)
            stale = False
            lr = r.get("last_run_at")
            if not failed and lr:
                try:
                    last = _datetime.fromisoformat(str(lr).replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=_timezone.utc)
                    stale = (now - last).total_seconds() > _CONNECTOR_STALE_HOURS * 3600
                except Exception:
                    stale = False
            if failed or stale:
                kind = "errored" if failed else "stalled"
                out.append({
                    "org_id": r.get("org_id") or ORG_ID, "source": f"{label} — {name}", "kind": kind,
                    "detail": (r.get("last_status") or f"no run in {_CONNECTOR_STALE_HOURS}h+")[:180],
                    "ref_key": f"connector:{table}:{r.get('id')}:{now.date()}:{kind}",
                })
    return out


@router.get("/connector-health")
def connector_health(org_id: str = ORG_ID):
    """This tenant's errored/stale data sources — drives a health banner + the alert cron."""
    require_org(org_id)
    return [f for f in _scan_connector_health(sb()) if f["org_id"] == org_id]


@router.post("/connector-health/run-due")
async def connector_health_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint (hourly): WhatsApp/email the assigned person for any errored/stale data source.
    Deduped via alert_log so it won't re-alert every tick until the source recovers."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    failures = _scan_connector_health(client)
    from app.modules.closing.router import _send_alert  # lazy import: avoids a commcalc↔closing cycle
    sent = []
    for f in failures:
        subject = f"⚠️ Data source {f['kind']}: {f['source']}"
        text = (f"MetricsPro — a data source {f['kind']}.\n\n{f['source']}\nStatus: {f['detail']}\n\n"
                f"Check the connector / re-run the sweep so reports keep updating.")
        try:
            res = await _send_alert(client, f["org_id"], "connector", subject, text, f["ref_key"])
            sent.append({"source": f["source"], "kind": f["kind"], "result": res})
        except Exception as e:
            sent.append({"source": f["source"], "kind": f["kind"], "error": str(e)[:160]})
    return {"checked_sources": len(_CONNECTOR_HEALTH_SOURCES), "failing": len(failures), "sent": sent}


@router.post("/email-sweep/run-due")
async def email_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint — run the email sweep if enabled + due, then advance next_run_at."""
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
        raise HTTPException(403, "forbidden")
    client = sb()
    now_iso = _datetime.now(_timezone.utc).isoformat()
    due = (client.schema('commcalc').table('email_sweep_config').select('*')
           .eq('enabled', True).lte('next_run_at', now_iso).execute().data) or []
    ran = []
    for cfg in due:
        oid = cfg.get('org_id') or ORG_ID
        acct = cfg.get('account') or 'default'
        res = await _run_email_sweep(oid, acct)
        nxt = _vip_next_run(cfg.get('frequency') or 'daily', None, None, cfg.get('hour'), 'America/New_York')
        _email_status_update(client, oid, acct, {'next_run_at': nxt})
        ran.append({"org_id": oid, "account": acct, "result": res})
    return {"ran": len(ran), "detail": ran}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# DATA SOURCES (migration 083) — the multi-processor registry. Real-world shape it models: one
# company → N distributors → N payment processors per distributor → N LOGINS per processor (all
# stores for one carrier usually live under one login). One commcalc.data_source row per LOGIN.
# Everything a source pulls lands in the shared raw tables stamped with source_id, so multiple
# sources COMBINE into one database. Portal scraping is dispatched per processor below — processors
# without a wired scraper still ingest via the email sweep or manual upload (ma_* upload types).
# ════════════════════════════════════════════════════════════════════════════════════════════════
_SOURCE_FIELDS = ["distributor_id", "carrier_id", "processor", "label", "portal_url", "username",
                  "account_id", "password", "proxy_url", "enabled", "frequency", "hour", "notes"]
# Columns that never leave the backend (credentials + serialized browser sessions).
_SOURCE_SECRETS = ("password", "session_state", "pending_state")


async def _vidapay_scraper(org_id, src_row):
    """_SOURCE_SCRAPERS handler for the VidaPay / Total Access portal. Uses the AUTHENTICATED
    session stored by the login/2FA flow; a missing/expired session raises VidaPayAuthError, which
    run_data_source turns into an auth_status=needs_2fa prompt rather than a hard error."""
    from app.modules.commcalc import vidapay_sweep as vp
    from fastapi.concurrency import run_in_threadpool
    # months_back is config-driven per source (notes-free knob on the row); default 2 months so a
    # manual "Pull now" / scheduled pull stays inside the gateway budget. Operator widens via the row's
    # months_back column; each report's param_spec.max_months_back still caps it (≤ 1 year hard limit).
    try:
        mb = int(src_row.get("months_back") or 2)
    except Exception:
        mb = 2
    return await run_in_threadpool(
        vp.run_vidapay_sweep, sb(), org_id, src_row.get("portal_url"),
        src_row.get("session_state"), src_row.get("id"), src_row.get("carrier_id"),
        src_row.get("proxy_url"), src_row.get("account_id"), mb, src_row)


async def _b2bsoft_scraper(org_id, src_row):
    """_SOURCE_SCRAPERS handler for b2bsoft (wsreports.b2bsoft.com — the daily Sales Transaction
    Details source). Reuses the SAME interactive-2FA + persisted-session + residential-proxy machinery
    as VidaPay (the login/start + login/verify endpoints already call the generic begin_login/
    complete_2fa), so a data_source with processor='b2bsoft' logs in with 2FA entered in the UI and the
    session is kept alive for ~90 days. A missing/expired session raises VidaPayAuthError → needs_2fa."""
    from app.modules.commcalc import vidapay_sweep as vp
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(
        vp.run_b2bsoft_sweep, sb(), org_id, src_row.get("portal_url"),
        src_row.get("session_state"), src_row.get("id"), src_row.get("carrier_id"),
        src_row.get("proxy_url"))


# processor key → scraper callable (org_id, source_row) -> result dict. VidaPay + Total Access
# share the same "Master Agent" portal family (vidapaycrm.com); both route through _vidapay_scraper.
# b2bsoft (wsreports) reuses the identical Playwright session/2FA/proxy path via _b2bsoft_scraper.
_SOURCE_SCRAPERS = {"vidapay": _vidapay_scraper, "total_access": _vidapay_scraper,
                    "b2bsoft": _b2bsoft_scraper, "b2b": _b2bsoft_scraper}


def _strip_source_pw(row):
    """Public view of a data_source row — drops every secret, exposes only booleans/status."""
    row = dict(row)
    row["has_password"] = bool(row.get("password"))
    row["has_session"] = bool(row.get("session_state"))
    row["has_login_shot"] = bool(row.get("login_shot"))
    for k in _SOURCE_SECRETS:
        row.pop(k, None)
    # Not secret, but ~50–100KB of base64 — the list is polled every 3s during login, so the
    # screenshot is served only by the dedicated /login/screenshot endpoint.
    row.pop("login_shot", None)
    return row


def _store_login_shot(client, sid, org_id, shot):
    """Best-effort: persist the 'what the headless browser saw' JPEG (base64) for the
    /login/screenshot endpoint. A SEPARATE update so a missing login_shot column (migration
    203_commission_login_screenshot.sql not applied yet) can never break the login flow itself."""
    if not shot:
        return
    try:
        client.schema("commcalc").table("data_source").update(
            {"login_shot": shot, "login_shot_at": datetime.now(timezone.utc).isoformat()})\
            .eq("id", sid).eq("org_id", org_id).execute()
    except Exception:
        pass


@router.get("/data-sources")
def list_data_sources(org_id: str = ORG_ID):
    """All of the tenant's payment-processor logins (passwords stripped). ready:false pre-083."""
    require_org(org_id)
    try:
        rows = (sb().schema("commcalc").table("data_source").select("*")
                .eq("org_id", org_id).order("created_at").execute().data) or []
    except Exception:
        return {"ready": False, "sources": [], "note": "Run migration 083_total_processor_sources.sql to enable."}
    return {"ready": True, "sources": [_strip_source_pw(r) for r in rows],
            "scrapers_wired": sorted(_SOURCE_SCRAPERS.keys())}


@router.put("/data-sources")
def save_data_source(body: dict, org_id: str = ORG_ID):
    """Create/update one login. Omitting password on an update KEEPS the stored one."""
    require_org(org_id)
    row = {k: body[k] for k in _SOURCE_FIELDS if k in body}
    if not (row.get("processor") or "").strip() and not body.get("id"):
        raise HTTPException(400, "processor is required (e.g. vidapay, total_access, epay)")
    for k in ("distributor_id", "carrier_id"):
        if k in row and not (row[k] or "").strip():
            row[k] = None
    if "password" in row and not (row.get("password") or "").strip():
        row.pop("password")   # blank password on the form = keep the saved one
    if (row.get("portal_url") or "").strip():
        # a scheme-less host crashes the Playwright login ("Cannot navigate to invalid URL")
        pu = row["portal_url"].strip()
        row["portal_url"] = pu if "://" in pu else "https://" + pu.lstrip("/")
    client = sb()
    try:
        if body.get("id"):
            client.schema("commcalc").table("data_source").update(row)\
                .eq("id", body["id"]).eq("org_id", org_id).execute()
            return {"ok": True, "id": body["id"]}
        row["org_id"] = org_id
        r = client.schema("commcalc").table("data_source").insert(row).execute()
        return {"ok": True, "id": (r.data or [{}])[0].get("id")}
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 083 applied? {e}")


@router.delete("/data-sources/{sid}")
def delete_data_source(sid: str, org_id: str = ORG_ID):
    require_org(org_id)
    sb().schema("commcalc").table("data_source").delete().eq("id", sid).eq("org_id", org_id).execute()
    return {"ok": True}


@router.post("/data-source/test-proxy")
def test_proxy(body: dict, org_id: str = ORG_ID):
    """Make ONE request through the given proxy_url to an IP-echo, so an operator can confirm a
    residential/allow-listed proxy WORKS (and see the egress IP + country) BEFORE fighting a portal's 2FA.
    Also probes the server's OWN egress (no proxy) for comparison, so 'routed through proxy' is provable.
    Read-only; nothing is stored."""
    proxy_url = (body.get("proxy_url") or "").strip()
    if not proxy_url:
        raise HTTPException(400, "enter a proxy_url first (http://user:pass@host:port)")
    import time, requests

    def _probe(px):
        proxies = {"http": px, "https": px} if px else None
        t0 = time.time()
        try:
            r = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=15,
                             headers={"User-Agent": "MetricsPro-proxy-test/1.0"})
            ms = int((time.time() - t0) * 1000)
            if r.status_code != 200:
                return {"ok": False, "error": f"HTTP {r.status_code} from ipinfo", "elapsed_ms": ms}
            d = r.json()
            return {"ok": True, "ip": d.get("ip"), "city": d.get("city"), "region": d.get("region"),
                    "country": d.get("country"), "org": d.get("org"), "elapsed_ms": ms}
        except Exception as e:
            msg = str(e)
            if "SOCKS" in msg or "Missing dependencies" in msg:
                msg = "SOCKS proxy needs the PySocks dependency — use an http:// proxy endpoint instead."
            return {"ok": False, "error": msg[:300], "elapsed_ms": int((time.time() - t0) * 1000)}

    via = _probe(proxy_url)
    direct = _probe(None)
    routed = bool(via.get("ok") and via.get("ip") and via.get("ip") != direct.get("ip"))
    return {
        "proxy": via, "direct": direct,
        "routed_through_proxy": routed,
        "is_us": (via.get("country") == "US") if via.get("ok") else None,
        "summary": (
            f"✅ Working — egress {via.get('ip')} ({via.get('city') or '?'}, {via.get('country') or '?'})"
            + ("" if routed else " ⚠️ but it matches the server's own IP — traffic may NOT be going through the proxy")
            + ("" if via.get("country") == "US" else " ⚠️ not a US IP")
            if via.get("ok") else f"❌ {via.get('error')}"
        ),
    }


@router.post("/data-sources/{sid}/run")
async def run_data_source(sid: str, org_id: str = ORG_ID):
    """Pull now from this login. Dispatches to the processor's scraper when one is wired; until
    then the row records an honest status and the data path is the email sweep / manual upload."""
    require_org(org_id)
    client = sb()
    rows = (client.schema("commcalc").table("data_source").select("*")
            .eq("id", sid).eq("org_id", org_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "unknown data source")
    src_row = rows[0]
    proc = (src_row.get("processor") or "").strip().lower()
    handler = _SOURCE_SCRAPERS.get(proc)
    if not handler:
        status = (f"scraper for '{proc}' not wired yet — reports ingest via the email sweep or the "
                  f"Data Imports upload (ma_commission / ma_daily_tx / ma_fulfillment) today")
        try:
            client.schema("commcalc").table("data_source").update(
                {"last_run_at": datetime.now(timezone.utc).isoformat(), "last_status": status})\
                .eq("id", sid).eq("org_id", org_id).execute()
        except Exception:
            pass
        return {"ok": False, "error": status}
    from app.modules.commcalc.vidapay_sweep import VidaPayAuthError
    # PREFER THE LIVE AUTHENTICATED BROWSER. T-CETRA/VidaPay re-challenges the cold storage_state restore
    # (a fresh browser + a new egress IP + a new server session is not the trusted device → it lands on
    # the 2FA screen and the cold path raises "session expired"; owner repro 2026-07-16). If a 🔴 Live
    # login session for this source is still alive in THIS worker's memory, run the pull on the SAME page
    # that just passed 2FA. Falls back to the cold restore for scheduled pulls / other workers.
    if proc in ("vidapay", "total_access"):
        try:
            from app.modules.commcalc import live_login as _ll
            from fastapi.concurrency import run_in_threadpool
            sess = _ll.get_session(sid, org_id)
        except Exception:
            sess = None
        if sess is not None and sess.can_pull():
            res = await run_in_threadpool(sess.run_pull_blocking, 900)
            if res is not None:
                try:
                    client.schema("commcalc").table("data_source").update(
                        {"last_run_at": datetime.now(timezone.utc).isoformat(),
                         "last_status": str((res or {}).get("status") or "ok"),
                         "auth_status": "authenticated"})\
                        .eq("id", sid).eq("org_id", org_id).execute()
                except Exception:
                    pass
                return {"ok": True, "via": "live-session", **(res or {})}
            # res is None → the live session couldn't pull (timed out / just closed) → fall through.
    try:
        res = await handler(org_id, src_row)
        client.schema("commcalc").table("data_source").update(
            {"last_run_at": datetime.now(timezone.utc).isoformat(),
             "last_status": str((res or {}).get("status") or "ok"),
             "auth_status": "authenticated"})\
            .eq("id", sid).eq("org_id", org_id).execute()
        return {"ok": True, **(res or {})}
    except VidaPayAuthError as e:
        # Session expired / never authenticated — not a hard failure; prompt the operator to log in.
        # Do NOT null session_state here: a transient nav/validity blip would otherwise DESTROY a good
        # saved login and force a needless re-login (+ new 2FA code). Leave the stored session in place;
        # a real re-login overwrites it, and a still-valid session is reused on the next Pull.
        try:
            client.schema("commcalc").table("data_source").update(
                {"last_run_at": datetime.now(timezone.utc).isoformat(),
                 "last_status": f"needs login: {str(e)[:160]}",
                 "auth_status": "needs_2fa", "auth_message": str(e)[:300]})\
                .eq("id", sid).eq("org_id", org_id).execute()
        except Exception:
            pass
        return {"ok": False, "needs_2fa": True, "error": str(e)}
    except Exception as e:
        try:
            client.schema("commcalc").table("data_source").update(
                {"last_run_at": datetime.now(timezone.utc).isoformat(),
                 "last_status": f"error: {str(e)[:180]}"})\
                .eq("id", sid).eq("org_id", org_id).execute()
        except Exception:
            pass
        raise HTTPException(500, f"pull failed: {e}")


# ── REPORT-PULL MAPPING (mig 207) — the config that decides report→table→column (RULE TWO). Visible +
# editable at /commcalc/report-mappings. An org's row overrides the house/default row; a missing table
# degrades to report_pull.DEFAULT_REPORT_SPECS so the engine still has sane defaults. ────────────────
_RPM_FIELDS = ["report_key", "display_name", "target_table", "column_map", "param_spec",
               "export_pref", "enabled", "sort_order", "processor"]


@router.get("/report-pull-map")
def list_report_pull_map(processor: str = "", org_id: str = ORG_ID):
    """The effective report-pull specs for this tenant (house defaults + this org's overrides). Each row
    carries `inherited` (true = showing the house default, editing creates an override) so the admin page
    shows exactly what will run and lets it be edited. Degrades to the Python defaults pre-migration 207."""
    require_org(org_id)
    from app.modules.commcalc import report_pull as rp
    proc = (processor or None)
    try:
        specs = rp.resolve_report_specs(sb(), org_id, processor=proc, only_enabled=False)
        ready = True
    except Exception:
        specs = [{**s, "org_id": org_id} for s in rp.default_specs(proc)]
        ready = False
    out = []
    for s in specs:
        s = dict(s)
        s["inherited"] = str(s.get("org_id")) != str(org_id)
        s.pop("_inherited", None)
        out.append(s)
    return {"ready": ready, "reports": out, "house_org": rp.HOUSE_ORG,
            "targets_note": "raw_ma_marketplace_orders is a view over raw_ma_fulfillment (mod-asset)"}


@router.put("/report-pull-map")
def save_report_pull_map(body: dict, org_id: str = ORG_ID):
    """Create/update THIS org's override for one report_key (never mutates the house default row — a
    tenant edit becomes a tenant-scoped override). Upserts on (org_id, report_key)."""
    require_org(org_id)
    rk = (body.get("report_key") or "").strip()
    if not rk:
        raise HTTPException(400, "report_key is required")
    row = {k: body[k] for k in _RPM_FIELDS if k in body}
    row["org_id"] = org_id
    row["report_key"] = rk
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    client = sb()
    try:
        existing = (client.schema("commcalc").table("report_pull_map").select("id")
                    .eq("org_id", org_id).eq("report_key", rk).limit(1).execute().data) or []
        if existing:
            client.schema("commcalc").table("report_pull_map").update(row)\
                .eq("org_id", org_id).eq("report_key", rk).execute()
            return {"ok": True, "updated": True}
        r = client.schema("commcalc").table("report_pull_map").insert(row).execute()
        return {"ok": True, "id": (r.data or [{}])[0].get("id")}
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 207 applied? {e}")


@router.post("/report-pull-map/{report_key}/reset")
def reset_report_pull_map(report_key: str, org_id: str = ORG_ID):
    """Drop THIS org's override for a report_key so it falls back to the house default."""
    require_org(org_id)
    if str(org_id) == "00000000-0000-0000-0000-000000000001":
        raise HTTPException(400, "cannot reset the house default itself")
    sb().schema("commcalc").table("report_pull_map").delete()\
        .eq("org_id", org_id).eq("report_key", report_key).execute()
    return {"ok": True}


@router.post("/report-pull-map/reseed")
def reseed_report_pull_map(org_id: str = ORG_ID):
    """(Re)seed the house/default rows from report_pull.DEFAULT_REPORT_SPECS — idempotent, only inserts
    the report_keys that are missing. A convenience mirror of migration 207's seed for the operator."""
    require_org(org_id)
    from app.modules.commcalc import report_pull as rp
    client = sb()
    inserted = []
    try:
        have = {r.get("report_key") for r in ((client.schema("commcalc").table("report_pull_map")
                .select("report_key").eq("org_id", rp.HOUSE_ORG).execute().data) or [])}
    except Exception as e:
        raise HTTPException(400, f"report_pull_map not available — run migration 207 first: {e}")
    for s in rp.DEFAULT_REPORT_SPECS:
        if s["report_key"] in have:
            continue
        row = {k: s.get(k) for k in _RPM_FIELDS}
        row["org_id"] = rp.HOUSE_ORG
        client.schema("commcalc").table("report_pull_map").insert(row).execute()
        inserted.append(s["report_key"])
    return {"ok": True, "inserted": inserted, "already_present": sorted(have)}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# PER-CARRIER MANUAL UPLOAD (mig 212) — the SAP-style manual track that runs in PARALLEL to the flaky
# live portal pull (owner directive 2026-07-17). A user picks a carrier, sees that carrier's report
# types with their mapping status, maps a sample ONCE (or inherits the report_pull default), then
# uploads data files — HISTORICAL (one file spanning many months, split per row's own date) or APPEND
# (dedup-and-append, like the Boost feed). INGEST-ONLY: nothing here recomputes a payout. The mapping
# store is commcalc.manual_report_mapping (per org,carrier,report_key), decoupled from the scraper's
# report_pull_map; absent an override the report_pull default column_map is used, so the MA reports are
# pre-mapped and uploads work with mig 212 unrun. ─────────────────────────────────────────────────
def _ma_effective_spec(org_id, report_key):
    """The report_pull spec for a report_key (this org's report_pull_map override wins over the house
    default, else the Python default). Returns the spec dict or None if the report_key is unknown."""
    from app.modules.commcalc import report_pull as rp
    try:
        specs = rp.resolve_report_specs(sb(), org_id, processor=None, only_enabled=False)
    except Exception:
        specs = [{**s, "org_id": org_id} for s in rp.default_specs(None)]
    for s in specs:
        if s.get("report_key") == report_key:
            return dict(s)
    return None


def _ma_all_specs(org_id):
    from app.modules.commcalc import report_pull as rp
    try:
        return [dict(s) for s in rp.resolve_report_specs(sb(), org_id, processor=None, only_enabled=True)]
    except Exception:
        return [{**s, "org_id": org_id} for s in rp.default_specs(None)]


def _ma_saved_mapping(org_id, carrier_id, report_key):
    """The manual_report_mapping override row for (org,carrier,report_key), or None. Never raises (mig
    212 unrun ⇒ None ⇒ the report_pull default is used)."""
    if not carrier_id:
        return None
    try:
        rows = (sb().schema("commcalc").table("manual_report_mapping").select("*")
                .eq("org_id", org_id).eq("carrier_id", carrier_id).eq("report_key", report_key)
                .limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _ma_eff_column_map(org_id, carrier_id, report_key, spec=None):
    """The column_map the manual upload will use: a saved override wins, else the report_pull default."""
    spec = spec or _ma_effective_spec(org_id, report_key) or {}
    saved = _ma_saved_mapping(org_id, carrier_id, report_key)
    return ma_upload.effective_column_map((saved or {}).get("column_map"), spec.get("column_map")), saved, spec


@router.get("/manual-upload/reports")
def manual_upload_reports(carrier_id: str = "", org_id: str = ORG_ID):
    """The report catalog for a carrier's manual upload, each with its saved-mapping STATUS so a user
    never re-maps what already exists. Reports come from the config catalog (report_pull specs); the
    per-carrier division + mapping live in commcalc.manual_report_mapping."""
    require_org(org_id)
    if not carrier_id:
        raise HTTPException(400, "carrier_id is required (uploads are divided per carrier)")
    out = []
    for spec in _ma_all_specs(org_id):
        rk = spec.get("report_key")
        saved = _ma_saved_mapping(org_id, carrier_id, rk)
        status = ma_upload.mapping_status(saved, spec.get("column_map"))
        ps = spec.get("param_spec") or {}
        out.append({
            "report_key": rk,
            "display_name": spec.get("display_name") or rk,
            "target_table": spec.get("target_table"),
            "calibration": bool(ps.get("calibration")),
            "date_field": ma_upload.date_field_for(rk, spec),
            "dedup_keys": list(ma_upload.DEDUP_KEYS.get(rk, ())),
            "join_note": ("Activation Order ↔ MA Daily Tx Order Number"
                          if rk in ("ma_commission", "ma_daily_tx") else None),
            **status,
        })
    return {"carrier_id": carrier_id, "reports": out,
            "money_note": "Ingest only — no payout is recalculated on upload."}


@router.get("/manual-upload/mapping")
def manual_upload_get_mapping(report_key: str, carrier_id: str = "", org_id: str = ORG_ID):
    """The effective column mapping for (org,carrier,report_key): the saved override if present, else
    the report_pull default — plus the target-field catalog for the mapping editor (pick-don't-type)."""
    require_org(org_id)
    if not carrier_id:
        raise HTTPException(400, "carrier_id is required")
    eff, saved, spec = _ma_eff_column_map(org_id, carrier_id, report_key)
    return {
        "report_key": report_key, "carrier_id": carrier_id,
        "target_table": spec.get("target_table"),
        "column_map": eff,
        "target_fields": ma_upload.target_field_catalog(eff or spec.get("column_map")),
        "status": ma_upload.mapping_status(saved, spec.get("column_map")),
        "sample_headers": (saved or {}).get("sample_headers"),
    }


@router.post("/manual-upload/detect")
async def manual_upload_detect(report_key: str = Form(...), carrier_id: str = Form(""),
                               file: UploadFile = File(...), org_id: str = ORG_ID):
    """Read an uploaded SAMPLE file's headers → suggest a source-header ⇢ dest-field mapping and detect
    which MONTHS the file spans (for the period picker + a multi-month preview). No ingest."""
    require_org(org_id)
    if not carrier_id:
        raise HTTPException(400, "carrier_id is required")
    from app.modules.commcalc import report_pull as rp
    contents = await file.read()
    try:
        rows = rp.parse_export_bytes(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    headers = list(rows[0].keys()) if rows else []
    eff, saved, spec = _ma_eff_column_map(org_id, carrier_id, report_key)
    eff_map = eff or spec.get("column_map") or {}
    # derive periods via the SAME per-row mapping the ingest uses
    mapped = rp.apply_column_map(rows, {**spec, "column_map": eff_map}, org_id)
    date_col = ma_upload.date_field_for(report_key, spec)
    return {
        "headers": headers, "rows_in": len(rows),
        "target_fields": ma_upload.target_field_catalog(eff_map),
        "suggestions": ma_upload.suggest_sources(headers, eff_map),
        "detected_periods": ma_upload.detected_periods(mapped, date_col),
    }


@router.post("/manual-upload/mapping")
def manual_upload_save_mapping(body: dict, org_id: str = ORG_ID):
    """Persist the per-(org,carrier,report_key) manual column mapping. Accepts either a ready column_map
    or a {dest_col: source_header} selection (field_sources) — the latter inherits value TYPES from the
    report_pull default so numeric/date casting is preserved. SAP: map once, upload against it forever."""
    require_org(org_id)
    rk = (body.get("report_key") or "").strip()
    carrier_id = (body.get("carrier_id") or "").strip()
    if not rk or not carrier_id:
        raise HTTPException(400, "report_key and carrier_id are required")
    spec = _ma_effective_spec(org_id, rk) or {}
    default_map = spec.get("column_map") or {}
    column_map = body.get("column_map")
    if not (isinstance(column_map, dict) and column_map):
        column_map = ma_upload.build_column_map(body.get("field_sources") or {}, default_map)
    if not column_map:
        raise HTTPException(400, "no columns mapped")
    row = {
        "org_id": org_id, "carrier_id": carrier_id, "report_key": rk,
        "target_table": spec.get("target_table"),
        "column_map": column_map,
        "sample_headers": body.get("sample_headers"),
        "saved_by": (body.get("saved_by") or None),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    client = sb()
    try:
        existing = (client.schema("commcalc").table("manual_report_mapping").select("id")
                    .eq("org_id", org_id).eq("carrier_id", carrier_id).eq("report_key", rk)
                    .limit(1).execute().data) or []
        if existing:
            client.schema("commcalc").table("manual_report_mapping").update(row)\
                .eq("org_id", org_id).eq("carrier_id", carrier_id).eq("report_key", rk).execute()
            return {"ok": True, "updated": True, "columns": len(column_map)}
        r = client.schema("commcalc").table("manual_report_mapping").insert(row).execute()
        return {"ok": True, "id": (r.data or [{}])[0].get("id"), "columns": len(column_map)}
    except Exception as e:
        raise HTTPException(400, f"Could not save mapping — is migration 212 applied? {e}")


@router.post("/manual-upload/reset-mapping")
def manual_upload_reset_mapping(body: dict, org_id: str = ORG_ID):
    """Drop the saved override for (org,carrier,report_key) so it falls back to the report_pull default."""
    require_org(org_id)
    rk = (body.get("report_key") or "").strip()
    carrier_id = (body.get("carrier_id") or "").strip()
    if not rk or not carrier_id:
        raise HTTPException(400, "report_key and carrier_id are required")
    try:
        sb().schema("commcalc").table("manual_report_mapping").delete()\
            .eq("org_id", org_id).eq("carrier_id", carrier_id).eq("report_key", rk).execute()
    except Exception:
        pass
    return {"ok": True}


def _ma_existing_keys(client, org_id, table, report_key, win_start, win_end, date_col):
    """The set of natural dedup keys already present in `table` for this org within [win_start, win_end]
    (by date_col). Used by APPEND to skip rows already ingested (from ANY source — a manual append never
    re-duplicates rows the portal pull already has). Paginated + ordered for stable reads. Best-effort."""
    cols = ma_upload.DEDUP_KEYS.get(report_key)
    keys = set()
    try:
        sel = ",".join(dict.fromkeys(list(cols or ()) + ([date_col] if date_col else [])))
        if not sel:
            return keys
        start, page = 0, 1000
        while True:
            q = (client.schema("commcalc").table(table).select(sel)
                 .eq("org_id", org_id).order("id"))
            if date_col and win_start:
                q = q.gte(date_col, win_start)
            if date_col and win_end:
                q = q.lte(date_col, win_end)
            rows = (q.range(start, start + page - 1).execute().data) or []
            for r in rows:
                keys.add(ma_upload.natural_key(report_key, r))
            if len(rows) < page:
                break
            start += page
            if start > 500000:      # hard safety cap
                break
    except Exception:
        pass
    return keys


def _ma_counterpart_order_numbers(client, org_id, report_key, win_start, win_end):
    """For the Activation-Order ↔ Order-Number linkage indicator: the counterpart table's order-number
    values within the window. ma_commission ↔ raw_ma_daily_tx.order_number; ma_daily_tx ↔
    raw_ma_commission.activation_order. Best-effort, capped."""
    if report_key == "ma_commission":
        table, col, dcol = "raw_ma_daily_tx", "order_number", "tx_date"
    elif report_key == "ma_daily_tx":
        table, col, dcol = "raw_ma_commission", "activation_order", "tx_date"
    else:
        return set()
    out = set()
    try:
        start, page = 0, 1000
        while True:
            q = (client.schema("commcalc").table(table).select(col).eq("org_id", org_id).order("id"))
            if win_start:
                q = q.gte(dcol, win_start)
            if win_end:
                q = q.lte(dcol, win_end)
            rows = (q.range(start, start + page - 1).execute().data) or []
            for r in rows:
                if r.get(col):
                    out.add(r.get(col))
            if len(rows) < page:
                break
            start += page
            if start > 500000:
                break
    except Exception:
        pass
    return out


@router.post("/manual-upload/ingest")
async def manual_upload_ingest(
    report_key: str = Form(...),
    carrier_id: str = Form(...),
    mode: str = Form("append"),          # 'append' (dedup-and-add) | 'historical' (replace covered months)
    date_from: str = Form(""),           # optional scope clip (YYYY-MM-DD) within the file
    date_to: str = Form(""),
    file: UploadFile = File(...),
    org_id: str = ORG_ID,
):
    """Manual ingest of an MA report file. Splits rows to their real months from the file's OWN date
    column (never forces a single period label onto multi-month data), then either APPENDs with dedup or
    HISTORICALly replaces each covered month's manual rows. INGEST-ONLY — no payout is recomputed."""
    require_org(org_id)
    if not carrier_id:
        raise HTTPException(400, "carrier_id is required (uploads are divided per carrier)")
    from app.modules.commcalc import report_pull as rp
    started = _datetime.now(timezone.utc)
    mode = (mode or "append").strip().lower()
    if mode not in ("append", "historical"):
        raise HTTPException(400, "mode must be 'append' or 'historical'")

    eff, saved, spec = _ma_eff_column_map(org_id, carrier_id, report_key)
    table = (spec.get("target_table") or "").strip()
    eff_map = eff or spec.get("column_map") or {}
    if not table:
        raise HTTPException(400, f"No target_table for report_key '{report_key}'.")
    if not eff_map:
        raise HTTPException(400, f"'{report_key}' has no column mapping. Map a sample first.")

    contents = await file.read()
    try:
        rows = rp.parse_export_bytes(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")

    stamp_carrier = carrier_id if _table_has_column(sb(), table, "carrier_id") else None
    mapped = rp.apply_column_map(rows, {**spec, "column_map": eff_map}, org_id,
                                 source_id=None, carrier_id=stamp_carrier)
    date_col = ma_upload.date_field_for(report_key, spec)

    # optional scope clip (day / week / custom range chosen in the UI)
    df, dt = (date_from or "").strip()[:10], (date_to or "").strip()[:10]
    if df or dt:
        def _in(r):
            d = str(r.get(date_col) or "")[:10] if date_col else ""
            if df and (not d or d < df):
                return False
            if dt and (not d or d > dt):
                return False
            return True
        mapped = [r for r in mapped if _in(r)]

    # in-file dedupe always applies (a file listing the same line twice never double-inserts)
    mapped, within_dropped = ma_upload.dedupe_within(mapped, report_key)
    win_start, win_end = ma_upload.date_span(mapped, date_col)
    client = sb()

    to_insert = []
    dupes_dropped = within_dropped
    replaced_periods = []
    if mode == "append":
        existing = _ma_existing_keys(client, org_id, table, report_key, win_start, win_end, date_col)
        to_insert, extra = ma_upload.filter_new(existing, mapped, report_key)
        # filter_new re-dedupes internally; account only the already-present portion beyond within_dropped
        dupes_dropped = extra
    else:  # historical — replace each covered month's MANUAL rows (never touches portal-pulled rows)
        by_period = ma_upload.group_by_period(mapped)
        for period, prows in by_period.items():
            if period:
                try:
                    d = (client.schema("commcalc").table(table).delete()
                         .eq("org_id", org_id).in_("period", _pvariants(period)).is_("source_id", "null"))
                    d.execute()
                    replaced_periods.append(period)
                except Exception:
                    pass
            to_insert.extend(prows)

    saved_n = 0
    for i in range(0, len(to_insert), 500):
        chunk = to_insert[i:i + 500]
        try:
            client.schema("commcalc").table(table).insert(chunk).execute()
            saved_n += len(chunk)
        except Exception as e:
            raise HTTPException(500, f"Insert into {table} failed at row {i}: {e}")

    # cheap post-upload linkage indicator (Activation Order ↔ Order Number) — recon is future work
    linkage = None
    try:
        counterpart = _ma_counterpart_order_numbers(client, org_id, report_key, win_start, win_end)
        if counterpart or report_key in ("ma_commission", "ma_daily_tx"):
            linkage = ma_upload.linkage_counts(report_key, to_insert, counterpart)
    except Exception:
        linkage = None

    periods = ma_upload.period_counts(to_insert)
    dcounts = ma_upload.date_counts(to_insert, date_col)
    note = (f"manual {mode}: {saved_n} row(s) saved, {dupes_dropped} duplicate(s) skipped"
            + (f"; replaced months {', '.join(replaced_periods)}" if replaced_periods else "")
            + (f"; span {win_start}..{win_end}" if win_start else ""))
    result = {
        "saved": saved_n, "rows_in": len(rows), "mode": mode,
        "report_key": report_key, "target_table": table, "carrier_id": carrier_id,
        "periods": periods, "date_span": [win_start, win_end],
        "dupes_dropped": dupes_dropped, "replaced_periods": replaced_periods,
        "linkage": linkage, "note": note,
        "money_note": "Ingest only — no payout recomputed. Review the loaded numbers before any recalc.",
        "_trace": {"rows_in": len(rows), "target_table": table,
                   "periods": periods, "date_counts": dcounts},
    }
    _write_upload_trace(org_id, source="manual", filename=getattr(file, "filename", None),
                        upload_type=report_key, period="", result=result,
                        duration_ms=int((_datetime.now(timezone.utc) - started).total_seconds() * 1000))
    return result


@router.post("/data-sources/sweep/run-due")
async def data_sources_run_due(org_id: str = ORG_ID):
    """Scheduled trigger (pg_cron → this endpoint, like the other /run-due sweeps): for every ENABLED
    data_source whose next_run_at has passed and whose processor has a wired scraper, pull ALL enabled
    reports (config-driven) and re-schedule next_run_at. The tenant does nothing in the UI. Best-effort
    per source — one failure never aborts the batch. NOT money-touching: this only INGESTS source data."""
    require_org(org_id)
    client = sb()
    now = datetime.now(timezone.utc)
    try:
        rows = (client.schema("commcalc").table("data_source").select("*")
                .eq("enabled", True)
                .or_(f"next_run_at.is.null,next_run_at.lte.{now.isoformat()}")
                .execute().data) or []
    except Exception as e:
        return {"ok": False, "error": f"data_source not ready (mig 083/084/207?): {e}", "ran": []}
    ran = []
    for s in rows:
        proc = (s.get("processor") or "").strip().lower()
        handler = _SOURCE_SCRAPERS.get(proc)
        oid = s.get("org_id") or org_id
        nxt = _vip_next_run(s.get("frequency") or "daily", None, None, s.get("hour"), "America/New_York")
        if not handler:
            continue
        try:
            res = await handler(oid, s)
            client.schema("commcalc").table("data_source").update(
                {"last_run_at": now.isoformat(), "last_status": str((res or {}).get("status") or "ok"),
                 "auth_status": "authenticated", "next_run_at": nxt})\
                .eq("id", s["id"]).eq("org_id", oid).execute()
            ran.append({"id": s["id"], "ok": True, "status": (res or {}).get("status")})
        except Exception as e:
            try:
                client.schema("commcalc").table("data_source").update(
                    {"last_run_at": now.isoformat(), "last_status": f"error: {str(e)[:160]}",
                     "next_run_at": nxt}).eq("id", s["id"]).eq("org_id", oid).execute()
            except Exception:
                pass
            ran.append({"id": s["id"], "ok": False, "error": str(e)[:200]})
    return {"ok": True, "ran": ran, "count": len(ran)}


def _do_portal_login(sid: str, org_id: str):
    """Blocking portal login (Playwright + residential proxy) — runs as a BACKGROUND task so the HTTP
    request returns instantly. A synchronous login through a residential proxy easily outlives the gateway
    timeout, which surfaced in the browser as 'Failed to fetch'. Writes the outcome to the row; the UI polls
    /data-sources until auth_status flips to needs_2fa / authenticated / error."""
    from app.modules.commcalc import vidapay_sweep as vp
    client = sb()
    rows = (client.schema("commcalc").table("data_source").select("*")
            .eq("id", sid).eq("org_id", org_id).limit(1).execute().data) or []
    if not rows:
        return
    s = rows[0]
    _login_fn = vp.begin_login_b2bsoft if (s.get("processor") or "").lower() in ("b2bsoft", "b2b") else vp.begin_login
    now = datetime.now(timezone.utc)
    try:
        res = _login_fn(s.get("portal_url"), s.get("account_id"), s.get("username"),
                        s.get("password"), s.get("proxy_url"))
    except vp.VidaPayLoginError as e:
        msg = str(e)
        if "egress" in msg.lower() or "waf" in msg.lower():
            msg += vp.egress_hint(s.get("proxy_url"))   # which IP did we actually go out from?
        client.schema("commcalc").table("data_source").update(
            {"auth_status": "error", "auth_message": msg[:400], "last_run_at": now.isoformat()})\
            .eq("id", sid).eq("org_id", org_id).execute()
        _store_login_shot(client, sid, org_id, getattr(e, "screenshot_b64", None))
        return
    except Exception as e:
        client.schema("commcalc").table("data_source").update(
            {"auth_status": "error", "auth_message": ("Login crashed: " + str(e))[:400],
             "last_run_at": now.isoformat()})\
            .eq("id", sid).eq("org_id", org_id).execute()
        _store_login_shot(client, sid, org_id, getattr(e, "screenshot_b64", None))
        return
    if res.get("status") == "authenticated":
        client.schema("commcalc").table("data_source").update(
            {"auth_status": "authenticated", "auth_message": "Logged in (no 2FA prompt). Session saved.",
             "session_state": res.get("storage_state"), "pending_state": None,
             "session_expires_at": (now + timedelta(hours=vp.SESSION_TTL_HOURS)).isoformat(),
             "last_run_at": now.isoformat()})\
            .eq("id", sid).eq("org_id", org_id).execute()
        _store_login_shot(client, sid, org_id, res.get("screenshot_b64"))
        return
    hint = res.get("two_fa_hint")
    sent_via = res.get("sent_via")
    btns = []
    try:
        for c in ((res.get("diag") or {}).get("controls") or []):
            v = (c.get("val") or "").strip()
            if v and c.get("vis") and v.lower() not in [b.lower() for b in btns]:
                btns.append(v)
    except Exception:
        btns = []
    if sent_via:
        amsg = f"Requested a code (clicked \u201c{sent_via}\u201d) \u2014 enter it below." + (f" Sent to: {hint}" if hint else "")
    else:
        amsg = "Enter the 2FA code." + (f" Sent to: {hint}" if hint else "")
        if btns:
            amsg += " If no code arrived, the portal likely needs a button clicked \u2014 buttons on the page: " + " | ".join(btns[:8])
    client.schema("commcalc").table("data_source").update(
        {"auth_status": "needs_2fa", "two_fa_hint": hint, "pending_state": res.get("storage_state"),
         "pending_started_at": now.isoformat(),
         "auth_message": amsg[:400],
         "last_run_at": now.isoformat()})\
        .eq("id", sid).eq("org_id", org_id).execute()
    _store_login_shot(client, sid, org_id, res.get("screenshot_b64"))


@router.post("/data-sources/{sid}/login/start")
async def data_source_login_start(sid: str, background_tasks: BackgroundTasks, org_id: str = ORG_ID):
    """Phase 1 of the interactive portal login. The Playwright login (slow through a residential proxy)
    runs in the BACKGROUND, so this returns instantly with auth_status='authenticating'; the UI then polls
    the row until it flips to needs_2fa / authenticated / error. This fixes the 'Failed to fetch' a
    synchronous, gateway-timeout-exceeding login used to throw."""
    require_org(org_id)
    client = sb()
    rows = (client.schema("commcalc").table("data_source").select("*")
            .eq("id", sid).eq("org_id", org_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "unknown data source")
    s = rows[0]
    if not (s.get("password") and (s.get("username") or s.get("account_id"))):
        raise HTTPException(400, "Enter the Account ID, User ID and Password on this login first, then Log in.")
    now = datetime.now(timezone.utc)
    client.schema("commcalc").table("data_source").update(
        {"auth_status": "authenticating", "auth_message": "Logging in…",
         "pending_started_at": now.isoformat(), "last_run_at": now.isoformat()})\
        .eq("id", sid).eq("org_id", org_id).execute()
    background_tasks.add_task(_do_portal_login, sid, org_id)
    return {"ok": True, "status": "authenticating",
            "message": "Logging in — this can take up to a minute (Chromium + your proxy). "
                       "Watch the status; the 2FA prompt appears here when it's ready."}


@router.post("/data-sources/{sid}/login/verify")
async def data_source_login_verify(sid: str, body: dict, org_id: str = ORG_ID):
    """Phase 2: submit the 2FA code against the pending session and, on success, store the durable
    authenticated session so scheduled/manual pulls reuse it until the portal invalidates it."""
    require_org(org_id)
    from app.modules.commcalc import vidapay_sweep as vp
    from fastapi.concurrency import run_in_threadpool
    code = str((body or {}).get("code") or "").strip()
    if not code:
        raise HTTPException(400, "Enter the verification code.")
    client = sb()
    rows = (client.schema("commcalc").table("data_source").select("*")
            .eq("id", sid).eq("org_id", org_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "unknown data source")
    s = rows[0]
    if not s.get("pending_state"):
        raise HTTPException(400, "No login in progress — click Log in again to request a new code.")
    started = s.get("pending_started_at")
    if started:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            if age > timedelta(minutes=vp.PENDING_TTL_MINUTES):
                raise HTTPException(400, "This login attempt expired — click Log in again for a fresh code.")
        except HTTPException:
            raise
        except Exception:
            pass
    _verify_fn = vp.complete_2fa_b2bsoft if (s.get("processor") or "").lower() in ("b2bsoft", "b2b") else vp.complete_2fa
    try:
        res = await run_in_threadpool(_verify_fn, s.get("portal_url"), s.get("pending_state"),
                                      code, s.get("proxy_url"))
    except vp.VidaPayAuthError as e:
        client.schema("commcalc").table("data_source").update(
            {"auth_status": "needs_2fa", "auth_message": str(e)[:400]})\
            .eq("id", sid).eq("org_id", org_id).execute()
        _store_login_shot(client, sid, org_id, getattr(e, "screenshot_b64", None))
        raise HTTPException(400, str(e))
    now = datetime.now(timezone.utc)
    client.schema("commcalc").table("data_source").update(
        {"auth_status": "authenticated", "auth_message": "Signed in — session saved.",
         "session_state": res.get("storage_state"), "pending_state": None, "pending_started_at": None,
         "session_expires_at": (now + timedelta(hours=vp.SESSION_TTL_HOURS)).isoformat(),
         "last_run_at": now.isoformat()})\
        .eq("id", sid).eq("org_id", org_id).execute()
    _store_login_shot(client, sid, org_id, res.get("screenshot_b64"))
    return {"ok": True, "status": "authenticated",
            "message": "Signed in — the session is saved and will be reused until it expires."}


@router.get("/data-sources/{sid}/login/screenshot")
def data_source_login_screenshot(sid: str, org_id: str = ORG_ID):
    """The JPEG (base64 data-URI) of the LAST page the headless login browser saw for this source —
    the 2FA challenge, a bot-wall, or the portal's error. This is the visual debugging channel for
    portal logins: the operator sees the actual screen instead of guessing from text diagnostics.
    Served separately from /data-sources because the list is polled every 3s during a login."""
    require_org(org_id)
    try:
        rows = (sb().schema("commcalc").table("data_source").select("id,login_shot,login_shot_at")
                .eq("id", sid).eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return {"ready": False, "shot": None,
                "note": "Run migration 203_commission_login_screenshot.sql to enable login screenshots."}
    if not rows:
        raise HTTPException(404, "unknown data source")
    r = rows[0]
    if not r.get("login_shot"):
        return {"ready": True, "shot": None,
                "note": "No screenshot captured yet — click 🔐 Log in first (screenshots are taken on every attempt)."}
    return {"ready": True, "shot": "data:image/jpeg;base64," + r["login_shot"], "at": r.get("login_shot_at")}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# LIVE PORTAL LOGIN — one persistent browser from login through code entry, so VidaPay/T-CETRA's
# new-device 2FA code is sent ONCE and the operator's code goes into the SAME live page (no re-navigate,
# no resend). See commcalc/live_login.py for the worker-thread + command-queue design. The screenshot
# stream is served by /live-login/state (polled ~1s), NOT by the data-sources list. OPERATOR CAVEAT:
# the session lives in ONE worker process's memory → the backend must run a SINGLE uvicorn worker
# (Railway FastAPI default) or start + submit could hit different workers. Fallback is login/start+verify.
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _live_persist(client, sid, org_id):
    """A persist callback the live session calls (from its worker thread) to store the durable session
    once authenticated. Org-scoped write; best-effort so a DB hiccup can't crash the login thread."""
    def _p(updates):
        try:
            client.schema("commcalc").table("data_source").update(updates)\
                .eq("id", sid).eq("org_id", org_id).execute()
        except Exception:
            pass
    return _p


def _live_persist_shot(client, sid, org_id):
    """A shot-persist callback the live session calls (from its worker thread) at EVERY stop to write its
    LAST live frame into the SAME data_source.login_shot store the begin_login/complete_2fa failures use,
    so '📷 What the browser saw' shows THIS live session's final screen (proxy_error / auth failure /
    idle timeout / operator Close) instead of a stale earlier attempt's frame. Best-effort — reuses the
    exact _store_login_shot path/shape (login_shot + login_shot_at); a missing column can't break login."""
    def _p(shot):
        _store_login_shot(client, sid, org_id, shot)
    return _p


def _live_pull(client, org_id, src_row):
    """Build the pull_fn the live session runs on its OWN authenticated page (reused, NOT a cold
    storage_state restore — T-CETRA re-challenges a fresh browser/egress/server session). Closes over
    the supabase client + org + this source row (id / carrier_id / months_back)."""
    from app.modules.commcalc import vidapay_sweep as vp
    sid = src_row.get("id")
    carrier_id = src_row.get("carrier_id")
    try:
        mb = int(src_row.get("months_back") or 2)
    except Exception:
        mb = 2

    def _p(page):
        return vp._pull_all_reports_on_page(page, client, org_id, sid, carrier_id, mb, dict(src_row or {}))
    return _p


def _live_source_row(client, sid, org_id):
    rows = (client.schema("commcalc").table("data_source").select("*")
            .eq("id", sid).eq("org_id", org_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "unknown data source")
    return rows[0]


@router.post("/data-sources/{sid}/live-login/start")
def live_login_start(sid: str, org_id: str = ORG_ID):
    """Spawn (or replace) the persistent live-login session for this source. Non-blocking — a worker
    thread drives the login to the 2FA code screen; the UI polls /live-login/state (~1s) to watch it."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    client = sb()
    s = _live_source_row(client, sid, org_id)
    if not (s.get("password") and (s.get("username") or s.get("account_id"))):
        raise HTTPException(400, "Enter the Account ID, User ID and Password on this login first, then start the live login.")
    now = datetime.now(timezone.utc)
    try:
        client.schema("commcalc").table("data_source").update(
            {"auth_status": "authenticating", "auth_message": "Live login in progress…",
             "pending_started_at": now.isoformat(), "last_run_at": now.isoformat()})\
            .eq("id", sid).eq("org_id", org_id).execute()
    except Exception:
        pass
    sess = live_login.start_session(sid, org_id, s, _live_persist(client, sid, org_id),
                                    _live_persist_shot(client, sid, org_id),
                                    _live_pull(client, org_id, s))
    return {"ok": True, "phase": sess.snapshot_phase(),
            "message": "Live session starting — watch it below. The 2FA code is sent ONCE to this same "
                       "live browser; enter it here when the code box appears."}


@router.get("/data-sources/{sid}/live-login/state")
def live_login_state(sid: str, org_id: str = ORG_ID):
    """The live session's phase + human message + the LATEST screenshot (data-uri JPEG). Polled ~1s by
    the UI. Screenshot is served ONLY here (never in the polled data-sources list)."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    sess = live_login.get_session(sid, org_id)
    if not sess:
        return {"phase": "idle", "message": "No live session running — click 🔴 Live login to start one.",
                "shot": None, "updated_at": None}
    return sess.state()


@router.post("/data-sources/{sid}/live-login/submit")
def live_login_submit(sid: str, body: dict, org_id: str = ORG_ID):
    """Enqueue the operator's 2FA code into the LIVE session. The worker thread fills it into the same
    open page, selects the trust radio and clicks Verify — the UI polls /state for the outcome."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    code = str((body or {}).get("code") or "").strip()
    if not code:
        raise HTTPException(400, "Enter the verification code.")
    sess = live_login.get_session(sid, org_id)
    if not sess:
        raise HTTPException(400, "No live session running — click 🔴 Live login to start one.")
    sess.submit(code)
    return {"ok": True, "phase": sess.snapshot_phase(), "message": "Submitting the code to the live session…"}


@router.post("/data-sources/{sid}/live-login/resend")
def live_login_resend(sid: str, org_id: str = ORG_ID):
    """Click the LIVE page's resend / send-code control — no re-login, no re-navigation."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    sess = live_login.get_session(sid, org_id)
    if not sess:
        raise HTTPException(400, "No live session running — click 🔴 Live login to start one.")
    sess.resend()
    return {"ok": True, "phase": sess.snapshot_phase()}


@router.post("/data-sources/{sid}/live-login/click")
def live_login_click(sid: str, body: dict, org_id: str = ORG_ID):
    """'Take control': forward an operator click (NORMALIZED x/y in 0..1 of the streamed image) to the
    live page, so they can press a control the auto-clicker missed (e.g. the portal's Next button)."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    sess = live_login.get_session(sid, org_id)
    if not sess:
        raise HTTPException(400, "No live session running — click 🔴 Live login to start one.")
    try:
        nx = float((body or {}).get("x")); ny = float((body or {}).get("y"))
    except (TypeError, ValueError):
        raise HTTPException(400, "click needs numeric x,y in 0..1")
    sess.click(nx, ny)
    return {"ok": True, "phase": sess.snapshot_phase()}


@router.post("/data-sources/{sid}/live-login/input")
def live_login_input(sid: str, body: dict, org_id: str = ORG_ID):
    """Forward a raw human input event to the LIVE page with HIGH priority (drained before SUBMIT_CODE /
    RESEND / PULL). type ∈ click|dblclick|type|key|scroll. Click coords are NORMALIZED (0..1 of the
    streamed image) and multiplied by the live viewport size server-side (DPR-proof — the img is rendered
    smaller than the real viewport). The first human input pauses auto-driving for the rest of pre-auth."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    sess = live_login.get_session(sid, org_id)
    if not sess:
        raise HTTPException(400, "No live session running — click 🔴 Live login to start one.")
    ev = body or {}
    et = str(ev.get("type") or "").strip().lower()
    if et not in ("click", "dblclick", "type", "key", "scroll"):
        raise HTTPException(400, "input type must be one of click|dblclick|type|key|scroll")
    norm = {"type": et}
    if et in ("click", "dblclick"):
        try:
            norm["x"] = float(ev.get("x")); norm["y"] = float(ev.get("y"))
        except (TypeError, ValueError):
            raise HTTPException(400, "click needs numeric x,y in 0..1")
    elif et == "type":
        norm["text"] = str(ev.get("text") or "")
    elif et == "key":
        norm["key"] = str(ev.get("key") or "")
    elif et == "scroll":
        try:
            norm["deltaY"] = float(ev.get("deltaY") or 0)
        except (TypeError, ValueError):
            norm["deltaY"] = 0.0
    sess.input_event(norm)
    return {"ok": True, "phase": sess.snapshot_phase()}


@router.get("/data-sources/{sid}/live-login/frame")
def live_login_frame(sid: str, since: int = 0, org_id: str = ORG_ID):
    """Lightweight frame poll for the low-latency live view. Returns the newest JPEG (as a data-uri) ONLY
    when `_seq` advanced past `since`, else a tiny unchanged payload — so the panel can poll this ~250-400ms
    while the modal is open without shipping a frame every time. phase + message are always included so the
    status line stays fresh. No live session → an idle payload (the panel stops polling)."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    sess = live_login.get_session(sid, org_id)
    if not sess:
        return {"seq": 0, "phase": "idle", "message": None, "changed": False, "shot": None}
    return sess.frame_since(since)


@router.post("/data-sources/{sid}/live-login/cancel")
def live_login_cancel(sid: str, org_id: str = ORG_ID):
    """Cancel + close the live session (frees the headless browser)."""
    require_org(org_id)
    from app.modules.commcalc import live_login
    sess = live_login.get_session(sid, org_id)
    if sess:
        sess.cancel()
    return {"ok": True}


_MA_COMPONENTS = ["device_margin", "consumer_margin", "consumer_financing", "rebate",
                  "wallet_funding", "fees_margin",
                  "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]


def _read_ma(client, org_id, table, period, cols):
    out, start, page = [], 0, 1000
    while True:
        q = client.schema("commcalc").table(table).select(cols).eq("org_id", org_id)
        if period:
            q = q.in_("period", _pvariants(period))
        rows = q.range(start, start + page - 1).execute().data or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


@router.get("/ma-commission/summary")
def ma_commission_summary(period: str = "", stores: str = "", reps: str = "", org_id: str = ORG_ID):
    """The Total-processor commission roll-up (raw_ma_commission + raw_ma_daily_tx, mig 083).
    Sign convention on the Commission Details export: NEGATIVE = paid TO the dealer, so payable
    figures here are sign-FLIPPED (positive = money the dealer receives). Org-scoped — computes
    against whatever org the MA reports were uploaded into. Read-only.

    RULE FIVE (§3d): optional `stores` / `reps` (comma-separated account-id / processor-login values)
    narrow the WHOLE roll-up — tiles, tables and export alike (WYSIWYG §3c) — server-side, so the
    aggregates stay correct under a filter. Both blank = the unfiltered report (backward-compatible).
    `store_options` / `rep_options` are computed from the UNFILTERED rows so the picker stays stable.
    There is no `market` dimension here: this is processor account-keyed data with no store_mapping
    linkage (documented deviation)."""
    require_org(org_id)
    client = sb()
    try:
        comm = _read_ma(client, org_id, "raw_ma_commission", period.strip(),
                        "tx_date,period,merchant_account_id,user_name,platform,activation_type,"
                        "activation_type2,sub_type,mrc_net_discount," + ",".join(_MA_COMPONENTS))
    except Exception:
        return {"ready": False, "note": "Run migration 083_total_processor_sources.sql, then upload the "
                                        "MA reports on Data Imports (or add mailbox rules)."}
    try:
        tx = _read_ma(client, org_id, "raw_ma_daily_tx", period.strip(),
                      "tx_date,period,account_id,account_name,retail_cost,merchant_discount")
    except Exception:
        tx = []

    # Stable pick-don't-type option lists from the UNFILTERED rows (before narrowing).
    _store_names: dict = {}
    for r in comm:
        k = (r.get("merchant_account_id") or "").strip()
        if k:
            _store_names.setdefault(k, None)
    for r in tx:
        k = (r.get("account_id") or "").strip()
        if k:
            nm = (r.get("account_name") or "").strip()
            if nm:
                _store_names[k] = nm
            else:
                _store_names.setdefault(k, None)
    store_options = sorted(({"id": k, "label": (v or k)} for k, v in _store_names.items()),
                           key=lambda o: str(o["label"]).lower())
    rep_options = sorted({(r.get("user_name") or "").strip() for r in comm if (r.get("user_name") or "").strip()})
    # Optional server-side narrowing (blank = no filter).
    store_sel = {s.strip() for s in (stores or "").split(",") if s.strip()}
    rep_sel = {s.strip() for s in (reps or "").split(",") if s.strip()}
    if store_sel:
        comm = [r for r in comm if (r.get("merchant_account_id") or "").strip() in store_sel]
        tx = [r for r in tx if (r.get("account_id") or "").strip() in store_sel]
    if rep_sel:
        comm = [r for r in comm if (r.get("user_name") or "").strip() in rep_sel]

    comps = {k: 0.0 for k in _MA_COMPONENTS}
    acts = {"total": 0, "new": 0, "add": 0, "branded": 0, "byop": 0}
    by_store, by_rep, by_platform = {}, {}, {}
    dates = [r.get("tx_date") for r in comm if r.get("tx_date")]
    for r in comm:
        pay = -sum(safe_float(r.get(k)) for k in _MA_COMPONENTS)   # flip: positive = dealer receives
        spiffs = -sum(safe_float(r.get(f"spiff_m{i}")) for i in range(1, 7))
        for k in _MA_COMPONENTS:
            comps[k] += safe_float(r.get(k))
        acts["total"] += 1
        at = (r.get("activation_type") or "").strip().lower()
        at2 = (r.get("activation_type2") or "").strip().lower()
        if at == "new":
            acts["new"] += 1
        elif at == "add":
            acts["add"] += 1
        if at2 in ("branded", "byop"):
            acts[at2] += 1
        sk = (r.get("merchant_account_id") or "?").strip() or "?"
        st = by_store.setdefault(sk, {"account_id": sk, "activations": 0, "payable": 0.0,
                                      "spiffs": 0.0, "rebates": 0.0, "airtime_margin": 0.0, "name": None})
        st["activations"] += 1
        st["payable"] += pay
        st["spiffs"] += spiffs
        st["rebates"] += -safe_float(r.get("rebate"))
        rk = (r.get("user_name") or "?").strip() or "?"
        rp = by_rep.setdefault(rk, {"rep": rk, "activations": 0, "payable": 0.0, "spiffs": 0.0,
                                    "rebates": 0.0, "avg_mrc": 0.0, "_mrc_n": 0, "_mrc_sum": 0.0})
        rp["activations"] += 1
        rp["payable"] += pay
        rp["spiffs"] += spiffs
        rp["rebates"] += -safe_float(r.get("rebate"))
        mrc = safe_float(r.get("mrc_net_discount"))
        if mrc > 0:
            rp["_mrc_n"] += 1
            rp["_mrc_sum"] += mrc
        pk = (r.get("platform") or "?").strip() or "?"
        pl = by_platform.setdefault(pk, {"platform": pk, "activations": 0, "payable": 0.0})
        pl["activations"] += 1
        pl["payable"] += pay

    airtime = {"orders": len(tx), "retail": 0.0, "margin": 0.0}
    for r in tx:
        airtime["retail"] += safe_float(r.get("retail_cost"))
        margin = safe_float(r.get("merchant_discount"))
        airtime["margin"] += margin
        sk = (r.get("account_id") or "?").strip() or "?"
        st = by_store.setdefault(sk, {"account_id": sk, "activations": 0, "payable": 0.0,
                                      "spiffs": 0.0, "rebates": 0.0, "airtime_margin": 0.0, "name": None})
        st["airtime_margin"] += margin
        if r.get("account_name") and not st.get("name"):
            st["name"] = r.get("account_name")
        dates.append(r.get("tx_date"))

    for rp in by_rep.values():
        rp["avg_mrc"] = round(rp.pop("_mrc_sum") / rp["_mrc_n"], 2) if rp["_mrc_n"] else None
        rp.pop("_mrc_n", None)
    rnd = lambda d: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}
    total_payable = -sum(comps.values())
    spiff_by_month = {f"m{i}": round(-comps[f"spiff_m{i}"], 2) for i in range(1, 7)}
    dates = [d for d in dates if d]
    return {"ready": True, "period": period or "all",
            "rows": len(comm), "date_range": ([min(dates), max(dates)] if dates else None),
            "store_options": store_options, "rep_options": rep_options,
            "activations": acts,
            "total_payable": round(total_payable, 2),
            "components": {"device_margin": round(-comps["device_margin"], 2),
                           "consumer_margin": round(-comps["consumer_margin"], 2),
                           "consumer_financing": round(-comps["consumer_financing"], 2),
                           "rebates": round(-comps["rebate"], 2),
                           "wallet_funding": round(-comps["wallet_funding"], 2),
                           "fees_margin": round(-comps["fees_margin"], 2),
                           "spiffs_total": round(-sum(comps[f"spiff_m{i}"] for i in range(1, 7)), 2)},
            "spiff_by_month": spiff_by_month,
            "airtime": rnd(airtime),
            "by_store": sorted((rnd(s) for s in by_store.values()),
                               key=lambda s: -(s["payable"] + s["airtime_margin"])),
            "by_rep": sorted((rnd(r) for r in by_rep.values()), key=lambda r: -r["payable"]),
            "by_platform": sorted((rnd(p) for p in by_platform.values()), key=lambda p: -p["payable"]),
            "note": None if comm or tx else
            "No MA rows for this period yet — upload the MA reports on Data Imports (no period needed) "
            "or add mailbox rules (*Commission*Details* → MA Commission Details)."}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# CUSTOM REPORT BUILDER (mig 211) — the config-driven, universal report over EVERY commcalc dataset.
# The dataset REGISTRY + all aggregation math live in commcalc.custom_report (pure, unit-tested). Here we
# bind each dataset key to a RESOLVER that REUSES the module's existing read functions (never duplicating
# query logic) and returns NORMALIZED rows: dicts keyed by the dataset's column-catalog fields PLUS the
# field_map dims (store/rep/market/day) so the RULE FIVE filter + group-by can operate universally.
# Everything degrades gracefully: mig 211 absent → code-default registry; a backing table absent →
# "dataset unavailable" (never a 500); mig-210 category columns absent → hidden.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _cr_market_resolver(client, org_id):
    """A `(_market_for, all_markets)` pair over store_mapping — the SAME address / store_code /
    leading-number resolution the Sales Report uses — so any dataset with a store string can carry a
    `market` for RULE FIVE market filtering + group-by. Never raises (a store_mapping read error → every
    market blank)."""
    import re as _re_cr
    try:
        sm = (client.schema("commcalc").table("store_mapping")
              .select("store_code,store_address,market").eq("org_id", org_id).execute().data) or []
    except Exception:
        sm = []
    by_code, by_addr, by_num, markets = {}, {}, {}, set()

    def _lead(s):
        m = _re_cr.match(r"\s*(\d+)", str(s or "")); return m.group(1) if m else ""

    for s in sm:
        mk = (s.get("market") or "").strip()
        if not mk:
            continue
        markets.add(mk)
        code = str(s.get("store_code") or "").strip()
        addr = str(s.get("store_address") or "").strip()
        if code:
            by_code[code] = mk
        if addr:
            by_addr[addr.lower()] = mk
        n = _lead(addr)
        if n:
            by_num.setdefault(n, mk)

    def _market_for(store):
        st = str(store or "").strip()
        return by_addr.get(st.lower()) or by_code.get(st) or by_num.get(_lead(st)) or ""

    return _market_for, sorted(markets)


def _cr_guarded(fn):
    """Run a resolver read; return (rows, True) on success or ([], False) when the backing table is absent
    / the read errors — so a section degrades to 'dataset unavailable' instead of 500ing the whole report."""
    try:
        return fn() or [], True
    except Exception as e:
        print(f"WARN custom_report resolver failed: {e}")
        return [], False


def _cr_resolve_sales_line(client, org_id, period, ctx):
    """Sales LINE grain — REUSES `_sales_rows_union` (the canonical feed∪raw_sales display source). Voided
    / Return lines are dropped so money totals match the Sales Report. Market is attached for RULE FIVE.
    mig-210 categories interface (loose coupling): if the underlying rows ever carry master_category /
    kpi_category columns they flow through untouched; absent (today) → simply not present, hidden silently."""
    market_for = ctx["market_for"]
    rows, _meta = _sales_rows_union(client, org_id, period)
    out = []
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        store = (r.get("store") or "").strip()
        row = {
            "store": store, "market": market_for(store),
            "salesperson": (r.get("salesperson") or "").strip(),
            "trans_date": str(r.get("trans_date") or "")[:10],
            "trans_id": r.get("trans_id"), "department": r.get("department"),
            "category": r.get("category"), "contract_type": r.get("contract_type"),
            "product_desc": r.get("product_desc"),
            "ext_price": safe_float(r.get("ext_price")), "gp": safe_float(r.get("gp")),
        }
        # mig-210 dual-category mapping (commcalc.item_mapping.sales_category / kpi_category, joined by
        # item_key). Degrades silently when absent; surfaces automatically once the join is wired.
        for extra in ("sales_category", "kpi_category"):
            if r.get(extra) not in (None, ""):
                row[extra] = r.get(extra)
        out.append(row)
    return out


def _cr_resolve_rep_commissions(client, org_id, period, ctx):
    """rep_commissions snapshot + chargeback deductions — the SAME read `get_commissions` serves (final
    payout = total_payout − deducted chargebacks). Market attached from the rep's store."""
    market_for = ctx["market_for"]
    rows = (client.schema("commcalc").table("rep_commissions").select("*")
            .eq("org_id", org_id).in_("period", _pvariants(period)).order("total_payout", desc=True)
            .execute().data) or []
    cb = (client.schema("commcalc").table("chargeback_items").select("epay_salesperson,amount,deduct")
          .eq("org_id", org_id).in_("period", _pvariants(period)).execute().data) or []
    ded = {}
    for it in cb:
        if it.get("deduct"):
            rep = it.get("epay_salesperson") or ""
            ded[rep] = ded.get(rep, 0) + safe_float(it.get("amount"))
    out = []
    for cr in rows:
        row = dict(cr)
        row["market"] = market_for(cr.get("store"))
        d = ded.get(cr.get("epay_salesperson") or "", 0)
        row["chargeback_deduction"] = d
        row["final_payout"] = safe_float(cr.get("total_payout")) - safe_float(d)
        out.append(row)
    return out


def _cr_resolve_targets_actuals(client, org_id, period, ctx):
    """Targets ACHIEVED actuals — REUSES `_compute_feed_actuals_py` (per store×rep×day, from the unified
    sales set). Market attached."""
    market_for = ctx["market_for"]
    rows = _compute_feed_actuals_py(client, org_id, period)
    for r in rows:
        r["market"] = market_for(r.get("store") or r.get("store_code"))
    return rows


def _cr_resolve_kpi_metrics(client, org_id, period, ctx):
    """Store KPI metrics — the SAME raw_dlar_store read `get_dlar_store_kpis` serves."""
    market_for = ctx["market_for"]
    mo, yr = _month_year(period)
    q = (client.schema("commcalc").table("raw_dlar_store")
         .select("location,address,store_code,atu,protect_pct,byod_pct,family_plan_pct,tmr3,"
                 "aal_conversion,conversion_rate,total_acts,gross_adds,total_upgrades")
         .eq("org_id", org_id))
    q = q.eq("period_month", mo).eq("period_year", yr) if mo and yr else q.in_("period", _pvariants(period))
    rows = q.order("location").execute().data or []
    for r in rows:
        r["market"] = market_for(r.get("location") or r.get("address") or r.get("store_code"))
    return rows


def _cr_resolve_store_expenses(client, org_id, period, ctx):
    """Store expenses — the SAME store_expenses read `get_expenses` serves (period-scoped)."""
    market_for = ctx["market_for"]
    rows = (client.schema("commcalc").table("store_expenses").select("*")
            .eq("org_id", org_id).in_("period", _pvariants(period)).order("store_code").execute().data) or []
    for r in rows:
        r["market"] = market_for(r.get("store_code"))
    return rows


def _cr_resolve_chargebacks(client, org_id, period, ctx):
    """Employee chargeback_items — the assigned chargebacks that deduct from rep pay."""
    market_for = ctx["market_for"]
    rows = (client.schema("commcalc").table("chargeback_items").select("*")
            .eq("org_id", org_id).in_("period", _pvariants(period)).order("epay_salesperson").execute().data) or []
    for r in rows:
        r["market"] = market_for(r.get("store"))
        r["deduct"] = "Yes" if r.get("deduct") else "No"
    return rows


def _cr_resolve_flags(client, org_id, period, ctx):
    """commcalc.flags — the SAME read `get_flags` serves."""
    market_for = ctx["market_for"]
    rows = (client.schema("commcalc").table("flags").select("*")
            .eq("org_id", org_id).in_("period", _pvariants(period)).order("severity").execute().data) or []
    for r in rows:
        r["market"] = market_for(r.get("store_address"))
    return rows


def _cr_resolve_ma_commission(client, org_id, period, ctx):
    """raw_ma_commission (M1-M6 spiffs + rebate per activated phone) — the same table the What-If
    carrier-income / BYOD-residual views read. The money columns are carrier-income → gated downstream."""
    q = (client.schema("commcalc").table("raw_ma_commission")
         .select("period,activation_type2,imei,ban,spiff_m1,spiff_m2,spiff_m3,spiff_m4,spiff_m5,spiff_m6,rebate")
         .eq("org_id", org_id))
    if period:
        q = q.in_("period", _pvariants(period))
    return q.limit(100000).execute().data or []


def _cr_resolve_ma_daily_tx(client, org_id, period, ctx):
    """raw_ma_daily_tx (incl. the Postpaid Residual Order rows) — the same table the What-If reads. Money
    columns are carrier-income → gated downstream."""
    q = (client.schema("commcalc").table("raw_ma_daily_tx")
         .select("period,order_type,account_id,order_number,merchant_invoice,merchant_discount,retail_cost")
         .eq("org_id", org_id))
    if period:
        q = q.in_("period", _pvariants(period))
    return q.limit(100000).execute().data or []


_CUSTOM_REPORT_RESOLVERS = {
    "sales_line": _cr_resolve_sales_line,
    "rep_commissions": _cr_resolve_rep_commissions,
    "targets_actuals": _cr_resolve_targets_actuals,
    "kpi_metrics": _cr_resolve_kpi_metrics,
    "store_expenses": _cr_resolve_store_expenses,
    "chargebacks": _cr_resolve_chargebacks,
    "flags": _cr_resolve_flags,
    "ma_commission": _cr_resolve_ma_commission,
    "ma_daily_tx": _cr_resolve_ma_daily_tx,
}


def _cr_registry(client, org_id):
    """(resolved_registry, config_present). Merges the code-default DATASETS with the mig-211 registry rows
    (HOUSE ∪ this org). Degrades to code defaults when the table is absent (config_present=False)."""
    try:
        rows = (client.schema("commcalc").table("custom_report_dataset").select("*")
                .in_("org_id", [custom_report.HOUSE_ORG, org_id]).execute().data) or []
        present = len(rows) > 0
    except Exception:
        rows, present = [], False
    return custom_report.resolve_registry(rows), present


def _cr_grants(authorization, org_id):
    """The permission-gate keys the caller holds, for per-column gating. Today: 'carrier_residual' when
    `_can_view_carrier_residual` passes (MA carrier-income money). A gate the caller lacks → that column is
    dropped from the metadata AND the rows (RULE FOUR: never leaks through an export)."""
    g = set()
    try:
        if _can_view_carrier_residual(authorization, org_id):
            g.add("carrier_residual")
    except Exception:
        pass
    return g


@router.get("/custom-report/datasets")
def custom_report_datasets(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The resolved dataset registry for this org — drives the Custom Report's dataset multi-select +
    column picker + group-by picker. Columns the caller may not see (gated) are already omitted."""
    require_org(org_id)
    client = sb()
    grants = _cr_grants(authorization, org_id)
    reg, present = _cr_registry(client, org_id)
    out = []
    for d in reg:
        cols = custom_report.visible_columns(d, grants)
        fm_vals = set(v for v in d["field_map"].values() if v)
        universal = [dim for dim in ("store", "rep", "market", "day") if d["field_map"].get(dim)]
        group_extra = [c["field"] for c in cols if c.get("group") and c["field"] not in fm_vals]
        out.append({
            "key": d["key"], "name": d["name"], "sort_order": d.get("sort_order"),
            "backing_tables": d.get("backing_tables"),
            "columns": [{"field": c["field"], "label": c["label"], "type": c["type"],
                         "numeric": custom_report.is_numeric(c), "money": c["type"] == "money",
                         "group": bool(c.get("group"))} for c in cols],
            "group_dims": universal + group_extra,
            "has_gated_hidden": any(c.get("gate") and c["gate"] not in grants for c in d["columns"]),
        })
    return {"datasets": out, "org_id": org_id, "grants": sorted(grants),
            "registry_source": "config" if present else "code-default"}


@router.get("/custom-report")
async def custom_report_run(datasets: str = "", period: str = "", date_from: str = "", date_to: str = "",
                            stores: str = "", markets: str = "", reps: str = "",
                            group_by: str = "", columns: str = "",
                            authorization: str = Header(default=""), org_id: str = ORG_ID):
    """THE universal Custom Report. Params: `datasets` (comma-sep registry keys), `period` (or
    `date_from`/`date_to`), the RULE FIVE core filters `stores`/`markets`/`reps` (comma-sep selected
    values, applied SERVER-SIDE before aggregation), optional `group_by` (a universal dim store/rep/market/
    day OR a groupable column field), optional `columns` (comma-sep field names to keep). Returns one
    SECTION per dataset (honest side-by-side; NO cross-dataset joins in v1) with rows + column metadata +
    totals + availability, plus the pick-don't-type filter options unioned across datasets. Multi-tenant:
    org-scoped reads, span-scope honored, gated columns dropped for a caller without the grant."""
    require_org(org_id)
    client = sb()
    if not period and not (date_from or date_to):
        n = datetime.now(timezone.utc)
        period = f"{n.year}-{n.month:02d}"
    reg, reg_present = _cr_registry(client, org_id)
    reg_by_key = {d["key"]: d for d in reg}
    grants = _cr_grants(authorization, org_id)
    market_for, sm_markets = _cr_market_resolver(client, org_id)
    ctx = {"market_for": market_for}

    want_keys = [k.strip() for k in (datasets or "").split(",") if k.strip()] or ([reg[0]["key"]] if reg else [])
    sel_stores = [s for s in (stores or "").split(",") if s.strip()]
    sel_markets = [s for s in (markets or "").split(",") if s.strip()]
    sel_reps = [s for s in (reps or "").split(",") if s.strip()]
    want_cols = [c.strip() for c in (columns or "").split(",") if c.strip()]

    try:
        from app.modules.storeops.router import scope_keyset, in_keyset
        ks = scope_keyset(authorization, org_id)   # None = unrestricted
    except Exception:
        ks, in_keyset = None, None

    sections, opt_src = [], []
    for key in want_keys:
        d = reg_by_key.get(key)
        if not d:
            sections.append({"key": key, "name": key, "available": False,
                             "reason": "unknown or disabled dataset", "columns": [], "rows": [], "totals": {}})
            continue
        resolver = _CUSTOM_REPORT_RESOLVERS.get(d["resolver"])
        if not resolver:
            sections.append({"key": key, "name": d["name"], "available": False,
                             "reason": "no resolver bound", "columns": [], "rows": [], "totals": {}})
            continue
        raw, available = _cr_guarded(lambda r=resolver: r(client, org_id, period, ctx))
        if not available:
            tbls = ", ".join(d.get("backing_tables") or [])
            sections.append({"key": key, "name": d["name"], "available": False,
                             "reason": f"dataset unavailable — backing table(s) {tbls} not present for this tenant",
                             "columns": [], "rows": [], "totals": {}})
            continue
        # Span-scope filter on the dataset's store field (when it has one) — a store-scoped user never sees
        # out-of-scope rows, matching every other report.
        f_store = d["field_map"].get("store")
        if ks is not None and f_store and in_keyset:
            raw = [r for r in raw if in_keyset(ks, r.get(f_store))]
        opt_src.append((d, raw))   # pick-don't-type options come from PRE-RULE-FIVE (post-scope) rows
        # mig-210 categories interface: expose master/kpi category columns WHEN the rows carry them
        # (loose coupling — hidden silently until mig 210 populates them).
        d = custom_report.augment_columns(d, raw)
        # RULE FIVE server-side filter BEFORE aggregation.
        filt = custom_report.filter_rows(raw, d["field_map"], sel_stores, sel_markets, sel_reps,
                                          day_from=(date_from or None), day_to=(date_to or None))
        allvis = custom_report.visible_columns(d, grants)
        vis = custom_report.select_columns(allvis, want_cols)
        grp_field = custom_report.resolve_group_field(d, group_by)
        if grp_field and not any(c["field"] == grp_field for c in vis):
            # keep the group column present so its value labels the grouped rows
            gc = next((c for c in allvis if c["field"] == grp_field), None)
            if gc:
                vis = [gc] + vis
        rows_out, cols_out = custom_report.group_and_aggregate(filt, vis, grp_field)
        proj = custom_report.project_rows(rows_out, cols_out)   # drop any non-visible field before it ships
        totals = custom_report.compute_totals(proj, cols_out)
        sections.append({
            "key": key, "name": d["name"], "available": True, "grouped_by": grp_field or None,
            "columns": [{"field": c["field"], "label": c["label"], "type": c["type"],
                         "numeric": custom_report.is_numeric(c), "money": c["type"] == "money",
                         "agg": custom_report.col_agg(c)} for c in cols_out],
            "rows": proj, "totals": totals, "row_count": len(proj),
            "gated_columns_hidden": [c["label"] for c in d["columns"] if c.get("gate") and c["gate"] not in grants],
        })

    opts = custom_report.option_values(opt_src)
    opts["markets"] = sorted(set(opts["markets"]) | set(sm_markets))   # a market with no rows is still a valid pick
    return {"org_id": org_id, "period": period, "date_from": date_from or None, "date_to": date_to or None,
            "datasets": want_keys, "sections": sections, "filter_options": opts,
            "applied_filters": {"stores": sel_stores, "markets": sel_markets, "reps": sel_reps,
                                "group_by": group_by or None},
            "registry_source": "config" if reg_present else "code-default"}


@router.get("/custom-report/definitions")
def custom_report_defs_list(org_id: str = ORG_ID):
    """Saved Custom Report definitions for this org (the RULE THREE load-a-saved-report picker)."""
    require_org(org_id)
    try:
        rows = (sb().schema("commcalc").table("custom_report_def").select("*")
                .eq("org_id", org_id).order("name").execute().data) or []
    except Exception:
        rows = []   # mig 211 not run → no saved reports yet (page still works)
    return {"definitions": rows, "org_id": org_id}


@router.post("/custom-report/definitions")
def custom_report_defs_save(body: dict, org_id: str = ORG_ID):
    """Save (upsert on org_id+name) a named report configuration — that's what makes it a recallable
    'primary report'. org_id is STAMPED on the row (RULE ONE)."""
    require_org(org_id)
    client = sb()
    reg, _present = _cr_registry(client, org_id)
    known = {d["key"] for d in reg}
    ok, res = custom_report.validate_definition(body, known)
    if not ok:
        raise HTTPException(400, res)
    row = {"org_id": org_id, "name": res["name"], "config": res["config"],
           "created_by": (body.get("created_by") or None),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        client.schema("commcalc").table("custom_report_def").upsert(row, on_conflict="org_id,name").execute()
    except Exception as e:
        raise HTTPException(400, f"could not save (run migration 211?): {e}")
    return {"ok": True, "name": res["name"]}


@router.delete("/custom-report/definitions/{def_id}")
def custom_report_defs_delete(def_id: str, org_id: str = ORG_ID):
    """Delete a saved definition (org-scoped)."""
    require_org(org_id)
    try:
        sb().schema("commcalc").table("custom_report_def").delete().eq("org_id", org_id).eq("id", def_id).execute()
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
