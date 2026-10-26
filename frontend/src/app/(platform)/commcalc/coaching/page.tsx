'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import { narrowPanel, screenKey, agreesToTheCent } from '@/lib/drill-narrow'
import { GoogleRatingChips, GoogleRatingDetail, ratingsText, useGoogleRatings } from '../_lib/googleRatings'

// Rep coaching — which KPIs each rep met vs missed + WHY they're losing money (commission at risk
// below tier + chargebacks) + flags. Sorted by money left on the table. Admin/DM (market-scoped).
//
// WHERE EVERY FIGURE CAME FROM (index §CORE-17.3t). The server serves a panel per summary figure
// from commcalc/performance_coaching_drilldown.py, folded over the SAME rep rows this page shows.
//
// THIS PAGE FOLDS ITS OWN TILES, so `lib/drill-narrow` IS WIRED HERE and each tile reads its number
// off its narrowed panel through the shared `agreesToTheCent` gate — the tile and its evidence are
// one fold by construction. `harness_batch20_drilldown.py` §F fails the build if that stops being
// true, in BOTH directions, so this cannot quietly revert to a panel about the whole company.
//
// Four figures drill and three do not. The Reps COUNT is the row count the panel below already
// states; the tier percentage and the KPIs-met fraction are ratios, and the figures they divide are
// the ones that open (§CORE-17.3h).

// The report's own columns on each drill row, off `fields` — never a fork of the shared panel.
const COACH_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Store', get: (r: DrillRow) => String(r.fields?.store ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.store ?? '') },
  { header: 'Full tier', get: (r: DrillRow) => String(r.fields?.['full tier'] ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.['full tier'] ?? '') },
  { header: 'Pay breakdown', get: (r: DrillRow) => String(r.fields?.['pay breakdown'] ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.['pay breakdown'] ?? '') },
]
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const chip = (active: boolean): React.CSSProperties => ({ padding: '5px 11px', borderRadius: 16, border: '1px solid var(--border)', cursor: 'pointer', fontSize: 12, fontWeight: 600, background: active ? 'var(--c-bg-1e3a5f)' : 'var(--surface)', color: active ? 'var(--c-fg-ffffff)' : 'var(--text2)' })
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
  useEffect(() => { if (storeF && !storesInScope.includes(storeF)) setStoreF('') }, [markets]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (repF && !repsInScope.includes(repF)) setRepF('') }, [markets, storeF]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleMarket = (m: string) => setMarkets(ms => ms.includes(m) ? ms.filter(x => x !== m) : [...ms, m])

  // Export columns — the same money/KPI fields the coaching table shows (what-you-see-is-what-exports).
  const cols: ExportColumn[] = [
    { header: 'Rep', field: 'rep', role: 'rep', get: (r: any) => r.rep },
    { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store || '' },
    { header: 'Tier %', field: 'tier', get: (r: any) => Math.round((r.tier ?? 0) * 100) },
    { header: 'KPIs met', field: 'kpis', get: (r: any) => `${r.kpis_met}/${r.total_kpis}` },
    { header: 'At risk', field: 'at_risk', money: true, get: (r: any) => r.at_risk || 0 },
    { header: 'Chargebacks', field: 'chargeback_deducted', money: true, get: (r: any) => r.chargeback_deducted || 0 },
    { header: 'Ops chargebacks', field: 'ops_chargeback_deduction', money: true, get: (r: any) => r.ops_chargeback_deduction || 0 },
    { header: 'Flags', field: 'flag_count', get: (r: any) => r.flag_count || 0 },
    { header: 'On the table', field: 'money_on_table', money: true, get: (r: any) => r.money_on_table || 0 },
  ]

  const reps: any[] = marketReps.filter(r =>
    (!storeF || r.store === storeF) &&
    (!repF || r.rep === repF) &&
    (!q || (r.rep || '').toLowerCase().includes(q.toLowerCase()) || (r.store || '').toLowerCase().includes(q.toLowerCase())))

  // Google store rating per rep (owner 2026-08-06) — ONE batched call for the reps the current filters
  // leave on screen. Display-only: it is context for the coaching conversation and is never part of the
  // "money on the table" arithmetic. Renders nothing until the Google Reviews endpoints are live.
  const { ratingsFor: googleFor, hasAny: hasGoogle } = useGoogleRatings(reps.map(r => r.rep))
  const exportCols: ExportColumn[] = hasGoogle
    ? [...cols, { header: 'Google rating', field: 'google_rating', get: (r: any) => ratingsText(googleFor(r.rep)) }]
    : cols

  // tiles reflect the current filter (recomputed client-side from the visible reps)
  const s: Record<string, number> = {
    reps: reps.length,
    below_tier: reps.filter(r => (r.tier ?? 1) < 1).length,
    total_at_risk: reps.reduce((a, r) => a + (r.at_risk || 0), 0),
    total_chargebacks: reps.reduce((a, r) => a + (r.chargeback_deducted || 0), 0),
    // Folded here so the posted-penalty panel has a figure to be gated against. It is NOT printed as
    // a tile of its own: it is a component of the total beside it, offered as a link there, which is
    // the batch-19 posture for a figure a reader needs and no tile shows.
    total_ops_chargebacks: reps.reduce((a, r) => a + (r.ops_chargeback_deduction || 0), 0),
    total_money_on_table: reps.reduce((a, r) => a + (r.money_on_table || 0), 0),
  }

  // THE PANELS, NARROWED TO THE ROWS ON SCREEN — the same selection `reps` is, matched on the row
  // key the server served with every drill row (rep plus store, which is what this page filters on).
  const coachPanels: Record<string, DrillPanel> = data?.drilldown || {}
  const coachKeys = new Set(reps.map(r => screenKey({ rep: r.rep, store: r.store })))
  const coachNarrowed: Record<string, DrillPanel> = {}
  Object.keys(coachPanels).forEach(k => {
    const p = narrowPanel(coachPanels[k], (_r: DrillRow, key: string) => coachKeys.has(key))
    if (p) coachNarrowed[k] = p
  })
  // Each tile reads its number off its narrowed panel where the two agree to the cent, through the
  // SHARED gate, and keeps this page's own fold where they do not. A disagreement costs the drill
  // link, never the number.
  const coachTile = (k: string): number => {
    const n = s[k] || 0
    return agreesToTheCent(coachNarrowed[k], n) ? Number(coachNarrowed[k]?.figure?.amount ?? n) : n
  }
  const coachDrill = useDrillTile(coachNarrowed, (k, p) => agreesToTheCent(p, s[k] || 0))

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎓 Rep Coaching — {period}</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Which KPIs each rep met vs missed, and the money they&apos;re leaving on the table (incentive below tier + chargebacks). Sorted by biggest opportunity.
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
        <div style={{ flex: 1 }} />
        {reps.length > 0 && <ReportExportBar title={`Rep Coaching ${period}`} filename={`rep_coaching_${String(period).replace(/\s+/g, '_')}`} columns={exportCols} rows={reps} />}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : reps.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          {allReps.length === 0 ? `No rep incentives for ${period}. (Run the incentive calc first.)` : 'No reps match the current filters.'}</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12, marginBottom: 18 }}>
            <Tile label="Reps" value={`${s.reps || 0}`} sub={`${coachTile('below_tier')} below full tier`}
              links={<DrillTileLinks drills={coachDrill.dl('below_tier', 'who is below full tier')} open={coachDrill.open} onToggle={coachDrill.toggle} />} />
            <Tile label="Incentive at risk" value={fmt(coachTile('total_at_risk'))} tone="#b45309"
              links={<DrillTileLinks drills={coachDrill.dl('total_at_risk', 'rows behind this')} open={coachDrill.open} onToggle={coachDrill.toggle} />} />
            <Tile label="Chargebacks lost" value={fmt(coachTile('total_chargebacks'))} tone="#b42318"
              links={<DrillTileLinks drills={coachDrill.dl('total_chargebacks', 'rows behind this')} open={coachDrill.open} onToggle={coachDrill.toggle} />} />
            <Tile label="Total left on table" value={fmt(coachTile('total_money_on_table'))} tone="#b42318"
              links={<DrillTileLinks drills={coachDrill.dl('total_money_on_table', 'rows behind this', 'total_ops_chargebacks', 'posted penalties')} open={coachDrill.open} onToggle={coachDrill.toggle} />} />
          </div>
          {coachDrill.panel && <DrillDownPanel panel={coachDrill.panel} extraColumns={COACH_DRILL_COLUMNS} />}

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
                        <td style={{ ...tdL, fontWeight: 600 }}>
                          {open[k] ? '▾ ' : '▸ '}{r.rep}
                          <span style={{ display: 'block', marginTop: 2 }}><GoogleRatingChips list={googleFor(r.rep)} compact /></span>
                        </td>
                        <td style={tdL}>{r.store || '—'}</td>
                        <td style={{ ...td, color: r.tier < 1 ? 'var(--c-fg-b45309)' : 'var(--green, var(--c-fg-16794a))', fontWeight: 600 }}>{Math.round(r.tier * 100)}%</td>
                        <td style={td}>{r.kpis_met}/{r.total_kpis}</td>
                        <td style={{ ...td, color: r.at_risk > 0 ? 'var(--c-fg-b45309)' : 'var(--text3)' }}>{r.at_risk > 0 ? fmt(r.at_risk) : '—'}</td>
                        <td style={{ ...td, color: r.chargeback_deducted > 0 ? 'var(--c-fg-b42318)' : 'var(--text3)' }}>{r.chargeback_deducted > 0 ? fmt(r.chargeback_deducted) : '—'}</td>
                        <td style={{ ...td, color: r.flag_high > 0 ? 'var(--c-fg-b42318)' : 'var(--text3)' }}>{r.flag_count || '—'}{r.flag_high ? ` (${r.flag_high}!)` : ''}</td>
                        <td style={{ ...td, fontWeight: 700, color: r.money_on_table > 0 ? 'var(--c-fg-b42318)' : 'var(--text3)' }}>{fmt(r.money_on_table)}</td>
                        <td style={td}></td>
                      </tr>
                      {open[k] && (
                        <tr key={k + 'd'}>
                          <td colSpan={9} style={{ padding: '4px 14px 16px', borderTop: 'none', background: 'var(--surface2)' }}>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
                              {(r.kpis || []).map((kpi: any) => (
                                <span key={kpi.kpi} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 99, fontWeight: 600,
                                  background: kpi.met ? 'var(--c-bg-e6f7ec)' : 'var(--c-bg-fde8e8)', color: kpi.met ? 'var(--c-fg-16794a)' : 'var(--c-fg-b42318)' }}>
                                  {kpi.met ? '✓' : '✗'} {kpi.label} {kpi.actual}/{kpi.target}
                                </span>
                              ))}
                            </div>
                            {r.tier < 1 && (
                              <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                                💸 <b>{fmt(r.at_risk)}</b> of incentive at risk — short on <b>{(r.short_kpis || []).join(', ') || '—'}</b>
                                {r.need_for_full ? <> · hit <b>{r.need_for_full}</b> more KPI(s) for full payout</> : null}.
                                {' '}Subtotal {fmt(r.subtotal)} → paid {fmt(r.final_payout)}.
                              </div>
                            )}
                            {r.chargeback_deducted > 0 && (
                              <div style={{ fontSize: 13, color: 'var(--c-fg-b42318)', marginTop: 4 }}>🔻 {fmt(r.chargeback_deducted)} chargebacks deducted ({r.chargeback_count}).</div>
                            )}
                            {/* Ops-accountability chargebacks (retail-ops, POSTED) deducted from this person's
                                commission — read-only; posted/waived on the DM Verify page. */}
                            {(r.ops_chargeback_deduction || 0) > 0 && (
                              <div style={{ fontSize: 13, color: 'var(--c-fg-b42318)', marginTop: 4 }}>
                                🔻 {fmt(r.ops_chargeback_deduction)} ops chargebacks deducted:
                                <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12 }}>
                                  {(r.ops_chargeback_lines || []).map((l: any, i: number) => (
                                    <li key={i}>{l.label} — −{fmt(l.amount)} <span style={{ color: 'var(--text3)', textTransform: 'uppercase' }}>({l.status || 'posted'})</span></li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {(r.coaching_notes || []).length > 0 && (
                              <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text3)' }}>
                                {r.coaching_notes.map((n: string, i: number) => <li key={i}>{n}</li>)}
                              </ul>
                            )}
                            {/* The rep's Google store rating(s) in full — rating vs target per store, any
                                open action plan, and Google's recent reviews behind a toggle. Coaching
                                context only; it moves no number in this row. */}
                            <GoogleRatingDetail repName={r.rep} title={`Google store ratings — ${r.rep}`} />
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

const Tile = ({ label, value, sub, tone, links }: { label: string; value: string; sub?: string; tone?: string; links?: React.ReactNode }) => (
  <div className="card" style={{ padding: 14 }}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: tone || 'var(--text)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    {links}
  </div>
)
