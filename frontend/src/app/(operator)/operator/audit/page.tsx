'use client'
// OPERATOR TRAIL — every operator action, hash-chained and verifiable.
//
// WHY A CHAIN AND NOT JUST A TABLE. `core.access_log` (mig 856) already records every HTTP request
// with the caller's auth id, and it is a good per-request trail. What it cannot express is INTENT
// ("entered LuxeLink because they reported a bad commission figure, until 14:35"), and — more to the
// point — it is an ordinary table that the person it is auditing also administers.
//
// So `core.operator_action` is append-only AND hash-chained: each row seals its own fields plus the
// previous row's hash. Editing or deleting any row breaks every link after it, and `verify_chain`
// reports the exact `seq` where the chain parts. It cannot PREVENT a service-role edit — nothing on
// a database you own can — but it makes one undeniable, which is what a tamper-EVIDENT audit is.
// The chain verdict below is computed over the WHOLE chain, never the filtered page: verifying a
// filtered subset would report a break for every row the filter removed.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { OPS, fmtWhen } from '@/lib/operator'
import { useOperator } from '@/lib/operator-context'
import { H1, Panel, Table, td, Err, Empty, Note, Lamp, Btn, Field, inputStyle } from '@/lib/operator-ui'

export default function OperatorAudit() {
  const me = useOperator()
  const [rows, setRows] = useState<any[]>([])
  const [chain, setChain] = useState<any>(null)
  const [findings, setFindings] = useState<any[]>([])
  const [actor, setActor] = useState('')
  const [err, setErr] = useState('')

  function load() {
    const q = actor ? `&actor=${encodeURIComponent(actor)}` : ''
    api(`/api/v1/core/operator/audit?limit=400${q}`)
      .then((d: any) => { setRows(d.rows || []); setChain(d.chain); if (d.error) setErr(d.error) })
      .catch(e => setErr(e?.message || 'Could not load the trail'))
  }
  useEffect(() => { if (me) { load(); api('/api/v1/core/operator/anomalies')
    .then((d: any) => setFindings(d.findings || [])).catch(() => {}) } }, [me])

  if (!me) return null

  return (
    <div style={{ maxWidth: 1120 }}>
      <H1 sub={<>Every action a platform operator took, recorded under their own account — never
        anonymised behind the company they were acting on.</>}>Operator trail</H1>

      <Err>{err}</Err>

      <Panel title="Chain integrity">
        {chain ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
            <Lamp lamp={chain.ok ? 'green' : 'red'} size={10} />
            <span>
              {chain.ok
                ? `${chain.length} sealed actions, chain intact.`
                : `TAMPERING DETECTED — the chain breaks at action #${chain.broken_at} (${chain.reason}).`}
            </span>
          </div>
        ) : <Empty>No trail yet (or migration 980 has not run).</Empty>}
        {chain && !chain.ok && (
          <Note tone="bad">
            A row at or before #{chain.broken_at} was edited or deleted after it was written. Everything
            up to that point is still provably intact; nothing after it can be trusted on its own.
          </Note>
        )}
      </Panel>

      {findings.length > 0 && (
        <Panel title="Unusual operator activity">
          {findings.map((f, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '6px 0' }}>
              <Lamp lamp={f.severity} />
              <span style={{ fontSize: 13 }}>{f.message}</span>
              <span style={{ color: OPS.text3, fontSize: 11.5 }}>{f.actor_email || f.actor_auth_id}</span>
            </div>
          ))}
        </Panel>
      )}

      <Panel title="Actions" right={
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <Field label="Filter by operator">
            <input value={actor} onChange={e => setActor(e.target.value)} style={{ ...inputStyle, width: 210 }}
              placeholder="email" />
          </Field>
          <Btn tone="ghost" onClick={load}>Apply</Btn>
        </div>}>
        <Table head={['#', 'When', 'Operator', 'Action', 'Company', 'Detail']}>
          {rows.map(r => (
            <tr key={r.id || r.seq}>
              <td style={{ ...td, color: OPS.text3 }}>{r.seq}</td>
              <td style={{ ...td, color: OPS.text3, whiteSpace: 'nowrap' }}>{fmtWhen(r.created_at)}</td>
              <td style={td}>{r.actor_email || r.actor_auth_id}</td>
              <td style={{ ...td, color: r.action?.endsWith('.denied') ? OPS.bad : OPS.accent }}>{r.action}</td>
              <td style={{ ...td, color: OPS.text2, fontSize: 11.4 }}>{r.target_ref || r.target_org_id || '—'}</td>
              <td style={{ ...td, color: OPS.text3, fontSize: 11.4 }}>
                {r.detail ? JSON.stringify(r.detail) : ''}</td>
            </tr>
          ))}
        </Table>
        {rows.length === 0 && <Empty>Nothing recorded yet.</Empty>}
      </Panel>
    </div>
  )
}
