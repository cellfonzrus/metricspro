'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID, localToday } from '@/lib/client'
import { apiCached } from '@/lib/cache'
import EmployeeWidgets from '@/components/EmployeeWidgets'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// Manager TEAM snapshot — headline target tiles + per-store + per-rep rollup for the caller's span
// (or a chosen org unit). Shared by the /portal "My Team" tab and the platform /storeops/team page.
// Tap a rep to drill into their full EmployeeWidgets (reuses /core/employee-dashboard).
//
// Auth: pass `token` to scope to the SIGNED-IN manager's span (no unit_id), or pass `unitId` to roll
// up a specific node (admins picking a unit). unitId wins on the backend.
//
// RULE FIVE (§3d, owner 2026-08-03 "add totals ... with a standard filters on top") — store(s) /
// market / rep(s) filter this ALREADY-loaded span client-side (the payload is already the caller's
// org-scoped span). PERIOD is deliberately NOT duplicated here (`show.period=false`): this component
// receives `period` as a prop already driven by the platform's own global period selector
// (`(platform)/layout.tsx`, which every page including /storeops/team already sits under) — a second,
// independent period control on this component would be a second source of truth for the same value,
// not an appended module filter. Filter state drives the headline tiles, the money-on-table tile, the
// people tree's row-level KPI badges (unaffected — see below), BOTH tables, the bottom Totals card, and
// the export sheets (RULE FOUR §3c) — the one thing it does NOT narrow is the org People tree, which is
// a navigation directory (every employee under the manager), not a report row set.
//
// A rep-only filter narrows stores to "stores at least one selected rep touched" (mirrors the backend's
// OWN rep-filter semantics in `get_targets_summary`, comment: "keeps only stores none of them touch" —
// store-level target/achieved is whole-store and can't be split per rep, so a selected rep's store still
// shows its FULL store total, not a per-rep slice of it).

const tile: React.CSSProperties = { flex: '1 1 150px', minWidth: 140, padding: 12, borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '7px 9px', borderBottom: '1px solid var(--border)', fontSize: 13 }
const CAT_LABEL: Record<string, string> = { activations: 'Activations', upgrades: 'Upgrades', byod: 'BYOD', accessories: 'Accessories' }
const cap = (s: string) => CAT_LABEL[s] || (s.charAt(0).toUpperCase() + s.slice(1))

export default function TeamSnapshot({ period, token, unitId, today }:
  { period: string; token?: string; unitId?: string; today?: string }) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [empMap, setEmpMap] = useState<Record<string, string>>({})   // upper(name) -> employee_id
  const [tree, setTree] = useState<any>(null)                         // org subtree (people hierarchy)
  const [drillRep, setDrillRep] = useState<string | null>(null)
  const [drill, setDrill] = useState<any>(null)
  const [drillBusy, setDrillBusy] = useState(false)
  const [filter, setFilter] = useState<StandardFilterValue>(emptyStandardFilter())

  const authed = useCallback((path: string) =>
    api(path, token ? { headers: { Authorization: `Bearer ${token}` } } : {}), [token])

  useEffect(() => {
    if (!period) return
    setLoading(true); setErr(''); setDrillRep(null); setDrill(null)
    const qs = `?today=${encodeURIComponent(today || localToday())}${unitId ? `&unit_id=${encodeURIComponent(unitId)}` : ''}`
    authed(`/api/v1/commcalc/team/${encodeURIComponent(period)}/snapshot${qs}`)
      .then(setData).catch((e: any) => setErr(e?.message || 'Failed to load team')).finally(() => setLoading(false))
    authed(`/api/v1/storeops/org/my-team${unitId ? `?unit_id=${encodeURIComponent(unitId)}` : ''}`)
      .then(setTree).catch(() => setTree(null))
    apiCached('/api/v1/storeops/employees').then((es: any[]) => {
      const m: Record<string, string> = {}
      ;(es || []).forEach(e => { if (e.employee_id && e.name) m[String(e.name).trim().toUpperCase()] = e.employee_id })
      setEmpMap(m)
    }).catch(() => {})
  }, [period, unitId, today, authed])

  const loadDrill = (eid: string, label: string) => {
    setDrillRep(label); setDrill(null); setDrillBusy(true)
    api(`/api/v1/core/employee-dashboard?org_id=${ORG_ID}&employee_id=${encodeURIComponent(eid)}`)
      .then(async (d: any) => {
        const out: any = { dash: d, coach: null, repTargets: null }
        const nm = d?.employee?.name, per = d?.period, store = d?.employee?.store
        if (nm && per) { try { const c = await api(`/api/v1/commcalc/coaching/${encodeURIComponent(per)}?rep=${encodeURIComponent(nm)}`); out.coach = (c?.reps || [])[0] || null } catch {} }
        if (nm && per && store) { try { out.repTargets = await api(`/api/v1/commcalc/targets/${encodeURIComponent(per)}/calendar?scope=rep&store_code=${encodeURIComponent(store)}&rep=${encodeURIComponent(nm)}&today=${localToday()}`) } catch {} }
        setDrill(out)
      }).catch((e: any) => setDrill({ _err: e?.message || 'Failed to load rep' })).finally(() => setDrillBusy(false))
  }
  const openRep = (repName: string) => {
    const eid = empMap[String(repName).trim().toUpperCase()]
    if (!eid) { setDrillRep(repName); setDrill({ _noEmp: true }); return }
    loadDrill(eid, repName)
  }
  // Drill straight from the people tree (employee_id known) — falls back to name lookup if missing.
  const openEmpId = (eid: string, name: string) => {
    const id = eid || empMap[String(name).trim().toUpperCase()]
    if (!id) { setDrillRep(name); setDrill({ _noEmp: true }); return }
    loadDrill(id, name)
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading team…</div>
  if (err) return <div className="card" style={{ padding: 14, color: '#c0392b', borderColor: '#c0392b' }}>{err}</div>
  if (!data) return null
  if (!data.is_manager && !unitId) {
    return <div className="card" style={{ padding: 18, color: 'var(--text2)', fontSize: 14 }}>
      You don’t manage any team yet. An admin can assign you to an org unit in <b>Org Structure</b>, then your
      stores and reps appear here.
    </div>
  }

  const totals = data.totals || {}
  const catKeys = Object.keys(totals)   // stable set (targets_engine.CATEGORIES) — kept even if a filter empties the result
  const stores: any[] = data.stores || []
  const reps: any[] = data.reps || []

  // ── RULE FIVE filtering (client-side, over the already-loaded span) ────────────────────────────
  // store/market narrow `stores` directly. rep narrows `reps` directly AND drops any store none of the
  // selected reps touched (store totals stay whole-store — see the header note). Options are the real
  // values present in this span (pick-don't-type, never a hard-coded list).
  const fold = (v: any) => String(v ?? '').trim().toLowerCase()
  const storesByFilter = filterRows(stores, filter, { store: (s: any) => s.address || s.store_code, market: (s: any) => s.market })
  const filteredStores = filter.reps.length === 0 ? storesByFilter : storesByFilter.filter((s: any) => {
    const sel = new Set(filter.reps.map(fold))
    return (s.reps || []).some((rr: any) => sel.has(fold(rr.rep)))
  })
  // Reps carry no market of their own — resolve it off the store they sold at (by address OR code, either
  // may be what `rep.store` holds), same fallback pattern as the store table itself.
  const marketByStoreKey: Record<string, string> = {}
  stores.forEach((s: any) => {
    const mk = s.market || ''
    if (s.store_code) marketByStoreKey[fold(s.store_code)] = mk
    if (s.address) marketByStoreKey[fold(s.address)] = mk
  })
  const filteredReps = filterRows(reps, filter, {
    store: (r: any) => r.store, rep: (r: any) => r.rep,
    market: (r: any) => marketByStoreKey[fold(r.store)] || '',
  })
  const filterOpts = optionsFromRows(stores, { store: (s: any) => s.address || s.store_code, market: (s: any) => s.market })
  const repFilterOpts = optionsFromRows(reps, { rep: (r: any) => r.rep }).reps

  // Sum the SAME additive per-category fields the backend's `_team_totals` sums, over the FILTERED
  // stores — byte-identical to the server's `totals` when no filter narrows anything (proof:
  // scratchpad/prove_myteam_filters.mjs), and a true subset total once a filter is applied.
  function sumCategories(storeRows: any[]): Record<string, any> {
    const out: Record<string, any> = {}
    for (const s of storeRows) {
      for (const [cat, c] of Object.entries((s.categories || {}) as Record<string, any>)) {
        const t = out[cat] || (out[cat] = { unit: (c as any).unit, monthly: 0, achieved_mtd: 0, need: 0, today_target: 0 })
        t.monthly += Number((c as any).monthly) || 0
        t.achieved_mtd += Number((c as any).achieved_mtd) || 0
        t.need += Number((c as any).need) || 0
        t.today_target += Number((c as any).today_target) || 0
      }
    }
    for (const t of Object.values(out) as any[]) {
      t.monthly = Math.round(t.monthly * 100) / 100
      t.achieved_mtd = Math.round(t.achieved_mtd * 100) / 100
      t.need = Math.round(t.need * 100) / 100
      t.today_target = Math.round(t.today_target * 100) / 100
      t.pct = t.monthly > 0 ? Math.round((100 * t.achieved_mtd / t.monthly) * 10) / 10 : 0
    }
    return out
  }
  const filteredTotals = sumCategories(filteredStores)
  const filteredMoneyOnTable = Math.round(filteredReps.reduce((a: number, r: any) => a + (Number(r.money_on_table) || 0), 0) * 100) / 100
  const accCat = filteredTotals['accessories']

  // Per-rep performance keyed by name, to annotate the people tree. Intentionally the FULL (unfiltered)
  // `reps` list — the people tree is a navigation directory of every employee under the manager, not a
  // filtered report row set (header note).
  const perfByName: Record<string, any> = {}
  reps.forEach((r: any) => { if (r.rep) perfByName[String(r.rep).trim().toUpperCase()] = r })
  const treeNodes: any[] = tree?.tree || []

  // RULE FOUR (§3c): tiles-doctrine multi-sheet export (Metric/Value summary + the two visible detail
  // tables) — no PII, these are already-computed KPI/target numbers this view displays, not a change
  // to any payout calculation. Exports the FILTERED rows — what-you-see-is-what-exports.
  const summaryRows = [
    ...catKeys.map(k => ({ k: cap(k), v: filteredTotals[k] ? `${filteredTotals[k].achieved_mtd} / ${filteredTotals[k].monthly} (${filteredTotals[k].pct}%)` : '0 / 0 (0%)' })),
    { k: 'Money on table', v: `$${Number(filteredMoneyOnTable || 0).toLocaleString()}` },
    { k: 'Stores', v: filteredStores.length }, { k: 'Reps', v: filteredReps.length },
  ]
  const summaryCols: ExportColumn[] = [{ header: 'Metric', get: (r: any) => r.k }, { header: 'Value', get: (r: any) => r.v }]
  const storeCols: ExportColumn[] = [
    { header: 'Store', field: 'store', role: 'store', get: (s: any) => s.address || s.store_code },
    { header: 'Conversion', field: 'conversion', get: (s: any) => s.conversion?.rate != null ? `${s.conversion.rate}%` : '' },
    ...catKeys.map(k => ({ header: cap(k), field: k, get: (s: any) => { const c = s.categories?.[k]; return c ? `${c.achieved_mtd}/${c.monthly}` : '' } } as ExportColumn)),
  ]
  const repCols: ExportColumn[] = [
    { header: 'Rep', field: 'rep', role: 'rep', get: (r: any) => r.rep },
    { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store },
    { header: 'Tier', field: 'tier', get: (r: any) => r.tier != null ? `${r.tier}×` : '' },
    { header: 'KPIs Met', field: 'kpis', get: (r: any) => `${r.kpis_met}/${r.total_kpis}` },
    { header: 'Money on Table', field: 'money_on_table', money: true, get: (r: any) => r.money_on_table || 0 },
  ]
  const exportSheets = [
    { name: 'Summary', columns: summaryCols, rows: summaryRows },
    ...(filteredStores.length > 0 ? [{ name: 'Stores', columns: storeCols, rows: filteredStores }] : []),
    { name: 'Reps', columns: repCols, rows: filteredReps },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
        <StandardFilterBar value={filter} onChange={setFilter}
          show={{ period: false, stores: true, markets: true, reps: true }}
          storeOptions={filterOpts.stores} marketOptions={filterOpts.markets} repOptions={repFilterOpts}
          storeLabel="Stores…" marketLabel="Markets…" repLabel="Reps…" />
        <ReportExportBar title="Team Snapshot" subtitle={period} sheets={exportSheets} />
      </div>
      {/* headline tiles — sums over the FILTERED stores (byte-identical to the unfiltered server totals
          when no filter is applied; narrows when one is) */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        {catKeys.map(k => {
          const t = filteredTotals[k] || { achieved_mtd: 0, monthly: 0, pct: 0, need: 0 }
          return (
            <div key={k} style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>{cap(k)}</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{t.achieved_mtd}<span style={{ fontSize: 13, color: 'var(--text3)', fontWeight: 500 }}> / {t.monthly}</span></div>
              <div style={{ height: 6, borderRadius: 4, background: 'var(--bg2)', marginTop: 6, overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(100, t.pct)}%`, height: '100%', background: t.pct >= 100 ? '#16794a' : t.pct >= 60 ? '#f5a623' : '#dc2626' }} />
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{t.pct}% · need {t.need}</div>
            </div>
          )
        })}
        <div style={{ ...tile, background: filteredMoneyOnTable > 0 ? '#fdeaea' : 'var(--surface)' }}>
          <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>Money on table</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: filteredMoneyOnTable > 0 ? '#b42318' : 'inherit' }}>${Number(filteredMoneyOnTable || 0).toLocaleString()}</div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{filteredStores.length} store{filteredStores.length !== 1 ? 's' : ''} · {filteredReps.length} rep{filteredReps.length !== 1 ? 's' : ''}</div>
        </div>
      </div>

      {/* people hierarchy — every employee under this manager, drillable (regional → market mgrs → reps) */}
      {treeNodes.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>Team</div>
          {treeNodes.map((n: any) => (
            <UnitNode key={n.unit_id} node={n} perfByName={perfByName} onEmp={openEmpId} depth={0} />
          ))}
        </div>
      )}

      {/* per-store */}
      {filteredStores.length > 0 && (
        <div className="card table-wrapper" style={{ padding: 0, marginBottom: 14 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <th style={th}>Store</th><th style={th}>Conversion</th>
              {catKeys.map(k => <th key={k} style={th}>{cap(k)}</th>)}
            </tr></thead>
            <tbody>
              {filteredStores.map((s, i) => (
                <tr key={i}>
                  <td style={td}>{s.address || s.store_code}</td>
                  <td style={td}>{s.conversion?.rate != null ? `${s.conversion.rate}%` : '—'}</td>
                  {catKeys.map(k => {
                    const c = s.categories?.[k]
                    return <td key={k} style={td}>{c ? `${c.achieved_mtd}/${c.monthly}` : '—'}</td>
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* per-rep */}
      <div className="card table-wrapper" style={{ padding: 0, marginBottom: 14 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={th}>Rep</th><th style={th}>Store</th><th style={th}>Tier</th>
            <th style={th}>KPIs</th><th style={th}>Money on table</th><th style={th}></th>
          </tr></thead>
          <tbody>
            {filteredReps.length === 0 && <tr><td style={td} colSpan={6}><span style={{ color: 'var(--text3)' }}>No reps match the current filters.</span></td></tr>}
            {filteredReps.map((r, i) => (
              <tr key={i}>
                <td style={{ ...td, fontWeight: 600 }}>{r.rep}</td>
                <td style={td}>{r.store}</td>
                <td style={td}>{r.tier != null ? `${r.tier}×` : '—'}</td>
                <td style={td}>{r.kpis_met}/{r.total_kpis}</td>
                <td style={{ ...td, color: r.money_on_table > 0 ? '#b42318' : 'inherit' }}>${Number(r.money_on_table || 0).toLocaleString()}</td>
                <td style={td}><button className="btn btn-sm" onClick={() => openRep(r.rep)}>View ▾</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── TOTALS (owner 2026-08-03: "add the totals for achieved vs the target and the total for
          accessories at the bottom") — achieved-vs-target per category (with attainment %), summed over
          the FILTERED stores above, plus a called-out Total Accessories line since that was the number
          named explicitly. See the code comment on the `accCat` source note below for what feeds it. */}
      <div className="card" style={{ padding: 14, marginTop: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>
          📊 Totals {(filter.stores.length || filter.markets.length || filter.reps.length) ? '(filtered)' : ''}
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: accCat ? 10 : 0 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={th}>Category</th><th style={th}>Achieved</th><th style={th}>Target</th>
            <th style={th}>Attainment</th><th style={th}>Need</th>
          </tr></thead>
          <tbody>
            {catKeys.map(k => {
              const t = filteredTotals[k] || { achieved_mtd: 0, monthly: 0, pct: 0, need: 0, unit: undefined }
              const money = t.unit === 'dollars'
              const fmt = (v: number) => money ? `$${Number(v || 0).toLocaleString()}` : String(v ?? 0)
              return (
                <tr key={k}>
                  <td style={{ ...td, fontWeight: 600 }}>{cap(k)}</td>
                  <td style={td}>{fmt(t.achieved_mtd)}</td>
                  <td style={td}>{fmt(t.monthly)}</td>
                  <td style={{ ...td, fontWeight: 600, color: t.pct >= 100 ? '#16794a' : t.pct >= 60 ? '#f5a623' : '#dc2626' }}>{t.pct}%</td>
                  <td style={td}>{fmt(t.need)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {accCat && (
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '8px 10px', borderRadius: 8, background: 'var(--surface2)' }}>
            <span style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>Total Accessories</span>
            <span style={{ fontSize: 18, fontWeight: 700 }}>${Number(accCat.achieved_mtd || 0).toLocaleString()}</span>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>of ${Number(accCat.monthly || 0).toLocaleString()} target ({accCat.pct}%)</span>
          </div>
        )}
      </div>

      {/* rep drill-down */}
      {drillRep && (
        <div className="card" style={{ marginTop: 14, padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontWeight: 700, fontSize: 16 }}>{drillRep}</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={() => { setDrillRep(null); setDrill(null) }}>Close</button>
          </div>
          {drillBusy && <div style={{ color: 'var(--text3)' }}>Loading…</div>}
          {drill?._noEmp && <div style={{ color: 'var(--text3)', fontSize: 13 }}>No employee record matched “{drillRep}” — ask an admin to link this rep’s Employee ID.</div>}
          {drill?._err && <div style={{ color: '#c0392b', fontSize: 13 }}>{drill._err}</div>}
          {drill?.dash && <EmployeeWidgets data={drill.dash} coach={drill.coach} repTargets={drill.repTargets} />}
        </div>
      )}
    </div>
  )
}

function countEmployees(n: any): number {
  return (n.employees?.length || 0) + (n.children || []).reduce((a: number, c: any) => a + countEmployees(c), 0)
}

// One org-unit node in the drillable people tree: name + level + manager(s), its employees (each with
// a money-on-table badge + drill), then its child units (recursive). Top two levels open by default.
function UnitNode({ node, perfByName, onEmp, depth }:
  { node: any; perfByName: Record<string, any>; onEmp: (eid: string, name: string) => void; depth: number }) {
  const [open, setOpen] = useState(depth < 2)
  const hasKids = (node.children?.length || 0) > 0 || (node.employees?.length || 0) > 0
  return (
    <div style={{ marginLeft: depth ? 12 : 0, borderLeft: depth ? '1px solid var(--border)' : undefined, paddingLeft: depth ? 10 : 0 }}>
      <div onClick={() => hasKids && setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: hasKids ? 'pointer' : 'default', padding: '4px 0', flexWrap: 'wrap' }}>
        <span style={{ width: 12, color: 'var(--text3)', fontSize: 11 }}>{hasKids ? (open ? '▾' : '▸') : ''}</span>
        <b style={{ fontSize: 13 }}>{node.name}</b>
        {node.level && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {node.level}</span>}
        {node.managers?.length > 0 && <span style={{ fontSize: 11, color: 'var(--text2)' }}>👤 {node.managers.map((m: any) => m.name).join(', ')}</span>}
        <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {countEmployees(node)} ppl</span>
      </div>
      {open && (
        <div>
          {(node.employees || []).map((e: any) => {
            const p = perfByName[String(e.name || '').trim().toUpperCase()]
            return (
              <div key={e.employee_id || e.name} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0 3px 22px', fontSize: 13, flexWrap: 'wrap' }}>
                <span>{e.is_manager ? '🧑‍💼' : '🧑'}</span>
                <span style={{ fontWeight: 500 }}>{e.name}</span>
                {e.role && <span style={{ fontSize: 11, color: 'var(--text3)' }}>{e.role}</span>}
                {e.home_store && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {e.home_store}</span>}
                {p && p.money_on_table > 0 && <span style={{ fontSize: 11, color: '#b42318' }}>${Number(p.money_on_table).toLocaleString()} on table</span>}
                <span style={{ flex: 1 }} />
                <button className="btn btn-sm" onClick={() => onEmp(e.employee_id, e.name)}>View ▾</button>
              </div>
            )
          })}
          {(node.children || []).map((c: any) => (
            <UnitNode key={c.unit_id} node={c} perfByName={perfByName} onEmp={onEmp} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}
