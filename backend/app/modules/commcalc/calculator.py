"""
CommCalc Commission Calculator
All business logic in Python - verified formulas
"""
from typing import Any
import re

DEVICE_DEPTS = {'Android - XP', 'IPHONE - XP', 'TABLET - XP'}
# BYOD: any contract type containing 'BYOD'
BYOD_ACT = {
    'BYOD','BYOD Port-In','BYOD Add A Line','BYOD Port-In Add A Line',
    'BYOD Swap','BYOD Eligible Port-In'
}
# Upgrade: any containing 'Upgrade'
UPGRADE_ACT = {'Upgrade','Upgrade Port-In','Device Upgrade'}
# Premium: standard activations (non-BYOD, non-upgrade)
PREMIUM_ACT = {
    'Activation','Port-In','Add A Line','Port-In Add A Line',
    'Eligible Port-In Activation','Activation Add A Line',
    'Eligible Port-In Add A Line'
}
# Activation keyword patterns — recognize label VARIANTS the exact sets above miss (e.g. "New
# Activation", "Standard Activation", "Eligible Port In Activation") so drifted B2B Contract Type
# labels still pay/count instead of being silently dropped. Kept activation-specific (NOT "any
# non-empty type") so a stray non-activation label can't accidentally earn an activation bounty.
_PREMIUM_KEYS = ('activation', 'port-in', 'port in', 'add a line', 'add-a-line', 'new line', ' aal', 'aal ')


def classify_contract_type(ct: str):
    """The ONE contract-type classifier shared by commissions, targets, and the sales report.
    Returns 'byod' | 'upgrade' | 'premium' | None. Tolerant of label drift: BYOD/Upgrade by CONTAINS,
    premium by the known set OR an activation keyword. None = not a phone-activation line (e.g. an
    accessory line, which carries a blank Contract Type)."""
    c = (ct or '').strip()
    if not c:
        return None
    cl = c.lower()
    if 'byod' in cl:
        return 'byod'
    if 'upgrade' in cl:
        return 'upgrade'
    if c in PREMIUM_ACT or any(k in cl for k in _PREMIUM_KEYS):
        return 'premium'
    return None

def parse_period(period: str) -> dict:
    months = {
        'january':1,'february':2,'march':3,'april':4,
        'may':5,'june':6,'july':7,'august':8,
        'september':9,'october':10,'november':11,'december':12
    }
    parts = period.lower().split()
    return {
        'month': months.get(parts[0], 1),
        'year': int(parts[1]) if len(parts) > 1 else 2026
    }

def safe_float(v) -> float:
    try: return float(v or 0)
    except: return 0.0

def calc_rep_commissions(
    sales: list[dict],
    pay_detail: list[dict], 
    dlar_rep: list[dict],
    dlar_store: list[dict],
    mi_rows: list[dict],
    catalog: list[dict],
    cfg: dict,
    store_mapping: list[dict],
    shifts: list[dict],
    employees: list[dict],
    stores: list[dict],
    period: str,
    name_map: list[dict]
) -> dict:
    """Main calculation function - returns commissions, flags, kpis"""
    
    pm = parse_period(period)
    period_month = pm['month']
    period_year = pm['year']
    
    # ── Config ──────────────────────────────────────────────
    G = {
        'upgrade_flat':     cfg.get('upgrade_flat') if cfg.get('upgrade_flat') is not None else 20,
        'premium_flat':     cfg.get('premium_flat') if cfg.get('premium_flat') is not None else 5,
        'byod_flat':        cfg.get('byod_flat') if cfg.get('byod_flat') is not None else 3,
        'byod_extra':       cfg.get('byod_extra_spiff') or 0,
        'trade_in_spiff':   cfg.get('trade_in_spiff') if cfg.get('trade_in_spiff') is not None else 20,
        'acima_spiff':      cfg.get('acima_spiff') if cfg.get('acima_spiff') is not None else 25,
        'acc_rate':         cfg.get('acc_rate') or 0.10,
        'setup_rate':       cfg.get('setup_fee_rate') or 0.10,
        'acc_target_on':    bool(cfg.get('acc_target_enabled', False)),
        'acc_target_pct':   cfg.get('acc_target_pct') or 0.10,
        'custom_spiffs':    cfg.get('custom_spiffs') or [],
        'straight':         bool(cfg.get('straight_line', False)),
        't100':             int(cfg.get('tier_100_min_kpis') or 7),
        't75':              int(cfg.get('tier_75_min_kpis') or 5),
        't75pct':           float(cfg.get('tier_75_pct') or 0.75),
        't50pct':           float(cfg.get('tier_50_pct') or 0.50),
    }
    KPI = {
        'atu':       float(cfg.get('kpi_atu_target') or 55),
        'protect':   float(cfg.get('kpi_protect_target') or 80),
        'boostapp':  float(cfg.get('kpi_boostapp_target') or 65),
        'familyplan':float(cfg.get('kpi_familyplan_target') or 45),
        'byod':      float(cfg.get('kpi_byod_target') or 35),
        'tmr3':      float(cfg.get('kpi_tmr3_target') or 70),
        'aal':       float(cfg.get('kpi_aal_target') or 5),
    }
    
    # ── Name map ─────────────────────────────────────────────
    name_lookup = {}  # epay_login → storeops_name
    for n in name_map:
        if n.get('epay_login'):
            name_lookup[n['epay_login'].lower()] = n.get('storeops_name','')
    
    # ── Payment categories ────────────────────────────────────
    # (passed in via pay_detail which already has categories resolved)
    
    # ── Catalog cost map ──────────────────────────────────────
    cat_cost = {}
    for c in catalog:
        pid = c.get('product_id')
        cost = c.get('cost')
        if pid and cost:
            cat_cost[float(pid)] = float(cost)
    
    # ── Filter valid sales ────────────────────────────────────
    valid = [
        r for r in sales
        if str(r.get('voided','')).upper().strip() != 'YES'
        and str(r.get('trans_type','')).strip() != 'Return'
    ]
    
    # ── DLAR lookups ──────────────────────────────────────────
    dlar_rep_by_name = {}
    for d in dlar_rep:
        name = str(d.get('rep_name','')).upper()
        if name: dlar_rep_by_name[name] = d
    
    dlar_store_by_num = {}
    for d in dlar_store:
        addr = str(d.get('address','')).strip()
        num = addr.split(' ')[0] if addr else ''
        if num: dlar_store_by_num[num] = d
    
    # ── Store targets ─────────────────────────────────────────
    store_target_by_code = {}
    for s in stores:
        code = str(s.get('store_code','')).upper()
        if code: store_target_by_code[code] = float(s.get('monthly_target') or 0)
    
    # ── Store mapping ─────────────────────────────────────────
    store_map_by_addr = {}
    for s in store_mapping:
        addr = str(s.get('store_address','')).lower().strip()
        if addr: store_map_by_addr[addr] = s
    
    # ── Accessory targets from shifts ────────────────────────
    rep_acc_targets = {}
    if G['acc_target_on'] and shifts and employees:
        # Filter shifts to period month
        period_shifts = [
            s for s in shifts
            if s.get('shift_date') and not s.get('is_deleted', False)
        ]
        # Store total hours
        store_total_hours = {}
        for s in period_shifts:
            sc = str(s.get('store_code','')).upper()
            store_total_hours[sc] = store_total_hours.get(sc, 0) + safe_float(s.get('scheduled_hours'))
        # Rep hours
        emp_by_name = {str(e.get('name','')).upper(): e for e in employees if e.get('name')}
        for emp in employees:
            rep_name = str(emp.get('epay_salesperson') or emp.get('name') or '').upper()
            if not rep_name: continue
            emp_name = str(emp.get('name','')).upper()
            emp_shifts = [s for s in period_shifts if str(s.get('employee_name','')).upper() == emp_name]
            rep_hours = sum(safe_float(s.get('scheduled_hours')) for s in emp_shifts)
            store_code = str(emp.get('home_store','') or (emp_shifts[0].get('store_code','') if emp_shifts else '')).upper()
            store_total = store_total_hours.get(store_code, 0)
            store_target = store_target_by_code.get(store_code, 0)
            if store_total > 0 and store_target > 0:
                rep_acc_targets[rep_name] = (store_target / store_total) * rep_hours
    
    # ── Build rep map ─────────────────────────────────────────
    rep_map = {}
    for r in valid:
        login = str(r.get('user_login','')).lower().strip()
        rep = str(r.get('salesperson','')).strip()
        if not rep or rep.lower().strip() == 'admin': continue
        key = rep.upper()
        if key not in rep_map:
            rep_map[key] = {
                'name': rep, 'login': login,
                'store': str(r.get('store','')).strip(),
                'storeops_name': name_lookup.get(login,''),
                'prem_set': set(), 'byod_set': set(), 'upg_set': set(),
                'acc_gp': 0, 'setup_fee_gp': 0, 'acc_sales': 0, 'setup_fee_sales': 0, 'trade_ins': 0,
                'sales': []
            }
        entry = rep_map[key]
        entry['sales'].append(r)
        tid = str(r.get('trans_id','')).replace('.0','').strip()
        gp = safe_float(r.get('gp'))
        ext = safe_float(r.get('ext_price'))
        ct = str(r.get('contract_type','')).strip()
        dept = str(r.get('department','')).strip()
        cat = str(r.get('category','')).strip()
        product = str(r.get('product_desc','')).strip()
        
        _cls = classify_contract_type(ct)
        if _cls == 'byod': entry['byod_set'].add(tid)
        elif _cls == 'upgrade': entry['upg_set'].add(tid)
        elif _cls == 'premium': entry['prem_set'].add(tid)
        
        if dept == 'Ondigo':
            entry['acc_gp'] += gp
            entry['acc_sales'] += ext
        if 'Device Setup Charge' in product:
            entry['setup_fee_gp'] += gp
            entry['setup_fee_sales'] += ext
        
    # ── Include DLAR reps with no sales yet (other markets) ──────
    for d in dlar_rep:
        dname = str(d.get('rep_name','')).strip()
        if not dname: continue
        key = dname.upper()
        if key not in rep_map:
            rep_map[key] = {
                'name': dname, 'login': '',
                'store': str(d.get('store','') or ''),
                'storeops_name': '',
                'prem_set': set(), 'byod_set': set(), 'upg_set': set(),
                'acc_gp': 0, 'setup_fee_gp': 0, 'acc_sales': 0, 'setup_fee_sales': 0, 'trade_ins': 0,
                'sales': []
            }

    # ── Payment lookups ───────────────────────────────────────
    pay_by_login = {}  # login → {comm, reimb, mdf, chb}
    for p in pay_detail:
        login = str(p.get('rep_username','')).lower().strip()
        cat = str(p.get('category','')).strip()
        amt = safe_float(p.get('amount'))
        if login not in pay_by_login:
            pay_by_login[login] = {'comm':0,'reimb':0,'mdf':0,'chb':0,'trades':0}
        if cat == 'Commission': pay_by_login[login]['comm'] += amt
        elif cat == 'Re-imbursement': pay_by_login[login]['reimb'] += amt
        elif cat == 'MDF': pay_by_login[login]['mdf'] += amt
        elif cat == 'Chargeback': pay_by_login[login]['chb'] += amt
        # Count trade-ins keyed by login (rep_username), resolved to the rep via rep['login'] in the
        # per-rep loop — the SAME path comm/reimb already use (pay_by_login.get(rep['login'])). The old
        # code did rep_map.get(login.upper()), but rep_map is keyed by salesperson NAME, so it ~always
        # missed and trade-in spiff was silently $0 for every rep.
        if 'trade' in str(p.get('payment_type','')).lower():
            pay_by_login[login]['trades'] += 1
    
    # ── Calculate per rep ─────────────────────────────────────
    comm_rows = []
    
    for key, rep in rep_map.items():
        pa = len(rep['prem_set'])
        ba = len(rep['byod_set'])
        ua = len(rep['upg_set'])
        
        prem_comm    = pa * G['premium_flat']
        byod_comm    = ba * (G['byod_flat'] + G['byod_extra'])
        upg_comm     = ua * G['upgrade_flat']
        # Accessory + setup-fee commission paid on SALES (ext price), not GP — the B2B export's
        # accessory/setup lines often carry no cost so GP-based pay was near-zero (user decision
        # 2026-06-17). Rate unchanged (acc_rate / setup_fee_rate, default 10%). acc_gp is kept
        # for the accessory-target check above.
        acc_comm     = rep['acc_sales'] * G['acc_rate']
        setup_comm   = rep['setup_fee_sales'] * G['setup_rate']
        rep['trade_ins'] = pay_by_login.get(rep['login'], {}).get('trades', 0)
        trade_comm   = rep['trade_ins'] * G['trade_in_spiff']
        
        # ACIMA lease spiff — paid PER ACIMA TENDERED. The tender shows up as
        # "ACIMA" / "ACIMA Lease" / "Acima Leasing" in raw_sales.tender_type, never the
        # literal "financing" — the old exact-match made acima_count always 0 ($0 for everyone).
        # Count DISTINCT transactions (by trans_id), NOT line items — one ACIMA tender is one
        # transaction but many raw_sales line rows (this codebase's line-item multiplicity). Summing
        # rows over-paid: e.g. Ali had 20 acima line items → $500 when it was a handful of real
        # tenders. Mirrors the prem_set/byod_set/upg_set trans_id dedupe above.
        acima_count = len({
            str(s.get('trans_id', '')).replace('.0', '').strip()
            for s in rep['sales']
            if 'acima' in str(s.get('tender_type', '')).lower()
        })
        acima_comm = acima_count * G['acima_spiff']
        
        # Custom spiffs
        custom_comm = 0
        for cs in G['custom_spiffs']:
            if not cs.get('name') or not cs.get('rate'): continue
            cs_count = sum(
                1 for s in rep['sales']
                if str(s.get('category','')).lower() == cs['name'].lower()
                or str(s.get('department','')).lower() == cs['name'].lower()
            )
            custom_comm += cs_count * float(cs.get('rate', 0))
        
        subtotal = prem_comm + byod_comm + upg_comm + acc_comm + setup_comm + trade_comm + acima_comm + custom_comm
        
        # ── KPI Tier ───────────────────────────────────────────
        tier = 1.0
        kpi_vals = {}
        kpis_met = 0
        
        if not G['straight']:
            dr = dlar_rep_by_name.get(rep['name'].upper())
            store_num = str(rep['store']).split(' ')[0]
            sr = dlar_store_by_num.get(store_num)
            
            # Rep-level KPIs from Advocate report (already whole-number %)
            rep_atu      = safe_float(dr.get('atu_pct')) if dr else 0
            rep_protect  = safe_float(dr.get('device_insurance_pct') or dr.get('protect_pct')) if dr else 0
            rep_boostapp = safe_float(dr.get('boost_app_pct')) if dr else 0
            rep_byod     = safe_float(dr.get('byod_pct')) if dr else 0

            # Store-level KPIs from Elevate Go Store DLAR (rolled down to rep)
            st_familyplan = safe_float(sr.get('family_plan_pct')) if sr else 0
            st_tmr3       = safe_float(sr.get('tmr3')) if sr else 0
            st_aal        = safe_float(sr.get('aal_conversion')) if sr else 0

            if dr or sr:
                kpi_vals = {
                    'atu':         rep_atu,
                    'protect':     rep_protect,
                    'boostapp':    rep_boostapp,
                    'byod':        rep_byod,
                    'familyplan':  st_familyplan,
                    'tmr3':        st_tmr3,
                    'aal':         st_aal,
                }
                kpis_met = sum(1 for k,v in kpi_vals.items() if v >= KPI[k])
            
            if kpis_met >= G['t100']: tier = 1.0
            elif kpis_met >= G['t75']: tier = G['t75pct']
            else: tier = G['t50pct']
            
            # Accessory target check
            if G['acc_target_on']:
                rep_upper = rep['name'].upper()
                acc_target = rep_acc_targets.get(rep_upper, 0)
                if acc_target > 0 and rep['acc_gp'] < acc_target:
                    tier = min(tier, G['t75pct'])
        
        pr = pay_by_login.get(rep['login'], {})
        
        comm_rows.append({
            'period': period,
            'period_month': period_month,
            'period_year': period_year,
            'epay_salesperson': rep['name'],
            'storeops_name': rep['storeops_name'],
            'store': rep['store'],
            'tier': tier,
            'tier_source': 'dlar',
            'kpis_met': kpis_met,
            'total_kpis': 7,
            'kpi_values': kpi_vals,
            'premium_acts': pa,
            'byod_acts': ba,
            'upgrade_acts': ua,
            'premium_comm': prem_comm,
            'byod_comm': byod_comm,
            'upgrade_comm': upg_comm,
            'acc_comm': acc_comm,
            'setup_fee_comm': setup_comm,
            'trade_in_comm': trade_comm,
            'acima_comm': acima_comm,
            'custom_comm': custom_comm,
            'acc_target': rep_acc_targets.get(rep['name'].upper(), 0),
            'subtotal': subtotal,
            'total_payout': subtotal * tier,
            'boost_commission': pr.get('comm'),
            'boost_reimbursement': pr.get('reimb'),
            'calculated_by': 'api_v1',
        })
    
    return {
        'commissions': comm_rows,
        'period': period,
        'reps': len(comm_rows),
        'total_payout': sum(r['total_payout'] for r in comm_rows),
    }
