'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, localToday, parseLocalDate, addDays, mondayOf } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

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
interface Store { id: number; store_code: string; address: string; market: string; is_active?: boolean }

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

// Tenant-aware work-week start (storeops.tenants.work_week_start_dow, mig 085 — 0=Mon..6=Sun;
// e.g. Luxelink=3/Thursday). `mondayOf()` in lib/client.ts is always Monday, so the schedule grid
// defaulted to Mon-Sun for EVERY tenant regardless of their actual configured work week — for a
// tenant on a different cycle the grid didn't line up with their real pay period ("not wired
// properly"). This generalizes it locally (client.ts is shared/core, not ours to edit) without
// touching mondayOf() itself, so a tenant with dow=0 (the default — Boost included) is
// byte-identical to the old mondayOf() behavior.
function workWeekStartOf(dow: number, iso?: string) {
  const d = iso ? parseLocalDate(iso) : new Date()
  const cur = d.getDay() === 0 ? 6 : d.getDay() - 1   // 0=Mon..6=Sun
  const delta = (cur - (dow || 0) + 7) % 7
  d.setDate(d.getDate() - delta)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export default function SchedulePage() {
  const [weekStart, setWeekStart] = useState(() => mondayOf())
  const [wwDow, setWwDow] = useState(0)   // tenant's work-week-start dow (0=Mon default; Boost stays 0)
  const [shifts, setShifts] = useState<Shift[]>([])
  const [employees, setEmployees] = useState<Employee[]>([])   // span-scoped — drives the grid
  const [allEmps, setAllEmps] = useState<Employee[]>([])       // whole-company roster — for the picker
  const [stores, setStores] = useState<Store[]>([])
  const [view, setView] = useState<'store' | 'employee'>('store')
  // Markets are MULTI-select so a market/district manager can build the schedule for every store
  // across all the markets he runs in one grid. Empty selection = all markets (the old default).
  const [filterMarkets, setFilterMarkets] = useState<string[]>([])
  const [mktOpen, setMktOpen] = useState(false)
  const [filterStore, setFilterStore] = useState('')
  const [loading, setLoading] = useState(true)
  // addModal carries the day + the fixed dimension (store OR emp) depending on the view.
  const [addModal, setAddModal] = useState<{ date: string; store?: string; emp?: string; editId?: number } | null>(null)
  const [newShift, setNewShift] = useState({ start_time: '10:00', end_time: '18:00', store_code: '', employee_name: '' })
  const [busy, setBusy] = useState(false)
  const [timeOff, setTimeOff] = useState<any[]>([])
  // Non-blocking heads-up when a shift was scheduled over an employee's approved time off (the
  // backend's default 'warn' policy — see PUT /storeops/timeoff-conflict-mode). Dismissible;
  // never prevents the shift that already saved.
  const [notice, setNotice] = useState<string | null>(null)

  const weekEnd = addDays(weekStart, 6)
  const weekDates = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart])

  // Snap the default view to the tenant's OWN work-week start once we know it (no-op for the
  // default Monday tenants — e.g. Boost — since dow===0 leaves weekStart unchanged).
  useEffect(() => {
    let cancelled = false
    api('/api/v1/core/tenant-settings').then((r: any) => {
      const dow = r?.settings?.work_week_start_dow
      if (!cancelled && typeof dow === 'number' && dow !== 0) {
        setWwDow(dow)
        setWeekStart(workWeekStartOf(dow))
      }
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api(`/api/v1/storeops/shifts?week_start=${weekStart}&week_end=${weekEnd}`),
      api('/api/v1/storeops/employees'),
      api('/api/v1/storeops/stores'),
      api('/api/v1/storeops/time-off').catch(() => []),
      api('/api/v1/storeops/employees?all_company=true').catch(() => []),
    ]).then(([s, e, st, to, ae]) => { setShifts(s || []); setEmployees(e || []); setStores(st || []); setTimeOff(to || []); setAllEmps((ae && ae.length ? ae : e) || []) })
      .catch(console.error).finally(() => setLoading(false))
  }, [weekStart])

  // market lookup by store_code (employees only carry home_store)
  const mktOf = useMemo(() => {
    const m: Record<string, string> = {}
    stores.forEach(s => { if (s.store_code) m[s.store_code] = s.market || '' })
    return m
  }, [stores])
  const markets = useMemo(() => Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort(), [stores])

  // Approved time-off → {employee_name: Set(dates off this week)} for the grid (employee view).
  const empById = useMemo(() => Object.fromEntries(employees.map(e => [String(e.id), e.name])), [employees])
  const offByName = useMemo(() => {
    const m: Record<string, Set<string>> = {}
    for (const t of timeOff) {
      if (String(t.status || '').toLowerCase() !== 'approved') continue
      const name = t.employee_name || empById[String(t.employee_id)]
      if (!name || !t.start_date || !t.end_date) continue
      let cur = t.start_date < weekStart ? weekStart : t.start_date
      const end = t.end_date > weekEnd ? weekEnd : t.end_date
      let guard = 0
      while (cur <= end && guard++ < 60) { (m[name] ||= new Set()).add(cur); cur = addDays(cur, 1) }
    }
    return m
  }, [timeOff, empById, weekStart, weekEnd])

  // Multi-market filter: empty selection = all markets (matches the old "All markets" default).
  const selMkt = useMemo(() => new Set(filterMarkets), [filterMarkets])
  function toggleMarket(m: string) {
    setFilterMarkets(prev => prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m])
    setFilterStore('') // a store filter from a now-hidden market would otherwise show nothing
  }
  const filteredStores = stores.filter(s =>
    s.store_code &&
    (selMkt.size === 0 || selMkt.has(s.market)) &&
    (!filterStore || s.store_code === filterStore))
  const filteredEmps = employees.filter(e =>
    (selMkt.size === 0 || selMkt.has(mktOf[e.home_store])) &&
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

  // By-store coverage gaps: a store that IS being scheduled this week but has a day with nobody on it.
  // Restricting to stores already in rotation keeps closed/out-of-scope stores from being false positives.
  const coverageGaps = useMemo(() => {
    if (view !== 'store') return [] as { store: string; date: string }[]
    const covered = new Set(shifts.map(s => `${s.store_code}|${s.shift_date}`))
    const inRotation = new Set(shifts.map(s => s.store_code))
    const gaps: { store: string; date: string }[] = []
    for (const st of filteredStores) {
      if (!st.store_code || !inRotation.has(st.store_code)) continue
      for (const d of weekDates) if (!covered.has(`${st.store_code}|${d}`)) gaps.push({ store: st.store_code, date: d })
    }
    return gaps
  }, [view, shifts, filteredStores, weekDates])

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

  // Pull LAST week's shifts into the current week (dedup against this week).
  async function copyFromLastWeek() {
    const ds = addDays(weekStart, -7), de = addDays(ds, 6)
    const prev = await api(`/api/v1/storeops/shifts?week_start=${ds}&week_end=${de}`).catch(() => [])
    if (!prev?.length) { alert('No shifts last week to copy.'); return }
    if (!confirm(`Copy ${prev.length} shifts from last week into this week? Existing shifts are kept (duplicates skipped).`)) return
    setBusy(true)
    try {
      const seen = new Set(shifts.map(s => `${s.employee_name}|${s.shift_date}|${s.start_time}`))
      const created: any[] = []; let added = 0
      for (const sh of prev) {
        const nd = addDays(sh.shift_date, 7)
        if (seen.has(`${sh.employee_name}|${nd}|${sh.start_time}`)) continue
        try {
          const c = await api('/api/v1/storeops/shifts', { method: 'POST', body: JSON.stringify({
            employee_id: sh.employee_id, employee_name: sh.employee_name, store_code: sh.store_code,
            shift_date: nd, start_time: sh.start_time, end_time: sh.end_time,
            scheduled_hours: sh.scheduled_hours, status: 'scheduled',
          }) })
          created.push(c); added++
        } catch { /* skip blocked (time-off) / failed rows */ }
      }
      setShifts(s => [...s, ...created])
      alert(`Copied ${added} shift${added === 1 ? '' : 's'} from last week.`)
    } finally { setBusy(false) }
  }

  // Recurring templates: save this week as the per-employee template; apply templates to this week.
  async function saveTemplate() {
    if (!shifts.length) { alert('No shifts this week to save as a template.'); return }
    if (!confirm("Save this week's shifts as the recurring weekly template? Replaces existing templates for these employees.")) return
    setBusy(true)
    try { const r = await api('/api/v1/storeops/shift-templates/save-week', { method: 'POST', body: JSON.stringify({ week_start: weekStart }) }); alert(`Saved ${r.saved} template entries for ${r.employees} employee(s).`) }
    catch (e: any) { alert(e?.message || 'Could not save template.') } finally { setBusy(false) }
  }
  async function applyTemplate() {
    if (!confirm('Fill this week from the saved templates? Existing shifts are kept (duplicates skipped).')) return
    setBusy(true)
    try {
      const r = await api('/api/v1/storeops/shift-templates/apply', { method: 'POST', body: JSON.stringify({ week_start: weekStart }) })
      const s = await api(`/api/v1/storeops/shifts?week_start=${weekStart}&week_end=${weekEnd}`)
      setShifts(s || [])
      alert(`Added ${r.added} shift${r.added === 1 ? '' : 's'} from templates${r.skipped_timeoff ? `, skipped ${r.skipped_timeoff} (time off)` : ''}.`)
    } catch (e: any) { alert(e?.message || 'Could not apply template.') } finally { setBusy(false) }
  }

  async function deleteShift(id: number) {
    if (!confirm('Remove this shift?')) return
    await api(`/api/v1/storeops/shifts/${id}`, { method: 'DELETE' })
    setShifts(s => s.filter(sh => sh.id !== id))
  }

  function openAdd(date: string, fixed: { store?: string; emp?: string }) {
    const emp = fixed.emp ? allEmps.find(e => e.name === fixed.emp) : undefined
    setNewShift({
      start_time: '10:00', end_time: '18:00',
      store_code: fixed.store || emp?.home_store || (filterStore || ''),
      employee_name: fixed.emp || '',
    })
    setAddModal({ date, ...fixed })
  }

  // Edit an existing shift — reuse the modal (store + employee fixed; adjust the times).
  function openEdit(s: Shift) {
    setNewShift({ start_time: s.start_time, end_time: s.end_time, store_code: s.store_code, employee_name: s.employee_name })
    setAddModal({ date: s.shift_date, store: s.store_code, emp: s.employee_name, editId: s.id })
  }

  async function addShift() {
    if (!addModal) return
    if (addModal.editId) {
      setBusy(true)
      try {
        const upd = { start_time: newShift.start_time, end_time: newShift.end_time,
          scheduled_hours: hoursBetween(newShift.start_time, newShift.end_time) }
        await api(`/api/v1/storeops/shifts/${addModal.editId}`, { method: 'PATCH', body: JSON.stringify(upd) })
        setShifts(s => s.map(sh => sh.id === addModal.editId ? { ...sh, ...upd } : sh))
        setAddModal(null)
      } catch (e: any) { alert(e?.message || 'Could not save shift.') } finally { setBusy(false) }
      return
    }
    const store_code = addModal.store || newShift.store_code
    const employee_name = addModal.emp || newShift.employee_name
    if (!store_code) { alert('Pick a store.'); return }
    if (!employee_name) { alert('Pick an employee.'); return }
    // Same data that draws the 🌴 OFF badge — confirm before scheduling over an employee's own
    // approved time off on this day (the backend now ALLOWS it by default; this is a courtesy
    // check-with-the-manager step, not a hard stop).
    if (offByName[employee_name]?.has(addModal.date)) {
      if (!confirm(`${employee_name} has approved time off on ${addModal.date}. Schedule anyway?`)) return
    }
    const emp = allEmps.find(e => e.name === employee_name)
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
      // Backend-confirmed heads-up (org's policy is 'warn', default for every tenant) — the shift
      // already saved; this never blocks, just informs.
      if (created?.timeoff_warning) setNotice(created.timeoff_warning)
    } catch (e: any) {
      // Backend still 409s when the tenant opted into 'block' via the timeoff-conflict-mode
      // setting (default is 'warn', which allows the schedule + returns `timeoff_warning` above).
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
      subtitle: `${view === 'store' ? 'By store' : 'By employee'}${filterMarkets.length ? ` · ${filterMarkets.join(', ')}` : ''}`,
      filename: `schedule_${weekStart}`,
      sheets: [{ name: 'Schedule', columns: cols, rows }],
    }
  }

  const today = localToday()
  const rows = view === 'store' ? filteredStores : filteredEmps

  // Render the chips of shifts inside one grid cell.
  const Cell = ({ date, cellShifts, onAdd, off }: { date: string; cellShifts: Shift[]; onAdd: () => void; off?: boolean }) => (
    <td style={{ padding: '4px 6px', textAlign: 'center', borderRight: '1px solid var(--border)', cursor: 'pointer', verticalAlign: 'top', background: off ? 'rgba(245,158,11,0.10)' : date === today ? 'rgba(37,99,235,0.04)' : undefined }}
      onClick={onAdd}>
      {off && <div style={{ fontSize: 10, color: '#b45309', fontWeight: 700, marginBottom: 2 }} title="Approved time off">🌴 OFF</div>}
      {cellShifts.map(s => (
        <div key={s.id} title="Click to edit times" onClick={e => { e.stopPropagation(); openEdit(s) }}
          style={{ background: 'var(--accent2)', borderRadius: 6, padding: '3px 6px', fontSize: 11, color: 'white', position: 'relative', marginBottom: 3, textAlign: 'left' }}>
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
          {/* Multi-select markets: pick one or several markets to schedule a whole district at once. */}
          <div style={{ position: 'relative' }}>
            <button className="btn btn-secondary" onClick={() => setMktOpen(o => !o)}
              style={{ minWidth: 150, display: 'inline-flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 180 }}>
                {filterMarkets.length === 0 ? 'All markets' : filterMarkets.length === 1 ? filterMarkets[0] : `${filterMarkets.length} markets`}
              </span>
              <span style={{ fontSize: 10, opacity: 0.7 }}>▾</span>
            </button>
            {mktOpen && (
              <>
                <div onClick={() => setMktOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
                <div className="card" style={{ position: 'absolute', top: '100%', right: 0, marginTop: 4, zIndex: 41, width: 240, maxHeight: 320, overflowY: 'auto', padding: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.18)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0 4px 8px', borderBottom: '1px solid var(--border)', marginBottom: 6 }}>
                    <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: 12 }} onClick={() => { setFilterMarkets(markets); setFilterStore('') }}>Select all</button>
                    <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: 12 }} onClick={() => { setFilterMarkets([]); setFilterStore('') }}>Clear</button>
                  </div>
                  {markets.length === 0 && <div style={{ padding: 8, color: 'var(--text3)', fontSize: 13 }}>No markets</div>}
                  {markets.map(m => (
                    <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 6px', cursor: 'pointer', fontSize: 13, borderRadius: 6 }}>
                      <input type="checkbox" checked={filterMarkets.includes(m)} onChange={() => toggleMarket(m)} />
                      <span>{m}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </div>
          <select className="select" value={filterStore} onChange={e => setFilterStore(e.target.value)}>
            <option value="">All stores</option>
            {stores.filter(s => s.store_code && (selMkt.size === 0 || selMkt.has(s.market))).map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.address?.substring(0, 26)}</option>)}
          </select>
          <button className="btn btn-secondary" onClick={prevWeek}>← Prev</button>
          <button className="btn btn-secondary" onClick={() => setWeekStart(workWeekStartOf(wwDow))}>Today</button>
          <button className="btn btn-secondary" onClick={nextWeek}>Next →</button>
          <button className="btn btn-secondary" disabled={busy} onClick={copyFromLastWeek} title="Pull last week's shifts into this week">⬅️ Copy last week</button>
          <button className="btn btn-primary" disabled={busy} onClick={copyWeeks} title="Duplicate this week's shifts into one or more following weeks">📋 Copy weeks</button>
          <button className="btn btn-secondary" disabled={busy} onClick={saveTemplate} title="Save this week as the recurring weekly template">⭐ Save template</button>
          <button className="btn btn-secondary" disabled={busy} onClick={applyTemplate} title="Fill this week from the saved templates">📌 Apply template</button>
          <ExportButtons payload={buildPayload} compact />
          <SendReportButton reportKey="storeops_schedule" filters={{ week_start: weekStart, ...(filterStore ? { store_code: filterStore } : {}) }} compact />
        </div>
      </div>

      {notice && (
        <div style={{ background: '#eff6ff', border: '1px solid #3b82f6', color: '#1e3a8a', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
          <span>🌴 {notice}</span>
          <button onClick={() => setNotice(null)} style={{ background: 'none', border: 'none', color: '#1e3a8a', cursor: 'pointer', fontWeight: 700 }}>✕</button>
        </div>
      )}

      {conflicts.length > 0 && (
        <div style={{ background: '#fef3c7', border: '1px solid #f59e0b', color: '#92400e', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13 }}>
          ⚠️ Double-booking detected:{' '}
          {conflicts.slice(0, 4).map((c, i) => (
            <span key={i}><strong>{c.emp}</strong> at {c.stores.join(' & ')} on {dayLabel(c.date).dow} {dayLabel(c.date).md}{i < Math.min(conflicts.length, 4) - 1 ? '; ' : ''}</span>
          ))}
          {conflicts.length > 4 && ` +${conflicts.length - 4} more`}
        </div>
      )}

      {view === 'store' && coverageGaps.length > 0 && (
        <div style={{ background: '#fee2e2', border: '1px solid #ef4444', color: '#991b1b', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13 }}>
          🪧 Coverage gaps — {coverageGaps.length} store-day{coverageGaps.length === 1 ? '' : 's'} with nobody scheduled:{' '}
          {coverageGaps.slice(0, 6).map((g, i) => (
            <span key={i}><strong>{g.store}</strong> {dayLabel(g.date).dow} {dayLabel(g.date).md}{i < Math.min(coverageGaps.length, 6) - 1 ? '; ' : ''}</span>
          ))}
          {coverageGaps.length > 6 && ` +${coverageGaps.length - 6} more`}
          <span style={{ opacity: 0.75 }}> (only stores already scheduled this week)</span>
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
                        off={!isStore && (offByName[r.name]?.has(date) ?? false)}
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
                  {allEmps.map(e => <option key={e.id} value={e.name}>{e.name}{e.home_store ? ` (${e.home_store})` : ''}</option>)}
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
                  {/* 2026-07-25 fix: a NEW shift can only be assigned to an ACTIVE store — a closed
                      store (is_active=false) should stop generating new shifts/hours entirely. The
                      view-filter dropdown above intentionally still lists every store (incl. closed
                      ones), so historical schedule data stays viewable. */}
                  {stores.filter(s => s.store_code && s.is_active !== false).map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.address?.substring(0, 24)}</option>)}
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
              <button className="btn btn-primary" style={{ flex: 1 }} disabled={busy} onClick={addShift}>{busy ? 'Saving…' : (addModal.editId ? 'Save Shift' : 'Add Shift')}</button>
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
