'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

// KPI definitions. repKey = key inside rep_commissions.kpi_values; storeKey = raw_dlar_store column.
// All values are whole-number percents (e.g. 70.6), compared directly to the target.
const KPIS = [
  { k: 'atu',        label: 'ATU %',         repKey: 'atu',        storeKey: 'atu',             tcfg: 'kpi_atu_target',        def: 55 },
  { k: 'protect',    label: 'Protect %',     repKey: 'protect',    storeKey: 'protect_pct',     tcfg: 'kpi_protect_target',    def: 80 },
  { k: 'byod',       label: 'BYOD %',        repKey: 'byod',       storeKey: 'byod_pct',        tcfg: 'kpi_byod_target',       def: 35 },
  { k: 'familyplan', label: 'Family Plan %', repKey: 'familyplan', storeKey: 'family_plan_pct', tcfg: 'kpi_familyplan_target', def: 45 },
  { k: 'tmr3',       label: '3MR %',         repKey: 'tmr3',       storeKey: 'tmr3',            tcfg: 'kpi_tmr3_target',       def: 70 },
  { k: 'aal',        label: 'AAL %',         repKey: 'aal',        storeKey: 'aal_conversion',  tcfg: 'kpi_aal_target',        def: 5 },
]

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

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => []),
      api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({})),
      api(`/api/v1/commcalc/dlar-store/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => []),
    ]).then(([comms, config, stores]) => {
      setRepData(comms || [])
      setCfg(config || {})
      setStoreData(stores || [])
    }).catch(console.error).finally(() => setLoading(false))
  }, [period])

  const targets: Record<string, number> = Object.fromEntries(
    KPIS.map(d => [d.k, Number(cfg[d.tcfg]) || d.def])
  )

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

  function metCount(vals: (number | undefined)[]) {
    return vals.filter((v, i) => v != null && !isNaN(v as number) && (v as number) >= targets[KPIS[i].k]).length
  }

  // Rep rows: only reps that have KPI data, then apply the store + rep filters.
  const repRows = repData
    .filter(r => r.kpi_values && Object.keys(r.kpi_values).length > 0)
    .filter(r => !storeFilter || r.store === storeFilter)
    .filter(r => !repFilter || (r.storeops_name || r.epay_salesperson) === repFilter)

  // Store rows: apply the store filter. address is the unique store key (location is a
  // coarse dealer name shared across stores), so filter + label on address.
  const storeRows = storeData.filter(s => !storeFilter || s.address === storeFilter)

  // Filter dropdown options (store list depends on the active view).
  const storeOptions = (view === 'rep'
    ? Array.from(new Set(repData.map(r => r.store).filter(Boolean)))
    : Array.from(new Set(storeData.map(s => s.address).filter(Boolean)))
  ).sort()
  const repOptions = Array.from(new Set(repData.map(r => r.storeops_name || r.epay_salesperson).filter(Boolean))).sort()

  function switchView(v: 'rep' | 'store') { setView(v); setStoreFilter(''); setRepFilter('') }

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
      cols = [
        { header: 'Store', get: s => s.address || s.location },
        { header: 'Dealer', get: s => s.location },
        ...KPIS.map(d => ({
          header: d.label, align: 'right' as const,
          get: (s: any) => { const n = Number(s[d.storeKey]); return isNaN(n) ? '' : Math.round(n * 10) / 10 },
        })),
        { header: 'Conv %', align: 'right', get: s => { const n = Number(s.conversion_rate); return isNaN(n) ? '' : Math.round(n * 10) / 10 } },
        { header: 'Total Acts', align: 'right', get: s => s.total_acts ?? '' },
        { header: 'KPIs Met', align: 'right', get: s => `${metCount(KPIS.map(d => { const n = Number(s[d.storeKey]); return isNaN(n) ? undefined : n }))}/6` },
      ]
      rows = storeRows
      sheetName = 'KPI by Store'
    }
    const filterParts = [storeFilter || null, view === 'rep' ? (repFilter || null) : null].filter(Boolean)
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
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
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

        {(storeFilter || repFilter) && (
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
            onClick={() => { setStoreFilter(''); setRepFilter('') }}>✕ Clear</button>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : count === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {view === 'store'
            ? 'No store KPI data — the Store DLAR (raw_dlar_store) is empty for this period.'
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
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Store</th>
                {KPIS.map(d => <th key={d.k} style={{ textAlign: 'right' }}>{d.label}</th>)}
                <th style={{ textAlign: 'right' }}>Conv %</th>
                <th style={{ textAlign: 'right' }}>Total Acts</th>
                <th style={{ textAlign: 'right' }}>KPIs Met</th>
              </tr>
            </thead>
            <tbody>
              {storeRows.map((s, i) => {
                const vals = KPIS.map(d => { const n = Number(s[d.storeKey]); return isNaN(n) ? undefined : n })
                const met = metCount(vals)
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>
                      {s.address || s.location}
                      {s.location && <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{s.location}</div>}
                    </td>
                    {KPIS.map((d, j) => <KPICell key={d.k} val={vals[j]} target={targets[d.k]} />)}
                    <td style={{ ...cellBase, fontSize: 13 }}>{s.conversion_rate != null ? Number(s.conversion_rate).toFixed(1) + '%' : '—'}</td>
                    <td style={{ ...cellBase, fontSize: 13 }}>{s.total_acts ?? '—'}</td>
                    <td style={{ ...cellBase, fontWeight: 700 }}>
                      <span style={{ color: met >= 6 ? 'var(--green)' : met >= 4 ? 'var(--amber)' : 'var(--red)' }}>{met}/6</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <ManualKpiSection period={period} stores={storeData} />
    </div>
  )
}

// Metrics with no automated feed yet — captured by hand until their email import is wired.
const EXTRA_METRICS = [
  { key: 'zulu', label: 'Zulu' },
  { key: 'twp', label: 'TWP' },
  { key: 'address_checks', label: 'Address Checks' },
]

function ManualKpiSection({ period, stores }: { period: string; stores: any[] }) {
  const [modes, setModes] = useState<Record<string, string>>({})
  const [rows, setRows] = useState<any[]>([])
  const [edits, setEdits] = useState<Record<string, string>>({})   // `${entity}|${metric}` -> value
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

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
        body: JSON.stringify({ metric_key: metric, label: EXTRA_METRICS.find(x => x.key === metric)?.label || metric, source_mode: mode }),
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

  return (
    <div className="card" style={{ marginTop: 24, padding: 16 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>Manual KPI entry — Zulu · TWP · Address Checks</h2>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 12px' }}>
        These have no automated feed yet. Enter values per store in <b>Manual</b> mode; flip a metric to <b>Email</b> once its
        import is wired — both sources are kept, so switching never loses data. The Management-Incentive Pull reads these for the qualification gate.
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '6px 8px' }}>Store</th>
              {EXTRA_METRICS.map(m => (
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
            {storeList.length === 0 && <tr><td colSpan={1 + EXTRA_METRICS.length} style={{ padding: 12, color: 'var(--text3)' }}>No stores for this period yet.</td></tr>}
            {storeList.map(s => (
              <tr key={s.code} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 8px' }}>{s.label.substring(0, 40)}</td>
                {EXTRA_METRICS.map(m => {
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
