// Market -> Store CASCADE — pure logic behind <MarketStorePicker> (OWNER DIRECTIVE 2026-08-04, in-chat:
// "need market and then selectable store should be an option" + "the store picker needs to have check
// box under the drop down to pick multiple stores"). This is a REFINEMENT of RULE FIVE (§3d), not a
// parallel filter system: it drives the same `StandardFilterValue.markets`/`.stores` string arrays every
// closing filter surface already reads (`filterRows`/`matchesStandardFilter` in `@/lib/standard-filters`
// already AND-match store+market independently, which is exactly "market selected + no store picked =
// whole market included" — see the owner's Q2 verdict). What was MISSING was the store picker's OPTION
// LIST narrowing to the selected market(s) — this module supplies that, framework-free (no React) so it
// is reused identically by every page and unit-provable.
//
// Stores with NO resolvable market are bucketed under `NO_MARKET_ID` ('(no market)', matching the
// existing convention in closing/page.tsx's own marketOptions) — NEVER silently hidden when a real
// market is selected, and NEVER silently shown either: picking "(no market)" is how you see them. This
// avoids the "my store just disappeared" trap a blanket hide-if-unmapped rule would create.

export const NO_MARKET_ID = '(no market)'

export type StoreOpt = { id: string; label: string; market?: string | null }

const norm = (v: any): string => (v == null ? '' : String(v)).trim()
const foldKey = (v: any): string => norm(v).toLowerCase()

/** Distinct, sorted market list derived from a store roster (pick-don't-type §3b — never a free list).
 *  Appends the `(no market)` bucket only when at least one store actually lacks a market. */
export function marketsFromStores(stores: StoreOpt[]): { id: string; label: string }[] {
  const m = new Map<string, string>()
  let hasUnmapped = false
  for (const s of stores) {
    const v = norm(s.market)
    if (v) { const k = foldKey(v); if (!m.has(k)) m.set(k, v) }
    else hasUnmapped = true
  }
  const out = [...m.values()].sort().map(v => ({ id: v, label: v }))
  if (hasUnmapped) out.push({ id: NO_MARKET_ID, label: NO_MARKET_ID })
  return out
}

/** The CASCADE itself: stores narrowed to the selected market(s). No markets selected -> every store
 *  (no narrowing at all — the "no market picked yet" state). `NO_MARKET_ID` selected -> stores with no
 *  market attribute. Case/whitespace-insensitive, mirrors `standard-filters.ts`'s own `foldKey` rule. */
export function cascadeStores(stores: StoreOpt[], selectedMarkets: string[]): StoreOpt[] {
  if (!selectedMarkets.length) return stores
  const wanted = new Set(selectedMarkets.map(foldKey))
  return stores.filter(s => {
    const v = norm(s.market)
    if (!v) return wanted.has(foldKey(NO_MARKET_ID))
    return wanted.has(foldKey(v))
  })
}

/** Drops any currently-selected store id that the cascade would no longer show (a market change that
 *  narrows the store list must never leave an invisible/inconsistent selection behind). Never ADDS
 *  anything — pure prune. A store id absent from `stores` entirely (e.g. an id-space this picker wasn't
 *  given, or one the caller resolves separately) is left untouched — this function only prunes ids it can
 *  actually classify, so it can never wrongly drop a selection it has no information about. */
export function pruneSelectedStores(stores: StoreOpt[], selectedMarkets: string[], selectedStoreIds: string[]): string[] {
  if (!selectedMarkets.length || !selectedStoreIds.length) return selectedStoreIds
  const known = new Set(stores.map(s => s.id))
  const visible = new Set(cascadeStores(stores, selectedMarkets).map(s => s.id))
  const pruned = selectedStoreIds.filter(id => !known.has(id) || visible.has(id))
  return pruned.length === selectedStoreIds.length ? selectedStoreIds : pruned
}

/** The RESOLVED store-code set a server call should send (owner Q2: "the filter sent to the backend is
 *  the resolved store set — market expansion happens picker-side"). Explicit store picks win (they are
 *  already cascade-narrowed by construction); with markets picked but no explicit store, expands to
 *  every store in those markets ("select a market and no specific stores = the whole market"); with
 *  neither, returns [] (no store-scoping — caller decides what "no filter" means for its own endpoint). */
export function resolveStoreCodes(stores: StoreOpt[], selectedMarkets: string[], selectedStoreIds: string[]): string[] {
  if (selectedStoreIds.length) return selectedStoreIds
  if (selectedMarkets.length) return cascadeStores(stores, selectedMarkets).map(s => s.id)
  return []
}
