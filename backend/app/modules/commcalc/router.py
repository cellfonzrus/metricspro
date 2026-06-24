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
from app.modules.commcalc import b2b_sweep
from app.modules.commcalc import sales_analyzer
from app.modules.commcalc import comp_trend
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
    
    SUPPORTED = ["sales","daily_sales","payment_detail","mi_report","dlar_rep","dlar_store","catalog","master_cats","comp_report"]
    if file_type not in SUPPORTED:
        raise HTTPException(400, f"Unknown file type: {file_type}. Supported: {SUPPORTED}")
    
    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents), dtype=str)
    except Exception as e:
        raise HTTPException(400, f"Could not read Excel file: {e}")
    
    df = df.fillna('')

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
        "daily_sales": "raw_sales",
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
    # period. daily_sales is append-mode (no clear). catalog/master_cats replace the whole table.
    if mapped:
        if has_period and period and file_type != 'daily_sales':
            try:
                client.schema('commcalc').table(table).delete().eq('org_id', org_id).eq('period', period).execute()
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

    # After a sales upload, scan for fraud (fake/reused email, duplicate id) → chargeback bucket.
    fraud = None
    if file_type in ('sales', 'daily_sales') and mapped:
        try:
            for p in sorted({m.get('period') for m in mapped if m.get('period')}) or [period]:
                fr = _detect_fraud(client, org_id, p)
                fraud = {'email_flags': (fraud or {}).get('email_flags', 0) + fr['email_flags'],
                         'dupe_flags': (fraud or {}).get('dupe_flags', 0) + fr['dupe_flags']}
        except Exception as e:
            print(f'WARN fraud scan after sales upload failed (run 036?): {e}')

    return {"saved": saved, "file_type": file_type, "period": period, "fraud": fraud}


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
            q = q.eq('period', period)
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
            q = q.eq('period', period)
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
        q = q.eq('period', period)
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
    if frequency == 'daily':
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


def _do_vip_sweep(org_id):
    """Background worker: read creds from the config table, run the invoice sweep, record status."""
    client = sb()
    cfg = _vip_cfg(client, org_id)
    if not cfg or not cfg.get('portal_user') or not cfg.get('portal_pass'):
        _vip_set_status(client, org_id, 'error', 'No VIP credentials set in the admin area', mark_run=True)
        return
    _vip_set_status(client, org_id, 'running', 'Sweep in progress…')
    # Default to the invoice sweep (back-compat: cfg may predate the toggles). sweep_asset
    # additionally pulls the PayGo / asset-lending weekly billing ledger (migration 014).
    do_invoices = cfg.get('sweep_invoices') is not False
    do_asset = bool(cfg.get('sweep_asset'))
    do_creditmemo = bool(cfg.get('sweep_creditmemo'))
    do_asset_ledger = cfg.get('sweep_asset_ledger') is not False  # default ON (refresh asset_ledger)
    do_chargebacks = cfg.get('sweep_chargebacks') is not False    # default ON (stage VIP chargebacks)
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
        _vip_set_status(client, org_id, 'ok', "Nothing enabled (tick a report on the VIP Sweep page)", mark_run=True)
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
        raise HTTPException(400, "Set the VIP credentials first.")
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
    cur = client.schema('commcalc').table('chargeback_review').select('needs_review').eq('id', cb_id).execute().data or []
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
        q = q.eq("period", period)
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
        _dlar_set_status(client, org_id, 'error', 'No Boost portal credentials set in the admin area', mark_run=True)
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
        raise HTTPException(400, "Set the Boost portal credentials first.")
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
    # which reports to pull — sweep_mi defaults on (back-compat); comp/payment opt-in
    reports = []
    if cfg.get('sweep_mi') is not False:
        reports.append('mi')
    if cfg.get('sweep_comp'):
        reports.append('comp_report')
    if cfg.get('sweep_payment'):
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
            client.schema('commcalc').table('rep_commissions').delete().eq('period', period).execute()
            comms = result['commissions']
            for row in comms:
                row['org_id'] = org_id
            for i in range(0, len(comms), 500):
                client.schema('commcalc').table('rep_commissions').insert(comms[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f"commissions: {e}")
        
        # Compute and save flags
        try:
            pm = parse_period(period)
            flag_list = calc_flags(
                sales=valid,
                pay_detail=pay_detail,
                mi_rows=mi_rows,
                dlar_store=dlar_store,
                store_mapping=store_map,
                period=period,
                period_month=pm['month'],
                period_year=pm['year'],
            )
            # Add port-out / transfer-out / suspended flags from MI report
            try:
                po_flags = calc_portout_flags(mi_rows, valid, store_map, period, pm['month'], pm['year'])
                flag_list = (flag_list or []) + po_flags
            except Exception as pe:
                save_errors.append(f'portout: {pe}')

            client.schema('commcalc').table('flags').delete().eq('period', period).execute()
            if flag_list:
                for row in flag_list:
                    row['org_id'] = org_id
                for i in range(0, len(flag_list), 500):
                    client.schema('commcalc').table('flags').insert(flag_list[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f'flags: {e}')

        # ── Detect potential chargebacks per rep ─────────────────
        try:
            existing = client.schema('commcalc').table('chargeback_items').select('source,source_ref,deduct').eq('period', period).execute().data or []
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
             .eq('period', period).neq('source', 'chargeback_review').execute())
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
    r = client.schema('commcalc').table('rep_commissions').select('*').eq('period', period).order('total_payout', desc=True).execute()
    comms = r.data or []
    # Apply chargeback deductions (deduct=true) per rep
    cb = client.schema('commcalc').table('chargeback_items').select('epay_salesperson,amount,deduct').eq('period', period).execute().data or []
    ded_by_rep = {}
    for item in cb:
        if item.get('deduct'):
            rep = item.get('epay_salesperson') or ''
            ded_by_rep[rep] = ded_by_rep.get(rep, 0) + (item.get('amount') or 0)
    for cr in comms:
        rep = cr.get('epay_salesperson') or ''
        d = ded_by_rep.get(rep, 0)
        cr['chargeback_deduction'] = d
        cr['final_payout'] = (cr.get('total_payout') or 0) - d
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
    q = q.eq('period_month', mo).eq('period_year', yr) if mo and yr else q.eq('period', period)
    return q.order('location').execute().data or []


@router.get("/flags/{period}")
async def get_flags(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('flags').select('*').eq('period', period).order('severity').execute()
    return r.data or []

@router.get("/config/{period}")
async def get_config(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('payout_config').select('*').eq('period', period).limit(1).execute()
    if r.data: return r.data[0]
    return {}

@router.put("/config/{period}")
async def save_config(period: str, config: dict, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    config.update({'period': period, 'org_id': org_id})
    r = client.schema('commcalc').table('payout_config').upsert(config, on_conflict='org_id,period').execute()
    return r.data[0] if r.data else config

@router.get("/stores")
async def get_stores(org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('store_mapping').select('*').order('store_address').execute()
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
    sales      = client.schema('commcalc').table('raw_sales').select('*').eq('period', period).limit(50000).execute().data or []
    pay_detail = client.schema('commcalc').table('raw_payment_detail').select('*').eq('period', period).limit(50000).execute().data or []
    mi_rows    = client.schema('commcalc').table('raw_mi').select('*').eq('period', period).execute().data or []
    rep_comms  = client.schema('commcalc').table('rep_commissions').select('*').eq('period', period).execute().data or []
    expenses   = client.schema('commcalc').table('store_expenses').select('*').eq('period', period).execute().data or []
    catalog    = client.schema('commcalc').table('raw_catalog').select('*').execute().data or []
    store_map  = client.schema('commcalc').table('store_mapping').select('*').execute().data or []
    pay_cats   = client.schema('commcalc').table('payment_categories').select('*').execute().data or []
    comp_rows  = client.schema('commcalc').table('raw_comp_report').select('*').eq('period', period).limit(50000).execute().data or []
    cat_map    = {r['description'].strip(): r['category'] for r in pay_cats if r.get('description')}
    for r in pay_detail:
        pt = str(r.get('payment_type', '') or '').strip()
        r['category'] = cat_map.get(pt, 'Unknown')
    result = calc_gp_report(sales, pay_detail, mi_rows, rep_comms, expenses, catalog, store_map, period, comp_rows=comp_rows)
    if market:
        result['store_rows'] = [r for r in result['store_rows'] if r.get('market', '').upper() == market.upper()]
    return result

@router.get("/chargebacks/{period}")
async def get_chargebacks(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('chargeback_items').select('*').eq('period', period).order('epay_salesperson').execute()
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
    r = client.schema('commcalc').table('calc_status').select('*').eq('period', period).limit(1).execute()
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
    resp = client.schema("commcalc").table("discrepancy_results")        .select("*")        .eq("org_id", org_id)        .eq("period", period)        .order("store")        .order("gap", desc=True)        .execute()
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
                  .select('epay_salesperson,storeops_name').execute().data or []):
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


def _fetch_shifts(client, start, end):
    rows = (client.schema('storeops').table('shifts')
            .select('employee_name,store_code,shift_date,scheduled_hours,is_deleted')
            .gte('shift_date', start.isoformat())
            .lt('shift_date', end.isoformat())
            .limit(50000).execute().data) or []
    cmap = _rep_canon_map(client)
    for r in rows:
        r['employee_name'] = _canon(r.get('employee_name'), cmap)
    return rows


def _fetch_actuals(client, org_id, period):
    try:
        rows = (client.schema('commcalc')
                .rpc('daily_sales_actuals', {'p_org_id': org_id, 'p_period': period})
                .execute().data) or []
    except Exception as e:
        print('daily_sales_actuals RPC failed:', e)
        return []
    cmap = _rep_canon_map(client, org_id)
    for r in rows:
        if r.get('rep_name'):
            r['rep_name'] = _canon(r.get('rep_name'), cmap)
    return rows


def _byod_pct_default(client, period):
    try:
        r = (client.schema('commcalc').table('payout_config')
             .select('kpi_byod_target').eq('period', period).limit(1).execute().data) or []
        if r and r[0].get('kpi_byod_target') is not None:
            return safe_float(r[0]['kpi_byod_target'])
    except Exception:
        pass
    return 35.0


@router.get("/targets/{period}")
async def get_targets(period: str, org_id: str = ORG_ID):
    """List per-store monthly target config, seeding defaults for stores without a row.
    Accessories seed from storeops.stores.monthly_target; byod_pct from KPI config."""
    client = sb()
    pm = parse_period(period)
    rows = (client.schema('commcalc').table('targets')
            .select('*').eq('org_id', org_id).eq('period', period).execute().data) or []
    by_code = {str(r.get('store_code', '')).upper(): r for r in rows}
    byod_def = _byod_pct_default(client, period)

    stores = (client.schema('storeops').table('stores')
              .select('store_code,address,market,monthly_target,is_active')
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
    rep: str = "", today: str = "", org_id: str = ORG_ID,
):
    """Schedule-weighted daily targets + catch-up + pace + day-by-day calendar
    for a single store (scope=store) or a single rep within it (scope=rep)."""
    client = sb()
    start, end, today = _period_bounds(period, today)
    byod_def = _byod_pct_default(client, period)

    trow = (client.schema('commcalc').table('targets')
            .select('*').eq('org_id', org_id).eq('period', period)
            .eq('store_code', store_code).limit(1).execute().data) or []
    target_row = trow[0] if trow else {}
    # Seed accessories from store monthly_target when no explicit row yet.
    if not trow:
        srow = (client.schema('storeops').table('stores')
                .select('monthly_target').eq('store_code', store_code).limit(1).execute().data) or []
        if srow:
            target_row = {'accessories_monthly': safe_float(srow[0].get('monthly_target'))}
    monthly = targets_engine.derive_monthly_by_cat(target_row, byod_def)

    shifts = _fetch_shifts(client, start, end)
    actuals = _fetch_actuals(client, org_id, period)
    rep_arg = rep if scope == 'rep' and rep else None

    hours_by_day = targets_engine.scope_hours_by_day(shifts, store_code, rep_arg)
    actuals_by_day = targets_engine.scope_actuals_by_day(actuals, store_code, rep_arg)
    result = targets_engine.compute_scope(monthly, hours_by_day, actuals_by_day, today,
                                          round_counts=True, month_end=end - _timedelta(days=1))
    result.update({
        'period': period, 'scope': scope, 'store_code': store_code,
        'rep': rep_arg, 'monthly_targets': monthly,
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
async def get_targets_summary(period: str, today: str = "", org_id: str = ORG_ID):
    """All-stores overview: store-level today/pace/need/monthly/achieved per category."""
    client = sb()
    start, end, today = _period_bounds(period, today)
    byod_def = _byod_pct_default(client, period)

    trows = (client.schema('commcalc').table('targets')
             .select('*').eq('org_id', org_id).eq('period', period).execute().data) or []
    by_code = {str(r.get('store_code', '')).upper(): r for r in trows}
    stores = (client.schema('storeops').table('stores')
              .select('store_code,address,market,monthly_target').execute().data) or []
    shifts = _fetch_shifts(client, start, end)
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
    return {'period': period, 'today': today.isoformat(), 'stores': out}


# KPI → commission tier inputs (mirrors calculator.py KPI defaults).
# Each is (key, label, payout_config column, default target %).
ACTION_KPI_DEFS = [
    ('atu', 'ATU', 'kpi_atu_target', 55),
    ('protect', 'Protect', 'kpi_protect_target', 80),
    ('boostapp', 'Boost App', 'kpi_boostapp_target', 65),
    ('familyplan', 'Family Plan', 'kpi_familyplan_target', 45),
    ('byod', 'BYOD', 'kpi_byod_target', 35),
    ('tmr3', 'TMR3', 'kpi_tmr3_target', 70),
    ('aal', 'AAL', 'kpi_aal_target', 5),
]
_AP_CAT_LABEL = {'activations': 'Activations', 'upgrades': 'Upgrades',
                 'byod': 'BYOD', 'accessories': 'Accessories'}


@router.get("/targets/{period}/action-plan")
async def get_action_plan(period: str, today: str = "", store_code: str = "", rep: str = "",
                          org_id: str = ORG_ID):
    """Daily Action Plan — prioritized focus areas per store (per-category catch-up
    + conversion) and per rep (conversion + commission-at-risk). Reuses the SAME
    targets engine + conversion the Daily Targets pages use, plus the computed
    rep_commissions (tier/KPIs) so 'commission at risk' reconciles with the payroll."""
    client = sb()
    start, end, today = _period_bounds(period, today)
    byod_def = _byod_pct_default(client, period)
    month_end = end - _timedelta(days=1)

    trows = (client.schema('commcalc').table('targets')
             .select('*').eq('org_id', org_id).eq('period', period).execute().data) or []
    by_code = {str(r.get('store_code', '')).upper(): r for r in trows}
    stores = (client.schema('storeops').table('stores')
              .select('store_code,address,market,monthly_target').execute().data) or []
    shifts = _fetch_shifts(client, start, end)
    actuals = _fetch_actuals(client, org_id, period)
    rank = targets_engine.SEV_RANK

    # ── Commission context: KPI targets + each rep's computed tier ($ at risk = the
    #    payout forfeited below tier 1.0 = subtotal × (1 − tier)). Empty/graceful if
    #    commissions haven't been run for the period yet.
    cfg_rows = (client.schema('commcalc').table('payout_config')
                .select('*').eq('period', period).limit(1).execute().data) or []
    cfg = cfg_rows[0] if cfg_rows else {}
    kpi_targets = {k: (safe_float(cfg.get(col)) or float(dv)) for (k, _l, col, dv) in ACTION_KPI_DEFS}
    t100 = int(cfg.get('tier_100_min_kpis') or 7)
    t75 = int(cfg.get('tier_75_min_kpis') or 5)
    comm_rows = (client.schema('commcalc').table('rep_commissions')
                 .select('storeops_name,epay_salesperson,tier,kpis_met,total_kpis,'
                         'kpi_values,subtotal,total_payout')
                 .eq('period', period).execute().data) or []
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
        for (k, lab, _col, _dv) in ACTION_KPI_DEFS:
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

    out = []
    tot_crit = tot_warn = 0
    tot_at_risk = 0.0
    for s in stores:
        code = str(s.get('store_code', '') or '').strip()
        if not code:
            continue
        if store_code and code.upper() != store_code.strip().upper():
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
        store_at_risk = round(sum((rp['commission'] or {}).get('at_risk', 0)
                                  for rp in rep_plans if rp.get('commission')), 2)
        if store_at_risk > 0:
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
                  .eq('is_deleted', False).limit(50000).execute().data or []):
            n = (s.get('employee_name') or '').strip()
            if n:
                names.add(n)
    except Exception:
        pass
    try:
        for r in (client.schema('commcalc').table('raw_dlar_rep').select('rep_name')
                  .limit(50000).execute().data or []):
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
@router.get("/expenses/{period}")
async def get_expenses(period: str, org_id: str = ORG_ID):
    """All store expenses for a period (commcalc.store_expenses)."""
    client = sb()
    rows = (client.schema('commcalc').table('store_expenses').select('*')
            .eq('org_id', org_id).eq('period', period).order('store_code').execute().data) or []
    return {"period": period, "expenses": rows}


@router.put("/expenses/{period}")
async def put_expenses(period: str, body: dict, org_id: str = ORG_ID):
    """Replace all expenses for the period (matrix save + bulk upload). Body:
    {rows:[{store_code, expense_name, expense_type, amount}]}. Zero/blank rows are dropped."""
    rows = body.get('rows') or []
    client = sb()
    client.schema('commcalc').table('store_expenses').delete() \
        .eq('org_id', org_id).eq('period', period).execute()
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
