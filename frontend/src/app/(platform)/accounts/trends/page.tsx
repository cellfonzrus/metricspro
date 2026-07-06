'use client'
import { useState, useEffect, useMemo, useRef } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { TrendChart, type TrendSeries } from '@/components/TrendChart'
import { ExportButtons, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { captureChartPng } from '@/lib/chart-capture'

// Central Trends hub: month-over-month charts for the key financial metrics, with shared
// range/store/market filters — so lower commissions vs residual, expenses vs profit, etc. are all
// visible in one place. Each metric has its own endpoint; the default line is the company total, and
// picking markets/stores re-aggregates every chart to that subset.
const MONTH_OPTS = [3, 6, 12, 24]
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const r2 = (n: number) => Math.round((n || 0) * 100) / 100
const shortPeriod = (p: string) => {
  const m = String(p || '').match(/^([A-Za-z]+)\s+(\d{4})$/)
  if (m) return `${m[1].slice(0, 3)} '${m[2].slice(2)}`
  const m2 = String(p || '').match(/^(\d{4})-(\d{2})$/)
  if (m2) { const mn = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m2[2]]; return `${mn} '${m2[1].slice(2)}` }
  return p
}

export default function TrendsHubPage() {
  const [months, setMonths] = useState(6)
  const [res, setRes] = useState<any>(null)
  const [exp, setExp] = useState<any>(null)
  const [comm, setComm] = useState<any>(null)
  const [gp, setGp] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [msg, setMsg] = useState('')

  useEffect(() => {
    setLoading(true); setMsg('')
    Promise.all([
      api(`/api/v1/account/residual-per-sub?months=${months}&org_id=${ORG_ID}`).catch(() => null),
      api(`/api/v1/commcalc/expenses-trend?months=${months}&org_id=${ORG_ID}`).catch(() => null),
      api(`/api/v1/commcalc/commission-trend?months=${months}&org_id=${ORG_ID}`).catch(() => null),
      api(`/api/v1/commcalc/gp-trend?months=${months}&org_id=${ORG_ID}`).catch(() => null),
    ]).then(([r, e, c, g]: any) => { setRes(r); setExp(e); setComm(c); setGp(g); if (g?.note) setMsg(g.note) })
      .finally(() => setLoading(false))
  }, [months])

  const allStores = useMemo(() => {
    const m = new Map<string, any>()
    ;[res, exp, comm, gp].forEach(d => (d?.stores || []).forEach((s: any) => { if (s.store_code && !m.has(s.store_code)) m.set(s.store_code, { store_code: s.store_code, store: s.store || s.store_code, market: s.market }) }))
    return [...m.values()]
  }, [res, exp, comm, gp])
  const markets = useMemo(() => [...new Set(allStores.map(s => s.market).filter(Boolean))].sort(), [allStores])
  const filtered = selMarkets.length > 0 || selStores.length > 0
  const visibleCodes = useMemo(() => new Set(allStores.filter(s => (!selMarkets.length || selMarkets.includes(s.market)) && (!selStores.length || selStores.includes(s.store_code))).map(s => s.store_code)), [allStores, selMarkets, selStores])

  const periods: string[] = res?.months?.length ? res.months : (exp?.months || comm?.months || gp?.months || [])

  // Sum an additive metric across the company (unfiltered) or the visible stores (filtered), per period.
  const aggAt = (trend: any, key: string, p: string) => {
    if (!filtered) { const c = (trend?.company || []).find((x: any) => x.period === p); return c?.[key] || 0 }
    let v = 0; (trend?.stores || []).filter((s: any) => visibleCodes.has(s.store_code)).forEach((s: any) => { const pt = s.series.find((x: any) => x.period === p); v += pt?.[key] || 0 })
    return v
  }

  const residualChart = useMemo(() => periods.map(p => {
    let residual = 0, subs = 0, commission = 0
    if (!filtered) { const c = (res?.company || []).find((x: any) => x.period === p); residual = c?.residual || 0; subs = c?.subs || 0; commission = c?.commission || 0 }
    else (res?.stores || []).filter((s: any) => visibleCodes.has(s.store_code)).forEach((s: any) => { const pt = s.series.find((x: any) => x.period === p); residual += pt?.residual || 0; subs += pt?.subs || 0; commission += pt?.commission || 0 })
    return { name: shortPeriod(p), per_sub: r2(subs ? residual / subs : 0), commission: Math.round(commission) }
  }), [res, periods, filtered, visibleCodes])
  const expChart = useMemo(() => periods.map(p => ({ name: shortPeriod(p), total: r2(aggAt(exp, 'total', p)) })), [exp, periods, filtered, visibleCodes])
  const commChart = useMemo(() => periods.map(p => ({ name: shortPeriod(p), total: r2(aggAt(comm, 'total', p)) })), [comm, periods, filtered, visibleCodes])
  const gpChart = useMemo(() => periods.map(p => ({ name: shortPeriod(p), net_profit: r2(aggAt(gp, 'net_profit', p)), total_rev: r2(aggAt(gp, 'total_rev', p)) })), [gp, periods, filtered, visibleCodes])

  const resRef = useRef<HTMLDivElement>(null), expRef = useRef<HTMLDivElement>(null), commRef = useRef<HTMLDivElement>(null), gpRef = useRef<HTMLDivElement>(null)
  const [imgs, setImgs] = useState<string[]>([])
  useEffect(() => {
    if (loading || !periods.length) { setImgs([]); return }
    const t = setTimeout(async () => {
      const out: string[] = []
      for (const rf of [resRef, expRef, commRef, gpRef]) { const im = await captureChartPng(rf.current); if (im) out.push(im) }
      setImgs(out)
    }, 650)
    return () => clearTimeout(t)
  }, [residualChart, expChart, commChart, gpChart, loading])   // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (arr: string[], v: string, set: (x: string[]) => void) => set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v])

  const buildPayload = (): ExportPayload => {
    const columns = [
      { header: 'Month', get: (r: any) => r.name },
      { header: 'Total Expenses', money: true, align: 'right' as const, get: (r: any) => r.expenses },
      { header: 'Commissions', money: true, align: 'right' as const, get: (r: any) => r.commissions },
      { header: 'Residual / sub', money: true, align: 'right' as const, get: (r: any) => r.per_sub },
      { header: 'Net Profit', money: true, align: 'right' as const, get: (r: any) => r.net_profit },
      { header: 'Revenue', money: true, align: 'right' as const, get: (r: any) => r.revenue },
    ]
    const rows = periods.map((p, i) => ({
      name: shortPeriod(p), expenses: r2(aggAt(exp, 'total', p)), commissions: r2(aggAt(comm, 'total', p)),
      per_sub: residualChart[i]?.per_sub || 0, net_profit: r2(aggAt(gp, 'net_profit', p)), revenue: r2(aggAt(gp, 'total_rev', p)),
    }))
    return { title: `Trends — ${filtered ? 'selected stores' : 'company'} summary`, subtitle: `Last ${months} months`, filename: `trends-${months}mo`, sheets: [{ name: 'Trends', columns, rows }], chartImages: imgs }
  }

  const scope = filtered ? `${visibleCodes.size} store(s)` : 'all stores'
  const card: React.CSSProperties = { padding: '14px 12px 8px', marginBottom: 16 }
  const cardTitle: React.CSSProperties = { fontSize: 13, fontWeight: 600, marginBottom: 8, paddingLeft: 6 }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Trends</h1>
          <p style={{ color: 'var(--text2)', fontSize: 13, margin: '4px 0 0' }}>Month-over-month across expenses, commissions, residual and profit · {scope}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)', maxWidth: 320 }}>{msg}</span>}
          {!loading && periods.length > 0 && <>
            <ExportButtons payload={buildPayload} compact />
            <SendReportButton exportPayload={buildPayload} title="Trends summary" compact />
          </>}
        </div>
      </div>

      {/* Shared filters */}
      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Range&nbsp;
          <select style={inp} value={months} onChange={e => setMonths(+e.target.value)}>
            {MONTH_OPTS.map(m => <option key={m} value={m}>Last {m} months</option>)}
          </select>
        </label>
        {markets.length > 0 && <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>Market:</span>
          {markets.map(m => <button key={m} className="btn" style={{ padding: '3px 9px', fontSize: 12, background: selMarkets.includes(m) ? 'var(--accent)' : 'var(--surface)', color: selMarkets.includes(m) ? 'white' : 'var(--text)' }} onClick={() => toggle(selMarkets, m, setSelMarkets)}>{m}</button>)}
        </div>}
        {(selMarkets.length > 0 || selStores.length > 0) && <button className="btn" style={{ padding: '3px 9px', fontSize: 12 }} onClick={() => { setSelMarkets([]); setSelStores([]) }}>Clear</button>}
      </div>
      {allStores.length > 0 && (
        <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', maxHeight: 96, overflowY: 'auto' }}>
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>Store:</span>
          {allStores.filter(s => !selMarkets.length || selMarkets.includes(s.market)).map(s => (
            <button key={s.store_code} className="btn" style={{ padding: '3px 9px', fontSize: 12, background: selStores.includes(s.store_code) ? 'var(--accent)' : 'var(--surface)', color: selStores.includes(s.store_code) ? 'white' : 'var(--text)' }} onClick={() => toggle(selStores, s.store_code, setSelStores)}>{String(s.store).slice(0, 20)}</button>
          ))}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !periods.length ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No trend data yet — upload/compute a couple of months first.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 16 }}>
          <div className="card" style={card}>
            <div style={cardTitle}>💵 Residual per subscriber <span style={{ fontWeight: 400, color: 'var(--text3)' }}>vs commission paid</span></div>
            <div ref={resRef}>
              <TrendChart data={residualChart} height={260}
                series={[{ key: 'per_sub', name: 'Residual / sub', axis: 'left', money: true }, { key: 'commission', name: 'Commission', color: '#94a3b8', axis: 'right', money: true, dashed: true }]}
                leftLabel="residual per subscriber ($)" rightLabel="commission paid ($)" />
            </div>
          </div>
          <div className="card" style={card}>
            <div style={cardTitle}>🧾 Total Expenses</div>
            <div ref={expRef}>
              <TrendChart data={expChart} height={260} series={[{ key: 'total', name: 'Total expenses', color: '#dc2626', money: true }]} leftLabel="total store expenses ($)" />
            </div>
          </div>
          <div className="card" style={card}>
            <div style={cardTitle}>🧮 Commissions Paid</div>
            <div ref={commRef}>
              <TrendChart data={commChart} height={260} series={[{ key: 'total', name: 'Commissions', color: '#f59e0b', money: true }]} leftLabel="commission paid to reps ($)" />
            </div>
          </div>
          <div className="card" style={card}>
            <div style={cardTitle}>📈 Net Profit <span style={{ fontWeight: 400, color: 'var(--text3)' }}>& revenue</span>{gp?.pending_months?.length ? <span style={{ fontWeight: 400, color: '#f59e0b' }}> · {gp.pending_months.length} month(s) computing</span> : ''}</div>
            <div ref={gpRef}>
              <TrendChart data={gpChart} height={260}
                series={[{ key: 'net_profit', name: 'Net profit', color: '#16a34a', money: true }, { key: 'total_rev', name: 'Revenue', color: '#2e75b6', money: true, dashed: true }]}
                leftLabel="net profit & revenue ($)" />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
