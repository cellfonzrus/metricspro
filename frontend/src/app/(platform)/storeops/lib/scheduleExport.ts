// Pure schedule-export payload builder — extracted 2026-08-04 (people-domain export-privacy audit,
// see docs/handoffs/people.md). Previously `/storeops/schedule` sent SendReportButton through the
// server `reportKey="storeops_schedule"` path, which only forwarded `week_start` + `store_code` — a
// MARKET-only filter (no single store picked) was silently dropped server-side, so Send emailed/
// WhatsApp'd every store's schedule org-wide instead of the filtered view on screen. This module is
// now the ONE place that renders the schedule grid into an ExportPayload; the page's local
// Excel/PDF/Print buttons AND SendReportButton both call it with the SAME on-screen-filtered rows,
// so there is no second query that can forget a filter. No runtime imports (only `import type`) so
// it's independently testable without a React/browser environment.
//
// ── 2026-11-26: ONE LINE PER PERSON. AN EXPORT IS A DOCUMENT, NOT A PICTURE OF A GRID. ───────────
// Owner, of the exported week of 2026-11-23: *"the schdule show show line by line which employee is
// working and thier houre next to their name instaed of in this illegible form"*.
//
// THE CLASS, not the instance. The instance was one store's Monday cell reading "Manan Ali
// 16:00-19:00; Hajra Cheema 09:00-19:00; Zohaib Ali 10:00-16:00" with 127.0 hours beside it — four
// people's week in a single row. The CLASS is that this builder mirrored the SCREEN GRID: one cell
// per row-per-day, holding however many shift chips happened to fall in it. A grid cell works on a
// wide scrolling screen with coloured chips and tooltips; in a fixed-width PDF column it wraps into
// an unreadable paragraph, and the wider the week the worse it gets. The shape was wrong for every
// store, every tenant and every week — not just this one.
//
// So the LINE, not the cell, is now the unit of the export: one line per person (per store they
// worked), their hours beside their name, each day holding only that person's own times. The day
// cell can hold at most that person's split shifts, stacked on their own lines — never a list of
// names. Both on-screen views keep working: "by store" groups the lines store-first, "by employee"
// groups them person-first, and the SAME line builder serves both so the two can never drift into
// two answers to "who works this week".
//
// WHAT IS DELIBERATELY KEPT. A store with nobody scheduled still gets a line (employee reads
// `NOBODY_SCHEDULED`), because the old grid showed that row and a coverage gap that vanishes from
// the export is a worse defect than an ugly one. Title, subtitle and filename are unchanged — the
// Send bar, the scheduled-report label and the saved filenames all key off them.
//
// AND THE SIBLING DEFECT FOUND ALONG THE WAY. The screen totalled a shift with the page's local
// `shiftHrs` (recomputed from start/end, overnight-safe) while this module totalled
// `scheduled_hours || 0` — two answers to "how long is this shift", so an overnight shift could read
// one way on screen and another in the PDF, and a stored negative read as negative. `shiftHours`
// below is now the ONE home and the page imports it; nothing recomputes hours a second time.
import type { ExportPayload, ExportColumn } from '@/lib/export'

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
  shifts: ScheduleShiftRow[]
}

// One printed line: a person, the store they work it at, their own weekly hours, and their own times
// per day. `cells` is parallel to `input.weekDates`.
export interface ScheduleExportLine {
  employee: string
  store: string
  totalHours: number
  cells: string[]
}

// What a store-view line says when nobody is scheduled there at all. A WORD, not a blank row: the
// line exists precisely so the gap is readable, and a blank line reads as a rendering fault.
export const NOBODY_SCHEDULED = '— nobody scheduled —'

// ── the ONE home for "how long is this shift" ────────────────────────────────────────────────────
// Recomputed from start/end because the stored `scheduled_hours` is editable and can be stale or
// negative; overnight-safe (an end before the start is the next morning). The stored value is the
// fallback for a row that carries no times at all. The schedule page imports this rather than
// keeping its own copy — see the header note.
export function hoursBetween(start: string, end: string): number {
  const [sh, sm] = String(start).split(':').map(Number)
  const [eh, em] = String(end).split(':').map(Number)
  const s = sh * 60 + sm, e = eh * 60 + em
  if (!isFinite(s) || !isFinite(e) || e === s) return 0
  let d = e - s
  if (d < 0) d += 24 * 60
  return d / 60
}
export function shiftHours(s: { start_time?: string; end_time?: string; scheduled_hours?: number }): number {
  if (s.start_time && s.end_time) return hoursBetween(s.start_time, s.end_time)
  return s.scheduled_hours || 0
}

// A person's times on one day, their own split shifts stacked on separate lines (autotable and the
// print stylesheet both honour a newline inside a cell). Never a name — the name IS the line.
function dayCell(rows: ScheduleShiftRow[]): string {
  return rows
    .slice()
    .sort((a, b) => String(a.start_time).localeCompare(String(b.start_time)))
    .map(s => `${s.start_time}-${s.end_time}`)
    .join('\n')
}

function lineFor(employee: string, store: string, rows: ScheduleShiftRow[],
                 weekDates: ScheduleDayLabel[]): ScheduleExportLine {
  return {
    employee, store,
    totalHours: rows.reduce((a, s) => a + shiftHours(s), 0),
    cells: weekDates.map(d => dayCell(rows.filter(s => s.shift_date === d.iso))),
  }
}

const uniqSorted = (xs: string[]) => Array.from(new Set(xs)).sort((a, b) => a.localeCompare(b))

/**
 * The printed lines for a week, in the order they appear in the document. ONE line per person per
 * store — see the header note. Both views share this, so "who works this week" has one answer.
 *
 * `view: 'store'`   → the filtered stores in the order given, each followed by its people A→Z.
 * `view: 'employee'`→ the filtered people in the order given, each followed by the stores they work.
 */
export function scheduleLines(input: ScheduleExportInput): ScheduleExportLine[] {
  const { view, weekDates, filteredStores, filteredEmps, shifts } = input
  const out: ScheduleExportLine[] = []

  if (view === 'store') {
    for (const st of filteredStores) {
      const atStore = shifts.filter(s => s.store_code === st.store_code)
      const names = uniqSorted(atStore.map(s => s.employee_name))
      if (!names.length) { out.push(lineFor(NOBODY_SCHEDULED, st.store_code, [], weekDates)); continue }
      for (const name of names) {
        out.push(lineFor(name, st.store_code, atStore.filter(s => s.employee_name === name), weekDates))
      }
    }
    return out
  }

  for (const emp of filteredEmps) {
    const theirs = shifts.filter(s => s.employee_name === emp.name)
    const stores = uniqSorted(theirs.map(s => s.store_code))
    if (!stores.length) { out.push(lineFor(emp.name, '', [], weekDates)); continue }
    for (const store of stores) {
      out.push(lineFor(emp.name, store, theirs.filter(s => s.store_code === store), weekDates))
    }
  }
  return out
}

export function buildScheduleExport(input: ScheduleExportInput): ExportPayload {
  const { view, weekStartLabel, weekStartIso, weekDates, filterMarkets } = input
  const rows = scheduleLines(input)

  // Name, store, then the hours — BESIDE the name, before the days, which is what was asked for and
  // also what makes the week readable: the number you look for is never off the far edge of a
  // landscape page.
  const cols: ExportColumn[] = [
    { header: 'Employee', get: (r: ScheduleExportLine) => r.employee },
    { header: 'Store', get: (r: ScheduleExportLine) => r.store },
    { header: 'Hrs', get: (r: ScheduleExportLine) => r.totalHours.toFixed(1), align: 'right' },
    ...weekDates.map((d, i) => ({
      header: `${d.dow} ${d.md}`,
      get: (r: ScheduleExportLine) => r.cells[i],
    })),
  ]

  const grouping = view === 'store' ? 'By store' : 'By employee'
  return {
    title: `Schedule — week of ${weekStartLabel}`,
    subtitle: `${grouping} · one line per employee${filterMarkets.length ? ` · ${filterMarkets.join(', ')}` : ''}`,
    filename: `schedule_${weekStartIso}`,
    sheets: [{ name: 'Schedule', columns: cols, rows }],
  }
}
