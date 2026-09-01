'use client'
// Payroll tiled dashboard (Phase W2, owner directive 2026-09-01). The front door for everything
// payroll: replaces hunting through the side menu with one tile hub — the menu entries stay as the
// secondary path. Hub-and-spoke like /configurations: each card links to the page that still owns
// the function (routes, RBAC and REPORT_DIRECTORY entries all unchanged — the hub itself is a
// LANDING, deliberately not registered as a report).
//
// Tile taxonomy is the owner's spec verbatim: Payroll Setup (Add Employees, Onboarding Checklist,
// Compliance, Who pays payroll) · Employee Database · Payroll (Hours Approval, Payroll, Payroll
// Expenses, Payroll Tax — plus the two previously-orphaned surfaces, Payroll Change Log and Salary
// Advances, which had no menu entry at all) · HR Total Comp.
import Link from 'next/link'

type Item = { href: string; icon: string; label: string; desc: string }
type Group = { title: string; desc: string; items: Item[] }

const GROUPS: Group[] = [
  {
    title: 'Payroll Setup',
    desc: 'Get people in and configured — do these first.',
    items: [
      { href: '/hr/people', icon: '🧑‍💼', label: 'Add Employees', desc: 'Add people, set pay & role, create their login.' },
      { href: '/hr/onboarding', icon: '🧩', label: 'Onboarding Checklist', desc: 'Intake packet, tasks and documents for each new hire.' },
      { href: '/hr/compliance', icon: '📋', label: 'Compliance', desc: 'Required documents & certifications — who is missing what.' },
      { href: '/storeops/payroll/payers', icon: '🏦', label: 'Who Pays Payroll', desc: 'The payer registry — which entity pays which store/person (admin only).' },
    ],
  },
  {
    title: 'Employee Database',
    desc: 'The full employee record.',
    items: [
      { href: '/hr/employee-database', icon: '🗄️', label: 'Employee Database', desc: 'Every employee field in one place — profile, pay, documents, history.' },
    ],
  },
  {
    title: 'Payroll',
    desc: 'The pay run, start to finish — all on the same default pay period.',
    items: [
      { href: '/storeops/payroll/approvals', icon: '✅', label: 'Hours Approval', desc: 'DM then HR approve the last complete pay period\'s hours, then send to payers.' },
      { href: '/storeops/payroll', icon: '💵', label: 'Payroll', desc: 'Scheduled vs actual hours & pay for the current pay period.' },
      { href: '/hr/payroll-expenses', icon: '💼', label: 'Payroll Expenses', desc: 'Employer taxes + burden items, rolled into Store Expenses monthly.' },
      { href: '/storeops/payroll-tax', icon: '🧾', label: 'Payroll Tax', desc: 'Withholding estimate (FICA / federal / state) and pay slips for the pay period.' },
      { href: '/storeops/payroll-change-log', icon: '📜', label: 'Payroll Change Log', desc: 'Every manual hours/punch correction — who, when, before → after.' },
      { href: '/storeops/salary-advances', icon: '🤝', label: 'Salary Advances', desc: 'Advances given and their repayment schedule.' },
    ],
  },
  {
    title: 'HR Total Comp',
    desc: 'What each person really costs and earns.',
    items: [
      { href: '/hr', icon: '📊', label: 'HR · Total Comp', desc: 'Wages + commission + incentives per employee, side by side.' },
    ],
  },
]

export default function PayrollDashboardPage() {
  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏠 Payroll</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Everything payroll in one place — setup, the employee database, the pay run, and total
          compensation. Hours Approval, Payroll, Payroll Tax and Payroll Expenses all default to the
          same company pay period.
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
