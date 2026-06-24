'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Default expense list (name + Fixed/Variable). The user can add ad-hoc expenses.
const DEFAULT_CATS: { name: string; type: string }[] = [
  { name: 'Rent / Lease', type: 'Variable' }, { name: 'B2B Platform Fee', type: 'Fixed' },
  { name: 'Cellsmart POS', type: 'Fixed' }, { name: 'Electric', type: 'Variable' },
  { name: 'Heat / Gas', type: 'Variable' }, { name: 'Internet', type: 'Fixed' },
  { name: 'Insurance', type: 'Fixed' }, { name: 'Advertising', type: 'Fixed' },
  { name: 'Cleaning', type: 'Fixed' }, { name: 'Garbage / Waste', type: 'Variable' },
  { name: 'Maintenance', type: 'Fixed' }, { name: 'ADT Security', type: 'Fixed' },
  { name: 'Back Office Fee', type: 'Fixed' }, { name: 'Taxes / Accounting', type: 'Fixed' },
  { name: 'Employee Salaries', type: 'Fixed' }, { name: 'Owner / Mgmt Salaries', type: 'Fixed' },
]
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function ExpensesPage() {
  const { period } = usePeriod()
  const [stores, setStores] = useState<any[]>([])
  const [cats, setCats] = useState<{ name: string; type: string }[]>(DEFAULT_CATS)
  const [amounts, setAmounts] = useState<Record<string, Record<string, number>>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [market, setMarket] = useState('')
  const [storeSearch, setStoreSearch] = useState('')
  const [newCat, setNewCat] = useState({ name: '', type: 'Fixed' })
  const [upBusy, setUpBusy] = useState(false)
  const [carriedFrom, setCarriedFrom] = useState('')

  function load() {
    setLoading(true)
    Promise.all([
      api('/api/v1/storeops/stores').catch(() => []),
      api(`/api/v1/commcalc/expenses/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({ expenses: [] })),
    ]).then(([st, ex]: any) => {
      setStores((st || []).filter((s: any) => s.is_active !== false))
      setCarriedFrom(ex?.carried_from || '')
      const map: any = {}; const extra: Record<string, string> = {}
      ;(ex.expenses || []).forEach((e: any) => {
        if (!map[e.store_code]) map[e.store_code] = {}
        map[e.store_code][e.expense_name] = parseFloat(e.amount) || 0
        if (!DEFAULT_CATS.find(c => c.name === e.expense_name)) extra[e.expense_name] = e.expense_type || 'Fixed'
      })
      setAmounts(map)
      setCats([...DEFAULT_CATS, ...Object.entries(extra).map(([name, type]) => ({ name, type: type as string }))])
    }).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [period])

  const markets = Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort()
  const visStores = stores.filter(s => (!market || s.market === market) &&
    (!storeSearch || `${s.store_code} ${s.address || ''}`.toLowerCase().includes(storeSearch.toLowerCase())))
  const getVal = (sc: string, n: string) => amounts[sc]?.[n] || 0
  const setVal = (sc: string, n: string, v: number) => setAmounts(a => ({ ...a, [sc]: { ...a[sc], [n]: v } }))
  const storeTotal = (sc: string) => cats.reduce((s, c) => s + getVal(sc, c.name), 0)
  const grand = visStores.reduce((s, st) => s + storeTotal(st.store_code), 0)

  function addCat() {
    const name = newCat.name.trim()
    if (!name) return
    if (cats.find(c => c.name.toLowerCase() === name.toLowerCase())) { setMsg('That expense already exists.'); return }
    setCats(c => [...c, { name, type: newCat.type }]); setNewCat({ name: '', type: 'Fixed' })
  }

  async function save() {
    setSaving(true); setMsg('')
    const rows: any[] = []
    stores.forEach(s => cats.forEach(c => { const amt = getVal(s.store_code, c.name); if (amt > 0) rows.push({ store_code: s.store_code, expense_name: c.name, expense_type: c.type, amount: amt }) }))
    try {
      const r = await api(`/api/v1/commcalc/expenses/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify({ rows }) })
      setMsg(`Saved ${r.saved} expense entries. Re-run Calculation to include in Gross Profit.`)
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  async function downloadTemplate() {
    const XLSX = await import('xlsx')
    const aoa: any[] = [['store_code', 'expense_name', 'expense_type', 'amount']]
    stores.forEach(s => cats.forEach(c => aoa.push([s.store_code, c.name, c.type, getVal(s.store_code, c.name) || ''])))
    const ws = XLSX.utils.aoa_to_sheet(aoa); const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Expenses'); XLSX.writeFile(wb, `expenses-${period.replace(/\s+/g, '-')}.xlsx`)
  }
  async function upload(file: File) {
    setUpBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, k: string[]) => { for (const kk of Object.keys(r)) if (k.includes(kk.trim().toLowerCase())) return String(r[kk]).trim(); return '' }
      const map = { ...amounts }; const newCats: Record<string, string> = {}
      raw.forEach(r => {
        const sc = pick(r, ['store_code', 'store']); const nm = pick(r, ['expense_name', 'expense', 'name'])
        const tp = pick(r, ['expense_type', 'type']) || 'Fixed'; const amt = parseFloat(pick(r, ['amount', 'amt'])) || 0
        if (!sc || !nm) return
        if (!map[sc]) map[sc] = {}; map[sc][nm] = amt
        if (!cats.find(c => c.name === nm)) newCats[nm] = tp
      })
      setAmounts(map)
      if (Object.keys(newCats).length) setCats(c => [...c, ...Object.entries(newCats).map(([name, type]) => ({ name, type: type as string }))])
      setMsg(`Loaded ${raw.length} rows. Review, then Save All.`)
    } catch (e: any) { setMsg('Upload failed: ' + (e?.message || e)) }
    setUpBusy(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Store Expenses</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · {visStores.length} stores shown · Total <strong>{fmt(grand)}</strong></p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : '💾 Save All'}</button>
        </div>
      </div>

      {/* Toolbar: filters + add expense + bulk upload */}
      <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select style={inp} value={market} onChange={e => setMarket(e.target.value)}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <input style={{ ...inp, width: 160 }} placeholder="Find store…" value={storeSearch} onChange={e => setStoreSearch(e.target.value)} />
        <span style={{ width: 1, height: 22, background: 'var(--border)' }} />
        <input style={{ ...inp, width: 150 }} placeholder="New expense name" value={newCat.name} onChange={e => setNewCat({ ...newCat, name: e.target.value })} />
        <select style={inp} value={newCat.type} onChange={e => setNewCat({ ...newCat, type: e.target.value })}><option>Fixed</option><option>Variable</option></select>
        <button className="btn" onClick={addCat}>＋ Add expense</button>
        <span style={{ width: 1, height: 22, background: 'var(--border)' }} />
        <button className="btn" onClick={downloadTemplate}>⬇️ Template</button>
        <label className="btn" style={{ cursor: upBusy ? 'default' : 'pointer', margin: 0 }}>
          {upBusy ? '⏳…' : '⬆️ Upload expenses'}
          <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={upBusy}
            onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.currentTarget.value = '' }} />
        </label>
      </div>

      {carriedFrom && !loading && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: '#eef6ff', borderLeft: '4px solid var(--accent)', fontSize: 13 }}>
          📋 <b>Carried forward from {carriedFrom}</b> — no expenses entered for {period} yet, so last month's are pre-filled below.
          Review and <b>Save All</b> to keep them for {period} (they'll carry to next month too).
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : visStores.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No stores match. (Add stores in StoreOps Admin.)</div>
      ) : (
        <div style={{ overflowX: 'auto', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
          <table style={{ minWidth: visStores.length * 150 + 240, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--accent)' }}>
                <th style={{ padding: '10px 16px', color: 'white', fontSize: 12, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--accent)', width: 230 }}>Expense</th>
                {visStores.map(s => (
                  <th key={s.store_code} style={{ padding: '8px 10px', color: 'white', fontSize: 11, textAlign: 'right', minWidth: 130 }}>
                    <div style={{ fontWeight: 700 }}>{s.store_code}</div>
                    <div style={{ fontWeight: 400, opacity: 0.7, fontSize: 10 }}>{(s.address || '').substring(0, 18)}</div>
                  </th>
                ))}
                <th style={{ padding: '10px 14px', color: 'white', fontSize: 12, textAlign: 'right', width: 110 }}>Total</th>
              </tr>
            </thead>
            <tbody>
              {cats.map((cat, ci) => {
                const rowTotal = visStores.reduce((s, st) => s + getVal(st.store_code, cat.name), 0)
                return (
                  <tr key={cat.name} style={{ background: ci % 2 ? '#fafbfc' : 'white' }}>
                    <td style={{ padding: '6px 16px', fontSize: 13, fontWeight: 500, position: 'sticky', left: 0, background: ci % 2 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                      {cat.name}
                      <span style={{ marginLeft: 6, fontSize: 10, color: cat.type === 'Fixed' ? '#2563eb' : '#16a34a', background: cat.type === 'Fixed' ? '#dbeafe' : '#dcfce7', padding: '1px 5px', borderRadius: 999 }}>{cat.type}</span>
                    </td>
                    {visStores.map(s => (
                      <td key={s.store_code} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)' }}>
                        <input type="number" min="0" step="0.01" value={getVal(s.store_code, cat.name) || ''} placeholder="0"
                          onChange={e => setVal(s.store_code, cat.name, parseFloat(e.target.value) || 0)}
                          style={{ width: '100%', border: '1px solid var(--border)', borderRadius: 4, padding: '4px 6px', fontSize: 13, textAlign: 'right', background: 'var(--surface)' }} />
                      </td>
                    ))}
                    <td style={{ padding: '6px 14px', textAlign: 'right', fontWeight: 600, fontSize: 13, borderBottom: '1px solid var(--border)', color: rowTotal > 0 ? 'var(--text)' : 'var(--text3)' }}>{rowTotal > 0 ? fmt(rowTotal) : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr style={{ background: 'var(--accent)', fontWeight: 700 }}>
                <td style={{ padding: '10px 16px', color: 'white', fontSize: 13, position: 'sticky', left: 0, background: 'var(--accent)' }}>Total</td>
                {visStores.map(s => <td key={s.store_code} style={{ padding: '10px 10px', textAlign: 'right', color: 'white', fontSize: 13 }}>{fmt(storeTotal(s.store_code))}</td>)}
                <td style={{ padding: '10px 14px', textAlign: 'right', color: 'white', fontSize: 13 }}>{fmt(grand)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  )
}
