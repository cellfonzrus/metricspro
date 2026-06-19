'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'

type Bucket = 'matched' | 'underpaid' | 'overpaid' | 'no_expected' | 'no_hotsheet' | 'unmapped_type'
type Item = {
  store: string | null; market: string | null; imei: string | null; mdn: string | null
  device_model: string; contract_type: string; promo_type: string | null
  acquired_date: string | null; effective_date: string | null
  actual: number; expected: number | null; variance: number | null; bucket: Bucket
}
type SumCell = { count: number; expected: number; actual: number; variance: number }
type ByType = { promo_type: string; count: number; expected: number; actual: number; variance: number; underpaid_count: number }
type Data = {
  hotsheet_loaded: boolean; tolerance: number; device_count: number; skipped_unactivated: number
  summary: Record<Bucket, SumCell>; underpaid_total: number; overpaid_total: number
  by_type: ByType[]; items: Item[]
  unmatched_models: { device_model: string; count: number }[]
  unmapped_contract_types: { contract_type: string; count: number }[]
}

const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December']
const BUCKET_LABEL: Record<Bucket, string> = {
  matched: 'Matched', underpaid: 'Underpaid', overpaid: 'Overpaid',
  no_expected: 'No expected $', no_hotsheet: 'Not on hotsheet', unmapped_type: 'Unmapped type',
}
const BUCKET_COLOR: Record<Bucket, string> = {
  matched: '#059669', underpaid: '#dc2626', overpaid: '#2563eb',
  no_expected: '#d97706', no_hotsheet: '#6b7280', unmapped_type: '#6b7280',
}

export default function HotsheetReconPage() {
  const [market, setMarket] = useState('')
  const [store, setStore] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{ store: string; market: string }[]>([])
  const [month, setMonth] = useState(0)
  const [year, setYear] = useState(new Date().getFullYear())
  const [tolerance, setTolerance] = useState(1)
  const [bucket, setBucket] = useState<Bucket>('underpaid')
  const [data, setData] = useState<Data | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncMsg, setSyncMsg] = useState('')
  const [syncing, setSyncing] = useState(false)

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d: any) => { setMarkets(d.markets || []); setStores(d.stores || []) })
      .catch(console.error)
  }, [])
  useEffect(() => { load() }, [market, store, month, year, tolerance])

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID, tolerance: String(tolerance) })
      if (market) qs.set('market', market)
      if (store) qs.set('store', store)
      if (month) { qs.set('month', String(month)); qs.set('year', String(year)) }
      setData(await api(`/api/v1/asset/hotsheet-recon?${qs.toString()}`))
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  async function syncFlags() {
    if (!confirm('Write a flag for every underpaid device (Boost reimbursed less than the hotsheet promo)? This replaces the previous hotsheet flags.')) return
    setSyncing(true); setSyncMsg('')
    try {
      const r = await api(`/api/v1/asset/sync-hotsheet-flags?org_id=${ORG_ID}&tolerance=${tolerance}`, { method: 'POST' })
      setSyncMsg(`✓ Wrote ${r.flags_written} flag(s) to Flags & Compliance (source: asset_hotsheet).`)
    } catch (e: any) { setSyncMsg(`✕ ${e?.message || 'sync failed'}`) }
    setSyncing(false)
  }

  const visibleStores = market ? stores.filter(s => s.market === market) : stores
  const selStyle = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const items = (data?.items || []).filter(i => i.bucket === bucket)

  function buildPayload(): ExportPayload {
    const cols: ExportColumn[] = [
      { header: 'Store', get: r => r.store },
      { header: 'Market', get: r => r.market },
      { header: 'Device Model', get: r => r.device_model },
      { header: 'IMEI/ESN', get: r => r.imei },
      { header: 'Contract Type', get: r => r.contract_type },
      { header: 'Promo Type', get: r => r.promo_type },
      { header: 'Acquired', get: r => r.acquired_date },
      { header: 'Hotsheet Eff.', get: r => r.effective_date },
      { header: 'Expected', get: r => r.expected, money: true },
      { header: 'Reimbursed', get: r => r.actual, money: true },
      { header: 'Variance', get: r => r.variance, money: true },
    ]
    const filterLabel = [market || null, store || null].filter(Boolean).join(' · ') || 'All markets'
    const periodLabel = month ? `${MONTHS[month - 1]} ${year}` : 'All time'
    return {
      title: `Hotsheet Recon — ${BUCKET_LABEL[bucket]}`,
      subtitle: `${filterLabel} · acquired ${periodLabel} · tolerance ${fmt(tolerance)}`,
      filename: `hotsheet-recon-${bucket}`,
      sheets: [{ name: BUCKET_LABEL[bucket], rows: items, columns: cols }],
    }
  }

  const card = (label: string, val: string, sub: string, color: string) => (
    <div key={label} className="card" style={{ padding: '16px 20px', borderTop: `3px solid ${color}` }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color, marginTop: 6 }}>{val}</div>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>Hotsheet Recon — Expected vs Paid</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            For each activated device, the <strong>expected</strong> promo from the pricing hotsheet (by device model + the
            hotsheet effective as of its acquired date, on the column chosen from the contract type:
            Upgrade &gt; AAL &gt; Port-In &gt; Non-Port) vs the <strong>actual</strong> Boost reimbursement. Unsold On-Inventory is excluded.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {data && data.summary.underpaid.count > 0 && (
            <button className="btn" onClick={syncFlags} disabled={syncing} title="Write underpaid devices to Flags & Compliance">
              {syncing ? 'Syncing…' : '🚩 Sync underpaid → Flags'}
            </button>
          )}
          {data && items.length > 0 && <ExportButtons payload={buildPayload} />}
        </div>
      </div>

      {syncMsg && <div style={{ marginBottom: 14, fontSize: 13, color: syncMsg.startsWith('✓') ? '#059669' : '#dc2626' }}>{syncMsg}</div>}

      {data && !data.hotsheet_loaded && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: '12px 16px', marginBottom: 16, fontSize: 13, color: '#92400e' }}>
          ⚠️ <strong>No hotsheet loaded.</strong> Upload a pricing hotsheet on the Commissions page first — every device will read “Not on hotsheet” until then.
        </div>
      )}

      {/* Filters */}
      <div className="card" style={{ padding: 14, marginBottom: 20, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>Filters:</span>
        <select style={selStyle} value={market} onChange={e => { setMarket(e.target.value); setStore('') }}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={selStyle} value={store} onChange={e => setStore(e.target.value)}>
          <option value="">All stores</option>
          {visibleStores.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
        </select>
        <select style={selStyle} value={month} onChange={e => setMonth(+e.target.value)} title="Acquired in">
          <option value={0}>All time</option>
          {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
        </select>
        {month > 0 && (
          <select style={selStyle} value={year} onChange={e => setYear(+e.target.value)}>
            {[2024, 2025, 2026].map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        )}
        <label style={{ fontSize: 13, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Tolerance $
          <input type="number" min={0} step={0.5} value={tolerance} onChange={e => setTolerance(Math.max(0, +e.target.value || 0))}
            style={{ ...selStyle, width: 70 }} />
        </label>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !data ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>No data.</div>
      ) : (
        <>
          {/* Summary cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 14, marginBottom: 22 }}>
            {card('Underpaid', fmt(data.underpaid_total), `${data.summary.underpaid.count} devices short`, '#dc2626')}
            {card('Overpaid', fmt(data.overpaid_total), `${data.summary.overpaid.count} devices over`, '#2563eb')}
            {card('Matched', data.summary.matched.count.toLocaleString(), `within ${fmt(data.tolerance)}`, '#059669')}
            {card('Not on hotsheet', (data.summary.no_hotsheet.count + data.summary.no_expected.count).toLocaleString(), `${data.unmatched_models.length} models unmatched`, '#6b7280')}
            {card('Unmapped type', data.summary.unmapped_type.count.toLocaleString(), `${data.unmapped_contract_types.length} contract types`, '#6b7280')}
          </div>

          {/* By promo type */}
          {data.by_type.length > 0 && (
            <div className="card" style={{ padding: 0, marginBottom: 22 }}>
              <div style={{ padding: '10px 14px', fontSize: 12, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase' }}>By promo type</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Promo Type', 'Devices', 'Expected', 'Reimbursed', 'Variance', 'Underpaid'].map((h, i) => (
                      <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {data.by_type.map(t => (
                      <tr key={t.promo_type} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>{t.promo_type}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{t.count.toLocaleString()}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(t.expected)}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(t.actual)}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', fontWeight: 700, color: t.variance < 0 ? '#dc2626' : 'var(--text)' }}>{fmt(t.variance)}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: t.underpaid_count ? '#dc2626' : 'var(--text3)' }}>{t.underpaid_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Line items by bucket */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '12px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>Devices:</span>
              <select style={selStyle} value={bucket} onChange={e => setBucket(e.target.value as Bucket)}>
                {(Object.keys(BUCKET_LABEL) as Bucket[]).map(b => (
                  <option key={b} value={b}>{BUCKET_LABEL[b]} ({data.summary[b].count})</option>
                ))}
              </select>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{items.length.toLocaleString()} shown</span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1000 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Store', 'Device', 'IMEI/ESN', 'Contract Type', 'Promo', 'Acquired', 'Hotsheet Eff.', 'Expected', 'Reimbursed', 'Variance'].map((h, i) => (
                    <th key={h} style={{ textAlign: i >= 7 ? 'right' : 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {items.slice(0, 1000).map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                      <td style={{ padding: '7px 12px', fontSize: 12 }}>{r.store || '—'}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12 }}>{r.device_model || '—'}</td>
                      <td style={{ padding: '7px 12px', fontSize: 11, color: 'var(--text2)', fontFamily: 'monospace' }}>{r.imei || '—'}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text2)' }}>{r.contract_type || '—'}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12 }}>{r.promo_type || '—'}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text2)' }}>{r.acquired_date || '—'}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text3)' }}>{r.effective_date || '—'}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, textAlign: 'right' }}>{r.expected == null ? '—' : fmt(r.expected)}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(r.actual)}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, textAlign: 'right', fontWeight: 700, color: r.variance != null && r.variance < 0 ? '#dc2626' : r.variance != null && r.variance > 0 ? '#2563eb' : 'var(--text3)' }}>{r.variance == null ? '—' : fmt(r.variance)}</td>
                    </tr>
                  ))}
                  {!items.length && <tr><td colSpan={10} style={{ padding: 30, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No devices in “{BUCKET_LABEL[bucket]}”.</td></tr>}
                </tbody>
              </table>
            </div>
            {items.length > 1000 && <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--text3)' }}>Showing first 1,000 of {items.length.toLocaleString()} — export for the full list.</div>}
          </div>

          {/* Data-quality panels: align the hotsheet / mapping */}
          {(data.unmatched_models.length > 0 || data.unmapped_contract_types.length > 0) && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 22 }}>
              {data.unmatched_models.length > 0 && (
                <div className="card" style={{ padding: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', marginBottom: 8 }}>Device models not on any hotsheet</div>
                  <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>Add these to the hotsheet (or align the naming) so they reconcile.</div>
                  <div style={{ maxHeight: 260, overflowY: 'auto' }}>
                    {data.unmatched_models.map(m => (
                      <div key={m.device_model} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0', borderBottom: '1px solid var(--border)' }}>
                        <span>{m.device_model || '(blank)'}</span><span style={{ color: 'var(--text3)' }}>{m.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {data.unmapped_contract_types.length > 0 && (
                <div className="card" style={{ padding: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', marginBottom: 8 }}>Contract types with no promo mapping</div>
                  <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>Blank/unknown contract types — confirm how each should map to a promo column.</div>
                  <div style={{ maxHeight: 260, overflowY: 'auto' }}>
                    {data.unmapped_contract_types.map(t => (
                      <div key={t.contract_type} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0', borderBottom: '1px solid var(--border)' }}>
                        <span>{t.contract_type || '(blank)'}</span><span style={{ color: 'var(--text3)' }}>{t.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          <div style={{ marginTop: 14, fontSize: 12, color: 'var(--text3)' }}>
            {data.device_count.toLocaleString()} activated devices reconciled · {data.skipped_unactivated.toLocaleString()} unsold On-Inventory excluded.
          </div>
        </>
      )}
    </div>
  )
}
