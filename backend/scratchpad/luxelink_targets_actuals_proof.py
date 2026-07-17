"""Proof for agent/commission/luxelink-targets-actuals (owner report 2026-07-17).

SYMPTOM: luxelink (Total carrier, b2bsoft POS) Daily-Targets store summary renders stores WITH targets,
but ACHIEVED activations = 0, trending box = 0, conversion 0/0, trending acc $0.00 — while the accessory
"needed today / $-per-day" columns are non-zero (those are TARGET-derived, not achieved).

TWO independent DISPLAY-path defects reproduced + fixed here (NO payout/calc change — calculator.py and
commission_engine.py are untouched; this only touches the shared DISPLAY aggregation + the Daily-Targets
store-code join):

  DEFECT 1 — CLASSIFICATION. `_sales_cell_agg` classified Contract Type via the HARD-CODED
    calculator.classify_contract_type keyword set (Boost-shaped). A Total tenant's Contract Type labels
    ('Prepaid New', 'Renewal', 'Bring Device', …) match none of them → every line → None → prem/upg/byod
    counts 0 → activations/upgrades achieved 0. FIX: per-org `contract_type_map` (mig 213) consumed by the
    new `_resolve_ct_bucket`; a mapped label wins, an unmapped one falls back to the classifier (empty map
    = byte-identical to today for the house).

  DEFECT 2 — STORE-CODE JOIN. `_store_code_resolver` resolved only via commcalc.store_mapping. luxelink has
    none, so its b2bsoft sales-store strings never resolved to its storeops store_codes → the Daily-Targets
    actuals + trending JOIN (scope_actuals_by_day matches on store_code) missed → EVERY achieved/trending
    metric read 0 while targets rendered. FIX: fall back to the org's OWN storeops.stores roster (address /
    store_code) — superset-only, so an existing store_mapping resolution (the house) is byte-identical.

Drives the REAL router functions over an in-memory FakeClient (house style; no DB/network).
Run:  cd backend && python3 scratchpad/luxelink_targets_actuals_proof.py
"""
import os, sys, asyncio, calendar as _cal
from datetime import date as _date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R
from app.modules.commcalc.calculator import classify_contract_type

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


# ── in-memory fake supabase client (honours eq / in_ / neq / count) ──────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.t = table
        self.f = []
        self.rng = None
        self._count = None

    def select(self, *a, **k):
        if k.get('count') == 'exact':
            self._count = True
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def neq(self, c, v):
        self.f.append(('neq', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

    def gte(self, c, v):
        self.f.append(('gte', c, v)); return self

    def lt(self, c, v):
        self.f.append(('lt', c, v)); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def order(self, *a, **k):
        return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v:
                return False
            if k == 'neq' and rv == v:
                return False
            if k == 'in' and rv not in v:
                return False
            if k == 'gte' and not (rv is not None and str(rv) >= str(v)):
                return False
            if k == 'lt' and not (rv is not None and str(rv) < str(v)):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        m = [r for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        if self._count:
            return FakeResult(data=[dict(r) for r in m], count=len(m))
        return FakeResult(data=[dict(r) for r in m])


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeQuery(self.store, t)

    def rpc(self, *a, **k):
        raise Exception('no rpc in this proof')


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, s):
        return FakeSchema(self.store)


ORG = 'lux'
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
MONTHNAME = f"{_cal.month_name[_T.month]} {_T.year}"
PERIOD = MONTHNAME
DIM = _cal.monthrange(_T.year, _T.month)[1]
# pick a "today" mid-open-month so there is an MTD window (guard for the 1st/2nd of a month)
TODAY = _date(_T.year, _T.month, min(15, max(3, _T.day)))
TODAY_ISO = TODAY.isoformat()
DAY = f"{OPEN}-02"

# Total-shaped Contract Type labels that classify_contract_type returns None for (the whole defect):
CT_PREM = 'Prepaid New'
CT_UPG = 'Renewal'
CT_BYOD = 'Bring Device'
CT_NONE = 'Plan Change'


def row(store, rep, tid, ct, day=DAY, cat='', dept='', ext=100.0, gp=20.0, pdesc=''):
    return {'org_id': ORG, 'period': OPEN, 'trans_id': tid, 'trans_date': day, 'store': store,
            'salesperson': rep, 'user_login': (rep or '').lower(), 'category': cat, 'department': dept,
            'contract_type': ct, 'product_desc': pdesc, 'ext_price': ext, 'gp': gp,
            'voided': '', 'trans_type': ''}


# luxelink-shaped feed: Total contract types + a category-based accessory line; store string = a NAME.
def lux_feed(store_str):
    return [
        row(store_str, 'REP1', 'T1', CT_PREM),
        row(store_str, 'REP1', 'T2', CT_UPG),
        row(store_str, 'REP1', 'T3', CT_BYOD),
        row(store_str, 'REP2', 'AC1', '', cat='HandsetBranded', ext=200.0),
    ]


CT_MAP = {CT_PREM: 'premium', CT_UPG: 'upgrade', CT_BYOD: 'byod', CT_NONE: 'none'}


def base_store(feed, ct_map, store_mapping):
    return {
        'daily_sales_feed': [dict(r) for r in feed],
        'raw_sales': [],
        'accessory_config': [{'org_id': ORG, 'departments': [], 'categories': ['HandsetBranded'],
                              'product_keywords': [], 'acima_tenders': [], 'box_departments': [],
                              'setup_fee_keywords': [], 'contract_type_map': ct_map}],
        'store_mapping': store_mapping,
        'stores': [
            {'org_id': ORG, 'store_code': 'LUX-HEMP', 'address': 'HEMPSTEAD', 'market': 'NY',
             'monthly_target': 0},
        ],
        'targets': [
            {'org_id': ORG, 'period': OPEN, 'store_code': 'LUX-HEMP', 'activations_monthly': 70,
             'upgrades_monthly': 10, 'accessories_monthly': 1000},
        ],
        'exec_metric_config': [], 'shifts': [], 'name_map': [], 'rep_aliases': [],
        'store_aliases': [], 'app_config': [], 'flag_rules': [], 'gp_category_map': [],
        'employees': [],
    }


def run_summary(store, period=PERIOD, today_iso=TODAY_ISO):
    c = FakeClient(store)
    _orig = R.sb
    R.sb = lambda: c
    try:
        return asyncio.run(R.get_targets_summary(period=period, today=today_iso, org_id=ORG,
                                                 include_untargeted=False,
                                                 stores=None, markets=None, reps=None)), c
    finally:
        R.sb = _orig


def hemp(summ):
    for s in summ['stores']:
        if s['store_code'] == 'LUX-HEMP':
            return s
    return None


def ach(store_obj, cat):
    return (store_obj['categories'].get(cat) or {}).get('achieved_mtd', 0.0)


# ══ (1) _resolve_ct_bucket — the pure classifier override matrix ═══════════════════════════════════
print("(1) _resolve_ct_bucket override matrix (pure)")
# empty map → byte-identical to classify_contract_type over a battery incl. Boost labels + drift
BATTERY = ['Activation', 'Port-In', 'Port with IDV', 'Add A Line', 'BYOD Activation', 'Upgrade',
           'New Activation', 'AAL', '', 'Prepaid New', 'Renewal', 'Bring Device', 'Plan Change',
           'Standard Activation', 'byod port', 'Eligible Port In Activation']
byte_ident = all(R._resolve_ct_bucket(x, None) == classify_contract_type(x) for x in BATTERY)
check("empty map → byte-identical to classify_contract_type (house unchanged)", byte_ident)
check("empty map {} also byte-identical", all(R._resolve_ct_bucket(x, {}) == classify_contract_type(x) for x in BATTERY))
NORM = {k.lower(): v for k, v in CT_MAP.items()}
check("mapped 'Prepaid New' → premium (was None)",
      classify_contract_type(CT_PREM) is None and R._resolve_ct_bucket(CT_PREM, NORM) == 'premium')
check("mapped 'Renewal' → upgrade (was None)",
      classify_contract_type(CT_UPG) is None and R._resolve_ct_bucket(CT_UPG, NORM) == 'upgrade')
check("mapped 'Bring Device' → byod (was None)",
      classify_contract_type(CT_BYOD) is None and R._resolve_ct_bucket(CT_BYOD, NORM) == 'byod')
check("mapped 'Plan Change' → 'none' → None (force-exclude)", R._resolve_ct_bucket(CT_NONE, NORM) is None)
check("case-insensitive match (PREPAID NEW)", R._resolve_ct_bucket('PREPAID NEW', NORM) == 'premium')
check("unmapped label still falls back to classifier (Upgrade → upgrade)",
      R._resolve_ct_bucket('Upgrade', NORM) == 'upgrade')
check("mapping OVERRIDES the classifier for a mapped label",
      R._resolve_ct_bucket(CT_PREM, {'prepaid new': 'byod'}) == 'byod')

# ══ (2) _accessory_config — reads + normalizes contract_type_map defensively ═══════════════════════
print("(2) _accessory_config resolves contract_type_map (normalized + defensive)")
c = FakeClient(base_store(lux_feed('HEMPSTEAD'), CT_MAP, []))
acfg = R._accessory_config(c, ORG)
check("contract_type_map normalized to lowercased keys/buckets",
      acfg['contract_type_map'] == {'prepaid new': 'premium', 'renewal': 'upgrade',
                                    'bring device': 'byod', 'plan change': 'none'}, acfg['contract_type_map'])
check("contract_type_map_raw preserved for the UI", acfg['contract_type_map_raw'] == CT_MAP)
# missing column / no row → empty map (pre-mig-213 graceful)
c_empty = FakeClient({'accessory_config': [{'org_id': ORG, 'categories': ['HandsetBranded']}],
                      'flag_rules': [], 'gp_category_map': []})
acfg_e = R._accessory_config(c_empty, ORG)
check("missing contract_type_map column → empty map (graceful)", acfg_e['contract_type_map'] == {})

# ══ (3) _sales_cell_agg — a Total ct_map yields non-zero activation buckets ════════════════════════
print("(3) _sales_cell_agg with the ct_map buckets Total labels")
rows = lux_feed('HEMPSTEAD')
cells_nomap = R._sales_cell_agg(rows, R._accessory_config(FakeClient(base_store(rows, {}, [])), ORG))
cells_map = R._sales_cell_agg(rows, acfg)
prem_nomap = sum(len(cc['_prem']) for cc in cells_nomap.values())
prem_map = sum(len(cc['_prem']) for cc in cells_map.values())
upg_map = sum(len(cc['_upg']) for cc in cells_map.values())
byod_map = sum(len(cc['_byod']) for cc in cells_map.values())
acc_map = sum(cc['accessory_rev'] for cc in cells_map.values())
check("no map → 0 activations (reproduces the defect)", prem_nomap == 0)
check("with map → premium=1, upgrade=1, byod=1", prem_map == 1 and upg_map == 1 and byod_map == 1,
      f"prem={prem_map} upg={upg_map} byod={byod_map}")
check("accessory$ classified regardless of ct_map (category-based)", acc_map == 200.0)

# ══ (4) _store_code_resolver — storeops.stores fallback (join robustness) ═══════════════════════════
print("(4) _store_code_resolver storeops fallback")
# no store_mapping, storeops address == sales store string
c_join = FakeClient(base_store(lux_feed('HEMPSTEAD'), CT_MAP, []))
res_join = R._store_code_resolver(c_join, ORG)
check("sales store == storeops address → resolves to store_code (join closes)",
      res_join('HEMPSTEAD') == 'LUX-HEMP', res_join('HEMPSTEAD'))
check("case-insensitive storeops-address match", res_join('hempstead') == 'LUX-HEMP')
check("raw string that IS a storeops code is preserved", res_join('LUX-HEMP') == 'LUX-HEMP')
check("unmatched store string → cleaned raw (needs an alias; owner action)",
      res_join('Hempstead Store #5') == 'Hempstead Store #5')
# store_mapping present (house style) → store_mapping WINS even if storeops has a different code → byte-identical
c_house = FakeClient({'store_mapping': [{'org_id': ORG, 'store_address': 'HEMPSTEAD', 'store_code': 'SM-CODE'}],
                      'stores': [{'org_id': ORG, 'store_code': 'LUX-HEMP', 'address': 'HEMPSTEAD'}],
                      'store_aliases': []})
res_house = R._store_code_resolver(c_house, ORG)
check("store_mapping hit UNCHANGED (house byte-identical; storeops does not override)",
      res_house('HEMPSTEAD') == 'SM-CODE', res_house('HEMPSTEAD'))

# ══ (5) INTEGRATION — get_targets_summary end-to-end ═══════════════════════════════════════════════
print("(5) get_targets_summary — reproduce the symptom + prove the fix")

# 5a: FULL FIX — storeops-resolvable store + ct_map → all achieved metrics non-zero
summ_fix, _ = run_summary(base_store(lux_feed('HEMPSTEAD'), CT_MAP, []))
h = hemp(summ_fix)
check("5a store renders with its target (as owner sees)", h is not None and h['categories']['activations']['monthly'] == 70)
check("5a activations ACHIEVED > 0 (fix: 2 = prem+byod)", h and ach(h, 'activations') == 2, ach(h, 'activations') if h else None)
check("5a upgrades ACHIEVED > 0 (fix: 1)", h and ach(h, 'upgrades') == 1)
check("5a accessory ACHIEVED > 0 (200)", h and ach(h, 'accessories') == 200.0)
check("5a conversion no longer 0/0 → boxes+bill both derive (billpays present? conversion computed)",
      h is not None and 'conversion' in h)

# 5b: JOIN works but NO map → accessory achieved > 0 while activations stay 0 (isolates DEFECT 1)
summ_nomap, _ = run_summary(base_store(lux_feed('HEMPSTEAD'), {}, []))
h2 = hemp(summ_nomap)
check("5b (no ct_map) activations ACHIEVED == 0 (classification defect isolated)", h2 and ach(h2, 'activations') == 0)
check("5b (no ct_map) accessory ACHIEVED still > 0 (category unaffected)", h2 and ach(h2, 'accessories') == 200.0)

# 5c: JOIN cannot resolve (sales store string ∉ storeops) → EVERYTHING achieved 0 (DEFECT 2 / alias-needed)
summ_join, _ = run_summary(base_store(lux_feed('Hempstead Store #5'), CT_MAP, []))
h3 = hemp(summ_join)
check("5c (unmatched store) store still renders with target", h3 is not None and h3['categories']['activations']['monthly'] == 70)
check("5c (unmatched store) activations ACHIEVED == 0 (join miss)", h3 and ach(h3, 'activations') == 0)
check("5c (unmatched store) accessory ACHIEVED == 0 too (join miss, uniform zeros)", h3 and ach(h3, 'accessories') == 0.0)
check("5c accessory NEED still > 0 (target − achieved) — the non-zero owner saw is TARGET-derived, not achieved",
      h3 and (h3['categories']['accessories'].get('need') or 0) > 0)

# ══ (6) MONEY-SAFETY — the payout classifier is untouched ══════════════════════════════════════════
print("(6) money-safety: calculator.classify_contract_type unchanged")
check("classify_contract_type still returns None for the Total labels (money path unaffected)",
      classify_contract_type(CT_PREM) is None and classify_contract_type(CT_UPG) is None)
check("classify_contract_type still classifies Boost labels (byte-identical money path)",
      classify_contract_type('Activation') == 'premium' and classify_contract_type('Upgrade') == 'upgrade'
      and classify_contract_type('BYOD') == 'byod')

print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
