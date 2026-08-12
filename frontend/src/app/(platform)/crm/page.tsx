'use client'
// CRM Dashboard — the funnel, the weighted forecast, and the things that need a human today.
// Standard filter set (period / store / market / rep) per RULE FIVE; the same filters drive the
// lead list and the reports so the numbers can never disagree with each other.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, btn, input, label, fmtMoney, relTime, STATUS_COLOR } from '@/lib/crm'

interface FunnelRow { stage_id: string; stage: string; count: number; value: number; probability: number; is_won: boolean; is_lost: boolean }
interface Summary {
  totals: { total: number; open: number; won: number; lost: number; win_rate: number; close_rate: number }
  funnel: FunnelRow[]
  forecast: number
  pipeline_value: number
  attention: { stale_leads: number; overdue_tasks: number; missed_tasks: number; unassigned: number; agency_unanswered: number }
  leaderboard: { employee_id: string; leads: number; won: number; lost: number; open: number; value: number; win_rate: number }[]
  by_source: { source: string; leads: number; won: number; conversion: number; value: number }[]
  stale_sample: any[]
  config: { stale_lead_hours: number; escalate_after_hours: number }
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

export default function CrmDashboard() {
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [store, setStore] = useState('')
  const [market, setMarket] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    try {
      const p = new URLSearchParams()
      if (start) p.set('start', start)
      if (end) p.set('end', end)
      if (store) p.set('store_code', store)
      if (market) p.set('market', market)
      setData(await api(`/api/v1/crm/summary?${p}`))
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setLoading(false)
  }, [start, end, store, market])

  useEffect(() => { load() }, [load])

  const maxCount = Math.max(1, ...(data?.funnel || []).map(f => f.count))
  const a = data?.attention

  return (
    <div style={{ padding: 20, maxWidth: 1400 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎯 Sales Pipeline</h1>
        <div style={{ flex: 1 }} />
        <Link href="/crm/leads/new" style={{ ...btn, background: '#2563eb', borderColor: '#2563eb', color: '#fff', fontWeight: 600, textDecoration: 'none' }}>➕ Log a lead</Link>
        <Link href="/crm/my-followups" style={{ ...btn, textDecoration: 'none' }}>🔔 My follow-ups</Link>
        <Link href="/crm/lookup" style={{ ...btn, textDecoration: 'none' }}>🔎 Customer lookup</Link>
      </div>

      <div style={{ ...panel, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'end', marginBottom: 14 }}>
        <div><span style={label}>From</span><input type="date" value={start} onChange={e => setStart(e.target.value)} style={{ ...input, width: 150 }} /></div>
        <div><span style={label}>To</span><input type="date" value={end} onChange={e => setEnd(e.target.value)} style={{ ...input, width: 150 }} /></div>
        <div><span style={label}>Store</span><input value={store} onChange={e => setStore(e.target.value)} placeholder="All stores" style={{ ...input, width: 140 }} /></div>
        <div><span style={label}>Market</span><input value={market} onChange={e => setMarket(e.target.value)} placeholder="All markets" style={{ ...input, width: 140 }} /></div>
        <button onClick={load} style={btn}>Apply</button>
        {(start || end || store || market) && (
          <button onClick={() => { setStart(''); setEnd(''); setStore(''); setMarket('') }} style={btn}>Clear</button>
        )}
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 14 }}>{msg}</div>}
      {loading && <div style={{ color: 'var(--text2)' }}>Loading…</div>}

      {data && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            <Tile label="Open leads" value={data.totals.open} sub={`${data.totals.total} total`} />
            <Tile label="Pipeline value" value={fmtMoney(data.pipeline_value)} sub="open leads, at face value" />
            <Tile label="Weighted forecast" value={fmtMoney(data.forecast)} sub="value × stage probability" color="#2563eb" />
            <Tile label="Won" value={data.totals.won} sub={`${data.totals.win_rate}% win rate`} color="#16a34a" />
            <Tile label="Lost" value={data.totals.lost} color="#dc2626" />
          </div>

          {a && (a.stale_leads + a.overdue_tasks + a.missed_tasks + a.unassigned + a.agency_unanswered) > 0 && (
            <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 14 }}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>⚠️ Needs attention</div>
              <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13 }}>
                {a.overdue_tasks > 0 && <Link href="/crm/my-followups?scope=team" style={{ color: '#f39c12' }}>{a.overdue_tasks} overdue follow-up(s)</Link>}
                {a.missed_tasks > 0 && <Link href="/crm/my-followups?scope=team&status=missed" style={{ color: '#dc2626' }}>{a.missed_tasks} missed follow-up(s)</Link>}
                {a.stale_leads > 0 && <span>{a.stale_leads} lead(s) with no activity for {data.config.stale_lead_hours}h</span>}
                {a.unassigned > 0 && <Link href="/crm/leads" style={{ color: '#f39c12' }}>{a.unassigned} lead(s) with no owner</Link>}
                {a.agency_unanswered > 0 && <Link href="/crm/agencies" style={{ color: '#f39c12' }}>{a.agency_unanswered} agency lead(s) not answered</Link>}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <div style={{ ...panel, flex: '2 1 420px' }}>
              <div style={{ fontWeight: 700, marginBottom: 10 }}>Funnel</div>
              {data.funnel.length === 0 && <div style={{ color: 'var(--text2)', fontSize: 13 }}>No stages configured yet.</div>}
              {data.funnel.map(f => (
                <div key={f.stage_id} style={{ marginBottom: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 3 }}>
                    <span>{f.stage} <span style={{ color: 'var(--text2)' }}>({f.probability}%)</span></span>
                    <span style={{ color: 'var(--text2)' }}>{f.count} · {fmtMoney(f.value)}</span>
                  </div>
                  <div style={{ height: 8, background: 'var(--surface)', borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${(f.count / maxCount) * 100}%`, background: f.is_won ? '#16a34a' : f.is_lost ? '#dc2626' : '#2563eb' }} />
                  </div>
                </div>
              ))}
              <Link href="/crm/pipeline" style={{ fontSize: 12 }}>Open the board →</Link>
            </div>

            <div style={{ ...panel, flex: '1 1 280px' }}>
              <div style={{ fontWeight: 700, marginBottom: 10 }}>Leaderboard</div>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th style={{ textAlign: 'left' }}>Rep</th><th>Leads</th><th>Won</th><th>Win %</th></tr></thead>
                <tbody>
                  {data.leaderboard.slice(0, 12).map(r => (
                    <tr key={r.employee_id}>
                      <td style={{ padding: '3px 0' }}>{r.employee_id}</td>
                      <td style={{ textAlign: 'center' }}>{r.leads}</td>
                      <td style={{ textAlign: 'center', color: '#16a34a' }}>{r.won}</td>
                      <td style={{ textAlign: 'center' }}>{r.win_rate}%</td>
                    </tr>
                  ))}
                  {data.leaderboard.length === 0 && <tr><td colSpan={4} style={{ color: 'var(--text2)' }}>No leads yet.</td></tr>}
                </tbody>
              </table>
            </div>

            <div style={{ ...panel, flex: '1 1 280px' }}>
              <div style={{ fontWeight: 700, marginBottom: 10 }}>Where leads come from</div>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th style={{ textAlign: 'left' }}>Source</th><th>Leads</th><th>Won</th><th>Conv.</th></tr></thead>
                <tbody>
                  {data.by_source.slice(0, 12).map(r => (
                    <tr key={r.source}>
                      <td style={{ padding: '3px 0' }}>{r.source}</td>
                      <td style={{ textAlign: 'center' }}>{r.leads}</td>
                      <td style={{ textAlign: 'center' }}>{r.won}</td>
                      <td style={{ textAlign: 'center' }}>{r.conversion}%</td>
                    </tr>
                  ))}
                  {data.by_source.length === 0 && <tr><td colSpan={4} style={{ color: 'var(--text2)' }}>No leads yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          {data.stale_sample.length > 0 && (
            <div style={{ ...panel, marginTop: 14 }}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>Going quiet</div>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
                Open leads with no activity for over {data.config.stale_lead_hours} hours. After another {data.config.escalate_after_hours} hours they escalate to the manager.
              </div>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                <tbody>
                  {data.stale_sample.map(l => (
                    <tr key={l.id}>
                      <td style={{ padding: '4px 0' }}><Link href={`/crm/leads/${l.id}`}>#{l.lead_no} {l.display_name}</Link></td>
                      <td style={{ color: 'var(--text2)' }}>{l.stage_name}</td>
                      <td style={{ color: 'var(--text2)' }}>{l.owner_employee_id || 'unassigned'}</td>
                      <td style={{ color: STATUS_COLOR[l.status] }}>{relTime(l.last_activity_at || l.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
