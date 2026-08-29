'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
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
  const [faceCfg, setFaceCfg] = useState<any>(null)
  const [showFaceSettings, setShowFaceSettings] = useState(false)
  const [retCfg, setRetCfg] = useState<any>(null)
  const [showRetSettings, setShowRetSettings] = useState(false)
  const [mh, setMh] = useState({ employee_id: '', work_date: iso(today), hours: '', reason: '' })
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiCached('/api/v1/storeops/employees?include_inactive=true', LOOKUP).then((e: any) => setEmployees(e || [])).catch(() => {})
    // include_inactive=true: this report is a HISTORICAL surface (RULE FIVE filter bar) — a store
    // closed today may still own past rows in this range, and the market lookup below must still
    // resolve it. GET /stores now defaults to active-only (2026-08-06 disabled-T-store fix).
    apiCached('/api/v1/storeops/stores?include_inactive=true', LOOKUP).then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    api('/api/v1/storeops/timeclock/lunch-config').then(setLunchCfg).catch(() => setLunchCfg(null))
    api('/api/v1/storeops/timeclock/face-config').then(setFaceCfg).catch(() => setFaceCfg(null))
    api('/api/v1/storeops/timeclock/face-retention/config').then(setRetCfg).catch(() => setRetCfg(null))
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
    .map(s => ({ id: s.store_code, label: s.store_code + (s.is_active === false ? ' (inactive)' : ''), sublabel: s.address || s.market || undefined }))
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
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Review clock-in/out punches with selfie, GPS and face-match audit. Employees clock in from the mobile portal.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowFaceSettings(s => !s)}>
            ⚙ Face Recognition{faceCfg?.tenant && faceCfg.tenant.enabled === false ? ' · OFF' : ''}
          </button>
          <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowLunchSettings(s => !s)}>⚙ Lunch Break Settings</button>
          <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowRetSettings(s => !s)}>
            🗑 Biometric Retention{retCfg?.tenant && retCfg.tenant.purge_on_disable ? ' · purge-on-disable' : ''}
          </button>
          {/* 2026-08-06 owner directive: who was scheduled and didn't clock in / who covered instead. */}
          <a href="/storeops/attendance" className="btn" style={{ fontSize: 13 }}>🚨 Attendance Exceptions</a>
          <a href="/storeops/payroll-change-log" className="btn" style={{ fontSize: 13 }}>📜 Payroll Change Log</a>
        </div>
      </div>

      {showFaceSettings && (
        <FaceSettingsPanel cfg={faceCfg} onClose={() => setShowFaceSettings(false)}
          onSaved={(t: any, stamped: number | null) => setFaceCfg((prev: any) => ({ ...prev, tenant: t, last_stamped: stamped }))} />
      )}

      {showLunchSettings && (
        <LunchSettingsPanel cfg={lunchCfg} onSaved={c => setLunchCfg((prev: any) => ({ ...prev, tenant: c }))} onClose={() => setShowLunchSettings(false)} />
      )}

      {showRetSettings && (
        <FaceRetentionPanel cfg={retCfg} onSaved={c => setRetCfg((prev: any) => ({ ...prev, tenant: c }))} onClose={() => setShowRetSettings(false)} />
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

// ── Face-recognition settings panel (owner directive 2026-08-09, migration 420) ───────────────────
// The tenant MASTER switch. Shipped OFF for every tenant; turning it back on is a one-click decision
// that lives here, not a code change. Per-employee assignment lives on the HR "Employees & Pay" tab
// (same place as the lunch override), because "assigned per employee" is a roster operation.
//
// Turning the switch ON also stamps the owner's "consent signed by all employees" across the roster
// (backend PUT /timeclock/face-config -> stamp_assumed_consent_for_all): every employee with NO
// consent record gets a dated 'signed' row sourced 'assumed_on_enable'. Anyone already recorded as
// 'declined' keeps that refusal. The panel says this out loud BEFORE you flip it, because for
// regulated biometric data the admin should know exactly what the click records.
function FaceSettingsPanel({ cfg, onSaved, onClose }: { cfg: any; onSaved: (t: any, stamped: number | null) => void; onClose: () => void }) {
  const [enabled, setEnabled] = useState(false)
  const [dflt, setDflt] = useState(true)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  useEffect(() => {
    if (cfg?.tenant) { setEnabled(!!cfg.tenant.enabled); setDflt(cfg.tenant.default_for_employees !== false) }
  }, [cfg])
  const turningOn = enabled && cfg?.tenant?.enabled === false
  async function save() {
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/storeops/timeclock/face-config', { method: 'PUT', body: JSON.stringify({ enabled, default_for_employees: dflt }) })
      onSaved(r.tenant, r.consent_stamped ?? null)
      setMsg(r.consent_stamped != null && r.consent_stamped > 0
        ? `✅ Saved. Consent recorded for ${r.consent_stamped} employee(s) with no prior record.`
        : r.consent_stamped === null && turningOn ? '✅ Saved — but the consent stamp did not run; re-open this panel to check.'
          : '✅ Saved.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  const s = cfg?.summary || {}
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>⚙ Face Recognition — tenant master switch</div>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onClose}>✕</button>
      </div>
      {cfg && !cfg.available && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
          ℹ️ Migration 420 hasn&apos;t run on this tenant yet. Face recognition is OFF regardless (the
          kiosk fails closed), but the switch below can&apos;t be saved until it runs.
        </div>
      )}
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        Off: the kiosk never loads the face models and clock-in just takes a photo for the record —
        punches, GPS and the selfie audit all keep working exactly as they do now. On: employees verify
        by face at clock-in again. Face geometry is regulated biometric data (Illinois BIPA covers the
        Chicago-area stores), so this is a deliberate, dated decision rather than a default.
        Already-enrolled templates ({cfg?.enrolled_templates ?? '—'}) are kept while it&apos;s off, so
        turning it back on needs no re-enrollment.
      </p>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600 }}>
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Face recognition enabled
        </label>
        <label style={{ fontSize: 13, opacity: enabled ? 1 : 0.5 }} title="What an employee with no explicit assignment inherits">
          When on, apply to{' '}
          <select value={dflt ? 'all' : 'assigned'} disabled={!enabled} style={sel} onChange={e => setDflt(e.target.value === 'all')}>
            <option value="all">every employee by default</option>
            <option value="assigned">only employees I assign</option>
          </select>
        </label>
        <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy || (cfg && !cfg.available)} onClick={save}>{busy ? '…' : 'Save'}</button>
        {msg && <span style={{ fontSize: 12 }}>{msg}</span>}
      </div>
      {turningOn && (
        <div style={{ fontSize: 12, color: '#92400e', background: '#fff7e6', border: '1px solid #f5a623', borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
          ⚠️ Saving this ON records face-recognition consent as <b>signed</b> for every employee who has
          no consent record yet ({s.unrecorded ?? '—'} people), dated now and marked
          &quot;assumed_on_enable&quot;. Employees already recorded as declined are left alone. Per the
          owner directive of 2026-08-09 — but if you need a real signed release on file per person,
          record it individually on HR → Employees &amp; Pay instead.
        </div>
      )}
      <div style={{ fontSize: 12, color: 'var(--text2)' }}>
        Consent on file: <b>{s.signed ?? 0}</b> signed · <b>{s.declined ?? 0}</b> declined · <b>{s.unrecorded ?? 0}</b> not recorded.
        {' '}Assignment: <b>{s.assigned_on ?? 0}</b> on · <b>{s.assigned_off ?? 0}</b> off · <b>{s.unassigned ?? 0}</b> following the tenant default.
        {' '}Set these per person on the HR → Employees &amp; Pay tab.
      </div>
    </div>
  )
}

// ── Biometric retention panel (owner decision 2026-08-09, migration 422) ───────────────────────────
// Closes security-plan Phase 9.2. "Whichever is first" of: N days (this tenant's config, default 90,
// hard-ceilinged at 1095) after termination_date, or the 1095-day statutory backstop since the
// employee's last interaction with their own descriptor — see face_retention.py for the full rule.
// The Preview/Destroy split mirrors this codebase's established dry-run-before-apply convention (HR's
// onboarding "Reconcile mandatory docs" dry run) — nothing is ever destroyed without a preview first.
function FaceRetentionPanel({ cfg, onSaved, onClose }: { cfg: any; onSaved: (t: any) => void; onClose: () => void }) {
  const [days, setDays] = useState(90)
  const [purgeOnDisable, setPurgeOnDisable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [log, setLog] = useState<any[] | null>(null)
  useEffect(() => {
    if (cfg?.tenant) { setDays(cfg.tenant.retention_days ?? 90); setPurgeOnDisable(!!cfg.tenant.purge_on_disable) }
  }, [cfg])
  useEffect(() => {
    api('/api/v1/storeops/timeclock/face-retention/log?limit=20').then((r: any) => setLog(r?.rows || [])).catch(() => setLog([]))
  }, [])
  async function save() {
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/storeops/timeclock/face-retention/config', {
        method: 'PUT', body: JSON.stringify({ retention_days: Number(days), purge_on_disable: purgeOnDisable }),
      })
      onSaved(r.tenant)
      setMsg(r.purge_result
        ? `✅ Saved. Purge-on-disable is now active and destroyed ${r.purge_result.destroyed} template(s) immediately.`
        : '✅ Saved.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  async function runPreview() {
    setBusy(true); setMsg(''); setPreview(null)
    try {
      const r = await api('/api/v1/storeops/timeclock/face-retention/run', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
      setPreview(r)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  async function destroyNow() {
    if (!preview || preview.candidates === 0) return
    if (!window.confirm(`Permanently destroy ${preview.candidates} face descriptor(s)? This cannot be undone.`)) return
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/storeops/timeclock/face-retention/run', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
      setMsg(`✅ Destroyed ${r.destroyed} template(s).`)
      setPreview(null)
      api('/api/v1/storeops/timeclock/face-retention/log?limit=20').then((rr: any) => setLog(rr?.rows || [])).catch(() => {})
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>🗑 Biometric Data Retention — face descriptors</div>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onClose}>✕</button>
      </div>
      {cfg && !cfg.available && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
          ℹ️ Migration 422 hasn&apos;t run on this tenant yet. Nothing is destroyed regardless (the job
          fails closed), but settings below can&apos;t be saved until it runs.
        </div>
      )}
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        Face descriptors are destroyed automatically, per BIPA&apos;s &quot;whichever is first&quot; rule:
        <b> {days} days</b> after an employee&apos;s last day of employment, or an absolute <b>3-year (1095
        day)</b> statutory backstop since their last interaction with their own template — whichever
        comes first. The 3-year bound is fixed by law and cannot be configured past. See{' '}
        <code>docs/BIOMETRIC_RETENTION_POLICY.md</code> for the written policy.
      </p>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <label style={{ fontSize: 13 }}>
          Destroy <input type="number" min={1} max={1095} style={{ ...sel, width: 70 }} value={days}
            onChange={e => setDays(Number(e.target.value))} /> days after last day of employment
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }} title="When face recognition is OFF for this tenant, also destroy every already-enrolled template instead of keeping them for an instant re-enable">
          <input type="checkbox" checked={purgeOnDisable} onChange={e => setPurgeOnDisable(e.target.checked)} />
          Also purge everything while face recognition is OFF for this tenant
        </label>
        <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy || (cfg && !cfg.available)} onClick={save}>{busy ? '…' : 'Save'}</button>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
        <button className="btn" style={{ fontSize: 13 }} disabled={busy || (cfg && !cfg.available)} onClick={runPreview}>Preview what&apos;s due now</button>
        {preview && (
          <>
            <span style={{ fontSize: 13 }}>
              {preview.candidates === 0 ? 'Nothing is due for destruction right now.' : `${preview.candidates} template(s) are due for destruction.`}
              {preview.purge_all && ' (tenant-wide purge-on-disable is active)'}
            </span>
            {preview.candidates > 0 && (
              <button className="btn" style={{ fontSize: 13, color: '#b91c1c', borderColor: '#b91c1c' }} disabled={busy} onClick={destroyNow}>Destroy now</button>
            )}
          </>
        )}
        {msg && <span style={{ fontSize: 12 }}>{msg}</span>}
      </div>
      {preview && preview.items?.length > 0 && (
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', marginBottom: 10 }}>
          <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}><th>Employee</th><th>Trigger</th><th>Due date</th></tr></thead>
          <tbody>
            {preview.items.map((it: any, i: number) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '3px 6px 3px 0' }}>{it.employee_name || it.employee_id}</td>
                <td style={{ padding: '3px 6px' }}>{it.trigger === 'purpose_satisfied' ? 'Termination + retention window' : it.trigger === 'statutory_backstop' ? '3-year statutory backstop' : it.trigger}</td>
                <td style={{ padding: '3px 6px' }}>{it.due_date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div style={{ fontSize: 12, color: 'var(--text2)', fontWeight: 600, marginTop: 6 }}>Recent destruction log (evidence)</div>
      {log && log.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>No descriptors have been destroyed yet on this tenant.</div>}
      {log && log.length > 0 && (
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}><th>When</th><th>Employee</th><th>Trigger</th><th>By</th></tr></thead>
          <tbody>
            {log.map((r: any) => (
              <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '3px 6px 3px 0' }}>{(r.destroyed_at || '').replace('T', ' ').slice(0, 16)}</td>
                <td style={{ padding: '3px 6px' }}>{r.employee_name || r.employee_id}</td>
                <td style={{ padding: '3px 6px' }}>{r.trigger}</td>
                <td style={{ padding: '3px 6px' }}>{r.destroyed_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
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
