'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, ORG_ID } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

function Tile({ label, value, color }: { label: string; value: any; color?: string }) {
  return (
    <div className="card" style={{ padding: 16, minWidth: 130 }}>
      <div style={{ fontSize: 12, color: 'var(--text3)' }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
    </div>
  )
}

export default function HelpdeskDashboard() {
  const [d, setD] = useState<any>(null)
  const [err, setErr] = useState('')
  // RULE FIVE (§3d): the tiles are aggregates with no store/market/rep dimension (documented deviation),
  // so only the period (date range) applies — it re-fetches the server-side aggregate for that window.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const load = useCallback(() => {
    const q = new URLSearchParams({ org_id: ORG_ID })
    if (filt.period) q.set('date_from', filt.period)
    if (filt.periodTo) q.set('date_to', filt.periodTo)
    setErr(''); api(`/api/v1/helpdesk/stats/dashboard?${q.toString()}`).then(setD).catch(e => setErr(e?.message || 'Failed'))
  }, [filt])
  useEffect(() => { load() }, [load])

  if (err) return <div style={{ padding: 24, color: '#c0392b' }}>{err}</div>

  return (
    <div style={{ padding: 24, maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Helpdesk Dashboard</h1>
        <span style={{ flex: 1 }} /><Link href="/helpdesk" className="btn">🎫 Inbox</Link>
      </div>
      <StandardFilterBar value={filt} onChange={setFilt} periodMode="range"
        show={{ period: true, stores: false, markets: false, reps: false }} />
      {!d ? <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div> : (
      <>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
        <Tile label="Total tickets" value={d.total} />
        <Tile label="Open" value={d.open} color="#f59e0b" />
        <Tile label="Resolved/Closed" value={d.by_stage?.done ?? 0} color="#22c55e" />
        <Tile label="Avg resolution (hrs)" value={d.avg_resolution_hours ?? '—'} />
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <div className="card" style={{ padding: 16, flex: 1, minWidth: 240 }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>By lifecycle stage</div>
          {Object.entries(d.by_stage || {}).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 14, textTransform: 'capitalize' }}>
              <span>{k}</span><b>{String(v)}</b></div>))}
        </div>
        <div className="card" style={{ padding: 16, flex: 1, minWidth: 240 }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Open-ticket aging</div>
          {Object.entries(d.aging || {}).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 14 }}>
              <span>{k}</span><b>{String(v)}</b></div>))}
        </div>
      </div>
      </>
      )}
    </div>
  )
}
