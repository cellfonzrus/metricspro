// Sales Feed Recon — the page's COPY as pure functions (owner 2026-09-20: "if we have declared there is
// no b2b in verizon why does this message show — it should customize the message based on what POS is
// being used"). The POS name is the tenant's term (lib/report-labels.ts usePosTerm); whether a daily
// feed kind even exists for that POS is the registry's answer (lib/report-kinds.ts feedFor). Nothing here
// spells a vendor, and prove_pos_term_copy.mjs drives these over the real transpiled TS.
import { notApplicableCopy, type FeedApplies } from '@/lib/report-kinds'

/** The upload route key the daily feed is uploaded through (registry row sales_imei_phone names it). */
export const DAILY_FEED_ROUTE = 'daily_sales'

export type SalesReconState = {
  applies: FeedApplies   // step 1 — is a daily-feed report kind visible for the declared POS?
  hasFeed: boolean       // step 2 — has a daily feed landed for the period?
  pos: string
  posDeclared: boolean
  period: string
}

export const dailyFeedLabel = (pos: string) => `Daily ${pos} feed`

export function salesReconTabs(pos: string) {
  return [
    { key: 'missing_in_monthly', label: 'Missing in Monthly', color: '#dc2626',
      blurb: `In the daily ${pos} feed but NOT in the authoritative monthly file — a real revenue / commission leak, or a same-day void. Investigate first.` },
    { key: 'amount_mismatch', label: 'Amount Mismatch', color: '#d97706',
      blurb: 'Same transaction in both, but the totals differ (price change, partial line, or a tender/return delta).' },
    { key: 'missing_in_daily', label: 'Missing in Daily', color: '#2563eb',
      blurb: 'In the monthly file but the daily feed never captured it — usually a feed-coverage gap (feed down that day), lower severity.' },
  ]
}

/**
 * The empty-state banner, or null when the feed is loaded. Step 1 wins: a POS with no daily-feed kind
 * is told so instead of being invited to upload one; step 2 is the original message with the term.
 */
export function salesReconEmptyState(s: SalesReconState): { kind: 'not_applicable' | 'not_loaded' | 'checking'; text: string } | null {
  if (s.applies === 'checking') return { kind: 'checking', text: 'Checking which report kinds apply to your POS…' }
  if (s.applies === 'not_defined') {
    return { kind: 'not_applicable', text: notApplicableCopy({ pos: s.pos, posDeclared: s.posDeclared, feedNoun: 'daily feed', kindNoun: 'daily-feed',
      purpose: 'reconcile the monthly file against' }) }
  }
  if (s.hasFeed) return null
  return { kind: 'not_loaded',
    text: `No daily ${s.pos} feed loaded for ${s.period} yet. Once the daily feed lands (via FTP Auto-Import or a manual ` +
      `“${DAILY_FEED_ROUTE}” upload), every transaction is reconciled here against the monthly file. The monthly totals ` +
      'below are shown for reference.' }
}
