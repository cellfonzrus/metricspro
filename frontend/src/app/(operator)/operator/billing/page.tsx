'use client'
// TENANT BILLING — the operator console's PLACEMENT for billing (owner 2026-09-05: "Tennat billing
// dashboard will be another module on the super admin side").
//
// ★ THIS PAGE OWNS NO BILLING LOGIC, ON PURPOSE. ★
// Billing already exists and is actively being extended by another workstream:
//   · `/admin/billing`  — per-tenant plans, subscriptions and invoices (backend
//                          `app/modules/billing/router.py`, super-admin gated on every endpoint);
//   · `/admin/pricing`  — the public price list and free-trial length (mig 908, `billing/pricing.py`);
//   · per-tenant AI usage metering and margin pricing — in flight in the same `billing/` module
//     (migrations 973+), landing alongside this change.
//
// Building a billing dashboard here would have been exactly the duplicate derivation the CLAUDE.md
// build gate forbids: two surfaces answering "what does this tenant owe" WILL drift. So the console
// contributes the one thing it legitimately owns — PLACEMENT and NAVIGATION on the operator side —
// and links to the billing module as the content.
//
// ASSUMPTION RECORDED (see the PR comment): the billing workstream keeps its surfaces at
// `/admin/billing` and `/admin/pricing`. If it introduces new operator-facing pages, they belong in
// the list below and in `operator.CONSOLE_SECTIONS` — a nav entry, not a reimplementation.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { OPS, can, fmtWhen } from '@/lib/operator'
import { useOperator } from '@/lib/operator-context'
import { H1, Panel, Table, td, Empty, Note, Lamp } from '@/lib/operator-ui'

export default function OperatorBilling() {
  const me = useOperator()
  const [tenants, setTenants] = useState<any[]>([])
  useEffect(() => {
    if (me && can(me, 'tenant.read')) {
      api('/api/v1/core/tenants').then((d: any) => setTenants(d.tenants || [])).catch(() => {})
    }
  }, [me])
  if (!me) return null

  return (
    <div style={{ maxWidth: 1080 }}>
      <H1 sub={<>Plans, invoices, usage and margin, per company. The billing module owns the numbers;
        this is where an operator reaches them.</>}>Tenant billing</H1>

      <Panel title="Billing surfaces">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 12 }}>
          <Card href="/admin/billing" icon="💳" title="Plans & invoices"
            body="Per-tenant subscription, plan modules, invoices and payment state." />
          <Card href="/admin/pricing" icon="🏷️" title="Pricing & free trial"
            body="The public price list and how long a new company's trial runs." />
          <Card href="/admin/control-box" icon="🛎️" title="AI spend & budgets"
            body="Per-org AI call budgets and token accounting feed the platform control box." />
        </div>
        <Note>
          These open in the tenant application’s admin area, which is where the billing module lives.
          Nothing about billing is duplicated here — one set of numbers, one place they are computed.
        </Note>
      </Panel>

      <Panel title="Companies by plan state">
        <Table head={['Company', 'Plan', 'Trial ends', 'Users', 'Logins']}>
          {tenants.map(t => (
            <tr key={t.org_id}>
              <td style={td}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Lamp lamp={t.is_active === false ? 'unmonitored' : 'green'} />{t.name}
                </div>
              </td>
              <td style={{ ...td, color: OPS.text2 }}>{t.plan_status || t.package_key || '—'}</td>
              <td style={{ ...td, color: OPS.text3 }}>{t.trial_ends_at ? fmtWhen(t.trial_ends_at) : '—'}</td>
              <td style={td}>{t.users ?? '—'}</td>
              <td style={td}>{t.logins ?? '—'}</td>
            </tr>
          ))}
        </Table>
        {tenants.length === 0 && <Empty>No companies.</Empty>}
        <Note>
          Plan state comes from <code>storeops.tenants</code> (mig 908), the same row the billing
          module reads. This table is a directory, not a second computation.
        </Note>
      </Panel>
    </div>
  )
}

function Card({ href, icon, title, body }: { href: string; icon: string; title: string; body: string }) {
  return (
    <Link href={href} style={{ textDecoration: 'none', display: 'block', padding: '13px 15px',
      borderRadius: 10, border: `1px solid ${OPS.border}`, background: OPS.panelSoft }}>
      <div style={{ fontSize: 19, marginBottom: 6 }} aria-hidden>{icon}</div>
      <div style={{ color: OPS.text, fontWeight: 600, fontSize: 13.4, marginBottom: 4 }}>{title} ↗</div>
      <div style={{ color: OPS.text3, fontSize: 12, lineHeight: 1.55 }}>{body}</div>
    </Link>
  )
}
