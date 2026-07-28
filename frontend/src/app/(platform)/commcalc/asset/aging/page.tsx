'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'
import { NoLedgerData } from '../_shared/NoLedgerData'

// "(no market)" bucket (2026-07-27 market-filter-dropdown fix): rows that never matched
// store_mapping never showed under any market filter before this — this sentinel makes them an
// explicit, pickable option instead of silently unreachable. Must match router.py's
// NO_MARKET_SENTINEL exactly.
const NO_MARKET_VALUE = '__no_market__'

type Row = {
  id: number; store: string; market: string; esn_imei: string|null; phone_number: string|null
  device_model: string|null; category: string|null; status: string|null
  acquired_date: string|null; due_date: string|null; owed_to_vip: number|null
  selling_price: number|null; days_aged?: number
  vip_invoice_number: string|null; vip_invoice_date: string|null
  physically_missing?: boolean; investigation_remark?: string
}
type Bucket = { count: number; owed: number; rows: Row[] }
type ByModel = { device_model: string; under45: number; warn: number; missed: number; zero: number; unknown_age: number; total: number; owed: number }
type Aging = {
  today: string; data_as_of: string|null
  buckets: { under45: Bucket; warn: Bucket; missed: Bucket }
  zero_inventory: { count: number; rows: Row[] }
  unknown_age: { count: number; owed: number; rows: Row[] }
  totals: {
    flagged_count: number; flagged_owed: number
    total_amount: number; total_amount_column: string; total_phones_outstanding: number
  }
  by_model: ByModel[]
  by_model_meta: { total_models: number; shown: number; omitted: number }
  bucket_basis: string
}
// Display-level variant merging (2026-07-28 owner-driven addition) — a "store" picker option can
// represent MULTIPLE raw asset_ledger.store spelling variants ("116-36 Springfield Blvd" +
// "11636 Springfield Blvd"); picking it expands to all its `variants` joined into the existing
// comma-separated `store` query param (the multi-select filter already built this package).
type StoreGroup = { key: string; display: string; variants: string[]; also_seen_as: string[]; market: string | null; row_count: number }

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']

function daysSince(iso: string|null) {
  if (!iso) return null
  const d = new Date(iso + 'T00:00:00'); const now = new Date()
  return Math.floor((+now - +d) / 86400000)
}

function RowTable({ rows, accent }: { rows: Row[]; accent: string }) {
  const [edit, setEdit] = useState<Record<string, { missing: boolean; remark: string }>>({})
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  if (!rows.length) return <div style={{ padding: 18, color: 'var(--text3)', fontSize: 13 }}>No devices.</div>
  const stateFor = (r: Row) => edit[r.esn_imei || ''] ?? { missing: !!r.physically_missing, remark: r.investigation_remark || '' }
  async function save(imei: string, missing: boolean, remark: string) {
    if (!imei) return
    setEdit(e => ({ ...e, [imei]: { missing, remark } }))
    setSaving(s => ({ ...s, [imei]: true }))
    try { await api('/api/v1/asset/investigation', { method: 'POST', body: JSON.stringify({ esn_imei: imei, physically_missing: missing, remark }) }) } catch { /* best-effort */ }
    setSaving(s => ({ ...s, [imei]: false }))
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 940 }}>
        <thead>
          <tr style={{ background: 'var(--surface2)' }}>
            {['Store','Market','Device','IMEI/ESN','Acquired','Days','Due Date','Owed','Selling','Distributor Invoice #','Invoice Date','Missing?','Investigation remark'].map(h => (
              <th key={h} style={{ textAlign:'left', padding:'8px 12px', fontSize:11, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', whiteSpace:'nowrap' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const st = stateFor(r); const imei = r.esn_imei || ''
            return (
            <tr key={r.id} style={{ borderTop:'1px solid var(--border)', background: st.missing ? '#fff1f2' : (i%2===0?'transparent':'var(--surface2)') }}>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.store || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, color:'var(--text2)' }}>{r.market || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.device_model || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.esn_imei || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.acquired_date || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, fontWeight:700, color: accent }}>{r.days_aged ?? '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.due_date || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, fontWeight:600 }}>{fmt(r.owed_to_vip || 0)}</td>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.selling_price==null ? '—' : fmt(r.selling_price)}</td>
              <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.vip_invoice_number || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '—'}</td>
              <td style={{ padding:'8px 12px', textAlign:'center' }}>
                <input type="checkbox" checked={st.missing} disabled={!imei || saving[imei]} title="Shows in aging but not physically in the store"
                  onChange={e => save(imei, e.target.checked, st.remark)} />
              </td>
              <td style={{ padding:'6px 12px' }}>
                <input value={st.remark} disabled={!imei} placeholder="investigation note…"
                  onChange={e => setEdit(x => ({ ...x, [imei]: { missing: st.missing, remark: e.target.value } }))}
                  onBlur={e => save(imei, st.missing, e.target.value)}
                  style={{ width: 200, padding:'4px 8px', fontSize:12, borderRadius:6, border:'1px solid var(--border)', background:'var(--surface)' }} />
              </td>
            </tr>
          )})}
        </tbody>
      </table>
    </div>
  )
}

function ByModelTable({ rows, meta }: { rows: ByModel[]; meta: { total_models: number; shown: number; omitted: number } }) {
  if (!rows.length) return <div style={{ padding: 18, color: 'var(--text3)', fontSize: 13 }}>No devices for this filter.</div>
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
        <thead>
          <tr style={{ background: 'var(--surface2)' }}>
            {['Device Model','Under 45','45–60 WARN','Over 60 MISSED','Unknown Age','$0 Owed','Total Phones','Owed'].map((h,i) => (
              <th key={h} style={{ textAlign: i===0?'left':'right', padding:'8px 12px', fontSize:11, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', whiteSpace:'nowrap' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((m, i) => (
            <tr key={m.device_model} style={{ borderTop:'1px solid var(--border)', background: i%2===0?'transparent':'var(--surface2)' }}>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{m.device_model}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right' }}>{m.under45}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', color:'#d97706', fontWeight:600 }}>{m.warn}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', color:'#dc2626', fontWeight:600 }}>{m.missed}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', color:'#6b7280' }}>{m.unknown_age}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', color:'var(--text3)' }}>{m.zero}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', fontWeight:700 }}>{m.total}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right' }}>{fmt(m.owed)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {meta.omitted > 0 && (
        <div style={{ padding:'10px 12px', fontSize:12, color:'var(--text3)' }}>
          Showing top {meta.shown} of {meta.total_models} models by phone count — {meta.omitted} more model(s) not shown (raise this if you need the full list).
        </div>
      )}
    </div>
  )
}

export default function AgingPage() {
  const [market, setMarket] = useState('')
  const [selStoreKeys, setSelStoreKeys] = useState<string[]>([])
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])
  const [storeGroups, setStoreGroups] = useState<StoreGroup[]>([])
  const [noMarketCount, setNoMarketCount] = useState(0)
  const [data, setData] = useState<Aging | null>(null)
  const [loading, setLoading] = useState(true)
  const [showZero, setShowZero] = useState(false)
  const [showUnknownAge, setShowUnknownAge] = useState(false)
  const [showByModel, setShowByModel] = useState(true)
  const [month, setMonth] = useState(0)  // 0 = all time (legacy quick-pick, kept alongside date range)
  const [year, setYear] = useState(new Date().getFullYear())
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  // Has GET /asset/filter-options resolved yet? Gates the "no ledger data" empty state so it can't
  // flash for a tenant that DOES have data (see NoLedgerData.tsx header comment — luxelink-parity).
  const [filterOptionsLoaded, setFilterOptionsLoaded] = useState(false)

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d:any) => { setMarkets(d.markets||[]); setStores(d.stores||[]); setNoMarketCount(d.no_market_count||0); setStoreGroups(d.store_groups||[]) })
      .catch(console.error)
      .finally(() => setFilterOptionsLoaded(true))
  }, [])
  useEffect(() => { load() }, [market, selStoreKeys, month, year, dateFrom, dateTo])

  // Display-level variant merging (2026-07-28): fall back to 1:1 groups from the plain `stores`
  // list if `store_groups` hasn't loaded yet (or an older backend), so the picker still works —
  // just without the spelling-variant merge.
  const groupSource: StoreGroup[] = storeGroups.length ? storeGroups
    : stores.map(s => ({ key: s.store, display: s.store, variants: [s.store], also_seen_as: [], market: s.market, row_count: 0 }))
  const keyToVariants = new Map(groupSource.map(g => [g.key, g.variants]))
  const keyToDisplay = new Map(groupSource.map(g => [g.key, g.display]))

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (market) qs.set('market', market)
      const storeParam = selStoreKeys.flatMap(k => keyToVariants.get(k) || [k]).join(',')
      if (storeParam) qs.set('store', storeParam)
      if (month) { qs.set('month', String(month)); qs.set('year', String(year)) }
      if (dateFrom) qs.set('date_from', dateFrom)
      if (dateTo) qs.set('date_to', dateTo)
      setData(await api(`/api/v1/asset/aging?${qs.toString()}`))
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  // When market changes, drop any selected store group no longer in the visible set (mirrors owed-weekly).
  function onMarketChange(v: string) {
    setMarket(v)
    const allowed = new Set(
      (v === NO_MARKET_VALUE ? groupSource.filter(g => !g.market)
        : v ? groupSource.filter(g => g.market === v) : groupSource).map(g => g.key)
    )
    setSelStoreKeys(prev => prev.filter(k => allowed.has(k)))
  }
  const visibleGroups = market === NO_MARKET_VALUE ? groupSource.filter(g => !g.market)
    : market ? groupSource.filter(g => g.market === market) : groupSource
  const selStyle = { padding:'6px 10px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, background:'var(--surface)' }
  const staleDays = data?.data_as_of ? daysSince(data.data_as_of) : null
  const isStale = staleDays !== null && staleDays > 3

  function buildPayload(): ExportPayload {
    const cols: ExportColumn[] = [
      { header:'Store', get:r=>r.store },
      { header:'Market', get:r=>r.market },
      { header:'Device', get:r=>r.device_model },
      { header:'IMEI/ESN', get:r=>r.esn_imei },
      { header:'Acquired', get:r=> r.acquired_date ? String(r.acquired_date).slice(0,10) : '' },
      { header:'Days Aged', get:r=>r.days_aged, align:'right' },
      { header:'Due Date', get:r=> r.due_date ? String(r.due_date).slice(0,10) : '' },
      { header:'Owed', get:r=>r.owed_to_vip, money:true },
      { header:'Selling Price', get:r=>r.selling_price, money:true },
      { header:'Distributor Invoice #', get:r=>r.vip_invoice_number },
      { header:'Distributor Invoice Date', get:r=> r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '' },
    ]
    const selStoreLabels = selStoreKeys.map(k => keyToDisplay.get(k) || k)
    const filterParts = [market === NO_MARKET_VALUE ? '(no market)' : market || null,
                         selStoreLabels.length ? selStoreLabels.join(', ') : null,
                         (dateFrom || dateTo) ? `${dateFrom||'…'} to ${dateTo||'…'}` : null]
      .filter(Boolean)
    const filterLabel = filterParts.join(' · ') || 'All markets'
    const summaryRows = data ? [{
      metric: 'Total Amount (Owed to Distributor)', value: fmt(data.totals.total_amount),
    }, {
      metric: 'Total Phones Outstanding', value: data.totals.total_phones_outstanding,
    }, {
      metric: '45–60 Day WARN', value: `${data.buckets.warn.count} devices / ${fmt(data.buckets.warn.owed)}`,
    }, {
      metric: 'Over 60 Day MISSED', value: `${data.buckets.missed.count} devices / ${fmt(data.buckets.missed.owed)}`,
    }, {
      metric: 'Unknown Age (owed>0, no acquired date)', value: `${data.unknown_age.count} devices / ${fmt(data.unknown_age.owed)}`,
    }] : []
    return {
      title: 'Inventory Aging — Sell Before 60 Days',
      subtitle: `${filterLabel}${data?.data_as_of ? ` · data as of ${data.data_as_of}, aged to ${data.today}` : ''}`,
      filename: `inventory-aging${selStoreLabels.length===1?'-'+selStoreLabels[0].replace(/[^a-z0-9]+/gi,'-').toLowerCase():''}`,
      sheets: [
        { name:'Summary', rows: summaryRows, columns: [
          { header:'Metric', get:(r:any)=>r.metric }, { header:'Value', get:(r:any)=>r.value },
        ]},
        { name:'By Model', rows: data?.by_model||[], columns: [
          { header:'Device Model', get:(r:any)=>r.device_model },
          { header:'Under 45', get:(r:any)=>r.under45, align:'right' },
          { header:'45-60 WARN', get:(r:any)=>r.warn, align:'right' },
          { header:'Over 60 MISSED', get:(r:any)=>r.missed, align:'right' },
          { header:'Unknown Age', get:(r:any)=>r.unknown_age, align:'right' },
          { header:'$0 Owed', get:(r:any)=>r.zero, align:'right' },
          { header:'Total Phones', get:(r:any)=>r.total, align:'right' },
          { header:'Owed', get:(r:any)=>r.owed, money:true },
        ]},
        { name:'45-60 Day Warning', rows:data?.buckets.warn.rows||[], columns:cols },
        { name:'Over 60 (Missed)', rows:data?.buckets.missed.rows||[], columns:cols },
        { name:'Under 45 Days', rows:data?.buckets.under45.rows||[], columns:cols },
      ],
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 20, display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:12, flexWrap:'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize:13, color:'var(--text3)', textDecoration:'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize:22, fontWeight:700, margin:'6px 0 0' }}>Inventory Aging — Sell Before 60 Days</h1>
          <p style={{ color:'var(--text2)', fontSize:14, margin:'4px 0 0' }}>
            Unsold NET60 inventory. Devices in the 45–60 day window must sell before day 60 or the Distributor bills them unsold.
          </p>
        </div>
        {data && <ExportButtons payload={buildPayload} />}
        {data && <SendReportButton reportKey="inventory_aging" filters={{ ...(selStoreKeys.length?{store:selStoreKeys.flatMap(k => keyToVariants.get(k) || [k]).join(',')}:{}), ...(market?{market}:{}), ...(month?{month, year}:{}), ...(dateFrom?{date_from:dateFrom}:{}), ...(dateTo?{date_to:dateTo}:{}) }} />}
      </div>

      {/* Stale data banner */}
      {isStale && (
        <div style={{ background:'#fef2f2', border:'1px solid #fecaca', borderRadius:10, padding:'12px 16px', marginBottom:16, fontSize:13, color:'#991b1b' }}>
          ⚠️ <strong>Data is {staleDays} days old</strong> (as of {data?.data_as_of}). Aging is measured from today, so the 45–60 warning window may be empty until you upload a current Asset_Lending.xlsx.
        </div>
      )}

      {/* Filters — standardized bar (RULE FIVE): market · store (multi) · acquired-date range */}
      <div className="card" style={{ padding:14, marginBottom:20, display:'flex', gap:12, alignItems:'center', flexWrap:'wrap' }}>
        <span style={{ fontSize:13, fontWeight:600, color:'var(--text2)' }}>Filters:</span>
        <select style={selStyle} value={market} onChange={e => onMarketChange(e.target.value)}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
          {noMarketCount > 0 && <option value={NO_MARKET_VALUE}>(no market) — {noMarketCount}</option>}
        </select>
        <MultiSelect allLabel="All stores" value={selStoreKeys} onChange={setSelStoreKeys}
          options={visibleGroups.map(g => ({ value: g.key, label: g.display + (g.also_seen_as.length ? ` (+${g.also_seen_as.length} variant${g.also_seen_as.length>1?'s':''})` : '') }))}
          width={220} searchable />
        <label style={{ fontSize:12, color:'var(--text3)' }}>Acquired</label>
        <select style={selStyle} value={month} onChange={e => setMonth(+e.target.value)} title="Quick month pick (acquired_date)">
          <option value={0}>All time</option>
          {MONTHS.map((m,i) => <option key={m} value={i+1}>{m}</option>)}
        </select>
        {month > 0 && (
          <select style={selStyle} value={year} onChange={e => setYear(+e.target.value)}>
            {[2024,2025,2026].map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        )}
        <span style={{ fontSize:12, color:'var(--text3)' }}>or range</span>
        <input type="date" style={selStyle} value={dateFrom} onChange={e => setDateFrom(e.target.value)} title="Acquired from" />
        <span style={{ fontSize:12, color:'var(--text3)' }}>to</span>
        <input type="date" style={selStyle} value={dateTo} onChange={e => setDateTo(e.target.value)} title="Acquired to" />
        {(market || selStoreKeys.length || month || dateFrom || dateTo) && (
          <button className="btn btn-secondary" style={{ fontSize:12, padding:'4px 10px' }}
            onClick={() => { setMarket(''); setSelStoreKeys([]); setMonth(0); setDateFrom(''); setDateTo('') }}>✕ Clear</button>
        )}
        {data?.data_as_of && <span style={{ marginLeft:'auto', fontSize:12, color:'var(--text3)' }}>Data as of {data.data_as_of} · aged to {data.today}</span>}
      </div>
      {data?.bucket_basis && (
        <div style={{ fontSize:12, color:'var(--text3)', marginTop:-14, marginBottom:16 }}>ℹ️ {data.bucket_basis}</div>
      )}

      {loading ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>Loading…</div>
      ) : filterOptionsLoaded && stores.length === 0 ? (
        <NoLedgerData title="Inventory Aging" />
      ) : !data ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>No data.</div>
      ) : (
        <>
          {/* WARN alert */}
          {data.buckets.warn.count > 0 && (
            <div style={{ background:'#fffbeb', border:'2px solid #f59e0b', borderRadius:10, padding:'14px 18px', marginBottom:20 }}>
              <div style={{ fontSize:15, fontWeight:700, color:'#92400e' }}>
                🚨 {data.buckets.warn.count} device{data.buckets.warn.count>1?'s':''} in the 45–60 day window — sell before day 60
              </div>
              <div style={{ fontSize:13, color:'#92400e', marginTop:4 }}>
                {fmt(data.buckets.warn.owed)} at risk of being billed unsold. These have days left; act now.
              </div>
            </div>
          )}

          {/* Footer-style totals, shown at the top for visibility too — recompute per active filter */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:16, marginBottom:20 }}>
            <div className="card" style={{ padding:'16px 20px' }}>
              <div style={{ fontSize:12, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', letterSpacing:'0.05em' }}>Total Amount ({data.totals.total_amount_column === 'owed_to_vip' ? 'Owed to Distributor' : data.totals.total_amount_column})</div>
              <div style={{ fontSize:24, fontWeight:700, marginTop:6 }}>{fmt(data.totals.total_amount)}</div>
              <div style={{ fontSize:12, color:'var(--text3)', marginTop:2 }}>across every filtered unsold on-inventory device</div>
            </div>
            <div className="card" style={{ padding:'16px 20px' }}>
              <div style={{ fontSize:12, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', letterSpacing:'0.05em' }}>Total Phones Outstanding</div>
              <div style={{ fontSize:24, fontWeight:700, marginTop:6 }}>{data.totals.total_phones_outstanding.toLocaleString()}</div>
              <div style={{ fontSize:12, color:'var(--text3)', marginTop:2 }}>unsold devices matching this filter (incl. $0 owed)</div>
            </div>
          </div>

          {/* Bucket cards */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:16, marginBottom:24 }}>
            {[
              { key:'under45', label:'Under 45 Days', sub:'safe — runway remains', color:'#059669', b:data.buckets.under45 },
              { key:'warn',    label:'45–60 Days (WARN)', sub:'sell before day 60', color:'#d97706', b:data.buckets.warn },
              { key:'missed',  label:'Over 60 Days (MISSED)', sub:'past due — billed unsold', color:'#dc2626', b:data.buckets.missed },
            ].map(c => (
              <div key={c.key} className="card" style={{ padding:'18px 22px', borderTop:`3px solid ${c.color}` }}>
                <div style={{ fontSize:12, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', letterSpacing:'0.05em' }}>{c.label}</div>
                <div style={{ fontSize:24, fontWeight:700, color:c.color, marginTop:6 }}>{fmt(c.b.owed)}</div>
                <div style={{ fontSize:12, color:'var(--text3)', marginTop:2 }}>{c.b.count.toLocaleString()} devices · {c.sub}</div>
              </div>
            ))}
          </div>

          {/* Per-model breakdown */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div onClick={() => setShowByModel(!showByModel)}
                 style={{ padding:'14px 18px', fontWeight:600, fontSize:14, cursor:'pointer', display:'flex', justifyContent:'space-between', borderBottom: showByModel ? '1px solid var(--border)' : 'none' }}>
              <span>{showByModel ? '▾' : '▸'} Phones per Device Model ({data.by_model_meta.total_models})</span>
              <span style={{ fontWeight:400, color:'var(--text3)', fontSize:12 }}>honors active filters</span>
            </div>
            {showByModel && <ByModelTable rows={data.by_model} meta={data.by_model_meta} />}
          </div>

          {/* WARN list */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'#92400e' }}>
              🟠 45–60 Day Warning ({data.buckets.warn.count})
            </div>
            <RowTable rows={data.buckets.warn.rows} accent="#d97706" />
          </div>

          {/* MISSED list */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'#991b1b' }}>
              🔴 Over 60 Days — Billed Unsold ({data.buckets.missed.count})
            </div>
            <RowTable rows={data.buckets.missed.rows} accent="#dc2626" />
          </div>

          {/* UNDER 45 list */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'#065f46' }}>
              🟢 Under 45 Days ({data.buckets.under45.count})
            </div>
            <RowTable rows={data.buckets.under45.rows} accent="#059669" />
          </div>

          {/* $0 collapsed */}
          <div className="card" style={{ padding:0 }}>
            <div onClick={() => setShowZero(!showZero)}
                 style={{ padding:'14px 18px', fontWeight:600, fontSize:14, cursor:'pointer', display:'flex', justifyContent:'space-between' }}>
              <span>{showZero ? '▾' : '▸'} Plain On Inventory · $0 owed ({data.zero_inventory.count})</span>
              <span style={{ fontWeight:400, color:'var(--text3)', fontSize:12 }}>no billing risk</span>
            </div>
            {showZero && (
              <div style={{ borderTop:'1px solid var(--border)' }}>
                <RowTable rows={data.zero_inventory.rows} accent="var(--text2)" />
              </div>
            )}
          </div>

          {/* Unknown age (Gate-1 NIT-1): owed>0 but no usable acquired_date — real outstanding
              inventory that used to be silently dropped from every total here; now explicit. */}
          {data.unknown_age.count > 0 && (
            <div className="card" style={{ padding:0, marginTop:20 }}>
              <div onClick={() => setShowUnknownAge(!showUnknownAge)}
                   style={{ padding:'14px 18px', fontWeight:600, fontSize:14, cursor:'pointer', display:'flex', justifyContent:'space-between' }}>
                <span>{showUnknownAge ? '▾' : '▸'} Unknown Age — no acquired date ({data.unknown_age.count})</span>
                <span style={{ fontWeight:400, color:'var(--text3)', fontSize:12 }}>{fmt(data.unknown_age.owed)} — counted in totals, not bucketed</span>
              </div>
              {showUnknownAge && (
                <div style={{ borderTop:'1px solid var(--border)' }}>
                  <RowTable rows={data.unknown_age.rows} accent="var(--text2)" />
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
