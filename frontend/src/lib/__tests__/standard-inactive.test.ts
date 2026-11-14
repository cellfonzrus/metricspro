// SHOW INACTIVE ON THE SHARED BAR (owner directive 2026-11-14, index §CORE-44) — the pure rules.
// The component's half (the checkbox renders, emits, and the note says what was hidden) is proved in
// `components/__tests__/StandardFilterBar.inactive.test.tsx`.
//
// The bar is the reason this reaches EVERY report at once rather than one screen: a page that already
// adopted the standard filters declares one accessor and gets the directive for free.
import { describe, expect, it } from 'vitest'
import {
  emptyStandardFilter, filterRows, isStandardFilterActive, isStandardInactiveShown,
  matchesStandardFilter, standardInactiveHidden, type StandardFilterValue,
} from '@/lib/standard-filters'

type Row = { store: string; is_active?: unknown; amount: number }

const ROWS: Row[] = [
  { store: 'B-1115', amount: 100 },                      // flag never set -> LIVE
  { store: 'B-2778', is_active: false, amount: 250 },     // closed
  { store: 'B-1598', is_active: true, amount: 300 },
  { store: 'B-9001', is_active: 'false', amount: 50 },    // came off the wire as a string
]
const ACC = { store: (r: Row) => r.store, active: (r: Row) => r.is_active }

describe('the standard filters hide inactive rows by default', () => {
  it('a page that declares the flag gets closed stores hidden with no further work', () => {
    const got = filterRows(ROWS, emptyStandardFilter(), ACC)
    expect(got.map(r => r.store)).toEqual(['B-1115', 'B-1598'])
  })

  it('ticking Show inactive brings them back', () => {
    const sel: StandardFilterValue = { ...emptyStandardFilter(), showInactive: true }
    expect(filterRows(ROWS, sel, ACC).map(r => r.store))
      .toEqual(['B-1115', 'B-2778', 'B-1598', 'B-9001'])
  })

  it('a page that declares NO active accessor is untouched — nothing is hidden behind its back', () => {
    const got = filterRows(ROWS, emptyStandardFilter(), { store: (r: Row) => r.store })
    expect(got.map(r => r.store)).toEqual(['B-1115', 'B-2778', 'B-1598', 'B-9001'])
  })

  it('combines with the other filters rather than replacing them', () => {
    const sel: StandardFilterValue = { ...emptyStandardFilter(), stores: ['B-2778'] }
    expect(filterRows(ROWS, sel, ACC)).toEqual([])          // selected, but closed
    expect(filterRows(ROWS, { ...sel, showInactive: true }, ACC).map(r => r.store))
      .toEqual(['B-2778'])
  })

  it('judges one row the same way the list is judged', () => {
    expect(matchesStandardFilter(ROWS[1], emptyStandardFilter(), ACC)).toBe(false)
    expect(matchesStandardFilter(ROWS[1], { ...emptyStandardFilter(), showInactive: true }, ACC)).toBe(true)
  })
})

describe('what the user is told', () => {
  it('counts what the default hid, so a report can say it out loud', () => {
    expect(standardInactiveHidden(ROWS, emptyStandardFilter(), ACC)).toBe(2)
  })

  it('counts nothing hidden once Show inactive is on', () => {
    expect(standardInactiveHidden(ROWS, { ...emptyStandardFilter(), showInactive: true }, ACC)).toBe(0)
  })

  it('counts nothing when the page declares no flag', () => {
    expect(standardInactiveHidden(ROWS, emptyStandardFilter(), { store: (r: Row) => r.store })).toBe(0)
  })

  it('does not count the inactive-hiding default as "a filter is narrowing this report"', () => {
    // Otherwise every adopting report would open claiming the user had filtered something.
    expect(isStandardFilterActive(emptyStandardFilter())).toBe(false)
    expect(isStandardInactiveShown(emptyStandardFilter())).toBe(false)
  })

  it('treats Show inactive as a WIDENING that Clear must still undo', () => {
    const sel: StandardFilterValue = { ...emptyStandardFilter(), showInactive: true }
    expect(isStandardFilterActive(sel)).toBe(false)   // it hides nothing
    expect(isStandardInactiveShown(sel)).toBe(true)   // but the bar must offer a way back
  })

  it('starts OFF — the directive is the default, not an opt-in', () => {
    expect(emptyStandardFilter().showInactive).toBe(false)
  })
})
