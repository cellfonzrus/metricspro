'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

// Marketplace/handset-fulfillment purchase orders — the VidaPay "MA - Marketplace Handset
// Fulfillment Orders" report (mod-commission's report-pull ingest), read here org-scoped via
// commcalc.raw_ma_marketplace_orders (mig 207). Asset-landing style: per-order rows with
// filters (date range / business / status / order type) — RULE THREE pickers, RULE FOUR exports.
// OWNER REQUEST 2026-07-15: "is similar to the asset landing which shows the purchases."

type MPRow = {
  id: string
  date_ordered: string | null
  date_filled: string | null
  date_shipped: string | null
  order_number: string | null
  order_status: string | null
  order_type: string | null
  tspid: string | null
  business_name: string | null
  business_address: string | null
  city: string | null
  state: string | null
  zip: string | null
  product_name: string | null
  number_ordered: number | null
  price: number | null
  tracking_number: string | null
  store: string | null   // canonicalized business_address via store_mapping (asset _canon_store)
}

type MPResponse = {
  available: boolean
  rows: MPRow[]
  count: number
  totals: { orders: number; qty: number; price: number }
  by_status: Record<string, { count: number; qty: number; price: number }>
  note?: string
}

type FilterOptions = {
  available: boolean
  businesses: string[]
  statuses: string[]
  order_types: string[]
}

const selStyle: React.CSSProperties = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="card" style={{ padding: '14px 16px' }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

export default function MarketplacePurchasesPage() {
  const [data, setData] = useState<MPResponse | null>(null)
  const [opts, setOpts] = useState<FilterOptions | null>(null)
  const [loading, setLoading] = useState(true)

  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [business, setBusiness] = useState('')
  const [status, setStatus] = useState('')
  const [orderType, setOrderType] = useState('')

  useEffect(() => {
    api(`/api/v1/asset/marketplace-purchases/filter-options?org_id=${ORG_ID}`)
      .then(setOpts).catch(() => setOpts({ available: false, businesses: [], statuses: [], order_types: [] }))
  }, [])

  function filterQS() {
    const p = new URLSearchParams({ org_id: ORG_ID })
    if (dateFrom) p.set('date_from', dateFrom)
    if (dateTo) p.set('date_to', dateTo)
    if (business) p.set('business', business)
    if (status) p.set('status', status)
    if (orderType) p.set('order_type', orderType)
    return p.toString()
  }

  function load() {
    setLoading(true)
    api(`/api/v1/asset/marketplace-purchases?${filterQS()}`)
      .then(setData)
      .catch((e: any) => setData({ available: false, rows: [], count: 0, totals: { orders: 0, qty: 0, price: 0 }, by_status: {}, note: e?.message || String(e) }))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [dateFrom, dateTo, business, status, orderType])

  function buildPayload(): ExportPayload {
    const rows = data?.rows || []
    return {
      title: 'Marketplace Purchases', subtitle: `${rows.length} order line(s)`,
      filename: 'marketplace-purchases',
      sheets: [{ name: 'Purchases', rows, columns: [
        { header: 'Date Ordered', get: (r: MPRow) => r.date_ordered ? String(r.date_ordered).slice(0, 10) : '' },
        { header: 'Date Filled', get: (r: MPRow) => r.date_filled ? String(r.date_filled).slice(0, 10) : '' },
        { header: 'Date Shipped', get: (r: MPRow) => r.date_shipped ? String(r.date_shipped).slice(0, 10) : '' },
        { header: 'Order #', get: (r: MPRow) => r.order_number },
        { header: 'Status', get: (r: MPRow) => r.order_status },
        { header: 'Order Type', get: (r: MPRow) => r.order_type },
        { header: 'Business / Store', get: (r: MPRow) => r.store || r.business_name },
        { header: 'City', get: (r: MPRow) => r.city },
        { header: 'State', get: (r: MPRow) => r.state },
        { header: 'Product', get: (r: MPRow) => r.product_name },
        { header: 'Qty', get: (r: MPRow) => r.number_ordered, align: 'right' },
        { header: 'Price', get: (r: MPRow) => r.price, money: true },
        { header: 'Tracking #', get: (r: MPRow) => r.tracking_number },
      ] }],
    }
  }

  const rows = data?.rows || []

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🛒 Marketplace Purchases</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            VidaPay Marketplace Handset Fulfillment Orders — handset/accessory purchases with tracking,
            per-order like the Asset Ledger landing.
          </p>
        </div>
        {data?.available && rows.length > 0 && (
          <div style={{ display: 'flex', gap: 8 }}>
            <ExportButtons payload={buildPayload} />
            <SendReportButton exportPayload={buildPayload} compact />
          </div>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.available ? (
        <div className="card" style={{ padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: 34, marginBottom: 12 }}>📭</div>
          <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 6 }}>No marketplace orders yet</div>
          <div style={{ color: 'var(--text2)', fontSize: 13 }}>
            {data?.note || 'Run the VidaPay report pull (MA - Marketplace Handset Fulfillment Orders) to populate this view.'}
          </div>
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 16 }}>
            <Stat label="Orders" value={data.totals.orders.toLocaleString()} />
            <Stat label="Units Ordered" value={data.totals.qty.toLocaleString()} />
            <Stat label="Total Purchased" value={fmt(data.totals.price)} color="var(--accent)" />
          </div>

          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>Filters:</span>
            <select style={selStyle} value={business} onChange={e => setBusiness(e.target.value)}>
              <option value="">All businesses</option>
              {(opts?.businesses || []).map(b => <option key={b} value={b}>{b}</option>)}
            </select>
            <select style={selStyle} value={status} onChange={e => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {(opts?.statuses || []).map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select style={selStyle} value={orderType} onChange={e => setOrderType(e.target.value)}>
              <option value="">All order types</option>
              {(opts?.order_types || []).map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Ordered</label>
            <input type="date" style={selStyle} value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>to</span>
            <input type="date" style={selStyle} value={dateTo} onChange={e => setDateTo(e.target.value)} />
            {(business || status || orderType || dateFrom || dateTo) && (
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
                onClick={() => { setBusiness(''); setStatus(''); setOrderType(''); setDateFrom(''); setDateTo('') }}>✕ Clear</button>
            )}
            <span style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 'auto' }}>{rows.length.toLocaleString()} order(s)</span>
          </div>

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
              No marketplace orders match these filters.
            </div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                    {['Date Ordered', 'Date Filled', 'Date Shipped', 'Order #', 'Status', 'Order Type', 'Business / Store', 'Product', 'Qty', 'Price', 'Tracking #'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 12px', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.id || i} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : undefined }}>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.date_ordered ? String(r.date_ordered).slice(0, 10) : '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.date_filled ? String(r.date_filled).slice(0, 10) : '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.date_shipped ? String(r.date_shipped).slice(0, 10) : '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontFamily: 'monospace' }}>{r.order_number || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.order_status || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.order_type || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.store || r.business_name || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.product_name || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{r.number_ordered ?? '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>{r.price == null ? '—' : fmt(r.price)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'monospace' }}>{r.tracking_number || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
