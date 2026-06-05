'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

interface Employee {
  id: number; employee_id: string; name: string; home_store: string
  role: string; pay_rate: number; is_active: boolean
  epay_login: string; epay_salesperson: string; email: string; phone: string
}

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<Employee | null>(null)
  const [showInactive, setShowInactive] = useState(false)

  useEffect(() => {
    api('/api/v1/storeops/employees')
      .then(setEmployees).catch(console.error).finally(() => setLoading(false))
  }, [])

  const filtered = employees.filter(e => {
    if (!showInactive && !e.is_active) return false
    if (search && !e.name?.toLowerCase().includes(search.toLowerCase()) &&
        !e.home_store?.toLowerCase().includes(search.toLowerCase()) &&
        !e.role?.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Employees</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {filtered.length} employees
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="input" placeholder="Search name, store, role..." value={search}
            onChange={e => setSearch(e.target.value)} style={{ width: 240 }} />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
            Show inactive
          </label>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Home Store</th>
                <th>Role</th>
                <th style={{ textAlign: 'right' }}>Pay Rate</th>
                <th>EPay Login</th>
                <th>Contact</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, i) => (
                <tr key={i} style={{ opacity: e.is_active ? 1 : 0.5 }}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{e.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>ID: {e.employee_id}</div>
                  </td>
                  <td style={{ fontSize: 13 }}>{e.home_store}</td>
                  <td>
                    <span className="badge badge-blue" style={{ fontSize: 11 }}>{e.role || '—'}</span>
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 600 }}>
                    ${(e.pay_rate || 0).toFixed(2)}/hr
                  </td>
                  <td style={{ fontSize: 12, fontFamily: 'monospace', color: e.epay_login ? 'var(--text)' : 'var(--text3)' }}>
                    {e.epay_login || '—'}
                    {e.epay_salesperson && <div style={{ fontFamily: 'sans-serif', fontSize: 11, color: 'var(--text3)' }}>{e.epay_salesperson}</div>}
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--text3)' }}>
                    {e.email && <div>📧 {e.email}</div>}
                    {e.phone && <div>📱 {e.phone}</div>}
                  </td>
                  <td>
                    <span className={`badge ${e.is_active ? 'badge-green' : 'badge-slate'}`}>
                      {e.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={7} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No employees found
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
