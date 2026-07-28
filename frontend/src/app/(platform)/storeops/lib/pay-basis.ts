// Salary pay-basis conversion helpers (owner directive 2026-07-27) — client-side, for the setup UI's
// LIVE preview only ("$52,000/yr = $1,000.00 per weekly period"). This is deliberately SIMPLE, pure
// arithmetic (the conversion table below), unlike ../lib/pay-period.ts's period-BOUNDARY math (work-
// week anchoring, biweekly_anchor snapping, payday resolution) which that file's own docstring
// explains is NEVER reimplemented client-side. There is no equivalent boundary/anchor logic here to
// drift from the server — the conversion factor for a given (pay_basis, pay_period_type) pair is a
// fixed constant, identical everywhere it's computed. The AUTHORITATIVE per-period/prorated dollar
// figure a rep is actually paid always comes from the backend (payroll_salary.py, via GET /payroll /
// GET /compensation) — this preview is informational only, never submitted or relied on for money.
export type PayBasis = 'hourly' | 'weekly' | 'monthly' | 'annual'
export const PAY_BASES: PayBasis[] = ['hourly', 'weekly', 'monthly', 'annual']
export const PAY_BASIS_LABEL: Record<PayBasis, string> = {
  hourly: 'Hourly', weekly: 'Weekly salary', monthly: 'Monthly salary', annual: 'Annual salary',
}

/** The SAME conversion table as backend/app/modules/storeops/payroll_salary.py convert_to_period_pay:
 *    weekly  -> ×1 (weekly period) / ×2 (biweekly period)
 *    monthly -> ×12/52 (weekly) / ×12/26 (biweekly)
 *    annual  -> /52 (weekly) / /26 (biweekly)
 *  Returns null for 'hourly' or a non-positive/unusable amount. Rounds to cents (half-up, matching
 *  the backend's Decimal ROUND_HALF_UP — JS floating point at 2dp is close enough for a live preview;
 *  the backend recomputes the real figure independently). */
export function periodPayPreview(basis: PayBasis, amount: number | null | undefined,
                                  payPeriodType: string | null | undefined): number | null {
  if (basis === 'hourly' || amount == null || !isFinite(amount) || amount <= 0) return null
  const biweekly = payPeriodType === 'biweekly'
  let factor: number
  if (basis === 'weekly') factor = biweekly ? 2 : 1
  else if (basis === 'monthly') factor = biweekly ? 12 / 26 : 12 / 52
  else factor = biweekly ? 1 / 26 : 1 / 52   // annual
  return Math.round(amount * factor * 100) / 100
}

/** Human label for the live preview, e.g. "= $1,000.00 per weekly period". */
export function periodPayPreviewLabel(basis: PayBasis, amount: number | null | undefined,
                                       payPeriodType: string | null | undefined): string {
  const v = periodPayPreview(basis, amount, payPeriodType)
  if (v == null) return ''
  const periodWord = payPeriodType === 'biweekly' ? 'biweekly' : 'weekly'
  return `= $${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} per ${periodWord} period`
}
