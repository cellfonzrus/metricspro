'use client'
import { useState, useEffect, useMemo, useRef, Fragment } from 'react'
import { api, localToday, parseLocalDate, addDays, mondayOf } from '@/lib/client'
import { apiCached, LOOKUP, CONFIG } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { buildScheduleExport } from '../lib/scheduleExport'
import { scheduledAxes, buildShiftAttributor, shiftHours, shiftHrs, UNATTRIBUTED_KEY, type EmployeeBucket } from '../lib/scheduleAxes'
import { currentPeriodFromSettingsResponse } from '@/lib/pay-period'

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

interface Employee { id: number; employee_id?: string | null; name: string; home_store: string; role: string }
interface Store { id: number; store_code: string; address: string; market: string; is_active?: boolean; is_agent?: boolean }

// Trend report (GET /storeops/schedule/hours-trend) — WoW / MoM scheduled hours with drill-down.
interface TrendBucket {
  key: string; label: string; total: number; prev_total: number | null; delta: number | null
  by_store: Record<string, number>; by_employee: Record<string, number>
  store_deltas: Record<string, number>; employee_deltas: Record<string, number>
}
interface IncRow { employee: string; store: string; current: number; prior: number; delta: number }
interface Trend {
  total: number; weeks: TrendBucket[]; months: TrendBucket[]
  increasing: { week: IncRow[]; month: IncRow[] }
}

// Local-safe day-of-week + m/d label (never the old DAYS[i] index — that was the
// off-by-one: an array index can't know the real weekday after a UTC date shift).
function dayLabel(date: string) {
  const d = parseLocalDate(date)
  return { dow: d.toLocaleDateString('en-US', { weekday: 'short' }), md: `${d.getMonth() + 1}/${d.getDate()}` }
}
// Scheduled hours — ONE home (../lib/scheduleAxes, mirroring backend schedule_hours), so the
// footer total, each row total, the export and the trend report cannot drift apart. The overnight
// wrap and the zero-length guard live there; this page only re-exports the names it already used.
const hoursBetween = (start: string, end: string) => shiftHours(start, end)

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
  // Hours-trend report (WoW/MoM + drill-down + "who's increasing"). Reacts to the active market/store
  // filter and to the viewed week (the anchor), so it always reflects exactly the on-screen scope.
  const [trend, setTrend] = useState<Trend | null>(null)
  const [trendErr, setTrendErr] = useState<string | null>(null)
  const [reportOpen, setReportOpen] = useState(false)
  const [reportGran, setReportGran] = useState<'week' | 'month'>('week')
  const [drillKey, setDrillKey] = useState<string | null>(null)

  const weekEnd = addDays(weekStart, 6)
  const weekDates = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart])

  // Snap the default view to the tenant's OWN work-week start once we know it — and (Phase W2 period
  // coherence, owner directive 2026-09-01) ANCHOR the default week to the week containing the CURRENT
  // pay period's START (shared resolver: @/lib/pay-period over preview[0], the same period Payroll /
  // Payroll Tax default to). The grid stays a week grid (structural); only which week opens first
  // changes. For a weekly tenant the current period's start IS this work week, so this is a no-op
  // there; for a biweekly tenant mid-fortnight it opens on the period's first week instead of the
  // second. Degrades to the old behavior (today's week, tenant work-week snap) when the period can't
  // be resolved.
  useEffect(() => {
    let cancelled = false
    apiCached('/api/v1/core/tenant-settings', CONFIG).then((r: any) => {
      if (cancelled) return
      const dowRaw = r?.settings?.work_week_start_dow
      const dow = typeof dowRaw === 'number' ? dowRaw : 0
      if (dow !== 0) setWwDow(dow)
      const cur = currentPeriodFromSettingsResponse(r)
      if (cur) setWeekStart(workWeekStartOf(dow, cur.period.start))
      else if (dow !== 0) setWeekStart(workWeekStartOf(dow))
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api(`/api/v1/storeops/shifts?week_start=${weekStart}&week_end=${weekEnd}`),
      apiCached('/api/v1/storeops/employees', LOOKUP),
      // include_inactive=true: this page's OWN "view filter" dropdown deliberately still lists closed
      // stores (see the comment lower in this file) so historical schedule data stays viewable — the
      // "add shift" picker already independently filters to is_active!==false client-side.
      apiCached('/api/v1/storeops/stores?include_inactive=true', LOOKUP),
      api('/api/v1/storeops/time-off').catch(() => []),
      apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP).catch(() => []),
      // Agent stores (Boost sub-dealers, migration 904) are reporting-only — exclude them from the
      // schedule's store pickers/grid entirely (owner directive 2026-08-20). They still appear in
      // ePay/financial reporting; they are simply never schedulable.
    ]).then(([s, e, st, to, ae]) => { setShifts(s || []); setEmployees(e || []); setStores((st || []).filter((x: Store) => !x.is_agent)); setTimeOff(to || []); setAllEmps((ae && ae.length ? ae : e) || []) })
      .catch(console.error).finally(() => setLoading(false))
  }, [weekStart])

  // ── Deep-link prefill (mig 915: block-and-hold auto clock-in) ─────────────────────────────────────
  // A manager clicked "add them to the schedule" from a held-clock-in notification. The link carries
  // ?prefill=1&emp=&store=&date=&start= (all store-local). We jump to that week and open the add-shift
  // modal pre-filled with the employee, store, date, and START = the rep's tap time — the manager just
  // enters the END time and saves. Saving POSTs the shift, which auto-activates the held punch. Read
  // from window.location (the codebase's no-Suspense convention — see timeclock/page.tsx). Runs once,
  // after the roster/stores load so openAdd can resolve the employee; then clears the query.
  const prefillDoneRef = useRef(false)
  useEffect(() => {
    if (prefillDoneRef.current || loading || allEmps.length === 0) return
    let sp: URLSearchParams
    try { sp = new URLSearchParams(window.location.search) } catch { return }
    if (sp.get('prefill') !== '1') return
    const date = sp.get('date') || ''
    const store = sp.get('store') || ''
    const empName = sp.get('emp') || ''
    const start = sp.get('start') || '10:00'
    if (!date) { prefillDoneRef.current = true; return }
    prefillDoneRef.current = true
    // Show the week that contains the tap date, then open the modal on that day.
    setWeekStart(workWeekStartOf(wwDow, date))
    // Default END = tap start + 8h (clamped to 23:59) so the form is valid even before the manager
    // adjusts it — they can, and should, change it to the real shift end.
    const [sh, sm] = start.split(':').map((n) => parseInt(n, 10))
    const endH = Number.isFinite(sh) ? Math.min(sh + 8, 23) : 18
    const endM = Number.isFinite(sm) ? sm : 0
    const end = `${String(endH).padStart(2, '0')}:${String(endM).padStart(2, '0')}`
    setNewShift({ start_time: start, end_time: end, store_code: store, employee_name: empName })
    setAddModal({ date, store: store || undefined, emp: empName || undefined })
    setNotice(`${empName || 'This employee'} tapped in at ${store || 'a store'} at ${start} and is waiting — set the end time and save to clock them in.`)
    // strip the query so a refresh/back doesn't reopen the modal.
    try { window.history.replaceState(null, '', window.location.pathname) } catch { /* ignore */ }
  }, [loading, allEmps.length, wwDow])

  // Fetch the hours trend whenever the scope (market/store filter) or the anchor week changes. The
  // backend applies the SAME RBAC store keyset as the grid, so a DM never sees hours outside their span.
  useEffect(() => {
    const qs = new URLSearchParams({ anchor: weekEnd, weeks: '8', months: '6', week_start_dow: String(wwDow) })
    if (filterMarkets.length) qs.set('markets', filterMarkets.join(','))
    if (filterStore) qs.set('store_code', filterStore)
    let cancelled = false
    setTrendErr(null)
    api(`/api/v1/storeops/schedule/hours-trend?${qs.toString()}`)
      .then((r: Trend) => { if (!cancelled) setTrend(r) })
      .catch((e: any) => { if (!cancelled) { setTrend(null); setTrendErr(e?.message || 'Could not load the hours trend.') } })
    return () => { cancelled = true }
  }, [weekEnd, wwDow, filterMarkets, filterStore])

  // market lookup by store_code (employees only carry home_store)
  const mktOf = useMemo(() => {
    const m: Record<string, string> = {}
    stores.forEach(s => { if (s.store_code) m[s.store_code] = s.market || '' })
    return m
  }, [stores])
  const markets = useMemo(() => Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort(), [stores])

  // ── WHO IS THIS ROW ABOUT — resolved once, dereferenced everywhere (§STOREOPS-01) ─────────────
  // The roster used to answer it: the widest one we hold, so a shift belonging to somebody outside
  // the current view still names them instead of falling into "unattributed".
  const attrRoster = useMemo(() => {
    const seen = new Set<string>()
    const out: { id: number; employee_id?: string | null; name: string }[] = []
    for (const e of [...allEmps, ...employees]) {
      const k = String(e.employee_id || e.id)
      if (seen.has(k)) continue
      seen.add(k)
      out.push({ id: e.id, employee_id: e.employee_id, name: e.name })
    }
    return out
  }, [allEmps, employees])
  const attribute = useMemo(() => buildShiftAttributor(attrRoster), [attrRoster])
  // The "is this the same shift" key, for every copy/apply dedupe on this page. It is the PERSON,
  // never their spelling: keyed on `employee_name`, a dedupe stopped matching the moment somebody
  // was renamed and copying a week inserted every one of their shifts a second time.
  const dedupeWho = (s: { employee_id?: string | number | null; employee_name?: string | null }) => {
    const a = attribute(s.employee_id, s.employee_name)
    return a.binds ? a.key : `name:${String(s.employee_name || '').trim()}`
  }

  // Approved time-off → {employee_name: Set(dates off this week)} for the grid (employee view).
  const empById = useMemo(() => Object.fromEntries(employees.map(e => [String(e.id), e.name])), [employees])
  // Approved time off, keyed by the PERSON (§STOREOPS-01): a time-off row also carries a
  // denormalized `employee_name`, so keying the 🌴 badge on that string hid an approved day off
  // from the grid the moment the employee was renamed. Falls back to the raw spelling only when the
  // row names nobody on the roster — the same "never drop it silently" rule as the hours axes.
  const offByEmp = useMemo(() => {
    const m: Record<string, Set<string>> = {}
    for (const t of timeOff) {
      if (String(t.status || '').toLowerCase() !== 'approved') continue
      const name = t.employee_name || empById[String(t.employee_id)]
      if (!name || !t.start_date || !t.end_date) continue
      const a = attribute(t.employee_id, name)
      const key = a.binds ? a.key : `name:${name}`
      let cur = t.start_date < weekStart ? weekStart : t.start_date
      const end = t.end_date > weekEnd ? weekEnd : t.end_date
      let guard = 0
      while (cur <= end && guard++ < 60) { (m[key] ||= new Set()).add(cur); cur = addDays(cur, 1) }
    }
    return m
  }, [timeOff, empById, weekStart, weekEnd, attribute])

  // The off-lookup for a bucket or a raw name — one helper, so no caller re-derives the key.
  const isOff = (bucket: { key?: string; name?: string } | null | undefined, date: string) => {
    if (!bucket) return false
    if (bucket.key && offByEmp[bucket.key]?.has(date)) return true
    return !!(bucket.name && offByEmp[`name:${bucket.name}`]?.has(date))
  }

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

  // ── SCOPE IS DECIDED ONCE (§STOREOPS-01) ───────────────────────────────────────────────────────
  // The market/store filter narrows the SHIFTS, and both axes are then built from that one scoped
  // row set. It used to narrow the ROWS instead, differently per view: by store code on the store
  // axis, by the employee's HOME store on the employee axis. Those are two different questions
  // ("worked here" vs "based here"), which is a second way the two totals could disagree for the
  // same filter.
  const scopedShifts = useMemo(() => {
    const codes = new Set(filteredStores.map(s => s.store_code))
    return shifts.filter(s => codes.has(s.store_code))
  }, [shifts, filteredStores])

  // THE ONE DERIVATION of both axes. Everything on this page that needs "hours by store", "hours by
  // employee", a row's shifts, a day column total or the grand total reads it from here.
  const axes = useMemo(() => scheduledAxes(scopedShifts, attrRoster), [scopedShifts, attrRoster])

  // Employee-axis rows: every roster employee in scope (so a person with no hours is still a row you
  // can click to schedule), UNIONED with every bucket that actually carries hours — including the
  // unattributed one. The union is what makes the two totals equal: a bucket with hours can never be
  // off-screen, which is exactly how a renamed employee's 44h went missing from this view.
  const employeeRows = useMemo(() => {
    const byKey = new Map(axes.byEmployee.map(b => [b.key, b]))
    const rows: EmployeeBucket[] = []
    const used = new Set<string>()
    for (const e of filteredEmps) {
      const a = attribute(e.employee_id || e.id, e.name)
      const b = byKey.get(a.key)
      if (b && !used.has(b.key)) { rows.push(b); used.add(b.key); continue }
      if (!b && !used.has(a.key)) {
        rows.push({ key: a.key, employee_id: a.employee_id, name: e.name, hours: 0, shifts: [],
                    stored_names: [], basis: a.basis, binds: a.binds, reason: a.reason })
        used.add(a.key)
      }
    }
    for (const b of axes.byEmployee) if (!used.has(b.key) && b.hours > 0) { rows.push(b); used.add(b.key) }
    return rows
  }, [axes, filteredEmps, attribute])

  // Double-booking: same employee, same day, 2+ shifts across different stores.
  const conflicts = useMemo(() => {
    const byKey: Record<string, Shift[]> = {}
    // Keyed on the PERSON (§STOREOPS-01), not on the name copy: a double-booking was invisible
    // whenever the same person's two shifts carried two spellings of their name.
    for (const s of scopedShifts) {
      const a = attribute(s.employee_id, s.employee_name)
      ;(byKey[`${a.key}|${s.shift_date}`] ||= []).push(s)
    }
    return Object.values(byKey)
      .filter(arr => arr.length > 1 && new Set(arr.map(a => a.store_code)).size > 1)
      .map(arr => ({ emp: attribute(arr[0].employee_id, arr[0].employee_name).name,
                     date: arr[0].shift_date, stores: Array.from(new Set(arr.map(a => a.store_code))) }))
  }, [scopedShifts, attribute])

  // By-store coverage gaps: a store that IS being scheduled this week but has a day with nobody on it.
  // Restricting to stores already in rotation keeps closed/out-of-scope stores from being false positives.
  const coverageGaps = useMemo(() => {
    if (view !== 'store') return [] as { store: string; date: string }[]
    const covered = new Set(scopedShifts.map(s => `${s.store_code}|${s.shift_date}`))
    const inRotation = new Set(scopedShifts.map(s => s.store_code))
    const gaps: { store: string; date: string }[] = []
    for (const st of filteredStores) {
      if (!st.store_code || !inRotation.has(st.store_code)) continue
      for (const d of weekDates) if (!covered.has(`${st.store_code}|${d}`)) gaps.push({ store: st.store_code, date: d })
    }
    return gaps
  }, [view, scopedShifts, filteredStores, weekDates])

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
        // Dedupe on the PERSON, not the spelling (§STOREOPS-01) — keyed on `employee_name` this
        // skipped nothing for a renamed employee and copied their week in twice.
        const seen = new Set((existing || []).map((s: any) => `${dedupeWho(s)}|${s.shift_date}|${s.start_time}`))
        for (const sh of shifts) {
          const nd = addDays(sh.shift_date, wk * 7)
          if (seen.has(`${dedupeWho(sh)}|${nd}|${sh.start_time}`)) continue
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
      const seen = new Set(shifts.map(s => `${dedupeWho(s)}|${s.shift_date}|${s.start_time}`))
      const created: any[] = []; let added = 0
      for (const sh of prev) {
        const nd = addDays(sh.shift_date, 7)
        if (seen.has(`${dedupeWho(sh)}|${nd}|${sh.start_time}`)) continue
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
    // Edit the shift under the person's CURRENT name (§STOREOPS-01) — opening it on the stale copy
    // stored on the row is how somebody ends up re-entering a second schedule for one person.
    const who = attribute(s.employee_id, s.employee_name).name || s.employee_name
    setNewShift({ start_time: s.start_time, end_time: s.end_time, store_code: s.store_code, employee_name: who })
    setAddModal({ date: s.shift_date, store: s.store_code, emp: who, editId: s.id })
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
    if (isOff({ key: attribute(allEmps.find(e => e.name === employee_name)?.employee_id, employee_name).key, name: employee_name }, addModal.date)) {
      if (!confirm(`${employee_name} has approved time off on ${addModal.date}. Schedule anyway?`)) return
    }
    const emp = allEmps.find(e => e.name === employee_name)
    const payload = {
      // 2026-07-27 money fix (owner-approved): store the employee's BUSINESS employee_id — the SAME
      // identity a kiosk punch (storeops.timelog), a manual-hours adjustment, and the employees
      // roster's own pay-rate lookup (emp_map) all key on. The old `emp?.id` here was the employee's
      // NUMERIC primary key, which almost never equals the business id ("E<id>") — that mismatch is
      // what made a scheduled+punched rep show as TWO payroll rows (one at pay_rate $0) and let the
      // no-double-count rule silently miss same-day shift+punch pairs (see payroll_identity.py +
      // migration 415's backfill for the full root-cause writeup). Falls back to the numeric id only
      // for the rare employee record with no business id assigned yet (pre-existing, safe no-op).
      employee_id: emp?.employee_id || emp?.id?.toString() || '',
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
      // AUTO CLOCK-IN confirmation (mig 915): if this schedule activated a held unscheduled tap, the
      // backend clocked the rep in from their original tap time — tell the manager so.
      const act = created?.activated_clockins
      if (Array.isArray(act) && act.length > 0) {
        const a = act[0]
        setNotice(`${a.employee_name || employee_name} is now clocked in${a.clock_in ? ` from ${a.clock_in}` : ''}.`)
      } else if (created?.timeoff_warning) {
        // Backend-confirmed heads-up (org's policy is 'warn', default for every tenant) — the shift
        // already saved; this never blocks, just informs.
        setNotice(created.timeoff_warning)
      }
    } catch (e: any) {
      // Backend still 409s when the tenant opted into 'block' via the timeoff-conflict-mode
      // setting (default is 'warn', which allows the schedule + returns `timeoff_warning` above).
      alert(e?.message || 'Could not add shift.')
    } finally { setBusy(false) }
  }

  // ── export payload (current view) ── delegates to the pure buildScheduleExport (storeops/lib/
  // scheduleExport.ts) so SendReportButton renders from EXACTLY this — see the export-privacy fix
  // note below on the Send button.
  function buildPayload(): ExportPayload {
    return buildScheduleExport({
      view,
      weekStartLabel: parseLocalDate(weekStart).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }),
      weekStartIso: weekStart,
      weekDates: weekDates.map(d => ({ iso: d, ...dayLabel(d) })),
      filterMarkets,
      filteredStores: filteredStores.map(s => ({ store_code: s.store_code })),
      // The export's employee axis is the SAME union of buckets the grid shows (§STOREOPS-01), so an
      // emailed schedule cannot total differently from the screen it was sent from.
      filteredEmps: employeeRows.map(b => ({ name: b.name, key: b.key })),
      employeeBuckets: employeeRows.map(b => ({ key: b.key, name: b.name, shifts: b.shifts })),
      byStoreShifts: axes.byStoreShifts,
      shifts: scopedShifts,
    })
  }

  const today = localToday()
  const rows: (Store | EmployeeBucket)[] = view === 'store' ? filteredStores : employeeRows

  // Footer totals — read from the ONE axes derivation, over exactly the rows the grid shows
  // (§STOREOPS-01). Both views sum the SAME scoped row set, so "By store" and "By employee" cannot
  // disagree: that disagreement (208h vs 164h, live, 2026-10-12) is the defect this replaced. The
  // employee view sums its displayed buckets and the store view sums its displayed stores; since
  // `employeeRows` is a union that can never leave a bucket with hours off-screen, the two agree by
  // construction rather than by luck.
  const footer = useMemo(() => {
    const perDay: Record<string, number> = {}
    let grand = 0
    const add = (list: Shift[]) => {
      for (const s of list) {
        const h = shiftHrs(s)
        perDay[s.shift_date] = (perDay[s.shift_date] || 0) + h
        grand += h
      }
    }
    if (view === 'store') for (const st of filteredStores) add((axes.byStoreShifts[st.store_code] || []) as Shift[])
    else for (const b of employeeRows) add(b.shifts as Shift[])
    return { perDay, grand: Math.round(grand * 100) / 100 }
  }, [axes, filteredStores, employeeRows, view])

  // The parity statement, on screen. Not a debug aid: if these two ever differ again the page SAYS
  // so with the number, instead of showing two confident totals and letting somebody reconcile them
  // by hand. `agrees` is proven for the pure derivation in harness_schedule_axes.py; this is the
  // same assertion where the user can see it.
  const parityGap = useMemo(() => {
    const storeTotal = filteredStores.reduce(
      (a, st) => a + (axes.byStoreShifts[st.store_code] || []).reduce((x, s) => x + shiftHrs(s), 0), 0)
    const empTotal = employeeRows.reduce((a, b) => a + b.hours, 0)
    return Math.round((storeTotal - empTotal) * 100) / 100
  }, [axes, filteredStores, employeeRows])

  // Render the chips of shifts inside one grid cell.
  const Cell = ({ date, cellShifts, onAdd, off }: { date: string; cellShifts: Shift[]; onAdd: () => void; off?: boolean }) => (
    <td style={{ padding: '4px 6px', textAlign: 'center', borderRight: '1px solid var(--border)', cursor: 'pointer', verticalAlign: 'top', background: off ? 'rgba(245,158,11,0.10)' : date === today ? 'rgba(37,99,235,0.04)' : undefined }}
      onClick={onAdd}>
      {off && <div style={{ fontSize: 10, color: '#b45309', fontWeight: 700, marginBottom: 2 }} title="Approved time off">🌴 OFF</div>}
      {cellShifts.map(s => (
        <div key={s.id} title="Click to edit times" onClick={e => { e.stopPropagation(); openEdit(s) }}
          style={{ background: 'var(--accent2)', borderRadius: 6, padding: '3px 6px', fontSize: 11, color: 'white', position: 'relative', marginBottom: 3, textAlign: 'left' }}>
          <div style={{ fontWeight: 600, paddingRight: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {view === 'store' ? (attribute(s.employee_id, s.employee_name).name || s.employee_name) : s.store_code}
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
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
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
            {stores.filter(s => s.store_code && (selMkt.size === 0 || selMkt.has(s.market))).map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.address?.substring(0, 26)}{s.is_active === false ? ' (inactive)' : ''}</option>)}
          </select>
          <button className="btn btn-secondary" onClick={prevWeek}>← Prev</button>
          <button className="btn btn-secondary" onClick={() => setWeekStart(workWeekStartOf(wwDow))}>Today</button>
          <button className="btn btn-secondary" onClick={nextWeek}>Next →</button>
          <button className="btn btn-secondary" disabled={busy} onClick={copyFromLastWeek} title="Pull last week's shifts into this week">⬅️ Copy last week</button>
          <button className="btn btn-primary" disabled={busy} onClick={copyWeeks} title="Duplicate this week's shifts into one or more following weeks">📋 Copy weeks</button>
          <button className="btn btn-secondary" disabled={busy} onClick={saveTemplate} title="Save this week as the recurring weekly template">⭐ Save template</button>
          <button className="btn btn-secondary" disabled={busy} onClick={applyTemplate} title="Fill this week from the saved templates">📌 Apply template</button>
          <button className="btn btn-primary" onClick={() => { setReportOpen(true); setDrillKey(null) }}
            title="Scheduled hours week-over-week & month-over-month, with per-store / per-employee drill-down">
            📈 Hours Trend
          </button>
          <ExportButtons payload={buildPayload} compact />
          {/* 2026-08-04 export-privacy fix: was reportKey="storeops_schedule" filters={week_start, store_code} —
              the backend re-query dropped the on-screen MARKET filter (only forwarded store_code), so
              filtering by market alone and hitting Send emailed/WhatsApp'd every store's schedule org-wide.
              Now renders from the same buildPayload() the local Excel/PDF/Print buttons use — WYSIWYG,
              honors market + store + view exactly as shown on screen. See docs/handoffs/people.md. */}
          <SendReportButton exportPayload={buildPayload} compact />
        </div>
      </div>

      {notice && (
        <div style={{ background: '#eff6ff', border: '1px solid #3b82f6', color: '#1e3a8a', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
          <span>🌴 {notice}</span>
          <button onClick={() => setNotice(null)} style={{ background: 'none', border: 'none', color: '#1e3a8a', cursor: 'pointer', fontWeight: 700 }}>✕</button>
        </div>
      )}

      {/* THE TWO TOTALS AGREE, OR THE PAGE SAYS SO (§STOREOPS-01). Both views now sum the same
          scoped rows through one derivation, so this can only fire if somebody re-introduces a
          second derivation — in which case the user sees the gap instead of reconciling two
          confident numbers by hand. */}
      {Math.abs(parityGap) >= 0.01 && (
        <div style={{ background: '#fee2e2', border: '1px solid #ef4444', color: '#991b1b', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13 }}>
          ⚠️ The by-store and by-employee totals disagree by <strong>{Math.abs(parityGap).toFixed(1)}h</strong> for
          this filter. That is a defect, not a setting — please report it; nothing on this screen should
          be able to produce it.
        </div>
      )}

      {/* HOURS THAT NAME NOBODY ARE AN ANSWER, NOT A GAP. They stay in both totals and are named
          here, so they can be assigned rather than quietly dropped off the employee axis. */}
      {axes.unattributed.shifts.length > 0 && (
        <div style={{ background: '#fef3c7', border: '1px solid #f59e0b', color: '#92400e', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13 }}>
          🧾 <strong>{axes.unattributed.hours.toFixed(1)}h</strong> across {axes.unattributed.shifts.length} shift
          {axes.unattributed.shifts.length === 1 ? '' : 's'} is not matched to an employee record. It is counted in
          both totals and listed at the bottom of the By-employee view.
        </div>
      )}

      {/* A SPELLING THAT NO LONGER MATCHES THE ROSTER IS A DATA FINDING — reported, never rewritten
          behind somebody's back. The hours are already correct; which spelling is right is a human
          decision (see the §STOREOPS-01 runbook). */}
      {axes.nameDivergence.length > 0 && (
        <div style={{ background: '#eff6ff', border: '1px solid #3b82f6', color: '#1e3a8a', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 13 }}>
          ℹ️ Some shifts are stored under an older spelling of an employee&apos;s name — their hours are counted
          correctly under the employee record either way:{' '}
          {axes.nameDivergence.slice(0, 4).map((d, i) => (
            <span key={i}><strong>{d.roster_name}</strong> also appears as “{d.stored_name}” ({d.rows} shift
              {d.rows === 1 ? '' : 's'}, {d.hours.toFixed(1)}h){i < Math.min(axes.nameDivergence.length, 4) - 1 ? '; ' : ''}</span>
          ))}
          {axes.nameDivergence.length > 4 && ` +${axes.nameDivergence.length - 4} more`}
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
        // Freeze panes: the grid scrolls inside its OWN bounded box (not the page), so the day
        // header row stays pinned on vertical scroll (position:sticky top) and the Store/Employee
        // column stays pinned on horizontal scroll (position:sticky left). Sticky cells need their
        // OWN opaque background or the scrolled content shows through; today's tint is composited
        // over an opaque --surface2 so it stays theme-aware AND opaque.
        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 200px)', position: 'relative' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                <th style={{ padding: '10px 14px', color: 'var(--text)', fontSize: 12, fontWeight: 700, textAlign: 'left', width: 170, position: 'sticky', top: 0, left: 0, zIndex: 3, background: 'var(--surface2)', boxShadow: 'inset 0 -2px 0 var(--border)' }}>
                  {view === 'store' ? 'Store' : 'Employee'}
                </th>
                {weekDates.map(date => {
                  const { dow, md } = dayLabel(date)
                  const isToday = date === today
                  return (
                    <th key={date} style={{ padding: '8px', color: 'var(--text)', fontSize: 12, fontWeight: 700, textAlign: 'center', position: 'sticky', top: 0, zIndex: 2, background: isToday ? 'linear-gradient(rgba(37,99,235,0.12), rgba(37,99,235,0.12)), var(--surface2)' : 'var(--surface2)', boxShadow: isToday ? 'inset 0 -2px 0 var(--accent)' : 'inset 0 -2px 0 var(--border)' }}>
                      <div>{dow}</div>
                      <div style={{ fontWeight: 500, color: 'var(--text2)' }}>{md}</div>
                    </th>
                  )
                })}
                <th style={{ padding: '10px 14px', color: 'var(--text)', fontSize: 12, fontWeight: 700, textAlign: 'right', position: 'sticky', top: 0, zIndex: 2, background: 'var(--surface2)', boxShadow: 'inset 0 -2px 0 var(--border)' }}>Total Hrs</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri: number) => {
                const r = row as Store & EmployeeBucket & { role?: string; address?: string }
                const isStore = view === 'store'
                const label = isStore ? r.store_code : r.name
                // The sub-line on the employee axis NAMES a divergence instead of hiding it: when
                // this person's shifts carry a spelling other than their roster name, say so, with
                // the spelling, so the data finding is visible to the person who can settle it.
                const otherNames: string[] = isStore ? []
                  : (r.stored_names || []).filter((n: string) => n && n !== r.name)
                const sub = isStore ? (r.market || r.address?.substring(0, 22))
                  : (r.key === UNATTRIBUTED_KEY ? r.reason
                     : otherNames.length ? `also scheduled as ${otherNames.join(', ')}` : r.role)
                // Row shifts come from the ONE axes derivation — never a re-filter by name.
                const rowShifts = (isStore
                  ? (axes.byStoreShifts[r.store_code] || [])
                  : (r.shifts || [])) as Shift[]
                const total = rowShifts.reduce((a, s) => a + shiftHrs(s), 0)
                const rowBg = ri % 2 === 1 ? 'var(--surface2)' : 'white'
                return (
                  <tr key={(isStore ? r.store_code : r.key) ?? label} style={{ background: rowBg }}>
                    <td style={{ padding: '8px 14px', fontWeight: 600, fontSize: 13, borderRight: '1px solid var(--border)', position: 'sticky', left: 0, zIndex: 1, background: rowBg }}>
                      <div>{label}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{sub}</div>
                    </td>
                    {weekDates.map(date => (
                      <Cell key={date} date={date}
                        cellShifts={rowShifts.filter(s => s.shift_date === date)}
                        off={!isStore && isOff(r, date)}
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
            {/* Total scheduled hours — per-day column totals + a grand total for the current filter. */}
            {rows.length > 0 && (
              <tfoot>
                <tr style={{ background: 'var(--surface2)', borderTop: '2px solid var(--border)' }}>
                  <td style={{ padding: '10px 14px', fontWeight: 700, fontSize: 12, position: 'sticky', left: 0, zIndex: 1, background: 'var(--surface2)', borderRight: '1px solid var(--border)' }}>
                    Total scheduled
                  </td>
                  {weekDates.map(date => (
                    <td key={date} style={{ padding: '10px 6px', textAlign: 'center', fontWeight: 600, fontSize: 12, color: 'var(--text2)' }}>
                      {(footer.perDay[date] || 0) > 0 ? `${(footer.perDay[date] || 0).toFixed(1)}h` : '—'}
                    </td>
                  ))}
                  <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 800, fontSize: 14, color: 'var(--accent)' }}>
                    {footer.grand.toFixed(1)}h
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}

      {/* Analysis summary — who is increasing scheduled hours, in which store (this week vs last).
          Factual + coaching-toned: a short ranked list, never punitive. Reflects the active filter. */}
      {!loading && trend && (
        <div className="card" style={{ marginTop: 18, padding: '14px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>📊 Who’s picking up hours — this week vs last</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              Total scheduled (8-week span): <strong style={{ color: 'var(--text2)' }}>{trend.total.toFixed(1)}h</strong>
            </div>
          </div>
          {trend.increasing.week.length === 0 ? (
            <div style={{ fontSize: 13, color: 'var(--text3)' }}>No employees are trending up on scheduled hours this week for the current filter.</div>
          ) : (
            <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, lineHeight: 1.7 }}>
              {trend.increasing.week.slice(0, 8).map((r, i) => (
                <li key={i}>
                  <strong>{r.employee}</strong> at <strong>{r.store}</strong> —{' '}
                  <span style={{ color: '#15803d', fontWeight: 700 }}>+{r.delta.toFixed(1)}h</span>{' '}
                  <span style={{ color: 'var(--text3)' }}>({r.prior.toFixed(1)}h → {r.current.toFixed(1)}h)</span>
                </li>
              ))}
            </ol>
          )}
          <div style={{ marginTop: 8 }}>
            <button className="btn btn-secondary" style={{ padding: '3px 10px', fontSize: 12 }}
              onClick={() => { setReportOpen(true); setDrillKey(null) }}>See the full week-over-week / month-over-month trend →</button>
          </div>
        </div>
      )}

      {/* Hours-trend report modal — WoW / MoM with per-store & per-employee drill-down. */}
      {reportOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', zIndex: 120, padding: '40px 16px', overflowY: 'auto' }}
          onClick={() => setReportOpen(false)}>
          <div className="card" style={{ width: 720, maxWidth: '100%' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 18 }}>Scheduled Hours Trend</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
                  {filterMarkets.length ? filterMarkets.join(', ') : 'All markets'}
                  {filterStore ? ` · ${filterStore}` : ''} · anchored on week of {parseLocalDate(weekStart).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </div>
              </div>
              <button onClick={() => setReportOpen(false)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: 'var(--text3)' }}>✕</button>
            </div>
            <div className="seg" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', margin: '10px 0 14px' }}>
              <button className="btn" style={{ borderRadius: 0, border: 'none', background: reportGran === 'week' ? 'var(--accent)' : 'transparent', color: reportGran === 'week' ? 'white' : 'var(--text2)' }} onClick={() => { setReportGran('week'); setDrillKey(null) }}>Week over week</button>
              <button className="btn" style={{ borderRadius: 0, border: 'none', background: reportGran === 'month' ? 'var(--accent)' : 'transparent', color: reportGran === 'month' ? 'white' : 'var(--text2)' }} onClick={() => { setReportGran('month'); setDrillKey(null) }}>Month over month</button>
            </div>
            {trendErr && <div style={{ color: '#b91c1c', fontSize: 13, marginBottom: 10 }}>{trendErr}</div>}
            {!trend ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><div className="spinner" /></div>
            ) : (
              <>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>Click a period to see the per-store &amp; per-employee breakdown and what drove the change.</div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text2)' }}>
                      <th style={{ textAlign: 'left', padding: '6px 8px' }}>Period</th>
                      <th style={{ textAlign: 'right', padding: '6px 8px' }}>Scheduled hrs</th>
                      <th style={{ textAlign: 'right', padding: '6px 8px' }}>vs prior</th>
                      <th style={{ width: 20 }}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {(reportGran === 'week' ? trend.weeks : trend.months).map(b => {
                      const open = drillKey === b.key
                      const deltaColor = b.delta == null ? 'var(--text3)' : b.delta > 0 ? '#15803d' : b.delta < 0 ? '#b91c1c' : 'var(--text3)'
                      const sd = Object.entries(b.store_deltas).sort((a, c) => Math.abs(c[1]) - Math.abs(a[1]))
                      const ed = Object.entries(b.employee_deltas).sort((a, c) => Math.abs(c[1]) - Math.abs(a[1]))
                      return (
                        <Fragment key={b.key}>
                          <tr onClick={() => setDrillKey(open ? null : b.key)}
                            style={{ cursor: 'pointer', borderBottom: '1px solid var(--border)', background: open ? 'var(--surface2)' : undefined }}>
                            <td style={{ padding: '7px 8px', fontWeight: 600 }}>{b.label}</td>
                            <td style={{ padding: '7px 8px', textAlign: 'right', fontWeight: 600 }}>{b.total.toFixed(1)}h</td>
                            <td style={{ padding: '7px 8px', textAlign: 'right', color: deltaColor, fontWeight: 600 }}>
                              {b.delta == null ? '—' : `${b.delta > 0 ? '+' : ''}${b.delta.toFixed(1)}h`}
                            </td>
                            <td style={{ textAlign: 'center', color: 'var(--text3)' }}>{open ? '▾' : '▸'}</td>
                          </tr>
                          {open && (
                            <tr>
                              <td colSpan={4} style={{ padding: '4px 8px 12px', background: 'var(--surface2)' }}>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                  <div>
                                    <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 4, color: 'var(--text2)' }}>By store {sd.length > 0 && '(Δ vs prior)'}</div>
                                    {Object.keys(b.by_store).length === 0 ? <div style={{ color: 'var(--text3)', fontSize: 12 }}>No shifts.</div> :
                                      Object.entries(b.by_store).sort((a, c) => c[1] - a[1]).map(([k, v]) => (
                                        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                                          <span>{k}</span>
                                          <span>{v.toFixed(1)}h {b.store_deltas[k] != null && <em style={{ color: b.store_deltas[k] > 0 ? '#15803d' : '#b91c1c', fontStyle: 'normal' }}>({b.store_deltas[k] > 0 ? '+' : ''}{b.store_deltas[k].toFixed(1)})</em>}</span>
                                        </div>
                                      ))}
                                  </div>
                                  <div>
                                    <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 4, color: 'var(--text2)' }}>By employee {ed.length > 0 && '(Δ vs prior)'}</div>
                                    {Object.keys(b.by_employee).length === 0 ? <div style={{ color: 'var(--text3)', fontSize: 12 }}>No shifts.</div> :
                                      Object.entries(b.by_employee).sort((a, c) => c[1] - a[1]).map(([k, v]) => (
                                        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                                          <span>{k}</span>
                                          <span>{v.toFixed(1)}h {b.employee_deltas[k] != null && <em style={{ color: b.employee_deltas[k] > 0 ? '#15803d' : '#b91c1c', fontStyle: 'normal' }}>({b.employee_deltas[k] > 0 ? '+' : ''}{b.employee_deltas[k].toFixed(1)})</em>}</span>
                                        </div>
                                      ))}
                                  </div>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </>
            )}
          </div>
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
