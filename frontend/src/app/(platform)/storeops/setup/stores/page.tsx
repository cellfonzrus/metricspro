'use client'
// Store Setup (Phase W2, owner directive 2026-09-01) — the STORES half of the old combined
// /storeops/admin page, lifted verbatim into its own route (mechanical extraction; shared helpers
// in ../lib.tsx). /storeops/admin keeps working for backward compat with a banner pointing here.
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { sel, cell, STORE_EDIT_FIELDS, STORE_TZ_OPTS, isDirty, MarketField } from '../lib'

export default function StoreSetupPage() {
  const [stores, setStores] = useState<any[]>([])
  const [origStores, setOrigStores] = useState<Record<string, any>>({})
  const [rowBusy, setRowBusy] = useState<Record<string, boolean>>({})
  const [rowMsg, setRowMsg] = useState<Record<string, string>>({})
  const [bulkBusy, setBulkBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [upBusy, setUpBusy] = useState(false)
  const [newStore, setNewStore] = useState<any>({ store_code: '', address: '', market: '', monthly_target: '', timezone: '' })
  const [markets, setMarkets] = useState<string[]>([])   // RULE THREE dropdown options (GET /storeops/markets)

  async function loadAll() {
    setLoading(true)
    try {
      const [s, mk] = await Promise.all([
        // 2026-08-06: GET /stores now defaults to active-only (the disabled-T-store picker-leak fix)
        // — this page manages/re-enables stores, so it MUST keep seeing inactive ones.
        api('/api/v1/storeops/stores?include_inactive=true').catch(() => []),
        api('/api/v1/storeops/markets').catch(() => ({ markets: [] })),
      ])
      const sList = (s || []).map((x: any) => ({ ...x }))
      setStores(sList)
      setMarkets(mk?.markets || [])
      setOrigStores(Object.fromEntries(sList.map((x: any) => [x.id, { ...x }])))
      setRowMsg({})
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoading(false)
  }
  useEffect(() => { loadAll() }, [])

  const setStore = (id: any, patch: any) => setStores(ss => ss.map(s => s.id === id ? { ...s, ...patch } : s))

  function flashRow(key: string, text: string, ms = 2500) {
    setRowMsg(m => ({ ...m, [key]: text }))
    if (ms) setTimeout(() => setRowMsg(m => (m[key] === text ? { ...m, [key]: '' } : m)), ms)
  }

  async function refreshMarkets() {
    try { const mk = await api('/api/v1/storeops/markets'); setMarkets(mk?.markets || []) } catch { /* best-effort */ }
  }

  async function saveStore(s: any) {
    setMsg('')
    const key = `store-${s.id}`
    setRowBusy(b => ({ ...b, [key]: true }))
    try {
      await api(`/api/v1/storeops/stores/${s.id}`, { method: 'PATCH', body: JSON.stringify({
        store_code: s.store_code, address: s.address, market: s.market,
        monthly_target: Number(s.monthly_target) || 0, net_profit_target: Number(s.net_profit_target) || 0,
        is_active: !!s.is_active, phone: s.phone,
        timezone: s.timezone || null,
      }) })
      setOrigStores(o => ({ ...o, [s.id]: { ...s } }))
      setMsg(`Saved ${s.store_code}`)
      flashRow(key, '✓ saved')
      refreshMarkets()
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)); flashRow(key, '✗ failed') }
    finally { setRowBusy(b => ({ ...b, [key]: false })) }
  }

  // AUTO-SAVE: a store's Active checkbox saves itself immediately (optimistic; rolls back on failure).
  async function toggleStoreActive(s: any, checked: boolean) {
    const prevVal = !!s.is_active
    const key = `store-${s.id}`
    setStore(s.id, { is_active: checked })
    setRowBusy(b => ({ ...b, [key]: true }))
    try {
      await api(`/api/v1/storeops/stores/${s.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: checked }) })
      setOrigStores(o => ({ ...o, [s.id]: { ...(o[s.id] || s), is_active: checked } }))
      flashRow(key, checked ? '✓ activated' : '✓ deactivated')
    } catch (err: any) {
      setStore(s.id, { is_active: prevVal })   // rollback — never show a fake success
      flashRow(key, '✗ ' + (err?.message || 'save failed'), 5000)
    } finally { setRowBusy(b => ({ ...b, [key]: false })) }
  }

  async function saveAllStores() {
    const dirty = stores.filter(s => isDirty(s, origStores[s.id], STORE_EDIT_FIELDS))
    if (!dirty.length) return
    setBulkBusy(true); setMsg('')
    let ok = 0, fail = 0
    for (const s of dirty) {
      try {
        await api(`/api/v1/storeops/stores/${s.id}`, { method: 'PATCH', body: JSON.stringify({
          store_code: s.store_code, address: s.address, market: s.market,
          monthly_target: Number(s.monthly_target) || 0, is_active: !!s.is_active, phone: s.phone,
        }) })
        setOrigStores(o => ({ ...o, [s.id]: { ...s } }))
        flashRow(`store-${s.id}`, '✓ saved')
        ok++
      } catch (err: any) { flashRow(`store-${s.id}`, '✗ failed', 5000); fail++ }
    }
    setMsg(`Saved ${ok} store(s)${fail ? ` · ${fail} failed (see row)` : ''}.`)
    setBulkBusy(false)
    refreshMarkets()
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

  // ---- bulk STORE setup ----
  async function downloadStoreTemplate() {
    const XLSX = await import('xlsx')
    const aoa = [['store_code', 'address', 'market', 'monthly_target', 'phone'],
      ['STORE01', '123 Main St, City, ST', 'North', '', '2125550123']]
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

  const dirtyStoreCount = stores.filter(s => isDirty(s, origStores[s.id], STORE_EDIT_FIELDS)).length

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏬 Store Setup</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Store codes, addresses, markets, time zones and targets — add, edit, bulk-upload.
          People live in <Link href="/storeops/setup/employees" style={{ color: 'var(--accent)' }}>Employee Setup</Link>.
        </p>
      </div>

      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : (
        <>
          {/* Add store */}
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>➕ Add store</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 120 }} placeholder="Store code *" value={newStore.store_code} onChange={e => setNewStore({ ...newStore, store_code: e.target.value })} />
              <input style={{ ...sel, width: 220 }} placeholder="Address" value={newStore.address} onChange={e => setNewStore({ ...newStore, address: e.target.value })} />
              <MarketField width={130} value={newStore.market} options={markets} onChange={v => setNewStore({ ...newStore, market: v })} />
              <select style={{ ...sel, width: 150 }} value={newStore.timezone} onChange={e => setNewStore({ ...newStore, timezone: e.target.value })} title="Store time zone — blank uses the company default">
                {STORE_TZ_OPTS.map(t => <option key={t.v || 'default'} value={t.v}>{t.label}</option>)}
              </select>
              <input style={{ ...sel, width: 120 }} type="number" placeholder="Monthly target" value={newStore.monthly_target} onChange={e => setNewStore({ ...newStore, monthly_target: e.target.value })} />
              <button className="btn btn-primary" onClick={addStore}>➕ Add</button>
            </div>
          </div>

          {/* Bulk store setup */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>{stores.length} stores</span>
            <button className="btn btn-primary" disabled={!dirtyStoreCount || bulkBusy} onClick={saveAllStores} title="Save every changed store row in one action">
              {bulkBusy ? '⏳ Saving…' : `💾 Save All Changed${dirtyStoreCount ? ` (${dirtyStoreCount})` : ''}`}
            </button>
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
                {['Store code', 'Address', 'Market', 'Time zone', 'Monthly target', 'Active', ''].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {stores.map(s => {
                  const key = `store-${s.id}`
                  const dirty = isDirty(s, origStores[s.id], STORE_EDIT_FIELDS)
                  return (
                  <tr key={s.id} style={{ opacity: s.is_active ? 1 : 0.5 }}>
                    <td style={cell}><input style={{ ...sel, width: 110 }} value={s.store_code || ''} onChange={ev => setStore(s.id, { store_code: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 220 }} value={s.address || ''} onChange={ev => setStore(s.id, { address: ev.target.value })} /></td>
                    <td style={cell}><MarketField width={130} value={s.market} options={markets} onChange={v => setStore(s.id, { market: v })} /></td>
                    <td style={cell}>
                      <select style={{ ...sel, width: 150 }} value={s.timezone || ''} onChange={ev => setStore(s.id, { timezone: ev.target.value || null })} title="Store time zone — blank uses the company default">
                        {STORE_TZ_OPTS.map(t => <option key={t.v || 'default'} value={t.v}>{t.label}</option>)}
                      </select>
                    </td>
                    <td style={cell}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                        <input style={{ ...sel, width: 120 }} type="number" title="Monthly sales/production target" value={s.monthly_target ?? ''} onChange={ev => setStore(s.id, { monthly_target: ev.target.value })} />
                        <input style={{ ...sel, width: 120 }} type="number" title="Net profit target ($) — the P&L goal" placeholder="Net profit $" value={s.net_profit_target ?? ''} onChange={ev => setStore(s.id, { net_profit_target: ev.target.value })} />
                      </div>
                    </td>
                    <td style={cell}>
                      <input type="checkbox" checked={!!s.is_active} disabled={!!rowBusy[key]}
                        onChange={ev => toggleStoreActive(s, ev.target.checked)} title="Auto-saves immediately" />
                    </td>
                    <td style={cell}>
                      <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 10px' }} disabled={!!rowBusy[key]} onClick={() => saveStore(s)}>💾</button>
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
