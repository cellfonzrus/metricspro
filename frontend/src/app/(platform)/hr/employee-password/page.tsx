'use client'
// ── RESET AN EMPLOYEE'S PASSWORD — the Payroll hub's door to it (owner 2026-10-14, §CORE-08) ──────
//
// OWNER: *"provide a link to reset the password for the employee login on payroll Dashboard, make a
// tile called Manage employee and move employee and pay and employee database along with password
// reset under that tile — in both config and this new link to password reset, the admin should be
// able to assign a manual password also"*.
//
// WHY A ROUTE AND NOT A LINK TO /admin/security. The capability already had exactly one home and one
// server path; what it did not have was a door for the people who manage employees. Pointing the
// Payroll hub's tile at `/admin/security` would have put an `admin`-module page behind an `hr`/market
// tile: the hub gate (lib/hub-gate.ts, §CORE-06) would correctly DROP the link for every HR or market
// manager, so the owner's new tile would show the password door to nobody but a full admin. This page
// is `module: 'hr'`, like the two tiles it now sits beside, and renders the SAME component — no
// second copy of the form, the fetch, or the policy echo (index §CORE-08).
//
// NOBODY'S ACCESS WIDENS. The authority is and stays the server: `POST /core/users/set-password`
// gates on `_can_edit_setting(caller, 'security')` and re-validates the password against the tenant
// policy. An HR manager who does not hold that grant opens this page and is told so, in words,
// instead of being shown a form that 403s. Same precedent as rbac.ts::MGMT_ONLY — a page whose NAV
// tier mirrors a server gate.
import AssignPasswordCard from '@/components/AssignPasswordCard'

export default function EmployeePasswordPage() {
  return (
    <div style={{ maxWidth: 780 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>🔑 Employee Password Reset</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 16px' }}>
        Give an employee a new password for their MetricsPro login. Pick the person, type the password
        you want them to use, and hand it to them — they&apos;ll be asked to choose their own the first
        time they sign in. The password has to pass this company&apos;s password rules, which an
        administrator sets under Security Settings.
      </p>
      <AssignPasswordCard />
      <p style={{ color: 'var(--text3)', fontSize: 12, margin: '14px 0 0' }}>
        Every password an administrator sets is written to the sign-in audit trail — who set it, for
        whom, and when.
      </p>
    </div>
  )
}
