// THE BROWSER'S STORE→MARKET LOOKUP, PROVED (index §13a, §CORE-43).
//
// The defect this starts from (owner, 2026-11-12): *"payroll for luxlink shows 110571.59 for october
// but when you do per market it does not total up chicago is 41207 and Ny is 24966, where is the rest
// and which reps are not assigned a market"*.
//
// MEASURED LIVE, LuxeLink, October 2026, by running the real `GET /storeops/payroll` producer against
// production: scheduled pay $110,571.59, of which the per-market view credited Chicago $41,201.67 and
// NY $24,966.83 and left **$44,403.09 under no market at all**. $37,760.23 of that was thirteen reps
// whose store the time clock had written in CAPITALS — `CICERO`, `LAWRENCE`, `ARMITAGE`, `NOSTRAND`,
// `CHICAGO HEIGHTS`, `BELMONT`, `3352 26TH`, `3735 26TH`, `CERMARK` — against a roster that declares
// `Cicero`, `lawrence`, `Armitage`, `Nostrand`, `Chicago heights`, `Belmont`, `3352 26th`,
// `3735 26th`, `Cermark`. Thirteen screens each built their own `{ [s.store_code]: s.market }` map and
// compared it to the row's store LETTER FOR LETTER, so one capital letter meant no market.
//
// THE CLASS, not the instance: a store spelling is free text wherever a feed, a clock or a human put
// it, and "which market is this store in?" has ONE answer per org. The backend has had that one home
// since 2026-09-03 (§13a `core.scope.store_market_resolver` — case-, punctuation- and
// address-tolerant, fail-closed on ambiguity). The browser had thirteen weaker copies. This module is
// the browser's single dereference of the same rule, and `store-market-one-home.test.ts` fails the
// build if a fourteenth copy appears.
import { describe, it, expect } from 'vitest'
import { buildStoreMarket, marketsFromRoster, type StoreRosterRow } from '../store-market'

// The LIVE LuxeLink roster, 2026-11-12 (storeops.stores, org 854f6d7b…). Verbatim spellings.
const LUXELINK: StoreRosterRow[] = [
  { store_code: 'Cicero', address: '2317 Cicero Cicero', market: 'Chicago' },
  { store_code: 'Cermark', address: '2414 Cermak Chicago', market: 'Chicago' },
  { store_code: 'Narragansett', address: '2640 Narragansett Chicago', market: 'Chicago' },
  { store_code: 'lawrence', address: '3248 Lawrence Chicago', market: 'Chicago' },
  { store_code: '3352 26th', address: '3352 26th Chicago', market: 'Chicago' },
  { store_code: '3735 26th', address: '3735 26th Chicago', market: 'Chicago' },
  { store_code: 'Grand', address: '3966 Grand Chicago', market: 'Chicago' },
  { store_code: 'Diversey', address: '4640 Diversey Chicago', market: 'Chicago' },
  { store_code: 'Armitage', address: '4801 Armitage Chicago', market: 'Chicago' },
  { store_code: 'Belmont', address: '5601 Belmont Chicago', market: 'Chicago' },
  { store_code: 'Chicago heights', address: '639 Lincoln Chicago Heights', market: 'Chicago' },
  { store_code: 'Irving Park', address: '6500 Irving Park Chicago', market: 'Chicago' },
  { store_code: 'kedzie', address: '18226 Kedzie Hazel Crest', market: 'Chicago' },
  { store_code: 'QV', address: '21880 Hempstead Ave', market: 'NY' },
  { store_code: 'Nostrand', address: '3560 Nostrand Ave', market: 'NY' },
  { store_code: '7812', address: '7812 Bergenline', market: 'NY' },
  { store_code: '957', address: '957 Pennsylvania Ave', market: 'NY' },
  { store_code: 'Ave U', address: '902 Ave U', market: 'NY', is_active: false },
  { store_code: 'Utica', address: '531 Utica Ave', market: 'NY' },
  { store_code: 'Lefferts', address: '104-08 Lefferts Blvd', market: 'NY' },
]

// The house org (Cellfonz R Us) — a DIFFERENT vocabulary, for the leakage proof.
const HOUSE: StoreRosterRow[] = [
  { store_code: 'B-1115', address: '1115 Liberty Ave', market: 'LI' },
  { store_code: 'B-1598', address: '1598 Broadway', market: 'Brooklyn' },
]

describe('the thirteen reps October lost — every capitalised spelling resolves', () => {
  const lk = buildStoreMarket(LUXELINK)
  // store spelling as the clock wrote it -> the market the roster declares
  const CAPS: [string, string][] = [
    ['CICERO', 'Chicago'],
    ['LAWRENCE', 'Chicago'],
    ['ARMITAGE', 'Chicago'],
    ['NOSTRAND', 'NY'],
    ['CHICAGO HEIGHTS', 'Chicago'],
    ['BELMONT', 'Chicago'],
    ['3352 26TH', 'Chicago'],
    ['3735 26TH', 'Chicago'],
    ['CERMARK', 'Chicago'],
    ['GRAND', 'Chicago'],
    ['DIVERSEY', 'Chicago'],
    ['UTICA', 'NY'],
    ['LEFFERTS', 'NY'],
    ['IRVING PARK', 'Chicago'],
    ['KEDZIE', 'Chicago'],
    ['AVE U', 'NY'],
  ]
  it.each(CAPS)('%s -> %s', (spelling, market) => {
    expect(lk.marketOf(spelling)).toBe(market)
  })

  it('still answers the exactly-spelled code (nothing that worked stops working)', () => {
    for (const s of LUXELINK) expect(lk.marketOf(s.store_code!)).toBe(s.market)
  })
})

describe('an ADDRESS in the store column resolves too (PR #684: the clock writes addresses)', () => {
  const lk = buildStoreMarket(LUXELINK)
  it('the roster address, as written', () => {
    expect(lk.marketOf('531 Utica Ave')).toBe('NY')
    expect(lk.marketOf('3560 Nostrand Ave')).toBe('NY')
    expect(lk.marketOf('4640 Diversey Chicago')).toBe('Chicago')
  })
  it('the address with case and punctuation drift', () => {
    expect(lk.marketOf('531 UTICA AVE')).toBe('NY')
    expect(lk.marketOf('104-08 lefferts blvd')).toBe('NY')
    expect(lk.marketOf('10408 Lefferts Blvd')).toBe('NY')
  })
  it('an unambiguous leading street number, which is all a feed sometimes carries', () => {
    expect(lk.marketOf('3560')).toBe('NY')          // 3560 Nostrand Ave
    expect(lk.marketOf('3738 26TH STREET')).toBe('')  // 3738 is nobody's number — never guessed
  })
})

describe('it never guesses, and it never leaks', () => {
  it('an unknown spelling is empty, not a nearby store', () => {
    const lk = buildStoreMarket(LUXELINK)
    expect(lk.marketOf('Unassigned')).toBe('')
    expect(lk.marketOf('Floating')).toBe('')
    expect(lk.marketOf('')).toBe('')
    expect(lk.marketOf(null)).toBe('')
    expect(lk.marketOf(undefined)).toBe('')
  })
  it('one tenants vocabulary never answers for another (every feature is for EVERY tenant)', () => {
    const lk = buildStoreMarket(LUXELINK)
    const house = buildStoreMarket(HOUSE)
    expect(lk.marketOf('B-1115')).toBe('')
    expect(lk.marketOf('1115 Liberty Ave')).toBe('')
    expect(house.marketOf('CICERO')).toBe('')
    expect(house.marketOf('Utica')).toBe('')
    // and the house roster still answers its own, in any spelling
    expect(house.marketOf('b - 1115')).toBe('LI')
    expect(house.marketOf('1115 LIBERTY AVE')).toBe('LI')
  })
  it('an empty roster identifies NOTHING rather than falling back to house words', () => {
    const none = buildStoreMarket([])
    expect(none.marketOf('CICERO')).toBe('')
    expect(none.marketOf('B-1115')).toBe('')
    expect(none.markets).toEqual([])
  })
  it('two markets claiming one spelling fails CLOSED, like the backend resolver', () => {
    const lk = buildStoreMarket([
      { store_code: 'Shared', address: '1 A St', market: 'Chicago' },
      { store_code: 'shared', address: '2 B St', market: 'NY' },
    ])
    expect(lk.marketOf('SHARED')).toBe('')
    expect(lk.marketOf('Shared')).toBe('')
  })
  it('two stores on one street number drop the number rule, keeping the exact ones', () => {
    const lk = buildStoreMarket([
      { store_code: 'A', address: '100 First Ave', market: 'Chicago' },
      { store_code: 'B', address: '100 Second Ave', market: 'NY' },
    ])
    expect(lk.marketOf('100')).toBe('')
    expect(lk.marketOf('100 First Ave')).toBe('Chicago')
  })
})

describe('accents fold, as they do in the one backend fold', () => {
  it('a store spelled with an accent is the same store', () => {
    const lk = buildStoreMarket([{ store_code: 'Café', address: '5 Café St', market: 'Chicago' }])
    expect(lk.marketOf('CAFE')).toBe('Chicago')
    expect(lk.marketOf('café')).toBe('Chicago')
  })
})

describe('the market OPTION list comes off the same roster', () => {
  it('distinct, sorted, no blanks', () => {
    expect(marketsFromRoster(LUXELINK)).toEqual(['Chicago', 'NY'])
  })
  it('folds a market spelled two ways to the first spelling, never two options', () => {
    expect(marketsFromRoster([
      { store_code: 'a', market: 'Chicago' },
      { store_code: 'b', market: 'chicago' },
      { store_code: 'c', market: '  Chicago ' },
      { store_code: 'd', market: null },
    ])).toEqual(['Chicago'])
  })
  it('a lookup exposes the same list it can bind', () => {
    expect(buildStoreMarket(LUXELINK).markets).toEqual(['Chicago', 'NY'])
  })
})

describe('the roster row shape every caller actually has', () => {
  it('reads store_address as well as address (closing/pos surfaces spell it that way)', () => {
    const lk = buildStoreMarket([{ store_code: 'X', store_address: '9 Ninth St', market: 'NY' }])
    expect(lk.marketOf('9 Ninth St')).toBe('NY')
    expect(lk.marketOf('X')).toBe('NY')
  })
  it('a codeless roster row still teaches its address', () => {
    const lk = buildStoreMarket([{ address: '77 Only Address Rd', market: 'Chicago' }])
    expect(lk.marketOf('77 Only Address Rd')).toBe('Chicago')
  })
  it('a store with no market is known but marketless, not unknown', () => {
    const lk = buildStoreMarket([{ store_code: 'NoMkt', address: '1 Nowhere', market: null }])
    expect(lk.marketOf('NOMKT')).toBe('')
    expect(lk.markets).toEqual([])
  })
})
