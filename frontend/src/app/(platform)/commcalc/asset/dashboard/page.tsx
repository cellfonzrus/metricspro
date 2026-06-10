'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'

type Group = { key: string; label: string; count: number; owed: number }
type Summary = { groups: Record<string, Group> }

const TILES = [
  { key:'appeals',       icon:'🚨', href:'/commcalc/asset/charges/appeals',       color:'#dc2626', blurb:'Boost denied or withheld payment — direct loss. Critical.' },
  { key:'vip_fees',      icon:'🧾', href:'/commcalc/asset/charges/vip-fees',       color:'#2563eb', blurb:'Processing, shipping, and SIM kit fees billed by VIP.' },
  { key:'stock_balance', icon:'📦', href:'/commcalc/asset/charges/stock-balance',  color:'#d97706', blurb:'Phones returned to VIP (unsold before 60 days, FIFO).' },
  { key:'recon_oddity',  icon:'🔍', href:'/commcalc/asset/charges/recon',          color:'#7c3aed', blurb:'Payment/data mismatches to investigate.' },
]

export default function AssetDashboard() {
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')

  useEffect(() => {
    api(`/api/v1/asset/charges-summary?org_id=${ORG_ID}`)
      .then((d:any) => setData(d)).catch(console.error).finally(() => setLoading(false))
  }, [])

  async function pushAppeals() {
    setSyncing(true); setSyncMsg('')
    try {
      const res = await fetch(`https://metricspro-production.up.railway.app/api/v1/asset/sync-appeal-flags?org_id=${ORG_ID}`, { method:'POST' })
      const d = await res.json()
      setSyncMsg(`✅ ${d.appeal_flags_written} appeal flags pushed to Flags page`)
    } catch(e:any) { setSyncMsg(`❌ ${e.message}`) }
    setSyncing(false)
  }

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:24 }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize:13, color:'var(--text3)', textDecoration:'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize:22, fontWeight:700, margin:'6px 0 0' }}>Asset Charges Dashboard</h1>
          <p style={{ color:'var(--text2)', fontSize:14, margin:'4px 0 0' }}>
            VIP charges and adjustments outside the normal sale/inventory flow.
          </p>
        </div>
        <div style={{ display:'flex', gap:10, alignItems:'center' }}>
          {syncMsg && <span style={{ fontSize:13 }}>{syncMsg}</span>}
          <button className="btn" onClick={pushAppeals} disabled={syncing}>
            {syncing ? '⏳ Pushing…' : '🚨 Push Appeals to Flags'}
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>Loading…</div>
      ) : !data ? (
        <div style={{ textAlign:'center', padding:60, color:'var(--text3)' }}>No data.</div>
      ) : (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:18 }}>
          {TILES.map(t => {
            const g = data.groups[t.key]
            if (!g) return null
            return (
              <a key={t.key} href={t.href} style={{ textDecoration:'none' }}>
                <div className="card" style={{ padding:'22px 24px', borderTop:`4px solid ${t.color}`, height:'100%' }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
                    <div style={{ fontSize:15, fontWeight:700, color:'var(--text1)' }}>{t.icon} {g.label}</div>
                    <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'0.05em' }}>{g.count.toLocaleString()} items</div>
                  </div>
                  <div style={{ fontSize:28, fontWeight:700, color:t.color, margin:'10px 0 4px' }}>{fmt(g.owed)}</div>
                  <div style={{ fontSize:13, color:'var(--text2)' }}>{t.blurb}</div>
                  <div style={{ fontSize:12, color:t.color, fontWeight:600, marginTop:14 }}>View report →</div>
                </div>
              </a>
            )
          })}
        </div>
      )}
    </div>
  )
}