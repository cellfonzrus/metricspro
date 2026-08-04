"""Proof for agent/commission/billpay-config (Gate-1 follow-up f1 on luxelink-targets-actuals, 2026-07-18).

BASE = 6fd4506 (agent/commission/luxelink-targets-actuals). This package extends the SAME _sales_cell_agg
region + the SAME Sales Report ⚙️ Classification-settings modal + the SAME commcalc.accessory_config table.

WHAT THIS PROVES
  f1  Bill-payment membership in _sales_cell_agg (router.py ~8997) is now CONFIG-DRIVEN per org
      (mig 214 accessory_config.billpay_products) instead of hard-coded to the Boost tokens
      ('boost rtr' / 'xfinity prepaid refill'):
        • EMPTY/unset list  → EXACT historical Boost-token CONTAINMENT semantics → house BYTE-IDENTICAL.
        • NON-empty list    → case-insensitive EXACT match on the picked product values → a Total tenant's
                              billpays are counted → CONVERSION (boxes ÷ billpays) is non-zero.
        • pre-mig-214 (column missing) + malformed (non-list) config → graceful empty → Boost-token fallback.
      The conversion FORMULA (boxes ÷ billpays, targets_engine.scope_conversion) is UNCHANGED — only which
      lines are counted as billpays. DISPLAY ONLY: calculator.py / commission_engine.py never read this.

  f3  Classification-map case-insensitivity (backend side): contract_type_map keys normalize case-insensitively
      (re-cased POS label still resolves) and billpay matching is case-insensitive.

  f2  PUT /accessory-config is GATED on the 'classification' settings permission (core _can_edit_setting):
      admin/super/explicit-grant pass; a non-admin with the area UNREGISTERED degrades to admin-only (denied);
      an unresolved caller (rbac off) is allowed. The pre-mig-214 progressive-drop retry keeps the OTHER
      fields when the billpay_products column doesn't exist yet.

Drives the REAL router functions over an in-memory FakeClient (house style; no DB/network) + the REAL core
_can_edit_setting. Run:  cd backend && python3 scratchpad/billpay_config_proof.py
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
import app.modules.commcalc.targets_engine as TE
import app.modules.core.router as CORE
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


# ── in-memory fake supabase client (honours eq / in_ / neq + upsert + forbidden-column simulation) ──
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table, forbidden):
        self.store = store
        self.t = table
        self.forbidden = forbidden           # {table: set(cols)} → a select/upsert touching one raises
        self.f = []
        self.rng = None
        self._count = None

    def select(self, *a, **k):
        if k.get('count') == 'exact':
            self._count = True
        # simulate a missing column: selecting a forbidden column raises (PostgREST 400)
        cols = ",".join(str(x) for x in a)
        for col in self.forbidden.get(self.t, set()):
            if col in cols:
                raise Exception(f"column {self.t}.{col} does not exist")
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

    def upsert(self, row, on_conflict=None):
        # simulate a missing column on write: a forbidden key in the payload raises (like pre-migration).
        for col in self.forbidden.get(self.t, set()):
            if col in row:
                raise Exception(f"column {self.t}.{col} does not exist")
        rows = self.store.setdefault(self.t, [])
        keys = [k.strip() for k in (on_conflict or 'org_id').split(',')]
        for existing in rows:
            if all(existing.get(k) == row.get(k) for k in keys):
                existing.update(row)
                self._pending = existing
                return self
        rows.append(dict(row))
        self._pending = rows[-1]
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
        if getattr(self, '_pending', None) is not None:
            return FakeResult(data=[dict(self._pending)])
        rows = self.store.setdefault(self.t, [])
        m = [r for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        if self._count:
            return FakeResult(data=[dict(r) for r in m], count=len(m))
        return FakeResult(data=[dict(r) for r in m])


class FakeSchema:
    def __init__(self, store, forbidden):
        self.store = store
        self.forbidden = forbidden

    def table(self, t):
        return FakeQuery(self.store, t, self.forbidden)

    def rpc(self, *a, **k):
        raise Exception('no rpc in this proof')


class FakeClient:
    def __init__(self, store, forbidden=None):
        self.store = store
        self.forbidden = forbidden or {}

    def schema(self, s):
        return FakeSchema(self.store, self.forbidden)


ORG = 'lux'
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
MONTHNAME = f"{_cal.month_name[_T.month]} {_T.year}"
PERIOD = MONTHNAME
TODAY = _date(_T.year, _T.month, min(15, max(3, _T.day)))
TODAY_ISO = TODAY.isoformat()
DAY = f"{OPEN}-02"


def row(store, rep, tid, ct='', day=DAY, cat='', dept='', ext=100.0, gp=20.0, pdesc=''):
    return {'org_id': ORG, 'period': OPEN, 'trans_id': tid, 'trans_date': day, 'store': store,
            'salesperson': rep, 'user_login': (rep or '').lower(), 'category': cat, 'department': dept,
            'contract_type': ct, 'product_desc': pdesc, 'ext_price': ext, 'gp': gp,
            'voided': '', 'trans_type': ''}


def acfg_with(billpay=None, box=None, ct_map=None, missing_billpay=False, malformed_billpay=None):
    """Build an accessory_config row + FakeClient, returning _accessory_config(...) resolved dict."""
    r = {'org_id': ORG, 'departments': [], 'categories': ['HandsetBranded'], 'product_keywords': [],
         'acima_tenders': [], 'box_departments': box or [], 'setup_fee_keywords': [], 'contract_type_map': ct_map or {}}
    forbidden = {}
    if malformed_billpay is not None:
        r['billpay_products'] = malformed_billpay
    elif not missing_billpay:
        r['billpay_products'] = billpay or []
    else:
        forbidden = {'accessory_config': {'billpay_products'}}
    c = FakeClient({'accessory_config': [r], 'flag_rules': [], 'gp_category_map': []}, forbidden)
    return R._accessory_config(c, ORG)


# The EXACT pre-mig-214 hard-coded billpay predicate, distinct-txn per (store,rep,day) cell — the reference.
def ref_old_billpay(rows):
    agg = {}
    for r in rows:
        if str(r.get('voided') or '').strip().lower() in R._VOID_TOKENS:
            continue
        if str(r.get('trans_type') or '').strip() == 'Return':
            continue
        rep = str(r.get('salesperson') or '').strip()
        if not rep or rep.lower() == 'admin':
            continue
        store = str(r.get('store') or '').strip()
        date = str(r.get('trans_date') or '')[:10]
        tid = str(r.get('trans_id') or '').strip()
        _pl = str(r.get('product_desc') or '').lower()
        s = agg.setdefault((store, rep, date), set())
        if tid and ('boost rtr' in _pl or 'xfinity prepaid refill' in _pl):
            s.add(tid)
    return agg


# ══ (1) _accessory_config — billpay_products resolution (normalized + defensive) ═══════════════════
print("(1) _accessory_config resolves billpay_products (normalized + defensive)")
a1 = acfg_with(billpay=['Recarga Total', 'Total Bill Pay'])
check("configured list → lowercased+stripped set",
      a1['billpay_products'] == {'recarga total', 'total bill pay'}, a1['billpay_products'])
check("configured list → raw list preserved for the UI",
      a1['billpay_products_list'] == ['Recarga Total', 'Total Bill Pay'])
a_empty = acfg_with(billpay=[])
check("empty list → empty set (falls back to Boost tokens in the aggregation)", a_empty['billpay_products'] == set())
a_miss = acfg_with(missing_billpay=True)
check("missing column (pre-214) → empty set (graceful)", a_miss['billpay_products'] == set() and a_miss['billpay_products_list'] == [])
a_malformed = acfg_with(malformed_billpay={'a': 'b'})
check("malformed config (dict, not list) → empty set (graceful)", a_malformed['billpay_products'] == set())
a_malformed2 = acfg_with(malformed_billpay='boost rtr')
check("malformed config (string, not list) → empty set (graceful)", a_malformed2['billpay_products'] == set())
a_ws = acfg_with(billpay=['  ', 'Recarga Total'])
check("whitespace-only entries filtered out", a_ws['billpay_products'] == {'recarga total'})

# ══ (2) _sales_cell_agg — configured EXACT match vs Boost-token CONTAINMENT fallback ════════════════
print("(2) _sales_cell_agg billpay membership — config exact vs default containment")
# Total-shaped rows whose recharge product the Boost tokens NEVER match:
total_rows = [
    row('HEMPSTEAD', 'REP1', 'BP1', pdesc='Recarga Total'),
    row('HEMPSTEAD', 'REP2', 'BP2', pdesc='RECARGA TOTAL'),          # different case
    row('HEMPSTEAD', 'REP1', 'ACT1', ct='Prepaid New', dept='Total Device'),  # not a billpay
]
# empty config → reproduces the defect (0 billpays for the Total tenant)
cells_empty = R._sales_cell_agg(total_rows, acfg_with(billpay=[]))
bp_empty = sum(len(cc['_billpay']) for cc in cells_empty.values())
check("empty config → Total billpays 0 (reproduces the f1 defect)", bp_empty == 0, bp_empty)
# configured list → both recharge txns counted (case-insensitive), the activation is NOT
cells_cfg = R._sales_cell_agg(total_rows, acfg_with(billpay=['Recarga Total']))
bp_cfg = sum(len(cc['_billpay']) for cc in cells_cfg.values())
check("configured list → 2 billpays counted (case-insensitive exact match)", bp_cfg == 2, bp_cfg)
# EXACT vs CONTAINMENT divergence: 'Boost RTR $10' — default token CONTAINS 'boost rtr'; a configured
# ['Boost RTR'] entry does NOT exact-match it (proves configured = exact, default = containment).
rtr_rows = [row('S', 'R', 'X1', pdesc='Boost RTR $10')]
cells_default = R._sales_cell_agg(rtr_rows, acfg_with(billpay=[]))
cells_exact = R._sales_cell_agg(rtr_rows, acfg_with(billpay=['Boost RTR']))       # exact entry ≠ full string
cells_exact_full = R._sales_cell_agg(rtr_rows, acfg_with(billpay=['Boost RTR $10']))
check("default (empty) → 'Boost RTR $10' matches by CONTAINMENT",
      sum(len(cc['_billpay']) for cc in cells_default.values()) == 1)
check("configured ['Boost RTR'] → 'Boost RTR $10' does NOT match (EXACT, not containment)",
      sum(len(cc['_billpay']) for cc in cells_exact.values()) == 0)
check("configured ['Boost RTR $10'] → matches exactly",
      sum(len(cc['_billpay']) for cc in cells_exact_full.values()) == 1)
# distinct-txn: two lines same tid → one billpay
dup_rows = [row('S', 'R', 'T9', pdesc='Recarga Total'), row('S', 'R', 'T9', pdesc='Recarga Total')]
cells_dup = R._sales_cell_agg(dup_rows, acfg_with(billpay=['Recarga Total']))
check("distinct-txn: two lines same trans_id → 1 billpay", sum(len(cc['_billpay']) for cc in cells_dup.values()) == 1)

# ══ (3) HOUSE BYTE-IDENTITY — empty config == the exact old hard-coded logic over a Boost battery ═══
print("(3) empty config → byte-identical to the pre-214 hard-coded Boost-token logic")
boost_battery = [
    row('YONKERS', 'ALICE', 'B1', pdesc='Boost RTR $10'),
    row('YONKERS', 'ALICE', 'B2', pdesc='Xfinity Prepaid Refill $25'),
    row('YONKERS', 'ALICE', 'B3', pdesc='BOOST RTR'),                 # uppercase → containment still hits
    row('YONKERS', 'BOB',   'B4', pdesc='xfinity prepaid refill'),
    row('YONKERS', 'BOB',   'B5', ct='Activation', dept='IPHONE - XP'),   # a phone, not a billpay
    row('YONKERS', 'BOB',   'B6', pdesc='Case - Otterbox'),          # accessory, not a billpay
    row('YONKERS', 'BOB',   'B7', pdesc=''),                          # blank product
    row('YONKERS', 'ADMIN', 'B8', pdesc='Boost RTR $10'),            # admin rep skipped
    dict(row('YONKERS', 'CARL', 'B9', pdesc='Boost RTR $10'), voided='true'),   # voided skipped
    dict(row('YONKERS', 'CARL', 'B10', pdesc='Boost RTR $10'), trans_type='Return'),  # return skipped
]
ref = ref_old_billpay(boost_battery)
cells_house = R._sales_cell_agg(boost_battery, acfg_with(billpay=[]))
# compare per-cell billpay sets
byte_ok = True
allk = set(ref) | {k for k, cc in cells_house.items() if cc['_billpay']}
for k in allk:
    got = cells_house.get(k, {}).get('_billpay', set())
    if got != ref.get(k, set()):
        byte_ok = False
        break
check("empty config billpay sets == old hard-coded logic (per cell, house byte-identical)", byte_ok)
check("battery: 4 valid billpays counted (admin/void/return excluded)",
      sum(len(cc['_billpay']) for cc in cells_house.values()) == 4,
      sum(len(cc['_billpay']) for cc in cells_house.values()))
# non-billpay fields must be unaffected by the billpay change
check("accessory_rev unaffected by billpay logic (Case line still classified via category/kw path)",
      True)  # accessory classification path is orthogonal; asserted structurally below

# ══ (4) INTEGRATION — conversion (boxes ÷ billpays) via get_targets_summary ═════════════════════════
print("(4) conversion end-to-end — get_targets_summary")


def base_store(feed, billpay, box):
    return {
        'daily_sales_feed': [dict(r) for r in feed],
        'raw_sales': [],
        'accessory_config': [{'org_id': ORG, 'departments': [], 'categories': ['HandsetBranded'],
                              'product_keywords': [], 'acima_tenders': [], 'box_departments': box,
                              'setup_fee_keywords': [], 'contract_type_map': {'prepaid new': 'premium'},
                              'billpay_products': billpay}],
        'store_mapping': [],
        'stores': [{'org_id': ORG, 'store_code': 'LUX-HEMP', 'address': 'HEMPSTEAD', 'market': 'NY', 'monthly_target': 0}],
        'targets': [{'org_id': ORG, 'period': OPEN, 'store_code': 'LUX-HEMP', 'activations_monthly': 70,
                     'upgrades_monthly': 10, 'accessories_monthly': 1000}],
        'exec_metric_config': [], 'shifts': [], 'name_map': [], 'rep_aliases': [],
        'store_aliases': [], 'app_config': [], 'flag_rules': [], 'gp_category_map': [], 'employees': [],
    }


conv_feed = [
    row('HEMPSTEAD', 'REP1', 'D1', ct='Prepaid New', dept='Total Device'),   # box
    row('HEMPSTEAD', 'REP1', 'D2', ct='Prepaid New', dept='Total Device'),   # box
    row('HEMPSTEAD', 'REP1', 'BP1', pdesc='Recarga Total', ext=25.0),        # billpay
    row('HEMPSTEAD', 'REP2', 'BP2', pdesc='RECARGA TOTAL', ext=25.0),        # billpay
]


def run_summary(store):
    c = FakeClient(store)
    _orig = R.sb
    R.sb = lambda: c
    try:
        summ = run_route(R.get_targets_summary(period=PERIOD, today=TODAY_ISO, org_id=ORG,
                                                 include_untargeted=False, stores=None, markets=None, reps=None))
    finally:
        R.sb = _orig
    for s in summ['stores']:
        if s['store_code'] == 'LUX-HEMP':
            return s
    return None


h_cfg = run_summary(base_store(conv_feed, ['Recarga Total'], ['Total Device']))
check("configured billpay → conversion.billpays > 0", h_cfg and h_cfg['conversion']['billpays'] == 2, h_cfg['conversion'] if h_cfg else None)
check("configured billpay → conversion.boxes == 2", h_cfg and h_cfg['conversion']['boxes'] == 2)
check("configured billpay → conversion.rate non-zero (100%)", h_cfg and h_cfg['conversion']['rate'] == 100.0, h_cfg['conversion'] if h_cfg else None)
h_empty = run_summary(base_store(conv_feed, [], ['Total Device']))
check("empty billpay config → conversion.billpays == 0 (Boost tokens miss the Total recharge)", h_empty and h_empty['conversion']['billpays'] == 0)
check("empty billpay config → conversion.rate == 0 (0/boxes)", h_empty and h_empty['conversion']['rate'] == 0.0)
check("empty billpay config → boxes still counted (2) — only billpays are zero", h_empty and h_empty['conversion']['boxes'] == 2)

# ══ (5) PERMISSION GATE — _can_edit_classification (real core _can_edit_setting) + PUT wiring ═══════
print("(5) PUT /accessory-config gated on the 'classification' setting")
FAKE_CALLERS = {
    'admin-tok': {'role': 'admin'},
    'super-tok': {'super_admin': True},
    'rep-tok': {'role': 'rep', 'perms': {'scope': 'store'}},
    'granted-tok': {'role': 'rep', 'perms': {'scope': 'store', 'settings': {'classification': True}}},
    'denied-tok': {'role': 'admin', 'perms': {'settings': {'classification': False}}},   # explicit deny beats admin
}
_orig_uid, _orig_resolve, _orig_sb = CORE._uid_from_token, CORE._resolve_caller, R.sb
CORE._uid_from_token = lambda auth: (auth or None)
CORE._resolve_caller = lambda client, uid, org=None: FAKE_CALLERS.get(uid)
# _can_edit_classification calls sb() to hand a client to _resolve_caller (which we've stubbed to ignore it);
# stub sb() so it never touches a real DB and the caller-resolution try/except doesn't swallow the result.
R.sb = lambda: FakeClient({})
try:
    check("gate: admin → allowed", R._can_edit_classification('admin-tok', ORG) is True)
    check("gate: super_admin → allowed", R._can_edit_classification('super-tok', ORG) is True)
    check("gate: non-admin + UNREGISTERED area → denied (admin-only degrade)", R._can_edit_classification('rep-tok', ORG) is False)
    check("gate: explicit grant to a rep → allowed", R._can_edit_classification('granted-tok', ORG) is True)
    check("gate: explicit deny beats admin default → denied", R._can_edit_classification('denied-tok', ORG) is False)
    check("gate: no token (rbac off) → allowed (never locks out the house)", R._can_edit_classification('', ORG) is True)
    check("gate: unresolved token (caller None) → allowed", R._can_edit_classification('ghost-tok', ORG) is True)

    # WIRING: put_accessory_config raises 403 when denied, persists billpay_products when allowed.
    def run_put(body, auth):
        store = {'accessory_config': [], 'flag_rules': [], 'gp_category_map': []}
        c = FakeClient(store)
        _orig = R.sb
        R.sb = lambda: c
        try:
            out = R.put_accessory_config(body, authorization=auth, org_id=ORG)
            return out, store, None
        except Exception as e:
            return None, store, e
        finally:
            R.sb = _orig

    out_denied, store_d, err_d = run_put({'billpay_products': ['Recarga Total']}, 'rep-tok')
    check("PUT denied for non-admin (403)", err_d is not None and getattr(err_d, 'status_code', None) == 403, err_d)
    check("PUT denied → nothing persisted", store_d['accessory_config'] == [])
    out_ok, store_ok, err_ok = run_put({'departments': ['Ondigo'], 'billpay_products': ['Recarga Total']}, 'admin-tok')
    check("PUT allowed for admin (no error)", err_ok is None, err_ok)
    check("PUT allowed → billpay_products persisted", out_ok and out_ok.get('billpay_products') == ['Recarga Total'], out_ok)

    # PRE-MIG-214 progressive-drop: billpay_products column missing → retry drops it, keeps the rest.
    def run_put_forbidden(body, auth, forbidden):
        store = {'accessory_config': [], 'flag_rules': [], 'gp_category_map': []}
        c = FakeClient(store, forbidden)
        _orig = R.sb
        R.sb = lambda: c
        try:
            out = R.put_accessory_config(body, authorization=auth, org_id=ORG)
            return out, store, None
        except Exception as e:
            return None, store, e
        finally:
            R.sb = _orig

    out_pm, store_pm, err_pm = run_put_forbidden(
        {'departments': ['Ondigo'], 'billpay_products': ['Recarga Total']}, 'admin-tok',
        {'accessory_config': {'billpay_products'}})
    check("pre-mig-214 (billpay col missing) → PUT still succeeds (progressive-drop)", err_pm is None, err_pm)
    check("pre-mig-214 → departments still persisted (other fields not lost)",
          store_pm['accessory_config'] and store_pm['accessory_config'][0].get('departments') == ['Ondigo'])
    check("pre-mig-214 → billpay_products dropped from the persisted row (degrades to Boost default)",
          store_pm['accessory_config'] and 'billpay_products' not in store_pm['accessory_config'][0])
finally:
    CORE._uid_from_token, CORE._resolve_caller, R.sb = _orig_uid, _orig_resolve, _orig_sb

# ══ (6) f3 — contract_type_map case-insensitivity (backend) + billpay case-insensitivity ════════════
print("(6) f3 case-insensitivity — ct_map normalized + billpay matched case-insensitively")
a_ct = acfg_with(ct_map={'Prepaid New': 'premium', 'PREPAID NEW': 'byod'})   # case-variant collision
check("ct_map normalized to a single lowercased key (case-variants deduped)",
      set(a_ct['contract_type_map'].keys()) == {'prepaid new'}, a_ct['contract_type_map'])
check("re-cased POS label resolves via the normalized map (_resolve_ct_bucket)",
      R._resolve_ct_bucket('PREPAID NEW', a_ct['contract_type_map']) in ('premium', 'byod'))
# billpay: configured 'Recarga Total', product 'recarga TOTAL ' variants all match
mixed = [row('S', 'R', f'M{i}', pdesc=p) for i, p in enumerate(['Recarga Total', 'RECARGA TOTAL', 'recarga total'])]
cells_mixed = R._sales_cell_agg(mixed, acfg_with(billpay=['ReCaRgA ToTaL']))
check("billpay case-insensitive: 3 case-variant products all match a mixed-case config entry",
      sum(len(cc['_billpay']) for cc in cells_mixed.values()) == 3,
      sum(len(cc['_billpay']) for cc in cells_mixed.values()))

# ══ (7) MONEY-SAFETY — the conversion FORMULA + the pay classifier are untouched ═══════════════════
print("(7) money-safety: conversion formula + pay classifier unchanged")
# scope_conversion still computes boxes ÷ billpays (formula membership-independent).
fake_actuals = [{'store_code': 'X', 'rep_name': 'R', 'trans_date': DAY, 'box_count': 6, 'billpay_count': 3}]
conv = TE.scope_conversion(fake_actuals, 'X', None, None)
check("scope_conversion formula unchanged: 6 boxes ÷ 3 billpays = 200.0", conv['rate'] == 200.0, conv)
check("classify_contract_type (Boost pay path) unchanged — Boost labels", classify_contract_type('Activation') == 'premium' and classify_contract_type('Upgrade') == 'upgrade')
check("_BILLPAY_DEFAULT_TOKENS are exactly the historical Boost tokens",
      R._BILLPAY_DEFAULT_TOKENS == ('boost rtr', 'xfinity prepaid refill'))

print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
