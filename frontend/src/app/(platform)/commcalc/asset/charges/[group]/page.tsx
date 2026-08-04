'use client'
import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { NoLedgerData } from '../../_shared/NoLedgerData'

// URL slug -> backend group key + display config
const GROUP_MAP: Record<string, { key: string; title: string; color: string; critical?: boolean; blurb: string }> = {
  'appeals':       { key:'appeals',       title:'Appeals & Denied Payments', color:'#dc2626', critical:true,
                     blurb:'The carrier denied or is withholding payment for these activations/reimbursements. Each is a potential direct loss — review and appeal.' },
  'vip-fees':      { key:'vip_fees',       title:'Distributor Fees', color:'#2563eb',
                     blurb:'Processing, shipping, and SIM kit fees billed by the Distributor.' },
  'stock-balance': { key:'stock_balance',  title:'Stock Balancing / Returns', color:'#d97706',
                     blurb:'Phones returned to the Distributor because they could not sell before 60 days (FIFO, unopened). Confirm the Distributor credited these.' },
  'recon':         { key:'recon_oddity',   title:'Reconciliation Oddities', color:'#7c3aed',
                     blurb:'Payment/data mismatches — wrong ESN paid, missing Elevate data, coupon issues, exchanges.' },
}

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']
function upcomingFriday(from = new Date()) {
  const d = new Date(from); const diff = (5 - d.getDay() + 7) % 7
  d.setDate(d.getDate() + diff); return d
}
function ymd(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
}

type Row = { id:number; store:string; market:string; esn_imei:string|null; phone_number:string|null
  device_model:string|null; category:string|null; status:string|null; owed_to_vip:number|null
  selling_price:number|null; period_date:string|null; notes:string|null
  vip_invoice_number:string|null; vip_invoice_date:string|null; denial_reason?:string|null }
type Group = { key:string; label:string; count:number; owed:number
  by_category:{category:string;count:number;owed:number}[]
  by_store:{store:string;market:string;count:number;owed:number}[] }
type ChargeRows = { rows:Row[]; total:number; total_owed:number }

export default function ChargeGroupPage() {
  const params = useParams()
  const slug = String(params.group || '')
  const cfg = GROUP_MAP[slug]

  const [market, setMarket] = useState('')
  const [store, setStore] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])
  // "(no market)" bucket (2026-07-27 fix) — must match router.py's NO_MARKET_SENTINEL exactly.
  const [noMarketCount, setNoMarketCount] = useState(0)
  const NO_MARKET_VALUE = '__no_market__'
  const [group, setGroup] = useState<Group | null>(null)
  const [lineItems, setLineItems] = useState<ChargeRows | null>(null)
  const [loading, setLoading] = useState(true)
  const [catFilter, setCatFilter] = useState('')  // click-through drill into one category

  // period filter (mirrors the dashboard)
  const [mode, setMode] = useState<'all'|'month'|'week'>('all')
  const [month, setMonth] = useState(new Date().getMonth()+1)
  const [year, setYear] = useState(new Date().getFullYear())
  const [weekFriday, setWeekFriday] = useState(ymd(upcomingFriday()))
  // Has GET /asset/filter-options resolved yet? Gates the "no ledger data" empty state so it can't
  // flash for a tenant that DOES have data (see NoLedgerData.tsx header comment — luxelink-parity).
  const [filterOptionsLoaded, setFilterOptionsLoaded] = useState(false)

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d:any) => { setMarkets(d.markets||[]); setStores(d.stores||[]); setNoMarketCount(d.no_market_count||0) })
      .catch(console.error)
      .finally(() => setFilterOptionsLoaded(true))
  }, [])
  useEffect(() => { if (cfg) load() }, [market, store, slug, mode, month, year, weekFriday])

  function periodParams(qs: URLSearchParams) {
    if (market) qs.set('market', market)
    if (store) qs.set('store', store)
    if (mode === 'month') { qs.set('month', String(month)); qs.set('year', String(year)) }
    if (mode === 'week') qs.set('week_friday', weekFriday)
    return qs
  }

  async function load() {
    setLoading(true)
    setCatFilter('')  // a new filter set clears any category drill
    try {
      const q1 = periodParams(new URLSearchParams({ org_id: ORG_ID }))
      const q2 = periodParams(new URLSearchParams({ org_id: ORG_ID, group: cfg.key, limit: '2000' }))
      const [summary, rows] = await Promise.all([
        api(`/api/v1/asset/charges-summary?${q1.toString()}`),
        api(`/api/v1/asset/charge-rows?${q2.toString()}`),
      ])
      setGroup(summary.groups[cfg.key] || null)
      setLineItems(rows)
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  if (!cfg) return <div style={{ padding:40 }}>Unknown report. <a href="/commcalc/asset/dashboard">Back to dashboard</a></div>

  const visibleStores = market === NO_MARKET_VALUE ? stores.filter(s => !s.market)
    : market ? stores.filter(s => s.market === market) : stores
  const selStyle = { padding:'6px 10px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, background:'var(--surface)' }
  const tabBtn = (active:boolean) => ({ padding:'6px 14px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, cursor:'pointer', fontWeight:600,
    background: active ? 'var(--accent)' : 'var(--surface)', color: active ? '#fff' : 'var(--text2)' })
  const periodLabel = mode==='all' ? 'All time' : mode==='month' ? `${MONTHS[month-1]} ${year}` : `Week of ${weekFriday}`
  // No asset_ledger rows for this org at all (see NoLedgerData.tsx — luxelink-parity, 2026-07-16):
  // don't show the unconditional "Critical — money at stake" banner when there is nothing to review.
  const ledgerEmpty = filterOptionsLoaded && stores.length === 0
  const filterLabel = [periodLabel, market||null, store||null, catFilter||null].filter(Boolean).join(' · ')
  const isAppeals = cfg.key === 'appeals'
  const shownRows = (lineItems?.rows || []).filter(r => !catFilter || r.category === catFilter)

  function buildPayload(): ExportPayload {
    const items = shownRows
    return {
      title: `${cfg.title} — Asset Charges`,
      subtitle: filterLabel,
      filename: `${slug}${catFilter?'-'+catFilter.replace(/[^a-z0-9]+/gi,'-').toLowerCase().slice(0,30):''}-${mode==='month'?`${year}-${String(month).padStart(2,'0')}`:mode}`,
      sheets: [
        { name: 'Line Items', rows: items, columns: [
          { header:'Store', get:r=>r.store },
          { header:'Market', get:r=>r.market },
          { header:'Category', get:r=>r.category },
          ...(isAppeals ? [{ header:'Reason', get:(r:Row)=>r.denial_reason } as ExportColumn] : []),
          { header:'Device', get:r=>r.device_model },
          { header:'IMEI/ESN', get:r=>r.esn_imei },
          { header:'Phone', get:r=>r.phone_number },
          { header:'Date', get:r=> r.period_date ? String(r.period_date).slice(0,10) : '' },
          { header:'Owed', get:r=>r.owed_to_vip, money:true },
          { header:'Selling Price', get:r=>r.selling_price, money:true },
          { header:'Distributor Invoice #', get:r=>r.vip_invoice_number },
          { header:'Distributor Invoice Date', get:r=> r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '' },
        ]},
        { name: 'By Store', rows: group?.by_store || [], columns: [
          { header:'Store', get:r=>r.store },
          { header:'Market', get:r=>r.market },
          { header:'Items', get:r=>r.count, align:'right' },
          { header:'Owed', get:r=>r.owed, money:true },
        ]},
        { name: 'By Category', rows: group?.by_category || [], columns: [
          { header:'Category', get:r=>r.category },
          { header:'Items', get:r=>r.count, align:'right' },
          { header:'Owed', get:r=>r.owed, money:true },
        ]},
      ],
    }
  }

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:20, gap:12, flexWrap:'wrap' }}>
        <div>
          <a href="/commcalc/asset/dashboard" style={{ fontSize:13, color:'var(--text3)', textDecoration:'none' }}>← Charges Dashboard</a>
          <h1 style={{ fontSize:22, fontWeight:700, margin:'6px 0 0' }}>{cfg.title}</h1>
          <p style={{ color:'var(--text2)', fontSize:14, margin:'4px 0 0' }}>{cfg.blurb}</p>
        </div>
        <div style={{ display: 'inline-flex', gap: 6 }}>
          <ExportButtons payload={buildPayload} />
          {/* WYSIWYG (§3c) — the send-path used to be the SERVER re-query (reportKey=SEND_KEY[cfg.key]):
              notify/report_registry._charges_builder forwards store/market/month/year/week_friday but
              has NO concept of the on-screen category drill-down (catFilter) and re-fetches line items
              at a DIFFERENT limit (500) than the page's own load (2000) — so a category-drilled or
              large-result send came back broader (or narrower) than the screen. Now renders in-browser
              from the SAME buildPayload() (built off `shownRows`, already catFilter-scoped) the
              Excel/PDF buttons use. */}
          {cfg && <SendReportButton exportPayload={buildPayload} title={`${cfg.title} — Asset Charges`} />}
        </div>
      </div>

      {cfg.critical && !ledgerEmpty && (
        <div style={{ background:'#fef2f2', border:'2px solid #dc2626', borderRadius:10, padding:'14px 18px', marginBottom:20, color:'#991b1b' }}>
          <strong>🚨 Critical — money at stake.</strong> These are denied/withheld carrier payments. Use “Push Appeals to Flags” on the dashboard to track them on the Flags page.
        </div>
      )}

      <div className="card" style={{ padding:14, marginBottom:20, display:'flex', gap:10, alignItems:'center', flexWrap:'wrap' }}>
        <div style={{ display:'flex', gap:6 }}>
          <button style={tabBtn(mode==='all')}   onClick={() => setMode('all')}>All time</button>
          <button style={tabBtn(mode==='month')} onClick={() => setMode('month')}>Month</button>
          <button style={tabBtn(mode==='week')}  onClick={() => setMode('week')}>Week</button>
        </div>
        {mode==='month' && (
          <>
            <select style={selStyle} value={month} onChange={e=>setMonth(+e.target.value)}>
              {MONTHS.map((m,i)=><option key={m} value={i+1}>{m}</option>)}
            </select>
            <select style={selStyle} value={year} onChange={e=>setYear(+e.target.value)}>
              {[2024,2025,2026].map(y=><option key={y} value={y}>{y}</option>)}
            </select>
          </>
        )}
        {mode==='week' && (
          <input type="date" style={selStyle} value={weekFriday} onChange={e=>setWeekFriday(e.target.value)} />
        )}
        <div style={{ width:1, height:24, background:'var(--border)' }} />
        <select style={selStyle} value={market} onChange={e => { setMarket(e.target.value); setStore('') }}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
          {noMarketCount > 0 && <option value={NO_MARKET_VALUE}>(no market) — {noMarketCount}</option>}
        </select>
        <select style={selStyle} value={store} onChange={e => setStore(e.target.value)}>
          <option value="">All stores</option>
          {visibleStores.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
        </select>
      </div>

      {loading ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>Loading…</div>
      ) : ledgerEmpty ? (
        <NoLedgerData title={cfg.title} />
      ) : !group ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>No data.</div>
      ) : (
        <>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:16, marginBottom:24 }}>
            <div className="card" style={{ padding:'18px 22px', borderTop:`3px solid ${cfg.color}` }}>
              <div style={{ fontSize:12, fontWeight:600, color:'var(--text2)', textTransform:'uppercase' }}>Total Charges</div>
              <div style={{ fontSize:26, fontWeight:700, color:cfg.color, marginTop:6 }}>{fmt(group.owed)}</div>
            </div>
            <div className="card" style={{ padding:'18px 22px' }}>
              <div style={{ fontSize:12, fontWeight:600, color:'var(--text2)', textTransform:'uppercase' }}>Item Count</div>
              <div style={{ fontSize:26, fontWeight:700, marginTop:6 }}>{group.count.toLocaleString()}</div>
            </div>
          </div>

          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16, marginBottom:24 }}>
            <div className="card" style={{ padding:0 }}>
              <div style={{ padding:'12px 16px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14 }}>
                By Category <span style={{ fontWeight:400, color:'var(--text3)', fontSize:12 }}>— click to drill into line items</span>
              </div>
              <table style={{ width:'100%', borderCollapse:'collapse' }}><tbody>
                {group.by_category.map((c,i) => {
                  const active = catFilter === c.category
                  return (
                  <tr key={c.category} onClick={() => setCatFilter(active ? '' : c.category)}
                      style={{ borderTop:'1px solid var(--border)', cursor:'pointer',
                               background: active ? cfg.color+'18' : (i%2?'var(--surface2)':'transparent') }}>
                    <td style={{ padding:'8px 16px', fontSize:13, fontWeight: active?700:400 }}>
                      <span style={{ display:'inline-block', width:14, color:'var(--text3)' }}>{active?'▾':'▸'}</span>{c.category}
                    </td>
                    <td style={{ padding:'8px 16px', fontSize:13, textAlign:'right' }}>{c.count}</td>
                    <td style={{ padding:'8px 16px', fontSize:13, fontWeight:600, textAlign:'right' }}>{fmt(c.owed)}</td>
                  </tr>
                  )
                })}
              </tbody></table>
            </div>
            <div className="card" style={{ padding:0 }}>
              <div style={{ padding:'12px 16px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14 }}>By Store</div>
              <div style={{ maxHeight:280, overflowY:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse' }}><tbody>
                  {group.by_store.map((s,i) => (
                    <tr key={s.store} style={{ borderTop:'1px solid var(--border)', background:i%2?'var(--surface2)':'transparent' }}>
                      <td style={{ padding:'8px 16px', fontSize:13 }}>{s.store}</td>
                      <td style={{ padding:'8px 16px', fontSize:12, color:'var(--text2)' }}>{s.market}</td>
                      <td style={{ padding:'8px 16px', fontSize:13, fontWeight:600, textAlign:'right' }}>{fmt(s.owed)}</td>
                    </tr>
                  ))}
                </tbody></table>
              </div>
            </div>
          </div>

          <div className="card" style={{ padding:0 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, display:'flex', alignItems:'center', gap:10, flexWrap:'wrap' }}>
              <span>Line Items <span style={{ fontWeight:400, color:'var(--text3)', fontSize:12 }}>
                ({shownRows.length.toLocaleString()}{lineItems && lineItems.total > lineItems.rows.length ? ` · ${lineItems.total.toLocaleString()} total` : ''})
              </span></span>
              {catFilter && (
                <span style={{ display:'inline-flex', alignItems:'center', gap:6, background:cfg.color+'18', color:cfg.color, borderRadius:14, padding:'3px 10px', fontSize:12, fontWeight:600 }}>
                  {catFilter}
                  <span onClick={() => setCatFilter('')} style={{ cursor:'pointer', fontWeight:700 }} title="Clear category">✕</span>
                </span>
              )}
            </div>
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', minWidth:820 }}>
                <thead><tr style={{ background:'var(--surface2)' }}>
                  {(isAppeals
                    ? ['Store','Market','Category','Reason','Device','IMEI/ESN','Phone','Date','Owed','Selling','Distributor Invoice #','Invoice Date']
                    : ['Store','Market','Category','Device','IMEI/ESN','Phone','Date','Owed','Selling','Distributor Invoice #','Invoice Date']
                  ).map(h => (
                    <th key={h} style={{ textAlign:'left', padding:'8px 12px', fontSize:11, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', whiteSpace:'nowrap' }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {shownRows.length === 0 ? (
                    <tr><td colSpan={isAppeals?12:11} style={{ padding:24, textAlign:'center', color:'var(--text3)', fontSize:13 }}>No line items for this filter.</td></tr>
                  ) : shownRows.map((r,i) => (
                    <tr key={r.id} style={{ borderTop:'1px solid var(--border)', background:i%2?'var(--surface2)':'transparent' }}>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.store||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12, color:'var(--text2)' }}>{r.market||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.category||'—'}</td>
                      {isAppeals && <td style={{ padding:'8px 12px', fontSize:12, color:'var(--text2)', maxWidth:320, whiteSpace:'normal' }}>{r.denial_reason||'—'}</td>}
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.device_model||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.esn_imei||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.phone_number||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.period_date ? String(r.period_date).slice(0,10) : '—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12, fontWeight:600 }}>{fmt(r.owed_to_vip||0)}</td>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.selling_price==null ? '—' : fmt(r.selling_price)}</td>
                      <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.vip_invoice_number||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
