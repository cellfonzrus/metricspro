'use client'
// HR Letters — Sent Letters log (audit trail). RULE FOUR: rendered through <ReportShell> so it gets
// Excel/PDF/Print/Send (email+WhatsApp) for free, plus ReportShell's own by-rep/by-store/by-date
// quick filters and "＋ Add filter" for anything else (status, category, tier…).
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'

const STATUS_LABEL: Record<string, string> = {
  sent: '✅ Sent', approved_sent: '✅ Sent (approved)', queued_approval: '📥 Queued for approval',
  rejected: '⛔ Rejected', failed: '⚠️ Failed',
}

export default function SentLettersPage() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    setLoading(true)
    api('/api/v1/hr/letters/sent?limit=1000')
      .then(d => setRows(d.letters || []))
      .catch(e => setErr(e?.message || 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  const columns: ExportColumn[] = [
    { header: 'Sent At', field: 'created_at', type: 'date', get: r => (r.created_at || '').slice(0, 16).replace('T', ' ') },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: r => r.employee_name || r.employee_id || '' },
    { header: 'Category', field: 'category', get: r => r.category || '' },
    { header: 'Tier', field: 'escalation_tier', get: r => r.escalation_tier || '' },
    { header: 'Subject', field: 'subject', get: r => r.subject || '' },
    { header: 'Incident/Period', field: 'incident_period', get: r => r.incident_date || r.period || '' },
    { header: 'Status', field: 'status', get: r => STATUS_LABEL[r.status] || r.status || '' },
    { header: 'Trigger', field: 'trigger', get: r => r.trigger === 'auto' ? 'Automatic' : 'Manual' },
    { header: 'Sender', field: 'sender', get: r => r.sender || '' },
  ]

  return (
    <div style={{ padding: 20 }}>
      <h2 style={{ margin: '0 0 4px' }}>📜 HR Letters — Sent Log</h2>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginTop: 0 }}>
        Every letter ever sent, queued, approved, or rejected — auto-detected or manually sent — with the
        merge data used at send time. HR/admin only.
      </p>
      {err && <div style={{ color: '#c0392b', fontSize: 13, margin: '8px 0' }}>{err}</div>}
      {loading && <div style={{ fontSize: 13 }}>Loading…</div>}
      {!loading && (
        <ReportShell title="HR Letters — Sent Log" filename="hr-sent-letters" columns={columns} rows={rows} />
      )}
    </div>
  )
}
