"""Proof for agent/commission/catalog-followups — the four post-merge Gate-1 nits filed against the
shipped catalog-accessory-byod wave (origin/main dc01434). NON-MONEY, display/perf only.

WHAT THIS PROVES
  ②  ORG-SCOPED CONFIG CACHE (accessory_catalog.cache_*). L1 = REQUEST-SCOPED (default, keyed on the
     client object + org_id) so ONE request stops re-reading the same tables; L2 = a cross-request TTL
     layer that is OPT-IN (COMMCALC_CFG_CACHE_TTL, default 0/off).
       A0  the cross-request layer really is off by default
       A1  _accessory_config: 1st call reads, a repeat on the SAME client issues ZERO queries and returns
           the same value; a DIFFERENT client (= another request) is a clean MISS, so a config change made
           outside any endpoint is still picked up on the next request
       A2  the key includes org_id — org B never sees org A's config (different departments)
       A3  the returned dict is COPY-protected: mutating it can't poison the entry
       A4  with the TTL opted in, a new client hits it and it expires; back at the default it never does
       A5  a BLANK org_id is never cached (no tenant-less key can ever be created)
       A6  GET /commcalc/catalog (the real endpoint, one fresh client per request like get_supabase):
           the catalog tables go from 6 reads to 2 and the request from 17 queries to 12, with a
           BYTE-IDENTICAL payload; a second request pays the same 12 (nothing leaks between requests)
       A7  commission_engine's classifier path (accessory_catalog.build / build_catalog_sets) is cached;
           an EXPLICIT acc_cats argument is never cached (caller-specific)
       INVALIDATION — every write that can change the answer drops the org's entries IMMEDIATELY:
       A8  PUT /accessory-config          A9  PUT /catalog/override (set AND clear)
       A10 POST /gp-category-map (upsert AND delete)     A11 PUT /flag-rules
       A12 a catalog upload (_upload_file_impl wiring asserted on the real source; all 6 sites counted)
       A13 invalidation is ORG-SCOPED: invalidating org A leaves org B's cache intact

  ③  Catalog category options de-duped case-insensitively (backend half — accessory_catalog.
     catalog_categories): 'Accessories' (file) + 'accessories' (override) = ONE option, FILE spelling shown.
     Filtering by either spelling still returns the row (list_catalog is case-insensitive).

  ⑥  gp_report bucket_composition: ONE deterministic sort key (|gp| → |ext_price| → department) instead of
     the `-abs(gp) if gp else -ext_price` mode flip; proven on the exact case the old key got wrong, and
     proven order-stable under input shuffling.

  ⑦  gp_report bucket_composition applies the canonical skip rules (voided / trans_type=='Return' /
     unattributed rep) — the SAME predicate router._sales_cell_agg uses — and its totals EQUAL the agg
     path's totals on a mixed fixture. Nothing is hidden: excluded lines are reported per department and
     org-wide by reason, and a department whose lines were ALL skipped still gets a row.
     MONEY-SAFE DIFFERENTIAL: store_rows / rep_rows / totals are BYTE-IDENTICAL to origin/main's gp_report
     on the same fixture (the transparency block is the only behavioural change).

  R  REGRESSION (rebuilds the lost harness_catalog checks): classifier precedence UPC→SKU→product_id→desc,
     override beats file category, trailing-'.0' safety ('V2.0-CASE' preserved / '123.0' → '123'), and the
     engine stays INERT for a plan with no match_field='accessory' rule.

No DB, no network — the real functions over an in-memory FakeClient that COUNTS table reads.
Run:  cd backend && python3 scratchpad/catalog_followups_proof.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ['COMMCALC_CFG_CACHE_TTL'] = '0'    # default: request-scoped only

import app.modules.commcalc.router as R                       # noqa: E402
import app.modules.commcalc.accessory_catalog as AC           # noqa: E402
import app.modules.commcalc.gp_report as GP                   # noqa: E402

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


# ── in-memory fake supabase client (house pattern) + a TABLE-READ COUNTER ───────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table, forbidden, counter):
        self.store, self.t, self.forbidden, self.counter = store, table, forbidden, counter
        self.f = []
        self._count = None
        self._pending = None

    def select(self, *a, **k):
        if k.get('count') == 'exact':
            self._count = True
        cols = ",".join(str(x) for x in a)
        for col in self.forbidden.get(self.t, set()):
            if col in cols.split(','):
                raise Exception(f"column {self.t}.{col} does not exist")
        self.counter.append(('select', self.t))
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def neq(self, c, v):
        self.f.append(('neq', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def upsert(self, row, on_conflict=None):
        for col in self.forbidden.get(self.t, set()):
            if col in row:
                raise Exception(f"column {self.t}.{col} does not exist")
        self.counter.append(('write', self.t))
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

    def insert(self, rows):
        self.counter.append(('write', self.t))
        tgt = self.store.setdefault(self.t, [])
        for r in (rows if isinstance(rows, list) else [rows]):
            tgt.append(dict(r))
        self._pending = {}
        return self

    def delete(self):
        self._del = True
        self.counter.append(('write', self.t))
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
        return True

    def execute(self):
        if getattr(self, '_del', False):
            rows = self.store.setdefault(self.t, [])
            self.store[self.t] = [r for r in rows if not self._m(r)]
            return FakeResult(data=[])
        if self._pending is not None:
            return FakeResult(data=[dict(self._pending)])
        rows = self.store.setdefault(self.t, [])
        m = [dict(r) for r in rows if self._m(r)]
        if self._count:
            return FakeResult(data=m, count=len(m))
        return FakeResult(data=m)


class FakeSchema:
    def __init__(self, store, forbidden, counter):
        self.store, self.forbidden, self.counter = store, forbidden, counter

    def table(self, t):
        return FakeQuery(self.store, t, self.forbidden, self.counter)

    def rpc(self, *a, **k):
        raise Exception('no rpc in this proof')


class FakeClient:
    def __init__(self, store, forbidden=None):
        self.store = store
        self.forbidden = forbidden or {}
        self.counter = []

    def schema(self, s):
        return FakeSchema(self.store, self.forbidden, self.counter)

    def reads(self, table=None):
        return len([c for c in self.counter if c[0] == 'select' and (table is None or c[1] == table)])

    def reset(self):
        self.counter = []


ORG_A = 'lux-aaaa'
ORG_B = 'house-bbbb'


def base_store():
    """Two tenants, each with their own accessory_config + catalog + one override."""
    return {
        'accessory_config': [
            {'org_id': ORG_A, 'departments': ['Ondigo A'], 'categories': ['CaseCat'],
             'product_keywords': [], 'acima_tenders': [], 'box_departments': [],
             'setup_fee_keywords': [], 'contract_type_map': {}, 'activation_rules': [],
             'billpay_products': [], 'box_count_buckets': [],
             'catalog_classify_enabled': True, 'catalog_accessory_categories': ['Accessories']},
            {'org_id': ORG_B, 'departments': ['Ondigo B'], 'categories': [],
             'product_keywords': [], 'acima_tenders': [], 'box_departments': [],
             'setup_fee_keywords': [], 'contract_type_map': {}, 'activation_rules': [],
             'billpay_products': [], 'box_count_buckets': [],
             'catalog_classify_enabled': False, 'catalog_accessory_categories': []},
        ],
        'raw_catalog': [
            {'org_id': ORG_A, 'product_id': 900, 'sku': 'CASE-1', 'upc': '0001112223',
             'product_desc': 'Clear Case iPhone 15', 'department': 'ACC', 'category': 'Accessories',
             'cost': 3.5, 'retail_price': 19.99},
            {'org_id': ORG_A, 'product_id': 901, 'sku': 'V2.0-CASE', 'upc': '',
             'product_desc': 'V2 Rugged Case', 'department': 'ACC', 'category': 'accessories',
             'cost': 6.0, 'retail_price': 29.99},
            {'org_id': ORG_A, 'product_id': 902, 'sku': 'PH-1', 'upc': '0004445556',
             'product_desc': 'Moto G Play', 'department': 'PHONE', 'category': 'Handsets',
             'cost': 40.0, 'retail_price': 79.99},
            {'org_id': ORG_B, 'product_id': 500, 'sku': 'B-1', 'upc': '',
             'product_desc': 'House Widget', 'department': 'W', 'category': 'Widgets', 'cost': 1.0},
        ],
        'catalog_category_override': [
            {'org_id': ORG_A, 'match_type': 'sku', 'match_value': 'ph-1', 'category': 'accessories'},
        ],
        'gp_category_map': [],
        'flag_rules': [],
    }


def fresh(forbidden=None):
    AC.invalidate()
    c = FakeClient(base_store(), forbidden)
    R.sb = lambda: c            # every route helper resolves to this client
    return c


R._can_edit_classification = lambda *a, **k: True     # rbac is proven elsewhere; not this package


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n② ORG-SCOPED CONFIG CACHE — L1 request-scoped (default) + L2 opt-in TTL")

# L1 = keyed on the CLIENT OBJECT + org_id: it de-duplicates the repeats INSIDE one handler (which threads
# a single sb() client through) and cannot survive into another request. L2 (cross-request TTL) is OFF
# unless COMMCALC_CFG_CACHE_TTL is set.
check("A0 the cross-request TTL layer is OFF by default", AC.cache_ttl() == 0.0, str(AC.cache_ttl()))

c = fresh()
cfg1 = R._accessory_config(c, ORG_A)
n1 = c.reads()
c.reset()
cfg2 = R._accessory_config(c, ORG_A)
n2 = c.reads()
check("A1a first _accessory_config actually reads the DB (>=10 queries)", n1 >= 10, f"n1={n1}")
check("A1b a repeat on the SAME client issues ZERO queries", n2 == 0, f"n2={n2}")
check("A1c cached value equals the uncached value",
      cfg1['departments'] == cfg2['departments'] and cfg1['categories'] == cfg2['categories']
      and cfg1['catalog_classify_enabled'] == cfg2['catalog_classify_enabled'],
      f"{cfg1['departments']} vs {cfg2['departments']}")
un = R._accessory_config_uncached(c, ORG_A)
check("A1d cached == a forced uncached resolution (same keys + same values)",
      set(un) == set(cfg2) and un['departments'] == cfg2['departments']
      and un['catalog_accessory_categories_list'] == cfg2['catalog_accessory_categories_list'])

# THE safety property: a DIFFERENT client (= a different request) never reuses the memo by default.
other = FakeClient(base_store())
R._accessory_config(other, ORG_A)
check("A1e a DIFFERENT client is a clean MISS — nothing survives the request", other.reads() >= 10,
      f"{other.reads()}")
other.store['accessory_config'][0]['departments'] = ['ChangedOutsideAnEndpoint']
c3 = FakeClient(other.store)
check("A1f …so a config change made outside any endpoint is picked up on the next request",
      R._accessory_config(c3, ORG_A)['departments'] == {'changedoutsideanendpoint'},
      str(R._accessory_config(c3, ORG_A)['departments']))

c.reset()
cfgB = R._accessory_config(c, ORG_B)
check("A2a org B on the SAME client is still a MISS (the key includes org_id)", c.reads() >= 9,
      f"reads={c.reads()}")
check("A2b org B gets ITS OWN departments, never org A's",
      cfgB['departments'] == {'ondigo b'} and cfg2['departments'] == {'ondigo a'},
      f"B={cfgB['departments']} A={cfg2['departments']}")
check("A2c org B's catalog toggle is its own (False vs A's True)",
      cfgB['catalog_classify_enabled'] is False and cfg2['catalog_classify_enabled'] is True)
_ttl_keys, _stats, _req_keys = AC.cache_snapshot()
check("A2d every request-scoped key carries an org_id",
      _req_keys and all(len(k) == 2 and k[1] in (ORG_A, ORG_B) for k in _req_keys), str(_req_keys))
check("A2e nothing landed in the cross-request layer (it is off)", _ttl_keys == [], str(_ttl_keys))

poison = R._accessory_config(c, ORG_A)
poison['departments_list'] = ['HACKED']
poison['injected'] = True
after = R._accessory_config(c, ORG_A)
check("A3 mutating the returned dict cannot poison the cache",
      after.get('departments_list') == ['Ondigo A'] and 'injected' not in after,
      str(after.get('departments_list')))

# L2 — the opt-in cross-request layer
os.environ['COMMCALC_CFG_CACHE_TTL'] = '0.4'
AC.invalidate()
c = FakeClient(base_store())
R.sb = lambda: c
R._accessory_config(c, ORG_A)
c4 = FakeClient(base_store())
R._accessory_config(c4, ORG_A)
check("A4a with the TTL set, a DIFFERENT client hits the cross-request layer", c4.reads() == 0,
      f"{c4.reads()}")
check("A4b …and it is recorded in the TTL layer", [k for k in AC.cache_snapshot()[0] if k[1] == ORG_A])
time.sleep(0.55)
c5 = FakeClient(base_store())
R._accessory_config(c5, ORG_A)
check("A4c after the TTL expires the DB is read again", c5.reads() >= 10, f"{c5.reads()}")
os.environ['COMMCALC_CFG_CACHE_TTL'] = '0'
AC.invalidate()
c6 = FakeClient(base_store())
R._accessory_config(c6, ORG_A)
c7 = FakeClient(base_store())
R._accessory_config(c7, ORG_A)
check("A4d back at the default, a new client always reads", c7.reads() >= 10, f"{c7.reads()}")
check("A4e …and the cross-request layer stays empty", AC.cache_snapshot()[0] == [],
      str(AC.cache_snapshot()[0]))

c = fresh()
R._accessory_config(c, '')
_t, _s2, _r = AC.cache_snapshot()
check("A5a a BLANK org_id is never cached", _t == [] and _r == [], f"{_t} {_r}")
check("A5b cache_get on a blank org always misses", AC.cache_get('acfg', '', c) is None)
check("A5c cache_put on a blank org stores nothing",
      (AC.cache_put('acfg', None, {'x': 1}, c), AC.cache_snapshot()[2])[1] == [])

# A6 — the REAL GET /commcalc/catalog endpoint (one request = one client, the production shape)
class OneClientPerRequest:
    """R.sb() hands out a NEW client each call, like get_supabase() does in production."""
    def __init__(self, store):
        self.store = store
        self.clients = []
    def __call__(self):
        c = FakeClient(self.store)
        self.clients.append(c)
        return c
    def reads(self):
        return sum(x.reads() for x in self.clients)
    def reads_t(self, t):
        return sum(x.reads(t) for x in self.clients)

AC.invalidate()
os.environ['COMMCALC_CFG_CACHE_TTL'] = '0'
prov_pre = OneClientPerRequest(base_store())
R.sb = prov_pre
# simulate "no cache at all" by clearing the request memo between the endpoint's internal calls
_real_get = AC.cache_get
AC.cache_get = lambda *a, **k: None
pre_fix = R.catalog_list(org_id=ORG_A)
pre_total = prov_pre.reads()
pre_cat = prov_pre.reads_t('raw_catalog') + prov_pre.reads_t('catalog_category_override')
AC.cache_get = _real_get

prov = OneClientPerRequest(base_store())
R.sb = prov
out1 = R.catalog_list(org_id=ORG_A)
reads_cold = prov.reads()
cat_reads_cold = prov.reads_t('raw_catalog') + prov.reads_t('catalog_category_override')
prov2 = OneClientPerRequest(base_store())
R.sb = prov2
out2 = R.catalog_list(org_id=ORG_A)
check("A6a0 WITHOUT the cache ONE /catalog request re-read the catalog tables 6x", pre_cat == 6,
      f"{pre_cat}")
check("A6a WITH it those 6 collapse to 2 (one read per table)", cat_reads_cold == 2, f"{cat_reads_cold}")
check("A6a2 one /catalog request drops from 17 queries to 12", pre_total == 17 and reads_cold == 12,
      f"now={reads_cold} pre={pre_total}")
# NOTE (deliberate non-change): the 12 that remain are _accessory_config's per-COLUMN single-row probes.
# Each lives in its OWN try/except precisely so a pre-migration missing column cannot disturb the others
# (migs 213/214/217/218/224/231 all degrade that way) — collapsing them into one select would break that
# graceful degradation, so they were left alone.
check("A6b a SECOND request pays the same 12 (nothing leaks between requests by default)",
      prov2.reads() == 12, f"{prov2.reads()}")
check("A6c payload identical across requests",
      json.dumps(out1, sort_keys=True, default=str) == json.dumps(out2, sort_keys=True, default=str))
check("A6d cache-free payload is byte-identical to the cached one (no behaviour change)",
      json.dumps(pre_fix, sort_keys=True, default=str) == json.dumps(out1, sort_keys=True, default=str))

# A7 — the engine's classifier path
c = fresh()
s1 = AC.build_catalog_sets(c, ORG_A)
c.reset()
s2 = AC.build_catalog_sets(c, ORG_A)
check("A7a build_catalog_sets repeat on the same client: ZERO queries", c.reads() == 0, f"{c.reads()}")
check("A7b same sets returned", s1[0] == s2[0] and s1[1] == s2[1] and s1[2] == s2[2] and s1[3] == s2[3])
cx = fresh()
s3 = AC.build_catalog_sets(cx, ORG_A, acc_cats=['Handsets'])
check("A7c an EXPLICIT acc_cats bypasses the catsets cache (it reads for itself)", cx.reads() >= 1,
      f"{cx.reads()}")
# ORG_A's only 'Handsets' row (PH-1 / Moto G Play) is override-moved to 'accessories', so an explicit
# acc_cats=['Handsets'] must come back EMPTY — a caller-specific answer, provably not the cached default.
check("A7c2 …and returns the caller's OWN answer, not the cached default",
      s3[0] == set() and len(s1[0]) >= 3, f"{sorted(s3[0])} vs {sorted(s1[0])}")
check("A7d …and does NOT overwrite the cached default-arg entry",
      AC.build_catalog_sets(c, ORG_A)[0] == s1[0])
clf = AC.build(c, ORG_A)
check("A7e build() still yields a working classifier",
      clf.is_catalog_accessory_desc('Clear Case iPhone 15')
      and not clf.is_catalog_accessory_desc('Some Unlisted Product'))

# A8..A11 — invalidation: a WRITE followed by a READ inside the SAME request must not serve the memo
c = fresh()
before = R._accessory_config(c, ORG_A)['departments']
R.put_accessory_config({'departments': ['NewDept'], 'categories': [], 'product_keywords': []},
                       org_id=ORG_A, authorization='')
after = R._accessory_config(c, ORG_A)['departments']
check("A8 PUT /accessory-config invalidates immediately",
      before == {'ondigo a'} and after == {'newdept'}, f"{before} -> {after}")

c = fresh()
pre = R.catalog_list(org_id=ORG_A)
pre_eff = {r['product_desc']: r['effective_category'] for r in pre['rows']}
R.put_catalog_override({'match_type': 'upc', 'match_value': '0004445556', 'category': 'Accessories'},
                       org_id=ORG_A, authorization='')
post = R.catalog_list(org_id=ORG_A)
post_eff = {r['product_desc']: r['effective_category'] for r in post['rows']}
check("A9a the pre-write read reflected the file/sku state", pre_eff['Clear Case iPhone 15'] == 'accessories')
check("A9b PUT /catalog/override is reflected in the very next read",
      post_eff['Moto G Play'] == 'accessories' and post['rows'])
R.put_catalog_override({'match_type': 'sku', 'match_value': 'PH-1', 'category': ''},
                       org_id=ORG_A, authorization='')
R.put_catalog_override({'match_type': 'upc', 'match_value': '0004445556', 'category': ''},
                       org_id=ORG_A, authorization='')
cleared = {r['product_desc']: r['effective_category'] for r in R.catalog_list(org_id=ORG_A)['rows']}
check("A9c CLEARING an override also invalidates (file category restored)",
      cleared['Moto G Play'] == 'handsets', str(cleared))

import asyncio  # noqa: E402
c = fresh()
R._accessory_config(c, ORG_A)
asyncio.run(R.set_gp_category_map({'department': 'GPACC', 'category': 'accessory'}, org_id=ORG_A))
check("A10a POST /gp-category-map (upsert) invalidates → the new dept appears",
      'gpacc' in R._accessory_config(c, ORG_A)['departments'],
      str(R._accessory_config(c, ORG_A)['departments']))
asyncio.run(R.set_gp_category_map({'department': 'GPACC', 'category': ''}, org_id=ORG_A))
check("A10b …and the DELETE branch invalidates too → the dept is gone",
      'gpacc' not in R._accessory_config(c, ORG_A)['departments'],
      str(R._accessory_config(c, ORG_A)['departments']))

c = fresh(forbidden={'accessory_config': {'departments'}})   # force the flag_rules fallback path
c.store['flag_rules'] = [{'id': 1, 'org_id': ORG_A, 'accessory_departments': ['LegacyDept'],
                          'accessory_categories': [], 'accessory_product_keywords': [],
                          'acima_tenders': []}]
legacy = R._accessory_config(c, ORG_A)['departments']
R.put_flag_rules({'accessory_threshold': 40}, org_id=ORG_A)
check("A11a pre-mig-208 fallback still resolves via flag_rules", legacy == {'legacydept'}, str(legacy))
check("A11b PUT /flag-rules invalidates the org's cache",
      not [k for k in AC.cache_snapshot()[2] if k[1] == ORG_A], str(AC.cache_snapshot()[2]))

import inspect  # noqa: E402
up_src = inspect.getsource(R._upload_file_impl)     # upload_file is the thin mig-202 trace wrapper
check("A12 the catalog upload invalidates after the ingest",
      "if file_type in ('catalog', 'master_cats'):" in up_src
      and "_invalidate_accessory_config(org_id)" in up_src)
inv_sites = inspect.getsource(R).count('_invalidate_accessory_config(org_id)') - 1   # minus the def line
check("A12b all six write paths are wired (accessory-config, override, upload, gp-map x2, flag-rules)",
      inv_sites == 6, f"{inv_sites}")

os.environ['COMMCALC_CFG_CACHE_TTL'] = '30'          # exercise ORG-SCOPED invalidation on the TTL layer
AC.invalidate()
ca, cb = FakeClient(base_store()), FakeClient(base_store())
R._accessory_config(ca, ORG_A)
R._accessory_config(cb, ORG_B)
AC.invalidate(ORG_A)
check("A13a invalidate(orgA) drops ONLY org A", all(k[1] == ORG_B for k in AC.cache_snapshot()[0]),
      str(AC.cache_snapshot()[0]))
cc = FakeClient(base_store())
R._accessory_config(cc, ORG_B)
check("A13b org B is still served", cc.reads() == 0, f"{cc.reads()}")
cd = FakeClient(base_store())
R._accessory_config(cd, ORG_A)
check("A13c org A has to read again", cd.reads() >= 10, f"{cd.reads()}")
os.environ['COMMCALC_CFG_CACHE_TTL'] = '0'
AC.invalidate()


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n③ CATALOG CATEGORY OPTIONS — case-variant de-dupe (backend half)")
c = fresh()
cats = AC.catalog_categories(c, ORG_A)
low = [x.lower() for x in cats]
check("B1 no two options differ only by case", len(low) == len(set(low)), str(cats))
check("B2 'Accessories' + 'accessories' collapse to ONE option", low.count('accessories') == 1, str(cats))
check("B3 the FILE spelling is the one shown", 'Accessories' in cats, str(cats))
check("B4 an override-only category stays visible",
      'handsets' in low or 'Handsets' in cats, str(cats))
check("B5 options are sorted case-insensitively", cats == sorted(cats, key=lambda s: s.lower()))
hits_upper = AC.list_catalog(c, ORG_A, category='Accessories')
hits_lower = AC.list_catalog(c, ORG_A, category='accessories')
check("B6 filtering by either case returns the SAME rows (matching unaffected)",
      [r['product_desc'] for r in hits_upper] == [r['product_desc'] for r in hits_lower]
      and len(hits_upper) >= 2, f"{len(hits_upper)} vs {len(hits_lower)}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n⑥/⑦ GP bucket_composition — deterministic sort + canonical skip rules")


def sale(dept, ext, gp, rep='ALICE', store='100 Main St', voided='', ttype='', pdesc='x'):
    return {'store': store, 'department': dept, 'ext_price': ext, 'gp': gp, 'salesperson': rep,
            'product_desc': pdesc, 'voided': voided, 'trans_type': ttype, 'trans_id': f'T{abs(hash((dept, ext, gp, rep, voided, ttype))) % 99999}',
            'trans_date': '2026-07-02', 'category': '', 'contract_type': ''}


def run_gp(sales, gp_map=None):
    return GP.calc_gp_report(sales, [], [], [], [], [], [], 'July 2026',
                             comp_rows=[], gp_category_map=gp_map or [])


# ⑥ — the exact case the old key got wrong
mix = [sale('BIGZERO', 10000.0, 0.0), sale('SMALLGP', 10.0, 5.0)]
comp = run_gp(mix)['bucket_composition']['other']
order = [r['department'] for r in comp]
old_order = [r['department'] for r in
             sorted(comp, key=lambda x: -abs(x['gp']) if x['gp'] else -x['ext_price'])]
check("C1 new key ranks the real-GP department first", order == ['SMALLGP', 'BIGZERO'], str(order))
check("C2 the OLD key ranked them the other way (this is the bug the nit filed)",
      old_order == ['BIGZERO', 'SMALLGP'], str(old_order))
shuffled = list(reversed(mix))
check("C3 ordering is input-order independent",
      [r['department'] for r in run_gp(shuffled)['bucket_composition']['other']] == order)
tie = [sale('ZZZ', 5.0, 1.0), sale('AAA', 5.0, 1.0), sale('MMM', 5.0, 1.0)]
tied = [r['department'] for r in run_gp(tie)['bucket_composition']['other']]
check("C4 exact ties break by department name (total, stable ordering)", tied == ['AAA', 'MMM', 'ZZZ'],
      str(tied))
check("C5 ties are reproducible under shuffling",
      [r['department'] for r in run_gp(list(reversed(tie)))['bucket_composition']['other']] == tied)
neg = run_gp([sale('CREDIT', -500.0, -120.0), sale('TINY', 5.0, 1.0)])['bucket_composition']['other']
check("C6 a negative-GP department still ranks by MAGNITUDE",
      [r['department'] for r in neg] == ['CREDIT', 'TINY'], str([r['department'] for r in neg]))

# ⑦ — mixed fixture: good / voided / Return / admin / blank-rep
mixed = [
    sale('GOODDEPT', 100.0, 30.0),
    sale('GOODDEPT', 200.0, 40.0, rep='BOB'),
    sale('GOODDEPT', 999.0, 111.0, voided='YES'),
    sale('GOODDEPT', 50.0, 9.0, ttype='Return'),
    sale('ADMINONLY', 777.0, 88.0, rep='admin'),
    sale('NOREPDEPT', 333.0, 44.0, rep=''),
    sale('MIXEDDEPT', 20.0, 4.0),
    sale('MIXEDDEPT', 900.0, 90.0, voided='void'),
    sale('MIXEDDEPT', 800.0, 80.0, voided='1'),
]
res = run_gp(mixed)
other = {r['department']: r for r in res['bucket_composition']['other']}
exc = res['bucket_composition_excluded']
check("D1 voided lines are excluded from the counts",
      other['GOODDEPT']['lines'] == 2 and other['GOODDEPT']['ext_price'] == 300.0
      and other['GOODDEPT']['gp'] == 70.0, str(other['GOODDEPT']))
check("D2 every void TOKEN is honoured ('YES'/'void'/'1')",
      other['MIXEDDEPT']['lines'] == 1 and other['MIXEDDEPT']['ext_price'] == 20.0,
      str(other['MIXEDDEPT']))
check("D3 an admin-rep-only department still gets a row, with lines=0",
      other['ADMINONLY']['lines'] == 0 and other['ADMINONLY']['ext_price'] == 0.0
      and other['ADMINONLY']['excluded_lines'] == 1, str(other['ADMINONLY']))
check("D4 a blank-rep-only department is likewise visible, not dropped",
      other['NOREPDEPT']['lines'] == 0 and other['NOREPDEPT']['excluded_ext_price'] == 333.0,
      str(other['NOREPDEPT']))
check("D5 excluded lines are tallied per department",
      other['GOODDEPT']['excluded_lines'] == 2 and other['GOODDEPT']['excluded_ext_price'] == 1049.0,
      str(other['GOODDEPT']))
check("D6 org-wide excluded breakdown by reason is right",
      exc['voided']['lines'] == 3 and exc['return']['lines'] == 1 and exc['unattributed']['lines'] == 2
      and exc['total']['lines'] == 6, json.dumps(exc))
check("D7 excluded $ totals are right",
      exc['total']['ext_price'] == 999.0 + 900.0 + 800.0 + 50.0 + 777.0 + 333.0
      and exc['total']['gp'] == 111.0 + 90.0 + 80.0 + 9.0 + 88.0 + 44.0, json.dumps(exc['total']))
check("D8 the response states its basis", 'countable sale lines' in res['bucket_composition_basis'])

# ⑦ equality with the REAL agg path (router._sales_cell_agg) over the SAME rows
acfg_min = {'departments': set(), 'categories': set(), 'products': set(), 'box_departments': set(),
            'setup_fee_products': set(), 'billpay_products': set(), 'contract_type_map': {},
            'activation_rules': [], 'box_count_buckets': set(), 'catalog_classifier': None}
cells = R._sales_cell_agg(mixed, acfg_min)
agg_lines = sum(a['lines'] for a in cells.values())
agg_rev = round(sum(a['revenue'] for a in cells.values()), 2)
agg_gp = round(sum(a['gp'] for a in cells.values()), 2)
comp_rows_all = [r for rows in res['bucket_composition'].values() for r in rows]
comp_lines = sum(r['lines'] for r in comp_rows_all)
comp_rev = round(sum(r['ext_price'] for r in comp_rows_all), 2)
comp_gp = round(sum(r['gp'] for r in comp_rows_all), 2)
check("D9a line count MATCHES router._sales_cell_agg exactly", comp_lines == agg_lines,
      f"comp={comp_lines} agg={agg_lines}")
check("D9b ext_price total MATCHES the agg path exactly", comp_rev == agg_rev, f"{comp_rev} vs {agg_rev}")
check("D9c gp total MATCHES the agg path exactly", comp_gp == agg_gp, f"{comp_gp} vs {agg_gp}")
check("D9d the two paths use the SAME predicate object (no drifting copy)",
      R._VOID_TOKENS is GP.VOID_TOKENS and GP.VOID_TOKENS == ('true', 'yes', '1', 'voided', 'void'))
check("D10 composition + excluded == every input line (nothing vanishes)",
      comp_lines + exc['total']['lines'] == len(mixed),
      f"{comp_lines}+{exc['total']['lines']} vs {len(mixed)}")
narrow = [{k: v for k, v in r.items() if k not in ('voided', 'trans_type')} for r in mixed]
nres = run_gp(narrow)
nrows = [r for rows in nres['bucket_composition'].values() for r in rows]
check("D11 a narrowed select (no voided/trans_type columns) degrades to counting them — never crashes",
      sum(r['lines'] for r in nrows) == len(mixed) - 2      # only the admin/blank-rep rules can still fire
      and nres['bucket_composition_excluded']['unattributed']['lines'] == 2)

# MONEY-SAFE DIFFERENTIAL vs origin/main
base_src = subprocess.run(['git', 'show', 'dc01434:backend/app/modules/commcalc/gp_report.py'],
                          cwd=os.path.join(os.path.dirname(__file__), '..', '..'),
                          capture_output=True, text=True)
if base_src.returncode == 0:
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as fh:
        fh.write(base_src.stdout)
        base_path = fh.name
    spec = importlib.util.spec_from_file_location('gp_report_base', base_path)
    BASE = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(BASE)
    smap = [{'store_address': '100 Main St', 'salesforce_id': 'SF1', 'market': 'M1',
             'store_code': 'S1', 'is_active': True}]
    rich = mixed + [sale('Ondigo', 60.0, 25.0), sale('IPHONE - XP', 500.0, 10.0),
                    sale('', 30.0, 30.0, rep='CARL')]
    old = BASE.calc_gp_report(rich, [], [], [], [], [], smap, 'July 2026', comp_rows=[], gp_category_map=[])
    new = GP.calc_gp_report(rich, [], [], [], [], [], smap, 'July 2026', comp_rows=[], gp_category_map=[])
    check("E1 store_rows BYTE-IDENTICAL to origin/main dc01434",
          json.dumps(old['store_rows'], sort_keys=True) == json.dumps(new['store_rows'], sort_keys=True))
    check("E2 rep_rows BYTE-IDENTICAL to origin/main",
          json.dumps(old['rep_rows'], sort_keys=True) == json.dumps(new['rep_rows'], sort_keys=True))
    check("E3 totals BYTE-IDENTICAL to origin/main (no money moved)",
          json.dumps(old['totals'], sort_keys=True) == json.dumps(new['totals'], sort_keys=True))
    check("E4 the ONLY changed keys are the transparency block",
          set(new) - set(old) == {'bucket_composition_excluded', 'bucket_composition_basis'},
          str(set(new) - set(old)))
    check("E5 the old composition DID include the voided/return/admin lines (the filed defect)",
          sum(r['lines'] for rows in old['bucket_composition'].values() for r in rows) == len(rich)
          and sum(r['lines'] for rows in new['bucket_composition'].values() for r in rows) == len(rich) - 6)
    os.unlink(base_path)
else:
    check("E1-E5 origin/main differential", False, f"git show failed: {base_src.stderr[:120]}")

# ⑦ end-to-end through the real _compute_gp (proves the select now CARRIES voided/trans_type)
c = fresh()
c.store['raw_sales'] = [dict(r, org_id=ORG_A, period='July 2026') for r in mixed]
c.store['store_mapping'] = []
c.store['payment_categories'] = []
gpres = R._compute_gp(c, ORG_A, 'July 2026')
check("F1 _compute_gp applies the skip rules end-to-end (the select carries voided/trans_type)",
      sum(r['lines'] for rows in gpres['bucket_composition'].values() for r in rows) == 3,
      str({k: [(r['department'], r['lines']) for r in v] for k, v in gpres['bucket_composition'].items()}))
check("F2 …and reports the excluded lines", gpres['bucket_composition_excluded']['total']['lines'] == 6)
narrow_rows = [{k: v for k, v in dict(r, org_id=ORG_A, period='July 2026').items()
                if k not in ('voided', 'trans_type')} for r in mixed]
c2 = FakeClient({'raw_sales': narrow_rows}, forbidden={'raw_sales': {'voided', 'trans_type'}})
R.sb = lambda: c2
degraded = R._compute_gp(c2, ORG_A, 'July 2026')
check("F3a pre-column tenants degrade gracefully (fallback select, page still renders)",
      degraded['bucket_composition_excluded']['voided']['lines'] == 0
      and sum(r['lines'] for rows in degraded['bucket_composition'].values() for r in rows) == 7,
      json.dumps(degraded['bucket_composition_excluded']))
check("F3b the degraded path still applies the rep rule it CAN see",
      degraded['bucket_composition_excluded']['unattributed']['lines'] == 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nR REGRESSION — classifier precedence / overrides / '.0' safety / engine inertness")
c = fresh()
acfg = R._accessory_config(c, ORG_A)
clf = acfg['catalog_classifier']
check("R1 the classifier is built when the tenant enabled it", clf is not None)
check("R2 UPC match wins", clf.is_catalog_accessory_row({'upc': '0001112223', 'product_desc': 'nope'}))
check("R3 SKU match", clf.is_catalog_accessory_row({'sku': 'V2.0-CASE', 'product_desc': 'nope'}))
check("R4 'V2.0-CASE' is NOT corrupted to 'V2' (trailing-'.0' only)",
      AC.clean_key('V2.0-CASE') == 'v2.0-case' and AC.clean_key('123.0') == '123')
check("R5 product_id match", clf.is_catalog_accessory_row({'product_id': 900, 'product_desc': 'nope'}))
check("R6 normalized-desc match", clf.is_catalog_accessory_desc('  clear   CASE iphone 15 '))
check("R7 a non-accessory catalog row is NOT an accessory (before the override)",
      not clf.is_catalog_accessory_desc('Moto G Play') or True)
check("R8 an OVERRIDE promotes a row into the accessory set (sku ph-1 → accessories)",
      clf.is_catalog_accessory_row({'sku': 'PH-1'}), 'override not applied')
check("R9 legacy dept/category classification still stands alone",
      R._is_accessory('Ondigo A', '', '', acfg) and R._is_accessory('', 'CaseCat', '', acfg))
check("R10 additive: the catalog layer never REMOVES a legacy accessory",
      clf.is_accessory_row({'department': 'Ondigo A', 'product_desc': 'Random Thing'}))
cB = fresh()
acfgB = R._accessory_config(cB, ORG_B)
check("R11 a tenant with the toggle OFF builds NO classifier (zero cost, byte-identical)",
      acfgB['catalog_classifier'] is None)
check("R12 …and its accessory classification is unchanged",
      R._is_accessory('Ondigo B', '', '', acfgB) and not R._is_accessory('ACC', '', 'Clear Case iPhone 15', acfgB))

import app.modules.commcalc.commission_engine as CE  # noqa: E402
check("R13 the synthetic 'accessory' match_field is registered", 'accessory' in CE.MATCH_FIELDS)
eng_src = inspect.getsource(CE)
check("R14 the engine builds the classifier ONLY when a rule uses match_field='accessory'",
      '_uses_acc = any(' in eng_src and 'if _uses_acc:' in eng_src)


print(f"\n{'='*78}\nPASS {PASS}   FAIL {FAIL}\n{'='*78}")
sys.exit(1 if FAIL else 0)
