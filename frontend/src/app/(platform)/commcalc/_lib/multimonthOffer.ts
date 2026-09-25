// IS MULTI-MONTH PAY CONFIGURED FOR THIS ORG? — the frontend half of THE predicate (owner 2026-09-25): "right
// now the rep comisison is shown with plan incentive and multi month, if multi month is not confgured then it
// should not be shown as an available option."
//
// The answer comes from GET /commcalc/multimonth/status (backend multimonth_config.load: an ACTIVE schedule in
// payout_schedule or plan_installment_schedule for the org — per-org config rows, never a carrier or tenant
// name), read through `useMultimonthStatus` (./multimonth.ts). Every surface that OFFERS the multi-month option
// asks `multimonthOffered` / `multimonthRows` here; harness_multimonth_offer_lock.py fails the build on a
// surface that offers it without asking. PURE, imports nothing (frontend/tools/multimonth-offer-proof.mjs).
//
// HIDDEN MEANS NOT OFFERED — NEVER MONEY DROPPED: a non-zero multi-month amount always shows, whatever the
// config says; the state 'off_with_money' carries a note the page prints beside it.

export type MultimonthStatus = {
  configured: boolean
  offered: boolean
  state: 'configured' | 'off' | 'off_with_money'
  money: Record<string, number>
  note: string | null
}

/** Until the answer arrives, or if it cannot be read, the option stays OFFERED (today's behaviour) —
 *  a failed read must never hide an option that may be live. */
export const MULTIMONTH_UNKNOWN: MultimonthStatus = { configured: true, offered: true, state: 'configured', money: {}, note: null }

export function multimonthOffered(s: MultimonthStatus | null | undefined): boolean {
  return (s ?? MULTIMONTH_UNKNOWN).offered !== false
}

/** Which multi-month rows a rep's pay card (and its export) shows. PURE.
 *  · a non-zero amount ALWAYS shows (money is never hidden);
 *  · the $0 sale-triggered row — the drill path — shows only when the option is offered, and only when the
 *    residual engine is not the sole payer (a second $0 row would be meaningless). */
export function multimonthRows(s: MultimonthStatus | null | undefined, instSale: number, instResid: number):
    { sale: boolean; resid: boolean } {
  const resid = (instResid || 0) !== 0
  const sale = (instSale || 0) !== 0 || (multimonthOffered(s) && !resid)
  return { sale, resid }
}
