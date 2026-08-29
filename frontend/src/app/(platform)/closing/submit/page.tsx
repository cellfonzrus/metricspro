'use client'
import Link from 'next/link'
import { useAuth } from '@/lib/auth-context'
import ClosingSubmitForm from '@/components/ClosingSubmitForm'

// Thin wrapper around the shared <ClosingSubmitForm> (also embedded in the /portal kiosk).
export default function SubmitClosingPage() {
  const { user } = useAuth()
  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>➕ Submit Daily Closing</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>One entry per rep per day — same fields as the closing sheet.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>
      <ClosingSubmitForm defaultEmployeeName={(user as any)?.full_name} />
    </div>
  )
}
