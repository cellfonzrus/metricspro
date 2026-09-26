// WHO A PAYOUT SURFACE IS FOR (owner 2026-09-26): "on the employee commission payout report we only need o
// show the line they are getting paid and other lines should be hidden and carrier commission not be
// displayed". ONE declaration per page; the backend's `payout_audience.resolve` decides (a self-scoped rep is
// ALWAYS 'employee' whatever a page asks) and states the result on the payload (`audience`), which is what the
// shared components render from. Import-free; harness_payout_audience_lock.py pins every payout page here.
//
//   · the Rep Incentive report (/commcalc/reports) — THE employee payout report: its plan drill, the Month
//     range, its exports and the Incentive Statement PDFs ask for 'employee' (paid lines only, no carrier $);
//   · /commcalc/commission-explain — "How was this calculated?", the manager diagnostic: every matched line,
//     the ⛔ reasons, Price / GP and the MA cross-reference, unchanged.

export type PayoutAudience = 'employee' | 'manager'

export const PAYOUT_AUDIENCE_OF_PAGE: Record<string, PayoutAudience> = {
  '/commcalc/reports': 'employee',
  '/commcalc/commission-explain': 'manager',
}

/** The `audience=` query parameter a payout page sends (always prefixed with '&'). */
export function audienceParam(page: keyof typeof PAYOUT_AUDIENCE_OF_PAGE | string): string {
  const a = PAYOUT_AUDIENCE_OF_PAGE[page]
  return a ? `&audience=${a}` : ''
}

/** The audience a payload was served for — 'manager' when the backend did not say (today's payload). */
export function servedAudience(payload: any): PayoutAudience {
  return payload?.audience === 'employee' ? 'employee' : 'manager'
}
