// UNIT: what the Weekly Performance Review exports (docs/TESTING.md — every change ships unit tests
// for its logic). The one thing a review pack must not do in a spreadsheet is print 0 for an item
// nobody measured: a cell has no colour, no tooltip and no banner, so the state has to be in words
// and the figure has to be blank.
import { describe, it, expect } from 'vitest'
import {
  WR_STATE_WORD, WR_WINDOW_WORD, cellFigure, screenFigure, reviewsOf, rowMoney,
  weeklyReviewSheets,
  type ReviewItem, type ReviewPack,
} from './weekly-review-export'

const WEEK = { date_from: '2026-11-09', date_to: '2026-11-15', week_ending: '2026-11-15',
               label: '9–15 November 2026' }

function item(over: Partial<ReviewItem> = {}): ReviewItem {
  return {
    key: 'profit', label: 'Profit by store', unit: 'money', window: 'period',
    state: 'measured', figure: 1234.5, reason: '', rows: [], detail: {},
    source: 'GET /commcalc/gp/{period}', ...over,
  } as ReviewItem
}

function review(label: string, kind: string, items: ReviewItem[]) {
  return { scope: { kind, label, store_codes: ['A-1'], note: '' }, week: WEEK, items,
           coverage: { items: items.length, measured: items.filter(i => i.state === 'measured').length,
                       not_measured: items.filter(i => i.state !== 'measured').length } }
}

function pack(over: Partial<ReviewPack> = {}): ReviewPack {
  return {
    as_of: '2026-11-16', period: 'November 2026', week: WEEK,
    company: review('Company (consolidated)', 'company', [item()]),
    markets: [review('Chicago', 'market', [item({ figure: 900 })])],
    unplaced: null, ...over,
  } as ReviewPack
}

describe('a figure nobody measured', () => {
  it('exports blank, never 0', () => {
    expect(cellFigure(item({ state: 'not_measured', figure: null }))).toBe('')
    expect(cellFigure(item({ figure: 0 }))).toBe(0)   // a measured zero IS zero
  })

  it('shows an em dash on screen, never 0', () => {
    expect(screenFigure(item({ state: 'not_measured', figure: null }))).toBe('—')
  })

  it('carries its reason into the file', () => {
    const p = pack({ company: review('Company (consolidated)', 'company',
      [item({ state: 'not_measured', figure: null, reason: 'the GP report could not be read' })]) })
    const row = weeklyReviewSheets(p)[0].rows[0]
    expect(row.Figure).toBe('')
    expect(row.State).toBe(WR_STATE_WORD.not_measured)
    expect(row['Why not measured']).toContain('could not be read')
  })
})

describe('what a figure covers is said in words', () => {
  it('names the window on every row', () => {
    const rows = weeklyReviewSheets(pack())[0].rows
    expect(rows[0].Covers).toBe(WR_WINDOW_WORD.period)
  })

  it('has a word for every window the backend declares', () => {
    for (const w of ['week', 'period', 'today']) expect(WR_WINDOW_WORD[w]).toBeTruthy()
  })
})

describe('the pack reads consolidated first, then each market', () => {
  it('orders the reviews the way the meeting works through them', () => {
    const p = pack({ unplaced: review('Stores with no market', 'unplaced', [item()]) })
    expect(reviewsOf(p).map(r => r.scope.label))
      .toEqual(['Company (consolidated)', 'Chicago', 'Stores with no market'])
  })

  it('is empty rather than guessing when there is no pack', () => {
    expect(reviewsOf(null)).toEqual([])
    expect(weeklyReviewSheets(null)).toEqual([])
  })
})

describe('the detail sheets', () => {
  it('carries the per-store profit rows', () => {
    const profit = item({ rows: [{ store_code: 'A-1', address: '1 First St', revenue: 10,
                                   net_profit: 2 }] })
    const sheets = weeklyReviewSheets(pack({
      company: review('Company (consolidated)', 'company', [profit]), markets: [] }))
    const sheet = sheets.find(s => s.name === 'Profit by store')
    expect(sheet?.rows[0].Store).toBe('A-1')
  })

  it('says whether each carrier declared its own selling window', () => {
    const aging = item({ key: 'aging', label: 'Inventory aging by carrier', unit: 'count',
                         window: 'today', figure: 1,
                         rows: [{ label: 'Total', sell_within_days: 12, declared: true,
                                  devices: 3, missed: 1, owed: 400 },
                                { label: 'Boost', sell_within_days: 60, declared: false,
                                  devices: 1, missed: 0, owed: 50 }] })
    const sheets = weeklyReviewSheets(pack({
      company: review('Company (consolidated)', 'company', [aging]), markets: [] }))
    const rows = sheets.find(s => s.name === 'Inventory aging')!.rows
    expect(rows[0]['Window declared']).toBe('yes')
    expect(rows[1]['Window declared']).toContain('house default')
  })

  it('does not invent a detail sheet for an item nobody measured', () => {
    const sheets = weeklyReviewSheets(pack({
      company: review('Company (consolidated)', 'company',
                      [item({ state: 'not_measured', figure: null, reason: 'no rows' })]),
      markets: [] }))
    expect(sheets.map(s => s.name)).toEqual(['Review'])
  })
})

describe('money is the platform\'s one home, not a second copy', () => {
  it('shows an em dash for an absent figure and a real $0.00 for a measured zero', () => {
    expect(rowMoney(null)).toBe('—')
    expect(rowMoney('')).toBe('—')
    expect(rowMoney(0)).toContain('0.00')
  })

  it('leaves a WORD as itself — a measurement claim is not a money claim', () => {
    expect(rowMoney('no target saved')).toBe('no target saved')
  })

  it('formats a measured money item through the same home', () => {
    expect(screenFigure(item({ unit: 'money', figure: 1234.5 }))).toContain('1,234.50')
  })
})
