"""Proof for the Store-Expenses bulk-apply batch endpoint.

Drives the REAL pure expansion `_bulk_apply_expand` (router.py) and a faithful in-memory FakeClient that
mirrors the endpoint's delete-then-insert-nonzero DB path, to prove:
  1. payload → (clear set, insert rows) expansion is correct for the two features
     (copy-column-to-many, multi-store common expense) and the clipboard multi-row case;
  2. amount 0 CLEARS a cell (delete, no re-insert);
  3. the write is IDEMPOTENT (re-running the same payload yields the identical table state);
  4. the batch touches ONLY the payload's (store, expense) cells — every other cell is untouched;
  5. org-scoping (a delete/insert never crosses org_id);
  6. last-write-wins on a duplicated (store, expense) pair in one payload.

Run: python3 backend/scratchpad/expenses_bulk_proof.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.modules.commcalc.router import _bulk_apply_expand

PASS = 0
FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


# ── FakeClient: mirrors the endpoint's DB path (delete-by-filters, insert) over a python list ──────────
class _Q:
    def __init__(self, table): self.t = table; self._f = {}; self._in = {}
    def delete(self): self._op = 'delete'; return self
    def insert(self, rows): self._op = 'insert'; self._rows = rows; return self
    def eq(self, k, v): self._f[k] = v; return self
    def in_(self, k, vs): self._in[k] = list(vs); return self
    def execute(self):
        if self._op == 'delete':
            keep = []
            for r in self.t.rows:
                match = all(r.get(k) == v for k, v in self._f.items()) and \
                        all(r.get(k) in vs for k, vs in self._in.items())
                if not match: keep.append(r)
            self.t.rows[:] = keep
        else:
            self.t.rows.extend(dict(r) for r in self._rows)
        return self

class _Tbl:
    def __init__(self): self.rows = []
    def q(self): return _Q(self)

class _Schema:
    def __init__(self, c): self.c = c
    def table(self, name): return self.c._tbls.setdefault(name, _Tbl()).q()

class FakeClient:
    def __init__(self): self._tbls = {}
    def schema(self, s): return _Schema(self)


# Replicates bulk_apply_expenses' DB body exactly (over the real _bulk_apply_expand output).
def do_bulk_apply(client, org_id, period, cells, pvariants):
    by_expense, ins_bare, cleared = _bulk_apply_expand(cells)
    for nm, stores in by_expense.items():
        scodes = list(stores.keys())
        for i in range(0, len(scodes), 200):
            client.schema('commcalc').table('store_expenses').delete() \
                .eq('org_id', org_id).in_('period', pvariants).eq('expense_name', nm) \
                .in_('store_code', scodes[i:i + 200]).execute()
    ins = [{'org_id': org_id, 'period': period, **row} for row in ins_bare]
    for i in range(0, len(ins), 500):
        client.schema('commcalc').table('store_expenses').insert(ins[i:i + 500]).execute()
    return {'saved': len(ins), 'cleared': cleared,
            'stores': len({sc for st in by_expense.values() for sc in st}),
            'expenses': len(by_expense)}

ORG = 'org-A'; PV = ['July 2026', '2026-07']; PERIOD = 'July 2026'
def cellmap(client):
    return {(r['store_code'], r['expense_name']): r['amount']
            for r in client._tbls['store_expenses'].rows if r['org_id'] == ORG and r['period'] in PV}


print("A. pure expansion — _bulk_apply_expand")
by, ins, cleared = _bulk_apply_expand([
    {'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 1200},
    {'store_code': 'S2', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 0},
    {'store_code': ' S3 ', 'expense_name': ' Rent / Lease ', 'amount': '  1300  '},   # trims + str amount
])
check("groups by expense name", list(by.keys()) == ['Rent / Lease'])
check("affected stores incl the 0-cell (to clear)", set(by['Rent / Lease'].keys()) == {'S1', 'S2', 'S3'})
check("insert rows are the NON-zero cells only", sorted(r['store_code'] for r in ins) == ['S1', 'S3'])
check("cleared counts the 0-cell", cleared == 1)
check("string amount parsed (S3=1300)", any(r['store_code'] == 'S3' and r['amount'] == 1300 for r in ins))
check("default type Fixed when absent (S3)", any(r['store_code'] == 'S3' and r['expense_type'] == 'Fixed' for r in ins))
by2, ins2, _ = _bulk_apply_expand([
    {'store_code': '', 'expense_name': 'Rent', 'amount': 5},          # blank store dropped
    {'store_code': 'S1', 'expense_name': '', 'amount': 5},            # blank expense dropped
    {'store_code': 'S1', 'expense_name': 'Rent', 'amount': 'oops'},   # unparseable → 0 → cleared
])
check("blank store/expense cells dropped", by2 == {'Rent': {'S1': {'type': 'Fixed', 'amount': 0}}})
check("unparseable amount → 0 (no insert)", ins2 == [])
by3, ins3, _ = _bulk_apply_expand([
    {'store_code': 'S1', 'expense_name': 'Rent', 'amount': 100},
    {'store_code': 'S1', 'expense_name': 'Rent', 'amount': 250},      # dup pair → last wins
])
check("last-write-wins on dup (store,expense) pair", ins3 == [{'store_code': 'S1', 'expense_name': 'Rent', 'expense_type': 'Fixed', 'amount': 250}])


print("\nB. multi-store COMMON EXPENSE — same amount to many stores in one submit")
c = FakeClient()
# pre-existing unrelated cell that must NOT be touched
c._tbls.setdefault('store_expenses', _Tbl()).rows.append(
    {'org_id': ORG, 'period': PERIOD, 'store_code': 'S9', 'expense_name': 'Electric', 'expense_type': 'Variable', 'amount': 77})
common = [{'store_code': s, 'expense_name': 'ADT Security', 'expense_type': 'Fixed', 'amount': 49.99} for s in ['S1', 'S2', 'S3', 'S4']]
res = do_bulk_apply(c, ORG, PERIOD, common, PV)
m = cellmap(c)
check("common expense written to all 4 stores", all(m[(s, 'ADT Security')] == 49.99 for s in ['S1', 'S2', 'S3', 'S4']))
check("saved=4 stores=4 expenses=1", (res['saved'], res['stores'], res['expenses']) == (4, 4, 1))
check("unrelated S9/Electric cell untouched", m[('S9', 'Electric')] == 77)
# re-apply identical payload → idempotent
before = sorted(cellmap(c).items())
do_bulk_apply(c, ORG, PERIOD, common, PV)
check("idempotent re-apply (identical table state, no dup rows)", sorted(cellmap(c).items()) == before)
check("no duplicate physical rows after re-apply", len([r for r in c._tbls['store_expenses'].rows if r['store_code'] == 'S1' and r['expense_name'] == 'ADT Security']) == 1)
# change the common amount → overwrites, doesn't stack
do_bulk_apply(c, ORG, PERIOD, [{'store_code': s, 'expense_name': 'ADT Security', 'expense_type': 'Fixed', 'amount': 60} for s in ['S1', 'S2', 'S3', 'S4']], PV)
check("re-apply with new amount overwrites (S1=60, single row)", cellmap(c)[('S1', 'ADT Security')] == 60)


print("\nC. COPY ONE COLUMN → MANY (with a 0 that clears the target)")
c = FakeClient()
c._tbls.setdefault('store_expenses', _Tbl())
# source S1 column: Rent 1000, Electric 200, Internet 0 (blank). Target S2 has a stale Internet=500.
seed = [
    {'org_id': ORG, 'period': PERIOD, 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 1000},
    {'org_id': ORG, 'period': PERIOD, 'store_code': 'S1', 'expense_name': 'Electric', 'expense_type': 'Variable', 'amount': 200},
    {'org_id': ORG, 'period': PERIOD, 'store_code': 'S2', 'expense_name': 'Internet', 'expense_type': 'Fixed', 'amount': 500},
]
c._tbls['store_expenses'].rows.extend(seed)
# copy S1's full column (every category) onto S2 + S3: Internet=0 in the source must CLEAR S2's stale 500
cats = [('Rent / Lease', 'Variable', 1000), ('Electric', 'Variable', 200), ('Internet', 'Fixed', 0)]
copy_cells = []
for tc in ['S2', 'S3']:
    for nm, tp, amt in cats:
        copy_cells.append({'store_code': tc, 'expense_name': nm, 'expense_type': tp, 'amount': amt})
res = do_bulk_apply(c, ORG, PERIOD, copy_cells, PV)
m = cellmap(c)
check("S2 now mirrors S1 (Rent=1000, Electric=200)", m.get(('S2', 'Rent / Lease')) == 1000 and m.get(('S2', 'Electric')) == 200)
check("S3 now mirrors S1", m.get(('S3', 'Rent / Lease')) == 1000 and m.get(('S3', 'Electric')) == 200)
check("source S1 column untouched", m.get(('S1', 'Rent / Lease')) == 1000 and m.get(('S1', 'Electric')) == 200)
check("0 in source CLEARED S2's stale Internet=500", ('S2', 'Internet') not in m)
check("cleared=2 (Internet 0 for S2 + S3)", res['cleared'] == 2)


print("\nD. clipboard multi-row paste — same payload shape (one store, many expenses)")
c = FakeClient(); c._tbls.setdefault('store_expenses', _Tbl())
paste = [{'store_code': 'S5', 'expense_name': nm, 'expense_type': 'Fixed', 'amount': amt}
         for nm, amt in [('Rent / Lease', 900), ('B2B Platform Fee', 150), ('Cellsmart POS', 75)]]
do_bulk_apply(c, ORG, PERIOD, paste, PV)
m = cellmap(c)
check("all 3 pasted cells written to S5", m[('S5', 'Rent / Lease')] == 900 and m[('S5', 'B2B Platform Fee')] == 150 and m[('S5', 'Cellsmart POS')] == 75)


print("\nE. org-scoping — a bulk apply for org-A never touches org-B's identical cell")
c = FakeClient(); c._tbls.setdefault('store_expenses', _Tbl())
c._tbls['store_expenses'].rows.append(
    {'org_id': 'org-B', 'period': PERIOD, 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 4242})
do_bulk_apply(c, ORG, PERIOD, [{'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 111}], PV)
b_rows = [r for r in c._tbls['store_expenses'].rows if r['org_id'] == 'org-B']
check("org-B's S1/Rent cell survived untouched (=4242)", len(b_rows) == 1 and b_rows[0]['amount'] == 4242)
check("org-A's S1/Rent written (=111)", cellmap(c)[('S1', 'Rent / Lease')] == 111)


print("\nF. period-spelling — delete uses _pvariants so a differently-spelled prior row is replaced")
c = FakeClient(); c._tbls.setdefault('store_expenses', _Tbl())
c._tbls['store_expenses'].rows.append(   # stored under the OTHER spelling
    {'org_id': ORG, 'period': '2026-07', 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 800})
do_bulk_apply(c, ORG, PERIOD, [{'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 950}], PV)
rent_rows = [r for r in c._tbls['store_expenses'].rows if r['store_code'] == 'S1' and r['expense_name'] == 'Rent / Lease']
check("old '2026-07' row replaced (not duplicated) — one row @ 950", len(rent_rows) == 1 and rent_rows[0]['amount'] == 950)


print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
