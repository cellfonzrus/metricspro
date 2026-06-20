'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--border)' }
const EMP_ROLES = ['Sales Rep', 'Assistant Manager', 'Store Manager', 'Market Manager', 'Admin', 'Other']

export default function StoreOpsAdminPage() {
  const [tab, setTab] = useState<'employees' | 'stores'>('employees')
  const [emps, setEmps] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [search, setSearch] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [upBusy, setUpBusy] = useState(false)
  const [newEmp, setNewEmp] = useState<any>({ name: '', employee_id: '', home_store: '', role: 'Sales Rep', pay_rate: '', email: '', phone: '' })
  const [newStore, setNewStore] = useState<any>({ store_code: '', address: '', market: '', monthly_target: '' })

  async function loadAll() {
    setLoading(true)
    try {
      const [e, s] = await Promise.all([
        api('/api/v1/storeops/employees?include_inactive=true').catch(() => []),
        api('/api/v1/storeops/stores').catch(() => []),
      ])
      setEmps((e || []).map((x: any) => ({ ...x })))
      setStores((s || []).map((x: any) => ({ ...x })))
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoading(false)
  }
  useEffect(() => { loadAll() }, [])

  const setEmp = (id: any, patch: any) => setEmps(es => es.map(e => e.id === id ? { ...e, ...patch } : e))
  const setStore = (id: any, patch: any) => setStores(ss => ss.map(s => s.id === id ? { ...s, ...patch } : s))

  async function saveEmp(e: any) {
    setMsg('')
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({
        name: e.name, employee_id: e.employee_id, home_store: e.home_store, role: e.role,
        pay_rate: Number(e.pay_rate) || 0, is_active: !!e.is_active, email: e.email, phone: e.phone,
      }) })
      setMsg(`Saved ${e.name}`)
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
  }
  async function addEmp() {
    if (!newEmp.name.trim()) { setMsg('Employee name is required.'); return }
    setMsg('')
    try {
      await api('/api/v1/storeops/employees', { method: 'POST', body: JSON.stringify({ ...newEmp, pay_rate: Number(newEmp.pay_rate) || 0 }) })
      setMsg(`Added ${newEmp.name}`)
      setNewEmp({ name: '', employee_id: '', home_store: '', role: 'Sales Rep', pay_rate: '', email: '', phone: '' })
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

  async function saveStore(s: any) {
    setMsg('')
    try {
      await api(`/api/v1/storeops/stores/${s.id}`, { method: 'PATCH', body: JSON.stringify({
        store_code: s.store_code, address: s.address, market: s.market,
        monthly_target: Number(s.monthly_target) || 0, is_active: !!s.is_active, phone: s.phone,
      }) })
      setMsg(`Saved ${s.store_code}`)
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
  }
  async function addStore() {
    if (!newStore.store_code.trim()) { setMsg('Store code is required.'); return }
    setMsg('')
    try {
      await api('/api/v1/storeops/stores', { method: 'POST', body: JSON.stringify({ ...newStore, monthly_target: Number(newStore.monthly_target) || 0 }) })
      setMsg(`Added ${newStore.store_code}`)
      setNewStore({ store_code: '', address: '', market: '', monthly_target: '' })
      await loadAll()
    } catch (err: any) { setMsg('Add failed: ' + (err?.message || err)) }
  }

  // ---- bulk payscale ----
  async function downloadPayscaleTemplate() {
    const XLSX = await import('xlsx')
    const aoa = [['employee_id', 'name', 'pay_rate'],
      ...emps.filter(e => e.is_active).map(e => [e.employee_id || '', e.name, e.pay_rate ?? ''])]
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    ws['!cols'] = [{ wch: 16 }, { wch: 24 }, { wch: 10 }]
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Payscale')
    XLSX.writeFile(wb, 'payscale-template.xlsx')
  }
  async function uploadPayscale(file: File) {
    setUpBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, keys: string[]) => { for (const k of Object.keys(r)) if (keys.includes(k.trim().toLowerCase())) return String(r[k]).trim(); return '' }
      const rows = raw.map(r => ({
        employee_id: pick(r, ['employee_id', 'emp id', 'id']),
        name: pick(r, ['name', 'employee', 'full_name']),
        pay_rate: pick(r, ['pay_rate', 'pay rate', 'rate', 'pay']),
      })).filter(r => r.pay_rate !== '' && (r.employee_id || r.name))
      if (!rows.length) { setMsg('No valid rows (need pay_rate + employee_id/name).'); setUpBusy(false); return }
      const res = await api('/api/v1/storeops/employees/bulk-payscale', { method: 'POST', body: JSON.stringify({ rows }) })
      const errs = (res.errors || []).map((e: any) => `Row ${e.row}: ${e.error}${e.ref ? ' (' + e.ref + ')' : ''}`)
      setMsg(`Pay rates updated: ${res.updated}${errs.length ? ` · ${errs.length} skipped` : ''}.${errs.length ? ' ' + errs.slice(0, 4).join('; ') : ''}`)
      await loadAll()
    } catch (err: any) { setMsg('Upload failed: ' + (err?.message || err)) }
    setUpBusy(false)
  }

  const filtered = emps.filter(e => (showInactive || e.is_active) &&
    (!search || `${e.name} ${e.home_store || ''} ${e.role || ''} ${e.email || ''}`.toLowerCase().includes(search.toLowerCase())))

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🛠️ StoreOps Admin</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Manage employees, pay rates, and stores.</p>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        {(['employees', 'stores'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{ padding: '7px 16px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, fontWeight: 600, cursor: 'pointer', background: tab === t ? 'var(--accent)' : 'var(--surface)', color: tab === t ? '#fff' : 'var(--text2)' }}>
            {t === 'employees' ? '👥 Employees & Pay' : '🏪 Stores'}
          </button>
        ))}
        {msg && <span style={{ fontSize: 13, marginLeft: 8 }}>{msg}</span>}
      </div>

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : tab === 'employees' ? (
        <>
          {/* Add employee */}
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>➕ Add employee</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 160 }} placeholder="Name *" value={newEmp.name} onChange={e => setNewEmp({ ...newEmp, name: e.target.value })} />
              <input style={{ ...sel, width: 110 }} placeholder="Emp ID" value={newEmp.employee_id} onChange={e => setNewEmp({ ...newEmp, employee_id: e.target.value })} />
              <input style={{ ...sel, width: 110 }} placeholder="Home store" value={newEmp.home_store} onChange={e => setNewEmp({ ...newEmp, home_store: e.target.value })} />
              <select style={sel} value={newEmp.role} onChange={e => setNewEmp({ ...newEmp, role: e.target.value })}>{EMP_ROLES.map(r => <option key={r}>{r}</option>)}</select>
              <input style={{ ...sel, width: 90 }} type="number" placeholder="$/hr" value={newEmp.pay_rate} onChange={e => setNewEmp({ ...newEmp, pay_rate: e.target.value })} />
              <input style={{ ...sel, width: 170 }} placeholder="Email" value={newEmp.email} onChange={e => setNewEmp({ ...newEmp, email: e.target.value })} />
              <input style={{ ...sel, width: 120 }} placeholder="Phone" value={newEmp.phone} onChange={e => setNewEmp({ ...newEmp, phone: e.target.value })} />
              <button className="btn btn-primary" onClick={addEmp}>➕ Add</button>
            </div>
          </div>

          {/* Toolbar + bulk payscale */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <input className="input" style={{ ...sel, width: 240 }} placeholder="Search name / store / role…" value={search} onChange={e => setSearch(e.target.value)} />
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 5 }}>
              <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} /> show inactive
            </label>
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>{filtered.length} shown</span>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Bulk pay rates:</span>
            <button className="btn" onClick={downloadPayscaleTemplate}>⬇️ Template</button>
            <label className="btn" style={{ cursor: upBusy ? 'default' : 'pointer', margin: 0 }}>
              {upBusy ? '⏳ Uploading…' : '⬆️ Upload pay rates'}
              <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={upBusy}
                onChange={e => { const f = e.target.files?.[0]; if (f) uploadPayscale(f); e.currentTarget.value = '' }} />
            </label>
          </div>

          <div className="table-wrapper">
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Name', 'Emp ID', 'Home store', 'Role', 'Pay $/hr', 'Email', 'Phone', 'Active', ''].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {filtered.map(e => (
                  <tr key={e.id} style={{ opacity: e.is_active ? 1 : 0.5 }}>
                    <td style={cell}><input style={{ ...sel, width: 150 }} value={e.name || ''} onChange={ev => setEmp(e.id, { name: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 100 }} value={e.employee_id || ''} onChange={ev => setEmp(e.id, { employee_id: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 100 }} value={e.home_store || ''} onChange={ev => setEmp(e.id, { home_store: ev.target.value })} /></td>
                    <td style={cell}><select style={sel} value={e.role || 'Other'} onChange={ev => setEmp(e.id, { role: ev.target.value })}>{EMP_ROLES.map(r => <option key={r}>{r}</option>)}</select></td>
                    <td style={cell}><input style={{ ...sel, width: 80 }} type="number" value={e.pay_rate ?? ''} onChange={ev => setEmp(e.id, { pay_rate: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 160 }} value={e.email || ''} onChange={ev => setEmp(e.id, { email: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 110 }} value={e.phone || ''} onChange={ev => setEmp(e.id, { phone: ev.target.value })} /></td>
                    <td style={cell}><input type="checkbox" checked={!!e.is_active} onChange={ev => setEmp(e.id, { is_active: ev.target.checked })} /></td>
                    <td style={cell}>
                      <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => saveEmp(e)}>💾</button>
                      <button className="btn" style={{ fontSize: 12, padding: '4px 10px', marginLeft: 6, color: '#b91c1c' }} onClick={() => delEmp(e)}>🗑️</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          {/* Add store */}
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>➕ Add store</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 120 }} placeholder="Store code *" value={newStore.store_code} onChange={e => setNewStore({ ...newStore, store_code: e.target.value })} />
              <input style={{ ...sel, width: 220 }} placeholder="Address" value={newStore.address} onChange={e => setNewStore({ ...newStore, address: e.target.value })} />
              <input style={{ ...sel, width: 110 }} placeholder="Market" value={newStore.market} onChange={e => setNewStore({ ...newStore, market: e.target.value })} />
              <input style={{ ...sel, width: 120 }} type="number" placeholder="Monthly target" value={newStore.monthly_target} onChange={e => setNewStore({ ...newStore, monthly_target: e.target.value })} />
              <button className="btn btn-primary" onClick={addStore}>➕ Add</button>
            </div>
          </div>

          <div className="table-wrapper">
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Store code', 'Address', 'Market', 'Monthly target', 'Active', ''].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {stores.map(s => (
                  <tr key={s.id} style={{ opacity: s.is_active ? 1 : 0.5 }}>
                    <td style={cell}><input style={{ ...sel, width: 110 }} value={s.store_code || ''} onChange={ev => setStore(s.id, { store_code: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 220 }} value={s.address || ''} onChange={ev => setStore(s.id, { address: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 110 }} value={s.market || ''} onChange={ev => setStore(s.id, { market: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 110 }} type="number" value={s.monthly_target ?? ''} onChange={ev => setStore(s.id, { monthly_target: ev.target.value })} /></td>
                    <td style={cell}><input type="checkbox" checked={!!s.is_active} onChange={ev => setStore(s.id, { is_active: ev.target.checked })} /></td>
                    <td style={cell}><button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => saveStore(s)}>💾</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
