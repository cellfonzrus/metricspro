'use client'
// Failing KPIs — high-level overview of every KPI below target (owner directive 2026-09-03:
// "Create new report from the KPI for the failing KPI … a high level overview of failing KPI with
// the option to drill down with our standard features").
//
// A VIEW over the existing KPI machinery (GET /commcalc/kpi-failing/{period}): definitions +
// targets are the /coaching resolution (carrier_kpi_metric + payout_config), store actuals are the
// /dlar-store rows, rep actuals are the pay engine's own rep_commissions.kpi_values, market comes
// from the canonical union resolver. This page only renders + filters what the backend already
// span-scoped. RULE FIVE: <StandardFilterBar> core set (month · markets · stores · reps) filtering
// client-side, what-you-see-is-what-exports. DRILL-DOWN: a store row expands to its failing
// metrics (target vs actual vs gap) and the failing reps working that store.
// A metric with no recorded value shows as "no data" — it is NEVER counted as failing.
import { useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportExportBar from '@/components/ReportExportBar'
import StatTile from '@/components/StatTile'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import type { ExportSheet } from '@/lib/export'

const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 10px', fontSize: 13, borderBottom: '1px solid var(--border)', verticalAlign: 'top' }

const gapChip = (e: any) => (
  <span key={e.kpi} style={{ display: 'inline-block', margin: '1px 4px 1px 0', padding: '2px 7px', borderRadius: 20, fontSize: 12, background: 'color-mix(in srgb, crimson 12%, var(--surface))', border: '1px solid color-mix(in srgb, crimson 35%, var(--border))' }}>
    {e.label} {e.actual}% <span style={{ color: 'var(--text2)' }}>/ {e.target}%</span>
  </span>
)

function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function FailingKpiPage() {
  const [filt, setFilt] = useState<StandardFilterValue>(() => emptyStandardFilter(thisMonth()))
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [showPassing, setShowPassing] = useState(false)

  const period = filt.period || thisMonth()
  useEffect(() => {
    setLoading(true); setErr('')
    api(`/api/v1/commcalc/kpi-failing/${encodeURIComponent(period)}`)
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [period])

  const allStores: any[] = data?.stores || []
  const allReps: any[] = data?.reps || []

  // RULE FIVE: client-side narrowing over the already-span-scoped payload (WYSIWYG exports).
  const stores = useMemo(() => filterRows(allStores, { ...filt, period: '', reps: [] }, {
    store: r => r.location || r.store_code, market: r => r.market,
  }), [allStores, filt])
  const storeKeys = useMemo(() => new Set(stores.flatMap((s: any) =>
    [s.store_code, s.location, s.address].filter(Boolean).map((x: string) => String(x).trim().toUpperCase()))), [stores])
  const reps = useMemo(() => {
    let rr = filterRows(allReps, { ...filt, period: '', stores: [], markets: [] }, { rep: r => r.rep })
    if (filt.stores.length || filt.markets.length) {
      rr = rr.filter((r: any) => storeKeys.has(String(r.store || '').trim().toUpperCase()))
    }
    return rr
  }, [allReps, filt, storeKeys])

  const failingStores = stores.filter((s: any) => s.failing_count > 0)
  const failingReps = reps.filter((r: any) => r.failing_count > 0)
  const shownStores = showPassing ? stores : failingStores
  const repsByStore = useMemo(() => {
    const m: Record<string, any[]> = {}
    for (const r of failingReps) {
      const k = String(r.store || '').trim().toUpperCase()
      ;(m[k] = m[k] || []).push(r)
    }
    return m
  }, [failingReps])

  // per-metric rollup over the FILTERED rows (so the overview follows the filters)
  const byMetric = useMemo(() => {
    const m: Record<string, { label: string; stores: number; reps: number }> = {}
    for (const s of failingStores) for (const e of s.failing) { (m[e.kpi] = m[e.kpi] || { label: e.label, stores: 0, reps: 0 }).stores++ }
    for (const r of failingReps) for (const e of r.failing) { (m[e.kpi] = m[e.kpi] || { label: e.label, stores: 0, reps: 0 }).reps++ }
    return Object.entries(m).map(([kpi, v]) => ({ kpi, ...v })).sort((a, b) => (b.stores + b.reps) - (a.stores + a.reps))
  }, [failingStores, failingReps])

  const storeOptSets = useMemo(() => optionsFromRows(allStores as any[], {
    store: (r: any) => r.location || r.store_code, market: (r: any) => r.market,
  }), [allStores])
  const repOptSets = useMemo(() => optionsFromRows(allReps as any[], { rep: (r: any) => r.rep }), [allReps])

  function sheets(): ExportSheet[] {
    const srows = shownStores.map((s: any) => ({
      store: s.location, market: s.market, failing: s.failing.map((e: any) => `${e.label} ${e.actual}%/${e.target}%`).join(' · '),
      failing_count: s.failing_count, evaluated: s.evaluated_count,
      no_data: s.no_data.map((d: any) => d.label).join(' · '),
    }))
    const rrows = failingReps.map((r: any) => ({
      rep: r.rep, store: r.store, tier: r.tier, kpis_met: r.kpis_met, total_kpis: r.total_kpis,
      failing: r.failing.map((e: any) => `${e.label} ${e.actual}%/${e.target}%`).join(' · '),
    }))
    return [
      { name: 'Failing by Store', rows: srows, columns: [
        { header: 'Store', get: (r: any) => r.store }, { header: 'Market', get: (r: any) => r.market },
        { header: 'Failing KPIs', get: (r: any) => r.failing },
        { header: '# Failing', get: (r: any) => r.failing_count }, { header: '# Evaluated', get: (r: any) => r.evaluated },
        { header: 'No data', get: (r: any) => r.no_data },
      ] },
      { name: 'Failing by Rep', rows: rrows, columns: [
        { header: 'Rep', get: (r: any) => r.rep }, { header: 'Store', get: (r: any) => r.store },
        { header: 'Tier', get: (r: any) => r.tier }, { header: 'KPIs met', get: (r: any) => r.kpis_met },
        { header: 'Total KPIs', get: (r: any) => r.total_kpis }, { header: 'Failing KPIs', get: (r: any) => r.failing },
      ] },
    ]
  }

  return (
    <div style={{ maxWidth: 1250 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 4 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎯 Failing KPIs — {period}</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 10px' }}>
            Every KPI below its target this month, at store and rep grain — targets are your configured
            KPI targets (payout config, falling back to the carrier defaults). A metric with no recorded
            value shows as <i>no data</i> and is never counted as failing.
          </p>
        </div>
        <ReportExportBar title={`Failing KPIs ${period}`} filename={`failing_kpis_${period}`} sheets={sheets()} />
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="month"
        storeOptions={storeOptSets.stores} marketOptions={storeOptSets.markets} repOptions={repOptSets.reps}
        right={
          <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <input type="checkbox" checked={showPassing} onChange={e => setShowPassing(e.target.checked)} /> show passing stores
          </label>
        }
      />

      {err && <div className="card" style={{ padding: 14, color: 'crimson', marginBottom: 12 }}>{err}</div>}
      {loading && <div style={{ color: 'var(--text2)', padding: 20 }}>Loading…</div>}

      {!loading && data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginBottom: 14 }}>
            <StatTile label="Stores failing ≥1 KPI" value={`${failingStores.length} / ${stores.length}`} />
            <StatTile label="Reps failing ≥1 KPI" value={`${failingReps.length} / ${reps.length}`} />
            <StatTile label="Failing KPI cells" value={String(failingStores.reduce((a: number, s: any) => a + s.failing_count, 0) + failingReps.reduce((a: number, r: any) => a + r.failing_count, 0))} />
            <StatTile label="Worst KPI" value={byMetric[0] ? byMetric[0].label : '—'} sub={byMetric[0] ? `${byMetric[0].stores} stores · ${byMetric[0].reps} reps` : undefined} />
          </div>

          {byMetric.length > 0 && (
            <div className="card" style={{ padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>By metric (filtered)</div>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                {byMetric.map(m => (
                  <div key={m.kpi} style={{ fontSize: 12.5 }}>
                    <b>{m.label}</b> — {m.stores} store{m.stores === 1 ? '' : 's'}, {m.reps} rep{m.reps === 1 ? '' : 's'}
                  </div>
                ))}
              </div>
            </div>
          )}

          {data.note && <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--text2)' }}>{data.note}</div>}

          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={th}></th><th style={th}>Store</th><th style={th}>Market</th>
                <th style={th}>Failing KPIs (actual / target)</th><th style={th}># Failing</th><th style={th}>No data</th>
              </tr></thead>
              <tbody>
                {shownStores.map((s: any) => {
                  const key = s.store_code || s.location
                  const kUp = [s.store_code, s.location, s.address].filter(Boolean).map((x: string) => String(x).trim().toUpperCase())
                  const storeReps = kUp.flatMap(k => repsByStore[k] || [])
                  const isOpen = !!open[key]
                  return (
                    <FragmentRow key={key} s={s} isOpen={isOpen} storeReps={storeReps}
                      toggle={() => setOpen(o => ({ ...o, [key]: !o[key] }))} />
                  )
                })}
                {shownStores.length === 0 && (
                  <tr><td style={td} colSpan={6}>
                    {stores.length === 0 ? 'No store KPI rows for this period.' : 'No store is failing a KPI under the current filters. 🎉'}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          {failingReps.length > 0 && (
            <div className="card" style={{ padding: 12, marginTop: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
                Reps failing KPIs ({failingReps.length}) — from the computed incentive run
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr><th style={th}>Rep</th><th style={th}>Store</th><th style={th}>Tier</th><th style={th}>KPIs met</th><th style={th}>Failing</th></tr></thead>
                  <tbody>
                    {failingReps.map((r: any) => (
                      <tr key={r.rep + r.store}>
                        <td style={td}>{r.rep}</td><td style={td}>{r.store}</td>
                        <td style={td}>{r.tier == null ? '—' : r.tier}</td>
                        <td style={td}>{r.kpis_met == null ? '—' : `${r.kpis_met} / ${r.total_kpis ?? '—'}`}</td>
                        <td style={td}>{r.failing.map(gapChip)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function FragmentRow({ s, isOpen, storeReps, toggle }: { s: any; isOpen: boolean; storeReps: any[]; toggle: () => void }) {
  return (
    <>
      <tr onClick={toggle} style={{ cursor: 'pointer' }}>
        <td style={td}><span style={{ display: 'inline-block', transition: 'transform .15s', transform: isOpen ? 'rotate(90deg)' : 'none' }}>▶</span></td>
        <td style={td}><b>{s.location}</b>{s.store_code ? <span style={{ color: 'var(--text2)', fontSize: 12 }}> · {s.store_code}</span> : null}</td>
        <td style={td}>{s.market || '—'}</td>
        <td style={td}>{s.failing_count ? s.failing.map(gapChip) : <span style={{ color: 'var(--text2)' }}>all met</span>}</td>
        <td style={td}>{s.failing_count} / {s.evaluated_count}</td>
        <td style={td}>{s.no_data?.length ? s.no_data.map((d: any) => d.label).join(', ') : '—'}</td>
      </tr>
      {isOpen && (
        <tr>
          <td style={{ ...td, background: 'var(--surface2, var(--surface))' }} colSpan={6}>
            <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', padding: '4px 2px' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Store KPI detail</div>
                <table style={{ borderCollapse: 'collapse' }}>
                  <thead><tr><th style={th}>KPI</th><th style={th}>Actual</th><th style={th}>Target</th><th style={th}>Gap</th></tr></thead>
                  <tbody>
                    {[...s.failing, ...(s.met || [])].map((e: any) => (
                      <tr key={e.kpi}>
                        <td style={td}>{e.label}</td>
                        <td style={{ ...td, color: e.met ? undefined : 'crimson', fontWeight: e.met ? 400 : 700 }}>{e.actual}%</td>
                        <td style={td}>{e.target}%</td>
                        <td style={td}>{e.met ? '—' : `${e.gap}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Reps failing here</div>
                {storeReps.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>No failing reps attributed to this store.</div>}
                {storeReps.map((r: any) => (
                  <div key={r.rep} style={{ fontSize: 12.5, marginBottom: 3 }}>
                    <b>{r.rep}</b>{r.tier != null ? ` (tier ${r.tier})` : ''}: {r.failing.map(gapChip)}
                  </div>
                ))}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
