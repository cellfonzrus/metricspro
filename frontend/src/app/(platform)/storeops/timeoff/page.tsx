'use client'
import { useState, useEffect } from 'react'
import { api, parseLocalDate } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

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
  // Edit an EXISTING request's dates/type/notes (employee stays fixed — RULE THREE) so a manager
  // can reschedule/correct a request instead of denying + re-entering it from scratch.
  const [editReq, setEditReq] = useState<Request | null>(null)
  const [editForm, setEditForm] = useState({ start_date: '', end_date: '', type: 'PTO', notes: '' })
  const [savingEdit, setSavingEdit] = useState(false)
  const [editErr, setEditErr] = useState('')
  // Org policy: does scheduling over an employee's approved time off WARN (default, every
  // tenant) or hard BLOCK (opt-in)? Read-only for everyone; the select below is manager-gated
  // server-side (PUT /storeops/timeoff-conflict-mode → 403 for a non-manager).
  const [conflictMode, setConflictMode] = useState<'warn' | 'block'>('warn')
  const [savingMode, setSavingMode] = useState(false)

  useEffect(() => {
    Promise.all([
      api('/api/v1/storeops/time-off'),
      apiCached('/api/v1/storeops/employees', LOOKUP),
    ]).then(([reqs, emps]) => {
      setRequests(reqs || [])
      setEmployees(emps || [])
    }).catch(console.error).finally(() => setLoading(false))
    api('/api/v1/storeops/timeoff-conflict-mode').then((r: any) => {
      if (r?.mode === 'block') setConflictMode('block')
    }).catch(() => {})
  }, [])

  async function changeConflictMode(mode: 'warn' | 'block') {
    const prev = conflictMode
    setConflictMode(mode)
    setSavingMode(true)
    try {
      await api('/api/v1/storeops/timeoff-conflict-mode', { method: 'PUT', body: JSON.stringify({ mode }) })
    } catch (e: any) {
      setConflictMode(prev)
      alert(e?.message || "Couldn't save — manager/admin only.")
    } finally { setSavingMode(false) }
  }

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

  function openEdit(r: Request) {
    setEditErr('')
    setEditReq(r)
    setEditForm({ start_date: r.start_date, end_date: r.end_date, type: r.type || 'PTO', notes: r.notes || '' })
  }

  async function saveEdit() {
    if (!editReq) return
    setEditErr('')
    if (!editForm.start_date || !editForm.end_date) { setEditErr('Start and end dates are required.'); return }
    if (editForm.end_date < editForm.start_date) { setEditErr('End date cannot be before the start date.'); return }
    setSavingEdit(true)
    try {
      const updated = await api(`/api/v1/storeops/time-off/${editReq.id}`, { method: 'PATCH', body: JSON.stringify(editForm) })
      setRequests(r => r.map(req => req.id === editReq.id ? { ...req, ...editForm, ...(updated || {}) } : req))
      setEditReq(null)
    } catch (e: any) {
      setEditErr(e?.message || 'Could not save the changes.')
    } finally { setSavingEdit(false) }
  }

  // RULE FOUR (§3c): export the visible rows — no PII (dates/type/notes/status only).
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: r => r.employee_name || empName(r.employee_id) },
    { header: 'Type', field: 'type', get: r => r.type },
    { header: 'Start', field: 'start_date', role: 'date', type: 'date', get: r => r.start_date },
    { header: 'End', field: 'end_date', type: 'date', get: r => r.end_date },
    { header: 'Days', field: 'days', type: 'number', get: r => {
      const s = parseLocalDate(r.start_date), e = parseLocalDate(r.end_date)
      return Math.round((e.getTime() - s.getTime()) / 86400000) + 1
    } },
    { header: 'Notes', field: 'notes', get: r => r.notes || '' },
    { header: 'Status', field: 'status', get: r => r.status },
    { header: 'Approved By', field: 'approved_by', get: r => r.approved_by || '' },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Time Off Requests</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {requests.filter(r => r.status === 'pending').length} pending · {requests.length} total ·{' '}
            {conflictMode === 'block'
              ? 'approved time off BLOCKS scheduling for this tenant'
              : 'scheduling over approved time off is allowed, with a warning'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text2)' }}>
            Scheduling over time off
            <select className="select" style={{ fontSize: 12, padding: '4px 8px' }} value={conflictMode} disabled={savingMode}
              onChange={e => changeConflictMode(e.target.value as 'warn' | 'block')}>
              <option value="warn">Warn, allow (default)</option>
              <option value="block">Block</option>
            </select>
          </label>
          <ReportExportBar title="Time Off Requests" columns={cols} rows={requests} />
          <button className="btn btn-primary" onClick={() => { setErr(''); setShowForm(true) }}>+ New Request</button>
        </div>
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
                {/* 2026-07-27 Gate-1 REDO N1 root-cause fix (owner-approved money-fix package): send
                    the BUSINESS employee_id — the same identity storeops.shifts/timelog/manual_hours
                    all key on — not the employee's numeric primary key. The old `e.id` here is the
                    exact same class of bug fixed in schedule/page.tsx:294 (numeric id stored where a
                    business id was expected), except here it silently poisoned time_off_requests
                    instead of shifts; migration 415 backfills the existing rows this produced. */}
                {employees.map(e => <option key={e.id} value={e.employee_id || e.id?.toString()}>{e.name}</option>)}
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

      {editReq && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}>
          <div className="card" style={{ width: 400 }}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 4 }}>Edit Time Off Request</div>
            <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16 }}>
              {editReq.employee_name || empName(editReq.employee_id)}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Type</label>
                <select className="select" style={{ width: '100%' }} value={editForm.type}
                  onChange={e => setEditForm(f => ({ ...f, type: e.target.value }))}>
                  <option value="PTO">PTO</option>
                  <option value="Sick">Sick</option>
                  <option value="Unpaid">Unpaid</option>
                  <option value="Personal">Personal</option>
                </select>
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Status</label>
                <div style={{ padding: '6px 0' }}>
                  <span className={`badge ${STATUS_COLORS[editReq.status] || 'badge-slate'}`} style={{ textTransform: 'capitalize' }}>{editReq.status}</span>
                </div>
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Start Date</label>
                <input className="input" type="date" value={editForm.start_date}
                  onChange={e => setEditForm(f => ({ ...f, start_date: e.target.value }))} />
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>End Date</label>
                <input className="input" type="date" value={editForm.end_date}
                  onChange={e => setEditForm(f => ({ ...f, end_date: e.target.value }))} />
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>Notes</label>
                <input className="input" style={{ width: '100%' }} value={editForm.notes} placeholder="Reason (optional)"
                  onChange={e => setEditForm(f => ({ ...f, notes: e.target.value }))} />
              </div>
            </div>
            {editErr && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 10 }}>{editErr}</div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button className="btn btn-primary" disabled={savingEdit} onClick={saveEdit}>{savingEdit ? 'Saving…' : 'Save Changes'}</button>
              <button className="btn btn-secondary" onClick={() => setEditReq(null)}>Cancel</button>
            </div>
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
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {r.status === 'pending' ? (
                          <>
                            <button className="btn btn-primary" style={{ fontSize: 11, padding: '4px 10px' }}
                              onClick={() => updateStatus(r.id, 'approved')}>Approve</button>
                            <button className="btn btn-secondary" style={{ fontSize: 11, padding: '4px 10px', color: 'var(--red)' }}
                              onClick={() => updateStatus(r.id, 'denied')}>Deny</button>
                          </>
                        ) : r.status === 'approved' ? (
                          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '4px 10px', color: 'var(--red)' }}
                            onClick={() => updateStatus(r.id, 'denied')}>Revoke</button>
                        ) : null}
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '4px 10px' }}
                          onClick={() => openEdit(r)}>Edit</button>
                      </div>
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
