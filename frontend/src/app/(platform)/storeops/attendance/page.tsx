'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'

// Attendance Exceptions (owner directive 2026-08-06, verbatim): "time clock should show who were
// scheduled and didn't clock in and also if somebody else clocked in instead of the scheduled."
//
// This page renders the GAPS between the schedule (storeops.shifts) and reality (storeops.timelog) —
// GET /storeops/timeclock/attendance-exceptions does the join+classification server-side (pure engine
// in attendance_exceptions.py; see docs/handoffs/people.md for the full correctness writeup: business
// timezone, multi-session union, don't-flag-the-future, cross-store handling, approved-time-off
// EXCUSED labeling). Only the date range triggers a fetch — store/market/rep AND the exception-type/
// hide-excused filters below are all CLIENT-SIDE over the already org+span-scoped response, the SAME
// established pattern the sibling Time Clock page uses (see that page's 2026-07-27 race-fix writeup).
//
// Dates render as opaque 'YYYY-MM-DD' strings from the backend (never re-parsed with
// `new Date("YYYY-MM-DD")`, the classic UTC-parse off-by-one); punch timestamps are full ISO instants
// formatted in BUSINESS_TZ via `fmtTime`, matching every other timeclock surface.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'top' }
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const BUSINESS_TZ = 'America/New_York'
const fmtTime = (t: string | null | undefined) => { if (!t) return '—'; try { return new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: BUSINESS_TZ }) } catch { return t } }
const NO_MARKET = '(no market)'

const TYPE_LABEL: Record<string, string> = {
  no_show: 'No Show', covered_by_other: 'Covered by Other', unscheduled: 'Unscheduled',
  late: 'Late', left_early: 'Left Early', late_and_left_early: 'Late + Left Early',
}
const TYPE_COLOR: Record<string, string> = {
  no_show: '#dc2626', covered_by_other: '#b45309', unscheduled: '#7c3aed',
  late: '#d97706', left_early: '#d97706', late_and_left_early: '#d97706',
}
function TypeBadge({ type, excused }: { type: string; excused?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      <span className="badge" style={{ fontSize: 11, background: TYPE_COLOR[type] || '#6b7280', color: '#fff' }}>
        {TYPE_LABEL[type] || type}
      </span>
      {excused && <span className="badge" style={{ fontSize: 11, background: '#16794a', color: '#fff' }} title="Approved time off covers this date">EXCUSED</span>}
    </span>
  )
}

const FILTER_TYPES = ['no_show', 'covered_by_other', 'unscheduled', 'late', 'left_early']

export default function AttendanceExceptionsPage() {
  const today = new Date()
  const weekAgo = new Date(); weekAgo.setDate(today.getDate() - 6)
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({ ...emptyStandardFilter(iso(weekAgo)), periodTo: iso(today) }))
  const [rows, setRows] = useState<any[]>([])
  const [employees, setEmployees] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [available, setAvailable] = useState(true)
  const [limitHit, setLimitHit] = useState(false)
  const [cfg, setCfg] = useState<any>(null)
  const [showSettings, setShowSettings] = useState(false)
  // RULE FIVE: module-specific filters APPENDED to the standard bar, never substituted.
  const [excTypes, setExcTypes] = useState<string[]>([])   // [] = every type
  const [hideExcused, setHideExcused] = useState(false)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api('/api/v1/storeops/employees?include_inactive=true').then((e: any) => setEmployees(e || [])).catch(() => {})
    api('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    api('/api/v1/storeops/timeclock/attendance-config').then((r: any) => { setCfg(r?.config || null); setAvailable(r?.available !== false) }).catch(() => {})
  }, [])

  const reqIdRef = useRef(0)
  const load = useCallback(() => {
    const start = filt.period, end = filt.periodTo
    if (!start || !end) return
    const myReqId = ++reqIdRef.current
    setLoading(true)
    api(`/api/v1/storeops/timeclock/attendance-exceptions?start=${start}&end=${end}`)
      .then((r: any) => { if (reqIdRef.current !== myReqId) return; setRows(r?.rows || []); setAvailable(r?.available !== false); setLimitHit(!!r?.limit_hit); if (r?.config) setCfg(r.config) })
      .catch((e: any) => { if (reqIdRef.current !== myReqId) return; setMsg('Load failed: ' + (e?.message || e)) })
      .finally(() => { if (reqIdRef.current === myReqId) setLoading(false) })
  }, [filt.period, filt.periodTo])
  useEffect(() => { load() }, [load])

  // Same guard as timeclock/payroll pages: "Clear filters" must not blank the range this page needs
  // data for — store/market/rep clear normally.
  function onFilterChange(v: StandardFilterValue) {
    setFilt(v.period || v.periodTo ? v : { ...v, period: filt.period, periodTo: filt.periodTo })
  }

  const empName = (id: string) => employees.find(e => e.employee_id === id)?.name || id

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

  const rowsWithMarket = useMemo(() => rows.map(r => ({ ...r, market: mktOf[r.store_code] || NO_MARKET })), [rows, mktOf])
  const stdFiltered = useMemo(() => filterRows(rowsWithMarket, filt, {
    store: r => r.store_code, market: r => r.market, rep: r => r.employee_name || empName(r.employee_id), date: r => r.work_date,
  }), [rowsWithMarket, filt, employees])

  function typeMatches(r: any): boolean {
    if (excTypes.length === 0) return true
    if (excTypes.includes(r.exception_type)) return true
    if (r.exception_type === 'late_and_left_early' && (excTypes.includes('late') || excTypes.includes('left_early'))) return true
    return false
  }
  const visibleRows = useMemo(() => stdFiltered.filter(r => typeMatches(r) && !(hideExcused && r.excused)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [stdFiltered, excTypes, hideExcused])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of visibleRows) c[r.exception_type] = (c[r.exception_type] || 0) + 1
    return c
  }, [visibleRows])
  const excusedCount = visibleRows.filter(r => r.excused).length

  function coverersText(r: any): string {
    if (!r.coverers || !r.coverers.length) return ''
    return r.coverers.map((c: any) =>
      `${c.employee_name || c.employee_id} (${fmtTime(c.clock_in)}–${c.open ? 'still in' : fmtTime(c.clock_out)}${c.same_store === false ? `, ${c.store_code}` : ''})`
    ).join('; ')
  }
  function actualText(r: any): string {
    if (r.exception_type === 'no_show') return '—'
    if (r.exception_type === 'covered_by_other') return `Actually worked: ${coverersText(r)}`
    if (r.exception_type === 'unscheduled') return `${fmtTime(r.clock_in)} – ${r.open ? 'still in' : fmtTime(r.clock_out)}`
    // late / left_early / late_and_left_early
    const parts = [`${fmtTime(r.actual_clock_in)} – ${r.actual_clock_out ? fmtTime(r.actual_clock_out) : 'still in'}`]
    if (r.same_store === false) parts.push(`at ${r.worked_store_code} (scheduled ${r.store_code})`)
    return parts.join(' ')
  }

  // RULE FOUR (§3c): export exactly the filtered set. Every filter (standard bar + exception-type +
  // hide-excused) flows into `visibleRows` before this is built — never a separately-fetched/unfiltered
  // payload (the storeops/schedule export-privacy bug this repo already fixed once, see handoff).
  const cols: ExportColumn[] = [
    { header: 'Type', field: 'exception_type', get: r => TYPE_LABEL[r.exception_type] || r.exception_type },
    { header: 'Excused', field: 'excused', get: r => r.excused ? (r.excused_reason || 'Yes') : '' },
    { header: 'Employee', field: 'employee', role: 'rep', get: r => r.employee_name || empName(r.employee_id) },
    { header: 'Date', field: 'work_date', role: 'date', type: 'date', get: r => r.work_date },
    { header: 'Store', field: 'store_code', role: 'store', get: r => r.store_code || '' },
    { header: 'Market', field: 'market', get: r => r.market === NO_MARKET ? '' : r.market },
    { header: 'Scheduled', field: 'scheduled', get: r => r.shift_start ? `${r.shift_start}–${r.shift_end}` : '' },
    { header: 'Actual', field: 'actual', get: r => actualText(r) },
    { header: 'Minutes Late', field: 'minutes_late', type: 'number', get: r => r.minutes_late || '' },
    { header: 'Minutes Early', field: 'minutes_early', type: 'number', get: r => r.minutes_early || '' },
  ]

  return (
    <div>
      <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🚨 Attendance Exceptions</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Who was scheduled and didn&apos;t clock in, and who covered for them instead — the gaps
            between the schedule and the punches.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowSettings(s => !s)}>⚙ Attendance Settings</button>
          <a href="/storeops/timeclock" className="btn" style={{ fontSize: 13 }}>⏱️ Time Clock (punches)</a>
        </div>
      </div>

      {!available && (
        <div className="card" style={{ marginBottom: 12, padding: '8px 12px', fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)' }}>
          ℹ️ Migration 421 hasn&apos;t run on this tenant yet — the thresholds below are the code
          defaults (10 min late / 10 min early / 30 min no-show grace / 15 min coverage overlap /
          label excused) and the report already works correctly on them; only the Save button on
          Attendance Settings needs the migration.
        </div>
      )}

      {limitHit && (
        <div className="card" style={{ marginBottom: 12, padding: '8px 12px', fontSize: 12, color: '#92400e', background: '#fef3c7' }}>
          ⚠️ This date range has a very large number of shifts/punches — the list below may be
          incomplete. Narrow the date range for a complete picture.
        </div>
      )}

      {showSettings && (
        <AttendanceSettingsPanel cfg={cfg} available={available} onSaved={c => { setCfg(c); setAvailable(true) }} onClose={() => setShowSettings(false)} />
      )}

      <StandardFilterBar
        value={filt}
        onChange={onFilterChange}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
      />

      {/* RULE FIVE — module-specific filters APPENDED after the standard bar, never substituted. */}
      <div className="card" style={{ padding: '8px 12px', marginBottom: 12, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Exception type:</span>
        {FILTER_TYPES.map(t => (
          <label key={t} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13 }}>
            <input type="checkbox" checked={excTypes.length === 0 || excTypes.includes(t)}
              onChange={e => {
                setExcTypes(prev => {
                  const base = prev.length === 0 ? FILTER_TYPES.slice() : prev.slice()
                  return e.target.checked ? Array.from(new Set([...base, t])) : base.filter(x => x !== t)
                })
              }} />
            {TYPE_LABEL[t]}
          </label>
        ))}
        {excTypes.length > 0 && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setExcTypes([])}>Show all types</button>}
        <span style={{ flex: 1 }} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13 }} title="Approved time off still shows the gap, labeled EXCUSED, unless you hide it here">
          <input type="checkbox" checked={hideExcused} onChange={e => setHideExcused(e.target.checked)} /> Hide excused (approved time off)
        </label>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <span className="badge" style={{ fontSize: 12, background: '#dc2626', color: '#fff' }}>{counts.no_show || 0} no show</span>
        <span className="badge" style={{ fontSize: 12, background: '#b45309', color: '#fff' }}>{counts.covered_by_other || 0} covered by other</span>
        <span className="badge" style={{ fontSize: 12, background: '#7c3aed', color: '#fff' }}>{counts.unscheduled || 0} unscheduled</span>
        <span className="badge" style={{ fontSize: 12, background: '#d97706', color: '#fff' }}>{(counts.late || 0) + (counts.late_and_left_early || 0)} late</span>
        <span className="badge" style={{ fontSize: 12, background: '#d97706', color: '#fff' }}>{(counts.left_early || 0) + (counts.late_and_left_early || 0)} left early</span>
        {excusedCount > 0 && <span className="badge" style={{ fontSize: 12 }}>{excusedCount} excused (approved time off)</span>}
        <span style={{ flex: 1 }} />
        <ReportExportBar title="Attendance Exceptions" subtitle={`${filt.period} → ${filt.periodTo}`} filename={`attendance-exceptions-${filt.period}_${filt.periodTo}`} columns={cols} rows={visibleRows} />
        {msg && <span style={{ fontSize: 13, width: '100%' }}>{msg}</span>}
      </div>

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1080 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Type', 'Employee', 'Date', 'Store', 'Market', 'Scheduled', 'Actual'].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} style={{ textAlign: 'center', padding: 36, color: 'var(--text3)' }}><div className="spinner" /></td></tr>
            ) : visibleRows.map((r, i) => (
              <tr key={`${r.exception_type}-${r.shift_id || r.punch_id}-${i}`}>
                <td style={cell}><TypeBadge type={r.exception_type} excused={r.excused} /></td>
                <td style={cell}>
                  {r.employee_name || empName(r.employee_id)}
                  {r.excused_reason && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.excused_reason}</div>}
                </td>
                <td style={cell}>{r.work_date}</td>
                <td style={{ ...cell, fontSize: 12 }}>{r.store_code || '—'}</td>
                <td style={{ ...cell, fontSize: 12 }}>{r.market === NO_MARKET ? <span style={{ color: 'var(--text3)' }}>(no market)</span> : r.market}</td>
                <td style={cell}>{r.shift_start ? `${r.shift_start} – ${r.shift_end}` : '—'}</td>
                <td style={cell}>
                  {actualText(r)}
                  {(r.minutes_late > 0) && <div style={{ fontSize: 11, color: '#b45309' }}>{r.minutes_late} min late</div>}
                  {(r.minutes_early > 0) && <div style={{ fontSize: 11, color: '#b45309' }}>{r.minutes_early} min early</div>}
                </td>
              </tr>
            ))}
            {!loading && visibleRows.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', padding: 36, color: 'var(--text3)' }}>No attendance exceptions in range — everyone scheduled showed up, on time, at the right store. 🎉</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Attendance Settings panel (RULE TWO admin UI) ─────────────────────────────────────────────────
function AttendanceSettingsPanel({ cfg, available, onSaved, onClose }: { cfg: any; available: boolean; onSaved: (c: any) => void; onClose: () => void }) {
  const [lateGrace, setLateGrace] = useState(10)
  const [earlyGrace, setEarlyGrace] = useState(10)
  const [noshowGrace, setNoshowGrace] = useState(30)
  const [overlap, setOverlap] = useState(15)
  const [timeoffMode, setTimeoffMode] = useState('label')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  useEffect(() => {
    if (cfg) {
      setLateGrace(cfg.late_grace_min ?? 10); setEarlyGrace(cfg.early_leave_grace_min ?? 10)
      setNoshowGrace(cfg.noshow_grace_min ?? 30); setOverlap(cfg.coverage_overlap_min ?? 15)
      setTimeoffMode(cfg.timeoff_mode || 'label')
    }
  }, [cfg])
  async function save() {
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/storeops/timeclock/attendance-config', {
        method: 'PUT',
        body: JSON.stringify({
          late_grace_min: Number(lateGrace), early_leave_grace_min: Number(earlyGrace),
          noshow_grace_min: Number(noshowGrace), coverage_overlap_min: Number(overlap),
          timeoff_mode: timeoffMode,
        }),
      })
      onSaved(r.config); setMsg('✅ Saved.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>⚙ Attendance Settings — tenant thresholds</div>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onClose}>✕</button>
      </div>
      {!available && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
          ℹ️ Migration 421 hasn&apos;t run on this tenant yet — Save will fail until it does. The
          values shown below are the code defaults, already in effect on the report.
        </div>
      )}
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        Universal for every tenant (RULE TWO). These control when a gap between the schedule and the
        punches becomes a reportable exception.
      </p>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13 }}>Late after <input type="number" min={0} style={{ ...sel, width: 70 }} value={lateGrace} onChange={e => setLateGrace(Number(e.target.value))} /> min</label>
        <label style={{ fontSize: 13 }}>Left early if out <input type="number" min={0} style={{ ...sel, width: 70 }} value={earlyGrace} onChange={e => setEarlyGrace(Number(e.target.value))} /> min before end</label>
        <label style={{ fontSize: 13 }} title="How long after a shift's start time before an un-punched shift becomes a No Show — protects against flagging a shift that just hasn't started yet">
          No-show grace <input type="number" min={0} style={{ ...sel, width: 70 }} value={noshowGrace} onChange={e => setNoshowGrace(Number(e.target.value))} /> min
        </label>
        <label style={{ fontSize: 13 }} title="How much a punch's window may fall outside the shift's own start/end and still count as covering it">
          Coverage tolerance <input type="number" min={0} style={{ ...sel, width: 70 }} value={overlap} onChange={e => setOverlap(Number(e.target.value))} /> min
        </label>
        <label style={{ fontSize: 13 }}>Approved time off:
          <select style={{ ...sel, marginLeft: 6 }} value={timeoffMode} onChange={e => setTimeoffMode(e.target.value)}>
            <option value="label">Label as EXCUSED (show, with a "Hide excused" toggle)</option>
            <option value="suppress">Suppress entirely (never show)</option>
          </select>
        </label>
        <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy} onClick={save}>{busy ? '…' : 'Save'}</button>
        {msg && <span style={{ fontSize: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}
