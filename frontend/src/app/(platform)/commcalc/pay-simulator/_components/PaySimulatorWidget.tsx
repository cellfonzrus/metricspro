'use client'
// COMPACT "What would I make?" widget for the EMPLOYEE DASHBOARD, to sit beside the existing
// "Commission Earned" card (owner 2026-08-03: "a widget for the same to be added to the employee
// dashboard with their commission widget").
//
// PLACEMENT IS A CROSS-MODULE STEP. The dashboard page `(platform)/employee/page.tsx` and its card
// grid `components/EmployeeWidgets.tsx` are mod-people's files (AGENT_CONTRACT §1) — mod-commission
// does not edit them. So the widget ships HERE, self-contained and self-fetching, and the two-line
// mount is filed under ## NEEDS CORE / cross-module in docs/handoffs/commission.md.
//
// SELF-ONLY BY CONSTRUCTION: it takes no employee_id and no rep — the backend resolves the caller
// from their bearer token. That is why the dashboard must render it only while the picker is on the
// caller's OWN record (the same rule `MyChargebacks` follows), otherwise an admin browsing another
// employee would see their OWN projection under that employee's name.
//
// The card renders the SAME <PaySimulator> the full page does, in `compact` mode — one component,
// one server round-trip, so the widget and the page can never quote different dollars.
import PaySimulator from './PaySimulator'

/** Current month in the 'Month YYYY' spelling the platform period selector emits. */
function thisPeriod() {
  const d = new Date()
  return `${d.toLocaleString('en-US', { month: 'long' })} ${d.getFullYear()}`
}

export default function PaySimulatorWidget({ period }: { period?: string }) {
  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    gap: 8, marginBottom: 10 }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>🎚️ What would I make?</div>
        <a href="/commcalc/pay-simulator" style={{ fontSize: 11, color: 'var(--text3)', textDecoration: 'none' }}>
          open full simulator →
        </a>
      </div>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
        Play with the numbers — this runs your real pay plan. It changes nothing.
      </div>
      <PaySimulator period={period || thisPeriod()} compact />
    </div>
  )
}
