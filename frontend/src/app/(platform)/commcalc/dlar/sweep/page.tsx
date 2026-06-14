'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID } from '@/lib/client'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

type Cfg = {
  configured: boolean; has_credentials: boolean; portal_user: string | null
  enabled: boolean; frequency: string; day_of_week: number; day_of_month: number
  hour: number; timezone: string
  next_run_at: string | null; last_run_at: string | null
  last_status: string | null; last_detail: string | null
}

const DOW = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const TZS = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Phoenix']
const sel = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const fmtTs = (s: string | null) => (s ? new Date(s).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }) : '—')

export default function DlarSweepAdmin() {
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [pass, setPass] = useState('')          // write-only; blank = keep existing
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    try { setCfg(await api(`/api/v1/commcalc/dlar/sweep/config?org_id=${ORG_ID}`)) }
    catch (e) { console.error(e) }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  function set<K extends keyof Cfg>(k: K, v: Cfg[K]) { setCfg(c => c ? { ...c, [k]: v } : c) }

  async function save() {
    if (!cfg) return
    setSaving(true); setMsg('')
    try {
      const body: any = {
        portal_user: cfg.portal_user, enabled: cfg.enabled, frequency: cfg.frequency,
        day_of_week: cfg.day_of_week, day_of_month: cfg.day_of_month, hour: cfg.hour,
        timezone: cfg.timezone,
      }
      if (pass.trim()) body.portal_pass = pass.trim()
      const res = await fetch(`${API}/api/v1/commcalc/dlar/sweep/config?org_id=${ORG_ID}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Save failed')
      setCfg(d); setPass('')
      setMsg('✅ Saved')
    } catch (e: any) { setMsg(`❌ ${e.message}`) }
    setSaving(false)
  }

  async function runNow() {
    setRunning(true); setMsg('')
    try {
      const res = await fetch(`${API}/api/v1/commcalc/dlar/sweep/run-now?org_id=${ORG_ID}`, { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Run failed')
      setMsg('⏳ Import started — refreshing status…')
      for (let i = 0; i < 10; i++) {
        await new Promise(r => setTimeout(r, 4000))
        await load()
        const c = await api(`/api/v1/commcalc/dlar/sweep/config?org_id=${ORG_ID}`)
        if (c.last_status && c.last_status !== 'running') { setMsg(c.last_status === 'ok' ? '✅ Import complete' : '❌ Import error'); break }
      }
    } catch (e: any) { setMsg(`❌ ${e.message}`) }
    setRunning(false)
  }

  if (loading) return <div style={{ padding: 60, textAlign: 'center', color: 'var(--text3)' }}>Loading…</div>
  if (!cfg) return <div style={{ padding: 60, textAlign: 'center', color: 'var(--text3)' }}>Could not load config. Did you run 012_dlar_sweep.sql?</div>

  const statusColor = cfg.last_status === 'ok' ? '#059669' : cfg.last_status === 'error' ? '#dc2626' : cfg.last_status === 'running' ? '#d97706' : 'var(--text3)'
  const row = { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' as const }
  const lab = { width: 160, fontSize: 13, fontWeight: 600, color: 'var(--text2)' }

  return (
    <div style={{ maxWidth: 760 }}>
      <a href="/commcalc/upload" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Upload</a>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 4px' }}>⚙️ DLAR Auto-Import</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 20px' }}>
        Pulls the store (DLAR) and rep (Advocate) reports from <strong>boostelevatego.com</strong> on a
        schedule and loads them automatically — <strong>this replaces the manual monthly DLAR upload</strong>.
        Update the portal login here so a password change never needs a code change.
      </p>

      {/* Status */}
      <div className="card" style={{ marginBottom: 18, padding: '16px 20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>Last import</div>
            <div style={{ fontSize: 14, marginTop: 3 }}>
              <span style={{ color: statusColor, fontWeight: 700 }}>{cfg.last_status || 'never run'}</span>
              {cfg.last_run_at && <span style={{ color: 'var(--text3)' }}> · {fmtTs(cfg.last_run_at)}</span>}
            </div>
            {cfg.last_detail && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 3 }}>{cfg.last_detail}</div>}
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>Next scheduled: <strong>{cfg.enabled ? fmtTs(cfg.next_run_at) : 'disabled'}</strong></div>
          </div>
          <button className="btn btn-primary" onClick={runNow} disabled={running || !cfg.has_credentials}
            title={cfg.has_credentials ? 'Import the DLAR right now' : 'Set credentials first'}>
            {running ? '⏳ Importing…' : '▶ Import DLAR now'}
          </button>
        </div>
      </div>

      {/* Credentials */}
      <div className="card" style={{ marginBottom: 18, padding: '18px 20px' }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 14 }}>Portal credentials</div>
        <div style={row}>
          <span style={lab}>Boost email</span>
          <input style={{ ...sel, width: 320 }} value={cfg.portal_user || ''} onChange={e => set('portal_user', e.target.value)} placeholder="user@example.com" />
        </div>
        <div style={row}>
          <span style={lab}>Boost password</span>
          <input type="password" style={{ ...sel, width: 320 }} value={pass} onChange={e => setPass(e.target.value)}
            placeholder={cfg.has_credentials ? '•••••••• (set — leave blank to keep)' : 'enter password'} autoComplete="new-password" />
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)' }}>
          🔒 The password is stored server-side only and never shown again. Leave blank to keep the current one.
        </div>
      </div>

      {/* Schedule */}
      <div className="card" style={{ marginBottom: 18, padding: '18px 20px' }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 14 }}>Schedule</div>
        <div style={row}>
          <span style={lab}>Enabled</span>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <input type="checkbox" checked={cfg.enabled} onChange={e => set('enabled', e.target.checked)} />
            Run automatically on the schedule below
          </label>
        </div>
        <div style={row}>
          <span style={lab}>Frequency</span>
          <select style={sel} value={cfg.frequency} onChange={e => set('frequency', e.target.value)}>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </select>
          {cfg.frequency === 'weekly' && (
            <select style={sel} value={cfg.day_of_week} onChange={e => set('day_of_week', +e.target.value)}>
              {DOW.map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
          )}
          {cfg.frequency === 'monthly' && (
            <select style={sel} value={cfg.day_of_month} onChange={e => set('day_of_month', +e.target.value)}>
              {Array.from({ length: 28 }, (_, i) => i + 1).map(d => <option key={d} value={d}>Day {d}</option>)}
            </select>
          )}
          <span style={{ fontSize: 13, color: 'var(--text2)' }}>at</span>
          <select style={sel} value={cfg.hour} onChange={e => set('hour', +e.target.value)}>
            {Array.from({ length: 24 }, (_, h) => h).map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
          </select>
          <select style={sel} value={cfg.timezone} onChange={e => set('timezone', e.target.value)}>
            {(TZS.includes(cfg.timezone) ? TZS : [cfg.timezone, ...TZS]).map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)' }}>
          The DLAR refreshes daily on the portal, so <strong>Daily</strong> is recommended. Each run replaces the current month's DLAR + Advocate data.
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : '💾 Save settings'}</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>
    </div>
  )
}
