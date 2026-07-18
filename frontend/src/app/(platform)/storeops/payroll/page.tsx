'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, parseLocalDate } from '@/lib/client'
import ReportShell from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import PtoAccrualPanel from './PtoAccrualPanel'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

interface PayrollRow {
  employee_id: string; name: string; store: string; pay_rate: number
  scheduled_hours: number; actual_hours: number; shifts: number
  scheduled_pay: number; actual_pay: number
}
interface StoreRow { store_code: string; address?: string; market?: string }

// Current month 'YYYY-MM', local-safe (not a stale hardcoded month — this used to default to
// '2026-04' regardless of today, silently landing every tenant, Boost included, on the wrong month).
function currentMonth() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export default function PayrollPage() {
  const [month, setMonth] = useState(() => currentMonth())
  const [rows, setRows] = useState<PayrollRow[]>([])
  const [loading, setLoading] = useState(true)
  const [stores, setStores] = useState<StoreRow[]>([])
  const [empEmail, setEmpEmail] = useState<Record<string, string>>({})
  // RULE FIVE (§3d): store(s)/rep(s) multi-select, options org-scoped off the loaded roster (pick-don't-
  // type, §3b). Market has no column on this row; derived below via the store→market map.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  function load() {
    setLoading(true)
    api(`/api/v1/storeops/payroll?month=${month}`)
      .then(setRows).catch(console.error).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [month])
  useEffect(() => {
    api('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    api('/api/v1/storeops/employees').then((r: any) => {
      const m: Record<string, string> = {}
      for (const e of (Array.isArray(r) ? r : [])) if (e.employee_id) m[e.employee_id] = e.email || ''
      setEmpEmail(m)
    }).catch(() => {})
  }, [])

  const storeMarket = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of stores) if (s.store_code) m[s.store_code] = s.market || ''
    return m
  }, [stores])
  const storeOptions = useMemo(() => stores
    .filter(s => s.store_code)
    .map(s => ({ id: s.store_code, label: s.store_code, sublabel: s.address || s.market || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [stores])
  const marketOptions = useMemo(() =>
    Array.from(new Set(stores.map(s => s.market).filter(Boolean) as string[])).sort(), [stores])
  const repOptions = useMemo(() => optionsFromRows(rows, {
    rep: r => r.name, repEmail: r => empEmail[r.employee_id],
  }).reps, [rows, empEmail])

  // Filters narrow the shift-derived per-employee rows (store attribution follows the actual shift
  // worked, not a static home store — see the GET /payroll backend fix). Summary tiles + export re-sum
  // from this FILTERED set, never the full load ("what you see is what exports"). With no filter active,
  // filterRows returns every row unchanged → totals stay byte-identical to before this change.
  const visibleRows = useMemo(() => filterRows(rows, filt, {
    store: r => r.store, market: r => storeMarket[r.store] || '', rep: r => r.name,
  }), [rows, filt, storeMarket])

  const totalScheduled = visibleRows.reduce((s, r) => s + r.scheduled_hours, 0)
  const totalActual    = visibleRows.reduce((s, r) => s + r.actual_hours, 0)
  const totalPayScheduled = visibleRows.reduce((s, r) => s + r.scheduled_pay, 0)
  const totalPayActual    = visibleRows.reduce((s, r) => s + r.actual_pay, 0)

  const monthName = parseLocalDate(month + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  // RULE FOUR (§3c): full export set (Excel/PDF/Print/Send) via ReportShell — replaces the old
  // CSV-only button. No PII here (name/store/pay/hours), nothing to mask.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'name', role: 'rep', get: r => r.name },
    { header: 'Store', field: 'store', role: 'store', get: r => r.store },
    { header: 'Pay Rate', field: 'pay_rate', get: r => `$${Number(r.pay_rate).toFixed(2)}/hr` },
    { header: 'Shifts', field: 'shifts', type: 'number', get: r => r.shifts },
    { header: 'Scheduled Hrs', field: 'scheduled_hours', type: 'number', get: r => r.scheduled_hours.toFixed(1) },
    { header: 'Actual Hrs', field: 'actual_hours', type: 'number', get: r => r.actual_hours.toFixed(1) },
    { header: 'Variance', field: 'variance', type: 'number', get: r => (r.actual_hours - r.scheduled_hours).toFixed(1) },
    { header: 'Scheduled Pay', field: 'scheduled_pay', money: true, get: r => r.scheduled_pay },
    { header: 'Actual Pay', field: 'actual_pay', money: true, get: r => r.actual_pay },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Payroll Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {monthName} · {visibleRows.length} employees{visibleRows.length !== rows.length ? ` (of ${rows.length})` : ''}
          </p>
        </div>
        <input className="input" type="month" value={month} onChange={e => setMonth(e.target.value)} style={{ width: 160 }} />
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt}
        show={{ period: false }}
        periodMode="none"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        storeLabel="Stores…" repLabel="Employees…"
      />

      {/* Summary */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { label: 'Scheduled Hours', val: totalScheduled.toFixed(1), unit: 'hrs', icon: '📅' },
          { label: 'Actual Hours', val: totalActual.toFixed(1), unit: 'hrs', icon: '⏱️' },
          { label: 'Scheduled Pay', val: fmt(totalPayScheduled), icon: '💵' },
          { label: 'Actual Pay', val: fmt(totalPayActual), icon: '💰' },
        ].map(({ label, val, unit, icon }) => (
          <div key={label} className="card">
            <div style={{ fontSize: 20 }}>{icon}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--accent)', marginTop: 8 }}>
              {val}{unit && <span style={{ fontSize: 13, color: 'var(--text3)', marginLeft: 4 }}>{unit}</span>}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>

      <PtoAccrualPanel month={month} />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <ReportShell title="Payroll Report" subtitle={monthName} filename={`payroll-${month}`} columns={cols} rows={visibleRows} />
      )}
    </div>
  )
}
