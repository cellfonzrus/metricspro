"""Proof for the carrier payout-template CLONER (mig 221).

Drives the REAL template_clone.list_shared_sources / clone_carrier_template and the REAL router gate
_require_carrier_template_edit against a faithful in-memory FakeClient (supabase-py chain: schema/table/
select/eq/in_/insert/update/delete/execute, with per-table COLUMN sets so a missing template_shared column
raises exactly like Postgres pre-mig-221). Proves:

  1. /sources lists ONLY template_shared=true carriers (regardless of org), with correct sched/line/mrc
     counts; NULL-carrier product_mrc rows are excluded (carrier-scoped only).
  2. dry-run manifest is correct AND writes nothing.
  3. real clone creates re-stamped rows (new UUIDs, target org_id, FKs remapped) and MUTATES NO source row.
  4. idempotent re-clone skips everything (0 created, all skipped).
  5. a hand-edited tenant copy survives a re-clone unchanged.
  6. a non-shared source is REFUSED (403).
  7. org isolation: a target can't clone an UNSHARED foreign carrier (403) and never sees it in /sources.
  8. pre-mig-221 (no template_shared column): /sources degrades ready=false; clone refuses (400) — no leak.
  9. company-scoped source schedules are NOT cloned cross-tenant.
 10. permission gate matrix (real _require_carrier_template_edit → real _can_edit_setting).

Run: python3 backend/scratchpad/carrier_template_clone_proof.py
"""
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.modules.commcalc import template_clone
import app.modules.commcalc.router as cr
import app.modules.core.router as core
from fastapi import HTTPException

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name}")

HOUSE = '00000000-0000-0000-0000-000000000001'
LUX   = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
ACME  = 'aaaaaaaa-0000-0000-0000-000000000009'


# ── FakeClient — faithful supabase-py chain with per-table column sets ─────────────────────────────
class _Exec:
    def __init__(self, data): self.data = data

class _Q:
    def __init__(self, table): self.t = table; self._op='select'; self._f=[]; self._rows=None; self._upd=None; self._cols='*'
    def select(self, cols='*'): self._op='select'; self._cols=cols; return self
    def insert(self, rows): self._op='insert'; self._rows = rows if isinstance(rows, list) else [rows]; return self
    def update(self, patch): self._op='update'; self._upd=patch; return self
    def delete(self): self._op='delete'; return self
    def eq(self, k, v): self._f.append(('eq', k, v)); return self
    def in_(self, k, vs): self._f.append(('in', k, list(vs))); return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    def range(self, a, b): return self
    def _match(self, r):
        for op, k, v in self._f:
            if op == 'eq' and r.get(k) != v: return False
            if op == 'in' and r.get(k) not in v: return False
        return True
    def execute(self):
        # PG-like: referencing a non-existent column raises
        for op, k, v in self._f:
            if k not in self.t.cols:
                raise Exception(f"column {self.t.name}.{k} does not exist")
        if self._op == 'insert':
            for r in self._rows:
                for k in r:
                    if k not in self.t.cols:
                        raise Exception(f"column {self.t.name}.{k} does not exist")
                self.t.rows.append(dict(r))
            return _Exec([dict(r) for r in self._rows])
        matched = [r for r in self.t.rows if self._match(r)]
        if self._op == 'delete':
            self.t.rows[:] = [r for r in self.t.rows if not self._match(r)]; return _Exec(matched)
        if self._op == 'update':
            for r in matched: r.update(self._upd)
            return _Exec(matched)
        # PG-faithful column projection: a select of specific columns returns ONLY those (and errors on
        # a non-existent selected column) — so an under-narrowed read is CAUGHT here.
        if self._cols and self._cols != '*':
            cols = [x.strip() for x in self._cols.split(',')]
            for k in cols:
                if k not in self.t.cols:
                    raise Exception(f"column {self.t.name}.{k} does not exist")
            return _Exec([{k: r.get(k) for k in cols} for r in matched])
        return _Exec([dict(r) for r in matched])

class _Tbl:
    def __init__(self, name, cols): self.name=name; self.cols=set(cols); self.rows=[]

class _Schema:
    def __init__(self, c): self.c=c
    def table(self, name): return _Q(self.c.tbls[name])

class FakeClient:
    def __init__(self, with_shared_col=True):
        carrier_cols = ['id','org_id','name','code','is_default','created_at']
        if with_shared_col: carrier_cols.append('template_shared')
        self.tbls = {
            'carrier': _Tbl('carrier', carrier_cols),
            'payout_schedule': _Tbl('payout_schedule', ['id','org_id','company_id','carrier_id','activation_type','num_months','gate_signal','bypass_tier','is_active','created_at']),
            'payout_schedule_line': _Tbl('payout_schedule_line', ['id','org_id','schedule_id','month_index','payout_kind','flat_amount','mrc_pct','mrc_basis','requires_paid']),
            'product_mrc': _Tbl('product_mrc', ['id','org_id','carrier_id','plan_pattern','match_op','mrc','priority','is_active','note','created_at','updated_at']),
        }
    def schema(self, s): return _Schema(self)


def seed_house_total(c):
    """Seed the house Total Wireless template: carrier (shared) + 3 schedules (6+5+1 lines) + 2 carrier
    product_mrc + 1 NULL-carrier product_mrc (must NOT be part of the template)."""
    tw = str(uuid.uuid4())
    c.tbls['carrier'].rows.append({'id': tw, 'org_id': HOUSE, 'name': 'Total Wireless', 'code': 'Total', 'is_default': False, 'template_shared': True})
    # Boost (NOT shared)
    c.tbls['carrier'].rows.append({'id': str(uuid.uuid4()), 'org_id': HOUSE, 'name': 'Boost', 'code': 'BOOST', 'is_default': True, 'template_shared': False})
    def sched(at, n, kind_lines):
        sid = str(uuid.uuid4())
        c.tbls['payout_schedule'].rows.append({'id': sid, 'org_id': HOUSE, 'company_id': None, 'carrier_id': tw,
            'activation_type': at, 'num_months': n, 'gate_signal': 'paid_residual', 'bypass_tier': True, 'is_active': True})
        for i, (kind, amt, pct) in enumerate(kind_lines, 1):
            c.tbls['payout_schedule_line'].rows.append({'id': str(uuid.uuid4()), 'org_id': HOUSE, 'schedule_id': sid,
                'month_index': i, 'payout_kind': kind, 'flat_amount': amt, 'mrc_pct': pct,
                'mrc_basis': 'commissionable_mrc', 'requires_paid': i > 1})
        return sid
    sched('*', 6, [('pct_mrc', 0, 0.5)] + [('pct_mrc', 0, 0.75)] * 5)     # 6 lines
    sched('edge', 5, [('pct_mrc', 0, 1.0)] * 3 + [('pct_mrc', 0, 0.75)] * 2)  # 5 lines
    sched('upgrade_edge', 1, [('flat', 25.0, 0)])                          # 1 line
    c.tbls['product_mrc'].rows.append({'id': str(uuid.uuid4()), 'org_id': HOUSE, 'carrier_id': tw, 'plan_pattern': 'Total Unlimited $60', 'match_op': 'equals', 'mrc': 60.0, 'priority': 100, 'is_active': True, 'note': None})
    c.tbls['product_mrc'].rows.append({'id': str(uuid.uuid4()), 'org_id': HOUSE, 'carrier_id': tw, 'plan_pattern': 'Total 5GB $35', 'match_op': 'equals', 'mrc': 35.0, 'priority': 100, 'is_active': True, 'note': None})
    c.tbls['product_mrc'].rows.append({'id': str(uuid.uuid4()), 'org_id': HOUSE, 'carrier_id': None, 'plan_pattern': 'ANY', 'match_op': 'contains', 'mrc': 10.0, 'priority': 200, 'is_active': True, 'note': 'any-carrier fallback'})
    # a foreign, UNSHARED carrier in another org (org-isolation target)
    sec = str(uuid.uuid4())
    c.tbls['carrier'].rows.append({'id': sec, 'org_id': ACME, 'name': 'Secret Carrier', 'code': 'SEC', 'is_default': False, 'template_shared': False})
    return tw, sec


print("A. /sources — only shared carriers, correct counts")
c = FakeClient(); tw, sec = seed_house_total(c)
src = template_clone.list_shared_sources(c, LUX)
check("ready true", src['ready'] is True)
check("exactly one shared source (Total Wireless)", len(src['sources']) == 1 and src['sources'][0]['carrier_name'] == 'Total Wireless')
s0 = src['sources'][0]
check("source is the house Total Wireless", s0['source_org_id'] == HOUSE and s0['source_carrier_id'] == tw)
check("schedule_count = 3", s0['schedule_count'] == 3)
check("line_count = 12 (6+5+1)", s0['line_count'] == 12)
check("product_mrc_count = 2 (carrier-scoped; NULL-carrier excluded)", s0['product_mrc_count'] == 2)
check("is_own false for LUX target", s0['is_own'] is False)
check("Boost (unshared) absent", all(x['carrier_name'] != 'Boost' for x in src['sources']))
check("Secret (unshared foreign) absent", all(x['carrier_name'] != 'Secret Carrier' for x in src['sources']))


print("\nB. dry-run manifest correct + writes nothing")
man = template_clone.clone_carrier_template(c, target_org_id=LUX, source_org_id=HOUSE, source_carrier_id=tw, dry_run=True)
check("dry_run flag true", man['dry_run'] is True)
check("carrier action=create (LUX has none)", man['carrier']['action'] == 'create' and man['carrier']['id'] is None)
check("schedules_created=3", man['counts']['schedules_created'] == 3)
check("lines_created=12", man['counts']['lines_created'] == 12)
check("product_mrc_created=2", man['counts']['product_mrc_created'] == 2)
check("nothing skipped", man['counts']['schedules_skipped'] == 0 and man['counts']['product_mrc_skipped'] == 0)
check("manifest create list has no ids (dry-run)", all(x['id'] is None for x in man['schedules']['create']))
check("DB untouched: LUX has 0 carriers", len([r for r in c.tbls['carrier'].rows if r['org_id'] == LUX]) == 0)
check("DB untouched: 0 LUX schedules/lines/mrc", all(len([r for r in c.tbls[t].rows if r['org_id'] == LUX]) == 0 for t in ('payout_schedule','payout_schedule_line','product_mrc')))


print("\nC. real clone — re-stamped rows, remapped FKs, source untouched")
house_carrier_ids_before = {r['id'] for r in c.tbls['carrier'].rows if r['org_id'] == HOUSE}
house_sched_ids_before = {r['id'] for r in c.tbls['payout_schedule'].rows if r['org_id'] == HOUSE}
res = template_clone.clone_carrier_template(c, target_org_id=LUX, source_org_id=HOUSE, source_carrier_id=tw, dry_run=False)
check("carriers_created=1", res['counts']['carriers_created'] == 1)
check("schedules_created=3 lines=12 mrc=2", (res['counts']['schedules_created'], res['counts']['lines_created'], res['counts']['product_mrc_created']) == (3, 12, 2))
lux_carr = [r for r in c.tbls['carrier'].rows if r['org_id'] == LUX]
check("LUX has exactly 1 carrier named Total Wireless", len(lux_carr) == 1 and lux_carr[0]['name'] == 'Total Wireless')
lc = lux_carr[0]
check("LUX carrier has a NEW uuid (not the house id)", lc['id'] != tw and lc['id'] not in house_carrier_ids_before)
check("LUX carrier is_default False", lc['is_default'] is False)
check("LUX carrier template_shared False (not itself re-shared)", lc.get('template_shared') is False)
lux_sched = [r for r in c.tbls['payout_schedule'].rows if r['org_id'] == LUX]
check("LUX has 3 schedules, all org=LUX", len(lux_sched) == 3 and all(r['org_id'] == LUX for r in lux_sched))
check("every LUX schedule carrier_id remapped to the LUX carrier", all(r['carrier_id'] == lc['id'] for r in lux_sched))
check("every LUX schedule company_id NULL", all(r['company_id'] is None for r in lux_sched))
check("LUX schedule ids are all new (none shared with house)", not ({r['id'] for r in lux_sched} & house_sched_ids_before))
lux_sids = {r['id'] for r in lux_sched}
lux_lines = [r for r in c.tbls['payout_schedule_line'].rows if r['org_id'] == LUX]
check("LUX has 12 lines, all org=LUX", len(lux_lines) == 12 and all(r['org_id'] == LUX for r in lux_lines))
check("every LUX line schedule_id points to a LUX schedule (FK remap)", all(r['schedule_id'] in lux_sids for r in lux_lines))
lux_mrc = [r for r in c.tbls['product_mrc'].rows if r['org_id'] == LUX]
check("LUX has 2 product_mrc, carrier remapped, org=LUX", len(lux_mrc) == 2 and all(r['carrier_id'] == lc['id'] and r['org_id'] == LUX for r in lux_mrc))
check("NULL-carrier fallback MRC NOT cloned", all(r['plan_pattern'] != 'ANY' for r in lux_mrc))
# source integrity
check("HOUSE carriers unchanged (same ids)", {r['id'] for r in c.tbls['carrier'].rows if r['org_id'] == HOUSE} == house_carrier_ids_before)
check("HOUSE schedules unchanged (same ids, still 3)", {r['id'] for r in c.tbls['payout_schedule'].rows if r['org_id'] == HOUSE} == house_sched_ids_before)
check("HOUSE still has 3 product_mrc (2 carrier + 1 NULL)", len([r for r in c.tbls['product_mrc'].rows if r['org_id'] == HOUSE]) == 3)


print("\nD. idempotent re-clone — skips everything")
res2 = template_clone.clone_carrier_template(c, target_org_id=LUX, source_org_id=HOUSE, source_carrier_id=tw, dry_run=False)
check("carrier action=match", res2['carrier']['action'] == 'match' and res2['carrier']['id'] == lc['id'])
check("0 created on re-clone", (res2['counts']['carriers_created'], res2['counts']['schedules_created'], res2['counts']['lines_created'], res2['counts']['product_mrc_created']) == (0, 0, 0, 0))
check("all skipped (3 sched, 2 mrc)", res2['counts']['schedules_skipped'] == 3 and res2['counts']['product_mrc_skipped'] == 2)
check("LUX still has 3 sched / 12 lines / 2 mrc (no duplication)", (len([r for r in c.tbls['payout_schedule'].rows if r['org_id']==LUX]), len([r for r in c.tbls['payout_schedule_line'].rows if r['org_id']==LUX]), len([r for r in c.tbls['product_mrc'].rows if r['org_id']==LUX])) == (3, 12, 2))
check("LUX still exactly 1 carrier (not duplicated)", len([r for r in c.tbls['carrier'].rows if r['org_id']==LUX]) == 1)


print("\nE. hand-edited tenant copy survives re-clone")
edited = [r for r in c.tbls['payout_schedule_line'].rows if r['org_id'] == LUX][0]
edited['flat_amount'] = 999.99  # owner tweaks a LUX line
edited['mrc_pct'] = 0.42
res3 = template_clone.clone_carrier_template(c, target_org_id=LUX, source_org_id=HOUSE, source_carrier_id=tw, dry_run=False)
still = [r for r in c.tbls['payout_schedule_line'].rows if r['org_id'] == LUX and r['id'] == edited['id']]
check("edited line still present, value preserved", len(still) == 1 and still[0]['flat_amount'] == 999.99 and still[0]['mrc_pct'] == 0.42)
check("re-clone created nothing", res3['counts']['schedules_created'] == 0 and res3['counts']['lines_created'] == 0)


print("\nF. non-shared source refused (Boost)")
boost = [r for r in c.tbls['carrier'].rows if r['org_id'] == HOUSE and r['name'] == 'Boost'][0]
try:
    template_clone.clone_carrier_template(c, target_org_id=LUX, source_org_id=HOUSE, source_carrier_id=boost['id'], dry_run=True)
    check("Boost clone raised", False)
except HTTPException as e:
    check("Boost (unshared) refused 403", e.status_code == 403)


print("\nG. org isolation — target cannot clone an unshared FOREIGN carrier")
try:
    template_clone.clone_carrier_template(c, target_org_id=LUX, source_org_id=ACME, source_carrier_id=sec, dry_run=True)
    check("Secret clone raised", False)
except HTTPException as e:
    check("unshared foreign carrier refused 403", e.status_code == 403)
check("LUX gained nothing from the refused foreign clone", len([r for r in c.tbls['carrier'].rows if r['org_id']==LUX]) == 1)
# even naming the foreign org with a shared carrier id from ANOTHER org must not cross (id/org mismatch)
try:
    template_clone.clone_carrier_template(c, target_org_id=LUX, source_org_id=ACME, source_carrier_id=tw, dry_run=True)
    check("mismatched org/carrier raised", False)
except HTTPException as e:
    check("carrier id under wrong org refused", e.status_code == 403)


print("\nH. pre-mig-221 (no template_shared column) — degrades safe, no leak")
c2 = FakeClient(with_shared_col=False); seed_pre = None
# seed carriers/scheds without template_shared (column absent)
tw2 = str(uuid.uuid4())
c2.tbls['carrier'].rows.append({'id': tw2, 'org_id': HOUSE, 'name': 'Total Wireless', 'code': 'Total', 'is_default': False})
srcp = template_clone.list_shared_sources(c2, LUX)
check("pre-mig /sources ready=false, empty", srcp['ready'] is False and srcp['sources'] == [])
try:
    template_clone.clone_carrier_template(c2, target_org_id=LUX, source_org_id=HOUSE, source_carrier_id=tw2, dry_run=True)
    check("pre-mig clone raised", False)
except HTTPException as e:
    check("pre-mig clone refused 400 (sharing not enabled)", e.status_code == 400)


print("\nI. company-scoped source schedule not cloned cross-tenant")
c3 = FakeClient(); tw3, _ = seed_house_total(c3)
comp_sid = str(uuid.uuid4())
c3.tbls['payout_schedule'].rows.append({'id': comp_sid, 'org_id': HOUSE, 'company_id': 'company-house-1', 'carrier_id': tw3,
    'activation_type': 'lifeline_ca', 'num_months': 1, 'gate_signal': 'paid_residual', 'bypass_tier': True, 'is_active': True})
c3.tbls['payout_schedule_line'].rows.append({'id': str(uuid.uuid4()), 'org_id': HOUSE, 'schedule_id': comp_sid, 'month_index': 1, 'payout_kind': 'flat', 'flat_amount': 35.0, 'mrc_pct': 0, 'mrc_basis': 'commissionable_mrc', 'requires_paid': False})
mancs = template_clone.clone_carrier_template(c3, target_org_id=LUX, source_org_id=HOUSE, source_carrier_id=tw3, dry_run=True)
check("company-scoped schedule reported skipped", mancs['counts']['schedules_company_skipped'] == 1)
check("company-scoped NOT in created (still only the 3 carrier-level)", mancs['counts']['schedules_created'] == 3)
check("company_skipped manifest names the activation_type", any(x['activation_type'] == 'lifeline_ca' for x in mancs['schedules']['company_skipped']))


print("\nJ. permission gate matrix — real _require_carrier_template_edit → real _can_edit_setting")
cr.sb = lambda: object()                                   # gate only needs a placeholder client
core._uid_from_token = lambda auth: ('uid' if auth else None)
def gate_with(caller):
    # stub is 3-ARG (client, uid, active_org) — the real gate now passes the ACTING org (F1). A 2-arg
    # stub would TypeError → resolution-except → caller=None → wrongly allowed, so this shape is required.
    core._resolve_caller = lambda client, uid, active_org=None: caller
    return cr._require_carrier_template_edit('Bearer x', LUX)
def allowed(caller):
    try: gate_with(caller); return True
    except HTTPException: return False
check("super_admin allowed", allowed({'super_admin': True}))
check("explicit grant settings.commission_plans=true allowed", allowed({'perms': {'settings': {'commission_plans': True}}, 'role': 'manager'}))
check("explicit DENY beats admin role", not allowed({'perms': {'settings': {'commission_plans': False}}, 'role': 'admin'}))
check("scope=all admin allowed", allowed({'perms': {'scope': 'all'}}))
check("plain admin role allowed", allowed({'perms': {}, 'role': 'admin'}))
check("non-admin, no grant → denied", not allowed({'perms': {}, 'role': 'rep'}))
# degrade-open: unresolved caller (RBAC off / house) → allowed, never locks out the house
core._uid_from_token = lambda auth: None
core._resolve_caller = lambda client, uid, active_org=None: {'perms': {}, 'role': 'rep'}  # would deny if consulted
check("no-token caller → degrade OPEN (allowed)", cr._require_carrier_template_edit('', LUX) is None)

print("\nK. F1 REGRESSION — caller resolved FOR THE ACTING ORG (org_id passed to _resolve_caller)")
core._uid_from_token = lambda auth: ('uid' if auth else None)
# A login that is ADMIN in its DEFAULT org but only a REP in the ACTING org (LUX). The gate must resolve
# for the ACTING org (LUX) → rep → DENIED. The OLD 2-arg call resolved the default-org (admin) → wrongly
# allowed, so this case would have FAILED against the pre-rework gate.
def resolver_admin_default_rep_acting(client, uid, active_org=None):
    return {'perms': {}, 'role': ('rep' if active_org == LUX else 'admin')}
core._resolve_caller = resolver_admin_default_rep_acting
try:
    cr._require_carrier_template_edit('Bearer x', LUX); _r1 = True
except HTTPException:
    _r1 = False
check("admin-in-default / rep-in-acting-org → DENIED (acting org gates)", _r1 is False)
# The inverse: REP in default org, ADMIN in the acting org (LUX) → ALLOWED. OLD code wrongly 403'd.
def resolver_rep_default_admin_acting(client, uid, active_org=None):
    return {'perms': {}, 'role': ('admin' if active_org == LUX else 'rep')}
core._resolve_caller = resolver_rep_default_admin_acting
try:
    cr._require_carrier_template_edit('Bearer x', LUX); _r2 = True
except HTTPException:
    _r2 = False
check("rep-in-default / admin-in-acting-org → ALLOWED (acting org gates)", _r2 is True)
# capture the org actually passed to _resolve_caller (proves org_id is threaded, not dropped)
_seen = {}
def resolver_capture(client, uid, active_org=None):
    _seen['org'] = active_org
    return {'perms': {'scope': 'all'}}
core._resolve_caller = resolver_capture
cr._require_carrier_template_edit('Bearer x', LUX)
check("gate passes the acting org_id to _resolve_caller (3rd arg)", _seen.get('org') == LUX)

print("\nL. F2 REGRESSION — a DECISION-path error does NOT fail open")
# _can_edit_setting is OUTSIDE the broad except → an error inside the decision propagates (fails closed),
# it must NOT be swallowed into an allow.
core._uid_from_token = lambda auth: ('uid' if auth else None)
core._resolve_caller = lambda client, uid, active_org=None: {'perms': {}, 'role': 'rep'}
_orig_can = core._can_edit_setting
core._can_edit_setting = lambda caller, area: (_ for _ in ()).throw(RuntimeError("boom in decision"))
try:
    cr._require_carrier_template_edit('Bearer x', LUX)
    _f2 = 'allowed'
except HTTPException:
    _f2 = 'http'
except RuntimeError:
    _f2 = 'propagated'
core._can_edit_setting = _orig_can
check("decision-path error propagates (not swallowed into allow)", _f2 == 'propagated')


print(f"\n{'='*54}\nRESULT: {PASS} passed, {FAIL} failed\n{'='*54}")
sys.exit(1 if FAIL else 0)
