'use client'
// The DUPLICATE-IMEI pop-up (owner 2026-09-24: "duplicate entires of imei have to checked before they are received and
// should give a pop up"; index §11b). Shown when receiving (or editing) a serial unit answers 409 from THE landing guard:
// the unit(s) already on record with their status, and any sale / commission evidence that the device was already sold.
// "Receive anyway" re-sends with confirm_duplicate: true — the ledger records that it was received over the warning.
import type { CSSProperties } from 'react'

export type DuplicateCheck = {
  reasons: string[]
  keys: string[]
  already_sold: boolean
  sold_on: string | null
  customer: { name: string; mobile?: string | null; source: string } | null
  existing: { id: string; serial_number: string; imei: string | null; store_code: string | null; status: string; date_received: string | null; sold_at: string | null }[]
  sale: { net: number; last_sold_on: string | null; lines: { invoice: string | null; date: string | null; qty: number; customer: string | null }[] } | null
  commission: { net_earned: number; charged_back: number; kept: boolean; invoices: string[]; sold_on: string | null; customer_name: string | null } | null
}
export type DuplicateDetail = { code: string; message: string; check: DuplicateCheck }

const overlay: CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 210, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }
const card: CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 640, maxWidth: '100%', maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }
const cell: CSSProperties = { padding: '6px 10px', borderBottom: '1px solid var(--border)', fontSize: 12, whiteSpace: 'nowrap' }
const money = (n: number | null | undefined) => `$${Number(n || 0).toFixed(2)}`

export default function DuplicateModal({ detail, busy, onCancel, onConfirm }: {
  detail: DuplicateDetail; busy: boolean; onCancel: () => void; onConfirm: () => void
}) {
  const c = detail.check
  return (
    <div style={overlay}>
      <div style={card}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <b style={{ fontSize: 14, color: '#b45309' }}>⚠️ Duplicate IMEI</b>
          <button onClick={onCancel} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
        </div>
        <div style={{ padding: 20, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13 }}>
          <div>{detail.message}</div>
          {c.existing.length > 0 && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Already on record</div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr>{['Serial #', 'IMEI', 'Store', 'Status', 'Received'].map(h => <th key={h} style={{ ...cell, textAlign: 'left', color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {c.existing.map(u => (
                    <tr key={u.id}>
                      <td style={{ ...cell, fontFamily: 'monospace' }}>{u.serial_number}</td>
                      <td style={{ ...cell, fontFamily: 'monospace' }}>{u.imei || '—'}</td>
                      <td style={cell}>{u.store_code || '—'}</td>
                      <td style={cell}>{u.status.replace('_', ' ')}</td>
                      <td style={cell}>{u.date_received || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {c.already_sold && (
            <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px' }}>
              <b>Already sold</b>{c.sold_on ? ` on ${c.sold_on}` : ''}{c.customer ? ` to ${c.customer.name}` : ''}
              {c.customer ? <span style={{ color: 'var(--text2)' }}> (per the {c.customer.source === 'commission' ? 'commission report' : 'sales report'})</span> : null}
              {c.sale && <div style={{ color: 'var(--text2)', marginTop: 4 }}>Sales report: net {c.sale.net} unit(s){c.sale.lines.map(l => l.invoice).filter(Boolean).length ? ` — invoice ${c.sale.lines.map(l => l.invoice).filter(Boolean).join(', ')}` : ''}</div>}
              {c.commission && <div style={{ color: 'var(--text2)', marginTop: 4 }}>Commission: {money(c.commission.net_earned)} {c.commission.kept ? 'kept' : 'charged back'}{c.commission.invoices.length ? ` — invoice ${c.commission.invoices.join(', ')}` : ''}</div>}
            </div>
          )}
          <div style={{ color: 'var(--text2)', fontSize: 12 }}>
            Receive anyway only if this is a different physical unit. The receipt is recorded in the adjustment ledger as received over this warning, and the integrity scan will flag it until a manager verifies.
          </div>
        </div>
        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="btn btn-primary" onClick={onConfirm} disabled={busy}>{busy ? 'Saving…' : 'Receive anyway'}</button>
        </div>
      </div>
    </div>
  )
}
