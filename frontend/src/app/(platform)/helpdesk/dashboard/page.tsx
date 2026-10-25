'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, ORG_ID } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import DrillDownPanel, { DrillTileLinks, useDrillTile, type DrillPanel, type DrillRow } from '@/components/DrillDownPanel'

// WHAT IDENTIFIES ONE TICKET IN A DRILL-DOWN — the client side of
// `helpdesk/dashboard_drilldown._fields`. `status` is the one that matters: a ticket whose status is
// not in the status list falls back to "open" on this screen, and the panel shows which rows those
// are rather than telling the reader so.
const TICKET_COLUMNS = [
  { header: 'Stage', get: (r: DrillRow) => String(r.fields?.stage || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.stage || '') },
  { header: 'Status', get: (r: DrillRow) => (r.fields?.status_mapped === false
      ? 'not in the status list' : String(r.fields?.status || '—')),
    sortValue: (r: DrillRow) => String(r.fields?.status || '') },
  { header: 'Raised', get: (r: DrillRow) => String(r.fields?.opened || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.opened || '') },
  { header: 'Resolved', get: (r: DrillRow) => String(r.fields?.resolved || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.resolved || '') },
]

function Tile({ label, value, color, sub, drills, drill }: {
  label: string; value: any; color?: string; sub?: string
  drills?: { key: string; label: string }[]
  drill?: { open: string | null; toggle: (k: string) => void }
}) {
  return (
    <div className="card" style={{ padding: 16, minWidth: 130 }}>
      <div style={{ fontSize: 12, color: 'var(--text3)' }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{sub}</div>}
      {/* A tile the server served no panel for offers no link, rather than one that opens nothing. */}
      {drill && drills && drills.length ? <DrillTileLinks drills={drills} open={drill.open} onToggle={drill.toggle} /> : null}
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

  // EVERY FIGURE OPENS TO ITS TICKETS (§CORE-17). No narrowing is needed or applied: the only filter
  // on this screen is the period, and it is a query parameter, so the server folded exactly the
  // tickets shown.
  const panels: Record<string, DrillPanel> = d?.drilldown || {}
  const drill = useDrillTile(panels)
  const dl = drill.dl

  if (err) return <div style={{ padding: 24, color: 'var(--c-fg-c0392b)' }}>{err}</div>

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
        <Tile label="Total tickets" value={d.total}
              drills={dl('total', 'the tickets')} drill={drill} />
        <Tile label="Open" value={d.open} color="var(--c-fg-f59e0b)"
              drills={dl('open', 'the tickets')} drill={drill} />
        <Tile label="Resolved/Closed" value={d.by_stage?.done ?? 0} color="var(--c-fg-22c55e)"
              drills={dl('stage_done', 'the tickets')} drill={drill} />
        {/* AN AVERAGE IS NOT A SUM, so this tile opens to the tickets it MEASURES rather than to a
            panel claiming to add up to a mean. It also now says how many of the period's tickets
            that is: the figure is a mean over the resolved ones only, and until this it read as a
            mean over all of them. */}
        <Tile label="Avg resolution (hrs)" value={d.avg_resolution_hours ?? '—'}
              sub={d.measured != null && d.total ? `over ${d.measured} of ${d.total} resolved` : undefined}
              drills={dl('measured', 'the tickets it measures')} drill={drill} />
      </div>
      <DrillDownPanel panel={drill.panel} extraColumns={TICKET_COLUMNS} />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <div className="card" style={{ padding: 16, flex: 1, minWidth: 240 }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>By lifecycle stage</div>
          {Object.entries(d.by_stage || {}).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 14, textTransform: 'capitalize' }}>
              <span>{k}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <b>{String(v)}</b>
                <DrillTileLinks drills={dl(`stage_${k}`, 'open')} open={drill.open} onToggle={drill.toggle} />
              </span>
            </div>))}
        </div>
        <div className="card" style={{ padding: 16, flex: 1, minWidth: 240 }}>
          {/* KEYED ON NOW, NOT ON THE PERIOD — an open ticket is bucketed by how old it is at this
              moment, while every figure above is keyed on when it was raised. Both are right and
              they are different questions, so the heading says so instead of letting a reader
              assume the two line up. No panel: a bucket of "how old right now" has no stable set of
              rows the server folded it over. */}
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Open-ticket aging <span style={{ fontWeight: 400, fontSize: 11, color: 'var(--text3)' }}>· age as of now, not the period</span></div>
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
