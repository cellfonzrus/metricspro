'use client'

import { useEffect, useState, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// "April 2026" -> "2026-04"
function toApiPeriod(label: string): string {
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December']
  const [mon, yr] = label.split(' ')
  const m = months.indexOf(mon) + 1
  return `${yr}-${String(m).padStart(2, '0')}`
}

type Row = {
  id: number
  imei: string
  mdn: string
  store: string
  rep_username: string
  activation_date: string
  activation_type: string
  device_model: string
  customer_plan: string
  commissionable_mrc: number
  bounty_month: number
  comp_type: string
  expected_amount: number
  received_amount: number
  gap: number
  status: string
}

type StoreSummary = {
  store: string
  total_gap: number
  flagged_count: number
  rows: Row[]
}

type DiscrepancyResp = {
  period: string
  summary: StoreSummary[]
  total_gap_usd: number
  total_flagged: number
}

const STATUS_TABS = [
  { key: 'open', label: 'Open (Receivable)', color: '#dc2626' },
  { key: 'lagged', label: 'Lagged (MI/ATU)', color: '#d97706' },
  { key: 'info', label: 'Info (SIMCR)', color: '#6b7280' },
]

export default function DiscrepancyPage() {
  const { period } = usePeriod()
  const apiPeriod = toApiPeriod(period)

  const [data, setData] = useState<DiscrepancyResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [err, setErr] = useState('')
  const [statusTab, setStatusTab] = useState('open')
  const [storeFilter, setStoreFilter] = useState('')
  const [compFilter, setCompFilter] = useState('')
  const [search, setSearch] = useState('')
  const [expandedStore, setExpandedStore] = useState<string | null>(null)

  const load = async () => {
    setLoading(true); setErr('')
    try {
      const r = await api(`/api/v1/commcalc/discrepancy/${apiPeriod}?org_id=${ORG_ID}`)
      setData(r)
    } catch (e: any) {
      setErr(e.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  const runDetection = async () => {
    setRunning(true); setErr('')
    try {
      await api('/api/v1/commcalc/discrepancy/run', {
        method: 'POST',
        body: JSON.stringify({ period: apiPeriod }),
      })
      await load()
    } catch (e: any) {
      setErr(e.message || 'Run failed')
    } finally {
      setRunning(false)
    }
  }

  useEffect(() => { load() }, [apiPeriod])

  // Flatten all rows for filtering
  const allRows = useMemo(() => {
    if (!data) return []
    return data.summary.flatMap(s => s.rows)
  }, [data])

  const compTypes = useMemo(() => {
    const set = new Set(allRows.map(r => r.comp_type))
    return Array.from(set).sort()
  }, [allRows])

  // Filtered rows by status tab + filters
  const filteredRows = useMemo(() => {
    return allRows.filter(r => {
      if (r.status !== statusTab) return false
      if (storeFilter && r.store !== storeFilter) return false
      if (compFilter && r.comp_type !== compFilter) return false
      if (search) {
        const s = search.toLowerCase()
        if (!r.imei?.toLowerCase().includes(s) &&
            !r.mdn?.toLowerCase().includes(s) &&
            !r.device_model?.toLowerCase().includes(s) &&
            !r.rep_username?.toLowerCase().includes(s)) return false
      }
      return true
    })
  }, [allRows, statusTab, storeFilter, compFilter, search])

  // Store summary for current status tab
  const storeSummary = useMemo(() => {
    const map: Record<string, { store: string; gap: number; count: number }> = {}
    allRows.filter(r => r.status === statusTab).forEach(r => {
      if (!map[r.store]) map[r.store] = { store: r.store, gap: 0, count: 0 }
      map[r.store].gap += r.gap
      map[r.store].count += 1
    })
    return Object.values(map).sort((a, b) => b.gap - a.gap)
  }, [allRows, statusTab])

  const tabTotal = useMemo(() =>
    allRows.filter(r => r.status === statusTab).reduce((s, r) => s + r.gap, 0),
  [allRows, statusTab])

  const stores = useMemo(() => {
    const set = new Set(allRows.map(r => r.store))
    return Array.from(set).sort()
  }, [allRows])

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0 }}>Pay Discrepancy</h1>
          <p style={{ color: '#6b7280', margin: '4px 0 0', fontSize: 14 }}>
            Bounties earned vs paid by Boost — {period}
          </p>
        </div>
        <button
          onClick={runDetection}
          disabled={running}
          style={{
            background: running ? '#9ca3af' : '#111827', color: 'white',
            border: 'none', borderRadius: 8, padding: '10px 18px',
            fontWeight: 600, cursor: running ? 'default' : 'pointer', fontSize: 14,
          }}
        >
          {running ? 'Running…' : 'Run Detection'}
        </button>
      </div>

      {err && (
        <div style={{ background: '#fef2f2', color: '#991b1b', padding: 12, borderRadius: 8, marginBottom: 16, fontSize: 14 }}>
          {err}
        </div>
      )}

      {/* Status tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {STATUS_TABS.map(t => {
          const cnt = allRows.filter(r => r.status === t.key).length
          const active = statusTab === t.key
          return (
            <button
              key={t.key}
              onClick={() => { setStatusTab(t.key); setExpandedStore(null) }}
              style={{
                padding: '8px 16px', borderRadius: 8, fontSize: 14, fontWeight: 600,
                border: active ? `2px solid ${t.color}` : '1px solid #e5e7eb',
                background: active ? `${t.color}10` : 'white',
                color: active ? t.color : '#374151', cursor: 'pointer',
              }}
            >
              {t.label} <span style={{ opacity: 0.7 }}>({cnt})</span>
            </button>
          )
        })}
      </div>

      {/* Headline */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 20 }}>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>
            {statusTab === 'open' ? 'Total Receivable from Boost' : statusTab === 'lagged' ? 'Lagged (awaiting prior-period data)' : 'Informational total'}
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: STATUS_TABS.find(t => t.key === statusTab)?.color }}>
            {fmt(tabTotal)}
          </div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>Line items</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>{filteredRows.length}</div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>Stores affected</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>{storeSummary.length}</div>
        </div>
      </div>

      {loading && <div style={{ color: '#6b7280', padding: 20 }}>Loading…</div>}

      {!loading && !data && (
        <div style={{ color: '#6b7280', padding: 20 }}>No results yet. Click Run Detection.</div>
      )}

      {!loading && data && (
        <>
          {/* Store summary matrix */}
          <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, marginBottom: 20, overflow: 'hidden' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #e5e7eb', fontWeight: 600, fontSize: 14 }}>
              By Store {statusTab === 'open' ? '(click to drill down)' : ''}
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
              <thead>
                <tr style={{ background: '#f9fafb', textAlign: 'left' }}>
                  <th style={{ padding: '8px 16px' }}>Store</th>
                  <th style={{ padding: '8px 16px', textAlign: 'right' }}>Line Items</th>
                  <th style={{ padding: '8px 16px', textAlign: 'right' }}>Gap</th>
                </tr>
              </thead>
              <tbody>
                {storeSummary.map(s => (
                  <tr
                    key={s.store}
                    onClick={() => { setStoreFilter(storeFilter === s.store ? '' : s.store); setExpandedStore(s.store) }}
                    style={{
                      borderTop: '1px solid #f3f4f6', cursor: 'pointer',
                      background: storeFilter === s.store ? '#eff6ff' : 'white',
                    }}
                  >
                    <td style={{ padding: '8px 16px', fontWeight: 500 }}>{s.store}</td>
                    <td style={{ padding: '8px 16px', textAlign: 'right' }}>{s.count}</td>
                    <td style={{ padding: '8px 16px', textAlign: 'right', fontWeight: 600 }}>{fmt(s.gap)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Filters */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <select value={storeFilter} onChange={e => setStoreFilter(e.target.value)}
              style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14 }}>
              <option value="">All stores</option>
              {stores.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={compFilter} onChange={e => setCompFilter(e.target.value)}
              style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14 }}>
              <option value="">All bounty types</option>
              {compTypes.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <input
              placeholder="Search IMEI / MDN / device / rep"
              value={search} onChange={e => setSearch(e.target.value)}
              style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14, flex: 1, minWidth: 200 }}
            />
            {(storeFilter || compFilter || search) && (
              <button onClick={() => { setStoreFilter(''); setCompFilter(''); setSearch('') }}
                style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', background: 'white', cursor: 'pointer', fontSize: 14 }}>
                Clear
              </button>
            )}
          </div>

          {/* Detail table */}
          <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#f9fafb', textAlign: 'left', position: 'sticky', top: 0 }}>
                  <th style={{ padding: '8px 12px' }}>Store</th>
                  <th style={{ padding: '8px 12px' }}>Type</th>
                  <th style={{ padding: '8px 12px', textAlign: 'center' }}>Mo</th>
                  <th style={{ padding: '8px 12px' }}>IMEI</th>
                  <th style={{ padding: '8px 12px' }}>MDN</th>
                  <th style={{ padding: '8px 12px' }}>Device</th>
                  <th style={{ padding: '8px 12px' }}>Plan</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right' }}>MRC</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right' }}>Expected</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right' }}>Received</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right' }}>Gap</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.slice(0, 500).map(r => (
                  <tr key={r.id} style={{ borderTop: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '6px 12px' }}>{r.store}</td>
                    <td style={{ padding: '6px 12px', fontWeight: 600 }}>{r.comp_type}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'center' }}>{r.bounty_month}</td>
                    <td style={{ padding: '6px 12px', fontFamily: 'monospace', fontSize: 12 }}>{r.imei}</td>
                    <td style={{ padding: '6px 12px' }}>{r.mdn}</td>
                    <td style={{ padding: '6px 12px', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.device_model}</td>
                    <td style={{ padding: '6px 12px', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.customer_plan}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right' }}>{fmt(r.commissionable_mrc)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right' }}>{fmt(r.expected_amount)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right' }}>{fmt(r.received_amount)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600, color: r.gap > 0.5 ? '#dc2626' : '#6b7280' }}>{fmt(r.gap)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredRows.length > 500 && (
              <div style={{ padding: 12, textAlign: 'center', color: '#6b7280', fontSize: 13 }}>
                Showing first 500 of {filteredRows.length} rows. Use filters to narrow.
              </div>
            )}
            {filteredRows.length === 0 && (
              <div style={{ padding: 20, textAlign: 'center', color: '#6b7280' }}>No rows match.</div>
            )}
          </div>
        </>
      )}
    </div>
  )
}