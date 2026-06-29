'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Unified connector registry (SaaS framework Phase 2): every vendor portal + the reports it provides
// + live sweep status, with a generic run-now. The single source of truth for the data pipeline.
const TWOFA: Record<string, { bg: string; fg: string }> = {
  ok: { bg: '#e6f7ec', fg: '#16794a' }, needs_setup: { bg: '#fef3e2', fg: '#b45309' }, blocked: { bg: '#fde8e8', fg: '#b42318' },
}
const dt = (s: string) => s ? new Date(s).toLocaleString() : '—'
const fin: React.CSSProperties = { padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function ConnectorsPage() {
  const [conns, setConns] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [nc, setNc] = useState<any>({ vendor_name: '', label: '', sweep_kind: 'manual', portal_url: '' })
  const [nr, setNr] = useState<Record<string, any>>({})
  const [sched, setSched] = useState<Record<string, any>>({})

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
  async function saveSchedule(c: any, sc: any) {
    setBusy(c.id + 'sched'); setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/connectors/${c.id}/schedule`, { method: 'PATCH', body: JSON.stringify({
        frequency: sc.frequency, day_of_week: Number(sc.day_of_week), day_of_month: Number(sc.day_of_month),
        hour: Number(sc.hour), enabled: !!sc.enabled,
      }) })
      setMsg(`✅ ${c.vendor_name} schedule saved — next run ${dt(r.next_run_at)}.`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy('') }
  }
  async function addConnector() {
    if (!nc.vendor_name?.trim()) { setMsg('Vendor name required.'); return }
    try { await api('/api/v1/commcalc/connectors', { method: 'POST', body: JSON.stringify(nc) }); setNc({ vendor_name: '', label: '', sweep_kind: 'manual', portal_url: '' }); setShowAdd(false); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function addReport(cid: string) {
    const r = nr[cid] || {}
    if (!r.report_key?.trim()) { setMsg('report_key required.'); return }
    try { await api('/api/v1/commcalc/report-definitions', { method: 'POST', body: JSON.stringify({ ...r, connector_id: cid }) }); setNr(p => ({ ...p, [cid]: {} })); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔌 Connectors</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Every vendor portal and the reports it feeds, with live sweep status and per-connector schedule. The data-pipeline registry (framework Phase 2).
        </p>
        <p style={{ color: 'var(--text3)', fontSize: 12, margin: '4px 0 0' }}>
          One cron drives them all: point pg_cron at <code>POST /api/v1/commcalc/connectors/run-due</code> (header <code>X-Notify-Secret</code>) and it fans out to every connector that's due.
        </p>
      </div>
      {msg && <div style={{ fontSize: 13, marginBottom: 10 }}>{msg}</div>}

      <div style={{ marginBottom: 14 }}>
        <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setShowAdd(s => !s)}>{showAdd ? '✕ Cancel' : '＋ Add connector'}</button>
        {showAdd && (
          <div className="card" style={{ padding: 14, marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input style={fin} placeholder="Vendor name *" value={nc.vendor_name} onChange={e => setNc({ ...nc, vendor_name: e.target.value })} />
            <input style={fin} placeholder="Label" value={nc.label} onChange={e => setNc({ ...nc, label: e.target.value })} />
            <input style={fin} placeholder="Portal URL" value={nc.portal_url} onChange={e => setNc({ ...nc, portal_url: e.target.value })} />
            <input style={fin} placeholder="Login username" value={nc.login_username || ''} onChange={e => setNc({ ...nc, login_username: e.target.value })} />
            <input style={fin} placeholder="Account ID (e.g. Total Wireless retailer #)" value={nc.account_id || ''} onChange={e => setNc({ ...nc, account_id: e.target.value })} />
            <select style={fin} value={nc.twofa_method || 'none'} onChange={e => setNc({ ...nc, twofa_method: e.target.value })} title="2-factor method">
              {['none', 'sms', 'totp', 'email', 'biometric'].map(k => <option key={k} value={k}>2FA: {k}</option>)}
            </select>
            <select style={fin} value={nc.sweep_kind} onChange={e => setNc({ ...nc, sweep_kind: e.target.value })} title="run-now dispatch kind">
              {['manual', 'vip', 'dlar', 'epay', 'b2b', 'google_closing'].map(k => <option key={k} value={k}>{k}</option>)}
            </select>
            <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addConnector}>Add</button>
          </div>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : conns.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No connectors — run migration 039.</div>
      ) : conns.map((c: any) => {
        const tf = TWOFA[c.twofa_status] || TWOFA.ok
        const st = c.status || {}
        const cr = c.creds || {}
        const stColor = st.last_status === 'ok' ? '#16794a' : st.last_status === 'partial' ? '#b45309' : st.last_status === 'error' ? '#b42318' : 'var(--text3)'
        const credReady = !!(cr.has_user && cr.has_pass)
        const ready = !!(c.automatable && credReady && st.enabled && st.next_run_at)
        const missing = !c.automatable ? 'manual-only' : !credReady ? 'credentials' : !st.enabled ? 'schedule off' : !st.next_run_at ? 'no schedule' : ''
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
                {c.config_table && (
                  <div style={{ fontSize: 11, marginTop: 4, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ background: credReady ? '#e6f7ec' : '#fef3e2', color: credReady ? '#16794a' : '#b45309', padding: '1px 7px', borderRadius: 99, fontWeight: 600 }}>
                      {credReady ? `🔑 credentials set${cr.user_hint ? ` (${cr.user_hint})` : ''}` : '⚠️ no credentials'}
                    </span>
                    <span style={{ background: ready ? '#e6f7ec' : 'var(--surface2)', color: ready ? '#16794a' : 'var(--text3)', padding: '1px 7px', borderRadius: 99, fontWeight: 600 }}>
                      {ready ? '✅ ready to auto-run' : `needs setup: ${missing}`}
                    </span>
                    {!credReady && <span style={{ color: 'var(--text3)' }}>set credentials on the vendor’s sweep page</span>}
                  </div>
                )}
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

            {c.config_table && (() => {
              const sc = sched[c.id] || { frequency: st.frequency || 'daily', day_of_week: st.day_of_week ?? 1, day_of_month: st.day_of_month ?? 1, hour: st.hour ?? 6, enabled: !!st.enabled }
              const upd = (patch: any) => setSched(p => ({ ...p, [c.id]: { ...sc, ...patch } }))
              return (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <input type="checkbox" checked={!!sc.enabled} onChange={e => upd({ enabled: e.target.checked })} /> auto-run on schedule
                  </label>
                  <select style={fin} value={sc.frequency} onChange={e => upd({ frequency: e.target.value })}>
                    {['daily', 'weekly', 'monthly'].map(f => <option key={f} value={f}>{f}</option>)}
                  </select>
                  {sc.frequency === 'weekly' && (
                    <select style={fin} value={sc.day_of_week} onChange={e => upd({ day_of_week: Number(e.target.value) })}>
                      {DOW.map((d, i) => <option key={i} value={i}>{d}</option>)}
                    </select>
                  )}
                  {sc.frequency === 'monthly' && (
                    <span>day <input style={{ ...fin, width: 52 }} value={sc.day_of_month} onChange={e => upd({ day_of_month: e.target.value })} /></span>
                  )}
                  <span>at <input style={{ ...fin, width: 52 }} value={sc.hour} onChange={e => upd({ hour: e.target.value })} />:00 {st.timezone || 'ET'}</span>
                  <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy === c.id + 'sched'} onClick={() => saveSchedule(c, sc)}>{busy === c.id + 'sched' ? '…' : 'Save schedule'}</button>
                  {st.next_run_at && <span style={{ color: 'var(--text3)' }}>next {dt(st.next_run_at)}</span>}
                </div>
              )
            })()}

            {(c.reports || []).length > 0 && (
              <div className="table-wrapper" style={{ marginTop: 12 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Report', 'Portal source', 'Target table', 'Last loaded', 'Mode', ''].map(h =>
                      <th key={h} style={{ textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {c.reports.map((r: any) => (
                      <tr key={r.id}>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13, fontWeight: 600 }}>{r.label || r.report_key}</td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text3)' }}>{r.source_name || '—'}{r.report_id ? ` (#${r.report_id})` : ''}</td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 12, fontFamily: 'monospace', color: 'var(--text3)' }}>{r.target_table || '—'}</td>
                        <td style={{ padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text3)' }}>{r.last_upload ? <span title={`${r.last_upload.rows_saved ?? ''} rows · ${r.last_upload.period ?? ''}`}>{dt(r.last_upload.uploaded_at)}</span> : '—'}</td>
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
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>+ report:</span>
              <input style={{ ...fin, width: 110 }} placeholder="key *" value={nr[c.id]?.report_key || ''} onChange={e => setNr(p => ({ ...p, [c.id]: { ...p[c.id], report_key: e.target.value } }))} />
              <input style={{ ...fin, width: 140 }} placeholder="label" value={nr[c.id]?.label || ''} onChange={e => setNr(p => ({ ...p, [c.id]: { ...p[c.id], label: e.target.value } }))} />
              <input style={{ ...fin, width: 130 }} placeholder="target_table" value={nr[c.id]?.target_table || ''} onChange={e => setNr(p => ({ ...p, [c.id]: { ...p[c.id], target_table: e.target.value } }))} />
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => addReport(c.id)}>Add</button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
