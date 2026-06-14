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

# ── Upload endpoints ─────────────────────────────────────────
@router.post("/upload/{file_type}")
async def upload_file(
    file_type: str,
    file: UploadFile = File(...),
    period: str = "",
    org_id: str = "00000000-0000-0000-0000-000000000001"
):
    """Upload a data file (sales, payment_detail, mi, dlar_rep, dlar_store, catalog)"""
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
    
    # Delete existing for this period (skip for daily_sales — append mode)
    if has_period and period and file_type != 'daily_sales':
        try:
            client.schema('commcalc').table(table).delete().eq('period', period).execute()
        except Exception as e:
            raise HTTPException(500, f"Failed to clear existing data: {e}. Run commcalc_master_fix.sql")
    elif not has_period:
        try:
            client.schema('commcalc').table(table).delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()
        except: pass
    
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
            }
        elif file_type == "payment_detail":
            row = {**base,
                'business_address': r.get('Business Address',''),
                'payment_type': r.get('Payment Type',''),
                'amount': safe_float(r.get('Amount')),
                'mdn': str(r.get('Phone Number',r.get('MDN',''))).replace('.0','').strip(),
                'imei': str(r.get('IMEI','')).replace('.0','').strip(),
                'payment_date': str(r.get('Payment Date',''))[:10] or None,
                'rep_username': r.get('Rep Username',''),
            }
        elif file_type == "mi_report":
            def _date(v):
                s = str(v or '').strip()
                return s[:10] if s and s.lower() not in ('nat','nan','none','') else None
            row = {**base,
                'salesforce_id': r.get('SalesForceID',''),
                'subscriber_id': r.get('SubscriberID',''),
                'subscriber_status': r.get('Subscriber Status',''),
                'phone_number': str(r.get('Phone Number','')).replace('.0','').strip(),
                'device_serial': str(r.get('Device Serial','')).replace('.0','').strip(),
                'mi_activation_date': _date(r.get('MI Activation Date')),
                'mi_deactivation_date': _date(r.get('MI Deactivation Date')),
                'residual_transfer_in_date': _date(r.get('Residual Transfer In Date')),
                'residual_transfer_out_date': _date(r.get('Residual Transfer Out Date')),
                'customer_plan': r.get('Customer Plan',''),
                'base_mrc': safe_float(r.get('Base MRC Amount')),
                'commissionable_mrc': safe_float(r.get('Commissionable MRC Amount')),
                'actual_mi_payout': safe_float(r.get('Actual MI Payout Amount')),
                'actual_atu_payout': safe_float(r.get('Actual ATU Payout Amount')),
                'rep_username': r.get('Rep Username',''),
                'door_type': r.get('Door Type',''),
                'report_month': r.get('Report Month',''),
            }
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
    
    # Insert in batches
    saved = 0
    for i in range(0, len(mapped), 500):
        batch = mapped[i:i+500]
        try:
            # Plain insert for all upload types. Monthly wipes the period
            # first, so there are no conflicts. The old unique dedup index
            # was dropped because one transaction has many line items that
            # share a single Trans ID.
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

    return {"saved": saved, "file_type": file_type, "period": period}


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
                     'lookback_days': 14, 'sweep_invoices': True, 'sweep_asset': False}


def _vip_public_cfg(cfg):
    """Config WITHOUT the password — only whether credentials are set."""
    if not cfg:
        return {**_VIP_CFG_DEFAULTS, 'configured': False, 'has_credentials': False,
                'portal_user': None, 'next_run_at': None, 'last_run_at': None,
                'last_status': None, 'last_detail': None}
    out = {k: cfg.get(k) for k in (
        'enabled', 'frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone',
        'lookback_days', 'sweep_invoices', 'sweep_asset', 'portal_user',
        'next_run_at', 'last_run_at', 'last_status', 'last_detail')}
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
    try:
        res = vip_sweep.run_invoice_sweep(
            client, org_id, cfg['portal_user'], cfg['portal_pass'],
            int(cfg.get('lookback_days') or 14),
            (_vip_money, _vip_int, _vip_ts, _vip_period))
        detail = (f"OK — {res['invoices']} invoices, {res['lines']} lines, "
                  f"{res['devices']} devices ({res['window']})")
        _vip_set_status(client, org_id, 'ok', detail, mark_run=True)
    except vip_sweep.VipLoginError as e:
        _vip_set_status(client, org_id, 'error', str(e), mark_run=True)
    except Exception as e:
        _vip_set_status(client, org_id, 'error', f"Sweep failed: {e}", mark_run=True)


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
              'lookback_days', 'sweep_invoices', 'sweep_asset', 'enabled', 'portal_user'):
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
            client.schema('commcalc').table('chargeback_items').delete().eq('period', period).execute()
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


def _fetch_shifts(client, start, end):
    return (client.schema('storeops').table('shifts')
            .select('employee_name,store_code,shift_date,scheduled_hours,is_deleted')
            .gte('shift_date', start.isoformat())
            .lt('shift_date', end.isoformat())
            .limit(50000).execute().data) or []


def _fetch_actuals(client, org_id, period):
    try:
        return (client.schema('commcalc')
                .rpc('daily_sales_actuals', {'p_org_id': org_id, 'p_period': period})
                .execute().data) or []
    except Exception as e:
        print('daily_sales_actuals RPC failed:', e)
        return []


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
    result = targets_engine.compute_scope(monthly, hours_by_day, actuals_by_day, today, round_counts=True)
    result.update({
        'period': period, 'scope': scope, 'store_code': store_code,
        'rep': rep_arg, 'monthly_targets': monthly,
        'reps': targets_engine.reps_in_scope(shifts, actuals, store_code),
    })
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
        res = targets_engine.compute_scope(monthly, hours_by_day, actuals_by_day, today, round_counts=True)
        out.append({
            'store_code': code, 'address': s.get('address'), 'market': s.get('market'),
            'scheduled_hours_total': res['scheduled_hours_total'],
            'categories': res['categories'],
        })
    out.sort(key=lambda r: str(r.get('address') or r.get('store_code') or ''))
    return {'period': period, 'today': today.isoformat(), 'stores': out}
