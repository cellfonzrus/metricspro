'use client'
// Vision — CUSTOMERS IN & OUT + the store HEAT MAP.
//
// Two questions on one page because they are one question: how many people came in, and where did
// they go once they were inside. The door count is what a manager checks against their ticket count;
// the map is what changes where a display table goes.
//
// The map is normalised against the backend's p95, not its max, and the reason is on the ramp helper
// in lib/vision.ts: clipping at the max makes every store look like one scorching cell at the
// register and nothing else, which hides the only comparisons worth making.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, btn, btnPrimary, cell, th, heatColor, hourLabel, fmtDuration, daysAgo, today,
  type Camera, type HeatPayload, type TrafficSummary, type VisionConfig, visionError,
} from '@/lib/vision'

export default function VisionHeatmapPage() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [config, setConfig] = useState<VisionConfig | null>(null)
  const [store, setStore] = useState('')
  const [from, setFrom] = useState(daysAgo(6))
  const [to, setTo] = useState(today())
  const [hours, setHours] = useState<number[]>([])
  const [traffic, setTraffic] = useState<any>(null)
  const [heat, setHeat] = useState<(HeatPayload & { hot_cells: any[] }) | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    (async () => {
      try {
        const r = await api('/api/v1/vision/cameras')
        setCameras(r.cameras || [])
        setConfig(r.config || null)
        const first = (r.cameras || []).map((c: Camera) => c.store_code).filter(Boolean)[0]
        if (first) setStore(first)
      } catch (e: any) { setMsg(visionError(e)) }
    })()
  }, [])

  const load = useCallback(async () => {
    if (!store) return
    setBusy(true); setMsg('')
    const qs = `store_code=${encodeURIComponent(store)}&date_from=${from}&date_to=${to}`
    const hq = hours.length ? `&hours=${hours.join(',')}` : ''
    try {
      const [t, h] = await Promise.all([
        api(`/api/v1/vision/traffic?${qs}`).catch(e => ({ error: visionError(e) })),
        api(`/api/v1/vision/heatmap?${qs}${hq}`).catch(e => ({ error: visionError(e) })),
      ])
      if (t.error && h.error) setMsg(t.error)
      setTraffic(t.error ? null : t)
      setHeat(h.error ? null : h)
    } finally { setBusy(false) }
  }, [store, from, to, hours])

  useEffect(() => { void load() }, [load])

  const stores = Array.from(new Set(cameras.map(c => c.store_code).filter(Boolean))) as string[]
  const summary: TrafficSummary | null = traffic?.summary || null
  const ceiling = heat ? (heat.p95 || heat.max) : 0

  if (config && !config.enabled) return (
    <div style={{ padding: 20, maxWidth: 620 }}>
      <div style={panel}>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>Camera analytics is turned off</div>
        <div style={{ fontSize: 13.5, color: 'var(--text2)', marginBottom: 14 }}>
          An administrator enables it in Vision → Settings.
        </div>
        <Link href="/vision/settings" style={{ ...btnPrimary, textDecoration: 'none' }}>Open Vision Settings</Link>
      </div>
    </div>
  )

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>🔥 Store Traffic & Heat Map</h1>
        <Link href="/vision" style={{ ...btn, textDecoration: 'none' }}>📹 Live Cameras</Link>
      </div>

      {/* Filters */}
      <div style={{ ...panel, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 14 }}>
        <Field label="Store">
          <select value={store} onChange={e => setStore(e.target.value)} style={input}>
            {stores.length === 0 && <option value="">No cameras assigned</option>}
            {stores.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <Field label="From"><input type="date" value={from} onChange={e => setFrom(e.target.value)} style={input} /></Field>
        <Field label="To"><input type="date" value={to} onChange={e => setTo(e.target.value)} style={input} /></Field>
        <Field label="Hours">
          <select value={hours.join(',')} onChange={e => setHours(e.target.value ? e.target.value.split(',').map(Number) : [])} style={input}>
            <option value="">All day</option>
            <option value="8,9,10,11">Morning (8a–12p)</option>
            <option value="12,13,14,15,16">Afternoon (12p–5p)</option>
            <option value="17,18,19,20">Evening rush (5p–9p)</option>
          </select>
        </Field>
        <button style={btn} onClick={() => void load()} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 14 }}>{msg}</div>}

      {/* Door counts */}
      {summary && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(170px,1fr))', gap: 12, marginBottom: 14 }}>
            <Stat label="Walked in" value={String(summary.total_in)} sub="entrance crossings" color="#2563eb" />
            <Stat label="Customers" value={String(summary.customers)} sub={`≥ ${traffic.config.min_visit_seconds}s in store`} color="#16a34a" />
            <Stat label="Average visit" value={fmtDuration(summary.avg_dwell_seconds)} sub={`median ${fmtDuration(summary.median_dwell_seconds)}`} color="#7c3aed" />
            <Stat label="Busiest hour" value={summary.peak_hour === null ? '—' : hourLabel(summary.peak_hour)} sub={`${summary.peak_hour_in} in`} color="#f39c12" />
          </div>

          <div style={{ ...panel, marginBottom: 14 }}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>People in and out, by hour</div>
            <HourlyBars hourly={summary.hourly} />
            {/* Stated, not hidden: a running in-minus-out drifts up by one for every exit the
                detector missed, so the number is presented as a data-quality note rather than as
                "people currently in the store". */}
            {summary.drift !== 0 && (
              <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10 }}>
                {summary.total_in} in vs {summary.total_out} out — {Math.abs(summary.drift)} exit
                {Math.abs(summary.drift) === 1 ? ' was' : 's were'} not seen. Entries are counted
                independently of exits, so the door count is unaffected; only dwell time needs a pair.
                {traffic.filtered.short > 0 && ` ${traffic.filtered.short} very short visit(s) were classed as passers-by.`}
                {traffic.filtered.long > 0 && ` ${traffic.filtered.long} very long one(s) were classed as staff.`}
              </div>
            )}
          </div>
        </>
      )}

      {/* Heat map */}
      <div style={{ ...panel, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Where customers stood</div>
          <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
            {heat ? `${heat.occupied_cells} of ${heat.grid_cols * heat.grid_rows} cells used` : ''}
          </div>
        </div>
        {!heat || heat.total_person_seconds === 0 ? (
          <div style={{ color: 'var(--text2)', fontSize: 13.5 }}>
            No occupancy recorded for this store and period. A heat map needs a camera assigned to the
            store with <b>analytics</b> enabled, and an edge analyzer running against it.
          </div>
        ) : (
          <>
            <div style={{
              display: 'grid', gap: 1, background: 'var(--border)', border: '1px solid var(--border)',
              borderRadius: 6, overflow: 'hidden', aspectRatio: `${heat.grid_cols} / ${heat.grid_rows}`,
              gridTemplateColumns: `repeat(${heat.grid_cols}, 1fr)`,
            }}>
              {heat.matrix.flatMap((row, y) => row.map((v, x) => (
                <div key={`${x}-${y}`} title={`cell (${x}, ${y}) — ${v} person-seconds`}
                  style={{ background: v > 0 ? heatColor(v, ceiling) : 'var(--surface)' }} />
              )))}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>Quiet</span>
              <div style={{ display: 'flex', height: 10, width: 180, borderRadius: 5, overflow: 'hidden' }}>
                {Array.from({ length: 24 }, (_, i) => (
                  <div key={i} style={{ flex: 1, background: heatColor((i + 1) / 24 * ceiling, ceiling) }} />
                ))}
              </div>
              <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>Busy</span>
              <span style={{ fontSize: 11.5, color: 'var(--text3)', marginLeft: 6 }}>
                scaled to the 95th percentile ({heat.p95}) so one hot register does not flatten the rest
              </span>
            </div>
          </>
        )}
      </div>

      {/* What to do about it */}
      {heat && heat.total_person_seconds > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 12 }}>
          <div style={panel}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>Busiest spots</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={th}>Cell</th><th style={th}>Person-seconds</th><th style={th}>Share</th></tr></thead>
              <tbody>
                {heat.hot_cells.map(c => (
                  <tr key={`${c.cell_x}-${c.cell_y}`}>
                    <td style={cell}>({c.cell_x}, {c.cell_y})</td>
                    <td style={cell}>{c.occupancy}</td>
                    <td style={cell}>{((c.occupancy / heat.total_person_seconds) * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={panel}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>Dead zones</div>
            <div style={{ fontSize: 13, color: 'var(--text2)' }}>
              <b>{heat.dead_zones.length}</b> of {heat.grid_cols * heat.grid_rows} floor cells saw
              almost no traffic. A display table sitting in one of them is merchandising nobody walks
              past — this is the half of the map that changes a floor plan.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const input: React.CSSProperties = {
  padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)',
  background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text3)' }}>{label}</span>
      {children}
    </label>
  )
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color: string }) {
  return (
    <div style={{ ...panel, borderLeft: `3px solid ${color}` }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text3)' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3 }}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function HourlyBars({ hourly }: { hourly: { hour: number; in: number; out: number }[] }) {
  // Only the hours a store is plausibly open carry information; a 24-column chart of mostly zeros
  // makes the two columns that matter unreadable.
  const active = hourly.filter(h => h.in > 0 || h.out > 0)
  const lo = active.length ? Math.min(...active.map(h => h.hour)) : 8
  const hi = active.length ? Math.max(...active.map(h => h.hour)) : 20
  const shown = hourly.filter(h => h.hour >= lo && h.hour <= hi)
  const peak = Math.max(1, ...shown.map(h => Math.max(h.in, h.out)))
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 130 }}>
      {shown.map(h => (
        <div key={h.hour} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', gap: 2, width: '100%' }}>
            <div title={`${h.in} in`} style={{ flex: 1, height: `${(h.in / peak) * 100}%`, background: '#2563eb', borderRadius: '3px 3px 0 0', minHeight: h.in ? 2 : 0 }} />
            <div title={`${h.out} out`} style={{ flex: 1, height: `${(h.out / peak) * 100}%`, background: '#94a3b8', borderRadius: '3px 3px 0 0', minHeight: h.out ? 2 : 0 }} />
          </div>
          <div style={{ fontSize: 10, color: 'var(--text3)' }}>{hourLabel(h.hour)}</div>
        </div>
      ))}
    </div>
  )
}
