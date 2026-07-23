'use client'
// Receiving — every open PO line (submitted / partially_received) with its remaining qty. Receive against
// a line: qty + date + optional per-unit IMEI/serial capture (capturing serials is what lets Sold Tally /
// Aging join a unit to raw_sales / ePay by IMEI at exact confidence instead of a qty-window estimate).
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, matchesStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import PoNav from '../_shared/PoNav'

type OpenLine = {
  po_id: string; po_number: string; status: string; ship_to_store: string | null; market: string | null
  order_date: string; expected_delivery_date: string | null; vendor_name: string | null
  po_line_id: string; sku: string | null; device_model: string
  qty_ordered: number; qty_received: number; remaining: number; unit_cost: number
}

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)', marginBottom: 16 }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13 }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13 }

export default function ReceivingPage() {
  const { user } = useAuth()
  const [rows, setRows] = useState<OpenLine[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [openRow, setOpenRow] = useState<string | null>(null)
  const [qty, setQty] = useState('')
  const [rdate, setRdate] = useState(localToday())
  const [rby, setRby] = useState('')
  const [notes, setNotes] = useState('')
  const [imeiText, setImeiText] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api('/api/v1/asset/po/open')
      setRows(d.rows || [])
      if (d.migrated === false) setMsg(d.note || 'Purchase Orders migration pending.')
    } catch (e: any) { setMsg('Could not load open purchase orders: ' + (e?.message || e)) }
    setLoading(false)
  }, [])
  useEffect(() => { load(); setRby(user?.full_name || user?.email || '') }, [load, user])

  const filtered = useMemo(() => rows.filter(r => matchesStandardFilter(r, filt, {
    store: r => r.ship_to_store, market: r => r.market,
  })), [rows, filt])

  function openReceive(r: OpenLine) {
    setOpenRow(r.po_line_id); setQty(String(r.remaining)); setRdate(localToday())
    setNotes(''); setImeiText('')
  }

  async function submitReceive(r: OpenLine) {
    const q = Math.max(1, parseInt(qty, 10) || 0)
    if (q <= 0) { setMsg('Enter a positive quantity.'); return }
    const units = imeiText.split('\n').map(s => s.trim()).filter(Boolean).map(s => ({ imei: s }))
    setBusy(true); setMsg('')
    try {
      await api(`/api/v1/asset/po/${r.po_id}/receive`, {
        method: 'POST',
        body: JSON.stringify({ po_line_id: r.po_line_id, qty_received: q, received_date: rdate, received_by: rby, notes, units }),
      })
      setMsg(`Received ${q} × ${r.device_model} against ${r.po_number}.`)
      setOpenRow(null)
      load()
    } catch (e: any) { setMsg('Could not record receipt: ' + (e?.message || e)) }
    setBusy(false)
  }

  const columns: ExportColumn[] = [
    { header: 'PO #', get: (r: OpenLine) => r.po_number },
    { header: 'Status', get: (r: OpenLine) => r.status },
    { header: 'Vendor', get: (r: OpenLine) => r.vendor_name || '—' },
    { header: 'Ship-To Store', get: (r: OpenLine) => r.ship_to_store || '—', role: 'store' },
    { header: 'Market', get: (r: OpenLine) => r.market || '—' },
    { header: 'Device Model', get: (r: OpenLine) => r.device_model },
    { header: 'Ordered', get: (r: OpenLine) => r.qty_ordered, type: 'number' },
    { header: 'Received', get: (r: OpenLine) => r.qty_received, type: 'number' },
    { header: 'Remaining', get: (r: OpenLine) => r.remaining, type: 'number' },
    { header: 'Expected Delivery', get: (r: OpenLine) => r.expected_delivery_date || '—', type: 'date' },
  ]
  const buildPayload = (): ExportPayload => ({
    title: 'Open Purchase Order Lines', filename: 'po_receiving_open',
    sheets: [{ name: 'Open Lines', columns, rows: filtered }],
  })

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>📥 Receiving</h1>
      <PoNav active="/commcalc/asset/purchase-orders/receiving" />
      {msg && <div style={{ ...card, background: 'var(--surface2)', fontSize: 13 }}>{msg}</div>}

      <div style={card}>
        <StandardFilterBar value={filt} onChange={setFilt}
          show={{ period: false, stores: true, markets: true, reps: false }}
          optionsUrl="/api/v1/core/filter-options"
          right={<>
            <ExportButtons payload={buildPayload} compact />
            <SendReportButton exportPayload={buildPayload} title="Open Purchase Order Lines" compact />
          </>} />
        {loading ? <p>Loading…</p> : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={th}>PO #</th><th style={th}>Store</th><th style={th}>Device Model</th>
              <th style={{ ...th, textAlign: 'right' }}>Remaining</th><th style={th}>Expected</th><th style={th} />
            </tr></thead>
            <tbody>
              {filtered.map(r => (
                <Fragment key={r.po_line_id}>
                  <tr>
                    <td style={td}>{r.po_number} <span style={{ color: 'var(--text3)', fontSize: 11 }}>({r.status})</span></td>
                    <td style={td}>{r.ship_to_store || '—'}</td>
                    <td style={td}>{r.device_model}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{r.remaining} / {r.qty_ordered}</td>
                    <td style={td}>{r.expected_delivery_date || '—'}</td>
                    <td style={td}>
                      <button className="btn btn-secondary" style={{ fontSize: 12 }}
                        onClick={() => openRow === r.po_line_id ? setOpenRow(null) : openReceive(r)}>
                        {openRow === r.po_line_id ? 'Cancel' : 'Receive'}
                      </button>
                    </td>
                  </tr>
                  {openRow === r.po_line_id && (
                    <tr>
                      <td colSpan={6} style={{ ...td, background: 'var(--surface2)' }}>
                        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                          <label style={{ fontSize: 12 }}>Qty received
                            <div><input type="number" min={1} max={r.remaining} style={sel} value={qty} onChange={e => setQty(e.target.value)} /></div>
                          </label>
                          <label style={{ fontSize: 12 }}>Received date
                            <div><input type="date" style={sel} value={rdate} onChange={e => setRdate(e.target.value)} /></div>
                          </label>
                          <label style={{ fontSize: 12 }}>Received by
                            <div><input style={sel} value={rby} onChange={e => setRby(e.target.value)} /></div>
                          </label>
                          <label style={{ fontSize: 12 }}>Notes
                            <div><input style={sel} value={notes} onChange={e => setNotes(e.target.value)} /></div>
                          </label>
                        </div>
                        <label style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                          IMEI / Serial per unit (optional — one per line; capturing these enables the exact-match Sold Tally / Aging join)
                          <div><textarea style={{ ...sel, width: '100%', minHeight: 60, fontFamily: 'monospace' }}
                            placeholder={'e.g.\n359123456789012\n359123456789013'}
                            value={imeiText} onChange={e => setImeiText(e.target.value)} /></div>
                        </label>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
                          {imeiText.split('\n').map(s => s.trim()).filter(Boolean).length} of {qty || 0} unit(s) will have a captured serial;
                          the remainder is still received (tracked at qty-level for aging/tally, at a lower "estimated" match confidence).
                        </div>
                        <button className="btn btn-primary" style={{ marginTop: 8 }} disabled={busy} onClick={() => submitReceive(r)}>
                          {busy ? 'Recording…' : 'Record receipt'}
                        </button>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {filtered.length === 0 && (
                <tr><td style={{ ...td, textAlign: 'center', color: 'var(--text3)' }} colSpan={6}>No open PO lines for this filter.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
