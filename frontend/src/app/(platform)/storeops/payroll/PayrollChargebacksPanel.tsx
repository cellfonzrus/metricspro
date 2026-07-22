'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt } from '@/lib/client'

// Payroll-time chargebacks (2026-07-22, owner-directed, MONEY-ADJACENT). Shows this period's
// commcalc.ops_chargeback rows with applied_to='payroll' (missed-closing chargebacks, PLUS any
// commission-settlement OVERFLOW child rows mod-commission upserts with parent_id set — those arrive
// already status='posted', decided_by='settlement') and lets a manager POST (becomes a real payroll
// deduction — pending rows only) or WAIVE (never deducts — any row, including an already-posted
// settlement child, per the 2026-07-22 owner default that management can always cancel a posted row).
// Backend: GET /storeops/payroll-chargebacks, POST /storeops/payroll-chargebacks/{id}/decision — both
// degrade gracefully (empty list / a clear error) if mod-retail-ops' table hasn't been migrated in
// yet. `onDeductions` lifts the map of POSTED amounts per employee up to the parent Payroll Report
// so it can show a Chargebacks/Net Pay column WITHOUT this component ever touching the `/payroll`
// endpoint's own numbers (purely additive, client-side only). A settlement child's amount is a
// POSTED row like any other, so it's picked up by that same deduction map automatically.

interface ChargebackRow {
  id: string; employee_id?: string; employee_name?: string; store_code?: string
  reason?: string; reason_label?: string; incident_date?: string; amount?: number; status?: string
  posted_ref?: string; decided_by?: string; decided_at?: string
  parent_id?: string | null; covered_amount?: number | null   // CASCADE settlement fields (retail-ops v2, may be absent pre-migration)
}

const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  posted: { bg: '#fdeaea', fg: '#b91c1c' },
  pending: { bg: '#fff7e6', fg: '#92400e' },
  waived: { bg: '#e7f6ec', fg: '#166534' },
}

export default function PayrollChargebacksPanel({ month, onDeductions }:
  { month: string; onDeductions?: (m: Record<string, number>) => void }) {
  const [rows, setRows] = useState<ChargebackRow[]>([])
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const [open, setOpen] = useState(false)

  function load() {
    setLoading(true)
    api(`/api/v1/storeops/payroll-chargebacks?month=${month}`)
      .then((r: any) => setRows(Array.isArray(r?.items) ? r.items : []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }

  // POSTED-only deduction per employee, lifted to the parent regardless of whether the panel is
  // expanded — the payroll table's Net Pay column shouldn't depend on this card being open. Includes
  // settlement-created overflow children (parent_id set) automatically — they're 'posted' too.
  useEffect(() => {
    if (!onDeductions) return
    const m: Record<string, number> = {}
    for (const r of rows) {
      if (String(r.status).toLowerCase() !== 'posted' || !r.employee_id) continue
      m[r.employee_id] = (m[r.employee_id] || 0) + Number(r.amount || 0)
    }
    onDeductions(m)
  }, [rows, onDeductions])

  // Load once (collapsed) so the deduction map is available even before the manager opens the card.
  useEffect(() => { load() }, [month])

  async function decide(id: string, decision: 'post' | 'waive') {
    setBusyId(id); setMsg('')
    try {
      await api(`/api/v1/storeops/payroll-chargebacks/${id}/decision`, {
        method: 'POST', body: JSON.stringify({ decision, period: month }),
      })
      setMsg(decision === 'post' ? '✅ Posted — now deducted from that employee’s net pay.' : '✅ Waived — no deduction.')
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || 'Action failed — you may need a manager login.')) }
    finally { setBusyId(null) }
  }

  const totalPosted = useMemo(() => rows
    .filter(r => String(r.status).toLowerCase() === 'posted')
    .reduce((s, r) => s + Number(r.amount || 0), 0), [rows])
  const pendingCount = rows.filter(r => String(r.status).toLowerCase() === 'pending').length

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
           onClick={() => setOpen(o => !o)}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>
          ⚠️ Chargebacks{pendingCount > 0 ? ` — ${pendingCount} pending decision${pendingCount === 1 ? '' : 's'}` : ''}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)' }}>{open ? '▲ collapse' : '▼ review'}</div>
      </div>

      {open && (
        <div style={{ marginTop: 14 }}>
          <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 0 }}>
            Missed-closing chargebacks flagged against payroll for {month}, including any overflow
            commission couldn't fully cover. POST to deduct a pending item from the employee's net pay
            this period, or WAIVE at any time (even an already-posted item) to cancel the deduction.
            Never auto-applied — a management decision is required either way.
          </p>
          {msg && <div style={{ fontSize: 12, marginBottom: 10 }}>{msg}</div>}
          {loading ? (
            <div style={{ textAlign: 'center', padding: 20, color: 'var(--text3)' }}>Loading…</div>
          ) : rows.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 20, color: 'var(--text3)', fontSize: 13 }}>
              No payroll chargebacks for {month}.
            </div>
          ) : (
            <table className="table" style={{ fontSize: 12 }}>
              <thead>
                <tr><th>Employee</th><th>Reason</th><th>Store</th><th>Date</th><th>Amount</th><th>Status</th><th>Action</th></tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const status = String(r.status || 'pending').toLowerCase()
                  const s = STATUS_STYLE[status] || STATUS_STYLE.pending
                  const isOverflowChild = !!r.parent_id
                  const hasCoveredContext = r.covered_amount != null
                  return (
                    <tr key={r.id}>
                      <td>{r.employee_name || r.employee_id || '—'}</td>
                      <td>
                        {r.reason_label || r.reason}
                        {isOverflowChild && (
                          <div style={{ fontSize: 10, color: '#b45309', marginTop: 2 }}>
                            ↳ overflow from commission{r.posted_ref ? ` — ${r.posted_ref}` : ''}
                          </div>
                        )}
                        {hasCoveredContext && (
                          <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>
                            commission covered {fmt(Number(r.covered_amount || 0))} of {fmt(Number(r.amount || 0) + Number(r.covered_amount || 0))}
                          </div>
                        )}
                      </td>
                      <td>{r.store_code || '—'}</td>
                      <td>{r.incident_date || '—'}</td>
                      <td>{fmt(Number(r.amount || 0))}</td>
                      <td>
                        <span style={{ background: s.bg, color: s.fg, borderRadius: 6, padding: '2px 8px', fontWeight: 700 }}>
                          {status.toUpperCase()}
                        </span>
                      </td>
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
                          <div style={{ display: 'flex', gap: 6 }}>
                            {status === 'pending' && (
                              <button className="btn btn-secondary" disabled={busyId === r.id}
                                      onClick={() => decide(r.id, 'post')} style={{ padding: '3px 8px', fontSize: 11 }}>
                                Post
                              </button>
                            )}
                            {/* Owner default (2026-07-22): management can WAIVE any pending OR posted
                                row (including an already-posted settlement overflow child) — waive is
                                the one action that's never restricted by status/parent_id. */}
                            {(status === 'pending' || status === 'posted') && (
                              <button className="btn btn-secondary" disabled={busyId === r.id}
                                      onClick={() => decide(r.id, 'waive')} style={{ padding: '3px 8px', fontSize: 11 }}>
                                Waive
                              </button>
                            )}
                          </div>
                          {r.decided_by && status !== 'pending' && (
                            <span style={{ color: 'var(--text3)', fontSize: 10 }}>by {r.decided_by}</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
          {totalPosted > 0 && (
            <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>
              Total posted this period: <b>{fmt(totalPosted)}</b> (already reflected in Net Pay below).
            </div>
          )}
        </div>
      )}
    </div>
  )
}
