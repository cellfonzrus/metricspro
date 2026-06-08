'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const GROUPS = [
  { id: 'all',          label: 'All Flags',          icon: '🚩' },
  { id: 'CHARGEBACK',   label: 'Chargebacks',         icon: '💸' },
  { id: 'IMEI_FRAUD',   label: 'IMEI Fraud',          icon: '🔴' },
  { id: 'SETUP_FEE_MISSING', label: 'Setup Fee',      icon: '🔧' },
  { id: 'RSK_ACTIVATIONS',   label: 'RSK',            icon: '⚡' },
  { id: 'UNMAPPED_PAYMENT_TYPE', label: 'Unmapped',   icon: '❓' },
  { id: 'HIGH_PORT_OUT_RATE',    label: 'Port Rate',  icon: '📊' },
  { id: 'MISSING_STORE_PAYMENT', label: 'Missing Pay', icon: '⚠️' },
]

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#dc2626', HIGH: '#d97706', MEDIUM: '#2563eb', LOW: '#64748b',
}

export default function FlagsPage() {
  const { period } = usePeriod()
  const [flags, setFlags] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [activeGroup, setActiveGroup] = useState('all')
  const [filterStore, setFilterStore] = useState('')
  const [stores, setStores] = useState<string[]>([])

  useEffect(() => {
    api(`/api/v1/commcalc/flags/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(d => {
        setFlags(d || [])
        const s = [...new Set((d || []).map((f: any) => f.store_address).filter(Boolean))].sort() as string[]
        setStores(s)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [period])

  const filtered = flags.filter(f => {
    if (activeGroup !== 'all' && f.flag_type !== activeGroup) return false
    if (filterStore && f.store_address !== filterStore) return false
    return true
  })

  const counts: Record<string, number> = { all: flags.length }
  flags.forEach(f => { counts[f.flag_type] = (counts[f.flag_type] || 0) + 1 })

  const critCount = flags.filter(f => f.severity === 'CRITICAL').length
  const highCount = flags.filter(f => f.severity === 'HIGH').length

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Flags & Compliance</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {flags.length} flags
            {critCount > 0 && <span style={{ color: '#dc2626', marginLeft: 8 }}>● {critCount} Critical</span>}
            {highCount > 0 && <span style={{ color: '#d97706', marginLeft: 8 }}>● {highCount} High</span>}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <select className="select" value={filterStore} onChange={e => setFilterStore(e.target.value)}>
            <option value="">All stores</option>
            {stores.map(s => <option key={s} value={s}>{s?.substring(0, 35)}</option>)}
          </select>
          <button className="btn btn-secondary" onClick={() => {
            const csv = ['Flag Type,Severity,Store,Rep,MDN,IMEI,Amount,Description']
            flags.forEach(f => csv.push(`"${f.flag_type}","${f.severity}","${f.store_address||''}","${f.epay_salesperson||''}","${f.mdn||''}","${f.imei||''}","${f.amount||''}","${f.description||''}"`.replace(/\n/g,' ')))
            const a = document.createElement('a'); a.href = 'data:text/csv,' + encodeURIComponent(csv.join('\n'))
            a.download = `flags-${period.replace(' ','-')}.csv`; a.click()
          }}>📥 CSV</button>
        </div>
      </div>

      {/* Group tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
        {GROUPS.map(g => (
          <button key={g.id} onClick={() => setActiveGroup(g.id)} className="btn" style={{
            background: activeGroup === g.id ? 'var(--accent)' : 'white',
            color: activeGroup === g.id ? 'white' : 'var(--text2)',
            border: `1px solid ${activeGroup === g.id ? 'var(--accent)' : 'var(--border)'}`,
            fontSize: 12,
          }}>
            {g.icon} {g.label}
            {counts[g.id] > 0 && (
              <span style={{ marginLeft: 6, background: activeGroup === g.id ? 'rgba(255,255,255,0.25)' : 'var(--surface2)', borderRadius: 999, padding: '1px 7px', fontWeight: 700, fontSize: 11 }}>
                {counts[g.id]}
              </span>
            )}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {flags.length === 0 ? 'No flags — run calculation to generate flag report' : 'No flags in this category'}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filtered.map((f, i) => (
            <div key={i} className="card" style={{ padding: '14px 18px', borderLeft: `4px solid ${SEVERITY_COLORS[f.severity] || '#94a3b8'}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: SEVERITY_COLORS[f.severity] || 'var(--text3)',
                      background: (SEVERITY_COLORS[f.severity] || '#94a3b8') + '18',
                      padding: '2px 8px', borderRadius: 999, textTransform: 'uppercase' }}>
                      {f.severity}
                    </span>
                    <span style={{ fontSize: 11, background: 'var(--surface2)', color: 'var(--text2)',
                      padding: '2px 8px', borderRadius: 999, fontWeight: 600 }}>
                      {f.flag_type?.replace(/_/g, ' ')}
                    </span>
                    {f.amount != null && f.amount !== 0 && (
                      <span style={{ fontSize: 12, fontWeight: 700, color: f.amount < 0 ? 'var(--red)' : 'var(--text)' }}>
                        {fmt(Math.abs(f.amount))}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>{f.description}</div>
                  {f.coaching_note && (
                    <div style={{ fontSize: 12, color: 'var(--text3)', fontStyle: 'italic' }}>
                      💡 {f.coaching_note}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
                    {f.store_address && <span style={{ fontSize: 11, color: 'var(--text3)' }}>🏪 {f.store_address.substring(0,35)}</span>}
                    {f.epay_salesperson && <span style={{ fontSize: 11, color: 'var(--text3)' }}>👤 {f.epay_salesperson}</span>}
                    {f.mdn && <span style={{ fontSize: 11, color: 'var(--text3)' }}>📱 {f.mdn}</span>}
                    {f.imei && <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>IMEI: {f.imei}</span>}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
