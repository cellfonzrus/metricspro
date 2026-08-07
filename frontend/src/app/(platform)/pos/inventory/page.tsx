'use client'
// POS module — Phase 1: Inventory (ported from the standalone pos-system app's Inventory Workcenter;
// data access rewired from direct Supabase to the FastAPI /pos router).
// Scope: Serialized + Standard stock views only. Store Transfers land with Phase 2; the source's
// Receiving/Adjustment form was a mock (alert-only) and is intentionally not ported.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { getActiveStore, setActiveStore } from '@/lib/pos-store'
import { useAuth } from '@/lib/auth-context'

interface SerialUnit {
  id: string; product_id: string; store_code: string | null
  product_name?: string | null; product_code?: number | null
  serial_number: string; imei: string | null; sim_card: string | null
  color: string | null; storage: string | null
  condition: string; status: string; cost: number | null
  date_received: string | null; po_number: string | null
  sold_at?: string | null; sold_in_sale_id?: string | null; created_at: string
}

interface StandardStock {
  id: string; product_id: string; store_code: string | null
  product_name?: string | null; product_code?: number | null; retail_price?: number | null
  qty_on_hand: number; qty_on_order: number; qty_reserved: number
  bin_location: string | null; updated_at: string
}

interface Store { store_code: string; address?: string | null; market?: string | null }
interface Product { id: string; short_name: string; inventory_type: string }

const SERIAL_STATUSES = ['in_stock', 'in_transit', 'sold', 'returned', 'transferred', 'rma', 'lost', 'stolen']
const CONDITIONS = ['new', 'refurbished', 'used', 'damaged']

const emptySerialForm = {
  product_id: '', serial_number: '', imei: '', sim_card: '',
  color: '', storage: '', condition: 'new', status: 'in_stock',
  cost: 0, date_received: new Date().toISOString().split('T')[0], po_number: '',
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
const th: React.CSSProperties = { textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const badge: React.CSSProperties = { fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600, textTransform: 'capitalize', whiteSpace: 'nowrap' }

const statusColor = (s: string) => ({
  in_stock: '#27ae60', in_transit: '#9b59b6', sold: '#3498db', returned: '#e67e22',
  transferred: '#9b59b6', rma: '#f39c12', lost: '#e74c3c', stolen: '#c0392b',
}[s] || '#6b7280')

const fmtDate = (d: string | null | undefined) => d ? new Date(d).toLocaleDateString() : '—'

export default function PosInventoryPage() {
  const { user } = useAuth()
  const [tab, setTab] = useState<'serial' | 'standard'>('serial')
  const [stores, setStores] = useState<Store[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [msg, setMsg] = useState('')

  // Active store — a property of this terminal (localStorage), falling back to the login's
  // own store grant, then the first store. Mirrors how the source stamped location_id.
  const [activeStore, setActive] = useState('')
  // Store filter for the stock views — defaults to the active store; 'all' shows every store.
  const [stockStore, setStockStore] = useState('all')

  const [serialUnits, setSerialUnits] = useState<SerialUnit[]>([])
  const [standardStock, setStandardStock] = useState<StandardStock[]>([])
  const [loadingSerial, setLoadingSerial] = useState(true)
  const [loadingStd, setLoadingStd] = useState(true)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [selected, setSelected] = useState<SerialUnit | null>(null)

  const [showSerialForm, setShowSerialForm] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [serialForm, setSerialForm] = useState({ ...emptySerialForm })
  const [saving, setSaving] = useState(false)

  const storeLabel = (code: string | null | undefined) => {
    if (!code) return '—'
    const s = stores.find(st => st.store_code === code)
    return s?.address || code
  }

  async function loadSerial(q: { store?: string; search?: string; status?: string } = {}) {
    setLoadingSerial(true); setMsg('')
    try {
      const store = q.store !== undefined ? q.store : stockStore
      const searchQ = q.search !== undefined ? q.search : search
      const status = q.status !== undefined ? q.status : statusFilter
      const params = new URLSearchParams()
      if (searchQ.trim()) params.set('search', searchQ.trim())
      if (store && store !== 'all') params.set('store_code', store)
      if (status) params.set('status', status)
      const r = await api(`/api/v1/pos/inventory/serial?${params}`)
      setSerialUnits(r.units || [])
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoadingSerial(false)
  }

  async function loadStandard(q: { store?: string } = {}) {
    setLoadingStd(true); setMsg('')
    try {
      const store = q.store !== undefined ? q.store : stockStore
      const params = new URLSearchParams()
      if (store && store !== 'all') params.set('store_code', store)
      const r = await api(`/api/v1/pos/inventory/standard?${params}`)
      setStandardStock(r.stock || [])
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoadingStd(false)
  }

  useEffect(() => {
    api('/api/v1/storeops/stores').then(rows => setStores(rows || [])).catch(() => {})
    api('/api/v1/pos/products?active_only=true').then(r => setProducts(r.products || [])).catch(() => {})
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // Resolve the active store once the store list (and possibly the login) is known:
  // localStorage → login's store_code → first store. Stock views default to it.
  useEffect(() => {
    if (activeStore || stores.length === 0) return
    const code = getActiveStore() || user?.store_code || stores[0]?.store_code || ''
    setActive(code)
    const filter = code || 'all'
    setStockStore(filter)
    loadSerial({ store: filter })
    loadStandard({ store: filter })
  }, [stores, user])  // eslint-disable-line react-hooks/exhaustive-deps

  function changeActiveStore(code: string) {
    setActiveStore(code || null)
    setActive(code)
    const filter = code || 'all'
    setStockStore(filter)
    loadSerial({ store: filter })
    loadStandard({ store: filter })
  }

  function changeStockStore(code: string) {
    setStockStore(code)
    loadSerial({ store: code })
    loadStandard({ store: code })
  }

  function openNewSerial() {
    setSerialForm({ ...emptySerialForm })
    setEditMode(false)
    setShowSerialForm(true)
  }

  function openEditSerial() {
    if (!selected) return
    setSerialForm({
      product_id: selected.product_id || '',
      serial_number: selected.serial_number || '',
      imei: selected.imei || '', sim_card: selected.sim_card || '',
      color: selected.color || '', storage: selected.storage || '',
      condition: selected.condition || 'new', status: selected.status || 'in_stock',
      cost: Number(selected.cost || 0),
      date_received: selected.date_received ? selected.date_received.split('T')[0] : '',
      po_number: selected.po_number || '',
    })
    setEditMode(true)
    setShowSerialForm(true)
  }

  async function saveSerial() {
    setSaving(true)
    const payload = {
      product_id: serialForm.product_id,
      serial_number: serialForm.serial_number.trim(),
      imei: serialForm.imei.trim() || null,
      sim_card: serialForm.sim_card.trim() || null,
      color: serialForm.color.trim() || null,
      storage: serialForm.storage.trim() || null,
      condition: serialForm.condition,
      status: serialForm.status,
      cost: serialForm.cost,
      date_received: serialForm.date_received || null,
      po_number: serialForm.po_number.trim() || null,
      // Receiving stamps the terminal's active store — same as the source stamped location_id.
      store_code: editMode ? (selected?.store_code ?? null) : (activeStore || null),
    }
    try {
      if (editMode && selected) await api(`/api/v1/pos/inventory/serial/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      else await api('/api/v1/pos/inventory/serial', { method: 'POST', body: JSON.stringify(payload) })
      setShowSerialForm(false); setEditMode(false); setSelected(null)
      setSerialForm({ ...emptySerialForm })
      await loadSerial()
    } catch (err: any) { alert('Could not save serial item: ' + (err?.message || err)) }
    setSaving(false)
  }

  const serialProducts = products.filter(p => p.inventory_type === 'serial')

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📦 Inventory</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Serial-tracked units &amp; standard stock{activeStore ? ` · active store: ${storeLabel(activeStore)}` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 13, color: '#dc2626' }}>{msg}</span>}
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Active store</label>
          <select value={activeStore} onChange={e => changeActiveStore(e.target.value)} style={{ ...input, width: 200 }}>
            <option value="">-- Select store --</option>
            {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
          </select>
          {tab === 'serial' && <button className="btn btn-primary" onClick={openNewSerial}>+ Add Serial Item</button>}
          {tab === 'serial' && selected && <button className="btn btn-secondary" onClick={openEditSerial}>View/Edit</button>}
        </div>
      </div>

      {/* Tabs — Transfers deliberately absent until Phase 2 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        {([['serial', '📱 Serialized'], ['standard', '📦 Standard']] as const).map(([key, lbl]) => (
          <button key={key} onClick={() => setTab(key)} className={tab === key ? 'btn btn-primary' : 'btn btn-secondary'}>
            {lbl}
          </button>
        ))}
        <span style={{ fontSize: 12, color: 'var(--text3)', border: '1px dashed var(--border)', borderRadius: 999, padding: '4px 12px' }}>
          🚚 Transfers arrive with Phase 2
        </span>
      </div>

      {/* ===== SERIALIZED ===== */}
      {tab === 'serial' && (
        <div>
          <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && loadSerial()}
              placeholder="Search serial #, IMEI…" style={{ ...input, flex: 1, minWidth: 200 }} />
            <select value={stockStore} onChange={e => changeStockStore(e.target.value)} style={{ ...input, width: 200 }}>
              <option value="all">All stores</option>
              {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}{s.store_code === activeStore ? ' (active)' : ''}</option>)}
            </select>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ ...input, width: 160 }}>
              <option value="">All statuses</option>
              {SERIAL_STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
            </select>
            <button className="btn btn-primary" onClick={() => loadSerial()}>Search</button>
            <button className="btn btn-secondary" onClick={() => { setSearch(''); setStatusFilter(''); loadSerial({ search: '', status: '' }) }}>Clear</button>
          </div>

          {loadingSerial ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : (
            <div className="table-wrapper" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100, fontSize: 13 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Product', 'Serial #', 'IMEI', 'SIM Card', 'Store', 'Color', 'Storage', 'Condition', 'Status', 'Cost', 'Received', 'PO #'].map(h =>
                    <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {serialUnits.map(u => (
                    <tr key={u.id} onClick={() => setSelected(selected?.id === u.id ? null : u)}
                      style={{ cursor: 'pointer', background: selected?.id === u.id ? 'var(--surface2)' : 'transparent' }}>
                      <td style={{ ...cell, fontWeight: 500 }}>{u.product_name || '—'}</td>
                      <td style={{ ...cell, fontFamily: 'monospace' }}>{u.serial_number}</td>
                      <td style={{ ...cell, color: 'var(--text2)', fontFamily: 'monospace' }}>{u.imei || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{u.sim_card || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{u.store_code || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{u.color || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{u.storage || '—'}</td>
                      <td style={cell}>
                        <span style={{ ...badge, border: '1px solid var(--border)', color: 'var(--text2)' }}>{u.condition}</span>
                      </td>
                      <td style={cell}>
                        <span style={{ ...badge, background: `${statusColor(u.status)}20`, color: statusColor(u.status) }}>
                          {u.status.replace('_', ' ')}
                        </span>
                      </td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{u.cost != null ? `$${Number(u.cost).toFixed(2)}` : '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{fmtDate(u.date_received)}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{u.po_number || '—'}</td>
                    </tr>
                  ))}
                  {serialUnits.length === 0 && (
                    <tr><td colSpan={12} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                      No serial items found. Click “+ Add Serial Item” to add phones/devices.
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ===== STANDARD (read-only — quantities move via checkout/void) ===== */}
      {tab === 'standard' && (
        <div>
          <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: 'var(--text2)', flex: 1 }}>
              Standard stock (accessories) — read-only; quantities move via checkout and voids.
            </span>
            <select value={stockStore} onChange={e => changeStockStore(e.target.value)} style={{ ...input, width: 200 }}>
              <option value="all">All stores</option>
              {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}{s.store_code === activeStore ? ' (active)' : ''}</option>)}
            </select>
          </div>

          {loadingStd ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : (
            <div className="table-wrapper" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900, fontSize: 13 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Product', 'Product #', 'Store', 'Retail', 'Qty On Hand', 'Qty On Order', 'Qty Reserved', 'Bin Location', 'Last Updated'].map(h =>
                    <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {standardStock.map(row => (
                    <tr key={row.id}>
                      <td style={{ ...cell, fontWeight: 500 }}>{row.product_name || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{row.product_code ?? '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{row.store_code || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>${Number(row.retail_price || 0).toFixed(2)}</td>
                      <td style={cell}>
                        <span style={{ fontWeight: 700, color: row.qty_on_hand <= 2 ? '#e74c3c' : row.qty_on_hand <= 5 ? '#f39c12' : '#27ae60' }}>
                          {row.qty_on_hand}
                        </span>
                      </td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{row.qty_on_order}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{row.qty_reserved}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{row.bin_location || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{fmtDate(row.updated_at)}</td>
                    </tr>
                  ))}
                  {standardStock.length === 0 && (
                    <tr><td colSpan={9} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                      No standard inventory items found.
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Serial item modal (add / edit) */}
      {showSerialForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 620, maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>{editMode ? `📱 Edit Serial Item — ${selected?.serial_number}` : '📱 Add Serial Tracked Item'}</b>
              <button onClick={() => setShowSerialForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 20, overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px' }}>
                {editMode
                  ? <>Store: <b style={{ color: 'var(--text)' }}>{storeLabel(selected?.store_code)}</b></>
                  : <>Receiving into: <b style={{ color: 'var(--text)' }}>{storeLabel(activeStore)}</b> (change via the active-store selector in the header)</>}
              </div>
              <div>
                <label style={label}>Product *</label>
                <select value={serialForm.product_id} onChange={e => setSerialForm(f => ({ ...f, product_id: e.target.value }))} style={input}>
                  <option value="">-- Select Product --</option>
                  {serialProducts.map(p => <option key={p.id} value={p.id}>{p.short_name}</option>)}
                </select>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div><label style={label}>Serial Number *</label><input value={serialForm.serial_number} onChange={e => setSerialForm(f => ({ ...f, serial_number: e.target.value }))} style={input} placeholder="Scan or type serial #" /></div>
                <div><label style={label}>IMEI</label><input value={serialForm.imei} onChange={e => setSerialForm(f => ({ ...f, imei: e.target.value }))} style={input} placeholder="15-digit IMEI" /></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div><label style={label}>SIM Card</label><input value={serialForm.sim_card} onChange={e => setSerialForm(f => ({ ...f, sim_card: e.target.value }))} style={input} /></div>
                <div><label style={label}>Color</label><input value={serialForm.color} onChange={e => setSerialForm(f => ({ ...f, color: e.target.value }))} style={input} placeholder="e.g. Space Black" /></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                <div><label style={label}>Storage</label><input value={serialForm.storage} onChange={e => setSerialForm(f => ({ ...f, storage: e.target.value }))} style={input} placeholder="e.g. 256GB" /></div>
                <div><label style={label}>Condition</label><select value={serialForm.condition} onChange={e => setSerialForm(f => ({ ...f, condition: e.target.value }))} style={input}>{CONDITIONS.map(c => <option key={c}>{c}</option>)}</select></div>
                <div><label style={label}>Status</label><select value={serialForm.status} onChange={e => setSerialForm(f => ({ ...f, status: e.target.value }))} style={input}>{SERIAL_STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}</select></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                <div><label style={label}>Cost</label><input type="number" step="0.01" value={serialForm.cost} onChange={e => setSerialForm(f => ({ ...f, cost: parseFloat(e.target.value) || 0 }))} style={input} /></div>
                <div><label style={label}>Date Received</label><input type="date" value={serialForm.date_received} onChange={e => setSerialForm(f => ({ ...f, date_received: e.target.value }))} style={input} /></div>
                <div><label style={label}>PO Number</label><input value={serialForm.po_number} onChange={e => setSerialForm(f => ({ ...f, po_number: e.target.value }))} style={input} /></div>
              </div>
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowSerialForm(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={saving || !serialForm.product_id || !serialForm.serial_number.trim()} onClick={saveSerial}>
                {saving ? 'Saving…' : 'Save & Close'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
