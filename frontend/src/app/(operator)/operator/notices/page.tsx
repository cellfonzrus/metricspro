'use client'
// STATUS NOTICES — the operator → tenants broadcast.
//
// A RESEARCHED GAP, NOT A REQUESTED FEATURE. The platform had no way to tell its customers "we are
// doing maintenance at 02:00" or "carrier ingest is degraded right now". Every tenant discovered an
// incident by noticing a number looked wrong, then opening a support ticket. This is the smallest
// honest fix: a severity, a window, and an audience.
//
// DUPLICATE CHECK: `/admin/whats-new` is a per-tenant MARKETING changelog for shipped features — no
// severity, no window, no incident semantics. Different question, different lifetime, so a separate
// table (mig 981) rather than an overloaded one.
//
// CROSS-TENANT DISCIPLINE (§19.15). Audience is by org_id, never by tenant name. The tenant-facing
// read resolves the caller's org from their VERIFIED membership — never from the request — and the
// response strips `org_ids`, so a tenant can never learn which OTHER tenants a notice was aimed at.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { OPS, fmtWhen } from '@/lib/operator'
import { useOperator } from '@/lib/operator-context'
import { H1, Panel, Table, td, Btn, Field, inputStyle, Err, Empty, Note, Lamp } from '@/lib/operator-ui'

const SEV_LAMP: Record<string, string> = {
  info: 'green', maintenance: 'amber', degraded: 'amber', outage: 'red',
}

export default function NoticesPage() {
  const me = useOperator()
  const [rows, setRows] = useState<any[]>([])
  const [tenants, setTenants] = useState<any[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [f, setF] = useState({ severity: 'maintenance', title: '', body: '', starts_at: '', ends_at: '', org_ids: [] as string[] })

  const load = useCallback(() => {
    api('/api/v1/core/operator/notices?include_expired=true')
      .then((d: any) => { setRows(d.rows || []); if (d.error) setErr(d.error) })
      .catch(e => setErr(e?.message || 'Could not load notices'))
    api('/api/v1/core/tenants').then((d: any) => setTenants(d.tenants || [])).catch(() => {})
  }, [])
  useEffect(() => { if (me) load() }, [me, load])

  async function publish() {
    setBusy(true); setErr('')
    try {
      await api('/api/v1/core/operator/notices', { method: 'POST', body: JSON.stringify(f) })
      setF({ severity: 'maintenance', title: '', body: '', starts_at: '', ends_at: '', org_ids: [] })
      load()
    } catch (e: any) { setErr(e?.message || 'Could not publish') } finally { setBusy(false) }
  }

  async function withdraw(id: string) {
    try { await api('/api/v1/core/operator/notices/withdraw', { method: 'POST', body: JSON.stringify({ id }) }); load() }
    catch (e: any) { setErr(e?.message || 'Could not withdraw') }
  }

  if (!me) return null

  return (
    <div style={{ maxWidth: 1050 }}>
      <H1 sub={<>Tell every company — or specific companies — what is happening to the platform. A
        notice with an end time takes itself down, so nobody is left staring at last month’s
        maintenance banner.</>}>Status notices</H1>

      <Err>{err}</Err>

      <Panel title="Publish">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Field label="Severity">
            <select value={f.severity} onChange={e => setF({ ...f, severity: e.target.value })} style={inputStyle}>
              {['info', 'maintenance', 'degraded', 'outage'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="Title">
            <input value={f.title} onChange={e => setF({ ...f, title: e.target.value })} style={inputStyle}
              placeholder="Scheduled maintenance tonight" />
          </Field>
          <Field label="Starts" hint="Blank = now">
            <input type="datetime-local" value={f.starts_at}
              onChange={e => setF({ ...f, starts_at: e.target.value })} style={inputStyle} />
          </Field>
          <Field label="Ends" hint="Blank = until withdrawn">
            <input type="datetime-local" value={f.ends_at}
              onChange={e => setF({ ...f, ends_at: e.target.value })} style={inputStyle} />
          </Field>
        </div>
        <div style={{ margin: '10px 0' }}>
          <Field label="Message">
            <textarea value={f.body} onChange={e => setF({ ...f, body: e.target.value })} rows={3}
              style={{ ...inputStyle, fontFamily: 'inherit' }} />
          </Field>
        </div>
        <Field label="Companies" hint="None selected = every company on the platform.">
          <select multiple value={f.org_ids} size={Math.min(6, Math.max(3, tenants.length))}
            onChange={e => setF({ ...f, org_ids: Array.from(e.target.selectedOptions).map(o => o.value) })}
            style={{ ...inputStyle, height: 'auto' }}>
            {tenants.map(t => <option key={t.org_id} value={t.org_id}>{t.name}</option>)}
          </select>
        </Field>
        <div style={{ marginTop: 12 }}>
          <Btn onClick={publish} disabled={busy || f.title.trim().length < 3}>
            {busy ? 'Publishing…' : 'Publish notice'}</Btn>
        </div>
      </Panel>

      <Panel title="Notices">
        <Table head={['', 'Severity', 'Title', 'Window', 'Audience', 'Live', '']}>
          {rows.map(n => (
            <tr key={n.id}>
              <td style={td}><Lamp lamp={SEV_LAMP[n.severity] || 'amber'} /></td>
              <td style={{ ...td, color: OPS.text2 }}>{n.severity}</td>
              <td style={td}>
                <div style={{ fontWeight: 600 }}>{n.title}</div>
                <div style={{ color: OPS.text3, fontSize: 11.4 }}>{n.body}</div>
              </td>
              <td style={{ ...td, color: OPS.text3, fontSize: 11.4, whiteSpace: 'nowrap' }}>
                {fmtWhen(n.starts_at)} → {n.ends_at ? fmtWhen(n.ends_at) : 'until withdrawn'}</td>
              <td style={{ ...td, color: OPS.text2 }}>
                {n.org_ids?.length ? `${n.org_ids.length} companies` : 'all companies'}</td>
              <td style={td}>{n.live ? 'yes' : 'no'}</td>
              <td style={{ ...td, textAlign: 'right' }}>
                {n.is_active && <Btn tone="danger" onClick={() => withdraw(n.id)}>Withdraw</Btn>}
              </td>
            </tr>
          ))}
        </Table>
        {rows.length === 0 && <Empty>No notices published.</Empty>}
        <Note>Tenants see live notices as a banner at the top of the application.</Note>
      </Panel>
    </div>
  )
}
