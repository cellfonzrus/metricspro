"""CommCalc API Router — all /api/v1/commcalc/* endpoints"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks, Header
from fastapi.responses import JSONResponse
import pandas as pd
import io
from app.core.database import get_supabase
from app.modules.commcalc.calculator import calc_rep_commissions, parse_period, safe_float
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
from app.modules.commcalc import b2b_sweep
from app.modules.commcalc import sales_analyzer
from app.modules.commcalc import sales_recon
from app.modules.commcalc import comp_trend
from app.modules.commcalc import carrier_map
from app.modules.commcalc import column_mapping
from app.modules.commcalc import commission_catalog
from app.modules.commcalc import target_registry
from app.modules.commcalc import commission_ledger
from app.core.config import settings
from datetime import date as _date, timedelta as _timedelta, datetime as _datetime, timezone as _timezone
import calendar as _calendar


router = APIRouter(prefix="/commcalc", tags=["CommCalc"])

# ── Helper ───────────────────────────────────────────────────
ORG_ID = "00000000-0000-0000-0000-000000000001"


def sb():
    return get_supabase()

def require_org(org_id: str):
    if not org_id:
        raise HTTPException(400, "org_id required")

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


# ── Upload endpoints ─────────────────────────────────────────
@router.post("/upload/{file_type}")
async def upload_file(
    file_type: str,
    file: UploadFile = File(...),
    period: str = "",
    force: bool = False,
    org_id: str = "00000000-0000-0000-0000-000000000001"
):
    """Upload a data file (sales, payment_detail, mi, dlar_rep, dlar_store, catalog).

    For comp_report, the selected `period` is checked against the month the file's rows actually
    belong to (their Begin Date); a mismatch is rejected (pass force=true to override) so a file
    can't be mislabeled into the wrong month — the bug that wiped a month's residual trend."""
    require_org(org_id)
    
    SUPPORTED = ["sales","daily_sales","payment_detail","mi_report","dlar_rep","dlar_store","catalog","master_cats","comp_report","inventory_aging","x_report"]
    if file_type not in SUPPORTED:
        raise HTTPException(400, f"Unknown file type: {file_type}. Supported: {SUPPORTED}")
    
    contents = await file.read()
    fname = (getattr(file, "filename", "") or "").lower()
    try:
        if fname.endswith((".csv", ".txt")):
            df = pd.read_csv(io.BytesIO(contents), dtype=str)
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
        try:
            client.schema('commcalc').table('upload_log').insert(
                {'org_id': org_id, 'file_type': 'inventory_aging', 'period': as_of,
                 'filename': getattr(file, 'filename', None), 'rows_saved': saved}).execute()
        except Exception:
            pass
        return {'success': True, 'file_type': 'inventory_aging', 'stores': saved,
                'as_of': as_of, 'rows_read': len(rows),
                'note': (None if saved else "No per-store inventory value found — check the file has a store "
                         "column + a value/cost column (or map it on Column Mapping).")}

    # POS "X report": daily takings BY TENDER TYPE per store → commcalc.pos_tender_summary, for the tender
    # reconciliation against the daily closing sheet. Flexible column detection (any POS). Periodless.
    if file_type == 'x_report':
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
                'department': r.get('Department',''), 'category': r.get('Category',''),
                'product_desc': r.get('Product Desc',''), 'product_id': safe_float(r.get('Product ID')) or None,
                'gp': safe_float(r.get('GP')), 'ext_price': safe_float(r.get('Ext Price')),
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
        else:
            row = {**base}
        
        if any(v for v in row.values() if v and v != org_id):
            mapped.append(row)

    # GUARD: only NOW (rows successfully mapped) do we clear the existing data, and only if the
    # upload actually produced rows — so a file that parsed to nothing can never wipe a populated
    # period. catalog/master_cats replace the whole table.
    if mapped:
        if file_type == 'daily_sales':
            # The daily feed is keyed by day, not month. Make a re-pull of the same day(s) idempotent
            # by clearing only the trans_dates this file covers (never the whole month — other days'
            # feed rows survive). Rows with no parseable date can't be deduped, so they just append.
            feed_dates = sorted({m.get('trans_date') for m in mapped if m.get('trans_date')})
            if feed_dates:
                try:
                    client.schema('commcalc').table(table).delete()\
                        .eq('org_id', org_id).in_('trans_date', feed_dates).execute()
                except Exception as e:
                    raise HTTPException(500, f"Failed to clear existing daily feed: {e}. Run migration 047.")
        elif has_period and period:
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
    if file_type == 'daily_sales':
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

    return {"saved": saved, "file_type": file_type, "period": period, "fraud": fraud, "recon": recon}


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
            client.schema('commcalc').table(tbl).delete().neq('id', SENTINEL).execute()
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
    if not rules:
        raise HTTPException(400, f"No column mapping configured for '{report_key}'. Map its columns first (or seed defaults).")

    contents = await file.read()
    try:
        df = _read_upload_df(contents, getattr(file, "filename", ""))
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")

    pm = parse_period(period) if period else {"month": 0, "year": 0}
    base = {"org_id": org_id}
    if period:
        base.update({"period": period, "period_month": pm["month"], "period_year": pm["year"]})
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
    # GUARD: only clear the period once we actually have rows — an empty/misaligned file never wipes.
    if mapped and period:
        try:
            client.schema("commcalc").table(table).delete().eq("org_id", org_id).in_("period", _pvariants(period)).execute()
        except Exception as e:
            raise HTTPException(500, f"Failed to clear existing data for {table}/{period}: {e}")
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
            "rules_used": len(rules), "mapped": len(mapped)}


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


def _item_key(sku, desc):
    s = str(sku or "").strip()
    if s and s.lower() not in ("nan", "none", "0", "0.0"):
        return s.upper()[:200]
    return str(desc or "").strip().upper()[:200]


def _guess_item_type(department, category, desc):
    """Best-effort first-sight classification from raw_sales Department / Category / description."""
    blob = " ".join(str(x or "") for x in (department, category, desc)).upper()
    if any(h in blob for h in ACC_HINTS):
        return "accessory"
    if any(h in blob for h in PHONE_HINTS):
        return "phone"
    return "unclassified"


def _flag_rules(client, org_id):
    try:
        rows = client.schema("commcalc").table("flag_rules").select("*").eq("org_id", org_id).eq("id", 1).limit(1).execute().data or []
        if rows:
            return rows[0]
    except Exception:
        pass
    return {"accessory_threshold": 35, "accessory_chargeback_amount": 0, "accessory_min_threshold": 0}


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


@router.get("/item-mapping")
def get_item_mapping(search: str = None, item_type: str = None, org_id: str = ORG_ID):
    """The item → type + phone-model mapping (search by sku/desc/model; filter by type)."""
    require_org(org_id)
    try:
        q = sb().schema("commcalc").table("item_mapping").select("*").eq("org_id", org_id)
        if item_type:
            q = q.eq("item_type", item_type)
        rows = q.limit(100000).execute().data or []
    except Exception as e:
        return {"items": [], "ready": False, "detail": str(e)[:200], "counts": {}, "total": 0}
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
    if not item_type and not device_model:
        raise HTTPException(400, "Provide item_type and/or device_model to apply.")
    patch = {"source": "manual", "updated_at": _cb_now()}
    if item_type:
        patch["item_type"] = item_type
    if device_model:
        patch["device_model"] = device_model
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
                    org_id: str = ORG_ID):
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
        if "boost protect" in (r.get("product_desc") or "").lower():
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
    return {"rows": out, "threshold": threshold, "min_threshold": min_t, "default_chargeback": default_cb,
            "total": len(out), "flagged_qty": sum(1 for x in out if x["already_flagged"]),
            "summary": summary,
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
    org_id: str = "00000000-0000-0000-0000-000000000001"
):
    """Trigger commission calculation for a period"""
    require_org(org_id)
    
    client = sb()
    
    # Mark as pending
    try:
        client.schema('commcalc').table('calc_status').upsert({
            'org_id': org_id, 'period': period, 'calc_status': 'running'
        }, on_conflict='org_id,period').execute()
    except: pass
    
    background_tasks.add_task(_run_calculation, period, org_id)
    return {"status": "started", "period": period, "message": "Calculation running in background"}


def _apply_new_engines(client, org_id, period, comms):
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
        if not inst_by_rep and not plan_by_rep and not stmt_by_rep:
            return comms

        cols = {}
        for c in ("residual_installment_comm", "plan_comm", "plan_name", "carrier_statement_comm"):
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
            stmt = next((stmt_by_rep[k] for k in ks if k in stmt_by_rep), 0.0)
            pv = next((plan_by_rep[k] for k in ks if k in plan_by_rep), None)
            if cols["residual_installment_comm"]:
                row["residual_installment_comm"] = inst
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
            row["total_payout"] = round(base + inst, 2)               # plan + installments = rep pay

        # reps with a PLAN but no standard row → add them (statement-only reps are captured in
        # commcalc.carrier_commission for recon, not paid here)
        pm = parse_period(period)
        for rn, pv in plan_by_rep.items():
            if rn in plan_matched:
                continue
            inst = inst_by_rep.get(rn, 0.0)
            base = safe_float(pv["amount"])
            newrow = {"org_id": org_id, "period": period,
                      "period_month": pm.get("month"), "period_year": pm.get("year"),
                      "storeops_name": rn.title(), "epay_salesperson": rn,
                      "subtotal": base, "tier": 1,
                      "total_payout": round(base + inst, 2)}
            if cols["plan_comm"]:
                newrow["plan_comm"] = pv["amount"]
            if cols["plan_name"]:
                newrow["plan_name"] = pv.get("plan_name")
            if cols["residual_installment_comm"]:
                newrow["residual_installment_comm"] = inst
            if cols["carrier_statement_comm"]:
                newrow["carrier_statement_comm"] = stmt_by_rep.get(rn, 0.0)
            comms.append(newrow)
        print(f"INFO new-engines applied org={org_id} period={period}: "
              f"plan_reps={len(plan_by_rep)} statement_reps={len(stmt_by_rep)} installment_reps={len(inst_by_rep)}")
        return comms
    except Exception as e:
        print(f"WARN new-engine wiring skipped (standard calc kept): {e}")
        return comms


async def _run_calculation(period: str, org_id: str):
    """Background calculation task"""
    client = sb()
    save_errors = []
    
    try:
        # Load all data
        def fetch(table, filters={}):
            q = client.schema('commcalc').table(table).select('*')
            for k, v in filters.items():
                q = q.eq(k, v)
            try:
                r = q.limit(50000).execute()
                return r.data or []
            except: return []
        
        sales      = fetch('raw_sales', {'period': period})
        pay_detail = fetch('raw_payment_detail', {'period': period})
        mi_rows    = fetch('raw_mi', {'period': period})
        dlar_rep   = fetch('raw_dlar_rep', {'period': period})
        dlar_store = fetch('raw_dlar_store', {'period': period})
        catalog    = fetch('raw_catalog')
        pay_cats   = fetch('payment_categories')
        cfg_rows   = fetch('payout_config', {'period': period})
        store_map  = fetch('store_mapping')
        name_map   = fetch('name_map')
        shifts     = fetch('storeops_shifts') if False else []  # use storeops schema when migrated
        employees  = fetch('employees')
        stores     = fetch('stores')
        
        cfg = cfg_rows[0] if cfg_rows else {}
        
        # Resolve payment categories
        cat_map = {r['description'].strip(): r['category'] for r in pay_cats if r.get('description')}
        for r in pay_detail:
            pt = str(r.get('payment_type','')).strip()
            r['category'] = cat_map.get(pt, 'Unknown')
        
        valid = [r for r in sales if str(r.get('voided','')).upper().strip() != 'YES' and str(r.get('trans_type','')).strip() != 'Return']
        if not sales:
            raise Exception(f"No sales data for {period}")
        
        # Run calculation
        result = calc_rep_commissions(
            sales=sales, pay_detail=pay_detail, dlar_rep=dlar_rep,
            dlar_store=dlar_store, mi_rows=mi_rows, catalog=catalog,
            cfg=cfg, store_mapping=store_map, shifts=shifts,
            employees=employees, stores=stores, period=period,
            name_map=name_map
        )
        
        # Save commissions
        try:
            client.schema('commcalc').table('rep_commissions').delete().in_('period', _pvariants(period)).execute()
            comms = result['commissions']
            for row in comms:
                row['org_id'] = org_id
            # ADDITIVE: layer the new configurable engines (multi-month payout + commission plans) on top.
            # Boost-safe: with no schedule/plan configured this returns comms byte-identical (see helper).
            comms = _apply_new_engines(client, org_id, period, comms)
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

            client.schema('commcalc').table('flags').delete().in_('period', _pvariants(period)).execute()
            if flag_list:
                for row in flag_list:
                    row['org_id'] = org_id
                for i in range(0, len(flag_list), 500):
                    client.schema('commcalc').table('flags').insert(flag_list[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f'flags: {e}')

        # ── Detect potential chargebacks per rep ─────────────────
        try:
            existing = client.schema('commcalc').table('chargeback_items').select('source,source_ref,deduct').in_('period', _pvariants(period)).execute().data or []
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
             .in_('period', _pvariants(period)).neq('source', 'chargeback_review').execute())
            if cb_items:
                for i in range(0, len(cb_items), 500):
                    client.schema('commcalc').table('chargeback_items').insert(cb_items[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f'chargebacks: {e}')

        # Update calc status
        client.schema('commcalc').table('calc_status').upsert({
            'org_id': org_id, 'period': period,
            'calc_status': 'done',
            'calc_finished_at': 'now()',
            'save_errors': save_errors or None,
        }, on_conflict='org_id,period').execute()
        
    except Exception as e:
        try:
            client.schema('commcalc').table('calc_status').upsert({
                'org_id': org_id, 'period': period,
                'calc_status': 'error',
                'save_errors': [str(e)],
            }, on_conflict='org_id,period').execute()
        except: pass


# ── Report endpoints ──────────────────────────────────────────
@router.get("/commissions/{period}")
async def get_commissions(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('rep_commissions').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).order('total_payout', desc=True).execute()
    comms = r.data or []
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
    return comms

@router.get("/dlar-store/{period}")
async def get_dlar_store_kpis(period: str, org_id: str = ORG_ID):
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
    return q.order('location').execute().data or []


@router.get("/flags/{period}")
async def get_flags(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('flags').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).order('severity').execute()
    return r.data or []

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
    try:
        rows = (client.schema('commcalc').table('ui_label_override').select('scope,key,label')
                .eq('org_id', org_id).execute().data) or []
        for r in rows:
            k = (r.get('key') or '')
            if r.get('scope') == 'group':
                k = 'group:' + k
            if k and r.get('label'):
                labels[k] = r['label']
    except Exception:
        labels = {}
    return {"labels": labels, "capabilities": {"asset_lending": _asset_lending_capability(client, org_id)}}


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


@router.get("/gp/{period}")
async def get_gp_report(period: str, view: str = "store", market: str = "", org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    pv = _pvariants(period)
    sc = client.schema('commcalc')
    # Select ONLY the columns calc_gp_report reads. select('*') pulled ~90k WIDE rows (~20s of transfer;
    # compute is 0.06s) — narrowing the 3 big tables (sales/pay_detail/mi) cut it ~3x. Verified identical.
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
async def get_chargebacks(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('chargeback_items').select('*').eq('org_id', org_id).in_('period', _pvariants(period)).order('epay_salesperson').execute()
    return r.data or []

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
    org_id: str = Form(default=ORG_ID),
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
        result = sales_recon.sync_recon_flags(period, include_mismatch=include_mismatch)
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
async def run_discrepancy_check(payload: dict):
    """Trigger discrepancy detection. Send: { "period": "2026-04" }"""
    period = payload.get("period")
    if not period or len(period) != 7:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    try:
        result = run_discrepancy(period)
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


@router.get("/sales-analyzer/{period}")
async def get_sales_analyzer(period: str, window_days: int = 90, rep: str = "",
                            org_id: str = ORG_ID):
    """3-Month Retention (3MR) behavior per rep: each rep's activations from 3 months before
    `period` and which churned (cancelled/ported/suspended/deactivated) before their 3rd bill
    (within window_days). Returns per-rep summary + churned line items (model, MRC, sold-for,
    dates, store)."""
    require_org(org_id)
    try:
        return sales_analyzer.analyze(sb(), org_id, period, window_days=window_days, rep=rep)
    except Exception as e:
        raise HTTPException(500, f"sales-analyzer failed: {type(e).__name__}: {e}")


@router.get("/comp/residual-trend")
async def get_comp_residual_trend(months: int = 6, store: str = "", market: str = "",
                                  min_drop_pct: float = 20.0, min_drop_amt: float = 1.0,
                                  org_id: str = ORG_ID):
    """Month-over-month carrier residual (Comprehensive Comp) trend. Returns total residual per
    month with deltas, plus per-account DIPS (residual fell or the account vanished from the
    report = likely cancellation) labeled by the month each dip occurred — so you can see which
    month a residual dropped and why."""
    require_org(org_id)
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
        return comp_trend.compute_rep_pay_trend(sb(), org_id, months=months, store=store)
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
    """True if `period` ('June 2026') is the current in-progress calendar month."""
    try:
        pm = parse_period(period)
        t = _date.today()
        return pm['month'] == t.month and pm['year'] == t.year
    except Exception:
        return False


def _merge_actuals(monthly, feed):
    """Per (store_code, rep_name, trans_date), the daily-feed row REPLACES the monthly row when present
    (the daily B2B feed is fresher intra-month); days only in the monthly file are kept."""
    def key(r):
        return (str(r.get('store_code') or ''), str(r.get('rep_name') or ''), str(r.get('trans_date') or ''))
    out = {key(r): r for r in monthly}
    for r in feed:
        out[key(r)] = r
    return list(out.values())


def _fetch_actuals(client, org_id, period):
    try:
        rows = (client.schema('commcalc')
                .rpc('daily_sales_actuals', {'p_org_id': org_id, 'p_period': period})
                .execute().data) or []
    except Exception as e:
        print('daily_sales_actuals RPC failed:', e)
        return []
    # THEME 5(2) intra-month freshness: for the CURRENT open month the authoritative monthly file lags
    # (re-uploaded periodically) while the daily B2B feed is current, so prefer the feed per day. Closed
    # months stay monthly-authoritative (the THEME 5 design decision). Graceful: if the sibling feed RPC
    # isn't deployed (migration 048) or returns nothing, behavior is identical to before.
    if _is_open_month(period):
        try:
            feed = (client.schema('commcalc')
                    .rpc('daily_sales_feed_actuals', {'p_org_id': org_id, 'p_period': period})
                    .execute().data) or []
        except Exception:
            feed = []
        if feed:
            rows = _merge_actuals(rows, feed)
    cmap = _rep_canon_map(client, org_id)
    for r in rows:
        if r.get('rep_name'):
            r['rep_name'] = _canon(r.get('rep_name'), cmap)
    return rows


def _byod_pct_default(client, period, org_id=ORG_ID):
    try:
        r = (client.schema('commcalc').table('payout_config')
             .select('kpi_byod_target').eq('org_id', org_id).in_('period', _pvariants(period)).limit(1).execute().data) or []
        if r and r[0].get('kpi_byod_target') is not None:
            return safe_float(r[0]['kpi_byod_target'])
    except Exception:
        pass
    return 35.0


@router.get("/targets/{period}")
async def get_targets(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """List per-store monthly target config, seeding defaults for stores without a row.
    Accessories seed from storeops.stores.monthly_target; byod_pct from KPI config."""
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
            row = {
                'org_id': org_id, 'store_code': code, 'period': period,
                'period_month': pm['month'], 'period_year': pm['year'],
                'activations_monthly': 0, 'upgrades_monthly': 0,
                'accessories_monthly': safe_float(s.get('monthly_target')),
                'byod_pct': byod_def, 'notes': None,
                'address': s.get('address'), 'market': s.get('market'),
                '_seeded': True,
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


@router.get("/targets/{period}/summary")
async def get_targets_summary(period: str, today: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """All-stores overview: store-level today/pace/need/monthly/achieved per category. When RBAC
    enforcement is on, a non-admin manager only sees the stores in their org-unit span (Phase 5)."""
    client = sb()
    start, end, today = _period_bounds(period, today)
    byod_def = _byod_pct_default(client, period, org_id)

    trows = (client.schema('commcalc').table('targets')
             .select('*').eq('org_id', org_id).in_('period', _pvariants(period)).execute().data) or []
    by_code = {str(r.get('store_code', '')).upper(): r for r in trows}
    stores = (client.schema('storeops').table('stores')
              .select('store_code,address,market,monthly_target').eq('org_id', org_id).execute().data) or []
    shifts = _fetch_shifts(client, start, end, org_id)
    actuals = _fetch_actuals(client, org_id, period)

    out = []
    for s in stores:
        code = str(s.get('store_code', '') or '').strip()
        if not code:
            continue
        trow = by_code.get(code.upper())
        if not trow:
            trow = {'accessories_monthly': safe_float(s.get('monthly_target'))}
        monthly = targets_engine.derive_monthly_by_cat(trow, byod_def)
        if sum(monthly.values()) <= 0:
            continue
        hours_by_day = targets_engine.scope_hours_by_day(shifts, code, None)
        actuals_by_day = targets_engine.scope_actuals_by_day(actuals, code, None)
        res = targets_engine.compute_scope(monthly, hours_by_day, actuals_by_day, today,
                                           round_counts=True, month_end=end - _timedelta(days=1))
        store_conv = targets_engine.scope_conversion(actuals, code, None, today)
        # Reps who worked/sold at this store + their MTD performance, so the store
        # row breaks down into the people driving it (for corrective action).
        reps = []
        for rep_name in targets_engine.reps_in_scope(shifts, actuals, code):
            ach = targets_engine.scope_achieved_mtd(actuals, code, rep_name, today)
            rconv = targets_engine.scope_conversion(actuals, code, rep_name, today)
            reps.append({'rep': rep_name, **ach, 'conversion': rconv,
                         'below_store': rconv['rate'] < store_conv['rate']})
        reps.sort(key=lambda r: -r['activations'])
        out.append({
            'store_code': code, 'address': s.get('address'), 'market': s.get('market'),
            'scheduled_hours_total': res['scheduled_hours_total'],
            'categories': res['categories'],
            'conversion': store_conv,
            'reps': reps,
        })
    out.sort(key=lambda r: str(r.get('address') or r.get('store_code') or ''))
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        out = [s for s in out if in_keyset(ks, s.get('store_code'), s.get('address'))]
    return {'period': period, 'today': today.isoformat(), 'stores': out}


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
                            'pace': m.get('pace', 0), 'today_target': m.get('today_target', 0)})

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


@router.put("/expenses/{period}")
async def put_expenses(period: str, body: dict, org_id: str = ORG_ID):
    """Replace all expenses for the period (matrix save + bulk upload). Body:
    {rows:[{store_code, expense_name, expense_type, amount}]}. Zero/blank rows are dropped."""
    rows = body.get('rows') or []
    client = sb()
    client.schema('commcalc').table('store_expenses').delete() \
        .eq('org_id', org_id).in_('period', _pvariants(period)).execute()
    ins = []
    for r in rows:
        try:
            amt = float(r.get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        if amt == 0 or not (r.get('store_code') and r.get('expense_name')):
            continue
        ins.append({'org_id': org_id, 'period': period,
                    'store_code': str(r['store_code']).strip(),
                    'expense_name': str(r['expense_name']).strip(),
                    'expense_type': (r.get('expense_type') or 'Fixed'),
                    'amount': amt})
    for i in range(0, len(ins), 500):
        client.schema('commcalc').table('store_expenses').insert(ins[i:i + 500]).execute()
    return {"saved": len(ins), "period": period}


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
            res = await upload_file(ut, uf, period, False, org_id)
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


def _email_cfg(client, org_id):
    rows = client.schema('commcalc').table('email_sweep_config').select('*').eq('org_id', org_id).limit(1).execute().data or []
    return rows[0] if rows else None


async def _run_email_sweep(org_id):
    """Connect to the IMAP mailbox, download every NEW attachment matching a configured pattern, route
    each through the existing upload pipeline, and record what was processed (dedup by message_id+name)."""
    from starlette.datastructures import UploadFile as _UF
    client = sb()
    cfg = _email_cfg(client, org_id)
    if not cfg or not (cfg.get('imap_host') or '').strip():
        return {"ok": False, "error": "Email/IMAP not configured"}
    seen = client.schema('commcalc').table('email_processed').select('message_id,filename').eq('org_id', org_id).limit(100000).execute().data or []
    already = {(r.get('message_id'), r.get('filename')) for r in seen}
    try:
        files = _email.fetch_new_attachments(cfg, already)
    except Exception as e:
        client.schema('commcalc').table('email_sweep_config').update(
            {'last_run_at': _datetime.now(_timezone.utc).isoformat(), 'last_status': f"connect error: {e}"}).eq('org_id', org_id).execute()
        return {"ok": False, "error": str(e)}
    results = []
    for f in files:
        name, size, ut, mid = f['name'], f['size'], f.get('upload_type'), f.get('message_id')
        period = "" if ut == "daily_sales" else _ftp_current_period()
        status, detail, rows_saved = "ok", None, 0
        try:
            uf = _UF(io.BytesIO(f['bytes']), filename=name)
            res = await upload_file(ut, uf, period, False, org_id)
            rows_saved = (res or {}).get('saved', 0)
        except HTTPException as he:
            status, detail = "error", str(he.detail)[:300]
        except Exception as e:
            status, detail = "error", str(e)[:300]
        try:
            client.schema('commcalc').table('email_processed').upsert(
                {'org_id': org_id, 'message_id': mid, 'filename': name, 'file_size': size, 'upload_type': ut,
                 'rows_saved': rows_saved, 'status': status, 'detail': detail,
                 'processed_at': _datetime.now(_timezone.utc).isoformat()},
                on_conflict='org_id,message_id,filename').execute()
        except Exception:
            pass
        results.append({"file": name, "upload_type": ut, "status": status, "rows_saved": rows_saved, "detail": detail})
    ok = sum(1 for r in results if r['status'] == 'ok')
    client.schema('commcalc').table('email_sweep_config').update(
        {'last_run_at': _datetime.now(_timezone.utc).isoformat(),
         'last_status': f"{ok}/{len(results)} attachments ingested"}).eq('org_id', org_id).execute()
    # Auto-derive the monthly commission basis (raw_sales) from the feed when 'sales' is set to auto
    # on the Connectors page — best-effort + guarded, never breaks the sweep. This is what lets the
    # user stop uploading the monthly Sales file by hand. OFF until 'sales' auto is enabled.
    try:
        if (any(r['upload_type'] == 'daily_sales' and r['status'] == 'ok' for r in results)
                and _registry_auto_map(client, org_id).get('sales')):
            _promote_feed_to_raw_sales(client, org_id, _ftp_current_period())
    except Exception as e:
        print(f"WARN auto-promote feed->raw_sales failed: {e}")
    return {"ok": True, "ingested": ok, "files": results}


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
    dry_run returns the would-be delta WITHOUT writing — the safe way to validate before committing."""
    pv = _pvariants(period)
    canon = next((v for v in pv if v[:1].isalpha()), period)  # 'June 2026' form for raw_sales

    def _all(table):
        out, start = [], 0
        while True:
            rows = (client.schema('commcalc').table(table).select('*')
                    .eq('org_id', org_id).in_('period', pv).range(start, start + 999).execute().data) or []
            out.extend(rows)
            if len(rows) < 1000:
                return out
            start += 1000

    feed = _all('daily_sales_feed')
    existing = _all('raw_sales')
    feed_trans = {r.get('trans_id') for r in feed if r.get('trans_id')}
    raw_cols = set(existing[0].keys()) if existing else None
    DROP = {'id', 'created_at'}

    new_rows = []
    for r in feed:
        row = {k: v for k, v in r.items() if k not in DROP and (raw_cols is None or k in raw_cols)}
        row['org_id'] = org_id
        row['period'] = canon
        new_rows.append(row)
    monthly_only = [r for r in existing if r.get('trans_id') not in feed_trans]
    for r in monthly_only:
        new_rows.append({k: v for k, v in r.items() if k != 'id'})

    def _amt(rows):
        return round(sum((safe_float(x.get('ext_price')) or 0) for x in rows), 2)
    summary = {
        "period": canon, "dry_run": dry_run,
        "feed_lines": len(feed), "feed_trans": len(feed_trans),
        "existing_lines": len(existing), "existing_trans": len({r.get('trans_id') for r in existing}),
        "monthly_only_trans": len({r.get('trans_id') for r in monthly_only}),
        "result_lines": len(new_rows), "result_trans": len({r.get('trans_id') for r in new_rows}),
        "existing_amount": _amt(existing), "result_amount": _amt(new_rows),
    }
    if not new_rows:
        summary["skipped"] = "no feed or monthly rows for this period"
        return summary
    if existing and not force and len(new_rows) < retain * len(existing):
        summary["skipped"] = (f"guard: result {len(new_rows)} lines < {int(retain * 100)}% of existing "
                              f"{len(existing)} — feed looks incomplete (use force to override)")
        return summary
    if dry_run:
        return summary

    client.schema('commcalc').table('raw_sales').delete().eq('org_id', org_id).in_('period', pv).execute()
    for i in range(0, len(new_rows), 500):
        client.schema('commcalc').table('raw_sales').insert(new_rows[i:i + 500]).execute()
    summary["written"] = len(new_rows)
    return summary


@router.post("/sales/promote-feed")
def promote_feed(period: str, org_id: str = ORG_ID, dry_run: bool = True, force: bool = False):
    """Build the monthly commission basis (raw_sales) for a period from the daily B2B email feed.
    dry_run=true (default) PREVIEWS the delta without writing — pass dry_run=false to commit, then
    recompute the period. Idempotent + guarded; merges so no transaction is ever dropped."""
    require_org(org_id)
    return _promote_feed_to_raw_sales(sb(), org_id, period, dry_run=dry_run, force=force)


@router.get("/email-sweep/config")
def get_email_config(org_id: str = ORG_ID):
    """Config WITHOUT the password (presence only)."""
    require_org(org_id)
    cfg = _email_cfg(sb(), org_id) or {"org_id": org_id, "patterns": [], "imap_port": 993, "use_ssl": True, "mailbox": "INBOX"}
    cfg = dict(cfg)
    cfg['has_password'] = bool(cfg.pop('password', None))
    return cfg


@router.put("/email-sweep/config")
def put_email_config(body: dict, org_id: str = ORG_ID):
    """Save config. Password only updated when a non-empty value is supplied (so it isn't wiped)."""
    require_org(org_id)
    row = {"org_id": org_id, "imap_host": (body.get("imap_host") or "").strip() or None,
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
    sb().schema("commcalc").table("email_sweep_config").upsert(row, on_conflict="org_id").execute()
    return {"ok": True}


@router.post("/email-sweep/test")
def test_email(body: dict, org_id: str = ORG_ID):
    """Connect to the mailbox (merging any unsaved overrides) and list recent messages + their
    attachments and which match a pattern. Used by the 'Test connection' button before saving creds."""
    require_org(org_id)
    cfg = dict(_email_cfg(sb(), org_id) or {})
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
async def email_run_now(org_id: str = ORG_ID):
    require_org(org_id)
    return await _run_email_sweep(org_id)


@router.get("/email-sweep/processed")
def email_processed(org_id: str = ORG_ID, limit: int = 100):
    require_org(org_id)
    return (sb().schema("commcalc").table("email_processed").select("*").eq("org_id", org_id)
            .order("processed_at", desc=True).limit(limit).execute().data) or []


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
        res = await _run_email_sweep(oid)
        nxt = _vip_next_run(cfg.get('frequency') or 'daily', None, None, cfg.get('hour'), 'America/New_York')
        client.schema('commcalc').table('email_sweep_config').update({'next_run_at': nxt}).eq('org_id', oid).execute()
        ran.append({"org_id": oid, "result": res})
    return {"ran": len(ran), "detail": ran}
