"""Proof for agent/commission/luxelink-universal-targets-gp (OWNER DIRECTIVE 2026-07-18).

Two universality bugs where the HOUSE works only because house data/config exists, and a tenant
(luxelink, Total carrier, NO commcalc.store_mapping) gets nothing:

  SYMPTOM 1 — Daily Targets "not showing ANYTHING". get_targets_summary sourced the store universe ONLY
    from storeops.stores, so a targeted store with no matching storeops row rendered NOTHING even though
    Target Settings were saved. FIX: the universe is now storeops.stores UNION every targeted store_code
    (address/market enriched from store_mapping when present) → a target ALWAYS renders. House is a no-op
    (its target codes are a subset of its storeops roster). Config-required conditions (no roster/targets;
    sales that don't match a target store) surface as an explicit `setup_hint` — never a silent blank.

  SYMPTOM 2 — GP expenses configured on the tenant don't appear. calc_gp_report derived each store row's
    store_code from commcalc.store_mapping (house market map) via a street-number join; a tenant with no
    store_mapping got store_code='' for every row, so its store_expenses (keyed by the org's storeops
    store_code) never attached (exp_total=0). FIX: when the derived store_code is empty, resolve the raw
    store string to the storeops store_code via the SAME resolver Daily Targets uses, and look up expenses
    under that. Gated on empty store_code → house (store_mapping populated) is byte-identical.

Drives the REAL router functions (get_targets_summary, get_gp_report) + the REAL calc_gp_report over an
in-memory FakeClient (no DB/network). Run:  cd backend && python3 scratchpad/luxelink_universal_targets_gp_proof.py
"""


def run_route(x):
    """Call a commcalc route handler in EITHER shape.

    ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers were converted from `async def` to
    `def` so FastAPI runs them in the threadpool instead of on the single uvicorn event loop (an
    `async def` doing blocking Supabase I/O froze the whole product for its duration). The only textual
    change was the keyword. This helper awaits a coroutine when it gets one and passes a plain result
    straight through, so the proof works against BOTH shapes and needs no further edit if a handler
    ever legitimately becomes a coroutine again."""
    import asyncio as _a
    return _a.run(x) if _a.iscoroutine(x) else x
import os, sys, asyncio, calendar as _cal
from datetime import date as _date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R
from app.modules.commcalc.gp_report import calc_gp_report

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── in-memory fake supabase (honours eq / in_ / neq / range) ─────────────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []; self.count = count


class FakeQuery:
    def __init__(self, store, table):
        self.store = store; self.t = table; self.f = []; self.rng = None; self._count = None

    def select(self, *a, **k):
        if k.get('count') == 'exact':
            self._count = True
        return self

    def eq(self, c, v): self.f.append(('eq', c, v)); return self
    def neq(self, c, v): self.f.append(('neq', c, v)); return self
    def in_(self, c, v): self.f.append(('in', c, list(v))); return self
    def gte(self, c, v): self.f.append(('gte', c, v)); return self
    def lt(self, c, v): self.f.append(('lt', c, v)); return self
    def limit(self, n): return self
    def range(self, a, b): self.rng = (a, b); return self
    def order(self, *a, **k): return self
    def upsert(self, *a, **k): return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v: return False
            if k == 'neq' and rv == v: return False
            if k == 'in' and rv not in v: return False
            if k == 'gte' and not (rv is not None and str(rv) >= str(v)): return False
            if k == 'lt' and not (rv is not None and str(rv) < str(v)): return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        m = [r for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng; m = m[a:b + 1]
        if self._count:
            return FakeResult(data=[dict(r) for r in m], count=len(m))
        return FakeResult(data=[dict(r) for r in m])


class FakeSchema:
    def __init__(self, store): self.store = store
    def table(self, t): return FakeQuery(self.store, t)
    def rpc(self, *a, **k): raise Exception('no rpc in this proof')


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, s): return FakeSchema(self.store)


ORG = 'lux'
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
PERIOD = f"{_cal.month_name[_T.month]} {_T.year}"
TODAY = _date(_T.year, _T.month, min(15, max(3, _T.day)))
TODAY_ISO = TODAY.isoformat()
DAY = f"{OPEN}-02"
CT_PREM, CT_UPG, CT_BYOD = 'Prepaid New', 'Renewal', 'Bring Device'
CT_MAP = {CT_PREM: 'premium', CT_UPG: 'upgrade', CT_BYOD: 'byod'}


def srow(store, rep, tid, ct, day=DAY, cat='', dept='', ext=100.0, gp=20.0, pdesc=''):
    return {'org_id': ORG, 'period': OPEN, 'trans_id': tid, 'trans_date': day, 'store': store,
            'salesperson': rep, 'user_login': (rep or '').lower(), 'category': cat, 'department': dept,
            'contract_type': ct, 'product_desc': pdesc, 'ext_price': ext, 'gp': gp, 'voided': '', 'trans_type': ''}


def feed(store_str):
    return [srow(store_str, 'REP1', 'T1', CT_PREM), srow(store_str, 'REP1', 'T2', CT_UPG),
            srow(store_str, 'REP1', 'T3', CT_BYOD),
            srow(store_str, 'REP2', 'AC1', '', cat='HandsetBranded', ext=200.0)]


TGT = [{'org_id': ORG, 'period': OPEN, 'store_code': 'LUX-HEMP', 'activations_monthly': 70,
        'upgrades_monthly': 10, 'accessories_monthly': 1000}]
SO_MATCH = [{'org_id': ORG, 'store_code': 'LUX-HEMP', 'address': 'HEMPSTEAD', 'market': 'NY', 'monthly_target': 0}]
SO_MISMATCH = [{'org_id': ORG, 'store_code': 'S001', 'address': 'HEMPSTEAD', 'market': 'NY', 'monthly_target': 0}]


def tstore(storeops, targets, store_mapping=None, sales_store='HEMPSTEAD'):
    return {'daily_sales_feed': [dict(r) for r in feed(sales_store)], 'raw_sales': [],
            'accessory_config': [{'org_id': ORG, 'departments': [], 'categories': ['HandsetBranded'],
                                  'product_keywords': [], 'acima_tenders': [], 'box_departments': [],
                                  'setup_fee_keywords': [], 'contract_type_map': CT_MAP}],
            'store_mapping': store_mapping or [], 'stores': storeops, 'targets': targets,
            'exec_metric_config': [], 'shifts': [], 'name_map': [], 'rep_aliases': [], 'store_aliases': [],
            'app_config': [], 'flag_rules': [], 'gp_category_map': [], 'employees': []}


def run_summary(store, include_untargeted=False):
    c = FakeClient(store); _o = R.sb; R.sb = lambda: c
    try:
        return run_route(R.get_targets_summary(period=PERIOD, today=TODAY_ISO, org_id=ORG,
                           include_untargeted=include_untargeted, stores=None, markets=None, reps=None))
    finally:
        R.sb = _o


def codes(summ): return [s['store_code'] for s in summ['stores']]
def ach(summ, code, cat):
    for s in summ['stores']:
        if s['store_code'] == code:
            return (s['categories'].get(cat) or {}).get('achieved_mtd', 0.0)
    return None


# ══ SYMPTOM 1 — Daily Targets store universe ═══════════════════════════════════════════════════════
print("(1) SYMPTOM 1 — Daily Targets universal store universe")

# 1a — TENANT with NO storeops.stores + NO store_mapping but targets SET → the targeted store renders.
r = run_summary(tstore([], TGT))
check("no storeops + no store_mapping, targets set → target store renders (was 0/blank)",
      codes(r) == ['LUX-HEMP'], codes(r))
check("include_untargeted=1 same → target renders", codes(run_summary(tstore([], TGT), True)) == ['LUX-HEMP'])
check("never raises for the minimal tenant shape (both flags)", True)

# 1b — REAL luxelink shape (storeops present, code matches target) → achieved attaches (unchanged).
r = run_summary(tstore(SO_MATCH, TGT))
check("storeops present + code matches → 1 store", codes(r) == ['LUX-HEMP'], codes(r))
check("storeops present → achieved activations = 2 (ct-map fix intact)", ach(r, 'LUX-HEMP', 'activations') == 2.0,
      ach(r, 'LUX-HEMP', 'activations'))
check("storeops present → achieved accessories = 200", ach(r, 'LUX-HEMP', 'accessories') == 200.0)
check("storeops-matched sale → no setup_hint", r.get('setup_hint') == [], r.get('setup_hint'))

# 1c — storeops present but store_code MISMATCHES the target code → union still renders the target row;
#      the sold store (resolves to S001, no target) doesn't attach → setup_hint flags Store Matching.
r = run_summary(tstore(SO_MISMATCH, TGT))
check("storeops code mismatch → target row LUX-HEMP still renders (union)", 'LUX-HEMP' in codes(r), codes(r))
check("mismatched store S001 dropped (no target, not untargeted)", 'S001' not in codes(r), codes(r))
check("mismatch → achieved 0 (sales keyed to S001, not the target)", ach(r, 'LUX-HEMP', 'activations') == 0.0,
      ach(r, 'LUX-HEMP', 'activations'))
check("mismatch → setup_hint mentions Store Matching",
      any('Store Matching' in h for h in r.get('setup_hint', [])), r.get('setup_hint'))

# 1d — HOUSE byte-identity: every target code is IN storeops → the union adds NOTHING; rendered set is
#      exactly the storeops-derived set that passes the target filter, and no setup_hint fires.
r = run_summary(tstore(SO_MATCH, TGT))
check("house-shape (target ⊆ storeops) → rendered set == storeops targeted set (no phantom rows)",
      set(codes(r)) == {'LUX-HEMP'})
# add an untargeted storeops store: must NOT appear without include_untargeted (unchanged behavior).
so2 = SO_MATCH + [{'org_id': ORG, 'store_code': 'LUX-XTRA', 'address': 'OTHER', 'market': 'NY', 'monthly_target': 0}]
check("untargeted extra storeops store hidden by default (unchanged)",
      codes(run_summary(tstore(so2, TGT))) == ['LUX-HEMP'])

# 1e — empty everything → returns stores=[] + a setup_hint, never raises.
r = run_summary(tstore([], []))
check("no roster + no targets → stores empty, no raise", r['stores'] == [])
check("no roster + no targets → setup_hint present (configure X, not blank)", len(r.get('setup_hint', [])) >= 1,
      r.get('setup_hint'))
check("no roster + no targets → hint names Target Settings",
      any('Target Settings' in h for h in r.get('setup_hint', [])), r.get('setup_hint'))

# 1f — filter OPTIONS include the target-only store (pick-don't-type over the FULL roster universe).
r = run_summary(tstore([], TGT))
check("filter options include the target-only store (roster universe)",
      any(o.get('value') == 'LUX-HEMP' for o in (r.get('filters', {}).get('stores') or [])),
      r.get('filters'))

# 1g — RBAC scope must still restrict (self/keyset). With RBAC off (proof default) admin sees all.
check("response always carries setup_hint (additive field, list)", isinstance(r.get('setup_hint'), list))


# ══ SYMPTOM 2 — GP expenses attach for a tenant with no store_mapping ═══════════════════════════════
print("(2) SYMPTOM 2 — GP expense join universality")

def gp_srow(store, dept='Accessories', gp=50.0, ext=100.0, pdesc='Case', sp='REP1'):
    return {'org_id': ORG, 'period': OPEN, 'store': store, 'department': dept, 'gp': gp,
            'ext_price': ext, 'product_desc': pdesc, 'salesperson': sp}


def gp_store(store_mapping, storeops, expenses, sales_store):
    return {'raw_sales': [gp_srow(sales_store)], 'raw_payment_detail': [], 'raw_mi': [], 'rep_commissions': [],
            'store_expenses': expenses, 'raw_catalog': [], 'store_mapping': store_mapping,
            'payment_categories': [], 'raw_comp_report': [], 'gp_category_map': [],
            'stores': storeops, 'store_aliases': [], 'gp_snapshot': []}


def run_gp(store):
    c = FakeClient(store); _o = R.sb; R.sb = lambda: c
    try:
        return asyncio.run(R.get_gp_report(period=OPEN, org_id=ORG))
    finally:
        R.sb = _o


def exp_of(res, store):
    for row in res['store_rows']:
        if row['store'] == store:
            return row['exp_total']
    return None


# 2a — TENANT no store_mapping, expense keyed by storeops code → attaches (was 0).
res = run_gp(gp_store([], [{'org_id': ORG, 'store_code': 'LUX-HEMP', 'address': 'HEMPSTEAD', 'market': 'NY'}],
                      [{'org_id': ORG, 'period': OPEN, 'store_code': 'LUX-HEMP', 'amount': 500, 'expense_name': 'Rent'}],
                      'HEMPSTEAD'))
check("tenant no store_mapping → configured expense ATTACHES (exp_total=500)", exp_of(res, 'HEMPSTEAD') == 500.0,
      exp_of(res, 'HEMPSTEAD'))
check("tenant expense flows to totals", res['totals']['exp_total'] == 500.0, res['totals']['exp_total'])

# 2b — HOUSE store_mapping present → expense attaches via the existing street-number join (unchanged).
HOUSE_SM = [{'org_id': ORG, 'store_code': 'B-1', 'store_address': '3 PALISADE AVE', 'market': 'Boost',
             'salesforce_id': 'SF1', 'is_active': True}]
res_h = run_gp(gp_store(HOUSE_SM, [{'org_id': ORG, 'store_code': 'B-1', 'address': '3 PALISADE AVE', 'market': 'Boost'}],
                        [{'org_id': ORG, 'period': OPEN, 'store_code': 'B-1', 'amount': 300, 'expense_name': 'Rent'}],
                        '3 PALISADE AVE'))
check("house store_mapping present → expense attaches (exp_total=300)", exp_of(res_h, '3 PALISADE AVE') == 300.0,
      exp_of(res_h, '3 PALISADE AVE'))
check("house store row keeps its store_mapping store_code (display unchanged)",
      any(r['store_code'] == 'B-1' for r in res_h['store_rows']))

# 2c — MONEY/HOUSE BYTE-IDENTITY: calc_gp_report with resolve_store_code=None vs a resolver produces the
#      SAME store_rows for a house-shaped input (store_code non-empty → the fallback never fires).
house_sales = [gp_srow('3 PALISADE AVE'), gp_srow('55 MAIN ST')]
house_sm = HOUSE_SM + [{'org_id': ORG, 'store_code': 'B-2', 'store_address': '55 MAIN ST', 'market': 'Boost',
                        'salesforce_id': 'SF2', 'is_active': True}]
house_exp = [{'org_id': ORG, 'period': OPEN, 'store_code': 'B-1', 'amount': 300, 'expense_name': 'Rent'},
             {'org_id': ORG, 'period': OPEN, 'store_code': 'B-2', 'amount': 150, 'expense_name': 'Rent'}]
res_off = calc_gp_report(house_sales, [], [], [], house_exp, [], house_sm, OPEN, resolve_store_code=None)
_resolver = lambda s: 'ZZZ-SHOULD-NOT-FIRE'   # if the fallback ever fired for the house it would corrupt exp
res_on = calc_gp_report(house_sales, [], [], [], house_exp, [], house_sm, OPEN, resolve_store_code=_resolver)
check("house byte-identity: resolver-off vs resolver-on store_rows identical (fallback never fires)",
      res_off['store_rows'] == res_on['store_rows'])
check("house byte-identity: totals identical", res_off['totals'] == res_on['totals'])

# 2d — tenant expense saved under a code that matches NOTHING → exp_total 0, no crash; and the SAME sale
#      with the expense under the RESOLVED storeops code attaches.
res_miss = run_gp(gp_store([], [{'org_id': ORG, 'store_code': 'LUX-HEMP', 'address': 'HEMPSTEAD', 'market': 'NY'}],
                           [{'org_id': ORG, 'period': OPEN, 'store_code': 'NOPE', 'amount': 500, 'expense_name': 'Rent'}],
                           'HEMPSTEAD'))
check("tenant expense under a non-matching code → exp_total 0 (no crash)", exp_of(res_miss, 'HEMPSTEAD') == 0.0,
      exp_of(res_miss, 'HEMPSTEAD'))

# 2e — GP report never raises for a fully-empty tenant (no sales, no mapping, no expenses).
res_empty = run_gp(gp_store([], [], [], 'HEMPSTEAD'))
check("GP report never raises for empty tenant", isinstance(res_empty.get('store_rows'), list))


print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
