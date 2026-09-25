'use client'
// Supply → Orders (index §36). Each supply order is a purchase order (the Purchase Orders module's record,
// one per vendor of a placed cart). From here the order is placed AT the vendor:
//   1. "Open vendor window" signs in to the vendor in a live browser and — when the vendor's portal config
//      has an ordering recipe — adds every line to the vendor's cart and opens the review page, capturing
//      the vendor's own cart total and a screenshot. It never submits.
//   2. The human places the order: in the live window (the vendor's own button), or — only for a vendor whose
//      recipe has a submit step — with "Confirm & place" here, after seeing the vendor's total.
//   3. "Capture confirmation" reads the vendor's confirmation page (pattern from config) and writes the
//      order number, total and screenshot back; the order becomes submitted. Or type the number by hand.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import LiveVendorWindow from '@/components/supply/LiveVendorWindow'
import { panel, input, btn, btnPrimary, btnDanger, th, cell, fmtMoney, fmtDate, PO_STATUS_COLOR } from '@/lib/supply'

export default function SupplyOrdersPage() {
  const [rows, setRows] = useState<any[]>([])
  const [note, setNote] = useState('')
  const [cartRef, setCartRef] = useState('')
  const [sel, setSel] = useState<any>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [live, setLive] = useState<string | null>(null)
  const [manual, setManual] = useState({ ref: '', total: '', note: '' })

  const load = useCallback(async () => {
    try {
      const r: any = await api('/api/v1/supply/orders')
      setRows(r.rows || []); setNote(r.migrated === false ? r.note : '')
    } catch (e: any) { setNote(e?.message || String(e)) }
  }, [])
  const open = useCallback(async (id: string) => {
    setMsg('')
    try {
      const r: any = await api(`/api/v1/supply/orders/${id}`)
      setSel(r); setManual({ ref: '', total: '', note: '' })
      setLive(r.live?.for_this_order ? r.live.sid : null)   // re-attach to a vendor window still open for this order
    }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }, [])
  useEffect(() => {
    load()
    try { setCartRef(new URLSearchParams(window.location.search).get('cart') || '') } catch { /* no query */ }
  }, [load])

  const h = sel?.header
  async function openSession() {
    setBusy(true); setMsg('')
    try {
      const r: any = await api(`/api/v1/supply/orders/${h.id}/open-session`, { method: 'POST', body: '{}' })
      if (r.blocked || r.requires_confirm || r.phase === 'route_disabled') setMsg('⚠️ ' + (r.message || 'The vendor login is paused.'))
      else { setLive(r.sid); setMsg(r.message) }
    } catch (e: any) {
      setMsg('❌ ' + (e?.status === 503
        ? 'This server cannot open a browser (the live vendor window runs on the browser worker, which is not configured here). Place the order on the vendor\'s site and type the confirmation number below.'
        : (e?.message || e)))
    }
    setBusy(false)
  }
  async function capture(path: 'capture' | 'submit') {
    if (path === 'submit') {
      const ev = h.cart_evidence || {}
      const vt = ev.vendor_cart_total != null ? fmtMoney(ev.vendor_cart_total) : 'not found on the page'
      if (!confirm(`Place this order at ${h.vendor_name_snapshot}?\n\nVendor's cart total: ${vt}\nOur estimate: ${fmtMoney(h.total)}\n\nThis presses the vendor's final order button.`)) return
    }
    setBusy(true); setMsg('')
    try {
      const r: any = await api(`/api/v1/supply/orders/${h.id}/${path}`, { method: 'POST', body: '{}' })
      setMsg(r.confirmed ? `✅ ${r.message || 'Confirmation captured.'}${r.needs_ref ? ' Type the order number below.' : ''}` : `ℹ️ ${r.message}`)
      await open(h.id); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  async function saveManual() {
    setBusy(true); setMsg('')
    try {
      await api(`/api/v1/supply/orders/${h.id}/confirm-manual`, { method: 'POST',
        body: JSON.stringify({ vendor_order_ref: manual.ref, vendor_order_total: manual.total || null, note: manual.note || null }) })
      setMsg('✅ Confirmation recorded.'); await open(h.id); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  async function cancelOrder() {
    if (!confirm('Cancel this purchase order? (It does not cancel anything already placed at the vendor.)')) return
    try { await api(`/api/v1/supply/orders/${h.id}/status`, { method: 'POST', body: JSON.stringify({ status: 'cancelled' }) }); await open(h.id); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const shown = cartRef ? rows.filter(r => r.supply_cart_ref === cartRef) : rows
  const ev = h?.cart_evidence
  const conf = h?.confirmation
  return (
    <div style={{ padding: 20, maxWidth: 1300 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Supply orders</h1>
      <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 12px' }}>
        One purchase order per vendor. Open an order to place it at the vendor and capture the confirmation.
        {cartRef && <> Showing the cart you just placed — <button style={{ ...btn, padding: '2px 8px' }} onClick={() => setCartRef('')}>show all</button></>}
      </p>
      {note && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 12, fontSize: 13 }}>{note}</div>}
      <section style={{ ...panel, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr><th style={th}>PO</th><th style={th}>Vendor</th><th style={th}>Status</th><th style={th}>Our total</th>
            <th style={th}>Vendor total</th><th style={th}>Vendor order #</th><th style={th}>Submitted</th></tr></thead>
          <tbody>
            {shown.map(r => (
              <tr key={r.id} onClick={() => open(r.id)} style={{ cursor: 'pointer', background: h?.id === r.id ? 'rgba(37,99,235,.06)' : undefined }}>
                <td style={cell}>{r.po_number}</td>
                <td style={cell}>{r.vendor_name_snapshot}</td>
                <td style={cell}><span style={{ color: PO_STATUS_COLOR[r.status] || '#6b7280', fontWeight: 600 }}>{r.status}</span></td>
                <td style={cell}>{fmtMoney(r.total)}{r.shipping_estimate ? <span style={{ fontSize: 11, color: 'var(--text2)' }}> (incl. {fmtMoney(r.shipping_estimate)} ship)</span> : null}</td>
                <td style={cell}>{r.vendor_order_total != null ? fmtMoney(r.vendor_order_total) : '—'}</td>
                <td style={cell}>{r.vendor_order_ref || '—'}</td>
                <td style={cell}>{r.submitted_at ? `${fmtDate(r.submitted_at)}${r.submitted_by ? ` · ${r.submitted_by}` : ''}` : '—'}</td>
              </tr>
            ))}
            {!shown.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={7}>No supply orders yet.</td></tr>}
          </tbody>
        </table>
      </section>

      {h && (
        <section style={{ ...panel, marginTop: 14 }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>{h.po_number} · {h.vendor_name_snapshot}</h2>
            <span style={{ color: PO_STATUS_COLOR[h.status], fontWeight: 600 }}>{h.status}</span>
            <span style={{ fontSize: 12, color: 'var(--text2)' }}>Ordering: {sel.recipe?.text}</span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 8 }}>
            <thead><tr><th style={th}>#</th><th style={th}>Item</th><th style={th}>SKU</th><th style={th}>Qty (packs)</th><th style={th}>Unit</th><th style={th}>Line</th></tr></thead>
            <tbody>{sel.lines.map((l: any) => (
              <tr key={l.id}><td style={cell}>{l.line_no}</td><td style={cell}>{l.device_model}{l.notes && <div style={{ fontSize: 11, color: '#b45309' }}>{l.notes}</div>}</td>
                <td style={cell}>{l.sku || '—'}</td><td style={cell}>{l.qty_ordered}</td><td style={cell}>{fmtMoney(l.unit_cost)}</td><td style={cell}>{fmtMoney(l.extended_cost)}</td></tr>
            ))}</tbody>
          </table>
          <div style={{ fontSize: 13, marginTop: 8 }}>
            Items {fmtMoney(h.subtotal)} · shipping estimate {fmtMoney(h.shipping_estimate)} · <strong>total {fmtMoney(h.total)}</strong>
            {h.supply_meta?.to_free_shipping != null && <span style={{ color: '#2563eb' }}> · {fmtMoney(h.supply_meta.to_free_shipping)} short of free shipping</span>}
          </div>

          {['draft', 'submitted'].includes(h.status) && (
            <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
              <button style={btnPrimary} disabled={busy} onClick={openSession}>Open vendor window</button>
              <button style={btn} disabled={busy || !live} onClick={() => capture('capture')}>Capture confirmation</button>
              {sel.can_submit && <button style={btn} disabled={busy || !live || !ev?.captured_at} onClick={() => capture('submit')}>Confirm &amp; place at vendor</button>}
              {h.status === 'draft' && <button style={btnDanger} disabled={busy} onClick={cancelOrder}>Cancel PO</button>}
            </div>
          )}
          {msg && <div style={{ fontSize: 13, marginTop: 8 }}>{msg}</div>}

          {ev && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginTop: 12 }}>
              <strong style={{ fontSize: 13 }}>Vendor cart (captured {fmtDate(ev.captured_at)})</strong>
              <div style={{ fontSize: 13 }}>{ev.status}</div>
              <div style={{ fontSize: 13 }}>Vendor&apos;s total: <strong>{ev.vendor_cart_total != null ? fmtMoney(ev.vendor_cart_total) : 'not found on the page'}</strong> vs our estimate {fmtMoney(h.total)}</div>
              {(ev.lines_failed || []).map((f: any) => <div key={f.line_no} style={{ fontSize: 12, color: '#dc2626' }}>Line {f.line_no} not added: {f.error}</div>)}
              {ev.shot && <img alt="Vendor cart as captured" src={`data:image/jpeg;base64,${ev.shot}`} style={{ maxWidth: 480, width: '100%', marginTop: 6, border: '1px solid var(--border)' }} />}
            </div>
          )}
          {conf && (
            <div style={{ border: '1px solid #16a34a', borderRadius: 8, padding: 10, marginTop: 12 }}>
              <strong style={{ fontSize: 13 }}>Confirmation ({conf.method}) — {fmtDate(conf.captured_at)}{conf.captured_by ? ` · ${conf.captured_by}` : ''}</strong>
              <div style={{ fontSize: 13 }}>Order #: <strong>{conf.order_ref || h.vendor_order_ref || 'not captured'}</strong>
                {conf.total != null && <> · total {fmtMoney(conf.total)}</>}{conf.note && <> · {conf.note}</>}</div>
              {conf.shot && <img alt="Vendor confirmation page" src={`data:image/jpeg;base64,${conf.shot}`} style={{ maxWidth: 480, width: '100%', marginTop: 6, border: '1px solid var(--border)' }} />}
            </div>
          )}
          {['draft', 'submitted'].includes(h.status) && (
            <div style={{ border: '1px dashed var(--border)', borderRadius: 8, padding: 10, marginTop: 12 }}>
              <strong style={{ fontSize: 13 }}>Placed it another way? Type the vendor&apos;s confirmation</strong>
              <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
                <input style={{ ...input, maxWidth: 200 }} placeholder="Order / confirmation #" value={manual.ref} onChange={e => setManual(m => ({ ...m, ref: e.target.value }))} />
                <input style={{ ...input, maxWidth: 140 }} placeholder="Total $ (optional)" inputMode="decimal" value={manual.total} onChange={e => setManual(m => ({ ...m, total: e.target.value }))} />
                <input style={{ ...input, maxWidth: 260 }} placeholder="Note (optional)" value={manual.note} onChange={e => setManual(m => ({ ...m, note: e.target.value }))} />
                <button style={btn} disabled={busy || !manual.ref.trim()} onClick={saveManual}>Save confirmation</button>
              </div>
            </div>
          )}
          {live && <LiveVendorWindow sid={live} title={`${h.vendor_name_snapshot} — live window`} onClose={() => { setLive(null); open(h.id) }} />}
        </section>
      )}
    </div>
  )
}
