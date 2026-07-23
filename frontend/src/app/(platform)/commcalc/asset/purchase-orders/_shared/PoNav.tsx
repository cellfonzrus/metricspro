'use client'
// Small in-module nav for the Purchase Orders sub-area (mod-asset-owned; NOT the platform sidebar — that
// registration is a NEEDS CORE escalation, see the asset handoff). Every PO page renders this at the top.
import Link from 'next/link'

const TABS: { href: string; label: string }[] = [
  { href: '/commcalc/asset/purchase-orders', label: '📑 Proposed PO' },
  { href: '/commcalc/asset/purchase-orders/receiving', label: '📥 Receiving' },
  { href: '/commcalc/asset/purchase-orders/tally', label: '✅ Sold Tally' },
  { href: '/commcalc/asset/purchase-orders/aging', label: '⏳ Unsold Aging' },
  { href: '/commcalc/asset/purchase-orders/vendors', label: '🏭 Vendors' },
]

export default function PoNav({ active }: { active: string }) {
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
      {TABS.map(t => {
        const isActive = t.href === active
        return (
          <Link key={t.href} href={t.href} className="btn" style={{
            textDecoration: 'none',
            background: isActive ? 'var(--accent, #2563eb)' : undefined,
            color: isActive ? '#fff' : undefined,
            borderColor: isActive ? 'var(--accent, #2563eb)' : undefined,
          }}>{t.label}</Link>
        )
      })}
      <div style={{ flex: 1 }} />
      <Link href="/commcalc/asset" className="btn btn-secondary" style={{ textDecoration: 'none' }}>← Asset Ledger</Link>
    </div>
  )
}
