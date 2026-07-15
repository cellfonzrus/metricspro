"""Proof for the Store-Expenses "apply a source month across many months" command.

Drives the REAL pure expansion `_apply_to_months_expand`, the REAL config resolver `_expense_apply_tokens`,
and the REAL `_pvariants` (all from router.py) plus a faithful in-memory FakeClient that mirrors the
endpoint `POST /commcalc/expenses/apply-to-months`'s delete-then-insert DB path, to prove:
  1. commission + salary (the default protected tokens) are NEVER copied — dropped + reported as skipped;
  2. expansion is IDEMPOTENT — re-running the same inputs yields the identical rows/affected AND the
     identical table state (no duplicate rows) via the endpoint mirror;
  3. org-scoping — an apply for org-A never touches org-B's cells;
  4. period-spelling — the per-cell delete uses _pvariants so a differently-spelled prior row is REPLACED,
     not duplicated;
  5. per-cell (never whole-month wipe) — an unrelated manual cell in a target month survives;
  6. the selection narrows which expenses copy; a protected name in the selection is STILL dropped
     (defense in depth), and reported skipped;
  7. the source month is never a target (self-write dropped);
  8. `_expense_apply_tokens` reads config when present, else the code default {commission, salary}, and
     degrades to the default when the table is missing;
  9. a `source_cells` override (the page's live grid) is honored when the DB source month is empty.

Run: python3 backend/scratchpad/expenses_apply_months_proof.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.modules.commcalc.router import (
    _apply_to_months_expand, _expense_apply_tokens, _EXPENSE_APPLY_DEFAULT_TOKENS, _pvariants)

PASS = 0
FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


# ── FakeClient: mirrors PostgREST chaining used by the endpoint (delete-by-filters, insert, select) ─────
class _Q:
    def __init__(self, table): self.t = table; self._f = {}; self._in = {}; self._op = None; self._rows = None
    def delete(self): self._op = 'delete'; return self
    def insert(self, rows): self._op = 'insert'; self._rows = rows; return self
    def select(self, *_a, **_k): self._op = 'select'; return self
    def eq(self, k, v): self._f[k] = v; return self
    def in_(self, k, vs): self._in[k] = list(vs); return self
    def order(self, *_a, **_k): return self
    def _match(self, r):
        return all(r.get(k) == v for k, v in self._f.items()) and \
               all(r.get(k) in vs for k, vs in self._in.items())
    def execute(self):
        if self._op == 'delete':
            self.t.rows[:] = [r for r in self.t.rows if not self._match(r)]
        elif self._op == 'insert':
            self.t.rows.extend(dict(r) for r in self._rows)
        else:  # select
            self._data = [dict(r) for r in self.t.rows if self._match(r)]
        return self
    @property
    def data(self):
        return getattr(self, '_data', [])

class _Tbl:
    def __init__(self): self.rows = []
    def q(self): return _Q(self)

class _Schema:
    def __init__(self, c): self.c = c
    def table(self, name):
        if name in self.c._missing:
            raise RuntimeError(f"relation {name} does not exist")
        return self.c._tbls.setdefault(name, _Tbl()).q()

class FakeClient:
    def __init__(self, missing=()): self._tbls = {}; self._missing = set(missing)
    def schema(self, s): return _Schema(self)
    def seed(self, rows):
        self._tbls.setdefault('store_expenses', _Tbl()).rows.extend(dict(r) for r in rows)


# ── Faithful mirror of the router endpoint's DB body (over the REAL pure helpers) ──────────────────────
def apply_endpoint(client, org_id, body):
    source_period = str(body.get('source_period') or '').strip()
    if not source_period:
        return {"ok": False, "error": "source_period required"}
    targets = []
    for p in (body.get('target_periods') or []):
        p = str(p or '').strip()
        if p and p != source_period and p not in targets:
            targets.append(p)
    if not targets:
        return {"ok": False, "error": "no target_periods"}
    src_override = body.get('source_cells')
    if isinstance(src_override, list) and src_override:
        src_rows = src_override
    else:
        src_rows = (client.schema('commcalc').table('store_expenses')
                    .select('store_code,expense_name,expense_type,amount')
                    .eq('org_id', org_id).in_('period', _pvariants(source_period)).execute().data) or []
    excluded_tokens = _expense_apply_tokens(client, org_id)
    selection = body.get('expense_names')
    sel = selection if (isinstance(selection, list) and selection) else None
    rows, affected, skipped = _apply_to_months_expand(src_rows, targets, excluded_tokens, sel)
    for p, by_expense in affected.items():
        pv = _pvariants(p)
        for nm, scodes in by_expense.items():
            for i in range(0, len(scodes), 200):
                client.schema('commcalc').table('store_expenses').delete() \
                    .eq('org_id', org_id).in_('period', pv).eq('expense_name', nm) \
                    .in_('store_code', scodes[i:i + 200]).execute()
    ins = [{'org_id': org_id, **row} for row in rows]
    for i in range(0, len(ins), 500):
        client.schema('commcalc').table('store_expenses').insert(ins[i:i + 500]).execute()
    return {"ok": True, "source_period": source_period, "target_periods": targets,
            "months": len(targets), "cells": len(ins), "saved": len(ins),
            "copied_expenses": sorted({r['expense_name'] for r in rows}),
            "skipped_excluded": skipped, "excluded_tokens": excluded_tokens}

ORG = 'org-A'
def cells_for(client, org, period):
    pv = set(_pvariants(period))
    return {(r['store_code'], r['expense_name']): r['amount']
            for r in client._tbls['store_expenses'].rows if r['org_id'] == org and r['period'] in pv}


# Canonical source month (what the owner entered in July) — mixed protected + normal expenses.
SRC_JULY = [
    {'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 1200},
    {'store_code': 'S1', 'expense_name': 'Electric', 'expense_type': 'Variable', 'amount': 210},
    {'store_code': 'S2', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 1500},
    {'store_code': 'S1', 'expense_name': 'Employee Commission', 'expense_type': 'Fixed', 'amount': 3000},
    {'store_code': 'S1', 'expense_name': 'Employee Salaries', 'expense_type': 'Fixed', 'amount': 5000},
    {'store_code': 'S2', 'expense_name': 'Owner / Mgmt Salaries', 'expense_type': 'Fixed', 'amount': 4000},
]
TARGETS3 = ['April 2026', 'May 2026', 'June 2026']
DEF = list(_EXPENSE_APPLY_DEFAULT_TOKENS)  # ['commission','salary']


print("A. exclusion — commission & salary are NEVER copied (default tokens)")
rows, affected, skipped = _apply_to_months_expand(SRC_JULY, TARGETS3, DEF, None)
copied = sorted({r['expense_name'] for r in rows})
check("copied only Electric + Rent (no commission/salary)", copied == ['Electric', 'Rent / Lease'])
check("skipped = the 3 protected names",
      skipped == ['Employee Commission', 'Employee Salaries', 'Owner / Mgmt Salaries'])
check("Owner / Mgmt Salaries caught by the 'salary' token (substring)", 'Owner / Mgmt Salaries' in skipped)
check("rows span all 3 target months", {r['period'] for r in rows} == set(TARGETS3))
check("cell count = 2 normal cells (S1 Rent,Electric + S2 Rent) × 3 months = 9", len(rows) == 9)
check("affected has NO commission/salary keys",
      all('Commission' not in k and 'Salaries' not in k for m in affected.values() for k in m))
check("each target got S1.Rent=1200 and S2.Rent=1500",
      all(any(r['period'] == p and r['store_code'] == 'S1' and r['expense_name'] == 'Rent / Lease' and r['amount'] == 1200 for r in rows)
          and any(r['period'] == p and r['store_code'] == 'S2' and r['amount'] == 1500 for r in rows) for p in TARGETS3))


print("\nB. idempotent expansion — re-expand identical inputs = identical output")
rows2, affected2, skipped2 = _apply_to_months_expand(SRC_JULY, TARGETS3, DEF, None)
check("rows identical on re-expand", rows2 == rows)
check("affected identical on re-expand", affected2 == affected)
check("skipped identical on re-expand", skipped2 == skipped)


print("\nC. selection narrows copy; a protected name in the selection is STILL dropped")
r_sel, _, sk_sel = _apply_to_months_expand(SRC_JULY, TARGETS3, DEF, ['Rent / Lease'])
check("selection=[Rent] → only Rent copied", sorted({r['expense_name'] for r in r_sel}) == ['Rent / Lease'])
check("selection=[Rent] → Electric not copied (not selected)", all(r['expense_name'] != 'Electric' for r in r_sel))
check("selection=[Rent] → nothing reported skipped (commission wasn't requested)", sk_sel == [])
r_dz, _, sk_dz = _apply_to_months_expand(SRC_JULY, TARGETS3, DEF, ['Rent / Lease', 'Employee Commission'])
check("defense-in-depth: commission in selection is DROPPED", all(r['expense_name'] != 'Employee Commission' for r in r_dz))
check("defense-in-depth: commission reported skipped", sk_dz == ['Employee Commission'])


print("\nD. endpoint mirror — idempotent DB re-apply (no duplicate rows), never a whole-month wipe")
c = FakeClient()
c.seed([{'org_id': ORG, 'period': 'July 2026', **{k: v for k, v in cell.items()}} for cell in SRC_JULY])
# a MANUAL prior-month cell that must survive (not in the source): June S1 Water
c.seed([{'org_id': ORG, 'period': 'June 2026', 'store_code': 'S1', 'expense_name': 'Water', 'expense_type': 'Variable', 'amount': 60}])
res = apply_endpoint(c, ORG, {'source_period': 'July 2026', 'target_periods': TARGETS3})
check("endpoint ok, months=3", res['ok'] and res['months'] == 3)
check("endpoint skipped = the 3 protected names", res['skipped_excluded'] == ['Employee Commission', 'Employee Salaries', 'Owner / Mgmt Salaries'])
check("endpoint cells=9 written", res['cells'] == 9)
june = cells_for(c, ORG, 'June 2026')
check("June got S1.Rent=1200 + S1.Electric=210 + S2.Rent=1500", june[('S1', 'Rent / Lease')] == 1200 and june[('S1', 'Electric')] == 210 and june[('S2', 'Rent / Lease')] == 1500)
check("June did NOT receive commission/salary", ('S1', 'Employee Commission') not in june and ('S1', 'Employee Salaries') not in june)
check("manual June S1.Water=60 SURVIVED (per-cell, no whole-month wipe)", june[('S1', 'Water')] == 60)
snap = sorted((r['org_id'], r['period'], r['store_code'], r['expense_name'], r['amount']) for r in c._tbls['store_expenses'].rows)
apply_endpoint(c, ORG, {'source_period': 'July 2026', 'target_periods': TARGETS3})
snap2 = sorted((r['org_id'], r['period'], r['store_code'], r['expense_name'], r['amount']) for r in c._tbls['store_expenses'].rows)
check("re-apply is idempotent — identical table state", snap2 == snap)
june_rent = [r for r in c._tbls['store_expenses'].rows if r['period'] == 'June 2026' and r['store_code'] == 'S1' and r['expense_name'] == 'Rent / Lease']
check("no duplicate physical June S1.Rent row after re-apply", len(june_rent) == 1)


print("\nE. org-scoping — apply for org-A never touches org-B's target cell")
c = FakeClient()
c.seed([{'org_id': ORG, 'period': 'July 2026', 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 1200}])
c.seed([{'org_id': 'org-B', 'period': 'June 2026', 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 9999}])
apply_endpoint(c, ORG, {'source_period': 'July 2026', 'target_periods': ['June 2026']})
b_rows = [r for r in c._tbls['store_expenses'].rows if r['org_id'] == 'org-B']
check("org-B June S1.Rent untouched (=9999, single row)", len(b_rows) == 1 and b_rows[0]['amount'] == 9999)
check("org-A June S1.Rent written (=1200)", cells_for(c, ORG, 'June 2026')[('S1', 'Rent / Lease')] == 1200)


print("\nF. period-spelling — a differently-spelled prior target row is REPLACED, not duplicated")
c = FakeClient()
c.seed([{'org_id': ORG, 'period': 'July 2026', 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 1200}])
# June already holds a value under the OTHER spelling '2026-06' for the same cell
c.seed([{'org_id': ORG, 'period': '2026-06', 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 700}])
apply_endpoint(c, ORG, {'source_period': 'July 2026', 'target_periods': ['June 2026']})
rent = [r for r in c._tbls['store_expenses'].rows if r['store_code'] == 'S1' and r['expense_name'] == 'Rent / Lease' and r['period'] in _pvariants('June 2026')]
check("old '2026-06' row replaced (single June row @ 1200), no spelling dup", len(rent) == 1 and rent[0]['amount'] == 1200)


print("\nG. source month is never a target (self-write dropped)")
c = FakeClient()
c.seed([{'org_id': ORG, 'period': 'July 2026', 'store_code': 'S1', 'expense_name': 'Rent / Lease', 'expense_type': 'Variable', 'amount': 1200}])
res = apply_endpoint(c, ORG, {'source_period': 'July 2026', 'target_periods': ['July 2026', 'June 2026']})
check("July dropped from targets → months=1 (June only)", res['ok'] and res['target_periods'] == ['June 2026'])


print("\nH. _expense_apply_tokens — config when present, else code default, else default on missing table")
c = FakeClient()
check("empty config → code default (commission/salary/salaries)", _expense_apply_tokens(c, ORG) == DEF)
c._tbls.setdefault('expense_apply_config', _Tbl()).rows.extend([
    {'org_id': ORG, 'token': 'commission'}, {'org_id': ORG, 'token': 'salary'}, {'org_id': ORG, 'token': 'rent'}])
check("configured org tokens returned (incl a custom 'rent')", sorted(_expense_apply_tokens(c, ORG)) == ['commission', 'rent', 'salary'])
check("another org still gets the default (org-scoped read)", _expense_apply_tokens(c, 'org-Z') == DEF)
cmiss = FakeClient(missing=['expense_apply_config'])
check("missing table (pre-mig-205) degrades to default", _expense_apply_tokens(cmiss, ORG) == DEF)
# a custom token config actually protects a normally-copied expense
r_rent, _, sk_rent = _apply_to_months_expand(SRC_JULY, ['June 2026'], ['commission', 'salary', 'rent'], None)
check("custom 'rent' token now protects Rent / Lease", all(r['expense_name'] != 'Rent / Lease' for r in r_rent) and 'Rent / Lease' in sk_rent)


print("\nI. source_cells override (live grid) honored when the DB source month is empty")
c = FakeClient()  # no July rows in the DB at all
res = apply_endpoint(c, ORG, {'source_period': 'July 2026', 'target_periods': ['June 2026'],
                              'source_cells': [{'store_code': 'S1', 'expense_name': 'Internet', 'expense_type': 'Fixed', 'amount': 80}]})
check("override copied Internet=80 into June even with empty DB source", cells_for(c, ORG, 'June 2026').get(('S1', 'Internet')) == 80)


print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
