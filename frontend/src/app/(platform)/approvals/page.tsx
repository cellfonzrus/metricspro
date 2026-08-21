'use client'
// Unified Approvals inbox (owner directive 2026-08-19). ONE place every module's approval/intimation
// request surfaces — the tick performs the module's real effect via the backend engine. Phase 1 covers
// time-clock permissions (the pilot); more request types light up here as each module is adapted.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { errText, loadErrorText, panelState, type Tab } from '@/lib/approvals'

interface Approval {
  id: string; request_no?: number; type: string; title: string; summary?: string | null
  status: string; priority?: string; store_code?: string | null; requested_by_name?: string | null
  decided_by?: string | null; decided_at?: string | null; created_at?: string
}

const badge: Record<string, { t: string; c: string; b: string }> = {
  pending: { t: 'Pending', c: '#92400e', b: '#fef3c7' },
  approved: { t: 'Approved', c: '#166534', b: '#dcfce7' },
  denied: { t: 'Denied', c: '#991b1b', b: '#fee2e2' },
  cancelled: { t: 'Cancelled', c: '#374151', b: '#e5e7eb' },
  expired: { t: 'Expired', c: '#374151', b: '#e5e7eb' },
}
const prioColor: Record<string, string> = { urgent: '#dc2626', high: '#d97706', normal: 'var(--text3)' }

export default function ApprovalsPage() {
  const [rows, setRows] = useState<Approval[]>([])
  const [types, setTypes] = useState<Record<string, string>>({})
  const [tab, setTab] = useState<Tab>('pending')
  const [typeFilter, setTypeFilter] = useState('')
  const [msg, setMsg] = useState('')
  // The load failure is its OWN state, not folded into `msg`. `msg` is decision feedback ("Approved.")
  // and is replaced by the next action; whether this list is trustworthy is a different fact with a
  // different lifetime, and the panel below has to be able to ask about it.
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string>('')

  const load = useCallback(() => {
    setLoading(true); setLoadError('')   // a retry starts clean, so the spinner (not a stale error) shows
    const status = tab === 'pending' ? 'pending' : 'all'
    const p = new URLSearchParams({ status }); if (typeFilter) p.set('type', typeFilter)
    api(`/api/v1/approvals?${p}`).then((r: { approvals?: Approval[]; types?: Record<string, string> }) => {
      let list: Approval[] = r.approvals || []
      if (tab === 'decided') list = list.filter(x => x.status !== 'pending')
      setRows(list); setTypes(r.types || {})
    }).catch((e: unknown) => {
      // DISCARD what we were showing. Rows from the previous tab/filter (or the initial []) are not an
      // answer to the question just asked, and leaving them up presents stale or absent data as current.
      setRows([]); setLoadError(loadErrorText(e))
    }).finally(() => setLoading(false))
  }, [tab, typeFilter])
  useEffect(() => { load() }, [load])

  async function decide(x: Approval, decision: 'approve' | 'deny') {
    const note = decision === 'deny' ? (prompt('Reason for denying (optional):') ?? '') : ''
    setBusy(x.id)
    try {
      await api(`/api/v1/approvals/${x.id}/decision`, { method: 'POST', body: JSON.stringify({ decision, note }) })
      setMsg(decision === 'approve' ? '✅ Approved.' : 'Denied.'); load()
    } catch (e: unknown) { setMsg('❌ ' + errText(e)) }
    setBusy('')
  }

  const typeLabel = (t: string) => types[t] || t
  const panel = panelState({ loading, error: loadError, count: rows.length, tab })

  return (
    <div style={{ maxWidth: 1040 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>✅ Approvals</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Everything across the app that needs your approval, in one place. Approving performs the action.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
            style={{ padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)' }}>
            <option value="">All types</option>
            {Object.entries(types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button className={tab === 'pending' ? 'btn btn-primary' : 'btn btn-secondary'} onClick={() => setTab('pending')}>Pending</button>
          <button className={tab === 'decided' ? 'btn btn-primary' : 'btn btn-secondary'} onClick={() => setTab('decided')}>Decided</button>
        </div>
      </div>
      {msg && <div style={{ fontSize: 13, margin: '8px 0' }}>{msg}</div>}

      {/* A load that FAILED gets a banner of its own, above the panel and styled as an error — not the
          same neutral line decision feedback uses. It carries the server's words verbatim, so the
          reference id in a masked 500 ("Reference: 881ae411") survives into a screenshot. */}
      {panel.kind === 'error' && (
        <div role="alert" style={{ margin: '8px 0 0', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 13, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ flex: 1, minWidth: 220 }}>⚠️ {loadError}</span>
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={load}>Retry</button>
        </div>
      )}

      <div className="card" style={{ padding: 18, marginTop: 12 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>
          {tab === 'pending' ? 'Waiting on you' : 'Recent decisions'} {panel.showCount && rows.length > 0 && <span style={{ color: 'var(--text3)' }}>· {rows.length}</span>}
        </div>
        {panel.kind === 'error' ? (
          <div style={{ fontSize: 13, color: '#dc2626' }}>{panel.message}</div>
        ) : panel.kind === 'loading' ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
        ) : panel.kind === 'empty' ? (
          <div style={{ fontSize: 13, color: 'var(--text3)' }}>{panel.message}</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Request', 'Type', 'Store', 'Requested by', 'Date', tab === 'pending' ? '' : 'Status'].map((h, i) =>
                <th key={i} style={{ textAlign: 'left', padding: '7px 9px', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map(x => { const b = badge[x.status] || badge.pending; return (
                <tr key={x.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 9px' }}>
                    <div style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
                      {x.priority && x.priority !== 'normal' && <span style={{ color: prioColor[x.priority], fontSize: 11 }}>●</span>}
                      {x.title}
                    </div>
                    {x.summary && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{x.summary}</div>}
                  </td>
                  <td style={{ padding: '8px 9px', color: 'var(--text2)' }}>{typeLabel(x.type)}</td>
                  <td style={{ padding: '8px 9px', color: 'var(--text2)' }}>{x.store_code || '—'}</td>
                  <td style={{ padding: '8px 9px', color: 'var(--text2)' }}>{x.requested_by_name || '—'}</td>
                  <td style={{ padding: '8px 9px', color: 'var(--text2)' }}>{(x.created_at || '').slice(0, 10)}</td>
                  {tab === 'pending' ? (
                    <td style={{ padding: '8px 9px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <button className="btn btn-primary" disabled={busy === x.id} style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => decide(x, 'approve')}>✓ Approve</button>{' '}
                      <button className="btn btn-secondary" disabled={busy === x.id} style={{ fontSize: 12, padding: '3px 10px', color: '#dc2626' }} onClick={() => decide(x, 'deny')}>✕ Deny</button>
                    </td>
                  ) : (
                    <td style={{ padding: '8px 9px' }}>
                      <span style={{ padding: '1px 7px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: b.c, background: b.b }}>{b.t}</span>
                      {x.decided_by && <span style={{ color: 'var(--text3)', marginLeft: 6, fontSize: 11 }}>{x.decided_by}</span>}
                    </td>
                  )}
                </tr>
              ) })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
