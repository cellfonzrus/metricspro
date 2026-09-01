'use client'
// Payroll tiled dashboard (Phase W2, owner directive 2026-09-01). The front door for everything
// payroll: replaces hunting through the side menu with one tile hub. Hub-and-spoke like
// /configurations: each link goes to the page that still owns the function (routes, RBAC and
// REPORT_DIRECTORY entries all unchanged — the hub itself is a LANDING, deliberately not registered
// as a report).
//
// Phase W2.1 (owner feedback 2026-09-01, "cleaner look"): the master tiles now COLLAPSE — each
// renders as one card (icon + title + desc + page count) and the interior links expand in place on
// click (HubTiles). Single-link tiles (Employee Database, HR Total Comp) navigate directly. In the
// same pass the side menu hides everything a tile covers (NavItem.tileOnly in rbac.ts), so
// 'HR Communications' (/hr/letters) — previously menu-only — joins the Payroll Setup tile here.
//
// Tile taxonomy is the owner's spec verbatim: Payroll Setup (Add Employees, Onboarding Checklist,
// Compliance, Who pays payroll, + HR Communications since W2.1) · Employee Database · Payroll
// (Hours Approval, Payroll, Payroll Expenses, Payroll Tax — plus the two previously-orphaned
// surfaces, Payroll Change Log and Salary Advances, which had no menu entry at all) · HR Total Comp.
import HubTiles, { type HubGroup } from '@/components/HubTiles'

const GROUPS: HubGroup[] = [
  {
    title: 'Payroll Setup',
    icon: '🛠️',
    desc: 'Get people in and configured — do these first.',
    items: [
      { href: '/hr/people', icon: '🧑‍💼', label: 'Add Employees', desc: 'Add people, set pay & role, create their login.' },
      { href: '/hr/onboarding', icon: '🧩', label: 'Onboarding Checklist', desc: 'Intake packet, tasks and documents for each new hire.' },
      { href: '/hr/compliance', icon: '📋', label: 'Compliance', desc: 'Required documents & certifications — who is missing what.' },
      { href: '/hr/letters', icon: '✉️', label: 'HR Communications', desc: 'Letter templates and sends — warnings, notices and other employee letters.' },
      { href: '/storeops/payroll/payers', icon: '🏦', label: 'Who Pays Payroll', desc: 'The payer registry — which entity pays which store/person (admin only).' },
    ],
  },
  {
    title: 'Employee Database',
    icon: '🗄️',
    desc: 'Every employee field in one place — profile, pay, documents, history.',
    items: [
      { href: '/hr/employee-database', icon: '🗄️', label: 'Employee Database', desc: 'Every employee field in one place — profile, pay, documents, history.' },
    ],
  },
  {
    title: 'Payroll',
    icon: '💵',
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
    icon: '📊',
    desc: 'Wages + commission + incentives per employee — what each person really costs and earns.',
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
          compensation. Click a tile to see what&apos;s inside. Hours Approval, Payroll, Payroll Tax
          and Payroll Expenses all default to the same company pay period.
        </p>
      </div>

      <HubTiles groups={GROUPS} />
    </div>
  )
}
