'use client'
import Link from 'next/link'

// One landing page for every mapping/alias screen (mirrors the Configurations & Uploads hubs).
// Each card links to the page that owns that mapping; those pages keep their own routes + nav entries.
type Item = { href: string; icon: string; label: string; desc: string }

const ITEMS: Item[] = [
  { href: '/commcalc/store-match', icon: '🏬', label: 'Store Matching', desc: 'Map raw store names from data feeds to a canonical store.' },
  { href: '/commcalc/carrier-mapping', icon: '🗺️', label: 'Carrier Mapping', desc: 'Add carriers + map their comp categories → Residual / Commission / SPIFF / Reimbursement.' },
  { href: '/commcalc/column-mapping', icon: '🧩', label: 'Column Mapping', desc: 'Map any carrier’s spreadsheet columns → our fields. Config-driven ingest for new carriers.' },
  { href: '/commcalc/item-mapping', icon: '🧩', label: 'Item / Model Mapping', desc: 'Classify items (accessory vs phone) + set phone model — the SU sheet. Drives Accessory Flags.' },
  { href: '/commcalc/ma-product-class', icon: '🏷️', label: 'MA Product Name Classification', desc: 'Classify each MA Daily Tx product name — commission vs spiff vs residual vs bill payment vs device sale. Exact match, owner-confirmed.' },
  { href: '/commcalc/accessory-definition', icon: '🎧', label: 'Accessory Definition', desc: 'What YOUR company counts as an accessory — map your own items/departments, plus the “department or category says accessory” rule. Read-only vs money; compares every existing classifier.' },
  { href: '/commcalc/rep-aliases', icon: '🔗', label: 'Rep Aliases', desc: 'Merge name variants of the same rep into one canonical person.' },
  { href: '/commcalc/asset/hotsheet-recon', icon: '🏷️', label: 'Pricing Hotsheet', desc: 'Carrier promo pricing by device model — expected-vs-paid reconciliation.' },
]

export default function MappingHubPage() {
  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🗂️ Mapping</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Every mapping &amp; alias screen in one place — stores, carriers, items/models, reps and device pricing.
        </p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
        {ITEMS.map(it => (
          <Link key={it.href} href={it.href} className="card" style={{
            padding: 14, display: 'flex', gap: 12, alignItems: 'flex-start',
            textDecoration: 'none', color: 'inherit', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 22, lineHeight: 1 }}>{it.icon}</div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 2 }}>{it.label}</div>
              <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.4 }}>{it.desc}</div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
