'use client'
import Link from 'next/link'
import { useAuth } from '@/lib/auth-context'
import { repConfined, REP_LANDING } from '@/lib/rbac'
import ClosingSubmitForm from '@/components/ClosingSubmitForm'
import { WorkflowNext } from '@/components/WorkflowNext'

// Thin wrapper around the shared <ClosingSubmitForm> (also embedded in the /portal kiosk).
//
// A CONFINED EMPLOYEE LANDS HERE from the Daily closing tile (owner 2026-11-27, §CORE-33.4), and the
// grant for it is this page EXACTLY — `/closing` and the rest of the subsystem stay refused. So the
// one hard-coded link out of here is hidden for them: `repConfined` off `/me` is used for that single
// purpose, exactly as on the barcode rescue — not offering a door that is already known to be shut.
// `WorkflowNext` needs nothing: it asks `useCanOpen`, the same predicate the sidebar asks.
export default function SubmitClosingPage() {
  const { user, permissions } = useAuth()
  const confined = repConfined(permissions)
  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>➕ Submit Daily Closing</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>One entry per rep per day — same fields as the closing sheet.</p>
        </div>
        {confined
          ? <Link href={REP_LANDING} className="btn btn-secondary" style={{ fontSize: 13 }}>← My day</Link>
          : <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>}
      </div>
      <ClosingSubmitForm defaultEmployeeName={(user as any)?.full_name} />
      <WorkflowNext here="/closing/submit" />
    </div>
  )
}
