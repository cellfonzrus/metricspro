'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// Chargeback & fraud bucket — candidates (VIP file now; fraud detectors next) ASSIGNED to the rep
// who did the sale, which writes the employee's chargeback. Fraud-review rows can be dismissed
// (approved as legit) or assigned (disapproved → charged to the rep).
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tin: React.CSSProperties = { ...sel, width: 130 }
const cell: React.CSSProperties = { padding: '8px 10px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'top' }
const SRC: Record<string, string> = { vip_file: 'VIP file', fraud_email: 'Fake/reused email', fraud_dupe: 'Duplicate ID', analyzer_churn: 'Early churn', manual: 'Manual' }

type Edit = { rep: string; amount: string; reason: string }

export default function ChargebacksPage() {
  const { user } = useAuth()
  const [status, setStatus] = useState('open')
  const [source, setSource] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [edit, setEdit] = useState<Record<string, Edit>>({})
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api(`/api/v1/commcalc/chargeback-review?${status ? `status=${status}&` : ''}${source ? `source=${source}` : ''}`)
      .then((d: any) => {
        setData(d)
        const e: Record<string, Edit> = {}
        ;(d.rows || []).forEach((r: any) => { e[r.id] = { rep: r.assigned_rep || r.suggested_rep || '', amount: String(r.amount ?? ''), reason: r.reason || '' } })
        setEdit(e)
      }).catch(console.error).finally(() => setLoading(false))
  }, [status, source])
  useEffect(() => { load() }, [load])

  const set = (id: string, patch: Partial<Edit>) => setEdit(p => ({ ...p, [id]: { ...p[id], ...patch } }))

  async function scan() {
    setBusy('scan'); setMsg('')
    try {
      const r = await api('/api/v1/commcalc/chargeback-review/scan-fraud', { method: 'POST' })
      setMsg(`✅ Scanned ${r.scanned} activations — ${r.email_flags} email flag(s), ${r.dupe_flags} duplicate-ID flag(s).`)
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  async function act(id: string, kind: 'assign' | 'dismiss' | 'reopen') {
    setBusy(id + kind); setMsg('')
    try {
      const e = edit[id] || {} as Edit
      const body = kind === 'assign'
        ? { rep: e.rep, amount: e.amount, reason: e.reason, assigned_by: user?.full_name || 'admin' }
        : { reviewed_by: user?.full_name || 'admin', reason: e.reason }
      const r = await api(`/api/v1/commcalc/chargeback-review/${id}/${kind}`, { method: 'POST', body: JSON.stringify(body) })
      if (kind === 'assign') setMsg(`✅ Assigned ${fmt(r.amount)} to ${r.rep} (${r.period || 'period n/a'}).`)
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  const rows: any[] = data?.rows || []
  const c = data?.counts || {}

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔻 Chargebacks & Fraud</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Assign each chargeback to the rep who did the sale — that writes it into the employee's chargeback. Fraud reviews can be dismissed (legit) or assigned (charged).
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <select style={sel} value={status} onChange={e => setStatus(e.target.value)}>
          <option value="open">Open ({c.open || 0})</option>
          <option value="assigned">Assigned ({c.assigned || 0})</option>
          <option value="dismissed">Dismissed ({c.dismissed || 0})</option>
          <option value="">All</option>
        </select>
        <select style={sel} value={source} onChange={e => setSource(e.target.value)}>
          <option value="">All sources</option>
          <option value="vip_file">VIP file</option>
          <option value="fraud_email">Fake/reused email</option>
          <option value="fraud_dupe">Duplicate ID</option>
          <option value="analyzer_churn">Early churn</option>
        </select>
        <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={busy === 'scan'} onClick={scan}>
          {busy === 'scan' ? '⏳ Scanning…' : '🔍 Scan sales for fraud'}
        </button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>Nothing here. VIP chargebacks stage automatically on the VIP sweep.</div>
      ) : (
        <div className="card table-wrapper" style={{ padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Source', 'Store', 'Customer / device', 'Amount', 'When', 'Assign to rep', ''].map((h, i) =>
                <th key={i} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map(r => {
                const e = edit[r.id] || {} as Edit
                const done = r.status !== 'open'
                const crit = r.severity === 'critical'
                return (
                  <tr key={r.id} style={{ borderLeft: crit ? '3px solid #b42318' : undefined }}>
                    <td style={cell}>
                      <div style={{ fontWeight: 600 }}>{SRC[r.source] || r.source}</div>
                      {crit && <span style={{ fontSize: 10, color: '#b42318', fontWeight: 700 }}>CRITICAL</span>}
                      {r.needs_review && <span style={{ fontSize: 10, color: 'var(--text3)' }}>{r.review ? ` · ${r.review}` : ' · needs review'}</span>}
                    </td>
                    <td style={cell}>{r.store_address || r.store_code || '—'}</td>
                    <td style={cell}>
                      <div>{r.customer_name || r.email || '—'}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)' }}>{[r.email, r.phone_number, r.esn || r.imei].filter(Boolean).join(' · ')}</div>
                      {r.detail && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.detail}</div>}
                    </td>
                    <td style={cell}>{done ? fmt(r.amount) : <input style={{ ...tin, width: 90 }} value={e.amount} onChange={ev => set(r.id, { amount: ev.target.value })} />}</td>
                    <td style={cell}>{r.occurred_date || r.period || '—'}</td>
                    <td style={cell}>
                      {done
                        ? <span style={{ fontSize: 12 }}>{r.assigned_rep ? `→ ${r.assigned_rep}` : <span style={{ color: 'var(--text3)' }}>{r.status}</span>}{r.reason ? <span style={{ color: 'var(--text3)' }}> · {r.reason}</span> : ''}</span>
                        : <>
                            <input style={tin} list={`reps-${r.id}`} placeholder="rep" value={e.rep} onChange={ev => set(r.id, { rep: ev.target.value })} />
                            {r.suggested_rep && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>suggested: {r.suggested_rep}</div>}
                            <input style={{ ...tin, marginTop: 4 }} placeholder="reason" value={e.reason} onChange={ev => set(r.id, { reason: ev.target.value })} />
                          </>}
                    </td>
                    <td style={cell}>
                      {done
                        ? <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy === r.id + 'reopen'} onClick={() => act(r.id, 'reopen')}>Reopen</button>
                        : <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                            <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={!e.rep || busy === r.id + 'assign'} onClick={() => act(r.id, 'assign')}>{busy === r.id + 'assign' ? '…' : 'Assign → charge'}</button>
                            <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy === r.id + 'dismiss'} onClick={() => act(r.id, 'dismiss')}>{r.needs_review ? 'Approve (legit)' : 'Dismiss'}</button>
                          </div>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
