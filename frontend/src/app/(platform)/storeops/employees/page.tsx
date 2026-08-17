'use client'
import { useState, useEffect, Fragment } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import GoogleReviewsCard from '@/components/GoogleReviewsCard'

interface Employee {
  id: number; employee_id: string | null; name: string; home_store: string | null
  role: string | null; pay_rate: number; is_active: boolean
  epay_login: string | null; epay_salesperson: string | null; email: string | null; phone: string | null
}

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }

// Role is display-only here (it's assigned in HR / Roles & Access, same as pay) — this column just
// makes it easy to see at a glance who is a manager vs. a sales rep.
const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin', owner: 'Owner', market_manager: 'Market Manager', district_manager: 'District Manager',
  regional_manager: 'Regional Manager', store_manager: 'Store Manager', general_manager: 'General Manager',
  director: 'Director', executive: 'Executive', rep: 'Sales Rep', sales_rep: 'Sales Rep', employee: 'Employee',
}
function prettyRole(role: string | null): string {
  if (!role) return '—'
  return ROLE_LABELS[role.toLowerCase()] || role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}
function isManagerRole(role: string | null): boolean {
  return /manager|admin|owner|director|executive|lead|supervisor|regional|district/.test((role || '').toLowerCase())
}

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState<number | null>(null)
  // Phase 1.5 (owner directive 2026-08-06): expand one row to show that employee's Google rating(s)
  // for the store(s) they work — compact embed, GoogleReviewsCard fetches its own data off
  // employee_id and renders nothing if the integration is off/empty for this tenant.
  const [expanded, setExpanded] = useState<number | null>(null)

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
        name: e.name, employee_id: e.employee_id, home_store: e.home_store,
        is_active: !!e.is_active, email: e.email, phone: e.phone,   // pay + role set in HR / Roles & Access
        epay_login: e.epay_login, epay_salesperson: e.epay_salesperson,
      }) })
      setMsg(`Saved ${e.name}`)
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
    setSaving(null)
  }

  async function deleteEmp(e: Employee) {
    if (!confirm(`Permanently delete ${e.name}?\n\nThis also removes their role assignment + login (reflected in Roles & Access). History keyed by name is kept. If they're linked to shifts you'll be offered to deactivate instead.`)) return
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'DELETE' })
      setEmployees(es => es.filter(x => x.id !== e.id)); setMsg(`Deleted ${e.name}`)
    } catch (err: any) {
      if (confirm(`Couldn't delete (${err?.message || 'linked records'}). Deactivate ${e.name} instead?`)) {
        await deactivateEmp(e, true)
      }
    }
  }

  async function deactivateEmp(e: Employee, skipConfirm = false) {
    if (!skipConfirm && !confirm(`Deactivate ${e.name}? Marks them inactive and revokes their login (reversible).`)) return
    try {
      await api('/api/v1/core/employees/purge', { method: 'POST', body: JSON.stringify({
        employee_pk: e.id, email: e.email, employee_id: e.employee_id, mode: 'deactivate',
      }) })
      setEmp(e.id, { is_active: false }); setMsg(`Deactivated ${e.name}`)
    } catch (err: any) { setMsg('Deactivate failed: ' + (err?.message || err)) }
  }

  async function mergeEmp(dup: Employee, targetId: string) {
    if (!targetId) return
    const tgt = employees.find(o => String(o.id) === String(targetId))
    if (!tgt || !confirm(`Merge ${dup.name} INTO ${tgt.name}?\n${dup.name}'s shifts + time-off move to ${tgt.name}, then ${dup.name} is removed.`)) return
    try {
      const r = await api('/api/v1/storeops/employees/merge', { method: 'POST', body: JSON.stringify({ dup_id: dup.id, target_id: tgt.id }) })
      setEmployees(es => es.filter(x => x.id !== dup.id))
      setMsg(`Merged ${dup.name} → ${tgt.name} (${r.moved?.shifts || 0} shifts, ${r.moved?.time_off || 0} time-off moved).`)
    } catch (err: any) { setMsg('Merge failed: ' + (err?.message || err)) }
  }

  const filtered = employees.filter(e => {
    if (!showInactive && !e.is_active) return false
    if (search && ![e.name, e.home_store, e.role, e.email, e.epay_salesperson]
      .some(v => (v || '').toLowerCase().includes(search.toLowerCase()))) return false
    return true
  })

  // RULE FOUR (§3c): export the search/active-toggle-FILTERED roster (what you see is what exports).
  // No Fernet-encrypted PII lives on this table (that's onboarding intake_data, HR-only) — email/phone
  // here are the same plain values already rendered inline, so nothing to mask.
  const cols: ExportColumn[] = [
    { header: 'Name', field: 'name', role: 'rep', get: e => e.name || '' },
    { header: 'Emp ID', field: 'employee_id', get: e => e.employee_id || '' },
    { header: 'Home Store', field: 'home_store', role: 'store', get: e => e.home_store || '' },
    { header: 'Role', field: 'role', get: e => prettyRole(e.role) },
    { header: 'Email', field: 'email', get: e => e.email || '' },
    { header: 'Phone', field: 'phone', get: e => e.phone || '' },
    { header: 'ePay Login', field: 'epay_login', get: e => e.epay_login || '' },
    { header: 'ePay Salesperson', field: 'epay_salesperson', get: e => e.epay_salesperson || '' },
    { header: 'Active', field: 'is_active', get: e => e.is_active ? 'Yes' : 'No' },
  ]

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
          <ReportExportBar title="Employees" columns={cols} rows={filtered} />
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['', 'Name', 'Emp ID', 'Home store', 'Role', 'Email', 'Phone', 'ePay login', 'ePay salesperson', 'Active', ''].map((h, i) =>
                <th key={h + i} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {filtered.map(e => (
                <Fragment key={e.id}>
                  <tr style={{ opacity: e.is_active ? 1 : 0.5 }}>
                    <td style={cell}>
                      {e.employee_id && (
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 6px' }}
                          onClick={() => setExpanded(x => x === e.id ? null : e.id)}
                          title="Show Google rating(s) for this employee's store(s)">
                          {expanded === e.id ? '▾' : '▸'} ⭐
                        </button>
                      )}
                    </td>
                    <td style={cell}><input style={{ ...sel, width: 150 }} value={e.name || ''} onChange={ev => setEmp(e.id, { name: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 90 }} value={e.employee_id || ''} placeholder="—" onChange={ev => setEmp(e.id, { employee_id: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 100 }} value={e.home_store || ''} onChange={ev => setEmp(e.id, { home_store: ev.target.value })} /></td>
                    <td style={cell}>
                      {e.role
                        ? <span title="Set in HR / Roles & Access" style={{ fontSize: 11.5, fontWeight: 600, padding: '3px 9px', borderRadius: 999, whiteSpace: 'nowrap', border: '1px solid var(--border)', background: isManagerRole(e.role) ? 'rgba(37,99,235,0.12)' : 'var(--surface2)', color: isManagerRole(e.role) ? 'var(--accent)' : 'var(--text2)' }}>{prettyRole(e.role)}</span>
                        : <span style={{ color: 'var(--text3)' }}>—</span>}
                    </td>
                    <td style={cell}><input style={{ ...sel, width: 180 }} type="email" value={e.email || ''} placeholder="add email…" onChange={ev => setEmp(e.id, { email: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 120 }} value={e.phone || ''} onChange={ev => setEmp(e.id, { phone: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 120 }} value={e.epay_login || ''} onChange={ev => setEmp(e.id, { epay_login: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 150 }} value={e.epay_salesperson || ''} onChange={ev => setEmp(e.id, { epay_salesperson: ev.target.value })} /></td>
                    <td style={cell}><input type="checkbox" checked={!!e.is_active} onChange={ev => setEmp(e.id, { is_active: ev.target.checked })} /></td>
                    <td style={cell}>
                      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                        <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 8px' }} disabled={saving === e.id} onClick={() => saveEmp(e)} title="Save">{saving === e.id ? '…' : '💾'}</button>
                        {e.is_active && <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 8px' }} onClick={() => deactivateEmp(e)} title="Deactivate + revoke login">🚫</button>}
                        <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 8px', color: '#dc2626' }} onClick={() => deleteEmp(e)} title="Delete employee (removes login too)">🗑</button>
                        <select style={{ ...sel, width: 80 }} value="" title="Merge this duplicate INTO another employee" onChange={ev => { mergeEmp(e, ev.target.value); ev.target.value = '' }}>
                          <option value="">merge→</option>
                          {employees.filter(o => o.id !== e.id).map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
                        </select>
                      </div>
                    </td>
                  </tr>
                  {expanded === e.id && e.employee_id && (
                    <tr>
                      <td colSpan={11} style={{ padding: '4px 8px 12px 34px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                        <GoogleReviewsCard employeeId={e.employee_id} compact compactTitle={`⭐ Google Reviews — ${e.name}'s store(s)`} />
                      </td>
                    </tr>
                  )}
                </Fragment>
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
