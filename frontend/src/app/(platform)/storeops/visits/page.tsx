'use client'
import { useState, useEffect, useMemo } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { ExportButtons, ExportPayload } from '@/lib/export'

const cell: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

function fmtDateTime(s?: string | null) {
  if (!s) return '—'
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function StoreVisitsPage() {
  const { user, permissions } = useAuth()
  const [visits, setVisits] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [market, setMarket] = useState('')
  const [storeF, setStoreF] = useState('')

  const isAdmin = permissions?.scope === 'all' || !!permissions?.modules?.admin

  useEffect(() => {
    // Default the market filter to the DM's own market (Market Manager scope).
    if (user?.market && permissions?.scope === 'market') setMarket(user.market)
  }, [user, permissions])

  function load() {
    setLoading(true)
    Promise.all([
      api('/api/v1/storevisit/visits').catch(() => []),
      api('/api/v1/storevisit/stores').catch(() => []),
    ]).then(([v, s]) => { setVisits(v || []); setStores(s || []) })
      .catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const markets = useMemo(() => Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort(), [stores])
  const filtered = visits.filter(v =>
    (!market || (v.market || '') === market) && (!storeF || (v.store_code || '') === storeF))

  function payload(): ExportPayload {
    return {
      title: 'Store Visits',
      subtitle: [market, storeF].filter(Boolean).join(' · ') || 'All stores',
      filename: 'store-visits',
      sheets: [{
        name: 'Visits',
        rows: filtered,
        columns: [
          { header: 'Check-in', get: r => fmtDateTime(r.check_in_at) },
          { header: 'Store', get: r => r.store_address || r.store_code },
          { header: 'Market', get: r => r.market },
          { header: 'DM', get: r => r.dm_name },
          { header: 'Scheduled rep', get: r => r.scheduled_rep },
          { header: 'Actual rep', get: r => r.actual_rep },
          { header: 'Status', get: r => r.status },
        ],
      }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📝 Store Visits</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            District-manager store visits: check-in, inspection checklist, accessories to order, and the clean-store photo.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {!loading && filtered.length > 0 && <ExportButtons payload={payload} />}
          {isAdmin && <Link href="/storeops/visits/settings" className="btn btn-secondary" style={{ fontSize: 13 }}>🧾 Checklist</Link>}
          <Link href="/storeops/visits/new" className="btn btn-primary" style={{ fontSize: 13 }}>＋ Start visit</Link>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <select style={sel} value={market} onChange={e => { setMarket(e.target.value); setStoreF('') }}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={sel} value={storeF} onChange={e => setStoreF(e.target.value)}>
          <option value="">All stores</option>
          {stores.filter(s => !market || s.market === market).map(s =>
            <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
        </select>
        {(market || storeF) && <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => { setMarket(''); setStoreF('') }}>✕ Clear</button>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No store visits yet. Click <strong>Start visit</strong> to check in at a store and run the inspection checklist.
        </div>
      ) : (
        <div className="table-wrapper">
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Check-in', 'Store', 'Market', 'DM', 'Scheduled rep', 'Actual rep', 'Status', ''].map((h, i) =>
                <th key={h || i} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {filtered.map(v => {
                const mismatch = v.actual_rep && v.scheduled_rep && v.actual_rep !== v.scheduled_rep
                return (
                  <tr key={v.id}>
                    <td style={cell}>{fmtDateTime(v.check_in_at)}</td>
                    <td style={{ ...cell, fontWeight: 500 }}>{v.store_address || v.store_code || '—'}</td>
                    <td style={{ ...cell, color: 'var(--text3)', fontSize: 12 }}>{v.market || '—'}</td>
                    <td style={cell}>{v.dm_name || '—'}</td>
                    <td style={cell}>{v.scheduled_rep || '—'}</td>
                    <td style={{ ...cell, color: mismatch ? 'var(--amber)' : undefined }}>{v.actual_rep || '—'}{mismatch ? ' ⚠️' : ''}</td>
                    <td style={cell}>
                      <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10,
                        background: v.status === 'submitted' ? 'var(--green-bg, #e6f7ec)' : 'var(--surface2)',
                        color: v.status === 'submitted' ? 'var(--green, #16794a)' : 'var(--text2)' }}>
                        {v.status === 'submitted' ? 'Submitted' : 'In progress'}
                      </span>
                    </td>
                    <td style={cell}><Link href={`/storeops/visits/${v.id}`} style={{ fontSize: 13, color: 'var(--accent)' }}>View →</Link></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
