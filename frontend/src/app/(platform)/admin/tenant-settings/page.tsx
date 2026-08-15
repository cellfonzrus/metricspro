'use client'
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'
import CarrierPicker from '@/components/CarrierPicker'

// Per-tenant pay period / work-week (mig 085). The tenant admin defines the work-week start, the
// pay cycle (weekly/biweekly), and how the payday is placed — with a LIVE worked example so they
// can tune the params to match their real payday (e.g. Luxelink: Thu→Wed week, pay the next Friday).
const DOW = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] // 0=Mon..6=Sun
// The tenant's DEFAULT business time zone (captured here at onboarding). Every business-local
// calculation — the time clock's auto-clock-out, POS day/month boundaries, schedules, payroll — uses
// it, unless a specific store overrides it in the store settings. IANA names; a store in a different
// zone (e.g. a Chicago store under an Eastern-default company) is set per-store, not here.
const TZ_OPTIONS: { v: string; label: string }[] = [
  { v: 'America/New_York', label: 'Eastern — New York (ET)' },
  { v: 'America/Chicago', label: 'Central — Chicago (CT)' },
  { v: 'America/Denver', label: 'Mountain — Denver (MT)' },
  { v: 'America/Phoenix', label: 'Mountain, no DST — Phoenix (MST)' },
  { v: 'America/Los_Angeles', label: 'Pacific — Los Angeles (PT)' },
  { v: 'America/Anchorage', label: 'Alaska — Anchorage (AKT)' },
  { v: 'Pacific/Honolulu', label: 'Hawaii — Honolulu (HST)' },
]
const fmt = (iso: string) => { try { const d = new Date(iso + 'T00:00:00'); return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) } catch { return iso } }

export default function TenantSettingsPage() {
  const [s, setS] = useState<any>(null)
  const [preview, setPreview] = useState<any[]>([])
  const [name, setName] = useState('')
  const [canEdit, setCanEdit] = useState(false)
  const [complete, setComplete] = useState(false)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/core/tenant-settings').then((r: any) => {
      setS(r.settings); setPreview(r.preview || []); setName(r.name || '')
      setCanEdit(!!r.can_edit); setComplete(!!r.setup_complete)
    }).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  async function save() {
    if (!s) return
    setBusy(true); setMsg('')
    try {
      const r: any = await api('/api/v1/core/tenant-settings', { method: 'PUT', body: JSON.stringify(s) })
      setS(r.settings); setPreview(r.preview || []); setComplete(true)
      setMsg('✅ Saved — schedules and payroll now use this work-week.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  if (!s) return <div style={{ padding: 8, color: 'var(--text3)' }}>{msg || 'Loading…'}</div>
  const set = (patch: any) => setS({ ...s, ...patch })
  const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }
  const inp: React.CSSProperties = { width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)' }

  return (
    <div style={{ maxWidth: 760 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>⚙️ Pay period & work-week</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 16px' }}>
        {name ? <b>{name}</b> : 'This company'}&apos;s work-week and pay cycle. The schedule week, the manager
        hours budget, and payroll all derive from this — define it once. {complete
          ? <span style={{ color: '#166534', fontWeight: 600 }}>✓ Setup complete.</span>
          : <span style={{ color: '#92400e', fontWeight: 600 }}>Not yet set — using the default Monday week.</span>}
      </p>

      {!canEdit && <div style={{ padding: 10, marginBottom: 12, background: 'var(--surface2)', borderRadius: 8, fontSize: 13, color: 'var(--text2)' }}>Only a company admin can change these settings — you can view the current cycle below.</div>}

      <div className="card" style={{ padding: 18, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>🕐 Business time zone</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
          The default zone for this company. The time clock (including auto clock-out at shift end),
          POS day totals, schedules and payroll all read times in this zone. A store located in a
          different zone can override it in that store&apos;s settings.
        </p>
        <label style={{ display: 'block', maxWidth: 340 }}><span style={lbl}>Default time zone</span>
          <select style={inp} disabled={!canEdit} value={s.timezone || ''} onChange={e => set({ timezone: e.target.value || null })}>
            <option value="">House default — Eastern (ET)</option>
            {TZ_OPTIONS.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
          </select></label>
      </div>

      <div className="card" style={{ padding: 18 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 14 }}>
          <label><span style={lbl}>Work week starts on</span>
            <select style={inp} disabled={!canEdit} value={s.work_week_start_dow} onChange={e => set({ work_week_start_dow: Number(e.target.value) })}>
              {DOW.map((d, i) => <option key={i} value={i}>{d}</option>)}
            </select></label>
          <label><span style={lbl}>Pay cycle</span>
            <select style={inp} disabled={!canEdit} value={s.pay_period_type} onChange={e => set({ pay_period_type: e.target.value })}>
              <option value="weekly">Weekly</option>
              <option value="biweekly">Every 2 weeks</option>
            </select></label>
          <label><span style={lbl}>Payday falls on</span>
            <select style={inp} disabled={!canEdit} value={s.payday_dow} onChange={e => set({ payday_dow: Number(e.target.value) })}>
              {DOW.map((d, i) => <option key={i} value={i}>{d}</option>)}
            </select></label>
          <label><span style={lbl}>Which payday after the period ends</span>
            <select style={inp} disabled={!canEdit} value={s.payday_weeks_after} onChange={e => set({ payday_weeks_after: Number(e.target.value) })}>
              <option value={1}>The first one (on/after period end)</option>
              <option value={2}>The next one (+1 week)</option>
              <option value={3}>+2 weeks</option>
            </select></label>
          {s.pay_period_type === 'biweekly' && (
            <label><span style={lbl}>A period start date (anchors the 2-week grid)</span>
              <input type="date" style={inp} disabled={!canEdit} value={(s.biweekly_anchor || '').slice(0, 10)} onChange={e => set({ biweekly_anchor: e.target.value })} /></label>
          )}
        </div>

        {canEdit && (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 16 }}>
            <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : '💾 Save & preview'}</button>
            {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
          </div>
        )}
      </div>

      <CarrierPicker canEdit={canEdit} />

      <div className="card" style={{ padding: 18, marginTop: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>Upcoming pay periods</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>Check these match your real cycle. Adjust the settings above until the payday lines up (Save to recompute).</p>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Period start', 'Period end', 'Payday'].map(h => <th key={h} style={{ textAlign: 'left', padding: '7px 10px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {preview.map((p, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '7px 10px' }}>{fmt(p.start)}</td>
                <td style={{ padding: '7px 10px' }}>{fmt(p.end)}</td>
                <td style={{ padding: '7px 10px', fontWeight: 600, color: 'var(--accent)' }}>{fmt(p.payday)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
