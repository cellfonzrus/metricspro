'use client'
// POS — Customer Special Order (store-facing). Owner directive 2026-08-19.
//
// Sell an item the store DOESN'T stock (accessories / electronics) by special-ordering it — it ships
// to the store, the customer picks it up. NEUTRAL BY DESIGN: this screen never names the back-end
// vendor. It reads only the neutral catalog (GET /pos/special-orders/catalog, which never returns the
// vendor linkage) and posts the order (POST /pos/special-orders); the server reads the vendor cost only
// to book COGS + guard the margin, so the source stays hidden from the customer and store staff.
//
// Booking is the existing accounting rails: the declared price becomes the sale line's unit_price
// (→ revenue) and the vendor cost the line's cost (→ COGS); profit derives automatically.
import { useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { getActiveStore } from '@/lib/pos-store'
import { useAuth } from '@/lib/auth-context'

interface CatalogItem {
  id: string; product_code: string | null; short_name: string | null; full_name: string | null
  category: string | null; retail_price: number | null; is_taxable: boolean | null
  system_category: string | null
}
interface SpecialOrder {
  id: string; order_no: number; store_code: string; ship_to_store: string | null
  customer_name: string | null; description: string | null; qty: number
  sale_price: number; status: string; tracking: string | null; created_at: string
}
interface Store { store_code: string }

const PAYMENT_METHODS = [
  { value: 'cash', label: '💵 Cash' },
  { value: 'credit_card', label: '💳 Credit' },
  { value: 'debit_card', label: '🏧 Debit' },
  { value: 'check', label: '📝 Check' },
  { value: 'card_external', label: '💳 Card (external)' },
]
// Customer-facing lifecycle labels — deliberately generic (no vendor terminology).
const STATUS_LABEL: Record<string, string> = {
  requested: 'Requested', ordered: 'Ordered', shipped: 'Shipped',
  received: 'Arrived at store', delivered: 'Delivered', cancelled: 'Cancelled',
}
const STATUS_COLOR: Record<string, string> = {
  requested: 'var(--amber)', ordered: 'var(--accent)', shipped: 'var(--accent)',
  received: 'var(--green)', delivered: 'var(--green)', cancelled: '#dc2626',
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none', boxSizing: 'border-box' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }

function money(n: number | null | undefined) { return `$${(Number(n) || 0).toFixed(2)}` }

export default function SpecialOrdersPage() {
  const { user } = useAuth()
  const [view, setView] = useState<'new' | 'orders'>('new')
  const [stores, setStores] = useState<Store[]>([])
  const [store, setStore] = useState('')

  // New-order form
  const [search, setSearch] = useState('')
  const [results, setResults] = useState<CatalogItem[]>([])
  const [searching, setSearching] = useState(false)
  const [picked, setPicked] = useState<CatalogItem | null>(null)
  const [salePrice, setSalePrice] = useState('')
  const [qty, setQty] = useState('1')
  const [taxTotal, setTaxTotal] = useState('')
  const [customerName, setCustomerName] = useState('')
  const [shipTo, setShipTo] = useState('')
  const [payMethod, setPayMethod] = useState('cash')
  const [payAmount, setPayAmount] = useState('')
  const [placing, setPlacing] = useState(false)
  const [msg, setMsg] = useState('')
  const [placed, setPlaced] = useState<SpecialOrder | null>(null)

  // Orders list
  const [orders, setOrders] = useState<SpecialOrder[]>([])
  const [loadingOrders, setLoadingOrders] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

  useEffect(() => {
    apiCached('/api/v1/storeops/stores', LOOKUP).then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
  }, [])
  useEffect(() => {
    const s = getActiveStore() || user?.store_code || ''
    setStore(s); setShipTo(s)
  }, [user])

  async function runSearch() {
    setSearching(true); setMsg('')
    try {
      const p = new URLSearchParams()
      if (search.trim()) p.set('search', search.trim())
      const r = await api(`/api/v1/pos/special-orders/catalog?${p}`)
      setResults(r.items || [])
    } catch (e: any) { setMsg('Catalog search failed: ' + (e?.message || e)) }
    setSearching(false)
  }

  function pick(it: CatalogItem) {
    setPicked(it)
    setSalePrice(it.retail_price != null ? String(it.retail_price) : '')
    setPlaced(null); setMsg('')
  }

  const subtotal = useMemo(() => (Number(salePrice) || 0) * (Number(qty) || 0), [salePrice, qty])
  const total = useMemo(() => subtotal + (Number(taxTotal) || 0), [subtotal, taxTotal])

  async function placeOrder() {
    if (!picked) { setMsg('Pick an item first.'); return }
    if (!store) { setMsg('Select the selling store.'); return }
    if (!(Number(salePrice) > 0)) { setMsg('Enter the sale price.'); return }
    setPlacing(true); setMsg('')
    try {
      const body: any = {
        store_code: store, product_id: picked.id, qty: Number(qty) || 1,
        declared_sale_price: Number(salePrice), tax_total: Number(taxTotal) || 0,
        customer_name: customerName.trim() || null, ship_to_store: shipTo || store,
      }
      if (Number(payAmount) > 0) body.payment = { payment_method: payMethod, amount: Number(payAmount) }
      const r = await api('/api/v1/pos/special-orders', { method: 'POST', body: JSON.stringify(body) })
      setPlaced(r.special_order || null)
      // Reset the item selection but keep the store for the next order.
      setPicked(null); setSalePrice(''); setQty('1'); setTaxTotal(''); setCustomerName(''); setPayAmount('')
      setSearch(''); setResults([])
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setPlacing(false)
  }

  async function loadOrders() {
    setLoadingOrders(true)
    try {
      const p = new URLSearchParams()
      if (statusFilter) p.set('status', statusFilter)
      const r = await api(`/api/v1/pos/special-orders?${p}`)
      setOrders(r.special_orders || [])
    } catch (e: any) { setMsg('Failed to load orders: ' + (e?.message || e)) }
    setLoadingOrders(false)
  }
  useEffect(() => { if (view === 'orders') loadOrders() }, [view, statusFilter])  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Customer Special Order</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Order an item the store doesn&rsquo;t stock — ships to the store for customer pickup.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <a href="/pos/sales" className="btn btn-secondary" style={{ textDecoration: 'none' }}>← Register</a>
          <button className={view === 'new' ? 'btn btn-primary' : 'btn btn-secondary'} onClick={() => setView('new')}>New Order</button>
          <button className={view === 'orders' ? 'btn btn-primary' : 'btn btn-secondary'} onClick={() => setView('orders')}>Orders</button>
        </div>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 12, borderColor: '#dc2626', color: '#dc2626', fontSize: 13 }}>{msg}</div>}

      {placed && (
        <div style={{ ...panel, marginBottom: 12, borderColor: 'var(--green)', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 20 }}>✅</span>
          <div style={{ fontSize: 14 }}>
            <b>Special order #{placed.order_no} placed.</b> {placed.description} — {money(placed.sale_price)}.{' '}
            Status: <b>{STATUS_LABEL[placed.status] || placed.status}</b>. The customer will be notified when it arrives at {placed.ship_to_store || placed.store_code}.
          </div>
        </div>
      )}

      {view === 'new' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 360px', gap: 14, alignItems: 'start' }}>
          {/* Catalog search + results */}
          <div style={panel}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>1. Find the item</div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && runSearch()}
                placeholder="Search special-order catalog…" style={{ ...input, flex: 1 }} />
              <button className="btn btn-primary" onClick={runSearch} disabled={searching}>{searching ? '…' : 'Search'}</button>
            </div>
            <div style={{ maxHeight: 420, overflowY: 'auto' }}>
              {results.length === 0 ? (
                <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20, textAlign: 'center' }}>
                  {searching ? 'Searching…' : 'Search the catalog to find an item to special-order.'}
                </div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <tbody>
                    {results.map(it => (
                      <tr key={it.id} onClick={() => pick(it)}
                        style={{ cursor: 'pointer', background: picked?.id === it.id ? 'var(--surface)' : 'transparent' }}>
                        <td style={cell}>
                          <div style={{ fontWeight: 600 }}>{it.short_name || it.full_name || 'Item'}</div>
                          <div style={{ fontSize: 11, color: 'var(--text3)' }}>{[it.category, it.product_code].filter(Boolean).join(' · ')}</div>
                        </td>
                        <td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{money(it.retail_price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Order form */}
          <div style={panel}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>2. Order details</div>
            {!picked ? (
              <div style={{ color: 'var(--text3)', fontSize: 13, padding: '20px 0' }}>Select an item on the left to begin.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ background: 'var(--surface)', borderRadius: 7, padding: 10, border: '1px solid var(--border)' }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{picked.short_name || picked.full_name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{picked.category || '—'} · list {money(picked.retail_price)}</div>
                </div>
                <div>
                  <label style={label}>Selling store *</label>
                  <select value={store} onChange={e => { setStore(e.target.value); if (!shipTo) setShipTo(e.target.value) }} style={input}>
                    <option value="">— select store —</option>
                    {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code}</option>)}
                  </select>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px', gap: 8 }}>
                  <div>
                    <label style={label}>Sale price (each) *</label>
                    <input type="number" value={salePrice} onChange={e => setSalePrice(e.target.value)} style={input} placeholder="0.00" />
                  </div>
                  <div>
                    <label style={label}>Qty</label>
                    <input type="number" value={qty} onChange={e => setQty(e.target.value)} style={input} min="1" />
                  </div>
                </div>
                <div>
                  <label style={label}>Tax (total, optional)</label>
                  <input type="number" value={taxTotal} onChange={e => setTaxTotal(e.target.value)} style={input} placeholder="0.00" />
                </div>
                <div>
                  <label style={label}>Customer name</label>
                  <input value={customerName} onChange={e => setCustomerName(e.target.value)} style={input} placeholder="For the pickup" />
                </div>
                <div>
                  <label style={label}>Ship to store</label>
                  <select value={shipTo} onChange={e => setShipTo(e.target.value)} style={input}>
                    {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code}</option>)}
                  </select>
                </div>
                <div>
                  <label style={label}>Payment</label>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                    {PAYMENT_METHODS.map(m => (
                      <button key={m.value} onClick={() => setPayMethod(m.value)}
                        style={{ background: payMethod === m.value ? 'var(--accent)' : 'var(--surface)', border: `1px solid ${payMethod === m.value ? 'var(--accent2)' : 'var(--border)'}`, color: payMethod === m.value ? 'white' : 'var(--text)', borderRadius: 6, padding: '5px 9px', fontSize: 11, cursor: 'pointer' }}>
                        {m.label}
                      </button>
                    ))}
                  </div>
                  <input type="number" value={payAmount} onChange={e => setPayAmount(e.target.value)} style={input} placeholder={`Amount (total ${money(total)})`} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, fontWeight: 700, padding: '6px 0', borderTop: '1px solid var(--border)' }}>
                  <span>Total</span><span>{money(total)}</span>
                </div>
                <button className="btn btn-primary" onClick={placeOrder} disabled={placing} style={{ width: '100%', justifyContent: 'center' }}>
                  {placing ? 'Placing…' : 'Place special order'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {view === 'orders' && (
        <div>
          <div style={{ ...panel, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text2)' }}>Status</span>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ ...input, width: 180 }}>
              <option value="">All</option>
              {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <button className="btn btn-secondary" onClick={loadOrders}>Refresh</button>
            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>{orders.length} order{orders.length === 1 ? '' : 's'}</span>
          </div>
          {loadingOrders ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : (
            <div className="table-wrapper" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820, fontSize: 13 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['#', 'Date', 'Item', 'Customer', 'Store', 'Ship to', 'Qty', 'Price', 'Status', 'Tracking'].map(h =>
                    <th key={h} style={{ textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {orders.map(o => (
                    <tr key={o.id}>
                      <td style={cell}>{o.order_no}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{(o.created_at || '').slice(0, 10)}</td>
                      <td style={{ ...cell, fontWeight: 500 }}>{o.description || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{o.customer_name || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{o.store_code}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{o.ship_to_store || o.store_code}</td>
                      <td style={cell}>{o.qty}</td>
                      <td style={cell}>{money(o.sale_price)}</td>
                      <td style={cell}><span style={{ color: STATUS_COLOR[o.status] || 'var(--text2)', fontWeight: 600 }}>{STATUS_LABEL[o.status] || o.status}</span></td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{o.tracking || '—'}</td>
                    </tr>
                  ))}
                  {orders.length === 0 && (
                    <tr><td colSpan={10} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No special orders yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
