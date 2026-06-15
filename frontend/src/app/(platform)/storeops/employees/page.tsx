'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

interface Employee {
  id: number; employee_id: string | null; name: string; home_store: string | null
  role: string | null; pay_rate: number; is_active: boolean
  epay_login: string | null; epay_salesperson: string | null; email: string | null; phone: string | null
}

const EMP_ROLES = ['Sales Rep', 'Assistant Manager', 'Store Manager', 'Market Manager', 'Admin', 'Other']
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState<number | null>(null)

  async function load() {
    setLoading(true)
    try {
      const e = await api('/api/v1/storeops/employees?include_inactive=true')
      setEmployees((e || []).map((x: any) => ({ ...x })))
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoading(false)
  }
  useEffect(() => { load() }, [])

  const setEmp = (id: number, patch: Partial<Employee>) =>
    setEmployees(es => es.map(e => e.id === id ? { ...e, ...patch } : e))

  async function saveEmp(e: Employee) {
    setSaving(e.id); setMsg('')
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({
        name: e.name, employee_id: e.employee_id, home_store: e.home_store, role: e.role,
        pay_rate: Number(e.pay_rate) || 0, is_active: !!e.is_active, email: e.email, phone: e.phone,
        epay_login: e.epay_login, epay_salesperson: e.epay_salesperson,
      }) })
      setMsg(`Saved ${e.name}`)
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
    setSaving(null)
  }

  const filtered = employees.filter(e => {
    if (!showInactive && !e.is_active) return false
    if (search && ![e.name, e.home_store, e.role, e.email, e.epay_salesperson]
      .some(v => (v || '').toLowerCase().includes(search.toLowerCase()))) return false
    return true
  })

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Employees</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {filtered.length} employees · edit inline, then 💾 to save each row.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
          <input className="input" placeholder="Search name, store, role, ePay…" value={search}
            onChange={e => setSearch(e.target.value)} style={{ ...sel, width: 240 }} />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
            Show inactive
          </label>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Name', 'Emp ID', 'Home store', 'Role', 'Pay $/hr', 'Email', 'Phone', 'ePay login', 'ePay salesperson', 'Active', ''].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {filtered.map(e => (
                <tr key={e.id} style={{ opacity: e.is_active ? 1 : 0.5 }}>
                  <td style={cell}><input style={{ ...sel, width: 150 }} value={e.name || ''} onChange={ev => setEmp(e.id, { name: ev.target.value })} /></td>
                  <td style={cell}><input style={{ ...sel, width: 90 }} value={e.employee_id || ''} placeholder="—" onChange={ev => setEmp(e.id, { employee_id: ev.target.value })} /></td>
                  <td style={cell}><input style={{ ...sel, width: 100 }} value={e.home_store || ''} onChange={ev => setEmp(e.id, { home_store: ev.target.value })} /></td>
                  <td style={cell}><select style={sel} value={e.role || 'Other'} onChange={ev => setEmp(e.id, { role: ev.target.value })}>{EMP_ROLES.map(r => <option key={r}>{r}</option>)}</select></td>
                  <td style={cell}><input style={{ ...sel, width: 80 }} type="number" value={e.pay_rate ?? ''} onChange={ev => setEmp(e.id, { pay_rate: ev.target.value as any })} /></td>
                  <td style={cell}><input style={{ ...sel, width: 180 }} type="email" value={e.email || ''} placeholder="add email…" onChange={ev => setEmp(e.id, { email: ev.target.value })} /></td>
                  <td style={cell}><input style={{ ...sel, width: 120 }} value={e.phone || ''} onChange={ev => setEmp(e.id, { phone: ev.target.value })} /></td>
                  <td style={cell}><input style={{ ...sel, width: 120 }} value={e.epay_login || ''} onChange={ev => setEmp(e.id, { epay_login: ev.target.value })} /></td>
                  <td style={cell}><input style={{ ...sel, width: 150 }} value={e.epay_salesperson || ''} onChange={ev => setEmp(e.id, { epay_salesperson: ev.target.value })} /></td>
                  <td style={cell}><input type="checkbox" checked={!!e.is_active} onChange={ev => setEmp(e.id, { is_active: ev.target.checked })} /></td>
                  <td style={cell}><button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 10px' }} disabled={saving === e.id} onClick={() => saveEmp(e)}>{saving === e.id ? '…' : '💾'}</button></td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={11} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No employees found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
