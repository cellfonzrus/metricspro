'use client'
import Link from 'next/link'

// One place for every settings / configuration screen. Hub-and-spoke (mirrors the Uploads page):
// each card links to the page that still owns that config — those pages keep their own routes and
// nav entries; this is the single "Configurations" landing that ties them together.
type Item = { href: string; icon: string; label: string; desc: string }
type Group = { title: string; desc: string; items: Item[] }

const GROUPS: Group[] = [
  {
    title: 'Commissions & Pay',
    desc: 'Rates, mappings and expense rules that drive payouts and the P&L.',
    items: [
      { href: '/commcalc/settings', icon: '⚙️', label: 'Commission Rates', desc: 'SPIFFs, tiered comp, KPI targets, custom payment components.' },
      { href: '/commcalc/accessory-flags', icon: '🔖', label: 'Accessory Flag Rules', desc: 'Threshold + default chargeback for accessories sold over $X.' },
      { href: '/commcalc/mapping', icon: '🗂️', label: 'Mappings (stores, carriers, items, reps)', desc: 'All mapping & alias screens in one place.' },
      { href: '/commcalc/expenses', icon: '🏪', label: 'Store Expenses', desc: 'Per-store fixed & variable expense lines (carry forward monthly).' },
    ],
  },
  {
    title: 'Targets',
    desc: 'Daily target rates per store and per rep.',
    items: [
      { href: '/commcalc/targets/settings', icon: '🎚️', label: 'Target Settings', desc: 'Activations / upgrades / accessories / BYOD targets by store.' },
    ],
  },
  {
    title: 'Data Imports & Connectors',
    desc: 'Vendor portals, sweep schedules and credentials.',
    items: [
      { href: '/commcalc/connectors', icon: '🔌', label: 'Connectors', desc: 'Vendor portal registry, sweep status, credentials, run-now.' },
      { href: '/commcalc/ftp-imports', icon: '🔁', label: 'FTP Auto-Import', desc: 'Pull report files a vendor (B2B Soft, etc.) FTP-pushes; route each filename to its parser.' },
      { href: '/commcalc/email-imports', icon: '📧', label: 'Email Auto-Import', desc: 'Poll a mailbox a vendor emails reports to; route each attachment to its parser. Alternative to FTP.' },
      { href: '/commcalc/upload', icon: '📁', label: 'Auto-Imports & Uploads', desc: 'Per-source schedules, last-loaded status, manual uploads.' },
      { href: '/commcalc/upload/wizard', icon: '🧭', label: 'Upload Wizard', desc: 'Guided per-report upload (exact report name + source link).' },
      { href: '/closing/imports', icon: '🔄', label: 'Closing Auto-Import', desc: 'Google Sheet closing import — sheet id, tab, schedule, service account.' },
    ],
  },
  {
    title: 'StoreOps',
    desc: 'Stores, schedules and store-visit configuration.',
    items: [
      { href: '/storeops/admin', icon: '🛠️', label: 'StoreOps Admin', desc: 'Stores (code / address / market / target), payscale, bulk tools.' },
      { href: '/storeops/visits/settings', icon: '🧾', label: 'Visit Checklist', desc: 'Store-visit checklist items, categories and order.' },
      { href: '/closing/pickup', icon: '💵', label: 'Cash Pickup Recipient', desc: 'Who receives the daily cash-envelope pickup notifications.' },
    ],
  },
  {
    title: 'Access & Notifications',
    desc: 'Logins, roles, employees and report delivery.',
    items: [
      { href: '/admin/roles', icon: '🔐', label: 'Roles & Access', desc: 'Roles, permissions, employee add / edit / delete, logins, login enforcement.' },
      { href: '/notify', icon: '📤', label: 'Notify', desc: 'Recipients, recurring report subscriptions, delivery history.' },
    ],
  },
]

export default function ConfigurationsPage() {
  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⚙️ Configurations</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Every settings &amp; configuration screen in one place — mappings, rates, data connectors, access and delivery.
        </p>
      </div>

      <div style={{ display: 'grid', gap: 22 }}>
        {GROUPS.map(g => (
          <section key={g.title}>
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 15, fontWeight: 700 }}>{g.title}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>{g.desc}</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {g.items.map(it => (
                <Link key={it.href} href={it.href} className="card" style={{
                  padding: 14, display: 'flex', gap: 12, alignItems: 'flex-start',
                  textDecoration: 'none', color: 'inherit', border: '1px solid var(--border)',
                }}>
                  <div style={{ fontSize: 22, lineHeight: 1 }}>{it.icon}</div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 2 }}>{it.label}</div>
                    <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.4 }}>{it.desc}</div>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
