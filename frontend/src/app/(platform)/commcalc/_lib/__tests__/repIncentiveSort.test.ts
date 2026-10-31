// WHAT THE REP INCENTIVE REPORT DECLARES SORTABLE (owner ask 2026-10-31, index §CORE-28).
//
// Owner, verbatim: *"in the Rep Incentive Report the month range shows , i need it to be sorted by
// employee store etc , add this to our standard filter bar and update the index"*.
//
// The report's two tables each DECLARE their sortable columns here, and the page hands the
// declaration to the shared bar and to `sortStandard`. Nothing about the comparison lives here.
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { sortSelectOptions, sortStandard, type SortFieldDecl, type StandardFilterValue } from '@/lib/standard-filters'
import { emptyStandardFilter } from '@/lib/standard-filters'
import { RANGE_SORT_FIELDS, REP_SORT_FIELDS } from '../repIncentiveSort'

const sortedBy = <T,>(rows: T[], field: string, dir: 'asc' | 'desc', fields: SortFieldDecl[]) =>
  sortStandard(rows, { ...emptyStandardFilter(), sort: { field, dir } } as StandardFilterValue, fields)

// Three tenants, three vocabularies — a Boost B-code, a LuxeLink street address, a Vzone shop name —
// and reps whose names arrive in mixed case from two different feeds.
const REPS = [
  { epay_salesperson: 'zahra k', storeops_name: 'Zahra Khan', store: 'B-1115', market: 'Queens', tier: 2, kpis_met: 3, subtotal: 400, total_payout: 900.25 },
  { epay_salesperson: 'ALI R', storeops_name: '', store: '104-08 Lefferts Blvd', market: 'Queens', tier: 1, kpis_met: 5, subtotal: 100, total_payout: 1200 },
  { epay_salesperson: 'mahmoud', storeops_name: 'Mahmoud S', store: 'Vzone Hempstead', market: 'Nassau', tier: 3, kpis_met: 0, subtotal: 0, total_payout: 50 },
]
const who = (rows: { storeops_name?: string; epay_salesperson: string }[]) => rows.map(r => r.storeops_name || r.epay_salesperson)

describe('REP_SORT_FIELDS — the breakdown table', () => {
  it('offers employee, store and market, which is what the owner asked for by name', () => {
    const labels = REP_SORT_FIELDS.map(f => f.label)
    expect(labels).toContain('Employee')
    expect(labels).toContain('Store')
    expect(labels).toContain('Market')
  })

  it('sorts by employee on the name the table SHOWS, not the raw feed id', () => {
    // The table prints `storeops_name || epay_salesperson`. Sorting on the other one would order the
    // rows by a string the user cannot see — a sort nobody can check is not a sort.
    expect(who(sortedBy(REPS, 'rep', 'asc', REP_SORT_FIELDS))).toEqual(['ALI R', 'Mahmoud S', 'Zahra Khan'])
  })

  it('sorts by store and by market over each tenant own spelling', () => {
    expect(sortedBy(REPS, 'store', 'asc', REP_SORT_FIELDS).map(r => r.store))
      .toEqual(['104-08 Lefferts Blvd', 'B-1115', 'Vzone Hempstead'])
    expect(sortedBy(REPS, 'market', 'desc', REP_SORT_FIELDS).map(r => r.market))
      .toEqual(['Queens', 'Queens', 'Nassau'])
  })

  it('sorts money and counts numerically, biggest first when asked', () => {
    expect(sortedBy(REPS, 'total_payout', 'desc', REP_SORT_FIELDS).map(r => r.total_payout)).toEqual([1200, 900.25, 50])
    expect(sortedBy(REPS, 'tier', 'asc', REP_SORT_FIELDS).map(r => r.tier)).toEqual([1, 2, 3])
    expect(sortedBy(REPS, 'kpis_met', 'desc', REP_SORT_FIELDS).map(r => r.kpis_met)).toEqual([5, 3, 0])
  })

  it('words every direction, so the dropdown never says asc', () => {
    const labels = sortSelectOptions(REP_SORT_FIELDS).map(o => o.label).join(' ')
    expect(labels).not.toMatch(/asc|desc/i)
    expect(sortSelectOptions(REP_SORT_FIELDS)).toContainEqual({ value: 'rep:asc', label: 'Employee (A–Z)' })
  })
})

describe('RANGE_SORT_FIELDS — the month range table the owner was looking at', () => {
  const ROWS = [
    { range_month: '2026-09', epay_salesperson: 'zahra k', storeops_name: 'Zahra Khan', store: 'B-1115', total_payout: 10, final_payout: 9 },
    { range_month: '2026-09', epay_salesperson: 'ALI R', storeops_name: '', store: '9 Nostrand Ave', total_payout: 30, final_payout: 30 },
    { range_month: '2026-08', epay_salesperson: 'mahmoud', storeops_name: 'Mahmoud S', store: 'Vzone Hempstead', total_payout: 20, final_payout: 20 },
  ]
  it('sorts employee and store inside the range, which is what "sorted by employee store" means here', () => {
    expect(who(sortedBy(ROWS, 'rep', 'asc', RANGE_SORT_FIELDS))).toEqual(['ALI R', 'Mahmoud S', 'Zahra Khan'])
    expect(sortedBy(ROWS, 'store', 'asc', RANGE_SORT_FIELDS).map(r => r.store))
      .toEqual(['9 Nostrand Ave', 'B-1115', 'Vzone Hempstead'])
  })
  it('does NOT offer a Month sort, because the table is already sectioned by month', () => {
    // A control that reorders nothing is worse than no control: it reads as broken.
    expect(RANGE_SORT_FIELDS.map(f => f.field)).not.toContain('range_month')
  })
  it('sorts the pay columns the range table actually prints', () => {
    expect(sortedBy(ROWS, 'total_payout', 'desc', RANGE_SORT_FIELDS).map(r => r.total_payout)).toEqual([30, 20, 10])
    expect(sortedBy(ROWS, 'final_payout', 'asc', RANGE_SORT_FIELDS).map(r => r.final_payout)).toEqual([9, 20, 30])
  })
})

describe('the lock: the page cannot stop dereferencing the shared sort', () => {
  const page = readFileSync(join(process.cwd(), 'src/app/(platform)/commcalc/reports/page.tsx'), 'utf8')
    .split('\n').filter(l => !l.trim().startsWith('//') && !l.trim().startsWith('*')).join('\n')

  it('hands its declaration to the shared bar', () => {
    expect(page).toMatch(/sortFields=\{sortFieldsForTab\}/)
    // and the per-tab declaration behind that one prop — the range grain is not the month grain
    expect(page).toMatch(/sortFieldsForTab\s*=\s*tab === 'range' \? RANGE_SORT_FIELDS : REP_SORT_FIELDS/)
  })

  it('orders BOTH tables through sortStandard, so what is shown is what is exported', () => {
    expect(page).toMatch(/sortStandard\(\s*filterRows\(reps/)
    expect(page).toMatch(/sortStandard\(\s*filterRows\(\(rangeData/)
  })

  it('writes no comparator of its own — one sort rule, fleet-wide', () => {
    expect(page).not.toMatch(/localeCompare\(.*\)\s*\*\s*/)
    expect(page).not.toMatch(/\bnew Intl\.Collator\b/)
  })

  it('keeps the month-range window IN the shared bar, next to the sort (owner 2026-10-31)', () => {
    // *"it should be next to tht filters"*. From-month / to-month ARE the range tab's filters, so they
    // ride the bar's documented `right` slot instead of a row of their own below the tabs — and there
    // is only ONE of each control, so the window cannot be set in two places.
    expect(page).toMatch(/\{tab === 'range' && rangeWindowControls\}/)
    expect(page.match(/From month/g) || []).toHaveLength(1)
    expect(page.match(/To month/g) || []).toHaveLength(1)
    expect(page.match(/onClick=\{loadRange\}/g) || []).toHaveLength(1)
  })

  it('toggles the SAME state from its column headers (one sort, two affordances)', () => {
    expect(page).toMatch(/toggleStandardSort\(filt, f\)/)
    expect(page).toMatch(/SortableTh/)
  })
})
