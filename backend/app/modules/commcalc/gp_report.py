"""
GP Report Calculator — Store-level P&L
19 columns: Acc GP, Setup GP, Phone Sales, Plan GP, Other,
Commission, Re-imb, MDF, Chargebacks, Unmapped,
MI, ATU, Total Rev, −Rep Pay, −Expenses, −Phone Cost,
Net Profit, Excl. MDF
"""
from typing import Any

# Commission LEG attribution (1st month vs M2-M12) — a PURE leaf module with no app imports of its
# own, so this file stays the dependency-free calculator it has always been (calculator.py imports
# gp_report, so anything reaching back into calculator here would be a cycle).
from app.modules.commcalc import commission_legs as _legs

DEVICE_DEPTS = {'Android - XP', 'IPHONE - XP', 'TABLET - XP'}
ONDIGO_DEPT = 'Ondigo'
GP_CATEGORIES = {'device', 'accessory', 'plan', 'other', 'exclude'}

def _gp_overrides(gp_category_map):
    """{department: gp-bucket} from commcalc.gp_category_map rows (mig 069) — the ONE override-parsing
    rule, shared by the legacy department classifier and the config-mode per-line classifier."""
    overrides = {}
    for row in (gp_category_map or []):
        d = str(row.get('department') or '').strip()
        c = str(row.get('category') or '').strip().lower()
        if c in GP_CATEGORIES:
            overrides[d] = c
    return overrides


def _dept_classifier(gp_category_map):
    """Return a fn department_label -> GP category. The map (commcalc.gp_category_map, mig 069) is a set
    of OVERRIDES layered on the built-in Boost defaults — so an EMPTY/None map reproduces the original
    hard-coded buckets byte-for-byte (device = Android/IPHONE/TABLET-XP, accessory = Ondigo, blank = plan,
    everything else = other). A tenant maps only the labels that differ; '' overrides blank-department rows."""
    overrides = _gp_overrides(gp_category_map)
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

def _leg_ladder_add(ladder, prefix, leg_month, amt):
    """Tally one dollar amount into the month-of-life LADDER (M1, M2, M3 … / 'unknown') for a source.

    The ladder is what makes the owner's 3MR/6MR question answerable: Boost's bounties pay Month 1..6
    legs and a leg only lands if the subscriber survived that month, so `comm` at leg 2/3 IS the money
    3-month retention produced and legs 4–6 are the 6-month tail. Display only — the two/three bucket
    columns above are what the money identity is proven on. PURE."""
    key = 'unknown' if leg_month in (None, '', 'unknown') else str(int(leg_month))
    d = ladder.setdefault(prefix, {})
    d[key] = round(d.get(key, 0.0) + safe_float(amt), 2)


def _leg_ladder_merge(ladder, prefix, part):
    """Fold a per-store/per-salesforce-id ladder into the report-wide one. Called from the STORE-ROW
    loop, never from the indexing pass — so the ladder totals track exactly the money that actually
    lands in a store row's columns (a payment address with no matching store, or an MI salesforce_id
    the store map doesn't know, is dropped from the money columns and must be dropped here too, or the
    ladder would out-total the very column it explains). PURE."""
    if not part:
        return
    d = ladder.setdefault(prefix, {})
    for k, v in part.items():
        d[k] = round(d.get(k, 0.0) + safe_float(v), 2)


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
    config_classify: dict = None,
    ma_income: dict = None,
    leg_classify=None,
) -> dict:
    """
    Returns store_rows (by store) and rep_rows (by rep).
    gp_category_map (commcalc.gp_category_map): optional per-tenant department→GP-category overrides;
    None/empty = the built-in Boost buckets (byte-identical to before this was added).
    resolve_store_code: optional callable raw-store-string -> canonical store_code, used ONLY to attach
    expenses (keyed by the org's storeops store_code) when the store_mapping street-number join yields no
    store_code — i.e. a tenant with no commcalc.store_mapping. Gated on an empty derived store_code so the
    house (store_mapping populated) is byte-identical. None = disabled (pre-existing behavior).
    config_classify (mig 250, per-org OPT-IN): {'is_accessory': fn(row)->bool, 'box_departments': set}.
    When given, per-LINE classification runs accessory-first through the org's Sales-Report accessory rule
    (department+category+keyword+catalog — POS feeds like luxelink's are ambiguous by department alone),
    then the explicit gp_category_map department overrides, then box departments => device, blank => plan,
    else other. None (default, and every org with apply_to_gp false) = the legacy department-only
    classifier, byte-identical.
    ma_income: {'comm': $, 'atu': $} of VidaPay/MA carrier income for ePay-less orgs (router computes it
    only when raw_payment_detail is EMPTY for the period). Lands on ONE company-wide row — MA rows carry
    no store address (same posture as the P&L's owner-approved MA fallback). None = no row (house).
    May also carry 'components' ({raw_ma_commission column: raw summed value}) and 'component_list' (the
    exact column list the router's own total was built from) so the commission-leg split of that income
    is exact rather than re-derived.
    leg_classify (owner directive 2026-08-04): a commission_legs.LegClassifier that attributes RECEIVED
    commission money to the 1st-month leg vs the M2–M12 trailing legs. None = the pure code-default
    classifier. This is a DECOMPOSITION ONLY: it adds `*_m1` / `*_m2_12` / `*_unsplit` companions to
    `comm`, `comp_comm`, `mi` and `atu`; each trio sums to its existing column to the cent, and no
    existing money column, total_rev, rep_pay, net_profit or bucket classification changes at all.
    """
    if leg_classify is None:
        leg_classify = _legs.default_classifier()
    leg_ladder: dict[str, dict] = {}
    classify = _dept_classifier(gp_category_map)
    if config_classify is None:
        def classify_row(r) -> str:
            return classify(r.get('department'))
    else:
        _is_acc = config_classify.get('is_accessory') or (lambda _r: False)
        _box = {str(b).strip() for b in (config_classify.get('box_departments') or ())}
        _ovr = _gp_overrides(gp_category_map)
        def classify_row(r) -> str:
            # Accessory FIRST (the whole point: category-level discrimination the dept map can't express),
            # then the tenant's explicit department overrides, then box departments = device, blank = plan.
            if _is_acc(r):
                return 'accessory'
            d = str(r.get('department') or '').strip()
            if d in _ovr:
                return _ovr[d]
            if d in _box:
                return 'device'
            if d == '':
                return 'plan'
            return 'other'
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
                mi_by_sfid[sfid] = {'mi': 0.0, 'atu': 0.0,
                                    'mi_legs': _legs.empty_split(), 'atu_legs': _legs.empty_split(),
                                    'mi_ladder': {}, 'atu_ladder': {}}
            _mi = safe_float(m.get('actual_mi_payout'))
            _atu = safe_float(m.get('actual_atu_payout'))
            mi_by_sfid[sfid]['mi']  += _mi
            mi_by_sfid[sfid]['atu'] += _atu
            # Residual is the ONE source that carries a real activation DATE, so it splits on the
            # owner's literal rule: activated in the report month = 1st month, earlier = M2–M12.
            # No mi_activation_date on the row (or the column not selected) -> honest 'unsplit'.
            _b, _leg, _ = leg_classify.activation(period, m.get('mi_activation_date'))
            mi_by_sfid[sfid]['mi_legs'][_b]  += _mi
            mi_by_sfid[sfid]['atu_legs'][_b] += _atu
            _leg_ladder_add(mi_by_sfid[sfid]['mi_ladder'], 'l', _leg, _mi)
            _leg_ladder_add(mi_by_sfid[sfid]['atu_ladder'], 'l', _leg, _atu)

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
            pay_by_num[num] = {'comm': 0, 'reimb': 0, 'mdf': 0, 'chb': 0, 'unmapped': 0,
                               'comm_legs': _legs.empty_split(), 'comm_ladder': {}}
        cat = str(r.get('category') or '').strip()
        amt = safe_float(r.get('amount'))
        if   cat == 'Commission':
            pay_by_num[num]['comm']    += amt
            # LEG SPLIT (decomposition only): the ePay payment type names its own month-of-life
            # ("New Activation Bounty - Month 3"). Every Commission dollar lands in exactly one of
            # m1 / trailing / unsplit, so the three always re-sum to 'comm'.
            _b, _leg, _ = leg_classify.label(r.get('payment_type'))
            pay_by_num[num]['comm_legs'][_b] += amt
            _leg_ladder_add(pay_by_num[num]['comm_ladder'], 'l', _leg, amt)
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
            comp_by_num[num] = {'comm': 0, 'reimb': 0, 'mdf': 0,
                                'comm_legs': _legs.empty_split(), 'comm_ladder': {}}
        ct = str(r.get('compensation_type') or '').lower()
        amt = safe_float(r.get('payment_amount'))
        if 'reimbursement' in ct or 'rebate' in ct:
            comp_by_num[num]['reimb'] += amt
        elif 'mdf' in ct:
            comp_by_num[num]['mdf'] += amt
        else:
            comp_by_num[num]['comm'] += amt
            # Same vocabulary as the Payment Detail (verified on the real Comprehensive Comp export),
            # so the same label classifier splits it.
            _b, _leg, _ = leg_classify.label(r.get('compensation_type'))
            comp_by_num[num]['comm_legs'][_b] += amt
            _leg_ladder_add(comp_by_num[num]['comm_ladder'], 'l', _leg, amt)

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

        acc_gp    = sum(safe_float(r.get('gp')) for r in rows if classify_row(r) == 'accessory')
        setup_gp  = sum(safe_float(r.get('gp')) for r in rows if 'Device Setup Charge' in str(r.get('product_desc','')))
        phone_sales = sum(safe_float(r.get('ext_price')) for r in rows if classify_row(r) == 'device')
        plan_gp   = sum(safe_float(r.get('gp')) for r in rows if classify_row(r) == 'plan')
        other_gp  = sum(safe_float(r.get('gp')) for r in rows
                        if classify_row(r) == 'other'
                        and 'Device Setup Charge' not in str(r.get('product_desc','')))

        pay = pay_by_num.get(num, {})
        comm_legs      = pay.get('comm_legs') or _legs.empty_split()
        _leg_ladder_merge(leg_ladder, 'comm', (pay.get('comm_ladder') or {}).get('l'))
        comm_recv  = pay.get('comm', 0)
        reimb      = pay.get('reimb', 0)
        mdf        = pay.get('mdf', 0)
        chargeback = pay.get('chb', 0)
        unmapped   = pay.get('unmapped', 0)

        comp = comp_by_num.get(num, {})
        comp_comm_legs = comp.get('comm_legs') or _legs.empty_split()
        _leg_ladder_merge(leg_ladder, 'comp_comm', (comp.get('comm_ladder') or {}).get('l'))
        comp_comm  = comp.get('comm', 0)
        comp_reimb = comp.get('reimb', 0)
        comp_mdf   = comp.get('mdf', 0)

        mi_data    = mi_by_sfid.get(sfid, {'mi': 0, 'atu': 0}) if sfid else {'mi': 0, 'atu': 0}
        mi_amt     = mi_data['mi']
        atu_amt    = mi_data['atu']
        mi_legs    = mi_data.get('mi_legs') or _legs.empty_split()
        atu_legs   = mi_data.get('atu_legs') or _legs.empty_split()
        _leg_ladder_merge(leg_ladder, 'mi', (mi_data.get('mi_ladder') or {}).get('l'))
        _leg_ladder_merge(leg_ladder, 'atu', (mi_data.get('atu_ladder') or {}).get('l'))

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
            # ── commission LEG split (owner 2026-08-04) — pure decomposition, adds no money ──
            **_legs.to_public('comm', comm_legs),
            **_legs.to_public('comp_comm', comp_comm_legs),
            **_legs.to_public('mi', mi_legs),
            **_legs.to_public('atu', atu_legs),
            'total_rev': total_rev, 'rep_pay': rep_pay,
            'exp_total': exp_total, 'net_phone_cost': net_phone_cost,
            'net_profit': net_profit, 'net_excl_mdf': net_excl_mdf,
        })

    # ── VidaPay/MA carrier income (ePay-less orgs) — ONE company-wide row ─────────────────────────
    # Owner 2026-07-29: "the commission received should be in commission column." MA rows carry no store
    # address (only a processor merchant id), so — exactly like the P&L's MA fallback — the money is
    # booked company-wide instead of inventing a phantom per-store bucket. comm = MA Commission Details
    # payable (sign-flipped, positive = dealer receives); atu = airtime margin (merchant_discount).
    # ma_income is None for every ePay org (house/Boost) → no row, byte-identical.
    if ma_income and (safe_float(ma_income.get('comm')) or safe_float(ma_income.get('atu'))):
        _mc, _ma = safe_float(ma_income.get('comm')), safe_float(ma_income.get('atu'))
        # LEG SPLIT of the MA commission: the leg is the COLUMN (spiff_m1 = 1st month, spiff_m2..m6 =
        # trailing; activation-order margins = 1st month). Split over the EXACT component list the
        # router built _mc from, so the three buckets re-sum to _mc. Without components (older caller)
        # the money is honestly reported as unsplit rather than guessed.
        _ma_comp_list = list(ma_income.get('component_list') or [])
        if _ma_comp_list:
            _ma_split_res = leg_classify.ma(ma_income.get('components') or {}, _ma_comp_list)
            _ma_legs = dict(_ma_split_res['buckets'])
            for _lk, _lv in (_ma_split_res.get('leg_ladder') or {}).items():
                _leg_ladder_add(leg_ladder, 'comm', None if _lk == 'unknown' else _lk, _lv)
            # Guard the identity even if a component list and the caller's total ever disagree
            # (rounding at the cent): any residue is reported, never silently dropped.
            _resid = round(_mc - sum(_ma_legs.values()), 2)
            if _resid:
                _ma_legs[_legs.UNSPLIT] = round(_ma_legs[_legs.UNSPLIT] + _resid, 2)
                _leg_ladder_add(leg_ladder, 'comm', None, _resid)
        else:
            _ma_legs = _legs.empty_split()
            _ma_legs[_legs.UNSPLIT] = _mc
            _leg_ladder_add(leg_ladder, 'comm', None, _mc)
        # MA ATU (airtime margin) carries no month-of-life at all in the feed -> honestly unsplit.
        _ma_atu_legs = _legs.empty_split()
        _ma_atu_legs[_legs.UNSPLIT] = _ma
        _leg_ladder_add(leg_ladder, 'atu', None, _ma)
        store_rows.append({
            'store': '(Company-wide — VidaPay/MA)', 'store_code': '', 'market': '',
            'acc_gp': 0.0, 'setup_gp': 0.0, 'phone_sales': 0.0, 'plan_gp': 0.0, 'other_gp': 0.0,
            'comm': _mc, 'reimb': 0.0, 'mdf': 0.0,
            'comp_comm': 0.0, 'comp_reimb': 0.0, 'comp_mdf': 0.0,
            'chargeback': 0.0, 'unmapped': 0.0, 'mi': 0.0, 'atu': _ma,
            **_legs.to_public('comm', _ma_legs),
            **_legs.to_public('comp_comm', _legs.empty_split()),
            **_legs.to_public('mi', _legs.empty_split()),
            **_legs.to_public('atu', _ma_atu_legs),
            'total_rev': _mc + _ma, 'rep_pay': 0.0, 'exp_total': 0.0, 'net_phone_cost': 0.0,
            'net_profit': _mc + _ma, 'net_excl_mdf': _mc + _ma,
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
        acc_gp   = sum(safe_float(r.get('gp')) for r in rows if classify_row(r) == 'accessory')
        setup_gp = sum(safe_float(r.get('gp')) for r in rows if 'Device Setup Charge' in str(r.get('product_desc','')))
        phone_s  = sum(safe_float(r.get('ext_price')) for r in rows if classify_row(r) == 'device')
        plan_gp  = sum(safe_float(r.get('gp')) for r in rows if classify_row(r) == 'plan')

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
    for _p in ('comm', 'comp_comm', 'mi', 'atu'):
        for _k in _legs.public_keys(_p):
            totals[_k] = round(sum(r.get(_k, 0.0) for r in store_rows), 2)

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
        cat = classify_row(r)
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
    # reproducible across requests regardless of dict insertion order. The RAW name is folded in after the
    # case-folded one (Gate-1 rework nit) so two departments differing only in case — 'ACC' vs 'Acc', which
    # ARE distinct rows here since the key is the raw string — can't fall back to dict insertion order.
    def _comp_sort_key(x):
        return (-abs(x['gp']), -abs(x['ext_price']), x['department'].lower(), x['department'])

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

    # ── COMMISSION LEG SPLIT summary (owner 2026-08-04) — 1st month vs M2–M12, per source ────────
    # DECOMPOSITION, not a recompute: for every source the three buckets are proven here to re-sum to
    # that source's own, unchanged column total. `identity_ok` is False only if a future edit breaks
    # that, and the page says so rather than quietly showing numbers that don't add up.
    leg_sources = []
    for _p, _label, _how in (
            ('comm', 'Commission received (ePay Payment Detail)',
             'the month named in the payment type — "… - Month N"'),
            ('comp_comm', 'Comp Comm (Comprehensive Compensation)',
             'the month named in the compensation type — "… - Month N"'),
            ('mi', 'MI residual', 'the subscriber\'s activation date vs this report month'),
            ('atu', 'ATU residual', 'the subscriber\'s activation date vs this report month')):
        _k1, _k2, _ku = _legs.public_keys(_p)
        _tot = round(safe_float(totals.get(_p)), 2)
        _sum = round(safe_float(totals.get(_k1)) + safe_float(totals.get(_k2))
                     + safe_float(totals.get(_ku)), 2)
        leg_sources.append({
            'key': _p, 'label': _label, 'splits_on': _how,
            'm1': round(safe_float(totals.get(_k1)), 2),
            'm2_12': round(safe_float(totals.get(_k2)), 2),
            'unsplit': round(safe_float(totals.get(_ku)), 2),
            'total': _tot, 'parts_total': _sum,
            'identity_ok': abs(_tot - _sum) < 0.01,
            'ladder': leg_ladder.get(_p, {}),
        })
    commission_legs_block = {
        'sources': leg_sources,
        'headline': next((s for s in leg_sources if s['key'] == 'comm'), None),
        'ladder': leg_ladder,
        'config': leg_classify.describe(),
        'identity_ok': all(s['identity_ok'] for s in leg_sources),
        'basis': ('1st Month = commission received in the same month the number activated. '
                  'M2–M12 = commission received for a number activated in an EARLIER month. '
                  'Unsplit = money whose source states no month-of-life (map it on Commission Legs).'),
    }

    return {'store_rows': store_rows, 'rep_rows': rep_rows, 'totals': totals, 'period': period,
            'commission_legs': commission_legs_block,
            'bucket_composition': bucket_composition, 'unmapped_departments': unmapped_departments,
            'bucket_composition_excluded': excluded,
            'bucket_composition_basis': 'countable sale lines (voided / Return / unattributed excluded — '
                                        'the shared _sales_cell_agg skip rules)'}
