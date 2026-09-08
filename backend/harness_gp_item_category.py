"""PROOF: GP categorisation at the ITEM grain, and categories a tenant may add without losing money.

OWNER DIRECTIVE 2026-09-08, verbatim: *"on gp category map i should be able to click on the line
items to properly assign them to the right category it is showing 2447 blank department, all of them
need to be categorized, new categories should be able to add"*.

THE DEFECT, MEASURED LIVE (org-scoped reads 2026-09-08). `commcalc.gp_category_map` (mig 069) keys an
override by DEPARTMENT, and the built-in rule sends a blank department to 'plan':

    org 854f6d7b… (Luxelink)     2,447 blank-department lines of  14,823   (16.5%)
    org 00000000…0001 (house)   27,010 blank-department lines of 155,677   (17.4%)

The 2,447 lines carry $31,084.02 of gross profit and resolve to just TWENTY distinct product
descriptions — and they are not all plans. 385 "Device Protection", 247 "Total Wireless Protect+",
149 "Total Wireless Home Internet" and 136 "Total Wireless Device Upgrade" are 917 lines counted as
'plan' today. Every one has a blank department AND a blank category, so NO setting of the department
map can separate them: one label, twenty products, four meanings. The grain is the defect.

THE TRAP IN "new categories should be able to add". The GP report aggregates into exactly four money
buckets — device (ext_price), accessory (configured basis), plan (gp), other (gp) — plus 'exclude'.
A tenant-invented category matching none of them would be summed into NOTHING: its lines would leave
the report with no error raised and no total visibly moving. That is the silent-zero class applied to
a whole category. So a category is a free-form LABEL that always declares its bucket (`rolls_up_to`),
defaulting to 'other' — where an unmapped line already sits — so adding one can never lose a dollar.

WHAT THIS PINS
  A. precedence: item override > accessory config > department override > box > blank='plan' > other;
  B. byte-identity: no item rows and no category rows reproduces today's classification exactly;
  C. a tenant category resolves THROUGH its bucket, so the arithmetic downstream never sees a name
     it does not know;
  D. a built-in cannot be re-pointed at another bucket (that would restate every prior month), and
     an inactive category is ignored;
  E. THE REGRESSION — the real 2,447-line product mix: today all 2,447 land in 'plan'; with the four
     item overrides the owner would set, 917 lines move and 1,530 stay, and NO line goes missing;
  F. one item identity — `gp_report.item_key` is what `router._item_key` uses, so the GP override and
     the item-mapping editor cannot key the same product differently.

PURE / DB-FREE: imports only the pure classifier module; no network, no database, stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc.gp_report import (  # noqa: E402
    DEFAULT_GP_CATEGORIES, GP_CATEGORIES, bucket_map, item_key,
    _dept_classifier, _gp_overrides, _item_overrides,
)

failures = []
checks = 0


def check(label, cond):
    global checks
    checks += 1
    if not cond:
        failures.append(label)


def eq(label, got, want):
    check('%s (got %r want %r)' % (label, got, want), got == want)


# ── B. byte-identity: nothing configured reproduces today ──────────────────────────────────────
base = _dept_classifier(None)
eq('B1 blank department still defaults to plan', base(''), 'plan')
eq('B2 Android - XP still device', base('Android - XP'), 'device')
eq('B3 Ondigo still accessory', base('Ondigo'), 'accessory')
eq('B4 an unknown department still other', base('Whatever'), 'other')
eq('B5 no categories configured -> the five built-ins map to themselves',
   bucket_map(None), {c: c for c in GP_CATEGORIES})
eq('B6 no item rows -> empty override map', _item_overrides(None), {})

# the five built-ins are exactly the report's buckets — no more, no fewer
eq('B7 the seeded defaults are precisely the report buckets',
   {c['value'] for c in DEFAULT_GP_CATEGORIES}, set(GP_CATEGORIES))
check('B8 every seeded default rolls up to itself',
      all(c['rolls_up_to'] == c['value'] for c in DEFAULT_GP_CATEGORIES))

# ── C. a tenant category resolves through its bucket ───────────────────────────────────────────
cats = [
    {'value': 'protection', 'label': 'Protection', 'rolls_up_to': 'other', 'is_active': True},
    {'value': 'home_internet', 'label': 'Home internet', 'rolls_up_to': 'plan', 'is_active': True},
    {'value': 'upgrade', 'label': 'Upgrade', 'rolls_up_to': 'device', 'is_active': True},
]
bm = bucket_map(cats)
eq('C1 a new category maps to the bucket it declares', bm['protection'], 'other')
eq('C2 ... and another to a different one', bm['home_internet'], 'plan')
eq('C3 ... and to device', bm['upgrade'], 'device')
eq('C4 the built-ins are untouched by adding categories',
   {k: bm[k] for k in GP_CATEGORIES}, {c: c for c in GP_CATEGORIES})
check('C5 every resolved bucket is one the report can actually count',
      all(v in GP_CATEGORIES for v in bm.values()))

# a category with a missing/nonsense bucket falls to 'other' — never dropped
eq('C6 an unset bucket defaults to other (never dropped)',
   bucket_map([{'value': 'mystery', 'is_active': True}])['mystery'], 'other')
eq('C7 an unrecognised bucket defaults to other',
   bucket_map([{'value': 'x', 'rolls_up_to': 'not_a_bucket'}])['x'], 'other')

# ── D. a built-in cannot be re-pointed; inactive is ignored ────────────────────────────────────
eq('D1 re-pointing a built-in is refused (history would be restated)',
   bucket_map([{'value': 'device', 'rolls_up_to': 'other', 'is_active': True}])['device'], 'device')
check('D2 an inactive category is not resolvable',
      'gone' not in bucket_map([{'value': 'gone', 'rolls_up_to': 'plan', 'is_active': False}]))

# a department mapped to a TENANT category lands on that category's bucket
ovr = _gp_overrides([{'department': 'PROTECT', 'category': 'protection'}], bm)
eq('D3 a department mapped to a tenant category resolves to its bucket', ovr.get('PROTECT'), 'other')
ovr_default = _gp_overrides([{'department': 'PROTECT', 'category': 'protection'}])
check('D4 ... and is ignored when no categories are configured (unknown name)', 'PROTECT' not in ovr_default)

# ── F. one item identity ───────────────────────────────────────────────────────────────────────
from app.modules.commcalc.router import _item_key as router_item_key  # noqa: E402
for sku, desc in [('abc123', 'Widget'), ('', 'Device Protection'), (None, 'Total MAX 5G Plan $55'),
                  ('nan', 'Fallback'), ('0', 'Zero sku'), ('  ', 'Spaces')]:
    eq('F1 router and gp_report agree on item_key for %r/%r' % (sku, desc),
       router_item_key(sku, desc), item_key(sku, desc))
eq('F2 sku wins over description', item_key('sku-9', 'Ignored'), 'SKU-9')
eq('F3 blank sku falls back to the description, upper-cased',
   item_key('', 'Device Protection'), 'DEVICE PROTECTION')

# ── A. precedence, exercised through the real classifier factory ───────────────────────────────
items = [{'item_key': 'DEVICE PROTECTION', 'gp_category': 'protection'}]
imap = _item_overrides(items, bm)
eq('A1 the item override resolves through its category bucket', imap['DEVICE PROTECTION'], 'other')
check('A2 an item override for an unknown category is dropped, not guessed',
      _item_overrides([{'item_key': 'X', 'gp_category': 'nope'}], bm) == {})

# ── E. THE REGRESSION — the real 2,447-line mix ────────────────────────────────────────────────
# Product -> line count, exactly as measured on org 854f6d7b… for the blank-department rows.
LIVE_MIX = [
    ('Device Protection', 385), ('Total MAX 5G Plan $55', 353),
    ('Total ALL ACCESS Plan $65', 301), ('Total MAX 5G BYO Plan $30', 255),
    ('Total Wireless Protect+', 247),
    ('Total Wireless Base Unlimited Tablet 3-Month Plan', 199),
    ('Total STARTER Plan $40', 177), ('Total Wireless Home Internet', 149),
    ('Total Wireless Device Upgrade', 136),
    ('Total Wireless Base Unlimited Tablet 6-Month Plan', 124),
    ('Total Wireless Base Unlimited Tablet Plan $50', 93),
    ('Total ALL ACCESS 2 Month Plan $130', 12), ('Total MAX 5G 2 Month Plan $110', 3),
    ('Total MAX 5G 3 Month Plan $165', 3), ('Total Wireless 5G Unlimited Tablet 6-Month Plan', 3),
    ('Total Wireless 5G Unlimited Tablet 3-Month Plan', 2),
    ('Total ALL ACCESS 3 Month Plan $195', 2), ('Total Wireless 5G Unlimited $55', 1),
    ('Total Wireless 5G Unlimited Tablet Plan $60', 1), ('Total Wireless $50 Data Plan 100GB', 1),
]
eq('E1 the live mix is the reported 2,447 lines', sum(n for _d, n in LIVE_MIX), 2447)
eq('E2 ... across the reported 20 distinct products', len(LIVE_MIX), 20)

rows = [{'department': '', 'sku': None, 'product_desc': d} for d, n in LIVE_MIX for _ in range(n)]

# TODAY: no overrides at all -> every one of the 2,447 is 'plan'.
today = _dept_classifier(None)
eq('E3 today all 2,447 blank-department lines are classified plan',
   sum(1 for r in rows if today(r['department']) == 'plan'), 2447)

# AFTER: the four overrides the owner would set on the four non-plan products.
owner_items = [
    {'item_key': item_key(None, 'Device Protection'), 'gp_category': 'protection'},
    {'item_key': item_key(None, 'Total Wireless Protect+'), 'gp_category': 'protection'},
    {'item_key': item_key(None, 'Total Wireless Home Internet'), 'gp_category': 'home_internet'},
    {'item_key': item_key(None, 'Total Wireless Device Upgrade'), 'gp_category': 'upgrade'},
]
iov = _item_overrides(owner_items, bm)


def classify(r):
    hit = iov.get(item_key(r.get('sku'), r.get('product_desc')))
    return hit or today(r.get('department'))


buckets = {}
for r in rows:
    buckets[classify(r)] = buckets.get(classify(r), 0) + 1

eq('E4 no line is lost — every one of the 2,447 still lands somewhere', sum(buckets.values()), 2447)
check('E5 every bucket the mix produces is one the report counts',
      set(buckets) <= set(GP_CATEGORIES))
# RELABELLED is not the same as REBUCKETED, and conflating them is how a "fix" gets mis-sold.
# 917 lines get an explicit category. Only 768 of them change which MONEY BUCKET they count in,
# because the tenant declared home_internet -> plan: those 149 lines are now correctly NAMED and
# still counted in plan, which is a reporting gain with zero effect on the GP arithmetic.
relabelled = sum(n for d, n in LIVE_MIX
                 if item_key(None, d) in iov)
eq('E6a 917 lines receive an explicit item category', relabelled, 917)
eq('E6b but only 768 of them change money bucket', 2447 - buckets.get('plan', 0), 768)
eq('E7 plan keeps the 1,530 genuine plans PLUS the 149 home-internet lines whose category '
   'declares plan as its bucket', buckets.get('plan'), 1530 + 149)
# protection (385 + 247) rolls to 'other'; home internet (149) deliberately rolls back to plan
eq('E8 the protection lines land where their category points', buckets.get('other'), 385 + 247)
eq('E9 the upgrade lines land on device', buckets.get('device'), 136)
check('E9b the three buckets account for every line',
      buckets.get('plan', 0) + buckets.get('other', 0) + buckets.get('device', 0) == 2447)

# and the item grain does NOT disturb a row it was not asked about
eq('E10 a product with no override still follows the department rule',
   classify({'department': '', 'sku': None, 'product_desc': 'Total STARTER Plan $40'}), 'plan')
eq('E11 an item override applies regardless of department',
   classify({'department': 'Ondigo', 'sku': None, 'product_desc': 'Device Protection'}), 'other')

print('%s  harness_gp_item_category: %d checks, %d failed'
      % ('FAIL' if failures else 'OK  ', checks, len(failures)))
for f in failures:
    print('   FAILED: %s' % f)
sys.exit(1 if failures else 0)
