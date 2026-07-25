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
GP_CATEGORIES = {'device', 'accessory', 'plan', 'other', 'exclude'}

def _dept_classifier(gp_category_map):
    """Return a fn department_label -> GP category. The map (commcalc.gp_category_map, mig 069) is a set
    of OVERRIDES layered on the built-in Boost defaults — so an EMPTY/None map reproduces the original
    hard-coded buckets byte-for-byte (device = Android/IPHONE/TABLET-XP, accessory = Ondigo, blank = plan,
    everything else = other). A tenant maps only the labels that differ; '' overrides blank-department rows."""
    overrides = {}
    for row in (gp_category_map or []):
        d = str(row.get('department') or '').strip()
        c = str(row.get('category') or '').strip().lower()
        if c in GP_CATEGORIES:
            overrides[d] = c
    def classify(dept) -> str:
        d = str(dept or '').strip()
        if d in overrides:        return overrides[d]
        if d in DEVICE_DEPTS:     return 'device'
        if d == ONDIGO_DEPT:      return 'accessory'
        if d == '':               return 'plan'
        return 'other'
    return classify

# ── VOIDED: the ONE token set shared by the pay path and every display surface ────────────────────
# Owner-approved 2026-07-25. The money path used to skip a line only when voided == 'YES' (upper/strip),
# while every display surface (Sales Report / GP / the shared aggregation / sales-recon / what-if) already
# treated 'true' / '1' / 'void' / 'voided' as voided too. A POS feed writing any of those variants produced
# a line that was PAID but excluded from the reports it should reconcile against. One constant, one
# predicate, imported by both sides so they can never drift again.
#   NOTE FOR THE MERGE: agent/commission/catalog-followups also lands `gp_report.VOID_TOKENS` with this
#   exact name + value; router.py aliases it as `_VOID_TOKENS` so every pre-existing display call site is
#   untouched. If both branches merge, keep ONE definition here.
VOID_TOKENS = ('true', 'yes', '1', 'voided', 'void')


def is_voided(v) -> bool:
    """True when a raw_sales/daily_sales_feed `voided` cell means VOIDED. Case/space-insensitive over
    VOID_TOKENS. Blank / None / any other value is NOT voided (so an un-populated column never hides a
    sale). PURE."""
    return str(v or "").strip().lower() in VOID_TOKENS


def safe_float(v) -> float:
    try: return float(v or 0)
    except: return 0.0

# ── THE canonical "is this a countable sale line?" rules ────────────────────────────────────────────
# The void-token SINGLE source of truth is the VOID_TOKENS definition above (with is_voided): router.py
# imports it from here (it used to hold its own literal copy next to _sales_cell_agg) so the display
# aggregation and the GP transparency map can never drift apart. Values are byte-identical to the
# shipped router literal. (Merge dedupe 2026-07-25: this section's re-declaration was removed.)


def countable_sale_skip_reason(row) -> str:
    """'' when the line is a countable sale; otherwise WHY it isn't — the EXACT three skip rules
    router._sales_cell_agg applies (voided / trans_type == 'Return' / no attributable rep):
      'voided'       — voided flag in VOID_TOKENS
      'return'       — trans_type == 'Return'
      'unattributed' — blank salesperson, or the 'admin' pseudo-rep
    Rows that simply do not CARRY the column (a narrowed select) are countable, exactly as before."""
    if str(row.get('voided') or '').strip().lower() in VOID_TOKENS:
        return 'voided'
    if str(row.get('trans_type') or '').strip() == 'Return':
        return 'return'
    rep = str(row.get('salesperson') or '').strip()
    if not rep or rep.lower() == 'admin':
        return 'unattributed'
    return ''

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
    gp_category_map: list[dict] = None,
    resolve_store_code=None,
) -> dict:
    """
    Returns store_rows (by store) and rep_rows (by rep).
    gp_category_map (commcalc.gp_category_map): optional per-tenant department→GP-category overrides;
    None/empty = the built-in Boost buckets (byte-identical to before this was added).
    resolve_store_code: optional callable raw-store-string -> canonical store_code, used ONLY to attach
    expenses (keyed by the org's storeops store_code) when the store_mapping street-number join yields no
    store_code — i.e. a tenant with no commcalc.store_mapping. Gated on an empty derived store_code so the
    house (store_mapping populated) is byte-identical. None = disabled (pre-existing behavior).
    """
    classify = _dept_classifier(gp_category_map)
    # ── Catalog cost map (product cost lookup) ────────────────────
    # Keyed by product_id (HOUSE format — byte-identical) PLUS, ADDITIVELY, UPC / SKU / normalized
    # product_desc so the TOTAL/luxelink UPC-keyed catalog (NO Product ID; migs 230/231) also yields a cost.
    # Additive keys only — the product_id path is untouched, so the house Phone-Cost lookup never changes.
    import re as _re_gp
    def _nd(s):
        return _re_gp.sub(r'\s+', ' ', str(s or '').strip().lower())
    def _ck(v):
        # Trailing-'.0' only (Excel numeric-cell artifact) — NOT every '.0' (preserves 'V2.0-CASE').
        s = str(v or '').strip()
        if s.endswith('.0'):
            s = s[:-2]
        return s.strip().lower()
    cat_cost: dict[str, float] = {}
    cat_cost_upc: dict[str, float] = {}
    cat_cost_sku: dict[str, float] = {}
    cat_cost_desc: dict[str, float] = {}
    for c in catalog:
        cost = safe_float(c.get('cost'))
        pid = c.get('product_id')
        if pid:
            try:
                cat_cost[str(int(float(pid)))] = cost
            except (TypeError, ValueError):
                pass
        u = _ck(c.get('upc'))
        if u:
            cat_cost_upc[u] = cost
        s = _ck(c.get('sku'))
        if s:
            cat_cost_sku[s] = cost
        d = _nd(c.get('product_desc'))
        if d:
            cat_cost_desc.setdefault(d, cost)

    def _catalog_cost_for(row):
        """Product cost from the catalog for a sale line: product_id → UPC → SKU → normalized desc.
        0.0 when unknown. Additive helper (currently exposed for the config-gated accessory-GP source /
        a future Phone-Cost wiring — house net_phone_cost formula unchanged)."""
        pid = row.get('product_id')
        if pid:
            try:
                k = str(int(float(pid)))
                if k in cat_cost:
                    return cat_cost[k]
            except (TypeError, ValueError):
                pass
        u = _ck(row.get('upc'))
        if u and u in cat_cost_upc:
            return cat_cost_upc[u]
        s = _ck(row.get('sku'))
        if s and s in cat_cost_sku:
            return cat_cost_sku[s]
        d = _nd(row.get('product_desc'))
        if d and d in cat_cost_desc:
            return cat_cost_desc[d]
        return 0.0

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

        acc_gp    = sum(safe_float(r.get('gp')) for r in rows if classify(r.get('department')) == 'accessory')
        setup_gp  = sum(safe_float(r.get('gp')) for r in rows if 'Device Setup Charge' in str(r.get('product_desc','')))
        phone_sales = sum(safe_float(r.get('ext_price')) for r in rows if classify(r.get('department')) == 'device')
        plan_gp   = sum(safe_float(r.get('gp')) for r in rows if classify(r.get('department')) == 'plan')
        other_gp  = sum(safe_float(r.get('gp')) for r in rows
                        if classify(r.get('department')) == 'other'
                        and 'Device Setup Charge' not in str(r.get('product_desc','')))

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
        # Expenses are keyed by the org's storeops store_code (the Expenses page picks from storeops.stores).
        # When the store_mapping street-number join yielded a store_code, use it (house — byte-identical).
        # When it did NOT (a tenant with no commcalc.store_mapping → store_code=''), resolve the raw store
        # string to the storeops store_code so the tenant's configured expenses attach. This changes ONLY
        # exp_total for rows that had no store_code; the row's displayed store_code/market are untouched.
        exp_code   = store_code or (str(resolve_store_code(store) or '').strip() if resolve_store_code else '')
        exp_total  = exp_by_code.get(exp_code, 0)
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
        acc_gp   = sum(safe_float(r.get('gp')) for r in rows if classify(r.get('department')) == 'accessory')
        setup_gp = sum(safe_float(r.get('gp')) for r in rows if 'Device Setup Charge' in str(r.get('product_desc','')))
        phone_s  = sum(safe_float(r.get('ext_price')) for r in rows if classify(r.get('department')) == 'device')
        plan_gp  = sum(safe_float(r.get('gp')) for r in rows if classify(r.get('department')) == 'plan')

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

    # ── GP bucket TRANSPARENCY (owner 2026-07-24: "'Other' does not detail any information") ──────────
    # Per-GP-bucket DEPARTMENT composition over ALL sale lines — so the GP page can show WHAT is inside
    # 'Other' (and every bucket): which departments landed there, how many lines, and their ext_price / gp $.
    # This is the map for the owner to send unmapped departments to gp_category_map. Pure display; no number
    # moves. `unmapped_departments` = the 'other'-bucket departments ranked by $ (the "map them →" banner).
    # ⑦ (Gate-1 follow-up 2026-07-25): the transparency map counts ONLY countable sale lines — the SAME
    # three skip rules the shared display aggregation applies (router._sales_cell_agg: voided / Return /
    # unattributed), so `lines`/`ext_price`/`gp` here tie out to the agg path instead of silently including
    # voided + returned + admin lines. Nothing is HIDDEN: what a rule skipped is still tallied per department
    # in `excluded_*` (and org-wide, by reason, in `bucket_composition_excluded`), and a department whose
    # lines were ALL skipped still gets a row (lines=0) so it can never disappear from the "map them" banner.
    # DISPLAY/TRANSPARENCY ONLY — the store_rows / rep_rows / totals money columns above are untouched and
    # still count every line, so their $ can legitimately exceed the composition $ by `excluded_*`.
    comp: dict[str, dict[str, dict]] = {}
    excluded = {k: {'lines': 0, 'ext_price': 0.0, 'gp': 0.0}
                for k in ('voided', 'return', 'unattributed')}
    for r in sales:
        cat = classify(r.get('department'))
        dept = str(r.get('department') or '').strip() or '(blank)'
        d = comp.setdefault(cat, {}).setdefault(dept, {'department': dept, 'lines': 0, 'ext_price': 0.0,
                                                       'gp': 0.0, 'excluded_lines': 0,
                                                       'excluded_ext_price': 0.0, 'excluded_gp': 0.0})
        ext = safe_float(r.get('ext_price'))
        gp = safe_float(r.get('gp'))
        skip = countable_sale_skip_reason(r)
        if skip:
            d['excluded_lines'] += 1
            d['excluded_ext_price'] += ext
            d['excluded_gp'] += gp
            e = excluded[skip]
            e['lines'] += 1
            e['ext_price'] += ext
            e['gp'] += gp
            continue
        d['lines'] += 1
        d['ext_price'] += ext
        d['gp'] += gp

    # ⑥ (Gate-1 follow-up 2026-07-25): ONE deterministic sort key for every row. The shipped key was
    # `-abs(gp) if gp else -ext_price`, which compared two DIFFERENT magnitudes in the same ordering — a
    # $10,000 zero-GP department outranked a $5-GP one purely because it fell into the other mode. Now:
    # |GP| (the P&L magnitude the bucket is about) → |Ext Price| (what actually separates rows whose GP is
    # 0 because the POS carries cost == price) → department name, so ordering is total, stable and
    # reproducible across requests regardless of dict insertion order.
    def _comp_sort_key(x):
        return (-abs(x['gp']), -abs(x['ext_price']), x['department'].lower())

    bucket_composition = {}
    for cat, depts in comp.items():
        rows_c = sorted(depts.values(), key=_comp_sort_key)
        for x in rows_c:
            x['ext_price'] = round(x['ext_price'], 2)
            x['gp'] = round(x['gp'], 2)
            x['excluded_ext_price'] = round(x['excluded_ext_price'], 2)
            x['excluded_gp'] = round(x['excluded_gp'], 2)
        bucket_composition[cat] = rows_c
    unmapped_departments = bucket_composition.get('other', [])
    for e in excluded.values():
        e['ext_price'] = round(e['ext_price'], 2)
        e['gp'] = round(e['gp'], 2)
    excluded['total'] = {'lines': sum(e['lines'] for e in excluded.values()),
                         'ext_price': round(sum(e['ext_price'] for e in excluded.values()), 2),
                         'gp': round(sum(e['gp'] for e in excluded.values()), 2)}

    return {'store_rows': store_rows, 'rep_rows': rep_rows, 'totals': totals, 'period': period,
            'bucket_composition': bucket_composition, 'unmapped_departments': unmapped_departments,
            'bucket_composition_excluded': excluded,
            'bucket_composition_basis': 'countable sale lines (voided / Return / unattributed excluded — '
                                        'the shared _sales_cell_agg skip rules)'}
