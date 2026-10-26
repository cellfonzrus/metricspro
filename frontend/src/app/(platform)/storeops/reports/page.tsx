'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt } from '@/lib/client'
import { apiCached, LOOKUP, CONFIG } from '@/lib/cache'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { currentPeriodFromSettingsResponse, monthRange, rangeLabel, stepPeriod, type PayPeriodSettings } from '../lib/pay-period'
import { PAY_BASIS_LABEL, type PayBasis } from '../lib/pay-basis'
import ActualHoursDrilldown from '../payroll/ActualHoursDrilldown'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import { narrowPanel, screenKey, agreesToTheCent } from '@/lib/drill-narrow'

// The hours basis words, shown as the human phrase — the SAME display map the Payroll Report uses,
// over the SAME server-composed basis. The decision and the sentence are the server's (§14y).
const BASIS_LABEL: Record<string, string> = {
  clocked: 'Clocked in',
  clocked_corrected: 'Corrected by hand',
  clocked_reconciled: 'Clock records reconciled',
  clocked_override: 'Overridden, with a reason',
  measured_zero: 'Measured — nothing worked',
  schedule_opt_in: 'From the schedule (ticked)',
  not_measured_unpaid: 'Not measured — unpaid',
  punch_unusable: 'Punch unusable — reported',
}

// WHAT A READER NEEDS BESIDE EACH PERSON IN A PANEL (§CORE-17.3x) — off the row's own `fields`.
const PAYROLL_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Store', get: r => (r.fields?.store as string) || '—',
    sortValue: r => (r.fields?.store as string) || '' },
  { header: 'Hours basis', get: r => {
      const b = r.fields?.['hours basis'] as string | undefined
      if (!b) return '—'
      const label = BASIS_LABEL[b] || String(b).replace(/_/g, ' ')
      return r.fields?.['mixed basis'] ? `${label} · mixed` : label
    }, sortValue: r => (r.fields?.['hours basis'] as string) || '' },
  { header: 'Why these hours', get: r => (r.fields?.['why these hours'] as string) || '—' },
]

const chip: React.CSSProperties = { padding: '5px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', cursor: 'pointer' }

export default function StoreOpsReportsPage() {
  // RULE FIVE (§3d) core filters: period is now an arbitrary date RANGE (owner 2026-07-25: "need time
  // range to create payroll for the employees universally" — biweekly/semimonthly/custom periods, not
  // just calendar months) plus store(s)/market(s)/rep(s), all driving the page AND its exports.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [rangeReady, setRangeReady] = useState(false)   // true once a default range has been resolved
  const [ppSettings, setPpSettings] = useState<PayPeriodSettings | null>(null)
  const [rows, setRows] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<'employee' | 'store'>('employee')
  // 2026-07-27 owner directive — same Deliverable 2/3/4 additions as the Payroll Report (shared data
  // source, GET /storeops/payroll): drill-down modal, over-limit highlighting (display only, reuses
  // storeops.hours_budget), manual-edit marker (storeops.payroll_change_log, migration 414).
  const [drill, setDrill] = useState<{ employee_id: string; name?: string } | null>(null)
  const [overAlone, setOverAlone] = useState<Set<string>>(new Set())
  const [overStores, setOverStores] = useState<Set<string>>(new Set())
  const [editedEmpIds, setEditedEmpIds] = useState<Set<string>>(new Set())
  // §CORE-17.3x — the people behind each tile, folded server-side over the SAME rows this report
  // serves, from the SAME declaration the Payroll Report's tiles open (one grain, one catalogue).
  const [panels, setPanels] = useState<Record<string, DrillPanel>>({})

  // Owner directive 2026-07-25: default the range to the tenant's OWN pay period (migration 085
  // config, resolved server-side — never re-derived here, see ../lib/pay-period.ts). Degrades to the
  // current calendar month if the endpoint/migration isn't reachable yet.
  useEffect(() => {
    let cancelled = false
    apiCached('/api/v1/core/tenant-settings', CONFIG).then((r: any) => {
      if (cancelled) return
      const cur = currentPeriodFromSettingsResponse(r)
      if (cur) {
        setPpSettings(cur.settings)
        setFilt(f => ({ ...f, period: cur.period.start, periodTo: cur.period.end }))
      } else {
        const mr = monthRange(0)
        setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end }))
      }
    }).catch(() => {
      const mr = monthRange(0)
      if (!cancelled) setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end }))
    }).finally(() => { if (!cancelled) setRangeReady(true) })
    return () => { cancelled = true }
  }, [])

  function load(start: string, end: string) {
    setLoading(true)
    Promise.all([
      api(`/api/v1/storeops/payroll?start=${start}&end=${end}`).catch(() => []),
      // include_inactive=true: this is a HISTORICAL report — a closed store may still own rows in
      // range, and the market lookup below must still resolve it (GET /stores now defaults to
      // active-only, 2026-08-06 disabled-T-store fix).
      apiCached('/api/v1/storeops/stores?include_inactive=true', LOOKUP).catch(() => []),
    ]).then(([p, s]) => { setRows(p || []); setStores(s || []) })
      .catch(console.error).finally(() => setLoading(false))
    api(`/api/v1/storeops/payroll/over-hours?start=${start}&end=${end}`).then((r: any) => {
      const alone = new Set<string>(), st = new Set<string>()
      for (const wk of (r?.weeks || [])) {
        if (wk.over) st.add(wk.store_code)
        for (const e of (wk.employees || [])) if (e.over_alone) alone.add(e.employee_id)
      }
      setOverAlone(alone); setOverStores(st)
    }).catch(() => { setOverAlone(new Set()); setOverStores(new Set()) })
    api(`/api/v1/storeops/payroll-change-log?start=${start}&end=${end}`).then((r: any) => {
      setEditedEmpIds(new Set<string>((r?.items || []).map((it: any) => it.employee_id).filter(Boolean)))
    }).catch(() => setEditedEmpIds(new Set()))
    // The rows behind the tiles (§CORE-17.3x) — /payroll is a bare array, so the panels come from
    // the sibling `…/payroll/drilldown` beside it. A pay-gated caller gets the hours panels only.
    api(`/api/v1/storeops/payroll/drilldown?start=${start}&end=${end}`)
      .then((d: { drilldown?: Record<string, DrillPanel> }) => setPanels(d?.drilldown || {}))
      .catch(() => setPanels({}))
  }
  useEffect(() => {
    if (!rangeReady || !filt.period || !filt.periodTo) return
    load(filt.period, filt.periodTo)
  }, [rangeReady, filt.period, filt.periodTo])

  // store_code -> market
  const mktOf: Record<string, string> = {}
  stores.forEach(s => { if (s.store_code) mktOf[s.store_code] = s.market || '' })
  const withMkt = rows.map(r => ({ ...r, market: mktOf[r.store] || '' }))

  const storeOptions = useMemo(() => stores
    .filter(s => s.store_code)
    .map(s => ({ id: s.store_code, label: s.store_code + (s.is_active === false ? ' (inactive)' : ''), sublabel: s.address || s.market || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [stores])
  const marketOptions = useMemo(() =>
    Array.from(new Set(stores.map(s => s.market).filter(Boolean) as string[])).sort(), [stores])
  const repOptions = useMemo(() => optionsFromRows(withMkt, { rep: r => r.name }).reps, [withMkt])

  // Filters narrow the range-loaded rows — tiles, both views, and the export all read from this SAME
  // filtered set ("what you see is what exports", RULE FIVE/FOUR).
  const visibleRows = useMemo(() => filterRows(withMkt, filt, {
    store: r => r.store, market: r => r.market, rep: r => r.name,
  }), [withMkt, filt])

  // per-store rollup (ReportShell handles any further ad hoc filtering on top)
  const byStore = Object.values(visibleRows.reduce((acc: any, r) => {
    const k = r.store || '—'
    if (!acc[k]) acc[k] = { store: k, market: r.market, employees: 0, scheduled_hours: 0, actual_hours: 0, scheduled_pay: 0, actual_pay: 0, shifts: 0 }
    acc[k].employees += 1
    acc[k].scheduled_hours += r.scheduled_hours || 0
    acc[k].actual_hours += r.actual_hours || 0
    acc[k].scheduled_pay += r.scheduled_pay || 0
    acc[k].actual_pay += r.actual_pay || 0
    acc[k].shifts += r.shifts || 0
    return acc
  }, {})).sort((a: any, b: any) => b.actual_pay - a.actual_pay)

  const tot = visibleRows.reduce((t, r) => ({
    sh: t.sh + (r.scheduled_hours || 0), ah: t.ah + (r.actual_hours || 0),
    sp: t.sp + (r.scheduled_pay || 0), ap: t.ap + (r.actual_pay || 0),
  }), { sh: 0, ah: 0, sp: 0, ap: 0 })

  // ── §CORE-17.3d/§CORE-17.3x — THE TILES AND THEIR EVIDENCE ARE ONE FOLD ───────────────────────
  // This page filters in the BROWSER and reduces over what survives (and regroups the same rows per
  // store in the By-Store view), so each panel is NARROWED to the people on screen on the
  // person-and-store key the server served, and each tile READS ITS NUMBER OFF its narrowed panel
  // wherever the two agree to the cent. The `Employees` tile is deliberately NOT drillable: it is a
  // count of the rows a panel's own header already states, never Σ(a field).
  const shownPeople = useMemo(
    () => new Set(visibleRows.map(r => screenKey({ employee_id: r.employee_id, store: r.store }))),
    [visibleRows])
  const narrowed = useMemo(() => {
    const out: Record<string, DrillPanel> = {}
    Object.keys(panels).forEach(k => {
      const p = narrowPanel(panels[k], (_r: DrillRow, key: string) => key === '' || shownPeople.has(key))
      if (p) out[k] = p
    })
    return out
  }, [panels, shownPeople])
  const folded: Record<string, number> = {
    scheduled_hours: tot.sh, actual_hours: tot.ah, scheduled_pay: tot.sp, actual_pay: tot.ap,
  }
  const tileValue = (k: string): number => {
    const n = folded[k] || 0
    return agreesToTheCent(narrowed[k], n) ? Number(narrowed[k]?.figure?.amount ?? n) : n
  }
  const payrollDrill = useDrillTile(
    narrowed, (k, p) => !(k in folded) || agreesToTheCent(p, folded[k] || 0))

  // PAY VISIBILITY (mig 434). This report reads the SAME gated GET /storeops/payroll the Payroll
  // page does, and a gated caller's money keys are DELETED server-side — but unlike that page this
  // one had no detection at all, so every pay column stayed on screen and `money()` rendered the
  // missing values as $0.00, in the table, the store rollup, the tiles AND the Excel/PDF export.
  // "This person earns nothing" is the exact lie strip-not-zero exists to prevent, and it was
  // reaching the one workforce report that shows hours and pay together.
  // Detection is by ABSENCE OF THE KEY, identical to payroll/page.tsx: rows exist but none carries a
  // pay key ⇒ gated ⇒ the pay columns and money tiles are DROPPED, never faked.
  const canSeePay = rows.length === 0
    || rows.some((r: any) => 'pay_rate' in r || 'actual_pay' in r || 'scheduled_pay' in r)

  const periodName = rangeLabel(filt.period || '', filt.periodTo || '')

  const empColsAll: ExportColumn[] = [
    { header: 'Employee', get: r => r.name, role: 'rep' },
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market },
    // Salary pay-basis (2026-07-27) — see storeops/payroll/page.tsx's identical column for rationale.
    { header: 'Pay basis', get: (r: any) => r.pay_basis && r.pay_basis !== 'hourly' ? (PAY_BASIS_LABEL[r.pay_basis as PayBasis] || r.pay_basis) : 'Hourly' },
    { header: 'Pay $/hr', get: (r: any) => r.pay_basis && r.pay_basis !== 'hourly' ? undefined : r.pay_rate, money: true },
    { header: 'Sched Hrs', get: r => Math.round((r.scheduled_hours || 0) * 10) / 10, align: 'right' },
    { header: 'Actual Hrs', get: r => Math.round((r.actual_hours || 0) * 10) / 10, align: 'right' },
    // Lunch-break auto-deduction (owner directive 2026-07-27, Deliverable 3) — HONESTY: an explicit
    // line, never folded silently into Actual Hrs (which is already the NET/post-deduction figure —
    // see GET /payroll). 0 for the common case (feature off, or no day qualified this period).
    { header: 'Lunch (auto)', get: r => r.lunch_deduction_hours ? `− ${Number(r.lunch_deduction_hours).toFixed(2)}` : '' },
    // Net = actual − scheduled, SIGNED (owner directive 2026-07-27) — ▲/▼ highlighted cue.
    { header: 'Net (Actual − Sched)', get: r => {
        const v = (r.actual_hours || 0) - (r.scheduled_hours || 0)
        const sign = v > 0.05 ? '▲ +' : v < -0.05 ? '▼ ' : '— '
        return `${sign}${v.toFixed(1)}`
      } },
    { header: 'Sched Pay', get: r => r.scheduled_pay, money: true },
    { header: 'Actual Pay', get: r => r.actual_pay, money: true },
    { header: 'Shifts', get: r => r.shifts, align: 'right' },
    // Flags (2026-07-27, Deliverables 3+4) — own column, own explanation, see payroll/page.tsx's
    // identical column for the full rationale (kept out of the numeric hour columns on purpose).
    { header: 'Flags', get: r => {
        const f: string[] = []
        if (editedEmpIds.has(r.employee_id)) f.push('✎ edited')
        if (overAlone.has(r.employee_id) || overStores.has(r.store)) f.push('⚠ over weekly limit')
        // Gate-1 N6 — see payroll/page.tsx's identical column for the full rationale.
        if ((r.scheduled_hours || 0) > 0.05 && !(r.scheduled_pay > 0) && (r.pay_rate || 0) > 0) f.push('ℹ sched pay incl. a $0-rate portion')
        return f.join(' · ')
      } },
  ]
  const storeColsAll: ExportColumn[] = [
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market },
    { header: 'Employees', get: r => r.employees, align: 'right' },
    { header: 'Sched Hrs', get: r => Math.round(r.scheduled_hours * 10) / 10, align: 'right' },
    { header: 'Actual Hrs', get: r => Math.round(r.actual_hours * 10) / 10, align: 'right' },
    { header: 'Sched Pay', get: r => r.scheduled_pay, money: true },
    { header: 'Actual Pay', get: r => r.actual_pay, money: true },
  ]

  // One money-column vocabulary, filtered in one place, so the table, the store rollup and every
  // export agree about what a gated caller may see.
  const PAY_COLS = new Set(['Pay $/hr', 'Sched Pay', 'Actual Pay'])
  const empCols = canSeePay ? empColsAll : empColsAll.filter(c => !PAY_COLS.has(c.header))
  const storeCols = canSeePay ? storeColsAll : storeColsAll.filter(c => !PAY_COLS.has(c.header))

  const Tile = ({ label, value, drillKey, drillLabel }:
    { label: string; value: string; drillKey?: string; drillLabel?: string }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 130 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
      {drillKey && (
        <DrillTileLinks drills={payrollDrill.dl(drillKey, drillLabel || 'the rows behind this figure')}
          open={payrollDrill.open} onToggle={payrollDrill.toggle} />
      )}
    </div>
  )

  function setRange(start: string, end: string) {
    setFilt(f => ({ ...f, period: start, periodTo: end }))
  }
  // StandardFilterBar's generic "Clear filters" also blanks period/periodTo in range mode — fine for
  // most reports, but this one always needs SOME range to fetch data. Re-pin the range to whatever's
  // currently active instead of letting it go blank (store/market/rep filters still clear normally).
  function onFilterChange(v: StandardFilterValue) {
    setFilt(v.period || v.periodTo ? v : { ...v, period: filt.period, periodTo: filt.periodTo })
  }
  function step(dir: 1 | -1) {
    if (!filt.period || !filt.periodTo) return
    const next = stepPeriod({ start: filt.period, end: filt.periodTo }, ppSettings, dir)
    setRange(next.start, next.end)
  }
  function useThisPayPeriod() {
    apiCached('/api/v1/core/tenant-settings', CONFIG).then((r: any) => {
      const cur = currentPeriodFromSettingsResponse(r)
      if (cur) { setPpSettings(cur.settings); setRange(cur.period.start, cur.period.end) }
    }).catch(() => {})
  }

  // Export filename reflects the ACTIVE range (RULE FOUR: what you see is what exports).
  const filename = `storeops-payroll-${filt.period || 'range'}_to_${filt.periodTo || 'range'}`

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📋 StoreOps Reports — Hours & Payroll</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Scheduled vs actual hours and pay, per employee and per store. {periodName}.
            {(overAlone.size > 0 || overStores.size > 0) && (
              <span style={{ color: 'var(--c-fg-dc2626)', fontWeight: 600 }}> · ⚠ hours over the configured weekly limit this period (highlighted below)</span>
            )}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a href="/storeops/salary-advances" className="btn" style={{ fontSize: 13 }}>💵 Salary Advances</a>
          <a href="/storeops/payroll-change-log" className="btn" style={{ fontSize: 13 }}>📜 Payroll Change Log</a>
          <a href="/storeops/dm-accessory-attribution" className="btn" style={{ fontSize: 13 }}>🗺️ DM Accessory Attribution</a>
        </div>
      </div>

      <StandardFilterBar
        value={filt} onChange={onFilterChange}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        right={
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <button style={chip} onClick={() => step(-1)} title="Previous pay period">‹ Prev period</button>
            <button style={chip} onClick={() => step(1)} title="Next pay period">Next period ›</button>
            <button style={chip} onClick={useThisPayPeriod}>This pay period</button>
            <button style={chip} onClick={() => { const r = monthRange(0); setRange(r.start, r.end) }}>This month</button>
            <button style={chip} onClick={() => { const r = monthRange(-1); setRange(r.start, r.end) }}>Last month</button>
          </div>
        }
      />

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
          {(['employee', 'store'] as const).map(v => (
            <button key={v} onClick={() => setView(v)} className="btn" style={{ background: view === v ? 'white' : 'transparent', color: view === v ? 'var(--accent)' : 'var(--text2)', fontSize: 13, boxShadow: view === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none' }}>
              {v === 'employee' ? '👤 By Employee' : '🏪 By Store'}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Scheduled Hrs" value={tileValue('scheduled_hours').toFixed(1)}
          drillKey="scheduled_hours" drillLabel="the shifts behind these hours" />
        <Tile label="Actual Hrs" value={tileValue('actual_hours').toFixed(1)}
          drillKey="actual_hours" drillLabel="the people behind these hours, and what measured them" />
        {canSeePay && <Tile label="Scheduled Pay" value={fmt(tileValue('scheduled_pay'))}
          drillKey="scheduled_pay" drillLabel="the people behind this pay" />}
        {canSeePay && <Tile label="Actual Pay" value={fmt(tileValue('actual_pay'))}
          drillKey="actual_pay" drillLabel="the people behind this pay" />}
        {/* A cardinality, not a fold — no panel (§CORE-17.3x). */}
        <Tile label="Employees" value={String(visibleRows.length)} />
      </div>

      {/* ONE renderer for whichever figure is open — the one home's panel, never a shape of ours. */}
      {payrollDrill.panel && (
        <div style={{ marginBottom: 18 }}>
          <DrillDownPanel panel={payrollDrill.panel} extraColumns={PAYROLL_DRILL_COLUMNS} />
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : visibleRows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No hours for {periodName}. Payroll is computed from entered shifts and clock-in/out activity — add shifts in the Schedule, or have employees clock in from the /portal, for this period.
        </div>
      ) : (
        <ReportShell
          title={`StoreOps Hours & Payroll — ${periodName}`}
          subtitle={view === 'employee' ? 'By employee · click a row for the day-by-day actual-hours breakdown' : 'By store'}
          filename={filename}
          columns={view === 'employee' ? empCols : storeCols}
          rows={view === 'employee' ? visibleRows : (byStore as any[])}
          onRowClick={view === 'employee' ? (r => r.employee_id && setDrill({ employee_id: r.employee_id, name: r.name })) : undefined}
          rowStyle={view === 'employee' ? (r => (overAlone.has(r.employee_id) || overStores.has(r.store)) ? { background: 'rgba(220,38,38,0.07)' } : undefined) : undefined}
        />
      )}

      {drill && (
        <ActualHoursDrilldown employeeId={drill.employee_id} name={drill.name}
          start={filt.period || ''} end={filt.periodTo || ''} onClose={() => setDrill(null)} />
      )}
    </div>
  )
}
