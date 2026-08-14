"""Proof for diag/blank-ct-unmatched-transactions (owner report 2026-08-14 — "see the 7").

SYMPTOM: the Sales Report banner says "7 transaction(s) have no contract type and no activation rule
matched — map them" but never says WHICH 7 or their line shape, so the owner cannot author the blank-ct
activation rule the banner asks for. The counts (blank_ct_unrecovered) existed; the actual transactions did
not.

FIX (DISPLAY-ONLY, read-only): _classification_gaps(..., want_samples=True) now ALSO returns the ACTUAL
unrecovered transactions — built from the SAME `unrecovered` tid set the counts already derive (no second
classifier pass). This proves:
  (1) the listed transactions ARE exactly the unrecovered ones (count matches blank_ct_unrecovered),
  (2) their department/category/product_desc are surfaced (the fields a mig-224 rule matches on),
  (3) the by_line grouping counts lines + distinct transactions per (store, dept, cat, product),
  (4) a bill-payment / accessory-only blank-ct txn is NOT listed (non-activation, never alarmed),
  (5) once an activation rule matches the shape, the txn leaves the unrecovered list (rescued),
  (6) want_samples defaults False -> the returned dict is byte-identical for existing callers.

Pure unit test over the REAL router function; NO DB/network.
Run:  cd backend && python3 scratchpad/classification_unmatched_samples_proof.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R  # noqa: E402

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}  {extra}")


def line(tid, dept="", cat="", pdesc="", ct="", store="4640 Diversey", tt="Sale", voided=""):
    return {'trans_id': tid, 'trans_date': '2026-07-05', 'store': store, 'salesperson': 'REP1',
            'department': dept, 'category': cat, 'product_desc': pdesc, 'contract_type': ct,
            'ext_price': 100.0, 'gp': 20.0, 'voided': voided, 'trans_type': tt}


# Real luxelink July shapes. T1/T2 = blank-ct activation-CAPABLE (branded device + rate plan) => unrecovered
# when no activation rule is configured. T3 = bill-payment only (blank ct) => non-activation, never listed.
# T4 = a labeled activation ('Prepaid New' via ct_map) => classed, never blank-ct.
FEED = [
    line('T1', dept='BrandedHandset', cat='KittedBranded', pdesc='Samsung Galaxy A15'),
    line('T1', dept='Rtr', cat='Other Carrier payments', pdesc='Unlimited Plan'),
    line('T2', dept='BrandedHandset', cat='HandsetBranded', pdesc='Moto G Play'),
    line('T2', dept='Rtr', cat='Other Carrier payments', pdesc='Unlimited Plan'),
    line('T3', dept='Rtr', cat='Other Carrier payments', pdesc='Boost RTR'),   # bill payment only
    line('T4', dept='BrandedHandset', cat='KittedBranded', pdesc='iPhone 13', ct='Prepaid New'),
]

# Tenant is config-driven via a ct_map that only covers the labeled activation; NO activation_rules yet.
ACFG_NO_RULES = {
    'departments': set(), 'categories': set(), 'products': set(), 'acima_tenders': set(),
    'box_departments': set(), 'setup_fee_products': set(),
    'billpay_products': set(),  # empty -> falls back to Boost default tokens ('boost rtr') for T3
    'box_count_buckets': set(), 'contract_type_map': {'prepaid new': 'premium'},
    'contract_type_map_raw': {'prepaid new': 'premium'}, 'activation_rules': [], 'catalog_classifier': None}

# Same tenant WITH the observed-shape premium rule (branded device + rate plan) => T1/T2 rescued.
ACFG_WITH_RULE = dict(ACFG_NO_RULES, activation_rules=[
    {'bucket': 'premium', 'all_of': [{'field': 'department', 'contains_any': ['BrandedHandset']},
                                     {'field': 'department', 'contains_any': ['Rtr']}]}])

print("(1) want_samples lists the ACTUAL unrecovered transactions (the exact 7 behind the banner)")
g = R._classification_gaps([dict(x) for x in FEED], ACFG_NO_RULES, want_samples=True)
check("blank_ct_unrecovered == 2 (T1, T2)", g['blank_ct_unrecovered'] == 2, g['blank_ct_unrecovered'])
tids = {t['trans_id'] for t in g['blank_ct_unrecovered_txns']}
check("transactions listed == the unrecovered tids", tids == {'T1', 'T2'}, tids)
check("count of listed txns == blank_ct_unrecovered",
      len(g['blank_ct_unrecovered_txns']) == g['blank_ct_unrecovered'])
check("T3 (bill-payment only) NOT listed", 'T3' not in tids, tids)
check("T4 (labeled activation) NOT listed", 'T4' not in tids, tids)
check("blank_ct_non_activation == 1 (T3)", g['blank_ct_non_activation'] == 1, g['blank_ct_non_activation'])

print("(2) each listed txn surfaces department/category/product_desc (what a mig-224 rule matches on)")
t1 = next(t for t in g['blank_ct_unrecovered_txns'] if t['trans_id'] == 'T1')
depts = {l['department'] for l in t1['lines']}
check("T1 shows both its device + plan departments", depts == {'BrandedHandset', 'Rtr'}, depts)
check("T1 store surfaced", t1['store'] == '4640 Diversey', t1['store'])
check("T1 product_desc surfaced", any(l['product_desc'] == 'Samsung Galaxy A15' for l in t1['lines']))

print("(3) by_line groups lines + distinct transactions per (store, dept, cat, product)")
bl = {(r['department'], r['category'], r['product_desc']): r for r in g['blank_ct_unrecovered_by_line']}
plan = bl.get(('Rtr', 'Other Carrier payments', 'Unlimited Plan'))
check("the shared rate-plan line groups 2 lines across 2 txns",
      plan and plan['lines'] == 2 and plan['transactions'] == 2, plan)
check("by_line is sorted by transactions desc",
      [r['transactions'] for r in g['blank_ct_unrecovered_by_line']]
      == sorted((r['transactions'] for r in g['blank_ct_unrecovered_by_line']), reverse=True))

print("(4) once an activation rule matches the shape, the txns are rescued and drop off the list")
g2 = R._classification_gaps([dict(x) for x in FEED], ACFG_WITH_RULE, want_samples=True)
check("blank_ct_unrecovered == 0 (T1/T2 rescued)", g2['blank_ct_unrecovered'] == 0, g2['blank_ct_unrecovered'])
check("rescued_by_rules == 2", g2['rescued_by_rules'] == 2, g2['rescued_by_rules'])
check("unrecovered_txns now empty", g2['blank_ct_unrecovered_txns'] == [], g2['blank_ct_unrecovered_txns'])

print("(5) want_samples defaults False -> byte-identical dict for existing callers (report / exec MTD)")
base = R._classification_gaps([dict(x) for x in FEED], ACFG_NO_RULES)
check("no sample keys leak into the default return",
      'blank_ct_unrecovered_by_line' not in base and 'blank_ct_unrecovered_txns' not in base, list(base))
check("default counts identical to want_samples counts",
      (base['blank_ct_unrecovered'], base['blank_ct_non_activation'], base['rescued_by_rules'])
      == (g['blank_ct_unrecovered'], g['blank_ct_non_activation'], g['rescued_by_rules']))

print("(6) empty/house tenant -> nothing listed, note None (no false alarm)")
HOUSE = dict(ACFG_NO_RULES, contract_type_map={}, contract_type_map_raw={})
house_feed = [line('H1', dept='XP', cat='Phone', pdesc='Boost Phone', ct='Activation')]
gh = R._classification_gaps(house_feed, HOUSE, want_samples=True)
check("house: no unrecovered, empty listing, note None",
      gh['blank_ct_unrecovered'] == 0 and gh['blank_ct_unrecovered_txns'] == [] and gh['note'] is None, gh)

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
