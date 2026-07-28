"""Proof harness for agent/commission/installment-edit-m1gate (mig 210).

Drives the REAL engine (sale_installment_engine) + REAL router endpoints over an in-memory FakeClient.
No DB, no network. Covers:
  A  byte-identical engine output for a schedule that does NOT opt into the new option (vs the pre-change
     HEAD snapshot _sie_head_snapshot.py)
  B  m1 activation-payment gate HIT + MISS (+ the two flags) + months 2..N still gated on paid_residual
  C  category-driven activation gate AUTHORITATIVE (item_mapping activation_payment) + heuristic FALLBACK
  D  activation matcher config default + org override (value_field / min_amount)
  E  graceful degradation without mig 210 (matcher/category/audit tables absent)
  F  schedule EDIT round-trip: PUT updates header+lines + audit row; in-flight sale_installment_ledger
     (already-paid past period) is NOT touched by an edit; future months follow the edited schedule
  G  dual-category endpoints: item-categories default+override, bulk assign, facets, store filter
  H  Boost / no-schedule no-op (empty output)

Run: python3 backend/scratchpad/installment_edit_m1gate_proof.py
"""
import os, sys, asyncio, importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.sale_installment_engine as E
import app.modules.commcalc.router as R

# HEAD snapshot (pre-change engine) for the byte-identity proof
_snap_path = os.path.join(os.path.dirname(__file__), '_sie_head_snapshot.py')
_spec = importlib.util.spec_from_file_location('sie_head', _snap_path)
HEAD = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(HEAD)

PASS = 0; FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok  {name}")
    else: FAIL += 1; print(f"FAIL  {name}   {extra}")

def run(coro):
    if asyncio.iscoroutine(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return coro   # sync `def` endpoints return their value directly

ORG = "00000000-0000-0000-0000-000000000001"

# ── in-memory fake supabase client (supports eq/in_/neq/limit/range/order + insert/update/delete/upsert) ──
class FakeResult:
    def __init__(self, data=None, count=None): self.data = data or []; self.count = count
class FakeQuery:
    def __init__(self, store, table, raise_tables):
        self.store = store; self.t = table; self.raise_tables = raise_tables
        self.f = []; self.cnt = False; self.op = 'select'; self.ins = None; self.rng = None
        self.conflict = None; self.patch = None
    def select(self, *a, **k):
        if k.get('count') == 'exact': self.cnt = True
        return self
    def eq(self, c, v): self.f.append(('eq', c, v)); return self
    def in_(self, c, v): self.f.append(('in', c, list(v))); return self
    def neq(self, c, v): self.f.append(('neq', c, v)); return self
    def limit(self, n): return self
    def range(self, a, b): self.rng = (a, b); return self
    def order(self, *a, **k): return self
    def delete(self): self.op = 'delete'; return self
    def insert(self, rows): self.op = 'insert'; self.ins = rows if isinstance(rows, list) else [rows]; return self
    def update(self, patch): self.op = 'update'; self.patch = patch; return self
    def upsert(self, rows, **k):
        self.op = 'upsert'; self.ins = rows if isinstance(rows, list) else [rows]
        self.conflict = k.get('on_conflict'); return self
    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v: return False
            if k == 'in' and rv not in v: return False
            if k == 'neq' and rv == v: return False
        return True
    def execute(self):
        if self.t in self.raise_tables:
            raise Exception(f"relation commcalc.{self.t} does not exist (simulated mig-absent)")
        rows = self.store.setdefault(self.t, [])
        if self.op == 'select':
            m = [r for r in rows if self._m(r)]
            if self.rng: a, b = self.rng; m = m[a:b + 1]
            if self.cnt: return FakeResult(data=m, count=len(m))
            return FakeResult(data=[dict(r) for r in m])
        if self.op == 'delete':
            self.store[self.t] = [r for r in rows if not self._m(r)]
            return FakeResult(data=[])
        if self.op == 'update':
            n = 0
            for r in rows:
                if self._m(r): r.update(self.patch); n += 1
            return FakeResult(data=[{}] * n)
        if self.op == 'insert':
            out = []
            for r in self.ins:
                d = dict(r); d.setdefault('id', f"id{len(rows)+len(out)+1}"); rows.append(d); out.append(dict(d))
            return FakeResult(data=out)
        if self.op == 'upsert':
            keys = [k.strip() for k in (self.conflict or '').split(',') if k.strip()]
            out = []
            for r in self.ins:
                d = dict(r)
                existing = None
                if keys:
                    existing = next((x for x in rows if all(x.get(k) == d.get(k) for k in keys)), None)
                if existing is not None:
                    existing.update(d)
                    out.append(dict(existing))
                else:
                    d.setdefault('id', f"id{len(rows)+len(out)+1}"); rows.append(d); out.append(dict(d))
            return FakeResult(data=out)
        return FakeResult()
class FakeSchema:
    def __init__(self, store, raise_tables): self.store = store; self.raise_tables = raise_tables
    def table(self, t): return FakeQuery(self.store, t, self.raise_tables)
class FakeClient:
    def __init__(self, store, raise_tables): self.store = store; self.raise_tables = raise_tables
    def schema(self, s): return FakeSchema(self.store, self.raise_tables)

def new_client(store=None, raise_tables=None):
    store = store if store is not None else {}
    rt = set(raise_tables or [])
    c = FakeClient(store, rt)
    R.sb = lambda: c
    return c, store

# ── seed helpers ────────────────────────────────────────────────────────────────────────────────
def base_plan(store):
    store['commission_plan'] = [{'id': 'plan1', 'org_id': ORG, 'name': 'Total Plan', 'is_active': True, 'carrier_id': 'car1'}]
    store['commission_plan_assignment'] = [{'id': 'a1', 'org_id': ORG, 'plan_id': 'plan1', 'scope': 'default', 'scope_value': None, 'priority': 0}]
    store['commission_rule'] = []; store['commission_tier'] = []; store['store_mapping'] = []
    store['product_mrc'] = []; store['carrier_category_map'] = []; store['flag_rules'] = []

def sched(store, m1_gate='inherit', gate_mode='paid_residual', num_months=3):
    store['plan_installment_schedule'] = [{
        'id': 'sch1', 'org_id': ORG, 'plan_id': 'plan1', 'name': 'TW 3mo', 'num_months': num_months,
        'trigger_match_field': 'contract_type', 'trigger_match_op': 'contains', 'trigger_match_value': 'activ',
        'gate_mode': gate_mode, 'gate_from_month': 1, 'm1_gate': m1_gate, 'clawback_enabled': False,
        'effective_from': None, 'effective_to': None, 'eligible_sale_periods': [], 'is_active': True}]
    store['plan_installment_line'] = [
        {'id': 'l1', 'org_id': ORG, 'schedule_id': 'sch1', 'month_index': 1, 'payout_kind': 'flat', 'flat_amount': 30, 'mrc_pct': 0, 'mrc_source': 'product_catalog'},
        {'id': 'l2', 'org_id': ORG, 'schedule_id': 'sch1', 'month_index': 2, 'payout_kind': 'flat', 'flat_amount': 20, 'mrc_pct': 0, 'mrc_source': 'product_catalog'},
        {'id': 'l3', 'org_id': ORG, 'schedule_id': 'sch1', 'month_index': 3, 'payout_kind': 'flat', 'flat_amount': 10, 'mrc_pct': 0, 'mrc_source': 'product_catalog'}]

def act_line(trans, mdn, contract='Activation', period='July 2026', date='2026-07-05', store_name='18226 Kedzie', rep='Collins, Mea'):
    return {'org_id': ORG, 'period': period, 'trans_id': trans, 'mdn': mdn, 'serial_1': 's' + trans,
            'salesperson': rep, 'store': store_name, 'contract_type': contract, 'trans_type': 'Sale',
            'voided': '', 'trans_date': date, 'product_desc': 'Total ALL ACCESS Plan $65', 'customer_plan': 'Total ALL ACCESS Plan $65',
            'department': '', 'category': '', 'ext_price': 0, 'gp': 0, 'sku': 'SKU-PLAN-65'}
def pay_line(trans, desc, dept, cat, ext, gp=0, period='July 2026', date='2026-07-05', sku=None, rep='Collins, Mea'):
    return {'org_id': ORG, 'period': period, 'trans_id': trans, 'mdn': '', 'serial_1': '',
            'salesperson': rep, 'store': '18226 Kedzie', 'contract_type': '', 'trans_type': 'Sale',
            'voided': '', 'trans_date': date, 'product_desc': desc, 'customer_plan': desc,
            'department': dept, 'category': cat, 'ext_price': ext, 'gp': gp, 'sku': sku}
def mi_row(mdn, active=True, mi=5.0, atu=1.0, period='July 2026'):
    return {'org_id': ORG, 'period': period, 'phone_number': mdn, 'device_serial': '',
            'subscriber_status': 'Active' if active else 'Deactivated', 'actual_mi_payout': mi, 'actual_atu_payout': atu}

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("A. byte-identical engine output for an INHERIT schedule (vs HEAD snapshot)")
c, s = new_client()
base_plan(s); sched(s, m1_gate='inherit', gate_mode='paid_residual')
s['raw_sales'] = [
    act_line('T1', '3125550001'), pay_line('T1', 'Access Charge - $25', 'System', 'System', 25, 12.5),
    act_line('T0', '2225550000', period='June 2026', date='2026-06-10'),
    pay_line('T0', 'Access Charge - $25', 'System', 'System', 25, 12.5, period='June 2026', date='2026-06-10')]
s['raw_mi'] = [mi_row('3125550001'), mi_row('2225550000')]   # both active+residual
def _drop_additive(res):
    """mig 233 (installment-plan-line-only) adds two purely-additive TOP-LEVEL keys — `chain_guard`
    (counters for the one-chain-per-activation guard) and `warnings` (operator diagnostics). `totals`,
    `by_rep`, `flags` and every ledger row are unchanged, so the byte-identity claim below is still a
    real behavioural differential."""
    out = dict(res)
    out.pop("chain_guard", None)
    out.pop("warnings", None)
    # mig 245 (2026-07-27) adds the same KIND of purely-additive material: one top-level key
    # (`category_guard`) and four DISPLAY-ONLY row keys. No money field moves — dropping them here is
    # exactly what makes the byte-identity claim below a real differential on the money shape.
    out.pop("category_guard", None)
    out["ledger"] = [{k: v for k, v in r.items()
                      if k not in ("device_category", "device_product", "plan_product", "display_label")}
                     for r in (out.get("ledger") or [])]
    return out


new_out = _drop_additive(E.compute_sale_installments(c, ORG, 'July 2026', persist=False))
head_out = _drop_additive(HEAD.compute_sale_installments(c, ORG, 'July 2026', persist=False))
check("inherit output byte-identical to HEAD", new_out == head_out,
      f"\n   new={new_out.get('by_rep')} ledger0={new_out.get('ledger')[:1]}\n   head={head_out.get('by_rep')}")
check("inherit ledger carries NO new gate_kind keys", all('gate_kind' not in l for l in new_out['ledger']))
check("inherit pays M1($30)+M2($20) for COLLINS, MEA", new_out['by_rep'].get('COLLINS, MEA') == 50.0, new_out['by_rep'])

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. m1 activation-payment gate HIT + MISS (+ flags) + months 2..N residual")
c, s = new_client()
base_plan(s); sched(s, m1_gate='activation_payment', gate_mode='paid_residual')
s['raw_sales'] = [
    # T1: July activation (month 1) WITH an access-charge payment line → activation gate MET even though
    # raw_mi has NO residual for its mdn (proves it's NOT the residual gate)
    act_line('T1', '3125550001'), pay_line('T1', 'Access Charge - $25', 'System', 'System', 25, 12.5),
    # T2: July activation (month 1) with NO payment line (device only, ext 0) → activation gate MISS
    act_line('T2', '3125550002'),
    # T0: June sale (month 2 in July) → residual gate; raw_mi July HAS its mdn active+residual → pays M2
    act_line('T0', '2225550000', period='June 2026', date='2026-06-10')]
s['raw_mi'] = [mi_row('2225550000')]   # only T0's mdn present (residual). T1/T2 absent.
out = E.compute_sale_installments(c, ORG, 'July 2026', persist=False)
led = {(l['trans_id'], l['month_index']): l for l in out['ledger']}
check("T1 M1 PAID via activation payment", led[('T1', 1)]['paid_gate_met'] is True and led[('T1', 1)]['gate_kind'] == 'activation_payment', led.get(('T1', 1)))
check("T1 M1 activation_payment_matched=True", led[('T1', 1)].get('activation_payment_matched') is True)
check("T2 M1 WITHHELD (no activation payment)", led[('T2', 1)]['paid_gate_met'] is False and led[('T2', 1)]['status'] == 'withheld_unpaid')
check("T0 M2 PAID via residual (gate_kind None)", led[('T0', 2)]['paid_gate_met'] is True and led[('T0', 2)].get('gate_kind') is None and led[('T0', 2)]['matched_mi_period'] == 'July 2026', led.get(('T0', 2)))
check("by_rep = 30 (T1 M1) + 20 (T0 M2)", out['by_rep'].get('COLLINS, MEA') == 50.0, out['by_rep'])
# two withheld flags for T2, with the activation-specific sources + copy
t2flags = [f for f in out['flags'] if f['mdn'] == '3125550002']
srcs = sorted(f['source'] for f in t2flags)
check("T2 emits BOTH flags (commission_rebate_tracking + employee_miss)", srcs == ['commission_rebate_tracking', 'employee_miss'], srcs)
check("T2 withheld-flag copy mentions activation", any('activation' in (f['description'] or '').lower() for f in t2flags))

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. category-driven activation gate AUTHORITATIVE + heuristic FALLBACK")
# Two trans: TA has ONLY an Access-Charge line (mapped to activation_payment). TB has ONLY a Wallet-Funding
# line (System dept, heuristic-matchable) but NOT mapped to activation_payment.
def cscene():
    c, s = new_client()
    base_plan(s); sched(s, m1_gate='activation_payment', gate_mode='paid_residual')
    s['raw_sales'] = [
        act_line('TA', '3125550010'), pay_line('TA', 'Access Charge - $25', 'System', 'System', 25, 12.5, sku='SKU-ACCESS'),
        act_line('TB', '3125550011'), pay_line('TB', 'Wallet Funding', 'System', 'System', 73, 73, sku='SKU-WALLET')]
    s['raw_mi'] = []
    return c, s
# fallback: no item_mapping → heuristic matches BOTH (System dept) → both paid
c, s = cscene()
out = E.compute_sale_installments(c, ORG, 'July 2026', persist=False)
led = {(l['trans_id'], l['month_index']): l for l in out['ledger']}
check("FALLBACK: TA M1 paid (heuristic)", led[('TA', 1)]['paid_gate_met'] is True)
check("FALLBACK: TB M1 paid (heuristic, System dept)", led[('TB', 1)]['paid_gate_met'] is True)
# authoritative: map ONLY the Access-Charge item to activation_payment → TA qualifies, TB does NOT
c, s = cscene()
s['item_mapping'] = [{'id': 'im1', 'org_id': ORG, 'item_key': 'SKU-ACCESS', 'sales_category': 'activation_payment', 'kpi_category': None}]
out = E.compute_sale_installments(c, ORG, 'July 2026', persist=False)
led = {(l['trans_id'], l['month_index']): l for l in out['ledger']}
check("AUTHORITATIVE: TA M1 paid (mapped activation_payment)", led[('TA', 1)]['paid_gate_met'] is True, led.get(('TA', 1)))
check("AUTHORITATIVE: TB M1 WITHHELD (item not mapped → heuristic ignored)", led[('TB', 1)]['paid_gate_met'] is False, led.get(('TB', 1)))
# kpi_category dimension also counts
c, s = cscene()
s['item_mapping'] = [{'id': 'im1', 'org_id': ORG, 'item_key': 'SKU-WALLET', 'sales_category': None, 'kpi_category': 'activation_payment'}]
out = E.compute_sale_installments(c, ORG, 'July 2026', persist=False)
led = {(l['trans_id'], l['month_index']): l for l in out['ledger']}
check("AUTHORITATIVE: kpi_category='activation_payment' qualifies TB", led[('TB', 1)]['paid_gate_met'] is True)
check("AUTHORITATIVE: unmapped TA withheld under kpi mapping", led[('TA', 1)]['paid_gate_met'] is False)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. activation matcher config default + org override (value_field / min_amount)")
c, s = new_client()
m = E._load_activation_matcher(c, ORG)
check("default matcher value_field=ext_price min=0.01", m['value_field'] == 'ext_price' and m['min_amount'] == 0.01, m)
s['commission_org_config'] = [{'org_id': ORG, 'activation_payment_matcher': {'value_field': 'gp', 'min_amount': 5, 'departments': ['system'], 'categories': [], 'product_keywords': []}}]
m2 = E._load_activation_matcher(c, ORG)
check("override matcher value_field=gp min=5", m2['value_field'] == 'gp' and m2['min_amount'] == 5.0, m2)
# a line ext=25 gp=0.43: passes under default (ext>=0.01) but FAILS under override (gp<5)
line = pay_line('X', 'Access Charge', 'System', 'System', 25, 0.43)
check("value gate: default passes", E._line_value_ok(line, m) is True)
check("value gate: override (gp>=5) fails", E._line_value_ok(line, m2) is False)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. graceful degradation without mig 210 (matcher/category/audit tables absent)")
c, s = new_client(raise_tables={'commission_org_config'})
check("E1 matcher loader → default when config table raises", E._load_activation_matcher(c, ORG)['value_field'] == 'ext_price')
c, s = new_client(raise_tables={'item_category_config'})
cats = run(R.get_item_categories(org_id=ORG))
check("E2 item-categories → seeded defaults when config table raises", any(x['value'] == 'activation_payment' for x in cats['sales']) and len(cats['kpi']) >= 6, cats)
# audit table absent → save still works (no-op audit)
c, s = new_client(raise_tables={'plan_installment_schedule_audit'})
base_plan(s)
res = run(R.save_plan_installment({'plan_id': 'plan1', 'num_months': 2, 'm1_gate': 'activation_payment',
    'lines': [{'month_index': 1, 'payout_kind': 'flat', 'flat_amount': 30}]}, authorization="", org_id=ORG))
check("E3 create schedule succeeds even when audit table absent", res.get('saved') is True and res.get('id'))
# a schedule row lacking m1_gate computes as inherit (functional degradation)
c, s = new_client(); base_plan(s)
s['plan_installment_schedule'] = [{'id': 'sX', 'org_id': ORG, 'plan_id': 'plan1', 'num_months': 1,
    'trigger_match_field': 'any', 'gate_mode': 'none', 'is_active': True, 'eligible_sale_periods': []}]  # no m1_gate key
s['plan_installment_line'] = [{'id': 'lX', 'org_id': ORG, 'schedule_id': 'sX', 'month_index': 1, 'payout_kind': 'flat', 'flat_amount': 7}]
s['raw_sales'] = [act_line('TZ', '3125550099')]
out = E.compute_sale_installments(c, ORG, 'July 2026', persist=False)
check("E4 schedule without m1_gate behaves as inherit (gate none → pays)", out['by_rep'].get('COLLINS, MEA') == 7.0 and all('gate_kind' not in l for l in out['ledger']), out['by_rep'])

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. schedule EDIT round-trip: PUT updates header+lines+audit; in-flight ledger untouched")
c, s = new_client(); base_plan(s)
created = run(R.save_plan_installment({'plan_id': 'plan1', 'name': 'orig', 'num_months': 2, 'gate_mode': 'paid_residual',
    'lines': [{'month_index': 1, 'payout_kind': 'flat', 'flat_amount': 30}, {'month_index': 2, 'payout_kind': 'flat', 'flat_amount': 20}]},
    authorization="", org_id=ORG))
sid = created['id']
# simulate an ALREADY-PAID ledger row for a PAST period
s['sale_installment_ledger'] = [{'id': 'old1', 'org_id': ORG, 'trans_id': 'PAST', 'mdn': '111', 'month_index': 1,
    'pay_period': 'June 2026', 'amount': 30, 'status': 'paid', 'schedule_id': sid}]
before_ledger = [dict(r) for r in s['sale_installment_ledger']]
# EDIT: change M1 to $45, rename, add m1_gate
upd = run(R.update_plan_installment(sid, {'plan_id': 'plan1', 'name': 'edited', 'num_months': 2, 'm1_gate': 'activation_payment',
    'gate_mode': 'paid_residual',
    'lines': [{'month_index': 1, 'payout_kind': 'flat', 'flat_amount': 45}, {'month_index': 2, 'payout_kind': 'flat', 'flat_amount': 20}]},
    authorization="", org_id=ORG))
check("F1 PUT returns updated", upd.get('updated') is True and upd.get('id') == sid)
head = next(x for x in s['plan_installment_schedule'] if x['id'] == sid)
check("F2 header updated (name + m1_gate)", head['name'] == 'edited' and head['m1_gate'] == 'activation_payment')
check("F3 updated_by stamped", head.get('updated_by') == 'web')
m1line = next(l for l in s['plan_installment_line'] if l['schedule_id'] == sid and l['month_index'] == 1)
check("F4 M1 line amount now 45", m1line['flat_amount'] == 45.0, m1line)
check("F5 in-flight sale_installment_ledger (past paid) UNTOUCHED by the edit", s['sale_installment_ledger'] == before_ledger)
audits = s.get('plan_installment_schedule_audit', [])
upd_audit = [a for a in audits if a['action'] == 'update']
check("F6 update audit row written (before='orig' / after='edited')", len(upd_audit) == 1 and upd_audit[0]['before_json']['name'] == 'orig' and upd_audit[0]['after_json']['name'] == 'edited', audits)
check("F6b create audit row also present", any(a['action'] == 'create' and a['before_json'] is None for a in audits))
# FUTURE month follows the edit: recompute July with the edited schedule
s['raw_sales'] = [act_line('TF', '3125550050'), pay_line('TF', 'Access Charge - $25', 'System', 'System', 25, 12.5)]
s['raw_mi'] = []
out = E.compute_sale_installments(c, ORG, 'July 2026', persist=False)
led = {(l['trans_id'], l['month_index']): l for l in out['ledger']}
check("F7 future month-1 follows edited $45 + activation gate", led[('TF', 1)]['amount'] == 45.0 and led[('TF', 1)]['paid_gate_met'] is True, led.get(('TF', 1)))
# GET audit endpoint
au = run(R.plan_installment_audit(sid, org_id=ORG))
check("F8 audit endpoint returns the trail (create + update)", au['ready'] is True and len(au['audit']) == 2)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. dual-category endpoints: defaults, override, bulk assign, facets, store filter")
c, s = new_client()
cats = run(R.get_item_categories(org_id=ORG))
check("G1 sales defaults incl activation_payment", any(x['value'] == 'activation_payment' for x in cats['sales']))
check("G2 kpi defaults incl protection + wireless_home_internet", {'protection', 'wireless_home_internet'} <= {x['value'] for x in cats['kpi']})
check("G3 defaults were seeded into item_category_config", len(s.get('item_category_config', [])) == 14, len(s.get('item_category_config', [])))
run(R.put_item_category({'dimension': 'kpi', 'value': 'byod', 'label': 'BYOD'}, authorization="", org_id=ORG))
cats2 = run(R.get_item_categories(org_id=ORG))
check("G4 org override adds a kpi category", any(x['value'] == 'byod' for x in cats2['kpi']))
# item_mapping + bulk dual-category assign
s['item_mapping'] = [
    {'id': 'im1', 'org_id': ORG, 'item_key': 'SKU-A', 'item_desc': 'Access Charge', 'department': 'System', 'category': 'System', 'item_type': 'other'},
    {'id': 'im2', 'org_id': ORG, 'item_key': 'SKU-B', 'item_desc': 'Protect+', 'department': 'Rtr', 'category': 'Other Carr. payments', 'item_type': 'other'}]
run(R.bulk_item_mapping({'item_keys': ['SKU-A'], 'sales_category': 'activation_payment'}, org_id=ORG))
run(R.bulk_item_mapping({'item_keys': ['SKU-B'], 'kpi_category': 'protection'}, org_id=ORG))
imA = next(x for x in s['item_mapping'] if x['item_key'] == 'SKU-A')
imB = next(x for x in s['item_mapping'] if x['item_key'] == 'SKU-B')
check("G5 bulk assigned SKU-A sales_category", imA.get('sales_category') == 'activation_payment')
check("G6 bulk assigned SKU-B kpi_category", imB.get('kpi_category') == 'protection')
check("G7 bulk did NOT touch the OTHER dimension", imA.get('kpi_category') is None and imB.get('sales_category') is None)
# facets + store filter
s['raw_sales'] = [
    {'org_id': ORG, 'store': 'Store X', 'department': 'System', 'category': 'System', 'sku': 'SKU-A', 'product_desc': 'Access Charge'},
    {'org_id': ORG, 'store': 'Store Y', 'department': 'Rtr', 'category': 'Other Carr. payments', 'sku': 'SKU-B', 'product_desc': 'Protect+'}]
fac = run(R.item_mapping_facets(org_id=ORG))
check("G8 facets return distinct stores", set(fac['stores']) == {'Store X', 'Store Y'}, fac['stores'])
filt = run(R.get_item_mapping(store='Store X', org_id=ORG))
check("G9 store filter restricts to items sold in that store", [i['item_key'] for i in filt['items']] == ['SKU-A'], [i['item_key'] for i in filt['items']])

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nH. Boost / no-schedule no-op")
c, s = new_client(); base_plan(s)
out = E.compute_sale_installments(c, ORG, 'July 2026', persist=False)
check("H1 no schedules → empty output, $0", out['by_rep'] == {} and out['ledger'] == [] and out['schedules'] == 0)
head = HEAD.compute_sale_installments(c, ORG, 'July 2026', persist=False)
check("H2 no-schedule output byte-identical to HEAD", out == head)

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
