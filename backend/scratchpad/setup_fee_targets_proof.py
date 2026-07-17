"""PACKAGE A proof — device set-up fee counts toward the accessory TARGET (reported SEPARATELY) +
accessory-per-day (A1). Pure / offline (FakeClient, no live DB). Run from backend/:
    python3 scratchpad/setup_fee_targets_proof.py

Proves:
  A2 · config-driven set-up-fee identification (_accessory_config setup_fee_products; default + custom +
      graceful missing column); _is_setup_fee; _sales_cell_agg keeps accessory_rev BYTE-IDENTICAL while
      adding a SEPARATE setup_fee_rev (no double-count); _compute_feed_actuals_py folds setup into acc_gp
      (attainment) yet emits it separately; targets_engine carries setup through scope_actuals_by_day /
      scope_achieved_mtd / compute_scope (setup_fee_mtd).
  A1 · the accessories category exposes today_target + pace + need (accessory $ needed per day).
"""
import os, sys
from datetime import date as _date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R
import app.modules.commcalc.targets_engine as TE

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok  {name}")
    else:
        FAIL += 1; print(f"FAIL  {name}   {extra}")


# ── FakeClient ────────────────────────────────────────────────────────────────────────────────────
class FakeResult:
    def __init__(self, data=None): self.data = data or []


class FakeQuery:
    def __init__(self, store, table, missing_cols=()):
        self.store, self.t, self.f, self.miss = store, table, [], set(missing_cols)
        self.sel = None

    def select(self, *a, **k):
        self.sel = a[0] if a else ""
        # emulate postgREST 400 when selecting a column that doesn't exist on the table
        for col in str(self.sel).replace(" ", "").split(","):
            if col and col in self.miss:
                raise Exception(f'column {self.t}.{col} does not exist')
        return self

    def eq(self, c, v): self.f.append(('eq', c, v)); return self
    def in_(self, c, v): self.f.append(('in', c, list(v))); return self
    def limit(self, n): return self
    def order(self, *a, **k): return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v: return False
            if k == 'in' and rv not in v: return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        return FakeResult([dict(r) for r in rows if self._m(r)])


class FakeSchema:
    def __init__(self, store, missing): self.store, self.missing = store, missing
    def table(self, t): return FakeQuery(self.store, t, self.missing.get(t, ()))
    def rpc(self, *a, **k): raise Exception('no rpc')


class FakeClient:
    def __init__(self, store, missing=None): self.store, self.missing = store, (missing or {})
    def schema(self, s): return FakeSchema(self.store, self.missing)


ORG = 'o1'


def acfg(depts=(), cats=(), prods=(), setup=('device setup charge',)):
    return {'departments': {d.lower() for d in depts}, 'categories': {c.lower() for c in cats},
            'products': {p.lower() for p in prods}, 'setup_fee_products': {s.lower() for s in setup},
            'departments_list': list(depts), 'categories_list': list(cats), 'products_list': list(prods),
            'acima_tenders_list': [], 'setup_fee_keywords_list': list(setup)}


def row(store, rep, tid, ct='Activation', day='2026-06-10', cat='CellPhone', dept='', ext=100.0,
        gp=20.0, pdesc='', voided='', tt='', period='2026-06'):
    return {'org_id': ORG, 'period': period, 'trans_id': tid, 'trans_date': day, 'store': store,
            'salesperson': rep, 'user_login': (rep or '').lower(), 'category': cat, 'department': dept,
            'contract_type': ct, 'product_desc': pdesc, 'ext_price': ext, 'gp': gp,
            'voided': voided, 'trans_type': tt}


print("── A2.1 _is_setup_fee (config-driven) ──")
cf = acfg(depts=['Ondigo'])
check("default keyword matches 'Device Setup Charge'", R._is_setup_fee('Device Setup Charge', cf))
check("case-insensitive contains", R._is_setup_fee('DEVICE setup CHARGE - 1yr', cf))
check("non-setup product is not setup", not R._is_setup_fee('Screen Protector', cf))
check("empty product not setup", not R._is_setup_fee('', cf))
check("empty setup config → never setup", not R._is_setup_fee('Device Setup Charge', acfg(setup=[])))
check("custom keyword honored", R._is_setup_fee('Activation Fee', acfg(setup=['activation fee'])))

print("── A2.2 _sales_cell_agg: accessory_rev BYTE-IDENTICAL, setup separate, no double-count ──")
rows = [
    row('S1', 'Rep A', 't1', dept='Ondigo', ext=40.0, pdesc='Case'),               # accessory
    row('S1', 'Rep A', 't1', dept='Fees', ext=25.0, pdesc='Device Setup Charge'),   # set-up fee (fee dept)
    row('S1', 'Rep A', 't1', ct='Activation', dept='', ext=100.0, pdesc='iPhone'),  # activation line
]
cells_on = R._sales_cell_agg(rows, acfg(depts=['Ondigo'], setup=['device setup charge']))
cells_off = R._sales_cell_agg(rows, acfg(depts=['Ondigo'], setup=[]))  # legacy: no set-up config
a_on = cells_on[('S1', 'Rep A', '2026-06-10')]
a_off = cells_off[('S1', 'Rep A', '2026-06-10')]
check("accessory_rev identical with/without set-up config (byte-identical)", a_on['accessory_rev'] == a_off['accessory_rev'] == 40.0,
      f"on={a_on['accessory_rev']} off={a_off['accessory_rev']}")
check("setup_fee_rev captures the set-up fee when configured", a_on['setup_fee_rev'] == 25.0, a_on['setup_fee_rev'])
check("setup_fee_rev is 0 when set-up config empty", a_off['setup_fee_rev'] == 0.0)
check("revenue/gp totals unchanged (not touched)", a_on['revenue'] == a_off['revenue'] == 165.0)

# Double-count guard: a line that is BOTH an accessory dept AND a set-up-fee keyword goes to setup ONLY.
rows2 = [row('S1', 'Rep A', 't2', dept='Ondigo', ext=30.0, pdesc='Device Setup Charge')]
c2 = R._sales_cell_agg(rows2, acfg(depts=['Ondigo'], setup=['device setup charge']))[('S1', 'Rep A', '2026-06-10')]
check("overlap line counted ONCE (setup only, not accessory)", c2['accessory_rev'] == 0.0 and c2['setup_fee_rev'] == 30.0,
      f"acc={c2['accessory_rev']} setup={c2['setup_fee_rev']}")

print("── A2.3 _accessory_config resolver (default / custom / graceful missing column) ──")
# (a) explicit per-org set-up keyword
cl_a = FakeClient({'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'], 'categories': [],
                                         'product_keywords': [], 'acima_tenders': [],
                                         'setup_fee_keywords': ['My Setup Fee']}]})
rc_a = R._accessory_config(cl_a, ORG)
check("explicit setup_fee_keywords resolved", rc_a['setup_fee_products'] == {'my setup fee'}, rc_a['setup_fee_products'])
# (b) row present but no setup keyword → code default
cl_b = FakeClient({'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'], 'categories': [],
                                         'product_keywords': [], 'acima_tenders': [], 'setup_fee_keywords': []}]})
rc_b = R._accessory_config(cl_b, ORG)
check("empty setup list → default 'device setup charge'", rc_b['setup_fee_products'] == {'device setup charge'}, rc_b['setup_fee_products'])
# (c) pre-217: setup_fee_keywords column MISSING → default, and accessory resolution still works
cl_c = FakeClient({'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'], 'categories': [],
                                         'product_keywords': [], 'acima_tenders': []}]},
                  missing={'accessory_config': ('setup_fee_keywords',)})
rc_c = R._accessory_config(cl_c, ORG)
check("missing column degrades to default (page not broken)", rc_c['setup_fee_products'] == {'device setup charge'})
check("accessory departments still resolved with column missing", 'ondigo' in rc_c['departments'], rc_c['departments'])

print("── A2.4 _compute_feed_actuals_py: acc_gp folds setup, emits setup_fee separately ──")
cl = FakeClient({'accessory_config': [{'org_id': ORG, 'departments': ['Ondigo'], 'categories': [],
                                       'product_keywords': [], 'acima_tenders': [],
                                       'setup_fee_keywords': ['Device Setup Charge']}],
                 'store_mapping': []})
feed_rows = [
    row('S1', 'Rep A', 't1', dept='Ondigo', ext=40.0, pdesc='Case'),
    row('S1', 'Rep A', 't1', dept='Fees', ext=25.0, pdesc='Device Setup Charge'),
]
out = R._compute_feed_actuals_py(cl, ORG, '2026-06', rows=feed_rows)
o = out[0]
check("acc_gp = accessory_rev + setup_fee (attainment counts set-up)", o['acc_gp'] == 65.0, o['acc_gp'])
check("setup_fee reported separately in actuals", o['setup_fee'] == 25.0, o['setup_fee'])

print("── A2.5 targets_engine threads setup + A1 accessory-per-day ──")
actuals = [
    {'store_code': 'B1', 'rep_name': 'REP A', 'trans_date': '2026-06-05', 'prem_count': 2, 'byod_count': 0,
     'upg_count': 1, 'acc_gp': 65.0, 'setup_fee': 25.0, 'box_count': 2, 'billpay_count': 0},
    {'store_code': 'B1', 'rep_name': 'REP A', 'trans_date': '2026-06-06', 'prem_count': 1, 'byod_count': 0,
     'upg_count': 0, 'acc_gp': 10.0, 'setup_fee': 0.0, 'box_count': 1, 'billpay_count': 0},
]
by_day = TE.scope_actuals_by_day(actuals, 'B1', None)
d5 = by_day[_date(2026, 6, 5)]
check("scope_actuals_by_day carries 'setup'", d5['setup'] == 25.0, d5)
check("scope_actuals_by_day 'acc' includes setup (folded in acc_gp)", d5['acc'] == 65.0, d5['acc'])
ach = TE.scope_achieved_mtd(actuals, 'B1', None, _date(2026, 6, 30))
check("scope_achieved_mtd accessories includes setup (65+10)", ach['accessories'] == 75.0, ach['accessories'])
check("scope_achieved_mtd reports accessory_setup_fee separately", ach['accessory_setup_fee'] == 25.0, ach['accessory_setup_fee'])

# compute_scope: accessory attainment + set-up-fee MTD + A1 per-day fields
hours = {_date(2026, 6, 5): 8.0, _date(2026, 6, 6): 8.0, _date(2026, 6, 20): 8.0}
abd = TE.scope_actuals_by_day(actuals, 'B1', None)
res = TE.compute_scope({'activations': 100, 'upgrades': 10, 'byod': 0, 'accessories': 1000.0},
                       hours, abd, _date(2026, 6, 6), round_counts=True, month_end=_date(2026, 6, 30))
accm = res['categories']['accessories']
check("A1: accessories exposes today_target (per-day $ needed)", 'today_target' in accm and accm['today_target'] is not None)
check("A1: accessories exposes pace (per open-day $ needed)", 'pace' in accm and accm['pace'] is not None)
check("A1: accessories exposes need", 'need' in accm)
check("accessory achieved_mtd includes set-up fee (75)", accm['achieved_mtd'] == 75.0, accm['achieved_mtd'])
check("setup_fee_mtd exposed separately on accessories cat", accm.get('setup_fee_mtd') == 25.0, accm.get('setup_fee_mtd'))
check("accessory need = target - achieved(incl setup) = 925", accm['need'] == 925.0, accm['need'])

print(f"\n{'='*54}\n  {PASS} passed · {FAIL} failed\n{'='*54}")
sys.exit(1 if FAIL else 0)
