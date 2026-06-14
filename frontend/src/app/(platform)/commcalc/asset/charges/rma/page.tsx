'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

type Row = { id:number; store:string; market:string; esn_imei:string|null; phone_number:string|null
  device_model:string|null; status:string|null; date_sold:string|null; owed_to_vip:number|null
  reimbursement:number|null; reimbursement_date:string|null; selling_price:number|null
  _bucket?:string; _shortfall?:number }
type Bucket = { count:number; owed:number; reimb:number; rows:Row[] }
type RmaData = { buckets:{ full:Bucket; short:Bucket; none:Bucket }; net_loss:number; total_rma:number }

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']

const BUCKET_META: Record<string,{label:string;color:string;note:string}> = {
  none:  { label:'Not Reimbursed', color:'#dc2626', note:'No credit received — full amount lost' },
  short: { label:'Reimbursed Short', color:'#d97706', note:'Credited less than owed' },
  full:  { label:'Reimbursed in Full', color:'#059669', note:'Fully credited' },
}

function RmaTable({ rows, showShort }: { rows: Row[]; showShort?: boolean }) {
  if (!rows.length) return <div style={{ padding:18, color:'var(--text3)', fontSize:13 }}>No devices.</div>
  return (
    <div style={{ overflowX:'auto' }}>
      <table style={{ width:'100%', borderCollapse:'collapse', minWidth:780 }}>
        <thead><tr style={{ background:'var(--surface2)' }}>
          {['Store','Market','Device','IMEI/ESN','Sold','Owed','Reimbursed','Selling','Reimb Date', showShort?'Short':'Gap'].map(h => (
            <th key={h} style={{ textAlign:'left', padding:'8px 12px', fontSize:11, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', whiteSpace:'nowrap' }}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {rows.map((r,i) => (
            <tr key={r.id} style={{ borderTop:'1px solid var(--border)', background:i%2?'var(--surface2)':'transparent' }}>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.store||'—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, color:'var(--text2)' }}>{r.market||'—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.device_model||'—'}</td>
              <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.esn_imei||'—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.date_sold ? String(r.date_sold).slice(0,10) : '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, fontWeight:600 }}>{fmt(r.owed_to_vip||0)}</td>
              <td style={{ padding:'8px 12px', fontSize:12, color:'#059669' }}>{fmt(r.reimbursement||0)}</td>
              <td style={{ padding:'8px 12px', fontSize:12 }}>{r.selling_price==null ? '—' : fmt(r.selling_price)}</td>
              <td style={{ padding:'8px 12px', fontSize:12, whiteSpace:'nowrap' }}>{r.reimbursement_date ? String(r.reimbursement_date).slice(0,10) : '—'}</td>
              <td style={{ padding:'8px 12px', fontSize:12, fontWeight:700, color:'#dc2626' }}>{fmt(r._shortfall||0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function RmaPage() {
  const [market, setMarket] = useState('')
  const [store, setStore] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])
  const [data, setData] = useState<RmaData | null>(null)
  const [loading, setLoading] = useState(true)
  const [month, setMonth] = useState(0)  // 0 = all time
  const [year, setYear] = useState(new Date().getFullYear())

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d:any) => { setMarkets(d.markets||[]); setStores(d.stores||[]) }).catch(console.error)
  }, [])
  useEffect(() => { load() }, [market, store, month, year])

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (market) qs.set('market', market)
      if (store) qs.set('store', store)
      if (month) { qs.set('month', String(month)); qs.set('year', String(year)) }
      setData(await api(`/api/v1/asset/rma?${qs.toString()}`))
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  const visibleStores = market ? stores.filter(s => s.market === market) : stores
  const sel = { padding:'6px 10px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, background:'var(--surface)' }

  function buildPayload(): ExportPayload {
    const cols: ExportColumn[] = [
      { header:'Store', get:r=>r.store },
      { header:'Market', get:r=>r.market },
      { header:'Device', get:r=>r.device_model },
      { header:'IMEI/ESN', get:r=>r.esn_imei },
      { header:'Sold', get:r=> r.date_sold ? String(r.date_sold).slice(0,10) : '' },
      { header:'Owed', get:r=>r.owed_to_vip, money:true },
      { header:'Reimbursed', get:r=>r.reimbursement, money:true },
      { header:'Selling Price', get:r=>r.selling_price, money:true },
      { header:'Reimb Date', get:r=> r.reimbursement_date ? String(r.reimbursement_date).slice(0,10) : '' },
      { header:'Shortfall', get:r=>r._shortfall, money:true },
    ]
    const filterLabel = [market||null, store||null].filter(Boolean).join(' · ') || 'All markets'
    return {
      title: 'RMA Reconciliation',
      subtitle: `${filterLabel} · Net loss ${fmt(data?.net_loss || 0)}`,
      filename: `rma-reconciliation${store?'-'+store.replace(/[^a-z0-9]+/gi,'-').toLowerCase():''}`,
      sheets: [
        { name:'Not Reimbursed', rows:data?.buckets.none.rows||[], columns:cols },
        { name:'Reimbursed Short', rows:data?.buckets.short.rows||[], columns:cols },
        { name:'Reimbursed Full', rows:data?.buckets.full.rows||[], columns:cols },
      ],
    }
  }

  return (
    <div>
      <div style={{ marginBottom:20, display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:12, flexWrap:'wrap' }}>
        <div>
          <a href="/commcalc/asset/dashboard" style={{ fontSize:13, color:'var(--text3)', textDecoration:'none' }}>← Charges Dashboard</a>
          <h1 style={{ fontSize:22, fontWeight:700, margin:'6px 0 0' }}>RMA Reconciliation</h1>
          <p style={{ color:'var(--text2)', fontSize:14, margin:'4px 0 0' }}>
            Returned devices — which were reimbursed in full, short-paid, or never credited by VIP.
          </p>
        </div>
        {data && <ExportButtons payload={buildPayload} />}
        {data && <SendReportButton reportKey="rma" filters={{ ...(store?{store}:{}), ...(market?{market}:{}), ...(month?{month, year}:{}) }} />}
      </div>

      <div className="card" style={{ padding:14, marginBottom:20, display:'flex', gap:12, alignItems:'center', flexWrap:'wrap' }}>
        <span style={{ fontSize:13, fontWeight:600, color:'var(--text2)' }}>Filters:</span>
        <select style={sel} value={market} onChange={e=>{ setMarket(e.target.value); setStore('') }}>
          <option value="">All markets</option>{markets.map(m=><option key={m} value={m}>{m}</option>)}
        </select>
        <select style={sel} value={store} onChange={e=>setStore(e.target.value)}>
          <option value="">All stores</option>{visibleStores.map(s=><option key={s.store} value={s.store}>{s.store}</option>)}
        </select>
        <select style={sel} value={month} onChange={e=>setMonth(+e.target.value)} title="Sold in">
          <option value={0}>All time</option>{MONTHS.map((m,i)=><option key={m} value={i+1}>{m}</option>)}
        </select>
        {month > 0 && (
          <select style={sel} value={year} onChange={e=>setYear(+e.target.value)}>
            {[2024,2025,2026].map(y=><option key={y} value={y}>{y}</option>)}
          </select>
        )}
      </div>

      {loading ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>Loading…</div>
      ) : !data ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>No data.</div>
      ) : (
        <>
          {/* Net loss headline */}
          <div className="card" style={{ padding:'20px 24px', marginBottom:20, borderLeft:'5px solid #dc2626', background:'#fef2f2' }}>
            <div style={{ fontSize:13, fontWeight:600, color:'#991b1b', textTransform:'uppercase', letterSpacing:'0.05em' }}>RMA Net Loss (uncredited + shortfall)</div>
            <div style={{ fontSize:30, fontWeight:800, color:'#dc2626', margin:'6px 0 2px' }}>{fmt(data.net_loss)}</div>
            <div style={{ fontSize:13, color:'#7f1d1d' }}>{data.total_rma} RMA devices total</div>
          </div>

          {/* Bucket cards */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:16, marginBottom:24 }}>
            {(['none','short','full'] as const).map(k => {
              const b = data.buckets[k]; const m = BUCKET_META[k]
              return (
                <div key={k} className="card" style={{ padding:'18px 22px', borderTop:`3px solid ${m.color}` }}>
                  <div style={{ fontSize:12, fontWeight:600, color:'var(--text2)', textTransform:'uppercase' }}>{m.label}</div>
                  <div style={{ fontSize:24, fontWeight:700, color:m.color, marginTop:6 }}>{fmt(b.owed)}</div>
                  <div style={{ fontSize:12, color:'var(--text3)', marginTop:2 }}>{b.count} devices · {m.note}</div>
                </div>
              )
            })}
          </div>

          {/* Not reimbursed */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'#991b1b' }}>
              🔴 Not Reimbursed ({data.buckets.none.count}) — {fmt(data.buckets.none.owed)} uncredited
            </div>
            <RmaTable rows={data.buckets.none.rows} />
          </div>

          {/* Short */}
          <div className="card" style={{ padding:0, marginBottom:20 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'#92400e' }}>
              🟠 Reimbursed Short ({data.buckets.short.count}) — {fmt(data.buckets.short.owed - data.buckets.short.reimb)} shortfall
            </div>
            <RmaTable rows={data.buckets.short.rows} showShort />
          </div>

          {/* Full */}
          <div className="card" style={{ padding:0 }}>
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14, color:'#065f46' }}>
              🟢 Reimbursed in Full ({data.buckets.full.count})
            </div>
            <RmaTable rows={data.buckets.full.rows} />
          </div>
        </>
      )}
    </div>
  )
}