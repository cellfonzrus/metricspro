// THE CASH SPLIT, for a LIVE form — the frontend mirror of `closing/deposit_recon.cash_components`
// (index §47.9). One drawer, three figures:
//
//     total_cash        = the whole drawer, bill payments inside it
//     store_cash        = max(total - bill payments, 0)   register cash, bill payments excluded
//     bill_payment_cash = the bill-payment (processor) cash
//
// WHY A MIRROR EXISTS AT ALL. Every REPORTED figure comes from the server, which dereferences the one
// home — no screen derives these (locked: harness_envelope_receipt_basis.py §I1-I2). A half-filled
// SUBMIT form has no server figure to read: the rep is still typing, and the owner asked for the store
// cash to show "calculated and greyed out" as they do. So the arithmetic has exactly one home on this
// side too, here, named for what it mirrors — never inline in a form.
//
// LOCKED: harness_envelope_receipt_basis.py §I3 fails the build if this file's formulas drift from
// deposit_recon's, if a second copy of the subtraction appears anywhere under frontend/src, or if the
// submit form stops reading this helper.

export type CashBasis = 'total_cash' | 'store_cash' | 'bill_payment_cash'

/** Round to cents the way the backend's `_f` does, so the preview matches what gets stored. */
const cents = (v: number) => Math.round((Number.isFinite(v) ? v : 0) * 100) / 100

/**
 * The figure for every basis at once, keyed by basis — the same shape and the same values as
 * `deposit_recon.cash_components(total_cash, bill_payment_cash)`.
 *
 * The net floors at 0: bill payments recorded above the drawer is bad data, and a negative drawer on
 * a screen is never the honest rendering of it.
 */
export function cashComponents(totalCash: number, billPayCash: number): Record<CashBasis, number> {
  const total = cents(totalCash)
  const billPay = cents(billPayCash)
  return {
    total_cash: total,
    store_cash: cents(Math.max(total - billPay, 0)),
    bill_payment_cash: billPay,
  }
}

/** The net register cash on its own — the figure a submit form shows beside what the rep typed. */
export function storeCashNet(totalCash: number, billPayCash: number): number {
  return cashComponents(totalCash, billPayCash).store_cash
}
