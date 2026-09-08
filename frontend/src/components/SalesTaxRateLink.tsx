'use client'
// ── ONE link to where a sales-tax RATE is actually set ───────────────────────────────────────────
// Owner report 2026-09-07: "the rate to fix the sales tax link is not there on top of that page, in
// addition to the finance or set up store module."
//
// The editor already exists and is good — components/pos/TaxCodesSection (store > market > org-wide
// precedence, the uncovered-stores banner, bulk set). What did not exist was any way to REACH it from
// the two screens where a wrong rate is noticed: Tax Collected (Finance) and Store Setup. Nothing is
// re-implemented here; this is a signpost to the one editor, so a second place to type a rate can
// never come into being.
//
// GENERALISED 2026-09-08 (owner: "need to have a link … if the option is presented for any menu").
// This component was the house's first self-gating signpost; the fleet-wide sweep needed the same
// behaviour for ~a dozen destinations, so the mechanism now lives ONCE in `components/ScreenLink`
// (registry of screen → NAV href, the same `canSeeItem` gate, plus `LinkedText` for backend-authored
// prose). This file is kept as the named, single-purpose wrapper its two call sites already use —
// the behaviour is unchanged, and there is still exactly one signpost implementation.
import { Signpost, SCREENS, useCanOpenScreen } from '@/components/ScreenLink'

/** The POS Settings page, anchored at its Sales Tax section (TaxCodesSection carries the id). */
export const SALES_TAX_HREF = SCREENS.sales_tax.href

/** Can this viewer open the sales-tax editor? Mirrors the sidebar's gate exactly. */
export function useCanEditTaxRates(): boolean {
  return useCanOpenScreen('sales_tax')
}

/**
 * Small inline link: "Set / fix a sales-tax rate →". Renders nothing for a viewer who may not open
 * the editor, so a store manager reading a tax report is not sent to a 403.
 */
export default function SalesTaxRateLink({ label, title }: { label?: string; title?: string }) {
  return <Signpost to="sales_tax" icon="💵" label={label || 'Set / fix a sales-tax rate'} title={title} />
}
