'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// PLAN ASSIGNMENT AUDIT — READ-ONLY. Nothing on this page changes what anyone is paid. For EVERY
// roster employee it shows which commission plan they resolve to and WHY (via which assignment), and
// flags the dangerous "by-name pin overrides store/market" pattern.
//
// WHY IT EXISTS (real production case, "Silvia Nava"): commission plans resolve per rep with precedence
// employee > role > store > market > default. A rep pinned to a plan BY NAME (employee-scope) therefore
// OUTRANKS their store/market. Silvia is in Chicago but keeps getting the "…NY" plan because she has a
// by-name pin on it; fixing her location does nothing while the pin stands. This page finds ALL such
// cases at once. Every "which plan / via which assignment" answer is read straight from the SAME
// matcher the live calc pays from (_resolve_plan_for(explain=True)), so it cannot drift from the money.

type OverriddenPlan = { plan_name: string; scope: string; scope_value: string }
type Flag = { flag: string; overridden_plans?: OverriddenPlan[]; plan_names_market?: string[]; rep_market?: string }
type Row = {
  employee: string; store: string; market: string; role: string | null
  is_active: boolean; resolved_plan: string | null
  winner_scope: string | null; winner_value: string | null; winner_priority: number | null
  flags: Flag[]; flag_names: string[]
}

const SCOPE_LABEL: Record<string, string> = {
  employee: 'by name (employee)', role: 'by role', store: 'by store', market: 'by market', default: 'default',
}

function flagText(r: Row): string {
  const parts: string[] = []
  for (const f of r.flags || []) {
    if (f.flag === 'by_name_override') {
      const ov = (f.overridden_plans || []).map(o => `${o.plan_name} (${o.scope})`).join(', ')
      parts.push(`BY-NAME OVERRIDE — pin beats location plan: ${ov}`)
    } else if (f.flag === 'no_plan') {
      parts.push('NO PLAN — nothing matched')
    } else if (f.flag === 'location_mismatch') {
      parts.push(`plan name suggests ${(f.plan_names_market || []).join('/')}, rep market is ${f.rep_market || '(blank)'}`)
    }
  }
  return parts.join(' · ')
}

const COLS: ExportColumn[] = [
  { header: 'Flag', get: r => (r.flag_names || []).includes('by_name_override') ? 'BY-NAME OVERRIDE'
      : (r.flag_names || []).includes('no_plan') ? 'NO PLAN'
      : (r.flag_names || []).includes('location_mismatch') ? 'location?' : '' },
  { header: 'Employee', get: r => r.employee, role: 'rep' },
  { header: 'Store', get: r => r.store, role: 'store' },
  { header: 'Market', get: r => r.market },
  { header: 'Role', get: r => r.role || '' },
  { header: 'Resolved plan', get: r => r.resolved_plan || '(none)' },
  { header: 'Won via', get: r => r.winner_scope ? (SCOPE_LABEL[r.winner_scope] || r.winner_scope) : '' },
  { header: 'Assignment value', get: r => r.winner_value || '' },
  { header: 'Details', get: r => flagText(r) },
]

export default function PlanAssignmentAuditPage() {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [onlyFlagged, setOnlyFlagged] = useState(false)

  const load = useCallback(() => {
    setBusy(true); setErr('')
    api(`/api/v1/commcalc/commission-plans/assignment-audit?org_id=${ORG_ID}`)
      .then(setData)
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [])

  useEffect(() => { load() }, [load])

  const rows: Row[] = data?.employees || []
  const acc = useMemo(() => ({ store: (r: Row) => r.store, market: (r: Row) => r.market, rep: (r: Row) => r.employee }), [])
  const opts = useMemo(() => optionsFromRows(rows, acc), [rows, acc])
  const filtered = useMemo(() => filterRows(rows, filt, acc), [rows, filt, acc])
  const shown = useMemo(() => onlyFlagged ? filtered.filter(r => (r.flag_names || []).length > 0) : filtered, [filtered, onlyFlagged])
  const counts = data?.counts || {}

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🕵️ Plan Assignment Audit</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          For every employee, which commission plan they resolve to and <b>why</b> — <b>read-only</b>, nothing here changes a payout.
          Precedence is <code>employee &gt; role &gt; store &gt; market &gt; default</code>, so a <b>by-name pin</b> silently
          overrides a rep&rsquo;s store/market plan. Every answer is read from the same matcher the live calc pays from, so it agrees with what actually pays.
        </p>
      </div>

      {(counts.by_name_override > 0) && (
        <div className="card" style={{ borderLeft: '4px solid var(--red)', marginBottom: 14, fontSize: 13.5 }}>
          <b style={{ color: 'var(--red)' }}>{counts.by_name_override}</b> employee(s) have a <b>by-name pin that overrides their store/market plan</b>.
          Fixing their location will NOT change their pay while the by-name assignment stands — remove the employee-scope assignment on
          the <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}>Incentive Plans</a> page.
        </div>
      )}

      <div className="card" style={{ marginBottom: 14 }}>
        <StandardFilterBar
          value={filt} onChange={setFilt} periodMode="none"
          show={{ period: false, stores: true, markets: true, reps: true }}
          storeOptions={opts.stores} marketOptions={opts.markets} repOptions={opts.reps}
          right={
            <>
              <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <input type="checkbox" checked={onlyFlagged} onChange={e => setOnlyFlagged(e.target.checked)} />
                Flagged only
              </label>
              <button className="btn btn-secondary" onClick={load} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>
            </>
          }
        />
      </div>

      {err && <div className="card" style={{ borderLeft: '4px solid var(--red)', marginBottom: 14, fontSize: 13 }}>{err}</div>}
      {data && !data.ready && (
        <div className="card" style={{ borderLeft: '4px solid var(--amber)', marginBottom: 14, fontSize: 13 }}>{data.note}</div>
      )}

      {data?.ready && (
        <>
          <div className="card" style={{ marginBottom: 14, display: 'flex', gap: 22, flexWrap: 'wrap', fontSize: 13 }}>
            <span>employees audited: <b>{counts.total ?? 0}</b></span>
            <span>by-name overrides: <b style={{ color: (counts.by_name_override || 0) ? 'var(--red)' : undefined }}>{counts.by_name_override ?? 0}</b></span>
            <span>no plan: <b style={{ color: (counts.no_plan || 0) ? 'var(--amber)' : undefined }}>{counts.no_plan ?? 0}</b></span>
            <span>possible location mismatch: <b>{counts.location_mismatch ?? 0}</b></span>
          </div>

          <ReportShell title="Plan assignment audit"
            subtitle="flagged rows first — by-name overrides at the top"
            filename="plan-assignment-audit"
            columns={COLS} rows={shown} compact stickyHeader
            rowStyle={(r: Row) => (r.flag_names || []).includes('by_name_override')
              ? { background: 'rgba(220,38,38,0.10)' }
              : (r.flag_names || []).includes('no_plan')
                ? { background: 'rgba(245,158,11,0.10)' }
                : undefined} />
        </>
      )}
    </div>
  )
}
