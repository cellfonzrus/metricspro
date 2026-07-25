'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { currentPeriodFromSettingsResponse, monthRange, rangeLabel, stepPeriod, type PayPeriodSettings } from '../lib/pay-period'

const chip: React.CSSProperties = { padding: '5px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', cursor: 'pointer' }

export default function StoreOpsReportsPage() {
  // RULE FIVE (§3d) core filters: period is now an arbitrary date RANGE (owner 2026-07-25: "need time
  // range to create payroll for the employees universally" — biweekly/semimonthly/custom periods, not
  // just calendar months) plus store(s)/market(s)/rep(s), all driving the page AND its exports.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [rangeReady, setRangeReady] = useState(false)   // true once a default range has been resolved
  const [ppSettings, setPpSettings] = useState<PayPeriodSettings | null>(null)
  const [rows, setRows] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<'employee' | 'store'>('employee')

  // Owner directive 2026-07-25: default the range to the tenant's OWN pay period (migration 085
  // config, resolved server-side — never re-derived here, see ../lib/pay-period.ts). Degrades to the
  // current calendar month if the endpoint/migration isn't reachable yet.
  useEffect(() => {
    let cancelled = false
    api('/api/v1/core/tenant-settings').then((r: any) => {
      if (cancelled) return
      const cur = currentPeriodFromSettingsResponse(r)
      if (cur) {
        setPpSettings(cur.settings)
        setFilt(f => ({ ...f, period: cur.period.start, periodTo: cur.period.end }))
      } else {
        const mr = monthRange(0)
        setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end }))
      }
    }).catch(() => {
      const mr = monthRange(0)
      if (!cancelled) setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end }))
    }).finally(() => { if (!cancelled) setRangeReady(true) })
    return () => { cancelled = true }
  }, [])

  function load(start: string, end: string) {
    setLoading(true)
    Promise.all([
      api(`/api/v1/storeops/payroll?start=${start}&end=${end}`).catch(() => []),
      api('/api/v1/storeops/stores').catch(() => []),
    ]).then(([p, s]) => { setRows(p || []); setStores(s || []) })
      .catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => {
    if (!rangeReady || !filt.period || !filt.periodTo) return
    load(filt.period, filt.periodTo)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeReady, filt.period, filt.periodTo])

  // store_code -> market
  const mktOf: Record<string, string> = {}
  stores.forEach(s => { if (s.store_code) mktOf[s.store_code] = s.market || '' })
  const withMkt = rows.map(r => ({ ...r, market: mktOf[r.store] || '' }))

  const storeOptions = useMemo(() => stores
    .filter(s => s.store_code)
    .map(s => ({ id: s.store_code, label: s.store_code, sublabel: s.address || s.market || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [stores])
  const marketOptions = useMemo(() =>
    Array.from(new Set(stores.map(s => s.market).filter(Boolean) as string[])).sort(), [stores])
  const repOptions = useMemo(() => optionsFromRows(withMkt, { rep: r => r.name }).reps, [withMkt])

  // Filters narrow the range-loaded rows — tiles, both views, and the export all read from this SAME
  // filtered set ("what you see is what exports", RULE FIVE/FOUR).
  const visibleRows = useMemo(() => filterRows(withMkt, filt, {
    store: r => r.store, market: r => r.market, rep: r => r.name,
  }), [withMkt, filt])

  // per-store rollup (ReportShell handles any further ad hoc filtering on top)
  const byStore = Object.values(visibleRows.reduce((acc: any, r) => {
    const k = r.store || '—'
    if (!acc[k]) acc[k] = { store: k, market: r.market, employees: 0, scheduled_hours: 0, actual_hours: 0, scheduled_pay: 0, actual_pay: 0, shifts: 0 }
    acc[k].employees += 1
    acc[k].scheduled_hours += r.scheduled_hours || 0
    acc[k].actual_hours += r.actual_hours || 0
    acc[k].scheduled_pay += r.scheduled_pay || 0
    acc[k].actual_pay += r.actual_pay || 0
    acc[k].shifts += r.shifts || 0
    return acc
  }, {})).sort((a: any, b: any) => b.actual_pay - a.actual_pay)

  const tot = visibleRows.reduce((t, r) => ({
    sh: t.sh + (r.scheduled_hours || 0), ah: t.ah + (r.actual_hours || 0),
    sp: t.sp + (r.scheduled_pay || 0), ap: t.ap + (r.actual_pay || 0),
  }), { sh: 0, ah: 0, sp: 0, ap: 0 })

  const periodName = rangeLabel(filt.period || '', filt.periodTo || '')

  const empCols: ExportColumn[] = [
    { header: 'Employee', get: r => r.name, role: 'rep' },
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market },
    { header: 'Pay $/hr', get: r => r.pay_rate, money: true },
    { header: 'Sched Hrs', get: r => Math.round((r.scheduled_hours || 0) * 10) / 10, align: 'right' },
    { header: 'Actual Hrs', get: r => Math.round((r.actual_hours || 0) * 10) / 10, align: 'right' },
    { header: 'Hrs Var', get: r => Math.round(((r.actual_hours || 0) - (r.scheduled_hours || 0)) * 10) / 10, align: 'right' },
    { header: 'Sched Pay', get: r => r.scheduled_pay, money: true },
    { header: 'Actual Pay', get: r => r.actual_pay, money: true },
    { header: 'Shifts', get: r => r.shifts, align: 'right' },
  ]
  const storeCols: ExportColumn[] = [
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market },
    { header: 'Employees', get: r => r.employees, align: 'right' },
    { header: 'Sched Hrs', get: r => Math.round(r.scheduled_hours * 10) / 10, align: 'right' },
    { header: 'Actual Hrs', get: r => Math.round(r.actual_hours * 10) / 10, align: 'right' },
    { header: 'Sched Pay', get: r => r.scheduled_pay, money: true },
    { header: 'Actual Pay', get: r => r.actual_pay, money: true },
  ]

  const Tile = ({ label, value }: { label: string; value: string }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 130 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  )

  function setRange(start: string, end: string) {
    setFilt(f => ({ ...f, period: start, periodTo: end }))
  }
  // StandardFilterBar's generic "Clear filters" also blanks period/periodTo in range mode — fine for
  // most reports, but this one always needs SOME range to fetch data. Re-pin the range to whatever's
  // currently active instead of letting it go blank (store/market/rep filters still clear normally).
  function onFilterChange(v: StandardFilterValue) {
    setFilt(v.period || v.periodTo ? v : { ...v, period: filt.period, periodTo: filt.periodTo })
  }
  function step(dir: 1 | -1) {
    if (!filt.period || !filt.periodTo) return
    const next = stepPeriod({ start: filt.period, end: filt.periodTo }, ppSettings, dir)
    setRange(next.start, next.end)
  }
  function useThisPayPeriod() {
    api('/api/v1/core/tenant-settings').then((r: any) => {
      const cur = currentPeriodFromSettingsResponse(r)
      if (cur) { setPpSettings(cur.settings); setRange(cur.period.start, cur.period.end) }
    }).catch(() => {})
  }

  // Export filename reflects the ACTIVE range (RULE FOUR: what you see is what exports).
  const filename = `storeops-payroll-${filt.period || 'range'}_to_${filt.periodTo || 'range'}`

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📋 StoreOps Reports — Hours & Payroll</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Scheduled vs actual hours and pay, per employee and per store. {periodName}.</p>
      </div>

      <StandardFilterBar
        value={filt} onChange={onFilterChange}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        right={
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <button style={chip} onClick={() => step(-1)} title="Previous pay period">‹ Prev period</button>
            <button style={chip} onClick={() => step(1)} title="Next pay period">Next period ›</button>
            <button style={chip} onClick={useThisPayPeriod}>This pay period</button>
            <button style={chip} onClick={() => { const r = monthRange(0); setRange(r.start, r.end) }}>This month</button>
            <button style={chip} onClick={() => { const r = monthRange(-1); setRange(r.start, r.end) }}>Last month</button>
          </div>
        }
      />

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
          {(['employee', 'store'] as const).map(v => (
            <button key={v} onClick={() => setView(v)} className="btn" style={{ background: view === v ? 'white' : 'transparent', color: view === v ? 'var(--accent)' : 'var(--text2)', fontSize: 13, boxShadow: view === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none' }}>
              {v === 'employee' ? '👤 By Employee' : '🏪 By Store'}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Scheduled Hrs" value={tot.sh.toFixed(1)} />
        <Tile label="Actual Hrs" value={tot.ah.toFixed(1)} />
        <Tile label="Scheduled Pay" value={fmt(tot.sp)} />
        <Tile label="Actual Pay" value={fmt(tot.ap)} />
        <Tile label="Employees" value={String(visibleRows.length)} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : visibleRows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No hours for {periodName}. Payroll is computed from entered shifts and clock-in/out activity — add shifts in the Schedule, or have employees clock in from the /portal, for this period.
        </div>
      ) : (
        <ReportShell
          title={`StoreOps Hours & Payroll — ${periodName}`}
          subtitle={view === 'employee' ? 'By employee' : 'By store'}
          filename={filename}
          columns={view === 'employee' ? empCols : storeCols}
          rows={view === 'employee' ? visibleRows : (byStore as any[])}
        />
      )}
    </div>
  )
}
