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
type Dataset = { key: string; name: string; columns: DsCol[]; group_dims: string[]; backing_tables: string[]; has_gated_hidden: boolean }
type SecCol = { field: string; label: string; type: string; numeric: boolean; money: boolean; agg: string }
type Section = {
  key: string; name: string; available: boolean; reason?: string; grouped_by?: string | null
  columns: SecCol[]; rows: any[]; totals: Record<string, number>; row_count?: number
  gated_columns_hidden?: string[]
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
    })
    api(`/api/v1/commcalc/custom-report?${qs.toString()}${orgParam()}`)
      .then((r: any) => { setData(r); if (r.filter_options) setOptions(r.filter_options) })
      .catch((e: any) => setData({ sections: [], error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [selDatasets, period, selStores, selMarkets, selReps, groupBy, selCols])
  useEffect(() => { run() }, [run])

  const regByKey = useMemo(() => Object.fromEntries(registry.map(d => [d.key, d])), [registry])

  // Group-by options — the union of the selected datasets' group_dims (universal dims first, RULE FIVE).
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
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
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
          </span>
        </div>
        {(section.gated_columns_hidden || []).length > 0 && (
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>🔒 {section.gated_columns_hidden!.length} restricted column(s) hidden</span>
        )}
      </div>
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
