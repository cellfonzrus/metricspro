// THE TWO COST TILES' OWN FOLD, PROVED (index §STOREOPS-08).
//
// Owner, 2026-11-12: *"in the payroll report add anopthe rtil for the payroll tax paid for that
// period and a another tile shows the total of payroll and payroll tax as a cost of payroll to teh
// company"*.
//
// WHAT THIS FOLD IS, AND WHAT IT MUST NOT BE. The two figures are the SERVER'S: the employer tax
// rule lives in `payroll_expenses.compute_payroll_tax_from_wages` and the route stamps
// `employer_tax` / `employer_cost` onto every row it pays. This fold only SUMS the rows currently on
// screen, because the payroll report filters in the browser (store / market / employee) and a tile
// must report the population the reader is looking at. It applies no rate and knows no rate.
//
// AND IT MUST NOT INVENT A ZERO. A row the report did not pay carries no cost key at all, and a
// pay-gated caller's rows carry none either (the money keys are deleted server-side, mig 434). Both
// cases have to fold to "nothing to show" rather than to $0.00, which would tell the owner this
// payroll cost the company nothing.
import { describe, it, expect } from 'vitest'
import { employerTotals, type BurdenRow } from '../payroll-cost-tiles'

const PAID = [
  { actual_pay: 1591.38, employer_tax: 174.27, employer_cost: 1765.65 },
  { actual_pay: 836.00, employer_tax: 91.54, employer_cost: 927.54 },
]

describe('the fold is a sum of what is on screen, and nothing else', () => {
  it('adds the server figures to the cent', () => {
    expect(employerTotals(PAID)).toEqual({ tax: 265.81, cost: 2693.19, counted: 2, available: true })
  })

  it('follows the filter: fewer rows, smaller figure', () => {
    expect(employerTotals([PAID[0]])).toEqual({ tax: 174.27, cost: 1765.65, counted: 1, available: true })
  })

  it('cost is pay + tax over the same rows — the tile cannot drift from the two beside it', () => {
    const t = employerTotals(PAID)
    const pay = PAID.reduce((a, r) => a + r.actual_pay, 0)
    expect(t.cost).toBeCloseTo(pay + t.tax, 2)
  })
})

describe('absence is never zero', () => {
  it('a row the report did not pay carries no cost and is not counted', () => {
    const t = employerTotals([...PAID, { actual_pay: 0 }])
    expect(t.counted).toBe(2)
    expect(t.tax).toBe(265.81)
  })

  it('a pay-gated caller (no money keys at all) gets NOTHING TO SHOW, not $0.00', () => {
    expect(employerTotals([{} as BurdenRow]))
      .toEqual({ tax: 0, cost: 0, counted: 0, available: false })
  })

  it('an empty period is not available either', () => {
    expect(employerTotals([]).available).toBe(false)
    expect(employerTotals(undefined).available).toBe(false)
  })

  it('an older backend that serves no burden keys degrades to unavailable, never to zero', () => {
    expect(employerTotals([{ actual_pay: 1591.38 }]).available).toBe(false)
  })
})

describe('rounding is done once, at the end', () => {
  it('fractions of a cent do not accumulate into a visible drift', () => {
    const rows = [
      { actual_pay: 1, employer_tax: 0.005, employer_cost: 1.005 },
      { actual_pay: 1, employer_tax: 0.005, employer_cost: 1.005 },
    ]
    expect(employerTotals(rows).tax).toBe(0.01)
  })
})
