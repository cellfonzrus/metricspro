'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'

// Device Forecasting & Vendor Payables (module 095). Reads the config-driven ledger built by
// POST /api/v1/payables/rebuild. Forecasting is phones-only; payables + due are per-IMEI.

type Tab = 'forecast' | 'payables' | 'owed'
const sel = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' } as const
const STATUS_COLORS: Record<string, string> = {
  discrepancy: '#dc2626', due: '#d97706', offset: '#16a34a', open: '#6b7280', unconfigured: '#6b7280',
}

export default function PayablesPage() {
  const [tab, setTab] = useState<Tab>('payables')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  // forecast
  const [days, setDays] = useState(30)
  const [fRows, setFRows] = useState<any[]>([])
  // payables
  const [status, setStatus] = useState('')
  const [store, setStore] = useState('')
  const [pRows, setPRows] = useState<any[]>([])
  const [drill, setDrill] = useState<any | null>(null)
  // owed-by-date
  const [oRows, setORows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [settings, setSettings] = useState<any>({ priority_ack_enabled: false, priority_window_pct: 25 })
  const [showSettings, setShowSettings] = useState(false)

  useEffect(() => { api(`/api/v1/payables/settings?org_id=${ORG_ID}`).then(setSettings).catch(() => {}) }, [])
  useEffect(() => { if (tab === 'forecast') loadForecast() }, [tab, days, store])
  useEffect(() => { if (tab === 'payables') loadPayables() }, [tab, status, store])
  useEffect(() => { if (tab === 'owed') loadOwed() }, [tab, store])

  async function rebuild() {
    setBusy(true); setMsg('Rebuilding ledger… (may take a minute for a full Boost rebuild)')
    try {
      const r = await api(`/api/v1/payables/rebuild?org_id=${ORG_ID}`, { method: 'POST' })
      const sc = r.status_counts || {}
      setMsg(`Rebuilt ${r.written} devices across ${r.carriers} carrier(s): ` +
        Object.entries(sc).map(([k, v]) => `${v} ${k}`).join(', ') +
        (r.flags_written != null ? ` · ${r.flags_written} discrepancy flags` : ''))
      if (tab === 'payables') loadPayables(); else if (tab === 'forecast') loadForecast(); else loadOwed()
    } catch (e: any) { setMsg('Rebuild error (it may still be completing server-side): ' + e.message) }
    setBusy(false)
  }

  async function loadForecast() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID, days: String(days) })
      if (store) qs.set('store', store)
      const d = await api(`/api/v1/payables/forecast?${qs}`); setFRows(d.rows || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function loadPayables() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (status) qs.set('status', status); if (store) qs.set('store', store)
      const d = await api(`/api/v1/payables/payables?${qs}`); setPRows(d.rows || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function loadOwed() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (store) qs.set('store', store)
      const d = await api(`/api/v1/payables/owed-by-date?${qs}`); setORows(d.rows || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function openDrill(imei: string) {
    try { setDrill(await api(`/api/v1/payables/offsets/${encodeURIComponent(imei)}?org_id=${ORG_ID}`)) }
    catch (e: any) { setMsg(e.message) }
  }
  async function saveSettings(patch: any) {
    const next = { ...settings, ...patch }; setSettings(next)
    try { await api(`/api/v1/payables/settings?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(patch) }) }
    catch (e: any) { setMsg(e.message) }
  }

  const th = { textAlign: 'left' as const, padding: '8px 10px', borderBottom: '2px solid var(--border)', fontSize: 12, color: 'var(--muted)' }
  const td = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Device Forecasting &amp; Vendor Payables</h1>
          <p style={{ color: 'var(--muted)', fontSize: 13, margin: '4px 0 0' }}>
            Per-IMEI owed vs due, reconciled against sold + rebate received — and phones-only stock forecasting.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowSettings(s => !s)}
            style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer' }}>⚙ Settings</button>
          <button onClick={rebuild} disabled={busy}
            style={{ padding: '8px 16px', borderRadius: 8, border: 'none', background: 'var(--accent, #2563eb)', color: '#fff', fontWeight: 600, cursor: busy ? 'wait' : 'pointer' }}>
            {busy ? 'Rebuilding…' : '↻ Rebuild ledger'}
          </button>
        </div>
      </div>
      {showSettings && (
        <div style={{ marginTop: 12, padding: 14, borderRadius: 10, background: 'var(--surface)', border: '1px solid var(--border)', display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={!!settings.priority_ack_enabled} onChange={e => saveSettings({ priority_ack_enabled: e.target.checked })} />
            Require clock-in acknowledgment of priority phones
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            Priority window (final %):
            <input type="number" min={1} max={100} value={settings.priority_window_pct}
              onChange={e => setSettings((s: any) => ({ ...s, priority_window_pct: +e.target.value }))}
              onBlur={e => saveSettings({ priority_window_pct: +e.target.value })} style={{ ...sel, width: 70 }} />
          </label>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>The % applies on the next Rebuild.</span>
        </div>
      )}
      {msg && <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 13 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 8, margin: '18px 0 14px', flexWrap: 'wrap' }}>
        {(['payables', 'forecast', 'owed'] as Tab[]).map(t => (
          <button key={t} onClick={() => setTab(t)}
            style={{ padding: '7px 14px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, cursor: 'pointer',
              background: tab === t ? 'var(--accent, #2563eb)' : 'var(--surface)', color: tab === t ? '#fff' : 'var(--text)', fontWeight: tab === t ? 600 : 400 }}>
            {t === 'payables' ? 'Payables (per IMEI)' : t === 'forecast' ? 'Forecast (phones)' : 'Daily Owed'}
          </button>
        ))}
        <input placeholder="Store filter…" value={store} onChange={e => setStore(e.target.value)} style={{ ...sel, minWidth: 160 }} />
        {tab === 'forecast' && (
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            Days: <input type="number" min={1} max={365} value={days} onChange={e => setDays(+e.target.value || 30)} style={{ ...sel, width: 70 }} />
          </label>
        )}
        {tab === 'payables' && (
          <select value={status} onChange={e => setStatus(e.target.value)} style={sel}>
            <option value="">All statuses</option>
            <option value="discrepancy">Discrepancy (sold, no rebate)</option>
            <option value="due">Due (unsold, past due)</option>
            <option value="offset">Offset (rebate received)</option>
            <option value="open">Open</option>
          </select>
        )}
      </div>

      {loading && <div style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</div>}

      {tab === 'forecast' && !loading && (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Store</th><th style={th}>Model</th><th style={th}>Sold (window)</th><th style={th}>Velocity/day</th><th style={th}>Projected</th><th style={th}>On hand</th><th style={th}>Recommend order</th></tr></thead>
          <tbody>{fRows.map((r, i) => (
            <tr key={i}><td style={td}>{r.store}</td><td style={td}>{r.device_model}</td><td style={td}>{r.units_sold_window}</td><td style={td}>{r.avg_daily_velocity}</td><td style={td}>{r.projected_demand}</td><td style={td}>{r.on_hand}</td>
              <td style={{ ...td, fontWeight: r.recommend_order > 0 ? 700 : 400, color: r.recommend_order > 0 ? '#d97706' : 'inherit' }}>{r.recommend_order}</td></tr>
          ))}</tbody>
        </table>
      )}

      {tab === 'payables' && !loading && (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>IMEI</th><th style={th}>Store</th><th style={th}>Model</th><th style={th}>Owed</th><th style={th}>Rebate</th><th style={th}>Net owed</th><th style={th}>Due</th><th style={th}>Status</th></tr></thead>
          <tbody>{pRows.map((r, i) => (
            <tr key={i} onClick={() => openDrill(r.imei)} style={{ cursor: 'pointer' }}>
              <td style={td}>{r.imei}</td><td style={td}>{r.store}</td><td style={td}>{r.device_model}</td>
              <td style={td}>{r.owed == null ? '—' : fmt(r.owed)}</td>
              <td style={td}>{r.rebate_amount ? fmt(r.rebate_amount) : '—'}{r.rebate_mismatch && <span title="ePay cross-check mismatch" style={{ color: '#dc2626' }}> ⚠︎</span>}</td>
              <td style={{ ...td, fontWeight: 600 }}>{r.net_owed == null ? '—' : fmt(r.net_owed)}</td>
              <td style={td}>{r.due_date || '—'}</td>
              <td style={td}><span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 11, color: '#fff', background: STATUS_COLORS[r.status] || '#6b7280' }}>{r.status}</span>{r.priority && <span title="Priority sell (final 25% of window)"> 🔥</span>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}

      {tab === 'owed' && !loading && (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Due date</th><th style={th}>Devices</th><th style={th}>Owed</th></tr></thead>
          <tbody>{oRows.map((r, i) => (
            <tr key={i}><td style={td}>{r.due_date}</td><td style={td}>{r.count}</td><td style={{ ...td, fontWeight: 600 }}>{fmt(r.owed)}</td></tr>
          ))}</tbody>
        </table>
      )}

      {drill && (
        <div onClick={() => setDrill(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', borderRadius: 12, padding: 24, maxWidth: 560, width: '90%', maxHeight: '80vh', overflow: 'auto' }}>
            <h3 style={{ margin: '0 0 4px' }}>What offsets what — {drill.device_model}</h3>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14 }}>IMEI {drill.imei} · {drill.store} · {drill.status}{drill.sold ? ` · sold ${drill.sold_date || ''}` : ' · unsold'}</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <tbody>
                <tr><td style={td}>Owed to vendor</td><td style={{ ...td, textAlign: 'right' }}>{drill.owed == null ? `— (${drill.owed_source})` : fmt(drill.owed)}</td></tr>
                <tr><td style={td}>Primary rebate ({drill.primary_rebate?.source})</td><td style={{ ...td, textAlign: 'right', color: '#16a34a' }}>{drill.primary_rebate?.amount ? '− ' + fmt(drill.primary_rebate.amount) : '—'}</td></tr>
                {(drill.epay_crosscheck?.lines || []).map((l: any, i: number) => (
                  <tr key={i}><td style={td}>ePay: {l.type} ({l.date})</td><td style={{ ...td, textAlign: 'right', color: 'var(--muted)' }}>{fmt(l.amount)}</td></tr>
                ))}
                {drill.epay_crosscheck?.mismatch && <tr><td style={td} colSpan={2}><span style={{ color: '#dc2626' }}>⚠︎ ePay cross-check ({fmt(drill.epay_crosscheck.amount || 0)}) disagrees with the primary rebate</span></td></tr>}
                <tr><td style={{ ...td, fontWeight: 700 }}>Net owed</td><td style={{ ...td, textAlign: 'right', fontWeight: 700 }}>{drill.net_owed == null ? '—' : fmt(drill.net_owed)}</td></tr>
              </tbody>
            </table>
            <button onClick={() => setDrill(null)} style={{ marginTop: 16, padding: '7px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer' }}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}
