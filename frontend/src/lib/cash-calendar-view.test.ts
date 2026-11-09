import { describe, expect, it } from 'vitest'
import { CASH_CAL_BASIS_WORD, cashCalWeeks, mondayIndex } from './cash-calendar-view'

// PROOF for the two pure display facts of the Cash Calendar (§COMMCALC-56). Both exist because they
// are rules that must not drift from the backend home that owns them, and neither is provable from a
// screenshot.

describe('mondayIndex', () => {
  it('is Monday-indexed, which is the off-by-one that would shift every cell one column', () => {
    expect(mondayIndex('2026-11-09')).toBe(0)   // a Monday
    expect(mondayIndex('2026-11-13')).toBe(4)   // the Friday of that week
    expect(mondayIndex('2026-11-15')).toBe(6)   // the Sunday ENDS the week here, it does not start it
  })

  it('does not shift a date by a day in a timezone behind UTC — the dates are parsed as UTC', () => {
    expect(mondayIndex('2026-11-09T00:00:00')).toBe(0)
    expect(mondayIndex('2026-11-09')).toBe(mondayIndex('2026-11-09T23:59:59'))
  })

  it('refuses to throw on a value that is not a date', () => {
    expect(mondayIndex('')).toBe(0)
    expect(mondayIndex('not a date')).toBe(0)
  })
})

describe('cashCalWeeks', () => {
  const day = (date: string) => ({ date })

  it('lays a run of days out as seven-column weeks, Monday first, padding the edges', () => {
    // Thu 5 Nov 2026 → Mon 9 Nov 2026
    const weeks = cashCalWeeks(['2026-11-05', '2026-11-06', '2026-11-07', '2026-11-08', '2026-11-09'].map(day))
    expect(weeks).toHaveLength(2)
    expect(weeks[0].map(c => c?.date ?? null))
      .toEqual([null, null, null, '2026-11-05', '2026-11-06', '2026-11-07', '2026-11-08'])
    expect(weeks[1].map(c => c?.date ?? null))
      .toEqual(['2026-11-09', null, null, null, null, null, null])
  })

  it('every day handed in appears exactly once — a calendar that dropped or doubled a day would '
     + 'double or hide real money', () => {
    const dates = Array.from({ length: 31 }, (_, i) => `2026-11-${String(i + 1).padStart(2, '0')}`)
    const flat = cashCalWeeks(dates.map(day)).flat().filter(Boolean).map(c => c!.date)
    expect(flat).toEqual(dates)
    expect(new Set(flat).size).toBe(31)
  })

  it('each week is exactly seven cells, so the grid never reflows', () => {
    const dates = Array.from({ length
: 31 }, (_, i) => `2026-11-${String(i + 1).padStart(2, '0')}`)
    for (const w of cashCalWeeks(dates.map(day))) expect(w).toHaveLength(7)
  })

  it('no days is no weeks, not one empty week', () => {
    expect(cashCalWeeks([])).toEqual([])
    expect(cashCalWeeks(null as never)).toEqual([])
  })

  it('a row with no usable date is left out rather than placed on Monday', () => {
    const weeks = cashCalWeeks([{ date: '2026-11-09' }, { date: '' } as never])
    expect(weeks.flat().filter(Boolean).map(c => c!.date)).toEqual(['2026-11-09'])
  })
})

describe('CASH_CAL_BASIS_WORD', () => {
  it('words every basis the backend can send, including the three that mean NOT MEASURED', () => {
    for (const k of ['statement_field', 'weekly_declared', 'semi_monthly_declared',
      'monthly_declared', 'net_days_declared', 'cadence_undeclared', 'day_undeclared', 'no_date']) {
      expect(CASH_CAL_BASIS_WORD[k]).toBeTruthy()
    }
  })

  it('says "not measured" in words for each of the three, so a screen cannot imply a rhythm the '
     + 'carrier never named', () => {
    for (const k of ['cadence_undeclared', 'day_undeclared', 'no_date']) {
      expect(CASH_CAL_BASIS_WORD[k]).toMatch(/not measured/)
    }
  })

  it('has no entry that quietly reads as a measurement for an unknown basis — the page falls '
     + 'through to the raw key instead of an empty cell', () => {
    expect(CASH_CAL_BASIS_WORD['a_basis_added_next_year']).toBeUndefined()
  })
})
