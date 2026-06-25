'use client'

import { useEffect, useState, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

type Row = {
  bucket: string; trans_id: string; store: string; salesperson: string; trans_date: string
  monthly_total: number | null; daily_total: number | null; delta: number
  monthly_lines: number; daily_lines: number
}
type StoreSummary = {
  store: string; missing_in_monthly: number; missing_in_daily: number
  amount_mismatch: number; delta_total: number
}
type Summary = {
  monthly_trans: number; daily_trans: number; monthly_lines: number; daily_lines: number
  monthly_total: number; daily_total: number; matched: number
  missing_in_monthly: number; missing_in_daily: number; amount_mismatch: number
  missing_in_monthly_total: number; missing_in_daily_total: number; mismatch_delta_total: number
}
type Resp = { period: string; has_feed: boolean; summary: Summary; by_store: StoreSummary[]; rows: Row[] }

const TABS = [
  { key: 'missing_in_monthly', label: 'Missing in Monthly', color: '#dc2626',
    blurb: "In the daily B2B feed but NOT in the authoritative monthly file — a real revenue / commission leak, or a same-day void. Investigate first." },
  { key: 'amount_mismatch', label: 'Amount Mismatch', color: '#d97706',
    blurb: 'Same transaction in both, but the totals differ (price change, partial line, or a tender/return delta).' },
  { key: 'missing_in_daily', label: 'Missing in Daily', color: '#2563eb',
    blurb: 'In the monthly file but the daily feed never captured it — usually a feed-coverage gap (feed down that day), lower severity.' },
]

export default function SalesReconPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [tab, setTab] = useState('missing_in_monthly')
  const [storeFilter, setStoreFilter] = useState('')
  const [search, setSearch] = useState('')

  const load = async () => {
    setLoading(true); setErr('')
    try {
      const r = await api(`/api/v1/commcalc/sales-recon?period=${encodeURIComponent(period)}&org_id=${ORG_ID}`)
      setData(r)
    } catch (e: any) { setErr(e.message || 'Failed to load') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [period])

  const stores = useMemo(() =>
    data ? Array.from(new Set(data.rows.map(r => r.store))).sort() : [], [data])

  const rows = useMemo(() => {
    if (!data) return []
    return data.rows.filter(r => {
      if (r.bucket !== tab) return false
      if (storeFilter && r.store !== storeFilter) return false
      if (search) {
        const s = search.toLowerCase()
        if (!r.trans_id?.toLowerCase().includes(s) && !r.store?.toLowerCase().includes(s) &&
            !r.salesperson?.toLowerCase().includes(s)) return false
      }
      return true
    })
  }, [data, tab, storeFilter, search])

  const s = data?.summary
  const tabMeta = TABS.find(t => t.key === tab)!
  const tabCount = (k: string) => (s ? (s as any)[k] as number : 0)
  const tabAmount =
    tab === 'missing_in_monthly' ? s?.missing_in_monthly_total
    : tab === 'missing_in_daily' ? s?.missing_in_daily_total
    : s?.mismatch_delta_total

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0 }}>Sales Feed Recon</h1>
          <p style={{ color: '#6b7280', margin: '4px 0 0', fontSize: 14 }}>
            Monthly authoritative upload vs the daily B2B feed — {period}
          </p>
        </div>
        <button onClick={load} disabled={loading}
          style={{ background: loading ? '#9ca3af' : '#111827', color: 'white', border: 'none', borderRadius: 8, padding: '10px 18px', fontWeight: 600, cursor: loading ? 'default' : 'pointer', fontSize: 14 }}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {err && <div style={{ background: '#fef2f2', color: '#991b1b', padding: 12, borderRadius: 8, marginBottom: 16, fontSize: 14 }}>{err}</div>}

      {data && !data.has_feed && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#92400e' }}>
          No daily B2B feed loaded for {period} yet. Once the daily feed lands (via FTP Auto-Import or a manual
          “daily_sales” upload), every transaction is reconciled here against the monthly file. The monthly totals
          below are shown for reference.
        </div>
      )}

      {/* Reconciliation totals */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>Monthly (authoritative)</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{fmt(s?.monthly_total || 0)}</div>
          <div style={{ fontSize: 12, color: '#9ca3af' }}>{s?.monthly_trans || 0} transactions · {s?.monthly_lines || 0} lines</div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>Daily B2B feed</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{fmt(s?.daily_total || 0)}</div>
          <div style={{ fontSize: 12, color: '#9ca3af' }}>{s?.daily_trans || 0} transactions · {s?.daily_lines || 0} lines</div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>Matched transactions</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: '#059669' }}>{s?.matched || 0}</div>
          <div style={{ fontSize: 12, color: '#9ca3af' }}>totals agree within $0.01</div>
        </div>
      </div>

      {/* Bucket tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {TABS.map(t => {
          const active = tab === t.key
          return (
            <button key={t.key} onClick={() => { setTab(t.key); setStoreFilter('') }}
              style={{ padding: '8px 16px', borderRadius: 8, fontSize: 14, fontWeight: 600,
                border: active ? `2px solid ${t.color}` : '1px solid #e5e7eb',
                background: active ? `${t.color}10` : 'white', color: active ? t.color : '#374151', cursor: 'pointer' }}>
              {t.label} <span style={{ opacity: 0.7 }}>({tabCount(t.key)})</span>
            </button>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>{tabMeta.label} — value at stake</div>
          <div style={{ fontSize: 26, fontWeight: 700, color: tabMeta.color }}>{fmt(Math.abs(tabAmount || 0))}</div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>Transactions</div>
          <div style={{ fontSize: 26, fontWeight: 700 }}>{rows.length}</div>
        </div>
      </div>

      <div style={{ background: `${tabMeta.color}08`, border: `1px solid ${tabMeta.color}30`, borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#374151' }}>
        {tabMeta.blurb}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <select value={storeFilter} onChange={e => setStoreFilter(e.target.value)}
          style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14, maxWidth: 260 }}>
          <option value="">All stores</option>
          {stores.map(st => <option key={st} value={st}>{st}</option>)}
        </select>
        <input placeholder="Search Trans ID / store / rep" value={search} onChange={e => setSearch(e.target.value)}
          style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14, flex: 1, minWidth: 200 }} />
        {(storeFilter || search) && (
          <button onClick={() => { setStoreFilter(''); setSearch('') }}
            style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', background: 'white', cursor: 'pointer', fontSize: 14 }}>Clear</button>
        )}
      </div>

      {loading && <div style={{ color: '#6b7280', padding: 20 }}>Loading…</div>}

      {!loading && data && (
        <>
          {data.by_store.length > 0 && (
            <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, marginBottom: 20, overflow: 'hidden' }}>
              <div style={{ padding: '12px 16px', borderBottom: '1px solid #e5e7eb', fontWeight: 600, fontSize: 14 }}>By Store (click to filter)</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                <thead><tr style={{ background: '#f9fafb', textAlign: 'left' }}>
                  <th style={{ padding: '8px 16px' }}>Store</th>
                  <th style={{ padding: '8px 16px', textAlign: 'right' }}>Missing in Monthly</th>
                  <th style={{ padding: '8px 16px', textAlign: 'right' }}>Mismatch</th>
                  <th style={{ padding: '8px 16px', textAlign: 'right' }}>Missing in Daily</th>
                  <th style={{ padding: '8px 16px', textAlign: 'right' }}>Net Δ</th>
                </tr></thead>
                <tbody>
                  {data.by_store.map(st => (
                    <tr key={st.store} onClick={() => setStoreFilter(storeFilter === st.store ? '' : st.store)}
                      style={{ borderTop: '1px solid #f3f4f6', cursor: 'pointer', background: storeFilter === st.store ? '#eff6ff' : 'white' }}>
                      <td style={{ padding: '8px 16px', fontWeight: 500 }}>{st.store}</td>
                      <td style={{ padding: '8px 16px', textAlign: 'right', color: st.missing_in_monthly ? '#dc2626' : '#9ca3af' }}>{st.missing_in_monthly}</td>
                      <td style={{ padding: '8px 16px', textAlign: 'right', color: st.amount_mismatch ? '#d97706' : '#9ca3af' }}>{st.amount_mismatch}</td>
                      <td style={{ padding: '8px 16px', textAlign: 'right', color: st.missing_in_daily ? '#2563eb' : '#9ca3af' }}>{st.missing_in_daily}</td>
                      <td style={{ padding: '8px 16px', textAlign: 'right', fontWeight: 600 }}>{fmt(st.delta_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr style={{ background: '#f9fafb', textAlign: 'left', position: 'sticky', top: 0 }}>
                <th style={{ padding: '8px 12px' }}>Trans ID</th>
                <th style={{ padding: '8px 12px' }}>Store</th>
                <th style={{ padding: '8px 12px' }}>Rep</th>
                <th style={{ padding: '8px 12px' }}>Date</th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>Monthly</th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>Daily</th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>Δ</th>
              </tr></thead>
              <tbody>
                {rows.slice(0, 500).map((r, i) => (
                  <tr key={`${r.trans_id}-${i}`} style={{ borderTop: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '6px 12px', fontFamily: 'monospace', fontSize: 12 }}>{r.trans_id}</td>
                    <td style={{ padding: '6px 12px', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.store}</td>
                    <td style={{ padding: '6px 12px' }}>{r.salesperson}</td>
                    <td style={{ padding: '6px 12px' }}>{r.trans_date}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right' }}>{r.monthly_total == null ? '—' : fmt(r.monthly_total)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right' }}>{r.daily_total == null ? '—' : fmt(r.daily_total)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600, color: tabMeta.color }}>{fmt(r.delta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 500 && (
              <div style={{ padding: 12, textAlign: 'center', color: '#6b7280', fontSize: 13 }}>
                Showing first 500 of {rows.length}. Use filters to narrow.
              </div>
            )}
            {rows.length === 0 && (
              <div style={{ padding: 20, textAlign: 'center', color: '#6b7280' }}>
                {data.has_feed ? 'No transactions in this bucket — clean.' : 'No daily feed loaded yet.'}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
