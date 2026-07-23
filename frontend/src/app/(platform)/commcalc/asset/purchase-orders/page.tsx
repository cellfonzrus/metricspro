'use client'
// Proposed Purchase Orders — the buying workflow: pick store(s)/market to scope which stores'
// recommended-phones data is in view (RULE FIVE standard filter bar), review/edit the proposed qty per
// SKU per store (sourced from the Forecasting & Vendor Payables engine's underlying tables — see
// backend/app/modules/asset/purchase_orders.py's module docstring for why this reads those tables
// independently rather than calling payables' own /forecast endpoint in-process), then save as a real PO
// (vendor, dates, ship-to store, buyer — RULE THREE pick-don't-type throughout). "My Purchase Orders"
// below is a full ReportShell (RULE FOUR exports) over every PO already created.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import ReportShell from '@/components/ReportShell'
import PoNav from './_shared/PoNav'

type RecRow = {
  store: string; market: string | null; device_model: string
  units_sold: number; on_hand: number; avg_daily_velocity: number; projected_demand: number
  recommend_qty: number; suggested_unit_cost: number
}
type ProposalRow = {
  key: string; store: string; market: string | null; sku: string; device_model: string
  qty: number; unit_cost: number; source: 'forecast' | 'manual'
}
type Vendor = { id: string; name: string; contact_name: string | null; email: string | null; phone: string | null; terms: string | null; is_active: boolean }
type PoRow = {
  id: string; po_number: string; order_date: string; ship_to_store: string | null; market: string | null
  vendor_name_snapshot: string | null; status: string; total: number; buyer: string | null
  expected_delivery_date: string | null
}

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)', marginBottom: 16 }
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13 }
const numInput: React.CSSProperties = { width: 76, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, textAlign: 'right' }

export default function PurchaseOrdersHubPage() {
  const { user } = useAuth()
  const buyerName = user?.full_name || user?.email || ''

  // RULE FIVE — period isn't meaningful here (lookback/horizon drive the recommendation window instead,
  // appended via `right`); reps aren't a dimension of a purchase order. Store(s)/market ARE core and drive
  // which stores' recommendations are in scope, per the task spec.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [lookback, setLookback] = useState(7)
  const [horizon, setHorizon] = useState(14)

  const [rows, setRows] = useState<ProposalRow[]>([])
  const [recLoading, setRecLoading] = useState(false)
  const [msg, setMsg] = useState('')

  // manual add-item row
  const [modelOptions, setModelOptions] = useState<EntityOption[]>([])
  const [addModel, setAddModel] = useState<string | null>(null)
  const [addStore, setAddStore] = useState<string | null>(null)
  const [addQty, setAddQty] = useState('1')
  const [addCost, setAddCost] = useState('0')

  // save-as-PO panel
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [vendorId, setVendorId] = useState<string | null>(null)
  const [shipToStore, setShipToStore] = useState<string | null>(null)
  const [orderDate, setOrderDate] = useState(localToday())
  const [expectedDate, setExpectedDate] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [lastCreated, setLastCreated] = useState<{ id: string; po_number: string } | null>(null)

  // existing POs
  const [pos, setPos] = useState<PoRow[]>([])
  const [posLoading, setPosLoading] = useState(true)

  const loadStatic = useCallback(async () => {
    try {
      const [mo, v] = await Promise.all([
        api('/api/v1/asset/po/model-options').catch(() => ({ models: [] })),
        api('/api/v1/asset/po/vendors').catch(() => ({ rows: [] })),
      ])
      setModelOptions((mo.models || []).map((m: string) => ({ id: m, label: m })))
      setVendors(v.rows || [])
    } catch { /* non-fatal */ }
  }, [])

  const loadPos = useCallback(async () => {
    setPosLoading(true)
    try {
      const d = await api('/api/v1/asset/po')
      setPos(d.rows || [])
      if (d.migrated === false) setMsg(d.note || 'Purchase Orders migration pending.')
    } catch (e: any) { setMsg('Could not load purchase orders: ' + (e?.message || e)) }
    setPosLoading(false)
  }, [])

  useEffect(() => { loadStatic(); loadPos() }, [loadStatic, loadPos])

  const loadRecs = useCallback(async () => {
    setRecLoading(true)
    setMsg('')
    try {
      const storesQs = filt.stores.length ? `&stores=${encodeURIComponent(filt.stores.join(','))}` : ''
      const d = await api(`/api/v1/asset/po/recommendations?lookback=${lookback}&horizon=${horizon}${storesQs}`)
      let recs: RecRow[] = d.rows || []
      if (filt.markets.length) {
        const mk = new Set(filt.markets.map(m => m.toLowerCase()))
        recs = recs.filter(r => r.market && mk.has(r.market.toLowerCase()))
      }
      const proposal: ProposalRow[] = recs
        .filter(r => r.recommend_qty > 0)
        .map(r => ({
          key: `${r.store}__${r.device_model}`, store: r.store, market: r.market,
          sku: '', device_model: r.device_model, qty: r.recommend_qty,
          unit_cost: r.suggested_unit_cost || 0, source: 'forecast',
        }))
      setRows(proposal)
      if (proposal.length === 0) setMsg('No recommended phones for this scope/window — nothing over on-hand demand, or no sales data yet.')
    } catch (e: any) { setMsg('Could not load recommendations: ' + (e?.message || e)) }
    setRecLoading(false)
  }, [filt.stores, filt.markets, lookback, horizon])

  useEffect(() => { loadRecs() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const storeOptions: EntityOption[] = useMemo(
    () => Array.from(new Set(rows.map(r => r.store))).sort().map(s => ({ id: s, label: s })),
    [rows],
  )

  function setQty(key: string, qty: number) {
    setRows(rs => rs.map(r => r.key === key ? { ...r, qty: Math.max(0, qty) } : r))
  }
  function setCost(key: string, cost: number) {
    setRows(rs => rs.map(r => r.key === key ? { ...r, unit_cost: Math.max(0, cost) } : r))
  }
  function removeRow(key: string) {
    setRows(rs => rs.filter(r => r.key !== key))
  }
  function addManualRow() {
    if (!addModel) { setMsg('Pick (or type + create) a device model first.'); return }
    const qty = Math.max(1, parseInt(addQty, 10) || 1)
    const cost = Math.max(0, parseFloat(addCost) || 0)
    const store = addStore || filt.stores[0] || 'Unknown'
    setRows(rs => [...rs, {
      key: `manual_${Date.now()}`, store, market: null, sku: '', device_model: addModel,
      qty, unit_cost: cost, source: 'manual',
    }])
    setAddModel(null); setAddQty('1'); setAddCost('0')
  }

  const activeRows = rows.filter(r => r.qty > 0)
  const shipRows = shipToStore ? activeRows.filter(r => r.store === shipToStore) : []
  const shipSubtotal = shipRows.reduce((s, r) => s + r.qty * r.unit_cost, 0)

  const buildPayload = (): ExportPayload => ({
    title: 'Proposed Purchase Order', filename: 'proposed_purchase_order',
    sheets: [{
      name: 'Proposal',
      columns: [
        { header: 'Store', get: (r: ProposalRow) => r.store },
        { header: 'Device Model', get: (r: ProposalRow) => r.device_model },
        { header: 'Order Qty', get: (r: ProposalRow) => r.qty, type: 'number' },
        { header: 'Unit Cost', get: (r: ProposalRow) => r.unit_cost, money: true },
        { header: 'Extended Cost', get: (r: ProposalRow) => r.qty * r.unit_cost, money: true },
        { header: 'Source', get: (r: ProposalRow) => r.source === 'forecast' ? 'Recommended' : 'Manual' },
      ],
      rows: activeRows,
    }],
  })

  async function createPo() {
    if (!shipToStore) { setMsg('Pick a ship-to store first — a PO ships to one location.'); return }
    if (shipRows.length === 0) { setMsg(`No non-zero line items for ${shipToStore} — adjust quantities or pick a different ship-to store.`); return }
    setSaving(true); setMsg('')
    try {
      const body = {
        vendor_id: vendorId, order_date: orderDate, ship_to_store: shipToStore,
        market: shipRows[0].market || filt.markets[0] || null,
        buyer: buyerName, expected_delivery_date: expectedDate || null, notes: notes || null,
        source: 'forecast', status: 'draft',
        lines: shipRows.map(r => ({
          sku: r.sku || null, device_model: r.device_model, qty_ordered: r.qty, unit_cost: r.unit_cost,
          store: shipToStore, market: shipRows[0].market || filt.markets[0] || null,
        })),
      }
      const res = await api('/api/v1/asset/po', { method: 'POST', body: JSON.stringify(body) })
      setLastCreated({ id: res.id, po_number: res.po_number })
      setMsg(`Created ${res.po_number} for ${shipToStore} (${shipRows.length} line item${shipRows.length === 1 ? '' : 's'}).`)
      setRows(rs => rs.filter(r => r.store !== shipToStore || r.qty === 0))
      loadPos()
    } catch (e: any) { setMsg('Could not create PO: ' + (e?.message || e)) }
    setSaving(false)
  }

  const poColumns: ExportColumn[] = [
    { header: 'PO #', get: (r: PoRow) => r.po_number },
    { header: 'Order Date', get: (r: PoRow) => r.order_date, type: 'date' },
    { header: 'Vendor', get: (r: PoRow) => r.vendor_name_snapshot || '—' },
    { header: 'Ship-To Store', get: (r: PoRow) => r.ship_to_store || '—', role: 'store' },
    { header: 'Market', get: (r: PoRow) => r.market || '—' },
    { header: 'Status', get: (r: PoRow) => r.status },
    { header: 'Buyer', get: (r: PoRow) => r.buyer || '—' },
    { header: 'Expected Delivery', get: (r: PoRow) => r.expected_delivery_date || '—', type: 'date' },
    { header: 'Total', get: (r: PoRow) => r.total, money: true },
  ]

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>📦 Purchase Orders</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        Generate a proposed order from recommended phones, review/edit it, and save it as a real purchase order.
      </p>
      <PoNav active="/commcalc/asset/purchase-orders" />

      {msg && <div style={{ ...card, background: 'var(--surface2)', fontSize: 13 }}>{msg}</div>}
      {lastCreated && (
        <div style={{ ...card, borderColor: '#16a34a' }}>
          ✅ <strong>{lastCreated.po_number}</strong> created.{' '}
          <Link href={`/commcalc/asset/purchase-orders/${lastCreated.id}`}>View / submit it →</Link>
        </div>
      )}

      <div style={card}>
        <StandardFilterBar
          value={filt} onChange={setFilt}
          show={{ period: false, stores: true, markets: true, reps: false }}
          optionsUrl="/api/v1/core/filter-options"
          right={(
            <>
              <label style={{ fontSize: 12, color: 'var(--text2)' }}>Lookback (days){' '}
                <input type="number" min={1} max={365} style={numInput} value={lookback}
                  onChange={e => setLookback(Math.max(1, parseInt(e.target.value, 10) || 1))} /></label>
              <label style={{ fontSize: 12, color: 'var(--text2)' }}>Horizon (days){' '}
                <input type="number" min={1} max={365} style={numInput} value={horizon}
                  onChange={e => setHorizon(Math.max(1, parseInt(e.target.value, 10) || 1))} /></label>
              <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={loadRecs} disabled={recLoading}>
                {recLoading ? 'Loading…' : '↻ Refresh Recommendations'}
              </button>
            </>
          )}
        />
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
          Recommended qty = projected demand (recent sales velocity × horizon) minus current on-hand,
          per store × device model. Edit any Order Qty / Unit Cost below, remove rows you don't want, or add
          an item manually. Refreshing recommendations replaces unsaved edits.
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 10, padding: '8px 0', borderBottom: '1px dashed var(--border)' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Add item — Device model
            <div><EntityPicker options={modelOptions} value={addModel} onChange={setAddModel}
              allowCreate onCreate={(v) => setAddModel(v)} placeholder="Model…" width={200} /></div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Store
            <div><EntityPicker options={storeOptions.length ? storeOptions : (filt.stores.map(s => ({ id: s, label: s })))}
              value={addStore} onChange={setAddStore} placeholder="Store…" width={180} /></div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Qty
            <div><input type="number" min={1} style={numInput} value={addQty} onChange={e => setAddQty(e.target.value)} /></div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Unit Cost
            <div><input type="number" min={0} step="0.01" style={numInput} value={addCost} onChange={e => setAddCost(e.target.value)} /></div>
          </label>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={addManualRow}>+ Add to proposal</button>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 6 }}>
          <ExportButtons payload={buildPayload} compact />
          <SendReportButton exportPayload={buildPayload} title="Proposed Purchase Order" compact />
        </div>
        <div className="table-wrapper" style={{ maxHeight: 420, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={th}>Store</th><th style={th}>Device Model</th>
              <th style={{ ...th, textAlign: 'right' }}>Order Qty</th>
              <th style={{ ...th, textAlign: 'right' }}>Unit Cost</th>
              <th style={{ ...th, textAlign: 'right' }}>Extended</th>
              <th style={th}>Source</th><th style={th} />
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.key}>
                  <td style={td}>{r.store}</td>
                  <td style={td}>{r.device_model}</td>
                  <td style={{ ...td, textAlign: 'right' }}>
                    <input type="number" min={0} style={numInput} value={r.qty}
                      onChange={e => setQty(r.key, parseInt(e.target.value, 10) || 0)} /></td>
                  <td style={{ ...td, textAlign: 'right' }}>
                    <input type="number" min={0} step="0.01" style={numInput} value={r.unit_cost}
                      onChange={e => setCost(r.key, parseFloat(e.target.value) || 0)} /></td>
                  <td style={{ ...td, textAlign: 'right' }}>{fmt(r.qty * r.unit_cost)}</td>
                  <td style={td}>{r.source === 'forecast' ? 'Recommended' : 'Manual'}</td>
                  <td style={td}><button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 7px', color: '#dc2626' }} onClick={() => removeRow(r.key)}>✕</button></td>
                </tr>
              ))}
              {rows.length === 0 && !recLoading && (
                <tr><td style={{ ...td, textAlign: 'center', color: 'var(--text3)' }} colSpan={7}>No proposed items — adjust the filters/window or add one manually above.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div style={card}>
        <h3 style={{ fontSize: 15, marginBottom: 10 }}>Save as Purchase Order</h3>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Ship-to store (required)
            <div><EntityPicker options={storeOptions} value={shipToStore} onChange={setShipToStore} placeholder="Store…" width={200} /></div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Vendor
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <EntityPicker options={vendors.map(v => ({ id: v.id, label: v.name, sublabel: v.email || undefined }))}
                value={vendorId} onChange={setVendorId} placeholder="Vendor…" width={200} />
              <Link href="/commcalc/asset/purchase-orders/vendors" style={{ fontSize: 11 }}>+ new</Link>
            </div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Order date
            <div><input type="date" style={sel} value={orderDate} onChange={e => setOrderDate(e.target.value)} /></div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Expected delivery
            <div><input type="date" style={sel} value={expectedDate} onChange={e => setExpectedDate(e.target.value)} /></div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Buyer
            <div><input style={sel} value={buyerName} disabled title="From your signed-in account" /></div>
          </label>
        </div>
        <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginTop: 8 }}>Notes
          <div><textarea style={{ ...sel, width: '100%', minHeight: 50 }} value={notes} onChange={e => setNotes(e.target.value)} /></div>
        </label>
        {shipToStore && (
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
            {shipRows.length} line item{shipRows.length === 1 ? '' : 's'} for <strong>{shipToStore}</strong> · subtotal {fmt(shipSubtotal)}
            {activeRows.length > shipRows.length && <> — the other {activeRows.length - shipRows.length} row(s) for other stores stay in the proposal; save again after picking their store.</>}
          </div>
        )}
        <button className="btn btn-primary" style={{ marginTop: 10 }} onClick={createPo} disabled={saving || !shipToStore}>
          {saving ? 'Creating…' : 'Create Purchase Order (draft)'}
        </button>
      </div>

      <div style={card}>
        <h3 style={{ fontSize: 15, marginBottom: 10 }}>My Purchase Orders</h3>
        {posLoading ? <p>Loading…</p> : (
          <ReportShell
            title="Purchase Orders" filename="purchase_orders"
            columns={poColumns}
            rows={pos}
            onRowClick={(r: PoRow) => { window.location.href = `/commcalc/asset/purchase-orders/${r.id}` }}
          />
        )}
      </div>
    </div>
  )
}
