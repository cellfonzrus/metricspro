'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
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
  // Stamped server-side from the ONE verdict home (device_aging_policy) so the flag means the same
  // thing here as on every other screen. `null` = NOT MEASURED, never "fine".
  out_of_pocket?: boolean|null; out_of_pocket_basis?: string; days_overdue?: number|null
  sell_within_days?: number; window_declared?: boolean
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
  // Every unsold device, aged. `buckets` above is the owed>0 subset of this.
  age_buckets?: Record<string, { count: number; owed: number }>
  age_buckets_basis?: string
  // `recovered_amount` and the two flags arrive from `payables/recovered_amount.py` (§COMMCALC-33):
  // what the carrier has ALREADY deducted or reimbursed, how much of the figure has never been
  // netted against a rebate, and the sentence to say so. A screen that prints the total without
  // them tells the owner he funded devices the carrier already paid for.
  out_of_pocket?: { devices: number; out_of_pocket: number; out_of_pocket_amount: number; not_measured: number
    recovered_amount?: number; netted_devices?: number; recovery_unmeasured?: number
    recovery_unmeasured_amount?: number; recovery_note?: string|null; amount_not_netted?: boolean }
  // The carrier's own declared selling window — what the deadline on screen must be derived from,
  // instead of one carrier's 60 days printed over everybody's devices (owner 2026-10-22).
  aging_policy?: {
    house: { sell_within_days: number; warn_after_days: number; declared: boolean }
    by_carrier: { carrier_id: string; label: string|null; unsold_source: string
                  sell_within_days: number; warn_after_days: number; declared: boolean }[]
  }
  sources?: { asset_ledger: number; payable_ledger: number }
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
            {['Store','Market','Device','IMEI/ESN','Acquired','Days','Due Date','Out of pocket','Owed','Selling','Distributor Invoice #','Invoice Date','Missing?','Investigation remark'].map(h => (
              <th key={h} style={{ textAlign:'left', padding:'8px 12px', fontSize:11, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', whiteSpace:'nowrap' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const st = stateFor(r); const imei = r.esn_imei || ''
            return (
            <tr key={r.id} style={{ borderTop:'1px solid var(--border)', background: st.missing ? 'var(--c-bg-fff1f2)' : (i%2===0?'transparent':'var(--surface2)') }}>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.store || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, color:'var(--text2)' }}>{r.market || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.device_model || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.esn_imei || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.acquired_date || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, fontWeight:700, color: accent }}>{r.days_aged ?? '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.due_date || '—'}</td>
              {/* THE UNIVERSAL FLAG, as the server computed it. Three states and three readings:
                  true = you are funding this device yourself; false = not yet; null = NOT MEASURED,
                  which is shown as such because a device with no date is not a device that is fine. */}
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>
                {r.out_of_pocket == null
                  ? <span style={{ color:'var(--text3)' }} title="No due date and no purchase date — this cannot be judged">not measured</span>
                  : r.out_of_pocket
                    ? <span style={{ color:'var(--c-fg-991b1b)', fontWeight:700 }}
                        title={r.out_of_pocket_basis === 'due_date'
                          ? `Payment was due and the device is unsold${r.days_overdue ? ` — ${r.days_overdue} day(s) overdue` : ''}`
                          : `Held past the carrier's ${r.sell_within_days ?? 60}-day selling window and still unsold`}>
                        💸 yes{r.days_overdue ? ` (${r.days_overdue}d)` : ''}
                      </span>
                    : <span style={{ color:'var(--text3)' }}>no</span>}
              </td>
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
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', color:'var(--c-fg-d97706)', fontWeight:600 }}>{m.warn}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', color:'var(--c-fg-dc2626)', fontWeight:600 }}>{m.missed}</td>
              <td style={{ padding:'8px 12px', fontSize:12, textAlign:'right', color:'var(--c-fg-6b7280)' }}>{m.unknown_age}</td>
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
  // THE DEADLINE ON SCREEN IS THE CARRIER'S OWN (owner 2026-10-22: "it should not say sell before 60
  // days as that is boost requirement , total is sell before 12 days"). The server resolves each
  // carrier's declared window and returns them; this picks the one to PRINT. One carrier -> its own
  // numbers. Several, disagreeing -> the shortest, because that is the first deadline that can be
  // missed, and the per-carrier list is stated beneath. Nothing declared -> the house default, which
  // reads exactly as it always has (45-60, before day 60).
  const pol = data?.aging_policy
  const windows = [...new Set((pol?.by_carrier || []).map(c => c.sell_within_days))].sort((a, b) => a - b)
  const windowLabel = windows.length === 1
    ? { warn: pol!.by_carrier[0].warn_after_days, sell: windows[0], mixed: false }
    : windows.length > 1
      ? { warn: Math.min(...(pol!.by_carrier.map(c => c.warn_after_days))), sell: windows[0], mixed: true }
      : { warn: pol?.house.warn_after_days ?? 45, sell: pol?.house.sell_within_days ?? 60, mixed: false }

  useEffect(() => {
    apiCached(`/api/v1/asset/filter-options?org_id=${ORG_ID}`, LOOKUP)
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
      // The flag the owner asked to see "universally" travels into the export too, with its BASIS,
      // so a spreadsheet someone acts on says why a device is on the list.
      { header:'Out of pocket', get:r=> r.out_of_pocket == null ? 'not measured' : (r.out_of_pocket ? 'YES' : 'no') },
      { header:'Days overdue', get:r=> r.days_overdue ?? '', align:'right', agg:'none' },
      { header:'Basis', get:r=> r.out_of_pocket_basis || '' },
      { header:'Sell within (days)', get:r=> r.sell_within_days ?? '', align:'right', agg:'none' },
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
      // The edges come from the carrier's declared window, so a 12-day carrier's export does not
      // claim a 45-60 band it was never held to.
      metric: `${windowLabel.warn}–${windowLabel.sell} Day WARN`, value: `${data.buckets.warn.count} devices / ${fmt(data.buckets.warn.owed)}`,
    }, {
      metric: `Over ${windowLabel.sell} Day MISSED`, value: `${data.buckets.missed.count} devices / ${fmt(data.buckets.missed.owed)}`,
    }, {
      metric: 'Out of pocket (unsold + payable)',
      value: data.out_of_pocket ? `${data.out_of_pocket.out_of_pocket} devices / ${fmt(data.out_of_pocket.out_of_pocket_amount)}` : 'not measured',
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
          <p className="pg-note" style={{ color:'var(--text2)', fontSize:14, margin:'4px 0 0' }}>
            Unsold NET60 inventory. Devices in the 45–60 day window must sell before day 60 or the Distributor bills them unsold.
          </p>
        </div>
        {data && <ExportButtons payload={buildPayload} />}
        {/* WYSIWYG (§3c) — the send-path used to be the SERVER re-query (reportKey="inventory_aging"):
            notify/report_registry._inventory_aging forwards store/market/month/year but DROPS
            date_from/date_to entirely, so a date-RANGE filter (as opposed to the month/year quick-pick)
            was silently ignored and the emailed/WhatsApp'd file came back broader than the screen.
            Now renders in-browser from the SAME buildPayload() the Excel/PDF buttons already use. */}
        {data && <SendReportButton exportPayload={buildPayload} title="Inventory Aging" />}
      </div>

      {/* Stale data banner */}
      {isStale && (
        <div style={{ background:'var(--c-bg-fef2f2)', border:'1px solid var(--c-bg-fecaca)', borderRadius:10, padding:'12px 16px', marginBottom:16, fontSize:13, color:'var(--c-fg-991b1b)' }}>
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
      ) : filterOptionsLoaded && stores.length === 0 && !(data?.totals?.total_phones_outstanding) ? (
        /* THE EMPTY-STATE GATE WAS ALSO BOOST-SHAPED (owner report 2026-10-22: "under Luxelink it is
           using Boost - it should not"). `stores` comes from /asset/filter-options, which is derived
           from the VIP asset-lending ledger, so a tenant on another carrier saw the "this program
           does not apply to your account" card even when the report had just returned their devices
           from the feed their own source map declares. The card now requires the REPORT to be empty
           too, which is the only honest test for "there is nothing here". */
        <NoLedgerData title="Inventory Aging" />
      ) : !data ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>No data.</div>
      ) : (
        <>
          {/* OUT OF POCKET, HIGHLIGHTED (owner 2026-10-22: "if the imei is not sold then the device
              has to be paid out of pocket whch needs to be highlighted universally"). One verdict,
              computed server-side in the one home, so this headline and the per-row flag below it
              can never disagree — and the devices no date could judge are stated separately rather
              than counted as settled. */}
          {!!data.out_of_pocket && data.out_of_pocket.out_of_pocket > 0 && (
            <div style={{ background:'var(--c-bg-fef2f2)', border:'2px solid var(--c-bg-dc2626)', borderRadius:10, padding:'14px 18px', marginBottom:20 }}>
              <div style={{ fontSize:15, fontWeight:700, color:'var(--c-fg-991b1b)' }}>
                💸 {data.out_of_pocket.out_of_pocket} unsold device{data.out_of_pocket.out_of_pocket>1?'s':''} must be paid OUT OF POCKET — {fmt(data.out_of_pocket.out_of_pocket_amount)}
              </div>
              <div style={{ fontSize:13, color:'var(--c-fg-991b1b)', marginTop:4 }}>
                These are past their payment due date (or past the carrier&apos;s selling window) and
                are still unsold.
                {/* OWNER 2026-10-26: "the payments for all phones automatically are done from
                    commission and rebates received and auto adjusted". Whether the invoice is
                    funded from his own cash is a MEASURED question, not an assumption, so the
                    sentence is chosen by what the build actually measured (§COMMCALC-33). */}
                {data.out_of_pocket.amount_not_netted
                  ? <> {data.out_of_pocket.recovery_note
                        || 'This figure has NOT been reduced by what the carrier may already have recovered.'}</>
                  : <> What the carrier already recovered is deducted, so this is what is left to fund
                      from your own cash.</>}
                {!!data.out_of_pocket.recovered_amount && <> The carrier has already recovered{' '}
                  {fmt(data.out_of_pocket.recovered_amount)} against these devices
                  {!!data.out_of_pocket.netted_devices && <>, settling {data.out_of_pocket.netted_devices} of
                  them outright</>}.</>}
                {!!data.out_of_pocket.recovery_unmeasured && <> {data.out_of_pocket.recovery_unmeasured} device
                  {data.out_of_pocket.recovery_unmeasured>1?'s':''} ({fmt(data.out_of_pocket.recovery_unmeasured_amount || 0)})
                  have no rebate source configured, so that part of the figure is unaccounted rather
                  than proven.</>}
                {data.out_of_pocket.not_measured > 0 && <> {data.out_of_pocket.not_measured} more carry no
                date to judge by and are reported as unmeasured, not as settled.</>}
              </div>
            </div>
          )}
          {/* WARN alert — the window is the CARRIER'S declared number (mig 1091), never a literal.
              With nothing declared the house default still reads "45–60 … before day 60". */}
          {data.buckets.warn.count > 0 && (
            <div style={{ background:'var(--c-bg-fffbeb)', border:'2px solid var(--c-bg-f59e0b)', borderRadius:10, padding:'14px 18px', marginBottom:20 }}>
              <div style={{ fontSize:15, fontWeight:700, color:'var(--c-fg-92400e)' }}>
                🚨 {data.buckets.warn.count} device{data.buckets.warn.count>1?'s':''} in the {windowLabel.warn}–{windowLabel.sell} day window — sell before day {windowLabel.sell}
              </div>
              <div style={{ fontSize:13, color:'var(--c-fg-92400e)', marginTop:4 }}>
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
              { key:'under45', label:'Under 45 Days', sub:'safe — runway remains', color:'var(--c-fg-059669)', b:data.buckets.under45 },
              { key:'warn',    label:'45–60 Days (WARN)', sub:'sell before day 60', color:'var(--c-fg-d97706)', b:data.buckets.warn },
              { key:'missed',  label:'Over 60 Days (MISSED)', sub:'past due — billed unsold', color:'var(--c-fg-dc2626)', b:data.buckets.missed },
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
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'var(--c-fg-92400e)' }}>
              🟠 45–60 Day Warning ({data.buckets.warn.count})
            </div>
            <RowTable rows={data.buckets.warn.rows} accent="#d97706" />
          </div>

          {/* MISSED list */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'var(--c-fg-991b1b)' }}>
              🔴 Over 60 Days — Billed Unsold ({data.buckets.missed.count})
            </div>
            <RowTable rows={data.buckets.missed.rows} accent="#dc2626" />
          </div>

          {/* UNDER 45 list */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'var(--c-fg-065f46)' }}>
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
