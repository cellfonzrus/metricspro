'use client'
import { useState } from 'react'
import { api } from '@/lib/client'

// Envelope-photo view link for the DM evening verify view + the cash-pickup check. The list endpoints
// (/closing/summary, /closing/pickups) return the RAW storage path (they skip per-row signing for
// perf), and the closing-envelopes bucket is PRIVATE — so a raw path never loads. This signs it
// LAZILY on click via GET /closing/envelope-url (org-scoped). If the row already carries a pre-signed
// envelope_url, we just use it. Owner-reported 2026-08-18: DMs couldn't see the envelope in either view.
export default function EnvelopeViewLink({ row, label = '📷' }: {
  row: { envelope_url?: string | null; envelope_picture?: string | null; store_code?: string; close_date?: string | number; employee_name?: string | null }
  label?: string
}) {
  const [busy, setBusy] = useState(false)
  if (!row?.envelope_url && !row?.envelope_picture) return <span>—</span>
  if (row.envelope_url) return <a href={row.envelope_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>{label}</a>
  const open = async () => {
    setBusy(true)
    try {
      const qs = new URLSearchParams({ store_code: row.store_code || '', close_date: String(row.close_date || '') })
      if (row.employee_name) qs.set('employee_name', row.employee_name)
      const r: any = await api(`/api/v1/closing/envelope-url?${qs.toString()}`)
      if (r?.url) window.open(r.url, '_blank', 'noopener')
      else alert('Envelope photo unavailable.')
    } catch (e: any) {
      alert('Could not open the envelope photo: ' + (e?.message || e))
    } finally { setBusy(false) }
  }
  return (
    <button onClick={open} disabled={busy} title="View the envelope photo"
      style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--accent)', fontSize: 'inherit' }}>
      {busy ? '…' : label}
    </button>
  )
}
