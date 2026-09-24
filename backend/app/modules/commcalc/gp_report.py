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
# Commission double-book suppression (owner decision 2026-09-08 "Rep commision should go in p&l").
# Also a PURE leaf module — its only import is the shared period parser, and that one is lazy — so
# the calculator stays dependency-free. The DECISION lives there, not here: this file only asks it
# which rows stop booking, so the P&L (account/coa) and this report can never suppress differently.
from app.modules.commcalc import labour_coverage as _lcov

DEVICE_DEPTS = {'Android - XP', 'IPHONE - XP', 'TABLET - XP'}
ONDIGO_DEPT = 'Ondigo'
GP_CATEGORIES = {'device', 'accessory', 'plan', 'other', 'exclude'}

# ── CUSTOM GP CATEGORIES (owner 2026-09-08) ──────────────────────────────────────────────────────
# Owner: "new categories should be able to add". The GP report aggregates into exactly FOUR money
# buckets — device (at ext_price), accessory (at the configured basis), plan (at gp) and other (at
# gp) — plus 'exclude', which drops the line. A tenant-invented category that matched none of them
# would have its money counted into NOTHING: the lines would vanish from GP with no error and no
# total moving anywhere visible. That is the silent-zero class, applied to a whole category.
#
# So a category is a LABEL the tenant may create freely, and it always declares where its money
# goes: `rolls_up_to` names one of the four built-in buckets. The label is what the tenant sees and
# assigns; the bucket is what the arithmetic uses. A new category therefore cannot lose money — the
# worst case is that it rolls into 'other', which is exactly where an unmapped line sits today.
#
# `basis` is derived from the bucket, never stored twice: device counts ext_price, everything else
# counts gp (accessory's sales-vs-gp choice stays with accessory_config.gp_acc_basis, mig 932 — this
# does NOT introduce a second place to decide it).
DEFAULT_GP_CATEGORIES = [
    {"value": "device",    "label": "Device",    "rolls_up_to": "device",    "sort_order": 10, "builtin": True},
    {"value": "accessory", "label": "Accessory", "rolls_up_to": "accessory", "sort_order": 20, "builtin": True},
    {"value": "plan",      "label": "Plan",      "rolls_up_to": "plan",      "sort_order": 30, "builtin": True},
    {"value": "other",     "label": "Other",     "rolls_up_to": "other",     "sort_order": 40, "builtin": True},
    {"value": "exclude",   "label": "Exclude",   "rolls_up_to": "exclude",   "sort_order": 50, "builtin": True},
]


def bucket_map(gp_categories=None):
    """{category value -> money bucket}. The five built-ins map to themselves, so passing None (or an
    empty list) reproduces the pre-existing behaviour byte-for-byte. A configured row may add a new
    value or RELABEL a built-in, but may never point a built-in at a different bucket — that would
    silently restate history, so a row attempting it is ignored and the built-in stands."""
    out = {c["value"]: c["rolls_up_to"] for c in DEFAULT_GP_CATEGORIES}
    builtin = set(out)
    for row in (gp_categories or []):
        v = str(row.get("value") or "").strip().lower()
        if not v or v in builtin:
            continue
        if row.get("is_active") is False:
            continue
        b = str(row.get("rolls_up_to") or "").strip().lower()
        out[v] = b if b in GP_CATEGORIES else "other"
    return out


def _gp_overrides(gp_category_map, allowed=None):
    """{department: gp-bucket} from commcalc.gp_category_map rows (mig 069) — the ONE override-parsing
    rule, shared by the legacy department classifier and the config-mode per-line classifier.

    `allowed` is the org's bucket_map when custom categories are in play; omitting it keeps the
    original built-ins-only behaviour. A mapping is resolved THROUGH the map, so a department mapped
    to a tenant category lands on that category's bucket and the arithmetic downstream is unchanged."""
    buckets = allowed if allowed is not None else {c: c for c in GP_CATEGORIES}
    overrides = {}
    for row in (gp_category_map or []):
        d = str(row.get('department') or '').strip()
        c = str(row.get('category') or '').strip().lower()
        if c in buckets:
            overrides[d] = buckets[c]
    return overrides


def item_key(sku, desc):
    """The item identity used by commcalc.item_mapping (mig 041): SKU when there is one, else the
    description. Duplicated NOWHERE — router._item_key delegates here so the GP item overrides and the
    item-mapping editor can never key the same product differently."""
    s = str(sku or "").strip()
    if s and s.lower() not in ("nan", "none", "0", "0.0"):
        return s.upper()[:200]
    return str(desc or "").strip().upper()[:200]


def _item_overrides(item_gp_map, allowed=None):
    """{item_key -> money bucket} from commcalc.item_mapping.gp_category (mig 992). Same resolution
    rule as the department overrides, so the two grains can never disagree about what a name means."""
    buckets = allowed if allowed is not None else {c: c for c in GP_CATEGORIES}
    out = {}
    for row in (item_gp_map or []):
        k = str(row.get('item_key') or '').strip().upper()
        c = str(row.get('gp_category') or '').strip().lower()
        if k and c in buckets:
            out[k] = buckets[c]
    return out


def _dept_classifier(gp_category_map, gp_categories=None):
    """Return a fn department_label -> GP category. The map (commcalc.gp_category_map, mig 069) is a set
    of OVERRIDES layered on the built-in Boost defaults — so an EMPTY/None map reproduces the original
    hard-coded buckets byte-for-byte (device = Android/IPHONE/TABLET-XP, accessory = Ondigo, blank = plan,
    everything else = other). A tenant maps only the labels that differ; '' overrides blank-department rows."""
    overrides = _gp_overrides(gp_category_map, bucket_map(gp_categories))
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
    key = _legs.ladder_key(leg_month)   # ONE home for the rung key (commission_legs, §4a.2)
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
    item_gp_map: list[dict] = None,
    gp_categories: list[dict] = None,
    resolve_store_code=None,
    config_classify: dict = None,
    ma_income: dict = None,
    resolve_store_canonical=None,
    leg_classify=None,
    acc_basis: str = 'gp',
    commission_suppression_names: list = None,
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
    ma_income: VidaPay/MA carrier income for ePay-less orgs, as produced by THE ONE HOME —
    `account/ma_store_pnl.gp_carrier_income` — which reads the very bookings `coa.build_inputs` books
    (owner bug report 2026-09-21: "the m1 commision is different in both … the gross profit shows
    company level commission it shoudl show store level as it is paid on store level"). This engine
    FILES those figures, it does not derive them: `by_store` is keyed by CANONICAL store address
    (`ma_store_pnl.canonical_store_index`), `''` is the honest company-wide bucket for a processor
    account no index knows, and `months` carries the booked basis's M1..M12+ ladder. Both bases ride
    along labelled in `earned` / `received`, display-only. None = no MA income (house/Boost).
    resolve_store_canonical: raw store string -> canonical store address (`coa.store_resolver`) — the
    SAME canonicalization `ma_store_pnl.canonical_store_index` puts the MA side through, so the two
    join on one spelling. A naive leading-street-number join does NOT work here and must never be
    reintroduced: LuxeLink's MA fulfillment spells Hempstead "21880" where the sales feed spells it
    "218-80", and matching on the first token sends that store's money to a phantom row (negative
    control in harness_gp_pnl_commission_parity.py). None = match on the raw string.
    leg_classify (owner directive 2026-08-04): a commission_legs.LegClassifier that attributes RECEIVED
    commission money to the 1st-month leg vs the M2–M12 trailing legs. None = the pure code-default
    classifier. This is a DECOMPOSITION ONLY: it adds `*_m1` / `*_m2_12` / `*_unsplit` companions to
    `comm`, `comp_comm`, `mi` and `atu`; each trio sums to its existing column to the cent, and no
    existing money column, total_rev, rep_pay, net_profit or bucket classification changes at all.
    acc_basis (owner directive 2026-09-02 — "Acc Gp should show the price at which the accessories
    were sold not the Gross profit as they are not entered correct … renamed to Acc Sales"):
      'gp'    — the legacy column: Σ `gp` of accessory lines (this FUNCTION's default, so every
                pure-harness caller stays byte-identical);
      'sales' — Σ `ext_price` (sell price) of accessory lines — the same basis the device bucket
                (`phone_sales`) has always used, and the basis the carrier portal's own 'Acc. Sales'
                column reconciled to within 1%. The router resolves the per-org config
                (accessory_config.gp_acc_basis, mig 932; HOUSE DEFAULT 'sales') and passes it here.
    The chosen basis flows into `acc_gp` (key name kept so every consumer/export keeps working),
    `total_rev` and `net_profit` consistently, and the result carries `acc_basis` + `acc_label`
    ('Acc Sales' / 'Acc GP') so display surfaces label the column from config, not hardcoded strings.
    commission_suppression_names (owner decision 2026-09-08, "Rep commision should go in p&l"):
      the org's `account_config.labour_commission_expense_names`. `rep_commissions` → the `−Rep Pay`
      column is the AUTHORITATIVE route and is untouched; an expense row whose name is listed here
      is the SAME labour dollars a second time inside `−Expenses`, so it stops booking. Suppression
      is per STORE-MONTH and only where rep pay actually exists to replace it — a store with the
      expense row and NO rep pay keeps its row (removing it would delete a real cost and book
      nothing back) and is surfaced instead. The decision itself lives in
      `labour_coverage.suppression_plan`, shared with the P&L so the two reports cannot disagree.
      None/[] (the house default) ⇒ nothing is suppressed and every figure is byte-identical.
      The result carries `labour_commission_suppressed` — the per-store swap, both dollar figures.
    """
    if leg_classify is None:
        leg_classify = _legs.default_classifier()
    acc_basis = 'sales' if str(acc_basis or '').strip().lower() == 'sales' else 'gp'
    _acc_field = 'ext_price' if acc_basis == 'sales' else 'gp'
    leg_ladder: dict[str, dict] = {}
    _buckets = bucket_map(gp_categories)
    classify = _dept_classifier(gp_category_map, gp_categories)
    # ITEM grain (mig 992) beats every department rule: it is the tenant pointing at ONE product and
    # saying what it is, which a department label -- blank, in the case that prompted this -- cannot
    # express. An empty map leaves _items empty, so every branch below is reached exactly as before.
    _items = _item_overrides(item_gp_map, _buckets)
    def _item_hit(r):
        return _items.get(item_key(r.get('sku'), r.get('product_desc'))) if _items else None
    if config_classify is None:
        def classify_row(r) -> str:
            return _item_hit(r) or classify(r.get('department'))
    else:
        _is_acc = config_classify.get('is_accessory') or (lambda _r: False)
        _box = {str(b).strip() for b in (config_classify.get('box_departments') or ())}
        _ovr = _gp_overrides(gp_category_map, _buckets)
        def classify_row(r) -> str:
            # Item override FIRST -- the most specific statement of intent there is. Then accessory
            # (category-level discrimination the dept map can't express), then the tenant's explicit
            # department overrides, then box departments = device, blank = plan.
            hit = _item_hit(r)
            if hit:
                return hit
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
        # NULL-SAFE (owner-approved 2026-08-06). `store_mapping.is_active` is a NULLABLE column:
        # `.get('is_active', True)` returns the default ONLY when the KEY is ABSENT, so a row whose
        # column exists but is NULL evaluated falsy and the store was WRONGLY dropped from the GP
        # report. Same predicate as commcalc's `_store_active` and storeops' `_inactive_ids_from`:
        # only an EXPLICIT false is inactive. **No-op against today's live data** — store_mapping
        # is_active is true on 31/31 house rows and 39/39 luxelink rows, so every GP number is
        # byte-identical; this closes the latent trap, it does not move money.
        if s.get('is_active') is False: continue
        addr = str(s.get('store_address') or '').strip()
        if not addr: continue
        num = street_num(addr)
        if num and not any(street_num(k) == num for k in by_store.keys()):
            by_store[addr] = []

    # ── Expense-key per store (ONE derivation, used twice) ────────────────────────────────────────
    # `exp_code` is the key the tenant's store_expenses are filed under: the store_mapping join's
    # store_code where it yielded one, else the universal resolver (a tenant with no store_mapping).
    # It was derived inline in the row loop below; the commission-suppression pairing needs the SAME
    # key BEFORE the loop (to pair each store's commission expense with its rep pay), so it is
    # derived once here and read in both places rather than computed twice and allowed to drift.
    exp_code_by_store = {}
    for store in by_store:
        _sm = store_by_num.get(street_num(store), {})
        exp_code_by_store[store] = (
            str(_sm.get('store_code') or '').strip()
            or (str(resolve_store_code(store) or '').strip() if resolve_store_code else ''))

    # ── Commission double-book suppression (owner decision 2026-09-08) ────────────────────────────
    # `rep_pay` (commcalc.rep_commissions) is the authoritative route and is NOT touched. A
    # commission-named expense row is the same labour dollars again inside `exp_total`, which
    # `net_profit` subtracts alongside `rep_pay`. The plan decides per STORE-MONTH and never
    # suppresses a cost rep_commissions cannot replace; with no configured name it is inert and
    # every column below is byte-identical (`suppression_index` returns empty frozensets).
    _supp_rep_by_key = {}
    for store, _ec in exp_code_by_store.items():
        if _ec:
            _supp_rep_by_key[_ec] = round(
                _supp_rep_by_key.get(_ec, 0.0) + rep_pay_by_store.get(street_num(store), 0), 2)
    commission_suppressed = _lcov.suppression_plan(
        expenses, _supp_rep_by_key, commission_suppression_names)
    _supp_by_key = {s['store_code']: s['expense']
                    for s in commission_suppressed['stores'] if s['suppressed']}

    # ── MA/VidaPay carrier income, PER STORE (owner bug report 2026-09-21) ───────────────────────
    # `ma_income` arrives already booked and already store-keyed by THE ONE HOME
    # (`ma_store_pnl.gp_carrier_income` → `canonical_store_index`). This engine only files it onto
    # the row it belongs to, so a dollar on the P&L and the same dollar here can never be two
    # different dollars again. Keys are CANONICAL store addresses; `''` is the honest company-wide
    # bucket for a processor account the index cannot resolve — it is never hidden and never guessed
    # onto a store. Matching goes through the SAME canonicalization the MA side went through, NOT a
    # leading-street-number join (which mis-joins "21880" against "218-80" and loses a store).
    _ma_cells = {str(k): dict(v) for k, v in ((ma_income or {}).get('by_store') or {}).items()}
    _ma_months = {str(k): dict((v or {}).get('months') or {})
                  for k, v in ((ma_income or {}).get('by_store') or {}).items()}

    def _ma_take(raw_store):
        """Pop this store's MA cell (once), matched on the canonical spelling. Popping is what makes
        the leftovers below provably 'nothing fell through the join' rather than an assumption."""
        if not _ma_cells:
            return None, None
        canon = str((resolve_store_canonical(raw_store) if resolve_store_canonical
                     else raw_store) or '').strip()
        if canon and canon in _ma_cells:
            return _ma_cells.pop(canon), _ma_months.pop(canon, {})
        return None, None

    # ── Build store rows ──────────────────────────────────────────
    store_rows = []
    for store, rows in by_store.items():
        num = street_num(store)
        sm = store_by_num.get(num, {})
        sfid = str(sm.get('salesforce_id') or '').strip()
        market = str(sm.get('market') or '').strip()
        store_code = str(sm.get('store_code') or '').strip()

        acc_gp    = sum(safe_float(r.get(_acc_field)) for r in rows if classify_row(r) == 'accessory')
        setup_gp  = sum(safe_float(r.get('gp')) for r in rows if 'Device Setup Charge' in str(r.get('product_desc','')))
        phone_sales = sum(safe_float(r.get('ext_price')) for r in rows if classify_row(r) == 'device')
        plan_gp   = sum(safe_float(r.get('gp')) for r in rows if classify_row(r) == 'plan')
        other_gp  = sum(safe_float(r.get('gp')) for r in rows
                        if classify_row(r) == 'other'
                        and 'Device Setup Charge' not in str(r.get('product_desc','')))

        pay = pay_by_num.get(num, {})
        comm_legs      = pay.get('comm_legs') or _legs.empty_split()
        # PER-ROW month ladder (owner report 2026-09-21: "display each months commission in separate
        # column so we see what is going on"). Fed from the SAME per-store parts that are merged into
        # the report-wide ladder one line below — one derivation, read twice, so a store row and the
        # header can never state different months.
        _row_ladder: dict[str, float] = {}
        _leg_ladder_merge({'comm': _row_ladder}, 'comm', (pay.get('comm_ladder') or {}).get('l'))
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

        # MA/VidaPay carrier income for THIS store. Each figure lands in the column that already
        # means it — commission in Commission, residual in MI, airtime margin in ATU, market spiff
        # in MDF — and a revenue line the report has no column for lands in Unmapped, named in
        # `ma_income['filed']` rather than folded into a column that would mean something else.
        # The rebate and the wallet funding are NOT here at all: `gp_carrier_income` excludes them
        # with the owner's own rulings attached (`ma_income['excluded']`).
        _ma_cell, _ma_cell_months = _ma_take(store)
        if _ma_cell:
            comm_recv  += _ma_cell.get('comm', 0.0)
            mdf        += _ma_cell.get('mdf', 0.0)
            mi_amt     += _ma_cell.get('mi', 0.0)
            atu_amt    += _ma_cell.get('atu', 0.0)
            unmapped   += _ma_cell.get('unmapped', 0.0)
            comm_legs = dict(comm_legs)
            comm_legs[_legs.M1]       = round(comm_legs.get(_legs.M1, 0.0) + _ma_cell.get('m1', 0.0), 2)
            comm_legs[_legs.TRAILING] = round(comm_legs.get(_legs.TRAILING, 0.0) + _ma_cell.get('trailing', 0.0), 2)
            comm_legs[_legs.UNSPLIT]  = round(comm_legs.get(_legs.UNSPLIT, 0.0) + _ma_cell.get('unsplit', 0.0), 2)
            for _mk, _mv in (_ma_cell_months or {}).items():
                _leg_ladder_add(leg_ladder, 'comm', _mk, _mv)
                _leg_ladder_add({'comm': _row_ladder}, 'comm', _mk, _mv)
            # The MA residual and airtime margin state no month-of-life anywhere in their feed, so
            # they land in the honest `unsplit` bucket — never guessed into 1st Month. Keeping them
            # in the split at all is what holds the report's own promise that, for every source,
            # m1 + M2-M12 + unsplit == that source's column (`identity_ok`).
            if _ma_cell.get('mi'):
                mi_legs = dict(mi_legs)
                mi_legs[_legs.UNSPLIT] = round(mi_legs.get(_legs.UNSPLIT, 0.0) + _ma_cell['mi'], 2)
                _leg_ladder_add(leg_ladder, 'mi', None, _ma_cell['mi'])
            if _ma_cell.get('atu'):
                atu_legs = dict(atu_legs)
                atu_legs[_legs.UNSPLIT] = round(atu_legs.get(_legs.UNSPLIT, 0.0) + _ma_cell['atu'], 2)
                _leg_ladder_add(leg_ladder, 'atu', None, _ma_cell['atu'])

        total_rev  = acc_gp + setup_gp + phone_sales + plan_gp + other_gp + comm_recv + reimb + mdf + chargeback + unmapped + mi_amt + atu_amt
        rep_pay    = rep_pay_by_store.get(num, 0)
        # Expenses are keyed by the org's storeops store_code (the Expenses page picks from storeops.stores).
        # When the store_mapping street-number join yielded a store_code, use it (house — byte-identical).
        # When it did NOT (a tenant with no commcalc.store_mapping → store_code=''), resolve the raw store
        # string to the storeops store_code so the tenant's configured expenses attach. This changes ONLY
        # exp_total for rows that had no store_code; the row's displayed store_code/market are untouched.
        exp_code   = exp_code_by_store.get(store, '')
        # Commission booked on BOTH routes: the expense-side copy stops booking (owner 2026-09-08).
        # Subtracted rather than filtered out of `exp_by_code` so the amount removed stays visible
        # per store in `labour_commission_suppressed` — never rendered as a measured $0.00.
        exp_total  = exp_by_code.get(exp_code, 0) - _supp_by_key.get(exp_code, 0.0)
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
            # ── commission MONTH LADDER (owner 2026-09-21) — {rung: $} for THIS store, keys from
            # the ONE home (commission_legs.ladder_key). No month count: a rung exists here because
            # the feed carried it. `_m_unlabelled` is its own rung and is never folded into a month.
            'comm_ladder': {k: round(v, 2) for k, v in _row_ladder.items()},
            # ── commission LEG split (owner 2026-08-04) — pure decomposition, adds no money ──
            **_legs.to_public('comm', comm_legs),
            **_legs.to_public('comp_comm', comp_comm_legs),
            **_legs.to_public('mi', mi_legs),
            **_legs.to_public('atu', atu_legs),
            'total_rev': total_rev, 'rep_pay': rep_pay,
            'exp_total': exp_total, 'net_phone_cost': net_phone_cost,
            'net_profit': net_profit, 'net_excl_mdf': net_excl_mdf,
        })

    # ── VidaPay/MA carrier income — what the account→store index could NOT place ─────────────────
    # Owner bug report 2026-09-21: "the gross profit shows company level commission it shoudl show
    # store level as it is paid on store level". It now does — every MA figure was filed onto its
    # store in the loop above. What reaches HERE is only what honestly has no store: a processor
    # account that `ma_store_pnl.canonical_store_index` cannot resolve (`''`), exactly the posture
    # the P&L takes. For LuxeLink August 2026 that is $0.00 across all 20 accounts; the row appears
    # only when a real dollar has nowhere to go, and it is NEVER hidden.
    #
    # A canonical store the MA side knows but the sales feed has no row for also lands here, under
    # its own name rather than being silently dropped — the join can lose nothing without saying so.
    _ma_leg_note = None
    if ma_income:
        _ma_leg_note = {
            'source': 'account/ma_store_pnl.gp_carrier_income — the SAME bookings the P&L books',
            'basis': ma_income.get('basis'),
            'basis_label': ma_income.get('basis_label'),
            'splits_on': ('the month-of-life the BOOKED basis states: the daily-transaction rows\''
                          ' own M<n> label (M1..M12+), or the commission sheet\'s spiff column '
                          '(spiff_m1..m6, which structurally stops at M6)'),
            # Both bases, labelled (owner decision 2026-09-21). Display only — the money column is
            # the basis this org BOOKS, so the Gross Profit report and the P&L cannot disagree.
            'earned': ma_income.get('earned'),
            'received': ma_income.get('received'),
            'filed': ma_income.get('filed'),
            # What is NOT in the Commission column any more, and the owner ruling that says so.
            'excluded': ma_income.get('excluded'),
            'store_attributed': ma_income.get('store_attributed'),
            'stores_resolved': len(ma_income.get('stores_resolved') or []),
            'unsplit_fields': list(ma_income.get('unsplit_fields') or []),
        }
    for _mk in sorted(_ma_cells):
        _cell = _ma_cells[_mk]
        _months = _ma_months.get(_mk) or {}
        if not any(safe_float(_cell.get(k)) for k in ('comm', 'mi', 'atu', 'mdf', 'unmapped')):
            continue
        _legs_split = dict(_legs.empty_split())
        _legs_split[_legs.M1]       = round(safe_float(_cell.get('m1')), 2)
        _legs_split[_legs.TRAILING] = round(safe_float(_cell.get('trailing')), 2)
        _legs_split[_legs.UNSPLIT]  = round(safe_float(_cell.get('unsplit')), 2)
        _mi_split = dict(_legs.empty_split())
        _mi_split[_legs.UNSPLIT] = round(safe_float(_cell.get('mi')), 2)
        _atu_split = dict(_legs.empty_split())
        _atu_split[_legs.UNSPLIT] = round(safe_float(_cell.get('atu')), 2)
        for _lk, _lv in _months.items():
            _leg_ladder_add(leg_ladder, 'comm', _lk, _lv)
        _leg_ladder_add(leg_ladder, 'mi', None, safe_float(_cell.get('mi')))
        _leg_ladder_add(leg_ladder, 'atu', None, safe_float(_cell.get('atu')))
        _c, _mi2 = safe_float(_cell.get('comm')), safe_float(_cell.get('mi'))
        _a, _md = safe_float(_cell.get('atu')), safe_float(_cell.get('mdf'))
        _um = safe_float(_cell.get('unmapped'))
        store_rows.append({
            'store': _mk or '(Company-wide — no store on the processor account)',
            'store_code': '', 'market': '',
            'acc_gp': 0.0, 'setup_gp': 0.0, 'phone_sales': 0.0, 'plan_gp': 0.0, 'other_gp': 0.0,
            'comm': _c, 'reimb': 0.0, 'mdf': _md,
            'comp_comm': 0.0, 'comp_reimb': 0.0, 'comp_mdf': 0.0,
            'chargeback': 0.0, 'unmapped': _um, 'mi': _mi2, 'atu': _a,
            'comm_ladder': {_legs.ladder_key(_lk): round(safe_float(_lv), 2)
                            for _lk, _lv in _months.items()},
            **_legs.to_public('comm', _legs_split),
            **_legs.to_public('comp_comm', _legs.empty_split()),
            **_legs.to_public('mi', _mi_split),
            **_legs.to_public('atu', _atu_split),
            'total_rev': _c + _mi2 + _a + _md + _um, 'rep_pay': 0.0, 'exp_total': 0.0,
            'net_phone_cost': 0.0,
            'net_profit': _c + _mi2 + _a + _md + _um, 'net_excl_mdf': _c + _mi2 + _a + _um,
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
        acc_gp   = sum(safe_float(r.get(_acc_field)) for r in rows if classify_row(r) == 'accessory')
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
        # `unmapped` is one of total_rev's terms and was never summed here — the SAME missing-column
        # defect the note below describes, found by the 2026-09-21 MA rewiring when carrier revenue
        # with no dedicated GP column started landing in it (Aug-2026 LuxeLink: $6,759.99 of device
        # margin + consumer financing visible per store, $0.00 in the header).
        'unmapped': sum(r['unmapped'] for r in store_rows),
        'mi': sum(r['mi'] for r in store_rows),
        'atu': sum(r['atu'] for r in store_rows),
        'total_rev': sum(r['total_rev'] for r in store_rows),
        'rep_pay': sum(r['rep_pay'] for r in store_rows),
        'exp_total': sum(r['exp_total'] for r in store_rows),
        # OWNER-REPORTED 2026-09-12, "the numbers are totally off". `net_phone_cost` is one of the
        # four terms of `net_profit` (total_rev - rep_pay - exp_total - net_phone_cost) and it was
        # computed per store but NEVER summed into `totals`, so the summary could not be reconciled
        # from what it showed: August 2026 displayed revenue 820,584.78 less rep_pay 10,723.40 less
        # exp_total 178,577.58 = 631,283.80, against a stated net profit of 541,274.47 — a gap of
        # exactly the 90,009.33 of phone cost nobody could see. A subtraction that moves the headline
        # must never be invisible in the header it moves. `plan_gp` and `other_gp` were missing for
        # the same reason (both are inside total_rev) and are summed here too.
        # harness_gp_totals_reconcile.py pins BOTH the identity and the no-missing-column rule, so a
        # column added to a store row in future cannot go un-totalled the same way.
        'net_phone_cost': sum(r['net_phone_cost'] for r in store_rows),
        'plan_gp': sum(r['plan_gp'] for r in store_rows),
        'other_gp': sum(r['other_gp'] for r in store_rows),
        'net_profit': sum(r['net_profit'] for r in store_rows),
        'net_excl_mdf': sum(r['net_excl_mdf'] for r in store_rows),
    }
    for _p in ('comm', 'comp_comm', 'mi', 'atu'):
        for _k in _legs.public_keys(_p):
            totals[_k] = round(sum(r.get(_k, 0.0) for r in store_rows), 2)

    # ── THE MONTH COLUMNS (owner report 2026-09-21) ───────────────────────────────────────────────
    # "the m2-m6 commission is 375% of the mrc … research where this is going wrong and display each
    # months commission in separate column so we see what is going on".
    #
    # `ladder_months` is the column LIST: the rungs the DATA carries, from the one home
    # (commission_legs.months_present) — not 6, not 12, not a config max. A rung that exists can
    # therefore never be invisible, which is the whole point: an M7 on a schedule the owner believes
    # runs to M6 is a FINDING, and it shows up as a column rather than being folded away.
    # The Unlabelled rung is a column of its own and is never folded into a month.
    ladder_months = _legs.months_present(*[r.get('comm_ladder') for r in store_rows])
    totals['comm_ladder'] = {k: round(sum(safe_float((r.get('comm_ladder') or {}).get(k))
                                          for r in store_rows), 2)
                             for k in ladder_months}
    # FLAT columns beside the dict, so an export/CSV that walks `totals` keys carries the months too
    # (WYSIWYG: what the page shows is what the export ships). Every row gets every column, so a
    # store with no M4 reads $0.00 rather than as a hole.
    for _r in store_rows:
        _r.update(_legs.ladder_to_public('comm', _r.get('comm_ladder') or {}, ladder_months))
    totals.update(_legs.ladder_to_public('comm', totals['comm_ladder'], ladder_months))

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
            ('comm',
             'Commission received (VidaPay / master agent)' if _ma_leg_note
             else 'Commission received (ePay Payment Detail)',
             (_ma_leg_note or {}).get('splits_on')
             or 'the month named in the payment type — "… - Month N"'),
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
            # Only ever set on the MA-fed Commission row. BOTH bases ride here, labelled (owner
            # decision 2026-09-21) — `earned` is what the commission sheet says was earned at
            # activation, `received` is what the cash rows say arrived; the money column carries
            # whichever one this org BOOKS (`basis`), so the Gross Profit report and the P&L are the
            # same dollars. `excluded` names what left the Commission column and the owner ruling
            # that took it out; `filed` names where every remaining dollar went.
            **({'unsplit_fields': _ma_leg_note.get('unsplit_fields') or [],
                'basis': _ma_leg_note.get('basis'),
                'basis_label': _ma_leg_note.get('basis_label'),
                'earned': _ma_leg_note.get('earned'),
                'received': _ma_leg_note.get('received'),
                'filed': _ma_leg_note.get('filed'),
                'excluded': _ma_leg_note.get('excluded'),
                'store_attributed': _ma_leg_note.get('store_attributed'),
                'stores_resolved': _ma_leg_note.get('stores_resolved'),
                'unsplit_why': ('money whose source states no month-of-life — an unlabelled spiff '
                                'row, or fee margin, which carries none at all')}
               if (_p == 'comm' and _ma_leg_note) else {}),
        })
    commission_legs_block = {
        # The column list for the per-month display, from the ONE home. `ladder_month_labels` is how
        # each rung is NAMED ('M1' … 'Unlabelled'), so no surface writes 'M' + n itself.
        'ladder_months': ladder_months,
        'ladder_month_labels': {k: _legs.ladder_label(k) for k in ladder_months},
        'ladder_columns': {k: _legs.ladder_column('comm', k) for k in ladder_months},
        'ladder_unknown_key': _legs.LADDER_UNKNOWN,
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
            # Which basis the accessory column carries + its display label — config-driven (mig 932),
            # so no surface hardcodes 'Acc GP' vs 'Acc Sales'.
            'acc_basis': acc_basis, 'acc_label': 'Acc Sales' if acc_basis == 'sales' else 'Acc GP',
            # The SWAP, per store and in total (owner 2026-09-08): what left `−Expenses` and what
            # `−Rep Pay` books in its place, plus every store where the cost was KEPT because
            # rep_commissions had nothing to replace it with. Inert ({'active': False}) for every
            # org that has configured no commission expense name.
            'labour_commission_suppressed': commission_suppressed,
            'commission_legs': commission_legs_block,
            'bucket_composition': bucket_composition, 'unmapped_departments': unmapped_departments,
            'bucket_composition_excluded': excluded,
            'bucket_composition_basis': 'countable sale lines (voided / Return / unattributed excluded — '
                                        'the shared _sales_cell_agg skip rules)'}
