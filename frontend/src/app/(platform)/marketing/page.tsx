'use client'
// Marketing & Events — the dashboard. What is coming up, what needs a human today, what just ran.
//
// The attention list is computed SERVER-side by the same `event_readiness` the event workspace and
// the admin attention providers use, so this page's count, the banner on an event and the
// notification an admin receives can never disagree with each other.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, btn, btnPrimary, input, label, th, cell,
  STATUS_COLOR, SEVERITY_COLOR,
  fmtDate, fmtTime, fmtMoney, relTime,
  type MarketingEvent, type MarketingConfig,
} from '@/lib/marketing'

interface Summary {
  upcoming: MarketingEvent[]
  needs_attention: MarketingEvent[]
  recent: MarketingEvent[]
  counts: { upcoming: number; needs_attention: number; recent: number; pending_approval: number }
  config: MarketingConfig
  window: { from: string; to: string }
  error?: string
}

function Tile({ label: l, value, sub, color }: { label: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div style={{ ...panel, minWidth: 150, flex: '1 1 150px' }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>{l}</div>
      <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4, color: color || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 20,
      color: '#fff', background: STATUS_COLOR[status] || '#6b7280', textTransform: 'capitalize',
    }}>{status}</span>
  )
}

function EventRow({ e, showIssues }: { e: MarketingEvent; showIssues?: boolean }) {
  return (
    <tr>
      <td style={cell}>
        <Link href={`/marketing/events/${e.id}`} style={{ fontWeight: 600, color: 'var(--text)' }}>
          {e.title}
        </Link>
        {e.theme_label && <div style={{ fontSize: 11, color: 'var(--text2)' }}>{e.theme_label}</div>}
      </td>
      <td style={cell}>
        <div>{fmtDate(e.event_start)}</div>
        <div style={{ fontSize: 11, color: 'var(--text2)' }}>
          {/* The owner asked for the call time as its own question, so it is shown as its own line. */}
          Event {fmtTime(e.event_start)}{e.staff_call_at ? ` · Staff by ${fmtTime(e.staff_call_at)}` : ''}
        </div>
      </td>
      <td style={cell}>{e.venue_name || '—'}<div style={{ fontSize: 11, color: 'var(--text2)' }}>{e.city || ''}</div></td>
      <td style={cell}>{(e.store_codes || []).join(', ') || e.primary_store_code || '—'}</td>
      <td style={cell}><StatusPill status={e.status} /></td>
      <td style={{ ...cell, whiteSpace: 'nowrap' }}>{relTime(e.staff_call_at || e.event_start)}</td>
      {showIssues && (
        <td style={cell}>
          {(e.issues || []).map((i, n) => (
            <div key={n} style={{ fontSize: 12, color: SEVERITY_COLOR[i.severity] || 'var(--text2)' }}>
              {i.severity === 'error' ? '●' : '▲'} {i.detail}
            </div>
          ))}
        </td>
      )}
    </tr>
  )
}

export default function MarketingDashboard() {
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [ahead, setAhead] = useState(30)
  const [back, setBack] = useState(30)

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    try {
      setData(await api(`/api/v1/marketing/summary?days_ahead=${ahead}&days_back=${back}`))
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setLoading(false)
  }, [ahead, back])

  useEffect(() => { load() }, [load])

  const c = data?.counts

  return (
    <div style={{ padding: 20, maxWidth: 1400 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎪 Marketing &amp; Events</h1>
        <div style={{ flex: 1 }} />
        <Link href="/marketing/events/new" style={{ ...btnPrimary, textDecoration: 'none' }}>➕ Plan an event</Link>
        <Link href="/marketing/my-checkins" style={{ ...btn, textDecoration: 'none' }}>📍 My check-ins</Link>
        <Link href="/marketing/settings" style={{ ...btn, textDecoration: 'none' }}>⚙️ Settings</Link>
      </div>

      <div style={{ ...panel, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'end', marginBottom: 14 }}>
        <div style={{ width: 150 }}>
          <span style={label}>Days ahead</span>
          <input style={input} type="number" min={0} max={365} value={ahead}
                 onChange={ev => setAhead(Number(ev.target.value) || 0)} />
        </div>
        <div style={{ width: 150 }}>
          <span style={label}>Days back</span>
          <input style={input} type="number" min={0} max={365} value={back}
                 onChange={ev => setBack(Number(ev.target.value) || 0)} />
        </div>
        <button style={btn} onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 14, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div>}
      {data?.error && <div style={{ ...panel, marginBottom: 14, borderColor: '#f39c12' }}>{data.error}</div>}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
        <Tile label="Upcoming" value={c?.upcoming ?? '—'} sub={`next ${ahead} days`} />
        <Tile label="Need attention" value={c?.needs_attention ?? '—'} sub="staffing, backup, kit"
              color={(c?.needs_attention || 0) > 0 ? '#dc2626' : undefined} />
        {/* Only shown when the org actually turned approval on — an off-by-default feature should
            not occupy a tile explaining that nothing is pending. */}
        {data?.config?.approval_required && (
          <Tile label="Awaiting approval" value={c?.pending_approval ?? '—'}
                color={(c?.pending_approval || 0) > 0 ? '#f39c12' : undefined} />
        )}
        <Tile label="Recently run" value={c?.recent ?? '—'} sub={`past ${back} days`} />
      </div>

      {(data?.needs_attention?.length || 0) > 0 && (
        <section style={{ marginBottom: 20 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>Needs a human</h2>
          <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr>
                <th style={th}>Event</th><th style={th}>When</th><th style={th}>Venue</th>
                <th style={th}>Store</th><th style={th}>Status</th><th style={th}>Call</th>
                <th style={th}>What is wrong</th>
              </tr></thead>
              <tbody>{data!.needs_attention.map(e => <EventRow key={e.id} e={e} showIssues />)}</tbody>
            </table>
          </div>
        </section>
      )}

      <section style={{ marginBottom: 20 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>Upcoming</h2>
        <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr>
              <th style={th}>Event</th><th style={th}>When</th><th style={th}>Venue</th>
              <th style={th}>Store</th><th style={th}>Status</th><th style={th}>Call</th>
            </tr></thead>
            <tbody>
              {(data?.upcoming || []).map(e => <EventRow key={e.id} e={e} />)}
              {!loading && !(data?.upcoming || []).length && (
                <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={6}>
                  Nothing planned in this window. <Link href="/marketing/events/new">Plan an event</Link>.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>Recently run</h2>
        <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr>
              <th style={th}>Event</th><th style={th}>When</th><th style={th}>Venue</th>
              <th style={th}>Store</th><th style={th}>Status</th><th style={th}>Ran</th>
            </tr></thead>
            <tbody>
              {(data?.recent || []).map(e => <EventRow key={e.id} e={e} />)}
              {!loading && !(data?.recent || []).length && (
                <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={6}>Nothing in this window.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
