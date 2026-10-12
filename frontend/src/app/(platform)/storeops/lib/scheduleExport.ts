// Pure schedule-export payload builder — extracted 2026-08-04 (people-domain export-privacy audit,
// see docs/handoffs/people.md). Previously `/storeops/schedule` sent SendReportButton through the
// server `reportKey="storeops_schedule"` path, which only forwarded `week_start` + `store_code` — a
// MARKET-only filter (no single store picked) was silently dropped server-side, so Send emailed/
// WhatsApp'd every store's schedule org-wide instead of the filtered view on screen. This module is
// now the ONE place that renders the schedule grid into an ExportPayload; the page's local
// Excel/PDF/Print buttons AND SendReportButton both call it with the SAME on-screen-filtered rows,
// so there is no second query that can forget a filter. No runtime imports (only `import type`) so
// it's independently testable without a React/browser environment.
import type { ExportPayload } from '@/lib/export'
import type { AxesShift } from './scheduleAxes'

export interface ScheduleShiftRow {
  employee_name: string
  store_code: string
  shift_date: string
  start_time: string
  end_time: string
  scheduled_hours: number
}

export interface ScheduleDayLabel { iso: string; dow: string; md: string }

export interface ScheduleExportInput {
  view: 'store' | 'employee'
  weekStartLabel: string    // pre-formatted, e.g. "August 4, 2026" — no date-lib dependency in this module
  weekStartIso: string      // e.g. "2026-08-04" — filename only
  weekDates: ScheduleDayLabel[]
  filterMarkets: string[]
  // Rows already scoped by the page's on-screen market/store filters. This module never re-derives
  // "what's in scope" — that decision stays in ONE place (the component state that also drives the
  // visible table), so the export can never disagree with the screen.
  filteredStores: { store_code: string }[]
  filteredEmps: { name: string }[]
  // §STOREOPS-01 — the axes come PRE-GROUPED from the one home (`scheduleAxes.scheduledAxes`), the
  // same object the grid renders. This module no longer re-groups shifts by `employee_name`: that
  // string is a denormalized copy of the roster name, and matching on it dropped a renamed
  // employee's hours out of the employee axis while leaving them in the store axis.
  employeeBuckets?: { key: string; name: string; shifts: AxesShift[] }[]
  byStoreShifts?: Record<string, AxesShift[]>
  shifts: ScheduleShiftRow[]
}

export function buildScheduleExport(input: ScheduleExportInput): ExportPayload {
  const { view, weekStartLabel, weekStartIso, weekDates, filterMarkets, filteredStores, filteredEmps,
          employeeBuckets, byStoreShifts, shifts } = input
  const isStore = view === 'store'
  // Pre-grouped rows from the one home when the caller has them (every in-app caller does); the
  // name-matched fallback stays only for a caller that predates §STOREOPS-01, and is the shape the
  // defect lived in — which is why `harness_schedule_identity_lock.py` pins this function.
  const baseRows: { label: string; rowShifts: ScheduleShiftRow[] }[] = isStore
    ? filteredStores.map(s => ({
        label: s.store_code,
        rowShifts: (byStoreShifts?.[s.store_code] as ScheduleShiftRow[] | undefined)
          ?? shifts.filter(x => x.store_code === s.store_code),
      }))
    : (employeeBuckets
        ? employeeBuckets.map(b => ({ label: b.name, rowShifts: b.shifts as ScheduleShiftRow[] }))
        : filteredEmps.map(e => ({ label: e.name,
            rowShifts: shifts.filter(x => x.employee_name === e.name) })))

  const rows = baseRows.map(r => {
    const rowShifts = r.rowShifts
    const cells = weekDates.map(d => rowShifts.filter(s => s.shift_date === d.iso)
      .map(s => `${isStore ? s.employee_name : s.store_code} ${s.start_time}-${s.end_time}`).join('; '))
    const total = rowShifts.reduce((a, s) => a + (s.scheduled_hours || 0), 0)
    return { label: r.label, cells, total }
  })

  const cols = [
    { header: isStore ? 'Store' : 'Employee', get: (r: typeof rows[number]) => r.label },
    ...weekDates.map((d, i) => ({ header: `${d.dow} ${d.md}`, get: (r: typeof rows[number]) => r.cells[i] })),
    { header: 'Total Hrs', get: (r: typeof rows[number]) => r.total.toFixed(1), align: 'right' as const },
  ]

  return {
    title: `Schedule — week of ${weekStartLabel}`,
    subtitle: `${isStore ? 'By store' : 'By employee'}${filterMarkets.length ? ` · ${filterMarkets.join(', ')}` : ''}`,
    filename: `schedule_${weekStartIso}`,
    sheets: [{ name: 'Schedule', columns: cols, rows }],
  }
}
