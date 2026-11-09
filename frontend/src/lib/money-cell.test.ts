// A MONEY COLUMN THAT SAYS A WORD MUST PRINT THAT WORD, NEVER $0.00.
//
// OWNER REPORT 2026-11-09. The Cash Calendar's grid said "in: not measured" while the table
// directly underneath it said Expected in $0.00, Net $0.00 and TOTAL $0.00. The report exists to
// never show a figure for money nobody measured, and its own table showed four of them.
//
// THE CLASS: every money formatter in the app read `Number(v) || 0`, so a NON-NUMERIC value became
// a confident zero — on screen, in the TOTAL row, in the .xlsx cell and in the PDF. `lib/export`
// fixed the ABSENT half of this on 2026-09-10 and said so in a comment, then deliberately left a
// non-numeric value rendering "$0.00 as before"; four other copies never got even that. This is
// §19.18's shape: one question, six answers.
//
// These tests are over the ONE home (`report-totals.moneyText` / `toNumber`), which every formatter
// now dereferences. They were RED before that home existed.
import { describe, it, expect } from 'vitest'
import { moneyText, moneyRaw, isUnmeasured, toNumber, resolveAgg, computeTotalRow } from './report-totals'

describe('toNumber is the one test of numeric-ness', () => {
  it('reads real numbers, including a negative and a currency string', () => {
    expect(toNumber(0)).toBe(0)
    expect(toNumber(-844.81)).toBe(-844.81)
    expect(toNumber('$2,119.50')).toBe(2119.5)
  })
  it('refuses a word — this is the distinction every formatter lost', () => {
    expect(toNumber('not measured')).toBeNull()
    expect(toNumber('—')).toBeNull()
    expect(toNumber('not set')).toBeNull()
  })
  it('refuses absent, which is a different fact from a word', () => {
    expect(toNumber(null)).toBeNull()
    expect(toNumber('')).toBeNull()
  })
})

describe('isUnmeasured separates a WORD from an ABSENCE', () => {
  it('a non-empty non-numeric value is unmeasured — it was said on purpose', () => {
    expect(isUnmeasured('not measured')).toBe(true)
    expect(isUnmeasured('—')).toBe(true)
  })
  it('absent is NOT unmeasured — nobody declared anything, and that stays as it was', () => {
    expect(isUnmeasured(null)).toBe(false)
    expect(isUnmeasured(undefined)).toBe(false)
    expect(isUnmeasured('')).toBe(false)
  })
  it('a real zero is measured', () => {
    expect(isUnmeasured(0)).toBe(false)
    expect(isUnmeasured('0')).toBe(false)
  })
})

describe('moneyText — what a money cell shows', () => {
  it('THE DEFECT: a word prints itself, never $0.00', () => {
    expect(moneyText('not measured')).toBe('not measured')
    expect(moneyText('—')).toBe('—')
  })
  it('a real zero still prints $0.00 — a measured nothing is a figure', () => {
    expect(moneyText(0)).toBe('$0.00')
    expect(moneyText('0')).toBe('$0.00')
  })
  it('a number formats as currency', () => {
    expect(moneyText(2119.5)).toBe('$2,119.50')
    expect(moneyText(-844.81)).toBe('-$844.81')
  })
  it('absent keeps whatever each surface already decided — this home does not change it', () => {
    expect(moneyText(null, { absent: '' })).toBe('')
    expect(moneyText(null, { absent: '$0.00' })).toBe('$0.00')
  })
})

describe('moneyRaw — what lands in a spreadsheet cell', () => {
  it('a word goes in as the WORD, so the file cannot be read as zero either', () => {
    expect(moneyRaw('not measured')).toBe('not measured')
  })
  it('a number goes in as a number, so the sheet can still sum it', () => {
    expect(moneyRaw(2119.5)).toBe(2119.5)
    expect(moneyRaw('$2,119.50')).toBe(2119.5)
  })
  it('a real zero is a numeric 0, and absent is an empty cell', () => {
    expect(moneyRaw(0)).toBe(0)
    expect(moneyRaw(null)).toBe('')
    expect(moneyRaw('')).toBe('')
  })
})

describe('a TOTAL over a column with an unmeasured cell refuses to be a figure', () => {
  type VRow = { v: number | string | null }
  const col = { header: 'Expected in', money: true, get: (r: VRow) => r.v }

  it('THE DEFECT: four unmeasured days totalled $0.00', () => {
    const rows = [{ v: 'not measured' }, { v: 'not measured' }, { v: 'not measured' }]
    expect(resolveAgg(col, rows)).toBe('none')
    const cells = computeTotalRow([col], rows)
    expect(cells[0].text).not.toBe('$0.00')
  })

  it('a PARTIAL measurement does not sum either — a total that skipped a day is not that day', () => {
    const rows = [{ v: 100 }, { v: 'not measured' }]
    expect(resolveAgg(col, rows)).toBe('none')
  })

  it('an all-measured money column still sums exactly as before', () => {
    const rows = [{ v: 844.81 }, { v: 374.9 }, { v: 309.93 }, { v: 589.86 }]
    expect(resolveAgg(col, rows)).toBe('sum')
    expect(computeTotalRow([col], rows)[0].text).toBe('$2,119.50')
  })

  it('absent cells still sum — that is the 2026-09-10 ruling and it is not reopened here', () => {
    const rows = [{ v: 844.81 }, { v: null }]
    expect(resolveAgg(col, rows)).toBe('sum')
  })

  it('the real Cash Calendar shape: owed sums, expected refuses', () => {
    const days = [
      { date: '2026-11-09', expected_in: 'not measured', owed_out: 844.81 },
      { date: '2026-11-10', expected_in: 'not measured', owed_out: 374.9 },
    ]
    const inCol = { header: 'Expected in', money: true, get: (r: typeof days[number]) => r.expected_in }
    const outCol = { header: 'Owed out', money: true, get: (r: typeof days[number]) => r.owed_out }
    const cells = computeTotalRow([inCol, outCol], days)
    expect(cells[0].text).not.toBe('$0.00')
    expect(cells[1].text).toBe('$1,219.71')
  })
})
