'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

// KPI definitions AT REP GRAIN. repKey = key inside rep_commissions.kpi_values. All values are
// whole-number percents (e.g. 70.6), compared directly to the target.
//
// SEVEN, not six (owner 2026-09-24: "what are the seven kpis, it shows only 6"). The commission tier
// is scored over the seven in calculator.py's KPI dict — the same seven the Settings → KPI tab edits
// and mig 002 stores as kpi_*_target — so a rep read "4/7" against six columns and could not see
// which one they missed. `boostapp` was the missing one.
//
// THE STORE-GRAIN COLUMN MAP USED TO BE HERE TOO, as a `storeKey` on each row, and it was the FOURTH
// copy of "which raw_dlar_store column carries which metric" (backend: dlar_sweep.STORE_FIELDS,
// kpi_failing.STORE_KPI_COLUMNS, two select literals in commcalc/router.py). It is deleted: the store
// view is built from `GET /dlar-store-catalog`, the one home (index §COMMCALC-58). That is why the 21
// columns the carrier portal was already sending could sit unread for weeks — a column could be
// declared, landed, and still invisible, because this array decided what a person could see.
// `backend/harness_dlar_metric_catalog.py` fails the build if `storeKey` comes back.
const KPIS = [
  { k: 'atu',        label: 'ATU %',         repKey: 'atu',        tcfg: 'kpi_atu_target',        def: 55 },
  { k: 'protect',    label: 'Protect %',     repKey: 'protect',    tcfg: 'kpi_protect_target',    def: 80 },
  { k: 'boostapp',   label: 'Carrier App %', repKey: 'boostapp',   tcfg: 'kpi_boostapp_target',   def: 65 },
  { k: 'byod',       label: 'BYOD %',        repKey: 'byod',       tcfg: 'kpi_byod_target',       def: 35 },
  { k: 'familyplan', label: 'Family Plan %', repKey: 'familyplan', tcfg: 'kpi_familyplan_target', def: 45 },
  { k: 'tmr3',       label: '3MR %',         repKey: 'tmr3',       tcfg: 'kpi_tmr3_target',       def: 70 },
  { k: 'aal',        label: 'AAL %',         repKey: 'aal',        tcfg: 'kpi_aal_target',        def: 5 },
]

// One entry of `GET /dlar-store-catalog`.metrics — the backend's own declaration, not a shape this
// page invents. `scored` is the KPI metric_key when the column carries a scored KPI (so the target
// and the pass/fail colouring apply), null otherwise. `available` is false when the column's
// migration has not been applied yet, and `reason` says so — a figure nobody has given us is
// reported as unavailable, never as 0, which would read as "the store sold none". The sentence rides
// the §19.36 key `reason` because it may name a setup internal: the API boundary gives a tenant the
// one neutral house sentence there and the platform super admin the detail, so this page renders
// whatever it is handed and never composes that wording itself.
type StoreMetric = {
  key: string; label: string; group: string; kind: 'count' | 'rate'
  scored: string | null; available: boolean; reason: string | null
}

// One row of `GET /dlar-store/{period}`: store identity plus whatever figures the catalog says this
// database carries. Flat by construction — a figure is a number the carrier published, a string the
// endpoint echoed, or null/absent meaning NOBODY PUBLISHED ONE. Never 0 for the last of those.
type StoreRow = Record<string, string | number | null | undefined>

// The groups a person picks between, labelled. Order and membership come from the catalog; only the
// words are here, and 'all' is this page's own option rather than one of the backend's groups.
const GROUP_LABELS: Record<string, string> = {
  all: 'All figures', scored: 'Scored KPIs', activations: 'Activations', trade_ins: 'Trade-ins',
  attach: 'Attach & quality', loyalty: 'Loyalty', target: 'Target & projection', trend: 'Trend',
}

const cellBase = { textAlign: 'right' as const, padding: '8px 10px', borderBottom: '1px solid var(--border)' }

export default function KPIPage() {
  const { period } = usePeriod()
  const [repData, setRepData] = useState<any[]>([])
  const [storeData, setStoreData] = useState<any[]>([])
  const [cfg, setCfg] = useState<any>({})
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<'rep' | 'store'>('rep')
  const [storeFilter, setStoreFilter] = useState('')
  const [repFilter, setRepFilter] = useState('')
  // STANDARD FILTERS (project report rule 2026-10-30) + the metric picker the owner asked for on
  // 2026-11-10 ("filtered by the kpi and out regular filters"). The date/look-back filter is the
  // period selector in the page chrome (`usePeriod`), shared by every report — not a second one here.
  const [marketFilter, setMarketFilter] = useState('')
  const [metricGroup, setMetricGroup] = useState('all')
  const [kpiFilter, setKpiFilter] = useState('')              // a scored metric_key, or ''
  const [kpiFilterMode, setKpiFilterMode] = useState<'missed' | 'met'>('missed')
  const [catalog, setCatalog] = useState<{ metrics: StoreMetric[]; groups: string[]; markets: string[]; has_rows: boolean; note: string | null }>(
    { metrics: [], groups: [], markets: [], has_rows: true, note: null })

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => []),
      api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({})),
      api(`/api/v1/commcalc/dlar-store/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => []),
      // WHICH FIGURES EXIST — the backend's own declaration (index §COMMCALC-58). Fetched rather
      // than hardcoded so a column the feed starts landing appears here without a frontend change.
      api(`/api/v1/commcalc/dlar-store-catalog?org_id=${ORG_ID}`).catch(() => null),
    ]).then(([comms, config, stores, cat]) => {
      setRepData(comms || [])
      setCfg(config || {})
      setStoreData(stores || [])
      if (cat) setCatalog(cat)
    }).catch(console.error).finally(() => setLoading(false))
  }, [period])

  const targets: Record<string, number> = Object.fromEntries(
    KPIS.map(d => [d.k, Number(cfg[d.tcfg]) || d.def])
  )

  // ── THE STORE VIEW'S COLUMNS, from the catalog ────────────────────────────────────────────────
  // A figure whose column the database has not been given yet is kept OUT of the grid and named
  // under it with its reason, rather than rendered as an empty column a person has to interpret.
  const storeMetrics = catalog.metrics.filter(m => m.available)
  const unavailable = catalog.metrics.filter(m => !m.available)
  const scoredMetrics = storeMetrics.filter(m => m.scored)
  const shownMetrics = metricGroup === 'all' ? storeMetrics
    : storeMetrics.filter(m => m.group === metricGroup)
  const groupOptions = ['all', ...catalog.groups.filter(g => storeMetrics.some(m => m.group === g))]

  function storeVal(s: StoreRow, key: string): number | undefined {
    const raw = s?.[key]
    const n = Number(raw)
    return raw == null || raw === '' || isNaN(n) ? undefined : n
  }

  // "KPIs met" at store grain counts over the SCORED figures that were actually measurable here —
  // never a fixed denominator, because three of the seven are not published at this grain.
  function storeMet(s: StoreRow) {
    return scoredMetrics.filter(m => {
      const v = storeVal(s, m.key)
      return v != null && v >= (targets[m.scored as string] ?? Infinity)
    }).length
  }

  // KPI cell — value is a whole-number percent (e.g. 70.6) or undefined.
  function KPICell({ val, target }: { val: number | undefined; target: number }) {
    if (val == null || isNaN(val)) {
      return <td style={{ ...cellBase, color: 'var(--text3)' }}>—</td>
    }
    const met = val >= target
    return (
      <td style={cellBase}>
        <span style={{ fontWeight: 600, fontSize: 13, color: met ? 'var(--green)' : val >= target * 0.8 ? 'var(--amber)' : 'var(--red)' }}>
          {val.toFixed(1)}%
        </span>
        <div style={{ fontSize: 10, color: 'var(--text3)' }}>target {target}%</div>
      </td>
    )
  }

  // Rep rows: only reps that have KPI data, then apply the store + rep filters.
  const repRows = repData
    .filter(r => r.kpi_values && Object.keys(r.kpi_values).length > 0)
    .filter(r => !storeFilter || r.store === storeFilter)
    .filter(r => !repFilter || (r.storeops_name || r.epay_salesperson) === repFilter)

  // Store rows: the standard filters, then the KPI filter. address is the unique store key
  // (location is a coarse dealer name shared across stores), so filter + label on address.
  //
  // THE KPI FILTER (owner 2026-11-10, "filtered by the kpi"). A store with NO value for the chosen
  // KPI is excluded from both directions rather than counted as missing it: nobody published a
  // figure for it, and calling that a miss would accuse a store of something nobody measured.
  const storeRows = storeData
    .filter(s => !storeFilter || s.address === storeFilter)
    .filter(s => !marketFilter || (s.market || '') === marketFilter)
    .filter(s => {
      if (!kpiFilter) return true
      const m = scoredMetrics.find(x => x.scored === kpiFilter)
      if (!m) return true
      const v = storeVal(s, m.key)
      if (v == null) return false
      const t = targets[kpiFilter] ?? 0
      return kpiFilterMode === 'met' ? v >= t : v < t
    })

  // Filter dropdown options (store list depends on the active view). Markets come from the
  // catalog — the canonical union list the backend resolver can actually bind, so a market option
  // here can never be one no store resolves to.
  const storeOptions = (view === 'rep'
    ? Array.from(new Set(repData.map(r => r.store).filter(Boolean)))
    : Array.from(new Set(storeData.map(s => s.address).filter(Boolean)))
  ).sort()
  const repOptions = Array.from(new Set(repData.map(r => r.storeops_name || r.epay_salesperson).filter(Boolean))).sort()
  const marketOptions = (catalog.markets.length
    ? catalog.markets
    : Array.from(new Set(storeData.map(s => s.market).filter(Boolean)))).slice().sort()

  function switchView(v: 'rep' | 'store') {
    setView(v); setStoreFilter(''); setRepFilter('')
    setMarketFilter(''); setKpiFilter(''); setMetricGroup('all')
  }

  const count = view === 'rep' ? repRows.length : storeRows.length

  // Export reflects the active view + filters (called at click time).
  function buildPayload(): ExportPayload {
    let cols: ExportColumn[]
    let rows: any[]
    let sheetName: string
    if (view === 'rep') {
      cols = [
        { header: 'Rep', get: r => r.storeops_name || r.epay_salesperson },
        { header: 'Store', get: r => r.store },
        { header: 'Tier %', align: 'right', get: r => Math.round((r.tier || 0) * 100) },
        ...KPIS.map(d => ({
          header: d.label, align: 'right' as const,
          get: (r: any) => { const v = (r.kpi_values || {})[d.repKey]; const n = Number(v); return v == null || isNaN(n) ? '' : Math.round(n * 10) / 10 },
        })),
        { header: 'KPIs Met', align: 'right', get: r => `${r.kpis_met}/${r.total_kpis}` },
      ]
      rows = repRows
      sheetName = 'KPI by Rep'
    } else {
      // The EXPORT IS THE VIEW (and so is what the Send bar sends): the same catalog-derived
      // columns, the same filters. A figure a person can see is a figure they can send.
      cols = [
        { header: 'Store', get: s => s.address || s.location },
        { header: 'Dealer', get: s => s.location },
        { header: 'Market', get: s => s.market || '' },
        ...shownMetrics.map(m => ({
          header: m.label, align: 'right' as const,
          get: (s: StoreRow) => { const v = storeVal(s, m.key); return v == null ? '' : Math.round(v * 100) / 100 },
        })),
        { header: 'KPIs Met', align: 'right', get: s => `${storeMet(s)}/${scoredMetrics.length}` },
      ]
      rows = storeRows
      sheetName = 'KPI by Store'
    }
    const filterParts = [
      storeFilter || null,
      view === 'rep' ? (repFilter || null) : (marketFilter || null),
      view === 'store' && kpiFilter
        ? `${scoredMetrics.find(m => m.scored === kpiFilter)?.label ?? kpiFilter} ${kpiFilterMode}`
        : null,
      view === 'store' && metricGroup !== 'all' ? GROUP_LABELS[metricGroup] ?? metricGroup : null,
    ].filter(Boolean)
    const filterLabel = filterParts.length ? filterParts.join(' · ') : (view === 'rep' ? 'All reps' : 'All stores')
    return {
      title: `KPI Metrics — ${view === 'rep' ? 'By Rep' : 'By Store'}`,
      subtitle: `${period} · ${filterLabel} · From DLAR Elevate Go`,
      filename: `kpi-metrics-${view}-${String(period).replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`,
      sheets: [{ name: sheetName, rows, columns: cols }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>KPI Metrics</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · From DLAR Elevate Go report · {count} {view === 'rep' ? 'reps' : 'stores'} with KPI data
          </p>
          <a href="/commcalc/productivity" style={{ fontSize: 12.5, color: 'var(--accent)', display: 'inline-block', marginTop: 4 }}>
            🏅 Productivity &amp; Reviews (per-employee output/hour · stack ranking · performance review) →
          </a>
        </div>
        {!loading && count > 0 && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
      </div>

      {/* Filter bar: view toggle + store filter + rep filter */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
          {(['rep', 'store'] as const).map(v => (
            <button key={v} onClick={() => switchView(v)} className="btn" style={{
              background: view === v ? 'white' : 'transparent',
              color: view === v ? 'var(--accent)' : 'var(--text2)',
              fontSize: 13, boxShadow: view === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            }}>
              {v === 'store' ? '🏪 By Store' : '👤 By Rep'}
            </button>
          ))}
        </div>

        <select className="select" value={storeFilter} onChange={e => setStoreFilter(e.target.value)}>
          <option value="">All stores</option>
          {storeOptions.map(s => <option key={s} value={s}>{String(s).substring(0, 45)}</option>)}
        </select>

        {view === 'rep' && (
          <select className="select" value={repFilter} onChange={e => setRepFilter(e.target.value)}>
            <option value="">All reps</option>
            {repOptions.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        )}

        {/* STANDARD FILTERS + THE KPI FILTER (owner 2026-11-10). The market list is the canonical
            union the backend resolver binds, so no option here can match nothing. */}
        {view === 'store' && marketOptions.length > 0 && (
          <select className="select" value={marketFilter} onChange={e => setMarketFilter(e.target.value)}>
            <option value="">All markets</option>
            {marketOptions.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        )}

        {view === 'store' && groupOptions.length > 1 && (
          <select className="select" value={metricGroup} onChange={e => setMetricGroup(e.target.value)}
            title="Which figures to show">
            {groupOptions.map(g => <option key={g} value={g}>{GROUP_LABELS[g] ?? g}</option>)}
          </select>
        )}

        {view === 'store' && scoredMetrics.length > 0 && (
          <>
            <select className="select" value={kpiFilter} onChange={e => setKpiFilter(e.target.value)}
              title="Show only stores by how they did on one KPI">
              <option value="">Any KPI</option>
              {scoredMetrics.map(m => <option key={m.key} value={m.scored as string}>{m.label}</option>)}
            </select>
            {kpiFilter && (
              <select className="select" value={kpiFilterMode}
                onChange={e => setKpiFilterMode(e.target.value as 'met' | 'missed')}>
                <option value="missed">below target</option>
                <option value="met">at or above target</option>
              </select>
            )}
          </>
        )}

        {(storeFilter || repFilter || marketFilter || kpiFilter || metricGroup !== 'all') && (
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
            onClick={() => { setStoreFilter(''); setRepFilter(''); setMarketFilter(''); setKpiFilter(''); setMetricGroup('all') }}>✕ Clear</button>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : count === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {/* NOTHING MEASURED MEANS NOTHING IDENTIFIED, AND THE SURFACE SAYS SO (§COMMCALC-49.10).
              When the catalog reports this organisation has no store rows at all, that sentence is
              the backend's — one tenant must never be told "0" because another tenant's carrier is
              the only one publishing. */}
          {view === 'store'
            ? (catalog.note
               || (storeData.length
                   ? 'No store matches these filters for this period.'
                   : 'No store KPI data — the store KPI feed is empty for this period.'))
            : 'No rep KPI data — import the DLAR report and run the calculation.'}
        </div>
      ) : view === 'rep' ? (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Rep</th><th>Store</th><th>Tier</th>
                {KPIS.map(d => <th key={d.k} style={{ textAlign: 'right' }}>{d.label}</th>)}
                <th style={{ textAlign: 'right' }}>KPIs Met</th>
              </tr>
            </thead>
            <tbody>
              {repRows.map((r, i) => {
                const kv = r.kpi_values || {}
                const tierPct = Math.round((r.tier || 0) * 100)
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>{r.storeops_name || r.epay_salesperson}</td>
                    <td style={{ color: 'var(--text3)', fontSize: 12 }}>{String(r.store || '').substring(0, 25)}</td>
                    <td>
                      <span className={`badge ${tierPct >= 100 ? 'badge-green' : tierPct >= 75 ? 'badge-amber' : 'badge-red'}`}>{tierPct}%</span>
                    </td>
                    {KPIS.map(d => <KPICell key={d.k} val={kv[d.repKey]} target={targets[d.k]} />)}
                    <td style={{ ...cellBase, fontWeight: 700 }}>
                      <span style={{ color: r.kpis_met >= 7 ? 'var(--green)' : r.kpis_met >= 5 ? 'var(--amber)' : 'var(--red)' }}>
                        {r.kpis_met}/{r.total_kpis}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <>
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Store</th>
                <th style={{ textAlign: 'left' }}>Market</th>
                {shownMetrics.map(m => (
                  <th key={m.key} style={{ textAlign: 'right' }}>{m.label}</th>
                ))}
                <th style={{ textAlign: 'right' }}>KPIs Met</th>
              </tr>
            </thead>
            <tbody>
              {storeRows.map((s, i) => {
                const met = storeMet(s)
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>
                      {s.address || s.location}
                      {s.location && <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{s.location}</div>}
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--text3)' }}>{s.market || '—'}</td>
                    {/* A SCORED figure gets its target and its colour; an unscored one is just the
                        number the carrier published, and `—` where it published none. A zero here
                        would say the store sold none, which is the defect this whole change came
                        from — so an absent figure stays absent. */}
                    {shownMetrics.map(m => {
                      const v = storeVal(s, m.key)
                      if (m.scored) {
                        return <KPICell key={m.key} val={v} target={targets[m.scored] ?? 0} />
                      }
                      return (
                        <td key={m.key} style={{ ...cellBase, fontSize: 13 }}>
                          {v == null ? <span style={{ color: 'var(--text3)' }}>—</span>
                            : m.kind === 'rate' ? v.toFixed(1) + '%'
                            : Number.isInteger(v) ? String(v) : v.toFixed(2)}
                        </td>
                      )
                    })}
                    <td style={{ ...cellBase, fontWeight: 700 }}>
                      <span style={{ color: met >= scoredMetrics.length ? 'var(--green)' : met >= scoredMetrics.length - 2 ? 'var(--amber)' : 'var(--red)' }}>{met}/{scoredMetrics.length}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {/* A FIGURE WHOSE COLUMN DOES NOT EXIST YET IS NAMED, WITH ITS REASON. Leaving it out would
            read as "the carrier does not publish this"; showing it empty would read as zero. */}
        {unavailable.length > 0 && (
          <p style={{ fontSize: 12, color: 'var(--text3)', margin: '8px 0 0' }}>
            {unavailable.length} further figure{unavailable.length === 1 ? '' : 's'} the carrier
            sends {unavailable.length === 1 ? 'is' : 'are'} not stored yet
            ({unavailable.map(m => m.label).join(', ')}){unavailable[0].reason ? ` — ${unavailable[0].reason}` : '.'}
          </p>
        )}
        </>
      )}

      <ParamountImport period={period} />
      <ManualKpiSection period={period} stores={storeData} />
    </div>
  )
}

function ParamountImport({ period }: { period: string }) {
  const [html, setHtml] = useState('')
  const [preview, setPreview] = useState<any[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  async function run(dry: boolean) {
    if (!html.trim()) { setMsg('Paste the report HTML first.'); return }
    setBusy(true); setMsg('')
    try {
      const r: any = await api(`/api/v1/commcalc/kpi-import/paramount?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ period, html, dry_run: dry }),
      })
      setPreview(r.preview || [])
      setMsg(dry ? `Parsed ${r.stores} store(s), ${r.values} value(s). Review, then Import.` : `✓ Imported ${r.saved} value(s) for ${r.stores} store(s).`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  return (
    <div className="card" style={{ marginTop: 24, padding: 16 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>Import Paramount MTD report (email body)</h2>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 10px' }}>
        Paste the report’s HTML (the email body). Pulls <b>Zulu</b>, <b>3MR</b>, and <b>TWP%</b> per door (Door TSP = store) into the KPI store as <b>email</b> values for <b>{period}</b>. Component counts stay on the rep-pay basis.
      </p>
      <textarea value={html} onChange={e => setHtml(e.target.value)} placeholder="<table>…Door TSP…</table>"
        style={{ width: '100%', minHeight: 90, fontFamily: 'monospace', fontSize: 12 }} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10 }}>
        <button className="btn" disabled={busy} onClick={() => run(true)}>{busy ? '…' : 'Preview'}</button>
        <button className="btn btn-primary" disabled={busy || !preview} onClick={() => run(false)}>Import</button>
        {msg && <span style={{ fontSize: 12 }}>{msg}</span>}
      </div>
      {preview && preview.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table style={{ fontSize: 12, borderCollapse: 'collapse' }}>
            <thead><tr>{['Door', 'Zulu%', '3MR%', 'TWP%'].map(h => <th key={h} style={{ textAlign: 'left', padding: '3px 12px 3px 0' }}>{h}</th>)}</tr></thead>
            <tbody>{preview.map((p: any) => (
              <tr key={p.store_code}>
                <td style={{ padding: '2px 12px 2px 0' }}>{p.store_code}</td>
                <td style={{ padding: '2px 12px 2px 0' }}>{p.zulu ?? '—'}</td>
                <td style={{ padding: '2px 12px 2px 0' }}>{p.tmr3 ?? '—'}</td>
                <td style={{ padding: '2px 12px 2px 0' }}>{p.twp ?? '—'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// WHICH metrics get a hand-entry box is the TENANT'S registry answer, not a list in this file.
//
// Owner (2026-09-25), looking at a Boost estate: "Manual KPI entry — Zulu · TWP · Address Checks …
// these dont belong on boost tenant, remove from cellfnz rus". The three were a literal array here,
// so EVERY tenant was asked to hand-enter another tenant's metrics — and the mode toggle POSTed a
// definition, so touching it would have CREATED a TWP row for an org that has no TWP.
//
// The registry already knew better. This grid now shows the metrics THIS org has defined that no
// feed fills (`auto_fed: false`, derived server-side from the feed maps' one home). Boost's seven
// are all auto-fed → the grid has nothing to offer and hides itself. LuxeLink's door-report metrics
// have no feed → they appear, for LuxeLink only. Nothing here names a metric, a carrier or a tenant.
type ManualMetric = { key: string; label: string }

function ManualKpiSection({ period, stores }: { period: string; stores: any[] }) {
  const [metrics, setMetrics] = useState<ManualMetric[]>([])
  const [modes, setModes] = useState<Record<string, string>>({})
  const [rows, setRows] = useState<any[]>([])
  const [edits, setEdits] = useState<Record<string, string>>({})   // `${entity}|${metric}` -> value
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api(`/api/v1/commcalc/carrier-kpi-metrics?org_id=${ORG_ID}`)
      .then((r: any) => setMetrics(
        (r?.metrics || [])
          .filter((m: any) => m && m.is_active !== false && m.auto_fed === false)
          .sort((a: any, b: any) => (a.sort ?? 0) - (b.sort ?? 0))
          .map((m: any) => ({ key: String(m.metric_key), label: String(m.label || m.metric_key) }))))
      .catch(() => setMetrics([]))
  }, [])

  function load() {
    api(`/api/v1/commcalc/kpi-actuals?period=${encodeURIComponent(period)}&scope=store&org_id=${ORG_ID}`)
      .then((act: any) => {
        setModes(act.source_modes || {})
        setRows(act.rows || [])
        const e: Record<string, string> = {}
        for (const r of (act.rows || [])) if (r.source === 'manual') e[`${r.entity}|${r.metric_key}`] = r.value ?? ''
        setEdits(e)
      }).catch(() => { setModes({}); setRows([]); setEdits({}) })
  }
  useEffect(load, [period])

  const storeList = stores
    .map(s => ({ code: String(s.store_code || s.address || ''), label: String(s.address || s.location || s.store_code || '') }))
    .filter(s => s.code)

  async function setMode(metric: string, mode: string) {
    setModes(m => ({ ...m, [metric]: mode }))
    try {
      await api(`/api/v1/commcalc/carrier-kpi-metrics?org_id=${ORG_ID}`, {
        method: 'POST',
        // label comes from the registry row we are ALREADY showing — this edits a definition the
        // org has, and can never bring a new metric into existence for a tenant that lacks it.
        body: JSON.stringify({ metric_key: metric, label: metrics.find(x => x.key === metric)?.label || metric, source_mode: mode }),
      })
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  async function save() {
    setBusy(true); setMsg('')
    const entries: any[] = []
    for (const [k, v] of Object.entries(edits)) {
      const [entity, metric_key] = k.split('|')
      if ((modes[metric_key] || 'manual') !== 'manual') continue   // only persist manual-mode metrics
      entries.push({ entity, metric_key, value: v })
    }
    try {
      const r: any = await api(`/api/v1/commcalc/kpi-actuals?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ period, scope: 'store', entries }),
      })
      setMsg(`✓ Saved ${r.saved} value(s).`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  // Every KPI this org has defined arrives on its own → there is nothing to hand-enter, so the whole
  // section stays out of the page rather than rendering an empty table asking for nothing.
  if (metrics.length === 0) return null

  return (
    <div className="card" style={{ marginTop: 24, padding: 16 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>
        Manual KPI entry — {metrics.map(m => m.label).join(' · ')}
      </h2>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 12px' }}>
        These KPIs are defined for this tenant but no feed fills them yet. Enter values per store in <b>Manual</b> mode; flip a
        metric to <b>Email</b> once its import is wired — both sources are kept, so switching never loses data. The
        Management-Incentive Pull reads these for the qualification gate. Add or remove metrics on <b>KPI Definitions</b>.
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '6px 8px' }}>Store</th>
              {metrics.map(m => (
                <th key={m.key} style={{ textAlign: 'right', padding: '6px 8px' }}>
                  <div>{m.label}</div>
                  <div style={{ display: 'inline-flex', gap: 2, marginTop: 2 }}>
                    {['manual', 'email'].map(md => (
                      <button key={md} className="btn" onClick={() => setMode(m.key, md)} style={{
                        fontSize: 10, padding: '1px 6px',
                        background: (modes[m.key] || 'manual') === md ? 'var(--accent)' : 'var(--surface2)',
                        color: (modes[m.key] || 'manual') === md ? 'white' : 'var(--text2)',
                      }}>{md}</button>
                    ))}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {storeList.length === 0 && <tr><td colSpan={1 + metrics.length} style={{ padding: 12, color: 'var(--text3)' }}>No stores for this period yet.</td></tr>}
            {storeList.map(s => (
              <tr key={s.code} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 8px' }}>{s.label.substring(0, 40)}</td>
                {metrics.map(m => {
                  const manual = (modes[m.key] || 'manual') === 'manual'
                  const key = `${s.code}|${m.key}`
                  if (manual) return (
                    <td key={m.key} style={{ padding: '4px 8px', textAlign: 'right' }}>
                      <input type="number" style={{ width: 70, textAlign: 'right' }} value={edits[key] ?? ''} onChange={e => setEdits(ed => ({ ...ed, [key]: e.target.value }))} />
                    </td>
                  )
                  const emailRow = rows.find(r => r.entity === s.code && r.metric_key === m.key && r.source === 'email')
                  return <td key={m.key} style={{ padding: '4px 8px', textAlign: 'right', color: 'var(--text3)' }}>{emailRow?.value ?? '—'} <span style={{ fontSize: 9 }} title="from email import">✉</span></td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12 }}>
        <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? '…' : 'Save manual values'}</button>
        {msg && <span style={{ fontSize: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}
