'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, ORG_ID, getActiveOrg } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'
import type { StoreOpt } from '@/lib/market-store-cascade'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// Marketplace/handset-fulfillment purchase orders — the VidaPay "MA - Marketplace Handset
// Fulfillment Orders" report (mod-commission's report-pull ingest), read here org-scoped via
// commcalc.raw_ma_marketplace_orders (mig 207). Asset-landing style: per-order rows with
// filters (date range / business / status / order type) — RULE THREE pickers, RULE FOUR exports.
// OWNER REQUEST 2026-07-15: "is similar to the asset landing which shows the purchases."
//
// RULE FIVE (§3d) retrofit 2026-08-10 (owner: "should also have our standard filters in addition to the
// ones which are there"): the shared <StandardFilterBar> is ADDED — period as a date range (it replaces
// the page's own Ordered from/to inputs, same two values), plus the market -> store cascade. The three
// existing pickers (business / status / order type) are module facets and are APPENDED, never
// substituted. DEVIATION, stated out loud: a marketplace order is placed against the DEALER account and
// carries no salesperson, so there is no rep dimension to filter on and the rep control is hidden.
//
// Store/market narrow the loaded rows CLIENT-side (the row's canonicalized `store`, with market joined
// from the org roster), and the three stat tiles are computed from that same filtered set — otherwise
// the tiles would keep reporting the server's unfiltered totals while the table showed a subset.

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

// Reads carry the ACTIVE tenant, not the house constant. The tenant middleware rewrites org_id from the
// JWT for a normal user (so this is a no-op for them) but it does NOT rewrite it for a super-admin — who
// would otherwise sit on luxelink's Marketplace Purchases and be shown the house org's orders. Same
// mitigation the Sales Report / Executive MTD already carry.
const orgQS = () => `org_id=${encodeURIComponent(getActiveOrg() || ORG_ID)}`

// Header -> row field, so the sortable columns and the rendered columns cannot drift apart.
const COLS: [string, keyof MPRow][] = [
  ['Date Ordered', 'date_ordered'], ['Date Filled', 'date_filled'], ['Date Shipped', 'date_shipped'],
  ['Order #', 'order_number'], ['Status', 'order_status'], ['Order Type', 'order_type'],
  ['Business / Store', 'store'], ['Product', 'product_name'], ['Qty', 'number_ordered'],
  ['Price', 'price'], ['Tracking #', 'tracking_number'],
]

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

  // The standard core set. `period`/`periodTo` ARE the Ordered from/to dates this page already sent to
  // the server, so the range control drives the same query — no second, competing date filter.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const dateFrom = filt.period || '', dateTo = filt.periodTo || ''
  const [business, setBusiness] = useState('')
  const [status, setStatus] = useState('')
  const [orderType, setOrderType] = useState('')
  // Org roster → the cascade's store options (each store WITH its market). Org-scoped by construction.
  const [roster, setRoster] = useState<StoreOpt[]>([])

  useEffect(() => {
    apiCached(`/api/v1/asset/marketplace-purchases/filter-options?${orgQS()}`, LOOKUP)
      .then(setOpts).catch(() => setOpts({ available: false, businesses: [], statuses: [], order_types: [] }))
    apiCached(`/api/v1/core/filter-options?${orgQS()}`, LOOKUP)
      .then((d: any) => setRoster((d?.stores || []).map((x: any) => ({ id: x.store, label: x.store, market: x.market || null }))))
      .catch(() => setRoster([]))
  }, [])

  function filterQS() {
    const p = new URLSearchParams({ org_id: getActiveOrg() || ORG_ID })
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
  useEffect(() => { load() }, [dateFrom, dateTo, business, status, orderType])   // store/market narrow client-side

  // Store/market narrowing happens here (client-side, over the rows the server already returned for the
  // date/business/status/type query). Market is joined from the org roster onto the row's canonical
  // `store`; a row whose store isn't in the roster keeps market=null and is reachable via '(no market)'.
  const marketByStore = useMemo(() => {
    const m: Record<string, string> = {}
    roster.forEach(r => { if (r.market) m[r.id.trim().toLowerCase()] = r.market })
    return m
  }, [roster])
  const serverRows = data?.rows || []
  const scopedRows = useMemo(() => filterRows(serverRows, filt, {
    store: (r: MPRow) => r.store || r.business_name,
    market: (r: MPRow) => marketByStore[String(r.store || r.business_name || '').trim().toLowerCase()] || '',
  }), [serverRows, filt, marketByStore])

  const getCell = useCallback((r: any, field: string) => r?.[field], [])
  const { sort, toggle, sorted: viewRows } = useTableSort(scopedRows, getCell)

  // Tiles follow the SAME filtered set the table shows (what you see is what the totals count).
  const tiles = useMemo(() => scopedRows.reduce(
    (t, r) => ({ orders: t.orders + 1, qty: t.qty + (Number(r.number_ordered) || 0), price: t.price + (Number(r.price) || 0) }),
    { orders: 0, qty: 0, price: 0 },
  ), [scopedRows])

  function buildPayload(): ExportPayload {
    const rows = viewRows
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

  const rows = viewRows

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🛒 Marketplace Purchases</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
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
            <Stat label="Orders" value={tiles.orders.toLocaleString()} />
            <Stat label="Units Ordered" value={tiles.qty.toLocaleString()} />
            <Stat label="Total Purchased" value={fmt(tiles.price)} color="var(--accent)" />
          </div>

          {/* RULE FIVE core set (period as an Ordered date range + market -> store cascade), with this
              module's own three pickers APPENDED via `right`. No rep control — see the header note. */}
          <div className="card" style={{ padding: '10px 14px', marginBottom: 12 }}>
            <StandardFilterBar
              value={filt} onChange={setFilt} periodMode="range"
              show={{ period: true, stores: true, markets: true, reps: false }}
              cascadeStores={roster}
              storeLabel="Stores…" marketLabel="Markets…"
              right={
                <>
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
                  {(business || status || orderType) && (
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
                      onClick={() => { setBusiness(''); setStatus(''); setOrderType('') }}>✕ Clear these</button>
                  )}
                  <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                    {rows.length.toLocaleString()} order(s){rows.length !== serverRows.length ? ` of ${serverRows.length.toLocaleString()}` : ''}
                  </span>
                </>
              }
            />
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
                    {COLS.map(([h, field]) => (
                      <SortableTh key={h} field={field} sort={sort} onSort={toggle}
                        style={{ textAlign: 'left', padding: '8px 12px', whiteSpace: 'nowrap' }}>{h}</SortableTh>
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
