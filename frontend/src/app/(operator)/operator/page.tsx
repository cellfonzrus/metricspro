'use client'
// OPERATOR CONSOLE HOME — the platform at a glance, and the state of the separation itself.
//
// COMPOSES, DOES NOT RE-DERIVE (CLAUDE.md duplicate gate). Every number on this page comes from a
// mechanism that already existed: tenant counts from `GET /core/tenants`, health from the CONTROL
// BOX's own platform view (`GET /core/control-box/platform` — the ONE cross-org surface, lamps and
// counts only, never another tenant's figures), and the operator trail from this console's own
// hash-chained log. Nothing here forms a second opinion about anything.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { OPS, fmtWhen, can } from '@/lib/operator'
import { useOperator } from '@/lib/operator-context'
import { Panel, Row, Note, Stat, Lamp } from '@/lib/operator-ui'

export default function OperatorHome() {
  const me = useOperator()
  const [tenants, setTenants] = useState<any[]>([])
  const [platform, setPlatform] = useState<any>(null)
  const [chain, setChain] = useState<any>(null)
  const [recent, setRecent] = useState<any[]>([])
  const [findings, setFindings] = useState<any[]>([])
  const [drill, setDrill] = useState<any>(null)

  useEffect(() => {
    if (!me) return
    if (can(me, 'tenant.read')) api('/api/v1/core/tenants').then((d: any) => setTenants(d.tenants || [])).catch(() => {})
    if (can(me, 'control_box.read')) {
      api('/api/v1/core/control-box/platform').then(setPlatform).catch(() => {})
      api('/api/v1/core/operator/restore-drill').then(setDrill).catch(() => {})
    }
    if (can(me, 'audit.read')) {
      api('/api/v1/core/operator/audit?limit=8').then((d: any) => { setChain(d.chain); setRecent(d.rows || []) }).catch(() => {})
      api('/api/v1/core/operator/anomalies').then((d: any) => setFindings(d.findings || [])).catch(() => {})
    }
  }, [me])

  if (!me) return null
  const active = tenants.filter(t => t.is_active !== false).length
  const onlyLegacy = me.sources.length === 1 && me.sources[0] === 'legacy'

  return (
    <div style={{ maxWidth: 1080 }}>
      <h1 style={h1}>Platform</h1>
      <p style={sub}>
        You are operating MetricsPro itself. Companies you administer from here are your customers —
        their data is theirs, and everything you do in one is logged under your own account.
      </p>

      {/* ── THE SEPARATION STATUS. The honest state of this migration, on the front page, because a
             half-finished separation that nobody can see is the failure mode worth designing against. */}
      <Panel title="Operator identity">
        <Row label="Signed in as" value={me.email} />
        <Row label="Operator role" value={me.operator_role || '— (legacy super-admin, unscoped)'} />
        <Row label="Authority comes from" value={
          onlyLegacy
            ? 'the super_admin flag on your tenant membership (not yet separated)'
            : me.sources.join(' + ')} warn={onlyLegacy} />
        <Row label="Platform operator records" value={String(me.active_registry_operators)} />
        <Row label="Legacy tenant flag still honored" value={me.legacy_honored ? 'yes' : 'no — cutover done'} />
        {onlyLegacy && (
          <Note>
            Your platform access is still a column on your employee record inside a tenant. Migration
            980 seeds a platform-operator record for every existing super-admin; once you can see
            yourself on <Link href="/operator/operators" style={link}>Operators</Link>, you can switch
            the legacy flag off there. Nothing is switched off for you — that step is yours, and it is
            reversible.
          </Note>
        )}
      </Panel>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(250px,1fr))', gap: 14 }}>
        {can(me, 'tenant.read') && (
          <Stat label="Companies" value={String(tenants.length)} hint={`${active} active`}
            href="/operator/tenants" />
        )}
        {can(me, 'control_box.read') && (
          <Stat label="Platform health" value={platform?.lamp || '—'} lamp={platform?.lamp}
            hint={platform?.headline || 'from the System Control Box'} href="/admin/control-box" />
        )}
        {can(me, 'audit.read') && (
          <Stat label="Operator trail" value={chain ? (chain.ok ? 'intact' : 'BROKEN') : '—'}
            lamp={chain ? (chain.ok ? 'green' : 'red') : undefined}
            hint={chain ? `${chain.length} sealed actions` : 'hash-chained'} href="/operator/audit" />
        )}
        {can(me, 'control_box.read') && (
          <Stat label="Backup restore drill" value={drill?.lamp || '—'} lamp={drill?.lamp}
            hint={drill?.reason || 'never tested'} href="/operator/tenants" />
        )}
      </div>

      {findings.length > 0 && (
        <Panel title="Needs a look — operator activity">
          {findings.map((f, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '7px 0',
              borderBottom: i < findings.length - 1 ? `1px solid ${OPS.border}` : 'none' }}>
              <Lamp lamp={f.severity} size={8} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13 }}>{f.message}</div>
                <div style={{ color: OPS.text3, fontSize: 11.5 }}>{f.actor_email || f.actor_auth_id} · {f.kind}</div>
              </div>
            </div>
          ))}
        </Panel>
      )}

      {can(me, 'audit.read') && recent.length > 0 && (
        <Panel title="Latest operator actions" right={<Link href="/operator/audit" style={link}>Full trail →</Link>}>
          {recent.map((r, i) => (
            <div key={r.id || i} style={{ display: 'flex', gap: 12, fontSize: 12.6, padding: '6px 0',
              borderBottom: i < recent.length - 1 ? `1px solid ${OPS.border}` : 'none' }}>
              <span style={{ color: OPS.text3, minWidth: 150 }}>{fmtWhen(r.created_at)}</span>
              <span style={{ color: OPS.accent, minWidth: 150 }}>{r.action}</span>
              <span style={{ color: OPS.text2, flex: 1 }}>{r.actor_email}</span>
              <span style={{ color: OPS.text3 }}>{r.target_ref || ''}</span>
            </div>
          ))}
        </Panel>
      )}
    </div>
  )
}

// The console's shared chrome lives in `@/lib/operator-ui` — five pages need the same panel, and a
// second copy of it here is exactly the duplication this codebase's build gate exists to prevent.
const h1: React.CSSProperties = { fontSize: 22, fontWeight: 700, margin: '0 0 6px', color: OPS.text }
const sub: React.CSSProperties = { color: OPS.text2, fontSize: 13.3, lineHeight: 1.6, margin: '0 0 20px', maxWidth: 720 }
const link: React.CSSProperties = { color: OPS.accent, textDecoration: 'none' }
