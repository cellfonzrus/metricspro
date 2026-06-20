'use client'
import { useState, useEffect } from 'react'
import { api, parseLocalDate } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

interface Request {
  id: number; employee_id: string; employee_name?: string
  start_date: string; end_date: string; type: string
  status: string; notes: string; approved_by: string
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'badge-amber', approved: 'badge-green', denied: 'badge-red',
}

export default function TimeOffPage() {
  const { user } = useAuth()
  const [requests, setRequests] = useState<Request[]>([])
  const [employees, setEmployees] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  // approveNow: when a manager enters a request, approve it on submit (vs an employee
  // request that a manager approves later).
  const [form, setForm] = useState({ employee_id: '', start_date: '', end_date: '', type: 'PTO', notes: '', approveNow: true })

  useEffect(() => {
    Promise.all([
      api('/api/v1/storeops/time-off'),
      api('/api/v1/storeops/employees'),
    ]).then(([reqs, emps]) => {
      setRequests(reqs || [])
      setEmployees(emps || [])
    }).catch(console.error).finally(() => setLoading(false))
  }, [])

  function empName(id: string) {
    return employees.find(e => e.id?.toString() === id || e.employee_id === id)?.name || id
  }

  async function submit() {
    setErr('')
    if (!form.employee_id) { setErr('Pick an employee.'); return }
    if (!form.start_date || !form.end_date) { setErr('Start and end dates are required.'); return }
    if (form.end_date < form.start_date) { setErr('End date cannot be before the start date.'); return }
    const approver = user?.full_name || user?.email || 'manager'
    const payload: any = {
      employee_id: form.employee_id, start_date: form.start_date, end_date: form.end_date,
      type: form.type, notes: form.notes,
      status: form.approveNow ? 'approved' : 'pending',
    }
    if (form.approveNow) payload.approved_by = approver
    setSaving(true)
    try {
      const req = await api('/api/v1/storeops/time-off', { method: 'POST', body: JSON.stringify(payload) })
      setRequests(r => [req, ...r])
      setShowForm(false)
      setForm({ employee_id: '', start_date: '', end_date: '', type: 'PTO', notes: '', approveNow: true })
    } catch (e: any) {
      setErr(e?.message || 'Could not save the request.')
    } finally { setSaving(false) }
  }

  async function updateStatus(id: number, status: string) {
    const approver = user?.full_name || user?.email || 'manager'
    const body: any = { status }
    if (status === 'approved') { body.approved_by = approver; body.approved_at = new Date().toISOString() }
    await api(`/api/v1/storeops/time-off/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
    setRequests(r => r.map(req => req.id === id ? { ...req, status, approved_by: status === 'approved' ? approver : req.approved_by } : req))
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Time Off Requests</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {requests.filter(r => r.status === 'pending').length} pending · {requests.length} total · approved time off blocks scheduling
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => { setErr(''); setShowForm(true) }}>+ New Request</button>
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: 20, borderTop: '3px solid var(--accent)' }}>
          <div style={{ fontWeight: 700, marginBottom: 16 }}>New Time Off Request</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Employee</label>
              <select className="select" style={{ width: '100%' }} value={form.employee_id}
                onChange={e => setForm(f => ({ ...f, employee_id: e.target.value }))}>
                <option value="">Select employee...</option>
                {employees.map(e => <option key={e.id} value={e.id?.toString()}>{e.name}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Type</label>
              <select className="select" style={{ width: '100%' }} value={form.type}
                onChange={e => setForm(f => ({ ...f, type: e.target.value }))}>
                <option value="PTO">PTO</option>
                <option value="Sick">Sick</option>
                <option value="Unpaid">Unpaid</option>
                <option value="Personal">Personal</option>
              </select>
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Start Date</label>
              <input className="input" type="date" value={form.start_date}
                onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>End Date</label>
              <input className="input" type="date" value={form.end_date}
                onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Notes</label>
              <input className="input" value={form.notes} placeholder="Reason (optional)"
                onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
            </div>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 14, fontSize: 13, fontWeight: 500 }}>
            <input type="checkbox" checked={form.approveNow} onChange={e => setForm(f => ({ ...f, approveNow: e.target.checked }))} />
            Approve immediately (manager entry) — uncheck to submit as a pending request
          </label>
          {err && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 10 }}>{err}</div>}
          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button className="btn btn-primary" disabled={saving} onClick={submit}>{saving ? 'Saving…' : (form.approveNow ? 'Submit & Approve' : 'Submit Request')}</button>
            <button className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Employee</th>
                <th>Type</th>
                <th>Dates</th>
                <th>Days</th>
                <th>Notes</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((r, i) => {
                const start = parseLocalDate(r.start_date)
                const end = parseLocalDate(r.end_date)
                const days = Math.round((end.getTime() - start.getTime()) / 86400000) + 1
                return (
                  <tr key={r.id ?? i}>
                    <td style={{ fontWeight: 500 }}>{r.employee_name || empName(r.employee_id)}</td>
                    <td><span className="badge badge-blue" style={{ fontSize: 11 }}>{r.type}</span></td>
                    <td style={{ fontSize: 12 }}>
                      {start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                      {r.start_date !== r.end_date && ` → ${end.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`}
                    </td>
                    <td style={{ textAlign: 'center' }}>{days}d</td>
                    <td style={{ fontSize: 12, color: 'var(--text3)' }}>{r.notes || '—'}</td>
                    <td>
                      <span className={`badge ${STATUS_COLORS[r.status] || 'badge-slate'}`} style={{ textTransform: 'capitalize' }}>
                        {r.status}
                      </span>
                    </td>
                    <td>
                      {r.status === 'pending' ? (
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '4px 10px' }}
                            onClick={() => updateStatus(r.id, 'approved')}>Approve</button>
                          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '4px 10px', color: 'var(--red)' }}
                            onClick={() => updateStatus(r.id, 'denied')}>Deny</button>
                        </div>
                      ) : r.status === 'approved' ? (
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '4px 10px', color: 'var(--red)' }}
                          onClick={() => updateStatus(r.id, 'denied')}>Revoke</button>
                      ) : null}
                    </td>
                  </tr>
                )
              })}
              {requests.length === 0 && (
                <tr><td colSpan={7} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No time off requests
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
