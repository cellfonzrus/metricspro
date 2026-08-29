'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// DM Accessory-Target Attribution (owner directive 2026-08-04, ledger Q7 — verbatim: "my team accessory
// numbers are the accessory target for the [stores] calculated by the schedule and for the dm it is the
// total of employees which run under him for the stores they worked in, if an employee works under 2 dms
// then their target for that store goes under the dm for that market."). This page is the "show your
// attribution work" surface: every DM's rollup, broken down to (employee × store × target) rows, with the
// MARKET that routed each row to that DM — so a 2-DM employee's split is verifiable at a glance instead of
// trusting a single aggregate number.
//
// The rep-level accessory TARGET itself is mod-commission's Daily Targets engine (schedule-derived
// proration, unchanged); this page only ROUTES those numbers by store → market → DM. The achieved-$ column
// is read straight off the same source EmployeeWidgets' own rep-target drill-down already uses — nothing
// here recomputes it.
//
// RULE FIVE (§3d): stores/markets/reps narrow the drill-down (period comes from the platform's global
// selector, same convention as My Team — a second period control here would be a second source of truth).
// RULE FOUR (§3c): the drill-down + cross-DM tables export Excel/PDF/email/WhatsApp, filtered rows only.

const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '7px 9px', borderBottom: '1px solid var(--border)', fontSize: 13 }
const card: React.CSSProperties = { flex: '1 1 220px', minWidth: 200, padding: 12, borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer' }
const $ = (n: number) => `$${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

type Row = {
  employee_name: string; employee_id?: string; store_code: string; address?: string; market: string
  target: number; achieved: number; rep_share?: number; ok?: boolean
  routed_dm?: string; routed_dm_label?: string; ambiguous?: boolean
}
type DmBlock = { label: string; markets: string[]; rows: Row[]; total_target: number; total_achieved: number }

export default function DmAccessoryAttributionPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [activeDm, setActiveDm] = useState<string>('')   // '' = every DM's rows shown together
  const [filter, setFilter] = useState<StandardFilterValue>(emptyStandardFilter())

  const load = useCallback(() => {
    if (!period) return
    setLoading(true); setErr('')
    api(`/api/v1/storeops/dm-accessory-attribution/${encodeURIComponent(period)}`)
      .then(setData).catch((e: any) => setErr(e?.message || 'Failed to load')).finally(() => setLoading(false))
  }, [period])

  useEffect(() => { load() }, [load])

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading DM attribution…</div>
  if (err) return <div className="card" style={{ padding: 14, margin: 24, color: '#c0392b', borderColor: '#c0392b' }}>{err}</div>
  if (!data) return null

  const byDm: Record<string, DmBlock> = data.by_dm || {}
  const dmKeys = Object.keys(byDm).sort((a, b) => byDm[a].label.localeCompare(byDm[b].label))
  const allRows: Row[] = dmKeys.flatMap(k => byDm[k].rows)
  const scopedRows = activeDm ? (byDm[activeDm]?.rows || []) : allRows

  const filterOpts = optionsFromRows(scopedRows, {
    store: (r: Row) => r.address || r.store_code, market: (r: Row) => r.market,
  })
  const repOpts = optionsFromRows(scopedRows, { rep: (r: Row) => r.employee_name }).reps
  const filteredRows = filterRows(scopedRows, filter, {
    store: (r: Row) => r.address || r.store_code, market: (r: Row) => r.market, rep: (r: Row) => r.employee_name,
  })

  const crossDm: any[] = data.cross_dm_employees || []
  const unassigned: Row[] = data.unassigned?.rows || []
  const ambiguous: Record<string, string[]> = data.ambiguous_markets || {}

  const rowCols: ExportColumn[] = [
    { header: 'DM', field: 'routed_dm_label', get: (r: Row) => r.routed_dm_label || '' },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (r: Row) => r.employee_name },
    { header: 'Store', field: 'store', role: 'store', get: (r: Row) => r.address || r.store_code },
    { header: 'Market', field: 'market', get: (r: Row) => r.market },
    { header: 'Accessory Target', field: 'target', money: true, get: (r: Row) => r.target },
    { header: 'Accessory Achieved', field: 'achieved', money: true, get: (r: Row) => r.achieved },
  ]
  const exportSheets = [
    { name: 'Attribution', columns: rowCols, rows: filteredRows },
    ...(crossDm.length > 0 ? [{
      name: 'Cross-DM Employees',
      columns: [
        { header: 'Employee', get: (r: any) => r.employee_name },
        { header: 'DM', get: (r: any) => r._dmLabel },
        { header: 'Target', money: true, get: (r: any) => r.total_target },
      ] as ExportColumn[],
      rows: crossDm.flatMap((e: any) => e.dms.map((d: any) => ({ employee_name: e.employee_name, _dmLabel: (d.label || byDm[d.dm_key]?.label || d.dm_key) + (d.redacted ? ' (not visible to you)' : ''), total_target: d.redacted ? null : d.total_target }))),
    }] : []),
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🗺️ DM Accessory-Target Attribution</h1>
        <span style={{ flex: 1 }} />
        <ReportExportBar title="DM Accessory-Target Attribution" subtitle={period} sheets={exportSheets} />
      </div>
      <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        Each rep's accessory number is their schedule-derived target at the store they worked. A DM's total is
        the sum of every employee-store row whose store's market is granted to them — a rep who worked stores
        in two DMs' markets is split per store, never merged, never double-counted.
      </p>

      {data.caller_scope === 'market' && (
        <div className="card" style={{ padding: 10, marginBottom: 12, fontSize: 12, color: 'var(--text3)' }}>
          🔒 Showing your own granted market(s) only. A rep who also works under another DM still appears
          below (to explain the split) but that other DM's numbers and roster are not shown here.
        </div>
      )}

      {Object.keys(ambiguous).length > 0 && (
        <div className="card" style={{ padding: 12, marginBottom: 12, background: '#fdeaea', borderColor: '#c0392b', fontSize: 13 }}>
          ⚠️ {Object.keys(ambiguous).length} market{Object.keys(ambiguous).length !== 1 ? 's are' : ' is'} granted to
          MORE THAN ONE DM ({Object.entries(ambiguous).map(([m, dms]) => `${m}: ${(dms as string[]).map(k => byDm[k]?.label || k).join(', ')}`).join(' · ')}) —
          those rows count toward every DM claiming the market until the grant is fixed in Roles.
        </div>
      )}

      {/* DM cards — click to focus the drill-down on one DM */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <div style={{ ...card, background: activeDm === '' ? 'var(--surface2)' : card.background }} onClick={() => setActiveDm('')}>
          <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>All DMs</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{$(data.total_target_all_rows || 0)}</div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{data.pairs_considered ?? allRows.length} rows</div>
        </div>
        {dmKeys.map(k => {
          const d = byDm[k]
          return (
            <div key={k} style={{ ...card, background: activeDm === k ? 'var(--surface2)' : card.background }} onClick={() => setActiveDm(k)}>
              <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>{d.label}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>{(d.markets || []).join(', ')}</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{$(d.total_target)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
                achieved {$(d.total_achieved)} · {d.rows.length} row{d.rows.length !== 1 ? 's' : ''}
              </div>
            </div>
          )
        })}
        {unassigned.length > 0 && (
          <div style={{ ...card, cursor: 'default', background: '#fff8e6' }}>
            <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>Unassigned (no DM grant)</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{$(data.unassigned?.total_target || 0)}</div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{unassigned.length} row{unassigned.length !== 1 ? 's' : ''} — grant the market to a DM in Roles</div>
          </div>
        )}
      </div>

      {/* cross-DM verification table — the explicit "verify a 2-DM split at a glance" ask */}
      {crossDm.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>🔀 Employees split across 2+ DMs this period</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <th style={th}>Employee</th><th style={th}>DM</th><th style={th}>Store(s)</th><th style={th}>Target</th>
            </tr></thead>
            <tbody>
              {crossDm.map((e: any) => e.dms.map((d: any, i: number) => (
                <tr key={`${e.employee_name}-${d.dm_key}`}>
                  {i === 0 && <td style={{ ...td, fontWeight: 600 }} rowSpan={e.dms.length}>{e.employee_name}</td>}
                  <td style={td}>{d.label || byDm[d.dm_key]?.label || d.dm_key}{d.redacted && <span title="Another DM's numbers — not visible to you" style={{ color: 'var(--text3)' }}> 🔒</span>}</td>
                  <td style={td}>{d.redacted ? '—' : (d.rows || []).map((r: Row) => r.address || r.store_code).join(', ')}</td>
                  <td style={td}>{d.redacted ? '—' : $(d.total_target)}</td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
      )}

      {/* drill-down: employee × store × target, with the market that routed it */}
      <div style={{ marginBottom: 10 }}>
        <StandardFilterBar value={filter} onChange={setFilter}
          show={{ period: false, stores: true, markets: true, reps: true }}
          storeOptions={filterOpts.stores} marketOptions={filterOpts.markets} repOptions={repOpts}
          storeLabel="Stores…" marketLabel="Markets…" repLabel="Reps…" />
      </div>
      <div className="card table-wrapper" style={{ padding: 0, marginBottom: 14 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={th}>DM</th><th style={th}>Employee</th><th style={th}>Store</th><th style={th}>Market</th>
            <th style={th}>Accessory Target</th><th style={th}>Achieved</th>
          </tr></thead>
          <tbody>
            {filteredRows.length === 0 && <tr><td style={td} colSpan={6}><span style={{ color: 'var(--text3)' }}>No rows match the current filters.</span></td></tr>}
            {filteredRows.map((r: Row, i: number) => (
              <tr key={i}>
                <td style={td}>{r.routed_dm_label}{r.ambiguous && <span title="Market granted to more than one DM" style={{ color: '#c0392b' }}> ⚠</span>}</td>
                <td style={{ ...td, fontWeight: 600 }}>{r.employee_name}</td>
                <td style={td}>{r.address || r.store_code}</td>
                <td style={td}>{r.market}</td>
                <td style={td}>{$(r.target)}</td>
                <td style={td}>{$(r.achieved)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.warnings?.length > 0 && (
        <div className="card" style={{ padding: 12, fontSize: 12, color: 'var(--text3)' }}>
          {data.warnings.length} row{data.warnings.length !== 1 ? 's' : ''} couldn't reach the schedule-target
          engine this load and show as $0 — reload to retry ({data.warnings.slice(0, 3).map((w: any) => `${w.employee_name}@${w.store_code}`).join(', ')}{data.warnings.length > 3 ? '…' : ''}).
        </div>
      )}
    </div>
  )
}
