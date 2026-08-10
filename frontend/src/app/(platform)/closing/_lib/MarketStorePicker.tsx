// PROMOTED to `@/components/MarketStorePicker` (2026-08-10) — see the note in `./market-store-cascade`.
// Re-export only, so the closing pages keep their existing import path and there is still exactly ONE
// cascade picker. New code should import from `@/components/MarketStorePicker`.
export { MarketStorePicker, default } from '@/components/MarketStorePicker'
export type { StoreOpt } from '@/lib/market-store-cascade'
