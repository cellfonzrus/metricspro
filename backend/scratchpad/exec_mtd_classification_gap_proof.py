"""Proof for agent/commission/luxelink-number-discrepancies (owner report 2026-08-13).

SYMPTOM: luxelink's Executive MTD / trend "Total Activation" per store reads LOWER than the b2bsoft
Month-To-Date report (owner: Diversey shows 30 here vs 49 in b2bsoft). Total Activation counts only
DISTINCT transactions whose Contract Type resolves to an activation bucket (premium/byod/upgrade) via the
shared classifier + the tenant's contract_type_map. A Contract Type the map doesn't cover — a Total-carrier
tenant's Home Internet / FiOS / Tablet activation labels — resolves to None and is SILENTLY EXCLUDED, so the
total reads low with no explanation.

FIX (DISPLAY-ONLY, no payout change): `_exec_mtd` now returns `classification_gaps` (the SAME
`_classification_gaps` the Sales Report already surfaces) so the Exec MTD/trend surface names WHICH Contract
Type labels are uncounted and by how many transactions, with a path to map them. This proves:
  (1) the uncounted labels are surfaced with correct transaction counts,
  (2) once mapped, those transactions COUNT and Total Activation reconciles upward,
  (3) a fully-mapped tenant / the house org → note None → the banner stays hidden (byte-identical).

Drives the REAL router `_exec_mtd` over monkeypatched helpers (house style; no DB/network).
Run:  cd backend && python3 scratchpad/exec_mtd_classification_gap_proof.py
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

# luxelink b2bsoft-shaped feed for ONE store (Diversey). Phone activations carry labels the map covers;
# Home Internet / FiOS / Tablet carry labels the map does NOT cover (the whole defect).
def r(tid, ct, pdesc="", dept="", cat="", ext=100.0, gp=20.0):
    return {'org_id': ORG, 'period': PERIOD, 'trans_id': tid, 'trans_date': DAY, 'store': 'Diversey',
            'salesperson': 'REP1', 'user_login': 'rep1', 'category': cat, 'department': dept,
            'contract_type': ct, 'product_desc': pdesc, 'ext_price': ext, 'gp': gp,
            'voided': '', 'trans_type': ''}

FEED = [
    r('P1', 'Prepaid New'), r('P2', 'Prepaid New'), r('P3', 'Bring Device'),   # 2 premium + 1 byod (mapped)
    r('HI1', 'Home Internet', pdesc='Wireless Home Internet Router'),          # unmapped -> dropped
    r('HI2', 'Home Internet', pdesc='Wireless Home Internet Router'),          # unmapped -> dropped
    r('FIOS1', 'FiOS', pdesc='FiOS Internet 300M'),                            # unmapped -> dropped
    r('TAB1', 'Tablet', pdesc='Galaxy Tab A9', dept='TABLET - XP'),            # unmapped -> dropped
]

# CT map covering only the phone-activation labels. It is NON-EMPTY, so the tenant is "config-driven" and
# the non-phone activation auto-count (_AUTO_ACT_CATEGORY_KEYS) applies to Home Internet / FiOS / Tablet.
CT_MAP_PARTIAL = {'prepaid new': 'premium', 'bring device': 'byod'}
# Same, but with Tablet explicitly force-excluded ('none' wins over the auto-count) — the escape hatch.
CT_MAP_EXCLUDE_TABLET = dict(CT_MAP_PARTIAL, **{'tablet': 'none'})


def run_exec(ct_map):
    """Drive the REAL _exec_mtd with helpers monkeypatched to feed our rows + ct_map (no DB)."""
    # Fully-normalized acfg exactly as _accessory_config emits it (empty tenant + our ct_map). The set-typed
    # keys are what _is_accessory / _is_setup_fee / _sales_cell_agg read.
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
        out = R._exec_mtd(_Client(), ORG, PERIOD, today=TODAY)
    finally:
        for n, f in saved.items():
            setattr(R, n, f)
    return out


print("(1) config-driven tenant — Home Internet / FiOS / Tablet AUTO-COUNT; total reconciles (3 -> 7)")
low = run_exec(CT_MAP_PARTIAL)
gaps = low.get('classification_gaps') or {}
unrec = {u['contract_type']: u['transactions'] for u in gaps.get('unrecognized_contract_types', [])}
diversey = next((x for x in low['by_location']['rows'] if x['store'].lower() == 'diversey'), None)
check("classification_gaps present on _exec_mtd", 'classification_gaps' in low)
check("Home Internet no longer flagged (auto-counted)", 'Home Internet' not in unrec, unrec)
check("FiOS no longer flagged (auto-counted)", 'FiOS' not in unrec, unrec)
check("Tablet no longer flagged (auto-counted)", 'Tablet' not in unrec, unrec)
check("note clears to None (nothing left unrecognized)", gaps.get('note') is None, gaps.get('note'))
check("Total Activation = 7 (3 phone + 2 home-internet + 1 fios + 1 tablet)",
      diversey and diversey['total_activation'] == 7, diversey and diversey['total_activation'])

print("(2) explicit 'none' wins over auto-count — Tablet force-excluded (7 -> 6), stays uncounted")
excl = run_exec(CT_MAP_EXCLUDE_TABLET)
gaps2 = excl.get('classification_gaps') or {}
unrec2 = {u['contract_type']: u['transactions'] for u in gaps2.get('unrecognized_contract_types', [])}
diversey2 = next((x for x in excl['by_location']['rows'] if x['store'].lower() == 'diversey'), None)
check("Tablet excluded by explicit 'none' (not counted, not flagged)", 'Tablet' not in unrec2, unrec2)
check("Total Activation = 6 (tablet dropped on purpose)",
      diversey2 and diversey2['total_activation'] == 6, diversey2 and diversey2['total_activation'])

print("(3) a genuinely unknown label still surfaces in the banner (auto-count is keyword-shaped, not blanket)")
saved_feed = FEED[:]
try:
    globals()['FEED'] = FEED + [r('MISC1', 'Loyalty Reward')]  # not a phone label, not an auto-category
    misc = run_exec(CT_MAP_PARTIAL)
finally:
    globals()['FEED'] = saved_feed
mg = misc.get('classification_gaps') or {}
munrec = {u['contract_type']: u['transactions'] for u in mg.get('unrecognized_contract_types', [])}
check("unknown 'Loyalty Reward' still flagged (×1)", munrec.get('Loyalty Reward') == 1, munrec)

print("(4) house/Boost org — EMPTY map -> auto-count skipped -> byte-identical, pay classifier untouched")
saved_feed = FEED[:]
try:
    globals()['FEED'] = [r('A1', 'Activation'), r('A2', 'Port-In'), r('B1', 'BYOD'), r('U1', 'Upgrade'),
                         r('HIX', 'Home Internet')]  # would auto-count for a config tenant, NOT for the house
    house = run_exec({})   # empty map = house default
finally:
    globals()['FEED'] = saved_feed
hg = house.get('classification_gaps') or {}
hd = next((x for x in house['by_location']['rows'] if x['store'].lower() == 'diversey'), None)
check("house: Home Internet NOT auto-counted (byte-identical, empty map)",
      hd and hd['total_activation'] == 4, hd and hd['total_activation'])
check("house: Home Internet instead SURFACES as an unrecognized label",
      any(u['contract_type'] == 'Home Internet' for u in hg.get('unrecognized_contract_types', [])), hg)
check("pay classifier untouched: classify_contract_type('Home Internet') is None",
      R.classify_contract_type('Home Internet') is None)
check("pay classifier untouched: classify_contract_type('FiOS') is None",
      R.classify_contract_type('FiOS') is None)

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
