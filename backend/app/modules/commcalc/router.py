"""CommCalc API Router — all /api/v1/commcalc/* endpoints"""
from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse
import pandas as pd
import io
from app.core.database import get_supabase
from app.modules.commcalc.calculator import calc_rep_commissions, parse_period, safe_float
from app.modules.commcalc.gp_report import calc_gp_report
from app.modules.commcalc.flags import calc_flags

router = APIRouter(prefix="/commcalc", tags=["CommCalc"])

# ── Helper ───────────────────────────────────────────────────
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
    
    SUPPORTED = ["sales","payment_detail","mi_report","dlar_rep","dlar_store","catalog","master_cats","comp_report"]
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
        'payment_detail': ['Payment Type', 'Amount'],
        'mi_report':      ['SalesForceID'],
        'dlar_rep':       ['Advocate Name', 'ATU %'],
        'dlar_store':     ['Business Address', 'PSA Projected'],
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
    }
    # Try schema-qualified first, fall back to public prefix
    table = TABLE_MAP[file_type]
    
    # Delete existing for this period
    if has_period and period:
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
        
        if file_type == "sales":
            row = {**base,
                'store': r.get('Store',''), 'salesperson': r.get('Salesperson',''),
                'user_login': r.get('User Login',''), 'contract_type': r.get('Contract Type',''),
                'department': r.get('Department',''), 'category': r.get('Category',''),
                'product_desc': r.get('Product Desc',''), 'product_id': safe_float(r.get('Product ID')) or None,
                'gp': safe_float(r.get('GP')), 'ext_price': safe_float(r.get('Ext Price')),
                'trans_id': str(r.get('Trans ID','')).replace('.0','').strip(),
                'trans_date': str(r.get('Trans Date Time',r.get('Trans Date','')))[:10] or None,
                'mdn': str(r.get('Activated Mobile Number','')).replace('.0','').strip(),
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
            row = {**base,
                'salesforce_id': r.get('SalesForceID',''),
                'actual_mi_payout': safe_float(r.get('Actual MI Payout Amount')),
                'actual_atu_payout': safe_float(r.get('Actual ATU Payout Amount')),
                'phone_number': r.get('Phone Number',''),
                'subscriber_status': r.get('Subscriber Status',''),
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
                'address': r.get('Business Address',''),
                'store_code': r.get('Store Code',''),
                'psa_projected': safe_float(r.get('PSA Projected',0)),
                'port_pct': safe_float(r.get('Port Out%',r.get('Port%',0))),
            }
        elif file_type == "comp_report":
            row = {**base,
                'business_address': r.get('Business Address',''),
                'compensation_type': r.get('Compensation Type',''),
                'quantity': int(safe_float(r.get('Quantity')) or 0),
                'payment_amount': safe_float(r.get('Payment Amount')),
                'salesforce_id': r.get('SalesForce ID',''),
                'brand': r.get('Brand',''),
                'begin_date': str(r.get('Begin Date',''))[:10] or None,
                'end_date': str(r.get('End Date',''))[:10] or None,
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
            client.schema('commcalc').table(table).insert(batch).execute()
            saved += len(batch)
        except Exception as e:
            raise HTTPException(500, f"Insert failed at row {i}: {e}")
    
    return {"saved": saved, "file_type": file_type, "period": period}


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
            client.schema('commcalc').table('flags').delete().eq('period', period).execute()
            if flag_list:
                for row in flag_list:
                    row['org_id'] = org_id
                for i in range(0, len(flag_list), 500):
                    client.schema('commcalc').table('flags').insert(flag_list[i:i+500]).execute()
        except Exception as e:
            save_errors.append(f'flags: {e}')

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
    return r.data or []

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

@router.get("/calc-status/{period}")
async def get_calc_status(period: str, org_id: str = "00000000-0000-0000-0000-000000000001"):
    client = sb()
    r = client.schema('commcalc').table('calc_status').select('*').eq('period', period).limit(1).execute()
    return r.data[0] if r.data else {'calc_status': 'not_run'}
