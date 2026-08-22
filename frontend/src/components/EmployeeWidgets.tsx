'use client'
// Shared employee widget grid — rendered by BOTH the admin /employee dashboard (pick-anyone) and the
// self-service kiosk /portal (scoped to the signed-in employee), plus the team drill-down.
//
// The grid renders several recharts charts (~8.8MB). To keep recharts out of every route bundle that
// merely CAN show this widget, the real implementation lives in ./EmployeeWidgets.impl and is loaded
// on demand via next/dynamic (ssr:false). First open shows a brief placeholder. Public API unchanged:
// the same default export with the same props.
import dynamic from 'next/dynamic'

export type EmployeeWidgetsProps = { data: any; coach: any; repTargets: any }

const EmployeeWidgets = dynamic<EmployeeWidgetsProps>(() => import('./EmployeeWidgets.impl'), {
  ssr: false,
  loading: () => (
    <div style={{ padding: 24, color: 'var(--text3)', fontSize: 13 }}>Loading…</div>
  ),
})

export default EmployeeWidgets
