// THE LOCK for the printed schedule (owner directive 2026-09-20: *"a design fix ships with a check
// that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy
// appears"*). Index §STOREOPS-13.
//
// Two facts are locked here, both of which have already been wrong once:
//
//   1. THE PRINTED LINE IS A PERSON, never a store-day cell holding a list of names. That is the
//      illegible form the owner reported on 2026-11-26, and it comes back the moment somebody puts
//      a name into a day cell again "so you can see who it is".
//   2. "HOW LONG IS THIS SHIFT" HAS ONE HOME. The schedule grid had its own `hoursBetween` /
//      `shiftHrs` while the export totalled `scheduled_hours || 0` — two answers, so an overnight
//      shift read one way on screen and another in the emailed PDF. The page now dereferences
//      `scheduleExport.shiftHours`; this fails if it stops, or if a second copy of the arithmetic
//      appears anywhere under the storeops screens.
//
// Source-reading is deliberate: a behavioural test cannot see a copy that is merely EQUAL today.
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { buildScheduleExport, scheduleLines, type ScheduleExportInput } from '../scheduleExport'

const HOME = 'src/app/(platform)/storeops/lib/scheduleExport.ts'
const PAGE = 'src/app/(platform)/storeops/schedule/page.tsx'
const src = (p: string) => readFileSync(p, 'utf8')
// Comment lines carry the words on purpose (they explain the defect), so a lock reads CODE only.
const code = (p: string) => src(p)
  .split('\n')
  .filter(l => { const t = l.trim(); return t && !t.startsWith('//') && !t.startsWith('*') && !t.startsWith('/*') })
  .join('\n')

describe('lock 1 — the printed line is a person, not a grid cell', () => {
  // A full week, three people at one store, every day occupied — the exact shape that produced the
  // 127-hour unreadable row.
  const week = ['2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-27', '2026-11-28', '2026-11-29']
  const names = ['Aaa Person', 'Bbb Person', 'Ccc Person']
  const input: ScheduleExportInput = {
    view: 'store', weekStartLabel: 'November 23, 2026', weekStartIso: '2026-11-23',
    weekDates: week.map((iso, i) => ({ iso, dow: 'Mon', md: `11/${23 + i}` })),
    filterMarkets: [], filteredStores: [{ store_code: 'ST-1' }], filteredEmps: [],
    shifts: week.flatMap(d => names.map(n => ({
      employee_name: n, store_code: 'ST-1', shift_date: d,
      start_time: '09:00', end_time: '17:00', scheduled_hours: 8,
    }))),
  }

  it('no day cell ever contains a person’s name', () => {
    for (const line of scheduleLines(input))
      for (const cell of line.cells)
        for (const n of names) expect(cell).not.toContain(n)
  })

  it('no day cell ever contains a list separator', () => {
    for (const line of scheduleLines(input))
      for (const cell of line.cells) expect(cell).not.toMatch(/[;,]/)
  })

  it('a person’s line never carries another person’s hours', () => {
    const lines = scheduleLines(input)
    expect(lines).toHaveLength(names.length)
    for (const l of lines) expect(l.totalHours).toBeCloseTo(7 * 8)   // their own week, not 3 × that
  })

  it('the hours column sits beside the name and nothing trails the last day', () => {
    // The owner asked twice: hours NEXT TO THE NAME (2026-11-26), and no figure after the last time
    // column (2026-11-26, of the week of 11/30). Both are the same column, and this pins it.
    const headers = buildScheduleExport(input).sheets[0].columns.map(c => c.header)
    expect(headers[2]).toBe('Hrs')
    expect(headers[headers.length - 1]).toBe('Mon 11/29')
    expect(headers.filter(h => /hrs|hour|total/i.test(h))).toEqual(['Hrs'])
  })

  it('the builder never renders a name into a cell (the source cannot do it)', () => {
    // The old line was `.map(s => \`${isStore ? s.employee_name : s.store_code} ${s.start_time}…\`)`.
    // A day cell is times only, so no cell template may mention a name or a store at all.
    const cellTemplates = code(HOME).match(/`[^`]*start_time[^`]*`/g) || []
    expect(cellTemplates.length).toBeGreaterThan(0)
    for (const t of cellTemplates) {
      expect(t).not.toContain('employee_name')
      expect(t).not.toContain('store_code')
    }
  })
})

describe('lock 2 — "how long is this shift" has one home', () => {
  it('the schedule page defines no hours arithmetic of its own', () => {
    const page = code(PAGE)
    expect(page).not.toMatch(/function\s+hoursBetween/)
    expect(page).not.toMatch(/function\s+shiftHrs/)
    expect(page).not.toContain('scheduled_hours || 0')
    expect(page).not.toContain('24 * 60')            // the overnight wrap, in its only home
  })

  it('the schedule page dereferences the home instead', () => {
    expect(code(PAGE)).toMatch(/import\s*\{[^}]*\bshiftHours\b[^}]*\}\s*from\s*'\.\.\/lib\/scheduleExport'/)
  })

  it('the export totals through the home, never through the stored column', () => {
    const home = code(HOME)
    expect(home).toContain('shiftHours(s)')
    // `scheduled_hours` may appear exactly three times in code: the row type, the `shiftHours`
    // parameter type, and the documented fallback inside `shiftHours` itself. A fourth is a second
    // answer to the question.
    expect((home.match(/scheduled_hours/g) || []).length).toBe(3)
  })
})
