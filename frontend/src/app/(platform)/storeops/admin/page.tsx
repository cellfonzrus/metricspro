'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--border)' }

export default function StoreOpsAdminPage() {
  const [tab, setTab] = useState<'employees' | 'stores'>('employees')
  const [emps, setEmps] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [search, setSearch] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [upBusy, setUpBusy] = useState(false)
  const [newEmp, setNewEmp] = useState<any>({ name: '', employee_id: '', home_store: '', email: '', phone: '' })
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

  const PHONE_EG = 'Enter a 10-digit number or include country code — e.g. 5162330422 or +1 516 233 0422'
  function cleanPhone(raw: any): string | null {
    const s = String(raw ?? '').trim()
    if (!s) return ''                          // empty allowed
    const hasPlus = s.startsWith('+')
    const d = s.replace(/\D/g, '')
    if (hasPlus && d.length >= 11 && d.length <= 15) return '+' + d
    if (d.length === 10) return d
    if (d.length === 11 && d.startsWith('1')) return '+' + d
    return null                                // invalid → caller prompts with PHONE_EG
  }

  const setEmp = (id: any, patch: any) => setEmps(es => es.map(e => e.id === id ? { ...e, ...patch } : e))
  const setStore = (id: any, patch: any) => setStores(ss => ss.map(s => s.id === id ? { ...s, ...patch } : s))

  async function saveEmp(e: any) {
    setMsg('')
    const ph = cleanPhone(e.phone)
    if (ph === null) { alert(PHONE_EG); return }
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({
        name: e.name, employee_id: e.employee_id, home_store: e.home_store,
        is_active: !!e.is_active, email: e.email, phone: ph,   // pay + role are set in HR / Roles & Access
      }) })
      setMsg(`Saved ${e.name}`)
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
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


  // ---- bulk EMPLOYEE setup (full records — for standing up a new tenant fast) ----
  async function downloadEmpTemplate() {
    const XLSX = await import('xlsx')
    const aoa = [['name', 'employee_id', 'home_store', 'email', 'phone'],
      ['Jane Doe', 'E1001', 'STORE01', 'jane@example.com', '5162330422']]
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
  // ---- bulk STORE setup ----
  async function downloadStoreTemplate() {
    const XLSX = await import('xlsx')
    const aoa = [['store_code', 'address', 'market', 'monthly_target', 'phone'],
      ['STORE01', '123 Main St, City, ST', 'North', '', '5162330422']]
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    ws['!cols'] = [{ wch: 12 }, { wch: 30 }, { wch: 14 }, { wch: 14 }, { wch: 16 }]
    const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, 'Stores')
    XLSX.writeFile(wb, 'stores-template.xlsx')
  }
  async function uploadStoreBulk(file: File) {
    setUpBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, keys: string[]) => { for (const k of Object.keys(r)) if (keys.includes(k.trim().toLowerCase())) return String(r[k]).trim(); return '' }
      const storeRows = raw.map(r => ({
        store_code: pick(r, ['store_code', 'store code', 'store', 'code']),
        address: pick(r, ['address', 'location']),
        market: pick(r, ['market', 'region', 'district']),
        monthly_target: parseFloat(pick(r, ['monthly_target', 'monthly target', 'target'])) || 0,
        phone: pick(r, ['phone']),
      })).filter(r => r.store_code)
      if (!storeRows.length) { setMsg('No valid rows (each needs a store_code).'); setUpBusy(false); return }
      const res = await api('/api/v1/storeops/stores/bulk', { method: 'POST', body: JSON.stringify({ stores: storeRows }) })
      setMsg(`Added ${res.inserted} store(s)${res.skipped ? ` · ${res.skipped} skipped (blank / duplicate code)` : ''}.`)
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
              <input style={{ ...sel, width: 170 }} placeholder="Email" value={newEmp.email} onChange={e => setNewEmp({ ...newEmp, email: e.target.value })} />
              <input style={{ ...sel, width: 150 }} placeholder="Phone e.g. 5162330422" title={PHONE_EG} value={newEmp.phone} onChange={e => setNewEmp({ ...newEmp, phone: e.target.value })} />
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
                {filtered.map(e => (
                  <tr key={e.id} style={{ opacity: e.is_active ? 1 : 0.5 }}>
                    <td style={cell}><input style={{ ...sel, width: 150 }} value={e.name || ''} onChange={ev => setEmp(e.id, { name: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 100 }} value={e.employee_id || ''} onChange={ev => setEmp(e.id, { employee_id: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 100 }} value={e.home_store || ''} onChange={ev => setEmp(e.id, { home_store: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 160 }} value={e.email || ''} onChange={ev => setEmp(e.id, { email: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 130 }} placeholder="5162330422" title={PHONE_EG} value={e.phone || ''} onChange={ev => setEmp(e.id, { phone: ev.target.value })} /></td>
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

          {/* Bulk store setup */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>{stores.length} stores</span>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Bulk add stores:</span>
            <button className="btn" onClick={downloadStoreTemplate}>⬇️ Template</button>
            <label className="btn" style={{ cursor: upBusy ? 'default' : 'pointer', margin: 0 }}>
              {upBusy ? '⏳ Uploading…' : '⬆️ Upload stores'}
              <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={upBusy}
                onChange={e => { const f = e.target.files?.[0]; if (f) uploadStoreBulk(f); e.currentTarget.value = '' }} />
            </label>
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
