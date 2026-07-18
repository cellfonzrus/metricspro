"""Proof harness for agent/commission/agency-phase1 (Agency Module Phase 1 — config + invoicing).

BASE = origin/main 7d7fbe7. Drives the REAL endpoint-logic functions (app.modules.commcalc.agency) + the REAL
pure math (agency_billing) + the REAL core _can_edit_setting over an in-memory FakeClient (no DB/network).

WHAT THIS PROVES
  (1) link CRUD + CYCLE GUARD — A→B→A rejected, deeper A→B→C→A rejected, self-link rejected, valid accepted.
  (2) CONSENT-GATED roster read — unconsented tenant sub → manual only (consented=False, ZERO store leak);
      accepted → the sub's OWN storeops.stores only (org isolation of the roster pull).
  (3) HOLDBACK RULE RESOLUTION ORDER (doc C1) — specificity, statement_line_type>commission_component,
      carrier-specific>carrier-any, priority tiebreak, effective-dating + carrier gate.
  (4) INVOICE EXACT MATH — equipment margin (% and flat), store-fee proration (full vs 19/30), taxable vs
      wholesale snapshot, per_invoice vs one_time vs monthly cadence, transfer consumption + no-double-bill,
      UNCONFIRMED rolls forward.
  (5) IDEMPOTENT draft regeneration (regenerate twice → identical totals + line count).
  (6) ISSUED IMMUTABILITY (generate on an issued period = no-op; issue a non-draft = error).
  (7) ORG ISOLATION both directions (a master can't read another master's link/invoices).
  (8) OCR confirm authority (core _can_edit_setting('agency')) + confirm/reject state transition.

Run:  cd backend && python3 scratchpad/agency_phase1_proof.py
"""
import os, sys, uuid
from datetime import date as _date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.agency as A
import app.modules.commcalc.agency_billing as AB
import app.modules.commcalc.router as R
import app.modules.core.router as CORE
from fastapi import HTTPException


def raises(status, fn, name):
    try:
        fn()
        check(name, False, "expected HTTPException")
    except HTTPException as e:
        check(name, e.status_code == status, f"got {e.status_code}, want {status}")
    except Exception as e:
        check(name, False, f"wrong exc {type(e).__name__}: {e}")

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


def raises400(fn, name):
    try:
        fn()
        check(name, False, "expected HTTPException")
    except HTTPException as e:
        check(name, True)
    except Exception as e:
        check(name, False, f"wrong exc {type(e).__name__}: {e}")


# ── in-memory fake supabase (eq/neq/in_/gte/lt + insert/update/delete/upsert; schema arg ignored) ──
class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.t = table
        self.f = []
        self.rng = None
        self._ins = None
        self._upd = None
        self._del = False
        self._up = None
        self._up_keys = None

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def neq(self, c, v):
        self.f.append(('neq', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

    def gte(self, c, v):
        self.f.append(('gte', c, v)); return self

    def lt(self, c, v):
        self.f.append(('lt', c, v)); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def order(self, *a, **k):
        return self

    def insert(self, row):
        self._ins = dict(row); return self

    def update(self, patch):
        self._upd = dict(patch); return self

    def delete(self):
        self._del = True; return self

    def upsert(self, row, on_conflict=None):
        self._up = dict(row); self._up_keys = [k.strip() for k in (on_conflict or 'id').split(',')]; return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v:
                return False
            if k == 'neq' and rv == v:
                return False
            if k == 'in' and rv not in v:
                return False
            if k == 'gte' and not (rv is not None and str(rv) >= str(v)):
                return False
            if k == 'lt' and not (rv is not None and str(rv) < str(v)):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        if self._ins is not None:
            r = dict(self._ins)
            r.setdefault('id', str(uuid.uuid4()))
            rows.append(r)
            return FakeResult([dict(r)])
        if self._up is not None:
            for existing in rows:
                if all(existing.get(k) == self._up.get(k) for k in self._up_keys):
                    existing.update(self._up)
                    return FakeResult([dict(existing)])
            rows.append(dict(self._up))
            return FakeResult([dict(rows[-1])])
        if self._upd is not None:
            n = [r for r in rows if self._m(r)]
            for r in n:
                r.update(self._upd)
            return FakeResult([dict(r) for r in n])
        if self._del:
            keep = [r for r in rows if not self._m(r)]
            self.store[self.t] = keep
            return FakeResult([])
        m = [r for r in rows if self._m(r)]
        if self.rng:
            m = m[self.rng[0]:self.rng[1] + 1]
        return FakeResult([dict(r) for r in m])


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeQuery(self.store, t)


class FakeClient:
    def __init__(self, store=None):
        self.store = store if store is not None else {}

    def schema(self, s):
        return FakeSchema(self.store)


def newc(store=None):
    return FakeClient(store)


# ═══ (1) LINK CRUD + CYCLE GUARD ═══════════════════════════════════════════════════════════════════
print("\n(1) link CRUD + cycle guard")
ORGA, ORGB, ORGC, ORGD = 'org-A', 'org-B', 'org-C', 'org-D'
c = newc()
# A creates a valid link to a fresh tenant D
r = A.upsert_link(c, ORGA, {'sub_kind': 'tenant', 'sub_org_id': ORGD, 'sub_name': 'Delta'}, who='u1')
check("valid A→D link created", r.get('ok') and r['link']['org_id'] == ORGA and r['link']['sub_org_id'] == ORGD)
check("new link default status draft", r['link']['status'] == 'draft')
# list + get
lst = A.list_links(c, ORGA)
check("A sees its 1 link", len(lst['links']) == 1 and lst['links'][0]['store_count'] == 0)
# self-link rejected
raises400(lambda: A.upsert_link(c, ORGA, {'sub_kind': 'tenant', 'sub_org_id': ORGA, 'sub_name': 'self'}), "self-link A→A rejected")
# external sub needs a name but no org
r_ext = A.upsert_link(c, ORGA, {'sub_kind': 'external', 'sub_name': 'Acme (external)'}, who='u1')
check("external sub link created (no org)", r_ext['ok'] and r_ext['link']['sub_kind'] == 'external')
raises400(lambda: A.upsert_link(c, ORGA, {'sub_kind': 'external', 'sub_name': ''}), "external sub w/o name rejected")
raises400(lambda: A.upsert_link(c, ORGA, {'sub_kind': 'tenant', 'sub_name': 'no org'}), "tenant sub w/o sub_org_id rejected")

# 2-cycle: B is master of A already → A cannot make B its sub
c2 = newc()
A.upsert_link(c2, ORGB, {'sub_kind': 'tenant', 'sub_org_id': ORGA, 'sub_name': 'A'}, who='u')   # B→A
raises400(lambda: A.upsert_link(c2, ORGA, {'sub_kind': 'tenant', 'sub_org_id': ORGB, 'sub_name': 'B'}), "cycle A→B→A rejected")
# but A→(fresh) still fine
ok_deep = A.upsert_link(c2, ORGA, {'sub_kind': 'tenant', 'sub_org_id': ORGD, 'sub_name': 'D'}, who='u')
check("A→D still allowed under a partial chain", ok_deep['ok'])

# 3-cycle: A→B, B→C exist → C cannot make A its sub
c3 = newc()
A.upsert_link(c3, ORGA, {'sub_kind': 'tenant', 'sub_org_id': ORGB, 'sub_name': 'B'}, who='u')   # A→B
A.upsert_link(c3, ORGB, {'sub_kind': 'tenant', 'sub_org_id': ORGC, 'sub_name': 'C'}, who='u')   # B→C
raises400(lambda: A.upsert_link(c3, ORGC, {'sub_kind': 'tenant', 'sub_org_id': ORGA, 'sub_name': 'A'}), "deep cycle A→B→C→A rejected")
# C→(fresh D) allowed
check("C→D allowed (no cycle)", A.upsert_link(c3, ORGC, {'sub_kind': 'tenant', 'sub_org_id': ORGD, 'sub_name': 'D'}, who='u')['ok'])
# update existing (id present) does not re-run create-guard incorrectly
lk = A.list_links(c3, ORGA)['links'][0]
upd = A.upsert_link(c3, ORGA, {'id': lk['id'], 'sub_kind': 'tenant', 'sub_org_id': ORGB, 'sub_name': 'B (renamed)', 'status': 'active'})
check("link update by id ok", upd.get('ok') and upd.get('id') == lk['id'])
check("link update applied", A.get_link(c3, ORGA, lk['id'])['link']['status'] == 'active')
# delete
A.delete_link(c3, ORGA, lk['id'])
raises400(lambda: A.get_link(c3, ORGA, lk['id']), "deleted link 404s")


# ═══ (2) CONSENT-GATED ROSTER READ ═══════════════════════════════════════════════════════════════
print("\n(2) consent-gated roster read (no cross-org leak)")
MASTER, SUBORG, OTHERORG = 'org-master', 'org-sub', 'org-other'
store = {}
c = newc(store)
lk = A.upsert_link(c, MASTER, {'sub_kind': 'tenant', 'sub_org_id': SUBORG, 'sub_name': 'SubCo'}, who='u')['link']
# seed storeops.stores for BOTH the sub and another org (isolation bait)
store['stores'] = [
    {'id': 1, 'org_id': SUBORG, 'store_code': 'S1', 'address': '1 Sub St', 'market': 'PA', 'is_active': True},
    {'id': 2, 'org_id': SUBORG, 'store_code': 'S2', 'address': '2 Sub St', 'market': 'PA', 'is_active': True},
    {'id': 9, 'org_id': OTHERORG, 'store_code': 'X9', 'address': 'other', 'market': 'NJ', 'is_active': True},
]
sc = A.store_candidates(c, MASTER, lk['id'])
check("unconsented sub → consented False", sc['consented'] is False)
check("unconsented sub → ZERO stores returned (no leak)", sc['stores'] == [])
# accept consent → pull the sub's OWN stores only
A.set_consent(c, MASTER, lk['id'], 'accepted', who='sub-admin')
sc2 = A.store_candidates(c, MASTER, lk['id'])
check("accepted sub → consented True", sc2['consented'] is True)
check("accepted sub → exactly the sub's 2 stores", len(sc2['stores']) == 2)
check("accepted sub → NO other-org store leaked", all(s['store_code'] in ('S1', 'S2') for s in sc2['stores']))
# external sub never pulls a roster
lk_ext = A.upsert_link(c, MASTER, {'sub_kind': 'external', 'sub_name': 'Ext'}, who='u')['link']
check("external sub → manual only", A.store_candidates(c, MASTER, lk_ext['id'])['consented'] is False)
# manual roster entry works regardless
ms = A.upsert_store(c, MASTER, lk_ext['id'], {'store_kind': 'external', 'store_label': 'Kiosk 5'}, who='u')
check("manual roster store added", ms['ok'])
raises400(lambda: A.upsert_store(c, MASTER, lk_ext['id'], {}), "empty roster store rejected")


# ═══ (3) HOLDBACK RULE RESOLUTION (doc C1) ═════════════════════════════════════════════════════════
print("\n(3) holdback rule resolution order")
PS, PE = _date(2026, 6, 1), _date(2026, 6, 30)


def rule(sk, sv=None, carrier=None, method='percent', value=0.1, prio=100, created='2026-01-01', active=True,
         es=None, ee=None):
    return {'scope_kind': sk, 'scope_value': sv, 'carrier_id': carrier, 'method': method, 'value': value,
            'priority': prio, 'created_at': created, 'is_active': active, 'effective_start': es, 'effective_end': ee}


rules = [rule('all', value=0.05), rule('ledger_bucket', 'residual_monthly', value=0.10),
         rule('product_class', 'device', value=0.20)]
ctx = {'carrier_id': None, 'ledger_bucket': 'residual_monthly', 'product_class': 'device'}
w = AB.resolve_holdback_rule(rules, ctx, PS, PE)
check("product_class (5) beats ledger_bucket (3) beats all (1)", w['scope_kind'] == 'product_class')
ctx2 = {'ledger_bucket': 'residual_monthly'}
check("ledger_bucket beats all when no product_class match", AB.resolve_holdback_rule(rules, ctx2, PS, PE)['scope_kind'] == 'ledger_bucket')
check("falls back to 'all' when nothing else matches", AB.resolve_holdback_rule(rules, {}, PS, PE)['scope_kind'] == 'all')
# statement_line_type (4b) beats commission_component (4a)
r_lvl4 = [rule('commission_component', 'device_margin', value=0.11), rule('statement_line_type', 'New ACT', value=0.12)]
ctx4 = {'commission_component': 'device_margin', 'statement_line_type': 'New ACT'}
check("statement_line_type beats commission_component (level 4)", AB.resolve_holdback_rule(r_lvl4, ctx4, PS, PE)['value'] == 0.12)
# carrier-specific beats carrier-any at the SAME specificity
CARR = 'carr-1'
r_carr = [rule('ledger_bucket', 'commission', carrier=None, value=0.10),
          rule('ledger_bucket', 'commission', carrier=CARR, value=0.15)]
check("carrier-specific beats carrier-any", AB.resolve_holdback_rule(r_carr, {'carrier_id': CARR, 'ledger_bucket': 'commission'}, PS, PE)['value'] == 0.15)
check("carrier-any still applies to a different carrier", AB.resolve_holdback_rule(r_carr, {'carrier_id': 'other', 'ledger_bucket': 'commission'}, PS, PE)['value'] == 0.10)
# carrier-specific rule does NOT fire on a line with no carrier
check("carrier-specific excluded when line has no carrier", AB.resolve_holdback_rule([rule('all', carrier=CARR, value=0.9)], {}, PS, PE) is None)
# priority tiebreak within same specificity
r_prio = [rule('ledger_bucket', 'commission', value=0.10, prio=100), rule('ledger_bucket', 'commission', value=0.20, prio=10)]
check("lower priority wins the tiebreak", AB.resolve_holdback_rule(r_prio, {'ledger_bucket': 'commission'}, PS, PE)['value'] == 0.20)
# effective-dating excludes an out-of-window rule
r_eff = [rule('all', value=0.05), rule('product_class', 'device', value=0.20, es='2026-07-01')]
check("future-dated rule excluded from June", AB.resolve_holdback_rule(r_eff, {'product_class': 'device'}, PS, PE)['scope_kind'] == 'all')
# holdback_amount: percent vs flat_per
check("holdback percent = value×gross", AB.holdback_amount(rule('all', method='percent', value=0.1), 1000) == 100.0)
check("holdback flat per activation", AB.holdback_amount({'method': 'flat', 'value': 2.0, 'flat_per': 'activation'}, 0, activations=5) == 10.0)
check("holdback flat per invoice = once", AB.holdback_amount({'method': 'flat', 'value': 2.0, 'flat_per': 'invoice'}, 0, qty=9, activations=9) == 2.0)


# ═══ (4)/(5)/(6) INVOICE MATH + consumption + idempotency + immutability ═══════════════════════════
print("\n(4) invoice exact math + transfer consumption + roll-forward")


def make_link_scenario():
    store = {}
    c = newc(store)
    lk = A.upsert_link(c, MASTER, {'sub_kind': 'tenant', 'sub_org_id': SUBORG, 'sub_name': 'SubCo',
                                   'taxable': False, 'default_proration_mode': 'full'}, who='u')['link']
    return c, lk['id'], store


# --- pure math: PA/DE store-fee proration (full 500 + prorated 750×19/30=475) ---
link = {'taxable': False, 'tax_rate': 0, 'default_proration_mode': 'full'}
stores = [{'id': 'beth', 'store_label': 'Bethlehem', 'effective_start': '2025-01-01'},
          {'id': 's1578', 'store_label': 'Store 1578', 'effective_start': '2026-06-12'}]
charges = [{'id': 'cb', 'cadence': 'monthly', 'method': 'flat', 'value': 500, 'link_store_id': 'beth', 'proration_mode': 'full', 'is_active': True},
           {'id': 'c1578', 'cadence': 'monthly', 'method': 'flat', 'value': 750, 'link_store_id': 's1578', 'proration_mode': 'prorated', 'is_active': True}]
pay = AB.compute_invoice_lines(link, stores, charges, [], [], 'June 2026')
check("store-fee full = 500", any(l['source_type'] == 'store_fee' and l['amount'] == 500.0 for l in pay['lines']))
check("store-fee prorated 750×19/30 = 475.00", any(l['source_type'] == 'store_fee' and l['amount'] == 475.0 for l in pay['lines']))
check("store_fee_total = 975", pay['store_fee_total'] == 975.0)
check("subtotal = 975 (no tax, wholesale)", pay['subtotal'] == 975.0 and pay['tax_total'] == 0.0 and pay['total'] == 975.0)
# taxable snapshot 6%
link_tax = dict(link); link_tax['taxable'] = True; link_tax['tax_rate'] = 0.06
pay_t = AB.compute_invoice_lines(link_tax, stores, charges, [], [], 'June 2026')
check("taxable snapshot → tax 6%×975 = 58.50", pay_t['tax_total'] == 58.5 and pay_t['total'] == 1033.5)
check("taxable_snapshot frozen True + rate 0.06", pay_t['taxable_snapshot'] is True and pay_t['tax_rate_snapshot'] == 0.06)

# --- equipment margin % and flat + roll-forward via generate_invoice ---
c, LID, store = make_link_scenario()
A.upsert_margin(c, MASTER, LID, {'equip_class_value': 'device', 'method': 'percent', 'value': 0.15, 'markup_basis': 'cost'}, who='u')
A.upsert_margin(c, MASTER, LID, {'equip_class_value': 'accessory', 'method': 'flat', 'value': 8.0}, who='u')
A.upsert_charge(c, MASTER, LID, {'label': 'Marketing co-op', 'method': 'percent', 'value': 0.02, 'percent_basis': 'invoice_subtotal', 'cadence': 'per_invoice'}, who='u')
# confirmed device transfer (bills) + UNCONFIRMED accessory transfer (rolls forward)
A.add_transfer(c, MASTER, LID, {'equip_class_value': 'device', 'product_desc': 'Moto G', 'qty': 40, 'unit_cost': 90, 'period': 'July 2026'}, who='u')
acc = A.ingest_ocr(c, MASTER, LID, 'July 2026',
                   [{'equip_class_value': 'accessory', 'product_desc': 'Cases', 'qty': 120, 'unit_cost': 4.5}],
                   doc_path=None, doc_name='inv.pdf', model='stub', confidence=0.9, who='u')['transfers'][0]
g1 = A.generate_invoice(c, MASTER, LID, 'July 2026', who='u')
check("July draft: device margin 0.15×90×40 = 540 only (accessory unconfirmed)", g1['totals']['equipment_margin_total'] == 540.0)
check("July draft: co-op 2% of 540 = 10.80", round(g1['totals']['other_charge_total'], 2) == 10.8)
check("July draft: subtotal 550.80", g1['totals']['subtotal'] == 550.8 and g1['totals']['total'] == 550.8)
# idempotent regen
g1b = A.generate_invoice(c, MASTER, LID, 'July 2026', who='u')
check("idempotent draft regen → identical subtotal", g1b['totals']['subtotal'] == 550.8)
check("idempotent draft regen → identical line count", g1b['line_count'] == g1['line_count'])
check("regen did NOT create a 2nd invoice", len(A.list_invoices(c, MASTER, LID)['invoices']) == 1)
# now CONFIRM the accessory (gated in real life) → it becomes billable and rolls into the draft
A.confirm_transfer(c, MASTER, acc['id'], 'confirm', who='mgr')
g2 = A.generate_invoice(c, MASTER, LID, 'July 2026', who='u')
check("after confirm: equipment 540 + 8×120=960 → 1500", g2['totals']['equipment_margin_total'] == 1500.0)
check("after confirm: co-op 2% of 1500 = 30", g2['totals']['other_charge_total'] == 30.0 and g2['totals']['subtotal'] == 1530.0)

# --- issue freezes + stamps transfers; regen is a no-op; new period doesn't re-bill consumed transfers ---
inv_id = g2['invoice_id']
iss = A.issue_invoice(c, MASTER, inv_id, who='u')
check("issue consumed 2 transfers", iss['consumed_transfers'] == 2 and iss['status'] == 'issued')
# generating the same period again = immutable no-op
g3 = A.generate_invoice(c, MASTER, LID, 'July 2026', who='u')
check("generate on issued period → immutable no-op", g3.get('immutable') is True)
raises400(lambda: A.issue_invoice(c, MASTER, inv_id, who='u'), "issuing a non-draft rejected")
# August draft: transfers already billed (stamped) → NOT re-billed (no double-bill)
gaug = A.generate_invoice(c, MASTER, LID, 'August 2026', who='u')
check("no double-bill: August equipment margin = 0 (transfers consumed)", gaug['totals']['equipment_margin_total'] == 0.0)
check("per_invoice co-op recurs but 0 base → 0", gaug['totals']['subtotal'] == 0.0)
check("August is a distinct 2nd invoice", len(A.list_invoices(c, MASTER, LID)['invoices']) == 2)
# void the issued invoice → releases its transfers to bill again
A.void_invoice(c, MASTER, inv_id, who='u')
released = [t for t in store.get('agency_equipment_transfer', []) if t.get('billed_invoice_id') is None and t.get('confirm_status') == 'confirmed']
check("void released both consumed transfers", len(released) == 2)

# --- one_time vs monthly cadence ---
c, LID, store = make_link_scenario()
A.upsert_charge(c, MASTER, LID, {'label': 'Signage (one-time)', 'method': 'flat', 'value': 300, 'cadence': 'one_time'}, who='u')
A.upsert_charge(c, MASTER, LID, {'label': 'Platform fee', 'method': 'flat', 'value': 100, 'cadence': 'per_invoice'}, who='u')
gj = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
check("first period bills one_time 300 + per_invoice 100 = 400", gj['totals']['other_charge_total'] == 400.0)
A.issue_invoice(c, MASTER, gj['invoice_id'], who='u')
gjul = A.generate_invoice(c, MASTER, LID, 'July 2026', who='u')
check("second period: one_time NOT re-billed, per_invoice recurs → 100", gjul['totals']['other_charge_total'] == 100.0)


# ═══ (7) ORG ISOLATION ═════════════════════════════════════════════════════════════════════════════
print("\n(7) org isolation both directions")
c = newc()
la = A.upsert_link(c, ORGA, {'sub_kind': 'external', 'sub_name': 'A-sub'}, who='u')['link']
lb = A.upsert_link(c, ORGB, {'sub_kind': 'external', 'sub_name': 'B-sub'}, who='u')['link']
A.generate_invoice  # noqa (touch)
check("A lists only its own link", [l['id'] for l in A.list_links(c, ORGA)['links']] == [la['id']])
check("B lists only its own link", [l['id'] for l in A.list_links(c, ORGB)['links']] == [lb['id']])
raises400(lambda: A.get_link(c, ORGB, la['id']), "B cannot read A's link (404)")
raises400(lambda: A.get_link(c, ORGA, lb['id']), "A cannot read B's link (404)")
# invoices scoped
A.upsert_margin(c, ORGA, la['id'], {'equip_class_value': 'device', 'method': 'flat', 'value': 5}, who='u')
A.add_transfer(c, ORGA, la['id'], {'equip_class_value': 'device', 'qty': 1, 'period': 'June 2026'}, who='u')
A.generate_invoice(c, ORGA, la['id'], 'June 2026', who='u')
check("A has 1 invoice", len(A.list_invoices(c, ORGA)['invoices']) == 1)
check("B sees 0 invoices (isolation)", len(A.list_invoices(c, ORGB)['invoices']) == 0)


# ═══ (8) OCR CONFIRM AUTHORITY (core _can_edit_setting) + transition ═══════════════════════════════
print("\n(8) OCR confirm authority + transition")


def caller(role=None, super_admin=False, settings_perm=None, scope=None):
    perms = {}
    if scope:
        perms['scope'] = scope
    if settings_perm is not None:
        perms['settings'] = {'agency': settings_perm}
    return {'role': role, 'super_admin': super_admin, 'perms': perms}


check("super_admin may confirm", CORE._can_edit_setting(caller(super_admin=True), 'agency') is True)
check("explicit grant may confirm (even non-admin)", CORE._can_edit_setting(caller(role='manager', settings_perm=True), 'agency') is True)
check("explicit deny cannot (even admin)", CORE._can_edit_setting(caller(role='admin', settings_perm=False), 'agency') is False)
check("full-scope admin default (area unregistered) may confirm", CORE._can_edit_setting(caller(role='admin'), 'agency') is True)
check("non-admin default (unregistered) DENIED (safe degrade)", CORE._can_edit_setting(caller(role='rep'), 'agency') is False)
check("None caller → False (router wrapper turns this into allow)", CORE._can_edit_setting(None, 'agency') is False)
# transition
c = newc()
lk = A.upsert_link(c, MASTER, {'sub_kind': 'external', 'sub_name': 'X'}, who='u')['link']
t = A.ingest_ocr(c, MASTER, lk['id'], 'June 2026', [{'equip_class_value': 'device', 'qty': 1, 'unit_cost': 1}],
                 None, 'd.pdf', 'stub', 0.9, who='u')['transfers'][0]
check("OCR transfer lands unconfirmed", t['confirm_status'] == 'unconfirmed')
check("confirm → confirmed", A.confirm_transfer(c, MASTER, t['id'], 'confirm', 'mgr')['confirm_status'] == 'confirmed')
t2 = A.ingest_ocr(c, MASTER, lk['id'], 'June 2026', [{'equip_class_value': 'device', 'qty': 1, 'unit_cost': 1}],
                  None, 'd2.pdf', 'stub', 0.9, who='u')['transfers'][0]
check("reject → rejected", A.confirm_transfer(c, MASTER, t2['id'], 'reject', 'mgr')['confirm_status'] == 'rejected')
raises400(lambda: A.confirm_transfer(c, MASTER, 'nope', 'confirm', 'mgr'), "confirm unknown transfer 404s")
# a billed transfer cannot be un-confirmed
A.upsert_margin(c, MASTER, lk['id'], {'equip_class_value': 'device', 'method': 'flat', 'value': 3}, who='u')
gi = A.generate_invoice(c, MASTER, lk['id'], 'June 2026', who='u')
A.issue_invoice(c, MASTER, gi['invoice_id'], who='u')
billed = [x for x in store.get('agency_equipment_transfer', []) if x.get('billed_invoice_id')]
# find the confirmed+billed transfer in this client's store
bstore = c.store.get('agency_equipment_transfer', [])
billed_t = next((x for x in bstore if x.get('billed_invoice_id')), None)
if billed_t:
    raises400(lambda: A.confirm_transfer(c, MASTER, billed_t['id'], 'reject', 'mgr'), "cannot un-confirm a billed transfer")
else:
    check("cannot un-confirm a billed transfer (setup)", False, "no billed transfer found")


# ═══ GATE-1 REWORK — M1 exact-match sub lookup (anti-enumeration) ═════════════════════════════════
print("\n(R-M1) exact-match sub lookup — no browse-all, no oracle")
MSTR = 'org-mstr'
store = {'tenants': [
    {'org_id': 'sub-1', 'name': 'Luxelink', 'slug': 'luxelink', 'is_active': True},
    {'org_id': 'sub-2', 'name': 'Bright Wireless', 'slug': 'bright', 'is_active': True},
    {'org_id': MSTR, 'name': 'Master Co', 'slug': 'master', 'is_active': True},
], 'app_users': [
    {'org_id': 'sub-1', 'email': 'admin@luxelink.com', 'role': 'admin', 'is_active': True},
    {'org_id': 'sub-2', 'email': 'rep@bright.com', 'role': 'rep', 'is_active': True},
]}
c = newc(store)
check("no browse-all endpoint: agency has no sub_candidates", not hasattr(A, 'sub_candidates'))
check("exact slug → single tenant", A.lookup_sub_tenant(c, MSTR, 'luxelink')['tenant']['org_id'] == 'sub-1')
check("wrong slug → empty (no oracle)", A.lookup_sub_tenant(c, MSTR, 'luxe')['tenant'] is None)
check("substring slug → empty (exact only)", A.lookup_sub_tenant(c, MSTR, 'lux')['tenant'] is None)
check("empty query → empty", A.lookup_sub_tenant(c, MSTR, '')['tenant'] is None)
check("admin email → that org's tenant", A.lookup_sub_tenant(c, MSTR, 'admin@luxelink.com')['tenant']['org_id'] == 'sub-1')
check("non-admin email → empty (only org-admin email resolves)", A.lookup_sub_tenant(c, MSTR, 'rep@bright.com')['tenant'] is None)
check("self slug → empty (no self-link enumeration)", A.lookup_sub_tenant(c, MSTR, 'master')['tenant'] is None)
# cycle: sub-1 is already master of MSTR → looking up sub-1 returns empty (uniform, no oracle)
A.upsert_link(c, 'sub-1', {'sub_kind': 'tenant', 'sub_org_id': MSTR, 'sub_name': 'Master'}, who='u')
check("upstream-master slug → empty (would-be cycle, uniform empty)", A.lookup_sub_tenant(c, MSTR, 'luxelink')['tenant'] is None)


# ═══ GATE-1 REWORK — M3 write gate wiring (every write) ════════════════════════════════════════════
print("\n(R-M3) write endpoints gated by _require_agency_edit")
# (a) the helper resolves correctly (real core _can_edit_setting over monkeypatched caller-resolution)
_orig = (CORE._uid_from_token, CORE._resolve_caller, R.sb)
try:
    R.sb = lambda: None
    def _mk(setter):
        CORE._resolve_caller = lambda *a, **k: setter
    CORE._uid_from_token = lambda auth: ('uid' if auth else None)
    _mk({'role': 'rep', 'super_admin': False, 'perms': {}})
    check("_can_edit_agency: non-admin (unregistered area) → False", R._can_edit_agency('Bearer x', MSTR) is False)
    _mk({'role': 'admin', 'super_admin': False, 'perms': {}})
    check("_can_edit_agency: admin default → True", R._can_edit_agency('Bearer x', MSTR) is True)
    _mk({'role': 'rep', 'super_admin': False, 'perms': {'settings': {'agency': True}}})
    check("_can_edit_agency: explicit grant → True", R._can_edit_agency('Bearer x', MSTR) is True)
    _mk({'role': 'admin', 'super_admin': False, 'perms': {'settings': {'agency': False}}})
    check("_can_edit_agency: explicit deny (even admin) → False", R._can_edit_agency('Bearer x', MSTR) is False)
    CORE._uid_from_token = lambda auth: None
    check("_can_edit_agency: unresolved caller → True (require_org posture)", R._can_edit_agency('', MSTR) is True)
    # _require_agency_edit raises 403 when denied
    R.sb = lambda: None
    CORE._uid_from_token = lambda auth: 'uid'
    _mk({'role': 'rep', 'super_admin': False, 'perms': {}})
    raises(403, lambda: R._require_agency_edit('Bearer x', MSTR), "_require_agency_edit denies non-admin (403)")
finally:
    CORE._uid_from_token, CORE._resolve_caller, R.sb = _orig
# (b) SOURCE-SCAN: every agency POST/DELETE endpoint body calls _require_agency_edit
import re as _re
_rsrc = open(R.__file__).read()
_region = _rsrc[_rsrc.index('AGENCY MODULE (Phase 1) — Master-Agent'):]
_chunks = _re.split(r'\n@router\.', _region)
_writes = [ch for ch in _chunks if _re.match(r'(post|delete)\("/agency', ch)]
_ungated = [ch.splitlines()[0] for ch in _writes if '_require_agency_edit(authorization, org_id)' not in ch]
check(f"every agency write is gated ({len(_writes)} POST/DELETE endpoints, 0 ungated)", len(_writes) >= 19 and not _ungated,
      f"ungated: {_ungated}")


# ═══ GATE-1 REWORK — M3 delete_link 409 with issued invoice ═══════════════════════════════════════
print("\n(R-M3b) delete_link refuses when issued invoices exist")
c, LID, store = make_link_scenario()
A.upsert_margin(c, MASTER, LID, {'equip_class_value': 'device', 'method': 'flat', 'value': 5}, who='u')
A.add_transfer(c, MASTER, LID, {'equip_class_value': 'device', 'qty': 1, 'period': 'June 2026'}, who='u')
gi = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
# draft only → delete ok
c2, LID2, _ = make_link_scenario()
A.generate_invoice(c2, MASTER, LID2, 'June 2026', who='u')  # a draft
check("delete_link with only a DRAFT invoice → ok", A.delete_link(c2, MASTER, LID2).get('ok') is True)
# issued → 409
A.issue_invoice(c, MASTER, gi['invoice_id'], who='u')
raises(409, lambda: A.delete_link(c, MASTER, LID), "delete_link with ISSUED invoice → 409")
# void it → delete ok
A.void_invoice(c, MASTER, gi['invoice_id'], who='u')
check("delete_link after void → ok", A.delete_link(c, MASTER, LID).get('ok') is True)


# ═══ GATE-1 REWORK — M4 C7 period-anchor + split_period reject ════════════════════════════════════
print("\n(R-M4) C7 period-anchor effectiveness + split_period de-scoped")
# split_period rejected at link save
raises(400, lambda: A.upsert_link(newc(), 'o', {'sub_kind': 'external', 'sub_name': 'x', 'rate_change_mode': 'split_period'}),
       "rate_change_mode='split_period' rejected at save")
check("rate_change_mode='period_anchor' accepted", A.upsert_link(newc(), 'o', {'sub_kind': 'external', 'sub_name': 'x', 'rate_change_mode': 'period_anchor'})['ok'])
# holdback: old rule ENDS Jun 15, new STARTS Jun 16 → period_anchor (period_end) picks NEW for June AND July
old_r = rule('all', method='percent', value=0.05, prio=100, ee='2026-06-15')
new_r = rule('all', method='percent', value=0.20, prio=100, es='2026-06-16')
JUN_S, JUN_E = _date(2026, 6, 1), _date(2026, 6, 30)
JUL_S, JUL_E = _date(2026, 7, 1), _date(2026, 7, 31)
check("June anchor(Jun30): superseding NEW rule wins (0.20), old excluded",
      AB.resolve_holdback_rule([old_r, new_r], {}, JUN_S, JUN_E, anchor=JUN_E)['value'] == 0.20)
check("July anchor(Jul31): NEW rule (0.20), old long gone",
      AB.resolve_holdback_rule([old_r, new_r], {}, JUL_S, JUL_E, anchor=JUL_E)['value'] == 0.20)
# a mid-period-superseded margin: the anchor rule (in effect on period_end) governs the whole invoice
c, LID, store = make_link_scenario()
A.upsert_margin(c, MASTER, LID, {'equip_class_value': 'device', 'method': 'flat', 'value': 10, 'effective_end': '2026-06-15'}, who='u')
A.upsert_margin(c, MASTER, LID, {'equip_class_value': 'device', 'method': 'flat', 'value': 25, 'effective_start': '2026-06-16'}, who='u')
A.add_transfer(c, MASTER, LID, {'equip_class_value': 'device', 'qty': 2, 'period': 'June 2026'}, who='u')
gc7 = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
check("invoice uses the period-anchor margin (25×2=50, NOT the ended 10)", gc7['totals']['equipment_margin_total'] == 50.0)


# ═══ GATE-1 REWORK — m1 regen releases stamps pointing at THIS draft ══════════════════════════════
print("\n(R-m1) draft regen releases its own stamps (crash-window under-bill fix)")
c, LID, store = make_link_scenario()
A.upsert_margin(c, MASTER, LID, {'equip_class_value': 'device', 'method': 'flat', 'value': 7}, who='u')
A.add_transfer(c, MASTER, LID, {'equip_class_value': 'device', 'qty': 1, 'period': 'June 2026'}, who='u')
g = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
# simulate a CRASHED issue: stamp the transfer to this draft WITHOUT flipping status to issued
tstore = c.store['agency_equipment_transfer']
tstore[0]['billed_invoice_id'] = g['invoice_id']
# a naive regen would exclude the stamped transfer → under-bill (0); the fix releases it first → 7 again
g2 = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
check("regen released the self-stamp → transfer re-billed (7, not 0)", g2['totals']['equipment_margin_total'] == 7.0)


# ═══ GATE-1 REWORK — m2 one_time re-bills after VOID (symmetric with transfer release) ════════════
print("\n(R-m2) one_time re-bills after void; void doesn't block re-draft")
c, LID, store = make_link_scenario()
A.upsert_charge(c, MASTER, LID, {'label': 'Signage', 'method': 'flat', 'value': 300, 'cadence': 'one_time'}, who='u')
gj = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
check("June bills one_time 300", gj['totals']['other_charge_total'] == 300.0)
A.issue_invoice(c, MASTER, gj['invoice_id'], who='u')
A.void_invoice(c, MASTER, gj['invoice_id'], who='u')
# same period re-draft is NOT blocked by the void, and the one_time is billable again
gj2 = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
check("void does NOT block re-draft (not immutable)", gj2.get('immutable') is not True)
check("one_time re-bills after its invoice was voided (300)", gj2['totals']['other_charge_total'] == 300.0)
# but a one_time on a LIVE (issued, non-void) invoice does NOT re-bill in the next period
A.issue_invoice(c, MASTER, gj2['invoice_id'], who='u')
gjul = A.generate_invoice(c, MASTER, LID, 'July 2026', who='u')
check("one_time on a live invoice does NOT re-bill next period (0)", gjul['totals']['other_charge_total'] == 0.0)


# ═══ GATE-1 REWORK — m4 reject-then-issue refuses a stale bill ════════════════════════════════════
print("\n(R-m4) issue re-checks confirm_status (reject-then-issue → 409)")
c, LID, store = make_link_scenario()
A.upsert_margin(c, MASTER, LID, {'equip_class_value': 'device', 'method': 'flat', 'value': 9}, who='u')
tr = A.add_transfer(c, MASTER, LID, {'equip_class_value': 'device', 'qty': 1, 'period': 'June 2026'}, who='u')['transfer']
gm = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
check("draft billed the confirmed transfer (9)", gm['totals']['equipment_margin_total'] == 9.0)
# reject the transfer AFTER the draft was computed
A.confirm_transfer(c, MASTER, tr['id'], 'reject', who='mgr')
raises(409, lambda: A.issue_invoice(c, MASTER, gm['invoice_id'], who='u'), "issue refuses when a billed transfer was rejected (409)")
# regenerate drops the line, then issue succeeds
gm2 = A.generate_invoice(c, MASTER, LID, 'June 2026', who='u')
check("regen drops the rejected transfer's line (margin 0)", gm2['totals']['equipment_margin_total'] == 0.0)
check("issue now succeeds", A.issue_invoice(c, MASTER, gm['invoice_id'], who='u')['status'] == 'issued')


print(f"\n==== agency_phase1_proof: {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
