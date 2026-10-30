// UNIT TESTS, WRITTEN BEFORE THE CODE (project standard 2026-10-28) for the Zero Sales report's
// sendable payload — index §CORE-26.
//
// The whole risk of this report is the one its own page banner names: a day nothing landed for is
// NOT a day with zero sales. On screen that day reads "not reported" in words and in colour. A
// spreadsheet carries neither, so the sheet must say it in the CELL, and these tests are what stops
// a future edit from printing a plausible 0 into an email nobody can question.
import { describe, it, expect } from 'vitest'
import { zeroSalesSheets, zeroSalesTiles, ZS_STORE_SHEET, ZS_REP_SHEET } from '../zero-sales-export'
import { SNAPSHOT_SHEET_NAME } from '../tile-snapshot'

const payload = {
  days: ['2026-10-01', '2026-10-02'],
  rows: [
    {
      grain: 'store' as const, scope_key: 'B-1115', label: 'B-1115 — 123 Main St', parent: null,
      state: 'measured_zero', day_states: { '2026-10-01': 'measured_zero', '2026-10-02': 'not_reported' },
      day_counts: { '2026-10-01': 0, '2026-10-02': null },
      zero_days: 1, first_zero_day: '2026-10-01', last_zero_day: '2026-10-01', longest_zero_run: 1,
      gap_days: ['2026-10-02'], ended_by_gap: true, trading_calendar: 'schedule',
      days_measured_zero: 1, days_not_reported: 1, days_had_sales: 0, days_closed: 0,
      alerting: false, note: 'one zero day, one day with no feed',
    },
    {
      grain: 'rep' as const, scope_key: 'B-1115/Arora, Tanish', label: 'Arora, Tanish', parent: 'B-1115',
      state: 'had_sales', day_states: { '2026-10-01': 'had_sales', '2026-10-02': 'not_reported' },
      day_counts: { '2026-10-01': 3, '2026-10-02': null },
      zero_days: 0, first_zero_day: null, last_zero_day: null, longest_zero_run: 0,
      gap_days: ['2026-10-02'], ended_by_gap: false, trading_calendar: 'schedule',
      days_measured_zero: 0, days_not_reported: 1, days_had_sales: 1, days_closed: 0,
      alerting: false, note: '',
    },
  ],
  totals: {
    scopes: 2, store_days_measured_zero: 1, store_days_not_reported: 1,
    stores_alerting: 0, stores_unknown_calendar: 0,
  },
  config: { consecutive_days: 3 },
  config_source: 'org_row', rules_refused: false, note: null,
  state_notes: { not_reported: 'no feed landed for this store-day' },
  markets: ['Long Island'], market_of: { 'B-1115': 'Long Island' },
  date_from: '2026-10-01', date_to: '2026-10-02', scanned_rows: 42,
}

const cells = (sheetName: string, p = payload) => {
  const s = zeroSalesSheets(p as never).find(x => x.name === sheetName)!
  return s.rows.map(r => Object.fromEntries(s.columns.map(c => [c.header, c.get(r)])))
}

describe('zeroSalesSheets — the three sheets a Zero Sales send carries', () => {
  it('leads with the tile snapshot, then the stores, then the reps', () => {
    expect(zeroSalesSheets(payload as never).map(s => s.name))
      .toEqual([SNAPSHOT_SHEET_NAME, ZS_STORE_SHEET, ZS_REP_SHEET])
  })

  it('puts the store rows on the store sheet and the rep rows on the rep sheet — never both', () => {
    expect(cells(ZS_STORE_SHEET).map(r => r['Store'])).toEqual(['B-1115 — 123 Main St'])
    expect(cells(ZS_REP_SHEET).map(r => r['Rep'])).toEqual(['Arora, Tanish'])
  })

  it('names the store a rep belongs to, so a rep row is never read as the whole store', () => {
    expect(cells(ZS_REP_SHEET)[0]['Store']).toBe('B-1115')
  })
})

describe('a day nobody measured is never exported as a zero', () => {
  it('carries the not-reported count as its own column rather than folding it into the zeros', () => {
    const r = cells(ZS_STORE_SHEET)[0]
    expect(r['Measured zero days']).toBe(1)
    expect(r['Not reported days']).toBe(1)
  })

  it('exports a per-day transaction count as BLANK when the day was not reported, never 0', () => {
    const r = cells(ZS_STORE_SHEET)[0]
    expect(r['2026-10-01']).toBe(0)        // measured: the feed carried the store and it sold nothing
    expect(r['2026-10-02']).toBe('')       // not measured: blank, because a 0 here would be a lie
  })

  it('states each day in words too, so a reader of the file knows which state a blank was', () => {
    expect(cells(ZS_STORE_SHEET)[0]['State']).toBe('measured zero')
    expect(cells(ZS_REP_SHEET)[0]['State']).toBe('had sales')
  })

  it('shows no consecutive-zero figure for a run that could not be asserted', () => {
    expect(cells(ZS_REP_SHEET)[0]['Consecutive zero days']).toBe('')
  })
})

describe('zeroSalesTiles — the snapshot row is the four tiles on screen, verbatim', () => {
  it('carries each tile with its label, figure and note, and no change column it cannot measure', () => {
    const tiles = zeroSalesTiles(payload as never)
    expect(tiles.map(t => t.label)).toEqual([
      'Stores at or past the alert threshold',
      'Store-days measured zero',
      'Store-days not reported',
      'Stores with an unknown trading calendar',
    ])
    expect(tiles.map(t => t.value)).toEqual([0, 1, 1, 0])
    // No period is being compared on this report, so EVERY tile declares the change unmeasured
    // rather than mailing a ±0% that reads as "held steady" (§COMMCALC-19's class).
    expect(tiles.every(t => t.measured === false)).toBe(true)
    expect(tiles.every(t => !!t.note)).toBe(true)
  })

  it('reads the threshold out of the payload — never a number spelled in code (RULE TWO)', () => {
    const tiles = zeroSalesTiles({ ...payload, config: { consecutive_days: 7 } } as never)
    expect(tiles[0].note).toContain('7')
  })

  it('serves no tiles at all when the report is claiming nothing for this organisation', () => {
    expect(zeroSalesTiles({ ...payload, rules_refused: true } as never)).toEqual([])
    expect(zeroSalesSheets({ ...payload, rules_refused: true } as never)
      .find(s => s.name === SNAPSHOT_SHEET_NAME)!.rows).toEqual([])
  })
})
