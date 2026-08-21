import type { Metadata } from 'next'
import Link from 'next/link'
import './welcome.css'

// Public marketing page for MetricsPro. Deliberately OUTSIDE the (platform) route group, so it renders
// with no auth, no AuthProvider gating and no sidebar — same as /login, /signup and /privacy. It is a
// pure server component (no 'use client', no hooks, no data fetch): the whole page is static, so it
// costs nothing to serve and can never 500 a signed-out visitor.
//
// SCOPE (owner directive): OVERVIEW ONLY. Each module gets one plain-English line describing what it
// covers — never a feature-by-feature list. The module names and their groupings mirror the NAV
// taxonomy in src/lib/rbac.ts, so this page stays recognizable next to the real sidebar; when a nav
// group is added there, add a card here rather than expanding an existing card into detail.

export const metadata: Metadata = {
  title: 'MetricsPro — The operating system for prepaid retail',
  description:
    'One platform for prepaid dealers: commissions, point of sale, inventory, workforce, daily closing, '
    + 'finance and store intelligence — across every carrier you sell.',
}

const MODULES: { icon: string; name: string; blurb: string }[] = [
  { icon: '📊', name: 'Commission Intelligence',
    blurb: 'Every residual, commission, SPIFF and reimbursement recalculated from carrier data — down to the rep, store and line.' },
  { icon: '💳', name: 'Payout Plans',
    blurb: 'Build the pay plans you actually run — tiered, management, multi-month — and pay from what the engine computed.' },
  { icon: '🛒', name: 'Point of Sale',
    blurb: 'Register, customers, products, activations and special orders for the counter.' },
  { icon: '📦', name: 'Inventory & Assets',
    blurb: 'Device ledger from purchase to sale, with aging, reconciliation and what you owe your distributor.' },
  { icon: '🎯', name: 'CRM',
    blurb: 'Leads, pipeline and follow-ups so the traffic that walked in gets worked.' },
  { icon: '📈', name: 'Targets & Coaching',
    blurb: 'Daily targets by store and rep, tracked against pace, with coaching built on the same numbers.' },
  { icon: '📅', name: 'Workforce',
    blurb: 'Scheduling, time clock, time off, shift swaps and staffing coverage across the fleet.' },
  { icon: '💵', name: 'Payroll & HR',
    blurb: 'People records, onboarding, compliance and payroll fed by hours and earned incentives.' },
  { icon: '🧾', name: 'Daily Closing',
    blurb: 'End-of-day cash, tender and deposit reconciliation, verified up the chain.' },
  { icon: '💼', name: 'Finance',
    blurb: 'Gross profit, expenses, P&L and balance sheet built from live operating data — not a re-keyed export.' },
  { icon: '📹', name: 'Store Vision',
    blurb: 'Live cameras, traffic and heat mapping that put foot traffic next to what was sold.' },
  { icon: '🎁', name: 'Referrals',
    blurb: 'Referral capture, approval and payout, tied back to the sale it produced.' },
  { icon: '✅', name: 'Approvals & Chat',
    blurb: 'Requests route to the right manager, and the conversation about them stays with the record.' },
  { icon: '🎫', name: 'Helpdesk',
    blurb: 'Ticketing for the field, with routine issues resolved automatically where they can be.' },
  { icon: '🔌', name: 'Integrations & Imports',
    blurb: 'Carrier portals, processors and vendor feeds pulled in on a schedule instead of by hand.' },
  { icon: '📤', name: 'Reporting & Notify',
    blurb: 'One report center, plus scheduled delivery of the numbers each person is supposed to see.' },
]

const CAPABILITIES: { name: string; blurb: string }[] = [
  { name: 'Carrier-neutral by design',
    blurb: 'Carriers, categories and pay rules are configured and mapped — never hardcoded — so a new carrier is a setup task, not a rebuild.' },
  { name: 'Multi-company, multi-market',
    blurb: 'Run several companies, markets and stores side by side, with the data kept apart and rolled up when you want it.' },
  { name: 'Roles and access you control',
    blurb: 'Owners, market managers, store managers and reps each see their own slice, down to the report and the column.' },
  { name: 'Native mobile app',
    blurb: 'iOS and Android for the frontline — time clock, POS, CRM and earnings — on the same backend as the web.' },
  { name: 'Built for audit',
    blurb: 'Access logs, approval trails and reconciliation reports, so any number can be traced back to its source.' },
  { name: 'Fits your operation',
    blurb: 'Rename modules, reorder the menu and switch off what you do not use — the platform speaks your company’s language.' },
]

const STEPS: { title: string; blurb: string }[] = [
  { title: 'Connect your sources',
    blurb: 'Carrier portals, payment processors, distributors and your own uploads land in one place on a schedule.' },
  { title: 'Let the engine settle it',
    blurb: 'Sales, pay, inventory and cash are reconciled against each other, and the gaps are flagged instead of buried.' },
  { title: 'Run the business on it',
    blurb: 'Dashboards, targets, payroll and the P&L all read from the same settled numbers — one version of the month.' },
]

export default function WelcomePage() {
  return (
    <div className="mpw">
      <header className="mpw-header">
        <div className="mpw-wrap mpw-header-in">
          <Link href="/welcome" className="mpw-logo">
            <span className="mpw-logo-mark" aria-hidden="true">M</span>
            <span>MetricsPro</span>
          </Link>
          <nav className="mpw-header-nav">
            <a href="#modules">Modules</a>
            <a href="#platform">Platform</a>
            <a href="#how">How it works</a>
            <Link href="/login" className="mpw-btn mpw-btn-primary mpw-btn-sm">Sign in</Link>
          </nav>
        </div>
      </header>

      <main>
        <section className="mpw-hero">
          <div className="mpw-wrap">
            <span className="mpw-eyebrow">Commission intelligence &amp; business operations</span>
            <h1>The operating system for prepaid retail.</h1>
            <p className="mpw-hero-sub">
              MetricsPro pulls your carrier, processor and store data into one engine — then runs the
              commissions, the counter, the inventory, the schedule, the closing and the P&amp;L on top of
              it. One platform, one set of numbers, every store.
            </p>
            <div className="mpw-hero-cta">
              <Link href="/signup" className="mpw-btn mpw-btn-primary">Get started</Link>
              <a href="#modules" className="mpw-btn mpw-btn-ghost">See what’s inside</a>
            </div>
            <div className="mpw-carriers">
              <span>Built for dealers on</span>
              <span>Boost</span><span>Cricket</span><span>Metro</span><span>Total Wireless</span>
            </div>
          </div>
        </section>

        <section className="mpw-section" id="how">
          <div className="mpw-wrap">
            <span className="mpw-kicker">How it works</span>
            <h2>Three moves, every month.</h2>
            <p className="mpw-lede">
              The work that usually spans a dozen spreadsheets and half a dozen portals collapses into a
              single pass.
            </p>
            <div className="mpw-pillars">
              {STEPS.map((s, i) => (
                <div className="mpw-pillar" key={s.title}>
                  <div className="mpw-pillar-step" aria-hidden="true">{i + 1}</div>
                  <h3>{s.title}</h3>
                  <p>{s.blurb}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mpw-section mpw-section-tint" id="modules">
          <div className="mpw-wrap">
            <span className="mpw-kicker">Modules</span>
            <h2>Everything the business runs on.</h2>
            <p className="mpw-lede">
              Each module stands on its own and shares the same data. Turn on what you need now, add the
              rest when you are ready.
            </p>
            <div className="mpw-modules">
              {MODULES.map(m => (
                <article className="mpw-module" key={m.name}>
                  <div className="mpw-module-icon" aria-hidden="true">{m.icon}</div>
                  <h3>{m.name}</h3>
                  <p>{m.blurb}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="mpw-section" id="platform">
          <div className="mpw-wrap">
            <span className="mpw-kicker">The platform</span>
            <h2>One spine under all of it.</h2>
            <p className="mpw-lede">
              The modules are the surface. What makes them work together is everything underneath.
            </p>
            <div className="mpw-caps">
              {CAPABILITIES.map(c => (
                <div className="mpw-cap" key={c.name}>
                  <span className="mpw-cap-dot" aria-hidden="true" />
                  <div>
                    <h3>{c.name}</h3>
                    <p>{c.blurb}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mpw-cta">
          <div className="mpw-wrap">
            <h2>See your own month, settled.</h2>
            <p>
              Bring one period of your carrier and store data, and see what the engine makes of it before
              you change a thing about how you operate.
            </p>
            <div className="mpw-cta-row">
              <Link href="/signup" className="mpw-btn mpw-btn-light">Create your company</Link>
              <Link href="/login" className="mpw-btn mpw-btn-light">Sign in</Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="mpw-footer">
        <div className="mpw-wrap mpw-footer-in">
          <span>MetricsPro — Commission Intelligence &amp; Business Operations Suite.</span>
          <div className="mpw-footer-links">
            <Link href="/signup">Get started</Link>
            <Link href="/login">Sign in</Link>
            <Link href="/privacy">Privacy</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
