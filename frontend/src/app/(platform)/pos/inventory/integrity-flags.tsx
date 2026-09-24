'use client'
// INTEGRITY FLAGS — units still in stock that the sales report (by invoice) or the commission report say were sold,
// duplicate IMEIs, and units received after they were sold (owner 2026-09-24; index §11b):
//   "it is not possible that 383 imei have not ben sold but appering in the invnetory so they have to be crashed agains
//    the sales report by invoice and the commission received reports to … it shoudl apprear as a flag and also highlight
//    which customer it was sold to from commisison report and have the ability to assign it tot hte customer with one
//    click … the flag still stays there till verified by the management that the imei was actually sold".
// Backend: GET /pos/inventory/integrity (the ONE engine's findings merged with the saved flags), POST …/integrity/scan,
// POST /pos/inventory/flags/{id}/assign | verify | dismiss. The kind labels, the counts and the permissions come from the
// payload — the page decides nothing about what a finding is.
import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '@/lib/client'
import AdjustModal, { type AdjustUnit } from './adjust-modal'

type Customer = { name: string; mobile?: string | null; ref?: string | null; source: 'commission' | 'sales' } | null
type FlagRow = {
  kind: string; kind_label?: string | null; device_key: string; unit_id: string | null; unit_ids?: string[]
  serial_number?: string | null; imei?: string | null; store_code?: string | null; status?: string | null
  product_name?: string | null; cost?: number | null; received_on?: string | null; sold_on?: string | null
  customer?: Customer; invoice_no?: string | null
  commission?: { net_earned: number; charged_back: number; kept: boolean; invoices?: string[] } | null
  sale?: { net: number; lines?: { invoice: string | null; date: string | null; qty: number; customer: string | null }[] } | null
  evidence?: { source: string; unit_id?: string; serial_number?: string; store_code?: string; status?: string }[]
  flag_id: string | null; flag_status: 'new' | 'open' | 'assigned' | 'verified' | 'dismissed'
  still_found: boolean; assigned_by?: string | null; assigned_at?: string | null; verified_by?: string | null; note?: string | null
}
type Payload = {
  rows: FlagRow[]
  summary: { by_kind: Record<string, { count: number; cost: number }>; by_status: Record<string, number>; open: number; open_cost: number; labels: Record<string, string> }
  totals: { units_live: number; not_flagged: Record<string, number> } | null
  basis: Record<string, { table?: string; read_ok?: boolean; rows?: number; truncated?: boolean }>
  flags_ready: boolean; setup_note: string | null
  can: { adjust: boolean; verify: boolean }
}

const cell: CSSProperties = { padding: '7px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap', fontSize: 12 }
const th: CSSProperties = { textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const tile: CSSProperties = { background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', minWidth: 170, flex: '1 1 170px' }
const small = (bg: string): CSSProperties => ({ background: bg, border: 'none', color: '#fff', borderRadius: 5, padding: '4px 9px', fontSize: 11, fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap' })
const money = (n: number | null | undefined) => `$${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const statusColor: Record<string, string> = { new: '#6b7280', open: '#dc2626', assigned: '#d97706', verified: '#16a34a', dismissed: '#6b7280' }
const overlay: CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }

export default function IntegrityFlags({ storeCode }: { storeCode: string }) {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [includeClosed, setIncludeClosed] = useState(false)
  const [kindFilter, setKindFilter] = useState('')
  const [noteFor, setNoteFor] = useState<{ row: FlagRow; action: 'dismiss' | 'reject' } | null>(null)
  const [note, setNote] = useState('')
  const [adjust, setAdjust] = useState<{ unit: AdjustUnit; flagId: string | null } | null>(null)

  async function load(closed = includeClosed) {
    setLoading(true); setErr('')
    try {
      const p = new URLSearchParams()
      if (closed) p.set('include_closed', 'true')
      if (storeCode && storeCode !== 'all') p.set('store_code', storeCode)
      setData(await api(`/api/v1/pos/inventory/integrity?${p}`))
    } catch (e: any) { setErr('Could not load the integrity flags: ' + String(e?.message || e)) }
    setLoading(false)
  }
  useEffect(() => { load() }, [storeCode])  // eslint-disable-line react-hooks/exhaustive-deps

  async function act(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusyId(key); setErr(''); setMsg('')
    try { const r = await fn(); setMsg(ok(r)); await load() } catch (e: any) { setErr(String(e?.message || e)) }
    setBusyId(null)
  }
  const scan = () => act('scan', () => api('/api/v1/pos/inventory/integrity/scan', { method: 'POST' }),
    r => `Scan saved: ${r.inserted} new flag(s), ${r.refreshed} refreshed, ${r.unchanged} closed flag(s) left closed${r.reopened ? `, ${r.reopened} re-opened (evidence changed)` : ''}.`)
  const assign = (row: FlagRow) => act(row.flag_id || '', () => api(`/api/v1/pos/inventory/flags/${row.flag_id}/assign`, { method: 'POST', body: '{}' }),
    r => row.kind === 'duplicate_on_hand'
      ? `Removed the duplicate record ${r.unit?.serial_number || ''} — awaiting a manager's verification.`
      : `Moved out as sold${row.customer ? ` to ${row.customer.name}` : ''}${r.customer_created ? ' (new customer created)' : ''} — the flag stays until a manager verifies.${r.customer_note ? ' ' + r.customer_note : ''}`)
  const verify = (row: FlagRow) => act(row.flag_id || '', () => api(`/api/v1/pos/inventory/flags/${row.flag_id}/verify`, { method: 'POST', body: '{}' }),
    () => `Verified — ${row.serial_number || row.device_key} is closed.`)
  async function submitNote() {
    if (!noteFor) return
    const { row, action } = noteFor
    const url = action === 'dismiss' ? `/api/v1/pos/inventory/flags/${row.flag_id}/dismiss` : `/api/v1/pos/inventory/flags/${row.flag_id}/verify`
    const body = action === 'dismiss' ? { note } : { approve: false, note }
    setNoteFor(null)
    await act(row.flag_id || '', () => api(url, { method: 'POST', body: JSON.stringify(body) }),
      () => action === 'dismiss' ? 'Dismissed with your note.' : 'Assignment rejected — the unit is back in stock and the flag is open again.')
    setNote('')
  }

  const rows = (data?.rows || []).filter(r => !kindFilter || r.kind === kindFilter)
  const s = data?.summary

  return (
    <div>
      <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)', marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: 'var(--text2)', flex: 1, minWidth: 260 }}>
          Units still in stock checked against the sales report (by invoice) and the commission report. A flag stays until a manager verifies it.
        </span>
        <select value={kindFilter} onChange={e => setKindFilter(e.target.value)} style={{ padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)' }}>
          <option value="">All kinds</option>
          {Object.entries(s?.labels || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', gap: 4, alignItems: 'center' }}>
          <input type="checkbox" checked={includeClosed} onChange={e => { setIncludeClosed(e.target.checked); load(e.target.checked) }} /> show closed
        </label>
        <button className="btn btn-secondary" onClick={() => load()} disabled={loading}>Refresh</button>
        {data?.can && (data.can.adjust || data.can.verify) && (
          <button className="btn btn-primary" onClick={scan} disabled={busyId === 'scan' || !data.flags_ready}>{busyId === 'scan' ? 'Scanning…' : '🔎 Scan & save flags'}</button>
        )}
      </div>

      {data?.setup_note && <div style={{ fontSize: 13, color: '#b45309', marginBottom: 10 }}>⚠️ {data.setup_note}</div>}
      {msg && <div style={{ fontSize: 13, color: '#16a34a', marginBottom: 10 }}>{msg}</div>}
      {err && <div style={{ fontSize: 13, color: '#dc2626', marginBottom: 10 }}>{err}</div>}

      {s && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={tile}>
            <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>Open flags</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{s.open}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>{money(s.open_cost)} at cost · {s.by_status.assigned || 0} awaiting verification</div>
          </div>
          {Object.entries(s.by_kind).map(([k, v]) => (
            <div key={k} style={{ ...tile, cursor: 'pointer', outline: kindFilter === k ? '2px solid var(--accent, #3b82f6)' : 'none' }} onClick={() => setKindFilter(kindFilter === k ? '' : k)}>
              <div style={{ fontSize: 11, color: 'var(--text2)' }}>{s.labels[k] || k}</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{v.count}</div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>{money(v.cost)}</div>
            </div>
          ))}
          {data?.totals && (
            <div style={tile}>
              <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>Not flagged</div>
              <div style={{ fontSize: 12, marginTop: 4 }}>{data.totals.units_live} units in stock checked</div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>{data.totals.not_flagged.returned_no_commission_kept || 0} returned / reversed (back in stock) · {data.totals.not_flagged.no_sale_no_commission || 0} with no sale and no commission</div>
            </div>
          )}
        </div>
      )}

      {data?.basis && (
        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 8 }}>
          Read: {Object.entries(data.basis).filter(([k]) => k !== 'complete').map(([k, b]) => `${k} ${b.read_ok === false ? '(not readable)' : `${b.rows ?? 0} rows${b.truncated ? ' (capped — a floor)' : ''}`}`).join(' · ')}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1250, fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['IMEI', 'Product', 'Store', 'Flag', 'Sold to (commission report)', 'Invoice', 'Sold on', 'Commission', 'Status', 'Actions'].map(h => <th key={h} style={th}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map(r => {
                const key = r.flag_id || `${r.device_key}-${r.kind}`
                const busy = busyId === r.flag_id
                return (
                  <tr key={key}>
                    <td style={{ ...cell, fontFamily: 'monospace' }}>{r.imei || r.serial_number || r.device_key}</td>
                    <td style={cell}>{r.product_name || '—'}</td>
                    <td style={cell}>{r.store_code || '—'}</td>
                    <td style={cell} title={r.kind}>
                      {r.kind_label || s?.labels[r.kind] || r.kind}
                      {r.kind === 'duplicate_on_hand' && r.unit_ids ? <span style={{ color: 'var(--text3)' }}> ({r.unit_ids.length} units)</span> : null}
                    </td>
                    <td style={cell}>
                      {r.customer ? <>{r.customer.name}{r.customer.mobile ? <span style={{ color: 'var(--text3)' }}> · {r.customer.mobile}</span> : null}{r.customer.source === 'sales' ? <span style={{ color: 'var(--text3)' }}> (sales report)</span> : null}</> : '—'}
                    </td>
                    <td style={{ ...cell, fontFamily: 'monospace' }}>{r.invoice_no || '—'}</td>
                    <td style={cell}>{r.sold_on || '—'}</td>
                    <td style={cell}>{r.commission ? <>{money(r.commission.net_earned)} <span style={{ color: r.commission.kept ? '#16a34a' : '#6b7280' }}>{r.commission.kept ? 'kept' : 'charged back'}</span></> : '—'}</td>
                    <td style={cell}>
                      <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600, background: `${statusColor[r.flag_status] || '#6b7280'}20`, color: statusColor[r.flag_status] || '#6b7280' }}>
                        {r.flag_status === 'new' ? 'not saved yet' : r.flag_status}
                      </span>
                      {!r.still_found && r.flag_status === 'assigned' ? <div style={{ fontSize: 10, color: 'var(--text3)' }}>moved out {r.assigned_at ? new Date(r.assigned_at).toLocaleDateString() : ''}{r.assigned_by ? ` by ${r.assigned_by}` : ''}</div> : null}
                      {r.note ? <div style={{ fontSize: 10, color: 'var(--text3)', maxWidth: 220, whiteSpace: 'normal' }}>{r.note}</div> : null}
                    </td>
                    <td style={cell}>
                      <div style={{ display: 'flex', gap: 5 }}>
                        {r.flag_status === 'open' && data?.can.adjust && (
                          <button disabled={busy} onClick={() => assign(r)} style={{ ...small('#2563eb'), opacity: busy ? 0.6 : 1 }}>
                            {busy ? '…' : r.kind === 'duplicate_on_hand' ? 'Remove duplicate' : 'Assign to customer'}
                          </button>
                        )}
                        {r.flag_status === 'open' && data?.can.adjust && r.unit_id && r.kind !== 'duplicate_on_hand' && (
                          <button disabled={busy} onClick={() => setAdjust({ unit: { id: r.unit_id as string, serial_number: r.serial_number || r.device_key, imei: r.imei || null, status: r.status || 'in_stock', store_code: r.store_code || null, product_name: r.product_name }, flagId: r.flag_id })}
                            style={small('#6b7280')}>Adjust…</button>
                        )}
                        {r.flag_status === 'assigned' && data?.can.verify && (
                          <>
                            <button disabled={busy} onClick={() => verify(r)} style={{ ...small('#16a34a'), opacity: busy ? 0.6 : 1 }}>Verify</button>
                            <button disabled={busy} onClick={() => { setNote(''); setNoteFor({ row: r, action: 'reject' }) }} style={small('#b91c1c')}>Reject</button>
                          </>
                        )}
                        {r.flag_status === 'open' && data?.can.verify && (
                          <button disabled={busy} onClick={() => { setNote(''); setNoteFor({ row: r, action: 'dismiss' }) }} style={small('#6b7280')}>Dismiss</button>
                        )}
                        {r.flag_status === 'new' && <span style={{ fontSize: 11, color: 'var(--text3)' }}>Scan to save</span>}
                      </div>
                    </td>
                  </tr>
                )
              })}
              {rows.length === 0 && (
                <tr><td colSpan={10} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  {data ? 'No integrity flags — every unit in stock agrees with the sales and commission reports.' : ''}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {noteFor && (
        <div style={overlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 480, maxWidth: '100%' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)' }}>
              <b style={{ fontSize: 14 }}>{noteFor.action === 'dismiss' ? 'Dismiss this flag' : 'Reject the assignment'}</b>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4 }}>
                {noteFor.action === 'dismiss'
                  ? 'The unit is not touched. A later scan re-opens the flag only if the sales or commission evidence changes.'
                  : 'The unit comes back into stock through the adjustment ledger and the flag opens again.'}
              </div>
            </div>
            <div style={{ padding: 20 }}>
              <textarea value={note} onChange={e => setNote(e.target.value)} rows={3} placeholder="Why (required)"
                style={{ padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', resize: 'vertical' }} />
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setNoteFor(null)}>Cancel</button>
              <button className="btn btn-primary" disabled={!note.trim()} onClick={submitNote}>{noteFor.action === 'dismiss' ? 'Dismiss' : 'Reject'}</button>
            </div>
          </div>
        </div>
      )}

      {adjust && (
        <AdjustModal unit={adjust.unit} flagId={adjust.flagId} onClose={() => setAdjust(null)}
          onDone={m => { setAdjust(null); setMsg(m); load() }} />
      )}
    </div>
  )
}
