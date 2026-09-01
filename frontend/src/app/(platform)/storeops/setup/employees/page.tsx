'use client'
// Employee Setup (Phase W2, owner directive 2026-09-01) — the EMPLOYEES half of the old combined
// /storeops/admin page, lifted verbatim into its own route (mechanical extraction; shared helpers
// in ../lib.tsx). /storeops/admin keeps working for backward compat with a banner pointing here.
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { sel, cell, EMP_EDIT_FIELDS, isDirty, PHONE_EG, cleanPhone } from '../lib'

export default function EmployeeSetupPage() {
  const [emps, setEmps] = useState<any[]>([])
  const [origEmps, setOrigEmps] = useState<Record<string, any>>({})
  const [rowBusy, setRowBusy] = useState<Record<string, boolean>>({})
  const [rowMsg, setRowMsg] = useState<Record<string, string>>({})
  const [bulkBusy, setBulkBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [search, setSearch] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [upBusy, setUpBusy] = useState(false)
  const [newEmp, setNewEmp] = useState<any>({ name: '', employee_id: '', home_store: '', email: '', phone: '' })

  async function loadAll() {
    setLoading(true)
    try {
      const e = await api('/api/v1/storeops/employees?include_inactive=true').catch(() => [])
      const eList = (e || []).map((x: any) => ({ ...x }))
      setEmps(eList)
      setOrigEmps(Object.fromEntries(eList.map((x: any) => [x.id, { ...x }])))
      setRowMsg({})
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoading(false)
  }
  useEffect(() => { loadAll() }, [])

  const setEmp = (id: any, patch: any) => setEmps(es => es.map(e => e.id === id ? { ...e, ...patch } : e))

  function flashRow(key: string, text: string, ms = 2500) {
    setRowMsg(m => ({ ...m, [key]: text }))
    if (ms) setTimeout(() => setRowMsg(m => (m[key] === text ? { ...m, [key]: '' } : m)), ms)
  }

  async function saveEmp(e: any) {
    setMsg('')
    const ph = cleanPhone(e.phone)
    if (ph === null) { alert(PHONE_EG); return }
    const key = `emp-${e.id}`
    setRowBusy(b => ({ ...b, [key]: true }))
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({
        name: e.name, employee_id: e.employee_id, home_store: e.home_store,
        is_active: !!e.is_active, email: e.email, phone: ph,   // pay + role are set in HR / Roles & Access
      }) })
      setOrigEmps(o => ({ ...o, [e.id]: { ...e, phone: ph } }))
      setMsg(`Saved ${e.name}`)
      flashRow(key, '✓ saved')
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)); flashRow(key, '✗ failed') }
    finally { setRowBusy(b => ({ ...b, [key]: false })) }
  }

  // AUTO-SAVE: the Active checkbox saves itself immediately (optimistic; rolls back on failure).
  async function toggleEmpActive(e: any, checked: boolean) {
    const prevVal = !!e.is_active
    const key = `emp-${e.id}`
    setEmp(e.id, { is_active: checked })
    setRowBusy(b => ({ ...b, [key]: true }))
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: checked }) })
      setOrigEmps(o => ({ ...o, [e.id]: { ...(o[e.id] || e), is_active: checked } }))
      flashRow(key, checked ? '✓ activated' : '✓ deactivated')
    } catch (err: any) {
      setEmp(e.id, { is_active: prevVal })   // rollback — never show a fake success
      flashRow(key, '✗ ' + (err?.message || 'save failed'), 5000)
    } finally { setRowBusy(b => ({ ...b, [key]: false })) }
  }

  async function saveAllEmps() {
    const dirty = emps.filter(e => isDirty(e, origEmps[e.id], EMP_EDIT_FIELDS))
    if (!dirty.length) return
    setBulkBusy(true); setMsg('')
    let ok = 0, fail = 0
    for (const e of dirty) {
      const ph = cleanPhone(e.phone)
      if (ph === null) { fail++; flashRow(`emp-${e.id}`, '✗ invalid phone', 5000); continue }
      try {
        await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({
          name: e.name, employee_id: e.employee_id, home_store: e.home_store,
          is_active: !!e.is_active, email: e.email, phone: ph,
        }) })
        setOrigEmps(o => ({ ...o, [e.id]: { ...e, phone: ph } }))
        flashRow(`emp-${e.id}`, '✓ saved')
        ok++
      } catch (err: any) { flashRow(`emp-${e.id}`, '✗ failed', 5000); fail++ }
    }
    setMsg(`Saved ${ok} employee(s)${fail ? ` · ${fail} failed (see row)` : ''}.`)
    setBulkBusy(false)
  }

  async function addEmp() {
    if (!newEmp.name.trim()) { setMsg('Employee name is required.'); return }
    const ph = cleanPhone(newEmp.phone)
    if (ph === null) { alert(PHONE_EG); return }
    setMsg('')
    try {
      await api('/api/v1/storeops/employees', { method: 'POST', body: JSON.stringify({ ...newEmp, phone: ph, pay_rate: Number(newEmp.pay_rate) || 0 }) })
      setMsg(`Added ${newEmp.name}`)
      setNewEmp({ name: '', employee_id: '', home_store: '', email: '', phone: '' })
      await loadAll()
    } catch (err: any) { setMsg('Add failed: ' + (err?.message || err)) }
  }

  async function delEmp(e: any) {
    if (!confirm(`Delete ${e.name}? This cannot be undone. (If they have shifts, deactivate instead.)`)) return
    setMsg('')
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'DELETE' })
      setMsg(`Deleted ${e.name}`)
      await loadAll()
    } catch (err: any) { setMsg('Delete failed: ' + (err?.message || err)) }
  }

  // ---- bulk EMPLOYEE setup (full records — for standing up a new tenant fast) ----
  async function downloadEmpTemplate() {
    const XLSX = await import('xlsx')
    const aoa = [['name', 'employee_id', 'home_store', 'email', 'phone'],
      ['Jane Doe', 'E1001', 'STORE01', 'jane@example.com', '2125550123']]
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    ws['!cols'] = [{ wch: 22 }, { wch: 12 }, { wch: 14 }, { wch: 24 }, { wch: 16 }]
    const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, 'Employees')
    XLSX.writeFile(wb, 'employees-template.xlsx')
  }
  async function uploadEmpBulk(file: File) {
    setUpBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, keys: string[]) => { for (const k of Object.keys(r)) if (keys.includes(k.trim().toLowerCase())) return String(r[k]).trim(); return '' }
      const employees = raw.map(r => ({
        name: pick(r, ['name', 'employee', 'full_name']),
        employee_id: pick(r, ['employee_id', 'emp id', 'id']),
        home_store: pick(r, ['home_store', 'home store', 'store', 'store_code']),
        email: pick(r, ['email']),
        phone: pick(r, ['phone', 'mobile', 'cell']),
      })).filter(r => r.name)
      if (!employees.length) { setMsg('No valid rows (each needs a name).'); setUpBusy(false); return }
      const res = await api('/api/v1/storeops/employees/bulk', { method: 'POST', body: JSON.stringify({ employees }) })
      setMsg(`Added ${res.inserted} employee(s)${res.skipped ? ` · ${res.skipped} skipped (blank name / duplicate ID)` : ''}.`)
      await loadAll()
    } catch (err: any) { setMsg('Upload failed: ' + (err?.message || err)) }
    setUpBusy(false)
  }

  const filtered = emps.filter(e => (showInactive || e.is_active) &&
    (!search || `${e.name} ${e.home_store || ''} ${e.role || ''} ${e.email || ''}`.toLowerCase().includes(search.toLowerCase())))
  const dirtyEmpCount = emps.filter(e => isDirty(e, origEmps[e.id], EMP_EDIT_FIELDS)).length

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧑‍🔧 Employee Setup</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Manage the employee records behind scheduling & payroll — add, edit, bulk-upload.
          Stores live in <Link href="/storeops/setup/stores" style={{ color: 'var(--accent)' }}>Store Setup</Link>.
        </p>
      </div>

      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : (
        <>
          {/* Add employee */}
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>➕ Add employee</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 160 }} placeholder="Name *" value={newEmp.name} onChange={e => setNewEmp({ ...newEmp, name: e.target.value })} />
              <input style={{ ...sel, width: 110 }} placeholder="Emp ID" value={newEmp.employee_id} onChange={e => setNewEmp({ ...newEmp, employee_id: e.target.value })} />
              <input style={{ ...sel, width: 110 }} placeholder="Home store" value={newEmp.home_store} onChange={e => setNewEmp({ ...newEmp, home_store: e.target.value })} />
              <input style={{ ...sel, width: 170 }} placeholder="Email" value={newEmp.email} onChange={e => setNewEmp({ ...newEmp, email: e.target.value })} />
              <input style={{ ...sel, width: 150 }} placeholder="Phone e.g. 2125550123" title={PHONE_EG} value={newEmp.phone} onChange={e => setNewEmp({ ...newEmp, phone: e.target.value })} />
              <button className="btn btn-primary" onClick={addEmp}>➕ Add</button>
            </div>
          </div>

          {/* Toolbar + bulk upload */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <input className="input" style={{ ...sel, width: 240 }} placeholder="Search name / store / role…" value={search} onChange={e => setSearch(e.target.value)} />
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 5 }}>
              <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} /> show inactive
            </label>
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>{filtered.length} shown</span>
            {/* Active toggle auto-saves per row (below); this covers any OTHER edited field (name/ID/
                store/email/phone) across multiple rows at once. */}
            <button className="btn btn-primary" disabled={!dirtyEmpCount || bulkBusy} onClick={saveAllEmps} title="Save every changed employee row in one action">
              {bulkBusy ? '⏳ Saving…' : `💾 Save All Changed${dirtyEmpCount ? ` (${dirtyEmpCount})` : ''}`}
            </button>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Bulk add employees:</span>
            <button className="btn" onClick={downloadEmpTemplate}>⬇️ Template</button>
            <label className="btn" style={{ cursor: upBusy ? 'default' : 'pointer', margin: 0 }}>
              {upBusy ? '⏳ Uploading…' : '⬆️ Upload employees'}
              <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={upBusy}
                onChange={e => { const f = e.target.files?.[0]; if (f) uploadEmpBulk(f); e.currentTarget.value = '' }} />
            </label>
            <span style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 6 }}>Pay rates are managed in the HR module.</span>
          </div>

          <div className="table-wrapper">
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Name', 'Emp ID', 'Home store', 'Email', 'Phone', 'Active', ''].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {filtered.map(e => {
                  const key = `emp-${e.id}`
                  const dirty = isDirty(e, origEmps[e.id], EMP_EDIT_FIELDS)
                  return (
                  <tr key={e.id} style={{ opacity: e.is_active ? 1 : 0.5 }}>
                    <td style={cell}><input style={{ ...sel, width: 150 }} value={e.name || ''} onChange={ev => setEmp(e.id, { name: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 100 }} value={e.employee_id || ''} onChange={ev => setEmp(e.id, { employee_id: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 100 }} value={e.home_store || ''} onChange={ev => setEmp(e.id, { home_store: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 160 }} value={e.email || ''} onChange={ev => setEmp(e.id, { email: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 130 }} placeholder="2125550123" title={PHONE_EG} value={e.phone || ''} onChange={ev => setEmp(e.id, { phone: ev.target.value })} /></td>
                    <td style={cell}>
                      <input type="checkbox" checked={!!e.is_active} disabled={!!rowBusy[key]}
                        onChange={ev => toggleEmpActive(e, ev.target.checked)} title="Auto-saves immediately" />
                    </td>
                    <td style={cell}>
                      <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 10px' }} disabled={!!rowBusy[key]} onClick={() => saveEmp(e)}>💾</button>
                      <button className="btn" style={{ fontSize: 12, padding: '4px 10px', marginLeft: 6, color: '#b91c1c' }} onClick={() => delEmp(e)}>🗑️</button>
                      {dirty && !rowMsg[key] && <span style={{ fontSize: 11, color: '#b45309', marginLeft: 6 }}>● unsaved</span>}
                      {rowMsg[key] && <span style={{ fontSize: 11, marginLeft: 6, color: rowMsg[key].startsWith('✗') ? '#b91c1c' : '#166534' }}>{rowMsg[key]}</span>}
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
