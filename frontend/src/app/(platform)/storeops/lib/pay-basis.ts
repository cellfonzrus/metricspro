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

// ── HOURS BASIS — WHY an hours figure is the figure it is (index §14y) ──────────────────────────
// Factored out of storeops/payroll/page.tsx on 2026-11-27 (owner: *"fix the card now across the
// paltform"*), because `/storeops/reports` shows the same "Actual Hrs" column and had NO reason
// beside it, so the same number read as measured on one screen and unexplained on the other. One
// map, every surface that shows the figure.
//
// THESE ARE LABELS, NOT MEANINGS. The meaning is the server's — `payroll_hours_basis.basis_reason`
// ships a full sentence on the row (`hours_basis_reason`) and that is what a reader is shown in a
// drill-down or a tooltip. These short words are for a COLUMN, where the sentence will not fit. A
// basis with no entry falls back to its own underscored name rather than rendering blank: an
// unexplained basis is a defect to see, not to hide.
export const HOURS_BASIS_LABEL: Record<string, string> = {
  clocked: 'Clocked in',
  clocked_corrected: 'Corrected by hand',
  clocked_reconciled: 'Clock records reconciled',
  clocked_override: 'Overridden, with a reason',
  clocked_trimmed_to_schedule: 'Trimmed to the schedule',
  measured_zero: 'Measured — nothing worked',
  schedule_opt_in: 'From the schedule (ticked)',
  not_measured_unpaid: 'Not measured — unpaid',
  punch_unusable: 'Punch unusable — reported',
}

/** The column text for one row's hours basis. `mixed` is said OUT LOUD rather than resolved: a
 *  period with punched days AND scheduled days has no single basis, and printing the dominant one
 *  alone would be the tidier lie. Returns '' when the server sent no basis (an older backend),
 *  so the column reads blank instead of inventing a reason. */
export function hoursBasisLabel(basis?: string | null, mixed?: boolean | null): string {
  if (!basis) return ''
  const label = HOURS_BASIS_LABEL[basis] || String(basis).replace(/_/g, ' ')
  return mixed ? `${label} · mixed` : label
}
