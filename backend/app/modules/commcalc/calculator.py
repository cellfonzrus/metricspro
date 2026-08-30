"""
CommCalc Commission Calculator
All business logic in Python - verified formulas
"""
from typing import Any
import re

from app.modules.commcalc import setup_fee_pay as _sfp

# ONE shared voided token set for pay + display (owner 2026-07-25) — see gp_report.VOID_TOKENS.
from app.modules.commcalc.gp_report import is_voided as _is_voided, VOID_TOKENS as _VOID_TOKENS

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
# 'idv' = an IDV (identity-verification) port activation, e.g. "Port with IDV" / "Port w/ IDV" /
# "Activation With IDV". OWNER RULING 2026-07-16: "Port with IDV" IS an activation. A bare CONTAINS
# on 'idv' catches the slash/casing drift too; it can't over-reach a BYOD/Upgrade label because the
# 'byod' and 'upgrade' checks in classify_contract_type return FIRST (so "BYOD Port with IDV" stays
# byod, "Upgrade with IDV" stays upgrade). 'port with idv' is kept explicit for intent/legibility.
_PREMIUM_KEYS = ('activation', 'port-in', 'port in', 'add a line', 'add-a-line', 'new line', ' aal', 'aal ',
                 'idv', 'port with idv')


def classify_contract_type(ct: str):
    """The ONE contract-type classifier shared by commissions, targets, and the sales report.
    Returns 'byod' | 'upgrade' | 'premium' | None. Tolerant of label drift: BYOD/Upgrade by CONTAINS,
    premium by the known set OR an activation keyword (incl. 'idv' — an IDV/identity-verification port
    activation such as "Port with IDV"; owner ruling 2026-07-16). None = not a phone-activation line
    (e.g. an accessory line, which carries a blank Contract Type)."""
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
    """Parse a report cell into a float, returning 0.0 for anything unparseable.

    CURRENCY-AWARE (owner 2026-08-30): b2bsoft's "Sales Transaction Details Legacy New with all columns"
    export writes money as TEXT — "$40.00", "1,234.56", accounting negatives "($42.50)". The old
    `float(v or 0)` threw on the "$"/comma and silently returned 0.0, so EVERY Ext Price / GP / Tax read
    as zero: the dollars vanished AND the daily feed's price-coverage guard saw "0 priced rows incoming"
    and refused the whole file every sweep — so nothing after the format switch imported at all. Strip a
    leading currency symbol, thousands commas and surrounding whitespace, and read (…) as a negative,
    before giving up. A genuinely non-numeric cell still returns 0.0 (unchanged); the only values whose
    result changes are currency-formatted strings that used to (wrongly) become 0."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        pass
    try:
        s = str(v).strip()
        if not s:
            return 0.0
        neg = s[0] == '(' and s[-1] == ')'   # accounting negative: ($42.50)
        if neg:
            s = s[1:-1].strip()
        for ch in ('$', '£', '€', ','):
            s = s.replace(ch, '')
        s = s.strip()
        if s in ('', '-', '.', '-.', '+'):
            return 0.0
        f = float(s)
        return -f if neg else f
    except (TypeError, ValueError):
        return 0.0


def safe_int(v):
    """Parse a report cell into a Python int, for a column typed INTEGER in Postgres.

    WHY THIS EXISTS (2026-08-09). Every mapper reached for safe_float, which returns a FLOAT even
    for a whole number ('1' -> 1.0). PostgREST serialises that as `1.0`, and Postgres rejects it
    against an integer column:
        invalid input syntax for type integer: "1.0"   (SQLSTATE 22P02)
    That killed the comp upload on ROW 0 — i.e. every row, not a data oddity — the moment the real
    comp mapper was wired into the manual upload path.

    Returns None for blank/unparseable input rather than 0, because on these reports an ABSENT
    quantity is not a quantity of zero, and the columns are nullable. Accepts the Excel float
    spellings the exports actually produce ('1', '1.0', 1.0, ' 2 '). A genuinely fractional value
    is NOT silently truncated — it returns None so the row lands with a NULL you can find, instead
    of quietly rounding a number somebody may later multiply by a rate.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat", "null"):
        return None
    try:
        f = float(s.replace(",", ""))
    except (TypeError, ValueError):
        return None
    i = int(f)
    return i if i == f else None


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
    name_map: list[dict],
    carrier_mode: str = 'boost'
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
        # The employee's share of the set-up fee COLLECTED. `payout_config.setup_fee_rate` remains the
        # source of truth for this (Boost) engine and still wins, so Boost pay is unchanged; the
        # per-carrier `setup_fee_pay` config (mig 263) is what the PLAN engine reads for every other
        # carrier. `or 0.10` is preserved verbatim, including its known quirk that a stored 0 falls back
        # to 10% — changing that here would silently move money for any tenant who stored a 0, so it is
        # REPORTED in the park record instead of fixed in the same breath as everything else.
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
    # Configurable accessory classification (mig 092/093): POS departments/categories/product-keywords
    # that are accessories. Empty → the historical default department 'Ondigo', so pay is unchanged
    # until it's configured. Product keywords cover POS feeds that carry no dept/category.
    _acc_depts = {str(d).strip().lower() for d in (cfg.get('accessory_departments') or []) if str(d).strip()}
    _acc_cats = {str(c).strip().lower() for c in (cfg.get('accessory_categories') or []) if str(c).strip()}
    _acc_kws = {str(k).strip().lower() for k in (cfg.get('accessory_product_keywords') or []) if str(k).strip()}
    # DEVICE SET-UP FEE recognition (owner 2026-08-01). Historically this was the hard-coded literal
    # `'Device Setup Charge' in product` RIGHT HERE on the pay path, while the REPORT path
    # (router._is_setup_fee, mig 217) read the tenant's own `accessory_config.setup_fee_keywords`. So a
    # tenant who edited that list moved every report and NOT their pay. Both now read the SAME list.
    # BYTE-IDENTICAL BY CONSTRUCTION: the default mode is `legacy_case_sensitive`, which reproduces the
    # old predicate exactly, and the default list IS ['Device Setup Charge']. A tenant only gets the
    # looser (report-shaped) matching by explicitly choosing it — because unifying two classifiers that
    # disagree on case is a MONEY change (see setup_fee_pay.divergence(), which measures it first).
    _setup_kws = _sfp.normalize_keywords(cfg.get('setup_fee_keywords'))
    _setup_mode = str(cfg.get('setup_fee_match_mode') or 'legacy_case_sensitive').strip().lower()
    if not _acc_depts and not _acc_cats and not _acc_kws:
        _acc_depts = {'ondigo'}
    # Configurable ACIMA-lease tender (mig 094): which Tender Type value(s) mark an ACIMA lease
    # (substring, e.g. 'financing'). Empty → the historical default substring 'acima'.
    _acima_tenders = {str(t).strip().lower() for t in (cfg.get('acima_tenders') or []) if str(t).strip()} or {'acima'}

    def _is_acc(dept, cat, product=''):
        d = (dept or '').strip().lower()
        c = (cat or '').strip().lower()
        if d in _acc_depts:
            return True
        if c and c in _acc_cats:
            return True
        if _acc_kws:
            p = (product or '').strip().lower()
            if p and any(k in p for k in _acc_kws):
                return True
        return False
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
    # VOIDED (owner 2026-07-25): the SHARED token set — 'YES' plus the 'true'/'1'/'void'/'voided'
    # variants every display surface already excluded. A feed emitting a variant used to be PAID here
    # while being excluded from the Sales Report it reconciles against.
    valid = [
        r for r in sales
        if not _is_voided(r.get('voided'))
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
        
        if _is_acc(dept, cat, product):
            entry['acc_gp'] += gp
            entry['acc_sales'] += ext
        if _sfp.is_setup_fee(product, _setup_kws, _setup_mode):
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
    # ── Non-Boost carriers: pay ONLY from configurable Commission Plans / Payout Schedules ──────
    # The Boost KPI-tier + flat-spiff model does NOT apply to other carriers (e.g. Total). Emit a
    # ZEROED base row per rep found in this period's sales so (a) un-planned reps earn $0 instead of
    # Boost pay, and (b) no Boost line-items/tier are shown. _apply_new_engines() then REPLACES
    # total_payout with each rep's assigned plan (+ installments). Boost tenants never reach this
    # branch (carrier_mode defaults to 'boost'), so their pay stays byte-identical.
    if carrier_mode != 'boost':
        plan_rows = []
        for key, rep in rep_map.items():
            plan_rows.append({
                'period': period,
                'period_month': period_month,
                'period_year': period_year,
                'epay_salesperson': rep['name'],
                'storeops_name': rep['storeops_name'],
                'store': rep['store'],
                'tier': 1.0,
                'tier_source': 'plan',
                'kpis_met': 0,
                'total_kpis': 0,
                'kpi_values': {},
                'premium_acts': len(rep['prem_set']),
                'byod_acts': len(rep['byod_set']),
                'upgrade_acts': len(rep['upg_set']),
                'premium_comm': 0,
                'byod_comm': 0,
                'upgrade_comm': 0,
                'acc_comm': 0,
                'setup_fee_comm': 0,
                'trade_in_comm': 0,
                'acima_comm': 0,
                'custom_comm': 0,
                'acc_target': 0,
                'subtotal': 0,
                'total_payout': 0,
                'boost_commission': None,
                'boost_reimbursement': None,
                'calculated_by': 'api_v1',
            })
        return {
            'commissions': plan_rows,
            'period': period,
            'reps': len(plan_rows),
            'total_payout': 0.0,
        }

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
            if any(at in str(s.get('tender_type', '')).lower() for at in _acima_tenders)
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
