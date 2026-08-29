'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'

// Super-admin org-resolution mitigation (same as the Sales Report): these reads carry NO org_id in the
// URL, so a super-admin (whom the tenant middleware does NOT rewrite) would default to the HOUSE org.
// Appending the active tenant fixes this page until the universal client.ts fix lands. No-op otherwise.
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const orgQuery = () => { const p = orgParam(); return p ? '?' + p.slice(1) : '' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

type DsCol = { field: string; label: string; type: string; numeric: boolean; money: boolean; group: boolean }
type PivotAxis = { field: string; label: string; type?: string; agg?: string }
type Dataset = {
  key: string; name: string; columns: DsCol[]; group_dims: string[]; backing_tables: string[]
  has_gated_hidden: boolean; pivot_dims?: PivotAxis[]; pivot_measures?: PivotAxis[]
}
type SecCol = { field: string; label: string; type: string; numeric: boolean; money: boolean; agg: string }
type Pivot = {
  available: boolean; reason?: string
  row_field?: string; col_field?: string; measure?: string; measure_label?: string
  measure_type?: string; agg?: string
  row_keys?: string[]; col_keys?: string[]
  cells?: Record<string, Record<string, number>>
  counts?: Record<string, Record<string, number>>
  row_totals?: Record<string, number>; col_totals?: Record<string, number>; grand_total?: number
  truncated_cols?: boolean; dropped_cols?: string[]
}
type Section = {
  key: string; name: string; available: boolean; reason?: string; grouped_by?: string | null
  columns: SecCol[]; rows: any[]; totals: Record<string, number>; row_count?: number
  gated_columns_hidden?: string[]; pivot?: Pivot | null
}

// A universal group-by dim's friendly label (RULE FIVE naming) — else fall back to the column label.
const UNIVERSAL_GROUP: Record<string, string> = { store: 'Store', rep: 'Rep', market: 'Market', day: 'Day' }
const colType = (t: string): ExportColumn['type'] =>
  t === 'money' ? 'money' : t === 'date' ? 'date' : (t === 'count' || t === 'pct') ? 'number' : 'text'

export default function CustomReportPage() {
  const [period, setPeriod] = useState(thisMonth())
  const [registry, setRegistry] = useState<Dataset[]>([])
  const [regSource, setRegSource] = useState('')
  const [grants, setGrants] = useState<string[]>([])
  const [selDatasets, setSelDatasets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selReps, setSelReps] = useState<string[]>([])
  const [selCols, setSelCols] = useState<Record<string, string[]>>({})   // per-dataset chosen fields (empty = all)
  const [groupBy, setGroupBy] = useState('')
  // Pivot axes (roadmap #4). Empty rows/cols = no pivot, and the page behaves exactly as before.
  const [pivotRows, setPivotRows] = useState('')
  const [pivotCols, setPivotCols] = useState('')
  const [pivotMeasure, setPivotMeasure] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [options, setOptions] = useState<{ stores: string[]; markets: string[]; reps: string[] }>({ stores: [], markets: [], reps: [] })
  const [showBuilder, setShowBuilder] = useState(true)
  // saved definitions
  const [defs, setDefs] = useState<any[]>([])
  const [defName, setDefName] = useState('')
  const [msg, setMsg] = useState('')

  const loadDefs = useCallback(() => {
    api(`/api/v1/commcalc/custom-report/definitions${orgQuery()}`).then((r: any) => setDefs(r.definitions || [])).catch(() => {})
  }, [])

  useEffect(() => {
    api(`/api/v1/commcalc/custom-report/datasets${orgQuery()}`).then((r: any) => {
      const ds: Dataset[] = r.datasets || []
      setRegistry(ds); setRegSource(r.registry_source || ''); setGrants(r.grants || [])
      setSelDatasets(prev => prev.length ? prev : (ds.length ? [ds[0].key] : []))
    }).catch(() => {})
    loadDefs()
  }, [loadDefs])

  const run = useCallback(() => {
    if (!selDatasets.length) { setData(null); return }
    setLoading(true)
    const cols = Array.from(new Set(Object.values(selCols).flat()))
    const qs = new URLSearchParams({
      datasets: selDatasets.join(','), period,
      stores: selStores.join(','), markets: selMarkets.join(','), reps: selReps.join(','),
      group_by: groupBy, columns: cols.join(','),
      pivot_rows: pivotRows, pivot_cols: pivotCols, pivot_measure: pivotMeasure,
    })
    api(`/api/v1/commcalc/custom-report?${qs.toString()}${orgParam()}`)
      .then((r: any) => { setData(r); if (r.filter_options) setOptions(r.filter_options) })
      .catch((e: any) => setData({ sections: [], error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [selDatasets, period, selStores, selMarkets, selReps, groupBy, selCols, pivotRows, pivotCols, pivotMeasure])
  useEffect(() => { run() }, [run])

  const regByKey = useMemo(() => Object.fromEntries(registry.map(d => [d.key, d])), [registry])

  // Group-by options — the union of the selected datasets' group_dims (universal dims first, RULE FIVE).
  // Pivot axes offered = the UNION across the selected datasets, de-duped by field. The server
  // validates per dataset anyway and reports "cannot pivot by that field" for one that lacks it, so a
  // union here is honest: it offers what SOME selected dataset can do rather than the intersection,
  // which would silently hide a usable axis whenever a second dataset is added.
  const pivotDims = useMemo(() => {
    const seen = new Map<string, PivotAxis>()
    for (const k of selDatasets) for (const d of (regByKey[k]?.pivot_dims || [])) if (!seen.has(d.field)) seen.set(d.field, d)
    return Array.from(seen.values())
  }, [selDatasets, regByKey])
  const pivotMeasures = useMemo(() => {
    const seen = new Map<string, PivotAxis>()
    for (const k of selDatasets) for (const m of (regByKey[k]?.pivot_measures || [])) if (!seen.has(m.field)) seen.set(m.field, m)
    return Array.from(seen.values())
  }, [selDatasets, regByKey])

  const groupOptions = useMemo(() => {
    const seen = new Map<string, string>()
    for (const k of selDatasets) {
      const d = regByKey[k]; if (!d) continue
      for (const g of d.group_dims || []) {
        if (seen.has(g)) continue
        const label = UNIVERSAL_GROUP[g] || d.columns.find(c => c.field === g)?.label || g
        seen.set(g, label)
      }
    }
    const universal = ['store', 'rep', 'market', 'day'].filter(g => seen.has(g))
    const extra = Array.from(seen.keys()).filter(g => !universal.includes(g))
    return [...universal, ...extra].map(g => ({ value: g, label: seen.get(g)! }))
  }, [selDatasets, regByKey])

  const toggleCol = (dsKey: string, field: string) => setSelCols(s => {
    const cur = s[dsKey] || []
    return { ...s, [dsKey]: cur.includes(field) ? cur.filter(f => f !== field) : [...cur, field] }
  })

  function applyDefinition(def: any) {
    const c = def?.config || {}
    setSelDatasets(c.datasets || [])
    setSelCols(c.columns || {})
    setGroupBy(typeof c.group_by === 'string' ? c.group_by : '')
    // A saved report predating the pivot has no pivot keys — it loads with the pivot cleared, exactly
    // as it was saved, rather than inheriting whatever was on screen.
    setPivotRows(typeof c.pivot_rows === 'string' ? c.pivot_rows : '')
    setPivotCols(typeof c.pivot_cols === 'string' ? c.pivot_cols : '')
    setPivotMeasure(typeof c.pivot_measure === 'string' ? c.pivot_measure : '')
    const f = c.filters || {}
    if (f.period) setPeriod(f.period)
    setSelStores(f.stores || []); setSelMarkets(f.markets || []); setSelReps(f.reps || [])
    setMsg(`Loaded “${def.name}”.`)
  }

  async function saveDefinition() {
    const name = defName.trim()
    if (!name) { setMsg('Give the report a name first.'); return }
    if (!selDatasets.length) { setMsg('Select at least one dataset.'); return }
    setMsg('Saving…')
    try {
      await api(`/api/v1/commcalc/custom-report/definitions${orgQuery()}`, {
        method: 'POST', body: JSON.stringify({
          name, config: {
            datasets: selDatasets, columns: selCols, group_by: groupBy,
            pivot_rows: pivotRows, pivot_cols: pivotCols, pivot_measure: pivotMeasure,
            filters: { period, stores: selStores, markets: selMarkets, reps: selReps },
          },
        }),
      })
      setMsg(`Saved “${name}”.`); setDefName(''); loadDefs()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  async function deleteDefinition(id: string) {
    try { await api(`/api/v1/commcalc/custom-report/definitions/${id}${orgQuery()}`, { method: 'DELETE' }); loadDefs() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const sections: Section[] = data?.sections || []
  const anyFilter = selStores.length > 0 || selMarkets.length > 0 || selReps.length > 0

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧩 Custom Report</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Build a report over any of your datasets — sales, commissions, targets, KPIs, expenses, chargebacks,
          flags and carrier data. Pick datasets and columns, filter by store / market / rep, group by anything,
          then export or send. Save a configuration to recall it as one of your primary reports.
        </p>
      </div>

      {/* RULE FIVE standardized filter bar */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Month{' '}
          <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />
        </label>
        <MultiSelect allLabel="All stores" width={150} value={selStores} options={options.stores} onChange={setSelStores} searchable />
        <MultiSelect allLabel="All markets" width={140} value={selMarkets} options={options.markets} onChange={setSelMarkets} />
        <MultiSelect allLabel="All reps" width={140} value={selReps} options={options.reps} onChange={setSelReps} searchable />
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Group by{' '}
          <select style={sel} value={groupBy} onChange={e => setGroupBy(e.target.value)}>
            <option value="">— none —</option>
            {groupOptions.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
          </select>
        </label>
        {/* PIVOT (roadmap #4) — rows x columns cross-tab. Dropdowns are populated from the server's
            GATED axis lists, so a column the caller may not see is never offered as an axis. */}
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Pivot rows{' '}
          <select style={sel} value={pivotRows} onChange={e => setPivotRows(e.target.value)}>
            <option value="">— none —</option>
            {pivotDims.map(d => <option key={d.field} value={d.field}>{d.label}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>×&nbsp;columns{' '}
          <select style={sel} value={pivotCols} onChange={e => setPivotCols(e.target.value)}>
            <option value="">— none —</option>
            {pivotDims.filter(d => d.field !== pivotRows).map(d => <option key={d.field} value={d.field}>{d.label}</option>)}
          </select>
        </label>
        {pivotRows && pivotCols && (
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Measure{' '}
            <select style={sel} value={pivotMeasure} onChange={e => setPivotMeasure(e.target.value)}>
              {pivotMeasures.map(m => <option key={m.field} value={m.field}>{m.label}</option>)}
            </select>
          </label>
        )}
        {(pivotRows || pivotCols) && <button className="btn btn-secondary" style={{ fontSize: 12 }}
          onClick={() => { setPivotRows(''); setPivotCols(''); setPivotMeasure('') }}>Clear pivot</button>}
        {anyFilter && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelStores([]); setSelMarkets([]); setSelReps([]) }}>Clear filters</button>}
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setShowBuilder(s => !s)}>{showBuilder ? '▾' : '▸'} Datasets & columns</button>
      </div>

      {/* Builder: dataset multi-select + per-dataset column picker + saved definitions */}
      {showBuilder && (
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Datasets</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
            {registry.map(d => {
              const on = selDatasets.includes(d.key)
              return (
                <button key={d.key} onClick={() => setSelDatasets(s => on ? s.filter(k => k !== d.key) : [...s, d.key])}
                  className={on ? 'btn btn-primary' : 'btn btn-secondary'} style={{ fontSize: 12 }} title={(d.backing_tables || []).join(', ')}>
                  {on ? '✓ ' : ''}{d.name}{d.has_gated_hidden ? ' 🔒' : ''}
                </button>
              )
            })}
            {registry.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Loading datasets…</span>}
          </div>
          {regSource === 'code-default' && <div style={{ fontSize: 11, color: '#b45309', marginBottom: 8 }}>Registry: code defaults (run migration 211 to make datasets editable per tenant).</div>}

          {/* Per-dataset column pickers */}
          {selDatasets.map(k => {
            const d = regByKey[k]; if (!d) return null
            const chosen = selCols[k] || []
            return (
              <div key={k} style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>{d.name} — columns <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(none ticked = all)</span></div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  {d.columns.map(c => (
                    <label key={c.field} style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12 }}>
                      <input type="checkbox" checked={chosen.includes(c.field)} onChange={() => toggleCol(k, c.field)} />
                      {c.label}{c.money ? ' $' : ''}
                    </label>
                  ))}
                </div>
              </div>
            )
          })}

          {/* Saved definitions (RULE THREE: load a saved report; never free-typed recall) */}
          <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>Saved reports:</span>
            {defs.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>none yet</span>}
            {defs.map(df => (
              <span key={df.id} style={{ display: 'inline-flex', gap: 4, alignItems: 'center', background: 'var(--surface2)', borderRadius: 12, padding: '2px 4px 2px 10px', fontSize: 12 }}>
                <span style={{ cursor: 'pointer' }} onClick={() => applyDefinition(df)}>{df.name}</span>
                <span style={{ cursor: 'pointer', color: '#dc2626', fontWeight: 700, padding: '0 4px' }} onClick={() => deleteDefinition(df.id)}>✕</span>
              </span>
            ))}
            <div style={{ flex: 1 }} />
            <input style={{ ...sel, minWidth: 160 }} placeholder="Name this report…" value={defName} onChange={e => setDefName(e.target.value)} />
            <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={saveDefinition}>💾 Save</button>
          </div>
          {msg && <div style={{ fontSize: 12, color: msg.startsWith('❌') ? '#dc2626' : 'var(--text3)', marginTop: 6 }}>{msg}</div>}
        </div>
      )}

      {data?.error && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ Custom Report could not run.</b> {data.error}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !selDatasets.length ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>Pick one or more datasets above to build a report.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {sections.map(s => <SectionView key={s.key} section={s} period={period} />)}
        </div>
      )}

      {data?.registry_source === 'code-default' && !loading && (
        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10 }}>
          Sections shown side-by-side (no cross-dataset joins in v1). Each dataset filters on its own store/rep identifier.
        </div>
      )}
    </div>
  )
}

/** The cross-tab. Every number here is computed server-side from the raw rows — including the
 *  subtotals, which is the point: a percentage subtotal is the mean of the underlying values, never
 *  the mean of the cells above it. Rendering does no arithmetic of its own. */
function PivotView({ pivot }: { pivot: Pivot }) {
  if (!pivot.available) {
    return <div style={{ fontSize: 12.5, color: '#b45309', marginBottom: 10 }}>⚠️ Pivot: {pivot.reason}</div>
  }
  const rk = pivot.row_keys || [], ck = pivot.col_keys || []
  const money = pivot.measure_type === 'money'
  const pct = pivot.measure_type === 'pct'
  const fmt = (v: number | null | undefined) => {
    if (v === null || v === undefined) return <span style={{ color: 'var(--text3)' }}>·</span>
    const n = money ? '$' + v.toLocaleString('en-US', { maximumFractionDigits: 0 })
      : pct ? v.toLocaleString('en-US', { maximumFractionDigits: 1 }) + '%'
      : v.toLocaleString('en-US', { maximumFractionDigits: 2 })
    return n
  }
  const th: React.CSSProperties = { padding: '6px 10px', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--text3)', fontWeight: 700, textAlign: 'right', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '6px 10px', fontSize: 13, textAlign: 'right', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', borderTop: '1px solid var(--border)' }
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>
        <b>{pivot.measure_label}</b> by <b>{pivot.row_field}</b> × <b>{pivot.col_field}</b>
        <span style={{ color: 'var(--text3)' }}> · {pivot.agg === 'avg' ? 'averaged' : pivot.agg === 'count' ? 'counted' : 'summed'}</span>
      </div>
      {pivot.truncated_cols && (
        <div style={{ fontSize: 12, color: '#b45309', marginBottom: 6 }}>
          ⚠️ Showing the {ck.length} largest columns — {(pivot.dropped_cols || []).length} more are not
          displayed, and the totals below cover only what is shown. Narrow the filters or pick a
          coarser column field.
        </div>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', minWidth: '100%' }}>
          <thead><tr>
            <th style={{ ...th, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--surface)' }}>{pivot.row_field}</th>
            {ck.map(c => <th key={c} style={th}>{c}</th>)}
            <th style={{ ...th, borderLeft: '2px solid var(--border)' }}>Total</th>
          </tr></thead>
          <tbody>
            {rk.map(r => (
              <tr key={r}>
                <td style={{ ...td, textAlign: 'left', fontWeight: 600, position: 'sticky', left: 0, background: 'var(--surface)' }}>{r}</td>
                {ck.map(c => (
                  <td key={c} style={td} title={pivot.counts?.[r]?.[c] ? `${pivot.counts[r][c]} row(s)` : 'no rows'}>
                    {fmt(pivot.cells?.[r]?.[c])}
                  </td>
                ))}
                <td style={{ ...td, fontWeight: 700, borderLeft: '2px solid var(--border)' }}>{fmt(pivot.row_totals?.[r])}</td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr>
            <td style={{ ...td, textAlign: 'left', fontWeight: 700, borderTop: '2px solid var(--border)', position: 'sticky', left: 0, background: 'var(--surface)' }}>Total</td>
            {ck.map(c => <td key={c} style={{ ...td, fontWeight: 700, borderTop: '2px solid var(--border)' }}>{fmt(pivot.col_totals?.[c])}</td>)}
            <td style={{ ...td, fontWeight: 800, borderTop: '2px solid var(--border)', borderLeft: '2px solid var(--border)' }}>{fmt(pivot.grand_total)}</td>
          </tr></tfoot>
        </table>
      </div>
    </div>
  )
}

function SectionView({ section, period }: { section: Section; period: string }) {
  const cols: ExportColumn[] = useMemo(() => (section.columns || []).map(c => ({
    header: c.label, field: c.field, get: (r: any) => r[c.field],
    money: c.money, type: colType(c.type), align: (c.numeric ? 'right' : 'left') as 'left' | 'right',
  })), [section.columns])

  if (!section.available) {
    return (
      <div className="card" style={{ padding: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>{section.name}</div>
        <div style={{ fontSize: 13, color: '#b45309' }}>⚠️ {section.reason || 'dataset unavailable'}</div>
      </div>
    )
  }
  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{section.name}
          <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12, marginLeft: 8 }}>
            {section.row_count ?? section.rows.length} row{(section.row_count ?? section.rows.length) === 1 ? '' : 's'}
            {section.grouped_by ? ` · grouped by ${section.grouped_by}` : ''}
            {section.pivot?.available ? ' · pivoted' : ''}
          </span>
        </div>
        {(section.gated_columns_hidden || []).length > 0 && (
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>🔒 {section.gated_columns_hidden!.length} restricted column(s) hidden</span>
        )}
      </div>
      {/* The cross-tab sits ABOVE the detail table, and the table stays: the pivot is the summary and
          the rows underneath it are the evidence, so a number you don't believe is one scroll from
          the lines that produced it. */}
      {section.pivot && <PivotView pivot={section.pivot} />}
      {section.rows.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 30, color: 'var(--text3)', fontSize: 13 }}>No rows for this dataset with the current filters.</div>
      ) : (
        <ReportShell
          title={`${section.name} — ${period}`}
          filename={`custom-${section.key}-${period.replace(/\s+/g, '-')}`}
          columns={cols}
          rows={section.rows}
          totals
          stickyHeader
        />
      )}
    </div>
  )
}
