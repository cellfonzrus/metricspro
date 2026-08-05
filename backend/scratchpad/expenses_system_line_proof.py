"""Proof for the AUTO/"system" store-expense line (Paid Leave Accumulated / PTO accrual receiver).

Drives the REAL router code:
  - pure `_system_line_expand` (cells → tagged INSERT rows),
  - the REAL async endpoints `upsert_expense_system_line`, `put_expenses`, `apply_expenses_to_months`
    (with router.sb monkeypatched to a faithful in-memory FakeClient mirroring store_expenses),
  - the REAL guards `_delete_manual_expenses`, `_system_line_keys`, `_is_missing_col_err`.

Proves:
  1. EXPANSION — cells→rows tagged AUTO (source_key), store/store_code alias, last-write-wins, zero/blank drop,
     org_id/period/label baked in.
  2. RECEIVER — writes only non-zero cells tagged source_key; returns {stores_written,total}; IDEMPOTENT
     (re-run identical → no dup); REPLACE (changed re-run replaces prior values, drops a now-zero store).
  3. ORG-SCOPING — an orgA system-line write never touches orgB's rows (and vice-versa).
  4. put_expenses does NOT clobber a system line — manual-only delete keeps the auto row; an incoming manual
     row that would SHADOW the system (store,expense) is dropped → no duplicate, no double-count in GP.
  5. apply-to-months does NOT copy a system line — the manual-only source read excludes it; a target month's
     own system line survives the per-cell manual-only delete.
  6. GRACEFUL DEGRADATION (pre-mig-206, source_key column absent) — receiver returns ok=False + the 206 hint;
     put_expenses still saves manual rows; `_is_missing_col_err` classifies only column/schema errors.

Run: python3 backend/scratchpad/expenses_system_line_proof.py
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.modules.commcalc import router
from app.modules.commcalc.router import (
    _system_line_expand, _delete_manual_expenses, _system_line_keys, _is_missing_col_err, _pvariants,
)

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else:    FAIL += 1; print(f"  FAIL {name}")


# ── FakeClient: mirrors the endpoints' DB path (delete/insert/select over a python list) ──────────────
class _Result:
    def __init__(self, data): self.data = data

class _Q:
    def __init__(self, tbl):
        self.t = tbl; self._f = {}; self._in = {}; self._null = []
        self._op = None; self._cols = None; self._rows = None
    def delete(self): self._op = 'delete'; return self
    def insert(self, rows): self._op = 'insert'; self._rows = rows; return self
    def select(self, cols): self._op = 'select'; self._cols = cols; return self
    def eq(self, k, v):
        if k == 'source_key' and not self.t.has_sk:          # filtering a missing column raises (pre-mig)
            raise Exception("column store_expenses.source_key does not exist (SQLSTATE 42703)")
        self._f[k] = v; return self
    def in_(self, k, vs): self._in[k] = list(vs); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def is_(self, k, v):
        if k == 'source_key' and not self.t.has_sk:          # simulate a pre-migration missing column
            raise Exception("column store_expenses.source_key does not exist (SQLSTATE 42703)")
        if str(v) == 'null': self._null.append(k)
        return self
    def _match(self, r):
        return (all(r.get(k) == v for k, v in self._f.items())
                and all(r.get(k) in vs for k, vs in self._in.items())
                and all(r.get(k) is None for k in self._null))
    def execute(self):
        if self._op == 'delete':
            self.t.rows[:] = [r for r in self.t.rows if not self._match(r)]
            return _Result(None)
        if self._op == 'insert':
            if not self.t.has_sk and any('source_key' in r for r in self._rows):   # schema-cache miss (pre-mig)
                raise Exception("Could not find the 'source_key' column of 'store_expenses' in the schema cache")
            self.t.rows.extend(dict(r) for r in self._rows); return _Result(list(self._rows))
        if self._op == 'select':
            cols = [c.strip() for c in str(self._cols or '*').split(',')]
            if 'source_key' in cols and not self.t.has_sk:   # selecting a missing column raises (pre-mig)
                raise Exception("column store_expenses.source_key does not exist (SQLSTATE 42703)")
            out = [dict(r) if self._cols in (None, '*') else {c: r.get(c) for c in cols}
                   for r in self.t.rows if self._match(r)]
            return _Result(out)
        return _Result(None)

class _Tbl:
    def __init__(self, has_sk): self.rows = []; self.has_sk = has_sk
    def q(self): return _Q(self)

class _Schema:
    def __init__(self, c): self.c = c
    def table(self, name): return self.c._tbl(name).q()

class FakeClient:
    def __init__(self, has_sk=True): self._tbls = {}; self._has_sk = has_sk
    def _tbl(self, name):
        if name not in self._tbls:
            self._tbls[name] = _Tbl(self._has_sk if name == 'store_expenses' else True)
        return self._tbls[name]
    def schema(self, s): return _Schema(self)


ORGA, ORGB = 'org-aaaa', 'org-bbbb'
JULY = 'July 2026'
def se(fc): return fc._tbl('store_expenses').rows                       # store_expenses rows
def rows_for(fc, org, period=JULY):                                     # helper: rows for an (org,period)
    pv = _pvariants(period)
    return [r for r in se(fc) if r['org_id'] == org and r['period'] in pv]
def use(fc):
    router.sb = lambda: fc                                              # drive REAL endpoints against fc


# ── 1. EXPANSION (pure _system_line_expand) ───────────────────────────────────────────────────────────
print("\n1. _system_line_expand (pure)")
rows = _system_line_expand(ORGA, JULY, 'pto_accrual', 'Paid Leave Accumulated',
                           [{'store': 'S1', 'amount': 100}, {'store': 'S2', 'amount': 50.5},
                            {'store': 'S3', 'amount': 0}, {'store': '', 'amount': 9}])
check("drops zero-amount + blank-store cells (2 rows from 4)", len(rows) == 2)
check("every row tagged source_key='pto_accrual' (AUTO)", all(r['source_key'] == 'pto_accrual' for r in rows))
check("label → expense_name", all(r['expense_name'] == 'Paid Leave Accumulated' for r in rows))
check("org_id + period baked in", all(r['org_id'] == ORGA and r['period'] == JULY for r in rows))
check("default expense_type Fixed", all(r['expense_type'] == 'Fixed' for r in rows))
alias = _system_line_expand(ORGA, JULY, 'k', 'L', [{'store_code': 'S9', 'amount': 7}])   # store_code alias
check("accepts store_code alias", len(alias) == 1 and alias[0]['store_code'] == 'S9')
lww = _system_line_expand(ORGA, JULY, 'k', 'L', [{'store': 'S1', 'amount': 1}, {'store': 'S1', 'amount': 9}])
check("last-write-wins per store", len(lww) == 1 and lww[0]['amount'] == 9)
badamt = _system_line_expand(ORGA, JULY, 'k', 'L', [{'store': 'S1', 'amount': 'oops'}])
check("unparseable amount → 0 → dropped", badamt == [])
etype = _system_line_expand(ORGA, JULY, 'k', 'L', [{'store': 'S1', 'amount': 5}], expense_type='Variable')
check("expense_type honored when supplied", etype[0]['expense_type'] == 'Variable')


# ── 2. RECEIVER endpoint: write / idempotent / replace / total ────────────────────────────────────────
print("\n2. POST /expenses/{period}/system-line (real endpoint)")
fc = FakeClient(); use(fc)
res = asyncio.run(router.upsert_expense_system_line(JULY, {
    'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 100}, {'store': 'S2', 'amount': 50}, {'store': 'S3', 'amount': 0}]}, org_id=ORGA))
check("returns ok", res.get('ok') is True)
check("stores_written = 2 (zero dropped)", res.get('stores_written') == 2)
check("total = 150.0", res.get('total') == 150.0)
check("2 tagged rows in store_expenses", len(rows_for(fc, ORGA)) == 2 and all(r['source_key'] == 'pto_accrual' for r in rows_for(fc, ORGA)))
# idempotent re-run (identical payload) → no duplicate
asyncio.run(router.upsert_expense_system_line(JULY, {
    'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 100}, {'store': 'S2', 'amount': 50}]}, org_id=ORGA))
check("idempotent re-run → still exactly 2 rows (no dup)", len(rows_for(fc, ORGA)) == 2)
# changed re-run → replace (S1 updated, S2 now zero → removed)
asyncio.run(router.upsert_expense_system_line(JULY, {
    'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 120}, {'store': 'S2', 'amount': 0}]}, org_id=ORGA))
after = rows_for(fc, ORGA)
check("changed re-run REPLACES prior values (1 row, S1=120)",
      len(after) == 1 and after[0]['store_code'] == 'S1' and after[0]['amount'] == 120)
missing = asyncio.run(router.upsert_expense_system_line(JULY, {'source_key': '', 'label': 'x', 'cells': []}, org_id=ORGA))
check("missing source_key rejected", missing.get('ok') is False)


# ── 3. ORG-SCOPING ────────────────────────────────────────────────────────────────────────────────────
print("\n3. org-scoping")
fc = FakeClient(); use(fc)
asyncio.run(router.upsert_expense_system_line(JULY, {'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 100}]}, org_id=ORGA))
asyncio.run(router.upsert_expense_system_line(JULY, {'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 999}]}, org_id=ORGB))
check("orgB row written independently", len(rows_for(fc, ORGB)) == 1 and rows_for(fc, ORGB)[0]['amount'] == 999)
# re-run orgA (replace) must not disturb orgB
asyncio.run(router.upsert_expense_system_line(JULY, {'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 111}]}, org_id=ORGA))
check("orgA replace left orgB untouched (999)", len(rows_for(fc, ORGB)) == 1 and rows_for(fc, ORGB)[0]['amount'] == 999)
check("orgA now 111", rows_for(fc, ORGA)[0]['amount'] == 111)


# ── 4. put_expenses does NOT clobber the system line ──────────────────────────────────────────────────
print("\n4. put_expenses (manual save) protects the system line")
fc = FakeClient(); use(fc)
# seed: a system pto line (S1=120,S2=80) + a manual Rent (S1=500)
asyncio.run(router.upsert_expense_system_line(JULY, {'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 120}, {'store': 'S2', 'amount': 80}]}, org_id=ORGA))
se(fc).append({'org_id': ORGA, 'period': JULY, 'store_code': 'S1', 'expense_name': 'Rent / Lease',
               'expense_type': 'Variable', 'amount': 500, 'source_key': None})
# a manual save that (a) updates Rent and (b) WRONGLY tries to write the system line back as manual
res = asyncio.run(router.put_expenses(JULY, {'rows': [
    {'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 600},
    {'store_code': 'S1', 'expense_name': 'Paid Leave Accumulated', 'expense_type': 'Fixed', 'amount': 9999}]}, org_id=ORGA))
sysrows = [r for r in rows_for(fc, ORGA) if r.get('source_key')]
manrows = [r for r in rows_for(fc, ORGA) if not r.get('source_key')]
check("both system pto rows survive the full-period save", len(sysrows) == 2)
check("system pto amounts unchanged (120,80 — NOT 9999)", sorted(r['amount'] for r in sysrows) == [80, 120])
check("no MANUAL 'Paid Leave Accumulated' shadow row created", not any(m['expense_name'] == 'Paid Leave Accumulated' for m in manrows))
check("manual Rent updated to 600", any(m['expense_name'] == 'Rent / Lease' and m['amount'] == 600 for m in manrows))
check("exactly one Rent row (no dup)", len([m for m in manrows if m['expense_name'] == 'Rent / Lease']) == 1)
# _system_line_keys returns the protected (store,expense) set
keys = _system_line_keys(fc, ORGA, _pvariants(JULY))
check("_system_line_keys = the 2 auto (store,expense) pairs",
      keys == {('S1', 'Paid Leave Accumulated'), ('S2', 'Paid Leave Accumulated')})


# ── 5. apply-to-months does NOT copy a system line; target's own system line survives ─────────────────
print("\n5. apply_expenses_to_months protects system lines")
fc = FakeClient(); use(fc)
# July (source): manual Rent(S1=500) + system pto(S1=120)
se(fc).append({'org_id': ORGA, 'period': JULY, 'store_code': 'S1', 'expense_name': 'Rent / Lease',
               'expense_type': 'Variable', 'amount': 500, 'source_key': None})
asyncio.run(router.upsert_expense_system_line(JULY, {'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 120}]}, org_id=ORGA))
# June (target): already has ITS OWN system pto(S1=77) that must survive
asyncio.run(router.upsert_expense_system_line('June 2026', {'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 77}]}, org_id=ORGA))
res = asyncio.run(router.apply_expenses_to_months(
    {'source_period': JULY, 'target_periods': ['June 2026']}, org_id=ORGA))
june = rows_for(fc, ORGA, 'June 2026')
check("apply ok", res.get('ok') is True)
check("June received manual Rent (copied)", any(r['expense_name'] == 'Rent / Lease' and r['amount'] == 500 for r in june))
check("June did NOT receive a copied 'Paid Leave Accumulated' from July",
      not any(r.get('source_key') is None and r['expense_name'] == 'Paid Leave Accumulated' for r in june))
check("June's OWN system pto (77) survived",
      any(r.get('source_key') == 'pto_accrual' and r['amount'] == 77 for r in june))
check("'Paid Leave Accumulated' not in the endpoint's copied_expenses",
      'Paid Leave Accumulated' not in (res.get('copied_expenses') or []))
# July's system line untouched by the operation
check("July's system pto (120) untouched",
      any(r.get('source_key') == 'pto_accrual' and r['amount'] == 120 for r in rows_for(fc, ORGA, JULY)))

# 5b. the guard helper directly: a manual-only delete removes a manual row but keeps a same-named system row
fc2 = FakeClient()
t = fc2._tbl('store_expenses').rows
t.append({'org_id': ORGA, 'period': JULY, 'store_code': 'S1', 'expense_name': 'X', 'amount': 1, 'source_key': None})
t.append({'org_id': ORGA, 'period': JULY, 'store_code': 'S1', 'expense_name': 'X', 'amount': 2, 'source_key': 'pto_accrual'})
_delete_manual_expenses(fc2, ORGA, _pvariants(JULY), {'expense_name': 'X', 'store_code': ['S1']})
check("_delete_manual_expenses removed the MANUAL X row", not any(r['source_key'] is None for r in t))
check("_delete_manual_expenses KEPT the system X row", any(r['source_key'] == 'pto_accrual' for r in t))


# ── 6. graceful degradation (pre-mig-206: source_key column absent) ───────────────────────────────────
print("\n6. pre-migration degradation (no source_key column)")
fc = FakeClient(has_sk=False); use(fc)
res = asyncio.run(router.upsert_expense_system_line(JULY, {'source_key': 'pto_accrual', 'label': 'Paid Leave Accumulated',
    'cells': [{'store': 'S1', 'amount': 100}]}, org_id=ORGA))
check("receiver returns ok=False before mig 206", res.get('ok') is False)
check("hint names migration 206", '206' in str(res.get('hint', '')))
check("no rows written pre-migration", len(se(fc)) == 0)
# put_expenses still works pre-migration (falls back to unguarded delete; no system rows exist to protect)
se(fc).append({'org_id': ORGA, 'period': JULY, 'store_code': 'S1', 'expense_name': 'Rent / Lease',
               'expense_type': 'Variable', 'amount': 500})   # no source_key column
r2 = asyncio.run(router.put_expenses(JULY, {'rows': [
    {'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 700}]}, org_id=ORGA))
check("put_expenses still saves manual rows pre-migration", r2.get('saved') == 1)
check("Rent replaced to 700 (no leftover dup)",
      len([r for r in rows_for(fc, ORGA) if r['expense_name'] == 'Rent / Lease']) == 1
      and rows_for(fc, ORGA)[0]['amount'] == 700)
check("_system_line_keys empty pre-migration", _system_line_keys(fc, ORGA, _pvariants(JULY)) == set())
# _is_missing_col_err classification
check("_is_missing_col_err True for a 42703/source_key error", _is_missing_col_err(Exception("column store_expenses.source_key does not exist")))
check("_is_missing_col_err False for a transient/network error", not _is_missing_col_err(Exception("connection reset by peer / timeout")))


print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
