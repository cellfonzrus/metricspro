'use client'
// POS module — Phase 1+2: Inventory (ported from the standalone pos-system app's Inventory Workcenter;
// data access rewired from direct Supabase to the FastAPI /pos router).
// Phase 1: Serialized + Standard stock views. Phase 2 adds Store Transfers (list, new-transfer draft
// with item picking, ship/receive/cancel — all inventory movement is server-side) and Purchase Orders
// (header-only list + Enter Order form, matching the standalone UI). The source's Receiving/Adjustment
// form was a mock (alert-only) and is intentionally not ported.
import { useEffect, useState } from 'react'
import { api, localToday } from '@/lib/client'
import { apiCached } from '@/lib/cache'
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

interface Transfer {
  id: string; transfer_number: string
  from_store_code: string | null; to_store_code: string | null
  created_by: string | null; status: string
  shipped_at: string | null; received_at: string | null
  notes: string | null; created_at: string; item_count?: number
}

interface TransferItem {
  product_id: string; qty: number | null; serial_number: string | null
  product_name?: string | null; product_code?: number | null
}

// Items picked onto a not-yet-saved transfer (serial units by serial #, standard items by qty)
interface DraftItem {
  kind: 'serial' | 'standard'
  product_id: string; product_name: string
  qty: number; serial_number: string | null
}

interface PurchaseOrder {
  id: string; po_number: string
  vendor_id: string | null; vendor_name?: string | null
  status: string; order_date: string | null; expected_date: string | null
  total_cost: number | null; created_at: string
}

interface Vendor { id: string; legal_name: string }

const SERIAL_STATUSES = ['in_stock', 'in_transit', 'sold', 'returned', 'transferred', 'rma', 'lost', 'stolen']
const CONDITIONS = ['new', 'refurbished', 'used', 'damaged']

const emptySerialForm = {
  product_id: '', serial_number: '', imei: '', sim_card: '',
  color: '', storage: '', condition: 'new', status: 'in_stock',
  cost: 0, date_received: localToday(), po_number: '',
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

const transferStatusColor = (s: string) => ({
  pending: '#f39c12', shipped: '#3498db', received: '#27ae60', cancelled: '#6b7280',
}[s] || '#6b7280')

const poStatusColor = (s: string) => ({
  draft: '#f39c12', ordered: '#3498db', received: '#27ae60', cancelled: '#6b7280',
}[s] || '#f39c12')

const smallBtn = (bg: string): React.CSSProperties => ({
  background: bg, border: 'none', color: '#fff', borderRadius: 5,
  padding: '4px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap',
})

const fmtDate = (d: string | null | undefined) => d ? new Date(d).toLocaleDateString() : '—'

export default function PosInventoryPage() {
  const { user } = useAuth()
  const [tab, setTab] = useState<'serial' | 'standard' | 'transfers' | 'po'>('serial')
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

  // ---- Store Transfers (Phase 2) ----
  const [transfers, setTransfers] = useState<Transfer[]>([])
  const [transfersLoading, setTransfersLoading] = useState(false)
  const [selectedTransfer, setSelectedTransfer] = useState<Transfer | null>(null)
  const [transferItems, setTransferItems] = useState<TransferItem[]>([])
  const [transferItemsLoading, setTransferItemsLoading] = useState(false)
  const [transferActionId, setTransferActionId] = useState<string | null>(null)
  const [showTransferForm, setShowTransferForm] = useState(false)
  const [transferForm, setTransferForm] = useState({ from_store_code: '', to_store_code: '', notes: '' })
  const [draftItems, setDraftItems] = useState<DraftItem[]>([])
  const [serialQuery, setSerialQuery] = useState('')
  const [serialResults, setSerialResults] = useState<SerialUnit[]>([])
  const [serialSearching, setSerialSearching] = useState(false)
  const [stdProductId, setStdProductId] = useState('')
  const [stdQty, setStdQty] = useState(1)
  // qty_on_hand by product_id at the draft's source store — client-side sanity check only;
  // shipping re-validates stock server-side.
  const [sourceQty, setSourceQty] = useState<Record<string, number>>({})

  // ---- Purchase Orders (Phase 2) ----
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([])
  const [poLoading, setPoLoading] = useState(false)
  const [showPOForm, setShowPOForm] = useState(false)
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [poForm, setPoForm] = useState({
    vendor_id: '', order_date: localToday(), expected_date: '', notes: '',
  })

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
    apiCached('/api/v1/storeops/stores').then(rows => setStores(rows || [])).catch(() => {})
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

  function selectTab(key: 'serial' | 'standard' | 'transfers' | 'po') {
    setTab(key)
    if (key === 'transfers') loadTransfers()
    if (key === 'po') { loadPurchaseOrders(); if (vendors.length === 0) loadVendors() }
  }

  // ==================== STORE TRANSFERS ====================

  async function loadTransfers() {
    setTransfersLoading(true); setMsg('')
    try {
      const r = await api('/api/v1/pos/transfers')
      setTransfers(r.transfers || [])
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setTransfersLoading(false)
  }

  async function openTransferDetail(t: Transfer) {
    setSelectedTransfer(t)
    setTransferItemsLoading(true)
    try {
      const r = await api(`/api/v1/pos/transfers/${t.id}`)
      setTransferItems(r.transfer?.items || [])
    } catch (err: any) { alert('Could not load transfer items: ' + (err?.message || err)) }
    setTransferItemsLoading(false)
  }

  function openTransferForm() {
    const source = activeStore || stores[0]?.store_code || ''
    setTransferForm({ from_store_code: source, to_store_code: '', notes: '' })
    setDraftItems([])
    setSerialQuery('')
    setSerialResults([])
    setStdProductId('')
    setStdQty(1)
    setShowTransferForm(true)
    if (source) loadSourceQty(source)
  }

  async function loadSourceQty(storeCode: string) {
    try {
      const r = await api(`/api/v1/pos/inventory/standard?store_code=${encodeURIComponent(storeCode)}`)
      const map: Record<string, number> = {}
      for (const row of (r.stock || []) as StandardStock[]) map[row.product_id] = row.qty_on_hand
      setSourceQty(map)
    } catch (err: any) { alert('Could not load stock levels at source: ' + (err?.message || err)) }
  }

  function changeTransferSource(storeCode: string) {
    setTransferForm(f => ({ ...f, from_store_code: storeCode }))
    // Items were picked against the previous source's stock — start over.
    setDraftItems([])
    setSerialResults([])
    setSerialQuery('')
    setSourceQty({})
    if (storeCode) loadSourceQty(storeCode)
  }

  async function searchSourceSerials() {
    const q = serialQuery.trim()
    if (!q) { setSerialResults([]); return }
    if (!transferForm.from_store_code) { alert('Pick a source store first.'); return }
    setSerialSearching(true)
    try {
      const params = new URLSearchParams({ store_code: transferForm.from_store_code, status: 'in_stock', search: q })
      const r = await api(`/api/v1/pos/inventory/serial?${params}`)
      setSerialResults(r.units || [])
    } catch (err: any) { alert('Serial search failed: ' + (err?.message || err)) }
    setSerialSearching(false)
  }

  function addSerialToDraft(item: SerialUnit) {
    if (draftItems.some(d => d.serial_number === item.serial_number)) {
      alert(`Serial ${item.serial_number} is already on this transfer.`)
      return
    }
    setDraftItems(d => [...d, {
      kind: 'serial',
      product_id: item.product_id,
      product_name: item.product_name || 'Unknown product',
      qty: 1,
      serial_number: item.serial_number,
    }])
  }

  function addStandardToDraft() {
    if (!stdProductId) { alert('Pick a product first.'); return }
    if (stdQty < 1) { alert('Quantity must be at least 1.'); return }
    const available = sourceQty[stdProductId] ?? 0
    if (stdQty > available) {
      alert(`Only ${available} on hand at the source store — cannot add ${stdQty}. (Shipping validates again server-side.)`)
      return
    }
    if (draftItems.some(d => d.kind === 'standard' && d.product_id === stdProductId)) {
      alert('That product is already on this transfer — remove it first to change the quantity.')
      return
    }
    const prod = products.find(p => p.id === stdProductId)
    setDraftItems(d => [...d, {
      kind: 'standard',
      product_id: stdProductId,
      product_name: prod?.short_name || 'Unknown product',
      qty: stdQty,
      serial_number: null,
    }])
    setStdProductId('')
    setStdQty(1)
  }

  // ATOMIC server-side: transfer_number (ST-YYMMDD-XXXX) and created_by are generated/stamped
  // by the server — never sent from here.
  async function saveTransfer() {
    if (!transferForm.from_store_code || !transferForm.to_store_code) { alert('Pick both a source and a destination store.'); return }
    if (transferForm.from_store_code === transferForm.to_store_code) { alert('Source and destination must be different stores.'); return }
    if (draftItems.length === 0) { alert('Add at least one item to the transfer.'); return }
    setSaving(true)
    try {
      await api('/api/v1/pos/transfers', {
        method: 'POST',
        body: JSON.stringify({
          from_store_code: transferForm.from_store_code,
          to_store_code: transferForm.to_store_code,
          notes: transferForm.notes.trim() || null,
          items: draftItems.map(d => ({ product_id: d.product_id, qty: d.qty, serial_number: d.serial_number })),
        }),
      })
      setShowTransferForm(false)
      setDraftItems([])
      await loadTransfers()
    } catch (err: any) { alert('Could not create transfer: ' + (err?.message || err)) }
    setSaving(false)
  }

  async function shipTransfer(t: Transfer) {
    setTransferActionId(t.id)
    try {
      await api(`/api/v1/pos/transfers/${t.id}/ship`, { method: 'POST' })
      await loadTransfers()
      if (selectedTransfer?.id === t.id) {
        setSelectedTransfer(st => st ? { ...st, status: 'shipped', shipped_at: new Date().toISOString() } : st)
      }
    } catch (err: any) {
      const m = String(err?.message || err)
      // 403 → the caller's role lacks pos_inventory_adjust; server also enforces
      // pending-only + per-item stock checks — surface those messages as-is.
      alert(m.includes('does not allow') ? 'Your role does not allow shipping inventory.' : m)
    }
    setTransferActionId(null)
  }

  async function receiveTransfer(t: Transfer) {
    setTransferActionId(t.id)
    try {
      await api(`/api/v1/pos/transfers/${t.id}/receive`, { method: 'POST' })
      await loadTransfers()
      if (selectedTransfer?.id === t.id) {
        setSelectedTransfer(st => st ? { ...st, status: 'received', received_at: new Date().toISOString() } : st)
      }
    } catch (err: any) {
      const m = String(err?.message || err)
      alert(m.includes('does not allow') ? 'Your role does not allow receiving inventory.' : m)
    }
    setTransferActionId(null)
  }

  async function cancelTransfer(t: Transfer) {
    if (!confirm(`Cancel transfer ${t.transfer_number}? This cannot be undone.`)) return
    setTransferActionId(t.id)
    try {
      await api(`/api/v1/pos/transfers/${t.id}/cancel`, { method: 'POST' })
      await loadTransfers()
      if (selectedTransfer?.id === t.id) setSelectedTransfer(null)
    } catch (err: any) {
      const m = String(err?.message || err)
      alert(m.includes('only pending')
        ? 'Only pending transfers can be cancelled (it may have just been shipped).'
        : m.includes('does not allow') ? 'Your role does not allow cancelling transfers.' : m)
      await loadTransfers()
    }
    setTransferActionId(null)
  }

  // ==================== PURCHASE ORDERS ====================

  async function loadPurchaseOrders() {
    setPoLoading(true); setMsg('')
    try {
      const r = await api('/api/v1/pos/purchase-orders')
      setPurchaseOrders(r.purchase_orders || [])
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setPoLoading(false)
  }

  async function loadVendors() {
    try {
      const r = await api('/api/v1/pos/vendors?active_only=true')
      setVendors(r.vendors || [])
    } catch { /* vendor list is only needed for the Enter Order form */ }
  }

  // po_number + created_by are server-generated; new POs start as drafts.
  async function savePO() {
    setSaving(true)
    try {
      await api('/api/v1/pos/purchase-orders', {
        method: 'POST',
        body: JSON.stringify({
          vendor_id: poForm.vendor_id,
          order_date: poForm.order_date || null,
          expected_date: poForm.expected_date || null,
          notes: poForm.notes.trim() || null,
        }),
      })
      setShowPOForm(false)
      setPoForm({ vendor_id: '', order_date: localToday(), expected_date: '', notes: '' })
      await loadPurchaseOrders()
    } catch (err: any) { alert('Could not create PO: ' + (err?.message || err)) }
    setSaving(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📦 Inventory</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
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
          {tab === 'transfers' && <button className="btn btn-primary" onClick={openTransferForm}>+ New Transfer</button>}
          {tab === 'po' && <button className="btn btn-primary" onClick={() => setShowPOForm(true)}>+ New PO</button>}
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        {([['serial', '📱 Serialized'], ['standard', '📦 Standard'], ['transfers', '🚚 Transfers'], ['po', '📋 Purchase Orders']] as const).map(([key, lbl]) => (
          <button key={key} onClick={() => selectTab(key)} className={tab === key ? 'btn btn-primary' : 'btn btn-secondary'}>
            {lbl}
          </button>
        ))}
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

      {/* ===== TRANSFERS (Phase 2 — all inventory movement happens server-side on ship/receive) ===== */}
      {tab === 'transfers' && (
        <div>
          <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: 'var(--text2)', flex: 1 }}>
              Store transfers — ship from the source store while pending, then receive at the destination.
            </span>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>Records: {transfers.length}</span>
            <button className="btn btn-secondary" onClick={loadTransfers}>Refresh</button>
          </div>

          {transfersLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : (
            <div className="table-wrapper" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1000, fontSize: 13 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Transfer #', 'From → To', 'Items', 'Status', 'Created', 'Shipped', 'Received', 'Actions'].map(h =>
                    <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {transfers.map(t => (
                    <tr key={t.id} onClick={() => openTransferDetail(t)}
                      style={{ cursor: 'pointer', background: selectedTransfer?.id === t.id ? 'var(--surface2)' : 'transparent' }}>
                      <td style={{ ...cell, fontWeight: 600, fontFamily: 'monospace' }}>{t.transfer_number}</td>
                      <td style={cell}>{storeLabel(t.from_store_code)} <span style={{ color: 'var(--text3)' }}>→</span> {storeLabel(t.to_store_code)}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{t.item_count ?? 0}</td>
                      <td style={cell}>
                        <span style={{ ...badge, background: `${transferStatusColor(t.status)}20`, color: transferStatusColor(t.status) }}>{t.status}</span>
                      </td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{fmtDate(t.created_at)}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{fmtDate(t.shipped_at)}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{fmtDate(t.received_at)}</td>
                      <td style={cell} onClick={e => e.stopPropagation()}>
                        <div style={{ display: 'flex', gap: 6 }}>
                          {t.status === 'pending' && (
                            <>
                              <button disabled={transferActionId === t.id} onClick={() => shipTransfer(t)} style={{ ...smallBtn('#e67e22'), opacity: transferActionId === t.id ? 0.6 : 1 }}>
                                {transferActionId === t.id ? '...' : '📤 Ship'}
                              </button>
                              <button disabled={transferActionId === t.id} onClick={() => cancelTransfer(t)} style={{ ...smallBtn('#6b7280'), opacity: transferActionId === t.id ? 0.6 : 1 }}>Cancel</button>
                            </>
                          )}
                          {t.status === 'shipped' && (
                            <button disabled={transferActionId === t.id} onClick={() => receiveTransfer(t)} style={{ ...smallBtn('#27ae60'), opacity: transferActionId === t.id ? 0.6 : 1 }}>
                              {transferActionId === t.id ? '...' : '📥 Receive'}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {transfers.length === 0 && (
                    <tr><td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                      No transfers yet. Click “+ New Transfer” to move stock between stores.
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* Transfer detail */}
          {selectedTransfer && (
            <div style={{ ...panel, marginTop: 14, padding: 0, overflow: 'hidden' }}>
              <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 13, fontWeight: 700 }}>
                  {selectedTransfer.transfer_number} — {storeLabel(selectedTransfer.from_store_code)} → {storeLabel(selectedTransfer.to_store_code)}
                  <span style={{ ...badge, marginLeft: 10, background: `${transferStatusColor(selectedTransfer.status)}20`, color: transferStatusColor(selectedTransfer.status) }}>{selectedTransfer.status}</span>
                </span>
                <button onClick={() => setSelectedTransfer(null)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 16, cursor: 'pointer' }}>×</button>
              </div>
              {selectedTransfer.notes && (
                <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>📝 {selectedTransfer.notes}</div>
              )}
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Product', 'Type', 'Serial #', 'Qty'].map(h => <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {transferItemsLoading ? (
                    <tr><td colSpan={4} style={{ textAlign: 'center', padding: 20, color: 'var(--text3)' }}>Loading…</td></tr>
                  ) : transferItems.length === 0 ? (
                    <tr><td colSpan={4} style={{ textAlign: 'center', padding: 20, color: 'var(--text3)' }}>No items on this transfer.</td></tr>
                  ) : transferItems.map((it, i) => (
                    <tr key={i}>
                      <td style={{ ...cell, fontWeight: 500 }}>{it.product_name || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{it.serial_number ? '📱 Serialized' : '📦 Standard'}</td>
                      <td style={{ ...cell, fontFamily: 'monospace' }}>{it.serial_number || '—'}</td>
                      <td style={cell}>{it.qty ?? 1}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ===== PURCHASE ORDERS (Phase 2 — header-only, matching the standalone UI) ===== */}
      {tab === 'po' && (
        <div>
          {/* Workflow steps — decorative except Enter Order (Receive Items / Enter Bill land later) */}
          <div style={{ ...panel, marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, justifyContent: 'center', flexWrap: 'wrap' }}>
              {([
                { label: 'Enter Order', icon: '📝', step: 1, sub: undefined as string | undefined },
                { label: '→', icon: '', step: 0, sub: undefined },
                { label: 'Receive Items', icon: '📦', step: 2, sub: `(${purchaseOrders.filter(p => p.status === 'draft').length} Drafts)` },
                { label: '→', icon: '', step: 0, sub: undefined },
                { label: 'Enter Bill', icon: '💰', step: 3, sub: undefined },
              ]).map((s, i) => s.step === 0 ? (
                <span key={i} style={{ fontSize: 20, color: 'var(--text3)' }}>→</span>
              ) : (
                <div key={i} onClick={() => s.step === 1 && setShowPOForm(true)}
                  style={{ textAlign: 'center', cursor: s.step === 1 ? 'pointer' : 'default', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 24px', minWidth: 120 }}>
                  <div style={{ fontSize: 28, marginBottom: 6 }}>{s.icon}</div>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{s.label}</div>
                  {s.sub && <div style={{ fontSize: 11, color: '#3498db', marginTop: 4 }}>{s.sub}</div>}
                </div>
              ))}
            </div>
          </div>

          {poLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : (
            <div className="table-wrapper" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 800, fontSize: 13 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['PO #', 'Vendor', 'Date', 'Expected', 'Status', 'Total Cost'].map(h =>
                    <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {purchaseOrders.map(po => (
                    <tr key={po.id}>
                      <td style={{ ...cell, fontWeight: 600 }}>{po.po_number}</td>
                      <td style={cell}>{po.vendor_name || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{fmtDate(po.order_date)}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{fmtDate(po.expected_date)}</td>
                      <td style={cell}>
                        <span style={{ ...badge, background: `${poStatusColor(po.status)}20`, color: poStatusColor(po.status) }}>{po.status}</span>
                      </td>
                      <td style={{ ...cell, color: '#27ae60' }}>${Number(po.total_cost || 0).toFixed(2)}</td>
                    </tr>
                  ))}
                  {purchaseOrders.length === 0 && (
                    <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                      No purchase orders yet. Click “+ New PO” to create one.
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

      {/* New store transfer modal (draft item picking; save is atomic server-side) */}
      {showTransferForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 760, maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>🚚 New Store Transfer</b>
              <button onClick={() => setShowTransferForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 20, overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 16 }}>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={label}>From (source store) *</label>
                  <select value={transferForm.from_store_code} onChange={e => changeTransferSource(e.target.value)} style={input}>
                    <option value="">-- Select Store --</option>
                    {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}{s.store_code === activeStore ? ' (active)' : ''}</option>)}
                  </select>
                  <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 3 }}>Changing the source clears added items.</div>
                </div>
                <div>
                  <label style={label}>To (destination store) *</label>
                  <select value={transferForm.to_store_code} onChange={e => setTransferForm(f => ({ ...f, to_store_code: e.target.value }))} style={input}>
                    <option value="">-- Select Store --</option>
                    {stores.filter(s => s.store_code !== transferForm.from_store_code).map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
                  </select>
                </div>
              </div>

              {/* Add serialized unit */}
              <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 8 }}>📱 Add serialized unit (in stock at source)</div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input value={serialQuery} onChange={e => setSerialQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && searchSourceSerials()}
                    placeholder="Search serial # or IMEI…" style={{ ...input, flex: 1 }} />
                  <button className="btn btn-secondary" onClick={searchSourceSerials} disabled={serialSearching}>
                    {serialSearching ? '...' : 'Search'}
                  </button>
                </div>
                {serialResults.length > 0 && (
                  <div style={{ marginTop: 10, maxHeight: 160, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 6 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                      <tbody>
                        {serialResults.map(r => {
                          const added = draftItems.some(d => d.serial_number === r.serial_number)
                          return (
                            <tr key={r.id}>
                              <td style={{ ...cell, fontWeight: 500 }}>{r.product_name || '—'}</td>
                              <td style={{ ...cell, fontFamily: 'monospace' }}>{r.serial_number}</td>
                              <td style={{ ...cell, color: 'var(--text2)', fontFamily: 'monospace' }}>{r.imei || '—'}</td>
                              <td style={{ ...cell, color: 'var(--text2)' }}>{[r.color, r.storage].filter(Boolean).join(' / ') || '—'}</td>
                              <td style={{ ...cell, textAlign: 'right' }}>
                                <button onClick={() => addSerialToDraft(r)} disabled={added} style={{ ...smallBtn(added ? '#6b7280' : '#27ae60'), cursor: added ? 'default' : 'pointer' }}>{added ? 'Added' : '+ Add'}</button>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Add standard item */}
              <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 8 }}>📦 Add standard item (by quantity)</div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <select value={stdProductId} onChange={e => setStdProductId(e.target.value)} style={{ ...input, flex: 1 }}>
                    <option value="">-- Select Product --</option>
                    {products.filter(p => p.inventory_type !== 'serial').map(p => (
                      <option key={p.id} value={p.id}>{p.short_name} — {sourceQty[p.id] ?? 0} on hand</option>
                    ))}
                  </select>
                  <input type="number" min={1} value={stdQty} onChange={e => setStdQty(parseInt(e.target.value) || 1)} style={{ ...input, width: 80 }} />
                  <button className="btn btn-primary" onClick={addStandardToDraft}>+ Add</button>
                </div>
                {stdProductId && (
                  <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>
                    Available at source: <b style={{ color: (sourceQty[stdProductId] ?? 0) > 0 ? '#27ae60' : '#e74c3c' }}>{sourceQty[stdProductId] ?? 0}</b>
                  </div>
                )}
              </div>

              {/* Draft items */}
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 6 }}>Items on this transfer ({draftItems.length})</div>
                {draftItems.length === 0 ? (
                  <div style={{ padding: 14, border: '1px dashed var(--border)', borderRadius: 6, color: 'var(--text3)', fontSize: 12, textAlign: 'center' }}>No items added yet.</div>
                ) : (
                  <div style={{ border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                      <thead><tr style={{ background: 'var(--surface2)' }}>{['Product', 'Type', 'Serial #', 'Qty', ''].map((h, i) => <th key={i} style={th}>{h}</th>)}</tr></thead>
                      <tbody>
                        {draftItems.map((d, i) => (
                          <tr key={i}>
                            <td style={{ ...cell, fontWeight: 500 }}>{d.product_name}</td>
                            <td style={{ ...cell, color: 'var(--text2)' }}>{d.kind === 'serial' ? '📱 Serialized' : '📦 Standard'}</td>
                            <td style={{ ...cell, fontFamily: 'monospace' }}>{d.serial_number || '—'}</td>
                            <td style={cell}>{d.qty}</td>
                            <td style={{ ...cell, textAlign: 'right' }}>
                              <button onClick={() => setDraftItems(items => items.filter((_, j) => j !== i))} style={smallBtn('#e74c3c')}>Remove</button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div>
                <label style={label}>Notes</label>
                <textarea value={transferForm.notes} onChange={e => setTransferForm(f => ({ ...f, notes: e.target.value }))} style={{ ...input, height: 60, resize: 'none' }} />
              </div>
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowTransferForm(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={saveTransfer}
                disabled={saving || !transferForm.from_store_code || !transferForm.to_store_code || transferForm.from_store_code === transferForm.to_store_code || draftItems.length === 0}>
                {saving ? 'Saving…' : 'Create Transfer'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New purchase order modal (Enter Order) */}
      {showPOForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 500, overflow: 'hidden' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>📋 New Purchase Order</b>
              <button onClick={() => setShowPOForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label style={label}>Vendor *</label>
                <select value={poForm.vendor_id} onChange={e => setPoForm(f => ({ ...f, vendor_id: e.target.value }))} style={input}>
                  <option value="">-- Select Vendor --</option>
                  {vendors.map(v => <option key={v.id} value={v.id}>{v.legal_name}</option>)}
                </select>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div><label style={label}>Order Date</label><input type="date" value={poForm.order_date} onChange={e => setPoForm(f => ({ ...f, order_date: e.target.value }))} style={input} /></div>
                <div><label style={label}>Expected Date</label><input type="date" value={poForm.expected_date} onChange={e => setPoForm(f => ({ ...f, expected_date: e.target.value }))} style={input} /></div>
              </div>
              <div><label style={label}>Notes</label><textarea value={poForm.notes} onChange={e => setPoForm(f => ({ ...f, notes: e.target.value }))} style={{ ...input, height: 70, resize: 'none' }} /></div>
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowPOForm(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={savePO} disabled={saving || !poForm.vendor_id}>
                {saving ? 'Saving…' : 'Create PO'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
