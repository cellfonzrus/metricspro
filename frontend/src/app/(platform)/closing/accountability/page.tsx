'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { api, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// THE FIVE-STAGE ACCOUNTABILITY CHAIN (owner 2026-10-02, index §48), verbatim: "we need to see in a
// daily report or date range report for the following / Daily Closing done or not with dates and by
// who / DM verified or not with dates and by who / CAsh pick with dates and by who / Cash Handover
// with dates and by who / managment review with dates and by who".
//
// One row per (store, day). The STAGES — their keys, their order and their wording — come from the
// SERVER (data.stages <- deposit_accountability.stage_catalog), so this screen spells no stage name
// of its own and a stage can be renamed or re-ordered in the pure module without touching the UI
// (the §47 basis-selector posture). The spine is every store expected to file × every day in the
// range, which is why a store that filed NOTHING still appears instead of silently vanishing.
const NO_MARKET = '(no market)'
const csv = (a: string[]) => (a.length ? a.join(',') : undefined)
const ymd = (s: string) => (s || '').slice(0, 10)
const stamp = (s: string) => (s || '').slice(0, 16).replace('T', ' ')

export default function AccountabilityChainPage() {
  const today = localToday()
  const [filt, setFilt] = useState<StandardFilterValue>({ period: today.slice(0, 8) + '01', periodTo: today, stores: [], markets: [], reps: [] })
  const [stuck, setStuck] = useState('')
  const [onlyGaps, setOnlyGaps] = useState(false)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const reqRef = useRef(0)

  const [pStores, setPStores] = useState<any[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])
  const marketOptions: EntityOption[] = useMemo(() => {
    const real = Array.from(new Set(pStores.map((s: any) => s.market).filter(Boolean))).sort()
    return [...real.map((m: string) => ({ id: m, label: m })), { id: NO_MARKET, label: NO_MARKET }]
  }, [pStores])

  const load = useCallback(() => {
    const myReq = ++reqRef.current
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    const from = filt.period || localToday()
    qs.set('start', from)
    qs.set('end', filt.periodTo || from)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('market', m)
    if (stuck) qs.set('stuck', stuck)
    api(`/api/v1/closing/accountability-chain?${qs.toString()}`)
      .then(d => { if (reqRef.current === myReq) setData(d) })
      .catch(e => { if (reqRef.current === myReq) { setErr(e?.message || String(e)); setData(null) } })
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt, stuck])
  useEffect(() => { load() }, [load])

  const stages: any[] = data?.stages || []
  const allRows: any[] = data?.rows || []
  const rows = useMemo(() => (onlyGaps ? allRows.filter(r => !r.complete) : allRows), [allRows, onlyGaps])
  const summary = data?.summary || {}
  const byStage: any[] = summary.by_stage || []

  // Sort: the fixed leading columns, plus one sortable key per stage (its timestamp), plus the
  // stuck pointer. Derived from the SERVER's stage list so a new stage sorts without a UI change.
  const cols = useMemo<[string, string][]>(() => ([
    ['Date', 'day'], ['Store', 'store_name'], ['Market', 'market'],
    ...stages.map((s: any, i: number) => [s.label, `st${i}`] as [string, string]),
    ['Stuck at', 'stuck_label'], ['Done', 'stages_done'],
  ]), [stages])
  const cell = useCallback((r: any, f: string) => {
    if (f.startsWith('st')) {
      const i = Number(f.slice(2))
      const st = (r.stages || [])[i]
      // sort by when it happened; a stage not done sorts before every done one
      return st?.done ? (st.at || '1') : ''
    }
    return r?.[f]
  }, [])
  // Seeded newest-day-first: a 30-day board is read from the most recent day backwards.
  const { sorted, sort, toggle } = useTableSort(rows, cell, { field: 'day', dir: 'desc' })

  const columns: ExportColumn[] = useMemo(() => {
    const base: ExportColumn[] = [
      { header: 'Date', field: 'day', type: 'date', role: 'date', get: (r: any) => r.day },
      { header: 'Store', field: 'store_name', role: 'store', get: (r: any) => r.store_name },
      { header: 'Market', field: 'market', get: (r: any) => r.market || '' },
    ]
    // THREE COLUMNS PER STAGE — done / when / who — because "with dates and by who" is the ask, and
    // a spreadsheet that collapses them into one cell cannot be filtered or pivoted on.
    stages.forEach((s: any, i: number) => {
      base.push({ header: `${s.label}`, field: `s${i}_done`, get: (r: any) => ((r.stages || [])[i]?.done ? 'Yes' : 'No') })
      base.push({ header: `${s.label} — when`, field: `s${i}_at`, get: (r: any) => stamp((r.stages || [])[i]?.at || '') })
      base.push({ header: `${s.label} — by`, field: `s${i}_by`, get: (r: any) => ((r.stages || [])[i]?.by || []).join(', ') })
    })
    base.push({ header: 'Stuck at', field: 'stuck_label', get: (r: any) => r.stuck_label || '' })
    base.push({ header: 'Stages done', field: 'stages_done', get: (r: any) => r.stages_done })
    return base
  }, [stages])

  const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const tile: React.CSSProperties = { padding: '10px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', minWidth: 132 }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔗 Cash Accountability Chain</h1>
          {/* Always visible, not a `.pg-note`: the help-text gate hides those from everyone but a
              Master admin who opted in, and the denominator is the one thing a reader must know to
              trust a "not done" cell. */}
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
            One line per <b>store and day</b>, showing each step of the cash chain — <b>done or not, when, and by
            whom</b>. A row appears for <b>every active store on every day in the range</b>, so a store that filed
            nothing is listed as missing rather than left out. <b>Stuck at</b> names the first step not yet done.
          </p>
        </div>
        {!loading && rows.length > 0 && (
          <ReportExportBar
            title="Cash Accountability Chain"
            subtitle={`${filt.period} → ${filt.periodTo || filt.period}`}
            filename={`accountability-chain_${filt.period}_${filt.periodTo || filt.period}`}
            sheets={[{ name: 'Chain', columns, rows: sorted }]}
          />
        )}
      </div>

      {/* The rep picker is hidden deliberately: a row here is a STORE-DAY, not a person's day, and
          several people appear in one row (two reps filing, a DM collecting, a manager receiving), so
          filtering the chain by one of them would answer a question this grain cannot answer. The
          standard-filter doctrine allows omitting a core control where it has no meaning, documented. */}
      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        show={{ reps: false }}
        storeOptions={storeOptions} marketOptions={marketOptions}
        storeLabel="Stores…" marketLabel="Markets…"
        right={(
          <>
            <select style={sel} value={stuck} onChange={e => setStuck(e.target.value)}
              title="Show only the store-days held up at one particular step.">
              <option value="">Stuck at: any step</option>
              {stages.map((s: any) => (<option key={s.key} value={s.key}>{s.label}</option>))}
            </select>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <input type="checkbox" checked={onlyGaps} onChange={e => setOnlyGaps(e.target.checked)} />
              Incomplete only
            </label>
          </>
        )}
      />

      {err && <div style={{ color: 'var(--danger)', margin: '10px 0' }}>{err}</div>}
      {loading && <div style={{ margin: '12px 0', color: 'var(--text2)' }}>Loading…</div>}

      {!loading && data && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '14px 0' }}>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Store-days</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{summary.store_days ?? 0}</div>
            </div>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Fully accounted</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{summary.complete_days ?? 0}</div>
            </div>
            {/* One tile per stage, labelled BY THE SERVER — done over total, so the weakest link is
                obvious at a glance without reading the grid. */}
            {byStage.map((s: any) => (
              <div key={s.key} style={tile}>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{s.label}</div>
                <div style={{ fontSize: 19, fontWeight: 700 }}>
                  {s.done}
                  <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text3)' }}> / {(s.done + s.missing)}</span>
                </div>
                {s.missing > 0 && <div style={{ fontSize: 11.5, color: 'var(--warn)' }}>{s.missing} missing</div>}
              </div>
            ))}
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table className="pg-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr>
                  {cols.map(([label, field]) => (
                    <SortableTh key={label + field} field={field} sort={sort} onSort={toggle}
                      disabled={!field}
                      style={{ padding: '8px 10px', whiteSpace: 'nowrap', fontWeight: 600, color: 'var(--text2)' }}>
                      {label}
                    </SortableTh>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r: any) => (
                  <tr key={`${r.store_code}|${r.day}`}>
                    <td>{ymd(r.day)}</td>
                    <td>{r.store_name || r.store_code}</td>
                    <td style={{ color: 'var(--text3)' }}>{r.market || ''}</td>
                    {(r.stages || []).map((st: any) => (
                      <td key={st.key} style={{ whiteSpace: 'nowrap' }}>
                        <div title={st.detail || ''}>
                          {st.done ? '✅' : '—'}
                          {st.at && <span style={{ color: 'var(--text3)', marginLeft: 5 }}>{stamp(st.at)}</span>}
                        </div>
                        {/* WHO. Several people can share one step (two reps filing one day, a DM who
                            collected and a manager who received), so every name is shown rather than
                            the first one. */}
                        {(st.by || []).length > 0 && (
                          <div style={{ fontSize: 11.5, color: 'var(--text2)' }}>{(st.by || []).join(', ')}</div>
                        )}
                        {(st.confirmed_by || []).length > 0 && (
                          <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                            received: {(st.confirmed_by || []).join(', ')}{st.confirmed_at ? ` · ${stamp(st.confirmed_at)}` : ''}
                          </div>
                        )}
                        {!st.done && st.detail && (
                          <div style={{ fontSize: 11, color: 'var(--text3)' }}>{st.detail}</div>
                        )}
                      </td>
                    ))}
                    <td style={{ whiteSpace: 'nowrap', color: r.complete ? 'var(--text3)' : 'var(--warn)' }}>
                      {r.stuck_label || '—'}
                    </td>
                    <td>{r.stages_done} / {stages.length}</td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr><td colSpan={cols.length} style={{ color: 'var(--text3)', padding: '14px 8px' }}>
                    Nothing in this range for the chosen filters.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
            {data.days} day(s) × {data.stores_expected} store(s) expected to file ·
            showing {rows.length} of {allRows.length} store-day row(s)
          </div>
        </>
      )}
    </div>
  )
}
