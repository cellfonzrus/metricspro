'use client'

// Cash-drawer session chip + open/close modals for the POS register header — ported from the
// standalone pos-system app's components/RegisterDrawer.tsx.
// Data access rewired to the FastAPI /pos router: sessions live in pos.register_sessions and all
// writes go through the open/close endpoints (which call the pos.open_register / pos.close_register
// RPCs — the server computes closing_expected + variance). Error messages are user-readable —
// surface them verbatim (alert() is the register idiom).

import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import {
  PosConfigValues,
  Denomination,
  denominationList,
  denominationTotal,
  getRegisterNumber,
  setRegisterNumber,
} from '@/lib/pos-config'

export interface RegisterSession {
  id: string
  store_code: string
  register_number: number
  status: string
  opened_by: string | null           // TEXT employee_id
  opened_at: string
  opening_float: number
  closing_counted: number | null
  closing_expected: number | null
  variance: number | null
  closed_at?: string | null
  notes: string | null
}

interface Props {
  activeStore: string | null         // store_code of the device's active store
  storeName?: string                 // display label for the pickup slip
  cfg: PosConfigValues
  session: RegisterSession | null
  onSessionChange: (session: RegisterSession | null) => void
}

const inputStyle: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 7, padding: '7px 10px', fontSize: 13, color: 'var(--text)', outline: 'none' }

function DenomGrid({ denoms, counts, onChange }: {
  denoms: Denomination[]
  counts: Record<string, number>
  onChange: (counts: Record<string, number>) => void
}) {
  return (
    <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', marginBottom: 12, maxHeight: '46vh', overflowY: 'auto' }}>
      {denoms.map(d => {
        const count = counts[d.key] || 0
        return (
          <div key={d.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '3px 0' }}>
            <span style={{ width: 46, fontSize: 12, fontWeight: 600, color: d.kind === 'bill' ? 'var(--green)' : 'var(--text2)' }}>{d.label}</span>
            <input
              type="number" min={0} step={1} value={count === 0 ? '' : count} placeholder="0"
              onChange={e => onChange({ ...counts, [d.key]: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
              style={{ ...inputStyle, width: 70, padding: '4px 8px', fontSize: 12 }}
            />
            <span style={{ marginLeft: 'auto', fontSize: 12 }}>${denominationTotal({ [d.key]: count }).toFixed(2)}</span>
          </div>
        )
      })}
      <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border)', marginTop: 8, paddingTop: 8, fontSize: 13, fontWeight: 700 }}>
        <span style={{ color: 'var(--text2)' }}>Total counted</span>
        <span style={{ color: 'var(--green)' }}>${denominationTotal(counts).toFixed(2)}</span>
      </div>
    </div>
  )
}

export default function RegisterDrawer({ activeStore, storeName, cfg, session, onSessionChange }: Props) {
  // Register number is a property of the physical device (localStorage) —
  // read it in an effect so SSR markup (always 1) matches first paint.
  const [regNum, setRegNum] = useState(1)
  const [showOpen, setShowOpen] = useState(false)
  const [showClose, setShowClose] = useState(false)
  const [busy, setBusy] = useState(false)
  const [openFloat, setOpenFloat] = useState('')
  const [openCounts, setOpenCounts] = useState<Record<string, number>>({})
  const [closeCounted, setCloseCounted] = useState('')
  const [closeCounts, setCloseCounts] = useState<Record<string, number>>({})
  const [closeNotes, setCloseNotes] = useState('')
  const [closeResult, setCloseResult] = useState<RegisterSession | null>(null)

  useEffect(() => { setRegNum(getRegisterNumber()) }, [])

  const denomRequired = cfg.denomination_count_required === true
  const denoms = denominationList(!!cfg.show_two_dollar_bill)

  // Changing this device's register number: persist it, then reload the open
  // session for the new store/register pair so the chip and rules stay honest.
  async function applyRegisterNumber(n: number) {
    setRegisterNumber(n)
    const applied = getRegisterNumber()
    setRegNum(applied)
    if (!activeStore) { onSessionChange(null); return }
    try {
      const r = await api(`/api/v1/pos/register/session?store_code=${encodeURIComponent(activeStore)}&register_number=${applied}`)
      onSessionChange((r.session as RegisterSession | null) || null)
    } catch {
      onSessionChange(null)
    }
  }

  function startOpen() {
    setOpenCounts({})
    setOpenFloat(cfg.use_default_float ? Number(cfg.default_float_amount).toFixed(2) : '')
    setShowOpen(true)
  }

  function startClose() {
    setCloseCounts({})
    setCloseCounted('')
    setCloseNotes('')
    setCloseResult(null)
    setShowClose(true)
  }

  async function confirmOpen() {
    if (!activeStore) { alert('Pick a store in the store selector first.'); return }
    const float = denomRequired ? denominationTotal(openCounts) : parseFloat(openFloat || '0')
    if (!Number.isFinite(float) || float < 0) { alert('Opening float must be zero or more.'); return }
    setBusy(true)
    try {
      const r = await api('/api/v1/pos/register/open', {
        method: 'POST',
        body: JSON.stringify({
          store_code: activeStore,
          register_number: regNum,
          opening_float: parseFloat(float.toFixed(2)),
          denominations: denomRequired ? openCounts : null,
        }),
      })
      onSessionChange(r.session as RegisterSession)
      setShowOpen(false)
    } catch (err: any) {
      alert(err?.message || 'Could not open the drawer')
    }
    setBusy(false)
  }

  async function confirmClose() {
    if (!session) return
    const counted = denomRequired ? denominationTotal(closeCounts) : parseFloat(closeCounted || '0')
    if (!Number.isFinite(counted) || counted < 0) { alert('Counted cash must be zero or more.'); return }
    setBusy(true)
    try {
      const r = await api('/api/v1/pos/register/close', {
        method: 'POST',
        body: JSON.stringify({
          session_id: session.id,
          counted: parseFloat(counted.toFixed(2)),
          denominations: denomRequired ? closeCounts : null,
          notes: closeNotes.trim() || null,
        }),
      })
      setCloseResult(r.session as RegisterSession)
    } catch (err: any) {
      alert(err?.message || 'Could not close the drawer')
    }
    setBusy(false)
  }

  function finishClose() {
    setShowClose(false)
    setCloseResult(null)
    onSessionChange(null)
  }

  function escapeHtml(s: string) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }

  // receipt_print_cash_pickup_slip (POS Configuration): thermal-style summary
  // slip for the close-out, printed via the same hidden-iframe idiom as the
  // register's receipts.
  function printPickupSlip() {
    if (!closeResult) return
    const store = storeName || closeResult.store_code || ''
    const money = (v: number | null | undefined) => `$${Number(v ?? 0).toFixed(2)}`
    const v = Number(closeResult.variance ?? 0)
    const html = `<!doctype html><html><head><title>Cash Pickup Slip — Drawer #${closeResult.register_number}</title><style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body { font-family: 'Courier New', monospace; font-size: 12px; color: #000; width: 300px; padding: 12px; }
      .center { text-align: center; }
      .title { font-size: 14px; font-weight: bold; }
      .pre { white-space: pre-wrap; }
      hr { border: none; border-top: 1px dashed #000; margin: 8px 0; }
      table { width: 100%; border-collapse: collapse; }
      td { padding: 1px 0; vertical-align: top; }
      .r { text-align: right; }
      .grand { font-size: 14px; font-weight: bold; }
      @media print { body { width: auto; } }
    </style></head><body>
      <div class="center">
        <div class="title">CASH PICKUP SLIP</div>
        ${store ? `<div>${escapeHtml(store)}</div>` : ''}
        <div>Drawer #${closeResult.register_number}</div>
        <div>Closed: ${escapeHtml(new Date(closeResult.closed_at || Date.now()).toLocaleString())}</div>
      </div>
      <hr><table>
        <tr><td>Opening float</td><td class="r">${money(closeResult.opening_float)}</td></tr>
        <tr><td>Expected in drawer</td><td class="r">${money(closeResult.closing_expected)}</td></tr>
        <tr><td>Counted</td><td class="r">${money(closeResult.closing_counted)}</td></tr>
        <tr class="grand"><td>Variance</td><td class="r">${v < 0 ? '−' : ''}${money(Math.abs(v))}</td></tr>
      </table>
      ${closeResult.notes ? `<hr><div class="pre">Notes: ${escapeHtml(closeResult.notes)}</div>` : ''}
    </body></html>`
    const iframe = document.createElement('iframe')
    iframe.style.position = 'fixed'
    iframe.style.right = '-9999px'
    document.body.appendChild(iframe)
    const doc = iframe.contentWindow?.document
    if (!doc) { alert('Could not open print view'); iframe.remove(); return }
    doc.open(); doc.write(html); doc.close()
    iframe.onload = () => {
      iframe.contentWindow?.focus()
      iframe.contentWindow?.print()
      setTimeout(() => iframe.remove(), 2000)
    }
  }

  const openedTime = session ? new Date(session.opened_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
  const variance = Number(closeResult?.variance ?? 0)
  const varianceOk = Math.abs(variance) < 0.005
  const alertThreshold = Number(cfg.variance_alert_threshold)
  const varianceOverThreshold = alertThreshold > 0 && Math.abs(variance) > alertThreshold

  const modalOverlay: React.CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }
  const modalCard: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 380, overflow: 'hidden' }
  const modalHeader: React.CSSProperties = { padding: '14px 20px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      {/* Status chip */}
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 7, padding: '4px 8px' }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: session ? 'var(--green)' : 'var(--text3)', whiteSpace: 'nowrap' }}>
          🗄 Drawer #{regNum}: {session ? `OPEN since ${openedTime}` : 'CLOSED'}
        </span>
        <input
          type="number" min={1} step={1} value={regNum} title="This device's register number"
          onChange={e => applyRegisterNumber(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
          style={{ ...inputStyle, width: 38, padding: '2px 4px', fontSize: 11, textAlign: 'center' }}
        />
      </span>
      {session ? (
        <button onClick={startClose} style={{ background: 'var(--red)', border: 'none', color: 'white', borderRadius: 6, padding: '5px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' }}>
          Close Drawer
        </button>
      ) : (
        <button onClick={startOpen} disabled={!activeStore}
          title={activeStore ? undefined : 'Pick a store in the store selector first'}
          style={{ background: 'var(--green)', border: 'none', color: 'white', borderRadius: 6, padding: '5px 10px', fontSize: 11, fontWeight: 600, cursor: activeStore ? 'pointer' : 'not-allowed', opacity: activeStore ? 1 : 0.5, whiteSpace: 'nowrap' }}>
          Open Drawer
        </button>
      )}

      {/* OPEN DRAWER MODAL */}
      {showOpen && (
        <div style={modalOverlay}>
          <div style={modalCard}>
            <div style={modalHeader}>
              <span style={{ fontSize: 14, fontWeight: 700 }}>🗄 Open Drawer #{regNum}</span>
              <button onClick={() => setShowOpen(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 20 }}>
              {denomRequired ? (
                <>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>Count the opening cash by denomination — the total becomes the float:</div>
                  <DenomGrid denoms={denoms} counts={openCounts} onChange={setOpenCounts} />
                </>
              ) : (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>Opening float ($):</div>
                  <input type="number" min={0} step="0.01" value={openFloat} onChange={e => setOpenFloat(e.target.value)}
                    style={{ ...inputStyle, width: '100%', boxSizing: 'border-box', fontSize: 16, fontWeight: 700 }} placeholder="0.00" autoFocus />
                </div>
              )}
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowOpen(false)}>Cancel</button>
              <button onClick={confirmOpen} disabled={busy}
                style={{ background: 'var(--green)', border: 'none', color: 'white', borderRadius: 7, padding: '10px 24px', fontSize: 14, fontWeight: 700, cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.7 : 1 }}>
                {busy ? 'Opening…' : `✅ Open with $${(denomRequired ? denominationTotal(openCounts) : parseFloat(openFloat || '0') || 0).toFixed(2)}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CLOSE DRAWER MODAL */}
      {showClose && (
        <div style={modalOverlay}>
          <div style={modalCard}>
            <div style={modalHeader}>
              <span style={{ fontSize: 14, fontWeight: 700 }}>🗄 Close Drawer #{regNum}</span>
              {!closeResult && (
                <button onClick={() => setShowClose(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
              )}
            </div>
            {closeResult ? (
              /* RESULT VIEW — server-computed expected vs counted */
              <div style={{ padding: 20 }}>
                <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: 16, marginBottom: 14 }}>
                  {[
                    { label: 'Expected in drawer', value: Number(closeResult.closing_expected ?? 0) },
                    { label: 'Counted', value: Number(closeResult.closing_counted ?? 0) },
                  ].map(row => (
                    <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
                      <span style={{ color: 'var(--text2)' }}>{row.label}</span>
                      <span style={{ fontWeight: 600 }}>${row.value.toFixed(2)}</span>
                    </div>
                  ))}
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0 2px', fontSize: 15, fontWeight: 700 }}>
                    <span>Variance</span>
                    <span style={{ color: varianceOk ? 'var(--green)' : 'var(--amber)' }}>
                      {variance >= 0 ? '' : '−'}${Math.abs(variance).toFixed(2)}
                    </span>
                  </div>
                </div>
                {varianceOverThreshold && (
                  <div style={{ background: 'rgba(220,38,38,0.08)', border: '1px solid var(--red)', borderRadius: 8, padding: '10px 12px', fontSize: 13, fontWeight: 700, color: 'var(--red)', marginBottom: 14, textAlign: 'center' }}>
                    ⚠ VARIANCE OVER ${alertThreshold.toFixed(2)} ALERT THRESHOLD
                  </div>
                )}
                {cfg.receipt_print_cash_pickup_slip === true && (
                  <button className="btn btn-primary" onClick={printPickupSlip} style={{ width: '100%', justifyContent: 'center', marginBottom: 8 }}>
                    🖨 Print pickup slip
                  </button>
                )}
                <button onClick={finishClose} style={{ width: '100%', background: 'var(--green)', border: 'none', color: 'white', borderRadius: 8, padding: 12, fontSize: 14, fontWeight: 700, cursor: 'pointer' }}>
                  Done
                </button>
              </div>
            ) : (
              <>
                <div style={{ padding: 20 }}>
                  {denomRequired ? (
                    <>
                      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>Count the cash in the drawer by denomination:</div>
                      <DenomGrid denoms={denoms} counts={closeCounts} onChange={setCloseCounts} />
                    </>
                  ) : (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>Counted cash ($):</div>
                      <input type="number" min={0} step="0.01" value={closeCounted} onChange={e => setCloseCounted(e.target.value)}
                        style={{ ...inputStyle, width: '100%', boxSizing: 'border-box', fontSize: 16, fontWeight: 700 }} placeholder="0.00" autoFocus />
                    </div>
                  )}
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>Notes (optional):</div>
                  <textarea value={closeNotes} onChange={e => setCloseNotes(e.target.value)} rows={2}
                    style={{ ...inputStyle, width: '100%', resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box' as const }} placeholder="e.g. cash drop, till discrepancy…" />
                </div>
                <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button className="btn btn-secondary" onClick={() => setShowClose(false)}>Cancel</button>
                  <button onClick={confirmClose} disabled={busy}
                    style={{ background: 'var(--red)', border: 'none', color: 'white', borderRadius: 7, padding: '10px 24px', fontSize: 14, fontWeight: 700, cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.7 : 1 }}>
                    {busy ? 'Closing…' : `Close with $${(denomRequired ? denominationTotal(closeCounts) : parseFloat(closeCounted || '0') || 0).toFixed(2)}`}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </span>
  )
}
