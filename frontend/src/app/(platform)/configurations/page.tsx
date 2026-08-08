'use client'
import Link from 'next/link'
import { useAuth } from '@/lib/auth-context'

// One place for EVERY settings / configuration screen. Hub-and-spoke: each card links to the page
// that still owns that config — those pages keep their own routes + in-module nav entries; this is
// the single landing that ties them all together so an admin can set the whole thing up from here.
// `adminOnly` items (platform-level) show only to super-admins.
type Item = { href: string; icon: string; label: string; desc: string; adminOnly?: boolean }
type Group = { title: string; desc: string; items: Item[] }

const GROUPS: Group[] = [
  {
    title: 'Company & Setup',
    desc: 'The first things to set when onboarding a company.',
    items: [
      { href: '/admin/tenant-settings', icon: '⚙️', label: 'Company Setup', desc: 'Pay period & work-week, and your CARRIER(S) — picking a carrier shows only the areas that apply.' },
      { href: '/admin/org', icon: '🏢', label: 'Org Structure', desc: 'Org levels, units and the managers over each — powers team views + span enforcement.' },
      { href: '/admin/org-chart', icon: '🗺️', label: 'Org Chart', desc: 'Visual org tree.' },
      { href: '/admin/labels', icon: '🏷️', label: 'Menu Labels & Capabilities', desc: 'Rename nav items and turn features on/off per tenant (the admin override for carrier-hidden pages too).' },
    ],
  },
  {
    title: 'Access & Roles',
    desc: 'Logins, roles and employees.',
    items: [
      { href: '/admin/roles', icon: '🔐', label: 'Roles & Access', desc: 'Roles, per-module permissions (incl. AI Assistant), employee add/edit, logins, login enforcement. Also where you open the app AS an employee to reproduce a problem they reported.' },
      { href: '/admin/impersonation', icon: '🕵️', label: 'Sign-in-as Audit', desc: 'Every time an admin viewed the app as an employee: who, whom, how long, and every change they made. Plus how long a session lasts before it ends itself.' },
    ],
  },
  {
    title: 'Communications',
    desc: 'Every channel and who receives what — email (Resend) & WhatsApp (Meta, live). Configure all platform messaging here.',
    items: [
      { href: '/notify', icon: '📣', label: 'Notify & Channels', desc: 'Email + WhatsApp channel status, report recipients, recurring subscriptions, on-demand send, delivery history.' },
      { href: '/notify/report-recipients', icon: '📬', label: 'Report Recipients', desc: 'Route each report to specific people and channels (email / WhatsApp).' },
      { href: '/closing/cash-config', icon: '🔔', label: 'Cash & Closing Alerts', desc: 'Who gets the daily cash-pickup + missing-closing alerts, and on which channel.' },
      { href: '/helpdesk/settings', icon: '🎫', label: 'Helpdesk Notifications', desc: 'Per-category ticket notifications (new ticket, updates) — also under Helpdesk.' },
    ],
  },
  {
    title: 'Commissions & Pay',
    desc: 'Rates, plans, mappings and expense rules that drive payouts and the P&L.',
    items: [
      { href: '/commcalc/settings', icon: '⚙️', label: 'Commission Rates', desc: 'SPIFFs, tiered comp, KPI targets, custom payment components.' },
      { href: '/commcalc/payout-schedules', icon: '📆', label: 'Payout Schedules', desc: 'Multi-month carrier payout schedules + observed-plan MRC.' },
      { href: '/commcalc/commission-plans', icon: '🧮', label: 'Commission Plans', desc: 'Configurable payout engine — user-defined line-match → payout kinds + tiers.' },
      { href: '/commcalc/accessory-flags', icon: '🔖', label: 'Accessory Flag Rules', desc: 'Threshold + default chargeback for accessories sold over $X.' },
      { href: '/commcalc/mapping', icon: '🗂️', label: 'Mappings (stores, carriers, items, reps)', desc: 'All mapping & alias screens in one place.' },
      { href: '/commcalc/expenses', icon: '🏪', label: 'Store Expenses', desc: 'Per-store fixed & variable expense lines (carry forward monthly).' },
      { href: '/commcalc/onboarding', icon: '🚀', label: 'Commission Onboarding', desc: 'Guided setup for a carrier’s commission rules.' },
      { href: '/commcalc/implementation', icon: '🧩', label: 'Implementation', desc: 'Implementation checklist / status.' },
    ],
  },
  {
    title: 'Targets',
    desc: 'Daily target rates per store and per rep.',
    items: [
      { href: '/commcalc/targets/settings', icon: '🎚️', label: 'Target Settings', desc: 'Activations / upgrades / accessories / BYOD targets by store.' },
      { href: '/commcalc/targets/rep-map', icon: '🧑‍💼', label: 'Rep Mapping', desc: 'Map source rep names/usernames to employees.' },
    ],
  },
  {
    title: 'Data Imports & Connectors',
    desc: 'Vendor portals, sweep schedules and credentials.',
    items: [
      { href: '/commcalc/connectors', icon: '🔌', label: 'Connectors', desc: 'Vendor portal registry, sweep status, credentials, run-now.' },
      { href: '/commcalc/ftp-imports', icon: '🔁', label: 'FTP Auto-Import', desc: 'Pull report files a vendor FTP-pushes; route each filename to its parser.' },
      { href: '/commcalc/email-imports', icon: '📧', label: 'Email Auto-Import', desc: 'Poll a mailbox a vendor emails reports to; route each attachment to its parser.' },
      { href: '/commcalc/upload', icon: '📁', label: 'Auto-Imports & Uploads', desc: 'Per-source schedules, last-loaded status, manual uploads.' },
      { href: '/commcalc/upload/wizard', icon: '🧭', label: 'Upload Wizard', desc: 'Guided per-report upload (exact report name + source link).' },
      { href: '/closing/imports', icon: '🔄', label: 'Closing Auto-Import', desc: 'Google Sheet closing import — sheet id, tab, schedule, service account.' },
      { href: '/admin/import-health', icon: '📡', label: 'Import Health', desc: 'Every feed this company expects, how often it should arrive, when it last did — and what to fix. Drives the admin login alert.' },
    ],
  },
  {
    title: 'StoreOps & Closing',
    desc: 'Stores, schedules, visits and daily cash.',
    items: [
      { href: '/storeops/admin', icon: '🛠️', label: 'StoreOps Admin', desc: 'Stores (code / address / market / target), payscale, bulk tools.' },
      { href: '/storeops/visits/settings', icon: '🧾', label: 'Visit Checklist', desc: 'Store-visit checklist items, categories and order.' },
      { href: '/closing/cash-config', icon: '💰', label: 'Cash Setup', desc: 'Closing deadline + gate, per-store closer, alert recipients, cash-aging.' },
      { href: '/closing/pickup', icon: '💵', label: 'Cash Pickup Recipient', desc: 'Who receives the daily cash-envelope pickup notifications.' },
    ],
  },
  {
    title: 'People (HR)',
    desc: 'Onboarding packet and employee intake.',
    items: [
      { href: '/hr/onboarding', icon: '🧑‍🎓', label: 'HR Onboarding & Intake', desc: 'Onboarding tasks/docs, the comprehensive intake packet, sensitive-field config.' },
    ],
  },
  {
    title: 'Helpdesk',
    desc: 'Ticketing configuration.',
    items: [
      { href: '/helpdesk/settings', icon: '🎫', label: 'Helpdesk Settings', desc: 'Categories, priorities, statuses, teams, custom fields, per-category notify.' },
    ],
  },
  {
    title: 'Platform (Super-Admin)',
    desc: 'Cross-tenant operator controls.',
    items: [
      { href: '/admin/tenants', icon: '🏢', label: 'Companies & Platform Admins', desc: 'Create/manage tenants; platform super-admins.', adminOnly: true },
      { href: '/admin/billing', icon: '💳', label: 'Billing & Platform Costs', desc: 'Price each tenant, invoices, MRR/ARR + your run-cost and break-even cost per tenant.', adminOnly: true },
    ],
  },
]

export default function ConfigurationsPage() {
  const { user } = useAuth()
  const isSuper = !!user?.super_admin
  const groups = GROUPS
    .map(g => ({ ...g, items: g.items.filter(it => !it.adminOnly || isSuper) }))
    .filter(g => g.items.length > 0)

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⚙️ Configurations</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Every settings &amp; configuration screen in one place — set the whole company up from here, or jump
          to any of these from inside its own module. Start with <b>Company Setup</b> (pay period + carriers).
        </p>
      </div>

      <div style={{ display: 'grid', gap: 22 }}>
        {groups.map(g => (
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
