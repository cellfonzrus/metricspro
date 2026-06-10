'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'

type Group = { key: string; label: string; count: number; owed: number }
type Summary = { groups: Record<string, Group>; total_loss: { total: number; appeals: number; rma: number } }

const TILES = [
  { key:'appeals',       icon:'🚨', href:'/commcalc/asset/charges/appeals',      color:'#dc2626', blurb:'Boost denied or withheld payment — direct loss.' },
  { key:'rma',           icon:'🔁', href:'/commcalc/asset/charges/rma',          color:'#db2777', blurb:'Returned devices not reimbursed or short-paid.' },
  { key:'vip_fees',      icon:'🧾', href:'/commcalc/asset/charges/vip-fees',      color:'#2563eb', blurb:'Processing, shipping, and SIM kit fees.' },
  { key:'stock_balance', icon:'📦', href:'/commcalc/asset/charges/stock-balance', color:'#d97706', blurb:'Phones returned to VIP (unsold before 60 days).' },
  { key:'recon_oddity',  icon:'🔍', href:'/commcalc/asset/charges/recon',         color:'#7c3aed', blurb:'Payment/data mismatches to investigate.' },
]

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']

function upcomingFriday(from = new Date()) {
  const d = new Date(from); const diff = (5 - d.getDay() + 7) % 7
  d.setDate(d.getDate() + diff); return d
}
function ymd(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
}

export default function AssetDashboard() {
  const [data, setData] = useState<Summary | null>(null)
  const [rmaData, setRmaData] = useState<{count:number;owed:number}>({count:0, owed:0})
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')

  // filters
  const [mode, setMode] = useState<'all'|'month'|'week'>('all')
  const [month, setMonth] = useState(new Date().getMonth()+1)
  const [year, setYear] = useState(new Date().getFullYear())
  const [weekFriday, setWeekFriday] = useState(ymd(upcomingFriday()))
  const [market, setMarket] = useState('')
  const [store, setStore] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d:any) => { setMarkets(d.markets||[]); setStores(d.stores||[]) }).catch(console.error)
  }, [])
  useEffect(() => { load() }, [mode, month, year, weekFriday, market, store])

  function buildQS() {
    const qs = new URLSearchParams({ org_id: ORG_ID })
    if (market) qs.set('market', market)
    if (store) qs.set('store', store)
    if (mode === 'month') { qs.set('month', String(month)); qs.set('year', String(year)) }
    if (mode === 'week') qs.set('week_friday', weekFriday)
    return qs
  }

  async function load() {
    setLoading(true)
    try {
      const qs = buildQS()
      const d = await api(`/api/v1/asset/charges-summary?${qs.toString()}`)
      setData(d)
      // RMA tile value from the /rma endpoint (net-loss aware); reuse same store/market
      const rqs = new URLSearchParams({ org_id: ORG_ID })
      if (market) rqs.set('market', market); if (store) rqs.set('store', store)
      try {
        const r = await api(`/api/v1/asset/rma?${rqs.toString()}`)
        const cnt = (r.buckets?.none?.count||0) + (r.buckets?.short?.count||0) + (r.buckets?.full?.count||0)
        setRmaData({ count: cnt, owed: r.net_loss || 0 })
      } catch { setRmaData({count:0,owed:0}) }
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  async function pushFlags() {
    setSyncing(true); setSyncMsg('')
    try {
      const a = await fetch(`https://metricspro-production.up.railway.app/api/v1/asset/sync-appeal-flags?org_id=${ORG_ID}`, { method:'POST' })
      const ad = await a.json()
      const m = await fetch(`https://metricspro-production.up.railway.app/api/v1/asset/sync-rma-flags?org_id=${ORG_ID}`, { method:'POST' })
      const md = await m.json()
      setSyncMsg(`✅ ${ad.appeal_flags_written} appeals + ${md.rma_flags_written} RMA flags pushed`)
    } catch(e:any) { setSyncMsg(`❌ ${e.message}`) }
    setSyncing(false)
  }

  const visibleStores = market ? stores.filter(s => s.market === market) : stores
  const sel = { padding:'6px 10px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, background:'var(--surface)' }
  const tabBtn = (active:boolean) => ({ padding:'6px 14px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, cursor:'pointer', fontWeight:600,
    background: active ? 'var(--accent)' : 'var(--surface)', color: active ? '#fff' : 'var(--text2)' })

  function tileVal(key:string): {count:number;owed:number} {
    if (key === 'rma') return rmaData
    const g = data?.groups[key]; return { count: g?.count||0, owed: g?.owed||0 }
  }

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:20 }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize:13, color:'var(--text3)', textDecoration:'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize:22, fontWeight:700, margin:'6px 0 0' }}>Asset Charges Dashboard</h1>
        </div>
        <div style={{ display:'flex', gap:10, alignItems:'center' }}>
          {syncMsg && <span style={{ fontSize:13 }}>{syncMsg}</span>}
          <button className="btn" onClick={pushFlags} disabled={syncing}>{syncing ? '⏳ Pushing…' : '🚨 Push Flags (Appeals + RMA)'}</button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="card" style={{ padding:14, marginBottom:20, display:'flex', gap:10, alignItems:'center', flexWrap:'wrap' }}>
        <div style={{ display:'flex', gap:6 }}>
          <button style={tabBtn(mode==='all')}   onClick={() => setMode('all')}>All time</button>
          <button style={tabBtn(mode==='month')} onClick={() => setMode('month')}>Month</button>
          <button style={tabBtn(mode==='week')}  onClick={() => setMode('week')}>Week</button>
        </div>
        {mode==='month' && (
          <>
            <select style={sel} value={month} onChange={e=>setMonth(+e.target.value)}>
              {MONTHS.map((m,i)=><option key={m} value={i+1}>{m}</option>)}
            </select>
            <select style={sel} value={year} onChange={e=>setYear(+e.target.value)}>
              {[2024,2025,2026].map(y=><option key={y} value={y}>{y}</option>)}
            </select>
          </>
        )}
        {mode==='week' && (
          <input type="date" style={sel} value={weekFriday} onChange={e=>setWeekFriday(e.target.value)} />
        )}
        <div style={{ width:1, height:24, background:'var(--border)' }} />
        <select style={sel} value={market} onChange={e=>{ setMarket(e.target.value); setStore('') }}>
          <option value="">All markets</option>{markets.map(m=><option key={m} value={m}>{m}</option>)}
        </select>
        <select style={sel} value={store} onChange={e=>setStore(e.target.value)}>
          <option value="">All stores</option>{visibleStores.map(s=><option key={s.store} value={s.store}>{s.store}</option>)}
        </select>
      </div>

      {loading ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>Loading…</div>
      ) : !data ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>No data.</div>
      ) : (
        <>
          {/* Total Loss headline */}
          <div className="card" style={{ padding:'22px 26px', marginBottom:20, borderLeft:'5px solid #dc2626', background:'#fef2f2' }}>
            <div style={{ fontSize:13, fontWeight:600, color:'#991b1b', textTransform:'uppercase', letterSpacing:'0.05em' }}>Total Loss (Denied Appeals + RMA Net Loss)</div>
            <div style={{ fontSize:34, fontWeight:800, color:'#dc2626', margin:'8px 0 4px' }}>{fmt(data.total_loss.total)}</div>
            <div style={{ fontSize:13, color:'#7f1d1d' }}>
              Appeals {fmt(data.total_loss.appeals)} · RMA net loss {fmt(data.total_loss.rma)}
            </div>
          </div>

          <div style={{ display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:18 }}>
            {TILES.map(t => {
              const v = tileVal(t.key)
              return (
                <a key={t.key} href={t.href} style={{ textDecoration:'none' }}>
                  <div className="card" style={{ padding:'22px 24px', borderTop:`4px solid ${t.color}`, height:'100%' }}>
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
                      <div style={{ fontSize:15, fontWeight:700 }}>{t.icon} {t.key==='rma' ? 'RMA Reconciliation' : data.groups[t.key]?.label}</div>
                      <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase' }}>{v.count.toLocaleString()} items</div>
                    </div>
                    <div style={{ fontSize:28, fontWeight:700, color:t.color, margin:'10px 0 4px' }}>{fmt(v.owed)}</div>
                    <div style={{ fontSize:13, color:'var(--text2)' }}>{t.key==='rma' ? 'Net loss (unreimbursed + shortfall)' : t.blurb}</div>
                    <div style={{ fontSize:12, color:t.color, fontWeight:600, marginTop:14 }}>View report →</div>
                  </div>
                </a>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}