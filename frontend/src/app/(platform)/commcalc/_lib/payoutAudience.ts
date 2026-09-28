// WHO A PAYOUT SURFACE IS FOR (owner 2026-09-26, index §6i / §6j). The SERVER decides, from who is looking —
// `payout_audience.resolve`: a self-scoped rep is ALWAYS 'employee' (paid lines only, their own pay only); a
// manager / admin gets the manager report (every line with its ⛔ reason). Pages send NO audience of their own
// ("managers can see rep incentive"); the server states the result on the payload (`audience`), which is what the
// shared components render from, and on /me (`permissions.payout`) for anything drawn before a payload arrives.
//
// CARRIER MONEY (owner 2026-09-28, index §6l): no Rep Incentive payload carries it for ANY audience. Only the
// carrier diagnostic's payload does, served to a holder of the carrier permission and stamped `carrier_view: true`
// by the server — `servedCarrierView` reads that stamp; nothing here decides who may see it.
// Import-free; harness_payout_audience_lock.py fails the build if a payout page forces an audience again.

export type PayoutAudience = 'employee' | 'manager'

/** The audience a payload was served for — 'manager' when the backend did not say (the full payload). */
export function servedAudience(payload: any): PayoutAudience {
  return payload?.audience === 'employee' ? 'employee' : 'manager'
}

/** The audience the server will serve THIS viewer (from /me's `permissions.payout`) — 'manager' when unknown. */
export function viewerAudience(perms: any): PayoutAudience {
  return perms?.payout?.audience === 'employee' ? 'employee' : 'manager'
}

/** Was this payload served in the CARRIER view (the server stamped it after checking the permission)? */
export function servedCarrierView(payload: any): boolean {
  return payload?.carrier_view === true
}
