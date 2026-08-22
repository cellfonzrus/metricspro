'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { useActiveCarrier } from '@/lib/auth-context'
import { MultiSelect } from '@/lib/multiselect'

// Sales Comparison — period-over-period % change per item sold (Phones, BYOD, Accessories, Tablets,
// Financing), across all stores. The backend does the two-window math; this page picks the scenario
// (month-over-month / year-over-year / custom · optional week-of-month · optional "as of day N") and
// renders tiles + a store×category table via ReportShell (filter / group / export / send for free).

// Super-admin org-resolution mitigation, same as the Sales Report (reads carry no org_id in the URL).
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

// A % change → a signed, arrow-prefixed label. null = no base to compare against (a brand-new item).
function pctLabel(pct: number | null, current: number): string {
  if (pct === null || pct === undefined) return current > 0 ? '▲ new' : '—'
  const a = pct > 0 ? '▲' : pct < 0 ? '▼' : '±'
  return `${a} ${pct > 0 ? '+' : ''}${pct}%`
}
function pctColor(pct: number | null, current: number): string {
  if (pct === null || pct === undefined) return current > 0 ? 'var(--green, #16a34a)' : 'var(--text3)'
  if (pct > 0) return 'var(--green, #16a34a)'
  if (pct < 0) return 'var(--red, #dc2626)'
  return 'var(--text3)'
}

const MODES = [
  { key: 'mom', label: 'Month over month' },
  { key: 'yoy', label: 'Year over year' },
  { key: 'custom', label: 'Custom period' },
]
const WEEKS = [
  { v: 0, label: 'Whole month' },
  { v: 1, label: 'Week 1 (1–7)' },
  { v: 2, label: 'Week 2 (8–14)' },
  { v: 3, label: 'Week 3 (15–21)' },
  { v: 4, label: 'Week 4 (22–28)' },
  { v: 5, label: 'Week 5 (29–31)' },
]

export default function SalesComparisonPage() {
  // Active-carrier lens: the intro copy names financing vendors generically (never ACIMA/TW/Edge) for
  // a dual-carrier tenant. Single-carrier tenants keep the original wording.
  const { multi } = useActiveCarrier()
  const [period, setPeriod] = useState(thisMonth())
  const [mode, setMode] = useState('mom')
  const [comparePeriod, setComparePeriod] = useState('')   // custom mode only
  const [week, setWeek] = useState(0)
  const [asOf, setAsOf] = useState<string>('')             // '' = auto, else a day number, '0' = full month
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period, mode, week: String(week) })
    if (mode === 'custom' && comparePeriod) qs.set('compare_period', comparePeriod)
    if (asOf !== '') qs.set('as_of_day', asOf)   // '' → backend auto; else explicit (0 = full month)
    if (selStores.length) qs.set('stores', selStores.join(','))
    if (selMarkets.length) qs.set('markets', selMarkets.join(','))
    api(`/api/v1/commcalc/sales-comparison?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, mode, comparePeriod, week, asOf, selStores, selMarkets])
  useEffect(() => { load() }, [load])

  const cats: any[] = data?.totals_by_category || []
  const rows: any[] = data?.rows || []
  const baseLbl = data?.base_period || period
  const cmpLbl = data?.compare_period || ''

  const cols = useMemo<ExportColumn[]>(() => [
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market || '—' },
    { header: 'Item', get: r => r.category },
    { header: `Units ${baseLbl}`, get: r => r.current, align: 'right' },
    { header: `Units ${cmpLbl}`, get: r => r.previous, align: 'right' },
    { header: 'Change', get: r => (r.delta > 0 ? '+' : '') + r.delta, align: 'right' },
    { header: 'Change %', get: r => pctLabel(r.pct, r.current), align: 'right' },
    { header: `$ ${baseLbl}`, get: r => r.current_rev, money: true },
    { header: `$ ${cmpLbl}`, get: r => r.previous_rev, money: true },
  ], [baseLbl, cmpLbl])

  const storeOpts: string[] = data?.stores || []
  const marketOpts: string[] = data?.markets || []
  const filtered = selStores.length > 0 || selMarkets.length > 0

  const Tile = ({ c }: { c: any }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600, display: 'flex', gap: 6, alignItems: 'center' }}>
        {c.label}
        {c.financing && c.detection_status && c.detection_status !== 'configured' &&
          <span title="Financing detection is not fully mapped — see the Financing report settings" style={{ color: '#b45309' }}>⚠︎</span>}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 3 }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{c.current}</div>
        <div style={{ fontSize: 13, fontWeight: 700, color: pctColor(c.pct, c.current) }}>{pctLabel(c.pct, c.current)}</div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
        was {c.previous} · {fmt(c.current_rev || 0)}
      </div>
    </div>
  )

  const OverTile = ({ label, cur, prev, pct, money }: { label: string; cur: number; prev: number; pct: number | null; money?: boolean }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150, background: 'var(--surface2)' }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 3 }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{money ? fmt(cur) : cur}</div>
        <div style={{ fontSize: 13, fontWeight: 700, color: pctColor(pct, cur) }}>{pctLabel(pct, cur)}</div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>was {money ? fmt(prev) : prev}</div>
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📈 Sales Comparison</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Period-over-period change per item sold — <b>Phones, BYOD, Accessories, Tablets</b> and each
          <b> Financing</b> vendor{multi ? '' : ' (ACIMA / TW / Edge)'} — across all stores. Compare month-over-month,
          year-over-year, or week-1-over-week-1, and align both periods to the same day of the month.
        </p>
      </div>

      {/* scenario controls */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Base month{' '}
          <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Compare{' '}
          <select style={sel} value={mode} onChange={e => setMode(e.target.value)}>
            {MODES.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </label>
        {mode === 'custom' &&
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>vs month{' '}
            <input type="month" style={sel} value={comparePeriod} onChange={e => setComparePeriod(e.target.value)} />
          </label>}
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Week{' '}
          <select style={sel} value={week} onChange={e => setWeek(Number(e.target.value))}>
            {WEEKS.map(w => <option key={w.v} value={w.v}>{w.label}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }} title="Cut both periods to day ≤ N so a mid-month base compares to the same first-N-days of the other period. Blank = auto (today when the base is the current month).">
          As of day{' '}
          <input type="number" min={0} max={31} placeholder="auto" style={{ ...sel, width: 78 }} value={asOf}
                 onChange={e => setAsOf(e.target.value)} disabled={week > 0} />
        </label>
        {marketOpts.length > 0 && <MultiSelect allLabel="All markets" width={150} value={selMarkets} options={marketOpts} onChange={setSelMarkets} />}
        {storeOpts.length > 0 && <MultiSelect allLabel="All stores" width={150} value={selStores} options={storeOpts} onChange={setSelStores} searchable />}
        {filtered && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelStores([]); setSelMarkets([]) }}>Clear filters</button>}
      </div>

      {data && !data.error &&
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
          Comparing <b>{baseLbl}</b> vs <b>{cmpLbl}</b> · <b>{data.window_label}</b>
          {data?.source_meta && <span style={{ color: 'var(--text3)' }}> · {data.source_meta.base_rows} base / {data.source_meta.compare_rows} compare sale lines</span>}
        </div>}

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ Sales Comparison could not be built.</b> {data.error}
        </div>}

      {/* per-item tiles */}
      {!loading && !data?.error && cats.length > 0 &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          {cats.map(c => <Tile key={c.key} c={c} />)}
        </div>}

      {/* overall tiles */}
      {!loading && !data?.error && data?.overall &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
          <OverTile label="Transactions" cur={data.overall.current_txns} prev={data.overall.previous_txns} pct={data.overall.txns_pct} />
          <OverTile label="Revenue" cur={data.overall.current_rev} prev={data.overall.previous_rev} pct={data.overall.rev_pct} money />
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {data?.note || 'No sales in either period for this selection.'}
        </div>
      ) : !data?.error ? (
        <ReportShell
          title={`Sales Comparison — ${baseLbl} vs ${cmpLbl}`}
          subtitle={`${data?.window_label || ''}${filtered ? ' · filtered' : ' · all stores'} · % change per item sold`}
          filename={`sales-comparison-${baseLbl}-vs-${cmpLbl}`}
          columns={cols}
          rows={rows}
          totals
          stickyHeader
          defaultGroupBy="Store"
          collapsibleGroups
          groupPersistKey="sales-comparison:groupBy"
        />
      ) : null}

      {!loading && rows.length > 0 &&
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
          💡 Group by <b>Item</b> (top-left of the table) to roll every store up per category, or by <b>Store</b> to see each store’s mix.
        </div>}
    </div>
  )
}
