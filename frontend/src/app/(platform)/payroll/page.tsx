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
// Compliance, Who pays payroll, + HR Communications since W2.1) · Manage Employees (2026-10-14,
// §CORE-08 — Employees & Pay, Employee Database and Employee Password Reset, the first two folded in
// from their own single-link tiles) · Payroll
// (Hours Approval, Payroll, Payroll Expenses, Payroll Tax — plus the two previously-orphaned
// surfaces, Payroll Change Log and Salary Advances, which had no menu entry at all) · HR Total Comp.
import HubTiles, { type HubGroup } from '@/components/HubTiles'
// VIEWER GATE — the ONE home (lib/hub-gate.ts, owner report 2026-10-14). Sibling of the Workforce
// hub: a hand-woven tile literal is a MENU, so every entry passes the same gate the sidebar and the
// route guard apply. Most of these pages are admin/HR-only, so before this a store-scoped viewer was
// shown payroll setup, the payer registry and the pay run and bounced by the guard on each.
import { useHubGate } from '@/lib/hub-gate'

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
  // ── MANAGE EMPLOYEES (owner 2026-10-14, index §CORE-08) ─────────────────────────────────────
  // Owner: *"make a tile called Manage employee and move employee and pay and employee database
  // along with password reset under that tile"*. Employees & Pay (its own tile since 2026-10-02,
  // §19.40) and Employee Database (its own tile since Phase W2.1) were two single-link tiles sitting
  // beside each other; they are now the first two items of ONE tile, joined by the password-reset
  // door the owner asked for.
  //
  // THE MENU ENTRY IS UNTOUCHED. §19.40's ruling was about the SIDE MENU — NAV still carries
  // 'Employees & Pay' → /hr?tab=employees as its own, non-tileOnly row, and harness_nav_deep_link_lock
  // still pins it. This changes only which tile the hub groups its door under.
  //
  // Employee Password Reset is `module: 'hr'` like its two neighbours, so this tile's three doors are
  // reachable by the same people (the hub gate drops any a viewer cannot open). The ABILITY to set a
  // password stays the server's own gate — _can_edit_setting(caller, 'security').
  {
    title: 'Manage Employees',
    icon: '👥',
    desc: 'Each person\u2019s pay, their full record, and their app login password.',
    items: [
      { href: '/hr?tab=employees', icon: '👥', label: 'Employees & Pay', desc: 'Set each person\u2019s pay — hourly or salary — plus lunch and face settings, one Save per row.' },
      { href: '/hr/employee-database', icon: '🗄️', label: 'Employee Database', desc: 'Every employee record in one exportable report — profile, contact, direct deposit, onboarding documents.' },
      { href: '/hr/employee-password', icon: '🔑', label: 'Employee Password Reset', desc: 'Give someone a new password for their login — type the one you want them to use.' },
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
  const { gateHubGroups } = useHubGate()
  const groups = gateHubGroups(GROUPS)
  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏠 Payroll</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Everything payroll in one place — setup, managing each employee, the pay
          run, and total compensation. Click a tile to see what&apos;s inside. Hours Approval, Payroll, Payroll Tax
          and Payroll Expenses all default to the same company pay period.
        </p>
      </div>

      <HubTiles groups={groups} />
      {groups.length === 0 && (
        <div className="card" style={{ padding: 24, fontSize: 13.5, color: 'var(--text2)' }}>
          Nothing to show — none of the Payroll pages are enabled for your role. Ask your
          administrator if you believe you should see them.
        </div>
      )}
    </div>
  )
}
