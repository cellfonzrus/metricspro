'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'

// Rep coaching — which KPIs each rep met vs missed + WHY they're losing money (commission at risk
// below tier + chargebacks) + flags. Sorted by money left on the table. Admin/DM (market-scoped).
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const chip = (active: boolean): React.CSSProperties => ({ padding: '5px 11px', borderRadius: 16, border: '1px solid var(--border)', cursor: 'pointer', fontSize: 12, fontWeight: 600, background: active ? '#1E3A5F' : 'var(--surface)', color: active ? '#fff' : 'var(--text2)' })
const th: React.CSSProperties = { textAlign: 'right', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '8px 10px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }

export default function RepCoachingPage() {
  const { period } = usePeriod()
  const { user, permissions } = useAuth()
  const [markets, setMarkets] = useState<string[]>([])   // selected markets (empty = all)
  const [storeF, setStoreF] = useState('')
  const [repF, setRepF] = useState('')
  const [q, setQ] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Record<string, boolean>>({})

  // default a market manager to their own market (they can still add others)
  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarkets([user.market]) }, [user, permissions])

  const load = useCallback(() => {
    if (!period) return
    setLoading(true)
    api(`/api/v1/commcalc/coaching/${encodeURIComponent(period)}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period])
  useEffect(() => { load() }, [load])

  const allReps: any[] = data?.reps || []
  const allMarkets = Array.from(new Set(allReps.map(r => r.market).filter(Boolean))).sort() as string[]
  const marketReps = allReps.filter(r => markets.length === 0 || markets.includes(r.market))
  const storesInScope = Array.from(new Set(marketReps.map(r => r.store).filter(Boolean))).sort() as string[]
  const repsInScope = Array.from(new Set(marketReps.filter(r => !storeF || r.store === storeF).map(r => r.rep).filter(Boolean))).sort() as string[]

  // keep the store/rep selections valid as the market/store scope narrows
  useEffect(() => { if (storeF && !storesInScope.includes(storeF)) setStoreF('') }, [markets]) // eslint-disable-line
  useEffect(() => { if (repF && !repsInScope.includes(repF)) setRepF('') }, [markets, storeF]) // eslint-disable-line

  const toggleMarket = (m: string) => setMarkets(ms => ms.includes(m) ? ms.filter(x => x !== m) : [...ms, m])

  const reps: any[] = marketReps.filter(r =>
    (!storeF || r.store === storeF) &&
    (!repF || r.rep === repF) &&
    (!q || (r.rep || '').toLowerCase().includes(q.toLowerCase()) || (r.store || '').toLowerCase().includes(q.toLowerCase())))

  // tiles reflect the current filter (recomputed client-side from the visible reps)
  const s = {
    reps: reps.length,
    below_tier: reps.filter(r => (r.tier ?? 1) < 1).length,
    total_at_risk: reps.reduce((a, r) => a + (r.at_risk || 0), 0),
    total_chargebacks: reps.reduce((a, r) => a + (r.chargeback_deducted || 0), 0),
    total_money_on_table: reps.reduce((a, r) => a + (r.money_on_table || 0), 0),
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎓 Rep Coaching — {period}</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Which KPIs each rep met vs missed, and the money they're leaving on the table (commission below tier + chargebacks). Sorted by biggest opportunity.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        {allMarkets.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>Markets:</span>
            <button onClick={() => setMarkets([])} style={chip(markets.length === 0)}>All</button>
            {allMarkets.map(m => <button key={m} onClick={() => toggleMarket(m)} style={chip(markets.includes(m))}>{m}</button>)}
          </div>
        )}
        <select style={sel} value={storeF} onChange={e => { setStoreF(e.target.value); setRepF('') }}>
          <option value="">All stores ({storesInScope.length})</option>
          {storesInScope.map(st => <option key={st} value={st}>{st}</option>)}
        </select>
        <select style={sel} value={repF} onChange={e => setRepF(e.target.value)}>
          <option value="">All reps ({repsInScope.length})</option>
          {repsInScope.map(rp => <option key={rp} value={rp}>{rp}</option>)}
        </select>
        <input style={{ ...sel, width: 180 }} placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />
        {(markets.length > 0 || storeF || repF || q) &&
          <button className="btn btn-sm" onClick={() => { setMarkets([]); setStoreF(''); setRepF(''); setQ('') }}>Clear</button>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : reps.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          {allReps.length === 0 ? `No rep commissions for ${period}. (Run the commission calc first.)` : 'No reps match the current filters.'}</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12, marginBottom: 18 }}>
            <Tile label="Reps" value={`${s.reps || 0}`} sub={`${s.below_tier || 0} below full tier`} />
            <Tile label="Commission at risk" value={fmt(s.total_at_risk)} tone="#b45309" />
            <Tile label="Chargebacks lost" value={fmt(s.total_chargebacks)} tone="#b42318" />
            <Tile label="Total left on table" value={fmt(s.total_money_on_table)} tone="#b42318" />
          </div>

          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Rep', 'Store', 'Tier', 'KPIs', 'At risk', 'Chargebacks', 'Flags', 'On the table', ''].map((h, i) =>
                  <th key={i} style={i < 2 ? thL : th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {reps.map((r: any) => {
                  const k = r.rep + '|' + r.store
                  return (
                    <>
                      <tr key={k} style={{ cursor: 'pointer' }} onClick={() => setOpen(o => ({ ...o, [k]: !o[k] }))}>
                        <td style={{ ...tdL, fontWeight: 600 }}>{open[k] ? '▾ ' : '▸ '}{r.rep}</td>
                        <td style={tdL}>{r.store || '—'}</td>
                        <td style={{ ...td, color: r.tier < 1 ? '#b45309' : 'var(--green, #16794a)', fontWeight: 600 }}>{Math.round(r.tier * 100)}%</td>
                        <td style={td}>{r.kpis_met}/{r.total_kpis}</td>
                        <td style={{ ...td, color: r.at_risk > 0 ? '#b45309' : 'var(--text3)' }}>{r.at_risk > 0 ? fmt(r.at_risk) : '—'}</td>
                        <td style={{ ...td, color: r.chargeback_deducted > 0 ? '#b42318' : 'var(--text3)' }}>{r.chargeback_deducted > 0 ? fmt(r.chargeback_deducted) : '—'}</td>
                        <td style={{ ...td, color: r.flag_high > 0 ? '#b42318' : 'var(--text3)' }}>{r.flag_count || '—'}{r.flag_high ? ` (${r.flag_high}!)` : ''}</td>
                        <td style={{ ...td, fontWeight: 700, color: r.money_on_table > 0 ? '#b42318' : 'var(--text3)' }}>{fmt(r.money_on_table)}</td>
                        <td style={td}></td>
                      </tr>
                      {open[k] && (
                        <tr key={k + 'd'}>
                          <td colSpan={9} style={{ padding: '4px 14px 16px', borderTop: 'none', background: 'var(--surface2)' }}>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
                              {(r.kpis || []).map((kpi: any) => (
                                <span key={kpi.kpi} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 99, fontWeight: 600,
                                  background: kpi.met ? '#e6f7ec' : '#fde8e8', color: kpi.met ? '#16794a' : '#b42318' }}>
                                  {kpi.met ? '✓' : '✗'} {kpi.label} {kpi.actual}/{kpi.target}
                                </span>
                              ))}
                            </div>
                            {r.tier < 1 && (
                              <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                                💸 <b>{fmt(r.at_risk)}</b> of commission at risk — short on <b>{(r.short_kpis || []).join(', ') || '—'}</b>
                                {r.need_for_full ? <> · hit <b>{r.need_for_full}</b> more KPI(s) for full payout</> : null}.
                                {' '}Subtotal {fmt(r.subtotal)} → paid {fmt(r.final_payout)}.
                              </div>
                            )}
                            {r.chargeback_deducted > 0 && (
                              <div style={{ fontSize: 13, color: '#b42318', marginTop: 4 }}>🔻 {fmt(r.chargeback_deducted)} chargebacks deducted ({r.chargeback_count}).</div>
                            )}
                            {(r.coaching_notes || []).length > 0 && (
                              <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text3)' }}>
                                {r.coaching_notes.map((n: string, i: number) => <li key={i}>{n}</li>)}
                              </ul>
                            )}
                          </td>
                        </tr>
                      )}
                    </>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

const Tile = ({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) => (
  <div className="card" style={{ padding: 14 }}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: tone || 'var(--text)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)
