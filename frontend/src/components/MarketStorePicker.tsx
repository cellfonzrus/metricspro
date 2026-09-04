'use client'
import { useEffect, useMemo, useRef } from 'react'
import { CheckboxDropdown } from '@/components/CheckboxDropdown'
import { cascadeStores, marketsFromStores, pruneSelectedStores, NO_MARKET_ID, type StoreOpt } from '@/lib/market-store-cascade'

export type { StoreOpt } from '@/lib/market-store-cascade'

/**
 * MarketStorePicker — THE ONE shared cascade-checkbox picker (OWNER DIRECTIVE 2026-08-04). Pick a
 * market -> the store dropdown narrows to that market's stores (no market picked = every store); both
 * dropdowns are checkbox multi-selects, typing filters (never free text, §3b). One component, not a
 * per-page reimplementation (dispatch requirement).
 *
 * PROMOTED out of `closing/_lib` into the shared components (2026-08-10): the owner's directive is
 * fleet-wide and retroactive ("applies to every store picker fleet-wide"), and commcalc reports need the
 * identical cascade — a second copy would be exactly the fork the original dispatch was avoiding. The
 * closing paths remain as re-export shims, so every existing caller still resolves to THIS file.
 * <StandardFilterBar> renders it in place of its own market/store pickers when given `cascadeStores`.
 *
 * Store options need a `market` field to cascade against — pass the store roster shape (`market-store-
 * cascade.ts`'s `StoreOpt[]`) rather than a plain string/EntityOption list.
 *
 * `marketOptions` (§13c ENUMERATION doctrine, owner 2026-09-04 B-1115/LI): a caller whose endpoint
 * composes the CANONICAL market vocabulary (core.scope.org_market_options — the org's full market
 * universe ∪ the surface's own row stamps) passes it here, and the market dropdown offers that list
 * UNIONED with the roster's own stamps. Without it the dropdown can only offer the markets the
 * loaded roster happens to carry, which is exactly how a market recorded on one vocabulary only
 * goes missing from some reports' filters. Omitted → today's behavior, byte-identical.
 */
export function MarketStorePicker({
  stores, selectedMarkets, onMarketsChange, selectedStores, onStoresChange,
  showMarket = true, marketPlaceholder = 'Markets…', storePlaceholder = 'Stores…',
  marketWidth = 170, storeWidth = 200, marketOptions,
}: {
  stores: StoreOpt[]
  selectedMarkets: string[]
  onMarketsChange: (ids: string[]) => void
  selectedStores: string[]
  onStoresChange: (ids: string[]) => void
  /** Hide the market control (e.g. a page with no market dimension at all) — the store list still
   *  renders (unfiltered, since there's no market to cascade from). Default true. */
  showMarket?: boolean
  marketPlaceholder?: string
  storePlaceholder?: string
  marketWidth?: number
  storeWidth?: number
  /** Canonical market vocabulary from the caller's endpoint (see the note above). Unioned with the
   *  roster's stamps, canonical spelling winning; the "(no market)" sentinel keeps its place last. */
  marketOptions?: string[]
}) {
  // Canonical options first (a market the org HAS is offered even with no roster store loaded yet),
  // then any roster stamp the vocabulary did not name — a spelling that labels real rows is never
  // dropped. Case/whitespace-insensitive dedupe; the sentinel is re-appended last.
  const marketOpts = useMemo(() => {
    const fromStores = marketsFromStores(stores)
    if (!marketOptions?.length) return fromStores
    const seen = new Map<string, { id: string; label: string }>()
    let sentinel: { id: string; label: string } | null = null
    for (const m of marketOptions) {
      const v = String(m || '').trim()
      if (v) seen.set(v.replace(/\s+/g, ' ').toLowerCase(), { id: v, label: v })
    }
    for (const o of fromStores) {
      if (o.id === NO_MARKET_ID) { sentinel = o; continue }
      const k = o.id.replace(/\s+/g, ' ').toLowerCase()
      if (!seen.has(k)) seen.set(k, o)
    }
    const out = [...seen.values()].sort((a, b) => a.label.localeCompare(b.label))
    return sentinel ? [...out, sentinel] : out
  }, [stores, marketOptions])
  const storeOpts = useMemo(() => {
    const cascaded = cascadeStores(stores, selectedMarkets)
    // Sublabel shows the market only while it's NOT already the narrowing dimension (once a market is
    // picked every visible store shares it — repeating it in every row is noise).
    return selectedMarkets.length ? cascaded.map(s => ({ id: s.id, label: s.label }))
      : cascaded.map(s => ({ id: s.id, label: s.label, sublabel: s.market || undefined }))
  }, [stores, selectedMarkets])

  // Auto-prune: a market change that narrows the store list drops any now-invisible selected store —
  // never silently keeps a selection the user can no longer see/deselect. Guarded against firing on
  // every render (only when the prune actually changes something) and against re-pruning on a `stores`
  // roster refresh alone (only market changes trigger it — a slow-loading roster must never wipe a
  // selection the user already made before the roster arrived).
  const prevMarkets = useRef<string[]>(selectedMarkets)
  useEffect(() => {
    const marketsChanged = prevMarkets.current.join('') !== selectedMarkets.join('')
    prevMarkets.current = selectedMarkets
    if (!marketsChanged) return
    const pruned = pruneSelectedStores(stores, selectedMarkets, selectedStores)
    if (pruned !== selectedStores) onStoresChange(pruned)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedMarkets])

  return (
    <>
      {showMarket && marketOpts.length > 0 && (
        <CheckboxDropdown options={marketOpts} value={selectedMarkets} onChange={onMarketsChange}
          placeholder={marketPlaceholder} width={marketWidth} ariaLabel="Filter by market" />
      )}
      {storeOpts.length > 0 && (
        <CheckboxDropdown options={storeOpts} value={selectedStores} onChange={onStoresChange}
          placeholder={storePlaceholder} width={storeWidth} ariaLabel="Filter by store" />
      )}
    </>
  )
}

export default MarketStorePicker
