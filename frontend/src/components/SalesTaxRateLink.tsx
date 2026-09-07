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
// RBAC: the link renders only for a viewer who could already open /pos/settings from the menu — the
// SAME predicate the sidebar uses (canSeeItem over that page's own NAV entry). It can therefore never
// advertise a page its viewer would be bounced out of.
import Link from 'next/link'
import { useMemo } from 'react'
import { useAuth } from '@/lib/auth-context'
import { NAV, canSeeItem } from '@/lib/rbac'

/** The POS Settings page, anchored at its Sales Tax section (TaxCodesSection carries the id). */
export const SALES_TAX_HREF = '/pos/settings#sales-tax'
const SETTINGS_HREF = '/pos/settings'

/** Can this viewer open the sales-tax editor? Mirrors the sidebar's gate exactly: RBAC applies only
 *  while login is enforced and a session exists (the open app shows everything, as the sidebar does). */
export function useCanEditTaxRates(): boolean {
  const { permissions, session, rbacEnabled } = useAuth()
  const item = useMemo(
    () => NAV.flatMap(g => g.items).find(it => it.href === SETTINGS_HREF) || null, [])
  if (!item) return false
  if (rbacEnabled === false || !session) return true
  return canSeeItem(permissions, item)
}

/**
 * Small inline link: "Set / fix a sales-tax rate →". Renders nothing for a viewer who may not open
 * the editor, so a store manager reading a tax report is not sent to a 403.
 */
export default function SalesTaxRateLink({ label, title }: { label?: string; title?: string }) {
  const allowed = useCanEditTaxRates()
  if (!allowed) return null
  return (
    <Link href={SALES_TAX_HREF} className="btn btn-secondary"
      title={title || 'POS Settings → Sales Tax: set a rate for one store, a whole market, or the company'}
      style={{ fontSize: 12, padding: '5px 10px', whiteSpace: 'nowrap' }}>
      💵 {label || 'Set / fix a sales-tax rate'}
    </Link>
  )
}
