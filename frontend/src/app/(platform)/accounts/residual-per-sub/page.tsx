'use client'
import { useState, useEffect, useMemo, useRef } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { TrendChart, type TrendSeries } from '@/components/TrendChart'
import { ExportButtons, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { captureChartPng } from '@/lib/chart-capture'
import { useColumnResize, ResizeHandle } from '@/lib/col-resize'
import { RestrictedReport, useReportGrant, isForbidden, RESIDUAL_PER_SUB_GRANT } from '../_components/ReportGate'

// Residual (MI+ATU) per subscriber per store, month over month, with a commission overlay — to see
// the effect of lower commissions on the residual payout. Data: GET /account/residual-per-sub?months=.
// Store/market filtering is client-side (same pattern as the GP report).
//
// PERMISSION (owner directive 2026-07-29): DEFAULT-CLOSED behind the 'residual_per_sub' data grant.
// Super-admins / scope-'all' roles / role 'admin' pass; everyone else needs an explicit per-role grant
// and sees the lock note instead of the report. The BACKEND is the source of truth (403 from
// GET /account/residual-per-sub) — this page also honors that 403 if the client-side hint disagrees.

const MONTH_OPTS = [3, 6, 12, 24]
const METRICS = [
  { k: 'per_sub', label: 'Residual / subscriber', money: true },
  { k: 'residual', label: 'Total residual', money: true },
  { k: 'subs', label: 'Subscribers', money: false },
]
const COLORS = ['#2e75b6', '#16a34a', '#dc2626', '#f59e0b', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#b91c1c']
const MAX_LINES = 12
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

const r2 = (n: number) => Math.round((n || 0) * 100) / 100
const shortPeriod = (p: string) => {
  const m = String(p || '').match(/^([A-Za-z]+)\s+(\d{4})$/)
  if (m) return `${m[1].slice(0, 3)} '${m[2].slice(2)}`
  const m2 = String(p || '').match(/^(\d{4})-(\d{2})$/)
  if (m2) { const mn = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m2[2]]; return `${mn} '${m2[1].slice(2)}` }
  return p
}
const usd0 = (v: number) => `$${Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + 'k' : Math.round(v)}`

export default function ResidualPerSubPage() {
  const [months, setMonths] = useState(6)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [metric, setMetric] = useState('per_sub')
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [showCommission, setShowCommission] = useState(true)
  const [perStore, setPerStore] = useState(false)
  const [msg, setMsg] = useState('')
  const chartWrapRef = useRef<HTMLDivElement>(null)
  const [chartImg, setChartImg] = useState('')
  const cw = useColumnResize()
  const { granted, ready } = useReportGrant(RESIDUAL_PER_SUB_GRANT)
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    if (!ready) return                                           // wait for permissions before deciding
    if (!granted) { setLoading(false); setData(null); return }   // no grant → don't even ask
    setLoading(true); setMsg(''); setDenied(false)
    api(`/api/v1/account/residual-per-sub?months=${months}&org_id=${ORG_ID}`)
      .then(setData)
      .catch(e => { if (isForbidden(e)) { setDenied(true); setData(null) } else setMsg('Load failed: ' + (e?.message || e)) })
      .finally(() => setLoading(false))
  }, [months, granted, ready])

  const periods: string[] = data?.months || []
  const stores: any[] = data?.stores || []
  const markets: string[] = data?.markets || []
  const metricDef = METRICS.find(m => m.k === metric)!

  const visibleStores = useMemo(() => stores.filter(s =>
    (!selMarkets.length || selMarkets.includes(s.market)) &&
    (!selStores.length || selStores.includes(s.store))
  ), [stores, selMarkets, selStores])
  const filtered = selMarkets.length > 0 || selStores.length > 0
  const breakout = perStore && filtered

  const metricVal = (o: any) => o == null ? 0
    : metric === 'per_sub' ? (o.subs ? o.residual / o.subs : 0)
      : metric === 'residual' ? o.residual : o.subs

  // Aggregate per period: exact company line when unfiltered, else sum of the visible stores.
  const aggByPeriod = useMemo(() => {
    const map: Record<string, any> = {}
    periods.forEach(p => (map[p] = { residual: 0, subs: 0, commission: 0 }))
    if (!filtered) (data?.company || []).forEach((c: any) => { map[c.period] = { residual: c.residual, subs: c.subs, commission: c.commission } })
    else visibleStores.forEach(s => s.series.forEach((pt: any) => { const m = map[pt.period]; if (m) { m.residual += pt.residual; m.subs += pt.subs; m.commission += pt.commission } }))
    return map
  }, [periods, filtered, data, visibleStores])

  const chartLines = breakout
    ? visibleStores.slice(0, MAX_LINES).map((s, i) => ({ key: s.store, name: s.store.slice(0, 22), color: COLORS[i % COLORS.length] }))
    : [{ key: '__agg', name: filtered ? 'Selected stores' : 'All stores', color: COLORS[0] }]

  const chartData = useMemo(() => periods.map(p => {
    const row: any = { period: p, name: shortPeriod(p) }
    row.commission = Math.round((aggByPeriod[p]?.commission) || 0)
    if (breakout) visibleStores.slice(0, MAX_LINES).forEach(s => { row[s.store] = r2(metricVal(s.series.find((x: any) => x.period === p))) })
    else row['__agg'] = r2(metricVal(aggByPeriod[p]))
    return row
  }), [periods, aggByPeriod, breakout, visibleStores, metric])

  const toggle = (arr: string[], v: string, set: (x: string[]) => void) => set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v])

  // Chart series for the shared <TrendChart>: the metric line(s) on the left axis + commission on the right.
  const chartSeries: TrendSeries[] = [
    ...chartLines.map(l => ({ key: l.key, name: l.name, color: l.color, axis: 'left' as const, money: metricDef.money })),
    ...(showCommission ? [{ key: 'commission', name: 'Commission', color: '#94a3b8', axis: 'right' as const, money: true, dashed: true }] : []),
  ]

  // Structured payload → Excel / PDF / Print / Send (shared lib), reflecting the current metric + filters.
  const buildPayload = (): ExportPayload => {
    const rows = filtered ? visibleStores : stores
    const columns = [
      { header: 'Store', get: (r: any) => r.store },
      { header: 'Market', get: (r: any) => r.market },
      ...periods.map(p => ({ header: shortPeriod(p), money: metricDef.money, align: 'right' as const, get: (r: any) => r2(metricVal(r.series.find((x: any) => x.period === p))) })),
      { header: 'Total', money: metricDef.money, align: 'right' as const, get: (r: any) => r2(metricVal(r.totals)) },
    ]
    return {
      title: `Residual per Subscriber — ${metricDef.label}`,
      subtitle: `Last ${months} months${filtered ? ' · filtered' : ''}`,
      filename: `residual-per-sub-${months}mo`,
      sheets: [{ name: 'Residual per Sub', columns, rows }],
      chartImage: chartImg,
    }
  }

  // Capture the rendered chart to a PNG (debounced) so PDF / Print / Send embed the graph, not just the table.
  useEffect(() => {
    if (loading || !chartData.length) { setChartImg(''); return }
    const t = setTimeout(() => { captureChartPng(chartWrapRef.current).then(setChartImg).catch(() => setChartImg('')) }, 450)
    return () => clearTimeout(t)
  }, [chartData, chartSeries, loading])   // eslint-disable-line react-hooks/exhaustive-deps

  const rowsToShow = filtered ? visibleStores : stores

  // Gate LAST (after every hook, so hook order never changes): once permissions are known, no grant
  // client-side or a backend 403 → the lock note replaces the whole report (no chart, no table, no
  // export/send buttons). Before `ready` the page just shows its spinner (nothing is fetched yet).
  if (ready && (!granted || denied)) {
    return <RestrictedReport title="Residual per Subscriber" grantKey={RESIDUAL_PER_SUB_GRANT}
      subtitle="Residual (MI + ATU) per paid subscriber, month over month, vs commissions paid." />
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Residual per Subscriber</h1>
          <p style={{ color: 'var(--text2)', fontSize: 13, margin: '4px 0 0' }}>
            Residual (MI + ATU) per paid subscriber, month over month, vs commissions paid — to see the effect of lower commissions on residual.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--red)' }}>{msg}</span>}
          {!loading && periods.length > 0 && <>
            <ExportButtons payload={buildPayload} compact />
            <SendReportButton exportPayload={buildPayload} title={`Residual per Subscriber — ${metricDef.label}`} compact />
          </>}
        </div>
      </div>

      {/* Controls */}
      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Range&nbsp;
          <select style={inp} value={months} onChange={e => setMonths(+e.target.value)}>
            {MONTH_OPTS.map(m => <option key={m} value={m}>Last {m} months</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Metric&nbsp;
          <select style={inp} value={metric} onChange={e => setMetric(e.target.value)}>
            {METRICS.map(m => <option key={m.k} value={m.k}>{m.label}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input type="checkbox" checked={showCommission} onChange={e => setShowCommission(e.target.checked)} /> Commission overlay
        </label>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, cursor: filtered ? 'pointer' : 'not-allowed', opacity: filtered ? 1 : 0.5 }}
          title={filtered ? 'One line per selected store' : 'Filter to a market or store first'}>
          <input type="checkbox" checked={breakout} disabled={!filtered} onChange={e => setPerStore(e.target.checked)} /> Break out by store
        </label>
      </div>

      {/* Market + store filters */}
      {(markets.length > 0 || stores.length > 0) && !loading && (
        <div className="card" style={{ padding: 12, marginBottom: 14 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: stores.length ? 8 : 0 }}>
            <span style={{ fontSize: 11, color: 'var(--text3)', marginRight: 4 }}>Market:</span>
            {markets.map(m => (
              <button key={m} onClick={() => toggle(selMarkets, m, setSelMarkets)}
                className="btn" style={{ padding: '3px 9px', fontSize: 12, background: selMarkets.includes(m) ? 'var(--accent)' : 'var(--surface)', color: selMarkets.includes(m) ? 'white' : 'var(--text)' }}>{m}</button>
            ))}
            {(selMarkets.length > 0 || selStores.length > 0) && <button className="btn" style={{ padding: '3px 9px', fontSize: 12 }} onClick={() => { setSelMarkets([]); setSelStores([]) }}>Clear</button>}
          </div>
          {stores.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', maxHeight: 96, overflowY: 'auto' }}>
              <span style={{ fontSize: 11, color: 'var(--text3)', marginRight: 4 }}>Store:</span>
              {stores.filter(s => !selMarkets.length || selMarkets.includes(s.market)).map(s => (
                <button key={s.store} onClick={() => toggle(selStores, s.store, setSelStores)}
                  className="btn" style={{ padding: '3px 9px', fontSize: 12, background: selStores.includes(s.store) ? 'var(--accent)' : 'var(--surface)', color: selStores.includes(s.store) ? 'white' : 'var(--text)' }}>{s.store.slice(0, 22)}</button>
              ))}
            </div>
          )}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !periods.length ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>{data?.note || 'No residual data yet.'}</div>
      ) : (
        <>
          {/* Trend chart — dual axis: metric (left) + commission (right) */}
          <div className="card" style={{ padding: '14px 12px 8px', marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, paddingLeft: 6 }}>
              {metricDef.label} — {breakout ? `${chartLines.length} store${chartLines.length > 1 ? 's' : ''}` : (filtered ? 'selected stores' : 'all stores')}
              {breakout && visibleStores.length > MAX_LINES && <span style={{ color: 'var(--text3)', fontWeight: 400 }}> (top {MAX_LINES} of {visibleStores.length} by residual)</span>}
            </div>
            <div ref={chartWrapRef}>
              <TrendChart data={chartData} series={chartSeries} height={330}
                leftMoney={metricDef.money} rightMoney
                leftLabel={`${metricDef.label}${metricDef.money ? ' ($)' : ''}`}
                rightLabel="commission paid ($)"
                hint="A subscriber = a distinct phone number paid MI+ATU that month." />
            </div>
          </div>

          {/* Matrix table */}
          <div style={{ overflowX: 'auto', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
            {cw.dirty && <div style={{ padding: '4px 10px', fontSize: 11, color: 'var(--text3)' }}><button className="btn" style={{ padding: '2px 8px', fontSize: 11 }} onClick={cw.resetAll}>↺ Reset column widths</button> <span>drag a column edge to resize · double-click to auto-fit</span></div>}
            <table style={{ borderCollapse: 'collapse', width: '100%', tableLayout: 'auto' }}>
              <colgroup>
                <col style={{ width: cw.width('store') }} />
                {periods.map(p => <col key={p} style={{ width: cw.width(p) }} />)}
                <col style={{ width: cw.width('total') }} />
              </colgroup>
              <thead>
                <tr style={{ background: 'var(--accent)' }}>
                  <th style={{ padding: '10px 14px', color: 'white', fontSize: 12, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--accent)', whiteSpace: 'nowrap' }}>Store · {metricDef.label}<ResizeHandle onDown={e => cw.start('store', e)} onReset={() => cw.reset('store')} /></th>
                  {periods.map(p => <th key={p} style={{ padding: '10px 10px', color: 'white', fontSize: 11, textAlign: 'right', whiteSpace: 'nowrap', position: 'relative' }}>{shortPeriod(p)}<ResizeHandle onDown={e => cw.start(p, e)} onReset={() => cw.reset(p)} /></th>)}
                  <th style={{ padding: '10px 12px', color: 'white', fontSize: 11, textAlign: 'right', whiteSpace: 'nowrap', position: 'relative' }}>Total<ResizeHandle onDown={e => cw.start('total', e)} onReset={() => cw.reset('total')} /></th>
                </tr>
              </thead>
              <tbody>
                <tr style={{ background: '#eef6ff', fontWeight: 600 }}>
                  <td style={{ padding: '7px 14px', fontSize: 12, position: 'sticky', left: 0, background: '#eef6ff' }}>{filtered ? 'Selected stores' : 'All stores'}</td>
                  {periods.map(p => <td key={p} style={{ padding: '7px 10px', textAlign: 'right', fontSize: 12 }}>{metricDef.money ? fmt(metricVal(aggByPeriod[p])) : Math.round(metricVal(aggByPeriod[p])).toLocaleString()}</td>)}
                  <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 12 }}>—</td>
                </tr>
                {rowsToShow.map((s, i) => (
                  <tr key={s.store} style={{ background: i % 2 ? '#fafbfc' : 'white' }}>
                    <td style={{ padding: '6px 14px', fontSize: 12, position: 'sticky', left: 0, background: i % 2 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                      {s.store} <span style={{ color: 'var(--text3)', fontSize: 10 }}>· {s.market}</span>
                    </td>
                    {periods.map(p => {
                      const pt = s.series.find((x: any) => x.period === p)
                      const v = metricVal(pt)
                      return <td key={p} style={{ padding: '6px 10px', textAlign: 'right', fontSize: 12, borderBottom: '1px solid var(--border)', color: v ? 'var(--text)' : 'var(--text3)' }}>{v ? (metricDef.money ? fmt(v) : Math.round(v).toLocaleString()) : '—'}</td>
                    })}
                    <td style={{ padding: '6px 12px', textAlign: 'right', fontSize: 12, fontWeight: 600, borderBottom: '1px solid var(--border)' }}>{metricDef.money ? fmt(metricVal(s.totals)) : Math.round(metricVal(s.totals)).toLocaleString()}</td>
                  </tr>
                ))}
                <tr style={{ background: '#f8fafc', color: 'var(--text2)' }}>
                  <td style={{ padding: '7px 14px', fontSize: 11, fontStyle: 'italic', position: 'sticky', left: 0, background: '#f8fafc' }}>Commission paid (Σ)</td>
                  {periods.map(p => <td key={p} style={{ padding: '7px 10px', textAlign: 'right', fontSize: 11 }}>{fmt(aggByPeriod[p]?.commission || 0)}</td>)}
                  <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 11 }}>—</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
