"""Proof for agent/commission/targets-filters-trending (owner request 2026-07-17).

Two deliverables, both DISPLAY-ONLY (no payout/calc change):
  1. RULE FIVE standardized filters (store(s) / market(s) / rep(s)) on the Targets summary
     (`get_targets_summary`), applied SERVER-SIDE.
  2. A Trending Accessories (+ Trending Box) column on the Targets surfaces, read DIRECTLY from
     Executive MTD's `_exec_mtd` via `_targets_trending_by_code` — ONE source, moves together.

Drives the REAL router functions over an in-memory FakeClient (house style; no DB/network):
  (a) BYTE-EQUALITY: for identical org/period/today inputs, each store's `trending_acc_sales` /
      `trending_box` on the Targets summary EQUALS Executive MTD's `by_location` trending for the
      SAME store — because the summary literally calls `_exec_mtd`. Also the achieved-accessory base
      the trending projects from equals Exec MTD's `acc_sales`.
  (a2) store-spelling MERGE: two raw feed spellings that resolve to one store_code sum into that
       code's single Targets row (trending == the sum of the two Exec MTD rows).
  (b) FILTERS constrain the aggregation SERVER-SIDE: store / market / rep selections drop rows in the
      endpoint (not client-side), the `filters` options are pick-don't-type over real data, and the
      trending sum over the shown stores tracks the selection.
  (c) rep filter keeps the store-level target/achieved/trending WHOLE-STORE (a store target can't be
      split per rep) while narrowing the per-rep breakdown; the trend factor matches Exec MTD's.

Run:  cd backend && python3 scratchpad/targets_filters_trending_proof.py
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

    def select(self, *a, **k):
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


ORG = 'o'
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"                 # the current (open) month (raw_sales/feed spelling)
MONTHNAME = f"{_cal.month_name[_T.month]} {_T.year}"
PERIOD = MONTHNAME                                 # the API period label (_period_bounds needs 'Month YYYY')
DIM = _cal.monthrange(_T.year, _T.month)[1]
TODAY = _date(_T.year, _T.month, 10)              # injected "today" → MTD cut at the 10th, elapsed = 9
TODAY_ISO = TODAY.isoformat()
ELAPSED = 9                                       # TODAY.day - 1
FACTOR = DIM / ELAPSED                             # open-month trend factor
D05 = f"{OPEN}-05"
D08 = f"{OPEN}-08"

S1_ADDR = '3 Palisade Ave'
S2_ADDR = '100 Main St'


def row(store, rep, tid, ct, day=D05, cat='CellPhone', dept='', ext=100.0, gp=20.0, pdesc=''):
    return {'org_id': ORG, 'period': OPEN, 'trans_id': tid, 'trans_date': day, 'store': store,
            'salesperson': rep, 'user_login': (rep or '').lower(), 'category': cat, 'department': dept,
            'contract_type': ct, 'product_desc': pdesc, 'ext_price': ext, 'gp': gp,
            'voided': '', 'trans_type': ''}


def base_store(feed):
    return {
        'daily_sales_feed': [dict(r) for r in feed],
        'raw_sales': [],
        'accessory_config': [{'org_id': ORG, 'departments': [], 'categories': ['accessory'],
                              'product_keywords': [], 'acima_tenders': []}],
        'store_mapping': [
            {'org_id': ORG, 'store_address': S1_ADDR, 'store_code': 'S1', 'market': 'North'},
            {'org_id': ORG, 'store_address': S2_ADDR, 'store_code': 'S2', 'market': 'South'},
        ],
        'stores': [
            {'org_id': ORG, 'store_code': 'S1', 'address': S1_ADDR, 'market': 'North', 'monthly_target': 0},
            {'org_id': ORG, 'store_code': 'S2', 'address': S2_ADDR, 'market': 'South', 'monthly_target': 0},
        ],
        'targets': [
            {'org_id': ORG, 'period': OPEN, 'store_code': 'S1', 'activations_monthly': 10,
             'upgrades_monthly': 5, 'accessories_monthly': 1000},
            {'org_id': ORG, 'period': OPEN, 'store_code': 'S2', 'activations_monthly': 5,
             'upgrades_monthly': 3, 'accessories_monthly': 500},
        ],
        'exec_metric_config': [], 'shifts': [], 'name_map': [], 'rep_aliases': [],
        'store_aliases': [], 'app_config': [],
    }


FEED = [
    row(S1_ADDR, 'ALICE', 'A1', 'Activation', ext=100.0),
    row(S1_ADDR, 'BOB', 'U1', 'Upgrade', ext=100.0),
    row(S1_ADDR, 'CARLA', 'AC1', '', day=D08, cat='Accessory', ext=40.0),
    row(S1_ADDR, 'ALICE', 'AC2', '', day=D08, cat='Accessory', ext=10.0),
    row(S2_ADDR, 'DAN', 'U2', 'Upgrade', ext=100.0),
    row(S2_ADDR, 'DAN', 'AC3', '', day=D08, cat='Accessory', ext=60.0),
]


def run_summary(store, stores=None, markets=None, reps=None, period=PERIOD, today_iso=TODAY_ISO):
    c = FakeClient(store)
    _orig = R.sb
    R.sb = lambda: c
    try:
        return run_route(R.get_targets_summary(period=period, today=today_iso, org_id=ORG,
                                                 stores=stores, markets=markets, reps=reps)), c
    finally:
        R.sb = _orig


# ══ (a) BYTE-EQUALITY: Targets trending == Executive MTD trending, per store ══════════════════════
print("(a) Targets trending == Executive MTD trending (same _exec_mtd, byte-for-byte)")
store = base_store(FEED)
summ, c = run_summary(store)
ex = R._exec_mtd(c, ORG, PERIOD, today=TODAY)
ex_by_store = {r['store']: r for r in ex['by_location']['rows']}
by_code = {s['store_code']: s for s in summ['stores']}

check("summary returned both target stores", set(by_code) == {'S1', 'S2'}, set(by_code))
check("open-month trend factor matches Exec MTD",
      round(summ['trending']['factor'], 6) == round(ex['trending']['factor'], 6),
      f"{summ['trending']} vs {ex['trending']}")
check("trend factor is a real projection (>1 mid-open-month)", summ['trending']['factor'] > 1.0)

for code, addr in (('S1', S1_ADDR), ('S2', S2_ADDR)):
    st = by_code[code]
    exr = ex_by_store[addr]
    check(f"{code}: Targets trending_acc_sales == Exec MTD trending_acc_sales",
          st['trending_acc_sales'] == exr['trending_acc_sales'],
          f"{st['trending_acc_sales']} vs {exr['trending_acc_sales']}")
    check(f"{code}: Targets trending_box == Exec MTD trending_box",
          st['trending_box'] == exr['trending_box'], f"{st['trending_box']} vs {exr['trending_box']}")
    # the achieved-accessory base the trending projects from == Exec MTD's acc_sales for that store
    ach = st['categories']['accessories']['achieved_mtd']
    check(f"{code}: Targets achieved accessories == Exec MTD acc_sales (shared base)",
          round(ach, 2) == round(exr['acc_sales'], 2), f"{ach} vs {exr['acc_sales']}")
    # trending is exactly base × the shared factor (rounded like Exec MTD)
    check(f"{code}: trending_acc_sales == round(acc_sales × factor, 2)",
          st['trending_acc_sales'] == round(exr['acc_sales'] * ex['trending']['factor'], 2))

# concrete hand-values (dim-independent identities)
check("S1 achieved accessories == $50 (40 + 10)", by_code['S1']['categories']['accessories']['achieved_mtd'] == 50.0,
      by_code['S1']['categories']['accessories']['achieved_mtd'])
check("S2 achieved accessories == $60", by_code['S2']['categories']['accessories']['achieved_mtd'] == 60.0)
check("S1 trending_acc_sales == round(50 × factor, 2)", by_code['S1']['trending_acc_sales'] == round(50.0 * FACTOR, 2),
      by_code['S1']['trending_acc_sales'])
check("S1 trending_box == round(2 × factor)", by_code['S1']['trending_box'] == round(2 * FACTOR),
      by_code['S1']['trending_box'])

# ══ (a2) store-spelling MERGE — two feed spellings → one code → summed trending ═══════════════════
print("\n(a2) two raw store spellings resolving to one store_code → one Targets row (summed trending)")
feed_m = FEED + [row('3 Palisade Avenue', 'ERIN', 'AC9', '', day=D08, cat='Accessory', ext=25.0)]
store_m = base_store(feed_m)
store_m['store_mapping'].append({'org_id': ORG, 'store_address': '3 Palisade Avenue', 'store_code': 'S1', 'market': 'North'})
store_m['stores'].append({'org_id': ORG, 'store_code': 'S1', 'address': S1_ADDR, 'market': 'North', 'monthly_target': 0})
# de-dupe the storeops row we just doubled (endpoint would iterate both; keep one canonical S1)
store_m['stores'] = [{'org_id': ORG, 'store_code': 'S1', 'address': S1_ADDR, 'market': 'North', 'monthly_target': 0},
                     {'org_id': ORG, 'store_code': 'S2', 'address': S2_ADDR, 'market': 'South', 'monthly_target': 0}]
summ_m, cm = run_summary(store_m)
ex_m = R._exec_mtd(cm, ORG, PERIOD, today=TODAY)
ex_m_by = {r['store']: r for r in ex_m['by_location']['rows']}
s1_m = {s['store_code']: s for s in summ_m['stores']}['S1']
check("Exec MTD emits TWO rows for the two spellings",
      {'3 Palisade Ave', '3 Palisade Avenue'} <= set(ex_m_by), set(ex_m_by))
exp_merged_acc = round((ex_m_by['3 Palisade Ave']['trending_acc_sales']
                        + ex_m_by['3 Palisade Avenue']['trending_acc_sales']), 2)
check("merged S1 trending_acc_sales == sum of the two Exec rows",
      s1_m['trending_acc_sales'] == exp_merged_acc, f"{s1_m['trending_acc_sales']} vs {exp_merged_acc}")
check("merged S1 achieved accessories == $75 (40 + 10 + 25)",
      s1_m['categories']['accessories']['achieved_mtd'] == 75.0,
      s1_m['categories']['accessories']['achieved_mtd'])

# ══ (b) FILTERS constrain the aggregation SERVER-SIDE + pick-don't-type options ═══════════════════
print("\n(b) filters constrain SERVER-SIDE + options are pick-don't-type over real data")
# options
opt_store_vals = {o['value'] for o in summ['filters']['stores']}
opt_store_lbls = {o['value']: o['label'] for o in summ['filters']['stores']}
check("store options list real store_codes", opt_store_vals == {'S1', 'S2'}, opt_store_vals)
check("store options carry the address as label (pick-don't-type)",
      opt_store_lbls.get('S1') == S1_ADDR and opt_store_lbls.get('S2') == S2_ADDR, opt_store_lbls)
check("market options == real markets", summ['filters']['markets'] == ['North', 'South'], summ['filters']['markets'])
check("rep options == reps present in the period's actuals",
      set(summ['filters']['reps']) == {'ALICE', 'BOB', 'CARLA', 'DAN'}, summ['filters']['reps'])

# store filter
s_only, _ = run_summary(base_store(FEED), stores=['S1'])
check("store filter [S1] returns ONLY S1 (server-side)", [s['store_code'] for s in s_only['stores']] == ['S1'],
      [s['store_code'] for s in s_only['stores']])
check("store filter echoes `applied.stores`", s_only['applied']['stores'] == ['s1'], s_only['applied'])
check("filtered trending sum == S1's trending (drives the tile)",
      round(sum(s['trending_acc_sales'] for s in s_only['stores']), 2) == by_code['S1']['trending_acc_sales'])

# market filter
m_only, _ = run_summary(base_store(FEED), markets=['South'])
check("market filter [South] returns ONLY S2", [s['store_code'] for s in m_only['stores']] == ['S2'],
      [s['store_code'] for s in m_only['stores']])

# rep filter — keeps only stores where the rep worked, narrows the per-rep breakdown
r_only, _ = run_summary(base_store(FEED), reps=['DAN'])
check("rep filter [DAN] returns ONLY S2 (DAN's store)", [s['store_code'] for s in r_only['stores']] == ['S2'],
      [s['store_code'] for s in r_only['stores']])
check("rep filter narrows the per-rep breakdown to DAN",
      [rp['rep'] for rp in r_only['stores'][0]['reps']] == ['DAN'], r_only['stores'][0]['reps'])

r_alice, _ = run_summary(base_store(FEED), reps=['ALICE'])
check("rep filter [ALICE] returns ONLY S1", [s['store_code'] for s in r_alice['stores']] == ['S1'])
check("rep filter [ALICE] drops BOB/CARLA from the breakdown",
      [rp['rep'] for rp in r_alice['stores'][0]['reps']] == ['ALICE'])

# combined store + rep
combo, _ = run_summary(base_store(FEED), stores=['S1', 'S2'], reps=['ALICE'])
check("store+rep combo [S1,S2]+[ALICE] → only S1", [s['store_code'] for s in combo['stores']] == ['S1'])

# a filter that matches nothing → empty, but options still present (so it can be changed)
none_match, _ = run_summary(base_store(FEED), reps=['NOBODY'])
check("non-matching rep filter → zero stores", none_match['stores'] == [])
check("options still returned on an empty result (selection is changeable)",
      set(o['value'] for o in none_match['filters']['stores']) == {'S1', 'S2'})

# ══ (c) rep filter keeps store headline WHOLE-STORE (target can't split per rep) ══════════════════
print("\n(c) rep filter keeps store-level target/achieved/trending WHOLE-STORE")
# S2 under rep filter [DAN] — DAN IS the only S2 rep, so whole-store == DAN anyway; assert the store
# headline trending equals the UNFILTERED S2 trending (proves it's read whole-store from Exec MTD).
check("S2 trending under rep filter == unfiltered S2 trending (whole-store)",
      r_only['stores'][0]['trending_acc_sales'] == by_code['S2']['trending_acc_sales'],
      f"{r_only['stores'][0]['trending_acc_sales']} vs {by_code['S2']['trending_acc_sales']}")
# S1 under rep filter [ALICE]: ALICE alone did not sell the accessories CARLA did, but the store
# headline achieved/trending stay WHOLE-STORE (== unfiltered S1), only the breakdown narrows.
check("S1 achieved accessories under [ALICE] filter stays whole-store ($50)",
      r_alice['stores'][0]['categories']['accessories']['achieved_mtd'] == 50.0,
      r_alice['stores'][0]['categories']['accessories']['achieved_mtd'])
check("S1 trending under [ALICE] filter stays whole-store == unfiltered S1",
      r_alice['stores'][0]['trending_acc_sales'] == by_code['S1']['trending_acc_sales'])

# ══ (d) closed/past month → trending == actual (factor 1) ════════════════════════════════════════
print("\n(d) a closed/past month trends at factor 1.0 (trending == achieved)")
# force a PAST month by using a period that is not the current calendar month.
py, pm = (_T.year - 1, 12)
PAST = f"{py}-{pm:02d}"
PAST_NAME = f"{_cal.month_name[pm]} {py}"
past_feed = [dict(r, period=PAST, trans_date=f"{PAST}-05") for r in FEED]
store_p = base_store(FEED)
store_p['daily_sales_feed'] = past_feed
store_p['raw_sales'] = [dict(r) for r in past_feed]
for t in store_p['targets']:
    t['period'] = PAST
summ_p, _ = run_summary(store_p, period=PAST_NAME, today_iso=f"{PAST}-15")
check("closed month trend factor == 1.0", round(summ_p['trending']['factor'], 6) == 1.0, summ_p['trending'])
pcode = {s['store_code']: s for s in summ_p['stores']}
if 'S1' in pcode:
    check("closed month S1 trending_acc_sales == achieved accessories (no projection)",
          pcode['S1']['trending_acc_sales'] == pcode['S1']['categories']['accessories']['achieved_mtd'],
          f"{pcode['S1']['trending_acc_sales']} vs {pcode['S1']['categories']['accessories']['achieved_mtd']}")
else:
    check("closed month returned S1", False, list(pcode))

print(f"\n=================  {PASS} passed, {FAIL} failed  =================")
sys.exit(1 if FAIL else 0)
