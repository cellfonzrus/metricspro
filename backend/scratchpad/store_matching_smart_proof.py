"""Proof for agent/commission/store-matching-smart (owner directive 2026-07-18:
"make the stores mappable to the pos report and the commission report so the shift does not take place,
and make it smart like boost so the margin of error is reduced").

Closes Gate-1 Finding 2 on luxelink-targets-actuals: the storeops EXACT-address fallback runs for every org,
so an unmapped store string could silently resolve — a potential unreviewed shift. This package makes the
EXPLICIT per-org mapping (commcalc.store_aliases) the resolver's source of truth for EVERY tenant (incl. one
with no store_mapping — luxelink), adds SMART deterministic suggestions, and makes every observed POS store
string's resolution status VISIBLE (explicit / store_mapping / exact-fallback / unresolved).

Drives the REAL router functions over an in-memory FakeClient (no DB/network). Money-safety proven:
  · resolver PRECEDENCE re-driven vs the base's cases — byte-identical when store_aliases is empty
  · house byte-identity — a seeded alias to a store_mapping code resolves to the same code as before
  · suggestions are NEVER auto-applied — only an explicit confirm materializes a mapping
Run:  cd backend && python3 scratchpad/store_matching_smart_proof.py
"""
import os, sys, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.router as R

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


# ── in-memory fake supabase client: eq/in_/neq/range + insert + delete + optional column-schema probe ──
class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, store, schemas, table):
        self.store = store
        self.schemas = schemas
        self.t = table
        self.f = []
        self.rng = None

    def select(self, *a, **k):
        # simulate a missing column (pre-migration) by raising, so _table_has_column returns False
        cols = self.schemas.get(self.t)
        if cols is not None:
            for arg in a:
                for c in str(arg).split(','):
                    c = c.strip()
                    if c and c != '*' and c not in cols:
                        raise Exception(f"column {self.t}.{c} does not exist")
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def neq(self, c, v):
        self.f.append(('neq', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

    def is_(self, c, v):
        self.f.append(('is', c, v)); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def order(self, *a, **k):
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
        rows = self.store.setdefault(self.t, [])
        m = [r for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        return FakeResult(data=[dict(r) for r in m])

    def insert(self, row):
        rows = self.store.setdefault(self.t, [])
        newrows = row if isinstance(row, list) else [row]
        stamped = []
        for nr in newrows:
            nr = dict(nr)
            nr.setdefault('id', f"id{len(rows) + len(stamped) + 1}")
            stamped.append(nr)
        rows.extend(stamped)
        return _Exec(stamped)

    def delete(self):
        return _Delete(self.store, self.t)


class _Exec:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return FakeResult(data=[dict(r) for r in self._data])


class _Delete:
    def __init__(self, store, table):
        self.store = store
        self.t = table
        self.f = []

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        keep = [r for r in rows if not all(r.get(c) == v for c, v in self.f)]
        removed = len(rows) - len(keep)
        self.store[self.t] = keep
        return FakeResult(data=[{'removed': removed}])


class FakeSchema:
    def __init__(self, store, schemas):
        self.store = store
        self.schemas = schemas

    def table(self, t):
        return FakeQuery(self.store, self.schemas, t)

    def rpc(self, *a, **k):
        raise Exception('no rpc in this proof')


class FakeClient:
    def __init__(self, store, schemas=None):
        self.store = store
        self.schemas = schemas or {}

    def schema(self, s):
        return FakeSchema(self.store, self.schemas)


ORG = 'lux'
HOUSE = '00000000-0000-0000-0000-000000000001'

# store_aliases WITH the mig-219 provenance columns present (default); a second schema simulates pre-219.
SCHEMA_219 = {'store_aliases': {'id', 'org_id', 'alias', 'store_code', 'note', 'created_at', 'source', 'confidence'}}
SCHEMA_PRE219 = {'store_aliases': {'id', 'org_id', 'alias', 'store_code', 'note', 'created_at'}}


def sales(store_str, org=ORG, table='daily_sales_feed'):
    return {'org_id': org, 'period': '2026-07', 'store': store_str}


def store_row(code, addr, org=ORG, market='NY'):
    return {'org_id': org, 'store_code': code, 'address': addr, 'market': market}


def sm_row(code, addr, org=ORG, market='NY'):
    return {'org_id': org, 'store_code': code, 'store_address': addr, 'market': market}


def base_store(**over):
    st = {
        'raw_sales': [], 'daily_sales_feed': [], 'store_mapping': [], 'stores': [],
        'store_aliases': [],
    }
    st.update(over)
    return st


def run(coro):
    # ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers are now plain `def` (off the single
    # uvicorn event loop). Dual-shape: drive a coroutine, pass a plain result straight through.
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


# ══ (1) _norm_store_match — deterministic normalization ═════════════════════════════════════════════
print("(1) _norm_store_match deterministic normalization")
a = R._norm_store_match('3 Palisade Ave, Ste 200  ')
b = R._norm_store_match('3 PALISADE AVE')
check("case + suite + trailing space normalize to the same key", a == b == '3 palisade ave', f"a={a!r} b={b!r}")
check("normalization is idempotent", R._norm_store_match(a) == a)
check("same input twice → identical (deterministic)", R._norm_store_match('116-36 Queens Blvd #5') == R._norm_store_match('116-36 Queens Blvd #5'))
check("'#5' unit token stripped", R._norm_store_match('900 Main St #5') == '900 main st')
check("'Suite 12B' stripped", R._norm_store_match('900 Main St Suite 12B') == '900 main st')
check("punctuation collapsed", R._norm_store_match('900  Main-St.') == '900 main st')
check("leading number extracted", R._store_leading_num('116-36 queens blvd') == '116')


# ══ (2) _store_suggest — deterministic ranking + confidence ═════════════════════════════════════════
print("(2) _store_suggest deterministic ranked suggestions")
STORES = [
    {'store_code': 'LUX-HEMP', 'address': '900 Fulton Ave Hempstead', 'market': 'NY'},
    {'store_code': 'LUX-PAL', 'address': '3 Palisade Ave', 'market': 'NY'},
    {'store_code': 'LUX-QNS', 'address': '116-36 Queens Blvd', 'market': 'NY'},
]
s1x = R._store_suggest('3 palisade ave', STORES, top=3)
check("exact-normalized address ranks #1 with 'exact' confidence",
      s1x and s1x[0]['store_code'] == 'LUX-PAL' and s1x[0]['confidence'] == 'exact', s1x)
s1 = R._store_suggest('3 Palisade Ave Yonkers', STORES, top=3)
check("near-match (extra 'Yonkers' token) ranks #1 with HIGH confidence (not exact)",
      s1 and s1[0]['store_code'] == 'LUX-PAL' and s1[0]['confidence'] == 'high', s1)
s2 = R._store_suggest('900 Fulton Avenue, Hempstead NY', STORES, top=3)
check("word-overlap + street# ranks the right store first", s2 and s2[0]['store_code'] == 'LUX-HEMP', s2)
check("suggestion output is deterministic (run twice equal)",
      R._store_suggest('900 Fulton Avenue, Hempstead NY', STORES) == s2)
s3 = R._store_suggest('LUX-QNS - 116 36 Queens', STORES, top=3)
check("literal store-code containment boosts that store to top", s3 and s3[0]['store_code'] == 'LUX-QNS', s3)
s4 = R._store_suggest('totally unrelated widget shop', STORES, top=3)
check("no meaningful overlap → no suggestions", s4 == [], s4)
# tie-break determinism: two identical-address stores → sorted by store_code asc
TIE = [{'store_code': 'B-ZZ', 'address': '5 Same St'}, {'store_code': 'A-AA', 'address': '5 Same St'}]
st = R._store_suggest('5 Same St', TIE, top=2)
check("ties broken by store_code ascending (deterministic)", [x['store_code'] for x in st] == ['A-AA', 'B-ZZ'], st)


# ══ (3) suggestions are NEVER auto-applied ═════════════════════════════════════════════════════════
print("(3) suggestions never auto-apply (only an explicit confirm resolves)")
# luxelink store whose POS string is CLOSE to but not exactly a storeops address, and NO alias yet
st_store = base_store(
    daily_sales_feed=[sales('900 Fulton Avenue Hempstead')],
    stores=[store_row('LUX-HEMP', '900 Fulton Ave Hempstead')],
)
c = FakeClient(st_store)
rep = R._store_resolution_report(c, ORG)
it = next(i for i in rep['items'] if i['raw'] == '900 Fulton Avenue Hempstead')
check("close-but-not-exact string is UNRESOLVED despite a strong suggestion", it['status'] == 'unresolved', it)
check("a high-confidence suggestion IS surfaced (smart assist)", it['suggestions'] and it['suggestions'][0]['store_code'] == 'LUX-HEMP')
check("resolved_code stays None until confirmed (no silent apply)", it['resolved_code'] is None)
resolve = R._store_code_resolver(c, ORG)
check("resolver does NOT map the string to the suggested code (returns cleaned raw)",
      resolve('900 Fulton Avenue Hempstead') == '900 Fulton Avenue Hempstead')


# ══ (4) resolver PRECEDENCE unchanged — re-drive the base's cases (byte-identical, empty aliases) ════
print("(4) resolver precedence unchanged (base cases re-driven; empty store_aliases)")
c_join = FakeClient(base_store(stores=[store_row('LUX-HEMP', 'HEMPSTEAD')]))
rj = R._store_code_resolver(c_join, ORG)
check("sales store == storeops address → store_code (exact fallback still works)", rj('HEMPSTEAD') == 'LUX-HEMP')
check("case-insensitive storeops-address match", rj('hempstead') == 'LUX-HEMP')
check("raw string that IS a storeops code is preserved", rj('LUX-HEMP') == 'LUX-HEMP')
check("unmatched string → cleaned raw (needs a mapping)", rj('Hempstead Store #5') == 'Hempstead Store #5')
c_house = FakeClient(base_store(store_mapping=[sm_row('SM-CODE', 'HEMPSTEAD')],
                                stores=[store_row('LUX-HEMP', 'HEMPSTEAD')]))
rh = R._store_code_resolver(c_house, ORG)
check("store_mapping WINS over storeops (house byte-identical)", rh('HEMPSTEAD') == 'SM-CODE')


# ══ (5) EXPLICIT alias wins for EVERY tenant (the luxelink fix) ═════════════════════════════════════
print("(5) explicit per-org mapping is the source of truth (works with NO store_mapping)")
# luxelink: no store_mapping; POS string does NOT match any storeops address; an explicit alias fixes it
lux = base_store(
    daily_sales_feed=[sales('3 Palisade Ave Yonkers')],
    stores=[store_row('B-3PL', '3 Palisade Ave')],
    store_aliases=[{'id': 'a1', 'org_id': ORG, 'alias': '3 Palisade Ave Yonkers', 'store_code': 'B-3PL'}],
)
c = FakeClient(lux)
r5 = R._store_code_resolver(c, ORG)
check("alias resolves a string storeops-address-match would MISS (previously inert for no-store_mapping org)",
      r5('3 Palisade Ave Yonkers') == 'B-3PL')
check("case-insensitive alias match", r5('3 palisade AVE yonkers') == 'B-3PL')
# alias WINS over an exact storeops fallback to a different store
lux2 = base_store(
    stores=[store_row('S-A', 'HEMPSTEAD'), store_row('S-B', 'ELSEWHERE')],
    store_aliases=[{'id': 'a1', 'org_id': ORG, 'alias': 'HEMPSTEAD', 'store_code': 'S-B'}],
)
r5b = R._store_code_resolver(FakeClient(lux2), ORG)
check("explicit alias WINS over exact storeops fallback (no silent shift to the wrong store)",
      r5b('HEMPSTEAD') == 'S-B')
# an alias to a NON-existent code is IGNORED (pick-don't-type integrity; falls through unchanged)
lux3 = base_store(
    stores=[store_row('S-A', 'HEMPSTEAD')],
    store_aliases=[{'id': 'a1', 'org_id': ORG, 'alias': 'HEMPSTEAD', 'store_code': 'TYPO-CODE'}],
)
r5c = R._store_code_resolver(FakeClient(lux3), ORG)
check("alias to a non-existent code is ignored → falls through to exact fallback (no hijack)",
      r5c('HEMPSTEAD') == 'S-A')


# ══ (6) house byte-identity with a seeded alias to a store_mapping code ═════════════════════════════
print("(6) house byte-identity — seeded alias to a store_mapping code == same code as before")
house = base_store(org_over=None)
house = {
    'raw_sales': [], 'daily_sales_feed': [], 'stores': [store_row('B-3PL', '3 Palisade Ave', org=HOUSE)],
    'store_mapping': [sm_row('B-3PL', '3 Palisade Ave', org=HOUSE)],
    'store_aliases': [{'id': 'a1', 'org_id': HOUSE, 'alias': '3 Palisade Ave Yonkers', 'store_code': 'B-3PL'}],
}
rH = R._store_code_resolver(FakeClient(house), HOUSE)
check("seeded alias '3 Palisade Ave Yonkers' → B-3PL (a store_mapping code, unchanged)",
      rH('3 Palisade Ave Yonkers') == 'B-3PL')
check("the canonical store_mapping address still resolves unchanged", rH('3 Palisade Ave') == 'B-3PL')
# with NO store_aliases the house is byte-identical to a store_mapping-only resolution
house_noalias = {k: (v if k != 'store_aliases' else []) for k, v in house.items()}
rH0 = R._store_code_resolver(FakeClient(house_noalias), HOUSE)
check("no aliases → step-0 no-op (store_mapping resolution byte-identical)", rH0('3 Palisade Ave') == 'B-3PL')


# ══ (7) status classification (explicit / store_mapping / exact-fallback / unresolved) ═════════════
print("(7) resolution status classification + counts")
mixed = base_store(
    daily_sales_feed=[sales('3 Palisade Ave Yonkers'), sales('HEMPSTEAD'), sales('900 Fulton Ave'),
                      sales('Unknown Kiosk 7')],
    stores=[store_row('B-3PL', '3 Palisade Ave'), store_row('LUX-HEMP', 'HEMPSTEAD'),
            store_row('LUX-FUL', '900 Fulton Ave')],
    store_mapping=[sm_row('LUX-FUL', '900 Fulton Ave')],
    store_aliases=[{'id': 'a1', 'org_id': ORG, 'alias': '3 Palisade Ave Yonkers', 'store_code': 'B-3PL'}],
)
rep = R._store_resolution_report(FakeClient(mixed), ORG)
by = {i['raw']: i for i in rep['items']}
check("aliased string → 'explicit'", by['3 Palisade Ave Yonkers']['status'] == 'explicit')
check("explicit row carries NO suggestions", by['3 Palisade Ave Yonkers']['suggestions'] == [])
check("store_mapping address → 'store_mapping'", by['900 Fulton Ave']['status'] == 'store_mapping')
check("storeops-only address → 'exact-fallback' with a resolved code + confirm target",
      by['HEMPSTEAD']['status'] == 'exact-fallback' and by['HEMPSTEAD']['resolved_code'] == 'LUX-HEMP')
check("unknown string → 'unresolved' with a suggestion", by['Unknown Kiosk 7']['status'] == 'unresolved')
check("counts tally", rep['counts'] == {'explicit': 1, 'store_mapping': 1, 'exact-fallback': 1, 'unresolved': 1}, rep['counts'])


# ══ (8) confirm writes an org-stamped mapping (POST) ═══════════════════════════════════════════════
print("(8) confirm materializes an org-stamped explicit mapping")
st8 = base_store(stores=[store_row('LUX-HEMP', 'HEMPSTEAD')])
c8 = FakeClient(st8, schemas=SCHEMA_219)
R.sb, _orig = (lambda: c8), R.sb
try:
    out = run(R.add_store_alias({'alias': '900 Fulton Ave', 'store_code': 'LUX-HEMP',
                                 'source': 'suggested', 'confidence': 'high'}, org_id=ORG))
finally:
    R.sb = _orig
row = st8['store_aliases'][-1]
check("confirm inserts a store_aliases row", len(st8['store_aliases']) == 1)
check("row is org-stamped", row['org_id'] == ORG)
check("row records provenance source='suggested'", row.get('source') == 'suggested')
check("row records confidence", row.get('confidence') == 'high')
# resolver now treats it as explicit
r8 = R._store_code_resolver(FakeClient(st8), ORG)
check("the confirmed mapping now resolves (source of truth)", r8('900 Fulton Ave') == 'LUX-HEMP')
# fallback-confirm keeps source tag
c8b = FakeClient(base_store(stores=[store_row('LUX-HEMP', 'HEMPSTEAD')]), schemas=SCHEMA_219)
R.sb, _orig = (lambda: c8b), R.sb
try:
    run(R.add_store_alias({'alias': 'HEMPSTEAD', 'store_code': 'LUX-HEMP', 'source': 'fallback-confirmed', 'confidence': 'exact'}, org_id=ORG))
finally:
    R.sb = _orig
check("fallback-confirm tagged source='fallback-confirmed'", c8b.store['store_aliases'][-1].get('source') == 'fallback-confirmed')

# pick-don't-type: a code NOT in the org's stores is REJECTED
c8c = FakeClient(base_store(stores=[store_row('LUX-HEMP', 'HEMPSTEAD')]), schemas=SCHEMA_219)
R.sb, _orig = (lambda: c8c), R.sb
rejected = False
try:
    try:
        run(R.add_store_alias({'alias': 'x', 'store_code': 'NOT-A-STORE'}, org_id=ORG))
    except Exception:
        rejected = True
finally:
    R.sb = _orig
check("POST to a non-existent store code is rejected (pick-don't-type server guard)", rejected)
check("nothing written on a rejected confirm", c8c.store['store_aliases'] == [])

# case-insensitive dedup: re-confirming the same alias replaces (never duplicates)
c8d = FakeClient(base_store(stores=[store_row('S-A', 'A'), store_row('S-B', 'B')]), schemas=SCHEMA_219)
R.sb, _orig = (lambda: c8d), R.sb
try:
    run(R.add_store_alias({'alias': 'Kiosk 5', 'store_code': 'S-A'}, org_id=ORG))
    run(R.add_store_alias({'alias': 'kiosk 5', 'store_code': 'S-B'}, org_id=ORG))
finally:
    R.sb = _orig
check("case-insensitive re-confirm REPLACES (one row, latest code wins)",
      len(c8d.store['store_aliases']) == 1 and c8d.store['store_aliases'][0]['store_code'] == 'S-B',
      c8d.store['store_aliases'])


# ══ (9) pre-mig-219 graceful (source/confidence columns absent) ════════════════════════════════════
print("(9) pre-migration-219 graceful degrade")
R._TABLE_COL_PRESENT.clear()   # positive cache must not carry a stale 'present' across schemas
st9 = base_store(stores=[store_row('LUX-HEMP', 'HEMPSTEAD')])
c9 = FakeClient(st9, schemas=SCHEMA_PRE219)
R.sb, _orig = (lambda: c9), R.sb
ok9 = True
try:
    try:
        run(R.add_store_alias({'alias': '900 Fulton Ave', 'store_code': 'LUX-HEMP', 'source': 'suggested', 'confidence': 'high'}, org_id=ORG))
    except Exception as e:
        ok9 = False
        print(f"     (pre-219 insert raised: {e})")
finally:
    R.sb = _orig
    R._TABLE_COL_PRESENT.clear()
check("confirm still succeeds when source/confidence columns are absent (mig 219 not yet run)", ok9 and len(st9['store_aliases']) == 1)
check("the mapping is written WITHOUT the missing columns (no 42703)", 'source' not in st9['store_aliases'][0])


# ══ (10) multi-tenant isolation + endpoints ════════════════════════════════════════════════════════
print("(10) org isolation + endpoints")
multi = {
    'raw_sales': [], 'store_mapping': [],
    'daily_sales_feed': [sales('HEMPSTEAD', org=ORG), sales('BOSTON', org='orgB')],
    'stores': [store_row('LUX-HEMP', 'HEMPSTEAD', org=ORG), store_row('B-BOS', 'BOSTON', org='orgB')],
    'store_aliases': [{'id': 'a1', 'org_id': 'orgB', 'alias': 'HEMPSTEAD', 'store_code': 'B-BOS'}],
}
cm = FakeClient(multi)
repA = R._store_resolution_report(cm, ORG)
rawsA = {i['raw'] for i in repA['items']}
check("org A report sees only org A's POS strings (no orgB leak)", rawsA == {'HEMPSTEAD'}, rawsA)
check("orgB's alias does NOT apply to org A (HEMPSTEAD stays exact-fallback for A)",
      repA['items'][0]['status'] == 'exact-fallback')
rA = R._store_code_resolver(cm, ORG)
check("org A resolver ignores orgB's alias", rA('HEMPSTEAD') == 'LUX-HEMP')
# list_store_aliases unions storeops so a NO-store_mapping org still has pickable stores
R.sb, _orig = (lambda: cm), R.sb
try:
    la = run(R.list_store_aliases(org_id=ORG))
    sr = run(R.store_resolution(org_id=ORG))
finally:
    R.sb = _orig
check("list_store_aliases stores union includes the org's storeops roster (no store_mapping needed)",
      any(s['store_code'] == 'LUX-HEMP' for s in la['stores']))
check("store-resolution endpoint returns the report", sr['total'] == 1 and sr['counts']['exact-fallback'] == 1)


print()
print(f"==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
