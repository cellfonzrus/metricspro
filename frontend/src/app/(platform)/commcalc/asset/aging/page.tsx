'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'

type Row = {
  id: number; store: string; market: string; esn_imei: string|null; phone_number: string|null
  device_model: string|null; category: string|null; status: string|null
  acquired_date: string|null; due_date: string|null; owed_to_vip: number|null; days_aged?: number
}
type Bucket = { count: number; owed: number; rows: Row[] }
type Aging = {
  today: string; data_as_of: string|null
  buckets: { under45: Bucket; warn: Bucket; missed: Bucket }
  zero_inventory: { count: number; rows: Row[] }
  totals: { flagged_count: number; flagged_owed: number }
}

function daysSince(iso: string|null) {
  if (!iso) return null
  const d = new Date(iso + 'T00:00:00'); const now = new Date()
  return Math.floor((+now - +d) / 86400000)
}

function RowTable({ rows, accent }: { rows: Row[]; accent: string }) {
  if (!rows.length) return <div style={{ padding: 18, color: 'var(--text3)', fontSize: 13 }}>No devices.</div>
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
        <thead>
          <tr style={{ background: 'var(--surface2)' }}>
            {['Store','Market','Device','IMEI/ESN','Acquired','Days','Due Date','Owed'].map(h => (
              <th key={h} style={{ textAlign:'left', padding:'8px 12px', fontSize:11, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', whiteSpace:'nowrap' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id} style={{ borderTop:'1px solid var(--border)', background: i%2===0?'transparent':'var(--surface2)' }}>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.store || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, color:'var(--text2)' }}>{r.market || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.device_model || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.esn_imei || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.acquired_date || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, fontWeight:700, color: accent }}>{r.days_aged ?? '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.due_date || '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, fontWeight:600 }}>{fmt(r.owed_to_vip || 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function AgingPage() {
  const [market, setMarket] = useState('')
  const [store, setStore] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])
  const [data, setData] = useState<Aging | null>(null)
  const [loading, setLoading] = useState(true)
  const [showZero, setShowZero] = useState(false)

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d:any) => { setMarkets(d.markets||[]); setStores(d.stores||[]) })
      .catch(console.error)
  }, [])
  useEffect(() => { load() }, [market, store])

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (market) qs.set('market', market)
      if (store) qs.set('store', store)
      setData(await api(`/api/v1/asset/aging?${qs.toString()}`))
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  const visibleStores = market ? stores.filter(s => s.market === market) : stores
  const selStyle = { padding:'6px 10px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, background:'var(--surface)' }
  const staleDays = data?.data_as_of ? daysSince(data.data_as_of) : null
  const isStale = staleDays !== null && staleDays > 3

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <a href="/commcalc/asset" style={{ fontSize:13, color:'var(--text3)', textDecoration:'none' }}>← Asset Ledger</a>
        <h1 style={{ fontSize:22, fontWeight:700, margin:'6px 0 0' }}>Inventory Aging — Sell Before 60 Days</h1>
        <p style={{ color:'var(--text2)', fontSize:14, margin:'4px 0 0' }}>
          Unsold NET60 inventory. Devices in the 45–60 day window must sell before day 60 or VIP bills them unsold.
        </p>
      </div>

      {/* Stale data banner */}
      {isStale && (
        <div style={{ background:'#fef2f2', border:'1px solid #fecaca', borderRadius:10, padding:'12px 16px', marginBottom:16, fontSize:13, color:'#991b1b' }}>
          ⚠️ <strong>Data is {staleDays} days old</strong> (as of {data?.data_as_of}). Aging is measured from today, so the 45–60 warning window may be empty until you upload a current Asset_Lending.xlsx.
        </div>
      )}

      {/* Filters */}
      <div className="card" style={{ padding:14, marginBottom:20, display:'flex', gap:12, alignItems:'center', flexWrap:'wrap' }}>
        <span style={{ fontSize:13, fontWeight:600, color:'var(--text2)' }}>Filters:</span>
        <select style={selStyle} value={market} onChange={e => { setMarket(e.target.value); setStore('') }}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={selStyle} value={store} onChange={e => setStore(e.target.value)}>
          <option value="">All stores</option>
          {visibleStores.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
        </select>
        {data?.data_as_of && <span style={{ marginLeft:'auto', fontSize:12, color:'var(--text3)' }}>Data as of {data.data_as_of} · aged to {data.today}</span>}
      </div>

      {loading ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>Loading…</div>
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
        </>
      )}
    </div>
  )
}