'use client'
import { useState, useEffect, useMemo } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }
const STATUS_BADGE: Record<string, string> = { pending: 'badge-amber', approved: 'badge-green', denied: 'badge-red', cancelled: 'badge-blue' }

function windowRange() {
  const d = new Date()
  const iso = (x: Date) => x.toISOString().slice(0, 10)
  const start = new Date(d); start.setDate(d.getDate() - 7)
  const end = new Date(d); end.setDate(d.getDate() + 21)
  return { start: iso(start), end: iso(end) }
}

export default function ShiftSwapsPage() {
  const [swaps, setSwaps] = useState<any[]>([])
  const [emps, setEmps] = useState<any[]>([])
  const [shifts, setShifts] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<any>({ requester_id: '', shift_id: '', target_id: '', target_shift_id: '', notes: '' })
  const win = windowRange()

  function load() {
    setLoading(true)
    Promise.all([
      api('/api/v1/storeops/shift-swaps').catch(() => []),
      apiCached('/api/v1/storeops/employees', LOOKUP).catch(() => []),
      api(`/api/v1/storeops/shifts?week_start=${win.start}&week_end=${win.end}`).catch(() => []),
    ]).then(([sw, e, sh]) => { setSwaps(sw || []); setEmps(e || []); setShifts(sh || []) })
      .catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const empOpts = emps.filter(e => e.employee_id).map(e => ({ id: String(e.employee_id), name: e.name }))
  const reqShifts = shifts.filter(s => String(s.employee_id) === String(form.requester_id))
  const tgtShifts = shifts.filter(s => String(s.employee_id) === String(form.target_id))
  const shiftLabel = (s: any) => s ? `${s.shift_date} ${(s.start_time || '').slice(0, 5)}-${(s.end_time || '').slice(0, 5)} @ ${s.store_code || ''}` : '—'

  async function create() {
    if (!form.requester_id) { setMsg('Pick the employee requesting the swap.'); return }
    setMsg('')
    try {
      await api('/api/v1/storeops/shift-swaps', { method: 'POST', body: JSON.stringify({
        requester_id: form.requester_id, target_id: form.target_id || null,
        shift_id: form.shift_id ? Number(form.shift_id) : null,
        target_shift_id: form.target_shift_id ? Number(form.target_shift_id) : null,
        notes: form.notes || null,
      }) })
      setMsg('Swap request created.')
      setForm({ requester_id: '', shift_id: '', target_id: '', target_shift_id: '', notes: '' })
      setShowForm(false); load()
    } catch (e: any) { setMsg('Create failed: ' + (e?.message || e)) }
  }
  async function setStatus(id: any, status: string) {
    setMsg('')
    try { await api(`/api/v1/storeops/shift-swaps/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }); load() }
    catch (e: any) { setMsg('Update failed: ' + (e?.message || e)) }
  }

  // Standard filters (Phase W2, RULE FIVE §3d): employee(s) + store(s) + a date range over the
  // requester's shift date. Options come off the loaded rows (org-scoped by construction); no market
  // control — a swap row carries no market. Table + export read the SAME filtered set.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const swapStore = (s: any) => s.shift?.store_code || s.target_shift?.store_code || ''
  const swapDate = (s: any) => s.shift?.shift_date || s.target_shift?.shift_date || ''
  const filtOpts = useMemo(() => optionsFromRows(swaps, {
    rep: s => s.requester_name || s.requester_id, store: swapStore,
  }), [swaps])
  const visible = useMemo(() => filterRows(swaps, filt, {
    rep: s => s.requester_name || s.requester_id, store: swapStore, date: swapDate,
  }), [swaps, filt])

  // RULE FOUR (§3c): export the visible rows — no PII (names/shift labels/status/notes only).
  const cols: ExportColumn[] = [
    { header: 'Requester', field: 'requester', role: 'rep', get: s => s.requester_name || s.requester_id },
    { header: 'Gives Up', field: 'shift', get: s => shiftLabel(s.shift) },
    { header: 'Swap With', field: 'target', get: s => s.target_name || '' },
    { header: 'Their Shift', field: 'target_shift', get: s => shiftLabel(s.target_shift) },
    { header: 'Status', field: 'status', get: s => s.status },
    { header: 'Notes', field: 'notes', get: s => s.notes || '' },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔄 Shift Swaps</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Request and approve shift swaps. Approving reassigns the shift(s).</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {swaps.length > 0 && <ReportExportBar title="Shift Swaps" columns={cols} rows={visible} />}
          <button className="btn btn-primary" onClick={() => setShowForm(v => !v)}>{showForm ? '✕ Cancel' : '＋ New swap request'}</button>
        </div>
      </div>
      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {showForm && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12, alignItems: 'end' }}>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Requester *</label>
              <select style={{ ...sel, width: '100%' }} value={form.requester_id} onChange={e => setForm({ ...form, requester_id: e.target.value, shift_id: '' })}>
                <option value="">— employee —</option>
                {empOpts.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Their shift to give up</label>
              <select style={{ ...sel, width: '100%' }} value={form.shift_id} onChange={e => setForm({ ...form, shift_id: e.target.value })} disabled={!form.requester_id}>
                <option value="">— shift (optional) —</option>
                {reqShifts.map(s => <option key={s.id} value={s.id}>{shiftLabel(s)}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Swap with</label>
              <select style={{ ...sel, width: '100%' }} value={form.target_id} onChange={e => setForm({ ...form, target_id: e.target.value, target_shift_id: '' })}>
                <option value="">— employee (optional) —</option>
                {empOpts.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Their shift (for a true swap)</label>
              <select style={{ ...sel, width: '100%' }} value={form.target_shift_id} onChange={e => setForm({ ...form, target_shift_id: e.target.value })} disabled={!form.target_id}>
                <option value="">— shift (optional) —</option>
                {tgtShifts.map(s => <option key={s.id} value={s.id}>{shiftLabel(s)}</option>)}
              </select>
            </div>
            <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 10, alignItems: 'center' }}>
              <input style={{ ...sel, flex: 1 }} placeholder="Notes / reason" value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} />
              <button className="btn btn-primary" onClick={create}>Create request</button>
            </div>
          </div>
        </div>
      )}

      {/* Standard filter bar (Phase W2) — employees + stores + date range over the shift date. */}
      {swaps.length > 0 && (
        <StandardFilterBar value={filt} onChange={setFilt} periodMode="range" show={{ markets: false }}
          storeOptions={filtOpts.stores} repOptions={filtOpts.reps} repLabel="Employees…" />
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : swaps.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>No shift-swap requests yet.</div>
      ) : visible.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>No swap requests match the current filters.</div>
      ) : (
        <div className="table-wrapper">
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Requester', 'Gives up', 'Swap with', 'Their shift', 'Status', 'Notes', ''].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {visible.map(s => (
                <tr key={s.id}>
                  <td style={{ ...cell, fontWeight: 500 }}>{s.requester_name || s.requester_id}</td>
                  <td style={cell}>{shiftLabel(s.shift)}</td>
                  <td style={cell}>{s.target_name || '—'}</td>
                  <td style={cell}>{shiftLabel(s.target_shift)}</td>
                  <td style={cell}><span className={`badge ${STATUS_BADGE[s.status] || ''}`} style={{ fontSize: 11 }}>{s.status}</span></td>
                  <td style={{ ...cell, color: 'var(--text3)' }}>{s.notes || '—'}</td>
                  <td style={cell}>
                    {s.status === 'pending' ? (
                      <span style={{ whiteSpace: 'nowrap' }}>
                        <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => setStatus(s.id, 'approved')}>✓ Approve</button>{' '}
                        <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => setStatus(s.id, 'denied')}>✕ Deny</button>
                      </span>
                    ) : <span style={{ color: 'var(--text3)', fontSize: 12 }}>—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
