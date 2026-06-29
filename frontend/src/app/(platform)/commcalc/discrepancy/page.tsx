'use client'

import { useEffect, useState, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { SendReportButton } from '@/lib/send-report'

function toApiPeriod(label: string): string {
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December']
  const [mon, yr] = label.split(' ')
  const m = months.indexOf(mon) + 1
  return `${yr}-${String(m).padStart(2, '0')}`
}

const COMP_DESCRIPTIONS: Record<string, string> = {
  NAB: 'New Activation Bounty — 20% of MRC, months 1–6, for new activations & port-ins',
  SSLB: 'Simplified SIM Loading Bounty — 10% of MRC, months 1–6, recommended SIM',
  BRB: 'Boost Ready Bounty — 5% of MRC, months 1–6, requires same-day app login',
  DUPGB: 'Device Upgrade Bounty — 10% of MRC, months 1–6, eligible/swap upgrades',
  ISDFB: 'In-Store Device Financing Bounty — 20% of MRC, months 1–6, DevFi in-store',
  MI: 'Monthly Incentive — 10% of MRC, ongoing retention (2-month payment lag)',
  ATUMI: 'Auto Top-Up Monthly Incentive — 5% of MRC, ongoing, ATU enrolled (2-month lag)',
  SIMCR: 'SIM Card Reimbursement — $2.50 flat per physical SIM activation',
  SOLD_NOT_IN_MI: 'Activation rung in store but no matching subscriber in MI report — review',
  DEVICE_REIMB: 'Device Reimbursement — SRP minus promo price (handled in Asset module)',
}

type Row = {
  id: number; imei: string; mdn: string; store: string; rep_username: string
  activation_date: string; activation_type: string; device_model: string
  customer_plan: string; commissionable_mrc: number; bounty_month: number
  comp_type: string; expected_amount: number; received_amount: number; gap: number; status: string
}
type StoreSummary = { store: string; total_gap: number; flagged_count: number; rows: Row[] }
type DiscrepancyResp = { period: string; summary: StoreSummary[]; total_gap_usd: number; total_flagged: number }
type PhantomRow = { mdn: string; imei: string; payment_type: string; amount: number; business_address: string; payment_date: string }
type PhantomStore = { business_address: string; total: number; count: number; rows: PhantomRow[] }
type PhantomResp = { period: string; phantom_total: number; phantom_count: number; matched_total: number; by_store: PhantomStore[] }

const STATUS_TABS = [
  { key: 'open', label: 'Open (Receivable)', color: '#dc2626' },
  { key: 'pending', label: 'Pending (recent)', color: '#2563eb' },
  { key: 'lagged', label: 'Lagged (MI/ATU)', color: '#d97706' },
  { key: 'info', label: 'Info (SIMCR)', color: '#6b7280' },
  { key: 'phantom', label: 'Phantom (paid, no sale)', color: '#7c3aed' },
]

export default function DiscrepancyPage() {
  const { period } = usePeriod()
  const apiPeriod = toApiPeriod(period)

  const [data, setData] = useState<DiscrepancyResp | null>(null)
  const [phantom, setPhantom] = useState<PhantomResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [err, setErr] = useState('')
  const [statusTab, setStatusTab] = useState('open')
  const [storeFilter, setStoreFilter] = useState('')
  const [compFilter, setCompFilter] = useState('')
  const [search, setSearch] = useState('')
  const [showLegend, setShowLegend] = useState(false)

  const load = async () => {
    setLoading(true); setErr('')
    try {
      const [r, ph] = await Promise.all([
        api(`/api/v1/commcalc/discrepancy/${apiPeriod}?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/discrepancy/${apiPeriod}/phantom?org_id=${ORG_ID}`).catch(() => null),
      ])
      setData(r); setPhantom(ph)
    } catch (e: any) {
      setErr(e.message || 'Failed to load')
    } finally { setLoading(false) }
  }

  const runDetection = async () => {
    setRunning(true); setErr('')
    try {
      await api('/api/v1/commcalc/discrepancy/run', { method: 'POST', body: JSON.stringify({ period: apiPeriod }) })
      await load()
    } catch (e: any) { setErr(e.message || 'Run failed') }
    finally { setRunning(false) }
  }

  useEffect(() => { load() }, [apiPeriod])

  const allRows = useMemo(() => data ? data.summary.flatMap(s => s.rows) : [], [data])
  const compTypes = useMemo(() => Array.from(new Set(allRows.map(r => r.comp_type))).sort(), [allRows])
  const stores = useMemo(() => Array.from(new Set(allRows.map(r => r.store))).sort(), [allRows])

  const isPhantom = statusTab === 'phantom'

  const filteredRows = useMemo(() => allRows.filter(r => {
    if (r.status !== statusTab) return false
    if (storeFilter && r.store !== storeFilter) return false
    if (compFilter && r.comp_type !== compFilter) return false
    if (search) {
      const s = search.toLowerCase()
      if (!r.imei?.toLowerCase().includes(s) && !r.mdn?.toLowerCase().includes(s) &&
          !r.device_model?.toLowerCase().includes(s) && !r.rep_username?.toLowerCase().includes(s)) return false
    }
    return true
  }), [allRows, statusTab, storeFilter, compFilter, search])

  const phantomStores = useMemo(() =>
    phantom ? phantom.by_store.map(s => s.business_address).sort() : [],
  [phantom])

  const phantomRows = useMemo(() => {
    if (!phantom) return []
    let rows = phantom.by_store.flatMap(s => s.rows)
    if (storeFilter) rows = rows.filter(r => r.business_address === storeFilter)
    if (search) {
      const s = search.toLowerCase()
      rows = rows.filter(r => r.imei?.toLowerCase().includes(s) || r.mdn?.toLowerCase().includes(s) ||
        (r.payment_type || '').toLowerCase().includes(s) || (r.business_address || '').toLowerCase().includes(s))
    }
    if (compFilter) rows = rows.filter(r => r.payment_type === compFilter)
    return rows.sort((a, b) => b.amount - a.amount)
  }, [phantom, search, compFilter, storeFilter])

  const phantomTypes = useMemo(() => {
    if (!phantom) return []
    return Array.from(new Set(phantom.by_store.flatMap(s => s.rows.map(r => r.payment_type)))).sort()
  }, [phantom])

  const storeSummary = useMemo(() => {
    if (isPhantom) {
      return phantom ? phantom.by_store.map(s => ({ store: s.business_address, gap: s.total, count: s.count })) : []
    }
    const map: Record<string, { store: string; gap: number; count: number }> = {}
    allRows.filter(r => r.status === statusTab).forEach(r => {
      if (!map[r.store]) map[r.store] = { store: r.store, gap: 0, count: 0 }
      map[r.store].gap += r.gap; map[r.store].count += 1
    })
    return Object.values(map).sort((a, b) => b.gap - a.gap)
  }, [allRows, statusTab, isPhantom, phantom])

  const tabTotal = useMemo(() =>
    allRows.filter(r => r.status === statusTab).reduce((s, r) => s + r.gap, 0),
  [allRows, statusTab])

  const tabCount = (key: string) => {
    if (key === 'phantom') return phantom?.phantom_count || 0
    return allRows.filter(r => r.status === key).length
  }

  const headlineColor = STATUS_TABS.find(t => t.key === statusTab)?.color
  const filterStores = isPhantom ? phantomStores : stores
  const filterTypes = isPhantom ? phantomTypes : compTypes

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0 }}>Pay Discrepancy</h1>
          <p style={{ color: '#6b7280', margin: '4px 0 0', fontSize: 14 }}>Bounties earned vs paid by the Carrier — {period}</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowLegend(!showLegend)}
            style={{ background: 'white', color: '#374151', border: '1px solid #e5e7eb', borderRadius: 8, padding: '10px 16px', fontWeight: 600, cursor: 'pointer', fontSize: 14 }}>
            {showLegend ? 'Hide' : 'What do these mean?'}
          </button>
          <button onClick={runDetection} disabled={running}
            style={{ background: running ? '#9ca3af' : '#111827', color: 'white', border: 'none', borderRadius: 8, padding: '10px 18px', fontWeight: 600, cursor: running ? 'default' : 'pointer', fontSize: 14 }}>
            {running ? 'Running…' : 'Run Detection'}
          </button>
          <SendReportButton reportKey="discrepancy" filters={{ period }} />
        </div>
      </div>

      {showLegend && (
        <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>Commission Types</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 24px', fontSize: 13 }}>
            {Object.entries(COMP_DESCRIPTIONS).map(([k, v]) => (
              <div key={k}><strong style={{ color: '#111827' }}>{k}</strong> — <span style={{ color: '#6b7280' }}>{v.split('—')[1]}</span></div>
            ))}
          </div>
        </div>
      )}

      {err && <div style={{ background: '#fef2f2', color: '#991b1b', padding: 12, borderRadius: 8, marginBottom: 16, fontSize: 14 }}>{err}</div>}

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        {STATUS_TABS.map(t => {
          const active = statusTab === t.key
          return (
            <button key={t.key} onClick={() => { setStatusTab(t.key); setStoreFilter(''); setCompFilter('') }}
              style={{ padding: '8px 16px', borderRadius: 8, fontSize: 14, fontWeight: 600,
                border: active ? `2px solid ${t.color}` : '1px solid #e5e7eb',
                background: active ? `${t.color}10` : 'white', color: active ? t.color : '#374151', cursor: 'pointer' }}>
              {t.label} <span style={{ opacity: 0.7 }}>({tabCount(t.key)})</span>
            </button>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: 16, marginBottom: 20 }}>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>
            {statusTab === 'open' ? 'Total Receivable from Carrier'
              : statusTab === 'pending' ? 'Pending (too recent to reconcile)'
              : statusTab === 'lagged' ? 'Lagged (awaiting prior-period data)'
              : statusTab === 'phantom' ? 'Paid by Carrier — no matching sale'
              : 'Informational total'}
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: headlineColor }}>
            {isPhantom ? fmt(phantom?.phantom_total || 0) : fmt(tabTotal)}
          </div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>Line items</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>{isPhantom ? (phantom?.phantom_count || 0) : filteredRows.length}</div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, flex: 1 }}>
          <div style={{ fontSize: 13, color: '#6b7280' }}>{isPhantom ? 'Stores' : 'Stores affected'}</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>{isPhantom ? (phantom?.by_store.length || 0) : storeSummary.length}</div>
        </div>
      </div>

      {isPhantom && (
        <div style={{ background: '#faf5ff', border: '1px solid #e9d5ff', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#6b21a8' }}>
          These payments don't match any sale or subscriber record. Large items (esp. device reimbursements) are worth investigating — small bounties are often data-coverage gaps from stores whose sales aren't loaded yet.
        </div>
      )}

      {loading && <div style={{ color: '#6b7280', padding: 20 }}>Loading…</div>}
      {!loading && !data && <div style={{ color: '#6b7280', padding: 20 }}>No results yet. Click Run Detection.</div>}

      {!loading && data && (
        <>
          <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, marginBottom: 20, overflow: 'hidden' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #e5e7eb', fontWeight: 600, fontSize: 14 }}>By Store (click to filter)</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
              <thead><tr style={{ background: '#f9fafb', textAlign: 'left' }}>
                <th style={{ padding: '8px 16px' }}>Store</th>
                <th style={{ padding: '8px 16px', textAlign: 'right' }}>Line Items</th>
                <th style={{ padding: '8px 16px', textAlign: 'right' }}>{statusTab === 'open' ? 'Gap' : 'Amount'}</th>
              </tr></thead>
              <tbody>
                {storeSummary.map(s => (
                  <tr key={s.store} onClick={() => setStoreFilter(storeFilter === s.store ? '' : s.store)}
                    style={{ borderTop: '1px solid #f3f4f6', cursor: 'pointer', background: storeFilter === s.store ? '#eff6ff' : 'white' }}>
                    <td style={{ padding: '8px 16px', fontWeight: 500 }}>{s.store}</td>
                    <td style={{ padding: '8px 16px', textAlign: 'right' }}>{s.count}</td>
                    <td style={{ padding: '8px 16px', textAlign: 'right', fontWeight: 600 }}>{fmt(s.gap)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <select value={storeFilter} onChange={e => setStoreFilter(e.target.value)}
              style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14, maxWidth: 260 }}>
              <option value="">All stores</option>
              {filterStores.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={compFilter} onChange={e => setCompFilter(e.target.value)}
              style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14 }}>
              <option value="">All types</option>
              {filterTypes.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <input placeholder="Search IMEI / MDN / device / rep" value={search} onChange={e => setSearch(e.target.value)}
              style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 14, flex: 1, minWidth: 200 }} />
            {(storeFilter || compFilter || search) && (
              <button onClick={() => { setStoreFilter(''); setCompFilter(''); setSearch('') }}
                style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e5e7eb', background: 'white', cursor: 'pointer', fontSize: 14 }}>Clear</button>
            )}
          </div>

          <div style={{ background: 'white', border: '1px solid #e5e7eb', borderRadius: 12, overflow: 'auto' }}>
            {isPhantom ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead><tr style={{ background: '#f9fafb', textAlign: 'left', position: 'sticky', top: 0 }}>
                  <th style={{ padding: '8px 12px' }}>Store Address</th>
                  <th style={{ padding: '8px 12px' }}>Payment Type</th>
                  <th style={{ padding: '8px 12px' }}>IMEI</th>
                  <th style={{ padding: '8px 12px' }}>MDN</th>
                  <th style={{ padding: '8px 12px' }}>Date</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right' }}>Amount</th>
                </tr></thead>
                <tbody>
                  {phantomRows.slice(0, 500).map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid #f3f4f6' }}>
                      <td style={{ padding: '6px 12px', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.business_address}</td>
                      <td style={{ padding: '6px 12px', fontWeight: 500 }}>{r.payment_type}</td>
                      <td style={{ padding: '6px 12px', fontFamily: 'monospace', fontSize: 12 }}>{r.imei}</td>
                      <td style={{ padding: '6px 12px' }}>{r.mdn}</td>
                      <td style={{ padding: '6px 12px' }}>{r.payment_date}</td>
                      <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600, color: r.amount > 100 ? '#7c3aed' : '#374151' }}>{fmt(r.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead><tr style={{ background: '#f9fafb', textAlign: 'left', position: 'sticky', top: 0 }}>
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
                </tr></thead>
                <tbody>
                  {filteredRows.slice(0, 500).map(r => (
                    <tr key={r.id} style={{ borderTop: '1px solid #f3f4f6' }}>
                      <td style={{ padding: '6px 12px' }}>{r.store}</td>
                      <td style={{ padding: '6px 12px', fontWeight: 600 }} title={COMP_DESCRIPTIONS[r.comp_type] || ''}>{r.comp_type}</td>
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
            )}
            {((isPhantom ? phantomRows : filteredRows).length > 500) && (
              <div style={{ padding: 12, textAlign: 'center', color: '#6b7280', fontSize: 13 }}>
                Showing first 500 of {(isPhantom ? phantomRows : filteredRows).length} rows. Use filters to narrow.
              </div>
            )}
            {(isPhantom ? phantomRows : filteredRows).length === 0 && (
              <div style={{ padding: 20, textAlign: 'center', color: '#6b7280' }}>No rows match.</div>
            )}
          </div>
        </>
      )}
    </div>
  )
}