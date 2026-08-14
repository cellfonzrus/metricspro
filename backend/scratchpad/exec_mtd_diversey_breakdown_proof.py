"""Proof that Diversey's Executive MTD "Total Activation" reconciles to the b2bsoft breakdown of 49.

Owner's b2bsoft MTD breakdown for Diversey (2026-08-13):
    7 new activation + 18 port + 6 byod + 12 tablet + 5 home internet + 1 edge = 49

Before the fix the system showed 30 — it counted the 31 phone activations (new act + port + byod, less a
dedup/void) and DROPPED the non-phone activations (tablet / home internet / edge) because their Contract
Type labels resolve to None in the phone classifier. This drives the REAL `_exec_mtd` over a 49-transaction
feed shaped like that breakdown and asserts the total now equals 49, per category, with nothing left
unrecognized. DISPLAY-ONLY (no payout path touched).

Run:  cd backend && python3 scratchpad/exec_mtd_diversey_breakdown_proof.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R
from datetime import date as _date

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  \033[92mPASS\033[0m {name}")
    else:
        FAIL += 1; print(f"  \033[91mFAIL\033[0m {name}  {extra}")

ORG = "org-lux"
PERIOD = "August 2026"
TODAY = _date(2026, 8, 13)
DAY = "2026-08-05"

# LuxeLink is a config-driven tenant (mig 213 was built for it): its phone-activation labels are MAPPED.
# The non-phone activation labels are NOT mapped -> they reach the auto-count categories.
CT_MAP = {'new activation': 'premium', 'port in': 'premium', 'byod': 'byod'}

# The exact Diversey breakdown, one distinct transaction per unit.
BREAKDOWN = [
    ('New Activation', 7),    # mapped -> premium
    ('Port In', 18),          # mapped -> premium (Port is a premium sub-split; still in Total Activation)
    ('BYOD', 6),              # mapped -> byod
    ('Tablet', 12),           # unmapped -> auto-count premium
    ('Home Internet', 5),     # unmapped -> auto-count premium
    ('Edge', 1),              # unmapped -> auto-count premium (needs 'edge' in _AUTO_ACT_CATEGORY_KEYS)
]

def build_feed():
    feed, n = [], 0
    for ct, qty in BREAKDOWN:
        for _ in range(qty):
            n += 1
            feed.append({'org_id': ORG, 'period': PERIOD, 'trans_id': f'T{n}', 'trans_date': DAY,
                         'store': 'Diversey', 'salesperson': 'REP1', 'user_login': 'rep1',
                         'category': '', 'department': '', 'contract_type': ct, 'product_desc': '',
                         'ext_price': 100.0, 'gp': 20.0, 'voided': '', 'trans_type': ''})
    return feed

FEED = build_feed()
assert len(FEED) == 49, len(FEED)


def run_exec(ct_map):
    acfg = {'departments': set(), 'categories': set(), 'products': set(), 'acima_tenders': set(),
            'box_departments': set(), 'setup_fee_products': set(), 'billpay_products': set(),
            'box_count_buckets': set(), 'box_count_buckets_list': [], 'contract_type_map': ct_map,
            'contract_type_map_raw': ct_map, 'activation_rules': [], 'catalog_classifier': None}
    saved = {}
    def patch(name, fn):
        saved[name] = getattr(R, name); setattr(R, name, fn)
    patch('_exec_metric_config', lambda c, o: None)
    patch('_accessory_config', lambda c, o: acfg)
    patch('_sales_rows_union', lambda c, o, p: ([dict(x) for x in FEED], {'primary': 'daily_sales_feed'}))
    patch('_storeops_roster', lambda c, o, cols='': [])
    patch('_canonical_store_key_fn', lambda c, o: (lambda s: (s or '').strip().lower()))
    class _Res:
        def __init__(self, d): self.data = d
    class _Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return _Res([])
    class _Schema:
        def table(self, *a, **k): return _Q()
    class _Client:
        def schema(self, *a, **k): return _Schema()
    try:
        return R._exec_mtd(_Client(), ORG, PERIOD, today=TODAY)
    finally:
        for n, f in saved.items():
            setattr(R, n, f)


print("Diversey Executive MTD reconciles to the b2bsoft breakdown of 49")
out = run_exec(CT_MAP)
row = next((x for x in out['by_location']['rows'] if x['store'].lower() == 'diversey'), None)
gaps = out.get('classification_gaps') or {}
check("row present", row is not None)
check("Total Activation == 49", row and row['total_activation'] == 49, row and row['total_activation'])
# premium (activation+port) = 7 new + 18 port + 12 tablet + 5 home internet + 1 edge = 43 ; byod = 6
check("premium activations (activation+port) == 43", row and (row['activation'] + row['port']) == 43,
      row and (row['activation'] + row['port']))
check("byod == 6", row and row['byod'] == 6, row and row['byod'])
check("nothing left unrecognized (note None)", gaps.get('note') is None, gaps.get('note'))

print("\nCounter-check: WITHOUT the auto-count (old behaviour) the same feed reads 31, not 49")
# Simulate old behaviour by pointing the auto-count keys at nothing.
_saved_keys = R._AUTO_ACT_CATEGORY_KEYS
try:
    R._AUTO_ACT_CATEGORY_KEYS = ()
    old = run_exec(CT_MAP)
finally:
    R._AUTO_ACT_CATEGORY_KEYS = _saved_keys
orow = next((x for x in old['by_location']['rows'] if x['store'].lower() == 'diversey'), None)
ogaps = old.get('classification_gaps') or {}
ounrec = {u['contract_type']: u['transactions'] for u in ogaps.get('unrecognized_contract_types', [])}
check("old total == 31 (phone activations only)", orow and orow['total_activation'] == 31,
      orow and orow['total_activation'])
check("old behaviour surfaced Tablet ×12", ounrec.get('Tablet') == 12, ounrec)
check("old behaviour surfaced Home Internet ×5", ounrec.get('Home Internet') == 5, ounrec)
check("old behaviour surfaced Edge ×1", ounrec.get('Edge') == 1, ounrec)

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
