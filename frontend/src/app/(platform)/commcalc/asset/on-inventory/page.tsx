'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

// "(no market)" bucket (2026-07-27 market-filter-dropdown fix) — must match router.py's
// NO_MARKET_SENTINEL exactly.
const NO_MARKET_VALUE = '__no_market__'

type StoreRow = {
  store: string; market: string | null; also_seen_as?: string[]
  count: number; owed: number
  under45_count: number; under45_owed: number
  warn_count: number; warn_owed: number
  missed_count: number; missed_owed: number
  zero_count: number
  unknown_age_count: number; unknown_age_owed: number
}
type Totals = {
  store_count: number; device_count: number; owed: number
  missed_owed: number; warn_owed: number; zero_count: number
  unknown_age_count: number; unknown_age_owed: number
  total_amount: number; total_amount_column: string; total_phones_outstanding: number
}
type Data = { today: string; data_as_of: string | null; stores: StoreRow[]; totals: Totals; bucket_basis?: string }
// Display-level variant merging (2026-07-28 owner-driven addition) — see aging/page.tsx header
// comment for the full rationale; same shape here.
type StoreGroup = { key: string; display: string; variants: string[]; also_seen_as: string[]; market: string | null; row_count: number }

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']

function daysSince(iso: string | null) {
  if (!iso) return null
  const d = new Date(iso + 'T00:00:00'); const now = new Date()
  return Math.floor((+now - +d) / 86400000)
}

type SortKey = keyof StoreRow

export default function OnInventoryByStorePage() {
  const [market, setMarket] = useState('')
  const [selStoreKeys, setSelStoreKeys] = useState<string[]>([])
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{ store: string; market: string }[]>([])
  const [storeGroups, setStoreGroups] = useState<StoreGroup[]>([])
  const [noMarketCount, setNoMarketCount] = useState(0)
  const [data, setData] = useState<Data | null>(null)
  const [loading, setLoading] = useState(true)
  const [month, setMonth] = useState(0)  // 0 = all time (legacy quick-pick, kept alongside date range)
  const [year, setYear] = useState(new Date().getFullYear())
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('owed')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  useEffect(() => {
    apiCached(`/api/v1/asset/filter-options?org_id=${ORG_ID}`, LOOKUP)
      .then((d: any) => { setMarkets(d.markets || []); setStores(d.stores || []); setNoMarketCount(d.no_market_count || 0); setStoreGroups(d.store_groups || []) })
      .catch(console.error)
  }, [])
  useEffect(() => { load() }, [market, selStoreKeys, month, year, dateFrom, dateTo])

  // Display-level variant merging (2026-07-28): fall back to 1:1 groups from the plain `stores`
  // list if `store_groups` hasn't loaded yet (or an older backend).
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
      setData(await api(`/api/v1/asset/on-inventory-by-store?${qs.toString()}`))
    } catch (e) { console.error(e) }
    setLoading(false)
  }

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
  const selStyle = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const staleDays = data?.data_as_of ? daysSince(data.data_as_of) : null
  const isStale = staleDays !== null && staleDays > 3

  function sortClick(k: SortKey) {
    if (k === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(k); setSortDir(k === 'store' || k === 'market' ? 'asc' : 'desc') }
  }

  const sorted = [...(data?.stores || [])].sort((a, b) => {
    const av = a[sortKey] ?? '', bv = b[sortKey] ?? ''
    const cmp = typeof av === 'number' && typeof bv === 'number'
      ? av - bv : String(av).localeCompare(String(bv))
    return sortDir === 'asc' ? cmp : -cmp
  })

  function buildPayload(): ExportPayload {
    const cols: ExportColumn[] = [
      { header: 'Store', get: r => r.store },
      { header: 'Also Seen As', get: r => (r.also_seen_as || []).join('; ') },
      { header: 'Market', get: r => r.market },
      { header: 'On-Inventory Devices', get: r => r.count, align: 'right' },
      { header: 'Total Owed', get: r => r.owed, money: true },
      { header: '<45 Devices', get: r => r.under45_count, align: 'right' },
      { header: '<45 Owed', get: r => r.under45_owed, money: true },
      { header: '45-60 WARN Devices', get: r => r.warn_count, align: 'right' },
      { header: '45-60 WARN Owed', get: r => r.warn_owed, money: true },
      { header: '>60 MISSED Devices', get: r => r.missed_count, align: 'right' },
      { header: '>60 MISSED Owed', get: r => r.missed_owed, money: true },
      { header: 'Unknown Age Devices', get: r => r.unknown_age_count, align: 'right' },
      { header: 'Unknown Age Owed', get: r => r.unknown_age_owed, money: true },
      { header: '$0 Owed Devices', get: r => r.zero_count, align: 'right' },
    ]
    const selStoreLabels = selStoreKeys.map(k => keyToDisplay.get(k) || k)
    const filterParts = [market === NO_MARKET_VALUE ? '(no market)' : market || null,
                         selStoreLabels.length ? selStoreLabels.join(', ') : null]
      .filter(Boolean)
    const filterLabel = filterParts.join(' · ') || 'All markets'
    const periodLabel = month ? `${MONTHS[month - 1]} ${year}` : (dateFrom || dateTo) ? `${dateFrom||'…'} to ${dateTo||'…'}` : 'All time'
    const summaryRows = data ? [
      { metric: `Total Amount (${data.totals.total_amount_column})`, value: fmt(data.totals.total_amount) },
      { metric: 'Total Phones Outstanding', value: data.totals.total_phones_outstanding },
      { metric: 'Unknown Age (owed>0, no acquired date)', value: `${data.totals.unknown_age_count} devices / ${fmt(data.totals.unknown_age_owed)}` },
    ] : []
    return {
      title: 'On-Inventory by Store',
      subtitle: `${filterLabel} · ${periodLabel}${data?.data_as_of ? ` · data as of ${data.data_as_of}, aged to ${data.today}` : ''}`,
      filename: `on-inventory-by-store${selStoreLabels.length===1 ? '-' + selStoreLabels[0].replace(/[^a-z0-9]+/gi, '-').toLowerCase() : ''}`,
      sheets: [
        { name: 'Summary', rows: summaryRows, columns: [
          { header: 'Metric', get: (r: any) => r.metric }, { header: 'Value', get: (r: any) => r.value },
        ]},
        { name: 'On-Inventory by Store', rows: sorted, columns: cols },
      ],
    }
  }

  const th = (label: string, k: SortKey, right = false) => (
    <th
      onClick={() => sortClick(k)}
      style={{ textAlign: right ? 'right' : 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap', cursor: 'pointer', userSelect: 'none' }}
    >
      {label}{sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  )

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>On-Inventory by Store</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Unsold On-Inventory devices and $ owed to the Distributor, rolled up per store (spelling variants of the same address are merged — see "also seen as"). Aging buckets match Inventory Aging (&lt;45 / 45–60 WARN / &gt;60 MISSED, from acquired date).
          </p>
        </div>
        {data && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
      </div>

      {/* Stale data banner */}
      {isStale && (
        <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 10, padding: '12px 16px', marginBottom: 16, fontSize: 13, color: '#991b1b' }}>
          ⚠️ <strong>Data is {staleDays} days old</strong> (as of {data?.data_as_of}). Aging is measured from today, so the buckets may shift until you upload a current Asset_Lending.xlsx.
        </div>
      )}

      {/* Filters — standardized bar (RULE FIVE): market · store (multi) · acquired-date range */}
      <div className="card" style={{ padding: 14, marginBottom: 20, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>Filters:</span>
        <select style={selStyle} value={market} onChange={e => onMarketChange(e.target.value)}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
          {noMarketCount > 0 && <option value={NO_MARKET_VALUE}>(no market) — {noMarketCount}</option>}
        </select>
        <MultiSelect allLabel="All stores" value={selStoreKeys} onChange={setSelStoreKeys}
          options={visibleGroups.map(g => ({ value: g.key, label: g.display + (g.also_seen_as.length ? ` (+${g.also_seen_as.length} variant${g.also_seen_as.length>1?'s':''})` : '') }))}
          width={220} searchable />
        <label style={{ fontSize: 12, color: 'var(--text3)' }}>Acquired</label>
        <select style={selStyle} value={month} onChange={e => setMonth(+e.target.value)} title="Quick month pick (acquired_date)">
          <option value={0}>All time</option>
          {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
        </select>
        {month > 0 && (
          <select style={selStyle} value={year} onChange={e => setYear(+e.target.value)}>
            {[2024, 2025, 2026].map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        )}
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>or range</span>
        <input type="date" style={selStyle} value={dateFrom} onChange={e => setDateFrom(e.target.value)} title="Acquired from" />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>to</span>
        <input type="date" style={selStyle} value={dateTo} onChange={e => setDateTo(e.target.value)} title="Acquired to" />
        {(market || selStoreKeys.length || month || dateFrom || dateTo) && (
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
            onClick={() => { setMarket(''); setSelStoreKeys([]); setMonth(0); setDateFrom(''); setDateTo('') }}>✕ Clear</button>
        )}
        {data?.data_as_of && <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>Data as of {data.data_as_of} · aged to {data.today}</span>}
      </div>
      {data?.bucket_basis && (
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: -14, marginBottom: 16 }}>ℹ️ {data.bucket_basis}</div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !data || !data.stores.length ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>No on-inventory devices for this filter.</div>
      ) : (
        <>
          {/* Summary cards — total_amount/total_phones_outstanding are the same "footer totals"
              shown on Inventory Aging, same money-column choice (owed_to_vip), labeled explicitly. */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 24 }}>
            {[
              { label: 'Total Phones Outstanding', val: data.totals.total_phones_outstanding.toLocaleString(), sub: `${data.totals.store_count} stores`, color: '#2563eb' },
              { label: `Total Amount (${data.totals.total_amount_column})`, val: fmt(data.totals.total_amount), sub: 'unsold exposure', color: '#d97706' },
              { label: '45–60 WARN Owed', val: fmt(data.totals.warn_owed), sub: 'sell before day 60', color: '#d97706' },
              { label: '>60 MISSED Owed', val: fmt(data.totals.missed_owed), sub: 'past due — billed unsold', color: '#dc2626' },
            ].map(c => (
              <div key={c.label} className="card" style={{ padding: '18px 22px', borderTop: `3px solid ${c.color}` }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c.label}</div>
                <div style={{ fontSize: 24, fontWeight: 700, color: c.color, marginTop: 6 }}>{c.val}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{c.sub}</div>
              </div>
            ))}
          </div>
          {data.totals.unknown_age_count > 0 && (
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: -12, marginBottom: 16 }}>
              ℹ️ {data.totals.unknown_age_count} device(s) / {fmt(data.totals.unknown_age_owed)} have no usable acquired date — counted in the totals above, see each store's own count below.
            </div>
          )}

          {/* Per-store table */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {th('Store', 'store')}
                    {th('Market', 'market')}
                    {th('Devices', 'count', true)}
                    {th('Total Owed', 'owed', true)}
                    {th('<45', 'under45_count', true)}
                    {th('45–60 WARN', 'warn_count', true)}
                    {th('>60 MISSED', 'missed_count', true)}
                    {th('Unknown Age', 'unknown_age_count', true)}
                    {th('$0 Owed', 'zero_count', true)}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r, i) => (
                    <tr key={r.store} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>
                        {r.store || '—'}
                        {!!r.also_seen_as?.length && (
                          <div style={{ fontSize: 11, fontWeight: 400, color: 'var(--text3)', marginTop: 2 }}>
                            also seen as: {r.also_seen_as.join(', ')}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text2)' }}>{r.market || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{r.count.toLocaleString()}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', fontWeight: 700 }}>{fmt(r.owed)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: '#059669' }}>{r.under45_count} <span style={{ color: 'var(--text3)' }}>· {fmt(r.under45_owed)}</span></td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: '#d97706', fontWeight: r.warn_count ? 600 : 400 }}>{r.warn_count} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>· {fmt(r.warn_owed)}</span></td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: '#dc2626', fontWeight: r.missed_count ? 600 : 400 }}>{r.missed_count} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>· {fmt(r.missed_owed)}</span></td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: '#6b7280' }}>{r.unknown_age_count} <span style={{ color: 'var(--text3)' }}>· {fmt(r.unknown_age_owed)}</span></td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: 'var(--text3)' }}>{r.zero_count}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700, background: 'var(--surface2)' }}>
                    <td style={{ padding: '10px 12px', fontSize: 12 }}>Total ({data.totals.store_count} stores)</td>
                    <td />
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right' }}>{data.totals.total_phones_outstanding.toLocaleString()}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(data.totals.total_amount)}</td>
                    <td />
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right', color: '#d97706' }}>{fmt(data.totals.warn_owed)}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right', color: '#dc2626' }}>{fmt(data.totals.missed_owed)}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right', color: '#6b7280' }}>{fmt(data.totals.unknown_age_owed)}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, textAlign: 'right', color: 'var(--text3)' }}>{data.totals.zero_count}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
