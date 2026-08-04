'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'

// Expenses report (RULE FOUR/FIVE) — every categorized Daily-Closing expense line (mig 506), by
// category/store/period/rep, with the standard universal filter bar and full export set via
// ReportShell. Reads GET /closing/expenses over a date range, then narrows client-side (what-you-
// see-is-what-exports) the same way every other RULE-FIVE report on this platform does.
function monthAgo(): string {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1)
  return d.toISOString().slice(0, 10)
}

export default function ExpensesReportPage() {
  const [dateFrom, setDateFrom] = useState(monthAgo())
  const [dateTo, setDateTo] = useState(() => localToday())
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [rows, setRows] = useState<any[]>([])
  const [cats, setCats] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  useEffect(() => { api('/api/v1/closing/expense-categories').then((d: any) => setCats(d?.categories || [])).catch(() => {}) }, [])
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): expense lines carry only
  // `store_code`, no market column at all — joined here from the same org-scoped `/closing/stores`
  // roster this module already calls elsewhere, not a new kind of read.
  const [pStores, setPStores] = useState<any[]>([])
  useEffect(() => { api('/api/v1/closing/stores').then((s: any) => setPStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {}) }, [])
  const marketByCode = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of pStores) if (s.store_code && s.market) m[s.store_code] = s.market
    return m
  }, [pStores])
  const storesForCascade: StoreOpt[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [pStores])

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const qs = [`date_from=${dateFrom}`, `date_to=${dateTo}`, status && `status=${status}`, category && `category_id=${category}`]
      .filter(Boolean).join('&')
    api(`/api/v1/closing/expenses?${qs}`)
      .then((d: any) => setRows(d?.rows || []))
      .catch((e: any) => { setErr(e?.message || String(e)); setRows([]) })
      .finally(() => setLoading(false))
  }, [dateFrom, dateTo, status, category])
  useEffect(() => { load() }, [load])

  // Market joined onto each row (see marketByCode above) so the shared `filterRows` market-matching
  // (used by every other RULE FIVE surface) works here too, not just the store cascade widget.
  const joinedRows = useMemo(() => rows.map(r => ({ ...r, market: marketByCode[r.store_code] || null })), [rows, marketByCode])
  const acc = useMemo(() => ({
    store: (r: any) => r.store_code, market: (r: any) => r.market, rep: (r: any) => r.employee_name, date: (r: any) => r.close_date,
  }), [])
  const opts = useMemo(() => optionsFromRows(joinedRows, acc), [joinedRows, acc])
  const filtered = useMemo(() => filterRows(joinedRows, filt, acc), [joinedRows, filt, acc])

  const columns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: any) => r.close_date },
    { header: 'Store', field: 'store_code', role: 'store', get: (r: any) => r.store_code },
    { header: 'Category', field: 'category_name', get: (r: any) => r.category_name },
    { header: 'Kind', field: 'category_kind', get: (r: any) => r.category_kind },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (r: any) => r.employee_name },
    { header: 'Amount', field: 'amount', money: true, get: (r: any) => r.amount },
    { header: 'Description', field: 'description', get: (r: any) => r.description },
    { header: 'Status', field: 'status', get: (r: any) => r.status },
    { header: 'Approved by', field: 'approved_by', get: (r: any) => r.approved_by },
    { header: 'Paid', field: 'paid', get: (r: any) => r.paid ? 'Yes' : 'No' },
  ], [])

  const total = filtered.reduce((a, r) => a + (Number(r.amount) || 0), 0)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Expenses Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Every categorized Daily-Closing expense line — by category, store, period, and rep.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}>
          <input type="date" className="select" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
          →<input type="date" className="select" value={dateTo} onChange={e => setDateTo(e.target.value)} />
        </span>
        <select className="select" value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        <select className="select" value={category} onChange={e => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {cats.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{filtered.length} line(s) · {fmt(total)}</span>
      </div>

      {/* Market/store render via the shared cascade-checkbox <MarketStorePicker> (OWNER DIRECTIVE
          2026-08-04) — this page previously had no market dimension at all (`markets: false`); the
          roster join above now supplies one. */}
      <StandardFilterBar value={filt} onChange={setFilt} periodMode="none"
        show={{ period: false, stores: false, markets: false, reps: true }}
        storeOptions={opts.stores} repOptions={opts.reps}
        right={
          <MarketStorePicker
            stores={storesForCascade}
            selectedMarkets={filt.markets} onMarketsChange={ids => setFilt(f => ({ ...f, markets: ids }))}
            selectedStores={filt.stores} onStoresChange={ids => setFilt(f => ({ ...f, stores: ids }))}
          />
        } />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : (
        <ReportShell title="Expenses" subtitle={`${dateFrom} → ${dateTo}`}
          filename={`closing-expenses_${dateFrom}_${dateTo}`} columns={columns} rows={filtered} />
      )}
    </div>
  )
}
