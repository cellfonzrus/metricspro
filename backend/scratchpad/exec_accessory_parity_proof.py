"""Proof for agent/commission/exec-accessory-parity — OWNER BUG 2026-07-30:
"accessory sales in Executive MTD are different from the accessory target link."

Drives the REAL router functions over an in-memory FakeClient (house style; no DB/network) and
reconciles, line by line, Executive MTD (`_exec_mtd` / `exec_mtd`) against the Accessory Targets page's
source (`get_targets_summary` -> `_fetch_actuals` + `_targets_trending_by_code`).

THE DIAGNOSIS this harness encodes (each suspect is asserted, not asserted-away):
  (a) SET-UP FEE — the real divergence.  `_sales_cell_agg` splits every accessory-ish line into
      `accessory_rev` (pure accessory) and `setup_fee_rev` (device set-up fee, config keyword, DEFAULT
      ['Device Setup Charge'] -> ACTIVE for every tenant).  Exec MTD's `acc_sales` took accessory_rev
      ONLY; the accessory TARGET's `achieved_mtd` takes accessory_rev + setup_fee_rev (owner directive
      2026-07-17: the set-up fee is a separate pay item that still counts toward the accessory target).
      Both are individually CORRECT; they were labelled as if they were the same number, and — worse —
      the Accessory Targets page's own "Trending" column projected the PURE basis while its "Achieved"
      column tracked the TARGET basis, so a projection could sit BELOW the achieved figure.
  (b) CLASSIFIER — ruled out: both surfaces go through the ONE `_sales_cell_agg` / `_is_accessory`.
      '* BYOD' / TABLET / SIM lines are asserted to land identically on both.
  (c) SOURCE TABLE — ruled out: both read `_sales_rows_union` (feed x raw_sales, same merge).  Only the
      column list differs and `_ACTUALS_COLS` is a superset of `_SALES_DISPLAY_COLS`.
  (d) WINDOW — real but secondary: `/exec-mtd` had NO `today` param (server UTC clock) while
      `/targets/{p}/summary` takes the browser's local date.  After 8pm ET the two pages used different
      MTD windows and different trend divisors.  Period SPELLING is ruled out (both `_pvariants`).
  (e) STORE COVERAGE — real: Exec MTD lists EVERY selling store; the targets summary only lists stores
      that resolve to a store_code in the roster/target universe, so an unmapped store's accessory $ is
      in one total and not the other, silently.  Now surfaced in `trending.unmapped_*`.

NEGATIVE CONTROL: run this against origin/main and section (P) FAILS (missing fields / unequal numbers)
while section (N) proves the pre-fix divergence is exactly the set-up fee.  Post-fix everything passes.

Run:  cd backend && python3 scratchpad/exec_accessory_parity_proof.py
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


# ── in-memory fake supabase client (honours eq / in_ / neq) ───────────────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.t = table
        self.f = []
        self.rng = None

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def neq(self, c, v):
        self.f.append(('neq', c, v)); return self

    def gte(self, c, v):
        self.f.append(('gte', c, v)); return self

    def lte(self, c, v):
        self.f.append(('lte', c, v)); return self

    def lt(self, c, v):
        self.f.append(('lt', c, v)); return self

    def gt(self, c, v):
        self.f.append(('gt', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

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
            if k in ('gte', 'lte', 'lt', 'gt'):
                if rv is None:
                    return False
                a, b = str(rv), str(v)
                if k == 'gte' and not a >= b:
                    return False
                if k == 'lte' and not a <= b:
                    return False
                if k == 'lt' and not a < b:
                    return False
                if k == 'gt' and not a > b:
                    return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        m = [r for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        return FakeResult(data=[dict(r) for r in m])


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeQuery(self.store, t)

    def rpc(self, *a, **k):
        raise Exception('no rpc in this proof')


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, s):
        return FakeSchema(self.store)


ORG = 'o'
_T = _date.today()
YMD = f"{_T.year}-{_T.month:02d}"                      # dashed spelling (daily_sales_feed)
MONTHNAME = f"{_cal.month_name[_T.month]} {_T.year}"   # 'Month YYYY' spelling (raw_sales) + the API label
PERIOD = MONTHNAME
DIM = _cal.monthrange(_T.year, _T.month)[1]
TODAY = _date(_T.year, _T.month, min(10, DIM))         # injected "today" -> MTD cut at the 10th
TODAY_ISO = TODAY.isoformat()
ELAPSED = max(1, TODAY.day - 1)
FACTOR = DIM / ELAPSED
D05 = f"{YMD}-05"
D08 = f"{YMD}-08"
DLATE = f"{YMD}-{min(20, DIM):02d}"                    # AFTER the cut

S1_ADDR = '3 Palisade Ave'
S2_ADDR = '100 Main St'
S3_ADDR = '999 Unmapped Rd'                            # sells, but is in NO roster / mapping / target


def row(store, rep, tid, ct='', day=D05, cat='CellPhone', dept='', ext=100.0, gp=20.0, pdesc='',
        period=YMD):
    return {'org_id': ORG, 'period': period, 'trans_id': tid, 'trans_date': day, 'store': store,
            'salesperson': rep, 'user_login': (rep or '').lower(), 'category': cat, 'department': dept,
            'contract_type': ct, 'product_desc': pdesc, 'ext_price': ext, 'gp': gp,
            'voided': '', 'trans_type': ''}


# ── THE FIXTURE — one row per suspect class ───────────────────────────────────────────────────────
# S1 (mapped): plain accessory 40 + '* BYOD' accessory 15 + SET-UP FEE 25 + TABLET 200 + SIM 5
#              + a POST-CUT accessory 500 (must be excluded by BOTH surfaces)
# S2 (mapped): plain accessory 60 + SET-UP FEE 30, plus a raw_sales-only day-8 accessory 70 written in
#              the OTHER period spelling ('Month YYYY') — proves both surfaces read both spellings
# S3 (UNMAPPED): accessory 300 — in Exec MTD, invisible to the targets summary (suspect (e))
FEED = [
    row(S1_ADDR, 'ALICE', 'A1', 'Activation', ext=100.0),
    row(S1_ADDR, 'CARLA', 'AC1', day=D08, cat='Accessory', ext=40.0, pdesc='Screen Protector'),
    row(S1_ADDR, 'CARLA', 'AC2', day=D08, cat='Accessory', ext=15.0, pdesc='Otter Case * BYOD'),
    row(S1_ADDR, 'CARLA', 'SF1', day=D08, cat='Fees', dept='Fees', ext=25.0,
        pdesc='Device Setup Charge'),                                    # (a) set-up fee
    row(S1_ADDR, 'CARLA', 'TB1', day=D08, cat='Tablet', ext=200.0, pdesc='Tab A9'),   # (b) not accessory
    row(S1_ADDR, 'CARLA', 'SM1', day=D08, cat='SIM', ext=5.0, pdesc='SIM Kit'),       # (b) not accessory
    row(S1_ADDR, 'CARLA', 'LATE', day=DLATE, cat='Accessory', ext=500.0, pdesc='Post-cut case'),
    row(S2_ADDR, 'DAN', 'U2', 'Upgrade', ext=100.0),
    row(S2_ADDR, 'DAN', 'AC3', day=D05, cat='Accessory', ext=60.0, pdesc='Charger'),
    row(S2_ADDR, 'DAN', 'SF2', day=D05, cat='Fees', dept='Fees', ext=30.0,
        pdesc='DEVICE SETUP CHARGE'),                                    # (a) case-insensitive
    row(S3_ADDR, 'ERIN', 'AC9', day=D05, cat='Accessory', ext=300.0, pdesc='Unmapped store case'),
]
# raw_sales — 'Month YYYY' spelling, a store-day cell the feed does NOT hold (S2 / day 8) (suspect d)
RAW = [
    row(S2_ADDR, 'DAN', 'AC4', day=D08, cat='Accessory', ext=70.0, pdesc='Cable', period=MONTHNAME),
]

SETUP_S1, SETUP_S2 = 25.0, 30.0
ACC_S1, ACC_S2, ACC_S3 = 55.0, 130.0, 300.0     # pure accessory$, MTD (post-cut LATE excluded)


def base_store():
    return {
        'daily_sales_feed': [dict(r) for r in FEED],
        'raw_sales': [dict(r) for r in RAW],
        'accessory_config': [{'org_id': ORG, 'departments': [], 'categories': ['accessory'],
                              'product_keywords': [], 'acima_tenders': []}],
        'store_mapping': [
            {'org_id': ORG, 'store_address': S1_ADDR, 'store_code': 'S1', 'market': 'North'},
            {'org_id': ORG, 'store_address': S2_ADDR, 'store_code': 'S2', 'market': 'South'},
        ],
        'stores': [
            {'org_id': ORG, 'store_code': 'S1', 'address': S1_ADDR, 'market': 'North', 'monthly_target': 0},
            {'org_id': ORG, 'store_code': 'S2', 'address': S2_ADDR, 'market': 'South', 'monthly_target': 0},
        ],
        'targets': [
            {'org_id': ORG, 'period': YMD, 'store_code': 'S1', 'activations_monthly': 10,
             'upgrades_monthly': 5, 'accessories_monthly': 1000},
            {'org_id': ORG, 'period': YMD, 'store_code': 'S2', 'activations_monthly': 5,
             'upgrades_monthly': 3, 'accessories_monthly': 500},
        ],
        'exec_metric_config': [], 'shifts': [], 'name_map': [], 'rep_aliases': [],
        'store_aliases': [], 'app_config': [],
    }


def run_summary(store, today_iso=TODAY_ISO):
    c = FakeClient(store)
    _orig = R.sb
    R.sb = lambda: c
    try:
        return run_route(R.get_targets_summary(period=PERIOD, today=today_iso, org_id=ORG,
                                                 include_untargeted=True)), c
    finally:
        R.sb = _orig


class _Missing:
    """Distinct-per-side sentinel: two ABSENT fields must NOT compare equal, or the pre-fix negative
    control would pass vacuously (None == None)."""
    def __init__(self, side):
        self.side = side

    def __repr__(self):
        return f"<missing:{self.side}>"

    def __eq__(self, other):
        return False

    def __ge__(self, other):
        return False

    def __hash__(self):
        return id(self)


def g(d, k, side='?'):
    """Field access that reports a MISSING key rather than blowing up (pre-fix negative control)."""
    if isinstance(d, dict) and k in d:
        return d[k]
    return _Missing(f"{side}.{k}")


store = base_store()
summ, c = run_summary(store)
ex = R._exec_mtd(c, ORG, PERIOD, today=TODAY)
ex_rows = {r['store']: r for r in ex['by_location']['rows']}
ex_tot = ex['by_location']['total']
tg = {s['store_code']: s for s in summ['stores']}
accv = lambda code: (tg[code].get('categories', {}) or {}).get('accessories', {}) or {}

# ══ (S) the shared aggregation actually produced the two components ═══════════════════════════════
print("(S) the ONE shared aggregation splits accessory$ from set-up-fee$ (inputs both surfaces read)")
urows, _um = R._sales_rows_union(c, ORG, PERIOD)
acfg = R._accessory_config(c, ORG)
cells = R._sales_cell_agg(urows, acfg)
tot_acc = round(sum(a['accessory_rev'] for a in cells.values()), 2)
tot_setup = round(sum(a['setup_fee_rev'] for a in cells.values()), 2)
check("union reads BOTH period spellings (feed '2026-07' + raw_sales 'July 2026' row present)",
      any(r['trans_id'] == 'AC4' for r in urows), "raw_sales 'Month YYYY' row missing from the union")
check("set-up-fee keyword default is ACTIVE for a tenant with no explicit config",
      R._is_setup_fee('Device Setup Charge', acfg) is True)
check("(b) '* BYOD' accessory line classifies as an accessory (one classifier, both surfaces)",
      R._is_accessory('', 'Accessory', 'Otter Case * BYOD', acfg) is True)
check("(b) TABLET line is NOT an accessory", R._is_accessory('', 'Tablet', 'Tab A9', acfg) is False)
check("(b) SIM line is NOT an accessory", R._is_accessory('', 'SIM', 'SIM Kit', acfg) is False)
check("whole-month accessory$ (S1 55 + S2 130 + unmapped 300 + post-cut 500) == 985.00",
      tot_acc == 985.0, tot_acc)
check("whole-month set-up-fee$ == 55.00 (25 + 30, case-insensitive)", tot_setup == 55.0, tot_setup)

# ══ (N) NEGATIVE CONTROL — the pre-fix divergence IS the set-up fee, to the cent ══════════════════
print("\n(N) the divergence the owner reported, quantified (holds pre- AND post-fix: acc_sales stays pure)")
for code, addr, acc, setup in (('S1', S1_ADDR, ACC_S1, SETUP_S1), ('S2', S2_ADDR, ACC_S2, SETUP_S2)):
    exr, a = ex_rows[addr], accv(code)
    check(f"{code}: Exec MTD 'Acc. Sales' == pure accessory$ ({acc})", exr['acc_sales'] == acc,
          f"got {exr['acc_sales']}")
    check(f"{code}: accessory TARGET achieved == accessory$ + set-up fee ({acc + setup})",
          a.get('achieved_mtd') == acc + setup, f"got {a.get('achieved_mtd')}")
    check(f"{code}: the two surfaces differ by EXACTLY the set-up fee ({setup})",
          round(a.get('achieved_mtd', 0) - exr['acc_sales'], 2) == setup,
          f"delta {round(a.get('achieved_mtd', 0) - exr['acc_sales'], 2)} vs {setup}")
    check(f"{code}: the target page already breaks the set-up fee out (setup_fee_mtd == {setup})",
          a.get('setup_fee_mtd') == setup, f"got {a.get('setup_fee_mtd')}")

# ══ (P) POST-FIX PARITY — Exec MTD carries BOTH bases, honestly labelled, from the same cells ═════
print("\n(P) POST-FIX: Exec MTD exposes the target basis; every store reconciles to the cent")
for code, addr, acc, setup in (('S1', S1_ADDR, ACC_S1, SETUP_S1), ('S2', S2_ADDR, ACC_S2, SETUP_S2)):
    exr, a = ex_rows[addr], accv(code)
    check(f"{code}: Exec MTD row carries 'setup_fee'", 'setup_fee' in exr, "field missing (pre-fix)")
    check(f"{code}: Exec MTD row carries 'acc_plus_setup'", 'acc_plus_setup' in exr, "field missing (pre-fix)")
    check(f"{code}: Exec MTD setup_fee == the target page's setup_fee_mtd ({setup})",
          g(exr, 'setup_fee', 'exec') == a.get('setup_fee_mtd'),
          f"{g(exr, 'setup_fee', 'exec')} vs {a.get('setup_fee_mtd')}")
    check(f"{code}: Exec MTD acc_plus_setup == accessory-target achieved_mtd ({acc + setup})",
          g(exr, 'acc_plus_setup', 'exec') == a.get('achieved_mtd'),
          f"{g(exr, 'acc_plus_setup', 'exec')} vs {a.get('achieved_mtd')}")
    check(f"{code}: Exec MTD acc_sales == target achieved MINUS the set-up fee (pure basis honest)",
          exr['acc_sales'] == round(a.get('achieved_mtd', 0) - a.get('setup_fee_mtd', 0), 2))

check("Exec MTD TOTAL row carries setup_fee + acc_plus_setup", 'acc_plus_setup' in ex_tot and 'setup_fee' in ex_tot,
      "totals not extended (pre-fix)")
check("Exec MTD TOTAL acc_plus_setup == sum of the row values (S1+S2+S3 incl. the unmapped store)",
      'acc_plus_setup' in ex_tot and ex_tot['acc_plus_setup'] ==
      round(sum(r.get('acc_plus_setup', 0) for r in ex['by_location']['rows']), 2),
      f"{g(ex_tot, 'acc_plus_setup', 'exec_total')}")
check("Exec MTD by_employee TOTAL acc_plus_setup == by_location TOTAL (same cells, two groupings)",
      'acc_plus_setup' in ex['by_employee']['total'] and 'acc_plus_setup' in ex_tot and
      ex['by_employee']['total']['acc_plus_setup'] == ex_tot['acc_plus_setup'],
      f"{g(ex['by_employee']['total'], 'acc_plus_setup', 'by_emp')} vs {g(ex_tot, 'acc_plus_setup', 'by_loc')}")

# ══ (T) TRENDING — the target page must project the basis it TRACKS ═══════════════════════════════
print("\n(T) trending is projected on the SAME basis the column next to it tracks")
for code, addr, acc, setup in (('S1', S1_ADDR, ACC_S1, SETUP_S1), ('S2', S2_ADDR, ACC_S2, SETUP_S2)):
    st, exr = tg[code], ex_rows[addr]
    check(f"{code}: Exec MTD trending_acc_plus_setup == acc_plus_setup x factor",
          g(exr, 'trending_acc_plus_setup', 'exec') == round((acc + setup) * FACTOR, 2),
          f"{g(exr, 'trending_acc_plus_setup', 'exec')} vs {round((acc + setup) * FACTOR, 2)}")
    check(f"{code}: targets summary carries 'trending_acc_target'", 'trending_acc_target' in st,
          "field missing (pre-fix)")
    check(f"{code}: targets trending_acc_target == Exec MTD trending_acc_plus_setup (ONE source)",
          'trending_acc_target' in st and 'trending_acc_plus_setup' in exr and
          st['trending_acc_target'] == exr['trending_acc_plus_setup'],
          f"{g(st, 'trending_acc_target', 'targets')} vs {g(exr, 'trending_acc_plus_setup', 'exec')}")
    check(f"{code}: trending (target basis) >= achieved (target basis) mid-open-month — the pre-fix absurdity",
          g(st, 'trending_acc_target', 'targets') >= accv(code).get('achieved_mtd', 0),
          f"trend {g(st, 'trending_acc_target', 'targets')} < achieved {accv(code).get('achieved_mtd')}")
    check(f"{code}: the PURE-basis trending is preserved unchanged (Exec MTD's own column)",
          st['trending_acc_sales'] == exr['trending_acc_sales'],
          f"{st['trending_acc_sales']} vs {exr['trending_acc_sales']}")

# ══ (C) STORE COVERAGE — the unmapped store is now explained, not silently missing ════════════════
print("\n(C) suspect (e): the unmapped selling store is REPORTED, so the two totals reconcile")
check("Exec MTD lists the unmapped store S3", S3_ADDR in ex_rows, sorted(ex_rows))
check("the targets summary does NOT list S3 (it has no store_code) — correct, but must be explained",
      'S3' not in tg and len(tg) == 2, sorted(tg))
tmeta = summ.get('trending', {}) or {}
check("targets summary reports unmapped_acc_sales", 'unmapped_acc_sales' in tmeta, "field missing (pre-fix)")
check(f"unmapped_acc_sales == the unmapped store's accessory$ ({ACC_S3})",
      g(tmeta, 'unmapped_acc_sales', 'targets') == ACC_S3, f"got {g(tmeta, 'unmapped_acc_sales', 'targets')}")
check("unmapped_stores names the store so the operator can map it",
      S3_ADDR in (tmeta.get('unmapped_stores') or []), tmeta.get('unmapped_stores'))
targets_total = round(sum(accv(k).get('achieved_mtd', 0) for k in tg), 2)
check("Exec MTD total - targets total == EXACTLY the unmapped store's accessory$ (fully reconciled)",
      'acc_plus_setup' in ex_tot and
      round(ex_tot['acc_plus_setup'] - targets_total, 2) == tmeta.get('unmapped_acc_plus_setup'),
      f"{round(g(ex_tot, 'acc_plus_setup', 'exec') if 'acc_plus_setup' in ex_tot else 0, 2)} - "
      f"{targets_total} vs {g(tmeta, 'unmapped_acc_plus_setup', 'targets')}")

# ══ (W) WINDOW — same MTD cut, and /exec-mtd now accepts the caller's local date ══════════════════
print("\n(W) suspect (d): identical MTD window; /exec-mtd accepts the client's local 'today'")
check("the post-cut accessory row (day 20) is excluded from Exec MTD",
      ex_rows[S1_ADDR]['acc_sales'] == ACC_S1, ex_rows[S1_ADDR]['acc_sales'])
check("the post-cut accessory row is excluded from the accessory target achieved too",
      accv('S1').get('achieved_mtd') == ACC_S1 + SETUP_S1, accv('S1').get('achieved_mtd'))
ex_late = R._exec_mtd(c, ORG, PERIOD, today=_date(_T.year, _T.month, DIM))
check("with today=month-end BOTH re-include it (the cut is the only difference)",
      ex_late['by_location']['rows'] and
      {r['store']: r for r in ex_late['by_location']['rows']}[S1_ADDR]['acc_sales'] == ACC_S1 + 500.0,
      {r['store']: r for r in ex_late['by_location']['rows']}[S1_ADDR]['acc_sales'])
check("exec MTD trend factor == the targets summary trend factor (same divisor)",
      round(ex['trending']['factor'], 6) == round(summ['trending']['factor'], 6),
      f"{ex['trending']} vs {summ['trending']}")
_orig = R.sb
R.sb = lambda: c
try:
    import inspect as _insp
    has_today = 'today' in _insp.signature(R.exec_mtd).parameters
    check("/exec-mtd endpoint accepts a `today` query param (client local date, not server UTC)",
          has_today, "endpoint has no today param (pre-fix)")
    if has_today:
        api_ex = R.exec_mtd(period=PERIOD, org_id=ORG, today=TODAY_ISO)
        check("/exec-mtd?today=<local> == _exec_mtd(today=<local>) byte-for-byte",
              api_ex['by_location']['total'] == ex_tot, "endpoint ignored the today param")
        api_def = R.exec_mtd(period=PERIOD, org_id=ORG)
        check("/exec-mtd with NO today still uses the server date (byte-identical default)",
              api_def['by_location']['total'] ==
              R._exec_mtd(c, ORG, PERIOD)['by_location']['total'])
        api_bad = R.exec_mtd(period=PERIOD, org_id=ORG, today='not-a-date')
        check("a malformed today falls back to the server date (never 500s)",
              api_bad['by_location']['total'] == api_def['by_location']['total'])
finally:
    R.sb = _orig

# ══ (Z) NON-REGRESSION — the pay-adjacent numbers and the Sales Report parity are untouched ═══════
print("\n(Z) non-regression: activation buckets + Sales Report parity unchanged (nothing money moved)")
sr_acc = round(sum(a['accessory_rev'] for (s, r, d), a in cells.items() if d <= TODAY_ISO), 2)
check("Exec MTD acc_sales TOTAL still equals the Sales Report's accessory_rev over the cut rows",
      ex_tot['acc_sales'] == sr_acc, f"{ex_tot['acc_sales']} vs {sr_acc}")
check("Exec MTD total_activation unchanged by this package (2 mapped + 0 unmapped activations)",
      ex_tot['total_activation'] == 2, ex_tot['total_activation'])
check("targets activations achieved unchanged (S1 1 activation, S2 1 upgrade)",
      accv('S1') is not None and tg['S1']['categories']['activations']['achieved_mtd'] == 1
      and tg['S2']['categories']['upgrades']['achieved_mtd'] == 1,
      f"{tg['S1']['categories']['activations']['achieved_mtd']} / "
      f"{tg['S2']['categories']['upgrades']['achieved_mtd']}")
check("APB still divides the PURE accessory basis (the b2bsoft metric is unchanged)",
      ex_rows[S1_ADDR]['apb'] == round(ACC_S1 / 1, 2), ex_rows[S1_ADDR]['apb'])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
