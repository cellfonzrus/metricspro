/**
 * The work week is the TENANT'S week. These tests exist because the one thing that would make the
 * phone's rota quietly wrong is assuming Monday.
 *
 * One tenant here runs a Thursday work week (`work_week_start_dow` = 3). A screen that defaulted to
 * Monday would render seven real shifts under the wrong seven dates and look entirely plausible —
 * which is why `weekOf` takes the dow as an argument and these cases cover each of the seven.
 */
import { describe, expect, it } from 'vitest'

import {
  MONDAY, dayLabel, daysOf, dowOf, isCurrentWeek, shiftWeek, startDowOf, timeRange, weekLabel,
  weekOf,
} from './schedule-week'

// 2026-11-01 is a SUNDAY; 2026-11-02 a Monday; 2026-11-05 a Thursday.
const SUN = '2026-11-01'
const MON = '2026-11-02'
const THU = '2026-11-05'

describe('dowOf — the product scale, 0 = Monday', () => {
  it('maps a Monday to 0 and a Sunday to 6, not JS order', () => {
    expect(dowOf(MON)).toBe(0)
    expect(dowOf(SUN)).toBe(6)
    expect(dowOf(THU)).toBe(3)
  })

  it('agrees with dayLabel for every day of one week', () => {
    expect(daysOf(weekOf(MONDAY, MON)).map(dayLabel)).toEqual(
      ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
  })
})

describe('startDowOf — a declaration read safely', () => {
  it('keeps a declared day', () => {
    expect(startDowOf(3)).toBe(3)
    expect(startDowOf(0)).toBe(0)
    expect(startDowOf(6)).toBe(6)
  })

  it('falls back to the HOUSE POLICY, never to today, when the setting is unreadable', () => {
    for (const junk of [null, undefined, '', 'nonsense', NaN, {}, []]) {
      expect(startDowOf(junk as never)).toBe(MONDAY)
    }
  })

  it('wraps a value outside 0–6 rather than producing a nonsense offset', () => {
    expect(startDowOf(7)).toBe(0)
    expect(startDowOf(-1)).toBe(6)
    expect(startDowOf(10)).toBe(3)
  })

  it('truncates a fractional day instead of carrying it into the arithmetic', () => {
    expect(startDowOf(3.7)).toBe(3)
  })
})

describe('weekOf — THE tenant question', () => {
  it('a Monday tenant: Sunday belongs to the week that STARTED on Monday', () => {
    expect(weekOf(0, SUN)).toEqual({ start: '2026-10-26', end: SUN })
  })

  it('a Monday tenant: Monday starts its own week', () => {
    expect(weekOf(0, MON)).toEqual({ start: MON, end: '2026-11-08' })
  })

  it('THE THURSDAY TENANT: the same Sunday belongs to a DIFFERENT week', () => {
    expect(weekOf(3, SUN)).toEqual({ start: '2026-10-29', end: '2026-11-04' })
  })

  it('a Thursday tenant: Thursday starts its own week', () => {
    expect(weekOf(3, THU)).toEqual({ start: THU, end: '2026-11-11' })
  })

  it('one tenant`s week is never the other`s for the same day', () => {
    expect(weekOf(0, SUN).start).not.toBe(weekOf(3, SUN).start)
  })

  it('every one of the seven declarations produces a week CONTAINING the day asked about', () => {
    for (let dow = 0; dow < 7; dow++) {
      // 2028-02-29 is a REAL leap day (2026 has none — an earlier version of this test asked
      // about 2026-02-29, which JS rolls to March 1, so the test was wrong and the code was not).
      for (const day of [SUN, MON, THU, '2026-02-28', '2028-02-29', '2027-01-01']) {
        const w = weekOf(dow, day)
        expect(daysOf(w)).toContain(day)
        expect(dowOf(w.start)).toBe(dow)
      }
    }
  })

  it('crosses a month boundary without losing a day', () => {
    const w = weekOf(0, '2026-10-29')
    expect(w).toEqual({ start: '2026-10-26', end: '2026-11-01' })
    expect(daysOf(w)).toHaveLength(7)
  })

  it('crosses a YEAR boundary', () => {
    const w = weekOf(0, '2026-12-31')
    expect(daysOf(w)).toContain('2026-12-31')
    expect(daysOf(w)).toContain('2027-01-01')
  })
})

describe('shiftWeek / daysOf', () => {
  it('steps forward and back by whole weeks', () => {
    const w = weekOf(0, MON)
    expect(shiftWeek(w, 1)).toEqual({ start: '2026-11-09', end: '2026-11-15' })
    expect(shiftWeek(w, -1)).toEqual({ start: '2026-10-26', end: '2026-11-01' })
  })

  it('a step and a step back returns the same week', () => {
    const w = weekOf(3, THU)
    expect(shiftWeek(shiftWeek(w, 4), -4)).toEqual(w)
  })

  it('a week is always exactly seven consecutive days', () => {
    const days = daysOf(weekOf(5, '2026-07-04'))
    expect(days).toHaveLength(7)
    expect(new Set(days).size).toBe(7)
  })

  it('stepping never changes which day of the week it starts on', () => {
    const w = weekOf(3, THU)
    for (const n of [-9, -1, 0, 1, 52]) expect(dowOf(shiftWeek(w, n).start)).toBe(3)
  })
})

describe('isCurrentWeek', () => {
  it('is true for the week containing today under that tenant`s declaration', () => {
    for (let dow = 0; dow < 7; dow++) expect(isCurrentWeek(dow, weekOf(dow))).toBe(true)
  })

  it('is false a week away', () => {
    expect(isCurrentWeek(0, shiftWeek(weekOf(0), 1))).toBe(false)
  })

  it('judges against the SAME declaration it is handed, not Monday', () => {
    // The current Monday-week and the current Thursday-week start on different dates, so each is
    // "current" only under its own tenant's declaration.
    expect(isCurrentWeek(3, weekOf(0))).toBe(weekOf(0).start === weekOf(3).start)
  })
})

describe('weekLabel', () => {
  it('repeats the month only when the week crosses one', () => {
    expect(weekLabel({ start: '2026-11-02', end: '2026-11-08' })).toBe('Nov 2 – 8')
    expect(weekLabel({ start: '2026-10-29', end: '2026-11-04' })).toBe('Oct 29 – Nov 4')
  })
})

describe('timeRange', () => {
  it('renders a normal shift', () => {
    expect(timeRange('07:00', '15:30')).toBe('7a – 3:30p')
  })

  it('renders noon and midnight the way a person reads them', () => {
    expect(timeRange('12:00', '00:30')).toBe('12p – 12:30a')
  })

  it('keeps seconds out of the label', () => {
    expect(timeRange('09:15:00', '17:45:00')).toBe('9:15a – 5:45p')
  })

  it('returns null for a shift with NO times rather than inventing midnight', () => {
    for (const [a, b] of [[null, null], ['', ''], [undefined, undefined], ['oops', 'oops']]) {
      expect(timeRange(a, b)).toBeNull()
    }
  })

  it('renders the one time it has when only one is recorded', () => {
    expect(timeRange('08:00', null)).toBe('8a')
    expect(timeRange(null, '16:00')).toBe('4p')
  })

  it('refuses an impossible hour instead of rendering it', () => {
    expect(timeRange('29:00', null)).toBeNull()
  })
})
