"""
GP Report Calculator — Store-level P&L
19 columns: Acc GP, Setup GP, Phone Sales, Plan GP, Other,
Commission, Re-imb, MDF, Chargebacks, Unmapped,
MI, ATU, Total Rev, −Rep Pay, −Expenses, −Phone Cost,
Net Profit, Excl. MDF
"""
from typing import Any

DEVICE_DEPTS = {'Android - XP', 'IPHONE - XP', 'TABLET - XP'}
ONDIGO_DEPT = 'Ondigo'

def safe_float(v) -> float:
    try: return float(v or 0)
    except: return 0.0

def street_num(addr: str) -> str:
    """Extract street number from address for matching."""
    return str(addr or '').strip().split(' ')[0]

def calc_gp_report(
    sales: list[dict],
    pay_detail: list[dict],
    mi_rows: list[dict],
    rep_commissions: list[dict],
    expenses: list[dict],
    catalog: list[dict],
    store_mapping: list[dict],
    period: str,
    comp_rows: list[dict] = None,
) -> dict:
    """
    Returns store_rows (by store) and rep_rows (by rep).
    """
    # ── Catalog cost map ──────────────────────────────────────────
    cat_cost: dict[str, float] = {}
    for c in catalog:
        pid = c.get('product_id')
        if pid:
            cat_cost[str(int(float(pid)))] = safe_float(c.get('cost'))

    # ── MI/ATU by salesforce_id ───────────────────────────────────
    mi_by_sfid: dict[str, dict] = {}
    for m in mi_rows:
        sfid = str(m.get('salesforce_id') or '').strip()
        if sfid:
            if sfid not in mi_by_sfid:
                mi_by_sfid[sfid] = {'mi': 0.0, 'atu': 0.0}
            mi_by_sfid[sfid]['mi']  += safe_float(m.get('actual_mi_payout'))
            mi_by_sfid[sfid]['atu'] += safe_float(m.get('actual_atu_payout'))

    # ── Store mapping: street_num → {sfid, market, code} ─────────
    store_by_num: dict[str, dict] = {}
    for s in store_mapping:
        num = street_num(s.get('store_address', ''))
        if num:
            store_by_num[num] = s

    # ── Payment detail bucketed by store street_num ───────────────
    pay_by_num: dict[str, dict] = {}
    for r in pay_detail:
        num = street_num(r.get('business_address', ''))
        if not num: continue
        if num not in pay_by_num:
            pay_by_num[num] = {'comm': 0, 'reimb': 0, 'mdf': 0, 'chb': 0, 'unmapped': 0}
        cat = str(r.get('category') or '').strip()
        amt = safe_float(r.get('amount'))
        if   cat == 'Commission':     pay_by_num[num]['comm']    += amt
        elif cat == 'Re-imbursement': pay_by_num[num]['reimb']   += amt
        elif cat == 'MDF':            pay_by_num[num]['mdf']     += amt
        elif cat == 'Chargeback':     pay_by_num[num]['chb']     += amt
        else:                         pay_by_num[num]['unmapped'] += amt

    # ── Comp report bucketed by store street_num ──────────────────
    comp_by_num: dict[str, dict] = {}
    for r in (comp_rows or []):
        num = street_num(r.get('business_address', ''))
        if not num: continue
        if num not in comp_by_num:
            comp_by_num[num] = {'comm': 0, 'reimb': 0, 'mdf': 0}
        ct = str(r.get('compensation_type') or '').lower()
        amt = safe_float(r.get('payment_amount'))
        if 'reimbursement' in ct or 'rebate' in ct:
            comp_by_num[num]['reimb'] += amt
        elif 'mdf' in ct:
            comp_by_num[num]['mdf'] += amt
        else:
            comp_by_num[num]['comm'] += amt

    # ── Rep pay by store ──────────────────────────────────────────
    rep_pay_by_store: dict[str, float] = {}
    for r in rep_commissions:
        store = str(r.get('store') or '').strip()
        num = street_num(store)
        if num:
            rep_pay_by_store[num] = rep_pay_by_store.get(num, 0) + safe_float(r.get('total_payout'))

    # ── Expenses by store_code ────────────────────────────────────
    exp_by_code: dict[str, float] = {}
    for e in expenses:
        code = str(e.get('store_code') or '').strip()
        if code:
            exp_by_code[code] = exp_by_code.get(code, 0) + safe_float(e.get('amount'))

    # ── Sales grouped by store ────────────────────────────────────
    by_store: dict[str, list] = {}
    for r in sales:
        store = str(r.get('store') or '').strip()
        if not store: continue
        if store not in by_store:
            by_store[store] = []
        by_store[store].append(r)

    # ── Include ALL mapped stores even with no sales ─────────────
    for s in store_mapping:
        if not s.get('is_active', True): continue
        addr = str(s.get('store_address') or '').strip()
        if not addr: continue
        num = street_num(addr)
        if num and not any(street_num(k) == num for k in by_store.keys()):
            by_store[addr] = []

    # ── Build store rows ──────────────────────────────────────────
    store_rows = []
    for store, rows in by_store.items():
        num = street_num(store)
        sm = store_by_num.get(num, {})
        sfid = str(sm.get('salesforce_id') or '').strip()
        market = str(sm.get('market') or 'Boost').strip()
        store_code = str(sm.get('store_code') or '').strip()

        acc_gp    = sum(safe_float(r.get('gp')) for r in rows if str(r.get('department','')).strip() == ONDIGO_DEPT)
        setup_gp  = sum(safe_float(r.get('gp')) for r in rows if 'Device Setup Charge' in str(r.get('product_desc','')))
        phone_sales = sum(safe_float(r.get('ext_price')) for r in rows if str(r.get('department','')).strip() in DEVICE_DEPTS)
        plan_gp   = sum(safe_float(r.get('gp')) for r in rows
                        if not str(r.get('department','')).strip()
                        and str(r.get('department','')).strip() not in DEVICE_DEPTS
                        and str(r.get('department','')).strip() != ONDIGO_DEPT)
        other_gp  = sum(safe_float(r.get('gp')) for r in rows
                        if str(r.get('department','')).strip() not in {ONDIGO_DEPT, *DEVICE_DEPTS}
                        and 'Device Setup Charge' not in str(r.get('product_desc',''))
                        and str(r.get('department','')).strip() != '')

        pay = pay_by_num.get(num, {})
        comm_recv  = pay.get('comm', 0)
        reimb      = pay.get('reimb', 0)
        mdf        = pay.get('mdf', 0)
        chargeback = pay.get('chb', 0)
        unmapped   = pay.get('unmapped', 0)

        comp = comp_by_num.get(num, {})
        comp_comm  = comp.get('comm', 0)
        comp_reimb = comp.get('reimb', 0)
        comp_mdf   = comp.get('mdf', 0)

        mi_data    = mi_by_sfid.get(sfid, {'mi': 0, 'atu': 0}) if sfid else {'mi': 0, 'atu': 0}
        mi_amt     = mi_data['mi']
        atu_amt    = mi_data['atu']

        total_rev  = acc_gp + setup_gp + phone_sales + plan_gp + other_gp + comm_recv + reimb + mdf + chargeback + unmapped + mi_amt + atu_amt
        rep_pay    = rep_pay_by_store.get(num, 0)
        exp_total  = exp_by_code.get(store_code, 0)
        net_phone_cost = phone_sales + reimb  # cash from customer + Boost reimbursement

        net_profit     = total_rev - rep_pay - exp_total - net_phone_cost
        net_excl_mdf   = net_profit - mdf

        store_rows.append({
            'store': store, 'store_code': store_code, 'market': market,
            'acc_gp': acc_gp, 'setup_gp': setup_gp, 'phone_sales': phone_sales,
            'plan_gp': plan_gp, 'other_gp': other_gp,
            'comm': comm_recv, 'reimb': reimb, 'mdf': mdf,
            'comp_comm': comp_comm, 'comp_reimb': comp_reimb, 'comp_mdf': comp_mdf,
            'chargeback': chargeback, 'unmapped': unmapped,
            'mi': mi_amt, 'atu': atu_amt,
            'total_rev': total_rev, 'rep_pay': rep_pay,
            'exp_total': exp_total, 'net_phone_cost': net_phone_cost,
            'net_profit': net_profit, 'net_excl_mdf': net_excl_mdf,
        })

    # ── Build rep rows ────────────────────────────────────────────
    by_rep: dict[str, list] = {}
    for r in sales:
        rep = str(r.get('salesperson') or '').strip()
        if not rep or rep.lower().strip() == 'admin': continue
        if rep not in by_rep: by_rep[rep] = []
        by_rep[rep].append(r)

    rep_rows = []
    for rep, rows in by_rep.items():
        acc_gp   = sum(safe_float(r.get('gp')) for r in rows if str(r.get('department','')).strip() == ONDIGO_DEPT)
        setup_gp = sum(safe_float(r.get('gp')) for r in rows if 'Device Setup Charge' in str(r.get('product_desc','')))
        phone_s  = sum(safe_float(r.get('ext_price')) for r in rows if str(r.get('department','')).strip() in DEVICE_DEPTS)
        plan_gp  = sum(safe_float(r.get('gp')) for r in rows
                       if not str(r.get('department','')).strip()
                       and str(r.get('department','')).strip() not in DEVICE_DEPTS)

        comm_row = next((c for c in rep_commissions if c.get('epay_salesperson') == rep), {})

        rep_rows.append({
            'rep': rep,
            'storeops_name': comm_row.get('storeops_name', ''),
            'store': next((r.get('store','') for r in rows), ''),
            'acc_gp': acc_gp, 'setup_gp': setup_gp,
            'phone_sales': phone_s, 'plan_gp': plan_gp,
            'comm_earned': safe_float(comm_row.get('total_payout')),
        })

    store_rows.sort(key=lambda x: x['net_profit'], reverse=True)
    rep_rows.sort(key=lambda x: x['acc_gp'], reverse=True)

    totals = {
        'acc_gp': sum(r['acc_gp'] for r in store_rows),
        'setup_gp': sum(r['setup_gp'] for r in store_rows),
        'phone_sales': sum(r['phone_sales'] for r in store_rows),
        'comm': sum(r['comm'] for r in store_rows),
        'reimb': sum(r['reimb'] for r in store_rows),
        'mdf': sum(r['mdf'] for r in store_rows),
        'comp_comm': sum(r['comp_comm'] for r in store_rows),
        'comp_reimb': sum(r['comp_reimb'] for r in store_rows),
        'comp_mdf': sum(r['comp_mdf'] for r in store_rows),
        'chargeback': sum(r['chargeback'] for r in store_rows),
        'mi': sum(r['mi'] for r in store_rows),
        'atu': sum(r['atu'] for r in store_rows),
        'total_rev': sum(r['total_rev'] for r in store_rows),
        'rep_pay': sum(r['rep_pay'] for r in store_rows),
        'exp_total': sum(r['exp_total'] for r in store_rows),
        'net_profit': sum(r['net_profit'] for r in store_rows),
        'net_excl_mdf': sum(r['net_excl_mdf'] for r in store_rows),
    }

    return {'store_rows': store_rows, 'rep_rows': rep_rows, 'totals': totals, 'period': period}
