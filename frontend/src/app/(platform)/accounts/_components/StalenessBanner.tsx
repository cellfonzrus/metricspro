'use client'
import { useState } from 'react'
import { api, ORG_ID } from '@/lib/client'

// Staleness banner for the P&L / Balance Sheet pages. The account endpoints now return
// `computed_at` + `newest_ingest_at` + `stale`; when stale we prompt a recompute (which the new
// /account/run-due sweep also does automatically). Renders nothing when the statements are current.
function hoursOlder(newest?: string | null, computedAt?: string | null): number | null {
  if (!newest || !computedAt) return null
  const dn = new Date(newest).getTime(), dc = new Date(computedAt).getTime()
  if (isNaN(dn) || isNaN(dc)) return null
  return (dn - dc) / 3600000
}

export function StalenessBanner({ period, computed, computedAt, newestIngestAt, stale, onRecomputed }: {
  period: string
  computed?: boolean
  computedAt?: string | null
  newestIngestAt?: string | null
  stale?: boolean
  onRecomputed?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!stale) return null

  const hrs = hoursOlder(newestIngestAt, computedAt)
  const age = hrs == null ? null : hrs < 1 ? 'less than an hour' : `${Math.round(hrs)} hour${Math.round(hrs) === 1 ? '' : 's'}`
  const label = !computed
    ? 'These books have never been computed for this period, but you have data for it.'
    : age
      ? `Statements are ${age} older than your newest data.`
      : 'Statements are older than your newest data.'

  async function recompute() {
    setBusy(true); setErr('')
    try {
      await api(`/api/v1/account/compute/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'POST' })
      onRecomputed?.()
    } catch (e: any) {
      setErr(e?.message || String(e))
    }
    setBusy(false)
  }

  return (
    <div className="card" style={{ padding: 12, marginBottom: 16, background: '#fffbeb', border: '1px solid #fde68a', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
      <span style={{ fontSize: 13, color: '#92400e', flex: 1, minWidth: 220 }}>⏱ {label}</span>
      {err && <span style={{ fontSize: 12, color: '#b91c1c' }}>{err}</span>}
      <button className="btn btn-primary" onClick={recompute} disabled={busy} style={{ fontSize: 13 }}>
        {busy ? '⏳ Recomputing…' : '↻ Recompute'}
      </button>
    </div>
  )
}
