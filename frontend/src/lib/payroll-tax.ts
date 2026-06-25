// Client-side payroll tax engine (StoreOps payroll spec, Section 8). The API returns raw inputs
// (hours, rate, W-4 settings); the browser computes withholding so stored figures never go stale when
// rates change. Rates are flat-rate estimates (the spec explicitly allows "W-4 table OR flat"); exact
// W-4 bracket tables are a future enhancement. Rate constants are grouped here so they can later be
// moved to a config table (SaaS directive) without touching the math.
//
// ⚠️ ESTIMATE for internal visibility — not a substitute for a payroll provider's exact withholding.

export const TAX_RATES = {
  fica_ss: 0.062, fica_ss_wage_base: 168600, // Social Security (employee share), 2024 base
  fica_medicare: 0.0145,                      // Medicare (employee share)
  federal_supplemental: 0.22,                 // flat 22% (skipped / supplemental mode)
  // simplified flat effective federal rate by filing status (hourly-wage approximation)
  federal_by_status: { Single: 0.12, Married: 0.10, HOH: 0.11 } as Record<string, number>,
  federal_allowance_credit: 15,               // $ reduction in withholding per allowance per period
  // state income tax (flat approximations; PA is genuinely flat, others are progressive → estimate)
  state_sit: { NY: 0.0633, NJ: 0.05525, PA: 0.0307, DE: 0.05 } as Record<string, number>,
  ny_disability_rate: 0.005, ny_disability_max: 0.60, // NYS Disability (employee), capped $0.60/wk
  ot_multiplier: 1.5, ot_threshold: 40,       // overtime above 40 hrs (per period passed in)
}

export type W4 = { filing_status: string; allowances: number; state: string; extra_withholding: number; skipped: boolean }

export type PayrollLine = {
  regular_hours: number; ot_hours: number; gross: number
  fica_ss: number; fica_medicare: number; federal: number; state: number; disability: number
  deductions: number; net: number
  employer_fica: number   // employer share (cost, not a deduction)
}

export function computePay(totalHours: number, rate: number, w4: W4): PayrollLine {
  const t = TAX_RATES
  const regular_hours = Math.min(Math.max(totalHours, 0), t.ot_threshold)
  const ot_hours = Math.max(totalHours - t.ot_threshold, 0)
  const gross = round(regular_hours * rate + ot_hours * rate * t.ot_multiplier)

  const fica_ss = round(gross * t.fica_ss)
  const fica_medicare = round(gross * t.fica_medicare)

  let federal: number
  if (w4.skipped) federal = gross * t.federal_supplemental
  else federal = gross * (t.federal_by_status[w4.filing_status] ?? t.federal_by_status.Single)
  federal = round(Math.max(0, federal - (w4.allowances || 0) * t.federal_allowance_credit) + (w4.extra_withholding || 0))

  const state = round(gross * (t.state_sit[(w4.state || 'NY').toUpperCase()] ?? 0))
  const disability = w4.state?.toUpperCase() === 'NY' ? round(Math.min(gross * t.ny_disability_rate, t.ny_disability_max)) : 0

  const deductions = round(fica_ss + fica_medicare + federal + state + disability)
  const net = round(gross - deductions)
  const employer_fica = round(fica_ss + fica_medicare) // employer matches SS + Medicare
  return { regular_hours: round(regular_hours), ot_hours: round(ot_hours), gross, fica_ss, fica_medicare, federal, state, disability, deductions, net, employer_fica }
}

function round(n: number) { return Math.round((n + Number.EPSILON) * 100) / 100 }
