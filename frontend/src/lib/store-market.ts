// THE BROWSER'S ONE HOME FOR "WHICH MARKET IS THIS STORE IN?" (index §13a, §CORE-43)
//
// The backend has answered this question in ONE place since 2026-09-03 — `core.scope`'s
// `store_market_resolver` / `market_by_code`, enforced by `harness_market_resolution_guard.py`,
// which accepts a store CODE, an ADDRESS, a POS synonym or an unambiguous leading street number in
// any case or punctuation, and FAILS CLOSED on ambiguity rather than picking a winner. §13a says,
// verbatim, that *any* code answering this question for a filter, group-by, bucket or option list
// resolves through one of those helpers. The guard only ever scanned the BACKEND.
//
// So thirteen screens in this app each carried their own copy:
//
//     const m: Record<string, string> = {}
//     for (const s of stores) if (s.store_code) m[s.store_code] = s.market || ''
//     ... filterRows(rows, filt, { market: r => m[r.store] || '' })
//
// — a LETTER-FOR-LETTER comparison between a roster code and a store string that a feed, a time
// clock or a human typed. One capital letter, one punctuation mark, or an address where a code was
// expected, and the row's market is '': it stays in every total and vanishes from every per-market
// view. That is the owner's report of 2026-11-12 (LuxeLink October payroll: $110,571.59 total,
// $44,403.09 of it under no market, $37,760.23 of THAT being thirteen reps whose store the clock
// had written `CICERO` against a roster that declares `Cicero`). The money was never wrong; the
// attribution was, silently, and a plausible-looking smaller number is exactly how this class hides.
//
// THIS MODULE IS THE BROWSER'S SINGLE DEREFERENCE OF THAT RULE. It is pure (no React, no fetch), so
// every page reuses it identically and it is unit-provable: `__tests__/store-market.test.ts` holds
// the truth table, against the LIVE LuxeLink and house rosters. `__tests__/store-market-one-home.test.ts`
// FAILS THE BUILD if a fourteenth private copy appears.
//
// IT FOLDS, IT DOES NOT GUESS. Nothing here does prefix, edit-distance or token-overlap matching:
// `3738 26th Street` is not resolved to `3735 26th` even though a human can see what was meant —
// that judgement belongs to the owner in the Store Matching UI, never to a matcher at runtime
// (same doctrine as `core.scope.resolve_store_grant`).
//
// IT CARRIES NO TENANT'S VOCABULARY. The roster is a REQUIRED argument; an empty roster identifies
// nothing and the surface says so, rather than falling back to another tenant's words (owner
// directive 2026-10-30). One tenant's roster can never answer for another's store.
//
// WHY THE BROWSER AND NOT THE SERVER. The pages that need this already hold a SPAN-SCOPED roster
// (`GET /storeops/stores`), and the lookup is built from exactly those rows — so a scoped user
// learns nothing about a store they could not already see. The canonical union (which adds
// `commcalc.store_mapping` / `store_aliases` spellings) is served by `GET /core/markets`, which is
// deliberately NOT span-scoped: a page may pass those rows in instead where its caller is an admin,
// and the fold below is the same either way.

/** A roster row as the store endpoints actually serve it. Both address spellings are read because
 *  `storeops.stores` says `address` and the closing/POS surfaces say `store_address`. */
export type StoreRosterRow = {
  store_code?: string | null
  address?: string | null
  store_address?: string | null
  market?: string | null
  is_active?: boolean | null
}

export type StoreMarketLookup = {
  /** The canonical market for ANY spelling of a store, or '' when this roster cannot say. */
  marketOf: (store: unknown) => string
  /** The distinct markets this lookup can actually bind — the option list and the resolver agree. */
  markets: string[]
}

const AMBIGUOUS = ''

/** THE accent fold, mirroring `account.store_identity.fold_accents`: `'Café' -> 'CAFE'`. A fold that
 *  does not fold accents makes `Martínez` a different identity from `Martinez` (measured defect
 *  2026-10-10), and `String.prototype.toUpperCase` alone leaves the accent in place. */
const foldAccents = (v: string): string => v.normalize('NFD').replace(/[̀-ͯ]/g, '')

/** Comparison form, mirroring `core.scope._squash`: accent-folded, upper-cased, every
 *  non-alphanumeric character dropped. So `'B - 2612'` compares equal to `'B-2612'`, `'ave u'` to
 *  `'Ave U'`, and `'104-08 Lefferts Blvd'` to `'10408 lefferts blvd'`. */
export const squashStore = (v: unknown): string =>
  foldAccents(v == null ? '' : String(v)).toUpperCase().replace(/[^A-Z0-9]/g, '')

const norm = (v: unknown): string => (v == null ? '' : String(v)).trim()

const leadingNumber = (v: unknown): string => {
  const m = /^\s*(\d+)/.exec(v == null ? '' : String(v))
  return m ? m[1] : ''
}

/** Every address spelling a roster row carries (both column names, de-duplicated). */
const addressesOf = (s: StoreRosterRow): string[] => {
  const out: string[] = []
  for (const a of [s.address, s.store_address]) {
    const v = norm(a)
    if (v && !out.includes(v)) out.push(v)
  }
  return out
}

/** THE LOOKUP. Build it once per roster (a `useMemo` on the pages) and ask it per row.
 *
 *  Learning rule, identical to `core.scope.build_store_market_lookup`: the first market learned for
 *  a key wins, a SECOND and DIFFERENT market makes that key ambiguous and it answers '' forever
 *  after — never an arbitrary winner. A key learned with no market stays known-and-marketless, so it
 *  does not fall through to the street-number rule and borrow a neighbour's market. */
export function buildStoreMarket(stores: readonly StoreRosterRow[] | null | undefined): StoreMarketLookup {
  const keyMarket = new Map<string, string>()
  const numMarket = new Map<string, string>()
  const marketSpellings = new Map<string, string>()

  const learn = (map: Map<string, string>, key: string, mk: string) => {
    if (!key) return
    const cur = map.get(key)
    if (cur === undefined) map.set(key, mk)
    else if (cur && mk && cur.toLowerCase() !== mk.toLowerCase()) map.set(key, AMBIGUOUS)
    else if (!cur && mk) map.set(key, mk)
  }

  for (const s of stores || []) {
    const mk = norm(s.market)
    if (mk) {
      const k = mk.toLowerCase()
      if (!marketSpellings.has(k)) marketSpellings.set(k, mk)
    }
    learn(keyMarket, squashStore(s.store_code), mk)
    for (const a of addressesOf(s)) {
      learn(keyMarket, squashStore(a), mk)
      // The street-number rule is a LAST resort for a feed that carries only `3560`, and it is
      // learned only from addresses — a code that happens to be numeric (`957`, `7812`) is already
      // an exact key above.
      if (mk) learn(numMarket, leadingNumber(a), mk)
    }
  }

  const markets = [...marketSpellings.values()].sort((a, b) => a.localeCompare(b))

  const marketOf = (store: unknown): string => {
    const sq = squashStore(store)
    if (!sq) return ''
    const hit = keyMarket.get(sq)
    if (hit !== undefined) return hit   // '' here = known but ambiguous/marketless: fail closed
    const n = leadingNumber(store)
    return n ? (numMarket.get(n) || '') : ''
  }

  return { marketOf, markets }
}

/** The market OPTION list off the same roster the lookup binds, so a picker can never offer a market
 *  the lookup cannot resolve (§13c's rule, in the browser). Distinct, case-folded to the first
 *  spelling seen, sorted, never a blank. */
export function marketsFromRoster(stores: readonly StoreRosterRow[] | null | undefined): string[] {
  return buildStoreMarket(stores).markets
}
