// THE REGRESSION, from the rows the API actually served (index §CORE-43).
//
// Owner, 2026-11-12: *"payroll for luxlink shows 110571.59 for october but when you do per market it
// does not total up chicago is 41207 and Ny is 24966, where is the rest and which reps are not
// assigned a market"*.
//
// `payroll-market-2026-10.json` is the LIVE answer of `GET /storeops/payroll?month=2026-10` for
// LuxeLink, captured 2026-11-12 at 22:5x UTC by running the real producer against production — one
// entry per row, carrying only the store spelling and the scheduled pay (no employee name, no rate:
// what is proved here is attribution, and a test needs no one's pay record to prove it). Thirty-five
// rows, $110,571.59, and nineteen distinct store spellings of which NINE are the roster's own codes
// in CAPITALS, because that is what the time clock had written.
//
// WHAT THE SCREEN DID: Chicago $41,201.67 + NY $24,966.83, and $44,403.09 under no market — money
// that was in the total and in no market. The filter bar then showed a market with a smaller, entirely
// plausible number, which is why this class hides.
//
// (The capitalised spellings were normalised in production later the same evening by the time clock's
// write-side fix. That repaired the instance; this test holds the MECHANISM, so the report survives
// the next feed that spells a store its own way.)
import { describe, it, expect } from 'vitest'
import { buildStoreMarket, type StoreRosterRow } from '../store-market'
import live from './store-market.live.json'
import payroll from './payroll-market-2026-10.json'

type Row = { store: string; scheduled_pay: number }
const ROWS = payroll as Row[]
const ROSTER = (live as unknown as Record<string, { roster: StoreRosterRow[] }>).luxelink.roster
const cents = (n: number) => Math.round(n * 100)
const sum = (rows: Row[]) => rows.reduce((a, r) => a + cents(r.scheduled_pay), 0)

// The screen's own fold, as all twenty-four copies spelled it, kept here as the BEFORE picture.
const letterForLetter = (() => {
  const m: Record<string, string> = {}
  for (const s of ROSTER) if (s.store_code) m[s.store_code] = s.market || ''
  return (store: string) => m[store] || ''
})()

const splitBy = (marketOf: (s: string) => string) => {
  const out: Record<string, number> = {}
  for (const r of ROWS) {
    const k = marketOf(r.store) || '(no market)'
    out[k] = (out[k] || 0) + cents(r.scheduled_pay)
  }
  return out
}

describe('the October report, as the owner saw it', () => {
  it('the captured rows are the figure he read', () => {
    expect(ROWS).toHaveLength(35)
    expect(sum(ROWS)).toBe(cents(110571.59))
  })

  it('reproduces the defect: $44,403.09 in no market, and his two market figures', () => {
    const before = splitBy(letterForLetter)
    expect(before['Chicago']).toBe(cents(41201.67))
    expect(before['NY']).toBe(cents(24966.83))
    expect(before['(no market)']).toBe(cents(44403.09))
  })
})

describe('through the one home, every market dollar is accounted for', () => {
  const lk = buildStoreMarket(ROSTER)
  const after = splitBy(s => lk.marketOf(s))

  it('nothing is lost and nothing is invented — the split still totals the report', () => {
    expect(Object.values(after).reduce((a, b) => a + b, 0)).toBe(sum(ROWS))
  })

  it('the $37,760.23 of capitalised stores comes back to its market', () => {
    expect(after['Chicago']).toBe(cents(75289.90))
    expect(after['NY']).toBe(cents(28638.83))
    expect(cents(41201.67) + cents(24966.83) + cents(37760.23))
      .toBe(after['Chicago'] + after['NY'])
  })

  it('what is left under no market is ONLY the salaried manager with no home store', () => {
    // $6,642.86, store 'Unassigned' — the platform's own explicit bucket (payroll_salary.py), and a
    // real gap for the owner to close with a roster row, never something to fold into a market here.
    expect(after['(no market)']).toBe(cents(6642.86))
    const unmapped = ROWS.filter(r => !lk.marketOf(r.store))
    expect(unmapped.map(r => r.store)).toEqual(['Unassigned'])
  })

  it('each drifted spelling carries its own dollars, not a neighbours', () => {
    // "Drifted" = a spelling the letter-for-letter map could not place but the one home can. That is
    // the population of the defect, and it must land in the right market, store by store.
    const drifted = ROWS.filter(r => !letterForLetter(r.store) && lk.marketOf(r.store))
    expect(drifted.map(r => r.store).sort()).toEqual([
      '3352 26TH', '3735 26TH', 'ARMITAGE', 'BELMONT', 'CERMARK', 'CHICAGO HEIGHTS',
      'CICERO', 'CICERO', 'CICERO', 'CICERO', 'LAWRENCE', 'LAWRENCE', 'NOSTRAND',
    ])
    expect(sum(drifted)).toBe(cents(37760.23))
    const ny = drifted.filter(r => lk.marketOf(r.store) === 'NY')
    expect(ny.map(r => r.store)).toEqual(['NOSTRAND'])   // the one NY store the clock capitalised
    expect(sum(ny)).toBe(cents(3672.00))
  })
})
