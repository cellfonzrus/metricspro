'use client'
// Payroll Change Log (owner directive 2026-07-27, Deliverable 4): "track and highlight any changes
// done by the DM to fix the hours manually in a separate log in the payroll module to see what
// changes have been made." Every write path that alters punches/hours (PATCH /shifts/{id}, manager
// clock-in override, manual hours add/delete, the force-clockout sweep) appends an append-only row
// to storeops.payroll_change_log (migration 414) — this page is its dedicated, filterable, exportable
// view. RULE FIVE (§3d) standard filter bar (period/stores/reps) + RULE FOUR (§3c) full export set
// via ReportShell.
import { useState, useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/client'
import type { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { currentPeriodFromSettingsResponse, monthRange, rangeLabel, type PayPeriodSettings } from '../lib/pay-period'

const ENTRY_POINT_LABEL: Record<string, string> = {
  shift_edit: 'Shift edit (Schedule)',
  shift_swap: 'Shift swap approval',
  timeclock_override: 'Manager clock-in override',
  manual_hours_add: 'Manual hours added',
  manual_hours_delete: 'Manual hours removed',
  force_clockout_manual: 'Force clock-out (DM "run now")',
  force_clockout_cron: 'Force clock-out (automatic sweep)',
  clock_out_stale_auto: 'Auto clock-out (stale punch, self-service)',
  lunch_deduction_config: 'Lunch-deduction setting changed',
}
const FIELD_LABEL: Record<string, string> = {
  scheduled_hours: 'Scheduled hours', actual_hours: 'Actual hours', start_time: 'Start time',
  end_time: 'End time', store_code: 'Store', shift_date: 'Shift date', status: 'Status',
  employee_id: 'Employee', clock_in: 'Clock in', clock_out: 'Clock out', shift_added: 'Shift added',
  manual_hours: 'Manual hours', lunch_deduction_enabled: 'Lunch deduction enabled',
  lunch_deduction_minutes: 'Lunch deduction minutes', lunch_deduction_min_shift_hours: 'Lunch min shift hours',
}

const chip: React.CSSProperties = { padding: '5px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', cursor: 'pointer' }

export default function PayrollChangeLogPage() {
  const router = useRouter()
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [rangeReady, setRangeReady] = useState(false)
  const [ppSettings, setPpSettings] = useState<PayPeriodSettings | null>(null)
  const [items, setItems] = useState<any[]>([])
  const [available, setAvailable] = useState(true)
  const [stores, setStores] = useState<any[]>([])
  const [empEmail, setEmpEmail] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  // Deep-link from the Time Clock report (?employee_id=&start=&end=) — an EXACT employee_id filter,
  // deliberately separate from StandardFilterBar's name-based `reps` multi-select (which matches on
  // display name, not id — a name match would be ambiguous for two people sharing a name). Undoable
  // via the banner's "Clear" button; the standard filter bar's own controls keep working normally.
  const [deepLinkEmployeeId, setDeepLinkEmployeeId] = useState('')
  const [deepLinkName, setDeepLinkName] = useState('')

  useEffect(() => {
    let cancelled = false
    let urlStart = '', urlEnd = '', urlEmp = ''
    try {
      const sp = new URLSearchParams(window.location.search)
      urlStart = sp.get('start') || ''; urlEnd = sp.get('end') || ''; urlEmp = sp.get('employee_id') || ''
    } catch { /* ignore */ }
    if (urlEmp) setDeepLinkEmployeeId(urlEmp)
    if (urlStart && urlEnd) {
      // Deep-link range wins outright — no need to resolve the tenant's default pay period.
      setFilt(f => ({ ...f, period: urlStart, periodTo: urlEnd }))
      setRangeReady(true)
      return
    }
    api('/api/v1/core/tenant-settings').then((r: any) => {
      if (cancelled) return
      const cur = currentPeriodFromSettingsResponse(r)
      if (cur) { setPpSettings(cur.settings); setFilt(f => ({ ...f, period: cur.period.start, periodTo: cur.period.end })) }
      else { const mr = monthRange(0); setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end })) }
    }).catch(() => {
      const mr = monthRange(0)
      if (!cancelled) setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end }))
    }).finally(() => { if (!cancelled) setRangeReady(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    api('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    api('/api/v1/storeops/employees').then((r: any) => {
      const m: Record<string, string> = {}
      for (const e of (Array.isArray(r) ? r : [])) if (e.employee_id) m[e.employee_id] = e.email || ''
      setEmpEmail(m)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!rangeReady || !filt.period || !filt.periodTo) return
    setLoading(true)
    api(`/api/v1/storeops/payroll-change-log?start=${filt.period}&end=${filt.periodTo}`)
      .then((r: any) => { setItems(r?.items || []); setAvailable(r?.available !== false) })
      .catch(() => { setItems([]); setAvailable(false) })
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeReady, filt.period, filt.periodTo])

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
  const repOptions = useMemo(() => optionsFromRows(items, {
    rep: r => r.employee_name || r.employee_id, repEmail: r => empEmail[r.employee_id],
  }).reps, [items, empEmail])

  const standardFiltered = useMemo(() => filterRows(items, filt, {
    store: r => r.store_code, market: r => storeMarket[r.store_code] || '',
    rep: r => r.employee_name || r.employee_id, date: r => r.work_date,
  }), [items, filt, storeMarket])
  const visibleItems = useMemo(() => deepLinkEmployeeId
    ? standardFiltered.filter(r => String(r.employee_id) === String(deepLinkEmployeeId))
    : standardFiltered, [standardFiltered, deepLinkEmployeeId])

  useEffect(() => {
    if (!deepLinkEmployeeId) { setDeepLinkName(''); return }
    const hit = items.find(r => String(r.employee_id) === String(deepLinkEmployeeId) && r.employee_name)
    if (hit) setDeepLinkName(hit.employee_name)
  }, [items, deepLinkEmployeeId])

  const periodName = rangeLabel(filt.period || '', filt.periodTo || '')

  const cols: ExportColumn[] = [
    { header: 'When (logged)', field: 'created_at', role: 'date', get: r => (r.created_at || '').replace('T', ' ').slice(0, 19) },
    { header: 'Work date', field: 'work_date', role: 'date', type: 'date', get: r => r.work_date || '' },
    { header: 'Employee', field: 'employee', role: 'rep', get: r => r.employee_name || r.employee_id || '' },
    { header: 'Store', field: 'store_code', role: 'store', get: r => r.store_code || '' },
    { header: 'Field changed', field: 'field', get: r => FIELD_LABEL[r.field] || r.field },
    { header: 'Before', field: 'before_value', get: r => r.before_value ?? '—' },
    { header: 'After', field: 'after_value', get: r => r.after_value ?? '—' },
    { header: 'How', field: 'entry_point', get: r => ENTRY_POINT_LABEL[r.entry_point] || r.entry_point },
    { header: 'Changed by', field: 'changed_by_email', get: r => r.changed_by_email || 'system' },
    { header: 'Role', field: 'changed_by_role', get: r => r.changed_by_role || '' },
    { header: 'Reason', field: 'reason', get: r => r.reason || '' },
  ]

  function setRange(start: string, end: string) { setFilt(f => ({ ...f, period: start, periodTo: end })) }
  function onFilterChange(v: StandardFilterValue) {
    setFilt(v.period || v.periodTo ? v : { ...v, period: filt.period, periodTo: filt.periodTo })
  }

  const filename = `payroll-change-log-${filt.period || 'range'}_to_${filt.periodTo || 'range'}`

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📜 Payroll Change Log</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Every manual change to a rep's scheduled/actual hours — shift edits, manager clock-in
          overrides, manual hours adjustments, force clock-outs, and lunch-deduction setting changes —
          who made it, when, and the before → after values. {periodName}. Click a row to jump to the
          Time Clock report for that employee/day.
        </p>
      </div>

      <StandardFilterBar
        value={filt} onChange={onFilterChange}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        right={
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <button style={chip} onClick={() => { const r = monthRange(0); setRange(r.start, r.end) }}>This month</button>
            <button style={chip} onClick={() => { const r = monthRange(-1); setRange(r.start, r.end) }}>Last month</button>
          </div>
        }
      />

      {deepLinkEmployeeId && (
        <div className="card" style={{ marginBottom: 12, padding: '8px 14px', fontSize: 13, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>🔗 Filtered from Time Clock: <strong>{deepLinkName || deepLinkEmployeeId}</strong> · {periodName}</span>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setDeepLinkEmployeeId('')}>Clear</button>
        </div>
      )}

      {!available && (
        <div className="card" style={{ marginBottom: 12, padding: '10px 14px', fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)' }}>
          ℹ️ The Payroll Change Log table isn't set up yet on this tenant (migration 414) — no entries
          can be recorded or shown until it runs. Every underlying write (shift edits, overrides,
          manual hours) still works normally; it just isn't logged yet.
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : visibleItems.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No manual payroll changes logged for {periodName}.
        </div>
      ) : (
        <ReportShell
          title="Payroll Change Log" subtitle={periodName} filename={filename}
          columns={cols} rows={visibleItems} defaultGroupBy="Employee"
          onRowClick={r => r.employee_id && r.work_date &&
            router.push(`/storeops/timeclock?employee_id=${encodeURIComponent(r.employee_id)}&start=${r.work_date}&end=${r.work_date}`)}
        />
      )}
    </div>
  )
}
