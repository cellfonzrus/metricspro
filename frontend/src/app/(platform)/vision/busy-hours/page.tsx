'use client'
// Vision — BUSY HOURS, from Google's own person events.
//
// The cameras detect people themselves and Google pushes us an event when they do, so this page
// needs no edge analyzer and no video: it is the one part of the module that costs nothing to run
// and works on every camera at once.
//
// WHAT IT IS NOT, said on the page and not only here. Google reports that a person was SEEN, never
// which way they walked. A customer leaving looks identical to one arriving, and a member of staff
// crossing the doorway looks like both. So this is an activity curve for staffing decisions — it is
// NOT a customer count, and the page refuses to let it be read as one: the words "footfall" and
// "customers" do not appear, the caveat sits above the chart rather than in a footnote, and the
// numbers are labelled "sightings".
//
// Chart choice: 24 discrete hour buckets, one measure, and the job is finding the peak — so bars,
// one series, no legend (the heading names it), the peak direct-labelled rather than recoloured.
// Recolouring by rank would mean colour tracked the ranking instead of the thing, and the bar that
// is busiest would change colour as the date range moved.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, btn, cell, th, hourLabel, storeOptions, withCurrent, visionError,
  peakHour, perDayLabel, type StoreOption, type BusyHourRow,
} from '@/lib/vision'

interface CameraShare { device_name: string; name: string; store_code: string; events: number; share: number }
interface Dominant { store_code: string; name: string; share: number; events: number; of: number }
interface BusyPayload {
  since: string
  store_code: string
  days_with_data: number
  events: number
  stores: number
  stores_scored: number
  stores_too_quiet: number
  by_hour: BusyHourRow[]
  cameras: CameraShare[]
  dominant: Dominant[]
  measure: string           // 'events' (one store, raw counts) | 'index' (several, equal-weighted)
  caveat: string            // what this particular measure does and does not say
  peak: BusyHourRow | null
  note: string
}

const BAR = '#2563eb'          // the app's own accent; validated ≥3:1 on both surfaces

export default function VisionBusyHoursPage() {
  const [data, setData] = useState<BusyPayload | null>(null)
  const [stores, setStores] = useState<StoreOption[] | null>(null)
  // DEFAULT TO ONE STORE, not the whole estate. Summing raw sightings across stores lets a store
  // with more cameras — or one chattier camera — outrank a genuinely busier store, and a real
  // estate showed exactly that: five of 21 cameras produced 45% of all events. Reading one store
  // is always sound, because its camera set is the same at 9am and at 5pm.
  const [store, setStore] = useState('')
  const [days, setDays] = useState(28)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [hover, setHover] = useState<number | null>(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const q = new URLSearchParams({ days: String(days) })
      if (store) q.set('store_code', store)
      setData(await api(`/api/v1/vision/busy-hours?${q}`))
    } catch (e) {
      // A failed load must not render as a quiet store. Discard the old data with it.
      setData(null); setErr(visionError(e))
    } finally { setLoading(false) }
  }, [store, days])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    api('/api/v1/storeops/stores')
      .then(r => {
        const opts = storeOptions(r)
        setStores(opts)
        // Land on a real store rather than the estate. The functional form cannot clobber a choice
        // the operator has already made, and this effect runs once, so no extra guard is needed.
        setStore(cur => cur || opts[0]?.code || '')
      })
      .catch(() => setStores(null))
  }, [])

  const rows = data?.by_hour || []
  // The server decides which measure is honest for this scope and says so; the page never picks,
  // or the two could disagree about what the bars mean.
  const indexed = data?.measure === 'index'
  const valueOf = (r: BusyHourRow) => (indexed ? (r.index ?? 0) : r.events)
  const peak = data?.peak || peakHour(rows)
  const ceiling = Math.max(valueOf(peak as BusyHourRow), indexed ? 0.01 : 1)
  const open = rows.filter(r => valueOf(r) > 0)

  const input: React.CSSProperties = {
    padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)',
    background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
  }

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 4, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>🕐 Busy Hours</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/vision/heatmap" style={{ ...btn, textDecoration: 'none' }}>🔥 Heat Map</Link>
          <Link href="/vision" style={{ ...btn, textDecoration: 'none' }}>📹 Live Cameras</Link>
        </div>
      </div>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13.5, margin: '0 0 14px' }}>
        When people are in the store, reported by the cameras themselves. No analyzer, no video.
      </p>

      {/* Filters in one row above the chart. */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <select value={store} onChange={e => setStore(e.target.value)} style={input} aria-label="Store">
          <option value="">All stores</option>
          {(stores ? withCurrent(stores, store) : []).map(s =>
            <option key={s.code} value={s.code}>{s.label}</option>)}
        </select>
        <select value={days} onChange={e => setDays(Number(e.target.value))} style={input} aria-label="Date range">
          <option value={7}>Last 7 days</option>
          <option value={28}>Last 28 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>

      {/* THE CAVEAT SITS ABOVE THE DATA, not under it. Someone who reads only the chart must not
          come away believing they have a customer count. */}
      <div style={{ ...panel, borderLeft: '3px solid #b45309', marginBottom: 16, fontSize: 13 }}>
        <b>This is activity, not a customer count.</b> The cameras report that a person was seen —
        never which direction they were walking. Someone leaving looks the same as someone arriving,
        and staff are counted alongside customers. Use it to see <i>when</i> the store is busy, and
        the entrance camera&apos;s in/out count for <i>how many</i>.
        {/* The second half comes from the server, because it depends on WHICH measure is being
            drawn — and a caveat that describes a different measure than the bars is worse than
            none at all. */}
        {data?.caveat && <div style={{ marginTop: 8, color: 'var(--text2)' }}>{data.caveat}</div>}
      </div>

      {/* A store whose curve is really one camera's curve. This was invisible until somebody ran a
          query by hand and found a lens facing the pavement carrying 96% of its store, a quarter of
          it after midnight. Now it says so where the numbers are read. */}
      {(data?.dominant?.length || 0) > 0 && (
        <div style={{ ...panel, borderLeft: '3px solid #b45309', marginBottom: 16, fontSize: 13 }}>
          <b>One camera is carrying {data!.dominant.length === 1 ? 'a store' : 'some stores'}.</b>
          {data!.dominant.map(d => (
            <div key={d.store_code} style={{ marginTop: 6, color: 'var(--text2)' }}>
              <b>{d.store_code}</b> — {Math.round(d.share * 100)}% of its sightings come from{' '}
              <b>{d.name}</b> ({d.events} of {d.of}). That store&apos;s bars are mostly that one
              camera, so check what it is pointed at before staffing against them.
            </div>
          ))}
        </div>
      )}

      {err && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', fontSize: 13, marginBottom: 16 }}>{err}</div>}

      {loading ? (
        <div style={{ color: 'var(--text2)' }}>Loading…</div>
      ) : !data ? null : data.events === 0 ? (
        <div style={{ ...panel, fontSize: 13.5, color: 'var(--text2)' }}>
          No sightings recorded in this period. If Google events were only just switched on, give it
          a day of trading — and check Vision → Settings § 4b shows events arriving.
        </div>
      ) : (
        <>
          {/* The headline, as a number rather than something to read off the chart. */}
          <div style={{ ...panel, marginBottom: 16, display: 'flex', gap: 28, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>Busiest hour</div>
              <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.2 }}>
                {peak.hour >= 0 ? hourLabel(peak.hour) : '—'}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>
                {indexed ? 'Stores compared' : 'Sightings'}
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
                {/* A summed sighting count across stores is the number that started all this — it
                    reads as volume while measuring camera population. Under the index it is
                    replaced by what the index actually averages over. */}
                {indexed ? data.stores_scored : data.events.toLocaleString()}
                {indexed && data.stores_too_quiet > 0 && (
                  <span style={{ fontSize: 15, fontWeight: 400, color: 'var(--text2)' }}>
                    {' '}of {data.stores}
                  </span>
                )}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>Days with data</div>
              <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
                {data.days_with_data}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>Active hours</div>
              <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
                {open.length}<span style={{ fontSize: 15, fontWeight: 400, color: 'var(--text2)' }}> of 24</span>
              </div>
            </div>
          </div>

          {/* The chart. Bars, one series, so no legend — the heading says what they are. */}
          <div style={{ ...panel, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>
              {indexed ? 'Share of a typical store\u2019s day' : 'Sightings per hour of day'}
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text2)', marginBottom: 16 }}>
              {indexed
                ? <>Each store weighted equally · {data.stores_scored} store{data.stores_scored === 1 ? '' : 's'} · since {data.since}</>
                : <>Averaged across {data.days_with_data} day{data.days_with_data === 1 ? '' : 's'} · since {data.since}</>}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: 620 }}>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 190 }}>
                  {rows.map(r => {
                    const v = valueOf(r)
                    const h = v > 0 ? Math.max(3, (v / ceiling) * 160) : 0
                    const isPeak = r.hour === peak.hour && v > 0
                    return (
                      <div key={r.hour}
                        onMouseEnter={() => setHover(r.hour)} onMouseLeave={() => setHover(null)}
                        style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
                          alignItems: 'center', height: '100%', position: 'relative', cursor: 'default' }}>
                        {/* Selective direct label: the peak only. A number on every bar is noise. */}
                        {(isPeak || hover === r.hour) && v > 0 && (
                          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4,
                            fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                            {indexed
                              ? <>{(r.index ?? 0).toFixed(1)}<span style={{ color: 'var(--text3)', fontWeight: 400 }}>%</span></>
                              : <>{perDayLabel(r)}<span style={{ color: 'var(--text3)', fontWeight: 400 }}>/day</span></>}
                          </div>
                        )}
                        <div title={indexed
                          ? `${hourLabel(r.hour)} · ${(r.index ?? 0).toFixed(1)}% of a typical store's day`
                          : `${hourLabel(r.hour)} · ${r.events} sighting${r.events === 1 ? '' : 's'} · ${perDayLabel(r)} per day`}
                          style={{
                            width: '100%', height: h, background: BAR,
                            // Rounded data-end, square at the baseline it is anchored to.
                            borderRadius: '4px 4px 0 0',
                            opacity: hover === null || hover === r.hour ? 1 : 0.55,
                            transition: 'opacity .12s',
                          }} />
                      </div>
                    )
                  })}
                </div>
                {/* Axis. Every third hour, so labels never collide. */}
                <div style={{ display: 'flex', gap: 2, marginTop: 6, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                  {rows.map(r => (
                    <div key={r.hour} style={{ flex: 1, textAlign: 'center', fontSize: 10,
                      color: r.hour === peak.hour ? 'var(--text)' : 'var(--text3)',
                      fontWeight: r.hour === peak.hour ? 600 : 400 }}>
                      {r.hour % 3 === 0 || r.hour === peak.hour ? hourLabel(r.hour) : ''}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* A table view, so the chart is never the only way to read this. */}
          <details style={{ ...panel }}>
            <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>Show the numbers</summary>
            <div style={{ overflowX: 'auto', marginTop: 12 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Hour</th>
                  <th style={th}>{indexed ? 'Share of day' : 'Sightings'}</th>
                  <th style={th}>{indexed ? 'Sightings' : 'Per day'}</th></tr></thead>
                <tbody>
                  {open.map(r => (
                    <tr key={r.hour}>
                      <td style={{ ...cell, fontWeight: r.hour === peak.hour ? 700 : 400 }}>{hourLabel(r.hour)}</td>
                      <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>
                        {indexed ? `${(r.index ?? 0).toFixed(1)}%` : r.events}</td>
                      <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>
                        {indexed ? r.events : perDayLabel(r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  )
}
