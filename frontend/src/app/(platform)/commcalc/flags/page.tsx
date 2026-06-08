'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#dc2626', HIGH: '#d97706', MEDIUM: '#2563eb', LOW: '#64748b',
}

const WINDOWS = [
  { id: '', label: 'Any window' },
  { id: '30', label: '≤ 30 days' },
  { id: '60', label: '≤ 60 days' },
  { id: '90', label: '≤ 90 days' },
  { id: '90+', label: '90+ days' },
]

export default function FlagsPage() {
  const { period } = usePeriod()
  const [flags, setFlags] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [fType, setFType] = useState('')
  const [fRep, setFRep] = useState('')
  const [fStore, setFStore] = useState('')
  const [fSearch, setFSearch] = useState('')
  const [fModel, setFModel] = useState('')
  const [fWindow, setFWindow] = useState('')
  const [sortKey, setSortKey] = useState('days_active')
  const [sortDir, setSortDir] = useState<'asc'|'desc'>('asc')

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/commcalc/flags/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(d => setFlags(d || []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [period])

  const types  = useMemo(() => [...new Set(flags.map(f => f.flag_type).filter(Boolean))].sort(), [flags])
  const reps   = useMemo(() => [...new Set(flags.map(f => f.epay_salesperson).filter(Boolean))].sort(), [flags])
  const stores = useMemo(() => [...new Set(flags.map(f => f.store_address).filter(Boolean))].sort(), [flags])

  const filtered = useMemo(() => {
    let rows = flags.filter(f => {
      if (fType && f.flag_type !== fType) return false
      if (fRep && f.epay_salesperson !== fRep) return false
      if (fStore && f.store_address !== fStore) return false
      if (fModel && !(f.phone_model || '').toLowerCase().includes(fModel.toLowerCase())) return false
      if (fSearch) {
        const q = fSearch.toLowerCase()
        const hay = `${f.mdn||''} ${f.imei||''} ${f.description||''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      if (fWindow) {
        const d = f.days_active == null ? null : Number(f.days_active)
        if (d == null || isNaN(d)) return false
        if (fWindow === '30' && !(d <= 30)) return false
        if (fWindow === '60' && !(d <= 60)) return false
        if (fWindow === '90' && !(d <= 90)) return false
        if (fWindow === '90+' && !(d > 90)) return false
       }
      return true
    })
    rows.sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey]
      if (av == null) av = sortDir === 'asc' ? Infinity : -Infinity
      if (bv == null) bv = sortDir === 'asc' ? Infinity : -Infinity
      if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv||'').toLowerCase() }
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return rows
  }, [flags, fType, fRep, fStore, fModel, fSearch, fWindow, sortKey, sortDir])

  const totalAtRisk = filtered.reduce((s, f) => s + Math.abs(f.amount || 0), 0)

  const summary = useMemo(() => {
    const m: Record<string, { count: number; amt: number }> = {}
    flags.forEach(f => {
      if (!m[f.flag_type]) m[f.flag_type] = { count: 0, amt: 0 }
      m[f.flag_type].count++
      m[f.flag_type].amt += Math.abs(f.amount || 0)
    })
    return Object.entries(m).sort((a, b) => b[1].count - a[1].count)
  }, [flags])

  function sortBy(key: string) {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('asc') }
  }

  function exportCSV() {
    const head = 'Flag Type,Severity,Days Active,Rep,Store,MDN,IMEI,Phone Model,Plan,Amount,Description'
    const rows = filtered.map(f => [
      f.flag_type, f.severity, f.days_active ?? '', f.epay_salesperson || '',
      `"${(f.store_address||'').replace(/"/g,'')}"`, f.mdn || '', f.imei || '',
      `"${(f.phone_model||'').replace(/"/g,'')}"`, `"${(f.customer_plan||'').replace(/"/g,'')}"`,
      f.amount || '', `"${(f.description||'').replace(/"/g,'').replace(/\n/g,' ')}"`,
    ].join(','))
    const a = document.createElement('a')
    a.href = 'data:text/csv,' + encodeURIComponent([head, ...rows].join('\n'))
    a.download = `flags-${period.replace(' ','-')}.csv`; a.click()
  }

  const TH = ({ k, label, align = 'left' }: { k: string; label: string; align?: string }) => (
    <th onClick={() => sortBy(k)} style={{
      padding: '10px 10px', fontSize: 11, fontWeight: 700, color: 'white', textAlign: align as any,
      cursor: 'pointer', whiteSpace: 'nowrap', position: 'sticky', top: 0, background: '#1e3a5f', zIndex: 2,
    }}>
      {label}{sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Flags & Compliance</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {filtered.length} of {flags.length} flags · At risk: <strong style={{ color: 'var(--red)' }}>{fmt(totalAtRisk)}</strong>
          </p>
        </div>
        <button className="btn btn-secondary" onClick={exportCSV}>📥 CSV</button>
      </div>

      {/* Store summary matrix */}
      {(() => {
        const storeRows: Record<string, Record<string, number>> = {}
        flags.forEach(f => {
          const st = f.store_address || 'Unknown'
          if (!storeRows[st]) storeRows[st] = {}
          storeRows[st][f.flag_type] = (storeRows[st][f.flag_type] || 0) + 1
        })
        const allTypes = [...new Set(flags.map(f => f.flag_type).filter(Boolean))].sort()
        const rows = Object.entries(storeRows)
          .map(([st, counts]) => ({ st, counts, total: Object.values(counts).reduce((a, b) => a + b, 0) }))
          .sort((a, b) => b.total - a.total)
        if (!rows.length) return null
        return (
          <div style={{ marginBottom: 20, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 12, background: 'white' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Store Summary — click a store to filter details below
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
              <thead>
                <tr>
                  <th style={{ padding: '8px 10px', fontSize: 10, fontWeight: 700, textAlign: 'left', position: 'sticky', left: 0, background: '#1e3a5f', color: 'white' }}>Store</th>
                  {allTypes.map(t => (
                    <th key={t} style={{ padding: '8px 6px', fontSize: 9, fontWeight: 700, textAlign: 'center', background: '#1e3a5f', color: 'white', whiteSpace: 'nowrap' }}>
                      {t.replace(/_/g, ' ')}
                    </th>
                  ))}
                  <th style={{ padding: '8px 10px', fontSize: 10, fontWeight: 700, textAlign: 'center', background: '#0f2540', color: 'white' }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ st, counts, total }, i) => (
                  <tr key={st} onClick={() => setFStore(fStore === st ? '' : st)}
                    style={{ cursor: 'pointer', background: fStore === st ? '#eff6ff' : i % 2 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '7px 10px', fontSize: 11, fontWeight: 600, position: 'sticky', left: 0, background: 'inherit' }}>{st.substring(0, 30)}</td>
                    {allTypes.map(t => (
                      <td key={t} style={{ padding: '7px 6px', fontSize: 12, textAlign: 'center', color: counts[t] ? 'var(--text)' : 'var(--text3)', fontWeight: counts[t] ? 700 : 400 }}>
                        {counts[t] || '·'}
                      </td>
                    ))}
                    <td style={{ padding: '7px 10px', fontSize: 12, textAlign: 'center', fontWeight: 700, background: '#f1f5f9' }}>{total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })()}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        {summary.map(([type, s]) => (
          <button key={type} onClick={() => setFType(fType === type ? '' : type)} className="card" style={{
            padding: '8px 14px', cursor: 'pointer', border: fType === type ? '2px solid var(--accent)' : '1px solid var(--border)',
            display: 'flex', flexDirection: 'column', gap: 2, minWidth: 120,
          }}>
            <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600 }}>{type.replace(/_/g, ' ')}</span>
            <span style={{ fontSize: 16, fontWeight: 700 }}>{s.count}</span>
            {s.amt > 0 && <span style={{ fontSize: 11, color: 'var(--red)' }}>{fmt(s.amt)}</span>}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        <select className="select" value={fType} onChange={e => setFType(e.target.value)}>
          <option value="">All types</option>
          {types.map(t => <option key={t} value={t}>{t.replace(/_/g,' ')}</option>)}
        </select>
        <select className="select" value={fRep} onChange={e => setFRep(e.target.value)}>
          <option value="">All reps</option>
          {reps.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <select className="select" value={fStore} onChange={e => setFStore(e.target.value)}>
          <option value="">All stores</option>
          {stores.map(s => <option key={s} value={s}>{s.substring(0,35)}</option>)}
        </select>
        <select className="select" value={fWindow} onChange={e => setFWindow(e.target.value)}>
          {WINDOWS.map(w => <option key={w.id} value={w.id}>{w.label}</option>)}
        </select>
        <input className="input" placeholder="Search MDN / IMEI…" value={fSearch}
          onChange={e => setFSearch(e.target.value)} style={{ width: 160 }} />
        <input className="input" placeholder="Phone model…" value={fModel}
          onChange={e => setFModel(e.target.value)} style={{ width: 140 }} />
        {(fType||fRep||fStore||fWindow||fSearch||fModel) && (
          <button className="btn btn-secondary" onClick={() => { setFType('');setFRep('');setFStore('');setFWindow('');setFSearch('');setFModel('') }}>✕ Clear</button>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 340px)', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100 }}>
            <thead>
              <tr>
                <TH k="flag_type" label="Flag Type" />
                <TH k="severity" label="Severity" />
                <TH k="days_active" label="Days Active" align="right" />
                <TH k="epay_salesperson" label="Rep" />
                <TH k="store_address" label="Store" />
                <TH k="mdn" label="MDN" />
                <TH k="imei" label="IMEI" />
                <TH k="phone_model" label="Phone Model" />
                <TH k="customer_plan" label="Plan" />
                <TH k="amount" label="Amount" align="right" />
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={10} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  {flags.length === 0 ? 'No flags — run calculation to generate' : 'No flags match filters'}
                </td></tr>
              ) : filtered.map((f, i) => (
                <tr key={i} style={{ background: i % 2 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 10px', fontSize: 12, fontWeight: 600 }}>{f.flag_type?.replace(/_/g,' ')}</td>
                  <td style={{ padding: '8px 10px' }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: SEVERITY_COLORS[f.severity] || '#64748b',
                      background: (SEVERITY_COLORS[f.severity] || '#64748b') + '18', padding: '2px 7px', borderRadius: 999 }}>
                      {f.severity}
                    </span>
                  </td>
                  <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600,
                    color: f.days_active != null && f.days_active <= 30 ? '#dc2626' : f.days_active != null && f.days_active <= 60 ? '#d97706' : 'var(--text)' }}>
                    {f.days_active != null ? f.days_active : '—'}
                  </td>
                  <td style={{ padding: '8px 10px', fontSize: 12 }}>{f.epay_salesperson || '—'}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11, color: 'var(--text3)' }}>{(f.store_address||'—').substring(0,28)}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11 }}>{f.mdn || '—'}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11, fontFamily: 'monospace' }}>{f.imei || '—'}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11 }}>{(f.phone_model||'—').substring(0,30)}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11, color: 'var(--text3)' }}>{(f.customer_plan||'—').substring(0,25)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600 }}>{f.amount ? fmt(Math.abs(f.amount)) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
    )
}