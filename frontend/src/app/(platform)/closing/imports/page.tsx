'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, apiUpload } from '@/lib/client'

// Auto-import config for the daily-closing Google "Envelopes Data (Responses)" sheet, read via a
// Google service account. The SA JSON key lives in the Railway env GOOGLE_SERVICE_ACCOUNT_JSON;
// here we set the sheet id + tab + schedule, run-now, and see last status. Manual upload too.
const inp: React.CSSProperties = { padding: '8px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)', width: '100%' }
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function ClosingImportsPage() {
  const [cfg, setCfg] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [upMsg, setUpMsg] = useState('')

  const load = useCallback(() => { api('/api/v1/closing/sweep/config').then(setCfg).catch(() => setCfg({})) }, [])
  useEffect(() => { load() }, [load])

  const set = (patch: any) => setCfg((c: any) => ({ ...c, ...patch }))

  async function save() {
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/closing/sweep/config', { method: 'PUT', body: JSON.stringify({
        sheet_id: cfg.sheet_id, tab: cfg.tab, enabled: cfg.enabled, frequency: cfg.frequency,
        day_of_week: Number(cfg.day_of_week), day_of_month: Number(cfg.day_of_month), hour: Number(cfg.hour),
        timezone: cfg.timezone,
      }) })
      setCfg(r); setMsg('✅ Saved.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function runNow() {
    setBusy(true); setMsg('')
    try { await api('/api/v1/closing/sweep/run-now', { method: 'POST' }); setMsg('⏳ Sweep started — refresh in a moment for status.'); setTimeout(load, 4000) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function upload(file: File) {
    setUpMsg('⏳ Uploading…')
    const fd = new FormData(); fd.append('file', file)
    try { const r = await apiUpload('/api/v1/closing/upload', fd); setUpMsg(`✅ Loaded ${r.rows_saved} rows across ${r.dates?.length || 0} day(s).`) }
    catch (e: any) { setUpMsg('❌ ' + (e?.message || e)) }
  }

  if (!cfg) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
  const saOk = cfg.service_account_configured

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔄 Closing Auto-Import</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Auto-pull the closing Google sheet on a schedule via a Google service account.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      {/* Setup steps */}
      <div className="card" style={{ padding: 14, marginBottom: 16, fontSize: 13, color: 'var(--text2)' }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Setup (one-time)</div>
        <ol style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li>Create a Google Cloud <b>service account</b>, enable the <b>Sheets API</b>, download its JSON key.</li>
          <li>On Railway set <code>GOOGLE_SERVICE_ACCOUNT_JSON</code> = that JSON, then redeploy.</li>
          <li><b>Share</b> the responses sheet (Viewer) with the service-account email below.</li>
          <li>Paste the sheet id, pick a schedule, and enable.</li>
        </ol>
        <div style={{ marginTop: 10, padding: '8px 10px', borderRadius: 7, background: saOk ? '#e6f7ec' : '#fef3e2' }}>
          {saOk
            ? <>✅ Service account configured: <b>{cfg.service_account_email || '(key set)'}</b> — share the sheet with this address.</>
            : <>⚠️ <code>GOOGLE_SERVICE_ACCOUNT_JSON</code> is not set on the server yet. Add it on Railway + redeploy to enable auto-import.</>}
        </div>
      </div>

      {/* Config */}
      <div className="card" style={{ padding: 18 }}>
        <Field label="Google Sheet ID"><input style={inp} value={cfg.sheet_id || ''} onChange={e => set({ sheet_id: e.target.value })} placeholder="from the URL: docs.google.com/spreadsheets/d/<THIS>/edit" /></Field>
        <Field label="Tab name (blank = first/responses tab)"><input style={inp} value={cfg.tab || ''} onChange={e => set({ tab: e.target.value })} placeholder="Form Responses 1" /></Field>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 4 }}>
          <div style={{ flex: '1 1 150px' }}>
            <Field label="Frequency">
              <select style={inp} value={cfg.frequency || 'daily'} onChange={e => set({ frequency: e.target.value })}>
                <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </select>
            </Field>
          </div>
          {cfg.frequency === 'weekly' && (
            <div style={{ flex: '1 1 120px' }}><Field label="Day of week">
              <select style={inp} value={cfg.day_of_week ?? 1} onChange={e => set({ day_of_week: e.target.value })}>
                {DOW.map((d, i) => <option key={i} value={i}>{d}</option>)}
              </select></Field></div>
          )}
          {cfg.frequency === 'monthly' && (
            <div style={{ flex: '1 1 120px' }}><Field label="Day of month"><input style={inp} type="number" min={1} max={31} value={cfg.day_of_month ?? 1} onChange={e => set({ day_of_month: e.target.value })} /></Field></div>
          )}
          <div style={{ flex: '1 1 110px' }}><Field label="Hour (0-23)"><input style={inp} type="number" min={0} max={23} value={cfg.hour ?? 22} onChange={e => set({ hour: e.target.value })} /></Field></div>
          <div style={{ flex: '1 1 170px' }}><Field label="Timezone"><input style={inp} value={cfg.timezone || 'America/New_York'} onChange={e => set({ timezone: e.target.value })} /></Field></div>
        </div>

        <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, fontSize: 14 }}>
          <input type="checkbox" checked={!!cfg.enabled} onChange={e => set({ enabled: e.target.checked })} />
          Enabled (run on schedule)
        </label>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 16, flexWrap: 'wrap' }}>
          <button className="btn btn-primary" style={{ fontSize: 14 }} disabled={busy} onClick={save}>💾 Save</button>
          <button className="btn btn-secondary" style={{ fontSize: 14 }} disabled={busy || !cfg.sheet_id} onClick={runNow}>▶ Run now</button>
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>

        {(cfg.last_status || cfg.next_run_at) && (
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)', fontSize: 13, color: 'var(--text2)' }}>
            {cfg.next_run_at && <div>Next run: <b>{new Date(cfg.next_run_at).toLocaleString()}</b></div>}
            {cfg.last_run_at && <div>Last run: {new Date(cfg.last_run_at).toLocaleString()}</div>}
            {cfg.last_status && <div>Last status: <b style={{ color: cfg.last_status === 'ok' ? 'var(--green, #16794a)' : 'var(--amber, #b45309)' }}>{cfg.last_status}</b> — {cfg.last_detail}</div>}
          </div>
        )}
      </div>

      {/* Manual upload fallback */}
      <div className="card" style={{ padding: 14, marginTop: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <label className="btn btn-secondary" style={{ fontSize: 13, cursor: 'pointer' }}>
          📤 Manual upload (.xlsx/.csv)
          <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) upload(f) }} />
        </label>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>One-off import of the sheet export — same parser as the auto-sweep.</span>
        {upMsg && <span style={{ fontSize: 13 }}>{upMsg}</span>}
      </div>
    </div>
  )
}

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <label style={{ display: 'block', marginBottom: 10 }}>
    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>{label}</div>
    {children}
  </label>
)
