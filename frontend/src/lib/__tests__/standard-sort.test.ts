// SORT-BY AS A CAPABILITY OF THE STANDARD FILTER BAR (owner ask 2026-10-31, index §CORE-28).
//
// Owner, verbatim: *"in the Rep Incentive Report the month range shows , i need it to be sorted by
// employee store etc , add this to our standard filter bar and update the index"*.
//
// WHAT THESE TESTS LOCK, and why each matters:
//  • the sort is ONE state on the StandardFilterValue, so a bar dropdown and a header click can
//    never disagree about how the table is ordered (two sort states is the duplicate defect);
//  • the COMPARISON is not re-derived — `sortStandard` dereferences `lib/table-sort`, which is the
//    one home for "how two cells compare" since 2026-08-10;
//  • the sortable fields are a REQUIRED argument that the PAGE declares. There is no house default
//    list of column names, because a default list is one report's (or one tenant's) vocabulary
//    standing in for everyone's — the class of defect §COMMCALC-49.10 names. Nothing declared means
//    nothing offered, and the bar says so by rendering no control at all.
import { describe, expect, it } from 'vitest'
import {
  emptyStandardFilter, isStandardFilterActive, isStandardSortActive, parseSortSelect,
  sortFieldCell, sortSelectOptions, sortSelectValue, sortStandard, toggleStandardSort,
  type SortFieldDecl, type StandardFilterValue,
} from '@/lib/standard-filters'

type Row = { rep: string; store: string; market: string; payout: number; month: string }

const FIELDS: SortFieldDecl[] = [
  { field: 'rep', label: 'Employee', get: r => r.rep },
  { field: 'store', label: 'Store', get: r => r.store },
  { field: 'market', label: 'Market', get: r => r.market },
  { field: 'payout', label: 'Payout', get: r => r.payout, kind: 'number' },
  { field: 'month', label: 'Month', get: r => r.month, kind: 'date' },
]

const ROWS: Row[] = [
  { rep: 'Zahra',  store: 'B-1115', market: 'Queens',   payout: 100.5, month: '2026-09-01' },
  { rep: 'ali',    store: 'B-0002', market: 'Brooklyn', payout: 900,   month: '2026-07-01' },
  { rep: 'Mahmoud', store: 'B-1598', market: 'Queens',  payout: 100.5, month: '2026-08-01' },
]
const names = (rows: Row[]) => rows.map(r => r.rep)

const withSort = (field: string, dir: 'asc' | 'desc'): StandardFilterValue =>
  ({ ...emptyStandardFilter(), sort: { field, dir } })

describe('sortStandard — the pure sort behind the bar', () => {
  it('sorts by a declared text field, case-insensitively (a rep is not filed under their capital letter)', () => {
    expect(names(sortStandard(ROWS, withSort('rep', 'asc'), FIELDS))).toEqual(['ali', 'Mahmoud', 'Zahra'])
    expect(names(sortStandard(ROWS, withSort('rep', 'desc'), FIELDS))).toEqual(['Zahra', 'Mahmoud', 'ali'])
  })

  it('sorts by store and market — the two the owner named alongside employee', () => {
    expect(names(sortStandard(ROWS, withSort('store', 'asc'), FIELDS))).toEqual(['ali', 'Zahra', 'Mahmoud'])
    expect(names(sortStandard(ROWS, withSort('market', 'asc'), FIELDS))).toEqual(['ali', 'Zahra', 'Mahmoud'])
  })

  it('sorts money numerically and keeps ties in the report own order (stable)', () => {
    expect(names(sortStandard(ROWS, withSort('payout', 'desc'), FIELDS))).toEqual(['ali', 'Zahra', 'Mahmoud'])
    // Zahra and Mahmoud both 100.50 — the order the report produced survives the sort.
    expect(names(sortStandard(ROWS, withSort('payout', 'asc'), FIELDS))).toEqual(['Zahra', 'Mahmoud', 'ali'])
  })

  it('returns the rows UNTOUCHED when no sort is chosen, so a report default order is the default', () => {
    expect(sortStandard(ROWS, emptyStandardFilter(), FIELDS)).toBe(ROWS)
  })

  it('refuses a field the page did not declare — it cannot invent an ordering it was not given', () => {
    expect(sortStandard(ROWS, withSort('secret_margin', 'asc'), FIELDS)).toBe(ROWS)
  })

  it('offers nothing at all when the page declared nothing (no house default column list)', () => {
    expect(sortSelectOptions([])).toEqual([])
    expect(sortStandard(ROWS, withSort('rep', 'asc'), [])).toBe(ROWS)
  })

  it('never mutates the rows it was handed', () => {
    const copy = [...ROWS]
    sortStandard(ROWS, withSort('rep', 'asc'), FIELDS)
    expect(ROWS).toEqual(copy)
  })

  it('reads a cell only through the declaration (sortFieldCell is the one accessor)', () => {
    expect(sortFieldCell(FIELDS, ROWS[0], 'store')).toBe('B-1115')
    expect(sortFieldCell(FIELDS, ROWS[0], 'nope')).toBeUndefined()
  })
})

describe('the control value: one state, two affordances', () => {
  it('round-trips a sort through the dropdown value', () => {
    expect(sortSelectValue({ field: 'store', dir: 'desc' })).toBe('store:desc')
    expect(sortSelectValue(null)).toBe('')
    expect(parseSortSelect('store:desc')).toEqual({ field: 'store', dir: 'desc' })
    expect(parseSortSelect('')).toBeNull()
    expect(parseSortSelect('store')).toEqual({ field: 'store', dir: 'asc' })
    expect(parseSortSelect('store:sideways')).toEqual({ field: 'store', dir: 'asc' })
  })

  it('labels each direction in the words of the thing being sorted, not asc/desc', () => {
    const opts = sortSelectOptions(FIELDS)
    expect(opts).toContainEqual({ value: 'rep:asc', label: 'Employee (A–Z)' })
    expect(opts).toContainEqual({ value: 'rep:desc', label: 'Employee (Z–A)' })
    expect(opts).toContainEqual({ value: 'payout:desc', label: 'Payout (high → low)' })
    expect(opts).toContainEqual({ value: 'month:desc', label: 'Month (newest first)' })
  })

  it('a header click walks the SAME state asc → desc → off', () => {
    const base = emptyStandardFilter()
    const a = toggleStandardSort(base, 'store')
    expect(a.sort).toEqual({ field: 'store', dir: 'asc' })
    const b = toggleStandardSort(a, 'store')
    expect(b.sort).toEqual({ field: 'store', dir: 'desc' })
    expect(toggleStandardSort(b, 'store').sort).toBeNull()
    // a different column starts fresh, ascending
    expect(toggleStandardSort(b, 'rep').sort).toEqual({ field: 'rep', dir: 'asc' })
    // and toggling the sort changes nothing else about the filter
    expect({ ...a, sort: undefined }).toEqual({ ...base, sort: undefined })
  })

  it('a sort is NOT a narrowing: isStandardFilterActive stays false so a page never claims rows were hidden', () => {
    const s = withSort('rep', 'asc')
    expect(isStandardFilterActive(s)).toBe(false)
    expect(isStandardSortActive(s)).toBe(true)
    expect(isStandardSortActive(emptyStandardFilter())).toBe(false)
  })
})

describe('every tenant, measured against its own values', () => {
  // Three tenants spell a store three ways (a Boost B-code, a street address, a named shop). The
  // sort must order each tenant's OWN values correctly, and one tenant's spelling must never decide
  // another's order. `asNumber` is anchored for exactly this reason: '104-08 Lefferts Blvd' is a
  // street, not the number 104.
  const tenantRows = [
    { rep: 'r1', store: '104-08 Lefferts Blvd', market: 'Queens', payout: 1, month: '2026-09-01' },
    { rep: 'r2', store: '9 Nostrand Ave', market: 'Brooklyn', payout: 2, month: '2026-09-01' },
    { rep: 'r3', store: 'Vzone Hempstead', market: 'Nassau', payout: 3, month: '2026-09-01' },
    { rep: 'r4', store: 'B-1115', market: 'Queens', payout: 4, month: '2026-09-01' },
  ]
  it('orders each tenant store spelling as text, never as a number it happens to start with', () => {
    const got = sortStandard(tenantRows, withSort('store', 'asc'), FIELDS).map(r => r.store)
    expect(got).toEqual(['9 Nostrand Ave', '104-08 Lefferts Blvd', 'B-1115', 'Vzone Hempstead'])
  })
  it('a tenant with no value for the sorted field sinks to the bottom in BOTH directions', () => {
    const rows = [
      { rep: 'has', store: 'B-1115', market: 'Queens', payout: 5, month: '2026-09-01' },
      { rep: 'none', store: '', market: '', payout: 0, month: '' },
    ]
    expect(sortStandard(rows, withSort('store', 'asc'), FIELDS).map(r => r.rep)).toEqual(['has', 'none'])
    expect(sortStandard(rows, withSort('store', 'desc'), FIELDS).map(r => r.rep)).toEqual(['has', 'none'])
  })
})
