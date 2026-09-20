// ── THE TWO WAYS TO CALCULATE EMPLOYEE COMMISSION — one ordered list, dereferenced by every surface ──
// Owner directive 2026-09-20, verbatim: "for employe commissioin structure it shows 2 options… move the
// one from executive mtd on top and the other one at the bottom and show on top that there are 2 ways to
// calculate employee commission: from the reporting on Executive MTD to get paid flat or set up a
// customized commission payout using the following steps — this should be a platform wide fix not just
// from verizon".
//
// This module is the ONE home of that header and that order. The Employee Commission Structure page and
// the Incentive Plans editor (the two surfaces that present the choice) render COMMISSION_WAYS through
// CommissionWaysHeader.tsx; neither spells the header or the option titles itself, so the order cannot
// drift between them (frontend/prove_commission_structure_order.mjs pins it).
//
// The two ways ARE the two values of `commcalc.commission_plan.commission_basis` (mig 298):
//   Option 1  'exec_mtd'  — flat $ per activation type + accessory % over the Executive MTD numbers
//                           (router._commission_mtd_result; `mtd_rates` saved on the plan)
//   Option 2  'rules'     — the default: the rule engine (commission_engine.preview) built step by step
// Nothing here writes that choice: `wayForBasis` only READS a plan's persisted basis so a page can say
// which way the selected plan pays through. Presentation only — no money computation, no migration.
//
// RULE TWO: no carrier, POS or tenant is named. The upload pages that feed Option 1 are not listed here:
// they come from GET /commcalc/report-kinds (`feedsForScreen` in lib/report-kinds.ts — the inverse of
// `showsInFor` over the same `shows_in` / `where` payload), rendered through ScreenLink.
import type { ScreenKey } from '@/components/ScreenLink'

export const COMMISSION_WAYS_HEADER = 'There are 2 ways to calculate employee commission'

/** The persisted values of commission_plan.commission_basis (mig 298). */
export type CommissionBasis = 'exec_mtd' | 'rules'

export type CommissionWay = {
  /** Presentation number — Option 1 is always the Executive MTD flat way. */
  n: 1 | 2
  key: 'exec_mtd_flat' | 'custom_steps'
  /** The commission_plan.commission_basis value a plan carries when it pays through this way. */
  basis: CommissionBasis
  /** The section id on the Employee Commission Structure page. */
  anchor: string
  title: string
  /** One layman sentence: what this way uses to pay. */
  layman: string
  /** The report whose numbers this way pays from (a ScreenLink key), or null when it pays from rules. */
  reads: ScreenKey | null
}

export const COMMISSION_WAYS: readonly CommissionWay[] = [
  {
    n: 1, key: 'exec_mtd_flat', basis: 'exec_mtd', anchor: 'option-1-exec-mtd-flat',
    title: 'Pay flat from Executive MTD reporting',
    layman: 'Each rep is paid a flat $ per activation (one rate per activation type) plus a % of their accessory sales, ' +
      'taken straight from the numbers on the Executive MTD report — what you see on that report is what pays.',
    reads: 'exec_mtd',
  },
  {
    n: 2, key: 'custom_steps', basis: 'rules', anchor: 'option-2-custom-steps',
    title: 'Set up a customised commission payout using the following steps',
    layman: 'Build your own rules — which sale lines qualify, what each pays, tiers, where activations are counted from, ' +
      'what counts as an accessory — then assign the plan to reps and preview the estimate.',
    reads: null,
  },
]

export const EXEC_MTD_FLAT: CommissionWay = COMMISSION_WAYS[0]
export const CUSTOM_STEPS: CommissionWay = COMMISSION_WAYS[1]

/**
 * Which way a plan pays through, from its persisted basis. NULL / '' / anything unknown reads as
 * 'rules' — the mig-298 column default and what the backend collapses an unknown value to.
 */
export function wayForBasis(basis: string | null | undefined): CommissionWay {
  return String(basis || '').trim().toLowerCase() === 'exec_mtd' ? EXEC_MTD_FLAT : CUSTOM_STEPS
}

/** "Option 1 — Pay flat from Executive MTD reporting" — the one spelling of an option's heading. */
export function optionHeading(way: CommissionWay): string {
  return `Option ${way.n} — ${way.title}`
}
