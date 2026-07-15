'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { TrendChart } from '@/components/TrendChart'
import { useColumnResize, ResizeHandle } from '@/lib/col-resize'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

const r2 = (n: number) => Math.round((n || 0) * 100) / 100
const shortPeriod = (p: string) => {
  const m = String(p || '').match(/^([A-Za-z]+)\s+(\d{4})$/)
  return m ? `${m[1].slice(0, 3)} '${m[2].slice(2)}` : p
}

// Default expense list (name + Fixed/Variable). The user can add ad-hoc expenses.
const DEFAULT_CATS: { name: string; type: string }[] = [
  { name: 'Rent / Lease', type: 'Variable' }, { name: 'B2B Platform Fee', type: 'Fixed' },
  { name: 'Cellsmart POS', type: 'Fixed' }, { name: 'Electric', type: 'Variable' },
  { name: 'Heat / Gas', type: 'Variable' }, { name: 'Internet', type: 'Fixed' },
  { name: 'Insurance', type: 'Fixed' }, { name: 'Advertising', type: 'Fixed' },
  { name: 'Cleaning', type: 'Fixed' }, { name: 'Garbage / Waste', type: 'Variable' },
  { name: 'Maintenance', type: 'Fixed' }, { name: 'ADT Security', type: 'Fixed' },
  { name: 'Back Office Fee', type: 'Fixed' }, { name: 'Taxes / Accounting', type: 'Fixed' },
  { name: 'Employee Salaries', type: 'Fixed' }, { name: 'Employee Commission', type: 'Fixed' },
  { name: 'Owner / Mgmt Salaries', type: 'Fixed' },
]
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const SALARY_ROW = 'Employee Salaries'
// 'June 2026' → '2026-06' for the storeops payroll month filter (no Date() to dodge the UTC off-by-one).
const MONTHS: Record<string, number> = { january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12 }
function periodToMonth(p: string): string {
  const [mon, yr] = (p || '').trim().toLowerCase().split(/\s+/)
  const m = MONTHS[mon]
  return m && yr ? `${yr}-${String(m).padStart(2, '0')}` : ''
}
const COMMISSION_ROW = 'Employee Commission'
// Matrix-upload support: map a store owner's own category labels → the canonical expense names, and the
// rows to skip (computed totals / targets, which aren't expenses).
const CAT_ALIASES: Record<string, string> = {
  rent: 'Rent / Lease', b2b: 'B2B Platform Fee', cellsmart: 'Cellsmart POS', electric: 'Electric',
  heat: 'Heat / Gas', 'heat / gas': 'Heat / Gas', water: 'Water', internet: 'Internet',
  insurane: 'Insurance', insurance: 'Insurance', advertise: 'Advertising', advertising: 'Advertising',
  cleaning: 'Cleaning', garbage: 'Garbage / Waste', 'garbage / waste': 'Garbage / Waste',
  maintance: 'Maintenance', maintenance: 'Maintenance', adt: 'ADT Security', 'adt security': 'ADT Security',
  backoffice: 'Back Office Fee', 'back office fee': 'Back Office Fee', 'taxes/lalit': 'Taxes / Accounting',
  taxes: 'Taxes / Accounting', 'taxes / accounting': 'Taxes / Accounting',
  'emp. salaries': 'Employee Salaries', 'emp salaries': 'Employee Salaries', 'employee salaries': 'Employee Salaries',
  'emp commission': 'Employee Commission', 'emp. commission': 'Employee Commission', 'employee commission': 'Employee Commission',
  'our salaries': 'Owner / Mgmt Salaries', 'owner / mgmt salaries': 'Owner / Mgmt Salaries',
}
const CAT_SKIP = new Set(['', 'store#', 'store #', 'total', 'sub total', 'subtotal', 'total exp.', 'total exp', 'min.sales target', 'min sales target', 'min. sales target'])
const canonCat = (raw: string) => { const k = (raw || '').trim().toLowerCase(); return CAT_ALIASES[k] || (raw || '').trim() }
const typeFor = (name: string) => DEFAULT_CATS.find(c => c.name === name)?.type || 'Fixed'

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
  const [autoSave, setAutoSave] = useState(false)
  const [salaryBusy, setSalaryBusy] = useState(false)
  const [salaryFrom, setSalaryFrom] = useState('')     // where the salary auto-fill came from (banner)
  const [commissionBusy, setCommissionBusy] = useState(false)
  const [commissionFrom, setCommissionFrom] = useState('')
  const [dirty, setDirty] = useState(0)                // bumps ONLY on real edits → drives auto-save
  const [expTrend, setExpTrend] = useState<any>(null)  // month-over-month total-expenses chart on top
  const cw = useColumnResize()                          // auto-fit + user-resizable columns

  function load() {
    setLoading(true)
    const mo = periodToMonth(period)
    Promise.all([
      api('/api/v1/storeops/stores').catch(() => []),
      api(`/api/v1/commcalc/expenses/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({ expenses: [] })),
      mo ? api(`/api/v1/storeops/payroll-by-store?month=${mo}&org_id=${ORG_ID}`).catch(() => ({ stores: [] })) : Promise.resolve({ stores: [] }),
      api(`/api/v1/commcalc/commission-by-store/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({ commission_by_store: {} })),
    ]).then(([st, ex, pay, comm]: any) => {
      setStores((st || []).filter((s: any) => s.is_active !== false))
      setCarriedFrom(ex?.carried_from || '')
      const map: any = {}; const extra: Record<string, string> = {}
      ;(ex.expenses || []).forEach((e: any) => {
        if (!map[e.store_code]) map[e.store_code] = {}
        map[e.store_code][e.expense_name] = parseFloat(e.amount) || 0
        if (!DEFAULT_CATS.find(c => c.name === e.expense_name)) extra[e.expense_name] = e.expense_type || 'Fixed'
      })
      // Auto-fill Employee Salaries (from worked hours) and Employee Commission (from calculated rep
      // commissions) when the month is fresh (carried from last month, or that row has nothing saved).
      // A month with a saved value is left alone — use the ↻ buttons to re-pull on demand.
      const hasSalary = (ex.expenses || []).some((e: any) => e.expense_name === SALARY_ROW && parseFloat(e.amount) > 0)
      if ((ex?.carried_from || !hasSalary) && (pay.stores || []).length) {
        ;(pay.stores || []).forEach((s: any) => { if (!map[s.store_code]) map[s.store_code] = {}; map[s.store_code][SALARY_ROW] = s.amount || 0 })
        setSalaryFrom(period)
      } else setSalaryFrom('')
      const hasComm = (ex.expenses || []).some((e: any) => e.expense_name === COMMISSION_ROW && parseFloat(e.amount) > 0)
      const cmap = comm?.commission_by_store || {}
      if ((ex?.carried_from || !hasComm) && Object.keys(cmap).length) {
        Object.entries(cmap).forEach(([sc, amt]: any) => { if (!map[sc]) map[sc] = {}; map[sc][COMMISSION_ROW] = Number(amt) || 0 })
        setCommissionFrom(period)
      } else setCommissionFrom('')
      setAmounts(map)
      setCats([...DEFAULT_CATS, ...Object.entries(extra).map(([name, type]) => ({ name, type: type as string }))])
    }).catch(console.error).finally(() => { setDirty(0); setLoading(false) })
  }
  useEffect(() => { load() }, [period])
  // Month-over-month total-expenses trend for the chart on top (cross-period, so fetched once).
  useEffect(() => { api(`/api/v1/commcalc/expenses-trend?months=6&org_id=${ORG_ID}`).then(setExpTrend).catch(() => {}) }, [])

  // Persist the auto-save preference across sessions.
  useEffect(() => { if (typeof window !== 'undefined' && localStorage.getItem('exp_autosave') === '1') setAutoSave(true) }, [])
  useEffect(() => { if (typeof window !== 'undefined') localStorage.setItem('exp_autosave', autoSave ? '1' : '0') }, [autoSave])
  // Auto-save: debounce 1.2s after a real edit (dirty>0), only when the toggle is on and not loading.
  useEffect(() => {
    if (!autoSave || loading || dirty === 0) return
    const t = setTimeout(() => { save(true) }, 1200)
    return () => clearTimeout(t)
  }, [dirty, autoSave])   // eslint-disable-line react-hooks/exhaustive-deps

  const markets = Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort()
  const visStores = stores.filter(s => (!market || s.market === market) &&
    (!storeSearch || `${s.store_code} ${s.address || ''}`.toLowerCase().includes(storeSearch.toLowerCase())))
  const getVal = (sc: string, n: string) => amounts[sc]?.[n] || 0
  const setVal = (sc: string, n: string, v: number) => { setAmounts(a => ({ ...a, [sc]: { ...a[sc], [n]: v } })); setDirty(d => d + 1) }
  const storeTotal = (sc: string) => cats.reduce((s, c) => s + getVal(sc, c.name), 0)
  const grand = visStores.reduce((s, st) => s + storeTotal(st.store_code), 0)
  // Total-expenses trend, aggregated to the current market filter (all stores when no market picked).
  const expTrendData = useMemo(() => (expTrend?.months || []).map((p: string) => {
    let v = 0
    if (!market) { const c = (expTrend?.company || []).find((x: any) => x.period === p); v = c?.total || 0 }
    else (expTrend?.stores || []).filter((s: any) => s.market === market).forEach((s: any) => { const pt = s.series.find((x: any) => x.period === p); v += pt?.total || 0 })
    return { name: shortPeriod(p), total: r2(v) }
  }), [expTrend, market])

  // RULE FOUR export — the same expense × store matrix on screen (respects the market/search filters via
  // visStores), one column per visible store + a Total column, one row per expense category.
  const expenseCols: ExportColumn[] = [
    { header: 'Expense', get: (r: any) => r.name },
    { header: 'Type', get: (r: any) => r.type },
    ...visStores.map(s => ({ header: s.store_code, money: true, get: (r: any) => getVal(s.store_code, r.name) })),
    { header: 'Total', money: true, get: (r: any) => visStores.reduce((sum, s) => sum + getVal(s.store_code, r.name), 0) },
  ]

  function addCat() {
    const name = newCat.name.trim()
    if (!name) return
    if (cats.find(c => c.name.toLowerCase() === name.toLowerCase())) { setMsg('That expense already exists.'); return }
    setCats(c => [...c, { name, type: newCat.type }]); setNewCat({ name: '', type: 'Fixed' })
  }

  async function save(silent = false) {
    setSaving(true); if (!silent) setMsg('')
    const rows: any[] = []
    stores.forEach(s => cats.forEach(c => { const amt = getVal(s.store_code, c.name); if (amt > 0) rows.push({ store_code: s.store_code, expense_name: c.name, expense_type: c.type, amount: amt }) }))
    try {
      const r = await api(`/api/v1/commcalc/expenses/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify({ rows }) })
      setMsg(silent ? `Auto-saved ✓ ${new Date().toLocaleTimeString()}` : `Saved ${r.saved} expense entries. Re-run Calculation to include in Gross Profit.`)
      setCarriedFrom(''); setSalaryFrom(''); setDirty(0)   // once saved for THIS period it's no longer carried/pending
    } catch (e: any) { setMsg((silent ? 'Auto-save' : 'Save') + ' failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  // Pull per-store worked-hours payroll (actual where clocked, else scheduled × pay rate) into the
  // Employee Salaries row. Editable afterward; auto-save persists it if the toggle is on.
  async function fillSalaries() {
    const mo = periodToMonth(period)
    if (!mo) { setMsg('Could not read the period as a month.'); return }
    setSalaryBusy(true); setMsg('')
    try {
      const r = await api(`/api/v1/storeops/payroll-by-store?month=${mo}&org_id=${ORG_ID}`)
      const map = { ...amounts }
      ;(r.stores || []).forEach((s: any) => { if (!map[s.store_code]) map[s.store_code] = {}; map[s.store_code][SALARY_ROW] = s.amount || 0 })
      setAmounts(map); setSalaryFrom(period); setDirty(d => d + 1)
      const total = (r.stores || []).reduce((a: number, s: any) => a + (s.amount || 0), 0)
      setMsg(`Filled Employee Salaries from worked hours for ${period} (${fmt(total)} across ${(r.stores || []).length} stores). Review, then Save.`)
    } catch (e: any) { setMsg('Could not load payroll: ' + (e?.message || e)) }
    setSalaryBusy(false)
  }

  // Pull the calculated rep commissions (rep_commissions.total_payout, summed per store) into the
  // Employee Commission row — the commission we pay reps, booked as a store expense. Editable after.
  async function fillCommission() {
    setCommissionBusy(true); setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/commission-by-store/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      const cmap = r?.commission_by_store || {}
      const map = { ...amounts }
      Object.entries(cmap).forEach(([sc, amt]: any) => { if (!map[sc]) map[sc] = {}; map[sc][COMMISSION_ROW] = Number(amt) || 0 })
      setAmounts(map); setCommissionFrom(period); setDirty(d => d + 1)
      const total = Object.values(cmap).reduce((a: number, v: any) => a + (Number(v) || 0), 0)
      setMsg(`Filled Employee Commission from calculated commissions for ${period} (${fmt(total)} across ${Object.keys(cmap).length} stores)`
        + (r?.unmatched_total ? ` · ${fmt(r.unmatched_total)} couldn't be matched to a store` : '') + '. Review, then Save.')
    } catch (e: any) { setMsg('Could not load commissions: ' + (e?.message || e)) }
    setCommissionBusy(false)
  }

  async function downloadTemplate() {
    const XLSX = await import('xlsx')
    const aoa: any[] = [['store_code', 'expense_name', 'expense_type', 'amount']]
    stores.forEach(s => cats.forEach(c => aoa.push([s.store_code, c.name, c.type, getVal(s.store_code, c.name) || ''])))
    const ws = XLSX.utils.aoa_to_sheet(aoa); const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Expenses'); XLSX.writeFile(wb, `expenses-${period.replace(/\s+/g, '-')}.xlsx`)
  }
  // Accepts BOTH layouts: LONG (columns store_code/expense_name/expense_type/amount) and the natural
  // MATRIX/pivot an owner keeps by hand (col A = expense names, header row = store codes across the top).
  // Category labels are mapped to the canonical names (canonCat) and computed total/target rows skipped.
  async function upload(file: File) {
    setUpBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const aoa: any[][] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, defval: '' })
      if (!aoa.length) { setMsg('That sheet is empty.'); setUpBusy(false); return }
      const hdr = (aoa[0] || []).map((h: any) => String(h).trim().toLowerCase())
      const isLong = hdr.some(h => ['store_code', 'store'].includes(h)) && hdr.some(h => ['expense_name', 'expense', 'name'].includes(h)) && hdr.some(h => ['amount', 'amt'].includes(h))
      const num = (v: any) => parseFloat(String(v ?? '').replace(/[$,\s]/g, '')) || 0
      const map = { ...amounts }; const newCats: Record<string, string> = {}; let loaded = 0
      const put = (sc: string, nm: string, amt: number) => { sc = String(sc).trim(); if (!sc || !nm) return; if (!map[sc]) map[sc] = {}; map[sc][nm] = amt; if (!cats.find(c => c.name === nm) && !newCats[nm]) newCats[nm] = typeFor(nm); loaded++ }
      if (isLong) {
        const idx = (keys: string[]) => hdr.findIndex(h => keys.includes(h))
        const iSC = idx(['store_code', 'store']), iNM = idx(['expense_name', 'expense', 'name']), iTP = idx(['expense_type', 'type']), iAM = idx(['amount', 'amt'])
        aoa.slice(1).forEach(r => {
          const nm = canonCat(String(r[iNM] || ''))
          if (iTP >= 0 && r[iTP] && !cats.find(c => c.name === nm)) newCats[nm] = String(r[iTP]).trim()
          put(String(r[iSC] || ''), nm, num(r[iAM]))
        })
      } else {
        const storeCodes = (aoa[0] || []).slice(1).map((s: any) => String(s).trim())
        aoa.slice(1).forEach(r => {
          const rawLabel = String(r[0] || '').trim()
          if (CAT_SKIP.has(rawLabel.toLowerCase())) return
          const nm = canonCat(rawLabel)
          storeCodes.forEach((sc, i) => { if (sc) put(sc, nm, num(r[i + 1])) })
        })
      }
      setAmounts(map); setDirty(d => d + 1)
      if (Object.keys(newCats).length) setCats(c => [...c, ...Object.entries(newCats).filter(([n]) => !c.find(x => x.name === n)).map(([name, type]) => ({ name, type: type as string }))])
      const matched = Object.keys(map).filter(sc => stores.find(s => s.store_code === sc)).length
      const unmatched = Object.keys(map).filter(sc => !stores.find(s => s.store_code === sc))
      setMsg(`Loaded ${loaded} values (${isLong ? 'long' : 'matrix'} format). ${matched} store(s) matched the system`
        + (unmatched.length ? `; ${unmatched.length} store code(s) not in StoreOps didn't map: ${unmatched.slice(0, 6).join(', ')}${unmatched.length > 6 ? '…' : ''}` : '')
        + '. Review, then Save All.')
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
          <button className="btn" onClick={fillSalaries} disabled={salaryBusy || loading}
            title="Fill the Employee Salaries row from worked hours (actual where clocked, else scheduled) × pay rate">
            {salaryBusy ? '…' : '↻ Salaries from hours'}</button>
          <button className="btn" onClick={fillCommission} disabled={commissionBusy || loading}
            title="Fill the Employee Commission row from calculated rep commissions (rep_commissions) per store">
            {commissionBusy ? '…' : '↻ Commission from calc'}</button>
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, color: 'var(--text2)', cursor: 'pointer' }}
            title="Save automatically ~1s after each edit">
            <input type="checkbox" checked={autoSave} onChange={e => setAutoSave(e.target.checked)} /> Auto-save
          </label>
          <button className="btn btn-primary" onClick={() => save()} disabled={saving}>{saving ? '…' : '💾 Save All'}</button>
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
        {!loading && visStores.length > 0 && (
          <>
            <span style={{ flex: 1 }} />
            <ReportExportBar title={`Store Expenses ${period}`} filename={`store_expenses_${period.replace(/\s+/g, '_')}`} columns={expenseCols} rows={cats} />
          </>
        )}
      </div>

      {expTrendData.length > 1 && !loading && (
        <div className="card" style={{ padding: '12px 12px 6px', marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4, paddingLeft: 6 }}>📉 Total expenses — last {expTrendData.length} months{market ? ` · ${market}` : ' · all stores'}</div>
          <TrendChart data={expTrendData} height={210} series={[{ key: 'total', name: 'Total expenses', color: '#dc2626', money: true }]} />
        </div>
      )}

      {carriedFrom && !loading && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: '#eef6ff', borderLeft: '4px solid var(--accent)', fontSize: 13 }}>
          📋 <b>Carried forward from {carriedFrom}</b> — no expenses entered for {period} yet, so last month's are pre-filled below.
          Review and <b>Save All</b> to keep them for {period} (they'll carry to next month too).
        </div>
      )}
      {salaryFrom && !loading && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: '#f0fdf4', borderLeft: '4px solid #16a34a', fontSize: 13 }}>
          💵 <b>Employee Salaries auto-filled from worked hours</b> for {period} (actual hours where clocked, otherwise scheduled, × each employee's pay rate).
          Edit any store's figure to override, or hit <b>↻ Salaries from hours</b> to re-pull. Nothing is saved until you <b>Save All</b>.
        </div>
      )}
      {commissionFrom && !loading && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: '#fef9f0', borderLeft: '4px solid #f59e0b', fontSize: 13 }}>
          🧮 <b>Employee Commission auto-filled from calculated commissions</b> for {period} (rep_commissions total payout, summed per store).
          Edit to override or hit <b>↻ Commission from calc</b> to re-pull. Save All to keep.
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : visStores.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No stores match. (Add stores in StoreOps Admin.)</div>
      ) : (
        <div style={{ overflowX: 'auto', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
          {cw.dirty && <div style={{ padding: '4px 10px', fontSize: 11, color: 'var(--text3)' }}><button className="btn" style={{ padding: '2px 8px', fontSize: 11 }} onClick={cw.resetAll}>↺ Reset column widths</button> <span>drag a column edge to resize · double-click to auto-fit</span></div>}
          <table style={{ borderCollapse: 'collapse', tableLayout: 'auto' }}>
            <colgroup>
              <col style={{ width: cw.width('expense') }} />
              {visStores.map(s => <col key={s.store_code} style={{ width: cw.width(s.store_code) }} />)}
              <col style={{ width: cw.width('total') }} />
            </colgroup>
            <thead>
              <tr style={{ background: 'var(--accent)' }}>
                <th style={{ padding: '10px 16px', color: 'white', fontSize: 12, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--accent)', whiteSpace: 'nowrap' }}>Expense<ResizeHandle onDown={e => cw.start('expense', e)} onReset={() => cw.reset('expense')} /></th>
                {visStores.map(s => (
                  <th key={s.store_code} style={{ padding: '8px 10px', color: 'white', fontSize: 11, textAlign: 'right', whiteSpace: 'nowrap', position: 'relative' }}>
                    <div style={{ fontWeight: 700 }}>{s.store_code}</div>
                    <div style={{ fontWeight: 400, opacity: 0.7, fontSize: 10 }}>{(s.address || '').substring(0, 18)}</div>
                    <ResizeHandle onDown={e => cw.start(s.store_code, e)} onReset={() => cw.reset(s.store_code)} />
                  </th>
                ))}
                <th style={{ padding: '10px 14px', color: 'white', fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap', position: 'relative' }}>Total<ResizeHandle onDown={e => cw.start('total', e)} onReset={() => cw.reset('total')} /></th>
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
