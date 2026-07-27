'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, parseLocalDate } from '@/lib/client'
import ReportShell from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import PtoAccrualPanel from './PtoAccrualPanel'
import PayrollChargebacksPanel from './PayrollChargebacksPanel'
import ActualHoursDrilldown from './ActualHoursDrilldown'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { currentPeriodFromSettingsResponse, isFullCalendarMonth, monthRange, rangeLabel, stepPeriod, type PayPeriodSettings } from '../lib/pay-period'
import { PAY_BASIS_LABEL, type PayBasis } from '../lib/pay-basis'

interface PayrollRow {
  employee_id: string; name: string; store: string; pay_rate: number
  scheduled_hours: number; actual_hours: number; shifts: number
  scheduled_pay: number; actual_pay: number
  // Salary pay-basis (owner directive 2026-07-27, migrations 416/417) — present only for a salaried
  // employee (pay_basis != 'hourly'); absent/undefined for every hourly row, pre-migration tenant, etc.
  pay_basis?: PayBasis; pay_amount?: number; salary_period_pay?: number; salary_prorated?: boolean
  salary_note?: string
}
interface StoreRow { store_code: string; address?: string; market?: string }

const chip: React.CSSProperties = { padding: '5px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', cursor: 'pointer' }

export default function PayrollPage() {
  // RULE FIVE (§3d) period filter: an arbitrary date RANGE (owner 2026-07-25: "need time range to
  // create payroll for the employees universally" — biweekly/semimonthly/custom periods), defaulted
  // to the tenant's OWN configured pay period (owner follow-up: "the date range by default should be
  // as defined in the payroll time period" — migration 085 config, resolved server-side, never
  // re-derived here; see ../lib/pay-period.ts). Falls back to the current calendar month pre-migration.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [rangeReady, setRangeReady] = useState(false)
  const [ppSettings, setPpSettings] = useState<PayPeriodSettings | null>(null)
  const [rows, setRows] = useState<PayrollRow[]>([])
  const [loading, setLoading] = useState(true)
  const [stores, setStores] = useState<StoreRow[]>([])
  const [empEmail, setEmpEmail] = useState<Record<string, string>>({})
  // 2026-07-22, owner-directed, MONEY-ADJACENT: POSTED payroll chargebacks per employee this period
  // (from PayrollChargebacksPanel — additive display only, never mutates /payroll's own numbers).
  const [chargebacks, setChargebacks] = useState<Record<string, number>>({})
  // 2026-07-27 owner directive — Deliverable 2 (drill-down) + 3 (over-limit highlighting, DISPLAY
  // ONLY — never touches pay) + 4 (manual-edit marker). `drill` opens the day-by-day modal for a
  // clicked row; `overAlone`/`overWeeks` come from GET /payroll/over-hours (reuses the EXISTING
  // storeops.hours_budget config, RULE TWO — no new setting); `editedEmpIds` comes from
  // GET /payroll-change-log (migration 414) so an employee with a manual hours correction this
  // period gets a ✎ marker + a link into the log.
  const [drill, setDrill] = useState<{ employee_id: string; name?: string } | null>(null)
  const [overAlone, setOverAlone] = useState<Set<string>>(new Set())
  const [overStores, setOverStores] = useState<Set<string>>(new Set())
  const [editedEmpIds, setEditedEmpIds] = useState<Set<string>>(new Set())

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
    api(`/api/v1/storeops/payroll?start=${start}&end=${end}`)
      .then(setRows).catch(console.error).finally(() => setLoading(false))
    // Deliverable 3: over-limit highlighting (display only). Degrades to empty sets on any error —
    // a tenant with no storeops.hours_budget configured, or pre-any-migration, just shows no highlight.
    api(`/api/v1/storeops/payroll/over-hours?start=${start}&end=${end}`).then((r: any) => {
      const alone = new Set<string>(), stores = new Set<string>()
      for (const wk of (r?.weeks || [])) {
        if (wk.over) stores.add(wk.store_code)
        for (const e of (wk.employees || [])) if (e.over_alone) alone.add(e.employee_id)
      }
      setOverAlone(alone); setOverStores(stores)
    }).catch(() => { setOverAlone(new Set()); setOverStores(new Set()) })
    // Deliverable 4: manual-edit marker. Degrades to an empty set pre-migration-414 (available:false).
    api(`/api/v1/storeops/payroll-change-log?start=${start}&end=${end}`).then((r: any) => {
      setEditedEmpIds(new Set<string>((r?.items || []).map((it: any) => it.employee_id).filter(Boolean)))
    }).catch(() => setEditedEmpIds(new Set()))
  }

  useEffect(() => {
    if (!rangeReady || !filt.period || !filt.periodTo) return
    load(filt.period, filt.periodTo)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeReady, filt.period, filt.periodTo])

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

  const periodName = rangeLabel(filt.period || '', filt.periodTo || '')

  // Chargebacks + PTO accrual are MONEY (posted deductions / accrued balances) and are keyed by
  // calendar MONTH on the backend (GET /payroll-chargebacks?month=, the PTO ledger) — owner guidance
  // 2026-07-25: don't invent proration/double-counting risk by trying to make them span-aware for an
  // arbitrary range. They stay keyed to the calendar month containing the range's START date, with an
  // explicit note below so a biweekly period straddling two months is never silently mis-attributed.
  const panelMonth = (filt.period || '').slice(0, 7) || monthRange(0).start.slice(0, 7)
  const panelMonthName = panelMonth
    ? parseLocalDate(panelMonth + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
    : ''

  // RULE FOUR (§3c): full export set (Excel/PDF/Print/Send) via ReportShell — replaces the old
  // CSV-only button. No PII here (name/store/pay/hours), nothing to mask.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'name', role: 'rep', get: r => r.name },
    { header: 'Store', field: 'store', role: 'store', get: r => r.store },
    // Salary pay-basis (2026-07-27): a salaried rep's "Pay Rate" isn't an hourly figure — show the
    // basis + period-converted amount instead, so the export never implies a $/hr number that isn't real.
    { header: 'Pay Rate', field: 'pay_rate', get: r => r.pay_basis && r.pay_basis !== 'hourly'
        ? `${PAY_BASIS_LABEL[r.pay_basis as PayBasis]} — $${(r.salary_period_pay ?? 0).toFixed(2)}/period`
        : `$${Number(r.pay_rate).toFixed(2)}/hr` },
    { header: 'Shifts', field: 'shifts', type: 'number', get: r => r.shifts },
    { header: 'Scheduled Hrs', field: 'scheduled_hours', type: 'number', get: r => r.scheduled_hours.toFixed(1) },
    // ONE ROW PER REP (2026-07-27): /payroll now returns a single, merged row per employee — this
    // column just displays it.
    { header: 'Actual Hrs', field: 'actual_hours', type: 'number', get: r => r.actual_hours.toFixed(1) },
    // Net = actual − scheduled, SIGNED (owner directive 2026-07-27). ▲/▼ is the "highlighted" cue
    // requested — text-based so it renders identically on screen AND in every export (RULE FOUR).
    { header: 'Net (Actual − Sched)', field: 'variance', type: 'number', get: r => {
        const v = r.actual_hours - r.scheduled_hours
        const sign = v > 0.05 ? '▲ +' : v < -0.05 ? '▼ ' : '— '
        return `${sign}${v.toFixed(1)}`
      } },
    // Flags (2026-07-27, Deliverables 3+4): ✎ = this employee has a logged manual hours/punch
    // correction in the active range (storeops.payroll_change_log — see the Payroll Change Log page
    // for the full before/after); ⚠ = their store-week is over its configured hours_budget limit
    // (DISPLAY ONLY — never changes pay). Kept as its OWN column (not appended to Actual Hrs) so the
    // hour columns stay clean numeric-looking values in the export.
    { header: 'Flags', field: 'flags', get: r => {
        const f: string[] = []
        if (editedEmpIds.has(r.employee_id)) f.push('✎ edited')
        if (overAlone.has(r.employee_id) || overStores.has(r.store)) f.push('⚠ over weekly limit')
        // Gate-1 N6 (2026-07-27): a merged row with scheduled hours but $0 scheduled pay means the
        // shift-derived sub-component was computed at a $0 rate (the id-mismatch's shift bucket
        // never matched emp_map before the merge) — visible inconsistency the owner will ask about,
        // so surface it rather than let it look like a silent data error.
        if (!r.pay_basis && (r.scheduled_hours || 0) > 0.05 && !(r.scheduled_pay > 0) && (r.pay_rate || 0) > 0) f.push('ℹ sched pay incl. a $0-rate portion')
        if (r.salary_prorated) f.push('◔ prorated period')
        if (r.salary_note) f.push('⚠ ' + r.salary_note)
        return f.join(' · ')
      } },
    { header: 'Scheduled Pay', field: 'scheduled_pay', money: true, get: r => r.scheduled_pay },
    { header: 'Actual Pay', field: 'actual_pay', money: true, get: r => r.actual_pay },
    // Additive-only (2026-07-22): a POSTED payroll chargeback shown as a visible deduction + the
    // resulting net — pending/waived chargebacks never deduct, so this is $0 for the common case
    // (no chargebacks this period) and byte-identical to Actual Pay when nothing's posted.
    { header: 'Chargebacks', field: 'chargeback_deduction', money: true, get: r => chargebacks[r.employee_id] || 0 },
    { header: 'Net Pay', field: 'net_pay', money: true, get: r => Math.max(0, r.actual_pay - (chargebacks[r.employee_id] || 0)) },
  ]

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
  const filename = `payroll-${filt.period || 'range'}_to_${filt.periodTo || 'range'}`

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Payroll Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {periodName} · {visibleRows.length} employees{visibleRows.length !== rows.length ? ` (of ${rows.length})` : ''}
            {(overAlone.size > 0 || overStores.size > 0) && (
              <span style={{ color: '#dc2626', fontWeight: 600 }}> · ⚠ {overStores.size || overAlone.size} store-week(s) over their configured weekly hours limit (highlighted below)</span>
            )}
          </p>
        </div>
        {/* rbac.ts (SHARED — no sidebar entry yet, see docs/handoffs/people.md NEEDS CORE) */}
        <a href="/storeops/payroll-change-log" className="btn" style={{ fontSize: 13 }}>📜 Payroll Change Log</a>
      </div>

      <StandardFilterBar
        value={filt} onChange={onFilterChange}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        storeLabel="Stores…" repLabel="Employees…"
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

      {/* 2026-07-25 owner guidance: chargebacks/PTO stay calendar-month-keyed, not range-prorated —
          say so plainly whenever the selected range ISN'T EXACTLY that one calendar month (Gate-1
          MINOR-A1, 2026-07-26: this must fire for a range NARROWER than a month too, not only one
          that straddles two — the DEFAULT weekly period sits entirely inside one month, yet the
          panels below still cover that WHOLE month and Net Pay deducts a full month's posted
          chargebacks from just one week's pay; reuses the same isFullCalendarMonth test rangeLabel
          uses, so "is this exactly a month" is decided in exactly one place). */}
      {!isFullCalendarMonth(filt.period || '', filt.periodTo || '') ? (
        <div className="card" style={{ marginBottom: 12, padding: '10px 14px', fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)' }}>
          ℹ️ Chargebacks and PTO accrual below are tracked by calendar month ({panelMonthName}) — they
          don't yet prorate across a custom pay-period range, so they cover the WHOLE month, not just
          your selected period ({periodName}).
        </div>
      ) : null}

      <PtoAccrualPanel month={panelMonth} />
      <PayrollChargebacksPanel month={panelMonth} onDeductions={setChargebacks} />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        // Genuinely-missing data (no shifts AND no clock punches this period) vs. a silent blank —
        // /payroll now also flows in clock-in/out activity that has no matching shift row, so an
        // empty state here means neither source has anything for this period yet.
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No shifts or clock-ins recorded for {periodName}. Add shifts in the Schedule, or have employees
          clock in from the /portal, to populate payroll for this period.
        </div>
      ) : (
        <ReportShell
          title="Payroll Report" subtitle={`${periodName} · click a row for the day-by-day actual-hours breakdown`}
          filename={filename} columns={cols} rows={visibleRows}
          onRowClick={r => r.employee_id && setDrill({ employee_id: r.employee_id, name: r.name })}
          rowStyle={r => overAlone.has(r.employee_id) || overStores.has(r.store)
            ? { background: 'rgba(220,38,38,0.07)' } : undefined}
        />
      )}

      {drill && (
        <ActualHoursDrilldown employeeId={drill.employee_id} name={drill.name}
          start={filt.period || ''} end={filt.periodTo || ''} onClose={() => setDrill(null)} />
      )}
    </div>
  )
}
