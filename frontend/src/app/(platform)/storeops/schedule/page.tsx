'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

interface Shift {
  id: number
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

function getWeekDates(weekStart: Date) {
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart)
    d.setDate(d.getDate() + i)
    return d.toISOString().split('T')[0]
  })
}

export default function SchedulePage() {
  const [weekStart, setWeekStart] = useState(() => {
    const d = new Date()
    const day = d.getDay()
    d.setDate(d.getDate() - (day === 0 ? 6 : day - 1))
    return d.toISOString().split('T')[0]
  })
  const [shifts, setShifts] = useState<Shift[]>([])
  const [employees, setEmployees] = useState<Employee[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [filterStore, setFilterStore] = useState('')
  const [loading, setLoading] = useState(true)
  const [addModal, setAddModal] = useState<{date: string, emp: string} | null>(null)
  const [newShift, setNewShift] = useState({ start_time: '09:00', end_time: '17:00' })

  const weekEnd = (() => {
    const d = new Date(weekStart)
    d.setDate(d.getDate() + 6)
    return d.toISOString().split('T')[0]
  })()
  const weekDates = getWeekDates(new Date(weekStart))

  useEffect(() => {
    Promise.all([
      api(`/api/v1/storeops/shifts?week_start=${weekStart}&week_end=${weekEnd}`),
      api('/api/v1/storeops/employees'),
      api('/api/v1/storeops/stores'),
    ]).then(([s, e, st]) => { setShifts(s); setEmployees(e); setStores(st) })
      .catch(console.error).finally(() => setLoading(false))
  }, [weekStart])

  const filteredEmps = filterStore
    ? employees.filter(e => e.home_store === filterStore)
    : employees

  function getShift(empName: string, date: string) {
    return shifts.find(s => s.employee_name === empName && s.shift_date === date)
  }

  function prevWeek() {
    const d = new Date(weekStart); d.setDate(d.getDate() - 7)
    setWeekStart(d.toISOString().split('T')[0])
  }
  function nextWeek() {
    const d = new Date(weekStart); d.setDate(d.getDate() + 7)
    setWeekStart(d.toISOString().split('T')[0])
  }

  async function deleteShift(id: number) {
    if (!confirm('Remove this shift?')) return
    await api(`/api/v1/storeops/shifts/${id}`, { method: 'DELETE' })
    setShifts(s => s.filter(sh => sh.id !== id))
  }

  async function addShift() {
    if (!addModal) return
    const [sh, eh] = [newShift.start_time, newShift.end_time].map(t => parseInt(t.split(':')[0]))
    const [sm, em] = [newShift.start_time, newShift.end_time].map(t => parseInt(t.split(':')[1]))
    const hrs = ((eh * 60 + em) - (sh * 60 + sm)) / 60
    const emp = employees.find(e => e.name === addModal.emp)
    const payload = {
      employee_id: emp?.id?.toString() || '',
      employee_name: addModal.emp,
      store_code: filterStore || emp?.home_store || '',
      shift_date: addModal.date,
      start_time: newShift.start_time,
      end_time: newShift.end_time,
      scheduled_hours: Math.max(0, hrs),
      status: 'scheduled',
    }
    const created = await api('/api/v1/storeops/shifts', { method: 'POST', body: JSON.stringify(payload) })
    setShifts(s => [...s, created])
    setAddModal(null)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Schedule</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Week of {new Date(weekStart).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select className="select" value={filterStore} onChange={e => setFilterStore(e.target.value)}>
            <option value="">All stores</option>
            {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.address?.substring(0, 30)}</option>)}
          </select>
          <button className="btn btn-secondary" onClick={prevWeek}>← Prev</button>
          <button className="btn btn-secondary" onClick={() => setWeekStart(new Date().toISOString().split('T')[0])}>Today</button>
          <button className="btn btn-secondary" onClick={nextWeek}>Next →</button>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
            <thead>
              <tr style={{ background: 'var(--accent)' }}>
                <th style={{ padding: '10px 14px', color: 'white', fontSize: 12, fontWeight: 600, textAlign: 'left', width: 160 }}>
                  Employee
                </th>
                {weekDates.map((date, i) => {
                  const d = new Date(date)
                  const isToday = date === new Date().toISOString().split('T')[0]
                  return (
                    <th key={date} style={{ padding: '10px 8px', color: 'white', fontSize: 12, fontWeight: 600, textAlign: 'center', background: isToday ? 'rgba(255,255,255,0.15)' : undefined }}>
                      <div>{DAYS[i]}</div>
                      <div style={{ fontWeight: 400, opacity: 0.7 }}>{d.getMonth()+1}/{d.getDate()}</div>
                    </th>
                  )
                })}
                <th style={{ padding: '10px 14px', color: 'white', fontSize: 12, fontWeight: 600, textAlign: 'right' }}>
                  Total Hrs
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredEmps.map((emp, ei) => {
                const empShifts = shifts.filter(s => s.employee_name === emp.name)
                const totalHrs = empShifts.reduce((s, sh) => s + (sh.scheduled_hours || 0), 0)
                return (
                  <tr key={emp.id} style={{ background: ei % 2 === 1 ? 'var(--surface2)' : 'white' }}>
                    <td style={{ padding: '8px 14px', fontWeight: 500, fontSize: 13, borderRight: '1px solid var(--border)' }}>
                      <div>{emp.name}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)' }}>{emp.role}</div>
                    </td>
                    {weekDates.map(date => {
                      const shift = getShift(emp.name, date)
                      return (
                        <td key={date} style={{ padding: '4px 6px', textAlign: 'center', borderRight: '1px solid var(--border)', cursor: 'pointer', verticalAlign: 'middle' }}
                          onClick={() => !shift && setAddModal({ date, emp: emp.name })}>
                          {shift ? (
                            <div style={{ background: 'var(--accent2)', borderRadius: 6, padding: '4px 6px', fontSize: 11, color: 'white', position: 'relative' }}>
                              <div style={{ fontWeight: 600 }}>{shift.start_time}–{shift.end_time}</div>
                              <div style={{ opacity: 0.8 }}>{shift.scheduled_hours}h</div>
                              <button
                                onClick={e => { e.stopPropagation(); deleteShift(shift.id) }}
                                style={{ position: 'absolute', top: 2, right: 4, background: 'none', border: 'none',
                                  color: 'rgba(255,255,255,0.7)', cursor: 'pointer', fontSize: 10, padding: 0 }}>
                                ✕
                              </button>
                            </div>
                          ) : (
                            <div style={{ height: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--border)', fontSize: 18, opacity: 0 }}
                              className="add-shift">+</div>
                          )}
                        </td>
                      )
                    })}
                    <td style={{ padding: '8px 14px', textAlign: 'right', fontWeight: 700, fontSize: 13 }}>
                      {totalHrs.toFixed(1)}h
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add shift modal */}
      {addModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}>
          <div className="card" style={{ width: 340 }}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 16 }}>Add Shift</div>
            <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16 }}>
              <strong>{addModal.emp}</strong> on {new Date(addModal.date).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>Start Time</label>
                <input className="input" type="time" value={newShift.start_time}
                  onChange={e => setNewShift(s => ({ ...s, start_time: e.target.value }))} style={{ marginTop: 4 }} />
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>End Time</label>
                <input className="input" type="time" value={newShift.end_time}
                  onChange={e => setNewShift(s => ({ ...s, end_time: e.target.value }))} style={{ marginTop: 4 }} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" style={{ flex: 1 }} onClick={addShift}>Add Shift</button>
              <button className="btn btn-secondary" onClick={() => setAddModal(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        tr:hover .add-shift { opacity: 1 !important; }
        .add-shift:hover { color: var(--accent2) !important; }
      `}</style>
    </div>
  )
}
