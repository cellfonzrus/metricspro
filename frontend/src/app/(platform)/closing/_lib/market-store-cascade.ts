// PROMOTED to `@/lib/market-store-cascade` (2026-08-10). The owner's 2026-08-04 cascade refinement of
// RULE FIVE is fleet-wide and retroactive, so the logic can no longer live inside one module's `_lib` —
// commcalc reports need the identical cascade. This file stays as a re-export so every closing page's
// existing `../_lib/market-store-cascade` import keeps resolving to the SAME single implementation
// (one module, not a fork). New code should import from `@/lib/market-store-cascade` directly.
export {
  NO_MARKET_ID, marketsFromStores, cascadeStores, pruneSelectedStores, resolveStoreCodes,
} from '@/lib/market-store-cascade'
export type { StoreOpt } from '@/lib/market-store-cascade'
