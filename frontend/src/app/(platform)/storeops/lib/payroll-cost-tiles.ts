// WHAT THIS PAY PERIOD COSTS THE COMPANY — the payroll report's two burden tiles (index §STOREOPS-08).
//
// Owner, 2026-11-12: *"in the payroll report add anopthe rtil for the payroll tax paid for that
// period and a another tile shows the total of payroll and payroll tax as a cost of payroll to teh
// company"*.
//
// THE FIGURES ARE THE SERVER'S. The employer tax rule has one home —
// `backend/app/modules/storeops/payroll_expenses.compute_payroll_tax_from_wages`, the same rule the
// HR Payroll Expenses page books from — and `GET /storeops/payroll` stamps `employer_tax` and
// `employer_cost` onto every row it pays. Nothing here applies, knows or guesses a rate: this module
// SUMS the rows that are on screen, because the payroll report filters in the browser (store,
// market, employee) and a tile has to report the population the reader is actually looking at.
//
// ABSENCE IS NEVER ZERO (§CORE-17.3i). Three different things produce a row with no burden key, and
// all three must read as "nothing to show" rather than as $0.00 — which would tell the owner this
// payroll cost the company nothing:
//   · a person the report did not pay (nothing measured, not paid from the schedule);
//   · a PAY-GATED caller, whose money keys are deleted server-side (mig 434) — so the tiles are
//     never rendered for them at all, exactly as the pay tiles beside them are not;
//   · an older backend that does not serve the keys yet, where the tiles simply do not appear.
// `available` is how the screen tells those apart from a real zero.

/** A payroll row, named by the three keys this fold reads and nothing else — a caller passes its own
 *  richer row type (`visibleRows`) straight in, and a test passes a bare object. Optional on purpose:
 *  the burden keys are ABSENT on a row the report did not pay and on a pay-gated caller's rows. */
export type BurdenRow = {
  actual_pay?: number | null
  employer_tax?: number | null
  employer_cost?: number | null
}

export type EmployerTotals = {
  /** Employer payroll tax over the rows on screen. */
  tax: number
  /** Pay + employer tax over the same rows — what the period costs the company. */
  cost: number
  /** How many rows carried a burden figure at all. */
  counted: number
  /** Did ANY row carry one? False means "do not render these tiles", never "$0.00". */
  available: boolean
}

const cents = (n: number): number => Math.round(n * 100) / 100

/** Σ of the server's own burden figures over the rows currently on screen. Rounded ONCE at the end,
 *  so a long period cannot accumulate a visible drift out of per-row rounding. */
export function employerTotals(rows: readonly BurdenRow[] | null | undefined): EmployerTotals {
  let tax = 0
  let cost = 0
  let counted = 0
  for (const r of rows || []) {
    if (!r || r.employer_tax == null || r.employer_cost == null) continue
    tax += Number(r.employer_tax) || 0
    cost += Number(r.employer_cost) || 0
    counted += 1
  }
  return { tax: cents(tax), cost: cents(cost), counted, available: counted > 0 }
}
