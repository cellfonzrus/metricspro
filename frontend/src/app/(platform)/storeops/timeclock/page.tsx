'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Time Clock admin (Part B / B1): review punches (clock-in/out, hours, selfie, GPS, face-match) and
// add manual-hours adjustments. The employee-facing clock-in lives in the mobile /portal.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const fmtTime = (t: string | null) => { if (!t) return '—'; try { return new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) } catch { return t } }

export default function TimeClockAdminPage() {
  const today = new Date()
  const weekAgo = new Date(); weekAgo.setDate(today.getDate() - 6)
  const [start, setStart] = useState(iso(weekAgo))
  const [end, setEnd] = useState(iso(today))
  const [empFilter, setEmpFilter] = useState('')
  const [rows, setRows] = useState<any[]>([])
  const [employees, setEmployees] = useState<any[]>([])
  const [manual, setManual] = useState<any[]>([])
  const [mh, setMh] = useState({ employee_id: '', work_date: iso(today), hours: '', reason: '' })
  const [msg, setMsg] = useState('')

  useEffect(() => { api('/api/v1/storeops/employees').then((e: any) => setEmployees(e || [])).catch(() => {}) }, [])

  const load = useCallback(() => {
    const qs = `start=${start}&end=${end}${empFilter ? `&employee_id=${encodeURIComponent(empFilter)}` : ''}`
    api(`/api/v1/storeops/timeclock/list?${qs}`).then((r: any) => setRows(r || [])).catch((e: any) => setMsg('Load failed: ' + (e?.message || e)))
    api(`/api/v1/storeops/manual-hours?${qs}`).then((r: any) => setManual(r || [])).catch(() => {})
  }, [start, end, empFilter])
  useEffect(() => { load() }, [load])

  async function addManual() {
    if (!mh.employee_id || !mh.hours || !mh.reason.trim()) { setMsg('Employee, hours and reason are required.'); return }
    try { await api('/api/v1/storeops/manual-hours', { method: 'POST', body: JSON.stringify({ ...mh, hours: Number(mh.hours) }) }); setMh({ employee_id: '', work_date: iso(today), hours: '', reason: '' }); setMsg('✅ Adjustment added.'); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function delManual(id: string) {
    try { await api(`/api/v1/storeops/manual-hours/${id}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const empName = (id: string) => employees.find(e => e.employee_id === id)?.name || id
  const totalHours = rows.reduce((s, r) => s + (Number(r.hours) || 0), 0)
  const openCount = rows.filter(r => !r.clock_out).length

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⏱️ Time Clock</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Review clock-in/out punches with selfie, GPS and face-match audit. Employees clock in from the mobile portal.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12 }}>From <input type="date" style={sel} value={start} onChange={e => setStart(e.target.value)} /></label>
        <label style={{ fontSize: 12 }}>To <input type="date" style={sel} value={end} onChange={e => setEnd(e.target.value)} /></label>
        <select style={sel} value={empFilter} onChange={e => setEmpFilter(e.target.value)}>
          <option value="">All employees</option>
          {employees.map(e => <option key={e.employee_id} value={e.employee_id}>{e.name}</option>)}
        </select>
        <div style={{ flex: 1 }} />
        <span className="badge" style={{ fontSize: 12 }}>{rows.length} punches</span>
        <span className="badge" style={{ fontSize: 12 }}>{totalHours.toFixed(2)} hrs</span>
        {openCount > 0 && <span className="badge" style={{ fontSize: 12, background: '#16794a', color: '#fff' }}>{openCount} clocked in</span>}
        {msg && <span style={{ fontSize: 13, width: '100%' }}>{msg}</span>}
      </div>

      <div className="card" style={{ padding: 0, overflowX: 'auto', marginBottom: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Employee', 'Date', 'In', 'Out', 'Hours', 'Store', 'Face', 'GPS', 'Selfie'].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td style={cell}>{r.employee_name || empName(r.employee_id)}</td>
                <td style={cell}>{r.work_date}</td>
                <td style={cell}>{fmtTime(r.clock_in)}</td>
                <td style={cell}>{r.clock_out ? fmtTime(r.clock_out) : <span style={{ color: '#16794a', fontWeight: 600 }}>open</span>}</td>
                <td style={cell}>{r.hours != null ? Number(r.hours).toFixed(2) : '—'}</td>
                <td style={{ ...cell, fontSize: 12 }}>{r.store_code || '—'}</td>
                <td style={cell}>{r.face_match_pct != null ? `${r.face_match_pct}%` : '—'}</td>
                <td style={cell}>{r.gps_lat != null ? <a href={`https://maps.google.com/?q=${r.gps_lat},${r.gps_lng}`} target="_blank" rel="noreferrer">map</a> : '—'}</td>
                <td style={cell}>{r.selfie_url ? <a href={r.selfie_url} target="_blank" rel="noreferrer"><img src={r.selfie_url} alt="selfie" style={{ width: 34, height: 34, borderRadius: 4, objectFit: 'cover' }} /></a> : '—'}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={9} style={{ textAlign: 'center', padding: 36, color: 'var(--text3)' }}>No punches in range. (Run migration 045 if this errors.)</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ padding: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>✍️ Manual hours adjustments</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
          <select style={sel} value={mh.employee_id} onChange={e => setMh({ ...mh, employee_id: e.target.value })}>
            <option value="">Employee…</option>
            {employees.map(e => <option key={e.employee_id} value={e.employee_id}>{e.name}</option>)}
          </select>
          <input type="date" style={sel} value={mh.work_date} onChange={e => setMh({ ...mh, work_date: e.target.value })} />
          <input style={{ ...sel, width: 90 }} placeholder="hours (±)" value={mh.hours} onChange={e => setMh({ ...mh, hours: e.target.value })} />
          <input style={{ ...sel, width: 240 }} placeholder="reason (required)" value={mh.reason} onChange={e => setMh({ ...mh, reason: e.target.value })} />
          <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addManual}>+ Add</button>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {manual.map(m => (
              <tr key={m.id}>
                <td style={cell}>{empName(m.employee_id)}</td>
                <td style={cell}>{m.work_date}</td>
                <td style={{ ...cell, fontWeight: 600, color: Number(m.hours) < 0 ? '#dc2626' : 'inherit' }}>{Number(m.hours) > 0 ? '+' : ''}{Number(m.hours).toFixed(2)}h</td>
                <td style={cell}>{m.reason}</td>
                <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 12, color: '#dc2626' }} onClick={() => delManual(m.id)}>✕</button></td>
              </tr>
            ))}
            {manual.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', padding: 18, color: 'var(--text3)', fontSize: 13 }}>No adjustments in range.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
