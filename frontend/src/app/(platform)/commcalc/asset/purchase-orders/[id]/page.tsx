'use client'
// One Purchase Order — header, line items (ordered vs received), receipts, and the status lifecycle
// (draft → submitted → partially_received → received → closed → cancelled).
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import PoNav from '../_shared/PoNav'

type Header = {
  id: string; po_number: string; order_date: string; status: string
  vendor_id: string | null; vendor_name_snapshot: string | null
  ship_to_store: string | null; market: string | null; buyer: string | null
  subtotal: number; total: number; expected_delivery_date: string | null; notes: string | null
  source: string; created_at: string
}
type Line = { id: string; sku: string | null; device_model: string; qty_ordered: number; unit_cost: number; extended_cost: number; qty_received: number }
type Receipt = { id: string; po_line_id: string; received_date: string; qty_received: number; received_by: string | null }

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)', marginBottom: 16 }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13 }
const NEXT: Record<string, string[]> = {
  draft: ['submitted', 'cancelled'],
  submitted: ['cancelled'],
  partially_received: ['closed'],
  received: ['closed'],
  closed: [], cancelled: [],
}

export default function PoDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params?.id as string
  const [header, setHeader] = useState<Header | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [vendor, setVendor] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api(`/api/v1/asset/po/${id}`)
      setHeader(d.header); setLines(d.lines || []); setReceipts(d.receipts || []); setVendor(d.vendor)
    } catch (e: any) { setMsg('Could not load PO: ' + (e?.message || e)) }
    setLoading(false)
  }, [id])
  useEffect(() => { if (id) load() }, [id, load])

  async function setStatus(s: string) {
    setBusy(true); setMsg('')
    try {
      await api(`/api/v1/asset/po/${id}`, { method: 'PATCH', body: JSON.stringify({ status: s }) })
      await load()
    } catch (e: any) { setMsg('Could not update status: ' + (e?.message || e)) }
    setBusy(false)
  }

  const columns: ExportColumn[] = [
    { header: 'SKU', get: (r: Line) => r.sku || '—' },
    { header: 'Device Model', get: (r: Line) => r.device_model },
    { header: 'Qty Ordered', get: (r: Line) => r.qty_ordered, type: 'number' },
    { header: 'Qty Received', get: (r: Line) => r.qty_received, type: 'number' },
    { header: 'Remaining', get: (r: Line) => r.qty_ordered - r.qty_received, type: 'number' },
    { header: 'Unit Cost', get: (r: Line) => r.unit_cost, money: true },
    { header: 'Extended Cost', get: (r: Line) => r.extended_cost, money: true },
  ]
  const buildPayload = (): ExportPayload => ({
    title: header ? `Purchase Order ${header.po_number}` : 'Purchase Order',
    filename: header ? `po_${header.po_number}` : 'purchase_order',
    sheets: [{ name: 'Lines', columns, rows: lines }],
  })

  if (loading) return <div style={{ padding: 20 }}>Loading…</div>
  if (!header) return (
    <div style={{ padding: 20 }}>
      <PoNav active="/commcalc/asset/purchase-orders" />
      <div style={card}>{msg || 'Purchase order not found.'}</div>
    </div>
  )

  const nextStatuses = NEXT[header.status] || []

  return (
    <div style={{ padding: 20, maxWidth: 1100, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>📦 {header.po_number}</h1>
      <PoNav active="/commcalc/asset/purchase-orders" />
      {msg && <div style={{ ...card, background: 'var(--surface2)', fontSize: 13 }}>{msg}</div>}

      <div style={card}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10, fontSize: 13 }}>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Status</div><strong>{header.status}</strong></div>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Order Date</div>{header.order_date}</div>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Vendor</div>{header.vendor_name_snapshot || '—'}</div>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Ship-To Store</div>{header.ship_to_store || '—'}</div>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Market</div>{header.market || '—'}</div>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Buyer</div>{header.buyer || '—'}</div>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Expected Delivery</div>{header.expected_delivery_date || '—'}</div>
          <div><div style={{ color: 'var(--text2)', fontSize: 11 }}>Total</div><strong>{fmt(header.total)}</strong></div>
        </div>
        {header.notes && <div style={{ marginTop: 10, fontSize: 13 }}><em>{header.notes}</em></div>}
        {vendor && (
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>
            Vendor contact: {vendor.contact_name || '—'} · {vendor.email || '—'} · {vendor.phone || '—'} · Terms: {vendor.terms || '—'}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          {nextStatuses.map(s => (
            <button key={s} className="btn btn-secondary" disabled={busy} onClick={() => setStatus(s)}>
              {s === 'submitted' ? 'Submit to vendor' : s === 'cancelled' ? 'Cancel PO' : s === 'closed' ? 'Close PO' : s}
            </button>
          ))}
          {(header.status === 'submitted' || header.status === 'partially_received') && (
            <Link href="/commcalc/asset/purchase-orders/receiving" className="btn btn-primary" style={{ textDecoration: 'none' }}>Go receive →</Link>
          )}
        </div>
      </div>

      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <h3 style={{ fontSize: 15 }}>Line Items</h3>
          <div style={{ display: 'flex', gap: 8 }}>
            <ExportButtons payload={buildPayload} compact />
            <SendReportButton exportPayload={buildPayload} title={`Purchase Order ${header.po_number}`} compact />
          </div>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            <th style={th}>SKU</th><th style={th}>Device Model</th>
            <th style={{ ...th, textAlign: 'right' }}>Ordered</th>
            <th style={{ ...th, textAlign: 'right' }}>Received</th>
            <th style={{ ...th, textAlign: 'right' }}>Remaining</th>
            <th style={{ ...th, textAlign: 'right' }}>Unit Cost</th>
            <th style={{ ...th, textAlign: 'right' }}>Extended</th>
          </tr></thead>
          <tbody>
            {lines.map(l => (
              <tr key={l.id}>
                <td style={td}>{l.sku || '—'}</td>
                <td style={td}>{l.device_model}</td>
                <td style={{ ...td, textAlign: 'right' }}>{l.qty_ordered}</td>
                <td style={{ ...td, textAlign: 'right' }}>{l.qty_received}</td>
                <td style={{ ...td, textAlign: 'right' }}>{l.qty_ordered - l.qty_received}</td>
                <td style={{ ...td, textAlign: 'right' }}>{fmt(l.unit_cost)}</td>
                <td style={{ ...td, textAlign: 'right' }}>{fmt(l.extended_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {receipts.length > 0 && (
        <div style={card}>
          <h3 style={{ fontSize: 15, marginBottom: 8 }}>Receiving History</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Received Date</th><th style={{ ...th, textAlign: 'right' }}>Qty</th><th style={th}>Received By</th></tr></thead>
            <tbody>
              {receipts.map(r => (
                <tr key={r.id}><td style={td}>{r.received_date}</td><td style={{ ...td, textAlign: 'right' }}>{r.qty_received}</td><td style={td}>{r.received_by || '—'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
