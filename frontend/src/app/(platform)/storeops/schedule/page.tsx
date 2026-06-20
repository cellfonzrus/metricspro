'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, localToday, parseLocalDate, addDays, mondayOf } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'

interface Shift {
  id: number
  employee_id?: string
  employee_name: string
  store_code: string
  shift_date: string
  start_time: string
  end_time: string
  scheduled_hours: number
  status: string
}

interface Employee { id: number; name: string; home_store: string; role: string }
interface Store { id: number; store_code: string; address: string; market: string }

// Local-safe day-of-week + m/d label (never the old DAYS[i] index — that was the
// off-by-one: an array index can't know the real weekday after a UTC date shift).
function dayLabel(date: string) {
  const d = parseLocalDate(date)
  return { dow: d.toLocaleDateString('en-US', { weekday: 'short' }), md: `${d.getMonth() + 1}/${d.getDate()}` }
}
function hoursBetween(start: string, end: string) {
  const [sh, sm] = start.split(':').map(Number)
  const [eh, em] = end.split(':').map(Number)
  return Math.max(0, ((eh * 60 + em) - (sh * 60 + sm)) / 60)
}

export default function SchedulePage() {
  const [weekStart, setWeekStart] = useState(() => mondayOf())
  const [shifts, setShifts] = useState<Shift[]>([])
  const [employees, setEmployees] = useState<Employee[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [view, setView] = useState<'store' | 'employee'>('store')
  const [filterMarket, setFilterMarket] = useState('')
  const [filterStore, setFilterStore] = useState('')
  const [loading, setLoading] = useState(true)
  // addModal carries the day + the fixed dimension (store OR emp) depending on the view.
  const [addModal, setAddModal] = useState<{ date: string; store?: string; emp?: string } | null>(null)
  const [newShift, setNewShift] = useState({ start_time: '10:00', end_time: '18:00', store_code: '', employee_name: '' })
  const [busy, setBusy] = useState(false)

  const weekEnd = addDays(weekStart, 6)
  const weekDates = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart])

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api(`/api/v1/storeops/shifts?week_start=${weekStart}&week_end=${weekEnd}`),
      api('/api/v1/storeops/employees'),
      api('/api/v1/storeops/stores'),
    ]).then(([s, e, st]) => { setShifts(s || []); setEmployees(e || []); setStores(st || []) })
      .catch(console.error).finally(() => setLoading(false))
  }, [weekStart])

  // market lookup by store_code (employees only carry home_store)
  const mktOf = useMemo(() => {
    const m: Record<string, string> = {}
    stores.forEach(s => { if (s.store_code) m[s.store_code] = s.market || '' })
    return m
  }, [stores])
  const markets = useMemo(() => Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort(), [stores])

  const filteredStores = stores.filter(s =>
    s.store_code &&
    (!filterMarket || s.market === filterMarket) &&
    (!filterStore || s.store_code === filterStore))
  const filteredEmps = employees.filter(e =>
    (!filterMarket || mktOf[e.home_store] === filterMarket) &&
    (!filterStore || e.home_store === filterStore))

  const shiftsOf = (pred: (s: Shift) => boolean) => shifts.filter(pred)

  // Double-booking: same employee, same day, 2+ shifts across different stores.
  const conflicts = useMemo(() => {
    const byKey: Record<string, Shift[]> = {}
    for (const s of shifts) { (byKey[`${s.employee_name}|${s.shift_date}`] ||= []).push(s) }
    return Object.values(byKey)
      .filter(arr => arr.length > 1 && new Set(arr.map(a => a.store_code)).size > 1)
      .map(arr => ({ emp: arr[0].employee_name, date: arr[0].shift_date, stores: Array.from(new Set(arr.map(a => a.store_code))) }))
  }, [shifts])

  function prevWeek() { setWeekStart(w => addDays(w, -7)) }
  function nextWeek() { setWeekStart(w => addDays(w, 7)) }

  // Copy this week's shifts forward into the next N weeks (dedup per target week).
  async function copyWeeks() {
    if (!shifts.length) { alert('No shifts this week to copy.'); return }
    const ans = prompt('Copy this week to how many following weeks?', '1')
    const n = Math.max(1, Math.min(12, parseInt(ans || '0') || 0))
    if (!n) return
    if (!confirm(`Copy ${shifts.length} shifts into the next ${n} week${n === 1 ? '' : 's'}? Existing shifts are kept (duplicates skipped).`)) return
    setBusy(true)
    let added = 0
    try {
      for (let wk = 1; wk <= n; wk++) {
        const ds = addDays(weekStart, wk * 7), de = addDays(ds, 6)
        const existing = await api(`/api/v1/storeops/shifts?week_start=${ds}&week_end=${de}`)
        const seen = new Set((existing || []).map((s: any) => `${s.employee_name}|${s.shift_date}|${s.start_time}`))
        for (const sh of shifts) {
          const nd = addDays(sh.shift_date, wk * 7)
          if (seen.has(`${sh.employee_name}|${nd}|${sh.start_time}`)) continue
          try {
            await api('/api/v1/storeops/shifts', { method: 'POST', body: JSON.stringify({
              employee_id: sh.employee_id, employee_name: sh.employee_name, store_code: sh.store_code,
              shift_date: nd, start_time: sh.start_time, end_time: sh.end_time,
              scheduled_hours: sh.scheduled_hours, status: 'scheduled',
            }) })
            added++
          } catch { /* skip blocked (time-off) / failed rows, keep going */ }
        }
      }
      alert(`Copied ${added} shift${added === 1 ? '' : 's'}.`)
      nextWeek()
    } finally { setBusy(false) }
  }

  async function deleteShift(id: number) {
    if (!confirm('Remove this shift?')) return
    await api(`/api/v1/storeops/shifts/${id}`, { method: 'DELETE' })
    setShifts(s => s.filter(sh => sh.id !== id))
  }

  function openAdd(date: string, fixed: { store?: string; emp?: string }) {
    const emp = fixed.emp ? employees.find(e => e.name === fixed.emp) : undefined
    setNewShift({
      start_time: '10:00', end_time: '18:00',
      store_code: fixed.store || emp?.home_store || (filterStore || ''),
      employee_name: fixed.emp || '',
    })
    setAddModal({ date, ...fixed })
  }

  async function addShift() {
    if (!addModal) return
    const store_code = addModal.store || newShift.store_code
    const employee_name = addModal.emp || newShift.employee_name
    if (!store_code) { alert('Pick a store.'); return }
    if (!employee_name) { alert('Pick an employee.'); return }
    const emp = employees.find(e => e.name === employee_name)
    const payload = {
      employee_id: emp?.id?.toString() || '',
      employee_name,
      store_code,
      shift_date: addModal.date,
      start_time: newShift.start_time,
      end_time: newShift.end_time,
      scheduled_hours: hoursBetween(newShift.start_time, newShift.end_time),
      status: 'scheduled',
    }
    setBusy(true)
    try {
      const created = await api('/api/v1/storeops/shifts', { method: 'POST', body: JSON.stringify(payload) })
      setShifts(s => [...s, created])
      setAddModal(null)
    } catch (e: any) {
      // Backend returns 409 when the employee has approved time-off on this date.
      alert(e?.message || 'Could not add shift.')
    } finally { setBusy(false) }
  }

  // ── export payload (current view) ──
  function buildPayload(): ExportPayload {
    const rows = (view === 'store' ? filteredStores : filteredEmps).map((r: any) => {
      const isStore = view === 'store'
      const label = isStore ? r.store_code : r.name
      const rowShifts = isStore
        ? shiftsOf(s => s.store_code === r.store_code)
        : shiftsOf(s => s.employee_name === r.name)
      const cells = weekDates.map(d => rowShifts.filter(s => s.shift_date === d)
        .map(s => `${isStore ? s.employee_name : s.store_code} ${s.start_time}-${s.end_time}`).join('; '))
      const total = rowShifts.reduce((a, s) => a + (s.scheduled_hours || 0), 0)
      return { label, cells, total }
    })
    const cols = [
      { header: view === 'store' ? 'Store' : 'Employee', get: (r: any) => r.label },
      ...weekDates.map((d, i) => { const { dow, md } = dayLabel(d); return { header: `${dow} ${md}`, get: (r: any) => r.cells[i] } }),
      { header: 'Total Hrs', get: (r: any) => r.total.toFixed(1), align: 'right' as const },
    ]
    return {
      title: `Schedule — week of ${parseLocalDate(weekStart).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}`,
      subtitle: `${view === 'store' ? 'By store' : 'By employee'}${filterMarket ? ` · ${filterMarket}` : ''}`,
      filename: `schedule_${weekStart}`,
      sheets: [{ name: 'Schedule', columns: cols, rows }],
    }
  }

  const today = localToday()
  const rows = view === 'store' ? filteredStores : filteredEmps

  // Render the chips of shifts inside one grid cell.
  const Cell = ({ date, cellShifts, onAdd }: { date: string; cellShifts: Shift[]; onAdd: () => void }) => (
    <td style={{ padding: '4px 6px', textAlign: 'center', borderRight: '1px solid var(--border)', cursor: 'pointer', verticalAlign: 'top', background: date === today ? 'rgba(37,99,235,0.04)' : undefined }}
      onClick={onAdd}>
      {cellShifts.map(s => (
        <div key={s.id} style={{ background: 'var(--accent2)', borderRadius: 6, padding: '3px 6px', fontSize: 11, color: 'white', position: 'relative', marginBottom: 3, textAlign: 'left' }}>
          <div style={{ fontWeight: 600, paddingRight: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {view === 'store' ? s.employee_name : s.store_code}
          </div>
          <div style={{ opacity: 0.85 }}>{s.start_time}–{s.end_time}</div>
          <button onClick={e => { e.stopPropagation(); deleteShift(s.id) }}
            style={{ position: 'absolute', top: 2, right: 4, background: 'none', border: 'none', color: 'rgba(255,255,255,0.8)', cursor: 'pointer', fontSize: 10, padding: 0 }}>✕</button>
        </div>
      ))}
      <div className="add-shift" style={{ height: cellShifts.length ? 16 : 36, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 16, opacity: 0 }}>+</div>
    </td>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Schedule</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Week of {parseLocalDate(weekStart).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <div className="seg" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: view === 'store' ? 'var(--accent)' : 'transparent', color: view === 'store' ? 'white' : 'var(--text2)' }} onClick={() => setView('store')}>By store</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: view === 'employee' ? 'var(--accent)' : 'transparent', color: view === 'employee' ? 'white' : 'var(--text2)' }} onClick={() => setView('employee')}>By employee</button>
          </div>
          <select className="select" value={filterMarket} onChange={e => { setFilterMarket(e.target.value); setFilterStore('') }}>
            <option value="">All markets</option>
            {markets.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <select className="select" value={filterStore} onChange={e => setFilterStore(e.target.value)}>
            <option value="">All stores</option>
            {stores.filter(s => !filterMarket || s.market === filterMarket).map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.address?.substring(0, 26)}</option>)}
          </select>
          <button className="btn btn-secondary" onClick={prevWeek}>← Prev</button>
          <button className="btn btn-secondary" onClick={() => setWeekStart(mondayOf())}>Today</button>
          <button className="btn btn-secondary" onClick={nextWeek}>Next →</button>
          <button className="btn btn-primary" disabled={busy} onClick={copyWeeks} title="Duplicate this week's shifts into one or more following weeks">📋 Copy weeks</button>
          <ExportButtons payload={buildPayload} compact />
        </div>
      </div>

      {conflicts.length > 0 && (
        <div style={{ background: '#fef3c7', border: '1px solid #f59e0b', color: '#92400e', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13 }}>
          ⚠️ Double-booking detected:{' '}
          {conflicts.slice(0, 4).map((c, i) => (
            <span key={i}><strong>{c.emp}</strong> at {c.stores.join(' & ')} on {dayLabel(c.date).dow} {dayLabel(c.date).md}{i < Math.min(conflicts.length, 4) - 1 ? '; ' : ''}</span>
          ))}
          {conflicts.length > 4 && ` +${conflicts.length - 4} more`}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
            <thead>
              <tr style={{ background: 'var(--surface2)', borderBottom: '2px solid var(--border)' }}>
                <th style={{ padding: '10px 14px', color: 'var(--text)', fontSize: 12, fontWeight: 700, textAlign: 'left', width: 170 }}>
                  {view === 'store' ? 'Store' : 'Employee'}
                </th>
                {weekDates.map(date => {
                  const { dow, md } = dayLabel(date)
                  const isToday = date === today
                  return (
                    <th key={date} style={{ padding: '8px', color: 'var(--text)', fontSize: 12, fontWeight: 700, textAlign: 'center', background: isToday ? 'rgba(37,99,235,0.12)' : undefined, borderBottom: isToday ? '2px solid var(--accent)' : undefined }}>
                      <div>{dow}</div>
                      <div style={{ fontWeight: 500, color: 'var(--text2)' }}>{md}</div>
                    </th>
                  )
                })}
                <th style={{ padding: '10px 14px', color: 'var(--text)', fontSize: 12, fontWeight: 700, textAlign: 'right' }}>Total Hrs</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: any, ri: number) => {
                const isStore = view === 'store'
                const label = isStore ? r.store_code : r.name
                const sub = isStore ? (r.market || r.address?.substring(0, 22)) : r.role
                const rowShifts = isStore ? shiftsOf(s => s.store_code === r.store_code) : shiftsOf(s => s.employee_name === r.name)
                const total = rowShifts.reduce((a, s) => a + (s.scheduled_hours || 0), 0)
                return (
                  <tr key={r.id ?? label} style={{ background: ri % 2 === 1 ? 'var(--surface2)' : 'white' }}>
                    <td style={{ padding: '8px 14px', fontWeight: 600, fontSize: 13, borderRight: '1px solid var(--border)' }}>
                      <div>{label}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{sub}</div>
                    </td>
                    {weekDates.map(date => (
                      <Cell key={date} date={date}
                        cellShifts={rowShifts.filter(s => s.shift_date === date)}
                        onAdd={() => openAdd(date, isStore ? { store: r.store_code } : { emp: r.name })} />
                    ))}
                    <td style={{ padding: '8px 14px', textAlign: 'right', fontWeight: 700, fontSize: 13 }}>{total.toFixed(1)}h</td>
                  </tr>
                )
              })}
              {rows.length === 0 && (
                <tr><td colSpan={9} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No {view === 'store' ? 'stores' : 'employees'} match the filter.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Add shift modal */}
      {addModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}>
          <div className="card" style={{ width: 360 }}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 6 }}>Add Shift</div>
            <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14 }}>
              {parseLocalDate(addModal.date).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
            </div>
            {/* Employee: fixed in employee-view, a picker in store-view */}
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Employee</label>
              {addModal.emp ? (
                <div style={{ fontWeight: 600, marginTop: 4 }}>{addModal.emp}</div>
              ) : (
                <select className="select" style={{ width: '100%', marginTop: 4 }} value={newShift.employee_name}
                  onChange={e => setNewShift(s => ({ ...s, employee_name: e.target.value }))}>
                  <option value="">Select employee…</option>
                  {employees.map(e => <option key={e.id} value={e.name}>{e.name}{e.home_store ? ` (${e.home_store})` : ''}</option>)}
                </select>
              )}
            </div>
            {/* Store: fixed in store-view, a picker in employee-view (fixes "can't add store to employee") */}
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Store</label>
              {addModal.store ? (
                <div style={{ fontWeight: 600, marginTop: 4 }}>{addModal.store}</div>
              ) : (
                <select className="select" style={{ width: '100%', marginTop: 4 }} value={newShift.store_code}
                  onChange={e => setNewShift(s => ({ ...s, store_code: e.target.value }))}>
                  <option value="">Select store…</option>
                  {stores.filter(s => s.store_code).map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.address?.substring(0, 24)}</option>)}
                </select>
              )}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Start</label>
                <input className="input" type="time" value={newShift.start_time}
                  onChange={e => setNewShift(s => ({ ...s, start_time: e.target.value }))} style={{ marginTop: 4 }} />
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>End</label>
                <input className="input" type="time" value={newShift.end_time}
                  onChange={e => setNewShift(s => ({ ...s, end_time: e.target.value }))} style={{ marginTop: 4 }} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" style={{ flex: 1 }} disabled={busy} onClick={addShift}>{busy ? 'Adding…' : 'Add Shift'}</button>
              <button className="btn btn-secondary" onClick={() => setAddModal(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        tr:hover .add-shift { opacity: 0.6 !important; }
        .add-shift:hover { color: var(--accent2) !important; opacity: 1 !important; }
      `}</style>
    </div>
  )
}
