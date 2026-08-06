'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'

// Time Clock admin (Part B / B1): review punches (clock-in/out, hours, selfie, GPS, face-match) and
// add manual-hours adjustments. The employee-facing clock-in lives in the mobile /portal.
//
// 2026-07-27 owner fixes (see docs/handoffs/people.md for the full writeup):
//  1) DATE-FILTER BUG — filtering a range ALSO showed today's punches, and the filtered view would
//     flicker to an unfiltered one. ROOT CAUSE: the old page kept THREE independent trigger states
//     (start/end/employee filter) each re-firing its own fetch via a shared useEffect, with NO guard
//     against out-of-order network responses — editing the "From" date alone fired a fetch with the
//     STILL-STALE "To" (often "today"), and if that wide, stale response resolved AFTER a
//     subsequently-fixed narrower one, it silently overwrote the correct filtered view with one that
//     included today (classic last-response-wins). Grepped the whole frontend for any scroll listener/
//     IntersectionObserver/infinite-scroll pattern that could independently explain "scrolling
//     shows/hides it" — NONE exists anywhere in this codebase; the flicker IS the race, observed while
//     scrolling the table, not caused by it. FIX: (a) the ONLY thing that re-fetches now is the DATE
//     RANGE (store/market/rep are client-side filters over the already-loaded, already-org-scoped
//     rows — matching every sibling report page's established pattern, eliminating an entire class of
//     unnecessary/racy fetches), and (b) a monotonic request-id guard (`reqIdRef`) discards any
//     response that isn't from the MOST RECENTLY issued request before it's ever applied to state —
//     see `load()` below. Date range stays INCLUSIVE both ends (already correct on the backend:
//     `.gte(start).lte(end)` on `work_date`, itself stored per BUSINESS_TZ at punch time — verified,
//     not changed). No `new Date("YYYY-MM-DD")` UTC-parse anywhere on this page (native <input
//     type="date"> already hands back a raw "YYYY-MM-DD" string; the only `new Date(...)` call here is
//     `fmtTime` on a FULL timestamp, not a bare date — the classic off-by-one does not apply to it).
//  2) MANUAL-ADJUSTMENT LINKAGE (Deliverable 2) — a punch/manual-hours row touched by a manager
//     override, a force clock-out, or a manual-hours add/delete (storeops.payroll_change_log, mig 414)
//     gets a ✎ marker with a who/when/before→after tooltip + a deep link into the Payroll Change Log,
//     pre-filtered to that employee/day. Best-effort: absent entirely pre-migration-414 (never a 500).
//  3) LUNCH-BREAK AUTO-DEDUCTION (Deliverable 3, money-adjacent) — see lunch_deduction.py. Shown as an
//     explicit "− 0:30 lunch (auto)" line, never folded silently into a punch's own `hours` value.
//     RULE FIVE (owner addition): the report now carries the full standardized filter bar
//     (period/store(s)/market/rep(s)), store→market resolved via storeops.stores.market — the SAME
//     path every sibling StoreOps report page already uses (mirrored into commcalc.store_mapping at
//     store-creation time; see router.py `_sync_store_mapping`) — never a fresh join.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
// Format punch times in the BUSINESS timezone so the report matches the kiosk (both ET) regardless of
// the viewer's browser timezone. (Keep in sync with backend _BIZ_TZ / settings.BUSINESS_TZ.)
const BUSINESS_TZ = 'America/New_York'
const fmtTime = (t: string | null) => { if (!t) return '—'; try { return new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: BUSINESS_TZ }) } catch { return t } }
const NO_MARKET = '(no market)'

const ENTRY_POINT_LABEL: Record<string, string> = {
  shift_edit: 'Shift edit (Schedule)', shift_swap: 'Shift swap approval',
  timeclock_override: 'Manager clock-in override', manual_hours_add: 'Manual hours added',
  manual_hours_delete: 'Manual hours removed', force_clockout_manual: 'Force clock-out (DM "run now")',
  force_clockout_cron: 'Force clock-out (automatic sweep)', clock_out_stale_auto: 'Auto clock-out (stale punch)',
  lunch_deduction_config: 'Lunch-deduction setting changed',
}
function describeEdits(items: any[]): string {
  return items.map(it => {
    const who = it.changed_by_email || 'system'
    const when = (it.created_at || '').replace('T', ' ').slice(0, 16)
    const what = ENTRY_POINT_LABEL[it.entry_point] || it.entry_point
    const ba = (it.before_value != null || it.after_value != null) ? ` (${it.before_value ?? '—'} → ${it.after_value ?? '—'})` : ''
    return `${what} by ${who} on ${when}${ba}`
  }).join('\n')
}

export default function TimeClockAdminPage() {
  const router = useRouter()
  const today = new Date()
  const weekAgo = new Date(); weekAgo.setDate(today.getDate() - 6)
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({ ...emptyStandardFilter(iso(weekAgo)), periodTo: iso(today) }))
  const [rows, setRows] = useState<any[]>([])
  const [employees, setEmployees] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [manual, setManual] = useState<any[]>([])
  const [changeLog, setChangeLog] = useState<any[]>([])
  const [changeLogAvailable, setChangeLogAvailable] = useState(true)
  const [lunchCfg, setLunchCfg] = useState<any>(null)
  const [showLunchSettings, setShowLunchSettings] = useState(false)
  const [mh, setMh] = useState({ employee_id: '', work_date: iso(today), hours: '', reason: '' })
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api('/api/v1/storeops/employees?include_inactive=true').then((e: any) => setEmployees(e || [])).catch(() => {})
    api('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    api('/api/v1/storeops/timeclock/lunch-config').then(setLunchCfg).catch(() => setLunchCfg(null))
  }, [])

  // Deep-link from the Payroll Change Log (Deliverable 2, reverse direction): ?employee_id=&start=&end=
  // pre-filters this page. Read from window.location (no useSearchParams -> no Suspense boundary
  // needed, same convention already used by helpdesk/new/page.tsx's "Contact support" deep-link).
  useEffect(() => {
    try {
      const sp = new URLSearchParams(window.location.search)
      const eid = sp.get('employee_id'); const s = sp.get('start'); const e = sp.get('end')
      if (s || e) setFilt(f => ({ ...f, period: s || f.period, periodTo: e || f.periodTo }))
      if (eid) {
        const emp = employees.find(x => x.employee_id === eid)
        if (emp?.name) setFilt(f => ({ ...f, reps: [emp.name] }))
      }
    } catch { /* ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees.length])

  // ── RACE / STALE-RESPONSE FIX (Deliverable 1) ─────────────────────────────────────────────────
  // A monotonic request id: only the response from the MOST RECENTLY issued fetch is ever applied to
  // state. Any earlier in-flight request that resolves LATER (the exact "shows all through today,
  // then flips back" symptom) is silently discarded here instead of clobbering the current view.
  const reqIdRef = useRef(0)
  const load = useCallback(() => {
    const start = filt.period, end = filt.periodTo
    if (!start || !end) return
    const myReqId = ++reqIdRef.current
    setLoading(true)
    const qs = `start=${start}&end=${end}`
    api(`/api/v1/storeops/timeclock/list?${qs}`)
      .then((r: any) => { if (reqIdRef.current !== myReqId) return; setRows(r || []) })
      .catch((e: any) => { if (reqIdRef.current !== myReqId) return; setMsg('Load failed: ' + (e?.message || e)) })
      .finally(() => { if (reqIdRef.current === myReqId) setLoading(false) })
    api(`/api/v1/storeops/manual-hours?${qs}`)
      .then((r: any) => { if (reqIdRef.current !== myReqId) return; setManual(r || []) })
      .catch(() => {})
    // Deliverable 2 linkage — degrades to an empty, "not available" set pre-migration-414 (never a 500).
    api(`/api/v1/storeops/payroll-change-log?${qs}`)
      .then((r: any) => { if (reqIdRef.current !== myReqId) return; setChangeLog(r?.items || []); setChangeLogAvailable(r?.available !== false) })
      .catch(() => { if (reqIdRef.current !== myReqId) return; setChangeLog([]); setChangeLogAvailable(false) })
  }, [filt.period, filt.periodTo])
  useEffect(() => { load() }, [load])

  // StandardFilterBar's generic "Clear filters" also blanks period/periodTo in range mode — fine for
  // most reports, but this one always needs SOME range to fetch data. Re-pin the range to whatever's
  // currently active instead of letting it go blank (store/market/rep filters still clear normally) —
  // same guard as reports/page.tsx / payroll/page.tsx / payroll-change-log/page.tsx.
  function onFilterChange(v: StandardFilterValue) {
    setFilt(v.period || v.periodTo ? v : { ...v, period: filt.period, periodTo: filt.periodTo })
  }

  async function addManual() {
    if (!mh.employee_id || !mh.hours || !mh.reason.trim()) { setMsg('Employee, hours and reason are required.'); return }
    try { await api('/api/v1/storeops/manual-hours', { method: 'POST', body: JSON.stringify({ ...mh, hours: Number(mh.hours) }) }); setMh({ employee_id: '', work_date: iso(today), hours: '', reason: '' }); setMsg('✅ Adjustment added.'); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function delManual(id: string) {
    try { await api(`/api/v1/storeops/manual-hours/${id}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const empName = (id: string) => employees.find(e => e.employee_id === id)?.name || id

  // RULE FIVE (owner addition 2026-07-27): store→market via storeops.stores.market — the SAME
  // resolution path every sibling StoreOps report page uses (reports/page.tsx, payroll/page.tsx,
  // payroll-change-log/page.tsx), never a fresh commcalc.store_mapping join. A store with no market
  // set falls into an explicit "(no market)" bucket instead of being unfilterable/invisible.
  const mktOf = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of stores) if (s.store_code) m[s.store_code] = (s.market && String(s.market).trim()) ? s.market : NO_MARKET
    return m
  }, [stores])
  const storeOptions = useMemo(() => stores
    .filter(s => s.store_code)
    .map(s => ({ id: s.store_code, label: s.store_code, sublabel: s.address || s.market || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [stores])
  const marketOptions = useMemo(() => {
    const ms = new Set<string>()
    for (const s of stores) ms.add((s.market && String(s.market).trim()) ? s.market : NO_MARKET)
    return Array.from(ms).sort()
  }, [stores])
  const repOptions = useMemo(() => employees
    .filter(e => e.name)
    .map(e => ({ id: e.name, label: e.name, sublabel: e.email || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [employees])

  // Deliverable 2 — linkage: a punch/manual-hours row's OWN change-log entries (source_table + id),
  // never a loose employee+date match, so the marker only lights up on the EXACT row that was touched.
  const punchEdits = useMemo(() => {
    const m = new Map<string, any[]>()
    for (const it of changeLog) if (it.source_table === 'timelog' && it.source_id) {
      const k = String(it.source_id); m.set(k, [...(m.get(k) || []), it])
    }
    return m
  }, [changeLog])
  const manualEdits = useMemo(() => {
    const m = new Map<string, any[]>()
    for (const it of changeLog) if (it.source_table === 'manual_hours' && it.source_id) {
      const k = String(it.source_id); m.set(k, [...(m.get(k) || []), it])
    }
    return m
  }, [changeLog])

  function gotoChangeLog(employeeId: string, workDate: string) {
    router.push(`/storeops/payroll-change-log?employee_id=${encodeURIComponent(employeeId)}&start=${workDate}&end=${workDate}`)
  }

  const rowsWithMarket = useMemo(() => rows.map(r => ({ ...r, market: mktOf[r.store_code] || NO_MARKET })), [rows, mktOf])
  const visibleRows = useMemo(() => filterRows(rowsWithMarket, filt, {
    store: r => r.store_code, market: r => r.market, rep: r => r.employee_name || empName(r.employee_id),
  }), [rowsWithMarket, filt, employees])
  // manual_hours carries no store_code (mig 045 — those hours have no shift to attribute to), so
  // store/market accessors are deliberately OMITTED here (never filtered out by store/market, only
  // by rep) rather than fabricating a "(no market)" bucket for a table that structurally has no store.
  const visibleManual = useMemo(() => filterRows(manual, filt, {
    rep: m => empName(m.employee_id),
  }), [manual, filt, employees])

  const totalHours = visibleRows.reduce((s, r) => s + (Number(r.hours) || 0), 0)
  const totalLunch = visibleRows.reduce((s, r) => s + (Number(r.lunch_deduction_hours) || 0), 0)
  const openCount = visibleRows.filter(r => !r.clock_out).length

  // RULE FOUR (§3c): export exactly what's rendered (the FILTERED set) — no PII (selfie/GPS are
  // already shown as plain links/thumbnails on this page, not Fernet-masked fields, so the export
  // mirrors them as-is.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: r => r.employee_name || empName(r.employee_id) },
    { header: 'Date', field: 'work_date', role: 'date', type: 'date', get: r => r.work_date },
    { header: 'In', field: 'clock_in', get: r => fmtTime(r.clock_in) },
    { header: 'Out', field: 'clock_out', get: r => r.clock_out ? fmtTime(r.clock_out) : 'open' },
    { header: 'Hours', field: 'hours', type: 'number', get: r => r.hours != null ? Number(r.hours).toFixed(2) : '' },
    { header: 'Lunch (auto)', field: 'lunch_deduction_hours', type: 'number', get: r => r.lunch_deduction_hours ? `− ${Number(r.lunch_deduction_hours).toFixed(2)}` : '' },
    { header: 'Net Hours', field: 'net_hours', type: 'number', get: r => r.hours != null ? (Number(r.hours) - (Number(r.lunch_deduction_hours) || 0)).toFixed(2) : '' },
    { header: 'Store', field: 'store_code', role: 'store', get: r => r.store_code || '' },
    { header: 'Market', field: 'market', get: r => r.market === NO_MARKET ? '' : r.market },
    { header: 'Face Match', field: 'face_match_pct', get: r => r.face_match_pct != null ? `${r.face_match_pct}%` : '' },
    { header: 'Manually Adjusted', field: 'edited', get: r => punchEdits.has(String(r.id)) ? 'Yes' : '' },
    { header: 'GPS', field: 'gps', get: r => r.gps_lat != null ? `https://maps.google.com/?q=${r.gps_lat},${r.gps_lng}` : '' },
    { header: 'Selfie', field: 'selfie_url', get: r => r.selfie_url || '' },
  ]
  const manualCols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: m => empName(m.employee_id) },
    { header: 'Date', field: 'work_date', role: 'date', type: 'date', get: m => m.work_date },
    { header: 'Hours', field: 'hours', type: 'number', get: m => Number(m.hours).toFixed(2) },
    { header: 'Reason', field: 'reason', get: m => m.reason },
  ]

  return (
    <div>
      <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⏱️ Time Clock</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Review clock-in/out punches with selfie, GPS and face-match audit. Employees clock in from the mobile portal.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowLunchSettings(s => !s)}>⚙ Lunch Break Settings</button>
          {/* 2026-08-06 owner directive: who was scheduled and didn't clock in / who covered instead. */}
          <a href="/storeops/attendance" className="btn" style={{ fontSize: 13 }}>🚨 Attendance Exceptions</a>
          <a href="/storeops/payroll-change-log" className="btn" style={{ fontSize: 13 }}>📜 Payroll Change Log</a>
        </div>
      </div>

      {showLunchSettings && (
        <LunchSettingsPanel cfg={lunchCfg} onSaved={c => setLunchCfg((prev: any) => ({ ...prev, tenant: c }))} onClose={() => setShowLunchSettings(false)} />
      )}

      <StandardFilterBar
        value={filt}
        onChange={onFilterChange}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
      />

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1 }} />
        <span className="badge" style={{ fontSize: 12 }}>{visibleRows.length} punches</span>
        <span className="badge" style={{ fontSize: 12 }}>{totalHours.toFixed(2)} hrs</span>
        {totalLunch > 0 && <span className="badge" style={{ fontSize: 12 }} title="Total auto lunch deduction across the visible punches">− {totalLunch.toFixed(2)} hrs lunch (auto)</span>}
        {openCount > 0 && <span className="badge" style={{ fontSize: 12, background: '#16794a', color: '#fff' }}>{openCount} clocked in</span>}
        <ReportExportBar title="Time Clock" subtitle={`${filt.period} → ${filt.periodTo}`} filename={`timeclock-${filt.period}_${filt.periodTo}`} columns={cols} rows={visibleRows} />
        {msg && <span style={{ fontSize: 13, width: '100%' }}>{msg}</span>}
      </div>

      {!changeLogAvailable && (
        <div className="card" style={{ marginBottom: 12, padding: '8px 12px', fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)' }}>
          ℹ️ The Payroll Change Log table isn&apos;t set up yet on this tenant (migration 414) — manual-adjustment markers below are unavailable until it runs; punches load normally.
        </div>
      )}

      <div className="card" style={{ padding: 0, overflowX: 'auto', marginBottom: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Employee', 'Date', 'In', 'Out', 'Hours', 'Store', 'Market', 'Face', 'GPS', 'Selfie'].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={10} style={{ textAlign: 'center', padding: 36, color: 'var(--text3)' }}><div className="spinner" /></td></tr>
            ) : visibleRows.map(r => {
              const edits = punchEdits.get(String(r.id))
              return (
                <tr key={r.id}>
                  <td style={cell}>{r.employee_name || empName(r.employee_id)}</td>
                  <td style={cell}>{r.work_date}</td>
                  <td style={cell}>{fmtTime(r.clock_in)}</td>
                  <td style={cell}>{r.clock_out ? fmtTime(r.clock_out) : <span style={{ color: '#16794a', fontWeight: 600 }}>open</span>}</td>
                  <td style={cell}>
                    {r.hours != null ? Number(r.hours).toFixed(2) : '—'}
                    {r.lunch_deduction_hours > 0 && (
                      <div style={{ fontSize: 11, color: '#b45309' }} title="Auto lunch deduction — configurable per tenant/employee, see ⚙ Lunch Break Settings">
                        − {Number(r.lunch_deduction_hours).toFixed(2)}h lunch (auto) = {(Number(r.hours) - Number(r.lunch_deduction_hours)).toFixed(2)}h net
                      </div>
                    )}
                    {edits && edits.length > 0 && (
                      <button onClick={() => gotoChangeLog(r.employee_id, r.work_date)} title={describeEdits(edits)}
                        style={{ marginLeft: 6, border: 'none', background: 'none', cursor: 'pointer', color: 'var(--accent,#2563eb)', fontSize: 12 }}>
                        ✎ edited
                      </button>
                    )}
                  </td>
                  <td style={{ ...cell, fontSize: 12 }}>{r.store_code || '—'}</td>
                  <td style={{ ...cell, fontSize: 12 }}>{r.market === NO_MARKET ? <span style={{ color: 'var(--text3)' }}>(no market)</span> : r.market}</td>
                  <td style={cell}>{r.face_match_pct != null ? `${r.face_match_pct}%` : '—'}</td>
                  <td style={cell}>{r.gps_lat != null ? <a href={`https://maps.google.com/?q=${r.gps_lat},${r.gps_lng}`} target="_blank" rel="noreferrer">map</a> : '—'}</td>
                  <td style={cell}>{r.selfie_url ? <a href={r.selfie_url} target="_blank" rel="noreferrer"><img src={r.selfie_url} alt="selfie" style={{ width: 34, height: 34, borderRadius: 4, objectFit: 'cover' }} /></a> : '—'}</td>
                </tr>
              )
            })}
            {!loading && visibleRows.length === 0 && <tr><td colSpan={10} style={{ textAlign: 'center', padding: 36, color: 'var(--text3)' }}>No punches in range. (Run migration 045 if this errors.)</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>✍️ Manual hours adjustments</div>
          {visibleManual.length > 0 && <ReportExportBar title="Manual Hours Adjustments" subtitle={`${filt.period} → ${filt.periodTo}`} columns={manualCols} rows={visibleManual} />}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
          <select style={sel} value={mh.employee_id} onChange={e => setMh({ ...mh, employee_id: e.target.value })}>
            <option value="">Employee…</option>
            {employees.map(e => <option key={e.employee_id} value={e.employee_id}>{e.name}</option>)}
          </select>
          <input type="date" style={sel} value={mh.work_date} onChange={e => setMh({ ...mh, work_date: e.target.value })} />
          <input style={{ ...sel, width: 90 }} placeholder="hours (±)" value={mh.hours} onChange={e => setMh({ ...mh, hours: e.target.value })} />
          <input style={{ ...sel, width: 240 }} placeholder="reason (required)" value={mh.reason} onChange={e => setMh({ ...mh, reason: e.target.value })} />
          <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addManual}>+ Add</button>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {visibleManual.map(m => {
              const edits = manualEdits.get(String(m.id))
              return (
                <tr key={m.id}>
                  <td style={cell}>{empName(m.employee_id)}</td>
                  <td style={cell}>{m.work_date}</td>
                  <td style={{ ...cell, fontWeight: 600, color: Number(m.hours) < 0 ? '#dc2626' : 'inherit' }}>{Number(m.hours) > 0 ? '+' : ''}{Number(m.hours).toFixed(2)}h</td>
                  <td style={cell}>{m.reason}</td>
                  <td style={cell}>
                    {edits && edits.length > 0 && (
                      <button onClick={() => gotoChangeLog(m.employee_id, m.work_date)} title={describeEdits(edits)}
                        style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--accent,#2563eb)', fontSize: 12 }}>✎</button>
                    )}
                  </td>
                  <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 12, color: '#dc2626' }} onClick={() => delManual(m.id)}>✕</button></td>
                </tr>
              )
            })}
            {visibleManual.length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', padding: 18, color: 'var(--text3)', fontSize: 13 }}>No adjustments in range.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Lunch-break settings panel (Deliverable 3) ────────────────────────────────────────────────────
// Tenant-level default here; per-employee override lives on the HR "Employees & Pay" tab (same
// permission posture as pay_rate edits — see backend PUT /employees/{id}/lunch-config).
function LunchSettingsPanel({ cfg, onSaved, onClose }: { cfg: any; onSaved: (c: any) => void; onClose: () => void }) {
  const [enabled, setEnabled] = useState(true)
  const [minutes, setMinutes] = useState(30)
  const [minHours, setMinHours] = useState(6)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  useEffect(() => {
    if (cfg?.tenant) { setEnabled(!!cfg.tenant.enabled); setMinutes(cfg.tenant.minutes ?? 30); setMinHours(cfg.tenant.min_shift_hours ?? 6) }
  }, [cfg])
  async function save() {
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/storeops/timeclock/lunch-config', { method: 'PUT', body: JSON.stringify({ enabled, minutes: Number(minutes), min_shift_hours: Number(minHours) }) })
      onSaved(r.tenant); setMsg('✅ Saved.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>⚙ Lunch Break Settings — tenant default</div>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onClose}>✕</button>
      </div>
      {cfg && !cfg.available && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
          ℹ️ Migration 418 hasn&apos;t run on this tenant yet — the default shown below (30 min, 6h
          threshold) is the OWNER&apos;S STATED DEFAULT for reference; nothing is actually deducted from
          any hours or pay figure until the migration runs.
        </div>
      )}
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        Universal for every tenant (RULE TWO). Auto-deducts a lunch break from a day&apos;s hours ONLY
        when the day is a single continuous punch (or gapless pairs — no real gap) meeting the minimum
        shift length below. A day that already has a real gap between punch-pairs (a lunch re-clock-in,
        or a genuine split shift) is never auto-deducted on top — see the Time Clock report&apos;s
        &quot;lunch (auto)&quot; line for exactly which days qualified. Per-employee overrides (including
        fully disabling it for one person) are set on the HR → Employees &amp; Pay tab, next to pay rate.
      </p>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Enabled by default
        </label>
        <label style={{ fontSize: 13 }}>Minutes <input type="number" min={0} style={{ ...sel, width: 70 }} value={minutes} onChange={e => setMinutes(Number(e.target.value))} /></label>
        <label style={{ fontSize: 13 }} title="Owner-confirmable — flagged as the default threshold, adjustable here">
          Only if shift ≥ <input type="number" min={0} step={0.5} style={{ ...sel, width: 70 }} value={minHours} onChange={e => setMinHours(Number(e.target.value))} /> hours
        </label>
        <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy} onClick={save}>{busy ? '…' : 'Save'}</button>
        {msg && <span style={{ fontSize: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}
