'use client'
// MANUAL ADJUST IN / OUT (owner 2026-09-24: "we need to make a mechanish of manually adjusting the inventory in or out of the
// system"; index §11b). A unit in stock can be adjusted OUT, any other unit adjusted back IN; the reason decides the status
// it lands in (server-side, inventory_integrity.ADJUST_REASONS) and every adjustment writes a row to the adjustment ledger
// (who, why, from → to). Needs the pos_inventory_adjust permission — the server refuses otherwise.
import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '@/lib/client'

type LedgerRow = { id: string; direction: string; reason: string; from_status: string | null; to_status: string | null; note: string | null; created_by: string | null; created_at: string }
export type AdjustUnit = { id: string; serial_number: string; imei: string | null; status: string; store_code: string | null; product_name?: string | null }

const LIVE = ['in_stock', 'in_transit']
// The reasons are the SERVER's (GET /pos/inventory/adjustments → reasons — inventory_integrity.ADJUST_REASONS); these labels
// are only how a known reason reads here — an unknown one shows as its key.
const LABELS: Record<string, string> = {
  sold: 'Sold (no receipt was rung)', lost: 'Lost', stolen: 'Stolen', rma: 'Sent for RMA / repair',
  duplicate_record: 'Duplicate record of the same device', damaged_write_off: 'Damaged — write off',
  count_correction: 'Count correction', found: 'Found on the shelf', customer_return: 'Customer return',
  rma_returned: 'Back from RMA / repair', reversal: 'Reverse an earlier adjustment',
}
const overlay: CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }
const input: CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }

export default function AdjustModal({ unit, flagId, onClose, onDone }: {
  unit: AdjustUnit; flagId?: string | null; onClose: () => void; onDone: (msg: string) => void
}) {
  const direction: 'in' | 'out' = LIVE.includes(unit.status) ? 'out' : 'in'
  const [list, setList] = useState<string[]>([])
  const [history, setHistory] = useState<LedgerRow[]>([])
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api(`/api/v1/pos/inventory/adjustments?unit_id=${encodeURIComponent(unit.id)}&limit=10`)
      .then(r => {
        const l: string[] = r.reasons?.[direction] || []
        setList(l); setReason(l[0] || ''); setHistory(r.rows || [])
      })
      .catch((e: any) => setErr('Could not load the adjustment reasons: ' + String(e?.message || e)))
  }, [unit.id, direction])

  async function save() {
    setBusy(true); setErr('')
    try {
      await api('/api/v1/pos/inventory/adjust', {
        method: 'POST',
        body: JSON.stringify({ unit_id: unit.id, direction, reason, note: note.trim() || null, flag_id: flagId || null }),
      })
      onDone(`Adjusted ${direction.toUpperCase()}: ${unit.serial_number} (${LABELS[reason] || reason}) — recorded in the adjustment ledger.`)
    } catch (e: any) {
      const m = String(e?.message || e)
      setErr(m.includes('does not allow') ? 'Your role does not allow adjusting inventory (pos_inventory_adjust).' : m)
    }
    setBusy(false)
  }

  return (
    <div style={overlay}>
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 520, maxWidth: '100%', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <b style={{ fontSize: 14 }}>{direction === 'out' ? '📤 Adjust OUT of inventory' : '📥 Adjust IN to inventory'}</b>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
        </div>
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13 }}>
          <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px' }}>
            {unit.product_name || 'Unit'} · <span style={{ fontFamily: 'monospace' }}>{unit.serial_number}</span>
            {unit.imei && unit.imei !== unit.serial_number ? <> · IMEI <span style={{ fontFamily: 'monospace' }}>{unit.imei}</span></> : null}
            <br /><span style={{ color: 'var(--text2)' }}>Store {unit.store_code || '—'} · now <b>{unit.status.replace('_', ' ')}</b></span>
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 3 }}>Reason *</label>
            <select value={reason} onChange={e => setReason(e.target.value)} style={input}>
              {list.map(r => <option key={r} value={r}>{LABELS[r] || r}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 3 }}>Note</label>
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={3} style={{ ...input, resize: 'vertical' }} placeholder="What happened (kept in the ledger)" />
          </div>
          {history.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>This unit's ledger</div>
              {history.map(h => (
                <div key={h.id}>{new Date(h.created_at).toLocaleDateString()} · {h.direction} · {LABELS[h.reason] || h.reason} · {h.from_status || '—'} → {h.to_status || '—'}{h.created_by ? ` · ${h.created_by}` : ''}{h.note ? ` — ${h.note}` : ''}</div>
              ))}
            </div>
          )}
          {err && <div style={{ color: '#dc2626', fontSize: 12 }}>{err}</div>}
        </div>
        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !reason}>{busy ? 'Saving…' : direction === 'out' ? 'Adjust out' : 'Adjust in'}</button>
        </div>
      </div>
    </div>
  )
}
