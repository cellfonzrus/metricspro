'use client'
import { useEffect, useState, useCallback } from 'react'
import { api, localToday } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// Shift-extension request → District Manager approval workflow (mig 086). A manager files a request
// to keep an employee past their scheduled end; the DM (or an admin) approves it IN-APP — the tick
// is the approval, recorded with who + when. Once approved, the forced-clockout job honors the
// extended end. Filing ahead of time is the ONLY way to avoid the auto clock-out at scheduled end.
interface Ext {
  id: string; employee_id: string; employee_name?: string; store_code?: string; shift_date?: string
  original_end?: string; requested_end?: string; reason?: string; status: string
  requested_by?: string; requested_at?: string; dm_email?: string
  decided_by?: string; decided_at?: string; decision_note?: string
}
const badge: Record<string, { t: string; c: string; b: string }> = {
  pending: { t: 'Pending', c: '#92400e', b: '#fef3c7' },
  approved: { t: 'Approved', c: '#166534', b: '#dcfce7' },
  denied: { t: 'Denied', c: '#991b1b', b: '#fee2e2' },
  expired: { t: 'Expired', c: 'var(--text3)', b: 'var(--surface2)' },
}

export default function ShiftExtensionsPage() {
  const [exts, setExts] = useState<Ext[]>([])
  const [emps, setEmps] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<any>({ employee_id: '', store_code: '', shift_date: localToday(), requested_end: '20:00', reason: '' })

  const load = useCallback(() => {
    api('/api/v1/storeops/shift-extensions').then((r: any) => setExts(r.extensions || [])).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => {
    load()
    api('/api/v1/storeops/employees').then((r: any) => setEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
    api('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : (r?.stores || []))).catch(() => {})
  }, [load])

  async function submit() {
    if (!form.employee_id || !form.shift_date || !form.requested_end) { setMsg('Pick an employee, date and new end time.'); return }
    setBusy(true); setMsg('')
    try {
      const emp = emps.find(e => String(e.employee_id || e.id) === String(form.employee_id))
      const r: any = await api('/api/v1/storeops/shift-extensions', { method: 'POST', body: JSON.stringify({ ...form, employee_name: emp?.name }) })
      setMsg(r.dm?.emailed ? `✅ Requested — the District Manager (${r.dm.name || r.dm.email}) was notified.` : `✅ Requested.${r.note ? ' ' + r.note : ''}`)
      setForm({ ...form, reason: '' }); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }
  async function decide(x: Ext, decision: 'approve' | 'deny') {
    const note = decision === 'deny' ? (prompt('Reason for denying (optional):') ?? '') : ''
    try {
      await api(`/api/v1/storeops/shift-extensions/${x.id}/decision`, { method: 'POST', body: JSON.stringify({ decision, note }) })
      setMsg(decision === 'approve' ? '✅ Approved — recorded.' : 'Denied.'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const pending = exts.filter(x => x.status === 'pending')
  const decided = exts.filter(x => x.status !== 'pending')
  const decidedVisible = decided.slice(0, 30)
  const inp: React.CSSProperties = { width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)' }
  const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }

  // RULE FOUR (§3c): export exactly what each table shows — no PII (name/store/date/status only).
  const pendingCols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: x => x.employee_name || x.employee_id },
    { header: 'Store', field: 'store_code', role: 'store', get: x => x.store_code || '' },
    { header: 'Date', field: 'shift_date', role: 'date', type: 'date', get: x => x.shift_date },
    { header: 'Extend To', field: 'requested_end', get: x => x.requested_end },
    { header: 'Reason', field: 'reason', get: x => x.reason || '' },
    { header: 'Requested By', field: 'requested_by', get: x => x.requested_by || '' },
  ]
  const decidedCols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: x => x.employee_name || x.employee_id },
    { header: 'Date', field: 'shift_date', role: 'date', type: 'date', get: x => x.shift_date },
    { header: 'Extend To', field: 'requested_end', get: x => x.requested_end },
    { header: 'Status', field: 'status', get: x => (badge[x.status] || badge.expired).t },
    { header: 'Decided By', field: 'decided_by', get: x => x.decided_by || '' },
  ]

  return (
    <div style={{ maxWidth: 960 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>⏱️ Shift extensions</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 16px' }}>
        Employees are automatically clocked out at their scheduled shift end. To keep someone longer, a manager
        files a request here <b>ahead of time</b> and the District Manager approves it — the approval is recorded.
      </p>
      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <div className="card" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Request an extension</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 12 }}>
          <label><span style={lbl}>Employee</span>
            <select style={inp} value={form.employee_id} onChange={e => setForm({ ...form, employee_id: e.target.value })}>
              <option value="">—</option>
              {emps.map(e => <option key={e.id} value={String(e.employee_id || e.id)}>{e.name}</option>)}
            </select></label>
          <label><span style={lbl}>Store</span>
            <select style={inp} value={form.store_code} onChange={e => setForm({ ...form, store_code: e.target.value })}>
              <option value="">—</option>
              {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code}{s.address ? ` — ${s.address}` : ''}</option>)}
            </select></label>
          <label><span style={lbl}>Date</span>
            <input type="date" style={inp} value={form.shift_date} onChange={e => setForm({ ...form, shift_date: e.target.value })} /></label>
          <label><span style={lbl}>Extend to (end time)</span>
            <input type="time" style={inp} value={form.requested_end} onChange={e => setForm({ ...form, requested_end: e.target.value })} /></label>
          <label style={{ gridColumn: '1 / -1' }}><span style={lbl}>Reason</span>
            <input style={inp} placeholder="Covering a call-out / high traffic / …" value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} /></label>
        </div>
        <div style={{ marginTop: 12 }}><button className="btn btn-primary" disabled={busy} onClick={submit}>{busy ? 'Submitting…' : 'Submit request'}</button></div>
      </div>

      <div className="card" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Pending approvals {pending.length > 0 && <span style={{ color: '#92400e' }}>· {pending.length}</span>}</div>
          {pending.length > 0 && <ReportExportBar title="Shift Extensions — Pending" columns={pendingCols} rows={pending} />}
        </div>
        {pending.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text3)' }}>Nothing waiting.</div> : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Employee', 'Store', 'Date', 'Extend to', 'Reason', 'Requested by', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '7px 9px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {pending.map(x => (
                <tr key={x.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 9px', fontWeight: 600 }}>{x.employee_name || x.employee_id}</td>
                  <td style={{ padding: '7px 9px' }}>{x.store_code || '—'}</td>
                  <td style={{ padding: '7px 9px' }}>{x.shift_date}</td>
                  <td style={{ padding: '7px 9px', fontWeight: 600, color: 'var(--accent)' }}>{x.requested_end}</td>
                  <td style={{ padding: '7px 9px', color: 'var(--text3)' }}>{x.reason || '—'}</td>
                  <td style={{ padding: '7px 9px', color: 'var(--text3)' }}>{x.requested_by || '—'}</td>
                  <td style={{ padding: '7px 9px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => decide(x, 'approve')}>✓ Approve</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 10px', color: '#dc2626' }} onClick={() => decide(x, 'deny')}>✕ Deny</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {decided.length > 0 && (
        <div className="card" style={{ padding: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>Recent decisions</div>
            <ReportExportBar title="Shift Extensions — Recent Decisions" columns={decidedCols} rows={decidedVisible} />
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Employee', 'Date', 'Extend to', 'Status', 'Decided by'].map(h => <th key={h} style={{ textAlign: 'left', padding: '7px 9px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {decidedVisible.map(x => { const b = badge[x.status] || badge.expired; return (
                <tr key={x.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 9px', fontWeight: 600 }}>{x.employee_name || x.employee_id}</td>
                  <td style={{ padding: '7px 9px' }}>{x.shift_date}</td>
                  <td style={{ padding: '7px 9px' }}>{x.requested_end}</td>
                  <td style={{ padding: '7px 9px' }}><span style={{ padding: '1px 7px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: b.c, background: b.b }}>{b.t}</span></td>
                  <td style={{ padding: '7px 9px', color: 'var(--text3)' }}>{x.decided_by || '—'}</td>
                </tr>
              ) })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
