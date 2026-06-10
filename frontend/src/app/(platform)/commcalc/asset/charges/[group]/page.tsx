'use client'
import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import { api, fmt, ORG_ID } from '@/lib/client'

// URL slug -> backend group key + display config
const GROUP_MAP: Record<string, { key: string; title: string; color: string; critical?: boolean; blurb: string }> = {
  'appeals':       { key:'appeals',       title:'Appeals & Denied Payments', color:'#dc2626', critical:true,
                     blurb:'Boost denied or is withholding payment for these activations/reimbursements. Each is a potential direct loss — review and appeal.' },
  'vip-fees':      { key:'vip_fees',       title:'VIP Fees', color:'#2563eb',
                     blurb:'Processing, shipping, and SIM kit fees billed by VIP.' },
  'stock-balance': { key:'stock_balance',  title:'Stock Balancing / Returns', color:'#d97706',
                     blurb:'Phones returned to VIP because they could not sell before 60 days (FIFO, unopened). Confirm VIP credited these.' },
  'recon':         { key:'recon_oddity',   title:'Reconciliation Oddities', color:'#7c3aed',
                     blurb:'Payment/data mismatches — wrong ESN paid, missing Elevate data, coupon issues, exchanges.' },
}

type Row = { id:number; store:string; market:string; esn_imei:string|null; phone_number:string|null
  device_model:string|null; category:string|null; status:string|null; owed_to_vip:number|null; notes:string|null }
type Group = { key:string; label:string; count:number; owed:number
  by_category:{category:string;count:number;owed:number}[]
  by_store:{store:string;market:string;count:number;owed:number}[]; rows:Row[] }

export default function ChargeGroupPage() {
  const params = useParams()
  const slug = String(params.group || '')
  const cfg = GROUP_MAP[slug]

  const [market, setMarket] = useState('')
  const [store, setStore] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])
  const [group, setGroup] = useState<Group | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d:any) => { setMarkets(d.markets||[]); setStores(d.stores||[]) }).catch(console.error)
  }, [])
  useEffect(() => { if (cfg) load() }, [market, store, slug])

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (market) qs.set('market', market)
      if (store) qs.set('store', store)
      const d = await api(`/api/v1/asset/charges-summary?${qs.toString()}`)
      setGroup(d.groups[cfg.key] || null)
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  if (!cfg) return <div style={{ padding:40 }}>Unknown report. <a href="/commcalc/asset/dashboard">Back to dashboard</a></div>

  const visibleStores = market ? stores.filter(s => s.market === market) : stores
  const selStyle = { padding:'6px 10px', borderRadius:8, border:'1px solid var(--border)', fontSize:13, background:'var(--surface)' }

  return (
    <div>
      <div style={{ marginBottom:20 }}>
        <a href="/commcalc/asset/dashboard" style={{ fontSize:13, color:'var(--text3)', textDecoration:'none' }}>← Charges Dashboard</a>
        <h1 style={{ fontSize:22, fontWeight:700, margin:'6px 0 0' }}>{cfg.title}</h1>
        <p style={{ color:'var(--text2)', fontSize:14, margin:'4px 0 0' }}>{cfg.blurb}</p>
      </div>

      {cfg.critical && (
        <div style={{ background:'#fef2f2', border:'2px solid #dc2626', borderRadius:10, padding:'14px 18px', marginBottom:20, color:'#991b1b' }}>
          <strong>🚨 Critical — money at stake.</strong> These are denied/withheld Boost payments. Use “Push Appeals to Flags” on the dashboard to track them on the Flags page.
        </div>
      )}

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
      </div>

      {loading ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>Loading…</div>
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
              <div style={{ padding:'12px 16px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14 }}>By Category</div>
              <table style={{ width:'100%', borderCollapse:'collapse' }}><tbody>
                {group.by_category.map((c,i) => (
                  <tr key={c.category} style={{ borderTop:'1px solid var(--border)', background:i%2?'var(--surface2)':'transparent' }}>
                    <td style={{ padding:'8px 16px', fontSize:13 }}>{c.category}</td>
                    <td style={{ padding:'8px 16px', fontSize:13, textAlign:'right' }}>{c.count}</td>
                    <td style={{ padding:'8px 16px', fontSize:13, fontWeight:600, textAlign:'right' }}>{fmt(c.owed)}</td>
                  </tr>
                ))}
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
            <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--border)', fontWeight:600, fontSize:14 }}>
              Line Items <span style={{ fontWeight:400, color:'var(--text3)', fontSize:12 }}>({group.rows.length.toLocaleString()})</span>
            </div>
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', minWidth:760 }}>
                <thead><tr style={{ background:'var(--surface2)' }}>
                  {['Store','Market','Category','Device','IMEI/ESN','Phone','Owed'].map(h => (
                    <th key={h} style={{ textAlign:'left', padding:'8px 12px', fontSize:11, fontWeight:600, color:'var(--text2)', textTransform:'uppercase', whiteSpace:'nowrap' }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {group.rows.map((r,i) => (
                    <tr key={r.id} style={{ borderTop:'1px solid var(--border)', background:i%2?'var(--surface2)':'transparent' }}>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.store||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12, color:'var(--text2)' }}>{r.market||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.category||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.device_model||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:11, fontFamily:'monospace' }}>{r.esn_imei||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12 }}>{r.phone_number||'—'}</td>
                      <td style={{ padding:'8px 12px', fontSize:12, fontWeight:600 }}>{fmt(r.owed_to_vip||0)}</td>
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