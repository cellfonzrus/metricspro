"""Proof harness for agent/commission/tenant-report-fixes2.

Drives the REAL router functions over an in-memory FakeClient + the REAL luxelink Total-Wireless sample
file. No DB, no network.  Run:  python3 backend/scratchpad/tenant_report_fixes2_proof.py

Covers:
  A) accessory classifier — Boost (empty/seeded 'Ondigo') byte-identical to today; luxelink categories now
     recognized (before/after $); flag_rules fallback preserved; gp_category_map 'accessory' bridge.
  B) targets unified read — _sales_rows_union_txn (feed∪raw dedup by trans_id) + _compute_feed_actuals_py.
  C) tax drill-down — per-store + per-day grouping, market resolution, date-range + unified source.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R

HOUSE = '00000000-0000-0000-0000-000000000001'
LUX = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
SAMPLE = "/workspaces/commcalc/commcalc/My Sales Transaction Details Legacy New with all columns (3).xlsx"

PASS = 0; FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok  {name}")
    else: FAIL += 1; print(f"FAIL  {name}   {extra}")

# ── in-memory fake supabase client ────────────────────────────────────────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None): self.data = data or []; self.count = count
class FakeQuery:
    def __init__(self, store, table):
        self.store = store; self.t = table; self.f = []; self.cnt = False
        self.op = 'select'; self.ins = None; self.rng = None; self.conflict = None
    def select(self, *a, **k):
        if k.get('count') == 'exact': self.cnt = True
        return self
    def eq(self, c, v): self.f.append(('eq', c, v)); return self
    def neq(self, c, v): self.f.append(('neq', c, v)); return self
    def in_(self, c, v): self.f.append(('in', c, list(v))); return self
    def limit(self, n): return self
    def range(self, a, b): self.rng = (a, b); return self
    def order(self, *a, **k): return self
    def delete(self): self.op = 'delete'; return self
    def insert(self, rows): self.op = 'insert'; self.ins = rows if isinstance(rows, list) else [rows]; return self
    def upsert(self, rows, **k):
        self.op = 'upsert'; self.conflict = k.get('on_conflict')
        self.ins = rows if isinstance(rows, list) else [rows]; return self
    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v: return False
            if k == 'neq' and rv == v: return False
            if k == 'in' and rv not in v: return False
        return True
    def execute(self):
        rows = self.store.setdefault(self.t, [])
        if self.op == 'select':
            m = [r for r in rows if self._m(r)]
            if self.rng: a, b = self.rng; m = m[a:b + 1]
            if self.cnt: return FakeResult(data=m, count=len(m))
            return FakeResult(data=[dict(r) for r in m])
        if self.op == 'delete':
            self.store[self.t] = [r for r in rows if not self._m(r)]
            return FakeResult(data=[])
        if self.op == 'upsert':
            keys = [k.strip() for k in (self.conflict or '').split(',') if k.strip()]
            for nr in self.ins:
                if keys:
                    self.store[self.t] = [r for r in self.store[self.t]
                                          if not all(r.get(k) == nr.get(k) for k in keys)]
                self.store[self.t].append(dict(nr))
            return FakeResult(data=list(self.ins))
        if self.op == 'insert':
            for r in self.ins: rows.append(dict(r))
            return FakeResult(data=list(self.ins))
        return FakeResult()
class FakeSchema:
    def __init__(self, store): self.store = store
    def table(self, t): return FakeQuery(self.store, t)
    def rpc(self, *a, **k):
        class _E:
            def execute(self): raise Exception("no rpc in fake")
        return _E()
class FakeClient:
    def __init__(self, store=None): self.store = store if store is not None else {}
    def schema(self, s): return FakeSchema(self.store)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ACCESSORY CLASSIFIER (per-org config) ==")

# A0. House, EVERYTHING empty (no accessory_config, no flag_rules, no gp_category_map) → ['Ondigo'].
c = FakeClient()
acfg = R._accessory_config(c, HOUSE)
check("house empty → default department 'Ondigo' (byte-identical Boost)",
      acfg['departments'] == {'ondigo'} and acfg['departments_list'] == ['Ondigo']
      and acfg['categories'] == set() and acfg['products'] == set(), acfg['departments_list'])

# A1. House with a LEGACY flag_rules row (pre-mig-208) → preserved EXACTLY via the fallback path.
c = FakeClient({'flag_rules': [{'id': 1, 'org_id': HOUSE,
    'accessory_departments': ['Ondigo', 'Widgets'], 'accessory_categories': ['acc-cat'],
    'accessory_product_keywords': ['case'], 'acima_tenders': ['acima lease']}]})
acfg = R._accessory_config(c, HOUSE)
check("house pre-208 flag_rules fallback preserved (depts)", acfg['departments'] == {'ondigo', 'widgets'})
check("house pre-208 flag_rules fallback preserved (cats)", acfg['categories'] == {'acc-cat'})
check("house pre-208 flag_rules fallback preserved (kws)", acfg['products'] == {'case'})
check("house pre-208 flag_rules fallback preserved (acima)", acfg['acima_tenders_list'] == ['acima lease'])

# A1b. A NON-house org gets NOTHING from the flag_rules singleton (the root-cause bug) → falls to Ondigo.
#      (The singleton row is the house's; luxelink's .eq(org_id=LUX) matches nothing.)
acfg_lux_pre = R._accessory_config(c, LUX)
check("ROOT CAUSE: luxelink pre-208 sees flag_rules singleton as empty → 'Ondigo' (matches nothing)",
      acfg_lux_pre['departments'] == {'ondigo'} and acfg_lux_pre['categories'] == set())

# A2. accessory_config (mig 208) row wins over the singleton, PER ORG.
c = FakeClient({
    'flag_rules': [{'id': 1, 'org_id': HOUSE, 'accessory_departments': ['Ondigo']}],
    'accessory_config': [
        {'org_id': HOUSE, 'departments': ['Ondigo'], 'categories': [], 'product_keywords': [], 'acima_tenders': []},
        {'org_id': LUX, 'departments': [], 'categories': ['HandsetBranded', 'Accessories', 'Accessory'],
         'product_keywords': [], 'acima_tenders': []},
    ]})
ah = R._accessory_config(c, HOUSE)
al = R._accessory_config(c, LUX)
check("mig-208 house row → still ['Ondigo'] (byte-identical)", ah['departments'] == {'ondigo'} and ah['categories'] == set())
check("mig-208 luxelink row → its categories recognized",
      al['categories'] == {'handsetbranded', 'accessories', 'accessory'} and al['departments'] == set())
check("per-org isolation: house and luxelink configs do not bleed", ah['categories'] == set() and al['departments'] == set())

# A3. gp_category_map 'accessory' department bridge (REUSE mig 069) — additive, empty-safe.
c = FakeClient({'gp_category_map': [
    {'org_id': LUX, 'department': 'BrandedHandset', 'category': 'accessory'},
    {'org_id': LUX, 'department': 'Rtr', 'category': 'other'},   # non-accessory → ignored
    {'org_id': HOUSE, 'department': 'Anything', 'category': 'device'}]})
al = R._accessory_config(c, LUX)
ah = R._accessory_config(c, HOUSE)
check("gp_category_map accessory dept bridged into accessory departments", 'brandedhandset' in al['departments'])
check("gp_category_map non-accessory row NOT bridged", 'rtr' not in al['departments'])
check("gp_category_map bridge empty-safe for house (no accessory rows) → 'Ondigo'", ah['departments'] == {'ondigo'})

# A4. put_accessory_config writes PER ORG (upsert on org_id) and does NOT touch the house singleton.
store = {'accessory_config': [{'org_id': HOUSE, 'departments': ['Ondigo'], 'categories': [],
                               'product_keywords': [], 'acima_tenders': []}]}
c = FakeClient(store)
R.sb = lambda: c
R.require_org = lambda x: None
R.put_accessory_config({'departments': [], 'categories': ['HandsetBranded', 'Accessories'],
                        'product_keywords': [], 'acima_tenders': []}, org_id=LUX)
rows = store['accessory_config']
house_row = [r for r in rows if r['org_id'] == HOUSE]
lux_row = [r for r in rows if r['org_id'] == LUX]
check("put_accessory_config(LUX) created a LUX row", len(lux_row) == 1 and set(lux_row[0]['categories']) == {'HandsetBranded', 'Accessories'})
check("put_accessory_config(LUX) did NOT overwrite the house row (multi-tenant corruption fixed)",
      len(house_row) == 1 and house_row[0]['departments'] == ['Ondigo'])

# A5. THE MONEY NUMBER — luxelink accessory $ before vs after, over the REAL sample, using _is_accessory.
import openpyxl
wb = openpyxl.load_workbook(SAMPLE, data_only=True); ws = wb.active
xrows = list(ws.iter_rows(values_only=True))
hdr = [str(h).strip() if h is not None else '' for h in xrows[0]]
def _ix(n):
    for i, h in enumerate(hdr):
        if h.lower() == n.lower(): return i
di, ci, sci, ei, vi, tti = (_ix('Department'), _ix('Category'), _ix('System Category'),
                            _ix('Ext Price'), _ix('Voided'), _ix('Trans Type'))
# Mimic the ingest: category = Category-col OR System-Category-col.
sample = []
for r in xrows[1:]:
    catcol = r[ci] if r[ci] is not None else ''
    syscol = r[sci] if r[sci] is not None else ''
    sample.append({
        'department': (str(r[di]).strip() if r[di] is not None else ''),
        'category': (str(catcol).strip() or str(syscol).strip()),
        'product_desc': '', 'ext_price': r[ei],
        'voided': str(r[vi] or '').strip(), 'trans_type': str(r[tti] or '').strip()})
def _acc_dollars(acfg):
    tot = 0.0; n = 0
    for r in sample:
        if str(r['voided']).upper() == 'YES' or r['trans_type'] == 'Return':
            continue
        if R._is_accessory(r['department'], r['category'], r['product_desc'], acfg):
            tot += R.safe_float(r['ext_price']); n += 1
    return round(tot, 2), n
before_cfg = R._accessory_config(FakeClient(), LUX)                       # empty → 'Ondigo'
after_cfg = R._accessory_config(FakeClient({'accessory_config': [
    {'org_id': LUX, 'departments': [], 'categories': ['HandsetBranded', 'Accessories', 'Accessory'],
     'product_keywords': [], 'acima_tenders': []}]}), LUX)
b_tot, b_n = _acc_dollars(before_cfg)
a_tot, a_n = _acc_dollars(after_cfg)
print(f"      luxelink accessory$  BEFORE (Ondigo default) = ${b_tot:,.2f} ({b_n} lines)")
print(f"      luxelink accessory$  AFTER  (categories cfg)  = ${a_tot:,.2f} ({a_n} lines)")
check("luxelink BEFORE = $0.00 (Ondigo matches nothing)", b_tot == 0.0 and b_n == 0)
check("luxelink AFTER recognizes accessory categories (> $0)", a_tot > 0 and a_n > 0)

# A6. BOOST BYTE-IDENTICAL over the SAME sample: with the current 'Ondigo' default the classifier output
#     is IDENTICAL whether resolved the old way (flag_rules singleton empty) or the new way (all empty).
#     _is_accessory is unchanged; only the CONFIG SOURCE moved — so an org whose effective config is the
#     same default gets the same buckets/$$ byte-for-byte.
old_default = {'departments': {'ondigo'}, 'categories': set(), 'products': set(),
               'departments_list': ['Ondigo'], 'categories_list': [], 'products_list': [], 'acima_tenders_list': []}
new_default = R._accessory_config(FakeClient(), HOUSE)
od_tot, od_n = _acc_dollars(old_default)
nd_tot, nd_n = _acc_dollars(new_default)
check("BOOST byte-identical: old-default vs new-resolver default give identical $$/lines",
      (od_tot, od_n) == (nd_tot, nd_n), f"{od_tot}/{od_n} vs {nd_tot}/{nd_n}")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. TARGETS UNIFIED READ (_sales_rows_union_txn + _compute_feed_actuals_py) ==")
PERIOD = 'July 2026'   # open month (today 2026-07-15)

def feed_row(tid, day, ext=10.0, cat='cellphone', ct='Activation', store='S1', rep='ALICE', tax=0.0):
    return {'org_id': LUX, 'period': '2026-07', 'trans_id': tid, 'trans_date': f'2026-07-{day:02d}',
            'store': store, 'salesperson': rep, 'user_login': rep.lower(), 'contract_type': ct,
            'department': '', 'category': cat, 'product_desc': '', 'gp': 0.0, 'ext_price': ext,
            'tax': tax, 'voided': '', 'trans_type': ''}
def raw_row(tid, day, **kw):
    r = feed_row(tid, day, **kw); r['period'] = 'July 2026'; return r

# B1. feed-only (raw_sales empty) → union == feed verbatim (byte-identical Boost).
store = {'daily_sales_feed': [feed_row('t1', 1), feed_row('t2', 2)], 'raw_sales': []}
c = FakeClient(store)
rows, meta = R._sales_rows_union_txn(c, LUX, PERIOD, cols='trans_id,trans_date,store,ext_price,tax,voided,trans_type,category,contract_type,salesperson,user_login,department,product_desc,gp')
check("B feed-only → union == feed rows (byte-identical)", len(rows) == 2 and meta['other_only_rows'] == 0)

# B2. feed ∪ raw with an OVERLAPPING trans_id → feed wins, no double count; raw-only trans added.
store = {'daily_sales_feed': [feed_row('t1', 1), feed_row('t2', 2)],
         'raw_sales': [raw_row('t1', 1), raw_row('t9', 3)]}   # t1 overlaps, t9 is monthly-only
c = FakeClient(store)
rows, meta = R._sales_rows_union_txn(c, LUX, PERIOD, cols='trans_id,trans_date,store,ext_price,tax,voided,trans_type,category,contract_type,salesperson,user_login,department,product_desc,gp')
tids = sorted(r['trans_id'] for r in rows)
check("B overlap t1 counted ONCE (feed wins) + monthly-only t9 added", tids == ['t1', 't2', 't9'], tids)
check("B meta other_only_rows == 1 (just t9)", meta['other_only_rows'] == 1)

# B3. multiple LINE ITEMS per transaction are preserved (not collapsed by trans_id).
store = {'daily_sales_feed': [feed_row('m1', 1, cat='accessory', ct=''),
                              feed_row('m1', 1, cat='accessory', ct='')],  # 2 lines, same trans
         'raw_sales': []}
c = FakeClient(store)
rows, _ = R._sales_rows_union_txn(c, LUX, PERIOD, cols='trans_id,trans_date,store,ext_price,tax,voided,trans_type,category,contract_type,salesperson,user_login,department,product_desc,gp')
check("B both line items of a transaction survive the union", len(rows) == 2)

# B4. blank-trans_id monthly rows are kept (mirror promotion's monthly_only(None)).
store = {'daily_sales_feed': [feed_row('t1', 1)],
         'raw_sales': [dict(raw_row('', 4), trans_id=None)]}
c = FakeClient(store)
rows, _ = R._sales_rows_union_txn(c, LUX, PERIOD, cols='trans_id,trans_date,store,ext_price,tax,voided,trans_type,category,contract_type,salesperson,user_login,department,product_desc,gp')
check("B blank-trans_id raw row kept in the union", len(rows) == 2)

# B5. _compute_feed_actuals_py over the UNION rows → correct activation/accessory aggregation.
acfg_store = {'accessory_config': [{'org_id': LUX, 'departments': [], 'categories': ['accessory'],
                                    'product_keywords': [], 'acima_tenders': []}]}
store = {**acfg_store,
         'daily_sales_feed': [feed_row('a1', 1, cat='cellphone', ct='Activation'),
                              feed_row('acc1', 1, ext=25.0, cat='accessory', ct='')],
         'raw_sales': [raw_row('a1', 1, cat='cellphone', ct='Activation'),   # overlap
                       raw_row('up9', 2, cat='cellphone', ct='Upgrade')],    # monthly-only upgrade
         'store_mapping': []}
c = FakeClient(store)
urows, _ = R._sales_rows_union_txn(c, LUX, PERIOD, cols=R._ACTUALS_COLS)
acts = R._compute_feed_actuals_py(c, LUX, PERIOD, rows=urows)
prem = sum(a['prem_count'] for a in acts); upg = sum(a['upg_count'] for a in acts)
accg = sum(a['acc_gp'] for a in acts)
check("B actuals: 1 activation (a1 deduped), 1 upgrade (up9 from raw), acc$ 25", prem == 1 and upg == 1 and accg == 25.0,
      f"prem={prem} upg={upg} acc={accg}")

# B6. _fetch_actuals end-to-end (union wired in) returns the same aggregation.
c = FakeClient(store)
R.sb = lambda: c
fa = R._fetch_actuals(c, LUX, PERIOD)
check("B _fetch_actuals uses the union (activation + upgrade + acc present)",
      sum(a['prem_count'] for a in fa) == 1 and sum(a['upg_count'] for a in fa) == 1
      and sum(a['acc_gp'] for a in fa) == 25.0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. TAX DRILL-DOWN (tax_collected: per-store+day, market, date-range, unified) ==")
def tx(tid, day, store, ext, tax):
    return {'org_id': LUX, 'period': '2026-07', 'trans_id': tid, 'trans_date': f'2026-07-{day:02d}',
            'store': store, 'ext_price': ext, 'tax': tax, 'voided': '', 'trans_type': ''}
store = {
    'daily_sales_feed': [tx('t1', 1, '3 Palisade Ave', 100.0, 8.0),
                         tx('t2', 2, '3 Palisade Ave', 50.0, 4.0),
                         tx('t3', 2, '9 Main St', 200.0, 16.0)],
    'raw_sales': [tx('t1', 1, '3 Palisade Ave', 100.0, 8.0),        # overlap → once
                  tx('t9', 5, '9 Main St', 300.0, 24.0)],           # monthly-only
    'store_mapping': [{'org_id': LUX, 'store_code': 'B-3PL', 'store_address': '3 Palisade Ave', 'market': 'North'},
                      {'org_id': LUX, 'store_code': 'B-9MN', 'store_address': '9 Main St', 'market': 'South'}],
}
c = FakeClient(store)
R.sb = lambda: c
R.require_org = lambda x: None
res = R.tax_collected(period=PERIOD, org_id=LUX)
by = {s['store']: s for s in res['stores']}
check("C unified: overlap t1 tax counted once (Palisade tax = 8+4 = 12)", by['3 Palisade Ave']['tax'] == 12.0, by['3 Palisade Ave']['tax'])
check("C monthly-only t9 included (Main St tax = 16+24 = 40)", by['9 Main St']['tax'] == 40.0, by['9 Main St']['tax'])
check("C market resolved from store_mapping", by['3 Palisade Ave']['market'] == 'North' and by['9 Main St']['market'] == 'South')
check("C markets list returned for the picker", set(res['markets']) == {'North', 'South'})
check("C per-day drill present", len(by['3 Palisade Ave']['days']) == 2
      and by['3 Palisade Ave']['days'][0]['date'] == '2026-07-01')
check("C effective rate computed (Palisade 12/150 = 8%)", by['3 Palisade Ave']['effective_rate'] == 8.0)
check("C totals across stores", res['totals']['tax'] == 52.0 and res['totals']['revenue'] == 650.0)

# C2. date-range filter (within the period): only day 2 → drops day-1 and day-5.
res2 = R.tax_collected(period=PERIOD, start='2026-07-02', end='2026-07-02', org_id=LUX)
by2 = {s['store']: s for s in res2['stores']}
check("C date-range: only 2026-07-02 rows → Palisade tax = 4", by2['3 Palisade Ave']['tax'] == 4.0)
check("C date-range: Main St keeps only day-2 (16), drops day-5 (24)", by2['9 Main St']['tax'] == 16.0)
check("C date-range totals", res2['totals']['tax'] == 20.0)

# C3. voided / Return excluded.
store['daily_sales_feed'].append({'org_id': LUX, 'period': '2026-07', 'trans_id': 'v1', 'trans_date': '2026-07-02',
                                  'store': '9 Main St', 'ext_price': 999.0, 'tax': 99.0, 'voided': 'YES', 'trans_type': ''})
c = FakeClient(store); R.sb = lambda: c
res3 = R.tax_collected(period=PERIOD, org_id=LUX)
by3 = {s['store']: s for s in res3['stores']}
check("C voided line excluded from tax", by3['9 Main St']['tax'] == 40.0)

# ── summary ─────────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*70}\nRESULT: {PASS} passed, {FAIL} failed\n{'='*70}")
sys.exit(1 if FAIL else 0)
