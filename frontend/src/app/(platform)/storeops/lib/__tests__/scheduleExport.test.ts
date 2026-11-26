// Proof for the schedule export's LINE shape (owner 2026-11-26: *"the schdule show show line by line
// which employee is working and thier houre next to their name instaed of in this illegible form"*).
//
// The defect this pins: the export reproduced the SCREEN GRID — one cell per store-day holding every
// person who worked it ("Manan Ali 16:00-19:00; Hajra Cheema 09:00-19:00; Zohaib Ali 10:00-16:00").
// A grid cell is readable on a wide scrolling screen and unreadable in a fixed-width PDF column, so
// the week of 2026-11-23 exported as one 127-hour store row nobody could read. The export is a
// DOCUMENT, not a screenshot of a grid: one line per person, their hours beside their name.
//
// DB-free by construction — `buildScheduleExport` / `scheduleLines` take rows and return a payload.
import { describe, it, expect } from 'vitest'
import {
  buildScheduleExport, scheduleLines, shiftHours, NOBODY_SCHEDULED,
  type ScheduleExportInput, type ScheduleShiftRow,
} from '../scheduleExport'
import { payloadToCsv } from '@/lib/export'

const WEEK = ['2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-27', '2026-11-28', '2026-11-29']
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const weekDates = WEEK.map((iso, i) => ({ iso, dow: DOW[i], md: `11/${23 + i}` }))

function shift(p: Partial<ScheduleShiftRow>): ScheduleShiftRow {
  return {
    employee_name: 'Manan Ali', store_code: '7710', shift_date: '2026-11-23',
    start_time: '16:00', end_time: '19:00', scheduled_hours: 3, ...p,
  }
}
function input(p: Partial<ScheduleExportInput> = {}): ScheduleExportInput {
  return {
    view: 'store', weekStartLabel: 'November 23, 2026', weekStartIso: '2026-11-23',
    weekDates, filterMarkets: [], filteredStores: [{ store_code: '7710' }],
    filteredEmps: [{ name: 'Manan Ali' }], shifts: [shift({})], ...p,
  }
}

// ── the reported defect, reproduced ──────────────────────────────────────────────────────────────
describe('one line per employee (the reported defect)', () => {
  // The real week of 2026-11-23 at store 7710 — four people, all in ONE cell before this fix.
  const real = [
    shift({ employee_name: 'Manan Ali', shift_date: '2026-11-23', start_time: '16:00', end_time: '19:00' }),
    shift({ employee_name: 'Hajra Cheema', shift_date: '2026-11-23', start_time: '09:00', end_time: '19:00' }),
    shift({ employee_name: 'Zohaib Ali', shift_date: '2026-11-23', start_time: '10:00', end_time: '16:00' }),
    shift({ employee_name: 'Manan Ali', shift_date: '2026-11-24', start_time: '16:00', end_time: '19:00' }),
  ]
  const lines = scheduleLines(input({ shifts: real, filteredEmps: [] }))

  it('gives every person their OWN line, never many names in one cell', () => {
    expect(lines.map(l => l.employee)).toEqual(['Hajra Cheema', 'Manan Ali', 'Zohaib Ali'])
    for (const l of lines) for (const c of l.cells) expect(c).not.toContain(';')
  })

  it('puts a NAME-FREE time range in the day cell — the name is already the line', () => {
    const manan = lines.find(l => l.employee === 'Manan Ali')!
    expect(manan.cells[0]).toBe('16:00-19:00')
    expect(manan.cells[0]).not.toContain('Manan')
    expect(manan.cells[2]).toBe('')     // Wed — not scheduled
  })

  it('carries each person’s OWN weekly hours, not the store’s 127', () => {
    expect(lines.find(l => l.employee === 'Manan Ali')!.totalHours).toBeCloseTo(6)
    expect(lines.find(l => l.employee === 'Hajra Cheema')!.totalHours).toBeCloseTo(10)
    expect(lines.find(l => l.employee === 'Zohaib Ali')!.totalHours).toBeCloseTo(6)
  })

  it('puts the hours column BESIDE the name, before the days', () => {
    const cols = buildScheduleExport(input({ shifts: real })).sheets[0].columns.map(c => c.header)
    expect(cols.slice(0, 3)).toEqual(['Employee', 'Store', 'Hrs'])
    expect(cols[3]).toBe('Mon 11/23')
    expect(cols[cols.length - 1]).toBe('Sun 11/29')
  })

  it('stacks a split shift on its own line inside the day cell, never with a semicolon', () => {
    const split = [
      shift({ shift_date: '2026-11-23', start_time: '09:00', end_time: '12:00' }),
      shift({ shift_date: '2026-11-23', start_time: '15:00', end_time: '19:00' }),
    ]
    const [l] = scheduleLines(input({ shifts: split }))
    expect(l.cells[0]).toBe('09:00-12:00\n15:00-19:00')
    expect(l.totalHours).toBeCloseTo(7)
  })
})

// ── the shape of each view ───────────────────────────────────────────────────────────────────────
describe('both views keep one line per person', () => {
  const twoStores = [
    shift({ employee_name: 'Ana Ruiz', store_code: 'B-1598', shift_date: '2026-11-23' }),
    shift({ employee_name: 'Ana Ruiz', store_code: 'B-117', shift_date: '2026-11-25' }),
  ]
  it('store view: a line per person PER store, grouped by store', () => {
    const lines = scheduleLines(input({
      view: 'store', shifts: twoStores,
      filteredStores: [{ store_code: 'B-117' }, { store_code: 'B-1598' }], filteredEmps: [],
    }))
    expect(lines.map(l => [l.store, l.employee])).toEqual([['B-117', 'Ana Ruiz'], ['B-1598', 'Ana Ruiz']])
    expect(lines[0].cells[2]).toBe('16:00-19:00')      // Wed, at B-117
  })
  it('employee view: a line per person per store they actually worked', () => {
    const lines = scheduleLines(input({
      view: 'employee', shifts: twoStores,
      filteredEmps: [{ name: 'Ana Ruiz' }], filteredStores: [],
    }))
    expect(lines.map(l => [l.employee, l.store])).toEqual([['Ana Ruiz', 'B-117'], ['Ana Ruiz', 'B-1598']])
  })
  it('a store with nobody scheduled still gets a line, so a coverage gap stays visible', () => {
    const lines = scheduleLines(input({
      view: 'store', shifts: [],
      filteredStores: [{ store_code: '7710' }], filteredEmps: [],
    }))
    expect(lines).toHaveLength(1)
    expect(lines[0]).toMatchObject({ store: '7710', employee: NOBODY_SCHEDULED, totalHours: 0 })
  })
  it('an employee with no shifts still gets a line in employee view', () => {
    const lines = scheduleLines(input({
      view: 'employee', shifts: [], filteredEmps: [{ name: 'Idle Rep' }], filteredStores: [],
    }))
    expect(lines).toEqual([{ employee: 'Idle Rep', store: '', totalHours: 0, cells: ['', '', '', '', '', '', ''] }])
  })
})

// ── hours are ONE answer, shared with the screen ─────────────────────────────────────────────────
describe('shiftHours is the one home for "how long is this shift"', () => {
  it('recomputes from start/end so the export agrees with the grid', () => {
    expect(shiftHours({ start_time: '09:00', end_time: '17:30', scheduled_hours: 999 })).toBeCloseTo(8.5)
  })
  it('is overnight-safe — the stored value used to be trusted and read as negative', () => {
    expect(shiftHours({ start_time: '21:00', end_time: '05:00' })).toBeCloseTo(8)
  })
  it('falls back to the stored value only when a time is missing', () => {
    expect(shiftHours({ scheduled_hours: 4 })).toBe(4)
    expect(shiftHours({})).toBe(0)
  })
  it('an overnight shift totals 8 on the line, not -16', () => {
    const [l] = scheduleLines(input({ shifts: [shift({ start_time: '21:00', end_time: '05:00', scheduled_hours: -16 })] }))
    expect(l.totalHours).toBeCloseTo(8)
  })
})

// ── EVERY TENANT (owner directive 2026-10-30) ────────────────────────────────────────────────────
describe('every tenant, with its own store-code and name shapes', () => {
  const tenants = [
    { name: 'Cellfonz R Us', stores: ['B-1598', 'B-117'], emp: 'Manan Ali' },
    { name: 'LuxeLink', stores: ['7710', '7711'], emp: 'Hajra Cheema' },
    { name: 'Vzone', stores: ['VZ-01'], emp: 'Rana Bilal' },
    { name: 'NY Logistics', stores: ['NYL-BK-1'], emp: 'Tooba Rafique' },
  ]
  for (const t of tenants) {
    it(`${t.name}: its own rows produce its own lines`, () => {
      const shifts = t.stores.map(sc => shift({ employee_name: t.emp, store_code: sc }))
      const lines = scheduleLines(input({
        shifts, filteredStores: t.stores.map(store_code => ({ store_code })), filteredEmps: [],
      }))
      expect(lines).toHaveLength(t.stores.length)
      for (const l of lines) {
        expect(l.employee).toBe(t.emp)
        expect(t.stores).toContain(l.store)
        expect(l.cells[0]).toBe('16:00-19:00')
      }
    })
  }
  it('one tenant’s rows can never land on another tenant’s line', () => {
    // Both tenants' rows in one call (what a leaking caller would pass): only the stores IN SCOPE
    // get lines, and no out-of-scope name appears anywhere in the output.
    const mixed = [shift({ employee_name: 'Manan Ali', store_code: 'B-1598' }),
                   shift({ employee_name: 'Rana Bilal', store_code: 'VZ-01' })]
    const lines = scheduleLines(input({ shifts: mixed, filteredStores: [{ store_code: 'B-1598' }], filteredEmps: [] }))
    expect(lines.map(l => l.employee)).toEqual(['Manan Ali'])
    expect(JSON.stringify(lines)).not.toContain('Rana Bilal')
    expect(JSON.stringify(lines)).not.toContain('VZ-01')
  })
  it('the builder knows none of the words above (RULE TWO)', async () => {
    // Every store code and person's name in this file is a FIXTURE. None of them may appear in the
    // module, because a builder that recognises one tenant's vocabulary is the per-tenant code
    // RULE TWO forbids. The brands themselves are not spelled here — "no carrier name in this
    // codebase" is the house `harness_carrier_vocab_guard` lock's question, not this file's, and
    // writing one to assert its absence trips that lock (it did, 2026-11-26).
    const fs = await import('node:fs')
    const src = fs.readFileSync('src/app/(platform)/storeops/lib/scheduleExport.ts', 'utf8')
    const body = src.split('\n')
      .filter(l => { const t = l.trim(); return t && !t.startsWith('//') && !t.startsWith('*') })
      .join('\n')
    const fixtures = [...tenants.flatMap(t => [...t.stores, t.emp]), 'Manan Ali', 'Hajra Cheema']
    for (const word of fixtures) expect(body).not.toContain(word)
    // …and nothing store-code SHAPED is written into it at all.
    expect(body).not.toMatch(/'[A-Z]{1,4}-?\d{3,5}'/)
  })
})

// ── it survives the REAL shared renderer ─────────────────────────────────────────────────────────
describe('through the real export renderer', () => {
  it('renders one CSV line per employee with the hours beside the name', () => {
    const payload = buildScheduleExport(input({
      shifts: [
        shift({ employee_name: 'Manan Ali', shift_date: '2026-11-23' }),
        shift({ employee_name: 'Hajra Cheema', shift_date: '2026-11-23', start_time: '09:00', end_time: '19:00' }),
      ],
      filteredEmps: [],
    }))
    const csv = payloadToCsv(payload)
    const lines = csv.split('\r\n').filter(Boolean)
    expect(lines[0]).toContain('Employee,Store,Hrs,Mon 11/23')
    expect(lines[1]).toBe('Hajra Cheema,7710,10.0,09:00-19:00,,,,,,')
    expect(lines[2]).toBe('Manan Ali,7710,3.0,16:00-19:00,,,,,,')
  })
  it('keeps the title, filename and market subtitle the Send bar already relies on', () => {
    const p = buildScheduleExport(input({ filterMarkets: ['Brooklyn', 'Queens'] }))
    expect(p.title).toBe('Schedule — week of November 23, 2026')
    expect(p.filename).toBe('schedule_2026-11-23')
    expect(p.subtitle).toContain('Brooklyn, Queens')
    expect(p.sheets).toHaveLength(1)
  })
})
