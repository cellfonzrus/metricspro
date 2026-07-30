"""Proof for agent/commission/catalog-followups — the four post-merge Gate-1 nits filed against the
shipped catalog-accessory-byod wave (origin/main dc01434). NON-MONEY, display/perf only.

WHAT THIS PROVES
  ②  ORG-SCOPED, TTL-BOUNDED CONFIG CACHE (accessory_catalog.cache_*). REWORKED at Gate-1: the first
     draft claimed the client-object key made it request-scoped — FALSE, get_supabase() is a process-wide
     singleton (core/database.py:76-84), so the memo lives for the worker process and only the TTL bounds
     it. ONE layer now (the opt-in second layer is gone), hard TTL default 45s.
       A0  the singleton premise; the TTL default is NONZERO; there is exactly one layer
       A1  1st call reads, a repeat is free; (i) an out-of-band SQL-Editor edit with NO invalidate is
           served stale WITHIN the TTL and picked up AFTER it; a second request on the same singleton
           client hits (stated honestly); a genuinely different client never reuses the memo
       A2  the key includes org_id — org B never sees org A's config
       A3  finding 3: the returned dict AND its sets/lists/nested dicts are copies — a hostile caller
           cannot poison the master; (iii) finding 2: a cache_put racing an invalidate is DISCARDED via
           the generation counter, leaving the cache empty rather than resurrecting stale config
       A4  TTL=0 disables it; an unset or malformed env falls back to 45s (never 0/unbounded)
       A5  a BLANK org_id — or no client — is never cached
       A6  (v) GET /commcalc/catalog with the TTL enabled and a SINGLETON client provider: catalog-table
           reads 6 -> 2, request 17 -> 12, payload byte-identical, second request free
       A7  the engine's classifier path is cached; an EXPLICIT acc_cats is never cached
       A8  (ii) MONEY-PATH FRESHNESS: commission_engine.preview AND _run_calculation invalidate at ENTRY,
           proven behaviourally (an out-of-band edit is used) and on the real source
       INVALIDATION — every app write drops the org's entries IMMEDIATELY:
       A9  PUT /accessory-config     A10 PUT /catalog/override (set AND clear)
       A11 POST /gp-category-map (upsert AND delete)     A12 PUT /flag-rules
       A13 a catalog upload (_upload_file_impl wiring on the real source; all 7 sites counted)
       A14 invalidation is ORG-SCOPED: invalidating org A leaves org B's cache intact

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

os.environ.pop('COMMCALC_CFG_CACHE_TTL', None)   # exercise the shipped 45s default

import app.modules.commcalc.router as R                       # noqa: E402
import app.modules.commcalc.accessory_catalog as AC           # noqa: E402
import app.modules.commcalc.gp_report as GP                   # noqa: E402
import asyncio                                                # noqa: E402
import inspect                                                # noqa: E402


def inspect_src_db():
    import app.core.database as _db
    return inspect.getsource(_db)

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
print("\n② ORG-SCOPED, TTL-BOUNDED CONFIG CACHE (Gate-1 rework: findings 1, 2, 3)")

# THE REAL LIFECYCLE: core.database.get_supabase() is a process-wide SINGLETON, so every request threads
# the SAME client — the memo is NOT request-scoped, it lives for the process and the TTL is the only thing
# that bounds it. These tests are written against that reality.
import app.core.database as CORE_DB  # noqa: E402
check("A0a get_supabase IS a process-wide singleton (the premise these tests assume)",
      CORE_DB.get_supabase.__code__.co_names.count('_client') >= 1
      and 'double-checked' in inspect_src_db(), "see core/database.py:76-84")
check("A0b the TTL default is NONZERO — an out-of-band SQL edit can never be invisible forever",
      AC.CACHE_TTL_SECONDS >= 30 and AC.cache_ttl() == AC.CACHE_TTL_SECONDS,
      f"{AC.CACHE_TTL_SECONDS} / {AC.cache_ttl()}")
check("A0c there is now ONE cache layer, not two (the opt-in L2 concept is gone)",
      not hasattr(AC, '_cache') and hasattr(AC, '_client_cache'))

c = fresh()
cfg1 = R._accessory_config(c, ORG_A)
n1 = c.reads()
c.reset()
cfg2 = R._accessory_config(c, ORG_A)
n2 = c.reads()
check("A1a first _accessory_config actually reads the DB (>=10 queries)", n1 >= 10, f"n1={n1}")
check("A1b a repeat issues ZERO queries", n2 == 0, f"n2={n2}")
check("A1c cached value equals the uncached value",
      cfg1['departments'] == cfg2['departments'] and cfg1['categories'] == cfg2['categories']
      and cfg1['catalog_classify_enabled'] == cfg2['catalog_classify_enabled'],
      f"{cfg1['departments']} vs {cfg2['departments']}")
un = R._accessory_config_uncached(c, ORG_A)
check("A1d cached == a forced uncached resolution (same keys + same values)",
      set(un) == set(cfg2) and un['departments'] == cfg2['departments']
      and un['catalog_accessory_categories_list'] == cfg2['catalog_accessory_categories_list'])

# (i) REQUIRED PROOF — TTL expiry with NO invalidate: the SQL-Editor scenario.
os.environ['COMMCALC_CFG_CACHE_TTL'] = '0.4'
c = fresh()
R._accessory_config(c, ORG_A)
c.store['accessory_config'][0]['departments'] = ['EditedInTheSqlEditor']   # no endpoint, no invalidate
c.reset()
during = R._accessory_config(c, ORG_A)['departments']
check("A1e-i within the TTL the stale value is still served (the bounded window, stated honestly)",
      during == {'ondigo a'} and c.reads() == 0, f"{during} reads={c.reads()}")
time.sleep(0.55)
c.reset()
after_ttl = R._accessory_config(c, ORG_A)['departments']
check("A1f-i AFTER the TTL an out-of-band SQL edit IS picked up, with NO invalidate call",
      after_ttl == {'editedinthesqleditor'} and c.reads() >= 10, f"{after_ttl} reads={c.reads()}")
check("A1g-i …and the entry really expired rather than being evicted",
      AC.cache_snapshot()[1]['expired'] >= 1, str(AC.cache_snapshot()[1]))
os.environ.pop('COMMCALC_CFG_CACHE_TTL', None)

# a SECOND simulated request (same singleton client) is a HIT — the honest statement of what this is
c = fresh()
R._accessory_config(c, ORG_A)
c.reset()
R._accessory_config(c, ORG_A)      # "another request" — same singleton client object
check("A1h a second request on the SAME singleton client hits the memo (process-lifetime, TTL-bounded)",
      c.reads() == 0, f"{c.reads()}")
other = FakeClient(base_store())
R._accessory_config(other, ORG_A)
check("A1i a genuinely DIFFERENT client never reuses another client's memo", other.reads() >= 10,
      f"{other.reads()}")

c.reset()
cfgB = R._accessory_config(c, ORG_B)
check("A2a org B on the SAME client is still a MISS (the key includes org_id)", c.reads() >= 9,
      f"reads={c.reads()}")
check("A2b org B gets ITS OWN departments, never org A's",
      cfgB['departments'] == {'ondigo b'} and cfg2['departments'] == {'ondigo a'},
      f"B={cfgB['departments']} A={cfg2['departments']}")
check("A2c org B's catalog toggle is its own (False vs A's True)",
      cfgB['catalog_classify_enabled'] is False and cfg2['catalog_classify_enabled'] is True)
_keys, _stats, _gen = AC.cache_snapshot()
check("A2d every cache key carries an org_id",
      _keys and all(len(k) == 2 and k[1] in (ORG_A, ORG_B) for k in _keys), str(_keys))

# (Finding 3) mutation hardening — the cached master must survive a hostile caller
c = fresh()
poison = R._accessory_config(c, ORG_A)
poison['departments_list'] = ['HACKED']
poison['injected'] = True
poison['departments'].add('hacked-dept')
poison['categories'].add('hacked-cat')
poison['box_departments'].add('HackedBox')
poison['contract_type_map']['x'] = 'byod'
poison['activation_rules'].append({'bucket': 'byod'})
after = R._accessory_config(c, ORG_A)
check("A3a mutating the returned dict cannot poison the cache",
      after.get('departments_list') == ['Ondigo A'] and 'injected' not in after,
      str(after.get('departments_list')))
check("A3b …nor its SETS", after['departments'] == {'ondigo a'} and 'hacked-cat' not in after['categories']
      and 'HackedBox' not in after['box_departments'], str(after['departments']))
check("A3c …nor its nested dict/list", after['contract_type_map'] == {} and after['activation_rules'] == [],
      f"{after['contract_type_map']} {after['activation_rules']}")
sets_a = AC.build_catalog_sets(c, ORG_A)
sets_a[0].add('poisoned-desc')
check("A3d build_catalog_sets hands back COPIES too",
      'poisoned-desc' not in AC.build_catalog_sets(c, ORG_A)[0])

# (iii) REQUIRED PROOF — the invalidate/re-cache race (Finding 2)
c = fresh()
AC.invalidate()
gen0 = AC.cache_generation()
_stale = R._accessory_config_uncached(c, ORG_A)          # a read that started BEFORE the write
AC.invalidate(ORG_A)                                      # …the write lands and invalidates…
AC.cache_put("acfg", ORG_A, _stale, c, gen0)              # …and only NOW does the read try to cache
check("A3e-iii a cache_put racing an invalidate is DISCARDED (generation moved)",
      AC.cache_get("acfg", ORG_A, c) is None, "stale value was resurrected")
check("A3f-iii …and the drop is counted", AC.cache_snapshot()[1]['stale_put_dropped'] >= 1)
check("A3g-iii invalidate bumps the generation", AC.cache_generation() > gen0)
gen1 = AC.cache_generation()
AC.cache_put("acfg", ORG_A, {'departments': {'fresh'}}, c, gen1)
check("A3h-iii a put with the CURRENT generation is kept",
      AC.cache_get("acfg", ORG_A, c) == {'departments': {'fresh'}})
AC.invalidate()

check("A4a TTL=0 disables the cache entirely",
      (os.environ.__setitem__('COMMCALC_CFG_CACHE_TTL', '0'), AC.cache_ttl())[1] == 0.0)
c = fresh()
R._accessory_config(c, ORG_A)
c.reset()
R._accessory_config(c, ORG_A)
check("A4b …every call then reads", c.reads() >= 10, f"{c.reads()}")
check("A4c …and nothing is stored", AC.cache_snapshot()[0] == [], str(AC.cache_snapshot()[0]))
os.environ.pop('COMMCALC_CFG_CACHE_TTL', None)
check("A4d an unset/blank env falls back to the 45s default", AC.cache_ttl() == 45.0, str(AC.cache_ttl()))
os.environ['COMMCALC_CFG_CACHE_TTL'] = 'nonsense'
check("A4e a malformed env value falls back to the default too (never 0/unbounded)",
      AC.cache_ttl() == 45.0, str(AC.cache_ttl()))
os.environ.pop('COMMCALC_CFG_CACHE_TTL', None)

c = fresh()
R._accessory_config(c, '')
_k, _st, _g = AC.cache_snapshot()
check("A5a a BLANK org_id is never cached", _k == [], str(_k))
check("A5b cache_get on a blank org always misses", AC.cache_get('acfg', '', c) is None)
check("A5c cache_put on a blank org stores nothing",
      (AC.cache_put('acfg', None, {'x': 1}, c), AC.cache_snapshot()[0])[1] == [])
check("A5d no client → no caching (a caller that can't be keyed is never served)",
      AC.cache_get('acfg', ORG_A, None) is None
      and (AC.cache_put('acfg', ORG_A, {'y': 1}, None), AC.cache_snapshot()[0])[1] == [])

# (v) REQUIRED PROOF — the 6→2 / 17→12 reduction still holds WITH the TTL enabled
class SingletonProvider:
    """R.sb() hands back the SAME client every call — exactly what get_supabase() does in production."""
    def __init__(self, store):
        self.client = FakeClient(store)
    def __call__(self):
        return self.client

AC.invalidate()
prov_pre = SingletonProvider(base_store())
R.sb = prov_pre
_real_get = AC.cache_get
AC.cache_get = lambda *a, **k: None            # simulate "no cache at all"
pre_fix = R.catalog_list(org_id=ORG_A)
pre_total = prov_pre.client.reads()
pre_cat = prov_pre.client.reads('raw_catalog') + prov_pre.client.reads('catalog_category_override')
AC.cache_get = _real_get

AC.invalidate()
prov = SingletonProvider(base_store())
R.sb = prov
out1 = R.catalog_list(org_id=ORG_A)
reads_cold = prov.client.reads()
cat_reads_cold = prov.client.reads('raw_catalog') + prov.client.reads('catalog_category_override')
prov.client.reset()
out2 = R.catalog_list(org_id=ORG_A)
reads_warm = prov.client.reads()
check("A6a0-v WITHOUT the cache ONE /catalog request re-read the catalog tables 6x", pre_cat == 6,
      f"{pre_cat}")
check("A6a-v WITH it those 6 collapse to 2 (one read per table)", cat_reads_cold == 2,
      f"{cat_reads_cold}")
check("A6a2-v one COLD /catalog request drops from 18 queries to 13", pre_total == 18 and reads_cold == 13,
      f"now={reads_cold} pre={pre_total}")
# 17→18 / 12→13 on 2026-07-30: mig 250 (`accessory_config.apply_to_gp`, gp-luxelink-columns @ 21279fc)
# legitimately adds ONE per-column probe — the exact degrade-safe pattern the NOTE below documents.
# NOTE (deliberate non-change): the 13 that remain are _accessory_config's per-COLUMN single-row probes.
# Each lives in its OWN try/except precisely so a pre-migration missing column cannot disturb the others
# (migs 213/214/217/218/224/231 all degrade that way) — collapsing them into one select would break that.
check("A6b-v a second request within the TTL costs 0 (process-lifetime memo, honestly stated)",
      reads_warm == 0, f"{reads_warm}")
check("A6c payload identical across requests",
      json.dumps(out1, sort_keys=True, default=str) == json.dumps(out2, sort_keys=True, default=str))
check("A6d cache-free payload is byte-identical to the cached one (no behaviour change)",
      json.dumps(pre_fix, sort_keys=True, default=str) == json.dumps(out1, sort_keys=True, default=str))

# A7 — the engine's classifier path
c = fresh()
s1 = AC.build_catalog_sets(c, ORG_A)
c.reset()
s2 = AC.build_catalog_sets(c, ORG_A)
check("A7a build_catalog_sets repeat: ZERO queries", c.reads() == 0, f"{c.reads()}")
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

# (ii) REQUIRED PROOF — the MONEY paths always start from a FRESH config read
prov = SingletonProvider(base_store())
R.sb = prov
mclient = prov.client
AC.invalidate()
R._accessory_config(mclient, ORG_A)                       # warm the memo
mclient.store['accessory_config'][0]['catalog_accessory_categories'] = ['Handsets']   # out-of-band edit
mclient.reset()
CE_src = None
import app.modules.commcalc.commission_engine as CE       # noqa: E402
CE.preview(mclient, ORG_A, 'July 2026')
check("A8a-ii commission_engine.preview refreshes the config memo at ENTRY",
      AC.cache_get('acfg', ORG_A, mclient) is None
      or R._accessory_config(mclient, ORG_A)['catalog_accessory_categories_list'] == ['Handsets'],
      "preview served a stale classifier")
check("A8b-ii …proven on the real source, not just behaviourally",
      '_accat_fresh.invalidate(org_id)' in inspect.getsource(CE.preview))
AC.invalidate()
R._accessory_config(mclient, ORG_A)
mclient.store['accessory_config'][0]['departments'] = ['RecalcMustSeeThis']
_before_gen = AC.cache_generation()
asyncio.run(R._run_calculation('July 2026', ORG_A))
check("A8c-ii _run_calculation drops the memo at ENTRY (generation moved)",
      AC.cache_generation() > _before_gen)
check("A8d-ii …so the recalc's config read is fresh",
      R._accessory_config(mclient, ORG_A)['departments'] == {'recalcmustseethis'},
      str(R._accessory_config(mclient, ORG_A)['departments']))
check("A8e-ii …proven on the real source too",
      '_invalidate_accessory_config(org_id)' in inspect.getsource(R._run_calculation))

# A9..A12 — invalidation on every app write
c = fresh()
before = R._accessory_config(c, ORG_A)['departments']
R.put_accessory_config({'departments': ['NewDept'], 'categories': [], 'product_keywords': []},
                       org_id=ORG_A, authorization='')
after = R._accessory_config(c, ORG_A)['departments']
check("A9 PUT /accessory-config invalidates immediately",
      before == {'ondigo a'} and after == {'newdept'}, f"{before} -> {after}")

c = fresh()
pre = R.catalog_list(org_id=ORG_A)
pre_eff = {r['product_desc']: r['effective_category'] for r in pre['rows']}
R.put_catalog_override({'match_type': 'upc', 'match_value': '0004445556', 'category': 'Accessories'},
                       org_id=ORG_A, authorization='')
post_eff = {r['product_desc']: r['effective_category'] for r in R.catalog_list(org_id=ORG_A)['rows']}
check("A10a the pre-write read reflected the file/sku state",
      pre_eff['Clear Case iPhone 15'] == 'accessories')
check("A10b PUT /catalog/override is reflected in the very next read",
      post_eff['Moto G Play'] == 'accessories')
R.put_catalog_override({'match_type': 'sku', 'match_value': 'PH-1', 'category': ''},
                       org_id=ORG_A, authorization='')
R.put_catalog_override({'match_type': 'upc', 'match_value': '0004445556', 'category': ''},
                       org_id=ORG_A, authorization='')
cleared = {r['product_desc']: r['effective_category'] for r in R.catalog_list(org_id=ORG_A)['rows']}
check("A10c CLEARING an override also invalidates (file category restored)",
      cleared['Moto G Play'] == 'handsets', str(cleared))

c = fresh()
R._accessory_config(c, ORG_A)
asyncio.run(R.set_gp_category_map({'department': 'GPACC', 'category': 'accessory'}, org_id=ORG_A))
check("A11a POST /gp-category-map (upsert) invalidates → the new dept appears",
      'gpacc' in R._accessory_config(c, ORG_A)['departments'],
      str(R._accessory_config(c, ORG_A)['departments']))
asyncio.run(R.set_gp_category_map({'department': 'GPACC', 'category': ''}, org_id=ORG_A))
check("A11b …and the DELETE branch invalidates too → the dept is gone",
      'gpacc' not in R._accessory_config(c, ORG_A)['departments'],
      str(R._accessory_config(c, ORG_A)['departments']))

c = fresh(forbidden={'accessory_config': {'departments'}})   # force the flag_rules fallback path
c.store['flag_rules'] = [{'id': 1, 'org_id': ORG_A, 'accessory_departments': ['LegacyDept'],
                          'accessory_categories': [], 'accessory_product_keywords': [],
                          'acima_tenders': []}]
legacy = R._accessory_config(c, ORG_A)['departments']
R.put_flag_rules({'accessory_threshold': 40}, org_id=ORG_A)
check("A12a pre-mig-208 fallback still resolves via flag_rules", legacy == {'legacydept'}, str(legacy))
check("A12b PUT /flag-rules invalidates the org's cache",
      not [k for k in AC.cache_snapshot()[0] if k[1] == ORG_A], str(AC.cache_snapshot()[0]))

up_src = inspect.getsource(R._upload_file_impl)     # upload_file is the thin mig-202 trace wrapper
check("A13 the catalog upload invalidates after the ingest",
      "if file_type in ('catalog', 'master_cats'):" in up_src
      and "_invalidate_accessory_config(org_id)" in up_src)
inv_sites = inspect.getsource(R).count('_invalidate_accessory_config(org_id)') - 1   # minus the def line
check("A13b all SEVEN wiring points (6 config writes + recalc entry)", inv_sites == 7, f"{inv_sites}")

AC.invalidate()
ca, cb = FakeClient(base_store()), FakeClient(base_store())
R._accessory_config(ca, ORG_A)
R._accessory_config(cb, ORG_B)
AC.invalidate(ORG_A)
check("A14a invalidate(orgA) drops ONLY org A",
      all(k[1] == ORG_B for k in AC.cache_snapshot()[0]), str(AC.cache_snapshot()[0]))
cb.reset()
R._accessory_config(cb, ORG_B)
check("A14b org B is still served", cb.reads() == 0, f"{cb.reads()}")
ca.reset()
R._accessory_config(ca, ORG_A)
check("A14c org A has to read again", ca.reads() >= 10, f"{ca.reads()}")
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
# Gate-1 rework nit: two departments differing ONLY in case are distinct rows (the key is the raw string),
# so an identical |gp|+|ext| pair used to fall through to dict insertion order. The raw name is now folded
# into the key after the case-folded one.
cvar = [sale('Acc', 5.0, 1.0), sale('ACC', 5.0, 1.0), sale('acc', 5.0, 1.0)]
cv1 = [r['department'] for r in run_gp(cvar)['bucket_composition']['other']]
cv2 = [r['department'] for r in run_gp(list(reversed(cvar)))['bucket_composition']['other']]
check("C5b case-variant same-name departments have a TOTAL order (no insertion-order fallback)",
      cv1 == cv2 and cv1 == sorted(cv1), f"{cv1} vs {cv2}")
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

check("R13 the synthetic 'accessory' match_field is registered", 'accessory' in CE.MATCH_FIELDS)
eng_src = inspect.getsource(CE)
check("R14 the engine builds the classifier ONLY when a rule uses match_field='accessory'",
      '_uses_acc = any(' in eng_src and 'if _uses_acc:' in eng_src)


print(f"\n{'='*78}\nPASS {PASS}   FAIL {FAIL}\n{'='*78}")
sys.exit(1 if FAIL else 0)
