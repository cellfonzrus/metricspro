'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Unified connector registry (SaaS framework Phase 2): every vendor portal + the reports it provides
// + live sweep status, with a generic run-now. The single source of truth for the data pipeline.
const TWOFA: Record<string, { bg: string; fg: string }> = {
  ok: { bg: '#e6f7ec', fg: '#16794a' }, needs_setup: { bg: '#fef3e2', fg: '#b45309' }, blocked: { bg: '#fde8e8', fg: '#b42318' },
}
const dt = (s: string) => s ? new Date(s).toLocaleString() : '—'

export default function ConnectorsPage() {
  const [conns, setConns] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api('/api/v1/commcalc/connectors').then(setConns).catch(console.error).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  async function setConn(c: any, patch: any) {
    try { await api(`/api/v1/commcalc/connectors/${c.id}`, { method: 'PATCH', body: JSON.stringify(patch) }); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function setReport(r: any, patch: any) {
    try { await api(`/api/v1/commcalc/report-definitions/${r.id}`, { method: 'PATCH', body: JSON.stringify(patch) }); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function runNow(c: any) {
    setBusy(c.id); setMsg('')
    try { const r = await api(`/api/v1/commcalc/connectors/${c.id}/run-now`, { method: 'POST' }); setMsg(`⏳ ${c.vendor_name} sweep started.`); setTimeout(load, 4000) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔌 Connectors</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Every vendor portal and the reports it feeds, with live sweep status. The data-pipeline registry (framework Phase 2).
        </p>
      </div>
      {msg && <div style={{ fontSize: 13, marginBottom: 10 }}>{msg}</div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : conns.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No connectors — run migration 039.</div>
      ) : conns.map((c: any) => {
        const tf = TWOFA[c.twofa_status] || TWOFA.ok
        const st = c.status || {}
        const stColor = st.last_status === 'ok' ? '#16794a' : st.last_status === 'partial' ? '#b45309' : st.last_status === 'error' ? '#b42318' : 'var(--text3)'
        return (
          <div key={c.id} className="card" style={{ padding: 16, marginBottom: 14, opacity: c.enabled ? 1 : 0.6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{c.vendor_name} <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>{c.label}</span></div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>
                  <a href={c.portal_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>{c.portal_url}</a> · {c.auth_type}
                  <span style={{ marginLeft: 8, background: tf.bg, color: tf.fg, padding: '1px 7px', borderRadius: 99, fontSize: 11, fontWeight: 600 }}>2FA: {c.twofa_status}</span>
                  <span style={{ marginLeft: 6, fontSize: 11, color: c.automatable ? '#16794a' : '#b45309', fontWeight: 600 }}>{c.automatable ? 'auto' : 'manual-only'}</span>
                </div>
                {(st.last_run_at || st.last_status) && (
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
                    Last run {dt(st.last_run_at)} · <b style={{ color: stColor }}>{st.last_status || '—'}</b>
                    {st.next_run_at ? <> · next {dt(st.next_run_at)}</> : null}
                    {st.last_detail ? <div style={{ color: 'var(--text3)', fontSize: 11, marginTop: 2 }}>{String(st.last_detail).slice(0, 200)}</div> : null}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
                  <input type="checkbox" checked={!!c.enabled} onChange={e => setConn(c, { enabled: e.target.checked })} /> enabled
                </label>
                {c.automatable && <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={busy === c.id} onClick={() => runNow(c)}>{busy === c.id ? '…' : '▶ Run now'}</button>}
              </div>
            </div>

            {(c.reports || []).length > 0 && (
              <div className="table-wrapper" style={{ marginTop: 12 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Report', 'Portal source', 'Target table', 'Period', 'Mode', ''].map(h =>
                      <th key={h} style={{ textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {c.reports.map((r: any) => (
                      <tr key={r.id}>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13, fontWeight: 600 }}>{r.label || r.report_key}</td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text3)' }}>{r.source_name || '—'}{r.report_id ? ` (#${r.report_id})` : ''}</td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 12, fontFamily: 'monospace', color: 'var(--text3)' }}>{r.target_table || '—'}</td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text3)' }}>{r.period_mode}</td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)' }}>
                          <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
                            <input type="checkbox" checked={!!r.auto} onChange={e => setReport(r, { auto: e.target.checked })} />
                            <span style={{ color: r.auto ? '#16794a' : '#b45309', fontWeight: 600 }}>{r.auto ? 'auto' : 'manual'}</span>
                          </label>
                        </td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)' }}></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
