"""PACKAGE B proof — visibility fixes. Pure / offline (FakeClient + monkeypatched auth helpers).
Run from backend/:  python3 scratchpad/visibility_fixes_proof.py

Proves:
  B1 · _caller_self_keyset — a self rep resolves to their OWN store keyset (not the empty set that hid
       every store in the targets summary); non-self → (False,None); self w/ no pinned store → (True,None)
       (unrestricted fallback, never locked out).
  B3 · box departments are CONFIG-DRIVEN in _sales_cell_agg (default = _BOX_DEPTS → house byte-identical;
       a tenant's own dept labels count) and _accessory_config resolves them (default / custom / missing col).
  B4 · the productivity/review endpoint DEGRADES (empty payload) instead of 500 when _prod_gather raises.
  B5 · _require_perf_review_edit gates config on the 'performance_review' setting (admin ok · non-admin 403 ·
       explicit grant ok · rbac-off no-block).
  B6 · accessory-flags scope_keyset filtering + universal org-scoped filter OPTIONS (store_mapping), narrowed
       to the caller's span.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R
import app.modules.storeops.router as SO
import app.modules.core.router as CORE
from fastapi import HTTPException

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok  {name}")
    else:
        FAIL += 1; print(f"FAIL  {name}   {extra}")


class FakeResult:
    def __init__(self, data=None): self.data = data or []


class FakeQuery:
    def __init__(self, store, table, missing=()):
        self.store, self.t, self.f, self.miss = store, table, [], set(missing)

    def select(self, *a, **k):
        for col in str(a[0] if a else "").replace(" ", "").split(","):
            if col and col in self.miss:
                raise Exception(f'column {self.t}.{col} does not exist')
        return self

    def eq(self, c, v): self.f.append(('eq', c, v)); return self
    def in_(self, c, v): self.f.append(('in', c, list(v))); return self
    def gte(self, c, v): return self
    def lte(self, c, v): return self
    def limit(self, n): return self
    def order(self, *a, **k): return self

    def upsert(self, row, on_conflict=None):
        rows = self.store.setdefault(self.t, [])
        recs = row if isinstance(row, list) else [row]
        key = on_conflict or 'org_id'
        for rec in recs:
            for col in rec:  # emulate a missing-column 500
                if col in self.miss:
                    raise Exception(f'column {self.t}.{col} does not exist')
            existing = next((r for r in rows if all(r.get(k) == rec.get(k) for k in key.split(','))), None)
            if existing:
                existing.update(rec)
            else:
                rows.append(dict(rec))
        self._up = True
        return self

    def _m(self, r):
        for k, c, v in self.f:
            if k == 'eq' and r.get(c) != v: return False
            if k == 'in' and r.get(c) not in v: return False
        return True

    def execute(self):
        if getattr(self, '_up', False):
            return FakeResult([])
        return FakeResult([dict(r) for r in self.store.get(self.t, []) if self._m(r)])


class FakeSchema:
    def __init__(self, store, missing): self.store, self.missing = store, missing
    def table(self, t): return FakeQuery(self.store, t, self.missing.get(t, ()))


class FakeClient:
    def __init__(self, store, missing=None): self.store, self.missing = store, (missing or {})
    def schema(self, s): return FakeSchema(self.store, self.missing)
    def table(self, t): return FakeQuery(self.store, t, self.missing.get(t, ()))   # public schema


HOUSE = '00000000-0000-0000-0000-000000000001'
ORG = 'o1'


def acfg(depts=('Ondigo',), box=None):
    d = {'departments': {x.lower() for x in depts}, 'categories': set(), 'products': set(),
         'departments_list': list(depts), 'categories_list': [], 'products_list': [], 'acima_tenders_list': [],
         'box_departments': set(box) if box is not None else set(R._BOX_DEPTS),
         'box_departments_list': list(box) if box is not None else list(R._BOX_DEPTS)}
    return d


def row(store, rep, tid, dept='', ext=100.0, gp=20.0, pdesc='', cat='CellPhone', day='2026-06-10'):
    return {'org_id': ORG, 'period': '2026-06', 'trans_id': tid, 'trans_date': day, 'store': store,
            'salesperson': rep, 'user_login': (rep or '').lower(), 'category': cat, 'department': dept,
            'contract_type': 'Activation', 'product_desc': pdesc, 'ext_price': ext, 'gp': gp,
            'voided': '', 'trans_type': ''}


# ── B1 ──────────────────────────────────────────────────────────────────────────────────────────
print("── B1 _caller_self_keyset ──")
_orig_sb = R.sb
SO._rbac_enabled = lambda org: True
SO._role_scope = lambda org, role: {'rep': 'self', 'dm': 'market', 'admin': 'all'}.get((role or '').lower(), 'all')
CORE._uid_from_token = lambda auth: (auth.replace('uid:', '') if auth and auth.startswith('uid:') else None)

store_b1 = {
    'app_users': [
        {'org_id': ORG, 'auth_id': 'rep1', 'role': 'rep', 'store_code': 'S1', 'store_codes': []},
        {'org_id': ORG, 'auth_id': 'rep2', 'role': 'rep', 'store_code': '', 'store_codes': []},
        {'org_id': ORG, 'auth_id': 'dm1', 'role': 'dm', 'store_code': 'M9', 'store_codes': ['M9', 'M8']},
    ],
    'stores': [{'org_id': ORG, 'store_code': 'S1', 'address': '1 Main St'}],
}
R.sb = lambda: FakeClient(store_b1)
is_self, ks = R._caller_self_keyset('uid:rep1', ORG)
check("self rep → is_self True", is_self)
check("self rep keyset = own store_code + address", ks == {'S1', '1 MAIN ST'}, ks)
is_self2, ks2 = R._caller_self_keyset('uid:rep2', ORG)
check("self rep, no pinned store → (True, None) unrestricted fallback", is_self2 and ks2 is None, (is_self2, ks2))
is_self3, ks3 = R._caller_self_keyset('uid:dm1', ORG)
check("market DM → (False, None) (handled by scope_keyset, not substituted)", (is_self3, ks3) == (False, None), (is_self3, ks3))
is_self4, ks4 = R._caller_self_keyset('', ORG)
check("no token → (False, None)", (is_self4, ks4) == (False, None))
SO._rbac_enabled = lambda org: False
check("rbac off → (False, None)", R._caller_self_keyset('uid:rep1', ORG) == (False, None))
SO._rbac_enabled = lambda org: True
R.sb = _orig_sb

# ── B3 ──────────────────────────────────────────────────────────────────────────────────────────
print("── B3 box departments config-driven ──")
rows = [row('S1', 'Rep A', 't1', dept='Android - XP'), row('S1', 'Rep A', 't1', dept='WIDGET-BOX'),
        row('S1', 'Rep A', 't1', dept='Ondigo', ext=40.0, pdesc='Case')]
c_default = R._sales_cell_agg(rows, acfg())[('S1', 'Rep A', '2026-06-10')]
check("default box set = _BOX_DEPTS → 'Android - XP' counts, 'WIDGET-BOX' does not (house byte-identical)",
      c_default['box_count'] == 1, c_default['box_count'])
c_custom = R._sales_cell_agg(rows, acfg(box={'WIDGET-BOX', 'Android - XP'}))[('S1', 'Rep A', '2026-06-10')]
check("tenant box config counts its own dept ('WIDGET-BOX' + 'Android - XP')", c_custom['box_count'] == 2, c_custom['box_count'])
check("accessory_rev unaffected by box config", c_default['accessory_rev'] == c_custom['accessory_rev'] == 40.0)

store_b3 = {'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'], 'categories': [],
                                  'product_keywords': [], 'acima_tenders': [], 'box_departments': ['Cell-Box']}],
            'gp_category_map': []}
rc = R._accessory_config(FakeClient(store_b3), ORG)
check("_accessory_config resolves configured box_departments", rc['box_departments'] == {'Cell-Box'}, rc['box_departments'])
rc_def = R._accessory_config(FakeClient({'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'],
                                         'categories': [], 'product_keywords': [], 'acima_tenders': [],
                                         'box_departments': []}], 'gp_category_map': []}), ORG)
check("empty box config → default _BOX_DEPTS", rc_def['box_departments'] == set(R._BOX_DEPTS), rc_def['box_departments'])
rc_miss = R._accessory_config(FakeClient({'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'],
                                          'categories': [], 'product_keywords': [], 'acima_tenders': []}],
                                          'gp_category_map': []},
                                         missing={'accessory_config': ('box_departments',)}), ORG)
check("missing box_departments column (pre-218) degrades to _BOX_DEPTS", rc_miss['box_departments'] == set(R._BOX_DEPTS))
check("accessory departments still resolve with box column missing", 'ondigo' in rc_miss['departments'])

# ── B4 ──────────────────────────────────────────────────────────────────────────────────────────
print("── B4 productivity/review degrades instead of 500 ──")
_orig_gather = R._prod_gather
_orig_req = R.require_org
R.require_org = lambda org: None
def _boom(*a, **k): raise Exception('simulated cross-module failure')
R._prod_gather = _boom
res = R.get_productivity_review('2026-06', org_id=ORG)
check("review endpoint returns a dict (no 500)", isinstance(res, dict))
check("review degrade → empty rows", res.get('rows') == [] and res.get('items') == [], res)
check("review degrade includes an error hint", 'error' in res)
res_r = R.get_productivity_rankings('2026-06', org_id=ORG)
check("rankings endpoint also degrades gracefully", isinstance(res_r, dict) and res_r.get('rows') == [])
res_p = R.get_productivity('2026-06', org_id=ORG)
check("productivity endpoint also degrades gracefully", isinstance(res_p, dict) and res_p.get('stores') == [])
R._prod_gather = _orig_gather
R.require_org = _orig_req

# ── B5 ──────────────────────────────────────────────────────────────────────────────────────────
print("── B5 performance-review config permission gate ──")
CORE._uid_from_token = lambda a: (a or None)
R.sb = lambda: FakeClient({})   # _require_perf_review_edit evaluates sb() before _resolve_caller

def _gate(caller_dict):
    CORE._resolve_caller = lambda client, uid, org=None: caller_dict
    R._require_perf_review_edit('some-uid', ORG)  # raises HTTPException(403) if denied

def _denied(caller_dict):
    try:
        _gate(caller_dict); return False
    except HTTPException as e:
        return e.status_code == 403

check("admin (scope=all) may edit", not _denied({'super_admin': False, 'role': 'admin', 'perms': {'scope': 'all'}}))
check("super_admin may edit", not _denied({'super_admin': True, 'role': 'x', 'perms': {}}))
check("non-admin rep is DENIED (403)", _denied({'super_admin': False, 'role': 'rep', 'perms': {'scope': 'self'}}))
check("explicit settings grant allows a manager", not _denied({'super_admin': False, 'role': 'dm',
      'perms': {'scope': 'market', 'settings': {'performance_review': True}}}))
check("explicit settings DENY overrides admin", _denied({'super_admin': False, 'role': 'admin',
      'perms': {'scope': 'all', 'settings': {'performance_review': False}}}))
CORE._resolve_caller = lambda client, uid, org=None: None
check("unidentifiable caller (rbac off) → no active block", R._require_perf_review_edit('x', ORG) is None)
R.sb = _orig_sb

# ── B6 ──────────────────────────────────────────────────────────────────────────────────────────
print("── B6 accessory-flags scope + universal filter options ──")
store_b6 = {
    'flag_rules': [{'org_id': ORG, 'id': 1, 'accessory_threshold': 30.0, 'accessory_min_threshold': 0.0,
                    'accessory_chargeback_amount': 5.0}],
    'raw_sales': [
        row('1 Main St', 'Rep A', 't1', dept='Ondigo', ext=99.0, pdesc='Gold Case'),   # over 30 → flagged
        row('9 Far Rd', 'Rep B', 't2', dept='Ondigo', ext=99.0, pdesc='Gold Case'),     # over 30, other mkt
    ],
    'item_mapping': [], 'chargeback_review': [],
    'store_mapping': [
        {'org_id': ORG, 'store_code': 'S1', 'store_address': '1 Main St', 'market': 'North'},
        {'org_id': ORG, 'store_code': 'S9', 'store_address': '9 Far Rd', 'market': 'South'},
    ],
}
R.sb = lambda: FakeClient(store_b6)
# admin (scope_keyset None): sees both, filters carry both markets/stores
SO.scope_keyset = lambda auth, org=ORG: None
res_admin = R.accessory_flags(org_id=ORG)
check("admin sees flags from both stores", len(res_admin['rows']) == 2, len(res_admin['rows']))
check("universal filters expose both markets (store_mapping source)", set(res_admin['filters']['markets']) == {'North', 'South'}, res_admin['filters']['markets'])
check("universal filters expose both stores", {s['value'] for s in res_admin['filters']['stores']} >= {'1 Main St', '9 Far Rd'})
# market DM scoped to North (store S1 / '1 Main St')
SO.scope_keyset = lambda auth, org=ORG: {'S1', '1 MAIN ST'}
res_dm = R.accessory_flags(org_id=ORG)
check("DM span sees ONLY their store's flags", len(res_dm['rows']) == 1 and res_dm['rows'][0]['store'] == '1 Main St', [r['store'] for r in res_dm['rows']])
check("DM filter options narrowed to their span (North only)", res_dm['filters']['markets'] == ['North'], res_dm['filters']['markets'])
check("DM store options narrowed to their store", {s['value'] for s in res_dm['filters']['stores']} == {'1 Main St'}, res_dm['filters']['stores'])
R.sb = _orig_sb

# ── REWORK 1 — employee sees ONLY their own KPI/commission row ────────────────────────────────────
print("── REWORK 1 (B2) get_commissions self-scope = own row only ──")
import asyncio
SO._rbac_enabled = lambda org: True
CORE._uid_from_token = lambda a: (a.replace('uid:', '') if a and a.startswith('uid:') else None)
store_r1 = {
    'app_users': [
        {'org_id': ORG, 'auth_id': 'rep1', 'role': 'rep', 'employee_id': 'E1'},
        {'org_id': ORG, 'auth_id': 'repX', 'role': 'rep', 'employee_id': 'E9'},  # maps to no rep row
        {'org_id': ORG, 'auth_id': 'boss', 'role': 'admin', 'employee_id': 'E0'},
    ],
    'employees': [{'org_id': ORG, 'employee_id': 'E1', 'name': 'Alice Smith'},
                  {'org_id': ORG, 'employee_id': 'E9', 'name': 'Ghost Rep'}],
    'name_map': [{'org_id': ORG, 'epay_salesperson': 'ASMITH', 'storeops_name': 'Alice Smith'}],
    'rep_aliases': [],
    'rep_commissions': [
        {'org_id': ORG, 'period': '2026-06', 'storeops_name': 'Alice Smith', 'epay_salesperson': 'ASMITH',
         'salesperson': 'Alice Smith', 'store': 'S1', 'total_payout': 500.0, 'kpi_values': {'atu': 60}},
        {'org_id': ORG, 'period': '2026-06', 'storeops_name': 'Bob Jones', 'epay_salesperson': 'BJONES',
         'salesperson': 'Bob Jones', 'store': 'S1', 'total_payout': 700.0, 'kpi_values': {'atu': 40}},
    ],
    'chargeback_items': [],
}
R.sb = lambda: FakeClient(store_r1)
SO._role_scope = lambda org, role: {'rep': 'self', 'admin': 'all', 'dm': 'market'}.get((role or '').lower(), 'all')

res_self = asyncio.run(R.get_commissions('2026-06', authorization='uid:rep1', org_id=ORG))
check("self rep gets exactly ONE row", len(res_self) == 1, len(res_self))
check("self rep gets ONLY their own row (Alice, via storeops_name)", res_self and res_self[0]['storeops_name'] == 'Alice Smith', res_self)
check("self rep NEVER sees a coworker's pay (no Bob)", all(r['storeops_name'] != 'Bob Jones' for r in res_self))

# alias path: a rep row keyed only by an epay alias that name_map canonicalizes to the employee's name
store_r1['rep_commissions'] = [
    {'org_id': ORG, 'period': '2026-06', 'storeops_name': '', 'epay_salesperson': 'ASMITH',
     'salesperson': '', 'store': 'S1', 'total_payout': 500.0},
    {'org_id': ORG, 'period': '2026-06', 'storeops_name': 'Bob Jones', 'epay_salesperson': 'BJONES',
     'salesperson': 'Bob Jones', 'store': 'S1', 'total_payout': 700.0},
]
res_alias = asyncio.run(R.get_commissions('2026-06', authorization='uid:rep1', org_id=ORG))
check("self rep matched via ALIAS (epay ASMITH → Alice) → own row", len(res_alias) == 1 and res_alias[0]['epay_salesperson'] == 'ASMITH', res_alias)

# self rep that maps to NO rep row → empty, never other people's data
res_none = asyncio.run(R.get_commissions('2026-06', authorization='uid:repX', org_id=ORG))
check("self rep with no rep mapping → EMPTY (not coworkers' rows)", res_none == [], res_none)

# admin unchanged (sees all; scope_keyset governs)
SO.scope_keyset = lambda auth, org=ORG: None
res_admin = asyncio.run(R.get_commissions('2026-06', authorization='uid:boss', org_id=ORG))
check("admin sees ALL rows (unchanged)", len(res_admin) == 2, len(res_admin))

# market DM unchanged: _caller_rep_keys returns None (not self) → scope_keyset store filter governs
store_r1['app_users'].append({'org_id': ORG, 'auth_id': 'dm1', 'role': 'dm', 'employee_id': 'E5'})
store_r1['rep_commissions'] = [
    {'org_id': ORG, 'period': '2026-06', 'storeops_name': 'Alice Smith', 'epay_salesperson': 'ASMITH',
     'salesperson': 'Alice Smith', 'store': 'S1', 'total_payout': 500.0},
    {'org_id': ORG, 'period': '2026-06', 'storeops_name': 'Carol Far', 'epay_salesperson': 'CFAR',
     'salesperson': 'Carol Far', 'store': 'S9', 'total_payout': 700.0},
]
SO.scope_keyset = lambda auth, org=ORG: {'S1'}
res_dm = asyncio.run(R.get_commissions('2026-06', authorization='uid:dm1', org_id=ORG))
check("market DM = store-scoped commission rows (S1 only, whole store — unchanged)",
      {r['store'] for r in res_dm} == {'S1'}, [r['store'] for r in res_dm])
R.sb = _orig_sb

# ── REWORK 2 — box_departments + setup_fee_keywords are ADMIN-EDITABLE config (not SQL-only) ───────
print("── REWORK 2 (B3) accessory-config UI surfaces + persists box_departments + setup_fee_keywords ──")
_orig_req = R.require_org
R.require_org = lambda org: None
store_r2 = {'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'], 'categories': [],
                                  'product_keywords': [], 'acima_tenders': [],
                                  'box_departments': ['Android - XP', 'IPHONE - XP', 'TABLET - XP'],
                                  'setup_fee_keywords': ['Device Setup Charge']}],
            'gp_category_map': [], 'daily_sales_feed': [], 'raw_sales': [
                {'org_id': ORG, 'period': '2026-06', 'department': 'Total-Android', 'category': '',
                 'product_desc': '', 'contract_type': 'Activation', 'tender_type': '', 'trans_type': ''}]}
R.sb = lambda: FakeClient(store_r2)
gac = R.get_accessory_config(ORG)
check("get_accessory_config returns box_departments", gac['box_departments'] == ['Android - XP', 'IPHONE - XP', 'TABLET - XP'])
check("get_accessory_config returns setup_fee_keywords", gac['setup_fee_keywords'] == ['Device Setup Charge'])
# owner adds the Total device department via the UI (pick-don't-type: it's a real raw_sales dept)
R.put_accessory_config({'box_departments': ['Android - XP', 'IPHONE - XP', 'TABLET - XP', 'Total-Android'],
                        'setup_fee_keywords': ['Device Setup Charge', 'Set Up Fee']}, ORG)
gac2 = R.get_accessory_config(ORG)
check("put persists the ADDED Total box department", 'Total-Android' in gac2['box_departments'], gac2['box_departments'])
check("put persists an ADDED set-up-fee keyword", 'Set Up Fee' in gac2['setup_fee_keywords'], gac2['setup_fee_keywords'])
sf = R.sales_fields(period='2026-06', org_id=ORG)
check("sales-fields offers the DISTINCT raw_sales department as a pick option (RULE THREE)", 'Total-Android' in sf['departments'], sf['departments'])
check("sales-fields returns current box_departments (for the UI)", 'Total-Android' in sf['box_departments'])
check("sales-fields returns current setup_fee_keywords (for the UI)", 'Set Up Fee' in sf['setup_fee_keywords'])
# graceful: a pre-218/217 tenant (columns missing) still saves the legacy lists
store_r2b = {'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'], 'categories': [],
                                   'product_keywords': [], 'acima_tenders': []}], 'gp_category_map': [],
             'daily_sales_feed': [], 'raw_sales': []}
R.sb = lambda: FakeClient(store_r2b, missing={'accessory_config': ('box_departments', 'setup_fee_keywords')})
try:
    R.put_accessory_config({'departments': ['Ondigo', 'Wireless'], 'box_departments': ['X']}, ORG)
    check("put degrades gracefully when box/setup columns missing (no 500)", True)
except Exception as e:
    check("put degrades gracefully when box/setup columns missing (no 500)", False, str(e))
R.require_org = _orig_req
R.sb = _orig_sb

print(f"\n{'='*54}\n  {PASS} passed · {FAIL} failed\n{'='*54}")
sys.exit(1 if FAIL else 0)
